"""Exercise an invented EH fold from causal snapshots through saved scoring."""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256

import pytest

from research import intraday_scenario_forecast_saved as saved
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_forecast_fold import FoldForecast
from research.intraday_scenario_forecast_seal import read_forecasts
from research.intraday_scenario_snapshot import project_opening_day, write_features, write_labels
from test_intraday_scenario_state import BELOW, OPENING, OPENING_EVIDENCE, session


SYMBOLS = tuple(f"invented-{number}" for number in range(1, 7))
TRAIN_DAYS = 30


def _invented_session(opening, symbol):
    template = session(BELOW, BELOW, (94, 100, 89, 99), (99, 101, 98, 100), *((100, 101, 99, 100),) * 2,
                       (100, 104, 99, 103), *((103, 104, 102, 103),) * 7, opening=opening, closing=70)
    return replace(template, symbol=symbol, minutes={at: replace(row, symbol=symbol)
                                                     for at, row in template.minutes.items()})


@pytest.fixture(scope="module")
def projection(tmp_path_factory):
    directory = tmp_path_factory.mktemp("eh-invented-fold")
    evaluation_open = OPENING + timedelta(days=TRAIN_DAYS)
    calendar = tuple((OPENING + timedelta(days=offset)).date().isoformat() for offset in range(TRAIN_DAYS + 1))
    fold = ChronologicalFold(1, (0, TRAIN_DAYS), (TRAIN_DAYS, TRAIN_DAYS + 1),
                             calendar[:-1], calendar[-1:], evaluation_open)
    sessions_by_day = {}

    def days():
        for offset, day in enumerate(calendar):
            opening = OPENING + timedelta(days=offset)
            sessions = {symbol: _invented_session(opening, symbol) for symbol in SYMBOLS}
            sessions_by_day[day] = sessions
            peers = SYMBOLS[:-1] if opening == evaluation_open else SYMBOLS
            targets = tuple(TargetCoordinate(scenario, symbol, day, rank, None, "no_stored_rows_unknown_reason", b"invented")
                            for scenario, members in (("S1", SYMBOLS), ("S2", peers))
                            for rank, symbol in enumerate(members, start=1))
            yield project_opening_day(day, targets, sessions, dict.fromkeys(sessions, OPENING_EVIDENCE),
                                      members_known_at=opening)

    features, labels = directory / "features.jsonl", directory / "labels.jsonl"
    feature_seal = write_features(features, days())
    label_seal = write_labels(labels, features, feature_seal, lambda day, symbol: sessions_by_day[day][symbol])
    return saved.ForecastSnapshot(features, feature_seal, labels, label_seal), fold


@pytest.fixture(scope="module")
def predictions(tmp_path_factory, projection):
    snapshot, fold = projection
    path = tmp_path_factory.mktemp("eh-invented-predictions") / "forecast.jsonl"
    seal = saved.fit_saved_fold(snapshot, fold, "S1", path, lambda _: None)
    return path, seal


def test_saved_fold_round_trip_keeps_chart_support_when_peer_support_falls(projection, predictions):
    snapshot, fold = projection
    path, seal = predictions
    forecast = read_forecasts(path, seal, scenario="S1", index=fold.index,
                              feature_seal=snapshot.feature_seal, label_seal=snapshot.label_seal)
    assert forecast.training_dates == forecast.matched_training_dates == TRAIN_DAYS
    assert forecast.training_events == forecast.matched_training_events == TRAIN_DAYS * len(SYMBOLS)
    assert forecast.original_test_coordinates == len(SYMBOLS)
    initial = tuple(row for row in forecast.decisions if row.stage == "approach" and row.key[2][0] == row.observed_at)
    assert tuple(row.symbol for row in initial) == SYMBOLS
    assert all(row.chart is not None and row.empirical is not None for row in initial)
    assert all(not row.peers_supported and row.chart_matched is None and row.market_matched is None for row in initial[:-1])
    assert initial[-1].peers_supported and initial[-1].chart_matched is not None and initial[-1].market_matched is not None

    report = saved.score_saved_fold(snapshot, fold, "S1", path, seal, lambda _: None)
    chart, market = (group.counts for group in report.groups if group.stage == "approach" and group.clock == "initial")
    assert report.original_coordinates == len(SYMBOLS)
    assert chart.paired_forecasts == chart.paired_outcomes == len(SYMBOLS)
    assert market.candidates == len(SYMBOLS)
    assert market.paired_forecasts == market.paired_outcomes == 1
    assert chart.scored_dates == market.scored_dates == 1
    assert chart.first_brier is not None and chart.second_brier is not None


