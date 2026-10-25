"""Invented authenticated-wire fixtures only; no market body or old replay."""
from copy import deepcopy
from fractions import Fraction
import inspect
import json
import math
import weakref

import pytest

from research import intraday_market_context_calibration as calibration
from research import intraday_market_context_readiness as market
from test_intraday_market_context_readiness import build_synthetic_fixture as _market_fixture
from test_intraday_breakout_loss_concentration import _set_return


def _spec():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE,
        "output_name": calibration.OUTPUT_NAME, "window": calibration.WINDOW,
        "source_roles": calibration.SOURCE_ROLES, "upstream_sha256": calibration.UPSTREAM_SHA256,
        "upstream_specification_sha256": calibration.UPSTREAM_SPECIFICATION_SHA256,
        "original_DK_sha256": calibration.ORIGINAL_DK_SHA256,
        "original_DK_specification_sha256": calibration.ORIGINAL_DK_SPECIFICATION_SHA256,
        "parameters": deepcopy(calibration.PARAMETERS), "resources": dict(calibration.RESOURCES)}


def _wire(source):
    source = deepcopy(source)
    source["specification_sha256"] = calibration.UPSTREAM_SPECIFICATION_SHA256
    events = [(policy, event) for policy in calibration.POLICIES
        for event in source["variants"][policy].pop("events")]
    return source, events


def fixture(*, full=False):
    dk, lines = _market_fixture(full=full)
    return dk, market.build_readiness(dk, lines())


def _result(dk, dq):
    header, events = _wire(dq)
    return calibration.build_calibration(calibration.project_dk(dk), header, iter(events))


def _controlled_mean(dq, values):
    """Invented completed atom evidence, never an attempt to authenticate bytes."""
    for policy in calibration.POLICIES:
        for event in dq["variants"][policy]["events"]:
            complete = [peer for peer in event["peers"] if peer["state"] == "complete"]
            for index, peer in enumerate(complete):
                peer.update(first_open="100", last_close=str(100 + values[index % len(values)]))


def test_fixed_registration_resources_and_small_dependency_boundary():
    calibration.validate_spec(_spec())
    assert calibration.NEXT_HYPOTHESIS == market.NEXT_HYPOTHESIS
    assert calibration.FLAGS["subgroup_return_means_calculated"] is False
    assert calibration.NEXT_HYPOTHESIS["other_conjuncts"] == []
    assert [calibration.RESOURCES[key] for key in ("cpu_seconds", "address_space_mib", "wall_seconds", "nice")] == [300, 512, 1200, 10]
    assert calibration.RESOURCES["maximum_source_bytes"] == 256 * 1024**2
    assert calibration.RESOURCES["maximum_original_source_bytes"] == calibration.RESOURCES["maximum_report_bytes"] == 64 * 1024**2
    source = inspect.getsource(calibration)
    assert not any(text in source for text in ("dl._event(", "dl._receipts(", "dm._feature(",
        "dm.build_calibration(", "market.build_readiness(", "import sqlite3", "read_bytes(", "gzip.open("))
    predicate = inspect.getsource(calibration._feature)
    assert not any(text in predicate for text in ("entry_open", "original_label", '["label"]', "breakout_bar", '["context"]'))


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(minimum_complete_peer_windows=4),
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec["parameters"]["next_hypothesis"].update(other_conjuncts=["CLV"]),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(extra=True),
])
def test_scope_refuses_before_projection_or_event_access(change):
    spec = _spec()
    change(spec)
    with pytest.raises(calibration.MarketContextCalibrationRefusal, match="specification_scope_mismatch"):
        calibration.Calibrator({}, specification=spec)


