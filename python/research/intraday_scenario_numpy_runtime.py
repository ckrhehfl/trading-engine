"""Verify and attach only the pinned NumPy tree to an isolated EH worker."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.machinery import ExtensionFileLoader, PathFinder, SourceFileLoader
import os
from pathlib import Path
import re
import stat
import sys

from research import intraday_campaign_wsl_transport as wire
from research.intraday_scenario_sequence import NUMPY_VERSION


_PACKAGE_ROOTS = ("numpy", "numpy.libs")
_MAX_ENTRIES = 2048
_MAX_BYTES = 96 * wire.MIB
_MAX_FILE_BYTES = 32 * wire.MIB
_NAME = re.compile(r"[A-Za-z0-9_.+-]+")
Checkpoint = Callable[[str], object]


@dataclass(frozen=True)
class NumPyRuntime:
    manifest_sha256: str
    files: int
    size: int


@dataclass(frozen=True)
class _Pin:
    sha256: str
    size: int
    identity: tuple


def _site(worktree: Path) -> Path:
    site = worktree / "python" / ".venv" / "lib" / "python3.12" / "site-packages"
    wire.safe_path(site)
    return site


def _inventory(site: Path, checkpoint: Checkpoint) -> tuple[NumPyRuntime, dict[Path, _Pin]]:
    pending = [site / name for name in _PACKAGE_ROOTS]
    observed = {}
    pins = {}
    total = 0
    while pending:
        directory = pending.pop()
        before = directory.lstat()
        wire.require(stat.S_ISDIR(before.st_mode), "NumPy package directory is absent or aliased")
        observed[directory] = wire.file_identity(before)
        with os.scandir(directory) as entries:
            for entry in entries:
                checkpoint("eh_numpy_manifest_entry")
                wire.require(len(observed) + len(pending) < _MAX_ENTRIES and _NAME.fullmatch(entry.name) is not None,
                             "NumPy package entry bound or name differs")
                child = directory / entry.name
                info = child.lstat()
                if stat.S_ISDIR(info.st_mode) and directory.name != "__pycache__":
                    pending.append(child)
                    continue
                wire.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= _MAX_FILE_BYTES,
                             "NumPy package entry must be a bounded exclusive regular file")
                identity = wire.file_identity(info)
                observed[child] = identity
                total += info.st_size
                wire.require(total <= _MAX_BYTES, "NumPy package byte bound exceeded")
                if directory.name == "__pycache__":
                    wire.require(child.suffix == ".pyc", "unexpected NumPy cache entry")
                    continue
                wire.require(child.suffix != ".pyc", "NumPy sourceless modules are unsupported")
                sha, size = wire.file_digest(child, _MAX_FILE_BYTES, checkpoint)
                pins[child] = _Pin(sha, size, identity)
    wire.require(all(wire.file_identity(path.lstat()) == before for path, before in observed.items()),
                 "NumPy package changed during verification")
    wire.require(site / "numpy" / "__init__.py" in pins
                 and any(path.is_relative_to(site / "numpy.libs") for path in pins),
                 "NumPy package or bundled native libraries are absent")
    manifest = [{"name": path.relative_to(site).as_posix(), "sha256": pin.sha256, "size": pin.size}
                for path, pin in pins.items()]
    manifest.sort(key=lambda member: member["name"])
    return NumPyRuntime(wire.digest(wire.encode(manifest)), len(pins), sum(pin.size for pin in pins.values())), pins


def inspect_numpy_installation(worktree: Path, checkpoint: Checkpoint) -> NumPyRuntime:
    """Measure the canonical installed artifact without importing it."""
    return _inventory(_site(worktree), checkpoint)[0]


class _PinnedSource(SourceFileLoader):
    def __init__(self, name: str, path: Path, pin: _Pin):
        super().__init__(name, str(path))
        self._pin = pin

    def get_code(self, fullname):
        # Source-only compilation avoids consuming mutable __pycache__ files.
        path = Path(self.path)
        wire.require(wire.file_identity(wire.regular(path)) == self._pin.identity, "NumPy source identity differs")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            wire.require(wire.file_identity(os.fstat(stream.fileno())) == self._pin.identity,
                         "NumPy source changed before import")
            raw = stream.read(self._pin.size + 1)
            wire.require(wire.file_identity(os.fstat(stream.fileno())) == self._pin.identity,
                         "NumPy source changed during import")
        wire.require(len(raw) == self._pin.size and wire.digest(raw) == self._pin.sha256
                     and wire.file_identity(wire.regular(path)) == self._pin.identity, "NumPy source pin differs")
        return self.source_to_code(raw, self.path)


class _PinnedFinder:
    def __init__(self, site: Path, pins: dict[Path, _Pin], checkpoint: Checkpoint):
        self._site = site
        self._pins = pins
        self._libraries = {path: pin.identity for path, pin in pins.items() if path.is_relative_to(site / "numpy.libs")}
        self._checkpoint = checkpoint

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "numpy" and not fullname.startswith("numpy."):
            return None
        spec = PathFinder.find_spec(fullname, [str(self._site)] if fullname == "numpy" else path, target)
        if spec is None:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        wire.require(spec.origin is not None, "NumPy module has no pinned origin")
        origin = Path(spec.origin)
        pin = self._pins.get(origin)
        wire.require(pin is not None and origin.is_relative_to(self._site / "numpy"), "NumPy module origin is unpinned")
        if type(spec.loader) is SourceFileLoader:
            spec.loader = _PinnedSource(fullname, origin, pin)
        else:
            wire.require(type(spec.loader) is ExtensionFileLoader
                         and wire.file_digest(origin, _MAX_FILE_BYTES, self._checkpoint) == (pin.sha256, pin.size)
                         and wire.file_identity(wire.regular(origin)) == pin.identity
                         and all(wire.file_identity(wire.regular(lib)) == identity for lib, identity in self._libraries.items()),
                         "NumPy native module or bundled library identity differs")
        return spec


def activate_pinned_numpy(worktree: Path, *, expected_manifest_sha256: str, checkpoint: Checkpoint) -> NumPyRuntime:
    """Expose NumPy alone after validating the worker's reviewed installation pin."""
    site = _site(worktree)
    wire.require(sys.platform == "linux" and sys.version_info[:2] == (3, 12) and sys.flags.isolated and sys.flags.no_site
                 and str(site) not in sys.path and not any(os.getenv(name) for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"))
                 and not any(name == "numpy" or name.startswith("numpy.") for name in sys.modules),
                 "NumPy requires a fresh isolated Python 3.12 worker")
    wire.require(wire.valid_sha(expected_manifest_sha256), "reviewed NumPy manifest pin required")
    runtime, pins = _inventory(site, checkpoint)
    wire.require(runtime.manifest_sha256 == expected_manifest_sha256, "NumPy manifest pin differs")
    sys.meta_path.insert(0, _PinnedFinder(site, pins, checkpoint))
    wire.require(import_module("numpy").__version__ == NUMPY_VERSION, "NumPy runtime version differs")
    return runtime
