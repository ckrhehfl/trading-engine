"""Do any Korean intraday features predict anything? -- the S11 question, in Korea.

`scalp-s11-feature-ic.md` asked this on BTC and produced the two most
transferable results the scalping arc has: every price and momentum IC
came back **negative** (mean reversion at the hour scale), and **order
flow was uncorrelated with every price feature** (|r| <= 0.006), which
was the first time this project held two genuinely independent
information sources at once.

None of that transfers to Korea by assumption. This re-measures it here,
and it is the one piece of the five-part plan that genuinely needed new
work: the CSTI structure, the leg vocabulary, the volatility conditioner
and the management policy are all already found and are adopted rather
than re-searched.

**Discovery mode.** Unlimited looking, every trial logged, and the
promotion `N` is not incremented -- the output of this module is a
written specification, never a candidate. That is what makes it legitimate
to measure twenty things here when measuring twenty things through the
confirmation gate would be self-defeating.

## The genuinely new axis: a cross-section

`research/ic.py`'s own docstring records why this project only ever had
half the instrument:

> *"This is a TIME-SERIES IC, not the cross-sectional one. The
> conventional quant-equity IC correlates a feature across many assets at
> one instant. This project trades one symbol, so the correlation runs
> across many instants for one asset instead … breadth here comes from
> independent decisions in time, so it is bounded by how long a bet is
> held, not by how many symbols are traded."*

With a real universe that constraint lifts, and it is the single largest
structural change from BTC to Korea. Under `IR ~= IC * sqrt(breadth)`,
ten names decided independently at one instant is breadth a single symbol
cannot buy at any holding period. **Both ICs are reported**, because they
answer different questions: the time-series one asks *"is now a good
time?"*, the cross-sectional one asks *"which name, right now?"* -- and a
feature can easily carry one and not the other.

## Two gap rules, not one

[`rd-p`](../../.planning/rd-p-what-krx-intraday-could-resolve.md) found
that a **forward** return computed positionally on KRX silently spans the
11-minute closing auction or a 17.5-hour overnight gap, and built
`continuous_blocks` for it. That fix is reused here.

**The same problem exists on the trailing side and had not been
handled.** A 60-bar momentum feature computed at 09:05 reaches back
through the overnight gap into yesterday afternoon, and reports it as an
hour of movement. So a feature is defined only where its **entire
trailing window lies inside one contiguous run of 60-second bars**, and
is `None` otherwise. The cost is real -- the first `lookback` bars of
every session are unusable -- and it is reported rather than absorbed.

## What is deliberately not here

**No entry rule, no exit, no sizing, no P&L.** Measuring features is far
cheaper than backtesting strategies and far harder to overfit, which is
S8's whole argument for doing it first.

**No 투자자별 매매동향.** It is the one information source Korea has that
crypto does not, and it is the obvious third orthogonal axis -- but
collection only started 2026-09-14 and it cannot be backfilled. A month
of it cannot support an IC. It is the reason waiting is productive here,
and it is added when there is enough of it.

Run:

    python -m research.krx_signal_ic
    python -m research.krx_signal_ic --symbols 005930,000660
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.ic import benjamini_hochberg, pearson, spearman
from research.krx_intraday_power import BAR_MS, continuous_blocks

INTERVAL = "1m"

#: rd-r's futures-liquidity top 10 — the names a strategy would actually
#: trade, selected by a rule fixed before this study existed. The other
#: eight collected names were selected on SPOT turnover (`ms-e`), a
#: different rule, and mixing the two would blur what the universe means.
UNIVERSE = (
    "000660", "005930", "005380", "034020", "006400",
    "042700", "035420", "000270", "402340", "012450",
)

#: Minutes. rd-q re-priced the Korean cost floor from 30bp to ~13bp for
#: the futures-liquid names, which is what puts h=15 back in range —
#: rd-p §5 called it implausible at 30bp and interesting at 13bp.
HORIZONS = (15, 60, 240)

#: S8's own calibration, carried over: |IC| of 0.02–0.05 is a genuinely
#: useful signal, so individual features looking unimpressive is normal
#: and expected rather than a disappointment.
USABLE_IC = 0.02


@dataclass(frozen=True)
class Feature:
    """A number known at bar close, plus how far back it reaches.

    `lookback` is load-bearing rather than documentation: it is what
    decides where the trailing window would cross a gap, and therefore
    where the feature is undefined.
    """

    name: str
    lookback: int
    compute: object  # (open, high, low, close, volume, value) -> np.ndarray
    #: The input column this feature cannot be computed without. A feature
    #: whose input is absent must be **dropped and said so**, never left
    #: to report `n=0` — in a results table that is indistinguishable from
    #: "measured it, found nothing", which is the opposite conclusion.
    requires: str = "close"


def _trailing_return(closes: np.ndarray, n: int) -> np.ndarray:
    out = np.full(closes.size, np.nan)
    if closes.size > n:
        past = closes[:-n]
        out[n:] = np.where(past > 0, (closes[n:] - past) / np.where(past > 0, past, 1), np.nan)
    return out


def _realised_vol(closes: np.ndarray, n: int) -> np.ndarray:
    out = np.full(closes.size, np.nan)
    if closes.size <= n:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        step = np.diff(closes) / np.where(closes[:-1] > 0, closes[:-1], np.nan)
    for i in range(n, closes.size):
        window = step[i - n : i]
        if np.isfinite(window).all():
            out[i] = float(np.std(window))
    return out


def _range_position(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Where the close sits inside the last `n` bars' range, 0..1.

    A flat range is `None`, not 0.5: a name that has not moved carries no
    information about where in its range it is, and imputing the midpoint
    would manufacture a neutral reading out of an absent one.
    """
    out = np.full(close.size, np.nan)
    for i in range(n, close.size):
        hi, lo = float(high[i - n : i + 1].max()), float(low[i - n : i + 1].min())
        if hi > lo:
            out[i] = (float(close[i]) - lo) / (hi - lo)
    return out


