"""Pure original-issuer attribution of the saved 2024/2025 BV contrasts.

The caller authenticates saved inputs and groups compulsory components by their
original investment before this seam. No files, books or market inputs are read.
"""
from __future__ import annotations

from datetime import date
from decimal import Context, Decimal, InvalidOperation, localcontext

from research.activity_accounting import sum_decimal_values


SCHEMA = "price-trend-issuer-comparison-v1"
ARMS = ("D1", "D2")
CONTROLS = ("C01", "C02", "C03", "C04", "C05")
YEARS = (2024, 2025)
ZERO = Decimal(0)
PNL_FIELDS = ("trend_pnl", "control_pnl", "excess_pnl")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _decimal(value) -> Decimal:
    _require(type(value) is Decimal and value.is_finite(), "finite exact Decimal required")
    sum_decimal_values((value,))
    return value


def _sum(values) -> Decimal:
    return sum_decimal_values(tuple(values))


def _difference(left: Decimal, right: Decimal) -> Decimal:
    return _sum((left, right.copy_negate()))


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator == 0 else numerator / denominator


def _saved_decimal(value) -> Decimal:
    _require(type(value) is str, "saved cash scalar must be Decimal text")
    try:
        return _decimal(Decimal(value))
    except InvalidOperation as exc:
        raise ValueError("invalid saved Decimal text") from exc


def _saved_year(value) -> int:
    _require(type(value) is int or (type(value) is str and value in tuple(str(y) for y in range(2019, 2027))),
             "invalid saved formation year")
    return int(value)


def _cash_row(row: dict) -> dict:
    _require(type(row) is dict and all(name in row for name in ("assigned_capital", *PNL_FIELDS)),
             "complete saved cash row required")
    result = {name: _saved_decimal(row[name]) for name in ("assigned_capital", *PNL_FIELDS)}
    _require(result["assigned_capital"] >= 0 and _difference(result["trend_pnl"], result["control_pnl"]) == result["excess_pnl"],
             "saved paired cash consistency mismatch")
    return result


def _reference(reference: dict) -> tuple[dict, dict, dict, Decimal]:
    _require(type(reference) is dict and reference.get("schema") == "price-trend-period-comparison-v1"
             and reference.get("status") == "saved_period_comparison_completed", "completed BV reference required")
    arms = reference.get("arms")
    _require(type(arms) is dict and set(arms) == set(ARMS), "complete saved arms required")
    formations, years, totals, budgets, trend = {}, {}, {}, None, {}
    for arm in ARMS:
        _require(type(arms[arm]) is dict and set(arms[arm]) == set(CONTROLS), "all five saved controls required")
        for control in CONTROLS:
            contrast = arms[arm][control]
            _require(type(contrast) is dict and all(name in contrast for name in ("by_formation", "by_formation_year", "all_formations")),
                     "complete saved contrast required")
            rows = contrast["by_formation"]
            _require(type(rows) in (list, tuple) and len(rows) == 14, "fourteen saved formation rows required")
            current = {}
            for row in rows:
                _require(type(row) is dict and type(row.get("formation_on")) is str, "saved ISO formation required")
                try:
                    day = date.fromisoformat(row["formation_on"])
                except ValueError as exc:
                    raise ValueError("saved ISO formation required") from exc
                _require(str(day) == row["formation_on"] and day not in current and 2019 <= day.year <= 2025,
                         "duplicate or outside saved formation")
                cash = _cash_row(row)
                _require(cash["assigned_capital"] > 0 or all(cash[name] == 0 for name in PNL_FIELDS),
                         "cash-only saved formation has nonzero PnL")
                current[day] = cash
                trend_key = arm, day
                _require(trend_key not in trend or trend[trend_key] == cash["trend_pnl"], "saved TREND PnL differs by control")
                trend[trend_key] = cash["trend_pnl"]
            current_budgets = {day: row["assigned_capital"] for day, row in current.items()}
            _require(budgets is None or budgets == current_budgets, "saved original budgets differ across contrasts")
            budgets = current_budgets
            annual_rows = contrast["by_formation_year"]
            _require(type(annual_rows) in (list, tuple) and len(annual_rows) == 8, "eight saved year rows required")
            annual = {}
            for row in annual_rows:
                _require(type(row) is dict and "formation_year" in row, "saved formation year required")
                year = _saved_year(row["formation_year"])
                _require(year in range(2019, 2027) and year not in annual, "duplicate or outside saved year")
                cash = _cash_row(row)
                members = tuple(value for day, value in current.items() if day.year == year)
                _require(all(cash[name] == _sum(value[name] for value in members) for name in cash),
                         "saved formation/year cash mismatch")
                annual[year] = cash
            total = _cash_row(contrast["all_formations"])
            _require(all(total[name] == _sum(value[name] for value in current.values()) for name in total),
                     "saved full-period cash mismatch")
            denominator = _saved_decimal(contrast["all_formations"].get("original_total_assigned_capital"))
            _require(denominator == total["assigned_capital"] == Decimal("1.80"), "unchanged original total budget required")
            formations[arm, control], years[arm, control], totals[arm, control] = current, annual, total
    _require(budgets is not None and sum(value > 0 for value in budgets.values()) == 12
             and all(value == 0 for day, value in budgets.items() if day.year == 2019), "original active-budget geometry changed")
    for year, expected in ((2024, Decimal("0.50")), (2025, Decimal("0.40"))):
        values = tuple(value for day, value in budgets.items() if day.year == year)
        _require(len(values) == 2 and all(value > 0 for value in values) and _sum(values) == expected,
                 "original 2024/2025 formation budgets changed")
    return formations, years, totals, Decimal("1.80")


