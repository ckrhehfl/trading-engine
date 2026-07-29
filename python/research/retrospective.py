"""Statistical close-out of this project's strategy research -- Strategy
Research Task R.

Eight strategy families have now been walk-forward validated against real
BingX data (CLAUDE.md's "Strategy Attempts So Far"), and none cleared the
Eligibility Bar. Two sibling tasks then fixed the *measurement apparatus*
rather than the strategies:

- `sr-p` (`research/lineage.py`, `research/overfitting_check.py`) -- an
  honest trial count `N`, at family level and project level, with
  parameter-sensitivity probes classified out of it.
- `sr-q` (`research/eligibility.py`) -- the Probabilistic and Deflated
  Sharpe Ratios, the statistically correct way to ask "is this best-of-N
  result real?".

This module composes the two and applies them, once, to **every distinct
multi-fold run in the real experiment log**. It computes nothing new about
any strategy; it re-judges what was already measured, under a statistic
that knows how much searching produced it.

## The composition seam sr-q deliberately left open

`sr-p` reports two defensible trial counts and correctly declined to pick
between them ("a judgment call the heuristic shouldn't make
unilaterally"); `sr-q` takes `num_trials` as a plain parameter for the same
reason. Choosing is this module's job, so it is made explicitly here rather
than left to a caller's accident:

**Both are computed and reported for every row, and the project-level `N`
is the one the `SURVIVES` verdict is decided on.** The reasoning is
Bailey's own definition -- `N` is "the number of trials from which this
maximum was selected". Within `trend-momentum` this project compared 97
momentum variants against each other, but it *also* chose momentum over
mean-reversion, over the momentum/reversion blend, over volume, over
funding, **after seeing all of their results**. For the question this
retrospective actually asks -- "is the best thing this project found
real?" -- the selection pool is the project's whole research total, not any
one family's slice of it. The family-level figure is kept alongside because
it is the right number for the narrower question "is this family's own
winner real *within that family*", and because publishing only the larger
one would hide how little of the deflation is attributable to any single
research thread.

`purpose` keeps the two pools separate: an `"infrastructure"` row (a
pipeline end-to-end demo that was never a paper-trading candidate) is
judged against the infrastructure `N`, not the research one, so neither
pool inflates the other.

## The honesty requirement, made structural

Every verdict row carries **the study's own detection floor** -- the
smallest true annualized Sharpe this data span could have distinguished
from zero at one-sided alpha=0.05, approximately
`Phi^-1(1 - alpha) / sqrt(years)` (~1.21 over the 1h research window,
~2.18 over the 15m one). Without it, "DSR ~= 0" reads as a far stronger
claim than the evidence supports: it means **indistinguishable from
best-of-N luck**, NOT **proven to lose money**. `render_markdown_table`
therefore emits the floor as a fixed column adjacent to the verdict, and
`VerdictRow.to_dict()` always includes it -- a reader cannot obtain one
without the other.

The same reasoning is why the verdict vocabulary distinguishes
`REJECTED-UNDERPOWERED` from `REJECTED`: "not shown" is not "shown
absent", and a four-label vocabulary that collapsed them would have let
this project record a stronger negative conclusion than it earned.

## Verdict vocabulary

- **`SURVIVES`** -- `DSR >= 0.95` at the project-level `N`.
- **`INCONCLUSIVE-DATA-LIMITED`** -- below the trade-count floor. The study
  cannot conclude in either direction.
- **`REJECTED`** -- below the DSR threshold with a non-positive point
  estimate (or an adequately powered study that still showed nothing --
  see `assign_verdict`).
- **`REJECTED-UNDERPOWERED`** -- below the DSR threshold with a *positive*
  point estimate, but the study's own detection floor exceeds any plausible
  true edge. Means "not shown", explicitly not "shown absent".

**Precedence is deliberate**: the trade-count floor is checked *before*
every other label, `SURVIVES` included. "This study cannot conclude"
logically precedes both "shown" and "not shown", so a 14-trade run is
recorded neither as a rejection nor as a survivor. See `assign_verdict` for
the full reasoning and the named cost. (On the real log the ordering
changes no row -- the one data-limited family's DSR is ~0 anyway -- but a
future low-frequency strategy would be misfiled by any other order.)

## Units, and why every Sharpe here is daily

PSR/DSR are defined on per-observation Sharpe. This module de-annualizes
everything to a **daily** scale (`SAMPLING_DAILY`), for two independent
reasons:

1. `sr-q`'s: PSR assumes iid returns, and per-bar returns inside a
   multi-bar holding period are autocorrelated, which inflates the
   effective `T` and makes PSR anti-conservative. Daily resampling is the
   standard remedy and costs well under 1% of detection power.
2. This module's own: a family's trial Sharpes span **two timeframes**
   (`trend-momentum` covers both the 15m and 1h eras). A per-bar scale
   would put 15m and 1h trial Sharpes on different axes and make `V_hat`
   meaningless; a per-day scale is the same axis for both.

The two conventions agree to five decimals on this project's headline
figure anyway -- `sr-q` published both (per-bar 0.5194673 at T=13,680;
daily 0.5194509 at T=570) and `.planning/sr-r-retrospective-closeout.md`
reproduces both.

## What cannot be recovered from the historical log, and how it is handled

- **Return moments** (`g3`/`g4`). `walkforward._metrics_summary` dropped
  `equity_curve` until `sr-q`, so no pre-`sr-q` record carries them. Every
  row here therefore falls back to the normal assumption, and says so
  (`PsrResult.moments_source == "normal_assumption"`). `sr-q` measured the
  effect at this project's magnitudes: ~1e-7 on PSR. Real, disclosed, and
  negligible.
- **`bars_per_day`**. Also `sr-q`-and-later only. `infer_bars_per_day`
  prefers the logged value and otherwise derives the bar interval
  arithmetically from `data_range` (`(end_ms - start_ms) / (num_bars - 1)`),
  which is exact on this log -- 3,600,000ms and 900,000ms on the nose -- and
  refuses to guess when the result does not divide a day evenly.

## Read-only, always

`runs/experiments.jsonl` is an append-only research audit trail. Nothing in
this module writes to it, and no test may read it (they use synthetic
fixtures) -- same discipline as `research/overfitting_check.py`.

## Eligibility Bar status: this module does not amend it

CLAUDE.md's Eligibility Bar is human-approval-gated (same status as Risk
Parameters). The DSR-driven verdicts here are a **retrospective diagnostic**
over already-completed research, not a gate any future run passes through.
Proposals to amend the Bar itself live in
`.planning/sr-r-retrospective-closeout.md` as clearly-labelled proposals
awaiting human approval, exactly as `sr-q` left them.
"""

