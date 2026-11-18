"""Invented lifecycle tests for sealed ES scoring and no-refit verification."""

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from research import intraday_event_success_run as runner


RUNTIME = "d" * 64
BINDING = "c" * 64


def request(mode="success-qualification", *, fold=3, qualification=None, **overrides):
    actual = "qualification" not in mode
    return SimpleNamespace(**({"mode": mode, "fold": fold, "invented_inputs_sha256": "a" * 64,
        "dataset_binding_sha256": "b" * 64 if actual else None, "success_qualification_sha256": qualification,
        "success_attempt": None, "ep_invented_report_sha256": "e" * 64, "ep_invented_audit_sha256": "f" * 64,
        "ep_fold_report_sha256": [str(i + 1) * 64 for i in range(4)] if actual else None,
        "ep_fold_audit_sha256": [str(i + 5) * 64 for i in range(4)] if actual else None,
        "ep_summary_sha256": "9" * 64 if actual else None} | overrides))


def forbidden(*_args, **_kwargs):
    raise AssertionError("activity outside the invented ES unit")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    (tmp_path / "canonical").mkdir()
    (tmp_path / "canonical" / "experiments.jsonl").touch(mode=0o600)
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "4" * 64}}
    calls = []
    active_output = None
    monkeypatch.setattr(runner.run, "NATIVE", tmp_path)
    monkeypatch.setattr(runner.run, "budget", lambda: calls.append("budget"))
    monkeypatch.setattr(runner.run, "runtime_pin", lambda: RUNTIME)
    monkeypatch.setattr(runner.run, "usage", lambda: {"worker_cpu_seconds": 7, "helper_cpu_seconds": 0, "wall_seconds": 9})
    monkeypatch.setattr(runner.run, "audit_budget", lambda _: calls.append("audit-budget"))
    monkeypatch.setattr(runner.run, "_locks", nullcontext)
    monkeypatch.setattr(runner.eq, "_public_predecessors", lambda _: calls.append("public-git"))
    monkeypatch.setattr(runner.bridge, "restrict_local_network", lambda: calls.append("network-off"))
    monkeypatch.setattr(runner.wire, "prohibit_ef_tasks", lambda: calls.append("no-children"))
    monkeypatch.setattr(runner.ep, "_source_after", lambda *_: calls.append("source-after"))
    monkeypatch.setattr(runner.eq, "_old_outcomes", forbidden)
    monkeypatch.setattr(runner.ep, "_publication", forbidden)
    monkeypatch.setattr(runner.workflow, "freeze_policy", forbidden)

    def inputs(identity):
        nonlocal active_output
        output = runner._slot(identity)
        assert runner.run._doc(output / "start.json") == identity
        rows = [json.loads(row) for row in runner._journal(output, identity).read_bytes().splitlines()]
        assert any(row["status"] == "started" and row["parameters"] == identity for row in rows)
        if (output / "completion.json").exists():
            assert runner.run._doc(output / "audit-start.json") == identity
        active_output = output
        calls.append("inputs")
        return ((None, {"counts": {"coordinates": 5158}}),), BINDING

    monkeypatch.setattr(runner.ep, "_invented_inputs", inputs)
    monkeypatch.setattr(runner.ep, "_actual_inputs", inputs)
    data = SimpleNamespace(base=SimpleNamespace(calendar=["invented"] * 257, support={"original": True}))
    roster = SimpleNamespace(episodes=(), candidates=())
    adapted = object()
    direct = [{"key": "invented", "direct": 0.2}]

    def old_fold(identity):
        assert calls[-1] == "native-admission"
        calls.append("ep-forecasts")
        return None, None, {"fold": identity["fold"]}, {"direct": {"predictions": direct}}, None

    monkeypatch.setattr(runner.eq, "_old_fold", old_fold)
    monkeypatch.setattr(runner.native, "activate", lambda kind, scratch: calls.append("native-admission") or (kind, scratch))
    monkeypatch.setattr(runner.native, "verify_after", lambda admitted: {"verified": True, "kind": admitted[0],
                                                                        "runtime_sha256": RUNTIME})
    monkeypatch.setattr(runner.workflow, "load_fold", lambda *_: data)
    monkeypatch.setattr(runner.workflow, "decision_roster", lambda *_: roster)
    monkeypatch.setattr(runner.mean, "training_phases", lambda *_: {"phase": "invented"})
    monkeypatch.setattr(runner.mean, "training_targets", lambda *_: {"target": "invented"})
    monkeypatch.setattr(runner.success, "prepare", lambda *_: (adapted, {"rate": "1/2"}))
    monkeypatch.setattr(runner.success, "project", lambda *_: [{"key": "invented", "rate": "1/2"}])

    def fit(arm, training, *, before_fit):
        assert arm == "direct" and training is adapted
        before_fit()
        calls.append("fit")
        return {"arm": arm, "fit_artifacts": 1, "artifact": {"native_joint_fit_count": 1,
                "training_diagnostics": {"status": "complete"}},
                "predictions": [{"key": "invented", "success": 1.5}], "components": [{"key": "invented"}]}

    monkeypatch.setattr(runner.workflow, "fit_unit", fit)
    monkeypatch.setattr(runner.metrics, "select_representatives", lambda *_: [{"key": "invented"}])
    for module, name in ((runner.reference, "verify_training_inputs"), (runner.reference, "verify_training"),
                         (runner.ep_reference, "verify_fit"), (runner.workflow, "verify_unit"),
                         (runner.reference, "verify_projection"), (runner.reference, "verify_representatives")):
        monkeypatch.setattr(module, name, lambda *_, name=name: calls.append(name))

    def outcomes(*_):
        for name in ("fit-start.json", runner.MODEL, runner.RATE, runner.COMPONENTS, runner.REPRESENTATIVES,
                     *(runner._prediction_file(arm) for arm in runner.ARMS)):
            assert (active_output / name).is_file()
        if (active_output / "audit-start.json").exists():
            assert calls[-1] == "verify_representatives"
        calls.append("targets")
        yield {"key": "invented"}

    monkeypatch.setattr(runner.eq, "_evaluation_records", outcomes)
    monkeypatch.setattr(runner.metrics, "summarize", lambda _c, fold, _r, outcomes: {"fold": fold, "rows": list(outcomes)})
    monkeypatch.setattr(runner.reference, "verify_report", lambda _c, _f, _r, outcomes, saved:
                        calls.append("verify_report") if list(outcomes) == saved["rows"] else forbidden())
    return source, calls


