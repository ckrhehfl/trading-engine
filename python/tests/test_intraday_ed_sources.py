"""Invented-only byte-cache/source/filing-context contract checks."""
from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import sqlite3

import pytest

from research import intraday_ed_sources as ed


DAY = "2026-01-05"
SYMBOL = "KRX:000001"


def checkpoint(_phase):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def chunk(kind, *, day=DAY, symbol=SYMBOL, volume="0", rows=True):
    at = ed.calendar_source.session_open(day)
    row = {"open_time_ms": int(at.timestamp()) * 1000, "fetched_at": "2026-10-30T00:00:00Z"}
    row.update({"symbol": symbol, "interval": "1m", "open": "100", "high": "101", "low": "99", "close": "100"}
               if kind == "CC" else {"volume": volume, "quote_volume": None})
    value = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
             "retrieved_at": "2026-10-30T00:00:00Z", "source": "collector klines", "is_final": None,
             "evidence_level": "assumed", "rows": [row] if rows else []}
    if kind == "CX":
        value.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return value


def archive(tmp_path, kind, values, *, raw=None):
    raw = raw if raw is not None else b"".join(canonical(value) + b"\n" for value in values)
    compressed = gzip.compress(raw, mtime=0)
    path = tmp_path / (kind + ".gz")
    path.write_bytes(compressed)
    pin = ed.RawArchivePin(kind, sha(compressed), sha(raw), len(values), sum(len(v["rows"]) for v in values),
                           sum(len(v.get("daily_rows", [])) for v in values))
    return path, pin


def archives(tmp_path):
    return {kind: archive(tmp_path, kind, [chunk(kind)]) for kind in ("CC", "CX")}


def test_received_roster_authenticates_local_names_without_opening_foreign_provenance(tmp_path):
    received = tmp_path / "received"
    received.mkdir(mode=0o700)
    metadata = []
    for name, maximum in ed.wire.ED_H1_INPUT_BOUNDS:
        raw = name.encode()
        (received / name).write_bytes(raw)
        metadata.append({"name": name, "original_path": "/nonexistent-foreign-host/" + name,
                         "sha256": sha(raw), "maximum": maximum, "size": len(raw)})
    got = ed.received_inputs(tmp_path, metadata, checkpoint)
    assert len(got) == 42 and got["CC"] == received / "CC"
    (received / "CC").write_bytes(b"altered")
    with pytest.raises(ed.SourceRefusal, match="received_input_pin_differs"):
        ed.received_inputs(tmp_path, metadata, checkpoint)


def test_full_eof_digest_and_geometry_are_checked_without_replacing_actual_pins(tmp_path):
    path, pin = archive(tmp_path, "CC", [chunk("CC")])
    assert list(ed.canonical_chunks(path, pin, checkpoint))[0].raw.endswith(b"\n")
    for bad in (replace(pin, decoded_sha256="0" * 64), replace(pin, expected_rows=2),
                replace(pin, expected_chunks=2), replace(pin, compressed_sha256="0" * 64)):
        with pytest.raises(ed.SourceRefusal):
            list(ed.canonical_chunks(path, bad, checkpoint))


def test_requested_minutes_preserve_exact_states_and_whole_source_provenance():
    price, volume = chunk("CC"), chunk("CX")
    opening = ed.calendar_source.session_open(DAY)
    later = opening + timedelta(minutes=1)
    for value in (price, volume):
        value["rows"].append(dict(value["rows"][0], open_time_ms=int(later.timestamp()) * 1000))
    # Collision survives projection; it must not collapse into an observed row.
    volume["rows"].append(dict(volume["rows"][0]))
    full = ed.session_inputs(SYMBOL, DAY, price, volume)
    projected = ed.session_inputs(SYMBOL, DAY, price, volume, requested_starts=(opening,))
    assert projected.session.minutes == {opening: full.session.minutes[opening]}
    assert projected.session.minutes[opening].volume_state == "conflict"
    a, b = json.loads(full.metadata), json.loads(projected.metadata)
    assert a["sources"] == b["sources"]
    assert b["requested_minute_starts"] == [opening.isoformat()]
    assert "requested_minute_starts" not in a
    empty = ed.session_inputs(SYMBOL, DAY, price, volume, requested_starts=())
    assert empty.session.minutes == {}
    for bad in ((later, opening), (opening, opening), (opening - timedelta(minutes=1),)):
        with pytest.raises(ed.SourceRefusal, match="invalid_requested_minute_projection"):
            ed.session_inputs(SYMBOL, DAY, price, volume, requested_starts=bad)


