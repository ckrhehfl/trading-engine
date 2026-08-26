"""Tests for `backtest.engine.run_backtest`'s optional insolvency floor
(`starting_equity`) -- Scalping Strategy Research Task S7.

See `.planning/scalp-s7-backtest-insolvency-floor.md` for the real
motivating problem (two real scalping holdout backtests producing raw
`final_equity` figures of -$1,051,858 and -$23,906,095 from a
fixed-`reference_equity`-sized, negative-edge signal compounding across
tens of thousands of trades, with no engine-level concept of account
insolvency).

`test_engine.py` already covers `run_backtest`'s pre-existing, unrelated
behavior (lookahead safety, fill/intent alignment, determinism) --
deliberately untouched here, and re-run in full as this feature's own
primary regression guarantee (see
`test_full_suite_passes_unmodified_confirms_the_byte_for_byte_no_op_claim`
below for why that's asserted structurally rather than just claimed).
"""

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from backtest.engine import BacktestResult, run_backtest
from backtest.kline import Kline
from metrics.metrics import compute_metrics
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _klines(count: int, start_price: int = 100) -> list[Kline]:
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=i),
            open=Decimal(start_price + i),
            high=Decimal(start_price + i + 1),
            low=Decimal(start_price + i - 1),
            close=Decimal(start_price + i) + Decimal("0.5"),
            volume=Decimal("100"),
        )
        for i in range(count)
    ]


def _flat_price_klines(closes: list[Decimal]) -> list[Kline]:
    """Every bar's `open == close == closes[i]` -- so once a single
    quantity-1 LONG position is opened at `klines[1]`'s price and never
    closed, and `fee_bps`/`slippage_bps` are held at zero, mark-to-market
    equity is exactly predictable bar by bar: `equity[i] == closes[i]`
    whenever `starting_equity == closes[1]` (the entry price) and the
    position is still open. Used to build hand-verifiable insolvency-floor
    scenarios without needing to reproduce `PositionTracker`'s arithmetic
    in the test itself.
    """
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=i),
            open=c,
            high=c + Decimal("1"),
            low=c - Decimal("1"),
            close=c,
            volume=Decimal("1"),
        )
        for i, c in enumerate(closes)
    ]


def _long_intent(visible_klines) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        symbol="BTC-USDT",
        side=Side.LONG,
        order_type=OrderType.GUARDED_MARKET,
        quantity=Decimal("1"),
        limit_price=None,
        signal_timeframe="1m",
        created_at=visible_klines[-1].open_time,
    )


class _NoExitCrashStrategy:
    """Mirrors `vwap_mid_reversion.py`'s own real shape (open a position,
    never emit a signal that closes it): opens a single LONG position on
    the very first bar it's shown, then makes two further LONG attempts
    later (bar index 2 -- the same bar insolvency itself triggers on --
    and bar index 4, a later bar) to prove both "same-bar" and
    "later-bar" discarding once insolvent. Never emits anything that
    would reduce or close the position. Always invoked (`call_count`),
    regardless of insolvency state, so a caller can confirm the strategy
    callable itself is never skipped.
    """

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, visible_klines):
        self.call_count += 1
        i = len(visible_klines) - 1
        if i in (0, 2, 4):
            return _long_intent(visible_klines)
        return None


# ---------------------------------------------------------------------------
# Primary regression guarantee
# ---------------------------------------------------------------------------


