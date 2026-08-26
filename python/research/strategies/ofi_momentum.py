"""Order-flow-imbalance momentum -- Scalping Strategy Research Task S6's
second candidate. See CLAUDE.md's "Scalping Strategy Research" section
and `.planning/scalp-s6-ofi-momentum.md` for the full design record
(every judgment call below, with its reasoning) -- this docstring is the
implementation-level summary.

## The signal: zero-fitted-parameter order-flow-imbalance momentum

A rolling **15-bar** ("quarter-hour", 15 minutes on native `1m` Binance
USDT-M futures data) mean and sample standard deviation of per-bar order
flow imbalance, `ofi_t = 2 * taker_buy_base_volume_t / volume_t - 1`
(range `[-1, +1]`: `+1` = 100% of this bar's volume was buyer-initiated,
`-1` = 100% seller-initiated). `close`-equivalent band:
`upper/lower = mean(ofi) +/- 2 * stdev(ofi)` over the same 15-bar window.
`ofi_t > upper` signals unusually strong recent buy-side pressure -->
**LONG** (momentum/continuation: the imbalance is expected to keep
pushing price the *same* direction, not revert); `ofi_t < lower` -->
**SHORT**.

**Both the 15-bar window and the 2-standard-deviation threshold are
external conventions, not fitted to this project's own data**: the
15-bar ("quarter-hour") window is named directly after, and grounded in,
Kim & Hansen (2026), "The Quarter-Hour Effect," arXiv:2607.09426, real
Binance USDT perpetuals research already cited in CLAUDE.md's Scalping
Strategy Research section; the 2-SD threshold is the *same* external
statistical convention this package has now reused a third time
(`mean_reversion.BollingerBands`, `vwap_mid_reversion.VwapMidBands`, now
this), applied to a different underlying series (order-flow imbalance,
not price).

**Disclosed honestly, not overclaimed**: the real cited paper's own
finding is that opening order imbalance predicts returns over **4-12
hours**, "with much weaker effects at finer clock-time frequencies" --
it does not validate this module's much shorter (minutes-to-tens-of-
minutes-scale) adaptation. This registration is a genuine, disclosed
hypothesis test of whether the *mechanism* (order-flow imbalance
predicting continuation, not reversion) transfers to a scalping-relevant
horizon -- not an imported result, the same honesty standard
`vwap_mid_reversion.py` already applied to its own 1-second-order-book-
vs-1-minute-OHLCV proxy gap.

## Real risk control -- the deliberate, disclosed fix for
## `vwap_mid_reversion.py`'s own catastrophic failure

`vwap_mid_reversion.py`'s real holdout result (`.planning/scalp-s4-vwap-
mid-reversion-result.md`) confirmed, catastrophically, the risk its own
docstring had disclosed in advance: with no ATR stop and no regime
filter, an unbounded mean-reversion position held against a market that
simply never reverted produced a raw drawdown of 10,619%. That module's
own closing disclosure named the real, open question for "a future
task": whether a stop-loss or regime filter should be treated as a
legitimate, literature-sourced (zero-*search*, not necessarily
zero-*parameter*) risk control. This module is that future task's
answer, for a momentum (not reversion) signal specifically.

Exit is **pure ATR-based stop/target, no signal-based exit at all** --
reusing `research.strategies.risk_management` **completely unmodified**:
`AverageTrueRange` (`DEFAULT_ATR_PERIOD=14`, Wilder's own standard
period), `compute_stop_and_target` (`DEFAULT_STOP_MULTIPLIER=1.5`,
`DEFAULT_TARGET_MULTIPLIER=3.0` -- both that module's own already-
documented external conventions, "not searched or tuned to this asset"
per its own docstring), `compute_position_size` (fixed-fractional,
`DEFAULT_REFERENCE_EQUITY=10000`, `DEFAULT_RISK_FRACTION=0.01` -- i.e.
exactly 1% of reference equity risked per trade, by construction,
bounding worst-case per-trade loss structurally rather than hoping a
signal-based exit fires in time), `check_exit_trigger` (stop-wins
same-bar tie-break), `OpenPosition`. Composition mirrors
`hourly_momentum.HourlyMomentumStrategy._open`/`_flatten` exactly (the
established ATR-stop/target precedent in this package), not
`vwap_mid_reversion.py`'s own signal-only exit shape.

**Disclosed consequence of this choice**: unlike `vwap_mid_reversion.py`'s
implicit ~20-minute average holding period, this design's real holding
period is price-determined (however long it takes price to move 1.5x or
3x ATR), not time-bounded -- it could occasionally exceed "tens of
minutes" in a slow-moving market. This is a disclosed, accepted
characteristic of reusing `hourly_momentum.py`'s own proven exit
pattern, not a scope violation snuck in.

## Entry, edge-triggered, only while flat

Mirrors `hourly_momentum.HourlyMomentumStrategy.__call__`'s exact
composition, not `vwap_mid_reversion.py`'s: while a position is open,
`__call__` only ever checks `check_exit_trigger` -- no new entry is
evaluated at all until flat again. State tracking (`previous_sign`/
`current_sign`, fire only on a genuine category change) mirrors
`HourlyMomentumStrategy`'s own `_crossover_sign` pattern.

## Order emission and timeframe

`OrderType.GUARDED_MARKET` **only**, hardcoded -- per CLAUDE.md's Task
S2 finding that `slippage_bps` is inert for `LIMIT` orders in this
backtest engine. `signal_timeframe="1m"`. `DEFAULT_BARS_PER_DAY = 1440`
(native 1-minute bars).

## Deliberately absent

No ADX regime gate, no volatility-targeting scalar -- `free_parameter_
count: 0` per the governing pre-registration: every number here (15-bar
window, 2-SD threshold, ATR period/stop/target multipliers, 1% risk
fraction) is an external convention, not fit to this project's own data.
`funding_included: false` -- this project has no Binance funding-rate
data pipeline at all (structural, not a design choice, unlike
`vwap_mid_reversion`'s or `daily_tsmom_ensemble`'s own different
`funding_included=false` reasons).
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

# This strategy's venue/symbol scope: Binance USDT-M futures BTCUSDT, the
# real order-flow-data source Scalping Strategy Research Task S5 built --
# NOT this project's own live-trading symbol (BingX BTC-USDT), which has
# no buyer/seller volume breakdown on its wire at all. See module
# docstring's own disclosed cross-venue-transferability caveat (inherited
# from Task S5, not re-litigated here).
DEFAULT_SYMBOL = "BINANCE-FUTURES:BTCUSDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")

# Native 1m bars (1440/day) -- every RollingRealizedVolatility/
# compute_metrics call in this module must pass this explicitly, or a
# reported Sharpe/annualized-vol figure would be silently computed
# against the wrong (15m/1h/1d) assumption. Same convention
# `vwap_mid_reversion.py` already established for the sibling BingX 1m
# candidate.
DEFAULT_BARS_PER_DAY = 1440

# "Quarter-Hour" -- 15 one-minute bars, named directly after and grounded
# in Kim & Hansen (2026) arXiv:2607.09426's own real research window
# convention. See module docstring for the full justification and the
# honest horizon-mismatch disclosure (that paper's own real finding is a
# 4-12 hour forward-return horizon, not this module's much shorter one).
DEFAULT_OFI_WINDOW_BARS = 15

# Same external 2-standard-deviation convention this package has already
# used twice for a price-centered band (`mean_reversion.
# DEFAULT_BOLLINGER_K`, `vwap_mid_reversion.DEFAULT_DEVIATION_K`), reused
# here for an order-flow-imbalance-centered band instead. See module
# docstring.
DEFAULT_DEVIATION_K = Decimal("2")


class OfiBands:
    """Incremental, look-ahead-safe order-flow-imbalance band calculator
    over a bar-by-bar stream, fed one `Kline` per call via `update()`.

    `update()` returns `(mean_ofi, upper, lower)` once at least `period`
    bars with a defined per-bar OFI have been fed, else `None` (warmup).
    Per-bar OFI is `2 * taker_buy_base_volume / volume - 1`
    (`[-1, +1]`); a bar with `volume == 0` or a missing (`None`)
    `taker_buy_base_volume` (e.g. a BingX-sourced bar, or any bar
    predating Scalping Strategy Research Task S5's real-order-flow-data
    capture) contributes no observation for that bar -- same "no
    evidence yet" treatment as ordinary warmup, not a crash, and not a
    fabricated zero.

    `upper/lower = mean_ofi +/- k * stdev(ofi)` -- sample stdev (ddof=1),
    matching every other rolling-window calculator in this codebase
    (`BollingerBands`, `VwapMidBands`, `volatility_targeting.
    RollingRealizedVolatility`).

    Look-ahead safety: `update(kline)` only ever reads `kline` and
    internal state accumulated from bars already fed -- same guarantee
    as every other incremental calculator in this codebase.
    """

    __slots__ = ("_period", "_k", "_values")

    def __init__(self, period: int = DEFAULT_OFI_WINDOW_BARS, k: Decimal = DEFAULT_DEVIATION_K) -> None:
        if period < 2:
            raise ValueError(f"period must be at least 2 (need >=2 observations for a sample stdev), got {period}")
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        self._period = period
        self._k = k
        self._values: deque[Decimal] = deque(maxlen=period)

    @property
    def period(self) -> int:
        return self._period

    @property
    def bars_seen(self) -> int:
        return len(self._values)

    def update(self, kline: Kline) -> tuple[Decimal, Decimal, Decimal] | None:
        ofi = _per_bar_ofi(kline)
        if ofi is not None:
            self._values.append(ofi)

        if len(self._values) < self._period:
            return None

        values = list(self._values)
        mean = sum(values) / self._period
        variance = sum((v - mean) ** 2 for v in values) / (self._period - 1)
        stdev = variance.sqrt()
        band_width = self._k * stdev
        return mean, mean + band_width, mean - band_width


def _per_bar_ofi(kline: Kline) -> Decimal | None:
    """`2 * taker_buy_base_volume / volume - 1`, or `None` if this bar
    has no real order-flow data (`taker_buy_base_volume is None`, e.g. a
    BingX-sourced bar) or a degenerate zero-volume bar (would divide by
    zero) -- both treated as "no evidence for this bar", never a crash
    or a fabricated value.
    """
    if kline.taker_buy_base_volume is None:
        return None
    if kline.volume == 0:
        return None
    return 2 * kline.taker_buy_base_volume / kline.volume - 1


def _sign(close: Decimal, lower: Decimal, upper: Decimal, ofi: Decimal | None) -> int:
    """`+1` (strong recent buy-side imbalance -> want LONG, momentum) if
    the current bar's own OFI is strictly above `upper`; `-1` (strong
    sell-side imbalance -> want SHORT) if strictly below `lower`; `0`
    otherwise (including when this bar itself has no real OFI reading,
    `ofi is None`, even though the rolling band computation from prior
    bars is warm) -- mirrors `mean_reversion._bollinger_signal`'s/
    `vwap_mid_reversion._vwap_signal`'s exact shape/strict-inequality
    convention. `close` is unused (kept for signature symmetry with
    those two sibling signal functions -- this signal is order-flow-
    based, not price-based, so the *current* price plays no role in the
    trigger itself, only OFI does).
    """
    del close
    if ofi is None:
        return 0
    if ofi > upper:
        return 1
    if ofi < lower:
        return -1
    return 0


class OfiMomentumStrategy:
    """Bound, stateful `Strategy`: order-flow-imbalance momentum signal
    (see module docstring), ATR-based stop/target exits, fixed-
    fractional position sizing. Edge-triggered on signal state changes,
    entry evaluated only while flat -- mirrors
    `hourly_momentum.HourlyMomentumStrategy`'s exact composition, not
    `vwap_mid_reversion.py`'s (which had no ATR stop/target to compose
    against).
    """

    __slots__ = (
        "_bands",
        "_symbol",
        "_atr_period",
        "_stop_multiplier",
        "_target_multiplier",
        "_reference_equity",
        "_risk_fraction",
        "_atr",
        "_signal_state",
        "_position",
    )

    def __init__(
        self,
        *,
        symbol: str,
        ofi_window_bars: int = DEFAULT_OFI_WINDOW_BARS,
        deviation_k: Decimal = DEFAULT_DEVIATION_K,
        atr_period: int = DEFAULT_ATR_PERIOD,
        stop_multiplier: Decimal = DEFAULT_STOP_MULTIPLIER,
        target_multiplier: Decimal = DEFAULT_TARGET_MULTIPLIER,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
    ) -> None:
        self._bands = OfiBands(period=ofi_window_bars, k=deviation_k)
        self._symbol = symbol
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction

        self._atr = AverageTrueRange(period=atr_period)
        self._signal_state: int | None = None
        self._position: OpenPosition | None = None

    @property
    def ofi_window_bars(self) -> int:
        return self._bands.period

    @property
    def open_position(self) -> OpenPosition | None:
        return self._position

    @property
    def bars_seen(self) -> int:
        return self._bands.bars_seen

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        atr = self._atr.update(current)
        bands = self._bands.update(current)

        current_signal = 0
        if bands is not None:
            _, upper, lower = bands
            current_signal = _sign(current.close, lower, upper, _per_bar_ofi(current))

        intent: OrderIntent | None = None

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif bands is not None:
            previous_signal = self._signal_state
            # `atr > 0` guards against a degenerate zero-True-Range
            # warmup result -- same guard `hourly_momentum.py` already
            # uses for the identical reason.
            if (
                previous_signal is not None
                and current_signal != 0
                and current_signal != previous_signal
                and atr is not None
                and atr > 0
            ):
                side = Side.LONG if current_signal > 0 else Side.SHORT
                intent = self._open(current, side, atr)

        if bands is not None:
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
            signal_timeframe="1m",
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


class OfiMomentumTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `OfiMomentumStrategy`.

    **`fit()` performs no search whatsoever** -- the governing
    pre-registration's `total_candidates: 1`. It builds exactly one
    `OfiMomentumStrategy` from this instance's construction-time
    constants, backtests it in-sample against `train_klines` for logging
    purposes only, logs that single evaluation as its own
    `record_type: "backtest_run"` entry (`candidate_index=0,
    total_candidates=1`), and returns a **fresh** strategy instance --
    never the one used for in-sample scoring, which would carry
    leftover internal state (rolling OFI bands, ATR estimator, open
    position). Mirrors `VwapMidReversionTrainable`'s/
    `HourlyMomentumTrainable`'s exact structure.
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
        ofi_window_bars: int = DEFAULT_OFI_WINDOW_BARS,
        deviation_k: Decimal = DEFAULT_DEVIATION_K,
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
        self._ofi_window_bars = ofi_window_bars
        self._deviation_k = deviation_k
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction
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

    def _build_strategy(self, *, symbol: str) -> OfiMomentumStrategy:
        return OfiMomentumStrategy(
            symbol=symbol,
            ofi_window_bars=self._ofi_window_bars,
            deviation_k=self._deviation_k,
            atr_period=self._atr_period,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
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
                "ofi_window_bars": self._ofi_window_bars,
                "deviation_k": str(self._deviation_k),
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
                    "in-sample scoring inside OfiMomentumTrainable.fit() -- the single, fixed, "
                    "zero-fitted-parameter order-flow-imbalance momentum signal, no search -- not "
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
