"""Tests for `python/research/overfitting_check.py` -- the MinBTL-style
(Bailey, Borwein, Lopez de Prado, Zhu 2016 "The Probability of Backtest
Overfitting") combination-count-vs-data-span heuristic (CLAUDE.md's
Strategy Research Methodology / `.planning/sr-g-overfitting-safeguards.md`,
"Finding 1").

This is a **documented approximation of the MinBTL spirit, not a literal
reproduction of Bailey et al.'s exact statistical test** -- see the
planning doc and `overfitting_check.py`'s own module docstring for why.
These tests cover the counting/aggregation logic (the part that must be
exactly right regardless of how approximate the risk-tiering is) and the
risk-tiering's directional behavior (more combinations / less data must
never report a *lower* risk tier than fewer combinations / more data).
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research import experiment_log
from research.overfitting_check import check_combination_count

BASE_TIME = datetime(2024, 1, 1, tzinfo=timezone.utc)
MS_PER_DAY = 24 * 3600 * 1000


def _log_backtest_run(
    runs_path,
    *,
    strategy_id: str,
    start_ms: int,
    end_ms: int,
    parent_run_id: str | None = None,
    candidate_index: int | None = None,
    total_candidates: int | None = None,
    run_id: str | None = None,
) -> dict:
    return experiment_log.log_run(
        run_id=run_id or f"run-{start_ms}-{candidate_index}-{parent_run_id}",
        strategy_id=strategy_id,
        strategy_version="v1",
        params={},
        fold_results=[],
        aggregate_metrics={},
        data_range={"start_ms": start_ms, "end_ms": end_ms, "num_bars": (end_ms - start_ms) // 900_000},
        walk_forward_config={},
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        is_holdout_run=False,
        parent_run_id=parent_run_id,
        candidate_index=candidate_index,
        total_candidates=total_candidates,
        runs_path=runs_path,
    )


def test_check_combination_count_returns_unknown_for_a_strategy_id_never_run(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"

    result = check_combination_count("never-run-strategy", runs_path=runs_path)

    assert result.total_combinations_tried == 0
    assert result.risk_level == "unknown"
    assert result.data_span_years is None


def test_check_combination_count_counts_standalone_runs_as_one_each(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 30 * MS_PER_DAY
    for i in range(3):
        _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id=f"standalone-{i}")

    result = check_combination_count("strat-a", runs_path=runs_path)

    assert result.total_combinations_tried == 3
    assert result.standalone_run_count == 3
    assert result.parent_run_groups == {}


def test_check_combination_count_sums_total_candidates_once_per_distinct_parent_run_id(tmp_path):
    # Mirrors the real shape: one run_walk_forward call with 3 folds, each
    # fold's fit() doing an independent 5-candidate grid search but all
    # sharing the SAME parent_run_id (the walk-forward run's own run_id) --
    # 15 logged candidate records, but only 5 *distinct* configurations
    # were actually considered, per the task's own counting instruction
    # ("summing total_candidates across distinct parent_run_ids").
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 200 * MS_PER_DAY

    for fold in range(3):
        for candidate_index in range(5):
            _log_backtest_run(
                runs_path,
                strategy_id="strat-b",
                start_ms=start,
                end_ms=end,
                parent_run_id="wf-run-1",
                candidate_index=candidate_index,
                total_candidates=5,
                run_id=f"fold{fold}-cand{candidate_index}",
            )

    result = check_combination_count("strat-b", runs_path=runs_path)

    assert result.total_combinations_tried == 5
    assert result.parent_run_groups == {"wf-run-1": 5}
    assert result.standalone_run_count == 0


def test_check_combination_count_sums_across_multiple_distinct_parent_run_ids(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 200 * MS_PER_DAY

    for candidate_index in range(5):
        _log_backtest_run(
            runs_path,
            strategy_id="strat-c",
            start_ms=start,
            end_ms=end,
            parent_run_id="wf-run-1",
            candidate_index=candidate_index,
            total_candidates=5,
            run_id=f"a-{candidate_index}",
        )
    for candidate_index in range(8):
        _log_backtest_run(
            runs_path,
            strategy_id="strat-c",
            start_ms=start,
            end_ms=end,
            parent_run_id="wf-run-2",
            candidate_index=candidate_index,
            total_candidates=8,
            run_id=f"b-{candidate_index}",
        )
    # Plus one standalone (e.g. a holdout confirmation run).
    _log_backtest_run(runs_path, strategy_id="strat-c", start_ms=start, end_ms=end, run_id="standalone-1")

    result = check_combination_count("strat-c", runs_path=runs_path)

    assert result.total_combinations_tried == 5 + 8 + 1
    assert result.parent_run_groups == {"wf-run-1": 5, "wf-run-2": 8}
    assert result.standalone_run_count == 1


def test_check_combination_count_ignores_other_strategy_ids(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 30 * MS_PER_DAY
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="a-1")
    _log_backtest_run(runs_path, strategy_id="strat-other", start_ms=start, end_ms=end, run_id="other-1")
    _log_backtest_run(runs_path, strategy_id="strat-other", start_ms=start, end_ms=end, run_id="other-2")

    result = check_combination_count("strat-a", runs_path=runs_path)

    assert result.total_combinations_tried == 1


def test_check_combination_count_ignores_holdout_access_records(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 30 * MS_PER_DAY
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="a-1")
    experiment_log.log_holdout_access(
        strategy_id="strat-a",
        symbol="BTC-USDT",
        interval="15m",
        start_ms=end,
        end_ms=end + MS_PER_DAY,
        runs_path=runs_path,
    )

    result = check_combination_count("strat-a", runs_path=runs_path)

    assert result.total_combinations_tried == 1


def test_check_combination_count_computes_data_span_in_years_from_the_widest_logged_range(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    # One run covering 365 days, another (e.g. a later re-run) covering a
    # wider 730-day span -- data_span must reflect the widest observed
    # extent across all of this strategy_id's logged runs, not just the
    # first/last one encountered.
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=start + 365 * MS_PER_DAY, run_id="r1")
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=start + 730 * MS_PER_DAY, run_id="r2")

    result = check_combination_count("strat-a", runs_path=runs_path)

    assert result.data_span_years == pytest.approx(2.0, rel=1e-3)


def test_check_combination_count_risk_level_is_low_for_few_combinations_over_much_data(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + (5 * 365) * MS_PER_DAY  # 5 years of data
    for i in range(3):
        _log_backtest_run(runs_path, strategy_id="strat-low-risk", start_ms=start, end_ms=end, run_id=f"r{i}")

    result = check_combination_count("strat-low-risk", runs_path=runs_path)

    assert result.risk_level == "low"


def test_check_combination_count_risk_level_is_high_for_many_combinations_over_little_data(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 60 * MS_PER_DAY  # ~2 months of data
    for candidate_index in range(200):
        _log_backtest_run(
            runs_path,
            strategy_id="strat-high-risk",
            start_ms=start,
            end_ms=end,
            parent_run_id="wf-run-1",
            candidate_index=candidate_index,
            total_candidates=200,
            run_id=f"c{candidate_index}",
        )

    result = check_combination_count("strat-high-risk", runs_path=runs_path)

    assert result.risk_level == "high"


def test_check_combination_count_risk_level_ordering_is_monotonic_in_combinations_per_year(tmp_path):
    # Same data span, increasing combination counts -> risk level must
    # never decrease. This is the property that actually matters (the
    # exact tier boundaries are a documented, not rigorously-derived,
    # heuristic -- see the module docstring) -- monotonicity is what must
    # hold regardless of where the boundaries are drawn.
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 365 * MS_PER_DAY
    levels = {"unknown": -1, "low": 0, "moderate": 1, "high": 2}
    previous_rank = -1
    for n in (1, 5, 15, 30, 60, 150):
        runs_path = tmp_path / f"experiments_{n}.jsonl"
        for candidate_index in range(n):
            _log_backtest_run(
                runs_path,
                strategy_id="strat-monotonic",
                start_ms=start,
                end_ms=end,
                parent_run_id="wf-run-1",
                candidate_index=candidate_index,
                total_candidates=n,
                run_id=f"c{candidate_index}",
            )
        result = check_combination_count("strat-monotonic", runs_path=runs_path)
        rank = levels[result.risk_level]
        assert rank >= previous_rank
        previous_rank = rank


def test_check_combination_count_handles_missing_total_candidates_via_fallback_count(tmp_path):
    # Defensive case: child records with parent_run_id set but no
    # total_candidates reported at all (malformed/older data) -- must not
    # crash, and must fall back to the observed distinct-record count for
    # that parent_run_id rather than silently reporting zero.
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 100 * MS_PER_DAY
    for candidate_index in range(4):
        _log_backtest_run(
            runs_path,
            strategy_id="strat-fallback",
            start_ms=start,
            end_ms=end,
            parent_run_id="wf-run-1",
            candidate_index=candidate_index,
            total_candidates=None,
            run_id=f"c{candidate_index}",
        )

    result = check_combination_count("strat-fallback", runs_path=runs_path)

    assert result.total_combinations_tried == 4
    assert any("no total_candidates" in note for note in result.notes)


def test_check_combination_count_notes_inconsistent_total_candidates_within_a_group(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 100 * MS_PER_DAY
    _log_backtest_run(
        runs_path,
        strategy_id="strat-inconsistent",
        start_ms=start,
        end_ms=end,
        parent_run_id="wf-run-1",
        candidate_index=0,
        total_candidates=5,
        run_id="c0",
    )
    _log_backtest_run(
        runs_path,
        strategy_id="strat-inconsistent",
        start_ms=start,
        end_ms=end,
        parent_run_id="wf-run-1",
        candidate_index=1,
        total_candidates=6,  # inconsistent with the record above
        run_id="c1",
    )

    result = check_combination_count("strat-inconsistent", runs_path=runs_path)

    assert result.parent_run_groups["wf-run-1"] == 6  # conservative: take the max
    assert any("inconsistent" in note for note in result.notes)


def test_check_combination_count_result_to_dict_is_json_serializable(tmp_path):
    import json

    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 100 * MS_PER_DAY
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="a-1")

    result = check_combination_count("strat-a", runs_path=runs_path)

    json.dumps(result.to_dict(), default=str)
    assert result.to_dict()["strategy_id"] == "strat-a"
