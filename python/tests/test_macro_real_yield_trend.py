"""Tests for `python/research/strategies/macro_real_yield_trend.py` --
Strategy Research Task X (the macro-real-yield-trend strategy). See
CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-x-macro-real-yield-strategy.md` for the full design context.

**Synthetic fixtures only.** No test in this file loads, queries, or
touches `python/data/var/klines.sqlite3` or any real BTC/FRED data --
every `Kline`/`ObservationRow` here is hand-built. This module is tested
against the BTC 1d *research* split (never the holdout), and even for the
research split, unit tests use synthetic fixtures throughout -- matching
this project's established convention (`test_daily_tsmom_ensemble.py`,
`test_ensemble_momentum.py`, etc. all do the same).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from data.fred_client import ObservationRow
from research.experiment_log import read_records
from research.strategies.macro_real_yield_trend import (
    DEFAULT_LOOKBACK_TRADING_DAYS,
    DEFAULT_REFERENCE_EQUITY,
    MacroRealYieldTrendStrategy,
    MacroRealYieldTrendTrainable,
    _sign,
    compute_real_yield_trend_signs,
)
from research.strategies.volatility_targeting import (
    DEFAULT_MAX_VOL_SCALAR,
    DEFAULT_MIN_VOL_SCALAR,
    DEFAULT_TARGET_ANNUALIZED_VOL,
    RollingRealizedVolatility,
    compute_vol_scalar,
)
from research.walkforward import run_walk_forward
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _kline(i: int, price: str = "100", base_time: datetime = BASE_TIME) -> Kline:
    c = Decimal(price)
    return Kline(
        open_time=base_time + timedelta(days=i),
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal("1"),
    )


def _klines(count: int, price: str = "100", base_time: datetime = BASE_TIME) -> list[Kline]:
    return [_kline(i, price, base_time) for i in range(count)]


def _obs(date: str, value: str | None) -> ObservationRow:
    return ObservationRow(observation_date=date, value=None if value is None else Decimal(value))


def _date_str(i: int, base_time: datetime = BASE_TIME) -> str:
    return (base_time + timedelta(days=i)).date().isoformat()


def _strategy(**overrides: Any) -> MacroRealYieldTrendStrategy:
    kwargs: dict[str, Any] = dict(symbol="BTC-USDT", trend_observations=[], vol_period=2, bars_per_day=1)
    kwargs.update(overrides)
    return MacroRealYieldTrendStrategy(**kwargs)


class TestSign:
    def test_positive(self):
        assert _sign(Decimal("0.01")) == 1

    def test_negative(self):
        assert _sign(Decimal("-0.01")) == -1

    def test_zero(self):
        assert _sign(Decimal("0")) == 0


class TestComputeRealYieldTrendSigns:
    def test_rejects_non_positive_lookback(self):
        with pytest.raises(ValueError, match="lookback must be positive"):
            compute_real_yield_trend_signs([_obs("2024-01-01", "2.0")], lookback=0)
        with pytest.raises(ValueError, match="lookback must be positive"):
            compute_real_yield_trend_signs([_obs("2024-01-01", "2.0")], lookback=-1)

    def test_rising_value_over_the_lookback_produces_positive_sign(self):
        observations = [_obs(f"2024-01-{d:02d}", v) for d, v in zip(range(1, 5), ["2.00", "2.10", "2.20", "2.50"])]
        result = compute_real_yield_trend_signs(observations, lookback=2)
        # index 2 (2.20) vs index 0 (2.00) -> +1 ; index 3 (2.50) vs index 1 (2.10) -> +1
        assert [row.value for row in result] == [Decimal(1), Decimal(1)]

    def test_falling_value_over_the_lookback_produces_negative_sign(self):
        observations = [_obs(f"2024-01-{d:02d}", v) for d, v in zip(range(1, 5), ["2.50", "2.20", "2.10", "2.00"])]
        result = compute_real_yield_trend_signs(observations, lookback=2)
        assert [row.value for row in result] == [Decimal(-1), Decimal(-1)]

    def test_unchanged_value_over_the_lookback_produces_zero_sign(self):
        observations = [_obs(f"2024-01-{d:02d}", "2.00") for d in range(1, 5)]
        result = compute_real_yield_trend_signs(observations, lookback=2)
        assert [row.value for row in result] == [Decimal(0), Decimal(0)]

    def test_output_preserves_the_later_dates_own_observation_date(self):
        observations = [_obs("2024-01-01", "2.0"), _obs("2024-01-02", "2.1"), _obs("2024-01-03", "2.2")]
        result = compute_real_yield_trend_signs(observations, lookback=1)
        assert [row.observation_date for row in result] == ["2024-01-02", "2024-01-03"]

    def test_null_holiday_rows_do_not_count_as_lookback_steps(self):
        # 5 real values with one holiday (None) row interleaved -- the
        # holiday must not consume a "trading day" step. With lookback=2,
        # counting only REAL rows: index2(2.20) vs index0(2.00) -> +1.
        observations = [
            _obs("2024-01-01", "2.00"),
            _obs("2024-01-02", None),  # holiday -- not a trading day
            _obs("2024-01-03", "2.10"),
            _obs("2024-01-04", "2.20"),
        ]
        result = compute_real_yield_trend_signs(observations, lookback=2)
        assert len(result) == 1
        assert result[0].observation_date == "2024-01-04"
        assert result[0].value == Decimal(1)

    def test_fewer_real_observations_than_lookback_yields_empty_result(self):
        observations = [_obs("2024-01-01", "2.0"), _obs("2024-01-02", "2.1")]
        result = compute_real_yield_trend_signs(observations, lookback=5)
        assert result == []

    def test_out_of_order_observations_are_sorted_defensively(self):
        observations = [_obs("2024-01-03", "2.20"), _obs("2024-01-01", "2.00"), _obs("2024-01-02", "2.10")]
        result = compute_real_yield_trend_signs(observations, lookback=2)
        assert len(result) == 1
        assert result[0].observation_date == "2024-01-03"
        assert result[0].value == Decimal(1)


class TestInversionAndDirection:
    """The single most important test class in this file: a long/short
    sign-error here would silently invert the entire hypothesis while
    still 'running' without error. Both directions are pinned explicitly.

    `RollingRealizedVolatility(period=2)` needs 3 valid bars before it
    stops returning `None` (see that class's own warmup contract), so
    every scenario below feeds >= 3 constant-price bars before the
    trend-driven opening it actually asserts on -- the strategy cannot
    size a position, and therefore cannot open one, until BOTH the trend
    signal AND the volatility estimator are simultaneously ready. This is
    the same "cannot-size retries automatically" behavior
    `TestCannotSizeRetriesAutomatically` exercises directly below.
    """

    def test_a_falling_real_yield_trend_produces_a_long_signal(self):
        # trend_observations carries a NEGATIVE (falling-yield) sign
        # dated at/before the kline -- the strategy must go LONG (the
        # INVERTED mapping the module docstring specifies).
        trend_observations = [_obs(_date_str(0), "-1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(3)  # constant price -> vol warms (Decimal(0)) at index 2
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert intents[0] is None  # vol not warm yet -- retries
        assert intents[1] is None  # vol still not warm yet -- retries
        opening = intents[2]
        assert opening is not None
        assert opening.side == Side.LONG
        assert strategy.position_sign == 1

    def test_a_rising_real_yield_trend_produces_a_short_signal(self):
        trend_observations = [_obs(_date_str(0), "1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(3)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        opening = intents[2]
        assert opening is not None
        assert opening.side == Side.SHORT
        assert strategy.position_sign == -1

    def test_an_exact_zero_trend_stays_flat_not_short_or_long(self):
        trend_observations = [_obs(_date_str(0), "0")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(3)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)
        assert strategy.position_sign == 0

    def test_flip_from_long_to_short_when_the_underlying_trend_reverses(self):
        # Day 0-2: trend = -1 (forward-filled), vol warms by index 2 ->
        # opens LONG at index 2. Day 3: trend flips to +1 -> flips SHORT.
        # Note: the list comprehension below evaluates all calls eagerly,
        # so `strategy.position_sign` reflects the FINAL state (after
        # index 3) by the time any assertion runs -- a separate `probe`
        # strategy (driven only through index 2) captures the intermediate
        # LONG quantity, same pattern `daily_tsmom_ensemble`'s own flip
        # test uses.
        trend_observations = [_obs(_date_str(0), "-1"), _obs(_date_str(3), "1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(4)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert intents[0] is None
        assert intents[1] is None
        opening = intents[2]
        assert opening is not None
        assert opening.side == Side.LONG

        probe = _strategy(trend_observations=trend_observations)
        for i in range(3):
            probe(klines[: i + 1])
        quantity_before_flip = probe.position_quantity
        assert quantity_before_flip > 0

        flip = intents[3]
        assert flip is not None
        assert flip.side == Side.SHORT
        assert strategy.position_sign == -1  # final state, after the flip
        new_target_quantity = strategy.position_quantity
        # A direct flip combines the closing quantity with the new target.
        assert flip.quantity == quantity_before_flip + new_target_quantity


class TestWarmup:
    def test_no_signal_before_any_real_trend_observation_is_available(self):
        # trend_observations start well after the klines being fed.
        trend_observations = [_obs(_date_str(10), "-1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(5)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)
        assert strategy.position_sign == 0


class TestOrderEmissionSignChangeOnly:
    def test_silent_while_the_forward_filled_sign_persists_across_multiple_bars(self):
        # A single trend row dated day 0 forward-fills across days 1-5
        # (no new FRED row in between, e.g. a weekend) -- the strategy
        # opens once (once vol warms, at index 2) and stays silent for
        # every later bar the same sign keeps forward-filling across.
        trend_observations = [_obs(_date_str(0), "-1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(6)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert intents[0] is None  # vol warmup retry
        assert intents[1] is None  # vol warmup retry
        assert intents[2] is not None  # opens once vol warms
        assert intents[3] is None
        assert intents[4] is None
        assert intents[5] is None
        assert strategy.position_sign == 1

    def test_flatten_when_trend_nets_to_exactly_zero(self):
        # Opens LONG once vol warms (index 2); trend nets to zero on day 3.
        # Note: the list comprehension evaluates all calls eagerly, so
        # `strategy.position_sign` only reflects the FINAL state (after
        # index 3) by the time any assertion runs.
        trend_observations = [_obs(_date_str(0), "-1"), _obs(_date_str(3), "0")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(4)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        opening = intents[2]
        assert opening is not None
        assert opening.side == Side.LONG
        flatten_intent = intents[3]
        assert flatten_intent is not None
        assert flatten_intent.side == Side.SHORT  # closing a LONG
        assert strategy.position_sign == 0  # final state, after the flatten
        assert strategy.position_quantity == Decimal(0)

    def test_reopen_from_flat_after_flatten(self):
        trend_observations = [_obs(_date_str(0), "-1"), _obs(_date_str(3), "0"), _obs(_date_str(4), "1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(5)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert intents[2] is not None  # opens LONG
        assert intents[3] is not None  # flattens
        reopen_intent = intents[4]
        assert reopen_intent is not None
        assert reopen_intent.side == Side.SHORT
        assert strategy.position_sign == -1  # final state, after the reopen
        assert reopen_intent.quantity == strategy.position_quantity  # fresh open, no residual

    def test_intent_shape(self):
        trend_observations = [_obs(_date_str(0), "-1")]
        strategy = _strategy(trend_observations=trend_observations)
        klines = _klines(3)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        opening = intents[2]
        assert opening is not None
        assert opening.order_type == OrderType.GUARDED_MARKET
        assert opening.limit_price is None
        assert opening.signal_timeframe == "1d"
        assert opening.symbol == "BTC-USDT"


class TestSizingComposition:
    def test_quantity_matches_reference_equity_over_price_times_vol_scalar(self):
        closes = ["100", "101", "99"]
        klines = [_kline(i, price) for i, price in enumerate(closes)]
        trend_observations = [_obs(_date_str(2), "-1")]
        strategy = _strategy(trend_observations=trend_observations, vol_period=2)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        opening = intents[2]
        assert opening is not None

        vol = RollingRealizedVolatility(period=2, bars_per_day=1)
        realized_vol = None
        for kline in klines:
            realized_vol = vol.update(kline)
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=DEFAULT_TARGET_ANNUALIZED_VOL,
            min_scalar=DEFAULT_MIN_VOL_SCALAR,
            max_scalar=DEFAULT_MAX_VOL_SCALAR,
        )
        assert vol_scalar is not None

        entry_price = Decimal("99")
        expected_quantity = (DEFAULT_REFERENCE_EQUITY / entry_price) * vol_scalar
        assert opening.quantity == expected_quantity

    def test_rejects_non_positive_reference_equity(self):
        with pytest.raises(ValueError, match="reference_equity must be positive"):
            _strategy(reference_equity=Decimal("0"))
        with pytest.raises(ValueError, match="reference_equity must be positive"):
            _strategy(reference_equity=Decimal("-5"))


class TestCannotSizeRetriesAutomatically:
    def test_stays_flat_while_vol_never_warms_despite_a_persistent_signal(self):
        trend_observations = [_obs(_date_str(0), "-1")]
        strategy = _strategy(trend_observations=trend_observations, vol_period=1000)
        klines = _klines(5)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)
        assert strategy.position_sign == 0
        assert strategy.position_quantity == Decimal(0)

    def test_degenerate_price_flattens_a_stale_position_and_retries(self):
        trend_observations = [_obs(_date_str(0), "-1"), _obs(_date_str(6), "1")]
        strategy = _strategy(trend_observations=trend_observations, vol_period=2)
        klines = _klines(6)  # through day 5, still LONG (trend flips at day 6)
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.position_sign == 1
        held_quantity = strategy.position_quantity
        assert held_quantity > 0

        degenerate = Kline(
            open_time=klines[-1].open_time + timedelta(days=1),
            open=Decimal("0"),
            high=Decimal("0"),
            low=Decimal("0"),
            close=Decimal("0"),
            volume=Decimal("1"),
        )
        intent = strategy(klines + [degenerate])
        assert intent is not None
        assert intent.side == Side.SHORT  # closing the LONG
        assert intent.quantity == held_quantity
        assert strategy.position_sign == 0
        assert strategy.position_quantity == Decimal(0)


class TestFitNoSearch:
    def _trainable(self, tmp_path, **overrides: Any) -> MacroRealYieldTrendTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-macro-real-yield-trend",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            bars_per_day=1,
            lookback=2,
            vol_period=2,
            # Enough REAL observations, with lookback=2, to produce a
            # non-empty trend-sign series (see
            # test_fit_logs_a_real_backtest_when_the_trend_actually_signals
            # below) -- a single-observation fixture would make
            # compute_real_yield_trend_signs return [] for the default
            # lookback=63, so every test in this class would silently
            # exercise a permanently-flat, trade-free fit() path without
            # any of them asserting on that fact. CodeRabbit review
            # finding on this PR.
            macro_observations=[_obs(_date_str(i - 10), v) for i, v in enumerate(["2.0", "2.1", "2.2", "2.0", "1.9"])],
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return MacroRealYieldTrendTrainable(**kwargs)

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_performs_no_search(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _klines(50)
        trainable.fit(klines, {"candidates": [(21,), (63,), (126,)]}, parent_run_id="parent-2")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-2"]
        assert len(candidate_records) == 1

    def test_fit_returns_fresh_strategy_with_zero_position(self, tmp_path):
        trainable = self._trainable(tmp_path, lookback=21)
        klines = _klines(50)
        result = trainable.fit(klines, {}, parent_run_id="parent-3")
        assert isinstance(result, MacroRealYieldTrendStrategy)
        assert result.position_sign == 0
        assert result.position_quantity == Decimal(0)
        assert trainable.lookback == 21

    def test_fit_logs_a_real_backtest_when_the_trend_actually_signals(self, tmp_path):
        # Exercises the actual trade-producing path (the fixture's default
        # lookback=2 + 5 real observations produce a real, non-empty trend
        # sign), not just the candidate-count/fresh-instance bookkeeping
        # every other test in this class checks.
        trainable = self._trainable(tmp_path)
        klines = _klines(50)
        trainable.fit(klines, {}, parent_run_id="parent-5")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-5"]
        assert len(candidate_records) == 1
        aggregate_metrics = candidate_records[0]["aggregate_metrics"]
        fold_results = candidate_records[0]["fold_results"]
        assert aggregate_metrics["num_trades"] >= 1
        assert len(fold_results) == 1
        assert fold_results[0]["metrics"]["num_trades"] == aggregate_metrics["num_trades"]

    def test_default_lookback_is_63_trading_days(self):
        assert DEFAULT_LOOKBACK_TRADING_DAYS == 63

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _klines(50)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.macro_real_yield_trend.run_backtest", spy)
        trainable.fit(klines, {}, parent_run_id="parent-4")
        assert all(kid == id(klines) for kid in seen_ids)


class TestFitNeverTouchesValidateData:
    """Structural guarantee, not just an assertion on `fit()` in
    isolation: `TrainableStrategy.fit()`'s own signature never receives
    `validate_klines` at all (see `research/walkforward.py`'s
    `TrainableStrategy` Protocol), so there is no code path by which this
    Trainable *could* read validate data even if it wanted to. This test
    drives it through the real `run_walk_forward` harness (multiple
    folds) and confirms `fit()` is only ever called with each fold's own
    `train_klines`, never anything from that fold's `validate_klines`.
    """

    def test_fit_is_only_ever_called_with_each_folds_train_klines(self, tmp_path, monkeypatch):
        klines = _klines(40, price="100")
        macro_observations = [_obs(_date_str(-5), "2.0")]
        trainable = MacroRealYieldTrendTrainable(
            strategy_id="test-macro-wf-fit-scope",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            macro_observations=macro_observations,
            bars_per_day=1,
            lookback=1,
            vol_period=2,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )

        seen_train_klines: list[list[Kline]] = []
        real_fit = MacroRealYieldTrendTrainable.fit

        def spy_fit(self, train_klines, params, *, parent_run_id):
            seen_train_klines.append(list(train_klines))
            return real_fit(self, train_klines, params, parent_run_id=parent_run_id)

        monkeypatch.setattr(MacroRealYieldTrendTrainable, "fit", spy_fit)

        result = run_walk_forward(
            klines,
            trainable,
            "test-macro-wf-fit-scope",
            "v1",
            {},
            train_bars=10,
            validate_bars=8,
            step_bars=8,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            bars_per_day=1,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
        assert len(seen_train_klines) == result.aggregate["fold_count"]
        for fold_result, observed_train in zip(result.folds, seen_train_klines, strict=True):
            expected_train = klines[fold_result.train_start_index : fold_result.train_end_index]
            assert observed_train == expected_train
            # And explicitly NOT any bar from the validate range -- an
            # index-boundary check, not a value comparison: every kline in
            # this test shares the same constant price, so a value
            # comparison against the validate slice would pass or fail by
            # coincidence of length rather than by evidence (CodeRabbit
            # review finding on this PR).
            assert fold_result.train_end_index <= fold_result.validate_start_index


class TestRunWalkForwardIntegrationSynthetic:
    def test_walk_forward_runs_without_error_and_logs(self, tmp_path):
        klines = _klines(36, price="100")
        macro_observations = [
            _obs(_date_str(i), v) for i, v in enumerate(["2.0", "2.1", "2.2", "2.0", "1.9", "1.8"] * 6)
        ]
        trainable = MacroRealYieldTrendTrainable(
            strategy_id="test-macro-wf",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            macro_observations=macro_observations,
            bars_per_day=1,
            lookback=2,
            vol_period=2,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = run_walk_forward(
            klines,
            trainable,
            "test-macro-wf",
            "v1",
            {},
            train_bars=10,
            validate_bars=8,
            step_bars=8,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            bars_per_day=1,
            strategy_family="macro-conditioned",
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        assert result.aggregate["fold_count"] >= 1
        assert result.aggregate["fold_count"] == len(result.folds)
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        walk_forward_records = [r for r in records if r.get("run_id") == result.run_id]
        assert len(walk_forward_records) == 1
        assert walk_forward_records[0]["walk_forward_config"]["bars_per_day"] == 1
        assert walk_forward_records[0]["strategy_family"] == "macro-conditioned"