def _cohorts(cohorts: tuple[dict, ...], reference: dict) -> dict:
    days = tuple(day for day in reference["D1", "C01"] if day.year in YEARS)
    expected = {(arm, group, day) for arm in ARMS for group in ("TREND", *CONTROLS) for day in days}
    _require(type(cohorts) is tuple and len(cohorts) == len(expected) == 48, "complete 48-cohort population required")
    keyed = {}
    fields = {"arm", "group_id", "formation_on", "assigned_capital", "total_book_pnl", "selected_codes", "investments"}
    for row in cohorts:
        _require(type(row) is dict and set(row) == fields, "exact seven cohort fields required")
        _require(type(row["arm"]) is str and type(row["group_id"]) is str and type(row["formation_on"]) is date,
                 "explicit cohort key required")
        key = row["arm"], row["group_id"], row["formation_on"]
        _require(key in expected and key not in keyed, "duplicate or outside-family cohort")
        budget, pnl = _decimal(row["assigned_capital"]), _decimal(row["total_book_pnl"])
        saved = reference[key[0], "C01" if key[1] == "TREND" else key[1]][key[2]]
        _require(budget > 0 and budget == saved["assigned_capital"] and pnl == saved["trend_pnl" if key[1] == "TREND" else "control_pnl"],
                 "cohort budget/PnL differs from BV reference")
        selected, investments = row["selected_codes"], row["investments"]
        _require(type(selected) is tuple and all(type(code) is str and code.strip() == code and code for code in selected)
                 and len(set(selected)) == len(selected), "unique original selected codes required")
        _require(type(investments) is tuple and len(investments) == len(selected), "one investment per selected original code required")
        projected = {}
        for investment in investments:
            _require(type(investment) is dict and set(investment) == {"original_code", "pnl", "filled_entries"},
                     "exact original investment fields required")
            code, fills = investment["original_code"], investment["filled_entries"]
            _require(type(code) is str and code in selected and code not in projected, "duplicate or unselected investment")
            amount = _decimal(investment["pnl"])
            _require(type(fills) is int and fills in (0, 1) and (fills == 1 or amount == 0), "invalid filled-entry/PnL state")
            projected[code] = dict(pnl=amount, filled_entries=fills)
        _require(_sum(value["pnl"] for value in projected.values()) == pnl, "investment/cohort PnL mismatch")
        keyed[key] = dict(selected_codes=selected, investments=projected)
    for day in days:
        _require(keyed["D1", "TREND", day]["selected_codes"] == keyed["D2", "TREND", day]["selected_codes"],
                 "paired TREND targets differ across arms")
    return keyed


