"""One registered EG fit unit over the split scenario snapshot.

The caller authenticates the prior receipt and qualifies the runtime. This
module checks the receipt bindings and consumes no test outcome records.
"""

from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from fractions import Fraction
import gzip
import hashlib
import json
from math import isfinite
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_models as models
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_wsl_bridge import bounded_read
from research.intraday_highvol_coverage import CoverageReader, CoverageSeal, MAX_COMPRESSED, MAX_DECODED, MAX_LINE, open_coverage
from research.intraday_highvol_model_dataset import assemble_fold
from research.intraday_highvol_snapshot_io import FILENAMES, ROLES, SCHEMAS, open_snapshot


OUTPUT = "eg-fit-receipt.json"
SCHEMA = "EG-model-fit-coordinate-v1"
RECEIPT_SCHEMA = "EG-model-fit-receipt-v1"


@dataclass(frozen=True)
class FitCoordinate:
    branch: str
    timing: str
    fold_index: int
    arm: str

    @property
    def name(self) -> str:
        return f"eg-fit-{self.branch}-{self.timing}-{self.fold_index}-{self.arm}.jsonl.gz"


def fit_roster(unit_id: str) -> tuple[FitCoordinate, ...]:
    """Return the only accepted model coordinates in their fixed order."""
    for branch in ("A", "B"):
        if unit_id == f"EG-LGB-{branch}-1":
            return tuple(FitCoordinate(branch, timing, fold, "LGB_CHART")
                         for timing in ("S1", "S2") for fold in range(1, 6))
        for timing in ("S1", "S2"):
            if unit_id == f"EG-TCN-{branch}-{timing}-1":
                return tuple(FitCoordinate(branch, timing, fold, arm) for fold in range(1, 6)
                             for arm in ("TCN_CHART", "TCN_CHART_PEER"))
    raise ValueError("unregistered EG model fit unit")


def _seal(raw: dict) -> CoverageSeal:
    result = CoverageSeal(**raw)
    wire.require(wire.valid_sha(result.sha256) and wire.valid_sha(result.decoded_sha256)
                 and type(result.compressed_bytes) is int and 0 < result.compressed_bytes <= MAX_COMPRESSED
                 and type(result.decoded_bytes) is int and 0 < result.decoded_bytes <= MAX_DECODED
                 and type(result.records) is int and result.records >= 0, "EG fit snapshot seal differs")
    return result


def _snapshot_contract(snapshot_receipt, binding, unit_id):
    wire.require(type(binding) is dict and binding.get("unit_id") == unit_id
                 and type(binding.get("invented_only")) is bool
                 and all(wire.valid_sha(binding.get(key)) for key in
                         ("source_sha256", "runtime_sha256", "registration_sha256"))
                 and json.loads(wire.encode(binding)) == binding, "EG fit binding differs")
    predecessor = snapshot_receipt["binding"]
    independent = snapshot_receipt["independent_verification"]
    report = snapshot_receipt["report"]
    seals = {role: _seal(snapshot_receipt["seals"][role]) for role in ROLES}
    wire.require(snapshot_receipt["schema"] == "EG-scenario-snapshot-receipt-v1"
                 and predecessor["unit_id"] == "EG-SCENARIO-SNAPSHOT-1"
                 and predecessor["invented_only"] is binding["invented_only"]
                 and independent["complete"] is True and type(independent["events"]) is int
                 and independent["events"] == report["union_events"] == seals["features"].records == seals["labels"].records
                 and independent["report_sha256"] == wire.digest(wire.encode(report)), "EG fit predecessor receipt differs")
    pins = {"receipt_sha256": wire.digest(wire.encode(snapshot_receipt)),
            **{f"{role}_sha256": seals[role].sha256 for role in ROLES}}
    return seals, pins


@contextmanager
def _labels_prefix(directory, seal, checkpoint):
    """Authenticate all compressed bytes, without reading the outcome footer."""
    path = directory / FILENAMES["labels"]
    before = wire.file_identity(wire.regular(path))
    wire.require(wire.file_digest(path, MAX_COMPRESSED, checkpoint) == (seal.sha256, seal.compressed_bytes),
                 "EG fit compressed outcome seal differs")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as zipped:
        wire.require(wire.file_identity(os.fstat(raw.fileno())) == before, "EG fit outcome changed before open")
        yield CoverageReader(zipped, seal, checkpoint, schema=SCHEMAS["labels"])
        wire.require(wire.file_identity(os.fstat(raw.fileno())) == before == wire.file_identity(wire.regular(path)),
                     "EG fit outcome changed during prefix read")


