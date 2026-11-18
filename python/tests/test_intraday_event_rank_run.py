"""Invented EO admission, causal sealing and independent publication witnesses."""

from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from research import intraday_event_rank_run as runner


def args(mode, *, report=None, audit=None):
    return SimpleNamespace(mode=mode, kind=None, scope=None, fold=None, shard=None,
        attempt=None, summary_sha256=None, dataset_binding_sha256=None,
        entry_value_report_sha256=report, entry_value_audit_sha256=audit)


def forbidden(*_args, **_kwargs):
    raise AssertionError("EO opened an original adapter or called a producer during audit")


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
    monkeypatch.setattr(runner.prior.original.bridge, "restrict_local_network", lambda: None)
    monkeypatch.setattr(runner.wire, "prohibit_ef_tasks", lambda: None)
    monkeypatch.setattr(run, "usage", lambda: {
        "worker_cpu_seconds": 1, "helper_cpu_seconds": 0, "wall_seconds": 1})
    reservations = []
    monkeypatch.setattr(run, "audit_budget", reservations.append)
    fixture = runner.fixtures.full_fixture(names=1)
    monkeypatch.setattr(runner.fixtures, "full_fixture", lambda: fixture)
    monkeypatch.setattr(runner.fixtures, "FULL_ROWS", 160 * 4)
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "2" * 64,
        runner.prior.PLAN: "3" * 64, "python/research/intraday_event_value_run.py": "4" * 64,
        runner.DISPATCHER: "5" * 64}}
    # Even a mistaken adapter call cannot touch any real research archive.
    monkeypatch.setattr(runner.prior, "_actual_inputs", forbidden)
    monkeypatch.setattr(runner.prior.original, "_predecessor", forbidden)
    monkeypatch.setattr(runner.prior.inputs, "_rows", forbidden)
    bounded = runner.prior.original.bridge.bounded_read

    def temporary_read(path, maximum):
        assert path.resolve().is_relative_to(tmp_path.resolve())
        return bounded(path, maximum)

    monkeypatch.setattr(runner.prior.original.bridge, "bounded_read", temporary_read)
    return tmp_path, source, reservations, fixture


def qualify(source):
    runner.execute(args("entry-rank-qualification"), {}, source, 0)
    runner.execute(args("entry-rank-qualification-audit"), {}, source, 0)


def forbid_producer(monkeypatch):
    for name in ("select_representatives", "join_targets", "summarize"):
        monkeypatch.setattr(runner.core, name, forbidden)


def rewrite(path, value):
    path.write_bytes(runner.wire.encode(value))


def reseal_report(slot, report):
    """Deliberate attacker can rewrite transport hashes, not scientific truth."""
    raw = runner.wire.encode(report)
    (slot / "report.json").write_bytes(raw)
    completion = runner.run._doc(slot / "completion.json")
    completion["result"].update(report_sha256=runner.wire.digest(raw), report_bytes=len(raw))
    rewrite(slot / "completion.json", completion)


