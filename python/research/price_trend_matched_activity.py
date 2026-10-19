"""Pure matched-count TREND control over the authenticated saved COMBO family.

Only the first m original TREND targets are replayed, where m is the original
COMBO target count. Original k/20 book cash and twenty-slot arithmetic survive;
reporting uses the common m/20 assigned-target denominator retrospectively.
This module performs no input loading, evidence certification or promotion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, localcontext
import math
from statistics import NormalDist, stdev
from types import SimpleNamespace

from research import activity_cohort_comparison as cohort
from research import price_trend_activity_comparison as combo
from research import price_trend_comparison as trend
from research.activity_accounting import sum_decimal_values
from research.activity_cohort_connection import CohortScopeBinding, _validate_cohort_scope, verify_cohort_inventory
from research.activity_cohort_return_policy import SCHEMA_V3, decode_return_policy, policy_report
from research.activity_paired_replay import _json_value
from research.activity_portfolio import calibration
from research.activity_preflight import digest, encoded, require, strict_json
from research.activity_quote_extension import _same
from research.activity_raw_quote_view import _immutable_fields, _prepare_raw_quote_context, _unchanged_fields
from research.activity_replay import _replay_synthetic_from_context, replay_synthetic
from research.activity_timing import SessionLagPolicy
from research.price_trend_signal import select_price_trend


GROUP = "TREND_MATCHED"
SCHEMA = "price-trend-matched-activity-v1"
ONE, SLOTS = Decimal(1), Decimal(20)
FALSE_FLAGS = {name: False for name in ("whole_source_population_complete", "source_truth_certified",
    "event_coverage_certified", "price_basis_certified", "historical_vintage_certified",
    "actual_input_reuse_readiness_established", "actual_study_completed",
    "comparison_power_prerequisite_met", "power_design_passed", "promotion_allowed")}


@dataclass(frozen=True)
class MatchedTrendCohortDeclaration:
    """A genuine new group retaining its COMBO proof and original TREND rank."""
    combo_declaration: combo.ComboCohortDeclaration
    chosen_codes: tuple[str, ...]
    group_id: str = field(default=GROUP, init=False)

    @property
    def arm(self): return self.combo_declaration.arm
    @property
    def formation_on(self): return self.combo_declaration.formation_on
    @property
    def decision_on(self): return self.combo_declaration.decision_on
    @property
    def selection_at(self): return self.combo_declaration.selection_at
    @property
    def activity_targets(self): return self.combo_declaration.activity_targets
    @property
    def original_activity_targets(self): return self.activity_targets
    @property
    def source_population(self): return self.combo_declaration.source_population
    @property
    def source_population_count(self): return self.combo_declaration.source_population_count
    @property
    def source_population_sha256(self): return self.combo_declaration.source_population_sha256
    @property
    def source_partition_sha256(self): return self.combo_declaration.source_partition_sha256
    @property
    def target_ceiling(self): return self.combo_declaration.target_ceiling
    @property
    def eligible_shortfall(self): return self.target_ceiling - len(self.chosen_codes)


def _identity(row):
    return digest(encoded(_json_value(asdict(row))))


def restore_matched_declarations(rows) -> tuple[MatchedTrendCohortDeclaration, ...]:
    """Decode exact saved canonical declarations; replay revalidates the proof."""
    require(type(rows) is list and bool(rows), "complete saved matched declarations required")
    result = []
    for raw in rows:
        saved = combo._exact_fields(raw, MatchedTrendCohortDeclaration)
        nested = combo._exact_fields(saved["combo_declaration"], combo.ComboCohortDeclaration)
        original, = combo.restore_trend_declarations([nested["trend_declaration"]])
        checks = []
        require(type(nested["activity_checks"]) is list, "saved immutable activity checks required")
        for value in nested["activity_checks"]:
            check = combo._exact_fields(value, combo.ComboActivityCheck)
            for name in ("normal_dates", "skipped_frozen_dates"):
                require(type(check[name]) is list, "saved immutable activity date lists required")
                check[name] = tuple(combo._day(day) for day in check[name])
            for name in ("formation_turnover", "activity_median", "activity_ratio"):
                if check[name] is not None:
                    check[name] = combo._decimal(check[name])
            checks.append(combo.ComboActivityCheck(**check))
        require(type(nested["chosen_codes"]) is list and type(saved["chosen_codes"]) is list,
            "saved immutable chosen code lists required")
        original_combo = combo.ComboCohortDeclaration(original, tuple(nested["chosen_codes"]),
            tuple(checks), nested["activity_audit_json"])
        declaration = MatchedTrendCohortDeclaration(original_combo, tuple(saved["chosen_codes"]))
        require(encoded(_json_value(asdict(declaration))) == encoded(raw), "noncanonical saved matched declaration")
        result.append(declaration)
    return tuple(result)


def build_matched_declarations(combo_declarations, *, universe, parameters) -> tuple[MatchedTrendCohortDeclaration, ...]:
    """Revalidate COMBO, then take only the old TREND prefix of its target count."""
    universe = trend._universe(universe, parameters)
    originals = combo._declarations(combo_declarations, universe, parameters)
    return tuple(MatchedTrendCohortDeclaration(row,
        row.trend_declaration.chosen_codes[:len(row.chosen_codes)]) for row in originals)


def _declarations(rows, universe, parameters):
    require(type(rows) is tuple and all(type(row) is MatchedTrendCohortDeclaration for row in rows),
        "immutable explicit TREND_MATCHED declarations required")
    expected = build_matched_declarations(tuple(row.combo_declaration for row in rows),
        universe=universe, parameters=parameters)
    require(_same(tuple(sorted(rows, key=cohort._key)), expected),
        "TREND_MATCHED original COMBO, ranked prefix or budget changed")
    return expected


def replay_matched_trend_cohorts(declarations, holding_inputs, events, *, universe, parameters,
                               raw_quote_view=None, scope_binding=None, before_cohort=None, on_cohort=None) -> dict:
    """Replay only the new matched family through the unchanged cohort engine."""
    trend._parameters(parameters)
    require(all(value is None or callable(value) for value in (before_cohort, on_cohort)), "callable cohort callbacks required")
    universe = trend._universe(universe, parameters)
    rows = _declarations(declarations, universe, parameters)
    raw_context = None
    if raw_quote_view is None:
        quotes = cohort._quotes(holding_inputs, None)
    else:
        raw_context = _prepare_raw_quote_context(raw_quote_view)
        quotes, _ = raw_context[1](raw_context[0])
        require(_same(quotes.base, holding_inputs), "matched raw view must preserve the complete supplied holding base")
    require(quotes.calendar == universe.calendar, "matched and original holding calendars differ")
    ordered_events = cohort._events(events, quotes, parameters, raw_quote_view)
    binding, policy_json = None, None
    if scope_binding is not None:
        require(type(scope_binding) is CohortScopeBinding
            and _same(scope_binding.declarations, universe.original_declarations), "matched proof must retain the original BS family")
        binding = _validate_cohort_scope(scope_binding, universe.original_declarations, quotes, ordered_events,
            parameters=parameters, _raw_context=raw_context)
        policy_json = binding.return_policy_json
        policy = decode_return_policy(policy_json)
        require(policy is not None and policy["schema"] == SCHEMA_V3, "unchanged approved five-issuer return policy required")
        bounds = {row[:4]: row[4] for row in binding.origin_bounds}
        originals = {(row.arm, row.formation_on, code, row.decision_on) for row in rows
            for code in row.combo_declaration.trend_declaration.chosen_codes}
        require(originals <= set(bounds), "original TREND origin outside original conditional proof union")
        needed = {(row.arm, row.formation_on, code, row.decision_on) for row in rows for code in row.chosen_codes}
        cohort._coverage(rows, quotes, ordered_events, parameters,
            origin_bounds=tuple(sorted((*key, bounds[key]) for key in needed)))
        cohort.require_supported_attribution_actions(ordered_events, return_policy_json=policy_json)
    else:
        cohort._coverage(rows, quotes, ordered_events, parameters)
    end = universe.calendar.index(date.fromisoformat(parameters["end"]))
    days = universe.calendar[parameters["lookback"] + 1:end + 1]
    initial = cohort.ActivityBook(ONE, (), universe.calendar[parameters["lookback"]])
    additional = (universe, rows, quotes, binding, holding_inputs, raw_quote_view)
    sealed = _immutable_fields(additional) if raw_context is None else raw_context[1](raw_context[0], additional_seal=additional)
    parameter_bytes = encoded(parameters)
    engine = replay_synthetic if raw_context is None else lambda *args, **kwargs: _replay_synthetic_from_context(*raw_context, *args, **kwargs)

    def unchanged():
        if raw_context is not None:
            raw_context[1](raw_context[0])
        require(encoded(parameters) == parameter_bytes and _unchanged_fields(sealed), "matched prepared inputs or parameters changed")

    results = []
    with localcontext() as context:
        context.prec = 28
        for row in rows:
            unchanged()
            if before_cohort is not None:
                before_cohort(row)
                unchanged()
            verify = None if binding is None else lambda replay, arm=row.arm: verify_cohort_inventory(binding, arm, replay)
            result = cohort._execute_one_cohort(row, declarations=rows, quotes=quotes, ordered_events=ordered_events,
                parameters=parameters, days=days, initial=initial, engine=engine,
                raw_quote_view=quotes if raw_context is not None else None, return_policy_json=policy_json,
                verify_inventory=verify, attribution=trend._attribution)
            result.update(original_combo_chosen_codes=row.combo_declaration.chosen_codes,
                original_trend_chosen_codes=row.combo_declaration.trend_declaration.chosen_codes,
                matched_target_count=len(row.chosen_codes), matched_target_budget=Decimal(len(row.chosen_codes)) / SLOTS,
                matched_selection_identity=_identity(row))
            if on_cohort is not None:
                on_cohort(result)
                unchanged()
            results.append(result)
        original = {arm: {GROUP: cohort._summary(tuple(row for row in results if row["arm"] == arm), days,
            return_policy_json=policy_json)} for arm in cohort.ARMS}
        matched = {arm: {GROUP: cohort._summary(tuple({**row, "assigned_capital": row["matched_target_budget"]}
            for row in results if row["arm"] == arm), days, return_policy_json=policy_json)} for arm in cohort.ARMS}
    return {"schema": SCHEMA, "status": "pure_matched_target_price_return_diagnostic",
        "parameters": strict_json(parameter_bytes), "calendar": universe.calendar, "cohorts": tuple(results),
        "aggregates": matched, "original_budget_aggregates": original,
        "cohort_evaluation_counts": {arm: sum(row["arm"] == arm for row in results) for arm in cohort.ARMS},
        "reuse_identity": universe.reuse_identity, "activity_snapshot_sha256": quotes.activity_snapshot_sha256,
        "quote_snapshot_sha256": quotes.quote_snapshot_sha256, "initial_nav_per_cohort": ONE,
        "old_combo_books_replayed": 0, "old_trend_books_replayed": 0, "old_control_books_replayed": 0,
        "original_budget_preserved": True, "rank_recomputed_for_matched": False, "replacement_targets": 0,
        "primary_denominator": "sum(COMBO target count / 20), identical for COMBO and TREND_MATCHED",
        "future_counts_used_only_for_ex_post_attribution": True, "realized_exposure_matched": False,
        "conditional_exit_bounds_consumed": binding is not None,
        "conditional_scope_binding_sha256": None if binding is None else binding.binding_sha256,
        **FALSE_FLAGS, **policy_report(policy_json)}


def _calibrate(summary, group, parameters):
    path = summary["metrics"]["path"]
    values = [float(row["session_return"]) for row in path if row["session_return"] is not None]
    require((not values and summary["assigned_capital"] == 0 and summary["filled_entries"] == 0)
        or (len(values) == len(path) and all(math.isfinite(value) for value in values)), "complete finite own return path or explicit unassigned cash required")
    sd = stdev(values) if len(values) >= 2 else None
    sd = sd if sd is not None and math.isfinite(sd) else None
    floor = parameters["sizing_round_trip_bps"] / 1e4 / parameters["holding_sessions"] * 1e4
    base = {"sessions": len(path), "filled_entries": summary["filled_entries"], "session_return_sd": sd,
        "power_design_passed": False, "comparison_power_prerequisite_met": False, "promotion_allowed": False,
        "cost_floor_exposure_matched": False,
        "original_cost_floor_daily_bps": floor, "cost_floor_daily_bps": floor, "cost_floor_resolvable": False,
        "cost_floor_resolvable_diagnostic": None,
        "interpretation": f"own {group} dispersion only; old cost floor is not exposure matched; no mean return or edge claim"}
    if summary["filled_entries"] == 0 or sd in (None, 0):
        return {**base, "status": "undefined_no_finite_nonzero_invested_dispersion", "iid_session_se": None,
            "block_se": None, "corrected_to_naive_se": None, "detectable_daily_bps": None}
    value = calibration({"returns": values, "diagnostics": {"filled_entries": summary["filled_entries"]},
        "closed_trades": summary["ordinary_closed_trades"], "unresolved_positions": summary["terminal_open_lots"]}, parameters)
    se = max(value["iid_session_se"], *value["block_se"].values())
    normal = NormalDist()
    mde = (normal.inv_cdf(1 - parameters["alpha"]) + normal.inv_cdf(parameters["power"])) * se * 10000
    return {**value, **base, "status": "own_dispersion_calibrated", "detectable_daily_bps": mde,
        "corrected_to_naive_se": se / value["iid_session_se"], "cost_floor_resolvable_diagnostic": mde <= floor,
        "standard_error_policy": "maximum of own IID and all registered own circular-block SEs"}


def blind_matched_calibration(replay: dict, parameters: dict) -> dict:
    """Own authentic matched-group dispersion; no fabricated COMBO relabeling."""
    trend._parameters(parameters)
    require(replay.get("schema") == SCHEMA and bool(replay["cohorts"])
        and {row["arm"] for row in replay["cohorts"]} == set(cohort.ARMS)
        and all(row.get("group_id") == GROUP for row in replay["cohorts"]),
        "authentic TREND_MATCHED replay required")
    return {arm: _calibrate(replay["aggregates"][arm][GROUP], GROUP, parameters) for arm in cohort.ARMS}


def _project(row, declaration, group, days, policy):
    amount = combo._amount
    original = declaration.combo_declaration
    codes = declaration.chosen_codes if group == GROUP else original.chosen_codes
    identity = "matched_selection_identity" if group == GROUP else "combo_selection_identity"
    expected = declaration if group == GROUP else original
    budget = Decimal(original.target_ceiling) / SLOTS
    require(row.get("group_id") == group and row.get(identity) == _identity(expected)
        and tuple(row["chosen_codes"]) == codes and type(row["target_ceiling"]) is int
        and row["target_ceiling"] == original.target_ceiling
        and row["eligible_shortfall"] == original.target_ceiling - len(codes)
        and amount(row["assigned_capital"]) == budget
        and tuple(row["original_activity_targets"]) == original.activity_targets,
        "saved cohort selection identity, original targets or budget changed")
    require(tuple(row["key"][:2]) == (original.arm, group) and _day(row["key"][2]) == original.formation_on,
        "saved cohort key changed")
    for name in ("source_population_sha256", "source_partition_sha256"):
        require(row[name] == getattr(original, name), "saved cohort source identity changed")
    require(all(row.get(name) == value for name, value in policy.items()), "same approved return-policy labels required")
    if group == GROUP:
        require(tuple(row["original_combo_chosen_codes"]) == original.chosen_codes
            and tuple(row["original_trend_chosen_codes"]) == original.trend_declaration.chosen_codes
            and type(row["matched_target_count"]) is int and row["matched_target_count"] == len(codes)
            and amount(row["matched_target_budget"]) == Decimal(len(codes)) / SLOTS,
            "saved matched rank or target allocation changed")
    else:
        require(tuple(row["original_trend_chosen_codes"]) == original.trend_declaration.chosen_codes
            and type(row["retained_target_count"]) is int and row["retained_target_count"] == len(codes)
            and amount(row["retained_target_budget"]) == Decimal(len(codes)) / SLOTS,
            "saved COMBO retained target allocation changed")
    path = row["raw_metrics"]["path"]
    require(tuple(_day(point["date"]) for point in path) == days, "complete paired cohort sessions required")
    navs = tuple(amount(point["nav"]) for point in path)
    cohort._path_metrics(days, navs)
    inventory = row["exposure"]["path"]
    require(tuple(_day(point["date"]) for point in inventory) == days, "complete paired inventory sessions required")
    result = {**row, "assigned_capital": Decimal(len(codes)) / SLOTS, "original_assigned_capital": budget,
        "replay": SimpleNamespace(navs=navs),
        **{name: amount(row[name]) for name in ("filled_capital", "filled_principal", "entry_cost_amount")},
        "exposure": {"path": [{"marked_inventory_value": amount(point["marked_inventory_value"])} for point in inventory]}}
    require(type(row["filled_entries"]) is int and 0 <= row["filled_entries"] <= len(codes)
        and result["filled_capital"] == Decimal(row["filled_entries"]) / SLOTS, "fixed target fill count/capital changed")
    if policy:
        attribution = row.get(cohort.ATTRIBUTION_KEY)
        require(type(attribution) is dict and type(attribution.get("by_code")) is dict, "same-book five-issuer attribution required")
        result[cohort.ATTRIBUTION_KEY] = {**attribution, "by_code": {code: {**value,
            **{name: amount(value[name]) for name in ("entry_budget", "net_exit_cash", "terminal_marked_value", "pnl")}}
            for code, value in attribution["by_code"].items()}}
    return result


def _day(value):
    return value if type(value) is date else combo._day(value)


def _report_declarations(rows, calendar, parameters):
    """Recheck saved contracts; source pins and original universe proof stay external."""
    trend._parameters(parameters)
    require(type(rows) is tuple and bool(rows) and all(type(row) is MatchedTrendCohortDeclaration for row in rows),
        "explicit immutable authenticated matched declarations required")
    restored = restore_matched_declarations(_json_value(rows))
    require(_same(rows, restored), "saved matched declaration projection changed")
    keyed = {}
    end = calendar.index(date.fromisoformat(parameters["end"]))
    indices = SessionLagPolicy(1, paired=True).formation_indices(calendar, lookback=parameters["lookback"],
        holding_sessions=parameters["holding_sessions"], end_index=end)
    for row in rows:
        original = row.combo_declaration
        old, selection = original.trend_declaration, original.trend_declaration.selection
        fresh = select_price_trend(expected_candidates=selection.expected_candidates,
            expected_baseline_on=selection.expected_baseline_on, formation_on=old.formation_on,
            selection_at=old.selection_at, target_count=old.target_ceiling, observations=old.observations)
        require(_same(fresh, selection), "saved original TREND rank changed")
        audits = strict_json(original.activity_audit_json.encode())
        require(type(audits) is list and len(audits) == len(old.chosen_codes)
            and tuple(check.code for check in original.activity_checks) == old.chosen_codes,
            "complete ordered original TREND activity checks required")
        for audit, check in zip(audits, original.activity_checks, strict=True):
            expected = combo._activity_check(audit, old, check.code,
                (old.formation_on, dict(selection.expected_candidates)[check.code], check.normal_dates), calendar)
            require(_same(check, expected), "saved original COMBO activity check changed")
        require(original.chosen_codes == tuple(check.code for check in original.activity_checks if check.activity_pass)
            and row.chosen_codes == old.chosen_codes[:len(original.chosen_codes)], "saved matched prefix or COMBO subset changed")
        key = row.arm, row.formation_on
        policy = SessionLagPolicy(int(row.arm[-1]), paired=True)
        require(row.arm in cohort.ARMS and key not in keyed
            and row.decision_on == policy.execution_on(calendar, calendar.index(row.formation_on))
            and row.selection_at == policy.selection_at(calendar, calendar.index(row.formation_on)), "original matched schedule changed")
        keyed[key] = row
    require(set(keyed) == {(arm, calendar[index]) for arm in cohort.ARMS for index in indices},
        "complete original family including zero-budget formations required")
    return tuple(sorted(rows, key=cohort._key))


def _paired_inputs(combo_cohorts, matched_cohorts, *, declarations, universe, parameters, return_policy_json, calendar=None):
    if universe is not None:
        universe = trend._universe(universe, parameters)
        declarations = _declarations(declarations, universe, parameters)
        calendar = universe.calendar
    else:
        require(type(calendar) is tuple, "complete original report calendar required")
        SessionLagPolicy._calendar(calendar)
        declarations = _report_declarations(declarations, calendar, parameters)
    policy = policy_report(return_policy_json)
    require(not policy or policy.get("return_policy_schema") == SCHEMA_V3, "approved v3 return policy required")
    days = calendar[parameters["lookback"] + 1:calendar.index(date.fromisoformat(parameters["end"])) + 1]
    expected = {(row.arm, row.formation_on): row for row in declarations}
    projected = {group: {} for group in (combo.GROUP, GROUP)}
    for group, rows in ((combo.GROUP, combo_cohorts), (GROUP, matched_cohorts)):
        require(type(rows) is tuple, "immutable complete saved cohort tuples required")
        for row in rows:
            key = row["arm"], _day(row["formation_on"])
            require(key in expected and key not in projected[group], "unique original paired cohort key required")
            original = expected[key]
            require(_day(row["decision_on"]) == original.decision_on
                and (row["selection_at"].isoformat() if hasattr(row["selection_at"], "isoformat") else row["selection_at"]) == original.selection_at.isoformat(),
                "saved original decision and cutoff changed")
            projected[group][key] = _project(row, original, group, days, policy)
        require(set(projected[group]) == set(expected), "complete original family including zero-budget formations required")
    return declarations, projected, days, policy


def calibrate_saved_groups(combo_cohorts, matched_cohorts, *, declarations, universe, parameters,
                           return_policy_json=None) -> dict:
    """Recompute each group's own m/20 dispersion from saved books, without means."""
    _, projected, days, _ = _paired_inputs(combo_cohorts, matched_cohorts, declarations=declarations,
        universe=universe, parameters=parameters, return_policy_json=return_policy_json)
    with localcontext() as context:
        context.prec = 28
        return {arm: {group: _calibrate(cohort._summary(tuple(row for (lag, _), row in values.items() if lag == arm),
            days, return_policy_json=return_policy_json), group, parameters) for group, values in projected.items()}
            for arm in cohort.ARMS}


