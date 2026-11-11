"""Invented input-only EL support checks; no actual archive access."""

from dataclasses import replace
import json

import pytest

from research import intraday_ed_sources as source
from research import intraday_event_support as census
from research import intraday_event_support_reference as reference
from research.intraday_ed_inputs import MINUTE
from research.intraday_pattern_calibration import session_open, session_close


DAY = "2026-01-05"
SYMBOL = "KRX:000001"


def chunks(day=DAY, symbol=SYMBOL, minutes=30):
    base = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
            "retrieved_at": "2026-10-01T00:00:00+09:00", "source": "collector klines",
            "is_final": None, "evidence_level": "assumed"}
    price = {**base, "rows": []}
    volume = {**base, "rows": [], "daily_rows": [], "daily_retrieved_at": base["retrieved_at"]}
    for index in range(minutes):
        stamp = int((session_open(day) + index * MINUTE).timestamp()) * 1000
        price["rows"].append({"symbol": symbol, "interval": "1m", "open_time_ms": stamp,
                              "open": "100", "high": "102", "low": "99", "close": "101", "fetched_at": None})
        volume["rows"].append({"open_time_ms": stamp, "volume": "1", "quote_volume": None, "fetched_at": None})
    return price, volume


def compare(price, volume, *, length=None, late=None):
    session = source.session_inputs(price["symbol"], price["session"], price, volume).session
    if length is not None:
        session = replace(session, closing=session.opening + length * MINUTE)
    if late is not None:
        minute = session.minutes[session.opening + late * MINUTE]
        session = replace(session, minutes={**session.minutes, minute.start_at:
                          replace(minute, available_at=minute.available_at + MINUTE)})
    result = census.session_support(session, price, volume)
    assert result == reference.session_support(session, price, volume)
    assert census.source_metadata(price) == reference.source_metadata(price)
    return result


def test_missing_boundary_and_continuity():
    price, volume = chunks(minutes=30)
    del price["rows"][4]
    result = compare(price, volume, length=30)
    assert result["price"] == {"observed": 29, "missing": 1, "invalid": 0, "collision": 0}
    assert result["price_prefix_minutes"] == 4
    assert result["longest_continuous_price_minutes"] == 25
    assert result["bars"]["5"]["price_complete"] == 5
    assert result["bars"]["15"]["price_complete"] == 1
    assert result["decision_masks"][0]["price5"] is False
    assert result["decision_masks"][1]["price5"] is True
    assert result["decision_masks"][1]["price_prefix"] is False


def test_collision_invalid_zero_and_volume_missing_are_distinct():
    price, volume = chunks(minutes=15)
    price["rows"].append(dict(price["rows"][2]))
    price["rows"][3]["low"] = "invalid"
    volume["rows"][0]["volume"] = "0"
    del volume["rows"][1]
    volume["rows"][1]["volume"] = "-1"
    result = compare(price, volume, length=15)
    assert result["price"]["collision"] == result["price"]["invalid"] == 1
    assert result["volume"] == {"positive": 12, "known_zero": 1, "missing": 1, "invalid": 1, "collision": 0}
    assert result["volume_supported"] == 13


def test_volume_collision_does_not_erase_price_support():
    price, volume = chunks(minutes=15)
    volume["rows"].append(dict(volume["rows"][0]))
    result = compare(price, volume, length=15)
    assert result["volume"]["collision"] == 1
    assert result["complete_price_session"]
    assert not result["complete_volume_session"]


def test_late_minute_never_repairs_an_earlier_deadline():
    price, volume = chunks(minutes=15)
    result = compare(price, volume, length=15, late=0)
    assert result["price"]["observed"] == 15
    assert result["price_late"] == result["volume_late"] == 1
    assert result["price_supported"] == 14
    assert not result["decision_masks"][0]["price5"]
    assert not result["decision_masks"][1]["price_prefix"]


def test_partial_final_bar_and_first60_obligation():
    result = compare(*chunks(minutes=13), length=13)
    assert result["complete_price_session"]
    assert result["bars"] == {"5": {"expected": 2, "price_complete": 2, "volume_complete": 2},
                              "15": {"expected": 0, "price_complete": 0, "volume_complete": 0}}
    assert not result["first60_price_complete"]
    assert [mask["end_offset"] for mask in result["decision_masks"]] == [5, 10]


def test_nonstandard_session_uses_its_own_denominator():
    price, volume = chunks(day="2026-01-02", minutes=330)
    result = compare(price, volume)
    assert result["expected_minutes"] == 330
    assert result["complete_price_session"]
    assert result["bars"]["5"]["expected"] == 66
    assert len(result["decision_masks"]) == 65


