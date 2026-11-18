"""Invented EQ lifecycle, admission and timing checks; no private evidence."""

from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from research import intraday_event_mean_run as runner


def args(mode="phase-mean-qualification", *, fold=3, **values):
    defaults = {"mode": mode, "fold": fold, "invented_inputs_sha256": "a" * 64,
        "dataset_binding_sha256": None, "mean_qualification_sha256": None, "mean_attempt": None,
        "ep_invented_report_sha256": "e" * 64, "ep_invented_audit_sha256": "f" * 64,
        "ep_fold_report_sha256": None, "ep_fold_audit_sha256": None, "ep_summary_sha256": None}
    return SimpleNamespace(**(defaults | values))


def rewrite(path, value):
    path.write_bytes(runner.wire.encode(value))


def forbidden(*_args, **_kwargs):
    raise AssertionError("Unregistered read, native fit, inference or child process")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    (tmp_path / "canonical").mkdir()
    (tmp_path / "canonical/experiments.jsonl").touch(mode=0o600)
    source = {"merge": "1" * 40, "reviewed": "2" * 40, "tree": "3" * 40,
              "sources": {runner.PLAN: "4" * 64}}
    calls = []
    monkeypatch.setattr(runner.run, "NATIVE", tmp_path)
    monkeypatch.setattr(runner.run, "budget", lambda: calls.append("budget"))
    monkeypatch.setattr(runner.run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(runner.run, "usage", lambda: {"worker_cpu_seconds": 7,
        "helper_cpu_seconds": 0, "wall_seconds": 9})
    monkeypatch.setattr(runner.run, "audit_budget", lambda producer: calls.append("reserve_audit"))
    monkeypatch.setattr(runner.run, "_locks", nullcontext)
    monkeypatch.setattr(runner, "_public_predecessors", lambda _: calls.append("public_git"))
    monkeypatch.setattr(runner.bridge, "restrict_local_network", lambda: calls.append("network_off"))
    monkeypatch.setattr(runner.wire, "prohibit_ef_tasks", lambda: calls.append("no_children"))
    monkeypatch.setattr(runner.ep, "_source_after", lambda *_: calls.append("source_after"))
    monkeypatch.setattr(runner.ep.native, "activate", forbidden)
    monkeypatch.setattr(runner.workflow, "fit_unit", forbidden)
    monkeypatch.setattr(runner.workflow, "verify_unit", forbidden)

    def inputs(identity):
        slot = runner._slot(identity)
        assert runner.run._doc(slot / "start.json") == identity
        records = [json.loads(line) for line in runner._journal(slot, identity).read_text().splitlines()]
        assert any(row["study_id"] == identity["id"] and row["status"] == "started"
                   and row["parameters"] == identity for row in records)
        if (slot / "completion.json").exists():
            assert runner.run._doc(slot / "audit-start.json") == identity
        calls.append("private_inputs")
        return (), "c" * 64

    monkeypatch.setattr(runner.ep, "_invented_inputs", inputs)
    monkeypatch.setattr(runner.ep, "_actual_inputs", inputs)

    def project(output, identity, _shards, binding, *, payload=None):
        calls.append("audit" if payload else "produce")
        if payload:
            return None
        pins = {arm: {
            "predictions": runner.artifacts._stream(output, f"{arm}-predictions.jsonl", [{"key": "invented"}]),
            "policy": runner.artifacts._document(output, f"{arm}-policy.json", {"invented": True})}
            for arm in runner.ARMS}
        return {"fold": identity["fold"], "native_fit_count": 0, "native_inference_count": 0,
            "inputs_sha256": binding, "arms": pins,
            "baseline": runner.artifacts._document(output, "baseline.json", {"invented": True}),
            "representatives": runner.artifacts._stream(output, "representatives.jsonl", [{"key": "invented"}]),
            "metrics": runner.artifacts._document(output, "metrics.json", {"fold": identity["fold"]}),
            "geometry": {"sessions": 257, "coordinates": 5158}, "calendar": ["invented"] * 257,
            "support": {}, "predecessor": {}}

    monkeypatch.setattr(runner, "_project", project)
    return tmp_path, source, calls


def qualify(source):
    runner.execute(args(), {}, source, 0)
    runner.execute(args("phase-mean-qualification-audit"), {}, source, 0)
    identity = runner._identity(source, "d" * 64, args(), invented=True, fold=3)
    return runner._audited(runner._slot(identity), identity)[1]


def actual(qualification, *, fold=0, mode="phase-mean-actual", attempt=None):
    return args(mode, fold=fold, dataset_binding_sha256="b" * 64,
        mean_qualification_sha256=qualification, mean_attempt=attempt,
        ep_fold_report_sha256=[str(i + 1) * 64 for i in range(4)],
        ep_fold_audit_sha256=[str(i + 5) * 64 for i in range(4)], ep_summary_sha256="9" * 64)


def test_qualification_inputs_follow_start_and_public_git_precedes_no_fork(runtime):
    _, source, calls = runtime
    assert len(qualify(source)) == 64
    assert calls[:5] == ["budget", "public_git", "network_off", "no_children", "private_inputs"]
    assert calls.count("produce") == calls.count("audit") == calls.count("reserve_audit") == 1


@pytest.mark.parametrize("values", [{"fold": 0}, {"fold": -1}, {"fold": True}, {"fold": 4},
    {"kind": "tcn"}, {"scope": "full_chart"}, {"shard": 0}, {"attempt": "actual1"},
    {"mean_attempt": "actual1"}, {"dataset_binding_sha256": "b" * 64},
    {"factorized_attempt": "actual1"}, {"invented_inputs_sha256": "bad"},
    {"ep_invented_report_sha256": None}, {"ep_fold_report_sha256": ["c" * 64] * 4},
    {"ep_summary_sha256": "d" * 64}])
def test_closed_cli_rejects_other_scopes_and_pin_contamination(runtime, values):
    root, source, _ = runtime
    request = args(**values)
    with pytest.raises(ValueError):
        runner.execute(request, {}, source, 0)
    assert not list((root / "output").iterdir())


def test_actual_requires_current_largest_fold_qualification(runtime, monkeypatch):
    _, source, _ = runtime
    pin = qualify(source)
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="qualification"):
        runner.execute(actual("b" * 64), {}, source, 0)
    assert pin != "b" * 64


