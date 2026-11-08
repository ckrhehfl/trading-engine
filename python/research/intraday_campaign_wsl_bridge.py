"""One registered WSL worker/controller and local or GCP journal lifecycle.

The private, reviewed entrypoint supplies fixed ``UnitPlan`` metadata and three
explicit hooks. Loading a plan must be source-only. Invented generation and
producer plus independent verification run only in the worker, after STARTED,
inside its one ordinary budget. No scientific implementation is imported here.
``run_role`` never dispatches a new attempt or retries an interrupted one.

All nested immutable metadata uses canonical ``wire.encode`` bytes. Hook output
paths are ``directory / plan.output_filename``; received inputs are in
``directory / 'received' / input.name``. The source closure is independently
derived at deployment, then frozen in the private entrypoint's source roster.
"""
from __future__ import annotations

from _socket import gethostname
from contextlib import contextmanager
from dataclasses import dataclass
import base64
import json
import math
import os
from pathlib import Path
import resource
import select
import signal
import stat
import subprocess
import sys
import time
from typing import BinaryIO, Callable

from research import intraday_campaign_wsl_transport as wire


PROFILE = wire.ORDINARY300
ED_H1_CPU_SCOPE = "ED-H1-operator-2026-10-31"
ED_SPECIFICATION_PATH = "configs/research/discovery/intraday-ed-h1-common-comparison-v1.json"
ED_UNIT_CONTRACTS = {"ED-H1-SNAPSHOT": wire.ED_INPUTS_CONTRACT,
    "ED-H1-RULES": wire.ED_SNAPSHOT_CONTRACT, "ED-H1-LIGHTGBM": wire.ED_SNAPSHOT_CONTRACT,
    "ED-H1-LIGHTGBM-ABLATION": wire.ED_SNAPSHOT_CONTRACT}
ED_H1_RECOVERY_CONTRACT = wire.encode({
    "schema": "ED-H1-technical-retry-v1",
    "approval": "call_1b4dcc6258244f38a8d59d6e99f0e27a option1",
    "date": "2026-11-01 KST", "unit_id": "ED-H1-SNAPSHOT",
    "previous_attempt": "actual1", "next_attempt": "actual2", "maximum_new_actual_attempts": 1,
    "previous_run_id": "85e1e2ab-86bd-4163-832f-32fd0da0bdf3",
    "previous_source_manifest_sha256": "a666dbce6d1f08030564756594fbb5628daffb6cef312fc9151efc25d29cc347",
    "previous_code_version": "7786fd35deef6989402dca1cab9ed0f4a87cc5b3",
    "previous_terminal_status": "failed",
    "previous_terminal_sha256": "1b946f24b89d59f4d48f75d1b5dd7b1028866e94e2229aff26905ed9c5edf5a4",
    "previous_terminal_offsets": [6346721, 6347559],
    "previous_marker_name": "ed-h1-common-unresolved.json",
    "recovery_marker_name": "ed-h1-recovery-20261101-unresolved.json",
    "snapshot_output_directory": "intraday-ed-h1-snapshot-recovery-20261101-v1",
    "resource_profile": "ed-h1-cpu600-as520-v1", "automatic_actual_retries": 0,
    "same_registered_42_inputs": True, "science_parameters_changed": False, "limits_changed": False,
    "scope": "One manually approved ED-H1 snapshot technical retry after exact predecessor failure "
        "authentication; preserve original evidence and marker, use new exclusive paths. Original "
        "RULES/LIGHTGBM reuse only the verified recovery snapshot, no additional attempts, parameters, "
        "fits, acquisition, H2/TCN, observer, order or resource changes."})
HOST = "local_wsl_with_gcp_canonical_journal"
LOCAL_HOST = "local_wsl_only"
EE_UNIT_ID = "EE-LOCAL-PRIMARY"
EE_SPECIFICATION_PATH = "configs/research/discovery/intraday-trader-local-input-v1.json"
EE_FOLLOWUP_UNIT_ID = "EE-MECHANISM-1"
EE_FOLLOWUP_SPECIFICATION_PATH = "configs/research/discovery/intraday-trader-mechanism-followups-v1.json"
EE_REENTRY_UNIT_ID = "EE-REENTRY-1"
EE_REENTRY_SPECIFICATION_PATH = "configs/research/discovery/intraday-trader-reentry-v1.json"
EE_JOINT_UNIT_ID = "EE-JOINT-1"
EE_JOINT_SPECIFICATION_PATH = "configs/research/discovery/intraday-trader-joint-v1.json"
EE_LOCAL_SPECIFICATIONS = ((EE_UNIT_ID, EE_SPECIFICATION_PATH),
                           (EE_FOLLOWUP_UNIT_ID, EE_FOLLOWUP_SPECIFICATION_PATH),
                           (EE_REENTRY_UNIT_ID, EE_REENTRY_SPECIFICATION_PATH),
                           (EE_JOINT_UNIT_ID, EE_JOINT_SPECIFICATION_PATH))
EF_UNIT_ID = "EF-SNAPSHOT-1"
EF_SPECIFICATION_PATH = "configs/research/discovery/intraday-chart-sequence-input-v1.json"
EF_RECEIPT_AUDITOR = "var/ef-snapshot-receipt-audit-v1.py"
EF_COMPARISON_SPEC = "configs/research/discovery/intraday-chart-sequence-execution-v1.json"
EF_COMPARISON_AUDITOR = "var/ef-comparison-receipt-audit-v1.py"
EF_FIT_UNITS = {"EF-TCN-S1-1": ("TCN", "S1"), "EF-TCN-S2-1": ("TCN", "S2"),
                "EF-LGB-S1-1": ("LGB", "S1"), "EF-LGB-S2-1": ("LGB", "S2")}
EF_REPORT_UNIT = "EF-COMPARISON-1"
EF_COMPARISON_UNITS = (*EF_FIT_UNITS, EF_REPORT_UNIT)
EF_LOCAL_BINDINGS = {EF_UNIT_ID: (EF_SPECIFICATION_PATH, wire.EF_INPUT_CONTRACT), **{
    unit: (EF_COMPARISON_SPEC, wire.EF_SEALED_CONTRACT) for unit in EF_COMPARISON_UNITS}}
EH_SUPPORT_UNIT_ID = "EH-SUPPORT-1"
EH_SUPPORT_SPECIFICATION_PATH = "configs/research/discovery/intraday-sequential-scenario-support-v1.json"
EH_SUPPORT_RUNTIME = "var/eh-support-runtime-v1.py"
EH_SUPPORT_RECEIPT_AUDITOR = "var/eh-support-receipt-audit-v1.py"
EH_SUPPORT_ATTEMPT = "actual1"
EH_SUPPORT_QUALIFICATION_ATTEMPT = "qualification-v1"
EH_SUPPORT_CAMPAIGN_LOCK = "eh-support-campaign.lock"
EH_SUPPORT_UNIT_LOCK = "eh-support-unit.lock"
EH_SUPPORT_LOCK_ENV = "EH_SUPPORT_LOCAL_UNIT_LOCK_FD"
EH_SUPPORT_OUTPUT_FILENAME = "eh-support.jsonl"
EH_SUPPORT_OUTPUT_MAX_BYTES = 16 * wire.MIB
EH_SUPPORT_OUTPUT_SCHEMA = "EH-SUPPORT-1-price-support-v1"
EH_OPEN_UNIT_ID = "EH-OPEN-1"
EH_OPEN_SPECIFICATION_PATH = "configs/research/discovery/intraday-sequential-opening-support-v1.json"
EH_OPEN_RUNTIME = "var/eh-opening-runtime-v1.py"
EH_OPEN_RECEIPT_AUDITOR = "var/eh-opening-receipt-audit-v1.py"
EH_OPEN_OUTPUT_FILENAME = "eh-opening-support.jsonl"
EH_OPEN_OUTPUT_MAX_BYTES = 32 * wire.MIB
EH_OPEN_OUTPUT_SCHEMA = "EH-OPEN-1-opening-support-v1"
EH_SNAPSHOT_UNIT_ID = "EH-SNAPSHOT-1"
EH_SNAPSHOT_SPECIFICATION_PATH = "configs/research/discovery/intraday-sequential-forecast-snapshot-v1.json"
EH_SNAPSHOT_RUNTIME = "var/eh-snapshot-runtime-v1.py"
EH_SNAPSHOT_RECEIPT_AUDITOR = "var/eh-snapshot-receipt-audit-v1.py"
EH_SNAPSHOT_OUTPUT_FILENAME = "eh-forecast-snapshot.bundle"
EH_SNAPSHOT_OUTPUT_MAX_BYTES = 160 * wire.MIB
EH_SNAPSHOT_OUTPUT_SCHEMA = "EH-SNAPSHOT-1-forecast-snapshot-v1"
EH_SNAPSHOT_RETRY_UNIT_ID = "EH-SNAPSHOT-RETRY-1"
EH_SNAPSHOT_RETRY_SPECIFICATION_PATH = "configs/research/discovery/intraday-sequential-forecast-snapshot-retry-v1.json"
EH_SNAPSHOT_RETRY_RUNTIME = "var/eh-snapshot-retry-runtime-v1.py"
EH_SNAPSHOT_RETRY_RECEIPT_AUDITOR = "var/eh-snapshot-retry-receipt-audit-v1.py"
EH_SNAPSHOT_RETRY_PRIOR = "var/eh-snapshot-retry-prior-v1.json"
EH_SNAPSHOT_RETRY_MARKER = "eh-snapshot-retry-unresolved.json"
EH_SNAPSHOT_RETRY_GUARD = "python/research/intraday_scenario_snapshot_retry.py"
EH_FORECAST_RUNTIME = "var/eh-forecast-model-runtime-v2.py"
EH_FORECAST_RECEIPT_AUDITOR = "var/eh-forecast-model-receipt-audit-v2.py"
EH_FORECAST_PREDECESSORS = "var/eh-forecast-predecessors-v1.json"
EH_FORECAST_MARKER = "eh-forecast-unresolved.json"
EH_FORECAST_OUTPUT_FILENAME = "eh-forecast-folds.bundle"
EH_FORECAST_OUTPUT_MAX_BYTES = 64 * wire.MIB
EH_FORECAST_OUTPUT_SCHEMA = "eh-forecast-fold-bundle-v1"
EH_FORECAST_EVALUATION_SESSIONS = 157
EH_FORECAST_S1_RECOVERY = "EH-MODEL-S1-QUAL-RETRY"
EH_FORECAST_S1_RECOVERY_SPEC = "configs/research/discovery/intraday-sequential-forecast-model-s1-qualification-recovery-v1.json"
EH_FORECAST_S1_RECOVERY_RUNTIME = "var/eh-forecast-model-s1-recovery-runtime-v1.py"
EH_FORECAST_S1_RECOVERY_AUDITOR = "var/eh-forecast-model-s1-recovery-receipt-audit-v1.py"
EH_MANAGEMENT_RUNTIME = "var/eh-management-runtime-v2.py"
EH_MANAGEMENT_RECEIPT_AUDITOR = "var/eh-management-receipt-audit-v2.py"
EH_MANAGEMENT_RUNTIME_V1 = "var/eh-management-runtime-v1.py"
EH_MANAGEMENT_AUDITOR_V1 = "var/eh-management-receipt-audit-v1.py"
EH_MANAGEMENT_C33_RECOVERY = "EH-MANAGEMENT-S1-C33-QUAL-RETRY"
EH_MANAGEMENT_C33_RECOVERY_SPEC = "configs/research/discovery/intraday-sequential-management-s1-c33-qualification-recovery-v1.json"
EH_MANAGEMENT_PREDECESSORS = "var/eh-management-predecessors-v1.json"
EH_MANAGEMENT_MARKER = "eh-management-unresolved.json"
EH_MANAGEMENT_ACTUAL_RETRY = "EH-MANAGEMENT-S1-C33-ACTUAL-RETRY"
EH_MANAGEMENT_ACTUAL_RETRY_SPEC = "configs/research/discovery/intraday-sequential-management-s1-c33-actual-recovery-v1.json"
EH_MANAGEMENT_RECOVERY_RUNTIME = "var/eh-management-runtime-v3.py"
EH_MANAGEMENT_RECOVERY_AUDITOR = "var/eh-management-receipt-audit-v3.py"
EH_MANAGEMENT_FAILURE_PIN = "var/eh-management-original-failure-v1.json"
EH_MANAGEMENT_RECOVERY_GUARD = "python/research/intraday_scenario_management_recovery.py"
EH_MANAGEMENT_RECOVERY_MARKER = "eh-management-recovery-unresolved.json"
EH_MANAGEMENT_OUTPUT_FILENAME = "eh-management-ledger.jsonl"
EH_MANAGEMENT_OUTPUT_MAX_BYTES = 32 * wire.MIB
EH_MANAGEMENT_OUTPUT_SCHEMA = "eh-sequential-management-trace-v1"
EH_MANAGEMENT_POLICY = wire.encode({"mode": "held", "risk_budget_won": 100_000,
                                   "downside_penalty": 1, "fresh_reentry": True})
_EH_RETRY_DEPENDENCIES = (EH_SUPPORT_RECEIPT_AUDITOR, EH_OPEN_RUNTIME, EH_OPEN_RECEIPT_AUDITOR,
                          EH_SNAPSHOT_RECEIPT_AUDITOR, EH_SNAPSHOT_RETRY_PRIOR)
_EH_MANAGEMENT_DEPENDENCIES = (*_EH_RETRY_DEPENDENCIES, EH_SNAPSHOT_RETRY_RUNTIME,
                               EH_SNAPSHOT_RETRY_RECEIPT_AUDITOR, EH_MANAGEMENT_PREDECESSORS)
_EH_MANAGEMENT_V2_DEPENDENCIES = (*_EH_MANAGEMENT_DEPENDENCIES, EH_MANAGEMENT_RUNTIME_V1,
                                  EH_MANAGEMENT_AUDITOR_V1)
_EH_MANAGEMENT_V3_DEPENDENCIES = (*_EH_MANAGEMENT_V2_DEPENDENCIES, EH_MANAGEMENT_RUNTIME,
                                  EH_MANAGEMENT_RECEIPT_AUDITOR, EH_MANAGEMENT_FAILURE_PIN)


@dataclass(frozen=True)
class _EHBinding:
    specification: str
    runtime: str
    auditor: str
    output: str
    output_max: int
    schema: str
    private_dependencies: tuple[str, ...] = ()
    scenario: str | None = None
    management_cost: int | None = None

    @property
    def input_contract(self) -> str:
        if self.management_cost is not None:
            return wire.EH_MANAGEMENT_INPUT_CONTRACT
        return wire.EH_SUPPORT_INPUT_CONTRACT if self.scenario is None else wire.EH_FORECAST_INPUT_CONTRACT


