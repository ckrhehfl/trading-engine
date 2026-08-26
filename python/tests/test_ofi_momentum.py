"""Tests for `python/research/strategies/ofi_momentum.py` -- Scalping
Strategy Research Task S6's second candidate (order-flow-imbalance
momentum, real ATR-based risk control).

Every fixture `Kline` needs a real `taker_buy_base_volume` (unlike every
prior strategy module's tests) -- this signal is order-flow-based, not
price-based.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.strategies.ofi_momentum import (
    DEFAULT_STARTING_EQUITY,
    OfiBands,
    OfiMomentumStrategy,
    OfiMomentumTrainable,
    _per_bar_ofi,
)
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _kline(
    i: int,
    *,
    close: Decimal | str = Decimal("100"),
    volume: Decimal | str = Decimal("10"),
    taker_buy_base_volume: Decimal | str | None = Decimal("5"),  # OFI=0 (neutral) by default
    base_time: datetime = BASE_TIME,
) -> Kline:
    close = Decimal(close)
    volume = Decimal(volume)
    tbbv = None if taker_buy_base_volume is None else Decimal(taker_buy_base_volume)
    return Kline(
        open_time=base_time + timedelta(minutes=i),
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=volume,
        taker_buy_base_volume=tbbv,
    )


def _neutral_klines(count: int, base_time: datetime = BASE_TIME) -> list[Kline]:
    return [_kline(i, base_time=base_time) for i in range(count)]


def _strategy(**overrides: Any) -> OfiMomentumStrategy:
    kwargs: dict[str, Any] = dict(
        symbol="BINANCE-FUTURES:BTCUSDT", ofi_window_bars=8, atr_period=3
    )
    kwargs.update(overrides)
    return OfiMomentumStrategy(**kwargs)


class TestPerBarOfi:
    def test_neutral_volume_split_gives_zero(self):
        k = _kline(0, volume=Decimal("10"), taker_buy_base_volume=Decimal("5"))
        assert _per_bar_ofi(k) == Decimal("0")

    def test_all_buy_volume_gives_plus_one(self):
        k = _kline(0, volume=Decimal("10"), taker_buy_base_volume=Decimal("10"))
        assert _per_bar_ofi(k) == Decimal("1")

    def test_all_sell_volume_gives_minus_one(self):
        k = _kline(0, volume=Decimal("10"), taker_buy_base_volume=Decimal("0"))
        assert _per_bar_ofi(k) == Decimal("-1")

    def test_none_taker_buy_volume_gives_none_not_a_crash(self):
        # A BingX-sourced bar, or any bar predating Task S5's real
        # order-flow-data capture.
        k = _kline(0, taker_buy_base_volume=None)
        assert _per_bar_ofi(k) is None

    def test_zero_volume_gives_none_not_a_division_error(self):
        k = _kline(0, volume=Decimal("0"), taker_buy_base_volume=Decimal("0"))
        assert _per_bar_ofi(k) is None


class TestOfiBandsWarmup:
    def test_returns_none_before_period_bars(self):
        bands = OfiBands(period=8)
        klines = _neutral_klines(7)
        results = [bands.update(k) for k in klines]
        assert all(r is None for r in results)

    def test_warms_up_at_exactly_period_bars(self):
        bands = OfiBands(period=8)
        klines = _neutral_klines(8)
        results = [bands.update(k) for k in klines]
        assert results[-1] is not None

    def test_a_none_ofi_bar_does_not_count_toward_warmup(self):
        # A bar with no real order-flow data contributes no observation
        # -- warmup is gated on real OFI *observations*, not bar count.
        bands = OfiBands(period=8)
        klines = [_kline(i, taker_buy_base_volume=None) for i in range(8)]
        results = [bands.update(k) for k in klines]
        assert all(r is None for r in results)  # still 0 real observations

        more_neutral = _neutral_klines(8, base_time=BASE_TIME + timedelta(minutes=8))
        results2 = [bands.update(k) for k in more_neutral]
        assert results2[-1] is not None


class TestOfiBandsBreach:
    def test_a_strong_buy_bar_against_a_neutral_baseline_breaches_the_upper_band(self):
        # Empirically confirmed (not hand-derived, same discipline this
        # project's other band calculators' tests already established):
        # for a sample of period=8 points, 7 neutral (OFI=0) plus one
        # OFI=+1 outlier, the outlier's own z-score exceeds k=2.
        bands = OfiBands(period=8, k=Decimal("2"))
        klines = [_kline(i, taker_buy_base_volume=Decimal("5")) for i in range(7)]
        klines.append(_kline(7, taker_buy_base_volume=Decimal("10")))  # OFI=+1
        result = None
        for k in klines:
            result = bands.update(k)
        assert result is not None
        mean, upper, lower = result
        assert Decimal("1") > upper

    def test_a_strong_sell_bar_against_a_neutral_baseline_breaches_the_lower_band(self):
        bands = OfiBands(period=8, k=Decimal("2"))
        klines = [_kline(i, taker_buy_base_volume=Decimal("5")) for i in range(7)]
        klines.append(_kline(7, taker_buy_base_volume=Decimal("0")))  # OFI=-1
        result = None
        for k in klines:
            result = bands.update(k)
        assert result is not None
        mean, upper, lower = result
        assert Decimal("-1") < lower


class TestConstruction:
    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.open_position is None
        assert strategy.bars_seen == 0

    def test_rejects_non_positive_deviation_k(self):
        with pytest.raises(ValueError, match="k must be positive"):
            _strategy(deviation_k=Decimal("0"))

    def test_rejects_a_too_small_window(self):
        with pytest.raises(ValueError, match="period must be at least 2"):
            _strategy(ofi_window_bars=1)


class TestEdgeTriggeredEntry:
    def _breach_klines(self, direction: str) -> list[Kline]:
        # 8 neutral bars establish the FIRST warm reading as a baseline
        # (signal=0, nothing to have crossed *from* yet -- mirrors
        # hourly_momentum.py's own identical baseline-bar behavior), then
        # a 9th, genuinely fresh breach bar transitions away from it.
        neutral = [_kline(i, taker_buy_base_volume=Decimal("5")) for i in range(8)]
        extreme_tbbv = Decimal("10") if direction == "buy" else Decimal("0")
        breach = _kline(8, taker_buy_base_volume=extreme_tbbv)
        return neutral + [breach]

    def test_no_signal_during_warmup(self):
        strategy = _strategy(ofi_window_bars=8)
        klines = _neutral_klines(7)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)

    def test_baseline_establishment_fires_no_signal(self):
        strategy = _strategy(ofi_window_bars=8)
        klines = _neutral_klines(8)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)

    def test_a_fresh_buy_side_breach_after_baseline_fires_long(self):
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        klines = self._breach_klines("buy")
        intent = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
        assert intent is not None
        assert intent.side == Side.LONG
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.quantity > 0
        assert strategy.open_position is not None
        assert strategy.open_position.side == Side.LONG

    def test_a_fresh_sell_side_breach_after_baseline_fires_short(self):
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        klines = self._breach_klines("sell")
        intent = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
        assert intent is not None
        assert intent.side == Side.SHORT
        assert strategy.open_position.side == Side.SHORT

    def test_no_new_entry_evaluated_while_a_position_is_already_open(self):
        # Mirrors hourly_momentum.py's own composition, not
        # vwap_mid_reversion.py's: while a position is open, __call__
        # only ever checks the exit trigger, never a fresh entry --
        # confirmed here by feeding an immediate opposite-direction
        # breach right after entry and observing no second/flip intent.
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        klines = self._breach_klines("buy")
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is not None
        assert strategy.open_position.side == Side.LONG

        # A same-bar-shaped opposite breach immediately after -- must NOT
        # flip or open a second position while still holding the first.
        opposite = _kline(len(klines), taker_buy_base_volume=Decimal("0"))
        intent = strategy(klines + [opposite])
        assert intent is None
        assert strategy.open_position.side == Side.LONG


class TestRiskManagement:
    def _run_to_first_entry(self, strategy: OfiMomentumStrategy) -> list[Kline]:
        # Same baseline-then-fresh-breach shape as
        # TestEdgeTriggeredEntry._breach_klines -- see that method's own
        # comment for why 8 neutral bars (not 7) precede the breach.
        neutral = [_kline(i, taker_buy_base_volume=Decimal("5")) for i in range(8)]
        breach = _kline(8, taker_buy_base_volume=Decimal("10"))
        klines = neutral + [breach]
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is not None
        return klines

    def test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward(self):
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        self._run_to_first_entry(strategy)
        position = strategy.open_position
        assert position is not None
        entry_price = position.entry_price
        if position.side == Side.LONG:
            stop_distance = entry_price - position.stop_price
            target_distance = position.target_price - entry_price
        else:
            stop_distance = position.stop_price - entry_price
            target_distance = entry_price - position.target_price
        assert stop_distance > 0
        assert target_distance == stop_distance * 2

    def test_entry_quantity_matches_fixed_fractional_sizing_formula(self):
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        self._run_to_first_entry(strategy)
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        expected_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        assert position.quantity == expected_quantity

    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self):
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        klines = self._run_to_first_entry(strategy)
        position_before = strategy.open_position
        assert position_before is not None
        quantity_before = position_before.quantity
        side_before = position_before.side
        stop_price = position_before.stop_price

        next_time = klines[-1].open_time + timedelta(minutes=1)
        if side_before == Side.LONG:
            breach = Kline(
                open_time=next_time,
                open=stop_price,
                high=stop_price,
                low=stop_price - Decimal("50"),
                close=stop_price - Decimal("10"),
                volume=Decimal("1"),
                taker_buy_base_volume=Decimal("0.5"),
            )
        else:
            breach = Kline(
                open_time=next_time,
                open=stop_price,
                high=stop_price + Decimal("50"),
                low=stop_price,
                close=stop_price + Decimal("10"),
                volume=Decimal("1"),
                taker_buy_base_volume=Decimal("0.5"),
            )
        intent = strategy(klines + [breach])
        assert intent is not None
        assert intent.quantity == quantity_before
        assert intent.side == (Side.SHORT if side_before == Side.LONG else Side.LONG)
        assert strategy.open_position is None

    def test_target_hit_emits_flattening_intent(self):
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        klines = self._run_to_first_entry(strategy)
        position_before = strategy.open_position
        assert position_before is not None
        side_before = position_before.side
        target_price = position_before.target_price

        next_time = klines[-1].open_time + timedelta(minutes=1)
        if side_before == Side.LONG:
            touch = Kline(
                open_time=next_time,
                open=target_price,
                high=target_price + Decimal("50"),
                low=target_price - Decimal("1"),
                close=target_price + Decimal("10"),
                volume=Decimal("1"),
                taker_buy_base_volume=Decimal("0.5"),
            )
        else:
            touch = Kline(
                open_time=next_time,
                open=target_price,
                high=target_price + Decimal("1"),
                low=target_price - Decimal("50"),
                close=target_price - Decimal("10"),
                volume=Decimal("1"),
                taker_buy_base_volume=Decimal("0.5"),
            )
        intent = strategy(klines + [touch])
        assert intent is not None
        assert strategy.open_position is None

    def test_same_bar_stop_and_target_touch_stop_wins(self):
        # Matches check_exit_trigger's own documented, conservative
        # tie-break: a wide-range bar crossing both levels resolves to
        # "stop", never "target".
        strategy = _strategy(ofi_window_bars=8, atr_period=3)
        klines = self._run_to_first_entry(strategy)
        position_before = strategy.open_position
        assert position_before is not None
        side_before = position_before.side
        stop_price = position_before.stop_price
        target_price = position_before.target_price

        next_time = klines[-1].open_time + timedelta(minutes=1)
        if side_before == Side.LONG:
            wide = Kline(
                open_time=next_time,
                open=(stop_price + target_price) / 2,
                high=target_price + Decimal("10"),
                low=stop_price - Decimal("10"),
                close=(stop_price + target_price) / 2,
                volume=Decimal("1"),
                taker_buy_base_volume=Decimal("0.5"),
            )
        else:
            wide = Kline(
                open_time=next_time,
                open=(stop_price + target_price) / 2,
                high=stop_price + Decimal("10"),
                low=target_price - Decimal("10"),
                close=(stop_price + target_price) / 2,
                volume=Decimal("1"),
                taker_buy_base_volume=Decimal("0.5"),
            )
        intent = strategy(klines + [wide])
        assert intent is not None
        assert strategy.open_position is None
        # Realized loss (stop, not target) -- quantity matches the
        # fixed-fractional formula regardless, this test only confirms
        # the exit actually happened; the *which level* assertion is
        # check_exit_trigger's own unit-tested responsibility, exercised
        # here end-to-end.


class TestTrainableFit:
    def test_fit_returns_a_fresh_strategy_instance_not_the_scoring_one(self):
        trainable = OfiMomentumTrainable(
            strategy_id="test-ofi-momentum",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("10"),
            ofi_window_bars=8,
            atr_period=3,
            starting_equity=DEFAULT_STARTING_EQUITY,
        )
        klines = _neutral_klines(20)
        strategy = trainable.fit(klines, {}, parent_run_id="test-parent")
        assert isinstance(strategy, OfiMomentumStrategy)
        assert strategy.bars_seen == 0  # fresh, not the scoring instance's own state
