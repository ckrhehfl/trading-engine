"""Pre-registration: committing a strategy specification, in full, before
the data is touched -- Strategy Research Task S.

See `.planning/sr-s-preregistration.md` for the schema's field-by-field
reasoning and CLAUDE.md's "Backtest/Walk-Forward Eligibility Bar" (amended
and human-approved 2026-07-29) for the criteria this artifact encodes.

## Why this exists

`sr-r` closed out eight strategy attempts statistically and found nothing
survives: 18 distinct configurations, 0 reaching DSR >= 0.95, with the best
result (mean annualized Sharpe +0.039) landing at DSR = 2.0e-05 against the
project's **117** research selection trials. The diagnosis was not about any
strategy -- it was that the search budget was spent without the count ever
being honest, on a window whose own detection floor (~1.21 annualized
Sharpe) sat above any plausible real edge. `sr-p` made the count honest and
`sr-q` implemented the statistic that consumes it; the Eligibility Bar was
then amended to gate on it.

This module closes the remaining hole: **nothing yet made it structurally
hard to quietly expand a search after seeing results.** Exactly one
pre-registered attempt is planned against the virgin `1d` window (`sr-t`),
and the pre-registration is what makes that attempt's `N = 1` defensible
rather than merely asserted.

## Where the artifact lives, and why not `.planning/`

`configs/research/preregistrations/<preregistration_id>.json`, git-tracked.
**Not** `.planning/`: that directory's own README requires a file there to
be "added in the same PR as the work it plans", i.e. *after* or *with* the
work -- whereas a pre-registration is only worth anything if it is committed
**before** the work, and it has to be machine-readable so code can check a
run against it. `configs/research/` already holds exactly this shape:
git-tracked JSON carrying a substantial `rationale`, read by code
(`holdout.json`, `holdout_1h.json`, `holdout_1d.json`). This follows that
precedent.

## Fail loud on a bad artifact; warn loud on a deviating run

Two different jobs, deliberately given two different severities:

- `validate_preregistration` **raises** (`PreregistrationError`, a
  `ValueError`) on anything malformed or incomplete. Same fail-loud
  discipline as `data/_grid.py`'s alignment validation and
  `holdout.load_holdout_config`'s `holdout_side` check. It validates the
  **artifact**; it never judges the research.
- `check_run_matches_preregistration` **warns** on every field mismatch --
  different fees, different fold geometry, a different `strategy_id` --
  following this project's established "make risky situations loud and
  visible, don't silently prevent them" convention (`holdout.py`'s
  `force_reclaim_reason`, `overfitting_check.py`'s warning-not-a-block).

**Exactly one hard block**: a run whose `total_candidates` exceeds the
registered value raises `GridExpansionError`. That is the single failure
mode pre-registration exists to prevent, and it is a number-vs-number
comparison with no judgment in it. Everything else warns. See
`check_run_matches_preregistration` for the full reasoning, including the
one residual gap (a caller omitting `total_candidates` entirely) that is
warned about rather than blocked.

## Conformance to the human-approved Bar is validated; the research is not

Four numbers in a registration are not free choices -- they are the
human-approved Eligibility Bar's own constants, and a registration
declaring a **laxer** value would be a non-human amending a
human-approval-gated policy (CLAUDE.md's Non-negotiable Rules and the
Bar's own "same status as Risk Parameters" clause). So:

- `primary_criterion.threshold` must be >= `ELIGIBILITY_BAR_DSR_THRESHOLD`
  (0.95, imported from `research.retrospective` rather than re-typed).
- `primary_criterion.max_drawdown_ceiling` must be <= 0.25.
- `primary_criterion.profit_factor_floor` must be >= 1.3.
- `primary_criterion.min_fold_consistency` (walk-forward only) must be
  >= 0.80.
- `primary_criterion.sign_test_alpha` (walk-forward only) must be <= 0.05.
- `primary_criterion.min_total_trades` must be >= the Bar's own
  frequency-scaled floor, `frequency_scaled_min_trades`, computed from the
  registration's *own* declared geometry.

**Stricter is always accepted** -- a researcher holding themselves to more
than the Bar is never a governance problem. Only laxer raises. This is
conformance checking against an approved constant, not an opinion about the
hypothesis.

What is deliberately **not** enforced, because enforcing it would be wrong:
the Bar's "minimum 8-10 folds for the result to be considered credible".
`sr-t`'s 822-bar `1d` research window plausibly cannot produce 8 folds at
any sensible window sizing, and a criterion that a data-limited window
cannot satisfy would block the very attempt this machinery exists to
enable. `expected_fold_count` is derived and
`ELIGIBILITY_BAR_MIN_FOLD_COUNT` is exported so a runner can *warn*; see
`research/run_preregistered.py`.

## What this module does not do

It does not load market data, run a backtest, or evaluate a result. It
validates an artifact, enumerates a grid, hashes a file, and compares a
run's parameters against what was registered. `research/run_preregistered.py`
is the thin runner that composes it with `research/walkforward.py`.

Grid alignment of the registered `[start_ms, end_ms)` window is
deliberately **not** re-validated here. `data/_grid.py`'s own docstring
scopes it to `python/data/`, and nothing outside that package imports it;
duplicating its interval table into `research/` -- to fail milliseconds
earlier than `data.store.fetch_klines` already fails loudly on the same
input -- would create exactly the drift that module exists to prevent. A
misaligned or unknown-interval registration therefore fails at load time,
from the data layer, with the data layer's own message. `test_run_
preregistered.py` proves that path still fires rather than being lost.
"""

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import product
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.retrospective import DEFAULT_DSR_THRESHOLD
from research.walkforward import generate_folds

