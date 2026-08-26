"""`python -m research.run_preregistered_holdout <path>` -- the single,
dedicated execution path for a `data.split == "holdout"` pre-registration.
Strategy Research Task V (`.planning/sr-v-preregistered-attempt-result.md`).

## Why this module exists, and why it is not `research.run_preregistered`

`research.run_preregistered.run_preregistered` deliberately **refuses** any
registration declaring `data.split == "holdout"` -- a scope guard against a
general-purpose runner spending an untouched holdout window as a one-command
accident (see that module's own docstring). A holdout confirmation is
supposed to be a single, deliberate act, so it needs its own, narrow, single-
purpose entry point rather than a flag on the general one.

**Not fold-based.** `research.walkforward.run_walk_forward`'s rolling
train/validate fold machinery does not fit a single-window holdout
confirmation: there is nothing to walk forward across, and CLAUDE.md's
holdout single-window confirmation variant explicitly drops every
fold-based Eligibility Bar clause (fold consistency, sign test) rather than
scaling them down. Instead, this module composes the pieces directly:

1. `research.preregistration.load_preregistration` -- read, hash, validate
   the committed registration.
2. `research.holdout.load_holdout_klines(..., i_understand_this_is_holdout_data=True,
   strategy_id=prereg.strategy_id)` -- the real, single, enforced-once
   access. If `strategy_id` already has a recorded `holdout_access` entry,
   this raises `research.holdout.HoldoutAlreadyClaimedError` and this
   module does **not** catch it, retry it, or pass a
   `force_reclaim_reason` on its own initiative -- a legitimate re-run is a
   human decision (see `research.holdout`'s own docstring), never something
   a runner decides for itself.
3. `research.run_preregistered.build_strategy` (reused, not duplicated) --
   instantiate the registered `strategy_entry_point` via this project's
   standard `TrainableStrategy` calling convention.
4. `strategy.fit(klines, prereg.run_params(), parent_run_id=...)` -- the
   registered strategy's own `fit()`, called once, against the **entire**
   registered window (there is no separate train/validate split at this
   level: the governing pre-registration's `total_candidates: 1` means
   `fit()` performs no search, so "in-sample scoring" here carries none of
   the overfitting risk it would for a strategy that actually searches).
   `fit()` returns a **fresh** bound `Strategy` instance -- this module then
   evaluates that fresh instance, not the one `fit()` used for its own
   internal (diagnostic-only) in-sample scoring pass.
5. `backtest.engine.run_backtest` -- the same lookahead-safe fill simulator
   every sibling strategy module in this package is scored through, run
   against the **same** klines `fit()` was given (see point 4: there is no
   separate validate window for a zero-search, single-window confirmation).
6. `metrics.metrics.compute_metrics` -- the summary `Metrics` (Sharpe,
   drawdown, trade count, profit factor, and -- unconditionally, Strategy
   Research Task Q -- the return skewness/kurtosis PSR needs).
7. `research.eligibility.psr_from_equity_curve` -- PSR against a zero
   benchmark, computed from **measured** moments of the real holdout equity
   curve (not the normal-assumption fallback), on the daily-resampled
   series (a near-no-op here since this strategy's own native bar is
   already 1 day -- `bars_per_day=1` -- but the real shared helper is
   called anyway, for consistency with every other PSR evaluation in this
   project, per the governing task brief).
8. `evaluate_gating` -- every one of the registration's own gating fields
   (PSR threshold, max-drawdown ceiling, minimum trade count, profit-factor
   floor, and CLAUDE.md's holdout clause 3 "observed Sharpe must exceed the
   window's own declared detection floor") compared against the observed
   result, and the PASS/INCONCLUSIVE/FAIL region the registration's own
   pre-committed `outcome_interpretation` text names -- literally, not
   re-narrated. This module computes which region the run lands in (a
   mechanical comparison against numbers the registration already
   committed to); it does **not** decide what that region *means* for the
   research program -- that consequence is the registration's own
   pre-committed text, quoted verbatim by whoever reads this run's result.
9. A direct `research.experiment_log.log_run` call (not
   `run_walk_forward`, for the fold-shape reason above) with
   `is_holdout_run=True`, `preregistration_id`, `preregistration_sha256`
   -- the record that matters for `research.holdout`'s single-access
   tracking and for any future re-evaluation of this run's PSR.

**One pre-existing, unrelated-to-this-task property worth restating**
(already flagged in `research.strategies.daily_tsmom_ensemble`'s module
docstring): `DailyTsmomEnsembleTrainable.fit()`'s own internal in-sample
scoring pass always logs its own diagnostic `backtest_run` record with
`is_holdout_run=False`, regardless of what data it was actually given --
matching every sibling `Trainable.fit()` in this package. That sub-record
is not the record that matters for holdout tracking; **this module's own
outer `log_run` call, step 9 above, is.**
"""

