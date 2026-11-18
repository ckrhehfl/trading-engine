"""Invented EP lifecycle checks; never open private evidence or native packages."""

from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from research import intraday_event_factorized_run as runner


def args(mode="factorized-qualification", *, fold=3, **values):
    return SimpleNamespace(mode=mode, fold=fold, invented_inputs_sha256="a" * 64,
        dataset_binding_sha256=values.pop("dataset_binding_sha256", None),
        factorized_qualification_sha256=values.pop("factorized_qualification_sha256", None),
        factorized_attempt=values.pop("factorized_attempt", None), **values)


def rewrite(path, value):
    path.write_bytes(runner.wire.encode(value))


def forbidden(*_args, **_kwargs):
    raise AssertionError("Unregistered read, fit or child process")


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
    monkeypatch.setattr(runner, "_source_after", lambda *_: calls.append("source_after"))

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

    monkeypatch.setattr(runner, "_invented_inputs", inputs)
    monkeypatch.setattr(runner, "_actual_inputs", inputs)

    def project(output, identity, _shards, binding, *, payload=None):
        calls.append("audit" if payload else "produce")
        if payload:
            return None
        pins = {}
        for arm in runner.ARMS:
            runner.wire.exclusive_bytes(output / f"{arm}-fit-attempt.json",
                runner.wire.encode({"identity": identity, "arm": arm}))
            runner.wire.exclusive_bytes(output / f"{arm}-fit-start.json",
                runner.wire.encode({"identity": identity, "arm": arm}))
            model = {"arm": arm, "fit_artifacts": 1, "artifact": {"native_joint_fit_count": 1,
                "training_diagnostics": {"status": "complete"}}}
            pins[arm] = {"model": runner.artifacts._document(output, f"{arm}-model.json", model),
                "predictions": runner.artifacts._stream(output, f"{arm}-predictions.jsonl", [{"key": "invented"}]),
                "components": runner.artifacts._stream(output, f"{arm}-components.jsonl", [{"key": "invented"}]),
                "policy": runner.artifacts._document(output, f"{arm}-policy.json", {"invented": True})}
        return {"fold": identity["fold"], "fit_count": 2, "fit_attempt_count": 2,
            "completed_native_fit_count": 2, "fitted_artifact_count": 2, "inputs_sha256": binding,
            "arms": pins, "representatives": runner.artifacts._stream(output, "representatives.jsonl", [{"key": "invented"}]),
            "metrics": runner.artifacts._document(output, "metrics.json", {"fold": identity["fold"]}),
            "geometry": {"sessions": 257, "coordinates": 5158}, "calendar": ["invented"] * 257,
            "support": {}, "runtime": {"verified": True, "kind": "tcn", "runtime_sha256": "d" * 64}}

    monkeypatch.setattr(runner, "_project", project)
    return tmp_path, source, calls


def qualify(source):
    runner.execute(args(), {}, source, 0)
    runner.execute(args("factorized-qualification-audit"), {}, source, 0)
    identity = runner._identity(source, "d" * 64, args(), invented=True, fold=3)
    return runner._audited(runner._slot(identity), identity)[1]


def actual(qualification, *, fold=0, mode="factorized-actual", attempt=None):
    return args(mode, fold=fold, dataset_binding_sha256="b" * 64,
        factorized_qualification_sha256=qualification, factorized_attempt=attempt)


def test_qualification_public_git_precedes_no_fork_and_inputs_follow_durable_start(runtime):
    _, source, calls = runtime
    pin = qualify(source)
    assert len(pin) == 64
    assert calls[:5] == ["budget", "public_git", "network_off", "no_children", "private_inputs"]
    assert calls.count("produce") == calls.count("audit") == calls.count("reserve_audit") == 1


@pytest.mark.parametrize("values", [{"fold": 0}, {"fold": -1}, {"fold": 4}, {"fold": True},
    {"kind": "tcn"}, {"scope": "full_chart"}, {"shard": 0}, {"attempt": "actual1"},
    {"dataset_binding_sha256": "b" * 64}, {"invented_inputs_sha256": "bad"}])
def test_closed_cli_rejects_other_scopes_or_invalid_pins(runtime, values):
    root, source, _ = runtime
    request = args()
    for name, value in values.items():
        setattr(request, name, value)
    with pytest.raises(ValueError):
        runner.execute(request, {}, source, 0)
    assert not list((root / "output").iterdir())


