from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backtest.fill import Fill
from metrics.funding import FundingRate
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


def _funding(rate: str, at: datetime, mark_price: str = "100") -> FundingRate:
    return FundingRate(funding_time=at, funding_rate=Decimal(rate), mark_price=Decimal(mark_price))


# ---------------------------------------------------------------------------
# Funding P&L attribution -- Strategy Research Task M. Opt-in via
# PositionTracker(funding_rates=...) / reconstruct_trades(..., funding_rates=...).
# Sign convention verified against BingX's own official documentation (see
# .planning/sr-m-funding-rate-pipeline.md): fundingRate > 0 -> longs pay
# shorts; fundingRate < 0 -> shorts pay longs. Payment = position notional
# (using the funding row's own mark_price, matching how BingX actually
# computes it) x funding_rate.
# ---------------------------------------------------------------------------


def test_position_held_across_zero_funding_timestamps_gets_no_adjustment():
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "110", "1", BASE_TIME + timedelta(hours=1))
    # Funding timestamp well outside [entry, exit] -- must not apply.
    funding = [_funding("0.0001", BASE_TIME + timedelta(hours=5))]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert len(trades) == 1
    assert trades[0].funding_pnl == Decimal("0")
    assert trades[0].realized_pnl == Decimal("10")  # unchanged from the no-funding price P&L


def test_long_position_pays_when_funding_rate_is_positive():
    # A long position held through a single funding timestamp with a
    # positive rate must PAY -- i.e. funding_pnl is negative and
    # realized_pnl is reduced relative to price-only P&L. This is the
    # sign-error trap the task brief explicitly calls out.
    entry_intent = _intent(Side.LONG, "2", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "2", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "2", BASE_TIME + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "100", "2", BASE_TIME + timedelta(hours=1))
    funding_time = BASE_TIME + timedelta(minutes=30)
    funding = [_funding("0.0001", funding_time, mark_price="100")]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert len(trades) == 1
    # notional = qty(2) * mark_price(100) = 200; payment = -200 * 0.0001 = -0.02
    assert trades[0].funding_pnl == Decimal("-0.02")
    assert trades[0].realized_pnl == Decimal("-0.02")  # 0 price P&L (flat exit) + funding


def test_long_position_receives_when_funding_rate_is_negative():
    entry_intent = _intent(Side.LONG, "2", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "2", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "2", BASE_TIME + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "100", "2", BASE_TIME + timedelta(hours=1))
    funding_time = BASE_TIME + timedelta(minutes=30)
    funding = [_funding("-0.0001", funding_time, mark_price="100")]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert trades[0].funding_pnl == Decimal("0.02")  # a long RECEIVES when the rate is negative
    assert trades[0].realized_pnl == Decimal("0.02")


def test_short_position_receives_when_funding_rate_is_positive():
    entry_intent = _intent(Side.SHORT, "2", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "2", BASE_TIME)
    exit_intent = _intent(Side.LONG, "2", BASE_TIME + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "100", "2", BASE_TIME + timedelta(hours=1))
    funding_time = BASE_TIME + timedelta(minutes=30)
    funding = [_funding("0.0001", funding_time, mark_price="100")]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert trades[0].funding_pnl == Decimal("0.02")  # a short RECEIVES when the rate is positive
    assert trades[0].realized_pnl == Decimal("0.02")


def test_short_position_pays_when_funding_rate_is_negative():
    entry_intent = _intent(Side.SHORT, "2", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "2", BASE_TIME)
    exit_intent = _intent(Side.LONG, "2", BASE_TIME + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "100", "2", BASE_TIME + timedelta(hours=1))
    funding_time = BASE_TIME + timedelta(minutes=30)
    funding = [_funding("-0.0001", funding_time, mark_price="100")]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert trades[0].funding_pnl == Decimal("-0.02")  # a short PAYS when the rate is negative
    assert trades[0].realized_pnl == Decimal("-0.02")


def test_funding_pnl_sums_across_multiple_funding_timestamps_held_through():
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "1", BASE_TIME + timedelta(hours=25))
    exit_fill = _fill(exit_intent, "100", "1", BASE_TIME + timedelta(hours=25))
    funding = [
        _funding("0.0001", BASE_TIME + timedelta(hours=8), mark_price="100"),
        _funding("0.0002", BASE_TIME + timedelta(hours=16), mark_price="100"),
        _funding("-0.0001", BASE_TIME + timedelta(hours=24), mark_price="100"),
    ]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    # notional = 100 each time; payments = -0.01, -0.02, +0.01 => sum -0.02
    assert trades[0].funding_pnl == Decimal("-0.02")


