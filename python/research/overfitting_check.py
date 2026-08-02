"""MinBTL-style combination-count-vs-data-span overfitting warning.

See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-g-overfitting-safeguards.md` ("Finding 1") for the full
research finding this module acts on: the commonly-cited "30 trades per
parameter" rule has no rigorous origin. The actual rigorous framework --
Bailey, Borwein, Lopez de Prado, Zhu, "The Probability of Backtest
Overfitting" (2016, *Journal of Computational Finance*), and their related
Minimum Backtest Length (MinBTL) concept -- relates a **different pair of
quantities**: the number of independent parameter combinations tried (N)
versus how much historical data (in years) is actually available. More
combinations tried against less data is the red flag, regardless of how
good any single combination looks in isolation.

**This module is a documented, defensible approximation of that framework's
spirit -- it is explicitly NOT a reproduction of Bailey et al.'s actual
statistical test (which involves the cross-validated distribution of
in-sample-vs-out-of-sample rank correlations, not a simple ratio).**
Reproducing the real test rigorously requires machinery (CSCV -- Combinatorially
Symmetric Cross-Validation -- and the resulting Probability of Backtest
Overfitting estimate) that CLAUDE.md's Strategy Research Methodology
explicitly assessed as too heavy for this project's current stage (single
symbol, a handful of strategy families) and deferred to whenever Priority
#9 (auto-retraining) increases the real scale of hyperparameter search.
What's implemented here instead: a simple, transparent
combinations-tried-per-year-of-data ratio, tiered into `"low"`/
`"moderate"`/`"high"` risk bands with `.planning/sr-g-overfitting-
safeguards.md`-documented (not rigorously derived) boundaries. This is a
**warning, not a hard block** -- matching this project's existing
established pattern (see `research/holdout.py`'s re-access override): a
solo researcher may have a legitimate reason to run more combinations than
this heuristic likes, and the tool's job is to make that visible, not to
silently prevent it.

`check_combination_count` reads only `runs/experiments.jsonl` (via
`research.experiment_log.read_records`) -- it does not query the sqlite
kline store directly. This means "data span available" here means "the
widest date range this `strategy_id`'s own logged runs have actually
touched so far" (derived from every matching record's `data_range` field),
not an independently-queried true BingX retention depth for the symbol/
interval. In practice these are usually close (a strategy's walk-forward
runs are typically built from the full `load_research_klines()` result),
but they are not guaranteed identical -- e.g. a strategy that has only
ever been run against a deliberately narrow slice of the available history
would under-report its true "available" depth here. Documented as a
known, accepted limitation rather than silently assumed away; see the
planning doc for the alternative (querying `data/store.py` directly) that
was considered and not built, to keep this module a pure function of the
experiment log alone.

## Strategy Research Task P: family-level and project-level accounting

`check_combination_count` above is kept **byte-for-byte behaviourally
unchanged** (it has tests and callers). Everything added below is strictly
additive, and fixes three defects that made its number unusable as the `N`
of a Deflated Sharpe Ratio -- full evidence in `.planning/sr-p-trial-
accounting.md`, summarized here:

1. **Renaming a strategy laundered its data-snooping history.** Counting
   keyed on the bare `strategy_id` meant `ensemble-momentum-configuration-c`
   reported 10 combinations / `"low"` risk while being a direct descendant
   of `ensemble-momentum`'s 234-combination `"high"`-risk search.
   `check_project_combination_count` counts per research *family*
   (`research.lineage`) instead.
2. **Parameter-sensitivity probes were miscounted as selection trials.**
   382 of the real log's 1,839 `backtest_run` records are
   `check_parameter_sensitivity` evaluations of an *already-selected*
   winner's neighbours. They never influenced which candidate was chosen,
   so under Bailey et al.'s framework they are not trials at all. sr-g
   considered excluding them and chose not to ("flagging risk earlier and
   more conservatively") -- defensible for a warning heuristic, not
   defensible as the `N` of a statistic where `N` has a precise meaning.
   They are now classified `TrialKind.SENSITIVITY_PROBE`, excluded from
   `N`, and reported separately rather than discarded.
3. **The count was sensitive to a wiring detail, not to research
   activity.** `ma-crossover-task-g-sensitivity-demo` scored 6 and its
   `-v2` re-run scored 33 for *identical* work, purely because the
   sensitivity `fit()` calls were re-wired from
   `parent_run_id=<enclosing run_id>` to `parent_run_id=None` between the
   two. Both now score the same.

`TrialKind` classification replaces the old flat "+1 per record":

- `SELECTION` -- a real selection trial: has a genuine `parent_run_id`
  (a grid-search candidate) or is a multi-fold standalone walk-forward run.
  These are what `N` counts.
- `SENSITIVITY_PROBE` -- excluded from `N`, reported separately.
- `REPRODUCTION` -- a re-run of an already-counted configuration. **Still
  counted in `N` by default and reported separately, never silently merged
  away** (see `FamilyOverfittingCheckResult.deduplicated_selection_trials`
  for the deduplicated view a reviewer can choose instead).

**Which `N` to report: both, always.** Bailey's `N` is "the number of
trials from which this maximum was selected". Within `trend-momentum` this
project compared 97 momentum variants against each other -- but it also
chose momentum over mean-reversion, over the blend, over volume, over
funding, *after seeing all of their results*. So the honest `N` for "the
best thing this project found" is the project-level research total, not
any single family's.

## Strategy Research Task V: holdout confirmations excluded from `N`

One narrow, provably-safe amendment to the "byte-for-byte behaviourally
unchanged" claim above: `_scan_records`/`_scan_project_records` now also
skip any record related to a logged holdout confirmation -- both the
outer `is_holdout_run=True` standalone record and any nested `fit()`
diagnostic sub-record naming it as `parent_run_id`, via the two-pass
`_holdout_run_ids`/`_is_holdout_related` helpers (a single forward pass
cannot exclude the nested record, because it is written to the log
*before* its own parent, and its own `is_holdout_run` field is not
reliable -- see `_holdout_run_ids`'s docstring for the concrete case that
motivated this).

This was a real, previously-latent gap: before this fix, `is_holdout_run`
was never referenced anywhere in this module, so a holdout confirmation's
records -- which were never searched over and must never inflate a
selection-trial `N` -- would have been counted exactly like a genuine
research trial the moment `check_project_combination_count` (or the older
`check_combination_count`) was next called. Found and fixed the same day
it was first exercisable: a direct scan of the real, complete
`runs/experiments.jsonl` at the time of this fix found exactly **one**
`is_holdout_run=True` record in the project's entire history (the `sr-v`
holdout confirmation itself), so this change is provably a no-op against
every prior computed result in this project, including `sr-r`'s
117-trial/DSR-2.0e-05 close-out. See
`.planning/sr-v-preregistered-attempt-result.md` for the full
investigation.
"""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from research import experiment_log
from research.lineage import INFRASTRUCTURE_PURPOSE, RESEARCH_PURPOSE, FamilyResolution, resolve_family

