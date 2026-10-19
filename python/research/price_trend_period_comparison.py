"""Pure paired decomposition of already verified, exact cohort cash PnL.

Callers pin and validate saved artifacts before projecting scalar rows. Never
derive PnL from a rounded return ratio. Years identify formation vintages; this
module loads no inputs, selects no portfolio and executes no strategy.
"""
from __future__ import annotations

from datetime import date
from decimal import Context, Decimal, localcontext

from research.activity_accounting import sum_decimal_values


SCHEMA = "price-trend-period-comparison-v1"
ARMS = ("D1", "D2")
CONTROLS = ("C01", "C02", "C03", "C04", "C05")
YEARS = tuple(range(2019, 2027))
ZERO = Decimal(0)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _decimal(value) -> Decimal:
    _require(type(value) is Decimal and value.is_finite(), "finite exact Decimal scalar required")
    sum_decimal_values((value,))  # Keep the existing bounded exact-arithmetic contract.
    return value


def _sum(values) -> Decimal:
    return sum_decimal_values(tuple(values))


def _difference(left: Decimal, right: Decimal) -> Decimal:
    return _sum((left, right.copy_negate()))


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator == 0 else numerator / denominator


def _summary(rows: tuple[dict, ...], total_budget: Decimal) -> dict:
    excess = _sum(row["excess_pnl"] for row in rows)
    positive = _sum(row["excess_pnl"] for row in rows if row["excess_pnl"] > 0)
    negative = _sum(row["excess_pnl"] for row in rows if row["excess_pnl"] < 0)
    absolute = _sum(row["excess_pnl"].copy_abs() for row in rows)
    budget = _sum(row["assigned_capital"] for row in rows)
    units = tuple({**row, "original_total_budget_contribution": _ratio(row["excess_pnl"], total_budget),
        "original_budget_weight": _ratio(row["assigned_capital"], total_budget),
        "own_budget_normalized_difference": _ratio(row["excess_pnl"], row["assigned_capital"]),
        "positive_excess_share": _ratio(max(row["excess_pnl"], ZERO), positive),
        "absolute_excess_share": _ratio(row["excess_pnl"].copy_abs(), absolute),
        "signed_net_excess_share": _ratio(row["excess_pnl"], excess)} for row in rows)
    leaders = sorted((row for row in rows if row["excess_pnl"] > 0),
                     key=lambda row: (row["excess_pnl"].copy_negate(), row["unit"]))
    subtraction = {}
    for count in (1, 3):
        chosen = leaders[:count]
        removed = _sum(row["excess_pnl"] for row in chosen)
        residual = _difference(excess, removed)
        subtraction[f"top{count}"] = dict(units=tuple(row["unit"] for row in chosen), excess_pnl=removed,
            positive_excess_share=_ratio(removed, positive), absolute_excess_share=_ratio(removed, absolute),
            signed_net_excess_share=_ratio(removed, excess), residual_excess_pnl=residual,
            original_total_assigned_capital=total_budget,
            residual_original_total_budget_contribution=_ratio(residual, total_budget))
    trend, control = (_sum(row[name] for row in rows) for name in ("trend_pnl", "control_pnl"))
    _require(_difference(trend, control) == excess == _sum((positive, negative)), "paired PnL decomposition mismatch")
    return dict(unit_count=len(rows), formation_count=sum(row["formation_count"] for row in rows),
        assigned_capital=budget, original_total_assigned_capital=total_budget,
        original_budget_weight=_ratio(budget, total_budget), trend_pnl=trend, control_pnl=control,
        excess_pnl=excess, original_total_budget_contribution=_ratio(excess, total_budget),
        own_budget_normalized_difference=_ratio(excess, budget), positive_excess_pnl=positive,
        negative_excess_pnl=negative, absolute_excess_pnl=absolute,
        positive_count=sum(row["excess_pnl"] > 0 for row in rows),
        negative_count=sum(row["excess_pnl"] < 0 for row in rows), zero_count=sum(row["excess_pnl"] == 0 for row in rows),
        losses_offset_positive_excess=positive > 0 and negative < 0,
        net_excess_nonpositive_with_positive_excess=positive > 0 and excess <= 0,
        top_positive_excess_subtraction=subtraction, units=units)


