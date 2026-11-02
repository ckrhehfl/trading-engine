"""Closed EE mechanism-one hooks over the existing original-three lifecycle.

Only source declarations are inspected by the factory. All market/index/report
work stays in the existing post-STARTED worker callback. The spent primary unit
is neither retried nor used as an admission oracle.
"""
from __future__ import annotations

import json
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_trader_unit as primary


UNIT_ID = bridge.EE_FOLLOWUP_UNIT_ID
SPECIFICATION_PATH = bridge.EE_FOLLOWUP_SPECIFICATION_PATH
SPECIFICATION_ID = "intraday-trader-mechanism-followups-v1"
PROFILE = "mechanism1"
POLICIES = ("EE-A-2R-TARGET", "EE-B-POSITIVE-PEERS", "EE-B-COMMON-PEERS")
OUTPUT_FILENAME = primary.OUTPUT_FILENAME
OUTPUT_SCHEMA = "EE-original-population-mechanism1-v1"
FULL_GEOMETRY = primary.FULL_GEOMETRY
CANDIDATE_BOUNDS = {clock: {policy: FULL_GEOMETRY["targets_per_scenario"] for policy in POLICIES}
                    for clock in ("S1", "S2")}
MINIMUM_PUBLIC_SOURCES = tuple(sorted(set(primary.MINIMUM_PUBLIC_SOURCES) | {
    SPECIFICATION_PATH, "python/research/intraday_trader_followup_unit.py",
    "python/tests/test_intraday_trader_followup_unit.py",
    "python/research/intraday_trader_followups.py",
    "python/research/intraday_trader_followup_reference.py",
    "python/research/intraday_trader_followup_inputs.py"}))


