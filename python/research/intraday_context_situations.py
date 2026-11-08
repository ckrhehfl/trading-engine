"""Pure EI pre-outcome situations and separate price/volume support.

Only the requested completed prefix is visited. Stored units and the caller's
calendar are not certified here. Situation A/B describe observations; neither
is an entry instruction, a future label or an estimate of profitability.
"""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from research.intraday_chart_inputs import _clock, _need, _session
from research.intraday_ed_inputs import MINUTE, STATES, Minute, SessionInputs


WINDOWS = (30, 60, 120)
OHLC = tuple[Fraction, Fraction, Fraction, Fraction]


@dataclass(frozen=True)
class ContextMinute:
    start_at: datetime
    available_at: datetime | None
    prices: OHLC | None
    volume: Fraction | None
    price_state: str
    volume_state: str


@dataclass(frozen=True)
class WindowSupport:
    minutes: int
    in_session_minutes: int
    price_counts: tuple[tuple[str, int], ...]
    volume_counts: tuple[tuple[str, int], ...]
    zero_volume_minutes: int
    volume_total: Fraction | None

    @property
    def price_complete(self) -> bool:
        return self.price_counts == (("observed", self.minutes),)

    @property
    def volume_complete(self) -> bool:
        return self.volume_counts == (("observed", self.minutes),)


@dataclass(frozen=True)
class ResistanceAnchor:
    start_at: datetime
    start_price: Fraction
    peak_end: datetime
    confirmed_at: datetime
    price: Fraction
    pre_peak_range: Fraction
    advance: Fraction
    band: Fraction


@dataclass(frozen=True)
class ContextMetrics:
    advance_in_ranges: Fraction
    pullback_depth: Fraction
    bars_since_peak: int
    first_pullback_at: datetime | None
    resumption_at: datetime | None
    retest_episodes: int
    latest_overlaps_resistance: bool
    invalidated_at: datetime | None


@dataclass(frozen=True)
class ContextFrame:
    symbol: str
    opening: datetime
    observation_end: datetime
    available_at: datetime
    prefix: tuple[ContextMinute, ...]
    prefix_support: WindowSupport
    windows: tuple[WindowSupport, ...]
    prefix_observed_on_grid: bool
    anchor: ResistanceAnchor | None
    metrics: ContextMetrics | None
    classification: Literal["A", "B", "other", "unclassified", "unknown"]
    phase: str
    reason: str


def _visible_minute(row: Minute | None, at: datetime, cutoff: datetime) -> ContextMinute:
    if row is None:
        return ContextMinute(at, None, None, None, "missing", "missing")
    if row.available_at > cutoff:
        # Do not inspect or validate a payload that is not visible yet.
        return ContextMinute(at, None, None, None, "late", "late")
    for state, payload in ((row.price_state, row.ohlc), (row.volume_state, row.shares)):
        _need(type(state) is str and state in STATES, "EI_invalid_observation_state")
        _need(state == "observed" or payload is None, "EI_unknown_observation_has_payload")
    prices = volume = None
    if row.price_state == "observed":
        raw = row.ohlc
        _need(type(raw) is tuple and len(raw) == 4
              and all(isinstance(value, Decimal) and value.is_finite() and value > 0 for value in raw),
              "EI_invalid_exact_price")
        op, high, low, close = raw
        _need(low <= min(op, close) <= max(op, close) <= high, "EI_invalid_ohlc_order")
        prices = tuple(map(Fraction, raw))
    if row.volume_state == "observed":
        _need(isinstance(row.shares, Decimal) and row.shares.is_finite() and row.shares >= 0,
              "EI_invalid_exact_volume")
        volume = Fraction(row.shares)
    return ContextMinute(at, row.available_at, prices, volume, row.price_state, row.volume_state)


def _anchor(bars: tuple[OHLC, ...], opening: datetime) -> tuple[ResistanceAnchor | None, int | None, str]:
    """Freeze the earliest qualifying peak, never a future global maximum.

    A peak has left.high < peak.high >= right.high and at least three prior
    five-minute bars. R is the median individual high-low range of the three
    immediately preceding bars, excluding the peak. The advance from the
    session's first OPEN is >= 2R. The right bar confirms the peak;
    no center-bar high/low order or execution is inferred.
    """
    zero_range = False
    for index in range(3, len(bars) - 1):
        peak = bars[index][1]
        if not bars[index - 1][1] < peak >= bars[index + 1][1]:
            continue
        before = bars[index - 3:index]
        width = sorted(bar[1] - bar[2] for bar in before)[1]
        if width == 0:
            zero_range = True
            continue
        advance = peak - bars[0][0]
        if advance < 2 * width:
            continue
        return ResistanceAnchor(opening, bars[0][0], opening + (index + 1) * 5 * MINUTE,
                                opening + (max(30, (index + 2) * 5) + 2) * MINUTE,
                                peak, width, advance, width / 4), index, ""
    return None, None, "zero_reference_range" if zero_range else "no_qualifying_confirmed_peak"


