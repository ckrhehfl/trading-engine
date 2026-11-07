"""EH price support over the original union and a caller-audited reader.

No events, path outcomes, prices or payloads are emitted. The CC decoder
assigns minute-end-plus-two availability. Counts therefore describe the
declared clock, without certifying historical receipt or a common price basis.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from operator import itemgetter
from typing import Literal

from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _clock, _require, _session, window
from research.intraday_trader_inputs import AuditedChunkReader, _groups


BEFORE_SESSION = "eh_support_before_session"
AFTER_SESSION = "eh_support_after_session"
_LAG = 2 * MINUTE
PriorStatus = Literal["outside_calendar", "outside_union", "chunk_absent", "incomplete", "late", "complete"]


@dataclass(frozen=True)
class PriceCoverage:
    scope: Literal["first15", "first20", "first60", "full"]
    expected: int
    cutoff_at: datetime
    counts: tuple[tuple[str, int], ...]

    @property
    def valid_exact(self) -> int:
        return sum(count for state, count in self.counts if state in ("observed", "late"))

    @property
    def complete(self) -> bool:
        return self.counts == (("observed", self.expected),)


@dataclass(frozen=True)
class SupportRecord:
    session: str
    symbol: str
    opening: datetime
    closing: datetime
    targets: tuple[sources.TargetCoordinate, ...]
    price_chunk_present: bool
    coverage: tuple[PriceCoverage, ...]
    prior_session: str | None
    prior_status: PriorStatus
    prior_full: PriceCoverage | None
    cross_session_basis: Literal["unknown"] = "unknown"

    @property
    def opening_range_price_supported(self) -> bool:
        return self.coverage[0].complete


@dataclass(frozen=True)
class _PriorPrice:
    chunk_present: bool
    full: PriceCoverage
    latest_available: datetime | None


def measure_price_support(session: SessionInputs) -> tuple[tuple[PriceCoverage, ...], datetime | None]:
    """Count each prefix at its endpoint +2 and retain latest valid availability.

    Late valid prices also count as exact prices. Requested slots beyond a
    short session count as outside_session. Present unobserved rows retain
    their own states, and invalid typed payloads fail the input contract.
    """
    _session(session)
    duration = (session.closing - session.opening) // MINUTE
    starts = tuple(session.opening + offset * MINUTE for offset in range(duration))
    rows = {}
    for at in starts:
        row = session.minutes.get(at)
        if row is not None:
            _require(isinstance(row, Minute), "typed_minute_required")
            _clock(row.available_at)
            rows[at] = row
    validation_at = max((session.closing, *(row.available_at for row in rows.values())))
    bounded = SessionInputs(session.symbol, session.opening, session.closing, rows)
    window(bounded, 0, duration, validation_at)
    latest = max((row.available_at for row in rows.values() if row.price_state == "observed"), default=None)
    coverage = []
    for scope, expected in (("first15", 15), ("first20", 20), ("first60", 60), ("full", duration)):
        end = min(expected, duration)
        cutoff = session.opening + end * MINUTE + _LAG
        counts = Counter()
        if end < expected:
            counts["outside_session"] = expected - end
        for at in starts[:end]:
            row = rows.get(at)
            state = row.price_state if row is not None else "missing"
            if state == "observed" and row.available_at > cutoff:
                state = "late"
            counts[state] += 1
        coverage.append(PriceCoverage(scope, expected, cutoff, tuple(sorted(counts.items()))))
    return tuple(coverage), latest


def _prior_status(day: str | None, prior: _PriorPrice | None, opening: datetime) -> PriorStatus:
    if day is None:
        return "outside_calendar"
    if prior is None:
        return "outside_union"
    if not prior.chunk_present:
        return "chunk_absent"
    if prior.full.valid_exact != prior.full.expected:
        return "incomplete"
    if prior.latest_available is None or prior.latest_available > opening + 20 * MINUTE + _LAG:
        return "late"
    return "complete"


def iter_support_records(
    universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
    checkpoint: Callable[[str], object],
) -> Iterator[SupportRecord]:
    """Visit each original symbol/date once and retain only prior date summaries."""
    groups = _groups(universe)
    sources._need(callable(getattr(raw_reader, "load_raw_chunk", None))
                  and callable(getattr(raw_reader, "assert_unchanged", None)), "raw_reader_contract_required")
    sources._need(callable(checkpoint), "checkpoint_callable_required")
    previous_day, previous = None, {}
    for day, coordinates in groupby(sorted(groups), key=itemgetter(0)):
        today = {}
        for _, symbol in coordinates:
            checkpoint(BEFORE_SESSION)
            raw_reader.assert_unchanged()
            try:
                raw = raw_reader.load_raw_chunk("CC", symbol, day)
                session = sources.full_reference_session_inputs(symbol, day, raw, None)
                coverage, latest = measure_price_support(session)
                prior = previous.get(symbol)
                record = SupportRecord(
                    day, symbol, session.opening, session.closing,
                    tuple(sorted(groups[(day, symbol)], key=lambda target: target.scenario)),
                    raw is not None, coverage, previous_day, _prior_status(previous_day, prior, session.opening),
                    prior.full if prior is not None else None,
                )
                today[symbol] = _PriorPrice(record.price_chunk_present, coverage[-1], latest)
                del raw, session
            finally:
                raw_reader.assert_unchanged()
                checkpoint(AFTER_SESSION)
            yield record
        previous_day, previous = day, today
