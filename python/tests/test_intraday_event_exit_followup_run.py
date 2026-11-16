"""Invented EM successor release, start and causal clock-seal checks."""

from contextlib import contextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from research import intraday_event_exit_followup_run as runner


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
    calendar, cases = runner.original.fixtures.invented_cases(calendar_sessions=3, names=2)
    cases = tuple(cases)
    monkeypatch.setattr(runner.original.fixtures, "full_cases", lambda: (calendar, iter(cases)))
    monkeypatch.setattr(runner.original.fixtures, "FULL_EVENTS", len(cases))
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "2" * 64}}
    return tmp_path, source, reservations


def qualify(source):
    runner.execute(args("exit-followup-qualification"), {}, source, 0)
    runner.execute(args("exit-followup-qualification-audit"), {}, source, 0)


def test_successor_qualification_covers_both_clocks_and_authenticates_stream(runtime):
    root, source, reservations = runtime
    qualify(source)
    assert len(runner._qualification(source, "d" * 64)) == 64
    report = runner.run._doc(root / "output" / runner.QUALIFICATION / "report.json")
    assert report["totals"]["opportunities"] == 6
    assert report["identity"]["scientific_comparison_count"] == len(report["identity"]["scientific_comparison_ids"]) == 2
    assert len(report["identity"]["reported_contrasts"]) == 3
    assert len(reservations) == 1
    with pytest.raises(ValueError):
        runner._qualification({**source, "merge": "2" * 40}, "d" * 64)
    with (root / "output" / runner.QUALIFICATION / "events.jsonl").open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(ValueError):
        runner._qualification(source, "d" * 64)


def test_successor_rejects_unregistered_scope_and_missing_actual_pins(runtime):
    _, source, _ = runtime
    invalid = args("exit-followup-qualification")
    invalid.fold = 0
    with pytest.raises(ValueError, match="fixed comparisons"):
        runner.execute(invalid, {}, source, 0)
    with pytest.raises(ValueError, match="pins"):
        runner.execute(args("exit-followup"), {}, source, 0)


def test_diagnostic_pin_and_original_implementation_are_bound(runtime, monkeypatch):
    root, _, _ = runtime
    wire = runner.wire
    old_path = root / "output" / runner.original.UNIT
    old_path.mkdir()
    source = {"merge": "1" * 40, "sources": {"unchanged.py": "e" * 64,
        "python/research/intraday_event_model_run.py": "new-dispatcher"}}
    identity = {"merge": runner.DIAGNOSTIC_MERGE, "runtime_sha256": "d" * 64,
        "summary_sha256": "a" * 64, "dataset_binding_sha256": "b" * 64,
        "source": {"sources": {**source["sources"], "python/research/intraday_event_model_run.py": "old"}}}
    usage = {"worker_cpu_seconds": 1, "helper_cpu_seconds": 0, "wall_seconds": 1}
    payload = {"identity": identity, "resources": usage}
    pin = wire.digest(wire.encode(payload))
    audit = {"identity": identity, "report_sha256": pin, "independently_verified": True, "resources": usage}
    audit_raw = wire.encode(audit)
    (old_path / "audit.json").write_bytes(audit_raw)
    monkeypatch.setattr(runner, "DIAGNOSTIC_REPORT", pin)
    monkeypatch.setattr(runner, "DIAGNOSTIC_AUDIT", wire.digest(audit_raw))
    monkeypatch.setattr(runner.original, "_publication", lambda *a, **k: (payload, {}))
    assert runner._diagnostic(args("exit-followup", True), source, "d" * 64)[1] == payload
    source["sources"]["unchanged.py"] = "f" * 64
    with pytest.raises(ValueError, match="implementation/input"):
        runner._diagnostic(args("exit-followup", True), source, "d" * 64)
    (old_path / "audit.json").write_bytes(audit_raw + b" ")
    with pytest.raises(ValueError, match="audit differs"):
        runner._diagnostic(args("exit-followup", True), source, "d" * 64)


def test_actual_start_and_clock_seal_precede_new_paths_and_auditor_uses_other_decoder(runtime, monkeypatch):
    root, source, reservations = runtime
    qualify(source)
    causal, ledgers, market = runner.original.fixtures.case(0)
    market = replace(market, bars={at:replace(bar, available_offset=at + 3) for at,bar in market.bars.items()})
    calendar = [causal["session"]]
    coordinate = {"session": causal["session"], "symbol": causal["symbol"], "continuous_length": 70,
        "continuous_price_observed": 70, "minute_states": [{"offset":i,"price":"observed"} for i in range(70)]}
    support_row = {"session": causal["session"], "symbol": causal["symbol"], "family": runner.original.FAMILY,
        "scheduled_minutes":70,"counts":{"active":70},"runs":[{"start":0,"end":70,"status":"active"}]}
    roster = {causal["event_id"]:causal}
    support = runner.original.summarize_source_support([coordinate], [support_row], roster)
    diagnostic = {"totals":{"opportunities":1}, "source_support":support,"events":{"test":"old-stream"}}
    output = root / "output" / runner.UNIT
    def prerequisite(*a):
        assert (output / "start.json").exists()
        assert '"status": "started"' in (root / "canonical/experiments.jsonl").read_text()
        return root, diagnostic
    monkeypatch.setattr(runner, "_diagnostic", prerequisite)
    monkeypatch.setattr(runner.original, "_predecessor", lambda *a: (
        {"report":{"original_episodes_by_family":{runner.original.FAMILY:1}}},
        [(root,{"session_indices":[96,128]})],calendar))
    monkeypatch.setattr(runner.original, "causal_roster", lambda *a: roster)
    monkeypatch.setattr(runner.inputs, "_rows", lambda p,m,name,k: iter(
        [coordinate] if name == "coordinate-support.jsonl" else [support_row]))
    monkeypatch.setattr(runner.original, "_fold_ledgers", lambda *a: iter([(causal,ledgers)]))
    reader = runner.artifacts._read_rows
    monkeypatch.setattr(runner.artifacts, "_read_rows", lambda p,pin:
        iter([{"roster":causal}]) if pin == {"test":"old-stream"} else reader(p,pin))
    @contextmanager
    def raw_inputs(slot, prior, invented, audit):
        sealed = json.loads((slot / "causal-roster.json").read_bytes())
        assert sealed["clock_gates"][causal["event_id"]] == {"session_length":70,"clock_admissible":False}
        assert not invented
        yield None, None
    monkeypatch.setattr(runner.run, "original_inputs", raw_inputs)
    decoders = []
    def markets(*a, audit):
        decoders.append(audit)
        return {(causal["session"], causal["symbol"]):market}
    monkeypatch.setattr(runner.inputs, "SessionMarkets", markets)
    registrations = {runner.run.ORIGINAL:{"approved":True}}
    runner.execute(args("exit-followup",True), registrations, source, 0)
    runner.execute(args("exit-followup-audit",True), registrations, source, 0)
    assert decoders == [False,True]
    assert runner.run._doc(output / "audit.json")["independently_verified"] is True
    assert len(reservations) == 2
