"""A failed or interrupted discovery look must not disappear from history."""

import math

import pytest

from research import experiment_log
from research.overfitting_check import check_project_combination_count


def payload(path, evaluate):
    return dict(
        study_id="activity-calibration-v1", specification_sha256="a" * 64,
        parameters={"threshold": 3.0},
        window={"symbol": "KRX:", "interval": "1d",
                "start": "2019-01-02", "end": "2026-09-18"},
        evaluate=evaluate, runs_path=path,
    )


def test_start_is_durable_before_evaluation_and_does_not_increment_promotion_n(tmp_path):
    path = tmp_path / "experiments.jsonl"

    def evaluate():
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
    calls = []

    def refuse(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(experiment_log, "_append_record", refuse)
    with pytest.raises(OSError):
        experiment_log.run_discovery_trial(**payload(tmp_path / "log", lambda: calls.append(1)))
    assert calls == []


@pytest.mark.parametrize("error", [ValueError("sensitive message"), KeyboardInterrupt()])
def test_failure_is_recorded_without_exception_contents(tmp_path, error):
    path = tmp_path / "log"

    def fail():
        raise error

    with pytest.raises(type(error)):
        experiment_log.run_discovery_trial(**payload(path, fail))
    records = list(experiment_log.read_records(path))
    assert [r["status"] for r in records] == ["started", "failed"]
    assert "sensitive message" not in path.read_text()


def test_reruns_are_visible_and_inputs_cannot_be_changed_by_callback(tmp_path):
    path = tmp_path / "log"
    params = {"levels": [2, 3]}

    def evaluate():
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
    path = tmp_path / "log"
    with pytest.raises(ValueError):
        experiment_log.run_discovery_trial(**payload(path, lambda: {"mean": math.nan}))
    assert list(experiment_log.read_records(path))[-1]["status"] == "failed"


def test_invalid_window_is_refused_before_callback(tmp_path):
    args = payload(tmp_path / "log", lambda: pytest.fail("must not evaluate"))
    args["window"]["end"] = "2018-01-01"
    with pytest.raises(ValueError):
        experiment_log.run_discovery_trial(**args)


def test_default_writer_is_isolated():
    experiment_log.run_discovery_trial(**payload(experiment_log.DEFAULT_RUNS_PATH, lambda: {}))
    assert list(experiment_log.read_records(experiment_log.DEFAULT_RUNS_PATH))[-1]["status"] == "completed"
