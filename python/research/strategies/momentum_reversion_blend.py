"""Regime-adaptive blend of momentum + mean-reversion (Strategy Research
Task K's third component) -- combines `ensemble_momentum.py`'s existing
multi-lookback momentum signal (reused unmodified, not rebuilt) with
`mean_reversion.py`'s new Bollinger-Band reversion signal (this same task's
"candidate B"), using the SAME continuous ADX regime weight already computed
by `regime_weighting.compute_regime_weight` to combine them -- momentum
weighted by the regime weight (high when trending), mean-reversion by its
complement (high when ranging). See CLAUDE.md's "Strategy Research
Methodology" section and `.planning/sr-k-mean-reversion-and-blend.md` for
the full design context: this project's earlier research explicitly found
that a naive fixed 50/50 blend of momentum and mean-reversion sometimes
underperforms either pure strategy alone, while a regime-aware blend
produced smoother, more robust risk-adjusted returns in multiple independent
studies -- this module is the first real, regime-adaptive (not naive-fixed)
attempt at that blend in this project. **"Smoother" describes that outside
research's *return-series* finding, not a guarantee about this
implementation's own position-SIZE formula** -- see "Why this combination
formula" below's "Disclosed sizing property" paragraph for a real,
documented case (both sub-signals agreeing at an intermediate regime
weight) where this blend's own sizing is *not* smoothly interpolated
between the two pure strategies' sizes, so the two claims are not in
tension despite sounding like they could be.

## Why this combination formula, not a naive 50/50 blend

`_blend_signals(momentum_sign, reversion_sign, regime_weight)` computes:

    blended_strength = regime_weight * momentum_sign
                        + (1 - regime_weight) * reversion_sign

`momentum_sign`/`reversion_sign` are each `-1`/`0`/`+1` (the same discrete
directional readings `ensemble_momentum._combined_sign`/`mean_reversion.
_bollinger_signal` already produce); `regime_weight` is `compute_regime_
weight`'s existing `[0, 1]` ADX-based ramp (unmodified, the same one
`ensemble_momentum.py` already uses for momentum's own conviction scaling).
This is a **convex combination** of two values already in `[-1, 1]`, so the
result is always in `[-1, 1]` too. At the extremes this collapses to exactly
one sub-signal: `regime_weight=1` (strongly trending) reduces to
`momentum_sign` alone (reversion's contribution is multiplied by
`1 - 1 = 0`); `regime_weight=0` (strongly ranging) reduces to
`reversion_sign` alone. Between the extremes, the two signals are blended
continuously and smoothly by how much the current market condition favors
each -- "using infrastructure that already exists" (the ADX regime weight
computed once and reused, `compute_regime_weight`, `_combined_sign`,
`_bollinger_signal`) "rather than inventing a new blending mechanism", per
this task's brief, instead of a fixed-fraction allocation that ignores
current market conditions entirely.

`abs(blended_strength)` (always in `[0, 1]`, by the convex-combination
property above) is used directly as the position-size conviction weight in
`_open` -- playing the same role `regime_weight` alone plays in the pure
momentum/reversion strategies. It is NOT multiplied by `regime_weight` a
second time: the ADX information is already fully incorporated into
`blended_strength` via the weighted combination itself, so applying
`compute_regime_weight` again on top would double-count it.

**Disclosed sizing property, not a bug**: when both sub-signals agree
(`momentum_sign == reversion_sign`, both `+1` or both `-1`),
`abs(blended_strength) == 1` regardless of `regime_weight` -- e.g. at
`regime_weight=0.5` with both signals bullish, `blended_strength =
0.5*1 + 0.5*1 = 1.0`, full conviction, even though the *pure*
`EnsembleMomentumStrategy`/`MeanReversionStrategy` would each size at only
0.5x under the identical ADX reading on their own. This is a direct,
intentional consequence of the convex-combination formula (two genuinely
agreeing signals are stronger joint evidence than either alone, not
evidence to be averaged down), not a double-counting error -- but it is a
real, disclosed contrast with the "regime-adaptive blend is smoother than
either pure strategy" framing this module's docstring otherwise
emphasizes: agreement at an intermediate regime weight can size UP to full
conviction, not just smoothly interpolate between the two pure sizes. The
existing `risk_fraction`-based position sizing cap is unaffected either
way (`abs(blended_strength)` never exceeds `1`, the same bound
`regime_weight` alone is already subject to).

## Why NOT a position-netting composite of two independent sub-strategies

An alternative design was considered and rejected: instantiate a full
`EnsembleMomentumStrategy` and a full `MeanReversionStrategy` internally
(each already self-contained, each independently ADX-regime-weighted) and
net their independently-tracked positions into one combined exposure. This
was rejected because every strategy in this codebase (and this project's
whole metrics/walk-forward pipeline) assumes a single, coherent open/flatten
position lifecycle -- `metrics.position.PositionTracker` reconstructs trades
from one ordered `(OrderIntent, Fill)` stream, and CLAUDE.md's Eligibility
Bar is evaluated from exactly that reconstruction. Netting two independently
-managed sub-positions would require *partial* position-size adjustments
(increase/decrease an existing position, not just open/flatten) -- a kind of
order nowhere else in this codebase, and a materially larger, riskier
addition than this task's scope warrants. The signal-level blend above
reuses the existing single-position/open-flatten/ATR-stop-target pattern
every strategy in this package already uses, producing one coherent trade
stream compatible with the existing metrics pipeline unmodified.

## What's reused, unmodified

- `research.strategies.ensemble_momentum`: `_combined_sign` (the actual
  multi-pair combination logic -- the meaningful "momentum side"
  implementation this task's brief says to reuse, not rebuild) and
  `DEFAULT_LOOKBACK_PAIRS`. The trivial per-pair SMA-sign computation loop
  is duplicated locally (three lines), matching this codebase's own
  established "duplicate small pieces of shared shape across strategy
  modules rather than couple them" convention (see e.g. `ma_crossover.py`'s
  `_data_range`/`_metrics_summary` docstrings, and every strategy module's
  own locally-defined `_sign` helper).
- `research.strategies.mean_reversion`: `BollingerBands`, `_bollinger_signal`,
  `DEFAULT_BOLLINGER_PERIOD`/`DEFAULT_BOLLINGER_K` -- the actual
  "mean-reversion side" signal logic, reused unmodified.
- `research.strategies.regime_weighting`: `AverageDirectionalIndex`,
  `compute_regime_weight` -- the shared ADX regime weight, computed ONCE
  per bar and used for both sides of the blend (not recomputed
  independently per sub-signal).
- `research.strategies.risk_management` / `research.strategies.
  volatility_targeting`: the identical ATR-based stop/target/sizing and
  real volatility-targeting scalar every strategy in this package uses,
  composed the same way.

## Edge-triggering

Same "fire only when `sign(blended_strength)` changes" pattern every
strategy in this package uses (`_signal_state` here, tracking the last
established nonzero blended sign). `self._signal_state` only updates when
either no entry was attempted this bar (still in a position, or no prior
state existed yet) or an attempted entry actually opened a position --
**not** when an entry was attempted (the edge-trigger conditions were met)
but `_open()` rejected it purely via a downstream filter (the
vol-scalar-weighted `final_quantity` rounding to `<= 0`, or vol warmup).
Without this distinction, a signal that fires the trigger but gets
filtered out at the sizing stage would be silently "consumed" -- a later
bar where filters would have allowed the trade, but the blended sign
hasn't changed again, would never retry. Same fix, same reasoning, as
`mean_reversion.MeanReversionStrategy`'s identical correction -- see that
module's docstring for the full explanation, including the disclosed,
NOT-fixed inconsistency this creates with `EnsembleMomentumStrategy`/
`SingleLookbackMomentumStrategy`'s own (unmodified, out of this task's
scope) signal-state trackers, which still update unconditionally.

## Warmup

Both sub-signals must be simultaneously ready (momentum's longest lookback
pair warmed up AND the Bollinger window full) before ANY blended signal is
computed at all -- the same "avoid the strategy's effective character
silently changing over the warmup period" reasoning
`EnsembleMomentumStrategy` already documents for its own 3-pair
simultaneous-warmup requirement, extended here to both signal *families*
rather than just the 3 pairs within one family.
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
from research.strategies.ensemble_momentum import DEFAULT_LOOKBACK_PAIRS, _combined_sign
from research.strategies.mean_reversion import (
    DEFAULT_BOLLINGER_K,
    DEFAULT_BOLLINGER_PERIOD,
    BollingerBands,
    _bollinger_signal,
)
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

DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")
DEFAULT_BARS_PER_DAY = 24

__all__ = [
    "DEFAULT_SYMBOL",
    "DEFAULT_STARTING_EQUITY",
    "DEFAULT_BARS_PER_DAY",
    "DEFAULT_LOOKBACK_PAIRS",
    "DEFAULT_BOLLINGER_PERIOD",
    "DEFAULT_BOLLINGER_K",
    "MomentumReversionBlendStrategy",
    "MomentumReversionBlendTrainable",
]


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _blend_signals(momentum_sign: int, reversion_sign: int, regime_weight: Decimal) -> Decimal:
    """`blended_strength = regime_weight * momentum_sign + (1 -
    regime_weight) * reversion_sign` -- see module docstring's "Why this
    combination formula" section. `momentum_sign`/`reversion_sign` are each
    `-1`/`0`/`+1`; `regime_weight` is always in `[0, 1]` (`compute_regime_
    weight`'s own contract). The result is a convex combination of two
    values in `[-1, 1]`, so it is itself always in `[-1, 1]` --
    `abs(result)` is used directly downstream as a `[0, 1]` conviction
    weight, the same role `regime_weight` alone plays in the pure momentum/
    reversion strategies.
    """
    return regime_weight * Decimal(momentum_sign) + (Decimal(1) - regime_weight) * Decimal(reversion_sign)


class MomentumReversionBlendStrategy:
    """Bound, stateful `Strategy`: regime-adaptive blend of
    `ensemble_momentum.py`'s multi-lookback crossover signal and
    `mean_reversion.py`'s Bollinger-Band signal (see module docstring),
    edge-triggered on the combined blended signal's sign, plus ATR-based
    stop/target exits and real volatility targeting.
    """

    __slots__ = (
        "_lookback_pairs",
        "_max_slow",
        "_closes",
        "_bands",
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
        "_signal_state",
        "_atr",
        "_adx",
        "_vol",
        "_position",
    )

    def __init__(
        self,
        *,
        symbol: str,
        lookback_pairs: Sequence[tuple[int, int]] = DEFAULT_LOOKBACK_PAIRS,
        bollinger_period: int = DEFAULT_BOLLINGER_PERIOD,
        bollinger_k: Decimal = DEFAULT_BOLLINGER_K,
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
        lookback_pairs = tuple(lookback_pairs)
        if len(lookback_pairs) < 3:
            raise ValueError(
                f"lookback_pairs must contain at least 3 (fast, slow) pairs for a meaningful "
                f"ensemble, got {len(lookback_pairs)}"
            )
        for fast, slow in lookback_pairs:
            if fast <= 0 or slow <= 0:
                raise ValueError(f"fast/slow window lengths must be positive, got fast={fast}, slow={slow}")
            if fast >= slow:
                raise ValueError(f"fast window ({fast}) must be strictly less than slow window ({slow})")

        self._lookback_pairs = lookback_pairs
        self._max_slow = max(slow for _, slow in lookback_pairs)
        self._closes: deque[Decimal] = deque(maxlen=self._max_slow)
        self._bands = BollingerBands(period=bollinger_period, k=bollinger_k)
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

        self._signal_state: int | None = None
        self._atr = AverageTrueRange(period=atr_period)
        self._adx = AverageDirectionalIndex(period=adx_period)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position: OpenPosition | None = None

    @property
    def lookback_pairs(self) -> tuple[tuple[int, int], ...]:
        return self._lookback_pairs

    @property
    def bollinger_period(self) -> int:
        return self._bands.period

    @property
    def stop_multiplier(self) -> Decimal:
        return self._stop_multiplier

    @property
    def target_multiplier(self) -> Decimal:
        return self._target_multiplier

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
        bands = self._bands.update(current)

        self._closes.append(current.close)
        have_momentum = len(self._closes) >= self._max_slow

        momentum_sign = 0
        if have_momentum:
            closes = list(self._closes)
            pair_signs = []
            for fast, slow in self._lookback_pairs:
                slow_sma = sum(closes[-slow:]) / slow
                fast_sma = sum(closes[-fast:]) / fast
                pair_signs.append(_sign(fast_sma - slow_sma))
            momentum_sign = _combined_sign(pair_signs)

        reversion_sign = 0
        if bands is not None:
            _, upper, lower = bands
            reversion_sign = _bollinger_signal(current.close, lower, upper)

        have_both = have_momentum and bands is not None

        blended_strength = Decimal(0)
        current_signal = 0
        if have_both:
            # The SAME shared regime weight drives both sides of the blend
            # -- see module docstring. Reused unmodified, computed once.
            regime_weight = compute_regime_weight(adx, low=self._adx_low, high=self._adx_high)
            blended_strength = _blend_signals(momentum_sign, reversion_sign, regime_weight)
            current_signal = _sign(blended_strength)

        intent: OrderIntent | None = None
        entry_rejected_by_filters = False

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif have_both:
            previous_signal = self._signal_state
            if (
                previous_signal is not None
                and current_signal != 0
                and current_signal != previous_signal
                and atr is not None
                and atr > 0
            ):
                side = Side.LONG if current_signal > 0 else Side.SHORT
                intent = self._open(current, side, atr, blended_strength, realized_vol)
                entry_rejected_by_filters = intent is None

        # See module docstring's "Edge-triggering" section: do NOT consume
        # the state when an attempted entry was rejected purely by a
        # downstream filter (vol scalar / warmup) -- only a genuinely
        # untried or already-open-position bar keeps the unconditional
        # update.
        if current_signal != 0 and not entry_rejected_by_filters:
            self._signal_state = current_signal

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
        blended_strength: Decimal,
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

        # abs(blended_strength) already fully incorporates the shared ADX
        # regime weight (see module docstring) -- NOT multiplied by
        # compute_regime_weight a second time here.
        conviction_weight = abs(blended_strength)
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None

        final_quantity = base_quantity * conviction_weight * vol_scalar
        if final_quantity <= 0:
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


class MomentumReversionBlendTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `MomentumReversionBlendStrategy`.

    `fit()` grid-searches nothing -- every constant (lookback pairs,
    Bollinger period/k, ATR/ADX/vol periods, ADX thresholds, stop/target
    multipliers, vol-target level/bounds, risk fraction) is fixed at
    `MomentumReversionBlendTrainable` construction time, matching
    `EnsembleMomentumTrainable`'s/`MeanReversionTrainable`'s default (no-
    `"candidates"`-key) behavior: one candidate built from
    `params["symbol"]` (default `DEFAULT_SYMBOL`) plus this instance's fixed
    constants, backtested against `train_klines` only, logged as its own
    `record_type: "backtest_run"` entry (`candidate_index=0`,
    `total_candidates=1`). No opt-in grid search (unlike `ensemble_momentum.
    py`'s Task I addition) -- same "don't add tuning surface to a
    not-yet-evaluated signal" reasoning as `mean_reversion.
    MeanReversionTrainable`.
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
        lookback_pairs: Sequence[tuple[int, int]] = DEFAULT_LOOKBACK_PAIRS,
        bollinger_period: int = DEFAULT_BOLLINGER_PERIOD,
        bollinger_k: Decimal = DEFAULT_BOLLINGER_K,
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
        self._lookback_pairs = tuple(lookback_pairs)
        self._bollinger_period = bollinger_period
        self._bollinger_k = bollinger_k
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
        symbol = params.get("symbol", DEFAULT_SYMBOL)

        scoring_strategy = self._build_strategy(symbol=symbol)
        backtest_result = run_backtest(train_klines, scoring_strategy, self._fee_bps, self._slippage_bps)
        metrics = compute_metrics(
            train_klines,
            backtest_result.filled_intents,
            backtest_result.fills,
            self._starting_equity,
            bars_per_day=self._bars_per_day,
        )
        self._log_candidate(symbol=symbol, train_klines=train_klines, metrics=metrics, parent_run_id=parent_run_id)
        return self._build_strategy(symbol=symbol)

    def _build_strategy(self, *, symbol: str) -> MomentumReversionBlendStrategy:
        return MomentumReversionBlendStrategy(
            symbol=symbol,
            lookback_pairs=self._lookback_pairs,
            bollinger_period=self._bollinger_period,
            bollinger_k=self._bollinger_k,
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
        symbol: str,
        train_klines: list[Kline],
        metrics: Metrics,
        parent_run_id: str,
    ) -> None:
        metrics_summary = _metrics_summary(metrics)
        experiment_log.log_run(
            run_id=str(uuid4()),
            strategy_id=self._strategy_id,
            strategy_version=self._strategy_version,
            params={
                "symbol": symbol,
                "lookback_pairs": [list(pair) for pair in self._lookback_pairs],
                "bollinger_period": self._bollinger_period,
                "bollinger_k": str(self._bollinger_k),
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
                    "in-sample scoring inside MomentumReversionBlendTrainable.fit() -- the single "
                    "fixed regime-adaptive blend, no grid search -- not itself a walk-forward run"
                ),
            },
            fee_bps=self._fee_bps,
            slippage_bps=self._slippage_bps,
            is_holdout_run=False,
            parent_run_id=parent_run_id,
            candidate_index=0,
            total_candidates=1,
            runs_path=self._runs_path,
        )
