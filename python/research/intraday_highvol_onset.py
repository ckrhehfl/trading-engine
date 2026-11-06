"""Evaluate the already registered A expansion at the original high clock."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
import re

from research.intraday_campaign_ml_labels import MLCalendar, fixed_calendar
from research.intraday_chart_evaluation import COST_BPS, MIN_INTERVAL_SESSIONS
from research.intraday_chart_folds import Key
from research.intraday_ed_inputs import FIVE, MINUTE, _require
from research.intraday_highvol_events import OFFSETS
from research.intraday_highvol_policy import _Clock, _Outcome, _clock, _contrast, _label, _summarize
from research.intraday_highvol_snapshot import TIMINGS, _fraction
from research.intraday_pattern_calibration import session_close, session_open


SCHEMA = "EG-onset-expansion-comparison-v1"
CLAIM = "fixed-clock stored OPEN diagnostics on observed high clusters; overlapping intents are not account trades"
EXPANSION, OTHER = "eligible_expansion", "eligible_other"
ELIGIBLE = (EXPANSION, OTHER)


@dataclass(frozen=True)
class _Onset:
    key: Key
    timings: tuple[str, ...]
    immediate: _Clock
    left_censored: bool
    support: str


@dataclass(frozen=True)
class OnsetPlan:
    calendar: MLCalendar
    events: tuple[_Onset, ...]


@dataclass(frozen=True)
class _Matched:
    event: _Onset
    immediate: _Outcome

    @property
    def scenario(self) -> _Outcome:
        # The early rule and the unconditional control use the same quotes.
        return self.immediate


def _initial_support(frame, current, high_end):
    bars = frame["attempt"]["observed_bars"][:3]
    prices = []
    for index, bar in enumerate(bars):
        end = high_end - (2 - index) * FIVE
        expected = (end - FIVE, end, end + 2 * MINUTE)
        _require(tuple(bar[name] for name in ("start_at", "end_at", "available_at")) ==
                 tuple(value.isoformat() for value in expected), "eg_onset_initial_bar_clock_changed")
        op, high, low, close = values = tuple(_fraction(value) for value in bar["ohlc"])
        _require(0 < low <= min(op, close) <= max(op, close) <= high, "eg_onset_initial_bar_prices_changed")
        prices.append(values)
    if len(prices) < 3:
        return "initial_context_unavailable"
    signed = prices[2][3] / prices[0][0] - 1
    _require(signed == _fraction(current["signed_move"]), "eg_onset_signed_move_changed")
    return EXPANSION if signed > 0 and prices[2][3] > max(prices[0][1], prices[1][1]) else OTHER


def prepare_onset(source_dates: Sequence[str], features: Iterable[dict]) -> OnsetPlan:
    """Freeze the prospective clock/support/membership before opening outcomes."""
    calendar = fixed_calendar(source_dates)
    source, test = set(calendar.source_reference_sessions), set(calendar.evaluation_reference_sessions)
    events, previous = [], None
    for frame in features:
        day, symbol, offset = key = tuple(frame["key"])
        _require(day in source and type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
                 and type(offset) is int and offset in OFFSETS, "invalid_eg_onset_key")
        order = day, offset, symbol
        _require(previous is None or order > previous, "duplicate_or_unordered_eg_onset_key")
        previous = order
        if day not in test:
            continue
        timings, high = frame["timings"], frame["first_high"]
        censored, current = high["left_censored"], high["current"]
        _require(type(timings) is list and bool(timings) and timings == [item for item in TIMINGS if item in timings]
                 and type(censored) is bool and (offset != 15 or censored), "eg_onset_membership_or_censor_changed")
        opening = session_open(day)
        high_end = opening + offset * MINUTE
        decision = high_end + 2 * MINUTE
        price_available = datetime.fromisoformat(current["price_available_at"])
        _require((current["opening"], current["symbol"], current["offset"], current["decision_at"]) ==
                 (opening.isoformat(), symbol, offset, decision.isoformat()) and current["price_status"] == "available"
                 and price_available.utcoffset() == opening.utcoffset() and high_end <= price_available <= decision
                 and _fraction(current["energy"]) > _fraction(high["threshold"]) >= 0,
                 "eg_onset_first_high_changed")
        clock = _clock(decision, session_close(day))
        saved = frame["immediate"]
        _require((saved["decision_at"], saved["entry_at"], saved["exit_at"]) ==
                 tuple(value.isoformat() for value in (decision, clock.entry, clock.exit))
                 and saved["status"] == ("supported" if clock.supported else "outside_clock"),
                 "eg_onset_immediate_clock_changed")
        support = _initial_support(frame, current, high_end) if clock.supported else "outside_clock"
        events.append(_Onset(key, tuple(timings), clock, censored, support))
    return OnsetPlan(calendar, tuple(events))


def prospective_keys(plan: OnsetPlan) -> tuple[Key, ...]:
    return tuple(event.key for event in plan.events if event.support in ELIGIBLE)


def _outcome(event, labels_at):
    frame = labels_at(event.key)
    _require(type(frame) is dict and tuple(frame["key"]) == event.key, "eg_onset_outcome_key_changed")
    saved = frame["immediate"]
    outcome = _label(saved, event.immediate, immediate=True)
    entry_open, exit_open = (None if saved[name] is None else _fraction(saved[name])
                             for name in ("entry_open", "exit_open"))
    _require(all(quote is None or quote > 0 for quote in (entry_open, exit_open))
             and (outcome.gross is not None) == (entry_open is not None and exit_open is not None),
             "eg_onset_outcome_quotes_changed")
    mature = saved["mature_at"]
    _require((mature is None) == (outcome.gross is None), "eg_onset_outcome_maturity_missing")
    if mature is not None:
        at = datetime.fromisoformat(mature)
        _require(at.utcoffset() == event.immediate.exit.utcoffset() and at.isoformat() == mature
                 and event.immediate.exit + 3 * MINUTE <= at <= session_close(event.key[0])
                 and outcome.gross == exit_open / entry_open - 1, "eg_onset_outcome_maturity_or_return_changed")
    return _Matched(event, outcome)


def evaluate_onset(plan: OnsetPlan, *, labels_at: Callable[[Key], dict]) -> dict:
    """Compare early A against all highs on the same eligible original rows."""
    calendar = plan.calendar
    sessions, weeks = calendar.evaluation_reference_sessions, calendar.evaluation_week_session_counts
    rows = [_outcome(event, labels_at) for event in plan.events if event.support in ELIGIBLE]
    timings = {}
    for timing in TIMINGS:
        population = [event for event in plan.events if timing in event.timings]
        first_hour = [event for event in population if event.key[2] <= 60]
        common = [row for row in rows if timing in row.event.timings]
        early = ["selected" if row.event.support == EXPANSION else "declined" for row in common]
        all_high = ["selected"] * len(common)
        costs = {str(cost): {"early_a": _summarize(common, early, sessions, weeks, cost, control=True),
                             "all_high": _summarize(common, all_high, sessions, weeks, cost, control=True),
                             "paired": _contrast(common, early, all_high, sessions, cost)} for cost in COST_BPS}
        net43 = costs["43"]["early_a"]["net_pp"]
        interval = net43["normal95_exploratory"]
        contributing = net43["contributing_sessions"]
        positive_lower = interval is not None and interval[0] > 0
        candidate = {"contributing_days": contributing, "net43_lower_positive": positive_lower,
                     "supported": contributing >= MIN_INTERVAL_SESSIONS and positive_lower}
        timings[timing] = {"observed_clusters": len(population),
                           "support_counts": dict(Counter(event.support for event in population)),
                           "first_hour_by_support": dict(Counter(event.support for event in first_hour)),
                           "first_hour_confirmed_by_support": dict(Counter(event.support for event in first_hour
                                                                          if not event.left_censored)),
                           "first_hour_left_censored_by_support": dict(Counter(event.support for event in first_hour
                                                                              if event.left_censored)),
                           "left_censored_by_support": dict(Counter(event.support for event in population if event.left_censored)),
                           "costs": costs, "candidate": candidate}
    return {"schema": SCHEMA, "reference_sessions": list(sessions),
            "evaluation_boundary_week_flags": {key: dict(value) for key, value in calendar.evaluation_boundary_week_flags.items()},
            "timings": timings, "candidate_net43_both_timings": all(report["candidate"]["supported"] for report in timings.values()),
            "claim": CLAIM}


def compare_onset_expansion(source_dates: Sequence[str], features: Iterable[dict], *, labels_at: Callable[[Key], dict]) -> dict:
    return evaluate_onset(prepare_onset(source_dates, features), labels_at=labels_at)