import argparse
import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist
from typing import Any
from uuid import uuid4

from backtest.engine import run_backtest
from backtest.kline import Kline
from data.backfill import DEFAULT_DB_PATH
from data.store import connect, find_missing_ranges
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from research.eligibility import SAMPLING_DAILY, PsrResult, psr_from_equity_curve
from research.holdout import load_holdout_klines
from research.preregistration import (
    Preregistration,
    frequency_scaled_min_trades,
    load_preregistration,
    warn_if_uncommitted,
)
from research.run_preregistered import build_strategy
from research.walkforward import TrainableStrategy

logger = logging.getLogger(__name__)

# Mark-to-market starting equity for this single-window evaluation. Same
# magnitude convention as `research.walkforward._DEFAULT_STARTING_EQUITY`
# and `daily_tsmom_ensemble.DEFAULT_STARTING_EQUITY` -- CLAUDE.md's
# Eligibility Bar is expressed in ratios (Sharpe, drawdown %, profit
# factor), which don't depend on this value's actual magnitude.
#
# Scalping Strategy Research Task S7: also passed to `backtest.engine.
# run_backtest`'s own `starting_equity` argument now, not just to
# `compute_metrics` -- so a holdout confirmation's insolvency floor (fills
# silently stop once mark-to-market equity would go to zero) is seeded
# from the identical figure the reported Sharpe/drawdown/profit-factor are
# computed against. See `.planning/scalp-s7-backtest-insolvency-floor.md`.
_DEFAULT_STARTING_EQUITY = Decimal("10000")

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
VALID_OUTCOMES = (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)

# `Phi^-1(0.95)`, one-sided alpha=0.05 -- matches every other detection-floor
# figure in this project's history (research/preregistration.py's own
# registration-time recomputation, sr-r, sr-q). Spelled out via
# `statistics.NormalDist().inv_cdf(0.95)` at call time below, not
# hardcoded, so this stays exact rather than a copied literal.
_DAYS_PER_YEAR = 365


def _data_range(klines: list[Kline]) -> dict:
    if not klines:
        return {"start_ms": None, "end_ms": None, "num_bars": 0}
    return {
        "start_ms": int(klines[0].open_time.timestamp() * 1000),
        "end_ms": int(klines[-1].open_time.timestamp() * 1000),
        "num_bars": len(klines),
    }


# ---------------------------------------------------------------------------
# Independent recomputation of the two numbers a registration is not free to
# choose -- not a duplicate of research.preregistration's own load-time
# validation, but a second, independent check at *execution* time that this
# module's own understanding of the registration's declared geometry agrees
# with what was committed.
#
# The two functions below are DELIBERATELY asymmetric in whether a mismatch
# gates execution (raises) or only warns -- see each docstring for why, and
# `run_preregistered_holdout`'s own call site below for where the asymmetry
# is actually enforced. In short: `min_total_trades` has a load-time
# "registered >= floor" guarantee from `research.preregistration
# .validate_preregistration` (stricter allowed, laxer rejected) computed from
# the exact same immutable inputs this module recomputes from, so a mismatch
# here can *only* ever mean "registered is legitimately stricter" -- gating
# execution on it would incorrectly block a valid registration.
# `declared_detection_floor_sharpe` has NO such load-time cross-check
# anywhere in this codebase (`validate_preregistration` only checks it is
# positive) -- a mismatch there is a genuine, reachable error with no other
# safety net, and DOES gate execution.
# ---------------------------------------------------------------------------


