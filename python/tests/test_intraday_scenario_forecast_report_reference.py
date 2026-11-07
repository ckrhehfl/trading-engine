"""Independent EH score checks over fixed invented scenarios."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_fold import FoldDecision, FoldForecast
from research.intraday_scenario_forecast_report import score_fold
from research.intraday_scenario_forecast_report_reference import verify_forecast_report
from research.intraday_scenario_labels import PathQuestion


START = datetime(2030, 1, 2, 0, 22, tzinfo=UTC)
DAYS = ("2030-01-02", "2030-01-03")
OUTCOMES = {"A": ("P1",) * 3, "B": ("P1",) * 3, "C": ("P4", "P4", None),
            "D": None, "E": ("P4", "P4", "P1")}


def certain(outcome):
    return tuple(float(index == outcome) for index in range(10))


def decision(symbol, session, offset, first, second, *, stage="approach", peers=False):
    started = START + timedelta(days=session)
    at = started + timedelta(minutes=offset)
    question = PathQuestion(started, started + timedelta(minutes=30), 100, 1, 105, 95)
    return FoldDecision(DAYS[session], symbol, stage, at, (symbol, stage, question.question_id, at), "ready", peers,
                        first, second, first if peers else None, second if peers else None)


def forecast(*rows):
    return FoldForecast("S1", 1, 40, 100, 30, 100, 30, (None, None, None), rows)


def examples():
    hit, no_hit = certain(0), certain(9)
    missing = replace(decision("X", 0, 0, None, None), key=None, chart_status="unsupported")
    return forecast(decision("A", 0, 0, hit, no_hit), decision("A", 0, 5, hit, no_hit, peers=True),
                    decision("A", 0, 10, hit, hit), decision("B", 0, 5, no_hit, hit),
                    decision("C", 1, 5, no_hit, no_hit), decision("D", 1, 0, hit, no_hit, stage="breakout", peers=True),
                    decision("E", 0, 0, hit, certain(6), stage="breakout"), decision("SOLO", 1, 5, hit, None), missing)


def test_reference_recomputes_brier_and_weights_events_then_dates():
    run = examples()
    reads = Counter()

    def read(key):
        reads[key[0]] += 1
        return OUTCOMES[key[0]]

    report = score_fold(run, lambda key: OUTCOMES[key[0]], allowed_dates=DAYS)
    verify_forecast_report(run, report, read, allowed_dates=DAYS)
    assert reads == dict.fromkeys(OUTCOMES, 1)
    assert report.decisions_without_question == 1
    approach_update = report.groups[2].counts
    assert (approach_update.candidates, approach_update.first_supported, approach_update.second_supported,
            approach_update.paired_forecasts, approach_update.paired_outcomes,
            approach_update.scored_events, approach_update.scored_dates) == (5, 5, 4, 4, 4, 3, 2)
    assert approach_update.horizon_pairs == (0, 4, 4)
    assert approach_update.horizon_outcomes == (0, 4, 3)
    assert approach_update.horizon_events == (0, 3, 2)
    assert approach_update.horizon_dates == (0, 2, 1)
    assert approach_update.first_brier == (None, 0.5, 1.0)
    assert approach_update.second_brier == (None, 0.25, 0.5)
    assert approach_update.daily_difference == ((date(2030, 1, 2), (None, -0.5, -0.5)),
                                                 (date(2030, 1, 3), (None, 0.0, None)))
    breakout_initial = report.groups[4].counts
    assert breakout_initial.first_brier == (2.0, 2.0, 0.0)
    assert breakout_initial.second_brier == (0.0,) * 3
    assert report.groups[5].counts.paired_forecasts == 1
    assert report.groups[5].counts.paired_outcomes == 0
    assert report.groups[5].counts.first_brier is None


def test_reference_verifies_non_degenerate_horizon_probabilities():
    distribution = (0.1, 0.05, 0.05, 0.1, 0.2, 0.05, 0.1, 0.1, 0.05, 0.2)
    run = forecast(decision("A", 0, 0, distribution, certain(4)))
    report = score_fold(run, lambda _: ("P4", "P2", "P2"), allowed_dates=DAYS)
    verify_forecast_report(run, report, lambda _: ("P4", "P2", "P2"), allowed_dates=DAYS)
    assert report.groups[0].counts.first_brier == pytest.approx((0.055, 0.815, 0.575))


def test_closed_update_horizons_never_request_an_evaluation_label():
    run = forecast(decision("A", 0, 30, certain(0), certain(9), peers=True))

    def forbidden(_):
        pytest.fail("requested an outcome after every horizon closed")

    report = score_fold(run, forbidden, allowed_dates=DAYS)
    verify_forecast_report(run, report, forbidden, allowed_dates=DAYS)
    counts = report.groups[2].counts
    assert counts.paired_forecasts == 1 and counts.paired_outcomes == 0
    assert counts.horizon_pairs == (0, 0, 0) and counts.first_brier is None


@pytest.mark.parametrize(("changes", "error"), [
    ({"scored_events": 4}, "counts_differ"),
    ({"paired_forecasts": 5}, "counts_differ"),
    ({"paired_outcomes": 3}, "counts_differ"),
    ({"horizon_pairs": (4, 4, 4)}, "horizon_counts_differ"),
    ({"horizon_dates": (0, 2, 2)}, "horizon_counts_differ"),
    ({"daily_difference": ((date(2030, 1, 2), (0.5,) * 3), (date(2030, 1, 3), (0.0,) * 3))}, "daily_values_differ"),
    ({"second_brier": (1.0,) * 3}, "brier_differs"),
])
def test_reference_refuses_report_roster_or_arithmetic_drift(changes, error):
    run = examples()

    def read(key):
        return OUTCOMES[key[0]]

    report = score_fold(run, read, allowed_dates=DAYS)
    group = report.groups[2]
    tampered = replace(group, counts=replace(group.counts, **changes))
    with pytest.raises(InputRefusal, match=error):
        verify_forecast_report(run, replace(report, groups=(*report.groups[:2], tampered, *report.groups[3:])),
                               read, allowed_dates=DAYS)


@pytest.mark.parametrize(("change", "error"), [
    ({"day": "2030-01-04"}, "decision_outside_fold"),
    ({"chart": (0.0,) * 10}, "probability_invalid"),
    ({"symbol": "MISMATCH"}, "question_identity_differs"),
])
def test_reference_refuses_invalid_forecast_before_outcome_access(change, error):
    run = examples()
    report = score_fold(run, lambda key: OUTCOMES[key[0]], allowed_dates=DAYS)
    altered = replace(run, decisions=(*run.decisions[:5], replace(run.decisions[5], **change), *run.decisions[6:]))
    with pytest.raises(InputRefusal, match=error):
        verify_forecast_report(altered, report, lambda _: pytest.fail("opened evaluation label"), allowed_dates=DAYS)


def test_reference_refuses_duplicate_forecast_wrong_group_and_invalid_label():
    run = forecast(decision("A", 0, 0, certain(0), certain(0)))
    report = score_fold(run, lambda _: ("P1",) * 3, allowed_dates=DAYS)
    with pytest.raises(InputRefusal, match="question_identity_differs"):
        verify_forecast_report(replace(run, decisions=run.decisions * 2), report,
                               lambda _: pytest.fail("opened evaluation label"), allowed_dates=DAYS)
    with pytest.raises(InputRefusal, match="group_identity_differs"):
        verify_forecast_report(run, replace(report, groups=tuple(reversed(report.groups))),
                               lambda _: pytest.fail("opened evaluation label"), allowed_dates=DAYS)
    with pytest.raises(InputRefusal, match="outcome_invalid"):
        verify_forecast_report(run, report, lambda _: False, allowed_dates=DAYS)
