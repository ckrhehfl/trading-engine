"""Admit fixed metadata for costed EI comparisons without opening market bytes."""

from dataclasses import dataclass
from pathlib import Path
import stat

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_economic_compute import INPUT_BOUNDS
from research.intraday_context_forecast_predecessor import validate_snapshot_predecessor
from research.intraday_context_support_unit import FULL, original_inputs


SCHEMA = "EI-economic-audited-forecast-predecessor-v1"
_MODES = {"qualification": "qualification-v1", "actual": "actual1"}


@dataclass(frozen=True)
class EconomicInputSet:
    files: tuple[bridge.InputFile, ...]
    snapshot_summary: dict
    decoded: dict


def _same(value, expected, reason):
    wire.require(wire.encode(value) == wire.encode(expected), "EI economic predecessor " + reason)


def validate_economic_predecessor(pin: dict, native: Path, original_registration: bytes,
                                  checkpoint) -> dict[str, EconomicInputSet]:
    """Verify prior launch, audited publication, journal span and sealed paths.

    The caller authenticates the registration with these exact hashes and
    holds both research locks. All bodies are read by the new worker only
    after the new durable STARTED.
    """
    wire.require(type(pin) is dict and set(pin) == {"schema", "scenario", "snapshot", "forecast_release", "attempts"}
                 and pin["schema"] == SCHEMA and pin["scenario"] in ("S1", "S2")
                 and set(pin["attempts"]) == set(_MODES) and callable(checkpoint),
                 "closed EI economic predecessor registration required")
    release = pin["forecast_release"]
    wire.require(type(release) is dict and set(release) == {"merge_commit", "reviewed_commit", "tree", "registration_sha256"}
                 and wire.valid_sha(release["registration_sha256"])
                 and all(type(release[key]) is str and len(release[key]) == 40
                         and all(char in "0123456789abcdef" for char in release[key])
                         for key in ("merge_commit", "reviewed_commit", "tree")), "EI forecast release pin differs")
    original_pins, expected_decoded = original_inputs(original_registration)
    snapshots = validate_snapshot_predecessor(pin["snapshot"], native, original_pins, checkpoint)
    identity = {key: release[key] for key in ("merge_commit", "reviewed_commit", "tree")}
    unit = "EI-FORECAST-" + pin["scenario"]
    spec = f"configs/research/discovery/intraday-context-forecast-{pin['scenario'].lower()}-v1.json"
    recorded = {}

    def read(path, sha):
        checkpoint("ei_economic_predecessor_metadata")
        raw = bridge.bounded_read(path, 65536)
        wire.require(wire.valid_sha(sha) and wire.digest(raw) == sha, "EI economic predecessor metadata changed")
        return bridge.canonical(raw)

    for mode, attempt in _MODES.items():
        q = mode == "qualification"
        refs = pin["attempts"][mode]
        wire.require(type(refs) is dict and set(refs) == {"launch_sha256", "publication_sha256", "audit_sha256"},
                     "EI economic forecast attempt pin fields differ")
        leaf = f"{unit}-{'invented-' if q else ''}{attempt}"
        output, launch_dir = native / "output" / leaf, native / "launches" / unit / attempt
        launch = read(launch_dir / "launch.json", refs["launch_sha256"])
        pub = read(output / "publication.json", refs["publication_sha256"])
        audit = read(launch_dir / "original-receipt-audit.json", refs["audit_sha256"])
        _same({key: launch[key] for key in identity}, identity, "reviewed forecast release")
        expected = {"invented_only": q, "unit_id": unit, "resource_profile": wire.EI_LOCAL_MODEL.id}
        _same({key: launch[key] for key in expected}, expected, "original forecast launch mode")
        _same({key: pub[key] for key in expected}, expected, "original forecast publication mode")
        wire.require(launch["attempt"] == attempt and launch["automatic_retries"] == 0
                     and launch["source_hashes"][spec] == release["registration_sha256"]
                     and launch["source_hashes"] == pub["source_hashes"] and launch["local_identity"] == pub["local_identity"]
                     and pub["status"] == "completed" and pub["input_contract"] == wire.EI_FORECAST_INPUT_CONTRACT
                     and pub["verification_complete"] is True, "EI economic original forecast lifecycle differs")
        if not q:
            orig_q = pin["attempts"]["qualification"]
            _same(launch["qualification"], {"output": str(native / "output" / f"{unit}-invented-qualification-v1"),
                  **{key: orig_q[key] for key in ("publication_sha256", "audit_sha256")}}, "forecast qualification dependency")
        source = launch["local_identity"]
        _same(audit, {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": q,
            "science_reexecuted": False, "unit_id": unit, "attempt": attempt,
            "publication_sha256": refs["publication_sha256"], "output_sha256": pub["output_sha256"],
            "source_manifest_sha256": source["source_manifest_sha256"], "runtime_sha256": source["runtime_sha256"],
            "merge_commit": identity["merge_commit"], "tree": identity["tree"],
            "auditor_source_sha256": launch["source_hashes"]["var/ei-forecast-receipt-audit-v1.py"]}, "forecast audit")
        journal = output / "invented-experiments.jsonl" if q else native / "canonical" / "experiments.jsonl"
        start, end = pub["canonical_offsets"]
        terminal, terminal_sha, _ = bridge.journal_record(journal, start, "completed", pub["run_id"], end=end)
        wire.require(terminal_sha == pub["terminal_record_sha256"] and terminal["study_id"] == unit
                     and terminal["code_version"] == identity["merge_commit"] and terminal["promotion_allowed"] is False
                     and terminal["result"]["output_sha256"] == pub["output_sha256"], "EI forecast durable completion differs")
        prior = snapshots[mode]
        proof = pub["independent_verification"]
        _same(proof["input_manifest"], [item.metadata() for item in prior.inputs], "forecast snapshot input")
        wire.require(proof["snapshot_summary_sha256"] == wire.digest(wire.encode(prior.summary))
                     and proof["contains_economic_returns"] is False, "EI forecast original snapshot identity differs")

        snapshot_leaf = f"EI-SNAPSHOT-1-{'invented-' if q else ''}{attempt}"
        original = read(native / "output" / snapshot_leaf / "publication.json", prior.publication_sha256)
        raw_proof = original["independent_verification"]
        manifests = raw_proof["input_manifest"]
        files = tuple(bridge.InputFile(row["name"], str(native / "cache" / snapshot_leaf / "received" / row["name"]),
                                      row["sha256"], row["maximum"], row["size"]) for row in manifests)
        archives = raw_proof["raw_byte_audit"]["archives"]
        decoded = {key: archives[key]["decoded_sha256"] for key in ("CC", "CX")}
        wire.require((q or decoded == expected_decoded) and files[0] == prior.inputs[0]
                     and pub["geometry"] == FULL, "EI economic original decoded stream or population differs")
        forecast = bridge.InputFile("EI-FORECAST", str(output / "ei-context-forecast.bundle"),
                                    pub["output_sha256"], INPUT_BOUNDS[-1][1], pub["output_bytes"])
        combined = (*files, prior.inputs[1], forecast)
        _same([(row.name, row.maximum) for row in combined], INPUT_BOUNDS, "fixed economic input roster")
        for row in combined:
            info = wire.regular(Path(row.original_path))
            wire.require(type(row.size) is int and 0 < row.size <= row.maximum and wire.valid_sha(row.sha256)
                         and info.st_size == row.size and stat.S_IMODE(info.st_mode) == 0o400,
                         "EI economic sealed predecessor size or mode differs")
        recorded[mode] = EconomicInputSet(combined, prior.summary, decoded)
    return recorded
