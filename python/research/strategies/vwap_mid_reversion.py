"""VWAP-to-mid deviation short-term reversion -- Scalping Strategy
Research Task S4's first candidate. See CLAUDE.md's "Scalping Strategy
Research" section and `.planning/scalp-s4-vwap-mid-reversion.md` for the
full design record (every judgment call below, with its reasoning) --
this docstring is the implementation-level summary.

## The signal: zero-fitted-parameter VWAP-band reversion

A rolling, volume-weighted average price (VWAP) over the trailing
**20 bars** (20 minutes on native 1m data), with a Bollinger-Band-shaped
envelope around it: `upper/lower = vwap +/- 2 * stdev(closes)` over the
same 20-bar window. `close < lower` signals oversold (want LONG,
expecting reversion up); `close > upper` signals overbought (want SHORT,
expecting reversion down).

**Both constants (period=20, k=2) are external, practitioner/technical-
analysis conventions, not fitted to this project's own data**: they are
the *exact same* values this package's own `mean_reversion.py` already
uses for its (differently-centered) Bollinger Bands
(`DEFAULT_BOLLINGER_PERIOD = 20`, `DEFAULT_BOLLINGER_K = Decimal("2")`),
reused here rather than re-derived, plus independent real 2026-08-25 web
research corroborating both as standard conventions specifically for
VWAP-band reversion trading (2 standard deviations as the most commonly
cited VWAP-reversion entry threshold across multiple independent
sources, e.g. one source's own cited "63% reversion rate from
2-standard-deviation extensions"; 20 (or 50) periods on a 1-minute chart
as a common practitioner scalping-anchor convention). **Disclosed
honestly, not overclaimed**: no peer-reviewed academic paper was found
pinning down 20 (vs. any other period) specifically for a rolling
(non-session) VWAP lookback -- this is a practitioner/technical-analysis
convention, not an academic one. This mirrors the same honesty standard
CLAUDE.md already applies to the VWAP-to-mid hypothesis itself:
arXiv:2602.00776 ("Explainable Patterns in Cryptocurrency
Microstructure") supports the reversion *mechanism* at 1-second Binance
Futures order-book/trade frequency -- it does not validate this
specific 1-minute-OHLCV-proxy implementation, which remains a
hypothesis this holdout run tests, not an imported result.

## Why a literal Bollinger-Band shape, not a band on the deviation series

The band width is `k * stdev(closes)` -- the sample standard deviation
of the trailing window's raw **close prices**, the same quantity
`mean_reversion.BollingerBands` already computes, just with the
**centerline** replaced (volume-weighted average instead of a simple
mean). An equally defensible alternative would band the *deviation*
series (`close - vwap`) instead of raw price -- not chosen here, to keep
this indicator's band-width computation identical to the already-proven
`BollingerBands.update()` shape rather than introducing a second,
untested statistical convention. Sample stdev (ddof=1), matching every
other rolling-window calculator in this codebase
(`BollingerBands`, `volatility_targeting.RollingRealizedVolatility`,
`metrics.metrics`'s Sharpe-ratio computation).

## Edge-triggering, same convention as every sibling strategy

Same "fire only when the signal STATE changes" pattern as
`mean_reversion.MeanReversionStrategy`'s `_signal_state` and
`daily_tsmom_ensemble`'s sign-category triggering: a position opens on a
transition INTO a nonzero state (from `0` or the opposite nonzero
state), and closes on a transition back to `0` -- price reverting
*inside* the bands, the reversion thesis realized. A direct opposite-
state flip (`+1` straight to `-1`) is expressed as a single
`OrderIntent` combining the close-then-reopen quantity, exactly like
`daily_tsmom_ensemble.DailyTsmomEnsembleStrategy._transition_to` --
`metrics.position.PositionTracker`'s existing over-sized-order flip
handling is the same shared, already-proven machinery, reused rather
than reinvented.

## Sizing: constant-target volatility, no ATR stop, no ADX regime gate

Deliberately mirrors `daily_tsmom_ensemble.py`'s composition, not
`mean_reversion.py`'s: `research.strategies.volatility_targeting`
(`RollingRealizedVolatility`, `compute_vol_scalar`, both reused
**unmodified**) drives sizing directly against a full-notional reference
baseline -- `target_quantity = (reference_equity / entry_price) *
vol_scalar`. **No ATR stop/target, no ADX regime gate, no risk:reward
grid** -- every one of these came from this project's own 117-trial
1h-window search (`sr-r`'s statistical close-out); excluding them is
what makes this candidate's `free_parameter_count: 0` true, the same
reasoning `daily_tsmom_ensemble.py`'s own docstring gives for excluding
them there.

**A real, disclosed risk, deliberately not engineered around**: with no
ATR stop and no ADX regime filter, a position can be held indefinitely
if price stays beyond the 2-SD band without reverting -- the
well-documented mean-reversion-fails-in-a-strong-trend failure mode
(confirmed repeatedly in real 2026-08-25 web research on VWAP-reversion
trading: "a major caveat across sources is the need to avoid trending
markets... the filter is what separates a tradeable strategy from one
that blows up"). This is deliberately **not** mitigated here: adding an
ADX filter or a stop would reintroduce free parameters from the
already-spent, already-rejected 1h-window search apparatus. Any real
damage this causes will show up honestly in the Eligibility Bar's own
max-drawdown gating criterion rather than being silently avoided.

## Order emission and timeframe

`OrderType.GUARDED_MARKET` **only**, hardcoded (not a parameter) -- per
CLAUDE.md's Task S2 finding that `slippage_bps` is inert for `LIMIT`
orders in this backtest engine, so a `LIMIT`-based scalping candidate is
unverifiable under it for now. `signal_timeframe="1m"` on every emitted
`OrderIntent`. `DEFAULT_BARS_PER_DAY = 1440` (native 1-minute bars).

## Deliberately absent

No ADX regime gate, no ATR stop/target, no risk:reward grid, no funding
signal driving direction or size (funding P&L accounting is also not
threaded in -- see the governing pre-registration's `funding_included:
false`: unlike `daily_tsmom_ensemble`'s reason (no holdout funding
loader exists yet), here funding literally cannot accrue meaningfully
within a minutes-to-tens-of-minutes holding period against BingX's 8-
hour funding cadence, so it is structurally irrelevant to this
strategy's own economics, not merely unbuilt).
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
from research.strategies.volatility_targeting import (
    DEFAULT_MAX_VOL_SCALAR,
    DEFAULT_MIN_VOL_SCALAR,
    DEFAULT_TARGET_ANNUALIZED_VOL,
    DEFAULT_VOL_LOOKBACK_PERIOD,
    RollingRealizedVolatility,
    compute_vol_scalar,
)
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol/native-1m conventions matching every sibling strategy
# module in this package.
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")

# This strategy's native timeframe is 1m (1440 bars/day) -- see CLAUDE.md's
# Scalping Strategy Research Task S3: every RollingRealizedVolatility/
# compute_metrics call in this module must pass this explicitly, or a
# reported Sharpe/annualized-vol figure would be silently computed against
# the wrong (15m/1h/1d) assumption.
DEFAULT_BARS_PER_DAY = 1440

# Same external convention `mean_reversion.py`'s DEFAULT_BOLLINGER_PERIOD/
# DEFAULT_BOLLINGER_K already use -- reused, not re-derived. See module
# docstring for the full justification and citations.
DEFAULT_VWAP_PERIOD = 20
DEFAULT_DEVIATION_K = Decimal("2")

# Full-notional sizing baseline for constant-target-vol sizing (see module
# docstring): base_quantity = reference_equity / entry_price. Same
# magnitude convention as daily_tsmom_ensemble.DEFAULT_REFERENCE_EQUITY.
DEFAULT_REFERENCE_EQUITY = Decimal("10000")


class VwapMidBands:
    """Incremental, look-ahead-safe VWAP-centered band calculator over a
    bar-by-bar stream of (close, volume) pairs, fed one `Kline` per call
    via `update()`.

    `update()` returns `(vwap, upper, lower)` once at least `period`
    bars have been fed, else `None` (warmup): `vwap = sum(close_i *
    volume_i) / sum(volume_i)` over the trailing window;
    `upper/lower = vwap +/- k * stdev(closes)` (sample stdev, ddof=1, of
    the SAME window's raw close prices -- see module docstring for why
    this bands the raw price series rather than the deviation series).

    **Zero-total-volume window handled explicitly**: if `sum(volume_i)`
    over the window is exactly zero (a real edge case a rolling window
    straddling one of BingX's 2 known real 1m gaps -- see CLAUDE.md's
    "Exchange API Facts -- BingX" -- could plausibly produce, though
    never observed in the confirmed real backfill), `update()` returns
    `None` for that bar rather than dividing by zero -- same "no
    evidence yet" treatment as ordinary warmup, not a crash.

    Look-ahead safety: `update(kline)` only ever reads `kline` and
    internal state accumulated from bars already fed -- same guarantee
    as every other incremental calculator in this codebase.
    """

    __slots__ = ("_period", "_k", "_closes", "_volumes")

    def __init__(self, period: int = DEFAULT_VWAP_PERIOD, k: Decimal = DEFAULT_DEVIATION_K) -> None:
        if period < 2:
            raise ValueError(f"period must be at least 2 (need >=2 closes for a sample stdev), got {period}")
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        self._period = period
        self._k = k
        self._closes: deque[Decimal] = deque(maxlen=period)
        self._volumes: deque[Decimal] = deque(maxlen=period)

    @property
    def period(self) -> int:
        return self._period

    @property
    def bars_seen(self) -> int:
        return len(self._closes)

    def update(self, kline: Kline) -> tuple[Decimal, Decimal, Decimal] | None:
        self._closes.append(kline.close)
        self._volumes.append(kline.volume)
        if len(self._closes) < self._period:
            return None

        closes = list(self._closes)
        volumes = list(self._volumes)
        total_volume = sum(volumes)
        if total_volume == 0:
            return None  # degenerate zero-volume window -- no evidence, not a crash

        vwap = sum(c * v for c, v in zip(closes, volumes)) / total_volume

        mean = sum(closes) / self._period
        variance = sum((c - mean) ** 2 for c in closes) / (self._period - 1)
        stdev = variance.sqrt()
        band_width = self._k * stdev
        return vwap, vwap + band_width, vwap - band_width


def _vwap_signal(close: Decimal, lower: Decimal, upper: Decimal) -> int:
    """`+1` (oversold -> want LONG, expecting reversion up) if `close` is
    strictly below `lower`; `-1` (overbought -> want SHORT, expecting
    reversion down) if `close` is strictly above `upper`; `0` (inside the
    bands, no reversion signal) otherwise. Mirrors
    `mean_reversion._bollinger_signal`'s exact shape/strict-inequality
    convention.
    """
    if close < lower:
        return 1
    if close > upper:
        return -1
    return 0


class VwapMidReversionStrategy:
    """Bound, stateful `Strategy`: the VWAP-mid-deviation reversion signal
    (see module docstring), constant-target-volatility sizing,
    edge-triggered on signal **state** changes. No ATR stop/target, no
    ADX regime gate: a position is held until the signal itself reverts
    to neutral or flips to the opposite extreme.
    """

    __slots__ = (
        "_bands",
        "_symbol",
        "_reference_equity",
        "_target_annualized_vol",
        "_min_vol_scalar",
        "_max_vol_scalar",
        "_vol",
        "_signal_state",
        "_position_sign",
        "_position_quantity",
    )

    def __init__(
        self,
        *,
        symbol: str,
        vwap_period: int = DEFAULT_VWAP_PERIOD,
        deviation_k: Decimal = DEFAULT_DEVIATION_K,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
    ) -> None:
        if reference_equity <= 0:
            raise ValueError(f"reference_equity must be positive, got {reference_equity}")

        self._bands = VwapMidBands(period=vwap_period, k=deviation_k)
        self._symbol = symbol
        self._reference_equity = reference_equity
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar

        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._signal_state: int | None = None
        self._position_sign = 0
        self._position_quantity = Decimal(0)

    @property
    def vwap_period(self) -> int:
        return self._bands.period

    @property
    def position_sign(self) -> int:
        """The currently held position's sign category: `+1` (long),
        `-1` (short), or `0` (flat). Exposed read-only for testing/
        introspection, same convention as `daily_tsmom_ensemble`'s
        `position_sign`.
        """
        return self._position_sign

    @property
    def position_quantity(self) -> Decimal:
        return self._position_quantity

    @property
    def bars_seen(self) -> int:
        return self._bands.bars_seen

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]
        realized_vol = self._vol.update(current)
        bands = self._bands.update(current)

        current_signal = 0
        if bands is not None:
            _, upper, lower = bands
            current_signal = _vwap_signal(current.close, lower, upper)

        intent: OrderIntent | None = None
        entry_rejected_by_filters = False

        if bands is not None:
            previous_signal = self._signal_state
            if previous_signal is not None and current_signal != previous_signal:
                if current_signal == 0:
                    intent = self._flatten_to(current)
                else:
                    target_quantity = self._compute_target_quantity(current, realized_vol)
                    if target_quantity is None:
                        # Cannot size the new leg right now (vol-targeting
                        # warmup, or a degenerate price) -- still relinquish
                        # a stale position if one is held (see module
                        # docstring's "daily_tsmom_ensemble-style" handling),
                        # but do NOT consume the edge-trigger state below:
                        # same real, evidenced defect class
                        # mean_reversion.MeanReversionStrategy's own
                        # docstring documents fixing -- without this, a
                        # signal that fires the trigger but gets rejected
                        # purely by sizing would be silently "consumed",
                        # permanently missing the entry once sizing becomes
                        # available unless the raw band-breach reading
                        # happens to change again first.
                        intent = self._flatten_to(current)
                        entry_rejected_by_filters = True
                    else:
                        intent = self._transition_to(current, current_signal, target_quantity)

        if not entry_rejected_by_filters:
            self._signal_state = current_signal

        return intent

    def _compute_target_quantity(self, current: Kline, realized_vol: Decimal | None) -> Decimal | None:
        entry_price = current.close
        if entry_price <= 0:
            return None
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None
        target_quantity = (self._reference_equity / entry_price) * vol_scalar
        if target_quantity <= 0:
            return None
        return target_quantity

    def _flatten_to(self, current: Kline) -> OrderIntent | None:
        if self._position_sign == 0:
            return None
        closing_side = Side.SHORT if self._position_sign > 0 else Side.LONG
        quantity = self._position_quantity
        self._position_sign = 0
        self._position_quantity = Decimal(0)
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=closing_side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=quantity,
            limit_price=None,
            signal_timeframe="1m",
            created_at=current.open_time,
        )

    def _transition_to(self, current: Kline, new_sign: int, target_quantity: Decimal) -> OrderIntent:
        # Delta quantity: the residual to close out of the OLD sign's
        # position (if any) plus the full new target -- same same-bar-flip
        # pattern as daily_tsmom_ensemble.DailyTsmomEnsembleStrategy
        # ._transition_to (metrics.position.PositionTracker already
        # interprets an over-sized order as close-then-open-residual).
        closing_quantity = self._position_quantity if self._position_sign != 0 else Decimal(0)
        order_quantity = closing_quantity + target_quantity
        side = Side.LONG if new_sign > 0 else Side.SHORT
        self._position_sign = new_sign
        self._position_quantity = target_quantity
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=order_quantity,
            limit_price=None,
            signal_timeframe="1m",
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


class VwapMidReversionTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `VwapMidReversionStrategy`.

    **`fit()` performs no search whatsoever** -- the governing
    pre-registration's `total_candidates: 1`. It builds exactly one
    `VwapMidReversionStrategy` from this instance's construction-time
    constants (`vwap_period`/`deviation_k`, fixed at
    `DEFAULT_VWAP_PERIOD`/`DEFAULT_DEVIATION_K`), backtests it in-sample
    against `train_klines` for logging purposes only, logs that single
    evaluation as its own `record_type: "backtest_run"` entry
    (`candidate_index=0, total_candidates=1`), and returns a **fresh**
    strategy instance -- never the one used for in-sample scoring, which
    would carry leftover internal state (rolling bands, vol estimator,
    open position). Mirrors `DailyTsmomEnsembleTrainable`'s exact
    structure.
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
        vwap_period: int = DEFAULT_VWAP_PERIOD,
        deviation_k: Decimal = DEFAULT_DEVIATION_K,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
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
        self._vwap_period = vwap_period
        self._deviation_k = deviation_k
        self._reference_equity = reference_equity
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

    def _build_strategy(self, *, symbol: str) -> VwapMidReversionStrategy:
        return VwapMidReversionStrategy(
            symbol=symbol,
            vwap_period=self._vwap_period,
            deviation_k=self._deviation_k,
            reference_equity=self._reference_equity,
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
                "vwap_period": self._vwap_period,
                "deviation_k": str(self._deviation_k),
                "reference_equity": str(self._reference_equity),
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
                    "in-sample scoring inside VwapMidReversionTrainable.fit() -- the single, "
                    "fixed, zero-fitted-parameter VWAP-mid-reversion signal, no search -- not "
                    "itself a walk-forward run"
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
