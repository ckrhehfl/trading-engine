"""Synthetic checks for ET admission, durable seals and bounded recovery."""

from contextlib import nullcontext
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from research import intraday_event_immediate_run as runner


PIN = "a" * 64
RUNTIME = "b" * 64
BINDING = "c" * 64
CALENDAR = tuple((date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(257))


def args(mode="immediate-inner-qualification", *, inner=1, fold=None, **changes):
    return SimpleNamespace(**({"mode": mode, "inner": inner, "fold": fold, "invented_inputs_sha256": PIN,
        "dataset_binding_sha256": None, "immediate_inner_qualification_sha256": None,
        "immediate_qualification_sha256": None, "immediate_attempt": None,
        **dict.fromkeys(runner.PREDECESSOR_PINS)} | changes))


def forbidden(*_args, **_kwargs):
    raise AssertionError("unregistered read, fit or process")


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    (tmp_path / "canonical").mkdir()
    (tmp_path / "canonical/experiments.jsonl").touch(mode=0o600)
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "d" * 64}}
    calls = []
    monkeypatch.setattr(runner.run, "NATIVE", tmp_path)
    monkeypatch.setattr(runner.run, "budget", lambda: calls.append("budget"))
    monkeypatch.setattr(runner.run, "runtime_pin", lambda: RUNTIME)
    monkeypatch.setattr(runner.run, "usage", lambda: {"worker_cpu_seconds": 7, "helper_cpu_seconds": 0, "wall_seconds": 9})
    monkeypatch.setattr(runner.run, "audit_budget", lambda _: calls.append("reserve-audit"))
    monkeypatch.setattr(runner.run, "_locks", nullcontext)
    monkeypatch.setattr(runner, "_public_predecessors", lambda _: calls.append("public-git"))
    monkeypatch.setattr(runner.bridge, "restrict_local_network", lambda: calls.append("network-off"))
    monkeypatch.setattr(runner.wire, "prohibit_ef_tasks", lambda: calls.append("no-children"))
    monkeypatch.setattr(runner.ep, "_source_after", lambda *_: calls.append("source-after"))

    def inputs(identity):
        slot = runner._slot(identity)
        assert runner.run._doc(slot / "start.json") == identity
        journal = runner._journal(slot, identity)
        assert any(row["status"] == "started" and row["parameters"] == identity
                   for row in map(json.loads, journal.read_bytes().splitlines()))
        if (slot / "completion.json").exists():
            assert runner.run._doc(slot / "audit-start.json") == identity
        calls.append("invented-inputs" if identity["invented"] else "substitute-actual-inputs")
        return (), BINDING

    monkeypatch.setattr(runner.ep, "_invented_inputs", inputs)
    monkeypatch.setattr(runner.ep, "_actual_inputs", inputs)

    def inner(output, identity, _shards, binding, *, payload=None):
        calls.append("inner-audit" if payload is not None else "inner-produce")
        return {"inner": identity["inner"], "inputs_sha256": binding, "calendar": list(CALENDAR),
            "geometry": runner.GEOMETRY, **dict.fromkeys(runner.FIT_COUNTS, 1),
            "runtime": {"verified": True, "kind": "tcn", "runtime_sha256": RUNTIME}}

    def outer(output, identity, _shards, binding, *, payload=None):
        calls.append("outer-audit" if payload is not None else "outer-produce")
        if payload is not None:
            return
        return {"fold": identity["fold"], "inputs_sha256": binding, "calendar": list(CALENDAR),
            "geometry": runner.GEOMETRY, **dict.fromkeys(runner.FIT_COUNTS, 0),
            "metrics": runner.artifacts._document(output, "metrics.json", {"fold": identity["fold"]})}

    monkeypatch.setattr(runner, "_project_inner", inner)
    monkeypatch.setattr(runner, "_project_outer", outer)
    return tmp_path, source, calls


