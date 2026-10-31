"""Invented-only ED common snapshot framing, causality and bounded retention."""
from collections import Counter
from collections import OrderedDict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
import copy
import gzip
import hashlib
import io
import json

import pytest

from research import intraday_ed_inputs as inputs
from research import intraday_ed_snapshot as ed
from research import intraday_ed_sources as sources


SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(1, 7))
BINDING = {key: "a" * 64 for key in ed.BINDING_KEYS - {"caller"}} | {"caller": "ED-H1-SNAPSHOT"}


def checkpoint(_phase):
    pass


def test_reference_lru_reuses_only_same_symbol_date_and_exact_window(monkeypatch):
    calls = []
    def source(_reader, symbol, day, *, requested_starts=None):
        calls.append((symbol, day, requested_starts))
        opening = ed.clock.session_open(day)
        return sources.SessionSourceView(inputs.SessionInputs(symbol, opening, opening + 390 * inputs.MINUTE, {}), b"{}")
    monkeypatch.setattr(ed, "_source", source)
    cache, day = OrderedDict(), "2026-01-05"
    a = ed._References(None, SYMBOLS[0], (day,), cache)
    a.for_window(0)
    opening = ed.clock.session_open(day)
    first = a[opening]
    b = ed._References(None, SYMBOLS[0], (day,), cache)
    b.for_window(0)
    assert b[opening] is first and len(calls) == 1
    b.for_window(5)
    assert b[opening] is not first and len(calls) == 2
    assert calls[-1][2] == tuple(opening + (5 + i) * inputs.MINUTE for i in range(30))
    c = ed._References(None, SYMBOLS[1], (day,), cache)
    c.for_window(0)
    assert c[opening] is not first and len(calls) == 3
    for index in range(450):
        prior = (date(2020, 1, 1) + timedelta(days=index)).isoformat()
        record = ed._References(None, SYMBOLS[0], (prior,), cache)
        record.for_window(0)
        record[ed.clock.session_open(prior)]
    assert len(cache) == 440


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def calendar(count=21):
    sessions = tuple((date(2026, 1, 5) + timedelta(days=index)).isoformat() for index in range(count))
    targets = tuple(sources.TargetCoordinate(scenario, symbol, day, rank, 100,
                    "stored_rows_completeness_unverified", b'{"availability":"assumed"}')
                    for scenario in ed.SCENARIOS for day in sessions for rank, symbol in enumerate(SYMBOLS, 1))
    return sources.UniverseCalendar(sessions, targets, "b" * 64)


def filings(universe, *, unknown=()):
    codes = tuple(symbol.removeprefix("KRX:") for symbol in SYMBOLS)
    return sources.FilingSource(codes, tuple(code for code in codes if code not in unknown), tuple(unknown), (),
                                date.fromisoformat(universe.sessions[0]), date.fromisoformat(universe.sessions[-1]),
                                "c" * 64)


class InventedAuditedReader(sources.AuditedChunkReader):
    """No filesystem/source acquisition; admitted trusted boundary is invented."""
    def __init__(self, universe):
        self.universe = universe
        self.receipt = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True,
                        "full_eof_verified": True, "raw_keys": len(universe.sessions) * len(SYMBOLS) * 2,
                        "cache_sha256": "d" * 64, "archives": {}}
        self.calls = Counter()
        self.mutations = {}

    def load_raw_chunk(self, kind, symbol, day):
        self.calls[kind, symbol, day] += 1
        opening = ed.clock.session_open(day)
        last_day = day == self.universe.sessions[-1]
        rows = []
        for index in range(100):
            at = opening + index * inputs.MINUTE
            row = {"open_time_ms": int(at.timestamp()) * 1000, "fetched_at": "2026-10-30T00:00:00Z"}
            if kind == "CC":
                close = ("104" if symbol == SYMBOLS[0] else "102") if last_day and index == 29 else "101" if last_day and index >= 30 else "100"
                row.update(symbol=symbol, interval="1m", open="100", high=max("101", close), low="99", close=close)
            else:
                row.update(volume="20" if last_day else "10", quote_volume=None)
            rows.append(row)
        result = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
                  "retrieved_at": "2026-10-30T00:00:00Z", "source": "collector klines", "is_final": None,
                  "evidence_level": "assumed", "rows": rows}
        if kind == "CX":
            result.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
        mutation = self.mutations.get((kind, symbol, day))
        return result if mutation is None else mutation(result)


def records(universe=None, reader=None, filing_source=None):
    universe = universe or calendar()
    reader = reader or InventedAuditedReader(universe)
    return list(ed.snapshot_records(universe, reader, filing_source or filings(universe), binding=BINDING,
                                   checkpoint=checkpoint))


def last_coordinate(rows, symbol=SYMBOLS[0], scenario="S1"):
    return next(row for row in reversed(rows) if row["kind"] == "coordinate"
                and row["coordinate"]["symbol"] == symbol and row["coordinate"]["scenario"] == scenario)


