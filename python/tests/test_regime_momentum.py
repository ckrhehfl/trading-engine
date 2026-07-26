"""Tests for `python/research/strategies/regime_momentum.py` -- the
regime-filtered 15m momentum strategy (the first real strategy hypothesis
in this project, not a pipeline-validation placeholder).

See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-e-regime-momentum.md` for the design this module
implements. Written first (TDD): this file existed and failed on
`ModuleNotFoundError` before `research/strategies/regime_momentum.py`
did.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.experiment_log import read_records
from research.strategies import regime_momentum
from research.strategies.regime_momentum import (
    HourlyResampler,
    RegimeMomentumStrategy,
    RegimeMomentumTrainable,
)
from research.walkforward import run_walk_forward
from schemas.order_intent import OrderType, Side

# Hour-aligned (minute == 0) so every test that doesn't specifically
# exercise the leading-partial-hour-discard behavior starts cleanly.
BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _kline(open_time: datetime, o: str, h: str, lo: str, c: str, v: str = "1") -> Kline:
    return Kline(
        open_time=open_time,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal(v),
    )


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


def _ramp_klines(count: int, start: int = 100, base_time: datetime = BASE_TIME) -> list[Kline]:
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


def _feed_strategy(
    strategy: RegimeMomentumStrategy, closes: list[Decimal], base_time: datetime = BASE_TIME
) -> list[int | None]:
    """Feeds one synthetic single-bar window per close -- the strategy is
    stateful, so this is exactly how `backtest.engine.run_backtest` would
    drive it. Returns, per bar, +1 for a LONG intent, -1 for a SHORT
    intent, None for no intent.
    """
    klines = _klines_from_closes(closes, base_time)
    fired: list[int | None] = []
    for kline in klines:
        intent = strategy([kline])
        fired.append(None if intent is None else (1 if intent.side == Side.LONG else -1))
    return fired


def _reference_crossover_sign_sequence(closes: list[Decimal], fast: int, slow: int) -> list[int | None]:
    """Independent, deliberately naive reference for the raw (ungated)
    15m fast/slow crossover sign -- recomputes both SMAs from scratch
    against the full closes-so-far prefix every bar, no incremental
    state. Same shape as `test_ma_crossover.py`'s own reference.
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


def _reference_hourly_completions(closes: list[Decimal], base_time: datetime) -> list[tuple[int, Decimal]]:
    """Independent reference for which 15m-bar index each synthetic 1h
    candle completes at, and its close -- aggregating every 4 consecutive
    bars starting from the first hour-aligned (`minute == 0`) bar. No
    code shared with `HourlyResampler`.
    """
    completions: list[tuple[int, Decimal]] = []
    pending: list[Decimal] = []
    started = False
    for i, close in enumerate(closes):
        open_time = base_time + timedelta(minutes=15 * i)
        if not started:
            if open_time.minute != 0:
                continue
            started = True
        pending.append(close)
        if len(pending) == 4:
            completions.append((i, pending[-1]))
            pending = []
    return completions


def _reference_regime_sequence(
    closes: list[Decimal], base_time: datetime, regime_sma_length: int
) -> list[str | None]:
    completions = _reference_hourly_completions(closes, base_time)
    result: list[str | None] = [None] * len(closes)
    comp_idx = 0
    current_regime: str | None = None
    hourly_closes: list[Decimal] = []
    for i in range(len(closes)):
        if comp_idx < len(completions) and completions[comp_idx][0] == i:
            hourly_closes.append(completions[comp_idx][1])
            comp_idx += 1
            if len(hourly_closes) >= regime_sma_length:
                recent = hourly_closes[-regime_sma_length:]
                sma = sum(recent) / regime_sma_length
                latest = recent[-1]
                if latest > sma:
                    current_regime = "up"
                elif latest < sma:
                    current_regime = "down"
                else:
                    current_regime = None
        result[i] = current_regime
    return result


