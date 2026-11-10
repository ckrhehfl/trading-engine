"""EK causal features tested only with invented minute observations."""
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from research import intraday_recovery_features as features
from research import intraday_recovery_features_reference as reference
from research.intraday_campaign_wsl_transport import encode
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs


OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))


def session(symbol="KRX:000001", *, closes=None, volume=10):
    closes = list(closes) if closes is not None else [10000 - index - 1 for index in range(90)]
    rows, previous = {}, Decimal(10000)
    for index, value in enumerate(closes):
        at, close = OPEN + index * MINUTE, Decimal(value)
        rows[at] = Minute(symbol, at, at + 3 * MINUTE,
                         (previous, max(previous, close) + 2, min(previous, close) - 2, close),
                         None if volume is None else Decimal(volume), "observed",
                         "missing" if volume is None else "observed")
        previous = close
    return SessionInputs(symbol, OPEN, OPEN + 390 * MINUTE, rows)


def changed(value, index, **updates):
    at = value.opening + index * MINUTE
    rows = dict(value.minutes)
    rows[at] = replace(rows[at], **updates)
    return replace(value, minutes=rows)


def missing(value, index):
    return replace(value, minutes={at: row for at, row in value.minutes.items() if at != OPEN + index * MINUTE})


def check(value, **kwargs):
    result = features.build_features(value, **kwargs)
    assert reference.build_features(value, **kwargs) == result
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    return result


@pytest.mark.parametrize("value", [
    session(), session(volume=None), session(volume=0),
    session(closes=[10000 + index for index in range(90)]),
    session(closes=[10000] * 90), missing(session(), 0), missing(session(), 22),
    missing(session(), 54), changed(session(), 12, ohlc=None, price_state="transport_error"),
    changed(session(), 27, shares=None, volume_state="conflict"),
    session(closes=[10000, 9950, 9900] + [9900 + index % 4 for index in range(87)]),
])
def test_independent_measurements_and_json_boundary(value):
    result = check(value)
    if result["chart_state"] == "available":
        assert len(result["minute"]) == 60
        assert all(len(row) == 10 for row in result["minute"])
        assert len(result["static"]) == len(features.STATIC_NAMES) == 25


def test_candidate_keeps_original_order_geometry_without_complete_chart():
    intact = check(session())
    inside = check(missing(session(), 22))
    assert intact["candidate_state"] == "ready"
    assert inside["candidate_state"] == "own_negative_chart_unsupported"
    assert inside["order_state"] == "ready"
    assert (inside["own_return"], inside["limit"], inside["stop"]) == (
        intact["own_return"], intact["limit"], intact["stop"])
    assert inside["minute"] is inside["static"] is None
    assert check(missing(session(), 54))["order_state"] == "unresolved"
    assert check(missing(session(), 59))["candidate_state"] == "endpoints_unknown"
    assert check(missing(session(closes=[10001] * 90), 22))["candidate_state"] == "own_nonnegative"


def test_limit_at_stop_is_known_decline():
    value = session(closes=[9900] * 90)
    for index in range(45, 60):
        value = changed(value, index, ohlc=(Decimal(9900),) * 4)
    result = check(value)
    assert (result["candidate_state"], result["order_state"]) == ("limit_at_stop", "decline")
    assert result["limit"] == result["stop"] == "9900"


def test_supported_nonnegative_chart_keeps_hypothetical_long_geometry():
    result = check(session(closes=[10000 + index for index in range(90)]))
    assert result["candidate_state"] == "own_nonnegative"
    assert result["order_state"] == "decline"
    assert result["limit"] is not None and result["stop"] is not None
    assert result["chart_state"] == "available" and result["minute"] and result["static"]


class PrefixOnly(Mapping):
    def __init__(self, rows, end):
        self.rows, self.end = rows, end

    def __getitem__(self, key):
        assert key < self.end, "future suffix accessed"
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("whole-session iteration")

    def __len__(self):
        raise AssertionError("whole-session length")


