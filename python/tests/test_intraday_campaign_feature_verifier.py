"""Invented original inputs and deliberately corrupted producer outputs only."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from research import intraday_campaign_feature_verifier as verifier
from research import intraday_campaign_features as producer
from research import intraday_campaign_volume as volume
from research import intraday_context_experiment as eb


ea, cc = eb.ea, eb.cc
DAY = "2025-09-04"
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(8))


def feature_inputs(peers=7):
    symbols = SYMBOLS[:peers + 1]
    opening = cc.session_open(DAY)
    sessions, sources = {}, {}
    for index, symbol in enumerate(symbols):
        sessions[symbol] = cc.Session(opening,
            {opening + n * cc.MINUTE: (Decimal(100), Decimal(102 + index), Decimal(99), Decimal(101 + index))
             for n in range(90)}, (), (), ())
        sources[symbol] = {
            "session": DAY, "symbol": symbol, "data_vintage": str(index) * 64,
            "raw_chunk_metadata": {"observation_date": DAY, "available_at": None,
                "retrieved_at": "2026-10-20T00:00:00+00:00", "source": "collector klines",
                "is_final": None, "evidence_level": "assumed"},
            "input_rows": 90, "row_linkage_sha256": "b" * 64,
            "source_row_metadata_location": "original_pinned_archive_chunk"}
    target = ea.Target(symbols[0], date.fromisoformat(DAY), "D+1", opening, cc.session_close(DAY),
        eb.PRICE_BASIS, ea.PriceSource(datetime(2026, 10, 20, tzinfo=timezone.utc),
            "collector klines", None, "0" * 64, "assumed"))
    fixed = datetime(2025, 9, 1, 8, tzinfo=cc.KST)
    universe = ea.MonthlyUniverse(date(2025, 9, 1), symbols, fixed,
        ea.Provenance(datetime(2025, 8, 31, 23, 59, tzinfo=cc.KST), fixed, fixed,
                      "invented membership", True, "invented-month", "synthetic"))
    return {"target": target, "universe": universe, "sessions": sessions, "sources": sources,
            "setup_id": "EA-A", "observed_end": opening + 60 * cc.MINUTE,
            "decision_at": opening + 62 * cc.MINUTE, "structural_stop": Fraction(197, 2),
            "inputs_authenticated": True, "authentication_reference": "invented-original-day"}


def lose(data, symbol, minute, reason):
    session = data["sessions"][symbol]
    at = session.opening + minute * cc.MINUTE
    minutes = dict(session.minutes)
    minutes[at] = None
    data["sessions"][symbol] = replace(session, minutes=minutes,
        gaps=({"start": at, "end": at + cc.MINUTE, "state": reason},))


def changed_feature(result, name, **changes):
    features = dict(result.features)
    features[name] = replace(features[name], **changes)
    return replace(result, features=features)


def test_exact_reconstruction_and_no_producer_computation_delegation(monkeypatch):
    data = feature_inputs(5)
    result = producer.prepare_features(**data)

    def forbidden(*args, **kwargs):
        raise AssertionError("producer computation called by verifier")

    for module, name in ((producer, "prepare_features"), (producer, "_window"),
                         (ea, "_peer_mean"), (ea, "_target"), (ea, "_peers"),
                         (eb, "_window"), (eb, "_provenance"), (eb, "_price_source"),
                         (volume, "project_volume_inputs")):
        monkeypatch.setattr(module, name, forbidden)
    receipt = verifier.verify_features(result, **data)
    assert receipt["verified"] and receipt["feature_count"] == 10
    assert receipt["window_count"] == 19 and receipt["source_receipt_count"] == 6
    assert receipt["original_peer_count"] == receipt["common_peer_count"] == 5
    assert not receipt["strategy_pass"] and not receipt["actual_execution_authorized"]
    assert result.features["peer_last15_return"].value == Fraction(1, 25)
    assert result.features["fraction_of_session_elapsed"].value == Fraction(62, 390)
    assert result.features["structural_stop_gap_from_signal_close"].value == Fraction(5, 202)
    assert result.features["trigger_close_location"].value == Fraction(2, 3)


def test_common_intersection_not_window_specific_sets_and_missing_target_separate():
    data = feature_inputs()
    lose(data, SYMBOLS[1], 10, "conflict")
    lose(data, SYMBOLS[2], 50, "invalid_price")
    lose(data, SYMBOLS[0], 10, "missing")
    result = producer.prepare_features(**data)
    receipt = verifier.verify_features(result, **data)
    assert receipt["common_peer_count"] == 5
    assert result.features["target_open_to_signal_return"].value is None
    assert result.features["target_last15_return"].value == Fraction(1, 100)
    assert result.features["peer_last15_return"].value == Fraction(3, 50)
    with pytest.raises(verifier.InputVerificationRefusal, match="common_peers"):
        verifier.verify_features(replace(result, common_peers=SYMBOLS[2:]), **data)
    wrong = changed_feature(result, "peer_first30_return", value=Fraction(7, 100))
    with pytest.raises(verifier.InputVerificationRefusal, match="peer_first30_return"):
        verifier.verify_features(wrong, **data)


@pytest.mark.parametrize("peer_count", [0, 4, 5])
def test_quorum_unknown_original_count_and_absent_session(peer_count):
    data = feature_inputs(peer_count)
    data["sessions"][SYMBOLS[0]] = None
    result = producer.prepare_features(**data)
    verifier.verify_features(result, **data)
    assert (result.features["peer_last15_return"].value is None) == (peer_count < 5)
    assert result.features["target_last15_return"].reasons == ("session_missing",)
    wrong = changed_feature(result, "target_last15_return", value=Fraction(0), reasons=())
    with pytest.raises(verifier.InputVerificationRefusal, match="target_last15_return"):
        verifier.verify_features(wrong, **data)


def test_zero_return_is_observed_but_zero_trigger_range_remains_unknown():
    data = feature_inputs(5)
    data["setup_id"] = "EA-B"
    for symbol, session in data["sessions"].items():
        data["sessions"][symbol] = replace(session, minutes={at: (Decimal(100),) * 4 for at in session.minutes})
    result = producer.prepare_features(**data)
    verifier.verify_features(result, **data)
    assert result.features["setup_is_A"].value == Fraction(0)
    assert result.features["peer_first30_return"].value == Fraction(0)
    assert result.features["trigger_close_location"].reasons == ("zero_trigger_range",)
    with pytest.raises(verifier.InputVerificationRefusal, match="trigger_close_location"):
        verifier.verify_features(changed_feature(result, "trigger_close_location", value=Fraction(0), reasons=()), **data)


@pytest.mark.parametrize("name", verifier.INPUT_NAMES)
def test_every_ml_input_value_and_exact_type_are_checked(name):
    data = feature_inputs()
    result = producer.prepare_features(**data)
    with pytest.raises(verifier.InputVerificationRefusal, match=name):
        verifier.verify_features(changed_feature(result, name, value=result.features[name].value + 1), **data)
    with pytest.raises(verifier.InputVerificationRefusal, match=name):
        verifier.verify_features(changed_feature(result, name, value=float(result.features[name].value)), **data)


@pytest.mark.parametrize("field,value", [
    ("observation_end", datetime(2025, 9, 4, 10, 1, tzinfo=cc.KST)),
    ("available_at", datetime(2025, 9, 4, 10, 3, tzinfo=cc.KST)),
    ("retrieved_at", datetime(2025, 9, 4, 9, 59, tzinfo=cc.KST)),
    ("source", "changed"), ("is_final", True), ("data_vintage", "c" * 64),
    ("evidence_level", "observed"),
])
def test_all_output_provenance_fields_checked(field, value):
    data = feature_inputs()
    result = producer.prepare_features(**data)
    records = list(result.windows)
    records[0] = replace(records[0], provenance=replace(records[0].provenance, **{field: value}))
    with pytest.raises(verifier.InputVerificationRefusal, match=field):
        verifier.verify_features(replace(result, windows=tuple(records)), **data)
    feature = result.features["peer_first30_return"]
    provenance = (replace(feature.provenance[0], **{field: value}), *feature.provenance[1:])
    with pytest.raises(verifier.InputVerificationRefusal, match=field):
        verifier.verify_features(changed_feature(result, "peer_first30_return", provenance=provenance), **data)


@pytest.mark.parametrize("mutate,path", [
    (lambda r: replace(r, fixed_peers=r.fixed_peers[:-1]), "fixed_peers"),
    (lambda r: replace(r, common_peers=r.common_peers[:-1]), "common_peers"),
    (lambda r: replace(r, observed_peers_by_window={**r.observed_peers_by_window, "last15": ()}), "last15"),
    (lambda r: replace(r, windows=r.windows[:-1]), "windows"),
    (lambda r: replace(r, windows=(replace(r.windows[0], start_at=r.windows[0].start_at + cc.MINUTE), *r.windows[1:])), "start_at"),
    (lambda r: replace(r, windows=(replace(r.windows[0], minute_issues=((r.windows[0].start_at, "missing"),)), *r.windows[1:])), "minute_issues"),
    (lambda r: replace(r, windows=(replace(r.windows[0], reasons=("missing",)), *r.windows[1:])), "reasons"),
    (lambda r: replace(r, source_evidence={}), "source_evidence"),
    (lambda r: replace(r, authentication_reference="changed"), "authentication_reference"),
    (lambda r: replace(r, official_index=True), "official_index"),
    (lambda r: replace(r, historical_timing_certified=True), "historical_timing_certified"),
    (lambda r: replace(r, actual_execution_authorized=True), "actual_execution_authorized"),
    (lambda r: replace(r, features={**r.features, "entry_OPEN": ea.Feature(Fraction(100))}), "features"),
])
def test_full_output_structure_is_checked(mutate, path):
    data = feature_inputs()
    result = producer.prepare_features(**data)
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        verifier.verify_features(mutate(result), **data)


@pytest.mark.parametrize("mutate,path", [
    (lambda d: d.update(inputs_authenticated=False), "authentication"),
    (lambda d: d.update(structural_stop=Decimal(98)), "structural_stop"),
    (lambda d: d.update(price_unit="USD"), "price_unit"),
    (lambda d: d.update(decision_at=d["decision_at"] + cc.MINUTE), "signal"),
    (lambda d: d.update(observed_end=d["target"].opening + 25 * cc.MINUTE), "signal"),
    (lambda d: d.update(observed_end=d["observed_end"].replace(tzinfo=None)), "aware_clock"),
    (lambda d: d.update(universe=replace(d["universe"], fixed_at=d["target"].opening + cc.MINUTE)), "membership_after_open"),
    (lambda d: d["sessions"].pop(SYMBOLS[-1]), "slots"),
    (lambda d: d["sources"][SYMBOLS[-1]].update(symbol=SYMBOLS[0]), "symbol"),
    (lambda d: d["sources"][SYMBOLS[-1]].update(input_rows=True), "input_rows"),
    (lambda d: d["sources"][SYMBOLS[-1]]["raw_chunk_metadata"].update(available_at="2025-09-04T10:05:00+09:00"), "available_at"),
    (lambda d: d["sources"][SYMBOLS[-1]]["raw_chunk_metadata"].update(retrieved_at="2025-09-04T09:59:00+09:00"), "retrieval_precedes"),
    (lambda d: d["sources"][SYMBOLS[-1]]["raw_chunk_metadata"].update(is_final=1), "is_final"),
    (lambda d: d["sources"][SYMBOLS[-1]]["raw_chunk_metadata"].update(evidence_level="observed"), "evidence_level"),
    (lambda d: d["sessions"][SYMBOLS[-1]].minutes.update({d["target"].opening: (100, 101, 99, 100)}), "decimal_ohlc"),
    (lambda d: d["sessions"][SYMBOLS[-1]].minutes.update({d["target"].opening + timedelta(seconds=1): None}), "minute_clock"),
])
def test_original_contract_mutations_fail_hard(mutate, path):
    data = feature_inputs()
    result = producer.prepare_features(**data)
    mutate(data)
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        verifier.verify_features(result, **data)


def test_future_price_suffix_and_prefix_are_not_consumed():
    data = feature_inputs()
    result = producer.prepare_features(**data)
    for symbol, session in data["sessions"].items():
        data["sessions"][symbol] = replace(session,
            minutes={at: prices if at < data["observed_end"] else object() for at, prices in session.minutes.items()},
            prefix=(object(),))
    verifier.verify_features(result, **data)


def test_invalid_numeric_ohlc_remains_unknown_with_its_original_reason():
    data = feature_inputs()
    at = data["target"].opening + 50 * cc.MINUTE
    data["sessions"][SYMBOLS[0]].minutes[at] = (Decimal("NaN"),) * 4
    result = producer.prepare_features(**data)
    verifier.verify_features(result, **data)
    assert result.features["target_last15_return"].reasons == ("invalid_price",)
    wrong = changed_feature(result, "target_last15_return", reasons=("missing",))
    with pytest.raises(verifier.InputVerificationRefusal, match="reasons"):
        verifier.verify_features(wrong, **data)


def saved_report():
    days = [DAY, "2025-09-08"]
    members = [{"symbol": f"KRX:{i:06d}", "rank": i + 1} for i in range(20)]
    sources = [{"session": day, "symbol": m["symbol"]} for day in days for m in members]
    report = {"mode": "fixed-ea-context-exploratory-comparison", "setup_id": "EA-A",
              "reference_sessions": days, "reference_weeks": ["2025-09-01", "2025-09-08"],
              "sources": sources, "volume_sources": deepcopy(sources), "source_target_matrix": {}, "variants": {}}
    for policy in verifier.POLICIES:
        memberships, rows = [], []
        for index, day in enumerate(days):
            memberships.append({"session": day, "selected_at": day + "T08:00:00+09:00",
                                "available_at": day + "T07:00:00+09:00", "targets": deepcopy(members)})
            for n, member in enumerate(members):
                state = "signal" if index == 0 and n < 5 else "unknown" if n % 2 else "known_no_signal"
                feature = {"value": {"numerator": ("2", "1", "0", "0", "0")[n], "denominator": "1"}, "reasons": []} if n < 5 else None
                basic, arm, features = "selected", "selected" if n == 0 else "rejected", {"relative_volume": feature, "forbidden_other_feature": object()}
                if n == 3:
                    feature.update(value=None, reasons=["missing_current_volume"])
                    arm = "unknown"
                if n == 4:
                    basic = arm = "unknown"
                    features = {"clock": {"value": None, "reasons": ["same_day_cap_infeasible"]}}
                if state != "signal":
                    basic = arm = "unknown" if state == "unknown" else "rejected"
                    features = {}
                rows.append({"session": day, "symbol": member["symbol"], "membership_day_index": index,
                    "membership_target": deepcopy(member), "source_index": index * 20 + n, "volume_source_index": index * 20 + n,
                    "signal": {"hypothesis_id": "H1-morning-trend-pullback-reclaim", "state": state, "reason": "invented",
                        "decision_at": day + "T10:17:00+09:00", "observed_through": None if state == "unknown" else day + "T10:15:00+09:00",
                        "signal_bar_start": day + "T10:10:00+09:00" if state == "signal" else None,
                        "signal_bar_end": day + "T10:15:00+09:00" if state == "signal" else None,
                        "setup_bar_end": day + "T10:10:00+09:00" if state == "signal" else None},
                    "arms": {"basic": basic, "volume_only": arm, "forbidden_other_arm": object()}, "features": features,
                    "outcome": object(), "common_context_observable": object(), "common_price_observable": object()})
        report["source_target_matrix"][policy] = {"days": memberships}
        report["variants"][policy] = {"days": rows, "statistics": object()}
    return report


class ReadGuard(Mapping):
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


def guard_outcomes(report):
    for variant in report["variants"].values():
        for index, row in enumerate(variant["days"]):
            row["arms"] = ReadGuard(row["arms"], {"forbidden_other_arm", "market_relative_only", "both"})
            row["features"] = ReadGuard(row["features"], {"forbidden_other_feature", "stock_minus_peer_return"})
            variant["days"][index] = ReadGuard(row, {"outcome", "entry_at", "cap_at", "structural_stop", "common_context_observable", "common_price_observable"})
    return report


def test_stored_volume_independence_all_states_and_zero_weeks(monkeypatch):
    original = guard_outcomes(saved_report())
    sidecar = volume.project_volume_inputs(original, inputs_authenticated=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("producer or label computation called")

    monkeypatch.setattr(volume, "project_volume_inputs", forbidden)
    monkeypatch.setattr(eb, "build_experiment", forbidden)
    monkeypatch.setattr(eb.signals, "detect_bars", forbidden)
    monkeypatch.setattr(eb.outcomes, "exploratory_outcome", forbidden)
    receipt = verifier.verify_volume_sidecar(sidecar, original, inputs_authenticated=True)
    assert receipt["targets_by_policy"] == {"D+1": 40, "D+2": 40}
    assert receipt["reference_week_count"] == 2
    assert not receipt["volume_availability_reauthenticated"]
    assert "stored_relative_volume_has_no_minute_provenance_or_available_at" in receipt["limitations"]
    rows = sidecar["variants"]["D+1"]["days"]
    assert [r["common_control"] for r in rows[:5]] == [True, True, True, False, False]
    assert rows[2]["relative_volume"]["value"] == Fraction(0)
    assert sidecar["variants"]["D+1"]["by_week"]["2025-09-08"]["signals"] == 0


@pytest.mark.parametrize("mutate,path", [
    (lambda v: v["variants"]["D+1"]["days"].pop(), "days"),
    (lambda v: v["variants"]["D+1"]["days"][0].update(source_index=1), "source_index"),
    (lambda v: v["variants"]["D+1"]["days"][0].update(volume_source_index=True), "volume_source_index"),
    (lambda v: v["variants"]["D+1"]["days"][0].update(decision_at="2025-09-04T10:18:00+09:00"), "decision_at"),
    (lambda v: v["variants"]["D+1"]["days"][0]["membership_target"].update(rank=99), "rank"),
    (lambda v: v["variants"]["D+1"]["days"][0]["relative_volume"].update(value=Fraction(3)), "value"),
    (lambda v: v["variants"]["D+1"]["days"][0].update(volume_only="rejected"), "volume_only"),
    (lambda v: v["variants"]["D+1"]["days"][1].update(common_control=False), "common_control"),
    (lambda v: v["variants"]["D+1"]["days"][3]["relative_volume"].update(reasons=()), "reasons"),
    (lambda v: v["variants"]["D+1"]["counts"].update(targets=39), "targets"),
    (lambda v: v["variants"]["D+1"]["by_week"]["2025-09-08"].update(signals=1), "signals"),
    (lambda v: v["variants"]["D+1"]["by_session"].pop("2025-09-08"), "by_session"),
    (lambda v: v.update(volume_availability_reauthenticated=True), "volume_availability_reauthenticated"),
    (lambda v: v.update(limitations=()), "limitations"),
    (lambda v: v.update(reference_sessions=[DAY]), "reference_sessions"),
])
def test_all_sidecar_rows_counts_and_availability_claims_checked(mutate, path):
    original = saved_report()
    sidecar = volume.project_volume_inputs(original, inputs_authenticated=True)
    mutate(sidecar)
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        verifier.verify_volume_sidecar(sidecar, original, inputs_authenticated=True)


@pytest.mark.parametrize("mutate,path", [
    (lambda r: r["variants"]["D+1"]["days"].pop(), "missing_target"),
    (lambda r: r["sources"][0].update(symbol="KRX:999999"), "source_identity"),
    (lambda r: r["variants"]["D+1"]["days"][0]["signal"].update(decision_at="2025-09-04T10:18:00+09:00"), "signal_clocks"),
    (lambda r: r["source_target_matrix"]["D+1"]["days"][0].update(available_at="2025-09-04T11:00:00+09:00"), "available_after"),
    (lambda r: r["variants"]["D+1"]["days"][0].update(membership_day_index=True), "membership_index"),
    (lambda r: r["variants"]["D+1"]["days"][0]["arms"].update(volume_only="rejected"), "volume_arm"),
    (lambda r: r["variants"]["D+1"]["days"][0]["features"]["relative_volume"].update(value={"numerator": "4", "denominator": "2"}), "noncanonical"),
    (lambda r: r["variants"]["D+1"]["days"][0]["features"]["relative_volume"].update(available_at="invented"), "fields_changed"),
    (lambda r: r["variants"]["D+1"]["days"][3]["features"]["relative_volume"].update(reasons=[]), "unknown_without_reason"),
    (lambda r: r["sources"].append(dict(r["sources"][0])), "source_inventory"),
])
def test_original_report_corruption_is_not_accepted_as_unknown(mutate, path):
    original = saved_report()
    sidecar = volume.project_volume_inputs(original, inputs_authenticated=True)
    mutate(original)
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        verifier.verify_volume_sidecar(sidecar, original, inputs_authenticated=True)


def test_public_builder_json_producer_and_independent_verifier_integration(monkeypatch):
    # Reuse the existing PUBLIC invented raw-input factory, not its projection
    # assertions or any outcome-derived population. It has 21 days x 20 targets.
    from test_intraday_campaign_volume import public_builder_fixture

    upstream, prices, volumes = public_builder_fixture()
    built = eb.build_experiment(upstream, iter(prices), iter(volumes), setup_id="EA-A",
        inputs_authenticated=True, authorization_reference="invented-independent-volume-integration")
    encoded = json.dumps(built, default=lambda value: value.isoformat()
                         if type(value) is date else json.loads(cc._canonical(value)))
    original = json.loads(encoded)
    sidecar = volume.project_volume_inputs(original, inputs_authenticated=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("verifier delegated to producer")

    monkeypatch.setattr(volume, "project_volume_inputs", forbidden)
    monkeypatch.setattr(eb, "build_experiment", forbidden)
    receipt = verifier.verify_volume_sidecar(sidecar, guard_outcomes(original), inputs_authenticated=True)
    assert receipt["targets_by_policy"] == {"D+1": 420, "D+2": 420}
    assert sidecar["variants"]["D+1"]["counts"]["common_control"] == 3
    assert sidecar["variants"]["D+1"]["counts"]["selected"] == 1


def test_volume_caller_authentication_is_required():
    original = saved_report()
    sidecar = volume.project_volume_inputs(original, inputs_authenticated=True)
    with pytest.raises(verifier.InputVerificationRefusal, match="caller_authentication_required"):
        verifier.verify_volume_sidecar(sidecar, original, inputs_authenticated=False)


def test_compact_context_combines_raw_and_typed_checks_once(monkeypatch):
    from research import intraday_campaign_context_readiness as readiness

    data = feature_inputs()
    result = producer.prepare_features(**data)
    sources = list(data["sources"].values())
    context = readiness.serialize_context(result, {symbol: index for index, symbol in enumerate(data["universe"].members)}, 0)
    original = verifier._expected_features
    calls = []

    def counted(**kwargs):
        calls.append(1)
        return original(**kwargs)

    monkeypatch.setattr(verifier, "_expected_features", counted)
    monkeypatch.setattr(producer, "prepare_features", lambda *a, **k: pytest.fail("producer replay"))
    monkeypatch.setattr(readiness, "serialize_context", lambda *a, **k: pytest.fail("producer serialization replay"))
    receipt = verifier.verify_compact_context(context, result, original_inputs=data, sources=sources, membership_day_index=0)
    assert calls == [1] and receipt["verified"]


@pytest.mark.parametrize("mutate,path", [
    (lambda c: c["features"]["target_last15_return"].update(value={"numerator": "2", "denominator": "100"}), "value"),
    (lambda c: c["features"]["peer_first30_return"]["provenance_window_indices"].reverse(), "provenance_window_indices"),
    (lambda c: c["features"]["target_last15_return"].update(universe_provenance=True), "universe_provenance"),
    (lambda c: c["features"]["setup_is_A"].update(available_at="2025-09-04T10:03:00+09:00"), "available_at"),
    (lambda c: c["windows"][0].update(source_index=0), "source_index"),
    (lambda c: c["windows"][0].update(name="last15"), "name"),
    (lambda c: c["window_ranges"]["last15"].update(end_at="2025-09-04T10:01:00+09:00"), "end_at"),
    (lambda c: c["source_indices"].reverse(), "source_indices"),
    (lambda c: c["fixed_peer_source_indices"].pop(), "fixed_peer_source_indices"),
    (lambda c: c["membership_provenance_ref"].update(day_index=1), "day_index"),
    (lambda c: c.update(entry_OPEN=100), "mapping_fields"),
])
def test_compact_publication_values_refs_intervals_and_extra_fields_fail(mutate, path):
    from research import intraday_campaign_context_readiness as readiness

    data = feature_inputs()
    result = producer.prepare_features(**data)
    sources = list(data["sources"].values())
    context = readiness.serialize_context(result, {symbol: index for index, symbol in enumerate(data["universe"].members)}, 0)
    mutate(context)
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        verifier.verify_compact_context(context, result, original_inputs=data, sources=sources, membership_day_index=0)


def test_compact_gap_intervals_are_lossless_canonical_and_verified():
    from research import intraday_campaign_context_readiness as readiness

    data = feature_inputs()
    symbol = SYMBOLS[1]
    session = data["sessions"][symbol]
    start = session.opening + 10 * cc.MINUTE
    session.minutes[start] = session.minutes[start + cc.MINUTE] = None
    data["sessions"][symbol] = replace(session, gaps=({"start": start, "end": start + 2 * cc.MINUTE, "state": "conflict"},))
    result = producer.prepare_features(**data)
    sources = list(data["sources"].values())
    context = readiness.serialize_context(result, {symbol: index for index, symbol in enumerate(data["universe"].members)}, 0)
    intervals = context["windows"][0]["minute_issue_intervals"]
    assert intervals == [{"start_at": start.isoformat(), "end_at": (start + 2 * cc.MINUTE).isoformat(), "state": "conflict"}]
    verifier.verify_compact_context(context, result, original_inputs=data, sources=sources, membership_day_index=0)
    intervals[0]["end_at"] = (start + cc.MINUTE).isoformat()
    with pytest.raises(verifier.InputVerificationRefusal, match="end_at"):
        verifier.verify_compact_context(context, result, original_inputs=data, sources=sources, membership_day_index=0)


@pytest.mark.parametrize("feature_name", [
    "target_last15_return", "structural_stop_gap_from_signal_close", "trigger_close_location",
    "peer_last15_return",
])
@pytest.mark.parametrize("observed_minutes", [30, 60])
def test_semantic_window_refs_cannot_alias_same_end_missing_or_different_windows(feature_name, observed_minutes, monkeypatch):
    from research import intraday_campaign_context_readiness as readiness

    data = feature_inputs(5)
    data["observed_end"] = data["target"].opening + observed_minutes * cc.MINUTE
    data["decision_at"] = data["observed_end"] + 2 * cc.MINUTE
    lose(data, SYMBOLS[0], 10, "missing")
    result = producer.prepare_features(**data)
    sources = list(data["sources"].values())
    context = readiness.serialize_context(result, {symbol: index for index, symbol in enumerate(data["universe"].members)}, 0)
    features = context["features"]
    # Five peers each have first30/open/last15 windows (0..14), followed by
    # the target's open/last15/trigger5/signal_close windows (15..18).
    assert features["target_open_to_signal_return"]["value"] is None
    assert features["target_open_to_signal_return"]["provenance_window_indices"] == [15]
    assert context["windows"][15]["reasons"] == ["missing"]
    for name, index, value in (
        ("target_last15_return", 16, Fraction(1, 100)),
        ("structural_stop_gap_from_signal_close", 18, Fraction(5, 202)),
        ("trigger_close_location", 17, Fraction(2, 3)),
    ):
        assert features[name]["value"] == {"numerator": str(value.numerator), "denominator": str(value.denominator)}
        assert features[name]["provenance_window_indices"] == [index]
        assert context["windows"][index]["reasons"] == []
    assert features["peer_first30_return"]["provenance_window_indices"] == [0, 3, 6, 9, 12]
    assert features["peer_open_to_signal_return"]["provenance_window_indices"] == [1, 4, 7, 10, 13]
    assert features["peer_last15_return"]["provenance_window_indices"] == [2, 5, 8, 11, 14]
    original = verifier._expected_features
    calls = []

    def counted(**kwargs):
        calls.append(1)
        return original(**kwargs)

    monkeypatch.setattr(verifier, "_expected_features", counted)
    verifier.verify_compact_context(context, result, original_inputs=data, sources=sources, membership_day_index=0)
    assert calls == [1]
    features[feature_name]["provenance_window_indices"] = (
        [1, 4, 7, 10, 13] if feature_name == "peer_last15_return" else [15])
    with pytest.raises(verifier.InputVerificationRefusal, match=f"{feature_name}.*provenance_window_indices"):
        verifier.verify_compact_context(context, result, original_inputs=data, sources=sources, membership_day_index=0)


@pytest.fixture(scope="module")
def full_readiness():
    from research import intraday_campaign_context_readiness as readiness
    from test_intraday_campaign_context_readiness import fixture, project

    a, b, chunks = fixture(sessions=2, churn=True)
    pa, pb = project(a, b)
    captured = []
    original = verifier.ReadinessAccumulator

    class Capture(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.append(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verifier, "ReadinessAccumulator", Capture)
        report = readiness.build_readiness(pa, pb, iter(chunks), inputs_authenticated=True,
                                          authentication_reference="invented-independent-final-output")
    assert len(captured) == 1
    return report, captured[0], a, b, pa, pb


def test_original_projection_verification_never_reads_outcomes_or_other_features(full_readiness):
    _, _, a, b, pa, pb = full_readiness
    for original, projected in ((deepcopy(a), pa), (deepcopy(b), pb)):
        for variant in original["variants"].values():
            for index, row in enumerate(variant["days"]):
                row["features"] = ReadGuard(row["features"], set(row["features"]) - {"relative_volume", "clock"})
                row["arms"] = ReadGuard(row["arms"], set(row["arms"]) - {"basic", "volume_only"})
                variant["days"][index] = ReadGuard(row, {"outcome", "common_context_observable", "common_price_observable", "entry_at", "cap_at"})
        receipt = verifier.verify_readiness_projection(projected, original, inputs_authenticated=True,
                                                       authentication_reference=projected.authentication_reference)
        assert receipt["verified"]


@pytest.mark.parametrize("field,mutate,path", [
    ("target_rows", lambda v: v["D+1"][0].update(source_index=1), "source_index"),
    ("target_rows", lambda v: v["D+1"][0].update(structural_stop=Fraction(1)), "structural_stop"),
    ("target_rows", lambda v: v["D+1"][0].update(signal_state="unknown"), "signal_state"),
    ("target_rows", lambda v: v["D+1"].reverse(), "session|original_row_index|symbol"),
    ("target_rows", lambda v: v["D+1"].pop(), "sequence_length"),
    ("request_metadata", lambda v: v["target_counts_by_policy"].update({"D+1": 0}), "target_counts"),
    ("account_projection", lambda v: v["sources"][0].update(input_rows=0), "input_rows"),
    ("account_projection", lambda v: v.update(old_outcome="unknown"), "mapping_fields"),
    ("volume_sidecar", lambda v: v["variants"]["D+1"]["counts"].update(selected=99), "selected"),
])
def test_readiness_projection_corruption_and_row_order_cannot_pass(full_readiness, field, mutate, path):
    _, _, original, _, projected, _ = full_readiness

    def mutable(value):
        if isinstance(value, Mapping):
            return {key: mutable(item) for key, item in value.items()}
        if type(value) in (list, tuple):
            return [mutable(item) for item in value]
        return value

    value = mutable(getattr(projected, field))
    mutate(value)
    changed = replace(projected, **{field: verifier._frozen_tree(value)})
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        verifier.verify_readiness_projection(changed, original, inputs_authenticated=True,
                                             authentication_reference=projected.authentication_reference)


@pytest.mark.parametrize("mutate,path", [
    (lambda r: r.update(authentication_reference="changed"), "authentication_reference"),
    (lambda r: r.update(future_label=0), "report_fields"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["days"].reverse(), "days"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["days"][0]["context"]["windows"][0].update(source_index=0), "days"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["days"][0]["input_support"].update(P05_market_inputs=False), "days"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["counts"].update(signals=0), "counts"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["feature_counts"]["setup_is_A"].update(observed=0), "feature_counts"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["support_counts"]["P04_volume_inputs"].update(observed=0), "support_counts"),
    (lambda r: r["variants"]["D+1"]["setups"]["EA-A"]["by_week"].clear(), "by_week"),
    (lambda r: r["variants"]["D+1"]["training_feature_support"]["by_session"][DAY]["past_training_feature_support"]["combined"].update(feature_complete_candidates=1), "training_feature_support"),
    (lambda r: r["variants"]["D+1"]["training_feature_support"]["expanding_blocks"][0].update(past_training_reference_session_count=101), "training_feature_support"),
    (lambda r: r["input_evidence"].update(consumed_price_rows=0), "input_evidence"),
    (lambda r: r["sources"][0].update(input_rows=0), "sources"),
    (lambda r: r["independent_verification"].update(verified_target_rows=1), "independent_verification"),
])
def test_final_report_rechecks_hashes_masks_counts_chronology_and_arbitrary_fields(full_readiness, mutate, path):
    original_report, accumulator, *_ = full_readiness
    report = deepcopy(original_report)
    mutate(report)
    with pytest.raises(verifier.InputVerificationRefusal, match=path):
        accumulator.verify_report(report)


def test_accumulator_cannot_certify_an_unconsumed_or_row_incomplete_stream(full_readiness):
    report, _, _, _, pa, pb = full_readiness
    accumulator = verifier.ReadinessAccumulator(pa, pb, authentication_reference=report["authentication_reference"])
    with pytest.raises(verifier.InputVerificationRefusal, match="stream_not_complete"):
        accumulator.verify_report(report)
    first = report["reference_sessions"][0]
    sources = {source["symbol"]: source for source in report["sources"] if source["session"] == first}
    day = {"session": first, "session_open": cc.session_open(first), "session_close": cc.session_close(first),
           "original_sources": sources, "original_sessions": dict.fromkeys(sources),
           "consumed_target_chunks": len(sources), "consumed_price_rows": sum(source["input_rows"] for source in sources.values()),
           "original_union_complete": False}
    accumulator.add_day(day)
    with pytest.raises(verifier.InputVerificationRefusal, match="rows_missing"):
        accumulator.finish_day()


def test_independent_training_support_excludes_each_test_day_from_its_past():
    accumulator = verifier.ReadinessAccumulator.__new__(verifier.ReadinessAccumulator)
    reference = [(date(2025, 9, 4) + timedelta(days=index)).isoformat() for index in range(257)]
    accumulator.reference = reference
    accumulator.weeks = sorted({verifier._week(day) for day in reference})
    accumulator.records = {}
    for setup in ("EA-A", "EA-B"):
        records = {}
        for index, day in enumerate(reference):
            count = 1 if setup == "EA-A" else 2 if index % 2 == 0 else 0
            for _ in range(count):
                records[len(records)] = {"session": day, "support": {"P09_P10_all_ten_inputs": True}}
        accumulator.records[("D+1", setup)] = records
    result = accumulator._training("D+1")
    assert result["initial_reference_sessions"] == reference[:100]
    assert [len(block["test_reference_sessions"]) for block in result["expanding_blocks"]] == [32, 32, 31, 31, 31]
    assert [block["past_training_reference_session_count"] for block in result["expanding_blocks"]] == [100, 132, 164, 195, 226]
    assert result["expanding_blocks"][0]["past_training_feature_support"]["combined"] == {
        "feature_complete_candidates": 200, "distinct_feature_complete_sessions": 100}
    assert result["expanding_blocks"][0]["test_feature_support"]["combined"] == {
        "feature_complete_candidates": 64, "distinct_feature_complete_sessions": 32}
    assert result["by_session"][reference[0]]["past_training_feature_support"]["combined"]["feature_complete_candidates"] == 0
    assert result["by_session"][reference[100]]["past_training_feature_support"]["combined"]["feature_complete_candidates"] == 200
