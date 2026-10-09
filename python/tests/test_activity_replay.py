"""Toy calendar integration: v1 compatibility and synthetic event bookkeeping."""

from array import array
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path

import pytest

from research.activity_accounting import FinalCashDistribution, Lot, PriceBasis
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment
from research.activity_portfolio import Series, eligible, simulate
from research.activity_replay import SyntheticCandidate, SyntheticSelection, replay_synthetic


SNAPSHOT = "a" * 64


def fixture():
    dates = [date(2026, 9, 8) + timedelta(days=i) for i in range(13)]
    spec = Path(__file__).resolve().parents[2] / "configs/research/discovery/activity-calibration-v1.json"
    params = json.loads(spec.read_text())["parameters"]
    params.update(lookback=2, holding_sessions=3, slots=1, end=dates[10].isoformat())
    return dates, params


def series(count=13, price=100):
    return Series(array("d", [price] * count), array("d", [price] * count),
                  array("d", [10] * count), bytearray(count), bytearray(count))


def candidate(code, known_on, *, activity=True):
    return SyntheticCandidate(code, "operating_common", known_on, True, "pass", "pass", activity)


def selections(dates, panel, params, *, legacy_activity=False):
    result = []
    end = dates.index(date.fromisoformat(params["end"]))
    for index in range(params["lookback"], end - params["holding_sessions"], params["holding_sessions"]):
        rows = tuple(candidate(code, dates[index], activity=
                               eligible(s, index, params["lookback"], params["threshold"])
                               if legacy_activity else True)
                     for code, s in panel.items() if s.closes[index] and not s.frozen[index])
        result.append(SyntheticSelection(dates[index], dates[index + 1], rows))
    return tuple(result)


def run(dates, panel, params, **kwargs):
    selected = kwargs.pop("selected", selections(dates, panel, params))
    return replay_synthetic(dates, panel, params, selected, dataset_sha256=SNAPSHOT, **kwargs)


def at(result, day):
    return next(book for book in result.books if book.as_of == day)


def basis(code, day, raw, adjusted):
    return PriceBasis(code, day, D(raw), D(adjusted), SNAPSHOT, "synthetic coordinate pair")


def test_action_free_toy_matches_all_v1_accounting_and_derived_session_returns():
    dates, params = fixture()
    params["slots"] = 2
    panel = {code: series() for code in ("A", "B", "C")}
    for s in panel.values():
        s.turnover[2] = s.turnover[5] = 30
    panel["A"].opens[3] = 200
    panel["A"].closes[3:6] = array("d", [210, 220, 230])
    panel["B"].frozen[6:8] = bytearray([1, 1])
    panel["B"].opens[6:8] = panel["B"].closes[6:8] = array("d", [999, 999])
    panel["C"].locked[3] = panel["C"].locked[6] = 1
    selected = selections(dates, panel, params, legacy_activity=True)
    legacy = simulate(dates, panel, params, trace_lots=True)
    result = run(dates, panel, params, selected=selected)
    assert list(map(float, result.navs)) == pytest.approx(legacy["navs"])
    navs = [D(1), *result.navs]
    assert [float(b / a - 1) for a, b in zip(navs, navs[1:])] == pytest.approx(legacy["returns"])
    assert dict(result.diagnostics) == legacy["diagnostics"]
    assert result.closed_trades == legacy["closed_trades"]
    assert result.books[-1].slot_count == legacy["unresolved_positions"]
    assert float(sum((lot.shares * lot.mark for lot in result.books[-1].lots), D(0))) == pytest.approx(
        legacy["unresolved_marked_value"])
    assert {(lot.code, str(lot.entered_on), str(lot.due_on)) for lot in result.books[-1].lots} == {
        (row["code"], row["entry_date"], row["due_date"]) for row in legacy["terminal_holdings"]}