def _dataset(directory, seals, predecessor, coordinate, checkpoint):
    with open_snapshot(directory, "features", seals["features"], checkpoint) as features, \
            _labels_prefix(directory, seals["labels"], checkpoint) as labels:
        universe = features.header["universe"]
        wire.require(features.header["binding"] == labels.header["binding"] == predecessor
                     and universe == labels.header["universe"], "EG fit snapshot headers differ")
        dates = universe["sessions"]
        fold = fixed_calendar(dates).folds[coordinate.fold_index - 1]
        first_test = fold.test_sessions[0]
        prefix_keys = []

        def feature_records():
            for frame in features.records():
                key = tuple(frame["key"])
                if key[0] < first_test:
                    prefix_keys.append(key)
                yield frame

        def paired_prefix():
            records = labels.records()
            for key in prefix_keys:
                outcome = next(records, None)
                wire.require(outcome is not None and tuple(outcome["key"]) == key,
                             "EG fit outcome prefix key differs")
                yield key, outcome

        pairs = paired_prefix()

        def training_label(wanted):
            wire.require(wanted[0] < first_test, "EG fit attempted a test outcome read")
            for key, outcome in pairs:
                if key == wanted:
                    return outcome
            raise ValueError("EG fit common training key absent from outcome prefix")

        dataset = assemble_fold(dates, coordinate.branch, coordinate.timing, coordinate.fold_index,
                                feature_records(), training_label)
        for _ in pairs:
            pass
        wire.require(labels.count == len(prefix_keys) and not labels.completed
                     and features.footer["report"] == {"union_events": seals["features"].records},
                     "EG fit prefix population or feature footer differs")
    return dataset, len(prefix_keys)


def _metadata(dataset, prefix, coordinate, snapshot, binding):
    selection = dataset.selection
    fold = selection.fold
    training = tuple(row for _, row in dataset.training_for(coordinate.arm))
    queried = dataset.predictions_for(coordinate.arm)
    target_counts = [sum(row.target_first is value for row in dataset.training) for value in (False, True)]
    observed = sum(target_counts)
    rate = Fraction(target_counts[1], observed) if observed else None
    fit_status = "fold_unsupported" if not selection.supported else "fit" if queried else "no_prediction_inputs"
    metadata = {"kind": "fit", "binding": {**binding, "snapshot": snapshot, "coordinate": asdict(coordinate)},
                "training_sessions": list(fold.training_sessions), "test_sessions": list(fold.test_sessions),
                "first_test_opening": fold.first_test_opening.isoformat(), "training_prefix_records": prefix,
                "selection_counts": dict(selection.counts), "training_task_counts": dict(dataset.counts),
                "target_first_false_true_rows": target_counts,
                "target_first_train_rate": None if rate is None else [rate.numerator, rate.denominator],
                "fit_status": fit_status}
    return metadata, training, queried


def _candidate(row, score, eligible, fit_status):
    if score is not None:
        status = ("task_unsupported" if row.key[3] and score.hold_advantage_pct is None else "scored")
    elif row.support in ("outside_clock", "chart_unavailable"):
        status = row.support
    elif row.entry_support == "chart_unavailable":
        status = "entry_chart_unavailable"
    elif not eligible:
        status = "peer_unavailable"
    else:
        status = fit_status
    return {"kind": "candidate", "key": list(row.key), "decision_at": row.decision_at.isoformat(),
            "support": row.support, "entry_support": row.entry_support, "status": status,
            "comparison_scope": ("common" if row.support == row.entry_support == "common" else "chart_only")
            if eligible else None, "prediction": None if score is None else asdict(score)}


