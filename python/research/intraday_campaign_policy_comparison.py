"""Pure, day-streamed P04--P08 support-matched fictional cash accounts.

The original reports have already been projected and independently checked.
Every original price chunk is authenticated before any causal admission. No
old outcome or entry price determines selection. Continuous unknowns remain
sticky; fresh daily accounts are separate conditional counterfactuals. This
library performs no IO and grants no actual execution or promotion permission.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime
from fractions import Fraction

from research import intraday_campaign_account as account
from research import intraday_campaign_account_comparison as comparison
from research import intraday_campaign_account_inputs as account_inputs
from research import intraday_campaign_cash_book as cash
from research import intraday_campaign_context_readiness as readiness
from research import intraday_campaign_features as features
from research import intraday_campaign_policy_selection as selection


MODE = "ec-p04-p08-support-matched-account-comparison"
UNIT_DEFINITIONS = {
    "P04": {"mode": "A", "setups": ("EA-A",), "arms": ("control", "selected")},
    "P05": {"mode": "A", "setups": ("EA-A",), "arms": ("control", "selected")},
    "P06": {"mode": "A", "setups": ("EA-A",),
            "arms": ("control", "volume_only", "market_only", "selected")},
    "P07": {"mode": "B", "setups": ("EA-B",), "arms": ("control", "selected")},
    "P08": {"mode": "A+B", "setups": ("EA-A", "EA-B"), "arms": ("control", "selected")},
}
COSTS = comparison.COSTS
SCOPES = comparison.SCOPES
POLICIES = readiness.POLICIES
eb, ea, cc = features.eb, features.ea, features.cc
CELLS = ("neither", "volume", "market", "both")


class PolicyComparisonRefusal(ValueError):
    """Malformed units, original plans or incomplete streams are hard errors."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PolicyComparisonRefusal(reason)


def _pairs(policy_id: str) -> tuple[tuple[str, str], ...]:
    pairs = (("selected", "control"),)
    if policy_id == "P06":
        pairs += (("volume_only", "control"), ("market_only", "control"),
                  ("selected", "volume_only"), ("selected", "market_only"))
    return pairs