def verify_trade_floor(prereg: Preregistration) -> bool:
    """Recompute CLAUDE.md's frequency-scaled minimum trade count from the
    registration's own declared `data.expected_bars`/`procedure.bars_per_day`
    and confirm the registered `primary_criterion.min_total_trades` equals
    it exactly. `research.preregistration.validate_preregistration` already
    enforces `registered >= floor` (stricter allowed) at load time, from
    this exact same recomputation, against the exact same immutable
    `prereg.data`/`prereg.procedure` inputs -- so **by construction, a
    `False` return here can only ever mean "registered is a legitimately
    stricter floor than required"**, never "registered is laxer than
    approved" (that case is structurally unreachable: it would already have
    raised `PreregistrationError` inside `load_preregistration`, before a
    `Preregistration` object exists to call this function with at all).

    Because of that guarantee, a `False` return is **never treated as fatal**
    by any caller in this module -- doing so would incorrectly block a valid,
    more-conservative-than-required registration, contradicting
    `research.preregistration`'s own explicit "stricter is always accepted"
    policy. This function still exists and is still called (logged loudly,
    never raised, on mismatch) purely as a self-consistency sanity check:
    this specific registration was designed to match the floor exactly (see
    `.planning/sr-u-preregistered-attempt-spec.md`), so a mismatch -- while
    provably safe -- is still worth a human noticing before trusting the
    gating checks below. Contrast `verify_detection_floor`, which gates
    execution, precisely because it has no equivalent structural guarantee.
    """
    floor = frequency_scaled_min_trades(
        total_evaluated_bars=prereg.data["expected_bars"],
        bars_per_day=prereg.procedure["bars_per_day"],
    )
    registered = prereg.primary_criterion["min_total_trades"]
    if registered != floor:
        logger.warning(
            "verify_trade_floor: pre-registration %r declares min_total_trades=%r, which does not "
            "exactly equal the recomputed frequency-scaled floor (%d) for this geometry "
            "(expected_bars=%d, bars_per_day=%d) -- registering a STRICTER value than the floor is "
            "allowed by research.preregistration, but this specific registration was designed to "
            "match the floor exactly; a mismatch here is worth understanding before trusting the "
            "gating checks below",
            prereg.preregistration_id,
            registered,
            floor,
            prereg.data["expected_bars"],
            prereg.procedure["bars_per_day"],
        )
        return False
    return True


def recompute_detection_floor_sharpe(*, total_evaluated_bars: int, bars_per_day: int) -> float:
    """CLAUDE.md's holdout detection-floor Sharpe, one-sided alpha=0.05:
    `Phi^-1(0.95) / sqrt(years)`, `years = total_evaluated_bars / bars_per_day / 365`.
    Same normal-approximation method `sr-r`/`sr-q` use elsewhere in this
    project, and the same formula the governing registration's own
    `declared_power`/`declared_detection_floor_sharpe` fields document their
    derivation from.
    """
    if total_evaluated_bars <= 0:
        raise ValueError(f"total_evaluated_bars must be positive, got {total_evaluated_bars}")
    if bars_per_day <= 0:
        raise ValueError(f"bars_per_day must be positive, got {bars_per_day}")
    years = total_evaluated_bars / bars_per_day / _DAYS_PER_YEAR
    return NormalDist().inv_cdf(0.95) / math.sqrt(years)


def verify_detection_floor(prereg: Preregistration, *, tolerance: float = 1e-3) -> bool:
    """Recompute the declared detection-floor Sharpe from the registration's
    own declared geometry and confirm it matches
    `declared_detection_floor_sharpe` within `tolerance`.

    Unlike `verify_trade_floor`, a `False` return here **does gate
    execution** (see `run_preregistered_holdout`'s call site) -- because,
    unlike `min_total_trades`, `declared_detection_floor_sharpe` has NO
    load-time cross-check anywhere in this codebase:
    `research.preregistration.validate_preregistration` only confirms it is
    positive, never that it actually equals the recomputed value for the
    registration's own declared geometry. A wrong `declared_detection_floor
    _sharpe` (a typo, a stale copy-paste, an arithmetic slip) would
    otherwise pass validation silently and then gate this run's own
    PASS/INCONCLUSIVE/FAIL determination against the wrong number, with
    nothing else in this project positioned to catch it. This function is
    that catch.
    """
    recomputed = recompute_detection_floor_sharpe(
        total_evaluated_bars=prereg.data["expected_bars"],
        bars_per_day=prereg.procedure["bars_per_day"],
    )
    registered = float(prereg.config["declared_detection_floor_sharpe"])
    matches = math.isclose(recomputed, registered, abs_tol=tolerance)
    if not matches:
        logger.warning(
            "verify_detection_floor: pre-registration %r declares declared_detection_floor_sharpe=%.4f, "
            "independently recomputed here as %.4f from its own declared geometry -- these should "
            "agree closely; investigate before trusting the PASS/INCONCLUSIVE/FAIL region below",
            prereg.preregistration_id,
            registered,
            recomputed,
        )
    return matches


