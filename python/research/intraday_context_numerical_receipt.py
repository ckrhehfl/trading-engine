"""Pure saved-receipt checks for the fixed EI native numerical environment.

The caller authenticates the original files, complete publication lifecycle,
reviewed source closure and external expected pins. This module opens no path,
imports no numerical library and does not authorize a run. It binds the saved
ED activation/after receipts to the original manifest, import proof and EI
publication. In the worker, ED activation hashes installed files before import
and mapped binaries after import; verify_after repeats the file/map checks.
These saved claims cannot establish a dead worker's historical mappings, its
execution order, OS controls, correct fitting or current installed-file bytes.
"""

from hashlib import sha256
import json
from math import isfinite
import re


_MIB = 1024**2
_PARENT = "/home/minju/research-runtime/ec-ml-20261031-v1"
_RUNTIME = "/usr/bin/python3.12"
_MANIFEST_SHA256 = "26a3cf0900d96984f49e0ed57103e1ef6c961c8fa781bfad58c746603e4a8049"
_IMPORT_PROOF_SHA256 = "ded322450fa15639b90aedca1470ab33c789e39b9d43936b4a8ce8e285f0387b"
_VERSIONS = {"numpy": "2.5.2", "scipy": "1.18.1", "lightgbm": "4.6.0"}
_THREADS = dict.fromkeys(("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"), "1")
_FILES = 2389
_BINDING_KEYS = {"unit_id", "invented_only", "source_manifest_sha256", "registration_path",
                 "registration_sha256", "runtime_sha256", "output_sha256"}


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("EI numerical receipt: " + reason)