def test_entering_exactly_at_a_funding_timestamp_does_not_charge_that_timestamp():
    # Judgment call (documented in .planning/sr-m-funding-rate-pipeline.md):
    # a position opened at the exact instant of a funding settlement was
    # not "held through" it -- the settlement snapshot is treated as
    # having already happened relative to this brand-new position.
    funding_time = BASE_TIME
    entry_intent = _intent(Side.LONG, "1", funding_time)
    entry_fill = _fill(entry_intent, "100", "1", funding_time)
    exit_intent = _intent(Side.SHORT, "1", funding_time + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "100", "1", funding_time + timedelta(hours=1))
    funding = [_funding("0.0001", funding_time, mark_price="100")]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert trades[0].funding_pnl == Decimal("0")


def test_exiting_exactly_at_a_funding_timestamp_does_charge_that_timestamp():
    # The mirror-image judgment call: a position closed at the exact
    # instant of a funding settlement WAS held through it (it was open
    # for the entire period up to and including that instant), so it is
    # charged/credited.
    funding_time = BASE_TIME + timedelta(hours=1)
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    exit_intent = _intent(Side.SHORT, "1", funding_time)
    exit_fill = _fill(exit_intent, "100", "1", funding_time)
    funding = [_funding("0.0001", funding_time, mark_price="100")]

    trades = reconstruct_trades([entry_intent, exit_intent], [entry_fill, exit_fill], funding_rates=funding)

    assert trades[0].funding_pnl == Decimal("-0.01")


def test_flat_period_between_two_trades_is_not_charged_funding():
    # A funding timestamp that passes while the tracker is flat (between
    # two separate lifecycles) must be skipped, not attributed to
    # whichever lifecycle happens to be reconstructed next.
    entry1 = _intent(Side.LONG, "1", BASE_TIME)
    fill1 = _fill(entry1, "100", "1", BASE_TIME)
    exit1 = _intent(Side.SHORT, "1", BASE_TIME + timedelta(hours=1))
    fill1_exit = _fill(exit1, "100", "1", BASE_TIME + timedelta(hours=1))
    # Flat here -- funding at hour 2 must be skipped entirely.
    entry2 = _intent(Side.LONG, "1", BASE_TIME + timedelta(hours=3))
    fill2 = _fill(entry2, "100", "1", BASE_TIME + timedelta(hours=3))
    exit2 = _intent(Side.SHORT, "1", BASE_TIME + timedelta(hours=4))
    fill2_exit = _fill(exit2, "100", "1", BASE_TIME + timedelta(hours=4))
    funding = [_funding("0.0001", BASE_TIME + timedelta(hours=2), mark_price="100")]

    trades = reconstruct_trades(
        [entry1, exit1, entry2, exit2],
        [fill1, fill1_exit, fill2, fill2_exit],
        funding_rates=funding,
    )

    assert len(trades) == 2
    assert trades[0].funding_pnl == Decimal("0")
    assert trades[1].funding_pnl == Decimal("0")


def test_position_tracker_funding_pnl_property_accumulates_across_the_trackers_lifetime():
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    funding = [_funding("0.0001", BASE_TIME + timedelta(hours=1), mark_price="100")]

    tracker = PositionTracker(funding_rates=funding)
    tracker.apply(entry_intent, entry_fill)
    applied = tracker.apply_funding_through(BASE_TIME + timedelta(hours=2))

    assert applied == Decimal("-0.01")
    assert tracker.funding_pnl == Decimal("-0.01")


def test_force_close_applies_funding_up_to_the_close_time():
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    funding = [_funding("0.0001", BASE_TIME + timedelta(hours=1), mark_price="100")]

    tracker = PositionTracker(funding_rates=funding)
    tracker.apply(entry_intent, entry_fill)
    closed = tracker.force_close(Decimal("100"), BASE_TIME + timedelta(hours=2))

    assert closed is not None
    assert closed.funding_pnl == Decimal("-0.01")
    assert closed.realized_pnl == Decimal("-0.01")  # flat price P&L (closed at entry price) + funding


def test_default_position_tracker_has_no_funding_awareness_and_is_unaffected():
    # No funding_rates argument at all -- the exact call shape every
    # pre-existing caller in this codebase already uses. Must behave
    # byte-for-byte as before this feature existed.
    tracker = PositionTracker()
    assert tracker.funding_pnl == Decimal("0")
    applied = tracker.apply_funding_through(BASE_TIME + timedelta(days=999))
    assert applied == Decimal("0")


