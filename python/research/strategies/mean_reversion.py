"""Regime-gated mean-reversion strategy (Strategy Research Task K,
"candidate B" of the momentum/mean-reversion blend) -- native-1h Bollinger-
Band reversion signal, gated by the SAME continuous ADX regime-weight
machinery `regime_weighting.py` already provides to the momentum strategies,
but INVERTED: mean-reversion gets more weight when ADX is LOW
(ranging/choppy) and less weight when ADX is HIGH (trending) -- the opposite
direction from `ensemble_momentum.py`/`single_lookback_momentum.py`'s use of
the same indicator. See CLAUDE.md's "Strategy Research Methodology" section
and `.planning/sr-k-mean-reversion-and-blend.md` for the full design
context: this project's momentum/trend-following signal class has now been
tried three times without clearing the (revised) Eligibility Bar on its own;
a deep research pass earlier in this project's history found blending
momentum with mean-reversion produces smoother, more robust risk-adjusted
returns than either alone in multiple independent studies -- but the
mean-reversion side of that finding had never actually been built before
this task. This module is that build.

## Why Bollinger Bands, not RSI

Both are standard, well-understood mean-reversion signals; either would have
been defensible. Bollinger Bands was chosen because:

1. It produces a **price-level** signal (distance from a rolling mean band),
   the same kind of quantity `risk_management.compute_stop_and_target`
   already consumes (an ATR-scaled distance from the entry price) -- so the
   entry-price/stop/target composition is structurally uniform with every
   momentum strategy in this package, with no new "which price level does
   this indicator's signal correspond to" question to resolve.
2. Its underlying calculation (rolling mean + rolling standard deviation of
   a price series) is structurally identical to `volatility_targeting.
   RollingRealizedVolatility`'s already-established rolling-mean/rolling-
   stdev shape in this codebase (same `deque(maxlen=period)`, same
   incremental `update()` contract, same ddof=1 sample-stdev convention --
   see `BollingerBands`' docstring for why ddof=1, not the more traditional
   population stdev, was chosen here too) -- reusing an already-proven-safe
   computational pattern rather than introducing RSI's genuinely different
   gain/loss-smoothing shape.
3. RSI would have been an equally defensible choice (and is arguably the
   more traditional "textbook" overbought/oversold oscillator) -- this is a
   documented judgment call, not a claim that Bollinger Bands is
   objectively superior for this purpose.

## Inverted regime gating -- the core, easy-to-get-backwards design point

`ensemble_momentum.py`/`single_lookback_momentum.py` call `regime_weighting.
compute_regime_weight(adx, low, high)` directly: weight ramps from 0
(ranging, ADX <= low) to 1 (trending, ADX >= high) -- more size when the
market is trending, because momentum needs a trend to ride. Mean-reversion
needs the opposite market condition: this project's own earlier research
explicitly found pure mean-reversion fails badly in trending markets (a real
cited backtest: 66% win rate but -16.88% net loss, because a few large
trend-riding losses erased many small reversion wins). This module therefore
uses the exact SAME `compute_regime_weight` function (reused unmodified, not
reimplemented) but takes its **complement**:
`mean_reversion_weight = Decimal(1) - compute_regime_weight(adx, low=adx_low,
high=adx_high)` -- 1 (full conviction) when ADX is at/below `low` (ranging),
0 (fully suppressed) when ADX is at/above `high` (trending), ramping
continuously between. Reusing the existing function and inverting via a
plain `1 - weight` (rather than writing a second, separately-tested
"inverted ramp" function) keeps this genuinely one piece of shared,
already-tested infrastructure, not a parallel reimplementation with its own
chance of a sign-flip bug.

## What's reused, unmodified, from sibling modules

- `research.strategies.regime_weighting`: `AverageDirectionalIndex`,
  `compute_regime_weight` (inverted via `1 -`, as above), and the default
  ADX period/thresholds.
- `research.strategies.risk_management`: `AverageTrueRange`, `OpenPosition`,
  `compute_stop_and_target`, `compute_position_size`, `check_exit_trigger`
  -- the identical ATR-based stop/target/sizing every momentum strategy in
  this package uses, composed the same way.
- `research.strategies.volatility_targeting`: `RollingRealizedVolatility`,
  `compute_vol_scalar` -- the identical real volatility-targeting position
  scalar every momentum strategy in this package uses.

`final_quantity = atr_sized_base_quantity * inverted_regime_weight *
vol_scalar` -- structurally identical composition to
`EnsembleMomentumStrategy._open`/`SingleLookbackMomentumStrategy._open`,
just with the regime-weight factor inverted.

## Edge-triggering and its one, disclosed behavioral consequence

Same "fire only when the signal STATE changes" pattern every strategy in
this package uses (`_crossover_sign` in the momentum strategies;
`_signal_state` here): a new entry attempt requires this bar's oversold/
overbought reading to differ from the *last established nonzero reading*,
not merely "outside the bands right now". This avoids constant re-triggering
every single bar while price remains beyond a band (which, unlike a
momentum crossover, can persist for many consecutive bars in a real trend)
-- but has one disclosed consequence: if a position is stopped out while
price is STILL beyond the same band (signal state unchanged), this strategy
will NOT immediately re-enter -- it waits for the state to flip away and
back. A more eager "re-enter immediately if still oversold/overbought" rule
was considered and rejected in favor of reusing this package's one, uniform,
already-tested edge-triggering convention rather than introducing a second,
bespoke triggering rule that would need its own separate justification and
tests.

## Warmup

Same "no evidence = no signal, no silently-changing character during
warmup" convention as `EnsembleMomentumStrategy`: a signal is only ever
computed once `BollingerBands.update()` stops returning `None` (its own
`period`-bar warmup). ATR/ADX/vol warmup are handled exactly as in the
momentum strategies (an entry is only attempted once every relevant
indicator has a real reading).
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

# Single-symbol/native-1h conventions matching every sibling strategy module
# in this package.
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")
DEFAULT_BARS_PER_DAY = 24

# Standard Bollinger Band convention (20-period rolling window, 2 standard
# deviations for the band width) -- not searched/tuned to this asset, same
# "few tunable knobs" discipline as every other indicator constant in this
# package.
DEFAULT_BOLLINGER_PERIOD = 20
DEFAULT_BOLLINGER_K = Decimal("2")


class BollingerBands:
    """Incremental, look-ahead-safe Bollinger Bands over a bar-by-bar stream
    of closes, fed one `Kline` per call via `update()`.

    `update()` returns `(middle, upper, lower)` once at least `period`
    closes have been fed, else `None` (warmup -- same "no evidence yet"
    convention as every other rolling-window warmup in this codebase):
    `middle = mean(closes)`, `upper/lower = middle +/- k * stdev(closes)`.

    **Deliberate deviation from the "textbook" convention, stated explicitly
    (same precedent as `risk_management.AverageTrueRange`/`regime_weighting.
    AverageDirectionalIndex`'s own docstrings)**: uses **sample** standard
    deviation (ddof=1, dividing by `period - 1`), not the population
    standard deviation (ddof=0) most traditional Bollinger Band references
    use -- for consistency with this codebase's own established statistical
    convention (`volatility_targeting.RollingRealizedVolatility` and
    `metrics.metrics`'s Sharpe-ratio computation both already use ddof=1),
    not a claim that ddof=1 is a more "correct" choice for this specific
    indicator. The numeric difference between the two conventions is small
    (a factor of `sqrt(period / (period - 1))` on the band width, ~2.6% at
    period=20) and does not change this indicator's qualitative behavior.

    Look-ahead safety: `update(kline)` only ever reads `kline` and internal
    state accumulated from bars already fed -- same guarantee as every other
    incremental calculator in this codebase.
    """

    __slots__ = ("_period", "_k", "_closes")

    def __init__(self, period: int = DEFAULT_BOLLINGER_PERIOD, k: Decimal = DEFAULT_BOLLINGER_K) -> None:
        if period < 2:
            raise ValueError(f"period must be at least 2 (need >=2 closes for a sample stdev), got {period}")
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        self._period = period
        self._k = k
        self._closes: deque[Decimal] = deque(maxlen=period)

    @property
    def period(self) -> int:
        return self._period

    @property
    def bars_seen(self) -> int:
        return len(self._closes)

    def update(self, kline: Kline) -> tuple[Decimal, Decimal, Decimal] | None:
        self._closes.append(kline.close)
        if len(self._closes) < self._period:
            return None

        closes = list(self._closes)
        mean = sum(closes) / self._period
        variance = sum((c - mean) ** 2 for c in closes) / (self._period - 1)
        stdev = variance.sqrt()
        band_width = self._k * stdev
        return mean, mean + band_width, mean - band_width


def _bollinger_signal(close: Decimal, lower: Decimal, upper: Decimal) -> int:
    """`+1` (oversold -> want LONG, expecting reversion up) if `close` is
    strictly below `lower`; `-1` (overbought -> want SHORT, expecting
    reversion down) if `close` is strictly above `upper`; `0` (inside the
    bands, no reversion signal) otherwise. Strict inequalities, matching
    every other sign helper in this package (e.g. `ensemble_momentum._sign`).
    """
    if close < lower:
        return 1
    if close > upper:
        return -1
    return 0


class MeanReversionStrategy:
    """Bound, stateful `Strategy`: Bollinger-Band mean-reversion signal,
    edge-triggered, plus ATR-based stop/target exits, ADX-based continuous
    regime weighting (INVERTED relative to the momentum strategies -- see
    module docstring), and real volatility targeting.
    """

    __slots__ = (
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
        return self._bands.bars_seen

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        atr = self._atr.update(current)
        adx = self._adx.update(current)
        realized_vol = self._vol.update(current)
        bands = self._bands.update(current)

        current_signal = 0
        if bands is not None:
            _, upper, lower = bands
            current_signal = _bollinger_signal(current.close, lower, upper)

        intent: OrderIntent | None = None

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif bands is not None:
            previous_signal = self._signal_state
            if (
                previous_signal is not None
                and current_signal != 0
                and current_signal != previous_signal
                and atr is not None
                and atr > 0
            ):
                side = Side.LONG if current_signal > 0 else Side.SHORT
                intent = self._open(current, side, atr, adx, realized_vol)

        if current_signal != 0:
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

        # INVERTED regime weight -- see module docstring's "Inverted regime
        # gating" section. compute_regime_weight itself is reused
        # unmodified; only its complement is taken here.
        regime_weight = Decimal(1) - compute_regime_weight(adx, low=self._adx_low, high=self._adx_high)
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None

        final_quantity = base_quantity * regime_weight * vol_scalar
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


class MeanReversionTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `MeanReversionStrategy`.

    `fit()` grid-searches nothing -- every constant (Bollinger period/k,
    ATR/ADX/vol periods, ADX thresholds, stop/target multipliers, vol-target
    level/bounds, risk fraction) is fixed at `MeanReversionTrainable`
    construction time, matching `EnsembleMomentumTrainable`'s default (no-
    `"candidates"`-key) behavior exactly: one candidate built from
    `params["symbol"]` (default `DEFAULT_SYMBOL`) plus this instance's fixed
    constants, backtested against `train_klines` only, logged as its own
    `record_type: "backtest_run"` entry (`candidate_index=0`,
    `total_candidates=1`).

    Deliberately does NOT include `ensemble_momentum.py`'s Task I opt-in
    risk:reward grid-search mechanism -- this is a brand-new, not-yet-
    evaluated signal family; adding a second tunable dimension before this
    signal has even been evaluated once would add search surface (and
    MinBTL-style overfitting risk, `research.overfitting_check`) ahead of
    any evidence it's warranted. See `.planning/sr-k-mean-reversion-and-
    blend.md` for this judgment call.
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

    def _build_strategy(self, *, symbol: str) -> MeanReversionStrategy:
        return MeanReversionStrategy(
            symbol=symbol,
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
                    "in-sample scoring inside MeanReversionTrainable.fit() -- the single fixed "
                    "Bollinger-Band strategy, no grid search -- not itself a walk-forward run"
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