def test_projection_all_original_masks_labels_clocks_metadata_and_hashes():
    dk, dq = fixture()
    before = deepcopy(dk)
    projection = calibration.project_dk(dk)
    assert projection["source_identity"] == dq["source_identity"]
    assert projection["upstream_calendar"] == dk["upstream_calendar"]
    assert projection["source_target_matrix"] == dk["source_target_matrix"]
    for policy in calibration.POLICIES:
        actual = projection["variants"][policy]
        assert actual["population"] == dq["variants"][policy]["population"]
        assert actual["source_rows_sha256"] == dq["variants"][policy]["source_rows_sha256"]
        assert actual["source_variant_sha256"] == dq["variants"][policy]["source_variant_sha256"]
        for row, item in zip(dk["variants"][policy]["days"], actual["targets"], strict=True):
            assert item["source_row_sha256"] == market._digest(row)
            assert item["original_label"] == row["arms"]["basic"]["label"]
            assert item["signal_observable"] == row["signal_observable"]
            assert item["outcome_complete"] == row["outcome_complete"]
            assert item["exclusion_reasons"] == row["exclusion_reasons"]
            assert item["raw_chunk_metadata"] == row["raw_chunk_metadata"]
            assert "arms" not in item and "comparison" not in actual and "by_arm" not in actual
    assert dk == before
    projection["source_target_matrix"]["D+1"]["days"][0]["targets"][0]["rank"] = 999
    assert dk == before


def test_all_complete_peers_including_original_signal_unknown_members_select_exact_mean():
    dk, dq = fixture()
    _controlled_mean(dq, [1])
    result = _result(dk, dq)
    for policy in calibration.POLICIES:
        variant = result["variants"][policy]
        for event in dq["variants"][policy]["events"]:
            item = next(item for item in variant["target_states"]
                if (item["session"], item["symbol"]) == (event["session"], event["symbol"]))
            assert item["complete_peer_windows"] == event["complete_peer_windows"]
            assert len(event["peers"]) == 19
            if event["quorum_usable"]:
                assert item["state"] == "selected"
                assert item["observed_peer_mean_price_change"] == Fraction(1, 100)
            else:
                assert item["state"] == "unknown"
                assert item["observed_peer_mean_price_change"] is None
        first = dq["variants"][policy]["events"][0]
        assert first["complete_peer_windows"] == 19
        assert any(peer["member"]["rank"] >= 10 and peer["state"] == "complete" for peer in first["peers"])
    assert result["calibration_only"] and result["candidate_measured"]
    assert not result["comparison_performed"] and not result["new_labels_calculated"]
    assert result["new_performance_families"] == 0 and not result["upstream_statistics_recalculated"]


@pytest.mark.parametrize("value,state", [(0, "rejected"), (-1, "rejected"), (1, "selected")])
def test_strict_positive_exact_zero_negative_and_positive(value, state):
    dk, dq = fixture()
    _controlled_mean(dq, [value])
    result = _result(dk, dq)
    for policy in calibration.POLICIES:
        first = result["variants"][policy]["target_states"][0]
        assert first["state"] == state
        assert first["observed_peer_mean_price_change"] == Fraction(value, 100)


def test_exact_fraction_cancellation_and_tiny_positive_do_not_use_float_or_decimal_context():
    dk, dq = fixture()
    for policy in calibration.POLICIES:
        first = dq["variants"][policy]["events"][0]
        for peer in first["peers"]:
            peer.update(first_open="1", last_close="1")
        first["peers"][0].update(first_open="3", last_close="4")
        first["peers"][1].update(first_open="3", last_close="2")
        first["peers"][2].update(first_open="1", last_close="1." + "0" * 80 + "1")
    result = _result(dk, dq)
    first = result["variants"]["D+1"]["target_states"][0]
    assert first["state"] == "selected"
    assert first["observed_peer_mean_price_change"] == Fraction(1, 19 * 10**81)