def test_previous_fold_failure_cannot_be_skipped(runtime, monkeypatch):
    _, source, _ = runtime
    pin = qualify(source)
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="preceding fold audit"):
        runner.execute(actual(pin, fold=1), {}, source, 0)


def test_retry_preserves_failed_artifacts_and_rejects_tampering(runtime, monkeypatch):
    _, source, _ = runtime
    pin = qualify(source)
    producer = runner._project
    def timeout(*_args, **_kwargs):
        raise TimeoutError("invented budget")
    monkeypatch.setattr(runner, "_project", timeout)
    request = actual(pin)
    with pytest.raises(TimeoutError):
        runner.execute(request, {}, source, 0)
    identity = runner._identity(source, "d" * 64, request, fold=0)
    output = runner._slot(identity)
    failure = runner.run._doc(output / "failure.json")
    assert failure["recovery_supported"] is True
    monkeypatch.setattr(runner, "_project", producer)
    runner.execute(actual(pin, attempt="actual2"), {}, source, 0)
    runner.execute(actual(pin, mode="phase-mean-audit", attempt="actual2"), {}, source, 0)
    assert runner.run._doc(output / "failure.json") == failure
    rewrite(output / "baseline.json", {"unrecorded": True})
    second = {**identity, "attempt": "actual2"}
    with pytest.raises(ValueError, match="technical timeout"):
        runner._retry_predecessors(second)


@pytest.mark.parametrize("artifact", ["baseline.json", "mean-predictions.jsonl", "direct-policy.json", "metrics.json"])
def test_saved_artifact_tampering_is_rejected(runtime, artifact):
    _, source, _ = runtime
    qualify(source)
    identity = runner._identity(source, "d" * 64, args(), invented=True, fold=3)
    output = runner._slot(identity)
    (output / artifact).write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        runner._audited(output, identity)