def _score(raw, *, holding):
    if raw is None:
        return None
    result = models.ModelPrediction(**raw)
    gross, prob, hold, raw_score = (result.gross30_pct, result.target_first_probability,
                                   result.hold_advantage_pct, result.target_first_raw)
    wire.require(all(value is None or (type(value) is float and isfinite(value))
                     for value in (gross, prob, hold, raw_score))
                 and type(result.target_first_clipped) is bool and (prob is None) == (raw_score is None)
                 and (prob is None or 0 <= prob <= 1) and (prob is not None or not result.target_first_clipped)
                 and (gross is prob is None if holding else gross is not None and hold is None),
                 "EG fit forecast fields differ")
    return result


def _write_coordinate(directory, coordinate, metadata, model, dataset, forecasts, queried, checkpoint):
    path = directory / coordinate.name
    wire.safe_path(path)
    digest = hashlib.sha256()
    size = records = 0
    counts = Counter()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=3) as zipped:
            def write(frame):
                nonlocal size
                encoded = wire.encode(frame)
                size += len(encoded)
                wire.require(len(encoded) <= MAX_LINE and size <= MAX_DECODED, "EG fit decoded output bound exceeded")
                digest.update(encoded)
                zipped.write(encoded)
                wire.require(raw.tell() <= MAX_COMPRESSED, "EG fit compressed output bound exceeded")

            write({"kind": "header", "schema": SCHEMA, "binding": metadata["binding"], "universe": {}})
            write({"kind": "record", "value": {**metadata, "model": model}})
            records += 1
            for row in dataset.inference:
                checkpoint("eg_fit_write_candidate")
                value = _candidate(row, forecasts.get(row.key), row.key in queried, metadata["fit_status"])
                counts[value["status"]] += 1
                write({"kind": "record", "value": value})
                records += 1
            write({"kind": "footer", "records": records, "report": dict(counts)})
        raw.flush()
        os.fsync(raw.fileno())
    sha, compressed = wire.file_digest(path, MAX_COMPRESSED, checkpoint)
    return asdict(CoverageSeal(sha, compressed, digest.hexdigest(), size, records)), dict(counts)


def _work(directory, snapshot_directory, snapshot_receipt, binding, unit_id, checkpoint, stored):
    roster = fit_roster(unit_id)
    seals, snapshot = _snapshot_contract(snapshot_receipt, binding, unit_id)
    fit_count, coordinates, proof = 0, [], []
    cached, dataset = None, None
    for index, coordinate in enumerate(roster):
        checkpoint("eg_fit_coordinate")
        identity = coordinate.branch, coordinate.timing, coordinate.fold_index
        if cached != identity:
            del dataset
            dataset, prefix = _dataset(snapshot_directory, seals, snapshot_receipt["binding"], coordinate, checkpoint)
            cached = identity
        metadata, training, queried = _metadata(dataset, prefix, coordinate, snapshot, binding)
        fitted = metadata["fit_status"] == "fit"
        fit_count += fitted
        inputs = tuple(value for _, value in queried)
        keys = tuple(key for key, _ in queried)
        eligible = set(keys)
        if stored is None:
            artifact = models.fit(coordinate.arm, training, binding=metadata["binding"],
                                  fold_index=coordinate.fold_index) if fitted else None
            predicted = models.predict(coordinate.arm, inputs, artifact, binding=metadata["binding"]) if fitted else ()
            forecasts = dict(zip(keys, predicted, strict=True)) if fitted else {}
            for key, score in forecasts.items():
                wire.require(_score(asdict(score), holding=bool(key[3])) is not None, "EG fit prediction missing")
            seal, counts = _write_coordinate(directory, coordinate, metadata, artifact, dataset, forecasts,
                                             eligible, checkpoint)
        else:
            pin = stored["coordinates"][index]
            wire.require(pin["name"] == coordinate.name, "EG fit coordinate name differs")
            seal = pin["seal"]
            counts, report = _verify_coordinate(directory, coordinate, _seal(seal), metadata, dataset, training,
                                                inputs, keys, eligible, checkpoint)
            proof.append({"coordinate": asdict(coordinate), **report})
        coordinates.append({"name": coordinate.name, "coordinate": asdict(coordinate), "seal": seal,
                            "fit_status": metadata["fit_status"], "candidate_counts": counts})
        del training, queried, inputs
    receipt = {"schema": RECEIPT_SCHEMA, "binding": binding, "snapshot": snapshot, "fit_count": fit_count,
               "coordinates": coordinates, "maximum_fits": len(roster)}
    return receipt, proof


