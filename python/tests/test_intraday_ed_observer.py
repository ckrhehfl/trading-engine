"""Tiny invented receipts only; no raw inputs, transport or observer activation."""

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect

import pytest

from research import intraday_ed_observer as observer
from research.intraday_ed_observer import (
    ExactField,
    ObservationStatus,
    ObserverPolicy,
    ObserverState,
    QuoteReceipt,
    ReceiptClocks,
    ReplayCapacityExceeded,
    StreamIdentity,
    TransportKind,
    TransportReceipt,
    freeze_decision,
    reduce_record,
    replay,
)


BASE = datetime(2026, 1, 2, 0, 10, tzinfo=timezone.utc)
STREAM = StreamIdentity("invented", "fixture-1", "fixture-stock", "KRX", "raw", "KRW/share", "Asia/Seoul")
POLICY = ObserverPolicy(32, timedelta(seconds=5), timedelta(seconds=3), timedelta(milliseconds=50))


def observed(value: str) -> ExactField:
    return ExactField(ObservationStatus.OBSERVED, Decimal(value))


def quote(index: int = 1, *, seconds: int = 0, **changes) -> QuoteReceipt:
    event_at = BASE + timedelta(seconds=seconds)
    receipt = QuoteReceipt(
        stream=STREAM,
        message_id=f"quote-{index}",
        sequence=index,
        data_vintage="fixture-v1",
        evidence_level="invented",
        raw_sha256="a" * 64,
        is_final=False,
        clocks=ReceiptClocks(
            date(2026, 1, 2), event_at, event_at,
            event_at + timedelta(milliseconds=10),
            event_at + timedelta(milliseconds=20),
            event_at + timedelta(milliseconds=20),
        ),
        session_state="continuous",
        bid=observed("100.000000000000000001"),
        ask=observed("101.000000000000000001"),
        bid_quantity=observed("0"),
        ask_quantity=observed("7"),
    )
    return replace(receipt, **changes)


def decision(state: ObserverState, *, seconds: int = 1, **changes):
    args = dict(
        decision_at=BASE + timedelta(seconds=seconds),
        outcome="decline",
        reason="injected frozen rule decline",
        model_sha256="b" * 64,
        contract_sha256="c" * 64,
    )
    args.update(changes)
    return freeze_decision(state, **args)


def test_exact_fields_and_frozen_no_order_outcome_are_deterministic():
    records = (quote(), quote(2, seconds=1))
    state = replay(STREAM, records, POLICY)
    assert state == replay(STREAM, records, POLICY)
    assert state.latest.bid.value == Decimal("100.000000000000000001")
    assert state.latest.bid_quantity.value == Decimal(0)
    assert state.records == records
    result = decision(state, seconds=2)
    assert result.status is ObservationStatus.OBSERVED
    assert result.frozen.action == "NO_ORDER"
    assert result.frozen.outcome == "decline"
    assert result.frozen.model_sha256 == "b" * 64
    assert result.frozen == decision(replay(STREAM, records, POLICY), seconds=2).frozen
    assert len(result.frozen.source_sha256) == 64
    with pytest.raises(FrozenInstanceError):
        result.frozen.action = "ORDER"
    changed = replay(STREAM, (replace(records[0], raw_sha256="d" * 64), records[1]), POLICY)
    assert decision(changed, seconds=2).frozen.source_sha256 != result.frozen.source_sha256


def test_bounded_batch_refuses_overflow_without_dropping_or_overwriting_receipts():
    records = (quote(), quote(2, seconds=1))
    policy = replace(POLICY, max_records=2)
    state = replay(STREAM, records, policy)
    incoming = quote(3, seconds=2)
    with pytest.raises(ReplayCapacityExceeded, match="max_records exceeded"):
        reduce_record(state, incoming)
    assert state.records == records
    assert state.latest is records[-1]
    assert len(state.quality) == 2
    assert incoming.sequence == 3
    with pytest.raises(ReplayCapacityExceeded):
        replay(STREAM, records + (incoming,), policy)
    assert decision(state, seconds=2).frozen.observer_policy.max_records == 2


@pytest.mark.parametrize("maximum", [0, -1, True, 2.0])
def test_batch_cap_requires_an_explicit_positive_integer(maximum):
    with pytest.raises(ValueError, match="positive integer"):
        replace(POLICY, max_records=maximum)