def test_old_release_runtime_is_top_level_and_dispatcher_is_only_source_exception():
    source = {**runner.EP_RELEASE, "sources": {runner.ep.PLAN: "p", "python/research/old.py": "q",
              runner.ep.DISPATCHER: "old"}}
    old = {"source": source, "merge": runner.EP_RELEASE["merge"], "runtime_sha256": "r",
        "registration_sha256": "p", "invented_inputs_sha256": "i", "dataset_binding_sha256": None,
        "scope": "common_chart_peer", "arms": list(runner.ep.ARMS)}
    identity = {"source": {"sources": {**source["sources"], runner.ep.DISPATCHER: "new"}},
        "runtime_sha256": "r", "invented_inputs_sha256": "i", "dataset_binding_sha256": None}
    runner._old_source(old, identity)
    bad = deepcopy(identity)
    bad["source"]["sources"]["python/research/old.py"] = "changed"
    with pytest.raises(ValueError, match="unchanged EP"):
        runner._old_source(old, bad)
    bad = {**identity, "runtime_sha256": "changed"}
    with pytest.raises(ValueError, match="unchanged EP"):
        runner._old_source(old, bad)


def test_project_seals_all_choices_before_old_or_new_outcomes_and_never_infers(tmp_path, monkeypatch):
    calls = []
    data = SimpleNamespace(base=SimpleNamespace(calendar=tuple("invented" for _ in range(257)), support={}))
    roster = SimpleNamespace(episodes=(), candidates=())
    identity = {"fold": 0, "invented": True, "ep_invented_audit_sha256": "a" * 64}
    results = {arm: {"arm": arm, "predictions": [{"key": arm}], "components": []} for arm in runner.ep.ARMS}
    old_policies = {arm: {"arm": arm} for arm in runner.ep.ARMS}
    monkeypatch.setattr(runner, "_old_fold", lambda _: (tmp_path, {}, {}, results, old_policies))
    monkeypatch.setattr(runner.ep.native, "activate", forbidden)
    monkeypatch.setattr(runner.workflow, "fit_unit", forbidden)
    monkeypatch.setattr(runner.workflow, "verify_unit", forbidden)
    monkeypatch.setattr(runner.workflow, "load_fold", lambda *_: data)
    monkeypatch.setattr(runner.workflow, "decision_roster", lambda *_: roster)
    monkeypatch.setattr(runner.baseline, "training_phases", lambda *_: {})
    monkeypatch.setattr(runner.baseline, "training_targets", lambda *_: {})
    monkeypatch.setattr(runner.baseline, "estimate", lambda *_: {"mean": "-1/10"})
    monkeypatch.setattr(runner.baseline, "project", lambda *_: [{"key": "mean"}])
    monkeypatch.setattr(runner.baseline, "freeze_policy", lambda *_: {"arm": "mean"})
    monkeypatch.setattr(runner.workflow, "freeze_policy", lambda _data, _roster, result: {"arm": result["arm"]})
    monkeypatch.setattr(runner.workflow, "serialize_policy", lambda policy: policy)
    monkeypatch.setattr(runner.metrics, "select_representatives", lambda *_args, **_kwargs: [{"key": "rep"}])
    def old_outcomes(*_):
        assert (tmp_path / "baseline.json").exists()
        assert (tmp_path / "representatives.jsonl").exists()
        assert all((tmp_path / f"{arm}-{name}").exists() for arm in runner.ARMS
                   for name in ("predictions.jsonl", "policy.json"))
        calls.append("old_outcomes")
    monkeypatch.setattr(runner, "_old_outcomes", old_outcomes)
    def targets(*_):
        assert calls == ["old_outcomes"]
        calls.append("targets")
        return ()
    monkeypatch.setattr(runner.workflow, "evaluation_records", targets)
    monkeypatch.setattr(runner.workflow, "join_policy_targets", lambda *_: ())
    monkeypatch.setattr(runner.baseline, "join_policy_targets", lambda *_: ())
    monkeypatch.setattr(runner.metrics, "summarize", lambda *_: {"invented": True})
    result = runner._project(tmp_path, identity, [(None, {"counts": {"coordinates": 5158}})], "c" * 64)
    assert calls == ["old_outcomes", "targets"]
    assert result["native_fit_count"] == result["native_inference_count"] == 0


