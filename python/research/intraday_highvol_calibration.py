"""Fixed reliability diagnostics for already saved out-of-sample probabilities."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import fsum, isfinite

from research.intraday_ed_inputs import InputRefusal


BIN_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


@dataclass(frozen=True)
class ProbabilityOutcome:
    probability: float | None
    target_first: bool | None
    train_rate: float | None


def _probability(value: float | None) -> bool:
    return value is None or (type(value) is float and isfinite(value) and 0 <= value <= 1)


def reliability(rows: Iterable[ProbabilityOutcome]) -> dict:
    """Use fixed bins and paired Brier losses without fitting a calibrator.

    The caller supplies each fold's training-only base rate. A tie, an
    incomplete path or a missing score remains outside the known pairs.
    """
    bins: list[list[tuple[float, bool]]] = [[] for _ in BIN_EDGES[:-1]]
    losses, model_paired, baseline_paired = [], [], []
    population = missing_forecast = missing_outcome = 0
    for row in rows:
        if (type(row) is not ProbabilityOutcome or not _probability(row.probability)
                or not _probability(row.train_rate)
                or (row.target_first is not None and type(row.target_first) is not bool)):
            raise InputRefusal("invalid_eg_probability_evidence")
        population += 1
        p, observed = row.probability, row.target_first
        missing_forecast += p is None
        missing_outcome += observed is None
        if p is None or observed is None:
            continue
        index = sum(p >= edge for edge in BIN_EDGES[1:-1])
        bins[index].append((p, observed))
        loss = (p - observed) ** 2
        losses.append(loss)
        if row.train_rate is not None:
            model_paired.append(loss)
            baseline_paired.append((row.train_rate - observed) ** 2)

    def mean(values):
        return fsum(values) / len(values) if values else None

    return {
        "population": population,
        "missing_forecast": missing_forecast,
        "missing_outcome": missing_outcome,
        "known_pairs": len(losses),
        "brier": mean(losses),
        "baseline_pairs": len(model_paired),
        "model_brier_on_baseline_pairs": mean(model_paired),
        "baseline_brier_on_same_pairs": mean(baseline_paired),
        "bins": [
            {"lower": lower, "upper": upper, "count": len(members),
             "mean_probability": mean([p for p, _ in members]),
             "target_first_rate": mean([observed for _, observed in members])}
            for lower, upper, members in zip(BIN_EDGES[:-1], BIN_EDGES[1:], bins, strict=True)
        ],
    }
