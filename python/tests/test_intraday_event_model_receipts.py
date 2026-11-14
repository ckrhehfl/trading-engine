"""Invented publication and independent-audit lifecycle, with no model fit."""

import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as dataset
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_receipts as receipts
from research.intraday_event_evaluation import CONTROLS
from research.intraday_event_execution import ENTRY_POLICIES, MANAGEMENT_POLICIES


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    for directory in ("output", "canonical"):
        (tmp_path / directory).mkdir()
    monkeypatch.setattr(dataset, "NATIVE", tmp_path)
    resources = {"worker_cpu_seconds": 20, "helper_cpu_seconds": 1, "wall_seconds": 25}
    monkeypatch.setattr(dataset, "usage", lambda: resources.copy())
    budgets = []
    monkeypatch.setattr(dataset, "audit_budget", lambda measured: budgets.append(measured))
    identity = {"kind": "empirical", "scope": "full_chart", "fold": 0, "invented": True,
                "merge": "1" * 40, "registration_sha256": "2" * 64, "runtime_sha256": "3" * 64,
                "inputs_sha256": "4" * 64}
    return receipts.slot_for(identity), identity, resources, budgets


def _runtime(identity):
    return {"verified": True, "kind": identity["kind"], "runtime_sha256": identity["runtime_sha256"]}


def _publish_invented(slot, identity, scratch):
    assert scratch == slot / "produce-scratch" and scratch.is_dir()
    _, _, journal = receipts._paths(slot, identity)
    first = json.loads(journal.read_bytes().splitlines()[-1])
    assert first["status"] == "started" and first["parameters"] == identity
    assert first["promotion_allowed"] is False and first["promotion_trial_increment"] == 0
    files = {
        "model.json": artifacts._document(slot, "model.json", {"artifact": "invented-model"}),
        "predictions.jsonl": artifacts._stream(slot, "predictions.jsonl", [{"prediction": "invented"}]),
        "comparison.json": artifacts._document(slot, "comparison.json", {"summary": "invented"}),
        "ledger.jsonl": artifacts._stream(slot, "ledger.jsonl", [
            {"control": control, "entry": entry, "management": management}
            for control in CONTROLS for entry in ENTRY_POLICIES for management in MANAGEMENT_POLICIES]),
    }
    artifacts._document(slot, artifacts.FIT, {"schema": "EL-fold-fit-v1", "identity": identity,
                         "files": {name: files[name] for name in ("model.json", "predictions.jsonl")}})
    manifest = artifacts._document(slot, artifacts.MANIFEST, {
        "schema": "EL-fold-artifacts-v1", "identity": identity, "files": files})
    return {"manifest": manifest, "runtime": _runtime(identity)}


def _audit_invented(slot, identity, scratch, manifest_pin):
    assert scratch == slot / "audit-scratch" and scratch.is_dir()
    started = dataset._doc(slot / "audit-start.json")
    assert started["identity"] == identity and started["manifest_sha256"] == manifest_pin["sha256"]
    arms = len(ENTRY_POLICIES) * len(MANAGEMENT_POLICIES)
    return {"runtime": _runtime(identity), "audit": {
        "identity": identity, "manifest_sha256": manifest_pin["sha256"], "ledger_rows": arms * len(CONTROLS),
        "forecasts": {"verified": True, "predictions": 1, "native_optimizer_reimplemented": False},
        "cash_checks": [{"episodes": 1, "ledgers": arms, "outcome_counts": {"no_entry": arms}}
                        for _ in CONTROLS]}}


