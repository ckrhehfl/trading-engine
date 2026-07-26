"""Native-1h momentum strategy -- Strategy Research Task F, Part 2. See
CLAUDE.md's "Strategy Research Operational Design" section and
`.planning/sr-f-risk-management-and-1h-variant.md` for the full design,
the real 1h BingX retention finding, and this task's honest, unembellished
walk-forward results.

A **separate, simpler** strategy than `regime_momentum_risk_managed.py`
(Part 1): operates on native 1h bars for both trend/regime *and* entry
signal -- no internal 15m -> 1h resampling needed, since the input klines
are already 1h. Concretely, this collapses `regime_momentum.
RegimeMomentumStrategy`'s two-tier design (resample 15m -> 1h for a
*separate* regime SMA, gate a *separate* 15m entry crossover) down to a
single-tier fast/slow SMA crossover computed directly on 1h closes,
edge-triggered -- the crossover state itself doubles as both the
trend/regime read (fast above slow = "in an uptrend") and the entry
trigger (the edge where that state just changed), rather than two
independently-timed SMA computations gating each other. Structurally this
is the same shape as `ma_crossover.MovingAverageCrossoverStrategy` (a
single-tier, edge-triggered crossover, unconditionally emitting a signal
on every genuine cross), not `RegimeMomentumStrategy` (a two-tier,
regime-gated one that suppresses a cross against the regime).

**Judgment call, stated plainly**: this task's brief ("operating natively
on 1h bars for both trend/regime and entry signal... no internal
resampling needed... Keep the same core idea (fast/slow SMA crossover,
edge-triggered)") is genuinely readable two ways -- (a) the single-tier
reading implemented here, or (b) a two-tier reading that keeps
`RegimeMomentumStrategy`'s separate regime-SMA-gates-entry-SMA structure,
just with *both* SMAs computed directly on 1h bars instead of resampling
15m into the regime tier. (a) was chosen because the brief explicitly
calls this variant "simpler" and describes "the same core idea" as *one*
fast/slow crossover, not a *regime-gated* one -- if a second, gating tier
were intended, "regime-gated" (the term used everywhere else in this
project when that concept is meant, including this very docstring's
description of Part 1) would most likely have been used here too. (b)
would have been the safer literal-compatibility choice with
`RegimeMomentumStrategy`'s architecture; it was not chosen because
CLAUDE.md's Strategy Research Methodology says to state assumptions and
proceed rather than block on an ambiguity a human wasn't asked about
mid-task, and (a) is more clearly what "simpler" and "no resampling"
point to on a plain reading. Recorded here in case a future session
believes (b) should have been built instead.

Adds the identical risk-management approach as Part 1
(`research.strategies.risk_management`) -- ATR-based stop/target (same
period/multiplier constants), fixed-fractional position sizing (same
reference equity/risk fraction) -- so the two strategies are genuinely
comparable per this task's brief, not independently reinvented (see that
module's docstring).
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

# Single-symbol scope per CLAUDE.md's Current Scope / Strategy Research
# Methodology -- same constant/reasoning as every other strategy module in
# this package.
DEFAULT_SYMBOL = "BTC-USDT"

# Mark-to-market starting equity for each candidate's in-sample scoring
# backtest during fit() -- same value/reasoning as every other strategy
# module's identical constant in this package: scoring is via ratios
# (total return), so the magnitude doesn't matter.
DEFAULT_STARTING_EQUITY = Decimal("10000")

# This strategy's native timeframe is 1h (24 bars/day), not this
# project's original 15m (96 bars/day) -- `metrics.metrics.compute_metrics`
# defaults to 96 (see that module's `_BARS_PER_DAY` comment), so this
# module must pass 24 explicitly everywhere it calls `compute_metrics`
# (candidate scoring inside `fit()`) or every logged/reported Sharpe for
# this strategy would be silently inflated by exactly sqrt(96/24) = 2x.
# `research.walkforward.run_walk_forward`'s own `bars_per_day` parameter
# (for validate-fold scoring) must likewise be passed `24` by any caller
# walk-forward-testing this strategy -- it is not read from this constant
# automatically, since `run_walk_forward` has no notion of which
# `TrainableStrategy` it's driving. See `.planning/sr-f-risk-management-
# and-1h-variant.md`.
DEFAULT_BARS_PER_DAY = 24

# A small, fixed grid of 1h (fast, slow) SMA window-length pairs. Sized
# directly in 1h-bar units -- not a mechanical /4 conversion of
# `regime_momentum.DEFAULT_CANDIDATE_GRID`'s 15m-bar pairs (that would
# produce non-integer, oddly-shaped window lengths for most entries, e.g.
# (5, 15) -> (1.25, 3.75)). Kept a similar fast:slow ratio spirit
# (~1:2.5-3, matching the 15m grid's own spirit) and the same candidate
# count (5) for a fair-ish comparison, per CLAUDE.md's Strategy Research
# Methodology "few tunable knobs" guidance -- not empirically tuned to
# this asset.
DEFAULT_CANDIDATE_GRID: tuple[tuple[int, int], ...] = (
    (3, 8),
    (4, 10),
    (5, 12),
    (6, 15),
    (8, 20),
)


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class HourlyMomentumStrategy:
    """Bound, stateful `Strategy`: single-tier fast/slow SMA crossover on
    native 1h closes, edge-triggered, plus ATR-based stop/target exits and
    fixed-fractional position sizing. See this module's docstring for the
    full mechanics and the single-tier-vs-two-tier judgment call.

    Unlike `RegimeMomentumStrategy`/`RegimeMomentumRiskManagedStrategy`, a
    genuine cross always produces a signal (LONG on a bullish cross, SHORT
    on a bearish one) -- there is no separate regime to gate it against,
    by this module's design (see docstring).
    """

    __slots__ = (
        "_fast",
        "_slow",
        "_symbol",
        "_atr_period",
        "_stop_multiplier",
        "_target_multiplier",
        "_reference_equity",
        "_risk_fraction",
        "_closes",
        "_crossover_sign",
        "_atr",
        "_position",
    )

    def __init__(
        self,
        *,
        fast: int,
        slow: int,
        symbol: str,
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
        self._fast = fast
        self._slow = slow
        self._symbol = symbol
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction

        self._closes: deque[Decimal] = deque(maxlen=slow)
        self._crossover_sign: int | None = None
        self._atr = AverageTrueRange(period=atr_period)
        self._position: OpenPosition | None = None

    @property
    def fast_window(self) -> int:
        return self._fast

    @property
    def slow_window(self) -> int:
        return self._slow

    @property
    def open_position(self) -> OpenPosition | None:
        return self._position

    @property
    def bars_seen(self) -> int:
        return len(self._closes)

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

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
            # `atr > 0` guards against a degenerate zero-True-Range
            # warmup result -- see the identical guard's comment in
            # `regime_momentum_risk_managed.py` for the full reasoning.
            if (
                previous_sign is not None
                and current_sign != 0
                and current_sign != previous_sign
                and atr is not None
                and atr > 0
            ):
                side = Side.LONG if current_sign > 0 else Side.SHORT
                intent = self._open(current, side, atr)

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
            signal_timeframe="1h",
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
            signal_timeframe="1h",
            created_at=current.open_time,
        )


def _data_range(klines: Sequence[Kline]) -> dict:
    """Same shape/logic as every other strategy module's identically-named
    helper in this package -- deliberately duplicated, not imported (see
    `regime_momentum.py`'s `_data_range` docstring for the reasoning).
    """
    if not klines:
        return {"start_ms": None, "end_ms": None, "num_bars": 0}
    return {
        "start_ms": int(klines[0].open_time.timestamp() * 1000),
        "end_ms": int(klines[-1].open_time.timestamp() * 1000),
        "num_bars": len(klines),
    }


def _metrics_summary(metrics: Metrics) -> dict:
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


class HourlyMomentumTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `HourlyMomentumStrategy`.

    `fit(train_klines, params, *, parent_run_id)`:

    - `params["candidates"]` (default `DEFAULT_CANDIDATE_GRID`): a
      sequence of 1h `(fast, slow)` window-length pairs to try.
    - `params["symbol"]` (default `DEFAULT_SYMBOL`).
    - ATR period and every risk-management constant are fixed at
      `HourlyMomentumTrainable` construction time, never read from
      `params` -- same "few tunable knobs" discipline as Part 1's
      `RegimeMomentumRiskManagedTrainable`.
    - Same per-candidate scoring/logging/tie-break/zero-trade-exclusion
      rules as `RegimeMomentumRiskManagedTrainable.fit` and, before it,
      `regime_momentum.RegimeMomentumTrainable.fit` -- see either for the
      full reasoning (deliberately not re-derived a third time here).
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

    def _build_strategy(self, *, fast: int, slow: int, symbol: str) -> HourlyMomentumStrategy:
        return HourlyMomentumStrategy(
            fast=fast,
            slow=slow,
            symbol=symbol,
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
                    "in-sample candidate scoring inside HourlyMomentumTrainable.fit() "
                    "-- not itself a walk-forward run"
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
