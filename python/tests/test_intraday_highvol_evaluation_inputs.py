"""Invented split snapshot and six fit receipts, with no native learners."""

from dataclasses import asdict
from fractions import Fraction
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_evaluation_inputs as ev
from research.intraday_highvol_coverage import CoverageReader
from research.intraday_highvol_models import ModelPrediction
from research.intraday_highvol_snapshot_io import SCHEMAS, write_snapshot
from test_intraday_highvol_model_dataset import DAYS, frame, label


ATTEMPT = "qualification1"
CHECKPOINT = lambda _: None


def binding(unit_id, input_contract, index):
    return {"unit_id": unit_id, "input_contract": input_contract, "resource_profile": ev.runtime.PROFILE,
            "geometry": ev.runtime.FULL, "invented_only": True, "attempt": ATTEMPT, "merge_commit": f"{index:040x}",
            **{key: "1" * 64 for key in ("source_sha256", "runtime_sha256", "registration_sha256")}}


def audit(directory, bound, summary, artifacts, independent):
    lifecycle = {name: wire.encode({"fixture": name}) for name in
                 ("service-final.json", "service-exit.json", "discovery.jsonl")}
    for name, raw in lifecycle.items():
        (directory / name).write_bytes(raw)
    publication = {"schema": "EG-unit-publication-v1", "binding": bound,
                   "journal_sha256": wire.digest(lifecycle["discovery.jsonl"]),
                   "result": {"geometry": ev.runtime.FULL, "summary": summary, "artifacts": artifacts,
                              "independent_verification": independent}}
    raw = wire.encode(publication)
    (directory / "publication.json").write_bytes(raw)
    audited = {"schema": "EG-original-audit-v1", "status": "PASS", "science_reexecuted": False,
               "binding": bound, "full_geometry": ev.runtime.FULL, "publication_sha256": wire.digest(raw),
               "service_final_sha256": wire.digest(lifecycle["service-final.json"]),
               "service_exit_sha256": wire.digest(lifecycle["service-exit.json"])}
    audit_raw = wire.encode(audited)
    (directory / "original-audit.json").write_bytes(audit_raw)
    return ev.PredecessorPin(bound["unit_id"], ATTEMPT, wire.digest(audit_raw), bound["merge_commit"])


def snapshot_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(ev.runtime, "NATIVE_ROOT", tmp_path)
    directory = tmp_path / ev.snapshot.UNIT / ATTEMPT
    directory.mkdir(parents=True)
    bound = binding(ev.snapshot.UNIT, ev.snapshot.INPUT_CONTRACT, 0)
    stream_bound = {**bound, "input_manifest_sha256": "2" * 64, "coverage_seal_sha256": "3" * 64,
                    "coverage_audit_sha256": None}
    rows = (frame(DAYS[0]), frame(DAYS[100]), frame(DAYS[100], 20, branch="B", peer=False),
            frame(DAYS[100], 25, branch="B"))
    universe = {"sessions": list(DAYS), "raw_sha256": "4" * 64, "target_count": 257}
    seals, report = write_snapshot(directory, ((row, label(row)) for row in rows), stream_bound, universe, CHECKPOINT)
    reconstruction = {"complete": True, "events": len(rows), "records": ev.runtime.FULL["union_coordinates"],
                      "report_sha256": wire.digest(wire.encode(report))}
    receipt = {"schema": "EG-scenario-snapshot-receipt-v1", "binding": stream_bound, "geometry": ev.runtime.FULL,
               "seals": {name: asdict(seal) for name, seal in seals.items()}, "report": report,
               "independent_verification": reconstruction}
    (directory / ev.snapshot.OUTPUT).write_bytes(wire.encode(receipt))
    persisted = ev.snapshot.verify_persisted(directory, receipt, CHECKPOINT)
    pins = [{**row, "maximum": maximum} for row, maximum in
            zip(persisted["artifacts"], (ev.MAX_COMPRESSED, ev.MAX_COMPRESSED, wire.MIB), strict=True)]
    independent = {"complete": True, "persisted": persisted, "raw_reconstruction": reconstruction}
    return directory, receipt, audit(directory, bound, report, pins, independent)


