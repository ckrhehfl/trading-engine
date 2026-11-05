"""Invented EF source populations and exact in-memory raw chunk pairs."""
from collections import Counter
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
import json

import pytest

from research import intraday_chart_population as population
from research import intraday_ed_sources as sources
from research.intraday_chart_inputs import build_peer_context
from research.intraday_chart_labels import build_chart_labels
from research.intraday_ed_inputs import MINUTE


FIRST, SECOND, THIRD = "2026-01-30", "2026-02-02", "2026-02-03"
SHORT = "2026-01-02"
A, B, C, D, E, F, G = (f"KRX:{number:06d}" for number in range(1, 8))


def _universe(rosters):
    targets = []
    for day, scenarios in rosters.items():
        for scenario, symbols in zip(("S1", "S2"), scenarios, strict=True):
            for rank, symbol in enumerate(symbols, 1):
                count = 389 if scenario == "S1" else None
                state = "stored_rows_completeness_unverified" if count else "no_stored_rows_unknown_reason"
                targets.append(sources.TargetCoordinate(scenario, symbol, day, rank, count, state,
                                                        f"{day} {scenario} opaque".encode()))
    return sources.UniverseCalendar(tuple(rosters), tuple(reversed(targets)), "a" * 64)


def _chunk(kind, day, symbol, *, missing=(), shift_suffix=0, invalid_volume=()):
    opening = sources.calendar_source.session_open(day)
    count = (sources.calendar_source.session_close(day) - opening) // MINUTE
    seed = int(symbol[-2:])
    rows = []
    for index in range(count):
        if index in missing:
            continue
        at = opening + index * MINUTE
        row = {"open_time_ms": int(at.timestamp()) * 1000, "fetched_at": "2026-10-30T01:00:00Z"}
        if kind == "CC":
            op = 1000 + seed * 31 + (index * seed) % 13 + (shift_suffix if index >= 60 else 0)
            close = op + (index + seed) % 7 - 3
            row.update(symbol=symbol, interval="1m", open=str(op), high=str(max(op, close) + 2),
                       low=str(min(op, close) - 1), close=str(close))
        else:
            row.update(volume="invalid" if index in invalid_volume else str(index % 11), quote_volume=None)
        rows.append(row)
    result = dict(symbol=symbol, session=day, observation_date=day, available_at=None,
                  retrieved_at="2026-10-30T00:00:00Z", source="collector klines", is_final=None,
                  evidence_level="assumed", rows=rows)
    if kind == "CX":
        result.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return result


class _Reader:
    def __init__(self, chunks=(), fail_check=None):
        self.chunks = {(kind, chunk["symbol"], chunk["session"]): chunk for kind, chunk in chunks}
        self.calls = []
        self.checks = 0
        self.fail_check = fail_check

    def load_raw_chunk(self, kind, symbol, day):
        self.calls.append((kind, symbol, day))
        return self.chunks.get((kind, symbol, day))

    def assert_unchanged(self):
        self.checks += 1
        if self.checks == self.fail_check:
            raise sources.SourceRefusal("invented_reader_changed")


def _stream(universe, reader, checkpoint=lambda _: None):
    return population.iter_chart_days(universe, reader, checkpoint)


def _view(reader, day, symbol):
    return sources.session_inputs(symbol, day, reader.chunks.get(("CC", symbol, day)),
                                  reader.chunks.get(("CX", symbol, day))).session


def test_entire_original_scope_audited_before_any_reader_action():
    calendar = _universe({FIRST: ((A,), (A,)), SECOND: ((B,), (B,))})
    malformed = replace(calendar.targets[-1], state="invented")
    calendar = replace(calendar, targets=(*calendar.targets[:-1], malformed))
    reader = _Reader()
    checkpoints = []
    with pytest.raises(sources.SourceRefusal, match="original_target_missingness_contract_differs"):
        next(_stream(calendar, reader, checkpoints.append))
    assert reader.calls == checkpoints == [] and reader.checks == 0


