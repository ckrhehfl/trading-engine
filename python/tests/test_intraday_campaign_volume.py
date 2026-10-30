"""Invented stored reports and public EB builder integration; no actual IO."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import json

import pytest

from research import intraday_campaign_volume as projection
from research import intraday_context_experiment as eb


def exact(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def report():
    """Handcrafted stored branches, including original EA cap early-return."""
    sessions = ["2025-09-04", "2025-09-08"]
    symbols = [f"KRX:{number:06d}" for number in range(1, 21)]
    sources = [{"session": day, "symbol": symbol} for day in sessions for symbol in symbols]
    value = {"mode": "fixed-ea-context-exploratory-comparison", "setup_id": "EA-A",
             "reference_sessions": sessions, "reference_weeks": ["2025-09-01", "2025-09-08"],
             "sources": sources, "volume_sources": deepcopy(sources),
             "source_target_matrix": {}, "variants": {}}
    for policy in projection.POLICIES:
        memberships, rows = [], []
        for day_index, day in enumerate(sessions):
            members = [{"symbol": symbol, "rank": index + 1} for index, symbol in enumerate(symbols)]
            memberships.append({"session": day, "available_at": day + "T07:00:00+09:00",
                                "selected_at": day + "T08:00:00+09:00", "targets": members})
            for index, member in enumerate(members):
                state = "signal" if day_index == 0 and index < 5 else "known_no_signal" if index % 2 else "unknown"
                volume = (exact(2), exact(1, 3), None, exact(0), None)[index] if index < 5 else None
                arms = {"basic": "selected", "volume_only": "selected" if index == 0 else "rejected",
                        "market_relative_only": "unknown", "both": "unknown"}
                features = {"relative_volume": {"value": volume, "reasons": []},
                            "stock_minus_peer_return": {"value": None, "reasons": ["incomplete_fixed_peer_basket"]}}
                if index == 2:
                    arms["volume_only"] = "unknown"
                    features["relative_volume"]["reasons"] = ["nonpositive_reference_median"]
                if index == 4:
                    arms = dict.fromkeys(arms, "unknown")
                    features = {"clock": {"value": None, "reasons": ["same_day_cap_infeasible"]}}
                if state != "signal":
                    arms = dict.fromkeys(arms, "unknown" if state == "unknown" else "rejected")
                    features = {}
                rows.append({"session": day, "symbol": member["symbol"], "membership_day_index": day_index,
                             "membership_target": deepcopy(member), "source_index": day_index * len(symbols) + index,
                             "volume_source_index": day_index * len(symbols) + index,
                             "signal": {"hypothesis_id": "H1-morning-trend-pullback-reclaim",
                                        "state": state, "reason": "reclaim" if state == "signal" else "missing_prefix" if state == "unknown" else "initial_condition_false",
                                        "decision_at": day + "T10:17:00+09:00",
                                        "observed_through": None if state == "unknown" else day + "T10:15:00+09:00",
                                        "signal_bar_start": day + "T10:10:00+09:00" if state == "signal" else None,
                                        "signal_bar_end": day + "T10:15:00+09:00" if state == "signal" else None,
                                        "setup_bar_end": day + "T10:10:00+09:00" if state == "signal" else None},
                             "features": features, "arms": arms,
                             "common_context_observable": False, "common_price_observable": False,
                             "outcome": {"status": "unknown", "reason": "required_path_missing", "gross": object()}})
        value["source_target_matrix"][policy] = {"days": memberships}
        value["variants"][policy] = {"days": rows, "statistics": object()}
    return value


def project(value):
    return projection.project_volume_inputs(value, inputs_authenticated=True)


class NoLabelReads(Mapping):
    def __init__(self, value, forbidden):
        self.value, self.forbidden = value, set(forbidden)

    def __getitem__(self, key):
        if key in self.forbidden:
            raise AssertionError(f"forbidden read: {key}")
        return self.value[key]

    def __iter__(self):
        return iter(self.value)

    def __len__(self):
        return len(self.value)


def poison(value):
    for policy in projection.POLICIES:
        variant = value["variants"][policy]
        for index, row in enumerate(variant["days"]):
            if "relative_volume" in row["features"]:
                row["features"] = NoLabelReads(row["features"], {
                    "stock_minus_peer_return", "peer_open_to_signal_return", "peer_last15_return"})
            row["arms"] = NoLabelReads(row["arms"], {"market_relative_only", "both"})
            variant["days"][index] = NoLabelReads(row, {
                "outcome", "common_context_observable", "common_price_observable", "structural_stop", "entry_at", "cap_at"})
        value["variants"][policy] = NoLabelReads(variant, {"statistics", "own_sizing", "arm_counts"})
    return value


def test_selected_rejected_unknown_zero_nonsignal_and_zero_week_survive():
    value = report()
    projected = project(value)
    for policy in projection.POLICIES:
        variant = projected["variants"][policy]
        rows = variant["days"]
        assert len(rows) == 40
        assert [r["volume_state"] for r in rows[:7]] == [
            "observed", "observed", "unknown", "observed", "not_computed", "not_applicable", "not_applicable"]
        assert [r["volume_only"] for r in rows[:5]] == ["selected", "rejected", "unknown", "rejected", "unknown"]
        assert [r["common_control"] for r in rows[:5]] == [True, True, False, True, False]
        assert rows[1]["relative_volume"]["value"] == Fraction(1, 3)
        assert rows[3]["relative_volume"]["value"] == Fraction(0)
        assert type(rows[3]["relative_volume"]["value"]) is Fraction
        assert rows[2]["relative_volume"] == {"value": None, "reasons": ("nonpositive_reference_median",)}
        assert rows[4]["relative_volume"] is None
        assert rows[4]["uncomputed_reasons"] == ("same_day_cap_infeasible",)
        assert variant["by_session"]["2025-09-08"]["signals"] == 0
        assert variant["by_week"]["2025-09-08"]["targets"] == 20
        assert variant["counts"]["common_control"] == 3
        assert variant["counts"]["selected"] == 1
        assert [(r["session"], r["symbol"]) for r in rows] == [
            (r["session"], r["symbol"]) for r in value["variants"][policy]["days"]]
        for field in projection.SIGNAL_CLOCKS:
            assert rows[0][field] == value["variants"][policy]["days"][0]["signal"][field]
    assert not projected["performance_computed"]
    assert not projected["volume_availability_reauthenticated"]
    assert "volume_availability_not_reauthenticated_from_report" in projected["limitations"]


def test_outcome_other_features_and_common_flags_are_never_read():
    value = report()
    baseline = project(value)
    assert project(poison(value)) == baseline


def test_mutating_incomplete_or_declined_old_outcome_does_not_change_admission():
    value = report()
    baseline = project(value)
    for variant in value["variants"].values():
        for index, row in enumerate(variant["days"]):
            row["outcome"] = {"status": "declined"} if index % 2 else None
            row["common_context_observable"] = True
            row["common_price_observable"] = True
    assert project(value) == baseline


def test_does_not_mutate_source_or_share_mutable_return_fields():
    value = report()
    for variant in value["variants"].values():
        variant.pop("statistics")
        for row in variant["days"]:
            row["outcome"] = None
    original = deepcopy(value)
    result = project(value)
    assert value == original
    result["reference_sessions"].append("2025-09-09")
    result["variants"]["D+1"]["days"][0]["membership_target"]["rank"] = 99
    assert value == original


def test_exact_large_fraction_and_boundary_do_not_round():
    value = report()
    numerator, denominator = 2 * 10**100 - 1, 10**100
    row = value["variants"]["D+1"]["days"][1]
    row["features"]["relative_volume"]["value"] = exact(numerator, denominator)
    result = project(value)["variants"]["D+1"]["days"][1]
    assert result["relative_volume"]["value"] == Fraction(numerator, denominator)
    assert result["common_control"] and not result["selected"]


@pytest.mark.parametrize("bad", [True, 0, 2.0, "2", Fraction(2), {}, exact(-1), exact(1, 0), exact(2, 2),
    exact("00"), exact(0, 2), {"numerator": 2, "denominator": "1"},
    {"numerator": "2", "denominator": True}, {**exact(2), "extra": 0}])
def test_malformed_fraction_types_and_noncanonical_values_refuse(bad):
    value = report()
    value["variants"]["D+1"]["days"][0]["features"]["relative_volume"]["value"] = bad
    with pytest.raises(projection.VolumeProjectionRefusal):
        project(value)


@pytest.mark.parametrize("mutate", [
    lambda r: r["arms"].update(volume_only="rejected"),
    lambda r: r["arms"].update(volume_only=True),
    lambda r: r["arms"].update(basic="unknown"),
    lambda r: r["features"]["relative_volume"].update(value=None),
    lambda r: r["features"]["relative_volume"].update(reasons=["missing_current_volume"]),
    lambda r: r["features"]["relative_volume"].update(reasons="missing"),
    lambda r: r["features"]["relative_volume"].update(reasons=[True]),
    lambda r: r["features"]["relative_volume"].update(reasons=["gap", "gap"]),
    lambda r: r["features"]["relative_volume"].update(available_at="2025-09-04T10:00:00+09:00"),
    lambda r: r.update(membership_day_index=True),
    lambda r: r.update(source_index=True),
    lambda r: r.update(volume_source_index=-1),
    lambda r: r["membership_target"].update(rank=True),
    lambda r: r["signal"].update(state=True),
    lambda r: r["signal"].update(reason=None),
])
def test_contradictory_state_value_reason_and_types_refuse(mutate):
    value = report()
    mutate(value["variants"]["D+1"]["days"][0])
    with pytest.raises(projection.VolumeProjectionRefusal):
        project(value)


@pytest.mark.parametrize("field,bad", [
    ("decision_at", "2025-09-04T07:59:00+09:00"),
    ("decision_at", "2025-09-04T10:14:00+09:00"),
    ("decision_at", "2025-09-04T10:17:00"), ("decision_at", True), ("decision_at", None),
    ("decision_at", "2025-09-05T10:17:00+09:00"),
    ("signal_bar_start", "2025-09-04T10:16:00+09:00"),
    ("setup_bar_end", "2025-09-04T10:11:00+09:00"),
    # Ordered-but-invalid clocks passed the old private prototype's inequalities.
    ("signal_bar_start", "2025-09-04T10:11:00+09:00"),
    ("observed_through", "2025-09-04T10:16:00+09:00"),
    ("decision_at", "2025-09-04T10:18:00+09:00"),
])
def test_confirmable_membership_availability_and_observation_clocks_refuse(field, bad):
    value = report()
    value["variants"]["D+1"]["days"][0]["signal"][field] = bad
    with pytest.raises(projection.VolumeProjectionRefusal):
        project(value)


def test_membership_available_after_selection_refuses():
    value = report()
    value["source_target_matrix"]["D+1"]["days"][0]["available_at"] = "2025-09-04T10:18:00+09:00"
    with pytest.raises(projection.VolumeProjectionRefusal, match="available_after_selection"):
        project(value)


def test_equivalent_timezone_and_available_equal_selection_are_valid():
    value = report()
    member = value["source_target_matrix"]["D+1"]["days"][0]
    member["available_at"] = member["selected_at"]
    row = value["variants"]["D+1"]["days"][0]
    row["signal"]["decision_at"] = "2025-09-04T01:17:00+00:00"
    result = project(value)
    assert result["variants"]["D+1"]["days"][0]["decision_at"] == row["signal"]["decision_at"]


@pytest.mark.parametrize("mutate", [
    lambda v: v["variants"]["D+1"]["days"].append(deepcopy(v["variants"]["D+1"]["days"][0])),
    lambda v: v["variants"]["D+1"]["days"].pop(),
    lambda v: v["variants"]["D+1"]["days"][0].update(symbol="KRX:999999"),
    lambda v: v["source_target_matrix"]["D+1"]["days"][0]["targets"].append({"symbol": "KRX:000001", "rank": 8}),
    lambda v: v["source_target_matrix"]["D+1"]["days"][0]["targets"][0].update(rank=True),
    lambda v: v["variants"]["D+1"]["days"][0]["membership_target"].update(rank=99),
    lambda v: v["sources"][0].update(symbol="KRX:999999"),
    lambda v: v["volume_sources"][0].update(session="2025-09-05"),
    lambda v: v["reference_sessions"].reverse(),
    lambda v: v["reference_weeks"].pop(),
    lambda v: v["variants"].pop("D+2"),
    lambda v: v.update(setup_id="EA-B"),
])
def test_duplicate_missing_extra_and_changed_identity_refuse(mutate):
    value = report()
    mutate(value)
    with pytest.raises(projection.VolumeProjectionRefusal):
        project(value)


@pytest.mark.parametrize("bad", [False, None, 1, "yes"])
def test_authentication_is_explicit_and_never_claimed_as_hash_verification(bad):
    with pytest.raises(projection.VolumeProjectionRefusal, match="authenticated"):
        projection.project_volume_inputs(report(), inputs_authenticated=bad)
    assert project(report())["source_authentication"] == "caller_assertion"


def public_builder_fixture():
    """Invented 21-date source grid; public EB supplies every saved report row.

    The calendar is supplied/uncertified, not an assertion about trading days.
    Twenty prior first60 windows allow final-date 2/1/0/null volume branches.
    Price gaps/entry invalidation are introduced only after signal detection.
    """
    cc = eb.cc
    days = [(date(2025, 9, 4) + timedelta(days=i)).isoformat() for i in range(21)]
    symbols = [f"KRX:{n:06d}" for n in range(1, 21)]
    upstream = {"study_id": "intraday-universe-sources-v1",
                "mode": "discovery-universe-source-and-coverage-only", **cc.FLAGS,
                "calendar": {"rows": [{"date": day, "fetched_at": None} for day in days], "certified": False},
                "variants": {}}
    for policy in projection.POLICIES:
        upstream["variants"][policy] = {"days": [{
            "session": day, "observation_date": "2025-08-31",
            "selected_at": day + "T08:30:00+09:00", "available_at": "2025-09-01T08:30:00+09:00",
            "retrieved_at": {"fixture": "2026-10-20T00:00:00+00:00"},
            "source": "invented monthly source", "is_final": None,
            "data_vintage": {"fixture": "b" * 64}, "evidence_level": "assumed",
            "targets": [{"symbol": symbol, "rank": i + 1, "stored_row_count": None,
                         "state": "no_stored_rows_unknown_reason"} for i, symbol in enumerate(symbols)],
        } for day in days]}
    prices, volumes = [], []
    for day_index, day in enumerate(days):
        opening = cc.session_open(day)
        for index, symbol in enumerate(symbols):
            signal = (day_index == 0 and index == 0) or (day_index == 20 and index < 4)
            rows = [{"symbol": symbol, "interval": "1m",
                     "open_time_ms": int((opening + i * cc.MINUTE).timestamp()) * 1000 + 123,
                     "open": "100", "high": "100", "low": "100", "close": "100",
                     "fetched_at": "2026-10-20T00:00:00+00:00"}
                    for i in range(145 if signal else 60)]
            if signal:
                for row in rows[:60]:
                    row.update(high="112", low="98", close="110")
                for start, end, op, hi, lo, cl in (
                    (60, 65, "109", "110", "107", "109"),
                    (65, 70, "108", "109", "107", "108"),
                    (70, 75, "110", "111", "108", "110"),
                    (75, 145, "110", "112", "109", "110"),
                ):
                    for row in rows[start:end]:
                        row.update(open=op, high=hi, low=lo, close=cl)
                if day_index == 20 and index == 0:
                    rows.pop(100)  # required holding minute missing; volume prefix intact
                if day_index == 20 and index == 1:
                    rows[80].update(open="106", high="106", low="106", close="106")
            if day_index == 20 and index == 4:
                rows = []  # explicit unknown signal and missing fixed peer
            chunk = {"symbol": symbol, "session": day, "observation_date": day,
                     "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00",
                     "source": "collector klines", "is_final": None, "evidence_level": "assumed", "rows": rows}
            prices.append(chunk)
            current_volume = ("2", "1", "0", None)[index] if day_index == 20 and index < 4 else "1"
            volumes.append({**{key: value for key, value in chunk.items() if key != "rows"},
                "daily_rows": [], "daily_retrieved_at": chunk["retrieved_at"],
                "rows": [{"open_time_ms": row["open_time_ms"], "volume": current_volume,
                          "quote_volume": None, "fetched_at": row["fetched_at"]} for row in rows]})
    return upstream, prices, volumes


def test_public_eb_builder_canonical_json_to_sidecar_preserves_volume_population(monkeypatch):
    upstream, prices, volumes = public_builder_fixture()
    original = eb.build_experiment(upstream, iter(prices), iter(volumes), setup_id="EA-A",
        inputs_authenticated=True, authorization_reference="invented-volume-sidecar-integration")
    # Match the existing publication boundary: dispersion also contains dates,
    # while CC's canonical encoder handles datetime/Decimal/Fraction values.
    publication = json.loads(json.dumps(original, default=lambda value: value.isoformat()
        if type(value) is date else json.loads(eb.cc._canonical(value))))
    saved = json.loads(eb.cc._canonical(publication))

    def forbidden(*args, **kwargs):
        raise AssertionError("projection cannot rerun a detector or outcomes")

    monkeypatch.setattr(eb.signals, "detect_bars", forbidden)
    monkeypatch.setattr(eb.outcomes, "exploratory_outcome", forbidden)
    projected = project(saved)
    for policy in projection.POLICIES:
        old = saved["variants"][policy]["days"]
        variant = projected["variants"][policy]
        final = variant["days"][-20:]
        assert len(variant["days"]) == 420
        assert old[-20]["outcome"]["status"] == "unknown"
        assert old[-19]["outcome"]["status"] == "declined"
        assert not old[-20]["common_price_observable"]
        assert not old[-19]["common_price_observable"]
        assert [r["relative_volume"]["value"] for r in final[:4]] == [Fraction(2), Fraction(1), Fraction(0), None]
        assert [r["volume_only"] for r in final[:4]] == ["selected", "rejected", "rejected", "unknown"]
        assert [r["common_control"] for r in final[:4]] == [True, True, True, False]
        assert final[3]["relative_volume"]["reasons"] == ("missing_current_volume",)
        assert final[4]["signal_state"] == "unknown"
        assert variant["days"][0]["relative_volume"]["reasons"] == ("volume_inputs_missing",)
        assert variant["counts"]["common_control"] == 3
        assert variant["counts"]["selected"] == 1
        assert variant["by_week"]["2025-09-08"]["signals"] == 0
        assert variant["by_week"]["2025-09-15"]["signals"] == 0
        assert [(r["session"], r["symbol"]) for r in variant["days"]] == [(r["session"], r["symbol"]) for r in old]
        for row, stored in zip(variant["days"], old, strict=True):
            assert row["volume_only"] == stored["arms"]["volume_only"]
            for field in projection.SIGNAL_CLOCKS:
                assert row[field] == stored["signal"][field]
    assert projected["reference_sessions"] == saved["reference_sessions"]
    assert projected["reference_weeks"] == saved["reference_weeks"]
    assert project(poison(saved)) == projected
