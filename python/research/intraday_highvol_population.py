"""EG coverage over a caller-authenticated calendar and unchanged raw reader.

Only original target coordinates are visited. The previous twenty calendar
sessions remain explicit even when a symbol was not in the preserved source
population. No trading labels, executions, outcomes or source authentication
are performed here. The caller must supply the registered audited reader.
"""
from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from research import intraday_ed_sources as sources
from research import intraday_highvol_events as events
from research.intraday_ed_inputs import MINUTE, SessionInputs, _require, _session, window
from research.intraday_trader_inputs import AuditedChunkReader, _groups


BEFORE_SESSION = "eg_coverage_before_session"
AFTER_SESSION = "eg_coverage_after_session"
AT_WINDOW = "eg_coverage_window"
OUTSIDE_POPULATION = "prior_symbol_outside_preserved_population"
SOURCE_ABSENT = "prior_price_chunk_absent"


@dataclass(frozen=True)
class MinuteCoverage:
    start_offset: int
    end_offset_exclusive: int
    price_states: tuple[tuple[str, int], ...]
    volume_states: tuple[tuple[str, int], ...]
    zero_volume_minutes: int


@dataclass(frozen=True)
class CoverageRecord:
    session: str
    symbol: str
    opening: datetime
    closing: datetime
    targets: tuple[sources.TargetCoordinate, ...]
    source_metadata: bytes
    price_chunk_present: bool
    volume_chunk_present: bool
    minute_coverage: tuple[MinuteCoverage, ...]
    decisions: tuple[events.HighVolDecision, ...]
    episodes: events.EpisodeSummary

    @property
    def windows(self) -> tuple[events.WindowMetric, ...]:
        return tuple(decision.current for decision in self.decisions)


def minute_coverage(session: SessionInputs) -> tuple[MinuteCoverage, ...]:
    """Count every regular minute, independently of the fifteen-minute warmup.

This is retrospective data coverage, not a feature used to select an event.
Late observations retain their presence but are not available at the declared
minute-end-plus-two clock. Unknown source reasons do not become trading halts.
"""
    _session(session)
    duration = int((session.closing - session.opening) / MINUTE)
    result = []
    for start, end in ((0, min(60, duration)), (min(60, duration), duration)):
        prices, volumes = Counter(), Counter()
        zero = 0
        if start != end:
            # Validate typed payloads using the established input boundary.
            # Coverage separately evaluates each minute's causal availability.
            cutoff = max([session.opening + (end + 2) * MINUTE]
                         + [row.available_at for at, row in session.minutes.items()
                            if session.opening + start * MINUTE <= at < session.opening + end * MINUTE])
            observed = window(session, start, end - start, cutoff, price=True, volume=True)
            by_time = {row.start_at: row for row in observed.minutes}
            for offset in range(start, end):
                at = session.opening + offset * MINUTE
                row = by_time.get(at)
                if row is None:
                    prices["missing_minute"] += 1
                    volumes["missing_minute"] += 1
                    continue
                _require(row.available_at >= at + 3 * MINUTE, "highvol_source_before_availability_floor")
                if row.available_at > at + 3 * MINUTE:
                    prices["late_at_assumed_clock"] += 1
                    volumes["late_at_assumed_clock"] += 1
                    continue
                prices["observed" if row.price_state == "observed" else "price_" + row.price_state] += 1
                volumes["observed" if row.volume_state == "observed" else "volume_" + row.volume_state] += 1
                zero += row.volume_state == "observed" and row.shares == 0
        result.append(MinuteCoverage(start, end, tuple(sorted(prices.items())),
                                     tuple(sorted(volumes.items())), zero))
    return tuple(result)


def iter_coverage_records(
    universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
    checkpoint: Callable[[str], object],
) -> Iterator[CoverageRecord]:
    """Parse each original union symbol/day once, caching twenty days of metrics.

The archive's registered geometry contains only original union coordinates.
If a prior symbol/day is absent from that population, it is absent baseline
support rather than permission to acquire it or to substitute an older date.
"""
    groups = _groups(universe)
    _require(callable(getattr(raw_reader, "load_raw_chunk", None))
             and callable(getattr(raw_reader, "assert_unchanged", None)), "raw_reader_contract_required")
    _require(callable(checkpoint), "checkpoint_callable_required")
    by_day: dict[str, list[str]] = {day: [] for day in universe.sessions}
    for day, symbol in sorted(groups):
        by_day[day].append(symbol)
    history: deque[tuple[datetime, dict[str, tuple[tuple[events.WindowMetric, ...], bool]]]] = deque(maxlen=20)
    for day in universe.sessions:
        opening = sources.calendar_source.session_open(day)
        expected = tuple(prior_opening for prior_opening, _ in history)
        day_metrics: dict[str, tuple[tuple[events.WindowMetric, ...], bool]] = {}
        for symbol in by_day[day]:
            checkpoint(BEFORE_SESSION)
            raw_reader.assert_unchanged()
            try:
                price = raw_reader.load_raw_chunk("CC", symbol, day)
                volume = raw_reader.load_raw_chunk("CX", symbol, day)
                price_present, volume_present = price is not None, volume is not None
                view = sources.session_inputs(symbol, day, price, volume)
                del price, volume
                decisions = []
                for index, offset in enumerate(events.OFFSETS):
                    checkpoint(AT_WINDOW)
                    current = events.measure_window(view.session, offset, opening + (offset + 2) * MINUTE)
                    prior_metrics, absent = {}, {}
                    for prior_opening, prior_day in history:
                        previous = prior_day.get(symbol)
                        if previous is None:
                            absent[prior_opening] = OUTSIDE_POPULATION
                        elif not previous[1]:
                            absent[prior_opening] = SOURCE_ABSENT
                        else:
                            prior_metrics[prior_opening] = previous[0][index]
                    decisions.append(events.classify_window(current, expected, prior_metrics,
                                                             absent_reasons=absent))
                typed = tuple(decisions)
                record = CoverageRecord(day, symbol, view.session.opening, view.session.closing,
                    tuple(sorted(groups[(day, symbol)], key=lambda target: (target.scenario, target.rank))),
                    view.metadata, price_present, volume_present, minute_coverage(view.session), typed,
                    events.group_episodes(typed))
                day_metrics[symbol] = (record.windows, price_present)
                del view, decisions
            finally:
                raw_reader.assert_unchanged()
                checkpoint(AFTER_SESSION)
            yield record
        history.append((opening, day_metrics))
