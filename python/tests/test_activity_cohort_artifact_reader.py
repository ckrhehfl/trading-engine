"""Synthetic private-file reads under the component process memory boundary."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from research import activity_cohort_preflight as module


def test_small_artifact_does_not_reserve_the_global_file_ceiling(tmp_path):
    os.chmod(tmp_path, 0o700)
    artifact = tmp_path / "tiny.json"
    artifact.write_bytes(b"{}\n")
    os.chmod(artifact, 0o600)
    program = """
import resource
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from research.activity_cohort_preflight import _read_large
resource.setrlimit(resource.RLIMIT_AS, (192 * 1024**2,) * 2)
resident = bytearray(64 * 1024**2)
assert _read_large(Path(sys.argv[2])) == b'{}\\n'
assert len(resident) == 64 * 1024**2
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", program, str(Path(module.__file__).parents[1]), str(artifact)],
        capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode()


def test_growth_during_read_is_rejected(tmp_path, monkeypatch):
    os.chmod(tmp_path, 0o700)
    artifact = tmp_path / "growing.json"
    artifact.write_bytes(b"{}\n")
    os.chmod(artifact, 0o600)
    original = os.fdopen

    class GrowingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            with artifact.open("ab") as writer:
                writer.write(b" ")
            return self.stream.read(size)

    monkeypatch.setattr(module.os, "fdopen", lambda *args, **kwargs: GrowingStream(original(*args, **kwargs)))
    with pytest.raises(ValueError, match="changed during read"):
        module._read_large(artifact)