def test_actual_requires_same_source_largest_fold_qualification_before_input(runtime, monkeypatch):
    root, source, _ = runtime
    monkeypatch.setattr(runner, "_actual_inputs", forbidden)
    with pytest.raises(FileNotFoundError):
        runner.execute(actual("e" * 64), {}, source, 0)
    slot = root / "output" / (runner.UNIT + "-f0-actual1")
    assert (slot / "start.json").exists() and (slot / "failure.json").exists()
    records = [json.loads(line) for line in (root / "canonical/experiments.jsonl").read_text().splitlines()]
    assert [row["status"] for row in records] == ["started", "failed"]


def test_actual_and_no_refit_audit_are_exclusive_and_budgeted_together(runtime):
    root, source, calls = runtime
    pin = qualify(source)
    request = actual(pin)
    runner.execute(request, {}, source, 0)
    request.mode = "factorized-audit"
    runner.execute(request, {}, source, 0)
    slot = root / "output" / (runner.UNIT + "-f0-actual1")
    assert runner.run._doc(slot / "audit.json")["independently_verified"] is True
    assert calls.count("produce") == calls.count("audit") == 2
    request.mode = "factorized-actual"
    with pytest.raises(FileExistsError):
        runner.execute(request, {}, source, 0)


@pytest.mark.parametrize("mutation", ["source", "runtime", "artifact", "audit", "fit_start"])
def test_qualification_rejects_changed_source_runtime_artifact_or_audit(runtime, mutation):
    _, source, _ = runtime
    pin = qualify(source)
    identity = runner._identity(source, "d" * 64, actual(pin), fold=0)
    qualified = runner._identity(source, "d" * 64, args(), invented=True, fold=3)
    slot = runner._slot(qualified)
    if mutation == "source":
        identity = deepcopy(identity)
        identity["source"]["sources"][runner.PLAN] = "e" * 64
    elif mutation == "runtime":
        identity["runtime_sha256"] = "e" * 64
    elif mutation == "artifact":
        (slot / "direct-predictions.jsonl").write_bytes(b"{}\n")
    elif mutation == "audit":
        value = runner.run._doc(slot / "audit.json")
        value["independently_verified"] = False
        rewrite(slot / "audit.json", value)
    else:
        rewrite(slot / "direct-fit-start.json", {"invented": True})
    with pytest.raises(ValueError):
        runner._qualification(identity)


def test_two_consecutive_timeouts_preserve_failures_and_refuse_unchanged_third_attempt(runtime, monkeypatch):
    root, source, _ = runtime
    pin = qualify(source)
    def timeout(*_args, **_kwargs):
        raise TimeoutError("invented controlled pre-fit timeout")

    monkeypatch.setattr(runner, "_project", timeout)
    retained = {}
    for attempt in ("actual1", "actual2"):
        with pytest.raises(TimeoutError):
            runner.execute(actual(pin, attempt=attempt), {}, source, 0)
        slot = root / "output" / (runner.UNIT + "-f0-" + attempt)
        retained[slot] = {path.name: path.read_bytes() for path in slot.iterdir()}
    monkeypatch.setattr(runner, "_actual_inputs", forbidden)
    with pytest.raises(ValueError):
        runner.execute(actual(pin, attempt="actual3"), {}, source, 0)
    for slot, files in retained.items():
        assert {path.name: path.read_bytes() for path in slot.iterdir()} == files
    assert not (root / "output" / (runner.UNIT + "-f0-actual3")).exists()


def test_postfit_timeout_retries_preserved_and_authenticated_partial_artifacts(runtime, monkeypatch):
    root, source, _ = runtime
    pin = qualify(source)
    project = runner._project

    def fail_after_fit(output, identity, *_args, **_kwargs):
        runner.wire.exclusive_bytes(output / "direct-fit-start.json",
            runner.wire.encode({"identity": identity, "arm": "direct"}))
        runner.artifacts._document(output, "direct-model.json", {"incomplete": True})
        raise TimeoutError("invented post-fit timeout")

    monkeypatch.setattr(runner, "_project", fail_after_fit)
    with pytest.raises(TimeoutError):
        runner.execute(actual(pin), {}, source, 0)
    previous = root / "output" / (runner.UNIT + "-f0-actual1")
    retained = {path.name: path.read_bytes() for path in previous.iterdir()}
    monkeypatch.setattr(runner, "_project", project)
    runner.execute(actual(pin, attempt="actual2"), {}, source, 0)
    assert {path.name: path.read_bytes() for path in previous.iterdir()} == retained
    report = runner.run._doc(root / "output" / (runner.UNIT + "-f0-actual2") / "report.json")
    assert report["predecessor_timeouts"][0]["fit_start_count"] == 1


