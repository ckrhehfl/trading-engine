"""Tiny invented files and mocked import boundaries; no native package access."""
import ast
from copy import deepcopy
from io import StringIO
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from research import intraday_ed_numerical as numerical
from research import intraday_campaign_wsl_transport as wire


def checkpoint(_stage):
    pass


def installed(tmp_path):
    site = tmp_path / "invented-site"
    site.mkdir()
    files = {"package/__init__.py": b"invented Python source\n", "package/libinvented.so": b"invented binary bytes\n"}
    pins = {}
    for name, raw in files.items():
        path = site / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        pins[name] = wire.digest(raw)
    return site, pins


@pytest.mark.parametrize("change", ["site", "manifest", "proof", "sha", "versions", "threads", "bytecode", "extra"])
def test_environment_overrides_are_rejected_before_any_io(monkeypatch, change):
    environment = deepcopy(numerical.ENVIRONMENT)
    if change == "site":
        environment["site_packages"] = "/invented/caller-chosen-site"
    elif change == "manifest":
        environment["manifest"] = "/invented/manifest.json"
    elif change == "proof":
        environment["import_proof"] = "/invented/proof.json"
    elif change == "sha":
        environment["manifest_sha256"] = "a" * 64
    elif change == "versions":
        environment["versions"]["numpy"] = "0.0"
    elif change == "threads":
        environment["thread_environment"]["OMP_NUM_THREADS"] = "2"
    elif change == "bytecode":
        environment["bytecode_writes"] = True
    else:
        environment["callback"] = "unregistered"
    attempts = []
    monkeypatch.setattr(numerical, "_authenticated_evidence", lambda *args: attempts.append("evidence"))
    with pytest.raises(ValueError, match="fixed ED numerical"):
        numerical.activate(environment, checkpoint)
    assert attempts == []


def test_source_only_environment_validation_does_not_resolve_or_open_native_paths(monkeypatch):
    def inaccessible(*args, **kwargs):
        raise AssertionError("source-only validation reached the filesystem")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", inaccessible)
        patch.setattr(Path, "lstat", inaccessible)
        patch.setattr(Path, "open", inaccessible)
        patch.setattr(Path, "resolve", inaccessible)
        assert numerical.validate_environment(deepcopy(numerical.ENVIRONMENT)) == numerical.ENVIRONMENT
    value = numerical.validate_environment(deepcopy(numerical.ENVIRONMENT))
    value["versions"].clear()
    assert numerical.validate_environment(deepcopy(numerical.ENVIRONMENT))["versions"]["numpy"] == "2.5.2"


def test_wrong_worker_runtime_does_not_read_numerical_evidence(monkeypatch):
    monkeypatch.setattr(numerical, "_runtime_path", lambda: "/invented/other-python")
    monkeypatch.setattr(numerical, "_authenticated_evidence",
                        lambda *args: pytest.fail("wrong runtime reached native evidence"))
    with pytest.raises(ValueError, match="fixed numerical worker"):
        numerical.activate(deepcopy(numerical.ENVIRONMENT), checkpoint)


def test_tiny_installed_roster_is_fully_hashed_and_returns_file_identities(tmp_path):
    site, files = installed(tmp_path)
    identities = numerical._hash_installed_files(site, files, checkpoint)
    assert set(identities) == set(files)
    assert identities == {name: wire.file_identity(wire.regular(site / name)) for name in files}
    numerical._verify_installed_identities(site, files, identities)


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "symlink", "hardlink"])
def test_tiny_installed_roster_missing_extra_changed_and_aliases_fail(tmp_path, change):
    site, files = installed(tmp_path)
    path = site / "package/__init__.py"
    if change == "missing":
        path.unlink()
    elif change == "extra":
        (site / "unregistered.py").write_bytes(b"extra\n")
    elif change == "bytes":
        path.write_bytes(b"altered\n")
    elif change == "symlink":
        path.unlink()
        path.symlink_to(site / "package/libinvented.so")
    else:
        (site / "alias").hardlink_to(path)
    with pytest.raises(ValueError):
        numerical._hash_installed_files(site, files, checkpoint)


@pytest.mark.parametrize("name", ["/absolute.py", "../escape.py", "package\\alias.py", ""])
def test_installed_manifest_cannot_choose_an_alias_outside_its_roster(tmp_path, name):
    site, _ = installed(tmp_path)
    with pytest.raises(ValueError, match="file manifest"):
        numerical._hash_installed_files(site, {name: "a" * 64}, checkpoint)