@pytest.mark.parametrize("status", [ObservationStatus.UNKNOWN, ObservationStatus.ERROR])
def test_missing_and_transport_error_are_not_actual_zero(status):
    missing = ExactField(status, None, "feed omitted quantity")
    state = replay(STREAM, (quote(bid_quantity=missing),), POLICY)
    result = decision(state)
    assert result.status is status
    assert result.frozen is None
    assert f"{status.value}:bid_quantity:feed omitted quantity" in result.reasons
    assert state.latest.bid_quantity.value is None
    assert decision(replay(STREAM, (quote(),), POLICY)).frozen is not None


@pytest.mark.parametrize("value", [0.1, 1, "1", Decimal("NaN"), Decimal("Infinity")])
def test_measurements_refuse_float_integer_string_and_nonfinite(value):
    with pytest.raises(ValueError, match="finite Decimal"):
        ExactField(ObservationStatus.OBSERVED, value)


@pytest.mark.parametrize("field,value,reason", [
    ("bid", "0", "invalid_bid"),
    ("ask", "-1", "invalid_ask"),
    ("bid_quantity", "-1", "invalid_bid_quantity"),
    ("bid", "102", "crossed_quote"),
])
def test_invalid_quotes_retain_reason_and_do_not_fall_back(field, value, reason):
    invalid = quote(2, seconds=1, **{field: observed(value)})
    state = replay(STREAM, (quote(), invalid), POLICY)
    result = decision(state, seconds=2)
    assert state.latest is invalid
    assert result.status is ObservationStatus.ERROR
    assert reason in result.reasons
    assert result.frozen is None


@pytest.mark.parametrize("field", ["source_event_at", "available_at", "first_seen_at", "received_at", "retrieved_at"])
def test_future_information_cannot_be_frozen_even_inside_clock_skew(field):
    first = quote()
    future = BASE + timedelta(seconds=1, milliseconds=1)
    receipt = replace(first, clocks=replace(first.clocks, **{field: future}))
    result = decision(replay(STREAM, (receipt,), POLICY))
    assert result.frozen is None
    assert f"future_{field}" in result.reasons


@pytest.mark.parametrize("field", ["source_event_at", "available_at", "first_seen_at", "retrieved_at"])
def test_unknown_clock_is_not_fabricated(field):
    first = quote()
    receipt = replace(first, clocks=replace(first.clocks, **{field: None}))
    result = decision(replay(STREAM, (receipt,), POLICY))
    assert result.status is ObservationStatus.UNKNOWN
    assert f"clock_unknown:{field}" in result.reasons
    assert result.frozen is None


def test_later_valid_quote_cannot_hide_an_earlier_future_retrieval_clock():
    first = quote()
    first = replace(first, clocks=replace(first.clocks, retrieved_at=BASE + timedelta(seconds=9)))
    state = replay(STREAM, (first, quote(2, seconds=1)), POLICY)
    result = decision(state, seconds=2)
    assert "future_retrieved_at" in result.reasons
    assert result.frozen is None


def test_clock_skew_tolerance_never_allows_a_source_later_than_decision():
    first = quote()
    first = replace(first, clocks=replace(first.clocks,
        source_event_at=BASE + timedelta(milliseconds=40)))
    state = replay(STREAM, (first,), POLICY)
    assert "source_clock_ahead" not in state.quality[0].reasons
    result = decision(state, decision_at=BASE + timedelta(milliseconds=30))
    assert "future_source_event_at" in result.reasons
    assert result.frozen is None


@pytest.mark.parametrize("seconds,reason", [(4, "stale_receipt"), (6, "stale_source")])
def test_stale_sources_and_receipts_are_distinct(seconds, reason):
    result = decision(replay(STREAM, (quote(),), POLICY), seconds=seconds)
    assert reason in result.reasons
    assert result.status is ObservationStatus.UNKNOWN
    assert result.frozen is None


def test_duplicate_delivery_is_retained_without_refreshing_quote_freshness():
    first = quote()
    duplicate = replace(first, clocks=replace(first.clocks,
        first_seen_at=BASE + timedelta(seconds=3),
        received_at=BASE + timedelta(seconds=3),
        retrieved_at=BASE + timedelta(seconds=3),
    ))
    state = replay(STREAM, (first, duplicate), POLICY)
    assert state.records == (first, duplicate)
    assert state.quality[-1].reasons == ("duplicate_message",)
    assert state.latest is first
    assert "stale_receipt" in decision(state, seconds=4).reasons


