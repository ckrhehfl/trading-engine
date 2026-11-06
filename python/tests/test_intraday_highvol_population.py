"""Invented original-calendar coverage and exact-history integration checks."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from research import intraday_ed_sources as sources
from research import intraday_highvol_population as population
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


SYMBOL = "KRX:000001"
OTHER = "KRX:000002"


def days(count=1):
    current, result = date(2026, 6, 1), []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(result)


def universe(calendar=None, *, rosters=None):
    calendar = days() if calendar is None else calendar
    targets = []
    for day in calendar:
        symbols = (SYMBOL,) if rosters is None else rosters[day]
        for scenario in ("S1", "S2"):
            for rank, symbol in enumerate(symbols, 1):
                targets.append(sources.TargetCoordinate(scenario, symbol, day, rank, None,
                    "no_stored_rows_unknown_reason", b"opaque original metadata\x00"))
    return sources.UniverseCalendar(calendar, tuple(targets), "a" * 64)


def chunk(kind, day, symbol=SYMBOL, *, count=60, skip=(), slope=1, volume="0"):
    opening = sources.calendar_source.session_open(day)
    rows = []
    for index in range(count):
        if index in skip:
            continue
        start = opening + index * MINUTE
        row = {"open_time_ms": int(start.timestamp()) * 1000, "fetched_at": "2026-10-30T00:00:00Z"}
        if kind == "CC":
            price = Decimal(1000 + slope * index)
            close = price + slope
            row.update(symbol=symbol, interval="1m", open=str(price), high=str(max(price, close) + 1),
                       low=str(min(price, close) - 1), close=str(close))
        else:
            row.update(volume=volume, quote_volume=None)
        rows.append(row)
    result = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
              "retrieved_at": "2026-10-30T00:00:00Z", "source": "collector klines",
              "is_final": None, "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        result.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return result


class Reader:
    def __init__(self, values=None, *, fail_check=None):
        self.values = values or {}
        self.calls = []
        self.checks = 0
        self.fail_check = fail_check

    def assert_unchanged(self):
        self.checks += 1
        if self.checks == self.fail_check:
            raise ValueError("invented raw reader changed")

    def load_raw_chunk(self, kind, symbol, session):
        self.calls.append((kind, symbol, session))
        return self.values.get((kind, symbol, session))


def records(calendar, reader):
    return tuple(population.iter_coverage_records(calendar, reader, lambda _: None))


def test_original_unknown_coordinates_keep_both_scenarios_and_read_once():
    original, reader = universe(), Reader()
    row, = records(original, reader)
    assert row.targets == original.targets
    assert row.source_metadata and row.price_chunk_present is False and row.volume_chunk_present is False
    assert reader.calls == [("CC", SYMBOL, days()[0]), ("CX", SYMBOL, days()[0])]
    assert len(row.decisions) == 58 and row.decisions[0].current.offset == 15
    assert all(item.status == "unavailable" for item in row.decisions)
    first, remaining = row.minute_coverage
    assert first.price_states == (("missing_minute", 60),)
    assert remaining.price_states == (("missing_minute", 330),)
    with pytest.raises(FrozenInstanceError):
        row.session = "changed"


def test_full_first_hour_coverage_is_separate_from_baseline_warmup_and_volume():
    day = days()[0]
    price = chunk("CC", day, count=60)
    volume = chunk("CX", day, count=60, skip=(5,), volume="0")
    reader = Reader({("CC", SYMBOL, day): price, ("CX", SYMBOL, day): volume})
    row, = records(universe(), reader)
    first = row.minute_coverage[0]
    assert first.price_states == (("observed", 60),)
    assert first.volume_states == (("observed", 59), ("volume_missing", 1))
    assert first.zero_volume_minutes == 59
    initial = row.decisions[0]
    assert initial.current.price_status == "available" and initial.current.shares is None
    assert initial.reason == "insufficient_original_history"
    assert all(item.current.price_status == "available" for item in row.decisions[:10])
    assert row.decisions[10].current.price_status == "unavailable"


def test_exact_twenty_original_dates_then_high_and_no_future_or_duplicate_reads():
    calendar = days(21)
    values = {("CC", SYMBOL, day): chunk("CC", day, count=15, slope=10 if index == 20 else 1)
              for index, day in enumerate(calendar)}
    reader = Reader(values)
    output = records(universe(calendar), reader)
    assert all(row.decisions[0].reason == "insufficient_original_history" for row in output[:20])
    last = output[-1].decisions[0]
    assert last.status == "high" and last.known_history_count == 20
    assert last.expected_prior_openings == tuple(sources.calendar_source.session_open(day) for day in calendar[:-1])
    assert output[-1].episodes.left_censored_clusters == 1
    assert reader.calls == [(kind, SYMBOL, day) for day in calendar for kind in ("CC", "CX")]


def test_absent_prior_chunk_is_not_replaced_by_an_older_successful_day():
    calendar = days(22)
    values = {("CC", SYMBOL, day): chunk("CC", day, count=15)
              for index, day in enumerate(calendar) if index != 10}
    output = records(universe(calendar), Reader(values))
    last = output[-1].decisions[0]
    assert last.status == "unavailable" and last.reason == "prior_history_unavailable"
    assert last.expected_prior_openings[0] == sources.calendar_source.session_open(calendar[1])
    assert last.known_history_count == 19
    assert [issue.reason for issue in last.history_issues] == [population.SOURCE_ABSENT]


def test_new_population_symbol_retains_absent_history_without_extra_raw_requests():
    calendar = days(21)
    rosters = {day: (OTHER,) for day in calendar[:-1]} | {calendar[-1]: (SYMBOL,)}
    original = universe(calendar, rosters=rosters)
    reader = Reader({("CC", SYMBOL, calendar[-1]): chunk("CC", calendar[-1], count=15)})
    last = records(original, reader)[-1].decisions[0]
    assert last.reason == "prior_history_unavailable"
    assert last.known_history_count == 0
    assert {issue.reason for issue in last.history_issues} == {population.OUTSIDE_POPULATION}
    assert len(reader.calls) == 42
    assert not any(symbol == SYMBOL and day != calendar[-1] for _, symbol, day in reader.calls)


def test_changing_later_session_does_not_change_previous_records():
    calendar = days(2)
    first = chunk("CC", calendar[0])
    basic = records(universe(calendar), Reader({("CC", SYMBOL, calendar[0]): first}))
    changed = records(universe(calendar), Reader({("CC", SYMBOL, calendar[0]): first,
        ("CC", SYMBOL, calendar[1]): chunk("CC", calendar[1], slope=50)}))
    assert basic[0] == changed[0]
    assert basic[1] != changed[1]


@pytest.mark.parametrize("fail_check", [1, 2])
def test_reader_mutation_fails_before_record_yield(fail_check):
    with pytest.raises(ValueError, match="reader changed"):
        records(universe(), Reader(fail_check=fail_check))


def test_invalid_original_population_rejected_before_any_raw_read():
    original = universe()
    reader = Reader()
    with pytest.raises(ValueError, match="duplicate_original_target"):
        records(replace(original, targets=(*original.targets, original.targets[0])), reader)
    assert reader.calls == []


def test_minute_coverage_preserves_late_payload_as_unavailable_at_assumed_clock():
    opening = sources.calendar_source.session_open(days()[0])
    prices = (Decimal(100), Decimal(101), Decimal(99), Decimal(100))
    late = Minute(SYMBOL, opening, opening + 4 * MINUTE, prices, Decimal(0))
    session = SessionInputs(SYMBOL, opening, opening + 5 * MINUTE, {opening: late})
    first, remaining = population.minute_coverage(session)
    assert first.price_states == (("late_at_assumed_clock", 1), ("missing_minute", 4))
    assert first.volume_states == first.price_states and first.zero_volume_minutes == 0
    assert remaining.start_offset == remaining.end_offset_exclusive == 5


def test_minute_coverage_does_not_accept_invalid_numeric_payload():
    opening = sources.calendar_source.session_open(days()[0])
    bad = Minute(SYMBOL, opening, opening + 3 * MINUTE,
                 (Decimal(100), Decimal(99), Decimal(98), Decimal(100)), Decimal(0))
    with pytest.raises(ValueError, match="invalid_ohlc_order"):
        population.minute_coverage(SessionInputs(SYMBOL, opening, opening + MINUTE, {opening: bad}))
