"""Tests for `python/research/retrospective.py` -- Strategy Research Task R,
the statistical close-out that applies sr-p's honest trial count and sr-q's
Deflated Sharpe Ratio to every distinct multi-fold run this project has
logged.

**Synthetic fixtures only.** The real `runs/experiments.jsonl` is gitignored,
absent from a fresh clone and from CI, and is this project's research audit
trail -- tests must never read it, and must certainly never append to it.
Same discipline as `test_overfitting_check.py` and `test_eligibility.py`.

The two things these tests care most about, because they are what makes the
report honest rather than merely computed:

1. **Verdict assignment at every boundary**, including the deliberately
   chosen precedence order (the trade-count floor is checked *before* any
   DSR-driven label, so "this study cannot conclude" is never overwritten by
   "shown"/"not shown").
2. **The detection floor travels with the verdict, structurally.** A reader
   must not be able to see "DSR is ~0" without simultaneously seeing the
   smallest true Sharpe this study could have detected at all -- otherwise
   the number reads as a far stronger negative claim than the data supports.
"""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from research import experiment_log, retrospective
from research.retrospective import (
    DEFAULT_DSR_THRESHOLD,
    VERDICT_INCONCLUSIVE_DATA_LIMITED,
    VERDICT_REJECTED,
    VERDICT_REJECTED_UNDERPOWERED,
    VERDICT_SURVIVES,
    _build_parser,
    assign_verdict,
    build_retrospective,
    detection_floor_sharpe,
    distinct_multi_fold_runs,
    infer_bars_per_day,
    main,
    render_markdown_table,
    trial_sharpe_ratios,
)

MS_PER_HOUR = 3_600_000
MS_PER_15M = 900_000
MS_PER_DAY = 86_400_000

# The real 1h research window's own arithmetic, written out as constants
# rather than read from the (gitignored) log: 16,078 bars of 1h data spans
# 16,077 hours.
ONE_HOUR_WINDOW_YEARS = (16_078 - 1) * MS_PER_HOUR / (365 * MS_PER_DAY)


def _fold(sharpe: float | None, *, num_trades: int = 5, index: int = 0) -> dict:
    return {
        "fold_index": index,
        "train_start_index": index * 720,
        "train_end_index": index * 720 + 2160,
        "validate_start_index": index * 720 + 2160,
        "validate_end_index": index * 720 + 2880,
        "metrics": {
            "sharpe_ratio": sharpe,
            "num_trades": num_trades,
            "total_return": "0.01",
            "max_drawdown": "0.02",
            "profit_factor": 1.5,
            "win_rate": 0.5,
        },
    }


def _log_run(
    runs_path,
    *,
    strategy_id: str,
    run_id: str,
    fold_sharpes,
    strategy_version: str = "v1",
    num_bars: int = 16_078,
    start_ms: int = 1_714_212_000_000,
    interval_ms: int = MS_PER_HOUR,
    total_trades: int | None = None,
    parent_run_id: str | None = None,
    candidate_index: int | None = None,
    total_candidates: int | None = None,
    strategy_family: str | None = None,
    walk_forward_config: dict | None = None,
) -> dict:
    folds = [_fold(s, index=i) for i, s in enumerate(fold_sharpes)]
    defined = [s for s in fold_sharpes if s is not None]
    return experiment_log.log_run(
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        params={},
        fold_results=folds,
        aggregate_metrics={
            "fold_count": len(folds),
            "mean_sharpe": (sum(defined) / len(defined)) if defined else None,
            "total_trades": total_trades if total_trades is not None else 5 * len(folds),
        },
        data_range={
            "start_ms": start_ms,
            "end_ms": start_ms + (num_bars - 1) * interval_ms,
            "num_bars": num_bars,
        },
        walk_forward_config=walk_forward_config
        or {"train_bars": 2160, "validate_bars": 720, "step_bars": 720, "fold_count": len(folds)},
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        is_holdout_run=False,
        parent_run_id=parent_run_id,
        candidate_index=candidate_index,
        total_candidates=total_candidates,
        strategy_family=strategy_family,
        runs_path=runs_path,
    )


# ---------------------------------------------------------------------------
# The detection floor
# ---------------------------------------------------------------------------


