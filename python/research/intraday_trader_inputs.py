"""Source-only EE iterator over the caller's complete original population.

The caller authenticates the calendar, archives and reader before invocation.
This module performs no IO or authenticity certification. It preserves both
original timing coordinates and compares first-attempt A/B session mathematics.
C content, automatic reentry, accounts and actual execution are outside scope.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from re import fullmatch
from typing import Protocol

from research import intraday_ed_sources as sources
from research import intraday_trader_reference as reference
from research import intraday_trader_session as producer


SCENARIOS = ("EE-A", "EE-B", "EE-A-WITHOUT-15M-CONTEXT", "EE-B-EARLY-RECLAIM")
BEFORE_SESSION = "ee_original_session_before"
AFTER_SESSION = "ee_original_session_after"


class AuditedChunkReader(Protocol):
    """Caller-owned reader contract; implementing it does not prove an audit."""

    def load_raw_chunk(self, kind: str, symbol: str, session: str) -> dict | None: ...

    def assert_unchanged(self) -> None: ...


@dataclass(frozen=True)
class OriginalSessionRecord:
    targets: tuple[sources.TargetCoordinate, ...]
    result: producer.SessionResult
    mathematical_verification: reference.VerificationReceipt


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise sources.SourceRefusal(reason)


def _groups(universe: sources.UniverseCalendar) -> dict[tuple[str, str], list[sources.TargetCoordinate]]:
    """Validate the entire metadata population before any raw lookup."""
    _require(type(universe) is sources.UniverseCalendar, "typed_universe_calendar_required")
    _require(type(universe.sessions) is tuple and bool(universe.sessions), "invalid_original_calendar")
    for session in universe.sessions:
        _require(type(session) is str, "invalid_original_session")
        try:
            canonical = date.fromisoformat(session).isoformat()
        except ValueError as error:
            raise sources.SourceRefusal("invalid_original_session") from error
        _require(canonical == session, "invalid_original_session")
    _require(tuple(sorted(set(universe.sessions))) == universe.sessions, "invalid_original_calendar")
    _require(type(universe.raw_sha256) is str
             and fullmatch(r"[0-9a-f]{64}", universe.raw_sha256) is not None,
             "invalid_original_calendar_identity")
    _require(type(universe.targets) is tuple and bool(universe.targets), "invalid_original_targets")
    sessions = set(universe.sessions)
    coordinates: set[tuple[str, str, str]] = set()
    ranks: set[tuple[str, str, int]] = set()
    clock_days: set[tuple[str, str]] = set()
    groups: dict[tuple[str, str], list[sources.TargetCoordinate]] = {}
    for target in universe.targets:
        _require(type(target) is sources.TargetCoordinate, "typed_original_target_required")
        _require(type(target.scenario) is str and target.scenario in {"S1", "S2"},
                 "invalid_original_timing_clock")
        _require(type(target.symbol) is str and fullmatch(r"KRX:[0-9A-Z]{6}", target.symbol) is not None,
                 "invalid_original_symbol")
        _require(type(target.session) is str and target.session in sessions, "target_outside_original_calendar")
        _require(type(target.rank) is int and target.rank > 0, "invalid_original_rank")
        _require(type(target.metadata) is bytes, "opaque_original_target_metadata_bytes_required")
        _require(type(target.state) is str and (
                 (target.stored_row_count is None and target.state == "no_stored_rows_unknown_reason")
                 or (type(target.stored_row_count) is int and target.stored_row_count > 0
                     and target.state == "stored_rows_completeness_unverified")),
                 "original_target_missingness_contract_differs")
        coordinate = target.scenario, target.session, target.symbol
        rank = target.scenario, target.session, target.rank
        _require(coordinate not in coordinates, "duplicate_original_target")
        _require(rank not in ranks, "duplicate_original_day_rank")
        coordinates.add(coordinate)
        ranks.add(rank)
        clock_days.add((target.scenario, target.session))
        groups.setdefault((target.session, target.symbol), []).append(target)
    _require(clock_days == {(clock, session) for clock in ("S1", "S2") for session in sessions},
             "original_timing_calendar_incomplete")
    return groups


def iter_original_sessions(
    universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
    checkpoint: Callable[[str], object],
) -> Iterator[OriginalSessionRecord]:
    """Read each union (session, symbol) once, preserving every original target.

    Only the current session's raw/decoded rows are retained. Production and
    reference decode the same original chunks separately; no produced five-minute
    cache or old EA/ED signal mask supplies the reference. Refusals and unknowns
    retain their mathematical meaning; reader changes and mismatches propagate.
    Opaque source bytes come only from the existing decoder, without certification.
    """
    groups = _groups(universe)
    _require(callable(getattr(raw_reader, "load_raw_chunk", None))
             and callable(getattr(raw_reader, "assert_unchanged", None)), "raw_reader_contract_required")
    _require(callable(checkpoint), "checkpoint_callable_required")
    for session, symbol in sorted(groups):
        checkpoint(BEFORE_SESSION)
        raw_reader.assert_unchanged()
        try:
            price = raw_reader.load_raw_chunk("CC", symbol, session)
            volume = raw_reader.load_raw_chunk("CX", symbol, session)
            production = sources.session_inputs(symbol, session, price, volume)
            independent = sources.full_reference_session_inputs(symbol, session, price, volume)
            result = producer.evaluate_session(production.session, scenarios=SCENARIOS,
                                               source_metadata=production.metadata)
            receipt = reference.verify_session_result(result, independent, scenarios=SCENARIOS,
                                                      source_metadata=production.metadata)
            record = OriginalSessionRecord(tuple(groups[(session, symbol)]), result, receipt)
            del price, volume, production, independent
        finally:
            raw_reader.assert_unchanged()
            checkpoint(AFTER_SESSION)
        yield record
