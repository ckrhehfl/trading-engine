"""Invented original-source-shaped days only; no actual archive or runner."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_campaign_features as features


eb, ea, cc = features.eb, features.ea, features.cc
DAY = "2025-09-04"
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(8))


def fixture(peers=7):
    symbols = SYMBOLS[:peers + 1]
    opening = cc.session_open(DAY)
    chunks, sessions, sources = {}, {}, {}
    for index, symbol in enumerate(symbols):
        chunk = {
            "session": DAY, "symbol": symbol, "observation_date": DAY,
            "available_at": None, "retrieved_at": "2026-10-20T00:00:00+00:00",
            "source": "collector klines", "is_final": None, "evidence_level": "assumed",
            "rows": [{"symbol": symbol, "interval": "1m",
                "open_time_ms": int((opening + minute * cc.MINUTE).timestamp()) * 1000,
                "open": "100", "high": str(102 + index), "low": "99",
                "close": str(101 + index), "fetched_at": "2026-10-20T00:00:00+00:00"}
                for minute in range(90)],
        }
        sessions[symbol], sources[symbol] = eb._read_price(chunk, DAY, symbol)
        chunks[symbol] = chunk
    target = ea.Target(symbols[0], date.fromisoformat(DAY), "D+1", opening,
        cc.session_close(DAY), eb.PRICE_BASIS, eb._price_source(sources[symbols[0]]))
    observed = datetime(2025, 8, 31, 23, 59, tzinfo=cc.KST)
    fixed = datetime(2025, 9, 1, 8, tzinfo=cc.KST)
    universe = ea.MonthlyUniverse(date(2025, 9, 1), symbols, fixed,
        ea.Provenance(observed, fixed, fixed, "invented monthly source", True,
                      "invented-month-v1", "synthetic"))
    return {"target": target, "universe": universe, "sessions": sessions,
            "sources": sources, "setup_id": "EA-A", "observed_end": opening + 60 * cc.MINUTE,
            "decision_at": opening + 62 * cc.MINUTE, "structural_stop": Fraction(197, 2),
            "inputs_authenticated": True, "authentication_reference": "invented-full-original-day-v1"}


def build(data):
    return features.prepare_features(**data)


def lose(data, symbol, minute, reason="missing"):
    session = data["sessions"][symbol]
    at = session.opening + minute * cc.MINUTE
    minutes = dict(session.minutes)
    minutes[at] = None
    data["sessions"][symbol] = replace(session, minutes=minutes,
        gaps=({"start": at, "end": at + cc.MINUTE, "state": reason},))


def test_exact_values_provenance_and_input_immutability():
    data = fixture(5)
    original = deepcopy(data)
    result = build(data)
    assert data == original
    assert tuple(result.features) == features.FEATURE_NAMES
    assert result.proxy_id == "EC-common-observed-peer-min5"
    assert result.fixed_peers == result.common_peers == SYMBOLS[1:6]
    assert result.target.symbol not in result.fixed_peers
    assert result.features["setup_is_A"].value == 1
    assert result.features["fraction_of_session_elapsed"].value == Fraction(62, 390)
    assert result.features["target_open_to_signal_return"].value == Fraction(1, 100)
    assert result.features["target_last15_return"].value == Fraction(1, 100)
    assert result.features["structural_stop_gap_from_signal_close"].value == Fraction(5, 202)
    assert result.features["trigger_close_location"].value == Fraction(2, 3)
    assert result.features["common_peer_count_divided_by_original_peer_count"].value == 1
    for name in features.WINDOWS:
        feature = result.features[f"peer_{name}_return"]
        assert feature.value == Fraction(1, 25)
        assert len(feature.provenance) == 5
        for meta in feature.provenance:
            assert meta.available_at == meta.observation_end + 2 * cc.MINUTE
            assert meta.available_at <= result.decision_at
            assert meta.retrieved_at > result.decision_at
            assert meta.source == "collector klines"
            assert meta.is_final is None and meta.evidence_level == "assumed"
    assert not result.historical_timing_certified and not result.official_index
    assert not result.actual_execution_authorized
    assert result.source_evidence[SYMBOLS[0]]["raw_chunk_metadata"]["available_at"] is None
    with pytest.raises(TypeError):
        result.features["setup_is_A"] = ea.Feature(Fraction(0))
    with pytest.raises(TypeError):
        result.source_evidence[SYMBOLS[0]]["raw_chunk_metadata"]["source"] = "changed"
    with pytest.raises(FrozenInstanceError):
        result.common_peers = ()


def test_four_common_peers_unknown_five_defined_without_erasing_known_count():
    result = build(fixture(4))
    for name in features.WINDOWS:
        assert result.features[f"peer_{name}_return"].value is None
        assert result.features[f"peer_{name}_return"].reasons == ("insufficient_common_observed_peers",)
    assert result.features["common_peer_count_divided_by_original_peer_count"].value == 1
    assert result.features["target_last15_return"].value == Fraction(1, 100)
    assert build(fixture(5)).features["peer_last15_return"].value is not None


def test_all_three_means_use_same_intersection_not_each_window_observed_set():
    data = fixture()
    lose(data, SYMBOLS[1], 10, "conflict")
    lose(data, SYMBOLS[2], 50, "invalid_price")
    result = build(data)
    assert SYMBOLS[1] in result.observed_peers_by_window["last15"]
    assert SYMBOLS[2] in result.observed_peers_by_window["first30"]
    assert result.common_peers == SYMBOLS[3:]
    assert len(result.common_peers) == 5
    for name in features.WINDOWS:
        assert result.features[f"peer_{name}_return"].value == Fraction(3, 50)
    assert result.features["common_peer_count_divided_by_original_peer_count"].value == Fraction(5, 7)
    issues = {(row.symbol, row.name): row for row in result.windows}
    assert issues[(SYMBOLS[1], "first30")].reasons == ("conflict",)
    assert issues[(SYMBOLS[2], "last15")].reasons == ("invalid_price",)
    assert len(issues[(SYMBOLS[2], "last15")].minute_issues) == 1


def test_separate_unknown_target_features_and_peer_observability():
    data = fixture()
    lose(data, SYMBOLS[0], 10)
    result = build(data)
    assert result.features["target_open_to_signal_return"].value is None
    assert result.features["target_last15_return"].value == Fraction(1, 100)
    assert result.features["trigger_close_location"].value == Fraction(2, 3)
    assert result.features["structural_stop_gap_from_signal_close"].value == Fraction(5, 202)
    assert len(result.common_peers) == 7
    data["sessions"][SYMBOLS[0]] = None
    result = build(data)
    assert result.features["target_last15_return"].reasons == ("session_missing",)
    assert result.features["setup_is_A"].value == 1
    assert result.features["fraction_of_session_elapsed"].value == Fraction(62, 390)
    assert result.features["peer_last15_return"].value == Fraction(1, 20)


def test_missing_future_suffix_and_future_ohlc_changes_do_not_change_features():
    data = fixture()
    original = build(data)
    for symbol, session in data["sessions"].items():
        minutes = {at: value for at, value in session.minutes.items() if at < data["observed_end"]}
        minutes[data["observed_end"] + cc.MINUTE] = (Decimal("999"),) * 4
        data["sessions"][symbol] = replace(session, minutes=minutes, prefix=(), gaps=())
    result = build(data)
    assert result == original


def test_zero_range_unknown_and_true_zero_return_observed():
    data = fixture()
    for symbol, session in data["sessions"].items():
        data["sessions"][symbol] = replace(session,
            minutes={at: (Decimal("100"),) * 4 for at in session.minutes})
    result = build(data)
    assert result.features["trigger_close_location"].value is None
    assert result.features["trigger_close_location"].reasons == ("zero_trigger_range",)
    for name in ("target_last15_return", "target_open_to_signal_return", "peer_first30_return"):
        assert result.features[name].value == Fraction(0)
        assert result.features[name].reasons == ()


def test_invalid_numeric_prices_unknown_but_wrong_types_hard_refused():
    data = fixture()
    session = data["sessions"][SYMBOLS[0]]
    at = session.opening + 50 * cc.MINUTE
    session.minutes[at] = (Decimal("NaN"),) * 4
    assert build(data).features["target_last15_return"].reasons == ("invalid_price",)
    session.minutes[at] = (100, 101, 99, 100)
    with pytest.raises(features.FeaturesRefusal, match="exact_decimal_ohlc_required"):
        build(data)


@pytest.mark.parametrize("change,reason", [
    (lambda d: d.update(inputs_authenticated=False), "caller_authenticated_full_original_day_required"),
    (lambda d: d.update(authentication_reference=" "), "authentication_reference_required"),
    (lambda d: d.update(setup_id="EA-C"), "unregistered_setup"),
    (lambda d: d.update(structural_stop=Decimal("98.5")), "positive_exact_fraction_stop_required"),
    (lambda d: d.update(structural_stop=Fraction(0)), "positive_exact_fraction_stop_required"),
    (lambda d: d.update(price_unit="USD"), "price_unit_or_basis_changed"),
    (lambda d: d.update(observed_end=d["observed_end"].replace(tzinfo=None)), "aware_clock_required"),
    (lambda d: d.update(observed_end=d["observed_end"].astimezone(timezone.utc)), "kst_session_clock_required"),
    (lambda d: d.update(observed_end=d["observed_end"] + cc.MINUTE), "original_signal_or_decision_clock_changed"),
    (lambda d: d.update(decision_at=d["decision_at"] + cc.MINUTE), "original_signal_or_decision_clock_changed"),
    (lambda d: d.update(target=replace(d["target"], price_basis="adjusted")), "price_unit_or_basis_changed"),
    (lambda d: d["sessions"].pop(SYMBOLS[7]), "full_original_day_slots_required"),
    (lambda d: d["sources"][SYMBOLS[1]].update(symbol=SYMBOLS[0]), "source_identity_or_schema_changed"),
    (lambda d: d["sources"][SYMBOLS[1]].update(data_vintage="bad"), "invalid_source_digest"),
    (lambda d: d["sources"][SYMBOLS[1]].update(input_rows=True), "invalid_source_receipt"),
    (lambda d: d["sources"][SYMBOLS[1]]["raw_chunk_metadata"].update(source="official index"), "source_metadata_changed"),
    (lambda d: d["sources"][SYMBOLS[1]]["raw_chunk_metadata"].update(is_final=1), "source_metadata_changed"),
    (lambda d: d["sources"][SYMBOLS[1]]["raw_chunk_metadata"].update(available_at="2025-09-04T11:00:00+09:00"), "source_metadata_changed"),
    (lambda d: d["sources"][SYMBOLS[1]]["raw_chunk_metadata"].update(retrieved_at="2025-09-04T09:59:00+09:00"), "retrieval_precedes_observation"),
    (lambda d: d["sources"][SYMBOLS[1]]["raw_chunk_metadata"].update(retrieved_at="2026-10-20T00:00:00"), "aware_clock_required"),
    (lambda d: d.update(target=replace(d["target"], price_source=replace(d["target"].price_source, data_vintage="changed"))), "target_source_binding_changed"),
    (lambda d: d["sessions"].update({SYMBOLS[1]: replace(d["sessions"][SYMBOLS[1]], opening=d["target"].opening + cc.MINUTE)}), "session_opening_changed"),
])
def test_contract_failures_are_refusals_not_unknowns(change, reason):
    data = fixture()
    change(data)
    with pytest.raises(features.FeaturesRefusal, match=reason):
        build(data)


def test_future_first30_and_late_window_never_become_unknown():
    data = fixture()
    data.update(observed_end=data["target"].opening + 25 * cc.MINUTE,
                decision_at=data["target"].opening + 27 * cc.MINUTE)
    with pytest.raises(features.FeaturesRefusal, match="future_first30_window"):
        build(data)
    data = fixture()
    with pytest.raises(features.FeaturesRefusal, match="future_or_late_available_window"):
        features._window(data["sessions"][SYMBOLS[1]], data["sources"][SYMBOLS[1]],
            SYMBOLS[1], "future", data["target"].opening, data["observed_end"] + cc.MINUTE,
            data["target"], data["decision_at"])


def test_b_membership_without_global_and_and_missing_peers_kept():
    data = fixture()
    data["setup_id"] = "EA-B"
    data["sessions"][SYMBOLS[1]] = None
    result = build(data)
    assert result.features["setup_is_A"].value == 0
    assert result.fixed_peers == SYMBOLS[1:]
    assert result.common_peers == SYMBOLS[2:]
    assert result.features["common_peer_count_divided_by_original_peer_count"].value == Fraction(6, 7)
    assert {row.reasons for row in result.windows if row.symbol == SYMBOLS[1]} == {("session_missing",)}


def test_source_snapshot_does_not_follow_later_input_mutation():
    data = fixture()
    result = build(data)
    data["sources"][SYMBOLS[0]]["raw_chunk_metadata"]["retrieved_at"] = "changed"
    assert result.source_evidence[SYMBOLS[0]]["raw_chunk_metadata"]["retrieved_at"] == "2026-10-20T00:00:00+00:00"


@pytest.mark.parametrize("clock,reason", [
    (lambda at: at.replace(tzinfo=None), "aware_clock_required"),
    (lambda at: at.astimezone(timezone.utc), "kst_session_clock_required"),
    (lambda at: at + timedelta(seconds=1), "invalid_session_minute_clock"),
])
def test_bad_minute_clocks_refused_instead_of_becoming_missing(clock, reason):
    data = fixture()
    session = data["sessions"][SYMBOLS[1]]
    minutes = dict(session.minutes)
    at = data["target"].opening + 5 * cc.MINUTE
    values = minutes.pop(at)
    minutes[clock(at)] = values
    data["sessions"][SYMBOLS[1]] = replace(session, minutes=minutes)
    with pytest.raises(features.FeaturesRefusal, match=reason):
        build(data)


def test_never_replays_old_peer_definition_detectors_or_outcomes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("old computation replayed")

    monkeypatch.setattr(ea, "_peer_mean", forbidden)
    monkeypatch.setattr(ea, "prepare_candidate", forbidden)
    monkeypatch.setattr(eb.signals, "detect_bars", forbidden)
    monkeypatch.setattr(eb.outcomes, "build_outcomes", forbidden, raising=False)
    assert build(fixture()).features["peer_first30_return"].value == Fraction(1, 20)