def _side(cohorts: tuple[dict, ...], code: str) -> dict:
    members = tuple(row["investments"][code] for row in cohorts if code in row["investments"])
    fills = sum(row["filled_entries"] for row in members)
    return dict(pnl=_sum(row["pnl"] for row in members), targeted_cohort_count=len(members), filled_entries=fills,
                selected_unfilled_count=len(members) - fills,
                filled_zero_pnl_count=sum(row["filled_entries"] == 1 and row["pnl"] == 0 for row in members),
                selection_status="not_selected" if not members else "selected_unfilled" if not fills else "selected_filled")


def _summary(rows: tuple[dict, ...], budget: Decimal, total_budget: Decimal) -> dict:
    trend, control, excess = (_sum(row[name] for row in rows) for name in PNL_FIELDS)
    positive = _sum(row["excess_pnl"] for row in rows if row["excess_pnl"] > 0)
    negative = _sum(row["excess_pnl"] for row in rows if row["excess_pnl"] < 0)
    absolute = _sum(row["excess_pnl"].copy_abs() for row in rows)
    _require(_difference(trend, control) == excess == _sum((positive, negative)), "issuer excess decomposition mismatch")
    units = tuple({**row, "original_total_budget_contribution": _ratio(row["excess_pnl"], total_budget),
        "own_budget_normalized_difference": _ratio(row["excess_pnl"], budget),
        "positive_excess_share": _ratio(max(row["excess_pnl"], ZERO), positive),
        "negative_excess_share": _ratio(min(row["excess_pnl"], ZERO), negative),
        "absolute_excess_share": _ratio(row["excess_pnl"].copy_abs(), absolute),
        "signed_net_excess_share": _ratio(row["excess_pnl"], excess)} for row in rows)
    result = dict(assigned_capital=budget, original_total_assigned_capital=total_budget,
        original_budget_weight=_ratio(budget, total_budget), trend_pnl=trend, control_pnl=control, excess_pnl=excess,
        original_total_budget_contribution=_ratio(excess, total_budget), own_budget_normalized_difference=_ratio(excess, budget),
        positive_excess_pnl=positive, negative_excess_pnl=negative, absolute_excess_pnl=absolute,
        positive_count=sum(row["excess_pnl"] > 0 for row in rows), negative_count=sum(row["excess_pnl"] < 0 for row in rows),
        zero_count=sum(row["excess_pnl"] == 0 for row in rows), issuer_count=len(rows), units=units,
        both_selected_issuer_count=sum(row["selection_status"] == "both_selected" for row in rows),
        trend_only_issuer_count=sum(row["selection_status"] == "trend_only" for row in rows),
        control_only_issuer_count=sum(row["selection_status"] == "control_only" for row in rows),
        losses_offset_positive_excess=positive > 0 and negative < 0,
        net_excess_nonpositive_with_positive_excess=positive > 0 and excess <= 0)
    for side, direction in (("positive", 1), ("negative", -1)):
        ranked = sorted((row for row in rows if (row["excess_pnl"] > 0 if direction == 1 else row["excess_pnl"] < 0)),
                        key=lambda row: (row["excess_pnl"].copy_negate() if direction == 1 else row["excess_pnl"], row["original_code"]))
        result[f"ranked_{side}_issuers"] = tuple(row["original_code"] for row in ranked)
        subtraction = {}
        for count in (1, 3):
            chosen = ranked[:count]
            removed = _sum(row["excess_pnl"] for row in chosen)
            residual = _difference(excess, removed)
            subtraction[f"top{count}"] = dict(original_codes=tuple(row["original_code"] for row in chosen),
                excess_pnl=removed, positive_excess_share=_ratio(max(removed, ZERO), positive),
                negative_excess_share=_ratio(min(removed, ZERO), negative),
                absolute_excess_share=_ratio(removed.copy_abs(), absolute), signed_net_excess_share=_ratio(removed, excess),
                residual_excess_pnl=residual, original_total_assigned_capital=total_budget, assigned_capital=budget,
                residual_original_total_budget_contribution=_ratio(residual, total_budget),
                residual_own_budget_normalized_difference=_ratio(residual, budget))
        result[f"top_{side}_excess_subtraction"] = subtraction
    return result


