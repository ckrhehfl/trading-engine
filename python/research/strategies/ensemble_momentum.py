"""Multi-lookback ensemble momentum strategy (the "treatment" arm of
Strategy Research Task H's ensemble-vs-single-lookback comparison) --
native-1h, combining at least 3 SMA-crossover pairs at different lookback
scales into one ensemble directional signal, plus the identical ADX-based
continuous regime weighting and real volatility targeting layers
`single_lookback_momentum.py` (the baseline/control) uses. See CLAUDE.md's
"Strategy Research Methodology" section and `.planning/sr-h-ensemble-
regime-voltargeting.md` for the full research context (the genuine,
unresolved-until-tested tension between Concretum/Man AHL/QuantPedia's
ensembling-helps findings and CFM/Valeyre's single-120-day-EMA-wins
finding), design reasoning, and honest real walk-forward results (this
vs. `single_lookback_momentum.py`).

**Same risk-management, regime-weighting, and vol-targeting layers as the
baseline, applied identically** (`research.strategies.risk_management`,
`research.strategies.regime_weighting`, `research.strategies.
volatility_targeting`, all imported unchanged) -- the *only* deliberate
difference between this strategy and `single_lookback_momentum.
SingleLookbackMomentumStrategy` is where the entry/direction signal comes
from. This is what makes the walk-forward comparison between the two
meaningful: every other variable is held constant.

## Combining 3 lookback scales into one signal

`DEFAULT_LOOKBACK_PAIRS = ((4, 12), (12, 36), (24, 72))` -- short (4h/12h),
medium (12h/36h, ~0.5day/1.5day), long (24h/72h, ~1day/3day) fast/slow SMA
pairs, in native 1h-bar units. Chosen to span sub-day to multi-day scales
-- roughly matching QuantPedia's BTC-specific finding (cited in this
task's research pass) that Bitcoin's regime shifts operate on a
day-scale, not the month-scale a traditional-CTA-asset lookback like
Valeyre's 120-day EMA would target -- **not searched/tuned to this
asset**. Two of these three pairs are deliberately shared with
`single_lookback_momentum.DEFAULT_CANDIDATE_GRID` (its `(12, 36)` and
`(24, 72)` entries), so the baseline's own grid search has a genuine
chance to land on a lookback comparable to what the ensemble offers.

**Fixed, not grid-searched** -- a deliberate `fit()` design choice (see
`EnsembleMomentumTrainable`'s docstring): searching over combinations of
3 lookback pairs would combinatorially blow up the tunable-parameter
count, directly working against CLAUDE.md's "few tunable knobs" guidance
and, more concretely, against `research.overfitting_check`'s MinBTL-style
combinations-tried-per-year-of-data heuristic (Task G) -- exactly the
overfitting risk that heuristic exists to flag.

Each pair's own fast/slow SMA crossover produces a sign (`+1`/`0`/`-1`,
same `_sign` helper as every other strategy in this package). The 3
pairs' signs are combined by a **net-sign rule** (`sign(sum(pair_signs))`):
sum the 3 signs; a strictly positive sum nets to `+1`, strictly negative
nets to `-1`, and an exact-zero sum nets to `0` (no signal). This
implements majority vote **exactly** whenever every pair is non-flat (2
bullish + 1 bearish sums to `+1`, i.e. `+1` wins by strict majority, and
symmetrically for bearish) -- but is a stated simplification of "majority
vote" in the general case: a *single* non-flat pair against two flat
ones (e.g. `[+1, 0, 0]`) also nets to `+1`, even though only 1 of 3 pairs
actually voted a direction, not a genuine 2-of-3 majority. This is a
deliberate, accepted simplification (not a bug -- see
`test_ensemble_momentum.py::TestCombinedSign::
test_one_bullish_two_flat_nets_bullish` for the locked-in behavior),
chosen because a plain summed-sign rule is simpler to reason about and
implement than special-casing "how many pairs actually voted," and
because 3 simultaneously-flat-or-near-flat SMA pairs on real BTC data is
an edge case, not the common path this rule is optimized for.
Deliberately simple, not weighted/optimized: this is the combination
rule this task's own brief explicitly left as an implementation
judgment call ("by averaging each pair's directional state, or majority
vote — your call, document reasoning"); this net-sign/majority-vote rule
was chosen over a weighted average because it requires no additional
tunable weight parameters (keeping the "few tunable knobs" discipline
intact) and is directly interpretable in the common (all-pairs-non-flat)
case, at the cost of discarding *how strongly* each pair agrees (e.g. a
barely-positive fast SMA counts the same as a strongly-positive one) --
a real, accepted simplification, not an oversight.

**All 3 pairs must be simultaneously warmed up (`len(closes) >=
max(slow for _, slow in lookback_pairs)`) before any combined signal is
produced at all** -- rather than combining whichever subset of pairs
happens to be warm at a given bar. This avoids the ensemble's effective
character silently changing over the warmup period (e.g. behaving like a
2-pair, then 3-pair ensemble as longer-lookback pairs come online) --
simpler to reason about and test, at the cost of a longer warmup than
the strictest possible (bounded by the *longest* configured `slow`, 72
bars here, rather than the shortest).

The combined sign is edge-triggered (fires only on the bar the combined
sign changes, same "last established regime" tracking pattern as every
other strategy in this package) -- structurally identical to
`hourly_momentum.HourlyMomentumStrategy`'s crossover-sign tracking, just
fed by the 3-pair combination instead of a single pair's sign.

## Strategy Research Task I: optional risk:reward grid search

See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-i-ensemble-refinement.md` for the full diagnosis this
addition responds to. Summary: Task H's real 19-fold walk-forward run's
actual target-hit rate (35.3% across all 262 real trades, excluding
fold-end forced closes) sits right at the ~33.3% breakeven the fixed 1:2
stop:target ratio (`DEFAULT_STOP_MULTIPLIER=1.5` /
`DEFAULT_TARGET_MULTIPLIER=3.0`) implies, and gross (pre-fee) trade P&L
summed across every real trade was close to zero (-$66) versus $2,146 in
real fees paid -- trading costs, not a clearly negative raw signal, are
what turned a near-breakeven raw edge negative. That thin, cost-dominated
margin is the concrete, diagnosis-driven justification for actually
searching a small risk:reward grid here -- a deliberate, narrow exception
to this module's own "fixed, not grid-searched" design above (which still
governs every other constant: lookback pairs, ATR/ADX/vol periods, ADX
thresholds, vol-target level/bounds, risk fraction).

**Purely additive and opt-in, not a redesign of the default path**:
`EnsembleMomentumTrainable.fit()` still behaves byte-for-byte as sr-h
built it (exactly one candidate, `target_multiplier` fixed at construction
time, `total_candidates=1`) whenever `params` omits the `"candidates"`
key entirely -- every existing caller, and every one of sr-h's own tests,
is unaffected. Only when a caller explicitly supplies
`params["candidates"]` (a non-empty sequence of `(risk_reward_tenths,)`
1-tuples) does `fit()` grid-search `target_multiplier` across them, per
fold, on that fold's own `train_klines` only -- the same train-only,
walk-forward-safe selection `SingleLookbackMomentumTrainable.fit()`
already uses for its `(fast, slow)` grid, applied to this one new
dimension instead. `stop_multiplier` is never part of the search --
only the target side of the ratio varies, holding the ATR-based stop
distance (and therefore position sizing, which depends only on stop
distance -- see `risk_management.compute_position_size`) fixed across
every candidate, so the comparison isolates the risk:reward ratio itself.

**`risk_reward_tenths` encoding**: an integer, `10x` the target:stop
ratio (e.g. `20` means `target_multiplier = stop_multiplier * 2.0`, the
original ratio; `30` means `target_multiplier = stop_multiplier * 3.0`),
rather than a `Decimal` `target_multiplier` directly. This is a deliberate
compatibility choice: `research.robustness.check_parameter_sensitivity`/
`perturb_candidate` (Task G's overfitting safeguard) are written for
integer, window-length-shaped candidates (round-half-up perturbation,
clamped to `>=1`) and are reused here **unmodified** -- exactly what this
task's own brief asked for ("using Task G's parameter-sensitivity check on
whatever wins"). Building a parallel Decimal-aware sensitivity checker
just for this one new dimension was considered and rejected as needless
duplication of already-tested, working machinery.
`DEFAULT_RISK_REWARD_TENTHS_CANDIDATES = ((15,), (20,), (25,), (30,))` --
4 candidates spanning 1:1.5 to 1:3, deliberately including `20` (today's
original, unsearched 1:2 ratio) as one of the options, not just wider
ones, so the search can honestly conclude "no change" if that is what the
data shows, rather than being structurally biased toward finding
something different from the status quo.
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

# Same single-symbol/starting-equity/bars-per-day conventions and
# reasoning as single_lookback_momentum.py's identical constants -- kept
# equal between the two strategies deliberately, so the comparison
# between them varies only in signal-generation, nothing else.
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")
DEFAULT_BARS_PER_DAY = 24

# Short/medium/long 1h (fast, slow) SMA pairs -- see module docstring for
# the full reasoning behind these specific scales.
DEFAULT_LOOKBACK_PAIRS: tuple[tuple[int, int], ...] = ((4, 12), (12, 36), (24, 72))

# Strategy Research Task I's optional risk:reward grid -- see module
# docstring's "Strategy Research Task I: optional risk:reward grid search"
# section for the diagnosis that justifies this, the risk_reward_tenths
# encoding, and why 20 (the original ratio) is deliberately included.
DEFAULT_RISK_REWARD_TENTHS_CANDIDATES: tuple[tuple[int, ...], ...] = ((15,), (20,), (25,), (30,))

# Strategy Research Task I: BTC-empirical-distribution-recalibrated ADX
# thresholds -- NOT this module's constructor default (regime_weighting.
# DEFAULT_ADX_LOW_THRESHOLD/DEFAULT_ADX_HIGH_THRESHOLD, 20/25, the
# traditional-TA convention, remains the default for both this strategy
# and single_lookback_momentum.py, unchanged -- every existing test and
# sr-h's own historical result stays reproducible against unmodified
# defaults). See .planning/sr-i-ensemble-refinement.md's Step 1
# diagnosis: on real BTC 1h data, this module's own (deliberately
# non-Wilder, plain-rolling-mean) ADX computation spends ~74% of bars at
# or above 25 (already full conviction weight) and only ~11.7% actually
# inside the 20-25 ramp -- the traditional convention barely functions as
# a *continuous* discriminator for this asset on this indicator. These
# two constants (computed leakage-free from fold 0's own train window --
# the earliest 2,160-bar slice of the research dataset, entirely before
# every walk-forward fold's validate window -- empirical p25~=25.72,
# p75~=50.20, rounded to 25/50) widen the ramp to span BTC's own
# empirical interquartile range instead. Tested for real via a full
# 19-fold walk-forward re-run (see the planning doc for the full
# before/after comparison) -- a genuine, evidenced improvement, not a
# blind convention swap. A caller opts in explicitly, e.g.
# `EnsembleMomentumTrainable(..., adx_low=RECALIBRATED_ADX_LOW_THRESHOLD,
# adx_high=RECALIBRATED_ADX_HIGH_THRESHOLD)`.
RECALIBRATED_ADX_LOW_THRESHOLD = Decimal("25")
RECALIBRATED_ADX_HIGH_THRESHOLD = Decimal("50")


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _combined_sign(pair_signs: Sequence[int]) -> int:
    """Net-sign combination of the constituent pairs' signs -- see
    module docstring for the full reasoning, including the precise
    relationship to "majority vote" (exact whenever no pair is flat; a
    documented simplification otherwise). `sum(pair_signs) > 0` nets to
    `+1` (bullish); `< 0` nets to `-1` (bearish); exactly `0` (e.g. a
    tie, or "all three disagree", or every pair flat) nets to `0` (no
    signal).
    """
    total = sum(pair_signs)
    if total > 0:
        return 1
    if total < 0:
        return -1
    return 0


class EnsembleMomentumStrategy:
    """Bound, stateful `Strategy`: majority-vote combination of 3
    fast/slow SMA-crossover pairs at different lookback scales, edge-
    triggered, plus ATR-based stop/target exits, ADX-based continuous
    regime weighting, and real volatility targeting -- identical
    risk-management/regime/vol-targeting mechanics to
    `single_lookback_momentum.SingleLookbackMomentumStrategy`; see this
    module's docstring for the combination logic that's the actual
    difference between the two.
    """

    __slots__ = (
        "_lookback_pairs",
        "_max_slow",
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
        symbol: str,
        lookback_pairs: Sequence[tuple[int, int]] = DEFAULT_LOOKBACK_PAIRS,
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

        self._closes: deque[Decimal] = deque(maxlen=self._max_slow)
        self._crossover_sign: int | None = None
        self._atr = AverageTrueRange(period=atr_period)
        self._adx = AverageDirectionalIndex(period=adx_period)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position: OpenPosition | None = None

    @property
    def lookback_pairs(self) -> tuple[tuple[int, int], ...]:
        return self._lookback_pairs

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

        self._closes.append(current.close)
        current_sign = 0
        have_all_pairs = len(self._closes) >= self._max_slow
        if have_all_pairs:
            closes = list(self._closes)
            pair_signs = []
            for fast, slow in self._lookback_pairs:
                slow_sma = sum(closes[-slow:]) / slow
                fast_sma = sum(closes[-fast:]) / fast
                pair_signs.append(_sign(fast_sma - slow_sma))
            current_sign = _combined_sign(pair_signs)

        intent: OrderIntent | None = None

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif have_all_pairs:
            previous_sign = self._crossover_sign
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

        regime_weight = compute_regime_weight(adx, low=self._adx_low, high=self._adx_high)
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None

        # No separate hard cap on final_quantity is needed beyond
        # compute_vol_scalar's own max_scalar clamp -- same bound and
        # reasoning as single_lookback_momentum.py's identical composition
        # (regime_weight in [0, 1] by construction, so the worst case is
        # deterministically base_quantity * max_vol_scalar).
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


class EnsembleMomentumTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `EnsembleMomentumStrategy`.

    **`fit()` grid-searches nothing by default.** The 3 lookback pairs are
    structurally fixed (`lookback_pairs`, default `DEFAULT_LOOKBACK_PAIRS`,
    set at `EnsembleMomentumTrainable` construction time) and every risk-
    management/regime/vol-targeting constant is likewise fixed, identical
    to `single_lookback_momentum.SingleLookbackMomentumTrainable`'s
    non-searched constants -- see module docstring for why searching the
    lookback-scale combination was deliberately not attempted (tunable-
    parameter-count discipline, MinBTL-style overfitting risk).

    When `params` omits `"candidates"` entirely (every caller before
    Strategy Research Task I, and every caller since that doesn't
    deliberately opt in): `fit(train_klines, params, *, parent_run_id)`
    builds exactly one `EnsembleMomentumStrategy` (from `params["symbol"]`,
    default `DEFAULT_SYMBOL`, plus this instance's fixed construction-time
    constants), backtests it against `train_klines` only, and logs that
    single evaluation as its own `record_type: "backtest_run"` entry
    (`candidate_index=0`, `total_candidates=1`) -- for symmetry with
    every other strategy's "every fit() call leaves a trace" logging
    convention, and so `research.overfitting_check.check_combination_count`
    correctly counts this as "1 combination tried" per fold (an honest
    count: nothing was actually searched, so 1 is the correct number, not
    an undercount).

    When `params["candidates"]` **is** given (a non-empty sequence of
    `(risk_reward_tenths,)` 1-tuples, e.g.
    `DEFAULT_RISK_REWARD_TENTHS_CANDIDATES`) -- Strategy Research Task I's
    opt-in addition, see module docstring's "optional risk:reward grid
    search" section for the diagnosis that motivates this: `fit()`
    grid-searches `target_multiplier` across those candidates on
    `train_klines` only, using the same per-candidate scoring (in-sample
    `total_return`, zero-trade candidates skipped, first-strict-improvement
    tie-break, fallback to the first candidate if none traded) and logging
    convention (`candidate_index`/`total_candidates` set to the real grid
    size) `SingleLookbackMomentumTrainable.fit()` already uses for its own
    grid. `stop_multiplier` is never varied -- only `target_multiplier`
    (derived as `stop_multiplier * risk_reward_tenths / 10`) changes
    per candidate.

    Either way, `fit()` returns a **fresh** `EnsembleMomentumStrategy`
    instance -- never the one used for in-sample scoring, which would
    carry leftover internal state.
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

        if "candidates" not in params:
            # Byte-for-byte the original, no-grid-search behavior (Strategy
            # Research Task H) -- see class/module docstrings for why this
            # stays the default when a caller doesn't opt in.
            scoring_strategy = self._build_strategy(symbol=symbol)
            backtest_result = run_backtest(train_klines, scoring_strategy, self._fee_bps, self._slippage_bps)
            metrics = compute_metrics(
                train_klines,
                backtest_result.filled_intents,
                backtest_result.fills,
                self._starting_equity,
                bars_per_day=self._bars_per_day,
            )
            self._log_candidate(
                symbol=symbol,
                train_klines=train_klines,
                metrics=metrics,
                target_multiplier=self._target_multiplier,
                parent_run_id=parent_run_id,
                candidate_index=0,
                total_candidates=1,
            )
            return self._build_strategy(symbol=symbol)

        # Strategy Research Task I's opt-in risk:reward grid search -- see
        # module docstring's "optional risk:reward grid search" section.
        candidates = list(params["candidates"])
        if not candidates:
            raise ValueError(
                "params['candidates'] must be a non-empty sequence of (risk_reward_tenths,) "
                "tuples when provided -- omit the 'candidates' key entirely for the original, "
                "fixed (non-grid-searched) target_multiplier"
            )
        total_candidates = len(candidates)

        best_score: Decimal | None = None
        best_risk_reward_tenths: int | None = None

        for index, candidate in enumerate(candidates):
            (risk_reward_tenths,) = candidate
            if risk_reward_tenths <= 0:
                raise ValueError(f"risk_reward_tenths must be positive, got {risk_reward_tenths}")
            target_multiplier = self._stop_multiplier * Decimal(risk_reward_tenths) / Decimal(10)

            candidate_strategy = self._build_strategy(symbol=symbol, target_multiplier=target_multiplier)
            backtest_result = run_backtest(train_klines, candidate_strategy, self._fee_bps, self._slippage_bps)
            candidate_metrics = compute_metrics(
                train_klines,
                backtest_result.filled_intents,
                backtest_result.fills,
                self._starting_equity,
                bars_per_day=self._bars_per_day,
            )

            self._log_candidate(
                symbol=symbol,
                train_klines=train_klines,
                metrics=candidate_metrics,
                target_multiplier=target_multiplier,
                parent_run_id=parent_run_id,
                candidate_index=index,
                total_candidates=total_candidates,
            )

            if candidate_metrics.num_trades == 0:
                continue
            score = candidate_metrics.total_return
            if best_score is None or score > best_score:
                best_score = score
                best_risk_reward_tenths = risk_reward_tenths

        if best_risk_reward_tenths is None:
            best_risk_reward_tenths = candidates[0][0]
        winning_target_multiplier = self._stop_multiplier * Decimal(best_risk_reward_tenths) / Decimal(10)
        return self._build_strategy(symbol=symbol, target_multiplier=winning_target_multiplier)

    def _build_strategy(self, *, symbol: str, target_multiplier: Decimal | None = None) -> EnsembleMomentumStrategy:
        return EnsembleMomentumStrategy(
            symbol=symbol,
            lookback_pairs=self._lookback_pairs,
            atr_period=self._atr_period,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=target_multiplier if target_multiplier is not None else self._target_multiplier,
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
        target_multiplier: Decimal,
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
                "symbol": symbol,
                "lookback_pairs": [list(pair) for pair in self._lookback_pairs],
                "atr_period": self._atr_period,
                "stop_multiplier": str(self._stop_multiplier),
                "target_multiplier": str(target_multiplier),
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
                    "in-sample scoring inside EnsembleMomentumTrainable.fit() -- either the "
                    "single fixed-shape ensemble (no grid search) or one candidate of Strategy "
                    "Research Task I's opt-in risk:reward grid, see module docstring -- not "
                    "itself a walk-forward run"
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