class TestDetectionFloor:
    def test_reproduces_the_1h_research_windows_own_floor(self):
        """~1.21 annualized Sharpe over the 1h research window -- the
        governing arithmetic behind this whole report's honesty clause.
        """
        assert detection_floor_sharpe(ONE_HOUR_WINDOW_YEARS) == pytest.approx(1.214, abs=1e-3)

    def test_matches_the_z_over_sqrt_years_closed_form(self):
        # 1.6448536... = Phi^-1(0.95), the one-sided alpha=0.05 critical value.
        assert detection_floor_sharpe(4.0) == pytest.approx(1.6448536269514722 / 2.0, rel=1e-12)

    def test_falls_as_the_window_lengthens(self):
        assert detection_floor_sharpe(10.0) < detection_floor_sharpe(2.0) < detection_floor_sharpe(0.5)

    def test_a_stricter_alpha_raises_the_floor(self):
        assert detection_floor_sharpe(2.0, alpha=0.01) > detection_floor_sharpe(2.0, alpha=0.05)

    @pytest.mark.parametrize("years", [0.0, -1.0])
    def test_no_usable_span_yields_none_rather_than_a_fabricated_number(self, years):
        assert detection_floor_sharpe(years) is None

    @pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
    def test_an_impossible_alpha_raises(self, alpha):
        with pytest.raises(ValueError):
            detection_floor_sharpe(2.0, alpha=alpha)


# ---------------------------------------------------------------------------
# bars_per_day inference
# ---------------------------------------------------------------------------


class TestInferBarsPerDay:
    def test_a_logged_bars_per_day_wins(self):
        record = {
            "walk_forward_config": {"bars_per_day": 24},
            "data_range": {"start_ms": 0, "end_ms": 99 * MS_PER_15M, "num_bars": 100},
        }
        assert infer_bars_per_day(record) == 24

    @pytest.mark.parametrize(
        "interval_ms,expected",
        [(MS_PER_15M, 96), (MS_PER_HOUR, 24), (MS_PER_DAY, 1), (5 * 60_000, 288)],
    )
    def test_derives_the_bar_interval_from_the_data_range(self, interval_ms, expected):
        start_ms = 1_714_212_000_000
        record = {
            "walk_forward_config": {},
            "data_range": {"start_ms": start_ms, "end_ms": start_ms + 99 * interval_ms, "num_bars": 100},
        }
        assert infer_bars_per_day(record) == expected

    @pytest.mark.parametrize(
        "data_range",
        [
            {"start_ms": 0, "end_ms": 0, "num_bars": 1},  # a single bar spans nothing
            {"start_ms": 0, "end_ms": 100, "num_bars": 100},  # interval does not divide a day
            {"num_bars": 100},  # missing endpoints
            {},
        ],
    )
    def test_unusable_data_range_yields_none_rather_than_a_guess(self, data_range):
        assert infer_bars_per_day({"walk_forward_config": {}, "data_range": data_range}) is None


# ---------------------------------------------------------------------------
# De-duplicating reproductions
# ---------------------------------------------------------------------------