def _reference_full_signal_sequence(
    closes: list[Decimal], base_time: datetime, fast: int, slow: int, regime_sma_length: int
) -> list[int | None]:
    raw = _reference_crossover_sign_sequence(closes, fast, slow)
    regimes = _reference_regime_sequence(closes, base_time, regime_sma_length)
    result: list[int | None] = []
    for sign, regime in zip(raw, regimes):
        if sign == 1 and regime == "up":
            result.append(1)
        elif sign == -1 and regime == "down":
            result.append(-1)
        else:
            result.append(None)
    return result


# ---------------------------------------------------------------------------
# HourlyResampler
# ---------------------------------------------------------------------------


def test_hourly_resampler_returns_none_while_hour_still_forming():
    resampler = HourlyResampler()
    bars = [
        _kline(BASE_TIME, "100", "101", "99", "100.5"),
        _kline(BASE_TIME + timedelta(minutes=15), "100.5", "102", "100", "101"),
        _kline(BASE_TIME + timedelta(minutes=30), "101", "103", "100.5", "102"),
    ]
    results = [resampler.update(b) for b in bars]
    assert results == [None, None, None]


def test_hourly_resampler_aggregates_ohlcv_correctly_on_the_fourth_bar():
    resampler = HourlyResampler()
    bars = [
        _kline(BASE_TIME, "100", "105", "95", "101", "10"),
        _kline(BASE_TIME + timedelta(minutes=15), "101", "110", "100", "108", "20"),
        _kline(BASE_TIME + timedelta(minutes=30), "108", "109", "90", "95", "5"),
        _kline(BASE_TIME + timedelta(minutes=45), "95", "97", "80", "85", "8"),
    ]
    results = [resampler.update(b) for b in bars]
    assert results[:3] == [None, None, None]
    candle = results[3]
    assert candle is not None
    assert candle.open_time == BASE_TIME
    assert candle.open == Decimal("100")  # first bar's open
    assert candle.high == Decimal("110")  # max of the 4 highs
    assert candle.low == Decimal("80")  # min of the 4 lows
    assert candle.close == Decimal("85")  # 4th bar's close
    assert candle.volume == Decimal("43")  # sum of the 4 volumes


def test_hourly_resampler_starts_a_fresh_group_after_completion():
    resampler = HourlyResampler()
    hour1 = [
        _kline(BASE_TIME + timedelta(minutes=15 * i), "1", "1", "1", str(i), "1") for i in range(4)
    ]
    hour2 = [
        _kline(BASE_TIME + timedelta(minutes=60 + 15 * i), "1", "1", "1", str(10 + i), "1")
        for i in range(4)
    ]
    for b in hour1:
        resampler.update(b)
    results = [resampler.update(b) for b in hour2]
    assert results[:3] == [None, None, None]
    assert results[3] is not None
    assert results[3].open_time == BASE_TIME + timedelta(minutes=60)
    assert results[3].close == Decimal("13")


def test_hourly_resampler_discards_leading_bars_before_first_hour_alignment():
    # Real BingX depth starts mid-hour (e.g. 2025-11-16T03:45:00Z) -- the
    # lone :45 bar before the next :00 boundary must never be folded into
    # a group.
    start = BASE_TIME + timedelta(minutes=45)  # :45
    next_hour = start + timedelta(minutes=15)  # lands exactly on :00
    bars = [_kline(start, "1", "1", "1", "999")]
    bars += [
        _kline(next_hour + timedelta(minutes=15 * i), "2", "2", "2", str(50 + i), "1") for i in range(4)
    ]

    resampler = HourlyResampler()
    results = [resampler.update(b) for b in bars]
    assert results[0] is None  # the :45 bar is discarded, not buffered
    assert results[1:4] == [None, None, None]
    candle = results[4]
    assert candle is not None
    assert candle.open_time == next_hour
    assert candle.close == Decimal("53")


