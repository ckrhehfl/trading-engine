"""Fixed, past-only EC selectors over two passes of the same original archive.

This pure orchestration accepts caller-authenticated original projections. It
has no file, network, order or tuning API. Initial training dates never become
account days. Original label availability is assumed and separately checked;
unknown model actions are not represented as empty cash strategies.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date, datetime
from fractions import Fraction

from research import intraday_campaign_account as account
from research import intraday_campaign_account_comparison as statistics
from research import intraday_campaign_account_inputs as inputs
from research import intraday_campaign_cash_book as cash
from research import intraday_campaign_context_readiness as readiness
from research import intraday_campaign_features as features
from research import intraday_campaign_ml_labels as labels
from research import intraday_campaign_ml_models as models
from research.intraday_campaign_ml_verifier import MLVerifier, fit_binding


MODE = "ec-fixed-ml-selector-cash-account"
LEARNERS = {"P09": "ridge", "P10": "lightgbm"}
TIMINGS = ("D+1", "D+2")
SETUPS = ("EA-A", "EA-B")
ARMS = ("control", "selected")
eb, ea, cc = features.eb, features.ea, features.cc


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _key(timing, setup, original):
    return timing, setup, original["original_row_index"], original["session"], original["symbol"]


def _prepared(day, timing, setup, original, membership, authentication_reference, day_cache):
    target = ea.Target(original["symbol"], date.fromisoformat(day["session"]), timing,
        day["session_open"], day["session_close"], eb.PRICE_BASIS,
        eb._price_source(day_cache.sources[original["symbol"]]))
    return features.prepare_features(target, eb._universe(membership, day["session"]),
        day_cache.sessions, day_cache.sources, setup_id=setup,
        observed_end=datetime.fromisoformat(original["observed_through"]),
        decision_at=datetime.fromisoformat(original["decision_at"]),
        structural_stop=original["structural_stop"], inputs_authenticated=True,
        authentication_reference=authentication_reference, price_unit="KRW", day_cache=day_cache)


def _selection_row(timing, setup, original, *, vector, prediction, fold_index, opening, closing):
    row = {"timing_policy": timing, "setup_id": setup,
        "original_row_index": original["original_row_index"], "session": original["session"],
        "symbol": original["symbol"], "signal_state": original["signal_state"],
        "feature_state": "absent", "fold_index": fold_index, "model_status": "absent",
        "prediction_hex": None, "state": "absent", "reason": original["signal_reason"],
        "clock_eligible": None, "entry_at": None, "cap_exit_at": None,
        "decision_at": datetime.fromisoformat(original["decision_at"])}
    if original["signal_state"] != "signal":
        return row
    at = datetime.fromisoformat(original["decision_at"])
    entry = opening + ((at - opening) // (5 * cc.MINUTE) + 1) * 5 * cc.MINUTE
    cap = entry + 60 * cc.MINUTE
    row.update(decision_at=at, entry_at=entry, cap_exit_at=cap,
        clock_eligible=opening < at < entry < cap < closing,
        feature_state="observed" if vector is not None and None not in vector else "unknown",
        state="unknown", reason="feature_unknown")
    if row["feature_state"] == "observed":
        _require(prediction is not None and prediction[0] == fold_index, "bound_fold_prediction_required")
        _, status, value = prediction
        row.update(model_status=status, reason="model_unavailable")
        if status == "trained":
            row.update(prediction_hex=value.hex(), state="selected" if value > 0 else "rejected",
                reason="positive_prediction" if value > 0 else "nonpositive_prediction")
    return row


def _plans_and_coverage(rows, originals, arm):
    counts, plans = {}, []
    for setup in SETUPS:
        own = [row for row in rows if row["setup_id"] == setup]
        counts[setup] = {"targets": len(own),
            "signals": sum(row["signal_state"] == "signal" for row in own),
            "known_no_signal": sum(row["signal_state"] == "known_no_signal" for row in own),
            "unknown_signals": sum(row["signal_state"] == "unknown" for row in own),
            "clock_ineligible": sum(row["clock_eligible"] is False for row in own),
            "feature_unknown": sum(row["feature_state"] == "unknown" for row in own),
            "support_observed": sum(row["feature_state"] == "observed" for row in own)}
    control = 0
    for row in rows:
        if row["feature_state"] != "observed" or not row["clock_eligible"]:
            continue
        control += 1
        if arm == "control" or row["state"] == "selected":
            original = originals[row["setup_id"], row["original_row_index"]]
            plans.append(account.CampaignCandidate(row["setup_id"][-1], row["symbol"],
                row["decision_at"], row["entry_at"], row["cap_exit_at"], original["structural_stop"]))
    plans.sort(key=lambda plan: (plan.decision_at, plan.symbol, plan.strategy))
    coverage = {"by_setup": counts,
        "unknown_signal_units": sum(value["unknown_signals"] for value in counts.values()),
        "clock_ineligible_units": sum(value["clock_ineligible"] for value in counts.values()),
        "recorded_signal_units": sum(value["signals"] for value in counts.values()),
        "unknown_feature_units": sum(value["feature_unknown"] for value in counts.values()),
        "eligible_control_candidates": control, "eligible_arm_candidates": len(plans),
        "model_unavailable_units": sum(row["model_status"] == "unavailable" for row in rows),
        "model_rejected_units": sum(row["state"] == "rejected" for row in rows),
        "model_selected_units": sum(row["state"] == "selected" for row in rows)}
    return tuple(plans), coverage


def _finish_statistics(report, checkpoint):
    """All 52 own/paired plans precede any saved performance mean."""
    scenarios, reference, weeks = (report[name] for name in
        ("scenarios", "reference_sessions", "reference_weeks"))
    fields = ("net_pnl", "gross_pnl_same_fills")
    for scenario in scenarios.values():
        for scope in statistics.SCOPES:
            content = scenario[scope]
            content["own_sizing"] = {name: statistics.own_sizing(content["days"], reference,
                label_field=name) for name in fields}
    comparisons, common_rows = {}, {}
    for timing in TIMINGS:
        for cost in statistics.COSTS:
            all_days = {arm: scenarios[f"{timing}|{cost}|{arm}"]["day_local_counterfactual"]["days"]
                for arm in ARMS}
            observed = [{row["session"] for row in days if row["status"] == "identified"}
                for days in all_days.values()]
            common = [day for day in reference if all(day in found for found in observed)]
            selected = {arm: [row for row in days if row["session"] in common]
                for arm, days in all_days.items()}
            key = f"{timing}|{cost}"
            common_rows[key] = selected
            comparisons[key] = {"timing_policy": timing, "cost_bps": cost,
                "scope": "support_matched_common_identified_fresh_daily_accounts",
                "reference_sessions": reference, "reference_weeks": weeks,
                "common_sessions": common, "excluded_sessions": [day for day in reference if day not in common],
                "own_sizing": {arm: {name: statistics.own_sizing(days, reference, label_field=name)
                    for name in fields} for arm, days in selected.items()},
                "paired_sizing": {"selected_minus_control": statistics.own_sizing(
                    selected["selected"], reference, peer_days=selected["control"])},
                "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
    report["comparisons"] = comparisons
    if checkpoint:
        checkpoint("sizing_complete", {"own_label_planning_count": 52})
    for scenario in scenarios.values():
        for scope in statistics.SCOPES:
            scenario[scope]["summary"] = statistics.summarize_days(scenario[scope]["days"], weeks, reference)
    for key, comparison in comparisons.items():
        comparison["accounts"] = {arm: statistics.summarize_days(days, weeks, reference)
            for arm, days in common_rows[key].items()}
        comparison["paired_mean_daily_net_per_initial_capital"] = {"selected_minus_control":
            comparison["accounts"]["selected"]["mean_daily_net_per_initial_capital"]
            - comparison["accounts"]["control"]["mean_daily_net_per_initial_capital"]
            if comparison["common_sessions"] else None}
    if checkpoint:
        checkpoint("summary_complete", {"comparisons": 4})


def _initial_for_day(previous, *, cost, scope, unavailable, policy_id, timing, fold_index, opening):
    initial = previous if scope == "continuous" else account.new_state(
        "A+B", cost_bps=cost, risk_scope="day_local_counterfactual")
    if unavailable:
        initial = replace(initial, book=cash.freeze(initial.book,
            f"policy_unavailable:{policy_id}:{timing}:fold{fold_index}"),
            unknown_at=initial.unknown_at or opening)
    return initial


def build_comparison(a, b, label_a, label_b, price_chunk_factory, *, policy_id,
                     inputs_authenticated, authentication_reference, numerical_environment,
                     checkpoint: Callable | None = None):
    """Run each fixed fit slot once, with complete original authentication twice."""
    readiness._authentication(inputs_authenticated, authentication_reference)
    _require(policy_id in LEARNERS and callable(price_chunk_factory), "registered_policy_and_fresh_source_factory_required")
    _require(checkpoint is None or callable(checkpoint), "invalid_checkpoint")
    projected, label_projection = {"EA-A": a, "EA-B": b}, {"EA-A": label_a, "EA-B": label_b}
    for setup in SETUPS:
        _require(type(projected[setup]) is readiness.ReadinessInputs and projected[setup].setup_id == setup,
            "original_readiness_projection_required")
        _require(type(label_projection[setup]) is labels.MLLabelProjection and label_projection[setup].setup_id == setup,
            "original_label_projection_required")
    accounts = {setup: readiness._account_plain(value.account_projection) for setup, value in projected.items()}
    inputs._linked_inputs(accounts["EA-A"], accounts["EA-B"])
    # Both original immutable projections were checked before sharing these
    # identical read-only fields. Do not keep two normalized copies of the same
    # source/universe calendar for the entire numerical/account worker.
    for name in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources"):
        accounts["EA-B"][name] = accounts["EA-A"][name]
    source_reference = list(accounts["EA-A"]["reference_sessions"])
    calendar = labels.fixed_calendar(source_reference)
    reference, weeks = list(calendar.evaluation_reference_sessions), list(calendar.evaluation_reference_weeks)
    rows_by_day = {(timing, day): [(setup, row) for setup in SETUPS
        for row in projected[setup].target_rows[timing] if row["session"] == day]
        for timing in TIMINGS for day in source_reference}
    original_labels = {row.row_key: row for setup in SETUPS for timing in TIMINGS
        for row in label_projection[setup].rows_by_policy[timing]}
    verifier = MLVerifier(policy_id, a, b, label_a, label_b, authentication_reference=authentication_reference,
        calendar=calendar, numerical_environment=numerical_environment)
    feature_rows, validated_labels = {}, {}
    # First pass: labels have a separate channel and never admit a feature.
    for index, day in enumerate(inputs.iter_full_target_price_days(accounts["EA-A"], accounts["EA-B"], price_chunk_factory())):
        _require(index < len(source_reference) and day["session"] == source_reference[index], "first_source_order_changed")
        verifier.observe_source_day(day, pass_index=1)
        producer_labels = {}
        for timing in TIMINGS:
            for setup, original in rows_by_day[timing, day["session"]]:
                if original["signal_state"] == "signal":
                    key = _key(timing, setup, original)
                    producer_labels[key] = labels.produce_label_for_row(day, original_labels[key], inputs_authenticated=True)
        verifier.prepare_day_labels(day, producer_labels)
        feature_cache = None
        for timing in TIMINGS:
            membership = accounts["EA-A"]["source_target_matrix"][timing]["days"][index]
            for setup, original in rows_by_day[timing, day["session"]]:
                if original["signal_state"] != "signal":
                    continue
                key = _key(timing, setup, original)
                if feature_cache is None:
                    feature_cache = features.FeatureDayCache(opening=day["session_open"], closing=day["session_close"],
                        sessions=day["original_sessions"], sources=day["original_sources"])
                prepared = _prepared(day, timing, setup, original, membership, authentication_reference, feature_cache)
                vector = tuple(prepared.features[name].value for name in models.FEATURE_NAMES)
                validated = verifier.verify_feature_row(day, timing, setup, original, prepared, vector, producer_labels[key])
                feature_rows[key], validated_labels[key] = vector, validated
                del prepared, validated
        verifier.finish_source_day(pass_index=1)
        if checkpoint:
            checkpoint("feature_day_complete", {"source_day_index": index})
        del day, producer_labels, feature_cache
    verifier.finish_features(feature_rows, validated_labels)
    # Exact past-only views are checked independently before each fit.
    fit_records, prediction_map, slot_status = [], {}, {}
    for timing in TIMINGS:
        for fold in calendar.folds:
            view = labels.prepare_fold(calendar, fold.index, timing, feature_rows, validated_labels)
            from research.intraday_campaign_ml_label_verifier import verify_fold_training_view
            verify_fold_training_view(view, calendar, feature_rows, validated_labels)
            artifact, predictions = None, ()
            fit_status = "unavailable"
            if view.status == "ready":
                artifact = models.fit_model(LEARNERS[policy_id], tuple(tuple(map(float, row)) for row in view.X),
                    tuple(map(float, view.y)), feature_names=models.FEATURE_NAMES,
                    binding=fit_binding(policy_id, view, numerical_environment))
                predictions = models.predict_model(artifact, tuple(tuple(map(float, row)) for row in view.test_X),
                    feature_names=models.FEATURE_NAMES)
                fit_status = "trained"
            record = {"timing_policy": timing, "fold_index": fold.index, "view": view,
                "fit_status": fit_status, "reason": view.reason, "artifact": artifact, "predictions": predictions}
            verifier.verify_fit_record(record)
            fit_records.append(record)
            slot_status[timing, fold.index] = fit_status
            for offset, key in enumerate(view.test_row_keys):
                prediction_map[key] = (fold.index, fit_status, predictions[offset] if fit_status == "trained" else None)
            if checkpoint:
                checkpoint("fit_slot_complete", {"timing_policy": timing, "fold_index": fold.index,
                    "status": fit_status})
    verified_predictions = verifier.bind_prediction_map()
    _require(prediction_map == verified_predictions, "independent_prediction_map_differs")
    del verified_predictions
    states, scenarios = {}, {}
    for timing in TIMINGS:
        for cost in statistics.COSTS:
            for arm in ARMS:
                key = f"{timing}|{cost}|{arm}"
                states[key] = account.new_state("A+B", cost_bps=cost)
                scenarios[key] = {"timing_policy": timing, "cost_bps": cost, "mode": "A+B", "arm": arm,
                    **{scope: {"days": []} for scope in statistics.SCOPES}}
    selection_rows = {timing: [] for timing in TIMINGS}
    # Second pass: same bytes and geometry; evaluation labels are not read here.
    for index, day in enumerate(inputs.iter_full_target_price_days(accounts["EA-A"], accounts["EA-B"], price_chunk_factory())):
        _require(index < len(source_reference) and day["session"] == source_reference[index], "second_source_order_changed")
        verifier.observe_source_day(day, pass_index=2)
        session = day["session"]
        if session in reference:
            fold_index = next(fold.index for fold in calendar.folds if session in fold.test_sessions)
            parsed = date.fromisoformat(session)
            openings, closes = {parsed: day["session_open"]}, {parsed: day["session_close"]}
            for timing in TIMINGS:
                rows = [_selection_row(timing, setup, original, vector=feature_rows.get(_key(timing, setup, original)),
                    prediction=prediction_map.get(_key(timing, setup, original)), fold_index=fold_index,
                    opening=day["session_open"], closing=day["session_close"])
                    for setup, original in rows_by_day[timing, session]]
                selection_rows[timing].extend(rows)
                originals = {(setup, row["original_row_index"]): row for setup, row in rows_by_day[timing, session]}
                for arm in ARMS:
                    candidates, coverage = _plans_and_coverage(rows, originals, arm)
                    unavailable = arm == "selected" and slot_status[timing, fold_index] == "unavailable"
                    for cost in statistics.COSTS:
                        key = f"{timing}|{cost}|{arm}"
                        for scope in statistics.SCOPES:
                            initial = _initial_for_day(states[key], cost=cost, scope=scope,
                                unavailable=unavailable, policy_id=policy_id, timing=timing,
                                fold_index=fold_index, opening=day["session_open"])
                            result = account.replay_account(candidates, day["minute_bars"], day["open_prices"],
                                openings, closes, mode="A+B", cost_bps=cost, state=initial, compress_idle_minutes=True)
                            if scope == "day_local_counterfactual":
                                result = replace(result, scenario="fresh_daily_counterfactual_no_continuous_weekly_monthly_claim")
                            compact = statistics.compact_day(day, initial, result, coverage)
                            compact["policy_state"] = "unavailable_model" if unavailable else "available"
                            scenario = {"timing_policy": timing, "cost_bps": cost, "mode": "A+B",
                                "risk_scope": scope, "compress_idle_minutes": True}
                            verifier.verify_day(arm, day, candidates, initial, result, compact, scenario)
                            scenarios[key][scope]["days"].append(compact)
                            if scope == "continuous":
                                states[key] = result.state
                            del result
        verifier.finish_source_day(pass_index=2)
        if checkpoint:
            checkpoint("account_source_day_complete", {"source_day_index": index})
        del day
    report = {"mode": MODE, "policy_id": policy_id, "authentication_reference": authentication_reference,
        "source_reference_sessions": source_reference, "source_reference_weeks": list(calendar.source_reference_weeks),
        "evaluation_reference_sessions": reference, "evaluation_reference_weeks": weeks,
        "evaluation_week_session_counts": dict(calendar.evaluation_week_session_counts),
        "boundary_week_flags": readiness._account_plain(calendar.evaluation_boundary_week_flags),
        "reference_sessions": reference, "reference_weeks": weeks, "initial_capital": cash.INITIAL_CAPITAL,
        "source_passes": 2, "source_pass_receipts": verifier.source_pass_receipts,
        "consumed_reference_sessions": len(source_reference), "consumed_target_chunks": len(accounts["EA-A"]["sources"]),
        "consumed_price_rows": sum(row["input_rows"] for row in accounts["EA-A"]["sources"]),
        "original_union_complete": True, "day_replay_count": len(reference) * 16,
        "numerical_environment": dict(numerical_environment),
        "feature_rows": [{"row_key": key, "values": value} for key, value in feature_rows.items()],
        "label_rows": list(validated_labels.values()), "fit_records": fit_records,
        "selection_rows": selection_rows, "scenarios": scenarios, "idle_minutes_compressed": True,
        "original_requests": {setup: readiness._account_plain(value.request_metadata) for setup, value in projected.items()},
        **{name: accounts["EA-A"][name] for name in ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
        "performance_computed": True, "strategy_pass": False, "promotion_allowed": False,
        "historical_brokerage_account_claim": False, "historical_timing_certified": False,
        "corporate_action_units_certified": False, "volume_availability_reauthenticated": False,
        "policy_results_not_pooled": True, "actual_execution_authorized": False, "untouched_validation_claim": False}
    _finish_statistics(report, checkpoint)
    report["independent_verification"] = verifier.finish_verify(report)
    return report
