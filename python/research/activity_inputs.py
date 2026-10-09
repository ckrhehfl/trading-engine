"""Lossless spent-panel inputs and an independent formation-population join.

This module does not certify sources or run a study. Its future caller must
register actual reads/trials, supply the independently established FULL present
non-frozen formation population, and adjudicate continuous domestic operating
common-issue periods, listing bounds and historical publication times. Neither
a digest nor an IdentityPeriod/source string proves those historical facts.
BA/BE's unknown times and uncertified source points cannot supply these inputs.

Quote activity is read from scan_bars.turnover. Source ACC_TRDVAL uses a separate
field-specific input type; it is never calculated from or filled by the quotes.
Only the existing spent panel can be loaded. SQLite is read-only and pinned to
one read transaction. NULL observations and absent sessions remain different.
The supplied index calendar is a caller input, not inferred from stock bars.
No CLI, exchange access, automatic certification or experiment logging is added.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from research.activity_accounting import IdentityPeriod, common_stock_at
from research.activity_portfolio import readonly
from research.activity_replay import DecimalSeries
from research.activity_screen import (
    PublishedValue, ScreenObservation, SyntheticListing, SyntheticScreen,
    screen_synthetic,
)


@dataclass(frozen=True)
class LoadedScan:
    """Immutable scan observations; digest covers calendar/progress/raw bars.

    Separate source liquidity, capitalization, identity and public-time inputs
    are outside this scan identifier. A future registered runner must pin them
    separately; this digest supplies neither that manifest nor historical proof.
    """

    calendar: tuple[date, ...]
    series: tuple[tuple[str, DecimalSeries], ...]
    dataset_sha256: str

    def __post_init__(self) -> None:
        if type(self.calendar) is not tuple or _calendar(self.calendar) != self.calendar:
            raise ValueError("loaded scan needs an immutable index calendar")
        if (type(self.series) is not tuple
                or any(type(row) is not tuple or len(row) != 2 for row in self.series)):
            raise ValueError("loaded scan requires immutable code/series pairs")
        _codes(tuple(code for code, _ in self.series))
        if any(not isinstance(series, DecimalSeries) or len(series.opens) != len(self.calendar)
               for _, series in self.series):
            raise ValueError("loaded scan series must align to its index calendar")
        if (not isinstance(self.dataset_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.dataset_sha256) is None):
            raise ValueError("loaded scan needs a lowercase SHA-256 identifier")

    @property
    def panel(self) -> Mapping[str, DecimalSeries]:
        return MappingProxyType(dict(self.series))


@dataclass(frozen=True)
class SourceLiquidity:
    """Inspected source ACC_TRDVAL, not scan quote/activity turnover."""

    state: str
    acc_trdval: Decimal | None
    public_available_at: datetime | None


@dataclass(frozen=True)
class FormationSource:
    """One independently supplied formation-code source row.

    Explicitly excluded controls may have no capitalization/liquidity inputs.
    These values/times are not synthesized from a response date or quote field.
    """

    formation_on: date
    code: str
    capitalization: PublishedValue | None
    liquidity: tuple[tuple[date, SourceLiquidity], ...]


def _codes(values: Sequence[str], *, allow_empty: bool = False) -> tuple[str, ...]:
    if (isinstance(values, (str, bytes)) or (not values and not allow_empty)
            or any(not isinstance(code, str) or re.fullmatch(r"[0-9A-Z]{6}", code) is None
                   for code in values) or len(set(values)) != len(values)):
        raise ValueError("codes must be explicit unique six-character issue codes")
    return tuple(sorted(values))


def _calendar(values: Sequence[date]) -> tuple[date, ...]:
    result = tuple(values)
    if (not result or any(type(day) is not date for day in result)
            or any(a >= b for a, b in zip(result, result[1:]))):
        raise ValueError("index calendar must contain strictly increasing dates")
    return result


def _day(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError("scan dates must be canonical YYYYMMDD strings")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError as exc:
        raise ValueError("invalid scan date") from exc


def _amount(value: object, *, positive: bool) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("scan amounts must preserve source TEXT or NULL")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid scan amount") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("invalid scan amount")
    return result


def load_scan(scan_path: Path, calendar: Sequence[date], requested_codes: Sequence[str]) -> LoadedScan:
    """Read only requested codes after panel/progress validation, without floats.

    Progress must be done and its row bounds/count must match. An internal
    absent observation is preserved, never deemed not-listed or a zero. The
    legacy progress frozen count includes NULL turnover; it is checked as
    collector metadata only and never used to label an observed NULL as frozen.
    Caller adjudication is still needed to establish membership/completeness.
    """
    days, codes = _calendar(calendar), _codes(requested_codes)
    positions = {day: index for index, day in enumerate(days)}
    digest = hashlib.sha256()

    def add(value: object) -> None:
        digest.update(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
        digest.update(b"\n")

    with closing(readonly(scan_path)) as scan:
        scan.execute("BEGIN")
        metadata = scan.execute("SELECT id,start,end FROM scan_panel ORDER BY id").fetchall()
        if metadata != [(1, "20190102", "20260918")]:
            raise ValueError("only the registered spent 20190102..20260918 panel is allowed")
        first, last = date(2019, 1, 2), date(2026, 9, 18)
        if days[0] != first or last not in positions or positions[last] + 2 >= len(days):
            raise ValueError("index calendar must cover the panel and two settlement sessions")
        add(["calendar", [day.isoformat() for day in days]])
        add(["panel", metadata, "requested_codes", codes])
        progress = {}
        # Validate ALL requested progress rows before querying any price row.
        for code in codes:
            rows = scan.execute(
                "SELECT first_date,last_date,bars,frozen,status,fetched_at "
                "FROM scan_progress WHERE code=?", (code,),
            ).fetchall()
            if (len(rows) != 1 or rows[0][4] != "done"
                    or type(rows[0][2]) is not int or rows[0][2] < 1
                    or type(rows[0][3]) is not int or not 0 <= rows[0][3] <= rows[0][2]
                    or not isinstance(rows[0][5], str) or not rows[0][5]):
                raise ValueError("requested code needs one complete scan_progress row")
            row = rows[0]
            if not first <= _day(row[0]) <= _day(row[1]) <= last:
                raise ValueError("scan_progress bounds disagree with the panel")
            progress[code] = row
            add(["progress", code, *row])

        output = []
        for code in codes:
            values = [[None] * len(days) for _ in range(5)]
            observed = [False] * len(days)
            count, first_raw, last_raw = 0, None, None
            for raw in scan.execute(
                "SELECT bsop_date,open,high,low,close,volume,turnover FROM scan_bars "
                "WHERE code=? ORDER BY bsop_date", (code,),
            ):
                day = _day(raw[0])
                if not first <= day <= last or day not in positions:
                    raise ValueError("scan and index calendar disagree")
                index = positions[day]
                if observed[index]:
                    raise ValueError("duplicate code/date observation")
                opening, high, low, close = (_amount(value, positive=True) for value in raw[1:5])
                volume, turnover = (_amount(value, positive=False) for value in raw[5:])
                if volume is not None and volume != volume.to_integral_value():
                    raise ValueError("scan volume must be integral")
                if all(value is not None for value in (opening, high, low, close)):
                    if not low <= min(opening, close) <= max(opening, close) <= high:
                        raise ValueError("invalid scan OHLC bounds")
                    equal = opening == high == low == close
                    frozen = equal and turnover == 0 if turnover is not None else None
                    locked = equal and turnover > 0 if turnover is not None else None
                else:
                    frozen = locked = None
                for column, value in zip(values, (opening, close, turnover, frozen, locked)):
                    column[index] = value
                observed[index] = True
                count += 1
                first_raw = first_raw or raw[0]
                last_raw = raw[0]
                add(["bar", code, *raw])
            # Match the original collector's TEXT/NULL predicate; this is NOT
            # the lossless quote-state rule above and supplies no eligibility.
            legacy_frozen = scan.execute(
                "SELECT COUNT(*) FROM scan_bars WHERE code=? AND open=high "
                "AND high=low AND low=close AND (turnover IS NULL OR CAST(turnover AS REAL)=0)",
                (code,),
            ).fetchone()[0]
            if (first_raw, last_raw, count, legacy_frozen) != progress[code][:4]:
                raise ValueError("scan_progress does not reconcile with requested observations")
            output.append((code, DecimalSeries(*(tuple(column) for column in values), tuple(observed))))
    return LoadedScan(days, tuple(output), digest.hexdigest())


def join_formation(
    scan: LoadedScan, formation_on: date, population: Sequence[str],
    sources: Sequence[FormationSource], periods: tuple[IdentityPeriod, ...],
    listings: Mapping[str, SyntheticListing],
    quote_available_at: Mapping[str, Mapping[date, datetime | None]], *,
    params: Mapping, liquidity_lookback: int, liquidity_floor: Decimal,
    capitalization_floor: Decimal, decision_at: datetime,
) -> SyntheticScreen:
    """Validate the independent full pool, then invoke the existing pure screen.

    population must be fixed independently, not obtained from scan.panel. It
    lists every present non-frozen formation issue before any size/activity
    screen, including excluded controls. Absent/frozen requested panel members
    remain in LoadedScan but cannot quietly disappear from this fixed pool.
    This join checks consistency/refusal, not historical source truth. The
    existing screen retains classification-at-formation and per-field timing.
    """
    if not isinstance(scan, LoadedScan) or type(formation_on) is not date:
        raise ValueError("an explicit loaded scan and formation date are required")
    try:
        index = scan.calendar.index(formation_on)
    except ValueError as exc:
        raise ValueError("formation must be an index session") from exc
    expected = set(_codes(population, allow_empty=True))
    panel, present = scan.panel, set()
    quotes = {}
    for code, series in panel.items():
        if series.observed[index]:
            if (series.closes[index] is None or series.frozen[index] is None
                    or series.turnover[index] is None):
                raise ValueError("unresolved observed formation quote")
            if not series.frozen[index]:
                present.add(code)
    if expected != present:
        raise ValueError("independent formation population and quote pool disagree")
    # Keep observed frozen formation bars in the screen input so its existing
    # publication guard runs BEFORE a frozen bar can remove a name from the pool.
    # Genuinely absent panel members remain separately represented in LoadedScan.
    for code, series in panel.items():
        if not series.observed[index]:
            continue
        availability = quote_available_at.get(code, {})
        quotes[code] = {
            day: ScreenObservation(
                "missing" if not series.observed[i] else
                "unknown" if series.frozen[i] is None else
                "frozen" if series.frozen[i] else "observed",
                series.turnover[i], availability.get(day),
            ) for i, day in enumerate(scan.calendar[:index + 1])
        }
    if (type(periods) is not tuple
            or any(not isinstance(period, IdentityPeriod)
                   or any(type(day) is not date for day in (period.start, period.end, period.known_on))
                   for period in periods)):
        raise ValueError("unknown or invalid historical identity periods")
    # Full classification validation precedes source screens and selection.
    for code in sorted(expected):
        common_stock_at(periods, code=code, session=formation_on)
    capitalization, liquidity, source_codes = {}, {}, set()
    for row in sources:
        if (not isinstance(row, FormationSource) or row.formation_on != formation_on
                or row.code not in expected or row.code in source_codes):
            raise ValueError("formation source rows must match the independent population exactly")
        source_codes.add(row.code)
        capitalization[row.code] = row.capitalization
        if type(row.liquidity) is not tuple:
            raise ValueError("source liquidity must use immutable dated ACC_TRDVAL rows")
        dated = {}
        for day, value in row.liquidity:
            if (type(day) is not date or day not in scan.calendar or day in dated
                    or not isinstance(value, SourceLiquidity)):
                raise ValueError("duplicate/misaligned source date or substituted quote activity")
            dated[day] = ScreenObservation(value.state, value.acc_trdval, value.public_available_at)
        liquidity[row.code] = dated
    if source_codes != expected:
        raise ValueError("formation source rows must match the independent population exactly")
    return screen_synthetic(
        scan.calendar, index, params, quotes, liquidity, capitalization, periods, listings,
        liquidity_lookback=liquidity_lookback, liquidity_floor=liquidity_floor,
        capitalization_floor=capitalization_floor, decision_at=decision_at,
    )