def test_stream_preserves_targets_raw_provenance_and_only_immediate_original_prior():
    rosters = {FIRST: ((A, B), (B, A)), SECOND: ((B,), (B,)), THIRD: ((A, B), (B, A))}
    calendar = _universe(rosters)
    symbol_days = ((FIRST, A), (FIRST, B), (SECOND, B), (THIRD, A), (THIRD, B))
    reader = _Reader((kind, _chunk(kind, day, symbol)) for day, symbol in symbol_days for kind in ("CC", "CX"))
    phases = []
    first_frames = {}
    for day in _stream(calendar, reader, phases.append):
        assert day.targets == tuple(target for target in calendar.targets if target.session == day.day)
        symbols = tuple(sorted(set(target.symbol for target in day.targets)))
        assert tuple(symbol for symbol, _ in day.provenance) == symbols
        for symbol, provenance in day.provenance:
            facts = json.loads(provenance)
            assert facts["available_at_assumption"] == "minute-end-plus2-not-retrieved-at"
            assert facts["price_basis"] is None and facts["session"] == day.day
            assert len(facts["sources"]["CC"]["raw_chunk_sha256"]) == 64
            assert facts["sources"]["CX"]["stored_fetched_at"][0][1] == "2026-10-30T01:00:00Z"
        assert reader.calls == [(kind, symbol, session) for session, symbol in symbol_days if session <= day.day
                                for kind in ("CC", "CX")]
        seen = []
        for frame in day.frames:
            seen.append((frame.offset, frame.symbol))
            if frame.offset == 60:
                first_frames[day.day, frame.symbol] = frame
        assert seen == [(offset, symbol) for offset in range(60, 331, 5) for symbol in symbols]

    first = first_frames[FIRST, A]
    assert first.chart.static[-1] == 0.0
    assert first.chart_issues.previous_expected_opening is None
    assert first.chart_issues.previous[0].reason == "previous_session_not_supplied"
    prior_from_first = first_frames[SECOND, B]
    assert prior_from_first.chart.static[-1] == 1.0
    assert prior_from_first.chart_issues.previous_expected_opening == sources.calendar_source.session_open(FIRST)
    stale = first_frames[THIRD, A]
    assert stale.chart.static[-1] == 0.0
    assert stale.chart_issues.previous_expected_opening == sources.calendar_source.session_open(SECOND)
    assert stale.chart_issues.previous[0].reason == "expected_previous_session_missing"
    assert first_frames[THIRD, B].chart.static[-1] == 1.0
    assert phases == ([population.BEFORE_DAY] + [population.AT_CLOCK] * 55 + [population.AFTER_DAY]) * 3


@pytest.mark.parametrize("close_frames", [False, True])
def test_partial_day_cannot_advance_to_another_raw_source_day(close_frames):
    calendar = _universe({FIRST: ((A,), (A,)), SECOND: ((A,), (A,))})
    reader = _Reader()
    stream = _stream(calendar, reader)
    first = next(stream)
    assert next(first.frames).offset == 60
    if close_frames:
        first.frames.close()
    with pytest.raises(sources.SourceRefusal, match="chart_day_frames_must_be_consumed"):
        next(stream)
    assert tuple(first.frames) == ()
    assert reader.calls == [("CC", A, FIRST), ("CX", A, FIRST)]


def test_reader_identity_checked_before_day_is_exposed():
    calendar = _universe({FIRST: ((A,), (A,))})
    with pytest.raises(sources.SourceRefusal, match="invented_reader_changed"):
        next(_stream(calendar, _Reader(fail_check=2)))


@pytest.mark.parametrize("identical_rosters", [False, True])
def test_leave_one_out_exact_peers_match_reference_with_original_rosters_and_missingness(monkeypatch, identical_rosters):
    symbols = (A, B, C, D, E, F, G)
    calendar = _universe({FIRST: (symbols, symbols if identical_rosters else symbols[:-1])})
    price_chunks = [("CC", _chunk("CC", FIRST, symbol, missing=(5,) if symbol == C else ())) for symbol in symbols]
    reader = _Reader([*price_chunks, ("CX", _chunk("CX", FIRST, C))])
    raw_contexts = []
    original_tensor = population.peer_tensor
    built_charts = Counter()
    original_chart = population.build_chart_inputs

    def record_context(context):
        raw_contexts.append(context)
        return original_tensor(context)

    def count_chart(session, *args, **kwargs):
        built_charts[session.symbol] += 1
        return original_chart(session, *args, **kwargs)

    monkeypatch.setattr(population, "peer_tensor", record_context)
    monkeypatch.setattr(population, "build_chart_inputs", count_chart)
    with closing(_stream(calendar, reader)) as stream:
        day = next(stream)
        frames = {symbol: next(day.frames) for symbol in symbols}

    sessions = {symbol: _view(reader, FIRST, symbol) for symbol in frames}
    assert built_charts == Counter({symbol: 1 for symbol in frames})
    references = []
    for scenario in ("S1", "S2"):
        roster = tuple(sorted(target.symbol for target in calendar.targets if target.scenario == scenario))
        for symbol in roster:
            frame = frames[symbol]
            peers = next(peer for peer in frame.peers if peer.scenario == scenario)
            expected = build_peer_context(sessions[symbol], frame.bar_end, frame.decision_at,
                                          expected_peer_ids=tuple(peer for peer in roster if peer != symbol),
                                          peer_sessions=sessions)
            references.append(expected)
            assert (peers.tensor, peers.expected_peer_ids, peers.common_peer_ids, peers.denominator, peers.issues) == (
                original_tensor(expected), expected.expected_peer_ids, expected.common_peer_ids,
                expected.denominator, expected.issues)
    assert raw_contexts == (references[:len(symbols)] if identical_rosters else references)
    assert len(frames[G].peers) == (2 if identical_rosters else 1)
    assert [peer.tensor is not None for peer in frames[A].peers] == [True, identical_rosters]
    if identical_rosters:
        assert frames[A].peers[0].tensor is frames[A].peers[1].tensor
    assert frames[C].chart is None and all(peer.tensor is not None for peer in frames[C].peers)
    assert frames[C].chart_issues.latest[0].reason == "price_missing"
    assert frames[C].chart_issues.volume == ()


