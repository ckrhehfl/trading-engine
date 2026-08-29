"""Tests for `research.conclusion_check`.

Each check exists because this project made that exact mistake, so each
gets a test that **replays the real incident with its real numbers**. A
check that only passes on invented fixtures is not evidence it would have
fired at the time.
"""

from __future__ import annotations

import pytest

from research.conclusion_check import (
    check_disjoint_intervals,
    BLOCKER,
    _fold_win_probability,
    WARNING,
    ConclusionCheckError,
    Finding,
    check_claim_monotonic,
    check_claim_universal,
    check_criterion_attainable,
    check_dsr_agrees,
    check_non_overlapping,
    check_parameter_swept,
    check_same_population,
    format_findings,
    require_no_blockers,
)


class TestNonOverlapping:
    def test_disjoint_positions_pass(self):
        assert check_non_overlapping([0, 100, 200], hold_bars=60) is None

    def test_touching_at_exactly_the_hold_boundary_still_overlaps(self):
        """A position entered `hold_bars` after another is still inside its
        window -- the boundary belongs to the earlier position."""
        assert check_non_overlapping([0, 60], hold_bars=60) is not None

    def test_one_bar_past_the_window_is_clean(self):
        assert check_non_overlapping([0, 61], hold_bars=60) is None

    def test_the_s13_incident_would_have_fired(self):
        """S13 took every qualifying bar as a position with a 60-bar hold;
        consecutive extreme bars cluster, so most overlapped."""
        clustered = [1000, 1001, 1002, 1003, 1004, 5000]
        finding = check_non_overlapping(clustered, hold_bars=60)
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "overlap" in finding.message

    def test_counts_the_overlaps_it_found(self):
        finding = check_non_overlapping([0, 1, 2, 500], hold_bars=60)
        assert finding is not None and "2 of 4" in finding.message

    def test_unordered_input_is_handled(self):
        assert check_non_overlapping([200, 0, 100], hold_bars=60) is None

    def test_zero_hold_means_only_identical_starts_collide(self):
        assert check_non_overlapping([0, 1, 2], hold_bars=0) is None
        assert check_non_overlapping([5, 5], hold_bars=0) is not None

    def test_empty_and_single_are_trivially_clean(self):
        assert check_non_overlapping([], hold_bars=60) is None
        assert check_non_overlapping([7], hold_bars=60) is None

    def test_negative_hold_is_rejected(self):
        with pytest.raises(ValueError, match="hold_bars must be non-negative"):
            check_non_overlapping([0], hold_bars=-1)


def _run(strategy_id, entry_z, family="btc-scalping"):
    return {"strategy_id": strategy_id, "strategy_family": family,
            "params": {"entry_z": entry_z, "max_hold_bars": 60}}


class TestParameterSwept:
    def test_two_distinct_values_pass(self):
        records = [_run("s", "5.0"), _run("s", "6.0")]
        assert check_parameter_swept(records, parameter="entry_z") is None

    def test_the_s14_s15_incident_would_have_fired(self):
        """Every walk-forward S14 and S15 ran used entry_z=5.0, and the
        conclusion was about the signal, not about 5.0."""
        records = [_run("selective-reversion", "5.0"),
                   _run("selective-reversion-no-stop", "5.0"),
                   _run("selective-reversion-no-stop", "5.0")]
        finding = check_parameter_swept(records, parameter="entry_z")
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "only 1 distinct value" in finding.message

    def test_repeated_identical_runs_do_not_count_as_a_sweep(self):
        records = [_run("s", "5.0") for _ in range(20)]
        assert check_parameter_swept(records, parameter="entry_z") is not None

    def test_filters_by_strategy_id(self):
        records = [_run("a", "5.0"), _run("b", "6.0")]
        assert check_parameter_swept(records, parameter="entry_z", strategy_id="a") is not None
        assert check_parameter_swept(records, parameter="entry_z") is None

    def test_filters_by_family(self):
        records = [_run("a", "5.0", family="btc-scalping"),
                   _run("b", "6.0", family="daily-tsmom")]
        finding = check_parameter_swept(records, parameter="entry_z", family="btc-scalping")
        assert finding is not None

    def test_a_parameter_absent_from_every_record_is_a_blocker(self):
        finding = check_parameter_swept([_run("s", "5.0")], parameter="stop_atr_multiple")
        assert finding is not None
        assert "not possible to tell" in finding.message

    def test_non_mapping_records_are_skipped_not_crashed_on(self):
        records = ["junk", None, _run("s", "5.0"), _run("s", "6.0")]
        assert check_parameter_swept(records, parameter="entry_z") is None

    def test_minimum_is_configurable(self):
        records = [_run("s", "5.0"), _run("s", "6.0")]
        assert check_parameter_swept(records, parameter="entry_z", minimum=3) is not None

    def test_minimum_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="minimum must be at least 1"):
            check_parameter_swept([], parameter="entry_z", minimum=0)


