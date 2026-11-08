"""Invented EI reference tests: causality and independently computed science."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import gc
import hashlib
import weakref

import pytest

from research import intraday_context_situations as producer
from research import intraday_context_support as storage
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_support_reference import _StreamingTotals, audit_saved, reference_frames, verify_record
from research.intraday_ed_inputs import InputRefusal, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate


OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
MINUTE = timedelta(minutes=1)
BARS = ((100, 101, 97, 100), (100, 104, 100, 103), (103, 107, 103, 106),
        (106, 114, 106, 113), (111, 112, 109, 110), (110, 114, 110, 112),
        (111, 112, 110, 111), (111, 114, 111, 113), (113, 115, 112, 114))


def session(bars=BARS):
    rows = {}
    for number, bar in enumerate(bars):
        for offset in range(5):
            stamp = OPEN + (number * 5 + offset) * MINUTE
            rows[stamp] = Minute("invented", stamp, stamp + 3 * MINUTE,
                                 tuple(Decimal(x) for x in bar), Decimal(offset))
    return SessionInputs("invented", OPEN, OPEN + len(bars) * 5 * MINUTE, rows)


def test_reference_reconstructs_first_pullback_then_distinct_retests(monkeypatch):
    source = session()
    expected = tuple(producer.scan_context_frames(source, through=source.closing))
    for name in ("_support", "_anchor", "_situation", "_frame", "context_frame", "scan_context_frames"):
        monkeypatch.setattr(producer, name, lambda *a, **k: pytest.fail("producer algorithm called"), raising=False)
    actual = tuple(reference_frames(source, source.closing))
    assert actual == expected
    assert [frame.classification for frame in actual] == ["A", "A", "B"]
    assert actual[0].anchor.pre_peak_range == 4
    assert actual[0].metrics.pullback_depth == Fraction(5, 14)
    assert actual[-1].metrics.retest_episodes == 2
    assert actual[-1].metrics.resumption_at == OPEN + 42 * MINUTE
    assert actual[0].windows[1].price_counts == (("observed", 30), ("outside_session", 30))
    assert actual[0].windows[0].zero_volume_minutes == 6


def test_late_opening_row_recovers_support_without_recovering_first_claim():
    source = session()
    source.minutes[OPEN] = replace(source.minutes[OPEN], available_at=OPEN + 37 * MINUTE)
    actual = tuple(reference_frames(source, source.closing))
    assert actual == tuple(producer.scan_context_frames(source, through=source.closing))
    assert actual[0].reason == "opening_price_prefix_incomplete"
    assert actual[1].prefix_support.price_complete
    assert not actual[1].prefix_observed_on_grid
    assert actual[1].reason == "prefix_not_observed_on_grid"
    assert all(frame.classification == "unknown" for frame in actual)


def test_source_prefix_is_read_once_and_future_payload_stays_unread():
    class ReadOnce(dict):
        def get(self, key, default=None):
            assert key not in self.seen
            self.seen.add(key)
            return super().get(key, default)

    source = session()
    rows = ReadOnce(source.minutes)
    rows.seen = set()
    rows[OPEN + 40 * MINUTE] = object()
    actual = tuple(reference_frames(replace(source, minutes=rows), OPEN + 37 * MINUTE))
    assert len(actual) == 2
    assert len(rows.seen) == 35
    assert OPEN + 40 * MINUTE not in rows.seen


def test_late_invalid_payload_is_rejected_only_at_visibility():
    source = session()
    source.minutes[OPEN] = replace(source.minutes[OPEN], ohlc="corrupt", available_at=OPEN + 37 * MINUTE)
    assert next(reference_frames(source, OPEN + 32 * MINUTE)).classification == "unknown"
    with pytest.raises(InputRefusal, match="ohlc_shape"):
        tuple(reference_frames(source, OPEN + 37 * MINUTE))


def test_invalidation_survives_rebound_and_volume_unknown_is_not_zero():
    bars = BARS[:5] + ((110, 114, 99, 100),) + BARS[6:]
    source = session(bars)
    source.minutes[OPEN] = replace(source.minutes[OPEN], shares=None, volume_state="unit_unknown")
    actual = tuple(reference_frames(source, source.closing))
    assert actual == tuple(producer.scan_context_frames(source, through=source.closing))
    assert all(frame.reason == "anchor_invalidated" for frame in actual)
    assert actual[-1].metrics.invalidated_at == OPEN + 32 * MINUTE
    assert actual[0].windows[0].volume_total is None
    assert actual[0].windows[0].zero_volume_minutes == 5
    assert actual[0].prefix_observed_on_grid


@pytest.mark.parametrize("change", [
    {"symbol": "wrong"}, {"available_at": OPEN + 2 * MINUTE},
    {"price_state": "missing"}, {"shares": Decimal(-1)},
])
def test_malformed_visible_source_is_refused(change):
    source = session()
    source.minutes[OPEN] = replace(source.minutes[OPEN], **change)
    with pytest.raises(InputRefusal):
        tuple(reference_frames(source, source.closing))


def test_nonminute_clock_and_crossdate_session_are_refused():
    source = session()
    with pytest.raises(InputRefusal, match="minute_clock"):
        tuple(reference_frames(source, OPEN + timedelta(seconds=1)))
    with pytest.raises(InputRefusal, match="session_geometry"):
        tuple(reference_frames(replace(source, closing=OPEN + timedelta(days=1)), source.closing))


def record_fixture():
    source = session()
    targets = (TargetCoordinate("S1", source.symbol, OPEN.date().isoformat(), 1, 45,
                                "stored_rows_completeness_unverified", b"invented"),)
    saved = storage.day_record(targets, producer.scan_context_frames(source, through=source.closing),
                               price_present=True, volume_present=True)
    return source, targets, saved


def test_independent_record_verifies_and_refuses_changed_science_or_timing(monkeypatch):
    source, targets, saved = record_fixture()
    monkeypatch.setattr(storage, "day_record", lambda *a, **k: pytest.fail("producer aggregation called"))
    verify_record(saved, targets, source, price_present=True, volume_present=True)
    peak = OPEN + 15 * MINUTE
    old = source.minutes[peak]
    source.minutes[peak] = replace(old, ohlc=(old.ohlc[0], Decimal(115), old.ohlc[2], old.ohlc[3]))
    with pytest.raises(InputRefusal, match="record_reconstruction_differs"):
        verify_record(saved, targets, source, price_present=True, volume_present=True)
    source.minutes[peak] = old
    source.minutes[OPEN] = replace(source.minutes[OPEN], available_at=OPEN + 33 * MINUTE)
    with pytest.raises(InputRefusal, match="record_reconstruction_differs"):
        verify_record(saved, targets, source, price_present=True, volume_present=True)


@pytest.mark.parametrize("field,value", [("frames", 4), ("price_chunk_present", 1), ("frame_sha256", "0" * 64)])
def test_independent_record_refuses_mutated_saved_denominators(field, value):
    source, targets, saved = record_fixture()
    saved[field] = value
    with pytest.raises(InputRefusal, match="record_reconstruction_differs"):
        verify_record(saved, targets, source, price_present=True, volume_present=True)


def saved_fixture():
    source = session(BARS + (BARS[-1],) * 69)
    targets = (TargetCoordinate("S1", source.symbol, OPEN.date().isoformat(), 1, 390,
                                "stored_rows_completeness_unverified", b"invented"),)
    targets += (replace(targets[0], scenario="S2"),)
    frames = tuple(producer.scan_context_frames(source, through=source.closing))
    record = storage.day_record(targets, frames, price_present=True, volume_present=True)
    geometry = {"sessions": 1, "union_coordinates": 1, "targets_per_scenario": 1}
    manifest = [{"name": name, "original_path": "/invented/" + name, "sha256": "a" * 64,
                 "size": 1, "maximum": maximum} for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS]
    rows = [{"kind": "header", "schema": storage.SCHEMA, "geometry": geometry, "input_manifest": manifest,
             "universe_sha256": "a" * 64, "calendar_sha256": wire.digest(wire.encode([record["session"]])),
             "case_selection": "first two distinct sorted coordinates per class, earliest class clock; outcome blind"}, record]
    for kind in storage.CLASSES:
        first = next((frame for frame in frames if frame.classification == kind), None)
        if first is not None:
            rows.append({"kind": "chart_case", "session": record["session"], "symbol": record["symbol"],
                         "classification": kind, "frame": storage.frame_payload(first), "chart_prefix": storage.exact(first.prefix)})
    rows.append({"kind": "footer", "records": 1, "cases": len(rows) - 2, "summary": storage.summarize([record])})
    return rows, geometry, manifest


def save_and_audit(tmp_path, rows, geometry, manifest, suffix=b""):
    data = b"".join(wire.encode(row) for row in rows) + suffix
    path = tmp_path / "invented-support.jsonl"
    path.write_bytes(data)
    return audit_saved(path, expected_sha256=hashlib.sha256(data).hexdigest(), expected_bytes=len(data),
                       expected_geometry=geometry, expected_input_manifest=manifest)


def test_saved_audit_independently_sums_populations_and_checks_cases(tmp_path, monkeypatch):
    rows, geometry, manifest = saved_fixture()
    monkeypatch.setattr(storage, "summarize", lambda *a: pytest.fail("producer summary called"))
    result = save_and_audit(tmp_path, rows, geometry, manifest)
    assert result == rows[-1]["summary"]
    assert result["populations"]["overlap"]["coordinates"] == 1
    assert result["populations"]["union"]["class_dates"]["A"] == 1


def test_streaming_totals_release_each_nested_record_and_preserve_bytes():
    class WeakRecord(dict):
        pass

    source, targets, raw = record_fixture()
    record = WeakRecord(raw)
    del raw
    expected = wire.encode(storage.summarize([record]))
    totals = _StreamingTotals()
    totals.add(record)
    retained = weakref.ref(record)
    del record
    gc.collect()
    assert retained() is None
    assert wire.encode(totals.finish()) == expected


def test_saved_audit_refuses_duplicate_original_day_rank(tmp_path):
    rows, geometry, manifest = saved_fixture()
    second = dict(rows[1], symbol="invented2")
    rows.insert(2, second)
    geometry["union_coordinates"] = geometry["targets_per_scenario"] = 2
    with pytest.raises(InputRefusal, match="duplicate_original_rank"):
        save_and_audit(tmp_path, rows, geometry, manifest)


def test_saved_audit_refuses_rank_beyond_fixed_daily_roster(tmp_path):
    rows, geometry, manifest = saved_fixture()
    rows[1]["targets"][0]["rank"] = 2
    with pytest.raises(InputRefusal, match="daily_rank_outside_roster"):
        save_and_audit(tmp_path, rows, geometry, manifest)


def test_saved_audit_refuses_self_consistent_zero_frame_record(tmp_path):
    rows, geometry, manifest = saved_fixture()
    record = rows[1]
    record["frames"] = record["prefix_observed_frames"] = 0
    for name in ("classes", "phases", "reasons", "first_hour_classes", "first_frames"):
        record[name] = {}
    for window in record["support"].values():
        for name in window:
            window[name] = {} if name.endswith("counts") else 0
    rows = rows[:2] + [{"kind": "footer", "records": 1, "cases": 0, "summary": storage.summarize([record])}]
    with pytest.raises(InputRefusal, match="clock_denominator"):
        save_and_audit(tmp_path, rows, geometry, manifest)


@pytest.mark.parametrize("mutation", ["summary", "chart", "cases", "calendar", "geometry", "suffix"])
def test_saved_audit_rejects_resealed_mutations(tmp_path, mutation):
    rows, geometry, manifest = saved_fixture()
    if mutation == "summary":
        rows[-1]["summary"]["populations"]["union"]["class_dates"]["A"] += 1
    elif mutation == "chart":
        rows[2]["chart_prefix"][15]["prices"][1][0] += 1
    elif mutation == "cases":
        rows[2], rows[3] = rows[3], rows[2]
    elif mutation == "calendar":
        rows[0]["calendar_sha256"] = "b" * 64
    elif mutation == "geometry":
        geometry["union_coordinates"] = 2
    with pytest.raises(ValueError):
        save_and_audit(tmp_path, rows, geometry, manifest, b"\n" if mutation == "suffix" else b"")
