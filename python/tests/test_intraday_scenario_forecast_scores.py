"""Matched and date-weighted forecast scoring on invented outcomes."""

from datetime import UTC, datetime, timedelta

from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_forecast_scores import ForecastPair, matched_forecast_scores
from research.intraday_scenario_forecast_targets import OUTCOMES
from research.intraday_scenario_labels import PathQuestion


START = datetime(2030, 1, 2, 0, 22, tzinfo=UTC)


def one_hot(label, horizon):
    return tuple(float(item == (label, horizon)) for item in OUTCOMES)


def row(symbol, day, first, second, *, delay=0):
    at = START + timedelta(days=day)
    question = PathQuestion(at, at + 30 * MINUTE, 100, 1, 105, 95)
    return ForecastPair((symbol, "approach", *question.forecast_id(at + delay * MINUTE)), first, second)


def test_multiple_updates_and_events_do_not_outvote_another_date():
    good, bad = one_hot("P1", 5), one_hot("P3", 5)
    forecasts = (row("A", 0, good, bad), row("A", 0, bad, good, delay=5), row("B", 0, good, good),
                 row("A", 1, bad, good), row("B", 2, good, None), row("C", 3, good, bad))
    read = []

    def observed(key):
        read.append(key)
        return ("P1", "P1", "P1") if key[0] != "C" else None

    score = matched_forecast_scores(forecasts, observed)
    assert forecasts[4].key not in read
    assert (score.candidates, score.first_supported, score.second_supported) == (6, 6, 5)
    assert (score.paired_forecasts, score.paired_outcomes, score.scored_events, score.scored_dates) == (5, 4, 3, 2)
    assert score.horizon_pairs == (4, 5, 5)
    assert score.horizon_outcomes == (3, 4, 4)
    assert score.horizon_events == (3, 3, 3) and score.horizon_dates == (2, 2, 2)
    assert score.first_brier == (1.0, 1.25, 1.25) and score.second_brier == (0.5, 0.25, 0.25)
    assert tuple(values for _, values in score.daily_difference) == ((1, 0, 0), (-2, -2, -2))


def test_known_no_touch_before_later_touch_scores_as_p4():
    too_late, correct = one_hot("P2", 30), one_hot("P2", 15)
    score = matched_forecast_scores((row("A", 0, too_late, correct),), lambda _: ("P4", "P2", "P2"))
    assert score.first_brier == (0, 2, 0) and score.second_brier == (0, 0, 0)


def test_only_still_future_horizons_with_known_labels_contribute_to_scores():
    forecasts = (
        row("A", 0, one_hot("P3", 15), one_hot("P3", 5)),
        row("B", 1, one_hot("P3", 15), one_hot("P1", 15), delay=5),
        row("C", 2, one_hot("P3", 30), one_hot("P1", 30), delay=15),
        row("D", 3, one_hot("P3", 30), one_hot("P1", 30), delay=30),
    )
    labels = {"A": ("P4", None, None), "B": ("P4", "P1", None), "C": ("P4", "P4", "P3")}

    score = matched_forecast_scores(forecasts, lambda key: labels[key[0]])

    assert score.paired_forecasts == 4
    assert score.paired_outcomes == score.scored_events == score.scored_dates == 3
    assert score.horizon_pairs == (1, 2, 3)
    assert score.horizon_outcomes == score.horizon_events == score.horizon_dates == (1, 1, 1)
    assert score.first_brier == (0, 2, 0) and score.second_brier == (2, 0, 2)
    assert tuple(values for _, values in score.daily_difference) == (
        (2, None, None), (None, -2, None), (None, None, 2),
    )


def test_missing_horizons_preserve_distinct_denominators_and_empty_scores():
    forecast = row("A", 0, one_hot("P3", 15), one_hot("P3", 5))

    partial = matched_forecast_scores((forecast,), lambda _: ("P4", None, None))
    assert partial.first_brier == (0, None, None) and partial.second_brier == (2, None, None)
    assert partial.horizon_pairs == (1, 1, 1) and partial.horizon_dates == (1, 0, 0)

    missing = matched_forecast_scores((forecast,), lambda _: (None, None, None))
    assert missing.paired_forecasts == 1 and missing.paired_outcomes == 0
    assert missing.first_brier is None and missing.second_brier is None and missing.daily_difference == ()
