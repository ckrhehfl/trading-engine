"""Finite offline KIS quote exports connected to the existing NO_ORDER journal.

The caller supplies already-received wire bytes and pinned receipt metadata.
There is no file acquisition, source connection, clock reader, inference or
executable entry point. The field map follows krx_quote_sampler's spot output1;
its network client and float conversions are deliberately not imported.

The dated receipt is not the date of the date-less aspr_acpt_hour. Source time,
sequence, publication/first-seen clocks, finality and cash-session state remain
unknown. A supplied analytical outcome consequently need not be freezable.
Hashes bind supplied evidence; they do not certify its upstream truth or access
permission. A quote touch never represents a fill.

One private source sidecar retains original wire bytes, request/receive metadata
and the external decision. Both files must be sealed before replay. Replay
decodes the original wire again and checks every canonical receipt. Failures
leave their incomplete evidence in place; no resume, overwrite or retry exists.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal

from research import intraday_ed_observer as core
from research import intraday_ed_observer_io as io


ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
TR_ID = "FHKST01010200"
SIDECAR_SUFFIX = ".kis-export.json"
SCHEMA = "ed-observer-kis-source-export-v1"
KST = timezone(timedelta(hours=9))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class KisExportRefusal(ValueError):
    """The finite supplied export cannot be bound to this offline contract."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise KisExportRefusal(reason)


