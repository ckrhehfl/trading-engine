"""Causal EH scenario state over caller-authenticated session observations.

The caller owns the original regular-session calendar and receipt authentication.
The previous-session high also requires a shared price basis. A separate
opening-range high only uses the current session's price coordinate.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_chart_inputs import _clock, _need, _session
from research.intraday_ed_inputs import FIVE, MINUTE, Issue, Minute, SessionInputs, window
from research.intraday_pattern_events import Bar


_PUBLICATION_LAG = 2 * MINUTE


@dataclass(frozen=True)
class PriceEvidence:
    receipt_id: str
    price_unit: str
    price_basis: str


@dataclass(frozen=True)
class ImportantLevel:
    symbol: str
    opening: datetime
    valid_until: datetime
    previous_opening: datetime
    previous_closing: datetime
    available_at: datetime
    price: Fraction
    previous_evidence: PriceEvidence
    current_evidence: PriceEvidence

    @property
    def level_id(self) -> str:
        return f"{self.symbol}:{self.opening.isoformat()}:previous_session_high"


@dataclass(frozen=True)
class PreviousHigh:
    level: ImportantLevel | None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class OpeningRangePriceEvidence:
    receipt_id: str
    price_unit: str


@dataclass(frozen=True)
class OpeningRangeHighLevel:
    symbol: str
    opening: datetime
    valid_until: datetime
    available_at: datetime
    price: Fraction
    current_evidence: OpeningRangePriceEvidence

    @property
    def level_id(self) -> str:
        return f"{self.symbol}:{self.opening.isoformat()}:opening_range_15m_high"


@dataclass(frozen=True)
class ScenarioEvent:
    level: ImportantLevel | OpeningRangeHighLevel
    band: Fraction
    width_context: tuple[Bar, ...]
    origin: Bar
    kind: Literal["prepared", "breakout", "left_censored_gap"]
    breakout: Bar | None = None
    censor_reason: Literal["opening_gap", "prior_close_above_at_first_eligible_grid"] | None = None

    @property
    def expires_at(self) -> datetime:
        return min(self.origin.available_at + 60 * MINUTE, self.level.valid_until - MINUTE)

    @property
    def prepared_bounds(self) -> tuple[Fraction, Fraction] | None:
        stop = self.level.price - 2 * self.band
        if self.kind != "prepared" or stop <= 0:
            return None
        return stop, self.level.price + 2 * self.band

    @property
    def breakout_stop(self) -> Fraction | None:
        if self.breakout is None:
            return None
        stop = min(Fraction(self.breakout.low), self.level.price - self.band)
        return stop if stop > 0 else None


@dataclass(frozen=True)
class ScenarioScan:
    status: Literal["not_yet_eligible", "known_no_event", "support_unknown", "event"]
    event: ScenarioEvent | None = None
    issues: tuple[Issue, ...] = ()


ReentryRoute = Literal["below_level", "above_level"]


@dataclass(frozen=True)
class ReentryEvidence:
    status: Literal["pending", "ready", "invalidated", "expired", "support_unknown"]
    route: ReentryRoute | None
    context: tuple[Bar, ...] = ()
    confirmation: Bar | None = None
    issues: tuple[Issue, ...] = ()


def _observed(
    session: SessionInputs, start: int, count: int, decision_at: datetime,
) -> tuple[tuple[Minute, ...], tuple[Issue, ...]]:
    """Separate unpublished prices from missing prices before checking OHLC."""
    visible = {}
    late = set()
    for offset in range(start, start + count):
        at = session.opening + offset * MINUTE
        row = session.minutes.get(at)
        if row is None:
            continue
        _need(isinstance(row, Minute) and row.symbol == session.symbol and row.start_at == at,
              "minute_identity_changed")
        _clock(row.available_at)
        if row.available_at > decision_at:
            late.add(at)
        else:
            visible[at] = row
    checked = window(SessionInputs(session.symbol, session.opening, session.closing, visible),
                     start, count, decision_at)
    issues = tuple(Issue(item.symbol, item.minute, "minute_available_after_cutoff")
                   if item.minute in late else item for item in checked.issues)
    return checked.minutes, issues


def previous_session_high(
    current: SessionInputs, previous: SessionInputs | None, *,
    expected_previous_opening: datetime | None, previous_evidence: PriceEvidence | None,
    current_evidence: PriceEvidence | None, decision_at: datetime,
) -> PreviousHigh:
    """Register a level only from a fully observed expected previous session."""
    _session(current)
    _clock(decision_at)
    if expected_previous_opening is None:
        _need(previous is None, "previous_session_requires_expected_opening")
        return PreviousHigh(None, (Issue(current.symbol, current.opening, "previous_calendar_unknown"),))
    _clock(expected_previous_opening)
    _need(expected_previous_opening.date() < current.opening.date(), "expected_previous_day_must_be_earlier")
    if previous is None:
        return PreviousHigh(None, (Issue(current.symbol, expected_previous_opening, "previous_session_missing"),))
    _session(previous)
    _need(previous.symbol == current.symbol and previous.opening == expected_previous_opening
          and previous.closing < current.opening, "previous_session_identity_or_geometry_differs")
    for evidence in (previous_evidence, current_evidence):
        if evidence is not None:
            _need(isinstance(evidence, PriceEvidence)
                  and all(type(item) is str and bool(item.strip()) for item in
                          (evidence.receipt_id, evidence.price_unit, evidence.price_basis)),
                  "invalid_price_evidence")
    if (previous_evidence is None or current_evidence is None
            or previous_evidence.price_unit != current_evidence.price_unit
            or previous_evidence.price_basis != current_evidence.price_basis):
        return PreviousHigh(None, (Issue(current.symbol, expected_previous_opening, "same_price_basis_unsupported"),))
    if decision_at < previous.closing:
        return PreviousHigh(None, (Issue(current.symbol, previous.closing, "previous_session_not_completed"),))
    count = (previous.closing - previous.opening) // MINUTE
    rows, issues = _observed(previous, 0, count, decision_at)
    if issues:
        return PreviousHigh(None, issues)
    return PreviousHigh(ImportantLevel(
        current.symbol, current.opening, current.closing, previous.opening, previous.closing,
        max(row.available_at for row in rows), max(Fraction(row.ohlc[1]) for row in rows),
        previous_evidence, current_evidence,
    ))


def _bar(session: SessionInputs, start: int, decision_at: datetime) -> tuple[Bar | None, tuple[Issue, ...]]:
    rows, issues = _observed(session, start, 5, decision_at)
    if issues:
        return None, issues
    prices = tuple(row.ohlc for row in rows)
    end = rows[-1].start_at + MINUTE
    available = max(end + _PUBLICATION_LAG, *(row.available_at for row in rows))
    return Bar(rows[0].start_at, end, available,
               prices[0][0], max(row[1] for row in prices), min(row[2] for row in prices),
               prices[-1][3]), ()


def _level_session(session: SessionInputs, level: ImportantLevel | OpeningRangeHighLevel) -> None:
    _need(isinstance(level, (ImportantLevel, OpeningRangeHighLevel)), "typed_important_level_required")
    _need((level.symbol, level.opening, level.valid_until) ==
          (session.symbol, session.opening, session.closing), "level_session_identity_changed")
    _clock(level.available_at)
    _need(isinstance(level.price, Fraction) and level.price > 0, "invalid_important_level_price")


def _first_grid(session: SessionInputs, through: datetime) -> datetime | None:
    _session(session)
    _clock(through)
    _need(through >= session.opening, "scenario_cutoff_before_opening")
    decision_at = session.opening + 4 * FIVE + _PUBLICATION_LAG
    return decision_at if decision_at <= through and decision_at < session.closing else None


def scan_previous_high(session: SessionInputs, level: PreviousHigh, *, through: datetime) -> ScenarioScan:
    """Find the first eligible candidate and any subsequent first breakout.

    Each five-minute endpoint is evaluated at E+2, from E20 onward. A missing
    eligible prefix prevents identifying the first candidate or crossing.
    A level known only after the first grid cannot backfill event identity.
    Opening-gap evidence is classified only at the first grid with a known
    band, using the session open and preceding close without inferring a cross.
    """
    first_decision = _first_grid(session, through)
    if first_decision is None:
        return ScenarioScan("not_yet_eligible")
    if level.level is None:
        return ScenarioScan("support_unknown", issues=level.issues)
    _need(isinstance(level.level, ImportantLevel), "typed_important_level_required")
    return _scan_level(session, level.level, first_decision, through)


def scan_opening_range_high(
    session: SessionInputs, *, current_evidence: OpeningRangePriceEvidence | None, through: datetime,
) -> ScenarioScan:
    """Freeze the first 15 minutes' high at E17 and scan from the E22 decision."""
    first_decision = _first_grid(session, through)
    if first_decision is None:
        return ScenarioScan("not_yet_eligible")
    registered_at = session.opening + 3 * FIVE + _PUBLICATION_LAG
    if current_evidence is None:
        return ScenarioScan("support_unknown", issues=(
            Issue(session.symbol, registered_at, "same_session_price_evidence_missing"),))
    _need(isinstance(current_evidence, OpeningRangePriceEvidence)
          and all(type(item) is str and bool(item.strip()) for item in
                  (current_evidence.receipt_id, current_evidence.price_unit)), "invalid_opening_range_price_evidence")
    rows, issues = _observed(session, 0, 15, registered_at)
    if issues:
        return ScenarioScan("support_unknown", issues=issues)
    key = OpeningRangeHighLevel(session.symbol, session.opening, session.closing, registered_at,
                                max(Fraction(row.ohlc[1]) for row in rows), current_evidence)
    return _scan_level(session, key, first_decision, through)


