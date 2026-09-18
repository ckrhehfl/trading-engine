"""Task E's state machine -- a trader's scenario thinking, written so it can be tested.

Contract: [`tm-e-scenario-playbook-preregistration.md`](../../../.planning/tm-e-scenario-playbook-preregistration.md).
Reasoning: [`tm-e-scenario-playbook-design.md`](../../../.planning/tm-e-scenario-playbook-design.md).

A discretionary trader imagines several paths and a response to each. The
part that makes that rigorous rather than a feeling is **exhaustiveness**:
at every bar exactly one branch applies. A state machine with a hole in it
silently holds, which is a position taken by accident -- and that is not a
hypothetical, it is Task C, whose parameter-free exit pinned its holding
period to one hour without anyone choosing it.

So `classify_branch` **raises** on a state no branch covers, rather than
falling through to `HOLD`. `HOLD` is reached only by matching its own
condition.

## What is fixed here and may not be tuned

Everything, per the registration's stopping rule. The management is Task
D's P3 unchanged -- scale 50% at +1R, trail the remainder at 3xATR(14),
stop at 1R, time exit at 10 sessions -- because it is the only policy in
project history to clear Gate A, and it is adopted rather than
re-searched.

## The one thing that would void a run

`eligible()` reads **`quote_volume[t-1]` and earlier, never
`quote_volume[t]`**. A daily bar's 거래대금 is cumulative over the whole
session, so it includes everything that traded after an opening-range
entry; ranking on it and then entering at the open picks the trade using
its own outcome. `test_scenario_playbook.py` sets `quote_volume[t]` to an
extreme for the selected names and asserts the selection does not move --
a filter that reads forward cannot pass that, and one that merely looks
right can.
"""

from __future__ import annotations

import datetime as dt
import math
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.krx_conjunction import INTERVAL, interleaved_sessions

#: rd-q's measured futures round trip, in bp. Charged per futures leg.
FUTURES_ROUND_TRIP_BP = 13.0

#: Single-stock futures contract size -- 10 shares, uniform across all 283
#: listed names (`rd-r`, from `fo_stk_code_mts.mst`). Not a guess.
CONTRACT_SHARES = 10

#: Task D's P3, adopted unchanged.
SCALE_AT_R = 1.0
SCALE_FRACTION = 0.5
TRAIL_ATR_MULT = 3.0
STOP_AT_R = 1.0
TIME_EXIT_SESSIONS = 10
ATR_PERIOD = 14

#: The activity filter's lookback, in sessions. One Korean trading month,
#: the same constant `rd-u` takes from Lou-Polk-Skouras. Not searched.
TURNOVER_LOOKBACK = 21

#: E3's hedge ratio, matching Task D's P5 so the comparison holds.
HEDGE_FRACTION = 0.5


class Playbook(StrEnum):
    """Which response contract a position is opened under.

    Selected by the **volatility axis only**. The structure axis is
    recorded and not acted on: S10 measured ADX carrying nothing on both
    axes it could have, and branching on a dead signal would be
    unsupported machinery presented as a design.
    """

    BREAKOUT = "breakout"
    """EXPANSION -- enter with the gap, betting on continuation."""

    FADE = "fade"
    """COMPRESSION -- enter against the gap, betting on reversion."""


class Policy(StrEnum):
    E0 = "E0"
    """One playbook always. The baseline, and not a strawman -- this is the
    configuration that cleared Gate A on another market."""

    E1 = "E1"
    """Regime selects the playbook; flat on invalidation."""

    E2 = "E2"
    """Regime selects; on invalidation the opposite thesis becomes live."""

    E3 = "E3"
    """Regime selects; the alternative is taken as a hedge leg, core kept."""


class Core(StrEnum):
    FUTURES = "futures"
    SPOT = "spot"


class Branch(StrEnum):
    """The exhaustive partition of what can happen on one bar.

    Ordered: the first matching branch fires. `HOLD` is last and is the
    explicit catch-all -- it is *matched*, never fallen into.
    """

    STOP = "stop"
    SCALE = "scale"
    TRAIL = "trail"
    TIME = "time"
    HOLD = "hold"


class StateMachineHole(AssertionError):
    """Raised when no branch matches. **Not a runtime failure -- a broken
    invariant.** A bar that matches nothing means the partition is not
    exhaustive, and the registration names that as voiding the run rather
    than producing a negative result."""


@dataclass(frozen=True)
class DailyPanel:
    """Dates x names, with everything the entry and management need."""

    dates: list[int]
    codes: list[str]
    open_px: np.ndarray
    high_px: np.ndarray
    low_px: np.ndarray
    close_px: np.ndarray
    quote_volume: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return self.open_px.shape


