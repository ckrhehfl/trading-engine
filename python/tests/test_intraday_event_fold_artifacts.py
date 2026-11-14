"""Invented fold persistence, replay, and tamper checks."""

import pytest

from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as workflow
from test_intraday_event_comparison import material  # noqa: F401 - shared invented raw-minute fixture
from test_intraday_event_model_workflow import CALENDAR


def test_saved_fold_has_independent_cash_audit_and_never_refits(tmp_path, material, monkeypatch):
    (slot, manifest), universe, reader = material
    shards = ((slot, manifest),)
    data = workflow.collect(CALENDAR, 0, "full_chart", inputs.query_rows(slot, manifest),
                            inputs.training_labels(slot, manifest, CALENDAR[97]))
    roster = inputs.decision_roster(shards, data)
    identity = {"kind": "empirical", "scope": data.scope, "fold": data.fold,
                "invented": True, "inputs_sha256": "f" * 64}

    def targets():
        return inputs.evaluation_targets(shards, data)

    native_scratch = []

    def activate(kind, scratch):
        assert kind == identity["kind"] and scratch not in native_scratch
        native_scratch.append(scratch)
        return scratch

    monkeypatch.setattr(artifacts.native, "activate", activate)
    monkeypatch.setattr(artifacts.native, "verify_after", lambda scratch: {"scratch": scratch.name})
    monkeypatch.setattr(inputs, "load_fold", lambda *_: data)
    with pytest.raises(ValueError, match="input binding"):
        artifacts.run_admitted_fold(tmp_path, identity, shards, universe, reader,
                                    dataset_binding="e" * 64, scratch=tmp_path / "produce")
    produced = artifacts.run_admitted_fold(tmp_path, identity, shards, universe, reader,
                                          dataset_binding=identity["inputs_sha256"], scratch=tmp_path / "produce")
    pin = produced["manifest"]
    assert produced["runtime"] == {"scratch": "produce"}

    def refuse_fit(*args):
        pytest.fail("An existing output or a verification pass must never refit the fold")

    monkeypatch.setattr(workflow, "fit_unit", refuse_fit)
    with pytest.raises(ValueError, match="unused"):
        artifacts.publish_fold(tmp_path, identity, data, roster, None, targets)
    audited = artifacts.run_admitted_fold(tmp_path, identity, shards, universe, reader,
                                         dataset_binding=identity["inputs_sha256"], scratch=tmp_path / "audit", audit_manifest=pin)
    assert audited["runtime"] == {"scratch": "audit"}
    audit = audited["audit"]
    saved = artifacts.read_comparison(tmp_path, pin, identity)
    assert audit["ledger_rows"] == len(saved["ledgers"]) == len(roster.episodes) * 18
    assert audit["forecasts"]["predictions"] == len(data.evaluation)
    assert sum(check["ledgers"] for check in audit["cash_checks"]) == audit["ledger_rows"]
    assert audit["manifest_sha256"] == pin["sha256"]

    with pytest.raises(ValueError, match="identity"):
        artifacts.read_comparison(tmp_path, pin, {**identity, "inputs_sha256": "e" * 64})
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(ledger.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="bytes differ"):
        artifacts.read_comparison(tmp_path, pin, identity)
