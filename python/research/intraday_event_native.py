"""Worker-only admission of the existing EL CPU numerical installations.

Call only after the caller's durable STARTED and resource barrier, before model
inputs. One fresh ``python -I -S`` process admits one backend, once. Importing
this module reads no installed files and imports no numerical library. It does
not install packages, launch workers, remove files, or authorize an actual run.

The classical branch reuses the unchanged ED numerical authenticator. The Torch
inventory/finder boundary is extracted from the reviewed EF runtime; no private
entrypoint or previous scientific code is imported. Retained wheel files may
have external cache hardlinks. Duplicate in-tree inodes, symlinks and observed
mutation fail; before/after hashes are observations, not an immutable mount.
Torch's system shared libraries and the OS are not a hermetic pinned closure.
The caller retains CPU/memory/wall limits, locks, source admission and journaling.
"""
from __future__ import annotations

import hashlib
from importlib.machinery import PathFinder
import json
import os
from pathlib import Path
import stat
import sys
from typing import Callable

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


RUNTIME = "/usr/bin/python3.12"
RUNTIME_SHA256 = "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f"
TORCH_PREFIX = Path("/home/minju/research-evidence/ed-tcn-feasibility-20261031-v1")
TORCH_SITE = TORCH_PREFIX / "venv/lib/python3.12/site-packages"
TORCH_VERSION = "2.7.0+cpu"
TORCH_PIN = "var/ef-backend-runtime-pin-v1.json"
TORCH_PIN_SHA256 = "a5c326ee60e444f8c660f63729f2f1fcd2b50c9ba3cb38a87a86a301c5edc4ff"
TORCH_TREE_PIN = "var/ef-comparison-torch-pins-v1.json"
TORCH_TREE_PIN_SHA256 = "b8efab2eb7b69e8220945c0f69a85c3922654a72a8928ff15be7910cffad4de6"
TORCH_INVENTORY = {"files": 14068, "directories": 813, "bytes": 704688723,
    "sha256": "275e70d889662824899b29b3ac1b657f31c6197717e6c636d7d0dd68250b4da4"}
TORCH_MODULES = frozenset("torch functorch torchgen filelock fsspec jinja2 markupsafe mpmath "
    "networkx sympy typing_extensions setuptools pkg_resources _distutils_hack".split())
CLASSICAL_MODULES = frozenset(("numpy", "scipy", "lightgbm"))
THREAD_ENVIRONMENT = dict.fromkeys(("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"), "1")
GPU_ENVIRONMENT = dict.fromkeys(("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"), "")
Checkpoint = Callable[[str], object]
_attempted = False
_active_receipt: bytes | None = None
_active_finder: NativeAttachment | None = None
_active_path: tuple[str, ...] | None = None


def _checkpoint(_stage: str) -> None:
    pass


class NativeAttachment:
    """Expose only pinned top-level namespaces, without site/.pth execution."""

    def __init__(self, directory: Path, modules: frozenset[str]):
        self.directory = directory
        self.modules = modules

    def find_spec(self, fullname: str, path=None, target=None):
        if path is not None or fullname not in self.modules:
            return None
        spec = PathFinder.find_spec(fullname, [str(self.directory)], target)
        wire.require(spec is not None and spec.origin is not None,
                     "admitted native module missing")
        origin = Path(spec.origin)
        wire.safe_path(origin)
        wire.require(origin.is_relative_to(self.directory), "native module escaped pinned directory")
        return spec


def retained_file_sha(path, before: os.stat_result, *, directory_fd=None,
                      checkpoint: Checkpoint = _checkpoint) -> str:
    """Hash one opened regular file while preserving its observed identity."""
    wire.require(stat.S_ISREG(before.st_mode) and before.st_size <= 1024**3,
                 "bounded retained Torch file required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(descriptor)) == wire.file_identity(before),
                     "retained Torch file changed before open")
        remaining, hashed = before.st_size, hashlib.sha256()
        while remaining:
            checkpoint("native_tree_hash")
            block = stream.read(min(remaining, wire.MIB))
            wire.require(bool(block), "retained Torch file truncated during hashing")
            hashed.update(block)
            remaining -= len(block)
        wire.require(not stream.read(1) and wire.file_identity(before)
            == wire.file_identity(os.fstat(descriptor))
            == wire.file_identity(os.stat(path, dir_fd=directory_fd, follow_symlinks=False)),
            "retained Torch file changed during hashing")
    return hashed.hexdigest()