def test_mutation_between_digest_and_saved_identity_is_rejected(tmp_path, monkeypatch):
    site, files = installed(tmp_path)
    original = wire.file_digest
    def changing(path, maximum, hook):
        result = original(path, maximum, hook)
        path.write_bytes(path.read_bytes() + b"changed after hash\n")
        return result
    monkeypatch.setattr(wire, "file_digest", changing)
    with pytest.raises(ValueError, match="changed around hashing"):
        numerical._hash_installed_files(site, files, checkpoint)


def test_second_roster_read_refuses_files_created_during_hashing(tmp_path, monkeypatch):
    site, files = installed(tmp_path)
    roster = numerical._installed_roster
    calls = []
    def changing(path):
        calls.append(path)
        if len(calls) == 2:
            (path / "extra.py").write_bytes(b"invented extra\n")
        return roster(path)
    monkeypatch.setattr(numerical, "_installed_roster", changing)
    with pytest.raises(ValueError, match="roster changed"):
        numerical._hash_installed_files(site, files, checkpoint)


def test_mapped_closure_authenticates_only_proof_and_installed_library_bytes(tmp_path, monkeypatch):
    site, files = installed(tmp_path)
    system = tmp_path / "invented-system.so"
    system.write_bytes(b"invented system library\n")
    native = site / "package/libinvented.so"
    proof = {"mapped_libraries": {str(system): wire.digest(system.read_bytes())}}
    monkeypatch.setattr(numerical, "_mapped_library_paths", lambda: {str(system), str(native)})
    got = numerical._hash_mapped_libraries(proof, files, site, checkpoint)
    assert got == {str(system): wire.digest(system.read_bytes()), str(native): files["package/libinvented.so"]}
    system.write_bytes(b"changed system bytes\n")
    with pytest.raises(ValueError, match="binary differs"):
        numerical._hash_mapped_libraries(proof, files, site, checkpoint)


@pytest.mark.parametrize("change", ["missing", "extra", "relative", "conflict"])
def test_mapped_library_closure_cannot_expand_or_omit_pinned_system_libraries(tmp_path, monkeypatch, change):
    site, files = installed(tmp_path)
    system = tmp_path / "invented-system.so"
    system.write_bytes(b"invented\n")
    native = site / "package/libinvented.so"
    paths = {str(system), str(native)}
    proof = {"mapped_libraries": {str(system): wire.digest(system.read_bytes())}}
    if change == "missing":
        paths.remove(str(system))
    elif change == "extra":
        paths.add(str(tmp_path / "unknown.so"))
    elif change == "relative":
        proof["mapped_libraries"] = {"relative.so": "a" * 64}
    else:
        proof["mapped_libraries"][str(native)] = "a" * 64
    monkeypatch.setattr(numerical, "_mapped_library_paths", lambda: paths)
    with pytest.raises(ValueError):
        numerical._hash_mapped_libraries(proof, files, site, checkpoint)


def test_kernel_map_parser_uses_only_mocked_text_and_rejects_deleted_binary(tmp_path, monkeypatch):
    library = tmp_path / "invented-library.so"
    library.write_bytes(b"invented\n")
    prefix = "1000-2000 r-xp 0000 00:00 1 "
    text = prefix + str(library) + "\n" + prefix + "[heap]\n"
    def maps_only(path, *args, **kwargs):
        assert path == Path("/proc/self/maps")
        return StringIO(text)
    monkeypatch.setattr(Path, "open", maps_only)
    assert numerical._mapped_library_paths() == {str(library)}
    text = prefix + str(library) + " (deleted)\n"
    with pytest.raises(ValueError, match="deleted mapped"):
        numerical._mapped_library_paths()


def test_imported_versions_and_locations_must_be_pinned(tmp_path):
    site = tmp_path / "invented-site"
    modules = []
    for name, version in numerical.VERSIONS.items():
        path = site / name / "__init__.py"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"invented\n")
        modules.append(SimpleNamespace(__version__=version, __file__=str(path)))
    numerical._verify_imports(tuple(modules), site)
    modules[0].__version__ = "0"
    with pytest.raises(ValueError, match="escaped pinned"):
        numerical._verify_imports(tuple(modules), site)
    modules[0].__version__ = numerical.VERSIONS["numpy"]
    modules[0].__file__ = str(tmp_path / "outside.py")
    with pytest.raises(ValueError, match="escaped pinned"):
        numerical._verify_imports(tuple(modules), site)