def test_hash_order_and_failed_selected_fill_have_no_hindsight_substitute():
    dates, params = fixture()
    params["end"] = dates[7].isoformat()  # one formation
    codes = sorted(("A", "B"), key=lambda code: hashlib.sha256(
        f"{params['seed']}:{dates[2]}:{code}".encode()).digest())
    panel = {code: series() for code in reversed(codes)}
    panel[codes[0]].frozen[3] = 1
    result = run(dates, panel, params)
    assert result.selections == ((dates[2], (codes[0],)),)
    assert result.books[-1].cash == D(1) and result.books[-1].lots == ()
    assert dict(result.diagnostics)["unfilled_entries"] == 1
    assert "entries" not in dict(result.diagnostics)


def test_actual_sale_cost_tax_cash_funds_same_session_reentry_before_close_mark():
    dates, params = fixture()
    panel = {"A": series()}
    panel["A"].opens[3] = 200
    panel["A"].closes[3] = 220
    panel["A"].opens[6] = 240
    panel["A"].closes[6] = 260
    result = run(dates, panel, params)
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    old_shares = D(1) / (D(200) * (1 + cost / 10000))
    sale_cash = old_shares * 240 * (1 - (cost + 20) / 10000)
    first = at(result, dates[3]).lots[0]
    reentry = at(result, dates[6]).lots[0]
    assert first.shares == old_shares and first.mark == D(220)
    assert first.due_on == dates[6]
    assert reentry.lot_id != first.lot_id and reentry.entered_on == dates[6]
    assert reentry.due_on == dates[9] and reentry.shares == sale_cash / (D(240) * (1 + cost / 10000))
    assert reentry.mark == D(260) and reentry.mark_date == dates[6]
    assert at(result, dates[6]).cash == 0
    assert result.closed_trades == 2 and dict(result.diagnostics)["entries"] == 2


def test_missing_or_frozen_due_sales_retain_slot_cash_and_last_observed_mark():
    dates, params = fixture()
    panel = {"A": series()}
    for index in range(5, 11):
        panel["A"].opens[index] = panel["A"].closes[index] = 999 if index % 2 else 0
        panel["A"].frozen[index] = index % 2
    result = run(dates, panel, params)
    final = result.books[-1]
    assert final.cash == 0 and final.slot_count == 1
    assert final.lots[0].mark == D(100) and final.lots[0].mark_date == dates[4]
    assert result.closed_trades == 0
    assert dict(result.diagnostics)["pending_exit_sessions"] == 5
    assert dict(result.diagnostics)["entries"] == 1


@pytest.mark.parametrize("changes", [
    {"classification": "unknown"}, {"classification": "conflicting"},
    {"classification_known_on": None}, {"classification_known_on": date(2099, 1, 1)},
    # Formation is Sep 10: next-session knowledge still misses AG's cutoff,
    # including this held code which also fails the size screen.
    {"classification_known_on": date(2026, 9, 11)},
])
def test_all_classifications_fail_before_hash_even_for_held_or_screen_failed_codes(changes, monkeypatch):
    dates, params = fixture()
    panel = {"A": series(), "B": series()}
    original = ActivityBook(D(0), (Lot("prior", "B", D("0.01"), D(100), dates[2], dates[0],
                                      dates[10], dates[0], SNAPSHOT),), dates[2])
    selected = selections(dates, panel, params)
    bad = replace(selected[0].candidates[1], size_pass=False, **changes)
    selected = (replace(selected[0], candidates=(selected[0].candidates[0], bad)), *selected[1:])
    monkeypatch.setattr("research.activity_replay.hashlib.sha256",
                        lambda *args: pytest.fail("hash must not run before all classifications resolve"))
    with pytest.raises(ValueError, match="point-in-time synthetic classification"):
        run(dates, panel, params, selected=selected, initial_book=original)
    assert original.cash == 0 and original.lots[0].lot_id == "prior" and original.as_of == dates[2]


@pytest.mark.parametrize("liquidity,baseline", [("pass", "insufficient"), ("insufficient", "pass"),
                                                ("fail", "pass"), ("pass", "fail")])