class UnexpectedKnownGapsError(RuntimeError):
    """Raised when a registration's declared `data.known_gaps` does not
    match the real gap set for its `symbol`/`interval`/`start_ms`/`end_ms`
    range exactly -- fewer, more, or relocated gaps versus what was
    declared before this registration was committed. Fail-closed: an
    undetermined/changed gap situation must never be silently treated as
    "safe to proceed" (same discipline as `KrxMarketCalendar`'s
    holiday-lookup rule, CLAUDE.md's Java side).
    """


def verify_known_gaps(prereg: Preregistration, *, db_path: str | Path) -> None:
    """No-op when `prereg.data` does not declare `known_gaps` (every
    registration before this field existed, and every future registration
    against an interval with zero known gaps, is completely unaffected --
    this check is purely additive).

    When `known_gaps` IS declared, diffs the real kline sequence for this
    registration's own `symbol`/`interval`/`start_ms`/`end_ms` (via
    `data.store.find_missing_ranges`) against the declared list and raises
    `UnexpectedKnownGapsError` if they do not match exactly. Called from
    `run_preregistered_holdout` BEFORE `load_holdout_klines`, same
    fail-closed-before-any-data-is-loaded placement as
    `verify_detection_floor` -- a real CodeRabbit review finding
    (Scalping Strategy Research Task S4's own PR) caught that an earlier,
    interval-specific-only version of this check (`research.
    verify_1m_gaps`, still present as a standalone diagnostic) was never
    actually enforced on the real execution path: nothing stopped a
    caller from invoking `run_preregistered_holdout` directly and bypassing
    it entirely. Folding the check in here, gated on an opt-in
    registration field rather than a hardcoded symbol/interval, closes
    that gap for every current and future registration in one place,
    rather than requiring a fresh dedicated wrapper module per interval
    that happens to have known gaps.
    """
    declared = prereg.data.get("known_gaps")
    if declared is None:
        return

    expected = tuple((int(start), int(end)) for start, end in declared)

    conn = connect(db_path)
    try:
        real_gaps = find_missing_ranges(
            conn,
            prereg.data["symbol"],
            prereg.data["interval"],
            prereg.data["start_ms"],
            prereg.data["end_ms"],
        )
    finally:
        conn.close()

    if tuple(real_gaps) != expected:
        raise UnexpectedKnownGapsError(
            f"pre-registration {prereg.preregistration_id!r} declares known_gaps={expected!r} for "
            f"{prereg.data['symbol']}/{prereg.data['interval']}, but the real gap set in "
            f"[{prereg.data['start_ms']}, {prereg.data['end_ms']}) is {real_gaps!r} -- refusing to "
            "load or score holdout data until this discrepancy (a new gap, a resolved gap, or a "
            "relocated one) is understood and, if genuine, disclosed and this registration updated "
            "accordingly (a changed known_gaps value requires a new preregistration_id, same as any "
            "other post-commit change to this file)."
        )


# ---------------------------------------------------------------------------
# Gating: every field the registration's own primary_criterion names,
# compared against the observed result, reduced to the PASS/INCONCLUSIVE/
# FAIL region CLAUDE.md's holdout single-window variant and the
# registration's own outcome_interpretation define.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatingCheck:
    label: str
    required: Any
    observed: Any
    passed: bool

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "required": self.required,
            "observed": self.observed,
            "passed": self.passed,
        }