def _retained_roster(site: Path) -> set[tuple[str, str]]:
    """Re-enumerate names: directory timestamps alone need not expose new entries."""
    rows = set()

    def failed(error: OSError) -> None:
        raise error

    for directory, subdirs, names in os.walk(site, followlinks=False, onerror=failed):
        path = Path(directory)
        rows.add(("directory", str(path.relative_to(site))))
        for name in subdirs:
            wire.require(stat.S_ISDIR((path / name).lstat().st_mode),
                         "aliased retained Torch directory refused")
        subdirs[:] = sorted(name for name in subdirs if name != "__pycache__")
        for name in names:
            wire.require(name != "__pycache__" and not name.endswith((".pyc", ".pyo"))
                and stat.S_ISREG((path / name).lstat().st_mode),
                "unadmitted retained Torch file in final roster")
            rows.add(("file", str((path / name).relative_to(site))))
    return rows


def retained_inventory(site: Path, legacy_files=(), *, checkpoint: Checkpoint = _checkpoint) -> dict:
    """Bind all entries except inert __pycache__; /dev/null cache routing is required."""
    wire.safe_path(site)
    wire.require(site.is_dir(), "retained Torch package root missing")
    expected = dict(legacy_files)
    rows, identities, seen_inodes = [], [], set()
    file_count = directory_count = total_bytes = 0

    def failed(error: OSError) -> None:
        raise error

    for directory, subdirs, names, descriptor in os.fwalk(site, follow_symlinks=False, onerror=failed):
        path = Path(directory)
        identities.append((path, wire.file_identity(os.fstat(descriptor))))
        directory_count += 1
        rows.append(["directory", str(path.relative_to(site))])
        for name in subdirs:
            entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            wire.require(stat.S_ISDIR(entry.st_mode), "aliased retained Torch directory refused")
        subdirs[:] = sorted(name for name in subdirs if name != "__pycache__")
        for name in sorted(names):
            relative = str((path / name).relative_to(site))
            wire.require(name != "__pycache__" and not name.endswith((".pyc", ".pyo")),
                         "standalone retained Torch bytecode refused")
            entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            inode = entry.st_dev, entry.st_ino
            wire.require(inode not in seen_inodes, "aliased retained Torch file refused")
            seen_inodes.add(inode)
            sha = retained_file_sha(name, entry, directory_fd=descriptor, checkpoint=checkpoint)
            wire.require(expected.pop(relative, sha) == sha, "legacy retained Torch package pin changed")
            identities.append((path / name, wire.file_identity(entry)))
            file_count += 1
            total_bytes += entry.st_size
            rows.append(["file", relative, entry.st_size, sha])
    wire.safe_path(site)
    wire.require(_retained_roster(site) == {(row[0], row[1]) for row in rows},
                 "retained Torch package tree changed during final roster")
    wire.require(file_count > 0 and not expected and all(
        wire.file_identity(path.lstat()) == identity for path, identity in identities),
        "retained Torch package tree changed during inventory")
    return {"files": file_count, "directories": directory_count, "bytes": total_bytes,
            "sha256": wire.digest(wire.encode(rows))}


def _torch_pins() -> tuple[dict, dict]:
    root = Path(__file__).parents[2]
    raw = bridge.bounded_read(root / TORCH_PIN, 64 * 1024)
    tree_raw = bridge.bounded_read(root / TORCH_TREE_PIN, 64 * 1024)
    wire.require(wire.digest(raw) == TORCH_PIN_SHA256 and wire.digest(tree_raw) == TORCH_TREE_PIN_SHA256,
                 "retained Torch metadata pin differs")
    pin, tree = json.loads(raw), json.loads(tree_raw)
    wire.require(pin["schema"] == "ef-retained-torch-runtime-v1" and pin["torch"] == TORCH_VERSION
        and pin["prefix"] == str(TORCH_PREFIX) and pin["site_packages"] == str(TORCH_SITE)
        and pin["interpreter"] == str(TORCH_PREFIX / "venv/bin/python")
        and pin["interpreter_sha256"] == RUNTIME_SHA256
        and tree == {"schema": "ef-comparison-torch-tree-v1", "torch": TORCH_VERSION,
            "site_packages": str(TORCH_SITE), "legacy_pin_sha256": TORCH_PIN_SHA256,
            "admitted_modules": sorted(TORCH_MODULES), "inventory": TORCH_INVENTORY},
        "fixed retained Torch metadata differs")
    return pin, tree


