"""Independent fixed-exit cash audit for one EI evaluation coordinate.

The caller supplies a separately authenticated sealed preparation and the
corresponding original session (or authenticated absence). This checks the
execution and retained denominator. It does not authenticate those inputs or
re-estimate the forecasts that produced the sealed decisions.
"""

from dataclasses import replace
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_context_entry import PreparedContextEntry
from research.intraday_context_entry_compare import (
    EntryArmOutcome, EntryCoordinateOutcome, EntryPairOutcome, EventPayoff, PreparedEntryCoordinate,
)
from research.intraday_context_entry_training_reference import _ORDER_FRACTION, _frozen, _same
from research.intraday_ed_inputs import MINUTE, InputRefusal, SessionInputs, _session
from research.intraday_scenario_management_compare import ManagementReplay
from research.intraday_scenario_open_reference import _price_row
from research.intraday_scenario_policy import EntryBounds, Episode, Request, Step
from research.intraday_scenario_rollout import ReplayEvent


_ZERO = EventPayoff(Fraction(), Fraction(), Fraction())


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_entry_compare_reference_" + reason)


def _prepared_arm(pair, mode):
    opportunity = pair.opportunity
    preparation = getattr(pair, mode)
    unavailable = getattr(pair, mode + "_unavailable")
    if opportunity.status != "opportunity":
        _require(preparation is None, "inactive_opportunity_has_preparation")
        return EntryArmOutcome(opportunity.status, ", ".join(opportunity.reasons)), None
    if preparation is None:
        _require(type(unavailable) is str and bool(unavailable), "missing_preparation_reason")
        return EntryArmOutcome("unsupported", unavailable), None
    _require(type(preparation) is PreparedContextEntry and preparation.mode == mode,
             "preparation_mode_differs")
    _same(preparation.features, opportunity.feature)
    if preparation.status == "abstain":
        _require(preparation.step is None, "abstention_has_step")
        return EntryArmOutcome("unsupported", preparation.reason), None
    step = preparation.step
    _require(preparation.status == "prepared" and type(step) is Step, "invalid_prepared_step")
    if step.action != "enter":
        _require(step.episode.pending is None, "no_entry_has_request")
        return EntryArmOutcome("no_entry", preparation.reason, _ZERO), None

    question = preparation.features.question
    at = preparation.features.forecast_at
    book, risk, cost = preparation.book_before, preparation.risk_budget, preparation.cost_bps
    _require(type(book) is cash.CashBook and not book.lots and not book.indeterminate
             and type(risk) is Fraction and risk > 0 and type(cost) is int and cost in (33, 43)
             and at + 6 * MINUTE <= question.path.end_at <= at + 60 * MINUTE,
             "invalid_first_entry_policy")
    stop = question.path.stop
    maximum = min(question.start_close + question.path.band, question.path.target)
    bounds = EntryBounds(stop, maximum)
    rate = Fraction(cost, 10_000)
    budget = min(book.cash * _ORDER_FRACTION, book.cash / (1 + rate))
    quantity = min((risk / 2) // (maximum * (1 + rate) - stop), budget // maximum)
    _require(quantity > 0, "request_beyond_budget")
    request = Request("enter", at + MINUTE, quantity, bounds, (book.cash, book.cash))
    expected = Episode(question.anchor_id[0], at, question.path.end_at, risk, cost,
                       book, book.next_lot_id, at, attempts=1, pending=request)
    _same(step, Step(expected, "enter", "higher_value_of_entry"))
    return None, expected


def _fill(episode, target, price):
    request = episode.pending
    book = episode.book
    rate = Fraction(episode.cost_bps, 10_000)
    if price is None:
        frozen = _frozen(book, f"missing_entry_price:{book.strategy}:{episode.symbol}")
        decision = cash.Decision(frozen, "entry_price_unknown", "indeterminate")
    elif price >= target:
        decision = cash.Decision(book, "entry_at_or_above_target")
    elif not request.bounds.stop < price <= request.bounds.max_price:
        decision = cash.Decision(book, "open_outside_frozen_entry_bounds")
    else:
        quantity = request.quantity
        budget = min(book.cash * _ORDER_FRACTION, book.cash / (1 + rate))
        notional = price * quantity
        _require(notional <= budget, "open_cost_above_reserved_budget")
        cost = notional * rate
        delta = -(notional + cost)
        lot = cash.Lot(book.next_lot_id, episode.symbol, quantity, quantity, price, cost)
        filled = replace(book, cash=book.cash + delta, lots=(lot,), next_lot_id=book.next_lot_id + 1)
        decision = cash.Decision(filled, "entered", "modeled", quantity, delta, cost, budget)
    at = request.execute_at
    return replace(episode, pending=None, book=decision.book, last_at=at,
                   stop=request.bounds.stop if decision.filled else None,
                   entered_at=at if decision.filled else None), decision


def _fixed_holding(initial, read_minute, read_open):
    entered = initial.entered_at
    cap = min(entered + 30 * MINUTE, initial.end_at)
    lot, = initial.book.lots
    for offset in range(1, (cap - entered) // MINUTE):
        decision_at = entered + offset * MINUTE
        execution_at = decision_at + MINUTE
        stop_due, issue = False, None
        due_close = decision_at - 3 * MINUTE
        if due_close >= entered:
            row, issue = read_minute(due_close, decision_at)
            stop_due = issue is None and Fraction(row.ohlc[3]) <= initial.stop
        if stop_due or execution_at == cap:
            reason = "structural_close_failure" if stop_due else "fixed_episode_end"
            request = ReplayEvent(decision_at, "exit", reason, execution_at, lot.quantity)
            price = read_open(execution_at)
            if price is None:
                reason = "exit_price_unknown"
                book = _frozen(initial.book, f"missing_exit_price:{initial.book.strategy}:{initial.symbol}")
                terminal = replace(initial, book=book, last_at=execution_at)
                fill = ReplayEvent(execution_at, "exit", reason, quantity=0, fill_status="indeterminate")
                return ManagementReplay(terminal, cap, "unresolved", reason, (request, fill)), None
            notional = lot.quantity * lot.entry_price
            proceeds = lot.quantity * price
            sale = cash.RealizedExit(lot.lot_id, lot.symbol, lot.quantity, notional, lot.original_entry_cost, proceeds)
            book = replace(initial.book, cash=initial.book.cash + proceeds, lots=(),
                           realized_exits=initial.book.realized_exits + (sale,))
            terminal = replace(initial, book=book, last_at=execution_at, stop=None, entered_at=None, closed_at=execution_at)
            fill = ReplayEvent(execution_at, "exit", "exited", quantity=lot.quantity, fill_status="modeled", open_price=price)
            payoff = EventPayoff(proceeds - notional, lot.original_entry_cost, notional)
            return ManagementReplay(terminal, cap, "closed", "exited", (request, fill)), payoff
        if issue is not None:
            reason = "holding_close_unknown:" + issue
            event = ReplayEvent(decision_at, "unresolved", reason)
            terminal = replace(initial, last_at=decision_at - MINUTE)
            return ManagementReplay(terminal, cap, "unresolved", reason, (event,)), None
    raise InputRefusal("EI_entry_compare_reference_invalid_holding_cap")


def _execution(preparation, episode, read_minute, read_open):
    row, issue = read_minute(episode.start_at - 3 * MINUTE, episode.start_at)
    _require(issue is None, "causal_close_not_observed")
    _same(row, preparation.visible_minute)
    initial, fill = _fill(episode, preparation.features.question.path.target,
                          read_open(episode.pending.execute_at))
    if fill.filled:
        holding, payoff = _fixed_holding(initial, read_minute, read_open)
        return EntryArmOutcome(holding.status, holding.reason, payoff, initial, fill, holding)
    if fill.outcome == "indeterminate":
        return EntryArmOutcome("unresolved", fill.reason, initial=initial, fill=fill)
    return EntryArmOutcome("no_fill", fill.reason, _ZERO, initial, fill)


def verify_entry_comparison_coordinate(result: EntryCoordinateOutcome, original_prepared: PreparedEntryCoordinate,
                                       original_session: SessionInputs | None) -> dict:
    """Check every paired outcome against the sealed decisions and original prices."""
    _require(type(result) is EntryCoordinateOutcome and type(original_prepared) is PreparedEntryCoordinate,
             "typed_comparison_required")
    _require(type(result.outcomes) is tuple and all(type(row) is EntryPairOutcome for row in result.outcomes),
             "typed_outcomes_required")
    _same(result.prepared, original_prepared)
    _same(tuple(pair.opportunity for pair in original_prepared.decisions), original_prepared.roster.questions)
    _same(tuple(row.decisions for row in result.outcomes), original_prepared.decisions)
    if original_session is not None:
        _session(original_session)
        _same((original_session.symbol, original_session.opening, original_session.closing),
              (original_prepared.roster.symbol, original_prepared.opening, original_prepared.closing))

    minutes, opens = {}, {}

    def read_minute(at, cutoff):
        _require(original_session.opening <= at < original_session.closing, "minute_outside_original_session")
        if at not in minutes:
            minutes[at] = original_session.minutes.get(at)
        return _price_row(SessionInputs(original_session.symbol, at, at + MINUTE, {at: minutes[at]}), at, cutoff)

    def read_open(at):
        if at not in opens:
            row, issue = read_minute(at, at + 3 * MINUTE)
            opens[at] = (Fraction(row.ohlc[0]) if issue is None else None, issue)
        return opens[at][0]

    def execute(preparation, episode):
        if original_session is None:
            return EntryArmOutcome("unresolved", "evaluation_session_missing")
        return _execution(preparation, episode, read_minute, read_open)

    known_pairs = 0
    for saved, pair in zip(result.outcomes, original_prepared.decisions, strict=True):
        baseline, baseline_request = _prepared_arm(pair, "baseline")
        selected, selected_request = _prepared_arm(pair, "selected")
        if pair.baseline is not None and pair.selected is not None:
            for field in ("book_before", "risk_budget", "cost_bps", "execution_policy_sha256", "visible_minute"):
                _same(getattr(pair.baseline, field), getattr(pair.selected, field))
        if baseline_request is not None and selected_request is not None:
            _same(baseline_request, selected_request)
            baseline = selected = execute(pair.baseline, baseline_request)
        elif baseline_request is not None:
            baseline = execute(pair.baseline, baseline_request)
        elif selected_request is not None:
            selected = execute(pair.selected, selected_request)
        _same(saved.baseline, baseline)
        _same(saved.selected, selected)
        known_pairs += saved.baseline.payoff is not None and saved.selected.payoff is not None
    _same(result.modeled_opens, tuple((at, *observation) for at, observation in sorted(opens.items())))
    return {"verified": True, "original_questions": len(original_prepared.decisions), "known_pairs": known_pairs,
            "original_minute_reads": len(minutes), "requested_opens": len(opens), "maximum_absolute_difference": 0}