class TestDistinctMultiFoldRuns:
    def test_identical_fold_results_collapse_to_one_row_keeping_the_first(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="first", fold_sharpes=[1.0, -1.0, 0.5])
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="second", fold_sharpes=[1.0, -1.0, 0.5])
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum-configuration-c",
            run_id="third",
            strategy_version="renamed",
            fold_sharpes=[1.0, -1.0, 0.5],
        )

        runs = distinct_multi_fold_runs(list(experiment_log.read_records(runs_path)))

        assert len(runs) == 1
        assert runs[0].run_id == "first"
        assert runs[0].duplicate_run_ids == ["second", "third"]
        assert runs[0].run_count == 3

    def test_different_fold_results_stay_distinct(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="a", fold_sharpes=[1.0, -1.0])
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="b", fold_sharpes=[2.0, -1.0])

        runs = distinct_multi_fold_runs(list(experiment_log.read_records(runs_path)))

        assert [r.run_id for r in runs] == ["a", "b"]
        assert all(r.duplicate_run_ids == [] for r in runs)

    def test_the_same_folds_over_a_different_data_range_stay_distinct(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="a", fold_sharpes=[1.0, -1.0])
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="b",
            fold_sharpes=[1.0, -1.0],
            start_ms=1_600_000_000_000,
        )

        assert len(distinct_multi_fold_runs(list(experiment_log.read_records(runs_path)))) == 2

    def test_the_same_folds_in_different_families_stay_distinct(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="a", fold_sharpes=[1.0, -1.0])
        _log_run(runs_path, strategy_id="mean-reversion", run_id="b", fold_sharpes=[1.0, -1.0])

        runs = distinct_multi_fold_runs(list(experiment_log.read_records(runs_path)))

        assert [r.family for r in runs] == ["trend-momentum", "mean-reversion"]

    def test_single_fold_and_non_backtest_records_are_excluded(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="probe", fold_sharpes=[1.0])
        experiment_log.log_holdout_access(
            strategy_id="ensemble-momentum",
            symbol="BTC-USDT",
            interval="1h",
            start_ms=0,
            end_ms=1,
            runs_path=runs_path,
        )
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="real", fold_sharpes=[1.0, -1.0])

        runs = distinct_multi_fold_runs(list(experiment_log.read_records(runs_path)))

        assert [r.run_id for r in runs] == ["real"]

    def test_a_logged_strategy_family_beats_the_curated_map(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="a",
            fold_sharpes=[1.0, -1.0],
            strategy_family="something-else",
        )

        assert distinct_multi_fold_runs(list(experiment_log.read_records(runs_path)))[0].family == "something-else"


# ---------------------------------------------------------------------------
# One trial Sharpe per counted selection trial (V_hat's input)
# ---------------------------------------------------------------------------


class TestTrialSharpeRatios:
    def test_one_value_per_grid_candidate_averaged_across_its_folds(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        for fold_index, sharpe in enumerate([1.0, 3.0]):
            _log_run(
                runs_path,
                strategy_id="ensemble-momentum",
                run_id=f"c0-f{fold_index}",
                fold_sharpes=[sharpe],
                parent_run_id="grid",
                candidate_index=0,
                total_candidates=2,
            )
        for fold_index, sharpe in enumerate([-2.0, -4.0]):
            _log_run(
                runs_path,
                strategy_id="ensemble-momentum",
                run_id=f"c1-f{fold_index}",
                fold_sharpes=[sharpe],
                parent_run_id="grid",
                candidate_index=1,
                total_candidates=2,
            )

        by_family = trial_sharpe_ratios(list(experiment_log.read_records(runs_path)))

        assert sorted(by_family["trend-momentum"]) == [-3.0, 2.0]

    def test_sensitivity_probes_are_excluded(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="probe-prefixed",
            fold_sharpes=[9.0],
            parent_run_id="sensitivity:grid",
            candidate_index=0,
            total_candidates=1,
        )
        # The historical shape: standalone AND single-fold.
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="probe-historical", fold_sharpes=[8.0])
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="real", fold_sharpes=[1.0, -1.0])

        assert trial_sharpe_ratios(list(experiment_log.read_records(runs_path))) == {"trend-momentum": [0.0]}

    def test_a_candidate_with_no_defined_sharpe_is_reported_as_none_not_dropped(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="c0",
            fold_sharpes=[None],
            parent_run_id="grid",
            candidate_index=0,
            total_candidates=1,
        )

        assert trial_sharpe_ratios(list(experiment_log.read_records(runs_path))) == {"trend-momentum": [None]}

    def test_families_are_kept_separate(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="a", fold_sharpes=[1.0, 1.0])
        _log_run(runs_path, strategy_id="obv-trend", run_id="b", fold_sharpes=[-2.0, -2.0])

        assert trial_sharpe_ratios(list(experiment_log.read_records(runs_path))) == {
            "trend-momentum": [1.0],
            "volume": [-2.0],
        }


# ---------------------------------------------------------------------------
# Verdict assignment -- every boundary
# ---------------------------------------------------------------------------


def _verdict(**overrides) -> str:
    kwargs = {
        "dsr_project": 0.5,
        "mean_fold_sharpe": -1.0,
        "total_trades": 500,
        "detection_floor": 1.21,
        "min_total_trades": 100,
        "plausible_max_true_sharpe": 1.0,
        "dsr_threshold": DEFAULT_DSR_THRESHOLD,
    }
    kwargs.update(overrides)
    return assign_verdict(**kwargs).verdict


