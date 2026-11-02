"""Original-population EE followup composition with a private one-day raw cache.

No file/network IO lives here. Every consumer receives a deep copy of raw
chunks; producer mutations cannot become a reference oracle. Saved verification
creates a fresh cache. The shared source parser remains an explicit boundary.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research import intraday_trader_followups as science
from research import intraday_trader_followup_reference as reference
from research import intraday_trader_inputs as original
from research import intraday_trader_reference as math
from research import intraday_trader_session as sessions


@dataclass(frozen=True)
class MechanismSessionRecord:
    targets: tuple[sources.TargetCoordinate, ...]
    result: sessions.SessionResult
    mathematical_verification: math.VerificationReceipt
    peer_context: science.PeerContext | None


_JSON_ATOMS = frozenset({str, int, bool, float, type(None)})


def _copy_raw(value, memo=None):
    """Copy raw JSON containers without generic dispatch for immutable atoms.

    This is a copy optimization, never input validation or a decoded feature
    cache. Every dict/list is independently copied, including malformed nested
    price payloads. Unknown Python objects retain deepcopy's existing behavior.
    A local memo also preserves aliases/cycles just as deepcopy does.
    """
    kind = type(value)
    if kind in _JSON_ATOMS:
        return value
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if kind is dict:
        copied = {}
        memo[identity] = copied
        for key, item in value.items():
            copied[key if type(key) in _JSON_ATOMS else deepcopy(key, memo)] = (
                item if type(item) in _JSON_ATOMS else _copy_raw(item, memo))
        return copied
    if kind is list:
        copied = []
        memo[identity] = copied
        copied.extend(item if type(item) in _JSON_ATOMS else _copy_raw(item, memo) for item in value)
        return copied
    return deepcopy(value, memo)


def population(universe: sources.UniverseCalendar) -> dict:
    """Close the complete original membership contract before any raw lookup."""
    groups = original._groups(universe)
    by_clock_day = {}
    for target in universe.targets:
        metadata = codec._json(target.metadata)
        sources._need(type(metadata) is dict and codec.canonical(metadata) == target.metadata
                      and metadata.get("session") == target.session, "followup_membership_metadata_differs")
        try:
            available, selected = (sources._clock(metadata[name]) for name in ("available_at", "selected_at"))
        except (KeyError, TypeError, ValueError) as error:
            raise sources.SourceRefusal("followup_membership_clock_invalid") from error
        sources._need(available <= selected < sources.calendar_source.session_open(target.session),
                      "followup_membership_noncausal")
        by_clock_day.setdefault((target.scenario, target.session), []).append(target)
    for members in by_clock_day.values():
        sources._need(len(members) == 20 and {item.rank for item in members} == set(range(1, 21))
                      and len({item.metadata for item in members}) == 1,
                      "followup_original_twenty_members_required")
    return groups


class DayRawCache:
    """Only original union coordinates, at most one original session at a time."""
    def __init__(self, universe: sources.UniverseCalendar, reader: original.AuditedChunkReader):
        self.groups = population(universe)
        sources._need(callable(getattr(reader, "load_raw_chunk", None))
                      and callable(getattr(reader, "assert_unchanged", None)), "raw_reader_contract_required")
        self.reader, self.day, self._raw = reader, None, {}
        self.members = {day: tuple(sorted((item for item in universe.targets
            if item.scenario == "S2" and item.session == day), key=lambda item: item.rank)) for day in universe.sessions}

    def own(self, symbol: str, day: str) -> tuple[dict | None, dict | None]:
        sources._need((day, symbol) in self.groups, "followup_nonoriginal_raw_request")
        if day != self.day:
            self.day, self._raw = day, {}
        if symbol not in self._raw:
            self._raw[symbol] = tuple(_copy_raw(self.reader.load_raw_chunk(kind, symbol, day)) for kind in ("CC", "CX"))
        return tuple(_copy_raw(chunk) for chunk in self._raw[symbol])

    def peers(self, symbol: str, day: str) -> Iterator[science.PeerInput]:
        for member in self.members[day]:
            if member.symbol != symbol:
                price, _ = self.own(member.symbol, day)
                yield science.PeerInput(member, price)


def verify_record(record: MechanismSessionRecord, universe: sources.UniverseCalendar,
                  cache: DayRawCache) -> math.VerificationReceipt:
    symbol, day = record.result.symbol, record.result.opening.date().isoformat()
    price, volume = cache.own(symbol, day)
    metadata = sources.session_inputs(symbol, day, price, volume).metadata
    session = sources.full_reference_session_inputs(symbol, day, price, volume)
    return reference.verify_session_result(record.result, record.peer_context, session,
        universe=universe, peers=cache.peers(symbol, day), source_metadata=metadata)


def iter_original_sessions(universe: sources.UniverseCalendar, raw_reader: original.AuditedChunkReader,
                           checkpoint: Callable[[str], object]) -> Iterator[MechanismSessionRecord]:
    cache = DayRawCache(universe, raw_reader)
    sources._need(callable(checkpoint), "checkpoint_callable_required")
    for day, symbol in sorted(cache.groups):
        checkpoint(original.BEFORE_SESSION)
        raw_reader.assert_unchanged()
        try:
            price, volume = cache.own(symbol, day)
            view = sources.session_inputs(symbol, day, price, volume)
            result, context = science.evaluate_session(view.session, peers=cache.peers(symbol, day), source_metadata=view.metadata)
            # A fresh private raw copy and separate decoder, never the produced
            # session, features, detector cache or output as a reference input.
            record = MechanismSessionRecord(tuple(cache.groups[(day, symbol)]), result,
                math.VerificationReceipt(science.POLICIES, len(science.POLICIES)), context)
            receipt = verify_record(record, universe, cache)
            record = MechanismSessionRecord(record.targets, result, receipt, context)
        finally:
            raw_reader.assert_unchanged()
            checkpoint(original.AFTER_SESSION)
        yield record
