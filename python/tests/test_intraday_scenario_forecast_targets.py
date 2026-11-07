"""Invented outcomes for one coherent EH multi-horizon training target."""

from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_forecast_targets import (
    OUTCOMES, condition_on_observations, horizon_probabilities, training_outcome,
)
from research.intraday_scenario_labels import PathQuestion, PathResult
from test_intraday_scenario_labels import at as observed_at, question, row, session


START = datetime(2026, 1, 2, tzinfo=UTC)


def at(offset):
    return START + offset * MINUTE


def resolved(horizon, path, observed):
    question = PathQuestion(START, at(horizon), 100, Fraction(1), 104, 97)
    return PathResult(question, "resolved", at(observed), path=path, mature_at=at(observed + 2))


def test_late_retest_is_one_joint_label_and_needs_prior_maturity():
    rows = (resolved(5, "P4", 5), resolved(15, "P2", 12), resolved(30, "P2", 12))
    label = training_outcome(rows, before=at(15))
    assert OUTCOMES[label.index] == ("P2", 15) and label.mature_at == at(14)
    assert training_outcome(rows, before=at(14)) is None
    assert training_outcome(rows[:2] + (replace(rows[2], status="unknown", path=None),), before=at(15)) is None
    with pytest.raises(ValueError, match="changed_across_horizons"):
        training_outcome(rows[:2] + (resolved(30, "P3", 12),), before=at(15))
    with pytest.raises(ValueError, match="touch_precedes_no_touch_horizon"):
        training_outcome((rows[0], resolved(15, "P2", 5), resolved(30, "P2", 5)), before=at(15))
    with pytest.raises(ValueError, match="invalid_resolved_EH_label"):
        training_outcome((replace(rows[0], mature_at=at(5)), *rows[1:]), before=at(15))


def test_joint_mass_becomes_monotonic_horizon_probabilities():
    values = {("P1", 5): 0.1, ("P2", 15): 0.3, ("P3", 30): 0.25, ("P4", 30): 0.35}
    weights = tuple(values.get(key, 0.0) for key in OUTCOMES)
    short, medium, long = horizon_probabilities(weights)
    assert short == pytest.approx((0.1, 0, 0, 0.9))
    assert medium == pytest.approx((0.1, 0.3, 0, 0.6))
    assert long == pytest.approx((0.1, 0.3, 0.25, 0.35))
    with pytest.raises(ValueError, match="mass_differs"):
        horizon_probabilities(tuple(value / 2 for value in weights))
    approximate = tuple({("P1", 5): 1.0, ("P1", 15): 5e-10}.get(key, 0.0) for key in OUTCOMES)
    assert horizon_probabilities(approximate)[1] == (1.0, 0, 0, 0)


def test_passage_of_clock_does_not_replace_observed_minutes():
    weights = tuple({("P1", 5): 0.5, ("P2", 15): 0.25, ("P4", 30): 0.25}.get(key, 0.0) for key in OUTCOMES)
    fixed = question()
    prefix, still_possible = condition_on_observations(fixed, 103, session(), cutoff_at=observed_at(6), weights=weights)
    assert prefix.observed_through == observed_at(4) and still_possible == weights
    updated, after_five = condition_on_observations(fixed, 103, session(), cutoff_at=observed_at(7), weights=weights)
    assert updated.question.question_id == prefix.question.question_id
    assert updated.observed_through == observed_at(5)
    assert horizon_probabilities(after_five) == ((0, 0, 0, 1), (0, 0.5, 0, 0.5), (0, 0.5, 0, 0.5))


def test_known_retest_excludes_direct_move_and_an_unknown_prefix_never_adds_probability():
    weights = tuple({("P1", 15): 0.5, ("P2", 15): 0.25, ("P3", 15): 0.25}.get(key, 0.0) for key in OUTCOMES)
    with_retest = session(row(0, (102, 104, 100, 102)))
    prefix, remaining = condition_on_observations(question(), 103, with_retest,
                                                  cutoff_at=observed_at(3), weights=weights)
    assert prefix.retested and horizon_probabilities(remaining)[1] == (0, 0.5, 0.5, 0)

    with_retest.minutes.pop(observed_at(1))
    unknown, remaining = condition_on_observations(question(), 103, with_retest,
                                                   cutoff_at=observed_at(4), weights=weights)
    assert unknown.status == "unknown" and remaining is None


def test_retest_requires_time_for_a_separate_departure_when_none_was_observed():
    weights = tuple({("P2", 5): 0.5, ("P2", 15): 0.25, ("P4", 30): 0.25}.get(key, 0.0) for key in OUTCOMES)
    flat = session(*(row(offset, (100, 101, 99, 100)) for offset in range(4)))
    prefix, reduced = condition_on_observations(question(), 100, flat, cutoff_at=observed_at(6), weights=weights)
    assert prefix.observed_through == observed_at(4) and not prefix.departed
    assert horizon_probabilities(reduced) == ((0, 0, 0, 1), (0, 0.5, 0, 0.5), (0, 0.5, 0, 0.5))
    departed, unchanged = condition_on_observations(question(), 103, session(),
                                                    cutoff_at=observed_at(6), weights=weights)
    assert departed.departed and unchanged == weights
