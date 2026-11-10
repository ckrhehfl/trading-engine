"""Small invented original rosters exercise EK sealing and independent replay."""
from copy import deepcopy
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research import intraday_recovery_snapshot as snapshot
from research.intraday_ed_inputs import MINUTE
from research.intraday_pattern_calibration import session_open


DAY = "2026-01-05"
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(1, 21))
BINDING = {"source_sha256": "a" * 64, "invented": True}


def chunk(kind, symbol):
    index = int(symbol[-6:])
    opening = session_open(DAY)
    rows = []
    for offset in range(124):
        if index == 4 or (index == 2 and offset == 0) or (index == 3 and offset == 50):
            continue
        if index == 20 and offset == 22:
            continue
        limit = 10060 if index == 1 else 10000 - index * 60
        if offset < 60:
            opened = 10000 if offset == 0 else (10000 + offset if index == 1 else 10000 - index * offset)
            close = opened + 1 if index == 1 else opened - index
        else:
            opened = close = limit - 1 if offset == 63 else limit + 10
        row = {"open_time_ms": int((opening + offset * MINUTE).timestamp()) * 1000,
               "fetched_at": "2026-10-30T00:00:00Z"}
        if kind == "CC":
            row.update(symbol=symbol, interval="1m", open=str(opened), high=str(max(opened, close) + 2),
                       low=str(min(opened, close) - 2), close=str(close))
        else:
            row.update(volume="0" if index == 5 else "10", quote_volume=None)
        rows.append(row)
    result = {"symbol": symbol, "session": DAY, "observation_date": DAY, "available_at": None,
              "retrieved_at": "2026-10-30T00:00:00Z", "source": "collector klines", "is_final": None,
              "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        result.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return result


class Reader:
    def __init__(self):
        self.chunks = {(kind, symbol, DAY): chunk(kind, symbol) for kind in ("CC", "CX") for symbol in SYMBOLS}
        self.lookups, self.checked = [], 0

    def load_raw_chunk(self, kind, symbol, day):
        self.lookups.append((kind, symbol, day))
        return deepcopy(self.chunks[kind, symbol, day])

    def assert_unchanged(self):
        self.checked += 1


def universe(reader):
    targets = []
    for scenario in ("S1", "S2"):
        for rank, symbol in enumerate(SYMBOLS, 1):
            count = len(reader.chunks["CC", symbol, DAY]["rows"])
            targets.append(sources.TargetCoordinate(scenario, symbol, DAY, rank, count or None,
                "stored_rows_completeness_unverified" if count else "no_stored_rows_unknown_reason",
                wire.encode({"invented_original_rank": rank})))
    return sources.UniverseCalendar((DAY,), tuple(targets), "b" * 64)


def test_original_opportunities_seal_before_outcomes_and_independent_roundtrip(tmp_path, monkeypatch):
    reader = Reader()
    original = universe(reader)
    real_session = snapshot._session
    phases = []

    def guarded_session(raw_reader, symbol, day, end, audit):
        if end > 60:
            seal = json.loads((tmp_path / "feature-seal.json").read_bytes())
            assert seal["features"]["rows"] == 20
            assert len(list(snapshot._rows(tmp_path / snapshot.FEATURES))) == 20
            assert wire.file_digest(tmp_path / snapshot.FEATURES, 512 * wire.MIB, lambda _: None) == (
                seal["features"]["sha256"], seal["features"]["size"])
            assert phases.count((audit, 60)) == 20
        phases.append((audit, end))
        return real_session(raw_reader, symbol, day, end, audit)

    monkeypatch.setattr(snapshot, "_session", guarded_session)
    receipt = snapshot.build_snapshot(tmp_path, original, reader, binding=BINDING)
    assert receipt["header"]["scenario_coordinates"] == {"S1": 20, "S2": 20}
    assert receipt["features"]["rows"] == receipt["labels"]["rows"] == 20
    records = {row["symbol"]: row for row in snapshot._rows(tmp_path / snapshot.FEATURES)}
    outcomes = {row["symbol"]: row for row in snapshot._rows(tmp_path / snapshot.LABELS)}
    held = {row["symbol"]: row for row in snapshot._rows(tmp_path / snapshot.HOLDING_FEATURES)}
    assert set(records) == set(outcomes) == set(held) == set(SYMBOLS)
    positive = records[SYMBOLS[0]]["own"]
    assert positive["candidate_state"] == "own_nonnegative" and positive["chart_state"] == "available"
    assert positive["limit"] is not None and positive["stop"] is not None and positive["static"]
    assert outcomes[SYMBOLS[0]]["outcome"]["baseline"] is not None
    incomplete = records[SYMBOLS[-1]]
    assert incomplete["own"]["candidate_state"] == "own_negative_chart_unsupported"
    assert incomplete["own"]["order_state"] == "ready" and incomplete["own"]["minute"] is None
    assert all(value["rank_control"]["rank_state"] == "select" for value in incomplete["scenarios"].values())
    assert outcomes[SYMBOLS[-1]]["outcome"]["baseline"] is not None
    absent = records[SYMBOLS[3]]
    assert absent["own"]["candidate_state"] == "endpoints_unknown"
    assert all(target["state"] == "no_stored_rows_unknown_reason" for target in absent["targets"])
    assert outcomes[SYMBOLS[3]]["outcome"]["baseline"] is None
    assert "holding_queries" not in positive
    assert [row["end_offset"] for row in outcomes[SYMBOLS[0]]["holding_queries"]] == [75, 90, 105]
    for query, label in zip(held[SYMBOLS[0]]["holding_queries"], outcomes[SYMBOLS[0]]["holding_queries"], strict=True):
        assert set(query["peer"]) == {"S1", "S2"}
        assert set(label) == {"end_offset", "remaining_gross_pct"}
        assert query["end_offset"] == label["end_offset"]
        assert "remaining_gross_pct" not in query and "remaining_gross_pct" not in query["features"]
        assert query["features"]["static"][-1] == (120 - query["end_offset"]) / 60
    assert snapshot.build_snapshot(tmp_path, original, reader, binding=BINDING, audit=True) == receipt
    assert reader.checked == 2

    path = tmp_path / snapshot.FEATURES
    values = list(snapshot._rows(path))
    values[0]["own"]["static"][0] += 1
    path.write_bytes(b"".join(wire.encode(row) for row in values))
    with pytest.raises(ValueError, match="independent snapshot differs"):
        snapshot.build_snapshot(tmp_path, original, reader, binding=BINDING, audit=True)
    before = len(reader.lookups)
    with pytest.raises(ValueError, match="feature seal changed"):
        list(snapshot._label_rows(tmp_path, reader, receipt["features"], False))
    assert len(reader.lookups) == before


def test_future_prices_cannot_change_initial_features_or_original_membership():
    reader = Reader()
    original = universe(reader)
    expected = list(snapshot._feature_rows(original, reader, False))
    boundary = int((session_open(DAY) + 60 * MINUTE).timestamp()) * 1000
    for (kind, _, _), value in reader.chunks.items():
        for row in value["rows"]:
            if row["open_time_ms"] >= boundary:
                for field in (("open", "high", "low", "close") if kind == "CC" else ("volume",)):
                    row[field] = "future-invalid-invented-value"
    assert list(snapshot._feature_rows(original, reader, False)) == expected
    assert list(snapshot._feature_rows(original, reader, True)) == expected


def test_holding_tape_roundtrips_finite_decimal_without_context_rounding():
    price = "100.000000000000000000000000001"
    restored = snapshot.holding_session({"session": DAY,"symbol": SYMBOLS[0],"holding_prices": [[63,[price]*4]]})
    quote, = restored.minutes.values()
    assert tuple(map(str,quote.ohlc)) == (price,)*4


@pytest.mark.parametrize("changed", [b'{"value":1}', b'{ "value":1}\n', b'{"value":2}\n',
                                    b'{"value":1}\n{"extra":true}\n'])
def test_bounded_canonical_snapshot_rejects_truncation_change_or_extra_rows(tmp_path, changed):
    path = tmp_path / "invented.jsonl"
    snapshot._write_or_check(path, iter([{"value": 1}]), audit=False)
    path.write_bytes(changed)
    with pytest.raises(ValueError):
        snapshot._write_or_check(path, iter([{"value": 1}]), audit=True)


def test_duplicate_original_membership_fails_before_any_raw_lookup(tmp_path):
    reader = Reader()
    good = universe(reader)
    malformed = sources.UniverseCalendar(good.sessions, (*good.targets, good.targets[0]), good.raw_sha256)
    with pytest.raises(sources.SourceRefusal, match="duplicate_original_target"):
        snapshot.build_snapshot(tmp_path, malformed, reader, binding=BINDING)
    assert not reader.lookups and not list(tmp_path.iterdir())
