"""Authenticate and stream a completed report without retaining its event arrays.

Only ``variants.D+1.events`` and ``variants.D+2.events`` are streamed. Both
policies and both event arrays are required. The returned mapping preserves all
other values, but omits those two ``events`` keys entirely. Event counts belong
to the caller, so an existing report key can never be overwritten by a count.

The byte ceiling and expected SHA256 authenticate the *entire original file*
before parsing or invoking ``on_event``. Each event and each retained subtree
is bounded separately; event count does not affect the reader's memory use.
Retained report metadata and anything retained by a callback are caller costs.
Callbacks are provisional until this function returns: later invalid JSON or
file mutation raises instead of returning a verified report. Callers must not
publish or commit callback effects before successful return.

JSON objects reject duplicate decoded keys at every level, numbers must be
finite, container nesting is limited to 64, and input must end immediately after
the root object's single LF. Member order and internal JSON whitespace are not
otherwise prescribed. No network, database, or research execution is performed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import BinaryIO, Callable


_CHUNK_BYTES = 64 * 1024
_MAXIMUM_DEPTH = 64
_WHITESPACE = re.compile(rb"[ \t\r\n]*")
_SCALAR_END = re.compile(rb"[ \t\r\n,\]}]")
_STRUCTURAL = re.compile(rb'["\\{}\[\]]')
_STRING_SPECIAL = re.compile(rb'["\\]')
_POLICIES = frozenset({"D+1", "D+2"})
_OMIT = object()


class ReportReadError(ValueError):
    """The pinned file, JSON syntax, or streaming report shape is invalid."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportReadError("duplicate JSON object key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ReportReadError("nonfinite JSON number")
    return result


def _reject_constant(value: str) -> None:
    raise ReportReadError("nonfinite JSON constant")


def _decode(raw: bytearray) -> object:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReportReadError("invalid bounded JSON value") from error


def _identity(stream: BinaryIO) -> tuple[int, int, int, int, int]:
    stat = os.fstat(stream.fileno())
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _authenticate(
    stream: BinaryIO,
    expected_sha256: str,
    maximum_bytes: int,
    checkpoint: Callable[[], None],
) -> int:
    digest = hashlib.sha256()
    count = 0
    last = b""
    while True:
        checkpoint()
        chunk = stream.read(_CHUNK_BYTES)
        if not chunk:
            break
        count += len(chunk)
        if count > maximum_bytes:
            raise ReportReadError("report exceeds maximum_bytes")
        digest.update(chunk)
        last = chunk[-1:]
    if digest.hexdigest() != expected_sha256:
        raise ReportReadError("report SHA256 mismatch")
    if last != b"\n":
        raise ReportReadError("report must end with LF")
    return count


class _Reader:
    def __init__(
        self,
        stream: BinaryIO,
        maximum_bytes: int,
        maximum_value_bytes: int,
        checkpoint: Callable[[], None],
    ) -> None:
        self.stream = stream
        self.maximum_bytes = maximum_bytes
        self.maximum_value_bytes = maximum_value_bytes
        self.checkpoint = checkpoint
        self.buffer = b""
        self.position = 0
        self.count = 0
        self.digest = hashlib.sha256()

    def peek(self) -> bytes:
        if self.position == len(self.buffer):
            self.checkpoint()
            self.buffer = self.stream.read(_CHUNK_BYTES)
            self.position = 0
            self.count += len(self.buffer)
            if self.count > self.maximum_bytes:
                raise ReportReadError("report exceeds maximum_bytes during parsing")
            self.digest.update(self.buffer)
        return self.buffer[self.position : self.position + 1]

    def expect(self, token: bytes) -> None:
        if self.peek() != token:
            raise ReportReadError("unexpected or truncated JSON token")
        self.position += 1

    def whitespace(self) -> None:
        while self.peek():
            match = _WHITESPACE.match(self.buffer, self.position)
            self.position = match.end()
            if self.position != len(self.buffer):
                return

    def _append(self, raw: bytearray, end: int) -> None:
        length = end - self.position
        if len(raw) + length > self.maximum_value_bytes:
            raise ReportReadError("JSON value exceeds maximum_value_bytes")
        raw.extend(self.buffer[self.position : end])
        self.position = end

    def value(self, parent_depth: int) -> object:
        """Scan lexical runs in C and decode only one bounded JSON value."""
        self.whitespace()
        first = self.peek()
        if not first:
            raise ReportReadError("truncated JSON value")
        raw = bytearray()
        if first not in (b'"', b"{", b"["):
            while self.peek():
                match = _SCALAR_END.search(self.buffer, self.position)
                self._append(raw, match.start() if match else len(self.buffer))
                if match:
                    break
            return _decode(raw)

        stack = [first] if first in (b"{", b"[") else []
        in_string = first == b'"'
        if parent_depth + len(stack) > _MAXIMUM_DEPTH:
            raise ReportReadError("JSON nesting exceeds maximum depth")
        self._append(raw, self.position + 1)
        while self.peek():
            pattern = _STRING_SPECIAL if in_string else _STRUCTURAL
            match = pattern.search(self.buffer, self.position)
            if match is None:
                self._append(raw, len(self.buffer))
                continue
            token = match.group()
            self._append(raw, match.end())
            if in_string:
                if token == b"\\":
                    if not self.peek():
                        raise ReportReadError("truncated JSON escape")
                    self._append(raw, self.position + 1)
                else:
                    in_string = False
                    if not stack:
                        return _decode(raw)
            elif token == b'"':
                in_string = True
            elif token in (b"{", b"["):
                stack.append(token)
                if parent_depth + len(stack) > _MAXIMUM_DEPTH:
                    raise ReportReadError("JSON nesting exceeds maximum depth")
            elif token in (b"}", b"]"):
                expected = b"{" if token == b"}" else b"["
                if not stack or stack.pop() != expected:
                    raise ReportReadError("mismatched JSON container")
                if not stack:
                    return _decode(raw)
            else:
                raise ReportReadError("JSON escape outside string")
        raise ReportReadError("truncated JSON container or string")

    def object(self, depth: int, member: Callable[[str], object]) -> tuple[dict, set[str]]:
        self.whitespace()
        self.expect(b"{")
        result = {}
        seen = set()
        self.whitespace()
        if self.peek() == b"}":
            self.position += 1
            return result, seen
        while True:
            self.whitespace()
            if self.peek() != b'"':
                raise ReportReadError("JSON object key must be a string")
            key = self.value(depth)
            if key in seen:
                raise ReportReadError("duplicate JSON object key")
            seen.add(key)
            self.whitespace()
            self.expect(b":")
            value = member(key)
            if value is not _OMIT:
                result[key] = value
            self.whitespace()
            if self.peek() == b"}":
                self.position += 1
                return result, seen
            self.expect(b",")


