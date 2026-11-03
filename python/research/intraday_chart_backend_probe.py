"""One offline EF backend diagnostic, using only fixed invented tensors.

This is not an ED retry, market fit, full-geometry qualification, or installer.
Torch is imported only in the bounded child, after the controller barrier.
Python socket interception is the network boundary; trusted native PyTorch
internals are not kernel-isolated. The retained environment is read-only.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import sys
import time

MIB = 1024**2
WORKER_CPU = 50
CONTROLLER_CPU = 10
WORKER_AS = 3072 * MIB
CONTROLLER_AS = 128 * MIB
WALL_SECONDS = 120
OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "var"
TORCH_VERSION = "2.7.0+cpu"
SEED = 20261103
THREAD_ENV = {name: "1" for name in (
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    "MALLOC_ARENA_MAX",
)}
BOOTSTRAP = (
    "import hashlib,sys; p=sys.argv.pop(1); h=sys.argv.pop(1); "
    "b=open(p,'rb').read(); "
    "assert hashlib.sha256(b).hexdigest()==h,'probe source hash mismatch'; "
    "sys.argv[0]=p; exec(compile(b,p,'exec'),{'__name__':'__main__','__file__':p})"
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(MIB):
            value.update(chunk)
    return value.hexdigest()


def save(path: Path, value: object) -> None:
    """Exclusive receipts preserve successes and incomplete/failed attempts."""
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def read_pin(path: Path, expected_sha256: str) -> dict:
    raw = path.read_bytes()
    if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("runtime pin hash/size mismatch")
    pin = json.loads(raw)
    if pin["schema"] != "ef-retained-torch-runtime-v1" or pin["torch"] != TORCH_VERSION:
        raise ValueError("unapproved runtime pin")
    prefix = Path(pin["prefix"])
    if not prefix.is_absolute() or prefix.is_symlink() or not prefix.is_dir():
        raise ValueError("runtime prefix must be an existing absolute directory")
    if pin["interpreter"] != str(prefix / "venv/bin/python"):
        raise ValueError("interpreter is not the pinned retained environment")
    if pin["site_packages"] != str(prefix / "venv/lib/python3.12/site-packages"):
        raise ValueError("unexpected site-packages directory")
    for name, expected in pin["files"].items():
        path = prefix / name
        if not path.resolve().is_relative_to(prefix.resolve()) or path.is_symlink():
            raise ValueError("runtime payload escapes prefix")
        if digest(path) != expected:
            raise ValueError("runtime payload hash mismatch: " + name)
    interpreter = Path(pin["interpreter"])
    if digest(interpreter) != pin["interpreter_sha256"]:
        raise ValueError("interpreter hash mismatch")
    return pin


def clean_environment(directory: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **THREAD_ENV,
        "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "",
        "ROCR_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(directory), "TMPDIR": str(directory / "tmp"),
        "TORCH_HOME": str(directory / "torch-home"),
        "TORCHINDUCTOR_CACHE_DIR": str(directory / "inductor-cache"),
    }


def restrict_common() -> None:
    if sys.platform != "linux" or len(os.listdir("/proc/self/task")) != 1:
        raise RuntimeError("requires a single-threaded Linux launcher")
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    nice = os.getpriority(os.PRIO_PROCESS, 0)
    if nice > 10:
        raise RuntimeError("cannot restore the exact approved nice value")
    os.nice(10 - nice)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MIB, MIB))


def prepare_controller() -> None:
    restrict_common()
    # Parent soft caps are active before fork; only the child needs higher hard
    # ceilings. The pipe stays closed until the parent tightens its hard caps.
    resource.setrlimit(resource.RLIMIT_AS, (CONTROLLER_AS, WORKER_AS))
    resource.setrlimit(resource.RLIMIT_CPU, (CONTROLLER_CPU, WORKER_CPU))


def tighten_controller() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (CONTROLLER_AS, CONTROLLER_AS))
    resource.setrlimit(resource.RLIMIT_CPU, (CONTROLLER_CPU, CONTROLLER_CPU))


def prepare_worker() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (WORKER_AS, WORKER_AS))
    resource.setrlimit(resource.RLIMIT_CPU, (WORKER_CPU, WORKER_CPU))


def readback() -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "pid": os.getpid(), "cpu_seconds": usage.ru_utime + usage.ru_stime,
        "peak_rss_kib": usage.ru_maxrss,
        "as_bytes": int(Path("/proc/self/statm").read_text().split()[0]) * os.sysconf("SC_PAGE_SIZE"),
        "rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
        "rlimit_cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
        "affinity": sorted(os.sched_getaffinity(0)),
        "nice": os.getpriority(os.PRIO_PROCESS, 0),
    }


def assert_worker_limits(value: dict) -> None:
    if (value["rlimit_as"] != [WORKER_AS, WORKER_AS]
            or value["rlimit_cpu"] != [WORKER_CPU, WORKER_CPU]
            or len(value["affinity"]) != 1 or value["nice"] != 10
            or value["as_bytes"] > WORKER_AS or value["cpu_seconds"] >= WORKER_CPU):
        raise RuntimeError("worker limit readback mismatch or exhausted budget")
    if any(os.environ.get(name) != value for name, value in THREAD_ENV.items()):
        raise RuntimeError("native thread environment mismatch")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("GPU environment mismatch")


def deny_network(*args: object, **kwargs: object) -> None:
    raise RuntimeError("EF invented backend worker forbids network access")


def restrict_network() -> None:
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        setattr(socket.socket, name, deny_network)
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    try:
        socket.create_connection(("127.0.0.1", 9))
    except RuntimeError:
        return
    raise RuntimeError("network self-check failed")


def tiny_training(directory: Path) -> dict:
    """Trusted PyTorch kernels; no hand-written deep-learning substitute."""
    import torch

    if torch.__version__ != TORCH_VERSION or torch.version.cuda is not None:
        raise RuntimeError("unexpected torch backend")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)
    if torch.get_num_threads() != 1 or torch.get_num_interop_threads() != 1:
        raise RuntimeError("torch thread count mismatch")
    save(directory / "cold-import.json", {"torch": torch.__version__, "source": torch.__file__, "resources": readback()})

    class TinyCausal(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.first = torch.nn.Conv1d(6, 8, 3)
            self.second = torch.nn.Conv1d(8, 8, 3, dilation=2)
            self.head = torch.nn.Conv1d(8, 1, 1)

        def forward(self, values):
            values = torch.relu(self.first(torch.nn.functional.pad(values, (2, 0))))
            values = torch.relu(self.second(torch.nn.functional.pad(values, (4, 0))))
            return self.head(values)

    values = torch.tensor([
        [[(row * 7 + channel * 3 + tick) % 17 / 17 - 0.5 for tick in range(30)]
         for channel in range(6)] for row in range(32)
    ], dtype=torch.float32, device="cpu")
    labels = values[:, :1, :] * 0.5 + values[:, 1:2, :] * 0.1
    model = TinyCausal().to(device="cpu", dtype=torch.float32)
    if sum(parameter.numel() for parameter in model.parameters()) != 361:
        raise RuntimeError("frozen tiny architecture changed")
    optimizer = torch.optim.SGD(model.parameters(), lr=0.05, momentum=0, weight_decay=0)
    initial = [parameter.detach().clone() for parameter in model.parameters()]
    losses = []
    for _ in range(3):
        optimizer.zero_grad(set_to_none=True)
        output = model(values)
        if tuple(output.shape) != (32, 1, 30) or not torch.isfinite(output).all().item():
            raise RuntimeError("invalid invented output")
        loss = torch.nn.functional.mse_loss(output, labels)
        if not math.isfinite(loss.item()):
            raise RuntimeError("nonfinite loss")
        losses.append(loss.item())
        loss.backward()
        if any(p.grad is None or not torch.isfinite(p.grad).all().item() for p in model.parameters()):
            raise RuntimeError("missing or nonfinite gradient")
        optimizer.step()
    with torch.no_grad():
        expected = model(values)
        final_loss = torch.nn.functional.mse_loss(expected, labels).item()
        if not math.isfinite(final_loss) or final_loss >= losses[0] or not any(
            not torch.equal(a, b) for a, b in zip(initial, model.parameters(), strict=True)
        ):
            raise RuntimeError("fixed invented learning check failed")
        future = values.clone()
        future[:, :, 15:] += 3
        if not torch.equal(expected[:, :, :15], model(future)[:, :, :15]):
            raise RuntimeError("future perturbation broke causality")
        artifact = {"schema": "ef-tiny-invented-causal-v1", "seed": SEED,
                    "state": {name: tensor.tolist() for name, tensor in model.state_dict().items()},
                    "predictions": expected.tolist(), "step_losses": losses, "final_loss": final_loss}
        artifact_path = directory / "invented-artifact.json"
        save(artifact_path, artifact)
        restored = json.loads(artifact_path.read_text())
        replay = TinyCausal().to(device="cpu", dtype=torch.float32)
        replay.load_state_dict({name: torch.tensor(value, dtype=torch.float32) for name, value in restored["state"].items()})
        if not torch.equal(replay(values), torch.tensor(restored["predictions"], dtype=torch.float32)):
            raise RuntimeError("numeric artifact replay differs")
    return {"synthetic_steps": 3, "market_fits": 0, "final_loss": final_loss,
            "initial_loss": losses[0], "parameters": 361, "artifact_sha256": digest(artifact_path),
            "future_perturbation_passed": True, "artifact_replay_passed": True}


def worker(args: argparse.Namespace) -> int:
    directory = Path(args.output)
    stage = "barrier"
    try:
        # An orphaned child still has a kernel wall timer, even if its parent
        # dies after releasing the barrier. SIGALRM's default terminates it.
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(WALL_SECONDS)
        with os.fdopen(args.barrier_fd, "rb", buffering=0) as barrier:
            if barrier.read(1) != b"1":
                raise RuntimeError("controller resource barrier did not open")
        assert_worker_limits(readback())
        stage = "runtime_authentication"
        pin = read_pin(Path(args.runtime_pin), args.runtime_sha256)
        if Path(sys.executable) != Path(pin["interpreter"]):
            raise RuntimeError("worker interpreter path differs from pin")
        restrict_network()
        sys.path.insert(0, pin["site_packages"])
        stage = "cold_import_and_invented_training"
        save(directory / "before-import.json", {"resources": readback(), "market_fits": 0})
        result = tiny_training(directory)
        assert_worker_limits(readback())
        save(directory / "worker-success.json", {"status": "ready_for_this_tiny_case_only", "result": result,
             "resources": readback(), "full_geometry_qualified": False, "market_fits": 0})
        return 0
    except Exception as exc:
        save(directory / "worker-failure.json", {"stage": stage, "type": type(exc).__name__,
             "message": str(exc), "resources": readback(), "market_fits": 0})
        return 1


def worker_command(args: argparse.Namespace, interpreter: str, barrier_fd: int) -> list[str]:
    return [interpreter, "-I", "-S", "-B", "-c", BOOTSTRAP,
            str(Path(__file__).resolve()), args.source_sha256,
            "--worker", "--barrier-fd", str(barrier_fd), "--output", args.output,
            "--runtime-pin", args.runtime_pin, "--runtime-sha256", args.runtime_sha256,
            "--source-sha256", args.source_sha256]


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def controller(args: argparse.Namespace) -> int:
    started = time.monotonic()
    directory = Path(args.output)
    if (not directory.is_absolute() or not directory.name.startswith("ef-backend-")
            or directory.parent.resolve() != OUTPUT_ROOT.resolve()):
        raise ValueError("use a new absolute workspace var/ef-backend-* output directory")
    directory.mkdir(mode=0o700, exist_ok=False)
    terminal = {"status": "failed", "market_fits": 0, "automatic_retry": False,
                "full_geometry_qualified": False, "source_sha256": args.source_sha256,
                "runtime_pin_sha256": args.runtime_sha256}
    process = None
    read_fd = write_fd = None
    try:
        prepare_controller()
        if digest(Path(__file__).resolve()) != args.source_sha256:
            raise ValueError("controller source hash mismatch")
        # Only small pin JSON here. Payload hashing belongs to the worker's CPU.
        raw = Path(args.runtime_pin).read_bytes()
        if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != args.runtime_sha256:
            raise ValueError("runtime pin hash/size mismatch")
        pin = json.loads(raw)
        if pin["schema"] != "ef-retained-torch-runtime-v1" or pin["torch"] != TORCH_VERSION:
            raise ValueError("unapproved runtime pin")
        prefix = Path(pin["prefix"])
        if directory.resolve().is_relative_to(prefix.resolve()):
            raise ValueError("output must not modify the preserved runtime")
        if pin["interpreter"] != str(prefix / "venv/bin/python"):
            raise ValueError("unexpected interpreter path")
        if digest(Path(pin["interpreter"])) != pin["interpreter_sha256"]:
            raise ValueError("interpreter hash mismatch before launch")
        for name in ("tmp", "torch-home", "inductor-cache"):
            (directory / name).mkdir(mode=0o700)
        save(directory / "controller-start.json", {**terminal, "worker_cpu_seconds": WORKER_CPU,
             "controller_cpu_seconds": CONTROLLER_CPU, "worker_as_bytes": WORKER_AS,
             "controller_as_bytes": CONTROLLER_AS, "wall_seconds": WALL_SECONDS})
        read_fd, write_fd = os.pipe()
        with (directory / "worker.stdout.txt").open("xb") as stdout, (directory / "worker.stderr.txt").open("xb") as stderr:
            process = subprocess.Popen(
                worker_command(args, pin["interpreter"], read_fd), stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, cwd=directory, env=clean_environment(directory),
                start_new_session=True,
                pass_fds=(read_fd,) + ((args.lock_fd,) if getattr(args, "lock_fd", None) is not None else ()),
                preexec_fn=prepare_worker,
            )
            os.close(read_fd)
            read_fd = None
            tighten_controller()
            save(directory / "controller-bounded.json", readback())
            os.write(write_fd, b"1")
            os.close(write_fd)
            write_fd = None
            remaining = WALL_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, WALL_SECONDS)
            returncode = process.wait(timeout=remaining)
            terminal["returncode"] = returncode
            if returncode != 0 or not (directory / "worker-success.json").is_file():
                raise RuntimeError("worker failed; original outputs preserved")
            terminal["status"] = "tiny_invented_backend_pass"
    except subprocess.TimeoutExpired:
        terminal["status"] = "wall_timeout"
    except Exception as exc:
        terminal.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        for descriptor in (read_fd, write_fd):
            if descriptor is not None:
                os.close(descriptor)
        if process is not None:
            stop_worker(process)
            terminal["returncode"] = process.returncode
        terminal["wall_seconds"] = time.monotonic() - started
        terminal["controller_resources"] = readback()
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        terminal["child_cpu_seconds"] = usage.ru_utime + usage.ru_stime
        terminal["child_peak_rss_kib"] = usage.ru_maxrss
        save(directory / "controller-terminal.json", terminal)
    return 0 if terminal["status"] == "tiny_invented_backend_pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-pin", required=True)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--barrier-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    # The inherited descriptor also holds this lock if the controller dies.
    descriptor = os.open(OUTPUT_ROOT / "ef-backend-probe.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        args.lock_fd = lock.fileno()
        return controller(args)


if __name__ == "__main__":
    raise SystemExit(main())
