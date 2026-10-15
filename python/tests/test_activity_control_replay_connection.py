"""Pure CONTROL integration over fabricated, canonically parameterized evidence.

The daily toy calendar asserts no exchange-calendar or historical-source truth.
No saved study, market file, database or external service is read by these tests.
"""
import builtins
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest

from research import activity_control_replay as module
from research.activity_book import ActivityBook
from research.activity_control_connection import bind_control_holding_scope
from research.activity_control_inputs_preflight import nominee_selections
from research.activity_control_selection import (
    CountedControlSelection, _rank, activity_targets_fingerprint, population_fingerprint,
)
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_failure_inputs import _typed
from research.activity_holding_inputs import _FINGERPRINT_SCOPE, _provenance, adapt_holding_scan
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_preflight import _audit
from research.activity_replay import DecimalSeries, replay_synthetic
from research.activity_timing import SessionLagPolicy
from research.activity_timing_report import paired_timing_report


ACTIVITY = "a" * 64
RECEIPT = "2026-10-15T00:00:00+00:00"


def fixture(*, cash_only=False, frozen_exit=False, frozen_entry=False):
    """Build a real bound nominee proof, then synthesize the saved ACTIVITY pair."""
    parameters = deepcopy(PARAMETERS)
    end = date.fromisoformat(parameters["end"])
    calendar = tuple(end + timedelta(days=index - 260) for index in range(263))
    formation = calendar[60]
    codes = tuple(f"{number:06d}" for number in range(1, 7))
    population = tuple((code, f"KR7{code}000") for code in codes)
    order = _rank(codes, formation, parameters["seed"])
    control_code = order[0]
    targets = {arm: () if cash_only else (order[-1],) for arm in ("D1", "D2")}
    scope = {"formations": [{"formation": formation.strftime("%Y%m%d")}]}
    normal = [{"formation": formation.isoformat(), "code": code, "isin": isin,
               "control_arms": {arm: {"eligible": True, "exclusion_reasons": []} for arm in ("D1", "D2")}}
              for code, isin in population]
    dispositions = [{"formation": formation.strftime("%Y%m%d"), "code": code, "isin": isin,
                     "normal_input_disposition": "eligible"} for code, isin in population]
    nominees, requested, identities = nominee_selections(normal, scope, calendar, parameters, dispositions)
    selections, partitions = {}, {}
    for arm, lag in (("D1", 1), ("D2", 2)):
        policy = SessionLagPolicy(lag, paired=True)
        decision, cutoff = policy.execution_on(calendar, 60), policy.selection_at(calendar, 60)
        selections[arm] = (CountedControlSelection(arm, formation, decision, cutoff, parameters["seed"], targets[arm],
            activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                selection_at=cutoff, targets=targets[arm], seed=parameters["seed"]),
            population, len(population), population_fingerprint(population), nominees[arm][0].source_partition_sha256,
            frozenset(), () if cash_only else ((control_code, "eligible", "b" * 64),)),)
        partitions[arm] = (PartitionSelection(formation, decision, cutoff, targets[arm], len(population), "c" * 64),)
    pins = {"synthetic_producer": "d" * 64, "normal_input_audit": digest(encoded(normal))}
    read_scope = {
        "schema": "activity-holding-price-preflight-v1:read-scope-v1",
        "producer_schema": "activity-control-inputs-v1",
        "scope_kind": "reviewed_nominee_original_issue_union_not_final_control_or_successor_closure",
        "calendar_dates": [day.strftime("%Y%m%d") for day in calendar],
        "requested_code_dates": requested, "activity_snapshot_sha256": ACTIVITY,
        "normal_input_audit_sha256": pins["normal_input_audit"], "input_sha256": pins,
    }
    read_sha = digest(encoded(read_scope))
    positions = {day.isoformat(): index for index, day in enumerate(calendar)}
    frozen = ({187, 188} if frozen_exit else set()) | ({61} if frozen_entry else set())
    raw_rows, fingerprint_rows = {}, []
    for code, days in requested.items():
        for text in days:
            compact = date.fromisoformat(text).strftime("%Y%m%d")
            raw = ([compact, "100", "100", "100", "100", "0", "0"]
                   if code == control_code and positions[text] in frozen
                   else [compact, "100", "102", "99", "101", "100", "1000"])
            typed = [_typed(value) for value in raw]
            raw_rows[code, compact] = {
                "values": dict(zip(("open", "high", "low", "close", "volume", "turnover"), raw[1:])),
                "typed_values": typed,
            }
            fingerprint_rows.append([code, text, typed])
    fingerprint = {
        "schema": "activity-cached-price-parity-v1:typed-scan-v1",
        "calendar_dates": read_scope["calendar_dates"], "read_scope_sha256": read_sha,
        "intersection_code_dates": requested,
        "panel": [[_typed(1), _typed("20190102"), _typed("20260918")]],
        "progress": [[code, [[_typed(value) for value in
            ("20190102", "20260918", len(calendar), 0, "done", RECEIPT)]]] for code in requested],
        "rows": fingerprint_rows,
    }
    quote_sha = digest(encoded(fingerprint))
    for (_, compact), row in raw_rows.items():
        row["provenance"] = _provenance(date.fromisoformat(compact), calendar, RECEIPT, quote_sha)
    scan = {"rows": raw_rows, "fingerprint": fingerprint, "typed_snapshot_sha256": quote_sha,
            "snapshot_received_at": RECEIPT, "fingerprint_scope": _FINGERPRINT_SCOPE,
            "whole_database_hash": False, "BI_snapshot_equality_checked": False,
            "progress_counts_recomputed_from_prices": False}
    holding = adapt_holding_scan(scan, calendar, requested, quote_snapshot_sha256=quote_sha,
        activity_snapshot_sha256=ACTIVITY, read_scope_sha256=read_sha)
    plan = provisional_exit_bounds(nominees["D1"], nominees["D2"], holding, parameters, requested, identities,
                                   input_sha256=pins)
    bound = bind_control_holding_scope(selections, calendar=calendar, parameters=parameters, scope=scope,
        dispositions=dispositions, normal_audits=normal,
        holding_audit=strict_json(encoded(_audit(scan, holding, requested, read_sha))),
        read_scope=read_scope, quote_snapshot_sha256=quote_sha, activity_snapshot_sha256=ACTIVITY,
        read_scope_sha256=read_sha, provisional_exit_scope=plan, producer_input_sha256=pins)
    coverage = {"schema": "activity-reviewed-actions-v1", "status": "reviewed_complete",
        "activity_snapshot_sha256": ACTIVITY, "quote_snapshot_sha256": quote_sha,
        "required_scope_sha256": plan["child_scope_sha256"], "calendar_sha256": plan["calendar_sha256"],
        "windows": [], "events": [], "noops": [], "unresolved": []}
    evidence = {"references": {}, "bases": {}}
    for index, window in enumerate(plan["child_windows"]):
        ref = f"synthetic-ordinary-{index}"
        coverage["windows"].append(dict(window, review_state="reviewed_no_event", event_ids=[], evidence_refs=[ref]))
        evidence["references"][ref] = dict(window, raw_sha256="e" * 64, review_sha256="f" * 64,
            coordinate="synthetic-document:section-2", reviewed_on="2026-10-15",
            finding="inferred_no_compulsory_action", event_ids=[])
    # These are fixture preparation calls, never original-book calls by the seam.
    activities = {arm: replay_synthetic(calendar, holding.panel, parameters, partitions[arm],
        dataset_sha256=quote_sha, initial_book=ActivityBook(Decimal(1), (), formation),
        timing_policy=SessionLagPolicy(lag, paired=True)) for arm, lag in (("D1", 1), ("D2", 2))}
    original_report = paired_timing_report(activities["D1"], activities["D2"], partitions["D1"], partitions["D2"],
                                          initial_nav=Decimal(1))
    original_replay = {"schema": "activity-paired-replay-v1", "status": "pure_paired_replay", "initial_nav": "1",
        "report": deepcopy(original_report), "replays": _json_value(activities), "selections": _json_value(partitions)}
    return dict(bound=bound, reviewed_coverage=coverage, basis_evidence=evidence,
                original_report=original_report, original_replay=original_replay, parameters=parameters)