def mocked_activation(monkeypatch):
    """Bypass authenticated native boundaries explicitly; never open that install."""
    events = []
    files = {f"invented-{index:04d}.py": "a" * 64 for index in range(2389)}
    manifest, proof = {"files": files}, {"mapped_libraries": {"/invented/system.so": "d" * 64}}
    modules = tuple(SimpleNamespace(__version__=version, __file__="/invented/not-imported")
                    for version in numerical.VERSIONS.values())
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    for name in numerical.THREAD_ENVIRONMENT:
        monkeypatch.setenv(name, "invented-before")
    monkeypatch.setattr(numerical, "_runtime_path", lambda: numerical.RUNTIME)
    monkeypatch.setattr(numerical.bridge, "runtime_sha", lambda: "c" * 64)
    def evidence(environment):
        events.append("authenticated_evidence")
        assert environment == numerical.ENVIRONMENT
        return manifest, proof
    monkeypatch.setattr(numerical, "_authenticated_evidence", evidence)
    def installed_hash(site, declared, hook):
        events.append("full_installed_hash")
        return {name: ("invented identity",) for name in declared}
    monkeypatch.setattr(numerical, "_hash_installed_files", installed_hash)
    monkeypatch.setattr(numerical, "_numerical_already_imported", lambda: False)
    def imported():
        events.append("import")
        assert sys.path[0] == numerical.ENVIRONMENT["site_packages"]
        assert sys.dont_write_bytecode
        assert all(os.environ[name] == "1" for name in numerical.THREAD_ENVIRONMENT)
        return modules
    monkeypatch.setattr(numerical, "_import_numerical", imported)
    monkeypatch.setattr(numerical, "_verify_imports", lambda *args: events.append("verify_imports"))
    monkeypatch.setattr(numerical, "_verify_installed_identities", lambda *args: events.append("stable_identities"))
    def mapped(*args):
        events.append("mapped_hash")
        return {"/invented/system.so": "d" * 64}
    monkeypatch.setattr(numerical, "_hash_mapped_libraries", mapped)
    for (name, _), module in zip(numerical.VERSIONS.items(), modules, strict=True):
        monkeypatch.setitem(sys.modules, name, module)
    return events, manifest, proof


def test_activation_hashes_before_path_import_and_after_verification_rehashes_every_file(monkeypatch):
    events, _, _ = mocked_activation(monkeypatch)
    receipt = numerical.activate(deepcopy(numerical.ENVIRONMENT), checkpoint)
    assert events == ["authenticated_evidence", "full_installed_hash", "import", "verify_imports",
                      "stable_identities", "mapped_hash"]
    assert receipt["installed_file_count"] == 2389
    assert receipt["environment_sha256"] == wire.digest(numerical._ENVIRONMENT_BYTES)
    result = numerical.verify_after(receipt, checkpoint)
    assert result["verified"] and result["full_installed_roster_rehashed"]
    assert result["mapped_binary_closure_unchanged"]
    assert events[6:] == ["authenticated_evidence", "full_installed_hash", "verify_imports", "mapped_hash"]


def test_failed_installed_hash_cannot_reach_path_activation_or_import(monkeypatch):
    events, _, _ = mocked_activation(monkeypatch)
    before = list(sys.path)
    def rejected(*args):
        raise ValueError("invented installed authentication failure")
    monkeypatch.setattr(numerical, "_hash_installed_files", rejected)
    with pytest.raises(ValueError, match="installed authentication"):
        numerical.activate(deepcopy(numerical.ENVIRONMENT), checkpoint)
    assert sys.path == before and events == ["authenticated_evidence"]


def test_preimported_numerical_library_is_refused_before_activation(monkeypatch):
    events, _, _ = mocked_activation(monkeypatch)
    before = list(sys.path)
    monkeypatch.setattr(numerical, "_numerical_already_imported", lambda: True)
    with pytest.raises(ValueError, match="already imported"):
        numerical.activate(deepcopy(numerical.ENVIRONMENT), checkpoint)
    assert sys.path == before and "import" not in events


def test_preimported_child_module_is_also_detected_without_an_import(monkeypatch):
    # A child module alone cannot be allowed to bypass root-package checks.
    modules = {name: module for name, module in sys.modules.items()
               if not any(name == package or name.startswith(package + ".") for package in numerical.VERSIONS)}
    with monkeypatch.context() as patch:
        patch.setattr(sys, "modules", modules)
        modules["numpy.invented_child"] = SimpleNamespace()
        assert numerical._numerical_already_imported()
        del modules["numpy.invented_child"]
        assert not numerical._numerical_already_imported()


