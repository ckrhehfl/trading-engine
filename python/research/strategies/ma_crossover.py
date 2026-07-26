"""Placeholder moving-average-crossover `TrainableStrategy` -- Task D of
CLAUDE.md's "Strategy Research Operational Design" ("Build sequencing").

**Pipeline-validation vehicle only -- not a validated trading strategy,
and must never be promoted toward paper/live trading based on this
task's results.** Mirrors the disclaimer on
`java/runtime/src/main/java/engine/runtime/DummySignalSource.java`: that
class exists purely so `TradingLoop`/`OrderPipeline` wiring could be
exercised end to end without a real strategy; this module exists purely
so the walk-forward/holdout/experiment-log pipeline
(`python/research/walkforward.py`, `holdout.py`, `experiment_log.py`)
could be exercised end to end with a real (if deliberately simple)
`TrainableStrategy`, not a stand-in that never trades. In-sample-best
parameter selection over a single small grid (`DEFAULT_CANDIDATE_GRID`,
5 candidates), scored on only ~3 non-overlapping walk-forward folds of
~207 days of real BingX history (see `.planning/sr-c-walkforward-
holdout.md` and `.planning/sr-d-placeholder-strategy.md`), is a
well-known-weak methodology on its own -- nowhere close to CLAUDE.md's
Strategy Research Methodology bar (walk-forward validation with enough
folds to be credible, a holdout confirmation, and the Backtest/
Walk-Forward Eligibility Bar) for paper-trading eligibility. See
CLAUDE.md's "Strategy Research Methodology" section for what a strategy
actually has to clear before paper trading, and `.planning/sr-d-
placeholder-strategy.md` for this task's own honest, unembellished
results.

Two pieces:

- `MovingAverageCrossoverStrategy` -- the bound, stateful `Strategy`
  (`Callable[[Sequence[Kline]], OrderIntent | None]`) that
  `MACrossoverTrainable.fit()` returns. Maintains a rolling window of
  recent closes and the last established fast-vs-slow SMA regime sign as
  internal state, updated one bar at a time; emits an `OrderIntent` only
  on a genuine edge-triggered crossing (fast SMA moves from below to
  above the slow SMA, or vice versa), never on every bar spent within an
  already-detected regime.
- `MACrossoverTrainable` -- the `TrainableStrategy` implementation
  (`python/research/walkforward.py`'s `Protocol`). `fit()` backtests a
  small fixed grid of `(fast, slow)` candidates against `train_klines`
  only (never `validate_klines` -- structurally true, since `fit()`'s own
  signature has no such parameter), scores each by total return, logs
  each candidate's own train-only backtest as its own `record_type:
  "backtest_run"` entry (via `research.experiment_log.log_run`, using
  the `parent_run_id`/`candidate_index`/`total_candidates` lineage fields
  -- see `research.walkforward.TrainableStrategy.fit`'s docstring for
  where `parent_run_id` comes from), and returns a *fresh* strategy
  instance bound to the best-scoring candidate's parameters.
"""

from collections import deque
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol scope per CLAUDE.md's Current Scope / Strategy Research
# Methodology -- a caller-supplied `params["symbol"]` overrides this, but
# nothing in this project uses a different symbol today.
DEFAULT_SYMBOL = "BTC-USDT"

# Deliberately small and arbitrary -- this is a backtest-only placeholder
# quantity, not a risk-sized position (that's the Java Risk Gateway's job,
# nowhere near this code path). ~0.001 BTC keeps notional small regardless
# of price, without needing any real sizing logic for a strategy that must
# never be promoted to paper/live in the first place.
DEFAULT_QUANTITY = Decimal("0.001")

# Mark-to-market starting equity for each candidate's in-sample scoring
# backtest -- same value and same reasoning as
# `research.walkforward._DEFAULT_STARTING_EQUITY`: scoring is done via
# ratios (total return), so this arbitrary-but-fixed reference value's
# actual magnitude doesn't matter.
DEFAULT_STARTING_EQUITY = Decimal("10000")

