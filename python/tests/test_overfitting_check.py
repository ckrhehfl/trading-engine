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
from research.overfitting_check import (
    SENSITIVITY_PARENT_RUN_ID_PREFIX,
    TrialKind,
    check_combination_count,
    check_project_combination_count,
    classify_trial_kind,
)

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
    is_holdout_run: bool = False,
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
        is_holdout_run=is_holdout_run,
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


def test_check_combination_count_excludes_a_standalone_holdout_confirmation_record(tmp_path):
    # Strategy Research Task V: a holdout confirmation was never searched
    # over, so it must not count as a selection trial either.
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 30 * MS_PER_DAY
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="a-1")
    _log_backtest_run(
        runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="holdout-1", is_holdout_run=True
    )

    result = check_combination_count("strat-a", runs_path=runs_path)

    assert result.total_combinations_tried == 1


def test_check_combination_count_excludes_a_holdout_confirmations_nested_fit_sub_record_even_when_mislabeled(
    tmp_path,
):
    # Reproduces the real sr-v log shape exactly: the nested fit()
    # diagnostic sub-record is written FIRST (is_holdout_run=False, matching
    # DailyTsmomEnsembleTrainable.fit()'s real, pre-existing, mislabeling
    # behaviour -- it never knows the data it was handed was a holdout
    # window), and its own parent (the real holdout confirmation,
    # is_holdout_run=True) is written SECOND, only after fit() returns. Both
    # must be excluded regardless of this write order or the child's own
    # (wrong) is_holdout_run field.
    runs_path = tmp_path / "experiments.jsonl"
    start = int(BASE_TIME.timestamp() * 1000)
    end = start + 30 * MS_PER_DAY
    _log_backtest_run(runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="a-1")
    _log_backtest_run(
        runs_path,
        strategy_id="strat-a",
        start_ms=start,
        end_ms=end,
        run_id="holdout-1-fit-diagnostic",
        parent_run_id="holdout-1",
        is_holdout_run=False,  # mislabeled, exactly like the real bug
    )
    _log_backtest_run(
        runs_path, strategy_id="strat-a", start_ms=start, end_ms=end, run_id="holdout-1", is_holdout_run=True
    )

    result = check_combination_count("strat-a", runs_path=runs_path)

    assert result.total_combinations_tried == 1
    assert result.parent_run_groups == {}


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


# ---------------------------------------------------------------------------
# Strategy Research Task P: family-level / project-level trial accounting
# (`.planning/sr-p-trial-accounting.md`). Everything below is strictly
# additive -- every test above must keep passing byte-for-byte, since
# `check_combination_count`'s existing signature and behaviour are a hard
# backward-compatibility requirement.
# ---------------------------------------------------------------------------


