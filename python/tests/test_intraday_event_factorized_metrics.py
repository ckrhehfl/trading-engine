"""Invented causal EP representatives, paired scores and original-week reports."""

from datetime import date
from fractions import Fraction

import pytest

from research import intraday_event_factorized_metrics as metrics
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction


def representatives(candidates, predictions=None):
    events = tuple({c.episode.event_id: c.episode for c in candidates}.values())
    forecasts = [prediction(c, 0.01, 0.02) for c in candidates] if predictions is None else predictions
    components = [{"key": c.key, "session": c.episode.session, "components": {"fill_wait3": .6}} for c in candidates]
    return metrics.select_representatives(CALENDAR, 0, events, candidates,
        {arm: forecasts for arm in metrics.ARMS}, components_by_arm={arm: components for arm in metrics.ARMS})


def test_earliest_causal_phase_does_not_wait_for_model_or_target_support():
    first, later = candidate(decision=36), candidate(decision=39)
    forecast = prediction(later)
    rows = representatives([first, later], [forecast])
    row = next(row for row in rows if row["phase"] == "waiting_pullback")
    assert row["key"] == first.key
    assert row["predictions"]["direct"]["net_immediate"] is None
    joined = metrics.join_targets(rows, [label_record(first), label_record(later)])
    assert not metrics._paired(next(row for row in joined if row["phase"] == "waiting_pullback"))


def test_future_confirmation_does_not_replace_earlier_phase_and_missing_peer_is_explicit():
    first, confirmed = candidate(), candidate(decision=39, state="CONFIRMED")
    rows = representatives([first, confirmed])
    assert next(row for row in rows if row["phase"] == "onset")["key"] == first.key
    assert next(row for row in rows if row["phase"] == "confirmation")["key"] == confirmed.key
    unsupported = representatives([candidate(peer=None)])
    assert all(row["key"] is None for row in unsupported)
    assert unsupported[0]["support"]["reasons"] == {"unknown_peer": 1}


@pytest.mark.parametrize("field", ["denominator", "net_cash", "matured_offset", "fill_matured_offset"])
def test_saved_target_tamper_rejected(field):
    c = candidate()
    raw = label_record(c)
    raw["targets"]["wait3"][field] = "1" if field in ("denominator", "net_cash") else 100
    with pytest.raises(ValueError):
        metrics.join_targets(representatives([c]), [raw])


def test_missing_cash_remains_unknown_with_known_fill_and_duplicate_target_fails():
    c = candidate()
    raw = label_record(c)
    raw["targets"]["immediate"].update(value=None, net_cash=None, status="unknown")
    raw["training_labels"]["net_immediate"] = None
    joined = metrics.join_targets(representatives([c]), [raw])
    first = joined[0]
    assert first["outcomes"]["net_immediate"]["fill"] == 1
    assert first["outcomes"]["net_immediate"]["target"] is None
    assert not metrics._paired(first)
    with pytest.raises(ValueError, match="duplicate"):
        metrics.join_targets(representatives([c]), [raw, raw])


def cash_row(c, arm, cash="1", fill=1):
    return {"event_id": c.episode.event_id, "session": c.episode.session, "symbol": c.episode.symbol,
            "cluster_id": c.episode.cluster_id, "family": c.episode.family, "arm": arm,
            "action": "immediate", "net_cash": cash, "fill": fill, "status": "known"}


def test_original_week_denominator_and_exact_paired_cash():
    c = candidate()
    reports = {"direct": [cash_row(c, "direct", "100000000000000001/3")],
               "factorized": [cash_row(c, "factorized", "100000000000000004/3")]}
    result = metrics.policy_summary(CALENDAR[97:137], reports)
    assert result["factorized_minus_direct_cash"] == "1"
    assert result["arms"]["direct"]["calendar_weeks"] > 1
    assert sum(row["fills"] == 0 for row in result["arms"]["direct"]["weekly_fills"]) > 0
    reports["factorized"][0].update(net_cash=None, fill=None, status="unknown")
    unknown = metrics.policy_summary(CALENDAR[97:137], reports)
    assert unknown["paired_known_episodes"] == 0
    assert unknown["arms"]["factorized"]["unknown_fills"] == 1


def test_perfect_value_calibration_and_fill_brier_are_different_metrics():
    c = candidate()
    record = label_record(c)
    value = float(Fraction(record["training_labels"]["net_immediate"]))
    forecast = prediction(c, value, float(Fraction(record["training_labels"]["net_wait3"])))
    reps = representatives([c], [forecast])
    policies = {arm: [cash_row(c, arm)] for arm in metrics.ARMS}
    report = metrics.summarize(CALENDAR, 0, reps, [record], policies)
    head = report["cells"][0]["arms"]["direct"]["net_immediate"]
    assert head["calibration"]["rmse"] == 0
    assert head["fill"]["brier"] == .25
    assert head["cross_cluster"]["auc"]["value"] is None
    assert report["cells"][1]["paired_known"] == 0


def test_pool_keeps_four_folds_and_does_not_double_count_boundary_weeks():
    reports = []
    for fold in range(4):
        c = candidate(97 + 40*fold)
        reports.append({"fold": fold, "calendar": list(CALENDAR[97 + 40*fold:137 + 40*fold]),
                        "cells": [{"fold": fold, "phase": phase} for phase in metrics.PHASES],
                        "policy_rows": {arm: [cash_row(c, arm)] for arm in metrics.ARMS}})
    result = metrics.pool(CALENDAR, reports)
    assert len(result["cells"]) == 16
    assert result["policy"]["arms"]["direct"]["fills"] == 4
    expected_weeks = len({date.fromisoformat(d).strftime("%G-W%V") for d in CALENDAR[97:]})
    assert result["policy"]["arms"]["direct"]["calendar_weeks"] == expected_weeks
    with pytest.raises(ValueError):
        metrics.pool(CALENDAR, reports[::-1])
