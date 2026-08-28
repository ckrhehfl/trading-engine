"""Tests for `research.strategies.selective_reversion` (Scalping Task S14).

Two things get the most attention here, because they are what this
strategy adds over its siblings and what would fail silently if wrong:
the **R:R qualification gate** (a trade the setup fires on can be
declined outright, which nothing in S4/S6 could do) and the two
**look-ahead-safe rolling calculators**, one of which replaces a stale-
snapshot bug that a sibling module really shipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.strategies.selective_reversion import (
    DEFAULT_PARAMS,
    SelectiveReversionStrategy,
    SelectiveReversionTrainable,
    TrailingPercentileRank,
    TrailingZScore,
    _taker_share,
)
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "BINANCE-FUTURES:BTCUSDT"


def _kline(
    i: int,
    *,
    close: Decimal | str = Decimal("100"),
    high: Decimal | str | None = None,
    low: Decimal | str | None = None,
    volume: Decimal | str = Decimal("10"),
    taker: Decimal | str | None = Decimal("5"),
) -> Kline:
    close = Decimal(close)
    return Kline(
        open_time=BASE_TIME + timedelta(minutes=i),
        open=close,
        high=Decimal(high) if high is not None else close + Decimal("1"),
        low=Decimal(low) if low is not None else close - Decimal("1"),
        close=close,
        volume=Decimal(volume),
        taker_buy_base_volume=None if taker is None else Decimal(taker),
    )


class TestTrailingZScore:
    def test_warms_up_before_returning_anything(self):
        z = TrailingZScore(window=4)
        assert [z.update(Decimal(v)) for v in (1, 2, 3, 4)] == [None] * 4

    def test_current_value_is_excluded_from_its_own_window(self):
        """The whole point: a bar must not normalise against itself."""
        z = TrailingZScore(window=4)
        for v in (0, 0, 0, 0):
            z.update(Decimal(v))
        # Reference window is four zeros -> zero variance -> no scale.
        assert z.update(Decimal("100")) is None

    def test_scores_against_the_trailing_window_only(self):
        z = TrailingZScore(window=4)
        for v in (1, 2, 3, 4):
            z.update(Decimal(v))
        result = z.update(Decimal("5"))
        assert result is not None
        score, mean = result
        assert mean == Decimal("2.5")          # (1+2+3+4)/4
        # population stdev of {1,2,3,4} = sqrt(1.25)
        assert float(score) == pytest.approx(float((Decimal("5") - Decimal("2.5")) / Decimal("1.25").sqrt()))

    def test_returns_the_mean_the_score_was_measured_against(self):
        """The reversion target depends on this mean; recomputing it
        elsewhere could drift from the window actually used."""
        z = TrailingZScore(window=3)
        for v in (10, 20, 30):
            z.update(Decimal(v))
        result = z.update(Decimal("40"))
        assert result is not None and result[1] == Decimal("20")

    def test_none_contributes_no_observation(self):
        z = TrailingZScore(window=3)
        for v in (1, None, 2, None, 3):
            z.update(None if v is None else Decimal(v))
        assert z.bars_seen == 3

    def test_zero_variance_window_yields_none_not_infinity(self):
        z = TrailingZScore(window=3)
        for _ in range(3):
            z.update(Decimal("7"))
        assert z.update(Decimal("9")) is None

    def test_window_below_two_is_rejected(self):
        with pytest.raises(ValueError, match="window must be at least 2"):
            TrailingZScore(window=1)


class TestTrailingPercentileRank:
    def test_warms_up_then_ranks_against_exactly_history_observations(self):
        r = TrailingPercentileRank(history=4)
        assert [r.update(Decimal(v)) for v in (1, 2, 3, 4)] == [None] * 4
        assert r.update(Decimal("5")) == Decimal("1")     # above all four
        # Window is now {2,3,4,5}; 0 is below all of them.
        assert r.update(Decimal("0")) == Decimal("0")

    def test_reference_is_never_stale(self):
        """A sibling module shipped a version that rebuilt its sorted
        reference on a schedule, leaving it up to `history - 1`
        observations behind. This is the regression test for that."""
        history = 8
        r = TrailingPercentileRank(history=history)
        seen: list[Decimal] = []
        for i in range(60):
            v = Decimal(i % 5)          # deliberately non-monotonic
            got = r.update(v)
            if len(seen) == history:
                expected = Decimal(sum(1 for s in seen if s < v)) / history
                assert got == expected, f"stale reference at i={i}"
                seen.pop(0)
            seen.append(v)

    def test_none_input_is_ignored(self):
        r = TrailingPercentileRank(history=2)
        r.update(Decimal("1"))
        assert r.update(None) is None
        r.update(Decimal("2"))
        assert r.update(Decimal("3")) == Decimal("1")

    def test_history_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="history must be at least 1"):
            TrailingPercentileRank(history=0)


class TestTakerShare:
    def test_none_when_order_flow_data_is_absent(self):
        assert _taker_share(_kline(0, taker=None)) is None

    def test_none_on_a_zero_volume_bar(self):
        assert _taker_share(_kline(0, volume="0", taker="0")) is None

    def test_ratio_when_present(self):
        assert _taker_share(_kline(0, volume="10", taker="7")) == Decimal("0.7")


def _strategy(**overrides: Any) -> SelectiveReversionStrategy:
    kwargs: dict[str, Any] = dict(
        symbol=SYMBOL,
        htf_lag=3,
        z_window=5,
        activity_history=5,
        activity_quantile=Decimal("0"),   # condition open unless a test closes it
        entry_z=Decimal("1"),
        atr_period=2,
        max_hold_bars=4,
    )
    kwargs.update(overrides)
    return SelectiveReversionStrategy(**kwargs)


def _feed(strategy: SelectiveReversionStrategy, klines: list[Kline]):
    out = []
    for i in range(len(klines)):
        out.append(strategy(klines[: i + 1]))
    return out


class TestConstruction:
    @pytest.mark.parametrize(
        "kwargs,message",
        [
            ({"htf_lag": 0}, "htf_lag must be at least 1"),
            ({"entry_z": Decimal("0")}, "entry_z must be positive"),
            ({"stop_atr_multiple": Decimal("-1")}, "stop_atr_multiple must be positive"),
            ({"min_rr": Decimal("0")}, "min_rr must be positive"),
            ({"max_hold_bars": 0}, "max_hold_bars must be at least 1"),
            ({"activity_quantile": Decimal("1")}, r"activity_quantile must be in \[0, 1\)"),
        ],
    )
    def test_rejects_degenerate_parameters(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            _strategy(**kwargs)


class TestNoSignalCases:
    def test_flat_market_emits_nothing(self):
        s = _strategy()
        assert all(i is None for i in _feed(s, [_kline(i) for i in range(40)]))

    def test_condition_closed_blocks_every_entry(self):
        """Activity quantile 1.0 is rejected at construction, so the
        strongest closable condition is just below it -- and a market
        whose volatility never reaches its own top rank must not trade."""
        s = _strategy(activity_quantile=Decimal("0.999"))
        klines = [_kline(i, close=Decimal(100) + Decimal(i % 7)) for i in range(60)]
        assert all(i is None for i in _feed(s, klines))

    def test_bars_without_order_flow_never_produce_a_setup(self):
        s = _strategy()
        klines = [_kline(i, close=Decimal(100) + Decimal(i % 9), taker=None) for i in range(60)]
        assert all(i is None for i in _feed(s, klines))


class TestRiskRewardGate:
    """The mechanism this strategy adds over every sibling."""

    def test_declines_a_setup_whose_structure_offers_no_room(self):
        # min_rr set absurdly high: no structural target can qualify, so
        # every setup must be declined rather than taken at bad odds.
        s = _strategy(min_rr=Decimal("1000"))
        klines = _oscillating(80)
        intents = _feed(s, klines)
        assert all(i is None for i in intents)
        assert s.declined_on_rr > 0, "the setup never fired -- fixture is not exercising the gate"

    def test_the_same_setups_are_taken_once_the_gate_is_relaxed(self):
        strict = _strategy(min_rr=Decimal("1000"))
        loose = _strategy(min_rr=Decimal("0.0001"))
        klines = _oscillating(80)
        _feed(strict, klines)
        taken = [i for i in _feed(loose, klines) if i is not None]
        assert strict.declined_on_rr > 0
        assert taken, "relaxing the gate must let some declined setup through"

    def test_declined_count_is_reported_not_swallowed(self):
        s = _strategy(min_rr=Decimal("1000"))
        assert s.declined_on_rr == 0
        _feed(s, _oscillating(80))
        assert s.declined_on_rr > 0


def _oscillating(n: int) -> list[Kline]:
    """A market with real volatility and a real order-flow swing, so the
    setup actually fires. Amplitude grows so the trailing z-window is
    genuinely exceeded rather than tracking the move."""
    out = []
    for i in range(n):
        swing = Decimal(i % 11) * (Decimal(1) + Decimal(i) / Decimal(40))
        close = Decimal(1000) + swing
        taker = Decimal("9") if i % 11 > 7 else Decimal("1")
        out.append(_kline(i, close=close, high=close + Decimal("3"), low=close - Decimal("3"),
                          volume=Decimal("10"), taker=taker))
    return out


class TestPositionLifecycle:
    def test_entry_is_a_guarded_market_intent_on_the_right_symbol(self):
        s = _strategy(min_rr=Decimal("0.0001"))
        entries = [i for i in _feed(s, _oscillating(80)) if i is not None]
        assert entries, "fixture produced no entry"
        first = entries[0]
        assert first.order_type is OrderType.GUARDED_MARKET
        assert first.symbol == SYMBOL
        assert first.limit_price is None
        assert first.signal_timeframe == "1m"

    def test_never_holds_beyond_max_hold_bars(self):
        max_hold = 4
        s = _strategy(min_rr=Decimal("0.0001"), max_hold_bars=max_hold)
        klines = _oscillating(120)
        held = 0
        worst = 0
        for i in range(len(klines)):
            s(klines[: i + 1])
            if s.open_position is not None:
                held += 1
                worst = max(worst, held)
            else:
                held = 0
        assert 0 < worst <= max_hold

    def test_exit_reverses_the_side_and_matches_the_open_quantity(self):
        s = _strategy(min_rr=Decimal("0.0001"))
        klines = _oscillating(120)
        opened = None
        for i in range(len(klines)):
            intent = s(klines[: i + 1])
            if intent is None:
                continue
            if opened is None:
                opened = intent
                continue
            assert intent.quantity == opened.quantity
            assert intent.side is (Side.SHORT if opened.side is Side.LONG else Side.LONG)
            return
        pytest.fail("fixture never produced a matched open/close pair")

    def test_only_one_position_at_a_time(self):
        s = _strategy(min_rr=Decimal("0.0001"))
        klines = _oscillating(200)
        net = 0
        for i in range(len(klines)):
            if s(klines[: i + 1]) is not None:
                net = 1 - net
            assert (s.open_position is not None) == bool(net)


class TestLookaheadSafety:
    def test_a_bar_sees_only_bars_up_to_itself(self):
        """Feeding the same prefix twice to two independent strategies
        must give identical decisions regardless of what follows -- the
        property that makes a future bar structurally unobservable."""
        klines = _oscillating(120)
        cut = 90
        full = _strategy(min_rr=Decimal("0.0001"))
        truncated = _strategy(min_rr=Decimal("0.0001"))
        a = _feed(full, klines)[:cut]
        b = _feed(truncated, klines[:cut])
        assert [x is None for x in a] == [x is None for x in b]
        assert [None if x is None else (x.side, x.quantity) for x in a] == [
            None if x is None else (x.side, x.quantity) for x in b
        ]


class TestTrainableAdapter:
    def test_fit_ignores_the_train_window(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        a = t.fit([_kline(i) for i in range(10)], DEFAULT_PARAMS, parent_run_id="r")
        b = t.fit([], DEFAULT_PARAMS, parent_run_id="r")
        assert isinstance(a, SelectiveReversionStrategy)
        assert isinstance(b, SelectiveReversionStrategy)

    def test_fit_returns_a_fresh_strategy_each_call(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        assert t.fit([], DEFAULT_PARAMS, parent_run_id="r") is not t.fit([], DEFAULT_PARAMS, parent_run_id="r")

    def test_params_override_the_defaults(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        s = t.fit([], {**DEFAULT_PARAMS, "max_hold_bars": 7}, parent_run_id="r")
        assert s._max_hold_bars == 7

    def test_string_and_float_params_are_coerced_to_decimal(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        s = t.fit([], {**DEFAULT_PARAMS, "entry_z": "3.5", "min_rr": 1.5}, parent_run_id="r")
        assert s._entry_z == Decimal("3.5")
        assert s._min_rr == Decimal("1.5")

    def test_default_params_are_accepted_as_given(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        assert t.fit([], DEFAULT_PARAMS, parent_run_id="r") is not None


class TestEquityAwareSizing:
    """Scalping Task S15. `compounding` closes the half S7 left open:
    fixed sizing keeps risking a share of the ORIGINAL account after most
    of it is gone."""

    def test_fixed_is_the_default_and_needs_no_equity(self):
        s = _strategy(min_rr=Decimal("0.0001"))
        assert s.sizing_equity == Decimal("10000")
        assert [i for i in _feed(s, _oscillating(80)) if i is not None]
        assert s.declined_no_equity == 0

    def test_compounding_refuses_to_trade_without_an_equity_figure(self):
        """Silently falling back to the constant would reproduce exactly
        the behaviour this mode replaces, while reporting itself as
        compounding."""
        s = _strategy(min_rr=Decimal("0.0001"), sizing_mode="compounding")
        assert s.sizing_equity is None
        assert all(i is None for i in _feed(s, _oscillating(80)))
        assert s.declined_no_equity > 0

    def test_compounding_trades_once_equity_arrives(self):
        s = _strategy(min_rr=Decimal("0.0001"), sizing_mode="compounding")
        klines = _oscillating(120)
        got = []
        for i in range(len(klines)):
            s.on_equity(Decimal("10000"))
            got.append(s(klines[: i + 1]))
        assert [g for g in got if g is not None]
        assert s.declined_no_equity == 0

    def test_a_wiped_out_account_cannot_be_sized_against(self):
        s = _strategy(min_rr=Decimal("0.0001"), sizing_mode="compounding")
        klines = _oscillating(80)
        for i in range(len(klines)):
            s.on_equity(Decimal("0"))
            assert s(klines[: i + 1]) is None
        assert s.declined_no_equity > 0

    def test_smaller_equity_gives_a_proportionally_smaller_position(self):
        klines = _oscillating(120)

        def first_quantity(equity: str):
            s = _strategy(min_rr=Decimal("0.0001"), sizing_mode="compounding")
            for i in range(len(klines)):
                s.on_equity(Decimal(equity))
                intent = s(klines[: i + 1])
                if intent is not None:
                    return intent.quantity
            return None

        full = first_quantity("10000")
        half = first_quantity("5000")
        assert full is not None and half is not None
        # Decimal division is exact only to the context's precision, so the
        # two differ in the last unit in the last place; the ratio is what
        # this test is about.
        assert abs(half / full - Decimal("0.5")) < Decimal("1e-20")

    def test_rejects_an_unknown_sizing_mode(self):
        with pytest.raises(ValueError, match="sizing_mode must be one of"):
            _strategy(sizing_mode="martingale")

    def test_trainable_threads_the_mode_through_params(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        s = t.fit([], {**DEFAULT_PARAMS, "sizing_mode": "compounding"}, parent_run_id="r")
        assert s.sizing_equity is None       # compounding, nothing delivered yet


class TestStopAsSizingBasisOnly:
    """Scalping Task S15(b). A stop of every tested width realises a
    larger loss than the position it catches would have taken on its own,
    so `use_stop=False` keeps the ATR distance as the sizing and R:R basis
    while removing it as an exit."""

    def test_default_still_places_a_real_stop(self):
        s = _strategy(min_rr=Decimal("0.0001"))
        klines = _oscillating(200)
        for i in range(len(klines)):
            s(klines[: i + 1])
            if s.open_position is not None:
                pos = s.open_position
                assert Decimal(0) < pos.stop_price < Decimal("1e30")
                return
        pytest.fail("fixture produced no position")

    def test_no_stop_puts_the_exit_level_out_of_reach(self):
        s = _strategy(min_rr=Decimal("0.0001"), use_stop=False)
        klines = _oscillating(200)
        for i in range(len(klines)):
            s(klines[: i + 1])
            pos = s.open_position
            if pos is not None:
                assert pos.stop_price in (Decimal(0), Decimal("1e30"))
                return
        pytest.fail("fixture produced no position")

    def test_no_stop_means_no_stop_exit_ever_fires(self):
        s = _strategy(min_rr=Decimal("0.0001"), use_stop=False, max_hold_bars=5)
        klines = _oscillating(400)
        for i in range(len(klines)):
            s(klines[: i + 1])
        assert s.exits["stop"] == 0
        assert s.exits["time"] + s.exits["target"] > 0

    def test_sizing_is_unchanged_by_removing_the_stop(self):
        """The ATR distance still sizes the position -- only its role as an
        exit level goes away. A different quantity would mean the risk
        basis had silently changed too."""
        klines = _oscillating(200)

        def first_quantity(use_stop: bool):
            s = _strategy(min_rr=Decimal("0.0001"), use_stop=use_stop)
            for i in range(len(klines)):
                intent = s(klines[: i + 1])
                if intent is not None:
                    return intent.quantity
            return None

        assert first_quantity(True) == first_quantity(False)

    def test_trainable_threads_use_stop_through_params(self):
        t = SelectiveReversionTrainable(symbol=SYMBOL)
        s = t.fit([], {**DEFAULT_PARAMS, "use_stop": False}, parent_run_id="r")
        assert s._use_stop is False
