"""Offline receipt decoding and private persistence for a future ED observer.

Only caller-provided stored bytes enter this boundary. It neither connects to a
source nor verifies entitlements, runs inference, activates observation or writes
operational signals. Imports perform no IO. The existing native-storage safety
primitives are reused; no transport or recovery protocol is introduced.

A journal is created exclusively, appended by its sole open writer, and fsynced
after every record. Completion is explicit. A crash, partial tail, malformed
record or changed identity refuses replay without modifying the original file.
There is no resume/truncate/recovery API. Replay derives state from raw receipt
lines, never from a saved state. Core continuity failures remain sticky.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal

from research import intraday_campaign_wsl_transport as storage
from research import intraday_ed_observer as core


RECEIPT_SCHEMA = "ed-observer-receipt-v1"
JOURNAL_SCHEMA = "ed-observer-journal-v1"


class ObserverIORefusal(ValueError):
    """Stored evidence is not a complete admissible offline journal/receipt."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ObserverIORefusal(message)


def _positive(value: int) -> None:
    _need(type(value) is int and value > 0, "explicit positive byte limits required")


def _shape(value: object, names: set[str]) -> dict:
    _need(type(value) is dict and set(value) == names, "exact object fields required")
    return value


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _no_number(value: str) -> None:
    raise ObserverIORefusal("floating/nonfinite JSON numbers are not accepted")


def _decode(raw: bytes, maximum: int) -> dict:
    _positive(maximum)
    _need(type(raw) is bytes and 0 < len(raw) <= maximum, "bounded receipt bytes required")
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_float=_no_number,
                           parse_constant=_no_number)
        _need(type(value) is dict and core._canonical(value) == raw, "canonical JSON required")
        return value
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise ObserverIORefusal("invalid canonical JSON") from exc


def _stream(value: object) -> core.StreamIdentity:
    return core.StreamIdentity(**_shape(value, {item.name for item in fields(core.StreamIdentity)}))


def _exact(value: object) -> core.ExactField:
    value = _shape(value, {"status", "value", "reason"})
    number = value["value"]
    _need(number is None or type(number) is str, "exact measurements require Decimal text")
    return core.ExactField(core.ObservationStatus(value["status"]),
                           None if number is None else Decimal(number), value["reason"])


def receipt_bytes(receipt: core.Record) -> bytes:
    """Canonical NO_ORDER envelope; Decimal spelling and unknowns remain exact."""
    _need(type(receipt) in (core.QuoteReceipt, core.TransportReceipt), "typed receipt required")
    return core._canonical({"schema": RECEIPT_SCHEMA, "action": "NO_ORDER",
                            "kind": "quote" if isinstance(receipt, core.QuoteReceipt) else "transport",
                            "receipt": json.loads(core._canonical(receipt))})


def parse_receipt(raw: bytes, *, max_line_bytes: int) -> core.Record:
    """Decode one bounded canonical stored receipt, with no IO or normalization."""
    value = _shape(_decode(raw, max_line_bytes), {"schema", "action", "kind", "receipt"})
    _need(value["schema"] == RECEIPT_SCHEMA and value["action"] == "NO_ORDER",
          "only the NO_ORDER receipt contract is accepted")
    try:
        if value["kind"] == "quote":
            payload = dict(_shape(value["receipt"], {item.name for item in fields(core.QuoteReceipt)}))
            clocks = _shape(payload["clocks"], {item.name for item in fields(core.ReceiptClocks)})
            parsed_clocks = {}
            for name, clock in clocks.items():
                _need(clock is None or type(clock) is str, "textual clocks required")
                parsed_clocks[name] = (date.fromisoformat(clock) if name == "observation_date"
                                       else None if clock is None else datetime.fromisoformat(clock))
            payload["clocks"] = core.ReceiptClocks(**parsed_clocks)
            payload["stream"] = _stream(payload["stream"])
            for name in ("bid", "ask", "bid_quantity", "ask_quantity"):
                payload[name] = _exact(payload[name])
            receipt = core.QuoteReceipt(**payload)
        else:
            _need(value["kind"] == "transport", "unsupported receipt kind")
            payload = dict(_shape(value["receipt"], {item.name for item in fields(core.TransportReceipt)}))
            payload["stream"] = _stream(payload["stream"])
            _need(type(payload["received_at"]) is str, "textual clocks required")
            payload["received_at"] = datetime.fromisoformat(payload["received_at"])
            payload["kind"] = core.TransportKind(payload["kind"])
            receipt = core.TransportReceipt(**payload)
        _need(receipt_bytes(receipt) == raw, "typed receipt must round-trip exactly")
        return receipt
    except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
        raise ObserverIORefusal("invalid typed receipt") from exc


