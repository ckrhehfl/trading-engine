import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import fmean, stdev
from uuid import uuid4

import pytest

from backtest.fill import Fill
from backtest.kline import Kline
from metrics.funding import FundingRate
from metrics.metrics import (
    Metrics,
    _max_drawdown,
    _profit_factor,
    _sharpe_ratio,
    _win_rate,
    build_equity_curve,
    compute_metrics,
)
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
STARTING_EQUITY = Decimal("10000")


def _kline(index: int, close: str) -> Kline:
    return Kline(
        open_time=BASE_TIME + timedelta(minutes=15 * index),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("100"),
    )


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


# --- Equity curve: the long/short unrealized-P&L sign trap -----------------


def test_equity_curve_unrealized_pnl_sign_for_a_long_position():
    klines = [_kline(0, "100"), _kline(1, "90"), _kline(2, "110")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)

    equity_curve, _ = build_equity_curve(klines, [entry_intent], [entry_fill], STARTING_EQUITY)

    # A price drop below entry must reduce equity for a long...
    assert equity_curve[1] == STARTING_EQUITY - Decimal("10")
    # ...and a price rise above entry must increase it.
    assert equity_curve[2] == STARTING_EQUITY + Decimal("10")


def test_equity_curve_unrealized_pnl_sign_for_a_short_position():
    klines = [_kline(0, "100"), _kline(1, "90"), _kline(2, "110")]
    entry_intent = _intent(Side.SHORT, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)

    equity_curve, _ = build_equity_curve(klines, [entry_intent], [entry_fill], STARTING_EQUITY)

    # position_qty is negative for a short: a price DROP below entry must
    # be a POSITIVE contribution to equity — the easy sign-error trap this
    # test exists to catch.
    assert equity_curve[1] == STARTING_EQUITY + Decimal("10")
    # ...and a price rise above entry must be a loss.
    assert equity_curve[2] == STARTING_EQUITY - Decimal("10")


# --- Equity curve: realization, fees, force-close ---------------------------


def test_equity_curve_reflects_realized_pnl_and_fees_after_a_close():
    klines = [_kline(0, "100"), _kline(1, "105"), _kline(2, "110")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time, fee="1")
    exit_intent = _intent(Side.SHORT, "1", klines[2].open_time)
    exit_fill = _fill(exit_intent, "110", "1", klines[2].open_time, fee="1")

    equity_curve, closed_trades = build_equity_curve(
        klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY
    )

    assert equity_curve[0] == STARTING_EQUITY - Decimal("1")  # fee only; fill price == bar0 close, so unrealized is 0
    assert equity_curve[2] == STARTING_EQUITY - Decimal("2") + Decimal("10")  # both fees + realized
    assert len(closed_trades) == 1
    assert closed_trades[0].realized_pnl == Decimal("10")


def test_still_open_position_is_force_closed_at_the_final_bars_close_price():
    klines = [_kline(0, "100"), _kline(1, "105"), _kline(2, "130")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)

    equity_curve, closed_trades = build_equity_curve(klines, [entry_intent], [entry_fill], STARTING_EQUITY)

    assert len(closed_trades) == 1
    trade = closed_trades[0]
    assert trade.exit_price == Decimal("130")
    assert trade.exit_time == klines[-1].open_time
    assert trade.realized_pnl == Decimal("30")
    # Force-closing at the same price already used to mark the final bar
    # to market must not change the final bar's equity value.
    assert equity_curve[-1] == STARTING_EQUITY + Decimal("30")


