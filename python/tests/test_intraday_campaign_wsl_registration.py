"""Invented-free registration checks; no inputs, network, credentials or runs."""

from copy import deepcopy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "configs/research/discovery"


def registration(version):
    return json.loads((DIRECTORY / f"intraday-campaign-account-v{version}.json").read_bytes())


def test_wsl_amendment_preserves_the_complete_v2_scientific_contract():
    old, new = registration(2), registration(3)
    mutable_metadata = {"id", "version", "supersedes_for_execution", "resource_authorization", "execution_contract"}
    assert {key: value for key, value in old.items() if key not in mutable_metadata} == {
        key: value for key, value in new.items() if key not in mutable_metadata
    }
    old_contract, new_contract = deepcopy(old["execution_contract"]), deepcopy(new["execution_contract"])
    additions = {"execution_host", "resource_scope", "role_limits", "bridge_contract"}
    assert set(new_contract) - set(old_contract) == additions
    assert not set(old_contract) - set(new_contract)
    for key in additions | {"before_actual", "actual_runner_ready"}:
        old_contract.pop(key, None)
        new_contract.pop(key, None)
    assert new_contract == old_contract
    assert new["id"] == "intraday-campaign-account-v3"
    assert new["version"] == 3
    assert new["supersedes_for_execution"] == old["id"]
    assert new["scientific_parameters_changed"] is False


def test_role_limits_partition_one_existing_unit_instead_of_duplicating_it():
    contract = registration(3)["execution_contract"]
    limits = contract["role_limits"]
    assert set(limits) == {"worker", "coordinator", "controller", "ssh_client"}
    assert sum(role["cpu_seconds"] for role in limits.values()) == contract["cpu_seconds_including_independent_verification"]
    assert sum(role["address_space_bytes"] for role in limits.values()) == contract["address_space_bytes"]
    for role in limits.values():
        assert set(role) == {"cpu_seconds", "address_space_bytes"}
        assert all(type(value) is int and value > 0 for value in role.values())
    assert contract["operator_cpu_scope"] == registration(2)["execution_contract"]["operator_cpu_scope"]


def test_only_the_qualification_host_clause_changes_in_the_final_gates():
    old, new = registration(2)["execution_contract"], registration(3)["execution_contract"]
    before = old["before_actual"]
    after = new["before_actual"]
    assert len(before) == len(after)
    differences = [(left, right) for left, right in zip(before, after, strict=True) if left != right]
    assert differences == [(
        "whole-scale invented GCP qualification",
        "whole-scale invented WSL compute and GCP journal/transport qualification on the identical full bridge",
    )]
    assert new["execution_host"] == "local_wsl_with_gcp_canonical_journal"
    bridge = new["bridge_contract"]
    assert bridge["input_files"] == 3
    assert bridge["started_before_input_access"] is True
    assert bridge["local_existing_journal_copy"] is False
    assert bridge["order_capability"] is bridge["collector_changes"] is bridge["new_source_acquisition"] is False
    assert new["concurrent_actual_units"] == 1
    assert new["automatic_actual_retries"] == 0
