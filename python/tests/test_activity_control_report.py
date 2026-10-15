"""Source-only comparison reporting over fabricated books and declarations."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal as D, localcontext
import hashlib
import json
import math
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_control_report as module
from research.activity_accounting import Lot
from research.activity_book import ActivityBook
from research.activity_control_selection import CountedControlSelection, activity_targets_fingerprint, population_fingerprint
from research.activity_partition_selection import PartitionSelection
from research.activity_replay import SyntheticReplay
from research.activity_timing import SessionLagPolicy
from research.activity_timing_report import paired_timing_report


def serialized(value):
    return json.loads(json.dumps(asdict(value), default=lambda item: str(item) if isinstance(item, D) else item.isoformat()))


def fixture(*, missed=False, carried=False, cash_only=False, zero=False, precision=False):
    params = deepcopy(module.PARAMETERS)
    end = date.fromisoformat(params["end"])
    calendar = tuple(end + timedelta(days=index - 260) for index in range(263))
    formation = calendar[60]
    codes = ("A", "B", "C")
    order = tuple(sorted(codes, key=lambda code: hashlib.sha256(f'{params["seed"]}:{formation}:{code}'.encode()).digest()))
    activity_code, control_code = order[-1], order[0]
    population = tuple((code, f"synthetic_{code}") for code in codes)
    activities, controls, partitions, selections = {}, {}, {}, {}
    with localcontext() as context:
        context.prec = 28
        for arm, lag in (("D1", 1), ("D2", 2)):
            policy = SessionLagPolicy(lag, paired=True)
            decision, cutoff = policy.execution_on(calendar, 60), policy.selection_at(calendar, 60)
            targets = () if cash_only else (activity_code,)
            partitions[arm] = (PartitionSelection(formation, decision, cutoff, targets, 3, "c" * 64),)
            selections[arm] = (CountedControlSelection(arm, formation, decision, cutoff, params["seed"], targets,
                activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                    selection_at=cutoff, targets=targets, seed=params["seed"]), population, 3,
                population_fingerprint(population), "d" * 64, frozenset(),
                () if cash_only else ((control_code, "eligible", "b" * 64),)),)
            for owner, code, cash, shares, miss in ((activities, activity_code, D(".5"), D(".5"), False),
                    (controls, control_code, D(".75"), D(".25"), missed)):
                books = []
                for index, day in enumerate(calendar[61:261]):
                    if cash_only or miss or day < decision:
                        books.append(ActivityBook(D(1), (), day))
                        continue
                    mark = D("1") + D(index % 7) / 100
                    value = D(".25") if carried else None
                    lot = Lot(arm + code, code, shares, mark, day, decision, calendar[260],
                        calendar[261] if carried else decision, "a" * 64, carried_value=value)
                    amount = D("1.23456789012345678901234567894") if precision else cash
                    books.append(ActivityBook(amount, (lot,), day))
                if zero:
                    books[5:] = [ActivityBook(D(0), (), book.as_of) for book in books[5:]]
                chosen = () if cash_only else (code,)
                owner[arm] = SyntheticReplay(tuple(books), ((formation, chosen),),
                    (("entries", 0 if cash_only or miss else 1), ("unfilled_entries", int(miss and not cash_only))), 0)
        report = paired_timing_report(activities["D1"], activities["D2"], partitions["D1"], partitions["D2"], initial_nav=D(1))
    saved = {"schema": "activity-paired-replay-v1", "status": "pure_paired_replay", "initial_nav": "1", "report": deepcopy(report),
        "replays": {arm: serialized(value) for arm, value in activities.items()},
        "selections": {arm: [serialized(value) for value in rows] for arm, rows in partitions.items()}}
    return dict(original_report=report, original_replay=saved, control_replays=controls, control_selections=selections,
        calendar=calendar, original_parameters=params, control_parameters=deepcopy(params))


def test_fixed_control_reports_each_lag_and_reuses_original_books_without_io(monkeypatch):
    values = fixture()
    before = deepcopy(values)
    def denied(*args, **kwargs):
        pytest.fail("pure report attempted IO")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, denied)
    result = module.descriptive_control_report(**values)
    assert values == before and set(result["arms"]) == {"D1", "D2"}
    json.dumps(result, allow_nan=False)
    for arm, row in result["arms"].items():
        for policy in ("ACTIVITY", "CONTROL"):
            assert row[policy]["filled_entries"] == row[policy]["terminal_open_lots"] == 1
            assert row[policy]["ordinary_closed_trades"] == row[policy]["unfilled_entries"] == 0
            assert row[policy]["session_return_count"] == 200
            assert row[policy]["exposure"]["active_sessions"] == (200 if arm == "D1" else 199)
            assert "unannualized" in result["units"]["session_return_sd"]
        assert row["target_overlap"]["symmetric_difference_count"] == row["target_overlap"]["union_count"] == 2
        assert D(row["target_overlap"]["rate"]) == 1
        assert D(row["CONTROL"]["cumulative_return"]) - D(row["ACTIVITY"]["cumulative_return"]) == D(row["CONTROL_minus_ACTIVITY"]["cumulative_return"])
    assert result["arms"]["D1"]["ACTIVITY"]["exposure"]["mean_invested_fraction"] != result["arms"]["D1"]["CONTROL"]["exposure"]["mean_invested_fraction"]
    assert all(result[key] is False for key in ("comparison_power_prerequisite_met", "power_design_passed", "promotion_allowed",
        "source_truth_certified", "full_economic_accounting_verified", "books_replayed"))
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert "not a permutation" in result["interpretation"] and "exclude dividends" in result["limitations"]


def test_failed_control_fill_keeps_original_target_ceiling_and_own_missed_count():
    result = module.descriptive_control_report(**fixture(missed=True))
    for row in result["arms"].values():
        assert row["CONTROL"]["filled_entries"] == 0 and row["CONTROL"]["unfilled_entries"] == 1
        assert row["target_overlap"]["formations"][0]["activity_target_ceiling"] == 1
        assert row["target_overlap"]["formations"][0]["control_targets"] == 1
        assert D(row["CONTROL"]["exposure"]["mean_invested_fraction"]) == 0


def test_carried_pending_inventory_contributes_to_exposure_without_becoming_liquid():
    result = module.descriptive_control_report(**fixture(carried=True))
    for row in result["arms"].values():
        control = row["CONTROL"]["exposure"]
        assert D(control["maximum_invested_fraction"]) == D(".25")
        assert "carried pending claims" in control["interpretation"]


def test_cash_only_empty_targets_have_zero_exposure_and_unknown_target_overlap():
    result = module.descriptive_control_report(**fixture(cash_only=True))
    for row in result["arms"].values():
        assert row["target_overlap"]["rate"] is None
        for policy in ("ACTIVITY", "CONTROL"):
            assert D(row[policy]["exposure"]["mean_invested_fraction"]) == 0
            assert row[policy]["exposure"]["active_sessions"] == 0


def test_zero_nav_sessions_have_unknown_exposure_and_remain_absorbing():
    result = module.descriptive_control_report(**fixture(zero=True))
    for row in result["arms"].values():
        assert row["CONTROL"]["exposure"]["undefined_zero_nav_sessions"] == 195
        assert all(point["invested_fraction"] is None for point in row["CONTROL"]["exposure"]["path"][5:])


def test_exact_long_nav_preserves_existing_decimal_division_boundaries():
    values = fixture(precision=True)
    assert any(len(D(point["nav"]).as_tuple().digits) > 28 for point in values["original_report"]["arms"]["D1"]["path"])
    result = module.descriptive_control_report(**values)
    assert result["arms"]["D1"]["ACTIVITY"]["cumulative_return"] == values["original_report"]["arms"]["D1"]["total_return"]


def test_control_cannot_use_extra_resolved_held_candidates_to_exceed_target_ceiling():
    values = fixture()
    selected = values["control_selections"]["D1"][0]
    order = tuple(sorted((code for code, _ in selected.source_population),
        key=lambda code: hashlib.sha256(f"{selected.seed}:{selected.formation_on}:{code}".encode()).digest()))
    selected = replace(selected, prior_potential_codes=frozenset(order[:1]),
        resolved_prefix=tuple((code, "eligible", "b" * 64) for code in order[:2]))
    values["control_selections"]["D1"] = (selected,)
    values["control_replays"]["D1"] = replace(values["control_replays"]["D1"],
        selections=((selected.formation_on, order[:2]),), diagnostics=(("entries", 2), ("unfilled_entries", 0)))
    with pytest.raises(ValueError, match="exceed original pre-opening ceiling"):
        module.descriptive_control_report(**values)


def test_each_lag_cannot_silently_use_a_different_issuer_identity_population():
    values = fixture()
    selected = values["control_selections"]["D2"][0]
    population = tuple((code, isin + "changed") for code, isin in selected.source_population)
    values["control_selections"]["D2"] = (replace(selected, source_population=population,
        source_population_sha256=population_fingerprint(population)),)
    with pytest.raises(ValueError, match="same formation source population"):
        module.descriptive_control_report(**values)


def test_matching_but_refitted_cost_parameters_are_still_rejected():
    values = fixture()
    for name in ("original_parameters", "control_parameters"):
        values[name]["slippage_bps_per_side"] = 10.0
    with pytest.raises(ValueError, match="unchanged canonical"):
        module.descriptive_control_report(**values)


def test_verified_original_headline_representation_is_retained():
    values = fixture()
    original = values["original_report"]["arms"]["D1"]
    original["session_return_sd"] = math.nextafter(original["session_return_sd"], math.inf)
    values["original_replay"]["report"] = deepcopy(values["original_report"])
    result = module.descriptive_control_report(**values)
    assert result["arms"]["D1"]["ACTIVITY"]["session_return_sd"] == original["session_return_sd"]


@pytest.mark.parametrize("mutation", ["cash_nan", "cash_negative", "lot_nan", "book_cash", "return", "nav_ratio", "sd", "count", "trade_count", "unknown_book", "report_link", "target_overlap"])
def test_saved_books_paths_metrics_and_target_overlap_must_reconcile(mutation):
    values = fixture()
    replay, report = values["original_replay"]["replays"]["D1"], values["original_report"]["arms"]["D1"]
    if mutation == "cash_nan": replay["books"][0]["cash"] = "NaN"
    elif mutation == "cash_negative": replay["books"][0]["cash"] = "-1"
    elif mutation == "lot_nan": replay["books"][0]["lots"][0]["mark"] = "NaN"
    elif mutation == "book_cash": replay["books"][0]["cash"] = "2"
    elif mutation == "return": report["path"][0]["session_return"] = "2"
    elif mutation == "nav_ratio": report["path"][0]["nav_ratio"] = "2"
    elif mutation == "sd": report["session_return_sd"] *= 2
    elif mutation == "count": report["session_return_count"] -= 1
    elif mutation == "trade_count": replay["diagnostics"][0][1] = 0
    elif mutation == "unknown_book": replay["books"][0]["future_price"] = "2"
    elif mutation == "target_overlap": values["original_report"]["target_overlap"]["formations"][0]["D1_codes"] = ["foreign"]
    else: values["original_replay"]["report"]["initial_nav"] = "2"
    if mutation != "report_link": values["original_replay"]["report"] = deepcopy(values["original_report"])
    with pytest.raises(ValueError): module.descriptive_control_report(**values)


@pytest.mark.parametrize("mutation", ["control_day", "activity_day", "formation", "late_decision", "original_targets", "foreign_control_target", "control_counter", "missing_lag", "parameters", "cost", "parameter_type", "calendar"])
def test_whole_calendar_formation_targets_and_frozen_cost_contract_are_required(mutation):
    values = fixture()
    control = values["control_replays"]["D1"]
    selection = values["control_selections"]["D1"][0]
    if mutation == "control_day": values["control_replays"]["D1"] = replace(control, books=control.books[1:])
    elif mutation == "activity_day": values["original_replay"]["replays"]["D1"]["books"].pop()
    elif mutation == "formation": values["control_selections"]["D1"] = ()
    elif mutation == "late_decision":
        later = replace(selection, decision_on=selection.decision_on + timedelta(days=1), selection_at=selection.selection_at + timedelta(days=1),
            activity_targets_sha256=activity_targets_fingerprint(arm="D1", formation_on=selection.formation_on,
                decision_on=selection.decision_on + timedelta(days=1), selection_at=selection.selection_at + timedelta(days=1), targets=selection.activity_targets, seed=selection.seed))
        values["control_selections"]["D1"] = (later,)
    elif mutation == "original_targets":
        changed = (selection.eligible_codes[0],)
        values["control_selections"]["D1"] = (replace(selection, activity_targets=changed,
            activity_targets_sha256=activity_targets_fingerprint(arm="D1", formation_on=selection.formation_on,
                decision_on=selection.decision_on, selection_at=selection.selection_at, targets=changed, seed=selection.seed)),)
    elif mutation == "foreign_control_target": values["control_replays"]["D1"] = replace(control, selections=((selection.formation_on, ("foreign",)),))
    elif mutation == "control_counter": values["control_replays"]["D1"] = replace(control, diagnostics=(("entries", 1), ("unfilled_entries", 1)))
    elif mutation == "missing_lag": values["control_replays"].pop("D2")
    elif mutation == "parameters": values["control_parameters"]["holding_sessions"] = 63
    elif mutation == "cost": values["control_parameters"]["slippage_bps_per_side"] = 10.0
    elif mutation == "parameter_type": values["control_parameters"]["threshold"] = 3
    else: values["calendar"] = tuple(reversed(values["calendar"]))
    with pytest.raises(ValueError): module.descriptive_control_report(**values)
