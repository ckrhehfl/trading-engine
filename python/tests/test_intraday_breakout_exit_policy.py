"""Invented DJ reports/raw rows only; no market, API, credentials or SSH."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
import json

import pytest

from research import intraday_breakout_exit_policy as policy
from test_intraday_breakout_path_diagnostics import (
    DAYS, SYMBOLS, _finish_parent, _pattern, build_synthetic_fixture, raw_rows, inputs, wire,
)


def _completed(dj_report):
    dj_report = wire(dj_report)
    dj_report["specification_sha256"] = policy.UPSTREAM_SPECIFICATION_SHA256
    dj_report["input_evidence"].update(
        source_sha256={"parent": policy.DJ_PARENT_SHA256, "archive": policy.INPUT_COMPRESSED_SHA256},
        source_roles=deepcopy(policy.dj.SOURCE_ROLES), authentication=policy.DJ_AUTHENTICATION)
    return dj_report


def build_synthetic_parent(days=DAYS):
    parent, chunks = build_synthetic_fixture(days)
    return _completed(policy.dj.build_diagnostics(parent, chunks)), chunks


def build_profile_fixture(days):
    """Invent full archive geometry and diverse exact price denominators.

    Generation happens outside the separately disclosed measured process. The
    affine positive-price transformation preserves original first events while
    giving sessions/names varied entry prices; one early close and delayed fill
    in alternating names exercise the managed branch and exact Fraction sums.
    """
    assert len(days) == 257
    observations = {}
    for day_index, day in enumerate(days):
        signal_count = (5 if day_index < 145 else 4) if day_index < 256 else 0
        for index, symbol in enumerate(SYMBOLS):
            rows = (_pattern(day, symbol, index % 4, 195) if index < signal_count else raw_rows(day, symbol, 195))
            if index < signal_count:
                rows[0]["high"] = "100.5"  # Keep DI's already fixed feature defined and selected.
                if index % 2 == 0:
                    rows[30].update(close="103.2", high="104", low="101")
                    rows[34].update(open="102.1", high="104", low="101")
            shift = ((day_index * 29 + index * 137) % 997) * 101
            for row in rows:
                for name in ("open", "high", "low", "close"):
                    row[name] = str(Decimal(row[name]) * 10000 + shift)
            observations[day, symbol] = rows
    observations[days[-1], SYMBOLS[-1]] = raw_rows(days[-1], SYMBOLS[-1], 106)
    cb, chunks = inputs(days=days, observations=observations)
    for index, day in enumerate(days[:18]):
        membership = cb["variants"]["D+2"]["days"][index]
        membership["targets"][0 if index < 2 else 19]["symbol"] = "KRX:000021"
        old = chunks[day, SYMBOLS[0]]
        chunks[day, "KRX:000021"] = {**{key: deepcopy(value) for key, value in old.items() if key != "rows"},
            "symbol": "KRX:000021", "rows": []}
    parent, chunks = _finish_parent(cb, chunks)
    source = _completed(policy.dj.build_diagnostics(parent, chunks))
    assert len(chunks) == 5158 and sum(len(chunk["rows"]) for chunk in chunks.values()) == 1002211
    assert [sum(policy.dj._eligible(row) for row in source["variants"][key]["days"])
        for key in policy.POLICIES] == [1169, 1167]
    return source, chunks


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_parent()


def fixture():
    return deepcopy(_base())


def _first(source):
    return next(row for row in source["variants"]["D+1"]["days"] if policy.dj._eligible(row))


def _pair_setup():
    source, chunks = fixture()
    row = _first(source)
    minutes, _, _ = policy.dj._minutes(chunks[row["session"], row["symbol"]]["rows"], row["session"], row["symbol"], lambda: None)
    entry = policy.cu._instant(row["arms"]["basic"]["label"]["entry_at"])
    return row, minutes, entry


def _bar(minutes, stamp, *, op="102", high="104", low="100", close="102"):
    minutes[stamp] = tuple(Decimal(value) for value in (op, high, low, close))


def test_symbolic_exact_registration_and_fixed_resources():
    spec = json.loads(policy.SPECIFICATION_PATH.read_text())
    policy.validate_spec(spec)
    assert "/home/" not in json.dumps(spec)
    assert [policy.RESOURCES[key] for key in ("cpu_seconds", "address_space_mib", "wall_seconds", "nice")] == [300, 512, 1200, 10]
    assert policy.RESOURCES["maximum_market_gets"] == policy.RESOURCES["maximum_token_posts"] == 0
    assert policy.RESOURCES["maximum_chunks"] == 5158 and policy.RESOURCES["maximum_rows"] == 1002211


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(time_cap_minutes=29),
    lambda spec: spec["parameters"]["take_profit_gross_return"].update(numerator="2"),
    lambda spec: spec["parameters"]["assumed_cost"].update(numerator="34"),
    lambda spec: spec.update(output_name="another"),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(extra=True),
])
def test_registration_refuses_before_source(change):
    spec = json.loads(policy.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(policy.BreakoutExitPolicyRefusal, match="specification_scope"):
        policy.build_comparison({}, [], specification=spec)


def test_original_completed_enriched_parent_all_rows_and_provenance_preserved():
    source, chunks = fixture()
    original = deepcopy(source)
    result = policy.build_comparison(source, chunks)
    assert source == original
    assert result["source_provenance"] == {key: value for key, value in source.items() if key not in policy.PROVENANCE_EXCLUDE}
    assert result["source_target_matrix"] == source["source_target_matrix"]
    assert result["upstream_calendar"] == source["upstream_calendar"]
    assert result["reference_sessions"] == list(DAYS)
    for key, variant in result["variants"].items():
        before = source["variants"][key]
        assert variant["source_variant_provenance"] == {name: value for name, value in before.items() if name != "days"}
        for old, new in zip(before["days"], variant["days"], strict=True):
            assert {name: value for name, value in new.items() if name != "breakout_exit_policy"} == old
            assert (new["breakout_exit_policy"] is not None) == policy.dj._eligible(old)
        for arm in policy.ARMS:
            own = variant["by_arm"][arm]
            assert own["own_label_dispersion"]["known_labels"] == 12
            assert own["own_label_dispersion"]["reference_sessions"] == 4
            assert own["frequency"]["zero_signal_weeks"] == 1
            assert own["frequency"]["signals_per_reference_week"] == 3
        comparison = variant["comparison"]
        assert comparison["paired"]["events"] == 12
        assert comparison["paired"]["mean_change"] == 0
        assert len(comparison["by_arm"]["time_only"]["by_week"]) == 4
        empty = comparison["by_arm"]["managed"]["by_week"][policy.ct._week(DAYS[-1])]
        assert empty["event_count"] == 0 and empty["gross_mean"] is None
    assert result["comparison_performed"] is True and result["new_performance_families"] == 1
    assert result["calibration_only"] is False
    assert result["requires_operator_choice"] is False
    assert result["orders_simulated"] is result["actual_fills_certified"] is False


@pytest.mark.parametrize("close,reason,threshold", [("103.02", "take_profit", Fraction(1, 100)), ("101.49", "stop_loss", Fraction(-1, 200))])
def test_threshold_equality_first_close_delayed_actual_open_not_threshold(close, reason, threshold):
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry, close=close)
    _bar(minutes, entry + 4 * policy.cc.MINUTE, op="101.8")
    pair = policy._label_pair(row, minutes)
    managed, trace = pair["managed"], pair["managed"]["trigger_trace"]
    assert managed["reason"] == reason and trace["price_change"] == threshold
    assert trace["close_at"] == entry + policy.cc.MINUTE
    assert trace["available_at"] == trace["decision_at"] == entry + 3 * policy.cc.MINUTE
    assert managed["exit_at"] == trace["scheduled_fill_at"] == entry + 4 * policy.cc.MINUTE
    assert managed["exit_open"] == Fraction(509, 5)
    assert managed["price_change"] == Fraction(-1, 510)
    assert managed["fill_minus_trigger_threshold"] == Fraction(-1, 510) - threshold
    assert managed["label_available_at"] == entry + 7 * policy.cc.MINUTE


def test_first_trigger_pending_reversal_does_not_replace_exit():
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry, close="103.5")
    _bar(minutes, entry + policy.cc.MINUTE, close="101")
    _bar(minutes, entry + 4 * policy.cc.MINUTE, op="100.5")
    pair = policy._label_pair(row, minutes)
    assert pair["managed"]["reason"] == "take_profit"
    assert pair["managed"]["exit_open"] == Fraction(201, 2)
    assert pair["managed"]["price_change"] < policy.STOP_LOSS
    assert pair["managed"]["holding_minutes"] == 4


@pytest.mark.parametrize("offset,reason,hold", [(25, "take_profit", 29), (26, "time_cap", 30), (29, "time_cap", 30)])
def test_last_eligible_closed_minute_and_cap_tie_fallback(offset, reason, hold):
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry + offset * policy.cc.MINUTE, close="103.5")
    pair = policy._label_pair(row, minutes)
    assert pair["managed"]["reason"] == reason and pair["managed"]["holding_minutes"] == hold
    assert pair["time_only"]["price_change"] == policy.COST


def test_high_low_both_touch_unknown_order_never_close_trigger_or_fill():
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry, high="104", low="100", close="102")
    pair = policy._label_pair(row, minutes)
    touch = pair["touch_diagnostic"]
    assert touch["touch_order"] == "same_minute_order_unknown"
    assert touch["both_touched_same_minute_offsets"] == [0]
    assert pair["managed"]["reason"] == "time_cap"
    assert touch["touches_used_as_fills"] is False
    assert "not_actual_managed_holding" in touch["scope"]


def test_touch_diagnostic_keeps_full_original_horizon_after_managed_exit():
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry, high="103.1", low="102", close="103.02")
    _bar(minutes, entry + 20 * policy.cc.MINUTE, high="104", low="100")
    pair = policy._label_pair(row, minutes)
    assert pair["managed"]["holding_minutes"] == 4
    assert pair["touch_diagnostic"]["both_touched_same_minute_offsets"] == [20]


@pytest.mark.parametrize("offset", [0, 4, 15, 30])
def test_any_required_path_or_anchor_gap_refuses_without_drop(offset):
    row, minutes, entry = _pair_setup()
    minutes[entry + offset * policy.cc.MINUTE] = None
    with pytest.raises(policy.BreakoutExitPolicyRefusal, match="path_incomplete"):
        policy._label_pair(row, minutes)


@pytest.mark.parametrize("offset", [0, 30])
def test_each_original_anchor_disagreement_refuses(offset):
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry + offset * policy.cc.MINUTE, op="102.2")
    with pytest.raises(policy.BreakoutExitPolicyRefusal, match="raw_anchor_mismatch"):
        policy._label_pair(row, minutes)


def test_low_decimal_precision_does_not_change_financial_arithmetic():
    row, minutes, entry = _pair_setup()
    _bar(minutes, entry, close="103.02")
    with localcontext() as context:
        context.prec = 2
        pair = policy._label_pair(row, minutes)
    assert pair["managed"]["trigger_trace"]["price_change"] == Fraction(1, 100)
    assert pair["time_only"]["price_change"] == policy.COST


@pytest.mark.parametrize("mode", ["failed", "undefined", "nonfinite"])
def test_any_one_own_cell_failure_blocks_every_new_mean_and_family(monkeypatch, mode):
    real, calls = policy.cc.dispersion, []
    def dispersion(records, reference):
        calls.append(len(records))
        own = real(records, reference)
        if len(calls) == 4:
            own["mde_bps"] = {"failed": 34.0, "undefined": None, "nonfinite": float("nan")}[mode]
        return own
    def forbidden(*args, **kwargs):
        raise AssertionError("new mean before all four own gates")
    monkeypatch.setattr(policy.cc, "dispersion", dispersion)
    monkeypatch.setattr(policy, "_comparison", forbidden)
    result = policy.build_comparison(*fixture())
    assert calls == [12, 12, 12, 12]
    assert result["requires_operator_choice"] is result["requires_operator_choice_before_comparison"] is True
    assert result["new_performance_families"] == 0 and result["comparison_performed"] is False
    assert result["calibration_only"] is True
    assert result["exit_policy_compared"] is False
    assert all(variant["comparison"] is None for variant in result["variants"].values())


def test_dj_statistics_are_not_replayed(monkeypatch):
    source, chunks = fixture()
    def forbidden(*args, **kwargs):
        raise AssertionError("DJ statistic replay forbidden")
    monkeypatch.setattr(policy.dj, "_summary", forbidden)
    monkeypatch.setattr(policy.dj, "_diagnostic", forbidden)
    policy.build_comparison(source, chunks)


@pytest.mark.parametrize("change", [
    lambda source: source.update(study_id="another"),
    lambda source: source.update(specification_sha256="a" * 64),
    lambda source: source.update(comparison_performed=True),
    lambda source: source["input_evidence"].pop("authentication"),
    lambda source: source["input_evidence"].update(authentication="unverified"),
    lambda source: source["input_evidence"]["source_sha256"].update(archive="a" * 64),
    lambda source: source["input_evidence"].update(extra=True),
    lambda source: _first(source).update(outcome_complete=False),
])
def test_source_identity_execution_enrichment_and_masks_mutations_refuse(change):
    source, chunks = fixture()
    change(source)
    with pytest.raises(policy.BreakoutExitPolicyRefusal):
        policy.build_comparison(source, chunks)


@pytest.mark.parametrize("change", [
    lambda chunk: chunk.update(source="another"),
    lambda chunk: chunk.update(available_at="2026-08-03T09:00:00+09:00"),
    lambda chunk: chunk["rows"][0].update(open="bad"),
    lambda chunk: chunk["rows"][0].update(symbol="KRX:999999"),
])
def test_every_unselected_raw_chunk_still_validated(change):
    source, chunks = fixture()
    key = DAYS[-1], SYMBOLS[4]
    change(chunks[key])
    with pytest.raises((policy.BreakoutExitPolicyRefusal, policy.dj.BreakoutPathRefusal)):
        policy.build_comparison(source, chunks)


@pytest.mark.parametrize("mode", ["missing", "extra", "order"])
def test_archive_chunk_identity_count_and_stream_order_refuse(mode):
    source, chunks = fixture()
    ordered = [chunks[key] for key in sorted(chunks)]
    if mode == "missing": ordered.pop()
    elif mode == "extra": ordered.append(ordered[-1])
    else: ordered[0], ordered[1] = ordered[1], ordered[0]
    with pytest.raises(policy.BreakoutExitPolicyRefusal):
        policy.build_comparison(source, iter(ordered))


def test_checkpoints_propagate_without_retry_or_partial_report():
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) == 3:
            raise RuntimeError("invented budget failure")
    with pytest.raises(RuntimeError, match="invented budget failure"):
        policy.build_comparison(*fixture(), checkpoint=checkpoint)
    assert len(calls) == 3
