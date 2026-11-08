"""Bounded stdio transport for one separately registered cross-host research unit.

No hosts, evidence paths, input pins, account calculations or journal writes live
here. Static hard-limit partitions bound concurrent address spaces even between
samples. Per-role SELF CPU is counted once; CHILDREN is diagnostic only, except
for explicitly constrained coordinator helpers charged to its remaining CPU.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import stat
import struct
import time
from typing import BinaryIO, Callable, Iterator

# CPython 3.12's hashlib fallback computes the same SHA-256 without mapping
# OpenSSL into bridge roles that have a small address-space ceiling.
try:
    from _sha2 import sha256 as _sha256
except ImportError:
    from hashlib import sha256 as _sha256

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


@dataclass(frozen=True)
class ResourceProfile:
    """Registered immutable ceilings, never a caller-supplied resource grant."""
    id: str
    roles: tuple[tuple[str, int, int], ...]
    helpers: tuple[tuple[str, int, int], ...]
    cpu_seconds: int
    address_space_bytes: int = 512 * MIB
    wall_seconds: int = 1200

    def role_limits(self) -> dict:
        return {role: {"cpu_seconds": cpu, "address_space_bytes": space}
                for role, cpu, space in self.roles}

    def helper_limits(self) -> dict:
        return {role: {"cpu_seconds": cpu, "address_space_bytes": space}
                for role, cpu, space in self.helpers}


CORE600 = ResourceProfile("ec-core600-v1", tuple(
    (role, caps["cpu_seconds"], caps["address_space_bytes"])
    for role, caps in ROLE_LIMITS.items()), tuple(
    (role, caps["cpu_seconds"], caps["address_space_bytes"])
    for role, caps in HELPER_LIMITS.items()), 600)
ORDINARY300 = ResourceProfile("ec-ordinary300-v1", (
    ("worker", 180, 352 * MIB), ("coordinator", 60, 64 * MIB),
    ("controller", 40, 64 * MIB), ("ssh_client", 20, 32 * MIB)),
    CORE600.helpers, 300)
ED_H1_600 = ResourceProfile("ed-h1-cpu600-v1", CORE600.roles, CORE600.helpers, 600)
ED_H1_600_AS520 = ResourceProfile("ed-h1-cpu600-as520-v1", (
    ("worker", 480, 352 * MIB), ("coordinator", 60, 72 * MIB),
    ("controller", 40, 64 * MIB), ("ssh_client", 20, 32 * MIB)),
    CORE600.helpers, 600, 520 * MIB)
EF_LOCAL_SEQUENCE = ResourceProfile("ef-local-sequence-cpu1800-as4096-v1", (
    ("worker", 1500, 3072 * MIB), ("coordinator", 180, 512 * MIB),
    ("controller", 120, 512 * MIB)), CORE600.helpers, 1800, 4096 * MIB, 3600)
EI_LOCAL_MODEL = ResourceProfile("ei-local-model-cpu1800-as4096-v1", (
    ("worker", 1500, 3072 * MIB), ("coordinator", 180, 512 * MIB),
    ("controller", 120, 512 * MIB)), CORE600.helpers, 1800, 4096 * MIB, 3600)


def resource_profile(profile: str | ResourceProfile | None = None) -> ResourceProfile:
    for preset in (CORE600, ORDINARY300, ED_H1_600, ED_H1_600_AS520, EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL):
        if profile is preset or (type(profile) is str and profile == preset.id):
            return preset
    require(profile is None, "unsupported registered resource profile")
    return CORE600


HEADER_MAX = 16384
BLOCK = 65536
MAGIC = b"ECWSL3\x00\x01"
EC_INPUT_CONTRACT = "ec-original-three-v1"
ED_INPUTS_CONTRACT = "ed-h1-inputs-v1"
ED_SNAPSHOT_CONTRACT = "ed-h1-snapshot-v1"
EE_INPUT_CONTRACT = "ee-market-original-three-v1"
EE_INPUT_BOUNDS = (("CB", 16 * MIB), ("CC", 512 * MIB), ("CX", 512 * MIB))
EF_INPUT_CONTRACT = "ef-chart-original-three-v1"
EF_INPUT_BOUNDS = EE_INPUT_BOUNDS
EF_SEALED_CONTRACT = "ef-chart-sealed-receipt-v1"
EF_SEALED_BOUNDS = (("EF-SEALED-RECEIPT", 64 * 1024),)
EH_SUPPORT_INPUT_CONTRACT = "eh-support-original-cb-cc-v1"
EH_SUPPORT_INPUT_BOUNDS = (("CB", 16 * MIB), ("CC", 512 * MIB))
EH_FORECAST_INPUT_CONTRACT = "eh-forecast-sealed-snapshot-v1"
EH_FORECAST_INPUT_BOUNDS = (("EH-SNAPSHOT", 160 * MIB),)
EH_MANAGEMENT_INPUT_CONTRACT = "eh-management-sealed-snapshot-cc-v1"
EH_MANAGEMENT_INPUT_BOUNDS = (*EH_SUPPORT_INPUT_BOUNDS, *EH_FORECAST_INPUT_BOUNDS)
EI_SUPPORT_INPUT_CONTRACT = "ei-support-original-cb-cc-cx-v1"
EI_SUPPORT_INPUT_BOUNDS = (("CB", 16 * MIB), ("CC", 512 * MIB), ("CX", 512 * MIB))
EI_FORECAST_INPUT_CONTRACT = "ei-forecast-audited-snapshot-and-cb-v1"
EI_FORECAST_INPUT_BOUNDS = (("CB", 16 * MIB), ("EI-SNAPSHOT", 256 * MIB))
EI_ECONOMIC_INPUT_CONTRACT = "ei-economic-original-minutes-and-audited-artifacts-v1"
EI_ECONOMIC_INPUT_BOUNDS = (*EI_SUPPORT_INPUT_BOUNDS, ("EI-SNAPSHOT", 256 * MIB), ("EI-FORECAST", 128 * MIB))
ED_H1_INPUT_BOUNDS = (
    ("CB", 16 * MIB), ("CC", 512 * MIB), ("CX", 512 * MIB),
    ("DART-REPORT", 16 * MIB), ("DART-PUBLICATION", 4 * MIB),
    ("DART-ORIGIN-MANIFEST", 4 * MIB),
    *((f"DART-R{index:03d}", MIB) for index in range(1, 19)),
    *((f"DART-M{index:03d}", 64 * 1024) for index in range(1, 19)),
)
ED_H1_SNAPSHOT_INPUT_BOUNDS = (("ED-SNAPSHOT", 512 * MIB),)


class EndOfFrames(EOFError):
    """Clean EOF between frames; a truncated frame remains a plain EOFError."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def encode(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False, ensure_ascii=False) + "\n").encode()