def produce_and_audit(source, request):
    runner.execute(request, {}, source, 0)
    audit = request.mode.replace("-actual", "-audit") if request.mode.endswith("-actual") else request.mode + "-audit"
    runner.execute(SimpleNamespace(**(vars(request) | {"mode": audit})), {}, source, 0)
    identity = runner._identity(source, RUNTIME, request)
    return runner._audited(runner._slot(identity), identity)[1]


def qualify(source):
    inner_pin = produce_and_audit(source, args())
    outer = args("immediate-qualification", inner=None, fold=3, immediate_inner_qualification_sha256=inner_pin,
        **dict.fromkeys(runner.INVENTED_PINS, PIN))
    return inner_pin, produce_and_audit(source, outer)


def actual_inner(pins, inner=0, *, attempt=None):
    return args("immediate-inner-actual", inner=inner, dataset_binding_sha256=PIN,
        immediate_inner_qualification_sha256=pins[0], immediate_qualification_sha256=pins[1],
        immediate_attempt=attempt, **dict.fromkeys(runner.INVENTED_PINS, PIN))


def actual_outer(pins, fold=0):
    return args("immediate-summary" if fold is None else "immediate-actual", inner=None, fold=fold,
        dataset_binding_sha256=PIN, immediate_inner_qualification_sha256=pins[0],
        immediate_qualification_sha256=pins[1], ep_summary_sha256=PIN,
        **dict.fromkeys(runner.INVENTED_PINS, PIN), **dict.fromkeys(runner.FOLD_PINS, [PIN] * 4))


@pytest.mark.parametrize("overrides", ({"inner": 0}, {"inner": True}, {"fold": 3}, {"success_attempt": "actual1"},
    {"dataset_binding_sha256": PIN}, {"es_fold_audit_sha256": [PIN] * 4}, {"immediate_attempt": "actual2"}))
def test_qualification_rejects_foreign_pins_and_coordinates(lifecycle, overrides):
    root, source, _ = lifecycle
    with pytest.raises(ValueError, match="ET"):
        runner.execute(args(**overrides), {}, source, 0)
    assert list((root / "output").iterdir()) == []


def test_actual_requires_both_early_audits_before_opening_inputs(lifecycle, monkeypatch):
    _, source, calls = lifecycle
    pins = qualify(source)
    assert calls[:5] == ["budget", "public-git", "network-off", "no-children", "invented-inputs"]
    produce_and_audit(source, actual_inner(pins))
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="preceding unit audit"):
        runner.execute(actual_outer(pins), {}, source, 0)


def test_actual_inner_requires_outer_qualification_before_inputs(lifecycle, monkeypatch):
    _, source, _ = lifecycle
    inner_pin = produce_and_audit(source, args())
    monkeypatch.setattr(runner.ep, "_actual_inputs", forbidden)
    with pytest.raises(FileNotFoundError):
        runner.execute(actual_inner((inner_pin, PIN)), {}, source, 0)


def test_retry_before_first_attempt_does_not_consume_its_slot(lifecycle):
    _, source, _ = lifecycle
    request = actual_inner((PIN, PIN), attempt="actual2")
    with pytest.raises(ValueError, match="preceding failed slot"):
        runner.execute(request, {}, source, 0)
    assert not runner._slot(runner._identity(source, RUNTIME, request)).exists()


def test_foreign_audit_pin_does_not_consume_matching_audit_slot(lifecycle):
    _, source, _ = lifecycle
    pins = qualify(source)
    production = actual_inner(pins)
    runner.execute(production, {}, source, 0)
    wrong = actual_inner((pins[0], PIN))
    wrong.mode = "immediate-inner-audit"
    with pytest.raises(ValueError, match="audit producer identity"):
        runner.execute(wrong, {}, source, 0)
    path = runner._slot(runner._identity(source, RUNTIME, production))
    assert not (path / "audit-start.json").exists()
    production.mode = "immediate-inner-audit"
    runner.execute(production, {}, source, 0)
    assert (path / "audit.json").is_file()


