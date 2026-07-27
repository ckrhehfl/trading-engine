"""Tests for `python/research/strategies/mean_reversion.py` -- Strategy
Research Task K's regime-gated Bollinger-Band mean-reversion strategy
("candidate B" of the momentum/mean-reversion blend). See CLAUDE.md's
"Strategy Research Methodology" section and `.planning/sr-k-mean-reversion-
and-blend.md` for the full design context.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/mean_reversion.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.mean_reversion import (
    DEFAULT_BOLLINGER_K,
    DEFAULT_BOLLINGER_PERIOD,
    BollingerBands,
    MeanReversionStrategy,
    MeanReversionTrainable,
    _bollinger_signal,
)
from research.strategies.regime_weighting import DEFAULT_ADX_HIGH_THRESHOLD, DEFAULT_ADX_LOW_THRESHOLD
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

_ADX_MODULE_PATH = "research.strategies.mean_reversion.AverageDirectionalIndex.update"
_VOL_MODULE_PATH = "research.strategies.mean_reversion.RollingRealizedVolatility.update"
_ATR_MODULE_PATH = "research.strategies.mean_reversion.AverageTrueRange.update"


def _hourly_kline(i: int, close: Decimal, base_time: datetime = BASE_TIME) -> Kline:
    return Kline(
        open_time=base_time + timedelta(hours=i),
        open=close,
        high=close + Decimal("0.5"),
        low=close - Decimal("0.5"),
        close=close,
        volume=Decimal("1"),
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


_TEST_BOLLINGER_PERIOD = 10


def _strategy(**overrides: object) -> MeanReversionStrategy:
    kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT",
        bollinger_period=_TEST_BOLLINGER_PERIOD,
        atr_period=3,
        adx_period=2,
        vol_period=2,
    )
    kwargs.update(overrides)
    return MeanReversionStrategy(**kwargs)


def _force_full_vol_readiness(monkeypatch: pytest.MonkeyPatch, adx_value: str) -> None:
    monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal(adx_value))
    monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))


def _flip_fire_klines(bollinger_period: int = _TEST_BOLLINGER_PERIOD, base_time: datetime = BASE_TIME) -> list[Kline]:
    """Bollinger-Band edge-triggering requires an established nonzero
    `_signal_state` before a flip can fire (same "last established regime"
    convention as the momentum strategies' crossover-sign tracking) -- a
    strategy's very FIRST-ever band breach never fires an entry (there is
    no prior state to differ from), only a later breach in the OPPOSITE
    direction does. This scenario seeds an oversold reading first (does NOT
    fire), lets the window fully roll the seed back out to a clean flat
    baseline, then flips to an overbought reading (DOES fire -- a SHORT
    entry, since it differs from the established oversold state).

    `period`/`k=2` (`DEFAULT_BOLLINGER_K`) are chosen so a single sharp bar
    following a flat `period`-bar run always breaches a band regardless of
    the move's magnitude: for a window of `(period - 1)` identical values
    plus one new value `x`, the breach condition reduces (by direct
    algebra on the sample mean/stdev) to `1/period + k/sqrt(period) < 1`,
    independent of `x` -- true at `period=10, k=2`
    (`0.1 + 2/sqrt(10) ~= 0.7325 < 1`), false at the small periods (e.g. 4)
    used in some of this module's warmup-only tests, where a single-bar
    move can never breach (`0.25 + 2/2 = 1.25 > 1`). See
    `.planning/sr-k-mean-reversion-and-blend.md` for the full derivation.
    """
    closes = (
        [Decimal(100) for _ in range(bollinger_period)]
        + [Decimal(50)]
        + [Decimal(100) for _ in range(bollinger_period + 5)]
        + [Decimal(200)]
    )
    return [_hourly_kline(i, c, base_time) for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------
# BollingerBands
# ---------------------------------------------------------------------------


class TestBollingerBands:
    def test_rejects_period_below_two(self):
        with pytest.raises(ValueError, match="period"):
            BollingerBands(period=1)

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError, match="k"):
            BollingerBands(period=4, k=Decimal("0"))

    def test_returns_none_during_warmup(self):
        bands = BollingerBands(period=3)
        assert bands.update(_hourly_kline(0, Decimal("100"))) is None
        assert bands.update(_hourly_kline(1, Decimal("100"))) is None

    def test_hand_computed_bands(self):
        # closes = [100, 102, 98] -> mean = 100, sample stdev (ddof=1) of
        # [100,102,98]: deviations [0,2,-2], sq sum=8, /2=4, sqrt=2.
        # k=2 -> band width = 4 -> upper=104, lower=96.
        bands = BollingerBands(period=3, k=Decimal("2"))
        assert bands.update(_hourly_kline(0, Decimal("100"))) is None
        assert bands.update(_hourly_kline(1, Decimal("102"))) is None
        result = bands.update(_hourly_kline(2, Decimal("98")))
        assert result is not None
        middle, upper, lower = result
        assert middle == Decimal("100")
        assert upper == Decimal("104")
        assert lower == Decimal("96")

    def test_zero_variance_yields_zero_width_bands(self):
        bands = BollingerBands(period=3, k=Decimal("2"))
        bands.update(_hourly_kline(0, Decimal("50")))
        bands.update(_hourly_kline(1, Decimal("50")))
        result = bands.update(_hourly_kline(2, Decimal("50")))
        assert result == (Decimal("50"), Decimal("50"), Decimal("50"))

    def test_lookahead_safety_value_at_bar_k_unaffected_by_future_bars(self):
        prefix = [_hourly_kline(i, Decimal(100 + i)) for i in range(4)]
        future = [_hourly_kline(4, Decimal("500")), _hourly_kline(5, Decimal("1"))]

        bands_prefix_only = BollingerBands(period=3)
        value_at_prefix_end = None
        for bar in prefix:
            value_at_prefix_end = bands_prefix_only.update(bar)

        bands_with_future = BollingerBands(period=3)
        for bar in prefix + future:
            value_at_bar_k = bands_with_future.update(bar)
            if bar is prefix[-1]:
                assert value_at_bar_k == value_at_prefix_end

    def test_default_period_and_k(self):
        assert DEFAULT_BOLLINGER_PERIOD == 20
        assert DEFAULT_BOLLINGER_K == Decimal("2")


# ---------------------------------------------------------------------------
# _bollinger_signal
# ---------------------------------------------------------------------------


class TestBollingerSignal:
    def test_close_below_lower_band_is_oversold_long_signal(self):
        assert _bollinger_signal(Decimal("95"), lower=Decimal("96"), upper=Decimal("104")) == 1

    def test_close_above_upper_band_is_overbought_short_signal(self):
        assert _bollinger_signal(Decimal("105"), lower=Decimal("96"), upper=Decimal("104")) == -1

    def test_close_inside_bands_is_no_signal(self):
        assert _bollinger_signal(Decimal("100"), lower=Decimal("96"), upper=Decimal("104")) == 0

    def test_close_exactly_on_lower_band_is_no_signal(self):
        assert _bollinger_signal(Decimal("96"), lower=Decimal("96"), upper=Decimal("104")) == 0

    def test_close_exactly_on_upper_band_is_no_signal(self):
        assert _bollinger_signal(Decimal("104"), lower=Decimal("96"), upper=Decimal("104")) == 0


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


# ---------------------------------------------------------------------------
# Inverted regime gating -- the core, easy-to-get-backwards design point
# ---------------------------------------------------------------------------


class TestInvertedRegimeGating:
    """Explicit verification that mean-reversion gets MORE weight in
    low-ADX (ranging) conditions and LESS weight in high-ADX (trending)
    conditions -- the OPPOSITE direction from
    `ensemble_momentum.py`/`single_lookback_momentum.py`'s use of the same
    `compute_regime_weight` function. A bug here (forgetting the `1 -`
    inversion) would be easy to introduce silently, since the rest of the
    strategy's structure is otherwise identical to the momentum strategies
    -- these tests exist specifically to catch that.
    """

    def test_adx_at_low_threshold_gives_full_conviction_where_momentum_would_be_fully_suppressed(
        self, monkeypatch
    ):
        # At ADX <= low threshold (20, default), ensemble_momentum's
        # compute_regime_weight(adx, low=20, high=25) == 0 (fully
        # suppressed for momentum -- see test_ensemble_momentum.py's
        # test_adx_at_or_below_low_threshold_suppresses_entry_entirely).
        # For mean-reversion, the SAME ADX reading must instead give FULL
        # (1.0) conviction: 1 - 0 == 1.
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("15"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _flip_fire_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        position = strategy.open_position
        assert position is not None, "expected mean-reversion to enter at full conviction under low ADX"
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        # regime_weight=1, vol_scalar=0.20 target / ... just confirm not
        # suppressed to zero and matches the full (unweighted-by-regime)
        # base*vol_scalar formula exactly.
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        assert position.quantity == base_quantity * Decimal("1") * vol_scalar

    def test_adx_at_high_threshold_suppresses_entry_entirely_where_momentum_would_be_full_conviction(
        self, monkeypatch
    ):
        # At ADX >= high threshold (25, default), momentum reads full (1.0)
        # conviction (see test_ensemble_momentum.py's
        # _force_full_conviction fixture, ADX=30). For mean-reversion, the
        # SAME reading must instead fully suppress: 1 - 1 == 0 -> no entry.
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _flip_fire_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is None

    def test_mid_ramp_adx_gives_inverted_weight_vs_what_momentum_would_compute(self, monkeypatch):
        # ADX=21.25, default low=20/high=25 -> momentum's own
        # compute_regime_weight would read (21.25-20)/(25-20) = 0.25.
        # Mean-reversion must read the complement: 1 - 0.25 = 0.75.
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("21.25"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _flip_fire_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        expected = base_quantity * Decimal("0.75") * vol_scalar
        assert position.quantity == expected

    def test_default_adx_thresholds_match_regime_weighting_module(self):
        # Asserts the actual constructor DEFAULT (not merely that passing
        # these values explicitly doesn't raise) equals regime_weighting's
        # own defaults -- the meaningful claim this test's name makes.
        import inspect

        signature = inspect.signature(MeanReversionStrategy.__init__)
        assert signature.parameters["adx_low"].default == DEFAULT_ADX_LOW_THRESHOLD
        assert signature.parameters["adx_high"].default == DEFAULT_ADX_HIGH_THRESHOLD


# ---------------------------------------------------------------------------
# Risk management composition (ATR stop/target, fixed-fractional sizing)
# ---------------------------------------------------------------------------


class TestRiskManagement:
    def _run_to_first_entry(self, strategy: MeanReversionStrategy) -> tuple[list[Kline], int]:
        klines = _flip_fire_klines()
        entry_index = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None and strategy.open_position is not None:
                entry_index = i
                break
        assert entry_index is not None, "expected an entry to fire in this scenario"
        return klines, entry_index

    def test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "15")  # low ADX -> full reversion conviction
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
        _force_full_vol_readiness(monkeypatch, "15")
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
        _force_full_vol_readiness(monkeypatch, "15")
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
    def test_no_signal_until_bollinger_window_is_full(self, monkeypatch):
        _force_full_vol_readiness(monkeypatch, "15")
        strategy = _strategy(bollinger_period=10)
        closes = [Decimal(100 + i) for i in range(9)]  # 9 < period (10)
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)


# ---------------------------------------------------------------------------
# MeanReversionTrainable / fit()
# ---------------------------------------------------------------------------


class TestFit:
    def _trainable(self, tmp_path, **overrides: object) -> MeanReversionTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-mean-reversion",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            bollinger_period=4,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return MeanReversionTrainable(**kwargs)

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_logs_the_fixed_bollinger_period_in_params(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-2")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        record = next(r for r in records if r.get("parent_run_id") == "parent-2")
        assert record["params"]["bollinger_period"] == 4

    def test_fit_returns_fresh_strategy_bound_to_the_fixed_bollinger_period(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {}, parent_run_id="parent-3")
        assert isinstance(result, MeanReversionStrategy)
        assert result.bars_seen == 0
        assert result.bollinger_period == 4

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.mean_reversion.run_backtest", spy)
        trainable.fit(klines, {}, parent_run_id="parent-4")
        assert all(kid == id(klines) for kid in seen_ids)

    def test_default_bollinger_period_used_when_trainable_constructed_without_override(self, tmp_path):
        trainable = MeanReversionTrainable(
            strategy_id="test-mean-reversion-default",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = trainable.fit(_flat_klines(30), {}, parent_run_id="parent-5")
        assert result.bollinger_period == DEFAULT_BOLLINGER_PERIOD