# A small, fixed grid of (fast, slow) SMA window-length pairs (bars, i.e.
# 15m units). Deliberately not tuned to this asset -- just enough
# candidates (5) to exercise the "pick the best of a small grid, log every
# candidate" plumbing this task exists to prove, per CLAUDE.md's Task D
# brief ("don't overbuild this, it's explicitly a placeholder"). See
# .planning/sr-d-placeholder-strategy.md for why these specific pairs.
DEFAULT_CANDIDATE_GRID: tuple[tuple[int, int], ...] = (
    (5, 20),
    (8, 21),
    (10, 30),
    (13, 34),
    (20, 50),
)


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class MovingAverageCrossoverStrategy:
    """Bound, stateful `Strategy`: fast/slow simple-moving-average
    crossover, edge-triggered.

    **Not a validated trading strategy** -- see this module's docstring.

    Reads only `window[-1]` (the current bar) on each call -- unlike a
    strategy that recomputes purely from the growing window it's handed,
    this one maintains its own rolling `deque` of the last `slow` closes
    plus the last established non-zero regime sign as genuine internal
    state, updated incrementally one bar at a time. This means each
    instance is meant to be driven by one, and only one, monotonically
    advancing bar sequence -- see `MACrossoverTrainable.fit`'s "fresh
    instance per candidate, and a fresh instance again for the winner"
    handling for why that matters.

    Emits a LONG `OrderIntent` on a bullish cross (fast SMA moves from at-
    or-below to strictly above the slow SMA), a SHORT `OrderIntent` on a
    bearish cross (the reverse), and `None` otherwise -- including every
    warmup bar before `slow` closes have accumulated, every bar within an
    already-detected regime (edge-triggered, not level-triggered -- a
    strategy that fires every bar while already in a regime is a real,
    easy-to-introduce bug this design avoids by tracking the *last
    established* non-zero regime sign, not just the immediately preceding
    bar's sign), and the bar that first establishes a baseline regime
    (there is nothing before it to have crossed from, so it cannot itself
    be a "crossing"). An exact tie (fast SMA == slow SMA) counts as "no
    signal" and does not update the tracked regime sign -- the next
    genuinely non-zero sign is compared against the last *real* regime,
    not against the tie.

    Uses `OrderType.GUARDED_MARKET` (simplest -- no limit-price
    bookkeeping needed for a placeholder) and a small, fixed `quantity`
    given at construction time.
    """

    __slots__ = ("_fast", "_slow", "_quantity", "_symbol", "_closes", "_last_regime_sign")

    def __init__(self, *, fast: int, slow: int, quantity: Decimal, symbol: str) -> None:
        if fast <= 0 or slow <= 0:
            raise ValueError(f"fast/slow window lengths must be positive, got fast={fast}, slow={slow}")
        if fast >= slow:
            raise ValueError(f"fast window ({fast}) must be strictly less than slow window ({slow})")
        self._fast = fast
        self._slow = slow
        self._quantity = quantity
        self._symbol = symbol
        self._closes: deque[Decimal] = deque(maxlen=slow)
        self._last_regime_sign: int | None = None

    @property
    def fast_window(self) -> int:
        return self._fast

    @property
    def slow_window(self) -> int:
        return self._slow

    @property
    def bars_seen(self) -> int:
        """Number of closes this instance has processed so far, capped at
        `slow` (the rolling window's `deque` capacity). `0` for a
        never-called, freshly constructed instance.
        """
        return len(self._closes)

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]
        self._closes.append(current.close)
        if len(self._closes) < self._slow:
            return None

        closes = list(self._closes)
        slow_sma = sum(closes) / self._slow
        fast_sma = sum(closes[-self._fast :]) / self._fast
        current_sign = _sign(fast_sma - slow_sma)

        previous_sign = self._last_regime_sign
        intent: OrderIntent | None = None
        if previous_sign is not None and current_sign != 0 and current_sign != previous_sign:
            side = Side.LONG if current_sign > 0 else Side.SHORT
            intent = OrderIntent(
                intent_id=uuid4(),
                symbol=self._symbol,
                side=side,
                order_type=OrderType.GUARDED_MARKET,
                quantity=self._quantity,
                limit_price=None,
                signal_timeframe="15m",
                created_at=current.open_time,
            )

        if current_sign != 0:
            self._last_regime_sign = current_sign

        return intent


def _data_range(klines: Sequence[Kline]) -> dict:
    """Same shape/logic as `research.walkforward._data_range` --
    duplicated rather than imported: that function is module-private
    (leading underscore) in `research/walkforward.py`, signaling it isn't
    meant to be reused across modules, and the logic is a handful of
    lines. See `.planning/sr-d-placeholder-strategy.md` for this
    decision.
    """
    if not klines:
        return {"start_ms": None, "end_ms": None, "num_bars": 0}
    return {
        "start_ms": int(klines[0].open_time.timestamp() * 1000),
        "end_ms": int(klines[-1].open_time.timestamp() * 1000),
        "num_bars": len(klines),
    }


