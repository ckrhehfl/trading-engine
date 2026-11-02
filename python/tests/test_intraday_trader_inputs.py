"""Invented in-memory population/chunk checks; no archives, databases or IO."""
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
import json
import weakref

import pytest

from research import intraday_ed_sources as sources
from research import intraday_trader_inputs as adapter
from research import intraday_trader_reference as reference


DAY = "2026-06-03"
NEXT_DAY = "2026-06-04"
SYMBOL = "KRX:000001"
OTHER = "KRX:000002"
MINUTE = timedelta(minutes=1)


def target(clock="S1", day=DAY, symbol=SYMBOL, rank=1, **changes):
    return replace(sources.TargetCoordinate(clock, symbol, day, rank, None,
        "no_stored_rows_unknown_reason", b'original opaque day metadata'), **changes)


def universe(*, days=(DAY,), symbols=(SYMBOL,)):
    targets = tuple(target(clock, day, symbol, rank) for clock in ("S1", "S2")
                    for day in days for rank, symbol in enumerate(symbols, 1))
    return sources.UniverseCalendar(days, targets, "a" * 64)


def a_blocks():
    return [(100, 105, 95, close) for close in (100, 100, 101, 101, 102, 103)] + [
        (108, 120, 107, 112), (112, 122, 111, 115), (115, 117, 108, 111),
        (111, 114, 109, 113), (113, 116, 110, 115)]


