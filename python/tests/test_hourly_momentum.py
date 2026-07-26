"""Tests for `python/research/strategies/hourly_momentum.py` -- Strategy
Research Task F, Part 2 (a native-1h momentum variant, single-tier
fast/slow SMA crossover plus the same risk management as Part 1's
`regime_momentum_risk_managed.py`). See CLAUDE.md's "Strategy Research
Operational Design" and `.planning/sr-f-risk-management-and-1h-variant.md`
for the full design and this task's honest, unembellished real
walk-forward results against real 1h BingX data.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/hourly_momentum.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.hourly_momentum import (
    DEFAULT_STARTING_EQUITY,
    HourlyMomentumStrategy,
    HourlyMomentumTrainable,
)
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from research.walkforward import run_walk_forward
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


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


def _strategy(**overrides: object) -> HourlyMomentumStrategy:
    kwargs: dict[str, Any] = dict(fast=2, slow=4, symbol="BTC-USDT", atr_period=3)
    kwargs.update(overrides)
    return HourlyMomentumStrategy(**kwargs)


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


class TestEdgeTriggeredCrossover:
    def test_no_signal_during_warmup(self):
        strategy = _strategy(fast=2, slow=4)
        closes = [Decimal(c) for c in [100, 101, 102]]  # only 3 < slow=4
        for i, close in enumerate(closes):
            intent = strategy(_flat_klines(0) + [_hourly_kline(i, close)])
        assert intent is None

    def test_baseline_regime_establishment_fires_no_signal(self):
        # First bar where fast/slow SMA relationship becomes defined is a
        # baseline -- nothing to have crossed *from* yet.
        strategy = _strategy(fast=2, slow=4)
        closes = [Decimal(c) for c in [97, 98, 99, 100]]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)

    def test_fresh_bullish_cross_fires_long(self):
        # fast=2, slow=3. Bars: 100,100,100 (baseline, sign 0 -> flat/tie,
        # not yet a defined sign), then a genuine rise establishes +1
        # (baseline, no signal), a dip flips to -1 (fresh cross, fires
        # SHORT since no regime gating in this single-tier design), then
        # a rise flips back to +1 (fresh cross, fires LONG).
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        closes = [Decimal(c) for c in [100, 100, 100, 105, 108, 95, 90, 120, 130]]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        long_intents = [i for i in intents if i is not None and i.side == Side.LONG]
        short_intents = [i for i in intents if i is not None and i.side == Side.SHORT]
        assert len(long_intents) >= 1
        assert len(short_intents) >= 1

    def test_no_signal_while_sign_stays_constant_across_many_bars(self):
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        closes = [Decimal(100 + i) for i in range(20)]  # smooth monotonic ramp
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        fired = [i for i in intents if i is not None]
        # At most one entry fires (the baseline-then-hold ramp never
        # produces a second, fresh cross) -- edge-triggered, not
        # level-triggered.
        assert len(fired) <= 1


class TestRiskManagement:
    def _run_to_first_entry(self, strategy: HourlyMomentumStrategy) -> tuple[list[Kline], int]:
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

    def test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward(self):
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        klines, entry_index = self._run_to_first_entry(strategy)
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

    def test_entry_quantity_matches_fixed_fractional_sizing_formula(self):
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        klines, entry_index = self._run_to_first_entry(strategy)
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        expected_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        assert position.quantity == expected_quantity

    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self):
        strategy = _strategy(fast=2, slow=3, atr_period=2)
        klines, entry_index = self._run_to_first_entry(strategy)
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
        assert intent.side != side_before  # opposite side: flattening, not flipping
        assert strategy.open_position is None

    def test_entry_intent_has_1h_signal_timeframe(self):
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


class TestFitGridSearch:
    def _trainable(self, tmp_path, **overrides: object) -> HourlyMomentumTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-hourly-momentum",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return HourlyMomentumTrainable(**kwargs)

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
        assert isinstance(result, HourlyMomentumStrategy)
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

        monkeypatch.setattr("research.strategies.hourly_momentum.run_backtest", spy)
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


class TestRealWalkForwardIntegration:
    def test_run_walk_forward_end_to_end_with_a_short_synthetic_series(self, tmp_path):
        trainable = HourlyMomentumTrainable(
            strategy_id="test-hourly-momentum-e2e",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        prices = [Decimal(100) + (Decimal(i) % 20) for i in range(400)]
        klines = [_hourly_kline(i, p) for i, p in enumerate(prices)]
        result = run_walk_forward(
            klines,
            trainable,
            "test-hourly-momentum-e2e",
            "v1",
            {"candidates": [(3, 8), (4, 10)]},
            train_bars=100,
            validate_bars=50,
            step_bars=50,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
