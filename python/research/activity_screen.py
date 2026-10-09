"""Pure toy screen preparation for BB; no actual selector or evidence adapter.

The caller supplies the COMPLETE present formation-bar pool and inspected toy
identity/listing boundaries. Omitted codes cannot be detected here. An AG
IdentityPeriod with common_stock=True means the caller has established the
combined domestic operating-company common-issue classification in the fixture;
this helper cannot certify it from current names, labels or a source string.

Quote/activity turnover, source ACC_TRDVAL and issue capitalization have separate
aware public_available_at inputs. They are checked at the explicitly supplied
next-index-session 08:30 KST modeled decision, not inferred from a source date.
Classification retains AG's earlier formation cutoff. The fixed AQ calendar
window includes verified zeros/frozen observations; only AN's same-operating-
period normal baseline skips frozen observations. Unknown observations stop
calculation. A verified start and complete observed coverage can prove a short
history; calendar truncation cannot. No future or pre-boundary values are read.

There is no IO, CLI, database, price loader, experiment, certification or ready
flag. Returned SyntheticSelection inputs do not establish actual availability,
historical membership or permission to run a real sizing/comparison study.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext

from research.activity_accounting import IdentityPeriod, common_stock_at
from research.activity_replay import SyntheticCandidate, SyntheticSelection


@dataclass(frozen=True)
class PublishedValue:
    """Explicit toy value and the time its applicable version became public."""

    value: Decimal | None
    public_available_at: datetime | None


@dataclass(frozen=True)
class ScreenObservation:
    """observed/frozen are verified states; missing is unresolved, never zero.

    turnover is quote/activity turnover in quotes and source ACC_TRDVAL in
    liquidity. These are separate mappings, with independent publication times.
    Frozen is a caller-verified state, not inferred from a zero turnover alone.
    """

    state: str
    turnover: Decimal | None
    public_available_at: datetime | None


@dataclass(frozen=True)
class SyntheticListing:
    """Caller-verified current-issue start, separate from an operating boundary."""

    listed_on: date
    known_on: date


@dataclass(frozen=True)
class ScreenDiagnostic:
    code: str
    normal_dates: tuple[date, ...]
    skipped_frozen_dates: tuple[date, ...]
    liquidity_dates: tuple[date, ...]
    liquidity_zero_count: int
    liquidity_frozen_count: int
    activity_median: Decimal | None
    activity_ratio: Decimal | None
    liquidity_median: Decimal | None


@dataclass(frozen=True)
class SyntheticScreen:
    selection: SyntheticSelection
    diagnostics: tuple[ScreenDiagnostic, ...]


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative Decimal")
    return value


def _available(value: datetime | None, decision: datetime) -> None:
    if (not isinstance(value, datetime) or value.utcoffset() is None
            or value > decision):
        raise ValueError("unknown, naive or late synthetic public availability")


def _observation(
    rows: Mapping[date, ScreenObservation], day: date, decision: datetime,
) -> ScreenObservation:
    row = rows.get(day)
    if not isinstance(row, ScreenObservation) or row.state not in {"observed", "frozen"}:
        raise ValueError(f"unresolved synthetic observation: {day}")
    _decimal(row.turnover, "turnover")
    _available(row.public_available_at, decision)
    return row


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    a, b = ordered[middle - 1:middle + 1]
    # Preserve every supplied decimal digit, including an even median's .5.
    with localcontext() as context:
        context.prec = max(28, max(a.adjusted(), b.adjusted())
                           - min(a.as_tuple().exponent, b.as_tuple().exponent) + 3)
        return (a + b) / 2


def _listing_start(
    listings: Mapping[str, SyntheticListing], code: str, formation: date,
) -> date | None:
    row = listings.get(code)
    if row is None:
        return None
    if (not isinstance(row, SyntheticListing) or type(row.listed_on) is not date
            or type(row.known_on) is not date or row.known_on > formation
            or row.listed_on > formation):
        raise ValueError("unresolved synthetic listing boundary")
    return row.listed_on


def _normal_history(
    calendar: Sequence[date], index: int, rows: Mapping[date, ScreenObservation],
    periods: tuple[IdentityPeriod, ...], period: IdentityPeriod, start: date,
    lookback: int, decision: datetime,
) -> tuple[list[Decimal], tuple[date, ...], tuple[date, ...], bool]:
    values, days, frozen = [], [], []
    for day in reversed(calendar[:index]):
        if day < start:
            break
        matches = [p for p in periods if p.code == period.code and p.start <= day < p.end]
        if matches != [period]:
            raise ValueError("unknown or conflicting operating-period history")
        row = _observation(rows, day, decision)
        if row.state == "frozen":
            frozen.append(day)
            continue
        values.append(row.turnover)
        days.append(day)
        if len(values) == lookback:
            return values, tuple(reversed(days)), tuple(reversed(frozen)), False
    if start < calendar[0]:
        raise ValueError("truncated calendar cannot prove short operating history")
    return values, tuple(reversed(days)), tuple(reversed(frozen)), True


def screen_synthetic(
    calendar: Sequence[date], formation_index: int, params: Mapping,
    quotes: Mapping[str, Mapping[date, ScreenObservation]],
    liquidity: Mapping[str, Mapping[date, ScreenObservation]],
    capitalization: Mapping[str, PublishedValue],
    periods: tuple[IdentityPeriod, ...], listings: Mapping[str, SyntheticListing], *,
    liquidity_lookback: int, liquidity_floor: Decimal, capitalization_floor: Decimal,
    decision_at: datetime,
) -> SyntheticScreen:
    """Calculate BB-compatible toy dispositions without hashing or held filtering.

    params supplies v1 lookback and threshold explicitly; AQ lookback/floors
    are also explicit. Every supplied non-frozen formation code is classified
    before size/history screens, including held or size-failing names. Known
    excluded controls need no operating screens. Each quotes code must have a
    verified formation observation; unknown formation data cannot remove it.
    """
    if (not calendar or any(type(day) is not date for day in calendar)
            or any(a >= b for a, b in zip(calendar, calendar[1:]))):
        raise ValueError("synthetic index calendar must be strictly increasing")
    if type(formation_index) is not int or not 0 <= formation_index < len(calendar) - 1:
        raise ValueError("formation needs an explicit next index session")
    lookback = params["lookback"]
    if any(type(n) is not int or n < 1 for n in (lookback, liquidity_lookback)):
        raise ValueError("lookbacks must be positive integers")
    raw_threshold = params["threshold"]
    if isinstance(raw_threshold, bool) or not isinstance(raw_threshold, (int, float, Decimal)):
        raise ValueError("v1 threshold must be finite and nonnegative")
    threshold = _decimal(Decimal(str(raw_threshold)), "v1 threshold")
    _decimal(liquidity_floor, "liquidity floor")
    _decimal(capitalization_floor, "capitalization floor")
    formation, decision = calendar[formation_index:formation_index + 2]
    cutoff = datetime.combine(decision, time(8, 30), timezone(timedelta(hours=9)))
    _available(decision_at, cutoff)
    if decision_at != cutoff:
        raise ValueError("decision must be next index session at 08:30 KST")
    if formation_index < liquidity_lookback:
        raise ValueError("truncated calendar cannot supply the fixed liquidity window")

    present = {}
    for code, rows in quotes.items():
        bar = _observation(rows, formation, decision_at)
        if bar.state != "frozen":
            present[code] = bar
    identities = {}
    # Resolve the full supplied pool before any screen can discard a name.
    for code in sorted(present):
        common = common_stock_at(periods, code=code, session=formation)
        period = next(p for p in periods if p.code == code and p.start <= formation < p.end)
        identities[code] = common, period

    candidates, diagnostics = [], []
    window = tuple(calendar[formation_index - liquidity_lookback:formation_index])
    for code in sorted(present):
        common, period = identities[code]
        if not common:
            candidates.append(SyntheticCandidate(
                code, "excluded", period.known_on, None, "unknown", "unknown", None))
            diagnostics.append(ScreenDiagnostic(code, (), (), (), 0, 0, None, None, None))
            continue
        cap = capitalization.get(code)
        if not isinstance(cap, PublishedValue):
            raise ValueError("unresolved synthetic capitalization")
        size = _decimal(cap.value, "capitalization")
        _available(cap.public_available_at, decision_at)
        listed_on = _listing_start(listings, code, formation)
        source = liquidity.get(code, {})
        liquid_values, zero_count, frozen_count = [], 0, 0
        short_liquidity = False
        for day in window:
            if listed_on is not None and day < listed_on:
                short_liquidity = True
                continue
            row = _observation(source, day, decision_at)
            liquid_values.append(row.turnover)
            zero_count += row.turnover == 0
            frozen_count += row.state == "frozen"
        liquid_median = None if short_liquidity else _median(liquid_values)
        liquid_state = ("insufficient" if short_liquidity else
                        "pass" if liquid_median >= liquidity_floor else "fail")

        start = max(period.start, listed_on) if listed_on is not None else period.start
        values, normal_days, frozen_days, short_normal = _normal_history(
            calendar, formation_index, quotes[code], periods, period, start, lookback, decision_at)
        baseline = None if short_normal else _median(values)
        ratio = present[code].turnover / baseline if baseline is not None and baseline > 0 else None
        active = False
        if ratio is not None:
            # Compare the exact decimal amounts: a rounded repeating quotient
            # must not move an inclusive threshold across its boundary.
            with localcontext() as context:
                context.prec = max(28, len(baseline.as_tuple().digits)
                                   + len(threshold.as_tuple().digits))
                active = present[code].turnover >= baseline * threshold
        candidates.append(SyntheticCandidate(
            code, "operating_common", period.known_on, size >= capitalization_floor,
            liquid_state, "insufficient" if short_normal else "pass", active))
        diagnostics.append(ScreenDiagnostic(
            code, normal_days, frozen_days, window, zero_count, frozen_count,
            baseline, ratio, liquid_median))
    return SyntheticScreen(SyntheticSelection(formation, decision, tuple(candidates)), tuple(diagnostics))