def forbid_replay(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("rejected input reached CONTROL performance")
    monkeypatch.setattr(module, "replay_synthetic", denied)


def test_real_ordinary_decoder_two_control_books_inventory_and_report_preserve_inputs(monkeypatch):
    data = fixture()
    before = deepcopy(data)
    order, calls = [], []
    for name in ("reviewed_actions", "replay_synthetic", "verify_ordinary_inventory", "descriptive_control_report"):
        original = getattr(module, name)
        def traced(*args, _name=name, _original=original, **kwargs):
            order.append(_name)
            if _name == "replay_synthetic":
                calls.append((args, kwargs))
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, name, traced)
    result = module.replay_bound_control(**data)
    assert data == before
    assert order == ["reviewed_actions", "replay_synthetic", "replay_synthetic",
                     "verify_ordinary_inventory", "descriptive_control_report"]
    assert len(calls) == 2
    for (args, kwargs), arm, lag in zip(calls, ("D1", "D2"), (1, 2), strict=True):
        assert args[3] == getattr(data["bound"], f"selections_{arm.lower()}")
        assert isinstance(args[3][0], CountedControlSelection)
        assert kwargs["timing_policy"] == SessionLagPolicy(lag, paired=True)
        assert kwargs["initial_book"] == ActivityBook(Decimal(1), (), data["bound"].holding_inputs.calendar[60])
        assert kwargs["events"] == ()
        row = result["report"]["arms"][arm]
        assert row["CONTROL"]["filled_entries"] == row["CONTROL"]["ordinary_closed_trades"] == 1
        assert row["CONTROL"]["terminal_open_lots"] == 0
        assert row["ACTIVITY"]["cumulative_return"] == data["original_report"]["arms"][arm]["total_return"]
        assert row["target_overlap"]["formations"][0]["activity_target_ceiling"] == 1
        assert len(result["replays"][arm]["selections"][0][1]) == 1
    assert all(isinstance(value, DecimalSeries) for value in data["bound"].holding_inputs.panel.values())
    assert len(data["bound"].holding_inputs.observations) == 1200
    assert all(result[key] is False for key in ("original_ACTIVITY_replayed", "comparison_power_prerequisite_met",
        "power_design_passed", "actual_study_completed", "promotion_allowed"))
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("corruption", ["targets", "source_count", "decision", "cutoff", "foreign_source",
    "duplicate_formation", "missing_formation", "schema", "status", "initial_nav", "report", "missing_arm"])
