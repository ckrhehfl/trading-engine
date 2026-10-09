"""Hand-built one-way failure proofs; no actual data or research trial."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D, localcontext
import json

import pytest

from research.activity_failure import activity_failure
from research.activity_accounting import IdentityPeriod
from research.activity_screen import PublishedValue, ScreenObservation, screen_synthetic
from research.activity_timing import AvailabilityMetadata, KST, SessionLagPolicy


def fixture(*, lookback=60, extra=0):
    days = tuple(date(2024, 1, 1) + timedelta(days=i) for i in range(lookback + extra + 3))
    formation = days[-3]
    observations = {}
    for i, day in enumerate(days[:-2]):
        available = datetime.combine(day, time(18, 10), KST)
        metadata = AvailabilityMetadata(
            observation_date=day, available_at=available, retrieved_at="unknown",
            source="hand-built scan turnover", is_final=None, data_vintage=None,
            evidence_level="assumed", availability_policy="registered synthetic assumption",
            evidence_reference="synthetic fixture",
        )
        observations[day] = ScreenObservation("observed", D(10), None, metadata)
    return dict(calendar=days, formation_on=formation, observations=observations,
                bb_required_observed_dates=days[:-2], bb_frozen_dates=(),
                threshold=D(3), lookback=lookback)


def set_amount(inputs, day, amount):
    inputs["observations"][day] = replace(inputs["observations"][day], turnover=amount)


def test_complete_numeric_failure_and_pass_never_certify_identity():
    inputs = fixture()
    failed = activity_failure(**inputs)
    assert failed.proven_failure and failed.reason == "activity_below_threshold"
    assert failed.baseline == D(10) and failed.required_turnover == D(30)
    assert failed.normal_dates == inputs["calendar"][:60]
    assert failed.consumed_dates == inputs["calendar"][:-2]
    assert failed.selection_at == tuple(SessionLagPolicy(lag).selection_at(inputs["calendar"], 60)
                                        for lag in (1, 2))
    set_amount(inputs, inputs["formation_on"], D(30))
    passed = activity_failure(**inputs)
    assert not passed.proven_failure and passed.reason == "numeric_pass_unresolved"
    assert passed.unresolved == ("operating_period_and_eligibility_uncertified",)
    assert not failed.historical_eligibility_certified and not passed.historical_eligibility_certified
    with pytest.raises(FrozenInstanceError):
        failed.proven_failure = False


def test_large_decimal_even_median_and_exact_inclusive_product_ignore_ambient_precision():
    inputs = fixture()
    a = D("10000000000000000000000000000000000000000")
    b = D("10000000000000000000000000000000000000001")
    for i, day in enumerate(inputs["calendar"][:60]):
        set_amount(inputs, day, a if i < 30 else b)
    formation = inputs["formation_on"]
    set_amount(inputs, formation, D("30000000000000000000000000000000000000001.5"))
    with localcontext() as context:
        context.prec = 3
        result = activity_failure(**inputs)
    assert result.baseline == D("10000000000000000000000000000000000000000.5")
    assert result.required_turnover == D("30000000000000000000000000000000000000001.5")
    assert not result.proven_failure
    set_amount(inputs, formation, D("30000000000000000000000000000000000000001.49"))
    assert activity_failure(**inputs).proven_failure


def test_exact_product_avoids_rounded_repeating_ratio_false_pass():
    inputs = fixture()
    for day in inputs["calendar"][:60]:
        set_amount(inputs, day, D(3))
    set_amount(inputs, inputs["formation_on"], D(2))
    inputs["threshold"] = D("0.6666666666666666666666666667")
    assert activity_failure(**inputs).proven_failure


def test_json_diagnostics_preserve_decimal_strings_and_unknown_metadata():
    inputs = fixture()
    set_amount(inputs, inputs["formation_on"], D("10.0000000000000000000000000001"))
    output = json.loads(json.dumps(activity_failure(**inputs).to_dict()))
    assert output["formation_turnover"] == "10.0000000000000000000000000001"
    assert output["baseline"] == "10" and output["required_turnover"] == "30"
    assert output["consumed_dates"] == [day.isoformat() for day in inputs["calendar"][:-2]]
    assert output["historical_eligibility_certified"] is False
    assert set(output["selection_at"]) == {"D1", "D2"}
    metadata = inputs["observations"][inputs["formation_on"]].availability
    assert metadata.retrieved_at == "unknown" and metadata.is_final is None
    assert metadata.data_vintage is None and metadata.source_public_available_at is None


def test_true_zero_is_normal_and_zero_baseline_has_its_own_failure():
    inputs = fixture()
    for day in inputs["calendar"][:60]:
        set_amount(inputs, day, D(0))
    result = activity_failure(**inputs)
    assert result.proven_failure and result.reason == "zero_baseline"
    assert result.baseline == 0 and len(result.normal_dates) == 60
    assert result.required_turnover is None


def test_nonfrozen_zero_formation_fails_positive_baseline_without_becoming_a_halt():
    inputs = fixture()
    set_amount(inputs, inputs["formation_on"], D(0))
    result = activity_failure(**inputs)
    assert result.proven_failure and result.reason == "activity_below_threshold"
    assert result.formation_turnover == 0 and not result.frozen_dates


def test_later_continuous_operating_starts_cannot_turn_failure_into_eligibility():
    inputs = fixture(lookback=2, extra=2)
    days = inputs["calendar"]
    set_amount(inputs, days[0], D(1000))
    inputs["observations"][days[2]] = replace(inputs["observations"][days[2]],
                                            state="frozen", turnover=D(0))
    inputs["bb_frozen_dates"] = (days[2],)
    set_amount(inputs, inputs["formation_on"], D(20))
    proof = activity_failure(**inputs)
    assert proof.proven_failure and proof.normal_dates == (days[1], days[3])
    assert proof.baseline == D(10) and proof.required_turnover == D(30)
    cutoff = proof.selection_at[0]
    for start in days[:5]:
        screen = screen_synthetic(
            calendar=days, formation_index=4, params={"lookback": 2, "threshold": D(3)},
            quotes={"A": inputs["observations"]}, liquidity={"A": inputs["observations"]},
            capitalization={"A": PublishedValue(D(100), cutoff)},
            periods=(IdentityPeriod("A", start, days[-1] + timedelta(days=1), days[0],
                                    True, "hand-built verified operating period"),),
            listings={}, liquidity_lookback=2, liquidity_floor=D(0),
            capitalization_floor=D(0), decision_at=cutoff,
        )
        candidate, diagnostic = screen.selection.candidates[0], screen.diagnostics[0]
        assert candidate.activity_pass is False
        if start <= days[1]:
            assert candidate.operating_baseline == "pass" and diagnostic.activity_median == 10
        else:
            assert candidate.operating_baseline == "insufficient" and diagnostic.activity_median is None


def test_frozen_prior_sessions_are_checked_and_skipped_locked_positive_turnover_is_normal():
    inputs = fixture(extra=2)
    days = inputs["calendar"]
    for day in days[60:62]:
        inputs["observations"][day] = replace(inputs["observations"][day], state="frozen", turnover=D(0))
    inputs["bb_frozen_dates"] = days[60:62]
    # A locked positive-turnover quote is represented as observed, not frozen.
    set_amount(inputs, days[59], D(1000))
    result = activity_failure(**inputs)
    assert result.proven_failure and result.baseline == 10
    assert result.normal_dates == days[:60] and result.frozen_dates == days[60:62]
    assert result.consumed_dates == days[:-2]


def test_only_latest_60_strictly_preceding_normals_enter_baseline():
    inputs = fixture(extra=2)
    days = inputs["calendar"]
    set_amount(inputs, days[0], D("1e80"))
    set_amount(inputs, days[1], D("1e80"))
    set_amount(inputs, inputs["formation_on"], D(30))
    result = activity_failure(**inputs)
    assert result.normal_dates == days[2:62] and result.baseline == 10
    assert not result.proven_failure


def test_frozen_formation_is_failure_only_after_every_interval_row_resolves():
    inputs = fixture()
    formation = inputs["formation_on"]
    inputs["observations"][formation] = replace(inputs["observations"][formation], state="frozen", turnover=D(0))
    inputs["bb_formation_state"] = "frozen"
    result = activity_failure(**inputs)
    assert result.proven_failure and result.reason == "formation_frozen"
    del inputs["observations"][inputs["calendar"][59]]
    assert not activity_failure(**inputs).proven_failure


@pytest.mark.parametrize("bad", [None, D("NaN"), D("Infinity"), D(-1), 0, 10.0, "10"])
def test_NULL_and_malformed_turnover_prevent_numeric_exclusion(bad):
    inputs = fixture()
    set_amount(inputs, inputs["calendar"][2], bad)
    result = activity_failure(**inputs)
    assert not result.proven_failure and result.reason == "unresolved_input"
    assert any("NULL_or_invalid_turnover" in issue for issue in result.unresolved)


@pytest.mark.parametrize("state", ["missing", "unknown", "invalid", "supplier_gap", "NULL", None, []])
def test_unknown_state_is_not_a_skippable_frozen_observation(state):
    inputs = fixture()
    day = inputs["calendar"][20]
    inputs["observations"][day] = replace(inputs["observations"][day], state=state)
    result = activity_failure(**inputs)
    assert not result.proven_failure and any("unknown_observation_state" in issue for issue in result.unresolved)


def test_missing_counterexample_can_change_apparent_failure_into_pass():
    inputs = fixture(lookback=2, extra=1)
    days = inputs["calendar"]
    set_amount(inputs, days[0], D(100))
    set_amount(inputs, days[1], D(0))
    set_amount(inputs, days[2], D(0))
    set_amount(inputs, inputs["formation_on"], D(1))
    del inputs["observations"][days[2]]
    unknown = activity_failure(**inputs)
    assert not unknown.proven_failure and unknown.reason == "unresolved_input"
    # Skipping the gap would produce median(100, 0)=50 and a false failure.
    # Completing it changes the latest baseline to zero (still AN-inactive).
    # Completing it as a small positive value instead makes this a numeric pass.
    inputs["observations"][days[2]] = replace(inputs["observations"][days[1]], turnover=D("0.1"),
                                            availability=replace(inputs["observations"][days[1]].availability,
                                                                 observation_date=days[2],
                                                                 available_at=datetime.combine(days[2], time(18), KST)))
    complete = activity_failure(**inputs)
    assert complete.baseline == D("0.05") and not complete.proven_failure
    assert complete.reason == "numeric_pass_unresolved"


@pytest.mark.parametrize("mode", ["absent", "wrong_type", "late_D1", "late_D2", "wrong_day", "legacy_conflict"])
def test_explicit_metadata_and_both_arm_cutoffs_apply_to_all_consumed_rows(mode):
    inputs = fixture(extra=1)
    day = inputs["calendar"][0]  # Older than the latest 60, but still consumed.
    row = inputs["observations"][day]
    metadata = row.availability
    if mode == "absent":
        row = replace(row, availability=None, public_available_at=inputs["observations"][inputs["formation_on"]].availability.available_at)
    elif mode == "wrong_type":
        row = replace(row, availability={"available_at": metadata.available_at})
    elif mode.startswith("late"):
        lag = int(mode[-1])
        cutoff = SessionLagPolicy(lag).selection_at(inputs["calendar"], len(inputs["calendar"]) - 3)
        row = replace(row, availability=replace(metadata, available_at=cutoff + timedelta(seconds=1)))
    elif mode == "wrong_day":
        row = replace(row, availability=replace(metadata, observation_date=day - timedelta(days=1)))
    else:
        row = replace(row, public_available_at=metadata.available_at)
    inputs["observations"][day] = row
    result = activity_failure(**inputs)
    assert not result.proven_failure and any("metadata" in issue for issue in result.unresolved)
    if mode == "late_D1":
        assert any("metadata_D1" in issue for issue in result.unresolved)
        assert not any("metadata_D2" in issue for issue in result.unresolved)


def test_even_skipped_frozen_metadata_must_be_available():
    inputs = fixture(extra=1)
    day = inputs["calendar"][60]
    inputs["observations"][day] = replace(inputs["observations"][day], state="frozen", turnover=D(0), availability=None)
    inputs["bb_frozen_dates"] = (day,)
    assert not activity_failure(**inputs).proven_failure


@pytest.mark.parametrize("mode", ["omit_session", "frozen_drift", "formation_drift", "frozen_positive", "frozen_outside"])
def test_BB_date_or_state_drift_never_refreshes_itself_into_exclusion(mode):
    inputs = fixture()
    days = inputs["calendar"]
    if mode == "omit_session":
        inputs["bb_required_observed_dates"] = days[:20] + days[21:-2]
    elif mode == "frozen_drift":
        inputs["bb_frozen_dates"] = (days[20],)
    elif mode == "formation_drift":
        inputs["bb_formation_state"] = "frozen"
    elif mode == "frozen_positive":
        inputs["observations"][days[20]] = replace(inputs["observations"][days[20]], state="frozen")
        inputs["bb_frozen_dates"] = (days[20],)
    else:
        inputs["bb_frozen_dates"] = (days[-1],)
    result = activity_failure(**inputs)
    assert not result.proven_failure and result.unresolved


def test_truncated_unclassified_interval_cannot_prove_insufficient_history():
    inputs = fixture()
    inputs["bb_required_observed_dates"] = inputs["calendar"][1:-2]
    result = activity_failure(**inputs)
    assert not result.proven_failure and result.unresolved == ("incomplete_normal_baseline",)


@pytest.mark.parametrize("changes", [{"threshold": 3}, {"threshold": D("NaN")},
                                    {"threshold": D(-1)}, {"lookback": True},
                                    {"calendar": ()}, {"bb_required_observed_dates": ()}])
def test_bad_parameters_do_not_produce_failure_proofs(changes):
    inputs = fixture()
    inputs.update(changes)
    if "bb_required_observed_dates" in changes:
        assert not activity_failure(**inputs).proven_failure
    else:
        with pytest.raises(ValueError):
            activity_failure(**inputs)