def test_exact_five_is_known_four_is_unknown_never_imputed_zero():
    dk, dq = fixture()
    _controlled_mean(dq, [1])
    result = _result(dk, dq)
    for policy in calibration.POLICIES:
        states = [item for item in result["variants"][policy]["target_states"] if item["in_original_book"]]
        five = [item for item in states if item["complete_peer_windows"] == 5]
        four_or_less = [item for item in states if item["complete_peer_windows"] < 5]
        assert five and four_or_less
        assert all(item["state"] == "selected" for item in five)
        assert all(item["state"] == "unknown" and item["observed_peer_mean_price_change"] is None
            for item in four_or_less)
        assert result["variants"][policy]["requires_operator_choice_before_comparison"]
        assert "unknown_original_book_features" in result["variants"][policy]["comparison_choice_reasons"]


@pytest.mark.parametrize("change", [
    lambda event: event.update(source_row_sha256="a" * 64),
    lambda event: event.update(decision_at="2025-09-04T09:23:00+09:00"),
    lambda event: event.update(window_start="2025-09-04T09:06:00+09:00"),
    lambda event: event.update(entry_at="2025-09-04T09:26:00+09:00"),
    lambda event: event.update(decision_slack_seconds=False),
    lambda event: event.update(complete_peer_windows=5),
    lambda event: event.update(quorum_usable=False),
    lambda event: event["peers"].reverse(),
    lambda event: event["peers"].pop(),
    lambda event: event["peers"][0].update(data_vintage="b" * 64),
    lambda event: event["peers"][0]["member"].update(rank=20),
    lambda event: event["peers"][0].update(available_at="2025-09-04T09:23:00+09:00"),
    lambda event: event["peers"][0].update(decision_slack_seconds=False),
    lambda event: event["peers"][0].update(unknown_minute_buckets=1),
    lambda event: event["peers"][0]["observation_offsets_ms"].pop(),
    lambda event: event["peers"][0]["observation_offsets_ms"][0].append(5),
    lambda event: event["peers"][0].update(first_open="0"),
    lambda event: event["peers"][0].update(last_close="NaN"),
    lambda event: event.update(future_label={"price_change": "1"}),
])
def test_original_event_member_provenance_clock_and_atom_contradictions_refuse(change):
    dk, dq = fixture()
    event = deepcopy(dq["variants"]["D+1"]["events"][0])
    change(event)
    sink = calibration.Calibrator(calibration.project_dk(dk))
    with pytest.raises(calibration.MarketContextCalibrationRefusal):
        sink.accept_event("D+1", event)


@pytest.mark.parametrize("change", [
    lambda peer: peer["gaps"][0].update(minute="2025-09-11T09:20:00+09:00"),
    lambda peer: peer["gaps"][0].update(state="missing"),
    lambda peer: peer["gap_counts"].update(conflict=15),
    lambda peer: peer.update(first_open="100"),
    lambda peer: peer["gaps"][0].update(source_fetched_at=["2020-01-01T00:00:00+00:00"]),
])
def test_unknown_peer_gaps_cannot_hide_contradictions(change):
    dk, dq = fixture()
    first = dq["variants"]["D+1"]["events"]
    event = next(event for event in first if any(peer["gaps"] for peer in event["peers"]))
    peer = next(peer for peer in event["peers"] if peer["gaps"] and peer["gaps"][0]["state"] == "invalid_price")
    change(peer)
    header, events = _wire(dq)
    with pytest.raises(calibration.MarketContextCalibrationRefusal):
        calibration.build_calibration(calibration.project_dk(dk), header, events)


@pytest.mark.parametrize("change", [
    lambda header: header.update(specification_sha256="a" * 64),
    lambda header: header["source_identity"].update(canonical_source_sha256="a" * 64),
    lambda header: header["parameters"].update(minimum_complete_peer_windows=4),
    lambda header: header["next_hypothesis"].update(other_conjuncts=["CLV"]),
    lambda header: header["variants"]["D+1"]["population"].update(targets=79),
    lambda header: header["variants"]["D+1"]["summary"]["overall"].update(events=1),
    lambda header: header["input_evidence"].update(queried_rows=1),
    lambda header: header["input_evidence"].update(chunk_fingerprints_sha256="b" * 64),
])
def test_completed_header_identity_population_calendar_receipts_and_evidence_refuse(change):
    dk, dq = fixture()
    header, events = _wire(dq)
    change(header)
    with pytest.raises(calibration.MarketContextCalibrationRefusal):
        calibration.build_calibration(calibration.project_dk(dk), header, events)


