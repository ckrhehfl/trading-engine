"""Daily time-series-momentum ensemble strategy -- Strategy Research Task U
(the pre-registered attempt against the virgin `1d` early-window holdout).
See CLAUDE.md's "Strategy Research Methodology" section (Gate 2) and
`.planning/sr-u-preregistered-attempt-spec.md` for the full design context,
the pre-registration this module implements
(`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`),
and every judgment call's full reasoning; this docstring is the
implementation-level summary. **This module was written, and this
docstring composed, without ever loading, querying or plotting one bar of
BTC-USDT `1d` kline data** -- see the planning doc's explicit confirmation
of that.

## The signal: zero-fitted-parameter time-series momentum, MOP 2012

At each daily bar `t`, for each lookback `L` in **{21, 63, 126, 252}**
trading days -- the Moskowitz-Ooi-Pedersen (2012) "Time Series Momentum,"
*Journal of Financial Economics* 104(2), canonical 1/3/6/12-month set,
chosen by the literature rather than by this project -- compute
`sign(close[t] - close[t-L])`. Position direction/strength is the
**average of the four signs**: a `Decimal` with 9 possible values, `-1`
to `+1` in steps of `0.25`. (The governing pre-registration's own prose
describes the achievable range loosely as "{-1, -0.5, 0, +0.5, +1} up to
ties/zeros" -- that enumeration is not exhaustive of what averaging 4
values each in {-1, 0, +1} actually produces; this module implements the
literal specified formula, average of the four signs, which yields all 9
multiples of 0.25 in [-1, 1], not only the 5 named. Implemented literally
rather than silently restricted to the smaller named set -- see the
planning doc's "Discrepancies noted, not silently resolved" section.)

## All four lookbacks warm up together, or none produces a signal

Same precedent as `ensemble_momentum.py`'s `EnsembleMomentumStrategy`
("All 3 pairs must be simultaneously warmed up... before any combined
signal is produced at all"): this strategy requires `max(lookbacks) + 1`
(253, for the default lookback set) closes before computing anything,
rather than combining whichever subset of lookbacks happens to already be
warm. This avoids the ensemble's effective character silently changing
over the warmup window -- simpler to reason about and test, at the cost
of a longer warmup than the shortest lookback alone would need.

## Sizing: constant-target volatility, no ATR stop, no fixed-fractional sizing

Deliberately different composition from every sibling strategy in this
package. Every other strategy in `research/strategies/` sizes a position
via `risk_management.compute_position_size` (fixed-fractional risk against
an ATR-scaled stop distance) and then applies a *separate*
volatility-targeting or regime-weighting *scalar* on top of that ATR-sized
base. This strategy has **no ATR stop, no ATR target, no risk:reward
grid** (deliberately absent -- the governing pre-registration is explicit
that every one of these came from this project's own 117-trial 1h-window
search and excluding them is what makes `free_parameter_count: 0` true),
so there is no ATR-sized base quantity to scale in the first place.
Instead, `research.strategies.volatility_targeting`
(`RollingRealizedVolatility`, `compute_vol_scalar`, both reused
**unmodified**, per the governing spec's own instruction) drives sizing
directly against a full-notional reference baseline:

    base_quantity   = reference_equity / entry_price
    target_quantity = base_quantity * abs(ensemble_value) * vol_scalar

`vol_scalar = compute_vol_scalar(realized_vol, target_annualized_vol=0.20,
...)` -- the same 20%-annualized constant-volatility-target convention
this codebase already documents as its convention
(`volatility_targeting.DEFAULT_TARGET_ANNUALIZED_VOL`), not re-chosen for
this attempt. `abs(ensemble_value)` scales size by the ensemble's own
conviction (`0.25` weak agreement through `1.0` full agreement across all
four lookbacks) -- the "strength" half of "Position direction/strength"
the governing spec names explicitly.

## Order emission: only-on-sign-change (a design decision, not left implicit)

See `.planning/sr-u-preregistered-attempt-spec.md`'s "Order-emission
design decision" section for the full reasoning; summarized here.

The governing spec explicitly left open how a change in the ensemble's
*fractional strength* (e.g. `+0.5 -> +1.0`, same sign, different
magnitude) maps to order emission: rebalance to the new target every time
the fractional value changes at all, or fire only when the *sign
category* (`-1`/`0`/`+1`) changes. **This module chooses the latter**: a
position, once opened, holds the quantity computed at the moment it was
opened until the ensemble's sign category changes again (to the opposite
sign, or to flat) -- it is never resized purely because the same-sign
magnitude moved.

This is the codebase's own established convention, not a new one invented
for this task: every sibling ensemble/crossover strategy in this package
(`ensemble_momentum.py`'s net-sign `_combined_sign`, `hourly_momentum.py`'s
`_crossover_sign`, `funding_extremity.py`'s `_signal_state`) fires only on
a **sign** transition and is silent on same-sign magnitude changes --
`ensemble_momentum.py`'s own docstring names this exact tradeoff
explicitly for its net-sign combination ("discarding *how strongly* each
pair agrees... a real, accepted simplification, not an oversight").
Combined with this strategy's complete absence of an ATR stop/target (see
above), "hold until the signal reverses" is also the standard pure-trend-
following exit convention the cited literature itself uses (no separate
stop-loss; the position is held until the signal itself says otherwise).

**Disclosed cost, not engineered around**: this choice trades away
same-sign resizing, which means (a) a held position's realized size can
lag the ensemble's *current* true conviction once a sign is established,
and (b) trade count is bounded by how often the ensemble's **sign** (not
magnitude) actually flips over the registered window -- materially fewer
events than "rebalance on any magnitude change" would produce. The
registered `min_total_trades` floor for the 1,079-bar `1d` holdout window
(53, via `research.preregistration.frequency_scaled_min_trades`) is a
real, disclosed risk under this choice, declared in the pre-registration
rather than resolved in either direction here.

## A single OrderIntent can flatten and reopen in the same bar (a flip)

`Strategy` (`backtest.engine`'s `Callable[[Sequence[Kline]], OrderIntent |
None]`) returns at most one intent per bar. A direct sign reversal (e.g.
`+1` straight to `-1`, no bar spent at exactly `0` in between) is
therefore expressed as **one** `OrderIntent` whose quantity is the sum of
the closing quantity (the position being relinquished) and the new target
quantity (the position being opened) -- `metrics.position.
PositionTracker._reduce_or_close_or_flip` already interprets an order
whose magnitude exceeds the currently open position as exactly this:
close the existing lifecycle, then open a fresh one for the residual in
the new direction, both at this same fill. This is existing,
already-tested machinery, reused rather than reinvented, and it is what
makes a same-bar flip possible without widening the
one-`OrderIntent`-per-bar `Strategy` contract.

If the new target cannot be sized (volatility-targeting warmup, or a
degenerate `entry_price <= 0`) at the moment a reversal is due, this
module still flattens the stale position -- the signal that justified
holding it has already gone away, so continuing to hold it on the hope
that sizing becomes available later is the wrong default -- and leaves
the tracked position sign at `0` (flat), so the very next bar retries the
transition automatically as soon as sizing succeeds. This mirrors the
`entry_rejected_by_filters` retry pattern `mean_reversion.py`/
`funding_extremity.py` already establish for a comparable
filter-rejection case.

## Deliberately absent, and why (mirrors the pre-registration's own list)

No ADX regime gate, no ATR stop/target, no risk:reward grid, no funding
signal driving direction or size. Every one of these came from this
project's own 117-trial 1h-window search (`sr-r`'s statistical
close-out); excluding every one of them is what makes this attempt's
registered `free_parameter_count: 0` literally true, not just
approximately true. Funding P&L *accounting* (never signal) was
considered and deliberately **not** wired in for this attempt -- see the
planning doc's "Funding accounting" section for the full reasoning (no
`load_holdout_funding` loader exists yet in `research/holdout.py`, and
building/testing one against the reserved window would itself be a form
of touching that window ahead of the registered run). `funding_rates`
remains an accepted, purely additive, optional constructor parameter on
`DailyTsmomEnsembleTrainable` for symmetry with every sibling
`Trainable`'s `compute_metrics` call -- unused (`None`) by the registered
configuration.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from metrics.funding import FundingRate
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

# Single-symbol/native-1d conventions matching every sibling strategy
# module in this package.
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")

# The full-notional sizing baseline for constant-target-vol sizing (see
# module docstring): base_quantity = reference_equity / entry_price. Same
# magnitude convention as `risk_management.DEFAULT_REFERENCE_EQUITY`,
# defined independently here since this module does not import
# `risk_management` at all (no ATR stop/target -- see module docstring).
DEFAULT_REFERENCE_EQUITY = Decimal("10000")

# This strategy's native timeframe is 1d (1 bar/day) -- distinct from
# every other strategy module in this package (15m/1h). Every
# `compute_metrics`/`RollingRealizedVolatility` call in this module must
# pass this explicitly, or a reported Sharpe/annualized-vol figure would
# be silently inflated by the 15m/1h assumption's much larger
# `sqrt(bars_per_day * 365)` factor. See `.planning/sr-t-daily-data-
# path.md`'s "bars_per_day" consumer-audit note.
REBALANCE_DEADBAND = Decimal("0.10")
"""Minimum relative change in target quantity before `rebalance_on_
conviction` emits an order. 10%.

