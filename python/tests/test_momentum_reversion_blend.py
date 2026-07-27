"""Tests for `python/research/strategies/momentum_reversion_blend.py` --
Strategy Research Task K's regime-adaptive blend of the existing momentum
ensemble signal (`ensemble_momentum.py`, reused unmodified) and the new
mean-reversion signal (`mean_reversion.py`, this task's "candidate B"),
combined via the SAME continuous ADX regime weight already computed
(`regime_weighting.compute_regime_weight`) -- momentum weighted by the
regime weight (high when trending), mean-reversion by its complement (high
when ranging). See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-k-mean-reversion-and-blend.md` for the full design context.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/momentum_reversion_blend.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.momentum_reversion_blend import (
    DEFAULT_BOLLINGER_K,
    DEFAULT_BOLLINGER_PERIOD,
    DEFAULT_LOOKBACK_PAIRS,
    MomentumReversionBlendStrategy,
    MomentumReversionBlendTrainable,
    _blend_signals,
)
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

_ADX_MODULE_PATH = "research.strategies.momentum_reversion_blend.AverageDirectionalIndex.update"
_VOL_MODULE_PATH = "research.strategies.momentum_reversion_blend.RollingRealizedVolatility.update"

_TEST_LOOKBACK_PAIRS = ((2, 3), (4, 6), (8, 10))  # max_slow=10, matches _TEST_BOLLINGER_PERIOD
_TEST_BOLLINGER_PERIOD = 10


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


def _strategy(**overrides: object) -> MomentumReversionBlendStrategy:
    kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT",
        lookback_pairs=_TEST_LOOKBACK_PAIRS,
        bollinger_period=_TEST_BOLLINGER_PERIOD,
        atr_period=3,
        adx_period=2,
        vol_period=2,
    )
    kwargs.update(overrides)
    return MomentumReversionBlendStrategy(**kwargs)


def _momentum_flip_klines() -> list[Kline]:
    """A decline then a rally, warming all 3 lookback pairs
    (`_TEST_LOOKBACK_PAIRS`, max_slow=10) and staying comfortably inside a
    `_TEST_BOLLINGER_PERIOD`-period/k=2 Bollinger band throughout (a
    gradual, moderate move, not a sharp single-bar jump) -- so the momentum
    ensemble's own combined crossover sign flips (decline establishes
    bearish, rally flips bullish and fires), while the reversion signal
    stays 0 (no band breach) the entire time. Same price-path *shape* as
    `test_ensemble_momentum.py`'s own crossover-flip scenario.
    """
    decline = [Decimal(200 - 3 * i) for i in range(14)]
    rally = [decline[-1] + Decimal(5 * i) for i in range(1, 10)]
    closes = decline + rally
    return [_hourly_kline(i, c) for i, c in enumerate(closes)]


def _reversion_flip_klines(period: int = _TEST_BOLLINGER_PERIOD) -> list[Kline]:
    """Same construction as `test_mean_reversion.py::_flip_fire_klines`
    (seed an oversold reading, let it roll out of the window, then flip to
    an overbought reading that fires) -- duplicated locally rather than
    imported across test modules, matching this codebase's own established
    "duplicate small test fixtures" convention (see e.g.
    `ma_crossover.py`'s `_data_range` docstring precedent, applied to test
    code here). The flat stretches keep the momentum ensemble's own
    crossover sign at (or very near) 0 throughout, so this scenario
    isolates the reversion signal's contribution to the blend.
    """
    closes = (
        [Decimal(100) for _ in range(period)]
        + [Decimal(50)]
        + [Decimal(100) for _ in range(period + 5)]
        + [Decimal(200)]
    )
    return [_hourly_kline(i, c) for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------
# _blend_signals -- the blend's core combination arithmetic
# ---------------------------------------------------------------------------


class TestBlendSignals:
    def test_both_bullish_agree_full_strength_regardless_of_weight_split(self):
        assert _blend_signals(1, 1, Decimal("0.7")) == Decimal("1")

    def test_both_bearish_agree_full_strength_regardless_of_weight_split(self):
        assert _blend_signals(-1, -1, Decimal("0.3")) == Decimal("-1")

    def test_disagreement_at_even_split_cancels_to_zero(self):
        assert _blend_signals(1, -1, Decimal("0.5")) == Decimal("0")

    def test_disagreement_favors_the_higher_weighted_side(self):
        # regime_weight=0.8 -> momentum favored: 0.8*1 + 0.2*(-1) = 0.6
        assert _blend_signals(1, -1, Decimal("0.8")) == Decimal("0.6")

    def test_disagreement_favors_reversion_when_regime_weight_is_low(self):
        # regime_weight=0.2 -> reversion favored: 0.2*1 + 0.8*(-1) = -0.6
        assert _blend_signals(1, -1, Decimal("0.2")) == Decimal("-0.6")

    def test_momentum_flat_reversion_only_scaled_by_complement(self):
        assert _blend_signals(0, 1, Decimal("0.9")) == Decimal("0.1")

    def test_reversion_flat_momentum_only_scaled_by_weight(self):
        assert _blend_signals(-1, 0, Decimal("0.9")) == Decimal("-0.9")

    def test_both_flat_is_zero(self):
        assert _blend_signals(0, 0, Decimal("0.5")) == Decimal("0")

    def test_regime_weight_zero_ignores_momentum_entirely(self):
        assert _blend_signals(1, -1, Decimal("0")) == Decimal("-1")

    def test_regime_weight_one_ignores_reversion_entirely(self):
        assert _blend_signals(1, -1, Decimal("1")) == Decimal("1")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.open_position is None
        assert strategy.bars_seen == 0

    def test_rejects_fewer_than_three_lookback_pairs(self):
        with pytest.raises(ValueError, match="at least 3"):
            _strategy(lookback_pairs=((2, 4), (3, 6)))

    def test_default_lookback_pairs_reused_from_ensemble_momentum(self):
        from research.strategies.ensemble_momentum import DEFAULT_LOOKBACK_PAIRS as ENSEMBLE_DEFAULT

        assert DEFAULT_LOOKBACK_PAIRS == ENSEMBLE_DEFAULT

    def test_default_bollinger_period_and_k_match_mean_reversion_module(self):
        from research.strategies.mean_reversion import DEFAULT_BOLLINGER_K as MR_K
        from research.strategies.mean_reversion import DEFAULT_BOLLINGER_PERIOD as MR_PERIOD

        assert DEFAULT_BOLLINGER_PERIOD == MR_PERIOD
        assert DEFAULT_BOLLINGER_K == MR_K


# ---------------------------------------------------------------------------
# Warmup: requires BOTH momentum and reversion signals ready
# ---------------------------------------------------------------------------


class TestWarmupRequiresBothSignalsSimultaneously:
    def test_no_signal_while_only_momentum_is_warmed_up(self, monkeypatch):
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        # bollinger_period (20) longer than momentum's max_slow (10) --
        # momentum alone would be ready well before reversion is.
        strategy = _strategy(bollinger_period=20)
        closes = [Decimal(100 + i) for i in range(15)]  # >= max_slow(10), < bollinger_period(20)
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)
        assert strategy.open_position is None


# ---------------------------------------------------------------------------
# Regime-adaptive behavior: extremes reduce to (near-)pure sub-strategies
# ---------------------------------------------------------------------------


class TestRegimeAdaptiveBehavior:
    def test_high_adx_trending_behaves_like_momentum_alone(self, monkeypatch):
        """regime_weight=1 at ADX>=high -- the reversion term is multiplied
        by (1-1)=0 and drops out entirely, so the blend's entry quantity
        must exactly match the unweighted (full-conviction) ATR-sized
        formula, the same way `EnsembleMomentumStrategy` behaves at full
        conviction.
        """
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _momentum_flip_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
            if strategy.open_position is not None:
                break
        position = strategy.open_position
        assert position is not None, "expected the momentum-driven flip to fire under high ADX"
        assert position.side == Side.LONG  # the rally leg -- see _momentum_flip_klines
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        assert position.quantity == base_quantity * Decimal("1") * vol_scalar

    def test_low_adx_ranging_behaves_like_reversion_alone(self, monkeypatch):
        """regime_weight=0 at ADX<=low -- the momentum term is multiplied
        by 0 and drops out entirely, so the blend's entry direction/
        quantity must exactly match the unweighted mean-reversion formula.
        """
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("15"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _reversion_flip_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        position = strategy.open_position
        assert position is not None, "expected the reversion-driven flip to fire under low ADX"
        assert position.side == Side.SHORT  # overbought flip -- see _reversion_flip_klines
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        assert position.quantity == base_quantity * Decimal("1") * vol_scalar

    def test_intermediate_adx_with_disagreeing_signals_sizes_by_blended_magnitude_only(self, monkeypatch):
        """Both prior tests above only exercise the ADX EXTREMES, where
        `abs(blended_strength)` always equals 1 regardless of whether the
        double-application bug (`compute_regime_weight` applied a second
        time on top of `blended_strength`, which already fully incorporates
        it) is present or not -- neither would catch a regression there.
        This test forces a genuine intermediate regime_weight (0.75, at
        ADX=23.75 under the default 20/25 thresholds) with the two
        sub-signals DISAGREEING (reversion forced bearish via monkeypatch;
        momentum flips bullish on the rally leg of `_momentum_flip_klines`),
        so `blended_strength = 0.75*(+1) + 0.25*(-1) = 0.5` -- a real
        partial-conviction value neither 0 nor 1. If `compute_regime_weight`
        were (incorrectly) applied a second time, the resulting quantity
        would be `base * 0.75 * 0.5 * vol_scalar`, not `base * 0.5 *
        vol_scalar` -- this test fails under that regression.
        """
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("23.75"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        monkeypatch.setattr(
            "research.strategies.momentum_reversion_blend._bollinger_signal",
            lambda close, lower, upper: -1,
        )
        strategy = _strategy()
        klines = _momentum_flip_klines()
        for i in range(len(klines)):
            strategy(klines[: i + 1])
            if strategy.open_position is not None:
                break
        position = strategy.open_position
        assert position is not None, "expected the momentum rally to flip the blended sign positive"
        assert position.side == Side.LONG
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        from research.strategies.volatility_targeting import compute_vol_scalar

        vol_scalar = compute_vol_scalar(Decimal("0.20"))
        assert position.quantity == base_quantity * Decimal("0.5") * vol_scalar


# ---------------------------------------------------------------------------
# Exit / order shape
# ---------------------------------------------------------------------------


class TestExitAndOrderShape:
    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self, monkeypatch):
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _momentum_flip_klines()
        entry_index = None
        for i in range(len(klines)):
            strategy(klines[: i + 1])
            if strategy.open_position is not None:
                entry_index = i
                break
        assert entry_index is not None
        position_before = strategy.open_position
        assert position_before is not None
        quantity_before = position_before.quantity
        side_before = position_before.side
        stop_price = position_before.stop_price

        prefix = klines[: entry_index + 1]
        next_time = prefix[-1].open_time + timedelta(hours=1)
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
        intent = strategy(prefix + [breach])
        assert intent is not None
        assert intent.quantity == quantity_before
        assert intent.side != side_before
        assert strategy.open_position is None

    def test_entry_intent_has_1h_signal_timeframe_and_guarded_market(self, monkeypatch):
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        klines = _momentum_flip_klines()
        intent = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if strategy.open_position is not None:
                break
        assert intent is not None
        assert intent.signal_timeframe == "1h"
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.limit_price is None


# ---------------------------------------------------------------------------
# MomentumReversionBlendTrainable / fit()
# ---------------------------------------------------------------------------


class TestFit:
    def _trainable(self, tmp_path, **overrides: object) -> MomentumReversionBlendTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-momentum-reversion-blend",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            lookback_pairs=_TEST_LOOKBACK_PAIRS,
            bollinger_period=_TEST_BOLLINGER_PERIOD,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return MomentumReversionBlendTrainable(**kwargs)

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_returns_fresh_strategy_bound_to_fixed_config(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {}, parent_run_id="parent-2")
        assert isinstance(result, MomentumReversionBlendStrategy)
        assert result.bars_seen == 0
        assert result.lookback_pairs == _TEST_LOOKBACK_PAIRS

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.momentum_reversion_blend.run_backtest", spy)
        trainable.fit(klines, {}, parent_run_id="parent-3")
        assert all(kid == id(klines) for kid in seen_ids)

    def test_default_config_used_when_trainable_constructed_without_override(self, tmp_path):
        trainable = MomentumReversionBlendTrainable(
            strategy_id="test-blend-default",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = trainable.fit(_flat_klines(30), {}, parent_run_id="parent-4")
        assert result.lookback_pairs == DEFAULT_LOOKBACK_PAIRS