@pytest.mark.parametrize("mutation", ["framing", "trailing", "nan"])
def test_noncanonical_or_truncated_raw_stream_refuses(tmp_path, mutation):
    value = chunk("CC")
    raw = json.dumps(value).encode() + b"\n" if mutation == "framing" else canonical(value)
    if mutation == "nan":
        raw = canonical(value).replace(b'"open":"100"', b'"open":NaN') + b"\n"
    path, pin = archive(tmp_path, "CC", [value], raw=raw)
    with pytest.raises((ValueError, ed.SourceRefusal)):
        list(ed.canonical_chunks(path, pin, checkpoint))


def test_gzip_footer_corruption_cannot_be_accepted_by_a_prefix(tmp_path):
    path, pin = archive(tmp_path, "CC", [chunk("CC")])
    corrupted = path.read_bytes()[:-5]
    path.write_bytes(corrupted)
    pin = replace(pin, compressed_sha256=sha(corrupted))
    with pytest.raises((EOFError, OSError, ed.SourceRefusal)):
        list(ed.canonical_chunks(path, pin, checkpoint))


def test_index_roundtrip_preserves_whole_canonical_blobs_and_missing_is_not_zero(tmp_path):
    source = archives(tmp_path)
    path = tmp_path / "raw-byte-chunks.sqlite"
    receipt = ed.ByteChunkIndex.build(path, source, checkpoint)
    assert path.stat().st_mode & 0o777 == 0o400
    with ed.audit_index(path, source, checkpoint, expected_cache_sha256=receipt["sha256"]) as reader:
        assert reader.receipt["raw_keys"] == 2
        assert reader.load_raw_chunk("CX", SYMBOL, DAY)["rows"][0]["volume"] == "0"
        assert reader.load_raw_chunk("CX", "KRX:000099", DAY) is None
        assert reader.load_raw_chunk("CC", SYMBOL, DAY) == chunk("CC")
    with pytest.raises(FileExistsError):
        ed.ByteChunkIndex.build(path, source, checkpoint)


def test_index_duplicate_keys_refuse_and_failed_cache_remains(tmp_path):
    source = archives(tmp_path)
    source["CC"] = archive(tmp_path, "CC", [chunk("CC"), chunk("CC")])
    path = tmp_path / "raw-byte-chunks.sqlite"
    with pytest.raises(ed.SourceRefusal, match="duplicate_raw_chunk_key"):
        ed.ByteChunkIndex.build(path, source, checkpoint)
    assert path.exists()


def test_rehashed_cache_corruption_still_fails_independent_raw_blob_audit(tmp_path):
    source = archives(tmp_path)
    path = tmp_path / "raw-byte-chunks.sqlite"
    ed.ByteChunkIndex.build(path, source, checkpoint)
    path.chmod(0o600)
    altered = canonical(chunk("CX", volume="999")) + b"\n"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE chunks SET raw=?,sha=? WHERE kind='CX'", (altered, sha(altered)))
    path.chmod(0o400)
    with pytest.raises(ed.SourceRefusal, match="raw_byte_cache_differs"):
        ed.audit_index(path, source, checkpoint, expected_cache_sha256=sha(path.read_bytes()))


def test_audited_cache_mutation_refuses_on_later_load(tmp_path):
    source = archives(tmp_path)
    path = tmp_path / "raw-byte-chunks.sqlite"
    receipt = ed.ByteChunkIndex.build(path, source, checkpoint)
    with ed.audit_index(path, source, checkpoint, expected_cache_sha256=receipt["sha256"]) as reader:
        path.chmod(0o600)
        with pytest.raises(ed.SourceRefusal, match="audited_cache_changed"):
            reader.load_raw_chunk("CC", SYMBOL, DAY)


