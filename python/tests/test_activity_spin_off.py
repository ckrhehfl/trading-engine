"""Synthetic compulsory two-component arithmetic, carry and group lifecycle."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D, getcontext, localcontext
import json

import pytest

from research.activity_accounting import (
    FinalCashDistribution, Lot, PriceBasis, observable_mark, successor_entitlement, sum_decimal_values,
)
from research.activity_book import ActivityBook, CompulsorySpinOff, FinalCashPayment


SNAPSHOT = "a" * 64


def day(number):
    return date(2025, 1, number)


def basis(code, number, raw, adjusted):
    return PriceBasis(code, day(number), D(raw), D(adjusted), SNAPSHOT, "synthetic same-session unit bridge")


def original():
    return Lot("entry-1", "OLD", D(100), D(50), day(5), day(2), day(10), day(2), SNAPSHOT)


def event():
    return CompulsorySpinOff(
        "split-1", D("0.6"), D("0.4"), basis("OLD", 5, 100, 50),
        basis("OLD", 9, 200, 50), basis("NEW", 15, 250, 50),
        day(4), day(5), day(6), day(9), day(15), D("0.7"),
        "synthetic registered carry allocation convention", "assumed",
        "synthetic inspected final compulsory terms",
    )


def book(*lots):
    return ActivityBook(D(123), tuple(lots) or (original(),), day(5))


def test_raw_ratios_bridge_all_three_bases_and_partition_old_value_once():
    before, terms = book(), event()
    after = before.apply_spin_off(terms, session=day(6))
    retained, allotted = after.lots
    # 100 adjusted old = 50 raw; 30 retained raw / .25 = 120 adjusted;
    # 20 new raw / .20 = 100 adjusted. Ratios are not carry-value weights.
    assert (retained.shares, allotted.shares) == (D(120), D(100))
    assert (retained.marked_value, allotted.marked_value) == (D(3500), D(1500))
    assert retained.marked_value + allotted.marked_value == original().marked_value
    assert retained.code == "OLD" and allotted.code == "NEW"
    assert retained.lot_id == original().lot_id and allotted.lot_id != retained.lot_id
    assert retained.investment_id == allotted.investment_id
    assert retained.investment_id is not None
    assert after.slot_count == before.slot_count == 1
    assert after.nav == before.nav == D(5123) and after.cash == before.cash
    assert after.applied_event_ids == (terms.event_id,)
    for component in after.lots:
        assert component.entered_on == original().entered_on and component.due_on == original().due_on
        assert component.mark_date == day(5) and component.dataset_sha256 == SNAPSHOT
    assert retained.available_on == day(9) and allotted.available_on == day(15)
    assert before == book()


def test_repeating_per_share_mark_cannot_lose_exact_pending_value():
    old = replace(original(), shares=D(1), mark=D(1))
    terms = replace(event(), retained_ratio=D(3), new_ratio=D(7), retained_carry_weight=D("0.5"),
                    old_basis=basis("OLD", 5, 1, 1), retained_basis=basis("OLD", 9, 1, 1),
                    new_basis=basis("NEW", 15, 1, 1))
    after = book(old).apply_spin_off(terms, session=day(6))
    retained, allotted = after.lots
    assert (retained.shares, allotted.shares) == (D(3), D(7))
    assert retained.shares * retained.mark != retained.marked_value
    assert retained.marked_value == allotted.marked_value == D("0.5")
    assert after.nav == book(old).nav == D(124)


def test_extreme_valid_weight_retains_a_positive_exact_residual():
    terms = replace(event(), retained_carry_weight=D("0." + "9" * 40))
    result = book().apply_spin_off(terms, session=day(6))
    assert result.lots[1].carried_value == D("5e-37")
    assert result.nav == book().nav


@pytest.mark.parametrize("precision,mark", [(28, "5.000000000000000000000000001"), (7, "5.000001")])
def test_component_sum_keeps_boundary_digit_without_changing_global_context(precision, mark):
    original_precision = getcontext().prec
    with localcontext() as context:
        context.prec = precision
        price = D(mark)
        old = replace(original(), shares=D(1), mark=price)
        terms = replace(event(), old_basis=basis("OLD", 5, price, price),
                        retained_basis=basis("OLD", 9, 1, 1), new_basis=basis("NEW", 15, 1, 1),
                        retained_ratio=D(1), new_ratio=D(1), retained_carry_weight=D("0.5"))
        before = ActivityBook(D(0), (old,), day(5))
        after = before.apply_spin_off(terms, session=day(6))
        assert after.nav == before.nav == price
        assert sum_decimal_values(tuple(lot.carried_value for lot in after.lots)) == price
        assert context.prec == precision
    assert getcontext().prec == original_precision


def test_normal_legacy_multilot_nav_snapshot_is_preserved():
    second = replace(original(), lot_id="entry-2", shares=D(40), entered_on=day(3),
                     due_on=day(20), available_on=day(3))
    before = ActivityBook(D(123), (original(), second), day(5))
    assert before.nav == D(7123)
    assert before.apply_spin_off(event(), session=day(6)).nav == D(7123)


def test_exact_value_sum_preserves_small_values_and_bounds_its_work():
    with localcontext() as context:
        context.prec = 7
        assert sum_decimal_values((D("1e20"), D(1), D("-1e20"))) == D(1)
        assert context.prec == 7
    assert sum_decimal_values(()) == 0
    for invalid in ((D("NaN"),), (D("Infinity"),), (1,), (D("1e5000"),), (D("1e-5000"),)):
        with pytest.raises(ValueError):
            sum_decimal_values(invalid)
    with pytest.raises(ValueError, match="bounded tuple"):
        sum_decimal_values([D(1)])


def test_fractional_entitlements_and_deterministic_ids_are_preserved():
    terms = replace(event(), retained_ratio=D("0.9112243"), new_ratio=D("0.1832341"))
    first = book().apply_spin_off(terms, session=day(6))
    second = book().apply_spin_off(terms, session=day(6))
    assert first == second
    assert tuple(component.shares for component in first.lots) == (D("182.24486"), D("45.808525"))
    assert first.nav == book().nav


@pytest.mark.parametrize("which", ["retained_basis", "new_basis"])
@pytest.mark.parametrize("scale", [D("0.001"), D(13)])
def test_future_price_levels_cannot_determine_pending_weights_or_value(which, scale):
    terms = event()
    previous = getattr(terms, which)
    changed = replace(terms, **{which: replace(previous, raw_close=previous.raw_close * scale,
                                             adjusted_close=previous.adjusted_close * scale)})
    assert book().apply_spin_off(changed, session=day(6)) == book().apply_spin_off(terms, session=day(6))


def test_changed_unit_factors_preserve_raw_entitlement_and_carried_value():
    terms = event()
    changed = replace(terms, retained_basis=replace(terms.retained_basis, adjusted_close=D(100)),
                      new_basis=replace(terms.new_basis, adjusted_close=D(100)))
    baseline, rebased = (book().apply_spin_off(value, session=day(6)) for value in (terms, changed))
    assert tuple(lot.shares for lot in rebased.lots) == (D(60), D(50))
    for old, new in zip(baseline.lots, rebased.lots, strict=True):
        assert old.shares == new.shares * 2 and new.mark == pytest.approx(old.mark * 2)
        assert old.marked_value == new.marked_value
    assert baseline.nav == rebased.nav == book().nav


def test_each_delivery_separately_gates_observable_marks_and_clears_only_its_carry():
    result = book().apply_spin_off(event(), session=day(6))
    retained, allotted = result.lots
    assert observable_mark(retained, session=day(8), close=D(999), frozen=False) == retained
    assert observable_mark(retained, session=day(9), close=D(999), frozen=True) == retained
    observed = observable_mark(retained, session=day(9), close=D(60), frozen=False)
    assert observed.mark == D(60) and observed.mark_date == day(9) and observed.carried_value is None
    assert observed.marked_value == D(7200)
    assert observable_mark(allotted, session=day(14), close=D(999), frozen=False) == allotted
    last = observable_mark(allotted, session=day(15), close=D(80), frozen=False)
    assert last.carried_value is None and last.marked_value == D(8000)
    assert last.investment_key == observed.investment_key


def test_partial_exit_or_last_component_settlement_keeps_and_then_releases_one_slot():
    result = book().apply_spin_off(event(), session=day(6))
    waiting = replace(result, lots=(result.lots[1],), as_of=day(10))
    assert waiting.slot_count == 1 and waiting.lots[0].due_on == day(10)
    assert waiting.nav == D(1623)
    assert replace(waiting, lots=(), as_of=day(15)).slot_count == 0


def test_final_cash_uses_component_raw_units_without_discarding_the_other_right():
    split = book().apply_spin_off(event(), session=day(6))
    payout = FinalCashPayment("new-final-cash", FinalCashDistribution(
        "NEW", day(16), day(18), day(20), D(10), basis("NEW", 16, 250, 50),
        "synthetic confirmed final net payment",
    ))
    paid = split.apply_final_cash(payout, session=day(20))
    assert paid.cash == D(323)  # 100 adjusted new * .2 raw/adjusted * 10.
    assert paid.lots == (split.lots[0],) and paid.slot_count == 1
    assert paid.lots[0].carried_value == D(3500)
    with pytest.raises(ValueError, match="already been applied"):
        paid.apply_final_cash(payout, session=day(20))


def test_multiple_original_investments_keep_their_own_terms_and_slots():
    first = original()
    second = replace(first, lot_id="entry-2", shares=D(40), entered_on=day(3),
                     available_on=day(3), due_on=day(20))
    before = book(first, second)
    after = before.apply_spin_off(event(), session=day(6))
    assert len(after.lots) == 4 and after.slot_count == before.slot_count == 2
    assert after.lots[0].investment_key == after.lots[1].investment_key
    assert after.lots[2].investment_key == after.lots[3].investment_key
    assert after.lots[0].investment_key != after.lots[2].investment_key
    assert [lot.due_on for lot in after.lots] == [day(10), day(10), day(20), day(20)]
    assert after.nav == before.nav and len({lot.lot_id for lot in after.lots}) == 4


def test_existing_group_identity_remains_stable_and_unrelated_component_is_preserved():
    first = replace(original(), investment_id="original-investment")
    unrelated = replace(first, lot_id="other-component", code="OTHER", shares=D(1))
    before = book(first, unrelated)
    after = before.apply_spin_off(event(), session=day(6))
    assert after.lots[-1] == unrelated
    assert all(lot.investment_id == "original-investment" for lot in after.lots)
    assert after.slot_count == before.slot_count == 1 and after.nav == before.nav


@pytest.mark.parametrize("prefix", ["spin-off-component:", "spin-off-investment:"])
def test_generated_component_or_group_collision_is_rejected_atomically(prefix):
    identity = json.dumps((event().event_id, original().lot_id), separators=(",", ":"))
    colliding = prefix + identity
    other = replace(original(), code="OTHER", lot_id=colliding if "component" in prefix else "other",
                    investment_id=colliding if "investment" in prefix else None)
    before = book(original(), other)
    with pytest.raises(ValueError, match="collision"):
        before.apply_spin_off(event(), session=day(6))
    assert before.lots == (original(), other) and before.applied_event_ids == ()


@pytest.mark.parametrize("change", [
    {"mark": D(49)}, {"mark_date": day(4)}, {"dataset_sha256": "b" * 64},
    {"available_on": day(6)}, {"carried_value": D(5000)},
])
def test_invalid_later_lot_refuses_whole_event_without_partial_conversion(change):
    bad = replace(original(), lot_id="invalid-later", **change)
    before = book(original(), bad)
    with pytest.raises(ValueError):
        before.apply_spin_off(event(), session=day(6))
    assert before == book(original(), bad) and before.applied_event_ids == ()


def test_later_record_ineligible_purchase_cannot_receive_old_rights():
    terms = replace(event(), old_basis=basis("OLD", 6, 100, 50), effective_on=day(7))
    late = replace(original(), entered_on=day(6), available_on=day(6), mark_date=day(6))
    before = ActivityBook(D(0), (late,), day(6))
    with pytest.raises(ValueError, match="record eligibility"):
        before.apply_spin_off(terms, session=day(7))


def test_ex_rights_entry_before_record_day_is_not_eligible():
    late = replace(original(), entered_on=day(5), available_on=day(5))
    before = book(late)
    assert late.entered_on <= event().record_on
    with pytest.raises(ValueError, match="record eligibility"):
        before.apply_spin_off(event(), session=day(6))
    valid = replace(event(), last_eligible_entry_on=day(5))
    assert before.apply_spin_off(valid, session=day(6)).slot_count == 1


def test_future_event_waits_and_empty_due_event_is_consumed_once():
    before = book()
    waiting = before.apply_spin_off(event(), session=day(5))
    assert waiting == before
    empty = replace(before, lots=()).apply_spin_off(event(), session=day(6))
    assert empty.lots == () and empty.applied_event_ids == (event().event_id,)
    applied = before.apply_spin_off(event(), session=day(6))
    with pytest.raises(ValueError, match="already been applied"):
        applied.apply_spin_off(event(), session=day(15))
    with pytest.raises(ValueError, match="precede the book date"):
        applied.apply_spin_off(replace(event(), event_id="different"), session=day(5))


def test_pending_carried_component_is_not_an_observable_exchange_basis():
    retained = book().apply_spin_off(event(), session=day(6)).lots[0]
    with pytest.raises(ValueError, match="carried allocation"):
        successor_entitlement(retained, ratio=D(1),
            old_basis=PriceBasis("OLD", day(5), retained.mark, retained.mark, SNAPSHOT, "synthetic invalid carry"),
            new_basis=basis("NEXT", 20, 50, 50), effective_on=day(19), available_on=day(20),
            session=day(19), source="synthetic subsequent exchange")


@pytest.mark.parametrize("field", ["retained_ratio", "new_ratio", "retained_carry_weight"])
@pytest.mark.parametrize("value", [D(0), D(-1), D("NaN"), D("Infinity"), 0.5, None])
def test_event_requires_exact_positive_decimal_ratios_and_weight(field, value):
    with pytest.raises(ValueError):
        replace(event(), **{field: value})


@pytest.mark.parametrize("change", [
    {"retained_carry_weight": D(1)}, {"retained_carry_weight": D("1.01")},
    {"carry_source": ""}, {"carry_source": " padded"}, {"carry_evidence_level": "unknown"},
    {"source": ""}, {"record_on": day(7)}, {"effective_on": day(5)},
    {"retained_available_on": day(5)}, {"new_available_on": day(5)},
    {"record_on": datetime(2025, 1, 5)},
    {"last_eligible_entry_on": day(6)}, {"last_eligible_entry_on": datetime(2025, 1, 4)},
])
def test_event_rejects_unsupported_weight_provenance_or_dates(change):
    with pytest.raises(ValueError):
        replace(event(), **change)


@pytest.mark.parametrize("which", ["old_basis", "retained_basis", "new_basis"])
def test_event_rejects_mixed_price_snapshots_for_any_component(which):
    with pytest.raises(ValueError, match="snapshot"):
        replace(event(), **{which: replace(getattr(event(), which), dataset_sha256="b" * 64)})


@pytest.mark.parametrize("change", [
    {"retained_basis": basis("OTHER", 9, 200, 50)}, {"new_basis": basis("OLD", 15, 250, 50)},
    {"retained_basis": basis("OLD", 8, 200, 50)}, {"new_basis": basis("NEW", 14, 250, 50)},
])
def test_event_requires_the_retained_code_and_each_availability_basis(change):
    with pytest.raises(ValueError):
        replace(event(), **change)


@pytest.mark.parametrize("value", [D(0), D(-1), D("NaN"), D("Infinity"), 1, True])
def test_carry_value_cannot_be_zero_unknown_or_nondecimal(value):
    with pytest.raises(ValueError, match="carried value"):
        replace(original(), carried_value=value)
    corrupted = original()
    object.__setattr__(corrupted, "carried_value", value)
    with pytest.raises(ValueError, match="carried value"):
        book(corrupted)