@pytest.mark.parametrize("end_offset", [60, 65, 90])
def test_only_completed_prefix_is_read_and_candidate_stays_at_first_hour(end_offset):
    value = session()
    first = check(value)
    guarded = replace(value, minutes=PrefixOnly(value.minutes, OPEN + end_offset * MINUTE))
    result = check(guarded, end_offset=end_offset, remaining_minutes=30)
    for field in ("candidate_state", "order_state", "own_return", "limit", "stop"):
        assert result[field] == first[field]
    assert result["static"][-1] == 0.5
    assert result["decision_at"] == (OPEN + (end_offset + 2) * MINUTE).isoformat()


def test_unknown_and_observed_zero_volume_are_distinct():
    unknown, zero = check(session(volume=None)), check(session(volume=0))
    assert unknown["candidate_state"] == zero["candidate_state"] == "ready"
    assert unknown["minute"][0][-2:] == [0.0, 0.0]
    assert zero["minute"][0][-2:] == [0.0, 1.0]
    assert unknown["volume_states"] == ["missing"] * 60
    assert zero["volume_states"] == ["observed"] * 60
    assert dict(zip(features.STATIC_NAMES, unknown["static"]))["volume_ratio_supported"] == 0


def test_shock_and_drift_remain_distinct_without_future_pivots():
    shock = check(session(closes=[9940] * 90))
    drift = check(session())
    shock_values = dict(zip(features.STATIC_NAMES, shock["static"]))
    drift_values = dict(zip(features.STATIC_NAMES, drift["static"]))
    assert shock_values["window_return_pct"] == drift_values["window_return_pct"]
    assert shock_values["negative_move_top3_fraction"] == 1
    assert drift_values["negative_move_top3_fraction"] == 3 / 60
    assert shock_values["longest_down_run_fraction"] < drift_values["longest_down_run_fraction"]
    recovered = check(session(closes=[9900] * 30 + [9950] * 60))
    assert dict(zip(features.STATIC_NAMES, recovered["static"]))["maximum_drawdown_recovery_fraction"] == 0.5


def test_peer_absence_never_changes_own_features_and_uses_leave_one_out():
    own = session()
    before = check(own)
    peers = {own.symbol: own, **{f"KRX:{index:06d}": session(f"KRX:{index:06d}", volume=None)
                               for index in range(2, 7)}}
    result = features.build_peers(own.symbol, peers)
    assert result == reference.build_peers(own.symbol, peers)
    assert result["status"] == "available"
    assert result["expected_count"] == result["observed_count"] == 5
    assert own.symbol not in result["observed_ids"]
    assert all(len(row) == 3 for row in result["minute"])
    peers["KRX:000002"] = missing(peers["KRX:000002"], 22)
    fewer = features.build_peers(own.symbol, peers)
    assert fewer == reference.build_peers(own.symbol, peers)
    assert fewer["status"] == "unavailable" and fewer["minute"] is None
    assert fewer["observed_count"] == 4 and fewer["expected_count"] == 5
    assert check(own) == before
    assert features.build_peers(own.symbol, {own.symbol: own})["status"] == "unavailable"


def test_numeric_vectors_have_no_symbol_or_roster_dependence():
    first, other = check(session()), check(session("KRX:999999"))
    assert first["minute"] == other["minute"] and first["static"] == other["static"]
    assert not any("peer" in name or "rank" in name or "symbol" in name for name in features.STATIC_NAMES)


def test_reference_does_not_call_producer_projection(monkeypatch):
    value = session()
    expected = features.build_features(value)
    def forbidden(*args, **kwargs):
        pytest.fail("reference called producer algebra")
    for name in ("build_features", "build_chart_inputs", "chart_tensor", "_candidate", "_static"):
        monkeypatch.setattr(features, name, forbidden)
    assert reference.build_features(value) == expected


@pytest.mark.parametrize("value", [
    changed(session(), 3, available_at=OPEN + 7 * MINUTE),
    changed(session(), 3, shares=Decimal(0), volume_state="missing"),
    changed(session(), 3, ohlc=(Decimal(1), Decimal(2), Decimal(3), Decimal(4))),
])
def test_malformed_observations_refuse_instead_of_becoming_zero(value):
    for build in (features.build_features, reference.build_features):
        with pytest.raises(InputRefusal):
            build(value)


@pytest.mark.parametrize("kwargs", [{"end_offset": 59}, {"end_offset": 61}, {"end_offset": 395},
                                    {"remaining_minutes": -1}, {"remaining_minutes": True}])