def digest(raw: bytes) -> str:
    return _sha256(raw).hexdigest()


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
    first = stream.read(1)
    if not first:
        raise EndOfFrames("bridge stream closed between frames")
    require(first + exact_read(stream, len(MAGIC) - 1) == MAGIC, "bridge magic differs")
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
    hashed, size = _sha256(), 0
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
        # Sender-host provenance is a label, not a path on this receiver.
        # Actual source/destination IO retains safe_path/regular checks below.
        original_path = item["original_path"]
        require(type(original_path) is str and "\x00" not in original_path,
                "invalid provenance path text")
        foreign_path = PurePosixPath(original_path)
        require(foreign_path.is_absolute() and ".." not in foreign_path.parts,
                "absolute unaliased POSIX provenance path required")
        maximum = 512 * MIB if item["name"] == "CC" else 64 * MIB
        require(type(item["maximum"]) is int and item["maximum"] == maximum
                and type(item["size"]) is int and 0 < item["size"] <= maximum,
                "input bound differs")
    require(len({item["original_path"] for item in manifest}) == 3, "duplicate input paths")
    return manifest


def validate_ed_input_manifest(manifest: object, *, input_contract: str) -> list[dict]:
    """Two fixed ED rosters; EC's original-three validator remains separate."""
    if input_contract == ED_INPUTS_CONTRACT:
        bounds = ED_H1_INPUT_BOUNDS
    else:
        require(input_contract == ED_SNAPSHOT_CONTRACT, "unsupported ED input contract")
        bounds = ED_H1_SNAPSHOT_INPUT_BOUNDS
    require(type(manifest) is list and len(manifest) == len(bounds)
            and all(type(item) is dict for item in manifest), "registered ED input roster differs")
    require([item.get("name") for item in manifest] == [name for name, _ in bounds],
            "ED input allowlist differs")
    for item, (_, maximum) in zip(manifest, bounds, strict=True):
        require(set(item) == {"name", "original_path", "sha256", "size", "maximum"}
                and valid_sha(item["sha256"]), "ED input metadata differs")
        original_path = item["original_path"]
        require(type(original_path) is str and "\x00" not in original_path, "invalid ED provenance path text")
        foreign_path = PurePosixPath(original_path)
        require(foreign_path.is_absolute() and ".." not in foreign_path.parts,
                "absolute unaliased POSIX ED provenance path required")
        require(type(item["maximum"]) is int and item["maximum"] == maximum
                and type(item["size"]) is int and 0 < item["size"] <= maximum, "ED input bound differs")
    require(len({item["original_path"] for item in manifest}) == len(bounds), "duplicate ED input paths")
    return manifest


