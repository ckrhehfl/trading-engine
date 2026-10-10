"""Descriptive reports use existing replay code and fabricated books only."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from statistics import stdev

import pytest

from research.activity_accounting import Lot
from research.activity_book import ActivityBook
from research.activity_replay import DecimalSeries, SyntheticReplay, replay_synthetic
from research.activity_timing import SessionLagPolicy
from research.activity_timing_report import paired_timing_report
from test_activity_holding_inputs import declarations


def case(*, cash=False, unavailable=False, absent=False, frozen_exit=False):
    """Run the existing engine over one fixed synthetic paired source partition."""
    d1, d2, days, params = declarations()
    code = d1[0].eligible_codes[0]
    initial = None
    if cash:
        d1 = tuple(replace(row, eligible_codes=()) for row in d1)
        d2 = tuple(replace(row, eligible_codes=()) for row in d2)
        panel = {}
    else:
        opens, closes = [D(100)] * len(days), [D(100)] * len(days)
        opens[62] = D(200)  # D2 entry differs without changing the formation pool.
        observed = [True] * len(days)
        if absent:
            opens[61] = closes[61] = None
            observed[61] = False
        frozen, locked, turnover = [False] * len(days), [False] * len(days), [D(10)] * len(days)
        if absent:
            frozen[61] = locked[61] = turnover[61] = None
        if frozen_exit:
            frozen[91:130] = [True] * 39
        panel = {code: DecimalSeries(tuple(opens), tuple(closes), tuple(turnover),
                                    tuple(frozen), tuple(locked), tuple(observed))}
        if unavailable:
            d1 = (d1[0], replace(d1[1], eligible_codes=(code,)))
            d2 = (d2[0], replace(d2[1], eligible_codes=(code,)))
            initial = ActivityBook(D(0), (Lot("prior", code, D("0.01"), D(100), days[60], days[0],
                days[91], days[92], "a" * 64),), days[60])
    results = tuple(replay_synthetic(days, panel, params, selection,
        dataset_sha256=None if cash else "a" * 64, initial_book=initial,
        timing_policy=SessionLagPolicy(lag, paired=True)) for lag, selection in ((1, d1), (2, d2)))
    return (*results, d1, d2)


def report(inputs, initial=D(1)):
    """Provide the one explicit initial NAV required by the report contract."""
    return paired_timing_report(*inputs, initial_nav=initial)


def test_real_engine_cost_loss_lag_divergence_and_json_safe_units():
    """The initial peak catches entry costs and equal signals do not imply equal NAV."""
    inputs = case()
    value = report(inputs)
    json.dumps(value, allow_nan=False)
    one, two = value["arms"]["D1"], value["arms"]["D2"]
    assert D(one["path"][0]["session_return"]) == inputs[0].navs[0] - 1 < 0
    assert D(one["path"][0]["nav_ratio"]) == inputs[0].navs[0]
    assert D(one["maximum_drawdown"]) >= 1 - inputs[0].navs[0] > 0
    assert D(two["path"][0]["session_return"]) == 0
    assert D(one["total_return"]) != D(two["total_return"])
    for result, arm in zip(inputs[:2], (one, two)):
        navs = (D(1), *result.navs)
        returns = [right / left - 1 for left, right in zip(navs, navs[1:])]
        assert arm["session_return_sd"] == pytest.approx(stdev(map(float, returns)))
        assert arm["filled_entries"] == arm["ordinary_closed_trades"] == 1
        assert arm["terminal_open_lots"] == 0
    assert value["signal_overlap"]["symmetric_difference_count"] == 0
    assert value["signal_overlap"]["union_count"] == 1
    assert value["signal_overlap"]["rate"] == "0"
    assert value["target_overlap"]["rate"] == "0"
    assert "unannualized" in value["units"]["session_return_sd"]


def test_cash_only_empty_union_has_unknown_overlap_and_zero_dispersion():
    """Cash sessions have zero returns while no signal/target union has no rate."""
    value = report(case(cash=True), D(1))
    for arm in value["arms"].values():
        assert D(arm["total_return"]) == D(arm["maximum_drawdown"]) == 0
        assert arm["session_return_sd"] == 0
        assert arm["filled_entries"] == arm["ordinary_closed_trades"] == arm["terminal_open_lots"] == 0
        assert all(D(row["nav_ratio"]) == 1 and D(row["session_return"]) == 0 for row in arm["path"])
    for key in ("signal_overlap", "target_overlap"):
        assert value[key]["symmetric_difference_count"] == value[key]["union_count"] == 0
        assert value[key]["rate"] is None


def test_delayed_exit_changes_targets_with_identical_signals_and_counts_stay_separate():
    """An unavailable initial lot blocks D1's cutoff before D2's released slot."""
    inputs = case(unavailable=True)
    value = report(inputs)
    assert dict(inputs[0].diagnostics)["delayed_exits"] == dict(inputs[1].diagnostics)["delayed_exits"] == 1
    assert value["signal_overlap"]["union_count"] == 2
    assert value["signal_overlap"]["rate"] == "0"
    assert value["target_overlap"]["symmetric_difference_count"] == 1
    assert value["target_overlap"]["union_count"] == 1
    assert value["target_overlap"]["rate"] == "1"
    assert value["arms"]["D1"]["filled_entries"] == 0
    assert value["arms"]["D1"]["ordinary_closed_trades"] == 1
    assert value["arms"]["D2"]["filled_entries"] == 1
    assert value["arms"]["D2"]["ordinary_closed_trades"] == 2


def test_absent_fill_is_not_a_signal_or_target_change_and_terminal_lots_are_not_closed_trades():
    """Report the existing engine's failed fill and retained-position distinctions."""
    value = report(case(absent=True))
    assert value["target_overlap"]["rate"] == value["signal_overlap"]["rate"] == "0"
    assert value["arms"]["D1"]["filled_entries"] == 0
    assert value["arms"]["D2"]["filled_entries"] == 1
    value = report(case(frozen_exit=True))
    for arm in value["arms"].values():
        assert arm["filled_entries"] == arm["terminal_open_lots"] == 1
        assert arm["ordinary_closed_trades"] == 0


