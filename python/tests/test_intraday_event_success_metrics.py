"""Invented success, missing outcome and same-day ranking witnesses."""

from fractions import Fraction

import pytest

from research import intraday_event_success_metrics as es
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction


HEAD = "net_immediate"


@pytest.mark.parametrize("fill,cash,result", [
    (1, "1/1000000000000000000000", ("success", 1)),
    (1, "-1/3", ("loss", 0)),
    (1, "0", ("break_even", 0)),
    (0, "0", ("not_filled", 0)),
    (None, "0", ("unknown_fill", None)),
    (1, None, ("unknown_cash", None)),
    (0, None, ("unknown_cash", None)),
])
def test_binary_target_keeps_exact_zero_missed_fill_and_unknown_distinct(fill, cash, result):
    assert es._outcome({"fill": fill, "net_cash": cash, "target": cash}) == result


def test_invalid_no_fill_cash_and_paired_value_support_are_rejected():
    with pytest.raises(ValueError, match="no-fill"):
        es._outcome({"fill": 0, "net_cash": "1", "target": "1"})
    with pytest.raises(ValueError, match="support"):
        es._outcome({"fill": 1, "net_cash": "1", "target": None})


def row(cluster, label, direct, success):
    return {"cluster_id": cluster, "binary": {HEAD: label}, "predictions": {
        "rate": {HEAD: 0.1}, "direct": {HEAD: direct}, "success": {HEAD: success}}}


def test_same_cluster_pairs_excluded_and_other_clusters_balanced():
    day = es._daily(CALENDAR[97], [row("a", 1, 0, 8), row("a", 0, 0, 99),
        row("b", 0, 0.5, 2), row("c", 0, 0, 10), row("c", 0, -1, 4)], HEAD)
    # Pair mass: a-b 1/2; the two a-c pairs 1/4 each. The positive wins
    # against b and one c under success, ties one c under direct.
    assert day == {"session": CALENDAR[97], "rows": 5, "positive": 1, "negative": 4,
        "clusters": 3, "pairs": 3, "auc": {"rate": "1/2", "direct": "3/8", "success": "3/4"}}


def test_date_is_the_average_unit_and_zero_pair_date_is_unknown():
    first = es._daily(CALENDAR[97], [row("a", 1, 0, 7), row("b", 0, 1, 1)], HEAD)
    absent = es._daily(CALENDAR[98], [row("a", 1, 0, 0), row("a", 0, 1, 1)], HEAD)
    third = es._daily(CALENDAR[99], [row("a", 1, 1, 0), row("b", 0, 0, 1), row("c", 0, 0, 1)], HEAD)
    assert absent["pairs"] == 0 and all(value is None for value in absent["auc"].values())
    assert first["pairs"] != third["pairs"]
    assert es._auc([first, absent, third]) == dict.fromkeys(es.ARMS, "1/2")


def test_orig_causal_representative_survives_missing_new_forecast_and_report_keeps_own_support():
    first = candidate()
    later = candidate(decision=35)
    forecasts = {arm: (prediction(first, None, 1) if arm == "success" else prediction(first, 0.0, 0.0),
                       prediction(later, 0.5, 0.5)) for arm in es.ARMS}
    reps = es.select_representatives(CALENDAR, 0, (first.episode,), (first, later), forecasts)
    assert reps[0]["key"] == first.key and reps[0]["phase"] == "onset"
    result = es.summarize(CALENDAR, 0, reps, (label_record(first), label_record(later)))
    cell = result["cells"][0]
    assert cell["selected"] == 1 and cell["paired_known"] == 0
    assert cell["actions"][HEAD]["own_known"] == {"rate": 1, "direct": 1, "success": 0}
    assert len(cell["actions"][HEAD]["days"]) == 40 and cell["actions"][HEAD]["eligible_days"] == 0


def test_pool_averages_eligible_phases_inside_date_before_equal_date_mean():
    reports = [es.summarize(CALENDAR, fold, (), ()) for fold in range(4)]
    actions = [cell["actions"][HEAD] for cell in reports[0]["cells"]]
    for action, index, success in ((actions[0], 0, "0"), (actions[1], 0, "1/2"), (actions[0], 1, "1")):
        action["days"][index].update(pairs=1, auc={"rate": "1/2", "direct": "1/2", "success": success})
    result = es.pool(CALENDAR, reports)
    assert result["calendar_days"] == 160 and len(result["cells"]) == 16
    assert result["actions"][HEAD]["eligible_days"] == 2
    assert Fraction(result["actions"][HEAD]["auc"]["success"]) == Fraction(5, 8)
    reports[2]["cells"][0]["actions"][HEAD]["days"].pop()
    with pytest.raises(ValueError, match="calendar"):
        es.pool(CALENDAR, reports)