def _turnover_z(value: np.ndarray, n: int) -> np.ndarray:
    """Abnormal 거래대금 against this name's own recent normal.

    **The literature's own answer to what matters**, per `rd-c` §2:
    Zarattini's "Stocks in Play" gets its result from restricting a plain
    breakout to abnormally active names, and the same entry rule fails
    entirely with no filter. If any feature here carries something, the
    prior says it is this one.
    """
    out = np.full(value.size, np.nan)
    for i in range(n, value.size):
        window = value[i - n : i]
        mu, sd = float(window.mean()), float(window.std())
        if sd > 0:
            out[i] = (float(value[i]) - mu) / sd
    return out


FEATURES: tuple[Feature, ...] = (
    Feature("ret_5", 5, lambda o, h, l, c, v, val: _trailing_return(c, 5)),
    Feature("ret_15", 15, lambda o, h, l, c, v, val: _trailing_return(c, 15)),
    Feature("ret_60", 60, lambda o, h, l, c, v, val: _trailing_return(c, 60)),
    Feature("rvol_15", 15, lambda o, h, l, c, v, val: _realised_vol(c, 15)),
    Feature("rvol_60", 60, lambda o, h, l, c, v, val: _realised_vol(c, 60)),
    Feature("range_pos_60", 60, lambda o, h, l, c, v, val: _range_position(h, l, c, 60)),
    Feature("turnover_z_60", 60, lambda o, h, l, c, v, val: _turnover_z(val, 60), "value"),
    Feature("volume_z_60", 60, lambda o, h, l, c, v, val: _turnover_z(v, 60)),
)


@dataclass(frozen=True)
class SymbolBars:
    code: str
    open_time_ms: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    value: np.ndarray
    blocks: np.ndarray