def fit_fixture(tmp_path, name, index, snapshot_receipt, calendar, cells):
    directory = tmp_path / name / ATTEMPT
    directory.mkdir(parents=True)
    bound = binding(name, ev.fit_unit.INPUT_CONTRACT, index)
    _, snapshot_pins = ev.fit._snapshot_contract(snapshot_receipt, bound, name)
    cells_saved, cells_proved, artifacts = [], [], []
    for j, coordinate in enumerate(ev.fit.fit_roster(name)):
        folded = calendar.folds[coordinate.fold_index - 1]
        candidates = cells[coordinate.branch, coordinate.timing, coordinate.fold_index]
        fitted = j == 0
        status = "fit" if fitted else "fold_unsupported"
        metadata = {"kind": "fit", "binding": {**bound, "snapshot": snapshot_pins, "coordinate": asdict(coordinate)},
                    "fit_status": status, "training_sessions": list(folded.training_sessions),
                    "test_sessions": list(folded.test_sessions), "first_test_opening": folded.first_test_opening.isoformat(),
                    "target_first_false_true_rows": [8, 2], "target_first_train_rate": [1, 5]}
        eligible = {row.key for row in candidates if row.eligible(coordinate.arm)}
        forecasts = {key: ModelPrediction(None, None, 0.0, None, False) if key[3] else
                     ModelPrediction(0.33, 0.5, None, 0.0, False) for key in eligible} if fitted else {}
        model = {"artifact_sha256": "8" * 64, "training": {"target_first_false_true_rows": [8, 2]}} if fitted else None
        seal, counts = ev.fit._write_coordinate(directory, coordinate, metadata, model,
                                                 SimpleNamespace(inference=candidates), forecasts, eligible, CHECKPOINT)
        cells_saved.append({"name": coordinate.name, "coordinate": asdict(coordinate), "fit_status": status,
                            "seal": seal, "candidate_counts": counts})
        proof = {"coordinate": asdict(coordinate), "status": "verified" if fitted else status,
                 "replayed_rows": len(forecasts)}
        if fitted:
            proof.update(artifact_sha256="8" * 64, inputs_sha256="9" * 64, target_first_clipped_rows=0,
                         threshold_ambiguous_keys={"gross33": [list(key) for key in sorted(forecasts, key=ev._order) if not key[3]],
                             "gross43": [], "hold_zero": [list(key) for key in sorted(forecasts, key=ev._order) if key[3]]})
        cells_proved.append(proof)
        artifacts.append(ev.runtime.Artifact(coordinate.name, seal["sha256"], seal["compressed_bytes"],
                                             ev.MAX_COMPRESSED).payload())
    receipt = {"schema": ev.fit.RECEIPT_SCHEMA, "binding": bound, "fit_count": 1, "maximum_fits": 10,
               "snapshot": snapshot_pins, "coordinates": cells_saved}
    raw = wire.encode(receipt)
    (directory / ev.fit.OUTPUT).write_bytes(raw)
    artifacts.append(ev.runtime.Artifact(ev.fit.OUTPUT, wire.digest(raw), len(raw), wire.MIB).payload())
    summary = {"fit_count": 1, "maximum_fits": 10, "snapshot": snapshot_pins, "coordinates": [
        {key: row[key] for key in ("coordinate", "fit_status", "candidate_counts")} for row in cells_saved]}
    proof = {"schema": "EG-model-fit-verification-v1", "complete": True, "refit_count": 0,
             "fit_count": 1, "receipt_sha256": wire.digest(raw), "coordinates": cells_proved}
    return audit(directory, bound, summary, artifacts, proof)


def test_six_audits_forecast_keys_and_strict_label_gate(tmp_path, monkeypatch):
    directory, receipt, snapshot_pin = snapshot_fixture(tmp_path, monkeypatch)
    calendar, _, _, cells = ev._feature_roster(directory, receipt, CHECKPOINT)
    pins = tuple(fit_fixture(tmp_path, name, i, receipt, calendar, cells)
                 for i, name in enumerate(ev.fit_unit.FIT_IDS, 1))
    labels_allowed = False
    label_records = []
    original = CoverageReader._line

    def observe(reader):
        value = original(reader)
        if value.get("schema") == SCHEMAS["labels"] or getattr(reader, "header", {}).get("schema") == SCHEMAS["labels"]:
            assert labels_allowed
            label_records.append(value["kind"])
        return value

    monkeypatch.setattr(CoverageReader, "_line", observe)
    with pytest.raises(ValueError, match="roster differs"):
        ev.open_evaluation_inputs(snapshot_pin, pins[:-1], invented_only=True, checkpoint=CHECKPOINT)
    with pytest.raises(ValueError, match="scope differs"):
        ev.open_evaluation_inputs(snapshot_pin, pins, invented_only=False, checkpoint=CHECKPOINT)
    inputs = ev.open_evaluation_inputs(snapshot_pin, pins, invented_only=True, checkpoint=CHECKPOINT)
    assert inputs.source_dates == DAYS and len(inputs.evaluation_keys) == 3 and label_records == []
    common = ("A", "S1", "TCN_CHART", (*inputs.evaluation_keys[0], 0))
    entry = inputs.entry_forecasts()[common]
    assert entry.gross30_pct == 0.33 and entry.training_target_rate == Fraction(1, 5)
    assert entry.replay_ambiguous_costs == frozenset({33})
    hold = (*common[:3], (*common[3][:3], 5))
    assert inputs.holding_forecasts()[hold].replay_ambiguous
    wider = ("B", "S1", "TCN_CHART", (*inputs.evaluation_keys[1], 0))
    peer = ("B", "S1", "TCN_CHART_PEER", wider[-1])
    assert inputs.forecasts[wider].comparison_scope == "chart_only"
    assert inputs.forecasts[peer].status == "peer_unavailable" and peer not in inputs.entry_forecasts()
    stream = inputs.features()
    assert tuple(next(stream)["key"])[0] == DAYS[0]
    with pytest.raises(ValueError, match="complete prospective features"):
        with inputs.open_labels(inputs.evaluation_keys):
            pytest.fail("outcome opened with incomplete features")
    assert len(tuple(stream)) == 3 and label_records == []
    with pytest.raises(ValueError, match="prospective label keys differ"):
        with inputs.open_labels(tuple(reversed(inputs.evaluation_keys))):
            pytest.fail("unordered roster admitted")
    labels_allowed = True
    with inputs.open_labels(inputs.evaluation_keys[1:]) as labels_at:
        with pytest.raises(ValueError, match="not registered prospectively"):
            labels_at(inputs.evaluation_keys[0])
        assert tuple(labels_at(inputs.evaluation_keys[1])["key"]) == inputs.evaluation_keys[1]
        with pytest.raises(ValueError, match="not monotonic"):
            labels_at(inputs.evaluation_keys[1])
    assert label_records == ["header", "record", "footer"]

    fit_directory = tmp_path / pins[0].unit_id / ATTEMPT
    first = ev.fit.fit_roster(pins[0].unit_id)[0]
    with (fit_directory / first.name).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="compressed artifact seal differs"):
        ev.open_evaluation_inputs(snapshot_pin, pins, invented_only=True, checkpoint=CHECKPOINT)
