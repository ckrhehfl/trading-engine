"""Fabricated full-family declarations and source rows; no actual input IO."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_cohort_raw_plan as module
from research import activity_raw_quote_view as raw_view
from research.activity_cohort_selection import CONTROL_IDS, CohortSelection, rank_control_codes
from research.activity_control_selection import activity_targets_fingerprint, population_fingerprint
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import RAW_FIELDS, extract_raw_episode_targets
from research.activity_timing import SessionLagPolicy
import test_activity_cohort_comparison as synthetic


LG, LX = "003550", "383800"
IDENTITIES = {LG: "KR7003550001", LX: "KR7383800006"}


def case(*, formations=3, cash=False, lg_only=False):
    end_index = 60 + 126 * formations + 2
    calendar = tuple(date(2026, 9, 18) + timedelta(days=index - end_index) for index in range(end_index + 3))
    population = tuple(sorted(IDENTITIES.items()))
    declarations = []
    for arm, lag in (("D1", 1), ("D2", 2)):
        policy = SessionLagPolicy(lag, paired=True)
        for index in range(60, end_index - 127, 126):
            formation = calendar[index]
            decision, cutoff = policy.execution_on(calendar, index), policy.selection_at(calendar, index)
            targets = () if cash else (LG,)
            for group in ("ACTIVITY", *CONTROL_IDS):
                prefix = []
                if group != "ACTIVITY" and targets:
                    for code in rank_control_codes(tuple(IDENTITIES), control_id=group, formation_on=formation):
                        prefix.append((code, "excluded" if lg_only and code != LG else "eligible", "c" * 64))
                        if prefix[-1][1] == "eligible":
                            break
                declarations.append(CohortSelection(group, arm, formation, decision, cutoff, PARAMETERS["seed"], targets,
                    activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                        selection_at=cutoff, targets=targets, seed=PARAMETERS["seed"]),
                    population, len(population), population_fingerprint(population), "d" * 64, tuple(prefix)))
    event = dict(event_id="synthetic_LG_LX", kind="compulsory_spin_off", code=LG, isin=IDENTITIES[LG],
        successor_code=LX, successor_isin=IDENTITIES[LX], old_basis_on=str(calendar[90]),
        last_eligible_entry_on=str(calendar[90]), record_on=str(calendar[95]), effective_on=str(calendar[100]),
        retained_available_on=str(calendar[101]), new_available_on=str(calendar[102]), evidence_sha256="e" * 64)
    values = dict(declarations=tuple(declarations), calendar=calendar, raw_event_plan=(event,), parameters=deepcopy(PARAMETERS))
    values["input_sha256"] = dict(activity_snapshot_sha256="a" * 64, original_quote_snapshot_sha256="b" * 64,
        original_read_scope_sha256="c" * 64, base_quote_snapshot_sha256="f" * 64,
        calendar_sha256=raw_view.calendar_sha256(calendar),
        cohort_declarations_sha256=digest(encoded(_json_value(values["declarations"]))),
        raw_event_plan_sha256=digest(encoded(values["raw_event_plan"])))
    return values


def repin(values):
    values["input_sha256"]["cohort_declarations_sha256"] = digest(encoded(_json_value(values["declarations"])))
    values["input_sha256"]["raw_event_plan_sha256"] = digest(encoded(values["raw_event_plan"]))
    return values


def test_all_groups_arms_reentries_and_overlaps_remain_price_free_and_io_free(monkeypatch):
    values = case()
    before = deepcopy(values)
    def forbidden(*args, **kwargs):
        pytest.fail("pure full-family raw planning attempted IO or a study")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("research.activity_cohort_comparison.replay_synthetic", forbidden)
    result = module.build_cohort_raw_plan(**values)
    plan = result["acquisition_plan"]
    assert values == before
    assert len(values["declarations"]) == 36
    assert {row["group_id"] for row in plan["chosen_cohort_origins"]} == {"ACTIVITY", *CONTROL_IDS}
    assert {row["arm"] for row in plan["raw_potential_origins"]} == {"D1", "D2"}
    assert len({row["formation_on"] for row in plan["raw_potential_origins"] if row["code"] == LG}) == 3
    assert all(row["original_end_on"] == PARAMETERS["end"] for row in plan["potential_origins"])
    keys = {(row["arm"], row["formation_on"], row["code"], row["entry_on"]) for row in plan["potential_origins"]}
    assert len(keys) == len(plan["potential_origins"])
    assert len(plan["chosen_cohort_origins"]) > len(keys)  # shared groups remain declared, proof origins deduplicate only.
    assert all(value is False for key, value in plan.items() if key in module.FALSE_FLAGS)
    assert all(type(plan[key]) is int and plan[key] == 0 for key in module.ZERO_FIELDS)
    assert plan["original_end_fallback_preserved"] is True
    assert result["unit_plan"]["schema"] == raw_view.PLAN_SCHEMA
    assert all(result["unit_plan"][key] == value for key, value in raw_view.UNITS.items())
    assert set(result["unit_plan"]) == {"schema", "activity_snapshot_sha256", "base_quote_snapshot_sha256",
        "calendar_sha256", "episodes", *raw_view.UNITS}
    assert all(row["service"] == module.SERVICE for row in plan["request_matrix"])
    assert plan["cached_response_candidates"] == []
    assert plan["uncached_request_candidates"] == plan["request_matrix"]


def test_minimum_and_contiguous_envelope_are_distinct_without_adjusted_gap_fallback():
    values = case(lg_only=True)
    result = module.build_cohort_raw_plan(**values)
    plan, calendar = result["acquisition_plan"], values["calendar"]
    assert str(calendar[100]) not in plan["minimum_code_dates"][LG]
    assert str(calendar[100]) in plan["requested_code_dates"][LG]
    assert plan["minimum_code_dates"][LX][0] == str(calendar[102])
    assert plan["requested_code_dates"][LG][-1] == PARAMETERS["end"]  # later independent origins stay full.
    successor = next(row for row in plan["action_priority_windows"] if row["code"] == LX)
    assert successor["start"] == str(calendar[100]) < plan["requested_code_dates"][LX][0]
    assert plan["adjusted_price_fallback_allowed"] is False


def test_complete_fourteen_formation_family_keeps_all_168_declarations_and_paired_origins():
    values = case(formations=14)
    plan = module.build_cohort_raw_plan(**values)["acquisition_plan"]
    assert plan["counts"]["cohort_declarations"] == 168
    assert plan["counts"]["chosen_cohort_origins"] == 168
    assert len({row["formation_on"] for row in plan["potential_origins"]}) == 14
    assert sum(row["code"] == LG for row in plan["raw_potential_origins"]) == 28
    assert all(row["due_on"] <= PARAMETERS["end"] for row in plan["potential_origins"])
    assert plan["D1_completed_studies"] == plan["D2_completed_studies"] == 0


@pytest.mark.parametrize("old_basis,last_eligible", [(61, 62), (62, 61)])
def test_two_unsupported_entry_boundaries_keep_original_and_successor_obligations(old_basis, last_eligible):
    values = case(lg_only=True)
    calendar, event = values["calendar"], values["raw_event_plan"][0]
    event.update(old_basis_on=str(calendar[old_basis]), last_eligible_entry_on=str(calendar[last_eligible]))
    plan = module.build_cohort_raw_plan(**repin(values))["acquisition_plan"]
    unresolved = [row for row in plan["unresolved"]
        if row["reason"] == "unsupported_raw_origin_eligibility_preserves_original_horizon"]
    assert [(row["origin"]["arm"], row["origin"]["entry_on"]) for row in unresolved] == [("D2", str(calendar[62]))]
    assert str(calendar[100]) in plan["minimum_code_dates"][LG]
    assert plan["requested_code_dates"][LG][-1] == plan["requested_code_dates"][LX][-1] == PARAMETERS["end"]
    assert plan["entitlement_certified"] is False


def test_paired_union_preserves_a_code_when_only_one_arm_or_control_chose_it():
    values = case(lg_only=True)
    rows = []
    for row in values["declarations"]:
        if row.arm == "D2":
            row = replace(row, activity_targets=(), resolved_prefix=(), activity_targets_sha256=activity_targets_fingerprint(
                arm=row.arm, formation_on=row.formation_on, decision_on=row.decision_on,
                selection_at=row.selection_at, targets=(), seed=row.seed))
        rows.append(row)
    values["declarations"] = tuple(rows)
    plan = module.build_cohort_raw_plan(**repin(values))["acquisition_plan"]
    assert all(row["arm"] == "D1" for row in plan["chosen_cohort_origins"])
    assert {row["arm"] for row in plan["raw_potential_origins"]} == {"D1", "D2"}
    assert len(plan["raw_potential_origins"]) == 6


def test_individually_valid_later_population_cannot_reuse_a_code_with_a_different_isin():
    values = case(lg_only=True)
    later = sorted({row.formation_on for row in values["declarations"]})[1]
    population = ((LG, "KR7003550002"), (LX, IDENTITIES[LX]))
    values["declarations"] = tuple(replace(row, source_population=population,
        source_population_sha256=population_fingerprint(population)) if row.formation_on == later else row
        for row in values["declarations"])
    with pytest.raises(ValueError, match="unique cohort issue"):
        module.build_cohort_raw_plan(**repin(values))


@pytest.mark.parametrize("change", ["group", "arm", "formation", "declaration_pin", "event_pin", "calendar_pin",
    "calendar_order", "calendar_missing", "settlement", "original_base_same", "parameter", "event_kind", "event_code",
    "event_duplicate", "event_isin", "origin_isin", "role_order", "date_text", "extra_event_field", "extra_input_field"])
def test_incomplete_or_contradictory_family_metadata_fails_closed(change):
    values = case()
    if change == "group":
        values["declarations"] = tuple(row for row in values["declarations"] if row.group_id != "C05")
    elif change == "arm":
        values["declarations"] = tuple(row for row in values["declarations"] if row.arm != "D2")
    elif change == "formation":
        first = values["declarations"][0].formation_on
        values["declarations"] = tuple(row for row in values["declarations"] if row.formation_on != first)
    elif change in ("declaration_pin", "event_pin", "calendar_pin"):
        key = {"declaration_pin": "cohort_declarations_sha256", "event_pin": "raw_event_plan_sha256",
            "calendar_pin": "calendar_sha256"}[change]
        values["input_sha256"][key] = "0" * 64
    elif change == "calendar_order":
        values["calendar"] = tuple(reversed(values["calendar"]))
    elif change == "calendar_missing":
        values["calendar"] = values["calendar"][:80] + values["calendar"][81:]
    elif change == "settlement":
        values["calendar"] = values["calendar"][:-1]
        values["input_sha256"]["calendar_sha256"] = raw_view.calendar_sha256(values["calendar"])
    elif change == "original_base_same":
        values["input_sha256"]["base_quote_snapshot_sha256"] = values["input_sha256"]["original_quote_snapshot_sha256"]
    elif change == "parameter":
        values["parameters"]["holding_sessions"] -= 1
    elif change == "event_kind":
        values["raw_event_plan"][0]["kind"] = "optional_tender"
    elif change == "event_code":
        values["raw_event_plan"][0]["code"] = "000001"
    elif change == "event_duplicate":
        values["raw_event_plan"] *= 2
    elif change == "event_isin":
        values["raw_event_plan"][0]["isin"] = "KR7003550002"
    elif change == "origin_isin":
        rows = list(values["declarations"])
        row = deepcopy(rows[0])
        object.__setattr__(row, "source_population", ((LG, "KR7003550002"), (LX, IDENTITIES[LX])))
        rows[0] = row
        values["declarations"] = tuple(rows)
    elif change == "role_order":
        values["raw_event_plan"][0]["new_available_on"] = str(values["calendar"][99])
    elif change == "date_text":
        values["raw_event_plan"][0]["record_on"] = "20260801"
    elif change == "extra_event_field":
        values["raw_event_plan"][0]["actual_filled"] = True
    elif change == "extra_input_field":
        values["input_sha256"]["old_raw_artifact_sha256"] = "1" * 64
    if change not in ("declaration_pin", "event_pin"):
        repin(values)
    with pytest.raises(ValueError):
        module.build_cohort_raw_plan(**values)


def response(calendar, index=102):
    return dict(service=module.SERVICE, bas_dd=calendar[index].strftime("%Y%m%d"),
        response_path="/synthetic/cache/response.json", response_sha256="1" * 64,
        receipt_path="/synthetic/cache/receipt.json", receipt_sha256="2" * 64,
        receipt_coordinate="/responses/4", retrieved_at="2026-10-10T00:00:00+00:00")


def test_cache_candidates_require_explicit_body_and_original_receipt_identity_only():
    values = case()
    row = response(values["calendar"])
    prior = (row,)
    before = deepcopy(prior)
    result = module.build_cohort_raw_plan(**values, prior_responses=prior, prior_inventory_sha256=digest(encoded(prior)))
    plan = result["acquisition_plan"]
    assert prior == before
    assert plan["cached_response_candidates"] == [dict(**row, targets=[LG, LX])]
    assert all(item["bas_dd"] != row["bas_dd"] for item in plan["uncached_request_candidates"])
    assert plan["cache_receipts_verified"] is plan["raw_rows_verified"] is plan["raw_input_reuse_certified"] is False
    assert plan["new_source_requests"] == 0


@pytest.mark.parametrize("change", ["no_pin", "pin", "missing_body_pin", "missing_receipt_pin", "duplicate", "service",
    "retrieved_at", "path", "receipt_coordinate", "old_artifact", "extra_field"])
def test_invented_cache_geometry_or_old_raw_artifact_relabel_is_rejected(change):
    values = case()
    prior = (response(values["calendar"]),)
    if change == "missing_body_pin": del prior[0]["response_sha256"]
    elif change == "missing_receipt_pin": del prior[0]["receipt_sha256"]
    elif change == "duplicate": prior *= 2
    elif change == "service": prior[0]["service"] = "ksq_bydd_trd"
    elif change == "retrieved_at": prior[0]["retrieved_at"] = "2026-10-10T00:00:00"
    elif change == "path": prior[0]["response_path"] = "/synthetic/../secret.json"
    elif change == "receipt_coordinate": prior[0]["receipt_coordinate"] = ""
    elif change == "old_artifact": prior = (dict(old_raw_artifact_sha256="1" * 64),)
    elif change == "extra_field": prior[0]["base_quote_snapshot_sha256"] = "f" * 64
    pin = None if change == "no_pin" else "0" * 64 if change == "pin" else digest(encoded(prior))
    with pytest.raises(ValueError):
        module.build_cohort_raw_plan(**values, prior_responses=prior, prior_inventory_sha256=pin)


def test_no_raw_origins_has_no_query_or_invented_episode_and_missing_event_stays_unresolved():
    values = case(cash=True)
    result = module.build_cohort_raw_plan(**values)
    assert result["unit_plan"] is None
    assert result["acquisition_plan"]["request_matrix"] == []
    assert result["acquisition_plan"]["potential_origins"] == []
    values = case(lg_only=True)
    values["raw_event_plan"] = ()
    result = module.build_cohort_raw_plan(**repin(values))
    assert result["unit_plan"] is None
    assert set(result["acquisition_plan"]["requested_code_dates"]) == {LG}
    assert any(row["reason"] == "raw_event_metadata_not_supplied" for row in result["acquisition_plan"]["unresolved"])


def test_nonraw_origins_stay_in_full_family_but_create_no_raw_query_or_empty_episode():
    values = case()
    codes = {LG: "000001", LX: "000002"}
    population = tuple((codes[code], isin) for code, isin in sorted(IDENTITIES.items()))
    rows = []
    for row in values["declarations"]:
        targets = tuple(codes[code] for code in row.activity_targets)
        # Recompute the fixed ranks for the renamed synthetic population.
        prefix = () if row.group_id == "ACTIVITY" else tuple((code, "eligible", "c" * 64)
            for code in rank_control_codes(tuple(code for code, _ in population), control_id=row.group_id,
                formation_on=row.formation_on)[:len(targets)])
        rows.append(replace(row, activity_targets=targets,
            activity_targets_sha256=activity_targets_fingerprint(arm=row.arm, formation_on=row.formation_on,
                decision_on=row.decision_on, selection_at=row.selection_at, targets=targets, seed=row.seed),
            source_population=population, source_population_sha256=population_fingerprint(population), resolved_prefix=prefix))
    values["declarations"], values["raw_event_plan"] = tuple(rows), ()
    result = module.build_cohort_raw_plan(**repin(values))
    assert result["unit_plan"] is None
    assert result["acquisition_plan"]["potential_origins"]
    assert result["acquisition_plan"]["raw_potential_origins"] == []
    assert result["acquisition_plan"]["request_matrix"] == []


def test_outside_calendar_basis_obligation_remains_unresolved():
    values = case(lg_only=True)
    values["raw_event_plan"][0]["old_basis_on"] = str(values["calendar"][0] - timedelta(days=1))
    result = module.build_cohort_raw_plan(**repin(values))
    assert any(row["reason"] == "raw_basis_anchor_outside_supplied_calendar" for row in result["acquisition_plan"]["unresolved"])
    assert result["acquisition_plan"]["unit_plan_ready"] is False


@pytest.mark.parametrize("state", ["positive", "zero", "absent", "impossible"])
def test_existing_decoder_and_raw_view_accept_new_plan_preserving_source_and_old_artifacts(state):
    values = case(formations=1, lg_only=True)
    calendar = values["calendar"]
    original = synthetic.quotes(calendar, scope={LG: calendar[61:-2]}, changes={(LG, calendar[80]): None})
    extension = synthetic.quotes(calendar, scope={LX: calendar[102:-2]}, scope_pin="9" * 64)
    base = extend_holding_quotes(original, extension)
    values["input_sha256"].update(activity_snapshot_sha256=base.activity_snapshot_sha256,
        original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256, base_quote_snapshot_sha256=base.quote_snapshot_sha256)
    result = module.build_cohort_raw_plan(**values)
    plan = result["unit_plan"]
    original_bytes, extension_bytes = original.fingerprint_json, extension.fingerprint_json
    observations = []
    chosen = LG, calendar[80]
    for episode in plan["episodes"]:
        code = episode["code"]
        for day in calendar:
            if not episode["start"] <= str(day) <= episode["end"]:
                continue
            source = dict(ISU_CD=code, BAS_DD=day.strftime("%Y%m%d"), TDD_OPNPRC="+007.00", TDD_HGPRC="9",
                TDD_LWPRC="6", TDD_CLSPRC="8", ACC_TRDVOL="9007199254740993", ACC_TRDVAL="12345678901234567890.125")
            if (code, day) == chosen:
                if state == "zero": source.update(dict.fromkeys(RAW_FIELDS, "0"))
                elif state == "impossible": source["TDD_HGPRC"] = "1"
            observations.extend(extract_raw_episode_targets([] if state == "absent" and (code, day) == chosen else [source],
                dict(service=module.SERVICE, bas_dd=day.strftime("%Y%m%d"), targets=[code]),
                "2026-10-10T00:00:00+00:00", "7" * 64))
    observations = tuple(observations)
    unit_bytes = encoded(plan)
    artifact = raw_view.encode_raw_artifact(base, observations, digest(unit_bytes))
    view = raw_view.build_raw_quote_view(base, observations, source_artifact=artifact, source_artifact_sha256=digest(artifact),
        unit_plan=unit_bytes, unit_plan_sha256=digest(unit_bytes))
    assert raw_view.restore_raw_quote_view(base, artifact, digest(artifact), unit_bytes, digest(unit_bytes)) == view
    assert original.fingerprint_json == original_bytes and extension.fingerprint_json == extension_bytes
    assert chosen in original.absent_coordinates and chosen in view.base_absent_coordinates
    row = next(row for row in observations if (row.code, row.requested_bas_dd) == (chosen[0], chosen[1].strftime("%Y%m%d")))
    assert row.response_sha256 == "7" * 64 and row.row_position == (None if state == "absent" else 0)
    assert row.retrieved_at == "2026-10-10T00:00:00+00:00"
    if state == "zero":
        assert chosen in view.no_trade_coordinates and chosen not in view.absent_coordinates
    elif state == "absent":
        assert chosen in view.absent_coordinates and chosen not in view.no_trade_coordinates
    elif state == "impossible":
        assert chosen in view.unresolved_coordinates
    else:
        assert view.panel[LG].opens[80] == Decimal("7.00")
        assert dict(row.original_texts)["TDD_OPNPRC"] == "+007.00"
    changed = dict(plan, base_quote_snapshot_sha256="8" * 64)
    changed_bytes = encoded(changed)
    with pytest.raises(ValueError, match="unit/base/calendar"):
        raw_view.build_raw_quote_view(base, observations, source_artifact=artifact, source_artifact_sha256=digest(artifact),
            unit_plan=changed_bytes, unit_plan_sha256=digest(changed_bytes))
    wrong_units = dict(plan, price_unit="KRW_per_adjusted_share")
    wrong_bytes = encoded(wrong_units)
    with pytest.raises(ValueError, match="unit/base/calendar"):
        raw_view.build_raw_quote_view(base, observations, source_artifact=artifact, source_artifact_sha256=digest(artifact),
            unit_plan=wrong_bytes, unit_plan_sha256=digest(wrong_bytes))
    assert strict_json(artifact)["base_quote_snapshot_sha256"] == base.quote_snapshot_sha256
