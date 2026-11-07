"""Joint first-touch outcomes for EH's fixed 5, 15 and 30 minute questions."""

from dataclasses import dataclass
from datetime import datetime
import math

from research.intraday_ed_inputs import MINUTE, SessionInputs, _clock, _require
from research.intraday_scenario_labels import ExactPrice, HORIZONS_MINUTES, PathQuestion, PathResult, label_path


TERMINALS = ("P1", "P2", "P3")
OUTCOMES = tuple((path, horizon) for horizon in HORIZONS_MINUTES for path in TERMINALS) + (("P4", 30),)


@dataclass(frozen=True)
class TrainingOutcome:
    index: int
    mature_at: datetime


def training_outcome(results: tuple[PathResult, ...], *, before: datetime) -> TrainingOutcome | None:
    """Encode one supported label, using only training outcomes mature before a split."""
    _clock(before)
    _require(type(results) is tuple and len(results) == len(HORIZONS_MINUTES)
             and all(isinstance(row, PathResult) for row in results), "fixed_EH_horizon_results_required")
    prefix = results[0].question.question_id[:-1]
    _require(all(row.question.question_id[:-1] == prefix
                 and row.question.end_at - row.question.start_at == horizon * MINUTE
                 for row, horizon in zip(results, HORIZONS_MINUTES, strict=True)),
             "EH_horizons_do_not_share_one_question")
    if any(row.status != "resolved" for row in results):
        return None
    chosen = None
    prior_end = results[0].question.start_at
    for row, horizon in zip(results, HORIZONS_MINUTES, strict=True):
        _require(row.path in (*TERMINALS, "P4") and row.mature_at is not None
                 and row.question.start_at < row.observed_through <= row.question.end_at
                 and row.mature_at >= row.observed_through + 2 * MINUTE,
                 "invalid_resolved_EH_label")
        if chosen is None and row.path in TERMINALS:
            _require(row.observed_through > prior_end, "EH_terminal_touch_precedes_no_touch_horizon")
            chosen = (row.path, horizon, row.observed_through, row.mature_at)
        if chosen is not None:
            _require((row.path, row.observed_through, row.mature_at) == (chosen[0], chosen[2], chosen[3]),
                     "EH_terminal_path_changed_across_horizons")
        else:
            _require(row.observed_through == row.question.end_at, "EH_unfinished_no_touch_label")
        prior_end = row.question.end_at
    mature = max(row.mature_at for row in results)
    if mature >= before:
        return None
    outcome = ("P4", 30) if chosen is None else (chosen[0], chosen[1])
    return TrainingOutcome(OUTCOMES.index(outcome), mature)


def _joint_mass(weights: tuple[float, ...]) -> float:
    _require(type(weights) is tuple and len(weights) == len(OUTCOMES)
             and all(type(value) in (int, float) and 0 <= value <= 1 for value in weights),
             "invalid_EH_joint_probability")
    total = math.fsum(weights)
    _require(math.isclose(total, 1.0, rel_tol=0, abs_tol=1e-9), "EH_probability_mass_differs")
    return total


def horizon_probabilities(weights: tuple[float, ...]) -> tuple[tuple[float, float, float, float], ...]:
    """Marginalize one joint prediction, retaining unresolved-at-horizon mass."""
    total = _joint_mass(weights)
    result = []
    for horizon in HORIZONS_MINUTES:
        p1, p2, p3 = (math.fsum(weight for (label, at), weight in zip(OUTCOMES, weights, strict=True)
                                if label == path and at <= horizon) / total for path in TERMINALS)
        p4 = math.fsum(weight for (label, at), weight in zip(OUTCOMES, weights, strict=True)
                       if label == "P4" or at > horizon) / total
        result.append((p1, p2, p3, p4))
    return tuple(result)


def condition_on_observations(
    question: PathQuestion, start_close: ExactPrice | None, session: SessionInputs, *,
    cutoff_at: datetime, weights: tuple[float, ...],
) -> tuple[PathResult, tuple[float, ...] | None]:
    """Update one fixed question by eliminating paths ruled out by its visible prefix."""
    _require(question.end_at - question.start_at == 30 * MINUTE, "expected_thirty_minute_question")
    _joint_mass(weights)
    prefix = label_path(question, start_close, session, cutoff_at=cutoff_at)
    if prefix.status != "pending":
        return prefix, None
    elapsed = (prefix.observed_through - question.start_at) // MINUTE
    remaining = []
    for (path, horizon), weight in zip(OUTCOMES, weights, strict=True):
        expired_touch = path != "P4" and horizon <= elapsed
        direct_move_excluded = path == "P1" and prefix.retested
        remaining.append(0.0 if expired_touch or direct_move_excluded else weight)
    mass = math.fsum(remaining)
    return prefix, tuple(weight / mass for weight in remaining) if mass else None
