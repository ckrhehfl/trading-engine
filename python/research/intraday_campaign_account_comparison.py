"""Pure, day-streamed EC comparison of the three fictional cash accounts.

Only caller-projected original plans and authenticated day prices are consumed.
Continuous accounts keep their first unknown forever; fresh daily accounts are
separate conditional diagnostics. Neither is a historical brokerage account.
IO, actual execution registration, output authentication and an independently
implemented verifier remain the caller's responsibilities.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from fractions import Fraction

from research import intraday_campaign_account as account
from research import intraday_campaign_account_inputs as inputs
from research import intraday_campaign_cash_book as cash
from research import intraday_pattern_calibration as cc


MODE = "ec-p01-p02-p03-observed-account-comparison"
MODES = ("A", "B", "A+B")
COSTS = (33, 43)
SCOPES = ("continuous", "day_local_counterfactual")


def _week(session: str) -> str:
    day = date.fromisoformat(session)
    return (day - timedelta(days=day.weekday())).isoformat()


def _plans(projections: tuple[Mapping, Mapping], policy: str, day: str, mode: str) -> tuple:
    strategies = {"A", "B"} if mode == "A+B" else {mode}
    return tuple(account.CampaignCandidate(**{key: value for key, value in row.items()
        if key not in {"clock_eligible", "clock_ineligible_reason"}})
        for projection in projections if projection["setup_id"][-1] in strategies
        for row in projection["variants"][policy]["dates"][day]["candidates"]
        if row["clock_eligible"])


def _coverage(projections: tuple[Mapping, Mapping], policy: str, day: str, mode: str) -> dict:
    strategies = {"A", "B"} if mode == "A+B" else {mode}
    by_setup = {p["setup_id"]: deepcopy(p["variants"][policy]["dates"][day]["counts"])
                for p in projections if p["setup_id"][-1] in strategies}
    return {"by_setup": by_setup,
            "unknown_signal_units": sum(row["unknown_signals"] for row in by_setup.values()),
            "clock_ineligible_units": sum(row["clock_ineligible"] for row in by_setup.values()),
            "recorded_signal_units": sum(row["signals"] for row in by_setup.values())}


def _closed_lots(initial_state: account.AccountState, result: account.AccountResult) -> list[dict]:
    """Match known full fills, including a carried lot, without outcome labels."""
    entries = {}
    for held in initial_state.holdings:
        plan = held.candidate
        lot = next(lot for lot in initial_state.book.lots if lot.symbol == plan.symbol)
        entries[plan.strategy, plan.symbol, plan.decision_at] = {
            "at": plan.entry_at, "quantity": lot.quantity,
            "cash_delta": -(lot.quantity * lot.entry_price + lot.entry_cost),
            "assumed_cost": lot.entry_cost}
    closed = []
    for event in result.events:
        key = (event.strategy, event.symbol, event.decision_at)
        if event.kind == "entry" and event.quantity:
            entries[key] = asdict(event)
        elif event.kind == "exit" and event.quantity:
            entry = entries.pop(key)
            net = entry["cash_delta"] + event.cash_delta
            closed.append({"strategy": event.strategy, "symbol": event.symbol,
                "decision_at": event.decision_at, "entry_at": entry["at"], "exit_at": event.at,
                "quantity": event.quantity, "entry_notional": -entry["cash_delta"] - entry["assumed_cost"],
                "exit_notional": event.cash_delta, "assumed_cost": entry["assumed_cost"],
                "net_pnl": net, "gross_pnl": net + entry["assumed_cost"],
                "identification": "identified_before_unknown" if result.state.unknown_at is None
                    or event.at < result.state.unknown_at else "conditional_after_or_at_unknown"})
    return closed


def compact_day(day_input: Mapping, initial_state: account.AccountState,
                result: account.AccountResult, coverage: Mapping) -> dict:
    """Reduce one replay once; no minute NAV vector survives the return value.

    Exposure integrates the final known valuation at each clock over [t,t+1).
    It stops at the first unknown decision, so incomplete exposure is explicitly
    a prefix, never a fabricated zero for the unobservable remainder.
    """
    opening, closing = day_input["session_open"], day_input["session_close"]
    state = result.state
    snapshots = result.snapshots
    start_nav = (snapshots[0].nav if snapshots and snapshots[0].at == opening
                 and snapshots[0].phase == "before_entries" else None)
    final_nav = (snapshots[-1].nav if snapshots and snapshots[-1].at == closing
                 and not state.book.indeterminate else None)
    identified = start_nav is not None and final_nav is not None
    net = final_nav - start_nav if identified else None
    cost = sum((event.assumed_cost for event in result.events), Fraction())
    end = min(closing, state.unknown_at) if state.unknown_at is not None else closing
    end = max(opening, end)
    exposure_minutes = Fraction()
    exposure_fraction_minutes = Fraction()
    maximum_exposure = Fraction()
    maximum_exposure_fraction = Fraction()
    current = None

    def accumulate(snapshot, until):
        nonlocal exposure_minutes, exposure_fraction_minutes
        if snapshot is not None and until > snapshot.at:
            minutes = Fraction((until - snapshot.at) // timedelta(seconds=1), 60)
            exposure = snapshot.nav - snapshot.cash
            exposure_minutes += minutes if exposure > 0 else 0
            exposure_fraction_minutes += exposure / snapshot.nav * minutes

    for snapshot in snapshots:
        if current is not None and snapshot.at != current.at:
            accumulate(current, min(snapshot.at, end))
        current = snapshot
        exposure = snapshot.nav - snapshot.cash
        maximum_exposure = max(maximum_exposure, exposure)
        maximum_exposure_fraction = max(maximum_exposure_fraction, exposure / snapshot.nav)
    accumulate(current, end)
    reasons = Counter(event.reason for event in result.events if event.kind == "entry" and not event.quantity)
    events = [asdict(event) for event in result.events]
    entries = [event for event in result.events if event.kind == "entry" and event.quantity]
    exits = [event for event in result.events if event.kind == "exit" and event.quantity]
    identified_exits = sum(state.unknown_at is None or event.at < state.unknown_at for event in exits)
    return {"session": day_input["session"], "week": _week(day_input["session"]),
        "status": "identified" if identified else "indeterminate",
        "full_universe_status": "indeterminate_missing_signals" if coverage["unknown_signal_units"]
            else "recorded_signal_population_only",
        "coverage": deepcopy(dict(coverage)), "start_nav": start_nav, "final_nav": final_nav,
        "net_pnl": net, "gross_pnl_same_fills": net + cost if net is not None else None,
        "assumed_cost": cost, "events": events,
        "known_entry_count": len(entries),
        "unique_entry_count": len({(event.strategy, event.symbol, event.decision_at) for event in entries}),
        "known_exit_count": len(exits), "exit_count_scope": "modeled_observed_OPEN_exits",
        "identified_exit_count": identified_exits, "conditional_exit_count": len(exits) - identified_exits,
        "entry_rejections": dict(sorted(reasons.items())),
        "same_symbol_declines": reasons["duplicate_position"],
        "cash_rejections": reasons["insufficient_cash_for_one_share"],
        "size_rejections": reasons["one_share_exceeds_nav_limit"],
        "risk_rejections": sum(count for reason, count in reasons.items()
                               if reason.startswith("entry_blocked:") and reason != "entry_blocked:account_indeterminate"),
        "closed_lots": _closed_lots(initial_state, result),
        "exposure": {"status": "complete" if identified else "identified_prefix_only",
            "identified_minutes": (end - opening) // timedelta(minutes=1),
            "invested_minutes": exposure_minutes, "fraction_minutes": exposure_fraction_minutes,
            "maximum_value": maximum_exposure, "maximum_fraction": maximum_exposure_fraction},
        "prefix_maximum_drawdown": state.maximum_drawdown,
        "last_identified_valuation": asdict(snapshots[-1]) if snapshots else None,
        "terminal": {"cash": state.book.cash,
            "cash_scope": "conditional_bookkeeping_after_first_unknown" if state.book.indeterminate
                else "identified_observed_candidate_scenario",
            "sleeves": [asdict(s) for s in state.book.sleeves],
            "lots": [asdict(lot) for lot in state.book.lots],
            "holdings": [asdict(held) for held in state.holdings],
            # Every eligible cap precedes close. Any terminal lot has an
            # unresolved scheduled exit, whose later marks cannot repair it.
            "marks": {lot.symbol: None for lot in state.book.lots},
            "equity_status": "identified" if final_nav is not None else "indeterminate",
            "unknown_at": state.unknown_at, "unknown_reasons": list(state.book.indeterminate_reasons),
            "daily_anchor": asdict(state.daily_anchor) if state.daily_anchor else None,
            "weekly_anchor": asdict(state.weekly_anchor) if state.weekly_anchor else None,
            "monthly_anchor": asdict(state.monthly_anchor) if state.monthly_anchor else None}}


def _weekly(days: list[dict], weeks: list[str], reference: list[str]) -> dict:
    result = {}
    for week in weeks:
        rows = [row for row in days if row["week"] == week]
        expected = sum(_week(day) == week for day in reference)
        complete = bool(rows) and len(rows) == expected and all(row["status"] == "identified" for row in rows)
        entries = sum(row["known_entry_count"] for row in rows)
        result[week] = {"reference_days": expected, "included_days": len(rows),
            "identified_days": sum(row["status"] == "identified" for row in rows),
            "status": "complete_observed_candidate_week" if complete else "incomplete",
            "known_entry_count": entries, "entry_count_if_complete": entries if complete else None,
            "unknown_signal_units": sum(row["coverage"]["unknown_signal_units"] for row in rows),
            "net_pnl_if_complete": sum((row["net_pnl"] for row in rows), Fraction()) if complete else None}
    return result


def _concentration(days: list[dict]) -> dict:
    by_date, by_symbol = {}, {}
    conditional = []
    identified_count = 0
    for day in days:
        for lot in day["closed_lots"]:
            if lot["identification"] != "identified_before_unknown":
                conditional.append(lot)
                continue
            identified_count += 1
            by_date[day["session"]] = by_date.get(day["session"], Fraction()) + lot["net_pnl"]
            by_symbol[lot["symbol"]] = by_symbol.get(lot["symbol"], Fraction()) + lot["net_pnl"]

    def describe(values):
        denominator = sum(map(abs, values.values()), Fraction())
        return {"net_pnl": values, "absolute_net_pnl_share": {
            key: abs(value) / denominator if denominator else None for key, value in values.items()},
            "sum_absolute_group_net_pnl": denominator}

    return {"scope": "identified_closed_lots_strictly_before_first_unknown_only",
            "identified_closed_lot_count": identified_count,
            "conditional_closed_lot_count": len(conditional),
            "conditional_net_pnl": sum((lot["net_pnl"] for lot in conditional), Fraction()),
            "conditional_gross_pnl": sum((lot["gross_pnl"] for lot in conditional), Fraction()),
            "conditional_scope": "bookkeeping_after_or_at_unknown_not_confirmed_realized_pnl",
            "by_date": describe(by_date), "by_symbol": describe(by_symbol)}


def summarize_days(days: list[dict], weeks: list[str], reference: list[str] | None = None) -> dict:
    """Descriptive quantities only; caller must compute all own sizing first."""
    known = [row for row in days if row["status"] == "identified"]
    reference = reference if reference is not None else [row["session"] for row in days]
    n = len(known)
    return {"reference_days": len(reference), "included_days": len(days), "excluded_days": len(reference) - len(days),
        "identified_days": n, "indeterminate_days": len(days) - n,
        "complete_zero_trade_days": sum(row["known_entry_count"] == 0 for row in known),
        "mean_daily_net_pnl": sum((row["net_pnl"] for row in known), Fraction()) / n if n else None,
        "mean_daily_gross_pnl_same_fills": sum((row["gross_pnl_same_fills"] for row in known), Fraction()) / n if n else None,
        "mean_daily_net_per_initial_capital": sum((row["net_pnl"] for row in known), Fraction()) / (n * cash.INITIAL_CAPITAL) if n else None,
        "mean_daily_gross_per_initial_capital": sum((row["gross_pnl_same_fills"] for row in known), Fraction()) / (n * cash.INITIAL_CAPITAL) if n else None,
        "known_total_assumed_cost": sum((row["assumed_cost"] for row in days), Fraction()),
        "known_unique_entry_count": sum(row["unique_entry_count"] for row in days),
        "known_entry_count": sum(row["known_entry_count"] for row in days),
        "known_exit_count": sum(row["known_exit_count"] for row in days),
        "exit_count_scope": "modeled_observed_OPEN_exits",
        "identified_exit_count": sum(row["identified_exit_count"] for row in days),
        "conditional_exit_count": sum(row["conditional_exit_count"] for row in days),
        "entry_count_if_complete": sum(row["known_entry_count"] for row in days) if n == len(reference) else None,
        "known_entries_per_reference_week": Fraction(sum(row["known_entry_count"] for row in days), len(weeks)) if weeks else None,
        "frequency_status": "complete_observed_candidates" if n == len(reference) else "known_entries_only_incomplete_calendar",
        "same_symbol_declines": sum(row["same_symbol_declines"] for row in days),
        "cash_rejections": sum(row["cash_rejections"] for row in days),
        "size_rejections": sum(row["size_rejections"] for row in days),
        "risk_rejections": sum(row["risk_rejections"] for row in days),
        "by_week": _weekly(days, weeks, reference), "closed_lot_concentration": _concentration(days)}


def own_sizing(days: list[dict], reference: list[str], *, label_field: str = "net_pnl",
               peer_days: list[dict] | None = None) -> dict:
    """Plan from newly reconstructed daily labels, in initial-account units.

    For a paired contrast use the same dated intersection and the larger of
    the two mean paid-cost scales. That cost scale is a disclosed planning
    benchmark, not a formal power guarantee for a selection advantage.
    Missing dates retain None, while identified zero-trade days contribute 0.
    The existing fixed-five-session, seed-zero dispersion method is unchanged.
    """
    if label_field not in ("net_pnl", "gross_pnl_same_fills"):
        raise ValueError("unknown_account_label")
    primary = {row["session"]: row for row in days}
    peer = {row["session"]: row for row in peer_days} if peer_days is not None else None
    if len(primary) != len(days) or not set(primary) <= set(reference):
        raise ValueError("invalid_own_sizing_dates")
    if peer is not None and (len(peer) != len(peer_days) or not set(peer) <= set(reference)):
        raise ValueError("invalid_peer_sizing_dates")
    records, costs, peer_costs = [], [], []
    for day in reference:
        row, other = primary.get(day), peer.get(day) if peer is not None else None
        known = row is not None and row["status"] == "identified" and (
            peer is None or other is not None and other["status"] == "identified")
        label = None
        if known:
            label = (row[label_field] - (other[label_field] if other is not None else 0)) / cash.INITIAL_CAPITAL
            costs.append(row["assumed_cost"] / cash.INITIAL_CAPITAL)
            if other is not None:
                peer_costs.append(other["assumed_cost"] / cash.INITIAL_CAPITAL)
        records.append({"session": day, "label": label})
    plan = cc.dispersion(records, reference)
    values = [row["label"] for row in records if row["label"] is not None]
    benchmark = sum(costs, Fraction()) / len(costs) if costs else None
    if peer_costs:
        benchmark = max(benchmark, sum(peer_costs, Fraction()) / len(peer_costs))
    benchmark_bps = benchmark * 10_000 if benchmark is not None else None
    constant = len(values) > 1 and len(set(values)) == 1
    defined = (plan["design_se"] is not None and plan["design_se"] > 0
               and plan["mde_bps"] is not None and benchmark is not None
               and benchmark > 0 and not constant)
    underpowered = bool(defined and plan["mde_bps"] > float(benchmark_bps))
    reason = ("no_known_labels" if not values else "fewer_than_two_known_days" if len(values) < 2
              else "zero_dispersion" if constant else "zero_paid_cost_benchmark" if benchmark == 0
              else plan["planning_constraint"] if not defined else None)
    plan.update(label=label_field, label_unit="daily_cash_pnl_divided_by_initial_capital",
        status="uninformative" if not defined else "underpowered" if underpowered else "descriptive_only",
        cost_floor_bps_assumed=benchmark_bps, economic_benchmark_bps=benchmark_bps,
        economic_benchmark_exact=benchmark,
        benchmark_formula="max(mean_own_cost,mean_peer_cost)/initial_capital" if peer is not None
            else "mean_assumed_cost_on_own_identified_days/initial_capital",
        benchmark_scope="comparison_planning_cost_scale_not_formal_power_guarantee",
        planning_constraint=reason if not defined else "mde_exceeds_account_cost_scale" if underpowered
            else "descriptive_only_no_certification",
        planning_constraint_scope="EC observed-only own account labels; underpower disclosure retained",
        would_require_operator_underpower_choice=False, requires_operator_choice=False,
        underpowered_descriptive_authorized=True, uninformative_reason=reason,
        family_adopted=False, full_family_certified=False, strategy_pass=False,
        sizing_pass=False, method="existing_naive_session_fixed5session_block2000_seed0")
    return plan


def _comparison_days(scenarios: Mapping, policy: str, cost: int) -> tuple[list[str], dict]:
    all_days = {mode: scenarios[f"{policy}|{cost}|{mode}"]["day_local_counterfactual"]["days"]
                for mode in MODES}
    common = set.intersection(*({row["session"] for row in days if row["status"] == "identified"}
                               for days in all_days.values()))
    return sorted(common), {mode: [row for row in days if row["session"] in common]
                            for mode, days in all_days.items()}


def _finish_statistics(report: dict, checkpoint: Callable | None) -> None:
    """All new own-label plans finish before any public profit mean is built."""
    scenarios, reference, weeks = report["scenarios"], report["reference_sessions"], report["reference_weeks"]
    labels = ("net_pnl", "gross_pnl_same_fills")
    for scenario in scenarios.values():
        for scope in SCOPES:
            content = scenario[scope]
            content["own_sizing"] = {label: own_sizing(content["days"], reference, label_field=label)
                                     for label in labels}
    comparisons = {}
    for policy in inputs.POLICIES:
        for cost in COSTS:
            common, days = _comparison_days(scenarios, policy, cost)
            pairs = (("A+B", "A"), ("A+B", "B"))
            comparisons[f"{policy}|{cost}"] = {
                "timing_policy": policy, "cost_bps": cost,
                "scope": "three_common_identified_fresh_daily_accounts",
                "reference_sessions": reference, "reference_weeks": weeks,
                "common_sessions": common, "excluded_sessions": sorted(set(reference) - set(common)),
                "own_sizing": {mode: {label: own_sizing(days[mode], reference, label_field=label)
                                      for label in labels} for mode in MODES},
                "paired_sizing": {f"{left}_minus_{right}": own_sizing(days[left], reference, peer_days=days[right])
                                  for left, right in pairs},
                "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
    report["comparisons"] = comparisons
    if checkpoint is not None:
        checkpoint("sizing_complete", {"scenario_scopes": len(scenarios) * len(SCOPES),
                                       "common_comparisons": len(comparisons)})
    for scenario in scenarios.values():
        for scope in SCOPES:
            scenario[scope]["summary"] = summarize_days(scenario[scope]["days"], weeks, reference)
    for comparison in comparisons.values():
        common, days = _comparison_days(scenarios, comparison["timing_policy"], comparison["cost_bps"])
        comparison["accounts"] = {mode: summarize_days(days[mode], weeks, reference) for mode in MODES}
        comparison["paired_mean_daily_net_per_initial_capital"] = {
            key: (comparison["accounts"][left]["mean_daily_net_per_initial_capital"]
                  - comparison["accounts"][right]["mean_daily_net_per_initial_capital"]) if common else None
            for left, right in (("A+B", "A"), ("A+B", "B"))
            for key in (f"{left}_minus_{right}",)}
    if checkpoint is not None:
        checkpoint("summary_complete", {"comparisons": len(comparisons)})


def build(a_projection: Mapping, b_projection: Mapping, price_chunks: Iterable[Mapping],
          checkpoint: Callable | None = None, verify_day: Callable | None = None) -> dict:
    """Consume one complete authenticated union; replay exactly 24 times/day.

    ``verify_day`` is called with keyword arguments ``day_input``, ``candidates``,
    ``initial_state``, ``result``, ``compact`` and ``scenario`` after each replay.
    It may independently reconstruct using that same day without any extra IO;
    raising aborts this build. No callback is not a verified-output claim.
    ``checkpoint(stage, payload)`` is a progress/budget seam, never a resume or
    permission bypass. Its exceptions also propagate without automatic retries.
    """
    projections = (a_projection, b_projection)
    reference = list(a_projection["reference_sessions"])
    weeks = list(a_projection["reference_weeks"])
    if not reference or reference != sorted(set(reference)) or weeks != sorted({_week(day) for day in reference}):
        raise inputs.AccountInputsRefusal("invalid_account_reference_calendar")
    states, scenarios = {}, {}
    for policy in inputs.POLICIES:
        for cost in COSTS:
            for mode in MODES:
                key = f"{policy}|{cost}|{mode}"
                states[key] = account.new_state(mode, cost_bps=cost)
                scenarios[key] = {"timing_policy": policy, "cost_bps": cost, "mode": mode,
                                  **{scope: {"days": []} for scope in SCOPES}}
    consumed_days, total_chunks, total_rows = [], 0, 0
    for day_input in inputs.iter_account_price_days(*projections, price_chunks):
        day = day_input["session"]
        consumed_days.append(day)
        total_chunks += day_input["consumed_target_chunks"]
        total_rows += day_input["consumed_price_rows"]
        parsed_day = date.fromisoformat(day)
        openings, closes = {parsed_day: day_input["session_open"]}, {parsed_day: day_input["session_close"]}
        for policy in inputs.POLICIES:
            for mode in MODES:
                plans = _plans(projections, policy, day, mode)
                coverage = _coverage(projections, policy, day, mode)
                for cost in COSTS:
                    key = f"{policy}|{cost}|{mode}"
                    for scope in SCOPES:
                        initial = states[key] if scope == "continuous" else account.new_state(
                            mode, cost_bps=cost, risk_scope="day_local_counterfactual")
                        result = account.replay_account(plans, day_input["minute_bars"],
                            day_input["open_prices"], openings, closes,
                            mode=mode, cost_bps=cost, state=initial, compress_idle_minutes=True)
                        if scope == "day_local_counterfactual":
                            result = replace(result, scenario="fresh_daily_counterfactual_no_continuous_weekly_monthly_claim")
                        compact = compact_day(day_input, initial, result, coverage)
                        scenario = {"timing_policy": policy, "cost_bps": cost, "mode": mode,
                                    "risk_scope": scope, "compress_idle_minutes": True}
                        if verify_day is not None:
                            verify_day(day_input=day_input, candidates=plans, initial_state=initial,
                                       result=result, compact=compact, scenario=scenario)
                        scenarios[key][scope]["days"].append(compact)
                        if scope == "continuous":
                            states[key] = result.state
                        del result
        if checkpoint is not None:
            checkpoint("day_complete", {"session": day, "completed_days": len(consumed_days),
                                        "day_replays": 24})
    if consumed_days != reference:
        raise inputs.AccountInputsRefusal("account_reference_sessions_changed")
    report = {"mode": MODE, "reference_sessions": reference, "reference_weeks": weeks,
        "initial_capital": cash.INITIAL_CAPITAL,
        "input_evidence": {"A": deepcopy(a_projection), "B": deepcopy(b_projection)},
        "consumed_target_chunks": total_chunks, "consumed_price_rows": total_rows,
        "original_union_complete": True, "day_replay_count": len(reference) * 24,
        "idle_minutes_compressed": True,
        "scenarios": scenarios, "independent_day_callback_completed": verify_day is not None,
        "account_path_scope": "recorded_detectable_candidates_conditioned_on_archive_availability",
        "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
        "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
        "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
        "performance_computed": True, "strategy_pass": False, "promotion_allowed": False,
        "strategy_certification": False, "historical_brokerage_account_claim": False}
    _finish_statistics(report, checkpoint)
    return report
