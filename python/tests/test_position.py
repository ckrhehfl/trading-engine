from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backtest.fill import Fill
from metrics.position import ClosedTrade, PositionTracker, reconstruct_trades
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _intent(side: Side, quantity: str, at: datetime) -> OrderIntent:
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


def _fill(intent: OrderIntent, price: str, quantity: str, at: datetime, fee: str = "0") -> Fill:
    price_d = Decimal(price)
    qty_d = Decimal(quantity)
    return Fill(
        intent_id=intent.intent_id,
        fill_time=at,
        fill_price=price_d,
        quantity=qty_d,
        fee=Decimal(fee),
        notional=price_d * qty_d,
    )


def test_simple_long_trade_realizes_pnl_on_close():
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=15))
    exit_fill = _fill(exit_intent, "110", "1", BASE_TIME + timedelta(minutes=15))

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill])

    assert len(trades) == 1
    trade = trades[0]
    assert isinstance(trade, ClosedTrade)
    assert trade.side == Side.LONG
    assert trade.realized_pnl == Decimal("10")  # (110-100)*1
    assert trade.entry_price == Decimal("100")
    assert trade.exit_price == Decimal("110")
    assert trade.quantity == Decimal("1")
    assert trade.entry_time == BASE_TIME
    assert trade.exit_time == BASE_TIME + timedelta(minutes=15)


def test_simple_short_trade_profits_when_price_drops():
    entry_intent = _intent(Side.SHORT, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    exit_intent = _intent(Side.LONG, "1", BASE_TIME + timedelta(minutes=15))
    exit_fill = _fill(exit_intent, "90", "1", BASE_TIME + timedelta(minutes=15))

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill])

    assert len(trades) == 1
    assert trades[0].side == Side.SHORT
    assert trades[0].realized_pnl == Decimal("10")  # a price drop profits a short


def test_short_trade_loses_when_price_rises():
    entry_intent = _intent(Side.SHORT, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    exit_intent = _intent(Side.LONG, "1", BASE_TIME + timedelta(minutes=15))
    exit_fill = _fill(exit_intent, "110", "1", BASE_TIME + timedelta(minutes=15))

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill])

    assert trades[0].realized_pnl == Decimal("-10")


def test_scaling_in_updates_size_weighted_average_entry_price():
    i1 = _intent(Side.LONG, "1", BASE_TIME)
    f1 = _fill(i1, "100", "1", BASE_TIME)
    i2 = _intent(Side.LONG, "1", BASE_TIME + timedelta(minutes=15))
    f2 = _fill(i2, "110", "1", BASE_TIME + timedelta(minutes=15))
    i3 = _intent(Side.SHORT, "2", BASE_TIME + timedelta(minutes=30))
    f3 = _fill(i3, "120", "2", BASE_TIME + timedelta(minutes=30))

    trades = reconstruct_trades([i1, i2, i3], [f1, f2, f3])

    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_price == Decimal("105")  # (100*1 + 110*1) / 2
    assert trade.quantity == Decimal("2")
    assert trade.realized_pnl == Decimal("30")  # (120-105)*2


def test_scaling_out_in_pieces_aggregates_into_one_trade_not_one_per_fill():
    entry_intent = _intent(Side.LONG, "3", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "3", BASE_TIME)
    # Two losing partial exits, one large winning exit — net winning trade,
    # exactly the "scales out in three pieces" case named in the design.
    exit1_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=15))
    exit1_fill = _fill(exit1_intent, "90", "1", BASE_TIME + timedelta(minutes=15))  # -10
    exit2_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=30))
    exit2_fill = _fill(exit2_intent, "95", "1", BASE_TIME + timedelta(minutes=30))  # -5
    exit3_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=45))
    exit3_fill = _fill(exit3_intent, "150", "1", BASE_TIME + timedelta(minutes=45))  # +50

    trades = reconstruct_trades(
        [entry_intent, exit1_intent, exit2_intent, exit3_intent],
        [entry_fill, exit1_fill, exit2_fill, exit3_fill],
    )

    assert len(trades) == 1  # one trade, not three
    assert trades[0].realized_pnl == Decimal("35")  # -10 -5 +50
    assert trades[0].quantity == Decimal("3")
    assert trades[0].exit_price == Decimal("335") / Decimal("3")  # size-weighted avg exit