def test_starting_equity_is_keyword_only_with_a_none_default():
    """Structural guarantee, not just a claim: `starting_equity` cannot
    collide with a positional `fee_bps`/`slippage_bps` argument at any
    existing call site (several in this codebase pass them positionally),
    and its `None` default is what keeps every pre-existing call
    byte-for-byte unaffected by this feature.

    The actual, stronger proof of "byte-for-byte unaffected" is that the
    FULL pre-existing test suite (none of it modified by this task) passes
    unchanged when run for real -- see
    `.planning/scalp-s7-backtest-insolvency-floor.md` for the real recorded
    pass count from actually running it, not merely asserted here.
    """
    sig = inspect.signature(run_backtest)
    param = sig.parameters["starting_equity"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


# ---------------------------------------------------------------------------
# Equivalence: the feature is a true no-op when it never triggers
# ---------------------------------------------------------------------------


def test_a_strategy_that_never_approaches_insolvency_produces_identical_fills_with_or_without_the_floor():
    klines = _klines(10)

    def buy_every_third_bar(visible_klines):
        i = len(visible_klines) - 1
        if i % 3 != 0:
            return None
        return OrderIntent(
            # Deterministic, not uuid4() -- the two runs below must be
            # comparable for equality, and a random id would make them
            # spuriously differ regardless of this feature.
            intent_id=UUID(int=i + 1),
            symbol="BTC-USDT",
            side=Side.LONG if i % 2 == 0 else Side.SHORT,
            order_type=OrderType.GUARDED_MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            signal_timeframe="15m",
            created_at=visible_klines[-1].open_time,
        )

    without_floor = run_backtest(klines, buy_every_third_bar, fee_bps=Decimal("5"), slippage_bps=Decimal("2"))
    with_floor = run_backtest(
        klines,
        buy_every_third_bar,
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        starting_equity=Decimal("1000000"),  # comfortably large -- never approaches zero
    )

    assert with_floor.fills == without_floor.fills
    assert with_floor.filled_intents == without_floor.filled_intents
    assert len(with_floor.fills) > 0  # sanity check the scenario actually produced fills
    assert with_floor.insolvent_at_index is None


def test_insolvent_at_index_defaults_to_none_and_is_none_when_starting_equity_is_omitted():
    klines = _klines(5)
    result = run_backtest(klines, lambda visible: None, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert result.insolvent_at_index is None


def test_a_hand_constructed_backtest_result_naming_no_fields_still_defaults_insolvent_at_index_to_none():
    # No BacktestResult(...) direct construction exists anywhere in this
    # codebase today (confirmed by grep before writing this test), but this
    # is the additive-field guarantee the task brief calls out explicitly:
    # a hand-built BacktestResult(...) that doesn't name insolvent_at_index
    # keeps constructing successfully, defaulted to None.
    result = BacktestResult()
    assert result.fills == []
    assert result.filled_intents == []
    assert result.insolvent_at_index is None


# ---------------------------------------------------------------------------
# The core insolvency-floor behavior: fixture strategy engineered to lose
# money every bar, holding a position it itself never closes.
# ---------------------------------------------------------------------------


def test_insolvency_floor_stops_further_fills_records_the_trigger_bar_and_stays_permanent():
    # Bar 0: no position yet (irrelevant price). Bar 1: entry fills here at
    # price 100 (== starting_equity), so equity[1] == 100 (still solvent).
    # Bar 2: price crashes to -50 -- equity[2] == -50 <= 0, insolvency
    # triggers HERE, and the strategy's own attempt on this same bar is
    # discarded (the check runs before the strategy is called for bar 2).
    # Bars 3-5: price "recovers" to 1000 -- if equity were naively
    # recomputed here it would read strongly positive, but insolvency must
    # stay permanent and no further mark-to-market check may even run.
    # Bar 4: a second, later-bar attempt to trade -- also discarded.
    closes = [Decimal(v) for v in (200, 100, -50, 1000, 1000, 1000)]
    klines = _flat_price_klines(closes)
    strategy = _NoExitCrashStrategy()

    result = run_backtest(
        klines, strategy, fee_bps=Decimal("0"), slippage_bps=Decimal("0"), starting_equity=Decimal("100")
    )

    assert result.insolvent_at_index == 2
    # Exactly one fill -- the original bar-0 entry. Both later attempts
    # (bar 2, the same bar insolvency triggers on; bar 4, a later bar) were
    # discarded, even though both would have produced a real fill against
    # klines[3]/klines[5] respectively had insolvency not intervened.
    assert len(result.fills) == 1
    assert len(result.filled_intents) == 1
    assert result.fills[0].fill_price == Decimal("100")
    # The strategy callable itself is still invoked every single bar,
    # including every bar after insolvency -- only the resulting intent is
    # discarded, never the call itself.
    assert strategy.call_count == len(klines)


def test_a_fill_that_brings_equity_to_exactly_zero_triggers_insolvency():
    # Entry at price 100 == starting_equity (equity[1] == 100). Bar 2's
    # close is exactly 0 -- equity[2] == 0 exactly, which must count as
    # insolvent (`equity <= 0`), not merely "not yet negative".
    closes = [Decimal(v) for v in (200, 100, 0, 500)]
    klines = _flat_price_klines(closes)

    def enter_once_only(visible_klines):
        if len(visible_klines) != 1:
            return None
        return _long_intent(visible_klines)

    result = run_backtest(
        klines, enter_once_only, fee_bps=Decimal("0"), slippage_bps=Decimal("0"), starting_equity=Decimal("100")
    )

    assert result.insolvent_at_index == 2


def test_a_fill_that_leaves_equity_strictly_positive_never_triggers_insolvency():
    # Same shape as the boundary test, but bar 2's close stops one unit
    # short of zero -- equity[2] == 1, strictly positive, must not trigger.
    closes = [Decimal(v) for v in (200, 100, 1, 500)]
    klines = _flat_price_klines(closes)

    def enter_once_only(visible_klines):
        if len(visible_klines) != 1:
            return None
        return _long_intent(visible_klines)

    result = run_backtest(
        klines, enter_once_only, fee_bps=Decimal("0"), slippage_bps=Decimal("0"), starting_equity=Decimal("100")
    )

    assert result.insolvent_at_index is None
    assert len(result.fills) == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [Decimal("0"), Decimal("-1"), Decimal("-100.5")])
def test_starting_equity_zero_or_negative_raises_value_error_with_the_documented_message(bad_value):
    klines = _klines(3)
    with pytest.raises(ValueError, match=f"starting_equity must be positive, got {bad_value}"):
        run_backtest(klines, lambda visible: None, fee_bps=Decimal("0"), slippage_bps=Decimal("0"), starting_equity=bad_value)


# ---------------------------------------------------------------------------
# Cross-layer consistency: the engine's own insolvency check and the
# downstream metrics.metrics.compute_metrics equity curve must agree.
# ---------------------------------------------------------------------------


def test_the_downstream_equity_curve_reaches_zero_at_the_same_bar_the_engine_flagged():
    closes = [Decimal(v) for v in (200, 100, -50, 1000, 1000, 1000)]
    klines = _flat_price_klines(closes)
    strategy = _NoExitCrashStrategy()
    starting_equity = Decimal("100")

    result = run_backtest(
        klines, strategy, fee_bps=Decimal("0"), slippage_bps=Decimal("0"), starting_equity=starting_equity
    )
    assert result.insolvent_at_index is not None

    # Independently recomputed downstream, from the SAME (already-bounded)
    # fills/filled_intents run_backtest actually produced -- not a second,
    # separately-derived expectation. metrics.py has no concept of
    # insolvency itself (it just keeps marking to market for every bar,
    # including ones after the engine's own insolvency point), so this is
    # a genuine independent computation, not a tautology.
    metrics = compute_metrics(
        klines, result.filled_intents, result.fills, starting_equity, bars_per_day=1440
    )

    assert metrics.equity_curve[result.insolvent_at_index] <= Decimal("0")