def chunk(kind="CC", *, day=DAY, symbol=SYMBOL, blocks=(), skip=(), volume="0"):
    opening = sources.calendar_source.session_open(day)
    rows = []
    for index in range(300):
        if index in skip:
            continue
        row = {"open_time_ms": int((opening + index * MINUTE).timestamp()) * 1000,
               "fetched_at": "2026-10-30T00:00:00Z"}
        if kind == "CC":
            values = blocks[index // 5] if index // 5 < len(blocks) else (110, 111, 109, 110)
            row.update(symbol=symbol, interval="1m", **dict(zip(
                ("open", "high", "low", "close"), map(str, values), strict=True)))
        else:
            row.update(volume=volume, quote_volume=None)
        rows.append(row)
    result = dict(symbol=symbol, session=day, observation_date=day, available_at=None,
                  retrieved_at="2026-10-30T00:00:00Z", source="collector klines",
                  is_final=None, evidence_level="assumed", rows=rows)
    if kind == "CX":
        result.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return result


class Reader:
    def __init__(self, chunks=None, *, fail_check=None):
        self.chunks = {} if chunks is None else chunks
        self.calls = []
        self.events = []
        self.checks = 0
        self.fail_check = fail_check

    def assert_unchanged(self):
        self.checks += 1
        self.events.append("unchanged")
        if self.checks == self.fail_check:
            raise sources.SourceRefusal("invented_reader_identity_changed")

    def load_raw_chunk(self, kind, symbol, session):
        self.calls.append((kind, symbol, session))
        self.events.append(kind)
        return self.chunks.get((kind, symbol, session))


def reader_for(price, volume=None):
    values = {("CC", price["symbol"], price["session"]): price}
    if volume is not None:
        values[("CX", volume["symbol"], volume["session"])] = volume
    return Reader(values)


def one(reader, calendar=None):
    records = tuple(adapter.iter_original_sessions(universe() if calendar is None else calendar,
                                                   reader, lambda _: None))
    assert len(records) == 1
    return records[0]


def policy(record, scenario="EE-A"):
    return next(item for item in record.result.results if item.scenario_id == scenario)


def test_unknown_original_coordinates_and_both_clocks_survive_one_shared_read():
    calendar = universe()
    reader = Reader()
    record = one(reader, calendar)
    assert record.targets == calendar.targets
    assert all(a is b for a, b in zip(record.targets, calendar.targets, strict=True))
    assert all(t.stored_row_count is None and t.state == "no_stored_rows_unknown_reason"
               for t in record.targets)
    assert reader.calls == [("CC", SYMBOL, DAY), ("CX", SYMBOL, DAY)]
    assert all(p.detection.state == "unknown" and p.path is None for p in record.result.results)
    assert record.mathematical_verification.scenarios == adapter.SCENARIOS
    assert record.mathematical_verification.compared_policy_count == 4
    assert "no source/runtime/archive/population/account/publication authentication" in record.mathematical_verification.claim
    with pytest.raises(FrozenInstanceError):
        record.targets = ()


def test_union_order_full_metadata_and_streaming_checkpoints():
    calendar = universe(days=(DAY, NEXT_DAY), symbols=(SYMBOL, OTHER))
    calendar = replace(calendar, targets=calendar.targets[::-1])
    reader = Reader()
    checkpoints = []
    def checkpoint(phase):
        checkpoints.append(phase)
        reader.events.append(phase)
    stream = adapter.iter_original_sessions(calendar, reader, checkpoint)
    assert reader.calls == []
    first = next(stream)
    assert (first.result.opening.date().isoformat(), first.result.symbol) == (DAY, SYMBOL)
    assert reader.calls == [("CC", SYMBOL, DAY), ("CX", SYMBOL, DAY)]
    records = (first, *stream)
    expected = [(day, symbol) for day in (DAY, NEXT_DAY) for symbol in (SYMBOL, OTHER)]
    assert [(r.result.opening.date().isoformat(), r.result.symbol) for r in records] == expected
    for record, (day, symbol) in zip(records, expected, strict=True):
        assert record.targets == tuple(t for t in calendar.targets if (t.session, t.symbol) == (day, symbol))
    assert reader.calls == [(kind, symbol, day) for day, symbol in expected for kind in ("CC", "CX")]
    assert checkpoints == ["ee_original_session_before", "ee_original_session_after"] * 4
    assert reader.events == ["ee_original_session_before", "unchanged", "CC", "CX", "unchanged",
                             "ee_original_session_after"] * 4


@pytest.mark.parametrize("change,reason", [
    (lambda u: {}, "typed_universe_calendar_required"),
    (lambda u: replace(u, sessions=[]), "invalid_original_calendar"),
    (lambda u: replace(u, sessions=()), "invalid_original_calendar"),
    (lambda u: replace(u, sessions=(DAY, DAY)), "invalid_original_calendar"),
    (lambda u: replace(u, sessions=(NEXT_DAY, DAY)), "invalid_original_calendar"),
    (lambda u: replace(u, sessions=("2026-02-30",)), "invalid_original_session"),
    (lambda u: replace(u, sessions=("20260603",)), "invalid_original_session"),
    (lambda u: replace(u, sessions=(1,)), "invalid_original_session"),
    (lambda u: replace(u, raw_sha256=b"a" * 64), "invalid_original_calendar_identity"),
    (lambda u: replace(u, raw_sha256="not a digest"), "invalid_original_calendar_identity"),
    (lambda u: replace(u, targets=[]), "invalid_original_targets"),
    (lambda u: replace(u, targets=()), "invalid_original_targets"),
    (lambda u: replace(u, targets=(object(), *u.targets)), "typed_original_target_required"),
    (lambda u: replace(u, targets=(*u.targets, u.targets[0])), "duplicate_original_target"),
    (lambda u: replace(u, targets=(*u.targets, target(symbol=OTHER))), "duplicate_original_day_rank"),
    (lambda u: replace(u, targets=u.targets[:1]), "original_timing_calendar_incomplete"),
    (lambda u: replace(u, sessions=(DAY, NEXT_DAY)), "original_timing_calendar_incomplete"),
])
def test_entire_calendar_rejected_before_raw_reads(change, reason):
    reader = Reader()
    with pytest.raises(sources.SourceRefusal, match=reason):
        tuple(adapter.iter_original_sessions(change(universe()), reader, lambda _: None))
    assert reader.calls == [] and reader.checks == 0


@pytest.mark.parametrize("changes,reason", [
    ({"scenario": "S3"}, "invalid_original_timing_clock"),
    ({"scenario": []}, "invalid_original_timing_clock"),
    ({"symbol": "005930"}, "invalid_original_symbol"),
    ({"symbol": None}, "invalid_original_symbol"),
    ({"session": NEXT_DAY}, "target_outside_original_calendar"),
    ({"session": []}, "target_outside_original_calendar"),
    ({"rank": True}, "invalid_original_rank"),
    ({"rank": 0}, "invalid_original_rank"),
    ({"rank": 1.0}, "invalid_original_rank"),
    ({"metadata": {}}, "opaque_original_target_metadata_bytes_required"),
    ({"metadata": "opaque"}, "opaque_original_target_metadata_bytes_required"),
    ({"stored_row_count": 0}, "original_target_missingness_contract_differs"),
    ({"stored_row_count": True}, "original_target_missingness_contract_differs"),
    ({"stored_row_count": 3}, "original_target_missingness_contract_differs"),
    ({"state": "observed"}, "original_target_missingness_contract_differs"),
])
def test_even_last_invalid_target_fails_before_first_read(changes, reason):
    calendar = universe()
    calendar = replace(calendar, targets=(calendar.targets[0], replace(calendar.targets[1], **changes)))
    reader = Reader()
    with pytest.raises(sources.SourceRefusal, match=reason):
        one(reader, calendar)
    assert reader.calls == []


def test_original_state_requires_exact_string_type_before_raw_reads():
    class DerivedState(str):
        pass
    calendar = universe()
    changed = replace(calendar.targets[-1], state=DerivedState("no_stored_rows_unknown_reason"))
    calendar = replace(calendar, targets=(*calendar.targets[:-1], changed))
    reader = Reader()
    with pytest.raises(sources.SourceRefusal, match="original_target_missingness_contract_differs"):
        one(reader, calendar)
    assert reader.calls == []


@pytest.mark.parametrize("reader", [object(), None, type("Bad", (), {"load_raw_chunk": 3,
                                                                     "assert_unchanged": lambda _: None})()])
def test_reader_requires_callable_protocol_not_a_concrete_class(reader):
    with pytest.raises(sources.SourceRefusal, match="raw_reader_contract_required"):
        one(reader)


def test_noncallable_checkpoint_is_rejected_before_reads():
    reader = Reader()
    with pytest.raises(sources.SourceRefusal, match="checkpoint_callable_required"):
        tuple(adapter.iter_original_sessions(universe(), reader, None))
    assert reader.calls == []


@pytest.mark.parametrize("fail_check,reads", [(1, 0), (2, 2)])
def test_reader_identity_change_before_or_after_session_refuses_yield(fail_check, reads):
    reader = Reader(fail_check=fail_check)
    with pytest.raises(sources.SourceRefusal, match="invented_reader_identity_changed"):
        next(adapter.iter_original_sessions(universe(), reader, lambda _: None))
    assert len(reader.calls) == reads


def test_price_without_volume_is_valid_and_original_count_does_not_filter_it():
    record = one(reader_for(chunk(blocks=a_blocks())))
    chosen = policy(record)
    assert chosen.detection.state == "candidate" and chosen.path.status == "traded"
    assert chosen.detection.candidate.attempt_number == 1
    assert chosen.detection.candidate.previous_terminal is None
    assert all(t.stored_row_count is None for t in record.targets)
    metadata = json.loads(record.result.source_metadata)
    assert metadata["sources"]["CX"] is None
    assert metadata["physical_unit_status"] is None
    assert metadata["stored_numeric_observations_are_unit_certification"] is False


def test_positive_original_stored_count_stays_unverified_when_price_is_missing():
    calendar = universe()
    calendar = replace(calendar, targets=(replace(calendar.targets[0], stored_row_count=300,
        state="stored_rows_completeness_unverified"), calendar.targets[1]))
    reader = Reader({("CX", SYMBOL, DAY): chunk("CX")})
    record = one(reader, calendar)
    assert record.targets == calendar.targets
    assert record.targets[0].stored_row_count == 300
    assert all(p.detection.state == "unknown" for p in record.result.results)
    metadata = json.loads(record.result.source_metadata)
    assert metadata["sources"]["CC"] is None
    assert metadata["minute_quality"][0] == {
        "minute": "2026-06-03T09:00:00+09:00", "price": "missing", "volume": "observed"}


@pytest.mark.parametrize("volume,expected,state", [("0", Decimal(0), "observed"),
                                                  (None, None, "missing")])
def test_volume_zero_and_missing_remain_distinct(monkeypatch, volume, expected, state):
    observations = []
    original = sources.session_inputs
    def observe(*args, **kwargs):
        view = original(*args, **kwargs)
        observations.append(next(iter(view.session.minutes.values())))
        return view
    monkeypatch.setattr(sources, "session_inputs", observe)
    price = chunk(blocks=a_blocks())
    record = one(reader_for(price, None if volume is None else chunk("CX", volume=volume)))
    assert observations[0].shares == expected
    assert observations[0].volume_state == state
    assert observations[0].price_state == "observed"
    assert policy(record).path.status == "traded"


def test_bad_raw_price_keeps_existing_decoder_quality_and_unknown():
    price = chunk(blocks=a_blocks())
    price["rows"][10]["close"] = "bad-number"
    record = one(reader_for(price))
    assert policy(record).detection.state == "unknown"
    quality = json.loads(record.result.source_metadata)["minute_quality"]
    assert quality[10]["price"] == "raw_invalid_numeric_or_ohlc"


def test_raw_identity_error_propagates_and_checks_session_after_failure():
    reader = reader_for(chunk(symbol=OTHER))
    reader.chunks[("CC", SYMBOL, DAY)] = reader.chunks[("CC", OTHER, DAY)]
    phases = []
    with pytest.raises(sources.SourceRefusal, match="session_source_identity_differs"):
        tuple(adapter.iter_original_sessions(universe(), reader, phases.append))
    assert reader.checks == 2
    assert phases == [adapter.BEFORE_SESSION, adapter.AFTER_SESSION]


def test_separate_raw_decodes_and_opaque_source_bytes_only(monkeypatch):
    original_production = sources.session_inputs
    original_reference = sources.full_reference_session_inputs
    seen = {}
    opaque = b'\x00original decoder metadata\xff'
    def production(symbol, day, price, volume):
        view = original_production(symbol, day, price, volume)
        seen["production"] = view.session
        seen["raw"] = price, volume
        return replace(view, metadata=opaque)
    def independent(symbol, day, price, volume):
        assert price is seen["raw"][0] and volume is seen["raw"][1]
        parsed = original_reference(symbol, day, price, volume)
        assert parsed is not seen["production"]
        assert parsed.minutes is not seen["production"].minutes
        seen["reference"] = parsed
        return parsed
    monkeypatch.setattr(sources, "session_inputs", production)
    monkeypatch.setattr(sources, "full_reference_session_inputs", independent)
    record = one(reader_for(chunk(blocks=a_blocks())))
    assert "reference" in seen and record.result.source_metadata == opaque


def test_decoded_session_rows_are_released_before_yield(monkeypatch):
    refs = []
    original_production = sources.session_inputs
    original_reference = sources.full_reference_session_inputs
    def production(*args):
        view = original_production(*args)
        refs.append(weakref.ref(view.session))
        return view
    def independent(*args):
        parsed = original_reference(*args)
        refs.append(weakref.ref(parsed))
        return parsed
    monkeypatch.setattr(sources, "session_inputs", production)
    monkeypatch.setattr(sources, "full_reference_session_inputs", independent)
    stream = adapter.iter_original_sessions(universe(), reader_for(chunk(blocks=a_blocks())), lambda _: None)
    record = next(stream)
    assert record.mathematical_verification.compared_policy_count == 4
    assert len(refs) == 2 and all(ref() is None for ref in refs)


def test_producer_only_decoding_corruption_is_not_reference_oracle(monkeypatch):
    original = sources.session_inputs
    def corrupt(*args):
        view = original(*args)
        changed = dict(view.session.minutes)
        del changed[view.session.opening + 10 * MINUTE]
        return replace(view, session=replace(view.session, minutes=changed))
    monkeypatch.setattr(sources, "session_inputs", corrupt)
    with pytest.raises(reference.ReferenceRefusal):
        one(reader_for(chunk(blocks=a_blocks())))


def test_production_result_mutation_is_rejected_without_zero_fallback(monkeypatch):
    original = adapter.producer.evaluate_session
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        p = result.results[0]
        return replace(result, results=(replace(p, path=replace(p.path, net33=0)), *result.results[1:]))
    monkeypatch.setattr(adapter.producer, "evaluate_session", changed)
    reader = reader_for(chunk(blocks=a_blocks()))
    with pytest.raises(reference.ReferenceRefusal):
        one(reader)
    assert reader.checks == 2


def test_refusal_is_verified_and_retained_without_numeric_result(monkeypatch):
    original_production = sources.session_inputs
    original_reference = sources.full_reference_session_inputs
    def malformed(session):
        rows = dict(session.minutes)
        at = session.opening + 60 * MINUTE
        rows[at] = replace(rows[at], available_at=at + 4 * MINUTE)
        return replace(session, minutes=rows)
    def production(*args):
        view = original_production(*args)
        return replace(view, session=malformed(view.session))
    def independent(*args):
        return malformed(original_reference(*args))
    monkeypatch.setattr(sources, "session_inputs", production)
    monkeypatch.setattr(sources, "full_reference_session_inputs", independent)
    record = one(reader_for(chunk(blocks=a_blocks())))
    assert policy(record).detection.state == "candidate"
    assert policy(record).path is None
    assert policy(record).path_refusal == "held_minute_unavailable_at_decision"


@pytest.mark.parametrize("gap,state,path_status", [(10, "unknown", None),
                                                  (70, "candidate", "unknown"),
                                                  (200, "candidate", "traded")])
def test_early_gap_held_gap_and_unused_late_gap_are_distinct(gap, state, path_status):
    record = one(reader_for(chunk(blocks=a_blocks(), skip=(gap,))))
    chosen = policy(record)
    assert chosen.detection.state == state
    assert (None if chosen.path is None else chosen.path.status) == path_status
    if path_status == "unknown":
        assert chosen.path.gross is None and chosen.path.net33 is None
        assert chosen.path.reason == "missing_held_minute"


@pytest.mark.parametrize("options", [{"scenarios": ("EE-C",)}, {"previous": {}}])
def test_c_content_and_reentry_are_outside_this_iterator_api(options):
    with pytest.raises(TypeError):
        tuple(adapter.iter_original_sessions(universe(), Reader(), lambda _: None, **options))


def test_no_old_ed_or_ea_mask_and_no_automatic_reentry(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("old source selector or ED signal mask called")
    monkeypatch.setattr(sources.calendar_source, "_target_matrix", forbidden)
    monkeypatch.setattr(sources.inputs, "first_h1_parent", forbidden)
    monkeypatch.setattr(sources.inputs, "h1_rule_decision", forbidden)
    record = one(reader_for(chunk(blocks=a_blocks())))
    assert tuple(p.scenario_id for p in record.result.results) == adapter.SCENARIOS
    assert all(p.detection.attempt_number == 1 for p in record.result.results)
    assert all(p.detection.candidate is None or p.detection.candidate.previous_terminal is None
               for p in record.result.results)