def publish_predecessor(runtime, monkeypatch):
    """Build an EN publication entirely from invented data in pytest's directory."""
    root, source, _, (roster, causal, outcomes) = runtime
    old = {"merge": runner.PREDECESSOR, "reviewed": "6" * 40, "tree": "7" * 40,
        "sources": {name: pin for name, pin in source["sources"].items() if name != runner.PLAN}}
    old["sources"][runner.DISPATCHER] = "8" * 64

    def git(*command):
        if command == ("show", "-s", "--format=%H%n%T%n%P", runner.PREDECESSOR):
            return (old["merge"] + "\n" + old["tree"] + "\n" + "9" * 40 + " " + old["reviewed"] + "\n").encode()
        if command == ("rev-parse", old["reviewed"] + "^{tree}"):
            return (old["tree"] + "\n").encode()
        assert command == ("merge-base", "--is-ancestor", runner.PREDECESSOR, source["merge"])
        return b""

    monkeypatch.setattr(runner.run, "_git", git)
    identity = runner.prior._identity(old, "d" * 64, invented=False,
        args=SimpleNamespace(summary_sha256="a" * 64, dataset_binding_sha256="b" * 64),
        qualification="c" * 64)
    slot = root / "output" / runner.prior.UNIT
    slot.mkdir()
    roster_pin = runner.artifacts._document(slot, "causal-roster.json", roster)
    causal_pin = runner.artifacts._stream(slot, "causal.jsonl", causal)
    query_pin = runner.artifacts._stream(slot, "query-errors.jsonl", outcomes())
    report = {"identity": identity, "calendar": roster["calendar"], "source_support": roster["source_support"],
        "causal_roster": roster_pin, "causal": causal_pin, "queries": query_pin,
        "resources": runner.run.usage()}
    rewrite(slot / "start.json", identity)
    rewrite(slot / "report.json", report)
    raw = runner.wire.encode(report)
    result = {"report_sha256": runner.wire.digest(raw), "report_bytes": len(raw), "resources": report["resources"]}
    journal = root / "canonical/experiments.jsonl"
    start = journal.stat().st_size
    runner.experiment_log.run_discovery_trial(study_id=runner.prior.UNIT,
        specification_sha256=identity["registration_sha256"], parameters=identity,
        window=runner.run.WINDOW, evaluate=lambda: result, runs_path=journal,
        code_version_provider=lambda: identity["merge"])
    span = runner.run._proof(identity, result, start, journal.stat().st_size, "completed", unit=runner.prior.UNIT)
    rewrite(slot / "completion.json", {"identity": identity, "result": result, "journal": span})
    audit = {"identity": identity, "report_sha256": result["report_sha256"],
        "independently_verified": True, "resources": runner.run.usage()}
    rewrite(slot / "audit-start.json", identity)
    rewrite(slot / "audit.json", audit)
    actual = args("entry-rank", report=result["report_sha256"], audit=runner.wire.digest(runner.wire.encode(audit)))
    return slot, actual, old


def test_same_release_qualification_audit_uses_no_producer_calculation(runtime, monkeypatch):
    root, source, reservations, _ = runtime
    runner.execute(args("entry-rank-qualification"), {}, source, 0)
    forbid_producer(monkeypatch)
    runner.execute(args("entry-rank-qualification-audit"), {}, source, 0)
    assert len(runner._qualification(source, "d" * 64)) == 64
    slot = root / "output" / runner.QUALIFICATION
    report = runner.run._doc(slot / "report.json")
    assert report["representatives"]["rows"] == report["joined"]["rows"] == 640
    assert len(report["totals"]["cells"]) == 16
    assert report["identity"]["new_fit_count"] == report["identity"]["new_policy_count"] == 0
    assert runner.run._doc(slot / "audit.json")["independently_verified"] is True
    assert len(reservations) == 1


@pytest.mark.parametrize("changed", ["source", "runtime", "representatives", "causal_roster", "audit"])
def test_qualification_rejects_changed_release_runtime_artifact_or_receipt(runtime, changed):
    root, source, _, _ = runtime
    qualify(source)
    current, runtime_pin = deepcopy(source), "d" * 64
    slot = root / "output" / runner.QUALIFICATION
    if changed == "source":
        current["sources"][runner.PLAN] = "e" * 64
    elif changed == "runtime":
        runtime_pin = "e" * 64
    elif changed == "audit":
        receipt = runner.run._doc(slot / "audit.json")
        receipt["independently_verified"] = False
        rewrite(slot / "audit.json", receipt)
    else:
        filename = "causal-roster.json" if changed == "causal_roster" else "representatives.jsonl"
        with (slot / filename).open("ab") as stream:
            stream.write(b"{}\n")
    with pytest.raises(ValueError):
        runner._qualification(current, runtime_pin)


def test_actual_cannot_open_saved_inputs_without_qualification(runtime, monkeypatch):
    root, source, _, _ = runtime
    monkeypatch.setattr(runner, "_actual_inputs", forbidden)
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.execute(args("entry-rank", report="a" * 64, audit="b" * 64), {}, source, 0)
    assert not (root / "output" / runner.UNIT).exists()
    assert not (root / "canonical/experiments.jsonl").read_bytes()


@pytest.mark.parametrize("field", ["kind", "scope", "fold", "shard", "attempt", "summary_sha256", "dataset_binding_sha256"])
def test_fixed_saved_record_scope_rejects_new_coordinates_and_original_input_pins(runtime, field):
    root, source, _, _ = runtime
    invalid = args("entry-rank-qualification")
    setattr(invalid, field, 0 if field in ("fold", "shard", "attempt") else "unregistered")
    with pytest.raises(ValueError, match="fixed saved-record"):
        runner.execute(invalid, {}, source, 0)
    assert not list((root / "output").iterdir())