def test_cache_symlink_and_worktree_and_nonprivate_parent_are_rejected(tmp_path):
    source = archives(tmp_path)
    other = tmp_path / "actual"
    other.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(other, target_is_directory=True)
    with pytest.raises((ValueError, ed.SourceRefusal)):
        ed.ByteChunkIndex.build(linked / "raw-byte-chunks.sqlite", source, checkpoint)
    (other / ".git").write_text("not a real checkout")
    with pytest.raises((ValueError, ed.SourceRefusal)):
        ed.ByteChunkIndex.build(other / "raw-byte-chunks.sqlite", source, checkpoint)
    tmp_path.chmod(0o755)
    with pytest.raises(ed.SourceRefusal, match="private_native_cache_parent_required"):
        ed.ByteChunkIndex.build(tmp_path / "raw-byte-chunks.sqlite", source, checkpoint)
    tmp_path.chmod(0o700)


def test_stored_zeros_invalid_and_missing_are_distinct_and_units_are_not_imputed():
    view = ed.session_inputs(SYMBOL, DAY, chunk("CC"), chunk("CX"))
    minute = next(iter(view.session.minutes.values()))
    assert minute.shares == Decimal(0) and minute.volume_state == "observed"
    assert minute.price_state == "observed" and minute.ohlc[3] == Decimal(100)
    assert minute.available_at == minute.start_at + timedelta(minutes=3)
    assert json.loads(view.metadata)["physical_unit_status"] is None
    missing = ed.session_inputs(SYMBOL, DAY, chunk("CC"), None)
    assert next(iter(missing.session.minutes.values())).shares is None
    assert next(iter(missing.session.minutes.values())).volume_state == "missing"
    invalid = ed.session_inputs(SYMBOL, DAY, chunk("CC"), chunk("CX", volume="not-number"))
    assert json.loads(invalid.metadata)["minute_quality"][0]["volume"] == "raw_invalid_numeric"
    conflict = chunk("CX")
    conflict["rows"] *= 2
    view = ed.session_inputs(SYMBOL, DAY, chunk("CC"), conflict)
    assert json.loads(view.metadata)["minute_quality"][0]["volume"] == "conflict"
    assert not ed.session_inputs(SYMBOL, DAY, None, None).session.minutes


def html(*, receipt="20260103000001", extra="", wrapper=False, anchor_extra="", remarks=""):
    title = "사업보고서 (2025.12)"
    anchor = (f'<a id="r_{receipt}" href="/dsaf001/main.do?rcpNo={receipt}" '
              f'onclick="openReportViewer(\'{receipt}\',\'\'); return false;" {anchor_extra}>{title}</a>')
    if wrapper:
        anchor = "<span>" + anchor + "</span>"
    text = ("<div class=\"pageInfo\">[1/1] [총 1건]</div><table><tbody id=\"tbody\"><tr><td>1</td>"
            "<td><a onclick=\"openCorpInfoNew('00000001', 'winCorpInfo', '/dsae001/selectPopup.ax');\">회사</a></td>"
            f"<td>{anchor}{extra}</td><td>회사</td><td>{receipt[:4]}.{receipt[4:6]}.{receipt[6:8]}</td><td>{remarks}</td>"
            "</tr></tbody></table>")
    return text.encode()


@pytest.mark.parametrize("extra", ["[정정]", '<img alt="정정"/>', '<span title="철회"></span>', '<b>표시</b>'])
def test_unadjudicated_report_cell_annotation_is_unknown(extra):
    assert ed.report_cell_semantics(html(extra=extra))["20260103000001"] == "semantic_unknown_annotation"


def test_whitespace_plain_wrapper_and_future_correction_remarks_do_not_delete_original():
    assert ed.report_cell_semantics(html(extra=" \n\t", wrapper=True, remarks="나중 정정됨")) == {
        "20260103000001": "plain_original_title_grammar"}
    assert ed.report_cell_semantics(html(anchor_extra='title="철회"'))["20260103000001"] == "semantic_unknown_annotation"