def test_fit_callback_excludes_targets_maturing_at_or_after_test_opening(tmp_path, projection, monkeypatch):
    snapshot, fold = projection
    read_labels_on = saved._labels_on
    outcomes = {}

    def change_maturity(source, dates):
        assert dates == set(fold.training_sessions)
        for index, label in enumerate(read_labels_on(source, dates)):
            assert label.key[2][0].date().isoformat() in dates
            if index in (1, 2):
                label = replace(label, training=replace(label.training,
                    mature_at=fold.first_test_opening + (index - 1) * MINUTE))
            if index < 3:
                outcomes[label.key] = label.training
            yield label

    def inspect_fit(days, active_fold, scenario, prior_outcome):
        assert active_fold == fold
        on_time, at_cutoff, after_cutoff = outcomes
        assert prior_outcome(on_time) == outcomes[on_time]
        assert prior_outcome(at_cutoff) is None
        assert prior_outcome(after_cutoff) is None
        test_day = tuple(days)[-1]
        with pytest.raises(InputRefusal, match="eh_fold_nonprior_label_read"):
            prior_outcome(test_day.rows[0].question_key)
        return FoldForecast(scenario, fold.index, len(test_day.coordinates), 0, 0, 0, 0, (None,) * 3, ())

    monkeypatch.setattr(saved, "_labels_on", change_maturity)
    monkeypatch.setattr(saved, "forecast_fold", inspect_fit)
    saved.fit_saved_fold(snapshot, fold, "S1", tmp_path / "forecast.jsonl", lambda _: None)


def test_resealed_prediction_trailing_bytes_refused_before_labels(tmp_path, projection, predictions, monkeypatch):
    snapshot, fold = projection
    path, seal = predictions
    trailing = path.read_bytes() + b"{}\n"
    tampered = tmp_path / "forecast.jsonl"
    tampered.write_bytes(trailing)
    tampered.chmod(0o400)
    resealed = replace(seal, size=len(trailing), sha256=sha256(trailing).hexdigest())

    def forbidden(*_):
        pytest.fail("evaluation labels opened before prediction EOF was verified")

    monkeypatch.setattr(saved, "_labels_on", forbidden)
    with pytest.raises(InputRefusal, match="footer_or_EOF_differs"):
        saved.score_saved_fold(snapshot, fold, "S1", tampered, resealed, lambda _: None)


def test_scoring_keeps_known_short_outcomes_without_joint_training_target(projection, predictions, monkeypatch):
    snapshot, fold = projection
    path, seal = predictions
    read_labels_on = saved._labels_on

    def remove_last_outcomes(source, dates):
        for label in read_labels_on(source, dates):
            if label.results:
                first, second, last = label.results
                yield replace(label, training=None, results=(first, second, replace(last, status="unknown", path=None)))
            else:
                yield label

    monkeypatch.setattr(saved, "_labels_on", remove_last_outcomes)
    report = saved.score_saved_fold(snapshot, fold, "S1", path, seal, lambda _: None)
    chart = report.groups[0].counts
    assert chart.horizon_outcomes == (len(SYMBOLS), len(SYMBOLS), 0)
    assert chart.first_brier[2] is chart.second_brier[2] is None