def contract(raw: bytes, original: bytes, scenario: bytes, *, actual: bool = False) -> tuple[dict, dict]:
    wire.require(type(raw) is bytes and 0 < len(raw) <= wire.MIB and type(actual) is bool,
                 "bounded mechanism-one registration required")
    spec = json.loads(raw, object_pairs_hook=primary._pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite mechanism registration")))
    wire.require(type(spec) is dict and spec["id"] == SPECIFICATION_ID and spec["unit_id"] == UNIT_ID
        and spec["campaign"] == "EE" and type(spec["actual_runner_ready"]) is bool
        and type(spec["actual_execution_authorized_by_this_file"]) is bool
        and spec["promotion_allowed"] is False, "mechanism-one registration identity differs")
    for reference, path, source in ((spec["source_preparation_reference"], primary.SPECIFICATION_PATH, original),
                                    (spec["scenario_reference"], primary.SCENARIO_PATH, scenario)):
        wire.require(type(source) is bytes and 0 < len(source) <= wire.MIB
            and reference["path"] == path and reference["sha256"] == wire.digest(source),
            "mechanism-one original source reference differs")
    original_spec = primary.contract(original, scenario)
    policies = spec["policies"]
    peer = spec["peer_contract"]
    output = spec["stored_output_preparation"]
    wire.require(type(policies) is list and tuple(row["id"] for row in policies) == POLICIES
        and spec["reporting"]["policies"] == list(POLICIES)
        and type(policies[0]["risk_multiple"]) is int and policies[0]["risk_multiple"] == 2
        and type(peer["window_minutes"]) is int and peer["window_minutes"] == 15
        and type(peer["minimum_complete_peer_windows"]) is int and peer["minimum_complete_peer_windows"] == 5
        and type(peer["maximum_other_members"]) is int and peer["maximum_other_members"] == 20
        and wire.encode(peer["mean_threshold"]) == wire.encode({"numerator":"0", "denominator":"1"})
        and peer["comparison"] == "strictly_greater"
        and output["profile"] == PROFILE and output["schema"] == OUTPUT_SCHEMA
        and output["filename"] == OUTPUT_FILENAME, "mechanism-one fixed scientific constants differ")
    if actual:
        wire.require(spec["actual_runner_ready"] and spec["actual_execution_authorized_by_this_file"],
                     "actual mechanism-one source not registered ready")
    return spec, original_spec


def mechanism_hooks(plan: bridge.UnitPlan, specification: bytes, original: bytes, scenario: bytes, *,
                    invented_only: bool, source_manifest_sha256: str,
                    runtime_sha256: str) -> bridge.UnitHooks:
    """Bind one registered new unit; config supplies neither science nor paths."""
    wire.require(type(invented_only) is bool and wire.valid_sha(source_manifest_sha256)
        and wire.valid_sha(runtime_sha256), "immutable mechanism hook identity required")
    spec, original_spec = contract(specification, original, scenario, actual=not invented_only)
    source = original_spec["actual_source_preparation"]
    wire.require(type(plan) is bridge.UnitPlan and isinstance(plan.local_execution_root, Path),
                 "mechanism-one typed local plan required")
    root = plan.local_execution_root
    leaf = UNIT_ID + "-actual1"
    wire.require(plan.unit_id == UNIT_ID and plan.specification_path == SPECIFICATION_PATH
        and plan.input_contract == wire.EE_INPUT_CONTRACT and plan.resource_profile == wire.ORDINARY300.id
        and plan.local_coordinator is True and plan.local_worktree == plan.remote_worktree
        and plan.ssh_argv == plan.remote_prefix == () and plan.recovery_contract is None
        and plan.input_pins == primary._pins(original_spec)
        and plan.decoded_stream_sha256 == primary.decoded_stream_pin(
            source["decoded_archives"]["CC"], source["decoded_archives"]["CX"])
        and plan.full_geometry == wire.encode(FULL_GEOMETRY)
        and plan.candidate_bounds == wire.encode(CANDIDATE_BOUNDS)
        and plan.output_filename == OUTPUT_FILENAME and plan.output_max_bytes == 512 * wire.MIB
        and plan.actual_runner_ready is spec["actual_runner_ready"]
        and set(MINIMUM_PUBLIC_SOURCES) <= set(plan.source_names)
        and plan.actual_cache == root / "cache" / leaf
        and plan.actual_output == root / "output" / leaf
        and plan.actual_diagnostics == root / "diagnostics" / leaf
        and plan.invented_cache_parent == root / "cache"
        and plan.invented_output_parent == root / "output"
        and plan.invented_diagnostics_parent == root / "diagnostics"
        and plan.canonical_journal == root / "canonical" / "experiments.jsonl"
        and plan.global_marker == root / "canonical" / "ee-unresolved.json"
        and plan.research_locks == (root / "canonical" / "intraday-research.lock", root / "output" / "ee-campaign.lock"),
        "mechanism-one fixed plan or exclusive unit paths differ")
    pins = {SPECIFICATION_PATH: wire.digest(specification), primary.SPECIFICATION_PATH: wire.digest(original),
            primary.SCENARIO_PATH: wire.digest(scenario)}

    def load_plan(config: dict) -> bridge.UnitPlan:
        wire.require(invented_only or (type(config.get("attempt")) is str and config["attempt"] == "actual1"
            and type(config.get("sessions")) is int and config["sessions"] == FULL_GEOMETRY["sessions"]),
            "mechanism actual requires fixed actual1 and full257 sessions")
        hashes = config["source_hashes"]
        wire.require(config.get("invented_only") is invented_only and config["unit_id"] == UNIT_ID
            and all(hashes.get(name) == value for name, value in pins.items())
            and wire.digest(wire.encode(hashes)) == source_manifest_sha256
            and config["local_identity"]["runtime_sha256"] == runtime_sha256,
            "mechanism-one fixed hook/source/runtime differs")
        return plan

    def make_invented(directory: Path, sessions: int, checkpoint: primary.Checkpoint) -> bridge.InputEnvelope:
        wire.require(invented_only, "invented mechanism generation unavailable to actual hook")
        return primary._make_invented(directory, sessions, checkpoint, profile=PROFILE)

    def compute_verify(directory: Path, envelope: bridge.InputEnvelope,
                       checkpoint: primary.Checkpoint) -> bridge.OutputReceipt:
        binding = {"registration_sha256": pins[SPECIFICATION_PATH],
            "scenario_specification_sha256": pins[primary.SCENARIO_PATH],
            "source_sha256": source_manifest_sha256, "runtime_sha256": runtime_sha256,
            "input_manifest_sha256": wire.digest(wire.encode([item.metadata() for item in envelope.manifest])),
            "caller": UNIT_ID}
        return primary._compute(directory, envelope, plan, source, invented_only, binding, checkpoint, profile=PROFILE)

    return bridge.UnitHooks(load_plan, make_invented, compute_verify)
