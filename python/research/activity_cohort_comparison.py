"""Pure independent formation cohorts and ex-post assigned-capital attribution.

Every cohort starts with cash NAV 1 and opens only its fixed formation targets.
The existing twenty-slot replay owns fills, costs, 126-session exits and pending
rights. No cohort excludes another cohort's holdings. The aggregate denominator
uses all declared target counts retrospectively; its curve is a descriptive
capital attribution, never a deployable continuous strategy or compounded book.
Callers separately establish evidence pins and reviewed action/no-action scope.
Internal consistency here certifies no source, event coverage or price basis.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
import math
from statistics import median, stdev
from typing import TYPE_CHECKING

from research.activity_accounting import sum_decimal_values
from research.activity_book import ActivityBook, CompulsorySpinOff, CompulsoryStockExchange, FinalCashPayment
from research.activity_cohort_selection import CohortSelection, CONTROL_IDS, SLOTS
from research.activity_cohort_return_attribution import (
    ATTRIBUTION_KEY, aggregate_known_issuer_returns, attribute_known_issuer_returns,
    require_supported_attribution_actions,
)
from research.activity_cohort_return_policy import policy_report
from research.activity_holding_inputs import HoldingInputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import encoded, require, strict_json
from research.activity_quote_extension import CompositeHoldingInputs, _same, _validated, extend_holding_quotes
from research.activity_raw_quote_view import (
    RawEpisodeQuoteInputs, validate_raw_quote_view, _prepare_raw_quote_context, _immutable_fields, _unchanged_fields,
)
from research.activity_replay import _check_event_quotes, _event_date, _quote, replay_synthetic, _replay_synthetic_from_context
from research.activity_timing import SessionLagPolicy

if TYPE_CHECKING:
    from research.activity_cohort_connection import CohortScopeBinding


ARMS = ("D1", "D2")
GROUPS = ("ACTIVITY", *CONTROL_IDS)
ONE = Decimal(1)
ZERO = Decimal(0)
EVENTS = (CompulsorySpinOff, CompulsoryStockExchange, FinalCashPayment)


def _difference(left: Decimal, right: Decimal) -> Decimal:
    return sum_decimal_values((left, right.copy_negate()))


def _key(row: CohortSelection) -> tuple[str, str, date]:
    return row.arm, row.group_id, row.formation_on


def _declarations(rows: tuple[CohortSelection, ...], calendar: tuple[date, ...], parameters: dict) -> dict:
    require(type(rows) is tuple and bool(rows) and all(type(row) is CohortSelection for row in rows),
        "immutable cohort selections required")
    end = calendar.index(date.fromisoformat(parameters["end"]))
    require(end + 2 < len(calendar), "two complete settlement sessions after evaluation end required")
    indices = SessionLagPolicy(1, paired=True).formation_indices(calendar,
        lookback=parameters["lookback"], holding_sessions=parameters["holding_sessions"], end_index=end)
    require(bool(indices), "nonempty original paired formation grid required")
    keyed = {}
    for row in rows:
        # Reapply the immutable selection boundary, including all fingerprints,
        # instead of trusting a dataclass fabricated with object.__setattr__.
        require(_same(row, replace(row)), "cohort selection projection mismatch")
        key = _key(row)
        require(key not in keyed, "duplicate cohort declaration")
        keyed[key] = row
    expected = {(arm, group, calendar[index]) for arm in ARMS for group in GROUPS for index in indices}
    require(set(keyed) == expected, "all groups and arms require the complete original formation grid")
    for index in indices:
        reference = keyed["D1", "ACTIVITY", calendar[index]]
        for arm in ARMS:
            policy = SessionLagPolicy(int(arm[-1]), paired=True)
            activity = keyed[arm, "ACTIVITY", calendar[index]]
            require(activity.source_population == reference.source_population
                and activity.population_identity == reference.population_identity,
                "paired cohorts must retain the same complete source population")
            for group in GROUPS:
                row = keyed[arm, group, calendar[index]]
                require(row.decision_on == policy.execution_on(calendar, index)
                    and row.selection_at == policy.selection_at(calendar, index),
                    "cohort decision/cutoff differs from explicit D1/D2 schedule")
                require(row.activity_targets == activity.activity_targets
                    and row.activity_targets_sha256 == activity.activity_targets_sha256
                    and row.source_population == activity.source_population
                    and row.population_identity == activity.population_identity,
                    "cohort original targets, assigned count or source fingerprints changed")
    return keyed


def _quotes(holding_inputs, raw_quote_view):
    if raw_quote_view is not None:
        fresh = validate_raw_quote_view(raw_quote_view)
        require(_same(fresh.base, holding_inputs), "raw view must preserve the complete supplied holding base")
        return fresh
    if type(holding_inputs) is HoldingInputs:
        _validated(holding_inputs)
    elif type(holding_inputs) is CompositeHoldingInputs:
        require(_same(holding_inputs, extend_holding_quotes(holding_inputs.original, holding_inputs.extension)),
            "complete composite holding projection mismatch")
    else:
        raise ValueError("existing immutable HoldingInputs or CompositeHoldingInputs required")
    return holding_inputs


def _events(events: tuple, quotes, parameters: dict, raw_quote_view) -> tuple:
    require(type(events) is tuple and all(type(event) in EVENTS for event in events),
        "immutable existing compulsory event types required")
    ordered = tuple(sorted(events, key=_event_date))
    require(len({event.event_id for event in ordered}) == len(ordered), "unique event ids required")
    # The reviewed decoder permits one full-issue action per exact issue.
    # This code-only boundary conservatively preserves that invariant; exact
    # issue identity and the rejection of code reuse remain caller obligations.
    old_codes = tuple(event.distribution.code if type(event) is FinalCashPayment else event.old_basis.code
        for event in ordered)
    require(len(set(old_codes)) == len(old_codes), "conflicting full-issue events for the same issue code")
    raw_rows = {} if raw_quote_view is None else {
        (row.code, date.fromisoformat(row.requested_bas_dd)): row for row in raw_quote_view.raw_observations}
    raw_codes = {code for code, _ in raw_rows}
    for event in ordered:
        replace(event)
        bases = ((event.old_basis, event.retained_basis, event.new_basis) if type(event) is CompulsorySpinOff
            else (event.old_basis, event.new_basis) if type(event) is CompulsoryStockExchange
            else (event.distribution.basis,))
        if type(event) is FinalCashPayment:
            replace(event.distribution)
        require(_event_date(event) > quotes.calendar[parameters["lookback"]],
            "events must be future to the initial cohort cutoff")
        for basis in bases:
            replace(basis)
            require(basis.dataset_sha256 == quotes.quote_snapshot_sha256,
                "events and quotes must retain the same snapshot")
            if basis.code in raw_codes:
                row = raw_rows.get((basis.code, basis.session))
                require(row is not None and row.status == "observed" and row.state in ("observed", "locked")
                    and row.close is not None and basis.raw_close == row.close == basis.adjusted_close,
                    "raw event bases must retain resolved raw/raw quote coordinates")
        if type(event) in (CompulsoryStockExchange, CompulsorySpinOff):
            require(event.new_basis.code in quotes.panel, "every possible successor needs a supplied quote series")
        if type(event) is CompulsorySpinOff:
            codes = {event.old_basis.code, event.new_basis.code}
            for other in ordered:
                if other is event or _event_date(other) != event.effective_on:
                    continue
                other_codes = ({other.old_basis.code, other.new_basis.code} if type(other) in
                    (CompulsoryStockExchange, CompulsorySpinOff) else {other.distribution.code})
                require(not codes & other_codes, "same-day spin-off event chains are ambiguous")
    # Preserve activity_reviewed_actions.reviewed_actions' complete graph
    # delivery checks independently of input order and event lifetime overlap.
    # This pure boundary has codes only; the caller must reject issue-code reuse
    # and establish exact ISIN/evidence joins before supplying these events.
    for parent in ordered:
        if type(parent) not in (CompulsoryStockExchange, CompulsorySpinOff):
            continue
        delivered = parent.new_available_on if type(parent) is CompulsorySpinOff else parent.available_on
        for child in ordered:
            code = child.distribution.code if type(child) is FinalCashPayment else child.old_basis.code
            if code != parent.new_basis.code:
                continue
            if type(child) is CompulsorySpinOff:
                require(delivered <= child.last_eligible_entry_on and delivered < child.effective_on,
                    "unsupported spin-off eligibility before parent delivery")
            elif type(child) is CompulsoryStockExchange:
                require(delivered < child.effective_on, "unsupported re-exchange of a pending entitlement")
            else:
                require(delivered <= child.distribution.record_on,
                    "unsupported final payout eligibility before parent delivery")
                # A final payment and the creation of its successor inventory
                # on the same day otherwise depend on stable input tie order.
                require(_event_date(parent) != child.distribution.paid_on,
                    "unsupported same-day final payout after compulsory successor creation")
    end = quotes.calendar.index(date.fromisoformat(parameters["end"]))
    for index in range(parameters["lookback"], end + 1):
        _check_event_quotes(ordered, quotes.panel, session=quotes.calendar[index], index=index)
    return ordered


def _coverage(rows: tuple[CohortSelection, ...], quotes, events: tuple, parameters: dict, *,
              origin_bounds: tuple[tuple[str, date, str, date, date], ...] | None = None) -> None:
    """Bound every potential lot without using prices to choose any target.

    Failed sales may retain an investment through end. Compulsory transitions
    change its required issue coordinates; unavailable descendants retain carry
    without a usable quote before delivery. This is input coverage arithmetic,
    not evidence that an event happens or that an actual lot exists.
    """
    calendar, panel = quotes.calendar, quotes.panel
    end = date.fromisoformat(parameters["end"])
    requested = set(quotes.requested_coordinates)
    needed = set()
    raw_codes = set() if type(quotes) is not RawEpisodeQuoteInputs else {row.code for row in quotes.raw_observations}
    bounds = {}
    if origin_bounds is not None:
        require(type(origin_bounds) is tuple and all(type(row) is tuple and len(row) == 5
            and type(row[0]) is str and type(row[1]) is date and type(row[2]) is str
            and type(row[3]) is date and type(row[4]) is date and row[3] <= row[4] <= end
            for row in origin_bounds), "immutable complete conditional origin bounds required")
        bounds = {row[:4]: row[4] for row in origin_bounds}
        expected = {(row.arm, row.formation_on, code, row.decision_on) for row in rows for code in row.chosen_codes}
        require(len(bounds) == len(origin_bounds) and bounds.keys() == expected,
            "conditional bounds must retain every chosen cohort origin")

    def interval(code, start, available, stop, pending_raw=False, endpoint=end):
        require(code in panel, "every chosen or successor issue requires a supplied quote series")
        # The existing raw replay bypasses quotes only for event-created
        # spin-off carry. A stock-exchange successor still crosses that raw
        # boundary while unavailable and therefore needs explicit raw rows.
        first = start if code in raw_codes and not pending_raw else max(start, available)
        needed.update((code, day) for day in calendar if first <= day <= endpoint and day < stop)

    for row in rows:
        # Keep every original investment separate even when two origins later
        # share a code. A transition must not erase an independently chosen lot.
        possible = [(code, row.decision_on, row.decision_on, False, False,
            bounds.get((row.arm, row.formation_on, code, row.decision_on), end)) for code in row.chosen_codes]
        for event in events:
            effective = _event_date(event)
            code = event.distribution.code if type(event) is FinalCashPayment else event.old_basis.code
            if effective > end:
                continue
            following = []
            for current_code, start, available, from_event, pending_raw, endpoint in possible:
                # Existing events retain supplied same-day order. Ordinary
                # entries on that day occur after all events.
                if (current_code != code or effective < start or effective > endpoint or (effective == start and not from_event)
                        or (type(event) is CompulsorySpinOff
                        and row.decision_on > event.last_eligible_entry_on)):
                    following.append((current_code, start, available, from_event, pending_raw, endpoint))
                    continue
                interval(code, start, available, effective, pending_raw, endpoint)
                if type(event) is CompulsoryStockExchange:
                    following.append((event.new_basis.code, effective, event.available_on, True, False, endpoint))
                elif type(event) is CompulsorySpinOff:
                    following.extend(((code, effective, event.retained_available_on, True, True, endpoint),
                        (event.new_basis.code, effective, event.new_available_on, True, True, endpoint)))
                    due = calendar[calendar.index(row.decision_on) + parameters["holding_sessions"]]
                    if event.last_eligible_entry_on < due < event.effective_on:
                        for day in calendar:
                            if due <= day <= endpoint and day < event.effective_on:
                                opening, _, frozen, _ = _quote(panel[code], calendar.index(day))
                                require(not opening or frozen,
                                    "unsupported pre-effective spin-off sale would detach pending rights")
            possible = following
        for code, start, available, _, pending_raw, endpoint in possible:
            interval(code, start, available, date.max, pending_raw, endpoint)
    require(needed <= requested, "complete chosen and possible descendant holding-input coverage required")
    positions = {day: index for index, day in enumerate(calendar)}
    for code, day in sorted(needed):
        _quote(panel[code], positions[day])


def _path_metrics(days: tuple[date, ...], navs: tuple[Decimal, ...] | None) -> dict:
    if navs is None:
        return {"path": tuple({"date": day, "nav": None, "session_return": None} for day in days),
            "total_return": None, "maximum_drawdown": None, "session_return_sd": None,
            "session_return_count": 0}
    previous = peak = ONE
    maximum_drawdown, returns, path = ZERO, [], []
    for day, nav in zip(days, navs, strict=True):
        require(nav.is_finite() and nav >= 0 and (previous != 0 or nav == 0),
            "finite nonnegative normalized NAV with absorbing zero required")
        change = ZERO if previous == 0 else nav / previous - ONE
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, ONE - nav / peak)
        returns.append(float(change))
        path.append({"date": day, "nav": nav, "session_return": change})
        previous = nav
    require(all(math.isfinite(value) for value in returns), "finite descriptive daily returns required")
    sd = stdev(returns) if len(returns) >= 2 else None
    require(sd is None or math.isfinite(sd), "finite descriptive session SD required")
    return {"path": tuple(path), "total_return": _difference(previous, ONE),
        "maximum_drawdown": maximum_drawdown, "session_return_sd": sd, "session_return_count": len(returns)}


def _summary(results: tuple[dict, ...], days: tuple[date, ...], *, return_policy_json: bytes | None = None) -> dict:
    assigned = sum_decimal_values(tuple(row["assigned_capital"] for row in results))
    histories = tuple(row["replay"].navs for row in results)
    pnls = tuple(sum_decimal_values(tuple(_difference(history[index], ONE)
        for history in histories)) for index in range(len(days)))
    normalized = None if assigned == 0 else tuple(sum_decimal_values((ONE, pnl / assigned)) for pnl in pnls)
    inventory = tuple(sum_decimal_values(tuple(row["exposure"]["path"][index]["marked_inventory_value"]
        for row in results)) for index in range(len(days)))
    navs = tuple(sum_decimal_values(tuple(history[index] for history in histories)) for index in range(len(days)))
    result = {"cohort_count": len(results), "positive_assigned_cohort_count": sum(row["assigned_capital"] > 0 for row in results),
        "assigned_capital": assigned, "filled_capital": sum_decimal_values(tuple(row["filled_capital"] for row in results)),
        "filled_principal": sum_decimal_values(tuple(row["filled_principal"] for row in results)),
        "entry_cost_amount": sum_decimal_values(tuple(row["entry_cost_amount"] for row in results)),
        "net_pnl": pnls[-1], "filled_entries": sum(row["filled_entries"] for row in results),
        "ordinary_closed_trades": sum(row["ordinary_closed_trades"] for row in results),
        "terminal_open_lots": sum(row["terminal_open_lots"] for row in results),
        "terminal_open_investments": sum(row["terminal_open_investments"] for row in results),
        "metrics": _path_metrics(days, normalized), "exposure": _exposure(days, inventory, navs, assigned, pnls,
            return_policy_json=return_policy_json)}
    policy = policy_report(return_policy_json)
    if policy:
        result.update(policy)
        result[ATTRIBUTION_KEY] = aggregate_known_issuer_returns(results, assigned_capital=assigned, total_pnl=pnls[-1],
            return_policy_json=return_policy_json)
    return result


def _exposure(days, inventory, navs, assigned: Decimal, pnls, *, return_policy_json: bytes | None = None) -> dict:
    """Marked noncash values include pending rights, never executable liquidity."""
    path = []
    for day, value, nav, pnl in zip(days, inventory, navs, pnls, strict=True):
        attributed_nav = sum_decimal_values((assigned, pnl))
        require(attributed_nav >= 0, "nonnegative funded assigned-capital NAV required")
        path.append({"date": day, "marked_inventory_value": value, "capital_attribution_nav": attributed_nav,
            "marked_inventory_nav_weight": None if attributed_nav == 0 else value / attributed_nav,
            "marked_inventory_assigned_capital_weight": None if assigned == 0 else value / assigned,
            "auxiliary_raw_books_nav_weight": None if nav == 0 else value / nav})
    count = Decimal(len(days))
    def mean(field):
        defined = tuple(row[field] for row in path if row[field] is not None)
        return {"mean": None if not defined else sum_decimal_values(defined) / Decimal(len(defined)),
            "defined_session_count": len(defined), "undefined_session_count": len(path) - len(defined)}
    nav_weight = mean("marked_inventory_nav_weight")
    assigned_weight = mean("marked_inventory_assigned_capital_weight")
    result = {"path": path,
        "mean_marked_inventory_value": sum_decimal_values(inventory) / count,
        "mean_marked_inventory_nav_weight": nav_weight["mean"], "nav_weight_session_counts": {
            key: value for key, value in nav_weight.items() if key != "mean"},
        "mean_marked_inventory_assigned_capital_weight": assigned_weight["mean"], "assigned_weight_session_counts": {
            key: value for key, value in assigned_weight.items() if key != "mean"},
        "auxiliary_raw_books_nav_weight": mean("auxiliary_raw_books_nav_weight"),
        "nav_weight_denominator": "assigned_capital + sum(cohort_NAV - 1); excludes unassigned synthetic cash",
        "pending_rights_included": True, "executable_liquidity_certified": False}
    policy = policy_report(return_policy_json)
    if policy:
        result.update(pending_rights_scope="modeled supported LG/LX compulsory components only",
            automatic_subscription_rights_included=False)
        if "automatic_stock_dividend_assets" in policy:
            result["automatic_stock_dividend_assets_included"] = False
    return result


def _deltas(activity: dict, controls: dict) -> dict:
    original = activity["metrics"]["total_return"]
    values = {group: None if original is None or row["metrics"]["total_return"] is None else
        _difference(original, row["metrics"]["total_return"]) for group, row in controls.items()}
    known = tuple(value for value in values.values() if value is not None)
    return {"activity_minus_control": values, "median": median(known) if known else None,
        "minimum": min(known) if known else None, "maximum": max(known) if known else None,
        "positive_count": sum(value > 0 for value in known), "ties": sum(value == 0 for value in known),
        "known_control_count": len(known)}


@dataclass(frozen=True)
class _PreparedReplay:
    declarations: tuple
    quotes: HoldingInputs | CompositeHoldingInputs | RawEpisodeQuoteInputs
    ordered_events: tuple
    binding: CohortScopeBinding | None
    raw_quote_view: RawEpisodeQuoteInputs | None
    parameters_json: bytes
    keyed: tuple
    calendar: tuple[date, ...]
    days: tuple[date, ...]
    initial: ActivityBook
    raw_context: tuple | None = None


def _prepare_cohort_replay(
    declarations: tuple[CohortSelection, ...], holding_inputs: HoldingInputs | CompositeHoldingInputs,
    events: tuple, *, parameters: dict, raw_quote_view: RawEpisodeQuoteInputs | None = None,
    scope_binding: CohortScopeBinding | None = None,
) -> tuple:
    """Validate one invocation and issue a closure for that exact frozen context."""
    require(type(parameters) is dict and encoded(parameters) == encoded(PARAMETERS),
        "unchanged canonical parameter bytes required")
    raw_context = None
    if raw_quote_view is None:
        quotes = _quotes(holding_inputs, None)
    else:
        raw_context = _prepare_raw_quote_context(raw_quote_view)
        quotes, _ = raw_context[1](raw_context[0])
        require(_same(quotes.base, holding_inputs), "raw view must preserve the complete supplied holding base")
    calendar = quotes.calendar
    require(type(calendar) is tuple, "immutable complete holding calendar required")
    SessionLagPolicy._calendar(calendar)
    require(date.fromisoformat(parameters["end"]) in calendar, "evaluation end must be in the complete calendar")
    keyed = _declarations(declarations, calendar, parameters)
    ordered_events = _events(events, quotes, parameters, raw_quote_view)
    binding = None
    if scope_binding is not None:
        from research.activity_cohort_connection import _validate_cohort_scope
        binding = _validate_cohort_scope(scope_binding, declarations, quotes, ordered_events,
            parameters=parameters, _raw_context=raw_context)
    else:
        _coverage(declarations, quotes, ordered_events, parameters)
    if binding is not None and policy_report(binding.return_policy_json):
        require_supported_attribution_actions(ordered_events, return_policy_json=binding.return_policy_json)
    end = calendar.index(date.fromisoformat(parameters["end"]))
    prepared = _PreparedReplay(declarations, quotes, ordered_events, binding, quotes if raw_context else None, encoded(parameters),
        tuple(keyed.items()), calendar, calendar[parameters["lookback"] + 1:end + 1],
        ActivityBook(ONE, (), calendar[parameters["lookback"]]), raw_context)
    additional = (prepared, holding_inputs, raw_quote_view, scope_binding)
    sealed = (_immutable_fields(additional) if raw_context is None else
        raw_context[1](raw_context[0], additional_seal=additional))

    def execute(candidate, cohort_keys=None):
        require(type(candidate) is _PreparedReplay and candidate is prepared,
            "only this invocation's issued prepared replay is accepted")
        if raw_context is not None:
            raw_context[1](raw_context[0])
        require(encoded(parameters) == prepared.parameters_json, "prepared replay source parameters changed")
        require(_unchanged_fields(sealed), "prepared replay or immutable input changed")
        return _execute_prepared_cohorts(prepared, cohort_keys)

    return prepared, execute


def _execute_prepared_cohorts(prepared: _PreparedReplay, cohort_keys: tuple | None) -> dict:
    """The issuing closure owns validation; this is the unchanged book loop."""
    declarations, quotes, ordered_events = prepared.declarations, prepared.quotes, prepared.ordered_events
    binding, raw_quote_view = prepared.binding, prepared.raw_quote_view
    return_policy_json = None if binding is None else binding.return_policy_json
    policy = policy_report(return_policy_json)
    parameters, keyed = strict_json(prepared.parameters_json), dict(prepared.keyed)
    calendar, days, initial = prepared.calendar, prepared.days, prepared.initial
    if binding is not None:
        from research.activity_cohort_connection import verify_cohort_inventory
    if cohort_keys is None:
        selected_keys = tuple(sorted(keyed))
    else:
        require(type(cohort_keys) is tuple and bool(cohort_keys)
            and all(type(key) is tuple and len(key) == 3 and key in keyed for key in cohort_keys)
            and len(set(cohort_keys)) == len(cohort_keys), "unique known immutable cohort keys required")
        selected_keys = tuple(sorted(cohort_keys))
    results = []
    engine = replay_synthetic if prepared.raw_context is None else lambda *args, **kwargs: _replay_synthetic_from_context(
        *prepared.raw_context, *args, **kwargs)
    with localcontext() as context:
        context.prec = 28
        for key in selected_keys:
            row = keyed[key]
            schedule = tuple(PartitionSelection(item.formation_on, item.decision_on, item.selection_at,
                row.chosen_codes if item.formation_on == row.formation_on else (),
                item.source_population_count, item.source_partition_sha256)
                for item in sorted((item for item in declarations if item.arm == row.arm
                    and item.group_id == row.group_id), key=lambda item: item.formation_on))
            result = engine(calendar, quotes.panel, parameters, schedule,
                dataset_sha256=quotes.quote_snapshot_sha256, events=ordered_events, initial_book=initial,
                timing_policy=SessionLagPolicy(int(row.arm[-1]), paired=True), raw_quote_view=raw_quote_view)
            if binding is not None:
                verify_cohort_inventory(binding, row.arm, result)
            require(tuple(book.as_of for book in result.books) == days, "complete common cohort session path required")
            require(all(set(codes) == (set(row.chosen_codes) if formation == row.formation_on else set())
                for formation, codes in result.selections), "fixed cohort targets changed during replay")
            entries = dict(result.diagnostics).get("entries", 0)
            require(entries + dict(result.diagnostics).get("unfilled_entries", 0) == len(row.chosen_codes),
                "all fixed chosen targets must be filled or explicitly unfilled")
            assigned, filled = Decimal(row.target_ceiling) / SLOTS, Decimal(entries) / SLOTS
            entry_book = next(book for book in result.books if book.as_of == row.decision_on)
            entry_index = calendar.index(row.decision_on)
            principal = sum_decimal_values(tuple(lot.shares * quotes.panel[lot.code].opens[entry_index]
                for lot in entry_book.lots))
            inventory = tuple(sum_decimal_values(tuple(lot.marked_value for lot in book.lots)) for book in result.books)
            normalized = None if assigned == 0 else tuple(sum_decimal_values((ONE,
                _difference(nav, ONE) / assigned)) for nav in result.navs)
            results.append({"key": key, "arm": row.arm, "group_id": row.group_id,
                "formation_on": row.formation_on, "decision_on": row.decision_on, "selection_at": row.selection_at,
                "original_activity_targets": row.activity_targets, "chosen_codes": row.chosen_codes,
                "target_ceiling": row.target_ceiling, "eligible_shortfall": row.eligible_shortfall,
                "assigned_capital": assigned, "filled_capital": filled, "filled_entries": entries,
                "filled_principal": principal, "entry_cost_amount": _difference(filled, principal),
                "ordinary_closed_trades": result.closed_trades, "terminal_open_lots": len(result.books[-1].lots),
                "terminal_open_investments": result.books[-1].slot_count,
                "raw_metrics": _path_metrics(days, result.navs), "metrics": _path_metrics(days, normalized),
                "exposure": _exposure(days, inventory, result.navs, assigned,
                    tuple(_difference(nav, ONE) for nav in result.navs), return_policy_json=return_policy_json),
                "source_population_sha256": row.source_population_sha256,
                "source_partition_sha256": row.source_partition_sha256, "replay": result})
            if policy:
                results[-1].update(policy)
                results[-1][ATTRIBUTION_KEY] = attribute_known_issuer_returns(result, row, quotes, parameters,
                    events=ordered_events, return_policy_json=return_policy_json)
        aggregates, comparisons = {}, {}
        for arm in ARMS:
            arm_results = tuple(row for row in results if row["arm"] == arm)
            aggregates[arm] = {group: _summary(tuple(row for row in arm_results if row["group_id"] == group), days,
                return_policy_json=return_policy_json)
                for group in GROUPS if any(row["group_id"] == group for row in arm_results)}
            group_formations = {group: {row["formation_on"] for row in arm_results if row["group_id"] == group}
                for group in GROUPS}
            if not group_formations["ACTIVITY"] or any(group_formations[group] != group_formations["ACTIVITY"] for group in GROUPS):
                comparisons[arm] = None
                continue
            per_formation = {}
            for formation in sorted(group_formations["ACTIVITY"]):
                current = {row["group_id"]: row for row in arm_results if row["formation_on"] == formation}
                per_formation[formation.isoformat()] = _deltas(current["ACTIVITY"], {group: current[group] for group in CONTROL_IDS})
            comparisons[arm] = {**_deltas(aggregates[arm]["ACTIVITY"],
                {group: aggregates[arm][group] for group in CONTROL_IDS}), "per_formation": per_formation}
    return {"schema": "activity-equal-capital-cohorts-v1", "status": "pure_ex_post_cohort_diagnostic",
        "initial_nav_per_cohort": ONE, "parameters": parameters, "calendar": calendar,
        "activity_snapshot_sha256": quotes.activity_snapshot_sha256,
        "quote_snapshot_sha256": quotes.quote_snapshot_sha256,
        "cohorts": tuple(results), "aggregates": aggregates, "comparisons": comparisons,
        "cohort_evaluation_counts": {arm: sum(row["arm"] == arm for row in results) for arm in ARMS},
        "aggregation": "ex-post sum(cohort_NAV-1)/sum(original_activity_target_count/20); not a deployable continuous strategy",
        "control_interpretation": "five fixed controls share market sessions; not independent market samples; no p-values",
        "future_counts_used_only_for_ex_post_attribution": True,
        "conditional_exit_bounds_consumed": binding is not None,
        "conditional_scope_binding_sha256": None if binding is None else binding.binding_sha256,
        "actual_input_reuse_readiness_established": False,
        "comparison_power_prerequisite_met": False, "power_design_passed": False,
        "source_truth_certified": False, "event_coverage_certified": False, "price_basis_certified": False,
        "historical_vintage_certified": False, "promotion_allowed": False, "actual_study_completed": False, **policy}


def replay_equal_capital_cohorts(
    declarations: tuple[CohortSelection, ...], holding_inputs: HoldingInputs | CompositeHoldingInputs,
    events: tuple, *, parameters: dict, raw_quote_view: RawEpisodeQuoteInputs | None = None,
    cohort_keys: tuple[tuple[str, str, date], ...] | None = None,
    scope_binding: CohortScopeBinding | None = None,
) -> dict:
    """Validate the full family, then execute the unchanged selected cohort loop.

    Optional keys restrict computation only. Each cohort keeps every original
    formation and uses its fixed targets only at its one active formation.
    Full quote, declaration, event and conditional proof validation precedes
    books. Every book retains its own inventory-bound checks. Decimal precision,
    costs, assigned budgets, missing fills and complete outputs are unchanged.
    The private context and executor are issued afresh for every invocation.
    """
    prepared, execute = _prepare_cohort_replay(declarations, holding_inputs, events,
        parameters=parameters, raw_quote_view=raw_quote_view, scope_binding=scope_binding)
    return execute(prepared, cohort_keys)