def test_fixed_calendar_liquidity_and_operating_normal_baseline_remain_separate(liquidity, baseline):
    dates, params = fixture()
    panel = {"A": series()}
    selected = tuple(replace(s, candidates=(replace(s.candidates[0], absolute_liquidity=liquidity,
                                                   operating_baseline=baseline),))
                     for s in selections(dates, panel, params))
    result = run(dates, panel, params, selected=selected)
    assert result.navs == (D(1),) * len(result.books)
    assert all(chosen == () for _, chosen in result.selections)


@pytest.mark.parametrize("field,value", [("size_pass", None), ("activity_pass", None),
                                         ("absolute_liquidity", "unknown"),
                                         ("operating_baseline", "conflicting")])
def test_unknown_screen_inputs_stop_instead_of_becoming_an_exclusion(field, value):
    dates, params = fixture()
    panel = {"A": series()}
    selected = selections(dates, panel, params)
    selected = (replace(selected[0], candidates=(replace(selected[0].candidates[0], **{field: value}),)),
                *selected[1:])
    with pytest.raises(ValueError, match="unresolved synthetic selector"):
        run(dates, panel, params, selected=selected)


def test_missing_selection_population_or_next_session_timing_is_refused():
    dates, params = fixture()
    panel = {"A": series(), "CONTROL": series()}
    selected = selections(dates, panel, params)
    excluded = replace(selected[0].candidates[1], classification="excluded", size_pass=None,
                       absolute_liquidity="unknown", operating_baseline="unknown", activity_pass=None)
    complete = replace(selected[0], candidates=(selected[0].candidates[0], excluded))
    assert run(dates, panel, params, selected=(complete, *selected[1:])).selections[0][1] == ("A",)
    with pytest.raises(ValueError, match="every non-frozen formation bar"):
        run(dates, panel, params, selected=(replace(complete, candidates=(complete.candidates[0],)), *selected[1:]))
    with pytest.raises(ValueError, match="next market session"):
        run(dates, panel, params, selected=(replace(complete, decision_on=dates[4]), *selected[1:]))


