"""Tests for `python/research/strategies/risk_management.py` -- shared
ATR / stop-target / position-sizing primitives introduced in Strategy
Research Task F (see `.planning/sr-f-risk-management-and-1h-variant.md`).

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/risk_management.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from research.strategies.risk_management import (
    AverageTrueRange,
    OpenPosition,
    check_exit_trigger,
    compute_position_size,
    compute_stop_and_target,
)
from schemas.order_intent import Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _kline(i: int, o: str, h: str, lo: str, c: str, v: str = "1") -> Kline:
    return Kline(
        open_time=BASE_TIME + timedelta(minutes=15 * i),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal(v),
    )


# ---------------------------------------------------------------------------
# AverageTrueRange
# ---------------------------------------------------------------------------


class TestAverageTrueRange:
    def test_rejects_non_positive_period(self):
        with pytest.raises(ValueError, match="period"):
            AverageTrueRange(period=0)
        with pytest.raises(ValueError, match="period"):
            AverageTrueRange(period=-1)

    def test_returns_none_during_warmup(self):
        atr = AverageTrueRange(period=3)
        assert atr.update(_kline(0, "100", "102", "98", "100")) is None
        assert atr.update(_kline(1, "100", "103", "99", "101")) is None

    def test_first_bar_true_range_is_high_minus_low(self):
        # No previous close yet -- True Range degenerates to high - low.
        atr = AverageTrueRange(period=1)
        result = atr.update(_kline(0, "100", "105", "95", "102"))
        assert result == Decimal("10")  # 105 - 95, exactly one bar -> SMA(1) == itself

    def test_hand_computed_three_bar_average(self):
        # Bar 0: prev_close=None -> TR0 = high-low = 105-95 = 10. close=100.
        # Bar 1: TR1 = max(high-low, |high-prevclose|, |low-prevclose|)
        #            = max(103-97, |103-100|, |97-100|) = max(6,3,3) = 6. close=101.
        # Bar 2: TR2 = max(108-104, |108-101|, |104-101|) = max(4,7,3) = 7. close=106.
        # ATR(3) after bar 2 = (10+6+7)/3 = 23/3
        atr = AverageTrueRange(period=3)
        assert atr.update(_kline(0, "100", "105", "95", "100")) is None
        assert atr.update(_kline(1, "100", "103", "97", "101")) is None
        result = atr.update(_kline(2, "101", "108", "104", "106"))
        assert result == Decimal("23") / Decimal("3")

    def test_rolling_window_drops_oldest_true_range(self):
        atr = AverageTrueRange(period=2)
        atr.update(_kline(0, "100", "105", "95", "100"))  # TR=10
        first_two = atr.update(_kline(1, "100", "103", "97", "101"))  # TR=6 -> (10+6)/2=8
        assert first_two == Decimal("8")
        # Bar 2: TR = max(112-108, |112-101|, |108-101|) = max(4,11,7)=11
        # Window now [6, 11] (bar 0's TR=10 dropped) -> (6+11)/2 = 8.5
        result = atr.update(_kline(2, "108", "112", "108", "110"))
        assert result == Decimal("17") / Decimal("2")

    def test_lookahead_safety_value_at_bar_k_unaffected_by_future_bars(self):
        """Feeding the same prefix of bars must produce the same ATR
        value regardless of what (if anything) is fed afterward -- the
        concrete expression of "only ever uses bars up to and including
        now" for this incremental, non-window-based class.
        """
        prefix = [
            _kline(0, "100", "105", "95", "100"),
            _kline(1, "100", "103", "97", "101"),
            _kline(2, "101", "108", "104", "106"),
        ]
        future = [
            _kline(3, "106", "150", "50", "90"),  # deliberately extreme
            _kline(4, "90", "95", "85", "92"),
        ]

        atr_prefix_only = AverageTrueRange(period=3)
        value_at_prefix_end = None
        for bar in prefix:
            value_at_prefix_end = atr_prefix_only.update(bar)

        atr_with_future = AverageTrueRange(period=3)
        captured_at_bar_2 = None
        for i, bar in enumerate(prefix + future):
            result = atr_with_future.update(bar)
            if i == 2:
                captured_at_bar_2 = result

        assert captured_at_bar_2 == value_at_prefix_end


# ---------------------------------------------------------------------------
# compute_stop_and_target
# ---------------------------------------------------------------------------


class TestComputeStopAndTarget:
    def test_long_stop_below_target_above_entry(self):
        stop, target = compute_stop_and_target(
            entry_price=Decimal("100"),
            atr=Decimal("2"),
            side=Side.LONG,
            stop_multiplier=Decimal("1.5"),
            target_multiplier=Decimal("3"),
        )
        assert stop == Decimal("97")  # 100 - 2*1.5
        assert target == Decimal("106")  # 100 + 2*3

    def test_short_stop_above_target_below_entry(self):
        stop, target = compute_stop_and_target(
            entry_price=Decimal("100"),
            atr=Decimal("2"),
            side=Side.SHORT,
            stop_multiplier=Decimal("1.5"),
            target_multiplier=Decimal("3"),
        )
        assert stop == Decimal("103")  # 100 + 2*1.5
        assert target == Decimal("94")  # 100 - 2*3

    def test_rejects_non_positive_atr(self):
        with pytest.raises(ValueError, match="atr"):
            compute_stop_and_target(entry_price=Decimal("100"), atr=Decimal("0"), side=Side.LONG)
        with pytest.raises(ValueError, match="atr"):
            compute_stop_and_target(entry_price=Decimal("100"), atr=Decimal("-1"), side=Side.LONG)

    def test_default_multipliers_give_a_one_to_two_risk_reward_ratio(self):
        stop, target = compute_stop_and_target(entry_price=Decimal("100"), atr=Decimal("4"), side=Side.LONG)
        stop_distance = Decimal("100") - stop
        target_distance = target - Decimal("100")
        assert target_distance == stop_distance * 2


# ---------------------------------------------------------------------------
# compute_position_size
# ---------------------------------------------------------------------------


class TestComputePositionSize:
    def test_sizes_so_stop_loss_equals_risk_fraction_of_reference_equity(self):
        # stop distance = 3, risking 1% of 10,000 = 100 -> quantity = 100/3
        quantity = compute_position_size(
            entry_price=Decimal("100"),
            stop_price=Decimal("97"),
            reference_equity=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
        )
        assert quantity == Decimal("100") / Decimal("3")

    def test_works_symmetrically_for_a_short_stop_above_entry(self):
        quantity = compute_position_size(
            entry_price=Decimal("100"),
            stop_price=Decimal("103"),
            reference_equity=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
        )
        assert quantity == Decimal("100") / Decimal("3")

    def test_returns_none_for_zero_stop_distance(self):
        assert (
            compute_position_size(
                entry_price=Decimal("100"),
                stop_price=Decimal("100"),
                reference_equity=Decimal("10000"),
                risk_fraction=Decimal("0.01"),
            )
            is None
        )

    def test_default_reference_equity_and_risk_fraction_are_used_when_omitted(self):
        quantity_defaults = compute_position_size(entry_price=Decimal("100"), stop_price=Decimal("90"))
        quantity_explicit = compute_position_size(
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            reference_equity=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
        )
        assert quantity_defaults == quantity_explicit


# ---------------------------------------------------------------------------
# check_exit_trigger
# ---------------------------------------------------------------------------


def _position(side: Side, stop: str, target: str) -> OpenPosition:
    return OpenPosition(
        side=side,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
    )


class TestCheckExitTrigger:
    def test_long_stop_hit_when_low_touches_or_crosses_stop(self):
        position = _position(Side.LONG, stop="97", target="106")
        bar = _kline(0, "99", "99", "97", "98")  # low == stop exactly
        assert check_exit_trigger(position, bar) == "stop"

    def test_long_target_hit_when_high_touches_or_crosses_target(self):
        position = _position(Side.LONG, stop="97", target="106")
        bar = _kline(0, "104", "106", "103", "105")  # high == target exactly
        assert check_exit_trigger(position, bar) == "target"

    def test_long_neither_hit_returns_none(self):
        position = _position(Side.LONG, stop="97", target="106")
        bar = _kline(0, "100", "101", "99", "100")
        assert check_exit_trigger(position, bar) is None

    def test_short_stop_hit_when_high_touches_or_crosses_stop(self):
        position = _position(Side.SHORT, stop="103", target="94")
        bar = _kline(0, "101", "103", "100", "102")
        assert check_exit_trigger(position, bar) == "stop"

    def test_short_target_hit_when_low_touches_or_crosses_target(self):
        position = _position(Side.SHORT, stop="103", target="94")
        bar = _kline(0, "96", "97", "94", "95")
        assert check_exit_trigger(position, bar) == "target"

    def test_both_hit_in_same_bar_resolves_to_stop_conservatively(self):
        position = _position(Side.LONG, stop="97", target="106")
        bar = _kline(0, "100", "110", "90", "95")  # wide bar crosses both
        assert check_exit_trigger(position, bar) == "stop"

        short_position = _position(Side.SHORT, stop="103", target="94")
        bar2 = _kline(0, "100", "110", "90", "95")
        assert check_exit_trigger(short_position, bar2) == "stop"