def test_a_fill_after_the_last_klines_open_time_raises_instead_of_silently_vanishing():
    # build_equity_curve's fill-consumption loop assumes every fill's
    # fill_time lines up with some kline's open_time (backtest/engine.py's
    # contract: fill_time is always next_bar.open_time for some bar in the
    # same klines list). If that contract is ever violated — a fill later
    # than the last kline — the loop would otherwise finish with fills
    # still unconsumed, silently dropping them from equity/trade metrics
    # instead of failing where the real bug is.
    klines = [_kline(0, "100"), _kline(1, "105")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    late_fill = _fill(entry_intent, "100", "1", klines[-1].open_time + timedelta(minutes=15))

    with pytest.raises(AssertionError):
        build_equity_curve(klines, [entry_intent], [late_fill], STARTING_EQUITY)


def test_flat_equity_curve_when_no_fills_at_all():
    klines = [_kline(0, "100"), _kline(1, "200"), _kline(2, "50")]

    equity_curve, closed_trades = build_equity_curve(klines, [], [], STARTING_EQUITY)

    assert equity_curve == [STARTING_EQUITY, STARTING_EQUITY, STARTING_EQUITY]
    assert closed_trades == []


# --- Sharpe ratio ------------------------------------------------------------


def test_sharpe_ratio_matches_manual_calculation_for_a_known_equity_curve():
    equity_curve = [Decimal("100"), Decimal("110"), Decimal("105"), Decimal("115")]
    returns = [
        float((equity_curve[1] - equity_curve[0]) / equity_curve[0]),
        float((equity_curve[2] - equity_curve[1]) / equity_curve[1]),
        float((equity_curve[3] - equity_curve[2]) / equity_curve[2]),
    ]
    expected = (fmean(returns) / stdev(returns)) * math.sqrt(96 * 365)

    assert _sharpe_ratio(equity_curve) == pytest.approx(expected)


def test_sharpe_ratio_default_bars_per_day_is_96_unchanged_from_before_the_1h_variant():
    """Regression guard: Strategy Research Task F added an explicit
    `bars_per_day` parameter (needed for the native-1h strategy variant --
    see `.planning/sr-f-risk-management-and-1h-variant.md`) to what was
    previously a hardcoded `sqrt(96 * 365)` annualization. Every existing
    15m caller must see byte-for-byte identical behavior when it omits the
    new parameter.
    """
    equity_curve = [Decimal("100"), Decimal("110"), Decimal("105"), Decimal("115")]
    assert _sharpe_ratio(equity_curve) == _sharpe_ratio(equity_curve, bars_per_day=96)


def test_sharpe_ratio_uses_a_smaller_annualization_factor_for_fewer_bars_per_day():
    """1h bars (24/day) must annualize with `sqrt(24 * 365)`, not the 15m
    default `sqrt(96 * 365)` -- using the wrong (15m) factor for 1h data
    would inflate the reported Sharpe by exactly sqrt(96/24) = 2x, since
    the same per-bar return series would be (incorrectly) treated as
    happening 4x more often per day than it really did.
    """
    equity_curve = [Decimal("100"), Decimal("110"), Decimal("105"), Decimal("115")]
    returns = [
        float((equity_curve[1] - equity_curve[0]) / equity_curve[0]),
        float((equity_curve[2] - equity_curve[1]) / equity_curve[1]),
        float((equity_curve[3] - equity_curve[2]) / equity_curve[2]),
    ]
    expected_1h = (fmean(returns) / stdev(returns)) * math.sqrt(24 * 365)

    result_1h = _sharpe_ratio(equity_curve, bars_per_day=24)
    assert result_1h == pytest.approx(expected_1h)

    result_15m = _sharpe_ratio(equity_curve, bars_per_day=96)
    assert result_1h == pytest.approx(result_15m / 2)


def test_sharpe_ratio_rejects_non_positive_bars_per_day():
    """`bars_per_day=0` would silently zero out the annualization factor,
    reporting Sharpe as `0.0` (a real "no edge" claim) rather than `None`
    ("no evidence") -- and a negative value would raise an opaque
    `math.sqrt` `ValueError` deep inside the annualization arithmetic
    instead of a clear, fail-loud error at the entry point. Same
    fail-loud convention as `compute_metrics`'s existing
    `starting_equity`/`research.walkforward.run_walk_forward`'s existing
    `fee_bps`/`slippage_bps` validation.
    """
    equity_curve = [Decimal("100"), Decimal("110"), Decimal("105")]
    with pytest.raises(ValueError, match="bars_per_day"):
        _sharpe_ratio(equity_curve, bars_per_day=0)
    with pytest.raises(ValueError, match="bars_per_day"):
        _sharpe_ratio(equity_curve, bars_per_day=-1)


def test_sharpe_ratio_is_none_when_per_bar_returns_have_zero_variance():
    # Constant 10% return every bar => stdev == 0 => sharpe denominator is
    # zero. Must yield None, never a ZeroDivisionError or an inflated value.
    equity_curve = [Decimal("100"), Decimal("110"), Decimal("121")]

    assert _sharpe_ratio(equity_curve) is None


def test_sharpe_ratio_is_none_for_a_flat_equity_curve():
    equity_curve = [Decimal("100"), Decimal("100"), Decimal("100")]

    assert _sharpe_ratio(equity_curve) is None


def test_sharpe_ratio_is_none_with_fewer_than_two_bars():
    assert _sharpe_ratio([]) is None
    assert _sharpe_ratio([Decimal("100")]) is None


# --- Max drawdown -------------------------------------------------------------


def test_max_drawdown_is_the_largest_peak_to_trough_decline():
    equity_curve = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("130"), Decimal("80")]

    assert _max_drawdown(equity_curve) == Decimal("50") / Decimal("130")


