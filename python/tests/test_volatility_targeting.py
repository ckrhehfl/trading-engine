"""Tests for `python/research/strategies/volatility_targeting.py` --
shared rolling realized-volatility estimator and vol-target position-size
scalar introduced in Strategy Research Task H (see `.planning/sr-h-
ensemble-regime-voltargeting.md`).

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/volatility_targeting.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from research.strategies.volatility_targeting import (
    RollingRealizedVolatility,
    compute_vol_scalar,
)

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _kline(i: int, close: str) -> Kline:
    # Only close matters to this module's formulas -- open/high/low are
    # fixed placeholders.
    return Kline(
        open_time=BASE_TIME + timedelta(hours=i),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


# ---------------------------------------------------------------------------
# RollingRealizedVolatility
# ---------------------------------------------------------------------------


class TestRollingRealizedVolatility:
    def test_rejects_period_below_two(self):
        # period must be >= 2: a sample stdev (ddof=1) over fewer than 2
        # returns is undefined (division by zero in the variance formula).
        with pytest.raises(ValueError, match="period"):
            RollingRealizedVolatility(period=1)
        with pytest.raises(ValueError, match="period"):
            RollingRealizedVolatility(period=0)
        with pytest.raises(ValueError, match="period"):
            RollingRealizedVolatility(period=-1)

    def test_returns_none_during_warmup(self):
        # period=3 needs 4 closes (3 returns) before a first reading.
        vol = RollingRealizedVolatility(period=3, bars_per_day=365)
        assert vol.update(_kline(0, "100")) is None
        assert vol.update(_kline(1, "130")) is None
        assert vol.update(_kline(2, "143")) is None

    def test_hand_computed_stdev_and_annualization(self):
        # closes: 100 -> 130 -> 143 -> 128.7 gives returns [0.3, 0.1, -0.1]
        # (each exact: (130-100)/100=0.3, (143-130)/130=0.1,
        # (128.7-143)/143=-0.1). mean=0.1; deviations [0.2, 0, -0.2];
        # squared [0.04, 0, 0.04]; sample variance (ddof=1) =
        # 0.08/(3-1) = 0.04; stdev = sqrt(0.04) = 0.2 (exact).
        #
        # bars_per_day=365 is a deliberately artificial value for this
        # test only (not the strategies' real 24-bars/day default) chosen
        # so bars_per_day * 365 = 365**2 is a perfect square, giving an
        # exact annualization factor (365) rather than an irrational one
        # -- annualized = 0.2 * 365 = 73.0 exactly.
        vol = RollingRealizedVolatility(period=3, bars_per_day=365)
        vol.update(_kline(0, "100"))
        vol.update(_kline(1, "130"))
        vol.update(_kline(2, "143"))
        result = vol.update(_kline(3, "128.7"))
        assert result == Decimal("73.0")

    def test_rolling_window_drops_oldest_return(self):
        # Continuing the hand-computed scenario above: feeding a 5th close
        # of 128.7 (a 0.0 return: (128.7-128.7)/128.7=0) rolls the window
        # to returns [0.1, -0.1, 0.0] (0.3 dropped). mean=0; deviations
        # [0.1,-0.1,0.0]; squared [0.01,0.01,0]; variance=0.02/2=0.01;
        # stdev=sqrt(0.01)=0.1 (exact) -> annualized = 0.1*365 = 36.5,
        # genuinely different from the first window's 73.0.
        vol = RollingRealizedVolatility(period=3, bars_per_day=365)
        vol.update(_kline(0, "100"))
        vol.update(_kline(1, "130"))
        vol.update(_kline(2, "143"))
        first = vol.update(_kline(3, "128.7"))
        assert first == Decimal("73.0")
        second = vol.update(_kline(4, "128.7"))
        assert second == Decimal("36.5")
        assert second != first

    def test_zero_variance_returns_zero_not_none(self):
        # A perfectly flat price series has a valid, meaningful volatility
        # reading of exactly zero -- unlike metrics.metrics's Sharpe
        # (where zero-variance returns are None because variance is used
        # as a *denominator* there), this estimator's own arithmetic never
        # divides by the realized-vol figure it's computing -- that
        # divide-by-zero risk lives downstream, in compute_vol_scalar,
        # and is handled there via capping (see below), not by suppressing
        # a valid zero reading here.
        vol = RollingRealizedVolatility(period=3, bars_per_day=365)
        vol.update(_kline(0, "100"))
        vol.update(_kline(1, "100"))
        vol.update(_kline(2, "100"))
        result = vol.update(_kline(3, "100"))
        assert result == Decimal("0")

    def test_lookahead_safety_value_at_bar_k_unaffected_by_future_bars(self):
        """Same structural guarantee as `risk_management.AverageTrueRange`
        and `regime_weighting.AverageDirectionalIndex`'s identical tests.
        """
        prefix = [
            _kline(0, "100"),
            _kline(1, "130"),
            _kline(2, "143"),
            _kline(3, "128.7"),
        ]
        future = [
            _kline(4, "50"),  # deliberately extreme
            _kline(5, "500"),
        ]

        vol_prefix_only = RollingRealizedVolatility(period=3, bars_per_day=365)
        value_at_prefix_end = None
        for bar in prefix:
            value_at_prefix_end = vol_prefix_only.update(bar)

        vol_with_future = RollingRealizedVolatility(period=3, bars_per_day=365)
        captured_at_bar_3 = None
        for i, bar in enumerate(prefix + future):
            result = vol_with_future.update(bar)
            if i == 3:
                captured_at_bar_3 = result

        assert captured_at_bar_3 == value_at_prefix_end


# ---------------------------------------------------------------------------
# compute_vol_scalar
# ---------------------------------------------------------------------------


class TestComputeVolScalar:
    def test_realized_equals_target_gives_scalar_of_one(self):
        scalar = compute_vol_scalar(Decimal("0.20"), target_annualized_vol=Decimal("0.20"))
        assert scalar == Decimal("1")

    def test_realized_below_target_gives_scalar_above_one_calm_market_scales_up(self):
        # realized 0.10 vs target 0.20 -> scalar = 2.0 (well within default
        # max_scalar=3).
        scalar = compute_vol_scalar(Decimal("0.10"), target_annualized_vol=Decimal("0.20"))
        assert scalar == Decimal("2")

    def test_realized_above_target_gives_scalar_below_one_choppy_market_scales_down(self):
        # realized 0.40 vs target 0.20 -> scalar = 0.5.
        scalar = compute_vol_scalar(Decimal("0.40"), target_annualized_vol=Decimal("0.20"))
        assert scalar == Decimal("0.5")

    def test_scalar_is_capped_at_max_scalar_for_very_calm_markets(self):
        # realized 0.01 vs target 0.20 -> raw scalar = 20, capped to
        # max_scalar (default 3).
        scalar = compute_vol_scalar(Decimal("0.01"), target_annualized_vol=Decimal("0.20"))
        assert scalar == Decimal("3")

    def test_scalar_is_floored_at_min_scalar_for_very_volatile_markets(self):
        scalar = compute_vol_scalar(
            Decimal("100"), target_annualized_vol=Decimal("0.20"), min_scalar=Decimal("0.1"), max_scalar=Decimal("3")
        )
        assert scalar == Decimal("0.1")

    def test_zero_realized_vol_is_capped_at_max_scalar_not_a_zero_division_error(self):
        scalar = compute_vol_scalar(Decimal("0"), target_annualized_vol=Decimal("0.20"), max_scalar=Decimal("3"))
        assert scalar == Decimal("3")

    def test_none_realized_vol_propagates_to_none(self):
        # Distinct from regime_weighting.compute_regime_weight's None-in
        # convention (which bakes None down to zero weight directly) --
        # see this module's docstring for why the two differ: "no
        # realized-vol estimate yet" has no natural "off" value on this
        # scale the way ADX's low-threshold floor does, so the caller
        # must decide explicitly how to handle it (this project's
        # strategies choose to skip trading entirely until warmup
        # completes -- see single_lookback_momentum.py/ensemble_momentum.py).
        assert compute_vol_scalar(None) is None

    def test_default_target_and_bounds_match_documented_convention(self):
        # Default target 20% annualized (the commonly-cited institutional
        # convention this task documents, not a literal reproduction of
        # any single firm's exact formula), default bounds [0, 3].
        assert compute_vol_scalar(Decimal("0.20")) == Decimal("1")
        assert compute_vol_scalar(Decimal("0.001")) == Decimal("3")
        # A very large realized vol (1000 = 100,000% annualized) gives a
        # tiny but still-positive raw scalar (0.20/1000 = 0.0002) --
        # default min_scalar=0 only floors a scalar that would otherwise
        # go *negative*, which this arithmetic structurally never
        # produces (target/realized with both positive is always > 0).
        assert compute_vol_scalar(Decimal("1000")) == Decimal("0.0002")

    def test_rejects_non_positive_target(self):
        with pytest.raises(ValueError, match="target"):
            compute_vol_scalar(Decimal("0.1"), target_annualized_vol=Decimal("0"))
        with pytest.raises(ValueError, match="target"):
            compute_vol_scalar(Decimal("0.1"), target_annualized_vol=Decimal("-0.1"))

    def test_rejects_negative_min_scalar(self):
        with pytest.raises(ValueError, match="min_scalar"):
            compute_vol_scalar(Decimal("0.1"), min_scalar=Decimal("-1"))

    def test_rejects_max_scalar_below_min_scalar(self):
        with pytest.raises(ValueError, match="max_scalar"):
            compute_vol_scalar(Decimal("0.1"), min_scalar=Decimal("2"), max_scalar=Decimal("1"))