def _situation(bars: tuple[OHLC, ...], opening: datetime, anchor: ResistanceAnchor, peak_index: int
               ) -> tuple[ContextMetrics, str, str, str]:
    lower, upper = anchor.price - anchor.band, anchor.price + anchor.band
    pullback = resumption = invalidated = None
    deepest = anchor.price
    episodes = 0
    armed = False
    last_overlap = False
    for index in range(peak_index + 1, len(bars)):
        op, high, low, close = bars[index]
        deepest = min(deepest, low)
        clock = opening + (max(30, (index + 1) * 5) + 2) * MINUTE
        if invalidated is None and close <= anchor.start_price:
            invalidated = clock
        if pullback is None and close < bars[index - 1][3]:
            pullback = clock
        elif pullback is not None and resumption is None and close > bars[index - 1][1]:
            resumption = clock
        overlap = low <= upper and high >= lower
        # The confirming right bar is not a retest of a previously known peak.
        # A wholly below-zone bar arms only a later bar. Adjacent overlapping
        # bars are one episode; no within-bar exit/reentry order is invented.
        if index > peak_index + 1 and armed and overlap:
            episodes += 1
            armed = False
        if high < lower:
            armed = True
        last_overlap = overlap
    duration = len(bars) - peak_index - 1
    depth = (anchor.price - deepest) / anchor.advance
    metrics = ContextMetrics(anchor.advance / anchor.pre_peak_range, depth, duration,
                             pullback, resumption, episodes, last_overlap, invalidated)
    now = opening + (len(bars) * 5 + 2) * MINUTE
    if invalidated is not None:
        return metrics, "other", "other", "anchor_invalidated"
    if last_overlap and anchor.advance >= 3 * anchor.pre_peak_range and episodes >= 2:
        return metrics, "B", "extended_resistance_retest", "extended_repeated_retest"
    if pullback is not None and 0 < depth <= Fraction(1, 2) and duration <= 6 and episodes <= 1:
        if resumption == now:
            return metrics, "A", "first_resumption", "first_shallow_attempt"
        if resumption is None:
            return metrics, "A", "first_pullback", "first_shallow_attempt"
    reason = "first_attempt_completed" if resumption is not None else "outside_A_B_definition"
    return metrics, "other", "other", reason


def _end_offset(session: SessionInputs, decision_at: datetime) -> int:
    _session(session)
    _clock(decision_at)
    end = decision_at - 2 * MINUTE
    elapsed = (end - session.opening) // MINUTE
    _need(elapsed >= 30 and elapsed % 5 == 0 and decision_at < session.closing
          and decision_at == session.opening + (elapsed + 2) * MINUTE,
          "EI_requires_E30_plus_5m_grid_E_plus_2_inside_session")
    return elapsed


def _frame(session: SessionInputs, decision_at: datetime, rows: tuple[ContextMinute, ...],
           prefix: WindowSupport, windows: tuple[WindowSupport, ...], on_grid: bool,
           bars: tuple[OHLC | None, ...]) -> ContextFrame:
    anchor = metrics = None
    classification, phase = "unclassified", "unclassified"
    if not prefix.price_complete:
        classification, phase = "unknown", "unknown"
        reason = "opening_price_prefix_incomplete"
    elif not on_grid:
        classification, phase = "unknown", "unknown"
        reason = "prefix_not_observed_on_grid"
    else:
        anchor, peak_index, reason = _anchor(bars, session.opening)
        if anchor is not None:
            metrics, classification, phase, reason = _situation(bars, session.opening, anchor, peak_index)
    return ContextFrame(session.symbol, session.opening, decision_at - 2 * MINUTE, decision_at, rows, prefix, windows,
                        on_grid, anchor, metrics, classification, phase, reason)


