"""Invented EM admission/sealing/publication tests; never original data."""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from research import intraday_event_attribution_run as runner


def _args(mode, *, actual=False):
    return SimpleNamespace(mode=mode, kind=None, scope=None, fold=None, shard=None, attempt=None,
        summary_sha256="a" * 64 if actual else None, dataset_binding_sha256="b" * 64 if actual else None)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    run, wire = runner.run, runner.wire
    (tmp_path / "canonical").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "canonical/experiments.jsonl").touch()
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    monkeypatch.setattr(run, "_locks", nullcontext)
    monkeypatch.setattr(run, "budget", lambda: None)
    monkeypatch.setattr(run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(runner.bridge, "restrict_local_network", lambda: None)
    monkeypatch.setattr(wire, "prohibit_ef_tasks", lambda: None)
    usage = {"worker_cpu_seconds": 1, "helper_cpu_seconds": 0, "wall_seconds": 1}
    monkeypatch.setattr(run, "usage", lambda: usage)
    reserves = []
    monkeypatch.setattr(run, "audit_budget", reserves.append)
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "3" * 64, "old.py": "4" * 64}}
    calendar, cases = runner.fixtures.invented_cases(calendar_sessions=3, names=2)
    cases = tuple(cases)
    monkeypatch.setattr(runner.fixtures, "full_cases", lambda: (calendar, iter(cases)))
    monkeypatch.setattr(runner.fixtures, "FULL_EVENTS", len(cases))
    return tmp_path, source, usage, reserves


def test_same_release_qualification_reconstructs_and_authenticates_stream(runtime):
    root, source, usage, reserves = runtime
    runner.execute(_args("attribution-qualification"), {}, source, 0)
    runner.execute(_args("attribution-qualification-audit"), {}, source, 0)
    assert reserves == [usage]
    assert len(runner._qualification(source, "d" * 64)) == 64
    slot = root / "output" / runner.QUALIFICATION
    with (slot / "events.jsonl").open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(ValueError):
        runner._qualification(source, "d" * 64)


def test_qualification_is_source_bound_and_actual_requires_pins(runtime):
    _, source, _, _ = runtime
    runner.execute(_args("attribution-qualification"), {}, source, 0)
    runner.execute(_args("attribution-qualification-audit"), {}, source, 0)
    with pytest.raises(ValueError, match="identity"):
        runner._qualification({**source, "merge": "2" * 40}, "d" * 64)
    with pytest.raises(ValueError, match="pins"):
        runner.execute(_args("attribution"), {}, source, 0)


def _causal_rows(causal):
    common = {"session": causal["session"], "symbol": causal["symbol"]}
    frame = {"event_id": causal["event_id"], "family": runner.FAMILY, "overlap_group_id": causal["cluster_id"],
             "kind": "at_onset", "terminal": False, "state": "WAITING_PULLBACK", "decision_offset": causal["onset_decision_offset"]}
    terminal = {**frame, "kind": "terminal", "terminal": True, "state": "CONFIRMED",
                "decision_offset": causal["confirmation_decision_offset"]}
    query = {**common, "frame": terminal, "key": causal["confirmation_key"], "query_kind": "entry",
             "reference_close": causal["confirmation_reference"], "label_query": {
                 "event_id": causal["event_id"], "decision_offset": causal["confirmation_decision_offset"],
                 "reference_close": causal["confirmation_reference"], "reference_offset": 12}}
    return [{**common, "frame": frame}, {**common, "frame": terminal}], [query]


def test_causal_roster_keeps_unconfirmed_and_rejects_future_reference(monkeypatch):
    causal, _, _ = runner.fixtures.case(0)
    other, _, _ = runner.fixtures.case(9)
    states, queries = _causal_rows(causal)
    extra, _ = _causal_rows({**other, "confirmation_decision_offset": 15})
    extra[-1]["frame"].update(state="EXPIRED_NO_CONFIRMATION")
    monkeypatch.setattr(runner.inputs, "_rows", lambda *args: iter(states + extra))
    monkeypatch.setattr(runner.inputs, "query_rows", lambda *args: iter(queries))
    shards = [(None, {"session_indices": [96, 128]})]
    roster = runner.causal_roster(shards, [causal["session"]])
    assert len(roster) == 2 and roster[other["event_id"]]["confirmation_key"] is None
    queries[0]["label_query"]["reference_offset"] = queries[0]["frame"]["decision_offset"]
    with pytest.raises(ValueError, match="reference"):
        runner.causal_roster(shards, [causal["session"]])


