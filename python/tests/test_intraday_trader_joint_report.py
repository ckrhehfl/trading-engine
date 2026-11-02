"""Invented original-population joint frames; no actual files/readers."""
from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
from io import BytesIO

import pytest

from research import intraday_ed_sources as sources
from research import intraday_trader_joint_report as report
from research import intraday_trader_joint_inputs as inputs
from research import intraday_trader_joint_account as account
from research import intraday_trader_joint_reference as reference
from research import intraday_trader_scenarios as detectors
from research import intraday_trader_paths as paths
from research import intraday_trader_reentry as reentry
from research import intraday_trader_followups as peer_science


DAY, LATER, SYMBOL = "2026-06-03", "2026-06-04", "KRX:000001"
PAIR = ("EE-A", "EE-B")
BINDING = {key: "a" * 64 for key in report.BINDING_KEYS - {"caller"}} | {"caller": "invented-joint"}


def calendar(days=(DAY,)):
    targets = tuple(sources.TargetCoordinate(clock, f"KRX:{i:06d}", day, i, None,
        "no_stored_rows_unknown_reason", report.codec.canonical(dict(session=day,
        available_at=day + "T00:00:00+09:00", selected_at=day + "T08:00:00+09:00")))
        for day in days for clock in ("S1", "S2") for i in range(1, 21))
    return sources.UniverseCalendar(days, targets, "b" * 64)