def test_two_acquisition_lots_share_successor_but_keep_due_dates_and_block_its_reentry():
    dates, params = fixture()
    params["slots"] = 2
    panel = {"OLD": series(), "NEW": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    panel["NEW"].opens[3:6] = panel["NEW"].closes[3:6] = array("d", [999] * 3)
    first = Lot("first", "OLD", D("0.004"), D(100), dates[2], dates[0], dates[4], dates[0], SNAPSHOT)
    second = replace(first, lot_id="second", shares=D("0.006"), entered_on=dates[1],
                     available_on=dates[1], due_on=dates[8])
    original = ActivityBook(D(0), (first, second), dates[2])
    event = CompulsoryStockExchange("exchange", D("0.5"), basis("OLD", dates[2], 100, 100),
                                    basis("NEW", dates[6], 200, 50), dates[3], dates[6], "synthetic mandatory")
    selected = selections(dates, panel, params)
    result = run(dates, panel, params, initial_book=original, events=(event,), selected=selected)
    waiting = at(result, dates[5])
    assert waiting.cash == 0 and waiting.slot_count == 2
    assert tuple(lot.shares for lot in waiting.lots) == (D("0.008"), D("0.012"))
    assert all(lot.code == "NEW" and lot.mark == D(50) and lot.mark_date == dates[2] for lot in waiting.lots)
    assert waiting.nav == original.nav == D(1)
    delivered = at(result, dates[6])
    assert tuple(lot.lot_id for lot in delivered.lots) == ("second",)
    assert delivered.lots[0].due_on == dates[8] and delivered.lots[0].mark_date == dates[6]
    assert result.selections == ((dates[2], ()), (dates[5], ()))
    assert result.closed_trades == 2 and result.books[-1].lots == ()
    assert dict(result.diagnostics)["delayed_exits"] == 1
    assert original.lots == (first, second) and original.applied_event_ids == ()


def test_payment_precedes_due_sale_and_entry_without_market_fee_or_tax_on_net_cash():
    dates, params = fixture()
    params["slots"] = 1
    panel = {"OLD": series(), "NEW": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    lot = Lot("old", "OLD", D("0.01"), D(100), dates[2], dates[0], dates[6], dates[0], SNAPSHOT)
    initial = ActivityBook(D("0.5"), (lot,), dates[2])
    payment = FinalCashPayment("net-payment", FinalCashDistribution(
        "OLD", dates[2], dates[4], dates[6], D(50), basis("OLD", dates[2], 200, 100),
        "synthetic verified net payment"))
    result = run(dates, panel, params, initial_book=initial, events=(payment,))
    assert at(result, dates[5]).cash == D("0.5") and at(result, dates[5]).slot_count == 1
    entry = at(result, dates[6])
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    assert entry.cash == 0 and entry.slot_count == 1 and entry.lots[0].code == "NEW"
    assert entry.lots[0].shares == D("0.75") / (100 * (1 + cost / 10000))
    assert entry.applied_event_ids == ("net-payment",)
    assert result.closed_trades == 1  # only the later NEW market sale


def test_future_mutation_cannot_change_earlier_choices_or_books():
    dates, params = fixture()
    panel = {"A": series(), "B": series()}
    for s in panel.values():
        s.turnover[2] = s.turnover[5] = 30
    selected = selections(dates, panel, params, legacy_activity=True)
    baseline = run(dates, panel, params, selected=selected)
    for s in panel.values():
        s.opens[7:] = array("d", [9000] * 6)
        s.closes[7:] = array("d", [8000] * 6)
        s.turnover[7:] = array("d", [1e10] * 6)
    changed = run(dates, panel, params, selected=selections(dates, panel, params, legacy_activity=True))
    assert baseline.books[:4] == changed.books[:4]
    assert baseline.selections == changed.selections
    assert baseline.books[0].slot_count == 1


def test_sale_tax_uses_calendar_settlement_across_the_2025_rate_boundary():
    _, params = fixture()
    dates = [date.fromisoformat(day) for day in (
        "2024-12-18", "2024-12-19", "2024-12-20", "2024-12-23", "2024-12-24",
        "2024-12-26", "2024-12-27", "2024-12-30", "2025-01-02", "2025-01-03")]
    params["end"] = dates[7].isoformat()
    result = run(dates, {"A": series(len(dates))}, params)
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    # A Dec 27 execution settles Jan 2: the new 15bp total already applies.
    expected = (1 - (cost + 15) / 10000) / (1 + cost / 10000)
    assert abs(result.books[-1].cash - expected) < D("1e-26")
    assert result.closed_trades == 1


def test_non_session_payment_is_applied_at_next_session_and_zero_nav_retains_cash_book():
    dates, params = fixture()
    # Leave a weekend-like gap before the actual processing session.
    dates = [day if index < 6 else day + timedelta(days=2) for index, day in enumerate(dates)]
    params["end"] = dates[10].isoformat()
    panel = {"OLD": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    lot = Lot("old", "OLD", D("0.01"), D(100), dates[2], dates[0], dates[10], dates[0], SNAPSHOT)
    initial = ActivityBook(D(0), (lot,), dates[2])
    event = FinalCashPayment("verified-zero", FinalCashDistribution(
        "OLD", dates[2], dates[4], dates[5] + timedelta(days=1), D(0),
        basis("OLD", dates[2], 100, 100), "synthetic verified zero net payment"))
    result = run(dates, panel, params, initial_book=initial, events=(event,))
    assert at(result, dates[5]).nav == D(1) and at(result, dates[5]).slot_count == 1
    assert at(result, dates[6]).nav == 0 and at(result, dates[6]).lots == ()
    assert result.books[-1].cash == 0 and result.closed_trades == 0
    assert result.books[-1].applied_event_ids == ("verified-zero",)


def test_effective_exchange_precedes_due_sale_and_delivery_still_needs_non_frozen_fill():
    dates, params = fixture()
    panel = {"OLD": series(), "NEW": series()}
    panel["OLD"].opens[6:] = panel["OLD"].closes[6:] = array("d", [0] * 7)
    panel["NEW"].opens[6:9] = panel["NEW"].closes[6:9] = array("d", [999] * 3)
    panel["NEW"].frozen[8] = 1
    lot = Lot("old", "OLD", D("0.01"), D(100), dates[2], dates[0], dates[6], dates[0], SNAPSHOT)
    initial = ActivityBook(D(0), (lot,), dates[2])
    event = CompulsoryStockExchange("exchange", D("0.5"), basis("OLD", dates[5], 100, 100),
                                    basis("NEW", dates[8], 200, 50), dates[6], dates[8], "synthetic mandatory")
    result = run(dates, panel, params, initial_book=initial, events=(event,))
    for index in (6, 7, 8):
        waiting = at(result, dates[index])
        assert waiting.cash == 0 and waiting.slot_count == 1 and waiting.nav == D(1)
        assert waiting.lots[0].code == "NEW" and waiting.lots[0].mark_date == dates[5]
    assert at(result, dates[9]).lots == ()
    assert dict(result.diagnostics)["pending_exit_sessions"] == 3
    assert dict(result.diagnostics)["delayed_exits"] == 1


@pytest.mark.parametrize("scale", [D("0.1"), D(8)])
def test_future_successor_price_levels_do_not_reach_early_books_or_selection(scale):
    dates, params = fixture()
    panel = {"OLD": series(), "NEW": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    lot = Lot("old", "OLD", D("0.01"), D(100), dates[2], dates[0], dates[10], dates[0], SNAPSHOT)
    initial = ActivityBook(D(0), (lot,), dates[2])
    event = CompulsoryStockExchange("exchange", D("0.5"), basis("OLD", dates[2], 100, 100),
                                    basis("NEW", dates[8], 200, 50), dates[3], dates[8], "synthetic mandatory")
    baseline = run(dates, panel, params, initial_book=initial, events=(event,))
    scaled = replace(event, new_basis=replace(event.new_basis, raw_close=D(200) * scale,
                                              adjusted_close=D(50) * scale))
    changed = run(dates, panel, params, initial_book=initial, events=(scaled,))
    assert baseline.books == changed.books and baseline.selections == changed.selections
    rebased = replace(event, new_basis=replace(event.new_basis, adjusted_close=D(100)))
    frame = run(dates, panel, params, initial_book=initial, events=(rebased,))
    assert frame.selections == baseline.selections
    assert frame.navs[:5] == baseline.navs[:5]
    assert at(frame, dates[3]).lots[0].shares * 2 == at(baseline, dates[3]).lots[0].shares
    assert at(frame, dates[3]).lots[0].mark == at(baseline, dates[3]).lots[0].mark * 2


def test_event_ids_and_mixed_snapshot_inputs_are_refused_before_replay():
    dates, params = fixture()
    panel = {"A": series(), "NEW": series()}
    event = CompulsoryStockExchange("event", D(1), basis("A", dates[2], 100, 100),
                                    basis("NEW", dates[4], 100, 100), dates[3], dates[4], "synthetic mandatory")
    with pytest.raises(ValueError, match="ids must be unique"):
        run(dates, panel, params, events=(event, event))
    wrong = replace(event, old_basis=replace(event.old_basis, dataset_sha256="b" * 64),
                    new_basis=replace(event.new_basis, dataset_sha256="b" * 64))
    with pytest.raises(ValueError, match="same snapshot"):
        run(dates, panel, params, events=(wrong,))


def test_future_event_is_not_consumed_and_a_due_empty_event_is_consumed_once():
    dates, params = fixture()
    params["end"] = dates[7].isoformat()
    panel = {"A": series(), "NEW": series()}
    panel["A"].opens[3:] = panel["A"].closes[3:] = array("d", [0] * 10)
    event = CompulsoryStockExchange("event", D(1), basis("A", dates[2], 100, 100),
                                    basis("NEW", dates[9], 100, 100), dates[8], dates[9], "synthetic mandatory")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=False) for row in s.candidates))
                     for s in selections(dates, panel, params))
    future = run(dates, panel, params, selected=selected, events=(event,))
    assert all(book.applied_event_ids == () for book in future.books)
    due = replace(event, effective_on=dates[3])
    applied = run(dates, panel, params, selected=selected, events=(due,))
    assert all(book.applied_event_ids == ("event",) for book in applied.books)