def test_invalid_management_geometry_refuses(kwargs):
    for build in (features.build_features, reference.build_features):
        with pytest.raises(InputRefusal):
            build(session(), **kwargs)


@pytest.mark.parametrize("count", [1, 5, 6, 12, 20])
@pytest.mark.parametrize("variant", ["equal", "different", "missing"])
@pytest.mark.parametrize("end_offset", [60, 75])
def test_all_peer_views_are_byte_identical_to_both_per_target_paths(count, variant, end_offset):
    roster = {}
    for index in range(1, count + 1):
        symbol = f"KRX:{index:06d}"
        closes = None if variant == "equal" else [10000 + ((index % 3) - 1) * minute for minute in range(90)]
        value = session(symbol, closes=closes, volume=None if index % 2 else 0)
        if variant == "missing" and index % 4 == 0:
            value = missing(value, 22)
        roster[symbol] = value
    # Input insertion order is not a feature or a change to original support.
    roster = dict(reversed(tuple(roster.items())))
    produced = features.build_all_peers(roster, end_offset=end_offset)
    rebuilt = reference.build_all_peers(roster, end_offset=end_offset)
    assert encode(produced) == encode(rebuilt)
    assert set(produced) == set(roster)
    for symbol in roster:
        assert encode(produced[symbol]) == encode(features.build_peers(symbol, roster, end_offset=end_offset))
        assert encode(produced[symbol]) == encode(reference.build_peers(symbol, roster, end_offset=end_offset))


@pytest.mark.parametrize("module", [features, reference])
def test_all_peers_validate_each_full_window_once_without_future_or_volume_reads(module, monkeypatch):
    roster = {f"KRX:{index:06d}": session(f"KRX:{index:06d}", volume=None) for index in range(1, 9)}
    roster = {name: replace(value, minutes=PrefixOnly(value.minutes, OPEN + 60 * MINUTE))
              for name, value in roster.items()}
    calls = []
    original = module.window
    def counted(value, offset, count, decision, **kwargs):
        if count == 60:
            calls.append(value.symbol)
            assert offset == 0 and not kwargs.get("volume", False)
        return original(value, offset, count, decision, **kwargs)
    monkeypatch.setattr(module, "window", counted)
    result = module.build_all_peers(roster)
    assert sorted(calls) == sorted(roster)
    assert all(value["status"] == "available" for value in result.values())


def test_all_peers_retain_distinct_original_rosters_and_target_specific_quorum():
    all_names = {f"KRX:{index:06d}": session(f"KRX:{index:06d}") for index in range(1, 9)}
    first = dict(list(all_names.items())[:6])
    second = dict(list(all_names.items())[2:])
    first["KRX:000001"] = missing(first["KRX:000001"], 22)
    for roster in (first, second):
        result = features.build_all_peers(roster)
        assert result == reference.build_all_peers(roster)
        for target in roster:
            assert result[target]["expected_ids"] == sorted(set(roster) - {target})
    initial = features.build_all_peers(first)
    assert initial["KRX:000001"]["status"] == "available"
    assert initial["KRX:000002"]["status"] == "unavailable"


def test_reference_batch_does_not_use_producer_or_per_target_algebra(monkeypatch):
    roster = {f"KRX:{index:06d}": session(f"KRX:{index:06d}") for index in range(1, 8)}
    expected = features.build_all_peers(roster)
    def forbidden(*args, **kwargs):
        pytest.fail("independent batch used producer or repeated per-target algebra")
    monkeypatch.setattr(features, "build_all_peers", forbidden)
    monkeypatch.setattr(features, "build_peers", forbidden)
    monkeypatch.setattr(reference, "build_peers", forbidden)
    assert reference.build_all_peers(roster) == expected


@pytest.mark.parametrize("module", [features, reference])
def test_all_peers_reject_unregistered_or_cross_calendar_rosters(module):
    with pytest.raises(InputRefusal, match="original peer roster"):
        module.build_all_peers({})
    value = session()
    wrong = replace(session("KRX:000002"), closing=OPEN + 389 * MINUTE)
    with pytest.raises(InputRefusal, match="calendar_differs"):
        module.build_all_peers({value.symbol: value, wrong.symbol: wrong})
