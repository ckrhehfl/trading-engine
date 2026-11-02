"""Tiny invented memory-only EE report and saved-mathematics checks."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
from io import BytesIO

import pytest

from research import intraday_ed_sources as sources
from research import intraday_trader_report as report
from research import intraday_trader_reference as reference
from research import intraday_trader_inputs as original


DAY, LATER, SYMBOL = "2026-06-03", "2026-06-10", "KRX:000001"
BINDING = {key: "a" * 64 for key in report.BINDING_KEYS - {"caller"}} | {"caller": "invented-ee"}


def calendar(days=(DAY,)):
    targets = tuple(sources.TargetCoordinate(clock, SYMBOL, day, 1, None,
        "no_stored_rows_unknown_reason", report.codec.canonical({"clock": clock, "day": day}))
        for clock in ("S1", "S2") for day in days)
    return sources.UniverseCalendar(days, targets, "b" * 64)


def geometry(universe):
    count = len(universe.sessions)
    return dict(sessions=count, targets_per_scenario=count, union_coordinates=count,
                cc_chunks=count, cc_rows=300, cx_chunks=count, cx_rows=0, cx_daily_rows=0)


def blocks(family="A"):
    if family == "B":
        return [(105, 110, 100, 105)] * 6 + [(105, 112, 90, 95), (95, 98, 92, 97),
            (97, 99, 93, 98), (98, 101, 94, 100), (100, 103, 95, 102), (102, 106, 96, 105)]
    return [(100, 105, 95, close) for close in (100, 100, 101, 101, 102, 103)] + [
        (108, 120, 107, 112), (112, 122, 111, 115), (115, 117, 108, 111),
        (111, 114, 109, 113), (113, 116, 110, 115)]


def chunk(family="A", skip=()):
    opening, values = sources.calendar_source.session_open(DAY), blocks(family)
    rows = []
    for i in range(300):
        if i in skip:
            continue
        filler = (105, 106, 104, 105) if family == "B" else (110, 111, 109, 110)
        prices = values[i // 5] if i // 5 < len(values) else filler
        rows.append(dict(symbol=SYMBOL, interval="1m", open_time_ms=int(
            (opening + timedelta(minutes=i)).timestamp()) * 1000,
            fetched_at="2026-10-30T00:00:00Z", **dict(zip(("open", "high", "low", "close"), map(str, prices)))))
    return dict(symbol=SYMBOL, session=DAY, observation_date=DAY, available_at=None,
        retrieved_at="2026-10-30T00:00:00Z", source="collector klines", is_final=None,
        evidence_level="assumed", rows=rows)


class Reader:
    def __init__(self, price=None, fail=None):
        self.price, self.fail, self.calls, self.checks = price, fail, [], 0

    def assert_unchanged(self):
        self.checks += 1
        if self.checks == self.fail:
            raise sources.SourceRefusal("invented_identity_changed")

    def load_raw_chunk(self, kind, symbol, day):
        self.calls.append((kind, symbol, day))
        return self.price if kind == "CC" and day == DAY else None


def records(universe=None, price=None):
    universe = calendar() if universe is None else universe
    return list(report.report_records(universe, Reader(price), binding=BINDING,
        geometry=geometry(universe), invented_only=True, checkpoint=lambda _: None))


def verify(rows, universe=None, price=None, **changes):
    universe = calendar() if universe is None else universe
    options = dict(universe=universe, raw_reader=Reader(price), binding=BINDING,
        geometry=geometry(universe), invented_only=True, checkpoint=lambda _: None)
    return report.verify_report(iter(rows), **(options | changes))


def packed(raw):
    sink = BytesIO()
    with gzip.GzipFile(filename="", fileobj=sink, mode="wb", mtime=0, compresslevel=1) as stream:
        stream.write(raw)
    return sink.getvalue()


def read(raw, decoded=None, **changes):
    decoded = gzip.decompress(raw) if decoded is None else decoded
    arguments = dict(expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_decoded_sha256=hashlib.sha256(decoded).hexdigest(), checkpoint=lambda _: None)
    return list(report.read_report(BytesIO(raw), **(arguments | changes)))


def reseal(rows):
    summary = report._Summary(tuple(rows[0]["calendar"]["sessions"]))
    for row in rows[1:-1]:
        summary.add(report._decode_record(row))
    rows[-1]["summary"] = summary.finish()
    rows[-1]["decoded_prefix_sha256"] = hashlib.sha256(b"".join(
        report.codec.canonical(row) + b"\n" for row in rows[:-1])).hexdigest()
    return rows


@pytest.mark.parametrize("family,policy", [("A", "EE-A"), ("B", "EE-B")])
def test_canonical_roundtrip_and_independent_counts(family, policy):
    universe, price, sink = calendar((DAY, LATER)), chunk(family), BytesIO()
    rows = records(universe, price)
    digest = report.write_report(iter(rows), sink, lambda _: None)
    restored = read(sink.getvalue())
    assert restored == rows and digest.records == 4
    assert digest.compressed_bytes == len(sink.getvalue())
    assert digest.decoded_bytes == len(gzip.decompress(sink.getvalue()))
    assert digest.compressed_sha256 == hashlib.sha256(sink.getvalue()).hexdigest()
    reader = Reader(price)
    receipt = verify(report.read_report(BytesIO(sink.getvalue()), digest.compressed_sha256,
        digest.decoded_sha256, lambda _: None), universe, price, raw_reader=reader)
    assert receipt["verified"] and receipt["record_stream_exhausted"]
    assert receipt["candidate_counts"]["S1"][policy] == 1
    summary = receipt["summary"]["S1"][policy]
    assert summary["original_targets"] == 2
    assert summary["detection_states"]["unknown"] == 1
    assert summary["weekly_entry_counts"] == {"2026-W23": 1, "2026-W24": 0}
    assert summary["returns"]["gross"]["count"] == 1
    assert reader.calls == [(kind, SYMBOL, day) for day in (DAY, LATER) for kind in ("CC", "CX")]
    typed = report._decode_record(restored[1])
    result = next(item for item in typed.result.results if item.scenario_id == policy)
    assert type(result.path.gross) is Fraction
    assert typed.targets == tuple(t for t in universe.targets if t.session == DAY)
    assert typed.result.source_metadata == sources.session_inputs(SYMBOL, DAY, price, None).metadata


def test_unknown_never_becomes_zero_return_and_decimal_context_is_local():
    with localcontext() as context:
        context.prec = 3
        summary = verify(records())["summary"]["S1"]["EE-A"]
        assert context.prec == 3
    assert summary["returns"]["gross"] == {"count": 0, "sum": None, "mean": None}
    assert summary["path_states"] == {"none": 1, "traded": 0, "declined": 0, "unknown": 0, "refusal": 0}
    price = chunk()
    rows = records(price=price)
    expected = verify(rows, price=price)["summary"]
    with localcontext() as context:
        context.prec = 3
        assert verify(rows, price=price)["summary"] == expected
        assert context.prec == 3


@pytest.mark.parametrize("skip,state,path", [((10,), "unknown", "none"),
    ((70,), "candidate", "unknown"), ((200,), "candidate", "traded")])
def test_missing_demanded_and_unused_source_suffix(skip, state, path):
    price = chunk(skip=skip)
    row = verify(records(price=price), price=price)["summary"]["S1"]["EE-A"]
    assert row["detection_states"][state] == 1 and row["path_states"][path] == 1
    assert row["returns"]["gross"]["count"] == (path == "traded")


def test_late_bad_source_is_disclosed_without_rewriting_a_completed_path():
    price = chunk()
    price["rows"][200]["close"] = "invalid"
    rows = records(price=price)
    receipt = verify(rows, price=price)
    assert receipt["summary"]["S1"]["EE-A"]["path_states"]["traded"] == 1
    assert rows[1]["payload"]["result"]["source_metadata"]["minute_quality"][200]["price"] == "raw_invalid_numeric_or_ohlc"


def test_saved_verifier_never_calls_producer_oracles(monkeypatch):
    price = chunk()
    rows = records(price=price)
    def forbidden(*args, **kwargs):
        raise AssertionError("producer oracle called by saved verifier")
    monkeypatch.setattr(original, "iter_original_sessions", forbidden)
    monkeypatch.setattr(original.producer, "evaluate_session", forbidden)
    monkeypatch.setattr(original.producer.scenarios_core, "detect_bars", forbidden)
    monkeypatch.setattr(original.producer.paths, "evaluate_path", forbidden)
    assert verify(rows, price=price)["verified"]


def test_declined_context_and_path_refusal_keep_separate_non_numeric_states(monkeypatch):
    price = chunk()
    for row in price["rows"][:30]:
        row["close"] = "100"
    summary = verify(records(price=price), price=price)["summary"]["S1"]
    assert summary["EE-A"]["eligible"]["false"] == 1
    assert summary["EE-A"]["path_states"]["declined"] == 1
    assert summary["EE-A"]["returns"]["gross"]["mean"] is None
    assert summary["EE-A-WITHOUT-15M-CONTEXT"]["eligible"]["true"] == 1
    production, independent = sources.session_inputs, sources.full_reference_session_inputs
    def malformed(session):
        rows = dict(session.minutes)
        at = session.opening + timedelta(minutes=60)
        rows[at] = replace(rows[at], available_at=at + timedelta(minutes=4))
        return replace(session, minutes=rows)
    def adapted(*args, **kwargs):
        view = production(*args, **kwargs)
        return replace(view, session=malformed(view.session))
    monkeypatch.setattr(sources, "session_inputs", adapted)
    monkeypatch.setattr(sources, "full_reference_session_inputs", lambda *args: malformed(independent(*args)))
    price = chunk()
    summary = verify(records(price=price), price=price)["summary"]["S1"]["EE-A"]
    assert summary["path_states"]["refusal"] == 1
    assert summary["path_reasons"] == {"held_minute_unavailable_at_decision": 1}
    assert summary["returns"]["gross"] == {"count": 0, "sum": None, "mean": None}
    assert summary["weekly_entry_counts"] == {"2026-W23": 0}
    assert summary["weekly_entry_attempt_counts"] == {"2026-W23": 0}
    assert summary["weekly_entry_refusal_counts"] == {"2026-W23": 1}


@pytest.mark.parametrize("case,state,reason,confirmed,unknown", [
    ("outside", "declined", "entry_outside_frozen_levels", 0, 0),
    ("cost", "declined", "entry_cost_space_not_strict", 0, 0),
    ("missing_open", "unknown", "missing_open_minute", 0, 1),
    ("missing_held", "unknown", "missing_held_minute", 1, 0),
    ("missing_exit", "unknown", "missing_open_minute", 1, 0),
])
def test_weekly_confirmed_entry_excludes_declined_gates_and_unknown_open(
        case, state, reason, confirmed, unknown):
    price = chunk()
    path = report._decode_record(records(price=price)[1]).result.results[0].path
    opening = sources.calendar_source.session_open(DAY)
    entry = int((path.entry_at - opening) / timedelta(minutes=1))
    exit_index = int((path.exit_at - opening) / timedelta(minutes=1))
    if case in ("outside", "cost"):
        value = path.plan.target if case == "outside" else path.plan.target - Decimal("0.1")
        price["rows"][entry]["open"] = str(value)
        price["rows"][entry]["high"] = str(path.plan.target)
    else:
        missing = {"missing_open": entry, "missing_held": entry + 1,
                   "missing_exit": exit_index}[case]
        del price["rows"][missing]
    summary = verify(records(price=price), price=price)["summary"]["S1"]["EE-A"]
    assert summary["path_states"][state] == 1
    assert summary["path_reasons"] == {reason: 1}
    assert summary["weekly_entry_counts"] == {"2026-W23": confirmed}
    assert summary["weekly_entry_attempt_counts"] == {"2026-W23": 1}
    assert summary["weekly_entry_unknown_counts"] == {"2026-W23": unknown}
    assert summary["weekly_entry_refusal_counts"] == {"2026-W23": 0}
    assert summary["returns"]["gross"]["mean"] is None


@pytest.mark.parametrize("field", ["net33", "signal", "clock", "metadata"])
def test_resealed_scientific_mutations_refuse_independently(field):
    price = chunk()
    rows = records(price=price)
    result = rows[1]["payload"]["result"]
    policy = result["results"][0]
    if field == "net33":
        policy["path"]["net33"] = {"fraction": [0, 1]}
    elif field == "signal":
        policy["detection"]["candidate"]["key_level"] = {"decimal": "1000"}
    elif field == "clock":
        policy["path"]["exit_at"] = "2026-06-03T12:00:00+09:00"
    else:
        result["source_metadata"]["price_basis"] = "fabricated"
    reseal(rows)
    with pytest.raises(reference.ReferenceRefusal):
        verify(rows, price=price)


@pytest.mark.parametrize("mutation", ["target", "footer", "count_type", "binding", "invented", "drop", "extra", "policy", "receipt"])
def test_closed_population_framing_and_binding_mutations(mutation):
    rows = records()
    if mutation == "target":
        rows[1]["payload"]["targets"][0]["rank"] = 2
    elif mutation == "footer":
        rows[-1]["summary"]["S1"]["EE-A"]["original_targets"] = 0
    elif mutation == "count_type":
        rows[-1]["record_counts"]["session"] = True
    elif mutation == "binding":
        rows[0]["binding"]["source_sha256"] = "f" * 64
    elif mutation == "invented":
        rows[0]["invented_only"] = False
    elif mutation == "drop":
        del rows[1]
    elif mutation == "extra":
        rows.insert(2, deepcopy(rows[1]))
    elif mutation == "policy":
        rows[1]["payload"]["result"]["results"].reverse()
    else:
        rows[1]["payload"]["mathematical_verification"]["claim"] = "verified all sources"
    with pytest.raises(report.ReportRefusal):
        verify(rows)


def test_reordered_union_sessions_and_incomplete_original_calendar_refuse_before_read():
    universe = calendar((DAY, LATER))
    rows = records(universe)
    rows[1], rows[2] = rows[2], rows[1]
    with pytest.raises(report.ReportRefusal, match="coordinate_order"):
        verify(rows, universe)
    reader = Reader()
    bad = replace(universe, targets=universe.targets[:1])
    with pytest.raises(sources.SourceRefusal):
        list(report.report_records(bad, reader, binding=BINDING, geometry=geometry(universe),
            invented_only=True, checkpoint=lambda _: None))
    assert reader.calls == []


@pytest.mark.parametrize("mutation", ["truncate", "trailer", "member", "duplicate", "noncanonical", "partialline", "nofooter", "pin", "decodedpin"])
def test_strict_gzip_jsonl_and_full_eof(mutation):
    rows = records()
    decoded = b"".join(report.codec.canonical(row) + b"\n" for row in rows)
    raw, options = packed(decoded), {}
    if mutation == "truncate":
        raw = raw[:-1]
    elif mutation == "trailer":
        raw += b"junk"
    elif mutation == "member":
        raw += packed(b"")
    elif mutation == "duplicate":
        raw = packed(decoded.replace(b'"kind":"header"', b'"kind":"header","kind":"header"', 1))
    elif mutation == "noncanonical":
        raw = packed(decoded.replace(b'"kind":"header"', b'"kind": "header"', 1))
    elif mutation == "partialline":
        raw = packed(decoded[:-1])
    elif mutation == "nofooter":
        raw = packed(b"".join(report.codec.canonical(row) + b"\n" for row in rows[:-1]))
    elif mutation == "pin":
        options["expected_sha256"] = "0" * 64
    else:
        options["expected_decoded_sha256"] = "0" * 64
    with pytest.raises(report.ReportRefusal):
        read(raw, decoded, **options)


@pytest.mark.parametrize("kind", ["compressed", "decoded", "line"])
def test_stream_bounds_are_enforced_without_large_allocations(monkeypatch, kind):
    rows = records()
    decoded = b"".join(report.codec.canonical(row) + b"\n" for row in rows)
    raw = packed(decoded)
    monkeypatch.setattr(report, {"compressed": "MAX_COMPRESSED", "decoded": "MAX_DECODED", "line": "MAX_LINE"}[kind], 10)
    with pytest.raises(report.ReportRefusal):
        read(raw, decoded)
    with pytest.raises(report.ReportRefusal):
        report.write_report(iter(rows), BytesIO(), lambda _: None)


@pytest.mark.parametrize("change", [{"sessions": True}, {"sessions": 258}, {"union_coordinates": 2}, {"extra": 1}])
def test_geometry_is_exact_and_bounded_before_raw_reads(change):
    universe, reader = calendar(), Reader()
    with pytest.raises(report.ReportRefusal):
        list(report.report_records(universe, reader, binding=BINDING,
            geometry=geometry(universe) | change, invented_only=True, checkpoint=lambda _: None))
    assert reader.calls == []


@pytest.mark.parametrize("raw", [b'{ "clock":"S1"}', b'opaque', b'{"x":1,"x":1}'])
def test_original_metadata_bytes_refuse_normalization(raw):
    universe, reader = calendar(), Reader()
    universe = replace(universe, targets=(replace(universe.targets[0], metadata=raw), universe.targets[1]))
    with pytest.raises(report.ReportRefusal):
        list(report.report_records(universe, reader, binding=BINDING,
            geometry=geometry(universe), invented_only=True, checkpoint=lambda _: None))
    assert reader.calls == []


@pytest.mark.parametrize("changed", [True, 1.0])
def test_target_metadata_numeric_type_cannot_change_in_both_header_and_payload(changed):
    universe = calendar()
    universe = replace(universe, targets=tuple(replace(target, metadata=b'{"flag":1}')
                                               for target in universe.targets))
    rows = records(universe)
    for target in rows[0]["calendar"]["targets"]:
        target["metadata"]["flag"] = changed
    for target in rows[1]["payload"]["targets"]:
        target["metadata"]["flag"] = changed
    reseal(rows)
    reader = Reader()
    with pytest.raises(report.ReportRefusal, match="expected_header_differs"):
        verify(rows, universe, raw_reader=reader)
    assert reader.calls == []


def test_source_metadata_bytes_refuse_normalization(monkeypatch):
    real = sources.session_inputs
    def changed(*args, **kwargs):
        view = real(*args, **kwargs)
        return replace(view, metadata=b" " + view.metadata)
    monkeypatch.setattr(sources, "session_inputs", changed)
    with pytest.raises(report.ReportRefusal, match="canonical_json_bytes"):
        records()


def test_reader_change_propagates_and_footer_is_mandatory():
    reader = Reader(fail=3)
    with pytest.raises(sources.SourceRefusal, match="identity_changed"):
        verify(records(), raw_reader=reader)
    with pytest.raises(report.ReportRefusal, match="footer_missing"):
        report.write_report(iter(records()[:-1]), BytesIO(), lambda _: None)
    with pytest.raises(report.ReportRefusal, match="footer_missing"):
        verify([])


def test_nonseekable_short_reads_and_deterministic_gzip():
    rows, first, second = records(), BytesIO(), BytesIO()
    digest = report.write_report(iter(rows), first, lambda _: None)
    assert report.write_report(iter(rows), second, lambda _: None) == digest
    assert first.getvalue() == second.getvalue()
    class ShortReads:
        def __init__(self, raw):
            self.stream = BytesIO(raw)
        def read(self, count):
            return self.stream.read(min(count, 3))
    assert list(report.read_report(ShortReads(first.getvalue()), digest.compressed_sha256,
        digest.decoded_sha256, lambda _: None)) == rows