class _ReportParser:
    def __init__(self, reader: _Reader, on_event: Callable[[str, dict], None]) -> None:
        self.reader = reader
        self.on_event = on_event

    def events(self, policy: str) -> object:
        reader = self.reader
        reader.whitespace()
        reader.expect(b"[")
        reader.whitespace()
        if reader.peek() == b"]":
            reader.position += 1
            return _OMIT
        while True:
            event = reader.value(4)
            if not isinstance(event, dict):
                raise ReportReadError("report event must be an object")
            self.on_event(policy, event)
            # Release the event even while the next bounded event is decoded.
            del event
            reader.whitespace()
            if reader.peek() == b"]":
                reader.position += 1
                return _OMIT
            reader.expect(b",")

    def variant(self, policy: str) -> dict:
        result, keys = self.reader.object(
            3,
            lambda key: self.events(policy) if key == "events" else self.reader.value(3),
        )
        if "events" not in keys:
            raise ReportReadError("policy is missing its events array")
        return result

    def variants(self) -> dict:
        def member(policy: str) -> dict:
            if policy not in _POLICIES:
                raise ReportReadError("unexpected report policy")
            return self.variant(policy)

        result, keys = self.reader.object(2, member)
        if keys != _POLICIES:
            raise ReportReadError("report requires exactly D+1 and D+2 policies")
        return result

    def report(self) -> dict:
        result, keys = self.reader.object(
            1,
            lambda key: self.variants() if key == "variants" else self.reader.value(1),
        )
        if "variants" not in keys:
            raise ReportReadError("report is missing variants")
        self.reader.expect(b"\n")
        if self.reader.peek():
            raise ReportReadError("extra data after report's final LF")
        return result


def read_report(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str,
    maximum_bytes: int,
    on_event: Callable[[str, dict], None],
    checkpoint: Callable[[], None] | None = None,
    maximum_value_bytes: int = 2 * 1024 * 1024,
) -> dict:
    """Return authenticated report metadata, omitting both policy events keys.

    Read three bounded passes: authenticate before any callback, parse/hash,
    and reauthenticate the path after callbacks. File identity/size/timestamps
    must also remain unchanged. ``checkpoint()`` runs before each bounded read
    in every pass and may raise to enforce a caller-owned budget. Invalid input
    raises ``ReportReadError``; callback/checkpoint and OS errors propagate.
    """
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        raise ValueError("expected_sha256 must be a SHA256 hexadecimal string")
    for name, value in (
        ("maximum_bytes", maximum_bytes),
        ("maximum_value_bytes", maximum_value_bytes),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not callable(on_event) or (checkpoint is not None and not callable(checkpoint)):
        raise TypeError("on_event and an optional checkpoint must be callable")
    checkpoint = checkpoint if checkpoint is not None else lambda: None
    expected_sha256 = expected_sha256.lower()
    path = Path(path)
    with path.open("rb") as stream:
        original_identity = _identity(stream)
        original_bytes = _authenticate(stream, expected_sha256, maximum_bytes, checkpoint)
        if _identity(stream) != original_identity:
            raise ReportReadError("report changed during initial authentication")
        stream.seek(0)
        reader = _Reader(stream, maximum_bytes, maximum_value_bytes, checkpoint)
        result = _ReportParser(reader, on_event).report()
        if reader.count != original_bytes or reader.digest.hexdigest() != expected_sha256:
            raise ReportReadError("report bytes changed during parsing")
        if _identity(stream) != original_identity:
            raise ReportReadError("report identity changed during parsing")
        with path.open("rb") as final_stream:
            if _identity(final_stream) != original_identity:
                raise ReportReadError("report path or metadata changed during parsing")
            final_bytes = _authenticate(final_stream, expected_sha256, maximum_bytes, checkpoint)
            if final_bytes != original_bytes or _identity(final_stream) != original_identity:
                raise ReportReadError("report changed during final authentication")
    return result
