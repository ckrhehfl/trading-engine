"""Source-only original-population reentry iterator; caller owns raw audit/IO.

Both typed consumers decode original chunks separately through the existing
shared parser. Producer caches/results and inline receipts never supply saved
verification's independent expected first/reset/additional mathematics.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import hashlib

from research import intraday_ed_snapshot as codec

from research import intraday_ed_sources as sources
from research import intraday_trader_inputs as original
from research import intraday_trader_reentry as producer
from research import intraday_trader_reentry_reference as reference


@dataclass(frozen=True)
class OriginalSessionRecord:
    targets: tuple[sources.TargetCoordinate, ...]
    result: producer.SessionResult
    mathematical_verification: reference.VerificationReceipt


def validate_record(record: OriginalSessionRecord) -> None:
    """Closed framing contract; scientific truth still requires fresh raw math."""
    need = codec._need
    need(type(record) is OriginalSessionRecord and type(record.result) is producer.SessionResult,
         "reentry_record_type_differs")
    result, receipt = record.result, record.mathematical_verification
    need(type(result.families) is tuple and tuple(item.family for item in result.families) == ("EE-A", "EE-B"),
         "reentry_family_order_differs")
    count = 0
    for index, family in enumerate(result.families):
        need(type(family) is producer.FamilyAttempt and type(family.reset) is producer.Reset,
             "reentry_family_record_differs")
        reset = family.reset
        expected_id = "EE-A-2R-TARGET" if index == 0 else "EE-B"
        need(type(reset.first_sha256) is str and reset.first_sha256 == hashlib.sha256(
            codec.canonical(codec.encode(family.first))).hexdigest(), "reentry_first_identity_differs")
        supported = reset.state == "supported"
        need(reset.state in {"supported", "unsupported", "unknown", "refusal"}
             and supported == (reset.previous is not None) == (family.second is not None),
             "reentry_null_support_contract_differs")
        if supported:
            previous = reset.previous
            producer.scenarios._validate_previous(previous, family.family, result.symbol, result.opening, result.closing)
            need(reset.determined_at == previous.terminal_at and type(reset.terminal_reason) is str
                 and bool(reset.terminal_reason), "reentry_terminal_proof_contract_differs")
            count += 1
        else:
            need(reset.terminal_reason is None and reset.determined_at is None, "reentry_null_terminal_differs")
        for attempt, policy in ((1, family.first), (2, family.second)):
            if policy is None:
                continue
            detection, candidate = policy.detection, policy.detection.candidate
            need(policy.scenario_id == detection.scenario_id == expected_id
                 and type(detection.attempt_number) is int and detection.attempt_number == attempt,
                 "reentry_policy_or_attempt_differs")
            if candidate is not None:
                need(candidate.scenario_id == expected_id and type(candidate.attempt_number) is int
                     and candidate.attempt_number == attempt and candidate.profit_facts is None
                     and candidate.previous_terminal == (None if attempt == 1 else reset.previous),
                     "reentry_candidate_previous_differs")
            if attempt == 2:
                need(detection.original_parent_end_at == reset.previous.parent_end_at,
                     "reentry_original_parent_differs")
    need(type(receipt) is reference.VerificationReceipt and receipt.verified is True
         and receipt.scenarios == reference.POLICIES and type(receipt.first_count) is int and receipt.first_count == 2
         and type(receipt.second_count) is int and receipt.second_count == count and receipt.claim == reference.CLAIM,
         "reentry_inline_receipt_contract_differs")


def iter_original_sessions(universe: sources.UniverseCalendar, raw_reader: original.AuditedChunkReader,
                           checkpoint: Callable[[str], object]) -> Iterator[OriginalSessionRecord]:
    groups = original._groups(universe)
    original._require(callable(getattr(raw_reader, "load_raw_chunk", None))
        and callable(getattr(raw_reader, "assert_unchanged", None)), "raw_reader_contract_required")
    original._require(callable(checkpoint), "checkpoint_callable_required")
    for day, symbol in sorted(groups):
        checkpoint(original.BEFORE_SESSION)
        raw_reader.assert_unchanged()
        try:
            price = raw_reader.load_raw_chunk("CC", symbol, day)
            volume = raw_reader.load_raw_chunk("CX", symbol, day)
            production = sources.session_inputs(symbol, day, price, volume)
            independent = sources.full_reference_session_inputs(symbol, day, price, volume)
            result, receipt = producer.evaluate_verified_session(production.session,
                reference_session=independent, source_metadata=production.metadata)
            record = OriginalSessionRecord(tuple(groups[(day, symbol)]), result, receipt)
            del price, volume, production, independent
        finally:
            raw_reader.assert_unchanged()
            checkpoint(original.AFTER_SESSION)
        yield record


def verify_record(record: OriginalSessionRecord, price: dict | None,
                  volume: dict | None) -> reference.VerificationReceipt:
    """Fresh expected source metadata/raw decode, never production evaluation."""
    symbol, day = record.result.symbol, record.result.opening.date().isoformat()
    metadata = sources.session_inputs(symbol, day, price, volume).metadata
    independent = sources.full_reference_session_inputs(symbol, day, price, volume)
    return reference.verify_session_result(record.result, independent, source_metadata=metadata)
