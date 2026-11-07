"""Independent census reconstruction over an authenticated minute session.

The result types are shared with the producer. The reference reconstructs the
decision prefix, questions, and first touches directly from the minute rows.
"""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from research.intraday_ed_inputs import MINUTE, STATES, InputRefusal, Issue, Minute, SessionInputs
from research.intraday_pattern_events import Bar
from research.intraday_scenario_labels import PathQuestion, PathResult
from research.intraday_scenario_observations import OpeningSupportObservation, PathObservation
from research.intraday_scenario_questions import InitialPath
from research.intraday_scenario_state import OpeningRangeHighLevel, OpeningRangePriceEvidence, ScenarioEvent, ScenarioScan


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _price_row(session: SessionInputs, at: datetime, cutoff: datetime, *, label: bool = False):
    row = session.minutes.get(at)
    if row is None:
        return None, "minute_not_observed_at_cutoff" if label else "missing_minute"
    if not isinstance(row, Minute) or (row.symbol, row.start_at) != (session.symbol, at):
        raise InputRefusal("minute_identity_changed")
    if not _aware(row.available_at):
        raise InputRefusal("aware_clock_required")
    if row.available_at > cutoff:
        return None, "minute_not_observed_at_cutoff" if label else "minute_available_after_cutoff"
    if row.available_at < at + MINUTE:
        raise InputRefusal("minute_available_before_completion" if label else "minute_unavailable_at_decision")
    if type(row.price_state) is not str or row.price_state not in STATES:
        raise InputRefusal("invalid_observation_state")
    if row.price_state != "observed":
        if row.ohlc is not None:
            raise InputRefusal("unknown_observation_has_payload")
        return None, f"price_{row.price_state}"
    if type(row.ohlc) is not tuple or len(row.ohlc) != 4:
        raise InputRefusal("observed_price_requires_ohlc")
    if any(not isinstance(price, Decimal) or (label and type(price) is not Decimal)
           or not price.is_finite() or price <= 0 for price in row.ohlc):
        raise InputRefusal("invalid_exact_price")
    first, high, low, last = row.ohlc
    if not (low <= first <= high and low <= last <= high):
        raise InputRefusal("invalid_ohlc_order")
    return row, None


def _prefix_rows(session: SessionInputs, offsets: range, cutoff: datetime):
    rows, issues = [], []
    for offset in offsets:
        at = session.opening + offset * MINUTE
        row, reason = _price_row(session, at, cutoff)
        if reason is None:
            rows.append(row)
        else:
            issues.append(Issue(session.symbol, at, reason))
    return rows, tuple(issues)


def _aggregate(rows: list[Minute]) -> Bar:
    start = rows[0].start_at
    end = start + 5 * MINUTE
    return Bar(start, end, max(end + 2 * MINUTE, max(row.available_at for row in rows)),
               rows[0].ohlc[0], max(row.ohlc[1] for row in rows), min(row.ohlc[2] for row in rows),
               rows[-1].ohlc[3])


def _first_event(session: SessionInputs, level: OpeningRangeHighLevel, bars: list[Bar], length: int):
    event = None
    for end_offset in range(20, length - 2, 5):
        at = session.opening + (end_offset + 2) * MINUTE
        if event is not None and at >= min(event.origin.available_at + 60 * MINUTE, session.closing - MINUTE):
            break
        rows, issues = _prefix_rows(session, range(end_offset - 5, end_offset), at)
        if issues:
            return ScenarioScan("support_unknown", event, issues)
        preceding = tuple(bars[-3:])
        bar = _aggregate(rows)
        bars.append(bar)
        if event is None:
            width = (max(Fraction(prior.high) for prior in preceding)
                     - min(Fraction(prior.low) for prior in preceding))
            if not width:
                return ScenarioScan("support_unknown", issues=(
                    Issue(session.symbol, bar.start_at, "zero_preceding_range"),))
            band = width / 10
        else:
            band = event.band
        distance = Fraction(bar.close) - level.price
        if distance > band and Fraction(preceding[-1].close) - level.price <= band:
            if event is None:
                event = ScenarioEvent(level, band, preceding, bar, "breakout", bar)
            else:
                event = ScenarioEvent(level, band, event.width_context, event.origin, "prepared", bar)
            return ScenarioScan("event", event)
        if event is None and abs(distance) <= band:
            event = ScenarioEvent(level, band, preceding, bar, "prepared")
    return ScenarioScan("known_no_event") if event is None else ScenarioScan("event", event)