def load(conn: sqlite3.Connection, code: str) -> SymbolBars:
    rows = conn.execute(
        "SELECT open_time_ms, CAST(open AS REAL), CAST(high AS REAL), "
        "CAST(low AS REAL), CAST(close AS REAL), CAST(volume AS REAL), "
        "CAST(COALESCE(quote_volume, 0) AS REAL) FROM klines "
        "WHERE symbol=? AND interval=? ORDER BY open_time_ms",
        (f"KRX:{code}", INTERVAL),
    ).fetchall()
    if not rows:
        raise ValueError(
            f"no {INTERVAL} bars for KRX:{code}. The instance collects these now; "
            f"run scripts/sync-krx-from-instance.sh before research."
        )
    cols = [np.array([r[i] for r in rows], dtype=float) for i in range(1, 7)]
    t = np.array([r[0] for r in rows], dtype=np.int64)
    return SymbolBars(code, t, *cols, blocks=continuous_blocks(t))


def defined_mask(blocks: np.ndarray, lookback: int) -> np.ndarray:
    """True where a `lookback`-bar trailing window stays inside one block.

    **The trailing half of rd-p's correction**, which rd-p itself did not
    need and so did not build. A feature reaching back across the
    overnight gap reports yesterday afternoon as this morning's movement,
    and nothing downstream can tell.
    """
    if blocks.size == 0:
        return np.array([], dtype=bool)
    out = np.zeros(blocks.size, dtype=bool)
    if blocks.size > lookback:
        out[lookback:] = blocks[lookback:] == blocks[: blocks.size - lookback]
    return out


def forward_return(bars: SymbolBars, horizon: int) -> np.ndarray:
    """Return from the open after `i` to the open `horizon` bars later.

    `nan` wherever entry and exit are not in the same contiguous block —
    the same rule `session_forward_return` applies, kept aligned to the
    bar index here so a feature can be paired with it.
    """
    n = bars.open.size
    out = np.full(n, np.nan)
    entry_i = np.arange(1, max(1, n - horizon))
    exit_i = entry_i + horizon
    ok = (exit_i < n) & (bars.blocks[entry_i] == bars.blocks[exit_i])
    e, x = entry_i[ok], exit_i[ok]
    base = bars.open[e]
    good = base > 0
    out[e[good] - 1] = (bars.open[x[good]] - base[good]) / base[good]
    return out


def _stats(xs: list[float], ys: list[float]) -> tuple[float | None, float | None, float | None]:
    """`(rank_ic, pearson_ic, p_value)` for one paired sample."""
    import math
    from statistics import NormalDist

    rank_ic = spearman(xs, ys)
    lin = pearson(xs, ys)
    p = None
    if rank_ic is not None and len(xs) > 2 and abs(rank_ic) < 1.0:
        t = rank_ic * math.sqrt((len(xs) - 2) / (1 - rank_ic**2))
        p = 2 * (1 - NormalDist().cdf(abs(t)))
    elif rank_ic is not None and abs(rank_ic) >= 1.0:
        p = 0.0
    return rank_ic, lin, p


@dataclass(frozen=True)
class IcRow:
    feature: str
    horizon: int
    kind: str
    n: int
    rank_ic: float | None
    pearson_ic: float | None
    p_value: float | None

    @property
    def usable(self) -> bool:
        return self.rank_ic is not None and abs(self.rank_ic) >= USABLE_IC


def timeseries_ic(
    universe: list[SymbolBars], values: dict[str, np.ndarray], feature: Feature, horizon: int
) -> IcRow:
    """Pooled across names: does this feature time the market?

    Sampled every `horizon` bars **within each contiguous block**, so no
    two forward windows overlap and none spans a gap.
    """
    xs: list[float] = []
    ys: list[float] = []
    for bars in universe:
        f = values[bars.code]
        fwd = forward_return(bars, horizon)
        ok = defined_mask(bars.blocks, feature.lookback) & np.isfinite(f) & np.isfinite(fwd)
        idx = np.flatnonzero(ok)
        # Step by the horizon so forward windows never overlap. Done per
        # symbol rather than globally, since the usable indices differ.
        for i in idx[:: max(horizon, 1)]:
            xs.append(float(f[i]))
            ys.append(float(fwd[i]))
    rank_ic, lin, p = _stats(xs, ys)
    return IcRow(feature.name, horizon, "time-series", len(xs), rank_ic, lin, p)