def qualify(source):
    runner.execute(request(), {}, source, 0)
    runner.execute(request("success-qualification-audit"), {}, source, 0)
    identity = runner._identity(source, RUNTIME, request())
    return runner._audited(runner._slot(identity), identity)[1]


def test_qualification_seals_all_scores_then_audits_without_refitting(runtime):
    source, calls = runtime
    assert len(qualify(source)) == 64
    assert calls[:5] == ["budget", "public-git", "network-off", "no-children", "inputs"]
    assert calls.count("fit") == calls.count("verify_unit") == 1
    assert calls.count("audit-budget") == calls.count("verify_report") == 1
    assert calls.count("targets") == 2


@pytest.mark.parametrize("changes", ({"fold": 0}, {"fold": True}, {"inner": 0}, {"scope": "chart_peer"},
    {"success_attempt": "actual1"}, {"calibration_attempt": "actual1"}, {"ep_invented_audit_sha256": None},
    {"ep_fold_report_sha256": ["e" * 64] * 4}, {"dataset_binding_sha256": "b" * 64}))
def test_closed_cli_rejects_other_coordinates_and_pins(runtime, changes):
    source, _ = runtime
    with pytest.raises(ValueError, match="ES"):
        runner.execute(request(**changes), {}, source, 0)
    assert not list((runner.run.NATIVE / "output").iterdir())


@pytest.mark.parametrize("fold", (0, 1))
def test_actual_read_requires_same_release_qualification_and_preceding_audits(runtime, monkeypatch, fold):
    source, _ = runtime
    pin = qualify(source)
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="qualification" if fold == 0 else "preceding fold audit"):
        runner.execute(request("success-actual", fold=fold, qualification=pin if fold else "a" * 64), {}, source, 0)


