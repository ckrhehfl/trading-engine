"""Pure original H4/H5 cash accounts over the full dated target population.

No old A/B signal or outcome mask admits a target. The original detector,
cash book and replay are reused. Each unit has one policy arm; uninvested cash
is only a zero-return reference, not an additional fitted/replayed strategy.
The caller owns source-byte authentication, registration and execution gates.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import date
from fractions import Fraction

from research import intraday_autonomous_signals as signals
from research import intraday_campaign_account as account
from research import intraday_campaign_account_comparison as comparison
from research import intraday_campaign_account_inputs as account_inputs
from research import intraday_campaign_cash_book as cash
from research import intraday_campaign_context_readiness as readiness


MODE = "ec-h45-original-signal-cash-account"
UNITS = {
    "P11": {"mode": "H4", "arm": "original_h4",
            "hypothesis_id": "H4-compression-expansion-breakout"},
    "P12": {"mode": "H5", "arm": "original_h5",
            "hypothesis_id": "H5-late-session-morning-strength"},
}
POLICIES = ("D+1", "D+2")
COSTS, SCOPES = comparison.COSTS, comparison.SCOPES
cc = account_inputs.eb.cc


class H45AccountRefusal(ValueError):
    """Invalid original scope and unfinished streams are hard errors."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise H45AccountRefusal(reason)