def test_durable_publication_and_independent_replay_receipt(lifecycle):
    slot, identity, resources, budgets = lifecycle
    report = receipts.produce(slot, identity, lambda scratch: _publish_invented(slot, identity, scratch))
    with pytest.raises(FileNotFoundError):
        receipts.audited(slot, identity)
    receipt = receipts.audit(slot, identity, lambda scratch, pin: _audit_invented(slot, identity, scratch, pin))
    assert budgets == [resources]
    assert receipt["manifest_sha256"] == report["outcome"]["manifest"]["sha256"]
    assert receipt["completion_sha256"] == wire.digest((slot / "completion.json").read_bytes())
    assert receipt["audit_report_sha256"] == wire.digest((slot / "audit-report.json").read_bytes())
    assert receipts.audited(slot, identity) == (report, wire.digest(wire.encode(receipt)))
    with pytest.raises(FileExistsError):
        receipts.produce(slot, identity, lambda _: pytest.fail("duplicate model fit"))
    with pytest.raises(FileExistsError):
        receipts.audit(slot, identity, lambda *_: pytest.fail("duplicate model audit"))


def test_incomplete_fit_is_durable_and_no_retry_is_admitted(lifecycle):
    slot, identity, _, _ = lifecycle

    def fail(scratch):
        assert scratch.is_dir()
        assert json.loads((slot / "experiments.jsonl").read_bytes())["status"] == "started"
        (slot / artifacts.FIT).write_bytes(b"invented retained fit\n")
        raise TimeoutError("invented comparison timeout after fit")

    with pytest.raises(TimeoutError, match="after fit"):
        receipts.produce(slot, identity, fail)
    journal = [json.loads(row) for row in (slot / "experiments.jsonl").read_bytes().splitlines()]
    assert [row["status"] for row in journal] == ["started", "failed"]
    assert journal[-1]["error_type"] == "TimeoutError" and journal[0]["run_id"] == journal[1]["run_id"]
    marker = slot.parent.parent / "canonical" / f"{slot.name}-unresolved.json"
    assert dataset._doc(marker) == identity and (slot / artifacts.FIT).is_file()
    with pytest.raises(ValueError, match="attempt unresolved"):
        receipts.produce(slot, identity, lambda _: pytest.fail("failed fit silently retried"))


def test_tampered_artifacts_leave_an_audit_failure_before_compute(lifecycle):
    slot, identity, _, _ = lifecycle
    receipts.produce(slot, identity, lambda scratch: _publish_invented(slot, identity, scratch))
    ledger = slot / "ledger.jsonl"
    original = ledger.read_bytes()
    ledger.write_bytes(original.replace(b"onset", b"other"))
    assert ledger.read_bytes() != original
    with pytest.raises(ValueError, match="saved artifact bytes differ"):
        receipts.audit(slot, identity, lambda *_: pytest.fail("altered inputs reached audit"))
    assert (slot / "audit-start.json").is_file()
    assert (slot / "audit-failure.json").is_file()


def test_tampered_artifacts_rejected_on_later_read(lifecycle):
    slot, identity, _, _ = lifecycle
    receipts.produce(slot, identity, lambda scratch: _publish_invented(slot, identity, scratch))
    receipts.audit(slot, identity, lambda scratch, pin: _audit_invented(slot, identity, scratch, pin))
    ledger = slot / "ledger.jsonl"
    original = ledger.read_bytes()
    ledger.write_bytes(original + b"{}\n")
    with pytest.raises(ValueError, match="saved artifact bytes differ"):
        receipts.audited(slot, identity)


def test_invalid_audit_cannot_publish_or_retry_after_start(lifecycle):
    slot, identity, _, _ = lifecycle
    receipts.produce(slot, identity, lambda scratch: _publish_invented(slot, identity, scratch))

    def omit_arm(scratch, pin):
        result = _audit_invented(slot, identity, scratch, pin)
        checks = result["audit"]["cash_checks"]
        checks[0]["ledgers"] -= 1
        checks[1]["ledgers"] += 1
        return result

    with pytest.raises(ValueError, match="verification coverage differs"):
        receipts.audit(slot, identity, omit_arm)
    assert dataset._doc(slot / "audit-failure.json")["error_type"] == "ValueError"
    assert not (slot / "audit.json").exists()
    with pytest.raises(FileExistsError):
        receipts.audit(slot, identity, lambda *_: pytest.fail("failed audit silently retried"))


