"""Regime-filtered 15m momentum (trend-following) `TrainableStrategy` --
the first real strategy hypothesis in this project. See CLAUDE.md's
"Strategy Research Methodology" section and `.planning/sr-e-regime-
momentum.md` for the full design and honest, unembellished results this
module produced.

**Unlike `ma_crossover.py` (a deliberate pipeline-validation vehicle
making no edge claim), this module is a genuine, honest attempt at
finding real edge, run through the exact same walk-forward/holdout/
experiment-log rigor.** See `.planning/sr-e-regime-momentum.md` for the
real result, whatever it turned out to be -- this docstring does not
repeat it, since results belong in the planning doc and
`runs/experiments.jsonl`, not duplicated in source comments that could
drift out of sync with the actual logged numbers.

CLAUDE.md's Current Scope already names "15m base, 5m extension, 1h
regime filter" as the intended timeframe architecture; this is the first
strategy to actually use that shape. Mechanics:

1. **1h regime filter, computed by internally resampling the 15m kline
   stream.** Deliberately does NOT change `backtest/engine.py` or
   `research/walkforward.py` to support a genuine multi-timeframe kline
   feed -- unnecessary infrastructure surgery for this. `HourlyResampler`
   aggregates every 4 consecutive 15m bars, aligned to the hour (the
   first bar of a group must have `open_time.minute == 0` -- any leading
   bars before the stream's first hour-aligned bar are discarded, never
   folded into a short first group), into one synthetic 1h candle
   (open = first 15m bar's open, high = max of the 4 highs, low = min of
   the 4 lows, close = 4th bar's close, volume = sum of the 4 volumes).
   Only **fully-completed** 1h candles feed the regime SMA -- the still-
   forming current hour (0-3 bars in) is never used.
2. **Regime**: a simple moving average of length `REGIME_SMA_LENGTH`
   (fixed, non-tunable -- see that constant's own docstring for why 24)
   over completed synthetic 1h closes. The latest completed 1h close
   strictly above the SMA => `"up"`; strictly below => `"down"`; an exact
   tie, or fewer than `REGIME_SMA_LENGTH` completed 1h candles so far
   (warmup), => no defined regime (`None`) -- gates out every entry until
   a real trend direction is established.
3. **15m entry, gated by regime**: fast/slow SMA crossover over 15m
   closes, edge-triggered (fires only on the bar a fresh cross happens,
   never on every bar spent within an already-detected crossover
   regime) -- same pattern as `ma_crossover.py`'s
   `MovingAverageCrossoverStrategy`. A bullish 15m cross only produces a
   LONG `OrderIntent` if the current 1h regime is `"up"`; a bearish cross
   only produces a SHORT `OrderIntent` if the current 1h regime is
   `"down"`. A cross against the current regime (or with no regime yet
   established) produces **no signal at all** -- not a suppressed-but-
   remembered signal, just nothing. The crossover-sign tracker used for
   edge-triggering still updates regardless of gating, so a later cross
   back the other way is correctly treated as "a fresh cross", not as
   "the same cross firing again".

Two pieces, same shape as `ma_crossover.py`:

- `RegimeMomentumStrategy` -- the bound, stateful `Strategy`
  (`Callable[[Sequence[Kline]], OrderIntent | None]`).
- `RegimeMomentumTrainable` -- the `TrainableStrategy` implementation
  (`python/research/walkforward.py`'s `Protocol`). `fit()` grid-searches
  15m `(fast, slow)` candidates only -- the 1h regime SMA length is never
  part of the grid, always `REGIME_SMA_LENGTH` (or whatever fixed value
  the `RegimeMomentumTrainable` was constructed with) for every
  candidate -- against `train_klines` only, scored by total return,
  logging **every** candidate as its own `backtest_run` entry via
  `parent_run_id`/`candidate_index`/`total_candidates`. Identical pattern
  to `MACrossoverTrainable.fit`; see that module for the shared reasoning
  behind total-return scoring, the tie-break rule, and returning a fresh
  bound instance rather than a reused scoring candidate.
"""

from collections import deque
from datetime import timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol scope per CLAUDE.md's Current Scope / Strategy Research
# Methodology -- see ma_crossover.py's identical constant for the same
# reasoning.
DEFAULT_SYMBOL = "BTC-USDT"