def load_daily_panel(conn: sqlite3.Connection, codes: list[str]) -> DailyPanel:
    """Align every name onto the dates they all have, with OHLC + 거래대금.

    Refuses a panel with an **interior hole** -- a session some name traded
    that the inner join dropped from inside the span. rd-p's gap rule on
    the daily axis: the overnight legs either side of such a hole span two
    nights and would be reported as one.
    """
    per: dict[str, dict[int, tuple[float, float, float, float, float]]] = {}
    for code in codes:
        rows = conn.execute(
            "SELECT open_time_ms, CAST(open AS REAL), CAST(high AS REAL), "
            "CAST(low AS REAL), CAST(close AS REAL), quote_volume FROM klines "
            "WHERE symbol=? AND interval=? ORDER BY open_time_ms",
            (f"KRX:{code}", INTERVAL),
        ).fetchall()
        if not rows:
            raise ValueError(f"no {INTERVAL} bars for KRX:{code}")
        per[code] = {
            int(r[0]): (
                float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                float(r[5]) if r[5] is not None else float("nan"),
            )
            for r in rows
        }

    common = sorted(set.intersection(*(set(v) for v in per.values())))
    if len(common) < TURNOVER_LOOKBACK + TIME_EXIT_SESSIONS + 2:
        raise ValueError(f"only {len(common)} common dates, too few to measure")
    holes = interleaved_sessions(common, sorted(set.union(*(set(v) for v in per.values()))))
    if holes:
        raise ValueError(
            f"{len(holes)} session(s) traded by some name were dropped from INSIDE "
            f"the common span (first {holes[0]})."
        )

    def col(i: int) -> np.ndarray:
        return np.array([[per[c][d][i] for c in codes] for d in common])

    return DailyPanel(common, list(codes), col(0), col(1), col(2), col(3), col(4))


def relative_turnover(panel: DailyPanel) -> np.ndarray:
    """`quote_volume[t-1]` over its own median across `t-21 .. t-1`.

    **Strictly `t-1` and earlier.** `quote_volume[t]` is never touched --
    it is the whole of day `t`'s session, so reading it to select a trade
    entered at day `t`'s open uses the outcome to pick the trade.
    """
    qv = panel.quote_volume
    out = np.full(qv.shape, np.nan)
    for t in range(TURNOVER_LOOKBACK + 1, qv.shape[0]):
        window = qv[t - TURNOVER_LOOKBACK : t]        # excludes t
        with np.errstate(invalid="ignore"):
            med = np.nanmedian(window, axis=0)
        prev = qv[t - 1]
        out[t] = np.where((med > 0) & np.isfinite(prev), prev / med, np.nan)
    return out


def eligible(panel: DailyPanel) -> np.ndarray:
    """Names in the **top half** of that day's relative-turnover cross-section.

    A median split, so there is no threshold to fit. Zarattini's own
    relative-volume cutoff is intraday and cannot transfer, and a
    cross-sectional median is the parameter-free substitute -- the same
    device `rd-u` used for the same reason.
    """
    rel = relative_turnover(panel)
    out = np.zeros(rel.shape, dtype=bool)
    for t in range(rel.shape[0]):
        row = rel[t]
        ok = np.isfinite(row)
        if ok.sum() < 3:
            continue
        out[t, ok] = row[ok] > np.median(row[ok])
    return out


def wilder_atr(panel: DailyPanel, period: int = ATR_PERIOD) -> np.ndarray:
    """Wilder's ATR per name, seeded on the first `period` true ranges."""
    h, l, c = panel.high_px, panel.low_px, panel.close_px
    n, m = h.shape
    tr = np.full((n, m), np.nan)
    tr[0] = h[0] - l[0]
    for t in range(1, n):
        prev = c[t - 1]
        tr[t] = np.maximum(h[t] - l[t], np.maximum(np.abs(h[t] - prev), np.abs(l[t] - prev)))
    atr = np.full((n, m), np.nan)
    if n <= period:
        return atr
    atr[period] = np.nanmean(tr[1 : period + 1], axis=0)
    for t in range(period + 1, n):
        atr[t] = (atr[t - 1] * (period - 1) + tr[t]) / period
    return atr


def entry_direction(playbook: Playbook, open_px: float, prev_close: float) -> int:
    """`+1` long, `-1` short, `0` no signal.

    The gap decides the axis and the playbook decides the sign:
    **EXPANSION goes with the gap, COMPRESSION goes against it.** That
    inversion is the whole content of regime selection here, so it is one
    function and it is tested both ways.
    """
    if not (math.isfinite(open_px) and math.isfinite(prev_close)) or prev_close <= 0:
        return 0
    if open_px == prev_close:
        return 0
    with_gap = 1 if open_px > prev_close else -1
    return with_gap if playbook is Playbook.BREAKOUT else -with_gap