class TestCriterionAttainable:
    def test_the_s15_incident_would_have_fired(self):
        """83 folds, an 80% floor, a median of 2 trades per fold."""
        finding = check_criterion_attainable(
            num_folds=83, required_fraction=0.80, trades_per_fold=2
        )
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "67/83" in finding.message
        assert "2 trades per fold" in finding.message

    def test_trades_per_fold_CHANGES_the_verdict_rather_than_annotating_it(self):
        """The defect an earlier version had: the argument was accepted,
        put in the message, and left out of the arithmetic."""
        thin = check_criterion_attainable(num_folds=83, required_fraction=0.80, trades_per_fold=2)
        thick = check_criterion_attainable(num_folds=83, required_fraction=0.80, trades_per_fold=500)
        assert thin is not None, "2 trades per fold cannot support a fold-sign bar"
        assert thick is None, "500 trades per fold can"

    def test_fold_win_probability_rises_with_trade_count(self):
        """The mechanism: a fold's sign only reflects the edge once there
        are enough trades in it for the majority to be informative."""
        probs = [_fold_win_probability(0.55, k) for k in (2, 6, 20, 50, 200)]
        assert probs == sorted(probs)
        assert probs[0] < 0.5, "at 2 trades a fold's sign is worse than a coin flip"
        assert probs[-1] > 0.9

    def test_a_reachable_criterion_passes(self):
        assert check_criterion_attainable(num_folds=83, required_fraction=0.50) is None

    def test_trades_per_fold_is_optional_and_falls_back(self):
        finding = check_criterion_attainable(num_folds=83, required_fraction=0.80)
        assert finding is not None and "trades per fold" not in finding.message

    def test_a_stronger_assumed_edge_can_make_the_bar_reachable(self):
        assert check_criterion_attainable(
            num_folds=83, required_fraction=0.80, plausible_win_rate=0.90
        ) is None

    @pytest.mark.parametrize(
        "kwargs,message",
        [
            ({"num_folds": 0, "required_fraction": 0.8}, "num_folds must be at least 1"),
            ({"num_folds": 10, "required_fraction": 0.0}, r"required_fraction must be in \(0, 1\]"),
            ({"num_folds": 10, "required_fraction": 1.5}, r"required_fraction must be in \(0, 1\]"),
            ({"num_folds": 10, "required_fraction": 0.8, "trades_per_fold": 0},
             "trades_per_fold must be positive"),
        ],
    )
    def test_rejects_degenerate_inputs(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            check_criterion_attainable(**kwargs)


class TestSamePopulation:
    def test_identical_observations_pass(self):
        assert check_same_population({"with_stop": [1, 2, 3], "no_stop": [1, 2, 3]}) is None

    def test_same_size_but_DIFFERENT_observations_is_a_blocker(self):
        """The failure a size-only check waves through: two statistics
        over 3 unrelated observations each."""
        finding = check_same_population({"a": [1, 2, 3], "b": [4, 5, 6]})
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "DIFFERENT observations" in finding.message

    def test_the_s15_stop_table_incident_would_have_fired(self):
        """The real shape: the `none` row averaged over every position
        while the stop rows excluded those with no MAE reading."""
        every_position = list(range(526))
        measurable_only = list(range(520))
        finding = check_same_population(
            {"stop rows": measurable_only, "none row": every_position}
        )
        assert finding is not None
        assert finding.severity == BLOCKER

    def test_reports_what_each_side_is_missing(self):
        finding = check_same_population({"a": [1, 2, 3], "b": [1, 2]})
        assert finding is not None
        assert "1 observation(s) present in a but not here" in finding.message

    def test_a_single_sample_is_trivially_consistent(self):
        assert check_same_population({"only": [1, 2, 3]}) is None

    def test_unhashable_or_duplicated_values_fall_back_and_SAY_SO(self):
        """A weaker check must never be mistaken for the strong one."""
        finding = check_same_population({"a": [1.0, 1.0, 2.0], "b": [1.0, 1.0, 2.0]})
        assert finding is not None
        assert finding.severity == WARNING
        assert "weak form" in finding.message
        assert "Pass identifiers" in finding.message

    def test_different_sizes_still_block_when_membership_is_uncomparable(self):
        finding = check_same_population({"a": [1.0, 1.0], "b": [2.0, 2.0, 2.0]})
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "a=2" in finding.message and "b=3" in finding.message


class TestClaimMonotonic:
    def test_the_s15_delay_series_would_have_fired(self):
        """The real series: immediate, +5, +10, +15, +30, +60 bars."""
        finding = check_claim_monotonic(
            [30.87, 18.92, 7.55, 9.23, -2.18, 3.93], direction="decreasing"
        )
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "7.55 -> 9.23" in finding.message

    def test_a_genuinely_decreasing_series_passes(self):
        assert check_claim_monotonic([5, 4, 3, 2], direction="decreasing") is None

    def test_a_genuinely_increasing_series_passes(self):
        assert check_claim_monotonic([1, 2, 3], direction="increasing") is None

    def test_equal_neighbours_are_not_a_break(self):
        assert check_claim_monotonic([3, 3, 2], direction="decreasing") is None

    def test_reports_at_most_three_breaks_then_counts_the_rest(self):
        # Four upward steps against a "decreasing" claim: three are shown
        # by name and the fourth is counted.
        finding = check_claim_monotonic([1, 0, 1, 0, 1, 0, 1, 0, 1], direction="decreasing")
        assert finding is not None and "and 1 more" in finding.message

    def test_short_series_are_trivially_monotonic(self):
        assert check_claim_monotonic([], direction="decreasing") is None
        assert check_claim_monotonic([1], direction="increasing") is None

    def test_rejects_an_unknown_direction(self):
        with pytest.raises(ValueError, match="direction must be"):
            check_claim_monotonic([1, 2], direction="sideways")


class TestClaimUniversal:
    def test_a_true_universal_passes(self):
        assert check_claim_universal([1, 2, 3], lambda v: v > 0, claim="all positive") is None

    def test_a_false_universal_fires_and_names_the_failures(self):
        finding = check_claim_universal(
            [1, -2, 3, -4], lambda v: v > 0, claim="all positive"
        )
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "2 of 4" in finding.message

    def test_the_delay_claim_would_have_fired_as_a_universal_too(self):
        """'every delay tested is worse' -- against the real net figures,
        measured against immediate entry's +18.87bps."""
        net_by_delay = [6.92, -4.45, -2.77, -14.18, -8.07]
        assert check_claim_universal(
            net_by_delay, lambda v: v < 18.87, claim="every delay is worse than immediate"
        ) is None

    def test_empty_input_is_vacuously_true(self):
        assert check_claim_universal([], lambda v: False, claim="anything") is None


class TestDsrAgrees:
    def test_matching_values_pass(self):
        assert check_dsr_agrees(computed=6.46139e-11, reference=6.46139e-11) is None

    def test_the_s15_disagreement_would_have_fired(self):
        """The real pair before the scorer was made to delegate."""
        finding = check_dsr_agrees(computed=1.92442e-11, reference=6.461386981015949e-11)
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "disagrees" in finding.message

    def test_both_undefined_is_consistent(self):
        assert check_dsr_agrees(computed=None, reference=None) is None

    def test_one_undefined_is_a_blocker(self):
        assert check_dsr_agrees(computed=0.5, reference=None) is not None
        assert check_dsr_agrees(computed=None, reference=0.5) is not None

    def test_a_zero_reference_requires_exact_zero(self):
        assert check_dsr_agrees(computed=0.0, reference=0.0) is None
        assert check_dsr_agrees(computed=1e-30, reference=0.0) is not None

    def test_tolerance_is_relative_not_absolute(self):
        # 1e-11 values differing in the 8th significant figure pass; a 3x
        # difference at the same magnitude does not.
        assert check_dsr_agrees(computed=1.000000e-11, reference=1.0000001e-11,
                                relative_tolerance=1e-5) is None
        assert check_dsr_agrees(computed=1e-11, reference=3e-11) is not None


class TestRequireNoBlockers:
    def _finding(self, severity):
        return Finding(check="c", severity=severity, message="m", scar="s")

    def test_passes_and_returns_warnings(self):
        warnings = require_no_blockers([None, self._finding(WARNING)])
        assert len(warnings) == 1

    def test_raises_on_a_blocker(self):
        with pytest.raises(ConclusionCheckError, match="conclusion blocked by 1 check"):
            require_no_blockers([self._finding(BLOCKER)])

    def test_the_message_carries_every_blocker(self):
        with pytest.raises(ConclusionCheckError) as exc:
            require_no_blockers([self._finding(BLOCKER), self._finding(BLOCKER)])
        assert "blocked by 2 check" in str(exc.value)

    def test_nones_are_ignored(self):
        assert require_no_blockers([None, None]) == []

    def test_a_warning_never_raises(self):
        require_no_blockers([self._finding(WARNING) for _ in range(5)])


class TestFormatFindings:
    def test_empty_says_so_explicitly(self):
        assert format_findings([]) == "all conclusion checks passed"
        assert format_findings([None, None]) == "all conclusion checks passed"

    def test_a_finding_renders_its_scar(self):
        text = format_findings([Finding(check="c", severity=BLOCKER, message="m", scar="the scar")])
        assert "BLOCKER" in text and "the scar" in text

    def test_a_warning_is_not_labelled_blocker(self):
        text = format_findings([Finding(check="c", severity=WARNING, message="m", scar="s")])
        assert "BLOCKER" not in text and "warning" in text


class TestPoissonBinomial:
    """Fold trade counts vary widely, so each fold has its own
    probability of ending positive and the tail is Poisson-binomial. A
    median collapsed to a binomial can report the wrong attainability."""

    def test_reduces_to_the_binomial_when_every_probability_is_equal(self):
        import math

        from research.conclusion_check import _poisson_binomial_tail

        n, p = 10, 0.5
        expected = sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(6, n + 1))
        assert _poisson_binomial_tail([p] * n, 6) == pytest.approx(expected, abs=1e-12)

    def test_a_certain_event_is_probability_one(self):
        from research.conclusion_check import _poisson_binomial_tail

        assert _poisson_binomial_tail([1.0, 1.0, 1.0], 3) == pytest.approx(1.0)

    def test_requiring_more_than_exist_is_impossible(self):
        from research.conclusion_check import _poisson_binomial_tail

        assert _poisson_binomial_tail([0.9, 0.9], 3) == 0.0

    def test_zero_trade_folds_cannot_be_positive(self):
        """The fact a median hides: a fold with no trades has probability
        zero, not the probability of the median fold."""
        finding = check_criterion_attainable(
            num_folds=83, required_fraction=0.80,
            trades_by_fold=[0] * 40 + [18] * 43,
        )
        assert finding is not None
        assert finding.severity == BLOCKER

    def test_a_uniform_series_agrees_with_the_scalar_form(self):
        uniform = check_criterion_attainable(
            num_folds=20, required_fraction=0.80, trades_by_fold=[6] * 20
        )
        scalar = check_criterion_attainable(
            num_folds=20, required_fraction=0.80, trades_per_fold=6
        )
        assert (uniform is None) == (scalar is None)

    def test_a_mismatched_series_length_is_refused(self):
        with pytest.raises(ValueError, match="must cover every fold"):
            check_criterion_attainable(
                num_folds=83, required_fraction=0.80, trades_by_fold=[6] * 10
            )

    def test_many_trades_per_fold_still_reaches_the_bar(self):
        assert check_criterion_attainable(
            num_folds=83, required_fraction=0.80, trades_by_fold=[500] * 83
        ) is None