@pytest.mark.parametrize("changed", ["partial", "ambiguous_report", "input"])
def test_retry_refuses_changed_or_ambiguous_failure_without_opening_inputs(runtime, monkeypatch, changed):
    root, source, _ = runtime
    pin = qualify(source)

    def fail(output, identity, *_args, **_kwargs):
        runner.artifacts._document(output, "direct-model.json", {"incomplete": True})
        raise TimeoutError("invented timeout")

    monkeypatch.setattr(runner, "_project", fail)
    with pytest.raises(TimeoutError):
        runner.execute(actual(pin), {}, source, 0)
    previous = root / "output" / (runner.UNIT + "-f0-actual1")
    request = actual(pin, attempt="actual2")
    if changed == "partial":
        rewrite(previous / "direct-model.json", {"incomplete": False})
    elif changed == "ambiguous_report":
        rewrite(previous / "report.json", {})
    else:
        request.dataset_binding_sha256 = "f" * 64
    monkeypatch.setattr(runner, "_actual_inputs", forbidden)
    with pytest.raises(ValueError):
        runner.execute(request, {}, source, 0)


def test_summary_requires_all_four_audits_and_binds_eight_fits(runtime, monkeypatch):
    root, source, _ = runtime
    pin = qualify(source)
    for fold in range(4):
        request = actual(pin, fold=fold)
        runner.execute(request, {}, source, 0)
        request.mode = "factorized-audit"
        runner.execute(request, {}, source, 0)
    monkeypatch.setattr(runner.metrics, "pool", lambda calendar, reports: {"folds": [r["fold"] for r in reports]})
    monkeypatch.setattr(runner.reference, "verify_pool", lambda calendar, reports, saved:
        None if saved == {"folds": list(range(4))} else forbidden())
    runner.execute(actual(pin, fold=None, mode="factorized-summary"), {}, source, 0)
    report = runner.run._doc(root / "output" / (runner.UNIT + "-summary") / "report.json")
    assert report["fit_count"] == 8
    assert [row["fold"] for row in report["fold_audits"]] == list(range(4))
    assert all(runner.wire.valid_sha(row["audit_sha256"]) for row in report["fold_audits"])


def test_public_predecessors_do_not_read_private_metadata(monkeypatch):
    calls = []

    def git(*command):
        calls.append(command)
        for release in (runner.FIXTURE_RELEASE, runner.DATASET_RELEASE):
            if command[0] == "show" and command[-1] == release["merge"]:
                return (release["merge"] + "\n" + release["tree"] + "\n" + "0" * 40 + " " + release["reviewed"] + "\n").encode()
            if command[0] == "rev-parse" and command[1] == release["reviewed"] + "^{tree}":
                return (release["tree"] + "\n").encode()
        assert command[0] == "merge-base"
        return b""

    monkeypatch.setattr(runner.run, "_git", git)
    monkeypatch.setattr(runner.run, "_doc", forbidden)
    runner._public_predecessors({"merge": "1" * 40})
    assert len(calls) == 6