def chunk(day=DAY, skip=()):
    blocks = [(100, 105, 95, c) for c in (100, 100, 101, 101, 102, 103)] + [
        (108, 120, 107, 112), (112, 122, 111, 115), (115, 117, 108, 111),
        (111, 114, 109, 113), (113, 116, 110, 115)]
    opening = sources.calendar_source.session_open(day)
    rows = [dict(symbol=SYMBOL, interval="1m", open_time_ms=int(
        (opening + timedelta(minutes=i)).timestamp()) * 1000,
        fetched_at="2026-10-30T00:00:00Z", **dict(zip(("open", "high", "low", "close"),
        map(str, blocks[i // 5] if i // 5 < len(blocks) else (110, 111, 109, 110)))))
        for i in range(300) if i not in skip]
    return dict(symbol=SYMBOL, session=day, observation_date=day, available_at=None,
        retrieved_at="2026-10-30T00:00:00Z", source="collector klines", is_final=None,
        evidence_level="assumed", rows=rows)


class Reader:
    def __init__(self, price=None):
        self.price, self.calls = price, []

    def assert_unchanged(self):
        pass

    def load_raw_chunk(self, kind, symbol, day):
        self.calls.append((kind, symbol, day))
        if isinstance(self.price, dict) and "rows" not in self.price:
            return self.price.get((symbol, day)) if kind == "CC" else None
        return self.price if kind == "CC" and symbol == SYMBOL and day == DAY else None


def options(universe, reader):
    n = len(universe.sessions)
    return dict(universe=universe, binding=BINDING, selected_pair=PAIR, invented_only=True,
        geometry=dict(sessions=n, targets_per_scenario=n * 20, union_coordinates=n * 20,
            cc_chunks=n * 20, cc_rows=300, cx_chunks=n * 20, cx_rows=0, cx_daily_rows=0),
        checkpoint=lambda _: None)


def records(price=None, days=(DAY,), pair=PAIR):
    universe = calendar(days)
    opts = options(universe, None) | {"selected_pair": pair}
    return list(report.report_records(reader=Reader(price), **opts)), opts


def verify(rows, opts, price=None):
    return report.verify_report(iter(rows), raw_reader=Reader(price), **opts)


def test_joint_roundtrip_12_half_allocation_arms_and_original_unknown_scope():
    rows, opts = records(chunk(), (DAY, LATER))
    sink = BytesIO()
    digest = report.write_report(iter(rows), sink, lambda _: None)
    restored = list(report.read_report(BytesIO(sink.getvalue()), digest.compressed_sha256,
                                       digest.decoded_sha256, lambda _: None))
    assert restored == rows and digest.records == 4
    receipt = verify(restored, opts, chunk())
    assert receipt["record_counts"] == {"days": 2, "targets": {"S1": 40, "S2": 40},
                                         "union_coordinates": 40}
    assert receipt["candidate_counts"] == {clock: {"A": 1, "B": 0} for clock in ("S1", "S2")}
    day = report.decode_day(rows[1])
    assert len(day.accounts) == 12
    for arm in day.accounts:
        assert arm.result.state.mode == "A+B"
        assert tuple(s.initial_allocation for s in arm.result.state.book.sleeves) == (
            account.cash.INITIAL_CAPITAL / 2,) * 2
    summary = receipt["summary"]["S1|33|JOINT"]
    assert summary["full_original_population_status"] == "unidentified"
    assert summary["original_population_return"] is None
    assert summary["observed_candidate_status"] == "identified"


def test_future_entry_and_held_missingness_never_remove_causal_candidate():
    for missing in (58, 70):
        rows, opts = records(chunk(skip=(missing,)))
        receipt = verify(rows, opts, chunk(skip=(missing,)))
        assert receipt["candidate_counts"]["S1"]["A"] == 1
        summary = receipt["summary"]["S1|33|JOINT"]
        assert summary["observed_candidate_status"] == "unidentified"
        assert summary["final_nav"] is None
        assert summary["cash"] > 0


@pytest.mark.parametrize("pair", [("EE-A-2R-TARGET", "EE-B-POSITIVE-PEERS"),
    ("EE-A-2R-WITH-ONE-REENTRY", "EE-B-WITH-ONE-REENTRY")])
def test_frozen_followup_and_composed_bundles_keep_first_unknown_candidates(pair):
    price = chunk(skip=(58,))
    rows, opts = records(price, pair=pair)
    receipt = verify(rows, opts, price)
    assert receipt["candidate_counts"]["S1"]["A"] == 1
    assert receipt["summary"]["S1|33|JOINT"]["final_nav"] is None


def test_composed_policy_preserves_both_causal_legs_without_using_reset_as_book_flatness():
    blocks = [(100, 105, 95, c) for c in (100, 100, 101, 101, 102, 103)] + [
        (108, 120, 107, 112), (112, 122, 111, 115), (115, 117, 108, 111),
        (111, 114, 109, 113), (113, 116, 110, 115), (115, 116, 114, 115),
        (123, 131, 114, 123), (123, 126, 121, 125), (125, 126, 120, 122),
        (122, 124, 121, 123), (123, 127, 122, 126)]
    overrides = {60: (115, 131, 114, 130), 64: (130, 131, 121, 123),
                 90: (126, 127, 118, 119), 94: (118, 119, 117, 118)}
    price = chunk()
    for minute, row in enumerate(price["rows"]):
        ohlc = overrides.get(minute, blocks[minute // 5] if minute // 5 < len(blocks) else (126, 127, 125, 126))
        row.update(zip(("open", "high", "low", "close"), map(str, ohlc)))
    pair = ("EE-A-2R-WITH-ONE-REENTRY", "EE-B-WITH-ONE-REENTRY")
    rows, opts = records(price, pair=pair)
    receipt = verify(rows, opts, price)
    assert receipt["candidate_counts"]["S1"]["A"] == 2
    day = report.decode_day(rows[1])
    support = next(c for c in day.coordinates if c.symbol == SYMBOL).slots[0]
    assert tuple(leg.source_policy_id for leg in support.legs) == ("EE-A2R-FIRST", "EE-A2R-ADDITIONAL")
    assert tuple(leg.candidate.policy_id for leg in support.legs) == (pair[0], pair[0])
    assert receipt["summary"]["S1|33|JOINT"]["entries"] == 2


@pytest.mark.parametrize("pair", [PAIR, ("EE-A-2R-TARGET", "EE-B-POSITIVE-PEERS"),
    ("EE-A-2R-WITH-ONE-REENTRY", "EE-B-WITH-ONE-REENTRY")])
def test_saved_verifier_reconstructs_without_any_producer_account_or_detector_oracle(monkeypatch, pair):
    price = chunk()
    rows, opts = records(price, pair=pair)
    def forbidden(*args, **kwargs):
        raise AssertionError("producer oracle called")
    monkeypatch.setattr(account, "replay", forbidden)
    monkeypatch.setattr(account, "new_state", forbidden)
    monkeypatch.setattr(detectors, "detect_bars", forbidden)
    monkeypatch.setattr(paths, "evaluate_path", forbidden)
    monkeypatch.setattr(reentry, "_detection", forbidden)
    monkeypatch.setattr(reentry, "evaluate_session", forbidden)
    monkeypatch.setattr(peer_science, "peer_context", forbidden)
    assert verify(rows, opts, price)["verified"]


def test_positive_peer_candidate_uses_all_original_peers_and_independent_math():
    blocks = [(105, 110, 100, 105)] * 6 + [(105, 112, 90, 95), (95, 98, 92, 97),
        (97, 99, 93, 98), (98, 101, 94, 100), (100, 103, 95, 102), (102, 106, 96, 105)]
    prices = {}
    for i in range(1, 21):
        symbol = f"KRX:{i:06d}"
        value = chunk()
        value["symbol"] = symbol
        for minute, row in enumerate(value["rows"]):
            row["symbol"] = symbol
            ohlc = ((blocks[minute // 5] if minute // 5 < len(blocks) else (105, 106, 104, 105))
                    if i == 1 else (100, 102, 99, 101))
            row.update(zip(("open", "high", "low", "close"), map(str, ohlc)))
        prices[symbol, DAY] = value
    rows, opts = records(prices, pair=("EE-A", "EE-B-POSITIVE-PEERS"))
    receipt = verify(rows, opts, prices)
    assert receipt["candidate_counts"]["S1"]["B"] == 1
    day = report.decode_day(rows[1])
    support = next(c for c in day.coordinates if c.symbol == SYMBOL).slots[1]
    context = report.codec._json(support.peer_context_metadata)
    assert context["complete_count"] == 19 and context["state"] == "known"


@pytest.mark.parametrize("damage", ["cash", "missing_day", "receipt", "unknown_zero", "footer", "boolean_count"])
def test_tampered_frames_and_missing_calendar_day_refused(damage):
    rows, opts = records(chunk(), (DAY, LATER))
    rows = deepcopy(rows)
    if damage == "cash":
        rows[1]["payload"]["accounts"][0]["result"]["state"]["book"]["sleeves"][0]["cash"] = {"fraction": [0, 1]}
    elif damage == "missing_day":
        del rows[1]
    elif damage == "receipt":
        rows[1]["payload"]["verification"]["verified"] = False
    elif damage == "unknown_zero":
        rows[-1]["summary"]["S1|33|JOINT"]["original_population_return"] = {"fraction": [0, 1]}
    elif damage == "boolean_count":
        rows[-1]["candidate_counts"]["S1"]["A"] = True
    else:
        rows.pop()
    with pytest.raises(ValueError):
        verify(rows, opts, chunk())


def test_exact_compressed_decoded_pins_and_full_gzip_eof_are_required():
    rows, _ = records(chunk())
    sink = BytesIO()
    digest = report.write_report(iter(rows), sink, lambda _: None)
    packed = sink.getvalue()
    decoded = gzip.decompress(packed)
    for raw, pin, plain_pin in ((packed[:-1], hashlib.sha256(packed[:-1]).hexdigest(), digest.decoded_sha256),
        (packed, "0" * 64, digest.decoded_sha256), (packed, digest.compressed_sha256, "0" * 64),
        (packed + packed, hashlib.sha256(packed + packed).hexdigest(), hashlib.sha256(decoded * 2).hexdigest())):
        with pytest.raises(ValueError):
            list(report.read_report(BytesIO(raw), pin, plain_pin, lambda _: None))


def test_original_population_is_validated_before_raw_and_cache_discards_prior_day():
    universe = calendar((DAY, LATER))
    reader = Reader(chunk())
    cache = inputs.followup.DayRawCache(universe, reader)
    cache.own(SYMBOL, DAY)
    cache.own(SYMBOL, LATER)
    assert cache.day == LATER and set(cache._raw) == {SYMBOL}
    from dataclasses import replace
    bad = replace(universe, targets=universe.targets[:-1])
    reader = Reader()
    with pytest.raises(ValueError):
        list(report.report_records(reader=reader, **(options(bad, reader))))
    assert reader.calls == []


def test_production_and_reference_parse_private_raw_copies_into_disjoint_minute_views():
    universe, reader = calendar(), Reader(chunk())
    cache = inputs.followup.DayRawCache(universe, reader)
    produced = inputs.prepare_day(universe, cache, DAY, PAIR)
    independent = inputs.prepare_day(universe, cache, DAY, PAIR, independent=True)
    assert produced.coordinates == independent.coordinates
    for symbol in produced.sessions:
        assert produced.sessions[symbol] is not independent.sessions[symbol]
        assert produced.sessions[symbol].minutes is not independent.sessions[symbol].minutes
    with pytest.raises((AttributeError, TypeError)):
        produced.sessions[SYMBOL].minutes.clear()
    assert len(independent.sessions[SYMBOL].minutes) == 300




def test_day_stream_releases_every_typed_minute_view_before_yield_and_never_reads_future_day(monkeypatch):
    import gc
    import weakref
    refs = []
    for name in ("session_inputs", "full_reference_session_inputs"):
        original = getattr(sources, name)
        def tracked(*args, _original=original, **kwargs):
            value = _original(*args, **kwargs)
            refs.append(weakref.ref(value.session if hasattr(value, "session") else value))
            return value
        monkeypatch.setattr(sources, name, tracked)
    universe, reader = calendar((DAY, LATER)), Reader(chunk())
    stream = report.report_records(reader=reader, **options(universe, reader))
    assert next(stream)["kind"] == "header" and reader.calls == []
    assert next(stream)["payload"]["day"] == DAY
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    assert all(day == DAY for _, _, day in reader.calls)
    assert next(stream)["payload"]["day"] == LATER
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert next(stream)["kind"] == "footer"
    with pytest.raises(StopIteration):
        next(stream)
