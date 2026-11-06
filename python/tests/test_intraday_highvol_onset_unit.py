"""Invented registration and durable-start checks for the no-fit EG follow-up."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_onset_unit as unit
from research import intraday_highvol_runtime as runtime


ROOT = Path(__file__).resolve().parents[2]


def registration():
    return (ROOT / unit.SPEC).read_bytes(), (ROOT / unit.SCIENCE).read_bytes()


def test_followup_is_separate_and_has_no_native_fit_or_new_input_permission():
    spec, science = registration()
    unit.onset_contract(spec, science, actual=True)
    descriptor = runtime.registered_unit(unit.UNIT)
    assert descriptor.specification == unit.SPEC and descriptor.input_contract == unit.INPUT_CONTRACT
    assert descriptor.service_prefix == descriptor.configuration_prefix == descriptor.metadata_prefix == "eg-highvol-onset-"
    for change in ({"new_fits": 1}, {"actual_runner_ready": False, "actual_execution_authorized_by_this_file": False},
                   {"selection": {**json.loads(spec)["selection"], "use_later_scenario_or_outcome": True}}):
        altered = {**json.loads(spec), **change}
        with pytest.raises(ValueError, match="registration scope|not registered ready|clock, comparison"):
            unit.onset_contract(wire.encode(altered), science, actual=True)


def test_preflight_and_failed_start_never_open_predecessor(tmp_path, monkeypatch):
    spec, science = registration()
    pins = ((unit.SPEC, wire.digest(spec)), (unit.SCIENCE, wire.digest(science)))
    binding = {"unit_id": unit.UNIT, "input_contract": unit.INPUT_CONTRACT, "registration_sha256": wire.digest(spec),
               "merge_commit": "b" * 40}
    run = SimpleNamespace(invented_only=True, unit_id=unit.UNIT, source_hashes=pins, binding=binding, directory=tmp_path)
    opened = []

    def should_not_open(*_):
        opened.append(True)
        raise AssertionError("predecessor opened without durable start")

    monkeypatch.setattr(unit.fit_unit, "_predecessor", should_not_open)
    callbacks = unit.closed_onset_unit(run, spec, science, snapshot_attempt="qualification1",
        snapshot_audit_sha256="a" * 64, snapshot_merge_commit="b" * 40)
    callbacks.preflight()
    with pytest.raises(FileNotFoundError):
        callbacks.compute(tmp_path, binding, lambda _: None)
    assert not opened


def test_invented_qualification_requires_a_known_expansion_outcome():
    empty = {"timings": {"S1": {"costs": {"33": {"early_a": {"known": 0}}}}}}
    with pytest.raises(ValueError, match="known expansion outcome"):
        unit._require_invented_outcome(empty)
    empty["timings"]["S1"]["costs"]["33"]["early_a"]["known"] = 1
    unit._require_invented_outcome(empty)