# 365, not 365.25 -- matches this project's existing fixed annualization
# convention (`metrics/metrics.py`'s `_DAYS_PER_YEAR`, CLAUDE.md's
# "Strategy Research Operational Design"), not open to per-call judgment.
_MS_PER_YEAR = 365 * 24 * 3600 * 1000

# Tier boundaries for "combinations tried per year of data available".
# **Explicitly a documented heuristic, not a rigorously-derived
# threshold** -- see this module's docstring and `.planning/sr-g-
# overfitting-safeguards.md` for why no attempt was made to derive an
# exact numeric formula from Bailey et al.'s actual MinBTL statistic.
# Chosen to be conservative (flag risk earlier rather than later) given
# this project's own thin real data depth (~0.57-2.24 years depending on
# timeframe, per `.planning/sr-c-walkforward-holdout.md` /
# `.planning/sr-f-risk-management-and-1h-variant.md`) and its still-small
# strategy family count. Revisit numerically -- or replace with full
# CSCV/PBO instead of trying to sharpen this heuristic further -- if/when
# Priority #9's larger-scale search makes this matter more.
_LOW_RISK_MAX_COMBINATIONS_PER_YEAR = 10.0
_MODERATE_RISK_MAX_COMBINATIONS_PER_YEAR = 30.0


@dataclass(frozen=True)
class OverfittingCheckResult:
    """`check_combination_count`'s full result. `notes` carries any
    defensive/fallback handling that occurred while aggregating the log
    (e.g. an inconsistent `total_candidates` within one `parent_run_id`
    group, or a group with no `total_candidates` reported at all) -- never
    silent, always visible to a caller/reviewer even though none of it
    blocks the result from being computed.
    """

    strategy_id: str
    total_combinations_tried: int
    parent_run_groups: dict[str, int]
    standalone_run_count: int
    data_span_start_ms: int | None
    data_span_end_ms: int | None
    data_span_years: float | None
    combinations_per_year: float | None
    risk_level: str  # "unknown" | "low" | "moderate" | "high"
    warning: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "total_combinations_tried": self.total_combinations_tried,
            "parent_run_groups": self.parent_run_groups,
            "standalone_run_count": self.standalone_run_count,
            "data_span_start_ms": self.data_span_start_ms,
            "data_span_end_ms": self.data_span_end_ms,
            "data_span_years": self.data_span_years,
            "combinations_per_year": self.combinations_per_year,
            "risk_level": self.risk_level,
            "warning": self.warning,
            "notes": list(self.notes),
        }


def _holdout_run_ids(runs_path: str | Path) -> set[str]:
    """Every `run_id` of a `backtest_run` record logged with
    `is_holdout_run=True` (Strategy Research Task V).

    A **separate pass**, not inlined into the main scan below, because a
    holdout confirmation's own nested `fit()` diagnostic sub-record is
    written to the log BEFORE its parent (the parent's own `log_run` call
    happens only after `fit()` returns) -- so a single forward pass cannot
    yet know, at the moment it encounters a child record, whether that
    child's `parent_run_id` will turn out to name a holdout run. Collecting
    the full set up front lets `_scan_records`/`_scan_project_records`
    exclude a holdout run's records -- both the outer standalone record
    itself and any nested sub-record naming it as `parent_run_id` --
    regardless of write order, and regardless of whether the nested
    sub-record's OWN `is_holdout_run` field happens to be accurate.

    That last clause is not hypothetical: it is the real, concrete case
    this exists to fix. `DailyTsmomEnsembleTrainable.fit()`'s in-sample-
    scoring sub-record was logged with `is_holdout_run=False` even though
    the data it actually scored was the real `1d` holdout window --
    matching every sibling `Trainable.fit()`'s pre-existing convention of
    never knowing (or asking) whether the data it was handed came from a
    holdout split. See `.planning/sr-v-preregistered-attempt-result.md`
    for the full investigation: before this fix, this module never
    referenced `is_holdout_run` at all, so neither that mislabeled child
    record nor its own correctly-labeled parent were excluded from
    selection-trial counting -- verified to be a real, previously-latent
    gap (a direct scan of the complete historical log found exactly one
    `is_holdout_run=True` record ever logged before this fix existed, so
    the exclusion below is provably a no-op against every prior computed
    result).
    """
    ids: set[str] = set()
    for record in experiment_log.read_records(runs_path):
        if record.get("record_type") != "backtest_run":
            continue
        if record.get("is_holdout_run") is True:
            run_id = record.get("run_id")
            if run_id is not None:
                ids.add(run_id)
    return ids