class TestAssignVerdict:
    def test_dsr_at_the_threshold_survives(self):
        assert _verdict(dsr_project=DEFAULT_DSR_THRESHOLD, mean_fold_sharpe=2.0) == VERDICT_SURVIVES

    def test_dsr_a_hair_below_the_threshold_does_not_survive(self):
        assert _verdict(dsr_project=DEFAULT_DSR_THRESHOLD - 1e-12, mean_fold_sharpe=2.0) != VERDICT_SURVIVES

    def test_a_negative_point_estimate_below_the_dsr_threshold_is_rejected(self):
        assert _verdict(dsr_project=0.5, mean_fold_sharpe=-1.0) == VERDICT_REJECTED

    def test_a_point_estimate_of_exactly_zero_is_rejected_not_underpowered(self):
        assert _verdict(dsr_project=0.5, mean_fold_sharpe=0.0) == VERDICT_REJECTED

    def test_a_positive_point_estimate_under_an_unreachable_floor_is_underpowered(self):
        assert (
            _verdict(dsr_project=0.5, mean_fold_sharpe=0.04, detection_floor=1.21, plausible_max_true_sharpe=1.0)
            == VERDICT_REJECTED_UNDERPOWERED
        )

    def test_a_positive_point_estimate_in_an_adequately_powered_study_is_rejected(self):
        """The one cell the four-label vocabulary leaves implicit: the study
        *could* have detected a plausible edge and still did not. That is a
        real rejection, not an underpowered one -- and the reason string must
        say which.
        """
        result = assign_verdict(
            dsr_project=0.5,
            mean_fold_sharpe=0.04,
            total_trades=500,
            detection_floor=0.4,
            min_total_trades=100,
            plausible_max_true_sharpe=1.0,
            dsr_threshold=DEFAULT_DSR_THRESHOLD,
        )
        assert result.verdict == VERDICT_REJECTED
        assert "adequately powered" in result.reason

    def test_the_floor_exactly_equal_to_the_plausible_edge_counts_as_adequately_powered(self):
        assert _verdict(dsr_project=0.5, mean_fold_sharpe=0.04, detection_floor=1.0, plausible_max_true_sharpe=1.0) == (
            VERDICT_REJECTED
        )

    def test_below_the_trade_floor_is_data_limited(self):
        assert _verdict(total_trades=14, mean_fold_sharpe=-0.48) == VERDICT_INCONCLUSIVE_DATA_LIMITED

    def test_exactly_at_the_trade_floor_is_not_data_limited(self):
        assert _verdict(total_trades=100, mean_fold_sharpe=-0.48) == VERDICT_REJECTED

    def test_the_trade_floor_is_checked_before_any_dsr_label(self):
        """Deliberate precedence: "this study cannot conclude" is not
        overwritten by "shown" or "not shown". A 14-trade run with a
        spectacular DSR is still data-limited.
        """
        assert _verdict(total_trades=14, mean_fold_sharpe=5.0, dsr_project=0.999) == (
            VERDICT_INCONCLUSIVE_DATA_LIMITED
        )

    def test_a_missing_trade_count_is_data_limited(self):
        assert _verdict(total_trades=None) == VERDICT_INCONCLUSIVE_DATA_LIMITED

    def test_an_undefined_dsr_is_treated_as_below_the_threshold(self):
        assert _verdict(dsr_project=None, mean_fold_sharpe=-1.0) == VERDICT_REJECTED

    def test_an_undefined_point_estimate_is_data_limited(self):
        assert _verdict(dsr_project=None, mean_fold_sharpe=None, total_trades=0) == (
            VERDICT_INCONCLUSIVE_DATA_LIMITED
        )

    def test_an_undefined_detection_floor_never_claims_adequate_power(self):
        result = assign_verdict(
            dsr_project=0.5,
            mean_fold_sharpe=0.04,
            total_trades=500,
            detection_floor=None,
            min_total_trades=100,
            plausible_max_true_sharpe=1.0,
            dsr_threshold=DEFAULT_DSR_THRESHOLD,
        )
        assert result.verdict == VERDICT_REJECTED_UNDERPOWERED

    def test_the_detection_floor_is_carried_on_the_result(self):
        assert assign_verdict(
            dsr_project=0.5,
            mean_fold_sharpe=-1.0,
            total_trades=500,
            detection_floor=1.21,
            min_total_trades=100,
            plausible_max_true_sharpe=1.0,
            dsr_threshold=DEFAULT_DSR_THRESHOLD,
        ).detection_floor == pytest.approx(1.21)


