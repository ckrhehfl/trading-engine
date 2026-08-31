from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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


def test_filled_intents_is_index_aligned_with_fills():
    klines = _klines(4)

    def buy_on_bars_0_and_2(visible_klines):
        i = len(visible_klines) - 1
        if i not in (0, 2):
            return None
        return OrderIntent(
            intent_id=UUID(int=i + 1),
            symbol="BTC-USDT",
            side=Side.LONG if i == 0 else Side.SHORT,
            order_type=OrderType.GUARDED_MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            signal_timeframe="15m",
            created_at=visible_klines[-1].open_time,
        )

    result = run_backtest(klines, buy_on_bars_0_and_2, fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

    assert len(result.filled_intents) == len(result.fills) == 2
    assert result.filled_intents[0].side == Side.LONG
    assert result.filled_intents[0].intent_id == result.fills[0].intent_id
    assert result.filled_intents[1].side == Side.SHORT
    assert result.filled_intents[1].intent_id == result.fills[1].intent_id


def test_no_fill_means_no_entry_appended_to_filled_intents():
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
    assert result.filled_intents == []


def test_same_inputs_produce_identical_results_every_run():
    klines = _klines(10)

    def buy_every_third_bar(visible_klines):
        i = len(visible_klines) - 1
        if i % 3 != 0:
            return None
        return OrderIntent(
            # Deterministic, not uuid4() — a random id would make the two
            # runs genuinely different and mask a real determinism bug.
            intent_id=UUID(int=i + 1),
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

    assert result_a.fills == result_b.fills
    assert len(result_a.fills) > 0  # sanity check the scenario actually produced fills


class TestEquityObserver:
    """Scalping Task S15. The engine already reconstructs mark-to-market
    equity every bar when `starting_equity` is supplied (that is how the S7
    insolvency floor works); this is the seam that hands the value it
    already has to a strategy that asks for it."""

    def test_observer_is_not_called_without_starting_equity(self):
        seen: list[Decimal] = []

        class Observing:
            def on_equity(self, equity, /):
                seen.append(equity)

            def __call__(self, window):
                return None

        run_backtest(_klines(5), Observing(), fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
        assert seen == [], "no starting_equity means there is no equity to report"

    def test_observer_receives_one_value_per_bar_before_the_intent(self):
        seen: list[Decimal] = []
        order: list[str] = []

        class Observing:
            def on_equity(self, equity, /):
                seen.append(equity)
                order.append("equity")

            def __call__(self, window):
                order.append("call")
                return None

        klines = _klines(4)
        run_backtest(
            klines, Observing(), fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            starting_equity=Decimal("10000"),
        )
        assert seen == [Decimal("10000")] * len(klines)
        # Strictly alternating, equity first: a strategy must never size
        # against a figure that already reflects its own pending decision.
        assert order == ["equity", "call"] * len(klines)

    def test_a_plain_callable_strategy_is_unaffected(self):
        calls: list[int] = []

        def plain(window):
            calls.append(len(window))
            return None

        run_backtest(
            _klines(5), plain, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            starting_equity=Decimal("10000"),
        )
        assert calls == [1, 2, 3, 4, 5]


class TestMultipleIntentsPerBar:
    """Trade Management Task A. A leg-scoped strategy routinely needs to
    act twice on one bar -- close the tactical short AND add to the core
    -- and splitting that across two bars would change the prices it acts
    at."""

    def _intent(self, side=Side.LONG, qty="1"):
        return OrderIntent(
            intent_id=uuid4(), symbol="BTC-USDT", side=side,
            order_type=OrderType.GUARDED_MARKET, quantity=Decimal(qty),
            limit_price=None, signal_timeframe="1d",
            created_at=BASE_TIME,
        )

    def test_a_single_intent_still_works_unchanged(self):
        klines = _klines(4)
        emitted = [self._intent(), None, None, None]
        result = run_backtest(
            klines, lambda w: emitted[len(w) - 1],
            fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
        )
        assert len(result.fills) == 1

    def test_a_sequence_fills_every_intent_on_the_same_bar(self):
        klines = _klines(4)
        pair = [self._intent(Side.SHORT), self._intent(Side.LONG, "2")]
        result = run_backtest(
            klines, lambda w: pair if len(w) == 1 else None,
            fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
        )
        assert len(result.fills) == 2
        assert [i.side for i in result.filled_intents] == [Side.SHORT, Side.LONG]

    def test_order_within_the_bar_is_preserved(self):
        """Closing before opening matters: the two are different economic
        events and `PositionTracker` applies them in order."""
        klines = _klines(4)
        first, second = self._intent(Side.SHORT, "1"), self._intent(Side.LONG, "3")
        result = run_backtest(
            klines, lambda w: [first, second] if len(w) == 1 else None,
            fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
        )
        assert [i.intent_id for i in result.filled_intents] == [first.intent_id, second.intent_id]

    def test_an_empty_sequence_is_a_no_op(self):
        result = run_backtest(
            _klines(4), lambda w: [],
            fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
        )
        assert result.fills == []

    def test_insolvency_discards_the_whole_batch(self):
        """Not just the first intent -- an insolvent account cannot fill
        any leg of a multi-leg action, so the check sits before the loop
        rather than inside it."""
        # `_klines` rises, so a large SHORT loses money fast.
        klines = _klines(12)
        big_short = self._intent(Side.SHORT, "500")

        def strategy(window):
            if len(window) == 1:
                return [big_short]
            return [self._intent(Side.LONG, "1"), self._intent(Side.LONG, "1")]

        result = run_backtest(
            klines, strategy, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            starting_equity=Decimal("100"),
        )
        assert result.insolvent_at_index is not None, "fixture must actually go insolvent"
        # Every post-insolvency batch is dropped whole. The fixture opens
        # with a single short and then emits complete two-order batches, so
        # a correct run ends on an ODD count; an even one would mean half a
        # batch got through before the drop.
        assert len(result.fills) % 2 == 1, (
            "one opening short plus zero-or-more complete pairs; a half-applied "
            "batch would make this even"
        )