def test_absorbing_zero_and_single_session_dispersion():
    """Include the initial denominator and allow zero only as an absorbing state."""
    inputs = case(cash=True)
    days = tuple(book.as_of for book in inputs[0].books)
    zero = SyntheticReplay(tuple(ActivityBook(D(0), (), day) for day in days[:3]), (), (), 0)
    value = report((zero, zero, (), ()), D(2))
    arm = value["arms"]["D1"]
    assert [D(row["session_return"]) for row in arm["path"]] == [D(-1), D(0), D(0)]
    assert D(arm["total_return"]) == -1 and D(arm["maximum_drawdown"]) == 1
    single = replace(zero, books=(ActivityBook(D("1.8"), (), days[0]),))
    arm = report((single, single, (), ()), D(2))["arms"]["D1"]
    assert arm["session_return_sd"] is None
    assert D(arm["path"][0]["session_return"]) == D("-0.1")
    assert D(arm["maximum_drawdown"]) == D("0.1")
    recovered = replace(zero, books=(zero.books[0], ActivityBook(D(1), (), days[1])))
    with pytest.raises(ValueError, match="recover from zero"):
        report((recovered, recovered, (), ()))


def test_target_change_aggregates_counts_instead_of_averaging_formation_rates():
    """One 1/1 change and one 2/3 change aggregate to 3/4, not their mean."""
    one, two, first, second = case()
    codes = ("000001", "000006", "000007")
    first = tuple(replace(row, eligible_codes=codes, source_population_count=3) for row in first)
    second = tuple(replace(row, eligible_codes=codes, source_population_count=3) for row in second)
    one = replace(one, selections=((first[0].formation_on, (codes[0],)), (first[1].formation_on, codes[:2])))
    two = replace(two, selections=((second[0].formation_on, ()), (second[1].formation_on, (codes[0], codes[2]))))
    one = replace(one, diagnostics=tuple(sorted({**dict(one.diagnostics), "unfilled_entries": 2}.items())))
    two = replace(two, diagnostics=tuple(sorted({**dict(two.diagnostics), "unfilled_entries": 1}.items())))
    overlap = report((one, two, first, second))["target_overlap"]
    assert overlap["symmetric_difference_count"] == 3
    assert overlap["union_count"] == 4
    assert overlap["rate"] == "0.75"


