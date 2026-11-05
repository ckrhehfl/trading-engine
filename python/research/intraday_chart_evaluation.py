"""EF chronological forecast and matched-lot diagnostics, with no file IO.

The caller binds complete saved forecasts before opening held-out outcomes.
Returns use retrospective stored OPENs, not actual fills or account PnL.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from itertools import groupby
from math import fsum, isclose, isfinite, sqrt
from statistics import correlation, stdev

from research.intraday_chart_folds import ENTRY_OFFSETS, HEAD_HORIZONS, FoldSelection, Key, Labels, ObservedLabel
from research.intraday_chart_management import UPDATE_MINUTES
from research.intraday_chart_model_inputs import ADMISSION_PCT, LGB_ARMS, TCN_ARMS
from research.intraday_chart_paired_paths import PathResult, paired_exit_paths
from research.intraday_ed_inputs import InputRefusal


CONTROLS = ("TRAIN_COMMON_GROSS30_MEAN", "LAST15_CHART_RETURN")
ARMS = (*TCN_ARMS, *LGB_ARMS, *CONTROLS)
WIDE_ARMS = ("TCN_CHART", "LGB_CHART")
MANAGEMENT_ARMS = ("TCN_CHART", "TCN_CHART_PEER")
CONTRASTS = (
    ("TCN_CHART_PEER", "TCN_CHART"),
    ("LGB_CHART_PEER", "LGB_CHART"),
    ("TCN_CHART", "TCN_CHART_ORDER_REMOVED"),
    ("TCN_CHART", "LGB_CHART"),
    ("TCN_CHART_PEER", "LGB_CHART_PEER"),
    *((arm, control) for arm in MANAGEMENT_ARMS for control in CONTROLS),
)
COST_BPS = (33, 43)
SUPPORT = ("outside_clock", "chart_unavailable", "chart_only", "common")
MIN_INTERVAL_SESSIONS = 10
NORMAL_95 = 1.959963984540054
NUMERICAL_ZERO = 1e-12


@dataclass(frozen=True)
class FoldForecasts:
    selection: FoldSelection
    # Gross 30-minute return in percentage points. Include explicit None on
    # every common and chart-only entry for each arm, even if unsupported.
    forecasts: Mapping[str, Mapping[Key, float | None]]
    # Gross 15-minute updates, keyed by update bar-end (entry offset + step).
    # Overlapping entry histories share the same clock-keyed prediction.
    holding: Mapping[str, Mapping[Key, float | None]]


@dataclass(frozen=True)
class _Row:
    key: Key
    fold: int
    support: str
    predictions: tuple[float | None, ...]
    outcomes: tuple[float | None, ...]

    @property
    def day(self) -> str:
        return self.key[0]

    @property
    def actual(self) -> float | None:
        return self.outcomes[0]


@dataclass(frozen=True)
class _Lot:
    day: str
    arm: str
    cost: int
    fixed: PathResult
    dynamic: PathResult


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal(reason)


def _forecast(value: float | None) -> bool:
    return value is None or (type(value) is float and isfinite(value))


def _admitted(value: float | None) -> bool:
    return value is not None and value > ADMISSION_PCT


def _week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def _validate_folds(folds: Sequence[FoldForecasts]) -> tuple[str, dict[int, tuple[str, ...]]]:
    _need(0 < len(folds) <= 5, "one_to_five_ef_evaluation_folds_required")
    scenario = folds[0].selection.scenario
    sessions = {}
    last_day = ""
    for item in folds:
        fold = item.selection.fold
        days = fold.test_sessions
        _need(scenario in ("S1", "S2") and item.selection.scenario == scenario
              and fold.index in range(1, 6) and fold.index > max(sessions, default=0)
              and 0 < len(days) <= 32 and tuple(sorted(set(days))) == days and days[0] > last_day,
              "ordered_nonoverlapping_ef_evaluation_folds_required")
        _need(set(item.forecasts) == set(ARMS) and set(item.holding) == set(MANAGEMENT_ARMS),
              "registered_ef_forecast_arms_required")
        keys = set()
        last = None
        allowed_updates = {arm: set() for arm in MANAGEMENT_ARMS}
        for row in item.selection.evaluation:
            day, symbol, offset = row.key
            position = day, offset, symbol
            _need(day in days and offset in ENTRY_OFFSETS and row.status in SUPPORT
                  and (last is None or position > last), "ordered_original_ef_evaluation_coordinates_required")
            last = position
            keys.add(row.key)
            for arm, entries in item.forecasts.items():
                if row.status in ("common", "chart_only"):
                    _need(row.key in entries, "explicit_ef_forecast_unavailability_required")
                forecast = entries.get(row.key)
                _need(_forecast(forecast), "finite_ef_forecast_required")
                if forecast is not None:
                    # The historical-mean and learned models require fit support.
                    _need((item.selection.supported or arm == CONTROLS[1]) and
                          (row.status == "common" or row.status == "chart_only" and arm in WIDE_ARMS),
                          "ef_forecast_outside_registered_support")
                if row.status == "common" and arm in allowed_updates and _admitted(forecast):
                    allowed_updates[arm].update((day, symbol, offset + step) for step in UPDATE_MINUTES)
        _need(all(entries.keys() <= keys for entries in item.forecasts.values()), "extra_ef_forecast_coordinate")
        for arm, updates in item.holding.items():
            _need(updates.keys() == allowed_updates[arm] and all(map(_forecast, updates.values())),
                  "explicit_ef_holding_forecast_coordinates_required")
        sessions[fold.index] = days
        last_day = days[-1]
    return scenario, sessions


def _outcomes(heads: Labels) -> tuple[float | None, ...]:
    _need(type(heads) is tuple and len(heads) == len(HEAD_HORIZONS), "six_ef_evaluation_heads_required")
    values = []
    for head in heads:
        _need(head is None or (type(head) is ObservedLabel and type(head.value) is Fraction),
              "exact_ef_evaluation_outcome_required")
        value = None if head is None else float(100 * head.value)
        _need(_forecast(value), "finite_ef_outcome_required")
        values.append(value)
    return tuple(values)


def _collect(folds, labels_at, quotes) -> tuple[list[_Row], list[_Lot]]:
    rows, lots = [], []
    for item in folds:
        for entry in item.selection.evaluation:
            key = entry.key
            heads = labels_at(key)
            values = _outcomes(heads)
            predictions = tuple(item.forecasts[arm].get(key) for arm in ARMS)
            rows.append(_Row(key, item.selection.fold.index, entry.status, predictions, values))
            if entry.status != "common":
                continue
            for arm in MANAGEMENT_ARMS:
                if not _admitted(predictions[ARMS.index(arm)]):
                    continue
                previous = None
                for cost in COST_BPS:
                    fixed, dynamic = paired_exit_paths(quotes, key, cost_bps=cost,
                                                       gross15_at=item.holding[arm].__getitem__)
                    if fixed.status != "entry_declined":
                        expected_return = None if heads[0] is None else heads[0].value
                        _need(fixed.gross_return == expected_return, "ef_fixed_path_and_label_differ")
                    _need(previous is None or previous == (fixed.original_quantity, dynamic.original_quantity),
                          "ef_cost_changed_initial_paired_quantity")
                    previous = fixed.original_quantity, dynamic.original_quantity
                    lots.append(_Lot(key[0], arm, cost, fixed, dynamic))
    return rows, lots


def _estimate(values: Sequence[tuple[str, float]], sessions: Sequence[str]) -> dict:
    n = len(values)
    counts: Counter[str] = Counter()
    sums: dict[str, list[float]] = defaultdict(list)
    for day, value in values:
        _need(isfinite(value), "finite_ef_comparison_value_required")
        counts[day] += 1
        sums[day].append(value)
    groups = len(sessions)
    constant = n and all(value == values[0][1] for _, value in values)
    center = values[0][1] if constant else fsum(value for _, value in values) / n if n else None
    naive = stdev(value for _, value in values) / sqrt(n) if n > 1 else None
    clustered = None
    if groups > 1 and n:
        cluster_residuals = (fsum(value - center for value in sums[day]) for day in sessions)
        clustered = sqrt(groups / (groups - 1) * fsum(x * x for x in cluster_residuals)) / n
    precise = len(counts) >= MIN_INTERVAL_SESSIONS and clustered is not None and clustered > NUMERICAL_ZERO
    return {
        "observations": n, "reference_sessions": groups, "contributing_sessions": len(counts),
        "largest_session_observation_share": max(counts.values()) / n if n else None,
        "mean": center, "naive_se_diagnostic_only": naive, "session_cluster_se": clustered,
        "cluster_to_naive_se_ratio": clustered / naive if clustered is not None and naive is not None and naive > NUMERICAL_ZERO else None,
        "normal95_exploratory": [center - NORMAL_95 * clustered, center + NORMAL_95 * clustered] if precise else None,
        "precision": "estimated" if precise else "insufficient_precision" if n else "no_observations",
    }


def _ranks(values: Sequence[float]) -> list[float]:
    result = [0.0] * len(values)
    position = 1
    for _, group in groupby(sorted(range(len(values)), key=values.__getitem__), key=values.__getitem__):
        indices = list(group)
        rank = position + (len(indices) - 1) / 2
        for index in indices:
            result[index] = rank
        position += len(indices)
    return result


def _rank_scores(points: Sequence[tuple[str, float, float]]) -> dict[str, float]:
    scores = {}
    for day, day_points in groupby(points, key=lambda point: point[0]):
        group = list(day_points)
        actual = [row[2] for row in group]
        predicted = [row[1] for row in group]
        if len(set(actual)) > 1 and len(set(predicted)) > 1:
            scores[day] = correlation(_ranks(predicted), _ranks(actual))
    return scores


def _forecast_summary(points: Sequence[tuple[str, float, float]], sessions: Sequence[str], ranks=None) -> dict:
    signed = [(day, forecast - actual) for day, forecast, actual in points]
    if ranks is None:
        ranks = list(_rank_scores(points).items())
    return {"signed_error_pp": _estimate(signed, sessions),
            "absolute_error_pp": _estimate([(day, abs(error)) for day, error in signed], sessions),
            "squared_error_pp2": _estimate([(day, error * error) for day, error in signed], sessions),
            "within_session_spearman": _estimate(ranks, sessions)}


def _frequency(sessions, admitted, observed) -> dict:
    weeks = Counter(_week(day) for day in sessions)
    admissions = Counter(_week(row.day) for row in admitted)
    outcomes = Counter(_week(row.day) for row in observed)
    return {week: {"observed_sessions": count, "admitted_opportunities": admissions[week],
                   "observed_outcomes": outcomes[week]} for week, count in sorted(weeks.items())}


def _outcome_status(scored: int, admitted: int, known: int) -> str:
    if not scored:
        return "prediction_unavailable"
    if not admitted:
        return "no_trade"
    if not known:
        return "missing_outcome"
    return "observed_complete" if known == admitted else "observed_partial"


def _arm_summary(rows, arm: str, sessions) -> dict:
    index = ARMS.index(arm)
    scored = [row for row in rows if row.predictions[index] is not None]
    admitted = [row for row in scored if _admitted(row.predictions[index])]
    known = [row for row in admitted if row.actual is not None]
    points = [(row.day, row.predictions[index], row.actual) for row in scored if row.actual is not None]
    return {
        "support_rows": len(rows), "scored": len(scored), "scored_missing_outcome": len(scored) - len(points),
        "admitted": len(admitted), "admitted_missing_outcome": len(admitted) - len(known),
        "status": _outcome_status(len(scored), len(admitted), len(known)),
        "forecasts": _forecast_summary(points, sessions),
        "observed_gross_pp": _estimate([(row.day, row.actual) for row in known], sessions),
        "observed_net_pp": {str(cost): _estimate([(row.day, row.actual - cost / 100) for row in known], sessions)
                            for cost in COST_BPS},
        "weeks": _frequency(sessions, admitted, known),
    }


def _pair_summary(rows, left: str, right: str, sessions) -> dict:
    li, ri = ARMS.index(left), ARMS.index(right)
    shared = [(row, row.predictions[li], row.predictions[ri]) for row in rows
              if row.predictions[li] is not None and row.predictions[ri] is not None]
    scored = [(row, lhs, rhs) for row, lhs, rhs in shared if row.actual is not None]
    left_points = [(row.day, lhs, row.actual) for row, lhs, _ in scored]
    right_points = [(row.day, rhs, row.actual) for row, _, rhs in scored]
    left_ranks, right_ranks = _rank_scores(left_points), _rank_scores(right_points)
    ranks = [(day, left_ranks[day] - right_ranks[day]) for day in left_ranks if day in right_ranks]
    left_metrics = _forecast_summary(left_points, sessions, [(day, left_ranks[day]) for day, _ in ranks])
    right_metrics = _forecast_summary(right_points, sessions, [(day, right_ranks[day]) for day, _ in ranks])
    policies = [(row, _admitted(lhs), _admitted(rhs)) for row, lhs, rhs in shared]
    eligible = [(row, lhs, rhs) for row, lhs, rhs in policies if not (lhs or rhs) or row.actual is not None]

    def contribution(row, admitted, cost):
        return row.actual - cost / 100 if admitted else 0.0

    def policy_summary(cost):
        # One common observation per jointly scored opportunity. An arm which
        # explicitly declines contributes zero. Unknown trades exclude both.
        values = [(row.day, contribution(row, lhs, cost), contribution(row, rhs, cost)) for row, lhs, rhs in eligible]
        return {
            "left_mean": fsum(lhs for _, lhs, _ in values) / len(values) if values else None,
            "right_mean": fsum(rhs for _, _, rhs in values) / len(values) if values else None,
            "left_minus_right": _estimate([(day, lhs - rhs) for day, lhs, rhs in values], sessions),
        }

    return {
        "left": left, "right": right, "common_support_rows": len(rows), "jointly_scored": len(shared),
        "jointly_scored_known_outcomes": len(scored), "rank_sessions_both_defined": len(ranks),
        "left_forecasts": left_metrics, "right_forecasts": right_metrics,
        "left_minus_right_absolute_error_pp": _estimate(
            [(row.day, abs(lhs - row.actual) - abs(rhs - row.actual)) for row, lhs, rhs in scored], sessions),
        "left_minus_right_squared_error_pp2": _estimate(
            [(row.day, (lhs - row.actual) ** 2 - (rhs - row.actual) ** 2) for row, lhs, rhs in scored], sessions),
        "left_minus_right_within_session_spearman": _estimate(ranks, sessions),
        "left_admitted": sum(lhs for _, lhs, _ in policies), "right_admitted": sum(rhs for _, _, rhs in policies),
        "both_admitted": sum(lhs and rhs for _, lhs, rhs in policies),
        "both_declined": sum(not (lhs or rhs) for _, lhs, rhs in policies),
        "unknown_policy_opportunities": len(policies) - len(eligible),
        "matched_opportunity_gross_pp": policy_summary(0),
        "matched_opportunity_net_pp": {str(cost): policy_summary(cost) for cost in COST_BPS},
    }


def _path_summary(lots: Sequence[_Lot], sessions) -> dict:
    paired = [lot for lot in lots if lot.fixed.status == lot.dynamic.status == "closed"]
    outcomes = {}
    for name in ("fixed", "dynamic"):
        paths = [getattr(lot, name) for lot in lots]
        outcomes[name] = {
            "statuses": dict(Counter(path.status for path in paths)),
            "holding_decisions_by_reason": dict(Counter(decision.reason for path in paths for decision in path.decisions)),
            "exit_legs_by_reason_and_status": dict(Counter(f"{leg.reason}:{leg.status}" for path in paths for leg in path.legs)),
            "last_known_units_at_unresolved_exit": sum(path.unresolved_quantity or 0 for path in paths),
            "entry_opportunities_with_unknown_quantity": sum(path.unresolved_quantity is None for path in paths),
            "paired_gross_pp": _estimate([(lot.day, float(100 * getattr(lot, name).gross_return)) for lot in paired], sessions),
            "paired_net_pp": _estimate([(lot.day, float(100 * getattr(lot, name).net_return)) for lot in paired], sessions),
            "paired_quantity_weighted_hold_minutes": _estimate(
                [(lot.day, float(getattr(lot, name).average_hold_minutes)) for lot in paired], sessions),
        }
    for field, name, scale in (("gross_return", "gross_pp", 100), ("net_return", "net_pp", 100),
                               ("average_hold_minutes", "quantity_weighted_hold_minutes", 1)):
        outcomes[f"dynamic_minus_fixed_{name}"] = _estimate(
            [(lot.day, float(scale * (getattr(lot.dynamic, field) - getattr(lot.fixed, field)))) for lot in paired], sessions)
    return {"paired_known": len(paired), "admitted_opportunities": len(lots), **outcomes}


def _summary(rows, lots, sessions) -> dict:
    common = [row for row in rows if row.support == "common"]
    wide = [row for row in rows if row.support == "chart_only"]
    return {
        "original_coordinates": len(rows), "reference_sessions": list(sessions),
        "support": {status: sum(row.support == status for row in rows) for status in SUPPORT},
        "unavailable_label_heads": {status: [sum(row.support == status and row.outcomes[index] is None for row in rows)
                                             for index in range(len(HEAD_HORIZONS))] for status in SUPPORT},
        "common_arms": {arm: _arm_summary(common, arm, sessions) for arm in ARMS},
        "common_contrasts": {f"{left}_minus_{right}": _pair_summary(common, left, right, sessions)
                             for left, right in CONTRASTS},
        "chart_only_outside_common_diagnostic": {arm: _arm_summary(wide, arm, sessions) for arm in WIDE_ARMS},
        "matched_lots": {arm: {str(cost): _path_summary([lot for lot in lots if lot.arm == arm and lot.cost == cost], sessions)
                               for cost in COST_BPS} for arm in MANAGEMENT_ARMS},
    }


def evaluate_scenario(folds: Sequence[FoldForecasts], *, labels_at: Callable[[Key], Labels], quotes) -> dict:
    """Compare completed chronological folds without conditioning support on outcomes."""
    scenario, fold_sessions = _validate_folds(folds)
    rows, lots = _collect(folds, labels_at, quotes)
    sessions = tuple(day for days in fold_sessions.values() for day in days)
    return {
        "schema": "ef-comparison-summary-v1", "scenario": scenario,
        "method": "nominal_stored_open_opportunities_and_non_redeploying_lots",
        "rank": "average_tie_rank_spearman_within_session_constant_ranks_undefined",
        "uncertainty": "exploratory_normal95_session_cluster_sandwich_no_serial_correction",
        "wider_chart_diagnostic": "outside_common_only_same_fit_with_different_support_distribution",
        "minimum_contributing_sessions_for_interval": MIN_INTERVAL_SESSIONS,
        "se_numerical_zero": NUMERICAL_ZERO,
        "pooled": _summary(rows, lots, sessions),
        "folds": {str(index): {"training_supported": item.selection.supported,
                               "training_and_evaluation_counts": dict(item.selection.counts),
                               **_summary([row for row in rows if row.fold == index],
                                          [lot for lot in lots if lot.day in days], days)}
                  for (index, days), item in zip(fold_sessions.items(), folds, strict=True)},
    }


def _check_number(observed, expected) -> bool:
    if expected is None:
        return observed is None
    return type(observed) in (int, float) and isclose(observed, expected, rel_tol=1e-10, abs_tol=1e-12)


def _check_stat(saved, samples, sessions) -> None:
    # Reconstruct the estimator from residual lists rather than the reporting
    # reducer's per-session totals. The naive denominator is diagnostic only.
    samples = list(samples)
    n, groups = len(samples), len(sessions)
    constant = n and all(value == samples[0][1] for _, value in samples)
    center = samples[0][1] if constant else fsum(value / n for _, value in samples) if n else None
    deviations: dict[str, list[float]] = defaultdict(list)
    for day, value in samples:
        deviations[day].append(value - center)
    naive = sqrt(fsum(residual * residual for items in deviations.values() for residual in items)
                 / (n * (n - 1))) if n > 1 else None
    clustered = (sqrt(fsum((fsum(items) / n) ** 2 for items in deviations.values()) * groups / (groups - 1))
                 if n and groups > 1 else None)
    numeric = {"mean": center, "naive_se_diagnostic_only": naive, "session_cluster_se": clustered,
               "largest_session_observation_share": max(map(len, deviations.values())) / n if n else None,
               "cluster_to_naive_se_ratio": clustered / naive if clustered is not None and naive is not None and naive > NUMERICAL_ZERO else None}
    _need(saved["observations"] == n and saved["reference_sessions"] == groups
          and saved["contributing_sessions"] == len(deviations)
          and all(_check_number(saved[field], value) for field, value in numeric.items()),
          "ef_independent_summary_arithmetic_differs")
    if len(deviations) >= MIN_INTERVAL_SESSIONS and clustered is not None and clustered > NUMERICAL_ZERO:
        interval = saved["normal95_exploratory"]
        _need(saved["precision"] == "estimated" and isinstance(interval, list) and len(interval) == 2
              and _check_number(interval[0], center - NORMAL_95 * clustered)
              and _check_number(interval[1], center + NORMAL_95 * clustered), "ef_independent_interval_differs")
    else:
        _need(saved["normal95_exploratory"] is None and saved["precision"] == (
            "insufficient_precision" if n else "no_observations"), "ef_independent_precision_state_differs")


def _reference_ranks(points) -> dict[str, float]:
    def centered_ranks(values):
        ordered = sorted(values)
        return [(bisect_left(ordered, value) + bisect_right(ordered, value) - len(ordered)) / 2 for value in values]

    days = defaultdict(list)
    for day, forecast, actual in points:
        days[day].append((forecast, actual))
    result = {}
    for day, values in days.items():
        forecasts, actuals = zip(*values, strict=True)
        x, y = centered_ranks(forecasts), centered_ranks(actuals)
        scale = sqrt(fsum(value ** 2 for value in x) * fsum(value ** 2 for value in y))
        if scale:
            result[day] = fsum(a * b for a, b in zip(x, y, strict=True)) / scale
    return result


def _check_forecasts(saved, points, ranks, sessions) -> None:
    for field, transform in (("signed_error_pp", float), ("absolute_error_pp", abs),
                             ("squared_error_pp2", lambda error: error ** 2)):
        _check_stat(saved[field], ((day, transform(forecast - actual)) for day, forecast, actual in points), sessions)
    _check_stat(saved["within_session_spearman"], ranks, sessions)


def _check_arm(saved, rows, arm, sessions) -> None:
    index = ARMS.index(arm)
    forecast_rows, admitted_rows, known_rows = [], [], []
    for row in rows:
        prediction = row.predictions[index]
        if prediction is None:
            continue
        if row.actual is not None:
            forecast_rows.append((row.day, prediction, row.actual))
        if prediction > ADMISSION_PCT:
            admitted_rows.append(row)
            if row.actual is not None:
                known_rows.append(row)
    scored = sum(row.predictions[index] is not None for row in rows)
    expected_counts = {"support_rows": len(rows), "scored": scored,
                       "scored_missing_outcome": scored - len(forecast_rows), "admitted": len(admitted_rows),
                       "admitted_missing_outcome": len(admitted_rows) - len(known_rows)}
    _need(all(saved[field] == count for field, count in expected_counts.items()) and
          saved["status"] == _outcome_status(scored, len(admitted_rows), len(known_rows)), "ef_arm_counts_differ")
    expected_weeks = {week: {"observed_sessions": 0, "admitted_opportunities": 0, "observed_outcomes": 0}
                      for week in map(_week, sessions)}
    for field, days in (("observed_sessions", sessions), ("admitted_opportunities", (row.day for row in admitted_rows)),
                        ("observed_outcomes", (row.day for row in known_rows))):
        for day in days:
            expected_weeks[_week(day)][field] += 1
    _need(saved["weeks"] == expected_weeks, "ef_week_counts_differ")
    _check_forecasts(saved["forecasts"], forecast_rows, _reference_ranks(forecast_rows).items(), sessions)
    _check_stat(saved["observed_gross_pp"], ((row.day, row.actual) for row in known_rows), sessions)
    for cost in COST_BPS:
        _check_stat(saved["observed_net_pp"][str(cost)], ((row.day, row.actual - cost / 100) for row in known_rows), sessions)


def _check_pair(saved, rows, left, right, sessions) -> None:
    li, ri = ARMS.index(left), ARMS.index(right)
    left_points, right_points, decisions = [], [], []
    for row in rows:
        a, b = row.predictions[li], row.predictions[ri]
        if a is None or b is None:
            continue
        if row.actual is not None:
            left_points.append((row.day, a, row.actual))
            right_points.append((row.day, b, row.actual))
        decisions.append((row, a > ADMISSION_PCT, b > ADMISSION_PCT))
    left_ranks, right_ranks = _reference_ranks(left_points), _reference_ranks(right_points)
    rank_days = sorted(left_ranks.keys() & right_ranks.keys())
    known_decisions = [(row, a, b) for row, a, b in decisions if row.actual is not None or not a and not b]
    counts = {"common_support_rows": len(rows), "jointly_scored": len(decisions),
              "jointly_scored_known_outcomes": len(left_points), "rank_sessions_both_defined": len(rank_days),
              "left_admitted": sum(a for _, a, _ in decisions), "right_admitted": sum(b for _, _, b in decisions),
              "both_admitted": sum(a and b for _, a, b in decisions),
              "both_declined": sum(not a and not b for _, a, b in decisions),
              "unknown_policy_opportunities": len(decisions) - len(known_decisions)}
    _need((saved["left"], saved["right"]) == (left, right) and all(saved[field] == count for field, count in counts.items()),
          "ef_pair_counts_differ")
    _check_forecasts(saved["left_forecasts"], left_points, ((day, left_ranks[day]) for day in rank_days), sessions)
    _check_forecasts(saved["right_forecasts"], right_points, ((day, right_ranks[day]) for day in rank_days), sessions)
    for field, transform in (("absolute_error_pp", abs), ("squared_error_pp2", lambda error: error ** 2)):
        _check_stat(saved[f"left_minus_right_{field}"], ((day, transform(a - y) - transform(b - y))
                    for (day, a, y), (_, b, _) in zip(left_points, right_points, strict=True)), sessions)
    _check_stat(saved["left_minus_right_within_session_spearman"],
                ((day, left_ranks[day] - right_ranks[day]) for day in rank_days), sessions)
    for cost in (0, *COST_BPS):
        paired = saved["matched_opportunity_net_pp"][str(cost)] if cost else saved["matched_opportunity_gross_pp"]
        n = len(known_decisions)
        left_values = [(row.actual - cost / 100 if a else 0.0) for row, a, _ in known_decisions]
        right_values = [(row.actual - cost / 100 if b else 0.0) for row, _, b in known_decisions]
        _need(_check_number(paired["left_mean"], fsum(value / n for value in left_values) if n else None)
              and _check_number(paired["right_mean"], fsum(value / n for value in right_values) if n else None),
              "ef_independent_paired_opportunity_means_differ")
        _check_stat(paired["left_minus_right"], ((row.day, a - b) for (row, _, _), a, b in
                    zip(known_decisions, left_values, right_values, strict=True)), sessions)


def _reference_lot(path: PathResult, cost: int) -> tuple[Fraction, Fraction, Fraction] | None:
    # Reconcile against retained quantities/prices with exact Fraction cash.
    quantity, basis = path.original_quantity, path.original_notional
    if not quantity:
        _need(path.status in ("entry_unavailable", "entry_declined") and basis is None
              and path.initial_cost is None and not path.legs, "ef_independent_entry_state_differs")
    else:
        paid = Fraction(cost, 10_000) * basis
        _need(path.initial_cost == paid, "ef_independent_lot_cost_differs")
        filled = [leg for leg in path.legs if leg.status == "modeled"]
        unsold = quantity - sum(leg.quantity for leg in filled)
        _need(unsold >= 0 and path.unresolved_quantity == unsold, "ef_independent_lot_inventory_differs")
        if not unsold:
            proceeds = sum((leg.quantity * leg.price for leg in filled), Fraction())
            weighted_time = Fraction(sum(leg.quantity * leg.offset_after_entry for leg in filled), quantity)
            result = (proceeds - basis) / basis, (proceeds - basis - paid) / basis, weighted_time
            _need(path.status == "closed" and result == (path.gross_return, path.net_return, path.average_hold_minutes),
                  "ef_independent_lot_return_differs")
            return result
        _need(path.status == "unavailable" and any(leg.status == "indeterminate" for leg in path.legs),
              "ef_independent_lot_state_differs")
    _need((path.gross_return, path.net_return, path.average_hold_minutes) == (None, None, None),
          "ef_independent_unknown_lot_differs")
    return None


def _check_paths(saved, lots, sessions) -> None:
    complete = []
    for lot in lots:
        _need((lot.fixed.original_quantity, lot.fixed.original_notional, lot.fixed.initial_cost) ==
              (lot.dynamic.original_quantity, lot.dynamic.original_notional, lot.dynamic.initial_cost),
              "ef_independent_paired_entry_differs")
        fixed, dynamic = _reference_lot(lot.fixed, lot.cost), _reference_lot(lot.dynamic, lot.cost)
        if fixed is not None and dynamic is not None:
            complete.append((lot.day, fixed, dynamic))
    _need(saved["paired_known"] == len(complete) and saved["admitted_opportunities"] == len(lots),
          "ef_independent_lot_counts_differ")
    for side in ("fixed", "dynamic"):
        paths = [getattr(lot, side) for lot in lots]
        statuses, reasons, legs = Counter(), Counter(), Counter()
        unknown, unsold = 0, 0
        for path in paths:
            statuses[path.status] += 1
            reasons.update(decision.reason for decision in path.decisions)
            legs.update(f"{leg.reason}:{leg.status}" for leg in path.legs)
            if path.unresolved_quantity is None:
                unknown += 1
            else:
                unsold += path.unresolved_quantity
        _need(saved[side]["statuses"] == statuses and saved[side]["holding_decisions_by_reason"] == reasons
              and saved[side]["exit_legs_by_reason_and_status"] == legs
              and saved[side]["entry_opportunities_with_unknown_quantity"] == unknown
              and saved[side]["last_known_units_at_unresolved_exit"] == unsold, "ef_independent_lot_status_counts_differ")
    for index, (field, scale) in enumerate((("gross_pp", 100), ("net_pp", 100), ("quantity_weighted_hold_minutes", 1))):
        _check_stat(saved["fixed"][f"paired_{field}"], ((day, float(fixed[index] * scale)) for day, fixed, _ in complete), sessions)
        _check_stat(saved["dynamic"][f"paired_{field}"], ((day, float(dynamic[index] * scale)) for day, _, dynamic in complete), sessions)
        _check_stat(saved[f"dynamic_minus_fixed_{field}"], ((day, float((dynamic[index] - fixed[index]) * scale))
                    for day, fixed, dynamic in complete), sessions)


def _verify_partition(saved, rows, lots, sessions) -> None:
    common = [row for row in rows if row.support == "common"]
    wide = [row for row in rows if row.support == "chart_only"]
    _need(saved["original_coordinates"] == len(rows) and saved["reference_sessions"] == list(sessions)
          and saved["support"] == {status: sum(row.support == status for row in rows) for status in SUPPORT},
          "ef_independent_support_differs")
    missing = {status: [0] * len(HEAD_HORIZONS) for status in SUPPORT}
    for row in rows:
        for index, value in enumerate(row.outcomes):
            missing[row.support][index] += value is None
    _need(saved["unavailable_label_heads"] == missing, "ef_independent_head_counts_differ")
    for arm in ARMS:
        _check_arm(saved["common_arms"][arm], common, arm, sessions)
    for arm in WIDE_ARMS:
        _check_arm(saved["chart_only_outside_common_diagnostic"][arm], wide, arm, sessions)
    for left, right in CONTRASTS:
        _check_pair(saved["common_contrasts"][f"{left}_minus_{right}"], common, left, right, sessions)
    for arm in MANAGEMENT_ARMS:
        for cost in COST_BPS:
            _check_paths(saved["matched_lots"][arm][str(cost)], [lot for lot in lots if (lot.arm, lot.cost) == (arm, cost)], sessions)


def verify_evaluation(result: Mapping, folds: Sequence[FoldForecasts], *, labels_at: Callable[[Key], Labels], quotes) -> dict:
    """Replay evidence and verify saved primary counts, estimates and lot arithmetic.

    Evidence collection reuses the registered path kernel. The arithmetic check
    recomputes from individual observations and reconciles the kernel's legs.
    Snapshot authentication and fit/prediction replay remain caller obligations.
    """
    scenario, fold_sessions = _validate_folds(folds)
    _need(result.get("schema") == "ef-comparison-summary-v1" and result.get("scenario") == scenario
          and set(result["folds"]) == set(map(str, fold_sessions)), "ef_evaluation_summary_identity_differs")
    rows, lots = _collect(folds, labels_at, quotes)
    sessions = tuple(day for days in fold_sessions.values() for day in days)
    _verify_partition(result["pooled"], rows, lots, sessions)
    for (index, days), item in zip(fold_sessions.items(), folds, strict=True):
        saved = result["folds"][str(index)]
        _need(saved["training_supported"] is item.selection.supported
              and saved["training_and_evaluation_counts"] == dict(item.selection.counts), "ef_fold_summary_identity_differs")
        _verify_partition(saved, [row for row in rows if row.fold == index], [lot for lot in lots if lot.day in days], days)
    return {"verified": True, "scenario": scenario, "evaluation_coordinates": len(rows),
            "paired_lot_cases_across_costs": len(lots), "folds": list(fold_sessions),
            "method": "separate_summary_arithmetic_and_exact_lot_leg_reconciliation"}