def _metrics_summary(metrics: Metrics) -> dict:
    """Same scalar-only shape as `research.walkforward._metrics_summary`
    (see `_data_range`'s docstring for why this is duplicated, not
    imported): omits `equity_curve`/`closed_trades` from what gets
    persisted to `runs/experiments.jsonl`.
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


class MACrossoverTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `MovingAverageCrossoverStrategy`.

    **Not a validated trading strategy** -- see this module's docstring.

    `fit(train_klines, params, *, parent_run_id)`:

    - `params["candidates"]` (default `DEFAULT_CANDIDATE_GRID` if omitted
      or absent): a sequence of `(fast, slow)` window-length pairs to try.
    - `params["quantity"]` (default `DEFAULT_QUANTITY`), `params["symbol"]`
      (default `DEFAULT_SYMBOL`): passed straight through to every
      candidate's bound `MovingAverageCrossoverStrategy`.
    - For each candidate, in order: build a fresh
      `MovingAverageCrossoverStrategy`, backtest it against `train_klines`
      only (via the existing, already lookahead-safety-tested
      `backtest.engine.run_backtest` -- never a custom evaluation loop),
      score it via `metrics.metrics.compute_metrics(...).total_return`,
      and log it as its own `record_type: "backtest_run"` entry (via
      `research.experiment_log.log_run`, `parent_run_id`=this call's
      `parent_run_id`, `candidate_index`=this candidate's 0-based
      position, `total_candidates`=the grid size) -- so the grid search
      doesn't quietly become exactly the untracked-variation-count
      problem CLAUDE.md's logging rule exists to prevent. **Every**
      candidate is logged, not only the eventual winner.
    - Total return (not Sharpe) is the scoring metric: Sharpe requires at
      least two non-degenerate per-bar return observations
      (`metrics.metrics._sharpe_ratio`'s degenerate-input rule) and is
      `None` whenever a candidate never trades (a real possibility here --
      a `slow` window close to or longer than `len(train_klines)` simply
      never accumulates enough history to fire) or has zero-variance
      returns; total return is always a defined `Decimal` for any
      non-empty `train_klines`, avoiding a `None`-handling tie-break
      inside this selection loop. This is a scoring-convenience choice
      for candidate selection only, not a claim that total return is a
      better fitness metric than Sharpe in general -- the *eligibility*
      bar a strategy has to clear for paper trading (CLAUDE.md's
      Backtest/Walk-Forward Eligibility Bar) is still expressed in terms
      of Sharpe/drawdown/profit-factor/trade-count, unaffected by this
      choice. Ties are broken by candidate order (first strictly-greatest
      score wins, via a plain `>` comparison) -- deterministic, not
      arbitrary.
    - Returns a **fresh** `MovingAverageCrossoverStrategy` instance bound
      to the winning `(fast, slow)` pair -- never the same object used to
      score that candidate during the loop above, which would already
      carry leftover internal state (rolling closes, last regime sign)
      from the in-sample scoring run. See
      `MovingAverageCrossoverStrategy`'s own docstring.
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
        runs_path: str = experiment_log.DEFAULT_RUNS_PATH,
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._starting_equity = starting_equity
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
            candidate_strategy = MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=quantity, symbol=symbol)
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

            score = candidate_metrics.total_return
            if best_score is None or score > best_score:
                best_score = score
                best_pair = (fast, slow)

        assert best_pair is not None  # candidates is non-empty, checked above
        fast, slow = best_pair
        return MovingAverageCrossoverStrategy(fast=fast, slow=slow, quantity=quantity, symbol=symbol)

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
            params={"fast": fast, "slow": slow, "quantity": str(quantity), "symbol": symbol},
            fold_results=[
                {
                    # Not a real walk-forward fold -- a single in-sample
                    # window (train == "validate" here, since this is
                    # candidate scoring inside fit(), not a walk-forward
                    # run). Recorded as one "fold" entry so this record's
                    # shape stays consistent with every other
                    # `record_type: "backtest_run"` entry in
                    # runs/experiments.jsonl.
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
                "note": "in-sample candidate scoring inside MACrossoverTrainable.fit() -- not itself a walk-forward run",
            },
            fee_bps=self._fee_bps,
            slippage_bps=self._slippage_bps,
            is_holdout_run=False,
            parent_run_id=parent_run_id,
            candidate_index=candidate_index,
            total_candidates=total_candidates,
            runs_path=self._runs_path,
        )
