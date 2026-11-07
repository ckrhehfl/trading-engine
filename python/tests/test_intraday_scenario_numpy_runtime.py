"""Exercise the local NumPy pin with invented package bytes."""

from pathlib import Path
import os
import py_compile
import subprocess
import sys

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_scenario_numpy_runtime as runtime


pytestmark = pytest.mark.skipif(sys.platform != "linux" or sys.version_info[:2] != (3, 12),
                                reason="isolated Linux CPython 3.12 worker")
SOURCE = Path(__file__).absolute().parents[1]


@pytest.fixture
def invented(tmp_path):
    site = tmp_path / "python/.venv/lib/python3.12/site-packages"
    (site / "numpy").mkdir(parents=True)
    (site / "numpy.libs").mkdir()
    (site / "numpy/__init__.py").write_text("from .helper import value\n__version__ = '2.5.2'\n", encoding="utf-8")
    (site / "numpy/helper.py").write_text("value = 'new'\n", encoding="utf-8")
    (site / "numpy.libs/libinvented.so").write_bytes(b"invented native bytes")
    return tmp_path, site


def test_manifest_hashes_canonical_relative_names_and_bundled_libraries(invented):
    root, site = invented
    names = ("numpy.libs/libinvented.so", "numpy/__init__.py", "numpy/helper.py")
    rows = []
    for name in names:
        raw = (site / name).read_bytes()
        rows.append({"name": name, "size": len(raw), "sha256": wire.digest(raw)})
    manifest = runtime.inspect_numpy_installation(root, lambda _: None)
    assert manifest == runtime.NumPyRuntime(wire.digest(wire.encode(rows)), 3, sum(row["size"] for row in rows))
    (site / names[0]).write_bytes(b"different native bytes")
    assert runtime.inspect_numpy_installation(root, lambda _: None).manifest_sha256 != manifest.manifest_sha256


@pytest.mark.parametrize("alias", ("symlink", "hardlink", "sourceless", "invalid-name"))
def test_refuses_aliases_and_unapproved_entry_names(invented, alias):
    root, site = invented
    source = site / "numpy/helper.py"
    if alias == "symlink":
        (site / "numpy/alias.py").symlink_to(source)
    elif alias == "hardlink":
        os.link(source, site / "numpy/alias.py")
    elif alias == "sourceless":
        (site / "numpy/alias.pyc").write_bytes(b"invented")
    else:
        (site / "numpy/invalid name.py").write_bytes(b"invented")
    with pytest.raises(ValueError, match="exclusive regular|sourceless|entry bound or name"):
        runtime.inspect_numpy_installation(root, lambda _: None)


def test_refuses_identity_change_after_member_hash(invented, monkeypatch):
    root, site = invented
    source = site / "numpy/helper.py"
    original = wire.file_digest

    def mutate(path, maximum, checkpoint):
        value = original(path, maximum, checkpoint)
        if path == source:
            path.write_text("value = 'mutated'\n", encoding="utf-8")
        return value

    monkeypatch.setattr(wire, "file_digest", mutate)
    with pytest.raises(ValueError, match="changed during verification"):
        runtime.inspect_numpy_installation(root, lambda _: None)


def test_activation_uses_pinned_source_without_site_packages_or_bytecode(invented):
    root, site = invented
    source = site / "numpy/helper.py"
    source.write_text("value = 'old'\n", encoding="utf-8")
    before = source.stat()
    py_compile.compile(str(source), doraise=True)
    source.write_text("value = 'new'\n", encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    (site / "should_not_be_importable.py").write_text("raise AssertionError('unreviewed')\n", encoding="utf-8")
    (site / "unreviewed.pth").write_text("import should_not_be_importable\n", encoding="utf-8")
    expected = runtime.inspect_numpy_installation(root, lambda _: None)
    script = """
import importlib.util
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from research.intraday_scenario_numpy_runtime import activate_pinned_numpy
try:
    activate_pinned_numpy(Path(sys.argv[2]), expected_manifest_sha256='0' * 64, checkpoint=lambda _: None)
except ValueError as error:
    assert 'manifest pin differs' in str(error) and 'numpy' not in sys.modules
else:
    raise AssertionError('bad pin accepted')
proof = activate_pinned_numpy(Path(sys.argv[2]), expected_manifest_sha256=sys.argv[3], checkpoint=lambda _: None)
import numpy
assert proof.manifest_sha256 == sys.argv[3] and numpy.value == 'new'
assert sys.argv[4] not in sys.path and importlib.util.find_spec('should_not_be_importable') is None
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script, str(SOURCE), str(root), expected.manifest_sha256, str(site)],
                   check=True, timeout=10)