def compare_issuer_periods(cohorts: tuple[dict, ...], *, reference: dict) -> dict:
    """Reconcile all 48 exact selected-original-issuer cohorts to a saved BV JSON.

    Cohorts have the exact seven fields validated below; every selected original
    code has one investment, including unfilled zero-PnL entries. BV cash values
    are numeric strings. This checks scalar consistency, never saved source truth.
    """
    with localcontext(Context(prec=28)):
        formations, annual, totals, total_budget = _reference(reference)
        keyed = _cohorts(cohorts, formations)
        arms = {}
        for arm in ARMS:
            contrasts = {}
            for control in CONTROLS:
                years, checked_formations = [], []
                for year in YEARS:
                    days = tuple(sorted(day for day in formations[arm, control] if day.year == year))
                    for day in days:
                        trend = _sum(row["pnl"] for row in keyed[arm, "TREND", day]["investments"].values())
                        placebo = _sum(row["pnl"] for row in keyed[arm, control, day]["investments"].values())
                        actual = dict(trend_pnl=trend, control_pnl=placebo, excess_pnl=_difference(trend, placebo))
                        _require(all(actual[name] == formations[arm, control][day][name] for name in PNL_FIELDS),
                                 "issuer/formation BV mismatch")
                        checked_formations.append(dict(formation_on=str(day), assigned_capital=formations[arm, control][day]["assigned_capital"], **actual))
                    trend_cohorts = tuple(keyed[arm, "TREND", day] for day in days)
                    control_cohorts = tuple(keyed[arm, control, day] for day in days)
                    codes = sorted({code for row in (*trend_cohorts, *control_cohorts) for code in row["selected_codes"]})
                    units = []
                    for code in codes:
                        trend, placebo = _side(trend_cohorts, code), _side(control_cohorts, code)
                        status = "both_selected" if trend["targeted_cohort_count"] and placebo["targeted_cohort_count"] else "trend_only" if trend["targeted_cohort_count"] else "control_only"
                        units.append(dict(original_code=code, selection_status=status,
                            trend_pnl=trend["pnl"], control_pnl=placebo["pnl"], excess_pnl=_difference(trend["pnl"], placebo["pnl"]),
                            **{f"{prefix}_{name}": value for prefix, side in (("trend", trend), ("control", placebo)) for name, value in side.items() if name != "pnl"}))
                    summary = _summary(tuple(units), annual[arm, control][year]["assigned_capital"], total_budget)
                    _require(all(summary[name] == annual[arm, control][year][name] for name in PNL_FIELDS), "issuer/year BV mismatch")
                    years.append(dict(formation_year=year, formation_count=len(days), **summary))
                contrasts[control] = dict(by_formation_year=tuple(years), by_formation=tuple(checked_formations), full_period_context=totals[arm, control])
            arms[arm] = contrasts
        return dict(schema=SCHEMA, arms=arms, formation_years=YEARS,
            time_unit="formation vintage, not calendar-year portfolio performance",
            absence_meaning="not-selected is algebraic zero, never missing-market-price or entitlement imputation",
            subtraction_meaning="arithmetic only, original budgets retained; no exclusion, redistribution or replay",
            control_interpretation="all five controls share market sessions and are not independent market samples",
            retrospective_period_selection=True, conditional_179_window_pool=True, whole_population_inference_allowed=False,
            failed_power_prerequisite_preserved=True, unmodeled_five_issuer_assets_preserved=True,
            original_denominator_preserved=True, new_evaluations=0, replayed=False, statistical_test_performed=False,
            edge_claim_allowed=False, causal_inference_allowed=False, holdout_accessed=False, promotion_allowed=False,
            actual_investment_total_return=False, source_truth_certified=False)
