"""Execute the KR-10 portfolio holdout confirmation — Multi-Asset Task F.

The registered hypothesis is that the missing piece was never the signal
but the portfolio: Moskowitz-Ooi-Pedersen's own 0.8–1.2 Sharpe is a
58-instrument figure, per-instrument is 0.2–0.4, and this project applied
it to one asset twice and got INCONCLUSIVE both times while passing PSR,
Sharpe-vs-floor and profit factor. So this runs the **same strategy
module, unchanged**, once per constituent, and aggregates.

Three properties that are the reason this is a separate runner rather
than a flag on `run_preregistered_holdout`:

**One access for the whole portfolio.** `research.holdout.
load_holdout_portfolio` is called exactly once and writes exactly one
`holdout_access` record naming every symbol. Calling the single-symbol
loader per member would consume the claim on the first and refuse the
second.

**Metrics are computed on the aggregated curve**, never averaged across
members. A mean of twelve Sharpes is not the portfolio's Sharpe, and the
whole hypothesis is about what aggregation does to volatility and
drawdown — averaging the inputs would assume away the thing being
measured.

**The universe must be complete or nothing is scored.** A member with no
data is a smaller portfolio than the one registered, and the registered
detection floor, trade-count floor and diversification prediction all
describe the twelve-member version.

`daily_tsmom_ensemble.py` is imported and used unmodified. Its
zero-fitted-parameter property is what the Paper Trading Policy Exception
rests on; the portfolio layer sits above it and never inside it.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from backtest.engine import run_backtest
from backtest.kline import Kline
from metrics.metrics import (
    Metrics,
    _max_drawdown,
    _profit_factor,
    _sharpe_ratio,
    compute_metrics,
    compute_return_moments,
)
from research import experiment_log
from research.eligibility import psr_from_equity_curve
from research.holdout import load_holdout_portfolio
from research.preregistration import Preregistration, load_preregistration
from data.store import connect
from research.strategies.daily_tsmom_ensemble import DailyTsmomEnsembleStrategy

LOGGER = logging.getLogger(__name__)

DEFAULT_STARTING_EQUITY = Decimal("10000")
DEFAULT_DB_PATH = "data/var/klines.sqlite3"


class PortfolioRunError(RuntimeError):
    """The run cannot proceed or its result cannot be trusted."""


@dataclass(frozen=True)
class MemberResult:
    symbol: str
    name: str
    metrics: Metrics


@dataclass(frozen=True)
class PortfolioResult:
    members: list[MemberResult]
    equity_curve: list[Decimal]
    total_trades: int
    sharpe_ratio: float | None
    max_drawdown: Decimal
    profit_factor: float | None
    psr: float | None
    num_returns: int
    gates: dict[str, bool] = field(default_factory=dict)
    verdict: str = "UNEVALUATED"


def load_universe(path: str | Path) -> tuple[list[dict], dict]:
    """`(members, config)` — equity members only.

    Index members are declared in the universe file and are **not** run
    here: an index has no `KRX:` spot series to signal on the way an
    equity does, and inventing one would be a different strategy. The
    count is reported so the gap between the declared twelve and the
    scored ten is visible rather than silent.
    """
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(config["equity_members"]), config


def preflight_bar_counts(
    symbols: list[str], interval: str, start_ms: int, end_ms: int, db_path: str | Path
) -> dict[str, int]:
    """Row counts per symbol, **before** the holdout claim is consumed.

    Counting rows is not reading prices: this issues a `COUNT(*)` and no
    price leaves the database, the same category as
    `run_preregistered_holdout`'s `verify_known_gaps`, which likewise runs
    before any load.

    It exists because the first attempt at this run consumed the
    single-access claim and *then* discovered an off-by-one in the
    registration's range bound. The claim is one-shot; spending it on a
    mechanical mismatch that a `COUNT(*)` could have caught is exactly the
    kind of avoidable loss the claim is meant to protect against.
    """
    conn = connect(db_path)
    try:
        return {
            symbol: conn.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol = ? AND interval = ? "
                "AND open_time_ms >= ? AND open_time_ms < ?",
                (symbol, interval, start_ms, end_ms),
            ).fetchone()[0]
            for symbol in symbols
        }
    finally:
        conn.close()


def run_member(
    klines: list[Kline],
    *,
    symbol: str,
    lookbacks: list[int],
    fee_bps: Decimal,
    slippage_bps: Decimal,
    member_equity: Decimal,
    bars_per_day: int,
) -> Metrics:
    """One constituent, through the unmodified strategy."""
    strategy = DailyTsmomEnsembleStrategy(
        symbol=symbol,
        lookbacks=lookbacks,
        reference_equity=member_equity,
        bars_per_day=bars_per_day,
    )
    result = run_backtest(
        klines, strategy, fee_bps, slippage_bps, starting_equity=member_equity
    )
    return compute_metrics(
        klines,
        result.filled_intents,
        result.fills,
        member_equity,
        bars_per_day=bars_per_day,
    )


MIN_RETURNS_FOR_PSR = 30
"""Below this, PSR is not evidence.

