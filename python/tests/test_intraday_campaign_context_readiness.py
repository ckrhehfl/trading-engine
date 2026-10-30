"""Explicit invented-only EC context report checks; no IO or actual inputs."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from fractions import Fraction
import json
import weakref

import pytest

from research import intraday_campaign_context_readiness as readiness
from test_intraday_campaign_readiness import reports


eb, cc = readiness.eb, readiness.cc


def bind_sources(a, b, chunks):
    sources = [eb._source(chunk) for chunk in chunks]
    indices = {(source["session"], source["symbol"]): index for index, source in enumerate(sources)}
    for report in (a, b):
        report["sources"] = deepcopy(sources)
        report["volume_sources"] = deepcopy(sources) if report["setup_id"] == "EA-A" else []
        for policy in readiness.POLICIES:
            members = report["source_target_matrix"][policy]["days"]
            for row in report["variants"][policy]["days"]:
                day, symbol = row["session"], row["symbol"]
                row["source_index"] = indices[(day, symbol)]
                row["volume_source_index"] = indices[(day, symbol)] if report["setup_id"] == "EA-A" else None
                peers = [member["symbol"] for member in members[row["membership_day_index"]]["targets"] if member["symbol"] != symbol]
                row["fixed_peers"] = peers
                row["peer_source_indices"] = {peer: indices[(day, peer)] for peer in peers}


def fixture(sessions=1, *, churn=False):
    a, b = reports(sessions=sessions)
    if churn:
        for report in (a, b):
            for membership in report["source_target_matrix"]["D+2"]["days"]:
                membership["targets"][-1]["symbol"] = "KRX:000021"
            for row in report["variants"]["D+2"]["days"]:
                if row["symbol"] == "KRX:000020":
                    row["symbol"] = row["membership_target"]["symbol"] = "KRX:000021"
    union = {}
    for membership_policy in a["source_target_matrix"].values():
        for membership in membership_policy["days"]:
            union.setdefault(membership["session"], set()).update(member["symbol"] for member in membership["targets"])
    chunks = []
    for day, symbols in union.items():
        opening = cc.session_open(day)
        for index, symbol in enumerate(sorted(symbols)):
            chunks.append({"session": day, "symbol": symbol, "observation_date": day,
                "available_at": None, "retrieved_at": "2026-10-20T00:00:00+00:00",
                "source": "collector klines", "is_final": None, "evidence_level": "assumed",
                "rows": [{"symbol": symbol, "interval": "1m",
                    "open_time_ms": int((opening + minute * cc.MINUTE).timestamp()) * 1000,
                    "open": "100", "high": "102", "low": "99", "close": "101",
                    "fetched_at": "2026-10-20T00:00:00+00:00"} for minute in range(90)]})
    for report in (a, b):
        for variant in report["variants"].values():
            for row in variant["days"]:
                signal = row["signal"]["state"] == "signal"
                row["structural_stop"] = {"numerator": "199", "denominator": "2"} if signal else None
                if signal and report["setup_id"] == "EA-A":
                    feature = row["features"]["relative_volume"]
                    if feature["value"] is not None:
                        feature["value"] = {"numerator": "2" if row["arms"]["volume_only"] == "selected" else "0", "denominator": "1"}
    bind_sources(a, b, chunks)
    return a, b, chunks


def project(a, b):
    return tuple(readiness.project_readiness_inputs(report, setup, inputs_authenticated=True,
        authentication_reference="invented-whole-original-" + setup) for report, setup in ((a, "EA-A"), (b, "EA-B")))


def build(a, b, chunks, **kwargs):
    pa, pb = project(a, b)
    return readiness.build_readiness(pa, pb, iter(chunks), inputs_authenticated=True,
                                    authentication_reference="invented-authenticated-union", **kwargs)


def first_signal(report, policy="D+1", setup="EA-A"):
    return next(row for row in report["variants"][policy]["setups"][setup]["days"] if row["signal_state"] == "signal")


def test_full_population_source_refs_independent_counts_and_json():
    a, b, chunks = fixture(churn=True)
    before = deepcopy((a, b, chunks))
    report = build(a, b, chunks)
    assert (a, b, chunks) == before
    assert report["input_evidence"] == {"consumed_reference_sessions": 1,
        "consumed_original_target_chunks": 21, "consumed_price_rows": 21 * 90,
        "original_union_complete": True, "independently_verified_signals": 64}
    assert report["independent_verification"]["verified"] is True
    assert report["minimum_common_peers"] == 5
    assert report["sources"] == a["sources"] == b["sources"]
    assert report["source_target_matrix"] == a["source_target_matrix"]
    assert report["upstream_calendar"] == a["upstream_calendar"]
    for policy in readiness.POLICIES:
        for setup in readiness.SETUPS:
            variant = report["variants"][policy]["setups"][setup]
            assert variant["counts"] == {"targets": 20, "signals": 16, "known_no_signal": 2, "unknown_signals": 2}
            assert variant["common_peer_count_distribution"] == {"19": 16}
            assert variant["peer_quorum"] == {"met": 16, "not_met": 0, "not_applicable": 4}
            assert variant["feature_counts"]["trigger_close_location"] == {"observed": 16, "unknown": 0, "not_applicable": 4}
            assert {row["signal_state"] for row in variant["days"] if row["context"] is None} == {"known_no_signal", "unknown"}
    first = first_signal(report)
    assert first["context"]["features"]["target_last15_return"]["value"] == {"numerator": "1", "denominator": "100"}
    assert first["input_support"]["P05_market_inputs"] is True
    assert first["input_support"]["P06_volume_and_market_inputs"] is True
    assert first["context"]["features"]["peer_first30_return"]["available_at"] == first["decision_at"]
    encoded = json.dumps(report, allow_nan=False)
    for forbidden in ('"gross"', '"primary_net"', '"entry_open"', '"exit_open"', '"statistics"', '"account_projection"', '"minute_bars"'):
        assert forbidden not in encoded
    assert not report["performance_computed"] and not report["strategy_pass"]
    assert report["model_fits"] == report["policy_evaluations"] == 0
    assert not report["volume_availability_reauthenticated"]


def test_projection_is_deep_immutable_and_only_stream_geometry():
    a, b, _ = fixture()
    pa, _ = project(a, b)
    with pytest.raises(FrozenInstanceError):
        pa.setup_id = "EA-B"
    with pytest.raises(TypeError):
        pa.target_rows["D+1"][0]["symbol"] = "KRX:999999"
    assert set(pa.account_projection) == {"mode", "setup_id", "reference_sessions", "reference_weeks",
        "source_target_matrix", "upstream_calendar", "sources", "volume_sources", "variants"}
    day = a["reference_sessions"][0]
    assert all(set(candidate) == {"symbol"} for candidate in pa.account_projection["variants"]["D+1"]["dates"][day]["candidates"])
    a["variants"]["D+1"]["days"][0]["membership_target"]["rank"] = 999
    assert pa.target_rows["D+1"][0]["membership_target"]["rank"] == 1


def test_original_arbitrary_row_order_preserved_separate_from_stream_geometry():
    a, b, chunks = fixture()
    for original in (a, b):
        for variant in original["variants"].values():
            variant["days"].reverse()
    report = build(a, b, chunks)
    for policy in readiness.POLICIES:
        for setup, original in (("EA-A", a), ("EA-B", b)):
            rows = report["variants"][policy]["setups"][setup]["days"]
            assert [row["symbol"] for row in rows] == [row["symbol"] for row in original["variants"][policy]["days"]]
            assert [row["original_row_index"] for row in rows] == list(range(20))


def test_nonsignal_members_supply_peer_windows_and_policy_membership_churn():
    a, b, chunks = fixture(churn=True)
    report = build(a, b, chunks)
    for policy in readiness.POLICIES:
        row = first_signal(report, policy)
        common = {report["sources"][index]["symbol"] for index in row["context"]["common_peer_source_indices"]}
        assert "KRX:000006" in common  # stored known_no_signal
        assert "KRX:000007" in common  # stored unknown detector state
        assert ("KRX:000020" in common) is (policy == "D+1")
        assert ("KRX:000021" in common) is (policy == "D+2")


def test_future_outcome_metadata_and_poisoned_profit_fields_cannot_change_admission():
    a, b, chunks = fixture()
    baseline = build(a, b, chunks)
    for original in (a, b):
        for variant in original["variants"].values():
            variant["statistics"] = object()
            for row in variant["days"]:
                if row["outcome"] is not None:
                    row["outcome"].update(status="unknown", reason="required_path_missing", exit_at=None,
                        gross=object(), primary_net=object(), entry_open=object(), exit_open=object())
                    row["common_price_observable"] = False
    assert build(a, b, chunks) == baseline


@pytest.mark.parametrize("complete_peers", [4, 5])
def test_quorum_four_five_with_missing_prefix_and_independent_feature_masks(complete_peers):
    a, b, chunks = fixture()
    # Retain exactly the named number of peers for the first target. Other
    # stored signals still survive; target-window failures do not erase them.
    for chunk in chunks[complete_peers + 1:]:
        chunk["rows"] = [row for row in chunk["rows"] if row["open_time_ms"] != chunk["rows"][10]["open_time_ms"]]
    chunks[0]["rows"].pop(10)
    bind_sources(a, b, chunks)
    report = build(a, b, chunks)
    row = first_signal(report)
    assert len(row["context"]["common_peer_source_indices"]) == complete_peers
    assert row["context"]["features"]["target_open_to_signal_return"]["value"] is None
    assert row["context"]["features"]["target_last15_return"]["value"] is not None
    assert (row["context"]["features"]["peer_last15_return"]["value"] is not None) is (complete_peers == 5)
    assert row["input_support"]["P05_market_inputs"] is False
    assert row["input_support"]["P04_volume_inputs"] is True
    assert row["input_support"]["P08_router_inputs"] is (complete_peers == 5)
    assert report["variants"]["D+1"]["setups"]["EA-A"]["counts"]["signals"] == 16


@pytest.mark.parametrize("observed_minutes", [30, 60])
def test_feature_references_follow_exact_scientific_window_names(observed_minutes):
    from test_intraday_campaign_features import fixture as feature_fixture, lose

    data = feature_fixture()
    opening = data["target"].opening
    data.update(observed_end=opening + observed_minutes * cc.MINUTE,
                decision_at=opening + (observed_minutes + 2) * cc.MINUTE)
    lose(data, data["target"].symbol, 10)
    prepared = readiness.features.prepare_features(**data)
    sources = list(data["sources"].values())
    source_indices = {source["symbol"]: index for index, source in enumerate(sources)}
    context = readiness.serialize_context(prepared, source_indices, 0)

    expected_target_windows = {
        "target_open_to_signal_return": "open_to_signal",
        "target_last15_return": "last15",
        "structural_stop_gap_from_signal_close": "signal_close",
        "trigger_close_location": "trigger5",
    }
    for name, window_name in expected_target_windows.items():
        feature = context["features"][name]
        assert len(feature["provenance_window_indices"]) == 1
        window = context["windows"][feature["provenance_window_indices"][0]]
        assert window["name"] == window_name
        assert window["source_index"] == source_indices[data["target"].symbol]
        assert (feature["value"] is None) is (name == "target_open_to_signal_return")
        assert (window["value"] is None) is (name == "target_open_to_signal_return")
        assert bool(window["minute_issue_intervals"]) is (name == "target_open_to_signal_return")

    for window_name in readiness.features.WINDOWS:
        feature = context["features"][f"peer_{window_name}_return"]
        windows = [context["windows"][index] for index in feature["provenance_window_indices"]]
        assert [window["name"] for window in windows] == [window_name] * len(prepared.common_peers)
        assert [window["source_index"] for window in windows] == [source_indices[symbol] for symbol in prepared.common_peers]
    for name in ("setup_is_A", "fraction_of_session_elapsed"):
        assert context["features"][name]["provenance_window_indices"] == []
        assert not context["features"][name]["universe_provenance"]
    count_feature = context["features"]["common_peer_count_divided_by_original_peer_count"]
    assert count_feature["provenance_window_indices"] == []
    assert count_feature["universe_provenance"] is True
    readiness.verifier.verify_compact_context(context, prepared, original_inputs=data,
        sources=sources, membership_day_index=0)


def test_real_zero_return_remains_observed_zero_trigger_range_is_unknown():
    a, b, chunks = fixture()
    for chunk in chunks:
        for row in chunk["rows"]:
            row.update(open="100", high="100", low="100", close="100")
    bind_sources(a, b, chunks)
    report = build(a, b, chunks)
    row = first_signal(report)
    assert row["context"]["features"]["peer_last15_return"]["value"] == {"numerator": "0", "denominator": "1"}
    assert row["context"]["features"]["trigger_close_location"]["value"] is None
    assert row["context"]["features"]["trigger_close_location"]["reasons"] == ["zero_trigger_range"]
    assert row["input_support"]["P05_market_inputs"] is True
    assert row["input_support"]["P09_P10_all_ten_inputs"] is False


def test_zero_signal_week_and_training_feature_support_calendar_preserved():
    a, b, chunks = fixture(sessions=7)
    for original in (a, b):
        for variant in original["variants"].values():
            for row in variant["days"]:
                if row["session"] < "2025-09-08":
                    continue
                row["signal"].update(state="unknown", reason="missing_prefix", observed_through=None,
                    signal_bar_start=None, signal_bar_end=None, setup_bar_end=None)
                row.update(entry_at=None, cap_at=None, structural_stop=None, outcome=None, features={},
                    arms=dict.fromkeys(row["arms"], "unknown"), common_context_observable=False, common_price_observable=False)
    report = build(a, b, chunks)
    for policy in readiness.POLICIES:
        variant = report["variants"][policy]
        weekly = variant["setups"]["EA-A"]["by_week"]["2025-09-08"]
        assert weekly["counts"] == {"targets": 100, "signals": 0, "known_no_signal": 0, "unknown_signals": 100}
        assert weekly["support_counts"]["P05_market_inputs"] == {"observed": 0, "unknown": 0, "not_applicable": 100}
        training = variant["training_feature_support"]
        assert training["by_week"]["2025-09-08"]["combined"]["feature_complete_candidates"] == 0
        assert training["by_session"]["2025-09-04"]["past_training_feature_support"]["combined"]["feature_complete_candidates"] == 0
        assert len(training["expanding_blocks"]) == 5
        assert all(block["test_reference_sessions"] == [] for block in training["expanding_blocks"])


def test_first100_and_expanding_five_blocks_use_reference_calendar_only():
    reference = [(date(2025, 1, 1) + timedelta(days=index)).isoformat() for index in range(257)]
    setups = {setup: {"days": [{"session": day, "input_support": {"P09_P10_all_ten_inputs": index % 3 == 0}}
                              for index, day in enumerate(reference)]} for setup in readiness.SETUPS}
    weeks = sorted({eb._week(day) for day in reference})
    result = readiness.training_feature_support(setups, reference, weeks)
    assert len(result["initial_reference_sessions"]) == 100
    assert [len(block["test_reference_sessions"]) for block in result["expanding_blocks"]] == [32, 32, 31, 31, 31]
    assert [block["past_training_reference_session_count"] for block in result["expanding_blocks"]] == [100, 132, 164, 195, 226]
    assert result["initial_feature_support"]["combined"] == {"feature_complete_candidates": 68, "distinct_feature_complete_sessions": 34}
    assert result["by_session"][reference[3]]["past_training_feature_support"]["combined"] == {"feature_complete_candidates": 2, "distinct_feature_complete_sessions": 1}


def test_minute_issue_rle_roundtrip_and_mixed_states():
    opening = cc.session_open("2025-09-04")
    issues = tuple((opening + index * cc.MINUTE, state) for index, state in
                   ((0, "missing"), (1, "missing"), (3, "missing"), (4, "conflict"), (5, "conflict"), (6, "invalid_price")))
    encoded = readiness.coalesce_minute_issues(issues)
    assert len(encoded) == 4
    assert encoded[0] == {"start_at": opening.isoformat(), "end_at": (opening + 2 * cc.MINUTE).isoformat(), "state": "missing"}
    assert readiness.expand_minute_issue_intervals(encoded) == issues
    assert readiness.expand_minute_issue_intervals([]) == ()


@pytest.mark.parametrize("kind", ["overlap", "adjacent_equal", "subminute", "naive"])
def test_minute_issue_rle_invalid_geometry_is_refused(kind):
    opening = cc.session_open("2025-09-04")
    intervals = [{"start_at": opening.isoformat(), "end_at": (opening + cc.MINUTE).isoformat(), "state": "missing"}]
    if kind in {"overlap", "adjacent_equal"}:
        start = opening if kind == "overlap" else opening + cc.MINUTE
        intervals.append({"start_at": start.isoformat(), "end_at": (opening + 2 * cc.MINUTE).isoformat(), "state": "missing"})
    elif kind == "subminute":
        intervals[0]["end_at"] = (opening + timedelta(seconds=30)).isoformat()
    else:
        intervals[0]["start_at"] = opening.replace(tzinfo=None).isoformat()
    with pytest.raises(readiness.ContextReadinessRefusal):
        readiness.expand_minute_issue_intervals(intervals)


def test_combined_independent_check_reconstructs_each_signal_exactly_once(monkeypatch):
    a, b, chunks = fixture()
    expected_features = readiness.verifier._expected_features
    calls = []

    def counted(**kwargs):
        calls.append((kwargs["setup_id"], kwargs["target"].policy, kwargs["target"].symbol))
        return expected_features(**kwargs)

    def duplicate(*args, **kwargs):
        raise AssertionError("standalone verifier must not repeat combined reconstruction")

    monkeypatch.setattr(readiness.verifier, "_expected_features", counted)
    monkeypatch.setattr(readiness.verifier, "verify_features", duplicate)
    report = build(a, b, chunks)
    assert len(calls) == len(set(calls)) == report["input_evidence"]["independently_verified_signals"] == 64


def test_previous_day_sessions_are_released_before_next_day_is_consumed(monkeypatch):
    a, b, chunks = fixture(sessions=2)
    original_stream = readiness.account_inputs.iter_context_price_days
    checked = []

    def stream(*args, **kwargs):
        previous = []
        for day in original_stream(*args, **kwargs):
            assert all(reference() is None for reference in previous)
            checked.append(day["session"])
            previous = [weakref.ref(session) for session in day["original_sessions"].values()]
            yield day
            del day

    monkeypatch.setattr(readiness.account_inputs, "iter_context_price_days", stream)
    build(a, b, chunks)
    assert checked == a["reference_sessions"]


@pytest.mark.parametrize("layer", ["prepared", "compact", "summary"])
def test_independent_checks_reject_producer_and_serialization_and_final_count_errors(monkeypatch, layer):
    a, b, chunks = fixture()
    if layer == "prepared":
        original = readiness.features.prepare_features

        def wrong(**kwargs):
            prepared = original(**kwargs)
            feature_map = dict(prepared.features)
            feature = feature_map["target_last15_return"]
            feature_map["target_last15_return"] = replace(feature, value=feature.value + Fraction(1))
            return replace(prepared, features=feature_map)

        monkeypatch.setattr(readiness.features, "prepare_features", wrong)
    elif layer == "compact":
        original = readiness.serialize_context

        def wrong(*args, **kwargs):
            context = original(*args, **kwargs)
            context["windows"][0]["source_index"] = 0
            return context

        monkeypatch.setattr(readiness, "serialize_context", wrong)
    else:
        original = readiness.summarize_rows

        def wrong(rows):
            summary = original(rows)
            summary["counts"]["targets"] += 1
            return summary

        monkeypatch.setattr(readiness, "summarize_rows", wrong)
    with pytest.raises(readiness.verifier.InputVerificationRefusal):
        build(a, b, chunks)


@pytest.mark.parametrize("kind", ["missing", "extra", "source_changed", "order"])
def test_complete_stream_failure_never_returns_partial_report(kind):
    a, b, chunks = fixture()
    pa, pb = project(a, b)
    if kind == "missing":
        chunks.pop()
    elif kind == "extra":
        chunks.append(deepcopy(chunks[-1]))
    elif kind == "source_changed":
        chunks[-1]["rows"][0]["close"] = "100.5"
    else:
        chunks.reverse()
    with pytest.raises((readiness.account_inputs.AccountInputsRefusal, eb.ExperimentRefusal)):
        readiness.build_readiness(pa, pb, iter(chunks), inputs_authenticated=True, authentication_reference="invented")


@pytest.mark.parametrize("bad", [False, None, 1, "yes"])
def test_projection_requires_explicit_whole_original_authentication(bad):
    a, _, _ = fixture()
    with pytest.raises(readiness.ContextReadinessRefusal, match="authenticated"):
        readiness.project_readiness_inputs(a, "EA-A", inputs_authenticated=bad, authentication_reference="invented")


def test_public_eb_builder_json_project_authenticated_day_producer_and_verifier(monkeypatch):
    from test_intraday_campaign_volume import public_builder_fixture

    upstream, prices, volumes = public_builder_fixture()
    built = []
    for setup in readiness.SETUPS:
        raw = eb.build_experiment(upstream, iter(prices), iter(volumes) if setup == "EA-A" else None,
            setup_id=setup, inputs_authenticated=True, authorization_reference="invented-public-context-readiness")
        built.append(json.loads(json.dumps(raw, default=lambda value: value.isoformat()
            if type(value) is date else json.loads(cc._canonical(value)))))

    def forbidden(*args, **kwargs):
        raise AssertionError("readiness must not call old detector/outcome/context producer")

    for module, name in ((eb, "build_experiment"), (eb.signals, "detect_bars"),
                         (eb.outcomes, "exploratory_outcome"), (eb.ea, "_peer_mean")):
        monkeypatch.setattr(module, name, forbidden)
    report = build(*built, prices)
    for policy in readiness.POLICIES:
        variant = report["variants"][policy]["setups"]["EA-A"]
        assert variant["counts"]["targets"] == 420
        assert variant["counts"]["signals"] == 5
        final = [row for row in variant["days"][-20:] if row["signal_state"] == "signal"]
        assert len(final) == 4
        # Old unknown holding path/declined entry both retain their causal
        # context records; neither outcome can censor the input population.
        assert all(row["context"] is not None for row in final)
        assert len(report["volume_sidecar"]["variants"][policy]["days"]) == 420
    assert report["input_evidence"]["original_union_complete"] is True
