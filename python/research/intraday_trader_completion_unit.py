"""Closed EE reentry/joint hooks, source-only until the worker callback.

The two named units share original-three authentication and ordinary resources.
Joint actual readiness additionally requires a source-frozen selected pair; a
preparation fixture or prior qualification never chooses that pair implicitly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_trader_unit as primary


REENTRY = bridge.EE_REENTRY_UNIT_ID
JOINT = bridge.EE_JOINT_UNIT_ID
REENTRY_POLICIES = ("EE-A2R-FIRST", "EE-A2R-ADDITIONAL", "EE-B-BASE-FIRST", "EE-B-BASE-ADDITIONAL")
FAMILY_A = ("EE-A", "EE-A-2R-TARGET", "EE-A-2R-WITH-ONE-REENTRY")
FAMILY_B = ("EE-B", "EE-B-POSITIVE-PEERS", "EE-B-WITH-ONE-REENTRY")
INVENTED_PAIR = (FAMILY_A[2], FAMILY_B[2])
UNITS = {
    REENTRY: (bridge.EE_REENTRY_SPECIFICATION_PATH, "reentry", "EE-original-population-reentry-v1", primary.OUTPUT_FILENAME),
    JOINT: (bridge.EE_JOINT_SPECIFICATION_PATH, "joint", "EE-original-population-joint-v1", "trader-joint-account.jsonl.gz"),
}
MINIMUM_PUBLIC_SOURCES = tuple(sorted(set(primary.MINIMUM_PUBLIC_SOURCES) | {
    *(row[0] for row in UNITS.values()),
    "python/research/__init__.py",
    "python/research/experiment_log.py",
    "python/research/intraday_campaign_account.py",
    "python/research/intraday_campaign_cash_book.py",
    "python/research/intraday_ed_disclosures.py",
    "python/research/intraday_ed_replay.py",
    "python/research/intraday_ed_snapshot_verify.py",
    "python/research/intraday_pattern_calibration.py",
    "python/research/intraday_pattern_events.py",
    "python/research/research_budget.py",
    "configs/research/discovery/intraday-trader-mechanism-followups-v1.json",
    "python/research/intraday_trader_completion_unit.py",
    "python/tests/test_intraday_trader_completion_unit.py",
    "python/research/intraday_trader_reentry.py",
    "python/research/intraday_trader_reentry_reference.py",
    "python/research/intraday_trader_reentry_inputs.py",
    "python/research/intraday_trader_joint_account.py",
    "python/research/intraday_trader_joint_reference.py",
    "python/research/intraday_trader_joint_inputs.py",
    "python/research/intraday_trader_joint_report.py",
    "python/research/intraday_campaign_independent.py",
    "python/research/intraday_trader_followups.py",
    "python/research/intraday_trader_followup_reference.py",
    "python/research/intraday_trader_followup_inputs.py",
    "python/tests/test_intraday_trader_reentry.py",
    "python/tests/test_intraday_trader_reentry_report.py",
    "python/tests/test_intraday_trader_joint_account.py",
    "python/tests/test_intraday_trader_joint_report.py",
}))


def bounds(unit: str) -> dict:
    wire.require(unit in UNITS, "closed completion unit required")
    counts = {policy: 5140 for policy in REENTRY_POLICIES} if unit == REENTRY else {"A":10280, "B":10280}
    return {clock: dict(counts) for clock in ("S1", "S2")}


def contract(unit: str, raw: bytes, original: bytes, scenario: bytes, *, actual: bool = False) -> tuple[dict, dict]:
    wire.require(type(unit) is str and unit in UNITS and type(actual) is bool
        and type(raw) is bytes and 0 < len(raw) <= wire.MIB, "bounded closed completion registration required")
    spec = json.loads(raw, object_pairs_hook=primary._pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite completion registration")))
    path, profile, schema, filename = UNITS[unit]
    wire.require(type(spec) is dict and spec["id"] == Path(path).stem and spec["unit_id"] == unit
        and spec["campaign"] == "EE" and type(spec["actual_runner_ready"]) is bool
        and type(spec["actual_execution_authorized_by_this_file"]) is bool and spec["promotion_allowed"] is False,
        "completion registration identity differs")
    for name, expected, source in (("source_preparation_reference", primary.SPECIFICATION_PATH, original),
                                   ("scenario_reference", primary.SCENARIO_PATH, scenario)):
        wire.require(type(source) is bytes and 0 < len(source) <= wire.MIB
            and spec[name]["path"] == expected and spec[name]["sha256"] == wire.digest(source),
            "completion original source reference differs")
    original_spec = primary.contract(original, scenario)
    output = spec["stored_output_preparation"]
    wire.require((output["profile"], output["schema"], output["filename"]) == (profile, schema, filename),
                 "completion output contract differs")
    if unit == REENTRY:
        wire.require(spec["policy_slots"] == spec["policy_ids"] == list(REENTRY_POLICIES),
                     "fixed reentry slots differ")
    else:
        pair = spec["selected_pair"]
        wire.require(pair is None or (type(pair) is list and len(pair) == 2
            and type(pair[0]) is str and pair[0] in FAMILY_A and type(pair[1]) is str and pair[1] in FAMILY_B),
            "closed joint selected pair differs")
        if spec["actual_runner_ready"]:
            wire.require(pair is not None and spec["invented_pair"] == pair,
                         "source-frozen audited joint selection required; invented/actual pair differs")
        else:
            wire.require(spec["invented_pair"] == list(INVENTED_PAIR), "fixed joint preparation pair differs")
        if actual:
            evidence = spec["selection_evidence"]
            wire.require(pair is not None and type(evidence) is dict
                and evidence.get("mode") == "audited_spent_discovery_same_source_fixed_rule"
                and wire.valid_sha(evidence.get("audit_sha256")), "source-frozen audited joint selection required")
    if actual:
        wire.require(spec["actual_runner_ready"] and spec["actual_execution_authorized_by_this_file"],
                     "actual completion unit source not registered ready")
    return spec, original_spec


def completion_hooks(plan: bridge.UnitPlan, specification: bytes, original: bytes, scenario: bytes, *,
                     invented_only: bool, source_manifest_sha256: str, runtime_sha256: str) -> bridge.UnitHooks:
    wire.require(type(invented_only) is bool and type(plan) is bridge.UnitPlan
        and wire.valid_sha(source_manifest_sha256) and wire.valid_sha(runtime_sha256),
        "typed immutable completion hook required")
    spec, original_spec = contract(plan.unit_id, specification, original, scenario, actual=not invented_only)
    unit = plan.unit_id
    path, profile, _, filename = UNITS[unit]
    source = original_spec["actual_source_preparation"]
    root, leaf = plan.local_execution_root, unit + "-actual1"
    wire.require(isinstance(root, Path) and plan.specification_path == path
        and plan.input_contract == wire.EE_INPUT_CONTRACT and plan.resource_profile == wire.ORDINARY300.id
        and plan.local_coordinator is True and plan.local_worktree == plan.remote_worktree
        and plan.ssh_argv == plan.remote_prefix == () and plan.recovery_contract is None
        and plan.input_pins == primary._pins(original_spec)
        and plan.decoded_stream_sha256 == primary.decoded_stream_pin(source["decoded_archives"]["CC"], source["decoded_archives"]["CX"])
        and plan.full_geometry == wire.encode(primary.FULL_GEOMETRY) and plan.candidate_bounds == wire.encode(bounds(unit))
        and plan.output_filename == filename and plan.output_max_bytes == 512 * wire.MIB
        and plan.actual_runner_ready is spec["actual_runner_ready"] and set(MINIMUM_PUBLIC_SOURCES) <= set(plan.source_names)
        and plan.actual_cache == root / "cache" / leaf and plan.actual_output == root / "output" / leaf
        and plan.actual_diagnostics == root / "diagnostics" / leaf
        and plan.invented_cache_parent == root / "cache" and plan.invented_output_parent == root / "output"
        and plan.invented_diagnostics_parent == root / "diagnostics"
        and plan.canonical_journal == root / "canonical" / "experiments.jsonl"
        and plan.global_marker == root / "canonical" / "ee-unresolved.json"
        and plan.research_locks == (root / "canonical" / "intraday-research.lock", root / "output" / "ee-campaign.lock"),
        "completion fixed plan or exclusive unit paths differ")
    pins = {path:wire.digest(specification), primary.SPECIFICATION_PATH:wire.digest(original), primary.SCENARIO_PATH:wire.digest(scenario)}

    def load_plan(config: dict) -> bridge.UnitPlan:
        wire.require(invented_only or (type(config.get("attempt")) is str and config["attempt"] == "actual1"
            and type(config.get("sessions")) is int and config["sessions"] == primary.FULL_GEOMETRY["sessions"]),
            "completion actual requires fixed actual1/full257")
        hashes = config["source_hashes"]
        wire.require(config.get("invented_only") is invented_only and config["unit_id"] == unit
            and all(hashes.get(name) == value for name, value in pins.items())
            and wire.digest(wire.encode(hashes)) == source_manifest_sha256
            and config["local_identity"]["runtime_sha256"] == runtime_sha256,
            "completion fixed hook/source/runtime differs")
        return plan

    def make_invented(directory: Path, sessions: int, checkpoint: primary.Checkpoint) -> bridge.InputEnvelope:
        wire.require(invented_only, "invented completion generation unavailable to actual hook")
        raw_profile = "reentry"
        if unit == JOINT:
            pair = spec["invented_pair"]
            if not any("WITH-ONE-REENTRY" in policy for policy in pair):
                raw_profile = "mechanism1" if FAMILY_B[1] in pair else "primary"
        return primary._make_invented(directory, sessions, checkpoint, profile=raw_profile)

    def compute_verify(directory: Path, envelope: bridge.InputEnvelope, checkpoint: primary.Checkpoint) -> bridge.OutputReceipt:
        binding = {"registration_sha256":pins[path], "scenario_specification_sha256":pins[primary.SCENARIO_PATH],
            "source_sha256":source_manifest_sha256, "runtime_sha256":runtime_sha256,
            "input_manifest_sha256":wire.digest(wire.encode([item.metadata() for item in envelope.manifest])), "caller":unit}
        if unit == REENTRY:
            return primary._compute(directory, envelope, plan, source, invented_only, binding, checkpoint, profile=profile)
        pair = tuple(spec["invented_pair"] if invented_only else spec["selected_pair"])
        return _joint_compute(directory, envelope, plan, source, invented_only, binding, checkpoint, pair)

    return bridge.UnitHooks(load_plan, make_invented, compute_verify)


def _joint_compute(directory, envelope, plan, source, invented_only, binding, checkpoint, pair):
    from research import intraday_ed_comparison_unit as reuse
    from research import intraday_ed_snapshot as codec
    from research import intraday_trader_joint_report as report

    geometry = bridge.canonical(envelope.geometry)
    output = directory / plan.output_filename
    wire.safe_path(output)
    with primary.original_inputs(directory, envelope, plan, source, invented_only, checkpoint) as (universe, reader):
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            digest = report.write_report(report.report_records(universe, reader, selected_pair=pair, binding=binding,
                geometry=geometry, invented_only=invented_only, checkpoint=checkpoint),
                reuse._BoundedSink(stream, plan.output_max_bytes), checkpoint)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            verified = report.verify_report(report.read_report(stream, expected_sha256=digest.compressed_sha256,
                expected_decoded_sha256=digest.decoded_sha256, checkpoint=checkpoint), universe=universe,
                raw_reader=reader, selected_pair=pair, binding=binding, geometry=geometry,
                invented_only=invented_only, checkpoint=checkpoint)
        expected = {"days":geometry["sessions"], "targets":{clock:geometry["targets_per_scenario"] for clock in ("S1", "S2")},
                    "union_coordinates":geometry["union_coordinates"]}
        wire.require(verified.get("verified") is True and verified.get("record_stream_exhausted") is True
            and verified["record_counts"] == expected, "joint complete original-day verification required")
        audit = dict(reader.receipt)
    wire.require(wire.file_digest(output, plan.output_max_bytes, checkpoint) == (digest.compressed_sha256, digest.compressed_bytes),
                 "joint stored report changed")
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
    wire.fsync_directory(directory)
    proof = {"verified":True, "schema":verified["schema"], "record_counts":verified["record_counts"],
        "full_eof_verified":True, "all_original_coordinates_verified":True,
        "summary_sha256":wire.digest(wire.encode(codec.encode(verified["summary"]))), "raw_byte_audit":audit,
        "decoded_sha256":digest.decoded_sha256, "decoded_bytes":digest.decoded_bytes,
        "compressed_bytes":digest.compressed_bytes, "invented_only":invented_only,
        "input_manifest_sha256":binding["input_manifest_sha256"], "full_geometry":geometry == primary.FULL_GEOMETRY,
        "qualifies_actual_by_itself":False, "scope":verified["claim"]}
    return bridge.OutputReceipt(digest.compressed_sha256, digest.compressed_bytes, envelope.geometry,
                                wire.encode(proof), wire.encode(verified["candidate_counts"]))