def _log_run_record(
    runs_path,
    *,
    strategy_id: str,
    run_id: str,
    start_ms: int,
    end_ms: int,
    fold_count: int = 19,
    params: dict | None = None,
    strategy_version: str = "v1",
    mean_sharpe: float | None = 0.5,
    strategy_family: str | None = None,
    parent_run_id: str | None = None,
    candidate_index: int | None = None,
    total_candidates: int | None = None,
    is_holdout_run: bool = False,
) -> dict:
    """Richer `log_run` helper than `_log_backtest_run` above: the Task P
    counting rules read `fold_results` length (the historical
    sensitivity-probe rule), `params`/`strategy_version`/
    `aggregate_metrics` (the reproduction fingerprint) and
    `strategy_family`, none of which the older helper varies.
    """
    return experiment_log.log_run(
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        params=params if params is not None else {},
        fold_results=[{"fold_index": i} for i in range(fold_count)],
        aggregate_metrics={"fold_count": fold_count, "mean_sharpe": mean_sharpe},
        data_range={"start_ms": start_ms, "end_ms": end_ms, "num_bars": (end_ms - start_ms) // 900_000},
        walk_forward_config={"train_bars": 2160, "validate_bars": 720, "step_bars": 720, "fold_count": fold_count},
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        is_holdout_run=is_holdout_run,
        parent_run_id=parent_run_id,
        candidate_index=candidate_index,
        total_candidates=total_candidates,
        strategy_family=strategy_family,
        runs_path=runs_path,
    )


def _two_year_span() -> tuple[int, int]:
    start = int(BASE_TIME.timestamp() * 1000)
    return start, start + 730 * MS_PER_DAY


def test_classify_trial_kind_treats_a_standalone_single_fold_record_as_a_sensitivity_probe():
    # The empirical rule over THIS log (see `.planning/sr-p-trial-
    # accounting.md`): every standalone one-fold record in
    # runs/experiments.jsonl is a check_parameter_sensitivity evaluation,
    # never a selection trial. Not a general invariant.
    record = {"parent_run_id": None, "fold_results": [{"fold_index": 0}]}

    assert classify_trial_kind(record) is TrialKind.SENSITIVITY_PROBE


def test_classify_trial_kind_treats_a_standalone_multi_fold_record_as_a_selection_trial():
    record = {"parent_run_id": None, "fold_results": [{"fold_index": i} for i in range(19)]}

    assert classify_trial_kind(record) is TrialKind.SELECTION


def test_classify_trial_kind_treats_a_sensitivity_prefixed_parent_as_a_probe_regardless_of_folds():
    record = {"parent_run_id": f"{SENSITIVITY_PARENT_RUN_ID_PREFIX}abc-123", "fold_results": []}

    assert classify_trial_kind(record) is TrialKind.SENSITIVITY_PROBE


def test_classify_trial_kind_treats_a_real_parent_run_id_as_a_selection_trial():
    record = {"parent_run_id": "abc-123", "fold_results": [{"fold_index": 0}]}

    assert classify_trial_kind(record) is TrialKind.SELECTION


def test_check_project_combination_count_excludes_sensitivity_probes_from_n(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(
        runs_path, strategy_id="ensemble-momentum", run_id="wf-1", start_ms=start, end_ms=end, mean_sharpe=0.1
    )
    for i in range(9):
        _log_run_record(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id=f"probe-{i}",
            start_ms=start,
            end_ms=end,
            fold_count=1,
            params={"fast": 10 + i, "slow": 40},
            mean_sharpe=0.1,
        )

    result = check_project_combination_count(runs_path=runs_path)
    family = result.families["trend-momentum"]

    assert family.sensitivity_probe_trials == 9
    assert family.selection_trials == 1
    assert family.naive_total_combinations == 10  # what the old flat +1-per-record rule gives


def test_check_project_combination_count_groups_renamed_strategy_ids_into_one_family(tmp_path):
    # Defect 1: counting keyed on bare strategy_id lets a rename launder
    # the data-snooping history. `ensemble-momentum-configuration-c` is a
    # direct descendant of `ensemble-momentum`; both must land in one
    # family whose N reflects the whole search.
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    for candidate_index in range(30):
        _log_run_record(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id=f"c{candidate_index}",
            start_ms=start,
            end_ms=end,
            fold_count=1,
            params={"lookback": candidate_index},
            parent_run_id="wf-run-1",
            candidate_index=candidate_index,
            total_candidates=30,
        )
    _log_run_record(
        runs_path,
        strategy_id="ensemble-momentum-configuration-c",
        run_id="wf-2",
        start_ms=start,
        end_ms=end,
        mean_sharpe=0.9,
    )

    result = check_project_combination_count(runs_path=runs_path)

    assert set(result.families) == {"trend-momentum"}
    family = result.families["trend-momentum"]
    assert family.selection_trials == 31
    assert sorted(family.strategy_ids) == ["ensemble-momentum", "ensemble-momentum-configuration-c"]
    # And the naive per-strategy_id view still reports the laundered 1.
    laundered = check_combination_count("ensemble-momentum-configuration-c", runs_path=runs_path)
    assert laundered.total_combinations_tried == 1


def test_check_project_combination_count_reports_reproductions_separately_without_merging_them(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    for i in range(3):
        _log_run_record(
            runs_path,
            strategy_id="obv-trend",
            run_id=f"rerun-{i}",
            start_ms=start,
            end_ms=end,
            strategy_version="v1",
            mean_sharpe=-2.85,
        )

    result = check_project_combination_count(runs_path=runs_path)
    family = result.families["volume"]

    assert family.selection_trials == 3  # NOT silently deduplicated to 1
    assert family.reproduction_trials == 2
    assert family.deduplicated_selection_trials == 1


def test_check_project_combination_count_does_not_call_distinct_results_a_reproduction(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    for i, sharpe in enumerate((-2.85, -1.11, 0.42)):
        _log_run_record(
            runs_path, strategy_id="obv-trend", run_id=f"run-{i}", start_ms=start, end_ms=end, mean_sharpe=sharpe
        )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.families["volume"].reproduction_trials == 0


def test_check_project_combination_count_splits_project_totals_by_purpose(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="r1", start_ms=start, end_ms=end, mean_sharpe=0.1)
    _log_run_record(runs_path, strategy_id="mean-reversion", run_id="r2", start_ms=start, end_ms=end, mean_sharpe=0.2)
    _log_run_record(
        runs_path, strategy_id="ma-crossover-task-d-e2e", run_id="r3", start_ms=start, end_ms=end, mean_sharpe=0.3
    )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.research_selection_trials == 2
    assert result.infrastructure_selection_trials == 1
    assert result.total_selection_trials == 3


def test_check_project_combination_count_prefers_a_logged_strategy_family(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(
        runs_path,
        strategy_id="obv-trend",
        run_id="r1",
        start_ms=start,
        end_ms=end,
        strategy_family="carry",
        mean_sharpe=0.1,
    )

    result = check_project_combination_count(runs_path=runs_path)

    assert set(result.families) == {"carry"}


def test_check_project_combination_count_keeps_an_unmapped_strategy_id_as_its_own_family_with_a_note(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="never-seen-before", run_id="r1", start_ms=start, end_ms=end)

    result = check_project_combination_count(runs_path=runs_path)

    assert "never-seen-before" in result.families
    assert any("never-seen-before" in note for note in result.families["never-seen-before"].notes)


def test_check_project_combination_count_counts_a_grid_group_once_not_once_per_record(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    for fold in range(19):
        for candidate_index in range(4):
            _log_run_record(
                runs_path,
                strategy_id="ensemble-momentum",
                run_id=f"f{fold}-c{candidate_index}",
                start_ms=start,
                end_ms=end,
                fold_count=1,
                params={"lookback": candidate_index},
                parent_run_id="wf-run-1",
                candidate_index=candidate_index,
                total_candidates=4,
            )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.families["trend-momentum"].selection_trials == 4


def test_check_project_combination_count_ignores_holdout_access_records(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="r1", start_ms=start, end_ms=end)
    experiment_log.log_holdout_access(
        strategy_id="obv-trend",
        symbol="BTC-USDT",
        interval="1h",
        start_ms=end,
        end_ms=end + MS_PER_DAY,
        runs_path=runs_path,
    )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.total_selection_trials == 1


def test_check_project_combination_count_excludes_a_standalone_holdout_confirmation_record(tmp_path):
    # Strategy Research Task V: a holdout confirmation was never searched
    # over, so it must not inflate any family's or the project's N.
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="r1", start_ms=start, end_ms=end)
    _log_run_record(
        runs_path,
        strategy_id="daily-tsmom-ensemble",
        run_id="holdout-1",
        start_ms=start,
        end_ms=end,
        is_holdout_run=True,
    )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.total_selection_trials == 1
    assert "daily-tsmom" not in result.families


def test_check_project_combination_count_excludes_the_real_sr_v_shaped_record_pair(tmp_path):
    # Reproduces the real logged shape exactly: the nested fit() diagnostic
    # sub-record (parent_run_id set, is_holdout_run=False -- mislabeled,
    # since fit() never knows the data it scored came from a holdout split)
    # is written FIRST, and the real holdout confirmation itself
    # (is_holdout_run=True, single fold, parent_run_id=None) is written
    # SECOND, only once fit() returns. Neither may count toward any
    # family's or the project's N, regardless of this write order.
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="r1", start_ms=start, end_ms=end)
    _log_run_record(
        runs_path,
        strategy_id="daily-tsmom-ensemble",
        run_id="holdout-1-fit-diagnostic",
        start_ms=start,
        end_ms=end,
        parent_run_id="holdout-1",
        total_candidates=1,
        fold_count=1,
        is_holdout_run=False,
    )
    _log_run_record(
        runs_path,
        strategy_id="daily-tsmom-ensemble",
        run_id="holdout-1",
        start_ms=start,
        end_ms=end,
        fold_count=1,
        is_holdout_run=True,
    )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.total_selection_trials == 1
    assert "daily-tsmom" not in result.families


def test_check_project_combination_count_on_an_empty_log_is_an_unremarkable_zero(tmp_path):
    result = check_project_combination_count(runs_path=tmp_path / "nothing-here.jsonl")

    assert result.families == {}
    assert result.total_selection_trials == 0
    assert "no backtest_run records" in result.warning


def test_check_project_combination_count_risk_level_uses_selection_trials_not_the_naive_total(tmp_path):
    # 400 sensitivity probes over 2 years would be >30/year (HIGH) under
    # the naive count; the corrected N here is 1 combination over 2 years.
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="wf-1", start_ms=start, end_ms=end)
    for i in range(400):
        _log_run_record(
            runs_path,
            strategy_id="obv-trend",
            run_id=f"probe-{i}",
            start_ms=start,
            end_ms=end,
            fold_count=1,
            params={"obv_ma_period": i},
        )

    result = check_project_combination_count(runs_path=runs_path)

    assert result.families["volume"].risk_level == "low"
    assert result.families["volume"].sensitivity_probe_trials == 400


def test_project_and_family_results_to_dict_are_json_serializable(tmp_path):
    import json

    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="r1", start_ms=start, end_ms=end)

    result = check_project_combination_count(runs_path=runs_path)

    json.dumps(result.to_dict(), default=str)
    assert result.to_dict()["families"]["volume"]["family"] == "volume"


def test_check_combination_count_is_unchanged_by_the_new_trial_accounting(tmp_path):
    # Hard backward-compatibility requirement: the per-strategy_id
    # function keeps its old flat "+1 per standalone record" behaviour,
    # sensitivity probes included, so its existing callers/tests see
    # exactly what they saw before Task P.
    runs_path = tmp_path / "experiments.jsonl"
    start, end = _two_year_span()
    _log_run_record(runs_path, strategy_id="obv-trend", run_id="wf-1", start_ms=start, end_ms=end)
    for i in range(9):
        _log_run_record(
            runs_path,
            strategy_id="obv-trend",
            run_id=f"probe-{i}",
            start_ms=start,
            end_ms=end,
            fold_count=1,
            params={"obv_ma_period": i},
        )

    assert check_combination_count("obv-trend", runs_path=runs_path).total_combinations_tried == 10