_EH_BINDINGS = {
    EH_SUPPORT_UNIT_ID: _EHBinding(EH_SUPPORT_SPECIFICATION_PATH, EH_SUPPORT_RUNTIME, EH_SUPPORT_RECEIPT_AUDITOR,
                                 EH_SUPPORT_OUTPUT_FILENAME, EH_SUPPORT_OUTPUT_MAX_BYTES, EH_SUPPORT_OUTPUT_SCHEMA),
    EH_OPEN_UNIT_ID: _EHBinding(EH_OPEN_SPECIFICATION_PATH, EH_OPEN_RUNTIME, EH_OPEN_RECEIPT_AUDITOR,
                              EH_OPEN_OUTPUT_FILENAME, EH_OPEN_OUTPUT_MAX_BYTES, EH_OPEN_OUTPUT_SCHEMA,
                              (EH_SUPPORT_RECEIPT_AUDITOR,)),
    EH_SNAPSHOT_UNIT_ID: _EHBinding(
        EH_SNAPSHOT_SPECIFICATION_PATH, EH_SNAPSHOT_RUNTIME, EH_SNAPSHOT_RECEIPT_AUDITOR,
        EH_SNAPSHOT_OUTPUT_FILENAME, EH_SNAPSHOT_OUTPUT_MAX_BYTES, EH_SNAPSHOT_OUTPUT_SCHEMA,
        (EH_SUPPORT_RECEIPT_AUDITOR, EH_OPEN_RUNTIME, EH_OPEN_RECEIPT_AUDITOR)),
    EH_SNAPSHOT_RETRY_UNIT_ID: _EHBinding(
        EH_SNAPSHOT_RETRY_SPECIFICATION_PATH, EH_SNAPSHOT_RETRY_RUNTIME, EH_SNAPSHOT_RETRY_RECEIPT_AUDITOR,
        EH_SNAPSHOT_OUTPUT_FILENAME, EH_SNAPSHOT_OUTPUT_MAX_BYTES, EH_SNAPSHOT_OUTPUT_SCHEMA,
        _EH_RETRY_DEPENDENCIES),
    **{f"EH-MODEL-{scenario}": _EHBinding(
        f"configs/research/discovery/intraday-sequential-forecast-model-{scenario.lower()}-v1.json",
        EH_FORECAST_RUNTIME, EH_FORECAST_RECEIPT_AUDITOR, EH_FORECAST_OUTPUT_FILENAME,
        EH_FORECAST_OUTPUT_MAX_BYTES, EH_FORECAST_OUTPUT_SCHEMA,
        (*_EH_RETRY_DEPENDENCIES, EH_SNAPSHOT_RETRY_RUNTIME, EH_SNAPSHOT_RETRY_RECEIPT_AUDITOR,
         EH_FORECAST_PREDECESSORS), scenario) for scenario in ("S1", "S2")},
    EH_FORECAST_S1_RECOVERY: _EHBinding(
        EH_FORECAST_S1_RECOVERY_SPEC, EH_FORECAST_S1_RECOVERY_RUNTIME, EH_FORECAST_S1_RECOVERY_AUDITOR,
        EH_FORECAST_OUTPUT_FILENAME, EH_FORECAST_OUTPUT_MAX_BYTES, EH_FORECAST_OUTPUT_SCHEMA,
        (*_EH_RETRY_DEPENDENCIES, EH_SNAPSHOT_RETRY_RUNTIME, EH_SNAPSHOT_RETRY_RECEIPT_AUDITOR,
         EH_FORECAST_PREDECESSORS, EH_FORECAST_RUNTIME, EH_FORECAST_RECEIPT_AUDITOR), "S1"),
    **{f"EH-MANAGEMENT-{scenario}-C{cost}": _EHBinding(
        f"configs/research/discovery/intraday-sequential-management-{scenario.lower()}-c{cost}-v1.json",
        EH_MANAGEMENT_RECOVERY_RUNTIME, EH_MANAGEMENT_RECOVERY_AUDITOR, EH_MANAGEMENT_OUTPUT_FILENAME,
        EH_MANAGEMENT_OUTPUT_MAX_BYTES, EH_MANAGEMENT_OUTPUT_SCHEMA,
        _EH_MANAGEMENT_V3_DEPENDENCIES, scenario, cost)
        for scenario, cost in (("S1", 43), ("S2", 33), ("S2", 43))},
    "EH-MANAGEMENT-S1-C33": _EHBinding(
        "configs/research/discovery/intraday-sequential-management-s1-c33-v1.json",
        EH_MANAGEMENT_RUNTIME_V1, EH_MANAGEMENT_AUDITOR_V1, EH_MANAGEMENT_OUTPUT_FILENAME,
        EH_MANAGEMENT_OUTPUT_MAX_BYTES, EH_MANAGEMENT_OUTPUT_SCHEMA, _EH_MANAGEMENT_DEPENDENCIES, "S1", 33),
    EH_MANAGEMENT_C33_RECOVERY: _EHBinding(
        EH_MANAGEMENT_C33_RECOVERY_SPEC, EH_MANAGEMENT_RUNTIME, EH_MANAGEMENT_RECEIPT_AUDITOR,
        EH_MANAGEMENT_OUTPUT_FILENAME, EH_MANAGEMENT_OUTPUT_MAX_BYTES, EH_MANAGEMENT_OUTPUT_SCHEMA,
        _EH_MANAGEMENT_V2_DEPENDENCIES, "S1", 33),
    EH_MANAGEMENT_ACTUAL_RETRY: _EHBinding(
        EH_MANAGEMENT_ACTUAL_RETRY_SPEC, EH_MANAGEMENT_RECOVERY_RUNTIME, EH_MANAGEMENT_RECOVERY_AUDITOR,
        EH_MANAGEMENT_OUTPUT_FILENAME, EH_MANAGEMENT_OUTPUT_MAX_BYTES, EH_MANAGEMENT_OUTPUT_SCHEMA,
        _EH_MANAGEMENT_V3_DEPENDENCIES, "S1", 33),
}
_EH_SPECIFICATIONS = frozenset(binding.specification for binding in _EH_BINDINGS.values())


EF_LOCK_ENV = "EF_LOCAL_UNIT_LOCK_FD"
EF_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CUDA_VISIBLE_DEVICES": "",
    "HIP_VISIBLE_DEVICES": "", "ROCR_VISIBLE_DEVICES": "", **{name: "1" for name in (
        "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS", "MALLOC_ARENA_MAX")}}
Checkpoint = Callable[[str], object]


def canonical(raw: bytes) -> dict:
    wire.require(type(raw) is bytes, "immutable canonical metadata required")
    value = json.loads(raw)
    wire.require(type(value) is dict and wire.encode(value) == raw, "canonical metadata differs")
    return value


@dataclass(frozen=True)
class InputPin:
    name: str
    original_path: str
    sha256: str
    maximum: int

    def metadata(self, size: int) -> dict:
        return {"name": self.name, "original_path": self.original_path,
                "sha256": self.sha256, "maximum": self.maximum, "size": size}


@dataclass(frozen=True)
class InputFile:
    name: str
    original_path: str
    sha256: str
    maximum: int
    size: int

    def metadata(self) -> dict:
        return {"name": self.name, "original_path": self.original_path,
                "sha256": self.sha256, "maximum": self.maximum, "size": self.size}


@dataclass(frozen=True)
class InputEnvelope:
    manifest: tuple[InputFile, ...]
    stream_sha256: str
    geometry: bytes
    input_contract: str = wire.EC_INPUT_CONTRACT

    def payload(self, kind: str = "INPUTS") -> dict:
        wire.require(type(self.manifest) is tuple and all(type(item) is InputFile for item in self.manifest),
                     "immutable input manifest required")
        manifest = [item.metadata() for item in self.manifest]
        inputs = wire.validate_registered_input_manifest(manifest, input_contract=self.input_contract)
        wire.require(wire.valid_sha(self.stream_sha256), "decoded stream pin missing")
        result = {"kind": kind, "inputs": inputs, "stream_sha256": self.stream_sha256,
                  "geometry": canonical(self.geometry)}
        if self.input_contract != wire.EC_INPUT_CONTRACT:
            result["input_contract"] = self.input_contract
        return result

    @classmethod
    def from_payload(cls, value: dict, kind: str = "INPUTS", *,
                     input_contract: str = wire.EC_INPUT_CONTRACT) -> InputEnvelope:
        fields = {"kind", "inputs", "stream_sha256", "geometry"}
        if input_contract != wire.EC_INPUT_CONTRACT:
            fields.add("input_contract")
        wire.require(set(value) == fields
                     and value["kind"] == kind, "input envelope fields differ")
        if input_contract != wire.EC_INPUT_CONTRACT:
            wire.require(value["input_contract"] == input_contract, "input envelope contract differs")
        manifest = wire.validate_registered_input_manifest(value["inputs"], input_contract=input_contract)
        result = cls(tuple(InputFile(**item) for item in manifest), value["stream_sha256"],
                     wire.encode(value["geometry"]), input_contract)
        result.payload()
        return result


@dataclass(frozen=True)
class OutputReceipt:
    output_sha256: str
    output_bytes: int
    geometry: bytes
    independent_verification: bytes
    candidate_counts: bytes

    def payload(self) -> dict:
        return {"output_sha256": self.output_sha256, "output_bytes": self.output_bytes,
                "geometry": canonical(self.geometry), "verification_complete": True,
                "independent_verification": canonical(self.independent_verification),
                "candidate_counts": canonical(self.candidate_counts)}


@dataclass(frozen=True)
class UnitPlan:
    """Fixed source metadata; config may choose only one fresh invented attempt.

    In remote mode, ``ssh_argv`` is the existing SSH binary, pinned key/known-hosts path options
    and destination, without a remote command. ``remote_prefix`` ends in the
    remote private entrypoint and ``coordinator``; config is appended as one
    base64 argument. Neither field contains credential contents. The fixed
    local modes require both tuples empty and one private native root.
    """
    unit_id: str
    specification_path: str
    source_names: tuple[str, ...]
    source_roster_sha256: str
    input_pins: tuple[InputPin, ...]
    decoded_stream_sha256: str
    window: bytes
    full_geometry: bytes
    candidate_bounds: bytes
    private_entrypoint: str
    local_worktree: Path
    remote_worktree: Path
    actual_cache: Path
    actual_output: Path
    actual_diagnostics: Path
    canonical_journal: Path
    global_marker: Path
    research_locks: tuple[Path, Path]
    invented_cache_parent: Path
    invented_output_parent: Path
    invented_diagnostics_parent: Path
    output_filename: str
    output_max_bytes: int
    ssh_argv: tuple[str, ...]
    remote_prefix: tuple[str, ...]
    actual_runner_ready: bool = False
    input_contract: str = wire.EC_INPUT_CONTRACT
    resource_profile: str = wire.ORDINARY300.id
    recovery_contract: bytes | None = None
    local_coordinator: bool = False
    local_execution_root: Path | None = None
    forecast_predecessors: bytes | None = None
    management_prices: bytes | None = None


@dataclass(frozen=True)
class UnitHooks:
    load_plan: Callable[[dict], UnitPlan]
    make_invented: Callable[[Path, int, Checkpoint], InputEnvelope]
    compute_verify: Callable[[Path, InputEnvelope, Checkpoint], OutputReceipt]


def _relative_source(name: str) -> bool:
    return (type(name) is str and bool(name) and "\\" not in name and "\x00" not in name
            and not name.startswith("/") and all(part not in ("", ".", "..") for part in name.split("/")))


def _is_ed_h1_profile(profile: wire.ResourceProfile) -> bool:
    return profile is wire.ED_H1_600 or profile is wire.ED_H1_600_AS520


def _eh_candidate_bounds(binding: _EHBinding, geometry: dict) -> dict:
    if binding.scenario is not None:
        return {binding.scenario: {"original": 20 * EH_FORECAST_EVALUATION_SESSIONS}}
    return {scenario: {"original": geometry["targets_per_scenario"]} for scenario in ("S1", "S2")}


def _forecast_predecessor(plan: UnitPlan, invented_only: bool):
    from research.intraday_scenario_forecast_handoff import SnapshotPredecessor

    wire.require(type(plan.forecast_predecessors) is bytes and type(invented_only) is bool,
                 "exact EH forecast predecessor registration required")
    modes = canonical(plan.forecast_predecessors)
    wire.require(set(modes) == {"invented", "actual"}, "fixed EH forecast predecessor modes required")
    pin = SnapshotPredecessor(**modes["invented" if invented_only else "actual"])
    wire.require(pin.invented_only is invented_only and all(wire.valid_sha(value) for value in (
        pin.publication_sha256, pin.receipt_audit_sha256, pin.output_sha256, pin.source_manifest_sha256,
        pin.runtime_sha256)) and type(pin.output_bytes) is int and 0 < pin.output_bytes <= EH_SNAPSHOT_OUTPUT_MAX_BYTES,
        "exact EH forecast predecessor pin differs")
    return pin


def _eh_snapshot_member(plan: UnitPlan, invented_only: bool) -> InputFile:
    pin = _forecast_predecessor(plan, invented_only)
    suffix = "-invented-" + EH_SUPPORT_QUALIFICATION_ATTEMPT if invented_only else "-" + EH_SUPPORT_ATTEMPT
    original = plan.local_execution_root / "output" / (EH_SNAPSHOT_RETRY_UNIT_ID + suffix) / EH_SNAPSHOT_OUTPUT_FILENAME
    return InputFile("EH-SNAPSHOT", str(original), pin.output_sha256, EH_SNAPSHOT_OUTPUT_MAX_BYTES, pin.output_bytes)


def _eh_management_prices(plan: UnitPlan, invented_only: bool) -> dict:
    wire.require(type(plan.management_prices) is bytes and type(invented_only) is bool,
                 "fixed EH management price registration required")
    modes = canonical(plan.management_prices)
    wire.require(set(modes) == {"invented", "actual"}, "fixed EH management price modes required")
    prices = modes["invented" if invented_only else "actual"]
    wire.require(type(prices) is dict and set(prices) == {"manifest", "decoded_sha256"}
                 and wire.valid_sha(prices["decoded_sha256"]), "EH management price registration differs")
    wire.validate_registered_input_manifest(prices["manifest"], input_contract=wire.EH_SUPPORT_INPUT_CONTRACT)
    return prices


def _eh_management_envelope(plan: UnitPlan, invented_only: bool) -> InputEnvelope:
    prices = _eh_management_prices(plan, invented_only)
    members = (*tuple(InputFile(**row) for row in prices["manifest"]), _eh_snapshot_member(plan, invented_only))
    return InputEnvelope(members, prices["decoded_sha256"], plan.full_geometry, wire.EH_MANAGEMENT_INPUT_CONTRACT)


def profile_for_plan(plan: UnitPlan) -> wire.ResourceProfile:
    wire.require(type(plan) is UnitPlan and type(plan.resource_profile) is str
                 and type(plan.unit_id) is str and type(plan.specification_path) is str,
                 "fixed unit resource profile required")
    profile = wire.resource_profile(plan.resource_profile)
    wire.require(type(plan.local_coordinator) is bool, "fixed coordinator mode required")
    if (profile is wire.EF_LOCAL_SEQUENCE or plan.unit_id in EF_LOCAL_BINDINGS
            or plan.specification_path in (EF_SPECIFICATION_PATH, EF_COMPARISON_SPEC)
            or plan.input_contract in (wire.EF_INPUT_CONTRACT, wire.EF_SEALED_CONTRACT)):
        wire.require(profile is wire.EF_LOCAL_SEQUENCE
            and EF_LOCAL_BINDINGS.get(plan.unit_id) == (plan.specification_path, plan.input_contract)
            and plan.local_coordinator and plan.recovery_contract is None,
            "EF local requires the exact registered unit/specification/input/resource pairing")
        return profile
    if (plan.unit_id in _EH_BINDINGS or plan.specification_path in _EH_SPECIFICATIONS
            or plan.input_contract in (wire.EH_SUPPORT_INPUT_CONTRACT, wire.EH_FORECAST_INPUT_CONTRACT,
                                       wire.EH_MANAGEMENT_INPUT_CONTRACT)):
        binding = _EH_BINDINGS.get(plan.unit_id)
        wire.require(binding is not None and plan.specification_path == binding.specification
                     and plan.input_contract == binding.input_contract and profile is PROFILE
                     and plan.local_coordinator and plan.recovery_contract is None,
                     "EH support requires the exact local unit/specification/input/ordinary300 pairing")
        return profile
    if (plan.local_coordinator or any(plan.unit_id == unit or plan.specification_path == spec
                                    for unit, spec in EE_LOCAL_SPECIFICATIONS)
            or plan.input_contract == wire.EE_INPUT_CONTRACT):
        wire.require(plan.local_coordinator
            and (plan.unit_id, plan.specification_path) in EE_LOCAL_SPECIFICATIONS
            and plan.input_contract == wire.EE_INPUT_CONTRACT
            and profile is PROFILE and plan.recovery_contract is None,
            "EE local requires the exact unit/specification/input/ordinary300 pairing")
    else:
        wire.require(plan.local_execution_root is None, "remote mode cannot bind a local execution root")
    wire.require(profile is PROFILE or _is_ed_h1_profile(profile), "unsupported unit resource profile")
    if _is_ed_h1_profile(profile):
        wire.require(plan.specification_path == ED_SPECIFICATION_PATH
            and type(plan.unit_id) is str and plan.unit_id in ED_UNIT_CONTRACTS
            and plan.input_contract == ED_UNIT_CONTRACTS[plan.unit_id],
            "ED CPU600 requires the exact registered ED unit/specification/input contract")
    if profile is wire.ED_H1_600_AS520:
        wire.require(type(plan.recovery_contract) is bytes
                     and plan.recovery_contract == ED_H1_RECOVERY_CONTRACT,
                     "AS520 requires the exact approved ED recovery contract")
    else:
        wire.require(plan.recovery_contract is None, "recovery contract requires the AS520 ED profile")
    return profile