**Only reachable when that flag is explicitly enabled**, which it is not
by default and which was measured as harmful -- so this constant is on no
path any evaluated configuration takes, and changing it re-tunes nothing
already on record.

It exists because the alternative is not "no parameter" but "a deadband
of zero", which is the one setting guaranteed to be wrong: `close` and
`vol_scalar` drift every bar, so an exact comparison rebalances almost
always and the result measures fees rather than conviction.
"""

DEFAULT_BARS_PER_DAY = 1

# Moskowitz-Ooi-Pedersen (2012) canonical 1/3/6/12-trading-month lookback
# set, in trading days -- literature-specified, never fitted on this
# project's own data (this is what makes the governing pre-registration's
# `free_parameter_count: 0` true). See module docstring.
DEFAULT_LOOKBACKS: tuple[int, ...] = (21, 63, 126, 252)


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _average_of_lookback_signs(closes: Sequence[Decimal], lookbacks: Sequence[int]) -> Decimal:
    """Pure function: the average of `sign(closes[-1] - closes[-(L+1)])` for
    each `L` in `lookbacks` -- the module docstring's "average of the four
    signs" computed directly, with no warmup handling of its own (`closes`
    must already contain at least `max(lookbacks) + 1` elements; callers
    gate that separately -- `DailyTsmomEnsembleStrategy._ensemble_value`
    below). Factored out as a standalone, directly testable function --
    same "the actual computation is a plain function, the class just drives
    it bar by bar" shape as `ensemble_momentum.py`'s `_combined_sign` --
    so signal correctness at each lookback can be verified against a
    hand-built `closes` list without first driving a full strategy
    instance through a multi-hundred-bar warmup sequence.
    """
    current_close = closes[-1]
    signs = [_sign(current_close - closes[-(lookback + 1)]) for lookback in lookbacks]
    return Decimal(sum(signs)) / Decimal(len(signs))


class DailyTsmomEnsembleStrategy:
    """Bound, stateful `Strategy`: the average-of-4-lookback-signs TSMOM
    ensemble signal (see module docstring), constant-target-volatility
    sizing, edge-triggered on **sign-category** changes only (Option B --
    see module docstring's "Order emission" section). No ATR stop/target,
    no regime gate: a position is held until the ensemble's sign flips or
    nets to exactly zero.
    """

    __slots__ = (
        "_lookbacks",
        "_warmup_bars",
        "_symbol",
        "_reference_equity",
        "_target_annualized_vol",
        "_min_vol_scalar",
        "_max_vol_scalar",
        "_closes",
        "_vol",
        "_position_sign",
        "_position_quantity",
        "_rebalance_on_conviction",
    )

    def __init__(
        self,
        *,
        symbol: str,
        lookbacks: Sequence[int] = DEFAULT_LOOKBACKS,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
        rebalance_on_conviction: bool = False,
    ) -> None:
        self._rebalance_on_conviction = rebalance_on_conviction
        lookbacks = tuple(lookbacks)
        if len(lookbacks) < 2:
            raise ValueError(
                f"lookbacks must contain at least 2 lookback windows for a meaningful ensemble, "
                f"got {len(lookbacks)}"
            )
        for lookback in lookbacks:
            if lookback <= 0:
                raise ValueError(f"every lookback must be positive, got {lookback}")
        if reference_equity <= 0:
            raise ValueError(f"reference_equity must be positive, got {reference_equity}")

        self._lookbacks = lookbacks
        self._warmup_bars = max(lookbacks) + 1
        self._symbol = symbol
        self._reference_equity = reference_equity
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar

        self._closes: deque[Decimal] = deque(maxlen=self._warmup_bars)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position_sign = 0
        self._position_quantity = Decimal(0)

    @property
    def lookbacks(self) -> tuple[int, ...]:
        return self._lookbacks

    @property
    def warmup_bars(self) -> int:
        return self._warmup_bars

    @property
    def position_sign(self) -> int:
        """The currently held position's sign category: `+1` (long),
        `-1` (short), or `0` (flat). Exposed read-only for testing/
        introspection, same convention as `open_position` on sibling
        strategies.
        """
        return self._position_sign

    @property
    def position_quantity(self) -> Decimal:
        """The currently held position's quantity (`Decimal(0)` while
        flat). The size fixed at the moment the current sign was
        established -- see module docstring's "Option B" section for why
        this does not track same-sign magnitude changes.
        """
        return self._position_quantity

    @property
    def bars_seen(self) -> int:
        return len(self._closes)

    def _ensemble_value(self) -> Decimal | None:
        """`None` before all `lookbacks` are simultaneously warm (see
        module docstring); otherwise `_average_of_lookback_signs` over the
        trailing `warmup_bars` closes.
        """
        if len(self._closes) < self._warmup_bars:
            return None
        return _average_of_lookback_signs(list(self._closes), self._lookbacks)

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]
        realized_vol = self._vol.update(current)
        self._closes.append(current.close)

        ensemble_value = self._ensemble_value()
        if ensemble_value is None:
            return None  # warmup: no signal at all, no state change

        current_sign = _sign(ensemble_value)
        if current_sign == self._position_sign:
            # Option B (the default, and this module's original behaviour):
            # silent unless the SIGN category changes. A position opened at
            # ensemble +1.0 is held unchanged even as conviction decays to
            # +0.25, because only a sign flip emits.
            if not self._rebalance_on_conviction:
                return None
            # Conviction rebalancing (Scalping-arc follow-up): the same-sign
            # magnitude IS the strategy's own confidence, already computed
            # every bar, and holding a full position through its decay is a
            # cost this module's docstring disclosed rather than defended.
            # Resizing to the current target uses no new parameter -- it
            # reuses `_compute_target_quantity`, which every entry already
            # calls.
            if current_sign == 0:
                return None  # a flat target with a flat position is a no-op
            target = self._compute_target_quantity(current, ensemble_value, realized_vol)
            if target is None or self._position_quantity == 0:
                return None
            # A deadband, because `target != position` is not the neutral
            # choice it looks like -- it is a deadband of zero, and it is
            # the pathological setting. `target` is
            # `equity / close x |ensemble| x vol_scalar`, and `close` and
            # `vol_scalar` both move a little every bar, so an exact
            # comparison fires on almost every bar even when the position
            # is materially unchanged. Each of those emits a real order
            # paying real fees and slippage, so turnover explodes and the
            # measurement becomes a study of costs.
            #
            # Declared as a named constant rather than inlined so it is
            # visible as the rebalance decision's one parameter, and
            # cannot become a quietly-tuned free variable later.
            drift = abs(target - self._position_quantity) / abs(self._position_quantity)
            if drift < REBALANCE_DEADBAND:
                return None
            return self._resize_to(current, current_sign, target)

        if current_sign == 0:
            return self._flatten_to(current)

        target_quantity = self._compute_target_quantity(current, ensemble_value, realized_vol)
        if target_quantity is None:
            # Cannot size the new leg right now (vol-targeting warmup, or a
            # degenerate price) -- still relinquish a stale position if one
            # is held (see module docstring), and leave position_sign at 0
            # so this transition is retried automatically once sizing
            # succeeds.
            return self._flatten_to(current)

        return self._transition_to(current, current_sign, target_quantity)

    def _compute_target_quantity(
        self, current: Kline, ensemble_value: Decimal, realized_vol: Decimal | None
    ) -> Decimal | None:
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
        target_quantity = base_quantity * abs(ensemble_value) * vol_scalar
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

    def _resize_to(self, current: Kline, sign: int, target_quantity: Decimal) -> OrderIntent:
        """Adjust an existing same-sign position to `target_quantity`.

        Distinct from `_transition_to`, which crosses a sign boundary and
        must close the old leg *and* open the new one in a single intent.
        Here the sign is unchanged, so the order is the pure delta: buying
        more of the same side when conviction strengthens, selling part of
        it back when conviction decays.

        `abs(...)` on the delta with a side flip is what makes a REDUCTION
        expressible -- shrinking a long is a SHORT order for the
        difference, which `metrics.position.PositionTracker` interprets as
        a partial close rather than a new opposite position, because the
        quantity is strictly smaller than the position it is reducing.
        """
        delta = target_quantity - self._position_quantity
        increasing = delta > 0
        side = (
            (Side.LONG if sign > 0 else Side.SHORT)
            if increasing
            else (Side.SHORT if sign > 0 else Side.LONG)
        )
        self._position_quantity = target_quantity
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=abs(delta),
            limit_price=None,
            signal_timeframe="1d",
            created_at=current.open_time,
        )

    def _transition_to(self, current: Kline, new_sign: int, target_quantity: Decimal) -> OrderIntent:
        # Delta quantity: the residual to close out of the OLD sign's
        # position (if any) plus the full new target -- see module
        # docstring's "same-bar flip" section for why one intent suffices
        # (`metrics.position.PositionTracker` already interprets an
        # over-sized order as close-then-open-residual).
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


class DailyTsmomEnsembleTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `DailyTsmomEnsembleStrategy`.

    **`fit()` performs no search whatsoever** -- the governing
    pre-registration's `total_candidates: 1`. It builds exactly one
    `DailyTsmomEnsembleStrategy` from this instance's construction-time
    constants (`lookbacks`, fixed at `DEFAULT_LOOKBACKS`, the literature-
    specified MOP set), backtests it in-sample against `train_klines` for
    logging purposes only, logs that single evaluation as its own
    `record_type: "backtest_run"` entry (`candidate_index=0,
    total_candidates=1`, matching every sibling `Trainable`'s "every
    fit() call leaves a trace" convention and `research.overfitting_check`'s
    "1 combination tried" accounting -- an honest count, since nothing was
    actually searched), and returns a **fresh** strategy instance -- never
    the one used for in-sample scoring, which would carry leftover
    internal state (rolling closes, vol estimator, open position).
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
        lookbacks: Sequence[int] = DEFAULT_LOOKBACKS,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
        # Additive, opt-in only -- see module docstring's "Deliberately
        # absent" section. The registered configuration never supplies
        # this (funding_included: false); kept here purely for symmetry
        # with every sibling Trainable's compute_metrics call, and so a
        # future, separately-registered attempt can opt in without a
        # constructor-signature change.
        funding_rates: Sequence[FundingRate] | None = None,
        runs_path: str = experiment_log.DEFAULT_RUNS_PATH,
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._starting_equity = starting_equity
        self._bars_per_day = bars_per_day
        self._lookbacks = tuple(lookbacks)
        self._reference_equity = reference_equity
        self._vol_period = vol_period
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._funding_rates = funding_rates
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
            funding_rates=self._funding_rates,
        )
        self._log_candidate(symbol=symbol, train_klines=train_klines, metrics=metrics, parent_run_id=parent_run_id)

        return self._build_strategy(symbol=symbol)

    def _build_strategy(self, *, symbol: str) -> DailyTsmomEnsembleStrategy:
        return DailyTsmomEnsembleStrategy(
            symbol=symbol,
            lookbacks=self._lookbacks,
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
                "lookbacks": list(self._lookbacks),
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
                    "in-sample scoring inside DailyTsmomEnsembleTrainable.fit() -- the single, "
                    "fixed, zero-fitted-parameter MOP-2012 ensemble, no search -- not itself a "
                    "walk-forward run"
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