def test_compulsory_event_rejects_positive_old_quotes_on_first_entry_session():
    dates, params = fixture()
    panel = {"OLD": series(), "NEW": series()}
    event = CompulsoryStockExchange("exchange", D(1), basis("OLD", dates[2], 100, 100),
                                    basis("NEW", dates[4], 100, 100), dates[3], dates[4], "synthetic mandatory")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=row.code == "OLD")
                                                for row in s.candidates))
                     for s in selections(dates, panel, params))
    with pytest.raises(ValueError, match="event/quote conflict for ceased issue: OLD"):
        run(dates, panel, params, selected=selected, events=(event,))


@pytest.mark.parametrize("frozen", [False, True])
def test_valid_old_selection_before_effective_date_can_remain_unfilled_without_replacement(frozen):
    dates, params = fixture()
    params["end"] = dates[7].isoformat()
    codes = sorted(("OLD", "OTHER"), key=lambda code: hashlib.sha256(
        f"{params['seed']}:{dates[2]}:{code}".encode()).digest())
    # Make the retired issue hash first without changing the fixed seed.
    old_code, other_code = codes
    panel = {old_code: series(), other_code: series(), "NEW": series()}
    if frozen:
        panel[old_code].frozen[3:] = bytearray([1] * 10)
    else:
        panel[old_code].opens[3:] = panel[old_code].closes[3:] = array("d", [0] * 10)
    event = CompulsoryStockExchange("exchange", D(1), basis(old_code, dates[2], 100, 100),
                                    basis("NEW", dates[4], 100, 100), dates[3], dates[4], "synthetic mandatory")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=row.code != "NEW")
                                                for row in s.candidates))
                     for s in selections(dates, panel, params))
    result = run(dates, panel, params, selected=selected, events=(event,))
    assert result.selections == ((dates[2], (old_code,)),)
    assert all(book.cash == 1 and book.lots == () for book in result.books)
    assert dict(result.diagnostics)["unfilled_entries"] == 1
    assert result.books[-1].applied_event_ids == ("exchange",)


