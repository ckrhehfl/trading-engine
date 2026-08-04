"""Macro-conditioned BTC strategy: S&P 500 (`SP500`) trend, NOT INVERTED --
Strategy Research Task Y, the direct structural sibling of Task X's
`macro_real_yield_trend.py`. See CLAUDE.md's "Strategy Research
Methodology" section and `.planning/sr-y-macro-sp500-strategy.md` for the
full design context: this is the second and (per explicit human decision)
last planned macro-data attempt, testing the second most-supported macro
variable a deep credibility-graded research pass identified (after real
yields, tested by the sibling) -- S&P 500 / risk-asset correlation with
BTC, documented as real, if regime-dependent, 0.4-0.7 correlation since
2020, strengthening during risk-off/stress periods.

## The hypothesis, and why it is NOT INVERTED (the one substantive
## difference from the sibling)

Unlike the sibling's real-yield hypothesis (an INVERSE relationship:
falling real yields -> BTC-bullish), this is a SAME-DIRECTION
co-movement hypothesis: **rising S&P 500 (risk-on) -> BTC-bullish/long;
falling S&P 500 (risk-off) -> BTC-bearish/short.** Getting this sign
backwards -- e.g. by reflexively copying the sibling's inversion logic --
would silently encode the WRONG hypothesis while still "running" without
error, which is exactly why `TestNonInversionAndDirection` in this
module's test suite pins both directions explicitly rather than trusting
the arithmetic by inspection (mirroring
`test_macro_real_yield_trend.py::TestInversionAndDirection`'s own
precedent, with the assertions swapped to match this module's opposite
mapping).

## The signal: the same single, pre-committed lookback, zero search

At each daily BTC bar, look at `SP500`'s own trend over the trailing
`DEFAULT_LOOKBACK_TRADING_DAYS` REAL (non-holiday, non-weekend) FRED
observations -- i.e. `L` steps back in the sequence of `SP500`'s actual
business-day readings, not `L` calendar days
(`compute_sp500_trend_signs` below, structurally identical to the
sibling's `compute_real_yield_trend_signs`). `sign(value[t] - value[t-L])`
is computed once, directly on the raw business-day series (skipping
`None`/holiday rows as non-events rather than treating them as
zero-change steps), and the resulting trend-sign series is then
forward-filled onto every BTC calendar bar via `research.macro_alignment`
(look-ahead-safe by construction -- see that module's docstring; reused
here UNMODIFIED, exactly as the sibling reuses it, since aligning a
*trend* series onto BTC bars is exactly the same "most recent real value
dated at/before this bar" problem regardless of which macro series or
which strategy is asking).

## Lookback: 63 trading days -- the SAME value as the sibling, chosen
## for direct comparability, not re-derived

The governing task brief is explicit: reuse the sibling's exact
63-trading-day lookback "chosen deliberately for direct comparability
between the two macro attempts and to avoid introducing a second 'which
lookback' decision." This is a deliberate departure from this project's
usual practice of deriving each new lookback from first principles (as
the sibling itself did, reasoning about real-yield-specific dynamics) --
here the point is explicitly NOT to re-litigate the lookback choice, but
to hold it fixed so any difference between this run's result and the
sibling's isolates the effect of *which macro variable* (S&P 500 vs. real
yield) and *which sign convention* (same-direction vs. inverse), not a
third, confounding "and also a different lookback" variable. See
`research.strategies.macro_real_yield_trend`'s own module docstring for
the full lookback-choice reasoning (still valid here: 21 trading days
risks short-term noise, 126/252 risk lagging a real turning point, 63 is
the defensible middle ground) -- not re-derived a second time, reused
for the reason stated above.

## Sizing, order emission, zero-trend tie-break: identical to the sibling

Reuses `research.strategies.volatility_targeting`
(`RollingRealizedVolatility`, `compute_vol_scalar`) **unmodified**, same
20%-annualized-vol-target convention, same `base_quantity * vol_scalar`
composition, same deliberate omission of an `abs(signal)` conviction
multiplier (this strategy's signal is a single binary directional call,
never fractional), same edge-triggered "only-on-sign-change" order
emission (Option B, same precedent `daily_tsmom_ensemble.py` established),
same automatic-retry-on-next-bar behavior when a transition can't be
sized, and the same FLAT tie-break on an exact-zero trend reading (a
genuine tie, not evidence in either direction -- matching every sibling
strategy's own zero-sign convention in this codebase). None of this
differs from `macro_real_yield_trend.py`; see that module's own docstring
for the full reasoning behind each choice, not repeated here a second
time.

## Deliberately absent (a clean, standalone test of the macro signal alone)

No ADX regime gate, no ATR stop/target, no risk:reward grid, no funding
signal, and critically **no combination with any BTC-price-derived
momentum signal, and no combination with the sibling's real-yield
signal** -- per the governing task's own instruction, this is a clean,
standalone test of the S&P 500 signal alone, matching this project's
established discipline of testing every signal type in isolation before
ever considering a blend.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from data.fred_client import ObservationRow
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from research.macro_alignment import MacroSeriesCursor
from research.strategies.volatility_targeting import (
    DEFAULT_MAX_VOL_SCALAR,
    DEFAULT_MIN_VOL_SCALAR,
    DEFAULT_TARGET_ANNUALIZED_VOL,
    DEFAULT_VOL_LOOKBACK_PERIOD,
    RollingRealizedVolatility,
    compute_vol_scalar,
)
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol/native-1d conventions matching every sibling strategy
# module in this package (see `daily_tsmom_ensemble.py`,
# `macro_real_yield_trend.py`).
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")

# The full-notional sizing baseline for constant-target-vol sizing (see
# module docstring): base_quantity = reference_equity / entry_price. Same
# magnitude convention as `macro_real_yield_trend.DEFAULT_REFERENCE_EQUITY`.
DEFAULT_REFERENCE_EQUITY = Decimal("10000")

# This strategy's native timeframe is 1d (1 bar/day), same as
# macro_real_yield_trend.py -- every compute_metrics/RollingRealizedVolatility
# call in this module must pass this explicitly, or a reported Sharpe/
# annualized-vol figure would be silently inflated by the 15m/1h
# assumption's much larger sqrt(bars_per_day * 365) factor.
DEFAULT_BARS_PER_DAY = 1

# 63 trading days -- the SAME value as macro_real_yield_trend.py's own
# DEFAULT_LOOKBACK_TRADING_DAYS, reused deliberately for direct
# comparability between the two macro attempts rather than re-derived
# (see module docstring's "Lookback" section).
DEFAULT_LOOKBACK_TRADING_DAYS = 63

# The FRED series this strategy is conditioned on: the S&P 500 index. See
# `.planning/sr-y-macro-sp500-strategy.md` for confirmation this series
# was already fully cached by `sr-w` (`data/backfill_macro.py`'s
# `SERIES_START_DATE["SP500"]`) and needed no new backfill.
SP500_SERIES_ID = "SP500"


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def compute_sp500_trend_signs(
    observations: Sequence[ObservationRow], *, lookback: int
) -> list[ObservationRow]:
    """Given a raw FRED observation series (which may contain `None`
    rows -- FRED's own `"."` marker for a real market holiday, see
    `fred_client.py`'s module docstring), return a NEW `ObservationRow`
    series -- one entry per date that had a REAL (non-`None`) FRED
    observation, starting from the `lookback`'th such date -- whose
    `value` is the SIGN (`Decimal(-1)`/`Decimal(0)`/`Decimal(1)`) of the
    change in the real value over the trailing `lookback` REAL
    observations.

    Structurally identical to
    `research.strategies.macro_real_yield_trend.compute_real_yield_trend_
    signs` (same algorithm, same docstring content) -- duplicated rather
    than imported cross-module, matching this codebase's established
    convention of each strategy module owning its own self-contained
    signal-computation logic (`daily_tsmom_ensemble.py`,
    `ensemble_momentum.py`, and `macro_real_yield_trend.py` itself all
    follow this pattern rather than cross-importing near-identical sign
    logic from one another). `research.macro_alignment` -- genuinely
    shared, cross-series-alignment infrastructure, not a strategy's own
    signal logic -- is the one piece this module DOES reuse unmodified;
    see the module docstring.

    `lookback` counts steps in the sequence of REAL values only -- a
    `None`/holiday row contributes no step at all (it is simply absent
    from the domain this function ever looks at), so this is genuinely
    "L TRADING days," not "L calendar days" and not "L calendar days
    with a holiday counted as a zero-change step." Weekends never appear
    in `observations` at all (FRED returns no row for them), so they are
    automatically excluded the same way.

    This function only computes the trend value AT each of `SP500`'s own
    real observation dates -- it does NOT align the result onto BTC's
    calendar-day bars. That is a deliberately separate, later step
    (`research.macro_alignment.forward_fill_macro_series`/
    `MacroSeriesCursor`).

    `observations` is sorted defensively by `observation_date` first.
    Raises `ValueError` for a non-positive `lookback` -- there is no
    sensible trend to compute over zero or a negative number of steps.
    """
    if lookback <= 0:
        raise ValueError(f"lookback must be positive, got {lookback}")

    real_observations = sorted(
        (row for row in observations if row.value is not None),
        key=lambda row: row.observation_date,
    )

    trend_rows: list[ObservationRow] = []
    for i in range(lookback, len(real_observations)):
        current_row = real_observations[i]
        lagged_row = real_observations[i - lookback]
        trend_sign = _sign(current_row.value - lagged_row.value)
        trend_rows.append(ObservationRow(observation_date=current_row.observation_date, value=Decimal(trend_sign)))
    return trend_rows


class MacroSp500TrendStrategy:
    """Bound, stateful `Strategy`: the NOT-INVERTED `SP500`-trend signal
    (see module docstring), constant-target-volatility sizing,
    edge-triggered on sign-category changes only (Option B -- same
    precedent as `daily_tsmom_ensemble.DailyTsmomEnsembleStrategy` and
    `macro_real_yield_trend.MacroRealYieldTrendStrategy`). No ATR stop/
    target, no regime gate: a position is held until the trend's own sign
    flips or nets to exactly zero.
    """

    __slots__ = (
        "_symbol",
        "_reference_equity",
        "_target_annualized_vol",
        "_min_vol_scalar",
        "_max_vol_scalar",
        "_macro_cursor",
        "_vol",
        "_position_sign",
        "_position_quantity",
    )

    def __init__(
        self,
        *,
        symbol: str,
        trend_observations: Sequence[ObservationRow],
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
    ) -> None:
        if reference_equity <= 0:
            raise ValueError(f"reference_equity must be positive, got {reference_equity}")

        self._symbol = symbol
        self._reference_equity = reference_equity
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._macro_cursor = MacroSeriesCursor(trend_observations)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position_sign = 0
        self._position_quantity = Decimal(0)

    @property
    def position_sign(self) -> int:
        """The currently held position's sign category: `+1` (long),
        `-1` (short), or `0` (flat). Exposed read-only for testing/
        introspection, same convention as every sibling strategy.
        """
        return self._position_sign

    @property
    def position_quantity(self) -> Decimal:
        return self._position_quantity

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]
        realized_vol = self._vol.update(current)
        raw_trend_sign = self._macro_cursor.update(current)

        if raw_trend_sign is None:
            return None  # warmup: no SP500 trend established yet at all

        btc_signal = raw_trend_sign  # NOT INVERTED -- see module docstring
        current_sign = _sign(btc_signal)
        if current_sign == self._position_sign:
            return None  # Option B: silent unless the SIGN category changes

        if current_sign == 0:
            return self._flatten_to(current)

        target_quantity = self._compute_target_quantity(current, realized_vol)
        if target_quantity is None:
            # Cannot size the new leg right now (vol-targeting warmup, or
            # a degenerate price) -- still relinquish a stale position if
            # one is held, and leave position_sign at 0 so this
            # transition is retried automatically once sizing succeeds.
            return self._flatten_to(current)

        return self._transition_to(current, current_sign, target_quantity)

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
        base_quantity = self._reference_equity / entry_price
        target_quantity = base_quantity * vol_scalar
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
            signal_timeframe="1d",
            created_at=current.open_time,
        )

    def _transition_to(self, current: Kline, new_sign: int, target_quantity: Decimal) -> OrderIntent:
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
            signal_timeframe="1d",
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


class MacroSp500TrendTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `MacroSp500TrendStrategy`.

    **`fit()` performs no search whatsoever** -- `total_candidates: 1`,
    matching `macro_real_yield_trend.MacroRealYieldTrendTrainable`'s own
    zero-search discipline. It builds exactly one
    `MacroSp500TrendStrategy` from this instance's construction-time
    constants (`lookback`, fixed at `DEFAULT_LOOKBACK_TRADING_DAYS`),
    backtests it in-sample against `train_klines` for logging purposes
    only, logs that single evaluation as its own `record_type:
    "backtest_run"` entry (`candidate_index=0, total_candidates=1`), and
    returns a **fresh** strategy instance -- never the one used for
    in-sample scoring, which would carry leftover internal state (the
    macro cursor's position, the vol estimator, an open position).

    `macro_observations` -- the RAW `SP500` `ObservationRow` series
    (including `None`/holiday rows) -- is supplied once at construction
    time and transformed into the fixed `lookback`-trading-day
    trend-sign series exactly ONCE, here in `__init__`, not recomputed on
    every `fit()` call: the transformation depends only on this
    instance's own fixed `lookback` hyperparameter, never on
    `train_klines`/`params`, so recomputing it per fold would be wasted
    work computing the identical result. The full macro history (ideally
    predating the earliest BTC bar this Trainable will ever see by at
    least `lookback` real observations) should be supplied -- unlike
    BTC's own price history, `SP500` has no per-fold "not yet available"
    boundary to respect: it is public macro data whose historical values
    were already known as of the date they are dated, regardless of
    which walk-forward fold's BTC window is currently being scored (same
    reasoning as `macro_real_yield_trend.MacroRealYieldTrendTrainable`'s
    own docstring -- not repeated in full here, this differs from BTC-
    price-only warmup for the identical reason).
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        macro_observations: Sequence[ObservationRow],
        starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        lookback: int = DEFAULT_LOOKBACK_TRADING_DAYS,
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
        self._lookback = lookback
        self._reference_equity = reference_equity
        self._vol_period = vol_period
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._runs_path = runs_path
        # Computed exactly once -- see class docstring.
        self._trend_observations = compute_sp500_trend_signs(macro_observations, lookback=lookback)

    @property
    def lookback(self) -> int:
        return self._lookback

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

    def _build_strategy(self, *, symbol: str) -> MacroSp500TrendStrategy:
        return MacroSp500TrendStrategy(
            symbol=symbol,
            trend_observations=self._trend_observations,
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
                "lookback": self._lookback,
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
                    "in-sample scoring inside MacroSp500TrendTrainable.fit() -- the single, fixed, "
                    "zero-fitted-parameter SP500-trend strategy, no search -- not itself a walk-forward run"
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
