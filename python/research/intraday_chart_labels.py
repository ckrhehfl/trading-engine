"""Pure EF supervised outcomes on the chart decision clock.

These are retrospective OPEN prices in stored units. Source authentication,
price basis and cutoff selection belong to the caller. Keep this result out
of the chart and peer input views used for inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from functools import cache
from typing import Literal

from research.intraday_chart_inputs import _geometry
from research.intraday_ed_inputs import MINUTE, STATES, Issue, Minute, SessionInputs, _clock, _require


HORIZONS_MINUTES = (15, 30)
Status = Literal["available", "unavailable", "unmatured", "outside_clock"]
_MATURITY_ISSUES = ("minute_not_mature_at_cutoff", "minute_available_after_cutoff")


@dataclass(frozen=True)
class MinuteEvidence:
    start_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class PathLabel:
    """Exact extrema when complete, with evidence for each observed price used."""

    status: Status
    low_return: Fraction | None = None
    high_return: Fraction | None = None
    mature_at: datetime | None = None
    evidence: tuple[MinuteEvidence, ...] = ()
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class HorizonLabel:
    """The endpoint label and its independent path, with the declared clocks."""

    horizon_minutes: int
    entry_at: datetime
    exit_at: datetime
    status: Status
    path: PathLabel
    entry_price: Fraction | None = None
    exit_price: Fraction | None = None
    gross_return: Fraction | None = None
    mature_at: datetime | None = None
    evidence: tuple[MinuteEvidence, ...] = ()
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class ChartLabels:
    symbol: str
    opening: datetime
    closing: datetime
    bar_end: datetime
    decision_at: datetime
    label_cutoff: datetime
    horizons: tuple[HorizonLabel, ...]


def _read_minute(session: SessionInputs, at: datetime, cutoff: datetime) -> tuple[Minute | None, Issue | None]:
    # The source projects complete minutes at minute end plus two. Even the
    # retrospective OPEN of an endpoint needs that minute's observation.
    earliest = at + 3 * MINUTE
    if earliest > cutoff:
        return None, Issue(session.symbol, at, "minute_not_mature_at_cutoff")
    item = session.minutes.get(at)
    if item is None:
        return None, Issue(session.symbol, at, "missing_minute")
    _require(isinstance(item, Minute) and item.symbol == session.symbol, "minute_identity_changed")
    _clock(item.start_at)
    _clock(item.available_at)
    _require(item.start_at == at, "minute_identity_changed")
    _require(item.available_at >= earliest, "minute_available_before_label_floor")
    if item.available_at > cutoff:
        return None, Issue(session.symbol, at, "minute_available_after_cutoff")
    _require(type(item.price_state) is str and item.price_state in STATES, "invalid_observation_state")
    if item.price_state != "observed":
        _require(item.ohlc is None, "unknown_observation_has_payload")
        return None, Issue(session.symbol, at, f"price_{item.price_state}")
    prices = item.ohlc
    _require(type(prices) is tuple and len(prices) == 4, "observed_price_requires_ohlc")
    _require(all(isinstance(value, Decimal) and value.is_finite() and value > 0
                 for value in prices), "invalid_exact_price")
    op, high, low, close = prices
    _require(low <= min(op, close) <= max(op, close) <= high, "invalid_ohlc_order")
    return item, None


def _status(issues: tuple[Issue, ...]) -> Status:
    if not issues:
        return "available"
    if all(issue.reason in _MATURITY_ISSUES for issue in issues):
        return "unmatured"
    return "unavailable"


def _evidence(rows: tuple[Minute | None, ...]) -> tuple[MinuteEvidence, ...]:
    return tuple(MinuteEvidence(row.start_at, row.available_at) for row in rows if row is not None)


def _path(
    rows: tuple[Minute | None, ...], issues: tuple[Issue, ...],
    entry_price: Fraction | None, exit_price: Fraction | None,
) -> PathLabel:
    evidence = _evidence(rows)
    if issues:
        return PathLabel(_status(issues), evidence=evidence, issues=issues)
    # Exit-minute HIGH/LOW describe moves after the modeled exit OPEN.
    low = min(exit_price, *(Fraction(row.ohlc[2]) for row in rows[:-1]))
    high = max(exit_price, *(Fraction(row.ohlc[1]) for row in rows[:-1]))
    return PathLabel("available", low / entry_price - 1, high / entry_price - 1,
                     max(item.available_at for item in evidence), evidence)


def build_chart_labels(
    session: SessionInputs, bar_end: datetime, decision_at: datetime, *, label_cutoff: datetime,
) -> ChartLabels:
    """Read only admissible future coordinates for both fixed horizons.

    The entry is decision+1 and each exit is entry+horizon, strictly before
    close-15. An excluded horizon carries its clocks and geometry issue only.
    A mature endpoint return survives an unavailable path; a path needs every
    minute and the exit OPEN. A value's maturity is the latest typed availability
    among its observations. A result with missing and immature observations is
    unavailable, with both reasons retained in its issues.
    """
    offset = _geometry(session, bar_end, decision_at, False)
    _clock(label_cutoff)
    entry_at = session.opening + (offset + 3) * MINUTE

    @cache
    def observe(at: datetime) -> tuple[Minute | None, Issue | None]:
        return _read_minute(session, at, label_cutoff)

    horizons = []
    for horizon in HORIZONS_MINUTES:
        exit_at = entry_at + horizon * MINUTE
        if exit_at >= session.closing - 15 * MINUTE:
            issues = (Issue(session.symbol, exit_at, "exit_outside_continuous_cash_clock"),)
            horizons.append(HorizonLabel(horizon, entry_at, exit_at, "outside_clock",
                                         PathLabel("outside_clock", issues=issues), issues=issues))
            continue
        observations = tuple(observe(entry_at + index * MINUTE) for index in range(horizon + 1))
        entry, entry_issue = observations[0]
        exit_row, exit_issue = observations[-1]
        entry_price = Fraction(entry.ohlc[0]) if entry is not None else None
        exit_price = Fraction(exit_row.ohlc[0]) if exit_row is not None else None
        evidence = _evidence((entry, exit_row))
        issues = tuple(issue for issue in (entry_issue, exit_issue) if issue is not None)
        gross = maturity = None
        if entry_price is not None and exit_price is not None:
            gross = exit_price / entry_price - 1
            maturity = max(item.available_at for item in evidence)
        path = _path(tuple(row for row, _ in observations),
                     tuple(issue for _, issue in observations if issue is not None), entry_price, exit_price)
        horizons.append(HorizonLabel(horizon, entry_at, exit_at, _status(issues), path, entry_price,
                                     exit_price, gross, maturity, evidence, issues))
    return ChartLabels(session.symbol, session.opening, session.closing, bar_end, decision_at,
                       label_cutoff, tuple(horizons))