def evaluate_gating(
    prereg: Preregistration,
    *,
    psr_result: PsrResult,
    metrics: Metrics,
    declared_detection_floor: float,
) -> tuple[str, dict[str, GatingCheck]]:
    """Compare the observed result against every field of the registration's
    `primary_criterion`, and return `(outcome_region, checks)`.

    `outcome_region` is one of `PASS`/`INCONCLUSIVE`/`FAIL`
    (`VALID_OUTCOMES`), determined mechanically:

    - **PASS** iff every one of the five gating checks passes (PSR
      threshold, max-drawdown ceiling, minimum trade count, profit-factor
      floor, and the observed annualized Sharpe exceeding the declared
      detection floor) -- matching the registration's own PASS text
      verbatim ("... all cleared, AND the observed annualized Sharpe
      exceeding ...").
    - **FAIL** iff PSR is undefined (a degenerate zero-trade/zero-variance
      result) or non-positive -- matching the registration's own FAIL text
      verbatim.
    - **INCONCLUSIVE** otherwise -- covers "PSR positive but below
      threshold", "Sharpe does not exceed the detection floor", and "trade
      count below the frequency-scaled floor" alike, per the registration's
      own INCONCLUSIVE text (each is named there as an independent `OR`
      condition, and any of them alone is enough to not reach PASS without
      being a FAIL).

    Profit factor's `None` case is interpreted per CLAUDE.md's own clause
    (also restated in the registration's `secondary_reported_not_gating`):
    a zero-trade result already fails via the trade-count check regardless
    of profit factor; a `None` profit factor with `num_trades > 0` means
    zero losing trades (`metrics.metrics._profit_factor`'s own contract),
    which trivially satisfies the floor rather than failing it.
    """
    criterion = prereg.primary_criterion
    psr_threshold = float(criterion["threshold"])
    max_dd_ceiling = Decimal(str(criterion["max_drawdown_ceiling"]))
    min_trades = int(criterion["min_total_trades"])
    profit_factor_floor = Decimal(str(criterion["profit_factor_floor"]))

    psr_pass = psr_result.psr is not None and psr_result.psr >= psr_threshold
    dd_pass = metrics.max_drawdown <= max_dd_ceiling
    trades_pass = metrics.num_trades >= min_trades

    if metrics.num_trades == 0:
        pf_pass = False  # no evidence either way; already fails via trades_pass
    elif metrics.profit_factor is None:
        pf_pass = True  # zero losing trades -- trivially satisfies the floor
    else:
        pf_pass = Decimal(str(metrics.profit_factor)) >= profit_factor_floor

    sharpe_pass = metrics.sharpe_ratio is not None and metrics.sharpe_ratio > declared_detection_floor

    checks = {
        "psr": GatingCheck("PSR >= registered threshold", psr_threshold, psr_result.psr, psr_pass),
        "max_drawdown": GatingCheck(
            "observed max drawdown <= registered ceiling",
            str(max_dd_ceiling),
            str(metrics.max_drawdown),
            dd_pass,
        ),
        "min_total_trades": GatingCheck(
            "observed trade count >= registered floor", min_trades, metrics.num_trades, trades_pass
        ),
        "profit_factor": GatingCheck(
            "observed profit factor >= registered floor",
            str(profit_factor_floor),
            metrics.profit_factor,
            pf_pass,
        ),
        "sharpe_above_detection_floor": GatingCheck(
            "observed annualized Sharpe > declared detection floor",
            declared_detection_floor,
            metrics.sharpe_ratio,
            sharpe_pass,
        ),
    }

    if psr_pass and dd_pass and trades_pass and pf_pass and sharpe_pass:
        outcome = OUTCOME_PASS
    elif psr_result.psr is None or psr_result.psr <= 0:
        outcome = OUTCOME_FAIL
    else:
        outcome = OUTCOME_INCONCLUSIVE

    return outcome, checks


# ---------------------------------------------------------------------------
# The execution itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldoutConfirmationResult:
    run_id: str
    strategy_id: str
    preregistration_id: str
    preregistration_sha256: str
    klines_count: int
    metrics: Metrics
    psr_result: PsrResult
    outcome: str
    gating_checks: dict[str, GatingCheck]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "preregistration_id": self.preregistration_id,
            "preregistration_sha256": self.preregistration_sha256,
            "klines_count": self.klines_count,
            "outcome": self.outcome,
            "gating_checks": {key: value.to_dict() for key, value in self.gating_checks.items()},
        }