def _signal_row(day: Mapping, target: Mapping, day_index: int,
                source_index: int, definition: Mapping) -> tuple[dict, account.CampaignCandidate | None]:
    opening, closing = day["session_open"], day["session_close"]
    detected = signals.detect_bars(definition["hypothesis_id"],
        day["original_sessions"][target["symbol"]].prefix, opening=opening, closing=closing)
    row = {"session": day["session"], "symbol": target["symbol"],
        "membership_day_index": day_index, "source_index": source_index,
        "membership_target": dict(target), "signal_state": detected.state,
        "signal_reason": detected.reason, "decision_at": detected.decision_at,
        "observed_through": detected.observed_through,
        "signal_bar_start": detected.signal_bar_start, "signal_bar_end": detected.signal_bar_end,
        "setup_bar_end": detected.setup_bar_end, "entry_at": None, "cap_exit_at": None,
        "structural_stop": None, "clock_eligible": None, "clock_ineligible_reason": None}
    candidate = None
    if detected.state == "signal":
        entry = opening + ((detected.decision_at - opening) // (5 * cc.MINUTE) + 1) * 5 * cc.MINUTE
        cap = entry + 60 * cc.MINUTE
        eligible = opening < detected.decision_at < entry < cap < closing
        if definition["mode"] == "H4":
            eligible = eligible and detected.decision_at <= opening + 240 * cc.MINUTE
        else:
            eligible = eligible and detected.decision_at == closing - 78 * cc.MINUTE \
                and entry == closing - 75 * cc.MINUTE and cap == closing - 15 * cc.MINUTE
        row.update(entry_at=entry, cap_exit_at=cap, clock_eligible=eligible,
                   clock_ineligible_reason=None if eligible else "same_day_cap_infeasible")
        if eligible:
            candidate = account.CampaignCandidate(definition["mode"], target["symbol"],
                detected.decision_at, entry, cap, None)
    return row, candidate


def _counts(rows: list[dict]) -> dict:
    states = Counter(row["signal_state"] for row in rows)
    return {"targets": len(rows), "signals": states["signal"],
            "known_no_signal": states["known_no_signal"], "unknown_signals": states["unknown"],
            "clock_ineligible": sum(row["clock_eligible"] is False for row in rows)}


def _coverage(rows: list[dict], mode: str) -> dict:
    counts = _counts(rows)
    return {"by_setup": {mode: counts}, "unknown_signal_units": counts["unknown_signals"],
            "clock_ineligible_units": counts["clock_ineligible"],
            "recorded_signal_units": counts["signals"]}


def _identified_days(scenario: Mapping) -> list[dict]:
    return [row for row in scenario["day_local_counterfactual"]["days"]
            if row["status"] == "identified"]


def _finish_statistics(report: dict, checkpoint: Callable | None) -> None:
    """Complete all 24 own-label plans before creating any profit mean."""
    scenarios, reference, weeks = (report[key] for key in
                                   ("scenarios", "reference_sessions", "reference_weeks"))
    arm = report["arm"]
    labels = ("net_pnl", "gross_pnl_same_fills")
    for scenario in scenarios.values():
        for scope in SCOPES:
            content = scenario[scope]
            content["own_sizing"] = {label: comparison.own_sizing(content["days"], reference,
                                        label_field=label) for label in labels}
    comparisons = {}
    for timing in POLICIES:
        for cost in COSTS:
            key = f"{timing}|{cost}"
            days = _identified_days(scenarios[key])
            common = [row["session"] for row in days]
            common_set = set(common)
            comparisons[key] = {"timing_policy": timing, "cost_bps": cost,
                "scope": "identified_fresh_daily_vs_uninvested_cash_reference",
                "reference_sessions": reference, "reference_weeks": weeks,
                "common_sessions": common, "excluded_sessions": [day for day in reference if day not in common_set],
                "own_sizing": {arm: {label: comparison.own_sizing(days, reference,
                    label_field=label) for label in labels}},
                "paired_sizing": {}, "paired_mean_daily_net_per_initial_capital": {},
                "cash_reference_net_per_initial_capital": Fraction(),
                "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
    report["comparisons"] = comparisons
    if checkpoint is not None:
        checkpoint("sizing_complete", {"scenario_scopes": 8, "own_label_planning_count": 24})
    for scenario in scenarios.values():
        for scope in SCOPES:
            scenario[scope]["summary"] = comparison.summarize_days(
                scenario[scope]["days"], weeks, reference)
    for key, content in comparisons.items():
        content["accounts"] = {arm: comparison.summarize_days(
            _identified_days(scenarios[key]), weeks, reference)}
    if checkpoint is not None:
        checkpoint("summary_complete", {"comparisons": len(comparisons)})


def build_comparison(a: readiness.ReadinessInputs, b: readiness.ReadinessInputs,
                     price_chunks: Iterable[Mapping], *, policy_id: str, inputs_authenticated: bool,
                     authentication_reference: str, checkpoint: Callable | None = None) -> dict:
    """Evaluate one original hypothesis with independent full-unit verification.

    Source report labels are not used. Both original projections retain and
    authenticate the dated full population; the detector runs on every target.
    A failed checkpoint or independent check propagates without partial adoption.
    """
    readiness._authentication(inputs_authenticated, authentication_reference)
    _require(type(policy_id) is str and policy_id in UNITS, "unregistered_policy")
    _require(not isinstance(price_chunks, Mapping), "ordered_price_chunks_required")
    _require(checkpoint is None or callable(checkpoint), "invalid_checkpoint")
    inputs = {"EA-A": a, "EA-B": b}
    for value, setup in ((a, "EA-A"), (b, "EA-B")):
        _require(type(value) is readiness.ReadinessInputs and value.setup_id == setup,
                 "invalid_readiness_projection")
    projections = {setup: readiness._account_plain(value.account_projection)
                   for setup, value in inputs.items()}
    account_inputs._linked_inputs(projections["EA-A"], projections["EA-B"])
    original = projections["EA-A"]
    reference, weeks = list(original["reference_sessions"]), list(original["reference_weeks"])
    _require(bool(reference) and reference == sorted(set(reference))
             and weeks == sorted({comparison._week(day) for day in reference}),
             "invalid_account_reference_calendar")
    definition = UNITS[policy_id]
    mode, arm = definition["mode"], definition["arm"]
    states, scenarios = {}, {}
    for timing in POLICIES:
        for cost in COSTS:
            key = f"{timing}|{cost}"
            states[key] = account.new_state(mode, cost_bps=cost)
            scenarios[key] = {"timing_policy": timing, "cost_bps": cost, "mode": mode,
                              "arm": arm, **{scope: {"days": []} for scope in SCOPES}}
    from research.intraday_campaign_h45_account_verifier import H45AccountVerifier

    verifier = H45AccountVerifier(policy_id, a, b, authentication_reference=authentication_reference)
    source_indices = {(source["session"], source["symbol"]): index
                      for index, source in enumerate(original["sources"])}
    signal_rows = {timing: [] for timing in POLICIES}
    consumed_days = total_chunks = total_rows = 0
    complete = False
    for day in account_inputs.iter_full_target_price_days(projections["EA-A"], projections["EA-B"], price_chunks):
        session = day["session"]
        _require(consumed_days < len(reference) and session == reference[consumed_days],
                 "original_day_order_changed")
        parsed = date.fromisoformat(session)
        openings, closes = {parsed: day["session_open"]}, {parsed: day["session_close"]}
        for timing in POLICIES:
            membership = original["source_target_matrix"][timing]["days"][consumed_days]
            _require(membership["session"] == session, "original_membership_day_changed")
            rows, plans = [], []
            for target in membership["targets"]:
                row, candidate = _signal_row(day, target, consumed_days,
                    source_indices[(session, target["symbol"])], definition)
                verifier.verify_signal_row(day_input=day, timing_policy=timing,
                    original_target=target, day_index=consumed_days, compact_row=row, candidate=candidate)
                rows.append(row)
                signal_rows[timing].append(row)
                if candidate is not None:
                    plans.append(candidate)
                del row, candidate
            candidates = tuple(sorted(plans, key=lambda plan: (plan.decision_at, plan.symbol, plan.strategy)))
            coverage = _coverage(rows, mode)
            for cost in COSTS:
                key = f"{timing}|{cost}"
                for scope in SCOPES:
                    initial = states[key] if scope == "continuous" else account.new_state(
                        mode, cost_bps=cost, risk_scope="day_local_counterfactual")
                    result = account.replay_account(candidates, day["minute_bars"], day["open_prices"],
                        openings, closes, mode=mode, cost_bps=cost, state=initial, compress_idle_minutes=True)
                    if scope == "day_local_counterfactual":
                        result = replace(result, scenario="fresh_daily_counterfactual_no_continuous_weekly_monthly_claim")
                    compact = comparison.compact_day(day, initial, result, coverage)
                    scenario = {"timing_policy": timing, "cost_bps": cost, "mode": mode,
                                "risk_scope": scope, "compress_idle_minutes": True}
                    verifier.verify_day(day_input=day, candidates=candidates, initial_state=initial,
                        result=result, compact=compact, scenario=scenario)
                    scenarios[key][scope]["days"].append(compact)
                    if scope == "continuous":
                        states[key] = result.state
                    del result
        consumed_days += 1
        total_chunks += day["consumed_target_chunks"]
        total_rows += day["consumed_price_rows"]
        complete = day["original_union_complete"]
        if checkpoint is not None:
            checkpoint("day_complete", {"session": session, "completed_days": consumed_days,
                                        "day_replays": 8})
        del day
    _require(complete and consumed_days == len(reference), "original_union_not_exhausted")
    report = {"mode": MODE, "policy_id": policy_id, "hypothesis_id": definition["hypothesis_id"],
        "arm": arm, "authentication_reference": authentication_reference,
        "reference_sessions": reference, "reference_weeks": weeks, "initial_capital": cash.INITIAL_CAPITAL,
        **{name: original[name] for name in
           ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
        "original_requests": {setup: readiness._account_plain(value.request_metadata)
                              for setup, value in inputs.items()},
        "signal_rows": signal_rows, "signal_counts": {timing: _counts(rows) for timing, rows in signal_rows.items()},
        "consumed_reference_sessions": consumed_days, "consumed_target_chunks": total_chunks,
        "consumed_price_rows": total_rows, "original_union_complete": complete,
        "day_replay_count": consumed_days * 8, "idle_minutes_compressed": True,
        "scenarios": scenarios,
        "account_path_scope": "all_original_dated_targets_detected_conditioned_on_archive_availability",
        "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
        "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
        "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
        "historical_timing_certified": False, "corporate_action_units_certified": False,
        "policy_results_not_pooled": True, "performance_computed": True,
        "strategy_pass": False, "promotion_allowed": False,
        "historical_brokerage_account_claim": False, "actual_execution_authorized": False}
    _finish_statistics(report, checkpoint)
    report["independent_verification"] = verifier.finish_verify(report)
    return report