def _scan_level(
    session: SessionInputs, key: ImportantLevel | OpeningRangeHighLevel, first_decision: datetime, through: datetime,
) -> ScenarioScan:
    _level_session(session, key)
    if key.available_at > first_decision:
        return ScenarioScan("support_unknown", issues=(
            Issue(session.symbol, first_decision, "level_unavailable_at_first_grid"),))

    history = []
    for start in (0, 5, 10):
        bar, issues = _bar(session, start, first_decision)
        if issues:
            return ScenarioScan("support_unknown", issues=issues)
        history.append(bar)

    event = None
    offset = 15
    while (decision_at := session.opening + offset * MINUTE + FIVE + _PUBLICATION_LAG) <= through:
        if decision_at >= session.closing or (event is not None and decision_at >= event.expires_at):
            break
        bar, issues = _bar(session, offset, decision_at)
        if issues:
            return ScenarioScan("support_unknown", event, issues)
        if event is None:
            width = Fraction(max(item.high for item in history)) - Fraction(min(item.low for item in history))
            if width == 0:
                return ScenarioScan("support_unknown", issues=(
                    Issue(session.symbol, bar.start_at, "zero_preceding_range"),))
            band = width / 10
        else:
            band = event.band
        lower, upper = key.price - band, key.price + band
        prior_close = history[-1].close
        if isinstance(key, ImportantLevel) and event is None and offset == 15:
            if history[0].open > upper or prior_close > upper:
                reason = "opening_gap" if history[0].open > upper else "prior_close_above_at_first_eligible_grid"
                return ScenarioScan("event", ScenarioEvent(
                    key, band, tuple(history), bar, "left_censored_gap", censor_reason=reason))
        if prior_close <= upper < bar.close:
            if event is None:
                event = ScenarioEvent(key, band, tuple(history), bar, "breakout", breakout=bar)
            else:
                event = replace(event, breakout=bar)
            return ScenarioScan("event", event)
        if event is None and lower <= bar.close <= upper:
            event = ScenarioEvent(key, band, tuple(history), bar, "prepared")
        history = [*history[1:], bar]
        offset += 5
    return ScenarioScan("event", event) if event is not None else ScenarioScan("known_no_event")


