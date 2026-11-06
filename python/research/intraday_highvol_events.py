"""Pure EG predecision path-energy windows and censored event grouping.

The caller supplies the original calendar and authenticated observations. This
module does not read archives, certify that calendar, inspect future prices or
calculate a trading return. Energy is anchored price-path variation, not the
usual close-to-close return variance. Unknown windows never become quiet ones.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import MINUTE, Issue, SessionInputs, _clock, _require, _session, window


OFFSETS = tuple(range(15, 301, 5))
WINDOW_MINUTES = 15
HISTORY_SESSIONS = 20
QUANTILE_INDEX = 15
PriceStatus = Literal["available", "unavailable", "outside_clock"]
DecisionStatus = Literal["high", "not_high", "unavailable"]


@dataclass(frozen=True)
class WindowMetric:
    symbol: str
    opening: datetime
    offset: int
    decision_at: datetime
    price_status: PriceStatus
    price_available_at: datetime | None
    energy: Fraction | None
    signed_move: Fraction | None
    direction_efficiency: Fraction | None
    zero_path: bool | None
    relative_range: Fraction | None
    shares: Fraction | None
    volume_available_at: datetime | None
    price_issues: tuple[Issue, ...] = ()
    volume_issues: tuple[Issue, ...] = ()

    @property
    def first_hour(self) -> bool:
        return self.offset <= 60


@dataclass(frozen=True)
class HighVolDecision:
    current: WindowMetric
    expected_prior_openings: tuple[datetime, ...]
    prior_energies: tuple[Fraction | None, ...]
    threshold: Fraction | None
    status: DecisionStatus
    reason: str
    threshold_tie: bool | None
    baseline_zero: bool | None
    history_issues: tuple[Issue, ...] = ()

    @property
    def known_history_count(self) -> int:
        return sum(value is not None for value in self.prior_energies)


@dataclass(frozen=True)
class Episode:
    """An observed high cluster, possibly covering unobserved interruptions.

Only a known not-high window establishes a reset. This is therefore not a
claim that a bridged cluster was one uninterrupted physical market episode.
"""
    high_offsets: tuple[int, ...]
    left_censored: bool
    unknown_bridge_offsets: tuple[int, ...]
    trailing_unknown_offsets: tuple[int, ...]
    right_censored: bool

    @property
    def confirmed_onset(self) -> bool:
        return not self.left_censored


@dataclass(frozen=True)
class EpisodeSummary:
    symbol: str
    opening: datetime
    episodes: tuple[Episode, ...]
    unavailable_offsets: tuple[int, ...]
    observed_high_windows: int
    confirmed_onsets: int
    left_censored_clusters: int
    exact_onset_count: int | None


def measure_window(session: SessionInputs, offset: int, decision_at: datetime) -> WindowMetric:
    """Measure exactly fifteen completed minutes, without iterating a suffix.