def cross_sectional_ic(
    universe: list[SymbolBars], values: dict[str, np.ndarray], feature: Feature, horizon: int
) -> IcRow:
    """At each instant, rank names by the feature and by what follows.

    **The IC this project could never compute before**, because it had one
    symbol. Reported as the mean of the per-instant rank correlations, the
    conventional quant-equity construction; `n` is the number of instants
    that had at least three names to rank.

    **Instants are chosen on a shared grid, not per symbol.** Stepping by
    the horizon inside each name lands different names on different
    minutes, so the intersection collapses -- measured at 3,840 instants
    at h=15 but **143** at h=60 and **20** at h=240, which is a sampling
    artefact and not a property of the market. The grid is built once over
    the union timeline and every name is asked about the same minute.
    """
    # Every minute any name could be sampled at, then thinned by the
    # horizon ONCE so forward windows do not overlap in time.
    usable: dict[str, dict[int, tuple[float, float]]] = {}
    all_ms: set[int] = set()
    for bars in universe:
        f = values[bars.code]
        fwd = forward_return(bars, horizon)
        ok = defined_mask(bars.blocks, feature.lookback) & np.isfinite(f) & np.isfinite(fwd)
        idx = np.flatnonzero(ok)
        usable[bars.code] = {
            int(bars.open_time_ms[i]): (float(f[i]), float(fwd[i])) for i in idx
        }
        all_ms.update(usable[bars.code])

    grid = sorted(all_ms)[:: max(horizon, 1)]
    per_instant: list[float] = []
    for ms in grid:
        pairs = [usable[b.code][ms] for b in universe if ms in usable[b.code]]
        if len(pairs) < 3:
            continue
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if rho is not None:
            per_instant.append(rho)
    if not per_instant:
        return IcRow(feature.name, horizon, "cross-sectional", 0, None, None, None)

    import math
    from statistics import NormalDist

    mean = float(np.mean(per_instant))
    sd = float(np.std(per_instant, ddof=1)) if len(per_instant) > 1 else 0.0
    p = None
    if sd > 0:
        t = mean / (sd / math.sqrt(len(per_instant)))
        p = 2 * (1 - NormalDist().cdf(abs(t)))
    return IcRow(feature.name, horizon, "cross-sectional", len(per_instant), mean, None, p)


def orthogonality(
    universe: list[SymbolBars], values: dict[str, np.ndarray],
    features: list[Feature] | None = None,
) -> dict[tuple[str, str], float]:
    """Pairwise |rank correlation| between features, pooled across names.

    **This decides how many signals there are, not how many features.**
    S11's ten survivors were about three signals; expecting the same
    collapse here is the prior, and `IR ~= IC * sqrt(breadth)` takes the
    number of *independent* ones.
    """
    features = features or list(FEATURES)
    out: dict[tuple[str, str], float] = {}
    names = [f.name for f in features]
    look = {f.name: f.lookback for f in features}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            xs: list[float] = []
            ys: list[float] = []
            for bars in universe:
                fa, fb = values[bars.code + "|" + a], values[bars.code + "|" + b]
                ok = (
                    defined_mask(bars.blocks, max(look[a], look[b]))
                    & np.isfinite(fa)
                    & np.isfinite(fb)
                )
                for k in np.flatnonzero(ok)[::60]:
                    xs.append(float(fa[k]))
                    ys.append(float(fb[k]))
            rho = spearman(xs, ys)
            if rho is not None:
                out[(a, b)] = abs(rho)
    return out