def _bootstrap_profile(config: dict) -> wire.ResourceProfile:
    """Metadata-only admission before a role can load its source-only plan."""
    wire.require(type(config.get("resource_profile")) is str, "explicit bootstrap resource profile required")
    profile = wire.resource_profile(config["resource_profile"])
    hashes = config.get("source_hashes")
    unit = config.get("unit_id")
    if (profile is wire.EF_LOCAL_SEQUENCE or (type(unit) is str and unit in EF_LOCAL_BINDINGS)
            or (type(hashes) is dict and (EF_SPECIFICATION_PATH in hashes or EF_COMPARISON_SPEC in hashes))):
        wire.require(profile is wire.EF_LOCAL_SEQUENCE and type(unit) is str and unit in EF_LOCAL_BINDINGS
            and type(hashes) is dict and wire.valid_sha(hashes.get(EF_LOCAL_BINDINGS[unit][0]))
            and config.get("local_coordinator") is True and config.get("execution_host") == LOCAL_HOST
            and config.get("local_pipe_test", False) is False and "test_ssh_sample" not in config,
            "EF snapshot local bootstrap declaration differs")
        return profile
    eh_binding = _EH_BINDINGS.get(unit) if type(unit) is str else None
    if eh_binding is not None or (type(hashes) is dict and _EH_SPECIFICATIONS.intersection(hashes)):
        wire.require(profile is PROFILE and eh_binding is not None
                     and type(hashes) is dict and _EH_SPECIFICATIONS.intersection(hashes) == {eh_binding.specification}
                     and wire.valid_sha(hashes[eh_binding.specification])
                     and config.get("local_coordinator") is True and config.get("execution_host") == LOCAL_HOST
                     and config.get("local_pipe_test", False) is False and "test_ssh_sample" not in config,
                     "EH support local bootstrap declaration differs")
        return profile
    wire.require(profile is PROFILE or _is_ed_h1_profile(profile), "unsupported bootstrap resource profile")
    if _is_ed_h1_profile(profile):
        wire.require(type(config.get("unit_id")) is str and config["unit_id"] in ED_UNIT_CONTRACTS and type(hashes) is dict
            and wire.valid_sha(hashes.get(ED_SPECIFICATION_PATH)), "ED CPU600 bootstrap declaration differs")
    return profile


def execution_host(plan: UnitPlan) -> str:
    return LOCAL_HOST if plan.local_coordinator else HOST


def _mode_metadata(plan: UnitPlan) -> dict:
    return ({"local_coordinator": True, "local_execution_root": str(plan.local_execution_root),
             "execution_host": LOCAL_HOST} if plan.local_coordinator else {})


def _validate_mode_metadata(value: dict, plan: UnitPlan) -> None:
    if plan.local_coordinator:
        wire.require(value.get("local_coordinator") is True
                     and all(value.get(key) == expected for key, expected in _mode_metadata(plan).items()),
                     "local execution mode/root/host binding differs")
    else:
        wire.require(value.get("local_coordinator", False) is False
                     and value.get("local_execution_root") is None
                     and value.get("execution_host", HOST) == HOST, "remote execution mode/host binding differs")


def _aggregate(samples: dict, plan: UnitPlan) -> dict:
    profile = profile_for_plan(plan)
    return (wire.aggregate_local_roles(samples, profile=profile) if plan.local_coordinator else
            wire.aggregate_roles(samples, profile=profile))


def validate_plan(plan: UnitPlan) -> None:
    profile = profile_for_plan(plan)
    wire.require(type(plan) is UnitPlan and type(plan.source_names) is tuple
                 and plan.source_names == tuple(sorted(set(plan.source_names)))
                 and all(_relative_source(name) for name in plan.source_names), "fixed source names differ")
    wire.require(wire.digest(wire.encode(list(plan.source_names))) == plan.source_roster_sha256
                 and {plan.specification_path, plan.private_entrypoint,
                      "python/research/intraday_campaign_wsl_transport.py",
                      "python/research/intraday_campaign_wsl_bridge.py",
                      "python/research/experiment_log.py", "python/research/research_budget.py"}
                 <= set(plan.source_names), "fixed source closure differs")
    wire.require(type(plan.input_pins) is tuple and all(type(pin) is InputPin for pin in plan.input_pins),
                 "immutable original input pins required")
    manifest = [pin.metadata(1) for pin in plan.input_pins]
    if plan.input_contract == wire.EC_INPUT_CONTRACT:
        wire.validate_input_manifest(manifest)
        wire.require(plan.unit_id not in ("ED-H1-SNAPSHOT", "ED-H1-RULES", "ED-H1-LIGHTGBM",
                     "ED-H1-LIGHTGBM-ABLATION") and plan.specification_path !=
                     "configs/research/discovery/intraday-ed-h1-common-comparison-v1.json",
                     "explicit ED input contract required")
    elif plan.input_contract in (wire.EE_INPUT_CONTRACT, wire.EF_INPUT_CONTRACT, wire.EF_SEALED_CONTRACT,
                                wire.EH_SUPPORT_INPUT_CONTRACT, wire.EH_FORECAST_INPUT_CONTRACT,
                                wire.EH_MANAGEMENT_INPUT_CONTRACT):
        wire.validate_registered_input_manifest(manifest, input_contract=plan.input_contract)
        if profile is wire.EF_LOCAL_SEQUENCE:
            wire.require(all(not Path(pin.original_path).is_relative_to("/mnt") for pin in plan.input_pins),
                         "EF original input provenance must be native local WSL")
    else:
        wire.validate_ed_input_manifest(manifest, input_contract=plan.input_contract)
        expected_units = (("ED-H1-SNAPSHOT",) if plan.input_contract == wire.ED_INPUTS_CONTRACT else
                          ("ED-H1-RULES", "ED-H1-LIGHTGBM", "ED-H1-LIGHTGBM-ABLATION"))
        wire.require(plan.unit_id in expected_units and plan.specification_path ==
                     "configs/research/discovery/intraday-ed-h1-common-comparison-v1.json",
                     "registered ED unit or specification differs")
    wire.require(wire.valid_sha(plan.decoded_stream_sha256), "registered decoded pin differs")
    window = canonical(plan.window)
    wire.require(set(window) == {"symbol", "interval", "start", "end"}
                 and all(type(value) is str and value for value in window.values()), "scientific window differs")
    geometry = canonical(plan.full_geometry)
    wire.require(type(geometry.get("sessions")) is int and geometry["sessions"] > 0,
                 "full registered session geometry required")
    if plan.input_contract != wire.EC_INPUT_CONTRACT:
        largest = InputEnvelope(tuple(InputFile(**pin.metadata(pin.maximum)) for pin in plan.input_pins),
                                plan.decoded_stream_sha256, plan.full_geometry, plan.input_contract)
        wire.require(len(wire.encode(largest.payload("PROFILE_INPUTS"))) <= wire.HEADER_MAX,
                     "registered input header exceeded")
    canonical(plan.candidate_bounds)
    wire.require(type(plan.output_filename) is str and Path(plan.output_filename).name == plan.output_filename
                 and plan.output_filename not in ("", ".", "..", "EA-A", "EA-B", "CC", "publication.json",
                     "unresolved.json", "source-manifest.json", "prepared.json", "worker-budget.json",
                     "worker-failure.json", "bridge-failure.json", "completed-ack.json")
                 and plan.output_filename not in {pin.name for pin in plan.input_pins}
                 and type(plan.output_max_bytes) is int and 0 < plan.output_max_bytes <= 512 * wire.MIB,
                 "registered single output differs")
    eh_binding = _EH_BINDINGS.get(plan.unit_id)
    if eh_binding is not None:
        wire.require({EE_SPECIFICATION_PATH, eh_binding.auditor, *eh_binding.private_dependencies} <= set(plan.source_names)
                     and plan.private_entrypoint == eh_binding.runtime
                     and plan.output_filename == eh_binding.output
                     and plan.output_max_bytes == eh_binding.output_max,
                     "EH support registration, auditor or output closure differs")
    if eh_binding is not None and eh_binding.scenario is not None:
        wire.require(isinstance(plan.local_execution_root, Path), "fixed EH forecast native execution root required")
        prior = _forecast_predecessor(plan, False)
        invented = _forecast_predecessor(plan, True)
        member = _eh_snapshot_member(plan, False)
        wire.require(plan.input_pins[-1] == InputPin(member.name, member.original_path, member.sha256, member.maximum)
                     and prior.output_sha256 != invented.output_sha256
                     and (prior.merge_commit, prior.tree, prior.source_manifest_sha256, prior.runtime_sha256)
                     == (invented.merge_commit, invented.tree, invented.source_manifest_sha256, invented.runtime_sha256)
                     and canonical(plan.candidate_bounds) == _eh_candidate_bounds(eh_binding, geometry),
                     "EH forecast predecessor identity or population differs")
        if eh_binding.management_cost is None:
            wire.require(plan.decoded_stream_sha256 == member.sha256 and plan.management_prices is None,
                         "EH forecast decoded snapshot pin differs")
        else:
            actual_prices = _eh_management_prices(plan, False)
            invented_prices = _eh_management_prices(plan, True)
            invented_directory = Path(_eh_snapshot_member(plan, True).original_path).parent / "invented-inputs"
            price_pins = tuple(InputPin(row["name"], row["original_path"], row["sha256"], row["maximum"])
                               for row in actual_prices["manifest"])
            wire.require(plan.input_pins[:2] == price_pins
                         and plan.decoded_stream_sha256 == actual_prices["decoded_sha256"]
                         and all(row["original_path"] == str(invented_directory / row["name"])
                                 for row in invented_prices["manifest"]),
                         "EH management original or invented price pins differ")
    else:
        wire.require(plan.forecast_predecessors is None and plan.management_prices is None,
                     "forecast predecessor is limited to EH model or management units")
    for path in (plan.local_worktree, plan.remote_worktree, plan.actual_cache, plan.actual_output,
                 plan.actual_diagnostics, plan.canonical_journal, plan.global_marker,
                 plan.invented_cache_parent, plan.invented_output_parent, plan.invented_diagnostics_parent,
                 *plan.research_locks):
        # Foreign-host metadata is never inspected on this filesystem.
        wire.require(type(path) is Path or isinstance(path, Path), "fixed path metadata required")
        wire.require(path.is_absolute() and ".." not in path.parts, "absolute fixed path required")
    wire.require(type(plan.research_locks) is tuple and len(plan.research_locks) == 2
                 and plan.research_locks[0] != plan.research_locks[1]
                 and plan.global_marker.parent == plan.canonical_journal.parent
                 and plan.actual_output.parent == plan.invented_output_parent,
                 "fixed actual journal/marker/lock scope differs")
    if eh_binding is not None:
        wire.require(plan.invented_output_parent / EH_SUPPORT_CAMPAIGN_LOCK in plan.research_locks
                     and plan.actual_cache.parent == plan.invented_cache_parent,
                     "EH invented and actual attempts must share the same campaign and worker locks")
    is_snapshot_retry = plan.unit_id == EH_SNAPSHOT_RETRY_UNIT_ID
    uses_snapshot = eh_binding is not None and eh_binding.scenario is not None
    management_recovery = eh_binding is not None and eh_binding.runtime == EH_MANAGEMENT_RECOVERY_RUNTIME
    if is_snapshot_retry or uses_snapshot:
        native = plan.local_execution_root
        wire.require(isinstance(native, Path), "fixed EH native root required")
        leaf = f"{plan.unit_id}-{EH_SUPPORT_ATTEMPT}"
        marker = EH_SNAPSHOT_RETRY_MARKER
        if uses_snapshot:
            marker = EH_FORECAST_MARKER if eh_binding.management_cost is None else EH_MANAGEMENT_MARKER
        if management_recovery:
            marker = EH_MANAGEMENT_RECOVERY_MARKER
        proves_original_failure = is_snapshot_retry or plan.input_contract == wire.EH_MANAGEMENT_INPUT_CONTRACT
        wire.require((not proves_original_failure or EH_SNAPSHOT_RETRY_GUARD in plan.source_names)
                     and (not management_recovery or EH_MANAGEMENT_RECOVERY_GUARD in plan.source_names)
                     and (plan.invented_cache_parent, plan.invented_output_parent, plan.invented_diagnostics_parent)
                     == (native / "cache", native / "output", native / "diagnostics")
                     and (plan.actual_cache, plan.actual_output, plan.actual_diagnostics)
                     == (native / "cache" / leaf, native / "output" / leaf, native / "diagnostics" / leaf)
                     and plan.canonical_journal == native / "canonical" / "experiments.jsonl"
                     and plan.global_marker == native / "canonical" / marker
                     and plan.research_locks == (native / "canonical" / "intraday-research.lock",
                                                  native / "output" / EH_SUPPORT_CAMPAIGN_LOCK),
                     "EH snapshot retry or forecast must use exclusive paths and share the original locks and journal")
    else:
        wire.require(plan.global_marker.name != EH_SNAPSHOT_RETRY_MARKER,
                     "retry marker is reserved for the fixed EH snapshot retry")
        wire.require(plan.global_marker.name != EH_FORECAST_MARKER,
                     "forecast marker is reserved for the fixed EH forecast units")
        wire.require(plan.global_marker.name != EH_MANAGEMENT_MARKER,
                     "management marker is reserved for the fixed EH management units")
    wire.require(management_recovery or plan.global_marker.name != EH_MANAGEMENT_RECOVERY_MARKER,
                 "management recovery marker is reserved for the four fixed EH units")
    if plan.recovery_contract is not None:
        recovery = canonical(plan.recovery_contract)
        wire.require(plan.global_marker.name == recovery["recovery_marker_name"],
                     "fixed ED recovery marker differs")
        if plan.unit_id == "ED-H1-SNAPSHOT":
            wire.require(plan.actual_output.name == recovery["snapshot_output_directory"],
                         "fixed ED recovery snapshot output differs")
        else:
            prior = Path(plan.input_pins[0].original_path)
            wire.require(prior.parent == plan.actual_output.parent / recovery["snapshot_output_directory"],
                         "fixed ED recovery snapshot input path differs")
    wire.require(type(plan.ssh_argv) is tuple and type(plan.remote_prefix) is tuple
                 and all(type(arg) is str and arg and "\x00" not in arg
                         for arg in (*plan.ssh_argv, *plan.remote_prefix)), "immutable private SSH metadata required")
    wire.require(type(plan.actual_runner_ready) is bool, "actual readiness must be fixed metadata")
    if plan.local_coordinator:
        local_root = plan.local_execution_root
        wire.require(isinstance(local_root, Path), "fixed private local execution root required")
        if profile is wire.EF_LOCAL_SEQUENCE:
            wire.require(local_root.anchor == "/", "canonical native EF execution root required")
        if eh_binding is not None:
            wire.require(local_root.anchor == "/" and not local_root.is_relative_to("/mnt"),
                         "canonical native EH execution root required")
        wire.native_cache(local_root, plan.local_worktree)
        state = local_root.stat()
        wire.require(stat.S_ISDIR(state.st_mode) and stat.S_IMODE(state.st_mode) == 0o700
                     and state.st_uid == os.getuid(), "private owned native local execution root required")
        wire.require(plan.local_worktree == plan.remote_worktree and not plan.ssh_argv and not plan.remote_prefix,
                     "local mode forbids SSH/remote command or foreign worktree")
        for path in (plan.actual_cache, plan.actual_output, plan.actual_diagnostics, plan.canonical_journal,
                     plan.global_marker, *plan.research_locks, plan.invented_cache_parent,
                     plan.invented_output_parent, plan.invented_diagnostics_parent):
            wire.require(local_root in path.parents, "all local lifecycle paths must remain under fixed local root")
            wire.safe_path(path)
        return
    wire.require(plan.ssh_argv and Path(plan.ssh_argv[0]).name == "ssh"
                 and len(plan.remote_prefix) >= 5
                 and plan.remote_prefix[-4:] == ("-I", "-S",
                     str(plan.remote_worktree / plan.private_entrypoint), "coordinator"),
                 "registered isolated remote command differs")
    # These are the only caller options before the destination. No proxies,
    # forwarding, mux, personal configuration, extra commands or shell syntax.
    args = plan.ssh_argv[1:]
    index = 0
    while index < len(args) - 1:
        flag = args[index]
        wire.require(flag in ("-i", "-p", "-o") and index + 1 < len(args) - 1,
                     "unsupported private SSH option")
        value = args[index + 1]
        if flag == "-o":
            alias = value.removeprefix("HostKeyAlias=")
            safe_alias = (value.startswith("HostKeyAlias=") and 1 <= len(alias) <= 255
                          and alias[0] in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                          and all(char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                                  for char in alias))
            wire.require(value.startswith("UserKnownHostsFile=")
                         or safe_alias or value in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes"),
                         "unsupported private SSH route option")
        elif flag == "-i":
            wire.require(Path(value).is_absolute(), "absolute existing SSH key path required")
        else:
            wire.require(value.isdigit() and 1 <= int(value) <= 65535, "SSH port differs")
        index += 2
    wire.require(index == len(args) - 1 and not args[-1].startswith("-")
                 and not any(char.isspace() for char in args[-1]) and args[:-1].count("-i") == 1,
                 "SSH destination or fixed existing key path differs")
    wire.require(type(plan.actual_runner_ready) is bool, "actual readiness must be fixed metadata")