# Deliberately small and arbitrary -- backtest-only placeholder quantity,
# not a risk-sized position (that's the Java Risk Gateway's job, nowhere
# near this code path). Same value and reasoning as
# ma_crossover.DEFAULT_QUANTITY.
DEFAULT_QUANTITY = Decimal("0.001")

# Mark-to-market starting equity for each candidate's in-sample scoring
# backtest -- same value/reasoning as research.walkforward's own default
# and ma_crossover.DEFAULT_STARTING_EQUITY: scoring is done via ratios
# (total return), so this arbitrary-but-fixed value's magnitude doesn't
# matter.
DEFAULT_STARTING_EQUITY = Decimal("10000")

# Fixed, non-tunable 1h regime SMA length -- deliberately kept OUT of the
# grid search. CLAUDE.md's design brief for this strategy is explicit
# that the number of tunable knobs must stay low given the thin real data
# (~3 usable walk-forward folds at the provisional default windows), so
# this is a judgment call fixed once here rather than something fit()
# ever searches over.
#
# 24 completed hourly candles = one full day of higher-timeframe closes.
# Chosen over the more common technical-analysis folklore numbers (20,
# 50) specifically because it's easy to explain and reason about (a
# calendar day, not an arbitrary round number), while staying small
# relative to every window this strategy will ever actually run against:
# at the provisional walk-forward defaults, even the 30-day/720-hour
# validate fold gives >29x the warmup this SMA needs, and the ~90-day/
# 2,160-hour train fold gives >90x -- so this choice costs negligible
# usable history either way, regardless of which of those two numbers
# had been picked instead.
REGIME_SMA_LENGTH = 24

# A small, fixed grid of 15m (fast, slow) SMA window-length pairs.
# Deliberately not tuned to this asset -- per CLAUDE.md's design brief,
# few tunable knobs matters here, so this grid stays small (5 candidates,
# same size as ma_crossover.py's own placeholder grid) rather than being
# expanded speculatively. Slightly shorter-window-biased than
# ma_crossover.py's grid (max slow=40 here vs 50 there): entries are
# already filtered by the 1h regime gate, so a faster-reacting 15m
# crossover is a more plausible choice for the entry trigger itself than
# it would be as a standalone (ungated) signal -- not empirically tuned,
# just the directional reasoning behind picking different numbers than
# ma_crossover.py's grid rather than copying it unchanged. See
# .planning/sr-e-regime-momentum.md.
DEFAULT_CANDIDATE_GRID: tuple[tuple[int, int], ...] = (
    (5, 15),
    (8, 20),
    (10, 25),
    (13, 30),
    (20, 40),
)

