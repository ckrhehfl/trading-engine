"""Invented DK wire and streamed stock chunks; no market IO or old replay."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import inspect
import json
import random
import sys

import pytest

from research import intraday_market_context_readiness as market
from test_intraday_breakout_loss_concentration import build_synthetic_fixture as _dk_fixture


SMALL_DAYS = ("2025-09-04", "2025-09-11", "2025-11-13", "2026-01-02")


def _assert_canonical_chunks(value):
    expected = market._canonical(value)
    chunks = list(market.canonical_chunks(value))
    assert all(type(chunk) is bytes and 0 < len(chunk) <= market.CANONICAL_CHUNK_BYTES for chunk in chunks)
    assert b"".join(chunks) == expected
    assert market._digest(value) == hashlib.sha256(expected + b"\n").hexdigest()


def test_canonical_chunks_scalar_key_default_and_escape_byte_parity():
    for value in (None, True, False, 0, -123, 1.25, -0.0, 5e-324, 1.7976931348623157e308,
            "", 'price 00100.0000\\\"\n\t\x00\x1f 한글 😀 \u2028', "\n", (), {}, [],
            Decimal("100.0000000000000000001"), Decimal("NaN"), Fraction(-1, 3),
            datetime(2026, 1, 2, 3, 4, 5, 123456, timezone(timedelta(hours=9))),
            {2: "two", -1: "minus", 10: "ten"}, {None: "none"}, {True: "true"},
            {-1.5: "float", 0.0: "zero"}, ("00100.000", {"한": [False, None, Fraction(1, 7)]})):
        _assert_canonical_chunks(value)
    assert b"".join(market.canonical_chunks({"price": "00100.0000"})) == b'{"price":"00100.0000"}'


def test_canonical_chunks_seeded_nested_byte_parity_and_shared_objects():
    rng = random.Random(20261025)
    atoms = [None, True, False, 0, -123, 0.01, -0.0, "00123.4500", "한글😀\\\"\n\x00", ""]

    def value(depth):
        if not depth or rng.randrange(3) == 0:
            return rng.choice(atoms)
        if rng.randrange(2):
            return [value(depth - 1) for _ in range(rng.randrange(6))]
        return {f"{index}한": value(depth - 1) for index in range(rng.randrange(6))}

    for _ in range(180):
        _assert_canonical_chunks(value(5))
    shared = {"source": ["한글😀\\\"\n", {"price": "00100.0000"}] * 50}
    _assert_canonical_chunks({"first": shared, "second": shared, "many": [shared] * 400})


def test_canonical_chunks_bound_c_encoder_before_serializing_giant_rows(monkeypatch):
    row = {"price": "00100.00000000000001", "metadata": ["한글😀\\\"\n\x00"] * 10}
    source = {"rows": [row] * 2000, "single_giant_row": {"text": "한😀\\\"\n\x00" * 100000}}
    expected = market._canonical(source)
    original = market.dl._encoder
    encoded = []

    class CheckedEncoder:
        def encode(self, current):
            assert current is not source and current is not source["rows"]
            assert current is not source["single_giant_row"]
            if isinstance(current, (dict, list, tuple)):
                assert market._canonical_native_probe(current)[0]
            if isinstance(current, str):
                assert 2 + 6 * str.__len__(current) <= market.CANONICAL_CHUNK_BYTES
            if isinstance(current, int):
                assert 3 + int.bit_length(current) * 302 // 1000 <= market.CANONICAL_CHUNK_BYTES
            encoded.append(current)
            return original().encode(current)

    monkeypatch.setattr(market.dl, "_encoder", CheckedEncoder)
    chunks = list(market.canonical_chunks(source))
    assert b"".join(chunks) == expected
    assert encoded and all(len(chunk) <= 262144 for chunk in chunks)
    assert any(current is row for current in encoded)


def test_canonical_chunks_deep_structure_and_large_scalar_key_remain_exact():
    value = {"price": "00100.00"}
    for _ in range(550):
        value = [value]
    _assert_canonical_chunks(value)
    _assert_canonical_chunks({"한😀\\\"\n" * 90000: {"tail": "0001.000"}})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
    {float("nan"): "invalid key"}, [object()], {"unsupported": {1, 2}}, b"bytes",
    {("tuple",): "unsupported key"}, {"string": 1, 2: "mixed sorted key types"}, "\ud800"])
def test_canonical_chunks_keep_native_refusals(value):
    with pytest.raises((TypeError, ValueError, UnicodeEncodeError)) as previous:
        market._canonical(value)
    with pytest.raises(type(previous.value)):
        list(market.canonical_chunks(value))


def test_canonical_chunks_circular_refusal_and_integer_digit_guard():
    for value in ([], {}):
        if isinstance(value, list):
            value.append(value)
        else:
            value["cycle"] = value
        with pytest.raises(ValueError, match="Circular reference"):
            list(market.canonical_chunks(value))
    limit = sys.get_int_max_str_digits()
    assert limit > 0, "the supported runtime retains CPython's integer digit guard"
    _assert_canonical_chunks(10 ** (limit - 1))
    for value in (10 ** limit, 10 ** market.CANONICAL_CHUNK_BYTES):
        with pytest.raises(ValueError):
            market._canonical(value)
        with pytest.raises(ValueError):
            list(market.canonical_chunks(value))


def test_canonical_chunks_checkpoints_interrupt_walk_and_long_scalar(monkeypatch):
    calls = 0
    encoded = 0

    def stop():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("invented_budget_exhausted")

    original = market.dl._encoder

    def measured_encode():
        class Encoder:
            def encode(self, value):
                nonlocal encoded
                encoded += 1
                return original().encode(value)
        return Encoder()

    monkeypatch.setattr(market.dl, "_encoder", measured_encode)
    with pytest.raises(RuntimeError, match="invented_budget_exhausted"):
        list(market.canonical_chunks([None] * 40000, checkpoint=stop))
    assert calls == 2
    assert 0 < encoded <= 4096
    calls = 0
    encoded = 0
    with pytest.raises(RuntimeError, match="invented_budget_exhausted"):
        list(market.canonical_chunks("x" * 300000, checkpoint=stop))
    assert calls == 2
    assert encoded == 0


def test_canonical_chunks_reflect_mutations_across_calls_and_do_not_mutate_source():
    shared = {"text": "00100.00", "metadata": ["assumed", {"final": None}]}
    source = [shared] * 4000
    first = b"".join(market.canonical_chunks(source))
    shared["text"] = "한😀\\\"\n" * 70000
    shared["metadata"][1]["final"] = False
    # Only a few references are needed for the large replacement.
    source = source[:2]
    before = deepcopy(source)
    second = b"".join(market.canonical_chunks(source))
    assert first != second and second == market._canonical(source)
    assert source == before
    assert market._digest(source) == hashlib.sha256(second + b"\n").hexdigest()


def test_canonical_chunks_mutation_during_iteration_cannot_reuse_mutable_size(monkeypatch):
    original = market.dl._encoder
    encoded = []

    class CheckedEncoder:
        def encode(self, current):
            if isinstance(current, (dict, list, tuple)):
                assert market._canonical_native_probe(current)[0]
            if isinstance(current, str):
                assert 2 + 6 * str.__len__(current) <= market.CANONICAL_CHUNK_BYTES
            encoded.append(current)
            return original().encode(current)

    monkeypatch.setattr(market.dl, "_encoder", CheckedEncoder)
    shared = {"text": "small"}
    stream = market.canonical_chunks([shared] * 30000)
    first = next(stream)
    shared["text"] = "y" * 300000
    # Close after the new large value reaches the wire; never build a giant
    # mutated parent merely to test that no stale container bound is used.
    observed = bytearray(first[-100:])
    for block in stream:
        assert len(block) <= market.CANONICAL_CHUNK_BYTES
        observed.extend(block)
        if b"y" * 100 in observed:
            break
    stream.close()
    assert b"y" * 100 in observed
    assert any(isinstance(value, str) and value.startswith("y") for value in encoded)


def test_canonical_scalar_cache_checks_object_identity_even_if_ids_are_reused(monkeypatch):
    native_id = id
    monkeypatch.setattr(market, "id", lambda value: 17 if type(value) is str else native_id(value), raising=False)
    source = [{"value": str(index) + "-invented", "ratio": Fraction(index, 7)} for index in range(500)]
    _assert_canonical_chunks(source)


def test_canonical_scalar_cache_entry_byte_caps_and_close_release(monkeypatch):
    monkeypatch.setattr(market, "CANONICAL_CACHE_ENTRIES", 16)
    monkeypatch.setattr(market, "CANONICAL_CACHE_BYTES", 1000)
    source = [str(index) + "x" * 100 for index in range(6000)]
    stream = market.canonical_chunks(source)
    next(stream)
    cache = stream.gi_frame.f_locals["cache"]
    assert 0 < len(cache) <= 16
    assert sum(len(raw) for _, raw in cache.values()) <= 1000
    assert all(type(value) in (str, int, float, bool, type(None)) for value, _ in cache.values())
    stream.close()
    assert cache == {}


def test_canonical_scalar_cache_and_buffer_memory_stay_bounded():
    import tracemalloc
    source = [str(index) + "x" * 100 for index in range(30000)]
    tracemalloc.start()
    try:
        digest = hashlib.sha256()
        for block in market.canonical_chunks(source):
            assert len(block) <= market.CANONICAL_CHUNK_BYTES
            digest.update(block)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 5 * 1024**2
    assert digest.hexdigest() == hashlib.sha256(market._canonical(source)).hexdigest()


def test_canonical_chunks_string_subclass_cannot_bypass_scalar_bound(monkeypatch):
    class InventedText(str):
        def __len__(self):
            return 1

        def __getitem__(self, index):
            raise AssertionError("use the immutable string contents")

    original = market.dl._encoder

    class CheckedEncoder:
        def encode(self, current):
            assert 2 + 6 * str.__len__(current) <= market.CANONICAL_CHUNK_BYTES
            return original().encode(current)

    monkeypatch.setattr(market.dl, "_encoder", CheckedEncoder)
    value = InventedText("한😀\\\"\n" * 90000)
    assert b"".join(market.canonical_chunks(value)) == original().encode(value).encode()


def test_canonical_chunks_excessive_depth_keeps_recursion_refusal():
    value = []
    for _ in range(sys.getrecursionlimit() + 100):
        value = [value]
    with pytest.raises(RecursionError):
        list(market.canonical_chunks(value))


def test_canonical_native_fast_path_is_fresh_and_handles_195_row_metadata(monkeypatch):
    original = market.dl._encoder
    retrievals = [f"2026-10-20T00:{index//60:02d}:{index%60:02d}+00:00" for index in range(195)]
    offsets = {str(index): 1 for index in range(195)}
    encoded = []

    class CheckedEncoder:
        def encode(self, value):
            if isinstance(value, (list, dict, tuple)):
                assert market._canonical_native_probe(value)[0]
                encoded.append(value)
            return original().encode(value)

    monkeypatch.setattr(market.dl, "_encoder", CheckedEncoder)
    value = [retrievals, offsets] * 3
    assert b"".join(market.canonical_chunks(value)) == original().encode(value).encode()
    assert any(value is retrievals for value in encoded)
    assert any(value is offsets for value in encoded)
    offsets["late"] = {"large": "x" * 300000}
    retrievals[0] = "한😀\\\"\n" * 70000
    assert not market._canonical_native_probe(offsets)[0]
    assert not market._canonical_native_probe(retrievals)[0]
    value = [retrievals, offsets]
    assert b"".join(market.canonical_chunks(value)) == original().encode(value).encode()


def test_canonical_nested_probe_caps_nodes_depth_bytes_and_unknown_types():
    nested = {"metadata": [{"price": "00100.00", "availability": None}] * 20}
    fits, visited = market._canonical_native_probe(nested)
    assert fits and 1 <= visited <= 1024
    _assert_canonical_chunks(nested)
    for value in ([None] * 1024, {"text": "x" * market.CANONICAL_CHUNK_BYTES},
            {"default": Fraction(1, 7)}, {"subclass": type("Text", (str,), {})("native fallback")}):
        fits, visited = market._canonical_native_probe(value)
        assert not fits and visited <= 1024
        _assert_canonical_chunks(value)
    deep = []
    for _ in range(10):
        deep = [deep]
    assert not market._canonical_native_probe(deep)[0]
    _assert_canonical_chunks(deep)


def test_canonical_nested_probe_mutation_checkpoint_is_before_fresh_scan(monkeypatch):
    original = market.dl._encoder
    value = [{"metadata": ["small"]}] * 2000
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            value[0]["metadata"][0] = "한😀\\\"\n" * 90000

    class CheckedEncoder:
        def encode(self, current):
            if isinstance(current, (dict, list, tuple)):
                fits, visited = market._canonical_native_probe(current)
                assert fits and visited <= 1024
            elif isinstance(current, str):
                assert 2 + 6 * str.__len__(current) <= market.CANONICAL_CHUNK_BYTES
            return original().encode(current)

    monkeypatch.setattr(market.dl, "_encoder", CheckedEncoder)
    stream = market.canonical_chunks(value, checkpoint=checkpoint)
    for index, block in enumerate(stream):
        assert len(block) <= market.CANONICAL_CHUNK_BYTES
        if calls >= 2 and index >= 2:
            break
    stream.close()
    assert calls >= 2


def test_canonical_encoder_releases_recursive_closure_without_gc(monkeypatch):
    import gc
    import weakref
    original = market.dl._encoder
    references = []

    def encoder():
        instance = original()
        references.append(weakref.ref(instance))
        return instance

    monkeypatch.setattr(market.dl, "_encoder", encoder)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        list(market.canonical_chunks({"metadata": [{"price": "00100.00"}] * 30}))
        assert references and all(reference() is None for reference in references)
    finally:
        if was_enabled:
            gc.enable()


def build_synthetic_fixture(*, full=False, metadata_padding=0, provenance_density=0):
    """Return (DK mapping, fresh zero-argument canonical line factory).

    Full geometry is exactly 10280 original policy rows, 257 sessions, 56 weeks,
    1169/1167 original-book events, 5158 union chunks and 1002211 raw rows.
    Rows/chunks are produced independently of all research math and never kept
    as a full archive. Dense provenance exercises nested decoded object pressure;
    padding remains available for the separately preserved original profile.
    """
    source = _dk_fixture(full=True, days=None if full else SMALL_DAYS, metadata_padding=metadata_padding)
    reference = source["reference_sessions"]
    # Eighteen policy differences create the original union cardinality without
    # changing either 20-member target grid or any original selected coordinate.
    if full:
        matrix = source["source_target_matrix"]["D+2"]["days"]
        for index in range(18):
            matrix[index]["targets"][-1]["symbol"] = "KRX:000021"
            row = source["variants"]["D+2"]["days"][index * 20 + 19]
            row["symbol"] = row["membership_target"]["symbol"] = "KRX:000021"
    if provenance_density:
        for policy in market.POLICIES:
            for day_index, membership in enumerate(source["source_target_matrix"][policy]["days"]):
                day = membership["session"]
                membership["retrieved_at"]["invented_receipts"] = [
                    {"request": {"policy": policy, "ordinal": index},
                     "response": {"evidence_level": "assumed", "is_final": None},
                     "lineage": [{"parent": index, "observation_date": day}]}
                    for index in range(provenance_density)]
                metadata = {key: value for key, value in membership.items() if key != "targets"}
                for row in source["variants"][policy]["days"][day_index * 20:(day_index + 1) * 20]:
                    # Decode-like distinct objects on each original row, rather
                    # than a long string or already-shared fixture shortcuts.
                    row["membership_source"] = deepcopy(metadata)
    by_key = {}
    for policy in market.POLICIES:
        rows = source["variants"][policy]["days"]
        # Unknown original masks remain separate from feature missingness.
        for row in rows:
            if row["breakout_exit_policy"] is not None:
                row["breakout_exit_policy"]["touch_diagnostic"] = {"synthetic_prior_diagnostic": True}
            if row["membership_target"]["rank"] >= 10:
                row["arms"]["basic"].update(status="unknown", event=None, label=None)
                row.update(signal_observable=False, outcome_complete=False,
                    exclusion_reasons=["basic:unknown_signal"], breakout_exit_policy=None)
            by_key.setdefault((row["session"], row["symbol"]), []).append(row)
    keys = sorted(by_key)
    day_indices = {day: index for index, day in enumerate(reference)}

    def chunk(index, day, symbol):
        day_index = day_indices[day]
        opening = market._opening(day)
        count = (195 if index < 1559 else 194) if full else 80
        rank = int(symbol[4:])
        cutoff = (21, 6, 4, 12)[day_index % 4]
        raw = []
        for minute_index in range(count):
            minute = opening + minute_index * timedelta(minutes=1)
            # Preserve exact varied price strings, millisecond offsets and delayed retrieval.
            stamp = int(minute.timestamp()) * 1000 + (rank * 37 + (minute_index % 2) * 13) % 1000
            price = 1000000 + day_index * 101 + rank * 137 + minute_index * (1 if rank % 2 else -1)
            row = {"symbol": symbol, "interval": "1m", "open_time_ms": stamp,
                "open": str(price), "high": str(price + 7), "low": str(price - 7),
                "close": str(price + (3 if rank % 2 else -3)),
                "fetched_at": f"2026-10-20T00:{minute_index % 16:02d}:00+00:00"}
            if rank > cutoff and minute_index == 5:
                if rank % 3 == 0:
                    row["open_time_ms"] += count * 60000  # missing causal bucket, future observation retained
                elif rank % 3 == 1:
                    row["open_time_ms"] += 60000  # duplicate even with individually valid OHLC
                else:
                    row["high"] = "0"  # invalid causal price
            raw.append(row)
        return {"session": day, "symbol": symbol, "observation_date": day,
            "available_at": None, "retrieved_at": "2026-10-22T12:00:00+00:00",
            "source": "collector klines", "is_final": None, "evidence_level": "assumed", "rows": raw}

    def lines():
        for index, (day, symbol) in enumerate(keys):
            yield market._canonical(chunk(index, day, symbol)) + b"\n"

    # Bind each original row to the independently generated original line.
    for index, (day, symbol) in enumerate(keys):
        body = chunk(index, day, symbol)
        vintage = hashlib.sha256(market._canonical(body) + b"\n").hexdigest()
        metadata = {key: body[key] for key in market.METADATA}
        offsets = dict(sorted(Counter(str(row["open_time_ms"] % 60000) for row in body["rows"]).items()))
        fetched = sorted({row["fetched_at"] for row in body["rows"] if row["fetched_at"] is not None})
        for row in by_key[day, symbol]:
            row.update(data_vintage=vintage, input_rows=len(body["rows"]), raw_chunk_metadata=deepcopy(metadata),
                source_offset_counts_ms=deepcopy(offsets), source_fetched_at=list(fetched))
            if row["breakout_exit_policy"] is not None:
                row["breakout_exit_policy"]["common"].update(data_vintage=vintage, raw_chunk_metadata=deepcopy(metadata))
    return source, lines


def fixture():
    return build_synthetic_fixture()


def _result(source=None, lines=None):
    if source is None:
        source, lines = fixture()
    return market.build_readiness(source, lines())


def _rebind(source, bodies):
    """Bind intentionally mutated invented chunks without changing source masks."""
    for body in bodies:
        line = market._canonical(body) + b"\n"
        for policy in market.POLICIES:
            for row in source["variants"][policy]["days"]:
                if (row["session"], row["symbol"]) == (body["session"], body["symbol"]):
                    row.update(data_vintage=hashlib.sha256(line).hexdigest(), input_rows=len(body["rows"]),
                        source_offset_counts_ms=dict(sorted(Counter(str(raw["open_time_ms"] % 60000) for raw in body["rows"]).items())),
                        source_fetched_at=sorted({raw["fetched_at"] for raw in body["rows"] if raw["fetched_at"] is not None}))
                    if row["breakout_exit_policy"] is not None:
                        row["breakout_exit_policy"]["common"]["data_vintage"] = row["data_vintage"]
    return lambda: (market._canonical(body) + b"\n" for body in bodies)


def test_registration_and_structural_only_dependency():
    market.validate_spec(json.loads(market.SPECIFICATION_PATH.read_text()))
    text = inspect.getsource(market)
    assert "dl._event(" not in text and "dl.build_diagnostics(" not in text
    assert "price_change" not in text.replace("price_changes", "")
    assert not any(word in text for word in ("sqlite3", "gzip.open", "read_bytes(", "open("))


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(window_minutes=30),
    lambda spec: spec["parameters"].update(minimum_complete_peer_windows=4),
    lambda spec: spec["parameters"]["next_hypothesis"].update(id="another"),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(input_stream_sha256="a" * 64),
])
def test_registration_refuses_before_any_source_read(change):
    spec = json.loads(market.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(market.MarketContextReadinessRefusal, match="specification_scope"):
        market.build_readiness({}, iter(()), specification=spec)


def test_all_original_book_denominators_metadata_atoms_and_no_mutation():
    source, lines = fixture()
    before = deepcopy(source)
    result = _result(source, lines)
    assert source == before
    assert result["source_identity"]["canonical_source_sha256"] == market._digest(source)
    assert result["reference_sessions"] == list(SMALL_DAYS)
    assert result["new_performance_families"] == 0 and not result["peer_returns_calculated"]
    assert result["next_hypothesis"]["status"] == "fixed_for_future_calibration"
    for policy in market.POLICIES:
        variant = result["variants"][policy]
        originals = [row for row in source["variants"][policy]["days"] if row["breakout_exit_policy"] is not None]
        assert variant["population"]["targets"] == 80
        assert variant["population"]["selected_events"] == len(variant["events"]) == len(originals)
        assert variant["population"]["basic_status_counts"]["unknown"] == 44
        assert variant["source_rows_sha256"] == market._digest(source["variants"][policy]["days"])
        for event, row in zip(variant["events"], originals, strict=True):
            assert len(event["peers"]) == 19
            assert row["symbol"] not in {peer["symbol"] for peer in event["peers"]}
            assert event["source_row_sha256"] == market._digest(row)
            for key in ("data_vintage", "raw_chunk_metadata", "source_fetched_at", "membership_target", "membership_source"):
                assert event[key] == row[key]
            for peer in event["peers"]:
                assert len(peer["observation_offsets_ms"]) == 15
                assert peer["valid_minute_buckets"] + peer["unknown_minute_buckets"] == 15
                assert sum(peer["gap_counts"].values()) == len(peer["gaps"])
                assert (peer["first_open"] is not None) == (peer["state"] == "complete")
        assert sum(cell["events"] for cell in variant["summary"]["by_week"].values()) == len(originals)
        assert variant["summary"]["by_session"][SMALL_DAYS[2]]["feature_unknown_events"] > 0
        assert variant["summary"]["overall"]["full_coverage_events"] > 0


def _mutable_ids(value):
    identities = set()
    if isinstance(value, (dict, list)):
        identities.add(id(value))
        for child in value.values() if isinstance(value, dict) else value:
            identities.update(_mutable_ids(child))
    return identities


def test_dense_provenance_snapshots_are_bounded_and_source_isolated():
    source, lines = build_synthetic_fixture(provenance_density=6)
    source_before = market._digest(source)
    result = _result(source, lines)
    # Captured from the pre-repair DO pure builder on this invented dense
    # fixture; this covers the complete logical wire, not just summaries.
    assert hashlib.sha256(market._canonical(result)).hexdigest() == "b9030eb1c79ec6c348fbc6940a26856eae71dfdb0176a575b7b6445672bfbe47"
    assert market._digest(source) == source_before
    assert not (_mutable_ids(source) & _mutable_ids(result))
    for policy in market.POLICIES:
        matrix = {item["session"]: item for item in source["source_target_matrix"][policy]["days"]}
        events = result["variants"][policy]["events"]
        for day in SMALL_DAYS:
            selected = [event for event in events if event["session"] == day]
            if not selected:
                continue
            metadata = {key: value for key, value in matrix[day].items() if key != "targets"}
            references = [event["membership_source"] for event in selected]
            references.extend(peer["membership_source"] for event in selected for peer in event["peers"])
            assert all(value == metadata for value in references)
            assert len({id(value) for value in references}) == 1
            for target in matrix[day]["targets"]:
                members = [peer["member"] for event in selected for peer in event["peers"]
                    if peer["symbol"] == target["symbol"]]
                members.extend(event["membership_target"] for event in selected if event["symbol"] == target["symbol"])
                assert all(value == target for value in members)
                assert len({id(value) for value in members}) == 1
    output_before = market._digest(result)
    original = source["source_target_matrix"]["D+1"]["days"][0]
    original["retrieved_at"]["invented_receipts"][0]["lineage"][0]["parent"] = "source_only_mutation"
    original["targets"][0]["state"] = "source_only_mutation"
    assert market._digest(result) == output_before


def test_window_reuse_is_chunk_bounded_and_keeps_policy_membership(monkeypatch):
    source, lines = build_synthetic_fixture(provenance_density=2)
    calls = []
    original = market._window

    def record(event, buckets):
        symbol = next(iter(buckets.values()))[0]["symbol"]
        calls.append((event["session"], symbol, event["window_start"], event["window_end"]))
        return original(event, buckets)

    monkeypatch.setattr(market, "_window", record)
    result = _result(source, lines)
    peers = [peer for variant in result["variants"].values() for event in variant["events"] for peer in event["peers"]]
    assert len(calls) == len(set(calls)) < len(peers)
    by_policy = [result["variants"][policy]["events"][0] for policy in market.POLICIES]
    first = [event["peers"][0] for event in by_policy]
    assert first[0]["gaps"] is first[1]["gaps"]
    assert first[0]["observation_offsets_ms"] is first[1]["observation_offsets_ms"]
    assert first[0]["membership_source"] is not first[1]["membership_source"]
    for policy, peer in zip(market.POLICIES, first, strict=True):
        assert peer["membership_source"]["retrieved_at"]["invented_receipts"][0]["request"]["policy"] == policy


def test_reused_raw_atoms_keep_different_original_window_ends_separate():
    source, lines = fixture()
    row = source["variants"]["D+1"]["days"][0]
    event, label = row["arms"]["basic"]["event"], row["arms"]["basic"]["label"]
    common, timed = row["breakout_exit_policy"]["common"], row["breakout_exit_policy"]["time_only"]
    for mapping, keys in ((event, ("decision_at", "available_at", "signal_bar_start", "signal_bar_end")),
            (label, ("entry_at", "exit_at", "time_cap_decided_at", "entry_price_available_at", "label_available_at")),
            (common, ("decision_at", "entry_at", "entry_available_at", "time_cap_at", "time_cap_decided_at")),
            (timed, ("exit_at", "label_available_at"))):
        for key in keys:
            mapping[key] = (market.dl._instant(mapping[key]) + timedelta(minutes=1)).isoformat()
    result = _result(source, lines)
    changed, unchanged = (result["variants"][policy]["events"][0] for policy in market.POLICIES)
    shifted, original = changed["peers"][0], unchanged["peers"][0]
    assert shifted["symbol"] == original["symbol"]
    assert shifted["window_end"] != original["window_end"]
    assert shifted["observation_offsets_ms"] is not original["observation_offsets_ms"]
    bodies = (json.loads(line) for line in lines())
    raw = next(body["rows"] for body in bodies
        if body["session"] == SMALL_DAYS[0] and body["symbol"] == shifted["symbol"])
    assert (shifted["first_open"], shifted["last_close"]) == (raw[6]["open"], raw[20]["close"])
    assert (original["first_open"], original["last_close"]) == (raw[5]["open"], raw[19]["close"])


def test_exact_window_excludes_F_and_all_future_prices_or_gaps():
    source, lines = fixture()
    baseline = _result(source, lines)
    bodies = [json.loads(line) for line in lines()]
    for body in bodies:
        cutoff = market._opening(body["session"]) + 20 * timedelta(minutes=1)
        for raw in body["rows"]:
            if raw["open_time_ms"] >= int(cutoff.timestamp()) * 1000:
                raw.update(open="NaN", high="0", low="0", close="0")
    changed = _result(source, _rebind(source, bodies))
    for policy in market.POLICIES:
        assert changed["variants"][policy]["summary"] == baseline["variants"][policy]["summary"]
        for old, new in zip(baseline["variants"][policy]["events"], changed["variants"][policy]["events"], strict=True):
            assert old["complete_peer_windows"] == new["complete_peer_windows"]
            assert [(p["first_open"], p["last_close"], p["gaps"]) for p in old["peers"]] == [(p["first_open"], p["last_close"], p["gaps"]) for p in new["peers"]]


def test_equal_duplicate_bucket_is_unknown_and_gap_causes_stay_distinct():
    source, lines = fixture()
    bodies = [json.loads(line) for line in lines()]
    first = bodies[1]
    first["rows"].append(deepcopy(first["rows"][5]))
    result = _result(source, _rebind(source, bodies))
    peer = result["variants"]["D+1"]["events"][0]["peers"][0]
    assert peer["state"] == "unknown" and peer["gap_counts"]["conflict"] == 1
    assert len(peer["observation_offsets_ms"][0]) == 2
    assert peer["first_open"] is peer["last_close"] is None
    assert all(result["variants"]["D+1"]["summary"]["overall"]["gap_counts"][key] > 0 for key in market.GAP_STATES)


def test_fixed_quorum_exact_five_and_four_and_not_ready_no_retuning():
    source, lines = fixture()
    result = _result(source, lines)
    cases = result["variants"]["D+1"]["events"]
    five = next(event for event in cases if event["session"] == SMALL_DAYS[1])
    four = next(event for event in cases if event["session"] == SMALL_DAYS[2] and event["symbol"] == "KRX:000005")
    assert five["complete_peer_windows"] == 5 and five["quorum_usable"]
    assert four["complete_peer_windows"] == 4 and not four["quorum_usable"]
    bodies = [json.loads(line) for line in lines()]
    for body in bodies:
        body["rows"][5]["open"] = "0"
    unavailable = _result(source, _rebind(source, bodies))
    assert unavailable["next_hypothesis"]["status"] == "not_data_ready"
    assert all(value == 0 for value in unavailable["next_hypothesis"]["basis"]["computable_events_by_policy"].values())
    assert unavailable["variants"]["D+1"]["population"] == result["variants"]["D+1"]["population"]


@pytest.mark.parametrize("mutation", ["vintage", "metadata", "offsets", "retrieval", "count", "membership", "decision", "mask"])
def test_original_nonbook_lineage_and_clocks_cannot_change(mutation):
    source, lines = fixture()
    row = source["variants"]["D+1"]["days"][-1]
    if mutation == "vintage": row["data_vintage"] = "a" * 64
    if mutation == "metadata": row["raw_chunk_metadata"]["is_final"] = True
    if mutation == "offsets": row["source_offset_counts_ms"] = {"0": 80}
    if mutation == "retrieval": row["source_fetched_at"] = []
    if mutation == "count": row["input_rows"] += 1
    if mutation == "membership": source["source_target_matrix"]["D+1"]["days"][0]["available_at"] = SMALL_DAYS[0] + "T09:00:00+09:00"
    if mutation == "decision": source["variants"]["D+1"]["days"][0]["arms"]["basic"]["event"]["decision_at"] = SMALL_DAYS[0] + "T09:23:00+09:00"
    if mutation == "mask": row["outcome_complete"] = True
    with pytest.raises(market.MarketContextReadinessRefusal):
        _result(source, lines)


@pytest.mark.parametrize("mutation", ["extra", "missing", "order", "framing", "timestamp", "symbol"])
def test_archive_scope_order_and_original_framing_are_enforced(mutation):
    source, lines = fixture()
    raw = list(lines())
    if mutation == "extra": raw.append(raw[-1])
    if mutation == "missing": raw.pop()
    if mutation == "order": raw[0], raw[1] = raw[1], raw[0]
    if mutation == "framing": raw[0] = b" " + raw[0]
    if mutation in {"timestamp", "symbol"}:
        body = json.loads(raw[0])
        body["rows"][0]["open_time_ms" if mutation == "timestamp" else "symbol"] = "wrong"
        raw[0] = market._canonical(body) + b"\n"
    with pytest.raises(market.MarketContextReadinessRefusal):
        market.build_readiness(source, iter(raw))


def test_does_not_evaluate_any_original_outcome_prices_or_statistics():
    source, lines = fixture()
    for policy in market.POLICIES:
        source["variants"][policy]["comparison"] = {"opaque_prior_statistic": "never_recalculated"}
        for row in source["variants"][policy]["days"]:
            if row["breakout_exit_policy"] is not None:
                row["arms"]["basic"]["label"]["price_change"] = {"opaque_future_label": True}
                row["arms"]["basic"]["label"]["entry_open"] = "not_evaluated"
                row["breakout_exit_policy"]["time_only"].update(price_change={"opaque_future_outcome": True},
                    entry_open="not_evaluated", exit_open="not_evaluated")
    result = _result(source, lines)
    assert result["next_hypothesis"]["status"] == "fixed_for_future_calibration"


def test_checkpoint_and_empty_reference_weeks_retained():
    source, lines = fixture()
    for policy in market.POLICIES:
        for row in source["variants"][policy]["days"]:
            if row["session"] == SMALL_DAYS[-1] and row["breakout_exit_policy"] is not None:
                row["arms"]["basic"].update(status="known_no_signal", event=None, label=None)
                row["breakout_exit_policy"] = None
    calls = []
    result = market.build_readiness(source, lines(), checkpoint=lambda: calls.append(1))
    assert len(calls) > 100
    assert all(set(result["variants"][policy]["summary"]["by_week"]) == set(result["reference_weeks"]) for policy in market.POLICIES)
    assert result["variants"]["D+1"]["summary"]["by_week"][market.dl._week(SMALL_DAYS[-1])]["events"] == 0


@pytest.mark.parametrize("price", ["NaN", "sNaN", "Infinity", "-Infinity", "0", "-1", "nonsense"])
def test_nonfinite_or_nonpositive_causal_prices_are_unknown_not_zero(price):
    source, lines = fixture()
    bodies = [json.loads(line) for line in lines()]
    bodies[1]["rows"][5]["open"] = price
    result = _result(source, _rebind(source, bodies))
    peer = result["variants"]["D+1"]["events"][0]["peers"][0]
    assert peer["gap_counts"]["invalid_price"] == 1
    assert peer["first_open"] is peer["last_close"] is None


def test_huge_finite_exact_prices_need_no_division_or_decimal_precision():
    source, lines = fixture()
    bodies = [json.loads(line) for line in lines()]
    for row in bodies[1]["rows"]:
        row.update(open="1E+999999", high="2E+999999", low="1E+999998", close="1.5E+999999")
    result = _result(source, _rebind(source, bodies))
    peer = result["variants"]["D+1"]["events"][0]["peers"][0]
    assert peer["state"] == "complete" and peer["first_open"] == "1E+999999"


def test_huge_timestamp_and_extra_null_line_refuse_with_stable_codes():
    source, lines = fixture()
    raw = list(lines())
    body = json.loads(raw[0])
    body["rows"][0]["open_time_ms"] = 10 ** 100
    raw[0] = market._canonical(body) + b"\n"
    with pytest.raises(market.MarketContextReadinessRefusal, match="malformed_or_mismatched_source"):
        market.build_readiness(source, raw)
    with pytest.raises(market.MarketContextReadinessRefusal, match="extra_input_chunk"):
        market.build_readiness(source, [*lines(), None])