def test_feature_issues_and_support_preserve_independence_from_quote_and_labels():
    calendar = _universe({FIRST: ((A,), (A,))})
    cx = _chunk("CX", FIRST, A, invalid_volume=(1,))
    baseline = _Reader([("CC", _chunk("CC", FIRST, A)), ("CX", cx)])
    changed = _Reader([("CC", _chunk("CC", FIRST, A, missing=(63, 93, 102), shift_suffix=50)), ("CX", cx)])
    all_frames = []
    for reader in (baseline, changed):
        with closing(_stream(calendar, reader)) as stream:
            day = next(stream)
            all_frames.append({frame.offset: frame for frame in day.frames})
    first, second = (frames[60] for frames in all_frames)
    assert first.chart == second.chart and first.peers == second.peers
    assert first.chart.static[-1] == 0.0
    assert first.chart.minute[0][-2:] == (0.0, 1.0)
    assert first.chart.minute[1][-2:] == (0.0, 0.0)
    assert first.chart_issues.volume[0].reason == "volume_conflict"
    assert first.entry_clock_eligible is second.entry_clock_eligible is True
    assert first.execution_state == "available" and isinstance(first.execution_open, Fraction)
    assert first.execution_mature_at == first.execution_at + 3 * MINUTE
    assert second.execution_state == "unavailable" and second.execution_open is None
    assert second.execution_mature_at is None and second.execution_issue.reason == "price_missing"
    for frame, reader in zip((first, second), (baseline, changed), strict=True):
        session = _view(reader, FIRST, A)
        assert frame.labels == build_chart_labels(session, frame.bar_end, frame.decision_at,
                                                  label_cutoff=session.closing + 3 * MINUTE)
    assert first.labels.horizons[-1].mature_at == first.execution_at + 33 * MINUTE
    assert second.labels.horizons[-1].status == "unavailable"

    regular = all_frames[0]
    assert tuple(offset for offset, frame in regular.items() if frame.labels is not None) == population.ENTRY_OFFSETS
    assert tuple(offset for offset, frame in regular.items() if frame.chart is not None) == population.FEATURE_OFFSETS
    for offset in (320, 325, 330):
        quote_only = regular[offset]
        assert quote_only.chart is quote_only.chart_issues is quote_only.labels is None and quote_only.peers == ()
        assert quote_only.execution_at == quote_only.bar_end + 3 * MINUTE
        assert quote_only.execution_state == "available"
    assert all_frames[1][165].chart is not None
    assert all_frames[1][165].chart_issues.latest == ()
    assert all_frames[1][165].chart_issues.coarse


def test_short_day_never_looks_up_outside_continuous_clock_quotes(monkeypatch):
    reader = _Reader([("CC", _chunk("CC", SHORT, A))])
    original = sources.session_inputs
    looked_up = []

    class Tracked(dict):
        def get(self, key):
            looked_up.append(key)
            return super().get(key)

    def track(*args, **kwargs):
        view = original(*args, **kwargs)
        return replace(view, session=replace(view.session, minutes=Tracked(view.session.minutes)))

    monkeypatch.setattr(sources, "session_inputs", track)
    with closing(_stream(_universe({SHORT: ((A,), (A,))}), reader)) as stream:
        day = next(stream)
        frames = {frame.offset: frame for frame in day.frames}

    assert day.closing - day.opening == 330 * MINUTE
    assert frames[270].entry_clock_eligible is True
    assert frames[285].entry_clock_eligible is frames[300].entry_clock_eligible is False
    assert [label.status for label in frames[285].labels.horizons] == ["available", "outside_clock"]
    assert [label.status for label in frames[300].labels.horizons] == ["outside_clock", "outside_clock"]
    assert frames[310].execution_state == "available"
    assert frames[315].chart is not None
    for offset in (315, 320, 325, 330):
        frame = frames[offset]
        assert frame.execution_state == "outside_clock" and frame.execution_open is None
        assert frame.execution_issue.reason == "execution_outside_continuous_cash_clock"
        assert frame.execution_at not in looked_up