def comparison_report(matched_replay, combo_cohorts, *, declarations, parameters, universe=None, return_policy_json=None) -> dict:
    """Describe authenticated saved COMBO and matched books; no replay or IO."""
    declarations, projected, days, policy = _paired_inputs(combo_cohorts, matched_replay["cohorts"],
        declarations=declarations, universe=universe, parameters=parameters, return_policy_json=return_policy_json,
        calendar=tuple(_day(day) for day in matched_replay["calendar"]))
    require(matched_replay.get("schema") == SCHEMA and (universe is None or matched_replay.get("reuse_identity") == universe.reuse_identity)
        and _same(matched_replay.get("parameters"), parameters)
        and all(matched_replay.get(name) == value for name, value in policy.items())
        and (bool(policy) or matched_replay.get("return_policy_schema") is None), "same authenticated matched replay and policy bytes required")
    arms = {}
    with localcontext() as context:
        context.prec = 28
        for arm in cohort.ARMS:
            rows = {group: tuple(row for (lag, _), row in sorted(values.items()) if lag == arm) for group, values in projected.items()}
            groups = {group: cohort._summary(values, days, return_policy_json=return_policy_json) for group, values in rows.items()}
            supplemental = {group: cohort._summary(tuple({**row, "assigned_capital": row["original_assigned_capital"]}
                for row in values), days, return_policy_json=return_policy_json) for group, values in rows.items()}
            matched_budget = groups[GROUP]["assigned_capital"]
            original_budget = supplemental[GROUP]["assigned_capital"]
            paired = []
            for declaration in (row for row in declarations if row.arm == arm):
                formation = declaration.formation_on
                left, right = (projected[group][arm, formation] for group in (combo.GROUP, GROUP))
                pnls = {group: cohort._difference(row["replay"].navs[-1], ONE) for group, row in ((combo.GROUP, left), (GROUP, right))}
                selected = {group: tuple(row["chosen_codes"]) for group, row in ((combo.GROUP, left), (GROUP, right))}
                paired.append({"formation_on": formation, "formation_year": formation.year,
                    "original_target_ceiling": declaration.target_ceiling, "original_assigned_capital": left["original_assigned_capital"],
                    "matched_target_count": len(declaration.chosen_codes), "matched_target_budget": left["assigned_capital"],
                    "chosen_codes": selected, "selection_overlap_codes": tuple(code for code in selected[combo.GROUP] if code in selected[GROUP]),
                    "filled_entries": {group: row["filled_entries"] for group, row in ((combo.GROUP, left), (GROUP, right))},
                    "filled_capital": {group: row["filled_capital"] for group, row in ((combo.GROUP, left), (GROUP, right))},
                    "net_pnl": pnls, "combo_minus_matched_pnl": cohort._difference(pnls[combo.GROUP], pnls[GROUP])})

            def period(values):
                budget = sum_decimal_values(tuple(row["matched_target_budget"] for row in values))
                original = sum_decimal_values(tuple(row["original_assigned_capital"] for row in values))
                pnl = sum_decimal_values(tuple(row["combo_minus_matched_pnl"] for row in values))
                return {"formation_count": len(values), "positive_target_formation_count": sum(row["matched_target_count"] > 0 for row in values),
                    "matched_target_budget": budget, "original_assigned_capital": original, "combo_minus_matched_pnl": pnl,
                    "own_matched_budget_difference": None if budget == 0 else pnl / budget,
                    "total_matched_budget_contribution": None if matched_budget == 0 else pnl / matched_budget,
                    "own_original_budget_difference": None if original == 0 else pnl / original,
                    "total_original_budget_contribution": None if original_budget == 0 else pnl / original_budget}

            arms[arm] = {"groups": groups, "supplemental_original_budget_groups": supplemental,
                "matched_target_budget": matched_budget, "original_assigned_capital": original_budget,
                "combo_minus_matched": period(paired), "by_formation": tuple(paired),
                "by_formation_year": tuple({"formation_year": year, **period(tuple(row for row in paired if row["formation_year"] == year))}
                    for year in range(min(day.year for _, day in projected[GROUP]), date.fromisoformat(parameters["end"]).year + 1)),
                "retrospective_periods": {label: period(tuple(row for row in paired if row["formation_year"] in years))
                    for label, years in (("2020_2023", (2020, 2021, 2022, 2023)), ("2024_2025", (2024, 2025)))}}
    return {"schema": SCHEMA + ":report-v1", "arms": arms,
        "primary_denominator": "sum(COMBO target count / 20), identical for COMBO and TREND_MATCHED",
        "supplemental_denominator": "sum(original target ceiling / 20); original opportunity budget retained",
        "path_meaning": "ex-post capital attribution; future counts used only for retrospective normalization",
        "period_meaning": "formation vintages; retrospective description, not calendar-year portfolio performance or new tests",
        "realized_exposure_matched": False, "new_books_replayed": 0, "statistical_test_performed": False,
        **FALSE_FLAGS, **policy}
