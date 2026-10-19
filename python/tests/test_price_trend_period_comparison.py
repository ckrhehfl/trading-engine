"""Fabricated scalar regressions; no saved books, prices, API or DB inputs."""
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest

from research.activity_accounting import sum_decimal_values
from research.price_trend_period_comparison import ARMS, CONTROLS, period_comparison


D = Decimal
BUDGETS = tuple((date(year, month, 15), D("0") if year == 2019 else D("0.15"))
                for year in range(2019, 2026) for month in (6, 12))
FIRST, SECOND, THIRD, FOURTH = tuple(day for day, budget in BUDGETS if budget > 0)[:4]


def family(trend=None, controls=None):
    trend, controls = trend or {}, controls or {}
    return tuple(dict(arm=arm, group_id=group, formation_on=day, assigned_capital=budget,
                      net_pnl=trend.get(day, D(0)) if group == "TREND" else controls.get((group, day), D(0)))
                 for arm in ARMS for group in ("TREND", *CONTROLS) for day, budget in BUDGETS)


def report(rows=None, budgets=BUDGETS):
    return period_comparison(family() if rows is None else rows, original_budgets=budgets)


def contrast(result, control="C01", arm="D1"):
    return result["arms"][arm][control]


def test_signed_cancellation_keeps_gross_contributions_and_original_denominator():
    result = report(family({FIRST: D("2"), SECOND: D("-1"), THIRD: D("0.5"), FOURTH: D("-1.5")}))
    row = contrast(result)["all_formations"]
    assert row["excess_pnl"] == 0
    assert row["positive_excess_pnl"] == D("2.5")
    assert row["negative_excess_pnl"] == D("-2.5")
    assert row["absolute_excess_pnl"] == 5
    assert (row["positive_count"], row["negative_count"], row["zero_count"]) == (2, 2, 10)
    assert row["losses_offset_positive_excess"] is True
    assert row["net_excess_nonpositive_with_positive_excess"] is True
    top = row["top_positive_excess_subtraction"]
    assert top["top1"]["units"] == (str(FIRST),)
    assert top["top1"]["positive_excess_share"] == D("0.8")
    assert top["top1"]["absolute_excess_share"] == D("0.4")
    assert top["top1"]["signed_net_excess_share"] is None
    assert top["top3"]["excess_pnl"] == D("2.5")
    assert top["top3"]["residual_excess_pnl"] == D("-2.5")
    assert top["top3"]["original_total_assigned_capital"] == D("1.80")
    assert top["top3"]["residual_original_total_budget_contribution"] == D("-2.5") / D("1.80")
    assert all(unit["signed_net_excess_share"] is None for unit in row["units"])


def test_signed_shares_can_exceed_one_and_be_negative_without_clipping():
    row = contrast(report(family({FIRST: D(2), SECOND: D(-1)})))["all_formations"]
    assert row["top_positive_excess_subtraction"]["top1"]["signed_net_excess_share"] == 2
    units = {unit["unit"]: unit for unit in row["units"]}
    assert units[str(SECOND)]["signed_net_excess_share"] == -1
    assert units[str(SECOND)]["positive_excess_share"] == 0
    assert units[str(SECOND)]["absolute_excess_share"] == D(1) / 3


def test_all_five_controls_both_arms_and_unfavorable_fifth_control_survive():
    controls = {("C05", day): D("0.03") for day, budget in BUDGETS if budget > 0}
    result = report(family(controls=controls))
    assert tuple(result["arms"]) == ARMS
    for arm in ARMS:
        assert tuple(result["arms"][arm]) == CONTROLS
        worst = contrast(result, "C05", arm)["all_formations"]
        assert worst["excess_pnl"] == D("-0.36")
        assert worst["own_budget_normalized_difference"] == D("-0.2")
        assert worst["negative_count"] == 12
        assert worst["top_positive_excess_subtraction"]["top1"]["units"] == ()
        assert worst["top_positive_excess_subtraction"]["top1"]["positive_excess_share"] is None


