"""EG's local, cgroup-accounted lifecycle; no market loader or public CLI.

The private, source-pinned entry point supplies a registered closed unit. Source
preflight, the durable discovery start, computation, independent verification
and persistence all run in the same transient service. A failed attempt leaves
its directory and marker in place. Neither this module nor a receipt authorizes
another attempt. Existing EF/ED process profiles are deliberately not used.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import threading
import time

from research import experiment_log
from research import intraday_campaign_wsl_transport as files


UNIT = "EG-COVERAGE-1"
PROFILE = "eg-local-cpu4-cuda-v1"
SPEC = "configs/research/discovery/intraday-highvol-coverage-v1.json"
NATIVE_ROOT = Path("/home/minju/research-evidence/eg-highvol-20261106")
ENTRYPOINT = "var/eg-highvol-runtime-v1.py"
RUNTIME_SOURCE = "python/research/intraday_highvol_runtime.py"
MEMORY = 2944 * files.MIB  # 128 MiB reserved for launcher + systemd-run (64 MiB AS each).
CPU_USEC = 7140 * 1_000_000  # launcher20 + systemd-run20 + later original-auditor20 are reserved.
WALL_SECONDS = 3560  # launcher overhead20 + later original-auditor20 are reserved.
# Reserve at most ten wall seconds / forty aggregate CPU seconds for the
# same-cgroup ExecStopPost counter receipt after the science process exits.
MAIN_WALL_SECONDS = WALL_SECONDS - 10
MAIN_CPU_USEC = CPU_USEC - 40 * 1_000_000
PIDS = 128
CPUS = frozenset(range(4))
FULL = {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
        "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158, "cx_rows": 1002211,
        "cx_daily_rows": 2674}
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
MAX_METADATA = 4 * files.MIB


@dataclass(frozen=True)
class UnitDescriptor:
    unit_id: str
    specification: str
    input_contract: str
    entrypoint: str
    auditor: str
    service_prefix: str
    configuration_prefix: str
    metadata_prefix: str


def _fit_descriptor(unit_id: str) -> UnitDescriptor:
    prefix = "eg-highvol-fit-" + unit_id.lower() + "-"
    return UnitDescriptor(unit_id, "configs/research/discovery/intraday-highvol-fit-v1.json",
        "eg-highvol-model-fit-v1", "var/eg-highvol-fit-runtime-v1.py", "var/eg-highvol-fit-audit-v1.py",
        prefix, prefix, prefix)


def _evaluation_descriptor(unit_id: str) -> UnitDescriptor:
    prefix = "eg-highvol-evaluation-" + unit_id.lower() + "-"
    return UnitDescriptor(unit_id, "configs/research/discovery/intraday-highvol-evaluation-v1.json",
        "eg-highvol-evaluation-v1", "var/eg-highvol-evaluation-runtime-v1.py",
        "var/eg-highvol-evaluation-audit-v1.py", prefix, prefix, prefix)


_UNITS = (
    UnitDescriptor(UNIT, SPEC, "eg-highvol-original-three-v1", ENTRYPOINT,
        "var/eg-highvol-original-audit-v1.py", "eg-highvol-coverage-", "eg-highvol-", "eg-highvol-"),
    UnitDescriptor("EG-SCENARIO-SNAPSHOT-1",
        "configs/research/discovery/intraday-highvol-snapshot-v1.json", "eg-highvol-scenario-snapshot-v1",
        "var/eg-highvol-snapshot-runtime-v1.py", "var/eg-highvol-snapshot-audit-v1.py",
        "eg-highvol-snapshot-", "eg-highvol-snapshot-", "eg-highvol-snapshot-"),
    *(_fit_descriptor(unit_id) for unit_id in ("EG-TCN-A-S1-1", "EG-TCN-A-S2-1", "EG-TCN-B-S1-1",
                                              "EG-TCN-B-S2-1", "EG-LGB-A-1", "EG-LGB-B-1")),
    *(_evaluation_descriptor(unit_id) for unit_id in ("EG-POLICY-COMPARISON-1", "EG-MANAGEMENT-1")),
)


def registered_unit(unit_id: str) -> UnitDescriptor:
    """Closed release roster, never a caller-defined path, clock or budget."""
    for descriptor in _UNITS:
        if type(unit_id) is str and unit_id == descriptor.unit_id:
            return descriptor
    raise ValueError("EG unit is not separately registered")


def _json(raw: bytes) -> dict:
    value = json.loads(raw)
    files.require(type(value) is dict and files.encode(value) == raw, "canonical EG metadata required")
    return value


def _read(path: Path, maximum: int = MAX_METADATA) -> bytes:
    before = files.regular(path)
    files.require(before.st_size <= maximum, "EG bounded metadata exceeded")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        files.require(files.file_identity(before) == files.file_identity(os.fstat(stream.fileno())),
                      "EG metadata changed before open")
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    files.require(len(raw) == before.st_size and files.file_identity(before) == files.file_identity(after)
                  == files.file_identity(files.regular(path)), "EG metadata changed during read")
    return raw


def _relative(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and str(path) == name


@dataclass(frozen=True)
class Artifact:
    name: str
    sha256: str
    size: int
    maximum: int

    def payload(self) -> dict:
        files.require(_relative(self.name) and len(PurePosixPath(self.name).parts) == 1
                      and files.valid_sha(self.sha256) and type(self.size) is int
                      and type(self.maximum) is int and 0 < self.size <= self.maximum <= 512 * files.MIB,
                      "EG artifact pin differs")
        return vars(self).copy()


@dataclass(frozen=True)
class RunContract:
    worktree: Path
    attempt: str
    invented_only: bool
    service: str
    source_hashes: tuple[tuple[str, str], ...]
    runtime_files: tuple[tuple[str, str, int], ...]
    merge_commit: str
    reviewed_commit: str
    tree: str
    qualification_attempt: str | None = None
    qualification_audit_sha256: str | None = None
    unit_id: str = UNIT

    @property
    def descriptor(self) -> UnitDescriptor:
        return registered_unit(self.unit_id)

    @property
    def directory(self) -> Path:
        return NATIVE_ROOT / self.descriptor.unit_id / self.attempt

    @property
    def binding(self) -> dict:
        descriptor = self.descriptor
        return {"unit_id": descriptor.unit_id, "resource_profile": PROFILE, "invented_only": self.invented_only,
                "attempt": self.attempt, "source_sha256": files.digest(files.encode(dict(self.source_hashes))),
                "runtime_sha256": files.digest(files.encode(self.runtime_files)),
                "registration_sha256": dict(self.source_hashes)[descriptor.specification],
                "merge_commit": self.merge_commit, "reviewed_commit": self.reviewed_commit, "tree": self.tree,
                "input_contract": descriptor.input_contract, "geometry": FULL}

    def validate(self) -> None:
        files.safe_path(self.worktree)
        descriptor = self.descriptor
        files.require(type(self.invented_only) is bool and
                      (bool(re.fullmatch(r"qualification[1-9][0-9]*", self.attempt)) if self.invented_only
                       else self.attempt == "actual1"), "EG attempt is not separately registered")
        expected = descriptor.service_prefix + self.attempt + "-"
        files.require(self.service.startswith(expected) and
                      re.fullmatch(re.escape(expected) + r"[0-9a-f]{16}\.service", self.service) is not None,
                      "EG transient service identity differs")
        names = [name for name, _ in self.source_hashes]
        files.require(len(names) == len(set(names)) and
                      {descriptor.specification, descriptor.entrypoint, RUNTIME_SOURCE} <= set(names)
                      and all(_relative(name) and files.valid_sha(pin) for name, pin in self.source_hashes),
                      "EG exact source roster differs")
        runtime_names = [name for name, _, _ in self.runtime_files]
        files.require(runtime_names and len(runtime_names) == len(set(runtime_names)) and
                      str(Path(sys.executable).resolve()) in runtime_names, "EG executable runtime pin missing")
        for name, pin, maximum in self.runtime_files:
            files.safe_path(Path(name))
            files.require(files.valid_sha(pin) and type(maximum) is int and 0 < maximum <= 512 * files.MIB,
                          "EG runtime file pin differs")
        files.require(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in
                          (self.merge_commit, self.reviewed_commit, self.tree)), "EG Git identity differs")
        if self.invented_only:
            files.require(self.qualification_attempt is None and self.qualification_audit_sha256 is None,
                          "invented run cannot consume an actual qualification")
        else:
            files.require(type(self.qualification_attempt) is str and re.fullmatch(
                r"qualification[1-9][0-9]*", self.qualification_attempt) is not None
                and files.valid_sha(self.qualification_audit_sha256), "EG original qualification audit missing")


@dataclass(frozen=True)
class UnitOutput:
    artifacts: tuple[Artifact, ...]
    geometry: bytes
    summary: bytes


@dataclass(frozen=True)
class ClosedUnit:
    """Only the reviewed private entry point constructs these three closures.

    preflight checks registrations, never opens market data. compute opens the
    fixed originals only after STARTED; independent_verify must finish before
    a completed terminal can be emitted. No CLI selects a module or callable.
    """
    preflight: Callable[[], None]
    compute: Callable[[Path, dict, Callable[[str], None]], UnitOutput]
    independent_verify: Callable[[Path, UnitOutput, dict, Callable[[str], None]], dict]


def launch_argv(contract: RunContract, python: Path, configuration: Path) -> list[str]:
    """Build only the EG private entry-point command; does not launch anything."""
    contract.validate()
    files.safe_path(configuration)
    files.safe_path(python)
    files.require(configuration.parent == contract.worktree / "var" and
                  configuration.name == contract.descriptor.configuration_prefix + contract.attempt + ".json",
                  "EG private configuration path differs")
    files.require(str(python.resolve()) in {row[0] for row in contract.runtime_files},
                  "EG launcher interpreter is not pinned")
    return ["/usr/bin/systemd-run", "--user", "--wait", "--pipe", "--collect",
            "--unit=" + contract.service, "--property=MemoryMax=3087007744", "--property=MemorySwapMax=0",
            "--property=CPUQuota=400%", "--property=CPUAffinity=0 1 2 3",
            "--property=TasksMax=128", "--property=Nice=10", "--property=RuntimeMaxSec=3550",
            "--property=TimeoutStopSec=10",
            "--property=ExecStopPost=" + " ".join((str(python), "-I", "-S", str(contract.worktree / contract.descriptor.entrypoint),
                                                      "stopstats", str(configuration))),
            "--property=KillMode=control-group", "--property=Delegate=no", "--property=UMask=0077",
            "--setenv=OMP_NUM_THREADS=4", "--setenv=MKL_NUM_THREADS=4", "--setenv=OPENBLAS_NUM_THREADS=4",
            "--setenv=PYTHONHASHSEED=0", "--setenv=PYTHONDONTWRITEBYTECODE=1",
            str(python), "-I", "-S", str(contract.worktree / contract.descriptor.entrypoint), "worker", str(configuration)]


def metadata_service(service: str, role: str) -> str:
    files.require(role in ("controller", "audit"), "closed EG metadata role required")
    for descriptor in _UNITS:
        if re.fullmatch(re.escape(descriptor.service_prefix) +
                        r"(?:qualification[1-9][0-9]*|actual1)-[0-9a-f]{16}\.service", service):
            return descriptor.metadata_prefix + role + "-" + service[len(descriptor.service_prefix):]
    raise ValueError("closed EG metadata service required")


def metadata_launch_argv(contract: RunContract, role: str, python: Path, configuration: Path) -> list[str]:
    """Command for the tool invoker; no recursive Python launcher is created."""
    launch_argv(contract, python, configuration)  # Validate the same fixed interpreter/configuration paths.
    service = metadata_service(contract.service, role)
    command = ([str(contract.worktree / contract.descriptor.entrypoint), "launch", str(configuration)]
               if role == "controller" else [str(contract.worktree / contract.descriptor.auditor), str(configuration)])
    return ["/usr/bin/systemd-run", "--user", "--wait", "--pipe", "--collect", "--unit=" + service,
            "--property=MemoryMax=134217728", "--property=MemorySwapMax=0", "--property=CPUQuota=400%",
            "--property=CPUAffinity=0 1 2 3", "--property=TasksMax=16", "--property=Nice=10",
            "--property=RuntimeMaxSec=" + ("3580" if role == "controller" else "20"),
            "--property=KillMode=control-group", "--property=Delegate=no", "--property=UMask=0077",
            "--setenv=PYTHONDONTWRITEBYTECODE=1", str(python), "-I", "-S", *command]


def _cpus(text: str) -> frozenset[int]:
    values = set()
    for part in text.split(","):
        ends = part.split("-")
        first, last = int(ends[0]), int(ends[-1])
        files.require(len(ends) <= 2 and 0 <= first <= last < 4096, "invalid cgroup CPU set")
        values.update(range(first, last + 1))
    return frozenset(values)


def _key_values(path: Path) -> dict[str, int]:
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


def _limit(path: Path) -> int | None:
    raw = path.read_text().strip()
    return None if raw == "max" else int(raw)


def _node(path: Path) -> dict:
    quota, period = (path / "cpu.max").read_text().split()
    cpuset = path / "cpuset.cpus.effective"
    return {"path": str(path), "cpu_quota": None if quota == "max" else int(quota),
            "cpu_period": int(period), "cpus": sorted(_cpus(cpuset.read_text().strip())) if cpuset.exists() else None,
            "memory": _limit(path / "memory.max"), "swap": _limit(path / "memory.swap.max"),
            "pids": _limit(path / "pids.max")}


def cgroup_path(service: str) -> Path:
    rows = Path("/proc/self/cgroup").read_text().splitlines()
    files.require(len(rows) == 1 and rows[0].startswith("0::/"), "unified cgroup v2 required")
    relative = PurePosixPath(rows[0][3:])
    files.require(".." not in relative.parts and relative.name == service
                  and str(relative).startswith("/user.slice/"), "EG service cgroup differs")
    path = Path("/sys/fs/cgroup") / str(relative).lstrip("/")
    files.safe_path(path)
    return path


def sample_cgroup(path: Path) -> dict:
    """Kernel subtree counters count all children once, including exited ones."""
    ancestors = []
    for parent in path.parents:
        if parent == Path("/sys/fs/cgroup"):
            break
        ancestors.append(_node(parent))
    directories = [path, *sorted(child for child in path.rglob("*") if child.is_dir())]
    files.require(len(directories) <= PIDS, "EG cgroup descendant bound exceeded")
    processes, threads = set(), []
    for directory in directories:
        files.safe_path(directory)
        for raw_pid in (directory / "cgroup.procs").read_text().split():
            pid = int(raw_pid)
            processes.add(pid)
            try:
                tasks = list((Path("/proc") / raw_pid / "task").iterdir())
            except FileNotFoundError:
                continue
            for task in tasks:
                try:
                    tid = int(task.name)
                    threads.append({"pid": pid, "tid": tid, "nice": os.getpriority(os.PRIO_PROCESS, tid),
                                    "cpus": sorted(os.sched_getaffinity(tid))})
                except ProcessLookupError:
                    continue
    return {"root": _node(path), "ancestors": ancestors, "descendants": [_node(item) for item in directories[1:]],
            "cpu_usec": _key_values(path / "cpu.stat")["usage_usec"],
            "memory_current": int((path / "memory.current").read_text()),
            "memory_peak": int((path / "memory.peak").read_text()),
            "swap_current": int((path / "memory.swap.current").read_text()),
            "pids_current": int((path / "pids.current").read_text()),
            "memory_events": _key_values(path / "memory.events"),
            "pids_events": _key_values(path / "pids.events"), "processes": sorted(processes), "threads": threads}


def validate_resources(sample: dict, elapsed: float) -> None:
    root = sample["root"]
    files.require(root["memory"] == MEMORY and root["swap"] == 0 and root["pids"] == PIDS
                  and root["cpu_quota"] == 4 * root["cpu_period"]
                  and (root["cpus"] is None or CPUS <= set(root["cpus"])),
                  "EG cgroup hard-limit readback differs")
    # Tighter ancestors change the qualified envelope; looser ancestors are
    # harmless because the service's aggregate caps apply to all descendants.
    for ancestor in sample["ancestors"]:
        files.require((ancestor["memory"] is None or ancestor["memory"] >= MEMORY)
                      and (ancestor["pids"] is None or ancestor["pids"] >= PIDS)
                      and (ancestor["cpu_quota"] is None or ancestor["cpu_quota"] >= 4 * ancestor["cpu_period"])
                      and (ancestor["cpus"] is None or CPUS <= set(ancestor["cpus"])),
                      "EG effective ancestor envelope differs")
    for child in sample["descendants"]:
        files.require(child["path"].startswith(root["path"] + "/")
                      and (child["cpus"] is None or root["cpus"] is None or set(child["cpus"]) <= set(root["cpus"])),
                      "EG descendant escaped the CPU subtree")
    files.require(0 <= sample["cpu_usec"] < CPU_USEC and 0 <= elapsed < WALL_SECONDS,
                  "EG shared CPU or whole-service wall budget exhausted")
    files.require(0 <= sample["memory_current"] <= sample["memory_peak"] <= MEMORY
                  and sample["swap_current"] == 0 and 0 < sample["pids_current"] <= PIDS
                  and not any(sample["memory_events"].get(key, 0) for key in ("max", "oom", "oom_kill", "oom_group_kill"))
                  and sample["pids_events"].get("max", 0) == 0, "EG memory/swap/process limit was hit")
    files.require(len(sample["threads"]) <= PIDS and all(row["nice"] >= 10 and set(row["cpus"]) <= CPUS
                  for row in sample["threads"]), "EG descendant priority or affinity differs")


def validate_metadata_resources(sample: dict, service: str, role: str) -> None:
    """The Python metadata role and all children share one swap-free envelope."""
    expected = metadata_service(service, role)
    root = sample["root"]
    files.require(root["path"].endswith("/" + expected) and root["memory"] == 128 * files.MIB
                  and root["swap"] == 0 and root["pids"] == 16
                  and root["cpu_quota"] == 4 * root["cpu_period"], "EG metadata cgroup hard limits differ")
    files.require(0 <= sample["cpu_usec"] < (40 if role == "controller" else 20) * 1_000_000
                  and 0 <= sample["elapsed_seconds"] < (3580 if role == "controller" else 20)
                  and 0 <= sample["memory_current"] <= sample["memory_peak"] <= 128 * files.MIB
                  and sample["swap_current"] == 0 and 0 < sample["pids_current"] <= 16
                  and not any(sample["memory_events"].get(key, 0) for key in ("max", "oom", "oom_kill", "oom_group_kill"))
                  and sample["pids_events"].get("max", 0) == 0, "EG metadata resource reservation exceeded")
    for node in sample["ancestors"]:
        files.require((node["memory"] is None or node["memory"] >= 128 * files.MIB)
                      and (node["pids"] is None or node["pids"] >= 16)
                      and (node["cpu_quota"] is None or node["cpu_quota"] >= 4 * node["cpu_period"]),
                      "EG metadata effective ancestor differs")
    files.require(all(node["path"].startswith(root["path"] + "/") for node in sample["descendants"])
                  and 0 < len(sample["threads"]) <= 16 and all(row["nice"] >= 10 and set(row["cpus"]) <= CPUS
                  for row in sample["threads"]), "EG metadata descendant priority/affinity differs")


def metadata_readback(service: str, role: str, started: float) -> dict:
    sample = sample_cgroup(cgroup_path(metadata_service(service, role)))
    sample["elapsed_seconds"] = time.clock_gettime(time.CLOCK_BOOTTIME) - started
    validate_metadata_resources(sample, service, role)
    files.require(os.getpid() in sample["processes"], "metadata controller left its cgroup")
    return sample


class BudgetGuard:
    """A periodic watchdog also covers blocking native compute and Git helpers.

    cgroup.kill terminates the entire service, including this process. Such a
    failure deliberately leaves STARTED/marker evidence instead of publishing
    success. RuntimeMaxSec is a separate kernel/service wall-time backstop.
    """
    def __init__(self, service: str) -> None:
        self.path = cgroup_path(service)
        properties = subprocess.check_output(["/usr/bin/systemctl", "--user", "show", service,
            "--property=RuntimeMaxUSec", "--property=KillMode", "--property=Delegate"], timeout=10).decode()
        files.require(dict(row.split("=", 1) for row in properties.splitlines()) == {
            "RuntimeMaxUSec": "59min 10s", "KillMode": "control-group", "Delegate": "no"},
            "EG service wall/whole-subtree termination readback differs")
        # proc start-time includes Python startup and imports before this class.
        fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        self.started = int(fields[19]) / os.sysconf("SC_CLK_TCK")
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_sample: dict = {}
        self.lock = threading.Lock()
        self.next_sample = 0.0

    def checkpoint(self, stage: str) -> None:
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        elapsed = now - self.started
        files.require(0 <= elapsed < MAIN_WALL_SECONDS, "EG main wall budget exhausted")
        force = stage in {"periodic_watchdog", "before_source_preflight", "release_verified",
                          "durable_started_before_raw", "verification_and_persistence_complete",
                          "terminal_persisted", "publication_persisted", "service_complete"}
        if not force and now < self.next_sample:
            return
        with self.lock:
            sample = sample_cgroup(self.path)
            validate_resources(sample, elapsed)
            files.require(sample["cpu_usec"] < MAIN_CPU_USEC, "EG main CPU budget exhausted")
            files.require(os.getpid() in sample["processes"], "EG controller left the registered cgroup")
            self.last_sample = {**sample, "elapsed_seconds": elapsed}
            self.next_sample = now + 0.25

    def _watch(self) -> None:
        while not self.stop_event.wait(0.25):
            try:
                self.checkpoint("periodic_watchdog")
            except BaseException:
                # No exception recovery may leave a compute child running.
                (self.path / "cgroup.kill").write_text("1")
                os._exit(125)

    def __enter__(self) -> BudgetGuard:
        self.checkpoint("before_source_preflight")
        files.require(self.last_sample["processes"] == [os.getpid()], "EG service already has another process")
        self.thread = threading.Thread(target=self._watch, name="eg-budget-watchdog", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
        files.require(not self.thread.is_alive(), "EG watchdog failed to stop")
        if exc_type is None:
            self.checkpoint("service_complete")
            files.require(self.last_sample["processes"] == [os.getpid()], "EG child survives completion")


def _git(root: Path, *arguments: str) -> str:
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(["/usr/bin/git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "index.threads=1",
                             "-C", str(root), *arguments], capture_output=True, check=True, timeout=30, env=environment)
    files.require(len(result.stdout) <= 4096, "EG source helper output exceeded")
    return result.stdout.decode().strip()


def verify_release(contract: RunContract, checkpoint: Callable[[str], None]) -> None:
    contract.validate()
    for name, pin in contract.source_hashes:
        files.require(files.file_digest(contract.worktree / name, 16 * files.MIB, checkpoint)[0] == pin,
                      "EG exact source bytes differ")
    for name, pin, maximum in contract.runtime_files:
        files.require(files.file_digest(Path(name), maximum, checkpoint)[0] == pin, "EG runtime bytes differ")
    files.require(_git(contract.worktree, "rev-parse", "HEAD") == contract.merge_commit
                  and _git(contract.worktree, "rev-parse", "HEAD^{tree}") == contract.tree
                  and not _git(contract.worktree, "status", "--porcelain", "--untracked-files=no"),
                  "EG merged HEAD/tree/clean tracked source differs")
    parents = _git(contract.worktree, "show", "-s", "--format=%P", contract.merge_commit).split()
    files.require(len(parents) == 2 and parents[1] == contract.reviewed_commit and
                  _git(contract.worktree, "rev-parse", contract.reviewed_commit + "^{tree}") == contract.tree,
                  "EG final reviewed normal merge differs")
    _git(contract.worktree, "merge-base", "--is-ancestor", contract.merge_commit, "origin/main")
    checkpoint("release_verified")


def _artifacts(directory: Path, artifacts: tuple[Artifact, ...], checkpoint: Callable[[str], None]) -> list[dict]:
    files.require(artifacts and len(artifacts) <= 16 and len({item.name for item in artifacts}) == len(artifacts),
                  "EG immutable artifact manifest differs")
    result = []
    for item in artifacts:
        row = item.payload()
        files.require(files.file_digest(directory / item.name, item.maximum, checkpoint) == (item.sha256, item.size),
                      "EG artifact changed")
        result.append(row)
    return result


def _publication(contract: RunContract, directory: Path, checkpoint: Callable[[str], None]) -> dict:
    publication = _json(_read(directory / "publication.json"))
    files.require(publication["schema"] == "EG-unit-publication-v1" and publication["binding"] == contract.binding,
                  "EG original publication binding differs")
    journal = _read(directory / "discovery.jsonl")
    rows = [json.loads(line) for line in journal.splitlines()]
    files.require(len(rows) == 2 and [row["status"] for row in rows] == ["started", "completed"]
                  and rows[0]["run_id"] == rows[1]["run_id"] == publication["run_id"]
                  and rows[1]["result"] == publication["result"]
                  and all(row["parameters"] == contract.binding and row["study_id"] == contract.unit_id
                          and row["code_version"] == contract.merge_commit and row["window"] == WINDOW
                          and row["specification_sha256"] == contract.binding["registration_sha256"]
                          and row["promotion_allowed"] is False and row["promotion_trial_increment"] == 0 for row in rows)
                  and files.digest(journal) == publication["journal_sha256"], "EG original start/terminal journal differs")
    _artifacts(directory, tuple(Artifact(**row) for row in publication["result"]["artifacts"]), checkpoint)
    files.require(not (directory / "unresolved.json").exists(), "EG unresolved original attempt")
    validate_resources(publication["result"]["resources"], publication["result"]["resources"]["elapsed_seconds"])
    return publication


def validate_qualification(contract: RunContract, checkpoint: Callable[[str], None]) -> None:
    from dataclasses import replace

    files.require(not contract.invented_only, "qualification is only required for actual execution")
    directory = NATIVE_ROOT / contract.descriptor.unit_id / contract.qualification_attempt
    raw = _read(directory / "original-audit.json")
    files.require(files.digest(raw) == contract.qualification_audit_sha256, "EG qualification audit pin differs")
    audit = _json(raw)
    expected = replace(contract, invented_only=True, attempt=contract.qualification_attempt,
                       qualification_attempt=None, qualification_audit_sha256=None)
    publication = _publication(expected, directory, checkpoint)
    final_raw, exit_raw = _read(directory / "service-final.json"), _read(directory / "service-exit.json")
    final, exited = _json(final_raw), _json(exit_raw)
    files.require(final["schema"] == "EG-service-final-v1" and final["binding"] == expected.binding
                  and (final["service_result"], final["exit_code"], final["exit_status"]) == ("success", "exited", "0")
                  and exited["schema"] == "EG-service-exit-v1" and exited["binding"] == expected.binding
                  and exited["returncode"] == 0 and exited["service_final_sha256"] == files.digest(final_raw),
                  "EG original whole-service success evidence differs")
    validate_resources(final["resources"], final["resources"]["elapsed_seconds"])
    helper, audit_resources = exited["helper_resources"], audit["audit_resources"]
    validate_metadata_resources(helper["cgroup"], exited["service"], "controller")
    validate_metadata_resources(audit_resources["cgroup"], exited["service"], "audit")
    files.require(helper["address_space_cap_each"] == 64 * files.MIB and helper["cpu_cap_each_seconds"] == 20
                  and 0 <= helper["cpu_seconds"] < 40 and 0 < helper["self_peak_bytes"] <= 64 * files.MIB
                  and 0 < helper["child_peak_bytes"] <= 64 * files.MIB and 0 < helper["elapsed_seconds"] < 3580
                  and audit_resources["address_space_cap_each"] == 64 * files.MIB
                  and audit_resources["cpu_upper_bound_seconds"] == 20
                  and 0 <= audit_resources["cpu_seconds"] < 20 and 0 < audit_resources["elapsed_seconds"] < 20
                  and audit_resources["self_peak_bytes"] <= 64 * files.MIB
                  and audit_resources["child_peak_bytes"] <= 64 * files.MIB
                  and audit_resources["science_reexecuted"] is False, "EG outside resource reservations differ")
    files.require(audit == {"schema": "EG-original-audit-v1", "status": "PASS", "binding": expected.binding,
                           "publication_sha256": files.digest(_read(directory / "publication.json")),
                           "science_reexecuted": False, "full_geometry": FULL,
                           "service_final_sha256": files.digest(final_raw), "service_exit_sha256": files.digest(exit_raw),
                           "audit_resources": audit_resources}
                  and publication["result"]["geometry"] == FULL
                  and publication["result"]["independent_verification"].get("complete") is True,
                  "EG exact-source full invented original audit required")


@contextmanager
def _campaign_lock():
    files.safe_path(NATIVE_ROOT)
    info = NATIVE_ROOT.stat()
    files.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                  and stat.S_IMODE(info.st_mode) == 0o700, "EG native evidence root must be private")
    descriptor = os.open(NATIVE_ROOT / "campaign.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        files.require(os.fstat(descriptor).st_nlink == 1, "EG lock must be an exclusive regular inode")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _execute(contract: RunContract, unit: ClosedUnit, guard: BudgetGuard) -> dict:
    """Internal seam; formal callers must go through run_registered_unit."""
    unit.preflight()
    if not contract.invented_only:
        validate_qualification(contract, guard.checkpoint)
    directory = contract.directory
    files.exclusive_directory(directory)  # Existing failed/started/successful attempts cannot be reused.
    files.exclusive_bytes(directory / "unresolved.json", files.encode(contract.binding))
    files.exclusive_bytes(directory / "service-start.json", files.encode({"schema": "EG-service-start-v1",
        "binding": contract.binding, "started_boottime": guard.started, "service": contract.service}))

    def evaluate() -> dict:
        guard.checkpoint("durable_started_before_raw")
        output = unit.compute(directory, contract.binding, guard.checkpoint)
        files.require(type(output) is UnitOutput and _json(output.geometry) == FULL,
                      "EG full-geometry output required")
        summary = _json(output.summary)
        verification = unit.independent_verify(directory, output, contract.binding, guard.checkpoint)
        files.require(type(verification) is dict and verification.get("complete") is True,
                      "EG independent output verification incomplete")
        artifact_rows = _artifacts(directory, output.artifacts, guard.checkpoint)
        verify_release(contract, guard.checkpoint)
        guard.checkpoint("verification_and_persistence_complete")
        files.require(guard.last_sample["processes"] == [os.getpid()], "EG compute child still alive")
        return {"geometry": FULL, "summary": summary, "independent_verification": verification,
                "artifacts": artifact_rows, "resources": guard.last_sample}

    terminal = experiment_log.run_discovery_trial(study_id=contract.descriptor.unit_id,
        specification_sha256=contract.binding["registration_sha256"], parameters=contract.binding,
        window=WINDOW, evaluate=evaluate, runs_path=directory / "discovery.jsonl",
        code_version_provider=lambda: contract.merge_commit)
    guard.checkpoint("terminal_persisted")
    publication = {"schema": "EG-unit-publication-v1", "binding": contract.binding,
                   "run_id": terminal["run_id"], "result": terminal["result"],
                   "journal_sha256": files.file_digest(directory / "discovery.jsonl", MAX_METADATA, guard.checkpoint)[0]}
    files.exclusive_bytes(directory / "publication.json", files.encode(publication))
    guard.checkpoint("publication_persisted")
    (directory / "unresolved.json").unlink()
    files.fsync_directory(directory)
    return publication


def run_registered_unit(contract: RunContract, unit: ClosedUnit) -> dict:
    """Run one reviewed closed unit, with no bypass flag for a real invocation."""
    with BudgetGuard(contract.service) as guard, _campaign_lock():
        verify_release(contract, guard.checkpoint)
        return _execute(contract, unit, guard)
