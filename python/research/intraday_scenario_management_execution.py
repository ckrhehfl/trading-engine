"""Seal and audit exact request/open evidence for EH management runs."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_readiness import KST, _opening
from research.intraday_campaign_wsl_transport import MIB, encode, valid_sha
from research.intraday_ed_inputs import MINUTE, InputRefusal, _require
from research.intraday_pattern_calibration import session_close
from research.intraday_scenario_empirical import MAX_NEIGHBORS, MAX_PER_DAY, MIN_NEIGHBOR_DAYS
from research.intraday_scenario_management_campaign import ManagementCampaign
from research.intraday_scenario_management_day import ManagementDayCase
from research.intraday_scenario_management_exit_reference import _orders, _trade
from research.intraday_scenario_management_prices_reference import (
    ReadBar, ReadBounds, ReadSession, audited_bar_reader, audited_session_bounds, reconstruct_held_values,
)
from research.intraday_scenario_management_report import ManagementReport
from research.intraday_scenario_management_report_audit import ModeledOpenReadback
from research.intraday_scenario_rollout import ReplayEvent
from research.intraday_scenario_snapshot import FEATURE_SCHEMA, SnapshotSeal, _read, _time, _write


SCHEMA = "eh-invented-management-execution-v3"
MANAGEMENT_SCHEMA = "eh-sequential-management-trace-v1"
MANAGEMENT_HEADER_MAX = MIB
MAX_COORDINATES = 157 * 20
ATTEMPT_STATUS = {"confirmed": "modeled", "declined": "declined", "unresolved": "indeterminate"}
MODEL_SALES = {"reduce_half": "higher_value_of_reduction", "exit": "higher_value_of_exit"}


def _header(features: SnapshotSeal, scenario: str, mode: str, cost_bps: int, fresh_reentry: bool,
            downside_penalty: Fraction) -> dict:
    _require(type(features) is SnapshotSeal and features.schema == FEATURE_SCHEMA and valid_sha(features.sha256)
             and features.records == 257 and type(features.size) is int and features.size > 0
             and scenario in ("S1", "S2") and mode in ("held", "pending_question")
             and type(cost_bps) is int and cost_bps in (33, 43) and type(fresh_reentry) is bool
             and type(downside_penalty) is Fraction and downside_penalty >= 0,
             "eh_execution_trace_profile_differs")
    return {"schema": SCHEMA, "invented_only": True, "feature_seal": asdict(features), "scenario": scenario,
            "management_mode": mode, "cost_bps": cost_bps, "fresh_reentry": fresh_reentry,
            "downside_penalty": str(downside_penalty)}


def _report_values(value: object) -> object:
    if type(value) is Fraction:
        return str(value)
    if type(value) is dict:
        return {key: _report_values(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_report_values(item) for item in value]
    return value


def _management_header(
    features: SnapshotSeal, scenario: str, mode: str, cost_bps: int, fresh_reentry: bool,
    downside_penalty: Fraction, *, invented_only: bool, risk_budget: Fraction, report: ManagementReport,
) -> dict:
    """Build the exact registered header for sealing and independent EOF readback."""
    header = _header(features, scenario, mode, cost_bps, fresh_reentry, downside_penalty)
    _require(type(invented_only) is bool and type(risk_budget) is Fraction and risk_budget > 0
             and mode == "held" and fresh_reentry is True and type(report) is ManagementReport
             and report.cost_bps == cost_bps and report.original_sessions == 157
             and report.original_symbol_days == MAX_COORDINATES
             and tuple(arm.name for arm in report.arms) == ("fixed", "dynamic", "reentry"),
             "eh_execution_trace_management_profile_differs")
    document = {**header, "schema": MANAGEMENT_SCHEMA, "invented_only": invented_only,
                "risk_budget": str(risk_budget), "report": _report_values(asdict(report))}
    _require(len(encode(document)) <= MANAGEMENT_HEADER_MAX, "eh_execution_trace_management_header_bound")
    return document


def _row(day: str, rank: int, case: ManagementDayCase) -> dict:
    initial = case.initial_entry
    entry = None
    if initial is not None:
        request = initial.request
        _require(request.bounds is not None, "eh_execution_trace_entry_bounds_required")
        entry = {"stop": str(request.bounds.stop), "maximum": str(request.bounds.max_price),
                 "trade": _trade(request.action, initial.decision_at, request.execute_at, request.quantity,
                                 initial.fill_status, initial.fill_quantity, initial.open_price, "higher_value_of_entry")}
    arms = {}
    decisions = []
    event_expiry = None
    if case.managed is not None:
        comparison = case.managed.comparison
        event_expiry = _time(comparison.initial.end_at)
        arms = {"fixed": [comparison.fixed.status, _orders(comparison.fixed.events)],
                "dynamic": [comparison.dynamic.status, _orders(comparison.dynamic.events)]}
        fresh = comparison.reentry
        if fresh is not None:
            later = fresh.holding.events if fresh.holding is not None else ()
            arms["fresh_increment"] = [fresh.status, _orders(fresh.events + later)]
        for assessment in case.managed.decisions:
            values = assessment.values
            utilities = None if values is None else [None if value is None else str(value)
                for value in (values.hold, values.reduce_half, values.exit)]
            decisions.append([_time(assessment.at), _time(assessment.planning_cap), assessment.attempt, assessment.reason,
                              [[day, symbol, _time(at)] for day, symbol, at in assessment.sources], utilities])
    return {"day": day, "rank": rank, "symbol": case.symbol, "status": case.status,
            "reason": case.reason, "initial": entry, "arms": arms, "decisions": decisions, "event_expiry": event_expiry}


def seal_invented_execution(path: Path, campaign: ManagementCampaign, features: SnapshotSeal) -> SnapshotSeal:
    """Save the original roster and exact modeled requests before independent readback."""
    report = campaign.report
    header = _header(features, campaign.scenario, campaign.management_mode, report.cost_bps,
                     any(arm.name == "reentry" for arm in report.arms), campaign.downside_penalty)
    return _seal_execution(path, campaign, header)


def seal_management_execution(
    path: Path, campaign: ManagementCampaign, features: SnapshotSeal, *, invented_only: bool, risk_budget: Fraction,
) -> SnapshotSeal:
    """Seal the registered held-policy trace, settings and aggregate report together."""
    report = campaign.report
    header = _management_header(features, campaign.scenario, campaign.management_mode, report.cost_bps,
        any(arm.name == "reentry" for arm in report.arms), campaign.downside_penalty,
        invented_only=invented_only, risk_budget=risk_budget, report=report)
    return _seal_execution(path, campaign, header)


def _seal_execution(path: Path, campaign: ManagementCampaign, header: dict) -> SnapshotSeal:
    report = campaign.report
    _require(len(campaign.days) == report.original_sessions == 157
             and report.original_symbol_days == MAX_COORDINATES and all(len(day.cases) == 20 for day in campaign.days),
             "eh_execution_trace_original_roster_required")
    rows = (_row(day.day, rank, case) for day in campaign.days for rank, case in enumerate(day.cases, 1))
    return _write(path, header, rows, maximum=MAX_COORDINATES)


def _exact(text: str, reason: str) -> Fraction:
    _require(type(text) is str and 0 < len(text) <= 128, reason)
    try:
        price = Fraction(text)
    except (ValueError, ZeroDivisionError) as error:
        raise InputRefusal(reason) from error
    _require(str(price) == text, reason)
    return price


def _price(text: str) -> Fraction:
    price = _exact(text, "eh_execution_trace_invalid_price")
    _require(price > 0, "eh_execution_trace_invalid_price")
    return price


def _clock_text(text: str, reason: str) -> datetime:
    _require(type(text) is str, reason)
    try:
        at = datetime.fromisoformat(text)
    except ValueError as error:
        raise InputRefusal(reason) from error
    _require(_time(at) == text and at.second == at.microsecond == 0, reason)
    return at


def _verify_trade(trade: list, day: str, symbol: str, actions: tuple[str, ...], previous: datetime | None,
                  opens: ModeledOpenReadback, counts: Counter) -> tuple[datetime, Fraction | None, str]:
    _require(type(trade) is list and len(trade) == 8, "eh_execution_trace_trade_shape_differs")
    action, decision, execute, requested, status, filled, price_text, reason = trade
    at = _clock_text(decision, "eh_execution_trace_trade_clock_differs")
    execute_at = _clock_text(execute, "eh_execution_trace_trade_clock_differs")
    _require(execute_at == at + MINUTE
             and at.astimezone(KST).date().isoformat() == execute_at.astimezone(KST).date().isoformat() == day
             and (previous is None or at > previous), "eh_execution_trace_trade_clock_differs")
    if action in ("enter", "reenter"):
        reasons = ("higher_value_of_entry",)
    elif action == "reduce_half":
        reasons = (MODEL_SALES["reduce_half"],)
    else:
        reasons = (MODEL_SALES["exit"], "structural_close_failure", "fixed_episode_end")
    _require(action in actions and reason in reasons and status in ("modeled", "declined", "indeterminate")
             and (action in ("enter", "reenter") or status != "declined")
             and type(requested) is int and requested > 0 and type(filled) is int
             and filled == (requested if status == "modeled" else 0), "eh_execution_trace_trade_outcome_differs")
    price = None if price_text is None else _price(price_text)
    expected = opens(day, symbol, execute_at)
    _require((expected is None or type(expected) is Fraction and expected > 0) and expected == price
             and (price is None) == (status == "indeterminate"), "eh_execution_trace_open_differs")
    counts[status] += 1
    return execute_at, price, status


def _verify_decisions(row: dict, calendar: Mapping[str, tuple[str, ...]], prior: frozenset[str], mode: str, *,
                      cutoff: datetime, stop: Fraction, downside_penalty: Fraction, read_session: ReadSession | None,
                      read_bar: ReadBar | None, event_expiry: datetime, read_bounds: ReadBounds | None = None) -> int:
    day, arms = row["day"], row["arms"]
    initial = row["initial"]["trade"]
    dynamic = arms["dynamic"][1]
    fresh = arms.get("fresh_increment", (None, []))[1]
    entries = {1: initial}
    if fresh and fresh[0][4] == "modeled":
        entries[2] = fresh[0]
    sales = {1: dynamic, 2: fresh[1:]}
    model_sales = {(attempt, trade[1]) for attempt, trades in sales.items() for trade in trades
                   if trade[7] in MODEL_SALES.values()}
    missing_prefix = "held_chart_prefix_unknown" if mode == "held" else "chart_question_unavailable"
    opening, closing = _opening(day), session_close(day)
    previous = ""
    payoff_checks = 0
    for decision in row["decisions"]:
        _require(type(decision) is list and len(decision) == 6, "eh_execution_trace_decision_shape_differs")
        clock, cap_text, attempt, reason, sources, utilities = decision
        at = _clock_text(clock, "eh_execution_trace_decision_clock_differs")
        cap = _clock_text(cap_text, "eh_execution_trace_decision_cap_differs")
        _require(type(attempt) is int and attempt in entries and clock > previous
                 and opening + 22 * MINUTE <= at < closing and not (at - opening - 2 * MINUTE) % (5 * MINUTE)
                 and type(sources) is list and len(sources) <= MAX_NEIGHBORS,
                 "eh_execution_trace_decision_clock_or_sources_differ")
        previous = clock
        entered = _clock_text(entries[attempt][2], "eh_execution_trace_trade_clock_differs")
        _require(cap == min(entered + 30 * MINUTE, event_expiry) and entered + 3 * MINUTE <= at < cap - MINUTE,
                 "eh_execution_trace_decision_outside_holding")
        earlier = [trade for trade in sales[attempt] if trade[2] <= clock]
        remaining = entries[attempt][5] - sum(trade[5] for trade in earlier)
        _require(remaining > 0 and all(trade[4] == "modeled" for trade in earlier),
                 "eh_execution_trace_decision_without_position")
        episodes, days = set(), Counter()
        coordinates = []
        for source in sources:
            _require(type(source) is list and len(source) == 3, "eh_execution_trace_decision_source_differs")
            date_key, symbol, source_clock = source
            source_at = _clock_text(source_clock, "eh_execution_trace_decision_source_differs")
            _require(type(date_key) is str and date_key in prior and symbol in calendar[date_key]
                     and source_at.astimezone(KST).date().isoformat() == date_key
                     and _opening(date_key) + 22 * MINUTE <= source_at < session_close(date_key)
                     and not (source_at - _opening(date_key) - 2 * MINUTE) % (5 * MINUTE)
                     and (date_key, symbol) not in episodes and days[date_key] < MAX_PER_DAY,
                     "eh_execution_trace_decision_source_differs")
            episodes.add((date_key, symbol))
            days[date_key] += 1
            coordinates.append((date_key, symbol, source_at))
        expected = "hold"
        scores = None
        already_reduced = any(trade[0] == "reduce_half" for trade in earlier)
        if attempt == 2:
            _require(reason == "fresh_holding_rule_only" and not sources and utilities is None,
                     "eh_execution_trace_fresh_decision_differs")
        elif reason == "ready":
            can_reduce = remaining > 1 and not already_reduced
            _require(len(days) >= MIN_NEIGHBOR_DAYS and type(utilities) is list and len(utilities) == 3
                     and (utilities[1] is not None) == can_reduce, "eh_execution_trace_utility_support_differs")
            hold = _exact(utilities[0], "eh_execution_trace_utility_differs")
            exit_value = _exact(utilities[2], "eh_execution_trace_utility_differs")
            reduce = _exact(utilities[1], "eh_execution_trace_utility_differs") if can_reduce else None
            scores = (hold, reduce, exit_value)
            if reduce is not None and reduce > max(hold, exit_value):
                expected = "reduce_half"
            elif exit_value > hold:
                expected = "exit"
        else:
            _require(utilities is None and (
                (reason == missing_prefix and not sources)
                or (reason == "insufficient_prior_chart_paths" and len(days) < MIN_NEIGHBOR_DAYS)
                or (reason in ("selected_prior_session_missing", "unresolved_path_mass") and len(days) >= MIN_NEIGHBOR_DAYS)),
                "eh_execution_trace_utility_support_differs")
        if read_session is not None and reason in ("ready", "selected_prior_session_missing", "unresolved_path_mass"):
            _require(read_bar is not None, "eh_execution_trace_payoff_inputs_required")
            reference_reason, reference = reconstruct_held_values(day=day, symbol=row["symbol"], at=at, cap=cap,
                cutoff=cutoff, entered_at=entered, quantity=remaining, half_reduced=already_reduced, stop=stop,
                downside_penalty=downside_penalty, sources=tuple(coordinates), read_session=read_session, read_bar=read_bar,
                read_bounds=read_bounds)
            recomputed = None if reference is None else (reference.hold, reference.reduce_half, reference.exit)
            _require((reference_reason, recomputed) == (reason, scores), "eh_execution_trace_recomputed_utility_differs")
            payoff_checks += 1
        orders = [trade for trade in sales[attempt] if trade[1] == clock]
        if expected == "hold":
            _require(not orders, "eh_execution_trace_utility_action_differs")
        else:
            _require(len(orders) == 1 and orders[0][0] == expected and orders[0][7] == MODEL_SALES[expected],
                     "eh_execution_trace_utility_action_differs")
            model_sales.remove((attempt, clock))
    _require(not model_sales, "eh_execution_trace_model_sale_without_decision")
    return payoff_checks


def verify_invented_execution(
    path: Path, seal: SnapshotSeal, *, features: SnapshotSeal, calendar: Mapping[str, tuple[str, ...]],
    scenario: str, mode: str, cost_bps: int, fresh_reentry: bool, modeled_open_at: ModeledOpenReadback,
    downside_penalty: Fraction, read_session: ReadSession | None = None,
    breakout_limits: Mapping[tuple[str, str], tuple[datetime, datetime, Fraction, Fraction]] | None = None,
) -> dict[str, int]:
    """Reopen exact requests against an external roster and independent modeled opens."""
    header = _header(features, scenario, mode, cost_bps, fresh_reentry, downside_penalty)
    return _verify_execution(path, seal, header=header, calendar=calendar, modeled_open_at=modeled_open_at,
        downside_penalty=downside_penalty, read_session=read_session, breakout_limits=breakout_limits)


def verify_management_execution(
    path: Path, seal: SnapshotSeal, *, features: SnapshotSeal, calendar: Mapping[str, tuple[str, ...]],
    scenario: str, mode: str, cost_bps: int, fresh_reentry: bool, modeled_open_at: ModeledOpenReadback,
    downside_penalty: Fraction, read_session: ReadSession,
    breakout_limits: Mapping[tuple[str, str], tuple[datetime, datetime, Fraction, Fraction]],
    invented_only: bool, risk_budget: Fraction, report: ManagementReport,
) -> dict[str, int]:
    """Reopen the registered trace with an independent roster, price and payoff readback."""
    header = _management_header(features, scenario, mode, cost_bps, fresh_reentry, downside_penalty,
        invented_only=invented_only, risk_budget=risk_budget, report=report)
    _require(callable(read_session) and isinstance(breakout_limits, Mapping),
             "eh_execution_trace_payoff_inputs_required")
    return _verify_execution(path, seal, header=header, calendar=calendar, modeled_open_at=modeled_open_at,
        downside_penalty=downside_penalty, read_session=read_session, breakout_limits=breakout_limits)


def _verify_execution(
    path: Path, seal: SnapshotSeal, *, header: dict, calendar: Mapping[str, tuple[str, ...]],
    modeled_open_at: ModeledOpenReadback, downside_penalty: Fraction, read_session: ReadSession | None,
    breakout_limits: Mapping[tuple[str, str], tuple[datetime, datetime, Fraction, Fraction]] | None,
) -> dict[str, int]:
    mode, fresh_reentry = header["management_mode"], header["fresh_reentry"]
    _require((read_session is None and breakout_limits is None) or (callable(read_session) and isinstance(breakout_limits, Mapping)),
             "eh_execution_trace_payoff_inputs_required")
    _require(type(seal) is SnapshotSeal and seal.records == MAX_COORDINATES and callable(modeled_open_at)
             and isinstance(calendar, Mapping) and len(calendar) == 257 and all(
                 type(day) is str and date.fromisoformat(day).isoformat() == day and type(symbols) is tuple
                 and len(symbols) == len(set(symbols)) == 20 and all(type(symbol) is str and symbol for symbol in symbols)
                 for day, symbols in calendar.items()),
             "eh_execution_trace_original_roster_required")
    counts = Counter(modeled=0, declined=0, indeterminate=0)
    read_bar = None
    read_bounds = None
    if read_session is not None:
        counts["payoff_checks"] = 0
        read_bar = audited_bar_reader(read_session)
        read_bounds = audited_session_bounds(read_session)
    splits = fixed_calendar(tuple(sorted(calendar)))
    fold_scope = {}
    for fold in splits.folds:
        fold_scope.update(dict.fromkeys(fold.test_sessions, (frozenset(fold.training_sessions), fold.first_test_opening)))
    coordinates = ((day, rank, symbol) for day in splits.evaluation_reference_sessions
                   for rank, symbol in enumerate(calendar[day], 1))
    for (row, _), (day, rank, symbol) in zip(_read(path, seal, header, maximum=MAX_COORDINATES), coordinates, strict=True):
        _require(set(row) == {"day", "rank", "symbol", "status", "reason", "initial", "arms", "decisions", "event_expiry"}
                 and (row["day"], row["rank"], row["symbol"]) == (day, rank, symbol) and type(row["rank"]) is int
                 and type(row["reason"]) is str and bool(row["reason"]), "eh_execution_trace_coordinate_differs")
        status, initial, arms = row["status"], row["initial"], row["arms"]
        _require(type(status) is str and type(arms) is dict and type(row["decisions"]) is list
                 and len(row["decisions"]) <= 12
                 and (status == "confirmed" or (not row["decisions"] and row["event_expiry"] is None)),
                 "eh_execution_trace_case_shape_differs")
        if status not in ATTEMPT_STATUS:
            _require(status in ("no_event", "unknown", "unsupported", "no_entry") and initial is None and not arms,
                     "eh_execution_trace_unattempted_case_differs")
            continue
        _require(type(initial) is dict and set(initial) == {"stop", "maximum", "trade"}, "eh_execution_trace_entry_differs")
        stop, maximum = _price(initial["stop"]), _price(initial["maximum"])
        limits = None if breakout_limits is None else breakout_limits.get((day, symbol))
        if breakout_limits is not None:
            _require(type(limits) is tuple and len(limits) == 4 and limits[2:] == (stop, maximum),
                     "eh_execution_trace_breakout_bounds_differ")
            trade = initial["trade"]
            _require(type(trade) is list and len(trade) == 8 and trade[1] == _time(limits[0]),
                     "eh_execution_trace_breakout_clock_differs")
        entered, price, outcome = _verify_trade(initial["trade"], day, symbol, ("enter",), None, modeled_open_at, counts)
        expected_outcome = "indeterminate" if price is None else "modeled" if stop < price <= maximum else "declined"
        _require(stop < maximum and outcome == ATTEMPT_STATUS[status] == expected_outcome,
                 "eh_execution_trace_entry_differs")
        expected_arms = {"fixed", "dynamic", "fresh_increment"} if fresh_reentry else {"fixed", "dynamic"}
        _require(set(arms) == (expected_arms if status == "confirmed" else set()), "eh_execution_trace_arms_differ")
        if status != "confirmed":
            continue
        expiry = _clock_text(row["event_expiry"], "eh_execution_trace_event_expiry_differs")
        _require(entered < expiry < session_close(day) and (limits is None or limits[1] == expiry),
                 "eh_execution_trace_event_expiry_differs")
        for name in ("fixed", "dynamic", "fresh_increment"):
            if name not in arms:
                continue
            arm = arms[name]
            actions = ("exit",) if name == "fixed" else ("reduce_half", "exit")
            fresh = name == "fresh_increment"
            terminals = ("closed", "unresolved", "not_reentered") if fresh else ("closed", "unresolved")
            _require(type(arm) is list and len(arm) == 2 and arm[0] in terminals and type(arm[1]) is list
                     and len(arm[1]) <= (3 if fresh else len(actions)), "eh_execution_trace_arm_differs")
            previous = dynamic_end if fresh else entered
            remaining = 0 if fresh else initial["trade"][5]
            reentered = reduced = False
            fill_status = None
            sale_cap = min(entered + 30 * MINUTE, expiry)
            for index, trade in enumerate(arm[1]):
                allowed = ("reenter",) if fresh and index == 0 else actions
                previous, _, fill_status = _verify_trade(trade, day, symbol, allowed, previous, modeled_open_at, counts)
                _require(name == "dynamic" or trade[7] not in MODEL_SALES.values(),
                         "eh_execution_trace_model_sale_outside_dynamic")
                action, requested, filled = trade[0], trade[3], trade[5]
                _require(fill_status == "modeled" or index == len(arm[1]) - 1, "eh_execution_trace_after_failed_fill")
                if action == "reenter":
                    _require(arms["dynamic"][0] == "closed" and previous + 5 * MINUTE <= expiry,
                             "eh_execution_trace_reentry_requires_close_and_time")
                    sale_cap = min(previous + 30 * MINUTE, expiry)
                    reentered, remaining = bool(filled), filled
                else:
                    _require(previous <= sale_cap and (trade[7] != "fixed_episode_end" or previous == sale_cap),
                             "eh_execution_trace_sale_cap_differs")
                    _require(requested == (remaining // 2 if action == "reduce_half" else remaining)
                             and (action != "reduce_half" or not reduced), "eh_execution_trace_inventory_differs")
                    remaining -= filled
                    reduced = reduced or action == "reduce_half"
            terminal = arm[0]
            if fresh and arms["dynamic"][0] != "closed":
                valid_terminal = terminal == "unresolved" and not arm[1]
            elif fresh and not reentered:
                valid_terminal = terminal in ("not_reentered", "unresolved") and (
                    fill_status is None or terminal == ("not_reentered" if fill_status == "declined" else "unresolved"))
            else:
                valid_terminal = terminal == ("closed" if remaining == 0 else "unresolved")
            _require(valid_terminal, "eh_execution_trace_terminal_inventory_differs")
            if name == "dynamic":
                dynamic_end = previous
        prior, cutoff = fold_scope[day]
        checked = _verify_decisions(row, calendar, prior, mode, cutoff=cutoff, stop=stop,
            downside_penalty=downside_penalty, read_session=read_session, read_bar=read_bar, event_expiry=expiry,
            read_bounds=read_bounds)
        if read_session is not None:
            counts["payoff_checks"] += checked
    return dict(counts)