def period_comparison(rows: tuple[dict, ...], *, original_budgets: tuple[tuple[date, Decimal], ...]) -> dict:
    """Compare TREND with all five controls on both original formation grids.

    Each row has exactly arm/group_id/formation_on/assigned_capital/net_pnl;
    dates are date objects and amounts are exact Decimal scalars from saved
    same-book PnL, never rounded-return reconstruction. The separately pinned
    original budget declaration must contain all fourteen formations. This
    checks scalar consistency, not artifact provenance or source truth.
    """
    _require(type(original_budgets) is tuple and len(original_budgets) == 14,
             "complete fourteen original formation budgets required")
    budgets = {}
    with localcontext(Context(prec=28)):
        allowed = tuple(Decimal(count) / 20 for count in range(21))
        for item in original_budgets:
            _require(type(item) is tuple and len(item) == 2 and type(item[0]) is date,
                     "explicit original date/budget tuple required")
            day, value = item
            value = _decimal(value)
            _require(day not in budgets and day.year in YEARS and value in allowed, "invalid original formation budget")
            budgets[day] = value
        _require(tuple(budgets) == tuple(sorted(budgets)), "ordered unique original formations required")
        total_budget = _sum(budgets.values())
        _require(total_budget == Decimal("1.80") and sum(value > 0 for value in budgets.values()) == 12,
                 "unchanged original total and twelve active budgets required")
        _require(all(value == 0 for day, value in budgets.items() if day.year == 2019)
                 and not any(day.year == 2026 for day in budgets), "original boundary-year geometry changed")
        expected = {(arm, group, day) for arm in ARMS for group in ("TREND", *CONTROLS) for day in budgets}
        _require(type(rows) is tuple and len(rows) == len(expected), "complete fixed paired cohort family required")
        keyed = {}
        for row in rows:
            _require(type(row) is dict and set(row) == {"arm", "group_id", "formation_on", "assigned_capital", "net_pnl"},
                     "exact scalar cohort fields required; ratios are not PnL inputs")
            _require(type(row["arm"]) is str and type(row["group_id"]) is str and type(row["formation_on"]) is date,
                     "explicit original cohort key required")
            key = row["arm"], row["group_id"], row["formation_on"]
            _require(key in expected and key not in keyed, "duplicate or outside-family cohort")
            budget, pnl = _decimal(row["assigned_capital"]), _decimal(row["net_pnl"])
            _require(budget == budgets[key[2]] and (budget > 0 or pnl == 0), "original assigned budget or cash-only PnL changed")
            keyed[key] = pnl
        _require(set(keyed) == expected, "paired formation population mismatch")
        arms = {}
        for arm in ARMS:
            contrasts = {}
            for control in CONTROLS:
                formations = tuple(dict(unit=str(day), formation_on=str(day), formation_count=1,
                    assigned_capital=budget, trend_pnl=keyed[arm, "TREND", day], control_pnl=keyed[arm, control, day],
                    excess_pnl=_difference(keyed[arm, "TREND", day], keyed[arm, control, day])) for day, budget in budgets.items())
                all_formations = _summary(formations, total_budget)
                years = []
                for year in YEARS:
                    members = tuple(row for row in formations if date.fromisoformat(row["formation_on"]).year == year)
                    summary = _summary(members, total_budget)
                    years.append(dict(formation_year=year,
                        status="no_formations" if not members else "zero_assigned_capital" if summary["assigned_capital"] == 0 else "observed_vintages",
                        **summary))
                annual = tuple(dict(unit=str(row["formation_year"]), **{name: row[name] for name in
                    ("formation_count", "assigned_capital", "trend_pnl", "control_pnl", "excess_pnl")}) for row in years)
                retrospective = {name: _summary(tuple(row for row in formations if date.fromisoformat(row["formation_on"]).year in selected), total_budget)
                    for name, selected in (("2024_2025", (2024, 2025)), ("2020_2023", (2020, 2021, 2022, 2023)))}
                contrasts[control] = dict(all_formations=all_formations, by_formation=all_formations["units"],
                    by_formation_year=tuple(years), formation_year_concentration=_summary(annual, total_budget),
                    retrospective_periods=retrospective)
            arms[arm] = contrasts
        return dict(schema=SCHEMA, arms=arms, years=YEARS,
            time_unit="formation vintage, not calendar-year portfolio performance",
            retrospective_periods_meaning="2024/2025 chosen after the prior TREND-only view; retrospective description, not a new test",
            control_interpretation="all five fixed controls share market sessions; not independent market samples",
            subtraction_meaning="arithmetic subtraction with the same original total assigned budget; no exclusion, redistribution or replay",
            original_denominator_preserved=True, new_evaluations=0, replayed=False, statistical_test_performed=False,
            edge_claim_allowed=False, causal_inference_allowed=False, holdout_accessed=False,
            promotion_allowed=False, actual_investment_total_return=False, source_truth_certified=False)
