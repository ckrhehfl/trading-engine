"""Invented timing, matching, cash and receipt tests for the EL mask."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest

from research import intraday_event_headroom as headroom
from research import intraday_event_headroom_reference as reference
from research import intraday_event_near_pullback as near_pullback
from research import intraday_event_near_pullback_reference as near_reference


DAY = "2026-02-02"


def query(event="confirm", peak="25100", close="25000"):
    return {"key": f"{event}@22", "session": DAY, "symbol": "invented", "query_kind": "entry",
            "frame": {"event_id": event, "family": headroom.FAMILY, "state": "CONFIRMED", "level": peak,
                      "decision_offset": 22, "offset": 19, "overlap_group_id": "cluster",
                      "conditions": {"pullback_at": 17}},
            "label_query": {"decision_offset": 22, "reference_offset": None if close is None else 19,
                            "reference_close": close, "event_id": event}, "reference_close": close}


def ledger(event, *, unresolved=False, no_commit=False, unknown_fill=False):
    quantity = 0 if no_commit else None if unknown_fill else 1
    sales = [] if unresolved or no_commit or unknown_fill else [{"quantity": 1, "price": "25200"}]
    gross = "0" if no_commit else None if unresolved or unknown_fill else "200"
    net = "0" if no_commit else None if unresolved or unknown_fill else "235/2"
    notional = "0" if no_commit else None if unknown_fill else "25000"
    return {**dict(zip(headroom.ARM_FIELDS, headroom.ARM, strict=True)), "family": headroom.FAMILY,
            "session": DAY, "symbol": "invented", "cluster_id": "cluster", "original_episode_id": event,
            "candidate_key": None if no_commit else f"{event}@22", "entry_decision_offset": None if no_commit else 22,
            "action": "no_commit" if no_commit else "immediate", "state": "NO_COMMIT" if no_commit else "CONFIRMED",
            "selection_reason": "invented_abstain" if no_commit else "fixed_confirmation",
            "execution": {"decision_offset": 22, "cost_bps": 33, "reference_close": "25000",
                          "filled_quantity": quantity, "entry_price": "25000" if quantity else None,
                          "entry_notional": notional, "remaining_quantity": None if unresolved else 0, "sales": sales},
            "outcome": {"filled_quantity": quantity, "entry_notional": notional, "gross": gross, "net": net}}


def test_exact_threshold_and_signed_distance_use_observed_limit():
    assert headroom.decision(query())["status"] == "pass"
    assert headroom.decision(query(peak="25099"))["status"] == "reject"
    assert headroom.decision(query(peak="24000"))["status"] == "reject"
    assert headroom.decision(query(close=None))["status"] == "unknown"


def test_reference_from_future_or_different_receipt_fails():
    future = query()
    future["label_query"]["reference_offset"] = 22
    with pytest.raises(ValueError, match="known close"):
        headroom.decision(future)
    mismatch = query()
    mismatch["label_query"]["reference_close"] = "24999"
    with pytest.raises(ValueError, match="known close"):
        headroom.decision(mismatch)


def test_same_orders_and_unknowns_and_zero_weeks_survive_independent_recalculation():
    fixtures = [query("pass"), query("reject", peak="25099"), query("unknown_exit"), query("unknown_fill")]
    decisions = {row["key"]: headroom.decision(row) for row in fixtures}
    rows = [ledger("pass"), ledger("reject"), ledger("unknown_exit", unresolved=True),
            ledger("unknown_fill", unknown_fill=True), ledger("no_commit", no_commit=True)]
    calendar = [DAY, "2026-02-09"]
    totals = headroom.summarize(calendar, decisions, rows)
    assert reference.verify(calendar, decisions, rows, {"totals": totals}) == totals
    assert totals["screen"] == {"pass": 3, "reject": 1, "unknown": 0, "no_commit": 1}
    assert totals["baseline"]["net_cash"] == "235"
    assert totals["filtered"]["net_cash"] == "235/2"
    assert totals["filtered"]["fills"] == 2
    assert totals["filtered"]["unknown_fills"] == 1
    assert totals["filtered"]["unknown_cash"] == 2
    assert totals["joint_known"] == 3 and totals["paired_net_cash"] == "-235/2"
    assert totals["weekly"]["2026-W07"] == dict.fromkeys(("baseline_fills", "filtered_fills", "filtered_unknown_fills"), 0)

    tampered = deepcopy(rows)
    tampered[0]["execution"]["sales"][0]["price"] = "25201"
    with pytest.raises(ValueError, match="reconstruction differs"):
        reference.verify(calendar, decisions, tampered, {"totals": totals})


def test_duplicate_and_wrong_identity_cannot_inflate_frequency():
    frame = query()
    decisions = {frame["key"]: headroom.decision(frame)}
    row = ledger("confirm")
    with pytest.raises(ValueError, match="duplicate"):
        headroom.summarize([DAY], decisions, [row, row])
    with pytest.raises(ValueError, match="sealed decision"):
        headroom.summarize([DAY], decisions, [{**row, "symbol": "different"}])


def near_fixture(*, peak="25050", base="24750", trough="24950", age=5, close="25000"):
    confirmation = query("test", peak, close)
    floor = str((Fraction(peak) + Fraction(base)) / 2)
    start = 19 - age
    confirmation["frame"].update(onset_offset=7, kind="terminal", structure=trough,
                                 conditions={"pullback_at": start, "pullback_age": age, "retained_advance_floor": floor})
    pullback = deepcopy(confirmation)
    pullback["frame"].update(offset=start, decision_offset=start + 3, kind="first_pullback", state="PULLBACK")
    onset = deepcopy(confirmation)
    onset["frame"].update(offset=7, decision_offset=10, kind="at_onset", state="WAITING_PULLBACK",
                          conditions={"base_close": base})
    return confirmation, pullback, onset


@pytest.mark.parametrize(("changes", "status", "near_status"), [
    ({}, "pass", "pass"),
    ({"trough": "24949"}, "reject", "pass"),
    ({"age": 6}, "reject", "pass"),
    ({"peak": "25100", "base": "24800", "trough": "25000"}, "reject", "reject"),
    ({"peak": "24900", "base": "24600", "trough": "24800"}, "reject", "reject"),
    ({"peak": "24950", "base": "24650", "trough": "24850"}, "pass", "pass"),
    ({"close": None}, "unknown", "unknown"),
])
def test_near_pullback_exact_boundaries_and_archived_geometry(changes, status, near_status):
    confirmation, pullback, onset = near_fixture(**changes)
    sealed = near_pullback.decision(confirmation)
    near_reference.verify_fields(sealed, confirmation, pullback, onset, confirmation)
    assert sealed["status"] == status and sealed["near_status"] == near_status
    with pytest.raises(ValueError, match="Sealed near-pullback fields"):
        near_reference.verify_fields({**sealed, "base": "111"}, confirmation, pullback, onset, confirmation)


@pytest.mark.parametrize("mode", ["headroom", "near-pullback"])
def test_launch_seals_causal_inputs_and_audits_frozen_peak(tmp_path, monkeypatch, mode):
    from research import intraday_event_headroom_run as runner
    from research import intraday_event_model_launch as launch

    run, wire, contract = launch.run, launch.wire, launch.contract
    (tmp_path / "canonical").mkdir()
    (tmp_path / "output").mkdir()
    journal = tmp_path / "canonical" / "experiments.jsonl"
    journal.touch()
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    monkeypatch.setattr(run, "_locks", nullcontext)
    monkeypatch.setattr(run, "budget", lambda: None)
    monkeypatch.setattr(run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(launch.bridge, "restrict_local_network", lambda: None)
    monkeypatch.setattr(wire, "prohibit_ef_tasks", lambda: None)
    usage = {"worker_cpu_seconds": 1, "helper_cpu_seconds": 0, "wall_seconds": 1}
    monkeypatch.setattr(run, "usage", lambda: usage)
    reserved = []
    monkeypatch.setattr(run, "audit_budget", reserved.append)

    input_binding = {"inputs_sha256": "f" * 64}
    raw = wire.encode(input_binding)
    (tmp_path / "canonical" / f"{contract.UNIT}-dataset-binding.json").write_bytes(raw)
    monkeypatch.setattr(contract, "validate_dataset_binding", lambda content, pin: SimpleNamespace(**content))
    unit = near_pullback.UNIT if mode == "near-pullback" else runner.UNIT
    slot = tmp_path / "output" / unit
    predecessor = tmp_path / "output" / f"{contract.UNIT}-summary"
    predecessor.mkdir()
    source = {"merge": "1" * 40, "sources": {runner.PLAN: "3" * 64, "script.py": "4" * 64}}
    identity = {"release": {"merge": runner.PREDECESSOR, "runtime_sha256": "d" * 64,
                            "sources": {"script.py": "4" * 64}},
                "inputs_sha256": input_binding["inputs_sha256"], "case_report_sha256": "5" * 64}
    calendar = [(date.fromisoformat(DAY) + timedelta(days=i)).isoformat() for i in range(160)]
    arm = dict(zip(headroom.ARM_FIELDS, headroom.ARM, strict=True))
    summary = {**identity, "fold_audits": [
        {"coordinate": [*headroom.ARM[:2], i], "audit_sha256": "6" * 64, "manifest_sha256": "7" * 64}
        for i in range(4)], "report": {"evaluation_sessions": calendar,
            "fold_support": [{"kind": headroom.ARM[0], "scope": headroom.ARM[1], "fold": i,
                              "policy_support": {"holding": True}} for i in range(4)],
            "original_episodes_by_family": {headroom.FAMILY: 1}, "cash": {"groups": [
                {**arm, "dimension": "family", "value": headroom.FAMILY,
                 "fills": 1, "commits": 1, "unknown_cash_rows": 0, "known_subset_net_cash": "235/2",
                 "known_subset_gross_cash": "200", "known_fill_notional": "25000"}]}}}
    summary_raw = wire.encode(summary)
    (predecessor / "report.json").write_bytes(summary_raw)
    (predecessor / "start.json").write_bytes(wire.encode(identity))
    (predecessor / "completion.json").write_bytes(wire.encode({"report_sha256": wire.digest(summary_raw),
                                                              "fold_audit_count": len(contract.ROSTER)}))

    def admit(binding):
        assert (slot / "start.json").is_file()
        assert '"status": "started"' in journal.read_text(encoding="utf-8")
        return [(tmp_path, {"dataset": {"session_indices": [96, 128]}}, None)]

    monkeypatch.setattr(launch, "_admit_actual", admit)
    if mode == "near-pullback":
        frame, pullback, onset = near_fixture()
        event_states = [onset, pullback, frame]
    else:
        frame = query("test")
        pullback = deepcopy(frame)
        pullback["frame"].update(kind="first_pullback", offset=17, decision_offset=20)
        event_states = [pullback]
    monkeypatch.setattr(launch.inputs, "query_rows", lambda *args: iter([frame]))
    monkeypatch.setattr(launch.inputs, "_rows", lambda *args: iter(event_states))
    monkeypatch.setattr(launch, "_invented_inputs", lambda _: ((), "8" * 64, {}))
    monkeypatch.setattr(launch, "_qualification_identity", lambda *args: "9" * 64)
    monkeypatch.setattr(launch, "_identity", lambda release, coords, *args: {"fold": coords[-1]})
    monkeypatch.setattr(launch.receipts, "audited_actual", lambda old: (
        tmp_path, old, {"outcome": {"manifest": {"sha256": "7" * 64}}}, "6" * 64))
    monkeypatch.setattr(launch.artifacts, "read_manifest", lambda path, manifest, old: {
        "files": {"ledger.jsonl": old["fold"]}})

    def ledger_read(path, fold):
        assert (slot / "causal-decisions.json").is_file()
        return iter([ledger("test")] if fold == 0 else ())

    monkeypatch.setattr(launch.artifacts, "_read_rows", ledger_read)
    args = SimpleNamespace(mode=mode, kind=None, scope=None, fold=None, shard=None, attempt=None,
                           summary_sha256=wire.digest(summary_raw), dataset_binding_sha256=wire.digest(raw))
    runner.execute(args, source, 0)
    args.mode = f"{mode}-audit"
    runner.execute(args, source, 0)
    assert reserved == [usage]
    assert run._doc(slot / "audit.json")["report_sha256"] == wire.digest((slot / "report.json").read_bytes())
    if mode == "near-pullback":
        totals = run._doc(slot / "report.json")["totals"]
        assert totals["near"]["filtered"]["fills"] == totals["screened"]["filtered"]["fills"] == 1
