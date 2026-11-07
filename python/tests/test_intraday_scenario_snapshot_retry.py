import json
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_transport as wire
from research.intraday_scenario_snapshot_retry import verify_original_failure


def _line(record: dict) -> bytes:
    return (json.dumps(record, sort_keys=True) + "\n").encode()


@pytest.fixture
def failed_attempt(tmp_path: Path):
    canonical = tmp_path / "canonical"
    output = tmp_path / "output" / "EH-SNAPSHOT-1-actual1"
    launch = tmp_path / "launches" / "EH-SNAPSHOT-1" / "actual1" / "launch.json"
    for directory in (canonical, output, launch.parent):
        directory.mkdir(parents=True)

    journal = canonical / "experiments.jsonl"
    marker = canonical / "eh-support-unresolved.json"
    marker.write_bytes(wire.encode({"attempt": "actual1", "state": "unresolved", "automatic_retries": 0}))
    parameters = {"attempt": "actual1", "automatic_retries": 0, "invented_only": False,
                  "resource_profile": wire.ORDINARY300.id, "execution_host": "local_wsl_only",
                  "local_execution_root": str(tmp_path), "local_coordinator": True}
    source_hashes = {"example.py": "b" * 64}
    launch.write_bytes(wire.encode({**parameters, "unit_id": "EH-SNAPSHOT-1", "output": str(output),
                                   "journal": str(journal), "merge_commit": "c" * 40,
                                   "source_hashes": source_hashes}))
    started = {"record_type": "discovery_trial", "study_id": "EH-SNAPSHOT-1", "status": "started",
               "run_id": "00000000-0000-4000-8000-000000000001", "code_version": "c" * 40,
               "parameters": {**parameters, "source_manifest_sha256": wire.digest(wire.encode(source_hashes)),
                              "input_contract": wire.EH_SUPPORT_INPUT_CONTRACT},
               "logged_at": "2026-01-01T00:00:00+00:00"}
    failed = {**started, "status": "failed", "error_type": "SyntheticError",
              "logged_at": "2026-01-01T00:00:01+00:00"}
    prefix = _line({"example": "prior unrelated record"})
    started_line, failed_line = _line(started), _line(failed)
    split = len(prefix) + len(started_line)
    journal.write_bytes(prefix + started_line + failed_line)
    pin = {
        "run_id": started["run_id"], "marker_sha256": wire.digest(marker.read_bytes()),
        "launch_sha256": wire.digest(launch.read_bytes()),
        "started": {"offsets": [len(prefix), split], "sha256": wire.digest(started_line)},
        "failed": {"offsets": [split, split + len(failed_line)], "sha256": wire.digest(failed_line)},
    }
    return tmp_path, output, launch, marker, journal, pin


def test_original_failure_accepts_later_append_and_leaves_evidence_intact(failed_attempt):
    root, output, launch, marker, journal, pin = failed_attempt
    with journal.open("ab") as stream:
        stream.write(_line({"example": "later journal record"}) * 2000)
    paths = list(root.rglob("*"))
    before = {path: path.read_bytes() for path in paths if path.is_file()}

    verify_original_failure(root, pin)

    assert sorted(root.rglob("*")) == sorted(paths)
    assert {path: path.read_bytes() for path in before} == before
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("target,match", [("launch", "launch hash"), ("marker", "marker differs"),
                                         ("started", "record hash"), ("failed", "record hash")])
def test_original_failure_rejects_tampered_bytes(failed_attempt, target, match):
    root, _, launch, marker, journal, pin = failed_attempt
    path = {"launch": launch, "marker": marker}.get(target, journal)
    raw = bytearray(path.read_bytes())
    offset = pin[target]["offsets"][0] if path == journal else 0
    raw[offset] = ord("!")
    path.write_bytes(raw)

    with pytest.raises(ValueError, match=match):
        verify_original_failure(root, pin)


@pytest.mark.parametrize("field,value", [("status", "completed"), ("study_id", "EH-OTHER"),
                                        ("run_id", "other-run"), ("record_type", "other")])
def test_original_failure_rejects_wrong_terminal_even_when_repinned(failed_attempt, field, value):
    root, _, _, _, journal, pin = failed_attempt
    start, end = pin["failed"]["offsets"]
    raw = journal.read_bytes()
    record = json.loads(raw[start:end])
    record[field] = value
    changed = _line(record)
    journal.write_bytes(raw[:start] + changed)
    pin["failed"] = {"offsets": [start, start + len(changed)], "sha256": wire.digest(changed)}

    with pytest.raises(ValueError, match="journal transition differs"):
        verify_original_failure(root, pin)


def test_original_failure_rejects_wrong_launch_scope_even_when_repinned(failed_attempt):
    root, _, launch, _, _, pin = failed_attempt
    altered = wire.encode({**json.loads(launch.read_bytes()), "invented_only": True})
    launch.write_bytes(altered)
    pin["launch_sha256"] = wire.digest(altered)

    with pytest.raises(ValueError, match="launch scope differs"):
        verify_original_failure(root, pin)


def test_original_failure_rejects_nonadjacent_journal_spans(failed_attempt):
    root, _, _, _, _, pin = failed_attempt
    pin["started"]["offsets"][1] -= 1

    with pytest.raises(ValueError, match="spans must be adjacent"):
        verify_original_failure(root, pin)


@pytest.mark.parametrize("as_link", [False, True])
def test_original_failure_rejects_any_original_publication_entry(failed_attempt, as_link):
    root, output, _, _, _, pin = failed_attempt
    publication = output / "publication.json"
    if as_link:
        publication.symlink_to(output / "absent-file")
    else:
        publication.write_bytes(wire.encode({"status": "completed"}))

    with pytest.raises(ValueError, match="publication must be absent|symlink rejected"):
        verify_original_failure(root, pin)
