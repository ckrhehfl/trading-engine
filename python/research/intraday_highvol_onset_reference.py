"""Independent scalar verification of the registered EG onset expansion."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from re import fullmatch

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_chart_evaluation import MIN_INTERVAL_SESSIONS
from research.intraday_ed_inputs import _require
from research.intraday_highvol_evaluation_reference import _key_evidence, _policy_line, _statistics
from research.intraday_highvol_events import OFFSETS
from research.intraday_pattern_calibration import session_close, session_open


TIMINGS = ("S1", "S2")
COSTS = (33, 43)
SUPPORTED = ("eligible_expansion", "eligible_other")
CLAIM = "fixed-clock stored OPEN diagnostics on observed high clusters; overlapping intents are not account trades"
_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class _Event:
    key: tuple[str, str, int]
    timings: tuple[str, ...]
    left_censored: bool
    status: str
    entry: datetime
    exit: datetime


def _ratio(value):
    _require(type(value) is dict and set(value) == {"fraction"}, "eg_onset_reference_fraction_type")
    parts = value["fraction"]
    _require(type(parts) is list and len(parts) == 2 and all(type(item) is int for item in parts)
             and parts[1] > 0, "eg_onset_reference_fraction_parts")
    return Fraction(*parts)


def _initial_direction(frame, end, decision):
    prefix = frame["attempt"]["observed_bars"][:3]
    prices = []
    for index, bar in enumerate(prefix):
        expected_end = end - (10 - 5 * index) * _MINUTE
        expected = (expected_end - 5 * _MINUTE, expected_end, expected_end + 2 * _MINUTE)
        _require(tuple(bar[field] for field in ("start_at", "end_at", "available_at")) ==
                 tuple(value.isoformat() for value in expected) and expected[-1] <= decision,
                 "eg_onset_reference_bar_clock")
        opening, high, low, close = map(_ratio, bar["ohlc"])
        _require(0 < low <= min(opening, close) <= max(opening, close) <= high,
                 "eg_onset_reference_ohlc")
        prices.append((opening, high, low, close))
    if len(prices) < 3:
        return "initial_context_unavailable"
    change = prices[2][3] / prices[0][0] - 1
    _require(change == _ratio(frame["first_high"]["current"]["signed_move"]), "eg_onset_reference_signed_move")
    expanded = change > 0 and prices[2][3] > max(prices[0][1], prices[1][1])
    return "eligible_expansion" if expanded else "eligible_other"


def _read_events(inputs, calendar, checkpoint):
    known_dates = set(calendar.source_reference_sessions)
    test_dates = set(calendar.evaluation_reference_sessions)
    previous = None
    for frame in inputs.features():
        checkpoint("eg_onset_reference_feature")
        day, symbol, offset = key = tuple(frame["key"])
        order = day, offset, symbol
        _require(day in known_dates and type(symbol) is str and fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
                 and type(offset) is int and offset in OFFSETS and (previous is None or previous < order),
                 "eg_onset_reference_feature_identity")
        previous = order
        if day not in test_dates:
            continue
        timings = tuple(frame["timings"])
        _require(type(frame["timings"]) is list and timings and
                 timings == tuple(timing for timing in TIMINGS if timing in timings),
                 "eg_onset_reference_timing_membership")
        first, saved = frame["first_high"], frame["immediate"]
        left = first["left_censored"]
        _require(type(left) is bool and (offset != 15 or left), "eg_onset_reference_left_censor")
        opening = session_open(day)
        end = opening + offset * _MINUTE
        decision = end + 2 * _MINUTE
        current = first["current"]
        available = datetime.fromisoformat(current["price_available_at"])
        _require((current["opening"], current["symbol"], current["offset"], current["decision_at"]) ==
                 (opening.isoformat(), symbol, offset, decision.isoformat())
                 and current["price_status"] == "available" and available.utcoffset() == opening.utcoffset()
                 and end <= available <= decision and _ratio(current["energy"]) > _ratio(first["threshold"]) >= 0,
                 "eg_onset_reference_high_evidence")
        entry, exit_at = decision + _MINUTE, decision + 31 * _MINUTE
        can_enter = exit_at < session_close(day) - 15 * _MINUTE
        expected_status = "supported" if can_enter else "outside_clock"
        _require(saved["status"] == expected_status and tuple(saved[name] for name in
                 ("decision_at", "entry_at", "exit_at")) == (decision.isoformat(), entry.isoformat(), exit_at.isoformat()),
                 "eg_onset_reference_immediate_clock")
        status = _initial_direction(frame, end, decision) if can_enter else "outside_clock"
        yield _Event(key, timings, left, status, entry, exit_at)


def _label(event, frame):
    _require(tuple(frame["key"]) == event.key, "eg_onset_reference_label_identity")
    label = frame["immediate"]
    _require((label["entry_at"], label["exit_at"]) == (event.entry.isoformat(), event.exit.isoformat()),
             "eg_onset_reference_label_clock")
    prices = [None if label[field] is None else _ratio(label[field]) for field in ("entry_open", "exit_open")]
    _require(all(value is None or value > 0 for value in prices), "eg_onset_reference_label_price")
    entry, exit_at = prices
    if entry is None or exit_at is None:
        _require(label["status"] == "unavailable" and label["gross"] is None and label["mature_at"] is None,
                 "eg_onset_reference_unknown_label")
        return ("entry_unavailable" if entry is None else "fixed_exit_unavailable"), None
    gross = exit_at / entry - 1
    _require(label["status"] == "available" and label["gross"] is not None and gross == _ratio(label["gross"]),
             "eg_onset_reference_gross")
    mature = datetime.fromisoformat(label["mature_at"])
    _require(mature.isoformat() == label["mature_at"] and mature.utcoffset() == event.exit.utcoffset()
             and event.exit + 3 * _MINUTE <= mature <= session_close(event.key[0]),
             "eg_onset_reference_label_maturity")
    return "available", gross


def _paired(report, rows, fee, sessions):
    known = [(key[0], decision, gross) for key, decision, _, gross in rows if gross is not None]
    _require(report["common_opportunities"] == len(rows) and report["known_matched_opportunities"] == len(known)
             and report["unknown_matched_opportunities"] == len(rows) - len(known),
             "eg_onset_reference_pair_counts")
    for name, cost in (("gross_pp", Fraction()), ("net_pp", Fraction(fee, 10_000))):
        outcomes = [(day, 100 * (gross - cost), decision) for day, decision, gross in known]
        _statistics(report[name]["left"], [(day, pnl if decision == "selected" else 0)
                                            for day, pnl, decision in outcomes], sessions)
        _statistics(report[name]["right"], [(day, pnl) for day, pnl, _ in outcomes], sessions)
        _statistics(report[name]["left_minus_right"], [(day, 0 if decision == "selected" else -pnl)
                                                       for day, pnl, decision in outcomes], sessions)


def _verify_timing(saved, events, labels, calendar, checkpoint):
    first_hour = [event for event in events if event.key[2] <= 60]
    _require(saved["observed_clusters"] == len(events)
             and saved["support_counts"] == dict(Counter(event.status for event in events))
             and saved["first_hour_by_support"] == dict(Counter(event.status for event in first_hour))
             and saved["first_hour_confirmed_by_support"] ==
                 dict(Counter(event.status for event in first_hour if not event.left_censored))
             and saved["first_hour_left_censored_by_support"] ==
                 dict(Counter(event.status for event in first_hour if event.left_censored))
             and saved["left_censored_by_support"] == dict(Counter(event.status for event in events if event.left_censored))
             and set(saved["costs"]) == {str(cost) for cost in COSTS}, "eg_onset_reference_population")
    eligible = [event for event in events if event.key in labels]
    clocks = {event.key: (event.entry, event.exit) for event in eligible}
    early = [(event.key, "selected" if event.status == "eligible_expansion" else "declined", *labels[event.key])
             for event in eligible]
    baseline = [(event.key, "selected", *labels[event.key]) for event in eligible]
    days, weeks = calendar.evaluation_reference_sessions, calendar.evaluation_week_session_counts
    for cost in COSTS:
        checkpoint("eg_onset_reference_comparison")
        comparison = saved["costs"][str(cost)]
        _require(set(comparison) == {"early_a", "all_high", "paired"}, "eg_onset_reference_arm_roster")
        _policy_line(comparison["early_a"], early, cost, days, weeks, clocks)
        _policy_line(comparison["all_high"], baseline, cost, days, weeks, clocks)
        _paired(comparison["paired"], early, cost, days)
    net = saved["costs"]["43"]["early_a"]["net_pp"]
    contributing = len({event.key[0] for event in eligible if event.status == "eligible_expansion"
                        and labels[event.key][1] is not None})
    positive = net["normal95_exploratory"] is not None and net["normal95_exploratory"][0] > 0
    passed = contributing >= MIN_INTERVAL_SESSIONS and positive
    _require(saved["candidate"] == {"contributing_days": contributing, "net43_lower_positive": positive,
                                    "supported": passed}, "eg_onset_reference_candidate")
    return passed


def verify_onset_expansion(inputs, report, checkpoint):
    """Recreate prospective selections, then verify only their immediate outcomes."""
    calendar = fixed_calendar(inputs.source_dates)
    events = tuple(_read_events(inputs, calendar, checkpoint))
    eligible = tuple(event for event in events if event.status in SUPPORTED)
    keys = tuple(event.key for event in eligible)
    _require(report["schema"] == "EG-onset-expansion-comparison-v1" and report["claim"] == CLAIM
             and report["reference_sessions"] == list(calendar.evaluation_reference_sessions)
             and report["evaluation_boundary_week_flags"] == calendar.evaluation_boundary_week_flags
             and set(report["timings"]) == set(TIMINGS), "eg_onset_reference_report_scope")
    with inputs.open_labels(keys) as labels_at:
        labels = {event.key: _label(event, labels_at(event.key)) for event in eligible}
    checks = [_verify_timing(report["timings"][timing], [event for event in events if timing in event.timings],
                             labels, calendar, checkpoint) for timing in TIMINGS]
    _require(report["candidate_net43_both_timings"] is all(checks), "eg_onset_reference_candidate_both")
    return {"schema": "EG-onset-expansion-reference-v1", "complete": True, "events": len(events),
            "known_label_rows": sum(value[1] is not None for value in labels.values()),
            "prospective_outcomes": _key_evidence(keys)}