def test_project_seals_both_arms_and_causal_representatives_before_targets(tmp_path, monkeypatch):
    data = SimpleNamespace(base=SimpleNamespace(calendar=tuple("invented" for _ in range(257)), support={}))
    roster = SimpleNamespace(episodes=(), candidates=())
    identity = {"fold": 3, "runtime_sha256": "d" * 64}
    calls = []
    monkeypatch.setattr(runner.native, "activate", lambda kind, scratch: calls.append("activate") or {})
    monkeypatch.setattr(runner.native, "verify_after", lambda _: {"verified": True, "kind": "tcn", "runtime_sha256": "d" * 64})
    monkeypatch.setattr(runner.workflow, "load_fold", lambda *_: data)
    monkeypatch.setattr(runner.workflow, "decision_roster", lambda *_: roster)
    monkeypatch.setattr(runner.reference, "verify_matched", lambda _: None)

    def fit(arm, _data, *, before_fit):
        before_fit()
        assert (tmp_path / f"{arm}-fit-start.json").exists()
        calls.append("fit-" + arm)
        return {"arm": arm, "fit_artifacts": 1, "artifact": {"native_joint_fit_count": 1,
                    "training_diagnostics": {"status": "complete"}},
                "predictions": [{"key": "invented"}], "components": [{"key": "invented"}]}

    monkeypatch.setattr(runner.workflow, "fit_unit", fit)
    monkeypatch.setattr(runner.workflow, "freeze_policy", lambda _data, _roster, result: {"arm": result["arm"]})
    monkeypatch.setattr(runner.workflow, "serialize_policy", lambda policy: policy)
    monkeypatch.setattr(runner.metrics, "select_representatives", lambda *_args, **_kwargs: [{"key": "invented"}])

    def targets(*_):
        for arm in runner.ARMS:
            for name in ("model.json", "predictions.jsonl", "components.jsonl", "policy.json"):
                assert (tmp_path / f"{arm}-{name}").exists()
        assert (tmp_path / "representatives.jsonl").exists()
        calls.append("targets")
        yield {"key": "invented"}

    monkeypatch.setattr(runner.workflow, "evaluation_records", targets)
    monkeypatch.setattr(runner.workflow, "join_policy_targets", lambda *_: ())
    monkeypatch.setattr(runner.metrics, "summarize", lambda *_: {"invented": True})
    result = runner._project(tmp_path, identity, [(None, {"counts": {"coordinates": 5158}})], "c" * 64)
    assert calls == ["activate", "fit-direct", "fit-factorized", "targets"]
    assert result["fit_count"] == 2


def test_empty_training_is_retained_as_unsupported_without_fabricated_native_fits(tmp_path, monkeypatch):
    data = SimpleNamespace(base=SimpleNamespace(calendar=tuple("invented" for _ in range(257)), support={}))
    roster = SimpleNamespace(episodes=(), candidates=())
    identity = {"fold": 0, "runtime_sha256": "d" * 64}
    monkeypatch.setattr(runner.native, "activate", lambda *_: {})
    monkeypatch.setattr(runner.native, "verify_after", lambda _: {"verified": True, "kind": "tcn", "runtime_sha256": "d" * 64})
    monkeypatch.setattr(runner.workflow, "load_fold", lambda *_: data)
    monkeypatch.setattr(runner.workflow, "decision_roster", lambda *_: roster)
    monkeypatch.setattr(runner.reference, "verify_matched", lambda _: None)
    monkeypatch.setattr(runner.workflow, "fit_unit", lambda arm, _data, before_fit:
        {"arm": arm, "status": "unsupported_empty_training", "fit_artifacts": 0,
         "artifact": None, "predictions": [], "components": []})
    monkeypatch.setattr(runner.workflow, "freeze_policy", lambda _data, _roster, result: {"arm": result["arm"]})
    monkeypatch.setattr(runner.workflow, "serialize_policy", lambda policy: policy)
    monkeypatch.setattr(runner.metrics, "select_representatives", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.workflow, "evaluation_records", lambda *_: ())
    monkeypatch.setattr(runner.workflow, "join_policy_targets", lambda *_: ())
    monkeypatch.setattr(runner.metrics, "summarize", lambda *_: {"invented": True})
    result = runner._project(tmp_path, identity, [(None, {"counts": {"coordinates": 5158}})], "c" * 64)
    assert result["fit_attempt_count"] == 2
    assert result["fit_count"] == result["completed_native_fit_count"] == result["fitted_artifact_count"] == 0
    assert all((tmp_path / f"{arm}-fit-attempt.json").exists() for arm in runner.ARMS)
    assert all(not (tmp_path / f"{arm}-fit-start.json").exists() for arm in runner.ARMS)


def test_previous_fold_failure_cannot_be_skipped(runtime, monkeypatch):
    _, source, _ = runtime
    pin = qualify(source)
    monkeypatch.setattr(runner, "_actual_inputs", forbidden)
    with pytest.raises(ValueError, match="preceding fold audit"):
        runner.execute(actual(pin, fold=1), {}, source, 0)
