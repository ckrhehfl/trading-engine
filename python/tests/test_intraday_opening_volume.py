"""Invented joined CT/CX reports only; no saved bodies, DB or network access."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
import json

import pytest

from research import intraday_opening_volume as calibration
from test_intraday_activity_readiness import schema
from test_intraday_opening_range_calibration import inputs, pattern, raw_rows, SYMBOLS

ct, cx = calibration.ct, calibration.cx


def weekdays(count=27, start="2025-09-04"):
    day, result = date.fromisoformat(start), []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def build_synthetic_sources(days=None, *, volume=None, selected_unknown=False,
        sparse_volatile=False, full_union=False):
    """Generate deterministic raw invented chunks afresh, avoiding a raw graph."""
    days = list(days or weekdays())
    cb, _ = inputs(days)
    if full_union:
        for index, membership in enumerate(cb["variants"]["D+2"]["days"][:18]):
            membership["targets"][-1]["symbol"] = f"KRX:{21 + index % 11:06d}"
    union = {day: sorted({target["symbol"] for variant in cb["variants"].values()
        for membership in variant["days"] if membership["session"] == day for target in membership["targets"]}) for day in days}
    positions = {day: index for index, day in enumerate(days)}
    def saved_chunks():
        for day in days:
            index = positions[day]
            for symbol in union[day]:
                number = int(symbol[-6:])
                rows = []
                if index != len(days) - 1 and number <= 11:
                    rows = pattern(day, symbol, "earlier")[:71] if number <= 9 else (
                        pattern(day, symbol, "early_only")[:235] if number == 10 else raw_rows(day, symbol, 235))
                    delta = Decimal((index + number) % 5 - 2) / 1000
                    if number <= 10:
                        rows[55].update(open=str(Decimal(100) + delta), high="101", low="99")
                    if number <= 9:
                        rows[70].update(open=str(Decimal(100) - delta / 2), high="101", low="99")
                    if sparse_volatile and number == 1 and index >= 20:
                        rows[55].update(open="101" if index % 2 else "99")
                    if number == 9:
                        rows.pop(70)
                    if selected_unknown and number == 1 and index == 21:
                        rows.pop(55)
                yield {"symbol": symbol, "session": day, "observation_date": day,
                    "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00",
                    "source": "collector klines", "is_final": None, "evidence_level": "assumed", "rows": rows}
    def current_chunks():
        for chunk in saved_chunks():
            index, number = positions[chunk["session"]], int(chunk["symbol"][-6:])
            amount = volume(index, number) if volume else (
                "20" if index >= 20 and number == 1 else "19.999999999999999999999999999"
                if index >= 20 and number == 2 else "21" if index >= 20 and number == 3 else "10")
            yield {**chunk, "retrieved_at": "2026-10-23T00:00:00+00:00", "rows": [
                {"open_time_ms": row["open_time_ms"], "fetched_at": row["fetched_at"],
                    "volume": amount, "quote_volume": None} for row in chunk["rows"]],
                "daily_rows": [], "daily_retrieved_at": "2026-10-23T00:00:00+00:00"}
    original = json.loads(ct._canonical(ct.build_calibration(cb, saved_chunks())))
    connection, inventory = schema()
    connection.close()
    activity = json.loads(ct._canonical(cx.build_readiness(cb, saved_chunks(), current_chunks(), inventory)))
    return original, activity


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_sources()


def sources():
    return deepcopy(_base())


def specification():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE,
        "window": calibration.WINDOW, "parameters": deepcopy(calibration.PARAMETERS),
        "resources": calibration.RESOURCES, **calibration.REGISTERED_PATHS,
        "upstream_sha256": calibration.UPSTREAM_SHA256,
        "upstream_specification_sha256": calibration.UPSTREAM_SPECIFICATION_SHA256,
        "activity_sha256": calibration.ACTIVITY_SHA256,
        "activity_specification_sha256": calibration.ACTIVITY_SPECIFICATION_SHA256}


def test_exact_registration_and_repo_specification():
    calibration.validate_spec(specification())
    calibration.validate_spec(json.loads(calibration.SPECIFICATION_PATH.read_bytes()))
    assert calibration.ARMS == ("15",)
    assert calibration.PARAMETERS["next_hypothesis"] == cx.NEXT_HYPOTHESIS
    assert calibration.RESOURCES["cpu_seconds"] == 300
    assert calibration.RESOURCES["address_space_mib"] == 512
    assert calibration.RESOURCES["automatic_retries"] == 0


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(activity_sha256="a" * 64),
    lambda x: x.update(upstream_sha256="a" * 64),
    lambda x: x["parameters"].update(arms=["15", "30"]),
    lambda x: x["parameters"].update(volume_ratio_threshold={"numerator": "3", "denominator": "1"}),
    lambda x: x["parameters"]["next_hypothesis"].update(price_impulse_conjunct=True),
    lambda x: x["resources"].update(cpu_seconds=600),
    lambda x: x.update(output_directory="/tmp/new"),
])
def test_registration_refuses_changes(mutate):
    spec = deepcopy(specification())
    mutate(spec)
    with pytest.raises(calibration.OpeningVolumeRefusal):
        calibration.validate_spec(spec)


def test_exact_ratio_boundary_and_original_first_signal_are_preserved():
    original, activity = sources()
    before = deepcopy((original, activity))
    with localcontext() as context:
        context.prec = 3
        result = calibration.build_calibration(original, activity)
    assert (original, activity) == before
    for policy, variant in result["variants"].items():
        selected_day = variant["days"][20 * 20:21 * 20]
        assert [row["feature"]["status"] for row in selected_day[:3]] == ["selected", "rejected", "selected"]
        assert selected_day[0]["feature"]["opening_volume_ratio"] == 2
        assert selected_day[1]["feature"]["opening_volume_ratio"] < 2
        for original_row, row in zip(original["variants"][policy]["days"], variant["days"], strict=True):
            assert row["original_finding"] == original_row["arms"]["15"]
            assert row["membership_target"] == original_row["membership_target"]
            assert row["membership_source"] == original_row["membership_source"]
            feature = row["feature"]
            assert feature["data_vintage"]["price_chunk_sha256"] == original_row["data_vintage"]
            assert feature["retrieved_at"] == "2026-10-23T00:00:00+00:00"
            assert row["price_source"]["raw_chunk_metadata"]["retrieved_at"] == "2026-10-20T01:00:00+00:00"
            if original_row["arms"]["15"]["status"] == "signal":
                assert calibration.cu._instant(feature["available_at"]) <= calibration.cu._instant(row["original_finding"]["event"]["decision_at"])
    assert result["source_links"]["opening_range"]["by_policy"]["D+1"] == original["variants"]["D+1"]["by_arm"]
    assert result["source_links"]["activity_readiness"]["summary"] == activity["summary"]


def test_original_and_feature_unknowns_and_known_no_signal_stay_separate():
    result = calibration.build_calibration(*sources())
    for variant in result["variants"].values():
        summary = variant["by_arm"]["15"]
        assert summary["unknown_origin_counts"]["original_signal_unknown"] > 0
        assert summary["unknown_origin_counts"]["feature_unavailable_on_original_signal"] > 0
        for row in variant["days"]:
            state = row["original_finding"]["status"]
            if state == "unknown":
                assert row["feature"]["status"] == "unknown"
                assert row["feature"]["unknown_reasons"] == ["original_signal_unknown"]
            elif state == "known_no_signal":
                assert row["feature"]["status"] == "not_applicable"
            if row["feature"]["status"] in {"unknown", "not_applicable"}:
                assert row["feature"]["opening_volume_ratio"] is None


def test_all_dates_weeks_months_zero_dates_own_sizing_and_loose_upper_bound():
    original, activity = sources()
    result = calibration.build_calibration(original, activity)
    for variant in result["variants"].values():
        rows, summary = variant["days"], variant["by_arm"]["15"]
        assert len(rows) == len(original["reference_sessions"]) * 20
        assert sum(summary["selection_counts"].values()) == len(rows)
        assert set(summary["by_week"]) == set(result["reference_weeks"])
        assert set(summary["by_month"]) == {day[:7] for day in result["reference_sessions"]}
        totals = [value["selection_counts"]["selected"] for value in summary["by_week"].values()]
        assert summary["frequency"]["weekly_selected_min"] == min(totals)
        assert summary["frequency"]["weekly_selected_max"] == max(totals)
        assert summary["frequency"]["selected_per_reference_week"] == Fraction(sum(totals), len(totals))
        for counts in summary["by_week"].values():
            assert counts["selected_plus_unknown_opportunity_upper_bound"] == counts["selection_counts"]["selected"] + counts["selection_counts"]["unknown"]
        records = [{"session": row["session"], "label": None if row["original_finding"]["label"]["status"] == "unknown" else
            calibration.cu._ratio(row["original_finding"]["label"]["price_change"])} for row in rows if row["feature"]["status"] == "selected"]
        assert summary["own_label_dispersion"] == ct.cc.dispersion(records, result["reference_sessions"])
        assert summary["own_label_dispersion"]["reference_sessions"] == len(result["reference_sessions"])


def test_zero_baseline_unavailable_and_observed_zero_current_rejected():
    for volume, expected in ((lambda *_: "0", "unknown"),
            (lambda index, _: "0" if index >= 20 else "10", "rejected")):
        original, activity = build_synthetic_sources(volume=volume)
        result = calibration.build_calibration(original, activity)
        assert result["variants"]["D+1"]["days"][400]["feature"]["status"] == expected


def test_special_opening_mismatch_never_replaces_required_prior_sessions():
    original, activity = build_synthetic_sources(weekdays(27, "2025-11-04"))
    result = calibration.build_calibration(original, activity)
    final = result["variants"]["D+1"]["days"][-40]["feature"]
    assert len(final["prior_sessions"]) == 20
    assert "2025-11-13" in final["prior_sessions"]
    assert final["status"] == "unknown" and final["opening_volume_ratio"] is None


def test_selected_unknown_labels_and_new_own_shortfall_keep_their_own_choice():
    result = calibration.build_calibration(*build_synthetic_sources(selected_unknown=True))
    for variant in result["variants"].values():
        assert variant["by_arm"]["15"]["label_counts"]["unknown"] == 1
        assert "unknown_own_labels" in variant["by_arm"]["15"]["comparison_choice_reasons"]
    original, activity = build_synthetic_sources(sparse_volatile=True,
        volume=lambda index, number: "20" if index >= 20 and number == 1 else "10")
    result = calibration.build_calibration(original, activity)
    assert original["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] < 33
    assert result["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] > 33
    assert result["requires_operator_choice_before_comparison"] is True


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(study_id="wrong"),
    lambda x: x.update(performance_calculated=True),
    lambda x: x["target_days"].pop(),
    lambda x: x["target_days"][0].update(saved_chunk_sha256="a" * 64),
    lambda x: x["target_days"][0].update(current_rows=0),
    lambda x: x["target_days"][0].update(source_fetched_at=["different"]),
    lambda x: x["target_days"][0]["bucket_comparison"].update(current_only=1),
    lambda x: x["target_days"][0]["field_counts"]["volume"].update(zero=1),
    lambda x: x["target_days"][0]["opening_prefixes"]["15"].update(volume_sum={"numerator":"-1","denominator":"1"}),
    lambda x: x["target_days"][0]["next_hypothesis_feature_readiness"].update(state="ready"),
    lambda x: x["summary"].update(feature_ready_union=0),
    lambda x: x["variants"]["D+1"]["days"][0]["targets"][0].update(feature_readiness="ready"),
])
def test_complete_activity_validation_even_outside_selected_subset(mutate):
    original, activity = sources()
    mutate(activity)
    with pytest.raises(calibration.OpeningVolumeRefusal):
        calibration.build_calibration(original, activity)


def test_rejected_first_signal_still_enforces_feature_causality(monkeypatch):
    original, activity = sources()
    old = calibration.ct.cc.session_open
    # The independent source validation is real; move the feature's opening
    # clock only after the whole source pass to test the dedicated causal guard.
    baseline = calibration._validate_activity(original, activity, lambda: None)
    row = original["variants"]["D+1"]["days"][401]
    monkeypatch.setattr(calibration.ct.cc, "session_open", lambda day: old(day) + timedelta(hours=1))
    with pytest.raises(calibration.OpeningVolumeRefusal, match="noncausal"):
        calibration._feature(row, baseline[0][row["session"], row["symbol"]], baseline[1][row["session"], row["symbol"]])


def test_no_new_performance_statistics_and_checkpoint_failure_propagates():
    result = calibration.build_calibration(*sources())
    forbidden = {"gross_mean", "net_mean", "gross_sum", "win_rate", "comparison", "contrasts", "operator_authorization"}
    def visit(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result)
    def stop():
        raise RuntimeError("invented_budget_stop")
    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        calibration.build_calibration(*sources(), checkpoint=stop)