@dataclass(frozen=True)
class JournalContract:
    stream: core.StreamIdentity
    policy: core.ObserverPolicy
    contract_sha256: str
    source_sha256: str
    runtime_sha256: str
    model_sha256: str
    rule_sha256: str
    max_line_bytes: int
    max_bytes: int
    action: Literal["NO_ORDER"] = field(default="NO_ORDER", init=False)

    def __post_init__(self) -> None:
        _need(type(self.stream) is core.StreamIdentity and type(self.policy) is core.ObserverPolicy,
              "typed stream and bounded observer policy required")
        for name in ("contract_sha256", "source_sha256", "runtime_sha256", "model_sha256", "rule_sha256"):
            core._sha256(getattr(self, name))
        _positive(self.max_line_bytes)
        _positive(self.max_bytes)


def _header(contract: JournalContract) -> bytes:
    _need(type(contract) is JournalContract, "immutable journal contract required")
    metadata = {item.name: getattr(contract, item.name) for item in fields(contract)
                if item.name not in {"stream", "policy"}}
    metadata["stream"] = json.loads(core._canonical(contract.stream))
    metadata["policy"] = {"max_records": contract.policy.max_records}
    for name in ("max_source_age", "max_receive_age", "max_source_clock_skew"):
        duration = getattr(contract.policy, name)
        metadata["policy"][name + "_microseconds"] = (
            duration.days * 86_400_000_000 + duration.seconds * 1_000_000 + duration.microseconds)
    return core._canonical({"schema": JOURNAL_SCHEMA, "kind": "metadata", "contract": metadata})


def _footer(digest: str, count: int) -> bytes:
    return core._canonical({"schema": JOURNAL_SCHEMA, "kind": "complete", "action": "NO_ORDER",
                            "raw_prefix_sha256": digest, "record_count": count})


def _outside_worktree(descriptor: int) -> None:
    try:
        os.stat(".git", dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ObserverIORefusal("journal directory inside a worktree")


def _parent(path: Path) -> int:
    _need(os.name == "posix" and isinstance(path, Path), "native WSL Path required")
    storage.native_cache(path, Path(__file__).absolute().parents[2])
    _need(bool(path.name), "named journal required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        _outside_worktree(descriptor)
        # Keep an anchored descriptor: replacing a checked parent with a symlink
        # must not redirect a later create/open into another directory.
        for component in path.parent.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _outside_worktree(descriptor)
        info = os.fstat(descriptor)
        _need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
              "existing private native parent directory required")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _file(info: os.stat_result) -> None:
    _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
          and stat.S_IMODE(info.st_mode) in {0o400, 0o600}, "private exclusive regular journal required")