def _text(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 256 and bool(value.strip())


@dataclass(frozen=True)
class ReceivedKisQuoteExport:
    """message_id identifies the supplied export, never an inferred market sequence."""

    raw: bytes
    raw_sha256: str
    source: str
    endpoint: str
    tr_id: str
    market_division: str
    code: str
    channel: str
    message_id: str
    received_at: datetime
    received_at_epoch_ms: int
    source_date: date | None = None


@dataclass(frozen=True)
class KisExportBinding:
    journal: io.JournalContract
    observation_date: date
    data_vintage: str
    evidence_level: str
    exports_sha256: str
    decision_sha256: str
    max_export_bytes: int
    max_total_export_bytes: int
    max_sidecar_bytes: int

    def __post_init__(self) -> None:
        _need(type(self.journal) is io.JournalContract, "frozen journal contract required")
        stream = self.journal.stream
        _need(stream.source == "KIS" and stream.channel == "KRX"
              and stream.price_basis == "raw" and stream.physical_unit == "KRW/share"
              and stream.timezone == "Asia/Seoul", "only the KIS raw KRX cash quote contract is supported")
        _need(re.fullmatch(r"KRX:[0-9]{6}", stream.symbol) is not None,
              "exact KRX six-digit symbol required; aliases are unsupported")
        _need(type(self.observation_date) is date, "one explicit observation date required")
        _need(_text(self.data_vintage) and _text(self.evidence_level), "bounded vintage/evidence text required")
        core._sha256(self.exports_sha256)
        core._sha256(self.decision_sha256)
        for name in ("max_export_bytes", "max_total_export_bytes", "max_sidecar_bytes"):
            value = getattr(self, name)
            _need(type(value) is int and value > 0, "explicit positive export byte limits required")


@dataclass(frozen=True)
class SuppliedDecision:
    decision_at: datetime
    outcome: Literal["accept", "decline", "unknown"]
    reason: str


def supplied_decision_sha256(decision: SuppliedDecision) -> str:
    """Pin the external analytical input independently of the retained file."""
    _need(type(decision) is SuppliedDecision, "typed external decision required")
    return hashlib.sha256(core._canonical(_decision_value(decision))).hexdigest()


def _decision_value(decision: SuppliedDecision) -> dict:
    return {"decision_at": decision.decision_at.isoformat(), "outcome": decision.outcome,
            "reason": decision.reason}


@dataclass(frozen=True)
class KisExportDayReport:
    binding: KisExportBinding
    exports: tuple[ReceivedKisQuoteExport, ...]
    supplied_decision: SuppliedDecision
    journal: io.JournalReplay
    assessment: core.DecisionAssessment
    observed_records: int
    unknown_records: int
    error_records: int
    action: Literal["NO_ORDER"] = field(default="NO_ORDER", init=False)


def _export_value(export: ReceivedKisQuoteExport) -> dict:
    value = {item.name: getattr(export, item.name) for item in fields(export) if item.name != "raw"}
    value["raw_base64"] = base64.b64encode(export.raw).decode("ascii")
    value["received_at"] = export.received_at.isoformat()
    value["source_date"] = None if export.source_date is None else export.source_date.isoformat()
    return value


def export_batch_sha256(exports: tuple[ReceivedKisQuoteExport, ...]) -> str:
    """Content pin for a caller's finite supplied batch, not a source certificate."""
    _need(type(exports) is tuple and all(type(item) is ReceivedKisQuoteExport for item in exports),
          "immutable typed export tuple required")
    return hashlib.sha256(core._canonical(tuple(_export_value(item) for item in exports))).hexdigest()


def _binding_value(binding: KisExportBinding) -> dict:
    value = {item.name: getattr(binding, item.name) for item in fields(binding) if item.name != "journal"}
    value["journal"] = json.loads(io._header(binding.journal))["contract"]
    value["observation_date"] = binding.observation_date.isoformat()
    return value


def _sidecar_bytes(binding: KisExportBinding, exports: tuple[ReceivedKisQuoteExport, ...],
                   decision: SuppliedDecision) -> bytes:
    return core._canonical({"schema": SCHEMA, "action": "NO_ORDER", "binding": _binding_value(binding),
                            "exports": tuple(_export_value(item) for item in exports),
                            "decision": _decision_value(decision)})


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate KIS JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise KisExportRefusal("nonfinite KIS JSON number")


def _measurement(block: dict, name: str, response_error: str | None) -> core.ExactField:
    if response_error is not None:
        return core.ExactField(core.ObservationStatus.ERROR, None, response_error)
    value = block.get(name)
    if value is None or (type(value) is str and not value.strip()):
        return core.ExactField(core.ObservationStatus.UNKNOWN, None, "kis_missing:" + name)
    if type(value) is not str or re.fullmatch(r"[0-9]{1,64}", value.strip()) is None:
        return core.ExactField(core.ObservationStatus.ERROR, None, "kis_invalid_integer:" + name)
    return core.ExactField(core.ObservationStatus.OBSERVED, Decimal(value.strip()))


def decode_received_export(export: ReceivedKisQuoteExport, binding: KisExportBinding) -> core.QuoteReceipt:
    """Parse actual spot output1 fields from supplied bytes; never contact KIS."""
    _need(type(binding) is KisExportBinding and type(export) is ReceivedKisQuoteExport,
          "frozen binding and typed export required")
    _need(type(export.raw) is bytes and 0 < len(export.raw) <= binding.max_export_bytes,
          "bounded nonempty KIS wire bytes required")
    core._sha256(export.raw_sha256)
    _need(hashlib.sha256(export.raw).hexdigest() == export.raw_sha256, "original wire SHA differs")
    _need((export.source, export.endpoint, export.tr_id, export.market_division, export.code, export.channel)
          == (binding.journal.stream.source, ENDPOINT, TR_ID, "J", binding.journal.stream.symbol[4:], "KRX"),
          "source/request/channel/symbol binding differs")
    _need(_text(export.message_id), "bounded explicit export message identity required")
    core._aware(export.received_at)
    _need(type(export.received_at_epoch_ms) is int and export.received_at_epoch_ms >= 0,
          "explicit received epoch milliseconds required")
    try:
        _need(EPOCH + timedelta(milliseconds=export.received_at_epoch_ms) == export.received_at,
              "received datetime and epoch milliseconds differ")
    except OverflowError as exc:
        raise KisExportRefusal("received epoch milliseconds out of range") from exc
    _need(export.received_at.astimezone(KST).date() == binding.observation_date,
          "receipt outside the explicit stream-day calendar")
    _need(export.source_date is None, "same-channel source-date proof is not implemented; keep source_date unknown")
    try:
        payload = json.loads(export.raw, object_pairs_hook=_object, parse_float=Decimal, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise KisExportRefusal("invalid KIS quote JSON") from exc
    _need(type(payload) is dict and set(payload) <= {"rt_cd", "msg_cd", "msg1", "output1", "output2"},
          "unsupported KIS export response fields")
    code = payload.get("rt_cd")
    _need(type(code) is str and re.fullmatch(r"[0-9]{1,8}", code) is not None, "explicit KIS response status required")
    _need(code != "0" or "output1" in payload, "spot response requires output1; no other-block fallback")
    block = payload.get("output1", {})
    _need(type(block) is dict, "KIS output1 must be a quote object")
    accepted = block.get("aspr_acpt_hour")
    if accepted is not None and accepted != "":
        _need(type(accepted) is str and re.fullmatch(r"[0-9]{6}", accepted) is not None,
              "aspr_acpt_hour must be date-less HHMMSS or unknown")
        _need(int(accepted[:2]) < 24 and int(accepted[2:4]) < 60 and int(accepted[4:]) < 60,
              "invalid date-less quote acceptance clock")
    response_error = None if code == "0" else "kis_response_error:" + code
    def exact(name: str) -> core.ExactField:
        return _measurement(block, name, response_error)
    return core.QuoteReceipt(
        binding.journal.stream, export.message_id, None, binding.data_vintage, binding.evidence_level,
        export.raw_sha256, None,
        core.ReceiptClocks(binding.observation_date, None, None, None, export.received_at, None),
        "unknown", exact("bidp1"), exact("askp1"), exact("bidp_rsqn1"), exact("askp_rsqn1"),
    )


def _prepare(binding: KisExportBinding, exports: tuple[ReceivedKisQuoteExport, ...],
             decision: SuppliedDecision) -> tuple[bytes, tuple[bytes, ...]]:
    _need(type(binding) is KisExportBinding and type(exports) is tuple
          and 0 < len(exports) <= binding.journal.policy.max_records, "one bounded nonempty export tuple required")
    # Validate every item and all metadata before opening either output file.
    receipts = tuple(decode_received_export(item, binding) for item in exports)
    _need(sum(len(item.raw) for item in exports) <= binding.max_total_export_bytes, "total original export bytes exceeded")
    _need(export_batch_sha256(exports) == binding.exports_sha256, "frozen export batch SHA differs")
    _need(type(decision) is SuppliedDecision and decision.outcome in {"accept", "decline", "unknown"}
          and _text(decision.reason), "bounded externally supplied analytical decision required")
    core._aware(decision.decision_at)
    _need(supplied_decision_sha256(decision) == binding.decision_sha256, "frozen external decision SHA differs")
    _need(decision.decision_at.astimezone(KST).date() == binding.observation_date,
          "external decision outside the explicit stream-day calendar")
    state = core.replay(binding.journal.stream, receipts, binding.journal.policy)
    core.freeze_decision(state, decision_at=decision.decision_at, outcome=decision.outcome,
                         reason=decision.reason, model_sha256=binding.journal.model_sha256,
                         contract_sha256=binding.journal.contract_sha256)
    lines = tuple(io.receipt_bytes(item) for item in receipts)
    _need(all(len(raw) <= binding.journal.max_line_bytes
              and io.parse_receipt(raw, max_line_bytes=binding.journal.max_line_bytes) == item
              for raw, item in zip(lines, receipts)), "canonical receipt line exceeds contract")
    header = io._header(binding.journal)
    reserve = io._footer("0" * 64, binding.journal.policy.max_records)
    _need(max(len(header), len(reserve)) <= binding.journal.max_line_bytes,
          "journal framing exceeds line contract")
    _need(len(header) + 1 + sum(len(raw) + 1 for raw in lines) + len(reserve) + 1
          <= binding.journal.max_bytes, "complete canonical journal exceeds byte contract")
    sidecar = _sidecar_bytes(binding, exports, decision)
    _need(len(sidecar) <= binding.max_sidecar_bytes, "complete original-export sidecar exceeds byte contract")
    return sidecar, lines


def _write_sidecar(parent: int, name: str, raw: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        handle = os.fdopen(descriptor, "wb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise
    with handle:
        io.storage.write_all(handle, raw)
        os.fsync(handle.fileno())
        _need(os.fstat(handle.fileno()).st_size == len(raw), "original-export sidecar size differs")
        os.fchmod(handle.fileno(), 0o400)
        os.fsync(handle.fileno())
        info = os.fstat(handle.fileno())
        io._file(info)
        _need(stat.S_IMODE(info.st_mode) == 0o400, "original-export sidecar seal failed")
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _need(io.storage.file_identity(info) == io.storage.file_identity(named), "original-export sidecar changed during write")
    os.fsync(parent)


def connect_offline_exports(path: Path, binding: KisExportBinding,
                            exports: tuple[ReceivedKisQuoteExport, ...],
                            decision: SuppliedDecision) -> KisExportDayReport:
    """Persist one prevalidated stream-day batch and replay its original exports."""
    sidecar, lines = _prepare(binding, exports, decision)
    with io.JournalWriter(path, binding.journal) as writer:
        _write_sidecar(writer._parent, path.name + SIDECAR_SUFFIX, sidecar)
        for raw in lines:
            writer.append(raw)
    return replay_export_journal(path, binding)


def _read_sidecar(path: Path, binding: KisExportBinding) -> bytes:
    parent = io._parent(path)
    descriptor = None
    try:
        name = path.name + SIDECAR_SUFFIX
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        io._file(before)
        _need(stat.S_IMODE(before.st_mode) == 0o400 and 0 < before.st_size <= binding.max_sidecar_bytes,
              "original-export sidecar must be sealed and bounded")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(binding.max_sidecar_bytes + 1)
            after = os.fstat(handle.fileno())
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _need(len(raw) == before.st_size and io.storage.file_identity(before)
                  == io.storage.file_identity(after) == io.storage.file_identity(named),
                  "original-export sidecar changed during replay")
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def replay_export_journal(path: Path, binding: KisExportBinding) -> KisExportDayReport:
    """Require both sealed files and reconstruct quality/NO_ORDER from raw KIS."""
    _need(type(binding) is KisExportBinding, "frozen export binding required")
    raw = _read_sidecar(path, binding)
    try:
        value = io._shape(io._decode(raw, binding.max_sidecar_bytes),
                          {"schema", "action", "binding", "exports", "decision"})
        _need(value["schema"] == SCHEMA and value["action"] == "NO_ORDER", "unsupported source sidecar contract")
        _need(value["binding"] == json.loads(core._canonical(_binding_value(binding))), "original-export binding differs")
        _need(type(value["exports"]) is list and 0 < len(value["exports"]) <= binding.journal.policy.max_records,
              "bounded stored export list required")
        exports = []
        names = {item.name for item in fields(ReceivedKisQuoteExport)} - {"raw"} | {"raw_base64"}
        for item in value["exports"]:
            item = dict(io._shape(item, names))
            encoded = item.pop("raw_base64")
            _need(type(encoded) is str, "textual original wire encoding required")
            item["raw"] = base64.b64decode(encoded, validate=True)
            item["received_at"] = datetime.fromisoformat(item["received_at"])
            item["source_date"] = None if item["source_date"] is None else date.fromisoformat(item["source_date"])
            exports.append(ReceivedKisQuoteExport(**item))
        supplied = dict(io._shape(value["decision"], {item.name for item in fields(SuppliedDecision)}))
        supplied["decision_at"] = datetime.fromisoformat(supplied["decision_at"])
        decision = SuppliedDecision(**supplied)
        exports = tuple(exports)
        reproduced, lines = _prepare(binding, exports, decision)
        _need(reproduced == raw, "original-export evidence does not round-trip")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise KisExportRefusal("invalid original-export sidecar") from exc
    journal = io.replay_journal(path, binding.journal)
    _need(journal.raw_receipts == lines, "original KIS exports and canonical journal differ")
    assessment = core.freeze_decision(
        journal.state, decision_at=decision.decision_at, outcome=decision.outcome,
        reason=decision.reason, model_sha256=binding.journal.model_sha256,
        contract_sha256=binding.journal.contract_sha256,
    )
    statuses = (core.ObservationStatus.OBSERVED, core.ObservationStatus.UNKNOWN, core.ObservationStatus.ERROR)
    counts = tuple(sum(item.status is status for item in journal.state.quality) for status in statuses)
    return KisExportDayReport(binding, exports, decision, journal, assessment, *counts)