def _is_holdout_related(record: Mapping[str, Any], holdout_run_ids: set[str]) -> bool:
    """`True` for a record that is itself a logged holdout confirmation, or
    a nested sub-record of one (its `parent_run_id` names a holdout run).
    See `_holdout_run_ids`'s docstring for why both cases matter and why
    this can't be decided from one record in isolation.
    """
    if record.get("is_holdout_run") is True:
        return True
    parent_run_id = record.get("parent_run_id")
    return parent_run_id is not None and parent_run_id in holdout_run_ids


def _scan_records(
    strategy_id: str,
    runs_path: str | Path,
) -> tuple[dict[str, list[int | None]], int, int | None, int | None]:
    """First pass over `runs_path`: bucket every matching `backtest_run`
    record's `total_candidates` by `parent_run_id` (raw, not yet
    resolved to one count per group -- see `_resolve_parent_groups`),
    count standalone (`parent_run_id is None`) records, and track the
    widest `[start_ms, end_ms]` span observed across every matching
    record's `data_range`. `record_type: "holdout_access"` entries,
    records for a different `strategy_id`, and any record related to a
    logged holdout confirmation (see `_holdout_run_ids`/
    `_is_holdout_related`, Strategy Research Task V) are skipped entirely
    -- a holdout confirmation was never searched over, so it is not a
    selection trial and must not be counted as one.
    """
    holdout_run_ids = _holdout_run_ids(runs_path)
    parent_totals: dict[str, list[int | None]] = {}
    standalone_count = 0
    min_start_ms: int | None = None
    max_end_ms: int | None = None

    for record in experiment_log.read_records(runs_path):
        if record.get("record_type") != "backtest_run":
            continue
        if record.get("strategy_id") != strategy_id:
            continue
        if _is_holdout_related(record, holdout_run_ids):
            continue

        parent_run_id = record.get("parent_run_id")
        if parent_run_id is None:
            standalone_count += 1
        else:
            parent_totals.setdefault(parent_run_id, []).append(record.get("total_candidates"))

        data_range = record.get("data_range") or {}
        start_ms = data_range.get("start_ms")
        end_ms = data_range.get("end_ms")
        if start_ms is not None:
            min_start_ms = start_ms if min_start_ms is None else min(min_start_ms, start_ms)
        if end_ms is not None:
            max_end_ms = end_ms if max_end_ms is None else max(max_end_ms, end_ms)

    return parent_totals, standalone_count, min_start_ms, max_end_ms


def _resolve_parent_groups(
    parent_totals: dict[str, list[int | None]],
) -> tuple[dict[str, int], list[str]]:
    """Second pass: reduce each `parent_run_id`'s raw list of observed
    `total_candidates` values down to one count per group (see
    `check_combination_count`'s docstring for why one count per group,
    not one per record), defensively handling the two anomalous cases
    that can occur (inconsistent values within a group; no value at all)
    with a conservative fallback plus a human-readable note for each,
    rather than silently picking one or crashing.
    """
    notes: list[str] = []
    parent_run_groups: dict[str, int] = {}
    for parent_run_id, totals in parent_totals.items():
        non_null = [t for t in totals if t is not None]
        if non_null:
            distinct = sorted(set(non_null))
            chosen = distinct[-1]
            if len(distinct) > 1:
                notes.append(
                    f"parent_run_id={parent_run_id!r}: inconsistent total_candidates values "
                    f"{distinct} across its child records; using the max ({chosen}) conservatively"
                )
        else:
            chosen = len(totals)
            notes.append(
                f"parent_run_id={parent_run_id!r}: no total_candidates reported on any child "
                f"record; falling back to the observed record count ({chosen})"
            )
        parent_run_groups[parent_run_id] = chosen

    return parent_run_groups, notes


def _compute_risk_level(
    total_combinations: int,
    data_span_years: float | None,
) -> tuple[str, float | None]:
    """Third pass: the MinBTL-spirit combinations-per-year ratio and its
    tier (`"low"`/`"moderate"`/`"high"`), or `"unknown"` when no usable
    data span exists to divide by -- see this module's docstring and
    `_LOW_RISK_MAX_COMBINATIONS_PER_YEAR`/`_MODERATE_RISK_MAX_COMBINATIONS_PER_YEAR`
    for the (documented-heuristic, not rigorously-derived) tier boundaries.
    """
    if data_span_years is None or data_span_years <= 0:
        return "unknown", None

    combinations_per_year = total_combinations / data_span_years
    if combinations_per_year <= _LOW_RISK_MAX_COMBINATIONS_PER_YEAR:
        risk_level = "low"
    elif combinations_per_year <= _MODERATE_RISK_MAX_COMBINATIONS_PER_YEAR:
        risk_level = "moderate"
    else:
        risk_level = "high"
    return risk_level, combinations_per_year