def _initial_paths(event: ScenarioEvent, bars: list[Bar]):
    level, band = event.level.price, event.band
    symbol = event.level.symbol
    if event.kind == "prepared":
        at, close = event.origin.available_at, Fraction(event.origin.close)
        stop = level - 2 * band
        if stop <= 0:
            yield InitialPath("approach", None, close, issues=(Issue(symbol, at, "invalid_prepared_risk_boundary"),))
        else:
            yield InitialPath("approach", PathQuestion(at, at + 30 * MINUTE, level, band,
                                                        level + 2 * band, stop), close, "prepared_band")
    if event.breakout is not None:
        bar = event.breakout
        at, close = bar.available_at, Fraction(bar.close)
        stop = min(Fraction(bar.low), level - band)
        if not 0 < stop < close:
            yield InitialPath("breakout", None, close, issues=(Issue(symbol, at, "invalid_breakout_risk_boundary"),))
            return
        obstacles = [Fraction(candidate.high) for index, candidate in enumerate(bars[1:-1], start=1)
                     if candidate.high > close and candidate.high > bars[index - 1].high
                     and candidate.high >= bars[index + 1].high]
        target = min(obstacles) if obstacles else 3 * close - 2 * stop
        basis = "known_intraday_swing_high" if obstacles else "twice_stop_distance"
        yield InitialPath("breakout", PathQuestion(at, at + 30 * MINUTE, level, band, target, stop), close, basis)


def _follow(session: SessionInputs, question: PathQuestion, initial_close: Fraction) -> PathResult:
    start, end = question.start_at, question.end_at
    lower, upper = question.level - question.band, question.level + question.band
    floor, ceiling = question.stop, question.target
    if not (0 < floor <= lower < upper < ceiling and floor < initial_close < ceiling):
        return PathResult(question, "unsupported", start, reason="unsupported_price_geometry")
    if not session.opening <= start < end <= session.closing:
        return PathResult(question, "unsupported", start, reason="outside_session")

    departed, retested, latest = initial_close > upper, False, start
    final_cutoff = session.closing + 2 * MINUTE
    for offset in range((end - start) // MINUTE):
        at = start + offset * MINUTE
        row, reason = _price_row(session, at, final_cutoff, label=True)
        if reason is not None:
            return PathResult(question, "unknown", at, reason=reason, departed=departed, retested=retested)
        first, high, low, last = row.ohlc
        observed_end = at + MINUTE
        latest = max(latest, row.available_at, observed_end + 2 * MINUTE)
        path, ambiguity = None, None
        if first <= floor:
            path = "P3"
        elif first >= ceiling:
            path = "P2" if retested else "P1"
        elif low <= floor:
            if high >= ceiling:
                ambiguity = "same_minute_target_stop_order_unknown"
            else:
                path = "P3"
        else:
            visits_band = departed and low <= upper and high >= lower
            if high >= ceiling and visits_band and not retested and not lower <= first <= upper:
                ambiguity = "same_minute_retest_target_order_unknown"
            else:
                retested |= visits_band
                if high >= ceiling:
                    path = "P2" if retested else "P1"
        if ambiguity is not None or path is not None:
            return PathResult(question, "unknown" if ambiguity else "resolved", observed_end, path, latest,
                              ambiguity, departed, retested)
        departed |= last > upper
    return PathResult(question, "resolved", end, "P4", latest, departed=departed, retested=retested)


def reference_opening_support(session: SessionInputs, evidence: OpeningRangePriceEvidence | None
                              ) -> OpeningSupportObservation:
    """Reconstruct an offline result without calling the production algorithms."""
    if not isinstance(session, SessionInputs):
        raise InputRefusal("typed_session_required")
    if not _aware(session.opening) or not _aware(session.closing):
        raise InputRefusal("aware_clock_required")
    if type(session.symbol) is not str or not session.symbol or not isinstance(session.minutes, Mapping):
        raise InputRefusal("invalid_session_identity")
    length, remainder = divmod(session.closing - session.opening, MINUTE)
    if length < 1 or remainder:
        raise InputRefusal("invalid_session_geometry")
    if length <= 22:
        return OpeningSupportObservation(ScenarioScan("not_yet_eligible"), ())
    registered_at = session.opening + 17 * MINUTE
    if evidence is None:
        return OpeningSupportObservation(ScenarioScan("support_unknown", issues=(
            Issue(session.symbol, registered_at, "same_session_price_evidence_missing"),)), ())
    if not isinstance(evidence, OpeningRangePriceEvidence) or any(
        type(field) is not str or not field.strip() for field in (evidence.receipt_id, evidence.price_unit)
    ):
        raise InputRefusal("invalid_opening_range_price_evidence")
    initial, issues = _prefix_rows(session, range(15), registered_at)
    if issues:
        return OpeningSupportObservation(ScenarioScan("support_unknown", issues=issues), ())

    bars = [_aggregate(initial[begin:begin + 5]) for begin in (0, 5, 10)]
    high = Fraction(max(bar.high for bar in bars))
    level = OpeningRangeHighLevel(session.symbol, session.opening, session.closing, registered_at, high, evidence)
    scan = _first_event(session, level, bars, length)
    if scan.event is None:
        return OpeningSupportObservation(scan, ())

    paths = []
    for initial_path in _initial_paths(scan.event, bars):
        question = initial_path.question
        labels = () if question is None else tuple(_follow(session, PathQuestion(
            question.start_at, question.start_at + horizon * MINUTE,
            question.level, question.band, question.target, question.stop,
        ), initial_path.start_close) for horizon in (5, 15, 30))
        paths.append(PathObservation(initial_path, labels))
    return OpeningSupportObservation(scan, tuple(paths))