def encoded(rows):
    sink = io.BytesIO()
    digest = ed.write_snapshot(iter(rows), sink, checkpoint)
    return sink.getvalue(), digest


def repack(rows):
    raw = b"".join(ed.canonical(row) + b"\n" for row in rows)
    return gzip.compress(raw, mtime=0)


def reseal(rows):
    rows[-1]["decoded_prefix_sha256"] = sha(b"".join(ed.canonical(row) + b"\n" for row in rows[:-1]))
    return rows


def test_common_snapshot_preserves_original_calendar_and_exact_typed_roundtrip():
    rows = records()
    data, digest = encoded(rows)
    assert data == encoded(rows)[0]
    assert digest.records == len(rows)
    assert digest.compressed_sha256 == sha(data)
    assert digest.decoded_sha256 == sha(gzip.decompress(data))
    restored = list(ed.read_snapshot(io.BytesIO(data), expected_sha256=sha(data),
                    expected_decoded_sha256=digest.decoded_sha256, checkpoint=checkpoint))
    assert restored == rows
    header, footer = rows[0], rows[-1]
    assert len(header["calendar"]["targets"]) == 21 * 6 * 2
    assert footer["record_counts"] == {"session": 21, "coordinate": 252}
    value = ed.decode_coordinate(last_coordinate(restored))
    assert len(value.features) == 206 and value.common_support
    assert value.window_inputs.past20_return_variance == Fraction()
    assert dict(value.window_inputs.static_exact)["last5_share_volume_ratio"] == Fraction(2)
    assert value.label.status == "known_traded"
    assert value.label.net33 == -Fraction(33, 10000)
    assert value.label.mature_at == value.candidate.cap_exit_at + 3 * inputs.MINUTE
    assert value.actions["rule"].status == "decline"
    assert value.actions["without_filing"].status == "accept"
    day = next(row for row in reversed(rows) if row["kind"] == "session")
    views = ed.decode_session(day)
    assert len(views) == 6 and len(views[0].session.minutes) == 100
    assert json.loads(views[0].metadata)["physical_unit_status"] is None
    assert ed.encode(value) == {key: item for key, item in last_coordinate(rows).items() if key != "kind"}