def test_changed_saved_activity_contract_rejected_before_either_control_book(monkeypatch, corruption):
    data = fixture()
    saved = data["original_replay"]
    partition = saved["selections"]["D1"][0]
    if corruption == "targets": saved["replays"]["D1"]["selections"][0][1] = []
    elif corruption == "source_count": partition["source_population_count"] += 1
    elif corruption == "decision": partition["decision_on"] = data["bound"].holding_inputs.calendar[62].isoformat()
    elif corruption == "cutoff": partition["selection_at"] = partition["selection_at"].replace("08:30", "08:31")
    elif corruption == "foreign_source": partition["eligible_codes"] = ["999999"]
    elif corruption == "duplicate_formation": saved["replays"]["D1"]["selections"] *= 2
    elif corruption == "missing_formation": saved["replays"]["D1"]["selections"] = []
    elif corruption in ("schema", "status"): saved[corruption] = "different"
    elif corruption == "initial_nav": saved["initial_nav"] = "2"
    elif corruption == "report": saved["report"]["schema"] = "changed"
    else: del saved["replays"]["D2"]
    forbid_replay(monkeypatch)
    with pytest.raises(ValueError):
        module.replay_bound_control(**data)


@pytest.mark.parametrize("corruption", ["population_identity", "population_code", "formation", "missing_arm", "targets"])
def test_changed_valid_counted_declarations_rejected_before_replay(monkeypatch, corruption):
    data = fixture()
    bound = data["bound"]
    selected = bound.selections_d2[0]
    if corruption in ("population_identity", "population_code"):
        population = list(selected.source_population)
        if corruption == "population_identity": population[0] = (population[0][0], "KR7999999000")
        else:
            excluded = next(index for index, (code, _) in enumerate(population)
                            if code not in selected.activity_targets and code not in selected.eligible_codes)
            population[excluded] = ("999999", "KR7999999000")
            population.sort()
        population = tuple(population)
        # Rehashing a changed complete identity population cannot evade the paired check.
        # Resolve its first ranked candidate to keep the declaration structurally valid.
        first = _rank((code for code, _ in population), selected.formation_on, selected.seed)[0]
        selected = replace(selected, source_population=population, source_population_sha256=population_fingerprint(population),
                           resolved_prefix=((first, "eligible", "b" * 64),))
    elif corruption == "formation": selected = replace(selected, formation_on=selected.formation_on - timedelta(days=1),
        activity_targets_sha256=activity_targets_fingerprint(arm=selected.arm,
            formation_on=selected.formation_on - timedelta(days=1), decision_on=selected.decision_on,
            selection_at=selected.selection_at, targets=selected.activity_targets, seed=selected.seed),
        resolved_prefix=tuple((code, "eligible", "b" * 64) for code in
            _rank((code for code, _ in selected.source_population), selected.formation_on - timedelta(days=1), selected.seed)[:1]))
    elif corruption == "targets":
        targets = (selected.eligible_codes[0],)
        selected = replace(selected, activity_targets=targets, activity_targets_sha256=activity_targets_fingerprint(
            arm=selected.arm, formation_on=selected.formation_on, decision_on=selected.decision_on,
            selection_at=selected.selection_at, targets=targets, seed=selected.seed))
    data["bound"] = replace(bound, selections_d2=() if corruption == "missing_arm" else (selected,))
    forbid_replay(monkeypatch)
    with pytest.raises(ValueError):
        module.replay_bound_control(**data)