def validate_registered_input_manifest(manifest: object, *, input_contract: str) -> list[dict]:
    """Dispatch exact registered contracts without sharing their input allowlists."""
    if input_contract == EC_INPUT_CONTRACT:
        return validate_input_manifest(manifest)
    if input_contract in (ED_INPUTS_CONTRACT, ED_SNAPSHOT_CONTRACT):
        return validate_ed_input_manifest(manifest, input_contract=input_contract)
    registered = {EE_INPUT_CONTRACT: ("EE", EE_INPUT_BOUNDS), EF_INPUT_CONTRACT: ("EF", EF_INPUT_BOUNDS),
                  EF_SEALED_CONTRACT: ("EF", EF_SEALED_BOUNDS),
                  EH_SUPPORT_INPUT_CONTRACT: ("EH", EH_SUPPORT_INPUT_BOUNDS),
                  EH_FORECAST_INPUT_CONTRACT: ("EH", EH_FORECAST_INPUT_BOUNDS),
                  EH_MANAGEMENT_INPUT_CONTRACT: ("EH", EH_MANAGEMENT_INPUT_BOUNDS),
                  EI_SUPPORT_INPUT_CONTRACT: ("EI", EI_SUPPORT_INPUT_BOUNDS),
                  EI_FORECAST_INPUT_CONTRACT: ("EI", EI_FORECAST_INPUT_BOUNDS),
                  EI_ECONOMIC_INPUT_CONTRACT: ("EI", EI_ECONOMIC_INPUT_BOUNDS)}
    require(input_contract in registered, "unsupported registered input contract")
    campaign, bounds = registered[input_contract]
    require(type(manifest) is list and len(manifest) == len(bounds)
            and all(type(item) is dict for item in manifest), f"registered {campaign} input roster differs")
    require([item.get("name") for item in manifest] == [name for name, _ in bounds],
            f"{campaign} input allowlist differs")
    for item, (_, maximum) in zip(manifest, bounds, strict=True):
        require(set(item) == {"name", "original_path", "sha256", "size", "maximum"}
                and valid_sha(item["sha256"]), f"{campaign} input metadata differs")
        original_path = item["original_path"]
        require(type(original_path) is str and "\x00" not in original_path, f"invalid {campaign} provenance path text")
        foreign_path = PurePosixPath(original_path)
        require(foreign_path.is_absolute() and ".." not in foreign_path.parts,
                f"absolute unaliased POSIX {campaign} provenance path required")
        if campaign in ("EF", "EH", "EI"):
            require(foreign_path.anchor == "/" and str(foreign_path) == original_path,
                    f"canonical {campaign} provenance path required")
        if campaign in ("EH", "EI"):
            require(not foreign_path.is_relative_to("/mnt"), f"native {campaign} provenance path required")
        require(type(item["maximum"]) is int and item["maximum"] == maximum
                and type(item["size"]) is int and 0 < item["size"] <= maximum, f"{campaign} input bound differs")
    require(len({item["original_path"] for item in manifest}) == len(bounds), f"duplicate {campaign} input paths")
    return manifest


def send_file(stream: BinaryIO, path: Path, metadata: dict,
              checkpoint: Callable[[str], object]) -> None:
    pin, size = file_digest(path, metadata["maximum"], checkpoint)
    require((pin, size) == (metadata["sha256"], metadata["size"]), "source pin differs")
    before = regular(path)
    send_header(stream, {"kind": "FILE", **metadata})
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    copied, hashed = 0, _sha256()
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
    hashed, remaining = _sha256(), expected["size"]
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


def validate_role_limits(limits: dict, *, profile: str | ResourceProfile | None = None) -> None:
    preset = resource_profile(profile)
    require(limits == preset.role_limits(), "registered role limits differ")
    require(sum(v["cpu_seconds"] for v in limits.values()) == preset.cpu_seconds
            and sum(v["address_space_bytes"] for v in limits.values()) == preset.address_space_bytes,
            "shared role totals differ")


def require_unprivileged_single_task() -> None:
    status = Path("/proc/self/status").read_text().splitlines()
    permitted = next(line.split()[1] for line in status if line.startswith("CapPrm:"))
    # Linux exempts real UID 0, CAP_SYS_ADMIN and CAP_SYS_RESOURCE from RLIMIT_NPROC.
    require(0 not in os.getresuid() and int(permitted, 16) & ((1 << 21) | (1 << 24)) == 0
            and len(os.listdir("/proc/self/task")) == 1, "local model requires an unprivileged single-task process")


