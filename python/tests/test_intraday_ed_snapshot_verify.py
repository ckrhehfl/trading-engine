"""Invented-only exhaustive science checks, mutations and oracle isolation."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
import io
import json

import pytest

from research import intraday_ed_inputs as inputs
from research import intraday_ed_replay as replay
from research import intraday_ed_snapshot as producer
from research import intraday_ed_snapshot_verify as verify
from research import intraday_ed_sources as sources
from research import intraday_pattern_calibration as calendar


SYMBOL = "KRX:000001"
NAMES = tuple(f"KRX:{number:06d}" for number in range(1, 7))
BINDING = {name: "a" * 64 for name in ("registration_sha256", "source_sha256",
                                     "runtime_sha256", "input_manifest_sha256")} | {"caller": "ED-H1-SNAPSHOT"}


def checkpoint(_phase):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class InventedReader(sources.AuditedChunkReader):
    """In-memory stand-in for the already separately tested raw-byte audit."""
    def __init__(self, chunks):
        self.chunks = chunks
        self.receipt = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True,
                        "full_eof_verified": True, "raw_keys": len(chunks), "cache_sha256": "b" * 64}

    def load_raw_chunk(self, kind, symbol, session):
        return self.chunks.get((kind, symbol, session))


def raw_chunk(kind, symbol, day, *, count, parent=False, shares="10"):
    opening = calendar.session_open(day)
    rows = []
    for index in range(count):
        row = {"open_time_ms": int((opening + index * inputs.MINUTE).timestamp()) * 1000,
               "fetched_at": "2026-10-30T00:00:00Z"}
        if kind == "CC":
            close = "102" if parent and index == 29 else "100"
            row.update(symbol=symbol, interval="1m", open="100", high="103" if close == "102" else "101",
                       low="99", close=close)
        else:
            row.update(volume=shares, quote_volume=None)
        rows.append(row)
    chunk = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
             "retrieved_at": "2026-10-30T00:00:00Z", "source": "collector klines", "is_final": None,
             "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        chunk.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return chunk


def example(*, event=False, unknown=False, original_missing=False):
    sessions = tuple((date(2026, 1, 5) + timedelta(days=index)).isoformat() for index in range(22))
    final = sessions[-1]
    targets = tuple(sources.TargetCoordinate(scenario, name, final, index + 1,
        None if original_missing and name == SYMBOL else 120,
        "no_stored_rows_unknown_reason" if original_missing and name == SYMBOL else "stored_rows_completeness_unverified",
        b'{"invented":true}') for scenario in ("S1", "S2") for index, name in enumerate(NAMES))
    universe = sources.UniverseCalendar(sessions, targets, "c" * 64)
    chunks = {}
    for day in sessions:
        for name in NAMES:
            for kind in ("CC", "CX"):
                chunks[kind, name, day] = raw_chunk(kind, name, day,
                    count=120 if day == final else 30, parent=day == final and name == SYMBOL,
                    shares="20" if day == final else "10")
    empty = b'<table><tbody id="tbody"></tbody></table>'
    raw_files, queries, rows = {}, {}, []
    receipt_date = sessions[-2]
    receipt = receipt_date.replace("-", "") + "000001"
    for index, name in enumerate(NAMES, 1):
        raw = empty
        if event and name == SYMBOL:
            title = "사업보고서 (2025.12)"
            raw = (f'<table><tbody id="tbody"><tr><td>1</td><td>회사</td><td>'
                   f'<a id="r_{receipt}" href="/report">{title}</a></td><td>회사</td>'
                   f'<td>{receipt_date.replace("-", ".")}</td><td></td></tr></tbody></table>').encode()
            metadata = {"report_type": "사업보고서", "version_prefixes": [], "title": title,
                        "semantic_state": "plain_original_title_grammar"}
            rows.append(sources.FilingRow(name, receipt, date.fromisoformat(receipt_date),
                        "ORIGINAL_ANNUAL", "plain_original_title_grammar", canonical(metadata)))
        raw_files[f"DART-R{index:03d}"] = raw
        queries[name.removeprefix("KRX:")] = {"raw_sha256": hashlib.sha256(raw).hexdigest()}
    codes = tuple(name.removeprefix("KRX:") for name in NAMES)
    complete = tuple(code for code in codes if not (unknown and code == "000001"))
    filings = sources.FilingSource(codes, complete, ("000001",) if unknown else (), tuple(rows),
        date.fromisoformat(sessions[0]), date.fromisoformat(final), "d" * 64, canonical(queries))
    reader = InventedReader(chunks)
    records = list(producer.snapshot_records(universe, reader, filings, binding=BINDING, checkpoint=checkpoint))
    return records, dict(universe=universe, filing_source=filings, raw_reader=reader,
                         binding=BINDING, checkpoint=checkpoint, raw_filing_files=raw_files)


def coordinate(records, *, scenario="S1", symbol=SYMBOL):
    return next(row for row in records if row["kind"] == "coordinate" and
                row["coordinate"]["symbol"] == symbol and row["coordinate"]["scenario"] == scenario)


def repin_footer(records):
    records[-1]["decoded_prefix_sha256"] = hashlib.sha256(
        b"".join(canonical(row) + b"\n" for row in records[:-1])).hexdigest()


def test_full_serialized_snapshot_is_verified_and_saved_reader_is_exhausted():
    records, kwargs = example(event=True)
    sink = io.BytesIO()
    digest = producer.write_snapshot(iter(records), sink, checkpoint)
    restored = producer.read_snapshot(io.BytesIO(sink.getvalue()), expected_sha256=digest.compressed_sha256,
        expected_decoded_sha256=digest.decoded_sha256, checkpoint=checkpoint)
    result = verify.verify_h1_snapshot(restored, **kwargs)
    assert result["verified"] is True and result["all_original_coordinates_verified"] is True
    assert result["record_counts"] == {"session": 22, "coordinate": 12}
    assert result["science_counts"]["parent"] == 2 and result["science_counts"]["common_support"] == 2
    assert result["sampled_scientific_rows"] is False
    assert coordinate(records)["actions"]["rule"]["status"] == "accept"
    assert coordinate(records, scenario="S2")["actions"]["rule"]["status"] == "decline"


def test_eliding_duplicate_type_objects_preserves_bytes_and_full_raw_verification():
    records, kwargs = example(event=True)
    typed, framed = io.BytesIO(), io.BytesIO()
    a = producer.write_snapshot(iter(records), typed, checkpoint)
    b = producer.write_snapshot(iter(records), framed, checkpoint, validate_types=False)
    assert a == b and typed.getvalue() == framed.getvalue()
    restored = producer.read_snapshot(io.BytesIO(framed.getvalue()), expected_sha256=b.compressed_sha256,
        expected_decoded_sha256=b.decoded_sha256, checkpoint=checkpoint, validate_types=False)
    result = verify.verify_h1_snapshot(restored, **kwargs)
    assert result["verified"] is True and result["sampled_scientific_rows"] is False


def test_framing_and_rehash_alone_never_adopt_a_wrong_exact_scientific_value():
    records, kwargs = example(event=True)
    coordinate(records)["label"]["net33"]["fraction"] = [0, 1]
    repin_footer(records)
    sink = io.BytesIO()
    digest = producer.write_snapshot(iter(records), sink, checkpoint, validate_types=False)
    restored = producer.read_snapshot(io.BytesIO(sink.getvalue()), expected_sha256=digest.compressed_sha256,
        expected_decoded_sha256=digest.decoded_sha256, checkpoint=checkpoint, validate_types=False)
    with pytest.raises(verify.SnapshotVerificationRefusal, match="differ"):
        verify.verify_h1_snapshot(restored, **kwargs)


def test_verification_calls_no_producer_scientific_helper(monkeypatch):
    records, kwargs = example(event=True)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("producer oracle was called")

    for module, names in ((inputs, ("first_h1_parent", "build_h1_window_inputs", "flatten_h1_features", "h1_rule_decision")),
                          (replay, ("candidate_for_parent", "ed_unit_label", "replay_ed_account")),
                          (sources, ("session_inputs", "build_opening_contexts", "filing_event_opening", "report_cell_semantics")),
                          (producer, ("snapshot_records", "_coordinate", "decode_coordinate", "decode_session"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True


@pytest.mark.parametrize("field", ["original_population", "source_price", "parent_close", "structural_stop",
    "dynamic_fraction", "feature_float", "context_type", "context_age", "peer_roster", "prior_calendar",
    "rule", "label_fee", "label_path", "label_maturity", "source_zero"])
def test_rehashed_scientific_mutations_are_rejected(field):
    records, kwargs = example(event=True)
    assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True
    row = coordinate(records)
    if field == "original_population":
        row["coordinate"]["rank"] += 1
    elif field == "source_price":
        day = next(row for row in records if row["kind"] == "session" and row["sources"])
        day["sources"][0]["session"]["minutes"][0]["ohlc"][0]["decimal"] = "98"
    elif field == "parent_close":
        row["parent_scan"]["parent"]["trigger"]["close"]["decimal"] = "103"
    elif field == "structural_stop":
        row["candidate"]["structural_stop"]["fraction"] = [98, 1]
    elif field == "dynamic_fraction":
        row["window_inputs"]["dynamic"][0][0]["fraction"] = [1, 7]
    elif field == "feature_float":
        row["features"][0]["float64"] = float(0.25).hex()
    elif field == "context_type":
        row["opening_context"]["context"]["original_types"] = ["ORIGINAL_QUARTER"]
    elif field == "context_age":
        row["window_inputs"]["event_age_minutes"] += 1
    elif field == "peer_roster":
        row["original_peers"].pop()
    elif field == "prior_calendar":
        row["expected_prior_openings"][0] = row["expected_prior_openings"][1]
    elif field == "rule":
        row["actions"]["rule"]["status"] = "decline"
    elif field == "label_fee":
        row["label"]["net33"]["fraction"] = [0, 1]
    elif field == "label_path":
        row["label"]["path"].pop()
    elif field == "label_maturity":
        row["label"]["mature_at"] = row["candidate"]["entry_at"]
    else:
        row["window_inputs"]["dynamic"][0][4]["fraction"] = [0, 1]
    repin_footer(records)
    with pytest.raises(verify.SnapshotVerificationRefusal, match="differ"):
        verify.verify_h1_snapshot(iter(records), **kwargs)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reorder", "truncated", "tail"])
def test_entire_coordinate_population_and_eof_are_required(mutation):
    records, kwargs = example()
    index = next(index for index, row in enumerate(records) if row["kind"] == "coordinate")
    if mutation == "missing":
        records.pop(index)
    elif mutation == "duplicate":
        records.insert(index, deepcopy(records[index]))
    elif mutation == "reorder":
        records[index], records[index + 1] = records[index + 1], records[index]
    elif mutation == "truncated":
        records.pop()
    else:
        records.append(None)
    with pytest.raises(verify.SnapshotVerificationRefusal):
        verify.verify_h1_snapshot(iter(records), **kwargs)


def test_future_price_and_volume_cannot_change_parent_features_support_or_rules():
    original, kwargs = example(event=True)
    changed = deepcopy(kwargs["raw_reader"].chunks)
    day = kwargs["universe"].sessions[-1]
    for kind in ("CC", "CX"):
        for row in changed[kind, SYMBOL, day]["rows"][35:]:
            if kind == "CC":
                row.update(open="200", high="201", low="198", close="199")
            else:
                row["volume"] = "999999"
    new_reader = InventedReader(changed)
    future = list(producer.snapshot_records(kwargs["universe"], new_reader, kwargs["filing_source"],
        binding=BINDING, checkpoint=checkpoint))
    before, after = coordinate(original), coordinate(future)
    for field in ("parent_scan", "window_inputs", "features", "common_support", "candidate", "actions"):
        assert before[field] == after[field]
    assert before["label"] != after["label"]
    assert verify.verify_h1_snapshot(iter(future), **(kwargs | {"raw_reader": new_reader}))["verified"] is True


def test_known_noevent_and_failed_query_are_distinct_common_support_states():
    noevent, known_kwargs = example()
    unknown, unknown_kwargs = example(unknown=True)
    a, b = coordinate(noevent), coordinate(unknown)
    assert a["common_support"] is True
    assert a["window_inputs"]["event_age_minutes"] is None
    assert a["window_inputs"]["event_age_bits"][0] == {"fraction": [1, 1]}
    assert a["actions"]["rule"]["status"] == "decline"
    assert a["actions"]["without_filing"]["status"] == "accept"
    assert b["common_support"] is False and b["features"] is None
    assert all(action["status"] == "unknown" for action in b["actions"].values())
    for records, kwargs in ((noevent, known_kwargs), (unknown, unknown_kwargs)):
        assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True


def test_original_unavailable_coordinate_cannot_be_rescued_by_raw_cache():
    records, kwargs = example(original_missing=True)
    row = coordinate(records)
    assert row["parent_scan"]["status"] == "parent_identity_unknown"
    assert row["candidate"] is None and row["features"] is None
    assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True


def test_missing_required_prior_session_retains_parent_and_label_without_features():
    _, kwargs = example()
    prior = kwargs["universe"].sessions[5]
    kwargs["raw_reader"].chunks.pop(("CX", SYMBOL, prior))
    records = list(producer.snapshot_records(kwargs["universe"], kwargs["raw_reader"], kwargs["filing_source"],
        binding=BINDING, checkpoint=checkpoint))
    row = coordinate(records)
    assert row["candidate"] is not None and row["label"] is not None
    assert row["common_support"] is False
    assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True


def test_later_unknown_parent_prefix_uses_its_own_completed_grid_clock():
    _, kwargs = example()
    day, symbol = kwargs["universe"].sessions[-1], NAMES[1]
    prices = kwargs["raw_reader"].chunks["CC", symbol, day]["rows"]
    prices.pop(41)
    records = list(producer.snapshot_records(kwargs["universe"], kwargs["raw_reader"], kwargs["filing_source"],
        binding=BINDING, checkpoint=checkpoint))
    row = coordinate(records, symbol=symbol)
    assert row["parent_scan"]["status"] == "parent_identity_unknown"
    assert row["actions"]["rule"]["at"] == (calendar.session_open(day) + 47 * inputs.MINUTE).isoformat()
    assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True


def test_original_missing_peer_is_excluded_for_every_minute_and_window():
    _, kwargs = example()
    universe = kwargs["universe"]
    changed = replace(universe, targets=tuple(replace(row, stored_row_count=None,
        state="no_stored_rows_unknown_reason") if row.symbol == NAMES[-1] else row for row in universe.targets))
    kwargs["universe"] = changed
    records = list(producer.snapshot_records(changed, kwargs["raw_reader"], kwargs["filing_source"],
        binding=BINDING, checkpoint=checkpoint))
    row = coordinate(records)
    assert len(row["window_inputs"]["common_peers"]) == 4
    assert row["features"] is None and row["label"]["status"] == "known_traded"
    assert verify.verify_h1_snapshot(iter(records), **kwargs)["verified"] is True


def test_final_parent_grid_includes_close_minus80_and_excludes_later_breakout():
    day = "2026-01-05"
    raw = raw_chunk("CC", SYMBOL, day, count=390)
    raw["rows"][309].update(high="103", close="102")
    session = verify.reconstruct_session(SYMBOL, day, raw, None).session
    scan = verify.reconstruct_parent(session)
    assert scan.status == "parent"
    assert scan.parent.trigger.end_at == session.closing - 80 * inputs.MINUTE
    raw["rows"][309].update(high="101", close="100")
    raw["rows"][314].update(high="103", close="102")
    session = verify.reconstruct_session(SYMBOL, day, raw, None).session
    assert verify.reconstruct_parent(session).status == "known_no_parent"


def test_label_no_fill_is_zero_policy_outcome_without_a_filled_trade():
    records, kwargs = example()
    row = producer.decode_coordinate(coordinate(records))
    session = verify.reconstruct_session(SYMBOL, row.coordinate.session,
        kwargs["raw_reader"].load_raw_chunk("CC", SYMBOL, row.coordinate.session),
        kwargs["raw_reader"].load_raw_chunk("CX", SYMBOL, row.coordinate.session)).session
    minutes = dict(session.minutes)
    entry = minutes[row.candidate.entry_at]
    minutes[entry.start_at] = replace(entry, ohlc=(Decimal(99), Decimal(100), Decimal(98), Decimal(99)))
    label = verify.reconstruct_label(row.candidate, minutes)
    assert label.status == "known_declined_entry" and label.known_filled_unit is False
    assert label.gross == label.net33 == label.net43 == 0 and not label.path


def test_close_stop_delay_and_low_touch_are_independently_reconstructed():
    records, kwargs = example()
    decoded = producer.decode_coordinate(coordinate(records))
    session = verify.reconstruct_session(SYMBOL, decoded.coordinate.session,
        kwargs["raw_reader"].load_raw_chunk("CC", SYMBOL, decoded.coordinate.session),
        kwargs["raw_reader"].load_raw_chunk("CX", SYMBOL, decoded.coordinate.session)).session
    minutes = dict(session.minutes)
    entry = decoded.candidate.entry_at
    minutes[entry] = replace(minutes[entry], ohlc=(Decimal(100), Decimal(101), Decimal(98), Decimal(100)))
    touched = verify.reconstruct_label(decoded.candidate, minutes)
    assert touched.low_touched_stop is True and touched.exit_at == decoded.candidate.cap_exit_at
    start = entry + 2 * inputs.MINUTE
    minutes[start] = replace(minutes[start], ohlc=(Decimal(100), Decimal(101), Decimal(98), Decimal(99)))
    stopped = verify.reconstruct_label(decoded.candidate, minutes)
    assert stopped.exit_at == start + 4 * inputs.MINUTE
    assert stopped.exit_reason == "delayed_structural_stop"
    assert stopped.net33 == stopped.gross - Fraction(33, 10000)
    assert stopped.net43 == stopped.gross - Fraction(43, 10000)
    minutes.pop(entry + inputs.MINUTE)
    failed = verify.reconstruct_label(decoded.candidate, minutes)
    assert failed.status == "unknown" and failed.first_unknown == (SYMBOL, entry + inputs.MINUTE)


@pytest.mark.parametrize("extra", ["<img alt='정정' src='mark'>", "추가", "<span title='정정'></span>"])
def test_report_cell_annotation_prevents_semantic_admission(extra):
    raw = ("<table><tbody id='tbody'><tr><td>1</td><td>회사</td><td>"
           "<span><a id='r_20260125000001'>사업보고서 (2025.12)</a></span>" + extra +
           "</td><td>회사</td><td>2026.01.25</td><td></td></tr></tbody></table>").encode()
    assert verify.independent_report_semantics(raw) == {"20260125000001": "semantic_unknown_annotation"}


def test_filing_semantic_and_title_tampering_is_rejected_even_with_snapshot_rehash():
    records, kwargs = example(event=True)
    source = kwargs["filing_source"]
    row = source.rows[0]
    metadata = json.loads(row.metadata)
    metadata["report_type"] = "분기보고서"
    changed = replace(source, rows=(replace(row, event_type="ORIGINAL_QUARTER", metadata=canonical(metadata)),))
    records[0]["filing_source"] = producer.encode(changed)
    repin_footer(records)
    with pytest.raises(verify.SnapshotVerificationRefusal, match="filing_row_identity_differs"):
        verify.verify_h1_snapshot(iter(records), **(kwargs | {"filing_source": changed}))