def invented_filing_bundle():
    codes = tuple(f"{index:06d}" for index in range(1, 19))
    raw_files, origin, listings = {}, {"artifacts": {}}, []
    for index, code in enumerate(codes, 1):
        raw = html(receipt=f"20260103{index:06d}") if index <= 14 else b"<p>unsupported empty source</p>"
        metadata = {"received_at": "2026-10-31T00:00:00Z", "status": 200, "url": ed.dart.SOURCE,
                    "bytes": len(raw), "received_bytes": len(raw), "sha256": sha(raw)}
        raw_files[f"DART-R{index:03d}"] = raw
        raw_files[f"DART-M{index:03d}"] = canonical(metadata)
        for kind, suffix in (("R", "-raw.html"), ("M", "-response.json")):
            origin["artifacts"][f"attempt-{index:03d}" + suffix] = sha(raw_files[f"DART-{kind}{index:03d}"])
        rows = []
        if index <= 14:
            page = ed.dart.parse_listing_page(raw, ed.dart.ListingRequest(code, ed.dart.WINDOW_START, ed.dart.WINDOW_END, 1))
            rows = [asdict(row) | {"observation_date": row.observation_date.isoformat()} for row in page.rows]
        listings.append({"query_code": code, "state": "complete" if index <= 14 else "unknown", "rows": rows})
    summary = {"wire_attempts": 18, "complete_codes": 14, "unknown_codes": 4, "complete_listing_rows": 14}
    report = {"schema": "ed-disclosure-acquisition-v1", "codes": list(codes), "source": ed.dart.SOURCE,
              "start_date": ed.dart.WINDOW_START.isoformat(), "end_date": ed.dart.WINDOW_END.isoformat(),
              "state": "complete", "failure": None, "attempts": [f"attempt-{i:03d}" for i in range(1, 19)],
              "listings": listings, "summary": summary}
    raw_files["DART-REPORT"] = canonical(report)
    origin["artifacts"]["report.json"] = sha(raw_files["DART-REPORT"])
    raw_files["DART-ORIGIN-MANIFEST"] = canonical(origin)
    publication = {"status": "completed", "verification_complete": True, "automatic_retries": 0,
                   "artifacts": {"origin-manifest.json": sha(raw_files["DART-ORIGIN-MANIFEST"])},
                   "verified_support": {"schema": report["schema"], "verified": True, "state": "complete", **summary}}
    raw_files["DART-PUBLICATION"] = canonical(publication)
    return codes, raw_files, {name: sha(raw) for name, raw in raw_files.items()}


def test_saved_repair_chain_and_unknown_four_are_preserved():
    codes, raw, pins = invented_filing_bundle()
    source = ed.load_filing_source(raw, pins, expected_query_codes=codes, expected_rows=14)
    assert source.unknown_codes == codes[14:] and len(source.rows) == 14
    assert all(row.event_type == "ORIGINAL_ANNUAL" for row in source.rows)
    assert all(json.loads(row.metadata)["source_event_at"] is None for row in source.rows)
    damaged = dict(raw)
    damaged["DART-R001"] += b" "
    with pytest.raises(ed.SourceRefusal, match="filing_external_pin_differs"):
        ed.load_filing_source(damaged, pins, expected_query_codes=codes, expected_rows=14)
    pins2 = dict(pins, **{"DART-R001": sha(damaged["DART-R001"])})
    with pytest.raises(ed.SourceRefusal, match="filing_origin_attempt_pin_differs"):
        ed.load_filing_source(damaged, pins2, expected_query_codes=codes, expected_rows=14)


def universe(sessions=("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"), *, drop_first=False):
    targets = tuple(ed.TargetCoordinate(scenario, symbol, day, rank, None, "no_stored_rows_unknown_reason", b"{}")
                    for scenario in ("S1", "S2") for day in sessions for rank, symbol in enumerate((SYMBOL, "KRX:000015"), 1)
                    if not (drop_first and day == "2026-01-05" and symbol == SYMBOL))
    return ed.UniverseCalendar(sessions, targets, "a" * 64)


def test_weekend_date_lag_boundary_unknown_four_and_no_postponement():
    codes, raw, pins = invented_filing_bundle()
    source = ed.load_filing_source(raw, pins, expected_query_codes=codes, expected_rows=14)
    by1 = {(r.context.symbol, r.context.opening.date().isoformat()): r for r in ed.build_opening_contexts(universe(), source, "S1")}
    by2 = {(r.context.symbol, r.context.opening.date().isoformat()): r for r in ed.build_opening_contexts(universe(), source, "S2")}
    assert by1[SYMBOL, "2026-01-02"].context.state == "UNKNOWN"
    assert by1[SYMBOL, "2026-01-05"].context.state == "ELIGIBLE_ORIGINAL"
    assert by1[SYMBOL, "2026-01-06"].context.state == "KNOWN_NO_WHITELISTED_LIST_EVENT"
    assert by2[SYMBOL, "2026-01-05"].context.state == "UNKNOWN"
    assert by2[SYMBOL, "2026-01-06"].context.state == "ELIGIBLE_ORIGINAL"
    assert all(row.context.state == "UNKNOWN" for key, row in by1.items() if key[0] == "KRX:000015")
    records = ed.build_opening_contexts(universe(drop_first=True), source, "S1")
    assert not any(row.context.state == "ELIGIBLE_ORIGINAL" and row.context.symbol == SYMBOL for row in records)
    assert next(row for row in records if row.context.symbol == SYMBOL and row.context.opening.date().isoformat() == "2026-01-06").context.state == "KNOWN_NO_WHITELISTED_LIST_EVENT"


