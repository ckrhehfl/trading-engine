"""Private immutable output for an admitted EL fold and its separate audit.

The outer runner authenticates the source and shards, admits the native runtime,
and journals STARTED before invoking these functions. This module neither opens
market archives directly nor issues an experiment or trading authorization.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
from itertools import zip_longest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_model_workflow as workflow
from research import intraday_event_model_inputs as inputs
from research import intraday_event_native as native
from research.intraday_event_comparison import compare
from research.intraday_event_comparison_verify import verify_replay_day
from research.intraday_event_dataset import MAX_FILE, MAX_ROW
from research.intraday_event_evaluation import _plain
from research.intraday_event_model_inputs import line, verified_stream


MANIFEST = "manifest.json"
FIT = "fit.json"
DOCUMENTS = ("model.json", "comparison.json")
STREAMS = ("predictions.jsonl", "ledger.jsonl")
MAX_DOCUMENT = 128 * wire.MIB


def _binding(identity, data):
    wire.require(identity["kind"] in workflow.KINDS and identity["scope"] == data.scope
                 and type(identity["fold"]) is int and identity["fold"] == data.fold
                 and type(identity["invented"]) is bool and wire.valid_sha(identity["inputs_sha256"]),
                 "EL fold identity differs from admitted inputs")


def _document(slot, name, value):
    raw = wire.encode(_plain(value))
    wire.require(len(raw) <= MAX_DOCUMENT, "EL fold document exceeds its size bound")
    wire.exclusive_bytes(slot / name, raw)
    return {"filename": name, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}


def _stream(slot, name, rows):
    path = slot / name
    wire.safe_path(path)
    digest, size, count = sha256(), 0, 0
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        for row in rows:
            raw = wire.encode(_plain(row))
            wire.require(len(raw) <= MAX_ROW and size + len(raw) <= MAX_FILE, "EL fold row or file exceeds its size bound")
            wire.write_all(stream, raw)
            digest.update(raw)
            size += len(raw)
            count += 1
        stream.flush()
        os.fsync(stream.fileno())
    wire.fsync_directory(slot)
    return {"filename": name, "sha256": digest.hexdigest(), "bytes": size, "rows": count}


def _read_document(slot, pin):
    raw = bridge.bounded_read(slot / pin["filename"], MAX_DOCUMENT)
    wire.require((sha256(raw).hexdigest(), len(raw)) == (pin["sha256"], pin["bytes"]), "EL fold document changed")
    value = json.loads(raw)
    wire.require(type(value) is dict and wire.encode(value) == raw, "EL fold document is noncanonical")
    return value


def _read_rows(slot, pin):
    if pin["rows"] == 0:
        wire.require(pin["bytes"] == 0 and bridge.bounded_read(slot / pin["filename"], 0) == b""
                     and pin["sha256"] == sha256(b"").hexdigest(), "EL empty stream changed")
        return
    with verified_stream(slot, pin) as stream:
        for _ in range(pin["rows"]):
            yield line(stream)
        wire.require(stream.read(1) == b"", "EL fold stream has unregistered rows")


def _persist_fit(slot, identity, data):
    """Retain fitted bytes and predictions before touching evaluation outcomes."""
    unit = workflow.fit_unit(identity["kind"], data)
    predictions = unit.pop("predictions")
    files = {"model.json": _document(slot, "model.json", unit),
             "predictions.jsonl": _stream(slot, "predictions.jsonl", predictions)}
    _document(slot, FIT, {"schema": "EL-fold-fit-v1", "identity": identity, "files": files})
    return {**unit, "predictions": predictions}, files


def publish_fold(slot, identity, data, roster, markets, targets, *, mark_fit_started=None):
    """Fit once, replay the frozen controls, and publish the manifest last."""
    slot = Path(slot)
    _binding(identity, data)
    wire.safe_path(slot)
    wire.require(slot.is_dir() and not any(os.path.lexists(slot / name) for name in (*DOCUMENTS, *STREAMS, FIT, MANIFEST)),
                 "EL fold output slot must be unused")
    if mark_fit_started is not None:
        mark_fit_started()
    unit, pins = _persist_fit(slot, identity, data)
    result = compare(unit, data, roster.episodes, roster.candidates, markets, targets)
    ledgers = result.pop("ledgers")
    result.pop("independent_cash_checks")
    pins.update({"comparison.json": _document(slot, "comparison.json", result),
                 "ledger.jsonl": _stream(slot, "ledger.jsonl", ledgers)})
    return _document(slot, MANIFEST, {"schema": "EL-fold-artifacts-v1", "identity": identity, "files": pins})


def read_manifest(slot, manifest_pin, identity):
    slot = Path(slot)
    wire.require(manifest_pin["filename"] == MANIFEST, "EL fold manifest name differs")
    manifest = _read_document(slot, manifest_pin)
    wire.require(manifest["schema"] == "EL-fold-artifacts-v1" and manifest["identity"] == identity
                 and set(manifest["files"]) == {*DOCUMENTS, *STREAMS}, "EL fold manifest identity or file roster differs")
    for name, pin in manifest["files"].items():
        is_stream = name in STREAMS
        wire.require(pin["filename"] == name and wire.valid_sha(pin["sha256"])
                     and type(pin["bytes"]) is int and 0 <= pin["bytes"] <= (MAX_FILE if is_stream else MAX_DOCUMENT)
                     and (not is_stream or (type(pin["rows"]) is int and 0 <= pin["rows"] <= pin["bytes"])),
                     "EL fold file pin differs")
    fit = bridge.bounded_read(slot / FIT, wire.MIB)
    expected = {"schema": "EL-fold-fit-v1", "identity": identity,
                "files": {name: manifest["files"][name] for name in ("model.json", "predictions.jsonl")}}
    wire.require(fit == wire.encode(expected), "EL retained fit receipt differs")
    return manifest


def audit_fold(slot, manifest_pin, identity, data, roster, markets, targets):
    """Verify the stored artifact and regenerate all results without refitting.

    The caller supplies the independent minute decoder. The verifier also uses
    separate sizing, receipt, holding feature and cash implementations.
    """
    slot = Path(slot)
    _binding(identity, data)
    pins = read_manifest(slot, manifest_pin, identity)["files"]
    unit = _read_document(slot, pins["model.json"])
    wire.require(unit["kind"] == identity["kind"], "EL saved model kind differs")
    unit["predictions"] = list(_read_rows(slot, pins["predictions.jsonl"]))
    forecast_audit = workflow.verify_unit(unit, data)
    result = compare(unit, data, roster.episodes, roster.candidates, markets, targets, verify_cash_day=verify_replay_day)
    ledgers, checks = result.pop("ledgers"), result.pop("independent_cash_checks")
    sentinel = object()
    for expected, saved in zip_longest(ledgers, _read_rows(slot, pins["ledger.jsonl"]), fillvalue=sentinel):
        wire.require(expected is not sentinel and saved is not sentinel and wire.encode(_plain(expected)) == wire.encode(saved),
                     "EL saved ledger differs from independently checked replay")
    wire.require(wire.encode(_plain(result)) == wire.encode(_read_document(slot, pins["comparison.json"])),
                 "EL saved comparison summary differs")
    return {"identity": identity, "manifest_sha256": manifest_pin["sha256"], "forecasts": forecast_audit,
            "cash_checks": checks, "ledger_rows": len(ledgers)}


def read_comparison(slot, manifest_pin, identity, *, stream_ledgers=False):
    """Read a caller-audited publication for pooling, checking the stored bytes."""
    slot = Path(slot)
    pins = read_manifest(slot, manifest_pin, identity)["files"]
    result = _read_document(slot, pins["comparison.json"])
    rows = _read_rows(slot, pins["ledger.jsonl"])
    result["ledgers"] = rows if stream_ledgers else tuple(rows)
    return result


def run_admitted_fold(slot, identity, shards, universe, reader, *, dataset_binding, scratch,
                      audit_manifest=None, mark_fit_started=None):
    """Run one journaled fold in the caller's isolated, bounded WSL worker.

    Each mode uses its own caller-created scratch directory. The
    external completion receipt must bind the manifest and runtime readback.
    """
    slot = Path(slot)
    shards = tuple(shards)
    wire.require(wire.valid_sha(dataset_binding) and identity["inputs_sha256"] == dataset_binding
                 and all(manifest["universe_sha256"] == universe.raw_sha256 for _, manifest in shards),
                 "EL fold input binding or market population differs")
    native_receipt = native.activate(identity["kind"], Path(scratch))
    data = inputs.load_fold(shards, identity["fold"], identity["scope"])
    wire.require(data.calendar == universe.sessions, "EL replay universe and model calendar differ")
    roster = inputs.decision_roster(shards, data)
    _, start, end = workflow.FOLDS[data.fold]
    markets = inputs.SessionMarkets(universe, reader, data.calendar[start:end], audit=audit_manifest is not None)

    def targets():
        return inputs.evaluation_targets(shards, data)

    if audit_manifest is None:
        result = {"manifest": publish_fold(slot, identity, data, roster, markets, targets,
                                           mark_fit_started=mark_fit_started)}
    else:
        wire.require(mark_fit_started is None, "EL audit must never start a fit")
        result = {"audit": audit_fold(slot, audit_manifest, identity, data, roster, markets, targets)}
    reader.assert_unchanged()
    return {**result, "runtime": native.verify_after(native_receipt)}