def _log_holdout_confirmation(
    prereg: Preregistration,
    *,
    run_id: str,
    klines: list[Kline],
    metrics: Metrics,
    psr_result: PsrResult,
    outcome: str,
    checks: dict[str, GatingCheck],
    runs_path: str | Path,
) -> dict:
    metrics_summary = {
        "starting_equity": metrics.starting_equity,
        "final_equity": metrics.final_equity,
        "total_return": metrics.total_return,
        "sharpe_ratio": metrics.sharpe_ratio,
        "max_drawdown": metrics.max_drawdown,
        "win_rate": metrics.win_rate,
        "num_trades": metrics.num_trades,
        "profit_factor": metrics.profit_factor,
        "return_skewness": metrics.return_skewness,
        "return_kurtosis": metrics.return_kurtosis,
        "num_returns": metrics.num_returns,
    }
    fold_results = [
        {
            "fold_index": 0,
            "train_start_index": 0,
            "train_end_index": len(klines),
            "validate_start_index": 0,
            "validate_end_index": len(klines),
            "metrics": metrics_summary,
        }
    ]
    aggregate_metrics = {
        "fold_count": 1,
        "mean_sharpe": metrics.sharpe_ratio,
        "min_sharpe": metrics.sharpe_ratio,
        "all_folds_positive_sharpe": metrics.sharpe_ratio is not None and metrics.sharpe_ratio > 0,
        "worst_fold_max_drawdown": metrics.max_drawdown,
        "mean_total_return": metrics.total_return,
        "total_trades": metrics.num_trades,
        "mean_profit_factor": metrics.profit_factor,
        "min_profit_factor": metrics.profit_factor,
        "folds_with_zero_trades": 1 if metrics.num_trades == 0 else 0,
        "return_skewness": metrics.return_skewness,
        "return_kurtosis": metrics.return_kurtosis,
        "num_returns": metrics.num_returns,
        "psr": psr_result.to_dict(),
        "declared_detection_floor_sharpe": float(prereg.config["declared_detection_floor_sharpe"]),
        "gating_checks": {key: value.to_dict() for key, value in checks.items()},
        "outcome_region": outcome,
    }
    walk_forward_config = {
        "train_bars": len(klines),
        "validate_bars": len(klines),
        "step_bars": 0,
        "fold_count": 1,
        "bars_per_day": int(prereg.procedure["bars_per_day"]),
        "note": (
            "single-window holdout confirmation (Strategy Research Task V, "
            "research.run_preregistered_holdout) -- not a walk-forward run: the entire "
            "registered window is fit and evaluated once, with no train/validate split within "
            "it, per CLAUDE.md's holdout single-window confirmation variant"
        ),
    }

    return experiment_log.log_run(
        run_id=run_id,
        strategy_id=prereg.strategy_id,
        strategy_version=prereg.strategy_version,
        params=prereg.run_params(),
        fold_results=fold_results,
        aggregate_metrics=aggregate_metrics,
        data_range=_data_range(klines),
        walk_forward_config=walk_forward_config,
        fee_bps=prereg.fee_bps,
        slippage_bps=prereg.slippage_bps,
        is_holdout_run=True,
        parent_run_id=None,
        candidate_index=None,
        total_candidates=prereg.total_candidates,
        strategy_family=prereg.strategy_family,
        preregistration_id=prereg.preregistration_id,
        preregistration_sha256=prereg.sha256,
        runs_path=runs_path,
    )