def test_missing_duplicate_extra_out_of_order_and_second_finish_refuse():
    dk, dq = fixture()
    projection, (header, events) = calibration.project_dk(dk), _wire(dq)
    for altered in (events[:-1], events + events[-1:], [events[1], events[0]] + events[2:]):
        with pytest.raises(calibration.MarketContextCalibrationRefusal):
            calibration.build_calibration(projection, header, altered)
    sink = calibration.Calibrator(projection)
    for policy, event in events:
        sink.accept_event(policy, event)
    sink.finish(header)
    with pytest.raises(calibration.MarketContextCalibrationRefusal, match="already_finished"):
        sink.finish(header)
    with pytest.raises(calibration.MarketContextCalibrationRefusal, match="already_finished"):
        sink.accept_event(*events[0])


def test_no_event_or_parent_mutation_retention_and_output_has_no_outcome_mean():
    dk, dq = fixture()
    before_dk, before_dq = deepcopy(dk), deepcopy(dq)
    projection, (header, events) = calibration.project_dk(dk), _wire(dq)
    sink = calibration.Calibrator(projection)

    class Event(dict):
        pass

    event = Event(events[0][1])
    ref = weakref.ref(event)
    sink.accept_event(events[0][0], event)
    del event
    assert ref() is None
    for policy, event in events[1:]:
        sink.accept_event(policy, event)
    result = sink.finish(header)
    assert dk == before_dk and dq == before_dq
    assert result["source_identity"]["DQ_header_sha256"] == market._digest(header)
    assert result["source_readiness_summary"] == {policy: dq["variants"][policy]["summary"] for policy in calibration.POLICIES}
    text = json.dumps(result, default=str)
    assert not any(key in text for key in ('"gross_mean"', '"net_mean"', '"win_rate"', '"contrast"', '"p_value"', '"source_calibration_receipts"'))
    for variant in result["variants"].values():
        assert variant["frequency"]["upper_bound_is_estimate"] is False
        assert all(variant["reconciliation"].values())
        assert variant["frequency"]["zero_selected_weeks"] >= 1
        assert sum(cell["targets"] for cell in variant["by_week"].values()) == variant["population"]["targets"]


def test_selection_independent_of_outcomes_future_entry_and_clv():
    dk, dq = fixture()
    before = _result(dk, dq)
    for policy in calibration.POLICIES:
        for row in dk["variants"][policy]["days"]:
            if row["breakout_exit_policy"] is not None:
                _set_return(row, Fraction(1, 1000), entry_price="9973")
                row["arms"]["retest"]["path"]["breakout_bar"] = {"close": "MALFORMED_NOT_USED"}
    # Changing original source bytes changes its row hashes; regenerate only
    # invented DQ lineage while preserving all causal atoms and source masks.
    projection = calibration.project_dk(dk)
    dq["source_identity"] = projection["source_identity"]
    for policy in calibration.POLICIES:
        dq["variants"][policy]["source_rows_sha256"] = projection["variants"][policy]["source_rows_sha256"]
        for event in dq["variants"][policy]["events"]:
            row = next(row for row in projection["variants"][policy]["targets"]
                if (row["session"], row["symbol"]) == (event["session"], event["symbol"]))
            event["source_row_sha256"] = row["source_row_sha256"]
    after = _result(dk, dq)
    for policy in calibration.POLICIES:
        assert after["variants"][policy]["selected_coordinates"] == before["variants"][policy]["selected_coordinates"]
        assert after["variants"][policy]["population"]["state_counts"] == before["variants"][policy]["population"]["state_counts"]


