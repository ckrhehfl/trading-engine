"""Tests for `python/research/experiment_log.py` -- the append-only
`runs/experiments.jsonl` log. See CLAUDE.md's "Strategy Research
Operational Design" section, "Experiment-tracking format" /
"Durability" subsections.
"""

import json
import re
from decimal import Decimal

import pytest

from research.experiment_log import log_holdout_access, log_run, read_records

# ---------------------------------------------------------------------------
# log_run
# ---------------------------------------------------------------------------


def _log_run_kwargs(**overrides):
    kwargs = dict(
        run_id="run-1",
        strategy_id="ma-crossover",
        strategy_version="v1",
        params={"fast": 10, "slow": 30},
        fold_results=[{"fold_index": 0, "sharpe_ratio": 1.2}],
        aggregate_metrics={"mean_sharpe": 1.2},
        data_range={"start_ms": 1000, "end_ms": 2000, "num_bars": 2},
        walk_forward_config={"train_bars": 10, "validate_bars": 5, "step_bars": 5},
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        is_holdout_run=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_log_run_writes_one_backtest_run_record(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(runs_path=runs_path))

    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["record_type"] == "backtest_run"
    assert record["run_id"] == "run-1"
    assert record["strategy_id"] == "ma-crossover"
    assert record["strategy_version"] == "v1"
    assert record["params"] == {"fast": 10, "slow": 30}
    assert record["fold_results"] == [{"fold_index": 0, "sharpe_ratio": 1.2}]
    assert record["aggregate_metrics"] == {"mean_sharpe": 1.2}
    assert record["data_range"] == {"start_ms": 1000, "end_ms": 2000, "num_bars": 2}
    assert record["walk_forward_config"] == {"train_bars": 10, "validate_bars": 5, "step_bars": 5}
    assert record["fee_bps"] == "5"
    assert record["slippage_bps"] == "2"
    assert record["is_holdout_run"] is False
    assert record["parent_run_id"] is None
    assert record["candidate_index"] is None
    assert record["total_candidates"] is None


def test_log_run_captures_git_head_sha_as_code_version(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(runs_path=runs_path))

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    # This test runs inside a real git checkout, so a real 40-char hex sha
    # is expected -- not mocked, so this also proves the subprocess call
    # actually runs rather than being stubbed out.
    assert re.fullmatch(r"[0-9a-f]{40}", record["code_version"])


def test_log_run_falls_back_to_none_when_git_is_unavailable(tmp_path, monkeypatch):
    import subprocess

    runs_path = tmp_path / "experiments.jsonl"

    def _boom(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _boom)

    log_run(**_log_run_kwargs(runs_path=runs_path))

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["code_version"] is None


def test_log_run_serializes_decimal_params_as_strings(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(params={"threshold": Decimal("1.5")}, runs_path=runs_path))

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["params"] == {"threshold": "1.5"}


def test_log_run_passes_through_grid_search_lineage_fields(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(
        **_log_run_kwargs(
            parent_run_id="grid-1",
            candidate_index=3,
            total_candidates=8,
            runs_path=runs_path,
        )
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["parent_run_id"] == "grid-1"
    assert record["candidate_index"] == 3
    assert record["total_candidates"] == 8


def test_log_run_omits_strategy_family_entirely_when_none(tmp_path):
    # Deliberately NOT `"strategy_family": null` (the shape
    # `parent_run_id` uses): "key absent" must unambiguously mean
    # "pre-lineage record, attribute via research.lineage's curated map",
    # while "key present" means "trust the record". A null would collide
    # the two cases. See `.planning/sr-p-trial-accounting.md`.
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(runs_path=runs_path))

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert "strategy_family" not in record


def test_log_run_writes_strategy_family_when_supplied(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(strategy_family="trend-momentum", runs_path=runs_path))

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["strategy_family"] == "trend-momentum"


def test_log_run_omits_both_preregistration_fields_entirely_when_none(tmp_path):
    # Same "key absent, never null" convention as `strategy_family` above,
    # and for the same reason: absent must unambiguously mean "this run was
    # not made under a pre-registration" (Strategy Research Task S).
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(runs_path=runs_path))

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert "preregistration_id" not in record
    assert "preregistration_sha256" not in record


def test_log_run_writes_both_preregistration_fields_when_supplied(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    sha = "a" * 64

    log_run(
        **_log_run_kwargs(
            preregistration_id="sr-u-daily-attempt",
            preregistration_sha256=sha,
            runs_path=runs_path,
        )
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["preregistration_id"] == "sr-u-daily-attempt"
    assert record["preregistration_sha256"] == sha


@pytest.mark.parametrize(
    "kwargs",
    [
        {"preregistration_id": "sr-u-daily-attempt"},
        {"preregistration_sha256": "a" * 64},
    ],
)
def test_log_run_rejects_a_half_specified_preregistration_pair(tmp_path, kwargs):
    # A record claiming a pre-registration with no integrity hash (or a
    # hash with nothing to attribute it to) is worse than one claiming
    # nothing -- same "supplied together or not at all" rule
    # `eligibility._resolve_moments` applies to skewness/kurtosis.
    runs_path = tmp_path / "experiments.jsonl"

    with pytest.raises(ValueError, match="preregistration"):
        log_run(**_log_run_kwargs(runs_path=runs_path, **kwargs))

    assert not runs_path.exists()


def test_log_run_appends_without_clobbering_prior_records(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(run_id="run-1", runs_path=runs_path))
    log_run(**_log_run_kwargs(run_id="run-2", runs_path=runs_path))

    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "run-1"
    assert json.loads(lines[1])["run_id"] == "run-2"


def test_log_run_creates_missing_parent_directory(tmp_path):
    runs_path = tmp_path / "nested" / "dir" / "experiments.jsonl"

    log_run(**_log_run_kwargs(runs_path=runs_path))

    assert runs_path.exists()


# ---------------------------------------------------------------------------
# log_holdout_access
# ---------------------------------------------------------------------------


def test_log_holdout_access_writes_one_holdout_access_record(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_holdout_access(
        strategy_id="ma-crossover",
        symbol="BTC-USDT",
        interval="15m",
        start_ms=1000,
        end_ms=2000,
        runs_path=runs_path,
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["record_type"] == "holdout_access"
    assert record["strategy_id"] == "ma-crossover"
    assert record["symbol"] == "BTC-USDT"
    assert record["interval"] == "15m"
    assert record["start_ms"] == 1000
    assert record["end_ms"] == 2000
    assert record["force_reclaim_reason"] is None


def test_log_holdout_access_records_force_reclaim_reason(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_holdout_access(
        strategy_id="ma-crossover",
        symbol="BTC-USDT",
        interval="15m",
        start_ms=1000,
        end_ms=2000,
        force_reclaim_reason="metrics bug fixed, re-running for a clean confirmation",
        runs_path=runs_path,
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["force_reclaim_reason"] == "metrics bug fixed, re-running for a clean confirmation"


def test_holdout_access_and_backtest_run_records_interleave_in_one_file(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    log_run(**_log_run_kwargs(runs_path=runs_path))
    log_holdout_access(
        strategy_id="ma-crossover",
        symbol="BTC-USDT",
        interval="15m",
        start_ms=1000,
        end_ms=2000,
        runs_path=runs_path,
    )

    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["record_type"] == "backtest_run"
    assert json.loads(lines[1])["record_type"] == "holdout_access"


# ---------------------------------------------------------------------------
# read_records
# ---------------------------------------------------------------------------


def test_read_records_returns_nothing_for_a_missing_file(tmp_path):
    runs_path = tmp_path / "does-not-exist.jsonl"

    assert list(read_records(runs_path)) == []


def test_read_records_yields_every_record_in_file_order(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    log_run(**_log_run_kwargs(run_id="run-1", runs_path=runs_path))
    log_holdout_access(
        strategy_id="s1", symbol="BTC-USDT", interval="15m", start_ms=1, end_ms=2, runs_path=runs_path
    )

    records = list(read_records(runs_path))

    assert [r["record_type"] for r in records] == ["backtest_run", "holdout_access"]


def test_read_records_skips_a_truncated_final_line_instead_of_raising(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    log_run(**_log_run_kwargs(run_id="run-1", runs_path=runs_path))
    # Simulate a crash mid-write of the second line: a trailing line that
    # isn't valid JSON at all.
    with open(runs_path, "a", encoding="utf-8") as f:
        f.write('{"record_type": "backtest_run", "run_id": "run-2", "trunc')

    records = list(read_records(runs_path))

    assert len(records) == 1
    assert records[0]["run_id"] == "run-1"


def test_read_records_raises_on_a_malformed_non_final_line():
    # A malformed line that ISN'T the final one indicates real corruption,
    # not an interrupted-write -- CLAUDE.md's durability note only asks
    # readers to tolerate a truncated *trailing* line.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        runs_path = Path(d) / "experiments.jsonl"
        runs_path.write_text('not json at all\n{"record_type": "backtest_run", "run_id": "run-2"}\n')

        with pytest.raises(json.JSONDecodeError):
            list(read_records(runs_path))