def validate_config(config: dict, plan: UnitPlan) -> None:
    validate_plan(plan)
    profile = profile_for_plan(plan)
    _validate_mode_metadata(config, plan)
    if plan.local_coordinator:
        wire.require(config.get("local_pipe_test", False) is False and "test_ssh_sample" not in config
                     and config.get("unit_id") == plan.unit_id
                     and config["local_identity"] == config["remote_identity"]
                     and config["local_identity"].get("execution_host") == LOCAL_HOST,
                     "local mode requires same-host identity and real samples; pipe smoke cannot qualify")
    wire.require(type(config.get("invented_only")) is bool
                 and config.get("automatic_retries") == 0 and config.get("wall_seconds") == profile.wall_seconds,
                 "single-attempt wall contract differs")
    wire.require(config.get("resource_profile") == profile.id, "config/plan resource profile differs")
    if profile is wire.EF_LOCAL_SEQUENCE:
        wire.require(type(config["automatic_retries"]) is int and type(config["wall_seconds"]) is int,
                     "EF exact retry and wall metadata required")
    if plan.unit_id in _EH_BINDINGS:
        wire.require(type(config["automatic_retries"]) is int and type(config["wall_seconds"]) is int,
                     "EH exact retry and wall metadata required")
    if _is_ed_h1_profile(profile):
        wire.require(config.get("unit_id") == plan.unit_id, "ED bootstrap unit/plan differs")
    hashes = config["source_hashes"]
    wire.require(type(hashes) is dict and set(hashes) == set(plan.source_names)
                 and all(wire.valid_sha(pin) for pin in hashes.values()), "source map missing/extra/invalid")
    for name in ("merge_commit", "tree", "reviewed_commit"):
        wire.require(type(config[name]) is str and len(config[name]) == 40
                     and all(char in "0123456789abcdef" for char in config[name]), "Git identity differs")
    wire.require(type(config["attempt"]) is str and 1 <= len(config["attempt"]) <= 100
                 and all(char.isalnum() or char in "-_" for char in config["attempt"]), "attempt identity differs")
    wire.require(type(config["deadline_epoch"]) in (int, float)
                 and math.isfinite(config["deadline_epoch"]), "fixed wall deadline differs")
    wire.require(type(config.get("local_pipe_test", False)) is bool, "test mode differs")
    if config["invented_only"]:
        wire.require(type(config["sessions"]) is int
                     and 1 <= config["sessions"] <= canonical(plan.full_geometry)["sessions"],
                     "invented geometry differs")
        if plan.unit_id in _EH_BINDINGS and config["attempt"] == EH_SUPPORT_QUALIFICATION_ATTEMPT:
            wire.require(config["sessions"] == canonical(plan.full_geometry)["sessions"],
                         "EH qualification requires full invented geometry")
        for key, parent in (("cache", plan.invented_cache_parent), ("output", plan.invented_output_parent),
                            ("diagnostics", plan.invented_diagnostics_parent)):
            path = Path(config[key])
            wire.require(path.parent == parent and path.name == plan.unit_id + "-invented-" + config["attempt"],
                         "exclusive invented path differs")
        wire.require(Path(config["journal"]) == Path(config["output"]) / "invented-experiments.jsonl"
                     and Path(config["journal"]) != plan.canonical_journal, "invented journal must be isolated")
    else:
        wire.require(plan.actual_runner_ready and not config.get("local_pipe_test", False),
                     "actual bridge not ready or local pipe cannot qualify")
        wire.require((Path(config["cache"]), Path(config["output"]), Path(config["diagnostics"]),
                      Path(config["journal"])) == (plan.actual_cache, plan.actual_output,
                      plan.actual_diagnostics, plan.canonical_journal), "fixed actual paths differ")
        if plan.recovery_contract is not None:
            recovery = canonical(plan.recovery_contract)
            expected_attempt = recovery["next_attempt"] if plan.unit_id == recovery["unit_id"] else "actual1"
            wire.require(config["attempt"] == expected_attempt, "fixed ED recovery actual attempt differs")
        if profile is wire.EF_LOCAL_SEQUENCE:
            wire.require(config["attempt"] == "actual1", "fixed EF first actual attempt required")
        if plan.unit_id in _EH_BINDINGS:
            wire.require(config["attempt"] == EH_SUPPORT_ATTEMPT and type(config.get("sessions")) is int
                         and config["sessions"] == canonical(plan.full_geometry)["sessions"],
                         "fixed EH actual1 and full geometry required")


def bounded_read(path: Path, maximum: int) -> bytes:
    before = wire.regular(path)
    wire.require(before.st_size <= maximum, "bounded source/evidence exceeded")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before),
                     "bounded file changed before open")
        raw = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
    wire.require(len(raw) == before.st_size and wire.file_identity(before)
                 == wire.file_identity(after) == wire.file_identity(wire.regular(path)), "bounded file changed")
    return raw


def runtime_sha() -> str:
    return wire.file_digest(Path(sys.executable).resolve(), 32 * wire.MIB, lambda _: None)[0]


def machine_sha() -> str:
    # CPython's public socket.gethostname is this same extension callable. The
    # coordinator needs no socket wrapper/enum allocation just to identify its host.
    return wire.digest(wire.encode({"hostname": gethostname(), "kernel": list(os.uname()),
        "python": sys.version, "executable": str(Path(sys.executable).resolve())}))


