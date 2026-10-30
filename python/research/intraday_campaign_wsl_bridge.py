"""One ordinary EC worker / WSL controller / GCP journal lifecycle.

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

from dataclasses import dataclass
import base64
import json
import math
import os
from pathlib import Path
import resource
import select
import shlex
import signal
import socket
import subprocess
import sys
import time
from typing import BinaryIO, Callable

from research import intraday_campaign_wsl_transport as wire


PROFILE = wire.ORDINARY300
HOST = "local_wsl_with_gcp_canonical_journal"
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

    def payload(self, kind: str = "INPUTS") -> dict:
        wire.require(type(self.manifest) is tuple and all(type(item) is InputFile for item in self.manifest),
                     "immutable input manifest required")
        inputs = wire.validate_input_manifest([item.metadata() for item in self.manifest])
        wire.require(wire.valid_sha(self.stream_sha256), "decoded stream pin missing")
        return {"kind": kind, "inputs": inputs, "stream_sha256": self.stream_sha256,
                "geometry": canonical(self.geometry)}

    @classmethod
    def from_payload(cls, value: dict, kind: str = "INPUTS") -> InputEnvelope:
        wire.require(set(value) == {"kind", "inputs", "stream_sha256", "geometry"}
                     and value["kind"] == kind, "input envelope fields differ")
        manifest = wire.validate_input_manifest(value["inputs"])
        result = cls(tuple(InputFile(**item) for item in manifest), value["stream_sha256"],
                     wire.encode(value["geometry"]))
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

    ``ssh_argv`` is the existing SSH binary, pinned key/known-hosts path options
    and destination, without a remote command. ``remote_prefix`` ends in the
    remote private entrypoint and ``coordinator``; config is appended as one
    base64 argument. Neither field contains credential contents.
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


@dataclass(frozen=True)
class UnitHooks:
    load_plan: Callable[[dict], UnitPlan]
    make_invented: Callable[[Path, int, Checkpoint], InputEnvelope]
    compute_verify: Callable[[Path, InputEnvelope, Checkpoint], OutputReceipt]


def _relative_source(name: str) -> bool:
    return (type(name) is str and bool(name) and "\\" not in name and "\x00" not in name
            and not name.startswith("/") and all(part not in ("", ".", "..") for part in name.split("/")))


def validate_plan(plan: UnitPlan) -> None:
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
    wire.validate_input_manifest([pin.metadata(1) for pin in plan.input_pins])
    wire.require(wire.valid_sha(plan.decoded_stream_sha256), "registered decoded pin differs")
    window = canonical(plan.window)
    wire.require(set(window) == {"symbol", "interval", "start", "end"}
                 and all(type(value) is str and value for value in window.values()), "scientific window differs")
    geometry = canonical(plan.full_geometry)
    wire.require(type(geometry.get("sessions")) is int and geometry["sessions"] > 0,
                 "full registered session geometry required")
    canonical(plan.candidate_bounds)
    wire.require(type(plan.output_filename) is str and Path(plan.output_filename).name == plan.output_filename
                 and plan.output_filename not in ("", ".", "..", "EA-A", "EA-B", "CC", "publication.json",
                     "unresolved.json", "source-manifest.json", "prepared.json", "worker-budget.json",
                     "worker-failure.json", "bridge-failure.json", "completed-ack.json")
                 and type(plan.output_max_bytes) is int and 0 < plan.output_max_bytes <= 512 * wire.MIB,
                 "registered single output differs")
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
    wire.require(type(plan.ssh_argv) is tuple and type(plan.remote_prefix) is tuple
                 and all(type(arg) is str and arg and "\x00" not in arg
                         for arg in (*plan.ssh_argv, *plan.remote_prefix)), "immutable private SSH metadata required")
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
    wire.require(type(config.get("invented_only")) is bool
                 and config.get("automatic_retries") == 0 and config.get("wall_seconds") == 1200,
                 "single-attempt ordinary contract differs")
    wire.require(config.get("resource_profile") == PROFILE.id, "ordinary resource profile required")
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
    return wire.digest(wire.encode({"hostname": socket.gethostname(), "kernel": list(os.uname()),
        "python": sys.version, "executable": str(Path(sys.executable).resolve())}))


class GitHelpers:
    """Cumulative children reservation belongs to this role, never another grant."""
    def __init__(self, root: Path, role: str) -> None:
        wire.require(role in PROFILE.helper_limits(), "role has no Git helper reserve")
        self.root, self.role = root, role
        self.receipt = {"cpu_seconds": 0.0, "count": 0, "readbacks": []}

    def git(self, *args: str) -> str:
        caps = PROFILE.helper_limits()[self.role]
        remaining = int(caps["cpu_seconds"] - self.receipt["cpu_seconds"])
        wire.require(remaining >= 1, "source helper CPU reserve exhausted")
        read_fd, write_fd = os.pipe()
        def preexec() -> None:
            os.close(read_fd)
            resource.setrlimit(resource.RLIMIT_CPU, (remaining,) * 2)
            resource.setrlimit(resource.RLIMIT_AS, (caps["address_space_bytes"],) * 2)
            os.write(write_fd, wire.encode({"cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU)[1],
                "address_space_bytes": resource.getrlimit(resource.RLIMIT_AS)[1]}))
            os.close(write_fd)
        process = None
        try:
            process = subprocess.Popen(["git", "-C", str(self.root), *args], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, preexec_fn=preexec, pass_fds=(write_fd,), bufsize=0)
            os.close(write_fd)
            write_fd = -1
            readback = json.loads(os.read(read_fd, 4096))
            wire.require(readback == {"cpu_seconds": remaining, "address_space_bytes": caps["address_space_bytes"]},
                         "source helper hard-limit readback differs")
            raw = bytearray()
            while block := process.stdout.read(4097 - len(raw)):
                raw.extend(block)
                wire.require(len(raw) <= 4096, "source helper output exceeded")
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            self.receipt["cpu_seconds"] += usage.ru_utime + usage.ru_stime
            self.receipt["count"] += 1
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
    wire.require(root == (plan.local_worktree if side == "local" else plan.remote_worktree),
                 "registered source checkout differs")
    hashes = config["source_hashes"]
    wire.require(set(hashes) == set(plan.source_names), "source map missing/extra")
    manifest = {name: wire.file_digest(root / name, 16 * wire.MIB, lambda _: None)[0]
                for name in plan.source_names}
    wire.require(manifest == hashes, "authentic source manifest differs")
    identity = {"source_manifest_sha256": wire.digest(wire.encode(manifest)), "runtime_sha256": runtime_sha(),
                "machine_sha256": machine_sha(), "merge_commit": config["merge_commit"], "tree": config["tree"],
                "execution_host": HOST, "resource_profile": PROFILE.id}
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


def worker_budget():
    from research.research_budget import ResearchBudget
    caps = PROFILE.role_limits()["worker"]
    return ResearchBudget(cpu_seconds=caps["cpu_seconds"], address_space_bytes=caps["address_space_bytes"])


def _sample(role: str, helpers: GitHelpers | None = None) -> dict:
    result = wire.role_sample(role, helpers.receipt if helpers else None, profile=PROFILE)
    wire.validate_role_sample(result, role, profile=PROFILE)
    return result


def _bounded_counts(counts: object, bounds: object) -> None:
    if type(bounds) is dict:
        wire.require(type(counts) is dict and set(counts) == set(bounds), "candidate count coordinates differ")
        for key in bounds:
            _bounded_counts(counts[key], bounds[key])
    else:
        wire.require(type(bounds) is int and type(counts) is int and 0 <= counts <= bounds,
                     "candidate bounds exceeded; no trimming")


def validate_result(result: dict, envelope: InputEnvelope, plan: UnitPlan) -> None:
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


def actual_input_metadata(plan: UnitPlan, phases: wire.WorkerPhases) -> InputEnvelope:
    # This is the only actual source stat. Its capability requires a durable ACK.
    phases.input_access()
    manifest = tuple(InputFile(**pin.metadata(wire.regular(Path(pin.original_path)).st_size))
                     for pin in plan.input_pins)
    envelope = InputEnvelope(manifest, plan.decoded_stream_sha256, plan.full_geometry)
    envelope.payload()
    return envelope


def _actual_envelope(envelope: InputEnvelope, plan: UnitPlan) -> None:
    wire.require(tuple(InputPin(item.name, item.original_path, item.sha256, item.maximum)
                       for item in envelope.manifest) == plan.input_pins
                 and envelope.stream_sha256 == plan.decoded_stream_sha256
                 and envelope.geometry == plan.full_geometry, "actual source/decoded pins or geometry differ")


def _invented_envelope(envelope: InputEnvelope, plan: UnitPlan, sessions: int) -> None:
    envelope.payload()
    geometry, full = canonical(envelope.geometry), canonical(plan.full_geometry)
    wire.require(set(geometry) == set(full) and geometry["sessions"] == sessions,
                 "invented geometry coordinates differ")
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
    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        setattr(socket.socket, method, no_network)
    socket.create_connection = no_network


def worker(config: dict, plan: UnitPlan, hooks: UnitHooks, root: Path,
           incoming: BinaryIO, outgoing: BinaryIO) -> int:
    wire.fixed_deadline(config["deadline_epoch"])
    wire.apply_role_limits("worker", profile=PROFILE)
    restrict_worker_network()
    budget = worker_budget()
    verify_sources(config, plan, root, "local")
    wire.require(config.get("local_pipe_test", False) or "microsoft" in os.uname().release.lower(),
                 "WSL runtime required")
    directory = Path(config["cache"])
    wire.native_cache(directory, root)
    require_free_disk(directory.parent)
    import fcntl
    wire.safe_path(directory.parent / "ec-ordinary-worker.lock")
    lock_fd = os.open(directory.parent / "ec-ordinary-worker.lock",
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
            envelope = InputEnvelope.from_payload(wire.read_header(incoming, "INPUTS"))
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
            validate_result(result, envelope, plan)
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
            finished, sample = budget.finish(), _sample("worker")
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
                "error_type": type(error).__name__, "automatic_retries": 0, "canonical_truth": "GCP journal only"}))
            raise
    finally:
        os.close(lock_fd)


def journal_record(log: Path, offset: int, status: str, run_id: str | None = None) -> tuple[dict, str, int]:
    wire.safe_path(log)
    before = wire.regular(log)
    descriptor = os.open(log, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before),
                     "canonical journal changed before read")
        stream.seek(offset)
        raw = stream.read(32769)
    wire.require(len(raw) <= 32768, "canonical compact span exceeded")
    lines = raw.splitlines(keepends=True)
    wire.require(len(lines) == (1 if status == "started" else 2), "canonical journal span count differs")
    records = [json.loads(line) for line in lines]
    wire.require(records[0]["status"] == "started" and records[-1]["status"] == status
                 and (run_id is None or all(item["run_id"] == run_id for item in records)),
                 "durable journal transition differs")
    return records[-1], wire.digest(lines[-1]), offset + len(raw)


def validate_qualification(config: dict, plan: UnitPlan) -> None:
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
    for key in ("source_hashes", "local_identity", "remote_identity"):
        wire.require(publication[key] == config[key], "qualification source/private/runtime/host differs")
    wire.require(publication["resource_profile"] == PROFILE.id and publication["unit_id"] == plan.unit_id
                 and publication["invented_only"] is True and publication["geometry"] == canonical(plan.full_geometry)
                 and publication["verification_complete"] is True
                 and publication["independent_verification"].get("verified") is True
                 and publication["status"] == "completed" and publication["full_bridge"] is True
                 and publication["local_pipe_test"] is False, "full same-source bridge qualification required")
    wire.aggregate_roles(publication["aggregate_budget"]["samples"], profile=PROFILE)
    _bounded_counts(publication["candidate_counts"], canonical(plan.candidate_bounds))
    wire.require(wire.file_digest(path / plan.output_filename, plan.output_max_bytes, lambda _: None)
                 == (publication["output_sha256"], publication["output_bytes"]), "qualification remote output differs")
    journal = path / "invented-experiments.jsonl"
    wire.require(publication["invented_journal"] == str(journal), "original invented journal required")
    record, pin, end = journal_record(journal, publication["canonical_offsets"][0], "completed", publication["run_id"])
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
                 and record["parameters"]["resource_profile"] == PROFILE.id, "qualification canonical terminal differs")


def coordinator(config: dict, plan: UnitPlan, root: Path, incoming: BinaryIO, outgoing: BinaryIO) -> int:
    wire.fixed_deadline(config["deadline_epoch"])
    wire.apply_role_limits("coordinator", profile=PROFILE)
    helpers = GitHelpers(root, "coordinator")
    verify_sources(config, plan, root, "remote", helpers)
    out, log = Path(config["output"]), Path(config["journal"])
    wire.native_cache(out, root)
    wire.safe_path(log)
    if not config["invented_only"]:
        validate_qualification(config, plan)
    require_free_disk(out.parent)
    wire.exclusive_directory(out)
    wire.exclusive_bytes(out / "source-manifest.json", wire.encode(config["source_hashes"]))
    marker = out / "unresolved.json" if config["invented_only"] else plan.global_marker
    locks = ([out.parent / "invented-research.lock", out.parent / "ec-campaign.lock"]
             if config["invented_only"] else list(plan.research_locks))
    holder: dict = {}
    with wire.research_locks(locks, marker, config["attempt"]):
        offset = wire.regular(log).st_size if log.exists() else 0
        def checkpoint(stage: str) -> None:
            _sample("coordinator", helpers)
        def evaluate() -> dict:
            started, pin, _ = journal_record(log, offset, "started")
            holder["run_id"] = started["run_id"]
            ack = {"kind": "STARTED", "identity": config["remote_identity"], "run_id": started["run_id"],
                   "record_sha256": pin, "offset": offset, "deadline_epoch": config["deadline_epoch"]}
            wire.send_header(outgoing, ack)
            phases = wire.WorkerPhases(config["remote_identity"])
            phases.accept_started(ack)
            if config["invented_only"]:
                envelope = InputEnvelope.from_payload(wire.read_header(incoming, "PROFILE_INPUTS"), "PROFILE_INPUTS")
                _invented_envelope(envelope, plan, config["sessions"])
                generated = out / "invented-inputs"
                wire.exclusive_directory(generated)
                for item in envelope.manifest:
                    wire.receive_file(incoming, generated / item.name, item.metadata(), checkpoint)
                export_paths = {item.name: generated / item.name for item in envelope.manifest}
            else:
                envelope = actual_input_metadata(plan, phases)
                export_paths = {item.name: Path(item.original_path) for item in envelope.manifest}
            wire.send_header(outgoing, envelope.payload())
            for item in envelope.manifest:
                wire.send_file(outgoing, export_paths[item.name], item.metadata(), checkpoint)
            produced = wire.read_header(incoming, "RESULT")
            wire.require(set(produced) == {"kind", "result", "file"}, "result frame fields differ")
            result, output_file = produced["result"], produced["file"]
            validate_result(result, envelope, plan)
            expected_output = {"name": plan.output_filename, "original_path": str(Path(config["cache"]) / plan.output_filename),
                "sha256": result["output_sha256"], "size": result["output_bytes"], "maximum": plan.output_max_bytes}
            wire.require(output_file == expected_output, "registered output file metadata differs")
            wire.receive_file(incoming, out / plan.output_filename, output_file, checkpoint)
            done, control = wire.read_header(incoming, "WORKER_DONE"), wire.read_header(incoming, "CONTROL")
            for item in envelope.manifest:
                wire.require(wire.file_digest(export_paths[item.name], item.maximum, checkpoint)
                             == (item.sha256, item.size), "original export changed during compute")
            verify_sources(config, plan, root, "remote", helpers)
            aggregate = wire.aggregate_roles({"worker": done["sample"], "coordinator": _sample("coordinator", helpers),
                "controller": control["controller"], "ssh_client": control["ssh_client"]}, profile=PROFILE)
            caps = PROFILE.role_limits()["worker"]
            wire.require(done["budget"]["status"] == "complete" and done["budget"]["violations"] == []
                         and done["budget"]["limits"] == {**caps, "wall_seconds": 1200, "nice": 10}
                         and "operator_cpu_scope" not in done["budget"], "ordinary worker budget not complete")
            holder.update(result=result, aggregate=aggregate)
            wire.exclusive_bytes(out / "prepared.json", wire.encode({"result": result, "aggregate_budget": aggregate}))
            return {"output_sha256": result["output_sha256"], "output_bytes": result["output_bytes"],
                    "verification_complete": True, "independent_verification": result["independent_verification"],
                    "aggregate_budget": aggregate, "automatic_retries": 0}
        try:
            # The controller authenticates the local Git merge before relaying
            # HELLO. The remote independently checks its own merge above. A
            # worker has no extra Git subprocess reservation of its own.
            wire.require(wire.read_header(incoming, "HELLO") == {"kind": "HELLO", "identity": config["local_identity"]},
                         "worker source/host/runtime differs")
            from research import experiment_log
            record = experiment_log.run_discovery_trial(study_id=plan.unit_id,
                specification_sha256=config["source_hashes"][plan.specification_path],
                parameters={"invented_only": config["invented_only"], "execution_host": HOST,
                    "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
                    "attempt": config["attempt"], "resource_profile": PROFILE.id, "automatic_retries": 0},
                window=canonical(plan.window), evaluate=evaluate, runs_path=log,
                code_version_provider=lambda: helpers.git("rev-parse", "HEAD"))
            terminal, pin, end = journal_record(log, offset, "completed", record["run_id"])
            publication = {"status": "completed", "unit_id": plan.unit_id, "resource_profile": PROFILE.id,
                "invented_only": config["invented_only"], "full_bridge": not config.get("local_pipe_test", False),
                "local_pipe_test": config.get("local_pipe_test", False), "source_hashes": config["source_hashes"],
                "local_identity": config["local_identity"], "remote_identity": config["remote_identity"],
                "run_id": record["run_id"], "canonical_offsets": [offset, end], "terminal_record_sha256": pin,
                "aggregate_budget": holder["aggregate"], "coordinator_after_terminal": _sample("coordinator", helpers),
                "invented_journal": str(log) if config["invented_only"] else None, **holder["result"]}
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
                       "partial_evidence_preserved": True, "unresolved_marker": str(marker)}
            try:
                terminal, pin, _ = journal_record(log, offset, "failed", holder.get("run_id"))
                failure.update(status="failed", terminal_record_sha256=pin)
            except BaseException:
                pass
            wire.exclusive_bytes(out / "bridge-failure.json", wire.encode(failure))
            raise


def source_probe(config: dict, plan: UnitPlan, root: Path, outgoing: BinaryIO) -> int:
    """Cold source bootstrap only: no input, output, journal or marker access."""
    wire.fixed_deadline(config["deadline_epoch"])
    wire.apply_role_limits("coordinator", profile=PROFILE)
    helpers = GitHelpers(root, "coordinator")
    verified = verify_sources(config, plan, root, "remote", helpers)
    wire.write_all(outgoing, wire.encode({"status": "coordinator_source_probe_complete", "study_id": plan.unit_id,
        "identity": verified, "source_files": len(plan.source_names), "role_sample": _sample("coordinator", helpers),
        "market_input_accesses": 0, "journal_accesses": 0, "output_directories_created": 0,
        "marker_accesses": 0, "qualifies_actual": False}))
    outgoing.flush()
    return 0


def sample_child(pid: int) -> dict:
    role = "ssh_client"
    caps = PROFILE.role_limits()[role]
    base = Path("/proc") / str(pid)
    fields = (base / "stat").read_text().split(") ", 1)[1].split()
    limits = (base / "limits").read_text().splitlines()
    def limit(label: str) -> list[int]:
        row = next(line for line in limits if line.startswith(label)).split()
        return [int(row[-3]), int(row[-2])]
    result = {"role": role, "pid": pid, "resource_profile": PROFILE.id, "limits": caps, "parent_limits": caps,
        "cpu_self_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
        "children_cpu_diagnostic_seconds": 0, "cpu_counting": "/proc dedicated SSH pid; server daemon excluded",
        "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "address_space_bytes": int((base / "statm").read_text().split()[0]) * os.sysconf("SC_PAGE_SIZE"),
        "readback": {"rlimit_cpu": limit("Max cpu time"), "rlimit_as": limit("Max address space"),
            "nice": os.getpriority(os.PRIO_PROCESS, pid), "affinity": sorted(os.sched_getaffinity(pid)),
            "oom_score_adj": (base / "oom_score_adj").read_text().strip()}}
    wire.validate_role_sample(result, role, profile=PROFILE)
    return result


def process_command(mode: str, config: dict, plan: UnitPlan, root: Path) -> list[str]:
    return [str(Path(sys.executable).resolve()), "-I", "-S", str(root / plan.private_entrypoint), mode,
            base64.b64encode(wire.encode(config)).decode()]


def remote_command(config: dict, plan: UnitPlan) -> list[str]:
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


def _terminate(process: subprocess.Popen, statuses: dict, name: str) -> None:
    if process.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        until = time.monotonic() + 2
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


def controller(config: dict, plan: UnitPlan, root: Path) -> int:
    # Child caps must be set before exec; tightening this parent before fork
    # would make the worker's larger inherited hard AS impossible to install.
    wire.fixed_deadline(config["deadline_epoch"])
    diagnostic = Path(config["diagnostics"])
    wire.native_cache(diagnostic, root)
    require_free_disk(diagnostic.parent)
    wire.exclusive_directory(diagnostic)
    helpers, processes, statuses, stderr_handles = GitHelpers(root, "controller"), {}, {}, []
    try:
        for name in ("worker", "ssh"):
            fd = os.open(diagnostic / (name + "-stderr.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            stderr_handles.append(os.fdopen(fd, "wb"))
        processes["worker"] = subprocess.Popen(process_command("worker", config, plan, root), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=stderr_handles[0], preexec_fn=lambda: wire.apply_role_limits("worker", profile=PROFILE),
            start_new_session=True, bufsize=0)
        if config.get("local_pipe_test", False):
            wire.require(config["invented_only"] is True, "local pipe is invented only")
            processes["ssh"] = subprocess.Popen(process_command("coordinator", config, plan, root), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=stderr_handles[1], preexec_fn=lambda: wire.apply_role_limits("coordinator", profile=PROFILE),
                start_new_session=True, bufsize=0)
        else:
            processes["ssh"] = subprocess.Popen(remote_command(config, plan), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=stderr_handles[1], preexec_fn=lambda: wire.apply_role_limits("ssh_client", profile=PROFILE),
                start_new_session=True, bufsize=0)
        wire.apply_role_limits("controller", profile=PROFILE)
        verify_sources(config, plan, root, "local", helpers)
        worker_process, remote_process = processes["worker"], processes["ssh"]
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
                    wire.require(received_terminal is not None and accepted is not None,
                                 "bridge disconnect before complete acceptance; no retry")
                    del active[source]
                    continue
                relay_frame(source, active[source], header, plan)
                if source is worker_process.stdout and header["kind"] == "WORKER_DONE":
                    verify_sources(config, plan, root, "local", helpers)
                    # Local smoke tests have no real SSH role and cannot qualify.
                    ssh = config["test_ssh_sample"] if config.get("local_pipe_test", False) else sample_child(remote_process.pid)
                    wire.validate_role_sample(ssh, "ssh_client", profile=PROFILE)
                    wire.send_header(remote_process.stdin, {"kind": "CONTROL", "controller": _sample("controller", helpers),
                                                            "ssh_client": ssh})
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
                "peak_rss_kib": usage.ru_maxrss, "measurement": "wait4 dedicated child/descendants; diagnostic only"}
        wire.require(received_terminal is not None and accepted is not None
                     and all(item["returncode"] == 0 for item in statuses.values()),
                     "whole bridge child exit/terminal incomplete; outcome unknown")
        wire.exclusive_bytes(diagnostic / "process-receipt.json", wire.encode({"status": "completed_ack_received",
            "terminal": received_terminal, "children": statuses, "controller": _sample("controller", helpers),
            "canonical_truth": "GCP journal only", "automatic_retries": 0}))
        return 0
    except BaseException as error:
        for name, process in processes.items():
            _terminate(process, statuses, name)
        wire.exclusive_bytes(diagnostic / "process-failure.json", wire.encode({"status": "unknown",
            "error_type": type(error).__name__, "automatic_retries": 0, "children": statuses,
            "canonical_truth": "GCP journal only; inspect remotely without replay"}))
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


def run_role(mode: str, config: dict, hooks: UnitHooks, *, root: Path,
             incoming: BinaryIO | None = None, outgoing: BinaryIO | None = None) -> int:
    """Thin private entrypoint API; apply role caps before loading its plan.

    The controller bootstrap creates its two children before tightening itself;
    the source-only same-PID launcher authenticates it before invoking this API.
    The private module must not import producer/fixture code at import time.
    """
    wire.require(mode in ("worker", "coordinator", "controller", "source-probe"), "explicit ordinary bridge role required")
    wire.fixed_deadline(config["deadline_epoch"])
    if mode != "controller":
        wire.apply_role_limits("coordinator" if mode == "source-probe" else mode, profile=PROFILE)
    if mode == "worker":
        restrict_worker_network()
    plan = hooks.load_plan(config)
    validate_config(config, plan)
    incoming = incoming if incoming is not None else sys.stdin.buffer
    outgoing = outgoing if outgoing is not None else sys.stdout.buffer
    if mode == "worker":
        return worker(config, plan, hooks, root, incoming, outgoing)
    if mode == "coordinator":
        return coordinator(config, plan, root, incoming, outgoing)
    if mode == "source-probe":
        return source_probe(config, plan, root, outgoing)
    return controller(config, plan, root)