import argparse
import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import NormalDist
from typing import Any

from research import experiment_log
from research.eligibility import (
    DEFAULT_SIGNIFICANCE_ALPHA,
    SAMPLING_DAILY,
    deannualize_sharpe,
    evaluate_deflated_sharpe,
    evaluate_fold_consistency,
    evaluate_mean_sharpe_significance,
    evaluate_psr,
    evaluate_sign_test,
    sharpe_variance_across_trials,
)
from research.lineage import FamilyResolution, resolve_family
from research.overfitting_check import (
    SENSITIVITY_PARENT_RUN_ID_PREFIX,
    TrialKind,
    check_project_combination_count,
    classify_trial_kind,
)

_MS_PER_DAY = 86_400_000
# 365, not 365.25 -- this project's fixed convention (`metrics/metrics.py`'s
# `_DAYS_PER_YEAR`, `research/eligibility.py`'s copy of it).
_MS_PER_YEAR = 365 * _MS_PER_DAY

VERDICT_REJECTED = "REJECTED"
VERDICT_REJECTED_UNDERPOWERED = "REJECTED-UNDERPOWERED"
VERDICT_INCONCLUSIVE_DATA_LIMITED = "INCONCLUSIVE-DATA-LIMITED"
VERDICT_SURVIVES = "SURVIVES"

# `DSR >= 0.95` is the same one-sided alpha=0.05 confidence level every other
# significance check in this project uses (`eligibility.
# DEFAULT_SIGNIFICANCE_ALPHA`), expressed as a probability rather than a
# tail area.
DEFAULT_DSR_THRESHOLD = 0.95

# CLAUDE.md's current Eligibility Bar floor. Carried as a default rather
# than hardcoded inline because Gate 1 Proposal 3 (`.planning/sr-r-
# retrospective-closeout.md`) proposes replacing it with a
# frequency-appropriate one, and a report re-run under a proposed floor
# must not require editing this module.
DEFAULT_MIN_TOTAL_TRADES = 100

# The largest annualized Sharpe it is reasonable to believe a real,
# single-symbol BTC-USDT futures strategy could have. Used only to decide
# whether a study that showed nothing was **capable** of showing something:
# when the detection floor exceeds this, a null result carries no
# information about edges at or below it. 1.0 is deliberately generous --
# the credible institutional trend-following research this project
# benchmarked against (`.planning/sr-g-overfitting-safeguards.md`) reports
# programme-level Sharpes below it, and a generous value makes
# `REJECTED-UNDERPOWERED` *harder* to claim, not easier.
DEFAULT_PLAUSIBLE_MAX_TRUE_SHARPE = 1.0


# ---------------------------------------------------------------------------
# The detection floor
# ---------------------------------------------------------------------------