def test_new_selected_own_dispersion_uses_all_sessions_and_never_old_sizing(monkeypatch):
    dk, dq = fixture()
    _controlled_mean(dq, [1])
    seen = []
    original = calibration.dm._dispersion

    def measured(records, reference, checkpoint):
        seen.append((deepcopy(records), list(reference)))
        return original(records, reference, checkpoint)

    monkeypatch.setattr(calibration.dm, "_dispersion", measured)
    result = _result(dk, dq)
    assert len(seen) == 2
    for policy, (records, reference) in zip(calibration.POLICIES, seen, strict=True):
        variant = result["variants"][policy]
        assert reference == dk["reference_sessions"]
        assert len(records) == variant["frequency"]["selected_events"]
        assert variant["own_label_dispersion"] == original(records, reference, lambda: None)
        assert variant["own_label_dispersion"]["mde_bps"] != 10.0


def test_undefined_sizing_completes_and_keeps_future_comparison_choice():
    dk, dq = fixture()
    _controlled_mean(dq, [0])
    result = _result(dk, dq)
    assert result["requires_operator_choice_before_comparison"]
    for variant in result["variants"].values():
        assert variant["own_label_dispersion"]["mde_bps"] is None
        assert not variant["sizing_passed"]
        assert "own_label_mde_undefined_or_nonfinite" in variant["comparison_choice_reasons"]


def test_checkpoints_interrupt_projection_and_stream_without_extra_access():
    dk, dq = fixture()
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("invented_budget_exhausted")

    with pytest.raises(RuntimeError, match="invented_budget_exhausted"):
        calibration.project_dk(dk, checkpoint=stop)
    projection, (header, events) = calibration.project_dk(dk), _wire(dq)
    calls = 0
    with pytest.raises(RuntimeError, match="invented_budget_exhausted"):
        calibration.build_calibration(projection, header, events, checkpoint=stop)


def test_full_invented_geometry_zero_weeks_and_all_denominators():
    dk, lines = _market_fixture(full=True)
    for policy in calibration.POLICIES:
        rows = dk["variants"][policy]["days"]
        originals = [row for row in rows if row["breakout_exit_policy"] is not None]
        candidates = [row for row in rows if row["breakout_exit_policy"] is None]
        unknown_count = 2609 if policy == "D+1" else 2611
        for index, row in enumerate(candidates):
            row["arms"]["basic"] = {"status": "unknown" if index < unknown_count else "known_no_signal", "event": None, "label": None}
            row["arms"]["retest"] = {"status": "known_no_signal", "event": None, "label": None}
            row.update(signal_observable=index >= unknown_count, outcome_complete=index >= unknown_count,
                exclusion_reasons=["basic:unknown_signal"] if index < unknown_count else [])
        for row in candidates[unknown_count:unknown_count + 12]:
            original = next(item for item in originals if item["session"] == row["session"])
            row["arms"]["basic"] = deepcopy(original["arms"]["basic"])
            row["arms"]["retest"] = {"status": "unknown", "event": None, "label": None}
            row.update(signal_observable=False, outcome_complete=False, exclusion_reasons=["retest:unknown_signal"])
    dq = market.build_readiness(dk, lines())
    result = _result(dk, dq)
    assert len(result["reference_sessions"]) == 257 and len(result["reference_weeks"]) == 56
    for policy in calibration.POLICIES:
        variant = result["variants"][policy]
        assert variant["population"]["targets"] == 5140
        assert variant["population"]["original_book_events"] == (1169 if policy == "D+1" else 1167)
        assert variant["population"]["basic_status_counts"]["unknown"] == (2609 if policy == "D+1" else 2611)
        assert variant["population"]["state_counts"]["outside_original_book"] == 12
        assert sum(variant["population"]["state_counts"].values()) == 5140
        assert variant["frequency"]["zero_selected_weeks"] > 0
        assert len(variant["by_week"]) == 56 and len(variant["by_session"]) == 257
        own = variant["own_label_dispersion"]
        assert own["reference_sessions"] == 257
        assert own["mde_bps"] is None or math.isfinite(own["mde_bps"])