class JournalWriter:
    """Sole writer for a newly created journal; existing files cannot be resumed."""

    def __init__(self, path: Path, contract: JournalContract):
        header = _header(contract) + b"\n"
        self._reserve = len(_footer("0" * 64, contract.policy.max_records)) + 1
        _need(len(header) - 1 <= contract.max_line_bytes and self._reserve - 1 <= contract.max_line_bytes,
              "metadata/completion exceeds max_line_bytes")
        _need(len(header) + self._reserve <= contract.max_bytes, "metadata/completion exceeds max_bytes")
        self._parent = _parent(path)
        self._stream = None
        self._failed = False
        self._contract = contract
        self._name = path.name
        self._state = core.ObserverState(contract.stream, contract.policy)
        self._digest = hashlib.sha256()
        self._size = 0
        try:
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
                                 | os.O_NOFOLLOW, 0o600, dir_fd=self._parent)
            try:
                self._stream = os.fdopen(descriptor, "wb", buffering=0)
            except BaseException:
                os.close(descriptor)
                raise
            os.fchmod(self._stream.fileno(), 0o600)
            self._write(header)
            os.fsync(self._parent)
        except BaseException:
            self.abort()
            raise

    @property
    def contract(self) -> JournalContract:
        return self._contract

    def _check(self) -> None:
        _need(self._stream is not None and not self._failed, "journal writer closed or failed")
        info = os.fstat(self._stream.fileno())
        _file(info)
        named = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
        _need(storage.file_identity(info) == self._identity
              and storage.file_identity(named) == self._identity, "journal changed outside sole writer")

    def _write(self, raw: bytes) -> None:
        try:
            storage.write_all(self._stream, raw)
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._size += len(raw)
            info = os.fstat(self._stream.fileno())
            _need(info.st_size == self._size, "journal size changed during append")
            self._identity = storage.file_identity(info)
            self._digest.update(raw)
        except BaseException:
            self._failed = True
            raise

    def append(self, raw: bytes) -> None:
        """Durably append exactly these stored receipt bytes, preserving duplicates."""
        self._check()
        receipt = parse_receipt(raw, max_line_bytes=self._contract.max_line_bytes)
        state = core.reduce_record(self._state, receipt)
        _need(self._size + len(raw) + 1 + self._reserve <= self._contract.max_bytes,
              "journal max_bytes exceeded; incoming bytes remain caller-owned")
        self._write(raw + b"\n")
        self._state = state

    def finish(self) -> None:
        """Write a durable completion marker; this never certifies source quality."""
        try:
            self._check()
            self._write(_footer(self._digest.hexdigest(), len(self._state.records)) + b"\n")
            os.fchmod(self._stream.fileno(), 0o400)
            os.fsync(self._stream.fileno())
        finally:
            self.abort()

    def abort(self) -> None:
        """Close without repairing or completing incomplete evidence."""
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._parent is not None:
            os.close(self._parent)
            self._parent = None

    def __enter__(self) -> JournalWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.finish()
        else:
            self.abort()


@dataclass(frozen=True)
class JournalReplay:
    contract: JournalContract
    raw_receipts: tuple[bytes, ...]
    state: core.ObserverState
    journal_sha256: str
    action: Literal["NO_ORDER"] = field(default="NO_ORDER", init=False)


def replay_journal(path: Path, expected: JournalContract) -> JournalReplay:
    """Read a complete immutable-contract journal and reconstruct the core state.

    Refusal leaves every byte intact and returns no apparently recovered state.
    A completion marker means durable framing, not successful market observation.
    """
    header = _header(expected)
    parent = _parent(path)
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        _file(before)
        _need(before.st_size <= expected.max_bytes, "journal max_bytes exceeded")
        state = core.ObserverState(expected.stream, expected.policy)
        raw_receipts = []
        prefix, whole = hashlib.sha256(), hashlib.sha256()
        size, complete = 0, False
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            while line := handle.readline(expected.max_line_bytes + 2):
                _need(not complete, "bytes after completion marker")
                size += len(line)
                _need(size <= expected.max_bytes, "journal max_bytes exceeded")
                _need(line.endswith(b"\n") and len(line) - 1 <= expected.max_line_bytes,
                      "partial or oversized journal line; explicit recovery required")
                raw = line[:-1]
                whole.update(line)
                if size == len(line):
                    _need(raw == header, "immutable journal metadata differs")
                elif _decode(raw, expected.max_line_bytes).get("kind") == "complete":
                    _need(raw == _footer(prefix.hexdigest(), len(raw_receipts)), "completion evidence differs")
                    complete = True
                    continue
                else:
                    receipt = parse_receipt(raw, max_line_bytes=expected.max_line_bytes)
                    state = core.reduce_record(state, receipt)
                    raw_receipts.append(raw)
                prefix.update(line)
            _need(complete, "journal incomplete; explicit recovery required")
            _need(stat.S_IMODE(before.st_mode) == 0o400, "journal completion was not sealed")
            after = os.fstat(handle.fileno())
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _need(storage.file_identity(before) == storage.file_identity(after)
                  == storage.file_identity(named), "journal changed during replay")
        return JournalReplay(expected, tuple(raw_receipts), state, whole.hexdigest())
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