def _encode(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode()


def _digest(value: object) -> str:
    return sha256(_encode(value)).hexdigest()


def _same(left: object, right: object) -> bool:
    # JSON byte equality distinguishes true/1 and 1/1.0, unlike Python equality.
    return _encode(left) == _encode(right)


def _pin(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("EI numerical receipt: nonfinite JSON number " + value)


def _float(value: str) -> float:
    result = float(value)
    _need(isfinite(result), "nonfinite JSON number")
    return result


def _document(raw: bytes, pin: str, *, canonical: bool = False) -> dict:
    _need(type(raw) is bytes and 0 < len(raw) <= _MIB and _pin(pin), "bounded original bytes/pin required")
    _need(sha256(raw).hexdigest() == pin, "original bytes differ from external pin")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    _need(type(value) is dict, "original JSON object required")
    # Native import-proof bytes retain their original formatting under their pin.
    # The publication uses the bridge's exact canonical representation.
    _need(not canonical or _encode(value) == raw, "noncanonical publication")
    return value


def _keys(value: object, keys: set[str]) -> None:
    _need(type(value) is dict and set(value) == keys, "missing/extra receipt fields")


def _path(value: object, *, absolute: bool) -> bool:
    if type(value) is not str or not 0 < len(value) <= 4096 or "\\" in value or "\x00" in value:
        return False
    if value.startswith("/") is not absolute:
        return False
    parts = value[1:].split("/") if absolute else value.split("/")
    return all(part not in ("", ".", "..") and not any(ord(char) < 32 for char in part) for part in parts)


def _map(value: object, *, absolute: bool, maximum: int) -> dict:
    _need(type(value) is dict and 0 < len(value) <= maximum
          and all(_path(name, absolute=absolute) and _pin(pin) for name, pin in value.items()),
          "invalid file/library pin map")
    return value


def _environment() -> dict:
    # Independent constants; no call into the producer's validation helpers.
    return {"site_packages": _PARENT + "/site-packages", "manifest": _PARENT + "/manifest.json",
        "manifest_sha256": _MANIFEST_SHA256, "import_proof": _PARENT + "/project-import-proof-v1.json",
        "import_proof_sha256": _IMPORT_PROOF_SHA256, "versions": _VERSIONS,
        "thread_environment": _THREADS, "worker_only_lazy_imports": True,
        "installed_file_and_loaded_binary_binding_required": True, "bytecode_writes": False,
        "no_external_training_service_GPU_or_paid_dependency": True}


def _preparation(manifest: dict, proof: dict, environment: dict) -> tuple[dict, dict]:
    for field, expected in {"status": "installed_not_qualified", "site_packages": environment["site_packages"],
                           "runtime": _RUNTIME, "thread_environment": _THREADS,
                           "actual_market_inputs": 0, "model_fits": 0}.items():
        _need(field in manifest and _same(manifest[field], expected), "original manifest preparation differs")
    for field, expected in {"status": "invented_import_only_pass", "versions": _VERSIONS,
                           "model_fits": 0, "market_inputs": 0}.items():
        _need(field in proof and _same(proof[field], expected), "original import preparation differs")
    files = _map(manifest.get("files"), absolute=False, maximum=_FILES)
    _need(len(files) == _FILES and all(name + "/__init__.py" in files for name in _VERSIONS),
          "fixed installed package roster/count differs")
    libraries = _map(proof.get("mapped_libraries"), absolute=True, maximum=_FILES)
    _need(all(".so" in name and not name.endswith(" (deleted)") for name in libraries),
          "invalid original mapped library path")
    return files, libraries


def _receipts(before: dict, after: dict, environment: dict, files: dict, libraries: dict,
              runtime_pin: str) -> None:
    _keys(before, {"registered", "environment_sha256", "manifest_sha256", "import_proof_sha256",
        "installed_file_count", "installed_file_map_sha256", "mapped_libraries", "mapped_libraries_sha256",
        "versions", "runtime_sha256", "thread_environment", "bytecode_writes"})
    mapped = _map(before["mapped_libraries"], absolute=True, maximum=_FILES)
    site = environment["site_packages"] + "/"
    native = {site + name: pin for name, pin in files.items() if ".so" in name}
    _need(all(not name.startswith(site) or native.get(name) == pin for name, pin in libraries.items()),
          "original installed/import-proof binary pins conflict")
    _need(all(".so" in name and not name.endswith(" (deleted)") for name in mapped),
          "invalid saved mapped library path")
    allowed = {**native, **libraries}
    _need(set(libraries) <= set(mapped) <= set(allowed)
          and all(allowed[name] == pin for name, pin in mapped.items()),
          "saved mapped libraries differ from original permitted closure")
    fixed = {"registered": environment, "environment_sha256": _digest(environment),
        "manifest_sha256": environment["manifest_sha256"], "import_proof_sha256": environment["import_proof_sha256"],
        "installed_file_count": len(files), "installed_file_map_sha256": _digest(files),
        "mapped_libraries_sha256": _digest(mapped), "versions": _VERSIONS, "runtime_sha256": runtime_pin,
        "thread_environment": _THREADS, "bytecode_writes": False}
    _need(_same({key: before[key] for key in fixed}, fixed), "saved activation differs from authenticated originals")
    expected_after = {"verified": True, "environment_sha256": fixed["environment_sha256"],
        "installed_file_count": len(files), "installed_file_map_sha256": fixed["installed_file_map_sha256"],
        "mapped_libraries_sha256": fixed["mapped_libraries_sha256"],
        "full_installed_roster_rehashed": True, "mapped_binary_closure_unchanged": True}
    _keys(after, set(expected_after))
    _need(_same(after, expected_after), "saved after-verification differs from original before receipt")


def _publication(publication: dict, binding: dict, before: dict, after: dict) -> None:
    _keys(binding, _BINDING_KEYS)
    _need(type(binding["unit_id"]) is str and len(binding["unit_id"]) <= 128
          and re.fullmatch(r"EI-[A-Z0-9]+(?:-[A-Z0-9]+)*", binding["unit_id"]) is not None
          and type(binding["invented_only"]) is bool
          and _path(binding["registration_path"], absolute=False)
          and binding["registration_path"].startswith("configs/research/discovery/")
          and binding["registration_path"].endswith(".json")
          and all(_pin(binding[key]) for key in _BINDING_KEYS - {"unit_id", "invented_only", "registration_path"}),
          "external EI source/publication binding differs")
    fixed = {"status": "completed", "unit_id": binding["unit_id"], "invented_only": binding["invented_only"],
             "output_sha256": binding["output_sha256"], "execution_host": "local_wsl_only",
             "resource_profile": "ei-local-model-cpu1800-as4096-v1", "full_bridge": True,
             "local_pipe_test": False, "verification_complete": True}
    _need(all(key in publication and _same(publication[key], value) for key, value in fixed.items()),
          "saved EI publication identity differs")
    sources = _map(publication.get("source_hashes"), absolute=False, maximum=512)
    _need(_digest(sources) == binding["source_manifest_sha256"]
          and sources.get(binding["registration_path"]) == binding["registration_sha256"]
          and "python/research/intraday_ed_numerical.py" in sources
          and "python/research/intraday_context_numerical_receipt.py" in sources,
          "saved reviewed source/registration binding differs")
    for role in ("local_identity", "remote_identity"):
        identity = publication.get(role)
        _need(type(identity) is dict and identity.get("runtime_sha256") == binding["runtime_sha256"]
              and identity.get("source_manifest_sha256") == binding["source_manifest_sha256"]
              and identity.get("execution_host") == "local_wsl_only", "saved source/interpreter identity differs")
    verification = publication.get("independent_verification")
    _need(type(verification) is dict and verification.get("verified") is True
          and type(verification.get("numerical_environment")) is dict
          and _same(verification["numerical_environment"], {"before": before, "after": after}),
          "numerical receipts are not the original publication members")


def verify_numerical_receipt(manifest_raw: bytes, import_proof_raw: bytes, publication_raw: bytes, *,
                             expected_environment: dict, expected_publication_sha256: str,
                             expected_binding: dict, before: dict, after: dict) -> dict:
    """Authenticate a saved EI numerical proof without file or process access.

    Expected pins/binding come from the caller's authenticated registration,
    launch and original publication, never from these receipts themselves.
    The caller also checks original file safety, lifecycle/journal/budget and
    full publication semantics. Native evidence may contain pinned diagnostic
    fields beyond the preparation fields consumed here. The exact ED before
    and after schemas are required, at independent_verification.numerical_environment.
    """
    _need(type(expected_environment) is dict and _same(expected_environment, _environment()),
          "fixed registered native environment differs")
    manifest = _document(manifest_raw, expected_environment["manifest_sha256"])
    proof = _document(import_proof_raw, expected_environment["import_proof_sha256"])
    publication = _document(publication_raw, expected_publication_sha256, canonical=True)
    _publication(publication, expected_binding, before, after)
    files, libraries = _preparation(manifest, proof, expected_environment)
    _receipts(before, after, expected_environment, files, libraries, expected_binding["runtime_sha256"])
    return {"schema": "ei-context-numerical-receipt-v1", "verified": True, "saved_receipt_only": True,
        **expected_binding, "publication_sha256": expected_publication_sha256,
        "environment_sha256": _digest(expected_environment), "manifest_sha256": expected_environment["manifest_sha256"],
        "import_proof_sha256": expected_environment["import_proof_sha256"], "installed_file_count": len(files),
        "installed_file_map_sha256": _digest(files), "mapped_libraries_sha256": _digest(before["mapped_libraries"]),
        "native_files_read": False, "numerical_libraries_imported": False, "historical_process_reobserved": False}
