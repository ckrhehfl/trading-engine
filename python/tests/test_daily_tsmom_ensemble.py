"""Tests for `python/research/strategies/daily_tsmom_ensemble.py` --
Strategy Research Task U (the pre-registered daily TSMOM ensemble attempt
against the virgin `1d` early-window holdout). See CLAUDE.md's "Strategy
Research Methodology" section (Gate 2) and
`.planning/sr-u-preregistered-attempt-spec.md` for the full design context.

**Synthetic fixtures only.** No test in this file loads, queries or
touches `python/data/var/klines.sqlite3` or any real BingX kline/funding
data -- every `Kline` here is hand-built. This is the inviolable rule this
task was built under: the 1d early-window holdout must not be accessed by
anything except the single, later, dedicated execution task (sr-v).

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/strategies/daily_tsmom_ensemble.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.lineage import resolve_family
from research.preregistration import load_preregistration
from research.run_preregistered import resolve_entry_point
from research.strategies.daily_tsmom_ensemble import (
    DEFAULT_LOOKBACKS,
    DEFAULT_REFERENCE_EQUITY,
    DailyTsmomEnsembleStrategy,
    DailyTsmomEnsembleTrainable,
    _average_of_lookback_signs,
    _sign,
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

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
PREREGISTRATION_PATH = "../configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json"


def _daily_kline(i: int, close: str, base_time: datetime = BASE_TIME) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time=base_time + timedelta(days=i),
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal("1"),
    )


def _klines(closes: list[str], base_time: datetime = BASE_TIME) -> list[Kline]:
    return [_daily_kline(i, c, base_time) for i, c in enumerate(closes)]


def _flat_klines(count: int, price: str = "100", base_time: datetime = BASE_TIME) -> list[Kline]:
    return [_daily_kline(i, price, base_time) for i in range(count)]


def _strategy(**overrides: object) -> DailyTsmomEnsembleStrategy:
    kwargs: dict[str, Any] = dict(symbol="BTC-USDT", lookbacks=(1, 2, 3, 4), vol_period=2, bars_per_day=1)
    kwargs.update(overrides)
    return DailyTsmomEnsembleStrategy(**kwargs)


# The one bar-by-bar sequence used across most order-emission tests --
# exercises warmup silence, a fresh open, silent same-sign persistence, a
# flatten-to-zero, a fresh re-open, and a direct sign flip, all in one
# deterministic 9-bar walk. Hand-derived and cross-checked directly against
# `_average_of_lookback_signs` (see `TestEnsembleSignalComputation`) before
# being relied on here.
_SCENARIO_CLOSES = ["100", "100", "100", "100", "90", "80", "95", "110", "70"]


class TestConstruction:
    def test_rejects_fewer_than_two_lookbacks(self):
        with pytest.raises(ValueError, match="at least 2 lookback"):
            _strategy(lookbacks=(21,))

    def test_rejects_non_positive_lookback(self):
        with pytest.raises(ValueError, match="every lookback must be positive"):
            _strategy(lookbacks=(0, 5))
        with pytest.raises(ValueError, match="every lookback must be positive"):
            _strategy(lookbacks=(-3, 5))

    def test_rejects_non_positive_reference_equity(self):
        with pytest.raises(ValueError, match="reference_equity must be positive"):
            _strategy(reference_equity=Decimal("0"))
        with pytest.raises(ValueError, match="reference_equity must be positive"):
            _strategy(reference_equity=Decimal("-100"))

    def test_starts_flat(self):
        strategy = _strategy()
        assert strategy.position_sign == 0
        assert strategy.position_quantity == Decimal(0)
        assert strategy.bars_seen == 0

    def test_warmup_bars_is_max_lookback_plus_one(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4))
        assert strategy.warmup_bars == 5
        strategy2 = _strategy(lookbacks=DEFAULT_LOOKBACKS)
        assert strategy2.warmup_bars == 253


class TestEnsembleSignalComputation:
    """`_average_of_lookback_signs` in isolation -- signal correctness at
    each lookback, independent of any warmup/order-emission concern.
    """

    def test_full_agreement_bullish(self):
        closes = [Decimal(c) for c in [90, 91, 92, 93, 100]]
        assert _average_of_lookback_signs(closes, (1, 2, 3, 4)) == Decimal(1)

    def test_full_agreement_bearish(self):
        closes = [Decimal(c) for c in [110, 109, 108, 107, 100]]
        assert _average_of_lookback_signs(closes, (1, 2, 3, 4)) == Decimal(-1)

    def test_exact_tie_nets_zero(self):
        # Two lookbacks bullish, two bearish -> average exactly 0.
        # current=100; L1 vs closes[-2]=120 -> -1; L2 vs closes[-3]=80 -> +1;
        # L3 vs closes[-4]=120 -> -1; L4 vs closes[-5]=80 -> +1. Sum=0.
        closes = [Decimal(c) for c in [80, 120, 80, 120, 100]]
        assert _average_of_lookback_signs(closes, (1, 2, 3, 4)) == Decimal(0)

    def test_partial_agreement_produces_fractional_values(self):
        # 3 lookbacks agree bullish, 1 disagrees -> +0.5, not only the
        # coarser {-1,-0.5,0,0.5,1} 5-value set the governing spec's prose
        # loosely describes -- this module implements the literal average,
        # which spans all 9 multiples of 0.25 for 4 lookbacks (see module
        # docstring's "Discrepancies noted, not silently resolved").
        # current=101; L1 vs closes[-2]=105 -> -1; L2 vs closes[-3]=100 -> +1;
        # L3 vs closes[-4]=100 -> +1; L4 vs closes[-5]=100 -> +1. Sum=2/4=0.5
        closes = [Decimal(c) for c in [100, 100, 100, 105, 101]]
        assert _average_of_lookback_signs(closes, (1, 2, 3, 4)) == Decimal("0.5")

    def test_single_lookback_disagreement_produces_quarter_step(self):
        closes = [Decimal(c) for c in [100, 100, 100, 105, 101]]
        value = _average_of_lookback_signs(closes, (1, 2, 3, 4))
        assert value == Decimal("0.5")
        # Every achievable value for 4 lookbacks is a multiple of 0.25.
        assert (value * 4) == (value * 4).to_integral_value()

    def test_scenario_sequence_matches_hand_derivation(self):
        # Cross-checks the shared `_SCENARIO_CLOSES` fixture every
        # order-emission test below relies on.
        closes = [Decimal(c) for c in _SCENARIO_CLOSES]
        expected = {4: Decimal(-1), 5: Decimal(-1), 6: Decimal(0), 7: Decimal(1), 8: Decimal(-1)}
        for index, expected_value in expected.items():
            assert _average_of_lookback_signs(closes[: index + 1], (1, 2, 3, 4)) == expected_value


class TestWarmup:
    def test_no_signal_before_all_lookbacks_are_warm(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        closes = _SCENARIO_CLOSES[:4]  # only 4 bars, warmup needs 5
        klines = _klines(closes)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)
        assert strategy.position_sign == 0
        assert strategy.bars_seen == 4

    def test_warmup_character_does_not_silently_shrink(self):
        # Every lookback must be simultaneously warm -- a strategy given
        # only 4 bars must never compute a signal from a SUBSET (e.g. just
        # the two shortest lookbacks), matching ensemble_momentum.py's
        # established precedent.
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(["100", "100", "100", "50"])  # would be a screaming signal at L=1,2,3 alone
        for i in range(len(klines)):
            intent = strategy(klines[: i + 1])
            assert intent is None


class TestOrderEmissionSignChangeOnly:
    """Option B: order emission fires only on a SIGN-category change of the
    ensemble value, never on a same-sign magnitude-only change. See module
    docstring's "Order emission" section.
    """

    def test_first_open_on_warmup_completion(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(_SCENARIO_CLOSES[:5])  # through index 4 -- first tradeable bar
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents[:4])
        opening = intents[4]
        assert opening is not None
        assert opening.side == Side.SHORT  # ensemble = -1 at index 4
        assert strategy.position_sign == -1
        assert strategy.position_quantity > 0
        assert opening.quantity == strategy.position_quantity

    def test_silent_while_sign_persists_despite_magnitude_change(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(_SCENARIO_CLOSES[:6])  # through index 5, still sign -1
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert intents[4] is not None  # the open
        assert intents[5] is None  # same sign (-1) -- no resize, no order
        assert strategy.position_sign == -1

    def test_flatten_when_ensemble_nets_to_zero(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(_SCENARIO_CLOSES[:7])  # through index 6, ensemble=0
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        flatten_intent = intents[6]
        assert flatten_intent is not None
        assert flatten_intent.side == Side.LONG  # closing a SHORT
        assert strategy.position_sign == 0
        assert strategy.position_quantity == Decimal(0)

    def test_reopen_from_flat_after_flatten(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(_SCENARIO_CLOSES[:8])  # through index 7, ensemble=+1
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        reopen_intent = intents[7]
        assert reopen_intent is not None
        assert reopen_intent.side == Side.LONG
        assert strategy.position_sign == 1
        assert reopen_intent.quantity == strategy.position_quantity  # fresh open, no residual close

    def test_direct_flip_emits_one_intent_with_combined_quantity(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(_SCENARIO_CLOSES)  # full 9-bar sequence, ends on a direct +1 -> -1 flip
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        quantity_before_flip = None
        # Capture the LONG position's quantity right after bar 7's reopen.
        probe = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        for i in range(8):
            probe(klines[: i + 1])
        quantity_before_flip = probe.position_quantity
        assert quantity_before_flip > 0

        flip_intent = intents[8]
        assert flip_intent is not None
        assert flip_intent.side == Side.SHORT  # new sign is -1
        assert strategy.position_sign == -1
        new_target_quantity = strategy.position_quantity
        assert flip_intent.quantity == quantity_before_flip + new_target_quantity

    def test_intent_shape(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        klines = _klines(_SCENARIO_CLOSES[:5])
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        opening = intents[4]
        assert opening.order_type == OrderType.GUARDED_MARKET
        assert opening.limit_price is None
        assert opening.signal_timeframe == "1d"
        assert opening.symbol == "BTC-USDT"


class TestSizingComposition:
    def test_quantity_matches_reference_equity_over_price_times_strength_times_vol_scalar(self):
        closes = ["100", "101", "99"]
        klines = _klines(closes)
        strategy = _strategy(lookbacks=(1, 2), vol_period=2)
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        opening = intents[2]
        assert opening is not None

        # Independently replicate the vol estimate/scalar the strategy's
        # own internal RollingRealizedVolatility must have produced, fed
        # the identical bar sequence.
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
        # ensemble at bar 2: lookbacks (1,2) -> sign(99-101)=-1, sign(99-100)=-1 -> avg=-1, abs=1
        expected_quantity = (DEFAULT_REFERENCE_EQUITY / entry_price) * Decimal(1) * vol_scalar
        assert opening.quantity == expected_quantity

    def test_weaker_conviction_produces_smaller_quantity(self):
        # A 0.5-strength ensemble should size to exactly half of a
        # 1.0-strength ensemble under otherwise identical price/vol
        # conditions. Isolated by feeding the SAME closes (so
        # RollingRealizedVolatility -- which depends only on price
        # history, never on `lookbacks` -- produces an identical reading
        # both times) to two strategies differing only in `lookbacks`:
        # (2,3,4) agrees fully on this path (|value|=1); (1,2,3,4) adds a
        # 4th lookback that disagrees (|value|=0.5).
        closes = ["100", "100", "100", "105", "101"]
        klines = _klines(closes)

        full_conviction = _strategy(lookbacks=(2, 3, 4), vol_period=2)
        full_intents = [full_conviction(klines[: i + 1]) for i in range(len(klines))]

        half_conviction = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        half_intents = [half_conviction(klines[: i + 1]) for i in range(len(klines))]

        full_opening = full_intents[-1]
        half_opening = half_intents[-1]
        assert full_opening is not None
        assert half_opening is not None
        # Not asserted via exact Decimal equality: `base_quantity *
        # Decimal(1) * vol_scalar` and `base_quantity * Decimal("0.5") *
        # vol_scalar` are two genuinely different multiplication chains
        # (different intermediate roundings under the ambient 28-
        # significant-digit Decimal context), so they can legitimately
        # differ in their final few digits even though both are
        # mathematically exactly half/full of the same underlying value.
        assert float(half_opening.quantity) == pytest.approx(float(full_opening.quantity) / 2, rel=1e-12)


class TestCannotSizeRetriesAutomatically:
    def test_stays_flat_while_vol_never_warms_despite_persistent_signal(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=1000)
        klines = _klines(_SCENARIO_CLOSES[:6])  # ensemble is -1 at both index 4 and 5
        intents = [strategy(klines[: i + 1]) for i in range(len(klines))]
        assert all(intent is None for intent in intents)
        assert strategy.position_sign == 0
        assert strategy.position_quantity == Decimal(0)

    def test_degenerate_price_flattens_a_stale_position_and_retries(self):
        strategy = _strategy(lookbacks=(1, 2, 3, 4), vol_period=2)
        # Drive to the LONG position established at scenario index 7.
        klines = _klines(_SCENARIO_CLOSES[:8])
        for i in range(len(klines)):
            strategy(klines[: i + 1])
        assert strategy.position_sign == 1
        held_quantity = strategy.position_quantity
        assert held_quantity > 0

        # A degenerate next bar (close <= 0) that would otherwise trigger a
        # reversal (ensemble flips to -1, same as the real scenario's bar
        # 8) cannot be sized -- the strategy must still flatten the stale
        # LONG rather than hold it, and must NOT crash trying to construct
        # an OrderIntent with a non-positive quantity/price.
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
        assert intent.side == Side.SHORT  # closing a LONG
        assert intent.quantity == held_quantity
        assert strategy.position_sign == 0
        assert strategy.position_quantity == Decimal(0)

        # Subsequent bars with valid positive prices and a persisting
        # nonzero ensemble sign retry the open automatically (retry
        # convention, same as the vol-never-warms case above) -- though not
        # necessarily on the very next bar: the zero-price bar's -100%
        # return still occupies a slot in RollingRealizedVolatility's own
        # rolling window (period=2, so it needs 2 valid consecutive-return
        # pairs; a pair straddling the zero price is itself skipped as
        # degenerate, so the window needs a few more genuinely valid bars
        # before it re-warms) -- a real, disclosed interaction between two
        # already-independently-correct pieces, not a bug in either.
        window = klines + [degenerate]
        retry_intent = None
        for offset, close in enumerate(("60", "65", "55"), start=9):
            window = window + [_daily_kline(offset, close, base_time=BASE_TIME)]
            retry_intent = strategy(window)
            if retry_intent is not None:
                break
        assert retry_intent is not None
        assert retry_intent.side == Side.SHORT
        assert strategy.position_sign == -1


class TestFitNoSearch:
    def _trainable(self, tmp_path, **overrides: object) -> DailyTsmomEnsembleTrainable:
        kwargs: dict[str, Any] = dict(
            strategy_id="test-daily-tsmom-ensemble",
            strategy_version="v1",
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            bars_per_day=1,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        kwargs.update(overrides)
        return DailyTsmomEnsembleTrainable(**kwargs)

    def test_fit_logs_exactly_one_candidate(self, tmp_path):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(300)
        trainable.fit(klines, {}, parent_run_id="parent-1")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-1"]
        assert len(candidate_records) == 1
        assert candidate_records[0]["candidate_index"] == 0
        assert candidate_records[0]["total_candidates"] == 1

    def test_fit_performs_no_search(self, tmp_path):
        # Passing an ignored "candidates" key (the grid-search convention
        # every sibling opt-in strategy honors) must have zero effect here
        # -- this Trainable has no grid at all, by the governing
        # pre-registration's own total_candidates: 1 requirement.
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(300)
        trainable.fit(klines, {"candidates": [(1,), (2,), (3,)]}, parent_run_id="parent-2")
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        candidate_records = [r for r in records if r.get("parent_run_id") == "parent-2"]
        assert len(candidate_records) == 1

    def test_fit_returns_fresh_strategy_bound_to_configured_lookbacks(self, tmp_path):
        trainable = self._trainable(tmp_path, lookbacks=(1, 2, 3, 4))
        klines = _flat_klines(300)
        result = trainable.fit(klines, {}, parent_run_id="parent-3")
        assert isinstance(result, DailyTsmomEnsembleStrategy)
        assert result.bars_seen == 0
        assert result.position_sign == 0
        assert result.lookbacks == (1, 2, 3, 4)

    def test_fit_never_reads_klines_beyond_train_klines(self, tmp_path, monkeypatch):
        trainable = self._trainable(tmp_path)
        klines = _flat_klines(300)

        import backtest.engine as engine_module

        real_run_backtest = engine_module.run_backtest
        seen_ids = []

        def spy(klines_arg, *args, **kwargs):
            seen_ids.append(id(klines_arg))
            return real_run_backtest(klines_arg, *args, **kwargs)

        monkeypatch.setattr("research.strategies.daily_tsmom_ensemble.run_backtest", spy)
        trainable.fit(klines, {}, parent_run_id="parent-4")
        assert all(kid == id(klines) for kid in seen_ids)

    def test_default_lookbacks_are_the_mop_2012_canonical_set(self):
        assert DEFAULT_LOOKBACKS == (21, 63, 126, 252)


class TestRunWalkForwardIntegrationSynthetic:
    """A small, fully synthetic (never real-data) sanity check that this
    strategy composes correctly with `research.walkforward.run_walk_forward`
    -- including that `bars_per_day=1` threads through without crashing or
    silently defaulting to the 15m/1h convention.
    """

    def test_walk_forward_runs_without_error_and_logs(self, tmp_path):
        klines = _klines(_SCENARIO_CLOSES * 4)  # 36 synthetic daily bars
        trainable = DailyTsmomEnsembleTrainable(
            strategy_id="test-daily-tsmom-ensemble-wf",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("2"),
            bars_per_day=1,
            lookbacks=(1, 2, 3, 4),
            vol_period=2,
            runs_path=str(tmp_path / "experiments.jsonl"),
        )
        result = run_walk_forward(
            klines,
            trainable,
            "test-daily-tsmom-ensemble-wf",
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
        assert result.aggregate["fold_count"] == len(result.folds)
        records = list(read_records(str(tmp_path / "experiments.jsonl")))
        walk_forward_records = [r for r in records if r.get("run_id") == result.run_id]
        assert len(walk_forward_records) == 1
        assert walk_forward_records[0]["walk_forward_config"]["bars_per_day"] == 1


class TestPreregistration:
    """Checks against the real, committed registration file --
    `configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`.
    This is a git-tracked JSON *config* (a cutoff timestamp and criteria,
    not price data) -- reading it is explicitly permitted even under this
    task's "no 1d kline data" rule. `test_preregistration.py`'s own
    `test_every_committed_registration_validates` already globs this file
    too (schema validation); the checks here are specific to this
    registration's content.
    """

    def test_registration_validates_and_loads(self):
        prereg = load_preregistration(PREREGISTRATION_PATH)
        assert prereg.preregistration_id == "daily-tsmom-ensemble-1d-holdout"
        assert prereg.strategy_id == "daily-tsmom-ensemble"
        assert prereg.strategy_family == "daily-tsmom"
        assert prereg.total_candidates == 1
        assert prereg.is_holdout_confirmation is True
        assert prereg.criterion_kind == "holdout_psr"

    def test_entry_point_resolves_to_the_real_trainable(self):
        prereg = load_preregistration(PREREGISTRATION_PATH)
        target = resolve_entry_point(prereg)
        assert target is DailyTsmomEnsembleTrainable

    def test_strategy_family_resolves_both_ways(self):
        prereg = load_preregistration(PREREGISTRATION_PATH)
        via_logged_record = resolve_family(prereg.strategy_id, {"strategy_family": prereg.strategy_family})
        assert via_logged_record.source == "logged"
        assert via_logged_record.family == "daily-tsmom"
        assert via_logged_record.purpose == "research"

        via_curated_map = resolve_family(prereg.strategy_id, None)
        assert via_curated_map.source == "curated_map"
        assert via_curated_map.family == "daily-tsmom"
        assert via_curated_map.citation == ".planning/sr-u-preregistered-attempt-spec.md"

    def test_data_window_matches_holdout_1d_config(self):
        prereg = load_preregistration(PREREGISTRATION_PATH)
        # 2021-05-14T00:00:00Z through 2024-04-27T00:00:00Z (exclusive) --
        # cross-checked against configs/research/holdout_1d.json's own
        # holdout_cutoff_ms, not by loading kline data.
        assert prereg.data["start_ms"] == 1620950400000
        assert prereg.data["end_ms"] == 1714176000000
        assert prereg.data["expected_bars"] == 1079
        assert prereg.data["holdout_config_path"] == "configs/research/holdout_1d.json"

    def test_trade_floor_matches_the_frequency_scaled_formula(self):
        from research.preregistration import frequency_scaled_min_trades

        prereg = load_preregistration(PREREGISTRATION_PATH)
        floor = frequency_scaled_min_trades(
            total_evaluated_bars=prereg.data["expected_bars"], bars_per_day=prereg.procedure["bars_per_day"]
        )
        assert floor == 53
        assert prereg.primary_criterion["min_total_trades"] == 53

    def test_declared_detection_floor_matches_recomputation(self):
        import math
        from statistics import NormalDist

        prereg = load_preregistration(PREREGISTRATION_PATH)
        years = prereg.data["expected_bars"] / prereg.procedure["bars_per_day"] / 365
        floor = NormalDist().inv_cdf(0.95) / math.sqrt(years)
        assert prereg.config["declared_detection_floor_sharpe"] == pytest.approx(floor, abs=1e-3)

    def test_free_parameter_count_is_zero(self):
        prereg = load_preregistration(PREREGISTRATION_PATH)
        assert prereg.config["free_parameter_count"] == 0
        candidates = prereg.candidates()
        assert len(candidates) == 1
        assert candidates[0]["lookbacks"] == [21, 63, 126, 252]
