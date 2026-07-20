from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from backtest.engine import run_backtest
from backtest.kline import Kline
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _klines(count: int) -> list[Kline]:
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=15 * i),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i) + Decimal("0.5"),
            volume=Decimal("100"),
        )
        for i in range(count)
    ]


def test_strategy_only_ever_sees_bars_up_to_and_including_the_current_one():
    klines = _klines(5)
    seen_lengths = []
    seen_last_bars = []

    def spy_strategy(visible_klines):
        seen_lengths.append(len(visible_klines))
        seen_last_bars.append(visible_klines[-1])
        return None

    run_backtest(klines, spy_strategy, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert seen_lengths == [1, 2, 3, 4, 5]
    assert seen_last_bars == klines


def test_signal_produces_a_fill_recorded_in_the_result():
    klines = _klines(3)

    def buy_on_first_bar_only(visible_klines):
        if len(visible_klines) != 1:
            return None
        return OrderIntent(
            intent_id=uuid4(),
            symbol="BTC-USDT",
            side=Side.LONG,
            order_type=OrderType.GUARDED_MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            signal_timeframe="15m",
            created_at=visible_klines[-1].open_time,
        )

    result = run_backtest(klines, buy_on_first_bar_only, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert len(result.fills) == 1
    assert result.fills[0].fill_price == klines[1].open


def test_signal_on_last_bar_produces_no_fill():
    klines = _klines(3)

    def buy_on_last_bar_only(visible_klines):
        if len(visible_klines) != len(klines):
            return None
        return OrderIntent(
            intent_id=uuid4(),
            symbol="BTC-USDT",
            side=Side.LONG,
            order_type=OrderType.GUARDED_MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            signal_timeframe="15m",
            created_at=visible_klines[-1].open_time,
        )

    result = run_backtest(klines, buy_on_last_bar_only, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert result.fills == []


def test_same_inputs_produce_identical_results_every_run():
    klines = _klines(10)

    def buy_every_third_bar(visible_klines):
        i = len(visible_klines) - 1
        if i % 3 != 0:
            return None
        return OrderIntent(
            intent_id=uuid4(),
            symbol="BTC-USDT",
            side=Side.LONG if i % 2 == 0 else Side.SHORT,
            order_type=OrderType.GUARDED_MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            signal_timeframe="15m",
            created_at=visible_klines[-1].open_time,
        )

    result_a = run_backtest(klines, buy_every_third_bar, fee_bps=Decimal("5"), slippage_bps=Decimal("2"))
    result_b = run_backtest(klines, buy_every_third_bar, fee_bps=Decimal("5"), slippage_bps=Decimal("2"))

    fills_a = [(f.fill_time, f.fill_price, f.fee, f.notional) for f in result_a.fills]
    fills_b = [(f.fill_time, f.fill_price, f.fee, f.notional) for f in result_b.fills]
    assert fills_a == fills_b
    assert len(fills_a) > 0  # sanity check the scenario actually produced fills
