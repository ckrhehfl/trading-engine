"""Bounded stdio transport for one separately registered cross-host research unit.

No hosts, evidence paths, input pins, account calculations or journal writes live
here. Static hard-limit partitions bound concurrent address spaces even between
samples. Per-role SELF CPU is counted once; CHILDREN is diagnostic only, except
for explicitly constrained coordinator helpers charged to its remaining CPU.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import stat
import struct
import time
from typing import BinaryIO, Callable, Iterator


MIB = 1024**2
ROLE_LIMITS = {
    "worker": {"cpu_seconds": 480, "address_space_bytes": 352 * MIB},
    "coordinator": {"cpu_seconds": 60, "address_space_bytes": 64 * MIB},
    "controller": {"cpu_seconds": 40, "address_space_bytes": 64 * MIB},
    "ssh_client": {"cpu_seconds": 20, "address_space_bytes": 32 * MIB},
}
# Fixed subdivisions retain room for sequential source-control helpers. A
# helper never inherits a fresh whole-role allowance or the worker's budget.
HELPER_LIMITS = {
    "coordinator": {"cpu_seconds": 10, "address_space_bytes": 32 * MIB},
    "controller": {"cpu_seconds": 5, "address_space_bytes": 32 * MIB},
}
HEADER_MAX = 16384
BLOCK = 65536
MAGIC = b"ECWSL3\x00\x01"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def encode(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False, ensure_ascii=False) + "\n").encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def valid_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def exact_read(stream: BinaryIO, size: int) -> bytes:
    require(type(size) is int and 0 <= size <= BLOCK, "invalid bounded read")
    parts = bytearray()
    while len(parts) < size:
        block = stream.read(size - len(parts))
        if not block:
            raise EOFError("incomplete bridge frame; no retry or resume")
        parts.extend(block)
    return bytes(parts)


def write_all(stream: BinaryIO, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = stream.write(view)
        require(type(written) is int and written > 0, "incomplete bridge write")
        view = view[written:]


def send_header(stream: BinaryIO, value: dict) -> None:
    raw = encode(value)
    require(len(raw) <= HEADER_MAX, "bridge header exceeded")
    write_all(stream, MAGIC + struct.pack(">I", len(raw)) + raw)
    stream.flush()


def read_header(stream: BinaryIO, kind: str | None = None) -> dict:
    require(exact_read(stream, len(MAGIC)) == MAGIC, "bridge magic differs")
    size = struct.unpack(">I", exact_read(stream, 4))[0]
    require(0 < size <= HEADER_MAX, "bridge header exceeded")
    raw = exact_read(stream, size)
    value = json.loads(raw)
    require(isinstance(value, dict) and encode(value) == raw, "noncanonical bridge header")
    require(kind is None or value.get("kind") == kind, "unexpected bridge phase")
    return value


def safe_path(path: Path) -> None:
    require(path.is_absolute() and ".." not in path.parts, "absolute unaliased path required")
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), "symlink rejected")


def regular(path: Path) -> os.stat_result:
    safe_path(path)
    result = path.stat()
    require(stat.S_ISREG(result.st_mode) and result.st_nlink == 1, "exclusive regular file required")
    return result


def file_identity(value: os.stat_result) -> tuple:
    # atime may legitimately advance when a pinned file is read.
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exclusive_directory(path: Path) -> None:
    safe_path(path)
    require(path.parent.is_dir(), "existing dedicated parent required")
    path.mkdir(mode=0o700)
    require(stat.S_IMODE(path.stat().st_mode) == 0o700, "private directory permissions differ")
    fsync_directory(path.parent)


def native_cache(path: Path, worktree: Path) -> None:
    safe_path(path)
    safe_path(worktree)
    require(path != worktree and worktree not in path.parents
            and not str(path).startswith("/mnt/"), "native cache outside worktree required")
    for parent in path.parents:
        require(not (parent / ".git").exists(), "cache inside a worktree")


def exclusive_bytes(path: Path, raw: bytes) -> None:
    safe_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        write_all(stream, raw)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def file_digest(path: Path, maximum: int, checkpoint: Callable[[str], object]) -> tuple[str, int]:
    before = regular(path)
    require(before.st_size <= maximum, "file size exceeded")
    hashed, size = hashlib.sha256(), 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        require(file_identity(os.fstat(stream.fileno())) == file_identity(before), "file changed before open")
        while block := stream.read(BLOCK):
            checkpoint("file_hash")
            size += len(block)
            require(size <= maximum, "file size exceeded")
            hashed.update(block)
        after = os.fstat(stream.fileno())
    require(file_identity(before) == file_identity(after) == file_identity(regular(path))
            and size == before.st_size, "file changed while hashing")
    return hashed.hexdigest(), size


def validate_input_manifest(manifest: object) -> list[dict]:
    require(isinstance(manifest, list) and len(manifest) == 3, "exactly three registered inputs required")
    require([item.get("name") for item in manifest] == ["EA-A", "EA-B", "CC"], "input allowlist differs")
    for item in manifest:
        require(set(item) == {"name", "original_path", "sha256", "size", "maximum"}
                and valid_sha(item["sha256"]), "input metadata differs")
        safe_path(Path(item["original_path"]))
        maximum = 512 * MIB if item["name"] == "CC" else 64 * MIB
        require(type(item["maximum"]) is int and item["maximum"] == maximum
                and type(item["size"]) is int and 0 < item["size"] <= maximum,
                "input bound differs")
    require(len({item["original_path"] for item in manifest}) == 3, "duplicate input paths")
    return manifest


def send_file(stream: BinaryIO, path: Path, metadata: dict,
              checkpoint: Callable[[str], object]) -> None:
    pin, size = file_digest(path, metadata["maximum"], checkpoint)
    require((pin, size) == (metadata["sha256"], metadata["size"]), "source pin differs")
    before = regular(path)
    send_header(stream, {"kind": "FILE", **metadata})
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    copied, hashed = 0, hashlib.sha256()
    with os.fdopen(descriptor, "rb") as source:
        require(file_identity(os.fstat(source.fileno())) == file_identity(before), "source changed before export")
        while block := source.read(min(BLOCK, size - copied)):
            checkpoint("export")
            copied += len(block)
            hashed.update(block)
            write_all(stream, block)
        require(source.read(1) == b"" and file_identity(os.fstat(source.fileno())) == file_identity(before), "export source changed")
    stream.flush()
    require(copied == size and hashed.hexdigest() == pin
            and file_identity(regular(path)) == file_identity(before),
            "export bytes changed")
    require(file_digest(path, metadata["maximum"], checkpoint) == (pin, size), "post-export pin differs")


def receive_file(stream: BinaryIO, path: Path, expected: dict,
                 checkpoint: Callable[[str], object]) -> None:
    require(read_header(stream, "FILE") == {"kind": "FILE", **expected}, "file frame metadata differs")
    safe_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    hashed, remaining = hashlib.sha256(), expected["size"]
    require(type(remaining) is int and 0 <= remaining <= expected["maximum"], "receive bound exceeded")
    with os.fdopen(descriptor, "wb") as target:
        while remaining:
            checkpoint("receive")
            block = exact_read(stream, min(BLOCK, remaining))
            remaining -= len(block)
            hashed.update(block)
            write_all(target, block)
        target.flush()
        os.fchmod(target.fileno(), 0o400)
        os.fsync(target.fileno())
    fsync_directory(path.parent)
    require(hashed.hexdigest() == expected["sha256"], "received digest differs")
    require(file_digest(path, expected["maximum"], checkpoint)
            == (expected["sha256"], expected["size"]), "stored digest differs")


def validate_role_limits(limits: dict) -> None:
    require(limits == ROLE_LIMITS, "registered role limits differ")
    require(sum(v["cpu_seconds"] for v in limits.values()) == 600
            and sum(v["address_space_bytes"] for v in limits.values()) == 512 * MIB,
            "shared role totals differ")


def apply_role_limits(role: str) -> None:
    """Run in each child preexec, before its parent tightens its own AS cap."""
    caps = parent_limits(role)
    resource.setrlimit(resource.RLIMIT_CPU, (caps["cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (caps["address_space_bytes"],) * 2)
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    Path("/proc/self/oom_score_adj").write_text("1000")


def parent_limits(role: str) -> dict:
    return {key: value - HELPER_LIMITS.get(role, {}).get(key, 0)
            for key, value in ROLE_LIMITS[role].items()}


def role_sample(role: str, helpers: dict | None = None) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "role": role, "pid": os.getpid(), "limits": ROLE_LIMITS[role],
        "parent_limits": parent_limits(role),
        "helpers": helpers or {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "cpu_self_seconds": usage.ru_utime + usage.ru_stime,
        "children_cpu_diagnostic_seconds": children.ru_utime + children.ru_stime,
        "cpu_counting": "RUSAGE_SELF only; children assigned to their own role",
        "address_space_bytes": int(Path("/proc/self/statm").read_text().split()[0]) * os.sysconf("SC_PAGE_SIZE"),
        "readback": {"rlimit_cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
                     "rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
                     "nice": os.getpriority(os.PRIO_PROCESS, 0),
                     "affinity": sorted(os.sched_getaffinity(0)),
                     "oom_score_adj": Path("/proc/self/oom_score_adj").read_text().strip()},
    }


def validate_role_sample(sample: dict, role: str) -> None:
    caps = ROLE_LIMITS[role]
    parent = parent_limits(role)
    require(sample["role"] == role and sample["limits"] == caps, "role identity differs")
    readback = sample["readback"]
    require(sample["parent_limits"] == parent
            and readback["rlimit_cpu"] == [parent["cpu_seconds"]] * 2
            and readback["rlimit_as"] == [parent["address_space_bytes"]] * 2
            and readback["nice"] == 10 and readback["oom_score_adj"] == "1000"
            and len(readback["affinity"]) == 1, "role hard-limit readback differs")
    require(type(sample["cpu_self_seconds"]) in (int, float)
            and math.isfinite(sample["cpu_self_seconds"])
            and 0 <= sample["cpu_self_seconds"] < parent["cpu_seconds"]
            and 0 < sample["address_space_bytes"] <= parent["address_space_bytes"], "role resource exhausted")
    helpers = sample["helpers"]
    require(0 <= helpers["cpu_seconds"] <= HELPER_LIMITS.get(role, {}).get("cpu_seconds", 0),
            "helper cumulative CPU exceeded")
    for readback in helpers["readbacks"]:
        require(role in HELPER_LIMITS and 0 < readback["cpu_seconds"] <= HELPER_LIMITS[role]["cpu_seconds"]
                and readback["address_space_bytes"] == HELPER_LIMITS[role]["address_space_bytes"],
                "helper sublimits differ")


def aggregate_roles(samples: dict) -> dict:
    require(set(samples) == set(ROLE_LIMITS), "all four measured roles required")
    for role, sample in samples.items():
        validate_role_sample(sample, role)
    return {"role_limits": ROLE_LIMITS, "samples": samples,
            "cpu_seconds_counted_once": sum(item["cpu_self_seconds"] + item["helpers"]["cpu_seconds"]
                                           for item in samples.values()),
            "hard_cpu_sum_seconds": 600, "hard_address_space_sum_bytes": 512 * MIB,
            "measurement_cutoff": "worker finished; other dedicated roles sampled before canonical terminal",
            "ssh_server_daemon_measured": False, "automatic_retries": 0}


def fixed_deadline(deadline: float) -> None:
    remaining = deadline - time.time()
    require(math.isfinite(deadline) and 0 < remaining <= 1200, "fixed remote wall deadline expired or differs")
    def expire(*_: object) -> None:
        raise TimeoutError("fixed cross-host wall deadline")
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextmanager
def research_locks(paths: list[Path], marker: Path, attempt: str) -> Iterator[None]:
    """An orphan marker blocks a later run even after a dead process lost flock."""
    import fcntl
    handles = []
    try:
        for path in paths:
            safe_path(path)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            handle = os.fdopen(descriptor, "a+b")
            handles.append(handle)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        exclusive_bytes(marker, encode({"attempt": attempt, "state": "unresolved", "automatic_retries": 0}))
        yield
    finally:
        for handle in reversed(handles):
            handle.close()


def clear_terminal_marker(marker: Path, attempt: str, terminal: dict) -> None:
    require(terminal.get("status") == "completed", "durable completion required; failure marker stays for audit")
    require(json.loads(marker.read_bytes())["attempt"] == attempt, "active marker differs")
    marker.unlink()
    fsync_directory(marker.parent)


class WorkerPhases:
    """ACK gates are independent of input parsing and never permit replay."""
    def __init__(self, expected_identity: dict) -> None:
        self.expected_identity = expected_identity
        self.started: dict | None = None
        self.completed = False

    def accept_started(self, ack: dict) -> None:
        require(self.started is None and ack.get("kind") == "STARTED"
                and ack.get("identity") == self.expected_identity
                and valid_sha(ack.get("record_sha256")) and bool(ack.get("run_id")),
                "durable started ACK or source/host/runtime differs")
        self.started = ack

    def input_access(self) -> None:
        require(self.started is not None and not self.completed, "input access before durable started ACK")

    def accept_completed(self, ack: dict, accounts_sha: str) -> None:
        require(self.started is not None and not self.completed
                and ack.get("kind") == "COMPLETED" and ack.get("status") == "completed"
                and ack.get("run_id") == self.started["run_id"]
                and ack.get("accounts_sha256") == accounts_sha
                and valid_sha(ack.get("record_sha256")), "missing durable terminal ACK; outcome unknown")
        self.completed = True
