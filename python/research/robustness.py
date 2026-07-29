"""Parameter-sensitivity overfitting check.

See CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-g-overfitting-safeguards.md` ("Finding 2") for the full
design/reasoning this module implements: perturb a grid search's winning
parameter combination by +-10% and +-25% (rounded to valid integer/
discrete values, per candidate slot, one at a time) and check whether
those neighbors also perform reasonably on the same `train_klines` used
for selection -- a "flat" in-sample surface (winner and neighbors all
roughly similarly good) is a sign of a real, robust signal; a "spiky" one
(only the exact winning value works, everything nearby fails) is a
classic curve-fitting/overfitting red flag. This is an in-sample check
(same data `fit()`'s grid search already used for selection) -- it is
*not* a second out-of-sample test, and does not touch `validate_klines`
or holdout data.

Deliberately generic over any `research.walkforward.TrainableStrategy`
that follows this project's established `params["candidates"]` convention
(a list of numeric tuples that `fit()` grid-searches over and picks one
from) -- every strategy in this project as of this module
(`ma_crossover.py`, `regime_momentum.py`, `regime_momentum_risk_managed.py`,
`hourly_momentum.py`) already follows this convention, so this module
works with all of them unmodified, by construction: evaluating one
candidate is just calling `fit()` with a single-element `candidates` list,
the same mechanism every one of those `fit()` implementations already
uses internally for each grid point. This module does not import
`research.walkforward.TrainableStrategy` directly -- doing so would create
a circular import (`walkforward.py` imports this module to attach a
per-fold sensitivity result) -- so it defines its own minimal, structurally
identical `_CandidateGridTrainable` Protocol below, matching this
project's existing precedent of duplicating small pieces of shared shape
across modules rather than coupling them (see `ma_crossover.py`'s
`_data_range`/`_metrics_summary` docstrings for the same reasoning applied
elsewhere in this codebase).

`check_parameter_sensitivity` never writes to `runs/experiments.jsonl`
itself -- every candidate it evaluates (the winner, re-evaluated for an
apples-to-apples comparison, and every neighbor) goes through the wrapped
strategy's own `fit()`, which already logs each single-candidate call it's
given via that strategy's own `experiment_log.log_run` call (the same
mechanism a normal grid-search candidate is logged through). This means a
sensitivity check run against a real strategy leaves a real audit trail of
every neighbor actually evaluated, without this module needing its own
logging path.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from metrics.metrics import compute_metrics

# Same values/reasoning as `research.walkforward`'s identical constants
# (duplicated, not imported, to avoid the circular import described in
# this module's docstring).
_DEFAULT_STARTING_EQUITY = Decimal("10000")
_DEFAULT_BARS_PER_DAY = 96

# +-10% and +-25% -- CLAUDE.md's Strategy Research Methodology /
# `.planning/sr-g-overfitting-safeguards.md`'s specified perturbation
# sizes for this check. Order doesn't matter (perturb_candidate visits
# every (fraction, sign) combination regardless of iteration order).
DEFAULT_PERTURBATION_FRACTIONS: tuple[Decimal, ...] = (Decimal("0.10"), Decimal("0.25"))

# Every candidate value in this project's existing strategies is a window
# length in bars -- 1 is the smallest structurally meaningful value (a
# 0-or-negative-length window is meaningless), so this is the floor a
# perturbation clamps to rather than ever emitting a non-positive
# candidate value.
DEFAULT_MIN_CANDIDATE_VALUE = 1

# A neighbor "performs reasonably" if it's also profitable in-sample; the
# surface counts as "flat"/robust if at least this fraction of the
# successfully-evaluated neighbors clear that bar. 0.5 (a simple majority)
# is a deliberately simple, documented threshold -- "most nearby values
# also work" is the plain-language reading of "flat surface" this check
# is meant to approximate; see this module's docstring and the planning
# doc for why a more elaborate/precise threshold wasn't attempted.
DEFAULT_MIN_POSITIVE_NEIGHBOR_FRACTION = 0.5


class _CandidateGridTrainable(Protocol):
    """Structurally identical to `research.walkforward.TrainableStrategy`
    -- duplicated here, not imported, to avoid a circular import (see this
    module's docstring). Every real strategy's `Trainable` class already
    satisfies both this and the real `TrainableStrategy` Protocol
    identically -- Python's structural typing means no wiring is needed to
    make that true, only that both Protocols describe the same shape.
    """

    def fit(self, train_klines: list[Kline], params: Mapping[str, Any], *, parent_run_id: str | None) -> Strategy: ...


@dataclass(frozen=True)
class PerturbedCandidate:
    """One neighbor candidate generated by `perturb_candidate`, before
    evaluation. `perturbed_index` is the 0-based position in the candidate
    tuple that was varied to produce this neighbor (every other position
    is held at the winning candidate's value); `fraction` is the
    perturbation magnitude that produced it (the sign is implicit in
    which direction `candidate[perturbed_index]` moved relative to the
    winning candidate).
    """

    candidate: tuple[int, ...]
    perturbed_index: int
    fraction: Decimal


@dataclass(frozen=True)
class NeighborEvaluation:
    """One `PerturbedCandidate`, plus its evaluation outcome.
    `total_return`/`num_trades` are `None` (never `0` as a stand-in) when
    `error` is set -- the candidate was structurally invalid for the
    wrapped strategy (e.g. `fast >= slow`) and could not be backtested at
    all, which is a different thing than a candidate that backtested
    cleanly and simply lost money or never traded.
    """

    candidate: tuple[int, ...]
    perturbed_index: int
    fraction: Decimal
    total_return: Decimal | None
    num_trades: int | None
    error: str | None

    def to_dict(self) -> dict:
        return {
            "candidate": list(self.candidate),
            "perturbed_index": self.perturbed_index,
            "fraction": self.fraction,
            "total_return": self.total_return,
            "num_trades": self.num_trades,
            "error": self.error,
        }


@dataclass(frozen=True)
class ParameterSensitivityResult:
    """The full sensitivity-check outcome for one winning candidate.
    `is_robust`/`reason` are the primary boolean-flag-plus-explanation
    result; `neighbors` (and `winning_total_return`) are kept for a
    caller/reviewer who wants the underlying numbers, not just the
    verdict.
    """

    winning_candidate: tuple[int, ...]
    winning_total_return: Decimal
    neighbors: list[NeighborEvaluation] = field(default_factory=list)
    is_robust: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "winning_candidate": list(self.winning_candidate),
            "winning_total_return": self.winning_total_return,
            "is_robust": self.is_robust,
            "reason": self.reason,
            "neighbors": [n.to_dict() for n in self.neighbors],
        }


def _round_half_up(value: float) -> int:
    """`round()` for the half-up convention (ties round away from zero
    for a positive input), not Python's builtin `round()` (banker's
    rounding / round-half-to-even) -- see `perturb_candidate`'s docstring
    for why this matters here: several of this project's real candidate
    grids (e.g. a window length of 10, perturbed by 25%) land on an exact
    `.5` tie, and round-half-up is the more predictable, easier-to-explain
    convention for a human reading a perturbation's resulting values.
    Every candidate value in this project is a positive bar count, so this
    is never called with a negative input.
    """
    return math.floor(value + 0.5)


def perturb_candidate(
    winning_candidate: Sequence[int],
    fractions: Sequence[Decimal] = DEFAULT_PERTURBATION_FRACTIONS,
    min_value: int = DEFAULT_MIN_CANDIDATE_VALUE,
) -> list[PerturbedCandidate]:
    """Generate neighbor candidates around `winning_candidate` by
    perturbing exactly one position at a time (every other position held
    fixed at the winning value) by each `+-fraction` in `fractions`,
    rounded half-up to the nearest int and clamped to `>= min_value`.

    Deduplicates: a perturbation that rounds back to the winning
    candidate itself, or to a candidate already produced by a different
    (position, fraction, sign) combination, is not repeated in the
    output -- the caller gets each distinct neighbor exactly once,
    regardless of how many ways it was reachable.

    Raises `ValueError` for an empty `winning_candidate` or `fractions`,
    or a non-positive fraction -- a perturbation of "nothing" or "by
    zero/a negative amount" has no sensible meaning here.
    """
    if not winning_candidate:
        raise ValueError("winning_candidate must be non-empty")
    if not fractions:
        raise ValueError("fractions must be non-empty")

    winning = tuple(winning_candidate)
    seen: set[tuple[int, ...]] = {winning}
    results: list[PerturbedCandidate] = []

    for index, value in enumerate(winning):
        for fraction in fractions:
            if fraction <= 0:
                raise ValueError(f"perturbation fractions must be positive, got {fraction}")
            for sign in (1, -1):
                multiplier = 1.0 + sign * float(fraction)
                perturbed_value = max(min_value, _round_half_up(value * multiplier))
                candidate = winning[:index] + (perturbed_value,) + winning[index + 1 :]
                if candidate in seen:
                    continue
                seen.add(candidate)
                results.append(PerturbedCandidate(candidate=candidate, perturbed_index=index, fraction=fraction))

    return results


def _evaluate_candidate(
    strategy: _CandidateGridTrainable,
    base_params: Mapping[str, Any],
    candidate: tuple[int, ...],
    train_klines: list[Kline],
    *,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    starting_equity: Decimal,
    bars_per_day: int,
    parent_run_id: str | None,
) -> tuple[Decimal | None, int | None, str | None]:
    """Force `strategy.fit()`'s own grid search down to exactly this one
    `candidate` (via `params["candidates"] = [candidate]` -- the same
    mechanism every real `Trainable.fit()` implementation already uses
    per grid point, so this reuses each strategy's real construction/
    validation/logging path rather than reimplementing it), then re-scores
    the returned bound `Strategy` via the existing, already
    lookahead-safety-tested `backtest.engine.run_backtest` +
    `metrics.metrics.compute_metrics` against `train_klines` -- the same
    in-sample scoring convention (total return) every real strategy's
    `fit()` already uses for its own candidate selection.

    Returns `(total_return, num_trades, None)` on success, or
    `(None, None, str(exception))` if `strategy.fit()` (or anything after
    it) raises for this candidate -- e.g. a structurally invalid candidate
    like `fast >= slow` -- so one bad neighbor can't crash the whole
    sensitivity check.
    """
    candidate_params = {**base_params, "candidates": [candidate]}
    try:
        bound_strategy = strategy.fit(train_klines, candidate_params, parent_run_id=parent_run_id)
        backtest_result = run_backtest(train_klines, bound_strategy, fee_bps, slippage_bps)
        candidate_metrics = compute_metrics(
            train_klines,
            backtest_result.filled_intents,
            backtest_result.fills,
            starting_equity,
            bars_per_day=bars_per_day,
        )
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any failure for one
        # candidate must degrade to "this neighbor couldn't be evaluated",
        # never abort the whole sensitivity check (see docstring above).
        return None, None, str(exc)

    return candidate_metrics.total_return, candidate_metrics.num_trades, None


def check_parameter_sensitivity(
    strategy: _CandidateGridTrainable,
    base_params: Mapping[str, Any],
    winning_candidate: Sequence[int],
    train_klines: list[Kline],
    *,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    starting_equity: Decimal = _DEFAULT_STARTING_EQUITY,
    bars_per_day: int = _DEFAULT_BARS_PER_DAY,
    fractions: Sequence[Decimal] = DEFAULT_PERTURBATION_FRACTIONS,
    min_candidate_value: int = DEFAULT_MIN_CANDIDATE_VALUE,
    min_positive_neighbor_fraction: float = DEFAULT_MIN_POSITIVE_NEIGHBOR_FRACTION,
    parent_run_id: str | None = None,
) -> ParameterSensitivityResult:
    """Evaluate `winning_candidate` and every neighbor `perturb_candidate`
    generates for it, all on the exact same `train_klines` (never
    `validate_klines` or holdout data -- this checks the shape of the
    in-sample optimization surface, not a second out-of-sample test), and
    decide whether the surface looks "flat" (robust) or "spiky" (likely
    overfit) around the winner.

    Robustness rule (documented explicitly, not left implicit -- see this
    module's `DEFAULT_MIN_POSITIVE_NEIGHBOR_FRACTION` docstring for the
    reasoning): if the winning candidate itself isn't profitable
    in-sample, "robust" isn't a meaningful claim to make about it either
    way (`is_robust=False`, `reason` says so explicitly, distinct from a
    genuinely-tested-and-failed spiky surface). Otherwise, `is_robust` is
    `True` iff at least `min_positive_neighbor_fraction` of the
    successfully-evaluated neighbors are *also* profitable in-sample --
    "most nearby parameter values also work" (flat) vs. "only the exact
    winner works" (spiky). A neighbor that couldn't be evaluated at all
    (an invalid candidate for this strategy, e.g. `fast >= slow`) is
    excluded from that fraction entirely -- "invalid" is a different
    claim than "valid but unprofitable".

    `parent_run_id` (default `None`) is passed straight through to every
    `strategy.fit()` call this function makes (the winner's re-evaluation
    and every neighbor's), so a caller can attribute all of them to a
    shared lineage id. **`research.walkforward.run_walk_forward`'s own
    `sensitivity_extractor` wiring must never pass its fold's bare
    `run_id` here** -- doing so would put this call's single-candidate
    `fit()` calls under the exact same `parent_run_id` as that same fold's
    real grid-search candidates (which share one `total_candidates`
    describing the real grid size), and `research.overfitting_check`
    groups records by `parent_run_id` assuming one consistent
    `total_candidates` per group -- mixing the two in would corrupt that
    count (a real bug caught by running this against the real accumulated
    experiment log during this feature's own development; see
    `.planning/sr-g-overfitting-safeguards.md`).

    `run_walk_forward` therefore passes
    `f"{overfitting_check.SENSITIVITY_PARENT_RUN_ID_PREFIX}{run_id}"` --
    a *distinct* id from the real grid's, so the group-corruption problem
    above stays solved, while making these records self-identifying as
    post-selection diagnostics rather than indistinguishable from real
    standalone runs (Strategy Research Task P; supersedes sr-g's original
    `parent_run_id=None` compromise, which achieved the first property but
    not the second -- see `.planning/sr-p-trial-accounting.md`). Records
    tagged this way are counted as `TrialKind.SENSITIVITY_PROBE` and
    reported separately from the selection-trial `N`, since a
    post-selection perturbation never influenced which candidate was
    chosen and so is not a "trial" in Bailey et al.'s sense.
    """
    winning = tuple(winning_candidate)
    winning_return, _winning_trades, winning_error = _evaluate_candidate(
        strategy,
        base_params,
        winning,
        train_klines,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        starting_equity=starting_equity,
        bars_per_day=bars_per_day,
        parent_run_id=parent_run_id,
    )
    if winning_error is not None:
        # The winning candidate came from the strategy's own fit() grid
        # search, so it must be re-evaluable here -- a failure at this
        # step means base_params/winning_candidate don't actually
        # describe a valid call for this strategy, which is a caller
        # error, not a "spiky surface" finding.
        raise ValueError(f"could not re-evaluate the winning candidate {winning}: {winning_error}")

    perturbed = perturb_candidate(winning, fractions=fractions, min_value=min_candidate_value)
    neighbors: list[NeighborEvaluation] = []
    for p in perturbed:
        total_return, num_trades, error = _evaluate_candidate(
            strategy,
            base_params,
            p.candidate,
            train_klines,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            starting_equity=starting_equity,
            bars_per_day=bars_per_day,
            parent_run_id=parent_run_id,
        )
        neighbors.append(
            NeighborEvaluation(
                candidate=p.candidate,
                perturbed_index=p.perturbed_index,
                fraction=p.fraction,
                total_return=total_return,
                num_trades=num_trades,
                error=error,
            )
        )

    valid_neighbors = [n for n in neighbors if n.error is None]

    if winning_return <= 0:
        is_robust = False
        reason = (
            f"winning candidate {winning} is not profitable in-sample "
            f"(total_return={winning_return}) -- robustness is not meaningful to claim "
            "for a losing baseline"
        )
    elif not valid_neighbors:
        is_robust = False
        reason = "no neighbor candidate could be evaluated (all perturbations were invalid for this strategy)"
    else:
        positive_count = sum(1 for n in valid_neighbors if n.total_return is not None and n.total_return > 0)
        positive_fraction = positive_count / len(valid_neighbors)
        is_robust = positive_fraction >= min_positive_neighbor_fraction
        reason = (
            f"{positive_count}/{len(valid_neighbors)} valid neighbors "
            f"({positive_fraction:.0%}) were also profitable in-sample "
            f"(threshold: {min_positive_neighbor_fraction:.0%})"
        )

    return ParameterSensitivityResult(
        winning_candidate=winning,
        winning_total_return=winning_return,
        neighbors=neighbors,
        is_robust=is_robust,
        reason=reason,
    )
