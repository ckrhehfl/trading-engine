"""Worker-only authentication of the existing pinned native numerical install.

The file-roster, mapped-library and import-proof checks are the narrow numerical
boundary retained from the original EC runtime. No old private entrypoint or
scientific implementation is imported. Importing this module and validating an
environment are source-only; activation belongs inside the bridge's budgeted
worker after STARTED. This module installs nothing and runs no fit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Callable

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


RUNTIME = "/usr/bin/python3.12"
PARENT = "/home/minju/research-runtime/ec-ml-20261031-v1"
MANIFEST_SHA256 = "26a3cf0900d96984f49e0ed57103e1ef6c961c8fa781bfad58c746603e4a8049"
IMPORT_PROOF_SHA256 = "ded322450fa15639b90aedca1470ab33c789e39b9d43936b4a8ce8e285f0387b"
VERSIONS = {"numpy": "2.5.2", "scipy": "1.18.1", "lightgbm": "4.6.0"}
THREAD_ENVIRONMENT = dict.fromkeys(("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"), "1")
INSTALLED_FILE_COUNT = 2389
_ENVIRONMENT_BYTES = wire.encode({
    "site_packages": PARENT + "/site-packages", "manifest": PARENT + "/manifest.json",
    "manifest_sha256": MANIFEST_SHA256, "import_proof": PARENT + "/project-import-proof-v1.json",
    "import_proof_sha256": IMPORT_PROOF_SHA256, "versions": VERSIONS,
    "thread_environment": THREAD_ENVIRONMENT, "worker_only_lazy_imports": True,
    "installed_file_and_loaded_binary_binding_required": True, "bytecode_writes": False,
    "no_external_training_service_GPU_or_paid_dependency": True,
})
ENVIRONMENT = bridge.canonical(_ENVIRONMENT_BYTES)
Checkpoint = Callable[[str], object]


def validate_environment(environment: dict) -> dict:
    """Exact fixed metadata, without reading the native installation."""
    wire.require(type(environment) is dict and wire.encode(environment) == _ENVIRONMENT_BYTES,
                 "fixed ED numerical environment differs")
    return bridge.canonical(_ENVIRONMENT_BYTES)


def _runtime_path() -> str:
    return str(Path(sys.executable).resolve())


def _installed_roster(site: Path) -> set[str]:
    """Enumerate only the dedicated install, never any repository source tree."""
    wire.safe_path(site)
    wire.require(site.is_dir(), "dedicated numerical site directory required")
    found = set()
    def failed(error: OSError) -> None:
        raise error
    for directory, directories, files in os.walk(site, followlinks=False, onerror=failed):
        parent = Path(directory)
        for name in (*directories, *files):
            wire.require(not (parent / name).is_symlink(), "numerical installed alias refused")
        for name in files:
            path = parent / name
            wire.regular(path)
            found.add(path.relative_to(site).as_posix())
    return found


def _hash_installed_files(site: Path, files: dict, checkpoint: Checkpoint) -> dict:
    wire.require(type(files) is dict and bool(files) and all(
        type(name) is str and name and not Path(name).is_absolute() and ".." not in Path(name).parts
        and "\\" not in name and wire.valid_sha(pin) for name, pin in files.items()),
        "numerical installed file manifest differs")
    wire.require(_installed_roster(site) == set(files), "numerical installed files missing/extra")
    identities = {}
    for name, pin in sorted(files.items()):
        path = site / name
        before = wire.file_identity(wire.regular(path))
        wire.require(wire.file_digest(path, 128 * wire.MIB, checkpoint)[0] == pin,
                     "numerical installed file pin differs")
        after = wire.file_identity(wire.regular(path))
        wire.require(before == after, "numerical installed file changed around hashing")
        identities[name] = after
    wire.require(_installed_roster(site) == set(files), "numerical installed roster changed")
    return identities


def _mapped_library_paths() -> set[str]:
    # This is the current worker's kernel pseudo-file, not a market-data input.
    paths = set()
    with Path("/proc/self/maps").open(encoding="utf-8") as stream:
        for line in stream:
            parts = line.split(None, 5)
            if len(parts) == 6 and parts[5].startswith("/") and ".so" in parts[5]:
                text = parts[5].strip()
                wire.require(not text.endswith(" (deleted)"), "deleted mapped numerical library refused")
                paths.add(str(Path(text).resolve(strict=True)))
    return paths


def _hash_mapped_libraries(proof: dict, files: dict, site: Path, checkpoint: Checkpoint) -> dict:
    expected = proof["mapped_libraries"]
    wire.require(type(expected) is dict and bool(expected) and all(
        type(name) is str and Path(name).is_absolute() and ".." not in Path(name).parts
        and wire.valid_sha(pin) for name, pin in expected.items()), "pinned import library map required")
    paths = _mapped_library_paths()
    site_libraries = {str(site / name): pin for name, pin in files.items() if ".so" in name}
    wire.require(all(expected[text] == pin for text, pin in site_libraries.items() if text in expected),
                 "numerical import proof/installed binary pins differ")
    allowed = {**site_libraries, **expected}
    wire.require(set(expected) <= paths <= set(allowed), "mapped numerical/system library closure differs")
    mapped = {}
    for text in sorted(paths):
        pin = wire.file_digest(Path(text), 128 * wire.MIB, checkpoint)[0]
        wire.require(pin == allowed[text], "mapped numerical/system binary differs")
        mapped[text] = pin
    return mapped


def _authenticated_evidence(environment: dict) -> tuple[dict, dict]:
    manifest_raw = bridge.bounded_read(Path(environment["manifest"]), wire.MIB)
    proof_raw = bridge.bounded_read(Path(environment["import_proof"]), wire.MIB)
    wire.require(wire.digest(manifest_raw) == MANIFEST_SHA256 and wire.digest(proof_raw) == IMPORT_PROOF_SHA256,
                 "native numerical manifest/import proof differs")
    manifest, proof = json.loads(manifest_raw), json.loads(proof_raw)
    _validate_preparation(manifest, proof, environment)
    return manifest, proof


def _validate_preparation(manifest: dict, proof: dict, environment: dict) -> None:
    wire.require(manifest["status"] == "installed_not_qualified"
        and manifest["site_packages"] == environment["site_packages"] and manifest["runtime"] == RUNTIME
        and manifest["thread_environment"] == THREAD_ENVIRONMENT and type(manifest["files"]) is dict
        and len(manifest["files"]) == INSTALLED_FILE_COUNT
        and manifest["actual_market_inputs"] == manifest["model_fits"] == 0
        and proof["status"] == "invented_import_only_pass" and proof["versions"] == VERSIONS
        and proof["model_fits"] == proof["market_inputs"] == 0,
        "native numerical preparation evidence differs")


def _numerical_already_imported() -> bool:
    return any(name == package or name.startswith(package + ".")
               for name in sys.modules for package in VERSIONS)


def _import_numerical() -> tuple:
    import numpy
    import scipy
    import lightgbm
    return numpy, scipy, lightgbm


def _verify_imports(modules: tuple, site: Path) -> None:
    wire.require(type(modules) is tuple and len(modules) == 3, "fixed numerical import roster differs")
    for (name, version), module in zip(VERSIONS.items(), modules, strict=True):
        wire.require(module.__version__ == version and site in Path(module.__file__).resolve().parents,
                     "numerical import escaped pinned native environment")


def _verify_installed_identities(site: Path, files: dict, identities: dict) -> None:
    wire.require(_installed_roster(site) == set(files) and all(
        wire.file_identity(wire.regular(site / name)) == identity for name, identity in identities.items()),
        "numerical installed files changed during import")


def _process_controls() -> None:
    wire.require(_runtime_path() == RUNTIME, "fixed numerical worker runtime required")
    wire.require(sys.dont_write_bytecode is True
        and all(os.environ.get(name) == value for name, value in THREAD_ENVIRONMENT.items()),
        "numerical thread/bytecode controls changed")


def activate(environment: dict, checkpoint: Checkpoint) -> dict:
    """Authenticate all installed bytes before adding their path or importing."""
    registered = validate_environment(environment)
    wire.require(_runtime_path() == RUNTIME, "fixed numerical worker runtime required")
    checkpoint("numerical_authentication")
    manifest, proof = _authenticated_evidence(registered)
    site, files = Path(registered["site_packages"]), manifest["files"]
    identities = _hash_installed_files(site, files, checkpoint)
    wire.require(not _numerical_already_imported(),
                 "numerical libraries already imported before authenticated activation")
    sys.dont_write_bytecode = True
    os.environ.update(THREAD_ENVIRONMENT)
    sys.path.insert(0, str(site))
    modules = _import_numerical()
    _verify_imports(modules, site)
    _process_controls()
    _verify_installed_identities(site, files, identities)
    mapped = _hash_mapped_libraries(proof, files, site, checkpoint)
    return {"registered": registered, "environment_sha256": wire.digest(_ENVIRONMENT_BYTES),
        "manifest_sha256": MANIFEST_SHA256, "import_proof_sha256": IMPORT_PROOF_SHA256,
        "installed_file_count": len(files), "installed_file_map_sha256": wire.digest(wire.encode(files)),
        "mapped_libraries": mapped, "mapped_libraries_sha256": wire.digest(wire.encode(mapped)),
        "versions": dict(VERSIONS), "runtime_sha256": bridge.runtime_sha(),
        "thread_environment": dict(THREAD_ENVIRONMENT), "bytecode_writes": False}


def verify_after(receipt: dict, checkpoint: Checkpoint) -> dict:
    """Rehash the whole installed roster and require the same mapped closure."""
    wire.require(type(receipt) is dict, "numerical activation receipt required")
    registered = validate_environment(receipt["registered"])
    wire.require(set(receipt) == {"registered", "environment_sha256", "manifest_sha256", "import_proof_sha256",
        "installed_file_count", "installed_file_map_sha256", "mapped_libraries", "mapped_libraries_sha256",
        "versions", "runtime_sha256", "thread_environment", "bytecode_writes"}
        and receipt["environment_sha256"] == wire.digest(_ENVIRONMENT_BYTES)
        and receipt["manifest_sha256"] == MANIFEST_SHA256 and receipt["import_proof_sha256"] == IMPORT_PROOF_SHA256
        and receipt["installed_file_count"] == INSTALLED_FILE_COUNT and receipt["versions"] == VERSIONS
        and receipt["thread_environment"] == THREAD_ENVIRONMENT and receipt["bytecode_writes"] is False,
        "fixed numerical activation receipt differs")
    _process_controls()
    checkpoint("numerical_verification_after")
    manifest, proof = _authenticated_evidence(registered)
    files, site = manifest["files"], Path(registered["site_packages"])
    wire.require(wire.digest(wire.encode(files)) == receipt["installed_file_map_sha256"],
                 "numerical installed manifest changed")
    _hash_installed_files(site, files, checkpoint)
    _verify_imports(tuple(sys.modules[name] for name in VERSIONS), site)
    mapped = _hash_mapped_libraries(proof, files, site, checkpoint)
    wire.require(mapped == receipt["mapped_libraries"] and wire.digest(wire.encode(mapped))
        == receipt["mapped_libraries_sha256"], "numerical mapped binary closure changed during computation")
    wire.require(bridge.runtime_sha() == receipt["runtime_sha256"], "numerical worker runtime changed")
    return {"verified": True, "environment_sha256": receipt["environment_sha256"],
        "installed_file_count": len(files), "installed_file_map_sha256": receipt["installed_file_map_sha256"],
        "mapped_libraries_sha256": receipt["mapped_libraries_sha256"],
        "full_installed_roster_rehashed": True, "mapped_binary_closure_unchanged": True}