def _build_warning(
    strategy_id: str,
    total_combinations: int,
    data_span_years: float | None,
    combinations_per_year: float | None,
    risk_level: str,
) -> str:
    if risk_level == "unknown":
        return (
            f"strategy_id={strategy_id!r}: {total_combinations} combination(s) tried, but no usable "
            "data-span could be computed from the logged records' data_range fields -- cannot assess "
            "overfitting risk from this heuristic"
        )
    return (
        f"strategy_id={strategy_id!r}: {total_combinations} independent parameter combination(s) "
        f"tried across {data_span_years:.2f} year(s) of logged data "
        f"({combinations_per_year:.1f}/year) -- MinBTL-spirit risk level: {risk_level.upper()}. "
        "This is a documented approximation, not Bailey et al.'s exact statistical test -- "
        "see research/overfitting_check.py's module docstring. A warning only, not a block: "
        "review before treating this strategy_id's best result as a genuine edge, but nothing "
        "here prevents further work."
    )


def check_combination_count(
    strategy_id: str,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
) -> OverfittingCheckResult:
    """Scan `runs_path` for every `record_type: "backtest_run"` entry
    belonging to `strategy_id` and compute the MinBTL-spirit
    combinations-tried-vs-data-years heuristic described in this module's
    docstring. Orchestrates three focused passes (`_scan_records`,
    `_resolve_parent_groups`, `_compute_risk_level`) plus warning-message
    formatting (`_build_warning`); see each helper's own docstring for
    what it's responsible for.

    Counting rule (CLAUDE.md's Strategy Research Operational Design /
    this task's own brief, followed literally): for each *distinct*
    `parent_run_id` observed among this `strategy_id`'s records, count
    `total_candidates` once (not once per child record) -- a walk-forward
    run's per-fold grid search logs one candidate record per (fold,
    candidate) pair, all sharing the same `parent_run_id` (the walk-
    forward run's own `run_id`), so summing `total_candidates` per
    *record* would overcount the same grid as "tried again" once per fold
    rather than recognizing it as the same N-candidate search repeated
    across folds. A `backtest_run` record with `parent_run_id is None`
    (a standalone run -- e.g. a direct `run_walk_forward` call not itself
    a candidate of an outer grid search) counts as exactly 1 combination.
    `record_type: "holdout_access"` entries are ignored entirely (not
    combinations-tried); so is any record related to a logged holdout
    confirmation (Strategy Research Task V, `_holdout_run_ids`/
    `_is_holdout_related`) -- a holdout confirmation was never searched
    over, so it is not a combination "tried" in the sense this heuristic
    measures.

    Returns `risk_level="unknown"` (not `"low"`) when nothing has ever
    been logged for `strategy_id`, or when a data span can't be computed
    (e.g. every matching record has an empty `data_range`) -- "no
    evidence to assess" is a different claim than "assessed as low risk".
    """
    parent_totals, standalone_count, min_start_ms, max_end_ms = _scan_records(strategy_id, runs_path)
    parent_run_groups, notes = _resolve_parent_groups(parent_totals)
    total_combinations = sum(parent_run_groups.values()) + standalone_count

    if total_combinations == 0:
        return OverfittingCheckResult(
            strategy_id=strategy_id,
            total_combinations_tried=0,
            parent_run_groups={},
            standalone_run_count=0,
            data_span_start_ms=None,
            data_span_end_ms=None,
            data_span_years=None,
            combinations_per_year=None,
            risk_level="unknown",
            warning=f"no backtest_run records found for strategy_id={strategy_id!r} -- nothing to assess",
            notes=notes,
        )

    data_span_years: float | None = None
    if min_start_ms is not None and max_end_ms is not None and max_end_ms > min_start_ms:
        data_span_years = (max_end_ms - min_start_ms) / _MS_PER_YEAR

    risk_level, combinations_per_year = _compute_risk_level(total_combinations, data_span_years)
    warning = _build_warning(strategy_id, total_combinations, data_span_years, combinations_per_year, risk_level)

    return OverfittingCheckResult(
        strategy_id=strategy_id,
        total_combinations_tried=total_combinations,
        parent_run_groups=parent_run_groups,
        standalone_run_count=standalone_count,
        data_span_start_ms=min_start_ms,
        data_span_end_ms=max_end_ms,
        data_span_years=data_span_years,
        combinations_per_year=combinations_per_year,
        risk_level=risk_level,
        warning=warning,
        notes=notes,
    )


# ===========================================================================
# Strategy Research Task P: family-level / project-level trial accounting.
# Everything below is strictly additive -- `check_combination_count` above
# is untouched. See this module's docstring and
# `.planning/sr-p-trial-accounting.md`.
# ===========================================================================

