"""Invented predecessor identities and fixed EL model registration."""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as dataset
from research import intraday_event_model_contract as contract
from research import intraday_event_model_inputs as inputs


ROOT = Path(__file__).resolve().parents[2]


def _spec():
    return json.loads((ROOT / contract.SPEC).read_bytes())


def _binding():
    return {
        "dataset_release": {
            "merge": "1" * 40, "reviewed": "2" * 40, "tree": "3" * 40,
            "registration_sha256": contract.DATASET_REGISTRATION_SHA256,
            "parents_sha256": dataset.ORIGINAL_SHA256,
            "sources": {dataset.OWN: "4" * 64, "python/research/intraday_event_dataset.py": "5" * 64},
            "runtime_sha256": "6" * 64,
            "support_audit_sha256": "7" * 64,
            "original_failed_attempt": {
                "marker_sha256": "8" * 64, "start": 14, "end": 20, "run_id": "invented",
                "terminal_sha256": "9" * 64,
            },
        },
        "qualification_audit_sha256": "f" * 64,
        "shard_audit_sha256": [f"{i:064x}" for i in range(9)],
    }


def _pin(value):
    return sha256(wire.encode(value)).hexdigest()


def test_registered_contract_matches_dataset_bytes_and_complete_roster():
    spec = _spec()
    contract.validate_spec(spec)
    assert sha256((ROOT / dataset.SPEC).read_bytes()).hexdigest() == contract.DATASET_REGISTRATION_SHA256
    assert len({contract.unit_id(*item) for item in contract.ROSTER}) == spec["maximum_fitted_artifacts"] == 36
    assert {tuple(unit) for unit in spec["qualification"]["roster"]} == {
        (kind, scope, 3) for kind in ("empirical", "lightgbm", "tcn") for scope in ("full_chart", "common_chart_peer")}
    assert [day for _, start, stop in spec["folds"] for day in range(start, stop)] == list(range(97, 257))
    with pytest.raises(ValueError, match="Unregistered"):
        contract.unit_id("empirical", "full_chart", True)


@pytest.mark.parametrize("drift", ["omit_fold", "change_common_support", "add_optimizer_update", "omit_audit", "enable_orders"])
def test_registration_rejects_scientific_or_boundary_drift(drift):
    spec = _spec()
    if drift == "omit_fold":
        spec["folds"].pop()
    elif drift == "change_common_support":
        spec["feature_scopes"]["common_chart"]["cohort"] = "own_chart_supported"
    elif drift == "add_optimizer_update":
        spec["learners"]["tcn"]["diagnostics"]["extra_optimizer_updates"] = 1
    elif drift == "omit_audit":
        spec["qualification"]["independent_shard_audits"] = 8
    else:
        spec["orders_enabled"] = True
    with pytest.raises(ValueError, match="registration differs"):
        contract.validate_spec(spec)


def test_private_binding_matches_existing_admission_hash_and_preserves_order(monkeypatch):
    value = _binding()
    admitted = contract.validate_dataset_binding(value, _pin(value))
    seen = []

    def audited_shard(identity, shard):
        assert identity["merge"] == value["dataset_release"]["merge"]
        seen.append(shard)
        return {"identity": {"attempt": f"actual1-s{shard:02}"}}, value["shard_audit_sha256"][shard]

    monkeypatch.setattr(dataset, "audited", lambda identity: ({}, value["qualification_audit_sha256"]))
    monkeypatch.setattr(dataset, "audited_shard", audited_shard)
    _, bound = inputs.admit_dataset(admitted.release, admitted.qualification_audit_sha256, admitted.shard_audit_sha256)
    assert seen == list(range(9))
    assert bound == admitted.inputs_sha256
    copy = admitted.release
    copy["sources"].clear()
    assert dataset.OWN in admitted.release["sources"]
    pin = _pin(value)
    value["shard_audit_sha256"].reverse()
    with pytest.raises(ValueError, match="binding pin"):
        contract.validate_dataset_binding(value, pin)


@pytest.mark.parametrize("drift", ["bad_source", "bad_parent", "bad_failure_span", "missing_shard", "duplicate_shard"])
def test_private_binding_rejects_incompatible_or_incomplete_predecessors_even_if_repinned(drift):
    value = _binding()
    release = value["dataset_release"]
    if drift == "bad_source":
        release["sources"]["python/research/../unexpected.py"] = "0" * 64
    elif drift == "bad_parent":
        release["parents_sha256"] = "0" * 64
    elif drift == "bad_failure_span":
        release["original_failed_attempt"]["end"] = True
    elif drift == "missing_shard":
        value["shard_audit_sha256"].pop()
    else:
        value["shard_audit_sha256"][-1] = value["shard_audit_sha256"][0]
    with pytest.raises(ValueError, match="EL dataset"):
        contract.validate_dataset_binding(value, _pin(value))
