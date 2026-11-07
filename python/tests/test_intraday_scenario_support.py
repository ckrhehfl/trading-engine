"""Invented support windows and audited-reader boundaries for EH."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_scenario_support import (
    AFTER_SESSION, BEFORE_SESSION, iter_support_records, measure_price_support,
)


DAYS = ("2099-02-02", "2099-02-03", "2099-02-05")
A, B, C = "KRX:Z00001", "KRX:Z00002", "KRX:Z00003"


def universe():
    coords = (
        (DAYS[0], "S1", (A, B)), (DAYS[0], "S2", (B, A)),
        (DAYS[1], "S1", (A, C)), (DAYS[1], "S2", (C,)),
        (DAYS[2], "S1", (B, C)), (DAYS[2], "S2", (B, C)),
    )
    targets = tuple(
        sources.TargetCoordinate(clock, symbol, day, rank, None, "no_stored_rows_unknown_reason",
                                 f"invented metadata {day} {clock}".encode())
        for day, clock, symbols in coords for rank, symbol in enumerate(symbols, 1)
    )
    return sources.UniverseCalendar(DAYS, targets[::-1], "a" * 64)


def chunk(day, symbol, *, count=390, missing=(), conflicts=(), invalid=()):
    opening = sources.calendar_source.session_open(day)
    rows = []
    for offset in range(count):
        if offset in missing:
            continue
        row = {
            "symbol": symbol, "interval": "1m",
            "open_time_ms": int((opening + offset * MINUTE).timestamp()) * 1000,
            "open": "NaN" if offset in invalid else "100.0000000000000000001",
            "high": "101", "low": "99", "close": "100",
            "fetched_at": "2099-03-01T00:00:00Z",
        }
        rows.append(row)
        if offset in conflicts:
            rows.append(dict(row, open_time_ms=row["open_time_ms"] + 15000))
    return dict(symbol=symbol, session=day, observation_date=day, available_at=None,
                retrieved_at="2099-03-01T00:00:00Z", source="collector klines",
                is_final=None, evidence_level="assumed", rows=rows)


class InventedReader:
    def __init__(self, *chunks, fail_check=None):
        self.chunks = {(item["session"], item["symbol"]): item for item in chunks}
        self.events = []
        self.checks = 0
        self.fail_check = fail_check

    def assert_unchanged(self):
        self.checks += 1
        self.events.append("unchanged")
        if self.checks == self.fail_check:
            raise sources.SourceRefusal("invented_reader_changed")

    def load_raw_chunk(self, kind, symbol, session):
        self.events.append((kind, symbol, session))
        assert kind == "CC"
        return self.chunks.get((session, symbol))


def test_original_union_and_exact_previous_calendar_are_preserved():
    original = universe()
    reader = InventedReader(chunk(DAYS[0], A), chunk(DAYS[0], B),
                            chunk(DAYS[1], A, count=60, missing=(17,), conflicts=(16,), invalid=(30,)),
                            chunk(DAYS[2], B, count=15))
    stream = iter_support_records(original, reader, reader.events.append)
    assert reader.events == []
    first = next(stream)
    assert reader.events == [BEFORE_SESSION, "unchanged", ("CC", A, DAYS[0]), "unchanged", AFTER_SESSION]
    records = (first, *stream)
    expected = [(DAYS[0], A), (DAYS[0], B), (DAYS[1], A), (DAYS[1], C), (DAYS[2], B), (DAYS[2], C)]
    assert [(item.session, item.symbol) for item in records] == expected
    assert [entry for entry in reader.events if isinstance(entry, tuple)] == [
        ("CC", symbol, day) for day, symbol in expected
    ]
    assert [(target.scenario, target.rank) for target in records[0].targets] == [("S1", 1), ("S2", 2)]
    for item in records:
        retained = tuple(sorted(
            (t for t in original.targets if (t.session, t.symbol) == (item.session, item.symbol)),
            key=lambda t: t.scenario,
        ))
        assert item.targets == retained and all(a is b for a, b in zip(item.targets, retained, strict=True))
        assert item.cross_session_basis == "unknown"

    assert [item.prior_session for item in records] == [None, None, DAYS[0], DAYS[0], DAYS[1], DAYS[1]]
    assert [item.prior_status for item in records] == [
        "outside_calendar", "outside_calendar", "complete", "outside_union", "outside_union", "chunk_absent",
    ]
    observed = records[2]
    assert observed.prior_full.complete
    assert observed.opening_range_price_supported
    assert [cell.scope for cell in observed.coverage] == ["first15", "first20", "first60", "full"]
    assert [cell.valid_exact for cell in observed.coverage] == [15, 18, 57, 57]
    assert dict(observed.coverage[1].counts) == {"conflict": 1, "missing": 1, "observed": 18}
    assert dict(observed.coverage[-1].counts) == {"conflict": 2, "missing": 331, "observed": 57}
    assert records[4].opening_range_price_supported and records[4].prior_full is None
    assert not records[5].price_chunk_present and records[5].prior_full.valid_exact == 0


def test_exact_price_late_at_prefix_is_not_missing_and_can_be_ready_later():
    opening = datetime(2099, 2, 2, 9, tzinfo=UTC)
    rows = {}
    prices = (Decimal(100), Decimal(101), Decimal(99), Decimal(100))
    for offset in range(25):
        start = opening + offset * MINUTE
        delay = {2: 18, 19: 23, 20: 50}.get(offset, offset + 3)
        rows[start] = Minute(A, start, opening + delay * MINUTE, prices, None, volume_state="missing")
    del rows[opening + 4 * MINUTE]
    at = opening + 3 * MINUTE
    rows[at] = replace(rows[at], ohlc=None, price_state="conflict")
    coverage, latest = measure_price_support(SessionInputs(A, opening, opening + 25 * MINUTE, rows))
    assert latest == opening + 50 * MINUTE
    assert [cell.valid_exact for cell in coverage] == [13, 18, 23, 23]
    assert dict(coverage[0].counts) == {"conflict": 1, "late": 1, "missing": 1, "observed": 12}
    assert dict(coverage[1].counts) == {"conflict": 1, "late": 1, "missing": 1, "observed": 17}
    assert dict(coverage[2].counts)["outside_session"] == 35
    assert dict(coverage[-1].counts) == {"conflict": 1, "late": 1, "missing": 1, "observed": 22}


def test_prior_price_complete_requires_valid_entire_expected_day():
    reader = InventedReader(chunk(DAYS[0], A, missing=(389,)), chunk(DAYS[1], A, count=60))
    records = list(iter_support_records(universe(), reader, reader.events.append))
    current = records[2]
    assert current.prior_session == DAYS[0] and current.prior_status == "incomplete"
    assert current.prior_full.valid_exact == 389
    assert not current.prior_full.complete
    assert current.opening_range_price_supported and current.cross_session_basis == "unknown"


def test_reader_change_and_malformed_union_refuse_without_a_record():
    original = universe()
    reader = InventedReader(fail_check=2)
    with pytest.raises(sources.SourceRefusal, match="invented_reader_changed"):
        next(iter_support_records(original, reader, reader.events.append))
    assert reader.events == [BEFORE_SESSION, "unchanged", ("CC", A, DAYS[0]), "unchanged"]

    reader = InventedReader()
    bad = replace(original, targets=(*original.targets, original.targets[0]))
    with pytest.raises(sources.SourceRefusal, match="duplicate_original_target"):
        next(iter_support_records(bad, reader, reader.events.append))
    assert reader.events == []
