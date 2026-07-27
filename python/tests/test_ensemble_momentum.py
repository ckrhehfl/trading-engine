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

`TestFitOptionalRiskRewardGridSearch`/the `test_run_walk_forward_with_
sensitivity_extractor` case in `TestRealWalkForwardIntegration` were added
for Strategy Research Task I (`.planning/sr-i-ensemble-refinement.md`) --
see that module's own docstring for why an opt-in risk:reward grid search
was added on top of sr-h's original (still-default) "fixed, not searched"
design.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.ensemble_momentum import (
    DEFAULT_LOOKBACK_PAIRS,
    RECALIBRATED_ADX_HIGH_THRESHOLD,
    RECALIBRATED_ADX_LOW_THRESHOLD,
    EnsembleMomentumStrategy,
    EnsembleMomentumTrainable,
    _combined_sign,
)
from research.strategies.regime_weighting import DEFAULT_ADX_HIGH_THRESHOLD, DEFAULT_ADX_LOW_THRESHOLD
from research.strategies.risk_management import (
    DEFAULT_REFERENCE_EQUITY,
    DEFAULT_RISK_FRACTION,
    DEFAULT_STOP_MULTIPLIER,
    DEFAULT_TARGET_MULTIPLIER,
)
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

    def test_recalibrated_adx_thresholds_are_not_the_default_and_construct_a_working_strategy(self):
        """Strategy Research Task I: RECALIBRATED_ADX_LOW_THRESHOLD/
        RECALIBRATED_ADX_HIGH_THRESHOLD (25/50, BTC-empirical, see module
        docstring) are an opt-in override, not the constructor default --
        confirms both that they differ from the still-unchanged default
        20/25 (regime_weighting's DEFAULT_ADX_LOW_THRESHOLD/HIGH_THRESHOLD)
        and that they actually construct a working strategy when passed
        explicitly (no exception, starts flat like any other construction).
        """
        assert RECALIBRATED_ADX_LOW_THRESHOLD != DEFAULT_ADX_LOW_THRESHOLD
        assert RECALIBRATED_ADX_HIGH_THRESHOLD != DEFAULT_ADX_HIGH_THRESHOLD
        assert RECALIBRATED_ADX_LOW_THRESHOLD == Decimal("25")
        assert RECALIBRATED_ADX_HIGH_THRESHOLD == Decimal("50")

        recalibrated_strategy = _strategy(
            adx_low=RECALIBRATED_ADX_LOW_THRESHOLD,
            adx_high=RECALIBRATED_ADX_HIGH_THRESHOLD,
        )
        assert recalibrated_strategy.open_position is None
        assert recalibrated_strategy.bars_seen == 0

    def test_stop_and_target_multiplier_exposed_as_properties(self):
        """Strategy Research Task I: these two properties are what a real
        sensitivity_extractor reads to recover the winning
        (risk_reward_tenths,) candidate for Task G's robustness check --
        see TestFitOptionalRiskRewardGridSearch and TestRealWalkForwardIntegration.
        """
        strategy = _strategy(stop_multiplier=Decimal("1.5"), target_multiplier=Decimal("4.5"))
        assert strategy.stop_multiplier == Decimal("1.5")
        assert strategy.target_multiplier == Decimal("4.5")


class TestEnsembleSignalDiffersFromAnySingleConstituent:
    def test_combined_signal_diverges_from_a_single_constituent_pairs_own_sign_and_actually_fires(
        self, monkeypatch
    ):
        """Construct a price path where the ensemble's combined signal
        actually fires an entry, and prove that entry's direction is NOT
        what a single constituent pair, taken alone, would have read at
        that exact bar -- proving the combination logic is genuinely not
        reducible to any one pair, with a real, non-vacuous assertion
        (not "either it fired the expected side, or it never fired at
        all", which a CodeRabbit review correctly flagged as trivially
        satisfiable by a scenario that never fires anything).

        short=(2,3), medium=(4,6), long=(8,12): a decline establishes all
        three pairs bearish, then a rally flips the two *faster* pairs
        (short, medium) bullish while the *slowest* pair (long, still
        anchored to the pre-rally decline via its longer trailing window)
        remains bearish for a few more bars. Majority (short+medium)
        outvotes the dissenting long pair, and the ensemble fires a real
        LONG entry at that bar -- verified directly below by independently
        recomputing the long pair's own sign at that exact bar, not
        assumed.
        """
        _force_full_conviction(monkeypatch)
        lookback_pairs = ((2, 3), (4, 6), (8, 12))
        strategy = _strategy(lookback_pairs=lookback_pairs, atr_period=3)
        decline = [Decimal(200 - 3 * i) for i in range(14)]
        rally = [decline[-1] + Decimal(5 * i) for i in range(1, 10)]
        closes = decline + rally
        klines = [_hourly_kline(i, c) for i, c in enumerate(closes)]

        fire_index = None
        entry_intent = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None:
                fire_index = i
                entry_intent = intent
                break  # the first-ever fired intent is necessarily an entry (no position existed before)
        assert entry_intent is not None, "expected the ensemble to fire a real entry in this scenario"
        assert entry_intent.side == Side.LONG

        # Independently recompute the LONGEST (dissenting) pair's own
        # sign at the exact bar the ensemble fired.
        long_fast, long_slow = lookback_pairs[2]
        window = closes[: fire_index + 1]
        long_slow_sma = sum(window[-long_slow:]) / long_slow
        long_fast_sma = sum(window[-long_fast:]) / long_fast
        long_pair_sign = 1 if long_fast_sma > long_slow_sma else (-1 if long_fast_sma < long_slow_sma else 0)
        assert long_pair_sign == -1, (
            "test setup assumption: the long pair alone should still read bearish at the fire bar"
        )


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


