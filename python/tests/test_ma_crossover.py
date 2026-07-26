"""Tests for `python/research/strategies/ma_crossover.py` -- Task D's
placeholder MA-crossover `TrainableStrategy`.

See CLAUDE.md's "Strategy Research Operational Design" section, "Build
sequencing" (Task D) subsection, and `.planning/sr-d-placeholder-
strategy.md` for the design this module implements. Written first
(TDD): this file existed and failed on `ModuleNotFoundError` before
`python/research/strategies/ma_crossover.py` did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies import ma_crossover
from research.strategies.ma_crossover import MACrossoverTrainable, MovingAverageCrossoverStrategy
from research.walkforward import run_walk_forward
from schemas.order_intent import Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ramp_klines(count: int, start: int = 100, base_time: datetime = BASE_TIME) -> list[Kline]:
    """A strictly monotonically increasing close-price sequence -- open =
    high = low = close, so fill mechanics don't matter, only the closes
    the strategy actually reacts to.
    """
    return [
        Kline(
            open_time=base_time + timedelta(minutes=15 * i),
            open=Decimal(start + i),
            high=Decimal(start + i),
            low=Decimal(start + i),
            close=Decimal(start + i),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


def _klines_from_closes(closes: list[Decimal], base_time: datetime = BASE_TIME) -> list[Kline]:
    return [
        Kline(
            open_time=base_time + timedelta(minutes=15 * i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1"),
        )
        for i, close in enumerate(closes)
    ]


def _feed_strategy(strategy: MovingAverageCrossoverStrategy, closes: list[Decimal]) -> list[int | None]:
    """Feeds `strategy` one synthetic single-bar window per close (the
    strategy is stateful -- it maintains its own rolling close history --
    so a length-1 window per call, growing chronologically bar by bar, is
    exactly how `backtest.engine.run_backtest` would drive it). Returns,
    per bar, +1 for a LONG intent, -1 for a SHORT intent, None for no
    intent.
    """
    klines = _klines_from_closes(closes)
    fired: list[int | None] = []
    for kline in klines:
        intent = strategy([kline])
        if intent is None:
            fired.append(None)
        else:
            fired.append(1 if intent.side == Side.LONG else -1)
    return fired


def _reference_signal_sequence(closes: list[Decimal], fast: int, slow: int) -> list[int | None]:
    """Independent, deliberately naive reference: recomputes both SMAs
    from scratch against the full closes-so-far prefix at every step (no
    incremental/stateful bookkeeping at all), used to cross-check
    `MovingAverageCrossoverStrategy`'s own stateful implementation agrees
    bar-for-bar. Returns, per bar, +1/-1 for a bullish/bearish crossing
    signal, or `None` for no signal (including every warmup bar and every
    bar within an already-established regime).
    """
    signals: list[int | None] = []
    last_regime_sign: int | None = None
    for i in range(len(closes)):
        window = closes[: i + 1]
        if len(window) < slow:
            signals.append(None)
            continue
        slow_sma = sum(window[-slow:]) / slow
        fast_sma = sum(window[-fast:]) / fast
        if fast_sma > slow_sma:
            sign = 1
        elif fast_sma < slow_sma:
            sign = -1
        else:
            sign = 0

        if last_regime_sign is not None and sign != 0 and sign != last_regime_sign:
            signals.append(sign)
        else:
            signals.append(None)
        if sign != 0:
            last_regime_sign = sign
    return signals


# ---------------------------------------------------------------------------
# MovingAverageCrossoverStrategy -- construction validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fast, slow",
    [
        (0, 10),
        (-1, 10),
        (5, 0),
        (5, -1),
        (10, 10),  # fast must be strictly less than slow
        (15, 10),
    ],
)
def test_rejects_invalid_fast_slow_window_lengths(fast, slow):
    with pytest.raises(ValueError):
        MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT")


# ---------------------------------------------------------------------------
# MovingAverageCrossoverStrategy -- signal correctness
# ---------------------------------------------------------------------------


def test_no_signal_before_slow_window_of_history_accumulates():
    fast, slow = 3, 5
    closes = [Decimal(100 + i) for i in range(4)]  # one short of slow=5

    strategy = MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT")
    fired = _feed_strategy(strategy, closes)

    assert fired == [None, None, None, None]


def test_crossover_signal_matches_independent_reference_over_a_zigzag_series():
    # Rises for 20 bars, falls for 40, rises again for 20 -- engineered so
    # both a bearish and a bullish crossing get exercised in one run, not
    # just a single up-only (or down-only) ramp.
    up = list(range(100, 120))
    down = list(range(119, 79, -1))
    up_again = list(range(80, 100))
    closes = [Decimal(v) for v in up + down + up_again]
    fast, slow = 3, 7

    strategy = MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT")
    actual = _feed_strategy(strategy, closes)
    expected = _reference_signal_sequence(closes, fast, slow)

    assert actual == expected
    # Sanity: the engineered zigzag must actually have produced a signal
    # of each direction, otherwise this test isn't exercising what it
    # claims to.
    assert 1 in expected
    assert -1 in expected


def test_does_not_fire_every_bar_while_already_in_the_same_regime():
    # Establish a bearish regime via a genuine reversal (a rise, then a
    # long fall), then keep falling for many more bars. A level-triggered
    # (buggy) implementation would emit a fresh SHORT intent on every one
    # of those extra bars; edge-triggered must emit exactly one.
    rising = list(range(100, 115))
    falling = list(range(114, 40, -1))
    closes = [Decimal(v) for v in rising + falling]
    fast, slow = 3, 7

    strategy = MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT")
    fired = _feed_strategy(strategy, closes)
    signals = [s for s in fired if s is not None]

    assert signals.count(-1) == 1
    # The very first (rising) regime is the established baseline, not a
    # detected "crossing" -- there was nothing before it to cross from.
    assert 1 not in signals


def test_emitted_intent_uses_guarded_market_and_the_given_quantity_symbol():
    fast, slow = 3, 7
    quantity = Decimal("0.02")
    symbol = "BTC-USDT"
    rising = [Decimal(v) for v in range(100, 108)]
    falling = [Decimal(v) for v in range(107, 90, -1)]

    strategy = MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=quantity, symbol=symbol)
    klines = _klines_from_closes(rising + falling)
    intents = [strategy([k]) for k in klines]
    fired = [i for i in intents if i is not None]

    assert len(fired) >= 1
    for intent in fired:
        assert intent.order_type.value == "GUARDED_MARKET"
        assert intent.quantity == quantity
        assert intent.symbol == symbol
        assert intent.limit_price is None


# ---------------------------------------------------------------------------
# MACrossoverTrainable.fit
# ---------------------------------------------------------------------------


def _trainable(tmp_path, **overrides) -> MACrossoverTrainable:
    kwargs = dict(
        strategy_id="ma-crossover-test",
        strategy_version="v1",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=tmp_path / "experiments.jsonl",
    )
    kwargs.update(overrides)
    return MACrossoverTrainable(**kwargs)


def test_fit_raises_on_empty_candidate_grid(tmp_path):
    trainable = _trainable(tmp_path)

    with pytest.raises(ValueError):
        trainable.fit(_ramp_klines(10), {"candidates": []}, parent_run_id="p1")


def test_fit_uses_default_candidate_grid_when_params_omit_candidates(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    trainable = _trainable(tmp_path, runs_path=runs_path)
    train_klines = _ramp_klines(80)

    trainable.fit(train_klines, {}, parent_run_id="p1")

    records = [r for r in read_records(runs_path) if r["record_type"] == "backtest_run"]
    assert len(records) == len(ma_crossover.DEFAULT_CANDIDATE_GRID)


def test_fit_only_ever_backtests_against_train_klines(monkeypatch, tmp_path):
    train_klines = _ramp_klines(40)
    captured_klines_args = []
    original_run_backtest = ma_crossover.run_backtest

    def _spy_run_backtest(klines, strategy, fee_bps, slippage_bps):
        captured_klines_args.append(klines)
        return original_run_backtest(klines, strategy, fee_bps, slippage_bps)

    monkeypatch.setattr(ma_crossover, "run_backtest", _spy_run_backtest)

    trainable = _trainable(tmp_path)
    params = {"candidates": [(3, 8), (5, 13)]}

    trainable.fit(train_klines, params, parent_run_id="p1")

    # One backtest per candidate, and every single one of them ran against
    # exactly the given train_klines object -- never a copy, never a
    # different (e.g. validate) list. fit()'s own signature has no
    # validate_klines parameter at all, so this is also structural, not
    # just conventional.
    assert len(captured_klines_args) == 2
    assert all(klines_arg is train_klines for klines_arg in captured_klines_args)


def test_fit_picks_the_best_scoring_candidate_by_total_return(tmp_path):
    # A trough shape (decline then rise, 99 bars total): (3, 10)
    # establishes a bearish baseline during the decline (no signal --
    # nothing to have crossed from yet), then fires exactly one bullish
    # cross once the rise overtakes it, held to a profitable force-closed
    # trade at the final bar (entered near the trough, exits near the
    # top). (3, 100): slow=100 exceeds the 99-bar train window, so it
    # never accumulates enough history to fire at all -- total_return is
    # exactly 0 (no evidence, not a loss). (3, 10) must win.
    decline = list(range(200, 150, -1))
    rise = list(range(151, 200))
    closes = [Decimal(v) for v in decline + rise]
    train_klines = _klines_from_closes(closes)
    params = {"candidates": [(3, 100), (3, 10)], "quantity": "0.5", "symbol": "BTC-USDT"}
    trainable = _trainable(tmp_path)

    bound = trainable.fit(train_klines, params, parent_run_id="p1")

    assert isinstance(bound, MovingAverageCrossoverStrategy)
    assert bound.fast_window == 3
    assert bound.slow_window == 10


def test_fit_returns_a_fresh_strategy_instance_not_a_scoring_candidate_reused(tmp_path):
    # The candidate instance used to score (3, 10) in-sample runs a full
    # 80-bar backtest during fit() -- if fit() handed that same object
    # back, it would already carry leftover internal state (rolling
    # closes, last regime sign) into whatever klines it's called against
    # next (e.g. a validate fold). A fresh instance has seen zero bars.
    train_klines = _ramp_klines(80)
    params = {"candidates": [(3, 100), (3, 10)]}
    trainable = _trainable(tmp_path)

    bound = trainable.fit(train_klines, params, parent_run_id="p1")

    assert bound.bars_seen == 0


# ---------------------------------------------------------------------------
# MACrossoverTrainable.fit -- per-candidate experiment logging
# ---------------------------------------------------------------------------


def test_fit_logs_one_backtest_run_entry_per_candidate_with_correct_lineage(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    train_klines = _ramp_klines(40)
    candidates = [(3, 8), (5, 13), (3, 100)]  # last one never fires
    trainable = _trainable(tmp_path, strategy_id="ma-crossover-lineage", runs_path=runs_path)

    trainable.fit(
        train_klines,
        {"candidates": candidates, "quantity": "0.01", "symbol": "BTC-USDT"},
        parent_run_id="parent-xyz",
    )

    records = [r for r in read_records(runs_path) if r["record_type"] == "backtest_run"]
    assert len(records) == len(candidates)

    seen_indices = sorted(r["candidate_index"] for r in records)
    assert seen_indices == [0, 1, 2]

    for record in records:
        assert record["parent_run_id"] == "parent-xyz"
        assert record["total_candidates"] == len(candidates)
        assert record["strategy_id"] == "ma-crossover-lineage"
        assert record["is_holdout_run"] is False
        fast, slow = candidates[record["candidate_index"]]
        assert record["params"]["fast"] == fast
        assert record["params"]["slow"] == slow


def test_fit_logs_nothing_beyond_the_per_candidate_records(tmp_path):
    # fit() alone (not routed through run_walk_forward) must not also log
    # some separate "overall" record -- only the per-candidate entries.
    runs_path = tmp_path / "experiments.jsonl"
    trainable = _trainable(tmp_path, runs_path=runs_path)

    trainable.fit(_ramp_klines(40), {"candidates": [(3, 8), (5, 13)]}, parent_run_id="p1")

    records = list(read_records(runs_path))
    assert len(records) == 2
    assert all(r["record_type"] == "backtest_run" for r in records)


# ---------------------------------------------------------------------------
# Integration: run_walk_forward + MACrossoverTrainable end to end (small,
# synthetic scale -- the real BingX end-to-end pass is run separately, see
# .planning/sr-d-placeholder-strategy.md, not as a unit test).
# ---------------------------------------------------------------------------


def test_run_walk_forward_with_ma_crossover_trainable_logs_per_candidate_and_final_records(tmp_path):
    klines = _ramp_klines(60)
    runs_path = tmp_path / "experiments.jsonl"
    candidates = [(3, 8), (5, 13)]
    trainable = _trainable(tmp_path, strategy_id="ma-crossover", runs_path=runs_path)

    result = run_walk_forward(
        klines,
        trainable,
        "ma-crossover",
        "v1",
        {"candidates": candidates, "quantity": "0.01", "symbol": "BTC-USDT"},
        train_bars=20,
        validate_bars=10,
        step_bars=10,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=runs_path,
    )

    assert len(result.folds) > 0  # sanity: this scenario actually produces folds

    records = list(read_records(runs_path))
    backtest_runs = [r for r in records if r["record_type"] == "backtest_run"]
    candidate_records = [r for r in backtest_runs if r["parent_run_id"] is not None]
    final_records = [r for r in backtest_runs if r["parent_run_id"] is None]

    assert len(final_records) == 1
    assert final_records[0]["run_id"] == result.run_id
    assert len(candidate_records) == len(result.folds) * len(candidates)
    for record in candidate_records:
        assert record["parent_run_id"] == result.run_id
        assert record["total_candidates"] == len(candidates)
        assert record["candidate_index"] in (0, 1)
