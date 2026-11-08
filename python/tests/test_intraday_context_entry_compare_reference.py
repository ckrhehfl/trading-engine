"""Invented sealed preparation and original-price checks for paired EI execution."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_context_entry_compare as producer
from research.intraday_context_entry_compare import EntryArmOutcome, EventPayoff
from research.intraday_context_entry_compare_reference import verify_entry_comparison_coordinate
from research.intraday_ed_inputs import InputRefusal, MINUTE
from test_intraday_context_entry_compare import RequestedMinutes, change_minute, data, first_pair, prepare, replay, value_model


def test_reconstructs_shared_open_and_exact_cash_without_producer_calls(data, monkeypatch):
    plan, _ = prepare(data)
    result, _ = replay(plan, data[-1])
    exit_at = first_pair(result).baseline.holding.episode.closed_at
    session = change_minute(data[-1], exit_at, ohlc=(Decimal(130),) * 4)
    result, _ = replay(plan, session)
    pair = first_pair(result)
    lot, = pair.baseline.initial.book.lots
    assert pair.baseline.payoff == EventPayoff(
        lot.quantity * (130 - lot.entry_price), lot.quantity * lot.entry_price * Fraction(33, 10_000),
        lot.quantity * lot.entry_price)

    def forbidden(*args, **kwargs):
        pytest.fail("paired evaluation producer called by independent verifier")

    for name in ("prepare_entry_comparison", "replay_entry_comparison", "_execute", "fill_context_entry", "_position_replay"):
        monkeypatch.setattr(producer, name, forbidden)
    original = RequestedMinutes(session.minutes)
    audit = verify_entry_comparison_coordinate(result.coordinates[0], plan.coordinates[0], replace(session, minutes=original))
    assert audit["verified"] and audit["maximum_absolute_difference"] == 0
    assert audit["known_pairs"] >= 1 and audit["requested_opens"] == len(result.coordinates[0].modeled_opens)
    assert audit["original_minute_reads"] == len(original.reads)
    assert original.reads and set(original.reads.values()) == {1}
    assert original.reads[exit_at] == 1
    for saved, sealed in zip(result.coordinates[1:], plan.coordinates[1:], strict=True):
        assert verify_entry_comparison_coordinate(saved, sealed, None)["original_questions"] == 0


@pytest.mark.parametrize("condition", ("entry_missing", "entry_late", "exit_halt", "close_late", "known_no_fill", "session_missing"))
def test_reconstructs_no_fill_and_unknown_as_distinct_states(data, condition):
    plan, _ = prepare(data, model=value_model(data[2], Fraction(-1)))
    session = data[-1]
    result, _ = replay(plan, session)
    original = first_pair(result)
    entered = original.baseline.initial.entered_at
    exited = original.baseline.holding.episode.closed_at
    if condition == "entry_missing":
        session = change_minute(session, entered, ohlc=None, price_state="missing")
    elif condition == "entry_late":
        session = change_minute(session, entered, available_at=entered + 4 * MINUTE)
    elif condition == "exit_halt":
        session = change_minute(session, exited, ohlc=None, price_state="halt")
    elif condition == "close_late":
        # Keep the first observed holding close above the structural stop so
        # the later close is needed before a decision can complete.
        opening_price = session.minutes[entered].ohlc[0]
        session = change_minute(session, entered, ohlc=(opening_price,) * 4)
        late = entered + MINUTE
        session = change_minute(session, late, available_at=late + 4 * MINUTE)
    elif condition == "known_no_fill":
        session = change_minute(session, entered, ohlc=(Decimal(1000),) * 4)
    else:
        session = None
    result, _ = replay(plan, session)
    pair = first_pair(result)
    assert pair.selected.status == "no_entry"
    if condition == "known_no_fill":
        assert pair.baseline.status == "no_fill" and pair.net_delta == 0
    else:
        assert pair.baseline.status == "unresolved" and pair.net_delta is None
    audit = verify_entry_comparison_coordinate(result.coordinates[0], plan.coordinates[0], session)
    assert audit["verified"]


@pytest.mark.parametrize("corruption", ("drop_pair", "alter_decision", "change_payoff", "open_receipt", "swap_session"))
def test_rejects_mutation_against_external_preparation_and_original_session(data, corruption):
    plan, _ = prepare(data)
    session = data[-1]
    comparison, _ = replay(plan, session)
    result = comparison.coordinates[0]
    if corruption == "drop_pair":
        result = replace(result, outcomes=result.outcomes[:-1])
    elif corruption == "alter_decision":
        result = replace(result, prepared=replace(result.prepared, decisions=result.prepared.decisions[:-1]))
    elif corruption == "change_payoff":
        pair = first_pair(comparison)
        # A known no-fill must not replace a complete, paid trade in one arm.
        changed = replace(pair, selected=EntryArmOutcome("no_fill", "invented", EventPayoff(Fraction(), Fraction(), Fraction())))
        result = replace(result, outcomes=tuple(changed if row is pair else row for row in result.outcomes))
    elif corruption == "open_receipt":
        first, *remaining = result.modeled_opens
        result = replace(result, modeled_opens=((first[0], first[1] + 1, first[2]), *remaining))
    else:
        session = replace(session, symbol="substituted")
    with pytest.raises(InputRefusal):
        verify_entry_comparison_coordinate(result, plan.coordinates[0], session)


def test_rejects_unequal_arm_costs_even_when_selected_waits(data):
    plan, _ = prepare(data, model=value_model(data[2], Fraction(-1)))
    original = plan.coordinates[0]
    pair = next(row for row in original.decisions if row.selected is not None)
    assert pair.selected.step.action == "wait"
    changed = replace(pair, selected=replace(pair.selected, cost_bps=43))
    sealed = replace(original, decisions=tuple(changed if row is pair else row for row in original.decisions))
    result, _ = replay(replace(plan, coordinates=(sealed, *plan.coordinates[1:])), data[-1])
    with pytest.raises(InputRefusal):
        verify_entry_comparison_coordinate(result.coordinates[0], sealed, data[-1])
