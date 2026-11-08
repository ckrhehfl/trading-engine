"""Invented worker-wiring tests; no native package or market input is opened."""

from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_compute as compute
from research.intraday_scenario_snapshot import SnapshotSeal
from research.intraday_scenario_snapshot_unit import BundleSeal


@pytest.fixture
def rig(tmp_path, monkeypatch):
    directory = tmp_path / "worker"
    directory.mkdir(mode=0o700)
    received = directory / "received"
    received.mkdir(mode=0o700)
    inputs = []
    for name, maximum in compute.INPUT_BOUNDS:
        raw = ("invented-" + name).encode()
        (received / name).write_bytes(raw)
        inputs.append(bridge.InputFile(name, "/invented/original/" + name, wire.digest(raw), maximum, len(raw)))
    inputs = tuple(inputs)
    features = SnapshotSeal("invented-features", "a" * 64, 10, 5158)
    labels = SnapshotSeal("invented-labels", "b" * 64, 11, 5158)
    members = {"features": asdict(features), "labels": asdict(labels)}
    snapshot_summary = {"members": members}
    events = []
    universe = SimpleNamespace(targets=(None,) * 10280)
    monkeypatch.setattr(compute.sources, "load_universe", lambda *args, **kwargs: universe)
    monkeypatch.setattr(compute.forecasts, "_original", lambda *args: (
        SimpleNamespace(evaluation_reference_sessions=(None,) * 157), dict.fromkeys(range(5158))))
    monkeypatch.setattr(compute.snapshots, "verify_bundle", lambda *args: (features, labels))

    def activate(environment, checkpoint):
        events.append("activate")
        return {"invented_activation": True}

    def after(receipt, checkpoint):
        assert receipt == {"invented_activation": True}
        events.append("verify_after")
        return {"invented_after": True}

    def compare(source, output, **kwargs):
        events.append("compare")
        assert source == received / "EI-SNAPSHOT"
        assert kwargs["universe"] is universe
        assert "adapters" not in kwargs  # Qualification must also use actual fixed model implementations.
        raw = b"invented-output"
        output.write_bytes(raw)
        result = {"schema": compute.forecasts.SCHEMA, "scenario": kwargs["scenario"], "adapter_mode": "fixed_native",
            "input_members": members, "folds": 5, "evaluation_sessions": 157,
            "original_evaluation_coordinates": 3140, "fold_prediction_readback_verified": True,
            "report_reference_verified": True, "independent_saved_bundle_audit": False,
            "timing_scenarios_are_independent": False, "contains_economic_returns": False,
            "learned_fit_count": 20, "empirical_fit_count": 5}
        return compute.forecasts.ForecastUnitResult(BundleSeal(wire.digest(raw), len(raw)), result)

    monkeypatch.setattr(compute.numerical, "activate", activate)
    monkeypatch.setattr(compute.numerical, "verify_after", after)
    monkeypatch.setattr(compute.forecasts, "compare_snapshot", compare)
    envelope = bridge.InputEnvelope(inputs, compute.input_digest(inputs), wire.encode(compute.FULL), compute.INPUT_CONTRACT)
    arguments = {"scenario": "S1", "expected_inputs": inputs, "expected_members": members,
        "expected_summary": snapshot_summary, "environment": deepcopy(compute.numerical.ENVIRONMENT),
        "invented_only": True, "checkpoint": events.append}
    return SimpleNamespace(directory=directory, inputs=inputs, envelope=envelope, arguments=arguments, events=events,
                           compare=compare, members=members)


def run(rig, **changes):
    return compute.compute_received(rig.directory, rig.envelope, **(rig.arguments | changes))


def test_full_wiring_authenticates_before_import_and_retains_separate_audit(rig):
    result = run(rig)
    proof = json.loads(result.independent_verification)
    assert rig.events.index("ei_model_inputs_authenticated") < rig.events.index("activate")
    assert rig.events.index("activate") < rig.events.index("compare") < rig.events.index("verify_after")
    assert proof["numerical_environment"] == {"before": {"invented_activation": True}, "after": {"invented_after": True}}
    assert not proof["forecast_summary"]["independent_saved_bundle_audit"]
    assert not proof["contains_economic_returns"]
    assert proof["input_manifest"] == [item.metadata() for item in rig.inputs]
    assert json.loads(result.candidate_counts) == {"S1": {"original": 3140}}


@pytest.mark.parametrize("name", ("CB", "EI-SNAPSHOT"))
def test_changed_received_bytes_rejected_before_numerical_import(rig, name):
    (rig.directory / "received" / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="received bytes"):
        run(rig)
    assert "activate" not in rig.events


@pytest.mark.parametrize("change", (
    {"input_contract": "eh-forecast-sealed-snapshot-v1"}, {"stream_sha256": "0" * 64},
    {"geometry": wire.encode({**compute.FULL, "sessions": 256})},
))
def test_wrong_transport_contract_population_or_digest_fails_before_import(rig, change):
    rig.envelope = replace(rig.envelope, **change)
    with pytest.raises(ValueError, match="received envelope"):
        run(rig)
    assert "activate" not in rig.events


def test_self_consistent_switched_members_disagree_with_received_snapshot(rig):
    members = deepcopy(rig.members)
    members["features"]["sha256"] = "c" * 64
    with pytest.raises(ValueError, match="snapshot seals"):
        run(rig, expected_members=members, expected_summary={"members": members})
    assert "activate" not in rig.events


def test_changed_environment_cannot_fall_back_to_worktree_venv(rig):
    environment = deepcopy(rig.arguments["environment"])
    environment["site_packages"] = "/other/site"
    with pytest.raises(ValueError, match="environment"):
        run(rig, environment=environment)
    assert "activate" not in rig.events


@pytest.mark.parametrize("name,value", (("adapter_mode", "injected_diagnostic"), ("folds", 4),
    ("learned_fit_count", 21), ("empirical_fit_count", True), ("contains_economic_returns", True),
    ("independent_saved_bundle_audit", True), ("report_reference_verified", 1)))
def test_compute_cannot_claim_other_scope_fits_or_a_completed_saved_audit(rig, monkeypatch, name, value):
    def altered(*args, **kwargs):
        original = rig.compare(*args, **kwargs)
        return replace(original, summary=original.summary | {name: value})
    monkeypatch.setattr(compute.forecasts, "compare_snapshot", altered)
    with pytest.raises(ValueError, match="result differs|fit budget"):
        run(rig)


def test_changed_numerical_closure_does_not_produce_receipt(rig, monkeypatch):
    def changed(*args):
        raise ValueError("invented changed binary closure")
    monkeypatch.setattr(compute.numerical, "verify_after", changed)
    with pytest.raises(ValueError, match="changed binary"):
        run(rig)


def test_input_mutation_during_fit_is_rejected(rig, monkeypatch):
    def altered(*args, **kwargs):
        result = rig.compare(*args, **kwargs)
        (rig.directory / "received" / "CB").write_bytes(b"changed-after-input-authentication")
        return result
    monkeypatch.setattr(compute.forecasts, "compare_snapshot", altered)
    with pytest.raises(ValueError, match="bytes changed during"):
        run(rig)


def test_no_admission_exists_for_the_new_contract_yet(rig):
    with pytest.raises(ValueError):
        rig.envelope.payload()
