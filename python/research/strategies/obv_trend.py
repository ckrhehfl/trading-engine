"""On-Balance Volume trend-confirmation strategy -- Strategy Research
Task L's standalone volume-based candidate. See CLAUDE.md's "Strategy
Research Methodology" section and `.planning/sr-l-volume-signal.md` for
the full research context: Step 1 of that task diagnosed whether volume
around entry discriminates `ensemble_momentum.py`'s recalibrated-ADX
"Configuration C" (`.planning/sr-i-ensemble-refinement.md`) own real
winning trades from its losing trades, and found NO real, robust
discriminating power (Welch's two-sample t-test on entry-bar
volume-vs-rolling-average ratio, winners vs losers: p=0.37 at the primary
20-bar window, and not significant at either a 10-bar or 30-bar window
either; a two-proportion z-test on above/below-rolling-average win rate:
p=0.34; a volume-spike (>=1.5x average) vs no-spike win-rate comparison:
p=0.11, still short of conventional significance; point-biserial
correlation between win/loss and entry volume ratio: r=0.07, p=0.34). Per
that task's own explicit branching instruction, this module does NOT bolt
a volume filter onto Configuration C's existing signal -- that path is not
justified by what the diagnosis actually found. Instead, this is a
genuinely standalone, independently-evaluated volume-based strategy.

## Why On-Balance Volume, not price/volume divergence or a
   volume-confirmed breakout

All three are legitimate, long-established technical-analysis concepts;
this is a documented judgment call, not a claim of objective superiority.
On-Balance Volume (Joseph Granville, 1963) was chosen because:

1. It is a genuinely DIFFERENT kind of volume signal than what Step 1's
   diagnosis already tested and found non-discriminating on this data.
   Step 1 tested raw entry-bar volume LEVEL relative to a trailing average
   (and a volume-"spike" threshold on that same level) -- both a static,
   single-bar comparison. OBV is a CUMULATIVE, path-dependent running
   total of signed volume (directional volume flow accumulated over
   time), structurally distinct from a single-bar level comparison. A
   volume-confirmed-breakout design would have re-tested essentially the
   same "is this bar's volume elevated" question Step 1 already answered
   negatively, just gating a different base signal (a Donchian-style
   breakout) instead of `ensemble_momentum.py`'s SMA crossover -- not a
   meaningfully different question given what was already found.
   Price/volume divergence is conceptually interesting but requires
   detecting local price extrema and comparing them against OBV's own
   local extrema -- a materially more complex detection algorithm with
   more implementation-risk surface for a first, exploratory pass at this
   signal family.
2. OBV admits the exact same "value vs. its own trailing rolling mean,
   sign-based, edge-triggered" structural shape this codebase already
   uses for price (`single_lookback_momentum.py`'s fast/slow SMA
   crossover) and for Bollinger Bands (`mean_reversion.py`'s close vs. its
   own rolling mean +/- k*stdev) -- so this module's composition with the
   shared regime-weighting/risk-management/volatility-targeting
   infrastructure is a direct, low-risk reuse of an already-tested
   pattern, not a new composition shape needing its own from-scratch
   design.
3. It is a genuinely standard, decades-old technique (not a novel
   invention with unknown backtesting properties), consistent with every
   other indicator choice in this package (ATR, ADX, Bollinger Bands are
   all similarly conventional).

## Non-inverted regime gating -- OBV trend is a momentum/confirmation
   signal, NOT a reversion one

Unlike `mean_reversion.py` (which inverts `regime_weighting.
compute_regime_weight` because reversion needs a RANGING market to work),
OBV trend-following is directionally the same kind of signal as
`ensemble_momentum.py`/`single_lookback_momentum.py`: it wants a market
that is actually moving/trending, not chopping sideways -- volume flow
confirming a directional move is a trend-following concept. This module
therefore uses `compute_regime_weight` UNMODIFIED, the same (non-inverted)
direction the momentum strategies use: full conviction at/above the high
ADX threshold, near-zero at/below the low one.

## What's reused, unmodified, from sibling modules

- `research.strategies.regime_weighting`: `AverageDirectionalIndex`,
  `compute_regime_weight` (NOT inverted -- see above).
- `research.strategies.risk_management`: `AverageTrueRange`,
  `OpenPosition`, `compute_stop_and_target`, `compute_position_size`,
  `check_exit_trigger` -- the identical ATR-based stop/target/sizing every
  other strategy in this package uses, composed the same way.
- `research.strategies.volatility_targeting`: `RollingRealizedVolatility`,
  `compute_vol_scalar` -- the identical real volatility-targeting position
  scalar every other strategy in this package uses.

`final_quantity = atr_sized_base_quantity * regime_weight * vol_scalar` --
structurally identical composition to every sibling strategy module.

## Edge-triggering, including the entry_rejected_by_filters fix from day one

Same "fire only when the signal STATE changes" convention every strategy
in this package uses. `mean_reversion.py`/`momentum_reversion_blend.py`
each needed a real fix during Strategy Research Task K's CodeRabbit review
for a genuine functional-correctness bug: an attempted-but-filter-rejected
entry (regime weight/vol scalar suppressing `final_quantity` to `<= 0`)
must NOT consume the edge-trigger state, or a signal that never actually
traded gets silently, permanently missed once conditions later become
favorable (unless the raw signal happens to change again first). This
module builds that fix in from the start (`entry_rejected_by_filters`,
identical mechanism to `mean_reversion.py`'s) rather than repeating the
same review round-trip on brand-new code.
`EnsembleMomentumStrategy`/`SingleLookbackMomentumStrategy` still lack this
fix (already-shipped, already-tested, out of this task's scope) -- a
known, disclosed inconsistency, same as `mean_reversion.py`'s own
docstring already discloses.

## Warmup

`OnBalanceVolume.update()` returns `None` for the very first bar ever fed
(no prior close exists to compare against -- same "no evidence yet"
convention as `risk_management.AverageTrueRange`/`regime_weighting.
AverageDirectionalIndex`'s own first-bar warmup). Once OBV itself is
defined, a further `obv_ma_period` OBV readings are needed before this
module's own rolling OBV-SMA is full -- same "no evidence = no signal,
don't let the strategy's character silently change during warmup"
convention as `BollingerBands`/`EnsembleMomentumStrategy`.
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

# Single-symbol/native-1h conventions matching every sibling strategy
# module in this package.
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")
DEFAULT_BARS_PER_DAY = 24

# 20-bar rolling OBV signal-line period -- matches this package's other
# 20-bar rolling conventions (`mean_reversion.DEFAULT_BOLLINGER_PERIOD`,
# `volatility_targeting.DEFAULT_VOL_LOOKBACK_PERIOD`) and a commonly cited
# OBV "signal line" length in technical-analysis references. Not
# searched/tuned to this asset, same "few tunable knobs" discipline as
# every other indicator constant in this package.
DEFAULT_OBV_MA_PERIOD = 20


class OnBalanceVolume:
    """Incremental, look-ahead-safe On-Balance Volume over a bar-by-bar
    stream, fed one `Kline` per call via `update()`.

    Standard Granville (1963) definition: a single cumulative running
    total, `obv[t] = obv[t-1] + volume[t]` if `close[t] > close[t-1]`,
    `obv[t] - volume[t]` if `close[t] < close[t-1]`, unchanged if
    `close[t] == close[t-1]`.

    Returns `None` for the very first bar ever fed (no prior close exists
    to compare against -- same "no evidence yet" convention as
    `risk_management.AverageTrueRange`/`regime_weighting.
    AverageDirectionalIndex`'s own first-bar warmup); a real `Decimal`
    cumulative value (starting from `Decimal(0)`, the natural baseline for
    a running total whose absolute level is otherwise arbitrary -- only
    its trend/slope relative to its own trailing mean is meaningful, see
    `_obv_trend_signal`) for every bar from the second onward.

    Look-ahead safety: `update(kline)` only ever reads `kline` (the
    current bar) and internal state accumulated from bars already fed --
    same guarantee as every other incremental calculator in this
    codebase.
    """

    __slots__ = ("_prev_close", "_cumulative")

    def __init__(self) -> None:
        self._prev_close: Decimal | None = None
        self._cumulative = Decimal(0)

    def update(self, kline: Kline) -> Decimal | None:
        if self._prev_close is None:
            self._prev_close = kline.close
            return None
        if kline.close > self._prev_close:
            self._cumulative += kline.volume
        elif kline.close < self._prev_close:
            self._cumulative -= kline.volume
        self._prev_close = kline.close
        return self._cumulative


def _obv_trend_signal(obv: Decimal, obv_sma: Decimal) -> int:
    """`+1` (volume flow confirms an up-move) if `obv` is strictly above
    its own trailing rolling mean, `-1` (confirms a down-move) if strictly
    below, `0` (no signal) if exactly equal -- same strict-inequality sign
    convention as every other signal helper in this package (e.g.
    `ensemble_momentum._sign`, `mean_reversion._bollinger_signal`).
    """
    if obv > obv_sma:
        return 1
    if obv < obv_sma:
        return -1
    return 0


class ObvTrendStrategy:
    """Bound, stateful `Strategy`: On-Balance-Volume-vs-its-own-rolling-
    mean signal, edge-triggered, plus ATR-based stop/target exits,
    ADX-based continuous regime weighting (NOT inverted -- see module
    docstring), and real volatility targeting.
    """

    __slots__ = (
        "_obv_ma_period",
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
        "_obv",
        "_obv_values",
        "_atr",
        "_adx",
        "_vol",
        "_position",
    )

    def __init__(
        self,
        *,
        symbol: str,
        obv_ma_period: int = DEFAULT_OBV_MA_PERIOD,
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
        if obv_ma_period < 1:
            raise ValueError(f"obv_ma_period must be at least 1, got {obv_ma_period}")
        self._obv_ma_period = obv_ma_period
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
        self._obv = OnBalanceVolume()
        self._obv_values: deque[Decimal] = deque(maxlen=obv_ma_period)
        self._atr = AverageTrueRange(period=atr_period)
        self._adx = AverageDirectionalIndex(period=adx_period)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position: OpenPosition | None = None

    @property
    def obv_ma_period(self) -> int:
        return self._obv_ma_period

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
        return len(self._obv_values)

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        atr = self._atr.update(current)
        adx = self._adx.update(current)
        realized_vol = self._vol.update(current)
        obv = self._obv.update(current)

        current_signal = 0
        obv_ready = False
        if obv is not None:
            self._obv_values.append(obv)
            if len(self._obv_values) >= self._obv_ma_period:
                obv_ready = True
                obv_sma = sum(self._obv_values) / len(self._obv_values)
                current_signal = _obv_trend_signal(obv, obv_sma)

        intent: OrderIntent | None = None
        entry_rejected_by_filters = False

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif obv_ready:
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
                entry_rejected_by_filters = intent is None

        # Do NOT consume the edge-trigger state when an entry was actually
        # attempted but rejected purely by a downstream filter (regime
        # weight/vol scalar suppressing final_quantity to <= 0, or vol
        # warmup) -- see module docstring's "Edge-triggering" section.
        # Every other case (no entry was attempted at all, e.g. still in a
        # position, or no prior state existed yet) keeps the original
        # unconditional update -- needed so a sign transition while a
        # position is open is still tracked correctly.
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

        # NOT inverted -- see module docstring's "Non-inverted regime
        # gating" section. compute_regime_weight is reused exactly as the
        # momentum strategies use it.
        regime_weight = compute_regime_weight(adx, low=self._adx_low, high=self._adx_high)
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


class ObvTrendTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `ObvTrendStrategy`.

    `fit()` grid-searches nothing -- every constant (OBV-MA period,
    ATR/ADX/vol periods, ADX thresholds, stop/target multipliers,
    vol-target level/bounds, risk fraction) is fixed at
    `ObvTrendTrainable` construction time, matching `MeanReversionTrainable`'s
    default behavior exactly: one candidate built from `params["symbol"]`
    (default `DEFAULT_SYMBOL`) plus this instance's fixed constants,
    backtested against `train_klines` only, logged as its own
    `record_type: "backtest_run"` entry (`candidate_index=0`,
    `total_candidates=1`).

    Deliberately does NOT include a grid search of any kind -- this is a
    brand-new, not-yet-evaluated signal family; adding a tunable dimension
    before this signal has even been evaluated once would add search
    surface (and MinBTL-style overfitting risk, `research.
    overfitting_check`) ahead of any evidence it's warranted. Same
    judgment call `MeanReversionTrainable`/`MomentumReversionBlendTrainable`
    made in Strategy Research Task K.
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
        obv_ma_period: int = DEFAULT_OBV_MA_PERIOD,
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
        self._obv_ma_period = obv_ma_period
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

    def _build_strategy(self, *, symbol: str) -> ObvTrendStrategy:
        return ObvTrendStrategy(
            symbol=symbol,
            obv_ma_period=self._obv_ma_period,
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
                "obv_ma_period": self._obv_ma_period,
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
                    "in-sample scoring inside ObvTrendTrainable.fit() -- the single fixed "
                    "OBV-trend strategy, no grid search -- not itself a walk-forward run"
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
