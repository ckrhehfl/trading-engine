"""Exercise the cost census lifecycle using temporary synthetic evidence."""

import json

import pytest

from research import intraday_context_cost_census_run as runner


RELEASE = {"merge": "1" * 40, "reviewed": "2" * 40, "tree": "3" * 40, "registration_sha256": "4" * 64}
SPEC = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}],
        "window": {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}}


@pytest.fixture
def native(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    (tmp_path / "canonical").mkdir()
    (tmp_path / runner.JOURNAL).touch()
    monkeypatch.setattr(runner, "NATIVE", tmp_path)
    return tmp_path


def test_full_qualification_requires_separate_audit(native):
    identity = runner._identity(RELEASE, runner.QUALIFICATION, None)
    runner._execute(identity, SPEC, lambda: False)
    with pytest.raises(FileNotFoundError):
        runner._qualification_gate(RELEASE)
    runner._audit(identity, SPEC)
    assert len(runner._qualification_gate(RELEASE)) == 64
    report = json.loads((runner._slot(runner.QUALIFICATION) / "report.json").read_bytes())
    assert set(report["scenarios"]) == {"S1", "S2"}
    assert all(row["coordinates"] == 3140 and row["weeks"] == 34 for row in report["scenarios"].values())
    assert (native / runner.JOURNAL).stat().st_size == 0


@pytest.mark.parametrize("controlled", [False, True])
def test_only_controlled_timeout_admits_replacement(native, monkeypatch, controlled):
    original = runner._identity(RELEASE, "actual1", "5" * 64)

    def time_limit(*_):
        raise TimeoutError("invented")

    monkeypatch.setattr(runner, "_row_source", time_limit)
    with pytest.raises(TimeoutError, match="invented"):
        runner._execute(original, SPEC, lambda: controlled)
    assert [json.loads(line)["status"] for line in (native / runner.JOURNAL).read_bytes().splitlines()] == [
        "started", "failed"]
    assert runner._marker("actual1").is_file()
    assert not (runner._slot("actual1") / "completion.json").exists()
    if not controlled:
        with pytest.raises(FileNotFoundError):
            runner._allow_replacement(RELEASE, "5" * 64)
        return
    runner._allow_replacement(RELEASE, "5" * 64)
    with pytest.raises(ValueError, match="identity or state differs"):
        runner._allow_replacement(RELEASE, "6" * 64)
    (runner._slot("actual1") / "completion.json").touch()
    with pytest.raises(ValueError, match="controlled timeout"):
        runner._allow_replacement(RELEASE, "5" * 64)
