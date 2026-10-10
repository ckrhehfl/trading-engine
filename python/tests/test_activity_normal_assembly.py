"""Synthetic BL boundary tests; no evidence, market, DB or historical-log IO."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
import json

import pytest

from research.activity_normal_assembly import accepted_period, assemble_window
from research.activity_screen import PublishedValue, ScreenObservation
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy


def fixture(*, frozen=2, normal=60):
    index = normal + frozen
    calendar = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(index + 4))
    formation = calendar[index]
    cutoff = SessionLagPolicy(1).selection_at(calendar, index)

    def metadata(day, source):
        return AvailabilityMetadata(
            day, cutoff, "unknown", source, None, None, "assumed",
            availability_policy="fixed next-session assumption",
            evidence_reference="synthetic source pin",
        )

    observations = {
        day: ScreenObservation("observed", D(10), None, metadata(day, "pinned BI turnover"))
        for day in calendar[:index + 1]
    }
    for day in calendar[normal:index]:
        observations[day] = replace(observations[day], state="frozen", turnover=D(0))
    observations[formation] = replace(observations[formation], turnover=D(30))
    liquidity = {
        day: ScreenObservation("observed", D("20000000000"), None,
                               metadata(day, "official KRX ACC_TRDVAL"))
        for day in calendar[index - 60:index]
    }
    for day in calendar[normal:index]:
        if day in liquidity:
            liquidity[day] = replace(liquidity[day], state="frozen", turnover=D(0))
    accepted = {
        "code": "000001", "decision": "accepted", "continuity_evidence_level": "inferred",
        "source_inventory_index": 0, "source_url": "https://dart.fss.or.kr/synthetic",
        "receipt": "synthetic-overview", "report_period": "2019.09",
        "published_on": "2019-11-14", "raw_sha256": "a" * 64,
        "readable_sha256": "b" * 64, "known_on_conservative_candidate": "2019-11-15",
        "passages": [{"readable_line": 17, "text": "Synthetic operating common-share issuer.",
                      "literal_text_present_in_raw_html": True}],
        "rationale": "Synthetic exact issue and positive operating evidence.",
        "source_supported_business_anchor": {"claim": "Synthetic operating history"},
        "accepted_boundary_basis": "bounded required dates only",
        "known_on_basis": "following calendar date convention",
        "required_windows": [{
            "window_id": f"{formation:%Y%m%d}/000001", "isin": "KR7000001000",
            "formation": formation.isoformat(),
            "effective_start_for_this_adjudication": calendar[0].isoformat(),
            "effective_end_exclusive": (formation + timedelta(days=1)).isoformat(),
            "required_dates": [day.isoformat() for day in calendar[:index + 1]],
            "known_before_formation": True,
            "krx_metadata_provenance": {
                "code": "000001", "formation": formation.strftime("%Y%m%d"),
                "source_observation_position": 4, "source_response_sha256": "c" * 64,
                "source_row_position": 12, "source_service": "stk_isu_base_info",
            },
        }],
    }
    return dict(
        calendar=calendar, formation=formation, code="000001", isin="KR7000001000",
        listing_date=date(2010, 1, 1), observations=observations,
        capitalization=PublishedValue(D("5000000000000"), None,
                                      metadata(formation, "official KRX capitalization")),
        liquidity=liquidity, accepted_record=accepted, params={"lookback": 60, "threshold": 3},
    )


def test_exact_normal_and_frozen_dates_use_separate_fixed_official_liquidity():
    inputs = fixture()
    result = assemble_window(**inputs)
    calendar = inputs["calendar"]
    for name, lag in (("D1", 1), ("D2", 2)):
        arm = result["arms"][name]
        assert arm["decision_on"] == calendar[62 + lag].isoformat()
        assert arm["selection_at"] == SessionLagPolicy(lag).selection_at(calendar, 62).isoformat()
        assert arm["normal_dates"] == [day.isoformat() for day in calendar[:60]]
        assert arm["skipped_frozen_dates"] == [day.isoformat() for day in calendar[60:62]]
        assert arm["liquidity_dates"] == [day.isoformat() for day in calendar[2:62]]
        assert arm["activity_median"] == "10" and arm["activity_ratio"] == "3"
        assert arm["liquidity_median"] == "20000000000"
        assert arm["liquidity_zero_count"] == arm["liquidity_frozen_count"] == 2
        assert arm["selected"] and arm["exclusion_reasons"] == []
    assert result["paired_eligibility_and_dates_equal"] is True
    assert result["identity_period"]["start"] == calendar[0].isoformat()
    assert result["identity_period"]["end"] == calendar[63].isoformat()
    assert result["classification_evidence_level"] == "inferred"
    assert result["original_vintage_certificate"] is False
    assert result["listing"]["known_on"] == inputs["formation"].isoformat()
    assert result["listing"]["evidence_level"] == "assumed"
    assert result["accepted_evidence"] == inputs["accepted_record"]
    json.dumps(result, allow_nan=False)


def test_all_field_provenance_and_unknown_original_times_are_preserved_without_aliases():
    inputs = fixture()
    result = assemble_window(**inputs)
    first = result["inputs"]["activity"][inputs["calendar"][0].isoformat()]
    assert first["public_available_at"] is None
    assert first["availability"] == {
        "observation_date": inputs["calendar"][0].isoformat(),
        "available_at": SessionLagPolicy(1).selection_at(inputs["calendar"], 62).isoformat(),
        "retrieved_at": "unknown", "source": "pinned BI turnover", "is_final": None,
        "data_vintage": None, "evidence_level": "assumed",
        "source_public_available_at": None,
        "availability_policy": "fixed next-session assumption",
        "evidence_reference": "synthetic source pin",
    }
    inputs["accepted_record"]["passages"][0]["text"] = "changed after call"
    assert result["accepted_evidence"]["passages"][0]["text"] != "changed after call"


def test_formation_known_retrospective_evidence_does_not_reset_or_delay_baseline():
    inputs = fixture()
    known = inputs["formation"] - timedelta(days=1)
    inputs["accepted_record"]["known_on_conservative_candidate"] = known.isoformat()
    result = assemble_window(**inputs)
    assert result["identity_period"]["known_on"] == known.isoformat()
    assert result["arms"]["D1"]["normal_dates"][0] == inputs["calendar"][0].isoformat()
    assert result["arms"]["D1"]["selected"] is True


@pytest.mark.parametrize("change", [
    "code", "decision", "continuity", "isin", "window_id", "provenance_code",
    "provenance_formation", "duplicate", "missing", "late_known", "known_flag",
    "start", "end", "required_dates", "raw_pin", "readable_pin", "passage", "passage_line",
])
def test_missing_conflicting_late_or_unbounded_bk_decisions_fail_closed(change):
    inputs = fixture()
    record = inputs["accepted_record"]
    window = record["required_windows"][0]
    if change == "code":
        record["code"] = "000002"
    elif change == "decision":
        record["decision"] = "unresolved"
    elif change == "continuity":
        record["continuity_evidence_level"] = "confirmed"
    elif change == "isin":
        window["isin"] = "KR7000002000"
    elif change == "window_id":
        window["window_id"] = f"{inputs['formation']:%Y%m%d}/000002"
    elif change == "provenance_code":
        window["krx_metadata_provenance"]["code"] = "000002"
    elif change == "provenance_formation":
        window["krx_metadata_provenance"]["formation"] = "20190101"
    elif change == "duplicate":
        record["required_windows"].append(deepcopy(window))
    elif change == "missing":
        record["required_windows"] = []
    elif change == "late_known":
        record["known_on_conservative_candidate"] = (inputs["formation"] + timedelta(days=1)).isoformat()
    elif change == "known_flag":
        window["known_before_formation"] = False
    elif change == "start":
        window["effective_start_for_this_adjudication"] = "2019-12-31"
    elif change == "end":
        window["effective_end_exclusive"] = inputs["formation"].isoformat()
    elif change == "required_dates":
        window["required_dates"].pop(1)
    elif change in {"raw_pin", "readable_pin"}:
        record[change.replace("_pin", "_sha256")] = "unverified"
    elif change == "passage":
        record["passages"][0]["literal_text_present_in_raw_html"] = False
    else:
        record["passages"][0]["readable_line"] = True
    with pytest.raises(ValueError, match="BK"):
        assemble_window(**inputs)


@pytest.mark.parametrize("change", ["gap", "future", "outside", "liquidity_gap", "liquidity_future", "listing"])
def test_exact_window_scope_refuses_stale_extra_missing_or_prelisting_values(change):
    inputs = fixture()
    if change == "gap":
        del inputs["observations"][inputs["calendar"][1]]
    elif change == "future":
        inputs["observations"][inputs["calendar"][63]] = inputs["observations"][inputs["formation"]]
    elif change == "outside":
        inputs["observations"][date(2019, 12, 31)] = inputs["observations"][inputs["formation"]]
    elif change == "liquidity_gap":
        inputs["liquidity"].pop(inputs["calendar"][2])
    elif change == "liquidity_future":
        inputs["liquidity"][inputs["formation"]] = inputs["liquidity"][inputs["calendar"][2]]
    else:
        inputs["listing_date"] = inputs["calendar"][1]
    with pytest.raises(ValueError):
        assemble_window(**inputs)


@pytest.mark.parametrize("source", ["activity", "liquidity", "capitalization"])
@pytest.mark.parametrize("change", ["missing", "late", "wrong_date", "publication_conflict"])
def test_every_consumed_source_requires_cutoff_and_consistent_field_provenance(source, change):
    inputs = fixture()
    if source == "capitalization":
        row = inputs["capitalization"]
    else:
        mapping = inputs["observations"] if source == "activity" else inputs["liquidity"]
        day = sorted(mapping)[0]
        row = mapping[day]
    if change == "missing":
        altered = replace(row, availability=None)
    elif change == "late":
        altered = replace(row, availability=replace(row.availability, available_at=(
            row.availability.available_at + timedelta(seconds=1))))
    elif change == "wrong_date":
        altered = replace(row, availability=replace(row.availability, observation_date=date(2019, 12, 31)))
    else:
        altered = replace(row, public_available_at=row.availability.available_at)
    if source == "capitalization":
        inputs["capitalization"] = altered
    else:
        mapping[day] = altered
    with pytest.raises(ValueError):
        assemble_window(**inputs)


def test_verified_normal_zero_remains_in_baseline_and_blocks_activity():
    inputs = fixture()
    for day in inputs["calendar"][:60]:
        inputs["observations"][day] = replace(inputs["observations"][day], turnover=D(0))
    arm = assemble_window(**inputs)["arms"]["D1"]
    assert arm["activity_median"] == "0" and arm["activity_ratio"] is None
    assert arm["candidate"]["operating_baseline"] == "pass"
    assert arm["selected"] is False
    assert arm["exclusion_reasons"] == ["activity_predicate_failed"]


def test_complete_short_normal_history_is_insufficient_without_zero_fill():
    inputs = fixture(frozen=2, normal=59)
    arm = assemble_window(**inputs)["arms"]["D1"]
    assert len(arm["normal_dates"]) == 59
    assert arm["activity_median"] is arm["activity_ratio"] is None
    assert arm["candidate"]["operating_baseline"] == "insufficient"
    assert arm["candidate"]["absolute_liquidity"] == "pass"
    assert arm["exclusion_reasons"] == ["operating_baseline_insufficient", "activity_predicate_failed"]


def test_exact_huge_half_medians_do_not_pass_through_float_or_default_decimal_rounding():
    inputs = fixture(frozen=0)
    a = D("10000000000000000000000000000000000000000")
    b = D("10000000000000000000000000000000000000001")
    midpoint = D("10000000000000000000000000000000000000000.5")
    for index, day in enumerate(inputs["calendar"][:60]):
        inputs["observations"][day] = replace(inputs["observations"][day], turnover=a if index < 30 else b)
        inputs["liquidity"][day] = replace(inputs["liquidity"][day], turnover=a if index < 30 else b)
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], turnover=midpoint)
    inputs["capitalization"] = replace(inputs["capitalization"], value=b)
    inputs["params"]["threshold"] = D(1)
    result = assemble_window(**inputs)
    for arm in result["arms"].values():
        assert arm["activity_median"] == arm["liquidity_median"] == str(midpoint)
        assert arm["selected"] is True
    assert result["inputs"]["official_capitalization"]["value"] == str(b)


def test_inclusive_threshold_compares_exact_product_instead_of_rounded_repeating_ratio():
    inputs = fixture(frozen=0)
    for day in inputs["calendar"][:60]:
        inputs["observations"][day] = replace(inputs["observations"][day], turnover=D(3))
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], turnover=D(2))
    inputs["params"]["threshold"] = D("0.6666666666666666666666666667")
    result = assemble_window(**inputs)
    assert result["arms"]["D1"]["candidate"]["activity_pass"] is False
    assert result["arms"]["D2"]["selected"] is False


def test_float_activity_threshold_is_not_interpreted_as_exact_input():
    inputs = fixture()
    inputs["params"]["threshold"] = 3.0
    with pytest.raises(ValueError, match="exact integer or Decimal"):
        assemble_window(**inputs)


def test_source_failure_can_never_be_disguised_as_capitalization_exclusion():
    inputs = fixture()
    inputs["capitalization"] = replace(inputs["capitalization"], value=D(0))
    day = inputs["calendar"][0]
    inputs["observations"][day] = replace(inputs["observations"][day], state="missing", turnover=None)
    with pytest.raises(ValueError, match="unresolved synthetic observation"):
        assemble_window(**inputs)


def test_frozen_formation_still_requires_consistent_capitalization_provenance():
    inputs = fixture()
    inputs["observations"][inputs["formation"]] = replace(
        inputs["observations"][inputs["formation"]], state="frozen", turnover=D(0))
    result = assemble_window(**inputs)
    assert result["arms"]["D1"]["exclusion_reasons"] == ["formation_frozen"]
    cap = inputs["capitalization"]
    inputs["capitalization"] = replace(cap, public_available_at=cap.availability.available_at)
    with pytest.raises(ValueError, match="public availability conflicts with provenance"):
        assemble_window(**inputs)


def test_accepted_period_rejects_noncanonical_dates_before_constructing_interval():
    inputs = fixture()
    inputs["accepted_record"]["known_on_conservative_candidate"] = "20191115"
    with pytest.raises(ValueError, match="ISO date"):
        accepted_period(inputs["accepted_record"], formation=inputs["formation"], code=inputs["code"],
                        isin=inputs["isin"], required_dates=tuple(inputs["observations"]))
