"""Conditional BS connections use fabricated typed quotes and reviewed declarations."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_cohort_comparison as comparison
from research import activity_cohort_connection as module
from research.activity_cohort_selection import (
    CohortSelection, CONTROL_IDS, activity_targets_fingerprint, population_fingerprint, rank_control_codes,
)
from research.activity_component_bounds import component_exit_bounds
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_pair_calibration import PARAMETERS
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_extension import extend_holding_quotes
from research import activity_raw_quote_view as raw_view
from research.activity_raw_episode import extract_raw_episode_targets
from research.activity_reviewed_actions import reviewed_action_terms
from research.activity_timing import SessionLagPolicy
from test_activity_cohort_return_policy import synthetic_policy, synthetic_policy_v2, synthetic_policy_v3
import test_activity_cohort_comparison as fixture


PINS = {"synthetic_original": "1" * 64}
LG, LX = "003550", "383800"


def isin(code):
    return "KR7" + code + "000"


def complete_coverage(coverage, evidence, windows, scope_sha):
    coverage, evidence = deepcopy(coverage), deepcopy(evidence)
    coverage.update(required_scope_sha256=scope_sha, windows=[])
    for index, window in enumerate(windows):
        ids = [row["event_id"] for row in coverage["events"] if row["code"] == window["code"]
            and row.get("last_eligible_entry_on", row.get("last_trading_on", row.get("effective_on"))) <= window["end"]
            and window["start"] <= row.get("effective_on", row.get("paid_on"))]
        ref = "complete-" + str(index)
        evidence["references"][ref] = dict(window, raw_sha256="5" * 64, review_sha256="6" * 64,
            coordinate="synthetic:interval", reviewed_on="2099-01-01",
            finding="inferred_supported_events_complete" if ids else "inferred_no_compulsory_action", event_ids=ids)
        coverage["windows"].append(dict(window, review_state="reviewed_supported_events" if ids else "reviewed_no_event",
            event_ids=ids, evidence_refs=[ref]))
    return coverage, evidence


def plan_inputs(declarations, original):
    selections = module.cohort_proof_selections(declarations, original.calendar, PARAMETERS)
    required = {}
    end = original.calendar.index(date.fromisoformat(PARAMETERS["end"]))
    for rows in selections.values():
        for row in rows:
            for code in row.eligible_codes:
                required.setdefault(code, set()).update(original.calendar[original.calendar.index(row.decision_on):end + 1])
    required = {code: [str(day) for day in sorted(days)] for code, days in sorted(required.items())}
    identities = {code: dict(row.source_population)[code] for row in declarations for code in row.chosen_codes}
    return selections, required, identities


def package(*, ks=(1, 1), component=False, changes=None):
    declarations, seed_quotes = fixture.case(ks)
    population = tuple((code, isin(code)) for code in fixture.CODES)
    declarations = tuple(replace(row, source_population=population,
        source_population_sha256=population_fingerprint(population)) for row in declarations)
    selections, required, identities = plan_inputs(declarations, seed_quotes)
    original = fixture.quotes(seed_quotes.calendar, scope={code: tuple(date.fromisoformat(day) for day in days)
        for code, days in required.items()}, changes=changes)
    plan = provisional_exit_bounds(selections["D1"], selections["D2"], original, PARAMETERS,
        required, identities, input_sha256=PINS)
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete",
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=original.quote_snapshot_sha256,
        required_scope_sha256=original.read_scope_sha256, calendar_sha256=plan["calendar_sha256"],
        windows=[], events=[], noops=[], unresolved=[])
    evidence = dict(references={}, bases={})
    proof = None
    if component:
        windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
            for row in plan["entries"])
        terms = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=original.calendar,
            activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=original.quote_snapshot_sha256)
        proof = component_exit_bounds(plan, selections["D1"], selections["D2"], original, PARAMETERS,
            required, identities, input_sha256=PINS, quotes=original, terms=terms)
    coverage, evidence = complete_coverage(coverage, evidence, proof["windows"] if proof else plan["child_windows"],
        proof["component_scope_sha256"] if proof else plan["child_scope_sha256"])
    return arguments(declarations, original, original, plan, proof, coverage, evidence, required, identities)


def arguments(declarations, original, quotes, plan, proof, coverage, evidence, required, identities):
    payloads = dict(ordinary_exit_scope=encoded(plan), reviewed_coverage=encoded(coverage), basis_evidence=encoded(evidence))
    pins = {"ordinary_exit_scope": digest(payloads["ordinary_exit_scope"]),
        "reviewed_action_coverage": digest(payloads["reviewed_coverage"]), "basis_evidence": digest(payloads["basis_evidence"])}
    if proof is not None:
        payloads["component_exit_scope"] = encoded(proof)
        pins["component_exit_scope"] = digest(payloads["component_exit_scope"])
    return dict(declarations=declarations, original=original, quotes=quotes, **payloads,
        parameters=deepcopy(PARAMETERS), original_required_code_dates=required, issue_isins=identities,
        input_sha256=deepcopy(PINS), expected_pins=pins)


def replay(values, binding, *, keys=None):
    quotes = values["quotes"]
    raw = quotes if type(quotes) is raw_view.RawEpisodeQuoteInputs else None
    return comparison.replay_equal_capital_cohorts(values["declarations"], raw.base if raw else quotes,
        binding.events, parameters=deepcopy(PARAMETERS), raw_quote_view=raw,
        cohort_keys=keys, scope_binding=binding)


@pytest.mark.parametrize("component", [False, True])
def test_union_binding_keeps_full_quotes_and_same_independent_books_without_io(monkeypatch, component):
    values = package(component=component)
    binding = module.prepare_cohort_scope(**values)
    before = deepcopy(values)
    ordinary = comparison.replay_equal_capital_cohorts(values["declarations"], values["quotes"], (), parameters=PARAMETERS)
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail("unexpected conditional IO"))
    result = replay(values, binding)
    assert values == before and binding.original.requested_coordinates == values["original"].requested_coordinates
    assert [row["replay"] for row in result["cohorts"]] == [row["replay"] for row in ordinary["cohorts"]]
    assert result["comparisons"] == ordinary["comparisons"]
    assert result["conditional_exit_bounds_consumed"] is True
    assert result["conditional_scope_binding_sha256"] == binding.binding_sha256
    assert result["actual_input_reuse_readiness_established"] is False


def test_paired_proof_union_does_not_change_a_lag_specific_target_ceiling():
    values = package(ks=(1, 0))
    rows = []
    for row in values["declarations"]:
        if row.arm == "D2":
            row = replace(row, activity_targets=(), resolved_prefix=(), activity_targets_sha256=activity_targets_fingerprint(
                arm=row.arm, formation_on=row.formation_on, decision_on=row.decision_on,
                selection_at=row.selection_at, targets=(), seed=row.seed))
        rows.append(row)
    declarations = tuple(rows)
    # The earlier family already supplied the same conservative paired union.
    values["declarations"] = declarations
    binding = module.prepare_cohort_scope(**values)
    selections = module.cohort_proof_selections(declarations, values["original"].calendar, PARAMETERS)
    assert all(left.eligible_codes == right.eligible_codes for left, right in zip(selections["D1"], selections["D2"]))
    key = ("D2", "ACTIVITY", declarations[0].formation_on)
    result = replay(values, binding, keys=(key,))
    assert result["cohorts"][0]["target_ceiling"] == result["cohorts"][0]["assigned_capital"] == 0
    assert all(not book.lots for book in result["cohorts"][0]["replay"].books)


@pytest.mark.parametrize("change", ["group", "target_union", "quote", "pin", "coverage", "component", "bound", "fingerprint"])
def test_invalid_or_stale_binding_never_reaches_first_book(monkeypatch, change):
    values = package(component=True)
    binding = module.prepare_cohort_scope(**values)
    if change == "group":
        row = deepcopy(values["declarations"][-1])
        object.__setattr__(row, "group_id", "C01")
        values["declarations"] = (*values["declarations"][:-1], row)
    elif change == "target_union":
        altered = deepcopy(binding)
        object.__setattr__(altered, "issue_isins_json", encoded({}))
        binding = altered
    elif change == "quote":
        values["quotes"] = replace(values["quotes"], quote_snapshot_sha256="e" * 64)
    elif change in ("pin", "coverage", "component", "bound", "fingerprint"):
        altered = deepcopy(binding)
        field, value = {"pin": ("expected_pins_json", encoded({})), "coverage": ("coverage_json", encoded({})),
            "component": ("component_proof_json", encoded({})), "bound": ("origin_bounds", ()),
            "fingerprint": ("binding_sha256", "e" * 64)}[change]
        object.__setattr__(altered, field, value)
        binding = altered
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("invalid binding reached replay"))
    with pytest.raises(ValueError):
        replay(values, binding)


def raw_package(*, end=188, later_code=None):
    _, seed_quotes = fixture.case((1, 0))
    calendar = seed_quotes.calendar
    population = tuple((code, isin(code)) for code in ((LG, LX) if later_code == LX else (LG,)))
    declarations = []
    for arm, lag in (("D1", 1), ("D2", 2)):
        policy = SessionLagPolicy(lag, paired=True)
        for index, targets in ((60, (LG,)), (186, () if later_code is None else (later_code,))):
            formation, decision, cutoff = calendar[index], policy.execution_on(calendar, index), policy.selection_at(calendar, index)
            for group in ("ACTIVITY", *CONTROL_IDS):
                prefix = []
                if group != "ACTIVITY" and targets:
                    for code in rank_control_codes(tuple(code for code, _ in population), control_id=group,
                            formation_on=formation):
                        prefix.append((code, "eligible" if code in targets else "excluded", "d" * 64))
                        if code in targets:
                            break
                declarations.append(CohortSelection(group, arm, formation, decision, cutoff, PARAMETERS["seed"], targets,
                    activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                        selection_at=cutoff, targets=targets, seed=PARAMETERS["seed"]),
                    population, len(population), population_fingerprint(population), fixture.PARTITION, tuple(prefix)))
    declarations = tuple(declarations)
    scope = {LG: calendar[61:315]}
    if later_code == LX:
        scope[LX] = calendar[187:315]
    original = fixture.quotes(calendar, scope=scope)
    selections, required, identities = plan_inputs(declarations, original)
    plan = provisional_exit_bounds(selections["D1"], selections["D2"], original, PARAMETERS,
        required, identities, input_sha256=PINS)
    extension = fixture.quotes(calendar, scope={LX: calendar[102:187] if later_code == LX else calendar[102:315]},
        scope_pin="f" * 64)
    base = extend_holding_quotes(original, extension)
    unit = dict(schema=raw_view.PLAN_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, calendar_sha256=raw_view.calendar_sha256(calendar),
        episodes=[dict(code=LG, start=str(calendar[61]), end=str(calendar[end])),
            dict(code=LX, start=str(calendar[102]), end=str(calendar[end]))], **raw_view.UNITS)
    observations = []
    for episode in unit["episodes"]:
        for day in calendar:
            if episode["start"] <= str(day) <= episode["end"]:
                source = dict(ISU_CD=episode["code"], BAS_DD=day.strftime("%Y%m%d"), TDD_OPNPRC="100", TDD_HGPRC="101",
                    TDD_LWPRC="99", TDD_CLSPRC="100", ACC_TRDVOL="100", ACC_TRDVAL="10")
                observations.extend(extract_raw_episode_targets([] if day == calendar[100] else [source],
                    dict(service="stk_bydd_trd", bas_dd=day.strftime("%Y%m%d"), targets=[episode["code"]]),
                    "2026-10-16T00:00:00+00:00", "d" * 64))
    unit_bytes = encoded(unit)
    artifact = raw_view.encode_raw_artifact(base, tuple(observations), digest(unit_bytes))
    quotes = raw_view.build_raw_quote_view(base, tuple(observations), source_artifact=artifact,
        source_artifact_sha256=digest(artifact), unit_plan=unit_bytes, unit_plan_sha256=digest(unit_bytes))
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete",
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        required_scope_sha256=original.read_scope_sha256, calendar_sha256=plan["calendar_sha256"], windows=[], noops=[], unresolved=[],
        events=[dict(event_id="split", kind="compulsory_spin_off", status="verified_effective", code=LG, isin=isin(LG),
            successor_code=LX, successor_isin=isin(LX), evidence_ref="event", old_basis_id="old", retained_basis_id="retained",
            new_basis_id="new", retained_ratio="0.6", new_ratio="0.4", retained_carry_weight="0.7",
            carry_evidence_ref="carry", carry_evidence_level="assumed", last_eligible_entry_on=str(calendar[98]),
            record_on=str(calendar[99]), effective_on=str(calendar[100]), retained_available_on=str(calendar[101]),
            new_available_on=str(calendar[102]))])
    evidence = dict(references={}, bases={})
    def reference(name, code, start, stop, finding, ids=()):
        evidence["references"][name] = dict(code=code, isin=isin(code), start=str(calendar[start]), end=str(calendar[stop]),
            raw_sha256="c" * 64, review_sha256="d" * 64, coordinate="synthetic:section", reviewed_on="2099-01-01",
            finding=finding, event_ids=list(ids))
    reference("event", LG, 98, 100, "verified_compulsory_spin_off", ("split",))
    reference("carry", LG, 100, 100, "registered_spin_off_carry_assumed", ("split",))
    for name, code, index, role in (("old", LG, 99, "last_observable_pre_event"),
            ("retained", LG, 101, "retained_availability"), ("new", LX, 102, "successor_availability")):
        evidence["bases"][name] = dict(code=code, isin=isin(code), session=str(calendar[index]), role=role,
            raw_close="100", adjusted_close="100", quote_snapshot_sha256=quotes.quote_snapshot_sha256, evidence_ref=name)
        reference(name, code, index, index, "verified_" + role + "_basis")
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
        for row in plan["entries"])
    terms = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=calendar,
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256)
    proof = component_exit_bounds(plan, selections["D1"], selections["D2"], original, PARAMETERS,
        required, identities, input_sha256=PINS, quotes=quotes, terms=terms)
    coverage, evidence = complete_coverage(coverage, evidence, proof["windows"], proof["component_scope_sha256"])
    return arguments(declarations, original, quotes, plan, proof, coverage, evidence, required, identities)


def test_bounded_raw_components_replay_all_shared_cohort_origins_without_extending_raw_quotes():
    values = raw_package()
    binding = module.prepare_cohort_scope(**values)
    raw = values["quotes"]
    with pytest.raises(ValueError, match="complete chosen and possible descendant"):
        comparison.replay_equal_capital_cohorts(values["declarations"], raw.base, binding.events,
            parameters=PARAMETERS, raw_quote_view=raw)
    result = replay(values, binding)
    assert len(result["cohorts"]) == 24 and result["conditional_exit_bounds_consumed"]
    assert all(row["terminal_open_lots"] == 0 for row in result["cohorts"])
    assert all(row["filled_entries"] == row["target_ceiling"] for row in result["cohorts"])
    assert binding.original.requested_coordinates == values["original"].requested_coordinates
    assert max(day for _, day in raw.requested_coordinates) == raw.calendar[188]


def test_missing_raw_arm_candidate_keeps_full_obligation_and_refuses_without_a_fallback(monkeypatch):
    values = raw_package(end=187)
    before = values["quotes"]
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("raw gap reached replay"))
    with pytest.raises(ValueError, match="potential raw scope"):
        module.prepare_cohort_scope(**values)
    assert values["quotes"] == before


@pytest.mark.parametrize("code", [LG, LX])
def test_later_independent_raw_origin_cannot_reuse_an_earlier_spin_bound(monkeypatch, code):
    values = raw_package(later_code=code)
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("later raw origin was dropped"))
    with pytest.raises(ValueError, match="potential raw scope"):
        module.prepare_cohort_scope(**values)


@pytest.mark.parametrize("code", [LG, LX])
def test_expanded_global_raw_quotes_keep_later_independent_origin_and_original_budget(code):
    values = raw_package(end=314, later_code=code)
    binding = module.prepare_cohort_scope(**values)
    formation = values["original"].calendar[186]
    result = replay(values, binding, keys=(("D1", "C01", formation), ("D2", "C01", formation)))
    for row in result["cohorts"]:
        assert row["chosen_codes"] == (code,) and row["filled_entries"] == 1
        assert row["assigned_capital"] == Decimal("0.05") and row["terminal_open_lots"] == 0


@pytest.mark.parametrize("change", ["ordinary", "component", "coverage", "full_requests"])
def test_repinned_changed_proofs_and_incomplete_coverage_are_recomputed(monkeypatch, change):
    values = package(component=True)
    if change == "full_requests":
        code = next(iter(values["original_required_code_dates"]))
        values["original_required_code_dates"][code].pop(0)
    else:
        field, role = {"ordinary": ("ordinary_exit_scope", "ordinary_exit_scope"),
            "component": ("component_exit_scope", "component_exit_scope"),
            "coverage": ("reviewed_coverage", "reviewed_action_coverage")}[change]
        value = strict_json(values[field])
        if change == "ordinary":
            value["entries"][0]["end_on"] = value["entries"][0]["entry_on"]
            value["child_scope_sha256"] = digest(encoded({key: item for key, item in value.items() if key != "child_scope_sha256"}))
        elif change == "component":
            value["entries"][0]["end_on"] = value["entries"][0]["origin"]["entry_on"]
            value["component_scope_sha256"] = digest(encoded({key: item for key, item in value.items() if key != "component_scope_sha256"}))
        else:
            value["windows"].pop()
        values[field] = encoded(value)
        values["expected_pins"][role] = digest(values[field])
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("repinned contradiction reached replay"))
    with pytest.raises(ValueError):
        module.prepare_cohort_scope(**values)


def test_changed_actual_targets_refuse_even_when_the_paired_proof_union_is_unchanged(monkeypatch):
    values = package(ks=(1, 0))
    binding = module.prepare_cohort_scope(**values)
    rows = []
    for row in values["declarations"]:
        if row.arm == "D2":
            row = replace(row, activity_targets=(), resolved_prefix=(), activity_targets_sha256=activity_targets_fingerprint(
                arm=row.arm, formation_on=row.formation_on, decision_on=row.decision_on,
                selection_at=row.selection_at, targets=(), seed=row.seed))
        rows.append(row)
    values["declarations"] = tuple(rows)
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("changed actual targets reached replay"))
    with pytest.raises(ValueError, match="binding differs"):
        replay(values, binding)


def test_conditional_scope_keeps_failed_target_budget_and_does_not_demand_a_potential_lot():
    _, quotes = fixture.case((1, 0))
    changes = {(code, day): None for code in fixture.CODES for day in quotes.calendar[61:63]}
    values = package(ks=(1, 0), changes=changes)
    binding = module.prepare_cohort_scope(**values)
    formation = values["original"].calendar[60]
    result = replay(values, binding, keys=(("D1", "ACTIVITY", formation), ("D2", "C01", formation)))
    for row in result["cohorts"]:
        assert row["chosen_codes"] and row["filled_entries"] == 0
        assert row["assigned_capital"] == Decimal("0.05") and row["metrics"]["total_return"] == 0


@pytest.mark.parametrize("component", [False, True])
def test_cash_only_binding_does_not_invent_quote_or_event_evidence(component):
    values = package(ks=(0, 0), component=component)
    binding = module.prepare_cohort_scope(**values)
    assert binding.origin_bounds == binding.events == () and binding.original.quote_snapshot_sha256 is None
    result = replay(values, binding)
    assert result["conditional_exit_bounds_consumed"] and result["quote_snapshot_sha256"] is None
    assert all(row["metrics"]["total_return"] is None and row["raw_metrics"]["total_return"] == 0
        for row in result["cohorts"])


@pytest.mark.parametrize("component", [False, True])
def test_surviving_inventory_fails_the_existing_postbook_bound_guard(monkeypatch, component):
    values = package(ks=(1, 0), component=component)
    binding = module.prepare_cohort_scope(**values)
    real = comparison.replay_synthetic
    def surviving(*args, **kwargs):
        result = real(*args, **kwargs)
        entry_lot = next(book.lots[0] for book in result.books if book.lots)
        return replace(result, books=(*result.books[:-1], replace(result.books[-1], lots=(entry_lot,))))
    monkeypatch.setattr(comparison, "replay_synthetic", surviving)
    key = ("D1", "ACTIVITY", values["original"].calendar[60])
    with pytest.raises(ValueError, match="survives"):
        replay(values, binding, keys=(key,))


@pytest.mark.parametrize("component", [False, True])
def test_existing_postbook_inventory_check_runs_for_every_selected_cohort(monkeypatch, component):
    values = package(component=component)
    binding = module.prepare_cohort_scope(**values)
    calls = []
    real = module.verify_component_inventory if component else module.verify_ordinary_inventory
    def checked(replays, *args):
        assert len(replays) == 1
        calls.append(next(iter(replays)))
        return real(replays, *args)
    monkeypatch.setattr(module, "verify_component_inventory" if component else "verify_ordinary_inventory", checked)
    result = replay(values, binding)
    assert calls.count("D1") == calls.count("D2") == 12 and len(calls) == len(result["cohorts"])


def rights_package(*, component=False, shortened=False, v2=False, v3=False, changes=None):
    """The artificial calendar contains all three fabricated historical periods."""
    policy = synthetic_policy_v3() if v3 else synthetic_policy_v2() if v2 else synthetic_policy()
    codes = tuple(sorted(row["code"] for row in policy["events"]))
    calendar = tuple(date(2023, 1, 1) + timedelta(days=index) for index in range(314)) + tuple(
        map(date.fromisoformat, (*(("2024-07-15",) if v3 else ()), "2025-04-03", "2025-04-06",
            *(("2026-03-17",) if v2 or v3 else ()), "2026-09-18", "2026-09-19", "2026-09-20")))
    issue_ids = {row["code"]: row["isin"] for row in policy["events"]}
    population, declarations = tuple((code, issue_ids[code]) for code in codes), []
    for arm, lag in (("D1", 1), ("D2", 2)):
        timing = SessionLagPolicy(lag, paired=True)
        for index, targets in ((60, codes), (186, ())):
            formation, decision, cutoff = calendar[index], timing.execution_on(calendar, index), timing.selection_at(calendar, index)
            targets = tuple(sorted(targets, key=lambda code: sha256(f"{PARAMETERS['seed']}:{formation}:{code}".encode()).digest()))
            for group in comparison.GROUPS:
                prefix = () if group == "ACTIVITY" or not targets else tuple((code, "eligible", "d" * 64)
                    for code in rank_control_codes(codes, control_id=group, formation_on=formation))
                declarations.append(CohortSelection(group, arm, formation, decision, cutoff, PARAMETERS["seed"], targets,
                    activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                        selection_at=cutoff, targets=targets, seed=PARAMETERS["seed"]), population, len(population),
                    population_fingerprint(population), fixture.PARTITION, prefix))
    declarations = tuple(declarations)
    end_index = calendar.index(date.fromisoformat(PARAMETERS["end"]))
    original = fixture.quotes(calendar, scope={code: calendar[61:end_index + 1] for code in codes}, changes=changes)
    selections, required, identities = plan_inputs(declarations, original)
    plan = provisional_exit_bounds(selections["D1"], selections["D2"], original, PARAMETERS, required, identities, input_sha256=PINS)
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
        for row in plan["entries"])
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete", calendar_sha256=plan["calendar_sha256"],
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=original.quote_snapshot_sha256,
        required_scope_sha256=original.read_scope_sha256, windows=[], events=[], noops=[], unresolved=[])
    evidence = dict(references={}, bases={})
    for row in policy["events"]:
        event = dict({key: row[key] for key in
            ("event_id", "kind", "code", "isin", "start", "end", "evidence_ref")}, status="reviewed_unmodeled")
        if "date_role" in row: event["date_role"] = row["date_role"]
        coverage["events"].append(event)
        evidence["references"][row["evidence_ref"]] = dict({key: row[key] for key in
            ("code", "isin", "start", "end", "raw_sha256", "review_sha256", "coordinate")},
            reviewed_on="2026-10-16", finding="reviewed_" + row["kind"], event_ids=[row["event_id"]])
    policy_raw, proof = encoded(policy), None
    if component:
        terms = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=calendar,
            activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=original.quote_snapshot_sha256,
            return_policy_json=policy_raw)
        proof = component_exit_bounds(plan, selections["D1"], selections["D2"], original, PARAMETERS, required, identities,
            input_sha256=PINS, quotes=original, terms=terms, unresolved_priority_intervals=() if shortened else windows)
        windows = tuple(proof["windows"])
        coverage["required_scope_sha256"] = proof["component_scope_sha256"]
    else:
        # All declared event issues retain original-end in mixed_action_scope.
        windows = tuple(dict(code=code, isin=issue_ids[code], start=str(calendar[61]), end=str(calendar[end_index])) for code in codes)
    for index, row in enumerate(windows):
        ids = [event["event_id"] for event in policy["events"] if event["code"] == row["code"]
            and row["start"] <= event["end"] and event["start"] <= row["end"]]
        ref = "complete-" + str(index)
        state = ("price_return_unmodeled_assets" if row["code"] in ("006800", "086280") else "price_return_unmodeled_rights") if ids else "reviewed_no_event"
        coverage["windows"].append(dict(row, review_state=state, event_ids=ids, evidence_refs=[ref]))
        evidence["references"][ref] = dict(row, raw_sha256="5" * 64, review_sha256="6" * 64,
            coordinate="synthetic:full-interval", reviewed_on="2026-10-16", event_ids=ids,
            finding=state if ids else "inferred_no_compulsory_action")
    values = arguments(declarations, original, original, plan, proof, coverage, evidence, required, identities)
    values["return_policy_json"] = policy_raw
    values["expected_pins"]["return_policy"] = digest(policy_raw)
    return values


@pytest.mark.parametrize("component", [False, True])
def test_policy_preserves_full_potential_horizons_and_existing_book_nav(component):
    values = rights_package(component=component)
    binding = module.prepare_cohort_scope(**values)
    assert binding.events == () and binding.return_policy_json == values["return_policy_json"]
    assert all(end == date.fromisoformat(PARAMETERS["end"]) for *_, end in binding.origin_bounds)
    assert binding.original == values["original"] == binding.quotes
    key = ("D1", "ACTIVITY", values["original"].calendar[60])
    result = replay(values, binding, keys=(key,))
    baseline = comparison.replay_equal_capital_cohorts(values["declarations"], values["quotes"], (), parameters=PARAMETERS, cohort_keys=(key,))
    assert result["cohorts"][0]["replay"] == baseline["cohorts"][0]["replay"]


def test_missing_component_priority_does_not_shorten_an_unmodeled_rights_origin(monkeypatch):
    values = rights_package(component=True, shortened=True)
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("shortened rights scope reached engine"))
    with pytest.raises(ValueError, match="original horizon"): module.prepare_cohort_scope(**values)


@pytest.mark.parametrize("change", ["no_policy", "missing_pin", "wrong_pin", "changed_policy", "binding_policy"])
def test_policy_bytes_and_pins_are_revalidated_before_engine(monkeypatch, change):
    values = rights_package()
    binding = module.prepare_cohort_scope(**values)
    if change == "no_policy": values.pop("return_policy_json"); values["expected_pins"].pop("return_policy")
    elif change == "missing_pin": values["expected_pins"].pop("return_policy")
    elif change == "wrong_pin": values["expected_pins"]["return_policy"] = "f" * 64
    elif change == "changed_policy": values["return_policy_json"] += b" "
    else: binding = replace(binding, return_policy_json=b"{}")
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("bad policy reached engine"))
    with pytest.raises(ValueError):
        if change == "binding_policy": replay(values, binding)
        else: module.prepare_cohort_scope(**values)


def test_optional_policy_keeps_existing_raw_lg_terms_quotes_and_nav():
    values = raw_package()
    old = module.prepare_cohort_scope(**values)
    values["return_policy_json"] = encoded(synthetic_policy())
    values["expected_pins"]["return_policy"] = digest(values["return_policy_json"])
    current = module.prepare_cohort_scope(**values)
    assert current.events == old.events and current.origin_bounds == old.origin_bounds
    assert current.quotes == old.quotes and current.scope_json == old.scope_json
    assert current.binding_sha256 != old.binding_sha256
    key = ("D1", "ACTIVITY", values["original"].calendar[60])
    assert replay(values, current, keys=(key,))["cohorts"][0]["replay"] == replay(values, old, keys=(key,))["cohorts"][0]["replay"]


@pytest.mark.parametrize("component", [False, True])
def test_stock_dividend_policy_preserves_full_horizons_and_same_original_replay(component):
    values = rights_package(component=component, v2=True)
    binding = module.prepare_cohort_scope(**values)
    assert binding.events == () and binding.return_policy_json == values["return_policy_json"]
    assert all(end == date.fromisoformat(PARAMETERS["end"]) for *_, end in binding.origin_bounds)
    key = ("D1", "ACTIVITY", values["original"].calendar[60])
    result = replay(values, binding, keys=(key,))
    baseline = comparison.replay_equal_capital_cohorts(values["declarations"], values["quotes"], (), parameters=PARAMETERS, cohort_keys=(key,))
    assert result["cohorts"][0]["replay"] == baseline["cohorts"][0]["replay"]


def test_stock_dividend_origin_cannot_use_shorter_component_scope(monkeypatch):
    values = rights_package(component=True, shortened=True, v2=True)
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("shortened stock-dividend scope reached engine"))
    with pytest.raises(ValueError, match="original horizon"): module.prepare_cohort_scope(**values)


def test_v3_accepts_the_same_recomputed_short_proof_that_v2_keeps_full():
    values = rights_package(component=True, shortened=True, v2=True)
    proof_bytes = values["component_exit_scope"]
    with pytest.raises(ValueError, match="original horizon"):
        module.prepare_cohort_scope(**values)
    values["return_policy_json"] = encoded(synthetic_policy_v3())
    values["expected_pins"]["return_policy"] = digest(values["return_policy_json"])
    binding = module.prepare_cohort_scope(**values)
    assert binding.component_proof_json == proof_bytes
    assert binding.original.requested_coordinates == values["original"].requested_coordinates
    assert all(end < date.fromisoformat(PARAMETERS["end"]) for *_, end in binding.origin_bounds)
    assert binding.basis_evidence_json == values["basis_evidence"]
    assert binding.coverage_json == values["reviewed_coverage"]


def test_v3_projection_does_not_hide_an_unchecked_extra_policy_named_reference():
    values = rights_package(component=True, shortened=True, v2=True)
    policy = synthetic_policy_v3()
    values["return_policy_json"] = encoded(policy)
    values["expected_pins"]["return_policy"] = digest(values["return_policy_json"])
    evidence = strict_json(values["basis_evidence"])
    evidence["references"][policy["events"][-1]["evidence_ref"]] = {"unchecked_extra": True}
    values["basis_evidence"] = encoded(evidence)
    values["expected_pins"]["basis_evidence"] = digest(values["basis_evidence"])
    with pytest.raises(ValueError, match="unrelated or unused"):
        module.prepare_cohort_scope(**values)


@pytest.mark.parametrize("arm", ["D1", "D2"])
def test_v3_five_issuer_scope_keeps_same_book_budgets_and_unknown_bonus_assets(arm):
    values = rights_package(component=True, shortened=True, v3=True)
    before = deepcopy(values)
    binding = module.prepare_cohort_scope(**values)
    key = (arm, "ACTIVITY", values["original"].calendar[60])
    result = replay(values, binding, keys=(key,))
    baseline = comparison.replay_equal_capital_cohorts(values["declarations"], values["quotes"], (),
        parameters=PARAMETERS, cohort_keys=(key,))
    row = result["cohorts"][0]
    assert row["replay"] == baseline["cohorts"][0]["replay"] and values == before
    assert row["assigned_capital"] == Decimal("0.25") and row["filled_entries"] == 5
    assert result["action_coverage_scope"] == row["action_coverage_scope"] == "conditional_common_share_lifetime"
    attribution = row["known_issuer_price_return_attribution"]
    assert set(attribution["by_code"]) == {"003670", "005070", "006400", "006800", "086280"}
    assert attribution["by_code"]["086280"]["automatic_bonus_shares"] is None
    assert attribution["automatic_bonus_fractional_cash"] is None


def test_v3_future_unmodeled_event_does_not_extend_common_share_bounds_but_its_source_is_checked():
    values = rights_package(component=True, shortened=True, v3=True)
    old = module.prepare_cohort_scope(**values)
    policy, coverage, evidence = (strict_json(values[key]) for key in ("return_policy_json", "reviewed_coverage", "basis_evidence"))
    event = next(row for row in policy["events"] if row["code"] == "003670")
    assert all(end < date.fromisoformat(event["start"]) for *_, end in old.origin_bounds)
    event["start"], event["end"] = "2025-05-01", "2025-05-02"
    next(row for row in coverage["events"] if row["event_id"] == event["event_id"]).update(start=event["start"], end=event["end"])
    evidence["references"][event["evidence_ref"]].update(start=event["start"], end=event["end"])
    for key, role, value in (("return_policy_json", "return_policy", policy), ("reviewed_coverage", "reviewed_action_coverage", coverage),
            ("basis_evidence", "basis_evidence", evidence)):
        values[key] = encoded(value); values["expected_pins"][role] = digest(values[key])
    current = module.prepare_cohort_scope(**values)
    assert current.scope_json == old.scope_json and current.origin_bounds == old.origin_bounds
    evidence["references"][event["evidence_ref"]]["raw_sha256"] = "f" * 64
    values["basis_evidence"] = encoded(evidence); values["expected_pins"]["basis_evidence"] = digest(values["basis_evidence"])
    with pytest.raises(ValueError, match="source or review"):
        module.prepare_cohort_scope(**values)


@pytest.mark.parametrize("change", ["missing_inside_event", "missing_final_day", "false_no_event", "repinned_candidate"])
def test_v3_never_waives_in_period_actions_or_inclusive_endpoint_coverage(change, monkeypatch):
    values = rights_package(component=True, shortened=True, v3=True)
    module.prepare_cohort_scope(**values)
    coverage = strict_json(values["reviewed_coverage"])
    if change == "repinned_candidate":
        proof = strict_json(values["component_exit_scope"])
        proof["entries"][0]["end_on"] = proof["entries"][0]["origin"]["entry_on"]
        proof["component_scope_sha256"] = digest(encoded({key: item for key, item in proof.items() if key != "component_scope_sha256"}))
        values["component_exit_scope"] = encoded(proof)
        values["expected_pins"]["component_exit_scope"] = digest(values["component_exit_scope"])
    else:
        if change == "missing_inside_event": coverage["events"] = [row for row in coverage["events"] if row["code"] != "005070"]
        else:
            row = next(row for row in coverage["windows"] if row["code"] == "005070")
            if change == "missing_final_day": row["end"] = str(date.fromisoformat(row["end"]) - timedelta(days=1))
            else: row.update(review_state="reviewed_no_event", event_ids=[])
        values["reviewed_coverage"] = encoded(coverage)
        values["expected_pins"]["reviewed_action_coverage"] = digest(values["reviewed_coverage"])
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("invalid v3 scope reached a book"))
    with pytest.raises(ValueError): module.prepare_cohort_scope(**values)


def test_v3_still_requires_every_action_on_the_actual_common_share_exit_day():
    values = rights_package(component=True, shortened=True, v3=True)
    policy, coverage, evidence = (strict_json(values[key]) for key in ("return_policy_json", "reviewed_coverage", "basis_evidence"))
    last = max(row["end"] for row in coverage["windows"] if row["code"] == "005070")
    event = next(row for row in policy["events"] if row["code"] == "005070")
    event["start"] = event["end"] = last
    next(row for row in coverage["events"] if row["event_id"] == event["event_id"]).update(start=last, end=last)
    evidence["references"][event["evidence_ref"]].update(start=last, end=last)
    for key, role, value in (("return_policy_json", "return_policy", policy), ("reviewed_coverage", "reviewed_action_coverage", coverage),
            ("basis_evidence", "basis_evidence", evidence)):
        values[key] = encoded(value); values["expected_pins"][role] = digest(values[key])
    module.prepare_cohort_scope(**values)
    row = next(row for row in coverage["windows"] if row["code"] == "005070")
    row["end"] = str(date.fromisoformat(last) - timedelta(days=1))
    values["reviewed_coverage"] = encoded(coverage); values["expected_pins"]["reviewed_action_coverage"] = digest(values["reviewed_coverage"])
    with pytest.raises(ValueError): module.prepare_cohort_scope(**values)


def test_v3_frozen_held_shares_keep_original_fallback_and_policy_period():
    initial = rights_package(component=True, shortened=True, v3=True)
    calendar = initial["original"].calendar
    changes = {("006800", day): (day.strftime("%Y%m%d"), "100", "100", "100", "100", "0", "0") for day in calendar[187:-2]}
    values = rights_package(component=True, shortened=True, v3=True, changes=changes)
    binding = module.prepare_cohort_scope(**values)
    assert all(end == date.fromisoformat(PARAMETERS["end"]) for _, _, code, _, end in binding.origin_bounds if code == "006800")
    assert all(end < date.fromisoformat(PARAMETERS["end"]) for _, _, code, _, end in binding.origin_bounds if code != "006800")


def test_v3_preserves_lg_raw_missing_candidate_fallback_and_requires_component_recomputation():
    values = raw_package(end=187)
    values["return_policy_json"] = encoded(synthetic_policy_v3())
    values["expected_pins"]["return_policy"] = digest(values["return_policy_json"])
    with pytest.raises(ValueError, match="potential raw scope"): module.prepare_cohort_scope(**values)
    values = rights_package(v3=True)
    with pytest.raises(ValueError, match="recomputed component proof"): module.prepare_cohort_scope(**values)
