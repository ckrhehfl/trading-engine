"""Run and verify one EF scenario and learner family on an authenticated seal.

The caller owns runtime admission, the independent snapshot receipt, and
resource enforcement. This module only opens the two injected snapshot files
and writes new private fit artifacts. Verification never refits a model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import stat

from research import intraday_campaign_wsl_transport as wire
from research.intraday_chart_folds import ENTRY_OFFSETS, FoldSelection
from research.intraday_chart_lightgbm import fit_lightgbm, predict_lightgbm, verify_lightgbm
from research.intraday_chart_management import UPDATE_MINUTES
from research.intraday_chart_model_inputs import ADMISSION_PCT, FitBinding, LGB_ARMS, TCN_ARMS, snapshot_fold
from research.intraday_chart_snapshot import SnapshotSeal, _checked_file, open_feature_snapshot, open_label_snapshot
from research.intraday_chart_tcn import HEADS
from research.intraday_chart_tcn_artifact import fit_sequence, predict_sequence, verify_sequence
from research.intraday_ed_sources import _private_parent


FIT_FILE = "ef-fit.jsonl.gz"
FIT_VERIFICATION_FILE = "ef-fit-verification.json"
FIT_MAX_BYTES = 128 * wire.MIB
SCHEMA = "EF-fold-fit-v1"
CONTROLS = ("TRAIN_COMMON_GROSS30_MEAN", "LAST15_CHART_RETURN")
_MAX_LINE = 4 * wire.MIB
_MAX_DECODED = 256 * wire.MIB
_WIDE_ARMS = ("TCN_CHART", "LGB_CHART")
_MANAGEMENT_ARMS = ("TCN_CHART", "TCN_CHART_PEER")


def _binding(seal, source_sha256, runtime_sha256, registration_sha256, scenario, family):
    wire.require(type(seal) is SnapshotSeal and scenario in ("S1", "S2") and family in ("TCN", "LGB"),
                 "registered EF fit scenario and family required")
    result = {**asdict(seal), "source_sha256": source_sha256, "runtime_sha256": runtime_sha256,
              "registration_sha256": registration_sha256}
    wire.require(all(wire.valid_sha(value) for value in result.values()), "exact EF fit pins required")
    return {**result, "scenario": scenario, "family": family}


def _arm_binding(binding, fold, arm):
    return FitBinding(binding["source_sha256"], binding["runtime_sha256"], binding["registration_sha256"],
                      binding["features_sha256"], binding["labels_sha256"], binding["scenario"], fold, arm)


def _arms(family):
    return TCN_ARMS if family == "TCN" else (*LGB_ARMS, *CONTROLS)


def _header(binding):
    return {"type": "header", "schema": SCHEMA, "binding": binding}


def _split(features, labels, scenario, fold) -> FoldSelection:
    selected = snapshot_fold(features, labels, scenario, fold)
    sessions = set(selected.fold.test_sessions)
    original = sorted((target.session, offset, target.symbol) for target in features.metadata.targets
                      if target.scenario == scenario and target.session in sessions for offset in ENTRY_OFFSETS)
    selected_keys = [(day, offset, symbol) for day, symbol, offset in (row.key for row in selected.evaluation)]
    wire.require(original == selected_keys,
                 "EF original evaluation coordinates differ")
    return selected


def _fold_record(split):
    return {"type": "fold", "fold": split.fold.index, "training_sessions": split.fold.training_sessions,
            "evaluation_sessions": split.fold.test_sessions, "cutoff": split.fold.first_test_opening.isoformat(),
            "counts": dict(split.counts), "supported": split.supported}


def _entry_status(split, arm, support):
    if support in ("outside_clock", "chart_unavailable"):
        return support
    if support == "chart_only" and arm not in _WIDE_ARMS:
        return "peer_unavailable"
    return "scored" if split.supported or arm == "LAST15_CHART_RETURN" else "fold_unsupported"


def _updates(features, scenario, arm, key, support, score):
    if arm not in _MANAGEMENT_ARMS or support != "common" or score is None or score[0] <= ADMISSION_PCT:
        return ()
    day, symbol, offset = key
    result = []
    for after in UPDATE_MINUTES:
        update_key = day, symbol, offset + after
        row = features.row(*update_key)
        wire.require((row.session, row.symbol, row.offset) == update_key, "EF update coordinate differs")
        status = "scored"
        if row.chart is None:
            status = "chart_unavailable"
        elif arm == "TCN_CHART_PEER":
            peers = tuple(peer.tensor for peer in row.peers if peer.scenario == scenario)
            wire.require(len(peers) == 1, "EF update peer roster differs")
            if peers[0] is None:
                status = "peer_unavailable"
        result.append((after, update_key, status))
    return tuple(result)


def _control(features, split, arm, keys):
    if arm == "TRAIN_COMMON_GROSS30_MEAN":
        mean = split.train_mean_pct
        return () if not keys else ((float(mean),),) * len(keys)
    forecasts = []
    for key in keys:
        row = features.row(*key)
        wire.require(row.chart is not None and (row.session, row.symbol, row.offset) == key,
                     "EF last15 chart coordinate differs")
        first_open = row.chart.minute[-15][0]
        last_close = row.chart.minute[-1][3]
        scale = 1 + first_open / 100
        wire.require(scale > 0, "EF last15 chart anchor differs")
        forecasts.append(((last_close - first_open) / scale,))
    return tuple(forecasts)


def _predict(features, split, binding, model, keys):
    if not keys:
        return ()
    if binding.arm in TCN_ARMS:
        return predict_sequence(features, keys, binding, model)
    if binding.arm in LGB_ARMS:
        return tuple((value,) for value in predict_lightgbm(features, keys, binding, model))
    return _control(features, split, binding.arm, keys)


def _fit(features, split, binding):
    if split.supported:
        if binding.arm in TCN_ARMS:
            return fit_sequence(features, split, binding)
        if binding.arm in LGB_ARMS:
            return fit_lightgbm(features, split, binding)
    return None


def _model_record(binding, model):
    return {"type": "model", "fold": binding.fold, "arm": binding.arm, "model": model}


def _forecast_record(binding, row, status, score, updates):
    return {"type": "forecast", "fold": binding.fold, "arm": binding.arm, "key": row.key,
            "support": row.status, "status": status, "predictions_pct": score, "updates": updates}


def _update_record(after, status, score):
    return {"after_minutes": after, "status": status, "predictions_pct": score}


def _forecasts(features, split, binding, model):
    entries = tuple((row, _entry_status(split, binding.arm, row.status)) for row in split.evaluation)
    keys = tuple(row.key for row, status in entries if status == "scored")
    entry_scores = dict(zip(keys, _predict(features, split, binding, model, keys), strict=True))
    updates = {row.key: _updates(features, binding.scenario, binding.arm, row.key, row.status, entry_scores.get(row.key))
               for row, _ in entries}
    update_keys = tuple(key for items in updates.values() for _, key, status in items if status == "scored")
    update_scores = dict(zip(update_keys, _predict(features, split, binding, model, update_keys), strict=True))
    for row, status in entries:
        yield _forecast_record(binding, row, status, entry_scores.get(row.key), [
            _update_record(after, state, update_scores.get(key)) for after, key, state in updates[row.key]
        ])


def _records(features, labels, binding, checkpoint):
    yield _header(binding)
    for fold in range(1, 6):
        checkpoint("ef_fit_fold")
        split = _split(features, labels, binding["scenario"], fold)
        yield _fold_record(split)
        for arm in _arms(binding["family"]):
            checkpoint("ef_fit_arm")
            bound = _arm_binding(binding, fold, arm)
            model = _fit(features, split, bound)
            yield _model_record(bound, model)
            yield from _forecasts(features, split, bound, model)


def _write_artifact(path, records, checkpoint):
    parent = _private_parent(path)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(path.name, flags, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as raw:
            total = 0
            with gzip.GzipFile(filename="", mode="wb", compresslevel=1, fileobj=raw, mtime=0) as output:
                for index, record in enumerate(records):
                    if index % 1024 == 0:
                        checkpoint("ef_fit_write")
                    line = wire.encode(record)
                    total += len(line)
                    wire.require(len(line) <= _MAX_LINE and total <= _MAX_DECODED and raw.tell() <= FIT_MAX_BYTES,
                                 "EF fit artifact bound exceeded")
                    wire.write_all(output, line)
            wire.require(raw.tell() <= FIT_MAX_BYTES, "EF fit artifact bound exceeded")
            raw.flush()
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
        os.fsync(parent)
    finally:
        os.close(parent)


@contextmanager
def _open_artifact(path):
    parent = _private_parent(path)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as raw:
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            wire.require(mode in (0o400, 0o600), "EF fit artifact must be private")
            before = _checked_file(parent, path.name, descriptor, mode)
            wire.require(0 < before[4] <= FIT_MAX_BYTES, "EF fit artifact size differs")
            yield raw
            wire.require(_checked_file(parent, path.name, descriptor, mode) == before,
                         "EF fit artifact changed while reading")
    finally:
        os.close(parent)


def _read_records(raw, checkpoint):
    total, index = 0, 0
    with gzip.GzipFile(mode="rb", fileobj=raw) as stream:
        while line := stream.readline(_MAX_LINE + 1):
            if index % 1024 == 0:
                checkpoint("ef_fit_readback")
            index += 1
            total += len(line)
            wire.require(len(line) <= _MAX_LINE and total <= _MAX_DECODED, "EF fit readback bound exceeded")
            value = json.loads(line)
            wire.require(type(value) is dict and wire.encode(value) == line, "noncanonical EF fit record")
            yield value


def iter_fit_records(path: Path, checkpoint) -> Iterator[dict]:
    """Stream canonical records for a caller that already checked its fit receipt."""
    with _open_artifact(path) as raw:
        yield from _read_records(raw, checkpoint)


def _match(actual, expected, message):
    wire.require(wire.encode(actual) == wire.encode(expected), message)


def _score(value, status, width):
    if status != "scored":
        wire.require(value is None, "unavailable EF forecast has a value")
        return None
    wire.require(type(value) is list and len(value) == width
                 and all(type(item) is float and math.isfinite(item) for item in value), "EF fit forecast shape differs")
    return tuple(value)


def _read_arm(features, split, bound, model, records):
    width = HEADS if bound.arm in TCN_ARMS else 1
    entries, updates = [], []
    counts: Counter[str] = Counter()
    for row in split.evaluation:
        saved = next(records, {})
        status = _entry_status(split, bound.arm, row.status)
        score = _score(saved.get("predictions_pct"), status, width)
        counts[f"{row.status}/{status}"] += 1
        if score is not None:
            entries.append((row.key, score))
        expected_updates = _updates(features, bound.scenario, bound.arm, row.key, row.status, score)
        saved_updates = saved.get("updates")
        wire.require(type(saved_updates) is list and len(saved_updates) == len(expected_updates),
                     "EF fit management update roster differs")
        checked_updates = []
        for (after, key, state), item in zip(expected_updates, saved_updates, strict=True):
            wire.require(type(item) is dict, "EF fit management update shape differs")
            updated = _score(item.get("predictions_pct"), state, width)
            counts[f"update/{state}"] += 1
            if updated is not None:
                updates.append((key, updated))
            checked_updates.append(_update_record(after, state, updated))
        _match(saved, _forecast_record(bound, row, status, score, checked_updates), "EF fit forecast row differs")

    keys = tuple(key for key, _ in (*entries, *updates))
    scores = tuple(value for _, value in (*entries, *updates))
    replay = None
    if model is not None:
        if bound.arm in TCN_ARMS:
            replay = verify_sequence(features, split, keys, bound, model, scores)
        else:
            replay = verify_lightgbm(features, split, keys, bound, model, tuple(score[0] for score in scores))
        wire.require(replay["verified"] is True and replay["rows"] == len(keys), "EF fit replay is incomplete")
    elif bound.arm in CONTROLS:
        wire.require(_control(features, split, bound.arm, keys) == scores, "EF fit control forecast differs")
    return {"fold": bound.fold, "arm": bound.arm, "counts": dict(counts), "replay": replay}


def _verify_artifact(path, features, labels, binding, checkpoint):
    with _open_artifact(path) as raw:
        hashed, size = hashlib.sha256(), 0
        while block := raw.read(wire.MIB):
            checkpoint("ef_fit_hash")
            size += len(block)
            wire.require(size <= FIT_MAX_BYTES, "EF fit artifact size differs")
            hashed.update(block)
        raw.seek(0)
        records = _read_records(raw, checkpoint)
        _match(next(records, {}), _header(binding), "EF fit header differs")
        receipts = []
        forecast_rows = 0
        for fold in range(1, 6):
            checkpoint("ef_fit_verify_fold")
            split = _split(features, labels, binding["scenario"], fold)
            _match(next(records, {}), _fold_record(split), "EF fit fold chronology or support differs")
            arms = _arms(binding["family"])
            forecast_rows += len(split.evaluation) * len(arms)
            for arm in arms:
                checkpoint("ef_fit_verify_arm")
                bound = _arm_binding(binding, fold, arm)
                record = next(records, {})
                model = record.get("model")
                wire.require((type(model) is dict) if split.supported and arm not in CONTROLS else model is None,
                             "EF fit model availability differs")
                _match(record, _model_record(bound, model), "EF fit model roster differs")
                receipts.append(_read_arm(features, split, bound, model, records))
        wire.require(next(records, None) is None, "unexpected trailing EF fit records")
    return {"schema": "EF-fold-fit-verification-v1", "verified": True, "binding": binding,
            "artifact_sha256": hashed.hexdigest(), "artifact_size": size, "fold_arms": receipts,
            "fits": sum(row["replay"] is not None for row in receipts), "forecast_rows": forecast_rows}


def run_fit(directory: Path, features_path: Path, labels_path: Path, seal: SnapshotSeal, *, source_sha256: str,
            runtime_sha256: str, registration_sha256: str, scenario: str, family: str, checkpoint) -> dict:
    """Create a new fit artifact and publish a receipt only after replay passes."""
    binding = _binding(seal, source_sha256, runtime_sha256, registration_sha256, scenario, family)
    path = directory / FIT_FILE
    with open_feature_snapshot(features_path, seal, checkpoint) as features, \
            open_label_snapshot(labels_path, seal, checkpoint) as labels:
        _write_artifact(path, _records(features, labels, binding, checkpoint), checkpoint)
        receipt = _verify_artifact(path, features, labels, binding, checkpoint)
    wire.exclusive_bytes(directory / FIT_VERIFICATION_FILE, wire.encode(receipt))
    return receipt


def verify_fit(artifact_path: Path, features_path: Path, labels_path: Path, seal: SnapshotSeal, *, source_sha256: str,
               runtime_sha256: str, registration_sha256: str, scenario: str, family: str, checkpoint) -> dict:
    """Independently read back a fit artifact using only its saved coefficients."""
    binding = _binding(seal, source_sha256, runtime_sha256, registration_sha256, scenario, family)
    with open_feature_snapshot(features_path, seal, checkpoint) as features, \
            open_label_snapshot(labels_path, seal, checkpoint) as labels:
        return _verify_artifact(artifact_path, features, labels, binding, checkpoint)
