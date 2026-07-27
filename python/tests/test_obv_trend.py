"""Tests for `python/research/strategies/obv_trend.py` -- Strategy
Research Task L's standalone On-Balance Volume trend-confirmation
strategy. See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-l-volume-signal.md` for the full design context: Step 1 of
that task found volume around entry does NOT discriminate Configuration
C's (ensemble_momentum.py's recalibrated-ADX result) own real winning
trades from its losing trades, so per that task's explicit branching
instruction this is a genuinely standalone, independently-evaluated
volume-based strategy -- not a filter bolted onto an existing signal.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/obv_trend.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.obv_trend import (
    DEFAULT_OBV_MA_PERIOD,
    ObvTrendStrategy,
    ObvTrendTrainable,
    OnBalanceVolume,
    _obv_trend_signal,
)
from research.strategies.regime_weighting import DEFAULT_ADX_HIGH_THRESHOLD, DEFAULT_ADX_LOW_THRESHOLD
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

_ADX_MODULE_PATH = "research.strategies.obv_trend.AverageDirectionalIndex.update"
_VOL_MODULE_PATH = "research.strategies.obv_trend.RollingRealizedVolatility.update"
_ATR_MODULE_PATH = "research.strategies.obv_trend.AverageTrueRange.update"


def _hourly_kline(
    i: int, close: Decimal, volume: Decimal = Decimal("1"), base_time: datetime = BASE_TIME
) -> Kline:
    return Kline(
        open_time=base_time + timedelta(hours=i),
        open=close,
        high=close + Decimal("0.5"),
        low=close - Decimal("0.5"),
        close=close,
        volume=volume,
    )


def _flat_klines(count: int, price: str = "100", base_time: datetime = BASE_TIME) -> list[Kline]:
    return [
        Kline(
            open_time=base_time + timedelta(hours=i),
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


_TEST_OBV_MA_PERIOD = 5


def _strategy(**overrides: object) -> ObvTrendStrategy:
    kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT",
        obv_ma_period=_TEST_OBV_MA_PERIOD,
        atr_period=3,
        adx_period=2,
        vol_period=2,
    )
    kwargs.update(overrides)
    return ObvTrendStrategy(**kwargs)


def _force_full_vol_readiness(monkeypatch: pytest.MonkeyPatch, adx_value: str) -> None:
    monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal(adx_value))
    monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))


def _obv_flip_klines(obv_ma_period: int = _TEST_OBV_MA_PERIOD, base_time: datetime = BASE_TIME) -> list[Kline]:
    """A monotonic price DECLINE (constant volume=1 each bar) drives OBV
    monotonically DOWN, `obv[t] = obv[t-1] - 1` every bar -- during a
    sustained decline, current OBV keeps setting new lows below the
    trailing mean of the last `obv_ma_period` OBV readings (which
    necessarily includes older, higher values), so `_obv_trend_signal`
    reads bearish (-1) once the OBV-SMA window is full. A subsequent
    monotonic price RALLY drives OBV monotonically UP by the same
    mechanism, and once it rises enough to clear the (lagging) trailing
    mean, the signal flips to bullish (+1) -- the exact same "V-shaped
    price path -> real crossover" construction
    `test_ensemble_momentum.py`/`test_single_lookback_momentum.py` already
    use for their own fast/slow price-SMA crossover tests, applied here to
    OBV instead of price (a structurally identical "value vs. its own
    trailing rolling mean" relationship -- see module docstring).

    Long enough decline/rally legs that the OBV-SMA warmup completes and a
    real sign flip fires well before either leg ends, for any
    `obv_ma_period` used in this test module (<=10).
    """
    decline = [Decimal(200 - i) for i in range(obv_ma_period + 10)]
    rally = [decline[-1] + Decimal(i) for i in range(1, obv_ma_period + 15)]
    closes = decline + rally
    return [_hourly_kline(i, c, base_time=base_time) for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------
# OnBalanceVolume
# ---------------------------------------------------------------------------


class TestOnBalanceVolume:
    def test_first_bar_returns_none(self):
        obv = OnBalanceVolume()
        assert obv.update(_hourly_kline(0, Decimal("100"))) is None

    def test_close_above_prior_close_adds_volume(self):
        obv = OnBalanceVolume()
        obv.update(_hourly_kline(0, Decimal("100"), volume=Decimal("5")))
        result = obv.update(_hourly_kline(1, Decimal("105"), volume=Decimal("10")))
        assert result == Decimal("10")

    def test_close_below_prior_close_subtracts_volume(self):
        obv = OnBalanceVolume()
        obv.update(_hourly_kline(0, Decimal("100"), volume=Decimal("5")))
        result = obv.update(_hourly_kline(1, Decimal("95"), volume=Decimal("10")))
        assert result == Decimal("-10")

    def test_unchanged_close_leaves_obv_unchanged(self):
        obv = OnBalanceVolume()
        obv.update(_hourly_kline(0, Decimal("100"), volume=Decimal("5")))
        obv.update(_hourly_kline(1, Decimal("105"), volume=Decimal("10")))
        result = obv.update(_hourly_kline(2, Decimal("105"), volume=Decimal("99")))
        assert result == Decimal("10")

    def test_hand_computed_cumulative_sequence(self):
        # closes: 100 -> 102 (up, +vol) -> 101 (down, -vol) -> 101 (flat) -> 104 (up, +vol)
        obv = OnBalanceVolume()
        volumes = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
        closes = [Decimal("100"), Decimal("102"), Decimal("101"), Decimal("101"), Decimal("104")]
        results = []
        for i, (c, v) in enumerate(zip(closes, volumes)):
            results.append(obv.update(_hourly_kline(i, c, volume=v)))
        assert results == [None, Decimal("2"), Decimal("-1"), Decimal("-1"), Decimal("4")]

    def test_lookahead_safety_value_at_bar_k_unaffected_by_future_bars(self):
        prefix = [_hourly_kline(i, Decimal(100 + i), volume=Decimal(i + 1)) for i in range(5)]
        future = [_hourly_kline(5, Decimal("500"), volume=Decimal("999")), _hourly_kline(6, Decimal("1"), volume=Decimal("1"))]

        obv_prefix_only = OnBalanceVolume()
        value_at_prefix_end = None
        for bar in prefix:
            value_at_prefix_end = obv_prefix_only.update(bar)

        obv_with_future = OnBalanceVolume()
        for bar in prefix + future:
            value_at_bar_k = obv_with_future.update(bar)
            if bar is prefix[-1]:
                assert value_at_bar_k == value_at_prefix_end


# ---------------------------------------------------------------------------
# _obv_trend_signal
# ---------------------------------------------------------------------------


class TestObvTrendSignal:
    def test_obv_above_its_sma_is_bullish(self):
        assert _obv_trend_signal(Decimal("10"), Decimal("5")) == 1

    def test_obv_below_its_sma_is_bearish(self):
        assert _obv_trend_signal(Decimal("5"), Decimal("10")) == -1

    def test_obv_exactly_at_its_sma_is_no_signal(self):
        assert _obv_trend_signal(Decimal("7"), Decimal("7")) == 0


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.open_position is None
        assert strategy.bars_seen == 0

    def test_stop_and_target_multiplier_exposed_as_properties(self):
        strategy = _strategy(stop_multiplier=Decimal("1.5"), target_multiplier=Decimal("4.5"))
        assert strategy.stop_multiplier == Decimal("1.5")
        assert strategy.target_multiplier == Decimal("4.5")

    def test_obv_ma_period_exposed_as_property(self):
        strategy = _strategy(obv_ma_period=7)
        assert strategy.obv_ma_period == 7

    def test_default_adx_thresholds_match_regime_weighting_module(self):
        import inspect

        signature = inspect.signature(ObvTrendStrategy.__init__)
        assert signature.parameters["adx_low"].default == DEFAULT_ADX_LOW_THRESHOLD
        assert signature.parameters["adx_high"].default == DEFAULT_ADX_HIGH_THRESHOLD

    def test_default_obv_ma_period_is_20(self):
        assert DEFAULT_OBV_MA_PERIOD == 20


# ---------------------------------------------------------------------------
# Non-inverted regime gating -- OBV trend is a momentum/confirmation
# signal, NOT a reversion one (contrast with mean_reversion.py's inversion)
# ---------------------------------------------------------------------------


class TestNonInvertedRegimeGating:
    def test_adx_at_or_below_low_threshold_suppresses_entry_entirely(self, monkeypatch):
        # Same direction as ensemble_momentum.py/single_lookback_momentum.py
        # -- NOT inverted like mean_reversion.py. At ADX <= low (20,
        # default), compute_regime_weight == 0 -> final_quantity == 0 -> no
        # entry, even though a real OBV crossover fires.
        _force_full_vol_readiness(monkeypatch, "15")
        strategy = _strategy()
        klines = _obv_flip_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is None

    def test_adx_at_or_above_high_threshold_gives_full_conviction(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "30")
        strategy = _strategy()
        klines = _obv_flip_klines()
        # Break at the first fired entry, same convention as
        # TestRiskManagement._run_to_first_entry below -- the rally leg
        # continues well past the flip bar, long enough that running the
        # WHOLE sequence would let this same entry hit its own target and
        # close again before the assertion runs.
        for i in range(len(klines)):
            strategy(klines[: i + 1])
            if strategy.open_position is not None:
                break
        position = strategy.open_position
        assert position is not None, "expected OBV trend to enter at full conviction under high ADX"
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        assert position.quantity == base_quantity * Decimal("1") * vol_scalar

    def test_mid_ramp_adx_gives_the_same_weight_momentum_would_compute_not_inverted(self, monkeypatch):
        # ADX=21.25, default low=20/high=25 -> compute_regime_weight reads
        # (21.25-20)/(25-20) = 0.25 -- used DIRECTLY, not complemented.
        _force_full_vol_readiness(monkeypatch, "21.25")
        strategy = _strategy()
        klines = _obv_flip_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
            if strategy.open_position is not None:
                break
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        expected = base_quantity * Decimal("0.25") * vol_scalar
        assert position.quantity == expected


# ---------------------------------------------------------------------------
# Risk management composition (ATR stop/target, fixed-fractional sizing)
# ---------------------------------------------------------------------------


class TestRiskManagement:
    def _run_to_first_entry(self, strategy: ObvTrendStrategy) -> tuple[list[Kline], int]:
        klines = _obv_flip_klines()
        entry_index = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None and strategy.open_position is not None:
                entry_index = i
                break
        assert entry_index is not None, "expected an entry to fire in this scenario"
        return klines, entry_index

    def test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "30")
        monkeypatch.setattr(_ATR_MODULE_PATH, lambda self, kline: Decimal("2"))
        strategy = _strategy()
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

    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "30")
        strategy = _strategy()
        klines, _ = self._run_to_first_entry(strategy)
        position_before = strategy.open_position
        assert position_before is not None
        quantity_before = position_before.quantity
        side_before = position_before.side
        stop_price = position_before.stop_price

        next_time = klines[-1].open_time + timedelta(hours=1)
        if side_before == Side.LONG:
            breach = Kline(
                open_time=next_time,
                open=stop_price,
                high=stop_price,
                low=stop_price - Decimal("50"),
                close=stop_price - Decimal("10"),
                volume=Decimal("1"),
            )
        else:
            breach = Kline(
                open_time=next_time,
                open=stop_price,
                high=stop_price + Decimal("50"),
                low=stop_price,
                close=stop_price + Decimal("10"),
                volume=Decimal("1"),
            )
        intent = strategy(klines + [breach])
        assert intent is not None
        assert intent.quantity == quantity_before
        assert intent.side != side_before
        assert strategy.open_position is None

    def test_entry_intent_has_1h_signal_timeframe_and_guarded_market(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "30")
        strategy = _strategy()
        klines, entry_index = self._run_to_first_entry(strategy)
        fresh = _strategy()
        intent = None
        for i in range(entry_index + 1):
            intent = fresh(klines[: i + 1])
        assert intent is not None
        assert intent.signal_timeframe == "1h"
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.limit_price is None


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------


class TestWarmup:
    def test_no_signal_until_obv_sma_window_is_full(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "30")
        strategy = _strategy(obv_ma_period=10)
        # 9 closes -> OBV defined from bar 2 onward (8 OBV readings), still
        # < obv_ma_period (10) -- never enough for a signal.
        closes = [Decimal(100 + i) for i in range(9)]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)

    def test_no_signal_on_the_very_first_bar_regardless_of_obv_ma_period(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "30")
        strategy = _strategy(obv_ma_period=1)
        intent = strategy([_hourly_kline(0, Decimal("100"))])
        assert intent is None


# ---------------------------------------------------------------------------
# Edge-triggering: entry_rejected_by_filters must not consume signal state
# ---------------------------------------------------------------------------


class TestEdgeTriggering:
    def test_filter_rejected_entry_does_not_consume_signal_state(self, monkeypatch):
        # ADX below the low threshold suppresses every entry (regime_weight
        # == 0) but a real OBV crossover still fires the edge-trigger
        # condition internally. If _signal_state were consumed anyway, a
        # LATER favorable-ADX bar with the SAME (already-consumed) OBV
        # signal state would never fire -- this test proves it still can.
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        adx_values = iter([Decimal("15")] * 200)  # start fully suppressed

        def fake_adx(self, kline):
            return next(adx_values)

        monkeypatch.setattr(_ADX_MODULE_PATH, fake_adx)
        strategy = _strategy()
        klines = _obv_flip_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is None, "sanity: suppressed the whole way through so far"

        # Now let ADX go favorable on a FRESH bar with the signal state
        # already established (bullish, from the flip klines above) --
        # the entry should still be able to fire since the edge-trigger
        # state was never consumed by the earlier filter-rejected attempt.
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        next_time = klines[-1].open_time + timedelta(hours=1)
        # A same-direction bar (price still rising) keeps current_signal
        # bullish without flipping it again -- proving the PRE-EXISTING
        # bullish state (never consumed) is what lets this fire, not a
        # brand new flip.
        extra = _hourly_kline(len(klines), klines[-1].close + Decimal("5"), base_time=BASE_TIME)
        extra = Kline(
            open_time=next_time,
            open=extra.open,
            high=extra.high,
            low=extra.low,
            close=extra.close,
            volume=extra.volume,
        )
        intent = strategy(klines + [extra])
        assert intent is not None
        assert strategy.open_position is not None


# ---------------------------------------------------------------------------
# ObvTrendTrainable / fit()
# ---------------------------------------------------------------------------


class TestFit:
    def _trainable(self, tmp_path, **overrides: object) -> ObvTrendTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-obv-trend",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            obv_ma_period=4,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return ObvTrendTrainable(**kwargs)

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_logs_the_fixed_obv_ma_period_in_params(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-2")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        record = next(r for r in records if r.get("parent_run_id") == "parent-2")
        assert record["params"]["obv_ma_period"] == 4

    def test_fit_returns_fresh_strategy_bound_to_the_fixed_obv_ma_period(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {}, parent_run_id="parent-3")
        assert isinstance(result, ObvTrendStrategy)
        assert result.bars_seen == 0
        assert result.obv_ma_period == 4

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.obv_trend.run_backtest", spy)
        trainable.fit(klines, {}, parent_run_id="parent-4")
        assert all(kid == id(klines) for kid in seen_ids)

    def test_default_obv_ma_period_used_when_trainable_constructed_without_override(self, tmp_path):
        trainable = ObvTrendTrainable(
            strategy_id="test-obv-trend-default",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = trainable.fit(_flat_klines(30), {}, parent_run_id="parent-5")
        assert result.obv_ma_period == DEFAULT_OBV_MA_PERIOD
