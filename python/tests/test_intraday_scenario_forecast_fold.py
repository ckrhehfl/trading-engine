"""A matched chart/market fold over invented, separately supplied labels."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_decision_inputs import ChartDecisionInputs, PeerDecisionInputs
from research.intraday_scenario_forecast_fold import forecast_fold
from research.intraday_scenario_forecast_targets import TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


START = datetime(2030, 1, 2, 0, 22, tzinfo=UTC)


def day(offset, *, evaluation=False):
    at = START + timedelta(days=offset)
    event_date = at.date().isoformat()
    rows = []
    coordinates = []
    symbols = ("A", "B", "C", "D", "E") if not evaluation else ("A", "E", "F")
    for symbol in symbols:
        question = PathQuestion(at, at + 30 * MINUTE, 100, 1, 105, 95)
        chart = ChartDecisionInputs((symbol, *question.forecast_id(at)), "ready",
                                    minutes=((Fraction(0), Fraction(1), Fraction(-1), Fraction(0)),) * 20,
                                    five_minute_bars=((Fraction(0), Fraction(1), Fraction(-1), Fraction(0)),) * 4,
                                    state=tuple(map(Fraction, (5, 5, 1, 0, 0, 0))))
        frame = SimpleNamespace(symbol=symbol, observed_at=at, initial=SimpleNamespace(stage="approach"), chart=chart)
        peer = None if symbol in ("E", "F") else PeerDecisionInputs(symbol, at, (), (), ((0, 0, 0),) * 20, ())
        scenarios = ("S1",) if symbol != "F" else ("S2",)
        rows.append(SimpleNamespace(frame=frame, peer=peer, scenarios=scenarios,
                                    question_key=(symbol, "approach", question.question_id)))
        coordinates.append(SimpleNamespace(scenarios=scenarios))
    return SimpleNamespace(day=event_date, rows=tuple(rows), coordinates=tuple(coordinates))


def test_fold_never_reads_test_outcomes_and_matches_training_and_evaluation_support():
    training = tuple(day(offset) for offset in range(30))
    test = day(30, evaluation=True)
    cutoff = START + timedelta(days=30) - 22 * MINUTE
    fold = ChronologicalFold(1, (0, 30), (30, 31), tuple(item.day for item in training), (test.day,), cutoff)
    reads = []

    def label(key):
        reads.append(key)
        observed = key[2][0]
        assert observed < cutoff
        return TrainingOutcome(0, observed + 4 * MINUTE)

    run = forecast_fold((*training, test), fold, "S1", label)
    assert len(reads) == run.training_events == 150 and run.training_dates == 30
    assert run.matched_training_events == 120 and run.matched_training_dates == 30
    assert run.original_test_coordinates == 2
    assert [artifact["training_rows"] for artifact in run.artifacts] == [150, 120, 120]
    first, second = run.decisions
    assert first.symbol == "A" and second.symbol == "E"
    assert first.empirical is not None and first.chart is not None
    assert first.chart_matched is not None and first.market_matched is not None
    assert second.chart is not None and second.chart_matched is None and second.market_matched is None