_FIFTEEN_MIN = timedelta(minutes=15)


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class HourlyResampler:
    """Aggregates a bar-by-bar stream of 15m `Kline`s into completed
    synthetic 1h candles, aligned to the hour.

    `update(kline)` must be called once per 15m bar, in chronological
    order. Returns the just-completed 1h `Kline` on the bar that
    completes a group of 4, else `None` -- including for every bar before
    the stream's first hour-aligned (`open_time.minute == 0`) bar, which
    are discarded rather than folded into a short first group (there is
    no such thing as a genuine, complete "hour" starting anywhere but
    :00).

    Defensive against a gap in the input stream (a missing bar -- not
    expected from this project's own gap-free `KlineWindow`-driven bar
    loop, but not assumed either): if a bar's `open_time` doesn't
    immediately follow the previously accumulated bar's by exactly one
    15m step, the in-progress group is discarded and alignment search
    resumes from this bar (i.e. this bar starts a fresh group if it
    happens to be hour-aligned itself, else it's discarded too, same as
    the leading-bars case). This avoids silently mislabeling a post-gap
    15m bar as, say, the 3rd bar of an hour it was never actually part
    of.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending: list[Kline] = []

    def update(self, kline: Kline) -> Kline | None:
        if self._pending and kline.open_time != self._pending[-1].open_time + _FIFTEEN_MIN:
            self._pending = []

        if not self._pending and kline.open_time.minute != 0:
            return None

        self._pending.append(kline)
        if len(self._pending) < 4:
            return None

        group = self._pending
        self._pending = []
        return Kline(
            open_time=group[0].open_time,
            open=group[0].open,
            high=max(bar.high for bar in group),
            low=min(bar.low for bar in group),
            close=group[-1].close,
            volume=sum((bar.volume for bar in group), Decimal(0)),
        )


class RegimeMomentumStrategy:
    """Bound, stateful `Strategy`: 15m fast/slow SMA crossover, edge-
    triggered, gated by a fixed-length SMA regime filter computed over
    internally-resampled 1h candles. See this module's docstring for the
    full mechanics.

    Reads only `window[-1]` (the current bar) on each call, maintaining
    its own internal state incrementally -- same design as
    `ma_crossover.MovingAverageCrossoverStrategy` (see that class's
    docstring for why: it makes the "fresh instance for the returned
    winner" requirement load-bearing and testable via `bars_seen`).
    """

    __slots__ = (
        "_fast",
        "_slow",
        "_quantity",
        "_symbol",
        "_regime_sma_length",
        "_closes",
        "_crossover_sign",
        "_resampler",
        "_hourly_closes",
        "_trend_regime",
    )

    def __init__(
        self,
        *,
        fast: int,
        slow: int,
        quantity: Decimal,
        symbol: str,
        regime_sma_length: int = REGIME_SMA_LENGTH,
    ) -> None:
        if fast <= 0 or slow <= 0:
            raise ValueError(f"fast/slow window lengths must be positive, got fast={fast}, slow={slow}")
        if fast >= slow:
            raise ValueError(f"fast window ({fast}) must be strictly less than slow window ({slow})")
        if regime_sma_length <= 0:
            raise ValueError(f"regime_sma_length must be positive, got {regime_sma_length}")
        self._fast = fast
        self._slow = slow
        self._quantity = quantity
        self._symbol = symbol
        self._regime_sma_length = regime_sma_length
        self._closes: deque[Decimal] = deque(maxlen=slow)
        self._crossover_sign: int | None = None
        self._resampler = HourlyResampler()
        self._hourly_closes: deque[Decimal] = deque(maxlen=regime_sma_length)
        self._trend_regime: str | None = None

    @property
    def fast_window(self) -> int:
        return self._fast

    @property
    def slow_window(self) -> int:
        return self._slow

    @property
    def regime_sma_length(self) -> int:
        return self._regime_sma_length

    @property
    def bars_seen(self) -> int:
        """Number of 15m closes this instance has processed so far,
        capped at `slow`. `0` for a never-called, freshly constructed
        instance -- see `RegimeMomentumTrainable.fit`'s "fresh instance
        for the winner" handling.
        """
        return len(self._closes)

    @property
    def trend_regime(self) -> str | None:
        """Currently-active 1h trend regime (`"up"`, `"down"`, or `None`
        for warmup/tie) -- exposed for tests and inspection, not read by
        any other production code.
        """
        return self._trend_regime

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        # 1h regime update first -- this bar's own entry decision (below)
        # must see this bar's regime state, including a candle that
        # happens to complete on this exact bar (per this module's
        # design: only fully-completed candles are ever used, but a
        # candle completing *on* this bar is fully completed as of this
        # bar).
        completed_hour = self._resampler.update(current)
        if completed_hour is not None:
            self._hourly_closes.append(completed_hour.close)
            if len(self._hourly_closes) == self._regime_sma_length:
                hourly_sma = sum(self._hourly_closes) / self._regime_sma_length
                latest = completed_hour.close
                if latest > hourly_sma:
                    self._trend_regime = "up"
                elif latest < hourly_sma:
                    self._trend_regime = "down"
                else:
                    self._trend_regime = None

        self._closes.append(current.close)
        if len(self._closes) < self._slow:
            return None

        closes = list(self._closes)
        slow_sma = sum(closes) / self._slow
        fast_sma = sum(closes[-self._fast :]) / self._fast
        current_sign = _sign(fast_sma - slow_sma)

        previous_sign = self._crossover_sign
        intent: OrderIntent | None = None
        if previous_sign is not None and current_sign != 0 and current_sign != previous_sign:
            # A fresh 15m cross just happened -- gate it against the
            # current 1h trend regime. A cross against the regime (or
            # with no regime yet established) produces no signal at all:
            # not suppressed-but-remembered, just nothing (per this
            # module's design).
            if current_sign > 0 and self._trend_regime == "up":
                intent = OrderIntent(
                    intent_id=uuid4(),
                    symbol=self._symbol,
                    side=Side.LONG,
                    order_type=OrderType.GUARDED_MARKET,
                    quantity=self._quantity,
                    limit_price=None,
                    signal_timeframe="15m",
                    created_at=current.open_time,
                )
            elif current_sign < 0 and self._trend_regime == "down":
                intent = OrderIntent(
                    intent_id=uuid4(),
                    symbol=self._symbol,
                    side=Side.SHORT,
                    order_type=OrderType.GUARDED_MARKET,
                    quantity=self._quantity,
                    limit_price=None,
                    signal_timeframe="15m",
                    created_at=current.open_time,
                )

        if current_sign != 0:
            self._crossover_sign = current_sign

        return intent


def _data_range(klines: Sequence[Kline]) -> dict:
    """Same shape/logic as `research.walkforward._data_range` --
    deliberately duplicated, not imported, same reasoning as
    `ma_crossover.py`'s identical helper (that function is module-private
    in `research/walkforward.py`; duplicating a ~10-line helper is
    cheaper than coupling this module to walkforward.py's internals).
    """
    if not klines:
        return {"start_ms": None, "end_ms": None, "num_bars": 0}
    return {
        "start_ms": int(klines[0].open_time.timestamp() * 1000),
        "end_ms": int(klines[-1].open_time.timestamp() * 1000),
        "num_bars": len(klines),
    }


def _metrics_summary(metrics: Metrics) -> dict:
    """Same scalar-only shape as `research.walkforward._metrics_summary`/
    `ma_crossover._metrics_summary` -- see `_data_range`'s docstring for
    why this is duplicated, not imported.
    """
    return {
        "starting_equity": metrics.starting_equity,
        "final_equity": metrics.final_equity,
        "total_return": metrics.total_return,
        "sharpe_ratio": metrics.sharpe_ratio,
        "max_drawdown": metrics.max_drawdown,
        "win_rate": metrics.win_rate,
        "num_trades": metrics.num_trades,
        "profit_factor": metrics.profit_factor,
    }


class RegimeMomentumTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `RegimeMomentumStrategy`.

    `fit(train_klines, params, *, parent_run_id)`:

    - `params["candidates"]` (default `DEFAULT_CANDIDATE_GRID`): a
      sequence of 15m `(fast, slow)` window-length pairs to try. The 1h
      regime SMA length is **not** read from `params` at all -- it is
      fixed at `RegimeMomentumTrainable` construction time
      (`regime_sma_length`, default `REGIME_SMA_LENGTH`) and used
      identically for every candidate, per this strategy's "few tunable
      knobs" design constraint.
    - `params["quantity"]` (default `DEFAULT_QUANTITY`), `params["symbol"]`
      (default `DEFAULT_SYMBOL"]`): passed straight through to every
      candidate's bound `RegimeMomentumStrategy`.
    - For each candidate, in order: build a fresh `RegimeMomentumStrategy`,
      backtest it against `train_klines` only (via the existing,
      already lookahead-safety-tested `backtest.engine.run_backtest` --
      never a custom evaluation loop), score it via
      `metrics.metrics.compute_metrics(...).total_return`, and log it as
      its own `record_type: "backtest_run"` entry (via
      `research.experiment_log.log_run`, `parent_run_id`=this call's
      `parent_run_id`, `candidate_index`=this candidate's 0-based
      position, `total_candidates`=the grid size). **Every** candidate is
      logged, not only the eventual winner -- identical pattern to
      `ma_crossover.MACrossoverTrainable.fit`.
    - Tie-break: first strictly-greatest score wins (`score > best_score`,
      not `>=`) -- deterministic.
    - A candidate with zero trades (`Metrics.num_trades == 0`, so
      `total_return` is exactly `0`) is excluded from winner selection --
      "no evidence", not "no loss". Left in the running, it would beat
      every candidate on a purely-losing grid purely for never having
      traded (`0 >` any negative return), silently turning a losing fold
      into a validate-fold run that never trades at all. Still logged
      like every other candidate, just never selected as the winner
      unless literally every candidate in the grid has zero trades (in
      which case the first-listed candidate is returned, same
      deterministic fallback spirit as the tie-break above).
    - Returns a **fresh** `RegimeMomentumStrategy` instance bound to the
      winning `(fast, slow)` pair -- never the instance used to score it,
      which would already carry leftover internal state (rolling 15m
      closes, hourly-resampler state, tracked regime) from the in-sample
      scoring run.
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
        regime_sma_length: int = REGIME_SMA_LENGTH,
        runs_path: str = experiment_log.DEFAULT_RUNS_PATH,
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._starting_equity = starting_equity
        self._regime_sma_length = regime_sma_length
        self._runs_path = runs_path

    def fit(self, train_klines: list[Kline], params: Mapping[str, Any], *, parent_run_id: str) -> Strategy:
        candidates = list(params.get("candidates", DEFAULT_CANDIDATE_GRID))
        if not candidates:
            raise ValueError("params['candidates'] must be a non-empty sequence of (fast, slow) pairs")
        quantity = Decimal(str(params.get("quantity", DEFAULT_QUANTITY)))
        symbol = params.get("symbol", DEFAULT_SYMBOL)
        total_candidates = len(candidates)

        best_score: Decimal | None = None
        best_pair: tuple[int, int] | None = None

        for index, pair in enumerate(candidates):
            fast, slow = pair
            candidate_strategy = RegimeMomentumStrategy(
                fast=fast,
                slow=slow,
                quantity=quantity,
                symbol=symbol,
                regime_sma_length=self._regime_sma_length,
            )
            backtest_result = run_backtest(train_klines, candidate_strategy, self._fee_bps, self._slippage_bps)
            candidate_metrics = compute_metrics(
                train_klines, backtest_result.filled_intents, backtest_result.fills, self._starting_equity
            )

            self._log_candidate(
                fast=fast,
                slow=slow,
                quantity=quantity,
                symbol=symbol,
                train_klines=train_klines,
                metrics=candidate_metrics,
                parent_run_id=parent_run_id,
                candidate_index=index,
                total_candidates=total_candidates,
            )

            if candidate_metrics.num_trades == 0:
                # A candidate that never fired scores an exactly-0 total
                # return -- "no evidence", not "no loss". Left in the
                # running, this would let a never-fired candidate beat
                # every genuinely-losing candidate on a purely-losing
                # grid (0 > any negative return) and get selected as the
                # "winner" purely for having sat out, silently turning a
                # losing fold into a validate-fold run that never trades
                # at all. Excluded from best-score tracking entirely;
                # still logged like every other candidate above.
                continue
            score = candidate_metrics.total_return
            if best_score is None or score > best_score:
                best_score = score
                best_pair = (fast, slow)

        if best_pair is None:
            # Every candidate had zero trades -- there is no evidence to
            # prefer any one of them over another, so fall back to the
            # first candidate in the grid (deterministic, same
            # first-listed-wins spirit as the tie-break above) rather
            # than raising. candidates is non-empty (checked above), so
            # this is always well-defined.
            best_pair = tuple(candidates[0])
        fast, slow = best_pair
        return RegimeMomentumStrategy(
            fast=fast,
            slow=slow,
            quantity=quantity,
            symbol=symbol,
            regime_sma_length=self._regime_sma_length,
        )

    def _log_candidate(
        self,
        *,
        fast: int,
        slow: int,
        quantity: Decimal,
        symbol: str,
        train_klines: list[Kline],
        metrics: Metrics,
        parent_run_id: str,
        candidate_index: int,
        total_candidates: int,
    ) -> None:
        metrics_summary = _metrics_summary(metrics)
        experiment_log.log_run(
            run_id=str(uuid4()),
            strategy_id=self._strategy_id,
            strategy_version=self._strategy_version,
            params={
                "fast": fast,
                "slow": slow,
                "quantity": str(quantity),
                "symbol": symbol,
                "regime_sma_length": self._regime_sma_length,
            },
            fold_results=[
                {
                    # Not a real walk-forward fold -- a single in-sample
                    # window (train == "validate" here, since this is
                    # candidate scoring inside fit(), not a walk-forward
                    # run). Same shape as ma_crossover.py's identical
                    # candidate-logging convention.
                    "fold_index": 0,
                    "train_start_index": 0,
                    "train_end_index": len(train_klines),
                    "validate_start_index": 0,
                    "validate_end_index": len(train_klines),
                    "metrics": metrics_summary,
                }
            ],
            aggregate_metrics=metrics_summary,
            data_range=_data_range(train_klines),
            walk_forward_config={
                "train_bars": len(train_klines),
                "validate_bars": 0,
                "step_bars": 0,
                "fold_count": 0,
                "note": "in-sample candidate scoring inside RegimeMomentumTrainable.fit() -- not itself a walk-forward run",
            },
            fee_bps=self._fee_bps,
            slippage_bps=self._slippage_bps,
            is_holdout_run=False,
            parent_run_id=parent_run_id,
            candidate_index=candidate_index,
            total_candidates=total_candidates,
            runs_path=self._runs_path,
        )
