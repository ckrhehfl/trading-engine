"""Compare independently reconstructed EH support against invented saved rows."""

from dataclasses import replace
from datetime import timedelta

import pytest

from research import intraday_scenario_support as producer
from research import intraday_scenario_support_reference as reference
from research.intraday_ed_sources import SourceRefusal

from test_intraday_scenario_support import A, B, C, DAYS, InventedReader, chunk, universe


def _stored(original, reader):
    return tuple(producer.iter_support_records(original, reader, lambda _phase: None))


def test_raw_verification_is_independent_and_consumes_saved_eof(monkeypatch):
    original = universe()
    partial = chunk(DAYS[1], A, count=60, missing=(17,), conflicts=(16,), invalid=(30,))
    opening_ms = partial["rows"][0]["open_time_ms"]
    for row in partial["rows"]:
        if row["open_time_ms"] == opening_ms + 19 * 60_000:
            # The tiny excess vanishes in float but makes this candle invalid.
            row["high"] = "100"
    partial["rows"].reverse()
    chunks = (chunk(DAYS[0], A), chunk(DAYS[0], B, count=0), partial, chunk(DAYS[2], B, count=15))
    records = _stored(original, InventedReader(*chunks))

    def forbidden(*_args, **_kwargs):
        pytest.fail("Reference consulted the producer or its minute parser")

    monkeypatch.setattr(producer, "measure_price_support", forbidden)
    monkeypatch.setattr(producer, "iter_support_records", forbidden)
    monkeypatch.setattr(producer.sources, "full_reference_session_inputs", forbidden)
    reader = InventedReader(*chunks)

    def stored():
        yield from records
        reader.events.append("saved_eof")

    receipt = reference.verify_support_records(stored(), original, reader, reader.events.append)
    assert (receipt.union_records, receipt.original_targets, receipt.calendar_sessions) == (6, 11, 3)
    assert (receipt.price_chunk_absent_records, receipt.opening_range_supported_records) == (2, 3)
    assert receipt.available_at_assumption == "minute-end-plus-two"
    assert receipt.historical_availability_certified is False
    assert {scope: dict(states) for scope, states in receipt.coverage_counts} == {
        "first15": {"observed": 45, "missing": 45},
        "first20": {"observed": 52, "missing": 66, "conflict": 2},
        "first60": {"observed": 131, "missing": 226, "conflict": 3},
        "full": {"observed": 461, "missing": 1876, "conflict": 3},
    }
    assert dict(receipt.prior_status_counts) == {
        "outside_calendar": 2, "outside_union": 2, "complete": 1, "chunk_absent": 1,
    }
    assert [entry for entry in reader.events if isinstance(entry, tuple)] == [
        ("CC", A, DAYS[0]), ("CC", B, DAYS[0]), ("CC", A, DAYS[1]),
        ("CC", C, DAYS[1]), ("CC", B, DAYS[2]), ("CC", C, DAYS[2]),
    ]
    assert reader.events[:5] == [
        reference.BEFORE_SESSION, "unchanged", ("CC", A, DAYS[0]), "unchanged", reference.AFTER_SESSION,
    ]
    assert reader.events[-2:] == ["saved_eof", "unchanged"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda rows: rows[:-1], "missing_stored_support_record"),
        (lambda rows: (*rows, None), "extra_stored_support_record"),
        (lambda rows: (rows[1], rows[0], *rows[2:]), "stored_support_record_differs"),
        (lambda rows: (replace(rows[0], price_chunk_present=1), *rows[1:]), "stored_support_record_differs"),
        (lambda rows: (replace(rows[0], targets=rows[0].targets[:1]), *rows[1:]), "stored_support_record_differs"),
        (lambda rows: (*rows[:2], replace(rows[2], prior_status="outside_union"), *rows[3:]),
         "stored_support_record_differs"),
        (lambda rows: (replace(rows[0], cross_session_basis="adjusted"), *rows[1:]), "stored_support_record_differs"),
        (lambda rows: (replace(rows[0], coverage=(replace(rows[0].coverage[0], counts=(("missing", 15),)),
                                                  *rows[0].coverage[1:])), *rows[1:]),
         "stored_support_record_differs"),
    ],
)
def test_saved_mismatch_refuses(mutate, reason):
    original, raw = universe(), chunk(DAYS[0], A)
    records = _stored(original, InventedReader(raw))
    with pytest.raises(SourceRefusal, match=reason):
        reference.verify_support_records(mutate(records), original, InventedReader(raw), lambda _phase: None)


def test_receipt_requires_exact_calendar_and_short_session_geometry(monkeypatch):
    original = universe()
    calendar = producer.sources.calendar_source
    monkeypatch.setattr(calendar, "session_close", lambda day: calendar.session_open(day) + timedelta(minutes=12))
    chunks = (chunk(DAYS[0], A, count=11), chunk(DAYS[1], A, count=12), chunk(DAYS[2], B, count=12))
    records = _stored(original, InventedReader(*chunks))
    receipt = reference.verify_support_records(records, original, InventedReader(*chunks), lambda _phase: None)
    assert receipt.opening_range_supported_records == 0
    assert dict(receipt.coverage_counts[0][1]) == {"observed": 35, "missing": 37, "outside_session": 18}
    assert dict(receipt.prior_status_counts)["incomplete"] == 1


def test_audit_refuses_changed_source_invalid_raw_and_bad_saved_footer():
    original = universe()
    records = _stored(original, InventedReader())
    reader = InventedReader(fail_check=2)
    with pytest.raises(SourceRefusal, match="invented_reader_changed"):
        reference.verify_support_records(records, original, reader, reader.events.append)
    assert reader.events == [reference.BEFORE_SESSION, "unchanged", ("CC", A, DAYS[0]), "unchanged"]

    reader = InventedReader()
    reader.chunks[(DAYS[0], A)] = chunk(DAYS[0], B)
    with pytest.raises(SourceRefusal, match="session_source_identity_differs"):
        reference.verify_support_records(records, original, reader, reader.events.append)
    assert reader.events[-2:] == ["unchanged", reference.AFTER_SESSION]

    def bad_footer():
        yield from records
        raise ValueError("invented_footer_invalid")

    with pytest.raises(ValueError, match="invented_footer_invalid"):
        reference.verify_support_records(bad_footer(), original, InventedReader(), lambda _phase: None)