def _verify_coordinate(directory, coordinate, seal, metadata, dataset, training, inputs, keys, eligible, checkpoint):
    fitted = metadata["fit_status"] == "fit"
    predictions = []
    counts = Counter()
    with open_coverage(directory / coordinate.name, seal, checkpoint, schema=SCHEMA) as reader:
        wire.require(reader.header["binding"] == metadata["binding"] and reader.header["universe"] == {},
                     "EG fit coordinate header differs")
        records = reader.records()
        body = next(records)
        model = body.pop("model")
        wire.require(body == metadata and (type(model) is dict if fitted else model is None),
                     "EG fit coordinate metadata differs")
        for row in dataset.inference:
            candidate = next(records, None)
            wire.require(type(candidate) is dict, "EG fit candidate missing")
            score = _score(candidate["prediction"], holding=bool(row.key[3]))
            wire.require((score is not None) == (fitted and row.key in eligible)
                         and candidate == _candidate(row, score, row.key in eligible, metadata["fit_status"]),
                         "EG fit candidate population or status differs")
            counts[candidate["status"]] += 1
            if score is not None:
                predictions.append(score)
        wire.require(next(records, None) is None and reader.footer["report"] == dict(counts),
                     "EG fit candidate footer differs")
    if not fitted:
        return dict(counts), {"status": metadata["fit_status"], "replayed_rows": 0}
    report = models.verify(coordinate.arm, training, inputs, model, predictions, binding=metadata["binding"],
                           fold_index=coordinate.fold_index)
    wire.require(report["verified"] is True and report["rows"] == len(keys)
                 and all(wire.valid_sha(report[field]) for field in ("artifact_sha256", "inputs_sha256"))
                 and report["target_first_clipped_rows"] == sum(row.target_first_clipped for row in predictions),
                 "EG fit replay count or digest differs")
    ambiguity = report["threshold_ambiguous_rows"]
    wire.require(set(ambiguity) == {"gross33", "gross43", "hold_zero"}
                 and all(all(type(index) is int and 0 <= index < len(keys) for index in items)
                         and items == sorted(set(items)) for items in ambiguity.values()),
                 "EG fit replay ambiguity indices differ")
    return dict(counts), {"status": "verified", "replayed_rows": report["rows"],
                          "artifact_sha256": report["artifact_sha256"], "inputs_sha256": report["inputs_sha256"],
                          "target_first_clipped_rows": report["target_first_clipped_rows"],
                          "threshold_ambiguous_keys": {field: [list(keys[index]) for index in indices]
                                                       for field, indices in ambiguity.items()}}


def produce_fit(directory: Path, snapshot_directory: Path, snapshot_receipt: dict, binding: dict,
                unit_id: str, checkpoint) -> dict:
    """Write new fit evidence. Prior artifacts and failed attempts stay intact."""
    wire.safe_path(directory)
    names = (OUTPUT, *(coordinate.name for coordinate in fit_roster(unit_id)))
    wire.require(directory.is_dir() and all(not (directory / name).exists() and not (directory / name).is_symlink()
                                            for name in names), "EG fit output already exists")
    receipt, _ = _work(directory, snapshot_directory, snapshot_receipt, binding, unit_id, checkpoint, None)
    wire.exclusive_bytes(directory / OUTPUT, wire.encode(receipt))
    return receipt


def verify_fit(directory: Path, snapshot_directory: Path, snapshot_receipt: dict, binding: dict,
               unit_id: str, checkpoint) -> dict:
    """Return independent saved-coefficient replay proof, without any refit."""
    raw = bounded_read(directory / OUTPUT, wire.MIB)
    stored = json.loads(raw)
    wire.require(raw == wire.encode(stored) and stored["schema"] == RECEIPT_SCHEMA
                 and len(stored["coordinates"]) == len(fit_roster(unit_id)), "EG fit receipt differs")
    receipt, proof = _work(directory, snapshot_directory, snapshot_receipt, binding, unit_id, checkpoint, stored)
    wire.require(receipt == stored, "EG fit receipt reconstruction differs")
    return {"schema": "EG-model-fit-verification-v1", "complete": True, "receipt_sha256": wire.digest(raw),
            "fit_count": receipt["fit_count"], "refit_count": 0, "coordinates": proof}
