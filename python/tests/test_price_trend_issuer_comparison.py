"""Fabricated issuer cash scalars only; no files, saved data or market inputs."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest

from research.price_trend_issuer_comparison import ARMS, CONTROLS, compare_issuer_periods


D = Decimal
BUDGETS = tuple((date(year, month, 15), D("0") if year == 2019 else
                 D("0.25") if year == 2024 else D("0.20") if year == 2025 else
                 D("0.20") if (year, month) == (2020, 6) else D("0.10"))
                for year in range(2019, 2026) for month in (6, 12))
DAYS = tuple(day for day, _ in BUDGETS if day.year in (2024, 2025))
FIRST, SECOND, THIRD, FOURTH = DAYS


def exact_sum(values):
    with localcontext() as context:
        context.prec = 4096
        return sum(values, D(0))


def investment(code, amount, fills=1):
    return dict(original_code=code, pnl=D(amount), filled_entries=fills)


def family(trend=None, controls=None):
    trend, controls = trend or {}, controls or {}
    result = []
    for arm in ARMS:
        for group in ("TREND", *CONTROLS):
            for day in DAYS:
                investments = trend.get(day, ()) if group == "TREND" else controls.get((group, day), ())
                result.append(dict(arm=arm, group_id=group, formation_on=day,
                    assigned_capital=dict(BUDGETS)[day], total_book_pnl=exact_sum(row["pnl"] for row in investments),
                    selected_codes=tuple(row["original_code"] for row in investments), investments=investments))
    return tuple(result)


def saved_reference(cohorts):
    """Independent synthetic BV shape, including original years outside BW."""
    pnl = {(row["arm"], row["group_id"], row["formation_on"]): row["total_book_pnl"] for row in cohorts}
    result = dict(schema="price-trend-period-comparison-v1", status="saved_period_comparison_completed", arms={})
    for arm in ARMS:
        contrasts = {}
        for control in CONTROLS:
            rows = []
            for day, budget in BUDGETS:
                trend = pnl.get((arm, "TREND", day), D(0))
                placebo = pnl.get((arm, control, day), D(0))
                rows.append(dict(formation_on=str(day), assigned_capital=budget, trend_pnl=trend,
                                 control_pnl=placebo, excess_pnl=exact_sum((trend, placebo.copy_negate()))))
            years = tuple(dict(formation_year=year, **{name: exact_sum(row[name] for row in rows if date.fromisoformat(row["formation_on"]).year == year)
                for name in ("assigned_capital", "trend_pnl", "control_pnl", "excess_pnl")}) for year in range(2019, 2027))
            total = {name: exact_sum(row[name] for row in rows) for name in ("assigned_capital", "trend_pnl", "control_pnl", "excess_pnl")}
            contrasts[control] = dict(by_formation=rows, by_formation_year=years,
                                     all_formations=dict(original_total_assigned_capital=D("1.80"), **total))
        result["arms"][arm] = contrasts

    def text(value):
        if isinstance(value, Decimal):
            return str(value)
        if type(value) is dict:
            return {key: text(item) for key, item in value.items()}
        if type(value) in (list, tuple):
            return [text(item) for item in value]
        return value

    return text(result)


def report(cohorts=None, reference=None):
    cohorts = family() if cohorts is None else cohorts
    return compare_issuer_periods(cohorts, reference=saved_reference(cohorts) if reference is None else reference)


def year(result, control="C01", arm="D1", value=2024):
    return next(row for row in result["arms"][arm][control]["by_formation_year"] if row["formation_year"] == value)


def test_shared_issuer_cancels_before_ranking_and_one_sided_codes_keep_signs():
    cohorts = family({FIRST: (investment("SHARED", "10"), investment("TREND", "2")), SECOND: (investment("SHARED", "-3"),)},
                     {("C01", FIRST): (investment("SHARED", "9"), investment("CONTROL", "4")),
                      ("C01", SECOND): (investment("SHARED", "-2"),)})
    result = report(cohorts)
    row = year(result)
    units = {unit["original_code"]: unit for unit in row["units"]}
    assert (row["trend_pnl"], row["control_pnl"], row["excess_pnl"]) == (D(9), D(11), D(-2))
    assert units["SHARED"]["trend_pnl"] == units["SHARED"]["control_pnl"] == D(7)
    assert units["SHARED"]["excess_pnl"] == 0
    assert units["SHARED"]["selection_status"] == "both_selected"
    assert units["SHARED"]["trend_targeted_cohort_count"] == units["SHARED"]["control_filled_entries"] == 2
    assert units["TREND"]["selection_status"] == "trend_only"
    assert units["CONTROL"]["selection_status"] == "control_only"
    assert (row["both_selected_issuer_count"], row["trend_only_issuer_count"], row["control_only_issuer_count"]) == (1, 1, 1)
    assert (row["positive_excess_pnl"], row["negative_excess_pnl"], row["absolute_excess_pnl"]) == (D(2), D(-4), D(6))
    assert (row["positive_count"], row["negative_count"], row["zero_count"]) == (1, 1, 1)
    assert row["ranked_positive_issuers"] == ("TREND",)
    assert row["ranked_negative_issuers"] == ("CONTROL",)
    assert row["net_excess_nonpositive_with_positive_excess"] is True
    top_negative = row["top_negative_excess_subtraction"]["top1"]
    assert top_negative["excess_pnl"] == -4
    assert top_negative["residual_excess_pnl"] == 2
    assert top_negative["assigned_capital"] == D("0.50")
    assert top_negative["original_total_assigned_capital"] == D("1.80")
    assert top_negative["residual_own_budget_normalized_difference"] == 4
    assert top_negative["negative_excess_share"] == 1
    assert top_negative["signed_net_excess_share"] == 2


def test_not_selected_unfilled_and_filled_actual_zero_are_distinct():
    cohorts = family({FIRST: (investment("UNFILLED", "0", 0), investment("ACTUAL_ZERO", "0"))},
                     {("C01", FIRST): (investment("CONTROL_ONLY", "0"),)})
    row = year(report(cohorts))
    units = {unit["original_code"]: unit for unit in row["units"]}
    unfilled, actual = units["UNFILLED"], units["ACTUAL_ZERO"]
    assert unfilled["trend_selection_status"] == "selected_unfilled"
    assert unfilled["trend_selected_unfilled_count"] == 1
    assert unfilled["trend_filled_entries"] == unfilled["trend_filled_zero_pnl_count"] == 0
    assert unfilled["control_selection_status"] == "not_selected"
    assert unfilled["control_targeted_cohort_count"] == unfilled["control_selected_unfilled_count"] == 0
    assert actual["trend_selection_status"] == "selected_filled"
    assert actual["trend_filled_entries"] == actual["trend_filled_zero_pnl_count"] == 1
    assert row["zero_count"] == row["issuer_count"] == 3
    assert all(unit["signed_net_excess_share"] is unit["absolute_excess_share"] is None for unit in row["units"])


def test_zero_net_retains_positive_negative_absolute_and_null_signed_shares():
    cohorts = family({FIRST: (investment("A", "3"), investment("B", "-3"))})
    row = year(report(cohorts))
    assert row["excess_pnl"] == 0
    assert (row["positive_excess_pnl"], row["negative_excess_pnl"], row["absolute_excess_pnl"]) == (D(3), D(-3), D(6))
    assert all(unit["signed_net_excess_share"] is None for unit in row["units"])
    assert row["top_positive_excess_subtraction"]["top3"]["residual_excess_pnl"] == -3
    assert row["top_negative_excess_subtraction"]["top3"]["residual_excess_pnl"] == 3
    assert row["top_positive_excess_subtraction"]["top1"]["signed_net_excess_share"] is None


def test_all_negative_empty_positive_rank_and_ties_have_deterministic_code_order():
    values = (investment("Z", "-2"), investment("B", "-3"), investment("A", "-3"), investment("ZERO", "0"))
    row = year(report(family({FIRST: values})))
    assert row["ranked_negative_issuers"] == ("A", "B", "Z")
    assert row["ranked_positive_issuers"] == ()
    assert row["top_negative_excess_subtraction"]["top3"]["residual_excess_pnl"] == 0
    assert row["top_positive_excess_subtraction"]["top1"]["positive_excess_share"] is None
    assert row["negative_count"] == 3 and row["zero_count"] == 1
    positive = year(report(family({FIRST: (investment("Z", "2"), investment("A", "2"), investment("B", "1"), investment("C", "1"))})))
    assert positive["ranked_positive_issuers"] == ("A", "Z", "B", "C")
    assert positive["top_positive_excess_subtraction"]["top3"]["original_codes"] == ("A", "Z", "B")
    assert positive["top_positive_excess_subtraction"]["top3"]["residual_excess_pnl"] == 1


def test_all_controls_arms_period_budgets_and_fixed_claim_limits_survive():
    cohorts = family({FIRST: (investment("LG_ORIGINAL", "0.1"),), THIRD: (investment("LG_ORIGINAL", "0.2"),)},
                     {("C05", FIRST): (investment("OTHER", "0.3"),)})
    result = report(cohorts)
    assert tuple(result["arms"]) == ARMS
    for arm in ARMS:
        assert tuple(result["arms"][arm]) == CONTROLS
        for control in CONTROLS:
            contrast = result["arms"][arm][control]
            assert len(contrast["by_formation"]) == 4
            assert tuple(row["formation_year"] for row in contrast["by_formation_year"]) == (2024, 2025)
            assert year(result, control, arm)["assigned_capital"] == D("0.50")
            assert year(result, control, arm, 2025)["assigned_capital"] == D("0.40")
            assert contrast["full_period_context"]["assigned_capital"] == D("1.80")
        assert year(result, "C05", arm)["excess_pnl"] == D("-0.2")
    assert result["conditional_179_window_pool"] is result["retrospective_period_selection"] is True
    assert result["failed_power_prerequisite_preserved"] is result["unmodeled_five_issuer_assets_preserved"] is True
    assert result["source_truth_certified"] is result["actual_investment_total_return"] is False
    assert result["edge_claim_allowed"] is result["promotion_allowed"] is result["replayed"] is result["holdout_accessed"] is False
    assert result["new_evaluations"] == 0


def test_long_exact_cash_survives_and_ratio_context_does_not_inherit_rounding():
    cash = "0.1234567890123456789012345678901234567890123456789"
    tiny = "0.0000000000000000000000000000000000000000000000001"
    cohorts = family({FIRST: (investment("A", cash), investment("B", "0.2"))}, {("C01", FIRST): (investment("A", tiny),)})
    reference = saved_reference(cohorts)
    expected = report(cohorts, reference)
    original = deepcopy((cohorts, reference))
    for rounding in (ROUND_DOWN, ROUND_UP):
        with localcontext() as context:
            context.prec, context.rounding = 5, rounding
            assert report(cohorts, reference) == expected
            assert context.prec == 5 and context.rounding == rounding
    assert (cohorts, reference) == original
    unit = next(row for row in year(expected)["units"] if row["original_code"] == "A")
    assert unit["excess_pnl"] == D("0.1234567890123456789012345678901234567890123456788")
    assert unit["trend_pnl"] == D(cash)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "outside", "bad_date", "budget", "zero_budget", "float", "nan",
    "extra", "selected_duplicate", "missing_investment", "duplicate_investment", "unselected_investment", "extra_investment_field",
    "bad_fills", "bool_fills", "unfilled_pnl", "investment_sum", "paired_targets"])
def test_rejects_invalid_cohort_population_and_investment_states(mutation):
    cohorts = list(family({FIRST: (investment("A", "1"), investment("B", "0"))}))
    reference = saved_reference(tuple(cohorts))
    first = deepcopy(cohorts[0])
    if mutation == "missing":
        cohorts.pop()
    elif mutation == "duplicate":
        cohorts[-1] = cohorts[0]
    elif mutation in ("outside", "bad_date", "budget", "zero_budget", "float", "nan", "extra", "selected_duplicate"):
        key, value = {"outside": ("group_id", "ACTIVITY"), "bad_date": ("formation_on", str(FIRST)),
            "budget": ("assigned_capital", D("0.20")), "zero_budget": ("assigned_capital", D(0)),
            "float": ("total_book_pnl", 1.0), "nan": ("total_book_pnl", D("NaN")),
            "extra": ("direct_Tp", D(4)), "selected_duplicate": ("selected_codes", ("A", "A"))}[mutation]
        first[key] = value
    else:
        investments = list(first["investments"])
        if mutation == "missing_investment":
            investments.pop()
        elif mutation == "duplicate_investment":
            investments[-1] = investments[0]
        elif mutation == "paired_targets":
            first["selected_codes"] = ("B", "A")
        else:
            key, value = {"unselected_investment": ("original_code", "X"), "extra_investment_field": ("child_code", "X"),
                "bad_fills": ("filled_entries", 2), "bool_fills": ("filled_entries", True),
                "unfilled_pnl": ("filled_entries", 0), "investment_sum": ("pnl", D(2))}[mutation]
            investments[0] = {**investments[0], key: value}
        first["investments"] = tuple(investments)
    if mutation not in ("missing", "duplicate"):
        cohorts[0] = first
    with pytest.raises(ValueError):
        report(tuple(cohorts), reference)


@pytest.mark.parametrize("mutation", ["schema", "status", "arm", "control", "formation", "year", "pnl", "annual_pnl", "total_pnl",
    "trend_disagrees", "year_duplicate", "budget", "period_budget", "decimal_type", "bad_decimal", "outside_year"])
def test_rejects_wrong_or_internally_inconsistent_bv_reference(mutation):
    cohorts = family({FIRST: (investment("A", "1"),)})
    reference = saved_reference(cohorts)
    contrast = reference["arms"]["D1"]["C01"]
    if mutation in ("schema", "status"):
        reference[mutation] = "wrong"
    elif mutation == "arm":
        reference["arms"].pop("D2")
    elif mutation == "control":
        reference["arms"]["D1"].pop("C05")
    elif mutation == "formation":
        contrast["by_formation"].pop()
    elif mutation == "year":
        contrast["by_formation_year"].pop()
    elif mutation == "pnl":
        contrast["by_formation"][10]["excess_pnl"] = "9"
    elif mutation == "annual_pnl":
        contrast["by_formation_year"][5]["trend_pnl"] = "2"
        contrast["by_formation_year"][5]["excess_pnl"] = "2"
    elif mutation == "total_pnl":
        contrast["all_formations"]["trend_pnl"] = contrast["all_formations"]["excess_pnl"] = "2"
    elif mutation == "trend_disagrees":
        reference["arms"]["D1"]["C02"]["by_formation"][10]["trend_pnl"] = "2"
        reference["arms"]["D1"]["C02"]["by_formation"][10]["excess_pnl"] = "2"
    elif mutation == "year_duplicate":
        contrast["by_formation_year"][-1] = contrast["by_formation_year"][0]
    elif mutation == "budget":
        contrast["all_formations"]["original_total_assigned_capital"] = "1.75"
    elif mutation == "period_budget":
        for arm in ARMS:
            for control in CONTROLS:
                value = reference["arms"][arm][control]
                value["by_formation"][10]["assigned_capital"] = "0.30"
                value["by_formation"][12]["assigned_capital"] = "0.15"
                value["by_formation_year"][5]["assigned_capital"] = "0.55"
                value["by_formation_year"][6]["assigned_capital"] = "0.35"
    elif mutation == "decimal_type":
        contrast["by_formation"][10]["trend_pnl"] = D(1)
    elif mutation == "bad_decimal":
        contrast["by_formation"][10]["trend_pnl"] = "not_cash"
    else:
        contrast["by_formation_year"][-1]["formation_year"] = 2027
    with pytest.raises(ValueError):
        report(cohorts, reference)


def test_numeric_string_years_and_input_order_do_not_change_attribution():
    cohorts = family({FIRST: (investment("A", "0.1"),)})
    reference = saved_reference(cohorts)
    expected = report(cohorts, reference)
    for controls in reference["arms"].values():
        for contrast in controls.values():
            for row in contrast["by_formation_year"]:
                row["formation_year"] = str(row["formation_year"])
            contrast["by_formation"].reverse()
            contrast["by_formation_year"].reverse()
    assert report(tuple(reversed(cohorts)), reference) == expected


def test_paired_targets_allow_different_fills_and_pnl_in_the_second_lag():
    cohorts = list(family({FIRST: (investment("A", "1"),)}))
    index = next(index for index, row in enumerate(cohorts) if (row["arm"], row["group_id"], row["formation_on"]) == ("D2", "TREND", FIRST))
    cohorts[index] = {**cohorts[index], "total_book_pnl": D(0), "investments": (investment("A", "0", 0),)}
    result = report(tuple(cohorts))
    assert year(result, arm="D1")["trend_pnl"] == 1
    assert year(result, arm="D2")["trend_pnl"] == 0
    assert year(result, arm="D2")["units"][0]["trend_selection_status"] == "selected_unfilled"


def test_controls_preserve_arm_specific_original_target_populations():
    cohorts = list(family(controls={("C01", FIRST): (investment("A", "1"),)}))
    index = next(index for index, row in enumerate(cohorts) if (row["arm"], row["group_id"], row["formation_on"]) == ("D2", "C01", FIRST))
    cohorts[index] = {**cohorts[index], "selected_codes": ("B",), "investments": (investment("B", "1"),)}
    result = report(tuple(cohorts))
    assert year(result, arm="D1")["ranked_negative_issuers"] == ("A",)
    assert year(result, arm="D2")["ranked_negative_issuers"] == ("B",)
    assert year(result, arm="D1")["control_pnl"] == year(result, arm="D2")["control_pnl"] == 1