def _candidate(original: Mapping, setup_id: str, opening: datetime,
               closing: datetime) -> tuple[bool, datetime, datetime, account.CampaignCandidate | None]:
    """Original decision/stop only; strict next-five OPEN and original cap."""
    decision = datetime.fromisoformat(original["decision_at"])
    entry = opening + ((decision - opening) // (5 * cc.MINUTE) + 1) * (5 * cc.MINUTE)
    cap = entry + 60 * cc.MINUTE
    eligible = opening < decision < entry < cap < closing
    candidate = (account.CampaignCandidate(setup_id.removeprefix("EA-"), original["symbol"],
        decision, entry, cap, original["structural_stop"]) if eligible else None)
    return eligible, entry, cap, candidate


def _feature_evidence(prepared: features.PreparedFeatures, policy_id: str,
                      source_indices: Mapping[str, int], volume_value: Fraction | None) -> dict:
    return {"proxy_id": prepared.proxy_id,
        "authentication_reference": prepared.authentication_reference,
        "common_peer_source_indices": [source_indices[symbol] for symbol in prepared.common_peers],
        "required_values": {name: {"value": prepared.features[name].value,
            "reasons": prepared.features[name].reasons,
            "observation_end": (prepared.target.opening + 30 * cc.MINUTE
                                if name == "peer_first30_return" else prepared.observed_end),
            "available_at": prepared.decision_at if prepared.features[name].value is not None else None}
            for name in selection.REQUIRED_FEATURES[policy_id]},
        "volume_value": volume_value if policy_id in {"P04", "P06"} else None}


def _selection_row(day: Mapping, timing_policy: str, setup_id: str, original: Mapping,
                   universe: ea.MonthlyUniverse, source_indices: Mapping[str, int], *,
                   policy_id: str, volume_value: Fraction | None,
                   authentication_reference: str) -> tuple:
    row = {name: original[name] for name in ("session", "symbol", "original_row_index",
        "membership_day_index", "source_index", "signal_state", "signal_reason",
        "observed_through", "decision_at")}
    row.update(setup_id=setup_id, clock_eligible=None, entry_at=None, cap_exit_at=None,
               selection=None, auxiliary_decisions={}, interaction_cell=None, feature_evidence=None)
    prepared = decision = candidate = None
    auxiliary = {}
    if original["signal_state"] == "signal":
        target = ea.Target(original["symbol"], date.fromisoformat(day["session"]), timing_policy,
            day["session_open"], day["session_close"], eb.PRICE_BASIS,
            eb._price_source(day["original_sources"][original["symbol"]]))
        prepared = features.prepare_features(target, universe, day["original_sessions"],
            day["original_sources"], setup_id=setup_id,
            observed_end=datetime.fromisoformat(original["observed_through"]),
            decision_at=datetime.fromisoformat(original["decision_at"]),
            structural_stop=original["structural_stop"], inputs_authenticated=True,
            authentication_reference=authentication_reference, price_unit="KRW")
        decision = selection.select_policy(policy_id, prepared, volume_value=volume_value)
        eligible, entry, cap, candidate = _candidate(original, setup_id,
                                                     day["session_open"], day["session_close"])
        if policy_id == "P06" and decision["state"] in {"selected", "rejected"}:
            auxiliary = {name: selection.select_policy(name, prepared, volume_value=volume_value)
                         for name in ("P04", "P05")}
            volume_pass = auxiliary["P04"]["state"] == "selected"
            market_pass = auxiliary["P05"]["state"] == "selected"
            row["interaction_cell"] = "both" if volume_pass and market_pass else (
                "volume" if volume_pass else "market" if market_pass else "neither")
        row.update(clock_eligible=eligible, entry_at=entry, cap_exit_at=cap,
            selection=decision, auxiliary_decisions=auxiliary,
            feature_evidence=_feature_evidence(prepared, policy_id, source_indices, volume_value))
    return prepared, decision, candidate, auxiliary, row


def _admitted(row: Mapping, arm: str) -> bool:
    decision = row["selection"]
    if decision is None or decision["state"] not in {"selected", "rejected"}:
        return False
    if arm == "control":
        return True
    if arm in {"volume_only", "market_only"}:
        return row["auxiliary_decisions"]["P04" if arm == "volume_only" else "P05"]["state"] == "selected"
    return decision["state"] == "selected"


def _coverage(rows: list[dict], setups: tuple[str, ...], arm: str) -> dict:
    by_setup = {}
    for setup in setups:
        current = [row for row in rows if row["setup_id"] == setup]
        states = Counter(row["signal_state"] for row in current)
        by_setup[setup] = {"targets": len(current), "signals": states["signal"],
            "known_no_signal": states["known_no_signal"], "unknown_signals": states["unknown"],
            "clock_ineligible": sum(row["clock_eligible"] is False for row in current),
            "feature_unknown": sum(row["selection"] is not None
                                   and row["selection"]["state"] == "unknown" for row in current),
            "support_observed": sum(_admitted(row, "control") for row in current)}
    return {"by_setup": by_setup,
        "unknown_signal_units": sum(counts["unknown_signals"] for counts in by_setup.values()),
        "clock_ineligible_units": sum(counts["clock_ineligible"] for counts in by_setup.values()),
        "recorded_signal_units": sum(counts["signals"] for counts in by_setup.values()),
        "unknown_feature_units": sum(counts["feature_unknown"] for counts in by_setup.values()),
        "eligible_control_candidates": sum(row["clock_eligible"] is True and _admitted(row, "control") for row in rows),
        "eligible_arm_candidates": sum(row["clock_eligible"] is True and _admitted(row, arm) for row in rows)}


def _common_days(scenarios: Mapping, timing_policy: str, cost: int,
                 arms: tuple[str, ...], reference: list[str]) -> tuple[list[str], dict]:
    all_days = {arm: scenarios[f"{timing_policy}|{cost}|{arm}"]["day_local_counterfactual"]["days"]
                for arm in arms}
    common_set = set.intersection(*({row["session"] for row in days if row["status"] == "identified"}
                                   for days in all_days.values()))
    common = [day for day in reference if day in common_set]
    return common, {arm: [row for row in days if row["session"] in common_set]
                    for arm, days in all_days.items()}


def _finish_statistics(report: dict, checkpoint: Callable | None) -> None:
    """Finish every own-label plan before constructing any public profit mean."""
    scenarios, reference, weeks = (report[name] for name in
                                   ("scenarios", "reference_sessions", "reference_weeks"))
    arms = UNIT_DEFINITIONS[report["policy_id"]]["arms"]
    pairs = _pairs(report["policy_id"])
    labels = ("net_pnl", "gross_pnl_same_fills")
    for scenario in scenarios.values():
        for scope in SCOPES:
            content = scenario[scope]
            content["own_sizing"] = {label: comparison.own_sizing(content["days"], reference,
                                        label_field=label) for label in labels}
    comparisons = {}
    for timing_policy in POLICIES:
        for cost in COSTS:
            common, days = _common_days(scenarios, timing_policy, cost, arms, reference)
            comparisons[f"{timing_policy}|{cost}"] = {
                "timing_policy": timing_policy, "cost_bps": cost,
                "scope": "support_matched_common_identified_fresh_daily_accounts",
                "reference_sessions": reference, "reference_weeks": weeks,
                "common_sessions": common, "excluded_sessions": [day for day in reference if day not in common],
                "own_sizing": {arm: {label: comparison.own_sizing(days[arm], reference, label_field=label)
                                     for label in labels} for arm in arms},
                "paired_sizing": {f"{left}_minus_{right}": comparison.own_sizing(
                    days[left], reference, peer_days=days[right]) for left, right in pairs},
                "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
    report["comparisons"] = comparisons
    if checkpoint is not None:
        checkpoint("sizing_complete", {"scenario_scopes": len(scenarios) * len(SCOPES),
                                       "common_comparisons": len(comparisons)})
    for scenario in scenarios.values():
        for scope in SCOPES:
            scenario[scope]["summary"] = comparison.summarize_days(scenario[scope]["days"], weeks, reference)
    for result in comparisons.values():
        common, days = _common_days(scenarios, result["timing_policy"], result["cost_bps"], arms, reference)
        result["accounts"] = {arm: comparison.summarize_days(days[arm], weeks, reference) for arm in arms}
        result["paired_mean_daily_net_per_initial_capital"] = {
            f"{left}_minus_{right}": (result["accounts"][left]["mean_daily_net_per_initial_capital"]
                - result["accounts"][right]["mean_daily_net_per_initial_capital"]) if common else None
            for left, right in pairs}
    if checkpoint is not None:
        checkpoint("summary_complete", {"comparisons": len(comparisons)})


def build_comparison(a: readiness.ReadinessInputs, b: readiness.ReadinessInputs,
                     price_chunks: Iterable[Mapping], *, policy_id: str, inputs_authenticated: bool,
                     authentication_reference: str, checkpoint: Callable | None = None) -> dict:
    """Authenticate the complete original union and independently verify all arms.

    ``checkpoint(stage, payload)`` is a progress/budget seam. Its exception and
    every verification failure propagate without retries or partial success.
    Both projected original inputs are required even for a single-setup unit.
    """
    readiness._authentication(inputs_authenticated, authentication_reference)
    _require(type(policy_id) is str and policy_id in UNIT_DEFINITIONS, "unregistered_policy")
    _require(not isinstance(price_chunks, Mapping), "ordered_price_chunks_required")
    _require(checkpoint is None or callable(checkpoint), "invalid_checkpoint")
    for value, setup in ((a, "EA-A"), (b, "EA-B")):
        _require(type(value) is readiness.ReadinessInputs and value.setup_id == setup,
                 "invalid_readiness_projection")
    projected = {"EA-A": a, "EA-B": b}
    accounts = {setup: readiness._account_plain(value.account_projection)
                for setup, value in projected.items()}
    account_inputs._linked_inputs(accounts["EA-A"], accounts["EA-B"])
    reference = list(accounts["EA-A"]["reference_sessions"])
    weeks = list(accounts["EA-A"]["reference_weeks"])
    _require(bool(reference) and reference == sorted(set(reference))
             and weeks == sorted({comparison._week(day) for day in reference}),
             "invalid_account_reference_calendar")
    mode, setups, arms = (UNIT_DEFINITIONS[policy_id][name] for name in ("mode", "setups", "arms"))
    rows_by_day = {setup: {timing: {day: [] for day in reference} for timing in POLICIES}
                   for setup in setups}
    for setup in setups:
        for timing in POLICIES:
            for original in projected[setup].target_rows[timing]:
                rows_by_day[setup][timing][original["session"]].append(original)
    volume_rows = {timing: {(row["session"], row["symbol"]): row
                   for row in a.volume_sidecar["variants"][timing]["days"]} for timing in POLICIES}
    sources = accounts["EA-A"]["sources"]
    source_indices = {(source["session"], source["symbol"]): index for index, source in enumerate(sources)}
    states, scenarios = {}, {}
    for timing in POLICIES:
        for cost in COSTS:
            for arm in arms:
                key = f"{timing}|{cost}|{arm}"
                states[key] = account.new_state(mode, cost_bps=cost)
                scenarios[key] = {"timing_policy": timing, "cost_bps": cost, "mode": mode, "arm": arm,
                                  **{scope: {"days": []} for scope in SCOPES}}
    # The dedicated independent finisher is intentionally separate from core3.
    from research.intraday_campaign_policy_account_verifier import PolicyAccountVerifier

    verifier = PolicyAccountVerifier(policy_id, a, b, authentication_reference=authentication_reference)
    selection_rows = {timing: [] for timing in POLICIES}
    cell_counts = {timing: dict.fromkeys(CELLS, 0) for timing in POLICIES} if policy_id == "P06" else {}
    consumed_days, total_chunks, total_rows, complete = 0, 0, 0, False
    replays_per_day = len(POLICIES) * len(COSTS) * len(arms) * len(SCOPES)
    for day in account_inputs.iter_context_price_days(accounts["EA-A"], accounts["EA-B"], price_chunks):
        session = day["session"]
        _require(consumed_days < len(reference) and session == reference[consumed_days], "original_day_order_changed")
        indices = {symbol: source_indices[(session, symbol)] for symbol in day["original_sources"]}
        parsed = date.fromisoformat(session)
        openings, closes = {parsed: day["session_open"]}, {parsed: day["session_close"]}
        for timing in POLICIES:
            membership = accounts["EA-A"]["source_target_matrix"][timing]["days"][consumed_days]
            universe = eb._universe(membership, session)
            rows, plans = [], {arm: [] for arm in arms}
            for setup in setups:
                for original in rows_by_day[setup][timing][session]:
                    volume_row = volume_rows[timing].get((session, original["symbol"]))
                    value = (volume_row["relative_volume"]["value"] if policy_id in {"P04", "P06"}
                             and volume_row is not None and volume_row["relative_volume"] is not None else None)
                    prepared, decision, candidate, auxiliary, compact = _selection_row(day, timing, setup,
                        original, universe, indices, policy_id=policy_id, volume_value=value,
                        authentication_reference=authentication_reference)
                    verifier.verify_selection_row(day_input=day, timing_policy=timing, setup_id=setup,
                        original_row=original, prepared=prepared, decision=decision, volume_value=value,
                        candidate=candidate, auxiliary_decisions=auxiliary, compact_row=compact)
                    rows.append(compact)
                    selection_rows[timing].append(compact)
                    if compact["interaction_cell"] is not None:
                        cell_counts[timing][compact["interaction_cell"]] += 1
                    if candidate is not None:
                        for arm in arms:
                            if _admitted(compact, arm):
                                plans[arm].append(candidate)
                    del prepared, decision, candidate, auxiliary, compact
            for arm in arms:
                candidates = tuple(sorted(plans[arm], key=lambda plan: (plan.decision_at, plan.symbol, plan.strategy)))
                coverage = _coverage(rows, setups, arm)
                for cost in COSTS:
                    key = f"{timing}|{cost}|{arm}"
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
                        verifier.verify_day(arm=arm, day_input=day, candidates=candidates,
                            initial_state=initial, result=result, compact=compact, scenario=scenario)
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
                                        "day_replays": replays_per_day})
        del day
    _require(complete and consumed_days == len(reference), "original_union_not_exhausted")
    report = {"mode": MODE, "policy_id": policy_id,
        "proxy_id": features.PROXY_ID, "authentication_reference": authentication_reference,
        "reference_sessions": reference, "reference_weeks": weeks, "initial_capital": cash.INITIAL_CAPITAL,
        **{name: accounts["EA-A"][name] for name in
           ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
        "original_requests": {setup: readiness._account_plain(value.request_metadata) for setup, value in projected.items()},
        "selection_rows": selection_rows, "interaction_cell_counts": cell_counts,
        "consumed_reference_sessions": consumed_days, "consumed_target_chunks": total_chunks,
        "consumed_price_rows": total_rows, "original_union_complete": complete,
        "day_replay_count": consumed_days * replays_per_day, "idle_minutes_compressed": True,
        "scenarios": scenarios,
        "account_path_scope": "recorded_detectable_candidates_conditioned_on_archive_availability",
        "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
        "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
        "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
        "historical_timing_certified": False, "corporate_action_units_certified": False,
        "volume_availability_reauthenticated": False, "policy_results_not_pooled": True,
        "performance_computed": True, "strategy_pass": False, "promotion_allowed": False,
        "historical_brokerage_account_claim": False, "actual_execution_authorized": False}
    _finish_statistics(report, checkpoint)
    report["independent_verification"] = verifier.finish_verify(report)
    return report