def test_max_drawdown_is_zero_for_a_monotonically_increasing_curve():
    equity_curve = [Decimal("100"), Decimal("110"), Decimal("120")]

    assert _max_drawdown(equity_curve) == Decimal("0")


def test_max_drawdown_of_empty_curve_is_zero():
    assert _max_drawdown([]) == Decimal("0")


# --- Win rate / profit factor degenerate cases --------------------------------


def test_win_rate_and_profit_factor_are_none_with_zero_closed_trades():
    assert _win_rate([]) is None
    assert _profit_factor([]) is None


def test_profit_factor_is_none_not_inf_when_there_are_zero_losing_trades():
    klines = [_kline(0, "100"), _kline(1, "110"), _kline(2, "120")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)
    exit_intent = _intent(Side.SHORT, "1", klines[1].open_time)
    exit_fill = _fill(exit_intent, "110", "1", klines[1].open_time)

    metrics = compute_metrics(klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY)

    assert metrics.num_trades == 1
    assert metrics.win_rate == 1.0
    assert metrics.profit_factor is None


def test_profit_factor_and_win_rate_with_a_mix_of_winning_and_losing_trades():
    klines = [_kline(i, "100") for i in range(5)]
    # Trade 1: long, +10.
    e1 = _intent(Side.LONG, "1", klines[0].open_time)
    f1 = _fill(e1, "100", "1", klines[0].open_time)
    x1 = _intent(Side.SHORT, "1", klines[1].open_time)
    fx1 = _fill(x1, "110", "1", klines[1].open_time)
    # Trade 2: long, -4.
    e2 = _intent(Side.LONG, "1", klines[2].open_time)
    f2 = _fill(e2, "100", "1", klines[2].open_time)
    x2 = _intent(Side.SHORT, "1", klines[3].open_time)
    fx2 = _fill(x2, "96", "1", klines[3].open_time)

    metrics = compute_metrics(
        klines, [e1, x1, e2, x2], [f1, fx1, f2, fx2], STARTING_EQUITY
    )

    assert metrics.num_trades == 2
    assert metrics.win_rate == 0.5
    assert metrics.profit_factor == pytest.approx(10 / 4)


# --- compute_metrics: total return, integration --------------------------------


def test_total_return_reflects_final_equity_over_starting_equity():
    klines = [_kline(0, "100"), _kline(1, "105"), _kline(2, "110"), _kline(3, "110")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)
    exit_intent = _intent(Side.SHORT, "1", klines[2].open_time)
    exit_fill = _fill(exit_intent, "110", "1", klines[2].open_time)

    metrics = compute_metrics(
        klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY
    )

    assert isinstance(metrics, Metrics)
    assert metrics.final_equity == Decimal("10010")
    assert metrics.total_return == Decimal("10010") / Decimal("10000") - 1
    assert metrics.num_trades == 1


def test_compute_metrics_with_zero_trades_reports_none_for_evidence_based_fields():
    klines = [_kline(0, "100"), _kline(1, "100"), _kline(2, "100")]

    metrics = compute_metrics(klines, [], [], STARTING_EQUITY)

    assert metrics.num_trades == 0
    assert metrics.sharpe_ratio is None
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.total_return == Decimal("0")
    assert metrics.max_drawdown == Decimal("0")


def test_compute_metrics_passes_bars_per_day_through_to_sharpe_ratio():
    """`compute_metrics`'s `bars_per_day` (default 96, matching the
    pre-Task-F hardcoded 15m assumption) must reach `_sharpe_ratio`'s own
    annualization -- see `.planning/sr-f-risk-management-and-1h-
    variant.md` for why this parameter exists (the native-1h strategy
    variant would otherwise silently get a 2x-inflated Sharpe using the
    15m annualization factor).
    """
    klines = [_kline(0, "100"), _kline(1, "110"), _kline(2, "105"), _kline(3, "115")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)
    exit_intent = _intent(Side.SHORT, "1", klines[1].open_time)
    exit_fill = _fill(exit_intent, "110", "1", klines[1].open_time)

    metrics_default = compute_metrics(klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY)
    metrics_default_explicit = compute_metrics(
        klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY, bars_per_day=96
    )
    metrics_1h = compute_metrics(
        klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY, bars_per_day=24
    )

    assert metrics_default.sharpe_ratio == metrics_default_explicit.sharpe_ratio
    assert metrics_default.sharpe_ratio is not None
    assert metrics_1h.sharpe_ratio is not None
    assert metrics_1h.sharpe_ratio == pytest.approx(metrics_default.sharpe_ratio / 2)


