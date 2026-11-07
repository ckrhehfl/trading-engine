"""Run all registered expanding folds for one EH population from a sealed snapshot."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_scenario_forecast_report import combine_fold_reports
from research.intraday_scenario_forecast_saved import ForecastSnapshot, fit_saved_fold, score_saved_fold
from research.intraday_scenario_snapshot import FEATURE_SCHEMA, MAX_DAYS, SnapshotSeal, _read
from research.intraday_scenario_snapshot_unit import BundleSeal, open_bundle


SCHEMA = "eh-forecast-fold-bundle-v1"
MAXIMUM_BYTES = 64 * wire.MIB
MAXIMUM_HEADER = wire.MIB
ORIGINAL_PER_DAY = 20


@dataclass(frozen=True)
class ForecastUnitResult:
    seal: BundleSeal
    summary: dict


def _calendar(snapshot: ForecastSnapshot):
    days = tuple(row["day"] for row, _ in _read(snapshot.features, snapshot.feature_seal,
                                              {"schema": FEATURE_SCHEMA}, maximum=MAX_DAYS))
    return fixed_calendar(days)


def _report(report) -> dict:
    output = asdict(report)
    for group in output["groups"]:
        scores = group["counts"]
        scores["daily_difference"] = tuple((day.isoformat(), deltas) for day, deltas in scores["daily_difference"])
    return output


def _pack(output: Path, header: dict, folds: tuple[tuple[Path, SnapshotSeal], ...], checkpoint) -> BundleSeal:
    raw_header = wire.encode(header)
    total = len(raw_header) + sum(seal.size for _, seal in folds)
    wire.require(len(raw_header) <= MAXIMUM_HEADER and total <= MAXIMUM_BYTES, "EH forecast bundle bounds exceeded")
    wire.safe_path(output)
    digest = sha256(raw_header)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as destination:
        wire.write_all(destination, raw_header)
        for source, seal in folds:
            before = wire.regular(source)
            wire.require(before.st_size == seal.size and before.st_mode & 0o777 == 0o400,
                         "EH forecast bundle member size or mode differs")
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                member, remaining = sha256(), seal.size
                while remaining:
                    checkpoint("eh_forecast_pack_fold")
                    raw = stream.read(min(wire.BLOCK, remaining))
                    wire.require(bool(raw), "EH forecast bundle member truncated")
                    remaining -= len(raw)
                    member.update(raw)
                    digest.update(raw)
                    wire.write_all(destination, raw)
                wire.require(not stream.read(1) and member.hexdigest() == seal.sha256
                             and wire.file_identity(before) == wire.file_identity(os.fstat(stream.fileno()))
                             == wire.file_identity(wire.regular(source)), "EH forecast bundle member changed")
        destination.flush()
        os.fchmod(destination.fileno(), 0o400)
        os.fsync(destination.fileno())
    wire.fsync_directory(output.parent)
    wire.require(wire.file_digest(output, MAXIMUM_BYTES, checkpoint) == (digest.hexdigest(), total),
                 "EH forecast bundle readback differs")
    return BundleSeal(digest.hexdigest(), total)


def compare_snapshot(snapshot_bundle: Path, output: Path, *, scenario: str, expected_members: dict,
                     checkpoint) -> ForecastUnitResult:
    """Consume only the transferred snapshot and keep each fold's predictions and scores."""
    wire.require(scenario in ("S1", "S2") and snapshot_bundle != output, "EH forecast population or output differs")
    with TemporaryDirectory(prefix="eh-forecast-folds-", dir=output.parent) as scratch:
        folder = Path(scratch)
        input_folder = folder / "snapshot"
        input_folder.mkdir(mode=0o700)
        snapshot = ForecastSnapshot(*open_bundle(snapshot_bundle, input_folder, checkpoint))
        actual_members = {"features": asdict(snapshot.feature_seal), "labels": asdict(snapshot.label_seal)}
        wire.require(actual_members == expected_members, "EH forecast snapshot members differ from audited predecessor")
        calendar = _calendar(snapshot)
        folds, reports, scored = [], [], []
        for fold in calendar.folds:
            checkpoint("eh_forecast_start_fold")
            predictions = folder / f"fold-{fold.index}.jsonl"
            seal = fit_saved_fold(snapshot, fold, scenario, predictions, checkpoint)
            report = score_saved_fold(snapshot, fold, scenario, predictions, seal, checkpoint)
            wire.require(report.original_coordinates == ORIGINAL_PER_DAY * len(fold.test_sessions),
                         "EH forecast original population differs")
            folds.append((predictions, seal))
            scored.append(report)
            reports.append({"fold": fold.index, "test_sessions": fold.test_sessions, "seal": asdict(seal), "score": _report(report)})
        header = {"schema": SCHEMA, "scenario": scenario, "snapshot": actual_members, "folds": reports,
                  "date_weighted_comparisons": combine_fold_reports(tuple(scored))}
        sealed = _pack(output, header, tuple(folds), checkpoint)
    summary = {"schema": SCHEMA, "scenario": scenario, "input_members": actual_members,
               "folds": len(reports), "evaluation_sessions": len(calendar.evaluation_reference_sessions),
               "original_evaluation_coordinates": sum(item["score"]["original_coordinates"] for item in reports),
               "fold_seals": [item["seal"] for item in reports], "full_forecast_and_score_verification": True}
    return ForecastUnitResult(sealed, summary)