class TestFitOptionalRiskRewardGridSearch:
    """Strategy Research Task I (`.planning/sr-i-ensemble-refinement.md`):
    `fit()` gained an opt-in grid search over `target_multiplier`
    (expressed as `risk_reward_tenths` -- see `ensemble_momentum.py`'s
    module docstring for the encoding and why) when `params["candidates"]`
    is explicitly provided. Omitting `"candidates"` entirely is exactly
    `TestFitNoGridSearch` above, unmodified -- this class only covers the
    new opt-in path.
    """

    def _trainable(self, tmp_path, **overrides: object) -> EnsembleMomentumTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-ensemble-momentum-rr",
            strategy_version="v2",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            lookback_pairs=((2, 4), (3, 6), (4, 8)),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return EnsembleMomentumTrainable(**kwargs)

    def test_fit_logs_one_candidate_record_per_risk_reward_tenths_value(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        trainable.fit(klines, {"candidates": [(15,), (20,), (25,), (30,)]}, parent_run_id="parent-rr-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-rr-1"]
        assert len(candidate_records) == 4
        assert {r["candidate_index"] for r in candidate_records} == {0, 1, 2, 3}
        assert all(r["total_candidates"] == 4 for r in candidate_records)

    def test_fit_logs_the_candidate_specific_target_multiplier_holding_stop_fixed(self, tmp_path):
        trainable = self._trainable(tmp_path, stop_multiplier=Decimal("1.5"))
        klines = _flat_klines(50)
        trainable.fit(klines, {"candidates": [(20,), (30,)]}, parent_run_id="parent-rr-2")
        records = {
            r["candidate_index"]: r
            for r in read_records(str(tmp_path / "experiments.jsonl"))
            if r.get("parent_run_id") == "parent-rr-2"
        }
        # risk_reward_tenths=20 -> target_multiplier = 1.5 * 20/10 = 3.0
        assert Decimal(records[0]["params"]["target_multiplier"]) == Decimal("3.0")
        # risk_reward_tenths=30 -> target_multiplier = 1.5 * 30/10 = 4.5
        assert Decimal(records[1]["params"]["target_multiplier"]) == Decimal("4.5")
        # stop_multiplier itself never varies across candidates.
        assert Decimal(records[0]["params"]["stop_multiplier"]) == Decimal("1.5")
        assert Decimal(records[1]["params"]["stop_multiplier"]) == Decimal("1.5")

    def test_fit_picks_the_higher_scoring_candidate(self, tmp_path, monkeypatch):
        """Isolates fit()'s own candidate-selection logic (pick the
        candidate with the highest in-sample total_return, matching
        SingleLookbackMomentumTrainable.fit()'s identical convention) from
        the strategy/backtest machinery, by monkeypatching compute_metrics
        to return canned, controlled Metrics per candidate in call order --
        the same dependency-isolation approach this test suite already uses
        for AverageDirectionalIndex.update/RollingRealizedVolatility.update
        elsewhere in this file, applied to the scoring step instead.
        """
        from metrics.metrics import Metrics

        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        canned_results = [
            Metrics(
                starting_equity=Decimal("10000"),
                final_equity=Decimal("10100"),
                equity_curve=[],
                closed_trades=[],
                total_return=Decimal("0.01"),
                sharpe_ratio=None,
                max_drawdown=Decimal("0"),
                win_rate=None,
                num_trades=5,
                profit_factor=None,
            ),
            Metrics(
                starting_equity=Decimal("10000"),
                final_equity=Decimal("10300"),
                equity_curve=[],
                closed_trades=[],
                total_return=Decimal("0.03"),
                sharpe_ratio=None,
                max_drawdown=Decimal("0"),
                win_rate=None,
                num_trades=5,
                profit_factor=None,
            ),
            Metrics(
                starting_equity=Decimal("10000"),
                final_equity=Decimal("10005"),
                equity_curve=[],
                closed_trades=[],
                total_return=Decimal("0.005"),
                sharpe_ratio=None,
                max_drawdown=Decimal("0"),
                win_rate=None,
                num_trades=5,
                profit_factor=None,
            ),
        ]
        calls = iter(canned_results)
        monkeypatch.setattr("research.strategies.ensemble_momentum.compute_metrics", lambda *a, **k: next(calls))

        # candidates[1] (risk_reward_tenths=20) gets the highest canned
        # total_return (0.03) -- fit() must pick it, not the first or last.
        # _trainable() doesn't override stop_multiplier, so it's DEFAULT_STOP_MULTIPLIER.
        result = trainable.fit(klines, {"candidates": [(15,), (20,), (25,)]}, parent_run_id="parent-rr-3")
        assert result.target_multiplier == DEFAULT_STOP_MULTIPLIER * Decimal(20) / Decimal(10)

    def test_fit_falls_back_to_first_candidate_when_every_candidate_has_zero_trades(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {"candidates": [(20,), (30,)]}, parent_run_id="parent-rr-4")
        # first candidate: risk_reward_tenths=20 -> target_multiplier = 1.5 * 2.0 = 3.0
        assert result.target_multiplier == Decimal("3.0")

    def test_fit_rejects_empty_candidate_list(self, tmp_path):
        trainable = self._trainable(tmp_path)
        with pytest.raises(ValueError, match="candidates"):
            trainable.fit(_flat_klines(10), {"candidates": []}, parent_run_id="parent-rr-5")

    def test_fit_rejects_non_positive_risk_reward_tenths(self, tmp_path):
        trainable = self._trainable(tmp_path)
        with pytest.raises(ValueError, match="risk_reward_tenths"):
            trainable.fit(_flat_klines(50), {"candidates": [(0,)]}, parent_run_id="parent-rr-6")

    def test_stop_multiplier_is_never_varied_by_the_search(self, tmp_path):
        trainable = self._trainable(tmp_path, stop_multiplier=Decimal("2.0"))
        result = trainable.fit(_flat_klines(50), {"candidates": [(20,)]}, parent_run_id="parent-rr-7")
        assert result.stop_multiplier == Decimal("2.0")
        assert result.target_multiplier == Decimal("4.0")  # 2.0 * 20/10

    def test_omitting_candidates_key_entirely_is_unaffected(self, tmp_path):
        """Belt-and-suspenders duplicate of TestFitNoGridSearch's own
        coverage, from this class's fixture/naming context -- confirms the
        opt-in branch and the default branch are genuinely independent
        code paths, not just independently named tests.
        """
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {}, parent_run_id="parent-rr-8")
        # _trainable() doesn't override target_multiplier, so it's DEFAULT_TARGET_MULTIPLIER.
        assert result.target_multiplier == DEFAULT_TARGET_MULTIPLIER
        records = [
            r for r in read_records(str(tmp_path / "experiments.jsonl")) if r.get("parent_run_id") == "parent-rr-8"
        ]
        assert len(records) == 1
        assert records[0]["total_candidates"] == 1


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

    def test_run_walk_forward_with_risk_reward_grid_and_sensitivity_extractor(self, tmp_path):
        """Strategy Research Task I: the opt-in risk:reward grid search
        (params["candidates"], a list of (risk_reward_tenths,) tuples) is
        compatible end to end with run_walk_forward's real fold loop and
        Task G's sensitivity_extractor -- the exact combination this task's
        real walk-forward run uses. `EnsembleMomentumStrategy.target_multiplier`/
        `stop_multiplier` are the properties a real sensitivity_extractor
        reads to recover the winning (risk_reward_tenths,) candidate.
        """
        trainable = EnsembleMomentumTrainable(
            strategy_id="test-ensemble-momentum-rr-e2e",
            strategy_version="v2",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            lookback_pairs=((4, 12), (8, 24), (16, 48)),
            atr_period=5,
            adx_period=5,
            vol_period=5,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        prices = [Decimal(100) + (Decimal(i) % 40) for i in range(700)]
        klines = [_hourly_kline(i, p) for i, p in enumerate(prices)]

        def extract_risk_reward_tenths(strategy: EnsembleMomentumStrategy) -> tuple[int]:
            ratio = strategy.target_multiplier / strategy.stop_multiplier
            return (int(ratio * 10),)

        result = run_walk_forward(
            klines,
            trainable,
            "test-ensemble-momentum-rr-e2e",
            "v2",
            {"candidates": [(15,), (20,), (25,), (30,)]},
            train_bars=300,
            validate_bars=200,
            step_bars=200,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            bars_per_day=24,
            sensitivity_extractor=extract_risk_reward_tenths,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
        for fold_result in result.folds:
            assert fold_result.parameter_sensitivity is not None
