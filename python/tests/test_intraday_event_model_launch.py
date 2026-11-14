"""Invented launcher boundaries without opening any dataset or native backend."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from research import intraday_event_model_launch as launch


def _args(mode, **fields):
    return SimpleNamespace(**{"mode": mode, "kind": None, "scope": None, "fold": None, "shard": None,
                              "attempt": None, "dataset_binding_sha256": None, **fields})


def _release():
    return {"merge": "1" * 40, "sources": {launch.run.SPEC: launch.contract.DATASET_REGISTRATION_SHA256,
                                                launch.run.ORIGINAL: launch.run.ORIGINAL_SHA256}}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for name in ("canonical", "output"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(launch.run, "NATIVE", tmp_path)
    monkeypatch.setattr(launch.run, "STARTED_CLOCK", 0)
    monkeypatch.setattr(launch.run, "budget", lambda: None)
    monkeypatch.setattr(launch.run, "runtime_pin", lambda: "2" * 64)
    monkeypatch.setattr(launch.run, "_locks", nullcontext)
    monkeypatch.setattr(launch.run, "validate_spec", lambda _: None)
    monkeypatch.setattr(launch.run, "verify_contract", lambda _: None)
    monkeypatch.setattr(launch.contract, "validate_spec", lambda _: None)
    monkeypatch.setattr(launch.bridge, "restrict_local_network", lambda: None)
    monkeypatch.setattr(launch.wire, "prohibit_ef_tasks", lambda: None)
    return tmp_path, {name: None for name in (launch.contract.SPEC, launch.run.SPEC, launch.run.ORIGINAL)}


def test_cases_write_start_before_any_actual_admission(sandbox, monkeypatch):
    directory, registrations = sandbox
    release = _release()
    upstream = {**release, "reviewed": "a" * 40, "tree": "b" * 40, "runtime_sha256": "2" * 64}
    binding = SimpleNamespace(release=upstream, inputs_sha256="c" * 64)
    raw = launch.wire.encode({"invented_binding_document": True})
    (directory / "canonical" / f"{launch.contract.UNIT}-dataset-binding.json").write_bytes(raw)
    monkeypatch.setattr(launch.contract, "validate_dataset_binding", lambda *_: binding)
    slot = directory / "output" / f"{launch.contract.UNIT}-cases"
    arguments = _args("cases", dataset_binding_sha256=launch.wire.digest(raw))
    with pytest.raises(ValueError, match="require all actual dataset shard audits"):
        launch.execute(arguments, registrations, release, 0, lambda *_: None)
    assert not slot.exists()
    for shard in range(len(launch.run.SHARDS)):
        admitted_slot = launch.run._slot(launch.run.attempt_name("actual1", shard))
        admitted_slot.mkdir()
        for name in ("completion.json", "audit.json"):
            (admitted_slot / name).touch()

    def admit(value):
        assert value is binding
        assert launch.run._doc(slot / "start.json")["inputs_sha256"] == binding.inputs_sha256
        raise TimeoutError("invented before content")

    monkeypatch.setattr(launch, "_admit_actual", admit)
    with pytest.raises(TimeoutError, match="before content"):
        launch.execute(arguments, registrations, release, 0, lambda *_: None)
    assert (slot / "start.json").is_file() and not (slot / "report.json").exists()


def test_actual_fold_maps_to_fixed_maximum_invented_coordinate(sandbox, monkeypatch):
    seen = []

    def audited(slot, identity):
        seen.append((slot, identity))
        return {}, "3" * 64

    monkeypatch.setattr(launch.receipts, "audited", audited)
    monkeypatch.setattr(launch.witness, "verify", lambda: {"invented": True})
    monkeypatch.setattr(launch.run, "_doc", lambda _: {"invented": True})
    release = {**_release(), "registration_sha256": "4" * 64, "runtime_sha256": "2" * 64}
    assert launch._qualification_identity(release, ("empirical", "common_chart", 0), "5" * 64) == "3" * 64
    assert launch._qualification_identity(release, ("tcn", "common_chart_peer", 1), "5" * 64) == "3" * 64
    assert [(identity["kind"], identity["scope"], identity["fold"]) for _, identity in seen] == [
        ("empirical", "full_chart", 3), ("tcn", "common_chart_peer", 3)]


def test_summary_readiness_checks_paths_before_opening_a_report(sandbox):
    directory, _ = sandbox
    release = {**_release(), "registration_sha256": "4" * 64, "runtime_sha256": "2" * 64}
    digest, case_pin = "5" * 64, "6" * 64
    for index, coordinate in enumerate(launch.contract.ROSTER):
        identity = launch._identity(release, coordinate, False, digest, case_pin=case_pin)
        if index == 0:
            identity["attempt"] = "actual2"
        slot = launch.receipts.slot_for(identity)
        slot.mkdir()
        (slot / "completion.json").touch()
        if index + 1 < len(launch.contract.ROSTER):
            (slot / "audit.json").touch()

    with pytest.raises(ValueError, match="requires every actual fold audit"):
        launch._require_actual_audit_paths(release, digest, case_pin)
    assert not (directory / "output" / f"{launch.contract.UNIT}-summary").exists()
    (slot / "audit.json").touch()
    launch._require_actual_audit_paths(release, digest, case_pin)