# `run_walk_forward` tags the `parent_run_id` of every `fit()` call its
# opt-in parameter-sensitivity check makes with this prefix, so those
# records are self-identifying instead of indistinguishable from real
# standalone runs. A *prefixed distinct* id (rather than the enclosing
# run's own id) keeps sr-g's original concern solved -- the probes still
# never join the real grid's `parent_run_id` group, so that group's
# `total_candidates` stays consistent.
SENSITIVITY_PARENT_RUN_ID_PREFIX = "sensitivity:"

# `params` keys that carry no information about the configuration actually
# under test, so a fingerprint built only from them would be worthless.
# `symbol` is constant project-wide (single-symbol scope). `candidates` is
# the grid *definition* a caller passed in, not the configuration the
# `Trainable` was constructed with -- verified against the real log: six
# separate ensemble-momentum runs representing materially different work
# all log the identical `{"candidates": [[15], [20], [25], [30]]}`. See
# `research/lineage.py`'s docstring for the full verification.
_UNINFORMATIVE_PARAM_KEYS = frozenset({"symbol", "candidates"})


class TrialKind(StrEnum):
    """What role a logged `backtest_run` record actually played, replacing
    the old flat "+1 per record". Only `SELECTION` counts toward `N`.

    `REPRODUCTION` is deliberately **not** returned by
    `classify_trial_kind`: whether a configuration is a re-run of an
    earlier one is a property of the record's position in the whole log,
    not of the record itself, so it can only be assigned while aggregating
    (see `check_project_combination_count`).
    """

    SELECTION = "selection"
    SENSITIVITY_PROBE = "sensitivity_probe"
    REPRODUCTION = "reproduction"


def classify_trial_kind(record: Mapping[str, Any]) -> TrialKind:
    """Classify one `backtest_run` record as a selection trial or a
    parameter-sensitivity probe.

    Two rules, in order:

    1. **Self-describing** (records written after Strategy Research Task
       P): a `parent_run_id` beginning with
       `SENSITIVITY_PARENT_RUN_ID_PREFIX` is a sensitivity probe. Any other
       non-`None` `parent_run_id` is a grid-search candidate, i.e. a real
       selection trial.
    2. **Empirical rule for historical records** (`parent_run_id is None`):
       a standalone record with **exactly one fold** is a sensitivity
       probe; a standalone multi-fold record is a real walk-forward run.

    Rule 2 is an **empirical fact about *this* project's
    `runs/experiments.jsonl`, not a general invariant** -- stated that way
    deliberately. It was verified exhaustively against the real 1,839-record
    log: exactly 382 records are standalone-and-single-fold, they belong to
    exactly three `strategy_id`s (`ensemble-momentum` 190,
    `single-lookback-momentum` 165,
    `ma-crossover-task-g-sensitivity-demo-v2` 27), and exactly those three
    ran `check_parameter_sensitivity` under sr-g's post-wiring-fix
    configuration. Each count reconciles exactly with that run's own
    recorded neighbour evaluations (e.g. `ensemble-momentum`'s two
    sensitivity-enabled runs: 2 x 19 folds x (1 winner + 4 neighbours) =
    190). Zero counterexamples across the whole log. Rule 1 makes rule 2
    unnecessary for anything logged from now on.
    """
    parent_run_id = record.get("parent_run_id")
    if isinstance(parent_run_id, str) and parent_run_id.startswith(SENSITIVITY_PARENT_RUN_ID_PREFIX):
        return TrialKind.SENSITIVITY_PROBE
    if parent_run_id is not None:
        return TrialKind.SELECTION
    if len(record.get("fold_results") or []) == 1:
        return TrialKind.SENSITIVITY_PROBE
    return TrialKind.SELECTION


