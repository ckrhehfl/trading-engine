"""Registration and sealed fit-unit composition with invented data only."""

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_fit_unit as unit
from research.intraday_highvol_snapshot_io import write_snapshot
from test_intraday_highvol_model_dataset import DAYS, frame, label


REPO = Path(__file__).resolve().parents[2]
UNIT = "EG-TCN-A-S1-1"
ATTEMPT = "qualification1"
MERGE = "a" * 40
CHECKPOINT = lambda _: None


def registrations():
    return (REPO / unit.SPEC).read_bytes(), (REPO / unit.SCIENCE).read_bytes()


def test_fit_contract_refuses_changed_scientific_scope():
    specification, science = registrations()
    unit.fit_contract(specification, science, UNIT, actual=False)
    registered = json.loads(specification)
    for actual in (False, True):
        registered.update(actual_runner_ready=actual, actual_execution_authorized_by_this_file=actual)
        raw = wire.encode(registered)
        if actual:
            unit.fit_contract(raw, science, UNIT, actual=True)
        else:
            with pytest.raises(ValueError, match="not registered ready"):
                unit.fit_contract(raw, science, UNIT, actual=True)
    registered["training_support"]["minimum_original_days"] = 9
    with pytest.raises(ValueError, match="scientific limits"):
        unit.fit_contract(wire.encode(registered), science, UNIT, actual=False)
    changed_design = json.loads(science)
    changed_design["resource_approval"]["shared_cpu_seconds"] = 7201
    with pytest.raises(ValueError, match="resource limits"):
        unit.fit_contract(specification, wire.encode(changed_design), UNIT, actual=False)


def audited_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(unit.runtime, "NATIVE_ROOT", tmp_path)
    root = tmp_path / unit.snapshot.UNIT / ATTEMPT
    root.mkdir(parents=True)
    binding = {"unit_id": unit.snapshot.UNIT, "input_contract": unit.snapshot.INPUT_CONTRACT,
               "resource_profile": unit.runtime.PROFILE, "attempt": ATTEMPT, "invented_only": True,
               "merge_commit": MERGE, "geometry": unit.runtime.FULL,
               **{name: "a" * 64 for name in ("source_sha256", "runtime_sha256", "registration_sha256")}}
    bound = {**binding, "input_manifest_sha256": "b" * 64, "coverage_seal_sha256": "c" * 64,
             "coverage_audit_sha256": None}
    event = frame(DAYS[0])
    universe = {"sessions": list(DAYS), "raw_sha256": "d" * 64, "target_count": 257}
    seals, report = write_snapshot(root, ((event, label(event)),), bound, universe, CHECKPOINT)
    reconstructed = {"complete": True, "records": unit.runtime.FULL["union_coordinates"], "events": 1,
                     "report_sha256": wire.digest(wire.encode(report)), "raw_byte_audit": {"invented_only": True}}
    receipt = {"schema": "EG-scenario-snapshot-receipt-v1", "binding": bound, "geometry": unit.runtime.FULL,
               "seals": {role: asdict(seal) for role, seal in seals.items()}, "report": report,
               "independent_verification": reconstructed}
    (root / unit.snapshot.OUTPUT).write_bytes(wire.encode(receipt))
    persisted = unit.snapshot.verify_persisted(root, receipt, CHECKPOINT)
    pins = [{**pin, "maximum": maximum} for pin, maximum in
            zip(persisted["artifacts"], (unit.MAX_COMPRESSED, unit.MAX_COMPRESSED, wire.MIB), strict=True)]
    lifecycle = {name: wire.encode({"invented_fixture": name}) for name in
                 ("service-final.json", "service-exit.json", "discovery.jsonl")}
    for name, raw in lifecycle.items():
        (root / name).write_bytes(raw)
    publication = {"schema": "EG-unit-publication-v1", "binding": binding,
                   "journal_sha256": wire.digest(lifecycle["discovery.jsonl"]),
                   "result": {"geometry": unit.runtime.FULL, "summary": report, "artifacts": pins,
                              "independent_verification": {"complete": True, "raw_reconstruction": reconstructed,
                                                           "persisted": persisted}}}
    (root / "publication.json").write_bytes(wire.encode(publication))
    audit = {"schema": "EG-original-audit-v1", "status": "PASS", "science_reexecuted": False,
             "binding": binding, "full_geometry": unit.runtime.FULL,
             "publication_sha256": wire.digest(wire.encode(publication)),
             "service_final_sha256": wire.digest(lifecycle["service-final.json"]),
             "service_exit_sha256": wire.digest(lifecycle["service-exit.json"])}
    (root / "original-audit.json").write_bytes(wire.encode(audit))
    return root, wire.digest(wire.encode(audit)), receipt


