"""Invented fixed-calendar ledgers for the EG entry comparison."""

from datetime import date, timedelta
from fractions import Fraction

import pytest

from research.intraday_campaign_readiness import _week
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_highvol_coverage import project
from research.intraday_highvol_policy import ARMS, EntryForecast, compare_entry_policies, entry_decision
from research.intraday_pattern_calibration import session_close, session_open


START = date(2025, 7, 1)
DAYS = tuple(day.isoformat() for i in range(370)
             if (day := START + timedelta(days=i)).weekday() < 5)[:257]
FIRST = DAYS[100]


def frames(day=FIRST, *, symbol="KRX:000001", offset=15, branch="A", state="confirmed",
           timings=("S1", "S2"), peers=("S1", "S2"), chart=True, delay=10,
           immediate_status="available", scenario_status="admitted", gross=Fraction(1, 200),
           touch="target_first"):
    key = [day, symbol, offset]
    high_decision = session_open(day) + (offset + 2) * MINUTE
    high_entry = high_decision + MINUTE
    high_exit = high_entry + 30 * MINUTE
    high_inside = high_exit < session_close(day) - 15 * MINUTE
    high_status = immediate_status if high_inside else "outside_clock"
    immediate = {"entry_at": high_entry, "exit_at": high_exit, "status": high_status,
                 "gross": Fraction(1, 1000) if high_status == "available" else None,
                 "entry_open": None if high_status == "outside_clock" else Fraction(100)}
    feature = {"key": key, "timings": list(timings), "entry": None,
               "attempt": {"scenario": branch, "state": state, "confirmation": None},
               "immediate": {"decision_at": high_decision, "entry_at": high_entry, "exit_at": high_exit,
                             "status": "supported" if high_inside else "outside_clock"}}
    label = {"key": key, "immediate": immediate, "primary": None}
    if state == "confirmed":
        decision = high_decision + delay * MINUTE
        entry, exit_at = decision + MINUTE, decision + 31 * MINUTE
        inside = exit_at < session_close(day) - 15 * MINUTE
        status = scenario_status if inside else "outside_clock"
        feature["entry"] = {"decision_at": decision, "primary_clock_supported": inside,
                            "chart": {"status": "available" if chart else "unavailable"},
                            "peers": {timing: {"status": "available" if timing in peers else "unavailable"}
                                      for timing in timings}}
        feature["attempt"]["confirmation"] = {"decision_at": decision, "intended_entry_at": entry}
        label["primary"] = {"status": status, "entry_at": entry, "exit_at": exit_at,
                            "fixed_gross": gross if status == "admitted" else None,
                            "first_touch": {"status": touch} if status == "admitted" else None}
    return project(feature), project(label)


def forecast_key(frame, arm="LGB_CHART", timing="S1"):
    return frame["attempt"]["scenario"], timing, arm, (*frame["key"], 0)


def compare(pairs, forecasts, **kwargs):
    labels = {tuple(frame["key"]): label for frame, label in pairs}
    return compare_entry_policies(DAYS, (frame for frame, _ in pairs), labels_at=labels.__getitem__,
                                  forecasts=forecasts, **kwargs)


def test_fixed_dates_and_separate_support_before_label_reads():
    training, full, chart, control = (frames(DAYS[99]), frames(peers=("S1",)),
                                      frames(symbol="KRX:000002", chart=False),
                                      frames(symbol="KRX:000003", branch=None, state="unsupported"))
    pairs = (training, full, chart, control)
    keys_read = []
    labels = {tuple(frame["key"]): label for frame, label in pairs[1:]}

    def features():
        for frame, _ in pairs:
            assert not keys_read
            yield frame

    def label(key):
        keys_read.append(key)
        return labels[key]

    forecasts = {forecast_key(full[0], timing=timing): EntryForecast(.50) for timing in ("S1", "S2")}
    result = compare_entry_policies(DAYS, features(), labels_at=label, forecasts=forecasts, include_chart_only=True)
    assert keys_read == list(labels)
    assert result["reference_sessions"] == list(DAYS[100:])
    s1, s2 = (result["groups"]["A"][timing] for timing in ("S1", "S2"))
    assert s1["support_states"] == {"common": 1, "chart_unavailable": 1}
    assert s2["support_states"] == {"chart_only": 1, "chart_unavailable": 1}
    assert s1["common"]["opportunities"] == s2["wider_chart_diagnostic"]["opportunities"] == 1
    assert s2["common"]["opportunities"] == 0
    assert set(s2["wider_chart_diagnostic"]["costs"]["33"]["arms"]) == {"rule", "LGB_CHART", "TCN_CHART"}
    assert result["immediate"]["S1"]["observed_clusters"] == 3
    assert result["immediate"]["S1"]["attempt_states"] == {"confirmed": 2, "unsupported": 1}
    assert result["immediate"]["S1"]["costs"]["33"]["outcome_counts"] == {"available": 3}
    attribution = s1["matched_timing_attribution"]
    assert attribution["known_matched_outcomes"] == 1
    assert attribution["scenario_minus_immediate_pp"]["mean"] == .4


