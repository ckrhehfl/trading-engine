"""Verify EH management accounting from the frozen roster and replay ledgers."""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from functools import partial

from research.intraday_campaign_readiness import KST
from research.intraday_ed_inputs import MINUTE, InputRefusal, _clock, _require
from research.intraday_scenario_management_compare import ManagementComparison
from research.intraday_scenario_management_report import (
    ArmSummary,
    Coordinate,
    EventTotals,
    ManagementContrast,
    ManagementObservation,
    ManagementReport,
    PairContribution,
    WeeklyEntries,
)
from research.intraday_scenario_policy import Episode
from research.intraday_scenario_rollout import ReplayEvent


ModeledOpenReadback = Callable[[str, str, datetime], Fraction | None]


@dataclass(frozen=True)
class _Known:
    gross: Fraction
    cost: Fraction
    exposure: Fraction
    turnover: Fraction

    @property
    def net(self) -> Fraction:
        return self.gross - self.cost


def _audit_arm(
    initial: Episode, terminal: Episode, status: str, events: tuple[ReplayEvent, ...], *, fresh: bool,
    read_open: Callable[[datetime], Fraction | None] | None,
) -> tuple[int, _Known | None]:
    lot, = initial.book.lots
    _require((terminal.symbol, terminal.start_at, terminal.end_at, terminal.first_lot_id, terminal.cost_bps,
              terminal.book.strategy, terminal.book.initial_capital)
             == (initial.symbol, initial.start_at, initial.end_at, initial.first_lot_id, initial.cost_bps,
                 initial.book.strategy, initial.book.initial_capital), "eh_management_audit_arm_identity")
    _require(status in (("closed", "not_reentered", "unresolved") if fresh else ("closed", "unresolved")),
             "eh_management_audit_terminal_status")

    lot_id, price, units = lot.lot_id, lot.entry_price, lot.quantity
    next_lot = initial.book.next_lot_id
    original_units, half_reduced = units, False
    rate = Fraction(initial.cost_bps, 10_000)
    cash, turnover = initial.book.cash, price * units
    gross = cost = exposure_area = Fraction()
    last_fill = last_event = initial.entered_at
    fresh_count, pending, exits = 0, None, []
    for event in events:
        _clock(event.at)
        _require(last_event < event.at <= initial.end_at and not (event.at - initial.start_at) % MINUTE,
                 "eh_management_audit_event_clock")
        last_event = event.at
        if event.fill_status is None:
            _require(pending is None, "eh_management_audit_missing_fill")
            if event.execute_at is not None:
                _require(event.action in ("exit", "reduce_half", "reenter") and event.execute_at == event.at + MINUTE
                         and type(event.quantity) is int and event.quantity > 0, "eh_management_audit_request")
                pending = event
            continue
        _require(pending is not None and event.action == pending.action and event.at == pending.execute_at
                 and event.execute_at is None and event.fill_status in ("modeled", "declined", "indeterminate"),
                 "eh_management_audit_fill_request")
        requested = pending.quantity
        pending = None
        if read_open is not None:
            expected_open = read_open(event.at)
            _require(
                ((expected_open is None and event.open_price is None) or
                 (type(expected_open) is Fraction and expected_open > 0
                  and type(event.open_price) is Fraction and expected_open == event.open_price))
                and (expected_open is None) == (event.fill_status == "indeterminate"),
                "eh_management_audit_fill_open_readback",
            )
        if event.fill_status != "modeled":
            _require(event.quantity == 0, "eh_management_audit_unmodeled_quantity")
            continue

        amount, fill_price = event.quantity, event.open_price
        _require(type(amount) is int and amount == requested and type(fill_price) is Fraction and fill_price > 0,
                 "eh_management_audit_modeled_fill")
        exposure_area += price * units * ((event.at - last_fill) // MINUTE)
        last_fill = event.at
        value = amount * fill_price
        turnover += value
        if event.action == "reenter":
            _require(fresh and fresh_count == 0 and units == 0, "eh_management_audit_fresh_entry")
            lot_id, price, units = next_lot, fill_price, amount
            original_units, half_reduced = amount, False
            cash -= value + value * rate
            next_lot += 1
            fresh_count = 1
            continue
        if event.action == "reduce_half":
            _require(not half_reduced and amount == units // 2, "eh_management_audit_half_reduction")
            half_reduced = True
        else:
            _require(event.action == "exit" and amount == units, "eh_management_audit_full_exit")
        basis = amount * price
        fee = basis * rate
        exits.append((lot_id, initial.symbol, amount, basis, fee, value))
        gross += value - basis
        cost += fee
        cash += value
        units -= amount

    prior = initial.book.realized_exits
    actual_exits = terminal.book.realized_exits
    _require(actual_exits[:len(prior)] == prior and
             [(row.lot_id, row.symbol, row.quantity, row.entry_basis, row.allocated_entry_cost, row.gross_proceeds)
              for row in actual_exits[len(prior):]] == exits, "eh_management_audit_realized_exits")
    remaining = tuple((row.lot_id, row.symbol, row.original_quantity, row.quantity, row.entry_price,
                       row.original_entry_cost, row.half_reduced) for row in terminal.book.lots)
    expected = ((lot_id, initial.symbol, original_units, units, price, original_units * price * rate, half_reduced),) if units else ()
    _require(terminal.book.cash == cash and terminal.book.next_lot_id == next_lot and remaining == expected,
             "eh_management_audit_cash_inventory")
    if status == "unresolved":
        return fresh_count, None
    _require(units == 0 and pending is None and terminal.pending is None and not terminal.book.indeterminate
             and terminal.entered_at is None and (not fresh or (status == "closed") == bool(fresh_count))
             and (status != "closed" or terminal.closed_at == last_fill), "eh_management_audit_known_terminal")
    duration = (initial.end_at - initial.start_at) // MINUTE
    return fresh_count, _Known(gross, cost, exposure_area / (initial.book.initial_capital * duration), turnover)


def _reconcile(
    comparison: ManagementComparison, day: str, symbol: str, cost_bps: int, include_reentry: bool,
    modeled_open_at: ModeledOpenReadback | None,
):
    _require(type(comparison) is ManagementComparison, "eh_management_audit_comparison")
    initial = comparison.initial
    _clock(initial.entered_at)
    _require(initial.symbol == symbol and initial.entered_at.astimezone(KST).date().isoformat() == day
             and initial.cost_bps == cost_bps and initial.start_at < initial.entered_at < initial.end_at
             and initial.last_at == initial.entered_at and not (initial.end_at - initial.start_at) % MINUTE
             and initial.attempts == 1 and initial.closed_at is None and initial.pending is None
             and not initial.book.indeterminate and len(initial.book.lots) == 1, "eh_management_audit_initial_entry")
    lot, = initial.book.lots
    basis = lot.entry_price * lot.quantity
    prior_pnl = sum((row.gross_proceeds - row.entry_basis - row.allocated_entry_cost
                     for row in initial.book.realized_exits), Fraction())
    _require(lot.symbol == symbol and lot.lot_id == initial.first_lot_id and lot.quantity == lot.original_quantity
             and not lot.half_reduced and lot.original_entry_cost == basis * Fraction(cost_bps, 10_000)
             and initial.book.cash + basis + lot.original_entry_cost == initial.book.initial_capital + prior_pnl
             and all(row.lot_id < lot.lot_id for row in initial.book.realized_exits), "eh_management_audit_initial_lot")
    read_open = None if modeled_open_at is None else partial(modeled_open_at, day, symbol)
    if read_open is not None:
        expected_open = read_open(initial.entered_at)
        _require(type(expected_open) is Fraction and expected_open > 0 and expected_open == lot.entry_price,
                 "eh_management_audit_initial_open_readback")
    arms = {
        name: _audit_arm(initial, arm.episode, arm.status, arm.events, fresh=False, read_open=read_open)
        for name, arm in (("fixed", comparison.fixed), ("dynamic", comparison.dynamic))
    }
    reentry = comparison.reentry
    if include_reentry and reentry is not None:
        if reentry.holding is not None:
            _require(reentry.holding.episode == reentry.episode and reentry.holding.status == reentry.status,
                     "eh_management_audit_fresh_holding")
        later = reentry.holding.events if reentry.holding else ()
        audited_reentry = _audit_arm(initial, reentry.episode, reentry.status,
                                    comparison.dynamic.events + reentry.events + later, fresh=True, read_open=read_open)
        _require(bool(audited_reentry[0]) == (reentry.holding is not None), "eh_management_audit_fresh_holding")
        arms["reentry"] = audited_reentry
    return basis, arms


def _totals(rows: list[tuple[Fraction, _Known]]) -> EventTotals | None:
    if not rows:
        return None
    nets = [item.net for _, item in rows]
    return EventTotals(
        sum((item.gross for _, item in rows), Fraction()), sum(nets, Fraction()),
        sum((item.cost for _, item in rows), Fraction()), sum(value < 0 for value in nets),
        -sum((value for value in nets if value < 0), Fraction()), min(nets),
        sum((10_000 * item.gross / basis for basis, item in rows), Fraction()) / len(rows),
        sum((10_000 * item.net / basis for basis, item in rows), Fraction()) / len(rows),
        sum((item.exposure for _, item in rows), Fraction()) / len(rows),
        sum((item.turnover for _, item in rows), Fraction()),
    )


def verify_management_report(
    report: ManagementReport, calendar: Mapping[str, tuple[str, ...]],
    observations: Mapping[Coordinate, ManagementObservation], *, cost_bps: int, include_reentry: bool,
    modeled_open_at: ModeledOpenReadback | None = None,
) -> None:
    """Verify arithmetic against replay evidence, optionally checking its opens.

    With no readback this is an arithmetic-only check. An independent readback
    receives the ISO session date, symbol and exact timezone-aware minute. It
    must return a positive Fraction or None when that modeled open is unknown.
    This checks recorded prices and missing opens, without redoing decisions.
    """
    _require(type(report) is ManagementReport and type(cost_bps) is int and cost_bps in (33, 43)
             and type(include_reentry) is bool and isinstance(calendar, Mapping) and 0 < len(calendar) <= 157
             and isinstance(observations, Mapping) and (modeled_open_at is None or callable(modeled_open_at)),
             "eh_management_audit_profile")
    week_by_day = {}
    for day, symbols in calendar.items():
        _require(type(day) is str and type(symbols) is tuple and 0 < len(symbols) <= 20
                 and all(type(symbol) is str and symbol for symbol in symbols) and len(set(symbols)) == len(symbols),
                 "eh_management_audit_roster")
        try:
            parsed = date.fromisoformat(day)
        except ValueError as exc:
            raise InputRefusal("eh_management_audit_session_date") from exc
        _require(parsed.isoformat() == day, "eh_management_audit_session_date")
        week_by_day[day] = (parsed - timedelta(days=parsed.weekday())).isoformat()
    keys = {(day, symbol) for day, symbols in calendar.items() for symbol in symbols}
    _require(set(observations) == keys and (report.cost_bps, report.original_sessions, report.original_symbol_days)
             == (cost_bps, len(calendar), len(keys)), "eh_management_audit_coordinates")

    names = ("fixed", "dynamic", "reentry") if include_reentry else ("fixed", "dynamic")
    statuses = Counter(dict.fromkeys(("no_event", "no_entry", "declined", "unconfirmed", "unsupported", "confirmed"), 0))
    original = Counter(week_by_day[day] for day, _ in keys)
    weeks = tuple(sorted(original))
    first_entries, missing_entries = Counter(), Counter()
    fresh_entries, support = Counter(), Counter()
    known_by_arm: dict[str, dict[Coordinate, tuple[Fraction, _Known]]] = {name: {} for name in names}
    for key, row in observations.items():
        day, symbol = key
        _require(type(row) is ManagementObservation and row.entry_status in statuses
                 and (row.entry_status == "confirmed" or row.comparison is None), "eh_management_audit_entry_status")
        statuses[row.entry_status] += 1
        week = week_by_day[day]
        if row.entry_status != "confirmed":
            if row.entry_status in ("unconfirmed", "unsupported"):
                missing_entries[row.entry_status, week] += 1
            continue
        first_entries[week] += 1
        basis, arms = ((None, {}) if row.comparison is None
                       else _reconcile(row.comparison, day, symbol, cost_bps, include_reentry, modeled_open_at))
        for name in names:
            if name not in arms:
                support[name, "unsupported", week] += 1
                continue
            fresh_count, known = arms[name]
            fresh_entries[name, week] += fresh_count
            if known is None:
                support[name, "unresolved", week] += 1
            else:
                known_by_arm[name][key] = basis, known

    _require(report.initial_status_counts == dict(statuses), "eh_management_audit_status_counts")
    expected_arms = []
    for name in names:
        weekly = tuple(WeeklyEntries(
            week, original[week], first_entries[week], fresh_entries[name, week],
            missing_entries["unconfirmed", week], missing_entries["unsupported", week],
            support[name, "unresolved", week], support[name, "unsupported", week],
        ) for week in weeks)
        expected_arms.append(ArmSummary(
            name, len(known_by_arm[name]), sum(support[name, "unresolved", week] for week in weeks),
            sum(support[name, "unsupported", week] for week in weeks), weekly,
            sum(row.confirmed_entries >= 15 for row in weekly),
        ))
    _require(report.arms == tuple(expected_arms), "eh_management_audit_arm_counts_or_frequency")

    expected_contrasts = []
    for first, second in zip(names, names[1:]):
        left, right = known_by_arm[first], known_by_arm[second]
        matched = sorted(left.keys() & right.keys())
        by_week, by_symbol = Counter(), Counter()
        deltas_week, deltas_symbol = Counter(), Counter()
        for day, symbol in matched:
            week = week_by_day[day]
            delta = right[day, symbol][1].net - left[day, symbol][1].net
            by_week[week] += 1
            by_symbol[symbol] += 1
            deltas_week[week] += delta
            deltas_symbol[symbol] += delta
        expected_contrasts.append(ManagementContrast(
            first, second, len(matched), _totals([left[key] for key in matched]), _totals([right[key] for key in matched]),
            tuple(PairContribution(week, by_week[week], deltas_week.get(week)) for week in weeks),
            tuple(PairContribution(symbol, by_symbol[symbol], deltas_symbol[symbol]) for symbol in sorted(by_symbol)),
        ))
    _require(report.contrasts == tuple(expected_contrasts), "eh_management_audit_matched_outcomes")
