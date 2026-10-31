"""Offline preparation for an order-incapable ED observer.

Only already-received, typed records enter this module. There is no transport,
clock reader, model inference, scheduler, persistence or executable entry point.
One bounded immutable batch represents one explicitly identified quote stream.
Replay retains bad/duplicate receipts; it never sorts them into apparent causal
order. A gap, disconnect or identity conflict remains unresolved in this version.
Actual transport, durable receipts and evidence-backed recovery need a separately
reviewed integration and source authorization. The caller owns original receipts
and their future durable preservation. This in-memory batch refuses overflow;
it is not a live process capable of observing indefinitely.

The frozen decision binds an externally supplied rule/model outcome to this
typed quote history. It does not certify the upstream model's features, source
entitlements, raw-body digest, training, strategy eligibility or hypothetical fills.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal


class ObservationStatus(StrEnum):
    OBSERVED = "observed"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass(frozen=True)
class ExactField:
    status: ObservationStatus
    value: Decimal | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ObservationStatus):
            raise ValueError("status must be an ObservationStatus")
        if self.status is ObservationStatus.OBSERVED:
            if not isinstance(self.value, Decimal) or not self.value.is_finite():
                raise ValueError("observed values require a finite Decimal")
            if self.reason is not None:
                raise ValueError("an observed value cannot carry a missing/error reason")
        elif self.value is not None or not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("unknown/error values require a reason and no numeric value")


@dataclass(frozen=True)
class StreamIdentity:
    source: str
    stream_id: str
    symbol: str
    channel: Literal["KRX", "NXT", "SOR", "UNKNOWN"]
    price_basis: Literal["raw", "adjusted", "unknown"]
    physical_unit: str
    timezone: str

    def __post_init__(self) -> None:
        for name in ("source", "stream_id", "symbol", "physical_unit", "timezone"):
            _nonempty(getattr(self, name), name)
        if self.channel not in {"KRX", "NXT", "SOR", "UNKNOWN"}:
            raise ValueError("unsupported channel")
        if self.price_basis not in {"raw", "adjusted", "unknown"}:
            raise ValueError("unsupported price basis")


@dataclass(frozen=True)
class ReceiptClocks:
    observation_date: date
    source_event_at: datetime | None
    available_at: datetime | None
    first_seen_at: datetime | None
    received_at: datetime
    retrieved_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.observation_date, date) or isinstance(
            self.observation_date, datetime
        ):
            raise ValueError("observation_date must be a date")
        for name in _CLOCK_FIELDS:
            value = getattr(self, name)
            if value is not None:
                _aware(value)
        _aware(self.received_at)


@dataclass(frozen=True)
class QuoteReceipt:
    stream: StreamIdentity
    message_id: str
    sequence: int | None
    data_vintage: str
    evidence_level: str
    raw_sha256: str
    is_final: bool | None
    clocks: ReceiptClocks
    session_state: Literal["continuous", "auction", "vi", "halted", "closed", "unknown"]
    bid: ExactField
    ask: ExactField
    bid_quantity: ExactField
    ask_quantity: ExactField

    def __post_init__(self) -> None:
        for name in ("message_id", "data_vintage", "evidence_level"):
            _nonempty(getattr(self, name), name)
        if self.sequence is not None and (
            type(self.sequence) is not int or self.sequence < 0
        ):
            raise ValueError("sequence must be a nonnegative integer or unknown")
        _sha256(self.raw_sha256)
        if self.is_final is not None and type(self.is_final) is not bool:
            raise ValueError("is_final must be bool or unknown")
        if self.session_state not in {
            "continuous", "auction", "vi", "halted", "closed", "unknown"
        }:
            raise ValueError("unsupported session state")
        if not isinstance(self.stream, StreamIdentity) or not isinstance(
            self.clocks, ReceiptClocks
        ):
            raise ValueError("typed stream and receipt clocks are required")
        if any(not isinstance(getattr(self, name), ExactField) for name in _QUOTE_FIELDS):
            raise ValueError("quote measurements require ExactField values")


class TransportKind(StrEnum):
    DISCONNECTED = "disconnected"
    RECONNECTED = "reconnected"
    GAP = "gap"


@dataclass(frozen=True)
class TransportReceipt:
    stream: StreamIdentity
    received_at: datetime
    kind: TransportKind
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.stream, StreamIdentity) or not isinstance(
            self.kind, TransportKind
        ):
            raise ValueError("typed stream and transport kind are required")
        _aware(self.received_at)
        _nonempty(self.reason, "transport reason")


Record = QuoteReceipt | TransportReceipt


@dataclass(frozen=True)
class ObserverPolicy:
    max_records: int
    max_source_age: timedelta
    max_receive_age: timedelta
    max_source_clock_skew: timedelta

    def __post_init__(self) -> None:
        if type(self.max_records) is not int or self.max_records <= 0:
            raise ValueError("max_records must be an explicit positive integer")
        for name in ("max_source_age", "max_receive_age", "max_source_clock_skew"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value < timedelta(0):
                raise ValueError("clock policy requires explicit nonnegative timedeltas")


@dataclass(frozen=True)
class QualityEvent:
    record_index: int
    status: ObservationStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ObserverState:
    stream: StreamIdentity
    policy: ObserverPolicy
    records: tuple[Record, ...] = ()
    quality: tuple[QualityEvent, ...] = ()
    latest: QuoteReceipt | None = None
    last_sequence: int | None = None
    last_received_at: datetime | None = None
    last_source_event_at: datetime | None = None
    continuity_reasons: tuple[str, ...] = ()


class ReplayCapacityExceeded(RuntimeError):
    """The caller must preserve the incoming receipt outside this bounded batch."""


def reduce_record(state: ObserverState, record: Record) -> ObserverState:
    """Append one injected receipt, retaining quality failures without repairing it."""
    if not isinstance(record, (QuoteReceipt, TransportReceipt)):
        raise ValueError("only typed already-received records are accepted")
    if len(state.records) >= state.policy.max_records:
        raise ReplayCapacityExceeded(
            "offline batch max_records exceeded; original state is unchanged; "
            "receipt preservation requires the future durable integration"
        )
    received_at = (
        record.clocks.received_at
        if isinstance(record, QuoteReceipt) else record.received_at
    )
    reasons: list[str] = []
    continuity = list(state.continuity_reasons)
    latest, sequence, source_at = state.latest, state.last_sequence, state.last_source_event_at
    if record.stream != state.stream:
        reasons.append("stream_identity_mismatch")
        continuity.append("stream_identity_mismatch")
    if state.last_received_at is not None and received_at < state.last_received_at:
        reasons.append("receipt_out_of_order")
        continuity.append("receipt_out_of_order")
    if isinstance(record, TransportReceipt):
        reason = f"transport_{record.kind.value}"
        reasons.append(reason)
        continuity.append(reason)
        latest = None
    else:
        reasons.extend(_quote_reasons(record, state.policy))
        previous = next(
            (item for item in state.records if isinstance(item, QuoteReceipt)
             and (item.stream, item.data_vintage, item.message_id)
             == (record.stream, record.data_vintage, record.message_id)),
            None,
        )
        if previous is not None:
            if _content_identity(previous) == _content_identity(record):
                reasons.append("duplicate_message")
            else:
                reasons.append("conflicting_message_id")
                continuity.append("conflicting_message_id")
        elif record.stream == state.stream:
            if record.sequence is None:
                reasons.append("sequence_unknown")
                continuity.append("sequence_unknown")
            elif sequence is not None:
                if record.sequence <= sequence:
                    reasons.append("sequence_out_of_order")
                    continuity.append("sequence_out_of_order")
                elif record.sequence != sequence + 1:
                    reasons.append("sequence_gap")
                    continuity.append("sequence_gap")
            event_at = record.clocks.source_event_at
            if event_at is not None and source_at is not None and event_at < source_at:
                reasons.append("source_event_out_of_order")
                continuity.append("source_event_out_of_order")
            if record.sequence is not None:
                sequence = max(sequence, record.sequence) if sequence is not None else record.sequence
            if event_at is not None:
                source_at = max(source_at, event_at) if source_at is not None else event_at
            # The newest receipt remains visible even if unusable: never fall
            # back silently to the previous good quote or forward-fill it.
            latest = record
    reasons_tuple = tuple(dict.fromkeys(reasons))
    status = _status(reasons_tuple)
    return replace(
        state,
        records=state.records + (record,),
        quality=state.quality + (QualityEvent(len(state.records), status, reasons_tuple),),
        latest=latest,
        last_sequence=sequence,
        last_received_at=(
            max(state.last_received_at, received_at)
            if state.last_received_at else received_at
        ),
        last_source_event_at=source_at,
        continuity_reasons=tuple(dict.fromkeys(continuity)),
    )


def replay(
    stream: StreamIdentity, records: tuple[Record, ...], policy: ObserverPolicy
) -> ObserverState:
    """Deterministic offline replay in original receipt order, with no IO."""
    state = ObserverState(stream, policy)
    for record in records:
        state = reduce_record(state, record)
    return state


@dataclass(frozen=True)
class FrozenDecision:
    decision_at: datetime
    outcome: Literal["accept", "decline", "unknown"]
    reason: str
    source_sha256: str
    model_sha256: str
    contract_sha256: str
    observer_policy: ObserverPolicy
    action: Literal["NO_ORDER"] = field(default="NO_ORDER", init=False)


@dataclass(frozen=True)
class DecisionAssessment:
    status: ObservationStatus
    reasons: tuple[str, ...]
    frozen: FrozenDecision | None


def freeze_decision(
    state: ObserverState,
    *,
    decision_at: datetime,
    outcome: Literal["accept", "decline", "unknown"],
    reason: str,
    model_sha256: str,
    contract_sha256: str,
) -> DecisionAssessment:
    """Bind a supplied analytical outcome; never generate a signal or an order.

    Every represented historical clock must be known and causal. A gap/reconnect
    prevents freezing: recovery and feature-history admission are unimplemented.
    Duplicate delivery never refreshes the latest quote.
    """
    _aware(decision_at)
    _sha256(model_sha256)
    _sha256(contract_sha256)
    _nonempty(reason, "decision reason")
    if outcome not in {"accept", "decline", "unknown"}:
        raise ValueError("only analytical accept/decline/unknown outcomes are accepted")
    reasons = list(state.continuity_reasons)
    quote = state.latest
    if state.last_received_at is not None and state.last_received_at > decision_at:
        reasons.append("future_receipt")
    if quote is None:
        reasons.append("quote_unknown")
    else:
        reasons.extend(_quote_reasons(quote, state.policy))
        if (
            quote.clocks.source_event_at is not None
            and decision_at - quote.clocks.source_event_at > state.policy.max_source_age
        ):
            reasons.append("stale_source")
        if decision_at - quote.clocks.received_at > state.policy.max_receive_age:
            reasons.append("stale_receipt")
    # A later good quote must not hide an earlier receipt with a future or
    # unresolved clock. All records contribute to the frozen source identity.
    for record in state.records:
        if isinstance(record, QuoteReceipt):
            reasons.extend(_clock_reasons(record.clocks, state.policy))
            for name in _CLOCK_FIELDS:
                value = getattr(record.clocks, name)
                if value is not None and value > decision_at:
                    reasons.append(f"future_{name}")
    reasons_tuple = tuple(dict.fromkeys(reasons))
    if reasons_tuple:
        return DecisionAssessment(_status(reasons_tuple), reasons_tuple, None)
    source_sha256 = hashlib.sha256(_canonical((state.stream, state.records))).hexdigest()
    frozen = FrozenDecision(
        decision_at, outcome, reason, source_sha256, model_sha256,
        contract_sha256, state.policy,
    )
    return DecisionAssessment(ObservationStatus.OBSERVED, (), frozen)


_QUOTE_FIELDS = ("bid", "ask", "bid_quantity", "ask_quantity")
_CLOCK_FIELDS = (
    "source_event_at", "available_at", "first_seen_at", "received_at", "retrieved_at"
)


def _quote_reasons(quote: QuoteReceipt, policy: ObserverPolicy) -> list[str]:
    reasons = _clock_reasons(quote.clocks, policy)
    for name in _QUOTE_FIELDS:
        value = getattr(quote, name)
        if value.status is not ObservationStatus.OBSERVED:
            reasons.append(f"{value.status.value}:{name}:{value.reason}")
        elif value.value < 0 or (name in {"bid", "ask"} and value.value == 0):
            reasons.append(f"invalid_{name}")
    if (
        quote.bid.value is not None and quote.ask.value is not None
        and quote.bid.value > quote.ask.value
    ):
        reasons.append("crossed_quote")
    if quote.session_state != "continuous":
        reasons.append(f"session_{quote.session_state}")
    if (
        quote.stream.channel == "UNKNOWN" or quote.stream.price_basis == "unknown"
        or quote.stream.physical_unit == "unknown"
    ):
        reasons.append("market_metadata_unknown")
    return reasons


def _clock_reasons(clocks: ReceiptClocks, policy: ObserverPolicy) -> list[str]:
    reasons: list[str] = []
    for name in ("source_event_at", "available_at", "first_seen_at", "retrieved_at"):
        if getattr(clocks, name) is None:
            reasons.append(f"clock_unknown:{name}")
    if (
        clocks.available_at is not None and clocks.first_seen_at is not None
        and clocks.available_at > clocks.first_seen_at
    ):
        reasons.append("availability_after_first_seen")
    if clocks.first_seen_at is not None and clocks.first_seen_at > clocks.received_at:
        reasons.append("first_seen_after_receipt")
    if (
        clocks.retrieved_at is not None and clocks.first_seen_at is not None
        and clocks.retrieved_at < clocks.first_seen_at
    ):
        reasons.append("retrieval_before_first_seen")
    if (
        clocks.source_event_at is not None
        and clocks.source_event_at > clocks.received_at + policy.max_source_clock_skew
    ):
        reasons.append("source_clock_ahead")
    if (
        clocks.source_event_at is not None and clocks.available_at is not None
        and clocks.source_event_at > clocks.available_at + policy.max_source_clock_skew
    ):
        reasons.append("source_after_availability")
    return reasons


def _status(reasons: tuple[str, ...]) -> ObservationStatus:
    if not reasons or reasons == ("duplicate_message",):
        return ObservationStatus.OBSERVED
    if any(reason.startswith(("invalid_", "error:", "conflicting_", "future_")) or reason in {
        "crossed_quote", "stream_identity_mismatch", "receipt_out_of_order",
        "source_clock_ahead", "source_after_availability", "availability_after_first_seen",
        "first_seen_after_receipt", "retrieval_before_first_seen", "sequence_out_of_order",
        "source_event_out_of_order",
    } for reason in reasons):
        return ObservationStatus.ERROR
    return ObservationStatus.UNKNOWN


def _content_identity(receipt: QuoteReceipt) -> bytes:
    # Redelivery receive/first-seen/retrieval clocks may differ. Neither a new
    # delivery time nor a duplicate ID can refresh source time or quote age.
    return _canonical((
        receipt.stream, receipt.sequence, receipt.data_vintage, receipt.evidence_level,
        receipt.raw_sha256, receipt.is_final, receipt.clocks.observation_date,
        receipt.clocks.source_event_at, receipt.clocks.available_at, receipt.session_state,
        receipt.bid, receipt.ask, receipt.bid_quantity, receipt.ask_quantity,
    ))


def _canonical(value: object) -> bytes:
    def convert(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            return {entry.name: convert(getattr(item, entry.name)) for entry in fields(item)}
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, tuple):
            return [convert(part) for part in item]
        return item
    return json.dumps(
        convert(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware datetimes")


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty")


def _sha256(value: str) -> None:
    if (
        not isinstance(value, str) or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("hashes must be lowercase SHA-256 hex strings")
