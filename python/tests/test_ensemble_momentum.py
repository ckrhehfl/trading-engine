"""Tests for `python/research/strategies/ensemble_momentum.py` --
Strategy Research Task H's multi-lookback ensemble/"treatment" strategy
(majority-vote combination of 3 SMA-crossover pairs at different lookback
scales + the identical ADX-based continuous regime weighting and real
volatility targeting `single_lookback_momentum.py` uses). See CLAUDE.md's
"Strategy Research Methodology" section and `.planning/sr-h-ensemble-
regime-voltargeting.md` for the full design and this task's honest real
walk-forward results (this vs. `single_lookback_momentum.py`).

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/ensemble_momentum.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.ensemble_momentum import (
    DEFAULT_LOOKBACK_PAIRS,
    EnsembleMomentumStrategy,
    EnsembleMomentumTrainable,
    _combined_sign,
)
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from research.walkforward import run_walk_forward
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

_ADX_MODULE_PATH = "research.strategies.ensemble_momentum.AverageDirectionalIndex.update"
_VOL_MODULE_PATH = "research.strategies.ensemble_momentum.RollingRealizedVolatility.update"
_ATR_MODULE_PATH = "research.strategies.ensemble_momentum.AverageTrueRange.update"


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


def _strategy(**overrides: object) -> EnsembleMomentumStrategy:
    kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT",
        lookback_pairs=((2, 4), (3, 6), (4, 8)),
        atr_period=3,
        adx_period=2,
        vol_period=2,
    )
    kwargs.update(overrides)
    return EnsembleMomentumStrategy(**kwargs)


def _force_full_conviction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
    monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))


class TestCombinedSign:
    def test_all_three_agree_bullish(self):
        assert _combined_sign([1, 1, 1]) == 1

    def test_all_three_agree_bearish(self):
        assert _combined_sign([-1, -1, -1]) == -1

    def test_majority_two_of_three_bullish_overrides_the_minority(self):
        # Meaningfully different from any single constituent pair: the
        # lone dissenting pair's own sign (-1) is NOT what the ensemble
        # reports.
        assert _combined_sign([1, 1, -1]) == 1

    def test_majority_two_of_three_bearish_overrides_the_minority(self):
        assert _combined_sign([-1, -1, 1]) == -1

    def test_exact_disagreement_nets_to_no_signal(self):
        assert _combined_sign([1, -1, 0]) == 0

    def test_all_flat_nets_to_no_signal(self):
        assert _combined_sign([0, 0, 0]) == 0

    def test_one_bullish_two_flat_nets_bullish(self):
        assert _combined_sign([1, 0, 0]) == 1


class TestConstruction:
    def test_rejects_fewer_than_three_lookback_pairs(self):
        with pytest.raises(ValueError, match="at least 3"):
            _strategy(lookback_pairs=((2, 4), (3, 6)))

    def test_rejects_non_positive_windows_in_any_pair(self):
        with pytest.raises(ValueError, match="fast/slow"):
            _strategy(lookback_pairs=((2, 4), (0, 6), (4, 8)))

    def test_rejects_fast_not_strictly_less_than_slow_in_any_pair(self):
        with pytest.raises(ValueError, match="fast window"):
            _strategy(lookback_pairs=((2, 4), (6, 6), (4, 8)))

    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.open_position is None
        assert strategy.bars_seen == 0

    def test_default_lookback_pairs_are_short_medium_long(self):
        assert DEFAULT_LOOKBACK_PAIRS == ((4, 12), (12, 36), (24, 72))


class TestEnsembleSignalDiffersFromAnySingleConstituent:
    def test_combined_signal_can_diverge_from_the_short_pairs_own_sign(self, monkeypatch):
        """Construct a price path where the short lookback pair alone
        would read one direction, but the medium+long pairs together
        outvote it -- proving the ensemble's combined signal is genuinely
        not just "whatever the shortest/most-reactive pair says", which
        would make the whole exercise of combining 3 pairs pointless.
        """
        _force_full_conviction(monkeypatch)
        # short=(2,3), medium=(4,6), long=(8,12): a late, sharp downward
        # kick moves the short pair's own fast/slow SMA negative while
        # the medium/long pairs (built on a much longer, still-rising
        # trailing window) remain positive -- majority (medium+long)
        # keeps the ensemble bullish despite the short pair itself
        # flipping.
        strategy = _strategy(lookback_pairs=((2, 3), (4, 6), (8, 12)), atr_period=3)
        rising = [Decimal(100 + 2 * i) for i in range(14)]  # steady uptrend, warms all 3 pairs
        kick_down = [rising[-1] - Decimal(20)]  # a dip large enough to flip only the short pair
        closes = rising + kick_down
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]

        # Compute the short pair's OWN sign in isolation at the final bar.
        last_3 = closes[-3:]
        short_slow_sma = sum(last_3) / 3
        short_fast_sma = sum(closes[-2:]) / 2
        short_sign = 1 if short_fast_sma > short_slow_sma else (-1 if short_fast_sma < short_slow_sma else 0)
        assert short_sign == -1, "test setup assumption: the short pair alone should read bearish here"

        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        fired = [i for i in intents if i is not None]
        # The ensemble's own crossover-sign tracker should still read
        # bullish (established during the steady uptrend, majority-held
        # through the final dip) -- i.e. it did NOT fire a fresh bearish
        # signal just because the short pair flipped.
        assert all(i.side == Side.LONG for i in fired) or not fired


class TestWarmupRequiresAllPairsSimultaneously:
    def test_no_signal_until_the_longest_pairs_slow_window_is_full(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy(lookback_pairs=((2, 4), (3, 6), (4, 8)))
        closes = [Decimal(100 + i) for i in range(7)]  # 7 < max slow (8)
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)


class TestRiskManagementWithFullConviction:
    def _run_to_first_entry(self, strategy: EnsembleMomentumStrategy) -> tuple[list[Kline], int]:
        # Long enough to warm every pair in the default test lookback_pairs
        # ((2,4),(3,6),(4,8), max slow=8), then a sharp reversal to force a
        # fresh combined-sign cross.
        closes = [Decimal(100 + i) for i in range(10)] + [Decimal(80), Decimal(70), Decimal(60)]
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
        # ATR is also pinned to a clean value here (2) -- this test's
        # purpose is confirming the strategy wires whatever ATR it reads
        # into compute_stop_and_target correctly (the 1:2 ratio arithmetic
        # itself is already thoroughly unit-tested in isolation by
        # test_risk_management.py::TestComputeStopAndTarget), not
        # re-deriving an organically-computed ATR from this price path,
        # which can land on a many-digit repeating decimal (e.g. a
        # non-exact sum/period division) whose two independent roundings
        # (*1.5 and *3.0, each clamped to Decimal's 28-significant-digit
        # default context precision) then fail to compare as exactly 2x.
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

    def test_entry_quantity_matches_unscaled_fixed_fractional_formula(self, monkeypatch):
        _force_full_conviction(monkeypatch)
        strategy = _strategy()
        self._run_to_first_entry(strategy)
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        expected_base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        assert position.quantity == expected_base_quantity

    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self, monkeypatch):
        _force_full_conviction(monkeypatch)
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

    def test_entry_intent_has_1h_signal_timeframe(self, monkeypatch):
        _force_full_conviction(monkeypatch)
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


class TestRegimeAndVolatilityScaling:
    def test_entry_quantity_scaled_by_half_regime_weight_and_double_vol_scalar(self, monkeypatch):
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("22.5"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.10"))
        strategy = _strategy()
        closes = [Decimal(100 + i) for i in range(10)] + [Decimal(80), Decimal(70), Decimal(60)]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None and strategy.open_position is not None:
                break
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        base_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        expected = base_quantity * Decimal("0.5") * Decimal("2")
        assert position.quantity == expected

    def test_adx_at_or_below_low_threshold_suppresses_entry_entirely(self, monkeypatch):
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("5"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))
        strategy = _strategy()
        closes = [Decimal(100 + i) for i in range(10)] + [Decimal(80), Decimal(70), Decimal(60)]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is None

    def test_warmup_incomplete_realized_vol_suppresses_entry_entirely(self, monkeypatch):
        monkeypatch.setattr(_ADX_MODULE_PATH, lambda self, kline: Decimal("30"))
        monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: None)
        strategy = _strategy()
        closes = [Decimal(100 + i) for i in range(10)] + [Decimal(80), Decimal(70), Decimal(60)]
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.open_position is None


class TestFitNoGridSearch:
    def _trainable(self, tmp_path, **overrides: object) -> EnsembleMomentumTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-ensemble-momentum",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            lookback_pairs=((2, 4), (3, 6), (4, 8)),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return EnsembleMomentumTrainable(**kwargs)

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_logs_the_fixed_lookback_pairs_in_params(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-2")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        record = next(r for r in records if r.get("parent_run_id") == "parent-2")
        assert record["params"]["lookback_pairs"] == [[2, 4], [3, 6], [4, 8]]

    def test_fit_returns_fresh_strategy_bound_to_the_fixed_lookback_pairs(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {}, parent_run_id="parent-3")
        assert isinstance(result, EnsembleMomentumStrategy)
        assert result.bars_seen == 0
        assert result.lookback_pairs == ((2, 4), (3, 6), (4, 8))

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.ensemble_momentum.run_backtest", spy)
        trainable.fit(klines, {}, parent_run_id="parent-4")
        assert all(kid == id(klines) for kid in seen_ids)

    def test_default_lookback_pairs_used_when_trainable_constructed_without_override(self, tmp_path):
        trainable = EnsembleMomentumTrainable(
            strategy_id="test-ensemble-momentum-default",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = trainable.fit(_flat_klines(10), {}, parent_run_id="parent-5")
        assert result.lookback_pairs == DEFAULT_LOOKBACK_PAIRS


class TestRealWalkForwardIntegration:
    def test_run_walk_forward_end_to_end_with_a_short_synthetic_series(self, tmp_path):
        trainable = EnsembleMomentumTrainable(
            strategy_id="test-ensemble-momentum-e2e",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            lookback_pairs=((4, 12), (8, 24), (16, 48)),
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
            "test-ensemble-momentum-e2e",
            "v1",
            {},
            train_bars=300,
            validate_bars=200,
            step_bars=200,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            bars_per_day=24,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