def test_timed_out_fit_is_retained_and_retried_once_with_identical_pins(runtime, monkeypatch):
    source, _ = runtime
    pin = qualify(source)
    fit = runner.workflow.fit_unit

    def timeout(_arm, _data, *, before_fit):
        before_fit()
        raise TimeoutError("invented native deadline")

    monkeypatch.setattr(runner.workflow, "fit_unit", timeout)
    first = request("success-actual", fold=0, qualification=pin)
    with pytest.raises(TimeoutError):
        runner.execute(first, {}, source, 0)
    identity = runner._identity(source, RUNTIME, first)
    slot = runner._slot(identity)
    failure = runner.run._doc(slot / "failure.json")
    assert failure["recovery_supported"] and failure["partial"]["fit_started"]
    monkeypatch.setattr(runner.workflow, "fit_unit", fit)
    retry = request("success-actual", fold=0, qualification=pin, success_attempt="actual2")
    runner.execute(retry, {}, source, 0)
    retry.mode = "success-audit"
    runner.execute(retry, {}, source, 0)
    assert runner.run._doc(slot / "failure.json") == failure
    (slot / "fit-start.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="retained fit receipt"):
        runner._retry_predecessors({**identity, "attempt": "actual2"})


def test_timeout_with_ambiguous_completion_is_retained_without_retry(runtime, monkeypatch):
    source, _ = runtime
    pin = qualify(source)
    proof = runner.receipts._proof

    def timeout(path, identity, unit, result, start, end, *, failed=False):
        if identity["invented"] or failed:
            return proof(path, identity, unit, result, start, end, failed=failed)
        raise TimeoutError("invented deadline after journal completion")

    monkeypatch.setattr(runner.receipts, "_proof", timeout)
    first = request("success-actual", fold=0, qualification=pin)
    with pytest.raises(TimeoutError):
        runner.execute(first, {}, source, 0)
    identity = runner._identity(source, RUNTIME, first)
    output = runner._slot(identity)
    failure = runner.run._doc(output / "failure.json")
    assert failure["error_type"] == "TimeoutError" and failure["recovery_supported"] is False
    assert (output / "report.json").is_file() and not (output / "completion.json").exists()
    with pytest.raises(ValueError, match="authenticated same-input technical timeout"):
        runner._retry_predecessors({**identity, "attempt": "actual2"})


@pytest.mark.parametrize("file", (runner.RATE, runner.MODEL, runner.COMPONENTS,
    runner._prediction_file("direct"), runner._prediction_file("success"), runner.REPRESENTATIVES, runner.METRICS))
def test_audited_artifact_tampering_fails_admission(runtime, file):
    source, _ = runtime
    qualify(source)
    identity = runner._identity(source, RUNTIME, request())
    output = runner._slot(identity)
    (output / file).write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        runner._audited(output, identity)


def test_all_period_summary_binds_only_four_success_fits(runtime, monkeypatch):
    source, calls = runtime
    pin = qualify(source)
    request_template = request("success-actual", fold=0, qualification=pin)
    old = {"calendar": ["invented"] * 257, "inputs_sha256": BINDING,
        "fold_audits": [{"fold": fold, "report_sha256": request_template.ep_fold_report_sha256[fold],
                         "audit_sha256": request_template.ep_fold_audit_sha256[fold]} for fold in range(4)]}
    old_pin = runner.wire.digest(runner.wire.encode(old))
    for fold in range(4):
        for mode in ("success-actual", "success-audit"):
            runner.execute(request(mode, fold=fold, qualification=pin, ep_summary_sha256=old_pin), {}, source, 0)
    old_slot = runner.run.NATIVE / "output" / (runner.ep.UNIT + "-summary")
    old_slot.mkdir()
    (old_slot / "start.json").write_bytes(runner.wire.encode({"id": runner.ep.UNIT + "-summary", "fold": None,
                                                             "invented": False, "fit_attempt_count": 0}))
    monkeypatch.setattr(runner.eq, "_old_source", lambda *_: None)

    def old_publication(*_):
        assert calls.count("verify_report") == 5
        return old

    monkeypatch.setattr(runner.ep, "_publication", old_publication)
    monkeypatch.setattr(runner.metrics, "pool", lambda _calendar, reports: {"folds": [report["fold"] for report in reports]})
    monkeypatch.setattr(runner.reference, "verify_pool", lambda *_: calls.append("verify_pool"))
    summary = request("success-summary", fold=None, qualification=pin, ep_summary_sha256=old_pin)
    runner.execute(summary, {}, source, 0)
    identity = runner._identity(source, RUNTIME, summary)
    report = runner._publication(runner._slot(identity), identity)
    assert report["fit_attempt_count"] == report["fit_count"] == report["completed_native_fit_count"] == 4
    assert report["additional_failed_fit_starts"] == 0
    assert report["pooled"] == {"folds": [0, 1, 2, 3]}
    assert calls.count("verify_pool") == 1