def test_predecessor_refuses_cross_scope_unsealed_or_changed_files(tmp_path, monkeypatch):
    root, pin, receipt = audited_snapshot(tmp_path, monkeypatch)
    assert unit._snapshot_scope(True, ATTEMPT, pin, MERGE) == root
    for invented, attempt in ((True, "actual1"), (False, ATTEMPT), (True, "../qualification1")):
        with pytest.raises(ValueError, match="pin or attempt differs"):
            unit._snapshot_scope(invented, attempt, pin, MERGE)
    assert unit._predecessor(root, True, ATTEMPT, pin, MERGE, CHECKPOINT) == receipt
    for invented, attempt, expected_pin, merge, error in (
        (True, ATTEMPT, "0" * 64, MERGE, "snapshot audit differs"),
        (True, ATTEMPT, pin, "b" * 40, "snapshot publication differs"),
        (False, ATTEMPT, pin, MERGE, "snapshot publication differs"),
    ):
        with pytest.raises(ValueError, match=error):
            unit._predecessor(root, invented, attempt, expected_pin, merge, CHECKPOINT)
    with (root / unit.FILENAMES["labels"]).open("ab") as stream:
        stream.write(b"changed compressed file")
    with pytest.raises(ValueError, match="persisted artifact differs"):
        unit._predecessor(root, True, ATTEMPT, pin, MERGE, CHECKPOINT)


def context(tmp_path):
    specification, science = registrations()
    pins = {unit.SPEC: wire.digest(specification), unit.SCIENCE: wire.digest(science)}
    binding = {"unit_id": UNIT, "input_contract": unit.INPUT_CONTRACT, "invented_only": True,
               "merge_commit": MERGE, "registration_sha256": pins[unit.SPEC]}
    root = tmp_path / UNIT / ATTEMPT
    root.mkdir(parents=True)
    run = SimpleNamespace(source_hashes=tuple(pins.items()), unit_id=UNIT, invented_only=True,
                          binding=binding, directory=root)
    return run, specification, science


def write_started(run):
    record = {"record_type": "discovery_trial", "status": "started", "study_id": run.unit_id,
              "parameters": run.binding, "specification_sha256": run.binding["registration_sha256"],
              "code_version": run.binding["merge_commit"], "window": unit.runtime.WINDOW,
              "promotion_allowed": False, "promotion_trial_increment": 0}
    (run.directory / "discovery.jsonl").write_bytes(wire.encode(record))


@pytest.mark.parametrize("fit_count,rows", [(1, 12), (1, 0), (0, 0)])
def test_closed_unit_started_gate_and_invented_replay_support(tmp_path, monkeypatch, fit_count, rows):
    root, pin, prior = audited_snapshot(tmp_path, monkeypatch)
    run, specification, science = context(tmp_path)
    callbacks = unit.closed_fit_unit(run, specification, science, snapshot_attempt=ATTEMPT,
                                     snapshot_audit_sha256=pin, snapshot_merge_commit=MERGE)
    calls = []
    original = unit._predecessor

    def observe(*args):
        calls.append("predecessor")
        return original(*args)

    monkeypatch.setattr(unit, "_predecessor", observe)
    callbacks.preflight()
    assert calls == []
    with pytest.raises(FileNotFoundError):
        callbacks.compute(run.directory, run.binding, CHECKPOINT)
    assert calls == []
    write_started(run)

    def produce(directory, snapshot_directory, receipt, binding, unit_id, checkpoint):
        assert snapshot_directory == root and receipt == prior and binding == run.binding and unit_id == UNIT
        calls.append("produce")
        fit_receipt = {"schema": unit.fit_run.RECEIPT_SCHEMA, "fit_count": fit_count, "maximum_fits": 10,
                       "snapshot": {"receipt_sha256": wire.digest(wire.encode(prior))}, "coordinates": []}
        for index, coordinate in enumerate(unit.fit_run.fit_roster(UNIT)):
            payload = wire.encode({"coordinate": asdict(coordinate), "invented": True})
            (directory / coordinate.name).write_bytes(payload)
            fit_receipt["coordinates"].append({"name": coordinate.name, "coordinate": asdict(coordinate),
                "fit_status": "fit" if index < fit_count else "fold_unsupported", "candidate_counts": {},
                "seal": {"sha256": wire.digest(payload), "compressed_bytes": len(payload)}})
        (directory / unit.fit_run.OUTPUT).write_bytes(wire.encode(fit_receipt))
        return fit_receipt

    def verify(directory, snapshot_directory, receipt, binding, unit_id, checkpoint):
        calls.append("verify")
        return {"complete": True, "refit_count": 0, "fit_count": fit_count,
                "receipt_sha256": wire.digest((directory / unit.fit_run.OUTPUT).read_bytes()),
                "coordinates": [{"coordinate": asdict(coordinate), "replayed_rows": rows if i < fit_count else 0,
                    "status": "verified" if i < fit_count else "fold_unsupported"}
                    for i, coordinate in enumerate(unit.fit_run.fit_roster(UNIT))]}

    monkeypatch.setattr(unit.fit_run, "produce_fit", produce)
    monkeypatch.setattr(unit.fit_run, "verify_fit", verify)
    output = callbacks.compute(run.directory, run.binding, CHECKPOINT)
    assert calls == ["predecessor", "produce"] and len(output.artifacts) == 11
    assert json.loads(output.geometry) == unit.runtime.FULL and json.loads(output.summary)["fit_count"] == fit_count
    if rows:
        assert callbacks.independent_verify(run.directory, output, run.binding, CHECKPOINT)["refit_count"] == 0
    else:
        with pytest.raises(ValueError, match="zero models|replay proof differs"):
            callbacks.independent_verify(run.directory, output, run.binding, CHECKPOINT)
    assert calls == ["predecessor", "produce", "verify"]
