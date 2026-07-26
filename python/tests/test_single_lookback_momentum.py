"""Tests for `python/research/strategies/single_lookback_momentum.py` --
Strategy Research Task H's single-best-lookback baseline/control strategy
(native-1h SMA crossover + ADX-based continuous regime weighting + real
volatility targeting, on top of the existing ATR risk-management layer).
See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-h-ensemble-regime-voltargeting.md` for the full design and
this task's honest real walk-forward results (this vs.
`ensemble_momentum.py`).

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/single_lookback_momentum.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from research.strategies.single_lookback_momentum import (
    SingleLookbackMomentumStrategy,
    SingleLookbackMomentumTrainable,
)
from research.walkforward import run_walk_forward
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

_ADX_MODULE_PATH = "research.strategies.single_lookback_momentum.AverageDirectionalIndex.update"
_VOL_MODULE_PATH = "research.strategies.single_lookback_momentum.RollingRealizedVolatility.update"


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


def _strategy(**overrides: object) -> SingleLookbackMomentumStrategy:
    kwargs: dict[str, Any] = dict(
        fast=2, slow=4, symbol="BTC-USDT", atr_period=3, adx_period=2, vol_period=2
    )
    kwargs.update(overrides)
    return SingleLookbackMomentumStrategy(**kwargs)


def _force_full_conviction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patches ADX to a fixed strongly-trending reading (30, above the
    default high threshold of 25 -> regime_weight=1) and realized vol to
    exactly the default target (0.20 -> vol_scalar=1) at the class level,
    so `final_quantity == base_quantity * 1 * 1 == base_quantity` --
    isolating crossover/exit/ATR-sizing tests from the regime/vol
    composition layers, which get their own dedicated tests below.
    """
    monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
    monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))


class TestConstruction:
    def test_rejects_non_positive_windows(self):
        with pytest.raises(ValueError, match="fast/slow"):
            _strategy(fast=0, slow=4)
        with pytest.raises(ValueError, match="fast/slow"):
            _strategy(fast=2, slow=0)

    def test_rejects_fast_not_strictly_less_than_slow(self):
        with pytest.raises(ValueError, match="fast window"):
            _strategy(fast=4, slow=4)

    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.open_position is None
        assert strategy.bars_seen == 0

    def test_invalid_adx_period_propagates_from_the_shared_calculator(self):
        with pytest.raises(ValueError, match="period"):
            _strategy(adx_period=0)

    def test_invalid_vol_period_propagates_from_the_shared_calculator(self):
        with pytest.raises(ValueError, match="period"):
            _strategy(vol_period=1)


