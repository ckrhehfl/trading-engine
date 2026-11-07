"""Read-only proof of the spent EH snapshot attempt before its one retry."""

import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire


ORIGINAL_UNIT = "EH-SNAPSHOT-1"
ORIGINAL_ATTEMPT = "actual1"
_MAX_LAUNCH = 65_536
_MAX_JOURNAL_SPAN = 32_768


def _bounded_read(path: Path, maximum: int, start: int = 0, end: int | None = None) -> bytes:
    before = wire.regular(path)
    end = before.st_size if end is None else end
    wire.require(0 <= start < end <= before.st_size and end - start <= maximum,
                 "original EH evidence span differs")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before),
                     "original EH evidence changed before open")
        stream.seek(start)
        raw = stream.read(end - start)
        after = os.fstat(stream.fileno())
    wire.require(len(raw) == end - start and wire.file_identity(before) == wire.file_identity(after)
                 == wire.file_identity(wire.regular(path)), "original EH evidence changed while reading")
    return raw


def _span(value: object) -> tuple[int, int]:
    wire.require(type(value) is dict and set(value) == {"offsets", "sha256"}
                 and wire.valid_sha(value["sha256"]), "original EH journal pin differs")
    offsets = value["offsets"]
    wire.require(type(offsets) is list and len(offsets) == 2 and all(type(n) is int for n in offsets)
                 and 0 <= offsets[0] < offsets[1], "original EH journal offsets differ")
    return offsets[0], offsets[1]


def _record(raw: bytes, expected_sha: str, status: str, run_id: str) -> dict:
    wire.require(raw.endswith(b"\n") and raw.count(b"\n") == 1 and wire.digest(raw) == expected_sha,
                 "original EH journal record hash or line differs")
    record = json.loads(raw)
    wire.require(type(record) is dict and (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode() == raw
                 and record.get("record_type") == "discovery_trial" and record.get("study_id") == ORIGINAL_UNIT
                 and record.get("status") == status and record.get("run_id") == run_id,
                 "original EH journal transition differs")
    return record


def verify_original_failure(root: Path, pin: dict) -> None:
    """Authenticate only original control evidence and require no original publication."""
    wire.require(type(pin) is dict and set(pin) == {"run_id", "launch_sha256", "marker_sha256", "started", "failed"}
                 and type(pin["run_id"]) is str and bool(pin["run_id"])
                 and wire.valid_sha(pin["launch_sha256"]) and wire.valid_sha(pin["marker_sha256"]),
                 "original EH failure pin differs")
    start, split = _span(pin["started"])
    failed_start, end = _span(pin["failed"])
    wire.require(split == failed_start, "original EH journal spans must be adjacent")

    canonical = root / "canonical"
    journal = canonical / "experiments.jsonl"
    original_output = root / "output" / f"{ORIGINAL_UNIT}-{ORIGINAL_ATTEMPT}"
    expected_marker = wire.encode({"attempt": ORIGINAL_ATTEMPT, "state": "unresolved", "automatic_retries": 0})
    marker = _bounded_read(canonical / "eh-support-unresolved.json", len(expected_marker))
    wire.require(marker == expected_marker and wire.digest(marker) == pin["marker_sha256"],
                 "original EH unresolved marker differs")

    launch_path = root / "launches" / ORIGINAL_UNIT / ORIGINAL_ATTEMPT / "launch.json"
    launch_raw = _bounded_read(launch_path, _MAX_LAUNCH)
    wire.require(wire.digest(launch_raw) == pin["launch_sha256"], "original EH launch hash differs")
    launch = json.loads(launch_raw)
    wire.require(type(launch) is dict and wire.encode(launch) == launch_raw
                 and launch.get("unit_id") == ORIGINAL_UNIT and launch.get("attempt") == ORIGINAL_ATTEMPT
                 and launch.get("invented_only") is False and launch.get("local_coordinator") is True
                 and launch.get("execution_host") == "local_wsl_only"
                 and launch.get("resource_profile") == wire.ORDINARY300.id
                 and type(launch.get("automatic_retries")) is int and launch["automatic_retries"] == 0
                 and launch.get("local_execution_root") == str(root)
                 and launch.get("output") == str(original_output) and launch.get("journal") == str(journal)
                 and type(launch.get("source_hashes")) is dict,
                 "original EH launch scope differs")

    raw = _bounded_read(journal, _MAX_JOURNAL_SPAN, start, end)
    started = _record(raw[:split - start], pin["started"]["sha256"], "started", pin["run_id"])
    failed = _record(raw[split - start:], pin["failed"]["sha256"], "failed", pin["run_id"])
    expected_parameters = {
        "attempt": ORIGINAL_ATTEMPT,
        "invented_only": False,
        "local_coordinator": True,
        "execution_host": launch["execution_host"],
        "resource_profile": launch["resource_profile"],
        "automatic_retries": 0,
        "local_execution_root": str(root),
        "source_manifest_sha256": wire.digest(wire.encode(launch["source_hashes"])),
        "input_contract": wire.EH_SUPPORT_INPUT_CONTRACT,
    }
    wire.require(wire.encode(started.get("parameters")) == wire.encode(expected_parameters)
                 and started.get("code_version") == launch.get("merge_commit")
                 and failed == {**started, "status": "failed", "error_type": failed.get("error_type"),
                                "logged_at": failed.get("logged_at")}
                 and type(failed.get("error_type")) is str and bool(failed["error_type"]),
                 "original EH failed record scope differs")

    publication = original_output / "publication.json"
    wire.safe_path(publication)
    wire.require(original_output.is_dir(), "original EH failure output directory missing")
    try:
        publication.lstat()
    except FileNotFoundError:
        return
    raise ValueError("original EH publication must be absent")