class TestProbabilityParameterRanges:
    """A probability outside [0, 1] makes every binomial term
    meaningless, and `min_power=0` is worse: it passes every criterion
    regardless of how unreachable it is, silently DISABLING the check
    rather than failing it."""

    @pytest.mark.parametrize("kwargs,message", [
        ({"plausible_win_rate": -0.1}, r"plausible_win_rate must be in \[0, 1\]"),
        ({"plausible_win_rate": 1.5}, r"plausible_win_rate must be in \[0, 1\]"),
        ({"trade_win_rate": -0.1}, r"trade_win_rate must be in \[0, 1\]"),
        ({"trade_win_rate": 1.5}, r"trade_win_rate must be in \[0, 1\]"),
        ({"min_power": 0.0}, r"min_power must be in \(0, 1\]"),
        ({"min_power": -0.1}, r"min_power must be in \(0, 1\]"),
        ({"min_power": 1.5}, r"min_power must be in \(0, 1\]"),
    ])
    def test_out_of_range_probabilities_are_refused(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            check_criterion_attainable(num_folds=10, required_fraction=0.8, **kwargs)

    def test_min_power_zero_would_otherwise_have_disabled_the_check(self):
        """The specific danger: without validation this returns None for
        an unreachable criterion, which reads as 'the bar is fine'."""
        with pytest.raises(ValueError):
            check_criterion_attainable(
                num_folds=83, required_fraction=0.80, trades_per_fold=2, min_power=0.0
            )

    def test_the_boundary_values_are_accepted(self):
        check_criterion_attainable(
            num_folds=10, required_fraction=1.0,
            plausible_win_rate=1.0, trade_win_rate=1.0, min_power=1.0,
        )


class TestCheckDisjointIntervals:
    """Variable-duration positions -- what `metrics.book`'s legs produce.

    The uniform-`hold_bars` form cannot express these without either
    over-flagging (using the longest hold) or under-flagging (using the
    shortest), and both are wrong in a way that misdirects the analyst.
    """

    def test_disjoint_intervals_pass(self):
        assert check_disjoint_intervals([(0, 5), (5, 6), (20, 40)]) is None

    def test_touching_is_disjoint(self):
        # Half-open: [0,5) and [5,9) share no bar.
        assert check_disjoint_intervals([(0, 5), (5, 9)]) is None

    def test_real_overlap_blocks(self):
        finding = check_disjoint_intervals([(0, 10), (5, 15)])
        assert finding is not None
        assert finding.severity == BLOCKER
        assert "overlap" in finding.message

    def test_unsorted_input_is_ordered_first(self):
        finding = check_disjoint_intervals([(5, 15), (0, 10)])
        assert finding is not None and finding.severity == BLOCKER

    def test_short_holds_close_together_are_not_overlaps(self):
        # The exact case the uniform form got wrong: one-bar holds two
        # bars apart, judged against a three-bar longest hold.
        intervals = [(0, 1), (2, 3), (4, 5), (6, 9)]
        assert check_non_overlapping([0, 2, 4, 6], hold_bars=3) is not None
        assert check_disjoint_intervals(intervals) is None

    def test_clustering_warns_without_blocking(self):
        finding = check_disjoint_intervals([(0, 1), (2, 3)], clustering_gap=24)
        assert finding is not None
        assert finding.severity == WARNING
        assert "not independent" in finding.message

    def test_clustering_silent_when_far_apart(self):
        assert check_disjoint_intervals([(0, 1), (100, 101)], clustering_gap=24) is None

    def test_overlap_outranks_clustering(self):
        # A real overlap must never be downgraded to a warning just
        # because a clustering gap was also supplied.
        finding = check_disjoint_intervals([(0, 10), (5, 15)], clustering_gap=24)
        assert finding is not None and finding.severity == BLOCKER

    def test_backwards_interval_raises(self):
        with pytest.raises(ValueError, match="precedes start"):
            check_disjoint_intervals([(10, 5)])

    def test_negative_clustering_gap_raises(self):
        with pytest.raises(ValueError, match="clustering_gap"):
            check_disjoint_intervals([(0, 1)], clustering_gap=-1)

    def test_empty_and_single_are_clean(self):
        assert check_disjoint_intervals([]) is None
        assert check_disjoint_intervals([(0, 5)], clustering_gap=24) is None
