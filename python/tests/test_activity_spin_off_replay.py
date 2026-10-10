"""Synthetic D+1/D+2 two-component delivery, independent exits and capacity."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
import json
from pathlib import Path

import pytest

from research.activity_accounting import Lot, PriceBasis
from research.activity_book import ActivityBook, CompulsorySpinOff, CompulsoryStockExchange
from research.activity_partition_selection import PartitionSelection
from research.activity_replay import DecimalSeries, replay_synthetic
from research.activity_timing import SessionLagPolicy


SNAPSHOT = "a" * 64


def inputs(lag=1, *, initial=True, end=10, due=5, retained_available=4, new_available=8):
    calendar = tuple(date(2024, 1, 1) + timedelta(days=index) for index in range(14))
    path = Path(__file__).resolve().parents[2] / "configs/research/discovery/activity-calibration-v1.json"
    params = json.loads(path.read_text())["parameters"]
    params.update(lookback=2, holding_sessions=3, slots=1, end=calendar[end].isoformat(),
                  commission_bps_per_side=0, slippage_bps_per_side=0)
    policy = SessionLagPolicy(lag, paired=True)
    def series(price):
        prices = (D(price),) * len(calendar)
        return DecimalSeries(prices, prices, (D(10),) * len(calendar),
                             (False,) * len(calendar), (False,) * len(calendar), (True,) * len(calendar))
    panel = {"OLD": series(50), "NEW": series(100), "OTHER": series(100)}
    def basis(code, index):
        price = D(50) if code == "OLD" else D(100)
        return PriceBasis(code, calendar[index], price, price, SNAPSHOT, "synthetic coherent unit factor")
    effective, old_index = (3, 2) if initial else (5, 4)
    if not initial:
        retained_available = max(retained_available, 6)
    terms = CompulsorySpinOff(
        "split", D("0.8"), D("0.2"), basis("OLD", old_index),
        basis("OLD", retained_available), basis("NEW", new_available), calendar[old_index],
        calendar[old_index], calendar[effective], calendar[retained_available], calendar[new_available],
        D("0.75"), "synthetic explicit carry rule", "assumed", "synthetic compulsory source",
    )
    initial_lots = (Lot("entry-old", "OLD", D(100), D(50), calendar[2], calendar[0],
                        calendar[due], calendar[0], SNAPSHOT),) if initial else ()
    book = ActivityBook(D(0) if initial else D(5000), initial_lots, calendar[2])
    indices = policy.formation_indices(calendar, lookback=2, holding_sessions=3, end_index=end)
    selections = tuple(PartitionSelection(calendar[index], calendar[index + lag], policy.selection_at(calendar, index),
                                         ("OTHER",) if initial or index != 2 else ("OLD",), 1, "b" * 64)
                       for index in indices)
    return calendar, panel, params, selections, book, policy, terms


def run(values, *, events=None):
    calendar, panel, params, selections, book, policy, terms = values
    return replay_synthetic(calendar, panel, params, selections, dataset_sha256=SNAPSHOT,
                            initial_book=book, timing_policy=policy, events=(terms,) if events is None else events)


def at(replay, day):
    return next(book for book in replay.books if book.as_of == day)


@pytest.mark.parametrize("lag", [1, 2])
def test_parent_continues_trading_and_components_deliver_and_exit_independently(lag):
    values = inputs(lag)
    calendar = values[0]
    replay = run(values)
    pending = at(replay, calendar[3])
    assert pending.nav == D(5000) and pending.cash == 0 and pending.slot_count == 1
    assert len(pending.lots) == 2
    assert all(lot.carried_value is not None and lot.mark_date == calendar[2] for lot in pending.lots)
    first_observed = at(replay, calendar[4])
    retained, allotted = first_observed.lots
    assert retained.marked_value == D(4000) and retained.carried_value is None
    assert allotted.marked_value == D(1250) and allotted.carried_value is not None
    partially_sold = at(replay, calendar[5])
    assert tuple(lot.code for lot in partially_sold.lots) == ("NEW",)
    assert partially_sold.slot_count == 1 and partially_sold.cash == D("3992.8")
    assert partially_sold.lots[0].due_on == calendar[5]
    assert at(replay, calendar[7]).lots == partially_sold.lots
    settled = at(replay, calendar[8])
    assert settled.lots == () and settled.slot_count == 0 and settled.cash == D("5989.2")
    assert replay.closed_trades == 2 and dict(replay.diagnostics)["delayed_exits"] == 1
    assert replay.selections == ((calendar[2], ()), (calendar[5], ()))
    assert values[4].lots[0].code == "OLD" and values[4].applied_event_ids == ()


def test_paired_arms_keep_original_acquisition_due_and_one_slot_after_actual_entry():
    arms = []
    for lag in (1, 2):
        values = inputs(lag, initial=False)
        replay = run(values)
        calendar = values[0]
        split = at(replay, calendar[5])
        assert len(split.lots) == 2 and split.slot_count == 1
        for lot in split.lots:
            assert lot.entered_on == calendar[2 + lag] and lot.due_on == calendar[5 + lag]
            assert lot.investment_key == split.lots[0].investment_key
        assert replay.selections == ((calendar[2], ("OLD",)), (calendar[5], ()))
        assert dict(replay.diagnostics)["entries"] == 1 and replay.closed_trades == 2
        assert at(replay, calendar[8]).lots == ()
        arms.append(replay)
    assert arms[0].books[-1].nav == arms[1].books[-1].nav


@pytest.mark.parametrize("lag", [1, 2])
def test_effective_event_runs_before_due_sale_and_pending_group_blocks_new_fill(lag):
    entry_index = 2 + lag
    values = inputs(lag, end=7, due=entry_index, retained_available=5, new_available=8)
    replay = run(values)
    current = at(replay, values[0][entry_index])
    if lag == 1:
        # The target is fixed from the prior book before the event or quotes;
        # new pending rights then block its execution without replacement.
        assert replay.selections == ((values[0][2], ("OTHER",)),)
        assert dict(replay.diagnostics)["unfilled_capacity_targets"] == 1
    else:
        # D+2 sees the already-created pending group in its prior book.
        assert replay.selections == ((values[0][2], ()),)
    assert current.slot_count == 1 and {lot.code for lot in current.lots} == {"OLD", "NEW"}
    assert current.cash == 0 and current.nav == D(5000)
    assert dict(replay.diagnostics).get("entries", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
def test_current_quotes_do_not_change_fixed_selection_or_pre_delivery_value(lag):
    entry_index = 2 + lag
    original = inputs(lag, end=7, due=entry_index, retained_available=5, new_available=8)
    changed = list(original)
    panel = dict(original[1])
    for code in ("OLD", "NEW"):
        series = panel[code]
        opens, closes, frozen = list(series.opens), list(series.closes), list(series.frozen)
        opens[entry_index] = closes[entry_index] = D(999)
        frozen[entry_index] = True
        panel[code] = replace(series, opens=tuple(opens), closes=tuple(closes), frozen=tuple(frozen))
    changed[1] = panel
    baseline, moved = run(original), run(changed)
    assert moved.selections == baseline.selections
    assert at(moved, original[0][entry_index]) == at(baseline, original[0][entry_index])


def test_both_due_delivered_components_sell_before_new_entry_and_release_one_slot():
    values = inputs(1, end=7, due=3, retained_available=3, new_available=3)
    replay = run(values)
    first = at(replay, values[0][3])
    assert replay.selections == ((values[0][2], ("OTHER",)),)
    assert tuple(lot.code for lot in first.lots) == ("OTHER",)
    assert first.slot_count == 1 and first.lots[0].investment_id is None
    assert first.cash == 0 and first.nav == D("5989.2")
    assert dict(replay.diagnostics)["entries"] == 1
    assert dict(replay.diagnostics).get("unfilled_capacity_targets", 0) == 0


def test_pre_effective_sale_cannot_silently_discard_already_attached_rights():
    values = list(inputs(1, due=3, retained_available=6, new_available=8))
    terms = values[-1]
    values[-1] = replace(terms, effective_on=values[0][5], old_basis=replace(terms.old_basis, session=values[0][4]))
    before = values[4]
    with pytest.raises(ValueError, match="unsupported pre-effective.*detach pending rights"):
        run(values)
    assert before.cash == 0 and tuple(lot.code for lot in before.lots) == ("OLD",)
    assert before.applied_event_ids == ()


@pytest.mark.parametrize("eligible_through,entered", [(4, 0), (1, 2)])
def test_pre_cutoff_sale_and_post_cutoff_acquisition_have_no_detached_right(eligible_through, entered):
    values = list(inputs(1, due=3, retained_available=6, new_available=8))
    calendar, terms = values[0], values[-1]
    values[-1] = replace(terms, last_eligible_entry_on=calendar[eligible_through], record_on=calendar[4],
                         effective_on=calendar[5], old_basis=replace(terms.old_basis, session=calendar[4]))
    if entered:
        old = replace(values[4].lots[0], entered_on=calendar[entered], available_on=calendar[entered])
        values[4] = replace(values[4], lots=(old,))
    replay = run(values)
    assert tuple(lot.code for lot in at(replay, calendar[3]).lots) == ("OTHER",)
    assert all(lot.code != "NEW" for book in replay.books for lot in book.lots)


def test_effectiveness_day_event_precedes_due_sale_without_detached_rights_rejection():
    values = list(inputs(1, due=5, retained_available=5, new_available=5))
    values[-1] = replace(values[-1], effective_on=values[0][5],
                         old_basis=replace(values[-1].old_basis, session=values[0][4]))
    replay = run(values)
    effective = at(replay, values[0][5])
    assert effective.lots == () and effective.applied_event_ids == ("split",)
    assert effective.cash == D("5989.2")


def test_absent_new_component_at_delivery_stays_pending_until_usable_opening():
    values = list(inputs())
    panel = dict(values[1])
    series = panel["NEW"]
    changes = {}
    for name in ("opens", "closes", "turnover", "frozen", "locked", "observed"):
        field = list(getattr(series, name))
        field[8] = False if name == "observed" else None
        changes[name] = tuple(field)
    panel["NEW"] = replace(series, **changes)
    values[1] = panel
    replay = run(values)
    pending = at(replay, values[0][8])
    assert len(pending.lots) == pending.slot_count == 1 and pending.lots[0].carried_value == D(1250)
    assert at(replay, values[0][9]).lots == () and replay.closed_trades == 2
    assert dict(replay.diagnostics)["delayed_exits"] == 1


def test_frozen_retained_sale_keeps_group_after_new_component_sells():
    values = list(inputs(new_available=4))
    panel = dict(values[1])
    series = panel["OLD"]
    frozen = tuple(5 <= index <= 6 for index in range(len(values[0])))
    panel["OLD"] = replace(series, frozen=frozen)
    values[1] = panel
    replay = run(values)
    partial = at(replay, values[0][5])
    assert tuple(lot.code for lot in partial.lots) == ("OLD",) and partial.slot_count == 1
    assert at(replay, values[0][6]).lots == partial.lots
    assert at(replay, values[0][7]).lots == () and replay.closed_trades == 2


@pytest.mark.parametrize("scale", [D("0.01"), D(7)])
def test_future_component_basis_price_levels_cannot_change_pending_books(scale):
    values = list(inputs())
    before = run(values)
    terms = values[-1]
    changes = {name: replace(getattr(terms, name), raw_close=getattr(terms, name).raw_close * scale,
                            adjusted_close=getattr(terms, name).adjusted_close * scale)
               for name in ("retained_basis", "new_basis")}
    values[-1] = replace(terms, **changes)
    after = run(values)
    assert before.books == after.books and before.selections == after.selections


def test_post_effective_ordinary_parent_purchase_does_not_receive_old_rights():
    values = list(inputs(initial=False, end=7, retained_available=6))
    terms = values[-1]
    old_basis = replace(terms.old_basis, session=values[0][2])
    values[-1] = replace(terms, old_basis=old_basis, last_eligible_entry_on=values[0][2],
                         record_on=values[0][2], effective_on=values[0][3])
    replay = run(values)
    entered = at(replay, values[0][3])
    assert len(entered.lots) == 1 and entered.lots[0].code == "OLD"
    assert entered.lots[0].investment_id is None and entered.lots[0].carried_value is None
    assert entered.applied_event_ids == ("split",) and replay.selections[0][1] == ("OLD",)
    assert all(lot.code != "NEW" for book in replay.books for lot in book.lots)


@pytest.mark.parametrize("reverse", [False, True])
def test_same_day_spin_off_code_chain_is_refused_independent_of_input_order(reverse):
    values = inputs()
    terms = values[-1]
    exchange = CompulsoryStockExchange(
        "same-day", D(1), replace(terms.new_basis, session=values[0][2]),
        PriceBasis("OTHER", values[0][8], D(100), D(100), SNAPSHOT, "synthetic bridge"),
        terms.effective_on, values[0][8], "synthetic chained event",
    )
    events = (exchange, terms) if reverse else (terms, exchange)
    with pytest.raises(ValueError, match="same-day spin-off"):
        run(values, events=events)


def test_spin_off_requires_explicit_new_series_and_matching_snapshot():
    values = list(inputs())
    values[1] = {code: series for code, series in values[1].items() if code != "NEW"}
    with pytest.raises(ValueError, match="successor.*series"):
        run(values)
    values = list(inputs())
    terms = values[-1]
    changes = {name: replace(getattr(terms, name), dataset_sha256="c" * 64)
               for name in ("old_basis", "retained_basis", "new_basis")}
    values[-1] = replace(terms, **changes)
    with pytest.raises(ValueError, match="same snapshot"):
        run(values)