@pytest.mark.parametrize("initial", [None, 1, 1.0, D(0), D(-1), D("NaN"), D("Infinity")])
def test_initial_nav_must_be_explicit_positive_finite_decimal(initial):
    with pytest.raises(ValueError, match="initial NAV"):
        report(case(cash=True), initial)


@pytest.mark.parametrize("corruption", [
    "absent_result", "books_missing", "books_list", "dates_duplicate", "dates_reverse", "dates_disagree",
    "selection_missing", "selection_list", "formation_disagree", "pool_disagree", "source_disagree",
    "population_disagree", "target_missing", "target_duplicate", "target_foreign", "target_formation",
    "diagnostic_duplicate", "diagnostic_bool", "closed_bool", "entries_exceed_targets",
    "unfilled_exceed_targets", "entry_outcome_missing", "entry_outcome_double_counted",
])
def test_malformed_or_incomplete_paired_results_are_rejected(corruption):
    one, two, first, second = case()
    if corruption == "absent_result": one = None
    elif corruption == "books_missing": one = replace(one, books=())
    elif corruption == "books_list": one = replace(one, books=list(one.books))
    elif corruption == "dates_duplicate": one = replace(one, books=(one.books[0], *one.books))
    elif corruption == "dates_reverse": one = replace(one, books=tuple(reversed(one.books)))
    elif corruption == "dates_disagree": two = replace(two, books=two.books[1:])
    elif corruption == "selection_missing": first = first[:-1]
    elif corruption == "selection_list": first = list(first)
    elif corruption == "formation_disagree": second = (replace(second[0], formation_on=second[0].formation_on - timedelta(days=1)), *second[1:])
    elif corruption == "pool_disagree": second = (replace(second[0], eligible_codes=()), *second[1:])
    elif corruption == "source_disagree": second = (replace(second[0], source_partition_sha256="e" * 64), *second[1:])
    elif corruption == "population_disagree": second = (replace(second[0], source_population_count=99), *second[1:])
    elif corruption == "target_missing": one = replace(one, selections=one.selections[:-1])
    elif corruption == "target_duplicate": one = replace(one, selections=((one.selections[0][0], ("000001", "000001")), *one.selections[1:]))
    elif corruption == "target_foreign": one = replace(one, selections=((one.selections[0][0], ("FOREIGN",)), *one.selections[1:]))
    elif corruption == "target_formation": one = replace(one, selections=tuple(reversed(one.selections)))
    elif corruption == "diagnostic_duplicate": one = replace(one, diagnostics=(("entries", 1), ("entries", 1)))
    elif corruption == "diagnostic_bool": one = replace(one, diagnostics=(("entries", True),))
    elif corruption == "closed_bool": one = replace(one, closed_trades=True)
    elif corruption == "entries_exceed_targets": one = replace(one, diagnostics=(("entries", 2),))
    elif corruption == "unfilled_exceed_targets": one = replace(one, diagnostics=(("unfilled_entries", 2),))
    elif corruption == "entry_outcome_missing": one = replace(one, diagnostics=())
    elif corruption == "entry_outcome_double_counted": one = replace(one, diagnostics=(("entries", 1), ("unfilled_entries", 1)))
    with pytest.raises(ValueError):
        report((one, two, first, second))
