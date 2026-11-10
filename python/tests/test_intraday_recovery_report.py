"""Invented exact cash, missingness, original weeks and date-cluster reports."""

from copy import deepcopy
from fractions import Fraction
from math import sqrt

import pytest

from research import intraday_recovery_report as report


CALENDAR = ["2026-01-05", "2026-01-06", "2026-01-13", "2026-01-20"]


def _row(key, day="2026-01-05", symbol="A", *, gross="10", status="closed", basis="1000", fold=1):
    if status in ("unresolved", "unsupported"):
        gross = cost = net = basis = None
    elif status in ("no_fill", "no_entry"):
        gross = cost = net = basis = "0"
    else:
        cost = str(Fraction(basis) * Fraction(33, 10000))
        net = str(Fraction(gross) - Fraction(cost))
    return {"key": key, "session": day, "symbol": symbol, "fold": fold, "status": status,
            "gross": gross, "cost": cost, "net": net, "entry_notional": basis,
            "selected": status not in ("no_entry", "unsupported"),
            "path_truth": None, "probabilities": None, "predicted_gross_pct": None}


def _records():
    return [_row("a"), _row("b", symbol="B", gross="-10"),
            _row("c", "2026-01-06", gross="20"),
            _row("d", "2026-01-06", symbol="B", status="no_entry"),
            _row("e", "2026-01-13", status="no_fill", fold=2),
            _row("f", "2026-01-13", symbol="B", status="unresolved", fold=2),
            _row("g", "2026-01-13", symbol="C", status="unsupported", fold=2)]


def _summarize(rows=None, calendar=CALENDAR):
    rows = _records() if rows is None else rows
    before = deepcopy(rows)
    result = report.summarize(rows, calendar)
    report.verify_summary(rows, calendar, result)
    assert rows == before
    return result


def test_exact_denominators_means_cash_and_calendar_zero_weeks():
    result = _summarize()
    totals = result["totals"]
    assert {key: totals[key] for key in (
        "all_opportunities", "selected", "known_economics", "selected_known", "filled_closed",
        "no_fill", "no_entry", "unresolved", "unsupported", "cost_clearing")} == {
            "all_opportunities": 7, "selected": 5, "known_economics": 5, "selected_known": 4,
            "filled_closed": 3, "no_fill": 1, "no_entry": 1, "unresolved": 1, "unsupported": 1,
            "cost_clearing": 2}
    assert (totals["mean_gross_pct"], totals["mean_net_pct"]) == ("2/3", "101/300")
    assert (totals["nominal_gross_cash"], totals["nominal_cost_cash"], totals["nominal_net_cash"]) == (
        "20", "99/10", "101/10")
    assert result["weeks"] == 3 and result["evaluation_dates"] == 4
    assert result["zero_trade_weeks"] == result["zero_cost_clearing_weeks"] == 2
    assert result["weekly"]["2026-W04"]["all_opportunities"] == 0
    assert result["by_date"]["2026-01-20"]["filled_closed"] == 0
    assert result["by_fold"]["2"]["unresolved"] == 1


def test_largest_positive_date_and_name_removal_preserves_signed_cash():
    result = _summarize()
    day, symbol = result["concentration"]["date"], result["concentration"]["symbol"]
    assert (day["largest_positive_group"], day["largest_positive_cash"],
            day["net_cash_without_largest_positive"]) == ("2026-01-06", "167/10", "-33/5")
    assert (symbol["largest_positive_group"], symbol["largest_positive_cash"],
            symbol["net_cash_without_largest_positive"]) == ("A", "117/5", "-133/10")
    assert day["largest_trade_fraction"] == symbol["largest_trade_fraction"] == "2/3"


def test_cost_clearing_is_strict_and_flat_outcomes_have_no_pertrade_return():
    rows = [_row("equal", gross="33/10"), _row("flat", symbol="B", status="no_fill")]
    result = _summarize(rows)
    assert result["totals"]["cost_clearing"] == 0
    assert result["totals"]["mean_net_pct"] == "0"
    assert result["totals"]["mean_gross_pct"] == "33/100"


def test_different_entry_basis_uses_pertrade_return_mean_not_ratio_of_pooled_cash():
    result = _summarize([_row("a", gross="10", basis="1000"),
                         _row("b", symbol="B", gross="100", basis="100000")])
    assert result["totals"]["mean_gross_pct"] == "11/20"
    assert result["totals"]["mean_net_pct"] == "11/50"


def test_brier_weights_dates_equally_and_does_not_drop_known_paths_without_fill():
    rows = _records()
    rows[0].update(path_truth=0, probabilities=[1.0, 0.0, 0.0], predicted_gross_pct=0.0)
    rows[1].update(path_truth=1, probabilities=[0.0, 1.0, 0.0], predicted_gross_pct=0.0)
    rows[3].update(path_truth=2, probabilities=[1.0, 0.0, 0.0], predicted_gross_pct=999.0)
    rows[4].update(probabilities=[0.0, 0.0, 1.0])
    result = _summarize(rows)["forecast"]
    assert (result["path_known"], result["probability_supported"], result["scored_paths"],
            result["scored_path_dates"]) == (3, 4, 3, 2)
    assert result["dateweighted_brier"] == 1.0  # Date means are 0 and 2, not row mean 2/3.
    assert all(sum(bucket["rows"] for bucket in path) == result["scored_paths"] for path in result["reliability"])
    assert all(path[-1]["upper"] == 1 for path in result["reliability"])
    assert result["payoff_prediction_supported"] == 3 and result["scored_payoffs"] == 2
    assert result["payoff_mae_pct"] == result["payoff_rmse_pct"] == 1.0