# ---------------------------------------------------------------------------
# End-to-end report
# ---------------------------------------------------------------------------


class TestBuildRetrospective:
    def _two_family_log(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        # A 10-fold run (not the real log's 19 -- fold count is deliberately
        # irrelevant to everything asserted here), plus a duplicate of it
        # under a rename.
        winner = [0.5, -0.4, 0.3, -0.2, 0.1, 0.6, -0.5, 0.2, -0.1, 0.4]
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="winner", fold_sharpes=winner, total_trades=400)
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum-configuration-c",
            run_id="winner-rerun",
            strategy_version="renamed",
            fold_sharpes=winner,
            total_trades=400,
        )
        # A grid search that supplies real per-trial dispersion.
        for candidate_index, sharpe in enumerate([-3.0, -1.0, 1.0, 2.0]):
            _log_run(
                runs_path,
                strategy_id="ensemble-momentum",
                run_id=f"cand-{candidate_index}",
                fold_sharpes=[sharpe],
                parent_run_id="grid",
                candidate_index=candidate_index,
                total_candidates=4,
            )
        # A second family, so the project-level N genuinely exceeds the
        # family-level one.
        _log_run(
            runs_path,
            strategy_id="obv-trend",
            run_id="volume",
            fold_sharpes=[-2.0, -3.0, -1.0, -2.5, -1.5],
            total_trades=300,
        )
        return runs_path

    def test_one_row_per_distinct_configuration(self, tmp_path):
        report = build_retrospective(
            runs_path=self._two_family_log(tmp_path), min_fold_consistency=Decimal("0.8")
        )

        assert [row.run_id for row in report.rows] == ["winner", "volume"]
        assert report.rows[0].duplicate_run_ids == ["winner-rerun"]
        assert report.total_multi_fold_runs == 3
        assert report.distinct_configurations == 2

    def test_project_n_exceeds_family_n_and_both_are_reported(self, tmp_path):
        report = build_retrospective(
            runs_path=self._two_family_log(tmp_path), min_fold_consistency=Decimal("0.8")
        )

        winner = report.rows[0]
        assert winner.family == "trend-momentum"
        # 4 grid candidates + 2 standalone walk-forward runs. The renamed
        # re-run counts toward N (sr-p reports reproductions, never merges
        # them away) even though it collapses into one *reported row* here.
        assert winner.family_n == 6
        assert winner.project_n == 7  # + the volume family's single standalone run
        assert winner.dsr_family is not None and winner.dsr_project is not None

    def test_every_row_carries_a_detection_floor_and_the_statistics(self, tmp_path):
        report = build_retrospective(
            runs_path=self._two_family_log(tmp_path), min_fold_consistency=Decimal("0.8")
        )

        for row in report.rows:
            assert row.detection_floor is not None
            assert row.sign_test_p is not None
            assert row.t_test_p is not None
            assert row.psr_n1 is not None
            assert row.verdict in {
                VERDICT_REJECTED,
                VERDICT_REJECTED_UNDERPOWERED,
                VERDICT_INCONCLUSIVE_DATA_LIMITED,
                VERDICT_SURVIVES,
            }
            assert "detection_floor" in row.to_dict()

    def test_fold_consistency_is_the_fraction_of_positive_folds(self, tmp_path):
        report = build_retrospective(
            runs_path=self._two_family_log(tmp_path), min_fold_consistency=Decimal("0.8")
        )

        assert report.rows[0].fold_consistency == pytest.approx(0.6)
        assert report.rows[1].fold_consistency == pytest.approx(0.0)

    def test_an_empty_log_produces_an_empty_report_rather_than_raising(self, tmp_path):
        report = build_retrospective(
            runs_path=tmp_path / "nothing.jsonl", min_fold_consistency=Decimal("0.8")
        )

        assert report.rows == []
        assert report.distinct_configurations == 0

    def test_the_report_is_json_serializable(self, tmp_path):
        report = build_retrospective(
            runs_path=self._two_family_log(tmp_path), min_fold_consistency=Decimal("0.8")
        )

        assert json.loads(json.dumps(report.to_dict()))["distinct_configurations"] == 2


