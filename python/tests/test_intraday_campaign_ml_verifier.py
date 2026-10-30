"""Invented boundary checks for the ML-specific independent verifier."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from fractions import Fraction
from decimal import Decimal
import hashlib
import json
from types import MappingProxyType, SimpleNamespace

import pytest

from research import intraday_campaign_account as account
from research import intraday_campaign_account_comparison as comparison
from research import intraday_campaign_cash_book as cash
from research import intraday_campaign_independent as oracle
from research import intraday_campaign_ml_labels as labels
from research import intraday_campaign_ml_verifier as verifier
from research import intraday_campaign_context_readiness as readiness
from research import intraday_context_experiment as eb


@pytest.mark.parametrize("value", [{"x": 1}, MappingProxyType({"x": []}), ("ok", {"x": 1})])
def test_mutable_projection_nodes_are_refused(value):
    with pytest.raises(verifier.VerificationError, match="deeply immutable"):
        verifier._immutable_projection(value)


def test_official_frozen_projection_is_retained_without_whole_tree_copy(monkeypatch):
    dates = ("2025-09-04",)
    calendar = SimpleNamespace(source_reference_sessions=dates, evaluation_reference_sessions=dates,
                               evaluation_reference_weeks=("2025-09-01",))
    monkeypatch.setattr(labels, "fixed_calendar", lambda _: calendar)
    originals, projections, external = [], [], []
    for setup in ("EA-A", "EA-B"):
        mutable = {"reference_sessions": list(dates), "reference_weeks": ["2025-09-01"],
            "source_target_matrix": {}, "upstream_calendar": {}, "sources": [], "volume_sources": []}
        frozen = readiness._freeze(mutable)
        targets = readiness._freeze({"D+1": [], "D+2": []})
        metadata = readiness._freeze({"setup_id": setup})
        originals.append(readiness.ReadinessInputs(setup, "invented", metadata, frozen, targets, None, None))
        projections.append(labels.MLLabelProjection(setup, "invented", dates, targets))
        external.append(mutable)
    own = verifier.MLVerifier("P09", *originals, *projections, authentication_reference="invented",
        calendar=calendar, numerical_environment={"sha256": "1" * 64})
    for value, setup, mutable in zip(originals, ("EA-A", "EA-B"), external, strict=True):
        assert own.originals[setup]["account_projection"] is value.account_projection
        assert own.originals[setup]["target_rows"] is value.target_rows
        assert own.originals[setup]["request_metadata"] is value.request_metadata
        mutable["reference_sessions"].clear()
        assert own.originals[setup]["account_projection"]["reference_sessions"] == dates
        with pytest.raises(TypeError):
            own.originals[setup]["account_projection"]["reference_sessions"] = ()


def test_constructor_refuses_author_mutable_original_projection():
    targets = readiness._freeze({"D+1": [], "D+2": []})
    original = readiness.ReadinessInputs("EA-A", "invented", readiness._freeze({}), {"sources": []}, targets, None, None)
    projection = labels.MLLabelProjection("EA-A", "invented", (), targets)
    with pytest.raises(verifier.VerificationError, match="deeply immutable"):
        verifier.MLVerifier("P09", original, None, projection, None, authentication_reference="invented",
            calendar=None, numerical_environment={"sha256": "1" * 64})


def signal_sidecars_with_full_targets(monkeypatch):
    """Three invented targets retain one original-index-2 signal per setup."""
    day, week = "2025-09-04", "2025-09-01"
    opening = eb.cc.session_open(day)
    end = opening + timedelta(minutes=75)
    calendar = SimpleNamespace(source_reference_sessions=(day,), evaluation_reference_sessions=(day,),
                               evaluation_reference_weeks=(week,))
    monkeypatch.setattr(labels, "fixed_calendar", lambda _: calendar)
    sources = [{"session": day, "symbol": f"KRX:{number:06d}", "data_vintage": str(number) * 64} for number in range(1, 4)]
    originals, projections = [], []
    for setup in ("EA-A", "EA-B"):
        targets, sidecars = {}, {}
        for timing in verifier.POLICIES:
            rows = []
            for index, state in enumerate(("known_no_signal", "unknown", "signal")):
                signal = state == "signal"
                reason = "reclaim" if setup == "EA-A" else "downside_reclaimed"
                rows.append({"session": day, "symbol": sources[index]["symbol"], "original_row_index": index,
                    "membership_day_index": 0, "membership_target": {"symbol": sources[index]["symbol"], "rank": index+1},
                    "source_index": index, "volume_source_index": index if setup == "EA-A" else None,
                    "signal_state": state, "signal_reason": reason if signal else "initial_condition_false" if index == 0 else "missing_prefix",
                    "decision_at": (end + timedelta(minutes=2)).isoformat(),
                    "observed_through": None if state == "unknown" else end.isoformat(),
                    "signal_bar_start": (end-timedelta(minutes=5)).isoformat() if signal else None,
                    "signal_bar_end": end.isoformat() if signal else None,
                    "setup_bar_end": (end-timedelta(minutes=5)).isoformat() if signal else None,
                    "structural_stop": Fraction(9000) if signal else None})
            targets[timing] = rows
            source = rows[2]
            signal = {"hypothesis_id": "H1-morning-trend-pullback-reclaim" if setup == "EA-A" else "H3-failed-downside-range-break",
                "state": "signal", "reason": source["signal_reason"],
                **{name: datetime.fromisoformat(source[name]) for name in
                   ("decision_at", "observed_through", "signal_bar_start", "signal_bar_end", "setup_bar_end")}}
            sidecars[timing] = (labels.OriginalLabelRow((timing, setup, 2, day, source["symbol"]),
                readiness._freeze(signal), end+timedelta(minutes=5), end+timedelta(minutes=65), Fraction(9000),
                0, readiness._freeze(source["membership_target"]), 2, source["volume_source_index"],
                readiness._freeze(sources[2]), "invented", readiness._freeze({"invented": True})),)
        account_projection = readiness._freeze({"reference_sessions": [day], "reference_weeks": [week],
            "source_target_matrix": {}, "upstream_calendar": {}, "sources": sources, "volume_sources": []})
        originals.append(readiness.ReadinessInputs(setup, "invented", readiness._freeze({"setup_id": setup}),
            account_projection, readiness._freeze(targets), None, None))
        # Preserve frozen OriginalLabelRow objects; the generic JSON tree freezer
        # intentionally does not transform dataclasses.
        projections.append(labels.MLLabelProjection(setup, "invented", (day,), readiness._freeze(sidecars)))
    own = verifier.MLVerifier("P09", *originals, *projections, authentication_reference="invented",
        calendar=calendar, numerical_environment={"sha256": "1" * 64})
    own._fits_closed = True
    own.features = {key: (Fraction(1),) * 10 for key in own.label_rows}
    own.predictions = {key: (1, "trained", 0.5) for key in own.label_rows}
    monkeypatch.setattr(own, "fold_status", lambda timing, session: (1, "trained"))
    return own, originals, projections


def test_signal_only_sidecars_preserve_original_indices_and_all_nonsignal_targets(monkeypatch):
    own, _, _ = signal_sidecars_with_full_targets(monkeypatch)
    assert len(own.label_rows) == 4
    assert {key[2] for key in own.label_rows} == {2}
    for timing in verifier.POLICIES:
        assert len(own.rows[timing, own.reference[0]]) == 6
        rows = [own.expected_selection_row(timing, setup, source) for setup, source in own.rows[timing, own.reference[0]]]
        assert [row["original_row_index"] for row in rows] == [0, 1, 2, 0, 1, 2]
        assert [row["state"] for row in rows] == ["absent", "absent", "selected"] * 2
        assert all(row["decision_at"] is not None for row in rows)


@pytest.mark.parametrize("field", ["row_key", "signal", "structural_stop", "source", "membership_target"])
def test_signal_sidecar_tampering_fails_against_full_original_projection(monkeypatch, field):
    own, originals, projections = signal_sidecars_with_full_targets(monkeypatch)
    row = projections[0].rows_by_policy["D+1"][0]
    changes = {"row_key": ("D+1", "EA-A", 0, row.row_key[3], row.row_key[4]),
        "signal": readiness._freeze({**row.signal, "decision_at": row.signal["decision_at"]+timedelta(minutes=1)}),
        "structural_stop": Fraction(8000), "source": readiness._freeze({**row.source, "data_vintage": "f" * 64}),
        "membership_target": readiness._freeze({**row.membership_target, "rank": 99})}
    altered = replace(row, **{field: changes[field]})
    projection = replace(projections[0], rows_by_policy=MappingProxyType({**projections[0].rows_by_policy, "D+1": (altered,)}))
    with pytest.raises(verifier.VerificationError, match="label.original"):
        verifier.MLVerifier("P09", *originals, projection, projections[1], authentication_reference="invented",
            calendar=own.calendar, numerical_environment={"sha256": "1" * 64})


def test_day_selection_and_coverage_cache_is_bounded_and_cannot_be_mutated_by_caller(monkeypatch):
    own, _, _ = signal_sidecars_with_full_targets(monkeypatch)
    session = own.reference[0]
    own._active = (2, session)
    plans, coverage = own._plans_and_coverage("D+1", session, "selected")
    assert len(own._selection_cache) == 6 and len(own._plan_cache) == 1
    plans[0]["symbol"] = "BAD"
    coverage["by_setup"]["EA-A"]["targets"] = 999
    plans_again, coverage_again = own._plans_and_coverage("D+1", session, "selected")
    assert plans_again[0]["symbol"] == "KRX:000003"
    assert coverage_again["by_setup"]["EA-A"]["targets"] == 3
    original = own.rows["D+1", session][0][1]
    row = own.expected_selection_row("D+1", "EA-A", original)
    row["state"] = "BAD"
    assert own.expected_selection_row("D+1", "EA-A", original)["state"] == "absent"
    with pytest.raises(verifier.VerificationError, match="selection.original_row"):
        own.expected_selection_row("D+1", "EA-A", {**original, "signal_reason": "BAD"})
    own._day_callbacks = set(range(16))
    own.finish_source_day(pass_index=2)
    assert own._selection_cache == own._plan_cache == {}
    own.expected_selection_row("D+1", "EA-A", original)
    assert own._selection_cache == {}  # final-report reconstruction adds no multi-day cache


def test_mutable_signal_sidecar_payload_is_refused(monkeypatch):
    own, originals, projections = signal_sidecars_with_full_targets(monkeypatch)
    row = projections[0].rows_by_policy["D+1"][0]
    altered = replace(row, signal=dict(row.signal))
    projection = replace(projections[0], rows_by_policy=MappingProxyType({**projections[0].rows_by_policy, "D+1": (altered,)}))
    with pytest.raises(verifier.VerificationError, match="deeply immutable"):
        verifier.MLVerifier("P09", *originals, projection, projections[1], authentication_reference="invented",
            calendar=own.calendar, numerical_environment={"sha256": "1" * 64})


def bare_source_verifier(days):
    """Exercise the raw-day boundary without generating a full 1M-row archive."""
    own = object.__new__(verifier.MLVerifier)
    own.policy_id = "P09"
    own.source_reference = list(days)
    own.reference = list(days)
    own.sources = []
    own.source_by_day = {}
    raw_days = []
    for index, day in enumerate(days):
        symbol = "KRX:000001"
        chunk = {"session": day, "symbol": symbol, "observation_date": day,
            "available_at": None, "retrieved_at": "2026-10-20T00:00:00+00:00",
            "source": "collector klines", "is_final": None, "evidence_level": "assumed", "rows": []}
        session, source = eb._read_price(chunk, day, symbol)
        own.sources.append(source)
        own.source_by_day[day] = {symbol: source}
        raw_days.append({"session": day, "session_open": session.opening, "session_close": eb.cc.session_close(day),
            "original_chunks": {symbol: chunk}, "original_sources": {symbol: source},
            "original_sessions": {symbol: session}, "sources": [source],
            "retained_price_symbols": [symbol], "consumed_target_chunks": 1, "consumed_price_rows": 0,
            "original_union_complete": index == len(days)-1,
            "minute_bars": {(symbol, at): row for at, row in session.minutes.items()}, "open_prices": {}})
    own._closed = own._features_closed = own._fits_closed = False
    own._active = own._raw_day = own._day_input_identity = own._batch_labels = None
    own._day_features, own._day_callbacks = set(), set()
    own._visited, own._receipts = {1: [], 2: []}, {1: [], 2: []}
    own._source_pass_receipts = []
    own._selection_cache, own._plan_cache = {}, {}
    own.signal_keys_by_day = {day: () for day in days}
    own._states, own._days, own._plans = {}, {}, {}
    return own, raw_days


def test_zero_signal_source_day_is_parsed_in_both_passes_and_released():
    own, (day,) = bare_source_verifier(["2025-09-04"])
    own.observe_source_day(day, pass_index=1)
    assert own._raw_day is not None
    own.finish_source_day(pass_index=1)
    assert own._raw_day is own._active is own._batch_labels is None
    own._fits_closed = True
    own.reference = []  # this unit-level source date represents initial training
    own.observe_source_day(deepcopy(day), pass_index=2)
    own.finish_source_day(pass_index=2)
    assert len(own.source_pass_receipts) == 2
    assert own.source_pass_receipts[0]["day_receipt_sha256"] == own.source_pass_receipts[1]["day_receipt_sha256"]
    external = own.source_pass_receipts
    external[0]["source_sessions"] = 999
    assert own.source_pass_receipts[0]["source_sessions"] == 1


def test_checked_raw_feature_sessions_are_owned_by_verifier():
    own, (day,) = bare_source_verifier(["2025-09-04"])
    own.observe_source_day(day, pass_index=1)
    day["original_sessions"]["KRX:000001"].minutes.clear()
    assert len(own._raw_day["original_sessions"]["KRX:000001"].minutes) == 390


@pytest.mark.parametrize("mutation", ["value", "missing", "extra"])
def test_flat_minutes_cannot_diverge_from_already_checked_raw_session(mutation):
    own, (day,) = bare_source_verifier(["2025-09-04"])
    key = next(iter(day["minute_bars"]))
    if mutation == "value":
        day["minute_bars"][key] = (Decimal(1),) * 4
    elif mutation == "missing":
        del day["minute_bars"][key]
    else:
        day["minute_bars"][key[0], key[1] - timedelta(minutes=1)] = None
    with pytest.raises(verifier.VerificationError, match="raw.minute_bars"):
        own.observe_source_day(day, pass_index=1)


def test_flat_minutes_equal_fresh_tuple_uses_numeric_fallback():
    own, (day,) = bare_source_verifier(["2025-09-04"])
    symbol = "KRX:000001"
    opening = day["session_open"]
    chunk = day["original_chunks"][symbol]
    chunk["rows"].append({"symbol": symbol, "interval": "1m",
        "open_time_ms": int(opening.timestamp()) * 1000 + 123,
        "open": "10", "high": "12", "low": "9", "close": "11", "fetched_at": chunk["retrieved_at"]})
    session, source = eb._read_price(chunk, day["session"], symbol)
    own.sources = [source]
    own.source_by_day[day["session"]] = {symbol: source}
    day.update(original_sessions={symbol: session}, original_sources={symbol: source}, sources=[source],
        consumed_price_rows=1, minute_bars={(symbol, at): value for at, value in session.minutes.items()},
        open_prices= {(symbol, at): value for at, value in eb._opening_prices(chunk["rows"]).items()})
    original = session.minutes[opening]
    fresh = tuple(Decimal(str(value)) for value in original)
    assert fresh == original and fresh is not original
    day["minute_bars"][symbol, opening] = fresh
    own.observe_source_day(day, pass_index=1)
    assert own._raw_day["minute_bars"][symbol, opening] == original


@pytest.mark.parametrize("field", ["minutes", "prefix", "gaps", "source_offsets_ms"])
def test_producer_session_cannot_replace_independent_raw_parse(field):
    own, (day,) = bare_source_verifier(["2025-09-04"])
    session = day["original_sessions"]["KRX:000001"]
    bad = {} if field == "minutes" else ("invented_bad",)
    day["original_sessions"]["KRX:000001"] = replace(session, **{field: bad})
    with pytest.raises(verifier.VerificationError, match="raw.Session"):
        own.observe_source_day(day, pass_index=1)


def test_changed_second_pass_archive_fails_even_with_unchanged_decoded_day():
    own, (day,) = bare_source_verifier(["2025-09-04"])
    own.observe_source_day(day, pass_index=1)
    own.finish_source_day(pass_index=1)
    own._fits_closed = True
    changed = deepcopy(day)
    changed["original_chunks"]["KRX:000001"]["retrieved_at"] = "2026-10-20T00:01:00+00:00"
    with pytest.raises(verifier.VerificationError, match="raw.source_receipt"):
        own.observe_source_day(changed, pass_index=2)


def test_signal_day_cannot_close_before_every_original_feature_callback():
    own, (day,) = bare_source_verifier(["2025-09-04"])
    own.signal_keys_by_day[day["session"]] = (("D+1", "EA-A", 0, day["session"], "KRX:000001"),)
    own.observe_source_day(day, pass_index=1)
    with pytest.raises(verifier.VerificationError, match="original_signal_coverage"):
        own.finish_source_day(pass_index=1)


def empty_coverage():
    counts = {setup: {"targets": 20, "signals": 0, "known_no_signal": 20, "unknown_signals": 0,
        "clock_ineligible": 0, "feature_unknown": 0, "support_observed": 0} for setup in ("EA-A", "EA-B")}
    return {"by_setup": counts, "unknown_signal_units": 0, "clock_ineligible_units": 0,
        "recorded_signal_units": 0, "unknown_feature_units": 0, "eligible_control_candidates": 0,
        "eligible_arm_candidates": 0, "model_unavailable_units": 0, "model_rejected_units": 0, "model_selected_units": 0}


def test_unavailable_zero_event_day_is_unknown_and_continuous_unknown_is_sticky(monkeypatch):
    own, days = bare_source_verifier(["2025-09-04", "2025-09-05"])
    for day in days:
        own.observe_source_day(day, pass_index=1)
        own.finish_source_day(pass_index=1)
    own._fits_closed = True
    monkeypatch.setattr(own, "_plans_and_coverage", lambda timing, session, arm: ([], empty_coverage()))
    monkeypatch.setattr(own, "fold_status", lambda timing, session: (1, "unavailable") if session == days[0]["session"] else (2, "trained"))
    producer_states = {}
    for index, day in enumerate(days):
        own.observe_source_day(day, pass_index=2)
        for timing in verifier.POLICIES:
            for cost in (33, 43):
                for arm in verifier.ARMS:
                    for scope in oracle.SCOPES:
                        key = arm, timing, cost, scope
                        initial = producer_states.get(key, account.new_state("A+B", cost_bps=cost, risk_scope=scope)) if scope == "continuous" else account.new_state("A+B", cost_bps=cost, risk_scope=scope)
                        unavailable = arm == "selected" and index == 0
                        if unavailable:
                            initial = replace(initial, book=cash.freeze(initial.book, f"policy_unavailable:P09:{timing}:fold1"),
                                              unknown_at=initial.unknown_at or day["session_open"])
                        parsed = date.fromisoformat(day["session"])
                        result = account.replay_account((), day["minute_bars"], day["open_prices"],
                            {parsed: day["session_open"]}, {parsed: day["session_close"]}, mode="A+B", cost_bps=cost,
                            state=initial, compress_idle_minutes=True)
                        if scope == "day_local_counterfactual":
                            result = replace(result, scenario="fresh_daily_counterfactual_no_continuous_weekly_monthly_claim")
                        compact = comparison.compact_day(day, initial, result, empty_coverage())
                        compact["policy_state"] = "unavailable_model" if unavailable else "available"
                        own.verify_day(arm, day, (), initial, result, compact,
                            {"timing_policy": timing, "cost_bps": cost, "mode": "A+B", "risk_scope": scope, "compress_idle_minutes": True})
                        producer_states[key] = result.state
                        if arm == "selected" and (index == 0 or scope == "continuous"):
                            assert compact["net_pnl"] is compact["final_nav"] is None
                        else:
                            assert compact["net_pnl"] == 0
        own.finish_source_day(pass_index=2)
        assert own._raw_day is None


def test_binding_hashes_exact_training_data_without_evaluation_labels():
    day = "2025-09-04"
    key = ("D+1", "EA-A", 0, day, "KRX:000001")
    cutoff = eb.cc.session_open("2026-02-02")
    vector = (Fraction(1, 7),) * 10
    view = labels.FoldTrainingView(1, "D+1", cutoff, models_feature_order(), (key,), (vector,),
        (Fraction(-1, 9),), (day,), (key,), (vector,), "ready", None, {}, {})
    bound = verifier.fit_binding("P09", view, {"runtime_sha256": "1" * 64})
    changed = verifier.fit_binding("P09", replace(view, y=(Fraction(-2, 9),)), {"runtime_sha256": "1" * 64})
    assert bound["train_labels_exact_sha256"] != changed["train_labels_exact_sha256"]
    assert bound["test_features_exact_sha256"] == changed["test_features_exact_sha256"]
    assert "test_labels" not in str(bound)
    with pytest.raises(verifier.VerificationError, match="nonfinite"):
        verifier.typed_sha256(float("nan"))
    # Statistical closeness cannot replace an exact prediction-byte binding.
    assert verifier.typed_sha256(0.1) != verifier.typed_sha256(0.1 + 1e-16)


def models_feature_order():
    return verifier.models.FEATURE_NAMES


def test_streaming_typed_hash_matches_original_plain_framing_without_tree_copy(monkeypatch):
    key = ("D+1", "EA-A", 37, "2025-09-04", "KRX:000003")
    vector = (Fraction(-2, 7),) * 10
    view = labels.FoldTrainingView(1, "D+1", eb.cc.session_open("2026-02-02"), models_feature_order(),
        (key,), (vector,), (Fraction(1, 11),), ("2025-09-04",), (key,), (vector,), "ready", None,
        MappingProxyType({"observed_labeled_candidates": 1}), ((key, ("label_end_reaches_test_opening",)),))
    record = {"timing_policy": "D+1", "fold_index": 1, "view": view,
        "fit_status": "trained", "reason": None, "artifact": {"unicode": "한글\n\"\\😀", "키\n😀": Fraction(-3, 19), "model": "x" * 70000,
        "runtime": MappingProxyType({"zero": -0.0, "bool": True, "none": None, "day": date(2025, 9, 4)})},
        "predictions": (0.1, -0.0)}
    expected = hashlib.sha256(json.dumps(verifier._typed(verifier._plain(record)),
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    monkeypatch.setattr(verifier, "_typed", lambda _: pytest.fail("no complete typed tree"))
    monkeypatch.setattr(verifier, "_plain", lambda _: pytest.fail("no complete plain clone"))
    assert verifier.typed_sha256(record) == expected
    changed = {**record, "predictions": (0.1 + 1e-16, -0.0)}
    assert verifier.typed_sha256(changed) != expected
    with pytest.raises(verifier.VerificationError, match="string keys"):
        verifier.typed_sha256({key: ("purged",)})


def test_retained_fit_digest_rejects_byte_tamper_without_model_matrix_clone():
    own = object.__new__(verifier.MLVerifier)
    own.fit_records = {}
    records = []
    for timing in verifier.POLICIES:
        for fold in range(1, 6):
            record = {"timing_policy": timing, "fold_index": fold, "predictions": (0.1,), "artifact": {"model": "invented"}}
            records.append(record)
            own.fit_records[timing, fold] = {"record_sha256": verifier.typed_sha256(record)}
    own._verify_retained_fit_records(records)
    records[0]["predictions"] = (0.1 + 1e-16,)
    with pytest.raises(verifier.VerificationError, match="exact identity"):
        own._verify_retained_fit_records(records)


def test_all_ten_unavailable_slots_are_retained_and_cannot_be_missing(monkeypatch):
    own = object.__new__(verifier.MLVerifier)
    own.policy_id, own.numerical_environment = "P09", {"runtime_sha256": "1" * 64}
    dates = []
    current = date(2025, 9, 4)
    while len(dates) < 257:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    own.calendar = labels.fixed_calendar(dates)
    own.reference = list(own.calendar.evaluation_reference_sessions)
    own.features, own.validated_labels = {}, {}
    own.fit_records, own.fit_receipts, own.predictions = {}, {}, {}
    own._features_closed, own._fits_closed = True, False
    monkeypatch.setattr(verifier.models, "fit_model", lambda *args, **kwargs: pytest.fail("verifier must never fit"))
    with pytest.raises(verifier.VerificationError, match="all_ten_fit_slots"):
        own.bind_prediction_map()
    for timing in verifier.POLICIES:
        for number in range(1, 6):
            view = labels.prepare_fold(own.calendar, number, timing, {}, {})
            record = {"timing_policy": timing, "fold_index": number, "view": view,
                "fit_status": "unavailable", "reason": view.reason, "artifact": None, "predictions": ()}
            own.verify_fit_record(record)
            assert set(own.fit_records[timing, number]) == {"timing_policy", "fold_index", "fit_status", "reason", "record_sha256"}
            record["fit_status"] = "trained"
            assert own.fit_records[timing, number]["fit_status"] == "unavailable"
    assert own.bind_prediction_map() == {}
    for timing in verifier.POLICIES:
        for fold in own.calendar.folds:
            assert own.fold_status(timing, fold.test_sessions[0]) == (fold.index, "unavailable")
