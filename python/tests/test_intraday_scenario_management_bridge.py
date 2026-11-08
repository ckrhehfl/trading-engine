"""The EH management bridge keeps a closed snapshot and original price scope."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from test_intraday_scenario_forecast_bridge import model_plan


def management_plan(tmp_path, scenario, cost, *, recovery=False, actual_retry=False):
    model, _ = model_plan(tmp_path, scenario)
    unit = bridge.EH_MANAGEMENT_C33_RECOVERY if recovery else f"EH-MANAGEMENT-{scenario}-C{cost}"
    if actual_retry:
        unit = bridge.EH_MANAGEMENT_ACTUAL_RETRY
    binding = bridge._EH_BINDINGS[unit]
    root = model.local_execution_root
    spec = model.specification_path
    names = (set(model.source_names) - {spec, bridge.EH_FORECAST_RUNTIME, bridge.EH_FORECAST_RECEIPT_AUDITOR,
                                        bridge.EH_FORECAST_PREDECESSORS})
    marker = bridge.EH_MANAGEMENT_MARKER
    if binding.runtime == bridge.EH_MANAGEMENT_RECOVERY_RUNTIME:
        names.add(bridge.EH_MANAGEMENT_RECOVERY_GUARD)
        marker = bridge.EH_MANAGEMENT_RECOVERY_MARKER
    names = tuple(sorted(names | {binding.specification, binding.runtime, binding.auditor,
                                 *binding.private_dependencies}))
    leaf = unit + "-actual1"
    actual = {"manifest": [bridge.InputFile(name, str(root / "original" / name), "a" * 64, maximum, 10).metadata()
                            for name, maximum in wire.EH_SUPPORT_INPUT_BOUNDS], "decoded_sha256": "c" * 64}
    generated = root / "output" / "EH-SNAPSHOT-RETRY-1-invented-qualification-v1" / "invented-inputs"
    invented = {"manifest": [{**row, "original_path": str(generated / row["name"]), "sha256": "d" * 64}
                             for row in actual["manifest"]], "decoded_sha256": "e" * 64}
    prices = {"actual": actual, "invented": invented}
    price_pins = tuple(bridge.InputPin(row["name"], row["original_path"], row["sha256"], row["maximum"])
                       for row in actual["manifest"])
    return replace(model, unit_id=unit, specification_path=binding.specification,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))), private_entrypoint=binding.runtime,
        input_pins=(*price_pins, *model.input_pins), decoded_stream_sha256=actual["decoded_sha256"],
        input_contract=binding.input_contract, output_filename=binding.output, output_max_bytes=binding.output_max,
        actual_output=model.actual_output.with_name(leaf), actual_cache=model.actual_cache.with_name(leaf),
        actual_diagnostics=model.actual_diagnostics.with_name(leaf),
        global_marker=model.global_marker.with_name(marker),
        management_prices=wire.encode(prices))


@pytest.mark.parametrize(("scenario", "cost"), (("S1", 33), ("S2", 43)))
def test_management_plan_and_both_input_modes_are_closed(tmp_path, scenario, cost):
    plan = management_plan(tmp_path, scenario, cost)
    bridge.validate_plan(plan)
    actual = bridge._eh_management_envelope(plan, False)
    invented = bridge._eh_management_envelope(plan, True)
    bridge._actual_envelope(actual, plan)
    bridge._invented_envelope(invented, plan, 257)
    with pytest.raises(ValueError, match="invented management predecessor differs"):
        bridge._invented_envelope(replace(invented, manifest=(*actual.manifest[:2], invented.manifest[-1])), plan, 257)
    with pytest.raises(ValueError, match="actual receipt sizes differ"):
        bridge._actual_envelope(replace(actual, manifest=(replace(actual.manifest[0], size=11), *actual.manifest[1:])), plan)
    with pytest.raises(ValueError, match="exclusive paths"):
        bridge.validate_plan(replace(plan, global_marker=plan.global_marker.with_name(bridge.EH_FORECAST_MARKER)))
    with pytest.raises(ValueError, match="ordinary300 pairing"):
        bridge.profile_for_plan(replace(plan, input_contract=wire.EH_FORECAST_INPUT_CONTRACT))


def test_management_prices_must_be_from_the_same_audited_snapshot(tmp_path, monkeypatch):
    plan = management_plan(tmp_path, "S1", 33)
    member = bridge._eh_snapshot_member(plan, True)
    path = Path(member.original_path).with_name("publication.json")
    path.parent.mkdir(parents=True)
    pinned = bridge._eh_management_prices(plan, True)
    proof = {"input_manifest": [{**row, "original_path": "/invented/producer/" + row["name"]}
                               for row in pinned["manifest"]],
             "raw_byte_audit": {"archives": {"CC": {"decoded_sha256": pinned["decoded_sha256"]}}}}
    raw = wire.encode({"independent_verification": proof})
    path.write_bytes(raw)
    monkeypatch.setattr(bridge, "_eh_snapshot_prior", lambda *_: {"publication_sha256": wire.digest(raw)})
    assert bridge._eh_management_prior(plan, True)["prices"] == pinned
    prices = bridge.canonical(plan.management_prices)
    prices["invented"]["decoded_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="differs from the same audited snapshot"):
        bridge._eh_management_prior(replace(plan, management_prices=wire.encode(prices)), True)


def test_management_replacement_requires_its_own_qualification_and_keeps_science(tmp_path):
    recovery = management_plan(tmp_path, "S1", 33, recovery=True)
    bridge.validate_plan(recovery)
    original_leaf = "EH-MANAGEMENT-S1-C33"
    with pytest.raises(ValueError, match="exclusive paths"):
        bridge.validate_plan(replace(recovery, actual_output=recovery.actual_output.with_name(original_leaf + "-actual1")))
    with pytest.raises(ValueError, match="independently audited qualification"):
        bridge.validate_eh_support_receipt_audit({"invented_only": False, "attempt": "actual1",
            "qualification": {"output": str(recovery.invented_output_parent / (original_leaf + "-invented-qualification-v1")),
                              "publication_sha256": "1" * 64, "audit_sha256": "2" * 64}}, recovery)

    root = Path(__file__).resolve().parents[2]
    original = json.loads((root / bridge._EH_BINDINGS[original_leaf].specification).read_bytes())
    new = json.loads((root / recovery.specification_path).read_bytes())
    assert original["actual_runner_ready"] is False
    assert new["actual_runner_ready"] is False
    assert new["automatic_actual_retries"] == 0
    for key in ("scenario", "cost_bps", "management_policy", "evaluation", "geometry", "output", "predecessor",
                "resource_profile", "input_roster"):
        assert new[key] == original[key]


def test_management_actual_retry_keeps_original_science_and_uses_its_own_paths(tmp_path):
    retry = management_plan(tmp_path, "S1", 33, actual_retry=True)
    original = bridge._EH_BINDINGS[bridge.EH_MANAGEMENT_C33_RECOVERY]
    bridge.validate_plan(retry)
    with pytest.raises(ValueError, match="exclusive paths"):
        bridge.validate_plan(replace(retry, actual_cache=retry.actual_cache.with_name(bridge.EH_MANAGEMENT_C33_RECOVERY + "-actual1")))
    with pytest.raises(ValueError, match="exclusive paths"):
        bridge.validate_plan(replace(retry, global_marker=retry.global_marker.with_name(bridge.EH_MANAGEMENT_MARKER)))
    root = Path(__file__).resolve().parents[2]
    old = json.loads((root / original.specification).read_bytes())
    new = json.loads((root / retry.specification_path).read_bytes())
    previous = json.loads((root / bridge.EH_MANAGEMENT_SPENT_QUALIFICATION_SPEC).read_bytes())
    assert old["actual_runner_ready"] is False and new["actual_runner_ready"] is True
    assert previous["actual_runner_ready"] is False and new["qualification"]["replaces"]["unit_id"] == previous["unit_id"]
    assert new["automatic_actual_retries"] == 0
    for key in ("scenario", "cost_bps", "management_policy", "evaluation", "geometry", "output", "predecessor",
                "resource_profile", "input_roster"):
        assert new[key] == old[key]
