"""Invented files/mocked backends only; these tests never admit the native installs."""
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from research import intraday_ed_numerical as numerical
from research import intraday_event_native as native


def tiny_tree(tmp_path):
    site = tmp_path / "invented-site"
    package = site / "package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"invented source\n")
    (package / "libinvented.so").write_bytes(b"invented native bytes\n")
    return site


def test_retained_inventory_binds_every_file_directory_and_byte(tmp_path):
    site = tiny_tree(tmp_path)
    rows = [["directory", "."], ["directory", "package"]]
    size = 0
    for path in sorted((site / "package").iterdir()):
        raw = path.read_bytes()
        size += len(raw)
        rows.append(["file", str(path.relative_to(site)), len(raw), sha256(raw).hexdigest()])
    canonical = (json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n").encode()
    got = native.retained_inventory(site)
    assert got == {"files": 2, "directories": 2, "bytes": size, "sha256": sha256(canonical).hexdigest()}
    (site / "extra").mkdir()
    assert native.retained_inventory(site)["sha256"] != got["sha256"]


@pytest.mark.parametrize("change", ["symlink_file", "symlink_directory", "hardlink", "bytecode", "cache_file"])
def test_tree_rejects_aliases_and_standalone_bytecode(tmp_path, change):
    site = tiny_tree(tmp_path)
    target = site / "package/__init__.py"
    if change == "symlink_file":
        (site / "alias.py").symlink_to(target)
    elif change == "symlink_directory":
        (site / "alias").symlink_to(site / "package", target_is_directory=True)
    elif change == "hardlink":
        (site / "alias.py").hardlink_to(target)
    elif change == "bytecode":
        (site / "cached.pyc").write_bytes(b"not admitted")
    else:
        (site / "__pycache__").write_bytes(b"not a directory")
    with pytest.raises(ValueError):
        native.retained_inventory(site)


def test_only_inert_cache_directory_is_excluded_and_external_wheel_links_are_disclosed(tmp_path):
    site = tiny_tree(tmp_path)
    before = native.retained_inventory(site)
    cache = site / "package/__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"invented inert cache")
    (tmp_path / "external-wheel-link").hardlink_to(site / "package/__init__.py")
    assert native.retained_inventory(site) == before


def test_legacy_pins_cannot_omit_missing_or_changed_expected_file(tmp_path):
    site = tiny_tree(tmp_path)
    path = site / "package/__init__.py"
    pin = sha256(path.read_bytes()).hexdigest()
    assert native.retained_inventory(site, {"package/__init__.py": pin})["files"] == 2
    with pytest.raises(ValueError, match="legacy retained"):
        native.retained_inventory(site, {"package/__init__.py": "a" * 64})
    with pytest.raises(ValueError, match="tree changed"):
        native.retained_inventory(site, {"missing.py": pin})


def test_file_mutated_after_its_hash_is_caught_before_inventory_returns(tmp_path, monkeypatch):
    site = tiny_tree(tmp_path)
    original = native.retained_file_sha
    first = site / "package/__init__.py"

    def changed_after_another_hash(path, before, **kwargs):
        result = original(path, before, **kwargs)
        if path == "libinvented.so":
            first.write_bytes(b"changed after its successful hash\n")
        return result

    monkeypatch.setattr(native, "retained_file_sha", changed_after_another_hash)
    with pytest.raises(ValueError, match="tree changed"):
        native.retained_inventory(site)


def test_inventory_detects_new_entry_during_hash(tmp_path, monkeypatch):
    site = tiny_tree(tmp_path)
    original = native.retained_file_sha

    def added(path, before, **kwargs):
        result = original(path, before, **kwargs)
        (site / "unseen.py").write_bytes(b"invented extra")
        return result

    monkeypatch.setattr(native, "retained_file_sha", added)
    with pytest.raises(ValueError, match="tree changed"):
        native.retained_inventory(site)


def test_file_changed_before_open_is_rejected(tmp_path):
    path = tmp_path / "invented.py"
    path.write_bytes(b"first")
    before = path.stat()
    path.write_bytes(b"second and larger")
    with pytest.raises(ValueError, match="before open"):
        native.retained_file_sha(path, before)


def test_file_change_during_hash_is_rejected(tmp_path):
    path = tmp_path / "invented.py"
    path.write_bytes(b"first")
    before = path.stat()

    def mutate(_stage):
        path.write_bytes(b"second and larger")

    with pytest.raises(ValueError, match="during hashing"):
        native.retained_file_sha(path, before, checkpoint=mutate)


def test_restricted_finder_never_adds_site_or_executes_pth(tmp_path, monkeypatch):
    site = tiny_tree(tmp_path)
    (site / "startup.pth").write_text("raise AssertionError('not executed')\n")
    seen = []
    spec = SimpleNamespace(origin=str(site / "package/__init__.py"))

    def find(fullname, paths, target):
        seen.append((fullname, paths, target))
        return spec

    monkeypatch.setattr(native.PathFinder, "find_spec", find)
    finder = native.NativeAttachment(site, frozenset({"package"}))
    assert finder.find_spec("unregistered") is None
    assert finder.find_spec("package.child", [str(site / "package")]) is None
    assert finder.find_spec("package") is spec
    assert seen == [("package", [str(site)], None)]
    spec.origin = str(tmp_path / "outside.py")
    with pytest.raises(ValueError, match="escaped"):
        finder.find_spec("package")


def test_metadata_bytes_are_authenticated_before_json_or_installed_file_access(monkeypatch):
    calls = []

    def read(path, maximum):
        calls.append((path.name, maximum))
        return b"invented invalid JSON and wrong pin"

    monkeypatch.setattr(native.bridge, "bounded_read", read)
    monkeypatch.setattr(native, "retained_inventory", lambda *a, **k: pytest.fail("native tree accessed"))
    with pytest.raises(ValueError, match="metadata pin"):
        native._torch_tree(lambda _: None)
    assert calls == [("ef-backend-runtime-pin-v1.json", 65536), ("ef-comparison-torch-pins-v1.json", 65536)]


def test_fixed_metadata_contract_is_checked_with_only_invented_json(monkeypatch):
    pin = {"schema": "ef-retained-torch-runtime-v1", "torch": native.TORCH_VERSION,
        "prefix": str(native.TORCH_PREFIX), "site_packages": str(native.TORCH_SITE),
        "interpreter": str(native.TORCH_PREFIX / "venv/bin/python"),
        "interpreter_sha256": native.RUNTIME_SHA256, "files": {"invented.py": "a" * 64}}
    raw = json.dumps(pin).encode()
    pin_sha = sha256(raw).hexdigest()
    tree = {"schema": "ef-comparison-torch-tree-v1", "torch": native.TORCH_VERSION,
        "site_packages": str(native.TORCH_SITE), "legacy_pin_sha256": pin_sha,
        "admitted_modules": sorted(native.TORCH_MODULES), "inventory": native.TORCH_INVENTORY}
    tree_raw = json.dumps(tree).encode()
    monkeypatch.setattr(native, "TORCH_PIN_SHA256", pin_sha)
    monkeypatch.setattr(native, "TORCH_TREE_PIN_SHA256", sha256(tree_raw).hexdigest())
    monkeypatch.setattr(native.bridge, "bounded_read", lambda path, maximum:
                        raw if path.name == "ef-backend-runtime-pin-v1.json" else tree_raw)
    assert native._torch_pins() == (pin, tree)
    tree["admitted_modules"].append("unregistered")
    tree_raw = json.dumps(tree).encode()
    monkeypatch.setattr(native, "TORCH_TREE_PIN_SHA256", sha256(tree_raw).hexdigest())
    with pytest.raises(ValueError, match="fixed retained Torch"):
        native._torch_pins()


@pytest.mark.parametrize("name", ["/absolute.py", "../escape.py", "sub\\alias.py", ""])
def test_legacy_file_metadata_cannot_escape_prefix(tmp_path, monkeypatch, name):
    monkeypatch.setattr(native, "TORCH_PREFIX", tmp_path)
    monkeypatch.setattr(native, "_torch_pins", lambda: ({"files": {name: "a" * 64}}, {}))
    with pytest.raises(ValueError, match="relative file pin"):
        native._torch_tree(lambda _: None)


def mocked_worker(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(native, "_attempted", False)
    monkeypatch.setattr(native, "_active_receipt", None)
    monkeypatch.setattr(native, "_active_finder", None)
    monkeypatch.setattr(native, "_active_path", None)
    monkeypatch.setattr(native, "_preimported", lambda: False)
    monkeypatch.setattr(native, "_isolated", lambda: events.append("isolated"))
    monkeypatch.setattr(native, "_prepare", lambda path: events.append("prepare"))
    monkeypatch.setattr(native, "_controls", lambda path: events.append("controls"))
    monkeypatch.setattr(native, "_runtime_sha", lambda checkpoint: native.RUNTIME_SHA256)
    monkeypatch.setattr(sys, "path", ["/invented/stdlib"])
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    monkeypatch.setattr(native, "_torch_tree", lambda checkpoint: events.append("tree") or dict(native.TORCH_INVENTORY))
    monkeypatch.setattr(native, "_check_torch", lambda torch: events.append("check_torch"))
    monkeypatch.setattr(native, "_prepare_torch_templates", lambda *args: events.append("torch_templates"))
    torch = SimpleNamespace(set_num_threads=lambda n: events.append(("threads", n)),
        set_num_interop_threads=lambda n: events.append(("interop", n)),
        use_deterministic_algorithms=lambda enabled: events.append(("deterministic", enabled)))

    def imported():
        events.append("import_torch")
        assert "tree" in events and isinstance(sys.meta_path[0], native.NativeAttachment)
        monkeypatch.setitem(sys.modules, "torch", torch)
        return torch

    monkeypatch.setattr(native, "_import_torch", imported)
    return events


def test_tcn_admission_hashes_before_import_and_rehashes_after_computation(tmp_path, monkeypatch):
    events = mocked_worker(tmp_path, monkeypatch)
    receipt = native.activate("tcn", tmp_path)
    assert events.index("tree") < events.index("import_torch")
    assert events.index("import_torch") < events.index("torch_templates")
    assert events.count("tree") == 1
    assert receipt["backend"]["system_shared_library_closure_pinned"] is False
    assert receipt["os_hermetic"] is False and receipt["resource_limits_owned_by_caller"] is True
    result = native.verify_after(receipt)
    assert result["verified"] and result["backend"]["full_installed_roster_rehashed"]
    assert events.count("tree") == 2
    assert sys.path == ["/invented/stdlib"]


@pytest.mark.parametrize("kind", ["empirical", "lightgbm"])
def test_classical_admission_reuses_exact_existing_authenticator_then_restricts_namespaces(tmp_path, monkeypatch, kind):
    events = mocked_worker(tmp_path, monkeypatch)
    backend = {"invented-authenticated-receipt": True}

    def activate(environment, checkpoint):
        assert environment == numerical.ENVIRONMENT
        events.append("classical_activate")
        sys.path.insert(0, environment["site_packages"])
        return backend

    def verify(receipt, checkpoint):
        assert receipt == backend
        events.append("classical_verify")
        return {"verified": True}

    monkeypatch.setattr(numerical, "activate", activate)
    monkeypatch.setattr(numerical, "verify_after", verify)
    receipt = native.activate(kind, tmp_path)
    assert receipt["backend"] == backend
    assert sys.path == ["/invented/stdlib"]
    assert sys.meta_path[0].modules == native.CLASSICAL_MODULES
    assert native.verify_after(receipt)["backend"] == {"verified": True}
    assert "classical_activate" in events and "classical_verify" in events
    assert "tree" not in events and "import_torch" not in events


def test_bad_tree_never_reaches_import_and_spends_worker_process(tmp_path, monkeypatch):
    events = mocked_worker(tmp_path, monkeypatch)

    def failed(checkpoint):
        raise ValueError("invented tree authentication failure")

    monkeypatch.setattr(native, "_torch_tree", failed)
    before = list(sys.meta_path)
    with pytest.raises(ValueError, match="tree authentication"):
        native.activate("tcn", tmp_path)
    assert sys.meta_path == before and "import_torch" not in events
    with pytest.raises(ValueError, match="fresh single-backend"):
        native.activate("lightgbm", tmp_path)


@pytest.mark.parametrize("change", ["receipt", "finder", "finder_modules", "finder_directory", "path", "tree", "runtime", "controls"])
def test_after_verification_refuses_changed_receipt_or_runtime_boundary(tmp_path, monkeypatch, change):
    mocked_worker(tmp_path, monkeypatch)
    receipt = native.activate("tcn", tmp_path)
    if change == "receipt":
        receipt["kind"] = "lightgbm"
    elif change == "finder":
        sys.meta_path.pop(0)
    elif change == "finder_modules":
        sys.meta_path[0].modules = frozenset({"unregistered"})
    elif change == "finder_directory":
        sys.meta_path[0].directory = tmp_path
    elif change == "path":
        sys.path.append("/invented/unapproved")
    elif change == "tree":
        changed = dict(native.TORCH_INVENTORY, files=1)
        monkeypatch.setattr(native, "_torch_tree", lambda checkpoint: changed)
    elif change == "runtime":
        monkeypatch.setattr(native, "_runtime_sha", lambda checkpoint: "a" * 64)
    else:
        def failed(workdir):
            raise ValueError("invented environment changed")
        monkeypatch.setattr(native, "_controls", failed)
    with pytest.raises(ValueError):
        native.verify_after(receipt)


@pytest.mark.parametrize("kind", ["unknown", "TCN", "LGB", None])
def test_unsupported_learner_fails_without_any_runtime_io(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(native, "_preimported", lambda: pytest.fail("reached native admission"))
    with pytest.raises(ValueError, match="fixed EL native learner"):
        native.activate(kind, tmp_path)


def test_preimported_child_is_detected_without_importing_a_backend(monkeypatch):
    modules = {"torch.invented_child": SimpleNamespace()}
    monkeypatch.setattr(sys, "modules", modules)
    assert native._preimported()
    modules.clear()
    modules["numpy.invented_child"] = SimpleNamespace()
    assert native._preimported()
    modules.clear()
    assert not native._preimported()


def test_prepare_creates_exclusive_tmp_and_never_cleans_prior_contents(tmp_path, monkeypatch):
    for name in native._environment(tmp_path):
        monkeypatch.setenv(name, "invented-before")
    retained = tmp_path / "retained.txt"
    retained.write_bytes(b"preserve me")
    native._prepare(tmp_path)
    assert (tmp_path / "tmp").is_dir()
    assert all(os.environ[name] == value for name, value in native._environment(tmp_path).items())
    with pytest.raises(FileExistsError):
        native._prepare(tmp_path)
    assert retained.read_bytes() == b"preserve me"


def test_isolation_rejects_readable_bytecode_cache_even_when_writes_are_disabled(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "version_info", (3, 12))
    monkeypatch.setattr(sys, "flags", SimpleNamespace(isolated=1, no_site=1))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    with pytest.raises(ValueError, match="disabled bytecode cache"):
        native._isolated()


def test_source_import_has_no_eager_backend_import_runtime_io_or_private_entrypoint():
    source = Path(__file__).parents[1] / "research/intraday_event_native.py"
    tree = ast.parse(source.read_text())
    top = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom))
        and any(name in ast.unparse(node) for name in ("import torch", "import numpy", "import lightgbm"))
        for node in top)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"open", "read_bytes", "read_text", "resolve", "file_digest", "bounded_read", "fwalk"}
        for statement in top for node in ast.walk(statement))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom))
        and any(name in ast.unparse(node) for name in ("subprocess", "socket", "ef_comparison_runtime"))
        for node in ast.walk(tree))