def test_only_fixed_unit_slot_and_attempt_are_admitted(lifecycle):
    slot, identity, _, _ = lifecycle
    with pytest.raises(ValueError, match="slot differs"):
        receipts.produce(slot.parent / "elsewhere", identity, lambda _: pytest.fail("incorrect slot"))
    with pytest.raises(ValueError, match="attempt must be fixed"):
        receipts.slot_for({**identity, "attempt": "qualification2"})
    assert not slot.exists()


def test_new_release_changes_only_invented_slot(lifecycle):
    slot, identity, _, _ = lifecycle
    updated = {**identity, "merge": "f" * 40}
    assert receipts.slot_for(updated) != slot
    assert receipts.slot_for({**updated, "invented": False}) == receipts.slot_for({**identity, "invented": False})


@pytest.fixture
def actual_lifecycle(lifecycle):
    _, qualification, _, _ = lifecycle
    actual = {**qualification, "invented": False, "attempt": "actual1", "resources": "fixed-local-envelope"}
    first = receipts.slot_for(actual)
    _, _, journal = receipts._paths(first, actual)
    journal.touch()
    retry = {**actual, "attempt": "actual2"}
    return first, actual, receipts.slot_for(retry), retry, journal


def _prefit_timeout(scratch, _mark_fit):
    (scratch / "tmp").mkdir(mode=0o700)
    raise TimeoutError("before fit")


def _instrumented_producer(slot, identity):
    def compute(scratch, mark_fit):
        mark_fit()
        assert dataset._doc(slot / "fit-start.json") == identity
        return _publish_invented(slot, identity, scratch)
    return compute


def test_prefit_timeout_allows_one_identical_attempt_and_binds_audit(actual_lifecycle):
    first, original, second, retry, journal = actual_lifecycle
    with pytest.raises(TimeoutError, match="before fit"):
        receipts.produce(first, original, _prefit_timeout, instrument_fit=True)
    timeout = dataset._doc(first / "timeout.json")
    assert timeout["identity"] == original and timeout["error_type"] == "TimeoutError"
    assert [json.loads(row)["status"] for row in journal.read_bytes().splitlines()] == ["started", "failed"]
    assert not (first / "fit-start.json").exists()
    assert receipts._paths(first, original)[1].is_file()
    report = receipts.produce(second, retry, _instrumented_producer(second, retry), instrument_fit=True)
    done = dataset._doc(second / "completion.json")
    assert done["result"]["predecessor_timeout_sha256"] == wire.digest(wire.encode(timeout))
    receipt = receipts.audit(second, retry, lambda scratch, pin: _audit_invented(second, retry, scratch, pin))
    assert receipts.audited_actual(original) == (second, retry, report, wire.digest(wire.encode(receipt)))
    with pytest.raises(FileExistsError):
        receipts.produce(second, retry, lambda _: pytest.fail("second slot was reused"))
    with pytest.raises(ValueError, match="attempt must be fixed"):
        receipts.slot_for({**retry, "attempt": "actual3"})


@pytest.mark.parametrize("field", ["merge", "registration_sha256", "runtime_sha256", "inputs_sha256", "resources"])
def test_retry_rejects_different_release_inputs_or_resources(actual_lifecycle, field):
    first, original, second, retry, _ = actual_lifecycle
    with pytest.raises(TimeoutError):
        receipts.produce(first, original, _prefit_timeout, instrument_fit=True)
    updated = {**retry, field: "f" * len(retry[field])}
    with pytest.raises(ValueError, match="timeout identity differs"):
        receipts.produce(second, updated, lambda _: pytest.fail("changed model retry"))
    assert not second.exists()