class TestUnresolvableTrialCount:
    """CodeRabbit review finding on PR #53: an unresolvable trial count must
    surface as `None` plus a note, never as a silent `0`.

    A literal `N=0` happens to propagate harmlessly -- `deflated_sharpe_
    benchmark` returns `None` below 1, so `dsr` would be `None` anyway and
    `SURVIVES` stays unreachable -- but it would do so with nothing in
    `notes` explaining why a row lost its DSR. `resolve_family` deliberately
    returns an unmapped `strategy_id` as its own single-member family, so
    this is a reachable state, not a theoretical one.
    """

    def test_an_unmapped_family_still_gets_a_real_count_never_zero(self, tmp_path):
        """`resolve_family` returns an unmapped `strategy_id` as its own
        single-member family, so the family IS present in sr-p's results and
        gets a genuine count of 1 -- not the 0 the old fallback produced for
        anything it could not look up.
        """
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="brand-new-idea",
            run_id="unmapped",
            fold_sharpes=[0.5, -0.4, 0.3],
            total_trades=400,
        )

        row = build_retrospective(runs_path=runs_path, min_fold_consistency=Decimal("0.8")).rows[0]

        assert row.family == "brand-new-idea"
        assert row.family_n == 1
        assert row.project_n == 1

    def test_an_unrecognized_purpose_leaves_the_project_dsr_undefined_with_a_note(self, tmp_path, monkeypatch):
        """The defensive branch itself, exercised end to end.

        `research/lineage.py` only ever emits `"research"`/`"infrastructure"`
        today, so this branch is unreachable through the public API as it
        stands -- which is exactly why it is pinned here rather than left as
        untested defensive code that could rot into a silent `N = 0`.
        """
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="odd-purpose",
            fold_sharpes=[0.5, -0.4, 0.3],
            total_trades=400,
        )

        real_resolve = retrospective.resolve_family

        def _exotic_purpose(strategy_id, record=None):
            return replace(real_resolve(strategy_id, record), purpose="diagnostic")

        monkeypatch.setattr(retrospective, "resolve_family", _exotic_purpose)

        report = build_retrospective(runs_path=runs_path, min_fold_consistency=Decimal("0.8"))
        row = report.rows[0]

        assert row.project_n is None
        assert row.dsr_project is None
        assert row.verdict != VERDICT_SURVIVES
        assert any("no sr-p selection-trial count available" in note for note in report.notes)

    def test_a_none_trial_count_never_produces_survives(self):
        """The safety property behind the whole finding."""
        assert _verdict(dsr_project=None, mean_fold_sharpe=5.0, total_trades=500) != VERDICT_SURVIVES

    def test_an_undefined_trial_count_renders_as_na_not_zero(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="only",
            fold_sharpes=[0.5, -0.4, 0.3],
            total_trades=400,
        )
        report = build_retrospective(runs_path=runs_path, min_fold_consistency=Decimal("0.8"))
        row = report.rows[0]
        blanked = replace(row, family_n=None, project_n=None)
        blanked_report = replace(report, rows=[blanked])

        body = [line for line in render_markdown_table(blanked_report).splitlines() if line.startswith("|")][2:]

        assert "| n/a | n/a |" in body[0]
        assert " 0 |" not in body[0].replace("| 0.0", "")


