"""Frozen forecast contrasts are scored only inside the registered fold."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_fold import FoldDecision, FoldForecast
from research.intraday_scenario_forecast_report import combine_fold_reports, score_fold
from research.intraday_scenario_labels import PathQuestion


DAY = "2030-01-02"
START = datetime(2030, 1, 2, 0, 22, tzinfo=UTC)
QUESTION = PathQuestion(START, START + timedelta(minutes=30), 100, 1, 105, 95).question_id
FIRST = (1.0,) + (0.0,) * 9
LAST = (0.0,) * 9 + (1.0,)


def decision(stage="approach", *, update=False, session_offset=0):
    shift = timedelta(days=session_offset)
    at = START + shift + timedelta(minutes=5 if update else 0)
    started, level, band, target, stop, end = QUESTION
    question = (started + shift, level, band, target, stop, end + shift)
    return FoldDecision(at.date().isoformat(), "A", stage, at, ("A", stage, question, at), "ready", True,
                        LAST, FIRST, FIRST, LAST)


def forecast(*rows):
    return FoldForecast("S1", 1, 20, 100, 30, 100, 30, (None, None, None), rows)


def test_score_fold_keeps_both_clocks_and_peer_contrast_separate():
    reads = []

    def read(key):
        reads.append(key)
        return ("P1", "P1", "P1") if key[1] == "approach" else None

    missing = replace(decision("breakout"), key=None, empirical=None, chart=None, chart_matched=None, market_matched=None)
    report = score_fold(forecast(decision(), decision(update=True), decision("breakout"), missing), read,
                        allowed_dates=(DAY,))
    assert report.original_coordinates == 20 and report.decisions_without_question == 1
    assert len(reads) == 2 and len(report.groups) == 8
    assert [group.counts.paired_outcomes for group in report.groups] == [1, 1, 1, 1, 0, 0, 0, 0]
    assert report.groups[0].counts.second_brier == (0.0,) * 3
    assert report.groups[1].counts.first_brier == (0.0,) * 3
    assert report.groups[2].counts.horizon_pairs == (0, 1, 1)
    assert report.groups[2].counts.second_brier == (None, 0.0, 0.0)


@pytest.mark.parametrize("bad", [replace(decision(), day="2030-01-03"), decision()])
def test_drift_is_detected_before_evaluation_label_access(bad):
    with pytest.raises(InputRefusal, match="outside_fold|duplicate_or_mismatched_question"):
        score_fold(forecast(decision(), bad), lambda _: pytest.fail("opened evaluation label"), allowed_dates=(DAY,))


def test_combined_scores_weight_dates_and_refuse_overlap():
    first = score_fold(forecast(decision()), lambda _: ("P1",) * 3, allowed_dates=(DAY,))
    second_run = replace(forecast(decision(session_offset=1), decision(session_offset=2)), index=2)
    second = score_fold(second_run, lambda _: ("P4", "P4", None), allowed_dates=("2030-01-03", "2030-01-04"))
    result = combine_fold_reports((first, second))
    assert result[0]["scored_dates"] == 3
    assert result[0]["horizon_pairs"] == (3, 3, 3)
    assert result[0]["horizon_outcomes"] == (3, 3, 1)
    assert result[0]["horizon_events"] == (3, 3, 1)
    assert result[0]["horizon_dates"] == (3, 3, 1)
    assert result[0]["first_brier"] == (2 / 3, 2 / 3, 2.0)
    assert result[0]["second_brier"] == (4 / 3, 4 / 3, 0.0)
    with pytest.raises(InputRefusal, match="scored_dates_overlap"):
        combine_fold_reports((first, replace(first, fold=2)))