logger = logging.getLogger(__name__)

# Relative to cwd, same convention as `experiment_log.DEFAULT_RUNS_PATH` and
# `holdout.DEFAULT_HOLDOUT_CONFIG_PATH` -- callers running for real are
# expected to run from the repo root; tests always pass an explicit path.
DEFAULT_PREREGISTRATION_DIR = "configs/research/preregistrations"

# Which split a registration is written against. `holdout` exists so a
# holdout-confirmation run can be pre-registered too -- CLAUDE.md's
# single-window variant requires its criteria to be "pinned *before*
# access, not chosen after".
SPLIT_RESEARCH = "research"
SPLIT_HOLDOUT = "holdout"
VALID_SPLITS = (SPLIT_RESEARCH, SPLIT_HOLDOUT)

# The two forms the amended Eligibility Bar's primary criterion can take.
# A multi-fold walk-forward run is gated on the **Deflated** Sharpe Ratio
# against the project-level `N`; a single-window holdout confirmation is
# gated on the **Probabilistic** Sharpe Ratio, deliberately not DSR,
# because the holdout was never searched over (`N`=1 makes DSR identical to
# PSR anyway).
CRITERION_KIND_WALK_FORWARD_DSR = "walk_forward_dsr"
CRITERION_KIND_HOLDOUT_PSR = "holdout_psr"
VALID_CRITERION_KINDS = (CRITERION_KIND_WALK_FORWARD_DSR, CRITERION_KIND_HOLDOUT_PSR)

# A criterion kind implies which split it can be evaluated on; a
# registration claiming otherwise is internally inconsistent.
SPLIT_BY_CRITERION_KIND = {
    CRITERION_KIND_WALK_FORWARD_DSR: SPLIT_RESEARCH,
    CRITERION_KIND_HOLDOUT_PSR: SPLIT_HOLDOUT,
}

# --- The human-approved Eligibility Bar's own constants ---------------------
# Imported, not re-typed, where an implementation of record already exists.
ELIGIBILITY_BAR_DSR_THRESHOLD = DEFAULT_DSR_THRESHOLD
ELIGIBILITY_BAR_MAX_DRAWDOWN_CEILING = Decimal("0.25")
ELIGIBILITY_BAR_MIN_PROFIT_FACTOR_FLOOR = Decimal("1.3")
ELIGIBILITY_BAR_MIN_FOLD_CONSISTENCY = Decimal("0.80")
ELIGIBILITY_BAR_MAX_SIGNIFICANCE_ALPHA = 0.05
# Reported, never enforced -- see the module docstring.
ELIGIBILITY_BAR_MIN_FOLD_COUNT = 8

# The frequency-scaled trade-count floor, CLAUDE.md (revised 2026-07-29):
# `max(30, min(100, floor(total_evaluated_bars / bars_per_day / 20)))`.
TRADE_FLOOR_ABSOLUTE_MINIMUM = 30
TRADE_FLOOR_CAP = 100
TRADE_FLOOR_DAYS_PER_TRADE = 20

# A registration names the code that will run it. That is a config-driven
# import, i.e. an execution surface, so it is confined to this project's
# own research package rather than left open to any importable name.
ENTRY_POINT_MODULE_PREFIX = "research."

# Three regions, not two. A binary pass/fail framing would be dishonest
# when a real edge can plausibly fail to be detected -- which is exactly
# what `sr-r` found had happened across all 117 prior trials. Additional
# regions are allowed (CLAUDE.md's holdout variant has its own "not powered
# to confirm" outcome); these three are mandatory.
REQUIRED_OUTCOME_REGIONS = ("PASS", "INCONCLUSIVE", "FAIL")

REQUIRED_TOP_LEVEL_KEYS = (
    "preregistration_id",
    "registered_at",
    "strategy_family",
    "strategy_id",
    "strategy_version",
    "strategy_entry_point",
    "hypothesis",
    "prior_art",
    "data",
    "parameter_grid",
    "total_candidates",
    "free_parameter_count",
    "procedure",
    "primary_criterion",
    "secondary_reported_not_gating",
    "declared_detection_floor_sharpe",
    "declared_power",
    "outcome_interpretation",
    "stopping_rule",
)

# Everything else is rejected. This departs from
# `holdout.load_holdout_config`'s tolerance of unknown keys, on purpose: a
# pre-registration's whole value is that a reader can tell exactly what was
# committed, and a silently-ignored key is a place for a claim to live that
# no code and no reader ever checks.
OPTIONAL_TOP_LEVEL_KEYS = ("rationale", "notes")

REQUIRED_DATA_KEYS = (
    "symbol",
    "interval",
    "source",
    "split",
    "holdout_config_path",
    "start_ms",
    "end_ms",
    "expected_bars",
)

# Always required. Fold geometry is required only for a walk-forward
# registration -- a single-window holdout run has no folds, and CLAUDE.md's
# single-window variant explicitly drops every fold-based clause rather
# than scaling it down.
REQUIRED_PROCEDURE_KEYS = ("fee_bps", "slippage_bps", "bars_per_day", "funding_included")
FOLD_GEOMETRY_KEYS = ("train_bars", "validate_bars", "step_bars")

REQUIRED_DECLARED_POWER_KEYS = ("assumed_true_sharpe", "probability", "derivation")

