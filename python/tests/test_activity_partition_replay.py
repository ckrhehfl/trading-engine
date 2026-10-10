"""Pure proof-selection/book contracts over synthetic calendars and quotes only."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import hashlib

import pytest

from research import activity_replay as module
from research.activity_accounting import FinalCashDistribution, PriceBasis
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment
from research.activity_partition_selection import PartitionSelection, selections_from_bl
from research.activity_replay import DecimalSeries, replay_synthetic
from research.activity_timing import SessionLagPolicy
from research.krx_tax_schedule import KOSPI, total_bp
from test_activity_replay import at
from test_activity_timing import absent_on, replay_inputs, run_case, target_case


SNAPSHOT, PARTITION = "a" * 64, "b" * 64


def case(lag, **changes):
    policy = SessionLagPolicy(lag, paired=True)
    days, panel, params, toy, initial, entry = target_case(policy, **changes)
    declarations = tuple(PartitionSelection(
        selection.formation_on, selection.decision_on, selection.selection_at,
        tuple(sorted(row.code for row in selection.candidates if row.activity_pass)),
        10000, PARTITION,
    ) for selection in toy)
    return policy, days, panel, params, declarations, initial, entry, toy


def target(params, formation, codes=("A", "B")):
    return min(codes, key=lambda code: hashlib.sha256(
        f"{params['seed']}:{formation}:{code}".encode()).digest())


@pytest.mark.parametrize("lag", [1, 2])
def test_partition_and_complete_toy_declarations_share_the_same_accounting(lag):
    policy, days, panel, params, selected, initial, entry, toy = case(lag)
    partition = run_case(policy, panel, params, selected, initial)
    synthetic = run_case(policy, panel, params, toy, initial)
    assert partition == synthetic
    assert partition.selections == ((days[2], (target(params, days[2]),)),)
    assert at(partition, days[entry]).lots[0].entered_on == days[entry]
    assert selected[0].source_population_count > len(panel)
    assert selected[0].source_partition_sha256 != SNAPSHOT


@pytest.mark.parametrize("lag", [1, 2])
@pytest.mark.parametrize("initial_cash", [None, D("37.25"), D(0)])
def test_cash_only_partition_replays_without_inventing_a_quote_snapshot(lag, initial_cash):
    """Both explicit arms retain numeric cash/NAV without price observations."""
    from research.activity_holding_inputs import adapt_holding_scan, holding_requirements

    policy, days, _, params, selected, _, _, _ = case(lag)
    empty = tuple(replace(row, eligible_codes=()) for row in selected)
    other = tuple(replace(row, eligible_codes=()) for row in case(3 - lag)[4])
    d1, d2 = (empty, other) if lag == 1 else (other, empty)
    requirements = holding_requirements(d1, d2, tuple(days), params, {})
    inputs = adapt_holding_scan(None, tuple(days), requirements, quote_snapshot_sha256=None,
                               read_scope_sha256="c" * 64, activity_snapshot_sha256=SNAPSHOT)
    book = None if initial_cash is None else ActivityBook(initial_cash, (), days[params["lookback"]])
    result = replay_synthetic(days, inputs.panel, params, empty,
                              dataset_sha256=inputs.quote_snapshot_sha256,
                              initial_book=book, timing_policy=policy)
    expected_cash = D(1) if initial_cash is None else initial_cash
    assert result.selections == ((days[2], ()),)
    assert tuple(book.as_of for book in result.books) == tuple(days[3:10])
    assert all(book.cash == book.nav == expected_cash and book.lots == () for book in result.books)
    assert result.closed_trades == 0
    assert dict(result.diagnostics).get("entries", 0) == 0
    assert dict(result.diagnostics)["invested_sessions"] == 0
    assert inputs.quote_snapshot_sha256 is None


@pytest.mark.parametrize("change", ["panel", "signal", "initial_lot", "event", "legacy", "legacy_timed"])
def test_none_snapshot_is_forbidden_outside_explicit_empty_partition(change):
    """No quote-free exception applies to prices, inventory, events or toy selectors."""
    policy, days, panel, params, selected, initial, entry, toy = case(1)
    declarations = tuple(replace(row, eligible_codes=()) for row in selected)
    quotes, book, events = {}, None, ()
    if change == "panel": quotes = panel
    elif change == "signal": declarations, quotes = selected, panel
    elif change == "initial_lot": book, quotes = initial, panel
    elif change == "event":
        basis = PriceBasis("HELD", days[2], D(100), D(100), SNAPSHOT, "synthetic basis")
        events = (FinalCashPayment("payment", FinalCashDistribution(
            "HELD", days[2], days[entry], days[entry + 2], D(100), basis, "synthetic payment")),)
    elif change in ("legacy", "legacy_timed"):
        if change == "legacy":
            toy = replay_inputs(SessionLagPolicy())[3]
            policy = None
        declarations = tuple(replace(row, candidates=()) for row in toy)
    with pytest.raises(ValueError, match="snapshot must be a lowercase SHA-256"):
        replay_synthetic(days, quotes, params, declarations, dataset_sha256=None,
                         initial_book=book, events=events, timing_policy=policy)


@pytest.mark.parametrize("snapshot", ["", "unknown", "A" * 64, 0])
def test_cash_only_exception_does_not_accept_malformed_non_none_snapshot(snapshot):
    """The exception denotes no snapshot with None, never a malformed identifier."""
    policy, days, _, params, selected, _, _, _ = case(1)
    empty = tuple(replace(row, eligible_codes=()) for row in selected)
    with pytest.raises(ValueError, match="snapshot must be a lowercase SHA-256"):
        replay_synthetic(days, {}, params, empty, dataset_sha256=snapshot, timing_policy=policy)


@pytest.mark.parametrize("arm,lag", [("D1", 1), ("D2", 2)])
def test_complete_synthetic_bl_partition_connects_to_books_with_a_smaller_price_panel(arm, lag):
    from test_activity_partition_selection import package

    result, population, normal, calendar, params = package()
    # Add settlement-only sessions, preserving the package's evaluation end.
    calendar = (*calendar, calendar[-1] + timedelta(days=1), calendar[-1] + timedelta(days=2))
    params = {**params, "slots": 1, "seed": 20261005,
              "commission_bps_per_side": D("1.77"), "slippage_bps_per_side": D(5)}
    declared = selections_from_bl(result, population, normal, calendar, params, arm)
    prices = (D(100),) * len(calendar)
    panel = {"000001": DecimalSeries(prices, prices, (D(10),) * len(calendar),
                                    (False,) * len(calendar), (False,) * len(calendar),
                                    (True,) * len(calendar))}
    replayed = replay_synthetic(calendar, panel, params, declared, dataset_sha256=SNAPSHOT,
                                timing_policy=SessionLagPolicy(lag, paired=True))
    assert declared[0].source_population_count == 6 and len(panel) == 1
    assert replayed.selections == ((calendar[60], ("000001",)), (calendar[90], ()))
    acquired = at(replayed, calendar[60 + lag]).lots[0]
    assert acquired.entered_on == calendar[60 + lag] and acquired.due_on == calendar[90 + lag]
    assert replayed.closed_trades == 1 and replayed.books[-1].lots == ()
    assert dict(replayed.diagnostics)["entries"] == 1
    assert all(0 < book.nav <= 1 for book in replayed.books)


def test_partition_declarations_require_explicit_timing_and_unmixed_types():
    policy, days, panel, params, selected, initial, _, toy = case(1)
    with pytest.raises(ValueError, match="explicit timing policy"):
        replay_synthetic(days, panel, params, selected, dataset_sha256=SNAPSHOT, initial_book=initial)
    for mixed in ((selected[0], toy[0]), (toy[0], selected[0]), (selected[0], object())):
        with pytest.raises(ValueError, match="unmixed"):
            run_case(policy, panel, params, mixed, initial)


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "unknown_panel_code"])
def test_every_formation_needs_one_declaration_and_every_eligible_code_needs_prices(change):
    policy, days, panel, params, selected, initial, _, _ = case(1)
    if change == "missing":
        selected = ()
    elif change == "duplicate":
        selected += selected
    elif change == "extra":
        selected += (replace(selected[0], formation_on=days[5], decision_on=days[6],
                             selection_at=policy.selection_at(days, 5)),)
    else:
        selected = (replace(selected[0], eligible_codes=("UNKNOWN",)),)
    with pytest.raises(ValueError):
        run_case(policy, panel, params, selected, initial)


@pytest.mark.parametrize("lag", [1, 2])
def test_partition_targets_precede_quotes_without_reconstructing_a_panel_population(lag, monkeypatch):
    policy, days, panel, params, selected, initial, entry, _ = case(lag)
    # This unconsumed panel series cannot provide a formation classification or
    # quote. Its presence must not redefine the independently declared source.
    unknown = (None,) * len(days)
    excluded = DecimalSeries(unknown, unknown, unknown, unknown, unknown, (True,) * len(days))
    panel["EXCLUDED"] = excluded
    original_select, original_quote = module.select_partition, module._quote
    chosen = False
    reads = []

    def select(*args, **kwargs):
        nonlocal chosen
        assert kwargs["held_codes"] == set() and kwargs["free_slots"] == 1
        result = original_select(*args, **kwargs)
        chosen = True
        return result

    def quote(series, index):
        assert series is not excluded, "unused price panel became the source population"
        if index == entry:
            assert chosen, "execution quote was read before targets were fixed"
        reads.append(index)
        return original_quote(series, index)

    def refuse_toy(*args, **kwargs):
        pytest.fail("partition evidence was sent through toy classification checks")

    monkeypatch.setattr(module, "select_partition", select)
    monkeypatch.setattr(module, "_quote", quote)
    monkeypatch.setattr(module, "_selected", refuse_toy)
    result = run_case(policy, panel, params, selected, initial)
    assert result.selections == ((days[2], (target(params, days[2]),)),)
    assert 2 not in reads


@pytest.mark.parametrize("lag", [1, 2])
@pytest.mark.parametrize("failure", ["absent", "frozen"])
def test_failed_target_gets_neither_replacement_nor_later_retry(lag, failure):
    policy, days, panel, params, selected, initial, entry, _ = case(lag)
    initial = replace(initial, cash=D(100), lots=())
    chosen = target(params, days[2])
    if failure == "absent":
        panel[chosen] = absent_on(panel[chosen], entry)
    else:
        panel[chosen] = replace(panel[chosen], frozen=tuple(i == entry for i in range(len(days))))
    result = run_case(policy, panel, params, selected, initial)
    assert result.selections == ((days[2], (chosen,)),)
    assert all(book.cash == 100 and book.lots == () for book in result.books)
    assert dict(result.diagnostics)["unfilled_entries"] == 1
    assert dict(result.diagnostics).get("entries", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
@pytest.mark.parametrize("failure", ["absent", "frozen"])
def test_failed_due_sale_keeps_its_slot_and_invalidates_the_fixed_target(lag, failure):
    policy, days, panel, params, selected, initial, entry, _ = case(lag)
    baseline = run_case(policy, panel, params, selected, initial)
    if failure == "absent":
        panel["HELD"] = absent_on(panel["HELD"], entry)
    else:
        panel["HELD"] = replace(panel["HELD"], frozen=tuple(i == entry for i in range(len(days))))
    failed = run_case(policy, panel, params, selected, initial)
    assert failed.selections == baseline.selections == ((days[2], (target(params, days[2]),)),)
    waiting = at(failed, days[entry])
    assert waiting.cash == 0 and waiting.slot_count == 1
    assert waiting.lots[0].lot_id == initial.lots[0].lot_id and waiting.lots[0].code == "HELD"
    assert waiting.lots[0].shares == initial.lots[0].shares
    assert waiting.nav == initial.nav == D(100)
    assert dict(failed.diagnostics)["unfilled_capacity_targets"] == 1
    assert dict(failed.diagnostics).get("entries", 0) == 0
    assert all(book.slot_count <= params["slots"] for book in failed.books)


@pytest.mark.parametrize("lag", [1, 2])
def test_failed_due_same_code_sale_cannot_duplicate_the_acquisition_lot(lag):
    policy, days, panel, params, selected, initial, entry, _ = case(lag, active=("HELD",))
    panel["HELD"] = absent_on(panel["HELD"], entry)
    result = run_case(policy, panel, params, selected, initial)
    assert result.selections == ((days[2], ("HELD",)),)
    assert at(result, days[entry]).lots[0].lot_id == initial.lots[0].lot_id
    assert at(result, days[entry]).slot_count == 1
    assert dict(result.diagnostics)["unfilled_held_targets"] == 1
    assert dict(result.diagnostics).get("entries", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
def test_pending_delivery_preserves_share_value_cash_and_slot_before_any_sale(lag):
    policy, days, panel, params, selected, initial, entry, _ = case(
        lag, active=("NEW",), initial_code="OLD")
    for index in range(entry, len(days)):
        panel["OLD"] = absent_on(panel["OLD"], index)
    panel["NEW"] = replace(panel["NEW"], opens=(D(200),) * len(days), closes=(D(200),) * len(days))
    basis = lambda code, day, price: PriceBasis(code, day, D(price), D(price), SNAPSHOT, "synthetic basis")
    event = CompulsoryStockExchange("exchange", D("0.5"), basis("OLD", days[entry - 1], 100),
        basis("NEW", days[entry + 2], 200), days[entry], days[entry + 2], "synthetic exchange")
    result = run_case(policy, panel, params, selected, initial, events=(event,))
    assert result.selections == ((days[2], ("NEW",)),)
    prior = initial if entry == 3 else at(result, days[entry - 1])
    for index in (entry, entry + 1):
        waiting = at(result, days[index])
        lot = waiting.lots[0]
        assert waiting.cash == 0 and waiting.slot_count == 1 and waiting.nav == initial.nav
        assert lot.lot_id == initial.lots[0].lot_id and lot.code == "NEW"
        assert lot.shares == initial.lots[0].shares / 2 and lot.mark == initial.lots[0].mark * 2
        assert lot.mark_date == prior.lots[0].mark_date and lot.available_on == days[entry + 2]
    assert at(result, days[entry + 2]).lots == ()
    assert result.books[-1].applied_event_ids == ("exchange",)
    assert dict(result.diagnostics)["unfilled_held_targets"] == 1
    assert dict(result.diagnostics).get("entries", 0) == 0 and result.closed_trades == 1


@pytest.mark.parametrize("lag", [1, 2])
def test_pending_final_payment_retains_inventory_until_cash_arrives_without_reselection(lag):
    policy, days, panel, params, selected, initial, entry, _ = case(lag)
    for index in range(3, len(days)):
        panel["HELD"] = absent_on(panel["HELD"], index)
    basis = PriceBasis("HELD", days[2], D(100), D(100), SNAPSHOT, "synthetic basis")
    payout = FinalCashPayment("payment", FinalCashDistribution(
        "HELD", days[2], days[entry], days[entry + 2], D(100), basis, "synthetic final payment"))
    late = run_case(policy, panel, params, selected, initial, events=(payout,))
    paid_today = replace(payout, distribution=replace(payout.distribution, paid_on=days[entry]))
    early = run_case(policy, panel, params, selected, initial, events=(paid_today,))
    assert late.selections == early.selections == ((days[2], (target(params, days[2]),)),)
    for book in late.books:
        if book.as_of < days[entry + 2]:
            assert book.cash == 0 and book.lots == initial.lots and book.nav == initial.nav
        else:
            assert book.cash == initial.nav == D(100) and book.lots == ()
    assert late.closed_trades == 0 and late.books[-1].applied_event_ids == ("payment",)
    assert dict(late.diagnostics)["unfilled_capacity_targets"] == 1
    assert dict(late.diagnostics).get("entries", 0) == 0
    assert dict(early.diagnostics)["entries"] == 1


@pytest.mark.parametrize("lag", [1, 2])
def test_cash_share_and_value_conservation_accounts_for_every_market_cost(lag):
    policy, days, panel, params, selected, initial, entry, _ = case(lag)
    result = run_case(policy, panel, params, selected, initial)
    acquired = at(result, days[entry]).lots[0]
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    initial_sale_cost = D(100) * (cost + D(str(total_bp(KOSPI, days[entry], days)))) / 10000
    budget = initial.nav - initial_sale_cost
    assert acquired.shares == budget / (D(100) * (1 + cost / 10000))
    entry_cost = budget - acquired.shares * 100
    sale_on = days[entry + params["holding_sessions"]]
    final_sale_cost = acquired.shares * 100 * (cost + D(str(total_bp(KOSPI, sale_on, days)))) / 10000
    final = result.books[-1]
    assert final.lots == () and result.closed_trades == 2
    assert abs(initial.nav - final.cash - initial_sale_cost - entry_cost - final_sale_cost) < D("1e-24")
    assert all(book.cash >= 0 and book.slot_count <= params["slots"] for book in result.books)


@pytest.mark.parametrize("change", ["decision", "cutoff"])
def test_partition_declaration_must_match_the_policy_dates_and_cutoff(change):
    policy, days, panel, params, selected, initial, entry, _ = case(1)
    with pytest.raises(ValueError):
        if change == "decision":
            selected = (replace(selected[0], decision_on=days[entry + 1]),)
        else:
            selected = (replace(selected[0], selection_at=selected[0].selection_at + timedelta(minutes=1)),)
        run_case(policy, panel, params, selected, initial)