@pytest.mark.parametrize(("key", "value"), [("threshold", 3), ("slippage_bps_per_side", 5),
    ("holding_sessions", 63), ("slots", 19), ("seed", 20261006), ("end", "2026-09-17"),
    ("commission_bps_per_side", 0), ("bootstrap_repetitions", 1)])
def test_canonical_parameter_value_and_numeric_type_changes_refused_before_replay(monkeypatch, key, value):
    data = fixture()
    data["parameters"][key] = value
    forbid_replay(monkeypatch)
    with pytest.raises(ValueError, match="unchanged canonical parameters"):
        module.replay_bound_control(**data)


@pytest.mark.parametrize("corruption", ["missing_window", "day_gap", "unknown", "unresolved", "event", "successor",
    "basis", "missing_ref", "no_ref", "identity", "source_pin", "scope_pin", "calendar_pin", "unsupported_finding"])
def test_incomplete_or_event_evidence_refused_before_control_performance(monkeypatch, corruption):
    data = fixture()
    coverage, evidence = data["reviewed_coverage"], data["basis_evidence"]
    window = coverage["windows"][0]
    ref = evidence["references"][window["evidence_refs"][0]]
    if corruption == "missing_window": coverage["windows"].pop(0)
    elif corruption == "day_gap": window["start"] = (date.fromisoformat(window["start"]) + timedelta(days=1)).isoformat()
    elif corruption == "unknown": window["review_state"] = "unknown"
    elif corruption == "unresolved": coverage["unresolved"] = [{"code": window["code"], "isin": window["isin"]}]
    elif corruption == "event": coverage["events"] = [{"code": window["code"], "successor_code": "999999"}]
    elif corruption == "successor": coverage["windows"].append(dict(window, code="999999", isin="KR7999999000"))
    elif corruption == "basis": evidence["bases"]["unexpected"] = {}
    elif corruption == "missing_ref": del evidence["references"][window["evidence_refs"][0]]
    elif corruption == "no_ref": window["evidence_refs"] = []
    elif corruption == "identity": window["isin"] = "KR7999999000"
    elif corruption == "source_pin": coverage["quote_snapshot_sha256"] = "0" * 64
    elif corruption == "scope_pin": coverage["required_scope_sha256"] = "0" * 64
    elif corruption == "calendar_pin": coverage["calendar_sha256"] = "0" * 64
    else: ref["finding"] = "metadata_only_no_event"
    forbid_replay(monkeypatch)
    with pytest.raises(ValueError):
        module.replay_bound_control(**data)