def test_timeout_preserved_with_only_same_input_retry(lifecycle, monkeypatch):
    _, source, _ = lifecycle
    pins = qualify(source)
    produce = runner._project_inner

    def timeout(output, identity, *_args, **_kwargs):
        for name in ("fit-attempt.json", "fit-start.json"):
            runner.wire.exclusive_bytes(output / name, runner.wire.encode(identity))
        raise TimeoutError("synthetic deadline")

    monkeypatch.setattr(runner, "_project_inner", timeout)
    first = actual_inner(pins)
    with pytest.raises(TimeoutError):
        runner.execute(first, {}, source, 0)
    slot = runner._slot(runner._identity(source, RUNTIME, first))
    failure = runner.run._doc(slot / "failure.json")
    assert failure["recovery_supported"] and failure["partial"]["fit_started"]
    monkeypatch.setattr(runner, "_project_inner", produce)
    wrong = actual_inner((pins[0], PIN), attempt="actual2")
    with pytest.raises(ValueError, match="authenticated same-input"):
        runner.execute(wrong, {}, source, 0)
    assert not runner._slot(runner._identity(source, RUNTIME, wrong)).exists()
    retry = actual_inner(pins, attempt="actual2")
    produce_and_audit(source, retry)
    assert runner.run._doc(slot / "failure.json") == failure
    (slot / "fit-start.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="retained fit receipt"):
        runner._retry_predecessors(runner._identity(source, RUNTIME, retry))


def test_summary_binds_two_early_and_four_comparison_audits(lifecycle, monkeypatch):
    _, source, calls = lifecycle
    pins = qualify(source)
    for inner in range(2):
        produce_and_audit(source, actual_inner(pins, inner))
    for fold in range(4):
        produce_and_audit(source, actual_outer(pins, fold))
    monkeypatch.setattr(runner.es, "_verify_ep_summary", lambda *_: calls.append("ep-summary"))
    monkeypatch.setattr(runner.policy, "pool", lambda _days, reports: {"folds": [row["fold"] for row in reports]})
    monkeypatch.setattr(runner.reference, "verify_pool", lambda *_: calls.append("summary-audit"))
    request = actual_outer(pins, None)
    runner.execute(request, {}, source, 0)
    identity = runner._identity(source, RUNTIME, request)
    report = runner._publication(runner._slot(identity), identity)
    assert all(report[name] == 2 for name in runner.FIT_COUNTS)
    assert len(report["inner_audits"]) == 2 and len(report["fold_audits"]) == 4
    assert report["pooled"] == {"folds": [0, 1, 2, 3]}
    assert calls.count("ep-summary") == calls.count("summary-audit") == 1


def test_es_forecast_receipt_allows_only_pinned_scores_without_economics(lifecycle, monkeypatch):
    _, source, _ = lifecycle
    es_source = {**runner.ES_RELEASE, "sources": {runner.es.PLAN: "1" * 64, runner.ep.DISPATCHER: "2" * 64}}
    source["sources"].update({runner.es.PLAN: "1" * 64, runner.ep.DISPATCHER: "3" * 64})
    request = args("immediate-qualification", inner=None, fold=3, immediate_inner_qualification_sha256=PIN,
        **dict.fromkeys(runner.INVENTED_PINS, PIN))
    identity = runner._identity(source, RUNTIME, request)
    old = {**{name: identity[name] for name in ("invented", "runtime_sha256", "scope", "invented_inputs_sha256",
        "dataset_binding_sha256", *runner.er.EP_PINS)}, "id": runner.es.UNIT + "-f3",
        "source": es_source, "merge": runner.ES_RELEASE["merge"], "registration_sha256": "1" * 64,
        "fold": 3, "attempt": "qualification1", "fit_attempt_count": 1, "arms": list(runner.es.ARMS)}
    slot = runner.es._slot(old)
    runner.wire.exclusive_directory(slot)
    runner.wire.exclusive_bytes(slot / "start.json", runner.wire.encode(old))
    predictions = [{"key": "synthetic", "session": CALENDAR[-1]}]
    model = {"schema": "EP-model-unit-v1", "arm": "direct", "artifact": None, "artifact_sha256": None,
        "predictions_sha256": runner.wire.digest(runner.wire.encode(predictions)[:-1])}
    resources = runner.run.usage()
    report = {"identity": old, "fold": 3, "fit_attempt_count": 1, "resources": resources,
        "runtime": {"verified": True, "kind": "tcn", "runtime_sha256": RUNTIME},
        "predictions": {"success": runner.artifacts._stream(slot, "success-predictions.jsonl", predictions)},
        "model": runner.artifacts._document(slot, runner.es.MODEL, model),
        "metrics": {"filename": "metrics.json"}}
    raw = runner.wire.encode(report)
    runner.wire.exclusive_bytes(slot / "report.json", raw)
    result = {"report_sha256": runner.wire.digest(raw), "report_bytes": len(raw), "resources": resources}
    journal = runner.es._journal(slot, old)
    runner.experiment_log.run_discovery_trial(study_id=old["id"], specification_sha256=old["registration_sha256"],
        parameters=old, window=runner.run.WINDOW, evaluate=lambda: result, runs_path=journal,
        code_version_provider=lambda: old["merge"])
    span = runner.receipts._proof(journal, old, old["id"], result, 0, journal.stat().st_size)
    runner.wire.exclusive_bytes(slot / "completion.json", runner.wire.encode({"identity": old, "result": result, "journal": span}))
    runner.wire.exclusive_bytes(slot / "audit-start.json", runner.wire.encode(old))
    audit = runner.wire.encode({"identity": old, "report_sha256": result["report_sha256"],
        "independently_verified": True, "resources": resources})
    runner.wire.exclusive_bytes(slot / "audit.json", audit)
    identity.update(es_invented_report_sha256=result["report_sha256"], es_invented_audit_sha256=runner.wire.digest(audit))
    monkeypatch.setattr(runner.es, "_saved", forbidden)
    _, admitted, provenance = runner._success_forecast(identity, 3)
    assert admitted["predictions"] == predictions and provenance["audit_sha256"] == runner.wire.digest(audit)
    (slot / "success-predictions.jsonl").write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        runner._success_forecast(identity, 3)
    with pytest.raises(ValueError, match="publication or audit differs"):
        runner._success_forecast({**identity, "es_invented_audit_sha256": PIN}, 3)


def test_outer_seals_before_targets_and_audit_checks_seals_first(tmp_path, monkeypatch):
    fold = 3
    data = SimpleNamespace(base=SimpleNamespace(calendar=CALENDAR, support={}))
    roster = SimpleNamespace(episodes=(), candidates=())
    old = {"predictions": [{"key": "synthetic"}]}
    monkeypatch.setattr(runner, "_direct_forecast", lambda *_: ({}, old, {"direct": True}))
    monkeypatch.setattr(runner, "_success_forecast", lambda *_: ({}, old, {"success": True}))
    monkeypatch.setattr(runner, "_check_current", lambda *_: None)
    monkeypatch.setattr(runner, "_oof_predictions", lambda *_: ([], {"invented": True}))
    monkeypatch.setattr(runner.workflow, "load_fold", lambda *_: data)
    monkeypatch.setattr(runner.workflow, "decision_roster", lambda *_: roster)
    monkeypatch.setattr(runner.mean, "training_phases", lambda *_: {})
    monkeypatch.setattr(runner.mean, "training_targets", lambda *_: {})
    monkeypatch.setattr(runner.value, "estimate", lambda *_: {"mapped": True})
    monkeypatch.setattr(runner.value, "project", lambda *_: [{"key": "synthetic", "mapped": 0}])
    monkeypatch.setattr(runner.policy, "freeze", lambda *_: {arm: {"arm": arm} for arm in runner.ARMS})
    monkeypatch.setattr(runner.es_reference, "verify_training_inputs", lambda *_: None)
    monkeypatch.setattr(runner.value, "verify", lambda *_: None)
    monkeypatch.setattr(runner.value, "verify_projection", lambda *_: None)
    monkeypatch.setattr(runner.native, "activate", forbidden)
    monkeypatch.setattr(runner.eq, "_old_outcomes", forbidden)
    events = []
    monkeypatch.setattr(runner.reference, "verify_policies", lambda *_: events.append("audit-policy"))

    def targets(*_):
        required = {"mapping.json", "oof-predictions.jsonl", "oof-provenance.json", "current-success-predictions.jsonl"}
        required.update(f"{arm}-{name}" for arm in runner.ARMS for name in ("predictions.jsonl", "policy.json"))
        assert {path.name for path in tmp_path.iterdir()} >= required
        events.append("targets")
        return ({"target": 3},)

    monkeypatch.setattr(runner.eq, "_evaluation_records", targets)
    monkeypatch.setattr(runner.policy, "summarize", lambda *_: {"totals": 3})
    monkeypatch.setattr(runner.reference, "verify_report", lambda *_: events.append("audit-report"))
    identity = {"fold": fold}
    inputs = ((None, {"counts": {"coordinates": 5158}}),)
    report = runner._project_outer(tmp_path, identity, inputs, BINDING)
    runner._project_outer(tmp_path, identity, inputs, BINDING, payload=report)
    assert events == ["targets", "audit-policy", "targets", "audit-report"]
    assert all(report[name] == 0 for name in runner.FIT_COUNTS)
    (tmp_path / "mapped-policy.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        runner._project_outer(tmp_path, identity, inputs, BINDING, payload=report)
    assert len(events) == 4


@pytest.mark.parametrize("fold", range(4))
def test_actual_oof_uses_exactly_two_early_blocks_then_only_prior_es(monkeypatch, fold):
    stop = 97 + 40 * fold
    rows = tuple(SimpleNamespace(key=f"key-{i}", session=CALENDAR[i]) for i in range(stop))
    data = SimpleNamespace(base=SimpleNamespace(calendar=CALENDAR, training=rows))
    identity = {"invented": False, "fold": fold, "dataset_binding_sha256": PIN, "inner_qualification_sha256": PIN,
                "qualification_sha256": PIN, **dict.fromkeys(runner.INVENTED_PINS, PIN)}

    def model(start, end):
        return {"arm": "direct", "kind": "tcn", "scope": "common_chart_peer", "artifact": None, "support": {},
            "training_sessions": list(CALENDAR[:start]), "evaluation_sessions": list(CALENDAR[start:end]),
            "predictions": [{"key": row.key, "session": row.session} for row in rows[start:end]]}

    def early(unit):
        _, start, end = runner.early.SPLITS[unit["inner"]]
        return None, {"model": model(start, end), "model_files": {"predictions": {"sha256": PIN}},
            "calendar": list(CALENDAR), "inputs_sha256": BINDING, "identity": unit}, PIN

    seen = []

    def prior(unit, index):
        assert unit == identity and index < fold
        seen.append(index)
        _, start, end = runner.workflow.FOLDS[index]
        return {"inputs_sha256": BINDING, "calendar": list(CALENDAR), "support": {}}, \
            {**model(start, end), "fold": index}, {"predictions_sha256": PIN}

    monkeypatch.setattr(runner, "_audited_unit", early)
    monkeypatch.setattr(runner.er, "_saved_inner", lambda _output, report: report["model"])
    monkeypatch.setattr(runner.es, "_fit_receipts", lambda *_: None)
    monkeypatch.setattr(runner.es, "_fit_counts", lambda *_: {})
    monkeypatch.setattr(runner, "_success_forecast", prior)
    monkeypatch.setattr(runner.oof, "invented_oof", forbidden)
    predictions, provenance = runner._oof_predictions(identity, data, BINDING)
    assert [row["key"] for row in predictions] == [row.key for row in rows[49:stop]]
    assert seen == list(range(fold)) and len(provenance["blocks"]) == 2 + fold and not provenance["invented"]
