"""A failed or interrupted discovery look must not disappear from history."""

import math

import pytest

from research import experiment_log
from research.overfitting_check import check_project_combination_count


def payload(path, evaluate):
    """Provide a declared spent window without inventing walk-forward folds."""
    return dict(
        study_id="activity-calibration-v1", specification_sha256="a" * 64,
        parameters={"threshold": 3.0},
        window={"symbol": "KRX:", "interval": "1d",
                "start": "2019-01-02", "end": "2026-09-18"},
        evaluate=evaluate, runs_path=path,
    )


def test_start_is_durable_before_evaluation_and_does_not_increment_promotion_n(tmp_path):
    """The callback observes its persisted start and the look stays outside promotion N."""
    path = tmp_path / "experiments.jsonl"

    def evaluate():
        """Read the actual audit trail from inside the evaluation boundary."""
        records = list(experiment_log.read_records(path))
        assert [r["status"] for r in records] == ["started"]
        return {"sessions": 100}

    result = experiment_log.run_discovery_trial(**payload(path, evaluate))
    records = list(experiment_log.read_records(path))
    assert [r["status"] for r in records] == ["started", "completed"]
    assert records[0]["run_id"] == records[1]["run_id"]
    assert result["result"] == {"sessions": 100}
    assert result["promotion_allowed"] is False
    assert check_project_combination_count(runs_path=path).research_selection_trials == 0


def test_failed_start_write_prevents_any_data_read(tmp_path, monkeypatch):
    """Storage failure must prevent the callback rather than merely warn."""
    calls = []

    def refuse(*args, **kwargs):
        """Model an unavailable log before the evaluation can begin."""
        raise OSError("disk unavailable")

    monkeypatch.setattr(experiment_log, "_append_record", refuse)
    with pytest.raises(OSError):
        experiment_log.run_discovery_trial(**payload(tmp_path / "log", lambda: calls.append(1)))
    assert calls == []


@pytest.mark.parametrize("error", [ValueError("sensitive message"), KeyboardInterrupt()])
def test_failure_is_recorded_without_exception_contents(tmp_path, error):
    """An exception or interrupt leaves a failed attempt without sensitive text."""
    path = tmp_path / "log"

    def fail():
        """Raise the selected failure exactly where a data-reading callback would."""
        raise error

    with pytest.raises(type(error)):
        experiment_log.run_discovery_trial(**payload(path, fail))
    records = list(experiment_log.read_records(path))
    assert [r["status"] for r in records] == ["started", "failed"]
    assert "sensitive message" not in path.read_text()


def test_reruns_are_visible_and_inputs_cannot_be_changed_by_callback(tmp_path):
    """Each look has its own identity and immutable input provenance."""
    path = tmp_path / "log"
    params = {"levels": [2, 3]}

    def evaluate():
        """Mutate caller-owned parameters to probe the stored snapshot."""
        params["levels"].append(4)
        return {}

    args = payload(path, evaluate)
    args["parameters"] = params
    experiment_log.run_discovery_trial(**args)
    experiment_log.run_discovery_trial(**payload(path, lambda: {}))
    records = list(experiment_log.read_records(path))
    assert records[1]["parameters"] == {"levels": [2, 3]}
    assert records[0]["run_id"] != records[2]["run_id"]


def test_nonfinite_result_is_a_failed_attempt(tmp_path):
    """Invalid JSON numbers cannot masquerade as a completed measurement."""
    path = tmp_path / "log"
    with pytest.raises(ValueError):
        experiment_log.run_discovery_trial(**payload(path, lambda: {"mean": math.nan}))
    assert list(experiment_log.read_records(path))[-1]["status"] == "failed"


def test_invalid_window_is_refused_before_callback(tmp_path):
    """An inverted declared date range cannot start an evaluation."""
    args = payload(tmp_path / "log", lambda: pytest.fail("must not evaluate"))
    args["window"]["end"] = "2018-01-01"
    with pytest.raises(ValueError):
        experiment_log.run_discovery_trial(**args)


def test_default_writer_is_isolated():
    """The pytest fixture redirects this writer as it does existing log writers."""
    experiment_log.run_discovery_trial(**payload(experiment_log.DEFAULT_RUNS_PATH, lambda: {}))
    assert list(experiment_log.read_records(experiment_log.DEFAULT_RUNS_PATH))[-1]["status"] == "completed"


def test_discovery_preserves_a_nonzero_promotion_history(tmp_path):
    """Adding a discovery look neither resets nor increments existing selection N."""
    path = tmp_path / "log"
    experiment_log.log_run(
        run_id="real-selection", strategy_id="ensemble-momentum-configuration-c",
        strategy_version="v1", params={"lookback": 20}, fold_results=[{}, {}],
        aggregate_metrics={}, data_range={"start_ms": 0, "end_ms": 100000},
        walk_forward_config={}, fee_bps=5, slippage_bps=2, is_holdout_run=False,
        runs_path=path,
    )
    before = check_project_combination_count(runs_path=path).research_selection_trials
    assert before == 1
    experiment_log.run_discovery_trial(**payload(path, lambda: {"sessions": 100}))
    assert check_project_combination_count(runs_path=path).research_selection_trials == before
