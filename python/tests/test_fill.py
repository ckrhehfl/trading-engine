from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from backtest.fill import simulate_fill
from backtest.kline import Kline
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _kline(index: int, open_: str, high: str, low: str, close: str) -> Kline:
    return Kline(
        open_time=BASE_TIME + timedelta(minutes=15 * index),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
    )


def _guarded_market_intent(side: Side, quantity: str, at: datetime) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        symbol="BTC-USDT",
        side=side,
        order_type=OrderType.GUARDED_MARKET,
        quantity=Decimal(quantity),
        limit_price=None,
        signal_timeframe="15m",
        created_at=at,
    )


def _limit_intent(side: Side, quantity: str, limit_price: str, at: datetime) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        symbol="BTC-USDT",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        signal_timeframe="15m",
        created_at=at,
    )


def test_guarded_market_fills_at_next_bar_open_not_current_close():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),  # signal bar; close = 100.5
        _kline(1, "102", "103", "101", "102.5"),  # next bar; open = 102
    ]
    intent = _guarded_market_intent(Side.LONG, "1", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is not None
    assert fill.fill_price == Decimal("102")  # next bar's open, not 100.5
    assert fill.fill_time == klines[1].open_time


def test_no_fill_when_signal_is_on_last_bar():
    klines = [_kline(0, "100", "101", "99", "100.5")]
    intent = _guarded_market_intent(Side.LONG, "1", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is None


def test_limit_order_fills_at_limit_price_when_next_bar_touches_it():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "102", "103", "97", "102.5"),  # low=97, touches limit=98
    ]
    intent = _limit_intent(Side.LONG, "1", "98", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is not None
    assert fill.fill_price == Decimal("98")


def test_limit_order_fill_price_ignores_slippage_never_worse_than_limit():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "102", "103", "97", "102.5"),  # low=97, touches limit=98
    ]
    intent = _limit_intent(Side.LONG, "1", "98", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("50"))

    # A LIMIT order fills at the limit price or not at all — never worse,
    # regardless of the configured slippage.
    assert fill.fill_price == Decimal("98")


def test_limit_order_does_not_fill_when_next_bar_never_touches_it():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "102", "103", "101", "102.5"),  # low=101, never reaches limit=98
    ]
    intent = _limit_intent(Side.LONG, "1", "98", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is None


def test_long_limit_fills_at_open_when_bar_gaps_entirely_below_limit():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "96.5", "97", "96", "96.8"),  # entire bar below limit=98 — favorable gap
    ]
    intent = _limit_intent(Side.LONG, "1", "98", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is not None
    assert fill.fill_price == Decimal("96.5")  # the bar's open, not the unreached limit


def test_short_limit_fills_at_open_when_bar_gaps_entirely_above_limit():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "103.5", "104", "103", "103.8"),  # entire bar above limit=102 — favorable gap
    ]
    intent = _limit_intent(Side.SHORT, "1", "102", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is not None
    assert fill.fill_price == Decimal("103.5")


def test_short_limit_fills_at_limit_price_when_next_bar_touches_it():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "101", "103", "100", "101.5"),  # high=103, touches limit=102
    ]
    intent = _limit_intent(Side.SHORT, "1", "102", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is not None
    assert fill.fill_price == Decimal("102")


def test_short_limit_does_not_fill_when_next_bar_never_touches_it():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "99", "100", "98", "99.5"),  # high=100, never reaches limit=102
    ]
    intent = _limit_intent(Side.SHORT, "1", "102", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert fill is None


def test_fee_and_notional_are_computed_from_fill_price():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "1000", "1000", "1000", "1000"),
    ]
    intent = _guarded_market_intent(Side.LONG, "2", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("10"), slippage_bps=Decimal("0"))

    assert fill.notional == Decimal("2000")  # 2 * 1000
    assert fill.fee == Decimal("2.000")  # 2000 * 10bps = 2000 * 0.001


def test_slippage_makes_long_fill_price_worse_higher():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "1000", "1000", "1000", "1000"),
    ]
    intent = _guarded_market_intent(Side.LONG, "1", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("50"))

    assert fill.fill_price == Decimal("1005.0")  # 1000 * 1.005


def test_slippage_makes_short_fill_price_worse_lower():
    klines = [
        _kline(0, "100", "101", "99", "100.5"),
        _kline(1, "1000", "1000", "1000", "1000"),
    ]
    intent = _guarded_market_intent(Side.SHORT, "1", klines[0].open_time)

    fill = simulate_fill(intent, klines, signal_bar_index=0, fee_bps=Decimal("0"), slippage_bps=Decimal("50"))

    assert fill.fill_price == Decimal("995.0")  # 1000 * 0.995
