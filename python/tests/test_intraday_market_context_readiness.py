"""Invented DK wire and streamed stock chunks; no market IO or old replay."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import hashlib
import inspect
import json

import pytest

from research import intraday_market_context_readiness as market
from test_intraday_breakout_loss_concentration import build_synthetic_fixture as _dk_fixture


SMALL_DAYS = ("2025-09-04", "2025-09-11", "2025-11-13", "2026-01-02")


def build_synthetic_fixture(*, full=False, metadata_padding=0):
    """Return (DK mapping, fresh zero-argument canonical line factory).

    Full geometry is exactly 10280 original policy rows, 257 sessions, 56 weeks,
    1169/1167 original-book events, 5158 union chunks and 1002211 raw rows.
    Rows/chunks are produced independently of all research math and never kept
    as a full archive. Padding exercises 45-64 MiB source decoding/hashing.
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
