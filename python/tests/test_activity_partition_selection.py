"""Synthetic BL-schema contract checks; no market/source/DB/log IO."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json

import pytest

from research.activity_normal_assembly import assemble_window
from research.activity_partition_selection import PartitionSelection, select_partition, selections_from_bl
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy
from test_activity_normal_assembly import fixture as normal_fixture


def package():
    """Small real producer-shaped package spanning every disposition and empty signals."""
    inputs = normal_fixture(frozen=0)
    calendar = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(130))
    inputs["calendar"] = calendar
    params = {"lookback": 60, "threshold": Decimal("3.0"), "holding_sessions": 30,
              "end": calendar[-1].isoformat()}
    inputs["params"] = params
    good = assemble_window(**inputs)
    bad_inputs = deepcopy(inputs)
    bad_inputs["code"] = "000006"
    bad_inputs["isin"] = "KR7000006000"
    accepted = bad_inputs["accepted_record"]
    accepted["code"] = "000006"
    window = accepted["required_windows"][0]
    window["window_id"] = window["window_id"].replace("000001", "000006")
    window["isin"] = bad_inputs["isin"]
    window["krx_metadata_provenance"]["code"] = "000006"
    bad_inputs["liquidity"] = {day: replace(row, turnover=Decimal("5000000000"))
                               for day, row in bad_inputs["liquidity"].items()}
    bad = assemble_window(**bad_inputs)
    normal = [good, bad]
    indices = SessionLagPolicy(1, paired=True).formation_indices(
        calendar, lookback=60, holding_sessions=30, end_index=129)
    schedule = [{"formation": calendar[index].strftime("%Y%m%d"), "arms": [
        {"session_lag": lag, "execution_on": calendar[index + lag].isoformat(),
         "selection_at": SessionLagPolicy(lag).selection_at(calendar, index).isoformat(),
         "due_on": calendar[index + lag + 30].isoformat(), "status": "input_coverage_only_not_executed"}
        for lag in (1, 2)]} for index in indices]
    metadata = {}
    for number, index in enumerate(indices):
        value = AvailabilityMetadata(calendar[index], SessionLagPolicy(1).selection_at(calendar, index),
            "unknown", "synthetic official source", None, None, "assumed",
            availability_policy="synthetic D1", evidence_reference="synthetic source")
        metadata[f"m{number}"] = json.loads(json.dumps(asdict(value), default=lambda item: item.isoformat()))
    rows = []
    for number, disposition in enumerate(("normal_window", "cap_below", "negative_type", "bh_failure",
                                         "bi_failure", "normal_window"), 1):
        index = indices[0]
        formation, code = calendar[index].strftime("%Y%m%d"), f"{number:06d}"
        proof = {"reason": "numeric_pass_unresolved"}
        if disposition == "cap_below":
            proof = {"capitalization": "4999999999999"}
        elif disposition == "negative_type":
            proof = {"literal_type": "preferred_share"}
        elif disposition == "bh_failure":
            proof = {"formation": formation, "code": code,
                     "proven_numeric_failures": ["BB_verified_observed_history_less_than_60"],
                     "potentially_influential_unresolved": False}
        elif disposition == "bi_failure":
            proof = {"formation": formation, "code": code, "proven_failure": True,
                     "reason": "activity_below_threshold", "unresolved": [],
                     "historical_eligibility_certified": False,
                     "consumed_dates": [day.isoformat() for day in calendar[:index + 1]],
                     "normal_dates": [day.isoformat() for day in calendar[:index]], "frozen_dates": [],
                     "baseline": "10", "formation_turnover": "29", "required_turnover": "30.0",
                     "selection_at": {f"D{lag}": SessionLagPolicy(lag).selection_at(calendar, index).isoformat()
                                      for lag in (1, 2)}}
        provenance = deepcopy(inputs["accepted_record"]["required_windows"][0]["krx_metadata_provenance"])
        provenance["code"] = code
        rows.append({"formation": formation, "code": code, "isin": f"KR7{code}000",
            "listing_date": inputs["listing_date"].isoformat(),
            "capitalization": "4999999999999" if disposition == "cap_below" else "5000000000000",
            "cap_pass": disposition != "cap_below", "negative_type": "preferred_share" if disposition == "negative_type" else None,
            "literal_source_labels": {"market": "KOSPI", "security_group": "주권",
                "share_class": "신형우선주" if disposition == "negative_type" else "보통주",
                "basic_section": "", "trade_section": ""},
            "krx_metadata_provenance": provenance,
            "capitalization_availability": "m0", "classification_availability": "m0",
            "trade_section_availability": "m0", "disposition": disposition, "proof": proof})
    # The second complete formation has only a cap failure and no normal signal.
    rows.append({**deepcopy(rows[1]), "formation": calendar[indices[1]].strftime("%Y%m%d"),
                 "capitalization_availability": "m1", "classification_availability": "m1",
                 "trade_section_availability": "m1"})
    rows[-1]["krx_metadata_provenance"]["formation"] = rows[-1]["formation"]
    population = {"schema": "activity-normal-population-audit-v1", "rows": rows,
                  "availability_metadata": metadata}
    control = {key: value for key, value in rows[2].items() if key not in {"proof", "disposition"}}
    for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
        control[field] = metadata[control[field]]
    signals = [{"formation": good["formation"], "code": good["code"], "isin": good["isin"]}]
    result = {"schema": "activity-normal-input-assembly-v1", "status": "normal_inputs_assembled",
        "returns_computed": False, "holding_selection_executed": False, "promotion_allowed": False,
        "source_manifest_sha256": "a" * 64, "specification_sha256": "b" * 64,
        "audit_sha256": {name: "c" * 64 for name in (
            "population-audit.json", "normal-input-audit.json", "restored-activity-audit.json",
            "read-scope.json", "raw-source-read-scope.json")},
        "paired_schedule": schedule, "negative_type_controls": [control],
        "formation_totals": {"trade_rows": 7, "cap_pass_rows": 5, "cap_below_rows": 2,
                             "cap_pass_common_label_rows": 4},
        "arms": {arm: {"screen_windows": 2, "signal_windows": 1, "signals": deepcopy(signals),
                       "performance_completed": False} for arm in ("D1", "D2")},
        "summary": {"source_rows": 7, "disposition_counts": {"cap_below": 2, "negative_type": 1,
            "bh_failure": 1, "bi_failure": 1, "normal_window": 2}, "replayed_BI_windows": 3,
            "normal_windows": 2, "normal_codes": 2, "signal_windows": {"D1": 1, "D2": 1},
            "paired_signal_symmetric_difference": 0, "paired_signal_union": 1, "unresolved_input_windows": 0}}
    return result, population, normal, calendar, params


@pytest.mark.parametrize("arm,lag", [("D1", 1), ("D2", 2)])
def test_actual_producer_schema_retains_full_pool_and_empty_formations(arm, lag):
    """Preserve full synthetic producer partitions and explicit empty signal formations."""
    result, population, normal, calendar, params = package()
    selections = selections_from_bl(result, population, normal, calendar, params, arm)
    assert len(selections) == 2
    first, empty = selections
    assert first.formation_on == calendar[60] and first.decision_on == calendar[60 + lag]
    assert first.selection_at == SessionLagPolicy(lag).selection_at(calendar, 60)
    assert first.eligible_codes == ("000001",) and first.source_population_count == 6
    assert empty.eligible_codes == () and empty.source_population_count == 1
    assert first.source_partition_sha256 != empty.source_partition_sha256
    assert result["returns_computed"] is False
    with pytest.raises(FrozenInstanceError):
        first.eligible_codes = ()


def test_fingerprint_covers_excluded_rows_and_every_referenced_field():
    """Include excluded source rows and referenced provenance in partition fingerprints."""
    args = package()
    original = selections_from_bl(*args, "D1")
    args[1]["rows"][1]["dated_aliases"] = {"trade_name": "synthetic historical alias"}
    changed = selections_from_bl(*args, "D1")
    assert original[0].eligible_codes == changed[0].eligible_codes
    assert original[0].source_partition_sha256 != changed[0].source_partition_sha256
    assert original[1].source_partition_sha256 == changed[1].source_partition_sha256
    # A provenance-only change stays part of the complete source fingerprint.
    args[1]["availability_metadata"]["m1"]["source"] = "other synthetic official provenance"
    assert changed[1].source_partition_sha256 != selections_from_bl(*args, "D1")[1].source_partition_sha256


@pytest.mark.parametrize("corruption", [
    "missing_source", "duplicate_source", "duplicate_isin", "missing_formation", "cap_lie", "false_cap_proof",
    "false_bh_proof", "unknown_bh_reason", "false_bi_proof", "bi_time", "false_normal_proof",
    "missing_normal", "duplicate_normal", "extra_normal", "isin", "accepted_isin", "source_provenance",
    "selected_lie", "exclusion_lie", "candidate_identity", "candidate_known_late", "opposite_candidate",
    "opposite_signals", "duplicate_signal", "summary", "formation_totals", "negative_controls",
    "missing_reference", "unreferenced_metadata", "late_source", "late_activity", "late_liquidity",
    "late_cap", "public_late", "missing_field_metadata", "schedule", "lineage", "promotion",
])
def test_corrupt_partition_metadata_joins_or_either_arm_stop_before_selection(corruption):
    """Reject corrupted source joins, proofs, metadata or either signal arm."""
    result, population, normal, calendar, params = package()
    rows = population["rows"]
    if corruption == "missing_source": rows.pop(1)
    elif corruption == "duplicate_source": rows.append(deepcopy(rows[0]))
    elif corruption == "duplicate_isin": rows[1]["isin"] = rows[0]["isin"]
    elif corruption == "missing_formation": rows.pop()
    elif corruption == "cap_lie": rows[1]["cap_pass"] = True
    elif corruption == "false_cap_proof": rows[1]["proof"]["capitalization"] = "0"
    elif corruption == "false_bh_proof": rows[3]["proof"]["proven_numeric_failures"] = []
    elif corruption == "unknown_bh_reason": rows[3]["proof"]["proven_numeric_failures"] = ["invented"]
    elif corruption == "false_bi_proof": rows[4]["proof"]["proven_failure"] = False
    elif corruption == "bi_time": rows[4]["proof"]["selection_at"]["D2"] = rows[4]["proof"]["selection_at"]["D1"]
    elif corruption == "false_normal_proof": rows[0]["proof"]["reason"] = "activity_below_threshold"
    elif corruption == "missing_normal": normal.pop()
    elif corruption == "duplicate_normal": normal.append(deepcopy(normal[0]))
    elif corruption == "extra_normal": normal[0]["code"] = "999999"
    elif corruption == "isin": normal[0]["isin"] = "KR7999999999"
    elif corruption == "accepted_isin": normal[0]["accepted_evidence"]["required_windows"][0]["isin"] = "other"
    elif corruption == "source_provenance": rows[0]["krx_metadata_provenance"]["source_row_position"] += 1
    elif corruption == "selected_lie": normal[1]["arms"]["D1"]["selected"] = True
    elif corruption == "exclusion_lie": normal[1]["arms"]["D1"]["exclusion_reasons"] = []
    elif corruption == "candidate_identity": normal[0]["arms"]["D1"]["candidate"]["code"] = "other"
    elif corruption == "candidate_known_late": normal[0]["arms"]["D1"]["candidate"]["classification_known_on"] = calendar[61].isoformat()
    elif corruption == "opposite_candidate": normal[0]["arms"]["D2"]["candidate"]["activity_pass"] = False
    elif corruption == "opposite_signals": result["arms"]["D2"]["signals"] = []
    elif corruption == "duplicate_signal": result["arms"]["D1"]["signals"] *= 2
    elif corruption == "summary": result["summary"]["source_rows"] = 6
    elif corruption == "formation_totals": result["formation_totals"]["trade_rows"] = 6
    elif corruption == "negative_controls": result["negative_type_controls"] = []
    elif corruption == "missing_reference": rows[0]["classification_availability"] = "absent"
    elif corruption == "unreferenced_metadata": population["availability_metadata"]["other"] = deepcopy(population["availability_metadata"]["m0"])
    elif corruption == "late_source": population["availability_metadata"]["m0"]["available_at"] = SessionLagPolicy(2).selection_at(calendar, 60).isoformat()
    elif corruption in {"late_activity", "late_liquidity", "missing_field_metadata"}:
        field = "official_liquidity" if corruption == "late_liquidity" else "activity"
        observation = next(iter(normal[0]["inputs"][field].values()))
        if corruption == "missing_field_metadata": observation["availability"] = None
        else: observation["availability"]["available_at"] = SessionLagPolicy(2).selection_at(calendar, 60).isoformat()
    elif corruption == "late_cap": normal[0]["inputs"]["official_capitalization"]["availability"]["available_at"] = SessionLagPolicy(2).selection_at(calendar, 60).isoformat()
    elif corruption == "public_late": normal[0]["inputs"]["official_capitalization"]["public_available_at"] = SessionLagPolicy(2).selection_at(calendar, 60).isoformat()
    elif corruption == "schedule": result["paired_schedule"].pop()
    elif corruption == "lineage": result["audit_sha256"]["normal-input-audit.json"] = None
    else: result["promotion_allowed"] = True
    with pytest.raises(ValueError):
        selections_from_bl(result, population, normal, calendar, params, "D1")


@pytest.mark.parametrize("row_index", [0, 3, 4])
@pytest.mark.parametrize("field,value", [
    ("security_group", "주식예탁증권"), ("share_class", "신형우선주"),
    ("basic_section", "외국기업(소속부없음)"), ("trade_section", "외국기업(소속부없음)"),
    ("basic_section", None), ("trade_section", None),
])
def test_downstream_proof_cannot_override_nonordinary_source_labels(row_index, field, value):
    """Refuse downstream proofs that conflict with literal ordinary-share labels."""
    result, population, normal, calendar, params = package()
    population["rows"][row_index]["literal_source_labels"][field] = value
    if field == "share_class":
        result["formation_totals"]["cap_pass_common_label_rows"] -= 1
    with pytest.raises(ValueError, match="ordinary domestic source labels"):
        selections_from_bl(result, population, normal, calendar, params, "D1")


def test_hash_order_held_exclusion_slots_and_no_mutation():
    """Preserve original hash ordering, held-code exclusion and slot limits immutably."""
    calendar = package()[3]
    cutoff = SessionLagPolicy(1).selection_at(calendar, 60)
    selection = PartitionSelection(calendar[60], calendar[61], cutoff, ("A", "B", "HELD"), 100, "d" * 64)
    options = dict(formation_on=calendar[60], decision_on=calendar[61], held_codes={"HELD"},
                   seed=7, free_slots=1, selection_at=cutoff)
    expected = sorted(("A", "B"), key=lambda code: hashlib.sha256(f"7:{calendar[60]}:{code}".encode()).digest())
    assert select_partition(selection, **options) == tuple(expected[:1])
    assert select_partition(replace(selection, eligible_codes=tuple(reversed(selection.eligible_codes))), **options) == tuple(expected[:1])
    assert select_partition(selection, **{**options, "free_slots": 0}) == ()
    assert select_partition(selection, **{**options, "free_slots": 10}) == tuple(expected)
    assert selection.eligible_codes == ("A", "B", "HELD")
    assert options["held_codes"] == {"HELD"}


@pytest.mark.parametrize("field,value", [("formation_on", datetime(2020, 1, 1)),
    ("decision_on", date(2020, 3, 1)), ("selection_at", datetime(2020, 3, 2)),
    ("eligible_codes", ["A"]), ("eligible_codes", ("A", "A")), ("eligible_codes", (" A",)),
    ("source_population_count", 0), ("source_population_count", True), ("source_partition_sha256", "bad")])
def test_declaration_rejects_mutable_or_ambiguous_inputs(field, value):
    """Reject mutable eligible codes and ambiguous dates, counts or partition pins."""
    values = dict(formation_on=date(2020, 3, 1), decision_on=date(2020, 3, 2),
        selection_at=SessionLagPolicy(1).selection_at((date(2020, 3, 1), date(2020, 3, 2)), 0),
        eligible_codes=("A",), source_population_count=2, source_partition_sha256="d" * 64)
    with pytest.raises(ValueError):
        PartitionSelection(**{**values, field: value})


@pytest.mark.parametrize("field,value", [("formation_on", date(2020, 2, 1)),
    ("decision_on", date(2020, 3, 3)), ("selection_at", datetime(2020, 3, 2)),
    ("free_slots", -1), ("free_slots", True), ("seed", True), ("held_codes", ["A"])])
def test_selection_cannot_change_schedule_or_use_invalid_book_arguments(field, value):
    """Reject altered selection timing and invalid prior-book selection arguments."""
    cutoff = SessionLagPolicy(1).selection_at((date(2020, 3, 1), date(2020, 3, 2)), 0)
    selection = PartitionSelection(date(2020, 3, 1), date(2020, 3, 2), cutoff, ("A",), 2, "e" * 64)
    options = dict(formation_on=selection.formation_on, decision_on=selection.decision_on,
                   selection_at=cutoff, held_codes=set(), seed=7, free_slots=1)
    with pytest.raises(ValueError):
        select_partition(selection, **{**options, field: value})