@dataclass
class Position:
    """One open position's state -- what a branch decision is made against."""

    code: str
    direction: int
    entry_px: float
    entry_index: int
    risk_per_share: float
    shares: float
    playbook: Playbook
    scaled: bool = False
    best_px: float = field(default=float("nan"))
    hedge_contracts: int = 0

    def r_multiple(self, px: float) -> float:
        if self.risk_per_share <= 0:
            return 0.0
        return self.direction * (px - self.entry_px) / self.risk_per_share


def classify_branch(
    pos: Position, index: int, high: float, low: float, atr: float
) -> Branch:
    """The exhaustive partition, evaluated in registered order.

    **Raises `StateMachineHole` rather than defaulting to `HOLD`.** The
    registration names a bar matching no branch as voiding the run, so
    this must be able to fail — and `test_scenario_playbook.py` proves it
    can by removing `HOLD`'s own condition and watching a real bar raise.
    """
    if not math.isfinite(high) or not math.isfinite(low):
        raise StateMachineHole(
            f"{pos.code} at index {index}: high={high} low={low} — a bar with no "
            f"prices cannot be classified, and holding through it would be a "
            f"decision made by accident."
        )

    adverse = pos.direction * (low - pos.entry_px) if pos.direction > 0 else pos.direction * (high - pos.entry_px)
    favourable = pos.direction * (high - pos.entry_px) if pos.direction > 0 else pos.direction * (low - pos.entry_px)
    r_adverse = adverse / pos.risk_per_share if pos.risk_per_share > 0 else 0.0
    r_favourable = favourable / pos.risk_per_share if pos.risk_per_share > 0 else 0.0

    # 1. stop — wins a same-bar tie against the scale, per S8 §3.7's
    #    MAE/MFE contract. The pessimistic resolution is the honest one.
    if r_adverse <= -STOP_AT_R:
        return Branch.STOP
    # 2. scale
    if not pos.scaled and r_favourable >= SCALE_AT_R:
        return Branch.SCALE
    # 3. trail — only after the scale, on the runner
    if pos.scaled and math.isfinite(atr) and math.isfinite(pos.best_px):
        trail = pos.best_px - pos.direction * TRAIL_ATR_MULT * atr
        touched = low <= trail if pos.direction > 0 else high >= trail
        if touched:
            return Branch.TRAIL
    # 4. time
    if index - pos.entry_index >= TIME_EXIT_SESSIONS:
        return Branch.TIME
    # 5. hold — matched by its own condition, never fallen into
    if True:
        return Branch.HOLD
    raise StateMachineHole(  # pragma: no cover - unreachable while HOLD matches
        f"{pos.code} at index {index}: no branch matched"
    )


def hedge_contracts(core_shares: float) -> int:
    """Whole single-stock-futures contracts for a 50% hedge, floored.

    **A core too small to hedge yields 0, and the caller must treat that
    as a fallback rather than as a hedge of size zero.** A policy that
    silently cannot act on some episodes is not being tested on them --
    Task C reported an aggregate before anyone noticed its hedge fired
    twice in 2,544 bars.
    """
    if not math.isfinite(core_shares) or core_shares <= 0:
        return 0
    return int(math.floor(core_shares * HEDGE_FRACTION / CONTRACT_SHARES))


#: The `rd-r` ten are all KOSPI names, so the tax schedule is read for
#: that market. Named rather than defaulted: `krx_tax_schedule.total_bp`
#: refuses to fall back on KOSPI's rate precisely because KONEX's is half
#: of it, and a silent default is how a future universe inherits the
#: wrong cost.
CORE_MARKET = "KOSPI"


def close_cost_bp(core: Core, trade_date: dt.date, calendar: list[dt.date]) -> float:
    """Cost of *closing* a core position, in bp — the quantity that makes
    the two cores different experiments.

    Spot pays 증권거래세 on the sell side, era-correct from `rd-s`'s
    schedule, which converts the statutory boundary to its trade-date
    equivalent so a trade in the seam is charged the rate that will apply
    when it settles. Futures pay a round trip and no transaction tax.

    The gap is ~13bp, the same magnitude Task D's P5 lost by on a market
    where it is **zero** — so it is the whole reason E3 is predicted to
    behave differently on the two cores.
    """
    if core is Core.FUTURES:
        return FUTURES_ROUND_TRIP_BP
    from research.krx_tax_schedule import total_bp

    return float(total_bp(CORE_MARKET, trade_date, calendar))


def as_date(ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).date()
