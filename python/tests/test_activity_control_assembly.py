"""Synthetic BR control boundary checks; no private or market input reads."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json

import pytest

from research.activity_control_assembly import assemble_control_window
from research.activity_normal_assembly import accepted_period, assemble_window
from test_activity_normal_assembly import fixture


@pytest.mark.parametrize("baseline,formation_value", [(D(10), D(29)), (D(0), D(30))])
def test_control_includes_activity_failure_only_with_complete_nonactivity_inputs(baseline, formation_value):
    inputs = fixture()
    inputs["params"]["threshold"] = D("3.0")
    for day in inputs["calendar"][:60]:
        inputs["observations"][day] = replace(inputs["observations"][day], turnover=baseline)
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], turnover=formation_value)
    before = deepcopy(inputs)
    result = assemble_control_window(**inputs)
    assert inputs == before
    assert result["activity_screen"] == assemble_window(**inputs, evidence_label="BR control")
    for name in ("D1", "D2"):
        activity = result["activity_screen"]["arms"][name]
        control = result["control_arms"][name]
        assert activity["candidate"]["activity_pass"] is False
        assert activity["candidate"]["operating_baseline"] == "pass"
        assert activity["activity_median"] == str(baseline)
        assert activity["selected"] is False
        assert activity["exclusion_reasons"] == ["activity_predicate_failed"]
        assert activity["normal_dates"] == [day.isoformat() for day in inputs["calendar"][:60]]
        assert activity["skipped_frozen_dates"] == [day.isoformat() for day in inputs["calendar"][60:62]]
        assert activity["liquidity_dates"] == [day.isoformat() for day in inputs["calendar"][2:62]]
        assert control["eligible"] is True and control["exclusion_reasons"] == []
        assert control["selection_at"] == activity["selection_at"]
    assert result["classification_evidence_level"] == "inferred"
    assert result["historical_publication_evidence_level"] == "assumed"
    assert result["paired_control_eligibility_equal"] is True
    assert result["activity_screen"]["original_vintage_certificate"] is False
    assert result["activity_screen"]["inputs"]["activity"][inputs["formation"].isoformat()][
        "availability"]["source_public_available_at"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("failure,expected", [
    ("capitalization", "capitalization_below_floor"),
    ("liquidity", "absolute_liquidity_fail"),
    ("insufficient", "operating_baseline_insufficient"),
    ("formation_frozen", "formation_frozen"),
])
@pytest.mark.parametrize("baseline", [D(10), D(0)])
def test_removing_activity_never_removes_shared_exclusions(failure, expected, baseline):
    inputs = fixture(normal=59 if failure == "insufficient" else 60)
    for day, row in inputs["observations"].items():
        if day < inputs["formation"] and row.state == "observed":
            inputs["observations"][day] = replace(row, turnover=baseline)
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], turnover=D(1))
    if failure == "capitalization":
        inputs["capitalization"] = replace(inputs["capitalization"], value=D("4999999999999"))
    elif failure == "liquidity":
        for day, row in inputs["liquidity"].items():
            inputs["liquidity"][day] = replace(row, turnover=D("9999999999"))
    elif failure == "formation_frozen":
        inputs["observations"][inputs["formation"]] = replace(
            inputs["observations"][inputs["formation"]], state="frozen", turnover=D(0))
    result = assemble_control_window(**inputs)
    for name, control in result["control_arms"].items():
        assert control["eligible"] is False
        assert control["exclusion_reasons"] == [expected]
        assert result["activity_screen"]["arms"][name]["selected"] is False


@pytest.mark.parametrize("corruption", [
    "classification", "code", "isin", "required_dates", "start", "late_known",
    "known_flag", "raw_pin", "literal_passage", "listing", "late_activity", "late_liquidity",
    "late_capitalization", "missing_observation",
])
def test_numeric_activity_failure_cannot_replace_positive_evidence_or_source_guards(corruption):
    inputs = fixture()
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], turnover=D(1))
    record = inputs["accepted_record"]
    window = record["required_windows"][0]
    if corruption == "classification":
        record["decision"] = "excluded"
    elif corruption == "code":
        record["code"] = "000002"
    elif corruption == "isin":
        window["isin"] = "KR7000002000"
    elif corruption == "required_dates":
        window["required_dates"].pop(60)  # Frozen dates also require bounded evidence.
    elif corruption == "start":
        window["effective_start_for_this_adjudication"] = inputs["calendar"][1].isoformat()
    elif corruption == "late_known":
        record["known_on_conservative_candidate"] = (inputs["formation"] + timedelta(days=1)).isoformat()
    elif corruption == "known_flag":
        window["known_before_formation"] = False
    elif corruption == "raw_pin":
        record["raw_sha256"] = "unverified"
    elif corruption == "literal_passage":
        record["passages"][0]["literal_text_present_in_raw_html"] = False
    elif corruption == "listing":
        inputs["listing_date"] = inputs["calendar"][1]
    elif corruption == "missing_observation":
        inputs["observations"][inputs["calendar"][0]] = replace(
            inputs["observations"][inputs["calendar"][0]], state="missing", turnover=None)
    else:
        source = corruption.removeprefix("late_")
        rows = inputs["observations"] if source == "activity" else inputs["liquidity"]
        day = inputs["formation"] if source == "capitalization" else sorted(rows)[0]
        row = inputs["capitalization"] if source == "capitalization" else rows[day]
        late = replace(row, availability=replace(row.availability,
                                                available_at=row.availability.available_at + timedelta(seconds=1)))
        if source == "capitalization":
            inputs["capitalization"] = late
        else:
            rows[day] = late
    with pytest.raises(ValueError):
        assemble_control_window(**inputs)


@pytest.mark.parametrize("lookback,threshold", [(59, D("3.0")), (60, 0), (60, D("2.9")),
                                               (60, 3.0), (60, True), (True, 3)])
def test_control_does_not_change_original_activity_math(lookback, threshold):
    inputs = fixture()
    inputs["params"] = {"lookback": lookback, "threshold": threshold}
    with pytest.raises(ValueError, match="unchanged"):
        assemble_control_window(**inputs)


def test_evidence_label_preserves_bk_default_and_identifies_new_br_record():
    inputs = fixture()
    original = assemble_window(**inputs)
    assert original == assemble_window(**inputs, evidence_label="BK")
    assert original["identity_period"]["source"].startswith("BK bounded inferred decision ")
    result = assemble_control_window(**inputs)
    br_audit = result["activity_screen"]
    assert br_audit["identity_period"]["source"].startswith("BR control bounded inferred decision ")
    br_audit["identity_period"]["source"] = original["identity_period"]["source"]
    assert br_audit == original
    with pytest.raises(ValueError, match="evidence label"):
        accepted_period(inputs["accepted_record"], formation=inputs["formation"], code=inputs["code"],
                        isin=inputs["isin"], required_dates=tuple(inputs["observations"]), evidence_label="")


def test_exact_large_decimal_inputs_and_half_medians_are_retained():
    inputs = fixture(frozen=0)
    a = D("10000000000000000000000000000000000000000")
    b = D("10000000000000000000000000000000000000001")
    midpoint = "10000000000000000000000000000000000000000.5"
    inputs["params"]["threshold"] = D("3.0")
    for index, day in enumerate(inputs["calendar"][:60]):
        value = a if index < 30 else b
        inputs["observations"][day] = replace(inputs["observations"][day], turnover=value)
        inputs["liquidity"][day] = replace(inputs["liquidity"][day], turnover=value)
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], turnover=b)
    inputs["capitalization"] = replace(inputs["capitalization"], value=b)
    result = assemble_control_window(**inputs)
    for name, control in result["control_arms"].items():
        activity = result["activity_screen"]["arms"][name]
        assert activity["activity_median"] == activity["liquidity_median"] == midpoint
        assert activity["candidate"]["activity_pass"] is False
        assert control["eligible"] is True
    assert result["activity_screen"]["inputs"]["official_capitalization"]["value"] == str(b)