The first OPEN is the common denominator. The first increment is first CLOSE
minus first OPEN; later increments are consecutive CLOSE differences. Price
and volume support are independent, including true zero versus missing volume.
"""
    _session(session)
    _clock(decision_at)
    _require(type(offset) is int and offset in OFFSETS, "invalid_highvol_offset")
    end = session.opening + offset * MINUTE
    _require(decision_at == end + 2 * MINUTE, "highvol_decision_clock_changed")
    if end > session.closing:
        issue = (Issue(session.symbol, end, "outside_session_clock"),)
        return WindowMetric(session.symbol, session.opening, offset, decision_at, "outside_clock",
                            None, None, None, None, None, None, None, None, issue, issue)
    observed = window(session, offset - WINDOW_MINUTES, WINDOW_MINUTES, decision_at,
                      price=True, volume=True)
    for row in observed.minutes:
        _require(row.available_at >= row.start_at + 3 * MINUTE, "highvol_source_before_availability_floor")
    price_issues = tuple(issue for issue in observed.issues if not issue.reason.startswith("volume_"))
    volume_issues = tuple(issue for issue in observed.issues if not issue.reason.startswith("price_"))
    energy = signed = efficiency = relative_range = shares = None
    zero_path = price_available = volume_available = None
    if not price_issues:
        _require(len(observed.minutes) == WINDOW_MINUTES, "highvol_price_window_incomplete")
        prices = tuple(tuple(Fraction(price) for price in row.ohlc) for row in observed.minutes)
        anchor = prices[0][0]
        closes = tuple(row[3] for row in prices)
        differences = tuple(close - before for close, before in zip(closes, (anchor,) + closes[:-1], strict=True))
        variation = sum((abs(change) for change in differences), Fraction(0))
        energy = sum((change * change for change in differences), Fraction(0)) / (anchor * anchor)
        signed = closes[-1] / anchor - 1
        zero_path = variation == 0
        efficiency = None if zero_path else abs(closes[-1] - anchor) / variation
        relative_range = (max(row[1] for row in prices) - min(row[2] for row in prices)) / anchor
        price_available = max(row.available_at for row in observed.minutes)
    if not volume_issues:
        _require(len(observed.minutes) == WINDOW_MINUTES, "highvol_volume_window_incomplete")
        shares = sum((Fraction(row.shares) for row in observed.minutes), Fraction(0))
        volume_available = max(row.available_at for row in observed.minutes)
    return WindowMetric(session.symbol, session.opening, offset, decision_at,
                        "available" if not price_issues else "unavailable", price_available,
                        energy, signed, efficiency, zero_path, relative_range, shares,
                        volume_available, price_issues, volume_issues)


def _metric(value: WindowMetric) -> None:
    _require(type(value) is WindowMetric and type(value.symbol) is str and bool(value.symbol),
             "typed_highvol_metric_required")
    _clock(value.opening)
    _clock(value.decision_at)
    _require(type(value.offset) is int and value.offset in OFFSETS
             and value.decision_at == value.opening + (value.offset + 2) * MINUTE,
             "highvol_metric_clock_changed")
    _require(value.price_status in ("available", "unavailable", "outside_clock"), "invalid_highvol_price_status")
    if value.price_status == "available":
        _require(not value.price_issues and all(type(number) is Fraction for number in
                 (value.energy, value.signed_move, value.relative_range))
                 and value.energy >= 0 and value.signed_move > -1
                 and value.relative_range >= abs(value.signed_move)
                 and type(value.zero_path) is bool and value.zero_path == (value.energy == 0),
                 "invalid_highvol_price_metrics")
        _clock(value.price_available_at)
        _require(value.opening + (value.offset + 2) * MINUTE <= value.price_available_at <= value.decision_at,
                 "highvol_metric_not_available")
        _require((value.zero_path and value.direction_efficiency is None and value.signed_move == 0)
                 or (not value.zero_path and type(value.direction_efficiency) is Fraction
                     and 0 <= value.direction_efficiency <= 1), "invalid_highvol_efficiency")
    else:
        _require(bool(value.price_issues) and all(item is None for item in
                 (value.price_available_at, value.energy, value.signed_move,
                  value.direction_efficiency, value.zero_path, value.relative_range)),
                 "unknown_highvol_price_has_payload")
    if value.shares is None:
        _require(value.volume_available_at is None and bool(value.volume_issues), "unknown_highvol_volume_without_reason")
    else:
        _require(type(value.shares) is Fraction and value.shares >= 0 and not value.volume_issues,
                 "invalid_highvol_volume")
        _clock(value.volume_available_at)
        _require(value.opening + (value.offset + 2) * MINUTE <= value.volume_available_at <= value.decision_at,
                 "highvol_volume_not_available")


def classify_window(
    current: WindowMetric,
    expected_prior_openings: tuple[datetime, ...],
    prior_metrics: Mapping[datetime, WindowMetric | None],
    *, absent_reasons: Mapping[datetime, str] | None = None,
) -> HighVolDecision:
    """Compare against the caller's exact previous twenty calendar sessions.

    A shorter original prefix is insufficient history. Missing sessions cannot
    be replaced by older observations. The caller, not this pure function,
    authenticates that the supplied calendar is the original trading calendar.
    """
    _metric(current)
    expected = expected_prior_openings
    _require(type(expected) is tuple and len(expected) <= HISTORY_SESSIONS, "invalid_highvol_history_length")
    for opening in expected:
        _clock(opening)
    _require(tuple(sorted(set(expected))) == expected
             and len({opening.date() for opening in expected}) == len(expected)
             and all(opening.date() < current.opening.date() for opening in expected),
             "invalid_highvol_prior_calendar")
    _require(isinstance(prior_metrics, Mapping) and set(prior_metrics) <= set(expected),
             "highvol_reference_outside_calendar")
    absent = {} if absent_reasons is None else absent_reasons
    _require(isinstance(absent, Mapping) and set(absent) <= set(expected)
             and all(type(reason) is str and bool(reason) for reason in absent.values()),
             "invalid_highvol_absence_reasons")
    energies, issues = [], []
    for opening in expected:
        prior = prior_metrics.get(opening)
        if prior is None:
            energies.append(None)
            issues.append(Issue(current.symbol, opening + (current.offset - WINDOW_MINUTES) * MINUTE,
                                absent.get(opening, "prior_window_absent")))
            continue
        _require(opening not in absent, "highvol_absence_has_metric")
        _metric(prior)
        _require(prior.symbol == current.symbol and prior.opening == opening and prior.offset == current.offset
                 and prior.decision_at < current.decision_at, "highvol_prior_identity_changed")
        energies.append(prior.energy)
        if prior.price_status != "available":
            # The original prior WindowMetric retains its minute-level reasons.
            # Reference that window once instead of repeating up to 300 issues
            # at every current endpoint in a full-population publication.
            issues.append(Issue(current.symbol, opening + (current.offset - WINDOW_MINUTES) * MINUTE,
                                "prior_window_price_unavailable"))
    complete = len(expected) == HISTORY_SESSIONS and not issues
    threshold = sorted(energies)[QUANTILE_INDEX] if complete else None
    tied = current.energy == threshold if complete and current.energy is not None else None
    if current.price_status != "available":
        status, reason = "unavailable", "current_price_unavailable"
    elif len(expected) != HISTORY_SESSIONS:
        status, reason = "unavailable", "insufficient_original_history"
    elif not complete:
        status, reason = "unavailable", "prior_history_unavailable"
    else:
        status = "high" if current.energy > threshold and current.energy > 0 else "not_high"
        reason = "strictly_above_q80" if status == "high" else "not_strictly_above_q80"
    return HighVolDecision(current, expected, tuple(energies), threshold, status, reason,
                           tied, threshold == 0 if complete else None, tuple(issues))


def group_episodes(decisions: tuple[HighVolDecision, ...]) -> EpisodeSummary:
    """Group the full registered grid without claiming resets across gaps.

    A first-grid high is left censored. High/unknown/high is one observed
    cluster with a bridge flag, never two confirmed onsets. Gaps can hide whole
    episodes, so the exact onset count is unknown whenever any grid point is
    unavailable or a cluster's onset is left censored.
    """
    _require(type(decisions) is tuple and len(decisions) == len(OFFSETS), "full_highvol_grid_required")
    _require(all(type(item) is HighVolDecision for item in decisions), "typed_highvol_decisions_required")
    first = decisions[0].current
    for offset, item in zip(OFFSETS, decisions, strict=True):
        _metric(item.current)
        _require(item.current.symbol == first.symbol and item.current.opening == first.opening
                 and item.current.offset == offset and item.status in ("high", "not_high", "unavailable"),
                 "highvol_episode_grid_changed")
        if item.status != "unavailable":
            _require(item.current.price_status == "available" and type(item.threshold) is Fraction
                     and item.threshold >= 0
                     and (item.status == "high") == (item.current.energy > item.threshold and item.current.energy > 0),
                     "highvol_decision_status_changed")
    episodes: list[Episode] = []
    highs: list[int] = []
    bridges: list[int] = []
    pending: list[int] = []
    unknown: list[int] = []
    left_censored = True
    previous = None
    for item in decisions:
        offset, status = item.current.offset, item.status
        if status == "high":
            if not highs:
                left_censored = previous != "not_high"
            else:
                bridges.extend(pending)
            pending = []
            highs.append(offset)
        elif status == "unavailable":
            unknown.append(offset)
            if highs:
                pending.append(offset)
        elif highs:
            episodes.append(Episode(tuple(highs), left_censored, tuple(bridges), tuple(pending), bool(pending)))
            highs, bridges, pending = [], [], []
        previous = status
    if highs:
        episodes.append(Episode(tuple(highs), left_censored, tuple(bridges), tuple(pending), True))
    confirmed = sum(item.confirmed_onset for item in episodes)
    censored = sum(item.left_censored for item in episodes)
    return EpisodeSummary(first.symbol, first.opening, tuple(episodes), tuple(unknown),
                          sum(len(item.high_offsets) for item in episodes), confirmed, censored,
                          None if unknown or censored else confirmed)
