"""Durable local EF fits and comparison over audited predecessor artifacts."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_chart_handoffs as handoff
from research.intraday_chart_snapshot_unit import FULL, SCIENCE, SCIENTIFIC_WINDOW, _json


SPEC = bridge.EF_COMPARISON_SPEC
PROFILE = wire.EF_LOCAL_SEQUENCE.id
REPORT_FILE = "ef-comparison.json"
EVAL_COORDINATES_PER_SCENARIO = 157 * 20 * 17


def bounds_for(unit: str) -> dict[str, int]:
    if unit == bridge.EF_REPORT_UNIT:
        return {"scenarios": 2}
    family, _ = bridge.EF_FIT_UNITS[unit]
    return {"fits": 15 if family == "TCN" else 10,
            "forecast_rows": EVAL_COORDINATES_PER_SCENARIO * (3 if family == "TCN" else 4)}


def contract(raw: bytes, science: bytes, *, actual: bool) -> dict:
    spec, design = _json(raw), _json(science)
    wire.require(spec["id"] == "intraday-chart-sequence-execution-v1" and spec["campaign"] == "EF"
        and spec["units"] == list(bridge.EF_COMPARISON_UNITS) and spec["input_contract"] == wire.EF_SEALED_CONTRACT
        and spec["execution_host"] == bridge.LOCAL_HOST and spec["resource_profile"] == PROFILE
        and spec["science_registration"] == {"path": SCIENCE, "sha256": wire.digest(science)}
        and design["id"] == "intraday-chart-sequence-comparison-v1" and design["learners"]["maximum_market_fits"] == 50
        and type(spec["actual_runner_ready"]) is bool and spec["live_or_paper_order_capable"] is False,
        "EF registered comparison contract differs")
    if actual:
        wire.require(spec["actual_runner_ready"], "EF comparison source is not registered ready")
    return spec


def comparison_hooks(plan: bridge.UnitPlan, specification: bytes, science: bytes, *, invented_only: bool,
                     source_sha256: str, runtime_sha256: str, native_runtime_sha256: str,
                     prepare_native: Callable[[str], None]) -> bridge.UnitHooks:
    spec = contract(specification, science, actual=not invented_only)
    bridge.profile_for_plan(plan)
    unit, root = plan.unit_id, plan.local_execution_root
    wire.require(unit in bridge.EF_COMPARISON_UNITS and root is not None and plan.local_coordinator is True
        and plan.specification_path == SPEC and plan.input_contract == wire.EF_SEALED_CONTRACT
        and plan.actual_runner_ready is spec["actual_runner_ready"] and plan.full_geometry == wire.encode(FULL)
        and plan.window == wire.encode(SCIENTIFIC_WINDOW) and plan.candidate_bounds == wire.encode(bounds_for(unit))
        and plan.output_filename == handoff.receipt_name(unit) and plan.output_max_bytes == wire.MIB
        and len(plan.input_pins) == 1 and plan.input_pins[0].name == handoff.HANDOFF_NAME
        and plan.input_pins[0].original_path == str(handoff.handoff_path(root, unit, False))
        and plan.input_pins[0].maximum == handoff.HANDOFF_LIMIT and plan.decoded_stream_sha256 == plan.input_pins[0].sha256
        and all(wire.valid_sha(value) for value in (source_sha256, runtime_sha256, native_runtime_sha256))
        and {SPEC, SCIENCE} <= set(plan.source_names) and type(invented_only) is bool,
        "EF fixed comparison hook binding differs")
    pins = {SPEC: wire.digest(specification), SCIENCE: wire.digest(science)}

    def load_plan(config: dict) -> bridge.UnitPlan:
        hashes = config["source_hashes"]
        wire.require(config["invented_only"] is invented_only and config["sessions"] == FULL["sessions"]
            and config["attempt"] == ("qualification-v1" if invented_only else "actual1")
            and all(hashes.get(name) == pin for name, pin in pins.items())
            and wire.digest(wire.encode(hashes)) == source_sha256
            and config["local_identity"]["runtime_sha256"] == runtime_sha256,
            "EF comparison exact runtime, scope or registrations differ")
        return plan

    def make_invented(directory, sessions, checkpoint):
        wire.require(invented_only and sessions == FULL["sessions"], "EF comparison needs full invented input")
        raw = bridge.bounded_read(handoff.handoff_path(root, unit, True), handoff.HANDOFF_LIMIT)
        handoff.resolve_handoff(raw, root, unit, True)
        file = directory / handoff.HANDOFF_NAME
        wire.exclusive_bytes(file, raw)
        entry = bridge.InputFile(file.name, str(file), wire.digest(raw), handoff.HANDOFF_LIMIT, len(raw))
        return bridge.InputEnvelope((entry,), entry.sha256, plan.full_geometry, wire.EF_SEALED_CONTRACT)

    def compute(directory, envelope, checkpoint):
        from research.intraday_chart_snapshot import SnapshotSeal
        from research.intraday_chart_snapshot_run import FEATURE_FILE, LABEL_FILE

        wire.require(envelope.input_contract == wire.EF_SEALED_CONTRACT and envelope.geometry == plan.full_geometry
            and len(envelope.manifest) == 1 and envelope.manifest[0].name == handoff.HANDOFF_NAME,
            "EF comparison input envelope differs")
        if not invented_only:
            bridge._actual_envelope(envelope, plan)
        raw = bridge.bounded_read(directory / "received" / handoff.HANDOFF_NAME, handoff.HANDOFF_LIMIT)
        raw_pin = wire.digest(raw)
        wire.require(raw_pin == envelope.stream_sha256 == envelope.manifest[0].sha256
                     and len(raw) == envelope.manifest[0].size, "EF received handoff pin differs")
        snapshot, *fit_artifacts = handoff.resolve_handoff(raw, root, unit, invented_only)
        seal = SnapshotSeal(**snapshot.receipt["seal"])
        feature_file, label_file = snapshot.cache / FEATURE_FILE, snapshot.cache / LABEL_FILE
        binding = {"caller": unit, "invented_only": invented_only, "source_sha256": source_sha256,
            "runtime_sha256": runtime_sha256, "native_runtime_sha256": native_runtime_sha256, "registrations": pins,
            "snapshot_receipt_sha256": snapshot.receipt_sha256, "snapshot_seal": snapshot.receipt["seal"]}
        if unit == bridge.EF_REPORT_UNIT:
            from research.intraday_chart_report_run import run_report

            verification = run_report(directory, feature_file, label_file, seal, tuple(fit_artifacts), checkpoint)
            artifact_name = REPORT_FILE
            counts = {"scenarios": verification["scenarios"]}
        else:
            from research.intraday_chart_fit_run import FIT_FILE, run_fit

            family, scenario = bridge.EF_FIT_UNITS[unit]
            prepare_native(family)
            verification = run_fit(directory, feature_file, label_file, seal, source_sha256=source_sha256,
                runtime_sha256=native_runtime_sha256, registration_sha256=pins[SCIENCE], scenario=scenario,
                family=family, checkpoint=checkpoint)
            artifact_name = FIT_FILE
            counts = {"fits": verification["fits"], "forecast_rows": verification["forecast_rows"]}
        size = wire.regular(directory / artifact_name).st_size
        wire.require(verification["verified"] is True and wire.valid_sha(verification["artifact_sha256"])
                     and type(verification["artifact_size"]) is int and verification["artifact_size"] == size > 0,
                     "EF independent artifact verification required")
        if invented_only:
            wire.require(counts == bounds_for(unit), "EF whole invented run did not exercise every registered fit or report")
        verification = {"verified": True, "schema": "EF-comparison-artifact-verification-v1", **counts,
                        "artifact_sha256": verification["artifact_sha256"]}
        receipt = {"schema": "EF-comparison-unit-receipt-v1", "binding": binding,
            "seal": {"artifact_sha256": verification["artifact_sha256"], "input_sha256": raw_pin},
            "artifact": {"name": artifact_name, "size": size},
            "independent_verification": verification}
        wire.exclusive_bytes(directory / plan.output_filename, wire.encode(receipt))
        digest, size = wire.file_digest(directory / plan.output_filename, plan.output_max_bytes, checkpoint)
        return bridge.OutputReceipt(digest, size, envelope.geometry, wire.encode(verification), wire.encode(counts))

    return bridge.UnitHooks(load_plan, make_invented, compute)
