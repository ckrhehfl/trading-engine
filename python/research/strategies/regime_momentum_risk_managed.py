"""Risk-managed variant of `regime_momentum.RegimeMomentumStrategy` --
Strategy Research Task F, Part 1. See CLAUDE.md's "Strategy Research
Operational Design" section and `.planning/sr-f-risk-management-and-1h-
variant.md` for the full design and this task's honest, unembellished
before/after walk-forward results.

Reuses `regime_momentum.py`'s `HourlyResampler`, `REGIME_SMA_LENGTH`,
`DEFAULT_CANDIDATE_GRID`, and `DEFAULT_SYMBOL` unchanged (imported, not
duplicated) -- deliberately, since this task's Part 1 brief is a direct,
comparable before/after test against `.planning/sr-e-regime-momentum.md`'s
already-logged real result: the regime-gated 15m entry logic itself must
stay identical to v1, with risk management (`research.strategies.
risk_management`) as the only addition. Diverging the entry/regime logic
here, even slightly, would make the before/after comparison meaningless.

Adds, on top of `RegimeMomentumStrategy`'s existing regime-gated,
edge-triggered 15m fast/slow SMA crossover entry logic:

1. **ATR-based stop-loss and take-profit** (`research.strategies.
   risk_management`, period 14, 1.5x ATR stop / 3.0x ATR target -- a 1:2
   risk/reward ratio; see that module's docstring for the full reasoning
   behind these constants). `entry_price` is the signal bar's own close
   (see `risk_management.OpenPosition`'s docstring for why, not the
   actual next-bar simulated fill price).
2. **Fixed-fractional position sizing**: quantity sized so a stop-loss
   hit loses exactly 1% of a fixed `risk_management.
   DEFAULT_REFERENCE_EQUITY` ($10,000) -- replacing v1's fixed tiny
   constant `quantity`.
3. **A held position suppresses new entry signals entirely** (no
   pyramiding, no same-instrument flip) until it's flattened by a stop or
   target hit -- extending this task's explicit "exactly flattening, not
   flipping" exit requirement to entries too: this strategy tracks at
   most one open position lifecycle at a time, and its `__call__` still
   only ever returns a single `OrderIntent | None` per bar, so opening and
   closing within the same bar was never an option to begin with.

The regime/crossover-sign trackers still update every bar regardless of
whether a position is currently open -- same "still updates regardless of
gating" rule as v1, so a stale crossover state never causes an incorrect
"fresh cross" read once a position closes.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from research.strategies.regime_momentum import (
    DEFAULT_CANDIDATE_GRID,
    DEFAULT_SYMBOL,
    REGIME_SMA_LENGTH,
    HourlyResampler,
)
from research.strategies.risk_management import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_REFERENCE_EQUITY,
    DEFAULT_RISK_FRACTION,
    DEFAULT_STOP_MULTIPLIER,
    DEFAULT_TARGET_MULTIPLIER,
    AverageTrueRange,
    OpenPosition,
    check_exit_trigger,
    compute_position_size,
    compute_stop_and_target,
)
from schemas.order_intent import OrderIntent, OrderType, Side

# Mark-to-market starting equity for each candidate's in-sample scoring
# backtest during fit() -- same value/reasoning as v1's
# regime_momentum.DEFAULT_STARTING_EQUITY and research.walkforward's own
# default: scoring is done via ratios (total return), so this
# arbitrary-but-fixed value's magnitude doesn't matter. A separate concern
# from risk_management.DEFAULT_REFERENCE_EQUITY (the position-sizing
# input) even though both happen to be $10,000 by convention.
DEFAULT_STARTING_EQUITY = Decimal("10000")

# Sharpe-annualization bars-per-day for this strategy's own 15m timeframe
# -- matches `metrics.metrics`'s default. Passed through to every
# `compute_metrics` call this module makes (candidate scoring inside
# `fit()`); `research.walkforward.run_walk_forward`'s own `bars_per_day`
# (also defaulting to 96) independently covers each fold's validate-window
# scoring. See `.planning/sr-f-risk-management-and-1h-variant.md` for why
# this became a real, explicit parameter (needed once this project had a
# non-15m strategy, `hourly_momentum.py`, that must not silently reuse the
# 15m annualization factor).
DEFAULT_BARS_PER_DAY = 96


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class RegimeMomentumRiskManagedStrategy:
    """Bound, stateful `Strategy`: `RegimeMomentumStrategy`'s regime-gated
    15m fast/slow SMA crossover entry, plus ATR-based stop/target exits
    and fixed-fractional position sizing. See this module's docstring for
    the full mechanics.
    """

    __slots__ = (
        "_fast",
        "_slow",
        "_symbol",
        "_regime_sma_length",
        "_atr_period",
        "_stop_multiplier",
        "_target_multiplier",
        "_reference_equity",
        "_risk_fraction",
        "_closes",
        "_crossover_sign",
        "_resampler",
        "_hourly_closes",
        "_trend_regime",
        "_atr",
        "_position",
    )

    def __init__(
        self,
        *,
        fast: int,
        slow: int,
        symbol: str,
        regime_sma_length: int = REGIME_SMA_LENGTH,
        atr_period: int = DEFAULT_ATR_PERIOD,
        stop_multiplier: Decimal = DEFAULT_STOP_MULTIPLIER,
        target_multiplier: Decimal = DEFAULT_TARGET_MULTIPLIER,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
    ) -> None:
        if fast <= 0 or slow <= 0:
            raise ValueError(f"fast/slow window lengths must be positive, got fast={fast}, slow={slow}")
        if fast >= slow:
            raise ValueError(f"fast window ({fast}) must be strictly less than slow window ({slow})")
        if regime_sma_length <= 0:
            raise ValueError(f"regime_sma_length must be positive, got {regime_sma_length}")
        self._fast = fast
        self._slow = slow
        self._symbol = symbol
        self._regime_sma_length = regime_sma_length
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction

        self._closes: deque[Decimal] = deque(maxlen=slow)
        self._crossover_sign: int | None = None
        self._resampler = HourlyResampler()
        self._hourly_closes: deque[Decimal] = deque(maxlen=regime_sma_length)
        self._trend_regime: str | None = None
        self._atr = AverageTrueRange(period=atr_period)
        self._position: OpenPosition | None = None

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
    def trend_regime(self) -> str | None:
        return self._trend_regime

    @property
    def open_position(self) -> OpenPosition | None:
        """Currently-tracked open position (or `None` if flat) -- exposed
        for tests/inspection, not read by any other production code.
        """
        return self._position

    @property
    def bars_seen(self) -> int:
        return len(self._closes)

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        # 1h regime update first -- same ordering rationale as v1: a
        # candle that happens to complete on this exact bar is eligible
        # to gate this same bar's entry decision.
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

        # ATR updates every bar regardless of position state -- it must
        # stay current so a fresh entry (once flat again) always has a
        # live ATR reading rather than a stale one from before the
        # previous position closed.
        atr = self._atr.update(current)

        self._closes.append(current.close)
        current_sign = 0
        have_slow_window = len(self._closes) >= self._slow
        if have_slow_window:
            closes = list(self._closes)
            slow_sma = sum(closes) / self._slow
            fast_sma = sum(closes[-self._fast :]) / self._fast
            current_sign = _sign(fast_sma - slow_sma)

        intent: OrderIntent | None = None

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif have_slow_window:
            previous_sign = self._crossover_sign
            # `atr > 0` (not just `atr is not None`) guards against a
            # degenerate zero-True-Range warmup result (e.g. a perfectly
            # flat synthetic price series in a test, or -- vanishingly
            # unlikely but not impossible -- a genuinely flat stretch of
            # real data): `compute_stop_and_target` raises on a
            # non-positive ATR, and a strategy must never let a
            # degenerate input crash `__call__` -- "no signal" is the
            # correct outcome here, same as every other degenerate case
            # in this codebase.
            if (
                previous_sign is not None
                and current_sign != 0
                and current_sign != previous_sign
                and atr is not None
                and atr > 0
            ):
                side: Side | None = None
                if current_sign > 0 and self._trend_regime == "up":
                    side = Side.LONG
                elif current_sign < 0 and self._trend_regime == "down":
                    side = Side.SHORT
                if side is not None:
                    intent = self._open(current, side, atr)

        # Crossover-sign tracker updates regardless of gating/position
        # state -- same rule as v1 -- so a later fresh cross is correctly
        # read once this strategy is flat again.
        if current_sign != 0:
            self._crossover_sign = current_sign

        return intent

    def _flatten(self, current: Kline) -> OrderIntent:
        position = self._position
        assert position is not None
        self._position = None
        closing_side = Side.SHORT if position.side == Side.LONG else Side.LONG
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=closing_side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=position.quantity,
            limit_price=None,
            signal_timeframe="15m",
            created_at=current.open_time,
        )

    def _open(self, current: Kline, side: Side, atr: Decimal) -> OrderIntent | None:
        entry_price = current.close
        stop_price, target_price = compute_stop_and_target(
            entry_price=entry_price,
            atr=atr,
            side=side,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
        )
        quantity = compute_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
        )
        if quantity is None:
            # Degenerate sizing input (should not occur for a positive
            # ATR, but compute_position_size's contract is checked
            # regardless of that expectation) -- no signal, same "skip"
            # semantics as every other degenerate case in this codebase.
            return None
        self._position = OpenPosition(
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
        )
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=quantity,
            limit_price=None,
            signal_timeframe="15m",
            created_at=current.open_time,
        )


def _data_range(klines: Sequence[Kline]) -> dict:
    """Same shape/logic as `research.walkforward._data_range` --
    deliberately duplicated, not imported, same reasoning as
    `ma_crossover.py`/`regime_momentum.py`'s identical helper.
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
    -- see `_data_range`'s docstring for why this is duplicated, not
    imported.
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


class RegimeMomentumRiskManagedTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `RegimeMomentumRiskManagedStrategy`.

    `fit(train_klines, params, *, parent_run_id)`:

    - `params["candidates"]` (default `regime_momentum.
      DEFAULT_CANDIDATE_GRID` -- the *same* grid v1 used, for direct
      before/after comparability): a sequence of 15m `(fast, slow)`
      window-length pairs to try.
    - `params["symbol"]` (default `regime_momentum.DEFAULT_SYMBOL`).
    - Unlike v1's `RegimeMomentumTrainable`, there is no `params["quantity"]`
      -- quantity is now computed per-trade by the strategy's own
      ATR/fixed-fractional sizing, not a fixed constant passed in.
    - Regime SMA length and every risk-management constant (ATR period,
      stop/target multipliers, reference equity, risk fraction) are fixed
      at `RegimeMomentumRiskManagedTrainable` construction time, never
      read from `params` -- kept out of the grid search entirely, per
      this task's "keep the total number of tunable knobs low" brief.
    - For each candidate, in order: build a fresh
      `RegimeMomentumRiskManagedStrategy`, backtest it against
      `train_klines` only (via the existing, already lookahead-safety-
      tested `backtest.engine.run_backtest`), score it via
      `metrics.metrics.compute_metrics(...).total_return`, and log it as
      its own `record_type: "backtest_run"` entry (`parent_run_id`/
      `candidate_index`/`total_candidates`). **Every** candidate is
      logged, not only the eventual winner -- identical pattern to v1.
    - Tie-break: first strictly-greatest score wins (`score > best_score`).
      A candidate with zero trades is excluded from winner selection ("no
      evidence", not "no loss") unless every candidate has zero trades, in
      which case the first-listed candidate is the deterministic fallback
      -- identical rule to v1 (see `regime_momentum.py`'s
      `RegimeMomentumTrainable` docstring for the full reasoning, added
      there in response to a CodeRabbit review finding on v1).
    - Returns a **fresh** `RegimeMomentumRiskManagedStrategy` instance
      bound to the winning `(fast, slow)` pair -- never the instance used
      to score it.
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        regime_sma_length: int = REGIME_SMA_LENGTH,
        atr_period: int = DEFAULT_ATR_PERIOD,
        stop_multiplier: Decimal = DEFAULT_STOP_MULTIPLIER,
        target_multiplier: Decimal = DEFAULT_TARGET_MULTIPLIER,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
        runs_path: str = experiment_log.DEFAULT_RUNS_PATH,
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._starting_equity = starting_equity
        self._bars_per_day = bars_per_day
        self._regime_sma_length = regime_sma_length
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction
        self._runs_path = runs_path

    def fit(self, train_klines: list[Kline], params: Mapping[str, Any], *, parent_run_id: str) -> Strategy:
        candidates = list(params.get("candidates", DEFAULT_CANDIDATE_GRID))
        if not candidates:
            raise ValueError("params['candidates'] must be a non-empty sequence of (fast, slow) pairs")
        symbol = params.get("symbol", DEFAULT_SYMBOL)
        total_candidates = len(candidates)

        best_score: Decimal | None = None
        best_pair: tuple[int, int] | None = None

        for index, pair in enumerate(candidates):
            fast, slow = pair
            candidate_strategy = self._build_strategy(fast=fast, slow=slow, symbol=symbol)
            backtest_result = run_backtest(train_klines, candidate_strategy, self._fee_bps, self._slippage_bps)
            candidate_metrics = compute_metrics(
                train_klines,
                backtest_result.filled_intents,
                backtest_result.fills,
                self._starting_equity,
                bars_per_day=self._bars_per_day,
            )

            self._log_candidate(
                fast=fast,
                slow=slow,
                symbol=symbol,
                train_klines=train_klines,
                metrics=candidate_metrics,
                parent_run_id=parent_run_id,
                candidate_index=index,
                total_candidates=total_candidates,
            )

            if candidate_metrics.num_trades == 0:
                continue
            score = candidate_metrics.total_return
            if best_score is None or score > best_score:
                best_score = score
                best_pair = (fast, slow)

        if best_pair is None:
            best_pair = tuple(candidates[0])
        fast, slow = best_pair
        return self._build_strategy(fast=fast, slow=slow, symbol=symbol)

    def _build_strategy(self, *, fast: int, slow: int, symbol: str) -> RegimeMomentumRiskManagedStrategy:
        return RegimeMomentumRiskManagedStrategy(
            fast=fast,
            slow=slow,
            symbol=symbol,
            regime_sma_length=self._regime_sma_length,
            atr_period=self._atr_period,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
        )

    def _log_candidate(
        self,
        *,
        fast: int,
        slow: int,
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
                "symbol": symbol,
                "regime_sma_length": self._regime_sma_length,
                "atr_period": self._atr_period,
                "stop_multiplier": str(self._stop_multiplier),
                "target_multiplier": str(self._target_multiplier),
                "reference_equity": str(self._reference_equity),
                "risk_fraction": str(self._risk_fraction),
            },
            fold_results=[
                {
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
                "note": (
                    "in-sample candidate scoring inside "
                    "RegimeMomentumRiskManagedTrainable.fit() -- not itself a walk-forward run"
                ),
            },
            fee_bps=self._fee_bps,
            slippage_bps=self._slippage_bps,
            is_holdout_run=False,
            parent_run_id=parent_run_id,
            candidate_index=candidate_index,
            total_candidates=total_candidates,
            runs_path=self._runs_path,
        )