@pytest.mark.parametrize("after_mark", [False, True])
def test_possible_fit_or_partial_publication_disallows_retry(actual_lifecycle, after_mark):
    first, original, second, retry, _ = actual_lifecycle

    def fail(scratch, mark_fit):
        if after_mark:
            mark_fit()
        else:
            (scratch / "partial-fit.tmp").touch()
        raise TimeoutError("ambiguous")

    with pytest.raises(TimeoutError, match="ambiguous"):
        receipts.produce(first, original, fail, instrument_fit=True)
    assert not (first / "timeout.json").exists()
    with pytest.raises(ValueError, match="without ambiguous publication"):
        receipts.produce(second, retry, lambda _: pytest.fail("possibly fitted model retried"))
    assert not second.exists()


def test_altered_timeout_journal_is_rejected_before_retry(actual_lifecycle):
    first, original, second, retry, journal = actual_lifecycle
    with pytest.raises(TimeoutError):
        receipts.produce(first, original, _prefit_timeout, instrument_fit=True)
    raw = journal.read_bytes()
    altered = raw.replace(b'"failed"', b'"forged"')
    assert altered != raw
    journal.write_bytes(altered)
    with pytest.raises(ValueError, match="journal identity differs"):
        receipts.produce(second, retry, lambda _: pytest.fail("altered failed journal"))
    assert not second.exists()


def test_second_timeout_remains_unresolved_without_another_attempt(actual_lifecycle):
    first, original, second, retry, _ = actual_lifecycle
    for slot, identity in ((first, original), (second, retry)):
        with pytest.raises(TimeoutError, match="before fit"):
            receipts.produce(slot, identity, _prefit_timeout, instrument_fit=True)
    assert (first / "timeout.json").is_file()
    assert not (second / "timeout.json").exists()
    with pytest.raises(ValueError, match="attempt unresolved"):
        receipts.produce(second, retry, lambda _: pytest.fail("second attempt was repeated"))
    with pytest.raises(ValueError, match="publication unresolved"):
        receipts.audited_actual(original)


def test_published_first_attempt_cannot_be_recovered_as_second(actual_lifecycle):
    first, original, second, retry, _ = actual_lifecycle
    report = receipts.produce(first, original, _instrumented_producer(first, original), instrument_fit=True)
    with pytest.raises(ValueError, match="without ambiguous publication"):
        receipts.produce(second, retry, lambda _: pytest.fail("published fold retried"))
    with pytest.raises(ValueError, match="without ambiguous publication"):
        receipts.audit(second, retry, lambda *_: pytest.fail("unplanned second attempt audited"))
    assert not second.exists()
    receipt = receipts.audit(first, original, lambda scratch, pin: _audit_invented(first, original, scratch, pin))
    assert receipts.audited_actual(original) == (first, original, report, wire.digest(wire.encode(receipt)))


def test_published_first_attempt_cannot_retry_its_audit_after_budget_failure(actual_lifecycle, monkeypatch):
    first, original, second, retry, _ = actual_lifecycle
    receipts.produce(first, original, _instrumented_producer(first, original), instrument_fit=True)

    def exhausted(_):
        raise TimeoutError("audit budget exhausted")

    monkeypatch.setattr(dataset, "audit_budget", exhausted)
    with pytest.raises(TimeoutError, match="audit budget exhausted"):
        receipts.audit(first, original, lambda *_: pytest.fail("exhausted audit started"))
    assert dataset._doc(first / "audit-failure.json")["error_type"] == "TimeoutError"
    monkeypatch.setattr(dataset, "audit_budget", lambda _: pytest.fail("failed audit started again"))
    with pytest.raises(FileExistsError):
        receipts.audit(first, original, lambda *_: pytest.fail("failed audit retried"))
    with pytest.raises(ValueError, match="without ambiguous publication"):
        receipts.audit(second, retry, lambda *_: pytest.fail("unplanned second attempt audited"))