def run_preregistered_holdout(
    prereg: Preregistration,
    *,
    strategy: TrainableStrategy | None = None,
    klines: list[Kline] | None = None,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    force_reclaim_reason: str | None = None,
    starting_equity: Decimal = _DEFAULT_STARTING_EQUITY,
) -> HoldoutConfirmationResult:
    """Execute the registered holdout confirmation exactly once.

    `strategy` and `klines` exist so a test can inject both without a
    database or an importable strategy, mirroring
    `research.run_preregistered.run_preregistered`'s identical pattern; in
    real use both are `None` and come from the registration
    (`strategy_entry_point`, and the registered window loaded through
    `research.holdout.load_holdout_klines` -- the ONLY loader in this
    project that can return real holdout data, and the one that enforces
    the single-access-per-`strategy_id` claim).

    Raises `ValueError` for a research-split registration (this is the
    dedicated holdout runner; use `research.run_preregistered` for those);
    `ValueError` for a bar-count mismatch between the loaded holdout window
    and the registration's `expected_bars` (fail-closed, since the
    registered detection floor and trade-count floor were computed from
    `expected_bars` and no longer describe a differently-sized window --
    **on the real loader path (`klines=None`, i.e. every real invocation),
    this failure happens AFTER the single-access holdout claim is already
    consumed**, since the mismatch can only be detected once the data has
    actually been loaded, so a caller hitting this on that path needs a
    deliberate, justified `force_reclaim_reason` to try again, not a free
    retry -- this does NOT apply when a test injects `klines` directly,
    since that path never calls `load_holdout_klines` at all and so never
    writes a claim to consume in the first place); `ValueError` if
    `verify_detection_floor` finds the registered `declared_detection_floor
    _sharpe` does not match the recomputed value for the registration's own
    declared geometry (checked, and fails closed, BEFORE any holdout data is
    loaded -- see `verify_detection_floor`'s own docstring for why this one,
    unlike `verify_trade_floor`, gates execution); and
    `research.holdout.HoldoutAlreadyClaimedError` if `strategy_id` already
    has a recorded `holdout_access` entry and no `force_reclaim_reason` was
    explicitly supplied by the caller -- this function never supplies one on
    its own initiative.
    """
    if not prereg.is_holdout_confirmation:
        raise ValueError(
            f"pre-registration {prereg.preregistration_id!r} declares data.split="
            f"{prereg.data['split']!r}: research.run_preregistered_holdout drives holdout-split "
            "registrations only. Use research.run_preregistered for a research-split run."
        )

    warn_if_uncommitted(prereg.path)
    # verify_trade_floor's False is never fatal here -- see its own
    # docstring: by construction it can only mean "registered is a
    # legitimately stricter floor", never an approved-floor violation.
    verify_trade_floor(prereg)
    # verify_detection_floor's False IS fatal, checked and raised BEFORE any
    # holdout data is loaded (i.e. before the single-access claim could be
    # consumed) -- see its own docstring for why this field has no other
    # safety net in this codebase.
    if not verify_detection_floor(prereg):
        raise ValueError(
            f"pre-registration {prereg.preregistration_id!r}: declared_detection_floor_sharpe="
            f"{prereg.config['declared_detection_floor_sharpe']!r} does not match the value "
            "independently recomputed from this registration's own declared geometry (see the "
            "WARNING logged just above by verify_detection_floor for the exact numbers). Refusing "
            "to load or score holdout data against a detection floor that may be wrong -- a "
            "committed registration must not be edited after the fact; fix requires either "
            "correcting a genuine transcription error before this registration's very first real "
            "access (none has happened yet if this fires), or registering a new specification "
            "under a new preregistration_id."
        )

    data = prereg.data
    if klines is not None:
        # Test-only injection path (mirrors research.run_preregistered's
        # identical `klines` parameter). Bypassing load_holdout_klines here
        # means NO holdout_access claim gets recorded and the single-access
        # enforcement is skipped entirely -- harmless for a test double, but
        # worth being loud about in case this is ever reached on a real path
        # by mistake. (CodeRabbit review finding on this PR.)
        logger.warning(
            "run_preregistered_holdout: klines were injected by the caller, so "
            "research.holdout.load_holdout_klines was NOT called and NO holdout_access claim was "
            "recorded for strategy_id=%r. This path is for tests only -- a real holdout "
            "confirmation must go through the enforced single-access loader.",
            prereg.strategy_id,
        )
    else:
        # Fail closed BEFORE any holdout data is loaded (and therefore
        # before the single-access claim could be consumed) if this
        # registration declares known_gaps and the real gap set no longer
        # matches -- see verify_known_gaps's own docstring. A no-op for
        # every registration that doesn't declare known_gaps (unchanged
        # behavior for every pre-existing registration).
        verify_known_gaps(prereg, db_path=db_path)

        klines = load_holdout_klines(
            data["start_ms"],
            data["end_ms"],
            strategy_id=prereg.strategy_id,
            i_understand_this_is_holdout_data=True,
            force_reclaim_reason=force_reclaim_reason,
            db_path=db_path,
            holdout_config_path=data["holdout_config_path"],
            runs_path=runs_path,
        )

    if len(klines) != data["expected_bars"]:
        # Fail CLOSED here, unlike research.run_preregistered's identical-
        # looking check on a research-split run (which only warns): this is
        # the holdout confirmation, evaluated exactly once and never re-run
        # under the same strategy_id. The registered detection floor and
        # trade-count floor were both computed FROM expected_bars at
        # registration time; continuing to gate against them when the
        # actually-loaded window differs would silently compare the result
        # against floors that no longer describe what was evaluated -- a
        # research-split caller can just re-run with a corrected window, but
        # a holdout confirmation cannot without burning a second, human-
        # justified claim. (CodeRabbit review finding on this PR.)
        raise ValueError(
            f"pre-registration {prereg.preregistration_id!r} declares expected_bars="
            f"{data['expected_bars']} but {len(klines)} bar(s) loaded. The registered window is "
            "what the declared detection floor and trade-count floor were computed from, so a gap "
            "here invalidates both gating thresholds -- refusing to score a holdout confirmation "
            "against floors that no longer describe the evaluated window."
        )

    run_id = str(uuid4())

    if strategy is None:
        strategy = build_strategy(prereg, runs_path=runs_path)

    # fit() performs no search (total_candidates: 1) -- it logs its own
    # diagnostic in-sample-scoring sub-record (is_holdout_run=False, matching
    # every sibling Trainable's fit(), see this module's docstring) and
    # returns a FRESH bound strategy, never the one it used for that
    # internal scoring pass.
    bound_strategy = strategy.fit(klines, prereg.run_params(), parent_run_id=run_id)

    # Evaluated against the SAME window fit() was given: there is no
    # separate train/validate split at this level (see module docstring) --
    # the whole point of a zero-fitted-parameter strategy is that there is
    # nothing to overfit to by doing so.
    backtest_result = run_backtest(
        klines, bound_strategy, prereg.fee_bps, prereg.slippage_bps, starting_equity=starting_equity
    )

    bars_per_day = int(prereg.procedure["bars_per_day"])
    metrics = compute_metrics(
        klines,
        backtest_result.filled_intents,
        backtest_result.fills,
        starting_equity,
        bars_per_day=bars_per_day,
        funding_rates=None,  # registered funding_included: false
    )

    psr_result = psr_from_equity_curve(
        metrics.equity_curve,
        bars_per_day=bars_per_day,
        sampling=SAMPLING_DAILY,
        benchmark_sharpe=0.0,
    )

    declared_floor = float(prereg.config["declared_detection_floor_sharpe"])
    outcome, checks = evaluate_gating(
        prereg, psr_result=psr_result, metrics=metrics, declared_detection_floor=declared_floor
    )

    _log_holdout_confirmation(
        prereg,
        run_id=run_id,
        klines=klines,
        metrics=metrics,
        psr_result=psr_result,
        outcome=outcome,
        checks=checks,
        runs_path=runs_path,
    )

    return HoldoutConfirmationResult(
        run_id=run_id,
        strategy_id=prereg.strategy_id,
        preregistration_id=prereg.preregistration_id,
        preregistration_sha256=prereg.sha256,
        klines_count=len(klines),
        metrics=metrics,
        psr_result=psr_result,
        outcome=outcome,
        gating_checks=checks,
    )


