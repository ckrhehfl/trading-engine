"""Invented-only protocol/resource boundaries; no host or market-data access."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import select
import struct

import pytest

from research import intraday_campaign_wsl_transport as wire


def noop(stage):
    return None


def metadata(path, raw=b"invented\n", name="EA-A"):
    return {"name": name, "original_path": str(path), "sha256": wire.digest(raw),
            "size": len(raw), "maximum": (512 if name == "CC" else 64) * wire.MIB}


def sample(role):
    caps = wire.ROLE_LIMITS[role]
    parent = wire.parent_limits(role)
    return {"role": role, "pid": 123, "limits": caps, "parent_limits": parent,
        "cpu_self_seconds": 1.0, "children_cpu_diagnostic_seconds": 400.0,
        "address_space_bytes": 8 * wire.MIB, "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "readback": {"rlimit_cpu": [parent["cpu_seconds"]] * 2,
            "rlimit_as": [parent["address_space_bytes"]] * 2, "nice": 10,
            "affinity": [0], "oom_score_adj": "1000"}}


def test_partial_reads_and_writes_roundtrip():
    class Partial(BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 3))
        def write(self, value):
            return super().write(value[:2])
    stream = Partial()
    wire.send_header(stream, {"kind": "TEST", "value": "합성"})
    stream.seek(0)
    assert wire.read_header(stream, "TEST")["value"] == "합성"


def test_two_headers_in_one_pipe_write_remain_visible_to_select():
    frames = BytesIO()
    wire.send_header(frames, {"kind": "FIRST"})
    wire.send_header(frames, {"kind": "SECOND"})
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, frames.getvalue())
        with os.fdopen(read_fd, "rb", buffering=0) as stream:
            read_fd = None
            assert wire.read_header(stream)["kind"] == "FIRST"
            assert select.select([stream], [], [], 0)[0] == [stream]
            assert wire.read_header(stream)["kind"] == "SECOND"
    finally:
        os.close(write_fd)
        if read_fd is not None:
            os.close(read_fd)


@pytest.mark.parametrize("raw", [b"", b"badmagic", wire.MAGIC + struct.pack(">I", wire.HEADER_MAX + 1),
    wire.MAGIC + struct.pack(">I", 3) + b"{}\n"[:-1],
    wire.MAGIC + struct.pack(">I", 3) + b"[]\n"])
def test_corrupt_or_incomplete_header_rejected(raw):
    with pytest.raises((ValueError, EOFError)):
        wire.read_header(BytesIO(raw))


def test_binary_file_roundtrip_and_readonly_exclusive_destination(tmp_path):
    source, target = tmp_path / "source", tmp_path / "received"
    raw = b"\x00\xffinvented\n" * 10000
    source.write_bytes(raw)
    info = metadata(source, raw)
    stream = BytesIO()
    wire.send_file(stream, source, info, noop)
    stream.seek(0)
    wire.receive_file(stream, target, info, noop)
    assert target.read_bytes() == raw
    assert target.stat().st_mode & 0o777 == 0o400
    stream.seek(0)
    with pytest.raises(FileExistsError):
        wire.receive_file(stream, target, info, noop)


@pytest.mark.parametrize("failure", ["digest", "metadata", "truncate"])
def test_corrupted_file_never_accepted_and_partial_is_preserved(tmp_path, failure):
    source, target = tmp_path / "source", tmp_path / "received"
    raw = b"invented\n"
    source.write_bytes(raw)
    info = metadata(source, raw)
    stream = BytesIO()
    wire.send_file(stream, source, info, noop)
    encoded = stream.getvalue()
    if failure == "digest":
        encoded = encoded[:-1] + b"!"
    elif failure == "metadata":
        info = {**info, "sha256": "f" * 64}
    else:
        encoded = encoded[:-2]
    with pytest.raises((ValueError, EOFError)):
        wire.receive_file(BytesIO(encoded), target, info, noop)
    if failure != "metadata":
        assert target.exists()


def test_source_mutation_before_send_rejected(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"invented\n")
    info = metadata(path)
    path.write_bytes(b"changed!\n")
    with pytest.raises(ValueError, match="pin differs"):
        wire.send_file(BytesIO(), path, info, noop)


def test_symlink_and_hardlink_are_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"invented\n")
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        wire.file_digest(alias, 100, noop)
    alias.unlink()
    alias.hardlink_to(source)
    with pytest.raises(ValueError, match="exclusive regular"):
        wire.file_digest(source, 100, noop)


def test_native_cache_rejects_worktree_and_mounted_windows_paths(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").write_text("invented")
    for cache in (root / "cache", Path("/mnt/c/invented/cache")):
        with pytest.raises(ValueError):
            wire.native_cache(cache, root)
    wire.native_cache(tmp_path / "native-cache", root)


def test_input_allowlist_is_three_fixed_files(tmp_path):
    inputs = [metadata(tmp_path / name, name=name) for name in ("EA-A", "EA-B", "CC")]
    assert wire.validate_input_manifest(inputs) == inputs
    for mutation in (inputs[:2], inputs + [inputs[0]], list(reversed(inputs))):
        with pytest.raises(ValueError):
            wire.validate_input_manifest(mutation)
    inputs[2]["maximum"] += 1
    with pytest.raises(ValueError):
        wire.validate_input_manifest(inputs)


def test_foreign_provenance_paths_do_not_access_receiver_filesystem(monkeypatch):
    inputs = [metadata(Path("/invented-foreign-origin") / name, name=name)
              for name in ("EA-A", "EA-B", "CC")]
    attempts = []
    def inaccessible(path, *args, **kwargs):
        attempts.append(str(path))
        raise PermissionError("invented foreign filesystem is inaccessible")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", inaccessible)
        patch.setattr(Path, "lstat", inaccessible)
        assert wire.validate_input_manifest(inputs) is inputs
    assert attempts == []


@pytest.mark.parametrize("label", ["relative/EA-A", "", "/foreign/../EA-A", "/foreign/\x00EA-A",
                                    None, 123, b"/foreign/EA-A", "C:\\foreign\\EA-A"])
def test_foreign_provenance_path_requires_unaliased_absolute_posix_text(label):
    inputs = [metadata(Path("/invented-foreign-origin") / name, name=name)
              for name in ("EA-A", "EA-B", "CC")]
    inputs[0]["original_path"] = label
    with pytest.raises(ValueError, match="provenance path"):
        wire.validate_input_manifest(inputs)


@pytest.mark.parametrize("alias", ["source", "source_hardlink", "destination", "destination_parent"])
def test_actual_transfer_keeps_local_source_and_destination_alias_guards(tmp_path, alias):
    raw = b"invented binary payload\x00\xff\n"
    source = tmp_path / "source"
    source.write_bytes(raw)
    info = metadata(Path("/invented-foreign-origin/EA-A"), raw)
    linked = tmp_path / "linked"
    if alias in ("source", "destination"):
        linked.symlink_to(source)
    elif alias == "source_hardlink":
        linked.hardlink_to(source)
    else:
        linked.symlink_to(tmp_path, target_is_directory=True)
    stream = BytesIO()
    if alias.startswith("source"):
        with pytest.raises(ValueError, match="symlink|exclusive regular"):
            wire.send_file(stream, linked, info, noop)
    else:
        wire.send_file(stream, source, info, noop)
        stream.seek(0)
        destination = linked if alias == "destination" else linked / "received"
        with pytest.raises(ValueError, match="symlink"):
            wire.receive_file(stream, destination, info, noop)
    assert source.read_bytes() == raw


def test_started_ack_precedes_every_input_access_and_cannot_repeat():
    phases = wire.WorkerPhases({"runtime": "invented"})
    with pytest.raises(ValueError, match="before durable"):
        phases.input_access()
    ack = {"kind": "STARTED", "identity": {"runtime": "invented"}, "run_id": "invented", "record_sha256": "a" * 64}
    phases.accept_started(ack)
    phases.input_access()
    with pytest.raises(ValueError):
        phases.accept_started(ack)


@pytest.mark.parametrize("field", ["host", "runtime", "source"])
def test_identity_mismatch_before_ack_rejects_input(field):
    expected = {"host": "a", "runtime": "b", "source": "c"}
    phases = wire.WorkerPhases(expected)
    changed = {**expected, field: "changed"}
    with pytest.raises(ValueError, match="source/host/runtime"):
        phases.accept_started({"kind": "STARTED", "identity": changed, "run_id": "invented", "record_sha256": "a" * 64})
    with pytest.raises(ValueError):
        phases.input_access()


def test_missing_or_wrong_terminal_ack_stays_unknown():
    phases = wire.WorkerPhases({})
    phases.accept_started({"kind": "STARTED", "identity": {}, "run_id": "invented", "record_sha256": "a" * 64})
    for ack in ({}, {"kind": "COMPLETED", "status": "completed", "run_id": "wrong"}):
        with pytest.raises(ValueError, match="outcome unknown"):
            phases.accept_completed(ack, "b" * 64)
    assert not phases.completed
    phases.accept_completed({"kind": "COMPLETED", "status": "completed", "run_id": "invented",
        "record_sha256": "c" * 64, "accounts_sha256": "b" * 64}, "b" * 64)
    assert phases.completed


def test_duplicate_and_orphan_marker_block_after_lock_release(tmp_path):
    locks = [tmp_path / "research.lock", tmp_path / "campaign.lock"]
    marker = tmp_path / "unresolved.json"
    with wire.research_locks(locks, marker, "invented-1"):
        with pytest.raises(BlockingIOError):
            with wire.research_locks(locks, marker, "invented-2"):
                pytest.fail("duplicate research execution")
    assert json.loads(marker.read_bytes())["state"] == "unresolved"
    with pytest.raises(FileExistsError):
        with wire.research_locks(locks, marker, "invented-3"):
            pytest.fail("orphan accepted")
    with pytest.raises(ValueError):
        wire.clear_terminal_marker(marker, "invented-1", {"status": "started"})
    with pytest.raises(ValueError, match="failure marker stays"):
        wire.clear_terminal_marker(marker, "invented-1", {"status": "failed"})
    assert marker.exists()
    wire.clear_terminal_marker(marker, "invented-1", {"status": "completed"})
    assert not marker.exists()


def test_static_role_total_and_children_are_not_counted_twice():
    wire.validate_role_limits(deepcopy(wire.ROLE_LIMITS))
    samples = {role: sample(role) for role in wire.ROLE_LIMITS}
    samples["coordinator"]["helpers"] = {"cpu_seconds": 2, "count": 1,
        "readbacks": [{"cpu_seconds": 10, "address_space_bytes": 32 * wire.MIB}]}
    aggregate = wire.aggregate_roles(samples)
    assert aggregate["cpu_seconds_counted_once"] == 6
    assert aggregate["hard_cpu_sum_seconds"] == 600
    assert aggregate["hard_address_space_sum_bytes"] == 512 * wire.MIB
    assert aggregate["ssh_server_daemon_measured"] is False
    for role in wire.HELPER_LIMITS:
        for key in ("cpu_seconds", "address_space_bytes"):
            assert wire.parent_limits(role)[key] + wire.HELPER_LIMITS[role][key] == wire.ROLE_LIMITS[role][key]


@pytest.mark.parametrize("mutation", ["role", "cpu", "AS", "nice", "affinity", "oom", "helpers", "missing"])
def test_incomplete_or_drifted_budget_rejected(mutation):
    samples = {role: sample(role) for role in wire.ROLE_LIMITS}
    changed = samples["controller"]
    if mutation == "role":
        changed["role"] = "worker"
    elif mutation == "cpu":
        changed["cpu_self_seconds"] = 35
    elif mutation == "AS":
        changed["readback"]["rlimit_as"] = [512 * wire.MIB] * 2
    elif mutation == "nice":
        changed["readback"]["nice"] = 0
    elif mutation == "affinity":
        changed["readback"]["affinity"] = [0, 1]
    elif mutation == "oom":
        changed["readback"]["oom_score_adj"] = "0"
    elif mutation == "helpers":
        changed["helpers"]["cpu_seconds"] = 6
    else:
        del samples["ssh_client"]
    with pytest.raises(ValueError):
        wire.aggregate_roles(samples)


def test_deadline_is_absolute_and_cannot_reset(monkeypatch):
    alarms = []
    monkeypatch.setattr(wire.time, "time", lambda: 1000)
    monkeypatch.setattr(wire.signal, "signal", lambda *args: None)
    monkeypatch.setattr(wire.signal, "setitimer", lambda *args: alarms.append(args))
    wire.fixed_deadline(1300)
    assert alarms[-1][1] == 300
    for deadline in (999, 2201, float("inf")):
        with pytest.raises(ValueError):
            wire.fixed_deadline(deadline)
