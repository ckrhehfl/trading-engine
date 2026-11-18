"""Invented matched support, exact cash and causal representatives for ER."""

from copy import deepcopy
from datetime import date
from fractions import Fraction

import pytest

from research import intraday_event_calibration_metrics as metrics
from test_intraday_event_factorized_metrics import cash_row
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction


def representatives(candidates, forecasts=None):
    events = tuple({c.episode.event_id: c.episode for c in candidates}.values())
    forecasts = forecasts or {arm: [prediction(c, .01, .02) for c in candidates] for arm in metrics.ARMS}
    return metrics.select_representatives(CALENDAR, 0, events, candidates, forecasts)


def test_missing_mean_never_selects_a_later_row_or_changes_old_scores():
    first, later = candidate(decision=36), candidate(decision=39)
    forecasts = {arm: [prediction(first), prediction(later)] for arm in metrics.ARMS}
    forecasts["mean"] = [prediction(later)]
    reps = representatives([first, later], forecasts)
    selected = next(row for row in reps if row["phase"] == "waiting_pullback")
    assert selected["key"] == first.key
    assert selected["predictions"]["mean"]["net_immediate"] is None
    assert selected["predictions"]["direct"]["net_immediate"] is not None
    assert all(set(values) == set(metrics.HEADS) for values in selected["predictions"].values())
    joined = metrics.join_targets(reps, [label_record(first), label_record(later)])
    assert not any(metrics._paired(row) for row in joined)


def test_comparison_uses_three_arm_known_support_and_reports_own_support():
    c = candidate()
    raw = label_record(c)
    forecasts = {arm: [prediction(c, float(Fraction(raw["training_labels"]["net_immediate"])),
                                      float(Fraction(raw["training_labels"]["net_wait3"])))]
                 for arm in metrics.ARMS}
    policies = {arm: [cash_row(c, arm)] for arm in metrics.ARMS}
    reps = representatives([c], forecasts)
    report = metrics.summarize(CALENDAR, 0, reps, [raw], policies)
    assert report["cells"][0]["paired_known"] == 1
    assert report["cells"][0]["arms"]["mean"]["net_immediate"]["calibration"]["rmse"] == 0
    forecasts["mean"][0]["predictions"]["net_wait3"] = None
    report = metrics.summarize(CALENDAR, 0, representatives([c], forecasts), [raw], policies)
    cell = report["cells"][0]
    assert cell["paired_known"] == 0
    assert cell["arms"]["direct"]["net_immediate"]["own_known"] == 1
    assert cell["arms"]["mean"]["net_wait3"]["own_known"] == 0
    assert cell["arms"]["direct"]["net_immediate"]["calibration"]["rmse"] is None


def test_cash_differences_are_exact_and_unknown_is_not_zero():
    c = candidate()
    reports = {arm: [cash_row(c, arm, str(Fraction(10**18 + i, 3)))] for i, arm in enumerate(metrics.ARMS)}
    value = metrics.policy_summary(CALENDAR[97:137], reports)
    assert value["comparisons"]["direct_minus_mean"]["paired_cash_difference"] == "1/3"
    assert value["comparisons"]["calibrated_minus_mean"]["paired_cash_difference"] == "2/3"
    assert any(row["fills"] == 0 for row in value["arms"]["mean"]["weekly_fills"])
    reports["mean"][0].update(net_cash=None, fill=None, status="unknown")
    value = metrics.policy_summary(CALENDAR[97:137], reports)
    assert value["comparisons"]["direct_minus_mean"]["paired_known_episodes"] == 0
    assert value["comparisons"]["calibrated_minus_direct"]["paired_known_episodes"] == 1
    assert value["arms"]["mean"]["unknown_cash_episodes"] == 1


def test_mismatched_policy_identity_and_missing_phase_rejected():
    c = candidate()
    policies = {arm: [cash_row(c, arm)] for arm in metrics.ARMS}
    policies["mean"][0]["symbol"] = "999999"
    with pytest.raises(ValueError, match="identity"):
        metrics.policy_summary(CALENDAR[97:137], policies)
    policies["mean"][0]["symbol"] = c.episode.symbol
    with pytest.raises(ValueError, match="phases"):
        metrics.summarize(CALENDAR, 0, representatives([c])[:-1], [label_record(c)], policies)


def test_constant_phase_mean_has_tied_rank_and_no_invented_fill_estimator():
    # A tied score is an expected property of this control, not a pipeline failure.
    a, b = candidate(), candidate(98)
    reps = representatives([a, b])
    policies = {arm: [cash_row(a, arm), cash_row(b, arm)] for arm in metrics.ARMS}
    positive, negative = label_record(a), label_record(b)
    for action in ("immediate", "wait3"):
        positive["targets"][action]["net_cash"] = str(abs(Fraction(positive["targets"][action]["net_cash"])))
        positive["targets"][action]["value"] = str(Fraction(positive["targets"][action]["net_cash"])
                                                     / Fraction(positive["targets"][action]["denominator"]))
        positive["training_labels"]["net_" + action] = positive["targets"][action]["value"]
        negative["targets"][action]["net_cash"] = str(-abs(Fraction(negative["targets"][action]["net_cash"])))
        negative["targets"][action]["value"] = str(Fraction(negative["targets"][action]["net_cash"])
                                                     / Fraction(negative["targets"][action]["denominator"]))
        negative["training_labels"]["net_" + action] = negative["targets"][action]["value"]
    report = metrics.summarize(CALENDAR, 0, reps, [positive, negative], policies)
    head = report["cells"][0]["arms"]["mean"]["net_immediate"]
    assert "fill" not in head
    assert head["cross_cluster"]["auc"]["value"] == .5


def test_pool_uses_original_calendar_and_all_cells():
    reports = []
    for fold in range(4):
        c = candidate(97 + 40*fold)
        reports.append({"fold": fold, "calendar": list(CALENDAR[97 + 40*fold:137 + 40*fold]),
            "cells": [{"fold": fold, "phase": phase} for phase in metrics.PHASES],
            "policy_rows": {arm: [cash_row(c, arm)] for arm in metrics.ARMS}})
    pooled = metrics.pool(CALENDAR, reports)
    assert len(pooled["cells"]) == 16
    assert pooled["policy"]["arms"]["mean"]["calendar_weeks"] == len({
        date.fromisoformat(day).strftime("%G-W%V") for day in CALENDAR[97:]})
    broken = deepcopy(reports)
    broken[3]["cells"].pop()
    with pytest.raises(ValueError, match="sixteen"):
        metrics.pool(CALENDAR, broken)
