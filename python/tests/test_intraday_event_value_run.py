"""Invented EN release, publication, causal ordering and saved-join witnesses."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from research import intraday_event_value_run as runner


def args(mode, actual=False):
    return SimpleNamespace(mode=mode, kind=None, scope=None, fold=None, shard=None, attempt=None,
        summary_sha256="a" * 64 if actual else None, dataset_binding_sha256="b" * 64 if actual else None)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    run = runner.run
    (tmp_path / "canonical").mkdir()
    (tmp_path / "canonical/experiments.jsonl").touch()
    (tmp_path / "output").mkdir()
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    monkeypatch.setattr(run, "_locks", nullcontext)
    monkeypatch.setattr(run, "budget", lambda: None)
    monkeypatch.setattr(run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(runner.original.bridge, "restrict_local_network", lambda: None)
    monkeypatch.setattr(runner.wire, "prohibit_ef_tasks", lambda: None)
    usage = {"worker_cpu_seconds": 1, "helper_cpu_seconds": 0, "wall_seconds": 1}
    monkeypatch.setattr(run, "usage", lambda: usage)
    reservations = []
    monkeypatch.setattr(run, "audit_budget", reservations.append)
    calendar, cases = runner.fixtures.invented_cases(calendar_sessions=3, names=2)
    cases = tuple(cases)
    monkeypatch.setattr(runner.fixtures, "full_cases", lambda: (calendar, iter(cases)))
    monkeypatch.setattr(runner.fixtures, "FULL_EVENTS", len(cases))
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "2" * 64}}
    return tmp_path, source, reservations


def qualify(source):
    runner.execute(args("entry-value-qualification"), {}, source, 0)
    runner.execute(args("entry-value-qualification-audit"), {}, source, 0)


def forbid_producer(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("An independent EN audit called a producer calculation")
    for name in ("causal_query", "select_event", "measure_query", "measure_episode", "summarize"):
        monkeypatch.setattr(runner.core, name, forbidden)


def test_same_release_qualification_audits_without_producer_calculations(runtime, monkeypatch):
    root, source, reservations = runtime
    runner.execute(args("entry-value-qualification"), {}, source, 0)
    forbid_producer(monkeypatch)
    runner.execute(args("entry-value-qualification-audit"), {}, source, 0)
    assert len(runner._qualification(source, "d" * 64)) == 64
    report = runner.run._doc(root / "output" / runner.QUALIFICATION / "report.json")
    assert report["totals"]["original_episodes"] == 6
    assert report["identity"]["new_fit_count"] == report["identity"]["new_policy_count"] == 0
    assert len(reservations) == 1
    assert runner.run._doc(root / "output" / runner.QUALIFICATION / "audit.json")["independently_verified"] is True


@pytest.mark.parametrize("changed", ["source", "runtime", "episode_stream", "audit_receipt"])
def test_qualification_is_same_release_and_authenticates_its_episode_geometry(runtime, changed):
    root, source, _ = runtime
    qualify(source)
    current, runtime_pin = deepcopy(source), "d" * 64
    if changed == "source":
        current["sources"][runner.PLAN] = "3" * 64
    elif changed == "runtime":
        runtime_pin = "e" * 64
    elif changed == "audit_receipt":
        path = root / "output" / runner.QUALIFICATION / "audit.json"
        receipt = runner.run._doc(path)
        receipt["independently_verified"] = False
        path.write_bytes(runner.wire.encode(receipt))
    else:
        with (root / "output" / runner.QUALIFICATION / "episode-errors.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
    with pytest.raises(ValueError):
        runner._qualification(current, runtime_pin)


def test_actual_cannot_read_inputs_without_audited_same_release_qualification(runtime, monkeypatch):
    root, source, _ = runtime
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Actual inputs were opened before qualification")
    monkeypatch.setattr(runner, "_actual_inputs", forbidden)
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.execute(args("entry-value", True), {}, source, 0)
    assert not (root / "output" / runner.UNIT).exists()


@pytest.mark.parametrize("field", ["kind", "scope", "fold", "shard", "attempt"])
def test_fixed_saved_model_scope_rejects_new_coordinates(runtime, field):
    root, source, _ = runtime
    invalid = args("entry-value-qualification")
    setattr(invalid, field, 0 if field in ("fold", "shard", "attempt") else "unregistered")
    with pytest.raises(ValueError, match="fixed saved-model"):
        runner.execute(invalid, {}, source, 0)
    assert not list((root / "output").iterdir())


@pytest.mark.parametrize("actual,summary,binding", [(True, None, None), (True, "a" * 64, None),
                                                  (True, "bad", "b" * 64), (False, "a" * 64, None)])
def test_original_pins_are_mandatory_only_for_actual_modes(runtime, actual, summary, binding):
    root, source, _ = runtime
    invalid = args("entry-value" if actual else "entry-value-qualification")
    invalid.summary_sha256, invalid.dataset_binding_sha256 = summary, binding
    with pytest.raises(ValueError, match="pins"):
        runner.execute(invalid, {}, source, 0)
    assert not list((root / "output").iterdir())


@pytest.mark.parametrize("filename", ["causal-roster.json", "causal.jsonl", "choices.jsonl", "query-errors.jsonl"])
def test_independent_audit_rejects_tampered_sealed_streams(runtime, filename):
    root, source, _ = runtime
    runner.execute(args("entry-value-qualification"), {}, source, 0)
    slot = root / "output" / runner.QUALIFICATION
    with (slot / filename).open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(ValueError):
        runner.execute(args("entry-value-qualification-audit"), {}, source, 0)
    assert not (slot / "audit.json").exists()
    assert runner.run._doc(slot / "entry-value-qualification-audit-failure.json")["error_type"] == "ValueError"


def test_independent_audit_rejects_economic_tampering_even_with_resealed_report_hash(runtime):
    root, source, _ = runtime
    runner.execute(args("entry-value-qualification"), {}, source, 0)
    slot = root / "output" / runner.QUALIFICATION
    report = runner.run._doc(slot / "report.json")
    arm = "|".join(runner.fixtures.ARMS[0])
    report["totals"]["arms"][arm]["net_cash"] = "999999"
    raw = runner.wire.encode(report)
    (slot / "report.json").write_bytes(raw)
    completion = runner.run._doc(slot / "completion.json")
    completion["result"].update(report_sha256=runner.wire.digest(raw), report_bytes=len(raw))
    (slot / "completion.json").write_bytes(runner.wire.encode(completion))
    with pytest.raises(ValueError, match="net_cash"):
        runner.execute(args("entry-value-qualification-audit"), {}, source, 0)
    assert not (slot / "audit.json").exists()


def saved_sources(root, monkeypatch, *, mutation=None):
    """Install only in-memory invented archived records behind native readers."""
    calendar, _ = runner.fixtures.invented_cases(calendar_sessions=160, names=0)
    cases = [runner.fixtures.case(fold, day=calendar[fold * 40], symbol=f"INVENTED{fold}", fold=fold)
             for fold in range(4)]
    roster = {case["event"]["event_id"]: case["event"] for case in cases}
    output = root / "output" / runner.UNIT
    seen = []

    def started(stage):
        assert (output / "start.json").exists()
        if (output / "completion.json").exists():
            assert (output / "audit-start.json").exists()
        journal = [json.loads(line) for line in (root / "canonical/experiments.jsonl").read_text().splitlines()]
        assert any(row["status"] == "started" for row in journal)
        seen.append(stage)

    def sealed(stage):
        started(stage)
        causal = [json.loads(line) for line in (output / "causal.jsonl").read_text().splitlines()]
        choices = [json.loads(line) for line in (output / "choices.jsonl").read_text().splitlines()]
        assert {row["event_id"] for row in choices} == roster.keys()
        assert len(causal) == sum(len(case["wrappers"]) for case in cases)
        assert runner.run._doc(output / "causal-roster.json")["events"] == roster

    def predecessor(*_args):
        started("predecessor")
        return ({"report": {"original_episodes_by_family": {runner.FAMILY: len(roster)}}},
                [(root, {"session_indices": [97, 257]})], calendar)

    monkeypatch.setattr(runner.original, "_predecessor", predecessor)
    monkeypatch.setattr(runner.original, "causal_roster", lambda *_args: roster)
    publications = [(fold, root, {"identity": {"fold": fold}, "files": {
        name: {"invented_source": name, "fold": fold} for name in ("predictions.jsonl", "ledger.jsonl", "comparison.json")}})
        for fold in range(4)]
    monkeypatch.setattr(runner, "_fold_publications", lambda _summary: publications)
    saved_reader = runner.artifacts._read_rows

    def rows(path, pin):
        if "invented_source" not in pin:
            return saved_reader(path, pin)
        case = cases[pin["fold"]]
        if pin["invented_source"] == "predictions.jsonl":
            started("predictions")
            result = list(case["predictions"].values())
            if mutation == "duplicate_prediction" and pin["fold"] == 0:
                result += result[:1]
        else:
            sealed("ledgers")
            result = list(case["ledgers"].values())
            if mutation == "missing_ledger" and pin["fold"] == 0:
                result.pop()
            elif mutation == "duplicate_ledger" and pin["fold"] == 0:
                result += result[:1]
        return iter(result)

    monkeypatch.setattr(runner.artifacts, "_read_rows", rows)

    def document(_path, pin):
        sealed("comparison")
        fold, case = pin["fold"], cases[pin["fold"]]
        policies = [{"control": control, "sha256": next(row["policy_sha256"] for row in case["ledgers"].values()
                     if row["control"] == control), "trace": deepcopy(case["trace"]) if control == "model" else []}
                    for control in ("model", "confirmation")]
        if mutation == "trace" and fold == 0:
            policies[0]["trace"][0][5] = "different_reason"
        # Actual runtime statuses are fold-indexed, unlike invented event-indexed cases.
        return {"kind": "tcn", "scope": "common_chart_peer", "fold": fold,
                "evaluation_sessions": calendar[fold * 40:(fold + 1) * 40], "model_status": "completed",
                "head_statuses": case["head_statuses"], "fit_counts": {"invented": 1},
                "training_diagnostics": {"invented_initial_loss": 0.25, "invented_final_loss": 0.125}, "policies": policies}

    monkeypatch.setattr(runner.artifacts, "_read_document", document)

    def originals(_path, _manifest, filename, _kind):
        if filename == "queries.jsonl":
            started("causal_queries")
            result = [row for case in cases for row in case["wrappers"]]
        elif filename == "labels.jsonl":
            sealed("labels")
            result = [row for case in cases for row in case["labels"].values()]
            if mutation == "missing_target":
                result.pop(0)
            elif mutation == "duplicate_target":
                result += result[:1]
        else:
            started(filename)
            result = []
            for case in cases:
                event = case["event"]
                common = {"session": event["session"], "symbol": event["symbol"]}
                if filename == "coordinate-support.jsonl":
                    result.append({**common, "continuous_length": 150, "continuous_price_observed": 150,
                                   "minute_states": [{"offset": i, "price": "observed"} for i in range(150)]})
                else:
                    result.append({**common, "family": runner.FAMILY, "scheduled_minutes": 150,
                                   "counts": {"active": 150}, "runs": [{"start": 0, "end": 150,
                                       "status": "active", "event_id": event["event_id"]}]})
        return iter(result)

    monkeypatch.setattr(runner.inputs, "_rows", originals)
    return output, seen


def test_actual_path_starts_before_reads_seals_choices_before_targets_and_audits_independently(runtime, monkeypatch):
    root, source, reservations = runtime
    qualify(source)
    output, stages = saved_sources(root, monkeypatch)
    runner.execute(args("entry-value", True), {}, source, 0)
    producer_stages = list(stages)
    assert producer_stages.index("causal_queries") < producer_stages.index("labels") < producer_stages.index("ledgers")
    assert runner.run._doc(output / "completion.json")["journal"]["end"] > 0
    forbid_producer(monkeypatch)
    runner.execute(args("entry-value-audit", True), {}, source, 0)
    audit = runner.run._doc(output / "audit.json")
    assert audit["independently_verified"] is True
    report = runner.run._doc(output / "report.json")
    assert report["totals"]["original_episodes"] == 4
    assert len(report["fit_diagnostics"]) == 4
    assert report["totals"]["arms"]["|".join(runner.fixtures.ARMS[0])]["unavailable"] == 1
    assert len(reservations) == 2
    with pytest.raises((FileExistsError, ValueError)):
        runner.execute(args("entry-value", True), {}, source, 0)


@pytest.mark.parametrize("mutation", ["duplicate_prediction", "missing_target", "duplicate_target",
                                      "missing_ledger", "duplicate_ledger", "trace"])
def test_saved_native_joins_fail_without_publishing_a_false_completion(runtime, monkeypatch, mutation):
    root, source, _ = runtime
    qualify(source)
    output, _ = saved_sources(root, monkeypatch, mutation=mutation)
    with pytest.raises(ValueError):
        runner.execute(args("entry-value", True), {}, source, 0)
    assert (output / "start.json").is_file()
    assert (output / "entry-value-failure.json").is_file()
    assert not (output / "completion.json").exists()
    assert not (output / "report.json").exists()
    journal = [json.loads(line) for line in (root / "canonical/experiments.jsonl").read_text().splitlines()]
    assert [row["status"] for row in journal] == ["started", "failed"]