@dataclass(frozen=True)
class FamilyOverfittingCheckResult:
    """One research family's corrected trial accounting.

    `selection_trials` is the honest `N` for this family. It **includes**
    `reproduction_trials` on purpose: a re-run is reported, never silently
    merged away, because whether two identically-fingerprinted runs are
    genuinely "one trial" is a judgment call the heuristic shouldn't make
    unilaterally. `deduplicated_selection_trials` is the merged view a
    reviewer can use instead once they've checked the reproductions are
    real.

    `naive_total_combinations` is what the pre-Task-P flat rule reports
    over the same records, kept alongside so the size of the correction is
    visible rather than something a reader has to reconstruct.
    """

    family: str
    purpose: str
    strategy_ids: list[str]
    naive_total_combinations: int
    selection_trials: int
    sensitivity_probe_trials: int
    reproduction_trials: int
    deduplicated_selection_trials: int
    data_span_start_ms: int | None
    data_span_end_ms: int | None
    data_span_years: float | None
    combinations_per_year: float | None
    risk_level: str
    warning: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "purpose": self.purpose,
            "strategy_ids": list(self.strategy_ids),
            "naive_total_combinations": self.naive_total_combinations,
            "selection_trials": self.selection_trials,
            "sensitivity_probe_trials": self.sensitivity_probe_trials,
            "reproduction_trials": self.reproduction_trials,
            "deduplicated_selection_trials": self.deduplicated_selection_trials,
            "data_span_start_ms": self.data_span_start_ms,
            "data_span_end_ms": self.data_span_end_ms,
            "data_span_years": self.data_span_years,
            "combinations_per_year": self.combinations_per_year,
            "risk_level": self.risk_level,
            "warning": self.warning,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ProjectOverfittingCheckResult:
    """Per-family results plus project-level totals split by `purpose`.

    **Report both levels, always.** Bailey et al.'s `N` is "the number of
    trials from which this maximum was selected". Within one family the
    project compared that family's own variants against each other -- but
    it also chose between families, after seeing all of their results, so
    the honest `N` behind "the best result this project found" is
    `research_selection_trials`, not any single family's figure.
    Infrastructure runs (pipeline end-to-end checks that were never
    candidates for paper trading) are counted and reported separately, so
    they neither inflate the research `N` nor silently disappear.
    """

    families: dict[str, FamilyOverfittingCheckResult]
    research_selection_trials: int
    infrastructure_selection_trials: int
    total_selection_trials: int
    research_sensitivity_probe_trials: int
    infrastructure_sensitivity_probe_trials: int
    total_sensitivity_probe_trials: int
    research_reproduction_trials: int
    infrastructure_reproduction_trials: int
    total_reproduction_trials: int
    naive_total_combinations: int
    warning: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "families": {name: result.to_dict() for name, result in self.families.items()},
            "research_selection_trials": self.research_selection_trials,
            "infrastructure_selection_trials": self.infrastructure_selection_trials,
            "total_selection_trials": self.total_selection_trials,
            "research_sensitivity_probe_trials": self.research_sensitivity_probe_trials,
            "infrastructure_sensitivity_probe_trials": self.infrastructure_sensitivity_probe_trials,
            "total_sensitivity_probe_trials": self.total_sensitivity_probe_trials,
            "research_reproduction_trials": self.research_reproduction_trials,
            "infrastructure_reproduction_trials": self.infrastructure_reproduction_trials,
            "total_reproduction_trials": self.total_reproduction_trials,
            "naive_total_combinations": self.naive_total_combinations,
            "warning": self.warning,
            "notes": list(self.notes),
        }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _params_fingerprint(record: Mapping[str, Any]) -> str | None:
    """Canonical JSON of the record's *informative* `params`, or `None`
    when it carries no real configuration information (see
    `_UNINFORMATIVE_PARAM_KEYS`).
    """
    params = record.get("params")
    if not isinstance(params, Mapping):
        return None
    informative = {k: v for k, v in params.items() if k not in _UNINFORMATIVE_PARAM_KEYS}
    if not informative:
        return None
    return _canonical(informative)


def _standalone_fingerprint(family: str, record: Mapping[str, Any]) -> str:
    """Configuration fingerprint for one standalone walk-forward record.

    Primary key `(family, params, walk_forward_config, data_range)` when
    `params` is informative. Every real multi-fold record in this project's
    log falls through to the documented fallback key
    `(family, strategy_version, aggregate_metrics.mean_sharpe)` instead,
    because `run_walk_forward` never sees the `Trainable`'s constructor
    arguments and so cannot log them -- see this module's docstring and
    `FamilyOverfittingCheckResult`'s honest-limits note.
    """
    params_fingerprint = _params_fingerprint(record)
    if params_fingerprint is not None:
        return _canonical(
            [
                "params",
                family,
                params_fingerprint,
                _canonical(record.get("walk_forward_config") or {}),
                _canonical(record.get("data_range") or {}),
            ]
        )
    return _canonical(
        [
            "fallback",
            family,
            record.get("strategy_version"),
            (record.get("aggregate_metrics") or {}).get("mean_sharpe"),
        ]
    )


def _group_fingerprint(family: str, records: list[Mapping[str, Any]]) -> str:
    """Configuration fingerprint for one grid-search group (all the records
    sharing a `parent_run_id`). Built from the *set* of `(params,
    data_range)` pairs its candidate records cover, so the same grid
    re-evaluated over the same windows fingerprints identically regardless
    of the order the folds happened to be logged in.
    """
    members = sorted(
        _canonical(
            [
                _params_fingerprint(record) or f"version:{record.get('strategy_version')}",
                _canonical(record.get("data_range") or {}),
            ]
        )
        for record in records
    )
    return _canonical(["group", family, members])


@dataclass
class _TrialUnit:
    """One countable unit of "combinations tried": either a whole
    `parent_run_id` group (weight = its `total_candidates`) or a single
    standalone record (weight 1). `order` is the log position of the first
    record that opened the unit, so reproduction detection can walk units
    in the order the research actually happened.
    """

    order: int
    kind: TrialKind
    weight: int
    fingerprint: str | None


@dataclass
class _FamilyAccumulator:
    """Mutable per-family scratch state for the single pass over the log."""

    family: str
    purpose: str
    strategy_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    standalone_records: list[tuple[int, dict]] = field(default_factory=list)
    parent_groups: dict[str, list[dict]] = field(default_factory=dict)
    group_order: dict[str, int] = field(default_factory=dict)
    min_start_ms: int | None = None
    max_end_ms: int | None = None

    def observe(self, order: int, record: dict, resolution: FamilyResolution) -> None:
        if resolution.strategy_id not in self.strategy_ids:
            self.strategy_ids.append(resolution.strategy_id)
        if resolution.note is not None and resolution.note not in self.notes:
            self.notes.append(resolution.note)

        parent_run_id = record.get("parent_run_id")
        if parent_run_id is None:
            self.standalone_records.append((order, record))
        else:
            if parent_run_id not in self.parent_groups:
                self.parent_groups[parent_run_id] = []
                self.group_order[parent_run_id] = order
            self.parent_groups[parent_run_id].append(record)

        data_range = record.get("data_range") or {}
        start_ms = data_range.get("start_ms")
        end_ms = data_range.get("end_ms")
        if start_ms is not None:
            self.min_start_ms = start_ms if self.min_start_ms is None else min(self.min_start_ms, start_ms)
        if end_ms is not None:
            self.max_end_ms = end_ms if self.max_end_ms is None else max(self.max_end_ms, end_ms)