def test_funding_rates_must_be_sorted_ascending_by_funding_time():
    unsorted = [
        _funding("0.0001", BASE_TIME + timedelta(hours=2)),
        _funding("0.0001", BASE_TIME + timedelta(hours=1)),
    ]
    with pytest.raises(ValueError, match="ascending"):
        PositionTracker(funding_rates=unsorted)


def test_scaling_position_uses_the_actually_held_quantity_at_each_funding_timestamp():
    # Enter 1, scale in to 3 total, THEN a funding timestamp passes --
    # the payment must be based on the size held at that instant (3), not
    # the lifecycle's eventual total-opened quantity computed some other
    # way, and not the size before scaling in (1).
    e1 = _intent(Side.LONG, "1", BASE_TIME)
    f1 = _fill(e1, "100", "1", BASE_TIME)
    e2 = _intent(Side.LONG, "2", BASE_TIME + timedelta(minutes=10))
    f2 = _fill(e2, "100", "2", BASE_TIME + timedelta(minutes=10))
    exit_intent = _intent(Side.SHORT, "3", BASE_TIME + timedelta(hours=1))
    exit_fill = _fill(exit_intent, "100", "3", BASE_TIME + timedelta(hours=1))
    funding = [_funding("0.0001", BASE_TIME + timedelta(minutes=30), mark_price="100")]

    trades = reconstruct_trades([e1, e2, exit_intent], [f1, f2, exit_fill], funding_rates=funding)

    # notional = 3 * 100 = 300; payment = -300 * 0.0001 = -0.03
    assert trades[0].funding_pnl == Decimal("-0.03")


def test_flip_in_a_single_fill_attributes_pre_flip_funding_to_the_closed_lifecycle_and_resets_for_the_new_one():
    # A flip (one fill both closes the existing lifecycle and opens a new,
    # opposite-side one -- see PositionTracker._reduce_or_close_or_flip)
    # is the regression risk CodeRabbit flagged for this task's PR:
    # _lifecycle_funding_pnl must land on the CLOSED (old) lifecycle's
    # ClosedTrade and reset to 0 for the freshly-opened one, not leak
    # across the flip in either direction.
    entry_intent = _intent(Side.LONG, "1", BASE_TIME)
    entry_fill = _fill(entry_intent, "100", "1", BASE_TIME)
    # Funding while LONG 1 is open, pre-flip -- must land on trade 1 only.
    pre_flip_funding_time = BASE_TIME + timedelta(minutes=30)
    flip_time = BASE_TIME + timedelta(hours=1)
    # A single SHORT 3 fill against a LONG 1 position: closes the LONG (1)
    # and opens a fresh SHORT 2 lifecycle at the same fill.
    flip_intent = _intent(Side.SHORT, "3", flip_time)
    flip_fill = _fill(flip_intent, "100", "3", flip_time)
    # Funding while SHORT 2 is open, post-flip -- must land on trade 2 only.
    post_flip_funding_time = flip_time + timedelta(minutes=30)
    exit_intent = _intent(Side.LONG, "2", BASE_TIME + timedelta(hours=2))
    exit_fill = _fill(exit_intent, "100", "2", BASE_TIME + timedelta(hours=2))
    funding = [
        _funding("0.0001", pre_flip_funding_time, mark_price="100"),
        _funding("0.0002", post_flip_funding_time, mark_price="100"),
    ]

    trades = reconstruct_trades(
        [entry_intent, flip_intent, exit_intent],
        [entry_fill, flip_fill, exit_fill],
        funding_rates=funding,
    )

    assert len(trades) == 2
    long_trade, short_trade = trades
    assert long_trade.side == Side.LONG
    assert short_trade.side == Side.SHORT
    # Trade 1 (closed by the flip): only the pre-flip funding, long pays
    # on a positive rate. notional = 1 * 100 = 100; payment = -100 * 0.0001 = -0.01
    assert long_trade.funding_pnl == Decimal("-0.01")
    # Trade 2 (opened by the flip's residual): only the post-flip funding,
    # starting from zero -- must NOT inherit trade 1's -0.01. Short
    # receives on a positive rate: notional = 2 * 100 = 200; payment =
    # -(-1) * 200 * 0.0002 = 0.04
    assert short_trade.funding_pnl == Decimal("0.04")


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
