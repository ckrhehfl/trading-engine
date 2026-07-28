"""Tests for `python/research/strategies/funding_extremity.py` -- Strategy
Research Task N's funding-rate-extremity contrarian strategy. See
CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-n-funding-rate-strategy.md` for the full design context.

Written first (TDD).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from metrics.funding import FundingRate
from research.experiment_log import read_records
from research.strategies.funding_extremity import (
    DEFAULT_ENTRY_Z_THRESHOLD,
    DEFAULT_FUNDING_ZSCORE_LOOKBACK,
    FundingExtremityStrategy,
    FundingExtremityTrainable,
    RollingFundingZScore,
    _funding_signal,
)
from research.strategies.risk_management import DEFAULT_REFERENCE_EQUITY, DEFAULT_RISK_FRACTION
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

_ATR_MODULE_PATH = "research.strategies.funding_extremity.AverageTrueRange.update"
_VOL_MODULE_PATH = "research.strategies.funding_extremity.RollingRealizedVolatility.update"
_ZSCORE_MODULE_PATH = "research.strategies.funding_extremity.RollingFundingZScore.update"


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


def _funding_rate(open_time: datetime, rate: str, mark_price: str = "100") -> FundingRate:
    return FundingRate(funding_time=open_time, funding_rate=Decimal(rate), mark_price=Decimal(mark_price))


def _force_full_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_VOL_MODULE_PATH, lambda self, kline: Decimal("0.20"))


_TEST_LOOKBACK = 5


def _strategy(**overrides: object) -> FundingExtremityStrategy:
    kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT",
        funding_rates=[],
        funding_zscore_lookback=_TEST_LOOKBACK,
        atr_period=3,
        vol_period=2,
    )
    kwargs.update(overrides)
    return FundingExtremityStrategy(**kwargs)


# ---------------------------------------------------------------------------
# RollingFundingZScore
# ---------------------------------------------------------------------------


class TestRollingFundingZScore:
    def test_rejects_lookback_below_two(self):
        with pytest.raises(ValueError):
            RollingFundingZScore([], lookback_settlements=1)

    def test_rejects_unsorted_funding_rates(self):
        rows = [
            _funding_rate(BASE_TIME + timedelta(hours=8), "0.0001"),
            _funding_rate(BASE_TIME, "0.0002"),
        ]
        with pytest.raises(ValueError):
            RollingFundingZScore(rows, lookback_settlements=2)

    def test_returns_none_during_warmup(self):
        rows = [_funding_rate(BASE_TIME + timedelta(hours=8 * i), "0.0001") for i in range(2)]
        zscore = RollingFundingZScore(rows, lookback_settlements=3)
        assert zscore.update(rows[-1].funding_time) is None

    def test_returns_none_before_the_first_settlement(self):
        rows = [_funding_rate(BASE_TIME + timedelta(hours=8 * i), "0.0001") for i in range(5)]
        zscore = RollingFundingZScore(rows, lookback_settlements=3)
        assert zscore.update(BASE_TIME - timedelta(hours=1)) is None

    def test_hand_computed_zscore(self):
        # rates 1, 2, 3 -> mean=2, sample variance=((1-2)^2+(2-2)^2+(3-2)^2)/2=1,
        # stdev=1, z of the latest (3) = (3-2)/1 = 1.
        rows = [
            _funding_rate(BASE_TIME, "1"),
            _funding_rate(BASE_TIME + timedelta(hours=8), "2"),
            _funding_rate(BASE_TIME + timedelta(hours=16), "3"),
        ]
        zscore = RollingFundingZScore(rows, lookback_settlements=3)
        result = zscore.update(rows[-1].funding_time)
        assert result == Decimal(1)

    def test_zero_variance_yields_zero_zscore_not_an_error(self):
        rows = [_funding_rate(BASE_TIME + timedelta(hours=8 * i), "0.0001") for i in range(3)]
        zscore = RollingFundingZScore(rows, lookback_settlements=3)
        result = zscore.update(rows[-1].funding_time)
        assert result == Decimal(0)

    def test_lookahead_safety_extra_future_rows_do_not_change_a_past_reading(self):
        rows = [
            _funding_rate(BASE_TIME, "1"),
            _funding_rate(BASE_TIME + timedelta(hours=8), "2"),
            _funding_rate(BASE_TIME + timedelta(hours=16), "3"),
        ]
        future_rows = rows + [
            _funding_rate(BASE_TIME + timedelta(hours=24), "1000"),
            _funding_rate(BASE_TIME + timedelta(hours=32), "-1000"),
        ]

        past_only = RollingFundingZScore(rows, lookback_settlements=3)
        with_future = RollingFundingZScore(future_rows, lookback_settlements=3)

        assert past_only.update(rows[2].funding_time) == with_future.update(rows[2].funding_time)

    def test_between_settlements_returns_cached_value_unchanged(self):
        rows = [_funding_rate(BASE_TIME + timedelta(hours=8 * i), str(i + 1)) for i in range(3)]
        zscore = RollingFundingZScore(rows, lookback_settlements=3)
        first = zscore.update(rows[-1].funding_time)
        second = zscore.update(rows[-1].funding_time + timedelta(hours=1))
        assert second == first

    def test_lookback_settlements_exposed_as_property(self):
        zscore = RollingFundingZScore([], lookback_settlements=7)
        assert zscore.lookback_settlements == 7


# ---------------------------------------------------------------------------
# _funding_signal -- the contrarian direction logic
# ---------------------------------------------------------------------------


class TestFundingSignal:
    def test_high_positive_zscore_at_or_above_threshold_is_contrarian_short(self):
        # Unusually high funding (longs paying heavily -- crowded LONG) ->
        # the contrarian play is SHORT.
        assert _funding_signal(Decimal("2"), Decimal("2")) == -1
        assert _funding_signal(Decimal("3.5"), Decimal("2")) == -1

    def test_low_negative_zscore_at_or_below_negative_threshold_is_contrarian_long(self):
        # Unusually low/negative funding (shorts paying -- crowded SHORT)
        # -> the contrarian play is LONG. This is the easy sign-error trap
        # this task's brief explicitly calls out -- verified directly.
        assert _funding_signal(Decimal("-2"), Decimal("2")) == 1
        assert _funding_signal(Decimal("-3.5"), Decimal("2")) == 1

    def test_zscore_inside_the_threshold_band_is_no_signal(self):
        assert _funding_signal(Decimal("0"), Decimal("2")) == 0
        assert _funding_signal(Decimal("1.9"), Decimal("2")) == 0
        assert _funding_signal(Decimal("-1.9"), Decimal("2")) == 0

    def test_rejects_non_positive_entry_threshold(self):
        with pytest.raises(ValueError):
            _funding_signal(Decimal("1"), Decimal("0"))
        with pytest.raises(ValueError):
            _funding_signal(Decimal("1"), Decimal("-1"))


# ---------------------------------------------------------------------------
# FundingExtremityStrategy construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_starts_flat(self):
        assert _strategy().open_position is None

    def test_stop_and_target_multiplier_exposed_as_properties(self):
        strategy = _strategy(stop_multiplier=Decimal("1.5"), target_multiplier=Decimal("3"))
        assert strategy.stop_multiplier == Decimal("1.5")
        assert strategy.target_multiplier == Decimal("3")

    def test_funding_zscore_lookback_and_entry_threshold_exposed_as_properties(self):
        strategy = _strategy(funding_zscore_lookback=42, entry_z_threshold=Decimal("2.5"))
        assert strategy.funding_zscore_lookback == 42
        assert strategy.entry_z_threshold == Decimal("2.5")

    def test_rejects_non_positive_entry_z_threshold(self):
        with pytest.raises(ValueError):
            _strategy(entry_z_threshold=Decimal("0"))

    def test_default_lookback_and_threshold_match_module_constants(self):
        strategy = FundingExtremityStrategy(symbol="BTC-USDT", funding_rates=[])
        assert strategy.funding_zscore_lookback == DEFAULT_FUNDING_ZSCORE_LOOKBACK
        assert strategy.entry_z_threshold == DEFAULT_ENTRY_Z_THRESHOLD


# ---------------------------------------------------------------------------
# Contrarian direction, end to end through the strategy (edge-triggered)
# ---------------------------------------------------------------------------


class TestContrarianDirectionEndToEnd:
    def _fire(self, monkeypatch, zscore_sequence: list[Decimal | None]) -> list:
        _force_full_readiness(monkeypatch)
        monkeypatch.setattr(_ATR_MODULE_PATH, lambda self, kline: Decimal("2"))
        values = iter(zscore_sequence)
        monkeypatch.setattr(_ZSCORE_MODULE_PATH, lambda self, current_open_time: next(values))

        strategy = _strategy()
        klines = _flat_klines(len(zscore_sequence))
        intents = []
        for i in range(len(klines)):
            intents.append(strategy(klines[: i + 1]))
        return intents

    def test_crossing_from_high_extreme_state_to_low_extreme_fires_a_long_entry(self, monkeypatch):
        # bar0: no signal yet (in-band) -> no prior state established.
        # bar1: high extreme (funding unusually high) -> establishes SHORT
        #       signal state, but does not fire (no prior state to differ
        #       from).
        # bar2: back in-band -> no entry attempt, state unchanged.
        # bar3: low extreme (funding unusually negative) -> differs from
        #       the established SHORT state -> fires a contrarian LONG.
        sequence = [Decimal("0"), Decimal("3"), Decimal("0"), Decimal("-3")]
        intents = self._fire(monkeypatch, sequence)
        fired = [i for i in intents if i is not None]
        assert len(fired) == 1
        assert fired[0].side == Side.LONG

    def test_crossing_from_low_extreme_state_to_high_extreme_fires_a_short_entry(self, monkeypatch):
        sequence = [Decimal("0"), Decimal("-3"), Decimal("0"), Decimal("3")]
        intents = self._fire(monkeypatch, sequence)
        fired = [i for i in intents if i is not None]
        assert len(fired) == 1
        assert fired[0].side == Side.SHORT

    def test_first_ever_extreme_reading_does_not_fire_without_a_prior_state(self, monkeypatch):
        sequence = [Decimal("3")]
        intents = self._fire(monkeypatch, sequence)
        assert all(i is None for i in intents)

    def test_none_zscore_readings_never_fire(self, monkeypatch):
        sequence = [None, None, None]
        intents = self._fire(monkeypatch, sequence)
        assert all(i is None for i in intents)

    def test_atr_warmup_does_not_consume_the_edge_trigger_state(self, monkeypatch):
        # Real CodeRabbit review finding on this task's PR: an entry
        # blocked purely by ATR being unavailable (warmup, or a
        # degenerate atr<=0 reading) must not be treated as "the signal
        # fired" for edge-trigger purposes -- otherwise the transition is
        # silently lost even once ATR later becomes available, unless the
        # raw z-score happens to change again first (see module
        # docstring's "Edge-triggering" section).
        _force_full_readiness(monkeypatch)
        # bar0: high extreme -> establishes SHORT signal state (no prior
        #       state to fire against yet).
        # bar1: back in-band.
        # bar2: low extreme -> WOULD flip to LONG, but ATR is still None
        #       here (warmup) -- must be treated as a downstream-filter
        #       rejection, not a consumed signal.
        # bar3: still low extreme (unchanged reading), ATR now ready ->
        #       must fire the LONG entry now, since bar2 must not have
        #       already consumed the state.
        zscore_sequence = [Decimal("3"), Decimal("0"), Decimal("-3"), Decimal("-3")]
        atr_sequence = [Decimal("2"), Decimal("2"), None, Decimal("2")]
        zvalues = iter(zscore_sequence)
        avalues = iter(atr_sequence)
        monkeypatch.setattr(_ZSCORE_MODULE_PATH, lambda self, current_open_time: next(zvalues))
        monkeypatch.setattr(_ATR_MODULE_PATH, lambda self, kline: next(avalues))

        strategy = _strategy()
        klines = _flat_klines(len(zscore_sequence))
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]

        fired = [i for i in intents if i is not None]
        assert len(fired) == 1
        assert fired[0].side == Side.LONG


# ---------------------------------------------------------------------------
# Risk management composition (ATR stop/target, fixed-fractional sizing)
# ---------------------------------------------------------------------------


class TestRiskManagement:
    def _run_to_first_entry(self, strategy, monkeypatch) -> tuple[list[Kline], int]:
        _force_full_readiness(monkeypatch)
        monkeypatch.setattr(_ATR_MODULE_PATH, lambda self, kline: Decimal("2"))
        sequence = [Decimal("0"), Decimal("3"), Decimal("0"), Decimal("-3"), Decimal("0")]
        values = iter(sequence)
        monkeypatch.setattr(_ZSCORE_MODULE_PATH, lambda self, current_open_time: next(values))

        klines = _flat_klines(len(sequence))
        entry_index = None
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            if intent is not None and strategy.open_position is not None:
                entry_index = i
                break
        assert entry_index is not None, "expected an entry to fire in this scenario"
        return klines, entry_index

    def test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward(self, monkeypatch):
        strategy = _strategy()
        self._run_to_first_entry(strategy, monkeypatch)
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
        strategy = _strategy()
        self._run_to_first_entry(strategy, monkeypatch)
        position = strategy.open_position
        assert position is not None
        stop_distance = abs(position.entry_price - position.stop_price)
        expected_quantity = (DEFAULT_REFERENCE_EQUITY * DEFAULT_RISK_FRACTION) / stop_distance
        assert position.quantity == expected_quantity

    def test_stop_hit_emits_flattening_intent_with_exact_quantity(self, monkeypatch):
        strategy = _strategy()
        klines, _ = self._run_to_first_entry(strategy, monkeypatch)
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
        strategy = _strategy()
        klines, entry_index = self._run_to_first_entry(strategy, monkeypatch)
        assert entry_index is not None
        # Re-run against a fresh strategy to capture the actual entry intent.
        _force_full_readiness(monkeypatch)
        fresh = _strategy()
        sequence = [Decimal("0"), Decimal("3"), Decimal("0"), Decimal("-3"), Decimal("0")]
        values = iter(sequence)
        monkeypatch.setattr(_ZSCORE_MODULE_PATH, lambda self, current_open_time: next(values))
        monkeypatch.setattr(_ATR_MODULE_PATH, lambda self, kline: Decimal("2"))
        intent = None
        for i in range(entry_index + 1):
            intent = fresh(klines[: i + 1])
        assert intent is not None
        assert intent.signal_timeframe == "1h"
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.limit_price is None


# ---------------------------------------------------------------------------
# Warmup -- genuine RollingFundingZScore wiring (not monkeypatched)
# ---------------------------------------------------------------------------


class TestWarmup:
    def test_no_signal_until_funding_zscore_lookback_is_satisfied(self, monkeypatch):
        _force_full_readiness(monkeypatch)
        monkeypatch.setattr(_ATR_MODULE_PATH, lambda self, kline: Decimal("2"))
        # Only 2 real settlements available, lookback=5 -> zscore stays
        # None for the whole run regardless of price action, so no entry
        # can ever fire, no matter how extreme any individual funding rate
        # value is.
        funding_rows = [
            _funding_rate(BASE_TIME, "1000"),
            _funding_rate(BASE_TIME + timedelta(hours=1), "-1000"),
        ]
        strategy = _strategy(funding_rates=funding_rows, funding_zscore_lookback=5)
        klines = _flat_klines(10)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)


# ---------------------------------------------------------------------------
# FundingExtremityTrainable / fit()
# ---------------------------------------------------------------------------


class TestFit:
    def _trainable(self, tmp_path, **overrides: object) -> FundingExtremityTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-funding-extremity",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            funding_rates=[],
            funding_zscore_lookback=4,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return FundingExtremityTrainable(**kwargs)

    def test_funding_rates_is_a_required_keyword(self, tmp_path):
        with pytest.raises(TypeError):
            FundingExtremityTrainable(
                strategy_id="s",
                strategy_version="v1",
                fee_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                runs_path=str(tmp_path / "experiments.jsonl"),
            )

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(20)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_logs_the_fixed_params(self, tmp_path):
        trainable = self._trainable(tmp_path, funding_zscore_lookback=4, entry_z_threshold=Decimal("2.5"))
        klines = _flat_klines(20)
        trainable.fit(klines, {}, parent_run_id="parent-2")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        record = next(r for r in records if r.get("parent_run_id") == "parent-2")
        assert record["params"]["funding_zscore_lookback"] == 4
        assert record["params"]["entry_z_threshold"] == "2.5"

    def test_fit_returns_fresh_strategy_bound_to_the_fixed_params(self, tmp_path):
        trainable = self._trainable(tmp_path, funding_zscore_lookback=4)
        klines = _flat_klines(20)
        result = trainable.fit(klines, {}, parent_run_id="parent-3")
        assert isinstance(result, FundingExtremityStrategy)
        assert result.open_position is None
        assert result.funding_zscore_lookback == 4

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        # Real CodeRabbit review finding on this task's PR: comparing only
        # id(klines_arg) == id(klines) doesn't actually prove lookahead
        # safety when `klines` IS the full array with nothing beyond it to
        # leak. This version gives fit() a real future slice (bars 20..39)
        # beyond train_klines (bars 0..19) and proves run_backtest is only
        # ever called with data ending at train_klines' own last bar.
        trainable = self._trainable(tmp_path)
        full = _flat_klines(40)
        train_klines = full[:20]

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_last_times = []

        def spy(klines_arg, *args, **kwargs):
            seen_last_times.append(klines_arg[-1].open_time)
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.funding_extremity.run_backtest", spy)
        trainable.fit(train_klines, {}, parent_run_id="parent-4")
        assert seen_last_times
        assert all(t == train_klines[-1].open_time for t in seen_last_times)

    def test_default_params_used_when_trainable_constructed_without_override(self, tmp_path):
        trainable = FundingExtremityTrainable(
            strategy_id="test-funding-extremity-default",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            funding_rates=[],
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = trainable.fit(_flat_klines(30), {}, parent_run_id="parent-5")
        assert result.funding_zscore_lookback == DEFAULT_FUNDING_ZSCORE_LOOKBACK
        assert result.entry_z_threshold == DEFAULT_ENTRY_Z_THRESHOLD

    def test_fit_threads_funding_rates_through_to_compute_metrics_for_in_sample_scoring(self, tmp_path, monkeypatch):
        # Strategy Research Task N's honesty requirement: even fit()'s own
        # in-sample logging should reflect real funding P&L, not just the
        # final walk-forward evaluation -- see module docstring's
        # "Funding P&L: signal input AND P&L component" section.
        funding_rows = [_funding_rate(BASE_TIME + timedelta(hours=i), "0.0001") for i in range(10)]
        trainable = self._trainable(tmp_path, funding_rates=funding_rows)
        klines = _flat_klines(20)

        captured_kwargs = []
        import research.strategies.funding_extremity as module

        real_compute_metrics = module.compute_metrics

        def spy(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return real_compute_metrics(*args, **kwargs)

        monkeypatch.setattr(module, "compute_metrics", spy)
        trainable.fit(klines, {}, parent_run_id="parent-6")

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["funding_rates"] is funding_rows