def test_actual_starts_before_reads_seals_before_cash_and_uses_independent_decoder(runtime, monkeypatch):
    root, source, _, reserves = runtime
    runner.execute(_args("attribution-qualification"), {}, source, 0)
    runner.execute(_args("attribution-qualification-audit"), {}, source, 0)
    wire, run, launch = runner.wire, runner.run, runner.launch
    causal, ledgers, market = runner.fixtures.case(1)
    market = replace(market, bars={offset: replace(bar, available_offset=offset + 3)
                                   for offset, bar in market.bars.items()})
    calendar = tuple((date.fromisoformat(causal["session"]) + timedelta(days=i)).isoformat() for i in range(160))
    binding_body = {"inputs_sha256": "f" * 64}
    (root / "canonical" / f"{runner.contract.UNIT}-dataset-binding.json").write_bytes(wire.encode(binding_body))
    monkeypatch.setattr(runner.contract, "validate_dataset_binding", lambda body, pin: SimpleNamespace(**body))
    prior = root / "output" / f"{runner.contract.UNIT}-summary"
    prior.mkdir()
    old = {"merge": runner.PREDECESSOR, "runtime_sha256": "d" * 64, "sources": {"old.py": "4" * 64}}
    identity = {"release": old, "inputs_sha256": "f" * 64, "case_report_sha256": "5" * 64}
    summary = {**identity, "fold_audits": [{"coordinate": [*runner.MODEL, i], "audit_sha256": "6" * 64,
        "manifest_sha256": "7" * 64} for i in range(4)], "report": {"evaluation_sessions": list(calendar),
            "original_episodes_by_family": {runner.FAMILY: 1}}}
    summary_raw = wire.encode(summary)
    (prior / "start.json").write_bytes(wire.encode(identity))
    (prior / "report.json").write_bytes(summary_raw)
    (prior / "completion.json").write_bytes(wire.encode({"report_sha256": wire.digest(summary_raw),
                                                        "fold_audit_count": len(runner.contract.ROSTER)}))
    output = root / "output" / runner.UNIT
    journal = root / "canonical/experiments.jsonl"
    def admit(binding):
        assert (output / "start.json").exists()
        assert '"status": "started"' in journal.read_text()
        return [(root, {"dataset": {"session_indices": [96, 128]}}, None)]
    monkeypatch.setattr(launch, "_admit_actual", admit)
    monkeypatch.setattr(runner.inputs, "calendar_from_coordinates", lambda _: ("prefix",) * 97 + calendar)
    states, queries = _causal_rows(causal)
    coordinate = {"session": causal["session"], "symbol": causal["symbol"], "continuous_length": 70,
        "continuous_price_observed": 70,
        "minute_states": [{"offset": i, "price": "observed"} for i in range(70)]}
    family_support = {"session": causal["session"], "symbol": causal["symbol"], "family": runner.FAMILY,
        "scheduled_minutes": 70, "counts": {"active": 70},
        "runs": [{"start": 0, "end": 70, "status": "active", "event_id": causal["event_id"]}]}
    def original_rows(path, manifest, name, kind):
        return iter({"event-states.jsonl": states, "coordinate-support.jsonl": [coordinate],
                     "event-support.jsonl": [family_support]}[name])
    monkeypatch.setattr(runner.inputs, "_rows", original_rows)
    monkeypatch.setattr(runner.inputs, "query_rows", lambda *args: iter(queries))
    monkeypatch.setattr(launch, "_invented_inputs", lambda _: ((), "8" * 64, {}))
    monkeypatch.setattr(launch, "_qualification_identity", lambda *args: "9" * 64)
    monkeypatch.setattr(launch, "_identity", lambda release, coords, *args: {"fold": coords[-1]})
    monkeypatch.setattr(runner.receipts, "audited_actual", lambda old: (
        root, old, {"outcome": {"manifest": {"sha256": "7" * 64}}}, "6" * 64))
    monkeypatch.setattr(runner.artifacts, "read_manifest", lambda path, manifest, old: {"files": {"ledger.jsonl": old["fold"]}})
    read_rows = runner.artifacts._read_rows
    def ledger_rows(path, pin):
        if type(pin) is int:
            assert (output / "causal-roster.json").is_file()
            return iter(ledgers.values() if pin == 0 else ())
        return read_rows(path, pin)
    monkeypatch.setattr(runner.artifacts, "_read_rows", ledger_rows)
    @contextmanager
    def raw_inputs(slot, registered, invented, audit):
        assert slot == output and not invented and registered == {"approved": True}
        assert (slot / "causal-roster.json").is_file()
        yield None, None
    monkeypatch.setattr(run, "original_inputs", raw_inputs)
    decoders = []
    def markets(universe, reader, days, *, audit):
        assert tuple(days) == calendar
        decoders.append(audit)
        return {(causal["session"], causal["symbol"]): market}
    monkeypatch.setattr(runner.inputs, "SessionMarkets", markets)
    args = _args("attribution", actual=True)
    args.summary_sha256 = wire.digest(summary_raw)
    registrations = {run.ORIGINAL: {"approved": True}}
    runner.execute(args, registrations, source, 0)
    args.mode += "-audit"
    runner.execute(args, registrations, source, 0)
    assert decoders == [False, True]
    assert run._doc(output / "audit.json")["independently_verified"] is True
    assert len(reserves) == 2
    with pytest.raises((ValueError, FileExistsError)):
        runner.execute(_args("attribution", actual=True), registrations, source, 0)


def test_original_actual_source_mismatch_is_not_a_new_result(runtime):
    _, source, _, _ = runtime
    with pytest.raises((FileNotFoundError, ValueError)):
        runner._predecessor(_args("attribution", actual=True), source, "d" * 64)