def test_semantic_unknown_and_multiple_originals_survive_opening_freeze():
    row = ed.FilingRow(SYMBOL, "20260103000001", date(2026, 1, 3), "ORIGINAL_ANNUAL", "plain_original_title_grammar", b"{}")
    second = replace(row, receipt="20260103000002", event_type="ORIGINAL_QUARTER")
    source = ed.FilingSource(("000001",), ("000001",), (), (row, second), date(2025, 9, 4), date(2026, 9, 23), "a" * 64)
    found = next(r.context for r in ed.build_opening_contexts(universe(), source, "S1")
                 if r.context.symbol == SYMBOL and r.context.opening.date().isoformat() == "2026-01-05")
    assert found.original_types == ("ORIGINAL_ANNUAL", "ORIGINAL_QUARTER")
    assert found.receipt_ids == (row.receipt, second.receipt)
    assert found.available_at <= found.opening == found.event_opening
    unknown = replace(second, event_type=None, semantic_state="semantic_unknown_annotation")
    modified = replace(source, rows=(row, unknown))
    found2 = next(r.context for r in ed.build_opening_contexts(universe(), modified, "S1")
                  if r.context.symbol == SYMBOL and r.context.opening.date().isoformat() == "2026-01-05")
    assert found2.state == "UNKNOWN"


def test_future_filing_changes_do_not_rewrite_earlier_opening_and_outside_window_is_retained():
    row = ed.FilingRow(SYMBOL, "20260103000001", date(2026, 1, 3), "ORIGINAL_ANNUAL", "plain_original_title_grammar", b"{}")
    source = ed.FilingSource(("000001",), ("000001",), (), (row,), date(2025, 9, 4), date(2026, 9, 23), "a" * 64)
    future = replace(row, receipt="20260107000002", observation_date=date(2026, 1, 7),
                     semantic_state="semantic_unknown_annotation", event_type=None)
    before = ed.build_opening_contexts(universe(), source, "S1")
    after = ed.build_opening_contexts(universe(), replace(source, rows=(row, future)), "S1")
    assert before == after
    assert ed.filing_event_opening(universe(), future, "S1") is None


def test_original_twenty_targets_both_variants_and_missing_coordinates_preserved():
    days = ("2026-01-02", "2026-01-05", "2026-01-06")
    variants = {}
    for name in ("D+1", "D+2"):
        variants[name] = {"days": [{"session": day, "available_at": day + "T08:30:00+09:00",
            "selected_at": day + "T08:30:00+09:00", "targets": [
                {"symbol": f"KRX:{rank:06d}", "rank": rank, "stored_row_count": None,
                 "state": "no_stored_rows_unknown_reason"} for rank in range(1, 21)]} for day in days]}
    upstream = {"study_id": "intraday-universe-sources-v1", "calendar": {"rows": [{"date": day} for day in days]},
                "variants": variants}
    upstream.update({key: False for key in ("historical_eligibility_certified", "liquidity_certified",
                    "historical_revision_status_certified", "calendar_completeness_certified",
                    "historical_api_availability_proven", "performance_certified", "promotion_allowed")})
    raw = canonical(upstream)
    got = ed.load_universe(raw, sha(raw), expected_sessions=3)
    assert got.sessions == days and len(got.targets) == 120
    assert len(got.coordinates("S1")) == 60 and all(item.stored_row_count is None for item in got.targets)
    with pytest.raises(ed.SourceRefusal, match="original_session_population_differs"):
        ed.load_universe(raw, sha(raw))