class _PrefixCache:
    """Read each source key once and validate each visible payload only once."""

    def __init__(self, session: SessionInputs):
        self.session = session
        self.rows: list[ContextMinute] = []
        self.delayed: dict[int, Minute] = {}
        self.price_counts = [Counter()]
        self.volume_counts = [Counter()]
        self.volume_totals = [Fraction()]
        self.zeros = [0]
        self.on_grid = [True]
        self.bars: list[OHLC | None] = []

    def _extend_summaries(self, start: int) -> None:
        # A late row can change current support, but never its original deadline.
        for values in (self.price_counts, self.volume_counts, self.volume_totals, self.zeros, self.on_grid):
            del values[start + 1:]
        for index in range(start, len(self.rows)):
            row = self.rows[index]
            for values, state in ((self.price_counts, row.price_state), (self.volume_counts, row.volume_state)):
                counts = values[-1].copy()
                counts[state] += 1
                values.append(counts)
            self.volume_totals.append(self.volume_totals[-1] + (row.volume if row.volume is not None else 0))
            self.zeros.append(self.zeros[-1] + (row.volume == 0))
            deadline = self.session.opening + (max(30, (index // 5 + 1) * 5) + 2) * MINUTE
            self.on_grid.append(self.on_grid[-1] and row.prices is not None and row.available_at <= deadline)
        bar_start = start // 5
        del self.bars[bar_start:]
        for index in range(bar_start * 5, len(self.rows), 5):
            prices = tuple(row.prices for row in self.rows[index:index + 5])
            self.bars.append(None if None in prices else (
                prices[0][0], max(row[1] for row in prices), min(row[2] for row in prices), prices[-1][3]))

    def _support(self, size: int) -> WindowSupport:
        end = len(self.rows)
        start = max(0, end - size)
        outside = max(0, size - end)
        prices = self.price_counts[end] - self.price_counts[start]
        volumes = self.volume_counts[end] - self.volume_counts[start]
        if outside:
            prices["outside_session"] = volumes["outside_session"] = outside
        total = self.volume_totals[end] - self.volume_totals[start] if volumes.get("observed") == size else None
        return WindowSupport(size, end - start, tuple(sorted(prices.items())), tuple(sorted(volumes.items())),
                             self.zeros[end] - self.zeros[start], total)

    def frame(self, decision_at: datetime) -> ContextFrame:
        end = _end_offset(self.session, decision_at)
        _need(end >= len(self.rows), "EI_scan_clock_moved_backwards")
        changed_from = len(self.rows)
        for index, row in tuple(self.delayed.items()):
            if row.available_at <= decision_at:
                self.rows[index] = _visible_minute(row, row.start_at, decision_at)
                del self.delayed[index]
                changed_from = min(changed_from, index)
        for index in range(len(self.rows), end):
            at = self.session.opening + index * MINUTE
            row = self.session.minutes.get(at)
            if row is not None:
                _need(isinstance(row, Minute) and row.symbol == self.session.symbol and row.start_at == at,
                      "EI_minute_identity_differs")
                _clock(row.available_at)
                _need(row.available_at >= at + 3 * MINUTE, "EI_source_before_availability_floor")
                if row.available_at > decision_at:
                    self.delayed[index] = row
            self.rows.append(_visible_minute(row, at, decision_at))
        self._extend_summaries(changed_from)
        return _frame(self.session, decision_at, tuple(self.rows), self._support(end),
                      tuple(self._support(size) for size in WINDOWS), self.on_grid[-1], tuple(self.bars))


def context_frame(session: SessionInputs, *, decision_at: datetime) -> ContextFrame:
    """Describe one E>=30, five-minute endpoint at E+2 within the cash session.

    Current window support can recover when old rows arrive. A missing earlier
    decision prefix cannot be repaired into a claim about the first attempt:
    every first-30-minute row must have been available at E32, and later bars
    at their own completion+2 deadline. Volume never admits/excludes price cases.
    """
    return _PrefixCache(session).frame(decision_at)


def scan_context_frames(session: SessionInputs, *, through: datetime) -> Iterator[ContextFrame]:
    """Yield every eligible clock while reading each original minute at most once.

    Only already requested delayed rows are revisited when their declared
    availability arrives. No source day suffix is preloaded for the scan.
    """
    _session(session)
    _clock(through)
    _need(through >= session.opening, "EI_scan_cutoff_before_opening")
    prefix = _PrefixCache(session)
    clock = session.opening + 32 * MINUTE
    while clock <= through and clock < session.closing:
        yield prefix.frame(clock)
        clock += 5 * MINUTE