def _torch_tree(checkpoint: Checkpoint) -> dict:
    pin, tree = _torch_pins()
    expected = {}
    wire.require(type(pin["files"]) is dict and bool(pin["files"]), "retained Torch file pins required")
    for name, sha in pin["files"].items():
        wire.require(type(name) is str and name and not Path(name).is_absolute()
            and ".." not in Path(name).parts and "\\" not in name and wire.valid_sha(sha),
            "retained Torch relative file pin required")
        path = TORCH_PREFIX / name
        wire.safe_path(path)
        if path.is_relative_to(TORCH_SITE):
            expected[str(path.relative_to(TORCH_SITE))] = sha
        else:
            wire.require(retained_file_sha(path, path.lstat(), checkpoint=checkpoint) == sha,
                         "retained Torch environment pin changed")
    inventory = retained_inventory(TORCH_SITE, expected, checkpoint=checkpoint)
    wire.require(inventory == tree["inventory"], "retained Torch full tree pin differs")
    return inventory


def _isolated() -> None:
    wire.require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
        and sys.pycache_prefix == "/dev/null" and str(Path(sys.executable).resolve()) == RUNTIME,
        "isolated native Python 3.12 with disabled bytecode cache required")


def _runtime_sha(checkpoint: Checkpoint) -> str:
    sha = wire.file_digest(Path(RUNTIME), 32 * wire.MIB, checkpoint)[0]
    wire.require(sha == RUNTIME_SHA256, "fixed native interpreter pin differs")
    return sha


def _preimported() -> bool:
    return any(name.partition(".")[0] in TORCH_MODULES | CLASSICAL_MODULES for name in sys.modules)


def _environment(workdir: Path) -> dict[str, str]:
    return {**THREAD_ENVIRONMENT, **GPU_ENVIRONMENT, "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(workdir), "TMPDIR": str(workdir / "tmp"),
        "TORCH_HOME": str(workdir / "torch-home"),
        "TORCHINDUCTOR_CACHE_DIR": str(workdir / "inductor-cache")}


def _prepare(workdir: Path) -> None:
    wire.safe_path(workdir)
    wire.require(workdir.is_dir() and not workdir.is_relative_to(TORCH_PREFIX)
        and not workdir.is_relative_to(Path("/home/minju/research-runtime/ec-ml-20261031-v1")),
        "separate existing worker scratch directory required")
    # Exclusive creation fails closed on a reused scratch directory; never clean it.
    (workdir / "tmp").mkdir(mode=0o700)
    os.environ.update(_environment(workdir))


def _controls(workdir: Path) -> None:
    _isolated()
    wire.safe_path(workdir / "tmp")
    wire.require((workdir / "tmp").is_dir(), "worker scratch directory changed")
    wire.require(all(os.environ.get(name) == value for name, value in _environment(workdir).items()),
                 "native worker environment changed")
    wire.require_unprivileged_single_task()


def _import_torch():
    import torch
    return torch


def _prepare_torch_templates(workdir, original_path):
    # The pinned package imports a generated RPC template from its private
    # directory. Admit that one bootstrap, then remove its search path.
    from torch.distributed.nn.jit import instantiator

    source = Path(instantiator.__file__)
    path = Path(instantiator.INSTANTIATED_TEMPLATE_DIR_PATH)
    generated = path / "_remote_module_non_scriptable.py"
    wire.safe_path(source)
    wire.safe_path(path)
    wire.require(source == TORCH_SITE / "torch/distributed/nn/jit/instantiator.py"
        and path.parent == workdir / "tmp" and path.is_dir() and set(path.iterdir()) == {generated}
        and getattr(sys.modules.get(generated.stem), "__file__", None) == str(generated)
        and tuple(sys.path) == (*original_path, str(path)), "unexpected pinned Torch template import path")
    wire.require(wire.regular(generated).st_size <= wire.MIB, "generated Torch template exceeds private bound")
    sys.path.pop()
    sys.path_importer_cache.pop(str(path), None)


def _check_torch(torch) -> None:
    wire.require(torch.__version__ == TORCH_VERSION and torch.version.cuda is None
        and TORCH_SITE in Path(torch.__file__).resolve().parents
        and torch.get_num_threads() == torch.get_num_interop_threads() == 1
        and torch.are_deterministic_algorithms_enabled(), "CPU Torch import or settings differ")


