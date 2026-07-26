"""Tests for `python/research/strategies/regime_momentum_risk_managed.py`
-- Strategy Research Task F, Part 1 (adding real risk management to the
regime-gated 15m momentum strategy from Task E). See CLAUDE.md's
"Strategy Research Operational Design" and `.planning/sr-f-risk-
management-and-1h-variant.md` for the full design.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/regime_momentum_risk_managed.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies.regime_momentum_risk_managed import (
    DEFAULT_STARTING_EQUITY,
    RegimeMomentumRiskManagedStrategy,
    RegimeMomentumRiskManagedTrainable,
)
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from research.walkforward import run_walk_forward
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _kline(i: int, o: str, h: str, lo: str, c: str, v: str = "1", base_time: datetime = BASE_TIME) -> Kline:
    return Kline(
        open_time=base_time + timedelta(minutes=15 * i),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _flat_klines(count: int, price: str = "100", base_time: datetime = BASE_TIME) -> list[Kline]:
    """A perfectly flat (zero-True-Range) series -- warms up SMAs/ATR
    without ever producing a real crossover or a positive ATR.
    """
    return [
        Kline(
            open_time=base_time + timedelta(minutes=15 * i),
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


def _strategy(**overrides: object) -> RegimeMomentumRiskManagedStrategy:
    # atr_period=3 (not the production default of 14) purely for test
    # tractability -- same testability trade-off precedent as
    # regime_sma_length=2 below (both are real constructor parameters,
    # just given small values here so a short, hand-verifiable scenario
    # can warm them up; production code always uses the real defaults
    # unless a caller overrides them, which nothing in this project does).
    kwargs: dict[str, Any] = dict(fast=2, slow=4, symbol="BTC-USDT", regime_sma_length=2, atr_period=3)
    kwargs.update(overrides)
    return RegimeMomentumRiskManagedStrategy(**kwargs)


# Closes for a hand-verified regime-gated entry scenario, reused directly
# from `.planning/sr-e-regime-momentum.md`'s own "Hand-verified regime-
# gating arithmetic" section (fast=2, slow=4, regime_sma_length=2) -- that
# document already worked out, bar by bar, that this exact series
# produces: a baseline crossover at idx 3 (no signal -- nothing to have
# crossed from yet), a regime flip to "up" when the 2nd synthetic 1h
# candle completes at idx 7, a genuine bearish cross at idx 8 correctly
# suppressed (regime is "up"), and a genuine bullish cross at idx 10 that
# correctly fires (regime still "up"). Reusing these exact, already-
# verified numbers here rather than inventing a fresh scenario avoids
# re-deriving (and risking re-breaking) the same arithmetic -- this
# module's crossover/regime logic is byte-for-byte identical to v1's,
# imported (`HourlyResampler`) rather than reimplemented, so the same
# input must produce the same crossover/regime trace.
_HAND_VERIFIED_CLOSES = [97, 98, 99, 100, 105, 107, 109, 110, 95, 98, 115]


def _hand_verified_klines(base_time: datetime = BASE_TIME) -> list[Kline]:
    """`_HAND_VERIFIED_CLOSES` turned into `Kline`s with a small nonzero
    high/low band around each close (`+/- 0.5`) -- `open=high=low=close`
    (as `.planning/sr-e-regime-momentum.md`'s own scenario used) would
    give a zero True Range on every bar, meaning ATR would never leave
    warmup and get stuck at exactly `0` once it did (both degenerate, and
    the latter is exactly the case the production `atr > 0` guard exists
    to handle safely -- see `RegimeMomentumRiskManagedStrategy.__call__`).
    A small nonzero band gives a realistic, always-positive ATR without
    disturbing the close-based crossover/regime arithmetic at all (that
    logic only ever reads `.close`).
    """
    klines = []
    for i, close in enumerate(_HAND_VERIFIED_CLOSES):
        price = Decimal(close)
        klines.append(
            Kline(
                open_time=base_time + timedelta(minutes=15 * i),
                open=price,
                high=price + Decimal("0.5"),
                low=price - Decimal("0.5"),
                close=price,
                volume=Decimal("1"),
            )
        )
    return klines


class TestConstruction:
    def test_rejects_non_positive_windows(self):
        with pytest.raises(ValueError, match="fast/slow"):
            _strategy(fast=0, slow=4)
        with pytest.raises(ValueError, match="fast/slow"):
            _strategy(fast=2, slow=0)

    def test_rejects_fast_not_strictly_less_than_slow(self):
        with pytest.raises(ValueError, match="fast window"):
            _strategy(fast=4, slow=4)

    def test_rejects_non_positive_regime_sma_length(self):
        with pytest.raises(ValueError, match="regime_sma_length"):
            _strategy(regime_sma_length=0)

    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.open_position is None
        assert strategy.bars_seen == 0


def _run_to_first_long_entry(strategy: RegimeMomentumRiskManagedStrategy) -> tuple[list[Kline], int]:
    """Module-level (not a test-class method) so both `TestEntrySizingAndLevels`
    and `TestExitFlattensNotFlips` can share it directly rather than one
    instantiating the other's test class to reach a "private" helper.

    Reuses the hand-verified regime-gated scenario directly (see
    `_hand_verified_klines`'s docstring): the genuine, gated-through LONG
    fire happens at bar index 10.
    """
    klines = _hand_verified_klines()

    entry_intent = None
    entry_index = None
    for i in range(len(klines)):
        window = klines[: i + 1]
        intent = strategy(window)
        if intent is not None and intent.side == Side.LONG and strategy.open_position is not None:
            entry_intent = intent
            entry_index = i
            break
    assert entry_intent is not None, "expected a LONG entry to fire in this constructed ramp"
    return klines, entry_index


class TestEntrySizingAndLevels:
    """Hand-verified scenario: a genuine bullish crossover, gated by an
    already-established "up" 1h regime, fires a LONG entry with ATR-based
    stop/target and fixed-fractional quantity computed from that exact
    entry.
    """

    def test_long_entry_has_atr_scaled_stop_and_target_below_above_entry(self):
        strategy = _strategy(fast=2, slow=4, regime_sma_length=2)
        klines, entry_index = _run_to_first_long_entry(strategy)
        position = strategy.open_position
        assert position is not None
        assert position.side == Side.LONG
        entry_price = klines[entry_index].close
        assert position.entry_price == entry_price
        assert position.stop_price < entry_price
        assert position.target_price > entry_price
        # 1:2 risk/reward by construction (default multipliers).
        stop_distance = entry_price - position.stop_price
        target_distance = position.target_price - entry_price
        assert target_distance == stop_distance * 2

    def test_long_entry_quantity_matches_fixed_fractional_sizing_formula(self):
        strategy = _strategy(fast=2, slow=4, regime_sma_length=2)
        _run_to_first_long_entry(strategy)
        position = strategy.open_position
        assert position is not None
        stop_distance = position.entry_price - position.stop_price
        expected_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        assert position.quantity == expected_quantity

    def test_entry_intent_shape(self):
        strategy = _strategy(fast=2, slow=4, regime_sma_length=2)
        klines, entry_index = _run_to_first_long_entry(strategy)
        # Re-derive the actual returned intent by replaying (state is
        # already past it, so re-run a fresh strategy identically).
        fresh = _strategy(fast=2, slow=4, regime_sma_length=2)
        intent = None
        for i in range(entry_index + 1):
            intent = fresh(klines[: i + 1])
        assert intent is not None
        assert intent.symbol == "BTC-USDT"
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.limit_price is None
        assert intent.signal_timeframe == "15m"
        assert intent.quantity > 0


class TestExitFlattensNotFlips:
    def test_stop_hit_emits_flattening_intent_with_exact_position_quantity(self):
        strategy = _strategy(fast=2, slow=4, regime_sma_length=2)
        # Manually place the strategy into a known LONG position via the
        # internal API surface the module exposes for tests (open_position
        # is read-only from outside; drive it through real entry logic
        # instead, then feed one bar that unambiguously breaches the
        # stop).
        klines = _run_to_first_long_entry(strategy)[0]
        # already entered inside the helper above via `strategy` itself
        position_before = strategy.open_position
        assert position_before is not None
        quantity_before = position_before.quantity
        stop_price = position_before.stop_price

        next_open_time = klines[-1].open_time + timedelta(minutes=15)
        breach_bar = Kline(
            open_time=next_open_time,
            open=stop_price,
            high=stop_price,
            low=stop_price - Decimal("50"),  # unambiguously below stop
            close=stop_price - Decimal("10"),
            volume=Decimal("1"),
        )
        window = klines + [breach_bar]
        intent = strategy(window)

        assert intent is not None
        assert intent.side == Side.SHORT  # opposite of the LONG position -- flattening
        assert intent.quantity == quantity_before  # exactly flattening, not flipping
        assert strategy.open_position is None

    def test_target_hit_emits_flattening_intent_with_exact_position_quantity(self):
        strategy = _strategy(fast=2, slow=4, regime_sma_length=2)
        klines = _run_to_first_long_entry(strategy)[0]
        position_before = strategy.open_position
        assert position_before is not None
        quantity_before = position_before.quantity
        target_price = position_before.target_price

        next_open_time = klines[-1].open_time + timedelta(minutes=15)
        breach_bar = Kline(
            open_time=next_open_time,
            open=target_price,
            high=target_price + Decimal("50"),
            low=target_price,
            close=target_price + Decimal("10"),
            volume=Decimal("1"),
        )
        window = klines + [breach_bar]
        intent = strategy(window)

        assert intent is not None
        assert intent.side == Side.SHORT
        assert intent.quantity == quantity_before
        assert strategy.open_position is None

    def test_no_new_entry_is_opened_while_a_position_is_already_open(self):
        strategy = _strategy(fast=2, slow=4, regime_sma_length=2)
        klines = _run_to_first_long_entry(strategy)[0]
        assert strategy.open_position is not None
        # Feed a further bar that would, in isolation, look like a fresh
        # bearish cross -- must not be acted on while a position is open.
        extra = Kline(
            open_time=klines[-1].open_time + timedelta(minutes=15),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )
        window = klines + [extra]
        intent = strategy(window)
        # Either None, or (if this bar also breaches stop/target) a
        # flattening SHORT -- never a fresh, differently-sized entry.
        if intent is not None:
            assert intent.side == Side.SHORT


class TestFitGridSearch:
    def _trainable(self, tmp_path, **overrides: object) -> RegimeMomentumRiskManagedTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-regime-momentum-risk-managed",
            strategy_version="v2-risk-managed",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return RegimeMomentumRiskManagedTrainable(**kwargs)

    def test_fit_logs_every_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)  # flat data: no candidate ever trades
        trainable.fit(klines, {"candidates": [(2, 4), (3, 6)]}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 2
        assert {r["candidate_index"] for r in candidate_records} == {0, 1}
        assert all(r["total_candidates"] == 2 for r in candidate_records)

    def test_fit_returns_a_fresh_strategy_instance_bound_to_the_winner(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)
        result = trainable.fit(klines, {"candidates": [(2, 4)]}, parent_run_id="parent-2")
        assert isinstance(result, RegimeMomentumRiskManagedStrategy)
        assert result.bars_seen == 0  # fresh instance, never called yet
        assert result.fast_window == 2
        assert result.slow_window == 4

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_klines_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_klines_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr(
            "research.strategies.regime_momentum_risk_managed.run_backtest", spy
        )
        trainable.fit(klines, {"candidates": [(2, 4)]}, parent_run_id="parent-3")
        assert all(kid == id(klines) for kid in seen_klines_ids)

    def test_fit_falls_back_to_first_candidate_when_every_candidate_has_zero_trades(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(50)  # flat -> zero ATR -> never a valid entry
        result = trainable.fit(klines, {"candidates": [(2, 4), (3, 6)]}, parent_run_id="parent-4")
        assert result.fast_window == 2
        assert result.slow_window == 4

    def test_fit_rejects_empty_candidate_list(self, tmp_path):
        trainable = self._trainable(tmp_path)
        with pytest.raises(ValueError, match="candidates"):
            trainable.fit(_flat_klines(10), {"candidates": []}, parent_run_id="parent-5")


class TestRealWalkForwardIntegration:
    def test_run_walk_forward_end_to_end_with_a_short_synthetic_series(self, tmp_path):
        """Smoke test: this strategy is a valid `TrainableStrategy` that
        `run_walk_forward` can drive without error, end to end, on a tiny
        synthetic dataset -- not a claim about results (see the real
        walk-forward run against actual BingX data reported in
        `.planning/sr-f-risk-management-and-1h-variant.md`).
        """
        trainable = RegimeMomentumRiskManagedTrainable(
            strategy_id="test-regime-momentum-risk-managed-e2e",
            strategy_version="v2-risk-managed",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        # Enough bars for a couple of small folds.
        prices = [Decimal("100") + (Decimal(i) % 20) for i in range(400)]
        klines = [
            Kline(
                open_time=BASE_TIME + timedelta(minutes=15 * i),
                open=p,
                high=p + Decimal("1"),
                low=p - Decimal("1"),
                close=p,
                volume=Decimal("1"),
            )
            for i, p in enumerate(prices)
        ]
        result = run_walk_forward(
            klines,
            trainable,
            "test-regime-momentum-risk-managed-e2e",
            "v2-risk-managed",
            {"candidates": [(2, 4), (3, 8)]},
            train_bars=100,
            validate_bars=50,
            step_bars=50,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