def _scan_project_records(runs_path: str | Path) -> dict[str, _FamilyAccumulator]:
    """Single pass over `runs_path`, bucketing every `backtest_run` record
    into its research family (`research.lineage.resolve_family`).
    `holdout_access` records are ignored entirely, same as
    `check_combination_count`. Any record related to a logged holdout
    confirmation (see `_holdout_run_ids`/`_is_holdout_related`, Strategy
    Research Task V) is skipped too -- a holdout confirmation was never
    searched over, so it must not inflate any family's or the project's
    selection-trial `N`.
    """
    holdout_run_ids = _holdout_run_ids(runs_path)
    accumulators: dict[str, _FamilyAccumulator] = {}
    for order, record in enumerate(experiment_log.read_records(runs_path)):
        if record.get("record_type") != "backtest_run":
            continue
        if _is_holdout_related(record, holdout_run_ids):
            continue
        resolution = resolve_family(record.get("strategy_id", ""), record)
        accumulator = accumulators.get(resolution.family)
        if accumulator is None:
            accumulator = _FamilyAccumulator(family=resolution.family, purpose=resolution.purpose)
            accumulators[resolution.family] = accumulator
        accumulator.observe(order, record, resolution)
    return accumulators


def _build_trial_units(accumulator: _FamilyAccumulator) -> tuple[list[_TrialUnit], dict[str, int], list[str]]:
    """Reduce one family's raw records to countable `_TrialUnit`s, plus the
    resolved per-`parent_run_id` counts and any defensive notes.
    """
    units: list[_TrialUnit] = []
    for order, record in accumulator.standalone_records:
        if classify_trial_kind(record) is TrialKind.SENSITIVITY_PROBE:
            units.append(_TrialUnit(order=order, kind=TrialKind.SENSITIVITY_PROBE, weight=1, fingerprint=None))
        else:
            units.append(
                _TrialUnit(
                    order=order,
                    kind=TrialKind.SELECTION,
                    weight=1,
                    fingerprint=_standalone_fingerprint(accumulator.family, record),
                )
            )

    selection_groups = {
        parent_run_id: records
        for parent_run_id, records in accumulator.parent_groups.items()
        if not parent_run_id.startswith(SENSITIVITY_PARENT_RUN_ID_PREFIX)
    }
    resolved_groups, notes = _resolve_parent_groups(
        {
            parent_run_id: [record.get("total_candidates") for record in records]
            for parent_run_id, records in selection_groups.items()
        }
    )

    for parent_run_id, records in accumulator.parent_groups.items():
        order = accumulator.group_order[parent_run_id]
        if parent_run_id.startswith(SENSITIVITY_PARENT_RUN_ID_PREFIX):
            # Every record in a sensitivity group is its own probe
            # evaluation (each logs `total_candidates=1`), so the group's
            # probe count is its record count -- not the "one count per
            # group" rule that applies to a real grid search, where the
            # same N-candidate grid is deliberately re-evaluated per fold.
            units.append(
                _TrialUnit(order=order, kind=TrialKind.SENSITIVITY_PROBE, weight=len(records), fingerprint=None)
            )
        else:
            units.append(
                _TrialUnit(
                    order=order,
                    kind=TrialKind.SELECTION,
                    weight=resolved_groups[parent_run_id],
                    fingerprint=_group_fingerprint(accumulator.family, records),
                )
            )

    units.sort(key=lambda unit: unit.order)
    return units, resolved_groups, notes


def _count_units(units: list[_TrialUnit]) -> tuple[int, int, int]:
    """Walk units in log order, returning
    `(selection_trials, sensitivity_probe_trials, reproduction_trials)`.
    A selection unit whose fingerprint was already seen is counted as a
    reproduction **in addition to** (not instead of) counting toward
    `selection_trials` -- see `FamilyOverfittingCheckResult`'s docstring.
    """
    seen: set[str] = set()
    selection = 0
    probes = 0
    reproductions = 0
    for unit in units:
        if unit.kind is TrialKind.SENSITIVITY_PROBE:
            probes += unit.weight
            continue
        selection += unit.weight
        if unit.fingerprint in seen:
            reproductions += unit.weight
        elif unit.fingerprint is not None:
            seen.add(unit.fingerprint)
    return selection, probes, reproductions


def _build_family_warning(result_family: str, selection: int, probes: int, years: float | None, level: str) -> str:
    if level == "unknown":
        return (
            f"family={result_family!r}: {selection} selection trial(s) (plus {probes} sensitivity probe(s) "
            "excluded from N), but no usable data span could be computed from the logged records' data_range "
            "fields -- cannot assess overfitting risk from this heuristic"
        )
    return (
        f"family={result_family!r}: {selection} selection trial(s) across {years:.2f} year(s) of logged data "
        f"({selection / years:.1f}/year) -- MinBTL-spirit risk level: {level.upper()}. "
        f"{probes} sensitivity probe(s) excluded from N (post-selection diagnostics, never selection trials). "
        "A warning only, not a block."
    )