30 is the same "usable power" threshold CLAUDE.md's trade-count floor
rests on -- not folklore about the CLT, but the point below which nothing
short of a near-sweep is detectable. It also keeps a degenerate series out
of `psr_from_equity_curve`, whose raw-kurtosis validation legitimately
rejects the 0.9999999999999998 a two-return series produces. Returning
`None` follows `Metrics`' own convention: "no evidence" is a different
claim from bad evidence, and neither is an exception.
"""


def _psr_or_none(curve: list[Decimal], bars_per_day: int) -> float | None:
    moments = compute_return_moments(curve)
    if moments.num_returns < MIN_RETURNS_FOR_PSR:
        return None
    return psr_from_equity_curve(curve, bars_per_day=bars_per_day).psr


def aggregate(members: list[MemberResult], *, bars_per_day: int) -> PortfolioResult:
    """Equal-weight aggregation, computed on the summed curve.

    Every member's curve must have identical length: they were loaded over
    one window from series whose bar counts were verified equal, and a
    mismatch would silently misalign dates against each other. Refused
    rather than truncated.
    """
    lengths = {len(m.metrics.equity_curve) for m in members}
    if len(lengths) != 1:
        raise PortfolioRunError(
            f"member equity curves differ in length ({sorted(lengths)}) -- dates would "
            "misalign, and a portfolio built from misaligned series is not the "
            "portfolio that was registered"
        )

    n = lengths.pop()
    curve = [
        sum((m.metrics.equity_curve[i] for m in members), Decimal("0")) for i in range(n)
    ]
    pooled = [t for m in members for t in m.metrics.closed_trades]

    moments = compute_return_moments(curve)
    sharpe = _sharpe_ratio(curve, bars_per_day)
    return PortfolioResult(
        members=members,
        equity_curve=curve,
        total_trades=sum(m.metrics.num_trades for m in members),
        sharpe_ratio=sharpe,
        max_drawdown=_max_drawdown(curve),
        profit_factor=_profit_factor(pooled),
        # psr_from_equity_curve, NOT evaluate_psr. evaluate_psr takes a
        # PER-OBSERVATION Sharpe and `sharpe` above is annualized; feeding
        # one to the other is the exact trap eligibility.py's own module
        # docstring names, and it is not a subtle error -- an annualized
        # 0.3263 read as per-observation over 1,881 points saturates PSR
        # at 1.0. A first version of this runner did precisely that.
        # This helper resamples to daily, measures real moments, and
        # derives the Sharpe, the moments and T from one series so they
        # cannot drift apart.
        psr=_psr_or_none(curve, bars_per_day),
        num_returns=moments.num_returns,
    )


def evaluate(result: PortfolioResult, prereg: Preregistration) -> PortfolioResult:
    """Apply the registered criteria, all five, to the portfolio."""
    c = prereg.primary_criterion
    floor = float(prereg.config["declared_detection_floor_sharpe"])
    gates = {
        "psr": result.psr is not None and result.psr >= float(c["threshold"]),
        "max_drawdown": float(result.max_drawdown) <= float(c["max_drawdown_ceiling"]),
        "min_total_trades": result.total_trades >= int(c["min_total_trades"]),
        "profit_factor": result.profit_factor is not None
        and result.profit_factor >= float(c["profit_factor_floor"]),
        "sharpe_above_detection_floor": result.sharpe_ratio is not None
        and result.sharpe_ratio > floor,
    }
    if result.psr is None or result.psr <= 0:
        verdict = "FAIL"
    elif all(gates.values()):
        verdict = "PASS"
    else:
        verdict = "INCONCLUSIVE"
    return PortfolioResult(
        members=result.members,
        equity_curve=result.equity_curve,
        total_trades=result.total_trades,
        sharpe_ratio=result.sharpe_ratio,
        max_drawdown=result.max_drawdown,
        profit_factor=result.profit_factor,
        psr=result.psr,
        num_returns=result.num_returns,
        gates=gates,
        verdict=verdict,
    )


def run(
    prereg: Preregistration,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
    starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
    force_reclaim_reason: str | None = None,
    universe_path: str | Path | None = None,
) -> PortfolioResult:
    if not prereg.is_holdout_confirmation:
        raise PortfolioRunError(
            f"{prereg.preregistration_id!r} declares data.split="
            f"{prereg.data['split']!r}; this runner drives holdout registrations only"
        )
    members_cfg, universe = load_universe(
        universe_path or prereg.data["universe_config_path"]
    )
    symbols = [m["storage_symbol"] for m in members_cfg]
    LOGGER.info(
        "universe %s: %d equity members scored, %d index members declared and not scored",
        universe["universe_id"],
        len(symbols),
        len(universe.get("index_members", [])),
    )

    start_ms, end_ms = int(prereg.data["start_ms"]), int(prereg.data["end_ms"])
    expected = int(prereg.data["expected_bars"])

    # Before the claim, not after. See preflight_bar_counts.
    counts = preflight_bar_counts(
        symbols, prereg.data["interval"], start_ms, end_ms, db_path
    )
    short = {s: n for s, n in counts.items() if n != expected}
    if short:
        raise PortfolioRunError(
            f"expected {expected} bars per member; the store holds {short}. The registered "
            "detection floor, trade-count floor and diversification prediction all describe "
            "the full universe, so nothing here can be scored. The single-access holdout "
            "claim has NOT been consumed."
        )

    loaded = load_holdout_portfolio(
        symbols,
        start_ms,
        end_ms,
        strategy_id=prereg.strategy_id,
        i_understand_this_is_holdout_data=True,
        force_reclaim_reason=force_reclaim_reason,
        db_path=db_path,
        holdout_config_path=prereg.data["holdout_config_path"],
        runs_path=runs_path,
    )
    delivered = {s: len(k) for s, k in loaded.items() if len(k) != expected}
    if delivered:
        raise PortfolioRunError(
            f"the loader returned {delivered} against an expected {expected} despite the "
            "preflight agreeing -- the two disagree, so neither can be trusted"
        )

    grid = prereg.parameter_grid[0]
    lookbacks = list(grid["lookbacks"])
    fee = Decimal(str(prereg.procedure["fee_bps"]))
    slip = Decimal(str(prereg.procedure["slippage_bps"]))
    bars_per_day = int(prereg.procedure["bars_per_day"])
    per_member_equity = starting_equity / Decimal(len(symbols))

    results: list[MemberResult] = []
    for cfg in members_cfg:
        sym = cfg["storage_symbol"]
        metrics = run_member(
            loaded[sym],
            symbol=sym,
            lookbacks=lookbacks,
            fee_bps=fee,
            slippage_bps=slip,
            member_equity=per_member_equity,
            bars_per_day=bars_per_day,
        )
        results.append(MemberResult(symbol=sym, name=cfg["name"], metrics=metrics))
        LOGGER.info(
            "  %-18s %-14s trades=%3d sharpe=%s dd=%.2f%%",
            sym,
            cfg["name"],
            metrics.num_trades,
            f"{metrics.sharpe_ratio:.3f}" if metrics.sharpe_ratio is not None else "n/a",
            float(metrics.max_drawdown) * 100,
        )

    return evaluate(aggregate(results, bars_per_day=bars_per_day), prereg)


def format_report(result: PortfolioResult, prereg: Preregistration) -> str:
    c = prereg.primary_criterion
    floor = float(prereg.config["declared_detection_floor_sharpe"])
    lines = [
        "=" * 74,
        f"{prereg.preregistration_id}  —  {result.verdict}",
        "=" * 74,
        "",
        "Per member (equal weight, each sized to 1/N of starting equity):",
        f"  {'symbol':<18} {'name':<16} {'trades':>7} {'sharpe':>8} {'maxDD':>8} {'return':>9}",
    ]
    for m in result.members:
        mm = m.metrics
        lines.append(
            f"  {m.symbol:<18} {m.name:<16} {mm.num_trades:>7} "
            f"{(f'{mm.sharpe_ratio:.3f}' if mm.sharpe_ratio is not None else 'n/a'):>8} "
            f"{float(mm.max_drawdown)*100:>7.2f}% {float(mm.total_return)*100:>8.2f}%"
        )
    lines += [
        "",
        "Portfolio (computed on the aggregated curve, not averaged):",
        f"  PSR                 {result.psr if result.psr is None else round(result.psr, 4)}"
        f"   (>= {c['threshold']})  {'PASS' if result.gates.get('psr') else 'FAIL'}",
        f"  Sharpe              {result.sharpe_ratio if result.sharpe_ratio is None else round(result.sharpe_ratio, 4)}"
        f"   (> {floor} floor)  {'PASS' if result.gates.get('sharpe_above_detection_floor') else 'FAIL'}",
        f"  Max drawdown        {float(result.max_drawdown)*100:.2f}%"
        f"   (<= {float(c['max_drawdown_ceiling'])*100:.0f}%)  {'PASS' if result.gates.get('max_drawdown') else 'FAIL'}",
        f"  Profit factor       {result.profit_factor if result.profit_factor is None else round(result.profit_factor, 4)}"
        f"   (>= {c['profit_factor_floor']})  {'PASS' if result.gates.get('profit_factor') else 'FAIL'}",
        f"  Total trades        {result.total_trades}"
        f"   (>= {c['min_total_trades']})  {'PASS' if result.gates.get('min_total_trades') else 'FAIL'}",
        f"  Daily observations  {result.num_returns}",
        "",
    ]
    if result.sharpe_ratio is not None and result.sharpe_ratio <= floor:
        lines.append(
            "  NOT POWERED TO CONFIRM: the observed Sharpe does not exceed this window's "
            "own detection floor, so clearing the other criteria would not constitute "
            "confirmation."
        )
    lines.append(
        "  UNVERIFIED EXECUTION PREMISE: KIS serves no expired-contract history, so "
        "nothing confirms these names had listed, liquid futures throughout the window."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preregistration", required=True)
    p.add_argument("--db-path", default=DEFAULT_DB_PATH)
    p.add_argument("--runs-path", default=str(experiment_log.DEFAULT_RUNS_PATH))
    p.add_argument("--starting-equity", default="10000")
    p.add_argument("--force-reclaim-reason", default=None)
    p.add_argument("--universe-path", default=None)
    args = p.parse_args(argv)

    prereg = load_preregistration(args.preregistration)
    try:
        result = run(
            prereg,
            db_path=args.db_path,
            runs_path=args.runs_path,
            starting_equity=Decimal(args.starting_equity),
            force_reclaim_reason=args.force_reclaim_reason,
            universe_path=args.universe_path,
        )
    except PortfolioRunError as exc:
        LOGGER.error("%s", exc)
        return 1
    print(format_report(result, prereg))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
