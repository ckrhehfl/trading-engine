"""Pure saved-TREND subset adapter, same-book replay and descriptive reporting.

COMBO filters the original ordered TREND targets by the unchanged activity
predicate. It never reranks, replaces a target or redistributes its cash. Saved
input loading, trial registration and independent verification belong to the
caller. No database, network, source certification or promotion occurs here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal, localcontext
import math
from statistics import NormalDist, stdev
from types import SimpleNamespace

from research import activity_cohort_comparison as cohort
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
from research.activity_screen import ScreenObservation, _median, _observation
from research.activity_timing import AvailabilityMetadata
from research.price_trend_signal import PriceTrendObservation, PriceTrendSelection


GROUP = "COMBO"
SCHEMA = "price-trend-activity-comparison-v1"
ZERO, ONE, SLOTS = Decimal(0), Decimal(1), Decimal(20)


def _day(value) -> date:
    require(type(value) is str, "explicit ISO date required")
    result = date.fromisoformat(value)
    require(result.isoformat() == value, "canonical ISO date required")
    return result


def _stamp(value) -> datetime:
    require(type(value) is str, "explicit aware timestamp text required")
    result = datetime.fromisoformat(value)
    require(result.utcoffset() is not None and result.isoformat() == value, "canonical aware timestamp required")
    return result


def _decimal(value) -> Decimal:
    require(type(value) is str and 0 < len(value) <= 8200, "bounded exact Decimal text required")
    try:
        result = Decimal(value)
    except ArithmeticError as exc:
        raise ValueError("finite canonical Decimal required") from exc
    require(result.is_finite() and str(result) == value, "finite canonical Decimal required")
    sum_decimal_values((result,))
    return result


def _exact_fields(raw, kind) -> dict:
    require(type(raw) is dict and set(raw) == {item.name for item in fields(kind)},
        f"exact saved {kind.__name__} fields required")
    return dict(raw)


def _metadata(raw) -> AvailabilityMetadata:
    row = _exact_fields(raw, AvailabilityMetadata)
    row["observation_date"] = _day(row["observation_date"])
    for name in ("available_at", "source_public_available_at"):
        if row[name] is not None:
            row[name] = _stamp(row[name])
    if row["retrieved_at"] != "unknown":
        row["retrieved_at"] = _stamp(row["retrieved_at"])
    return AvailabilityMetadata(**row)


def restore_trend_declarations(rows: list[dict]) -> tuple[trend.TrendCohortDeclaration, ...]:
    """Decode exact saved dataclasses; build/replay revalidates original ranks."""
    require(type(rows) is list and bool(rows), "complete saved TREND declarations required")
    restored = []
    for raw in rows:
        row = _exact_fields(raw, trend.TrendCohortDeclaration)
        row["formation_on"], row["decision_on"] = _day(row["formation_on"]), _day(row["decision_on"])
        row["selection_at"] = _stamp(row["selection_at"])
        for name in ("original_activity_targets", "source_population", "observations"):
            require(type(row[name]) is list, "saved immutable TREND lists required")
        row["original_activity_targets"] = tuple(row["original_activity_targets"])
        row["source_population"] = tuple(tuple(item) for item in row["source_population"])
        selection = _exact_fields(row["selection"], PriceTrendSelection)
        selection["formation_on"], selection["selection_at"] = _day(selection["formation_on"]), _stamp(selection["selection_at"])
        for name in ("expected_candidates", "expected_baseline_on", "ranked_codes", "chosen_codes"):
            require(type(selection[name]) is list, "saved immutable trend selection lists required")
            selection[name] = tuple(selection[name])
        selection["expected_candidates"] = tuple(tuple(item) for item in selection["expected_candidates"])
        selection["expected_baseline_on"] = tuple((code, _day(day)) for code, day in selection["expected_baseline_on"])
        row["selection"] = PriceTrendSelection(**{item.name: selection[item.name]
            for item in fields(PriceTrendSelection) if item.init})
        observations = []
        for value in row["observations"]:
            quote = _exact_fields(value, PriceTrendObservation)
            quote["close"], quote["metadata"] = _decimal(quote["close"]), _metadata(quote["metadata"])
            observations.append(PriceTrendObservation(**quote))
        row["observations"] = tuple(observations)
        result = trend.TrendCohortDeclaration(**{item.name: row[item.name]
            for item in fields(trend.TrendCohortDeclaration) if item.init})
        require(encoded(_json_value(asdict(result))) == encoded(raw), "noncanonical saved TREND declaration")
        restored.append(result)
    return tuple(restored)


@dataclass(frozen=True)
class ComboActivityCheck:
    code: str
    isin: str
    normal_dates: tuple[date, ...]
    skipped_frozen_dates: tuple[date, ...]
    formation_turnover: Decimal
    activity_median: Decimal
    activity_ratio: Decimal | None
    activity_pass: bool


@dataclass(frozen=True)
class ComboCohortDeclaration:
    """A subset of one immutable original TREND selection, with its old budget."""
    trend_declaration: trend.TrendCohortDeclaration
    chosen_codes: tuple[str, ...]
    activity_checks: tuple[ComboActivityCheck, ...]
    activity_audit_json: str
    group_id: str = field(default=GROUP, init=False)

    @property
    def arm(self): return self.trend_declaration.arm
    @property
    def formation_on(self): return self.trend_declaration.formation_on
    @property
    def decision_on(self): return self.trend_declaration.decision_on
    @property
    def selection_at(self): return self.trend_declaration.selection_at
    @property
    def original_activity_targets(self): return self.trend_declaration.original_activity_targets
    @property
    def activity_targets(self): return self.original_activity_targets
    @property
    def source_population(self): return self.trend_declaration.source_population
    @property
    def source_population_count(self): return self.trend_declaration.source_population_count
    @property
    def source_population_sha256(self): return self.trend_declaration.source_population_sha256
    @property
    def source_partition_sha256(self): return self.trend_declaration.source_partition_sha256
    @property
    def target_ceiling(self): return self.trend_declaration.target_ceiling
    @property
    def eligible_shortfall(self): return self.target_ceiling - len(self.chosen_codes)


def _activity_check(audit, original, code, window, calendar) -> ComboActivityCheck:
    formation, isin, normal = window
    require(type(audit) is dict and audit.get("formation") == formation.isoformat()
        and audit.get("code") == code and audit.get("isin") == isin, "exact activity audit issue/formation required")
    screen = audit.get("activity_screen")
    require(type(screen) is dict and screen.get("formation") == formation.isoformat()
        and screen.get("code") == code and screen.get("isin") == isin and type(screen.get("inputs")) is dict
        and type(screen["inputs"].get("activity")) is dict
        and type(screen.get("arms")) is dict and set(screen["arms"]) == set(cohort.ARMS),
        "saved activity inputs and both original arms required")
    source = screen["inputs"]["activity"]
    required = calendar[calendar.index(normal[0]):calendar.index(formation) + 1]
    require(set(source) == {day.isoformat() for day in required}, "complete exact oldest-normal through formation activity union required")
    observations = {}
    for day in required:
        raw = _exact_fields(source[day.isoformat()], ScreenObservation)
        raw["turnover"] = _decimal(raw["turnover"])
        raw["public_available_at"] = None if raw["public_available_at"] is None else _stamp(raw["public_available_at"])
        raw["availability"] = _metadata(raw["availability"])
        observations[day] = ScreenObservation(**raw)
        _observation(observations, day, original.selection_at, require_metadata=True)
    observed = tuple(day for day in required[:-1] if observations[day].state == "observed")
    frozen = tuple(day for day in required[:-1] if observations[day].state == "frozen")
    require(len(normal) == 60 and observed == normal and observations[formation].state == "observed",
        "unchanged exact sixty normal dates and executable formation state required")
    baseline = _median([observations[day].turnover for day in normal])
    current = observations[formation].turnover
    with localcontext() as context:
        context.prec = 28
        ratio = None if baseline == 0 else current / baseline
    with localcontext() as context:
        context.prec = max(28, len(baseline.as_tuple().digits) + 1)
        passed = baseline > 0 and current >= baseline * Decimal(3)
    saved_arm = screen["arms"][original.arm]
    require(type(saved_arm) is dict and type(saved_arm.get("candidate")) is dict
        and saved_arm["candidate"].get("code") == code and type(saved_arm["candidate"].get("activity_pass")) is bool
        and saved_arm.get("decision_on") == original.decision_on.isoformat()
        and saved_arm.get("selection_at") == original.selection_at.isoformat()
        and saved_arm.get("normal_dates") == [day.isoformat() for day in normal]
        and saved_arm.get("skipped_frozen_dates") == [day.isoformat() for day in frozen],
        "saved activity timing, normal/frozen dates or candidate changed")
    require(_decimal(saved_arm.get("activity_median")) == baseline
        and (None if saved_arm.get("activity_ratio") is None else _decimal(saved_arm["activity_ratio"])) == ratio
        and saved_arm["candidate"]["activity_pass"] == passed,
        "saved activity median, ratio or predicate differs from exact inputs")
    return ComboActivityCheck(code, isin, normal, frozen, current, baseline, ratio, passed)


def build_combo_declarations(trend_declarations, normal_input_audit, *, universe, parameters) -> tuple[ComboCohortDeclaration, ...]:
    """Recompute only the fixed TREND origins, preserving their order and k/20."""
    universe = trend._universe(universe, parameters)
    originals = trend._declarations(trend_declarations, universe, parameters)
    require(type(normal_input_audit) is list, "saved normal-input audit list required")
    audits = {}
    for audit in normal_input_audit:
        require(type(audit) is dict and type(audit.get("code")) is str, "typed dated normal audit required")
        key = _day(audit.get("formation")), audit["code"]
        require(key not in audits, "duplicate dated normal audit")
        audits[key] = audit
    windows = {(formation, code): (formation, isin, normal) for formation, code, isin, normal in universe.windows}
    result = []
    for original in originals:
        checks, consumed = [], []
        for code in original.chosen_codes:
            key = original.formation_on, code
            require(key in audits and key in windows, "missing original TREND activity window; no false/zero fallback")
            checks.append(_activity_check(audits[key], original, code, windows[key], universe.calendar))
            consumed.append(audits[key])
        chosen = tuple(row.code for row in checks if row.activity_pass)
        result.append(ComboCohortDeclaration(original, chosen, tuple(checks), encoded(consumed).decode()))
    return tuple(result)


def _declarations(rows, universe, parameters):
    require(type(rows) is tuple and all(type(row) is ComboCohortDeclaration for row in rows),
        "immutable explicit COMBO declarations required")
    audits = {}
    for row in rows:
        require(type(row.activity_audit_json) is str, "retained immutable activity audit required")
        values = strict_json(row.activity_audit_json.encode())
        require(type(values) is list, "retained exact activity audit list required")
        for audit in values:
            key = audit["formation"], audit["code"]
            require(key not in audits or encoded(audits[key]) == encoded(audit), "conflicting repeated activity audit")
            audits[key] = audit
    fresh = build_combo_declarations(tuple(row.trend_declaration for row in rows), list(audits.values()),
        universe=universe, parameters=parameters)
    require(_same(tuple(sorted(rows, key=cohort._key)), fresh), "COMBO subset, original TREND or activity evidence changed")
    return fresh


def replay_price_trend_activity_cohorts(declarations, holding_inputs, events, *, universe, parameters,
                                      raw_quote_view=None, scope_binding=None, before_cohort=None, on_cohort=None) -> dict:
    """Replay COMBO only through the same sealed BS/TREND book arithmetic."""
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
        require(_same(quotes.base, holding_inputs), "COMBO raw view must preserve the complete supplied holding base")
    require(quotes.calendar == universe.calendar, "COMBO and original holding calendars differ")
    ordered_events = cohort._events(events, quotes, parameters, raw_quote_view)
    binding, policy_json = None, None
    if scope_binding is not None:
        require(type(scope_binding) is CohortScopeBinding
            and _same(scope_binding.declarations, universe.original_declarations), "COMBO proof must retain the original BS family")
        binding = _validate_cohort_scope(scope_binding, universe.original_declarations, quotes, ordered_events,
            parameters=parameters, _raw_context=raw_context)
        policy_json = binding.return_policy_json
        policy = decode_return_policy(policy_json)
        require(policy is not None and policy["schema"] == SCHEMA_V3, "unchanged approved five-issuer return policy required")
        bounds = {row[:4]: row[4] for row in binding.origin_bounds}
        # Prove every original TREND origin before activity can remove any of it.
        original_origins = {(row.arm, row.formation_on, code, row.decision_on)
            for row in rows for code in row.trend_declaration.chosen_codes}
        require(original_origins <= set(bounds), "original TREND origin outside original conditional proof union")
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
        require(encoded(parameters) == parameter_bytes and _unchanged_fields(sealed), "COMBO prepared inputs or parameters changed")

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
            result.update(original_trend_chosen_codes=row.trend_declaration.chosen_codes,
                retained_target_count=len(row.chosen_codes), retained_target_budget=Decimal(len(row.chosen_codes)) / SLOTS,
                screen_rejected_codes=tuple(check.code for check in row.activity_checks if not check.activity_pass),
                combo_selection_identity=digest(encoded(_json_value(asdict(row)))))
            if on_cohort is not None:
                on_cohort(result)
                unchanged()
            results.append(result)
        aggregates = {arm: {GROUP: cohort._summary(tuple(row for row in results if row["arm"] == arm), days,
            return_policy_json=policy_json)} for arm in cohort.ARMS}
    return {"schema": SCHEMA, "status": "pure_restricted_combo_price_return_diagnostic",
        "parameters": strict_json(parameter_bytes), "calendar": universe.calendar, "cohorts": tuple(results),
        "aggregates": aggregates, "cohort_evaluation_counts": {arm: sum(row["arm"] == arm for row in results) for arm in cohort.ARMS},
        "reuse_identity": universe.reuse_identity, "activity_snapshot_sha256": quotes.activity_snapshot_sha256,
        "quote_snapshot_sha256": quotes.quote_snapshot_sha256, "initial_nav_per_cohort": ONE,
        "old_trend_books_replayed": 0, "old_control_books_replayed": 0,
        "original_budget_preserved": True, "rank_recomputed_for_combo": False, "replacement_targets": 0,
        "conditional_exit_bounds_consumed": binding is not None,
        "conditional_scope_binding_sha256": None if binding is None else binding.binding_sha256,
        "whole_source_population_complete": False, "source_truth_certified": False,
        "event_coverage_certified": False, "price_basis_certified": False, "historical_vintage_certified": False,
        "comparison_power_prerequisite_met": False, "power_design_passed": False, "promotion_allowed": False,
        **policy_report(policy_json)}


def blind_combo_calibration(replay: dict, parameters: dict) -> dict:
    """Own COMBO dispersion only; cash/constant paths cannot be a power pass.

    Retain the old cost-floor arithmetic as a diagnostic. The original floor
    does not match COMBO's reduced exposure, so it never establishes power.
    """
    trend._parameters(parameters)
    arms = {}
    original_cost_floor = parameters["sizing_round_trip_bps"] / 1e4 / parameters["holding_sessions"] * 1e4
    for arm in cohort.ARMS:
        summary = replay["aggregates"][arm][GROUP]
        path = summary["metrics"]["path"]
        values = [float(row["session_return"]) for row in path if row["session_return"] is not None]
        unassigned = summary["assigned_capital"] == 0
        require((not values and unassigned and summary["filled_entries"] == 0)
            or (len(values) == len(path) and all(math.isfinite(value) for value in values)),
            "complete finite own COMBO return path or explicit unassigned cash required")
        sd = stdev(values) if len(values) >= 2 else None
        sd = sd if sd is not None and math.isfinite(sd) else None
        base = {"sessions": len(path), "filled_entries": summary["filled_entries"], "session_return_sd": sd,
            "power_design_passed": False, "comparison_power_prerequisite_met": False,
            "cost_floor_exposure_matched": False, "original_cost_floor_daily_bps": original_cost_floor,
            "cost_floor_daily_bps": original_cost_floor,
            "cost_floor_resolvable": False, "cost_floor_resolvable_diagnostic": None,
            "interpretation": "own COMBO dispersion only; old cost floor is not exposure matched; no mean return or edge claim"}
        if summary["filled_entries"] == 0 or sd in (None, 0):
            arms[arm] = {**base, "status": "undefined_no_finite_nonzero_invested_dispersion", "iid_session_se": None,
                "block_se": None, "corrected_to_naive_se": None, "detectable_daily_bps": None}
            continue
        value = calibration({"returns": values, "diagnostics": {"filled_entries": summary["filled_entries"]},
            "closed_trades": summary["ordinary_closed_trades"], "unresolved_positions": summary["terminal_open_lots"]}, parameters)
        se = max(value["iid_session_se"], *value["block_se"].values())
        normal = NormalDist()
        mde = (normal.inv_cdf(1 - parameters["alpha"]) + normal.inv_cdf(parameters["power"])) * se * 10000
        arms[arm] = {**value, **base, "status": "own_dispersion_calibrated", "detectable_daily_bps": mde,
            "corrected_to_naive_se": se / value["iid_session_se"],
            "cost_floor_resolvable_diagnostic": mde <= value["cost_floor_daily_bps"],
            "standard_error_policy": "maximum of own IID and all registered own circular-block SEs"}
    return arms


def _amount(value):
    return value if type(value) is Decimal and value.is_finite() else _decimal(value)


def comparison_report(combo_replay: dict, trend_cohorts: tuple[dict, ...], *, parameters: dict,
                      return_policy_json: bytes | None = None) -> dict:
    """Describe paired original budgets and retained-target sensitivity, no replay."""
    trend._parameters(parameters)
    combos = combo_replay["cohorts"]
    require(type(combos) is tuple and type(trend_cohorts) is tuple and len(combos) == len(trend_cohorts),
        "complete original TREND and new COMBO cohort tuples required")
    require(bool(combos) and len({(row["arm"], row["formation_on"]) for row in combos}) == len(combos)
        and {row["arm"] for row in combos} == set(cohort.ARMS)
        and all(row["group_id"] == GROUP for row in combos), "complete unique paired COMBO cohort keys required")
    keyed = {}
    for row in trend_cohorts:
        day = row["formation_on"] if type(row["formation_on"]) is date else _day(row["formation_on"])
        key = row["arm"], day
        require(row["group_id"] == trend.GROUP and key not in keyed, "unique original TREND cohort required")
        keyed[key] = row
    require(set(keyed) == {(row["arm"], row["formation_on"]) for row in combos}, "paired original formation population required")
    days = tuple(point["date"] for point in combos[0]["raw_metrics"]["path"])
    policy = policy_report(return_policy_json)
    require((not policy and combo_replay.get("return_policy_schema") is None)
        or (policy.get("return_policy_schema") == SCHEMA_V3
            and all(combo_replay.get(name) == value for name, value in policy.items())),
        "same approved COMBO policy bytes required for the comparison report")

    def project(row, expected_codes, budget):
        require(tuple(row["chosen_codes"]) == expected_codes and _amount(row["assigned_capital"]) == budget,
            "original targets or assigned budget changed in saved cohort")
        require(all(row.get(name) == value for name, value in policy.items()), "same approved return-policy labels required")
        path = row["raw_metrics"]["path"]
        require(tuple(point["date"] if type(point["date"]) is date else _day(point["date"]) for point in path) == days,
            "complete paired cohort sessions required")
        navs = tuple(_amount(point["nav"]) for point in path)
        inventory = row["exposure"]["path"]
        require(len(inventory) == len(days), "complete paired inventory path required")
        result = {**row, "assigned_capital": budget, "replay": SimpleNamespace(navs=navs),
            **{name: _amount(row[name]) for name in ("filled_capital", "filled_principal", "entry_cost_amount")},
            "exposure": {"path": [{"marked_inventory_value": _amount(point["marked_inventory_value"])} for point in inventory]}}
        if policy:
            attribution = row.get(cohort.ATTRIBUTION_KEY)
            require(type(attribution) is dict and type(attribution.get("by_code")) is dict,
                "same-book named issuer attribution required")
            result[cohort.ATTRIBUTION_KEY] = {**attribution, "by_code": {code: {**value,
                **{name: _amount(value[name]) for name in ("entry_budget", "net_exit_cash", "terminal_marked_value", "pnl")}}
                for code, value in attribution["by_code"].items()}}
        return result

    arms = {}
    with localcontext() as context:
        context.prec = 28
        for arm in cohort.ARMS:
            paired, projected = [], {trend.GROUP: [], GROUP: []}
            for combo in sorted((row for row in combos if row["arm"] == arm), key=lambda row: row["formation_on"]):
                old = keyed[arm, combo["formation_on"]]
                budget, retained = _amount(combo["assigned_capital"]), _amount(combo["retained_target_budget"])
                require(budget == Decimal(combo["target_ceiling"]) / SLOTS
                    and retained == Decimal(len(combo["chosen_codes"])) / SLOTS and retained <= budget
                    and tuple(code for code in combo["original_trend_chosen_codes"] if code in combo["chosen_codes"]) == combo["chosen_codes"],
                    "unaltered original ceiling and ordered COMBO subset required")
                projected[trend.GROUP].append(project(old, combo["original_trend_chosen_codes"], budget))
                projected[GROUP].append(project(combo, combo["chosen_codes"], budget))
                left, right = projected[trend.GROUP][-1], projected[GROUP][-1]
                trend_pnl, combo_pnl = (cohort._difference(row["replay"].navs[-1], ONE) for row in (left, right))
                paired.append({"formation_on": combo["formation_on"], "formation_year": combo["formation_on"].year,
                    "assigned_capital": budget, "retained_target_budget": retained,
                    "trend_target_count": len(combo["original_trend_chosen_codes"]), "combo_target_count": len(combo["chosen_codes"]),
                    "trend_pnl": trend_pnl, "combo_pnl": combo_pnl, "combo_minus_trend_pnl": cohort._difference(combo_pnl, trend_pnl),
                    "trend_original_budget_return": None if budget == 0 else trend_pnl / budget,
                    "combo_original_budget_return": None if budget == 0 else combo_pnl / budget,
                    "combo_retained_budget_return": None if retained == 0 else combo_pnl / retained})
            summaries = {group: cohort._summary(tuple(rows), days, return_policy_json=return_policy_json)
                for group, rows in projected.items()}
            assigned = sum_decimal_values(tuple(row["assigned_capital"] for row in paired))
            retained = sum_decimal_values(tuple(row["retained_target_budget"] for row in paired))

            def period(rows):
                budget = sum_decimal_values(tuple(row["assigned_capital"] for row in rows))
                pnl = sum_decimal_values(tuple(row["combo_minus_trend_pnl"] for row in rows))
                return {"formation_count": len(rows), "assigned_capital": budget, "combo_minus_trend_pnl": pnl,
                    "own_original_budget_difference": None if budget == 0 else pnl / budget,
                    "original_total_budget_contribution": None if assigned == 0 else pnl / assigned}

            secondary_navs = None if retained == 0 else tuple(ONE + sum_decimal_values(tuple(
                cohort._difference(row["replay"].navs[index], ONE) for row in projected[GROUP])) / retained for index in range(len(days)))
            arms[arm] = {"groups": summaries, "original_assigned_capital": assigned, "retained_target_budget": retained,
                "combo_minus_trend": period(paired), "by_formation": tuple(paired),
                "by_formation_year": tuple({"formation_year": year, **period(tuple(row for row in paired if row["formation_year"] == year))}
                    for year in range(min(day.year for _, day in keyed), date.fromisoformat(parameters["end"]).year + 1)),
                "retrospective_periods": {label: period(tuple(row for row in paired if row["formation_year"] in years))
                    for label, years in (("2020_2023", (2020, 2021, 2022, 2023)), ("2024_2025", (2024, 2025)))},
                "secondary_combo_retained_budget_metrics": cohort._path_metrics(days, secondary_navs)}
    return {"schema": SCHEMA + ":report-v1", "arms": arms,
        "primary_denominator": "sum(original target ceiling / 20), identical for TREND and COMBO",
        "secondary_denominator": "sum(COMBO retained target count / 20); excludes screened cash; not exposure matching",
        "path_meaning": "ex-post capital attribution; future counts used only for retrospective normalization",
        "period_meaning": "formation vintages; retrospective description, not calendar-year portfolio performance or new tests",
        "realized_exposure_matched": False, "new_books_replayed": 0, "statistical_test_performed": False,
        "comparison_power_prerequisite_met": False, "power_design_passed": False, "promotion_allowed": False,
        **policy}
