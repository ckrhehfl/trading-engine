"""Local lifecycle checks use invented receipts only, never research artifacts."""

from hashlib import sha256
import json
from types import ModuleType

import pytest

from research import intraday_candidate_search_run as run


def _journal(tmp_path):
    identity = {"merge": "1" * 40, "registration_sha256": "2" * 64, "attempt": "actual1"}
    common = {"study_id": run.UNIT, "parameters": identity, "promotion_allowed": False,
              "code_version": identity["merge"], "specification_sha256": identity["registration_sha256"], "run_id": "invented"}
    rows = [{**common, "status": "started"}, {**common, "status": "completed", "result": {"answer": 1}}]
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    path = tmp_path / "experiments.jsonl"
    path.write_bytes(raw)
    return path, identity, raw


def test_durable_trial_pair_preserves_exact_result_and_identity(tmp_path):
    path, identity, raw = _journal(tmp_path)
    proof = run._proof(path, identity, {"answer": 1}, 0, len(raw), "completed")
    assert proof["run_id"] == "invented"
    assert proof["terminal_sha256"] == sha256(raw.splitlines(keepends=True)[1]).hexdigest()
    with pytest.raises(ValueError, match="result differs"):
        run._proof(path, identity, {"answer": 2}, 0, len(raw), "completed")
    with pytest.raises(ValueError, match="identity differs"):
        run._proof(path, {**identity, "attempt": "actual2"}, {"answer": 1}, 0, len(raw), "completed")


def test_partial_or_extra_journal_is_not_completion(tmp_path):
    path, identity, raw = _journal(tmp_path)
    with pytest.raises(ValueError, match="pair missing"):
        run._proof(path, identity, {"answer": 1}, 0, len(raw) - 1, "completed")
    path.write_bytes(raw + raw)
    with pytest.raises(ValueError, match="pair missing"):
        run._proof(path, identity, {"answer": 1}, 0, len(raw) * 2, "completed")


def test_closed_loader_uses_reviewed_bytes_and_refuses_unknown_module():
    loader = run._ReviewedModules({"research.invented": ("python/research/invented.py", b"VALUE = 7\n")})
    module = ModuleType("research.invented")
    loader.exec_module(module)
    assert module.VALUE == 7
    assert loader.find_spec("json") is None
    with pytest.raises(ValueError, match="Unregistered"):
        loader.find_spec("research.not_reviewed")


def test_third_identical_timeout_does_not_loop(monkeypatch):
    identity = {"attempt": "actual3", "invented": False}
    span = {"start": 0, "end": 1, "run_id": "invented", "terminal_sha256": "0" * 64}

    def document(path):
        text = str(path)
        number = 1 if "actual1" in text else 2
        previous = {**identity, "attempt": f"actual{number}"}
        if path.name == "timeout.json":
            return {"identity": previous, "error_type": "TimeoutError", "journal": span}
        return previous

    monkeypatch.setattr(run, "_doc", document)
    monkeypatch.setattr(run, "_proof", lambda *args: span)
    monkeypatch.setattr(run.os.path, "lexists", lambda _: False)
    with pytest.raises(ValueError, match="alternative"):
        run._retry(identity)


def test_timeout_retry_refuses_an_ambiguous_publication(monkeypatch):
    identity = {"attempt": "actual2", "invented": False}
    previous = {**identity, "attempt": "actual1"}
    monkeypatch.setattr(run, "_doc", lambda path: previous if path.name != "timeout.json" else
                        {"identity": previous, "error_type": "TimeoutError", "journal": {}})
    monkeypatch.setattr(run.os.path, "lexists", lambda path: path.name == "report.json")
    with pytest.raises(ValueError, match="ambiguous publication"):
        run._retry(identity)


def test_full_invented_geometry_and_separate_saved_audit(tmp_path):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    reports, outputs = run._calculate({}, parents, tmp_path, True)
    for report in reports.values():
        comparison = report["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        assert comparison["groups"]["all"]["totals"]["selected_triggers"]["profit66"] > 0
    audited, _ = run._calculate({}, parents, tmp_path, True, audit=True, stored=outputs)
    assert audited == reports
