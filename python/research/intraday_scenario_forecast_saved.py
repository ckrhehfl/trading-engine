"""Fit and score one EH fold with a sealed prediction boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_campaign_wsl_transport import encode
from research.intraday_ed_inputs import _require
from research.intraday_scenario_forecast_fold import forecast_fold
from research.intraday_scenario_forecast_report import ForecastReport, score_fold
from research.intraday_scenario_forecast_report_reference import verify_forecast_report
from research.intraday_scenario_forecast_seal import read_forecasts, seal_forecasts
from research.intraday_scenario_snapshot import (
    MAX_QUESTIONS, SnapshotSeal, _label_header, _label_row, _read, _read_label, read_features,
)


Checkpoint = Callable[[str], object]


@dataclass(frozen=True)
class ForecastSnapshot:
    features: Path
    feature_seal: SnapshotSeal
    labels: Path
    label_seal: SnapshotSeal


def _labels_on(snapshot: ForecastSnapshot, dates: set[str]):
    header = _label_header(snapshot.feature_seal)
    previous = None
    for row, raw in _read(snapshot.labels, snapshot.label_seal, header, maximum=MAX_QUESTIONS):
        date = row["key"][2][0][:10]
        if date in dates:
            label = _read_label(row)
            order = label.key[2][0].date().isoformat(), *label.key
            _require(order[0] == date and (previous is None or previous < order)
                     and encode(_label_row(label)) == raw, "eh_fold_label_subset_differs")
            previous = order
            yield label


def fit_saved_fold(snapshot: ForecastSnapshot, fold: ChronologicalFold, scenario: str,
                   predictions: Path, checkpoint: Checkpoint) -> SnapshotSeal:
    """Materialize only mature prior-date targets in the fit's capability."""
    prior_dates, all_dates = set(fold.training_sessions), set((*fold.training_sessions, *fold.test_sessions))
    outcomes = {}
    for row in _labels_on(snapshot, prior_dates):
        checkpoint("eh_fold_prior_label")
        if row.training is not None and row.training.mature_at < fold.first_test_opening:
            outcomes[row.key] = row.training

    def days():
        for day in read_features(snapshot.features, snapshot.feature_seal):
            checkpoint("eh_fold_feature_day")
            if day.day in all_dates:
                yield day

    def prior_outcome(key):
        _require(key[2][0].date().isoformat() in prior_dates, "eh_fold_nonprior_label_read")
        return outcomes.get(key)

    result = forecast_fold(days(), fold, scenario, prior_outcome)
    checkpoint("eh_fold_before_prediction_seal")
    seal = seal_forecasts(predictions, result, snapshot.feature_seal, snapshot.label_seal)
    checkpoint("eh_fold_after_prediction_seal")
    return seal


def score_saved_fold(snapshot: ForecastSnapshot, fold: ChronologicalFold, scenario: str, predictions: Path,
                     prediction_seal: SnapshotSeal, checkpoint: Checkpoint) -> ForecastReport:
    """Verify the complete prediction EOF before linking evaluation outcomes."""
    forecast = read_forecasts(predictions, prediction_seal, scenario=scenario, index=fold.index,
                              feature_seal=snapshot.feature_seal, label_seal=snapshot.label_seal)
    dates = set(fold.test_sessions)
    outcomes = {}
    for row in _labels_on(snapshot, dates):
        checkpoint("eh_fold_evaluation_label")
        if row.results:
            outcomes[row.key] = tuple(result.path if result.status == "resolved" else None for result in row.results)

    def outcome(key):
        return outcomes.get(key[:3])

    report = score_fold(forecast, outcome, allowed_dates=fold.test_sessions)
    verify_forecast_report(forecast, report, outcome, allowed_dates=fold.test_sessions)
    return report
