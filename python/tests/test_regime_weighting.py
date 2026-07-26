"""Tests for `python/research/strategies/regime_weighting.py` -- shared
ADX (Average Directional Index) calculator and continuous regime-weight
ramp introduced in Strategy Research Task H (see `.planning/sr-h-ensemble-
regime-voltargeting.md`).

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/regime_weighting.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from research.strategies.regime_weighting import (
    AverageDirectionalIndex,
    compute_regime_weight,
)

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _kline(i: int, h: str, lo: str, c: str) -> Kline:
    # open is unused by ADX's formulas (only high/low/close matter), so a
    # fixed placeholder keeps every test fixture terse.
    return Kline(
        open_time=BASE_TIME + timedelta(hours=i),
        open=Decimal(c),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal("1"),
    )


# ---------------------------------------------------------------------------
# AverageDirectionalIndex
# ---------------------------------------------------------------------------


class TestAverageDirectionalIndex:
    def test_rejects_non_positive_period(self):
        with pytest.raises(ValueError, match="period"):
            AverageDirectionalIndex(period=0)
        with pytest.raises(ValueError, match="period"):
            AverageDirectionalIndex(period=-1)

    def test_returns_none_for_first_bar_ever(self):
        # No prior bar to compute True Range / directional movement against.
        adx = AverageDirectionalIndex(period=2)
        assert adx.update(_kline(0, "10", "8", "9")) is None

    def test_returns_none_during_warmup(self):
        adx = AverageDirectionalIndex(period=2)
        assert adx.update(_kline(0, "10", "8", "9")) is None
        assert adx.update(_kline(1, "12", "9", "11")) is None  # TR/DM window filling
        assert adx.update(_kline(2, "11", "9", "10")) is None  # TR/DM window full, DX window filling

    def test_hand_computed_four_bar_adx(self):
        # period=2. Bars: (high, low, close).
        # bar0=(10,8,9): first bar ever -- no prior high/low/close -> None.
        # bar1=(12,9,11): up_move=12-10=2, down_move=8-9=-1
        #   +DM = 2 (2 > -1 and 2 > 0); -DM = 0 (-1 is not > up_move)
        #   TR = max(12-9, |12-9|, |9-9|) = max(3,3,0) = 3
        #   TR/DM window: [3]/[2]/[0] -- len 1 < period(2) -> None
        # bar2=(11,9,10): up_move=11-12=-1, down_move=9-9=0
        #   +DM = 0 (-1 not > 0); -DM = 0 (0 not > up_move=-1... it IS > -1,
        #     but 0 is not strictly > 0, so -DM=0 too -- both DMs 0 here)
        #   TR = max(11-9, |11-11|, |9-11|) = max(2,0,2) = 2
        #   TR/DM window now full: TR=[3,2] sum=5, +DM=[2,0] sum=2, -DM=[0,0] sum=0
        #   +DI = 100*2/5 = 40, -DI = 100*0/5 = 0
        #   DX = 100*|40-0|/(40+0) = 100
        #   DX window: [100] -- len 1 < period(2) -> None
        # bar3=(13,10,12): up_move=13-11=2, down_move=9-10=-1
        #   +DM = 2 (2 > -1 and > 0); -DM = 0
        #   TR = max(13-10, |13-10|, |10-10|) = max(3,3,0) = 3
        #   TR/DM window rolls: TR=[2,3] sum=5, +DM=[0,2] sum=2, -DM=[0,0] sum=0
        #   +DI = 100*2/5 = 40, -DI = 0, DX = 100
        #   DX window: [100,100] -- len 2 == period(2) -> ADX = mean([100,100]) = 100
        adx = AverageDirectionalIndex(period=2)
        assert adx.update(_kline(0, "10", "8", "9")) is None
        assert adx.update(_kline(1, "12", "9", "11")) is None
        assert adx.update(_kline(2, "11", "9", "10")) is None
        result = adx.update(_kline(3, "13", "10", "12"))
        assert result == Decimal("100")

    def test_flat_market_true_range_and_dm_all_zero_yields_zero_dx(self):
        # Every bar identical -- TR=0, +DM=0, -DM=0. tr_sum == 0 is handled
        # as a degenerate case (DX=0, not a ZeroDivisionError) -- a
        # perfectly flat market has no directional movement to measure,
        # which is exactly what ADX=0 means.
        adx = AverageDirectionalIndex(period=2)
        assert adx.update(_kline(0, "10", "10", "10")) is None
        assert adx.update(_kline(1, "10", "10", "10")) is None
        assert adx.update(_kline(2, "10", "10", "10")) is None
        result = adx.update(_kline(3, "10", "10", "10"))
        assert result == Decimal("0")

    def test_lookahead_safety_value_at_bar_k_unaffected_by_future_bars(self):
        """Feeding the same prefix of bars must produce the same ADX
        value regardless of what (if anything) is fed afterward -- same
        structural guarantee as `risk_management.AverageTrueRange`'s
        identical test.
        """
        prefix = [
            _kline(0, "10", "8", "9"),
            _kline(1, "12", "9", "11"),
            _kline(2, "11", "9", "10"),
            _kline(3, "13", "10", "12"),
        ]
        future = [
            _kline(4, "50", "5", "8"),  # deliberately extreme
            _kline(5, "9", "7", "8"),
        ]

        adx_prefix_only = AverageDirectionalIndex(period=2)
        value_at_prefix_end = None
        for bar in prefix:
            value_at_prefix_end = adx_prefix_only.update(bar)

        adx_with_future = AverageDirectionalIndex(period=2)
        captured_at_bar_3 = None
        for i, bar in enumerate(prefix + future):
            result = adx_with_future.update(bar)
            if i == 3:
                captured_at_bar_3 = result

        assert captured_at_bar_3 == value_at_prefix_end

    def test_rolling_window_drops_oldest_dx_value(self):
        """A 5th bar (period=2) rolls the DX window -- the ADX after it
        must reflect only the two most recent DX values, not all three
        ever computed.
        """
        adx = AverageDirectionalIndex(period=2)
        adx.update(_kline(0, "10", "8", "9"))
        adx.update(_kline(1, "12", "9", "11"))
        adx.update(_kline(2, "11", "9", "10"))
        first_adx = adx.update(_kline(3, "13", "10", "12"))
        assert first_adx == Decimal("100")

        # bar4=(9,7,8): up_move=9-13=-4, down_move=10-7=3
        #   +DM = 0 (-4 not > 0); -DM = 3 (3 > -4 and > 0)
        #   TR = max(9-7, |9-12|, |7-12|) = max(2,3,5) = 5
        #   TR/DM window rolls: TR=[3,5] sum=8, +DM=[2,0] sum=2, -DM=[0,3] sum=3
        #   +DI = 100*2/8 = 25, -DI = 100*3/8 = 37.5
        #   DX = 100*|25-37.5|/(25+37.5) = 100*12.5/62.5 = 20
        #   DX window rolls: [100,20] -> ADX = mean([100,20]) = 60
        second_adx = adx.update(_kline(4, "9", "7", "8"))
        assert second_adx == Decimal("60")
        assert second_adx != first_adx


# ---------------------------------------------------------------------------
# compute_regime_weight
# ---------------------------------------------------------------------------


class TestComputeRegimeWeight:
    def test_at_or_below_low_threshold_is_zero_weight(self):
        assert compute_regime_weight(Decimal("20"), low=Decimal("20"), high=Decimal("25")) == Decimal(0)
        assert compute_regime_weight(Decimal("5"), low=Decimal("20"), high=Decimal("25")) == Decimal(0)
        assert compute_regime_weight(Decimal("0"), low=Decimal("20"), high=Decimal("25")) == Decimal(0)

    def test_at_or_above_high_threshold_is_full_weight(self):
        assert compute_regime_weight(Decimal("25"), low=Decimal("20"), high=Decimal("25")) == Decimal(1)
        assert compute_regime_weight(Decimal("60"), low=Decimal("20"), high=Decimal("25")) == Decimal(1)
        assert compute_regime_weight(Decimal("100"), low=Decimal("20"), high=Decimal("25")) == Decimal(1)

    def test_linear_ramp_between_thresholds(self):
        # Midpoint of [20, 25] is 22.5 -> weight 0.5.
        assert compute_regime_weight(Decimal("22.5"), low=Decimal("20"), high=Decimal("25")) == Decimal("0.5")
        # A quarter of the way from low to high (21.25) -> weight 0.25.
        assert compute_regime_weight(Decimal("21.25"), low=Decimal("20"), high=Decimal("25")) == Decimal("0.25")

    def test_default_thresholds_are_20_and_25(self):
        # Conventional ADX interpretation: <20 ranging, >25 trending.
        assert compute_regime_weight(Decimal("19.99")) == Decimal(0)
        assert compute_regime_weight(Decimal("25.01")) == Decimal(1)

    def test_none_adx_is_zero_weight(self):
        # "No ADX reading yet" (warmup) is baked in as zero conviction --
        # a legitimate, sensible value on this continuous scale, unlike
        # volatility_targeting.compute_vol_scalar's None-in/None-out
        # contract (see that module's docstring for why the two differ).
        assert compute_regime_weight(None) == Decimal(0)

    def test_rejects_low_greater_than_or_equal_to_high(self):
        with pytest.raises(ValueError, match="low"):
            compute_regime_weight(Decimal("22"), low=Decimal("25"), high=Decimal("25"))
        with pytest.raises(ValueError, match="low"):
            compute_regime_weight(Decimal("22"), low=Decimal("30"), high=Decimal("25"))