def test_day_cluster_uncertainty_exposes_correlated_rows_instead_of_treating_them_independent():
    rows = [_row(str(i), "2026-01-05" if i < 10 else "2026-01-06", str(i),
                 gross="10" if i < 10 else "-10") for i in range(20)]
    result = _summarize(rows)["uncertainty"]
    assert result["mean_net_pct"] == "-33/100"
    assert result["clustered_day_se_pct"] == 1.0
    assert result["naive_se_pct"] == pytest.approx(sqrt(1 / 19))
    assert result["clustered_day_approx95_pct"] == pytest.approx([-2.29, 1.63])
    assert "Approximate" in result["method"]


def test_one_traded_date_does_not_get_a_cluster_interval_from_multiple_stocks():
    result = _summarize([_row("a"), _row("b", symbol="B", gross="-10")])["uncertainty"]
    assert result["naive_se_pct"] == 1.0
    assert result["clustered_day_se_pct"] is result["clustered_day_approx95_pct"] is None


def test_empty_and_unknown_only_panels_keep_undefined_means_and_original_weeks():
    for rows in ([], [_row("a", status="unresolved")]):
        result = _summarize(rows)
        assert result["totals"]["mean_net_pct"] is None
        assert result["totals"]["nominal_net_cash"] == "0"
        assert result["zero_trade_weeks"] == 3
        assert result["forecast"]["dateweighted_brier"] is None
        assert result["uncertainty"]["mean_net_pct"] is None
        assert result["concentration"]["date"]["largest_positive_group"] is None
        assert result["concentration"]["date"]["net_cash_without_largest_positive"] is None


@pytest.mark.parametrize("mutation", ["duplicate", "future_day", "unknown_zero", "wrong_net", "wrong_cost",
                                     "closed_unselected", "float_amount", "nan", "probability", "truth_bool"])
def test_rejects_ambiguous_identity_accounting_and_model_numbers(mutation):
    rows = [_row("a")]
    if mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutation == "future_day":
        rows[0]["session"] = "2026-02-01"
    elif mutation == "unknown_zero":
        rows[0]["status"] = "unresolved"
    elif mutation == "wrong_net":
        rows[0]["net"] = "10"
    elif mutation == "wrong_cost":
        rows[0]["cost"], rows[0]["net"] = "0", "10"
    elif mutation == "closed_unselected":
        rows[0]["selected"] = False
    elif mutation == "float_amount":
        rows[0]["gross"] = 10.0
    elif mutation == "nan":
        rows[0]["predicted_gross_pct"] = float("nan")
    elif mutation == "probability":
        rows[0]["probabilities"] = [0.5, 0.5, 0.5]
    else:
        rows[0]["path_truth"] = True
    with pytest.raises(ValueError):
        report.summarize(rows, CALENDAR)


@pytest.mark.parametrize("calendar", [[CALENDAR[0], CALENDAR[0]], CALENDAR[::-1]])
def test_calendar_cannot_duplicate_or_reorder_evaluation_dates(calendar):
    with pytest.raises(ValueError):
        report.summarize([], calendar)


def test_paired_selection_and_unknown_changes_keep_original_roster_and_common_closed_separate():
    baseline = [_row("a"), _row("b", symbol="B"), _row("c", symbol="C"),
                _row("d", symbol="D", status="unresolved"), _row("e", symbol="E", status="unsupported")]
    candidate = [_row("a", gross="20"), _row("b", symbol="B", status="no_entry"),
                 _row("c", symbol="C", status="unresolved"), _row("d", symbol="D"),
                 _row("e", symbol="E", status="unsupported")]
    result = report.pair(candidate, baseline)
    assert (result["all_opportunities"], result["selection_changed"], result["compare_unknown_only"],
            result["baseline_unknown_only"], result["both_unknown"]) == (5, 1, 1, 1, 1)
    assert result["common_closed"] == {"coordinates": 1, "baseline_net_cash": "67/10",
                                        "compare_net_cash": "167/10", "delta_net_cash": "10"}
    assert result["common_known"] == {"coordinates": 2, "baseline_net_cash": "67/5",
                                       "compare_net_cash": "167/10", "delta_net_cash": "33/10"}


@pytest.mark.parametrize("change", ["roster", "identity"])
def test_pair_rejects_different_original_keys_or_identity(change):
    baseline = [_row("a")]
    candidate = [_row("b" if change == "roster" else "a", symbol="B")]
    with pytest.raises(ValueError, match="paired"):
        report.pair(candidate, baseline)


@pytest.mark.parametrize("change", ["mean", "cash", "count", "week", "date", "fold"])
def test_independent_aggregate_audit_rejects_tampering(change):
    rows = _records()
    result = _summarize(rows)
    if change == "mean":
        result["totals"]["mean_net_pct"] = "0"
    elif change == "cash":
        result["totals"]["nominal_net_cash"] = "0"
    elif change == "count":
        result["totals"]["unresolved"] = 0
    elif change == "week":
        del result["weekly"]["2026-W04"]
    elif change == "date":
        del result["by_date"]["2026-01-20"]
    else:
        del result["by_fold"]["2"]
    with pytest.raises(ValueError):
        report.verify_summary(rows, CALENDAR, result)


def test_aggregate_audit_is_independent_of_producer_panel(monkeypatch):
    rows, result = _records(), _summarize()
    monkeypatch.setattr(report, "_panel", lambda *_: pytest.fail("producer panel called"))
    report.verify_summary(rows, CALENDAR, result)