def test_complete_years_budget_context_and_retrospective_recent_group():
    result = report(family({FIRST: D("0.3"), date(2024, 6, 15): D("0.6")}))
    row = contrast(result)
    years = {value["formation_year"]: value for value in row["by_formation_year"]}
    assert tuple(years) == tuple(range(2019, 2027))
    assert years[2019]["status"] == "zero_assigned_capital"
    assert years[2019]["formation_count"] == 2
    assert years[2019]["own_budget_normalized_difference"] is None
    assert years[2026]["status"] == "no_formations"
    assert years[2026]["formation_count"] == 0
    assert years[2026]["excess_pnl"] == 0
    assert years[2020]["assigned_capital"] == D("0.30")
    assert years[2020]["own_budget_normalized_difference"] == 1
    assert years[2020]["original_total_budget_contribution"] == D("0.3") / D("1.80")
    periods = row["retrospective_periods"]
    assert periods["2024_2025"]["excess_pnl"] == D("0.6")
    assert periods["2020_2023"]["excess_pnl"] == D("0.3")
    assert sum_decimal_values(tuple(period["excess_pnl"] for period in periods.values())) == row["all_formations"]["excess_pnl"]
    assert "retrospective" in result["retrospective_periods_meaning"]
    assert result["new_evaluations"] == 0
    assert result["statistical_test_performed"] is result["replayed"] is result["promotion_allowed"] is False


def test_exact_scalar_pnl_not_reverse_reconstruction_from_rounded_ratio():
    exact_pnl = D("0.012345678901234567890123456789")
    tiny_control = D("0.000000000000000000000000000001")
    rows = family({FIRST: exact_pnl}, {("C01", FIRST): tiny_control})
    with localcontext() as context:
        context.prec = 8
        result = report(rows)
    unit = next(row for row in contrast(result)["by_formation"] if row["formation_on"] == str(FIRST))
    assert unit["trend_pnl"] == exact_pnl
    assert unit["excess_pnl"] == D("0.012345678901234567890123456788")
    with localcontext() as context:
        context.prec = 28
        rounded_ratio = exact_pnl / D("0.15")
        assert rounded_ratio * D("0.15") != exact_pnl
    altered = tuple({**row, "direct_Tp": rounded_ratio} if index == 0 else row for index, row in enumerate(rows))
    with pytest.raises(ValueError, match="ratios are not PnL inputs"):
        report(altered)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "group", "arm", "date", "budget", "float", "nan", "zero_pnl"])
def test_rejects_incomplete_wrong_family_and_changed_scalar_geometry(mutation):
    rows = list(family())
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = rows[0]
    elif mutation in ("group", "arm", "date"):
        key, value = {"group": ("group_id", "ACTIVITY"), "arm": ("arm", "D3"),
                      "date": ("formation_on", date(2026, 6, 15))}[mutation]
        rows[-1] = {**rows[-1], key: value}
    else:
        key, value = {"budget": ("assigned_capital", D("0.20")), "float": ("net_pnl", 0.0),
                      "nan": ("net_pnl", D("NaN")), "zero_pnl": ("net_pnl", D("0.1"))}[mutation]
        index = 0 if mutation == "zero_pnl" else -1
        rows[index] = {**rows[index], key: value}
    with pytest.raises(ValueError):
        report(tuple(rows))


@pytest.mark.parametrize("mutation", ["missing", "reordered", "duplicate", "total", "slot"])
def test_rejects_changed_original_budget_declaration(mutation):
    budgets = list(BUDGETS)
    if mutation == "missing":
        budgets.pop()
    elif mutation == "reordered":
        budgets.reverse()
    elif mutation == "duplicate":
        budgets[-1] = budgets[0]
    else:
        budgets[-1] = (budgets[-1][0], D("0.20") if mutation == "total" else D("0.151"))
    with pytest.raises(ValueError):
        report(budgets=tuple(budgets))


def test_tied_positive_units_use_original_formation_order():
    values = contrast(report(family({FIRST: D("0.1"), SECOND: D("0.1")})))["all_formations"]
    assert values["top_positive_excess_subtraction"]["top1"]["units"] == (str(FIRST),)
    assert values["top_positive_excess_subtraction"]["top3"]["units"] == (str(FIRST), str(SECOND))


def test_ratio_precision_and_rounding_do_not_inherit_callers_context():
    rows = family({FIRST: D("0.1"), SECOND: D("0.2")})
    expected = report(rows)
    for rounding in (ROUND_DOWN, ROUND_UP):
        with localcontext() as context:
            context.prec, context.rounding = 6, rounding
            assert report(rows) == expected