class TestFractionArgument:
    """CodeRabbit review finding on PR #53. The finding was right -- an
    unparseable `--min-fold-consistency` escaped as a raw traceback -- but
    its suggested `type=Decimal` does NOT fix it: `argparse` only converts
    `TypeError`/`ValueError` into a usage error, and
    `decimal.InvalidOperation` is an `ArithmeticError`.
    """

    def test_a_valid_fraction_parses(self):
        assert _build_parser().parse_args(["--min-fold-consistency", "0.85"]).min_fold_consistency == Decimal("0.85")

    def test_the_default_is_the_permissive_end_of_the_approved_range(self):
        assert _build_parser().parse_args([]).min_fold_consistency == Decimal("0.80")

    @pytest.mark.parametrize("bad", ["abc", "", "nan%"])
    def test_an_unparseable_value_is_a_usage_error_not_a_traceback(self, bad):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--min-fold-consistency", bad])

    @pytest.mark.parametrize("bad", ["0", "-0.5", "1.5"])
    def test_an_out_of_range_value_is_a_usage_error(self, bad):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--min-fold-consistency", bad])

    @pytest.mark.parametrize("bad", ["NaN", "sNaN", "-NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_decimal_is_a_usage_error_not_a_traceback(self, bad):
        """`Decimal` PARSES these, then raises `InvalidOperation` from the
        `0 < value <= 1` comparison -- an `ArithmeticError`, which `argparse`
        does not convert. Caught by the `is_finite()` check.
        """
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--min-fold-consistency", bad])

    def test_exactly_one_is_allowed(self):
        assert _build_parser().parse_args(["--min-fold-consistency", "1"]).min_fold_consistency == Decimal(1)


class TestMain:
    """The CLI path the close-out document's own reproduction command uses."""

    def _log(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="only",
            fold_sharpes=[0.5, -0.4, 0.3],
            total_trades=400,
        )
        return runs_path

    def test_markdown_output_carries_the_detection_floor_column(self, tmp_path, capsys):
        exit_code = main(["--runs-path", str(self._log(tmp_path)), "--min-fold-consistency", "0.80"])

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "detection floor" in out
        assert "ensemble-momentum" in out

    def test_json_output_is_parseable_and_carries_every_row(self, tmp_path, capsys):
        exit_code = main(["--runs-path", str(self._log(tmp_path)), "--format", "json"])

        payload = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert payload["distinct_configurations"] == 1
        assert payload["rows"][0]["detection_floor"] is not None
        assert payload["min_fold_consistency"] == "0.80"

    def test_notes_are_printed_after_the_markdown_table(self, tmp_path, capsys, monkeypatch):
        real_resolve = retrospective.resolve_family
        monkeypatch.setattr(
            retrospective,
            "resolve_family",
            lambda strategy_id, record=None: replace(real_resolve(strategy_id, record), purpose="diagnostic"),
        )

        main(["--runs-path", str(self._log(tmp_path))])

        assert "NOTE: " in capsys.readouterr().out

    def test_a_missing_log_produces_an_empty_table_rather_than_an_error(self, tmp_path, capsys):
        exit_code = main(["--runs-path", str(tmp_path / "absent.jsonl")])

        assert exit_code == 0
        assert "detection floor" in capsys.readouterr().out


class TestRenderMarkdownTable:
    def test_the_detection_floor_cannot_be_rendered_away(self, tmp_path):
        """The structural honesty requirement: a reader cannot see a verdict
        without the study's own detection floor next to it.
        """
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(
            runs_path,
            strategy_id="ensemble-momentum",
            run_id="only",
            fold_sharpes=[0.5, -0.4, 0.3],
            total_trades=400,
        )
        report = build_retrospective(runs_path=runs_path, min_fold_consistency=Decimal("0.8"))

        table = render_markdown_table(report)
        header, _separator, *body = [line for line in table.splitlines() if line.startswith("|")]

        assert "detection floor" in header.lower()
        assert body, "expected at least one rendered row"
        for line in body:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            floor_index = [c.strip().lower() for c in header.strip("|").split("|")].index("detection floor")
            assert cells[floor_index] not in {"", "-"}

    def test_every_distinct_configuration_gets_exactly_one_row(self, tmp_path):
        runs_path = tmp_path / "experiments.jsonl"
        _log_run(runs_path, strategy_id="ensemble-momentum", run_id="a", fold_sharpes=[0.5, -0.4])
        _log_run(runs_path, strategy_id="obv-trend", run_id="b", fold_sharpes=[-1.0, -2.0])
        report = build_retrospective(runs_path=runs_path, min_fold_consistency=Decimal("0.8"))

        body = [line for line in render_markdown_table(report).splitlines() if line.startswith("|")][2:]

        assert len(body) == 2
