"""Hand-built timing/provenance contracts; no actual observations or trial."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path

import pytest

from research.activity_accounting import FinalCashDistribution, IdentityPeriod, Lot, PriceBasis
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment
from research.activity_replay import (
    DecimalSeries, SyntheticCandidate, SyntheticSelection, replay_synthetic,
)
from research.activity_screen import PublishedValue, ScreenObservation, screen_synthetic
from research.activity_timing import AvailabilityMetadata, KST, SessionLagPolicy
from research.krx_tax_schedule import KOSPI, total_bp


def calendar():
    # Explicit toy complete-session calendar: Oct 3/9 are absent holidays and
    # Oct 5/6/12/13 are absent weekends. Nothing derives sessions from weekdays.
    return tuple(date(2024, 10, day) for day in (1, 2, 4, 7, 8, 10, 11, 14, 15, 16, 17, 18))


def at(day, hour=8, minute=30):
    return datetime.combine(day, time(hour, minute), KST)


def metadata(day, available, **changes):
    values = dict(observation_date=day, available_at=available, retrieved_at="unknown",
                  source="hand-built source field", is_final=None, data_vintage=None,
                  evidence_level="assumed", source_public_available_at=None,
                  availability_policy="registered historical 08:30 assumption",
                  evidence_reference="synthetic approval reference")
    values.update(changes)
    return AvailabilityMetadata(**values)


def screen_inputs(policy=SessionLagPolicy()):
    days = calendar()
    cutoff = policy.selection_at(days, 2)
    modeled = SessionLagPolicy().selection_at(days, 2)
    quotes = {day: ScreenObservation("observed", D(30 if i == 2 else 10), None,
                                    metadata(day, modeled))
              for i, day in enumerate(days[:3])}
    source = {day: ScreenObservation("observed", D(100), None, metadata(day, modeled))
              for day in days[:2]}
    return dict(calendar=days, formation_index=2, params={"lookback": 2, "threshold": 3},
                quotes={"A": quotes}, liquidity={"A": source},
                capitalization={"A": PublishedValue(D(1000), None, metadata(days[2], modeled))},
                periods=(IdentityPeriod("A", days[0], days[-1] + timedelta(days=1), days[0],
                                        True, "reviewed synthetic classification"),), listings={},
                liquidity_lookback=2, liquidity_floor=D(100), capitalization_floor=D(1000),
                decision_at=cutoff, timing_policy=policy)


def replay_inputs(policy):
    days = calendar()
    spec = Path(__file__).resolve().parents[2] / "configs/research/discovery/activity-calibration-v1.json"
    params = json.loads(spec.read_text())["parameters"]
    params.update(lookback=2, holding_sessions=3, slots=1, end=days[9].isoformat())
    prices = tuple(D(200 if i == 4 else 100) for i in range(len(days)))
    series = DecimalSeries(prices, prices, (D(10),) * len(days), (False,) * len(days),
                           (False,) * len(days), (True,) * len(days))
    formations = policy.formation_indices(days, lookback=2, holding_sessions=3, end_index=9)
    selections = tuple(SyntheticSelection(
        days[i], policy.execution_on(days, i),
        (SyntheticCandidate("A", "operating_common", days[i], True, "pass", "pass", True),),
        policy.selection_at(days, i)) for i in formations)
    return days, {"A": series}, params, selections


def replay(policy, **changes):
    days, panel, params, selected = replay_inputs(policy)
    return replay_synthetic(days, panel, params, changes.pop("selections", selected),
                            dataset_sha256="a" * 64, timing_policy=policy, **changes)


def test_session_lags_skip_the_supplied_weekend_and_holiday_sessions():
    days = calendar()
    assert SessionLagPolicy(1).execution_on(days, 1) == date(2024, 10, 4)
    assert SessionLagPolicy(2).execution_on(days, 1) == date(2024, 10, 7)
    assert SessionLagPolicy(2).execution_on(days, 2) == date(2024, 10, 8)
    assert SessionLagPolicy(1).selection_at(days, 4) == at(date(2024, 10, 10))
    assert SessionLagPolicy(2).selection_at(days, 2).time() == time(8, 30)


@pytest.mark.parametrize("lag", [0, 3, -1, True, 1.0, "2"])
def test_only_explicit_one_or_two_session_delays_are_supported(lag):
    with pytest.raises(ValueError, match="session_lag"):
        SessionLagPolicy(lag)


@pytest.mark.parametrize("days,index", [((), 0), ((date(2024, 1, 1),) * 3, 0),
                                        (calendar(), -1), (calendar(), True), (calendar(), 11)])
def test_calendar_and_later_session_must_exist(days, index):
    with pytest.raises(ValueError):
        SessionLagPolicy(2).selection_at(days, index)


def test_unknown_actual_release_retrieval_and_vintage_stay_explicitly_unknown():
    day, cutoff = calendar()[2], at(calendar()[3])
    item = metadata(day, cutoff)
    item.require_available(cutoff, observation_date=day)
    assert item.source_public_available_at is None and item.retrieved_at == "unknown"
    assert item.data_vintage is None and item.is_final is None and item.evidence_level == "assumed"
    with pytest.raises(FrozenInstanceError):
        item.available_at = cutoff + timedelta(days=1)
    assert screen_synthetic(**screen_inputs()).selection.candidates[0].activity_pass is True


@pytest.mark.parametrize("field", ["available_at", "retrieved_at", "source_public_available_at"])
def test_naive_metadata_timestamps_are_refused(field):
    with pytest.raises(ValueError, match="aware"):
        metadata(calendar()[2], at(calendar()[3]), **{field: datetime(2024, 10, 7, 8, 30)})


@pytest.mark.parametrize("changes", [
    {"retrieved_at": None}, {"retrieved_at": ""}, {"data_vintage": ""}, {"source": " "},
    {"evidence_level": "certain"}, {"availability_policy": None}, {"evidence_reference": ""},
    {"is_final": 1}, {"evidence_level": "confirmed"},
])
def test_missing_or_invented_provenance_declarations_are_refused(changes):
    with pytest.raises(ValueError):
        metadata(calendar()[2], at(calendar()[3]), **changes)


@pytest.mark.parametrize("level", ["confirmed", "inferred", "assumed"])
def test_evidence_label_never_overrides_known_later_actual_availability(level):
    with pytest.raises(ValueError, match="known actual availability"):
        metadata(calendar()[2], at(calendar()[3]), evidence_level=level,
                 source_public_available_at=at(calendar()[3]) + timedelta(seconds=1))


def test_confirmed_or_inferred_metadata_preserves_its_actual_version_and_reference():
    day, cutoff = calendar()[2], at(calendar()[3])
    for level in ("confirmed", "inferred"):
        item = metadata(day, cutoff, evidence_level=level, source_public_available_at=at(day, 18, 10),
                        retrieved_at=at(calendar()[-1], 12), data_vintage="explicit fixture version")
        item.require_available(cutoff, observation_date=day)
        assert item.source_public_available_at == at(day, 18, 10)
        assert item.retrieved_at == at(calendar()[-1], 12)
        assert item.data_vintage == "explicit fixture version"


def test_future_observation_and_naive_selection_are_refused():
    days = calendar()
    item = metadata(days[4], at(days[4]))
    with pytest.raises(ValueError, match="future"):
        item.require_available(at(days[3]), observation_date=days[4])
    with pytest.raises(ValueError, match="aware"):
        metadata(days[2], at(days[3])).require_available(datetime(2024, 10, 7, 8, 30),
                                                       observation_date=days[2])


@pytest.mark.parametrize("field", ["formation_quote", "prior_quote", "liquidity", "capitalization"])
def test_each_mixed_source_field_must_be_available_at_the_selection_cutoff(field):
    inputs = screen_inputs()
    cutoff, days = inputs["decision_at"], inputs["calendar"]
    # Early retrieval or another field's earlier release cannot repair this one.
    late = cutoff + timedelta(seconds=1)
    if field == "capitalization":
        inputs["capitalization"]["A"] = PublishedValue(D(1000), None, metadata(days[2], late))
    else:
        group = "liquidity" if field == "liquidity" else "quotes"
        day = days[2] if field == "formation_quote" else days[1]
        row = inputs[group]["A"][day]
        inputs[group]["A"][day] = replace(row, availability=metadata(day, late))
    with pytest.raises(ValueError, match="available_at is later"):
        screen_synthetic(**inputs)


def test_metadata_cannot_disagree_with_the_consumed_date_or_legacy_public_time():
    inputs = screen_inputs()
    cap = inputs["capitalization"]["A"]
    inputs["capitalization"]["A"] = replace(cap, availability=replace(
        cap.availability, observation_date=calendar()[4], available_at=at(calendar()[4]),
    ))
    with pytest.raises(ValueError, match="observation_date"):
        screen_synthetic(**inputs)
    inputs["capitalization"]["A"] = replace(cap, public_available_at=inputs["decision_at"])
    with pytest.raises(ValueError, match="conflicts with provenance"):
        screen_synthetic(**inputs)


def test_d2_uses_the_same_formation_fields_but_a_later_cutoff_and_entry_date():
    first = screen_synthetic(**screen_inputs(SessionLagPolicy(1, paired=True))).selection
    second = screen_synthetic(**screen_inputs(SessionLagPolicy(2, paired=True))).selection
    assert first.formation_on == second.formation_on == calendar()[2]
    assert first.candidates == second.candidates
    assert first.decision_on == calendar()[3] and second.decision_on == calendar()[4]
    assert first.selection_at == at(calendar()[3]) and second.selection_at == at(calendar()[4])


def test_paired_terminal_horizon_holding_length_and_evaluation_dates_are_equal():
    days = calendar()
    one, two = SessionLagPolicy(1, paired=True), SessionLagPolicy(2, paired=True)
    assert SessionLagPolicy().formation_indices(days, lookback=2, holding_sessions=3, end_index=9) == (2, 5)
    assert one.formation_indices(days, lookback=2, holding_sessions=3, end_index=9) == (2,)
    assert two.formation_indices(days, lookback=2, holding_sessions=3, end_index=9) == (2,)
    first, second = replay(one), replay(two)
    assert tuple(day for day, _ in first.selections) == tuple(day for day, _ in second.selections) == (days[2],)
    assert tuple(book.as_of for book in first.books) == tuple(book.as_of for book in second.books) == days[3:10]
    assert first.closed_trades == second.closed_trades == 1
    for result, entry in ((first, 3), (second, 4)):
        lot = next(book.lots[0] for book in result.books if book.lots)
        assert lot.entered_on == days[entry] and lot.due_on == days[entry + 3]
        assert days.index(lot.due_on) - days.index(lot.entered_on) == 3
        assert result.books[-1].lots == ()
        _, _, params, _ = replay_inputs(one)
        cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
        entry_price = D(100 if entry == 3 else 200)
        assert lot.shares == 1 / (entry_price * (1 + cost / 10000))
        tax = D(str(total_bp(KOSPI, days[entry + 3], days)))
        assert result.books[-1].cash == lot.shares * 100 * (1 - (cost + tax) / 10000)


def test_paired_policy_rejects_extra_formation_and_wrong_cutoff():
    policy = SessionLagPolicy(1, paired=True)
    _, _, _, unpaired = replay_inputs(SessionLagPolicy())
    with pytest.raises(ValueError, match="every v1 formation"):
        replay(policy, selections=unpaired)
    _, _, _, selected = replay_inputs(policy)
    with pytest.raises(ValueError, match="08:30"):
        replay(policy, selections=(replace(selected[0], selection_at=selected[0].selection_at
                                           + timedelta(seconds=1)),))
    with pytest.raises(ValueError, match="requires selection_at"):
        replay(policy, selections=(replace(selected[0], selection_at=None),))


@pytest.mark.parametrize("field", ["formation_quote", "prior_quote", "liquidity", "capitalization"])
def test_explicit_policy_requires_provenance_on_every_consumed_field(field):
    inputs = screen_inputs()
    cutoff, days = inputs["decision_at"], inputs["calendar"]
    if field == "capitalization":
        inputs["capitalization"]["A"] = PublishedValue(D(1000), cutoff)
    else:
        group = "liquidity" if field == "liquidity" else "quotes"
        day = days[2] if field == "formation_quote" else days[1]
        row = inputs[group]["A"][day]
        inputs[group]["A"][day] = replace(row, availability=None, public_available_at=cutoff)
    with pytest.raises(ValueError, match="requires field availability metadata"):
        screen_synthetic(**inputs)


def test_default_path_still_accepts_legacy_timestamps_without_new_provenance():
    inputs = screen_inputs()
    inputs.pop("timing_policy")
    for group in ("quotes", "liquidity"):
        for day, row in inputs[group]["A"].items():
            inputs[group]["A"][day] = replace(row, availability=None,
                                               public_available_at=inputs["decision_at"])
    inputs["capitalization"]["A"] = PublishedValue(D(1000), inputs["decision_at"])
    result = screen_synthetic(**inputs)
    assert result.selection.candidates[0].activity_pass is True
    assert result.selection.selection_at is None


def test_new_policy_retains_complete_pool_and_zero_null_guards():
    policy = SessionLagPolicy(2, paired=True)
    days, panel, params, selected = replay_inputs(policy)
    with pytest.raises(ValueError, match="every non-frozen formation"):
        replay(policy, selections=(replace(selected[0], candidates=()),))
    quotes = panel["A"]
    with pytest.raises(ValueError, match="positive prices"):
        replace(quotes, opens=(D(0), *quotes.opens[1:]))
    null_quote = replace(quotes, turnover=(*quotes.turnover[:4], None, *quotes.turnover[5:]))
    with pytest.raises(ValueError, match="unresolved observed Decimal quote"):
        replay_synthetic(days, {"A": null_quote}, params, selected, dataset_sha256="a" * 64,
                         timing_policy=policy)
    inputs = screen_inputs(policy)
    inputs["liquidity"]["A"][days[0]] = replace(inputs["liquidity"]["A"][days[0]], turnover=None)
    with pytest.raises(ValueError, match="nonnegative Decimal"):
        screen_synthetic(**inputs)


def test_unused_future_observations_cannot_change_an_earlier_screen():
    inputs = screen_inputs(SessionLagPolicy(2, paired=True))
    first = screen_synthetic(**inputs)
    day = calendar()[-1]
    inputs["quotes"]["A"][day] = ScreenObservation("observed", D("1e40"), None,
                                                  metadata(day, at(day)))
    inputs["liquidity"]["A"][day] = ScreenObservation("missing", None, None)
    assert screen_synthetic(**inputs) == first


def absent_on(series, index):
    values = {name: tuple(None if i == index else value for i, value in enumerate(getattr(series, name)))
              for name in ("opens", "closes", "turnover", "frozen", "locked")}
    return replace(series, **values,
                   observed=tuple(i != index and value for i, value in enumerate(series.observed)))


def target_case(policy, *, active=("A", "B"), initial_code="HELD"):
    days, panel, params, _ = replay_inputs(policy)
    plain = replace(panel["A"], opens=(D(100),) * len(days), closes=(D(100),) * len(days))
    panel = {code: plain for code in (*active, initial_code)}
    selected = (SyntheticSelection(
        days[2], policy.execution_on(days, 2),
        tuple(SyntheticCandidate(code, "operating_common", days[2], True, "pass", "pass",
                                 code in active) for code in panel), policy.selection_at(days, 2)),)
    entry = 2 + policy.session_lag
    initial = ActivityBook(D(0), (Lot("initial", initial_code, D(1), D(100), days[2], days[0],
                                     days[entry], days[0], "a" * 64),), days[2])
    return days, panel, params, selected, initial, entry


def run_case(policy, panel, params, selected, initial, **changes):
    return replay_synthetic(calendar(), panel, params, selected, dataset_sha256="a" * 64,
                            timing_policy=policy, initial_book=initial, **changes)


@pytest.mark.parametrize("lag", [1, 2])
@pytest.mark.parametrize("failure", ["absent", "frozen"])
def test_due_sale_failure_changes_fills_but_never_the_preopen_targets(lag, failure):
    policy = SessionLagPolicy(lag, paired=True)
    days, panel, params, selected, initial, entry = target_case(policy)
    baseline = run_case(policy, panel, params, selected, initial)
    if failure == "absent":
        panel["HELD"] = absent_on(panel["HELD"], entry)
    else:
        panel["HELD"] = replace(panel["HELD"],
                                frozen=tuple(i == entry for i in range(len(days))))
    failed = run_case(policy, panel, params, selected, initial)
    target = min(("A", "B"), key=lambda code: hashlib.sha256(
        f"{params['seed']}:{days[2]}:{code}".encode()).digest())
    assert baseline.selections == failed.selections == ((days[2], (target,)),)
    first = next(book for book in baseline.books if book.as_of == days[entry])
    other = next(book for book in failed.books if book.as_of == days[entry])
    assert tuple(lot.code for lot in first.lots) == (target,)
    assert tuple(lot.code for lot in other.lots) == ("HELD",) and other.cash == 0
    assert dict(baseline.diagnostics)["entries"] == 1
    assert dict(failed.diagnostics).get("entries", 0) == 0
    assert dict(failed.diagnostics)["unfilled_capacity_targets"] == 1
    assert all(book.slot_count <= params["slots"] for book in failed.books)


@pytest.mark.parametrize("lag", [1, 2])
def test_selection_is_frozen_before_any_execution_session_quote_is_read(lag, monkeypatch):
    import research.activity_replay as module

    policy = SessionLagPolicy(lag, paired=True)
    _, panel, params, selected, initial, entry = target_case(policy)
    original_select, original_quote = module._selected, module._quote
    chosen = False

    def select(*args, **kwargs):
        nonlocal chosen
        result = original_select(*args, **kwargs)
        chosen = True
        return result

    def quote(series, index):
        if index == entry:
            assert chosen, "execution-session quote leaked into the 08:30 target choice"
        return original_quote(series, index)

    monkeypatch.setattr(module, "_selected", select)
    monkeypatch.setattr(module, "_quote", quote)
    run_case(policy, panel, params, selected, initial)


@pytest.mark.parametrize("lag", [1, 2])
def test_predeclared_same_code_reentry_fails_if_the_due_sale_cannot_fill(lag):
    policy = SessionLagPolicy(lag, paired=True)
    days, panel, params, selected, initial, entry = target_case(policy, active=("HELD",))
    baseline = run_case(policy, panel, params, selected, initial)
    panel["HELD"] = absent_on(panel["HELD"], entry)
    failed = run_case(policy, panel, params, selected, initial)
    assert baseline.selections == failed.selections == ((days[2], ("HELD",)),)
    assert dict(baseline.diagnostics)["entries"] == 1
    assert dict(failed.diagnostics).get("entries", 0) == 0
    assert dict(failed.diagnostics)["unfilled_held_targets"] == 1
    book = next(book for book in failed.books if book.as_of == days[entry])
    assert book.lots[0].lot_id == "initial" and book.slot_count == 1


def test_d2_failed_target_has_no_hindsight_substitute_or_later_retry():
    policy = SessionLagPolicy(2, paired=True)
    days, panel, params, selected, initial, entry = target_case(policy)
    initial = replace(initial, cash=D(100), lots=())
    target = min(("A", "B"), key=lambda code: hashlib.sha256(
        f"{params['seed']}:{days[2]}:{code}".encode()).digest())
    panel[target] = absent_on(panel[target], entry)
    result = run_case(policy, panel, params, selected, initial)
    assert result.selections == ((days[2], (target,)),)
    assert all(book.cash == 100 and book.lots == () for book in result.books)
    assert dict(result.diagnostics)["unfilled_entries"] == 1
    assert dict(result.diagnostics).get("entries", 0) == 0


def test_compulsory_exchange_between_formation_and_d2_does_not_replace_the_old_target():
    policy = SessionLagPolicy(2, paired=True)
    days, panel, params, selected, initial, entry = target_case(policy, active=("OLD",),
                                                               initial_code="NEW")
    initial = replace(initial, cash=D(100), lots=())
    for index in range(3, len(days)):
        panel["OLD"] = absent_on(panel["OLD"], index)
    basis = lambda code, day: PriceBasis(code, day, D(100), D(100), "a" * 64, "synthetic basis")
    event = CompulsoryStockExchange("exchange", D(1), basis("OLD", days[2]),
                                    basis("NEW", days[4]), days[3], days[4], "synthetic event")
    result = run_case(policy, panel, params, selected, initial, events=(event,))
    assert result.selections == ((days[2], ("OLD",)),)
    assert all(book.cash == 100 and book.lots == () for book in result.books)
    assert dict(result.diagnostics)["unfilled_entries"] == 1
    assert result.books[-1].applied_event_ids == ("exchange",)


def test_today_exchange_can_invalidate_a_predeclared_target_without_freeing_its_slot():
    policy = SessionLagPolicy(2, paired=True)
    days, panel, params, selected, initial, entry = target_case(policy, active=("NEW",),
                                                               initial_code="OLD")
    for index in range(entry, len(days)):
        panel["OLD"] = absent_on(panel["OLD"], index)
    basis = lambda code, day: PriceBasis(code, day, D(100), D(100), "a" * 64, "synthetic basis")
    event = CompulsoryStockExchange("exchange", D(1), basis("OLD", days[entry - 1]),
                                    basis("NEW", days[entry + 1]), days[entry], days[entry + 1],
                                    "synthetic event")
    result = run_case(policy, panel, params, selected, initial, events=(event,))
    assert result.selections == ((days[2], ("NEW",)),)
    book = next(book for book in result.books if book.as_of == days[entry])
    assert book.cash == 0 and book.slot_count == 1
    assert book.lots[0].code == "NEW" and book.lots[0].lot_id == "initial"
    assert book.lots[0].available_on == days[entry + 1]
    assert dict(result.diagnostics)["unfilled_held_targets"] == 1
    assert dict(result.diagnostics).get("entries", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
def test_actual_final_payment_date_changes_fills_without_changing_preopen_targets(lag):
    policy = SessionLagPolicy(lag, paired=True)
    days, panel, params, selected, initial, entry = target_case(policy)
    for index in range(3, len(days)):
        panel["HELD"] = absent_on(panel["HELD"], index)
    basis = PriceBasis("HELD", days[2], D(100), D(100), "a" * 64, "synthetic basis")
    payout = FinalCashPayment("final", FinalCashDistribution(
        "HELD", days[2], days[entry], days[7], D(100), basis, "verified synthetic payment"))
    late = run_case(policy, panel, params, selected, initial, events=(payout,))
    paid_today = replace(payout, distribution=replace(payout.distribution, paid_on=days[entry]))
    early = run_case(policy, panel, params, selected, initial, events=(paid_today,))
    target = min(("A", "B"), key=lambda code: hashlib.sha256(
        f"{params['seed']}:{days[2]}:{code}".encode()).digest())
    assert early.selections == late.selections == ((days[2], (target,)),)
    early_entry = next(book for book in early.books if book.as_of == days[entry])
    late_entry = next(book for book in late.books if book.as_of == days[entry])
    assert tuple(lot.code for lot in early_entry.lots) == (target,)
    assert early_entry.cash == 0
    assert tuple(lot.code for lot in late_entry.lots) == ("HELD",) and late_entry.cash == 0
    assert dict(early.diagnostics)["entries"] == 1
    assert dict(late.diagnostics).get("entries", 0) == 0
    assert dict(late.diagnostics)["unfilled_capacity_targets"] == 1
    for book in late.books:
        if book.as_of < days[7]:
            assert book.cash == 0 and book.slot_count == 1 and book.lots[0].code == "HELD"
        else:
            assert book.cash == 100 and book.lots == ()
    assert all(book.cash == 0 and book.lots[0].code == "HELD"
               for book in early.books if book.as_of < days[entry])
    assert all(book.slot_count <= params["slots"] for book in (*early.books, *late.books))
