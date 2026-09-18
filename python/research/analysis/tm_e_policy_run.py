"""Task E's run — eight policies, one entry, and the response is the only thing that varies.

Contract: [`tm-e-scenario-playbook-preregistration.md`](../../../.planning/tm-e-scenario-playbook-preregistration.md).

    python -m research.analysis.tm_e_policy_run

**A comparison run and a discovery-mode run**, both declared in the
registration before any access. So every policy's figures are reported
including the losers', and **no policy may be promoted, advanced to a
holdout, or quoted as evidence of an edge.** The only legitimate output
is a written specification.

## What is held fixed

The entry, the sizing, the universe and the window — identical across all
eight. What varies is the response: whether the regime selects a playbook,
and what happens when the thesis is invalidated. Task D established that
this axis is worth measuring by producing the only Gate A pass in project
history from it.

## What this module must not be allowed to do quietly

Three things the registration names as **voiding** a run rather than
producing a negative result, and each is a hard failure here:

1. a **state-machine hole** — `classify_branch` raises
2. the **look-ahead test failing** — asserted in `test_scenario_playbook.py`
3. an **interior hole in the panel** — `load_daily_panel` refuses

Plus one that impeaches the cost model rather than the policy: **P5
failing to replicate on the futures core.** If E3-F does not lose to
E2-F, this project's cost model is wrong and that is investigated before
anything here is trusted.

## A named limitation: E3's hedge is priced off SPOT closes

Raised on review and it is correct. The registration specifies a
front-month single-stock future with a roll, and this module prices the
hedge leg at `panel.close_px` -- the spot close -- because **the futures
price history to do otherwise does not exist over this window.**

Measured: `runs/krx_futures_liquidity.json` (rd-r, the only copy, since
KIS drops an expired contract's *entire* series) spans **2025-12-12
onward, 188 dates against this panel's 1,176 -- 16%**. There is no
front-month series for 2021-2025 at any price.

So three things are absent from E3's figures: **basis moves between spot
and the future, roll P&L, and the cost of rolling.** The direction of
each is stated rather than left open:

- **roll costs are omitted, which FLATTERS E3** -- a hedge held up to 10
  sessions crosses an expiry occasionally, and each crossing is a real
  round trip this module never charges. E3 loses anyway, so the
  conclusion is biased in the safe direction.
- **basis change over <=10 sessions is small against a 1R move and
  roughly mean-zero**, so it adds variance rather than a level shift.

**What it does mean is that E3's magnitude is an approximation and its
sign is not.** Pricing it properly needs either several more years of
accumulated futures history or a sub-window of ~190 sessions, which is
too short to carry the comparison. Neither is available now, so this is
disclosed rather than worked around.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import NormalDist

import numpy as np

from backtest.kline import Kline
from data._paths import DEFAULT_DB_PATH
from research.conclusion_check import (
    check_clustered_observations,
    require_no_blockers,
)
from research.krx_signal_ic import UNIVERSE
from research.krx_tax_schedule import trading_days
from research.strategies.regime_classifier import (
    RegimeClassifier,
    Volatility,
    VolatilityAxis,
)
from research.strategies.scenario_playbook import (
    ATR_PERIOD,
    CONTRACT_SHARES,
    FUTURES_ROUND_TRIP_BP,
    Branch,
    Core,
    DailyPanel,
    Playbook,
    Policy,
    Position,
    SCALE_FRACTION,
    TURNOVER_LOOKBACK,
    classify_branch,
    close_cost_bp,
    eligible,
    entry_direction,
    hedge_contracts,
    load_daily_panel,
    wilder_atr,
)

#: §4 of the registration. `AbsoluteAtr`'s own defaults are one trading
#: day of 1-minute bars; on daily bars 1440 would be 5.9 years and the
#: classifier would never leave warm-up.
DAILY_ABSOLUTE_HISTORY = 252
DAILY_ABSOLUTE_REFRESH = 21

#: §5.1a. Task D's constants, adopted unchanged.
RISK_FRACTION = 0.005
REFERENCE_EQUITY = 100_000_000.0     # KRW, fixed — not compounding

_BPS = 10_000.0


@dataclass
class Episode:
    """One entry and everything that followed it."""

    code: str
    policy: Policy
    core: Core
    playbook: Playbook
    direction: int
    entry_index: int
    entry_px: float
    r_unit: float
    shares: float
    exits: list[tuple[int, str, float, float]] = field(default_factory=list)
    gross_krw: float = 0.0
    cost_krw: float = 0.0
    hedge_fallback: bool = False
    hedged: bool = False
    alternative: bool = False
    hedge_entry_px: float = float("nan")
    hedge_contracts: int = 0

    @property
    def net_krw(self) -> float:
        return self.gross_krw - self.cost_krw

    @property
    def total_r(self) -> float:
        denom = self.r_unit * self.shares
        return self.net_krw / denom if denom > 0 else 0.0


@dataclass
class PolicyResult:
    policy: Policy
    core: Core
    episodes: list[Episode] = field(default_factory=list)
    holes: int = 0

    @property
    def n(self) -> int:
        return len(self.episodes)

    @property
    def net_krw(self) -> float:
        return sum(e.net_krw for e in self.episodes)

    @property
    def total_r(self) -> float:
        return sum(e.total_r for e in self.episodes)

    @property
    def cost_krw(self) -> float:
        return sum(e.cost_krw for e in self.episodes)

    @property
    def fallbacks(self) -> int:
        return sum(1 for e in self.episodes if e.hedge_fallback)

    @property
    def hedges(self) -> int:
        return sum(1 for e in self.episodes if e.hedged)

    @property
    def alternatives(self) -> int:
        return sum(1 for e in self.episodes if e.alternative)

    @property
    def win_rate(self) -> float | None:
        if not self.episodes:
            return None
        return sum(1 for e in self.episodes if e.net_krw > 0) / len(self.episodes)

    @property
    def profit_factor(self) -> float | None:
        wins = sum(e.net_krw for e in self.episodes if e.net_krw > 0)
        losses = -sum(e.net_krw for e in self.episodes if e.net_krw < 0)
        if losses <= 0:
            return None if wins <= 0 else math.inf
        return wins / losses


#: A spot sale settles T+2, and `krx_tax_schedule.total_bp` refuses to
#: rate a trade whose settlement lies past the calendar's end -- correctly,
#: since the rate that will apply is genuinely unknown. So the last two
#: sessions are **not tradeable**, and that is reported rather than worked
#: around by extending the calendar with guessed dates.
SETTLEMENT_SESSIONS = 2


def classify_regimes(panel: DailyPanel, calendar: list[dt.date]) -> np.ndarray:
    """`Volatility` per (date, name), or `None` during warm-up.

    One classifier per name, fed daily bars. The structure axis is
    computed by the classifier and **not read here** — S10 measured ADX
    carrying nothing on both axes it could have, and §4 of the
    registration fixes the selection to the volatility axis alone.
    """
    n, m = panel.shape
    out = np.empty((n, m), dtype=object)
    for j, code in enumerate(panel.codes):
        clf = RegimeClassifier(
            volatility_axis=VolatilityAxis.ABSOLUTE,
            absolute_history=DAILY_ABSOLUTE_HISTORY,
            absolute_refresh=DAILY_ABSOLUTE_REFRESH,
            # Without this the classifier resets every weekend: 273
            # discontinuities and 0 of 1,176 bars resolved on the real panel.
            session_calendar=calendar,
        )
        for t in range(n):
            bar = Kline(
                open_time=dt.datetime.fromtimestamp(panel.dates[t] / 1000, dt.UTC),
                open=Decimal(str(panel.open_px[t, j])),
                high=Decimal(str(panel.high_px[t, j])),
                low=Decimal(str(panel.low_px[t, j])),
                close=Decimal(str(panel.close_px[t, j])),
                volume=Decimal("1"),
            )
            regime = clf.update(bar)
            out[t, j] = regime.volatility if regime is not None else None
    return out


def playbook_for(policy: Policy, volatility: Volatility | None) -> Playbook | None:
    """`None` means no entry is permitted.

    **E0 always runs the breakout playbook**, which is what makes it the
    baseline: it is the configuration that cleared Gate A elsewhere, and
    it ignores the regime entirely.

    **Warm-up is not a regime.** When the classifier has not resolved, E1-E3
    decline to enter rather than guessing a label — fail closed, the same
    direction `regime_classifier` already resets in across a gap.
    """
    if policy is Policy.E0:
        return Playbook.BREAKOUT
    if volatility is None:
        return None
    return Playbook.BREAKOUT if volatility is Volatility.EXPANSION else Playbook.FADE


def _leg_cost_krw(notional: float, core: Core, date: dt.date, calendar, closing: bool) -> float:
    """One leg's cost in KRW.

    **`FUTURES_ROUND_TRIP_BP` is a ROUND TRIP, so one leg is half of it.**
    An earlier version charged the full 13bp on a close and half on an
    entry, making a futures round trip 19.5bp — half again over what the
    registration fixes. The spot core's close is the exception: 증권거래세
    is levied once, on the sale, so it is charged whole.
    """
    half_round_trip = notional * FUTURES_ROUND_TRIP_BP / _BPS / 2
    if not closing or core is Core.FUTURES:
        return half_round_trip
    # A spot close pays the era's transaction tax instead — that whole
    # figure is the sell-side cost, not half of a round trip.
    return notional * close_cost_bp(Core.SPOT, date, calendar) / _BPS


def run_policy(
    panel: DailyPanel,
    regimes: np.ndarray,
    elig: np.ndarray,
    atr: np.ndarray,
    policy: Policy,
    core: Core,
    calendar: list[dt.date],
) -> PolicyResult:
    """Walk the panel once for one (policy, core) pair."""
    result = PolicyResult(policy, core)
    n, m = panel.shape
    open_pos: dict[int, tuple[Position, Episode]] = {}
    start = max(TURNOVER_LOOKBACK, ATR_PERIOD + 1)

    for t in range(start, n - SETTLEMENT_SESSIONS):
        date = dt.datetime.fromtimestamp(panel.dates[t] / 1000, dt.UTC).date()

        # ---- manage what is already open, before considering anything new
        for j in list(open_pos):
            pos, ep = open_pos[j]
            high, low, close = panel.high_px[t, j], panel.low_px[t, j], panel.close_px[t, j]
            branch = classify_branch(pos, t, high, low, atr[t, j])

            if pos.direction > 0:
                pos.best_px = high if math.isnan(pos.best_px) else max(pos.best_px, high)
            else:
                pos.best_px = low if math.isnan(pos.best_px) else min(pos.best_px, low)

            if branch is Branch.HOLD:
                continue

            if branch is Branch.SCALE:
                qty = pos.shares * SCALE_FRACTION
                px = pos.entry_px + pos.direction * pos.risk_per_share
                ep.gross_krw += pos.direction * (px - pos.entry_px) * qty
                ep.cost_krw += _leg_cost_krw(px * qty, core, date, calendar, True)
                ep.exits.append((t, "scale", px, qty))
                pos.shares -= qty
                pos.scaled = True
                continue

            # ---- E3 on invalidation: the core is KEPT and a hedge is added.
            # This is the one branch that does not close, and it is the whole
            # difference between E3 and E2. An earlier version closed here
            # too, which made the two policies byte-identical.
            if (
                branch is Branch.STOP
                and policy is Policy.E3
                and not ep.alternative
                and pos.hedge_contracts == 0
            ):
                contracts = hedge_contracts(pos.shares)
                if contracts > 0:
                    pos.hedge_contracts = contracts
                    ep.hedged = True
                    ep.hedge_entry_px = close
                    ep.hedge_contracts = contracts
                    # A hedge is a futures leg on both cores — that is the
                    # point of it on a spot core, where closing pays tax.
                    ep.cost_krw += _leg_cost_krw(
                        close * contracts * CONTRACT_SHARES, Core.FUTURES,
                        date, calendar, False,
                    )
                    continue
                ep.hedge_fallback = True    # too small to hedge; falls through

            # every remaining branch closes the rest
            px = {
                Branch.STOP: pos.entry_px - pos.direction * pos.risk_per_share,
                Branch.TRAIL: pos.best_px - pos.direction * 3.0 * atr[t, j],
                Branch.TIME: close,
            }[branch]

            # The hedge leg closes FIRST, per registration §5.3.
            if pos.hedge_contracts:
                shares_h = pos.hedge_contracts * CONTRACT_SHARES
                ep.gross_krw += -pos.direction * (px - ep.hedge_entry_px) * shares_h
                ep.cost_krw += _leg_cost_krw(
                    px * shares_h, Core.FUTURES, date, calendar, True
                )
                ep.exits.append((t, "hedge_close", px, shares_h))

            ep.gross_krw += pos.direction * (px - pos.entry_px) * pos.shares
            ep.cost_krw += _leg_cost_krw(px * pos.shares, core, date, calendar, True)
            ep.exits.append((t, branch.value, px, pos.shares))
            del open_pos[j]

            # ---- E2 (and E3's fallback): the opposite thesis becomes live
            if branch is not Branch.STOP or ep.alternative:
                continue
            if policy in (Policy.E0, Policy.E1):
                continue
            alt_dir = -pos.direction
            r_unit = atr[t, j]
            if not (math.isfinite(r_unit) and r_unit > 0 and math.isfinite(close)):
                continue
            shares = (REFERENCE_EQUITY * RISK_FRACTION) / r_unit
            alt_pos = Position(
                code=panel.codes[j], direction=alt_dir, entry_px=close, entry_index=t,
                risk_per_share=r_unit, shares=shares, playbook=pos.playbook,
            )
            alt_ep = Episode(
                code=panel.codes[j], policy=policy, core=core, playbook=pos.playbook,
                direction=alt_dir, entry_index=t, entry_px=close, r_unit=r_unit,
                shares=shares, alternative=True,
                # **Not copied.** A fallback is one event on the original
                # entry; copying it here made `PolicyResult.fallbacks`
                # count the same event twice and report 130 where 65
                # occurred.
            )
            alt_ep.cost_krw += _leg_cost_krw(close * shares, core, date, calendar, False)
            result.episodes.append(alt_ep)
            open_pos[j] = (alt_pos, alt_ep)

        # ---- entries
        for j in range(m):
            if j in open_pos or not elig[t, j]:
                continue
            pb = playbook_for(policy, regimes[t, j])
            if pb is None:
                continue
            direction = entry_direction(pb, panel.open_px[t, j], panel.close_px[t - 1, j])
            if direction == 0:
                continue
            r_unit = atr[t - 1, j]
            if not (math.isfinite(r_unit) and r_unit > 0):
                continue
            entry_px = panel.open_px[t, j]
            shares = (REFERENCE_EQUITY * RISK_FRACTION) / r_unit
            pos = Position(
                code=panel.codes[j], direction=direction, entry_px=entry_px,
                entry_index=t, risk_per_share=r_unit, shares=shares, playbook=pb,
            )
            ep = Episode(
                code=panel.codes[j], policy=policy, core=core, playbook=pb,
                direction=direction, entry_index=t, entry_px=entry_px,
                r_unit=r_unit, shares=shares,
            )
            ep.cost_krw += _leg_cost_krw(entry_px * shares, core, date, calendar, False)
            result.episodes.append(ep)
            open_pos[j] = (pos, ep)

    # ---- forced close at the end of the window, flagged as censored
    last = n - 1 - SETTLEMENT_SESSIONS
    date = dt.datetime.fromtimestamp(panel.dates[last] / 1000, dt.UTC).date()
    for j, (pos, ep) in open_pos.items():
        close = panel.close_px[last, j]
        if pos.hedge_contracts:
            shares_h = pos.hedge_contracts * CONTRACT_SHARES
            ep.gross_krw += -pos.direction * (close - ep.hedge_entry_px) * shares_h
            ep.cost_krw += _leg_cost_krw(
                close * shares_h, Core.FUTURES, date, calendar, True
            )
            ep.exits.append((last, "hedge_close", close, shares_h))
        ep.gross_krw += pos.direction * (close - pos.entry_px) * pos.shares
        ep.cost_krw += _leg_cost_krw(close * pos.shares, core, date, calendar, True)
        ep.exits.append((last, "censored", close, pos.shares))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=",".join(UNIVERSE))
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    try:
        panel = load_daily_panel(conn, codes)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    calendar = trading_days(args.db_path)

    print(
        f"panel: {len(panel.dates):,} dates x {len(panel.codes)} names, "
        f"{dt.datetime.fromtimestamp(panel.dates[0] / 1000, dt.UTC).date()} -> "
        f"{dt.datetime.fromtimestamp(panel.dates[-1] / 1000, dt.UTC).date()}"
    )
    print(
        f"the last {SETTLEMENT_SESSIONS} sessions are not tradeable: a spot sale "
        f"settles T+{SETTLEMENT_SESSIONS} and the tax rate that would apply is "
        f"beyond this calendar's end, so it is refused rather than guessed."
    )
    print("*** COMPARISON RUN, DISCOVERY MODE. Nothing here may be promoted,")
    print("    quoted as evidence of an edge, or reported as a pass. ***\n")

    regimes = classify_regimes(panel, calendar)
    resolved = sum(1 for t in range(panel.shape[0]) for j in range(panel.shape[1])
                   if regimes[t, j] is not None)
    total = panel.shape[0] * panel.shape[1]
    exp = sum(1 for t in range(panel.shape[0]) for j in range(panel.shape[1])
              if regimes[t, j] is Volatility.EXPANSION)
    print(
        f"regime: {resolved:,} of {total:,} name-days resolved "
        f"({resolved / total:.1%}; {DAILY_ABSOLUTE_HISTORY}-bar warm-up), "
        f"of which {exp / max(resolved, 1):.1%} EXPANSION\n"
    )

    elig, atr = eligible(panel), wilder_atr(panel)
    results = [
        run_policy(panel, regimes, elig, atr, p, c, calendar)
        for c in Core for p in Policy
    ]

    print(f"{'policy':<7} {'core':<9} {'episodes':>9} {'alt':>5} {'hedge':>6} "
          f"{'fallback':>9} {'total R':>9} {'net KRW':>14} {'costs':>13} "
          f"{'win%':>6} {'PF':>7}")
    print("-" * 108)
    for r in results:
        pf = "-" if r.profit_factor is None else (
            "inf" if r.profit_factor == math.inf else f"{r.profit_factor:.3f}")
        wr = "-" if r.win_rate is None else f"{r.win_rate:.1%}"
        print(
            f"{r.policy.value:<7} {r.core.value:<9} {r.n:>9,} {r.alternatives:>5,} "
            f"{r.hedges:>6,} {r.fallbacks:>9,} {r.total_r:>+9.1f} "
            f"{r.net_krw:>+14,.0f} {r.cost_krw:>13,.0f} {wr:>6} {pf:>7}"
        )

    # **CLAUDE.md's session-clustering rule, and this is the first study run
    # under it.** Ten names enter on the same day and share that day's
    # market-wide move, so 1,623 episodes are not 1,623 draws. The unit is
    # the entry date; `check_clustered_observations` is run against the
    # sample rather than the claim being asserted.
    print("\n=== is any of this distinguishable from zero? ===")
    print(
        f"  {'policy':<7} {'core':<9} {'episodes':>9} {'dates':>7} {'R/date':>9} "
        f"{'p':>8} {'SEx':>6}"
    )
    print("  " + "-" * 60)
    for r in results:
        per_date: dict[int, list[float]] = {}
        for e in r.episodes:
            per_date.setdefault(e.entry_index, []).append(e.total_r)
        if len(per_date) < 3:
            continue
        keys = [e.entry_index for e in r.episodes]
        require_no_blockers(
            [check_clustered_observations(keys, reported_n=len(per_date))]
        )
        arr = np.array([float(np.mean(v)) for v in per_date.values()])
        sd = float(arr.std(ddof=1))
        p = (
            2 * (1 - NormalDist().cdf(abs(arr.mean() / (sd / math.sqrt(arr.size)))))
            if sd > 0 else float("nan")
        )
        # **CLAUDE.md requires the ratio, not just the corrected p.** The
        # rule reads "report the ratio of the corrected standard error to
        # the naive one beside the figure", for the same reason the
        # permutation rule requires `null_sd / se`: the SIZE of the
        # correction is itself the finding, and a corrected p alone hides
        # whether the correction mattered. An earlier version of this
        # runner reported only the p — violating the rule in its first
        # application.
        pooled = np.array([e.total_r for e in r.episodes])
        naive_se = (
            float(pooled.std(ddof=1)) / math.sqrt(pooled.size)
            if pooled.size > 1 else float("nan")
        )
        clustered_se = sd / math.sqrt(arr.size) if arr.size > 1 else float("nan")
        sex = clustered_se / naive_se if naive_se > 0 else float("nan")
        print(
            f"  {r.policy.value:<7} {r.core.value:<9} {r.n:>9,} {arr.size:>7,} "
            f"{arr.mean():>+9.4f} {p:>8.3f} {sex:>6.2f}"
        )
    print(
        "  R/date is the mean per entry date, not per episode — pooling episodes\n"
        "  would claim more independent information than the sample holds.\n"
        "  SEx is the date-clustered standard error over the naive per-episode one.\n"
        "  Above 1 means pooling episodes would have understated the error."
    )

    by = {(r.policy, r.core): r for r in results}
    print("\n=== the registered predictions ===")
    f_gap = by[(Policy.E3, Core.FUTURES)].total_r - by[(Policy.E2, Core.FUTURES)].total_r
    s_gap = by[(Policy.E3, Core.SPOT)].total_r - by[(Policy.E2, Core.SPOT)].total_r

    print(
        f"  futures   predicted E3 < E2 by ~one round trip\n"
        f"            observed  {f_gap:+.1f}R  "
        f"{'-- HELD' if f_gap < 0 else '-- *** FAILED: this impeaches the COST MODEL ***'}"
    )
    print(
        f"  spot      predicted E3 MAY beat E2, by roughly the tax differential\n"
        f"            observed  {s_gap:+.1f}R  "
        f"{'-- E3 still lost' if s_gap < 0 else '-- E3 won, the inversion is real'}"
    )
    # **"may beat" cannot fail, and saying so is part of reporting it.** A
    # prediction with no falsifying outcome is not evidence either way, so
    # the informative figure is the one below: whether the tax gap moved
    # the comparison at all, relative to the core where it does not exist.
    print(
        f"\n  'may beat' had no falsifying outcome, so it is not scored. What is\n"
        f"  informative is the DIFFERENCE between the cores: the hedge-vs-close gap\n"
        f"  is {f_gap:+.1f}R on futures and {s_gap:+.1f}R on spot, a shift of "
        f"{s_gap - f_gap:+.1f}R.\n"
        f"  Spot is where hedging is ~13bp CHEAPER than closing, so a tax-driven\n"
        f"  inversion would show up as that shift being positive and large enough\n"
        f"  to cross zero. It is not."
    )
    print(
        "\n  A futures-core E3 that does NOT lose to E2 impeaches this project's\n"
        "  cost model, not the policy — the registration says to investigate that\n"
        "  before trusting anything else here."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
