"""Invented reports/chunks only; no archive, DB, network or detector replay."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from research import intraday_campaign_account_inputs as inputs
from test_intraday_campaign_readiness import reports


def fixture(sessions=2):
    a, b = reports(sessions=sessions)
    chunks = []
    for source in a["sources"]:
        day, symbol = source["session"], source["symbol"]
        opening = inputs.eb.cc.session_open(day)
        rows = [{"symbol": symbol, "interval": "1m",
                 "open_time_ms": int((opening + timedelta(minutes=minute)).timestamp()) * 1000 + 123,
                 "open": "100", "high": "101", "low": "99", "close": "100",
                 "fetched_at": "2026-10-20T00:00:00+00:00"}
                for minute in (80, 140)] if source["input_rows"] else []
        chunks.append({"session": day, "symbol": symbol, "observation_date": day,
                       "available_at": None, "retrieved_at": "2026-10-20T00:00:00+00:00",
                       "source": "collector klines", "is_final": None, "evidence_level": "assumed",
                       "rows": rows})
    for report in (a, b):
        report["sources"] = [inputs.eb._source(chunk) for chunk in chunks]
        if report["setup_id"] == "EA-A":
            report["volume_sources"] = deepcopy(report["sources"])
        for variant in report["variants"].values():
            for row in variant["days"]:
                row["structural_stop"] = ({"numerator": "199", "denominator": "2"}
                                           if row["signal"]["state"] == "signal" else None)
    return a, b, chunks


def projections(a, b):
    return inputs.project_account_inputs(a, "EA-A"), inputs.project_account_inputs(b, "EA-B")


def stored_report(raw):
    # The publication boundary also normalizes dispersion's date objects.
    return json.loads(json.dumps(raw, default=lambda value: value.isoformat()
        if type(value) is date else json.loads(inputs.eb.cc._canonical(value))))


def test_all_signals_survive_without_old_label_or_context_selection():
    a, b, _ = fixture()
    before = deepcopy(a)
    projected, _ = projections(a, b)
    assert a == before
    for policy in inputs.POLICIES:
        variant = projected["variants"][policy]
        assert variant["counts"]["signals"] == 32
        assert sum(len(day["candidates"]) for day in variant["dates"].values()) == 32
        first = variant["dates"]["2025-09-04"]
        assert first["counts"] == {"targets": 20, "signals": 16, "known_no_signal": 2,
                                   "unknown_signals": 2, "clock_ineligible": 0}
        assert [target["original_outcome"]["status"] for target in first["targets"][:8]] == [
            "known", "known", "unknown", "unknown", "declined", "absent", "absent", "known"]
        assert {row["symbol"] for row in first["candidates"]} >= {"KRX:000003", "KRX:000005", "KRX:000008"}
        assert all(row["structural_stop"] == Fraction(199, 2) for row in first["candidates"])
        assert all(row["decision_at"].utcoffset() == timedelta(hours=9) for row in first["candidates"])
        assert all("original_outcome" not in row for row in first["candidates"])
    for key in ("source_target_matrix", "upstream_calendar", "sources", "volume_sources",
                "reference_sessions", "reference_weeks"):
        assert projected[key] == a[key]
    encoded = inputs.eb.cc._canonical(projected).decode()
    assert "999999999999" not in encoded
    assert '"gross"' not in encoded and '"entry_open"' not in encoded and '"exit_at"' not in encoded


@pytest.mark.parametrize("bad", [None, "99.5", 99.5, 0, True,
    {"numerator": "0", "denominator": "1"}, {"numerator": "1", "denominator": "0"},
    {"numerator": "-1", "denominator": "2"}, {"numerator": "01", "denominator": "2"},
    {"numerator": 199, "denominator": "2"}, {"numerator": "199", "denominator": "2", "extra": 0},
    {"numerator": "398", "denominator": "4"}])
def test_malformed_or_nonpositive_stops_refuse_instead_of_dropping_signal(bad):
    a, _, _ = fixture(1)
    a["variants"]["D+1"]["days"][0]["structural_stop"] = bad
    with pytest.raises(inputs.AccountInputsRefusal, match="structural_stop"):
        inputs.project_account_inputs(a, "EA-A")


def test_old_outcome_prices_and_returns_are_never_decoded():
    a, _, _ = fixture(1)
    for variant in a["variants"].values():
        for row in variant["days"]:
            if row["outcome"] is not None:
                for field in ("structural_stop", "entry_open", "exit_open", "gross", "primary_net"):
                    row["outcome"][field] = object()
    projected = inputs.project_account_inputs(a, "EA-A")
    assert len(projected["variants"]["D+1"]["dates"]["2025-09-04"]["candidates"]) == 16


def test_invalid_report_metadata_is_rejected_by_existing_validator():
    a, _, _ = fixture(1)
    a["variants"]["D+1"]["days"][0]["source_index"] = 1
    with pytest.raises(inputs.readiness.ReadinessRefusal, match="source_index_identity_changed"):
        inputs.project_account_inputs(a, "EA-A")


def test_clock_infeasible_signal_stays_counted_with_causal_marker():
    a, _, _ = fixture(1)
    row = a["variants"]["D+1"]["days"][0]
    for field in ("decision_at", "observed_through", "signal_bar_start", "signal_bar_end", "setup_bar_end"):
        row["signal"][field] = (datetime.fromisoformat(row["signal"][field]) + timedelta(minutes=250)).isoformat()
    row.update(entry_at="2025-09-04T14:30:00+09:00", cap_at="2025-09-04T15:30:00+09:00",
               features={"clock": {"value": None, "reasons": ["same_day_cap_infeasible"]}},
               arms=dict.fromkeys(row["arms"], "unknown"), common_context_observable=False,
               common_price_observable=False)
    row["outcome"].update(status="unknown", reason="same_day_cap_infeasible", entry_at=row["entry_at"], exit_at=None)
    projected = inputs.project_account_inputs(a, "EA-A")
    day = projected["variants"]["D+1"]["dates"]["2025-09-04"]
    assert day["counts"]["signals"] == 16 and day["counts"]["clock_ineligible"] == 1
    assert day["candidates"][0]["clock_eligible"] is False
    assert day["candidates"][0]["clock_ineligible_reason"] == "same_day_cap_infeasible"


def test_zero_signal_week_and_original_unknowns_survive_projection():
    a, _, _ = fixture(7)
    for variant in a["variants"].values():
        for row in variant["days"]:
            if row["session"] < "2025-09-08":
                continue
            row["signal"].update(state="unknown", reason="missing_prefix", signal_bar_start=None,
                                 signal_bar_end=None, setup_bar_end=None, observed_through=None)
            row.update(entry_at=None, cap_at=None, structural_stop=None, outcome=None, features={},
                       arms=dict.fromkeys(row["arms"], "unknown"), common_context_observable=False,
                       common_price_observable=False)
    projected = inputs.project_account_inputs(a, "EA-A")
    variant = projected["variants"]["D+1"]
    assert variant["by_week"]["2025-09-08"]["targets"] == 100
    assert variant["by_week"]["2025-09-08"]["unknown_signals"] == 100
    assert variant["by_week"]["2025-09-08"]["signals"] == 0
    assert variant["dates"]["2025-09-08"]["candidates"] == []


def test_day_stream_consumes_all_original_targets_and_preserves_missing_minutes():
    a, b, chunks = fixture()
    a, b = projections(a, b)
    consumed = []

    def streamed():
        for chunk in chunks:
            consumed.append((chunk["session"], chunk["symbol"]))
            yield chunk

    stream = inputs.iter_account_price_days(a, b, streamed())
    first = next(stream)
    assert len(consumed) == 20
    assert first["consumed_target_chunks"] == 20 and len(first["sources"]) == 20
    assert first["original_union_complete"] is False
    assert len(first["retained_price_symbols"]) == 16
    assert "KRX:000006" not in first["retained_price_symbols"]
    assert "KRX:000005" in first["retained_price_symbols"]  # old decline retained
    at = inputs.eb.cc.session_open("2025-09-04") + timedelta(minutes=80)
    assert first["open_prices"][("KRX:000001", at)] == Decimal("100")
    assert first["minute_bars"][("KRX:000001", at + timedelta(minutes=1))] is None
    second = next(stream)
    assert len(consumed) == 40 and second["original_union_complete"] is True
    assert {at.date().isoformat() for _, at in second["minute_bars"]} == {"2025-09-05"}
    with pytest.raises(StopIteration):
        next(stream)


@pytest.mark.parametrize("field", ["source_target_matrix", "upstream_calendar", "sources"])
def test_ab_canonical_membership_calendar_and_pins_must_match(field):
    a, b, chunks = fixture(1)
    a, b = projections(a, b)
    if field == "sources":
        b[field][0]["data_vintage"] = "a" * 64
    elif field == "upstream_calendar":
        b[field]["extra"] = "different provenance"
    else:
        b[field]["D+1"]["days"][0]["source"] = "different provenance"
    with pytest.raises(inputs.AccountInputsRefusal, match=f"setup_{field}_differ"):
        next(inputs.iter_account_price_days(a, b, chunks))


@pytest.mark.parametrize("change,reason", [
    (lambda chunks: chunks.pop(), "missing_price_chunk"),
    (lambda chunks: chunks.append(deepcopy(chunks[-1])), "extra_price_chunk"),
    (lambda chunks: chunks.reverse(), "price_chunk_identity_or_metadata_changed"),
    (lambda chunks: chunks[0]["rows"][0].update(open="101"), "price_source_data_vintage_changed"),
    (lambda chunks: chunks[0]["rows"].reverse(), "price_source_data_vintage_changed"),
    (lambda chunks: chunks[5]["rows"][0].update(fetched_at=None), "price_source_data_vintage_changed"),
    (lambda chunks: chunks[6].update(retrieved_at="2026-10-21T00:00:00+00:00"), "price_source_data_vintage_changed"),
])
def test_chunk_population_order_vintage_and_noncandidate_integrity_are_not_waived(change, reason):
    a, b, chunks = fixture(1)
    a, b = projections(a, b)
    change(chunks)
    with pytest.raises((inputs.AccountInputsRefusal, inputs.eb.ExperimentRefusal), match=reason):
        list(inputs.iter_account_price_days(a, b, chunks))


def test_row_linkage_digest_checked_even_with_matching_vintage():
    a, b, chunks = fixture(1)
    for report in (a, b):
        report["sources"][0]["row_linkage_sha256"] = "a" * 64
    a, b = projections(a, b)
    with pytest.raises(inputs.AccountInputsRefusal, match="price_source_row_linkage_sha256_changed"):
        next(inputs.iter_account_price_days(a, b, chunks))


def test_raw_open_survives_invalid_future_hlc_but_duplicate_open_is_unknown():
    a, b, chunks = fixture(1)
    chunks[0]["rows"][0]["close"] = "invalid future close"
    chunks[1]["rows"].append(deepcopy(chunks[1]["rows"][0]))
    for report in (a, b):
        report["sources"] = [inputs.eb._source(chunk) for chunk in chunks]
    a, b = projections(a, b)
    day = next(inputs.iter_account_price_days(a, b, chunks))
    at = inputs.eb.cc.session_open("2025-09-04") + timedelta(minutes=80)
    assert day["minute_bars"][("KRX:000001", at)] is None
    assert day["open_prices"][("KRX:000001", at)] == Decimal("100")
    assert day["open_prices"][("KRX:000002", at)] is None


@pytest.mark.parametrize("setup,stop", [("EA-A", 107), ("EA-B", 88)])
def test_real_producer_shape_with_invented_rows_roundtrips_without_detector_replay(monkeypatch, setup, stop):
    from test_intraday_context_experiment import build, fixture as eb_fixture

    raw = build(eb_fixture(setup, days=("2025-09-04",)), setup)
    report = stored_report(raw)

    def prohibited(*args, **kwargs):
        raise AssertionError("adapter replayed a signal detector or outcome")

    monkeypatch.setattr(inputs.eb.signals, "detect_bars", prohibited)
    monkeypatch.setattr(inputs.eb.outcomes, "exploratory_outcome", prohibited)
    projected = inputs.project_account_inputs(report, setup)
    candidates = projected["variants"]["D+1"]["dates"]["2025-09-04"]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["structural_stop"] == Fraction(stop)


def test_differing_policy_targets_consume_original_union_including_unused_prices(monkeypatch):
    from test_intraday_context_experiment import build, fixture as eb_fixture

    upstream, chunks, volumes = eb_fixture("EA-B", days=("2025-09-04",))
    upstream["variants"]["D+2"]["days"][0]["targets"][-1]["symbol"] = "KRX:000021"
    added = deepcopy(chunks[-1])
    added["symbol"] = "KRX:000021"
    for row in added["rows"]:
        row["symbol"] = "KRX:000021"
    chunks.append(added)
    added_volume = deepcopy(volumes[-1])
    added_volume["symbol"] = "KRX:000021"
    volumes.append(added_volume)
    data = (upstream, chunks, volumes)
    a, b = projections(stored_report(build(data, "EA-A")), stored_report(build(data, "EA-B")))

    def prohibited(*args, **kwargs):
        raise AssertionError("price adapter replayed a detector or outcome")

    monkeypatch.setattr(inputs.eb.signals, "detect_bars", prohibited)
    monkeypatch.setattr(inputs.eb.outcomes, "exploratory_outcome", prohibited)
    day = next(inputs.iter_account_price_days(a, b, iter(chunks)))
    assert day["consumed_target_chunks"] == 21
    assert len(day["sources"]) == 21
    assert day["retained_price_symbols"] == ["KRX:000001"]
    assert day["original_union_complete"] is True
    assert a["variants"]["D+1"]["counts"]["targets"] == 20
    assert a["variants"]["D+2"]["counts"]["targets"] == 20


def test_nonmatching_report_row_count_is_integrity_failure():
    a, b, chunks = fixture(1)
    for report in (a, b):
        report["sources"][0]["input_rows"] += 1
    a, b = projections(a, b)
    with pytest.raises(inputs.AccountInputsRefusal, match="price_source_input_rows_changed"):
        next(inputs.iter_account_price_days(a, b, chunks))


def test_context_projection_keeps_old_account_view_and_nonsignal_peers():
    a, b, chunks = fixture(2)
    a, b = projections(a, b)
    old = list(inputs.iter_account_price_days(a, b, chunks))
    contextual = list(inputs.iter_context_price_days(a, b, chunks))
    for ordinary, expanded in zip(old, contextual, strict=True):
        assert {key: value for key, value in expanded.items()
                if key not in {"original_sessions", "original_sources"}} == ordinary
        assert set(expanded["original_sessions"]) == set(expanded["original_sources"])
        assert len(expanded["original_sessions"]) == 20
        assert "KRX:000006" not in ordinary["retained_price_symbols"]
        assert expanded["original_sessions"]["KRX:000006"].minutes
        assert expanded["original_sources"]["KRX:000006"]["symbol"] == "KRX:000006"
        empty = expanded["original_sessions"]["KRX:000007"]
        assert len(empty.minutes) == 390 and all(value is None for value in empty.minutes.values())
        assert expanded["original_sources"]["KRX:000007"]["input_rows"] == 0


@pytest.mark.parametrize("project", [inputs.iter_account_price_days, inputs.iter_context_price_days])
def test_nonsignal_peer_source_change_is_still_integrity_failure(project):
    a, b, chunks = fixture(1)
    a, b = projections(a, b)
    assert "KRX:000006" not in {row["symbol"] for row in a["variants"]["D+1"]["dates"]["2025-09-04"]["candidates"]}
    chunks[5]["rows"][0]["close"] = "100.5"
    with pytest.raises(inputs.AccountInputsRefusal, match="price_source_.*_changed"):
        list(project(a, b, chunks))


def test_context_projection_partial_day_is_not_full_archive_authentication():
    a, b, chunks = fixture(2)
    a, b = projections(a, b)
    stream = inputs.iter_context_price_days(a, b, [*chunks, deepcopy(chunks[-1])])
    first = next(stream)
    assert first["original_union_complete"] is False
    with pytest.raises(inputs.AccountInputsRefusal, match="extra_price_chunk"):
        next(stream)
