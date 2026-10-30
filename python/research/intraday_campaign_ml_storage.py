"""Bounded exact ML report storage, shared by the runner and checkout tests."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from fractions import Fraction
import hashlib
import json
import os

from research import intraday_campaign_wsl_transport as wire


def store_json(path, value, maximum, checkpoint):
    """Stream new ML records without duplicating the complete report tree."""
    def default(item):
        if type(item) is Fraction:
            return {"numerator": str(item.numerator), "denominator": str(item.denominator)}
        if type(item) in (date, datetime):
            return item.isoformat()
        if is_dataclass(item) and not isinstance(item, type):
            return {field.name: getattr(item, field.name) for field in fields(item)}
        if isinstance(item, Mapping):
            return dict(item)  # Shallow node conversion; no recursive asdict/deepcopy.
        raise TypeError(type(item).__name__)

    wire.safe_path(path)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                               allow_nan=False, default=default)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    hashed, size, pending = hashlib.sha256(), 0, bytearray()
    with os.fdopen(descriptor, "wb") as handle:
        def flush():
            nonlocal size
            if pending:
                checkpoint("exact_output_store")
                size += len(pending)
                wire.require(size <= maximum, "exact output size exceeded")
                hashed.update(pending)
                wire.write_all(handle, pending)
                pending.clear()
        for text in encoder.iterencode(value):
            pending.extend(text.encode())
            if len(pending) >= wire.BLOCK:
                flush()
        pending.extend(b"\n")
        flush()
        handle.flush()
        os.fchmod(handle.fileno(), 0o400)
        os.fsync(handle.fileno())
    wire.fsync_directory(path.parent)
    pin = hashed.hexdigest()
    wire.require(wire.file_digest(path, maximum, checkpoint) == (pin, size), "exact store changed")
    return pin, size