def test_frozen_due_opens_keep_lot_until_shared_first_usable_open():
    data = fixture(frozen_exit=True)
    result = module.replay_bound_control(**data)
    calendar = data["bound"].holding_inputs.calendar
    for arm in ("D1", "D2"):
        books = {row["as_of"]: row for row in result["replays"][arm]["books"]}
        assert len(books[calendar[187].isoformat()]["lots"]) == 1
        assert len(books[calendar[188].isoformat()]["lots"]) == 1
        assert books[calendar[189].isoformat()]["lots"] == []
        assert result["report"]["arms"][arm]["CONTROL"]["ordinary_closed_trades"] == 1
        code = data["bound"].selections_d1[0].eligible_codes[0]
        proof = next(row for row in result["conditional_action_scope"]["entries"] if row["arm"] == arm and row["code"] == code)
        assert proof["candidate_on"] == calendar[189].isoformat()


def test_frozen_entry_keeps_one_target_without_replacement_and_separate_lag_fills():
    result = module.replay_bound_control(**fixture(frozen_entry=True))
    first, second = (result["report"]["arms"][arm]["CONTROL"] for arm in ("D1", "D2"))
    assert (first["filled_entries"], first["unfilled_entries"]) == (0, 1)
    assert (second["filled_entries"], second["unfilled_entries"]) == (1, 0)
    assert all(len(result["replays"][arm]["selections"][0][1]) == 1 for arm in ("D1", "D2"))


def test_cash_only_original_ceiling_runs_empty_control_books_on_same_reviewed_nominee_scope():
    data = fixture(cash_only=True)
    result = module.replay_bound_control(**data)
    assert len(result["conditional_action_scope"]["ordinary_codes"]) == 6
    for arm, row in result["report"]["arms"].items():
        assert row["target_overlap"]["rate"] is None
        for policy in ("ACTIVITY", "CONTROL"):
            assert row[policy]["filled_entries"] == row[policy]["terminal_open_lots"] == 0
            assert row[policy]["exposure"]["active_sessions"] == 0
            assert Decimal(row[policy]["cumulative_return"]) == 0
        assert result["replays"][arm]["selections"][0][1] == []


def test_pure_replay_performs_no_files_database_network_or_process_io(monkeypatch):
    data = fixture()
    def denied(*args, **kwargs):
        pytest.fail("pure CONTROL seam attempted IO")
    with monkeypatch.context() as patch:
        for owner, name in ((builtins, "open"), (Path, "open"), (Path, "read_bytes"), (Path, "read_text"),
                            (socket, "create_connection"), (sqlite3, "connect"), (subprocess, "run")):
            patch.setattr(owner, name, denied)
        result = module.replay_bound_control(**data)
    assert result["status"] == "pure_descriptive_control_replay"
