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
"""

from dataclasses import dataclass, field
from pathlib import Path

from research import experiment_log

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


def _scan_records(
    strategy_id: str,
    runs_path: str | Path,
) -> tuple[dict[str, list[int | None]], int, int | None, int | None]:
    """First pass over `runs_path`: bucket every matching `backtest_run`
    record's `total_candidates` by `parent_run_id` (raw, not yet
    resolved to one count per group -- see `_resolve_parent_groups`),
    count standalone (`parent_run_id is None`) records, and track the
    widest `[start_ms, end_ms]` span observed across every matching
    record's `data_range`. `record_type: "holdout_access"` entries and
    records for a different `strategy_id` are skipped entirely.
    """
    parent_totals: dict[str, list[int | None]] = {}
    standalone_count = 0
    min_start_ms: int | None = None
    max_end_ms: int | None = None

    for record in experiment_log.read_records(runs_path):
        if record.get("record_type") != "backtest_run":
            continue
        if record.get("strategy_id") != strategy_id:
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
    combinations-tried).

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