def prohibit_ef_tasks() -> None:
    require_unprivileged_single_task()
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def prepare_local_model_controller(profile: str | ResourceProfile) -> None:
    """Enforce controller soft caps while retaining the children's hard ceilings."""
    preset = resource_profile(profile)
    require(preset in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL), "unsupported local model controller profile")
    require_unprivileged_single_task()
    caps = parent_limits("controller", profile=preset)
    worker = preset.role_limits()["worker"]
    signal.signal(signal.SIGXCPU, signal.SIG_DFL)
    resource.setrlimit(resource.RLIMIT_CPU, (caps["cpu_seconds"], worker["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_AS, (caps["address_space_bytes"], worker["address_space_bytes"]))
    _schedule_role()


def prepare_ef_controller() -> None:
    """Retain the existing EF controller entry point and its fixed partition."""
    prepare_local_model_controller(EF_LOCAL_SEQUENCE)


def _schedule_role() -> None:
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    Path("/proc/self/oom_score_adj").write_text("1000")


def apply_role_limits(role: str, *, profile: str | ResourceProfile | None = None) -> None:
    """Set hard role limits before loading science or authenticating inputs."""
    preset = resource_profile(profile)
    caps = parent_limits(role, profile=preset)
    resource.setrlimit(resource.RLIMIT_CPU, (caps["cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (caps["address_space_bytes"],) * 2)
    _schedule_role()
    if preset in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL):
        if role == "worker":
            prohibit_ef_tasks()
        else:
            require_unprivileged_single_task()


def parent_limits(role: str, *, profile: str | ResourceProfile | None = None) -> dict:
    preset = resource_profile(profile)
    require(role in preset.role_limits(), "unsupported measured role")
    return {key: value - preset.helper_limits().get(role, {}).get(key, 0)
            for key, value in preset.role_limits()[role].items()}


def role_sample(role: str, helpers: dict | None = None, *,
                profile: str | ResourceProfile | None = None) -> dict:
    preset = resource_profile(profile)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    sample = {
        "role": role, "pid": os.getpid(), "limits": preset.role_limits()[role],
        "parent_limits": parent_limits(role, profile=preset),
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
    if profile is not None:
        sample["resource_profile"] = preset.id
    if preset in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL):
        require_unprivileged_single_task()
        sample["readback"]["tasks"] = 1
        if role == "worker":
            sample["readback"]["rlimit_nproc"] = list(resource.getrlimit(resource.RLIMIT_NPROC))
    return sample


def validate_role_sample(sample: dict, role: str, *,
                         profile: str | ResourceProfile | None = None) -> None:
    preset = resource_profile(profile)
    caps = preset.role_limits()[role]
    parent = parent_limits(role, profile=preset)
    require(sample.get("resource_profile") == (preset.id if profile is not None else None),
            "sample resource profile differs")
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
            and 0 < sample["address_space_bytes"] <= parent["address_space_bytes"],
            f"role resource exhausted: {role} CPU {sample['cpu_self_seconds']!r}/{parent['cpu_seconds']}s; "
            f"address space {sample['address_space_bytes']!r}/{parent['address_space_bytes']} bytes")
    helpers = sample["helpers"]
    helper_limits = preset.helper_limits()
    require(type(helpers["cpu_seconds"]) in (int, float) and math.isfinite(helpers["cpu_seconds"])
            and 0 <= helpers["cpu_seconds"] <= helper_limits.get(role, {}).get("cpu_seconds", 0)
            and type(helpers["count"]) is int and helpers["count"] == len(helpers["readbacks"]),
            "helper cumulative CPU exceeded")
    for readback in helpers["readbacks"]:
        require(role in helper_limits and 0 < readback["cpu_seconds"] <= helper_limits[role]["cpu_seconds"]
                and readback["address_space_bytes"] == helper_limits[role]["address_space_bytes"],
                "helper sublimits differ")
    if preset in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL):
        require(sample["readback"].get("tasks") == 1 and
                (role != "worker" or (sample["readback"].get("rlimit_nproc") == [0, 0]
                                      and sample["children_cpu_diagnostic_seconds"] == 0)),
                "local model role task limits differ")
        helper_cpu = 0.0
        for item in helpers["readbacks"]:
            elapsed = item.get("cpu_self_seconds")
            require(item.get("rlimit_nproc") == [0, 0] and item.get("nice") == 10
                    and item.get("affinity") == sample["readback"]["affinity"]
                    and type(elapsed) in (int, float) and 0 <= elapsed < item["cpu_seconds"]
                    and item["cpu_seconds"] == int(helper_limits[role]["cpu_seconds"] - helper_cpu),
                    "local model helper cumulative CPU or task limits differ")
            helper_cpu += elapsed
        require(helper_cpu == helpers["cpu_seconds"], "local model helper cumulative CPU receipt differs")


def aggregate_roles(samples: dict, *, profile: str | ResourceProfile | None = None) -> dict:
    preset = resource_profile(profile)
    require(preset not in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL) and set(samples) == set(preset.role_limits()),
            "all four measured roles required for a cross-host profile")
    for role, sample in samples.items():
        validate_role_sample(sample, role, profile=profile)
    result = {"role_limits": preset.role_limits(), "samples": samples,
            "cpu_seconds_counted_once": sum(item["cpu_self_seconds"] + item["helpers"]["cpu_seconds"]
                                           for item in samples.values()),
            "hard_cpu_sum_seconds": preset.cpu_seconds, "hard_address_space_sum_bytes": preset.address_space_bytes,
            "measurement_cutoff": "worker finished; other dedicated roles sampled before canonical terminal",
            "ssh_server_daemon_measured": False, "automatic_retries": 0}
    if profile is not None:
        result["resource_profile"] = preset.id
        result["measurement_cutoff"] = (
            "worker after exact output export, before terminal wait; other roles before canonical terminal; "
            "later acknowledgement work remains hard-limited but is outside these CPU snapshots")
    return result


def aggregate_local_roles(samples: dict, *, profile: str | ResourceProfile) -> dict:
    """Three measured local PIDs with the exact registered partition."""
    preset = resource_profile(profile)
    require(preset in (ORDINARY300, EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL), "unsupported local resource profile")
    validate_role_limits(preset.role_limits(), profile=preset)
    roles = ("worker", "coordinator", "controller")
    require(set(samples) == set(roles), "three real local roles required")
    # Canonical JSON sorts mapping keys. Keep the original calculation order
    # when reconstructing the same receipt after reading it back.
    samples = {role: samples[role] for role in roles}
    for role, sample in samples.items():
        validate_role_sample(sample, role, profile=profile)
        require(type(sample.get("pid")) is int and sample["pid"] > 0, "real local PID required")
    require(len({sample["pid"] for sample in samples.values()}) == 3, "distinct local role PIDs required")
    extra = {"unused_ssh_reserve": preset.role_limits()["ssh_client"]} if preset is ORDINARY300 else {}
    if preset in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL):
        require(len({tuple(sample["readback"]["affinity"]) for sample in samples.values()}) == 1,
                "local model measured roles must share one CPU")
    return {"resource_profile": preset.id, "execution_host": "local_wsl_only",
        "role_limits": {role: preset.role_limits()[role] for role in samples}, "samples": samples,
        **extra,
        "cpu_seconds_counted_once": sum(item["cpu_self_seconds"] + item["helpers"]["cpu_seconds"]
                                       for item in samples.values()),
        "hard_cpu_sum_seconds": preset.cpu_seconds, "hard_address_space_sum_bytes": preset.address_space_bytes,
        "measurement_cutoff": "worker after exact output export, before terminal wait; other roles before canonical terminal; "
            "later acknowledgement work remains hard-limited but is outside these CPU snapshots",
        "automatic_retries": 0}


def fixed_deadline(deadline: float, *, profile: str | ResourceProfile | None = None) -> None:
    remaining = deadline - time.time()
    preset = resource_profile(profile)
    require(math.isfinite(deadline) and 0 < remaining <= preset.wall_seconds,
            "fixed remote wall deadline expired or differs")
    def expire(*_: object) -> None:
        raise TimeoutError("fixed local model wall deadline" if preset in (EF_LOCAL_SEQUENCE, EI_LOCAL_MODEL)
                           else "fixed cross-host wall deadline")
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextmanager
def research_locks(paths: list[Path], marker: Path, attempt: str, *,
                   before_marker: Callable[[], None] | None = None) -> Iterator[None]:
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
        if before_marker is not None:
            before_marker()
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

    def accept_completed(self, ack: dict, accounts_sha: str, *, digest_field: str = "accounts_sha256") -> None:
        require(digest_field in ("accounts_sha256", "output_sha256"), "unregistered output digest field")
        require(self.started is not None and not self.completed
                and ack.get("kind") == "COMPLETED" and ack.get("status") == "completed"
                and ack.get("run_id") == self.started["run_id"]
                and ack.get(digest_field) == accounts_sha
                and valid_sha(ack.get("record_sha256")), "missing durable terminal ACK; outcome unknown")
        self.completed = True