def available_features(universe: list[SymbolBars]) -> tuple[list[Feature], list[str]]:
    """`(computable, reasons the rest were dropped)`.

    **`quote_volume` is NULL on every KRX intraday bar**, and that is not
    a collection failure: `acml_tr_pbmn` is cumulative within a session,
    so KIS never serves a per-minute 거래대금 at all (`kis_intraday.py`
    leaves it unpopulated deliberately). Daily bars do carry it.

    The consequence is worth naming because it costs the study its best
    prior: `rd-c` §2 records that the literature's strongest filter is
    **abnormal turnover** — Zarattini's "Stocks in Play" gets Sharpe 2.81
    from restricting a plain breakout to abnormally active names, and the
    same rule fails entirely unencumbered. That exact feature cannot be
    computed here. Share volume is carried as the available proxy, and it
    is a proxy: it misses that the same share count is a different amount
    of money at different prices.
    """
    have_value = any(np.any(b.value > 0) for b in universe)
    keep, dropped = [], []
    for f in FEATURES:
        if f.requires == "value" and not have_value:
            dropped.append(
                f"{f.name}: needs per-bar 거래대금, which KIS does not serve intraday "
                f"(acml_tr_pbmn is cumulative). NOT measured — not 'measured and flat'."
            )
        else:
            keep.append(f)
    return keep, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=",".join(UNIVERSE))
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    try:
        universe = [load(conn, c) for c in codes]
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    bars_total = sum(b.open.size for b in universe)
    print(
        f"universe: {len(universe)} names, {bars_total:,} bars, "
        f"{len(set(np.concatenate([b.blocks for b in universe]).tolist())):,} contiguous blocks\n"
    )

    features, dropped = available_features(universe)
    for line in dropped:
        print(f"DROPPED {line}")
    if dropped:
        print()

    per_feature: dict[str, dict[str, np.ndarray]] = {}
    flat: dict[str, np.ndarray] = {}
    for f in features:
        per_feature[f.name] = {}
        for bars in universe:
            v = np.asarray(
                f.compute(bars.open, bars.high, bars.low, bars.close, bars.volume, bars.value),
                dtype=float,
            )
            per_feature[f.name][bars.code] = v
            flat[bars.code + "|" + f.name] = v

    rows: list[IcRow] = []
    for f in features:
        for h in HORIZONS:
            rows.append(timeseries_ic(universe, per_feature[f.name], f, h))
            rows.append(cross_sectional_ic(universe, per_feature[f.name], f, h))

    flags = benjamini_hochberg([r.p_value for r in rows])
    print(f"{'feature':<15} {'h':>4} {'kind':<16} {'n':>9} {'rank IC':>9} {'p':>10}  ")
    print("-" * 74)
    for row, survives in zip(rows, flags):
        ic = f"{row.rank_ic:+.4f}" if row.rank_ic is not None else "-"
        p = f"{row.p_value:.2e}" if row.p_value is not None else "-"
        mark = "  <<" if (survives and row.usable) else ""
        print(
            f"{row.feature:<15} {row.horizon:>4} {row.kind:<16} {row.n:>9,} "
            f"{ic:>9} {p:>10}{mark}"
        )

    kept = [r for r, s in zip(rows, flags) if s and r.usable]
    print(
        f"\n{len(kept)} of {len(rows)} clear BOTH the |IC| >= {USABLE_IC} floor and "
        f"Benjamini-Hochberg at alpha=0.05."
    )
    if not kept:
        print(
            "  Nothing carries. That is a finding about these features on this "
            "market, not about the market."
        )

    print("\n=== orthogonality: how many SIGNALS, not how many features ===")
    pairs = orthogonality(universe, flat, features)
    strong = sorted(pairs.items(), key=lambda kv: -kv[1])[:8]
    for (a, b), r in strong:
        note = "  (same signal)" if r >= 0.5 else ""
        print(f"  |rho|={r:.3f}  {a:<15} vs {b:<15}{note}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