class Reader:
    def __init__(self, values):
        self.values = values

    def load_raw_chunk(self, kind, symbol, day):
        return self.values[kind, symbol, day]

    def assert_unchanged(self):
        pass


def fixture():
    days = ("2026-01-02", "2026-01-05")
    targets, values = [], {}
    for day_index, day in enumerate(days):
        for rank in range(1, 21):
            symbol = f"KRX:{rank + (1 if day_index else 0):06}"
            count = (session_close(day) - session_open(day)) // MINUTE if rank == 1 else 0
            for kind, chunk in zip(("CC", "CX"), chunks(day, symbol, count), strict=True):
                values[kind, symbol, day] = chunk
            for scenario in ("S1", "S2"):
                targets.append(source.TargetCoordinate(scenario, symbol, day, rank, count or None,
                    "stored_rows_completeness_unverified" if count else "no_stored_rows_unknown_reason", b"{}"))
    return source.UniverseCalendar(days, tuple(targets), "a" * 64), Reader(values)


def test_full_population_previous_coordinate_and_independent_audit(tmp_path):
    universe, reader = fixture()
    result = census.build_support(tmp_path, universe, reader)
    assert result == census.build_support(tmp_path, universe, reader, audit=True)
    assert result["counts"]["union"]["coordinates"] == 40
    assert result["counts"]["S1"]["coordinates"] == result["counts"]["S2"]["coordinates"] == 40
    assert result["counts"]["union"]["expected_minutes"] == 20 * (330 + 390)
    assert result["counts"]["union"]["price_supported"] == 330 + 390
    rows = list(map(json.loads, (tmp_path / census.OUTPUT).read_bytes().splitlines()))
    new = next(row for row in rows if row.get("symbol") == "KRX:000021")
    assert new["previous_session"] == {"session": "2026-01-02", "in_original_union": False,
        "complete_price_session": None, "economic_price_basis": "unknown"}
    present = next(row for row in rows if row.get("symbol") == "KRX:000002" and row.get("session") == "2026-01-05")
    assert present["previous_session"]["in_original_union"] is True
    assert present["previous_session"]["complete_price_session"] is False
    assert "open" not in new["support"]


@pytest.mark.parametrize("filename", [census.OUTPUT, census.SUMMARY])
def test_tampered_saved_artifact_is_refused(tmp_path, filename):
    universe, reader = fixture()
    census.build_support(tmp_path, universe, reader)
    with (tmp_path / filename).open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(ValueError, match="EOF|differs"):
        census.build_support(tmp_path, universe, reader, audit=True)


def test_population_cannot_drop_a_target(tmp_path):
    universe, reader = fixture()
    with pytest.raises(ValueError, match="population"):
        census.build_support(tmp_path, replace(universe, targets=universe.targets[:-1]), reader)


def test_reference_rejects_a_producer_count_defect(tmp_path, monkeypatch):
    universe, reader = fixture()
    original = census.session_support

    def wrong(*args):
        result = original(*args)
        result["longest_continuous_price_minutes"] += 1
        return result

    monkeypatch.setattr(census, "session_support", wrong)
    census.build_support(tmp_path, universe, reader)
    with pytest.raises(ValueError, match="independent"):
        census.build_support(tmp_path, universe, reader, audit=True)


def test_reference_rejects_a_producer_summary_defect(tmp_path, monkeypatch):
    universe, reader = fixture()
    original = census.compact_support

    def wrong(value):
        result = original(value)
        result["totals"]["decision_frames"] += 1
        return result

    monkeypatch.setattr(census, "compact_support", wrong)
    census.build_support(tmp_path, universe, reader)
    with pytest.raises(ValueError, match="independent"):
        census.build_support(tmp_path, universe, reader, audit=True)


def test_existing_previous_price_still_has_unknown_economic_basis(tmp_path):
    universe, reader = fixture()
    day, symbol = universe.sessions[0], "KRX:000002"
    for kind, chunk in zip(("CC", "CX"), chunks(day, symbol, 330), strict=True):
        reader.values[kind, symbol, day] = chunk
    census.build_support(tmp_path, universe, reader)
    rows = list(map(json.loads, (tmp_path / census.OUTPUT).read_bytes().splitlines()))
    current = next(row for row in rows if row.get("session") == universe.sessions[1] and row.get("symbol") == symbol)
    assert current["previous_session"]["complete_price_session"] is True
    assert current["previous_session"]["economic_price_basis"] == "unknown"


def test_later_price_change_cannot_change_earlier_support_mask():
    price, volume = chunks(minutes=30)
    earlier = compare(price, volume, length=30)["decision_masks"][:2]
    price["rows"][20]["high"] = "invalid"
    assert compare(price, volume, length=30)["decision_masks"][:2] == earlier
