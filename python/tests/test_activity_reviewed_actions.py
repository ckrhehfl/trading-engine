"""Synthetic schema checks only: no market source, replay, DB, or actual approval."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

import pytest


from research.activity_accounting import Lot
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment
from research.activity_reviewed_actions import reviewed_actions


CALENDAR = tuple(date.fromisoformat(text) for text in (
    "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09"))
ACTIVITY, QUOTE, SCOPE = "a" * 64, "b" * 64, "c" * 64
A, AI, B, BI = "000001", "KR7000001000", "000002", "KR7000002000"


def reference(code=A, isin=AI, start="2020-01-02", end="2020-01-08",
              finding="confirmed_no_compulsory_action", ids=()):
    return dict(raw_sha256="d" * 64, review_sha256="e" * 64,
                coordinate="synthetic-document:section-2", code=code, isin=isin,
                start=start, end=end, reviewed_on="2020-01-10", finding=finding, event_ids=list(ids))


def window(code=A, isin=AI, start="2020-01-02", end="2020-01-08", ids=(), ref="complete"):
    return dict(code=code, isin=isin, start=start, end=end, event_ids=list(ids), evidence_refs=[ref],
                review_state="reviewed_supported_events" if ids else "reviewed_no_event")


def fixture():
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete",
                    activity_snapshot_sha256=ACTIVITY, quote_snapshot_sha256=QUOTE,
                    required_scope_sha256=SCOPE,
                    calendar_sha256=sha256(json.dumps([day.isoformat() for day in CALENDAR],
                                                      separators=(",", ":")).encode()).hexdigest(),
                    windows=[window()], events=[], noops=[], unresolved=[])
    evidence = {"references": {"complete": reference()}, "bases": {}}
    required = (dict(code=A, isin=AI, start="2020-01-02", end="2020-01-08"),)
    return coverage, evidence, required


def call(coverage, evidence, required, quote=QUOTE):
    return reviewed_actions(coverage, evidence, expected_windows=required,
        calendar=CALENDAR, activity_snapshot_sha256=ACTIVITY,
        quote_snapshot_sha256=quote, read_scope_sha256=SCOPE)


def price_basis(evidence, name, code, isin, session, role):
    evidence["bases"][name] = dict(code=code, isin=isin, session=session, role=role,
        raw_close="100.2500", adjusted_close="50.1250", quote_snapshot_sha256=QUOTE,
        evidence_ref=name)
    evidence["references"][name] = reference(code, isin, session, session, "verified_" + role + "_basis")


def exchange_fixture():
    coverage, evidence, required = fixture()
    coverage["events"] = [dict(event_id="exchange-1", kind="compulsory_stock_exchange",
        status="verified_effective", code=A, isin=AI, successor_code=B, successor_isin=BI,
        ratio="1.12500000000000000000000001", old_basis_id="old", new_basis_id="new",
        effective_on="2020-01-04", available_on="2020-01-06", evidence_ref="event")]
    coverage["windows"] = [window(ids=("exchange-1",)),
        window(B, BI, "2020-01-04", "2020-01-08", ref="successor-complete")]
    evidence["references"]["complete"] = reference(finding="confirmed_supported_events_complete", ids=("exchange-1",))
    evidence["references"]["successor-complete"] = reference(B, BI, "2020-01-04", "2020-01-08")
    evidence["references"]["event"] = reference(start="2020-01-04", end="2020-01-04",
        finding="verified_compulsory_stock_exchange", ids=("exchange-1",))
    price_basis(evidence, "old", A, AI, "2020-01-03", "last_observable_pre_event")
    price_basis(evidence, "new", B, BI, "2020-01-06", "successor_availability")
    return coverage, evidence, required


def payment_fixture():
    coverage, evidence, required = fixture()
    coverage["events"] = [dict(event_id="payment-1", kind="final_cash_payment", status="verified_paid",
        code=A, isin=AI, last_trading_on="2020-01-03", record_on="2020-01-04",
        paid_on="2020-01-07", net_cash_per_raw_share="0", basis_id="last", evidence_ref="event")]
    coverage["windows"] = [window(ids=("payment-1",))]
    evidence["references"]["complete"] = reference(finding="confirmed_supported_events_complete", ids=("payment-1",))
    evidence["references"]["event"] = reference(start="2020-01-07", end="2020-01-07",
        finding="verified_final_cash_payment", ids=("payment-1",))
    price_basis(evidence, "last", A, AI, "2020-01-03", "last_trading")
    return coverage, evidence, required


def test_pure_complete_no_action_preserves_overlapping_requirements_and_inputs(monkeypatch):
    coverage, evidence, required = fixture()
    before = deepcopy((coverage, evidence, required))
    required += (dict(code=A, isin=AI, start="2020-01-03", end="2020-01-07"),)
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: pytest.fail("unexpected IO"))
    assert call(coverage, evidence, required) == ((), {})
    assert (coverage, evidence, required[:1]) == before


def test_split_complete_coverage_including_weekend():
    coverage, evidence, required = fixture()
    coverage["windows"] = [window(end="2020-01-04"), window(start="2020-01-05")]
    assert call(coverage, evidence, required) == ((), {})


@pytest.mark.parametrize("case", [fixture, exchange_fixture, payment_fixture], ids=["no-event", "exchange", "payment"])
def test_inferred_complete_intervals_preserve_evidence_strength_and_verified_events(case):
    coverage, evidence, required = case()
    for row in coverage["windows"]:
        finding = "inferred_supported_events_complete" if row["event_ids"] else "inferred_no_compulsory_action"
        for ref_id in row["evidence_refs"]:
            evidence["references"][ref_id]["finding"] = finding
    before = deepcopy((coverage, evidence, required))
    events, _ = call(coverage, evidence, required)
    assert tuple(event.event_id for event in events) == tuple(row["event_id"] for row in coverage["events"])
    assert (coverage, evidence, required) == before
    assert all(evidence["references"][ref_id]["finding"].startswith("inferred_")
               for row in coverage["windows"] for ref_id in row["evidence_refs"])


def test_confirmed_and_inferred_complete_references_can_support_the_same_interval():
    coverage, evidence, required = fixture()
    evidence["references"]["inference"] = reference(finding="inferred_no_compulsory_action")
    coverage["windows"][0]["evidence_refs"].append("inference")
    before = deepcopy((coverage, evidence, required))
    assert call(coverage, evidence, required) == ((), {})
    assert (coverage, evidence, required) == before


@pytest.mark.parametrize("case,wrong_finding", [
    (fixture, "inferred_supported_events_complete"),
    (exchange_fixture, "inferred_no_compulsory_action"),
    (payment_fixture, "inferred_no_compulsory_action"),
])
def test_inferred_completeness_cannot_change_the_interval_event_kind(case, wrong_finding):
    coverage, evidence, required = case()
    evidence["references"]["complete"]["finding"] = wrong_finding
    with pytest.raises(ValueError, match="evidence finding"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("finding", ["unknown", "assumed_no_compulsory_action", "reviewed_no_event",
                                     "inferred_no_event", "reviewed_name_change"])
def test_unknown_assumed_or_named_noop_findings_do_not_establish_complete_intervals(finding):
    coverage, evidence, required = fixture()
    evidence["references"]["complete"]["finding"] = finding
    with pytest.raises(ValueError, match="evidence finding"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("case", [exchange_fixture, payment_fixture], ids=["exchange", "payment"])
def test_inferred_complete_interval_does_not_promote_proposed_event_or_event_reference(case):
    coverage, evidence, required = case()
    evidence["references"]["complete"]["finding"] = "inferred_supported_events_complete"
    event = coverage["events"][0]
    verified_status = event["status"]
    event["status"] = "proposed"
    with pytest.raises(ValueError, match="proposed or unverified"):
        call(coverage, evidence, required)
    event["status"] = verified_status
    evidence["references"]["event"]["finding"] = "inferred_supported_events_complete"
    with pytest.raises(ValueError, match="coordinates or finding"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("change", ["gap", "overlap", "outside", "unknown", "identity", "no-reference", "unresolved"])
def test_coverage_must_exactly_cover_required_calendar_days(change):
    coverage, evidence, required = fixture()
    if change == "gap":
        coverage["windows"] = [window(end="2020-01-03"), window(start="2020-01-06")]
    elif change == "overlap":
        coverage["windows"].append(window(start="2020-01-03"))
    elif change == "outside":
        coverage["windows"][0]["end"] = "2020-01-09"
    elif change == "unknown":
        coverage["windows"][0]["review_state"] = "unknown"
    elif change == "identity":
        coverage["windows"][0]["isin"] = BI
    elif change == "no-reference":
        coverage["windows"][0]["evidence_refs"] = []
    else:
        coverage["unresolved"] = ["unknown action"]
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


@pytest.mark.parametrize("kind", ["voluntary_offer", "name_change", "market_movement"])
def test_noops_require_their_own_evidence_and_never_complete_coverage(kind):
    coverage, evidence, required = fixture()
    coverage["noops"] = [dict(code=A, isin=AI, on="2020-01-03", kind=kind, evidence_ref="noop")]
    evidence["references"]["noop"] = reference(start="2020-01-03", end="2020-01-03", finding="reviewed_" + kind)
    assert call(coverage, evidence, required) == ((), {})
    coverage["windows"] = []
    with pytest.raises(ValueError, match="uncovered"):
        call(coverage, evidence, required)
    coverage["windows"] = [window(ref="noop")]
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


def test_exchange_uses_existing_accounting_objects_exact_decimal_and_successor_scope():
    events, successor = call(*exchange_fixture())
    assert len(events) == 1 and type(events[0]) is CompulsoryStockExchange
    event = events[0]
    assert event.ratio == Decimal("1.12500000000000000000000001")
    assert event.old_basis.raw_close.as_tuple() == Decimal("100.2500").as_tuple()
    assert event.old_basis.dataset_sha256 == event.new_basis.dataset_sha256 == QUOTE
    assert "review_sha256:" in event.source and "coordinate:synthetic-document:section-2" in event.source
    assert successor == {B: ("2020-01-06", "2020-01-07", "2020-01-08")}


def chained_exchange_fixture():
    coverage, evidence, required = exchange_fixture()
    third, third_isin = "000003", "KR7000003000"
    first = coverage["events"][0]
    second = dict(first, event_id="exchange-2", code=B, isin=BI,
        successor_code=third, successor_isin=third_isin, ratio="2",
        old_basis_id="second-old", new_basis_id="third", effective_on="2020-01-07",
        available_on="2020-01-08", evidence_ref="second-event")
    coverage["events"] = [second, first]  # chronological stable decoding, not input guessing
    coverage["windows"][1] = window(B, BI, "2020-01-04", "2020-01-08", ("exchange-2",), "successor-complete")
    coverage["windows"].append(window(third, third_isin, "2020-01-07", "2020-01-08", ref="third-complete"))
    evidence["references"]["successor-complete"] = reference(B, BI, "2020-01-04", "2020-01-08",
        "confirmed_supported_events_complete", ("exchange-2",))
    evidence["references"]["third-complete"] = reference(third, third_isin, "2020-01-07", "2020-01-08")
    evidence["references"]["second-event"] = reference(B, BI, "2020-01-07", "2020-01-07",
        "verified_compulsory_stock_exchange", ("exchange-2",))
    price_basis(evidence, "second-old", B, BI, "2020-01-06", "last_observable_pre_event")
    price_basis(evidence, "third", third, third_isin, "2020-01-08", "successor_availability")
    return coverage, evidence, required


def test_chained_successor_requires_reviewed_closure_and_coherent_bases():
    coverage, evidence, required = chained_exchange_fixture()
    events, successor = call(coverage, evidence, required)
    assert tuple(event.event_id for event in events) == ("exchange-1", "exchange-2")
    assert successor == {B: ("2020-01-06", "2020-01-07", "2020-01-08"), "000003": ("2020-01-08",)}
    coverage["windows"].pop()
    with pytest.raises(ValueError, match="uncovered"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("parent_delivery", ["2020-01-07", "2020-01-09"])
def test_pending_successor_cannot_be_re_exchanged_on_or_before_delivery(parent_delivery):
    coverage, evidence, required = chained_exchange_fixture()
    parent = next(event for event in coverage["events"] if event["event_id"] == "exchange-1")
    parent["available_on"] = parent_delivery
    evidence["bases"]["new"]["session"] = parent_delivery
    evidence["references"]["new"] = reference(B, BI, parent_delivery, parent_delivery,
        "verified_successor_availability_basis")
    with pytest.raises(ValueError, match="pending entitlement"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("record_on,accepted", [("2020-01-07", False), ("2020-01-09", True)])
def test_successor_final_cash_requires_delivery_no_later_than_record_date(record_on, accepted):
    coverage, evidence, required = exchange_fixture()
    parent = coverage["events"][0]
    parent["available_on"] = "2020-01-09"
    evidence["bases"]["new"]["session"] = "2020-01-09"
    evidence["references"]["new"] = reference(B, BI, "2020-01-09", "2020-01-09",
        "verified_successor_availability_basis")
    child = dict(event_id="child-payment", kind="final_cash_payment", status="verified_paid", code=B, isin=BI,
        last_trading_on="2020-01-06", record_on=record_on, paid_on="2020-01-12",
        net_cash_per_raw_share="3", basis_id="child-last", evidence_ref="child-event")
    coverage["events"] = [child, parent]
    coverage["windows"][1] = window(B, BI, "2020-01-04", "2020-01-08", ("child-payment",), "successor-complete")
    evidence["references"]["successor-complete"] = reference(B, BI, "2020-01-04", "2020-01-08",
        "confirmed_supported_events_complete", ("child-payment",))
    evidence["references"]["child-event"] = reference(B, BI, "2020-01-12", "2020-01-12",
        "verified_final_cash_payment", ("child-payment",))
    evidence["references"]["child-event"]["reviewed_on"] = "2020-01-13"
    price_basis(evidence, "child-last", B, BI, "2020-01-06", "last_trading")
    if accepted:
        events, _ = call(coverage, evidence, required)
        assert tuple(event.event_id for event in events) == ("exchange-1", "child-payment")
        assert events[1].distribution.record_on == events[0].available_on
    else:
        with pytest.raises(ValueError, match="payout eligibility"):
            call(coverage, evidence, required)


def child_before_parent_fixture(kind):
    coverage, evidence, required = exchange_fixture()
    parent = coverage["events"][0]
    parent["effective_on"], parent["available_on"] = "2020-01-08", "2020-01-09"
    evidence["references"]["event"] = reference(start="2020-01-08", end="2020-01-08",
        finding="verified_compulsory_stock_exchange", ids=("exchange-1",))
    evidence["bases"]["new"]["session"] = "2020-01-09"
    evidence["references"]["new"] = reference(B, BI, "2020-01-09", "2020-01-09",
        "verified_successor_availability_basis")
    required += (dict(code=B, isin=BI, start="2020-01-02", end="2020-01-08"),)
    coverage["windows"][1] = window(B, BI, "2020-01-02", "2020-01-08", ("child-2",), "successor-complete")
    evidence["references"]["successor-complete"] = reference(B, BI, "2020-01-02", "2020-01-08",
        "confirmed_supported_events_complete", ("child-2",))
    if kind == "cash":
        child = dict(event_id="child-2", kind="final_cash_payment", status="verified_paid", code=B, isin=BI,
            last_trading_on="2020-01-03", record_on="2020-01-04", paid_on="2020-01-07",
            net_cash_per_raw_share="3", basis_id="child-old", evidence_ref="child-event")
        price_basis(evidence, "child-old", B, BI, "2020-01-03", "last_trading")
        evidence["references"]["child-event"] = reference(B, BI, "2020-01-07", "2020-01-07",
            "verified_final_cash_payment", ("child-2",))
    else:
        third, third_isin = "000003", "KR7000003000"
        child = dict(event_id="child-2", kind="compulsory_stock_exchange", status="verified_effective",
            code=B, isin=BI, successor_code=third, successor_isin=third_isin, ratio="2",
            old_basis_id="child-old", new_basis_id="child-new", effective_on="2020-01-06",
            available_on="2020-01-07", evidence_ref="child-event")
        price_basis(evidence, "child-old", B, BI, "2020-01-03", "last_observable_pre_event")
        price_basis(evidence, "child-new", third, third_isin, "2020-01-07", "successor_availability")
        evidence["references"]["child-event"] = reference(B, BI, "2020-01-06", "2020-01-06",
            "verified_compulsory_stock_exchange", ("child-2",))
        coverage["windows"].append(window(third, third_isin, "2020-01-06", "2020-01-08", ref="third-complete"))
        evidence["references"]["third-complete"] = reference(third, third_isin, "2020-01-06", "2020-01-08")
    coverage["events"] = [child, parent]
    return coverage, evidence, required


@pytest.mark.parametrize("kind", ["cash", "stock"])
@pytest.mark.parametrize("reverse_input", [False, True])
def test_complete_graph_rejects_child_decoded_before_parent_even_without_lifetime_overlap(kind, reverse_input):
    coverage, evidence, required = child_before_parent_fixture(kind)
    child, parent = coverage["events"]
    if kind == "cash":
        assert child["paid_on"] < parent["effective_on"]  # disjoint declared lifetimes still contradict delivery
    else:
        assert child["effective_on"] < parent["effective_on"]
    if reverse_input:
        coverage["events"].reverse()
    message = "payout eligibility" if kind == "cash" else "pending entitlement"
    with pytest.raises(ValueError, match=message):
        call(coverage, evidence, required)


def test_complete_graph_rejects_code_reuse_even_in_disjoint_isin_periods():
    coverage, evidence, required = child_before_parent_fixture("cash")
    child, parent = coverage["events"]
    later_isin = "KR7000002990"
    parent["successor_isin"] = later_isin
    evidence["bases"]["new"]["isin"] = later_isin
    evidence["references"]["new"]["isin"] = later_isin
    # Old B's payout ends on the 7th; the later ISIN uses the code from the 8th.
    required = (required[0], dict(required[1], end="2020-01-07"))
    coverage["windows"][1]["end"] = "2020-01-07"
    evidence["references"]["successor-complete"]["end"] = "2020-01-07"
    coverage["windows"].append(window(B, later_isin, "2020-01-08", "2020-01-08", ref="later-issue"))
    evidence["references"]["later-issue"] = reference(B, later_isin, "2020-01-08", "2020-01-08")
    with pytest.raises(ValueError, match="code reuse"):
        call(coverage, evidence, required)


def test_disjoint_original_issue_periods_cannot_reuse_a_code_in_one_quote_snapshot():
    coverage, evidence, required = fixture()
    earlier = dict(required[0], end="2020-01-03")
    later = dict(required[0], isin="KR7000001990", start="2020-01-06")
    with pytest.raises(ValueError, match="code reuse"):
        call(coverage, evidence, (earlier, later))


def test_pending_successor_basis_cannot_reuse_an_original_code_after_horizon():
    coverage, evidence, required = exchange_fixture()
    required += (dict(code=B, isin="KR7000002990", start="2020-01-02", end="2020-01-03"),)
    coverage["events"][0]["available_on"] = "2020-01-09"
    evidence["bases"]["new"]["session"] = "2020-01-09"
    evidence["references"]["new"] = reference(B, BI, "2020-01-09", "2020-01-09",
        "verified_successor_availability_basis")
    with pytest.raises(ValueError, match="code reuse"):
        call(coverage, evidence, required)


def test_delivery_after_horizon_retains_availability_basis_anchor():
    coverage, evidence, required = exchange_fixture()
    coverage["events"][0]["available_on"] = "2020-01-09"
    evidence["bases"]["new"]["session"] = "2020-01-09"
    evidence["references"]["new"] = reference(B, BI, "2020-01-09", "2020-01-09",
        "verified_successor_availability_basis")
    events, successor = call(coverage, evidence, required)
    assert events[0].available_on == date(2020, 1, 9)
    assert successor == {B: ("2020-01-09",)}


@pytest.mark.parametrize("change", ["missing-basis", "missing-closure", "basis-isin", "basis-quote", "basis-role",
    "basis-anchor", "late-effective", "proposed", "event-id", "duplicate-event", "conflicting-event",
    "unsupported-kind", "no-event-conflict"])
def test_exchange_rejects_incomplete_or_conflicting_declarations(change):
    coverage, evidence, required = exchange_fixture()
    event = coverage["events"][0]
    if change == "missing-basis":
        del evidence["bases"]["new"]
    elif change == "missing-closure":
        coverage["windows"].pop()
    elif change == "basis-isin":
        evidence["bases"]["new"]["isin"] = AI
    elif change == "basis-quote":
        evidence["bases"]["new"]["quote_snapshot_sha256"] = ACTIVITY
    elif change == "basis-role":
        evidence["bases"]["old"]["role"] = "assumed"
    elif change == "basis-anchor":
        evidence["bases"]["new"]["session"] = "2020-01-07"
    elif change == "late-effective":
        event["effective_on"] = "2020-01-09"
    elif change == "proposed":
        event["status"] = "proposed"
    elif change == "event-id":
        event["event_id"] = "different-id"
    elif change == "duplicate-event":
        coverage["events"].append(deepcopy(event))
    elif change == "conflicting-event":
        coverage["events"].append(dict(event, event_id="different-full-issue-event"))
    elif change == "unsupported-kind":
        event["kind"] = "ordinary_dividend"
    else:
        coverage["windows"][0] = window()
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


@pytest.mark.parametrize("amount", [None, 100.25, 100, True, "NaN", "Infinity", "0", "-1", "1e3"])
def test_basis_never_substitutes_float_zero_or_unknown(amount):
    coverage, evidence, required = exchange_fixture()
    evidence["bases"]["old"]["raw_close"] = amount
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


def test_verified_zero_net_final_payment_is_preserved_not_missing_replacement():
    events, successor = call(*payment_fixture())
    assert successor == {} and type(events[0]) is FinalCashPayment
    assert events[0].distribution.net_cash_per_raw_share == Decimal(0)
    assert events[0].distribution.paid_on == date(2020, 1, 7)


@pytest.mark.parametrize("paid_on", ["2020-01-09", "2020-01-12"])
def test_known_actual_payment_after_horizon_is_retained_without_early_cash_or_liquidation(paid_on):
    coverage, evidence, required = payment_fixture()
    coverage["events"][0]["paid_on"] = paid_on
    coverage["events"][0]["net_cash_per_raw_share"] = "25.1250"
    evidence["references"]["event"] = reference(start=paid_on, end=paid_on,
        finding="verified_final_cash_payment", ids=("payment-1",))
    evidence["references"]["event"]["reviewed_on"] = "2020-01-13"
    events, successor = call(coverage, evidence, required)
    assert successor == {} and len(events) == 1
    event = events[0]
    assert event.distribution.paid_on.isoformat() == paid_on
    assert event.distribution.net_cash_per_raw_share.as_tuple() == Decimal("25.1250").as_tuple()
    assert event.distribution.basis.session == date(2020, 1, 3)
    # Existing pure book primitive does not credit cash or consume the lot early.
    lot = Lot("synthetic-lot", A, Decimal("2"), Decimal("50.1250"), date(2020, 1, 3),
              date(2020, 1, 2), date(2020, 1, 6), date(2020, 1, 2), QUOTE)
    book = ActivityBook(Decimal("3"), (lot,), date(2020, 1, 3))
    at_horizon = book.apply_final_cash(event, session=date(2020, 1, 8))
    assert at_horizon.cash == book.cash and at_horizon.lots == book.lots
    assert at_horizon.applied_event_ids == () and at_horizon.slot_count == 1


def test_pending_payment_lifetime_can_overlap_requirement_after_last_trading():
    coverage, evidence, _ = payment_fixture()
    required = (dict(code=A, isin=AI, start="2020-01-06", end="2020-01-08"),)
    coverage["windows"] = [window(start="2020-01-06", ids=("payment-1",))]
    events, _ = call(coverage, evidence, required)
    assert events[0].distribution.last_trading_on < date(2020, 1, 6)
    assert events[0].distribution.paid_on == date(2020, 1, 7)
    coverage["windows"][0] = window(start="2020-01-06")
    with pytest.raises(ValueError, match="finding"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("outside", ["already-paid", "not-yet-ceased"])
def test_unrelated_payment_lifetime_outside_all_requirements_is_rejected(outside):
    coverage, evidence, _ = payment_fixture()
    required = (dict(code=A, isin=AI, start="2020-01-08", end="2020-01-08"),) if outside == "already-paid" else (
        dict(code=A, isin=AI, start="2020-01-02", end="2020-01-02"),)
    coverage["windows"] = [window(start=required[0]["start"], end=required[0]["end"], ids=("payment-1",))]
    with pytest.raises(ValueError, match="lifetime"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("amount", [None, 0, 0.0, True, "NaN", "Infinity", "-0.01", " 0", "1e3"])
def test_unknown_or_lossy_payment_amount_is_not_zero(amount):
    coverage, evidence, required = payment_fixture()
    coverage["events"][0]["net_cash_per_raw_share"] = amount
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


@pytest.mark.parametrize("change", ["missing-amount", "proposed", "unlinked-later-payment", "review-before-payment", "wrong-last-session"])
def test_payment_requires_actual_reviewed_payment_and_exact_basis(change):
    coverage, evidence, required = payment_fixture()
    event = coverage["events"][0]
    if change == "missing-amount":
        del event["net_cash_per_raw_share"]
    elif change == "proposed":
        event["status"] = "announced"
    elif change == "unlinked-later-payment":
        event["paid_on"] = "2020-01-09"
    elif change == "review-before-payment":
        evidence["references"]["event"]["reviewed_on"] = "2020-01-06"
    else:
        event["last_trading_on"] = "2020-01-02"
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


@pytest.mark.parametrize("field,value", [("raw_sha256", "x" * 64), ("review_sha256", None),
    ("coordinate", ""), ("isin", BI), ("start", "2020-01-03"), ("end", "2020-01-07"),
    ("reviewed_on", "2020-01-07"), ("finding", "reviewed_voluntary_offer"), ("event_ids", ["missing"])])
def test_reference_link_is_internally_checked_not_merely_nonempty(field, value):
    coverage, evidence, required = fixture()
    evidence["references"]["complete"][field] = value
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


@pytest.mark.parametrize("field", ["activity_snapshot_sha256", "quote_snapshot_sha256", "required_scope_sha256", "calendar_sha256"])
def test_wrong_scope_pin_rejected(field):
    coverage, evidence, required = fixture()
    coverage[field] = "f" * 64
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


def test_conflicting_required_identity_and_unrelated_evidence_rejected():
    coverage, evidence, required = fixture()
    wrong = dict(required[0], isin=BI)
    with pytest.raises(ValueError, match="identity"):
        call(coverage, evidence, required + (wrong,))
    evidence["references"]["unused"] = reference()
    with pytest.raises(ValueError, match="unused"):
        call(coverage, evidence, required)


@pytest.mark.parametrize("invalid", ["20200102", "2020-1-02", date(2020, 1, 2), None])
def test_date_decoding_is_exact(invalid):
    coverage, evidence, required = fixture()
    coverage["windows"][0]["start"] = invalid
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


def test_cash_only_requires_no_quote_no_evidence_and_no_events():
    coverage, _, _ = fixture()
    coverage["quote_snapshot_sha256"] = None
    coverage["windows"] = []
    evidence = {"references": {}, "bases": {}}
    assert call(coverage, evidence, (), quote=None) == ((), {})
    coverage["quote_snapshot_sha256"] = QUOTE
    with pytest.raises(ValueError, match="empty requirement"):
        call(coverage, evidence, ())
    coverage["quote_snapshot_sha256"] = None
    coverage["events"] = exchange_fixture()[0]["events"]
    with pytest.raises(ValueError, match="empty requirement"):
        call(coverage, evidence, (), quote=None)
