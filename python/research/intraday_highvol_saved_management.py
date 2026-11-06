"""Compare paired EG exits from authenticated saved decision and quote frames."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import isfinite

from research import intraday_chart_cash as cash
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_readiness import _week
from research.intraday_chart_evaluation import _estimate
from research.intraday_chart_folds import Key
from research.intraday_chart_paired_paths import ExitLeg, _path_result
from research.intraday_ed_inputs import MINUTE, _require
from research.intraday_highvol_management import HoldingUpdate, ManagementPair
from research.intraday_highvol_policy import (
    ARMS, BRANCHES, CHART_ARMS, COST_BPS, EntryForecast, ForecastKey, _events, _label,
)
from research.intraday_highvol_snapshot import HOLD_STEPS, PRIMARY_HORIZON, TIMINGS, _fraction
from research.intraday_pattern_calibration import session_close


@dataclass(frozen=True)
class HoldingForecast:
    hold_advantage_pct: float | None = None
    replay_ambiguous: bool = False
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class _Context:
    decision: datetime
    close: Fraction | None
    chart: bool
    peer_timings: frozenset[str]


@dataclass(frozen=True)
class _Holding:
    stop: Fraction
    target: Fraction
    contexts: tuple[_Context, ...]


@dataclass(frozen=True)
class _Observation:
    key: Key
    status: str
    pair: ManagementPair | None = None


def _positive_price(value) -> Fraction | None:
    price = None if value is None else _fraction(value)
    _require(price is None or price > 0, "invalid_eg_management_quote")
    return price


def _context(frame, entry: datetime, step: int, timings) -> _Context:
    at = entry + (step - 1) * MINUTE
    close = _positive_price(frame["last_close"])
    chart, peers = frame["chart"]["status"], frame["peers"]
    _require(frame["decision_at"] == at.isoformat() and set(peers) == set(timings)
             and chart in ("available", "unavailable") and (chart != "available" or close is not None)
             and all(peer["status"] in ("available", "unavailable") for peer in peers.values()),
             "eg_saved_management_context_changed")
    return _Context(at, close, chart == "available", frozenset(
        timing for timing, peer in peers.items() if peer["status"] == "available"))


def _contexts(frame) -> _Holding:
    confirmation = frame["attempt"]["confirmation"]
    stop, target = (_positive_price(confirmation[name]) for name in ("stop", "target"))
    _require(stop is not None and target is not None and stop < target, "eg_saved_management_barriers_changed")
    entry = datetime.fromisoformat(confirmation["intended_entry_at"])
    rows = frame["holding"]
    _require(type(rows) is list and tuple(item["step_minutes"] for item in rows) == HOLD_STEPS,
             "eg_saved_management_update_roster_changed")
    return _Holding(stop, target, tuple(_context(row, entry, step, frame["timings"])
                                       for row, step in zip(rows, HOLD_STEPS, strict=True)))


def _checked_forecast(value) -> HoldingForecast:
    _require(type(value) is HoldingForecast and type(value.replay_ambiguous) is bool,
             "typed_eg_holding_forecast_required")
    gain, reason = value.hold_advantage_pct, value.unsupported_reason
    _require((gain is not None and type(gain) is float and isfinite(gain) and reason is None)
             or (gain is None and type(reason) is str and bool(reason) and not value.replay_ambiguous),
             "invalid_eg_holding_forecast")
    return value


def _bind_events(calendar, features, entry_forecasts, holding_forecasts):
    metadata = {}
    test = set(calendar.evaluation_reference_sessions)

    def feature_stream():
        for frame in features:
            key = tuple(frame["key"])
            if key[0] in test and frame["entry"] is not None and frame["entry"]["primary_clock_supported"] is True:
                metadata[key] = _contexts(frame)
            yield frame

    events = _events(feature_stream(), set(calendar.source_reference_sessions), test, entry_forecasts)
    used, bound = set(), []
    for event in events:
        if event.support not in ("common", "chart_only"):
            continue
        holding = metadata[event.key]
        scores = {}
        for arm in ARMS:
            states = []
            for step, context in zip(HOLD_STEPS, holding.contexts, strict=True):
                key = event.branch, event.timing, arm, (*event.key, step)
                eligible = context.chart and (arm in CHART_ARMS or (
                    event.support == "common" and event.timing in context.peer_timings))
                value = None
                if not eligible:
                    reason = "holding_chart_unavailable" if not context.chart else "holding_peer_unavailable"
                elif key not in holding_forecasts:
                    reason = "holding_forecast_absent"
                else:
                    value = _checked_forecast(holding_forecasts[key])
                    used.add(key)
                    reason = value.unsupported_reason
                states.append((value, reason))
            scores[arm] = tuple(states)
        if event.support == "common":
            bound.append((event, holding, scores))
    _require(len(used) == len(holding_forecasts), "eg_holding_forecast_outside_sealed_evaluation_support")
    return bound


def _maturity(price, saved, at, close):
    _require((price is None) == (saved is None), "eg_saved_management_quote_maturity_missing")
    if saved is not None:
        mature = datetime.fromisoformat(saved)
        _require(mature.isoformat() == saved and mature.utcoffset() == at.utcoffset()
                 and at + 3 * MINUTE <= mature <= close, "eg_saved_management_update_quote_maturity_changed")


def _known_quote(row, *, at, close, cap_available):
    price = _positive_price(row["next_open"])
    _require(row["next_open_at"] == at.isoformat() and row["decision_at"] == (at - MINUTE).isoformat()
             and row["status"] == ("next_open_unavailable" if price is None else
                                   "available" if cap_available else "cap_unavailable"),
             "eg_saved_management_update_quote_changed")
    _maturity(price, row["next_open_mature_at"], at, close)
    return price


def _update(context, score, reason, stop, reduced, previous_nonpositive):
    gain = None
    if context.close is not None and context.close <= stop:
        action, reason = "exit", "structural_close_failure"
    elif context.close is None:
        action, reason = "hold", "structural_close_unavailable"
    elif score is not None and score.replay_ambiguous:
        action, reason = "hold", "holding_replay_ambiguous"
    elif score is None or score.hold_advantage_pct is None:
        action = "hold"
    else:
        gain = score.hold_advantage_pct
        if gain <= 0 and previous_nonpositive:
            action, reason = "exit", "consecutive_nonpositive_hold_gain"
        elif gain <= 0 and not reduced:
            action, reason = "reduce_half", "first_nonpositive_hold_gain"
        else:
            action, reason = "hold", "no_exit_signal"
    return HoldingUpdate(context.decision, context.close, gain, action, reason)


def _sell(book, symbol, step, reason, quote, *, half=False):
    order = (cash.reduce_half(book, symbol, exit_price=quote) if half else
             cash.exit(book, symbol, quantity=book.lots[0].quantity, exit_price=quote))
    return order.book, ExitLeg(step, reason, order.outcome, order.quantity, quote)


def _pair(event, holding, forecasts, label, cost):
    primary = label["primary"]
    observed = _label(primary, event.scenario, immediate=False)
    status = primary["status"]
    _require(status in ("admitted", "entry_refused", "entry_unavailable"), "eg_saved_management_entry_clock_changed")
    entry_open = _positive_price(primary["entry_open"])
    _require((entry_open is None) == (status == "entry_unavailable")
             and (entry_open is None or (holding.stop < entry_open < holding.target) == (status == "admitted")),
             "eg_saved_management_entry_bracket_changed")
    if status != "admitted":
        return _Observation(event.key, status)
    day, symbol, _ = event.key
    entry = cash.enter(cash.new_book("EG-paired-path"), symbol, entry_price=entry_open,
                       observed_prices={}, cost_bps=cost)
    if not entry.filled:
        return _Observation(event.key, "nominal_entry_declined")
    cap_open = _positive_price(primary["fixed_exit_open"])
    _require((cap_open is None) == (observed.gross is None)
             and (cap_open is None or observed.gross == cap_open / entry_open - 1),
             "eg_saved_management_cap_return_changed")
    closing = session_close(day)
    _maturity(cap_open, primary["fixed_mature_at"], event.scenario.exit, closing)
    updates, legs = [], []
    book = entry.book
    reduction_requested = previous_nonpositive = False
    quotes = label["holding"]
    _require(type(quotes) is list and tuple(row["step_minutes"] for row in quotes) == HOLD_STEPS,
             "eg_saved_management_quote_roster_changed")
    for step, context, (score, reason), quote in zip(HOLD_STEPS, holding.contexts, forecasts, quotes, strict=True):
        update = _update(context, score, reason, holding.stop, reduction_requested, previous_nonpositive)
        updates.append(update)
        gain = update.hold_gain
        previous_nonpositive = gain is not None and gain <= 0
        if update.action == "hold":
            continue
        half = update.action == "reduce_half"
        if half:
            reduction_requested = True
            if book.lots[0].quantity == 1:
                legs.append(ExitLeg(step, update.reason, "declined", 0, None))
                continue
        price = _known_quote(quote, at=context.decision + MINUTE, close=closing,
                             cap_available=cap_open is not None)
        book, leg = _sell(book, symbol, step, update.reason, price, half=half)
        legs.append(leg)
        if book.inventory_uncertain or not book.lots:
            break
    if book.lots and not book.inventory_uncertain:
        book, leg = _sell(book, symbol, PRIMARY_HORIZON, "fixed_cap", cap_open)
        legs.append(leg)
    fixed_book, fixed_leg = _sell(entry.book, symbol, PRIMARY_HORIZON, "fixed_cap", cap_open)
    notional = entry.quantity * entry_open
    return _Observation(event.key, "modeled_entry", ManagementPair(
        _path_result(fixed_book, entry.quantity, notional, entry.assumed_cost, (fixed_leg,), ()),
        _path_result(book, entry.quantity, notional, entry.assumed_cost, legs, ()), tuple(updates)))


def _evaluate(bound, labels_at):
    observations = defaultdict(list)
    current_key, label = None, None
    for event, holding, scores in bound:
        for cost_index, cost in enumerate(COST_BPS):
            for arm, decision in zip(ARMS, event.decisions[cost_index], strict=True):
                if decision == "selected":
                    if event.key != current_key:
                        current_key = event.key
                        label = labels_at(current_key)
                        _require(type(label) is dict and tuple(label["key"]) == current_key,
                                 "eg_saved_management_outcome_key_changed")
                    row = _pair(event, holding, scores[arm], label, cost)
                else:
                    row = _Observation(event.key, decision)
                observations[event.branch, event.timing, cost, arm].append(row)
    return observations


def _summary(rows, calendar):
    sessions, weeks = calendar.evaluation_reference_sessions, calendar.evaluation_week_session_counts
    entries = [row for row in rows if row.pair is not None]
    matched = [row for row in entries if row.pair.fixed.status == row.pair.managed.status == "closed"]
    deltas = [(row.key, row.pair.managed.net_return - row.pair.fixed.net_return) for row in matched]
    by_week, by_symbol = Counter(_week(key[0]) for key, _ in deltas), Counter(key[1] for key, _ in deltas)
    opportunities = Counter(_week(row.key[0]) for row in rows)
    requested = Counter(_week(row.key[0]) for row in rows if row.status in
                        ("entry_unavailable", "entry_refused", "nominal_entry_declined", "modeled_entry"))
    modeled = Counter(_week(row.key[0]) for row in entries)

    def path_summary(name):
        paths = [(row.key[0], getattr(row.pair, name)) for row in entries]
        known = [(day, path) for day, path in paths if path.status == "closed"]
        return {"status_counts": dict(Counter(path.status for _, path in paths)),
                "gross_pp": _estimate([(day, float(100 * path.gross_return)) for day, path in known], sessions),
                "net_pp": _estimate([(day, float(100 * path.net_return)) for day, path in known], sessions),
                "average_hold_minutes": _estimate([(day, float(path.average_hold_minutes))
                                                   for day, path in known], sessions)}

    return {"entry_status_counts": dict(Counter(row.status for row in rows)),
            "fixed": path_summary("fixed"), "managed": path_summary("managed"),
            "update_reasons": dict(Counter(update.reason for row in entries for update in row.pair.updates)),
            "managed_leg_statuses": dict(Counter(leg.status for row in entries for leg in row.pair.managed.legs)),
            "known_matched": len(matched), "unresolved_matched": len(entries) - len(matched),
            "paired_managed_minus_fixed_net_pp": _estimate([(key[0], float(100 * value)) for key, value in deltas], sessions),
            "known_matched_improvements": sum(value > 0 for _, value in deltas),
            "known_matched_worsenings": sum(value < 0 for _, value in deltas),
            "largest_symbol_known_matched_share": max(by_symbol.values()) / len(deltas) if deltas else None,
            "known_matched_by_symbol": dict(sorted(by_symbol.items())),
            "weeks": {week: {"reference_sessions": count, "common_opportunities": opportunities[week],
                             "selected": requested[week], "modeled_entries": modeled[week],
                             "known_matched": by_week[week]} for week, count in weeks.items()}}


def compare_saved_management(
    source_dates: Sequence[str], features: Iterable[dict], *, labels_at: Callable[[Key], dict],
    entry_forecasts: Mapping[ForecastKey, EntryForecast], holding_forecasts: Mapping[ForecastKey, HoldingForecast],
) -> dict:
    """Clone each supported learned entry at each cost; pair fixed and managed exits.

    A caller authenticates the snapshot and all fold forecast artifacts. The
    functions here never fit, read a label outside the fixed evaluation scope,
    choose a winning arm, run a shared account, or redeploy released cash.
    """
    calendar = fixed_calendar(source_dates)
    _require(isinstance(entry_forecasts, Mapping) and isinstance(holding_forecasts, Mapping),
             "invalid_eg_management_forecast_scope")
    bound = _bind_events(calendar, features, entry_forecasts, holding_forecasts)
    observations = _evaluate(bound, labels_at)
    return {"schema": "EG-saved-management-v1", "reference_sessions": list(calendar.evaluation_reference_sessions),
            "groups": {branch: {timing: {str(cost): {
                arm: _summary(observations[branch, timing, cost, arm], calendar) for arm in ARMS
            } for cost in COST_BPS} for timing in TIMINGS} for branch in BRANCHES},
            "claim": "paired nominal lots on stored prices; no shared account, physical shares or broker fills"}