def test_pre_effective_old_entry_is_not_retroactively_excluded_and_then_converts():
    dates, params = fixture()
    panel = {"OLD": series(), "NEW": series()}
    panel["OLD"].opens[4:] = panel["OLD"].closes[4:] = array("d", [0] * 9)
    event = CompulsoryStockExchange("exchange", D("0.5"), basis("OLD", dates[3], 100, 100),
                                    basis("NEW", dates[6], 200, 50), dates[4], dates[6], "synthetic mandatory")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=row.code == "OLD")
                                                for row in s.candidates))
                     for s in selections(dates, panel, params))
    result = run(dates, panel, params, selected=selected, events=(event,))
    acquired = at(result, dates[3]).lots[0]
    pending = at(result, dates[4]).lots[0]
    assert acquired.code == "OLD" and acquired.entered_on == dates[3]
    assert pending.code == "NEW" and pending.lot_id == acquired.lot_id
    assert pending.shares == acquired.shares * 2 and pending.mark == acquired.mark / 2
    assert pending.available_on == dates[6] and pending.mark_date == dates[3]
    assert abs(at(result, dates[4]).nav - at(result, dates[3]).nav) < D("1e-26")


def test_post_effective_old_quote_cannot_create_a_later_formation_candidate():
    dates, params = fixture()
    panel = {"OLD": series(), "NEW": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    panel["OLD"].opens[5] = panel["OLD"].closes[5] = 100
    event = CompulsoryStockExchange("exchange", D(1), basis("OLD", dates[2], 100, 100),
                                    basis("NEW", dates[4], 100, 100), dates[3], dates[4], "synthetic mandatory")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=False) for row in s.candidates))
                     for s in selections(dates, panel, params))
    with pytest.raises(ValueError, match=f"ceased issue: OLD {dates[5]}"):
        run(dates, panel, params, selected=selected, events=(event,))