def test_conflicting_id_is_not_overwritten_or_healed_by_a_later_quote():
    first = quote()
    conflict = replace(first, ask=observed("103"), raw_sha256="d" * 64)
    state = replay(STREAM, (first, conflict, quote(2, seconds=1)), POLICY)
    assert len(state.records) == 3
    assert "conflicting_message_id" in state.quality[1].reasons
    result = decision(state, seconds=2)
    assert "conflicting_message_id" in result.reasons
    assert result.status is ObservationStatus.ERROR
    assert result.frozen is None


def test_new_vintage_preserves_amendment_without_overwriting_original():
    first = quote()
    correction = replace(quote(2, seconds=1), message_id=first.message_id,
        data_vintage="fixture-amendment-v2", ask=observed("102"), raw_sha256="d" * 64)
    state = replay(STREAM, (first, correction), POLICY)
    assert state.records[0] == first
    assert state.latest == correction
    assert decision(state, seconds=2).frozen is not None


@pytest.mark.parametrize("records,reason", [
    ((quote(), quote(3, seconds=1)), "sequence_gap"),
    ((quote(2), quote(1, seconds=1)), "sequence_out_of_order"),
    ((quote(1, seconds=1), quote(2)), "receipt_out_of_order"),
    ((quote(sequence=None),), "sequence_unknown"),
])
def test_sequence_and_arrival_disorder_are_never_sorted_or_silently_repaired(records, reason):
    state = replay(STREAM, records, POLICY)
    assert state.records == records
    result = decision(state, seconds=2)
    assert reason in result.reasons
    assert result.frozen is None


def test_source_event_disorder_is_detected_even_when_receive_order_is_monotone():
    second = quote(2, seconds=1)
    second = replace(second, clocks=replace(second.clocks, source_event_at=BASE - timedelta(seconds=1)))
    state = replay(STREAM, (quote(), second), POLICY)
    assert "source_event_out_of_order" in decision(state, seconds=2).reasons


def test_disconnect_reconnect_stays_unavailable_until_separate_recovery_integration():
    lost = TransportReceipt(STREAM, BASE + timedelta(milliseconds=100), TransportKind.DISCONNECTED, "fixture cable")
    reconnected = TransportReceipt(STREAM, BASE + timedelta(milliseconds=200), TransportKind.RECONNECTED, "fixture reconnect")
    state = replay(STREAM, (quote(), lost, reconnected, quote(2, seconds=1)), POLICY)
    result = decision(state, seconds=2)
    assert {"transport_disconnected", "transport_reconnected"} <= set(result.reasons)
    assert len(state.records) == 4
    assert result.status is ObservationStatus.UNKNOWN
    assert result.frozen is None


def test_explicit_gap_receipt_keeps_its_source_reason_and_blocks_freezing():
    gap = TransportReceipt(STREAM, BASE + timedelta(milliseconds=100),
        TransportKind.GAP, "source explicitly reports missing messages 10..12")
    state = replay(STREAM, (quote(), gap, quote(2, seconds=1)), POLICY)
    assert state.records[1].reason == "source explicitly reports missing messages 10..12"
    assert "transport_gap" in decision(state, seconds=2).reasons
    assert decision(state, seconds=2).frozen is None


def test_source_stream_identity_cannot_be_replaced_mid_replay():
    foreign = quote(2, seconds=1, stream=replace(STREAM, channel="NXT"))
    state = replay(STREAM, (quote(), foreign), POLICY)
    assert state.records[-1] is foreign
    assert state.latest.message_id == "quote-1"
    assert "stream_identity_mismatch" in decision(state, seconds=2).reasons
    assert decision(state, seconds=2).frozen is None


@pytest.mark.parametrize("session", ["auction", "vi", "halted", "closed", "unknown"])
def test_session_state_is_recorded_and_never_treated_as_continuous(session):
    state = replay(STREAM, (quote(session_state=session),), POLICY)
    assert f"session_{session}" in decision(state).reasons
    assert decision(state).frozen is None


def test_bad_record_and_order_like_outcome_have_no_callable_order_path():
    with pytest.raises(ValueError, match="typed already-received"):
        reduce_record(ObserverState(STREAM, POLICY), {"action": "BUY", "qty": 10})
    with pytest.raises(ValueError, match="analytical"):
        decision(replay(STREAM, (quote(),), POLICY), outcome="BUY")
    with pytest.raises(ValueError, match="timezone-aware"):
        decision(replay(STREAM, (quote(),), POLICY), decision_at=BASE.replace(tzinfo=None))
    tree = ast.parse(inspect.getsource(observer))
    imports = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    assert imports == {"__future__", "dataclasses", "datetime", "decimal", "enum", "hashlib", "json", "typing"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"open", "eval", "exec", "__import__"} for node in ast.walk(tree))