def detection_floor_sharpe(years: float, *, alpha: float = DEFAULT_SIGNIFICANCE_ALPHA) -> float | None:
    """The smallest true annualized Sharpe a study spanning `years` could
    have distinguished from zero, one-sided, at significance `alpha`:

        floor ~= Phi^-1(1 - alpha) / sqrt(years)

    (`Phi^-1(0.95) = 1.6448536...`, the "1.645" of the usual shorthand.)

    This is the standard large-sample result: the standard error of an
    annualized Sharpe estimated over `T` observations is ~`1/sqrt(T)`
    *observations*, which in annualized units is `1/sqrt(years)` regardless
    of sampling frequency -- which is exactly why resampling hourly bars to
    daily costs almost no detection power (`sr-q` measured it at well under
    1%), and why the floor is a property of the **calendar span**, not of
    the bar size.

    `None` -- never a fabricated number -- when there is no usable span, the
    same "no evidence" convention as the rest of this project's statistics.
    An impossible `alpha` is a caller mistake and raises.

    Real values on this project's data: ~1.21 over the 1h research window
    (1.835 years), ~2.18 over the 15m one (0.567 years). Both are far above
    any Sharpe any strategy here produced, which is the single most
    important fact this report has to communicate.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if years <= 0:
        return None
    return NormalDist().inv_cdf(1.0 - alpha) / math.sqrt(years)


def infer_bars_per_day(record: Mapping[str, Any]) -> int | None:
    """`walk_forward_config.bars_per_day` when the record carries it
    (`sr-q` onward), otherwise derived arithmetically from `data_range`:
    `(end_ms - start_ms) / (num_bars - 1)` is the bar interval, and a day
    divided by it is `bars_per_day`.

    The derivation is exact on this project's real log -- it recovers
    3,600,000ms for every 1h record and 900,000ms for every 15m one, with no
    remainder -- which is why it is preferred over `sr-q`'s noted fallback of
    reverse-engineering `train_bars` (2160 => 1h, 8640 => 15m): that mapping
    is a coincidence of two chosen window sizes, while this is a
    measurement.

    `None` (never a guess) when the range spans fewer than two bars, is
    missing an endpoint, or yields an interval that does not divide a day
    evenly -- an interval that isn't a whole-number fraction of a day means
    something is wrong with the record, not that a rounded answer would do.
    """
    logged = (record.get("walk_forward_config") or {}).get("bars_per_day")
    if isinstance(logged, int) and logged > 0:
        return logged

    data_range = record.get("data_range") or {}
    start_ms = data_range.get("start_ms")
    end_ms = data_range.get("end_ms")
    num_bars = data_range.get("num_bars")
    if start_ms is None or end_ms is None or not isinstance(num_bars, int) or num_bars < 2:
        return None

    span_ms = end_ms - start_ms
    if span_ms <= 0 or span_ms % (num_bars - 1) != 0:
        return None
    interval_ms = span_ms // (num_bars - 1)
    if interval_ms <= 0 or _MS_PER_DAY % interval_ms != 0:
        return None
    return _MS_PER_DAY // interval_ms


# ---------------------------------------------------------------------------
# De-duplicating reproductions into distinct configurations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DistinctRun:
    """One distinct configuration, plus every logged re-run of it.

    `record` is the **first** logged run with this fingerprint -- first, not
    best, so the report cannot be accused of selecting the most favourable
    of a set of identical results. `duplicate_run_ids` names the rest in log
    order, so nothing disappears silently.
    """

    family: str
    purpose: str
    record: dict
    duplicate_run_ids: list[str] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.record.get("run_id", "")

    @property
    def run_count(self) -> int:
        return 1 + len(self.duplicate_run_ids)


def _fold_fingerprint(record: Mapping[str, Any]) -> str:
    """Fingerprint a multi-fold run by its **results**, not its metadata.

    `sr-p` established that the configuration under test is never in the
    log -- it lives in the `Trainable`'s constructor arguments, which
    `run_walk_forward` never sees -- so `params`/`strategy_version` cannot
    identify a configuration. What *can*, and much more sharply, is the
    per-fold result vector itself: two runs agreeing on `(sharpe_ratio,
    num_trades, total_return)` in all 19 folds over the same data range are
    the same computation by any standard, and the chance of two genuinely
    different configurations colliding on 57 floats is nil.

    This deliberately fingerprints *narrower* than `sr-p`'s
    `_standalone_fingerprint`, which also keys on `strategy_version` and so
    keeps re-runs under a new version string apart. That conservatism is
    right for `N` (never merge away a trial you might have to answer for);
    it is wrong for a report whose unit is "one row per distinct
    configuration", where the same result printed three times under three
    version strings is noise. Both views coexist: `N` still counts the
    re-runs, this table does not re-print them.
    """
    return json.dumps(
        [
            record.get("data_range") or {},
            [
                [
                    (fold.get("metrics") or {}).get("sharpe_ratio"),
                    (fold.get("metrics") or {}).get("num_trades"),
                    (fold.get("metrics") or {}).get("total_return"),
                ]
                for fold in (record.get("fold_results") or [])
            ],
        ],
        sort_keys=True,
        default=str,
    )


def _is_multi_fold_backtest(record: Mapping[str, Any]) -> bool:
    return record.get("record_type") == "backtest_run" and len(record.get("fold_results") or []) > 1


def distinct_multi_fold_runs(records: Iterable[Mapping[str, Any]]) -> list[DistinctRun]:
    """Every distinct multi-fold walk-forward run in `records`, in log
    order, with reproductions folded into `duplicate_run_ids`.

    Single-fold records are excluded: they are grid candidates or
    parameter-sensitivity probes, not runs that can carry a verdict.
    `holdout_access` records are ignored entirely, same as
    `research/overfitting_check.py`.

    Family attribution goes through `research.lineage.resolve_family`, so a
    renamed `strategy_id` does not split one research thread across two
    rows, and the fingerprint is family-scoped so two families cannot merge
    on a coincidence.
    """
    # Accumulated in plain mutable scratch state first, so the frozen
    # `DistinctRun`s are built once, complete, rather than mutating a list
    # held by an already-"frozen" object (which Python permits but which
    # reads as a contradiction).
    grouped: dict[tuple[str, str], tuple[FamilyResolution, dict, list[str]]] = {}

    for record in records:
        if not _is_multi_fold_backtest(record):
            continue
        resolution = resolve_family(record.get("strategy_id", ""), record)
        key = (resolution.family, _fold_fingerprint(record))
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = (resolution, dict(record), [])
        else:
            existing[2].append(record.get("run_id", ""))

    return [
        DistinctRun(
            family=resolution.family,
            purpose=resolution.purpose,
            record=record,
            duplicate_run_ids=duplicates,
        )
        for resolution, record, duplicates in grouped.values()
    ]


# ---------------------------------------------------------------------------
# One trial Sharpe per counted selection trial (the input to V_hat)
# ---------------------------------------------------------------------------


def _record_sharpe(record: Mapping[str, Any]) -> float | None:
    """The one Sharpe a record reports: its aggregate mean fold Sharpe when
    it has one, else its single fold's own Sharpe (the shape every grid
    candidate takes).
    """
    aggregate = (record.get("aggregate_metrics") or {}).get("mean_sharpe")
    if aggregate is not None:
        return float(aggregate)
    folds = record.get("fold_results") or []
    if len(folds) == 1:
        sharpe = (folds[0].get("metrics") or {}).get("sharpe_ratio")
        return None if sharpe is None else float(sharpe)
    return None


def trial_sharpe_ratios(records: Iterable[Mapping[str, Any]]) -> dict[str, list[float | None]]:
    """One **annualized** Sharpe per counted selection trial, grouped by
    family -- the empirical input to `V_hat`.

    Bailey's `V_hat` is "the variance across the `N` trials of the estimated
    Sharpe", so the trial set must be the *same* set `N` counts. It is,
    exactly:

    - A grid-search group logs one record per (fold, candidate) pair, and
      `sr-p` counts the group once with weight `total_candidates`. So the
      trial unit is the **candidate**, and its Sharpe is the mean of that
      candidate's own per-fold Sharpes -- one value per `(parent_run_id,
      candidate_index)` pair.
    - A standalone multi-fold walk-forward run is one trial of weight 1, and
      its Sharpe is its aggregate mean fold Sharpe.
    - `TrialKind.SENSITIVITY_PROBE` records are excluded, exactly as they
      are from `N`.

    Verified against the real log: this yields 97 / 8 / 8 / 4 / 19 values for
    `trend-momentum` / `mean-reversion` / `funding` / `volume` /
    `infrastructure`, reconciling **exactly** with `sr-p`'s
    `selection_trials` for every family. `build_retrospective` re-checks
    that reconciliation at run time and records a note if it ever breaks
    (it would mean a grid logged fewer candidates than it claimed).

    A trial with no defined Sharpe is reported as `None` rather than dropped,
    so the returned list length stays equal to the trial count;
    `sharpe_variance_across_trials` excludes them from the variance itself.

    Values are annualized, as logged. De-annualize before feeding them to
    PSR/DSR -- `build_retrospective` does so on the daily scale, see the
    module docstring.
    """
    grid: dict[str, dict[tuple[str, Any], list[float | None]]] = {}
    standalone: dict[str, list[float | None]] = {}

    for record in records:
        if record.get("record_type") != "backtest_run":
            continue
        if classify_trial_kind(record) is TrialKind.SENSITIVITY_PROBE:
            continue

        family = resolve_family(record.get("strategy_id", ""), record).family
        parent_run_id = record.get("parent_run_id")
        sharpe = _record_sharpe(record)
        if parent_run_id is not None and not str(parent_run_id).startswith(SENSITIVITY_PARENT_RUN_ID_PREFIX):
            key = (str(parent_run_id), record.get("candidate_index"))
            grid.setdefault(family, {}).setdefault(key, []).append(sharpe)
        else:
            standalone.setdefault(family, []).append(sharpe)

    by_family: dict[str, list[float | None]] = {}
    for family in sorted(set(grid) | set(standalone)):
        values: list[float | None] = []
        for candidate_sharpes in grid.get(family, {}).values():
            defined = [s for s in candidate_sharpes if s is not None]
            values.append(statistics.fmean(defined) if defined else None)
        values.extend(standalone.get(family, []))
        by_family[family] = values
    return by_family


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictResult:
    """A verdict and the detection floor it was reached under -- one object,
    never separable. `reason` states the specific arithmetic, so a row is
    self-explanatory without re-deriving it from the other columns.
    """

    verdict: str
    reason: str
    detection_floor: float | None

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason, "detection_floor": self.detection_floor}


def assign_verdict(
    *,
    dsr_project: float | None,
    mean_fold_sharpe: float | None,
    total_trades: int | None,
    detection_floor: float | None,
    min_total_trades: int,
    plausible_max_true_sharpe: float,
    dsr_threshold: float = DEFAULT_DSR_THRESHOLD,
) -> VerdictResult:
    """Assign one of the four verdicts, in this deliberate order:

    1. **`INCONCLUSIVE-DATA-LIMITED`** -- fewer than `min_total_trades`
       closed trades (or no trade count / no point estimate at all).
    2. **`SURVIVES`** -- `dsr_project >= dsr_threshold`. Decided on the
       project-level `N`, per the module docstring's `N` choice.
    3. **`REJECTED`** -- below the DSR threshold with a non-positive point
       estimate. No edge shown, and the point estimate points the wrong way.
    4. **`REJECTED-UNDERPOWERED`** -- below the DSR threshold with a
       *positive* point estimate, but `detection_floor >
       plausible_max_true_sharpe`: the study could not have detected a
       plausible edge even if one existed. "Not shown", explicitly not
       "shown absent".

    **The trade-count floor is checked first, ahead of every other label
    including `SURVIVES`.** "This study cannot conclude" logically precedes
    both "shown" and "not shown", so a 14-trade run is recorded neither as a
    rejection nor as a survivor. This is a conscious strengthening of the
    plain reading "SURVIVES = DSR >= 0.95 at the project N": the direction
    that matters for a system that may eventually place real money is never
    to emit a promotion signal off a sample too thin to support one.

    The cost is real and is named rather than hidden: a *genuinely strong
    but legitimately low-frequency* strategy is parked in
    `INCONCLUSIVE-DATA-LIMITED` rather than promoted -- which is exactly the
    tension CLAUDE.md already flags on the 100-trade floor ("may unfairly
    penalize a legitimately low-frequency strategy"). The resolution is to
    fix the floor, not the precedence: see Gate 1 Proposal 3 in
    `.planning/sr-r-retrospective-closeout.md`, which proposes a
    frequency-appropriate floor. On the real log the ordering changes no
    row -- the only data-limited family's DSR is ~0 either way.

    The four-label vocabulary leaves one cell implicit -- below the DSR
    threshold, positive point estimate, and **adequately powered**
    (`detection_floor <= plausible_max_true_sharpe`). That is a genuine
    rejection, not an underpowered one, so it returns `REJECTED` with a
    `reason` that says which of the two rejection routes it took. It does
    not arise on this project's real log (every window's floor is above any
    plausible edge) but must not silently fall through.

    An **undefined** `detection_floor` never yields the adequately-powered
    reading: an unmeasurable floor is not evidence of adequate power.

    `dsr_project is None` is treated as below the threshold -- `None` means
    "no evidence" throughout this project's statistics, and no evidence
    cannot clear a bar.
    """
    if total_trades is None or total_trades < min_total_trades or mean_fold_sharpe is None:
        return VerdictResult(
            verdict=VERDICT_INCONCLUSIVE_DATA_LIMITED,
            reason=(
                f"{total_trades if total_trades is not None else 'no'} closed trade(s) against a floor of "
                f"{min_total_trades}: too few for any conclusion in either direction. This says nothing "
                "about the signal -- only that this run cannot settle it."
            ),
            detection_floor=detection_floor,
        )

    if dsr_project is not None and dsr_project >= dsr_threshold:
        return VerdictResult(
            verdict=VERDICT_SURVIVES,
            reason=(
                f"DSR at the project-level N is {dsr_project:.4g} >= {dsr_threshold} on "
                f"{total_trades} closed trade(s): the result is not explainable as the best of the "
                "project's whole search."
            ),
            detection_floor=detection_floor,
        )

    dsr_text = f"{dsr_project:.4g}" if dsr_project is not None else "undefined"
    if mean_fold_sharpe <= 0:
        return VerdictResult(
            verdict=VERDICT_REJECTED,
            reason=(
                f"DSR at the project-level N is {dsr_text} < {dsr_threshold} and the point estimate "
                f"({mean_fold_sharpe:+.4f} annualized) points the wrong way."
            ),
            detection_floor=detection_floor,
        )

    if detection_floor is None or detection_floor > plausible_max_true_sharpe:
        floor_text = "undefined" if detection_floor is None else f"{detection_floor:.2f}"
        return VerdictResult(
            verdict=VERDICT_REJECTED_UNDERPOWERED,
            reason=(
                f"DSR at the project-level N is {dsr_text} < {dsr_threshold} on a {mean_fold_sharpe:+.4f} "
                f"annualized point estimate, but this study's detection floor is {floor_text} -- above any "
                f"plausible true edge ({plausible_max_true_sharpe:.2f}). NOT SHOWN, not shown absent."
            ),
            detection_floor=detection_floor,
        )

    return VerdictResult(
        verdict=VERDICT_REJECTED,
        reason=(
            f"DSR at the project-level N is {dsr_text} < {dsr_threshold} on a {mean_fold_sharpe:+.4f} annualized "
            f"point estimate, in a study adequately powered for a plausible edge (detection floor "
            f"{detection_floor:.2f} <= {plausible_max_true_sharpe:.2f}): searched enough, still nothing."
        ),
        detection_floor=detection_floor,
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictRow:
    """One distinct configuration's full close-out record.

    `detection_floor` is not optional garnish: see the module docstring's
    "honesty requirement". `to_dict()` always emits it, and
    `render_markdown_table` always gives it a column.
    """

    family: str
    purpose: str
    strategy_id: str
    strategy_version: str
    run_id: str
    duplicate_run_ids: list[str]
    fold_count: int
    total_trades: int | None
    mean_fold_sharpe: float | None
    fold_consistency: float | None
    fold_consistency_passed: bool
    sign_test_p: float | None
    t_test_p: float | None
    psr_n1: float | None
    # `None` when no `sr-p` selection-trial count could be established for
    # this row's family/purpose -- see `build_retrospective`. Never 0.
    family_n: int | None
    project_n: int | None
    dsr_family: float | None
    dsr_project: float | None
    bars_per_day: int | None
    num_observations: int
    data_span_years: float | None
    validated_span_years: float | None
    detection_floor: float | None
    detection_floor_validated_span: float | None
    moments_source: str
    verdict: str
    verdict_reason: str

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "purpose": self.purpose,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "run_id": self.run_id,
            "duplicate_run_ids": list(self.duplicate_run_ids),
            "fold_count": self.fold_count,
            "total_trades": self.total_trades,
            "mean_fold_sharpe": self.mean_fold_sharpe,
            "fold_consistency": self.fold_consistency,
            "fold_consistency_passed": self.fold_consistency_passed,
            "sign_test_p": self.sign_test_p,
            "t_test_p": self.t_test_p,
            "psr_n1": self.psr_n1,
            "family_n": self.family_n,
            "project_n": self.project_n,
            "dsr_family": self.dsr_family,
            "dsr_project": self.dsr_project,
            "bars_per_day": self.bars_per_day,
            "num_observations": self.num_observations,
            "data_span_years": self.data_span_years,
            "validated_span_years": self.validated_span_years,
            "detection_floor": self.detection_floor,
            "detection_floor_validated_span": self.detection_floor_validated_span,
            "moments_source": self.moments_source,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
        }


@dataclass(frozen=True)
class RetrospectiveReport:
    rows: list[VerdictRow]
    total_multi_fold_runs: int
    distinct_configurations: int
    research_n: int
    infrastructure_n: int
    min_fold_consistency: Decimal
    min_total_trades: int
    plausible_max_true_sharpe: float
    dsr_threshold: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "total_multi_fold_runs": self.total_multi_fold_runs,
            "distinct_configurations": self.distinct_configurations,
            "research_n": self.research_n,
            "infrastructure_n": self.infrastructure_n,
            "min_fold_consistency": str(self.min_fold_consistency),
            "min_total_trades": self.min_total_trades,
            "plausible_max_true_sharpe": self.plausible_max_true_sharpe,
            "dsr_threshold": self.dsr_threshold,
            "notes": list(self.notes),
        }


def _fold_sharpe_values(record: Mapping[str, Any]) -> list[float | None]:
    return [(fold.get("metrics") or {}).get("sharpe_ratio") for fold in (record.get("fold_results") or [])]


def _validated_bars(record: Mapping[str, Any]) -> int:
    """Total out-of-sample bars behind the reported Sharpe: the sum of the
    folds' own validate windows.

    Taken from the fold indices rather than
    `walk_forward_config.validate_bars * fold_count` because the indices are
    what the run actually evaluated, and they are present on every record
    including any whose config fields differ. The two agree on this
    project's real log (19 x 720 = 13,680 for the 1h runs).
    """
    total = 0
    for fold in record.get("fold_results") or []:
        start = fold.get("validate_start_index")
        end = fold.get("validate_end_index")
        if isinstance(start, int) and isinstance(end, int) and end > start:
            total += end - start
    if total:
        return total
    config = record.get("walk_forward_config") or {}
    validate_bars = config.get("validate_bars")
    fold_count = len(record.get("fold_results") or [])
    return validate_bars * fold_count if isinstance(validate_bars, int) else 0


def _daily(annualized: float | None, bars_per_day: int) -> float | None:
    if annualized is None:
        return None
    return deannualize_sharpe(float(annualized), bars_per_day=bars_per_day, sampling=SAMPLING_DAILY)


def build_retrospective(
    *,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
    min_fold_consistency: Decimal,
    min_total_trades: int = DEFAULT_MIN_TOTAL_TRADES,
    plausible_max_true_sharpe: float = DEFAULT_PLAUSIBLE_MAX_TRUE_SHARPE,
    dsr_threshold: float = DEFAULT_DSR_THRESHOLD,
    significance_alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
) -> RetrospectiveReport:
    """Read `runs_path` (read-only) and produce one `VerdictRow` per distinct
    multi-fold configuration.

    `min_fold_consistency` is REQUIRED with no default, matching
    `research/eligibility.py`'s deliberate refusal to pick a point value
    inside CLAUDE.md's human-approved 80-90% range on a caller's behalf. It
    affects only the reported `fold_consistency_passed` flag; the verdict
    itself is DSR-driven and does not consult it.

    Every trial Sharpe, and every row's own Sharpe, is de-annualized to the
    **daily** scale before reaching PSR/DSR -- see the module docstring for
    the two reasons.
    """
    records = list(experiment_log.read_records(runs_path))
    runs = distinct_multi_fold_runs(records)
    trials = trial_sharpe_ratios(records)
    project = check_project_combination_count(runs_path)

    notes: list[str] = []
    for family, values in sorted(trials.items()):
        family_result = project.families.get(family)
        if family_result is None:
            notes.append(f"family={family!r}: trial Sharpes collected but no sr-p family result to reconcile against")
        elif len(values) != family_result.selection_trials:
            notes.append(
                f"family={family!r}: {len(values)} trial Sharpe(s) recovered but sr-p counts "
                f"{family_result.selection_trials} selection trial(s) -- V_hat is computed from the "
                "recovered set while N stays sr-p's, so the two describe slightly different trial pools"
            )

    n_by_purpose = {
        "research": project.research_selection_trials,
        "infrastructure": project.infrastructure_selection_trials,
    }
    pooled_by_purpose: dict[str, list[float | None]] = {}
    for family, values in trials.items():
        family_result = project.families.get(family)
        purpose = family_result.purpose if family_result is not None else "research"
        pooled_by_purpose.setdefault(purpose, []).extend(values)

    rows: list[VerdictRow] = []
    for run in runs:
        record = run.record
        fold_sharpes = _fold_sharpe_values(record)
        aggregate = record.get("aggregate_metrics") or {}
        mean_sharpe = aggregate.get("mean_sharpe")
        total_trades = aggregate.get("total_trades")

        consistency = evaluate_fold_consistency(fold_sharpes, min_fraction=min_fold_consistency)
        sign_test = evaluate_sign_test(fold_sharpes, alpha=significance_alpha)
        t_test = evaluate_mean_sharpe_significance(fold_sharpes, alpha=significance_alpha)

        bars_per_day = infer_bars_per_day(record)
        data_range = record.get("data_range") or {}
        start_ms = data_range.get("start_ms")
        end_ms = data_range.get("end_ms")
        data_span_years = (
            (end_ms - start_ms) / _MS_PER_YEAR if start_ms is not None and end_ms is not None and end_ms > start_ms
            else None
        )

        validated_bars = _validated_bars(record)
        validated_span_years = validated_bars / (bars_per_day * 365) if bars_per_day else None
        num_observations = validated_bars // bars_per_day if bars_per_day else 0

        # `None`, never a fabricated 0, when a trial count cannot be
        # established -- the module's own "no evidence, not zero evidence"
        # convention. A literal `N=0` does in fact propagate harmlessly
        # (`deflated_sharpe_benchmark` returns `None` below 1, so `dsr`
        # would be `None` anyway and `SURVIVES` is unreachable), but it
        # would do so *silently*: a row would lose its DSR with nothing in
        # `notes` saying why. That matters here specifically because
        # `resolve_family` deliberately returns an unmapped `strategy_id`
        # as its own single-member family, so a family absent from `sr-p`'s
        # results is a real, documented possibility rather than a
        # theoretical one. (CodeRabbit review finding on PR #53.)
        family_result = project.families.get(run.family)
        family_n = family_result.selection_trials if family_result is not None else None
        project_n = n_by_purpose.get(run.purpose)
        if family_n is None or project_n is None:
            notes.append(
                f"run_id={run.run_id!r} family={run.family!r} purpose={run.purpose!r}: no sr-p selection-trial "
                "count available -- DSR left undefined rather than deflated against a fabricated N"
            )

        psr_n1 = None
        dsr_family = None
        dsr_project = None
        moments_source = "normal_assumption"
        if bars_per_day:
            daily_sharpe = _daily(mean_sharpe, bars_per_day)
            psr = evaluate_psr(
                sharpe_ratio=daily_sharpe, num_observations=num_observations, sampling=SAMPLING_DAILY
            )
            psr_n1 = psr.psr
            moments_source = psr.moments_source
            if family_n is not None:
                dsr_family = evaluate_deflated_sharpe(
                    sharpe_ratio=daily_sharpe,
                    num_observations=num_observations,
                    num_trials=family_n,
                    trial_sharpe_variance=sharpe_variance_across_trials(
                        [_daily(value, bars_per_day) for value in trials.get(run.family, [])]
                    ),
                    sampling=SAMPLING_DAILY,
                ).dsr
            if project_n is not None:
                dsr_project = evaluate_deflated_sharpe(
                    sharpe_ratio=daily_sharpe,
                    num_observations=num_observations,
                    num_trials=project_n,
                    trial_sharpe_variance=sharpe_variance_across_trials(
                        [_daily(value, bars_per_day) for value in pooled_by_purpose.get(run.purpose, [])]
                    ),
                    sampling=SAMPLING_DAILY,
                ).dsr

        floor = detection_floor_sharpe(data_span_years, alpha=significance_alpha) if data_span_years else None
        floor_validated = (
            detection_floor_sharpe(validated_span_years, alpha=significance_alpha) if validated_span_years else None
        )
        verdict = assign_verdict(
            dsr_project=dsr_project,
            mean_fold_sharpe=mean_sharpe,
            total_trades=total_trades,
            detection_floor=floor,
            min_total_trades=min_total_trades,
            plausible_max_true_sharpe=plausible_max_true_sharpe,
            dsr_threshold=dsr_threshold,
        )

        rows.append(
            VerdictRow(
                family=run.family,
                purpose=run.purpose,
                strategy_id=record.get("strategy_id", ""),
                strategy_version=record.get("strategy_version", ""),
                run_id=run.run_id,
                duplicate_run_ids=list(run.duplicate_run_ids),
                fold_count=len(fold_sharpes),
                total_trades=total_trades,
                mean_fold_sharpe=mean_sharpe,
                fold_consistency=consistency.fraction_positive,
                fold_consistency_passed=consistency.passed,
                sign_test_p=sign_test.p_value,
                t_test_p=t_test.p_value,
                psr_n1=psr_n1,
                family_n=family_n,
                project_n=project_n,
                dsr_family=dsr_family,
                dsr_project=dsr_project,
                bars_per_day=bars_per_day,
                num_observations=num_observations,
                data_span_years=data_span_years,
                validated_span_years=validated_span_years,
                detection_floor=verdict.detection_floor,
                detection_floor_validated_span=floor_validated,
                moments_source=moments_source,
                verdict=verdict.verdict,
                verdict_reason=verdict.reason,
            )
        )

    return RetrospectiveReport(
        rows=rows,
        total_multi_fold_runs=sum(1 for record in records if _is_multi_fold_backtest(record)),
        distinct_configurations=len(rows),
        research_n=project.research_selection_trials,
        infrastructure_n=project.infrastructure_selection_trials,
        min_fold_consistency=min_fold_consistency,
        min_total_trades=min_total_trades,
        plausible_max_true_sharpe=plausible_max_true_sharpe,
        dsr_threshold=dsr_threshold,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Fixed, non-negotiable column order. "Detection floor" sits immediately
# before "verdict" so the two are read together -- see the module
# docstring's honesty requirement. There is deliberately no option to omit
# a column.
_COLUMNS = (
    "family",
    "strategy_id",
    "version",
    "run_id",
    "folds",
    "mean Sharpe",
    "fold consistency",
    "sign-test p",
    "t-test p",
    "PSR(N=1)",
    "family N",
    "project N",
    "DSR(family N)",
    "DSR(project N)",
    "detection floor",
    "verdict",
)

_RUN_ID_PREFIX_LENGTH = 8


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _row_cells(row: VerdictRow) -> list[str]:
    return [
        row.family,
        row.strategy_id,
        row.strategy_version,
        # Truncated for width; `VerdictRow.run_id`/`to_dict()` keep the full
        # value, and the prefixes are unique across this project's log.
        row.run_id[:_RUN_ID_PREFIX_LENGTH],
        str(row.fold_count),
        _fmt(row.mean_fold_sharpe, "+.4f"),
        "n/a" if row.fold_consistency is None else f"{row.fold_consistency:.1%}",
        _fmt(row.sign_test_p, ".3f"),
        _fmt(row.t_test_p, ".3f"),
        _fmt(row.psr_n1, ".4f"),
        "n/a" if row.family_n is None else str(row.family_n),
        "n/a" if row.project_n is None else str(row.project_n),
        _fmt(row.dsr_family, ".3g"),
        _fmt(row.dsr_project, ".3g"),
        _fmt(row.detection_floor, ".2f"),
        row.verdict,
    ]


def render_markdown_table(report: RetrospectiveReport) -> str:
    """The verdict table as GitHub-flavoured Markdown.

    The detection-floor column is structural, not optional: it is in
    `_COLUMNS`, every row emits it, and no parameter can turn it off. A
    reader cannot obtain a verdict from this function without the floor it
    was reached under.
    """
    lines = [
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "|".join(["---"] * len(_COLUMNS)) + "|",
    ]
    lines.extend("| " + " | ".join(_row_cells(row)) + " |" for row in report.rows)
    return "\n".join(lines)


def _fraction_argument(text: str) -> Decimal:
    """`argparse` `type=` converter for `--min-fold-consistency`.

    Deliberately **not** a bare `type=Decimal`: `argparse` only converts an
    exception into a clean usage error when it is a `TypeError` or a
    `ValueError`, and `decimal.InvalidOperation` is an `ArithmeticError` --
    so `--min-fold-consistency abc` under `type=Decimal` still escapes as a
    raw traceback. Verified, not assumed. (CodeRabbit review finding on
    PR #53; the finding was right, its suggested fix was not.)

    Range is `(0, 1]`, matching `eligibility.evaluate_fold_consistency`'s
    own contract. CLAUDE.md's approved 80-90% band is deliberately NOT
    enforced here: this is a retrospective diagnostic, not the Eligibility
    Bar, and a reviewer must stay free to ask what the table looks like at
    another floor.
    """
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a valid decimal: {text!r}") from exc
    # `is_finite()` first, and not folded into the comparison: `Decimal`
    # PARSES "NaN"/"sNaN" happily and then raises `InvalidOperation` from
    # the COMPARISON, which -- being an `ArithmeticError` -- escapes
    # `argparse` as a raw traceback just like the parse failure above.
    # Verified, not assumed. (CodeRabbit review finding on PR #53, second
    # pass.) `Infinity` needs no special case, but is covered by the same
    # check rather than relying on the comparison to reject it.
    if not value.is_finite() or not 0 < value <= 1:
        raise argparse.ArgumentTypeError(f"must be a finite value in (0, 1], got {text!r}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strategy Research Task R: re-judge every distinct multi-fold run in the experiment log under "
            "sr-p's honest trial count and sr-q's Deflated Sharpe Ratio. Read-only."
        )
    )
    parser.add_argument("--runs-path", default=experiment_log.DEFAULT_RUNS_PATH)
    parser.add_argument(
        "--min-fold-consistency",
        type=_fraction_argument,
        default=Decimal("0.80"),
        help="CLAUDE.md's approved range is 0.80-0.90; affects the reported pass flag only, never the verdict",
    )
    parser.add_argument("--min-total-trades", type=int, default=DEFAULT_MIN_TOTAL_TRADES)
    parser.add_argument("--plausible-max-true-sharpe", type=float, default=DEFAULT_PLAUSIBLE_MAX_TRUE_SHARPE)
    parser.add_argument("--dsr-threshold", type=float, default=DEFAULT_DSR_THRESHOLD)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = build_retrospective(
        runs_path=args.runs_path,
        min_fold_consistency=args.min_fold_consistency,
        min_total_trades=args.min_total_trades,
        plausible_max_true_sharpe=args.plausible_max_true_sharpe,
        dsr_threshold=args.dsr_threshold,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(render_markdown_table(report))
        print()
        for note in report.notes:
            print(f"NOTE: {note}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