class GitHelpers:
    """Cumulative children reservation belongs to this role, never another grant."""
    def __init__(self, root: Path, role: str, *, profile: wire.ResourceProfile = PROFILE,
                 unit_lock_fd: int | None = None) -> None:
        self.profile = wire.resource_profile(profile)
        wire.require(role in self.profile.helper_limits(), "role has no Git helper reserve")
        self.root, self.role = root, role
        self.unit_lock_fd = unit_lock_fd
        self.receipt = {"cpu_seconds": 0.0, "count": 0, "readbacks": []}

    def git(self, *args: str) -> str:
        caps = self.profile.helper_limits()[self.role]
        remaining = int(caps["cpu_seconds"] - self.receipt["cpu_seconds"])
        wire.require(remaining >= 1, "source helper CPU reserve exhausted")
        ef_local = self.profile is wire.EF_LOCAL_SEQUENCE
        if ef_local:
            wire.require(self.unit_lock_fd is not None, "EF Git helper requires the lifecycle lock")
        if self.unit_lock_fd is not None:
            wire.require(args and args[0] in ("rev-parse", "status", "show", "merge-base"),
                         "local lifecycle Git helper only permits source verification commands")
        expected = {"cpu_seconds": remaining, "address_space_bytes": caps["address_space_bytes"]}
        read_fd, write_fd = os.pipe()
        def preexec() -> None:
            os.close(read_fd)
            resource.setrlimit(resource.RLIMIT_CPU, (remaining,) * 2)
            resource.setrlimit(resource.RLIMIT_AS, (caps["address_space_bytes"],) * 2)
            readback = {"cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU)[1],
                "address_space_bytes": resource.getrlimit(resource.RLIMIT_AS)[1]}
            if ef_local:
                wire.prohibit_ef_tasks()
                readback.update(rlimit_nproc=list(resource.getrlimit(resource.RLIMIT_NPROC)),
                    nice=os.getpriority(os.PRIO_PROCESS, 0), affinity=sorted(os.sched_getaffinity(0)))
            os.write(write_fd, wire.encode(readback))
            os.close(write_fd)
        process = None
        try:
            argv, extra_fds, env = ["git"], (), None
            if self.unit_lock_fd is not None:
                # These builtins are sequential, with no FS-monitor hook, pager or lazy fetch.
                argv = ["/usr/bin/git", "--no-pager", "-c", "core.fsmonitor=false",
                        "-c", "core.preloadIndex=false", "-c", "index.threads=1"]
                extra_fds = (self.unit_lock_fd,)
                env = {**EF_ENV, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                       "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
            if ef_local:
                expected.update(rlimit_nproc=[0, 0], nice=10, affinity=sorted(os.sched_getaffinity(0)))
            process = subprocess.Popen([*argv, "-C", str(self.root), *args], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, preexec_fn=preexec, pass_fds=(write_fd, *extra_fds), bufsize=0, env=env)
            os.close(write_fd)
            write_fd = -1
            readback = json.loads(os.read(read_fd, 4096))
            wire.require(readback == expected, "source helper hard-limit readback differs")
            raw = bytearray()
            while block := process.stdout.read(4097 - len(raw)):
                raw.extend(block)
                wire.require(len(raw) <= 4096, "source helper output exceeded")
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            elapsed = usage.ru_utime + usage.ru_stime
            self.receipt["cpu_seconds"] += elapsed
            self.receipt["count"] += 1
            if ef_local:
                readback["cpu_self_seconds"] = elapsed
            self.receipt["readbacks"].append(readback)
            wire.require(process.returncode == 0 and self.receipt["cpu_seconds"] < caps["cpu_seconds"],
                         "source helper failed or reserve exhausted")
            return raw.decode().strip()
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
            if process is not None:
                process.stdout.close()
                if process.returncode is None:
                    process.kill()
                    process.wait()


def verify_sources(config: dict, plan: UnitPlan, root: Path, side: str,
                   helpers: GitHelpers | None = None) -> dict:
    profile = profile_for_plan(plan)
    wire.require(root == (plan.local_worktree if side == "local" else plan.remote_worktree),
                 "registered source checkout differs")
    hashes = config["source_hashes"]
    wire.require(set(hashes) == set(plan.source_names), "source map missing/extra")
    manifest = {name: wire.file_digest(root / name, 16 * wire.MIB, lambda _: None)[0]
                for name in plan.source_names}
    wire.require(manifest == hashes, "authentic source manifest differs")
    eh_binding = _EH_BINDINGS.get(plan.unit_id)
    if eh_binding is not None:
        raw = bounded_read(root / plan.specification_path, 65536)
        wire.require(wire.digest(raw) == hashes[plan.specification_path], "EH support registration changed")
        specification = json.loads(raw)
        output = specification["output"]
        geometry = canonical(plan.full_geometry)
        private_names = (eh_binding.runtime, eh_binding.auditor, *eh_binding.private_dependencies)
        wire.require(specification["unit_id"] == plan.unit_id
                     and specification["input_contract"] == plan.input_contract
                     and specification["resource_profile"] == profile.id
                     and specification["execution_host"] == LOCAL_HOST
                     and specification["actual_runner_ready"] is plan.actual_runner_ready
                     and specification["actual_attempt"] == EH_SUPPORT_ATTEMPT
                     and type(specification["automatic_actual_retries"]) is int
                     and specification["automatic_actual_retries"] == 0
                     and specification["source_registration"] == {
                         "path": EE_SPECIFICATION_PATH, "sha256": hashes[EE_SPECIFICATION_PATH]}
                     and specification["private_source_pins"] == {name: hashes[name] for name in private_names}
                     and wire.encode(specification["geometry"]) == plan.full_geometry
                     and canonical(plan.candidate_bounds) == _eh_candidate_bounds(eh_binding, geometry)
                     and output["filename"] == plan.output_filename and output["schema"] == eh_binding.schema
                     and type(output["maximum_bytes"]) is int and output["maximum_bytes"] == plan.output_max_bytes,
                     "EH support source registration or readiness differs")
        if eh_binding.scenario is not None:
            management = eh_binding.management_cost is not None
            name = EH_MANAGEMENT_PREDECESSORS if management else EH_FORECAST_PREDECESSORS
            bounds = wire.EH_MANAGEMENT_INPUT_BOUNDS if management else wire.EH_FORECAST_INPUT_BOUNDS
            predecessor_raw = bounded_read(root / name, 65536)
            predecessor = canonical(predecessor_raw)
            wire.require(specification["scenario"] == eh_binding.scenario
                         and specification["predecessor"]["unit"] == EH_SNAPSHOT_RETRY_UNIT_ID
                         and specification["predecessor"]["registration"] == EH_SNAPSHOT_RETRY_SPECIFICATION_PATH
                         and wire.digest(predecessor_raw) == hashes[name]
                         and specification["input_roster"] == [list(row) for row in bounds]
                         and wire.encode(predecessor["predecessors"]) == plan.forecast_predecessors,
                         "EH forecast scenario or predecessor registration differs")
            if management:
                from research.intraday_scenario_support_unit import WINDOW, _price_contract

                _, pins, decoded = _price_contract(raw, bounded_read(root / EE_SPECIFICATION_PATH, 65536), plan.unit_id)
                wire.require(type(specification["cost_bps"]) is int
                             and specification["cost_bps"] == eh_binding.management_cost
                             and wire.encode(specification["management_policy"]) == EH_MANAGEMENT_POLICY
                             and wire.encode(predecessor["prices"]) == plan.management_prices
                             and plan.input_pins[:2] == pins and plan.decoded_stream_sha256 == decoded
                             and plan.window == wire.encode(WINDOW),
                             "EH management policy or original price registration differs")
            if plan.unit_id == EH_FORECAST_S1_RECOVERY:
                wire.require(specification["qualification"]["replaces"] == {"unit_id": "EH-MODEL-S1",
                             "attempt": EH_SUPPORT_QUALIFICATION_ATTEMPT,
                             "status": "stopped_before_started_in_source_verification"},
                             "EH S1 replacement qualification identity differs")
            if plan.unit_id == EH_MANAGEMENT_C33_RECOVERY:
                wire.require(specification["qualification"]["replaces"] == {"unit_id": "EH-MANAGEMENT-S1-C33",
                             "attempt": EH_SUPPORT_QUALIFICATION_ATTEMPT,
                             "status": "invented_only_stopped_in_source_closure"},
                             "EH management replacement qualification identity differs")
            if eh_binding.runtime == EH_MANAGEMENT_RECOVERY_RUNTIME:
                expected = {"failure_pin": EH_MANAGEMENT_FAILURE_PIN,
                            "retry_unit": EH_MANAGEMENT_ACTUAL_RETRY,
                            "role": "one_actual_retry" if plan.unit_id == EH_MANAGEMENT_ACTUAL_RETRY
                            else "first_actual_after_independent_retry_audit"}
                wire.require(specification["management_recovery"] == expected,
                             "EH management actual recovery registration differs")
        if plan.unit_id == EH_SNAPSHOT_RETRY_UNIT_ID:
            from research import intraday_scenario_snapshot_unit as snapshot

            original = bounded_read(root / EE_SPECIFICATION_PATH, 65536)
            _, pins, decoded = snapshot.contract(raw, original, retry=True)
            wire.require(specification["retry_of"] == {"unit_id": EH_SNAPSHOT_UNIT_ID,
                         "attempt": EH_SUPPORT_ATTEMPT, "evidence": EH_SNAPSHOT_RETRY_PRIOR}
                         and specification["input_roster"] == [list(row) for row in wire.EH_SUPPORT_INPUT_BOUNDS]
                         and plan.input_pins == pins and plan.decoded_stream_sha256 == decoded
                         and plan.window == wire.encode(snapshot.WINDOW),
                         "EH snapshot retry original input binding differs")
    identity = {"source_manifest_sha256": wire.digest(wire.encode(manifest)), "runtime_sha256": runtime_sha(),
                "machine_sha256": machine_sha(), "merge_commit": config["merge_commit"], "tree": config["tree"],
                "execution_host": execution_host(plan), "resource_profile": profile.id}
    wire.require(identity == config[side + "_identity"], "source/host/runtime/profile identity differs")
    if helpers is not None:
        wire.require(helpers.git("rev-parse", "HEAD") == config["merge_commit"]
                     and helpers.git("rev-parse", "HEAD^{tree}") == config["tree"]
                     and not helpers.git("status", "--porcelain", "--untracked-files=no"),
                     "source HEAD/tree/clean tracked differs")
        if not config.get("local_pipe_test", False):
            parents = helpers.git("show", "-s", "--format=%P", config["merge_commit"]).split()
            wire.require(len(parents) == 2 and parents[1] == config["reviewed_commit"]
                         and helpers.git("rev-parse", config["reviewed_commit"] + "^{tree}") == config["tree"],
                         "normal reviewed merge differs")
            helpers.git("merge-base", "--is-ancestor", config["merge_commit"], "origin/main")
    return identity


def require_free_disk(parent: Path) -> None:
    wire.safe_path(parent)
    available = os.statvfs(parent)
    wire.require(available.f_bavail * available.f_frsize >= 1024**3, "at least one GiB available disk required")


def worker_budget(plan: UnitPlan | None = None):
    from research.research_budget import EF_WORKER_SCOPE, ResearchBudget
    profile = PROFILE if plan is None else profile_for_plan(plan)
    caps = profile.role_limits()["worker"]
    scope = {"operator_cpu_scope": ED_H1_CPU_SCOPE} if _is_ed_h1_profile(profile) else {}
    if profile is wire.EF_LOCAL_SEQUENCE:
        scope = {"operator_cpu_scope": EF_WORKER_SCOPE, "wall_seconds": profile.wall_seconds}
    return ResearchBudget(cpu_seconds=caps["cpu_seconds"], address_space_bytes=caps["address_space_bytes"], **scope)


def _set_deadline(config: dict, profile: wire.ResourceProfile) -> None:
    if profile is wire.EF_LOCAL_SEQUENCE:
        wire.fixed_deadline(config["deadline_epoch"], profile=profile)
    else:
        wire.fixed_deadline(config["deadline_epoch"])


def _validate_worker_budget(budget: dict, profile: wire.ResourceProfile) -> None:
    scope = ED_H1_CPU_SCOPE if _is_ed_h1_profile(profile) else None
    if profile is wire.EF_LOCAL_SEQUENCE:
        from research.research_budget import EF_WORKER_SCOPE

        scope = EF_WORKER_SCOPE
    scope_matches = (budget.get("operator_cpu_scope") == scope if scope is not None else
                     "operator_cpu_scope" not in budget)
    wire.require(budget["status"] == "complete" and budget["violations"] == [] and scope_matches
                 and budget["limits"] == {**profile.role_limits()["worker"],
                     "wall_seconds": profile.wall_seconds, "nice": 10}, "registered worker budget not complete")


def _sample(role: str, helpers: GitHelpers | None = None, *, profile: wire.ResourceProfile = PROFILE) -> dict:
    wire.require(helpers is None or helpers.profile is profile, "helper/role resource profile differs")
    result = wire.role_sample(role, helpers.receipt if helpers else None, profile=profile)
    wire.validate_role_sample(result, role, profile=profile)
    return result


def _bounded_counts(counts: object, bounds: object) -> None:
    if type(bounds) is dict:
        wire.require(type(counts) is dict and set(counts) == set(bounds), "candidate count coordinates differ")
        for key in bounds:
            _bounded_counts(counts[key], bounds[key])
    else:
        wire.require(type(bounds) is int and type(counts) is int and 0 <= counts <= bounds,
                     "candidate bounds exceeded; no trimming")


def validate_result(result: dict, envelope: InputEnvelope, plan: UnitPlan, *,
                    invented_only: bool | None = None) -> None:
    wire.require(envelope.input_contract == plan.input_contract, "result input contract differs")
    wire.require(set(result) == {"output_sha256", "output_bytes", "geometry", "verification_complete",
                 "independent_verification", "candidate_counts"}
                 and wire.valid_sha(result["output_sha256"]) and type(result["output_bytes"]) is int
                 and 0 < result["output_bytes"] <= plan.output_max_bytes
                 and result["geometry"] == canonical(envelope.geometry)
                 and result["verification_complete"] is True
                 and type(result["independent_verification"]) is dict
                 and result["independent_verification"].get("verified") is True,
                 "verified single output metadata differs")
    _bounded_counts(result["candidate_counts"], canonical(plan.candidate_bounds))
    eh_binding = _EH_BINDINGS.get(plan.unit_id)
    if eh_binding is not None:
        verification = result["independent_verification"]
        manifest = [item.metadata() for item in envelope.manifest]
        manifest_sha = wire.digest(wire.encode(manifest))
        geometry = result["geometry"]
        full_geometry = envelope.geometry == plan.full_geometry
        wire.require(type(invented_only) is bool and verification.get("invented_only") is invented_only
                     and verification.get("schema") == eh_binding.schema
                     and verification.get("full_eof_verified") is True
                     and verification.get("all_original_coordinates_verified") is True
                     and verification.get("full_geometry") is full_geometry
                     and verification.get("input_manifest_sha256") == manifest_sha
                     and result["candidate_counts"] == _eh_candidate_bounds(eh_binding, geometry),
                     "EH support independent reconstruction or original denominators differ")
        if eh_binding.scenario is not None and eh_binding.management_cost is None:
            summary = verification.get("forecast_summary")
            wire.require(full_geometry and verification.get("input_manifest") == manifest
                         and len(manifest) == 1 and manifest[0]["sha256"] == envelope.stream_sha256
                         and wire.valid_sha(verification.get("numpy_manifest_sha256"))
                         and type(summary) is dict and summary.get("schema") == eh_binding.schema
                         and summary.get("scenario") == eh_binding.scenario and summary.get("folds") == 5
                         and summary.get("evaluation_sessions") == EH_FORECAST_EVALUATION_SESSIONS
                         and summary.get("original_evaluation_coordinates") == 20 * EH_FORECAST_EVALUATION_SESSIONS
                         and summary.get("full_forecast_and_score_verification") is True,
                         "EH forecast full prediction, receipt or score verification differs")
            return
        raw_audit = verification.get("raw_byte_audit")
        archive_pins = {"CC": {"kind": "CC", "compressed_sha256": envelope.manifest[1].sha256,
            "decoded_sha256": envelope.stream_sha256, "expected_chunks": geometry["cc_chunks"],
            "expected_rows": geometry["cc_rows"], "expected_daily_rows": 0}}
        wire.require(wire.valid_sha(verification.get("summary_sha256"))
                     and type(raw_audit) is dict and raw_audit.get("schema") == "ED-raw-byte-cache-audit-v1"
                     and raw_audit.get("verified") is True and raw_audit.get("full_eof_verified") is True
                     and wire.valid_sha(raw_audit.get("cache_sha256"))
                     and type(raw_audit.get("raw_keys")) is int and raw_audit["raw_keys"] == geometry["cc_chunks"]
                     and wire.encode(raw_audit.get("archives")) == wire.encode(archive_pins),
                     "EH support independent reconstruction or original denominators differ")
        if eh_binding.management_cost is not None:
            summary = verification.get("management_summary")
            wire.require(full_geometry and verification.get("input_manifest") == manifest
                         and type(summary) is dict and summary.get("scenario") == eh_binding.scenario
                         and type(summary.get("cost_bps")) is int and summary["cost_bps"] == eh_binding.management_cost
                         and summary.get("evaluation_sessions") == EH_FORECAST_EVALUATION_SESSIONS
                         and summary.get("original_evaluation_coordinates") == 20 * EH_FORECAST_EVALUATION_SESSIONS
                         and wire.valid_sha(summary.get("report_sha256"))
                         and wire.digest(wire.encode(summary)) == verification["summary_sha256"]
                         and type(summary.get("input_members")) is dict and set(summary["input_members"]) == {"features", "labels"}
                         and type(summary.get("verified_execution")) is dict
                         and set(summary["verified_execution"]) == {"modeled", "declined", "indeterminate", "payoff_checks"}
                         and all(type(count) is int and count >= 0 for count in summary["verified_execution"].values()),
                         "EH management verified ledger and report evidence differs")


def actual_input_metadata(plan: UnitPlan, phases: wire.WorkerPhases) -> InputEnvelope:
    # This is the only actual source stat. Its capability requires a durable ACK.
    phases.input_access()
    manifest = tuple(InputFile(**pin.metadata(wire.regular(Path(pin.original_path)).st_size))
                     for pin in plan.input_pins)
    envelope = InputEnvelope(manifest, plan.decoded_stream_sha256, plan.full_geometry, plan.input_contract)
    envelope.payload()
    return envelope


def _actual_envelope(envelope: InputEnvelope, plan: UnitPlan) -> None:
    wire.require(envelope.input_contract == plan.input_contract
                 and tuple(InputPin(item.name, item.original_path, item.sha256, item.maximum)
                       for item in envelope.manifest) == plan.input_pins
                 and envelope.stream_sha256 == plan.decoded_stream_sha256
                 and envelope.geometry == plan.full_geometry, "actual source/decoded pins or geometry differ")
    if plan.input_contract == wire.EH_FORECAST_INPUT_CONTRACT:
        wire.require(envelope.manifest[0].size == _forecast_predecessor(plan, False).output_bytes,
                     "EH forecast actual predecessor size differs")
    if plan.input_contract == wire.EH_MANAGEMENT_INPUT_CONTRACT:
        wire.require(envelope == _eh_management_envelope(plan, False), "EH management actual receipt sizes differ")


def _invented_envelope(envelope: InputEnvelope, plan: UnitPlan, sessions: int) -> None:
    envelope.payload()
    wire.require(envelope.input_contract == plan.input_contract, "invented input contract differs")
    geometry, full = canonical(envelope.geometry), canonical(plan.full_geometry)
    wire.require(set(geometry) == set(full) and geometry["sessions"] == sessions,
                 "invented geometry coordinates differ")
    if plan.input_contract == wire.EH_FORECAST_INPUT_CONTRACT:
        member = _eh_snapshot_member(plan, True)
        wire.require(envelope.geometry == plan.full_geometry and envelope.manifest == (member,)
                     and envelope.stream_sha256 == member.sha256, "EH invented forecast predecessor differs")
        return
    if plan.input_contract == wire.EH_MANAGEMENT_INPUT_CONTRACT:
        wire.require(envelope == _eh_management_envelope(plan, True), "EH invented management predecessor differs")
        return
    def bounded(value: object, limit: object) -> None:
        if type(limit) is dict:
            wire.require(type(value) is dict and set(value) == set(limit), "invented geometry fields differ")
            for key in limit:
                bounded(value[key], limit[key])
        elif type(limit) is int:
            wire.require(type(value) is int and 0 <= value <= limit, "invented geometry exceeded")
        else:
            wire.require(value == limit, "invented geometry metadata differs")
    bounded(geometry, full)


def no_network(*args: object, **kwargs: object) -> None:
    raise RuntimeError("ordinary WSL research worker permits stdio IPC only")


def restrict_worker_network() -> None:
    """Install only inside the dedicated worker; SSH remains its own child."""
    import socket

    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        setattr(socket.socket, method, no_network)
    socket.create_connection = no_network


def restrict_local_network() -> None:
    """An all-local lifecycle has no socket-creation or remote transport role."""
    import socket

    restrict_worker_network()
    # Keep the class available for imports such as ssl.SSLSocket. Block its
    # constructor in place, including callers that imported the class earlier.
    socket.socket.__init__ = no_network
    socket.socketpair = no_network


def worker(config: dict, plan: UnitPlan, hooks: UnitHooks, root: Path,
           incoming: BinaryIO, outgoing: BinaryIO) -> int:
    profile = profile_for_plan(plan)
    _set_deadline(config, profile)
    wire.apply_role_limits("worker", profile=profile)
    restrict_worker_network()
    budget = worker_budget(plan)
    verify_sources(config, plan, root, "local")
    if profile is wire.EF_LOCAL_SEQUENCE and not config["invented_only"]:
        validate_ef_receipt_audit(config, plan)
    if plan.unit_id in _EH_BINDINGS and not config["invented_only"]:
        validate_eh_support_receipt_audit(config, plan)
    wire.require(config.get("local_pipe_test", False) or "microsoft" in os.uname().release.lower(),
                 "WSL runtime required")
    directory = Path(config["cache"])
    wire.native_cache(directory, root)
    require_free_disk(directory.parent)
    import fcntl
    lock_name = "ee-local-worker.lock" if plan.local_coordinator else "ec-ordinary-worker.lock"
    if plan.unit_id in _EH_BINDINGS:
        lock_name = "eh-support-worker.lock"
    elif profile is wire.EF_LOCAL_SEQUENCE:
        lock_name = "ef-local-worker.lock"
    worker_lock = directory.parent / lock_name
    wire.safe_path(worker_lock)
    lock_fd = os.open(worker_lock,
                      os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        wire.exclusive_directory(directory)
        wire.exclusive_bytes(directory / "source-manifest.json", wire.encode(config["source_hashes"]))
        phases = wire.WorkerPhases(config["remote_identity"])
        wire.send_header(outgoing, {"kind": "HELLO", "identity": config["local_identity"]})
        try:
            started = wire.read_header(incoming, "STARTED")
            wire.require(started.get("deadline_epoch") == config["deadline_epoch"], "remote deadline differs")
            phases.accept_started(started)
            phases.input_access()
            original = None
            if config["invented_only"]:
                generated = directory / "generated"
                wire.exclusive_directory(generated)
                original = hooks.make_invented(generated, config["sessions"], budget.checkpoint)
                _invented_envelope(original, plan, config["sessions"])
                wire.send_header(outgoing, original.payload("PROFILE_INPUTS"))
                for item in original.manifest:
                    wire.send_file(outgoing, generated / item.name, item.metadata(), budget.checkpoint)
            envelope = InputEnvelope.from_payload(wire.read_header(incoming, "INPUTS"),
                                                  input_contract=plan.input_contract)
            if config["invented_only"]:
                wire.require(envelope == original, "invented round-trip envelope differs")
            else:
                _actual_envelope(envelope, plan)
            received = directory / "received"
            wire.exclusive_directory(received)
            for item in envelope.manifest:
                phases.input_access()
                wire.receive_file(incoming, received / item.name, item.metadata(), budget.checkpoint)
            receipt = hooks.compute_verify(directory, envelope, budget.checkpoint)
            wire.require(type(receipt) is OutputReceipt, "registered output receipt required")
            result = receipt.payload()
            validate_result(result, envelope, plan, invented_only=config["invented_only"])
            for item in envelope.manifest:
                wire.require(wire.file_digest(received / item.name, item.maximum, budget.checkpoint)
                             == (item.sha256, item.size), "input changed during computation")
            output = directory / plan.output_filename
            wire.require(wire.file_digest(output, plan.output_max_bytes, budget.checkpoint)
                         == (receipt.output_sha256, receipt.output_bytes), "stored exact output differs")
            verify_sources(config, plan, root, "local")
            metadata = {"name": plan.output_filename, "original_path": str(output), "sha256": receipt.output_sha256,
                        "size": receipt.output_bytes, "maximum": plan.output_max_bytes}
            wire.send_header(outgoing, {"kind": "RESULT", "result": result, "file": metadata})
            wire.send_file(outgoing, output, metadata, budget.checkpoint)
            finished, sample = budget.finish(), _sample("worker", profile=profile)
            wire.exclusive_bytes(directory / "worker-budget.json", wire.encode({"budget": finished, "sample": sample}))
            wire.send_header(outgoing, {"kind": "WORKER_DONE", "sample": sample, "budget": finished})
            terminal = wire.read_header(incoming, "COMPLETED")
            phases.accept_completed(terminal, receipt.output_sha256, digest_field="output_sha256")
            wire.require(wire.valid_sha(terminal.get("publication_sha256")), "publication pin differs")
            wire.exclusive_bytes(directory / "completed-ack.json", wire.encode(terminal))
            wire.send_header(outgoing, {"kind": "ACK_ACCEPTED", "run_id": terminal["run_id"],
                "record_sha256": terminal["record_sha256"], "publication_sha256": terminal["publication_sha256"]})
            return 0
        except BaseException as error:
            wire.exclusive_bytes(directory / "worker-failure.json", wire.encode({"status": "unknown",
                "error_type": type(error).__name__, "automatic_retries": 0,
                "canonical_truth": ("local WSL journal only" if plan.local_coordinator else "GCP journal only"),
                **_mode_metadata(plan)}))
            raise
    finally:
        os.close(lock_fd)


def journal_record(log: Path, offset: int, status: str, run_id: str | None = None, *,
                   end: int | None = None) -> tuple[dict, str, int]:
    wire.safe_path(log)
    before = wire.regular(log)
    descriptor = os.open(log, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before),
                     "canonical journal changed before read")
        wire.require(type(offset) is int and offset >= 0 and (end is None or
                     type(end) is int and offset < end <= before.st_size and end - offset <= 32768),
                     "canonical compact span coordinates differ")
        stream.seek(offset)
        raw = stream.read(32769 if end is None else end - offset)
    wire.require(end is None or len(raw) == end - offset, "canonical fixed span truncated")
    wire.require(len(raw) <= 32768, "canonical compact span exceeded")
    lines = raw.splitlines(keepends=True)
    wire.require(len(lines) == (1 if status == "started" else 2), "canonical journal span count differs")
    records = [json.loads(line) for line in lines]
    wire.require(records[0]["status"] == "started" and records[-1]["status"] == status
                 and (run_id is None or all(item["run_id"] == run_id for item in records)),
                 "durable journal transition differs")
    return records[-1], wire.digest(lines[-1]), offset + len(raw)


def failed_journal_record(log: Path, offset: int, end: int, run_id: str) -> tuple[dict, str, int]:
    """Authenticate one bounded failed terminal line, never a lifecycle span.

    Recovery approval pins the original failed line itself. Completed lifecycle
    readers and durable-completion marker handling retain their existing rules.
    """
    wire.safe_path(log)
    before = wire.regular(log)
    wire.require(type(offset) is int and type(end) is int
                 and 0 <= offset < end <= before.st_size and end - offset <= 32768,
                 "failed terminal coordinates differ")
    descriptor = os.open(log, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before),
                     "canonical journal changed before read")
        stream.seek(offset)
        raw = stream.read(end - offset)
    wire.require(len(raw) == end - offset and len(raw.splitlines(keepends=True)) == 1,
                 "failed terminal must be one complete bounded line")
    record = json.loads(raw)
    wire.require(type(record) is dict
                 and (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode() == raw
                 and record.get("record_type") == "discovery_trial"
                 and record.get("status") == "failed" and record.get("run_id") == run_id,
                 "canonical failed terminal differs")
    return record, wire.digest(raw), end


def ed_recovery_proof() -> dict:
    """Pure expected control-metadata proof; this function does no filesystem IO."""
    recovery = canonical(ED_H1_RECOVERY_CONTRACT)
    marker = wire.encode({"attempt": recovery["previous_attempt"], "state": "unresolved", "automatic_retries": 0})
    return {"verified": True, "unit_id": recovery["unit_id"],
        "recovery_contract_sha256": wire.digest(ED_H1_RECOVERY_CONTRACT),
        "source_manifest_sha256": recovery["previous_source_manifest_sha256"],
        "code_version": recovery["previous_code_version"], "run_id": recovery["previous_run_id"],
        "canonical_offsets": recovery["previous_terminal_offsets"],
        "terminal_record_sha256": recovery["previous_terminal_sha256"],
        "unresolved_marker_sha256": wire.digest(marker), "market_input_accesses": 0}


def _ed_recovery_parameters(plan: UnitPlan) -> dict:
    if plan.recovery_contract is None:
        return {}
    proof = ed_recovery_proof()
    return {"recovery_contract_sha256": proof["recovery_contract_sha256"],
        "recovery_predecessor_terminal_sha256": proof["terminal_record_sha256"]}


def _validate_ed_recovery_evidence(publication: dict, parameters: dict, plan: UnitPlan) -> None:
    if plan.recovery_contract is not None:
        wire.require(wire.encode(publication.get("recovery_predecessor")) ==
                     wire.encode(ed_recovery_proof())
                     and all(parameters.get(key) == value
                             for key, value in _ed_recovery_parameters(plan).items()),
                     "ED recovery publication/canonical proof differs")


def validate_ed_recovery_predecessor(config: dict, plan: UnitPlan) -> dict | None:
    """Read only the approved failed control span and original unresolved marker.

    The coordinator calls this under its locks before STARTED, for invented and
    actual lifecycles alike. No price source, failed output body or tail is read.
    The original marker is authenticated byte for byte and is never cleared.
    """
    profile_for_plan(plan)
    if plan.recovery_contract is None:
        return None
    validate_plan(plan)
    recovery = canonical(plan.recovery_contract)
    wire.require(plan.global_marker == plan.canonical_journal.parent / recovery["recovery_marker_name"],
                 "fixed ED recovery marker differs")
    first, last = recovery["previous_terminal_offsets"]
    record, pin, finish = failed_journal_record(plan.canonical_journal, first, last,
                                               recovery["previous_run_id"])
    parameters = record["parameters"]
    wire.require(pin == recovery["previous_terminal_sha256"] and finish == last
        and record["study_id"] == recovery["unit_id"]
        and record["code_version"] == recovery["previous_code_version"]
        and record["window"] == canonical(plan.window)
        and parameters["invented_only"] is False
        and parameters["attempt"] == recovery["previous_attempt"]
        and parameters["input_contract"] == wire.ED_INPUTS_CONTRACT
        and parameters["resource_profile"] == recovery["resource_profile"]
        and type(parameters["automatic_retries"]) is int and parameters["automatic_retries"] == 0
        and parameters["source_manifest_sha256"] == recovery["previous_source_manifest_sha256"],
        "approved ED recovery predecessor failure differs")
    old_marker = plan.canonical_journal.parent / recovery["previous_marker_name"]
    expected = wire.encode({"attempt": recovery["previous_attempt"], "state": "unresolved", "automatic_retries": 0})
    wire.require(bounded_read(old_marker, len(expected)) == expected,
                 "approved ED recovery predecessor marker bytes differ")
    return ed_recovery_proof()


def validate_ed_prior_snapshot(config: dict, plan: UnitPlan) -> dict | None:
    """Authenticate a completed prior unit's metadata; never open its body.

    Called after this unit's STARTED, before any derived input stat/transfer.
    Historical spans use their exact offsets so later journal records cannot
    turn a prior completed span into an unbounded tail read.
    """
    if config["invented_only"] or plan.input_contract != wire.ED_SNAPSHOT_CONTRACT:
        return None
    profile = profile_for_plan(plan)
    wire.require(plan.unit_id in ("ED-H1-RULES", "ED-H1-LIGHTGBM", "ED-H1-LIGHTGBM-ABLATION")
                 and len(plan.input_pins) == 1, "fixed derived ED prerequisite required")
    prior = plan.input_pins[0]
    path = Path(prior.original_path)
    wire.require(prior.name == "ED-SNAPSHOT" and path.name == "h1-snapshot.jsonl.gz",
                 "fixed prior snapshot filename differs")
    raw = bounded_read(path.parent / "publication.json", 65536)
    digest = wire.digest(raw)
    wire.require(digest == config["snapshot_actual_publication_sha256"]
                 and base64.b64decode(config["snapshot_actual_publication_b64"], validate=True) == raw
                 and config["resolved_snapshot"]["publication_sha256"] == digest,
                 "prior snapshot publication bytes differ")
    pub = json.loads(raw)
    wire.require(pub["unit_id"] == "ED-H1-SNAPSHOT" and pub["status"] == "completed"
                 and pub["invented_only"] is False and pub["full_bridge"] is True
                 and pub["local_pipe_test"] is False and pub["resource_profile"] == profile.id
                 and pub["input_contract"] == wire.ED_INPUTS_CONTRACT
                 and pub["geometry"] == canonical(plan.full_geometry)
                 and pub["verification_complete"] is True
                 and pub["output_sha256"] == prior.sha256 == config["resolved_snapshot"]["sha256"],
                 "completed full prior snapshot required")
    for name in ("source_hashes", "local_identity", "remote_identity"):
        wire.require(pub[name] == config[name], "prior snapshot source/runtime/host differs")
    verified = pub["independent_verification"]
    wire.require(verified.get("verified") is True and verified.get("full_geometry") is True
                 and verified.get("all_original_coordinates_verified") is True
                 and verified.get("sampled_scientific_rows") is False
                 and verified.get("decoded_sha256") == plan.decoded_stream_sha256
                 == config["resolved_snapshot"]["decoded_sha256"],
                 "prior snapshot independent verification differs")
    offsets = pub["canonical_offsets"]
    wire.require(type(offsets) is list and len(offsets) == 2, "prior snapshot span required")
    record, pin, finish = journal_record(plan.canonical_journal, offsets[0], "completed", pub["run_id"], end=offsets[1])
    _validate_ed_recovery_evidence(pub, record["parameters"], plan)
    wire.require(pin == pub["terminal_record_sha256"] and finish == offsets[1]
                 and record["study_id"] == "ED-H1-SNAPSHOT" and record["code_version"] == config["merge_commit"]
                 and record["specification_sha256"] == config["source_hashes"][plan.specification_path]
                 and record["window"] == canonical(plan.window)
                 and record["parameters"]["invented_only"] is False
                 and record["parameters"]["input_contract"] == wire.ED_INPUTS_CONTRACT
                 and record["parameters"]["resource_profile"] == profile.id
                 and record["parameters"]["source_manifest_sha256"] == wire.digest(wire.encode(config["source_hashes"]))
                 and record["result"]["output_sha256"] == pub["output_sha256"]
                 and record["result"]["output_bytes"] == pub["output_bytes"]
                 and record["result"]["verification_complete"] is True
                 and record["result"]["independent_verification"] == verified,
                 "prior snapshot canonical completion differs")
    return {"verified": True, "unit_id": "ED-H1-SNAPSHOT", "publication_sha256": digest,
            "output_sha256": prior.sha256, "run_id": pub["run_id"], "canonical_offsets": offsets,
            "terminal_record_sha256": pin, "snapshot_body_opened": False}


def _eh_snapshot_prior(plan: UnitPlan, invented_only: bool) -> dict:
    from research.intraday_scenario_forecast_handoff import validate_predecessor

    pin = _forecast_predecessor(plan, invented_only)
    path, members = validate_predecessor(plan.local_execution_root, pin, geometry=canonical(plan.full_geometry),
                                         window=canonical(plan.window))
    wire.require(str(path) == _eh_snapshot_member(plan, invented_only).original_path, "EH snapshot input path differs")
    return {"publication_sha256": pin.publication_sha256, "receipt_audit_sha256": pin.receipt_audit_sha256,
            "output_sha256": pin.output_sha256, "members": members}


def _eh_management_prior(plan: UnitPlan, invented_only: bool) -> dict:
    """Bind only receipt metadata for the snapshot and its original CC/CB inputs."""
    snapshot = _eh_snapshot_prior(plan, invented_only)
    output = Path(_eh_snapshot_member(plan, invented_only).original_path).parent
    raw = bounded_read(output / "publication.json", 65536)
    wire.require(wire.digest(raw) == snapshot["publication_sha256"], "EH management prior publication changed")
    proof = canonical(raw)["independent_verification"]
    manifest = proof["input_manifest"]
    if invented_only:
        manifest = [{**row, "original_path": str(output / "invented-inputs" / row["name"])} for row in manifest]
    prices = _eh_management_prices(plan, invented_only)
    wire.require(prices == {"manifest": manifest,
        "decoded_sha256": proof["raw_byte_audit"]["archives"]["CC"]["decoded_sha256"]},
        "EH management CB/CC differs from the same audited snapshot")
    return {**snapshot, "prices": prices}


def validate_qualification(config: dict, plan: UnitPlan) -> None:
    profile = profile_for_plan(plan)
    qualification = config["qualification"]
    path = Path(qualification["output"])
    wire.safe_path(path)
    wire.require(path.parent == plan.invented_output_parent
                 and path.name.startswith(plan.unit_id + "-invented-")
                 and path != plan.actual_output and not os.path.lexists(path / "unresolved.json"),
                 "qualification original output/marker scope differs")
    raw = bounded_read(path / "publication.json", 65536)
    wire.require(wire.digest(raw) == qualification["publication_sha256"], "qualification publication differs")
    publication = json.loads(raw)
    _validate_mode_metadata(publication, plan)
    wire.require(("input_contract" not in publication if plan.input_contract == wire.EC_INPUT_CONTRACT else
                  publication.get("input_contract") == plan.input_contract), "qualification input contract differs")
    for key in ("source_hashes", "local_identity", "remote_identity"):
        wire.require(publication[key] == config[key], "qualification source/private/runtime/host differs")
    wire.require(publication["resource_profile"] == profile.id and publication["unit_id"] == plan.unit_id
                 and publication["invented_only"] is True and publication["geometry"] == canonical(plan.full_geometry)
                 and publication["verification_complete"] is True
                 and publication["independent_verification"].get("verified") is True
                 and publication["status"] == "completed" and publication["full_bridge"] is True
                 and publication["local_pipe_test"] is False, "full same-source bridge qualification required")
    aggregate = _aggregate(publication["aggregate_budget"]["samples"], plan)
    if plan.local_coordinator:
        wire.require(aggregate == publication["aggregate_budget"], "local qualification aggregate differs")
        diagnostics = plan.invented_diagnostics_parent / path.name
        wire.safe_path(diagnostics)
        wire.require(not os.path.lexists(diagnostics / "process-failure.json"),
                     "local qualification controller reported failure")
        process_receipt = json.loads(bounded_read(diagnostics / "process-receipt.json", 65536))
        _validate_mode_metadata(process_receipt, plan)
        wire.require(process_receipt["status"] == "completed_ack_received"
            and process_receipt["terminal"] == {"kind": "COMPLETED", "status": "completed",
                "run_id": publication["run_id"], "output_sha256": publication["output_sha256"],
                "record_sha256": publication["terminal_record_sha256"],
                "publication_sha256": qualification["publication_sha256"]}
            and set(process_receipt["children"]) == {"worker", "coordinator"}
            and all(type(process_receipt["children"][role]["returncode"]) is int
                    and process_receipt["children"][role]["returncode"] == 0
                    and process_receipt["children"][role]["pid"] == aggregate["samples"][role]["pid"]
                    for role in ("worker", "coordinator"))
            and process_receipt["controller"]["pid"] == aggregate["samples"]["controller"]["pid"],
            "complete local child PID/exit/ACK qualification required")
        wire.validate_role_sample(process_receipt["controller"], "controller", profile=profile)
        if profile is wire.EF_LOCAL_SEQUENCE or plan.unit_id in _EH_BINDINGS:
            cache = plan.invented_cache_parent / path.name
            wire.require(not os.path.lexists(cache / "worker-failure.json"), "local qualification worker reported failure")
            wire.require(bounded_read(cache / "completed-ack.json", 65536) == wire.encode(process_receipt["terminal"])
                         and bounded_read(cache / "source-manifest.json", 65536) == wire.encode(config["source_hashes"]),
                         "local qualification original worker ACK/source differs")
            receipt = canonical(bounded_read(cache / "worker-budget.json", 65536))
            wire.require(receipt["sample"] == aggregate["samples"]["worker"], "local qualification worker sample differs")
            _validate_worker_budget(receipt["budget"], profile)
    _bounded_counts(publication["candidate_counts"], canonical(plan.candidate_bounds))
    eh_binding = _EH_BINDINGS.get(plan.unit_id)
    if eh_binding is not None:
        verification = publication["independent_verification"]
        wire.require(path.name == plan.unit_id + "-invented-" + EH_SUPPORT_QUALIFICATION_ATTEMPT
                     and publication["candidate_counts"] == canonical(plan.candidate_bounds)
                     and verification.get("schema") == eh_binding.schema
                     and verification.get("full_eof_verified") is True
                     and verification.get("all_original_coordinates_verified") is True
                     and verification.get("full_geometry") is True and verification.get("invented_only") is True,
                     "EH qualification must verify the full original population and EOF")
    wire.require(wire.file_digest(path / plan.output_filename, plan.output_max_bytes, lambda _: None)
                 == (publication["output_sha256"], publication["output_bytes"]), "qualification remote output differs")
    journal = path / "invented-experiments.jsonl"
    wire.require(publication["invented_journal"] == str(journal), "original invented journal required")
    record, pin, end = journal_record(journal, publication["canonical_offsets"][0], "completed", publication["run_id"])
    _validate_mode_metadata(record["parameters"], plan)
    if eh_binding is not None:
        wire.require(record["parameters"].get("attempt") == EH_SUPPORT_QUALIFICATION_ATTEMPT,
                     "EH qualification canonical attempt differs")
    _validate_ed_recovery_evidence(publication, record["parameters"], plan)
    wire.require(("input_contract" not in record["parameters"] if plan.input_contract == wire.EC_INPUT_CONTRACT else
                  record["parameters"].get("input_contract") == plan.input_contract),
                 "qualification canonical input contract differs")
    wire.require(pin == publication["terminal_record_sha256"] and end == publication["canonical_offsets"][1]
                 and record["result"]["output_sha256"] == publication["output_sha256"]
                 and record["result"]["output_bytes"] == publication["output_bytes"]
                 and record["result"]["verification_complete"] is True
                 and record["result"]["independent_verification"] == publication["independent_verification"]
                 and record["result"]["aggregate_budget"] == publication["aggregate_budget"]
                 and record["code_version"] == config["merge_commit"]
                 and record["specification_sha256"] == config["source_hashes"][plan.specification_path]
                 and record["study_id"] == plan.unit_id and record["window"] == canonical(plan.window)
                 and record["parameters"]["invented_only"] is True
                 and record["parameters"]["source_manifest_sha256"] == wire.digest(wire.encode(config["source_hashes"]))
                 and record["parameters"]["resource_profile"] == profile.id, "qualification canonical terminal differs")


def validate_ef_receipt_audit(config: dict, plan: UnitPlan) -> None:
    """Require the reviewed independent audit on direct actual role entry too."""
    qualification = config.get("qualification")
    expected = plan.invented_output_parent / (plan.unit_id + "-invented-qualification-v1")
    wire.require(type(qualification) is dict and set(qualification) == {
        "output", "publication_sha256", "audit_sha256"} and qualification["output"] == str(expected)
        and wire.valid_sha(qualification["publication_sha256"]) and wire.valid_sha(qualification["audit_sha256"]),
        "EF independently audited qualification is required")
    launches = plan.local_execution_root / "launches"
    if plan.unit_id != EF_UNIT_ID:
        launches /= plan.unit_id
    audit_path = launches / "qualification-v1/original-receipt-audit.json"
    wire.require(audit_path.is_file(), "EF original receipt audit is missing")
    raw = bounded_read(audit_path, 65536)
    wire.require(wire.digest(raw) == qualification["audit_sha256"], "EF original receipt audit hash differs")
    audit = canonical(raw)
    fields = {"status", "invented_only", "publication_sha256", "source_manifest_sha256", "receipt_sha256",
              "seal", "science_reexecuted", "auditor_source_sha256"}
    hashes = config["source_hashes"]
    auditor = EF_RECEIPT_AUDITOR if plan.unit_id == EF_UNIT_ID else EF_COMPARISON_AUDITOR
    seal_fields = ({"fingerprint", "features_sha256", "labels_sha256"} if plan.unit_id == EF_UNIT_ID else
                   {"artifact_sha256", "input_sha256"})
    publication_raw = bounded_read(expected / "publication.json", 65536)
    publication = canonical(publication_raw)
    receipt_raw = bounded_read(expected / plan.output_filename, plan.output_max_bytes)
    receipt = canonical(receipt_raw)
    if plan.unit_id != EF_UNIT_ID:
        wire.require(publication.get("candidate_counts") == canonical(plan.candidate_bounds),
                     "EF qualification must exercise every registered fit or report")
    wire.require(set(audit) == fields and audit["status"] == "PASS" and audit["invented_only"] is True
        and audit["science_reexecuted"] is False and wire.valid_sha(hashes.get(auditor))
        and audit["auditor_source_sha256"] == hashes[auditor]
        and audit["publication_sha256"] == qualification["publication_sha256"] == wire.digest(publication_raw)
        and audit["source_manifest_sha256"] == wire.digest(wire.encode(hashes))
        and publication["source_hashes"] == hashes and publication["invented_only"] is True
        and audit["receipt_sha256"] == publication["output_sha256"] == wire.digest(receipt_raw)
        and type(audit["seal"]) is dict and set(audit["seal"]) == seal_fields
        and all(wire.valid_sha(value) for value in audit["seal"].values()) and audit["seal"] == receipt["seal"],
        "EF original receipt audit binding differs")


def validate_eh_support_receipt_audit(config: dict, plan: UnitPlan) -> None:
    """Bind actual role admission to a separate audit of the exact invented receipts."""
    binding = _EH_BINDINGS.get(plan.unit_id)
    qualification = config.get("qualification")
    expected = plan.invented_output_parent / (plan.unit_id + "-invented-" + EH_SUPPORT_QUALIFICATION_ATTEMPT)
    wire.require(binding is not None and plan.actual_runner_ready is True
                 and config.get("invented_only") is False and config.get("attempt") == EH_SUPPORT_ATTEMPT
                 and type(qualification) is dict and set(qualification) == {
                     "output", "publication_sha256", "audit_sha256"}
                 and qualification["output"] == str(expected)
                 and wire.valid_sha(qualification["publication_sha256"])
                 and wire.valid_sha(qualification["audit_sha256"]),
                 "EH actual requires actual1 and the independently audited qualification")
    launches = plan.local_execution_root / "launches"
    if plan.unit_id != EH_SUPPORT_UNIT_ID:
        launches /= plan.unit_id
    audit_path = launches / EH_SUPPORT_QUALIFICATION_ATTEMPT / "original-receipt-audit.json"
    audit_raw = bounded_read(audit_path, 65536)
    wire.require(wire.digest(audit_raw) == qualification["audit_sha256"], "EH original receipt audit hash differs")
    publication_raw = bounded_read(expected / "publication.json", 65536)
    wire.require(wire.digest(publication_raw) == qualification["publication_sha256"],
                 "EH qualification publication hash differs")
    audit, publication = canonical(audit_raw), canonical(publication_raw)
    hashes = config["source_hashes"]
    auditor_sha = hashes.get(binding.auditor)
    _validate_mode_metadata(publication, plan)
    wire.require(wire.valid_sha(auditor_sha) and wire.valid_sha(publication.get("output_sha256"))
                 and publication.get("invented_only") is True and publication.get("unit_id") == plan.unit_id
                 and publication.get("source_hashes") == hashes
                 and publication.get("local_identity") == publication.get("remote_identity") == config["local_identity"]
                 and audit.get("invented_only") is True and audit.get("science_reexecuted") is False
                 and audit == {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS",
                     "invented_only": True, "science_reexecuted": False, "unit_id": plan.unit_id,
                     "attempt": EH_SUPPORT_QUALIFICATION_ATTEMPT, "publication_sha256": qualification["publication_sha256"],
                     "source_manifest_sha256": wire.digest(wire.encode(hashes)),
                     "output_sha256": publication["output_sha256"], "merge_commit": config["merge_commit"],
                     "tree": config["tree"], "runtime_sha256": config["local_identity"]["runtime_sha256"],
                     "auditor_source_sha256": auditor_sha}, "EH original receipt audit binding differs")


def _verify_eh_snapshot_failure(config: dict, plan: UnitPlan, root: Path) -> None:
    from research.intraday_scenario_snapshot_retry import verify_original_failure

    prior = bounded_read(root / EH_SNAPSHOT_RETRY_PRIOR, 65536)
    wire.require(wire.digest(prior) == config["source_hashes"][EH_SNAPSHOT_RETRY_PRIOR],
                 "EH prior snapshot failure pin changed")
    verify_original_failure(plan.local_execution_root, canonical(prior))


def _verify_eh_management_recovery(config: dict, plan: UnitPlan, root: Path, helpers: GitHelpers) -> None:
    """Authenticate fixed predecessor evidence before the new campaign marker."""
    from research import intraday_scenario_management_recovery as recovery

    _verify_eh_snapshot_failure(config, plan, root)
    recovery.verify_admission(plan.local_execution_root, root, plan.specification_path, config, helpers.git)


def coordinator(config: dict, plan: UnitPlan, root: Path, incoming: BinaryIO, outgoing: BinaryIO, *,
                unit_lock_fd: int | None = None) -> int:
    profile = profile_for_plan(plan)
    if plan.unit_id in _EH_BINDINGS:
        wire.require(unit_lock_fd is not None, "EH coordinator requires the lifecycle lock")
    _set_deadline(config, profile)
    wire.apply_role_limits("coordinator", profile=profile)
    helpers = GitHelpers(root, "coordinator", profile=profile, unit_lock_fd=unit_lock_fd)
    verify_sources(config, plan, root, "remote", helpers)
    out, log = Path(config["output"]), Path(config["journal"])
    wire.native_cache(out, root)
    wire.safe_path(log)
    if not config["invented_only"]:
        if plan.unit_id in _EH_BINDINGS:
            validate_eh_support_receipt_audit(config, plan)
        validate_qualification(config, plan)
        if profile is wire.EF_LOCAL_SEQUENCE:
            validate_ef_receipt_audit(config, plan)
    require_free_disk(out.parent)
    wire.exclusive_directory(out)
    wire.exclusive_bytes(out / "source-manifest.json", wire.encode(config["source_hashes"]))
    marker = out / "unresolved.json" if config["invented_only"] else plan.global_marker
    campaign_lock = "ee-local-campaign.lock" if plan.local_coordinator else "ec-campaign.lock"
    if plan.unit_id in _EH_BINDINGS:
        campaign_lock = EH_SUPPORT_CAMPAIGN_LOCK
    elif profile is wire.EF_LOCAL_SEQUENCE:
        campaign_lock = "ef-local-campaign.lock"
    locks = ([out.parent / "invented-research.lock", out.parent / campaign_lock]
             if config["invented_only"] else list(plan.research_locks))
    holder: dict = {}
    is_management_recovery = (not config["invented_only"]
                              and plan.global_marker.name == EH_MANAGEMENT_RECOVERY_MARKER)
    gate = ({"before_marker": lambda: _verify_eh_management_recovery(config, plan, root, helpers)}
            if is_management_recovery else {})
    with wire.research_locks(locks, marker, config["attempt"], **gate):
        if (not config["invented_only"] and not is_management_recovery
                and (plan.unit_id == EH_SNAPSHOT_RETRY_UNIT_ID or plan.input_contract == wire.EH_MANAGEMENT_INPUT_CONTRACT)):
            _verify_eh_snapshot_failure(config, plan, root)
        offset = wire.regular(log).st_size if log.exists() else 0
        def checkpoint(stage: str) -> None:
            _sample("coordinator", helpers, profile=profile)
        def evaluate() -> dict:
            started, pin, _ = journal_record(log, offset, "started")
            holder["run_id"] = started["run_id"]
            ack = {"kind": "STARTED", "identity": config["remote_identity"], "run_id": started["run_id"],
                   "record_sha256": pin, "offset": offset, "deadline_epoch": config["deadline_epoch"]}
            wire.send_header(outgoing, ack)
            phases = wire.WorkerPhases(config["remote_identity"])
            phases.accept_started(ack)
            if config["invented_only"]:
                envelope = InputEnvelope.from_payload(wire.read_header(incoming, "PROFILE_INPUTS"), "PROFILE_INPUTS",
                                                      input_contract=plan.input_contract)
                _invented_envelope(envelope, plan, config["sessions"])
                generated = out / "invented-inputs"
                wire.exclusive_directory(generated)
                for item in envelope.manifest:
                    wire.receive_file(incoming, generated / item.name, item.metadata(), checkpoint)
                export_paths = {item.name: generated / item.name for item in envelope.manifest}
            else:
                if plan.input_contract == wire.EH_FORECAST_INPUT_CONTRACT:
                    phases.input_access()
                    holder["prior_snapshot"] = _eh_snapshot_prior(plan, False)
                elif plan.input_contract == wire.EH_MANAGEMENT_INPUT_CONTRACT:
                    phases.input_access()
                    prior = _eh_management_prior(plan, False)
                    holder["prior_snapshot"] = {key: value for key, value in prior.items() if key != "prices"}
                else:
                    holder["prior_snapshot"] = validate_ed_prior_snapshot(config, plan)
                envelope = actual_input_metadata(plan, phases)
                if plan.input_contract in (wire.EH_FORECAST_INPUT_CONTRACT, wire.EH_MANAGEMENT_INPUT_CONTRACT):
                    _actual_envelope(envelope, plan)
                export_paths = {item.name: Path(item.original_path) for item in envelope.manifest}
            wire.send_header(outgoing, envelope.payload())
            for item in envelope.manifest:
                wire.send_file(outgoing, export_paths[item.name], item.metadata(), checkpoint)
            produced = wire.read_header(incoming, "RESULT")
            wire.require(set(produced) == {"kind", "result", "file"}, "result frame fields differ")
            result, output_file = produced["result"], produced["file"]
            validate_result(result, envelope, plan, invented_only=config["invented_only"])
            if holder.get("prior_snapshot") is not None:
                if plan.input_contract in (wire.EH_FORECAST_INPUT_CONTRACT, wire.EH_MANAGEMENT_INPUT_CONTRACT):
                    summary_key = ("forecast_summary" if plan.input_contract == wire.EH_FORECAST_INPUT_CONTRACT
                                   else "management_summary")
                    wire.require(result["independent_verification"][summary_key]["input_members"]
                                 == holder["prior_snapshot"]["members"], "EH forecast audited member seals differ")
                result["independent_verification"] = {**result["independent_verification"],
                    "coordinator_prior_snapshot": holder["prior_snapshot"]}
            expected_output = {"name": plan.output_filename, "original_path": str(Path(config["cache"]) / plan.output_filename),
                "sha256": result["output_sha256"], "size": result["output_bytes"], "maximum": plan.output_max_bytes}
            wire.require(output_file == expected_output, "registered output file metadata differs")
            wire.receive_file(incoming, out / plan.output_filename, output_file, checkpoint)
            done, control = wire.read_header(incoming, "WORKER_DONE"), wire.read_header(incoming, "CONTROL")
            for item in envelope.manifest:
                wire.require(wire.file_digest(export_paths[item.name], item.maximum, checkpoint)
                             == (item.sha256, item.size), "original export changed during compute")
            verify_sources(config, plan, root, "remote", helpers)
            expected_control = {"kind", "controller"} if plan.local_coordinator else {"kind", "controller", "ssh_client"}
            wire.require(set(control) == expected_control and control["kind"] == "CONTROL", "measured control roles differ")
            samples = {"worker": done["sample"], "coordinator": _sample("coordinator", helpers, profile=profile),
                       "controller": control["controller"]}
            if not plan.local_coordinator:
                samples["ssh_client"] = control["ssh_client"]
            aggregate = _aggregate(samples, plan)
            _validate_worker_budget(done["budget"], profile)
            holder.update(result=result, aggregate=aggregate)
            wire.exclusive_bytes(out / "prepared.json", wire.encode({"result": result, "aggregate_budget": aggregate}))
            return {"output_sha256": result["output_sha256"], "output_bytes": result["output_bytes"],
                    "verification_complete": True, "independent_verification": result["independent_verification"],
                    "aggregate_budget": aggregate, "automatic_retries": 0}
        try:
            holder["recovery_predecessor"] = validate_ed_recovery_predecessor(config, plan)
            # The controller authenticates the local Git merge before relaying
            # HELLO. The remote independently checks its own merge above. A
            # worker has no extra Git subprocess reservation of its own.
            wire.require(wire.read_header(incoming, "HELLO") == {"kind": "HELLO", "identity": config["local_identity"]},
                         "worker source/host/runtime differs")
            from research import experiment_log
            record = experiment_log.run_discovery_trial(study_id=plan.unit_id,
                specification_sha256=config["source_hashes"][plan.specification_path],
                parameters={"invented_only": config["invented_only"], "execution_host": execution_host(plan),
                    "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
                    "attempt": config["attempt"], "resource_profile": profile.id, "automatic_retries": 0,
                    **_ed_recovery_parameters(plan),
                    **_mode_metadata(plan),
                    **({"input_contract": plan.input_contract} if plan.input_contract != wire.EC_INPUT_CONTRACT else {})},
                window=canonical(plan.window), evaluate=evaluate, runs_path=log,
                code_version_provider=lambda: helpers.git("rev-parse", "HEAD"))
            terminal, pin, end = journal_record(log, offset, "completed", record["run_id"])
            publication = {"status": "completed", "unit_id": plan.unit_id, "resource_profile": profile.id,
                "invented_only": config["invented_only"], "full_bridge": not config.get("local_pipe_test", False),
                "local_pipe_test": config.get("local_pipe_test", False), "source_hashes": config["source_hashes"],
                "local_identity": config["local_identity"], "remote_identity": config["remote_identity"],
                "run_id": record["run_id"], "canonical_offsets": [offset, end], "terminal_record_sha256": pin,
                "aggregate_budget": holder["aggregate"], "coordinator_after_terminal": _sample("coordinator", helpers, profile=profile),
                "invented_journal": str(log) if config["invented_only"] else None, **holder["result"],
                **_mode_metadata(plan),
                **({"recovery_predecessor": holder["recovery_predecessor"]}
                   if holder["recovery_predecessor"] is not None else {}),
                **({"input_contract": plan.input_contract} if plan.input_contract != wire.EC_INPUT_CONTRACT else {})}
            wire.exclusive_bytes(out / "publication.json", wire.encode(publication))
            publication_sha = wire.digest(wire.encode(publication))
            wire.send_header(outgoing, {"kind": "COMPLETED", "status": "completed", "run_id": record["run_id"],
                "output_sha256": holder["result"]["output_sha256"], "record_sha256": pin,
                "publication_sha256": publication_sha})
            wire.require(wire.read_header(incoming, "ACK_ACCEPTED") == {"kind": "ACK_ACCEPTED", "run_id": record["run_id"],
                "record_sha256": pin, "publication_sha256": publication_sha}, "completion acceptance lost; marker retained")
            wire.clear_terminal_marker(marker, config["attempt"], terminal)
            return 0
        except BaseException as error:
            failure = {"status": "unknown", "error_type": type(error).__name__, "automatic_retries": 0,
                       "partial_evidence_preserved": True, "unresolved_marker": str(marker), **_mode_metadata(plan)}
            try:
                terminal, pin, _ = journal_record(log, offset, "failed", holder.get("run_id"))
                failure.update(status="failed", terminal_record_sha256=pin)
            except BaseException:
                pass
            wire.exclusive_bytes(out / "bridge-failure.json", wire.encode(failure))
            # Preserve the original diagnostic before traceback formatting can
            # allocate under the coordinator cap or the controller observes EOF.
            try:
                sys.stderr.write(f"{type(error).__name__}: {error}\n")
                sys.stderr.flush()
            except (OSError, MemoryError, ValueError):
                pass
            raise


def source_probe(config: dict, plan: UnitPlan, root: Path, outgoing: BinaryIO) -> int:
    """Cold source/journal imports only: no input, output, journal or marker IO."""
    profile = profile_for_plan(plan)
    wire.fixed_deadline(config["deadline_epoch"])
    wire.apply_role_limits("coordinator", profile=profile)
    helpers = GitHelpers(root, "coordinator", profile=profile)
    verified = verify_sources(config, plan, root, "remote", helpers)
    from research import experiment_log

    wire.write_all(outgoing, wire.encode({"status": "coordinator_source_probe_complete", "study_id": plan.unit_id,
        "identity": verified, "source_files": len(plan.source_names), "role_sample": _sample("coordinator", helpers, profile=profile),
        "journal_module_imported": experiment_log.__name__ == "research.experiment_log",
        "market_input_accesses": 0, "journal_accesses": 0, "output_directories_created": 0,
        "marker_accesses": 0, "qualifies_actual": False, **_mode_metadata(plan)}))
    outgoing.flush()
    return 0


def sample_child(pid: int, *, profile: wire.ResourceProfile = PROFILE) -> dict:
    role = "ssh_client"
    caps = profile.role_limits()[role]
    base = Path("/proc") / str(pid)
    fields = (base / "stat").read_text().split(") ", 1)[1].split()
    limits = (base / "limits").read_text().splitlines()
    def limit(label: str) -> list[int]:
        row = next(line for line in limits if line.startswith(label)).split()
        return [int(row[-3]), int(row[-2])]
    result = {"role": role, "pid": pid, "resource_profile": profile.id, "limits": caps, "parent_limits": caps,
        "cpu_self_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
        "children_cpu_diagnostic_seconds": 0, "cpu_counting": "/proc dedicated SSH pid; server daemon excluded",
        "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "address_space_bytes": int((base / "statm").read_text().split()[0]) * os.sysconf("SC_PAGE_SIZE"),
        "readback": {"rlimit_cpu": limit("Max cpu time"), "rlimit_as": limit("Max address space"),
            "nice": os.getpriority(os.PRIO_PROCESS, pid), "affinity": sorted(os.sched_getaffinity(pid)),
            "oom_score_adj": (base / "oom_score_adj").read_text().strip()}}
    wire.validate_role_sample(result, role, profile=profile)
    return result


def process_command(mode: str, config: dict, plan: UnitPlan, root: Path) -> list[str]:
    flags = (["-B", "-X", "pycache_prefix=/dev/null"]
             if plan.resource_profile == wire.EF_LOCAL_SEQUENCE.id or plan.unit_id in _EH_BINDINGS else [])
    return [str(Path(sys.executable).resolve()), "-I", "-S", *flags, str(root / plan.private_entrypoint), mode,
            base64.b64encode(wire.encode(config)).decode()]


def remote_command(config: dict, plan: UnitPlan) -> list[str]:
    wire.require(not plan.local_coordinator, "all-local mode forbids remote_command/SSH")
    import shlex

    options = ["-F", "/dev/null", "-T", "-o", "ControlMaster=no", "-o", "ControlPath=none",
               "-o", "ClearAllForwardings=yes", "-o", "ProxyCommand=none", "-o", "ProxyJump=none",
               "-o", "IdentityAgent=none", "-o", "ForwardAgent=no", "-o", "PermitLocalCommand=no",
               "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
    command = [*plan.remote_prefix, base64.b64encode(wire.encode(config)).decode()]
    return [plan.ssh_argv[0], *options, *plan.ssh_argv[1:], shlex.join(command)]


def relay_frame(source: BinaryIO, target: BinaryIO, header: dict, plan: UnitPlan) -> None:
    wire.require(header.get("kind") in {"HELLO", "STARTED", "PROFILE_INPUTS", "INPUTS", "FILE", "RESULT",
                                       "WORKER_DONE", "COMPLETED", "ACK_ACCEPTED"}, "unregistered relay phase")
    if header["kind"] == "FILE":
        maxima = {pin.name: pin.maximum for pin in plan.input_pins}
        maxima[plan.output_filename] = plan.output_max_bytes
        size = header["size"]
        wire.require(set(header) == {"kind", "name", "original_path", "sha256", "size", "maximum"}
                     and header["name"] in maxima and header["maximum"] == maxima[header["name"]]
                     and wire.valid_sha(header["sha256"]) and type(size) is int and 0 < size <= header["maximum"],
                     "registered relay binary bound differs")
    wire.send_header(target, header)
    if header["kind"] == "FILE":
        while size:
            block = wire.exact_read(source, min(size, wire.BLOCK))
            size -= len(block)
            wire.write_all(target, block)
        target.flush()


def _terminate(process: subprocess.Popen, statuses: dict, name: str, *, immediate: bool = False,
               eof_grace: float = 0) -> None:
    if process.returncode is not None:
        return
    signals = (signal.SIGKILL,) if immediate else (signal.SIGTERM, signal.SIGKILL)
    for sig in (None, *signals) if eof_grace else signals:
        if sig is not None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        until = time.monotonic() + (eof_grace if sig is None else 2)
        while time.monotonic() < until:
            child, status, usage = os.wait4(process.pid, os.WNOHANG)
            if child:
                process.returncode = os.waitstatus_to_exitcode(status)
                statuses[name] = {"returncode": process.returncode,
                    "cpu_rusage_seconds": usage.ru_utime + usage.ru_stime,
                    "peak_rss_kib": usage.ru_maxrss, "measurement": "failure wait4 diagnostic only"}
                return
            time.sleep(0.01)
    statuses[name] = {"status": "termination_sent_final_usage_unknown"}


def controller(config: dict, plan: UnitPlan, root: Path, *, unit_lock_fd: int | None = None) -> int:
    # Child caps must be set before exec; tightening this parent before fork
    # would make the worker's larger inherited hard AS impossible to install.
    profile = profile_for_plan(plan)
    _set_deadline(config, profile)
    if plan.unit_id in _EH_BINDINGS and not config["invented_only"]:
        validate_eh_support_receipt_audit(config, plan)
    options = {}
    if profile is wire.EF_LOCAL_SEQUENCE:
        wire.require(unit_lock_fd is not None, "EF controller requires the lifecycle lock")
        options = {"pass_fds": (unit_lock_fd,), "env": {**EF_ENV, EF_LOCK_ENV: str(unit_lock_fd)}}
    if plan.unit_id in _EH_BINDINGS:
        wire.require(unit_lock_fd is not None, "EH controller requires the lifecycle lock")
        options = {"pass_fds": (unit_lock_fd,), "env": {**EF_ENV, EH_SUPPORT_LOCK_ENV: str(unit_lock_fd)}}
    diagnostic = Path(config["diagnostics"])
    wire.native_cache(diagnostic, root)
    require_free_disk(diagnostic.parent)
    wire.exclusive_directory(diagnostic)
    helpers = GitHelpers(root, "controller", profile=profile, unit_lock_fd=unit_lock_fd)
    processes, statuses, stderr_handles = {}, {}, []
    coordinator_name = "coordinator" if plan.local_coordinator else "ssh"
    worker_eof = False
    try:
        for name in ("worker", coordinator_name):
            fd = os.open(diagnostic / (name + "-stderr.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            stderr_handles.append(os.fdopen(fd, "wb"))
        processes["worker"] = subprocess.Popen(process_command("worker", config, plan, root), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=stderr_handles[0], preexec_fn=lambda: wire.apply_role_limits("worker", profile=profile),
            start_new_session=True, bufsize=0, **options)
        if plan.local_coordinator:
            processes[coordinator_name] = subprocess.Popen(process_command("coordinator", config, plan, root), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=stderr_handles[1], preexec_fn=lambda: wire.apply_role_limits("coordinator", profile=profile),
                start_new_session=True, bufsize=0, **options)
        elif config.get("local_pipe_test", False):
            wire.require(config["invented_only"] is True, "local pipe is invented only")
            processes["ssh"] = subprocess.Popen(process_command("coordinator", config, plan, root), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=stderr_handles[1], preexec_fn=lambda: wire.apply_role_limits("coordinator", profile=profile),
                start_new_session=True, bufsize=0)
        else:
            processes["ssh"] = subprocess.Popen(remote_command(config, plan), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=stderr_handles[1], preexec_fn=lambda: wire.apply_role_limits("ssh_client", profile=profile),
                start_new_session=True, bufsize=0)
        wire.apply_role_limits("controller", profile=profile)
        verify_sources(config, plan, root, "local", helpers)
        worker_process, remote_process = processes["worker"], processes[coordinator_name]
        active = {worker_process.stdout: remote_process.stdin, remote_process.stdout: worker_process.stdin}
        received_terminal = accepted = None
        while active:
            ready, _, _ = select.select(list(active), [], [], min(1, max(0, config["deadline_epoch"] - time.time())))
            for source in ready:
                try:
                    header = wire.read_header(source)
                except wire.EndOfFrames:
                    try:
                        active[source].close()
                    except (BrokenPipeError, OSError):
                        pass
                    worker_eof = source is worker_process.stdout and (received_terminal is None or accepted is None)
                    wire.require(received_terminal is not None and accepted is not None,
                                 "bridge disconnect before complete acceptance; no retry")
                    del active[source]
                    continue
                relay_frame(source, active[source], header, plan)
                if source is worker_process.stdout and header["kind"] == "WORKER_DONE":
                    verify_sources(config, plan, root, "local", helpers)
                    control = {"kind": "CONTROL", "controller": _sample("controller", helpers, profile=profile)}
                    if not plan.local_coordinator:
                        # The legacy local pipe remains invented-only smoke.
                        ssh = (config["test_ssh_sample"] if config.get("local_pipe_test", False) else
                               sample_child(remote_process.pid, profile=profile))
                        wire.validate_role_sample(ssh, "ssh_client", profile=profile)
                        control["ssh_client"] = ssh
                    wire.send_header(remote_process.stdin, control)
                if source is remote_process.stdout and header["kind"] == "COMPLETED":
                    received_terminal = header
                if source is worker_process.stdout and header["kind"] == "ACK_ACCEPTED":
                    wire.require(received_terminal is not None and header == {"kind": "ACK_ACCEPTED",
                        "run_id": received_terminal["run_id"], "record_sha256": received_terminal["record_sha256"],
                        "publication_sha256": received_terminal["publication_sha256"]}, "forwarded acceptance differs")
                    accepted = header
        for name, process in processes.items():
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            statuses[name] = {"returncode": process.returncode, "cpu_self_seconds": usage.ru_utime + usage.ru_stime,
                "peak_rss_kib": usage.ru_maxrss, "measurement": "wait4 dedicated child/descendants; diagnostic only",
                **({"pid": process.pid} if plan.local_coordinator else {})}
        wire.require(received_terminal is not None and accepted is not None
                     and all(item["returncode"] == 0 for item in statuses.values()),
                     "whole bridge child exit/terminal incomplete; outcome unknown")
        wire.exclusive_bytes(diagnostic / "process-receipt.json", wire.encode({"status": "completed_ack_received",
            "terminal": received_terminal, "children": statuses, "controller": _sample("controller", helpers, profile=profile),
            "canonical_truth": ("local WSL journal only" if plan.local_coordinator else "GCP journal only"),
            "automatic_retries": 0, **_mode_metadata(plan)}))
        return 0
    except BaseException as error:
        immediate = profile is wire.EF_LOCAL_SEQUENCE and time.time() >= config["deadline_epoch"]
        for name, process in processes.items():
            # The coordinator already received EOF. Give its failure journal a
            # bounded chance to finish before signalling the coordinator itself.
            grace = (min(2, max(0, config["deadline_epoch"] - time.time()))
                     if worker_eof and name == coordinator_name else 0)
            _terminate(process, statuses, name, immediate=immediate, eof_grace=grace)
        wire.exclusive_bytes(diagnostic / "process-failure.json", wire.encode({"status": "unknown",
            "error_type": type(error).__name__, "automatic_retries": 0, "children": statuses,
            "canonical_truth": ("local WSL journal only; inspect without replay" if plan.local_coordinator else
                                "GCP journal only; inspect remotely without replay"), **_mode_metadata(plan)}))
        raise
    finally:
        for process in processes.values():
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        for handle in stderr_handles:
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()


@contextmanager
def _local_unit_lock(mode: str, plan: UnitPlan, *, name: str, environment_key: str, campaign: str):
    """Keep one lease across the controller, its roles and the last Git helper."""
    import fcntl

    path = plan.local_execution_root / name
    wire.safe_path(path)
    if mode == "controller":
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    else:
        inherited = os.environ.get(environment_key, "")
        wire.require(inherited.isdecimal() and int(inherited) > 2,
                     f"{campaign} child requires an inherited lifecycle lock")
        fd = int(inherited)
    try:
        state = wire.regular(path)
        descriptor = os.fstat(fd)
        wire.require((state.st_dev, state.st_ino) == (descriptor.st_dev, descriptor.st_ino)
                     and state.st_uid == os.getuid() and stat.S_IMODE(state.st_mode) == 0o600,
                     f"{campaign} lifecycle lock identity differs")
        if mode != "controller" and campaign == "EH":
            # An open but unlocked descriptor cannot start a standalone child.
            probe = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise ValueError("EH child requires an already held lifecycle lock")
            finally:
                os.close(probe)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        # An inherited open-file description preserves the lease until the last
        # admitted role/helper exits, including after controller interruption.
        os.close(fd)


def _ef_unit_lock(mode: str, plan: UnitPlan):
    return _local_unit_lock(mode, plan, name="ef-local-unit.lock", environment_key=EF_LOCK_ENV, campaign="EF")


def run_role(mode: str, config: dict, hooks: UnitHooks, *, root: Path,
             incoming: BinaryIO | None = None, outgoing: BinaryIO | None = None) -> int:
    """Thin private entrypoint API; apply role caps before loading its plan.

    The controller bootstrap creates its two children before tightening itself;
    the source-only same-PID launcher authenticates it before invoking this API.
    The private module must not import producer/fixture code at import time.
    """
    wire.require(mode in ("worker", "coordinator", "controller", "source-probe"), "explicit ordinary bridge role required")
    profile = _bootstrap_profile(config)
    _set_deadline(config, profile)
    ef_local = profile is wire.EF_LOCAL_SEQUENCE
    eh_local = type(config.get("unit_id")) is str and config["unit_id"] in _EH_BINDINGS
    if eh_local:
        wire.require(mode != "source-probe" and "microsoft" in os.uname().release.lower(),
                     "EH support requires the local WSL lifecycle")
        restrict_local_network()
    if ef_local:
        wire.require(mode != "source-probe" and "microsoft" in os.uname().release.lower(),
                     "EF snapshot requires the local WSL lifecycle")
        os.environ.update(EF_ENV)
        restrict_local_network()
    if ef_local and mode == "controller":
        wire.prepare_ef_controller()
    elif mode != "controller":
        wire.apply_role_limits("coordinator" if mode == "source-probe" else mode, profile=profile)
    if mode == "worker" and not ef_local:
        restrict_worker_network()
    plan = hooks.load_plan(config)
    validate_config(config, plan)
    if plan.local_coordinator and not (ef_local or eh_local):
        restrict_local_network()
    wire.require(profile_for_plan(plan) is profile, "bootstrap/plan resource profile differs")
    incoming = incoming if incoming is not None else sys.stdin.buffer
    outgoing = outgoing if outgoing is not None else sys.stdout.buffer
    if ef_local or eh_local:
        wire.require(root == plan.local_worktree, "registered local source checkout differs")
        lease = (_local_unit_lock(mode, plan, name=EH_SUPPORT_UNIT_LOCK, environment_key=EH_SUPPORT_LOCK_ENV,
                                  campaign="EH") if eh_local else _ef_unit_lock(mode, plan))
        with lease as fd:
            if mode == "controller":
                if ef_local and not config["invented_only"]:
                    validate_ef_receipt_audit(config, plan)
                return controller(config, plan, root, unit_lock_fd=fd)
            if mode == "coordinator":
                return coordinator(config, plan, root, incoming, outgoing, unit_lock_fd=fd)
            return worker(config, plan, hooks, root, incoming, outgoing)
    if mode == "worker":
        return worker(config, plan, hooks, root, incoming, outgoing)
    if mode == "coordinator":
        return coordinator(config, plan, root, incoming, outgoing)
    if mode == "source-probe":
        return source_probe(config, plan, root, outgoing)
    return controller(config, plan, root)