def test_compute_metrics_rejects_non_positive_bars_per_day():
    klines = [_kline(0, "100"), _kline(1, "110")]
    with pytest.raises(ValueError, match="bars_per_day"):
        compute_metrics(klines, [], [], STARTING_EQUITY, bars_per_day=0)


# ---------------------------------------------------------------------------
# funding_rates threading -- Strategy Research Task M, additive/opt-in.
# Sign-convention/attribution correctness itself is metrics.position's
# responsibility and covered by test_position.py; these tests only check
# that metrics.py wires the (opt-in) funding series through correctly and
# that omitting it changes nothing.
# ---------------------------------------------------------------------------


def _funding(rate: str, at: datetime, mark_price: str = "100") -> FundingRate:
    return FundingRate(funding_time=at, funding_rate=Decimal(rate), mark_price=Decimal(mark_price))


def test_build_equity_curve_omitting_funding_rates_behaves_exactly_as_passing_none_explicitly():
    klines = [_kline(0, "100"), _kline(1, "100")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)

    omitted = build_equity_curve(klines, [entry_intent], [entry_fill], STARTING_EQUITY)
    explicit_none = build_equity_curve(klines, [entry_intent], [entry_fill], STARTING_EQUITY, funding_rates=None)

    assert omitted == explicit_none


def test_build_equity_curve_reflects_funding_payment_at_the_bar_it_occurs():
    # A long position held from bar 0, with a funding settlement landing
    # exactly on bar 1's open_time -- the equity curve must show the
    # funding-driven drop starting at bar 1, not only once the trade
    # eventually closes (which is the whole point of calling
    # apply_funding_through once per bar, not only once per fill).
    klines = [_kline(0, "100"), _kline(1, "100"), _kline(2, "100")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)
    funding = [_funding("0.0001", klines[1].open_time, mark_price="100")]

    equity_curve, closed_trades = build_equity_curve(
        klines, [entry_intent], [entry_fill], STARTING_EQUITY, funding_rates=funding
    )

    assert equity_curve[0] == STARTING_EQUITY  # before the funding settlement
    assert equity_curve[1] == STARTING_EQUITY - Decimal("0.01")  # notional 100 * rate 0.0001
    assert equity_curve[2] == STARTING_EQUITY - Decimal("0.01")  # unchanged after
    # Still open at klines[-1] -- force-closed there per the existing
    # degenerate-input rule (unrelated to funding), which already
    # includes the funding payment realized before that force-close ran.
    assert len(closed_trades) == 1
    assert closed_trades[0].funding_pnl == Decimal("-0.01")


def test_compute_metrics_threads_funding_rates_through_to_closed_trades():
    klines = [_kline(0, "100"), _kline(1, "100"), _kline(2, "100")]
    entry_intent = _intent(Side.LONG, "1", klines[0].open_time)
    entry_fill = _fill(entry_intent, "100", "1", klines[0].open_time)
    exit_intent = _intent(Side.SHORT, "1", klines[2].open_time)
    exit_fill = _fill(exit_intent, "100", "1", klines[2].open_time)
    funding = [_funding("0.0001", klines[1].open_time, mark_price="100")]

    with_funding = compute_metrics(
        klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY, funding_rates=funding
    )
    without_funding = compute_metrics(klines, [entry_intent, exit_intent], [entry_fill, exit_fill], STARTING_EQUITY)

    assert len(with_funding.closed_trades) == 1
    assert with_funding.closed_trades[0].funding_pnl == Decimal("-0.01")
    assert with_funding.closed_trades[0].realized_pnl == Decimal("-0.01")
    assert with_funding.final_equity == STARTING_EQUITY - Decimal("0.01")
    # The only difference from the no-funding run is the funding
    # adjustment itself -- same trade count, same entry/exit prices.
    assert without_funding.closed_trades[0].funding_pnl == Decimal("0")
    assert without_funding.final_equity == STARTING_EQUITY