REQUIRED_CRITERION_KEYS = (
    "kind",
    "threshold",
    "max_drawdown_ceiling",
    "min_total_trades",
    "profit_factor_floor",
    "criteria_pinned_at_claude_md_revision",
)
WALK_FORWARD_CRITERION_KEYS = ("min_fold_consistency", "sign_test_alpha")
HOLDOUT_CRITERION_KEYS = ("require_sharpe_above_detection_floor",)


class PreregistrationError(ValueError):
    """A malformed or incomplete registration artifact. A `ValueError`
    subclass so a caller that only knows about `ValueError` (e.g. an
    `argparse` `type=` converter) still handles it.
    """


class GridExpansionError(RuntimeError):
    """The one hard block: a run reporting more candidates than were
    registered. Named as its own type (rather than a bare `RuntimeError`)
    so a caller can catch precisely this and nothing else -- same shape as
    `holdout.HoldoutAlreadyClaimedError`.
    """


# ---------------------------------------------------------------------------
# Small typed field readers -- each raises PreregistrationError naming the
# field, so a validation failure always says which key is wrong.
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreregistrationError(f"{field}: expected a JSON object, got {type(value).__name__}")
    return value


def _require_present(config: Mapping[str, Any], field: str, where: str = "") -> Any:
    if field not in config:
        prefix = f"{where}." if where else ""
        raise PreregistrationError(f"{prefix}{field} is required and is missing")
    return config[field]


def _require_non_blank_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreregistrationError(f"{field} must be a non-blank string, got {value!r}")
    return value


def _require_int(value: Any, field: str, *, minimum: int) -> int:
    # `bool` is an `int` subclass in Python; a `true` where a count belongs
    # is a caller mistake, not the integer 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreregistrationError(f"{field} must be an integer, got {value!r}")
    if value < minimum:
        raise PreregistrationError(f"{field} must be >= {minimum}, got {value}")
    return value


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PreregistrationError(f"{field} must be a boolean, got {value!r}")
    return value