def test_flip_closes_existing_lifecycle_and_opens_a_new_one_in_the_new_direction():
    entry_intent = _intent(Side.LONG, "5", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "5", BASE_TIME)
    flip_intent = _intent(Side.SHORT, "8", BASE_TIME + timedelta(minutes=15))
    flip_fill = _fill(flip_intent, "110", "8", BASE_TIME + timedelta(minutes=15))

    tracker = PositionTracker()
    trades = [
        closed
        for intent, fill in [(entry_intent, entry_fill), (flip_intent, flip_fill)]
        if (closed := tracker.apply(intent, fill)) is not None
    ]

    assert len(trades) == 1
    assert trades[0].side == Side.LONG
    assert trades[0].quantity == Decimal("5")
    assert trades[0].realized_pnl == Decimal("50")  # (110-100)*5

    # Residual 3 opens a fresh short lifecycle at the flip fill's price,
    # still open (no second ClosedTrade emitted for it yet).
    assert tracker.position_qty == Decimal("-3")
    assert tracker.avg_entry_price == Decimal("110")
    assert tracker.realized_pnl == Decimal("50")


def test_exact_close_leaves_flat_position_with_no_open_lifecycle():
    entry_intent = _intent(Side.LONG, "2", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "2", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "2", BASE_TIME + timedelta(minutes=15))
    exit_fill = _fill(exit_intent, "105", "2", BASE_TIME + timedelta(minutes=15))

    tracker = PositionTracker()
    tracker.apply(entry_intent, entry_fill)
    closed = tracker.apply(exit_intent, exit_fill)

    assert closed is not None
    assert tracker.position_qty == Decimal("0")


def test_partial_reduction_does_not_emit_a_closed_trade():
    entry_intent = _intent(Side.LONG, "3", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "3", BASE_TIME)
    partial_exit_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=15))
    partial_exit_fill = _fill(partial_exit_intent, "110", "1", BASE_TIME + timedelta(minutes=15))

    tracker = PositionTracker()
    tracker.apply(entry_intent, entry_fill)
    closed = tracker.apply(partial_exit_intent, partial_exit_fill)

    assert closed is None
    assert tracker.position_qty == Decimal("2")
    assert tracker.avg_entry_price == Decimal("100")  # cost basis unchanged by a reduction
    assert tracker.realized_pnl == Decimal("10")  # (110-100)*1 already realized


def test_cumulative_fees_accumulate_across_all_fills_regardless_of_direction():
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME, fee="0.5")
    exit_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=15))
    exit_fill = _fill(exit_intent, "110", "1", BASE_TIME + timedelta(minutes=15), fee="0.6")

    tracker = PositionTracker()
    tracker.apply(entry_intent, entry_fill)
    tracker.apply(exit_intent, exit_fill)

    assert tracker.cumulative_fees == Decimal("1.1")


def test_force_close_realizes_pnl_and_emits_a_trade_for_a_still_open_position():
    entry_intent = _intent(Side.LONG, "2", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "2", BASE_TIME)

    tracker = PositionTracker()
    tracker.apply(entry_intent, entry_fill)
    closed = tracker.force_close(Decimal("120"), BASE_TIME + timedelta(minutes=15))

    assert closed is not None
    assert closed.realized_pnl == Decimal("40")  # (120-100)*2
    assert closed.exit_price == Decimal("120")
    assert tracker.position_qty == Decimal("0")
    assert tracker.realized_pnl == Decimal("40")


def test_force_close_on_a_flat_tracker_is_a_no_op():
    tracker = PositionTracker()

    assert tracker.force_close(Decimal("100"), BASE_TIME) is None
    assert tracker.position_qty == Decimal("0")


def test_reconstruct_trades_with_no_fills_returns_no_trades():
    assert reconstruct_trades([], []) == []


def test_reconstruct_trades_raises_on_mismatched_length_inputs_instead_of_silently_truncating():
    # filled_intents/fills must be index-aligned (same length) per the
    # BacktestResult contract — a mismatch is a caller bug that must be
    # loud (ValueError via zip(strict=True)), not silently truncated to
    # the shorter list's length, which would quietly corrupt trade
    # reconstruction instead of failing where the real bug is.
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    extra_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(minutes=15))

    with pytest.raises(ValueError):
        reconstruct_trades([entry_intent, extra_intent], [entry_fill])