def test_future_final_payment_rejects_trading_after_declared_last_trading_date():
    dates, params = fixture()
    panel = {"OLD": series()}
    lot = Lot("old", "OLD", D("0.01"), D(100), dates[2], dates[0], dates[4], dates[0], SNAPSHOT)
    initial = ActivityBook(D(0), (lot,), dates[2])
    event = FinalCashPayment("payment", FinalCashDistribution(
        "OLD", dates[2], dates[3], dates[6], D(50), basis("OLD", dates[2], 100, 100),
        "synthetic verified final net payment"))
    with pytest.raises(ValueError, match=f"ceased issue: OLD {dates[3]}"):
        run(dates, panel, params, initial_book=initial, events=(event,))
    assert initial.cash == 0 and initial.lots == (lot,) and initial.applied_event_ids == ()


@pytest.mark.parametrize("formation_state", ["active", "absent", "frozen"])
def test_first_formation_quote_is_checked_against_earlier_final_trading_date(formation_state):
    dates, params = fixture()
    panel = {"OLD": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    if formation_state == "absent":
        panel["OLD"].opens[2] = panel["OLD"].closes[2] = 0
    elif formation_state == "frozen":
        panel["OLD"].frozen[2] = 1
    event = FinalCashPayment("payment", FinalCashDistribution(
        "OLD", dates[1], dates[2], dates[6], D(50), basis("OLD", dates[1], 100, 100),
        "synthetic verified final net payment"))
    if formation_state == "active":
        with pytest.raises(ValueError, match=f"ceased issue: OLD {dates[2]}"):
            run(dates, panel, params, events=(event,))
    else:
        result = run(dates, panel, params, events=(event,))
        assert all(book.cash == 1 and book.lots == () for book in result.books)
        assert all(chosen == () for _, chosen in result.selections)


def test_reverse_input_events_still_apply_chronologically_to_the_same_acquisition_lot():
    dates, params = fixture()
    panel = {"A": series(), "B": series(price=50), "C": series(price=50)}
    panel["A"].opens[3:] = panel["A"].closes[3:] = array("d", [0] * 10)
    panel["B"].opens[5:] = panel["B"].closes[5:] = array("d", [0] * 8)
    lot = Lot("acquisition", "A", D("0.01"), D(100), dates[2], dates[0], dates[12], dates[0], SNAPSHOT)
    initial = ActivityBook(D(0), (lot,), dates[2])
    first = CompulsoryStockExchange("a-b", D(1), basis("A", dates[2], 100, 100),
                                    basis("B", dates[4], 100, 50), dates[3], dates[4], "synthetic first")
    second = CompulsoryStockExchange("b-c", D("0.5"), basis("B", dates[4], 100, 50),
                                     basis("C", dates[6], 200, 50), dates[5], dates[6], "synthetic second")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=False) for row in s.candidates))
                     for s in selections(dates, panel, params))
    result = run(dates, panel, params, selected=selected, initial_book=initial, events=(second, first))
    assert at(result, dates[3]).applied_event_ids == ("a-b",)
    assert at(result, dates[3]).lots[0].code == "B"
    final = result.books[-1]
    assert final.applied_event_ids == ("a-b", "b-c") and final.cash == 0 and final.nav == 1
    assert final.lots[0].code == "C" and final.lots[0].shares == D("0.02")
    assert final.lots[0].lot_id == "acquisition" and final.lots[0].due_on == dates[12]