def activate(kind: str, workdir: Path, *, checkpoint: Checkpoint = _checkpoint) -> dict:
    """Admit empirical/lightgbm or tcn once, preserving an exclusive scratch directory."""
    global _attempted, _active_receipt, _active_finder, _active_path
    wire.require(kind in ("empirical", "lightgbm", "tcn"), "fixed EL native learner required")
    wire.require(not _attempted and not _preimported(), "fresh single-backend worker required")
    _isolated()
    _attempted = True  # A partial failure also spends this process; never retry in place.
    workdir = Path(workdir)
    _prepare(workdir)
    _controls(workdir)
    runtime_sha = _runtime_sha(checkpoint)
    original_path = tuple(sys.path)
    wire.require(not any("site-packages" in name or "dist-packages" in name for name in original_path),
                 "isolated standard-library search path required")
    if kind == "tcn":
        inventory = _torch_tree(checkpoint)
        finder = NativeAttachment(TORCH_SITE, TORCH_MODULES)
        sys.meta_path.insert(0, finder)
        torch = _import_torch()
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        torch.use_deterministic_algorithms(True)
        _check_torch(torch)
        _prepare_torch_templates(workdir, original_path)
        backend = {"torch": TORCH_VERSION, "metadata_sha256": TORCH_PIN_SHA256,
            "tree_metadata_sha256": TORCH_TREE_PIN_SHA256, "inventory": inventory,
            "system_shared_library_closure_pinned": False}
    else:
        from research import intraday_ed_numerical as numerical

        # Its unchanged authenticator briefly adds the fully hashed EC site for
        # its imports. Remove that exact insertion and retain namespace-only access.
        backend = numerical.activate(numerical.ENVIRONMENT, checkpoint)
        site = Path(numerical.ENVIRONMENT["site_packages"])
        wire.require(tuple(sys.path) == (str(site), *original_path), "numerical search path changed")
        del sys.path[0]
        finder = NativeAttachment(site, CLASSICAL_MODULES)
        sys.meta_path.insert(0, finder)
    _controls(workdir)
    wire.require(tuple(sys.path) == original_path, "native search path changed")
    receipt = {"schema": "el-native-admission-v1", "kind": kind, "workdir": str(workdir),
        "runtime_sha256": runtime_sha, "backend": backend, "threads": 1, "gpu": False,
        "bytecode_cache": "/dev/null", "os_hermetic": False, "resource_limits_owned_by_caller": True}
    _active_receipt = wire.encode(receipt)
    _active_finder, _active_path = finder, original_path
    return receipt


def verify_after(receipt: dict, *, checkpoint: Checkpoint = _checkpoint) -> dict:
    """Reauthenticate bytes and controls after computation in the admitting process."""
    wire.require(type(receipt) is dict and _active_receipt is not None
        and wire.encode(receipt) == _active_receipt, "original same-process native receipt required")
    wire.require(_active_finder is not None and bool(sys.meta_path) and sys.meta_path[0] is _active_finder
        and tuple(sys.path) == _active_path, "native import boundary changed")
    workdir = Path(receipt["workdir"])
    _controls(workdir)
    wire.require(_runtime_sha(checkpoint) == receipt["runtime_sha256"], "native interpreter changed")
    if receipt["kind"] == "tcn":
        wire.require(_active_finder.directory == TORCH_SITE and _active_finder.modules == TORCH_MODULES,
                     "native Torch namespace boundary changed")
        inventory = _torch_tree(checkpoint)
        wire.require(inventory == receipt["backend"]["inventory"], "retained Torch tree changed")
        _check_torch(sys.modules["torch"])
        backend = {"verified": True, "full_installed_roster_rehashed": True,
            "inventory": inventory, "system_shared_library_closure_pinned": False}
    else:
        from research import intraday_ed_numerical as numerical

        wire.require(_active_finder.directory == Path(numerical.ENVIRONMENT["site_packages"])
            and _active_finder.modules == CLASSICAL_MODULES, "native numerical namespace boundary changed")
        backend = numerical.verify_after(receipt["backend"], checkpoint)
    _controls(workdir)
    return {"verified": True, "kind": receipt["kind"], "runtime_sha256": receipt["runtime_sha256"],
        "backend": backend, "os_hermetic": False, "resource_limits_owned_by_caller": True}