def _require_decimal(value: Any, field: str) -> Decimal:
    """A `Decimal`-valued field. A JSON string is preferred (exact); a JSON
    number is accepted and converted via `str` so it never round-trips
    through binary float arithmetic -- the same reasoning as
    `experiment_log._json_default`'s `Decimal`-as-text rule.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PreregistrationError(f"{field} must be a number or a numeric string, got {value!r}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise PreregistrationError(f"{field} is not a valid decimal: {value!r}") from exc
    if not parsed.is_finite():
        raise PreregistrationError(f"{field} must be finite, got {value!r}")
    return parsed


def _require_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PreregistrationError(f"{field} must be a number, got {value!r}")
    return float(_require_decimal(value, field))


def _require_string_list(value: Any, field: str) -> list[str]:
    """A non-empty list of non-blank strings.

    Non-empty on purpose for `prior_art`: an empty list is ambiguous
    between "no prior art exists" and "nobody looked", and the honest entry
    for genuinely novel work is a string saying which of the two it is.
    """
    if not isinstance(value, list) or not value:
        raise PreregistrationError(f"{field} must be a non-empty list of strings, got {value!r}")
    for index, item in enumerate(value):
        _require_non_blank_string(item, f"{field}[{index}]")
    return list(value)


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def parameter_names(grid: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[str]:
    """The registered parameter names, in the order the file declares them.

    Declaration order, not sorted order, so the enumeration a run receives
    is exactly the one a reader of the committed file sees. `json.load`
    preserves object key order, so this is stable across processes.
    """
    if isinstance(grid, Mapping):
        return list(grid.keys())
    if isinstance(grid, Sequence) and grid and isinstance(grid[0], Mapping):
        return list(grid[0].keys())
    raise PreregistrationError(f"parameter_grid must be a JSON object or a non-empty list of objects, got {grid!r}")


def enumerate_candidates(grid: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[dict]:
    """Every candidate the registered grid covers, enumerated in full.

    Two accepted shapes, distinguished structurally (no discriminator key
    needed, since JSON objects and arrays cannot be confused):

    - a **JSON object** `{"fast": [5, 8], "slow": [20, 30]}` -- the
      cartesian product, in declared key order (`fast` varying slowest).
    - a **JSON array** `[{"fast": 5, "slow": 20}, ...]` -- an explicit
      enumeration, returned as given. This exists because a real grid is
      often not a full product (e.g. a `fast < slow` constraint), and
      forcing such a grid into a product form would either register
      candidates that will never be run or silently understate the count.
    """
    if isinstance(grid, Mapping):
        if not grid:
            raise PreregistrationError("parameter_grid must not be empty")
        names = list(grid.keys())
        value_lists = []
        for name in names:
            values = grid[name]
            if not isinstance(values, list) or not values:
                raise PreregistrationError(
                    f"parameter_grid[{name!r}] must be a non-empty list of values, got {values!r}"
                )
            value_lists.append(values)
        return [dict(zip(names, combination)) for combination in product(*value_lists)]

    if not isinstance(grid, Sequence) or isinstance(grid, (str, bytes)) or not grid:
        raise PreregistrationError(f"parameter_grid must be a JSON object or a non-empty list of objects, got {grid!r}")

    candidates: list[dict] = []
    expected: list[str] | None = None
    for index, candidate in enumerate(grid):
        mapping = _require_mapping(candidate, f"parameter_grid[{index}]")
        if not mapping:
            raise PreregistrationError(f"parameter_grid[{index}] must declare at least one parameter")
        names = list(mapping.keys())
        if expected is None:
            expected = names
        elif set(names) != set(expected):
            raise PreregistrationError(
                f"parameter_grid[{index}] declares parameters {sorted(names)} but "
                f"parameter_grid[0] declares {sorted(expected)} -- every explicitly enumerated "
                "candidate must describe the same parameters, or the count is not comparable"
            )
        candidates.append(dict(mapping))
    return candidates


def candidate_rows(grid: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[list]:
    """`enumerate_candidates` as plain value lists ordered by
    `parameter_names` -- the shape this project's `TrainableStrategy`
    implementations already consume as `params["candidates"]` (the real log
    records e.g. `{"candidates": [[15], [20], [25], [30]]}`).

    Derived from `enumerate_candidates` rather than computed separately so
    the readable enumeration and the shape actually handed to a strategy
    cannot drift apart.
    """
    names = parameter_names(grid)
    return [[candidate[name] for name in names] for candidate in enumerate_candidates(grid)]


def frequency_scaled_min_trades(*, total_evaluated_bars: int, bars_per_day: int) -> int:
    """CLAUDE.md's frequency-scaled minimum trade count (revised
    2026-07-29, human-approved):

        max(30, min(100, floor(total_evaluated_bars / bars_per_day / 20)))

    i.e. roughly one trade per 20 evaluated days, clamped to `[30, 100]`.
    Reproduces that section's own published table exactly: 30 for the 1h
    19x720 geometry, 30 for the 15m 3x2880 one, and 41 for `sr-t`'s 822-bar
    `1d` research window.
    """
    if total_evaluated_bars < 0:
        raise ValueError(f"total_evaluated_bars must be non-negative, got {total_evaluated_bars}")
    if bars_per_day <= 0:
        raise ValueError(f"bars_per_day must be positive, got {bars_per_day}")
    evaluated_days = total_evaluated_bars // bars_per_day
    return max(TRADE_FLOOR_ABSOLUTE_MINIMUM, min(TRADE_FLOOR_CAP, evaluated_days // TRADE_FLOOR_DAYS_PER_TRADE))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_entry_point(value: Any) -> None:
    entry_point = _require_non_blank_string(value, "strategy_entry_point")
    if entry_point.count(":") != 1:
        raise PreregistrationError(
            f"strategy_entry_point must be exactly 'module.path:attribute', got {entry_point!r}"
        )
    module_path, attribute = entry_point.split(":")
    _require_non_blank_string(module_path, "strategy_entry_point (module part)")
    _require_non_blank_string(attribute, "strategy_entry_point (attribute part)")
    if not module_path.startswith(ENTRY_POINT_MODULE_PREFIX):
        raise PreregistrationError(
            f"strategy_entry_point must name a module inside {ENTRY_POINT_MODULE_PREFIX!r} "
            f"(a config-driven import is an execution surface), got {module_path!r}"
        )


def _validate_data(data: Any) -> tuple[str, int]:
    """Returns `(split, expected_bars)` for the cross-checks below."""
    mapping = _require_mapping(data, "data")
    for key in REQUIRED_DATA_KEYS:
        _require_present(mapping, key, "data")

    for key in ("symbol", "interval", "source", "holdout_config_path"):
        _require_non_blank_string(mapping[key], f"data.{key}")

    split = mapping["split"]
    if split not in VALID_SPLITS:
        raise PreregistrationError(f"data.split must be one of {VALID_SPLITS}, got {split!r}")

    start_ms = _require_int(mapping["start_ms"], "data.start_ms", minimum=0)
    end_ms = _require_int(mapping["end_ms"], "data.end_ms", minimum=0)
    if end_ms <= start_ms:
        raise PreregistrationError(f"data.end_ms ({end_ms}) must be greater than data.start_ms ({start_ms})")
    expected_bars = _require_int(mapping["expected_bars"], "data.expected_bars", minimum=1)
    return split, expected_bars


def _validate_procedure(procedure: Any, *, kind: str) -> Mapping[str, Any]:
    mapping = _require_mapping(procedure, "procedure")
    required = REQUIRED_PROCEDURE_KEYS
    if kind == CRITERION_KIND_WALK_FORWARD_DSR:
        required = required + FOLD_GEOMETRY_KEYS
    for key in required:
        _require_present(mapping, key, "procedure")

    for key in ("fee_bps", "slippage_bps"):
        if _require_decimal(mapping[key], f"procedure.{key}") < 0:
            raise PreregistrationError(f"procedure.{key} must be non-negative, got {mapping[key]!r}")
    _require_int(mapping["bars_per_day"], "procedure.bars_per_day", minimum=1)
    _require_bool(mapping["funding_included"], "procedure.funding_included")
    if kind == CRITERION_KIND_WALK_FORWARD_DSR:
        for key in FOLD_GEOMETRY_KEYS:
            _require_int(mapping[key], f"procedure.{key}", minimum=1)
    return mapping


def _validate_criterion(criterion: Any, *, split: str) -> str:
    """Returns the criterion `kind`."""
    mapping = _require_mapping(criterion, "primary_criterion")
    for key in REQUIRED_CRITERION_KEYS:
        _require_present(mapping, key, "primary_criterion")

    kind = mapping["kind"]
    if kind not in VALID_CRITERION_KINDS:
        raise PreregistrationError(
            f"primary_criterion.kind must be one of {VALID_CRITERION_KINDS}, got {kind!r}"
        )
    if SPLIT_BY_CRITERION_KIND[kind] != split:
        raise PreregistrationError(
            f"primary_criterion.kind={kind!r} is evaluated on the "
            f"{SPLIT_BY_CRITERION_KIND[kind]!r} split, but data.split is {split!r} -- "
            "a Deflated Sharpe Ratio gate belongs to a multi-fold research run and a "
            "Probabilistic Sharpe Ratio gate to the single-window holdout confirmation"
        )

    threshold = _require_float(mapping["threshold"], "primary_criterion.threshold")
    if not ELIGIBILITY_BAR_DSR_THRESHOLD <= threshold <= 1:
        raise PreregistrationError(
            f"primary_criterion.threshold must be in "
            f"[{ELIGIBILITY_BAR_DSR_THRESHOLD}, 1] -- CLAUDE.md's Eligibility Bar is "
            f"human-approval-gated, so a laxer threshold would amend it here; got {threshold}"
        )

    drawdown = _require_decimal(mapping["max_drawdown_ceiling"], "primary_criterion.max_drawdown_ceiling")
    if not 0 < drawdown <= ELIGIBILITY_BAR_MAX_DRAWDOWN_CEILING:
        raise PreregistrationError(
            f"primary_criterion.max_drawdown_ceiling must be in "
            f"(0, {ELIGIBILITY_BAR_MAX_DRAWDOWN_CEILING}] per the approved Bar, got {drawdown}"
        )

    profit_factor = _require_decimal(mapping["profit_factor_floor"], "primary_criterion.profit_factor_floor")
    if profit_factor < ELIGIBILITY_BAR_MIN_PROFIT_FACTOR_FLOOR:
        raise PreregistrationError(
            f"primary_criterion.profit_factor_floor must be >= "
            f"{ELIGIBILITY_BAR_MIN_PROFIT_FACTOR_FLOOR} per the approved Bar, got {profit_factor}"
        )

    _require_int(mapping["min_total_trades"], "primary_criterion.min_total_trades", minimum=1)
    _require_non_blank_string(
        mapping["criteria_pinned_at_claude_md_revision"],
        "primary_criterion.criteria_pinned_at_claude_md_revision",
    )

    if kind == CRITERION_KIND_WALK_FORWARD_DSR:
        for key in WALK_FORWARD_CRITERION_KEYS:
            _require_present(mapping, key, "primary_criterion")
        consistency = _require_decimal(
            mapping["min_fold_consistency"], "primary_criterion.min_fold_consistency"
        )
        if not ELIGIBILITY_BAR_MIN_FOLD_CONSISTENCY <= consistency <= 1:
            raise PreregistrationError(
                f"primary_criterion.min_fold_consistency must be in "
                f"[{ELIGIBILITY_BAR_MIN_FOLD_CONSISTENCY}, 1] per the approved Bar's 80-90% "
                f"band, got {consistency}"
            )
        alpha = _require_float(mapping["sign_test_alpha"], "primary_criterion.sign_test_alpha")
        if not 0 < alpha <= ELIGIBILITY_BAR_MAX_SIGNIFICANCE_ALPHA:
            raise PreregistrationError(
                f"primary_criterion.sign_test_alpha must be in "
                f"(0, {ELIGIBILITY_BAR_MAX_SIGNIFICANCE_ALPHA}], got {alpha}"
            )
    else:
        for key in HOLDOUT_CRITERION_KEYS:
            _require_present(mapping, key, "primary_criterion")
        if mapping["require_sharpe_above_detection_floor"] is not True:
            raise PreregistrationError(
                "primary_criterion.require_sharpe_above_detection_floor must be true -- "
                "CLAUDE.md's single-window holdout variant, clause 3: an observed Sharpe below "
                "the holdout window's own detection floor is reported 'not powered to confirm', "
                "and clearing the other criteria does not constitute confirmation"
            )
    return kind


def _validate_declared_power(power: Any) -> None:
    mapping = _require_mapping(power, "declared_power")
    for key in REQUIRED_DECLARED_POWER_KEYS:
        _require_present(mapping, key, "declared_power")
    if _require_float(mapping["assumed_true_sharpe"], "declared_power.assumed_true_sharpe") <= 0:
        raise PreregistrationError("declared_power.assumed_true_sharpe must be positive")
    probability = _require_float(mapping["probability"], "declared_power.probability")
    if not 0 < probability <= 1:
        raise PreregistrationError(f"declared_power.probability must be in (0, 1], got {probability}")
    _require_non_blank_string(mapping["derivation"], "declared_power.derivation")


def _validate_outcome_interpretation(outcome: Any) -> None:
    mapping = _require_mapping(outcome, "outcome_interpretation")
    for region in REQUIRED_OUTCOME_REGIONS:
        _require_present(mapping, region, "outcome_interpretation")
    for region, text in mapping.items():
        _require_non_blank_string(region, "outcome_interpretation (region name)")
        _require_non_blank_string(text, f"outcome_interpretation.{region}")


def _validate_trade_floor(
    criterion: Mapping[str, Any],
    procedure: Mapping[str, Any],
    *,
    kind: str,
    expected_bars: int,
) -> None:
    """The registered `min_total_trades` must be at least the Bar's own
    frequency-scaled floor for the registered geometry. Stricter is fine;
    laxer would amend a human-approved criterion.
    """
    bars_per_day = procedure["bars_per_day"]
    if kind == CRITERION_KIND_WALK_FORWARD_DSR:
        folds = generate_folds(
            expected_bars, procedure["train_bars"], procedure["validate_bars"], procedure["step_bars"]
        )
        total_evaluated_bars = len(folds) * procedure["validate_bars"]
    else:
        total_evaluated_bars = expected_bars

    floor = frequency_scaled_min_trades(
        total_evaluated_bars=total_evaluated_bars, bars_per_day=bars_per_day
    )
    if criterion["min_total_trades"] < floor:
        raise PreregistrationError(
            f"primary_criterion.min_total_trades ({criterion['min_total_trades']}) is below "
            f"CLAUDE.md's frequency-scaled floor for this geometry ({floor}, from "
            f"{total_evaluated_bars} evaluated bars at {bars_per_day}/day) -- the floor is "
            "human-approved; register a stricter value or fix the geometry"
        )


def validate_preregistration(config: Any) -> None:
    """Raise `PreregistrationError` unless `config` is a complete, internally
    consistent registration.

    Validates the **artifact**, never the research: it checks that every
    required claim is present, well-typed and consistent with the other
    claims, and that the four Eligibility Bar constants it restates are not
    laxer than the human-approved ones (see the module docstring). It has no
    opinion on whether the hypothesis is any good.
    """
    mapping = _require_mapping(config, "preregistration")

    unknown = sorted(set(mapping) - set(REQUIRED_TOP_LEVEL_KEYS) - set(OPTIONAL_TOP_LEVEL_KEYS))
    if unknown:
        raise PreregistrationError(
            f"unknown top-level key(s) {unknown}: a pre-registration may not carry claims no "
            f"code and no reader checks. Known: {sorted(REQUIRED_TOP_LEVEL_KEYS)}; "
            f"optional: {sorted(OPTIONAL_TOP_LEVEL_KEYS)}"
        )
    for key in REQUIRED_TOP_LEVEL_KEYS:
        _require_present(mapping, key)

    for key in (
        "preregistration_id",
        "registered_at",
        "strategy_family",
        "strategy_id",
        "strategy_version",
        "hypothesis",
        "stopping_rule",
    ):
        _require_non_blank_string(mapping[key], key)
    _validate_entry_point(mapping["strategy_entry_point"])
    _require_string_list(mapping["prior_art"], "prior_art")
    _require_string_list(mapping["secondary_reported_not_gating"], "secondary_reported_not_gating")

    split, expected_bars = _validate_data(mapping["data"])
    kind = _validate_criterion(mapping["primary_criterion"], split=split)
    procedure = _validate_procedure(mapping["procedure"], kind=kind)

    candidates = enumerate_candidates(mapping["parameter_grid"])
    total_candidates = _require_int(mapping["total_candidates"], "total_candidates", minimum=1)
    if total_candidates != len(candidates):
        raise PreregistrationError(
            f"total_candidates ({total_candidates}) does not match the enumerated grid "
            f"({len(candidates)} candidate(s)) -- the declared count is what a run is checked "
            "against, so a count that disagrees with its own grid makes that check meaningless"
        )
    _require_int(mapping["free_parameter_count"], "free_parameter_count", minimum=0)

    if _require_float(
        mapping["declared_detection_floor_sharpe"], "declared_detection_floor_sharpe"
    ) <= 0:
        raise PreregistrationError(
            "declared_detection_floor_sharpe must be positive -- it is the smallest true "
            "annualized Sharpe the registered window could distinguish from zero, and writing "
            "it down before the run is what all 117 prior trials never did"
        )
    _validate_declared_power(mapping["declared_power"])
    _validate_outcome_interpretation(mapping["outcome_interpretation"])
    _validate_trade_floor(
        mapping["primary_criterion"], procedure, kind=kind, expected_bars=expected_bars
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def file_sha256(path: str | Path) -> str:
    """SHA-256 of a file's raw bytes.

    Bytes, not parsed content: the point is that the log records exactly
    what was on disk when the run happened, so *any* later edit -- including
    one that changes no field -- is detectable by comparison.
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Preregistration:
    """A validated registration, the path it came from, and the SHA-256 of
    that file's bytes as read.

    `sha256` is computed at load time, which for `run_preregistered` is run
    time -- so the value threaded into `runs/experiments.jsonl` is the hash
    of the file the run actually read, not of whatever the file says later.
    """

    path: Path
    sha256: str
    config: dict

    @property
    def preregistration_id(self) -> str:
        return self.config["preregistration_id"]

    @property
    def strategy_id(self) -> str:
        return self.config["strategy_id"]

    @property
    def strategy_version(self) -> str:
        return self.config["strategy_version"]

    @property
    def strategy_family(self) -> str:
        return self.config["strategy_family"]

    @property
    def strategy_entry_point(self) -> str:
        return self.config["strategy_entry_point"]

    @property
    def data(self) -> dict:
        return self.config["data"]

    @property
    def procedure(self) -> dict:
        return self.config["procedure"]

    @property
    def primary_criterion(self) -> dict:
        return self.config["primary_criterion"]

    @property
    def criterion_kind(self) -> str:
        return self.primary_criterion["kind"]

    @property
    def is_holdout_confirmation(self) -> bool:
        return self.data["split"] == SPLIT_HOLDOUT

    @property
    def parameter_grid(self):
        return self.config["parameter_grid"]

    @property
    def total_candidates(self) -> int:
        return self.config["total_candidates"]

    @property
    def fee_bps(self) -> Decimal:
        return Decimal(str(self.procedure["fee_bps"]))

    @property
    def slippage_bps(self) -> Decimal:
        return Decimal(str(self.procedure["slippage_bps"]))

    @property
    def expected_fold_count(self) -> int | None:
        """How many folds the registered geometry produces over the
        registered bar count -- `None` for a holdout registration, which has
        no folds at all.

        Reported so a runner can warn when it falls below the Bar's 8-10
        credibility floor. Deliberately not a validation failure: see the
        module docstring.
        """
        if self.is_holdout_confirmation:
            return None
        return len(
            generate_folds(
                self.data["expected_bars"],
                self.procedure["train_bars"],
                self.procedure["validate_bars"],
                self.procedure["step_bars"],
            )
        )

    def candidates(self) -> list[dict]:
        return enumerate_candidates(self.parameter_grid)

    def candidate_rows(self) -> list[list]:
        return candidate_rows(self.parameter_grid)

    def parameter_names(self) -> list[str]:
        return parameter_names(self.parameter_grid)

    def run_params(self) -> dict:
        """The `params` mapping a pre-registered run passes to
        `run_walk_forward` (and therefore to every `fit()` call).

        Deliberately carries the grid in the shape this project's existing
        `TrainableStrategy` implementations already consume
        (`params["candidates"]` as value lists -- the real log records e.g.
        `{"candidates": [[15], [20], [25], [30]]}`), plus the declared
        parameter names so a strategy can read them by name, plus the
        registration's own identity so the `params` blob logged with the run
        is self-describing.
        """
        return {
            "candidates": self.candidate_rows(),
            "parameter_names": self.parameter_names(),
            "total_candidates": self.total_candidates,
            "preregistration_id": self.preregistration_id,
            "symbol": self.data["symbol"],
        }

    def to_dict(self) -> dict:
        return {
            "preregistration_id": self.preregistration_id,
            "preregistration_sha256": self.sha256,
            "path": str(self.path),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_family": self.strategy_family,
            "criterion_kind": self.criterion_kind,
            "total_candidates": self.total_candidates,
            "expected_fold_count": self.expected_fold_count,
            "config": dict(self.config),
        }