def test_free_slot_allocation_is_capped_by_available_cash_without_leverage():
    dates, params = fixture()
    params["slots"] = 2
    panel = {"HELD": series(), "ENTRY": series()}
    lot = Lot("held", "HELD", D("0.009"), D(100), dates[2], dates[0], dates[12], dates[0], SNAPSHOT)
    initial = ActivityBook(D("0.1"), (lot,), dates[2])
    result = run(dates, panel, params, initial_book=initial)
    first = at(result, dates[3])
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    entry = next(lot for lot in first.lots if lot.code == "ENTRY")
    # Opening NAV is 1, allocation .5, but only .1 cash exists.
    assert initial.nav == 1 and first.cash == 0 and first.slot_count == 2
    assert entry.shares == D("0.1") / (100 * (1 + cost / 10000))
    assert entry.shares * 100 * (1 + cost / 10000) == D("0.1")


def test_unavailable_successor_open_cannot_change_another_entry_budget():
    dates, params = fixture()
    params["slots"] = 2
    panel = {"OLD": series(), "NEW": series(price=50), "ENTRY": series()}
    panel["OLD"].opens[3:] = panel["OLD"].closes[3:] = array("d", [0] * 10)
    panel["NEW"].opens[3:8] = array("d", [999] * 5)
    lot = Lot("old", "OLD", D("0.01"), D(100), dates[2], dates[0], dates[12], dates[0], SNAPSHOT)
    initial = ActivityBook(D(3), (lot,), dates[2])
    event = CompulsoryStockExchange("exchange", D("0.5"), basis("OLD", dates[2], 100, 100),
                                    basis("NEW", dates[8], 200, 50), dates[3], dates[8], "synthetic mandatory")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=
                                                       row.code == "ENTRY" and s.formation_on == dates[2])
                                                for row in s.candidates))
                     for s in selections(dates, panel, params))
    baseline = run(dates, panel, params, selected=selected, initial_book=initial, events=(event,))
    panel["NEW"].opens[3:8] = array("d", [1000000] * 5)
    changed = run(dates, panel, params, selected=selected, initial_book=initial, events=(event,))
    first = at(baseline, dates[3])
    entry = next(lot for lot in first.lots if lot.code == "ENTRY")
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    # Cash3 + carried OLD entitlement value1, two slots: entry budget is 2.
    assert first.cash == 1 and entry.shares == D(2) / (100 * (1 + cost / 10000))
    assert changed.books == baseline.books and changed.selections == baseline.selections


def test_event_quote_consistency_does_not_read_settlement_only_price_rows():
    dates, params = fixture()
    end = dates.index(date.fromisoformat(params["end"]))

    class ReplayOnly:
        def __init__(self, values):
            self.values = values

        def __len__(self):
            return len(self.values)

        def __getitem__(self, index):
            assert index <= end, "settlement-only price row was read"
            return self.values[index]

    s = series()
    panel = {"OLD": Series(ReplayOnly(s.opens), ReplayOnly(s.closes), s.turnover,
                           ReplayOnly(s.frozen), ReplayOnly(s.locked)), "NEW": series()}
    event = CompulsoryStockExchange("future", D(1), basis("OLD", dates[10], 100, 100),
                                    basis("NEW", dates[12], 100, 100), dates[11], dates[12], "synthetic future")
    selected = tuple(replace(s, candidates=tuple(replace(row, activity_pass=False) for row in s.candidates))
                     for s in selections(dates, panel, params))
    result = run(dates, panel, params, selected=selected, events=(event,))
    assert result.books[-1].as_of == dates[end] and result.books[-1].applied_event_ids == ()