def test_summary_cannot_read_ep_summary_before_all_four_new_audits(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_qualification", lambda _: None)
    monkeypatch.setattr(runner.run, "_doc", forbidden)
    def fold(_identity, number):
        calls.append(number)
        if number == 3:
            raise ValueError("missing fourth audit")
        return tmp_path, {"metrics": {}, "inputs_sha256": "i", "calendar": ["d"]}, "a"
    monkeypatch.setattr(runner, "_audited_fold", fold)
    monkeypatch.setattr(runner.ep, "_read_document", lambda *_: {})
    with pytest.raises(ValueError, match="fourth audit"):
        runner._summary(tmp_path, {})
    assert calls == [0, 1, 2, 3]


def test_four_fold_invented_lifecycle_and_final_summary_are_bound(runtime, monkeypatch):
    root, source, calls = runtime
    pin = qualify(source)
    old_summary = {"calendar": ["invented"] * 257, "inputs_sha256": "c" * 64,
        "fold_audits": [{"fold": i, "report_sha256": str(i + 1) * 64,
                         "audit_sha256": str(i + 5) * 64} for i in range(4)]}
    summary_pin = runner.wire.digest(runner.wire.encode(old_summary))
    for fold in range(4):
        for mode in ("phase-mean-actual", "phase-mean-audit"):
            request = actual(pin, fold=fold, mode=mode)
            request.ep_summary_sha256 = summary_pin
            runner.execute(request, {}, source, 0)
    old_output = root / "output" / (runner.ep.UNIT + "-summary")
    old_output.mkdir()
    rewrite(old_output / "start.json", {"id": runner.ep.UNIT + "-summary", "fold": None,
                                       "invented": False, "fit_attempt_count": 0})
    monkeypatch.setattr(runner, "_old_source", lambda *_: None)
    def prior_summary(*_):
        assert calls.count("audit") == 5
        return old_summary
    monkeypatch.setattr(runner.ep, "_publication", prior_summary)
    monkeypatch.setattr(runner.metrics, "pool", lambda _calendar, reports: {"folds": [row["fold"] for row in reports]})
    verified = []
    monkeypatch.setattr(runner.reference, "verify_pool", lambda *_: verified.append(True))
    request = actual(pin, fold=None, mode="phase-mean-summary")
    request.ep_summary_sha256 = summary_pin
    runner.execute(request, {}, source, 0)
    identity = runner._identity(source, "d" * 64, request, summary=True)
    report = runner._publication(runner._slot(identity), identity)
    assert report["pooled"] == {"folds": [0, 1, 2, 3]}
    assert len(report["fold_audits"]) == 4 and verified == [True]
    assert report["native_fit_count"] == report["native_inference_count"] == 0


def test_old_fold_stage_one_does_not_open_metrics_or_summary(tmp_path, monkeypatch):
    output = tmp_path / "output" / (runner.ep.UNIT + "-f3-" + runner.EP_RELEASE["merge"] + "-qualification1")
    output.mkdir(parents=True)
    old = {"id": runner.ep.UNIT + "-f3", "fold": 3, "invented": True,
           "fit_attempt_count": 2, "attempt": "qualification1"}
    rewrite(output / "start.json", old)
    arms = {}
    for arm in runner.ep.ARMS:
        arms[arm] = {
            "model": runner.artifacts._document(output, f"{arm}-model.json", {"arm": arm}),
            "predictions": runner.artifacts._stream(output, f"{arm}-predictions.jsonl", [{"key": "x"}]),
            "components": runner.artifacts._stream(output, f"{arm}-components.jsonl", []),
            "policy": runner.artifacts._document(output, f"{arm}-policy.json", {"arm": arm})}
    report = {"arms": arms, "metrics": {"filename": "must-not-open.json"}}
    monkeypatch.setattr(runner.run, "NATIVE", tmp_path)
    monkeypatch.setattr(runner, "_old_source", lambda *_: None)
    monkeypatch.setattr(runner, "_envelope", lambda *_args, **_kw: report)
    monkeypatch.setattr(runner, "_audit_receipt", lambda *_: "a" * 64)
    monkeypatch.setattr(runner.ep, "_publication", forbidden)
    identity = {"fold": 3, "invented": True, "ep_invented_audit_sha256": "a" * 64,
                "ep_invented_report_sha256": runner.wire.digest(runner.wire.encode(report))}
    admitted = runner._old_fold(identity)
    assert admitted[0] == output and set(admitted[3]) == set(runner.ep.ARMS)
    identity["ep_invented_report_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="pinned EP"):
        runner._old_fold(identity)