def test_no_parent_does_not_load_reference_panel_and_both_scenarios_share_target_references(monkeypatch):
    universe = calendar()
    reader = InventedAuditedReader(universe)
    requested = []
    original = sources.session_inputs

    def observed(*args, **kwargs):
        if "requested_starts" in kwargs:
            requested.append((args[0], args[1], kwargs["requested_starts"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(sources, "session_inputs", observed)
    records(universe, reader)
    # Only final-day parents require baselines. Twenty30 windows per target;
    # they are reused by the target's two scenarios, with no full-day refs.
    assert len(requested) == 6 * 20
    assert all(len(starts) == 30 for _, _, starts in requested)
    assert max(reader.calls.values()) == 2  # once daily, once as final baseline


def test_original_unavailable_coordinate_stays_unknown_despite_observed_cache():
    universe = calendar()
    changed = tuple(replace(row, stored_row_count=None, state="no_stored_rows_unknown_reason")
                    if row.session == universe.sessions[-1] and row.symbol == SYMBOLS[0] else row
                    for row in universe.targets)
    universe = replace(universe, targets=changed)
    rows = records(universe)
    value = ed.decode_coordinate(last_coordinate(rows))
    assert value.parent_scan.status == "parent_identity_unknown"
    assert value.parent_scan.issues[0].reason == "original_coordinate_unavailable"
    assert value.candidate is None and value.label is None and not value.common_support
    assert value.actions["rule"].at == ed.clock.session_open(universe.sessions[-1]) + 32 * inputs.MINUTE
    assert value.actions["rule"].status == "unknown"
    peer = ed.decode_coordinate(last_coordinate(rows, SYMBOLS[1]))
    assert SYMBOLS[0] not in peer.window_inputs.common_peers
    assert not peer.common_support  # original remaining support has only4 peers
    assert SYMBOLS[0] in peer.original_peers
    assert len(rows[0]["calendar"]["targets"]) == len(changed)


def test_insufficient_calendar_and_unknown_filing_remain_unknown_without_discarding_parent():
    universe = calendar(1)
    rows = records(universe)
    value = ed.decode_coordinate(last_coordinate(rows))
    assert value.parent_scan.status == "parent" and value.label.status == "known_traded"
    assert value.feature_reason == "insufficient_expected_prior_sessions"
    assert value.window_inputs is None and value.features is None
    assert value.actions["without_filing"].status == "unknown"
    universe = calendar()
    rows = records(universe, filing_source=filings(universe, unknown=("000001",)))
    value = ed.decode_coordinate(last_coordinate(rows))
    assert value.opening_context.context.state == "UNKNOWN"
    assert value.window_inputs.dynamic is not None and value.features is None
    assert value.actions["rule"].status == value.actions["without_filing"].status == "unknown"


def test_future_label_gap_cannot_select_feature_support_or_later_parent():
    universe = calendar()
    before = ed.decode_coordinate(last_coordinate(records(universe)))
    reader = InventedAuditedReader(universe)
    def without_entry(chunk):
        chunk["rows"] = [row for row in chunk["rows"] if row["open_time_ms"] !=
                         int((ed.clock.session_open(universe.sessions[-1]) + 35 * inputs.MINUTE).timestamp()) * 1000]
        return chunk
    reader.mutations["CC", SYMBOLS[0], universe.sessions[-1]] = without_entry
    after = ed.decode_coordinate(last_coordinate(records(universe, reader)))
    assert before.parent_scan == after.parent_scan
    assert before.window_inputs == after.window_inputs and before.features == after.features
    assert after.common_support and after.label.status == "unknown"
    assert after.actions == before.actions


def test_first_unknown_prefix_and_final_eligible_grid_are_preserved():
    universe = calendar(1)
    reader = InventedAuditedReader(universe)
    def late_break(chunk):
        opening = ed.clock.session_open(universe.sessions[0])
        rows = []
        for index in range(310):
            at = opening + index * inputs.MINUTE
            close = "102" if index == 309 else "100"
            rows.append({"symbol": SYMBOLS[0], "interval": "1m", "open_time_ms": int(at.timestamp()) * 1000,
                         "open": "100", "high": "102" if index == 309 else "101", "low": "99", "close": close,
                         "fetched_at": "2026-10-30T00:00:00Z"})
        chunk["rows"] = rows
        return chunk
    reader.mutations["CC", SYMBOLS[0], universe.sessions[0]] = late_break
    late = ed.decode_coordinate(last_coordinate(records(universe, reader)))
    assert late.parent_scan.status == "parent"
    assert late.parent_scan.parent.trigger.end_at == ed.clock.session_close(universe.sessions[0]) - 80 * inputs.MINUTE
    def no_break(chunk):
        chunk = late_break(chunk)
        chunk["rows"][-1].update(high="101", close="100")
        return chunk
    reader.mutations["CC", SYMBOLS[0], universe.sessions[0]] = no_break
    absent = ed.decode_coordinate(last_coordinate(records(universe, reader)))
    assert absent.parent_scan.status == "known_no_parent"
    assert absent.actions["rule"].at == ed.clock.session_close(universe.sessions[0]) - 78 * inputs.MINUTE
    def late_gap(chunk):
        chunk = late_break(chunk)
        chunk["rows"].pop(33)
        return chunk
    reader.mutations["CC", SYMBOLS[0], universe.sessions[0]] = late_gap
    unknown = ed.decode_coordinate(last_coordinate(records(universe, reader)))
    assert unknown.parent_scan.status == "parent_identity_unknown"
    assert unknown.actions["rule"].at == ed.clock.session_open(universe.sessions[0]) + 37 * inputs.MINUTE


@pytest.mark.parametrize("damage", ["compressed_pin", "decoded_pin", "truncate", "extra", "reorder", "fraction", "float"])
def test_strict_reader_rejects_corruption_reordering_and_noncanonical_exact_scalars(damage):
    rows = records(calendar(1))
    if damage == "extra":
        rows.append(copy.deepcopy(rows[-1]))
    elif damage == "reorder":
        rows[2], rows[3] = rows[3], rows[2]
    elif damage == "fraction":
        rows[2]["candidate"]["structural_stop"] = {"fraction": [198, 2]}
        reseal(rows)
    elif damage == "float":
        # This one-session row has no feature vector; put a noncanonical vector
        # in both support fields to exercise the binary64 decoder itself.
        rows[2]["features"] = [{"float64": "nan"}] * 206
        rows[2]["common_support"] = True
        reseal(rows)
    data = repack(rows)
    if damage == "truncate":
        data = data[:-4]
    with pytest.raises((ed.SnapshotRefusal, EOFError, OSError, ValueError)):
        list(ed.read_snapshot(io.BytesIO(data), expected_sha256="0" * 64 if damage == "compressed_pin" else sha(data),
              expected_decoded_sha256="0" * 64 if damage == "decoded_pin" else None, checkpoint=checkpoint))


def test_no_footer_or_unaudited_reader_is_never_accepted():
    rows = records(calendar(1))
    with pytest.raises(ed.SnapshotRefusal, match="footer_missing"):
        encoded(rows[:-1])
    universe = calendar(1)
    reader = InventedAuditedReader(universe)
    reader.receipt["verified"] = False
    with pytest.raises(ed.SnapshotRefusal, match="audited_byte_reader"):
        records(universe, reader)
    assert ed.encode((Decimal("1.00"), Fraction(1, 3), -0.0)) == [
        {"decimal": "1.00"}, {"fraction": [1, 3]}, {"float64": "-0x0.0p+0"}]