@pytest.mark.parametrize("mode,report,audit", [
    ("entry-rank", None, None), ("entry-rank-audit", "a" * 64, None),
    ("entry-rank", "bad", "b" * 64), ("entry-rank-qualification", "a" * 64, "b" * 64)])
def test_predecessor_pins_are_mandatory_only_for_actual_modes(runtime, mode, report, audit):
    root, source, _, _ = runtime
    with pytest.raises(ValueError, match="pins"):
        runner.execute(args(mode, report=report, audit=audit), {}, source, 0)
    assert not list((root / "output").iterdir())


@pytest.mark.parametrize("mutation", ["report_pin", "audit_pin", "runtime", "dependency", "audit_start",
                                      "journal", "resources", "causal_artifact"])
def test_saved_en_authentication_rejects_invalid_predecessor_before_outcomes(runtime, monkeypatch, mutation):
    root, source, _, _ = runtime
    slot, actual, _ = publish_predecessor(runtime, monkeypatch)
    runtime_pin = "d" * 64
    if mutation == "report_pin":
        actual.entry_value_report_sha256 = "f" * 64
    elif mutation == "audit_pin":
        actual.entry_value_audit_sha256 = "f" * 64
    elif mutation == "runtime":
        runtime_pin = "e" * 64
    elif mutation == "dependency":
        source["sources"]["python/research/intraday_event_value_run.py"] = "e" * 64
    elif mutation == "audit_start":
        rewrite(slot / "audit-start.json", {"invented": True})
    elif mutation == "journal":
        journal = root / "canonical/experiments.jsonl"
        records = [json.loads(line) for line in journal.read_text().splitlines()]
        records[-1]["status"] = "failed"
        journal.write_bytes(b"".join(runner.wire.encode(row) for row in records))
    elif mutation == "resources":
        audit = runner.run._doc(slot / "audit.json")
        audit["resources"]["worker_cpu_seconds"] = 1500
        rewrite(slot / "audit.json", audit)
        actual.entry_value_audit_sha256 = runner.wire.digest(runner.wire.encode(audit))
    else:
        with (slot / "causal.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
    read_rows = runner.artifacts._read_rows

    def before_outcomes(path, pin):
        assert pin["filename"] != "query-errors.jsonl"
        return read_rows(path, pin)

    monkeypatch.setattr(runner.artifacts, "_read_rows", before_outcomes)
    with pytest.raises(ValueError):
        runner._actual_inputs(actual, source, runtime_pin)


@pytest.mark.parametrize("mutation", ["single_parent", "reviewed_tree"])
def test_predecessor_must_be_its_reviewed_normal_merge(runtime, monkeypatch, mutation):
    _, source, _, _ = runtime
    _, actual, _ = publish_predecessor(runtime, monkeypatch)
    git = runner.run._git

    def altered_git(*command):
        result = git(*command)
        if mutation == "single_parent" and command[0] == "show":
            commit, tree, parents = result.decode().splitlines()
            return (commit + "\n" + tree + "\n" + parents.split()[0] + "\n").encode()
        if mutation == "reviewed_tree" and command[0] == "rev-parse":
            return ("f" * 40 + "\n").encode()
        return result

    monkeypatch.setattr(runner.run, "_git", altered_git)
    with pytest.raises(ValueError, match="reviewed normal merge"):
        runner._actual_inputs(actual, source, "d" * 64)


def test_actual_start_and_canonical_start_precede_reads_and_representatives_precede_outcomes(runtime, monkeypatch):
    root, source, reservations, (roster, _, _) = runtime
    qualify(source)
    _, actual, _ = publish_predecessor(runtime, monkeypatch)
    output = root / "output" / runner.UNIT
    bounded = runner.prior.original.bridge.bounded_read
    read_rows = runner.artifacts._read_rows
    seen = []

    def started():
        assert (output / "start.json").is_file()
        journal = [json.loads(line) for line in (root / "canonical/experiments.jsonl").read_text().splitlines()]
        assert any(row["study_id"] == runner.UNIT and row["status"] == "started" for row in journal)
        if (output / "completion.json").exists():
            assert (output / "audit-start.json").exists()

    def observed_read(path, maximum):
        if path.parent.name == runner.prior.UNIT:
            started()
            seen.append(path.name)
        return bounded(path, maximum)

    def observed_rows(path, pin):
        if path.name == runner.prior.UNIT:
            started()
            if pin["filename"] == "query-errors.jsonl":
                selections = [json.loads(line) for line in (output / "representatives.jsonl").read_text().splitlines()]
                assert len(selections) == len(roster["events"]) * 4
                assert {row["event_id"] for row in selections} == roster["events"].keys()
                assert runner.run._doc(output / "causal-roster.json") == roster
            seen.append(pin["filename"])
        return read_rows(path, pin)

    monkeypatch.setattr(runner.prior.original.bridge, "bounded_read", observed_read)
    monkeypatch.setattr(runner.artifacts, "_read_rows", observed_rows)
    runner.execute(actual, {}, source, 0)
    assert seen.index("causal.jsonl") < seen.index("query-errors.jsonl")
    assert runner.run._doc(output / "completion.json")["journal"]["end"] > 0
    forbid_producer(monkeypatch)
    actual.mode = "entry-rank-audit"
    runner.execute(actual, {}, source, 0)
    assert runner.run._doc(output / "audit.json")["independently_verified"] is True
    assert len(reservations) == 2
    actual.mode = "entry-rank"
    with pytest.raises((FileExistsError, ValueError)):
        runner.execute(actual, {}, source, 0)


@pytest.mark.parametrize("mutation", ["selection", "joined_target", "rank_summary"])
def test_independent_audit_rejects_logical_tampering_even_after_hashes_are_resealed(runtime, monkeypatch, mutation):
    root, source, _, _ = runtime
    runner.execute(args("entry-rank-qualification"), {}, source, 0)
    slot = root / "output" / runner.QUALIFICATION
    report = runner.run._doc(slot / "report.json")
    if mutation == "rank_summary":
        report["totals"]["cells"][0]["heads"]["net_immediate"]["cross_cluster"]["auc"]["value"] = 0.999
    else:
        field = "representatives" if mutation == "selection" else "joined"
        rows = list(runner.artifacts._read_rows(slot, report[field]))
        if mutation == "selection":
            rows[0]["reason"] = "outcome_selected"
        else:
            rows[0]["heads"]["net_immediate"]["target"] = "999"
        raw = b"".join(runner.wire.encode(row) for row in rows)
        (slot / report[field]["filename"]).write_bytes(raw)
        report[field].update(sha256=runner.wire.digest(raw), bytes=len(raw))
    reseal_report(slot, report)
    forbid_producer(monkeypatch)
    with pytest.raises(ValueError, match="independent audit"):
        runner.execute(args("entry-rank-qualification-audit"), {}, source, 0)
    assert not (slot / "audit.json").exists()
    assert runner.run._doc(slot / "entry-rank-qualification-audit-failure.json")["error_type"] == "ValueError"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "projection"])
def test_invalid_measured_stream_keeps_failed_actual_attempt_and_no_completion(runtime, monkeypatch, mutation):
    root, source, _, (roster, causal, outcomes) = runtime
    qualify(source)
    measured = outcomes()
    if mutation == "missing":
        measured.pop()
    elif mutation == "duplicate":
        measured.append(deepcopy(measured[0]))
    else:
        measured[0]["label_query"]["reference_close"] = "200"
    monkeypatch.setattr(runner, "_actual_inputs", lambda *_args:
        (roster, causal, lambda: measured, {"invented": True}))
    with pytest.raises(ValueError, match="original measured"):
        runner.execute(args("entry-rank", report="a" * 64, audit="b" * 64), {}, source, 0)
    slot = root / "output" / runner.UNIT
    assert (slot / "start.json").is_file()
    assert (slot / "representatives.jsonl").is_file()
    assert (slot / "entry-rank-failure.json").is_file()
    assert not (slot / "report.json").exists()
    assert not (slot / "completion.json").exists()
    journal = [json.loads(line) for line in (root / "canonical/experiments.jsonl").read_text().splitlines()]
    assert [row["status"] for row in journal] == ["started", "failed"]
