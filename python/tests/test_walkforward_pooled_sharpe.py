"""The aggregate reports the stitched-return Sharpe beside the mean of folds.

**Why both.** They answer different questions and differ by a lot once fold
volatility varies, which is what a regime shift looks like. Measured on synthetic
daily returns at this project's 1d geometry (12 folds x 60 sessions), drift held
fixed while volatility ranged 0.5x-1.5x across folds:

    true Sharpe 0.8  ->  mean-of-folds 1.241,  pooled 0.697

and with one fold at 8x volatility the mean barely moved (0.823 to 0.764) while
pooled fell to 0.327. **The mean of per-fold Sharpes is the optimistic one in
exactly the conditions a real strategy meets**, because a low-volatility fold
produces a large ratio and equal weighting carries it into the average.

`mean_sharpe` stays the *scored* figure — changing which statistic the
Eligibility Bar reads is a human-approved gate change. What this adds is the
ability to see when the two disagree, instead of assuming they do not.

The external review that prompted this is recorded in
`.planning/xr-f-phase2-result.md`.
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal

import pytest

from metrics.metrics import Metrics
from research.walkforward import FoldResult, _aggregate_metrics, _pooled_validation_sharpe


def _metrics(levels: list[float], *, sharpe: float | None, trades: int = 5) -> Metrics:
    """A `Metrics` carrying a real equity curve plus a stated fold Sharpe.

    The fold Sharpe is passed in rather than derived so a test can hold one
    constant while varying the curve, which is how the two statistics are pulled
    apart.
    """
    return Metrics(
        starting_equity=Decimal(str(levels[0])),
        final_equity=Decimal(str(levels[-1])),
        total_return=Decimal("0"),
        max_drawdown=Decimal("0"),
        sharpe_ratio=sharpe,
        num_trades=trades,
        win_rate=0.5,
        profit_factor=1.5,
        equity_curve=[Decimal(str(v)) for v in levels],
        closed_trades=[],
    )


def _fold(index: int, levels: list[float], *, sharpe: float | None, adjacent: bool = True) -> FoldResult:
    """One fold whose validation window abuts the previous one by default."""
    start = index * 100
    return FoldResult(
        fold_index=index,
        train_start_index=0,
        train_end_index=start,
        validate_start_index=start,
        validate_end_index=start + 100,
        metrics=_metrics(levels, sharpe=sharpe),
        backtest_result=None,  # type: ignore[arg-type]
    )


def _rising(n: int, step: float, start: float = 100.0) -> list[float]:
    return [start + i * step for i in range(n)]


def test_pooled_is_reported_alongside_the_mean():
    folds = [_fold(0, _rising(50, 0.5), sharpe=1.0), _fold(1, _rising(50, 0.5), sharpe=1.0)]
    agg = _aggregate_metrics(folds, bars_per_day=1)
    assert agg["mean_sharpe"] == pytest.approx(1.0)
    assert agg["pooled_validation_sharpe"] is not None
    assert agg["mean_minus_pooled_sharpe"] == pytest.approx(
        agg["mean_sharpe"] - agg["pooled_validation_sharpe"]
    )


def test_without_bars_per_day_nothing_is_computed():
    """Existing callers that pass no annualization get the old shape, with the
    new keys present and null rather than absent — a consumer reading the dict
    should not have to branch on whether a key exists."""
    folds = [_fold(0, _rising(50, 0.5), sharpe=1.0)]
    agg = _aggregate_metrics(folds)
    assert agg["pooled_validation_sharpe"] is None
    assert agg["mean_minus_pooled_sharpe"] is None
    assert agg["mean_sharpe"] == pytest.approx(1.0)


def test_overlapping_folds_return_none_rather_than_double_counting():
    """Stitching overlapping validation windows would count shared bars twice,
    and this project's rule is that a statistic over duplicated observations is
    not a statistic over independent ones."""
    a = _fold(0, _rising(50, 0.5), sharpe=1.0)
    b = _fold(1, _rising(50, 0.5), sharpe=1.0)
    overlapped = FoldResult(
        fold_index=1,
        train_start_index=0,
        train_end_index=50,
        validate_start_index=50,  # inside a's [0, 100)
        validate_end_index=150,
        metrics=b.metrics,
        backtest_result=None,  # type: ignore[arg-type]
    )
    assert _pooled_validation_sharpe([a, overlapped], bars_per_day=1) is None
    assert _aggregate_metrics([a, overlapped], bars_per_day=1)["pooled_validation_sharpe"] is None


def test_folds_arriving_out_of_order_still_pool():
    """**The sort's real job is avoiding a false positive, not catching a missed
    overlap**, and getting that backwards is why a first version of this test
    passed against an unsorted implementation.

    A Sharpe is a mean over a standard deviation, so pooling is
    order-independent. What order does affect is the adjacency scan: without a
    sort, two perfectly adjacent folds handed back in reverse read as
    `later.start (0) < earlier.end (200)` and the function refuses to pool
    anything. A caller returning results as they finish — a parallel evaluation —
    would silently lose this statistic.
    """
    first = _fold(0, _rising(50, 0.5), sharpe=1.0)   # validates [0, 100)
    second = _fold(1, _rising(50, 0.5), sharpe=1.0)  # validates [100, 200)

    in_order = _pooled_validation_sharpe([first, second], bars_per_day=1)
    reversed_order = _pooled_validation_sharpe([second, first], bars_per_day=1)

    assert in_order is not None
    assert reversed_order == pytest.approx(in_order), (
        "adjacent folds must pool to the same figure whatever order they arrive in"
    )


def test_fold_boundaries_do_not_inject_a_fabricated_return():
    """Each fold's curve is rebased to its own starting equity, so a naive
    `per_bar_returns` over the concatenation would read the jump from one fold's
    final level to the next fold's first level as a real return — one invented
    observation per boundary, twelve at the default geometry.

    Constructed so that jump would be enormous and unmistakable: fold 2 starts an
    order of magnitude below where fold 1 ended.
    """
    first = _fold(0, _rising(40, 1.0, start=1000.0), sharpe=1.0)
    second = _fold(1, _rising(40, 1.0, start=100.0), sharpe=1.0)
    pooled = _pooled_validation_sharpe([first, second], bars_per_day=1)
    assert pooled is not None

    # Both folds rise by exactly 1.0 per bar, so a boundary-free pooling sees two
    # runs of steadily shrinking positive returns and nothing negative. A
    # fabricated -90% return at the boundary would drag the mean below zero.
    assert pooled > 0, (
        "a boundary jump leaked in as a return; pooled Sharpe should stay positive "
        "when every real per-bar return is positive"
    )


def test_a_low_volatility_fold_inflates_the_mean_but_not_the_pooled():
    """The measured divergence, reproduced deterministically rather than by
    simulation: one quiet fold and one violent one, with the same drift.

    The quiet fold's own Sharpe is enormous (tiny denominator), so the mean of
    folds is dominated by it. Pooling divides by the whole span's dispersion,
    which the violent fold sets.
    """
    quiet = _fold(0, _rising(60, 0.01), sharpe=20.0)
    violent_levels = [100.0]
    for i in range(59):
        violent_levels.append(violent_levels[-1] * (1.10 if i % 2 else 0.92))
    violent = _fold(1, violent_levels, sharpe=0.2)

    agg = _aggregate_metrics([quiet, violent], bars_per_day=1)
    assert agg["mean_sharpe"] == pytest.approx(10.1)
    pooled = agg["pooled_validation_sharpe"]
    assert pooled is not None
    assert pooled < agg["mean_sharpe"], (
        "the whole point: equal-weighting a quiet fold's huge ratio must not "
        "survive pooling"
    )
    assert agg["mean_minus_pooled_sharpe"] > 1.0, (
        f"the gap should be large here, got {agg['mean_minus_pooled_sharpe']}"
    )


def test_a_zero_variance_stitch_is_none_not_zero():
    """`metrics.metrics`'s own convention: no evidence of risk-adjusted edge is
    `None`, never an edge of exactly zero."""
    flat = _fold(0, [100.0] * 50, sharpe=None)
    assert _pooled_validation_sharpe([flat], bars_per_day=1) is None


def test_the_annualization_matches_this_projects_convention():
    """`sqrt(bars_per_day * 365)`, 365 regardless of `bars_per_day`. Pinned
    because a second annualization convention in the codebase would make two
    reported Sharpes incomparable."""
    levels = [100.0, 101.0, 102.5, 103.0, 105.0, 104.0, 106.0]
    fold = _fold(0, levels, sharpe=1.0)
    got = _pooled_validation_sharpe([fold], bars_per_day=24)

    from metrics.metrics import per_bar_returns

    r = per_bar_returns([Decimal(str(v)) for v in levels])
    expected = (statistics.fmean(r) / statistics.stdev(r)) * math.sqrt(24 * 365)
    assert got == pytest.approx(expected)