def test_threshold_ambiguity_refused_unknown_and_paired_opportunity_costs():
    pairs = (frames(symbol="KRX:000001"), frames(symbol="KRX:000002", gross=None, touch="same_minute_unknown"),
             frames(symbol="KRX:000003", scenario_status="entry_refused"),
             frames(symbol="KRX:000004", scenario_status="entry_unavailable"))
    scores = (.33, .50, .50, .50)
    forecasts = {forecast_key(frame): EntryForecast(score, .80, Fraction(1, 2),
                 frozenset({43}) if i == 0 else frozenset()) for i, ((frame, _), score) in enumerate(zip(pairs, scores))}
    group = compare(pairs, forecasts)["groups"]["A"]["S1"]["common"]
    first, second = (group["costs"][cost] for cost in ("33", "43"))
    assert entry_decision(EntryForecast(.43, 1.0), 43) == "declined"
    assert first["arms"]["LGB_CHART"]["outcome_counts"] == {
        "declined": 1, "fixed_exit_unavailable": 1, "entry_refused": 1, "entry_unavailable": 1}
    assert second["arms"]["LGB_CHART"]["outcome_counts"]["replay_ambiguous"] == 1
    assert first["arms"]["LGB_CHART"]["net_pp"]["mean"] is None
    assert first["arms"]["TCN_CHART"]["outcome_counts"] == {"prediction_unavailable": 4}
    assert first["arms"]["rule"]["gross_pp"]["mean"] == .5
    assert first["arms"]["rule"]["net_pp"]["mean"] == .17
    pair = first["contrasts"]["LGB_CHART_minus_rule"]
    assert pair["known_matched_opportunities"] == pair["unknown_matched_opportunities"] == 2
    assert pair["net_pp"]["left_minus_right"]["mean"] == -.085
    assert second["contrasts"]["LGB_CHART_minus_rule"]["known_matched_opportunities"] == 1
    reliability = group["target_first_reliability"]["LGB_CHART"]
    assert reliability["bins"][4]["rows"] == 1
    assert reliability["bins"][4]["upper_inclusive"] is True
    assert reliability["statuses"]["target_same_minute_unknown"] == 1
    assert reliability["training_prevalence_brier_same_pairs"]["mean"] == .25
    assert reliability["model_minus_training_prevalence_brier_same_pairs"]["mean"] == pytest.approx(-.21)


def test_fixed_selection_stress_charges_43_without_filtering_33bp_entries():
    pair = frames()
    forecasts = {forecast_key(pair[0]): EntryForecast(.40, replay_ambiguous_costs=frozenset({43}))}
    common = compare((pair,), forecasts)["groups"]["A"]["S1"]["common"]
    assert common["costs"]["33"]["arms"]["LGB_CHART"]["net_pp"]["mean"] == .17
    assert common["costs"]["43"]["arms"]["LGB_CHART"]["outcome_counts"] == {"replay_ambiguous": 1}
    stress = common["fixed_selection_stress"]
    assert (stress["selection_bps"], stress["outcome_bps"]) == (33, 43)
    assert stress["arms"]["LGB_CHART"]["net_pp"]["mean"] == .07
    assert stress["contrasts"]["LGB_CHART_minus_rule"]["known_matched_opportunities"] == 1


def test_week_frequency_distinguishes_overlaps_and_counts_zero_weeks():
    monday = next(day for day in DAYS[100:] if date.fromisoformat(day).weekday() == 0)
    start = DAYS.index(monday)
    pairs = tuple(frames(day, offset=offset, timings=("S1",))
                  for day in DAYS[start:start + 4] for offset in (15, 25, 35, 45, 55))
    forecasts = {forecast_key(frame, arm): EntryForecast(.60) for frame, _ in pairs for arm in ARMS}
    result = compare(pairs, forecasts)
    group = result["groups"]["A"]["S1"]["common"]
    arm = group["costs"]["43"]["arms"]["TCN_CHART_PEER"]
    week = arm["weeks"][_week(monday)]
    assert group["first_hour_onsets"] == 20
    assert (week["selected"], week["same_symbol_nonoverlapping_selected"], week["modeled_entries"]) == (20, 8, 20)
    assert week["at_least_15_selected"] and not week["at_least_15_nonoverlapping_selected"]
    assert sum(value["reference_sessions"] for value in arm["weeks"].values()) == 157
    assert arm["weeks"][_week(DAYS[-1])]["known_returns"] == 0
    assert arm["concentration"]["symbol"]["largest_known_return_share"] == 1
    assert arm["concentration"]["session"]["largest_known_return_share"] == .25


def test_outside_clock_distinct_from_observed_unavailable_and_model_unsupported():
    regular = frames(branch="B", immediate_status="unavailable", gross=None)
    late = frames("2026-01-02", offset=300, branch="B", delay=30)
    forecasts = {forecast_key(regular[0]): EntryForecast(unsupported_reason="fold_unsupported")}
    result = compare((regular, late), forecasts)
    scenario = result["groups"]["B"]["S1"]
    assert scenario["support_states"] == {"common": 1, "outside_clock": 1}
    assert scenario["common"]["forecast_statuses"]["LGB_CHART"] == {"fold_unsupported": 1}
    control = result["immediate"]["S1"]["costs"]["43"]
    assert control["outcome_counts"] == {"fixed_exit_unavailable": 1, "outside_clock": 1}


def test_unsealed_forecast_rejected_before_outcome_callback():
    frame, _ = frames()
    def label(_):
        pytest.fail("invalid forecast must not open a label")

    invalid = (EntryForecast(float("nan")), EntryForecast(.50, training_target_rate=float("nan")))
    for value in invalid:
        with pytest.raises(InputRefusal, match="invalid_eg_entry_forecast"):
            compare_entry_policies(DAYS, (frame,), labels_at=label, forecasts={forecast_key(frame): value})
    off_calendar, _ = frames(DAYS[99])
    with pytest.raises(InputRefusal, match="eg_forecast_outside_sealed_evaluation_support"):
        compare_entry_policies(DAYS, (frame,), labels_at=label,
                               forecasts={forecast_key(off_calendar): EntryForecast(.50)})
