"""Invented ER lifecycle and chronology witnesses; never private evidence."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from research import intraday_event_calibration_run as runner


def args(mode="calibration-inner-qualification", *, inner=1, fold=None, **changes):
    values = {"mode": mode, "inner": inner, "fold": fold, "invented_inputs_sha256": "a" * 64,
        "dataset_binding_sha256": None, "calibration_inner_qualification_sha256": None,
        "calibration_qualification_sha256": None, "calibration_attempt": None,
        **dict.fromkeys(runner.EP_PINS)}
    return SimpleNamespace(**(values | changes))


def rewrite(path, value):
    path.write_bytes(runner.wire.encode(value))


def forbidden(*_args, **_kwargs):
    raise AssertionError("unregistered read, native fit, inference or process")


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
    monkeypatch.setattr(runner.run, "usage", lambda: {"worker_cpu_seconds": 7, "helper_cpu_seconds": 0, "wall_seconds": 9})
    monkeypatch.setattr(runner.run, "audit_budget", lambda _: calls.append("reserve_audit"))
    monkeypatch.setattr(runner.run, "_locks", nullcontext)
    monkeypatch.setattr(runner, "_public_predecessors", lambda _: calls.append("public_git"))
    monkeypatch.setattr(runner.bridge, "restrict_local_network", lambda: calls.append("network_off"))
    monkeypatch.setattr(runner.wire, "prohibit_ef_tasks", lambda: calls.append("no_children"))
    monkeypatch.setattr(runner.ep, "_source_after", lambda *_: calls.append("source_after"))
    monkeypatch.setattr(runner.native, "activate", forbidden)

    def inputs(identity):
        output = runner._slot(identity)
        assert runner.run._doc(output / "start.json") == identity
        journal = [json.loads(line) for line in runner._journal(output, identity).read_text().splitlines()]
        assert any(row["study_id"] == identity["id"] and row["status"] == "started"
                   and row["parameters"] == identity for row in journal)
        if (output / "completion.json").exists():
            assert runner.run._doc(output / "audit-start.json") == identity
        calls.append("private_inputs")
        return (), "c" * 64

    monkeypatch.setattr(runner.ep, "_invented_inputs", inputs)
    monkeypatch.setattr(runner.ep, "_actual_inputs", inputs)

    def inner(output, identity, _shards, binding, *, payload=None):
        calls.append("inner-audit" if payload else "inner-produce")
        if payload:
            return None
        rewrite(output / "fit-attempt.json", identity)
        rewrite(output / "fit-start.json", identity)
        model = {"fit_artifacts": 1, "artifact": {"native_joint_fit_count": 1,
                  "training_diagnostics": {"status": "complete"}}}
        files = {"model": runner.artifacts._document(output, "model.json", model),
            "predictions": runner.artifacts._stream(output, "predictions.jsonl", [{"key": "invented"}]),
            "components": runner.artifacts._stream(output, "components.jsonl", [{"key": "invented"}])}
        return {"inner": identity["inner"], "fit_attempt_count": 1, "fit_count": 1,
            "completed_native_fit_count": 1, "model_files": files, "inputs_sha256": binding,
            "geometry": {"sessions": 257, "coordinates": 5158}, "calendar": ["invented"] * 257,
            "support": {}, "runtime": {"verified": True, "kind": "tcn", "runtime_sha256": "d" * 64}}

    def outer(output, identity, _shards, binding, *, payload=None):
        calls.append("outer-audit" if payload else "outer-produce")
        if payload:
            return None
        pins = {arm: {"predictions": runner.artifacts._stream(output, f"{arm}-predictions.jsonl", [{"key": "invented"}]),
            "policy": runner.artifacts._document(output, f"{arm}-policy.json", {"invented": True})}
            for arm in runner.ARMS}
        return {"fold": identity["fold"], "fit_attempt_count": 0, "fit_count": 0,
            "inputs_sha256": binding, "arms": pins,
            "mean": runner.artifacts._document(output, "mean.json", {"invented": True}),
            "calibration": runner.artifacts._document(output, "calibration.json", {"invented": True}),
            "oof_predictions": runner.artifacts._stream(output, "oof-predictions.jsonl", []),
            "oof_provenance": runner.artifacts._document(output, "oof-provenance.json", {"invented": True}),
            "representatives": runner.artifacts._stream(output, "representatives.jsonl", []),
            "metrics": runner.artifacts._document(output, "metrics.json", {"fold": identity["fold"]}),
            "geometry": {"sessions": 257, "coordinates": 5158}, "calendar": ["invented"] * 257,
            "support": {}, "predecessor": {}}

    monkeypatch.setattr(runner, "_project_inner", inner)
    monkeypatch.setattr(runner, "_project_outer", outer)
    return tmp_path, source, calls


def qualify(source):
    request = args()
    runner.execute(request, {}, source, 0)
    runner.execute(args("calibration-inner-qualification-audit"), {}, source, 0)
    identity = runner._identity(source, "d" * 64, request)
    inner_pin = runner._audited(runner._slot(identity), identity)[1]
    request = args("calibration-qualification", inner=None, fold=3,
        calibration_inner_qualification_sha256=inner_pin,
        ep_invented_report_sha256="e" * 64, ep_invented_audit_sha256="f" * 64)
    runner.execute(request, {}, source, 0)
    request.mode = "calibration-qualification-audit"
    runner.execute(request, {}, source, 0)
    identity = runner._identity(source, "d" * 64, request)
    return inner_pin, runner._audited(runner._slot(identity), identity)[1]


def actual_inner(inner_pin, inner=0, *, audit=False, attempt=None):
    return args("calibration-inner-audit" if audit else "calibration-inner-actual", inner=inner,
        dataset_binding_sha256="b" * 64, calibration_inner_qualification_sha256=inner_pin,
        calibration_attempt=attempt)


def actual_outer(pins, fold=0, *, audit=False, summary=False, summary_pin="9" * 64):
    return args("calibration-summary" if summary else "calibration-audit" if audit else "calibration-actual",
        inner=None, fold=None if summary else fold, dataset_binding_sha256="b" * 64,
        calibration_inner_qualification_sha256=pins[0], calibration_qualification_sha256=pins[1],
        ep_invented_report_sha256="e" * 64, ep_invented_audit_sha256="f" * 64,
        ep_fold_report_sha256=[str(i + 1) * 64 for i in range(4)],
        ep_fold_audit_sha256=[str(i + 5) * 64 for i in range(4)], ep_summary_sha256=summary_pin)


def finish_inners(source, pin):
    for inner in range(2):
        runner.execute(actual_inner(pin, inner), {}, source, 0)
        runner.execute(actual_inner(pin, inner, audit=True), {}, source, 0)


def test_qualification_has_separate_native_and_consumer_units_and_durable_starts(runtime):
    _, source, calls = runtime
    assert all(len(pin) == 64 for pin in qualify(source))
    assert calls[:5] == ["budget", "public_git", "network_off", "no_children", "private_inputs"]
    assert calls.count("inner-produce") == calls.count("inner-audit") == 1
    assert calls.count("outer-produce") == calls.count("outer-audit") == 1


@pytest.mark.parametrize("changes", [{"inner": 0}, {"inner": True}, {"inner": -1}, {"fold": 0},
    {"kind": "tcn"}, {"scope": "full_chart"}, {"attempt": "actual1"}, {"mean_attempt": "actual1"},
    {"calibration_attempt": "actual1"}, {"dataset_binding_sha256": "b" * 64},
    {"ep_invented_report_sha256": "c" * 64}, {"invented_inputs_sha256": "bad"}])
def test_closed_cli_rejects_coordinate_and_pin_contamination(runtime, changes):
    root, source, _ = runtime
    with pytest.raises(ValueError):
        runner.execute(args(**changes), {}, source, 0)
    assert not list((root / "output").iterdir())


def test_outer_training_cannot_open_before_both_early_actual_audits(runtime, monkeypatch):
    _, source, _ = runtime
    pins = qualify(source)
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="preceding unit audit"):
        runner.execute(actual_outer(pins), {}, source, 0)


def test_second_early_unit_cannot_skip_first_early_audit(runtime, monkeypatch):
    _, source, _ = runtime
    pins = qualify(source)
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="preceding unit audit"):
        runner.execute(actual_inner(pins[0], 1), {}, source, 0)


def test_failed_inner_fit_preserved_before_one_same_input_retry(runtime, monkeypatch):
    _, source, _ = runtime
    pins = qualify(source)
    producer = runner._project_inner
    def timeout(output, identity, *_args, **_kwargs):
        rewrite(output / "fit-attempt.json", identity)
        rewrite(output / "fit-start.json", identity)
        raise TimeoutError("invented native deadline")
    monkeypatch.setattr(runner, "_project_inner", timeout)
    request = actual_inner(pins[0])
    with pytest.raises(TimeoutError):
        runner.execute(request, {}, source, 0)
    identity = runner._identity(source, "d" * 64, request)
    output = runner._slot(identity)
    failure = runner.run._doc(output / "failure.json")
    assert failure["recovery_supported"] is True and failure["partial"]["fit_started"] is True
    monkeypatch.setattr(runner, "_project_inner", producer)
    retry = actual_inner(pins[0], attempt="actual2")
    runner.execute(retry, {}, source, 0)
    runner.execute(actual_inner(pins[0], audit=True, attempt="actual2"), {}, source, 0)
    assert runner.run._doc(output / "failure.json") == failure
    report = runner._publication(runner._slot(runner._identity(source, "d" * 64, retry)),
                                 runner._identity(source, "d" * 64, retry))
    assert report["predecessor_timeouts"][0]["fit_started"] is True
    rewrite(output / "fit-start.json", {"changed": True})
    with pytest.raises(ValueError):
        runner._retry_predecessors({**identity, "attempt": "actual2"})


@pytest.mark.parametrize("name", ["model.json", "predictions.jsonl", "components.jsonl"])
def test_inner_artifact_tampering_is_rejected(runtime, name):
    _, source, _ = runtime
    qualify(source)
    identity = runner._identity(source, "d" * 64, args())
    (runner._slot(identity) / name).write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        runner._audited(runner._slot(identity), identity)


def test_complete_six_unit_lifecycle_summary_keeps_two_native_fits(runtime, monkeypatch):
    root, source, calls = runtime
    pins = qualify(source)
    finish_inners(source, pins[0])
    old = {"calendar": ["invented"] * 257, "inputs_sha256": "c" * 64,
        "fold_audits": [{"fold": i, "report_sha256": str(i + 1) * 64,
                         "audit_sha256": str(i + 5) * 64} for i in range(4)]}
    old_pin = runner.wire.digest(runner.wire.encode(old))
    for fold in range(4):
        for audit in (False, True):
            runner.execute(actual_outer(pins, fold, audit=audit, summary_pin=old_pin), {}, source, 0)
    old_output = root / "output" / (runner.ep.UNIT + "-summary")
    old_output.mkdir()
    rewrite(old_output / "start.json", {"id": runner.ep.UNIT + "-summary", "fold": None,
                                       "invented": False, "fit_attempt_count": 0})
    monkeypatch.setattr(runner.eq, "_old_source", lambda *_: None)
    def old_publication(*_):
        assert calls.count("outer-audit") == 5
        return old
    monkeypatch.setattr(runner.ep, "_publication", old_publication)
    monkeypatch.setattr(runner.metrics, "pool", lambda _calendar, reports: {"folds": [row["fold"] for row in reports]})
    checked = []
    monkeypatch.setattr(runner.reference, "verify_pool", lambda *_: checked.append(True))
    request = actual_outer(pins, summary=True, summary_pin=old_pin)
    runner.execute(request, {}, source, 0)
    identity = runner._identity(source, "d" * 64, request)
    report = runner._publication(runner._slot(identity), identity)
    assert report["fit_count"] == report["fit_attempt_count"] == report["completed_native_fit_count"] == 2
    assert report["additional_failed_fit_starts"] == 0
    assert report["pooled"] == {"folds": [0, 1, 2, 3]} and checked == [True]


def test_outer_seals_calibrator_predictions_and_choices_before_outcomes(tmp_path, monkeypatch):
    data = SimpleNamespace(base=SimpleNamespace(calendar=["invented"] * 257, support={}))
    roster = SimpleNamespace(episodes=(), candidates=())
    identity = {"fold": 3, "invented": True, "ep_invented_audit_sha256": "a" * 64}
    direct = {"arm": "direct", "predictions": [{"key": "direct"}]}
    monkeypatch.setattr(runner.eq, "_old_fold", lambda _: (tmp_path, {}, {}, {"direct": direct}, {"direct": {"arm": "direct"}}))
    monkeypatch.setattr(runner.native, "activate", forbidden)
    monkeypatch.setattr(runner.workflow, "load_fold", lambda *_: data)
    monkeypatch.setattr(runner.workflow, "decision_roster", lambda *_: roster)
    monkeypatch.setattr(runner.mean, "training_phases", lambda *_: {})
    monkeypatch.setattr(runner.mean, "training_targets", lambda *_: {})
    monkeypatch.setattr(runner, "_oof_predictions", lambda *_: ([], {"invented": True}))
    monkeypatch.setattr(runner.mean, "estimate", lambda *_: {"mean": True})
    monkeypatch.setattr(runner.calibration, "estimate", lambda *_: {"calibration": True})
    monkeypatch.setattr(runner.mean, "project", lambda *_: [{"key": "mean"}])
    monkeypatch.setattr(runner.calibration, "project", lambda *_: [{"key": "calibrated"}])
    monkeypatch.setattr(runner.mean, "freeze_policy", lambda *_: {"arm": "mean"})
    monkeypatch.setattr(runner.workflow, "freeze_policy", lambda *_: {"arm": "direct"})
    monkeypatch.setattr(runner.calibration, "freeze_policy", lambda *_: {"arm": "calibrated"})
    monkeypatch.setattr(runner.workflow, "serialize_policy", lambda policy: policy)
    monkeypatch.setattr(runner.metrics, "select_representatives", lambda *_: [])
    calls = []
    def prior(*_):
        for name in ("mean.json", "calibration.json", "oof-predictions.jsonl", "oof-provenance.json", "representatives.jsonl"):
            assert (tmp_path / name).exists()
        assert all((tmp_path / f"{arm}-{name}").exists() for arm in runner.ARMS
                   for name in ("predictions.jsonl", "policy.json"))
        calls.append("prior_economics")
    monkeypatch.setattr(runner.eq, "_old_outcomes", prior)
    def targets(*_):
        assert calls == ["prior_economics"]
        calls.append("outer_targets")
        return ()
    monkeypatch.setattr(runner.eq, "_evaluation_records", targets)
    for module in (runner.mean, runner.workflow, runner.calibration):
        monkeypatch.setattr(module, "join_policy_targets", lambda *_: ())
    monkeypatch.setattr(runner.metrics, "summarize", lambda *_: {})
    result = runner._project_outer(tmp_path, identity, [(None, {"counts": {"coordinates": 5158}})], "b" * 64)
    assert calls == ["prior_economics", "outer_targets"] and result["fit_count"] == 0


@pytest.mark.parametrize("outer", range(4))
def test_actual_oof_uses_only_complete_preceding_blocks(monkeypatch, outer):
    calendar = tuple((date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(257))
    stop = 97 + 40 * outer
    rows = tuple(SimpleNamespace(key=f"row{i}", session=calendar[i]) for i in range(stop))
    data = SimpleNamespace(base=SimpleNamespace(calendar=calendar, training=rows))
    identity = {"invented": False, "fold": outer, "dataset_binding_sha256": "b",
                "inner_qualification_sha256": "q", "ep_fold_audit_sha256": ["a"] * 4}
    def result(first, end):
        return {"arm": "direct", "kind": "tcn", "scope": "common_chart_peer", "artifact": None,
            "training_sessions": list(calendar[:first]), "evaluation_sessions": list(calendar[first:end]),
            "predictions": [{"key": row.key, "session": row.session} for row in rows[first:end]]}
    seen = []
    def inner(unit):
        index = unit["inner"]
        first, end = ((49, 73), (73, 97))[index]
        return index, {"model_files": {"predictions": {"sha256": "p"}}, "result": result(first, end)}, "a"
    monkeypatch.setattr(runner, "_audited_unit", inner)
    monkeypatch.setattr(runner, "_saved_inner", lambda _path, report: report["result"])
    monkeypatch.setattr(runner.oof, "invented_oof", forbidden)
    def old(unit):
        fold = unit["fold"]
        assert fold < outer
        seen.append(fold)
        first, end = 97 + 40 * fold, 137 + 40 * fold
        return None, None, {"arms": {"direct": {"predictions": {"sha256": "p"}}}}, {"direct": result(first, end)}, None
    monkeypatch.setattr(runner.eq, "_old_fold", old)
    predictions, provenance = runner._oof_predictions(identity, data)
    assert [row["key"] for row in predictions] == [row.key for row in rows[49:stop]]
    assert seen == list(range(outer)) and len(provenance["blocks"]) == 2 + outer
    assert provenance["invented"] is False


def test_oof_block_rejects_future_predictions_and_changed_training_cutoff():
    calendar = tuple((date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(257))
    data = SimpleNamespace(base=SimpleNamespace(calendar=calendar,
        training=(SimpleNamespace(key="a", session=calendar[49]),)))
    result = {"arm": "direct", "kind": "tcn", "scope": "common_chart_peer", "artifact": None,
        "training_sessions": list(calendar[:49]), "evaluation_sessions": list(calendar[49:73]),
        "predictions": [{"key": "a", "session": calendar[49]}]}
    runner._check_oof_block(result, data, 49, 73)
    for field, value in (("training_sessions", list(calendar[:73])),
                         ("predictions", [{"key": "a", "session": calendar[97]}])):
        with pytest.raises(ValueError, match="past-only"):
            runner._check_oof_block({**result, field: value}, data, 49, 73)