def _build_family_result(accumulator: _FamilyAccumulator) -> FamilyOverfittingCheckResult:
    units, resolved_groups, group_notes = _build_trial_units(accumulator)
    selection, probes, reproductions = _count_units(units)

    data_span_years: float | None = None
    if (
        accumulator.min_start_ms is not None
        and accumulator.max_end_ms is not None
        and accumulator.max_end_ms > accumulator.min_start_ms
    ):
        data_span_years = (accumulator.max_end_ms - accumulator.min_start_ms) / _MS_PER_YEAR

    risk_level, combinations_per_year = _compute_risk_level(selection, data_span_years)
    naive_total = sum(resolved_groups.values()) + len(accumulator.standalone_records)

    return FamilyOverfittingCheckResult(
        family=accumulator.family,
        purpose=accumulator.purpose,
        strategy_ids=list(accumulator.strategy_ids),
        naive_total_combinations=naive_total,
        selection_trials=selection,
        sensitivity_probe_trials=probes,
        reproduction_trials=reproductions,
        deduplicated_selection_trials=selection - reproductions,
        data_span_start_ms=accumulator.min_start_ms,
        data_span_end_ms=accumulator.max_end_ms,
        data_span_years=data_span_years,
        combinations_per_year=combinations_per_year,
        risk_level=risk_level,
        warning=_build_family_warning(accumulator.family, selection, probes, data_span_years, risk_level),
        notes=accumulator.notes + group_notes,
    )


def _sum_by_purpose(families: dict[str, FamilyOverfittingCheckResult], attribute: str, purpose: str) -> int:
    return sum(getattr(result, attribute) for result in families.values() if result.purpose == purpose)


def check_project_combination_count(
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
) -> ProjectOverfittingCheckResult:
    """Project-wide corrected trial accounting: one
    `FamilyOverfittingCheckResult` per research family, plus project-level
    totals split by `purpose` (`"research"` vs `"infrastructure"`).

    This is the function whose `research_selection_trials` is the honest
    `N` for "the best result this project found" -- families were not
    chosen blind of each other, so a single family's own count understates
    the real selection bias. See this module's docstring for the three
    defects in `check_combination_count`'s number that this corrects, and
    `.planning/sr-p-trial-accounting.md` for the verified evidence behind
    each.

    Excludes every record related to a logged holdout confirmation
    (Strategy Research Task V, `_holdout_run_ids`/`_is_holdout_related`) --
    a holdout confirmation was never searched over, so counting it toward
    `N` would be exactly the selection-bias overstatement this function
    exists to correct, not an instance of it.

    A warning, never a block -- the established convention throughout this
    module (and `research/holdout.py` before it).
    """
    accumulators = _scan_project_records(runs_path)
    families = {name: _build_family_result(accumulator) for name, accumulator in accumulators.items()}

    research_selection = _sum_by_purpose(families, "selection_trials", RESEARCH_PURPOSE)
    infrastructure_selection = _sum_by_purpose(families, "selection_trials", INFRASTRUCTURE_PURPOSE)
    research_probes = _sum_by_purpose(families, "sensitivity_probe_trials", RESEARCH_PURPOSE)
    infrastructure_probes = _sum_by_purpose(families, "sensitivity_probe_trials", INFRASTRUCTURE_PURPOSE)
    research_reproductions = _sum_by_purpose(families, "reproduction_trials", RESEARCH_PURPOSE)
    infrastructure_reproductions = _sum_by_purpose(families, "reproduction_trials", INFRASTRUCTURE_PURPOSE)
    naive_total = sum(result.naive_total_combinations for result in families.values())

    notes: list[str] = []
    for result in families.values():
        notes.extend(result.notes)

    if not families:
        warning = f"no backtest_run records found in {str(runs_path)!r} -- nothing to assess"
    else:
        warning = (
            f"project-wide corrected trial accounting: {research_selection} research selection trial(s) "
            f"(the honest N for the best result this project found, since strategy families were compared "
            f"against each other, not chosen blind) + {infrastructure_selection} infrastructure selection "
            f"trial(s) = {research_selection + infrastructure_selection} total. "
            f"{research_probes + infrastructure_probes} sensitivity probe(s) excluded from N; "
            f"{research_reproductions + infrastructure_reproductions} of the selection trial(s) are "
            f"reproductions of an already-counted configuration, reported but deliberately not merged away. "
            f"The pre-correction flat count over the same records was {naive_total}. "
            "A warning only, not a block."
        )

    return ProjectOverfittingCheckResult(
        families=families,
        research_selection_trials=research_selection,
        infrastructure_selection_trials=infrastructure_selection,
        total_selection_trials=research_selection + infrastructure_selection,
        research_sensitivity_probe_trials=research_probes,
        infrastructure_sensitivity_probe_trials=infrastructure_probes,
        total_sensitivity_probe_trials=research_probes + infrastructure_probes,
        research_reproduction_trials=research_reproductions,
        infrastructure_reproduction_trials=infrastructure_reproductions,
        total_reproduction_trials=research_reproductions + infrastructure_reproductions,
        naive_total_combinations=naive_total,
        warning=warning,
        notes=notes,
    )