@pytest.mark.parametrize("change", ["installed_map", "mapped_map", "runtime", "threads", "bytecode", "receipt"])
def test_after_verification_refuses_all_native_environment_drift(monkeypatch, change):
    _, manifest, _ = mocked_activation(monkeypatch)
    receipt = numerical.activate(deepcopy(numerical.ENVIRONMENT), checkpoint)
    if change == "installed_map":
        manifest["files"]["invented-0000.py"] = "e" * 64
    elif change == "mapped_map":
        monkeypatch.setattr(numerical, "_hash_mapped_libraries", lambda *args: {"/invented/extra.so": "e" * 64})
    elif change == "runtime":
        monkeypatch.setattr(numerical.bridge, "runtime_sha", lambda: "e" * 64)
    elif change == "threads":
        monkeypatch.setenv("OMP_NUM_THREADS", "2")
    elif change == "bytecode":
        sys.dont_write_bytecode = False
    else:
        receipt["registered"]["site_packages"] = "/invented/other"
    with pytest.raises(ValueError):
        numerical.verify_after(receipt, checkpoint)


def test_pinned_evidence_digest_is_checked_before_json_or_installed_bytes(monkeypatch):
    calls = []
    def invented(path, maximum):
        calls.append(path)
        return b"not the pinned native manifest"
    monkeypatch.setattr(numerical.bridge, "bounded_read", invented)
    with pytest.raises(ValueError, match="manifest/import proof differs"):
        numerical._authenticated_evidence(deepcopy(numerical.ENVIRONMENT))
    assert calls == [Path(numerical.ENVIRONMENT["manifest"]), Path(numerical.ENVIRONMENT["import_proof"])]


@pytest.mark.parametrize("change", [None, "status", "site", "runtime", "threads", "count", "market", "fits",
    "proof_status", "proof_versions", "proof_market", "proof_fits"])
def test_preparation_evidence_fields_are_checked_without_reading_installed_bytes(change):
    environment = deepcopy(numerical.ENVIRONMENT)
    manifest = {"status": "installed_not_qualified", "site_packages": environment["site_packages"],
        "runtime": numerical.RUNTIME, "thread_environment": dict(numerical.THREAD_ENVIRONMENT),
        "files": {f"invented-{index:04d}.py": "a" * 64 for index in range(2389)},
        "actual_market_inputs": 0, "model_fits": 0}
    proof = {"status": "invented_import_only_pass", "versions": dict(numerical.VERSIONS),
             "model_fits": 0, "market_inputs": 0}
    if change == "status":
        manifest["status"] = "qualified"
    elif change == "site":
        manifest["site_packages"] = "/invented/other"
    elif change == "runtime":
        manifest["runtime"] = "/invented/python"
    elif change == "threads":
        manifest["thread_environment"]["OMP_NUM_THREADS"] = "2"
    elif change == "count":
        manifest["files"].pop("invented-0000.py")
    elif change == "market":
        manifest["actual_market_inputs"] = 1
    elif change == "fits":
        manifest["model_fits"] = 1
    elif change == "proof_status":
        proof["status"] = "qualified"
    elif change == "proof_versions":
        proof["versions"]["numpy"] = "0"
    elif change == "proof_market":
        proof["market_inputs"] = 1
    elif change == "proof_fits":
        proof["model_fits"] = 1
    if change is None:
        numerical._validate_preparation(manifest, proof, environment)
    else:
        with pytest.raises(ValueError, match="preparation evidence"):
            numerical._validate_preparation(manifest, proof, environment)


def test_module_has_no_eager_numeric_import_installer_private_runtime_or_module_body_io():
    path = Path(__file__).resolve().parents[1] / "research/intraday_ed_numerical.py"
    tree = ast.parse(path.read_text())
    top_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(name in ast.unparse(node) for node in top_imports for name in ("numpy", "scipy", "lightgbm"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(name in ast.unparse(node) for node in imports
                   for name in ("subprocess", "socket", "requests", "importlib", "ec_ml_runtime"))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"eval", "exec", "compile", "__import__"} for node in ast.walk(tree))
    statements = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"open", "read_bytes", "read_text", "resolve", "file_digest", "bounded_read", "walk"}
        for statement in statements for node in ast.walk(statement))