def reentry_evidence(
    session: SessionInputs, event: ScenarioEvent, *, exit_confirmed_at: datetime,
    route: ReentryRoute | None, through: datetime,
) -> ReentryEvidence:
    """Look for new evidence after a confirmed exit and within the same event.

    The caller attests whether price fell below T or remained above T through
    exit. A full fresh bar must begin at or after exit, and readiness requires
    at least five minutes remaining after the next modeled open. The result records
    the first confirmation rather than a new confirmation at each later cutoff.
    """
    _session(session)
    _level_session(session, event.level)
    _clock(exit_confirmed_at)
    _clock(through)
    _need(event.origin.available_at <= exit_confirmed_at <= through, "invalid_reentry_exit_clock")
    _need(route in (None, "below_level", "above_level"), "invalid_reentry_route")
    last_decision = event.expires_at - FIVE - MINUTE
    if through > last_decision:
        return ReentryEvidence("expired", route)
    if route is None:
        return ReentryEvidence("support_unknown", route, issues=(
            Issue(session.symbol, exit_confirmed_at, "exit_path_unknown"),))
    minutes_since_opening = (exit_confirmed_at - session.opening) // MINUTE
    offset = (minutes_since_opening + 4) // 5 * 5
    context = []
    previous = None
    level, band = event.level.price, event.band
    while (decision_at := session.opening + offset * MINUTE + FIVE + _PUBLICATION_LAG) <= through:
        bar, issues = _bar(session, offset, decision_at)
        if issues:
            return ReentryEvidence("support_unknown", route, tuple(context), issues=issues)
        if route == "above_level":
            if bar.low <= level:
                return ReentryEvidence("invalidated", route, tuple(context), bar)
            if len(context) < 2:
                context.append(bar)
            elif bar.close > max(item.high for item in context):
                return ReentryEvidence("ready", route, tuple(context), bar)
        elif not context:
            if bar.close > level + band:
                context.append(bar)
        elif bar.close <= level - band:
            return ReentryEvidence("invalidated", route, tuple(context), bar)
        elif len(context) == 1 and bar.close < previous.close:
            if bar.low <= level - band:
                return ReentryEvidence("invalidated", route, tuple(context), bar)
            context.append(bar)
        elif len(context) == 2 and bar.close > context[-1].high:
            return ReentryEvidence("ready", route, tuple(context), bar)
        previous = bar
        offset += 5
    return ReentryEvidence("pending", route, tuple(context))
