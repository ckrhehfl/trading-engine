"""Tests for `python/research/walkforward.py`.

See CLAUDE.md's "Strategy Research Operational Design" section,
"Walk-forward harness" subsection, for the full design this module
implements.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backtest.kline import Kline
from research.walkforward import generate_folds, run_walk_forward
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _klines(count: int, start_price: int = 100) -> list[Kline]:
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=15 * i),
            open=Decimal(start_price + i),
            high=Decimal(start_price + i + 1),
            low=Decimal(start_price + i - 1),
            close=Decimal(start_price + i) + Decimal("0.5"),
            volume=Decimal("100"),
        )
        for i in range(count)
    ]


class _RecordingStrategy:
    """A `TrainableStrategy` test double that never trades but records
    every `train_klines`/`params`/`parent_run_id` it was fit with -- used
    to prove `run_walk_forward` calls `fit` once per fold with exactly
    that fold's train window, never leaks validate data into it, and
    passes its own `run_id` through as `parent_run_id` (see
    `TrainableStrategy.fit`'s docstring -- this is the interface gap Task
    D's `parent_run_id` parameter closes).
    """

    def __init__(self):
        self.fit_calls: list[list[Kline]] = []
        self.params_seen: list[dict] = []
        self.parent_run_ids_seen: list[str] = []

    def fit(self, train_klines, params, *, parent_run_id=None):
        self.fit_calls.append(list(train_klines))
        self.params_seen.append(dict(params))
        self.parent_run_ids_seen.append(parent_run_id)

        def _strategy(visible_klines):
            return None

        return _strategy


class _BuyAndHoldStrategy:
    """A `TrainableStrategy` test double that buys once on the first bar
    it's shown (of whatever `Sequence[Kline]` it's called with -- i.e.
    the validate window at evaluation time) and never sells, so
    `run_backtest`'s force-close-at-final-bar rule always yields exactly
    one closed trade per fold. Ignores `parent_run_id` -- it has nothing
    of its own to log, which `TrainableStrategy.fit`'s docstring says is
    a valid thing for an implementation to do.
    """

    def fit(self, train_klines, params, *, parent_run_id=None):
        def _strategy(visible_klines):
            if len(visible_klines) != 1:
                return None
            return OrderIntent(
                intent_id=uuid4(),
                symbol="BTC-USDT",
                side=Side.LONG,
                order_type=OrderType.GUARDED_MARKET,
                quantity=Decimal("1"),
                limit_price=None,
                signal_timeframe="15m",
                created_at=visible_klines[-1].open_time,
            )

        return _strategy


# ---------------------------------------------------------------------------
# generate_folds
# ---------------------------------------------------------------------------


def test_generate_folds_produces_expected_boundaries_for_non_overlapping_default():
    folds = generate_folds(n_bars=12, train_bars=4, validate_bars=2, step_bars=2)

    assert [
        (f.train_start_index, f.train_end_index, f.validate_start_index, f.validate_end_index) for f in folds
    ] == [
        (0, 4, 4, 6),
        (2, 6, 6, 8),
        (4, 8, 8, 10),
        (6, 10, 10, 12),
    ]


def test_generate_folds_validate_windows_are_non_overlapping_and_contiguous_by_default():
    # step == validate_bars is the documented default -- adjacent validate
    # windows must exactly abut, never overlap or leave a gap.
    folds = generate_folds(n_bars=12, train_bars=4, validate_bars=2, step_bars=2)

    for prev_fold, next_fold in zip(folds, folds[1:]):
        assert prev_fold.validate_end_index == next_fold.validate_start_index


def test_generate_folds_matches_the_real_bingx_depth_finding_from_task_a():
    # Locks in the exact number .planning/sr-a-data-pipeline.md and
    # CLAUDE.md's design flagged: the provisional default windows
    # (train=8640, validate=2880, step=2880) over the real ~24,199-bar
    # depth produce 5 folds, short of the 8-10 credibility floor.
    folds = generate_folds(n_bars=24199, train_bars=8640, validate_bars=2880, step_bars=2880)

    assert len(folds) == 5


def test_generate_folds_returns_empty_list_when_data_is_too_short():
    assert generate_folds(n_bars=5, train_bars=4, validate_bars=2, step_bars=2) == []


def test_generate_folds_returns_empty_list_for_zero_bars():
    assert generate_folds(n_bars=0, train_bars=4, validate_bars=2, step_bars=2) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(train_bars=0, validate_bars=2, step_bars=2),
        dict(train_bars=4, validate_bars=0, step_bars=2),
        dict(train_bars=4, validate_bars=2, step_bars=0),
        dict(train_bars=-1, validate_bars=2, step_bars=2),
        dict(train_bars=4, validate_bars=-1, step_bars=2),
        dict(train_bars=4, validate_bars=2, step_bars=-1),
    ],
)
def test_generate_folds_rejects_non_positive_sizes(kwargs):
    with pytest.raises(ValueError):
        generate_folds(n_bars=20, **kwargs)


def test_generate_folds_allows_overlapping_validate_windows_when_step_is_smaller_than_validate_bars():
    folds = generate_folds(n_bars=10, train_bars=4, validate_bars=4, step_bars=2)

    assert len(folds) == 2
    assert (folds[0].validate_start_index, folds[0].validate_end_index) == (4, 8)
    assert (folds[1].validate_start_index, folds[1].validate_end_index) == (6, 10)


def test_generate_folds_uses_pure_bar_count_arithmetic_not_calendar_splitting():
    # Boundaries are pure index arithmetic -- this is exercised implicitly
    # by every test above (no datetime ever enters generate_folds), but
    # asserted explicitly here: fold indices are identical regardless of
    # what `n_bars` "means" in wall-clock time.
    folds = generate_folds(n_bars=100, train_bars=30, validate_bars=10, step_bars=10)
    assert folds[0].train_start_index == 0
    assert folds[0].train_end_index == 30
    assert folds[-1].validate_end_index <= 100


# ---------------------------------------------------------------------------
# run_walk_forward
# ---------------------------------------------------------------------------


def test_run_walk_forward_fit_is_called_once_per_fold_with_only_that_folds_train_klines(tmp_path):
    klines = _klines(12)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {"x": 1},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    assert len(strategy.fit_calls) == 4
    for fit_train_klines, fold in zip(strategy.fit_calls, result.folds):
        assert fit_train_klines == klines[fold.train_start_index : fold.train_end_index]
    assert all(params == {"x": 1} for params in strategy.params_seen)


def test_run_walk_forward_passes_its_own_run_id_to_fit_as_parent_run_id(tmp_path):
    # The interface gap closed for Task D: fit() has no other way to learn
    # the run_id its own run_walk_forward call will log under, so it must
    # be handed the same value every fold call receives, and that value
    # must match what actually ends up in the logged record (result.run_id).
    klines = _klines(12)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {"x": 1},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    assert len(strategy.parent_run_ids_seen) == 4
    assert all(parent_run_id == result.run_id for parent_run_id in strategy.parent_run_ids_seen)
    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["run_id"] == result.run_id


def test_run_walk_forward_fit_never_receives_validate_klines(tmp_path):
    klines = _klines(12)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    # For each fold, none of the klines fit() saw share an open_time with
    # that fold's validate window.
    for fit_train_klines, fold in zip(strategy.fit_calls, result.folds):
        validate_open_times = {k.open_time for k in klines[fold.validate_start_index : fold.validate_end_index]}
        seen_open_times = {k.open_time for k in fit_train_klines}
        assert seen_open_times.isdisjoint(validate_open_times)


def test_run_walk_forward_evaluates_via_run_backtest_against_validate_klines(tmp_path):
    klines = _klines(16)
    strategy = _BuyAndHoldStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=4,
        step_bars=4,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    assert len(result.folds) == 3
    for fold in result.folds:
        # Rising prices + buy-and-hold + force-close at the fold's final
        # bar => exactly one profitable closed trade per fold.
        assert fold.metrics.num_trades == 1
        assert fold.metrics.closed_trades[0].realized_pnl > 0


def test_run_walk_forward_passes_bars_per_day_through_to_fold_sharpe_annualization(tmp_path):
    """`bars_per_day` (default 96, matching the pre-Task-F 15m-only
    assumption) must reach each fold's `compute_metrics` call -- added for
    the native-1h strategy variant (`research/strategies/
    hourly_momentum.py`), see `.planning/sr-f-risk-management-and-1h-
    variant.md`. Without this, every 1h walk-forward run would silently
    report a 2x-inflated Sharpe (the 15m annualization factor applied to
    1h-frequency returns).
    """
    klines = _klines(16)

    result_default = run_walk_forward(
        klines,
        _BuyAndHoldStrategy(),
        "strat-bars-per-day",
        "v1",
        {},
        train_bars=4,
        validate_bars=4,
        step_bars=4,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=tmp_path / "experiments_default.jsonl",
    )
    result_1h = run_walk_forward(
        klines,
        _BuyAndHoldStrategy(),
        "strat-bars-per-day",
        "v1",
        {},
        train_bars=4,
        validate_bars=4,
        step_bars=4,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        bars_per_day=24,
        runs_path=tmp_path / "experiments_1h.jsonl",
    )

    assert len(result_default.folds) == len(result_1h.folds) == 3
    for fold_default, fold_1h in zip(result_default.folds, result_1h.folds, strict=True):
        assert fold_default.metrics.sharpe_ratio is not None
        assert fold_1h.metrics.sharpe_ratio is not None
        assert fold_1h.metrics.sharpe_ratio == pytest.approx(fold_default.metrics.sharpe_ratio / 2)


def test_run_walk_forward_with_zero_folds_still_logs_exactly_one_record(tmp_path):
    klines = _klines(5)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=4,
        step_bars=4,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    assert result.folds == []
    assert strategy.fit_calls == []
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["walk_forward_config"]["fold_count"] == 0
    assert record["fold_results"] == []


def test_run_walk_forward_result_run_id_matches_the_logged_record(tmp_path):
    klines = _klines(12)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {"x": 1},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["record_type"] == "backtest_run"
    assert record["run_id"] == result.run_id
    assert record["strategy_id"] == "strat-1"
    assert record["strategy_version"] == "v1"
    assert record["params"] == {"x": 1}
    assert record["is_holdout_run"] is False


def test_run_walk_forward_passes_through_lineage_and_holdout_flag(tmp_path):
    klines = _klines(12)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        is_holdout_run=True,
        parent_run_id="grid-1",
        candidate_index=2,
        total_candidates=5,
        runs_path=runs_path,
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["is_holdout_run"] is True
    assert record["parent_run_id"] == "grid-1"
    assert record["candidate_index"] == 2
    assert record["total_candidates"] == 5


def test_run_walk_forward_logs_the_full_input_data_range(tmp_path):
    klines = _klines(12)
    strategy = _RecordingStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    record = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["data_range"]["num_bars"] == 12
    assert record["data_range"]["start_ms"] == int(klines[0].open_time.timestamp() * 1000)
    assert record["data_range"]["end_ms"] == int(klines[-1].open_time.timestamp() * 1000)


def test_run_walk_forward_aggregate_treats_null_sharpe_folds_as_not_positive(tmp_path):
    klines = _klines(12)
    strategy = _RecordingStrategy()  # never trades -> every fold's sharpe is None
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=2,
        step_bars=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    assert result.aggregate["fold_count"] == 4
    assert result.aggregate["all_folds_positive_sharpe"] is False
    assert result.aggregate["mean_sharpe"] is None
    assert result.aggregate["min_sharpe"] is None
    assert result.aggregate["total_trades"] == 0


def test_run_walk_forward_aggregate_all_folds_positive_sharpe_true_when_every_fold_is_positive(tmp_path):
    klines = _klines(16)
    strategy = _BuyAndHoldStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=4,
        step_bars=4,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    # Buy-and-hold on strictly rising prices: every fold's equity curve is
    # monotonically increasing, so every fold should have a defined,
    # positive Sharpe.
    assert result.aggregate["fold_count"] == 3
    assert all(fold.metrics.sharpe_ratio is not None and fold.metrics.sharpe_ratio > 0 for fold in result.folds)
    assert result.aggregate["all_folds_positive_sharpe"] is True
    assert result.aggregate["total_trades"] == 3


def test_run_walk_forward_aggregate_has_the_fields_needed_for_the_eligibility_bar(tmp_path):
    klines = _klines(16)
    strategy = _BuyAndHoldStrategy()
    runs_path = tmp_path / "experiments.jsonl"

    result = run_walk_forward(
        klines,
        strategy,
        "strat-1",
        "v1",
        {},
        train_bars=4,
        validate_bars=4,
        step_bars=4,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    # CLAUDE.md's Eligibility Bar needs: per-fold positive Sharpe, fold
    # count, per-fold/aggregate max drawdown, total trade count, profit
    # factor. run_walk_forward doesn't evaluate the bar itself (not this
    # task's job) but must log what a later evaluator needs.
    expected_keys = {
        "fold_count",
        "mean_sharpe",
        "min_sharpe",
        "all_folds_positive_sharpe",
        "worst_fold_max_drawdown",
        "total_trades",
        "mean_profit_factor",
        "min_profit_factor",
    }
    assert expected_keys.issubset(result.aggregate.keys())


def test_run_walk_forward_propagates_generate_folds_valueerror():
    # Sanity guard: run_walk_forward must not silently swallow a
    # generate_folds ValueError (e.g. an invalid train/validate/step
    # combination) -- it should propagate, not be caught and hidden.
    klines = _klines(12)
    strategy = _RecordingStrategy()

    with pytest.raises(ValueError, match="train_bars"):
        run_walk_forward(
            klines,
            strategy,
            "strat-1",
            "v1",
            {},
            train_bars=0,
            validate_bars=2,
            step_bars=2,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )


def test_run_walk_forward_rejects_negative_fee_bps():
    klines = _klines(12)
    strategy = _RecordingStrategy()

    with pytest.raises(ValueError, match="fee_bps"):
        run_walk_forward(
            klines,
            strategy,
            "strat-1",
            "v1",
            {},
            train_bars=4,
            validate_bars=2,
            step_bars=2,
            fee_bps=Decimal("-1"),
            slippage_bps=Decimal("0"),
        )


def test_run_walk_forward_rejects_negative_slippage_bps():
    klines = _klines(12)
    strategy = _RecordingStrategy()

    with pytest.raises(ValueError, match="slippage_bps"):
        run_walk_forward(
            klines,
            strategy,
            "strat-1",
            "v1",
            {},
            train_bars=4,
            validate_bars=2,
            step_bars=2,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("-1"),
        )


def test_run_walk_forward_requires_train_validate_step_fee_slippage_to_be_keyword_only():
    # Locks in the CodeRabbit-requested hardening: five adjacent
    # same-typed positional parameters (three bare ints, two Decimals)
    # are exactly the shape a transposed-argument bug hides in, so
    # calling with them positional must be a TypeError, not silently
    # accepted.
    klines = _klines(12)
    strategy = _RecordingStrategy()

    with pytest.raises(TypeError):
        run_walk_forward(
            klines,
            strategy,
            "strat-1",
            "v1",
            {},
            4,
            2,
            2,
            Decimal("0"),
            Decimal("0"),
        )