class TestEdgeTriggeredCrossover:
    def test_no_signal_during_warmup(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=4)
        closes = [Decimal(c) for c in [100, 101, 102]]  # only 3 < slow=4
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)

    def test_baseline_regime_establishment_fires_no_signal(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=4)
        closes = [Decimal(c) for c in [97, 98, 99, 100]]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)

    def test_fresh_crosses_fire_with_full_conviction(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        closes = [Decimal(c) for c in [100, 100, 100, 105, 108, 95, 90, 120, 130]]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        long_intents = [i for i in intents if i is not None and i.side == Side.LONG]
        short_intents = [i for i in intents if i is not None and i.side == Side.SHORT]
        assert len(long_intents) >= 1
        assert len(short_intents) >= 1

    def test_no_signal_while_sign_stays_constant_across_many_bars(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        closes = [Decimal(100 + i) for i in range(20)]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        fired = [i for i in intents if i is not None]
        assert len(fired) <= 1


class TestRiskManagementWithFullConviction:
    """Confirms the ATR stop/target/base-sizing formula (inherited
    unchanged from `research.strategies.risk_management`) still holds
    exactly when regime_weight=1 and vol_scalar=1 (full conviction) --
    i.e. the new layers are purely multiplicative and don't otherwise
    disturb the existing risk-management math.
    """

    def _run_to_first_entry(
        self, strategy: SingleLookbackMomentumStrategy
    ) -> tuple[list[Kline], int]:
        closes = [Decimal(c) for c in [100, 100, 100, 105, 108, 95, 90, 120, 130, 140]]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        entry_index = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None and strategy.open_position is not None:
                entry_index = i
                break
        assert entry_index is not None, "expected some entry to fire in this scenario"
        return klines, entry_index

    def test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
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

    def test_entry_quantity_matches_unscaled_fixed_fractional_formula(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        self._run_to_first_entry(strategy)
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        expected_base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        assert position.quantity == expected_base_quantity

    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
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

    def test_entry_intent_has_1h_signal_timeframe(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        klines, entry_index = self._run_to_first_entry(strategy)
        fresh = _strategy(fast=2, slow=3, atr_period=2)
        intent = None
        for i in range(entry_index + 1):
            intent = fresh(klines[: i + 1])
        assert intent is not None
        assert intent.signal_timeframe == "1h"
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.limit_price is None


class TestRegimeAndVolatilityScaling:
    """The actual point of this task: quantity is scaled continuously by
    both the ADX regime weight and the volatility-target scalar, and
    either factor going to zero/None fully suppresses an otherwise-valid
    crossover signal.
    """

    _CLOSES = [Decimal(c) for c in [100, 100, 100, 105, 108, 95, 90, 120, 130, 140]]

    def _run_to_first_entry(self, strategy: SingleLookbackMomentumStrategy) -> list[Kline]:
        klines = [_hourly_kline(i, c) for i, c in enumerate(self._CLOSES)]
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None and strategy.open_position is not None:
                return klines[: i + 1]
        return klines

    def _run_all(self, strategy: SingleLookbackMomentumStrategy) -> list[Kline]:
        klines = [_hourly_kline(i, c) for i, c in enumerate(self._CLOSES)]
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        return klines

    def test_entry_quantity_scaled_by_half_regime_weight_and_double_vol_scalar(self, monkeypatch):
        # ADX=22.5 is exactly the midpoint of the default [20, 25] ramp ->
        # regime_weight=0.5. realized_vol=0.10 vs the default 0.20 target
        # -> vol_scalar=2 (well within the default max_scalar=3).
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("22.5"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.10"))
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        self._run_to_first_entry(strategy)
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        expected = base_quantity * Decimal("0.5") * Decimal("2")
        assert position.quantity == expected

    def test_adx_at_or_below_low_threshold_suppresses_entry_entirely(self, monkeypatch):
        # ADX=5 (well below the default low threshold of 20) -> regime_weight=0
        # -> final_quantity == 0 -> no signal at all, even though the same
        # crossover would fire under full conviction (see
        # TestEdgeTriggeredCrossover.test_fresh_crosses_fire_with_full_conviction).
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("5"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        self._run_all(strategy)
        assert strategy.open_position is None

    def test_warmup_incomplete_realized_vol_suppresses_entry_entirely(self, monkeypatch):
        # RollingRealizedVolatility.update returning None (warmup) must
        # NOT be treated as "no scaling" (scalar=1) -- it forces a skip,
        # per compute_vol_scalar's documented None-in/None-out contract.
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: None)
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        self._run_all(strategy)
        assert strategy.open_position is None


class TestFitGridSearch:
    def _trainable(self, tmp_path, **overrides: object) -> SingleLookbackMomentumTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-single-lookback-momentum",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return SingleLookbackMomentumTrainable(**kwargs)

    def test_fit_logs_every_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {"candidates": [(3, 8), (4, 10)]}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 2
        assert {r["candidate_index"] for r in candidate_records} == {0, 1}
        assert all(r["total_candidates"] == 2 for r in candidate_records)

    def test_fit_returns_fresh_strategy_bound_to_winner(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {"candidates": [(3, 8)]}, parent_run_id="parent-2")
        assert isinstance(result, SingleLookbackMomentumStrategy)
        assert result.bars_seen == 0
        assert result.fast_window == 3
        assert result.slow_window == 8

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.single_lookback_momentum.run_backtest", spy)
        trainable.fit(klines, {"candidates": [(3, 8)]}, parent_run_id="parent-3")
        assert all(kid == id(klines) for kid in seen_ids)

    def test_fit_falls_back_to_first_candidate_when_every_candidate_has_zero_trades(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {"candidates": [(3, 8), (4, 10)]}, parent_run_id="parent-4")
        assert result.fast_window == 3
        assert result.slow_window == 8

    def test_fit_rejects_empty_candidate_list(self, tmp_path):
        trainable = self._trainable(tmp_path)
        with pytest.raises(ValueError, match="candidates"):
            trainable.fit(_flat_klines(10), {"candidates": []}, parent_run_id="parent-5")

    def test_fit_uses_default_candidate_grid_when_omitted(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(10)
        trainable.fit(klines, {}, parent_run_id="parent-6")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-6"]
        assert len(candidate_records) == 6  # len(DEFAULT_CANDIDATE_GRID)


class TestRealWalkForwardIntegration:
    def test_run_walk_forward_end_to_end_with_a_short_synthetic_series(self, tmp_path):
        trainable = SingleLookbackMomentumTrainable(
            strategy_id="test-single-lookback-momentum-e2e",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            atr_period=5,
            adx_period=5,
            vol_period=5,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        prices = [Decimal(100) + (Decimal(i) % 40) for i in range(700)]
        klines = [_hourly_kline(i, p) for i, p in enumerate(prices)]
        result = run_walk_forward(
            klines,
            trainable,
            "test-single-lookback-momentum-e2e",
            "v1",
            {"candidates": [(4, 12), (8, 24)]},
            train_bars=300,
            validate_bars=200,
            step_bars=200,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            bars_per_day=24,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1

    def test_run_walk_forward_with_sensitivity_extractor(self, tmp_path):
        trainable = SingleLookbackMomentumTrainable(
            strategy_id="test-single-lookback-momentum-sensitivity",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            atr_period=5,
            adx_period=5,
            vol_period=5,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        prices = [Decimal(100) + (Decimal(i) % 40) for i in range(400)]
        klines = [_hourly_kline(i, p) for i, p in enumerate(prices)]
        result = run_walk_forward(
            klines,
            trainable,
            "test-single-lookback-momentum-sensitivity",
            "v1",
            {"candidates": [(4, 12), (8, 24)]},
            train_bars=300,
            validate_bars=100,
            step_bars=100,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            bars_per_day=24,
            sensitivity_extractor=lambda s: (s.fast_window, s.slow_window),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
        assert all(fold.parameter_sensitivity is not None for fold in result.folds)
