"""Independent EH support arithmetic over the caller's audited CC reader.

The E+2 clock assumes availability two minutes after bar completion (start+3).
The receipt covers agreement on this clock and the supplied original roster.
Archive and saved artifact authentication remain caller responsibilities.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from research import intraday_ed_sources as sources
from research.intraday_scenario_support import PriceCoverage, PriorStatus, SupportRecord
from research.intraday_trader_inputs import AuditedChunkReader, _groups


BEFORE_SESSION = "eh_support_verify_before_session"
AFTER_SESSION = "eh_support_verify_after_session"
_MINUTE = timedelta(minutes=1)
_SCOPES = ("first15", "first20", "first60", "full")


@dataclass(frozen=True)
class SupportVerificationReceipt:
    union_records: int
    original_targets: int
    calendar_sessions: int
    price_chunk_absent_records: int
    opening_range_supported_records: int
    coverage_counts: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    prior_status_counts: tuple[tuple[str, int], ...]
    available_at_assumption: str = "minute-end-plus-two"
    historical_availability_certified: bool = False


def _valid_ohlc(row: dict) -> bool:
    raw = (row["open"], row["high"], row["low"], row["close"])
    if any(type(value) is not str for value in raw):
        return False
    try:
        op, hi, lo, cl = (Decimal(value) for value in raw)
    except InvalidOperation:
        return False
    return (all(value.is_finite() and value > 0 for value in (op, hi, lo, cl))
            and lo <= op <= hi and lo <= cl <= hi)


def _raw_coverage(raw: dict | None, day: str, symbol: str, opening: datetime,
                  closing: datetime) -> tuple[PriceCoverage, ...]:
    duration, remainder = divmod(closing - opening, _MINUTE)
    start_ms = int(opening.timestamp()) * 1000
    sources._need(duration > 0 and not remainder and start_ms % 60_000 == 0,
                  "invalid_session_geometry")
    states: dict[int, str] = {}
    if raw is not None:
        identity = sources._raw_identity(raw, "CC")
        sources._need(identity[:2] == (symbol, day), "session_source_identity_differs")
        for row in raw["rows"]:
            offset = (row["open_time_ms"] - start_ms) // 60_000
            states[offset] = "observed" if offset not in states and _valid_ohlc(row) else "conflict"

    coverage = []
    for scope, size in zip(_SCOPES, (15, 20, 60, duration), strict=True):
        end = min(size, duration)
        counts = Counter(state for offset, state in states.items() if offset < end)
        # The last possible start in a window is end-1, so start+3 <= end+2.
        counts["missing"] = end - counts.total()
        counts["outside_session"] = size - end
        coverage.append(PriceCoverage(scope, size, opening + (end + 2) * _MINUTE,
                                      tuple(sorted((state, n) for state, n in counts.items() if n))))
    return tuple(coverage)


def _prior_status(day: str | None, prior: tuple[bool, PriceCoverage] | None,
                  opening: datetime) -> PriorStatus:
    if day is None:
        return "outside_calendar"
    if prior is None:
        return "outside_union"
    present, full = prior
    if not present:
        return "chunk_absent"
    if full.counts != (("observed", full.expected),):
        return "incomplete"
    # With complete support, the last slot's assumed availability is close+2.
    return "late" if full.cutoff_at > opening + 22 * _MINUTE else "complete"


def _exact(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if is_dataclass(right):
        return all(_exact(getattr(left, field.name), getattr(right, field.name)) for field in fields(right))
    if isinstance(right, tuple):
        return len(left) == len(right) and all(_exact(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(right, datetime):
        return left.isoformat() == right.isoformat()
    return left == right


def verify_support_records(
    stored: Iterable[SupportRecord], universe: sources.UniverseCalendar,
    raw_reader: AuditedChunkReader, checkpoint: Callable[[str], object],
) -> SupportVerificationReceipt:
    """Compare all stored rows in roster order, requiring EOF before returning."""
    groups = _groups(universe)
    sources._need(callable(getattr(raw_reader, "load_raw_chunk", None))
                  and callable(getattr(raw_reader, "assert_unchanged", None)), "raw_reader_contract_required")
    sources._need(callable(checkpoint), "checkpoint_callable_required")
    symbols = defaultdict(list)
    for day, symbol in sorted(groups):
        symbols[day].append(symbol)

    actual, eof = iter(stored), object()
    previous_day, previous = None, {}
    counts = {scope: Counter() for scope in _SCOPES}
    prior_counts = Counter()
    absent = opening_range_supported = 0
    for day in universe.sessions:
        opening = sources.calendar_source.session_open(day)
        closing = sources.calendar_source.session_close(day)
        today = {}
        for symbol in symbols[day]:
            checkpoint(BEFORE_SESSION)
            raw_reader.assert_unchanged()
            try:
                raw = raw_reader.load_raw_chunk("CC", symbol, day)
                present = raw is not None
                coverage = _raw_coverage(raw, day, symbol, opening, closing)
                del raw
            finally:
                raw_reader.assert_unchanged()
                checkpoint(AFTER_SESSION)

            prior = previous.get(symbol)
            targets = tuple(sorted(groups[day, symbol], key=lambda target: target.scenario))
            status = _prior_status(previous_day, prior, opening)
            expected = SupportRecord(day, symbol, opening, closing, targets, present, coverage,
                                     previous_day, status, prior[1] if prior is not None else None, "unknown")
            row = next(actual, eof)
            sources._need(row is not eof, "missing_stored_support_record")
            sources._need(_exact(row, expected), "stored_support_record_differs")
            today[symbol] = present, coverage[-1]
            for cell in coverage:
                counts[cell.scope].update(dict(cell.counts))
            prior_counts[status] += 1
            absent += int(not present)
            opening_range_supported += int(coverage[0].counts == (("observed", 15),))
        previous_day, previous = day, today

    sources._need(next(actual, eof) is eof, "extra_stored_support_record")
    raw_reader.assert_unchanged()
    return SupportVerificationReceipt(
        len(groups), len(universe.targets), len(universe.sessions), absent, opening_range_supported,
        tuple((scope, tuple(sorted(values.items()))) for scope, values in counts.items()),
        tuple(sorted(prior_counts.items())),
    )
