"""Pure one-way activity exclusions; no historical identity certification or IO.

The caller supplies one code's observations and the pinned BB/AU date-state
map. Every index session from BB's oldest required observation through formation
is consumed, including frozen sessions. Unknown values, unavailable provenance
or a changed date/state map prevent exclusion rather than shortening a window.

For a complete latest-normal baseline, an operating period starting on/before
its oldest observation has the same baseline; a later start has too few normal
observations. An exact activity failure therefore cannot become eligible by
moving that start later. A numeric pass proves no period, signal or eligibility.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

from research.activity_screen import ScreenObservation, _median, _published
from research.activity_timing import SessionLagPolicy


@dataclass(frozen=True)
class ActivityFailureResult:
    """A failure proof or an unresolved window, never a positive certificate.

    ``normal_dates`` lists the latest strictly preceding nonfrozen observations;
    ``frozen_dates`` lists all prior frozen sessions in the consumed interval.
    Neither list establishes an operating-company classification.
    """

    proven_failure: bool
    reason: str
    unresolved: tuple[str, ...]
    consumed_dates: tuple[date, ...]
    normal_dates: tuple[date, ...]
    frozen_dates: tuple[date, ...]
    selection_at: tuple[datetime, datetime]
    baseline: Decimal | None = None
    formation_turnover: Decimal | None = None
    required_turnover: Decimal | None = None
    historical_eligibility_certified: bool = False

    def to_dict(self) -> dict:
        """Return JSON-safe diagnostics without losing Decimal digits."""
        return {
            "proven_failure": self.proven_failure, "reason": self.reason,
            "unresolved": list(self.unresolved),
            "consumed_dates": [day.isoformat() for day in self.consumed_dates],
            "normal_dates": [day.isoformat() for day in self.normal_dates],
            "frozen_dates": [day.isoformat() for day in self.frozen_dates],
            "selection_at": {f"D{lag}": cutoff.isoformat()
                             for lag, cutoff in enumerate(self.selection_at, 1)},
            "baseline": str(self.baseline) if self.baseline is not None else None,
            "formation_turnover": (str(self.formation_turnover)
                                   if self.formation_turnover is not None else None),
            "required_turnover": (str(self.required_turnover)
                                  if self.required_turnover is not None else None),
            "historical_eligibility_certified": self.historical_eligibility_certified,
        }


def _dates(values: Sequence[date], name: str) -> tuple[date, ...]:
    days = tuple(values)
    if (any(type(day) is not date for day in days)
            or any(a >= b for a, b in zip(days, days[1:]))):
        raise ValueError(f"{name} must be strictly increasing explicit dates")
    return days


def activity_failure(
    calendar: Sequence[date], formation_on: date,
    observations: Mapping[date, ScreenObservation], *,
    bb_required_observed_dates: Sequence[date], bb_frozen_dates: Sequence[date],
    threshold: Decimal, lookback: int = 60, bb_formation_state: str = "observed",
) -> ActivityFailureResult:
    """Prove only AN activity failures under both BH 08:30 timing cutoffs.

    ``bb_required_observed_dates`` contains ALL BB required interval sessions,
    including prior frozen sessions and formation. ``bb_frozen_dates`` is the
    pinned AU prior-frozen subset, excluding formation; ``bb_formation_state``
    is the caller's AU formation state normalized to ``observed``/``frozen``.
    BB/AU reconciliation is checked here rather than silently refreshed.

    The calendar must contain D+1 and D+2. Every consumed row must have explicit
    AvailabilityMetadata available at each arm's cutoff. Legacy public times
    alone are insufficient. Invalid function parameters raise ValueError;
    unknown observations or a date/state disagreement return unresolved.
    The lookback is explicit for synthetic tests; the real runner freezes 60.
    """
    days = _dates(calendar, "index calendar")
    if not days or type(formation_on) is not date or formation_on not in days:
        raise ValueError("formation must belong to the complete index calendar")
    if type(lookback) is not int or lookback < 1:
        raise ValueError("lookback must be a positive integer")
    if (not isinstance(threshold, Decimal) or not threshold.is_finite()
            or threshold < 0):
        raise ValueError("threshold must be a finite nonnegative Decimal")
    formation_index = days.index(formation_on)
    cutoffs = tuple(SessionLagPolicy(lag).selection_at(days, formation_index)
                    for lag in (1, 2))
    expected = _dates(bb_required_observed_dates, "BB required dates")
    expected_frozen = _dates(bb_frozen_dates, "BB frozen dates")
    if not expected:
        return ActivityFailureResult(False, "unresolved_input", ("empty_BB_interval",),
                                     (), (), (), cutoffs)
    interval = tuple(day for day in days if expected[0] <= day <= formation_on)
    issues = []
    if expected != interval:
        issues.append("BB_required_dates_disagree_with_calendar_interval")
    if (not set(expected_frozen) <= set(interval)
            or formation_on in expected_frozen):
        issues.append("BB_frozen_dates_outside_prior_interval")
    if not isinstance(bb_formation_state, str) or bb_formation_state not in {"observed", "frozen"}:
        issues.append("unknown_BB_formation_state")

    normal, frozen = [], []
    formation = None
    # Do not return early for a frozen formation, a zero baseline or an already
    # failing number: a later unknown in the same interval invalidates proof.
    for day in interval:
        row = observations.get(day)
        if not isinstance(row, ScreenObservation):
            issues.append(f"missing_or_invalid_observation:{day.isoformat()}")
            continue
        state_known = isinstance(row.state, str) and row.state in {"observed", "frozen"}
        if not state_known:
            issues.append(f"unknown_observation_state:{day.isoformat()}")
        valid_amount = (isinstance(row.turnover, Decimal) and row.turnover.is_finite()
                        and row.turnover >= 0)
        if not valid_amount:
            issues.append(f"NULL_or_invalid_turnover:{day.isoformat()}")
        elif row.state == "frozen" and row.turnover != 0:
            issues.append(f"frozen_observation_has_positive_turnover:{day.isoformat()}")
        for lag, cutoff in enumerate(cutoffs, 1):
            try:
                _published(row, day, cutoff, require_metadata=True)
            except (TypeError, ValueError):
                issues.append(f"unknown_or_unavailable_metadata_D{lag}:{day.isoformat()}")
        if day == formation_on:
            formation = row
            if row.state != bb_formation_state:
                issues.append("BB_formation_state_disagreement")
        elif state_known:
            (frozen if row.state == "frozen" else normal).append(day)
    if tuple(frozen) != expected_frozen:
        issues.append("BB_frozen_dates_disagreement")
    expected_normal = tuple(day for day in expected
                            if day != formation_on and day not in expected_frozen)
    if tuple(normal) != expected_normal:
        issues.append("BB_normal_dates_disagreement")
    latest_normal = tuple(normal[-lookback:])
    if issues:
        return ActivityFailureResult(False, "unresolved_input", tuple(issues), interval,
                                     latest_normal, tuple(frozen), cutoffs)
    if formation is None:
        return ActivityFailureResult(False, "unresolved_input", ("missing_formation",),
                                     interval, latest_normal, tuple(frozen), cutoffs)
    if formation.state == "frozen":
        return ActivityFailureResult(True, "formation_frozen", (), interval,
                                     latest_normal, tuple(frozen), cutoffs,
                                     formation_turnover=formation.turnover)
    if len(latest_normal) < lookback:
        # This interval's beginning is not an independently verified operating
        # or listing start, so calendar truncation alone cannot prove shortage.
        return ActivityFailureResult(False, "unresolved_input", ("incomplete_normal_baseline",),
                                     interval, latest_normal, tuple(frozen), cutoffs,
                                     formation_turnover=formation.turnover)
    baseline = _median([observations[day].turnover for day in latest_normal])
    if baseline == 0:
        return ActivityFailureResult(True, "zero_baseline", (), interval, latest_normal,
                                     tuple(frozen), cutoffs, baseline, formation.turnover)
    with localcontext() as context:
        context.prec = max(28, len(baseline.as_tuple().digits)
                           + len(threshold.as_tuple().digits))
        required = baseline * threshold
    failure = formation.turnover < required
    return ActivityFailureResult(
        failure, "activity_below_threshold" if failure else "numeric_pass_unresolved",
        () if failure else ("operating_period_and_eligibility_uncertified",),
        interval, latest_normal, tuple(frozen), cutoffs, baseline, formation.turnover, required,
    )