def load_preregistration(path: str | Path) -> Preregistration:
    """Read, validate and hash a registration file.

    Raises `PreregistrationError` for unparseable JSON, for anything
    `validate_preregistration` rejects, and when `preregistration_id` does
    not match the filename stem -- the id is how a run and a log record
    refer to this file, so an id that disagrees with where it lives makes
    two files able to claim the same registration.
    """
    file_path = Path(path)
    raw = file_path.read_bytes()  # FileNotFoundError propagates, as it should
    try:
        config = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PreregistrationError(f"{file_path}: not valid UTF-8 JSON: {exc}") from exc

    validate_preregistration(config)

    if config["preregistration_id"] != file_path.stem:
        raise PreregistrationError(
            f"preregistration_id={config['preregistration_id']!r} does not match the filename "
            f"stem {file_path.stem!r} -- the id is how runs and log records refer to this file"
        )

    return Preregistration(
        path=file_path, sha256=hashlib.sha256(raw).hexdigest(), config=dict(config)
    )


# ---------------------------------------------------------------------------
# Is the registration actually committed?
# ---------------------------------------------------------------------------


def warn_if_uncommitted(path: str | Path) -> bool | None:
    """Best-effort check that the registration is committed and unmodified.

    Returns `True` when clean, `False` when the file has uncommitted changes
    **or has never been committed at all**, and `None` when git could not
    answer (not installed, not a checkout) -- never a raised exception, and
    never a block: the sha recorded on the run is the durable record, and
    this check exists to make a soft failure visible rather than to prevent
    it. Same defensive `try/except (OSError, SubprocessError)` shape as
    `experiment_log._git_head_sha`.

    Uses `git status --porcelain -- <path>` rather than the plainer
    `git diff --quiet -- <path>`: an **untracked** file produces no diff at
    all, so `git diff` reports "clean" for a registration that was never
    committed -- the strongest version of exactly the failure this is here
    to catch. `git status` reports it as `??`.
    """
    file_path = Path(path).resolve()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(file_path)],
            cwd=file_path.parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "warn_if_uncommitted: could not ask git about %s (%s) -- cannot confirm the "
            "pre-registration is committed; the recorded sha256 remains the durable record",
            file_path,
            exc,
        )
        return None

    if result.stdout.strip():
        logger.warning(
            "warn_if_uncommitted: %s has uncommitted changes (or is untracked): %r. A "
            "pre-registration is only evidence if it was committed BEFORE the run -- commit it, "
            "then re-run, or the recorded sha256 will point at a file no reviewer can retrieve",
            file_path,
            result.stdout.strip(),
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Checking a run against the registration
# ---------------------------------------------------------------------------

# The audit list: exactly which fields a run is compared on. Named as a
# constant so "what wasn't checked" is answerable from the code rather than
# by reading the function body. A field absent from `run_kwargs` is skipped
# (and reported in `fields_compared`'s absence), never invented.
COMPARED_RUN_FIELDS = (
    "strategy_id",
    "strategy_version",
    "strategy_family",
    "symbol",
    "interval",
    "train_bars",
    "validate_bars",
    "step_bars",
    "bars_per_day",
    "fee_bps",
    "slippage_bps",
    "funding_included",
    "is_holdout_run",
    "total_candidates",
)


@dataclass(frozen=True)
class PreregistrationCheckResult:
    """What a run deviated from, if anything. `mismatches` is a list of
    human-readable strings, each naming the field, the registered value and
    the observed one -- the same "make it visible" shape as
    `overfitting_check`'s `notes`.
    """

    preregistration_id: str
    preregistration_sha256: str
    fields_compared: list[str]
    mismatches: list[str]

    @property
    def matched(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict:
        return {
            "preregistration_id": self.preregistration_id,
            "preregistration_sha256": self.preregistration_sha256,
            "fields_compared": list(self.fields_compared),
            "mismatches": list(self.mismatches),
            "matched": self.matched,
        }


def _registered_values(prereg: Preregistration) -> dict:
    procedure = prereg.procedure
    values = {
        "strategy_id": prereg.strategy_id,
        "strategy_version": prereg.strategy_version,
        "strategy_family": prereg.strategy_family,
        "symbol": prereg.data["symbol"],
        "interval": prereg.data["interval"],
        "bars_per_day": procedure["bars_per_day"],
        "fee_bps": prereg.fee_bps,
        "slippage_bps": prereg.slippage_bps,
        "funding_included": procedure["funding_included"],
        "is_holdout_run": prereg.is_holdout_confirmation,
        "total_candidates": prereg.total_candidates,
    }
    for key in FOLD_GEOMETRY_KEYS:
        if key in procedure:
            values[key] = procedure[key]
    return values


def _equal(registered: Any, observed: Any) -> bool:
    """Compare a registered value against an observed one, tolerating the
    representational differences a JSON config and a live call legitimately
    have: `Decimal("5")` vs `5` vs `"5"` for a cost, but never `True` vs
    `1` (a bool and a count are different claims).
    """
    if isinstance(registered, bool) or isinstance(observed, bool):
        return registered is observed
    if isinstance(registered, Decimal) or isinstance(observed, Decimal):
        try:
            return Decimal(str(registered)) == Decimal(str(observed))
        except InvalidOperation:
            return False
    return registered == observed


def check_run_matches_preregistration(
    prereg: Preregistration,
    run_kwargs: Mapping[str, Any],
) -> PreregistrationCheckResult:
    """Compare a run's parameters against the registration, warning on every
    deviation and blocking exactly one.

    **Warns** (`logging.warning`, plus an entry in `mismatches`) for every
    field in `COMPARED_RUN_FIELDS` whose observed value differs from the
    registered one -- a different `strategy_id`, different fees, different
    fold geometry. This is deliberate: this project's convention is to make
    a risky situation loud and visible rather than silently prevent it
    (`holdout.py`'s `force_reclaim_reason` override,
    `overfitting_check.py`'s "a warning only, not a block"), and a
    legitimate reason to deviate does exist -- e.g. re-running a registered
    specification against a corrected fee assumption, which should be
    recorded, not forbidden.

    **Raises `GridExpansionError`** when `run_kwargs["total_candidates"]`
    **exceeds** the registered value. This is the one failure mode
    pre-registration exists to prevent -- searching more than was declared,
    after seeing results -- and it is the only check here with no judgment
    in it: two integers, one comparison. Running *fewer* candidates than
    registered is a deviation but not an expansion, so it warns.

    `funding_included` may be given directly or inferred from a truthy
    `funding_rates`, so a caller that threads the real series does not have
    to restate the flag.

    A field absent from `run_kwargs` is skipped rather than treated as a
    mismatch (it appears in neither `fields_compared` nor `mismatches`).
    **One residual gap, named rather than hidden**: a caller who omits
    `total_candidates` entirely is warned, not blocked -- absence is not an
    exceedance, and the brief's single hard block is specifically about
    exceeding a registered number. The omission is visible three ways: the
    warning, `mismatches`, and a logged `total_candidates: null` on the
    record itself.
    """
    registered = _registered_values(prereg)
    observed = dict(run_kwargs)
    if "funding_included" not in observed and "funding_rates" in observed:
        observed["funding_included"] = bool(observed["funding_rates"])

    registered_total = registered["total_candidates"]
    if "total_candidates" in observed:
        observed_total = observed["total_candidates"]
        if isinstance(observed_total, int) and not isinstance(observed_total, bool):
            if observed_total > registered_total:
                raise GridExpansionError(
                    f"total_candidates: this run reports {observed_total} candidate(s) but the "
                    f"pre-registration {prereg.preregistration_id!r} registered {registered_total} "
                    f"(sha256 {prereg.sha256}). Searching more than was declared is the one "
                    "thing pre-registration exists to prevent: it inflates the N every future "
                    "result must be deflated against, invisibly. Widen the grid in the "
                    "committed registration file -- where the change shows up in git log -- or "
                    "register a new specification with a new id."
                )

    fields_compared: list[str] = []
    mismatches: list[str] = []
    for field in COMPARED_RUN_FIELDS:
        if field not in observed or field not in registered:
            continue
        fields_compared.append(field)
        if not _equal(registered[field], observed[field]):
            mismatches.append(
                f"{field}: registered {registered[field]!r}, run reports {observed[field]!r}"
            )
    if "total_candidates" not in observed:
        mismatches.append(
            "total_candidates: registered "
            f"{registered_total!r}, run reports nothing -- the candidate count could not be "
            "verified against the registration"
        )

    for mismatch in mismatches:
        logger.warning(
            "check_run_matches_preregistration: run deviates from pre-registration %r -- %s",
            prereg.preregistration_id,
            mismatch,
        )

    return PreregistrationCheckResult(
        preregistration_id=prereg.preregistration_id,
        preregistration_sha256=prereg.sha256,
        fields_compared=fields_compared,
        mismatches=mismatches,
    )