def _format_summary(prereg: Preregistration, result: HoldoutConfirmationResult) -> str:
    lines = [
        f"pre-registration : {prereg.preregistration_id}",
        f"  file           : {prereg.path}",
        f"  sha256         : {prereg.sha256}",
        f"  strategy       : {prereg.strategy_id} {prereg.strategy_version} (family {prereg.strategy_family})",
        "",
        f"run_id           : {result.run_id}",
        f"  bars evaluated : {result.klines_count}",
        f"  observed annualized Sharpe : {result.metrics.sharpe_ratio}",
        f"  declared detection floor   : {prereg.config['declared_detection_floor_sharpe']}",
        f"  PSR                        : {result.psr_result.psr}",
        f"  max drawdown               : {result.metrics.max_drawdown}",
        f"  total trades               : {result.metrics.num_trades}",
        f"  profit factor              : {result.metrics.profit_factor}",
        "",
        "gating checks:",
    ]
    for key, check in result.gating_checks.items():
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{status}] {key}: required={check.required!r} observed={check.observed!r}")
    lines += [
        "",
        f"OUTCOME: {result.outcome}",
        prereg.config["outcome_interpretation"][result.outcome],
        "",
        "This runner does not itself amend or re-narrate the pre-committed outcome text above.",
    ]
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute the SINGLE pre-registered holdout confirmation this module exists for "
            "(Strategy Research Task V). Refuses a research-split registration -- use "
            "research.run_preregistered for those. Loads real holdout data through "
            "research.holdout.load_holdout_klines's single-access-per-strategy_id claim: a "
            "second invocation for the same strategy_id raises HoldoutAlreadyClaimedError "
            "unless --force-reclaim-reason is given explicitly, which is a human decision this "
            "script never makes on its own."
        )
    )
    parser.add_argument("preregistration_path", help="path to configs/research/preregistrations/<id>.json")
    parser.add_argument("--runs-path", default=experiment_log.DEFAULT_RUNS_PATH)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--force-reclaim-reason",
        default=None,
        help=(
            "Only pass this for a deliberate, justified re-run of an ALREADY-claimed holdout "
            "access. Omit it for the normal, single, real execution this script exists for."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    prereg = load_preregistration(args.preregistration_path)
    result = run_preregistered_holdout(
        prereg,
        runs_path=args.runs_path,
        db_path=args.db_path,
        force_reclaim_reason=args.force_reclaim_reason,
    )
    print(_format_summary(prereg, result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