def test_hourly_resampler_resets_on_a_gap_in_the_15m_stream():
    resampler = HourlyResampler()
    b0 = _kline(BASE_TIME, "1", "1", "1", "1")
    b1 = _kline(BASE_TIME + timedelta(minutes=15), "1", "1", "1", "2")
    # Gap: skip straight to +60 (an hour-aligned bar) instead of +30 --
    # the in-progress 2-bar group must be discarded, not silently
    # continued as if bar b1 were followed immediately by this one.
    b_gap = _kline(BASE_TIME + timedelta(minutes=60), "1", "1", "1", "3")
    rest = [
        _kline(BASE_TIME + timedelta(minutes=60 + 15 * i), "1", "1", "1", str(10 + i))
        for i in range(1, 4)
    ]

    resampler.update(b0)
    resampler.update(b1)
    result_gap = resampler.update(b_gap)
    assert result_gap is None  # starts a fresh group, doesn't complete anything
    results_rest = [resampler.update(b) for b in rest]
    assert results_rest[:2] == [None, None]
    candle = results_rest[2]
    assert candle is not None
    assert candle.open_time == BASE_TIME + timedelta(minutes=60)
    assert candle.close == Decimal("13")


# ---------------------------------------------------------------------------
# RegimeMomentumStrategy -- construction validation
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
        RegimeMomentumStrategy(fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT")


@pytest.mark.parametrize("regime_sma_length", [0, -1])
def test_rejects_non_positive_regime_sma_length(regime_sma_length):
    with pytest.raises(ValueError):
        RegimeMomentumStrategy(
            fast=3, slow=7, quantity=Decimal("1"), symbol="BTC-USDT", regime_sma_length=regime_sma_length
        )


# ---------------------------------------------------------------------------
# RegimeMomentumStrategy -- regime warmup
# ---------------------------------------------------------------------------


def test_no_signal_at_all_while_regime_is_still_warming_up():
    # 12 bars = exactly 3 completed 1h candles -- short of a
    # regime_sma_length=5 requirement, so the 1h trend regime never
    # becomes defined. The 15m fast/slow crossover (fast=2, slow=3)
    # genuinely crosses during this run (confirmed below against the raw
    # reference), but gating must suppress every signal since there is no
    # regime yet to match against.
    up = list(range(100, 106))
    down = list(range(105, 99, -1))
    closes = [Decimal(v) for v in up + down]

    raw = _reference_crossover_sign_sequence(closes, 2, 3)
    assert any(s is not None for s in raw)  # sanity: a real cross occurs

    strategy = RegimeMomentumStrategy(
        fast=2, slow=3, quantity=Decimal("1"), symbol="BTC-USDT", regime_sma_length=5
    )
    fired = _feed_strategy(strategy, closes)

    assert all(f is None for f in fired)
    assert strategy.trend_regime is None


# ---------------------------------------------------------------------------
# RegimeMomentumStrategy -- hand-verified regime gating
#
# See .planning/sr-e-regime-momentum.md for the full bar-by-bar arithmetic
# these two scenarios were derived from. fast=2, slow=4, regime_sma_length=2
# throughout, closes = [97, 98, 99, 100, 105, 107, 109, 110, ...]:
#   - bar idx 3: 15m baseline crossover sign established (+1, bullish) --
#     no signal (nothing to have crossed from yet).
#   - bar idx 3: 1st synthetic 1h candle completes, close=100.
#   - bar idx 7: 2nd synthetic 1h candle completes, close=110.
#     SMA(2) of [100, 110] = 105 < 110 -> regime flips to "up".
#   - bar idx 8 (close=95): 15m sign flips to -1 (bearish) -- a genuine
#     fresh cross -- but regime is "up", so this must produce NOTHING.
# ---------------------------------------------------------------------------


def test_bearish_cross_against_an_up_regime_produces_no_signal():
    closes = [Decimal(v) for v in [97, 98, 99, 100, 105, 107, 109, 110, 95]]
    fast, slow, regime_sma_length = 2, 4, 2

    raw = _reference_crossover_sign_sequence(closes, fast, slow)
    assert raw[8] == -1  # sanity: this really is a genuine bearish cross

    strategy = RegimeMomentumStrategy(
        fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT", regime_sma_length=regime_sma_length
    )
    fired = _feed_strategy(strategy, closes)

    assert strategy.trend_regime == "up"
    assert fired == [None] * len(closes)


def test_hand_verified_regime_gating_suppresses_and_allows_correctly():
    """One continuous scenario proving gating is direction-sensitive, not
    "block everything" or "allow everything": the bearish cross at bar 8
    (against the "up" regime) is suppressed; a later bullish cross at bar
    10 (matching the still-active "up" regime) is allowed through as a
    real LONG intent with the expected shape.
    """
    closes = [Decimal(v) for v in [97, 98, 99, 100, 105, 107, 109, 110, 95, 98, 115]]
    fast, slow, regime_sma_length = 2, 4, 2
    quantity = Decimal("0.02")
    symbol = "BTC-USDT"

    raw = _reference_crossover_sign_sequence(closes, fast, slow)
    assert raw[8] == -1  # suppressed
    assert raw[10] == 1  # allowed through

    strategy = RegimeMomentumStrategy(
        fast=fast, slow=slow, quantity=quantity, symbol=symbol, regime_sma_length=regime_sma_length
    )
    klines = _klines_from_closes(closes)
    intents = [strategy([k]) for k in klines]
    fired = [None if i is None else (1 if i.side == Side.LONG else -1) for i in intents]

    assert strategy.trend_regime == "up"
    assert fired == [None, None, None, None, None, None, None, None, None, None, 1]

    winner = intents[10]
    assert winner is not None
    assert winner.side == Side.LONG
    assert winner.order_type == OrderType.GUARDED_MARKET
    assert winner.quantity == quantity
    assert winner.symbol == symbol
    assert winner.limit_price is None


def test_regime_resets_to_none_on_an_exact_tie_after_being_previously_defined():
    # First two synthetic 1h candles (closes 100, 110) establish an "up"
    # regime (avg=105 < 110). A third candle closing exactly at 110 ties
    # its own SMA(2) (avg(110, 110) == 110) -- regime must reset to None,
    # not silently keep the stale "up" value.
    closes = [Decimal(v) for v in [97, 98, 99, 100, 105, 107, 109, 110, 105, 108, 109, 110]]
    strategy = RegimeMomentumStrategy(
        fast=2, slow=4, quantity=Decimal("1"), symbol="BTC-USDT", regime_sma_length=2
    )
    regimes_over_time = []
    for k in _klines_from_closes(closes):
        strategy([k])
        regimes_over_time.append(strategy.trend_regime)

    assert regimes_over_time[7] == "up"
    assert regimes_over_time[-1] is None


# ---------------------------------------------------------------------------
# RegimeMomentumStrategy -- edge-triggered firing
# ---------------------------------------------------------------------------


def test_edge_triggered_no_duplicate_signal_while_already_in_the_same_crossover_and_regime():
    closes = [Decimal(v) for v in [97, 98, 99, 100, 105, 107, 109, 110, 95, 98, 115, 120, 125]]
    fast, slow, regime_sma_length = 2, 4, 2

    strategy = RegimeMomentumStrategy(
        fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT", regime_sma_length=regime_sma_length
    )
    fired = _feed_strategy(strategy, closes)
    signals = [s for s in fired if s is not None]

    assert signals == [1]  # exactly one LONG -- not one per bar spent in-regime afterward


# ---------------------------------------------------------------------------
# RegimeMomentumStrategy -- property-based cross-check against an
# independent reference over a long, multi-phase zigzag
# ---------------------------------------------------------------------------


def test_full_signal_sequence_matches_independent_reference_over_a_long_zigzag():
    # A long primary rise/fall (establishing a firm 1h "up"/"down" regime
    # each direction, well past the regime_sma_length=20 warmup) each
    # followed by a shallow, short countertrend dip/bounce and a
    # resumption -- shallow and short enough that the *slower* 1h regime
    # SMA never reacts to it, but sharp enough that the *faster* 15m
    # fast=3/slow=7 crossover genuinely double-crosses (reversal, then a
    # resumption re-cross). This is deliberately engineered so the
    # resumption re-cross lands *after* the regime has settled, giving a
    # real same-direction (gated-through) fire in each phase, alongside
    # several genuinely-suppressed opposite-direction crosses -- a purely
    # monotonic or single-reversal zigzag never produces this, since the
    # (slower) regime always lags a lone reversal cross and so never
    # agrees with it (confirmed by hand while building this test -- see
    # .planning/sr-e-regime-momentum.md).
    rise = list(range(100, 400))
    dip = list(range(399, 391, -1))
    resume_rise = list(range(392, 420))
    fall = list(range(419, 119, -1))
    bounce = list(range(120, 128))
    resume_fall = list(range(127, 99, -1))
    closes = [Decimal(v) for v in rise + dip + resume_rise + fall + bounce + resume_fall]
    fast, slow, regime_sma_length = 3, 7, 20

    strategy = RegimeMomentumStrategy(
        fast=fast, slow=slow, quantity=Decimal("1"), symbol="BTC-USDT", regime_sma_length=regime_sma_length
    )
    actual = _feed_strategy(strategy, closes)
    expected = _reference_full_signal_sequence(closes, BASE_TIME, fast, slow, regime_sma_length)

    assert actual == expected
    # Sanity: this scenario must genuinely exercise gating (at least one
    # bar where the raw crossover disagrees with the final gated
    # signal), and must fire in both directions at least once.
    raw = _reference_crossover_sign_sequence(closes, fast, slow)
    assert raw != expected
    assert 1 in expected
    assert -1 in expected


# ---------------------------------------------------------------------------
# RegimeMomentumTrainable.fit
# ---------------------------------------------------------------------------


def _trainable(tmp_path, **overrides: object) -> RegimeMomentumTrainable:
    kwargs: dict[str, Any] = {
        "strategy_id": "regime-momentum-test",
        "strategy_version": "v1",
        "fee_bps": Decimal("0"),
        "slippage_bps": Decimal("0"),
        "runs_path": tmp_path / "experiments.jsonl",
    }
    kwargs.update(overrides)
    return RegimeMomentumTrainable(**kwargs)


def test_fit_raises_on_empty_candidate_grid(tmp_path):
    trainable = _trainable(tmp_path)

    with pytest.raises(ValueError):
        trainable.fit(_ramp_klines(10), {"candidates": []}, parent_run_id="p1")


def test_fit_uses_default_candidate_grid_when_params_omit_candidates(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    trainable = _trainable(tmp_path, runs_path=runs_path)
    train_klines = _ramp_klines(200)

    trainable.fit(train_klines, {}, parent_run_id="p1")

    records = [r for r in read_records(runs_path) if r["record_type"] == "backtest_run"]
    assert len(records) == len(regime_momentum.DEFAULT_CANDIDATE_GRID)


def test_fit_only_ever_backtests_against_train_klines(monkeypatch, tmp_path):
    train_klines = _ramp_klines(200)
    captured_klines_args = []
    original_run_backtest = regime_momentum.run_backtest

    def _spy_run_backtest(klines, strategy, fee_bps, slippage_bps):
        captured_klines_args.append(klines)
        return original_run_backtest(klines, strategy, fee_bps, slippage_bps)

    monkeypatch.setattr(regime_momentum, "run_backtest", _spy_run_backtest)

    trainable = _trainable(tmp_path)
    params = {"candidates": [(3, 8), (5, 13)]}

    trainable.fit(train_klines, params, parent_run_id="p1")

    assert len(captured_klines_args) == 2
    assert all(klines_arg is train_klines for klines_arg in captured_klines_args)


def test_fit_picks_the_best_scoring_candidate_by_total_return(tmp_path):
    # Decline then rise (trough), long enough that (3, 10) can accumulate
    # an "up" 1h regime and fire a profitable bullish cross, while
    # (3, 200) never accumulates enough 15m history to fire at all within
    # the train window (total_return stays exactly 0 -- no evidence, not
    # a loss). regime_sma_length=2 (small, overriding the fixed
    # production default of 24) keeps this scenario a tractable size --
    # fit() always uses the trainable's configured value uniformly across
    # every candidate regardless.
    decline = list(range(300, 200, -1))
    rise = list(range(201, 300))
    closes = [Decimal(v) for v in decline + rise]
    train_klines = _klines_from_closes(closes)
    params = {"candidates": [(3, 200), (3, 10)], "quantity": "0.5", "symbol": "BTC-USDT"}
    trainable = _trainable(tmp_path, regime_sma_length=2)

    bound = trainable.fit(train_klines, params, parent_run_id="p1")

    assert isinstance(bound, RegimeMomentumStrategy)
    assert bound.fast_window == 3
    assert bound.slow_window == 10


def test_fit_never_picks_a_zero_trade_candidate_over_a_genuinely_losing_one(tmp_path):
    # Same trough shape as the test above, but with fee_bps cranked high
    # enough (5000 bps -- extreme, deliberately synthetic, just needs to
    # exceed the one real trade's gross profit) that the one real,
    # evidence-backed trade (3, 10) fires nets a *negative* total_return.
    # (3, 200) still never fires at all (slow=200 exceeds the 99-bar
    # train window) -- its total_return is exactly 0, which is *higher*
    # than (3, 10)'s negative score. Naive "greatest score wins" would
    # wrongly pick the candidate that never traded; fit() must still pick
    # (3, 10), since a candidate with zero trades carries no evidence at
    # all and must never be preferred over one that actually traded, even
    # a losing one.
    decline = list(range(300, 200, -1))
    rise = list(range(201, 300))
    closes = [Decimal(v) for v in decline + rise]
    train_klines = _klines_from_closes(closes)
    params = {"candidates": [(3, 200), (3, 10)], "quantity": "0.5", "symbol": "BTC-USDT"}
    trainable = _trainable(tmp_path, regime_sma_length=2, fee_bps=Decimal("5000"), slippage_bps=Decimal("0"))

    bound = trainable.fit(train_klines, params, parent_run_id="p1")

    assert bound.fast_window == 3
    assert bound.slow_window == 10


def test_fit_falls_back_to_the_first_candidate_when_every_candidate_has_zero_trades(tmp_path):
    # If literally nothing in the grid ever fires (every slow window
    # exceeds the train window here), there is no evidence to prefer any
    # one candidate over another -- fit() must still return something
    # deterministic (the first-listed candidate) rather than raising or
    # leaving an undefined winner.
    train_klines = _ramp_klines(20)
    params = {"candidates": [(3, 200), (5, 250)]}
    trainable = _trainable(tmp_path)

    bound = trainable.fit(train_klines, params, parent_run_id="p1")

    assert bound.fast_window == 3
    assert bound.slow_window == 200


def test_fit_passes_fee_and_slippage_through_to_candidate_scoring(tmp_path):
    # Regression coverage for a real cost-modeling risk: if fee_bps/
    # slippage_bps were ever silently dropped before reaching
    # run_backtest, every fit() score would be a fee-free (overly
    # optimistic) approximation -- exactly what this project's Strategy
    # Research Methodology exists to catch. Same trough scenario as
    # above (one real bullish trade fires), scored once at zero fee and
    # once at a large nonzero fee; the logged score must be strictly
    # lower (worse) with the fee applied.
    decline = list(range(300, 200, -1))
    rise = list(range(201, 300))
    closes = [Decimal(v) for v in decline + rise]
    train_klines = _klines_from_closes(closes)
    params = {"candidates": [(3, 10)], "quantity": "0.5", "symbol": "BTC-USDT"}

    def _logged_total_return(fee_bps: Decimal, runs_path) -> Decimal:
        trainable = _trainable(
            tmp_path, runs_path=runs_path, regime_sma_length=2, fee_bps=fee_bps, slippage_bps=Decimal("0")
        )
        trainable.fit(train_klines, params, parent_run_id="p1")
        records = [r for r in read_records(runs_path) if r["record_type"] == "backtest_run"]
        return Decimal(str(records[0]["aggregate_metrics"]["total_return"]))

    zero_fee_return = _logged_total_return(Decimal("0"), tmp_path / "zero_fee.jsonl")
    high_fee_return = _logged_total_return(Decimal("5000"), tmp_path / "high_fee.jsonl")

    assert high_fee_return < zero_fee_return


def test_fit_returns_a_fresh_strategy_instance_not_a_scoring_candidate_reused(tmp_path):
    train_klines = _ramp_klines(200)
    params = {"candidates": [(3, 150), (3, 10)]}
    trainable = _trainable(tmp_path)

    bound = trainable.fit(train_klines, params, parent_run_id="p1")

    assert bound.bars_seen == 0


def test_fit_uses_the_fixed_regime_sma_length_for_every_candidate_ignoring_params(tmp_path):
    # fit()'s params dict has no "regime_sma_length" key read anywhere --
    # the regime SMA length is fixed at construction time on the
    # Trainable itself (CLAUDE.md's design: not part of the grid search).
    # Passing one in params must have zero effect on what's actually used
    # or logged.
    runs_path = tmp_path / "experiments.jsonl"
    trainable = _trainable(tmp_path, runs_path=runs_path, regime_sma_length=7)
    train_klines = _ramp_klines(40)

    trainable.fit(
        train_klines,
        {"candidates": [(3, 8)], "regime_sma_length": 999},
        parent_run_id="p1",
    )

    records = [r for r in read_records(runs_path) if r["record_type"] == "backtest_run"]
    assert len(records) == 1
    assert records[0]["params"]["regime_sma_length"] == 7


# ---------------------------------------------------------------------------
# RegimeMomentumTrainable.fit -- per-candidate experiment logging
# ---------------------------------------------------------------------------


def test_fit_logs_one_backtest_run_entry_per_candidate_with_correct_lineage(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    train_klines = _ramp_klines(40)
    candidates = [(3, 8), (5, 13), (3, 150)]  # last one never fires
    trainable = _trainable(tmp_path, strategy_id="regime-momentum-lineage", runs_path=runs_path)

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
        assert record["strategy_id"] == "regime-momentum-lineage"
        assert record["is_holdout_run"] is False
        fast, slow = candidates[record["candidate_index"]]
        assert record["params"]["fast"] == fast
        assert record["params"]["slow"] == slow


def test_fit_logs_nothing_beyond_the_per_candidate_records(tmp_path):
    runs_path = tmp_path / "experiments.jsonl"
    trainable = _trainable(tmp_path, runs_path=runs_path)

    trainable.fit(_ramp_klines(40), {"candidates": [(3, 8), (5, 13)]}, parent_run_id="p1")

    records = list(read_records(runs_path))
    assert len(records) == 2
    assert all(r["record_type"] == "backtest_run" for r in records)


# ---------------------------------------------------------------------------
# Integration: run_walk_forward + RegimeMomentumTrainable end to end
# (small, synthetic scale -- the real BingX end-to-end pass is run
# separately, see .planning/sr-e-regime-momentum.md, not as a unit test).
# ---------------------------------------------------------------------------


def test_run_walk_forward_with_regime_momentum_trainable_logs_per_candidate_and_final_records(tmp_path):
    klines = _ramp_klines(60)
    runs_path = tmp_path / "experiments.jsonl"
    candidates = [(3, 8), (5, 13)]
    trainable = _trainable(tmp_path, strategy_id="regime-momentum", runs_path=runs_path)

    result = run_walk_forward(
        klines,
        trainable,
        "regime-momentum",
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
