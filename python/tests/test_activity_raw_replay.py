"""Fabricated raw episode, pending carry and execution-boundary regressions.

The daily synthetic calendar is deliberately not a claimed KRX calendar.
No saved financial input, DB, API, SSH, credential or trial is consumed.
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
import socket
import sqlite3
from unittest.mock import patch

import pytest

from research import activity_raw_quote_view as raw_module
from research import activity_replay as replay_module
from research.activity_accounting import FinalCashDistribution, Lot, PriceBasis
from research.activity_book import ActivityBook, CompulsorySpinOff, CompulsoryStockExchange, FinalCashPayment
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import RAW_FIELDS, extract_raw_episode_targets
from research.activity_timing import SessionLagPolicy
import test_activity_quote_extension as synthetic


LG, LX, OTHER = "003550", "383800", "000001"
CALENDAR = tuple(date(2021, 4, 27) + timedelta(days=index) for index in range(42))
AVAILABLE = date(2021, 5, 27)
EFFECTIVE = date(2021, 5, 1)
OLD_MARK = date(2021, 4, 28)


def fixture(*, weight=".5", lag=1, due=date(2021, 5, 26), states=None,
            lg_start=None, lg_end=None, lx_start=None, lx_end=None):
    """Retain a complete original base while independently declaring raw scope."""
    with patch.object(synthetic, "DAYS", CALENDAR):
        original = synthetic.component({LG: CALENDAR[:-1], OTHER: CALENDAR[:-1]}, "b" * 64)
        extension = synthetic.component({LX: CALENDAR[30:-1]}, "c" * 64)
    base = extend_holding_quotes(original, extension)
    plan = dict(schema=raw_module.PLAN_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256,
        calendar_sha256=raw_module.calendar_sha256(CALENDAR), episodes=[
            dict(code=LG, start=str(lg_start or CALENDAR[0]), end=str(lg_end or CALENDAR[-1])),
            dict(code=LX, start=str(lx_start or AVAILABLE), end=str(lx_end or CALENDAR[-1]))], **raw_module.UNITS)
    observations = []
    for episode in plan["episodes"]:
        code = episode["code"]
        for day in CALENDAR:
            if not episode["start"] <= str(day) <= episode["end"]:
                continue
            price = "100" if day <= OLD_MARK else "50" if code == LG else "200"
            source = dict(ISU_CD=code, BAS_DD=day.strftime("%Y%m%d"),
                **dict.fromkeys(RAW_FIELDS[:4], price), ACC_TRDVOL="100", ACC_TRDVAL="1000")
            chosen = (states or {}).get((code, day), "zero" if code == LG and OLD_MARK < day < AVAILABLE else None)
            if chosen == "zero":
                source.update(dict.fromkeys(RAW_FIELDS, "0.00"))
            elif chosen == "invalid":
                source["TDD_OPNPRC"] = "NULL"
            elif chosen == "frozen":
                source.update(ACC_TRDVOL="0", ACC_TRDVAL="0")
            rows = [] if chosen == "absent" else [source, source] if chosen == "duplicate" else [source]
            observations.extend(extract_raw_episode_targets(rows,
                dict(service="stk_bydd_trd", bas_dd=source["BAS_DD"], targets=[code]),
                "2026-10-10T00:00:00+00:00", "d" * 64))
    plan_raw = encoded(plan)
    source = raw_module.encode_raw_artifact(base, tuple(observations), digest(plan_raw))
    view = raw_module.build_raw_quote_view(base, tuple(observations), source_artifact=source,
        source_artifact_sha256=digest(source), unit_plan=plan_raw, unit_plan_sha256=digest(plan_raw))
    policy = SessionLagPolicy(lag, paired=True)
    params = dict(lookback=1, holding_sessions=5, slots=2, end=str(CALENDAR[-3]), seed=17,
        threshold=0, commission_bps_per_side=0, slippage_bps_per_side=0)
    selections = tuple(PartitionSelection(CALENDAR[index], CALENDAR[index + lag],
        policy.selection_at(CALENDAR, index), (OTHER,), 1, "e" * 64)
        for index in policy.formation_indices(CALENDAR, lookback=1, holding_sessions=5, end_index=len(CALENDAR) - 3))
    def basis(code, session, price):
        return PriceBasis(code, session, D(price), D(price), view.quote_snapshot_sha256,
                          "synthetic declared raw/raw unit factor one")
    event = CompulsorySpinOff("split", D(".8"), D(".2"), basis(LG, OLD_MARK, "100"),
        basis(LG, AVAILABLE, "50"), basis(LX, AVAILABLE, "200"), OLD_MARK, date(2021, 4, 30),
        EFFECTIVE, AVAILABLE, AVAILABLE, D(weight), "synthetic registered carry convention", "assumed",
        "synthetic final terms; availability explicitly assumed")
    book = ActivityBook(D(2000), (Lot("original", LG, D(10), D(100), OLD_MARK, CALENDAR[0],
        due, CALENDAR[0], view.quote_snapshot_sha256),), OLD_MARK)
    return dict(dates=CALENDAR, panel=view.panel, params=params, selections=selections,
        dataset_sha256=view.quote_snapshot_sha256, events=(event,), initial_book=book,
        timing_policy=policy, raw_quote_view=view)


def run(values):
    return replay_module.replay_synthetic(**values)


def at(result, session):
    return next(book for book in result.books if book.as_of == session)


def test_raw_event_basis_cannot_halve_successor_quantity_under_the_same_view_snapshot():
    values = fixture()
    normal = run(values)
    event = values["events"][0]
    forged = replace(event, new_basis=replace(event.new_basis, raw_close=D("100")))
    # The pre-existing explicit adjusted-unit seam remains caller-supplied.
    legacy = run({**values, "raw_quote_view": None, "events": (forged,)})
    def successor_shares(result):
        return next(lot.shares for lot in at(result, EFFECTIVE).lots if lot.code == LX)
    assert successor_shares(normal) == D(2) and successor_shares(legacy) == D(1)
    assert at(normal, AVAILABLE).nav != at(legacy, AVAILABLE).nav
    with pytest.raises(ValueError, match="raw event basis"):
        run({**values, "events": (forged,)})


@pytest.mark.parametrize("field", ["old_basis", "retained_basis", "new_basis"])
@pytest.mark.parametrize("mutation", ["nonunit", "different_level"])
def test_each_spin_basis_must_use_actual_raw_coordinate_close_before_prices(monkeypatch, field, mutation):
    values = fixture(); event = values["events"][0]
    basis = getattr(event, field)
    changed = replace(basis, raw_close=basis.raw_close / D(2)) if mutation == "nonunit" else replace(
        basis, raw_close=basis.raw_close * D(2), adjusted_close=basis.adjusted_close * D(2))
    values["events"] = (replace(event, **{field: changed}),)
    monkeypatch.setattr(replay_module, "_quote", lambda *a: pytest.fail("unbound basis reached prices"))
    with pytest.raises(ValueError, match="raw event basis"):
        run(values)


@pytest.mark.parametrize("kind", ["exchange_old", "exchange_new", "cash"])
def test_raw_basis_binding_also_covers_stock_exchange_and_final_cash_before_prices(monkeypatch, kind):
    values = fixture(); spin = values["events"][0]
    if kind == "cash":
        basis = replace(spin.old_basis, raw_close=D(50))
        event = FinalCashPayment("final", FinalCashDistribution(LG, OLD_MARK, date(2021, 4, 30),
            EFFECTIVE, D(7), basis, "synthetic verified payment declaration"))
    else:
        old, new = spin.old_basis, spin.new_basis
        if kind == "exchange_old": old = replace(old, raw_close=old.raw_close / D(2))
        else: new = replace(new, raw_close=new.raw_close / D(2))
        event = CompulsoryStockExchange("exchange", D(".2"), old, new, EFFECTIVE, AVAILABLE, "synthetic exchange declaration")
    values["events"] = (event,)
    monkeypatch.setattr(replay_module, "_quote", lambda *a: pytest.fail("unbound event basis reached prices"))
    with pytest.raises(ValueError, match="raw event basis"):
        run(values)


@pytest.mark.parametrize("state", ["absent", "zero", "invalid", "duplicate", "frozen"])
def test_raw_basis_anchor_requires_resolved_nonfrozen_quote_before_prices(monkeypatch, state):
    values = fixture(states={(LX, AVAILABLE): state})
    monkeypatch.setattr(replay_module, "_quote", lambda *a: pytest.fail("unusable raw anchor reached prices"))
    with pytest.raises(ValueError, match="raw event basis"):
        run(values)


def test_pending_event_components_skip_prices_preserve_one_slot_and_refreeze_once(monkeypatch):
    values = fixture(states={(LG, EFFECTIVE): "invalid"})
    view, original_quote = values["raw_quote_view"], replay_module._quote
    calls, quote_indices = [], []
    original_validate = raw_module.validate_raw_quote_view
    def validate(candidate):
        calls.append(candidate)
        return original_validate(candidate)
    def quote(series, index):
        if series == view.panel[LX] and CALENDAR[index] < AVAILABLE:
            pytest.fail("pending LX used an unrequested pre-delivery quote")
        if series == view.panel[LG] and EFFECTIVE <= CALENDAR[index] < AVAILABLE:
            pytest.fail("pending retained component used a price")
        quote_indices.append(index)
        return original_quote(series, index)
    monkeypatch.setattr(raw_module, "validate_raw_quote_view", validate)
    monkeypatch.setattr(replay_module, "_quote", quote)
    result = run(values)
    assert calls == [view] and quote_indices
    for book in result.books:
        if EFFECTIVE <= book.as_of < AVAILABLE:
            components = tuple(lot for lot in book.lots if lot.code in (LG, LX))
            assert len(components) == 2 and len({lot.investment_key for lot in components}) == 1
            assert all(lot.carried_value == D(500) and lot.mark_date == OLD_MARK for lot in components)
            assert all(lot.due_on == date(2021, 5, 26) for lot in components)
    assert not any(lot.code in (LG, LX) for lot in at(result, AVAILABLE).lots)
    assert result.closed_trades >= 2


def test_non_session_effect_creates_pending_group_only_on_first_later_calendar_session(monkeypatch):
    first_later = date(2021, 5, 3)
    sparse = tuple(day for day in CALENDAR if not EFFECTIVE <= day < first_later)
    monkeypatch.setitem(globals(), "CALENDAR", sparse)
    values = fixture()
    assert EFFECTIVE not in values["dates"]
    result = run(values)
    before = tuple(lot for lot in at(result, date(2021, 4, 30)).lots if lot.code in (LG, LX))
    assert len(before) == 1 and before[0].investment_id is None and before[0].carried_value is None
    pending = tuple(lot for lot in at(result, first_later).lots if lot.code in (LG, LX))
    assert len(pending) == 2 and len({lot.investment_key for lot in pending}) == 1
    assert all(lot.mark_date == OLD_MARK and lot.carried_value == D(500) for lot in pending)
    assert not any(lot.code in (LG, LX) for lot in at(result, AVAILABLE).lots)


@pytest.mark.parametrize("lag", [1, 2])
@pytest.mark.parametrize("due", [date(2021, 5, 26), date(2021, 6, 1)])
@pytest.mark.parametrize("weight", [".01", ".1", ".9", ".99"])
def test_same_first_normal_price_carry_weight_does_not_change_nav_cash_slots_or_later_fills(lag, due, weight):
    values = fixture(lag=lag, due=due)
    baseline = run(values)
    changed = run({**values, "events": (replace(values["events"][0], retained_carry_weight=D(weight)),)})
    assert [(book.as_of, book.nav, book.cash, book.slot_count) for book in changed.books] == [
        (book.as_of, book.nav, book.cash, book.slot_count) for book in baseline.books]
    assert (changed.selections, changed.diagnostics, changed.closed_trades) == (
        baseline.selections, baseline.diagnostics, baseline.closed_trades)
    for left, right in zip(changed.books, baseline.books):
        assert [(lot.lot_id, lot.code, lot.shares, lot.due_on, lot.available_on, lot.investment_key) for lot in left.lots] == [
            (lot.lot_id, lot.code, lot.shares, lot.due_on, lot.available_on, lot.investment_key) for lot in right.lots]
    assert tuple(book for book in changed.books if book.as_of >= AVAILABLE) == tuple(
        book for book in baseline.books if book.as_of >= AVAILABLE)
    assert any(lot.code == OTHER and lot.entered_on > AVAILABLE for book in changed.books for lot in book.lots)


def test_delayed_first_component_price_is_a_counterexample_to_unconditional_carry_invariance():
    missing = {(LX, day): "absent" for day in CALENDAR if AVAILABLE <= day < date(2021, 5, 31)}
    values = fixture(weight=".1", states=missing)
    with pytest.raises(ValueError, match="raw event basis"):
        run(values)
    # The counterexample remains valid for the explicit legacy basis seam;
    # raw mode cannot invent an availability-date anchor from an absent row.
    values["raw_quote_view"] = None
    low = run(values)
    high = run({**values, "events": (replace(values["events"][0], retained_carry_weight=D(".9")),)})
    assert at(low, AVAILABLE).nav - at(high, AVAILABLE).nav == D(800)
    later = date(2021, 5, 29)
    assert at(low, later).cash != at(high, later).cash
    assert tuple(lot.shares for lot in at(low, later).lots if lot.code == OTHER) != tuple(
        lot.shares for lot in at(high, later).lots if lot.code == OTHER)
    assert at(low, later).slot_count == at(high, later).slot_count


@pytest.mark.parametrize("state", ["absent", "zero", "invalid", "duplicate"])
def test_available_component_missingness_is_not_a_pending_boundary_exception(state):
    due = AVAILABLE + timedelta(days=1)
    values = fixture(states={(LX, due): state}, due=due)
    if state in ("invalid", "duplicate"):
        with pytest.raises(ValueError, match="unresolved raw"):
            run(values)
        return
    result = run(values)
    waiting = tuple(lot for lot in at(result, due).lots if lot.code == LX)
    assert len(waiting) == 1 and waiting[0].carried_value is None and waiting[0].mark_date == AVAILABLE
    key = LX, due
    assert (key in values["raw_quote_view"].absent_coordinates) == (state == "absent")
    assert (key in values["raw_quote_view"].no_trade_coordinates) == (state == "zero")
    assert not any(lot.code == LX for lot in at(result, due + timedelta(days=1)).lots)


@pytest.mark.parametrize("case", ["initial_carry", "independent", "new_entry", "available_outside", "post_episode"])
def test_outside_scope_cannot_use_initial_fake_carry_independent_lot_entry_or_delivery_exceptions(case):
    values = fixture(lx_start=AVAILABLE + timedelta(days=1) if case == "available_outside" else None,
                     lx_end=AVAILABLE if case == "post_episode" else None,
                     due=date(2021, 6, 1) if case == "post_episode" else date(2021, 5, 26))
    if case in ("initial_carry", "independent"):
        old = values["initial_book"].lots[0]
        lot = replace(old, code=LX, available_on=AVAILABLE if case == "initial_carry" else CALENDAR[0],
            investment_id="caller-supplied-group", carried_value=D(1000) if case == "initial_carry" else None)
        values.update(initial_book=replace(values["initial_book"], lots=(lot,)), events=())
    elif case == "new_entry":
        values.update(initial_book=replace(values["initial_book"], lots=()), events=(),
                      selections=tuple(replace(selection, eligible_codes=(LX,)) for selection in values["selections"]))
    with pytest.raises(ValueError, match="raw event basis" if case == "available_outside" else "raw episode boundary"):
        run(values)


@pytest.mark.parametrize("mutation", ["calendar", "snapshot", "panel_missing", "panel_extra", "panel_decimal_scale",
                                     "view_rows", "view_summary", "view_base"])
def test_entry_validation_rejects_forged_view_or_mismatched_full_typed_inputs_before_prices(monkeypatch, mutation):
    values = fixture()
    view = values["raw_quote_view"]
    if mutation == "calendar":
        values["dates"] = CALENDAR[:-1]
    elif mutation == "snapshot":
        values["dataset_sha256"] = "f" * 64
    elif mutation in ("panel_missing", "panel_extra", "panel_decimal_scale"):
        panel = dict(view.panel)
        if mutation == "panel_missing":
            panel.pop(OTHER)
        elif mutation == "panel_extra":
            panel["999999"] = panel[OTHER]
        else:
            panel[LG] = replace(panel[LG], closes=(D("100.0"), *panel[LG].closes[1:]))
        values["panel"] = panel
    elif mutation == "view_rows":
        values["raw_quote_view"] = replace(view, raw_observations=(replace(view.raw_observations[0], close=D(999)), *view.raw_observations[1:]))
    elif mutation == "view_summary":
        values["raw_quote_view"] = replace(view, summary_json=b"{}\n")
    else:
        values["raw_quote_view"] = replace(view, base=replace(view.base, requested_coordinates=()))
    monkeypatch.setattr(replay_module, "_quote", lambda *args: pytest.fail("invalid input reached a price"))
    with pytest.raises(ValueError):
        run(values)


def test_omitting_raw_view_preserves_existing_absent_panel_behavior():
    values = fixture(lx_end=AVAILABLE, due=date(2021, 6, 1))
    with pytest.raises(ValueError, match="raw episode boundary"):
        run(values)
    values.pop("raw_quote_view")
    result = run(values)
    assert any(lot.code == LX for lot in result.books[-1].lots)


def test_pending_exception_does_not_cover_an_independent_lot_of_the_same_successor_code():
    values = fixture(states={(LX, EFFECTIVE): "invalid"}, lx_start=CALENDAR[0])
    original = values["initial_book"].lots[0]
    independent = replace(original, lot_id="independent-successor", code=LX, shares=D(1),
                          due_on=date(2021, 6, 1))
    values["initial_book"] = replace(values["initial_book"], lots=(original, independent))
    with pytest.raises(ValueError, match="unresolved raw"):
        run(values)


def test_normal_raw_mode_matches_legacy_books_and_neither_path_performs_io(monkeypatch):
    values = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("pure raw replay attempted IO")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    raw = run(values)
    legacy = run({**values, "raw_quote_view": None})
    assert raw == legacy
