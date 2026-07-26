"""Single-best-lookback momentum strategy (the "control" arm of Strategy
Research Task H's ensemble-vs-single-lookback comparison) -- native-1h
fast/slow SMA crossover, `fit()`-grid-searched over a single `(fast,
slow)` pair, plus two new institutional-grade layers on top: ADX-based
continuous regime weighting and real volatility targeting. See CLAUDE.md's
"Strategy Research Methodology" section and `.planning/sr-h-ensemble-
regime-voltargeting.md` for the full research context, design reasoning,
and honest real walk-forward results (this vs. `ensemble_momentum.py`).

This is `regime_momentum_risk_managed.py`'s natural successor, not a
from-scratch strategy: same ATR-based stop/target and fixed-fractional
sizing (`research.strategies.risk_management`, imported unchanged), same
single-tier edge-triggered SMA-crossover entry shape as
`hourly_momentum.HourlyMomentumStrategy` (operating natively on 1h bars,
same as that module -- see this module's own timeframe-choice reasoning
in `.planning/sr-h-ensemble-regime-voltargeting.md`). What's new:

1. **ADX-based continuous regime weighting** (`research.strategies.
   regime_weighting`), replacing `regime_momentum.py`'s old hard binary
   up/down 1h-SMA regime gate. ADX measures trend *strength*, not
   *direction* -- so unlike the old gate (which fully blocked a cross
   against the regime direction), this strategy's crossover sign alone
   still decides LONG vs. SHORT; ADX instead continuously scales *how
   much* size that decision gets, smoothly ramping from ~0 conviction in
   a choppy/ranging market (ADX <= 20) to full conviction in a strongly
   trending one (ADX >= 25), linearly interpolated between.
2. **Real volatility targeting** (`research.strategies.
   volatility_targeting`), applied as a second, independent multiplier on
   top of the ATR-sized base quantity -- scaling overall exposure to a
   target annualized volatility (20%, the documented general
   institutional convention), a portfolio/exposure-level control
   genuinely separate from the ATR stop's per-trade risk control. See
   that module's docstring for why these two stay conceptually and
   computationally separate rather than being folded together.

`final_quantity = atr_sized_base_quantity * regime_weight * vol_scalar`.
Either factor going to (near-)zero naturally suppresses the trade
entirely (no explicit binary branch needed) -- a genuinely continuous
"gate", per this task's research finding that continuous scaling reduces
whipsaw versus an abrupt on/off switch.
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
from research.strategies.regime_weighting import (
    DEFAULT_ADX_HIGH_THRESHOLD,
    DEFAULT_ADX_LOW_THRESHOLD,
    DEFAULT_ADX_PERIOD,
    AverageDirectionalIndex,
    compute_regime_weight,
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
from research.strategies.volatility_targeting import (
    DEFAULT_MAX_VOL_SCALAR,
    DEFAULT_MIN_VOL_SCALAR,
    DEFAULT_TARGET_ANNUALIZED_VOL,
    DEFAULT_VOL_LOOKBACK_PERIOD,
    RollingRealizedVolatility,
    compute_vol_scalar,
)
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol scope per CLAUDE.md's Current Scope / Strategy Research
# Methodology -- same constant/reasoning as every other strategy module in
# this package.
DEFAULT_SYMBOL = "BTC-USDT"

# Mark-to-market starting equity for each candidate's in-sample scoring
# backtest during fit() -- same value/reasoning as every other strategy
# module's identical constant.
DEFAULT_STARTING_EQUITY = Decimal("10000")

# This strategy's native timeframe is 1h (24 bars/day), same choice and
# same reasoning as hourly_momentum.py's identical constant -- see
# .planning/sr-h-ensemble-regime-voltargeting.md for why this task picked
# 1h over 15m (statistical credibility: 1h's real BingX depth supports the
# 8-10+ fold walk-forward floor this comparison needs to be meaningful;
# 15m's thin ~252-day depth structurally caps out at 3 folds, per every
# prior task in this project).
DEFAULT_BARS_PER_DAY = 24

# A grid of 1h (fast, slow) SMA window-length pairs spanning sub-day
# (4h/12h) to multi-day (24h/72h = 3 days) scales -- deliberately sized to
# span the same short/medium/long range ensemble_momentum.py's fixed 3
# lookback pairs cover (this grid's (12, 36) and (24, 72) entries are
# exactly ensemble_momentum.DEFAULT_LOOKBACK_PAIRS' medium/long pairs),
# so the "single best lookback" this strategy's fit() picks is a genuinely
# comparable alternative to what the ensemble offers, not an artificially
# narrower or differently-scaled search. 6 candidates -- a modest grid,
# not searched/tuned to this asset beyond choosing this scale spread.
DEFAULT_CANDIDATE_GRID: tuple[tuple[int, int], ...] = (
    (4, 12),
    (6, 18),
    (8, 24),
    (12, 36),
    (16, 48),
    (24, 72),
)


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class SingleLookbackMomentumStrategy:
    """Bound, stateful `Strategy`: single-tier fast/slow SMA crossover on
    native 1h closes, edge-triggered (structurally identical to
    `hourly_momentum.HourlyMomentumStrategy`'s crossover logic), plus
    ATR-based stop/target exits, ADX-based continuous regime weighting,
    and real volatility targeting. See this module's docstring for the
    full mechanics.
    """

    __slots__ = (
        "_fast",
        "_slow",
        "_symbol",
        "_adx_low",
        "_adx_high",
        "_target_annualized_vol",
        "_min_vol_scalar",
        "_max_vol_scalar",
        "_stop_multiplier",
        "_target_multiplier",
        "_reference_equity",
        "_risk_fraction",
        "_closes",
        "_crossover_sign",
        "_atr",
        "_adx",
        "_vol",
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
        adx_period: int = DEFAULT_ADX_PERIOD,
        adx_low: Decimal = DEFAULT_ADX_LOW_THRESHOLD,
        adx_high: Decimal = DEFAULT_ADX_HIGH_THRESHOLD,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
    ) -> None:
        if fast <= 0 or slow <= 0:
            raise ValueError(f"fast/slow window lengths must be positive, got fast={fast}, slow={slow}")
        if fast >= slow:
            raise ValueError(f"fast window ({fast}) must be strictly less than slow window ({slow})")
        self._fast = fast
        self._slow = slow
        self._symbol = symbol
        self._adx_low = adx_low
        self._adx_high = adx_high
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction

        self._closes: deque[Decimal] = deque(maxlen=slow)
        self._crossover_sign: int | None = None
        self._atr = AverageTrueRange(period=atr_period)
        self._adx = AverageDirectionalIndex(period=adx_period)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
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
        adx = self._adx.update(current)
        realized_vol = self._vol.update(current)

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
            # atr > 0 (not just atr is not None) guards against a
            # degenerate zero-True-Range warmup result -- same rationale
            # as regime_momentum_risk_managed.py/hourly_momentum.py's
            # identical guard.
            if (
                previous_sign is not None
                and current_sign != 0
                and current_sign != previous_sign
                and atr is not None
                and atr > 0
            ):
                side = Side.LONG if current_sign > 0 else Side.SHORT
                intent = self._open(current, side, atr, adx, realized_vol)

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

    def _open(
        self,
        current: Kline,
        side: Side,
        atr: Decimal,
        adx: Decimal | None,
        realized_vol: Decimal | None,
    ) -> OrderIntent | None:
        entry_price = current.close
        stop_price, target_price = compute_stop_and_target(
            entry_price=entry_price,
            atr=atr,
            side=side,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
        )
        base_quantity = compute_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
        )
        if base_quantity is None:
            return None

        # ADX conviction weight -- None (warmup) is baked in as zero
        # weight by compute_regime_weight itself; no special-casing
        # needed here.
        regime_weight = compute_regime_weight(adx, low=self._adx_low, high=self._adx_high)

        # Vol-target scalar -- unlike regime_weight, None here (warmup)
        # is NOT baked in by compute_vol_scalar (see that function's
        # docstring for why the two contracts differ); this strategy's
        # own choice is to skip trading entirely until the realized-vol
        # estimator has warmed up, same "no evidence = no trade"
        # convention as everywhere else in this codebase.
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None

        # No separate hard cap on final_quantity (or the resulting
        # per-trade risk-if-stopped) is needed beyond compute_vol_scalar's
        # own max_scalar clamp: regime_weight is always in [0, 1] by
        # construction (compute_regime_weight's own contract), so the
        # worst case is exactly base_quantity * 1 * max_vol_scalar --
        # i.e. the maximum possible risk-if-stopped is deterministically
        # risk_fraction * max_vol_scalar of reference_equity (1% * 3 =
        # 3% at these defaults), already bounded by the existing,
        # documented max_vol_scalar cap in volatility_targeting.py. A
        # second, independent cap here would be redundant with that
        # bound, not an additional safety property.
        final_quantity = base_quantity * regime_weight * vol_scalar
        if final_quantity <= 0:
            # Near-zero conviction (choppy/ranging regime) or a vol
            # scalar that rounded to zero -- no signal, same "skip"
            # semantics as every other degenerate case in this codebase.
            return None

        self._position = OpenPosition(
            side=side,
            quantity=final_quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
        )
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=final_quantity,
            limit_price=None,
            signal_timeframe="1h",
            created_at=current.open_time,
        )


def _data_range(klines: Sequence[Kline]) -> dict:
    """Same shape/logic as every other strategy module's identically-named
    helper in this package -- deliberately duplicated, not imported (see
    `regime_momentum.py`'s `_data_range` docstring for the established
    reasoning).
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


class SingleLookbackMomentumTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `SingleLookbackMomentumStrategy`.

    `fit(train_klines, params, *, parent_run_id)`:

    - `params["candidates"]` (default `DEFAULT_CANDIDATE_GRID`): a
      sequence of 1h `(fast, slow)` window-length pairs to try.
    - `params["symbol"]` (default `DEFAULT_SYMBOL`).
    - ADX period/thresholds, volatility-targeting period/target/scalar
      bounds, and every ATR risk-management constant are fixed at
      `SingleLookbackMomentumTrainable` construction time, never read from
      `params` -- same "few tunable knobs" discipline as every other
      `Trainable` in this package (`regime_momentum_risk_managed.py`,
      `hourly_momentum.py`).
    - Same per-candidate scoring/logging/tie-break/zero-trade-exclusion
      rules as those two `Trainable`s' `fit()` -- see either for the full
      reasoning (deliberately not re-derived a third time here).
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
        adx_period: int = DEFAULT_ADX_PERIOD,
        adx_low: Decimal = DEFAULT_ADX_LOW_THRESHOLD,
        adx_high: Decimal = DEFAULT_ADX_HIGH_THRESHOLD,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
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
        self._adx_period = adx_period
        self._adx_low = adx_low
        self._adx_high = adx_high
        self._vol_period = vol_period
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
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

    def _build_strategy(self, *, fast: int, slow: int, symbol: str) -> SingleLookbackMomentumStrategy:
        return SingleLookbackMomentumStrategy(
            fast=fast,
            slow=slow,
            symbol=symbol,
            atr_period=self._atr_period,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
            adx_period=self._adx_period,
            adx_low=self._adx_low,
            adx_high=self._adx_high,
            vol_period=self._vol_period,
            bars_per_day=self._bars_per_day,
            target_annualized_vol=self._target_annualized_vol,
            min_vol_scalar=self._min_vol_scalar,
            max_vol_scalar=self._max_vol_scalar,
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
                "adx_period": self._adx_period,
                "adx_low": str(self._adx_low),
                "adx_high": str(self._adx_high),
                "vol_period": self._vol_period,
                "target_annualized_vol": str(self._target_annualized_vol),
                "min_vol_scalar": str(self._min_vol_scalar),
                "max_vol_scalar": str(self._max_vol_scalar),
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
                    "in-sample candidate scoring inside SingleLookbackMomentumTrainable.fit() "
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
