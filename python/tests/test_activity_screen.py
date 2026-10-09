"""Hand-computed toy screen contracts; no actual data or source certification."""

from array import array
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path

import pytest

from research.activity_accounting import IdentityPeriod
from research.activity_portfolio import Series, eligible, simulate
from research.activity_replay import replay_synthetic
from research.activity_screen import (
    PublishedValue, ScreenObservation, SyntheticListing, screen_synthetic,
)


KST = timezone(timedelta(hours=9))
CAP_FLOOR = D("5000000000000")
LIQUIDITY_FLOOR = D("10000000000")


def fixture(*, count=65, formation=60, lookback=60):
    calendar = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(count))
    cutoff = datetime.combine(calendar[formation + 1], time(8, 30), KST)
    quotes = {"A": {day: ScreenObservation("observed", D(10), cutoff) for day in calendar}}
    liquidity = {"A": {day: ScreenObservation("observed", 2 * LIQUIDITY_FLOOR, cutoff)
                        for day in calendar}}
    periods = (IdentityPeriod("A", calendar[0], calendar[-1] + timedelta(days=1),
                              calendar[0], True, "reviewed synthetic combined classification"),)
    return dict(calendar=calendar, formation_index=formation,
                params={"lookback": lookback, "threshold": 3}, quotes=quotes,
                liquidity=liquidity,
                capitalization={"A": PublishedValue(CAP_FLOOR, cutoff)},
                periods=periods, listings={}, liquidity_lookback=60,
                liquidity_floor=LIQUIDITY_FLOOR, capitalization_floor=CAP_FLOOR,
                decision_at=cutoff)


def row(result):
    return result.selection.candidates[0]


def diagnostic(result):
    return result.diagnostics[0]


def test_prior_median_not_mean_and_inclusive_activity_threshold_matches_v1():
    inputs = fixture()
    dates = inputs["calendar"]
    inputs["quotes"]["A"][dates[59]] = replace(inputs["quotes"]["A"][dates[59]], turnover=D(1000))
    inputs["quotes"]["A"][dates[60]] = replace(inputs["quotes"]["A"][dates[60]], turnover=D(30))
    result = screen_synthetic(**inputs)
    assert diagnostic(result).activity_median == D(10)
    assert diagnostic(result).activity_ratio == D(3)
    assert row(result).activity_pass is True
    legacy = Series(array("d", [100] * len(dates)), array("d", [100] * len(dates)),
                    array("d", [10] * 59 + [1000, 30] + [10] * (len(dates) - 61)),
                    bytearray(len(dates)), bytearray(len(dates)))
    assert eligible(legacy, 60, 60, 3) is row(result).activity_pass


@pytest.mark.parametrize("zeros, expected, passed", [(30, LIQUIDITY_FLOOR, "pass"), (31, D(0), "fail")])
def test_fixed_liquidity_exact_even_median_keeps_verified_frozen_zeros(zeros, expected, passed):
    inputs = fixture()
    dates = inputs["calendar"]
    for day in dates[:zeros]:
        inputs["liquidity"]["A"][day] = ScreenObservation("frozen", D(0), inputs["decision_at"])
    # Formation is deliberately enormous: it cannot enter the preceding window.
    inputs["liquidity"]["A"][dates[60]] = ScreenObservation("observed", D("1e30"), inputs["decision_at"])
    result = screen_synthetic(**inputs)
    assert diagnostic(result).liquidity_median == expected
    assert row(result).absolute_liquidity == passed
    assert diagnostic(result).liquidity_dates == dates[:60]
    assert diagnostic(result).liquidity_zero_count == diagnostic(result).liquidity_frozen_count == zeros


def test_decimal_median_preserves_large_integer_half_without_float_or_context_rounding():
    inputs = fixture()
    a = D("10000000000000000000000000000000000000000")
    b = D("10000000000000000000000000000000000000001")
    for i, day in enumerate(inputs["calendar"][:60]):
        inputs["liquidity"]["A"][day] = ScreenObservation("observed", a if i < 30 else b,
                                                         inputs["decision_at"])
    assert diagnostic(screen_synthetic(**inputs)).liquidity_median == D(
        "10000000000000000000000000000000000000000.5")


@pytest.mark.parametrize("gap", [None, ScreenObservation("missing", D(0), None)])
def test_unknown_gap_is_not_zero_or_verified_short_history(gap):
    inputs = fixture()
    if gap is None:
        del inputs["quotes"]["A"][inputs["calendar"][0]]
    else:
        inputs["quotes"]["A"][inputs["calendar"][0]] = gap
    with pytest.raises(ValueError, match="unresolved synthetic observation"):
        screen_synthetic(**inputs)


def test_verified_listing_and_complete_59_observations_prove_short_history():
    inputs = fixture()
    dates = inputs["calendar"]
    inputs["listings"] = {"A": SyntheticListing(dates[1], dates[0])}
    # Pre-listing values are unnecessary and must not be consumed.
    inputs["quotes"]["A"][dates[0]] = ScreenObservation("missing", None, None)
    inputs["liquidity"]["A"][dates[0]] = ScreenObservation("missing", None, None)
    result = screen_synthetic(**inputs)
    assert row(result).operating_baseline == row(result).absolute_liquidity == "insufficient"
    assert row(result).activity_pass is False
    assert len(diagnostic(result).normal_dates) == 59
    assert diagnostic(result).activity_median is diagnostic(result).activity_ratio is None
    assert diagnostic(result).liquidity_median is None
    # A gap after the proven start still blocks, even with an already short window.
    del inputs["liquidity"]["A"][dates[2]]
    with pytest.raises(ValueError, match="unresolved synthetic observation"):
        screen_synthetic(**inputs)


def test_100_spac_observations_cannot_complete_59_operating_observations():
    inputs = fixture(count=165, formation=159)
    dates = inputs["calendar"]
    inputs["periods"] = (
        IdentityPeriod("A", dates[0], dates[100], dates[0], False, "toy SPAC period"),
        IdentityPeriod("A", dates[100], dates[-1] + timedelta(days=1), dates[99], True,
                       "reviewed toy operating boundary"),
    )
    for day in dates[:100]:
        inputs["quotes"]["A"][day] = ScreenObservation("missing", None, None)
    result = screen_synthetic(**inputs)
    assert row(result).operating_baseline == "insufficient"
    assert row(result).activity_pass is False
    assert diagnostic(result).normal_dates == dates[100:159]
    assert diagnostic(result).activity_median is diagnostic(result).activity_ratio is None
    assert row(result).absolute_liquidity == "pass"


def test_normal_baseline_skips_frozen_but_liquidity_stays_in_fixed_calendar_window():
    inputs = fixture(count=66, formation=62)
    dates = inputs["calendar"]
    for day in dates[60:62]:
        inputs["quotes"]["A"][day] = ScreenObservation("frozen", D(0), inputs["decision_at"])
        inputs["liquidity"]["A"][day] = ScreenObservation("frozen", D(0), inputs["decision_at"])
    result = screen_synthetic(**inputs)
    assert diagnostic(result).normal_dates == dates[:60]
    assert diagnostic(result).skipped_frozen_dates == dates[60:62]
    assert diagnostic(result).liquidity_dates == dates[2:62]
    assert diagnostic(result).liquidity_frozen_count == 2
    assert diagnostic(result).activity_median == D(10)
    assert row(result).operating_baseline == "pass"


def test_verified_nonfrozen_zero_remains_in_activity_baseline():
    inputs = fixture()
    for day in inputs["calendar"][:60]:
        inputs["quotes"]["A"][day] = ScreenObservation("observed", D(0), inputs["decision_at"])
    result = screen_synthetic(**inputs)
    assert row(result).operating_baseline == "pass" and row(result).activity_pass is False
    assert diagnostic(result).activity_median == 0 and diagnostic(result).activity_ratio is None


def test_rounded_repeating_ratio_does_not_create_a_threshold_pass():
    inputs = fixture()
    inputs["params"]["threshold"] = D("0.6666666666666666666666666667")
    for day in inputs["calendar"][:60]:
        inputs["quotes"]["A"][day] = ScreenObservation("observed", D(3), inputs["decision_at"])
    day = inputs["calendar"][60]
    inputs["quotes"]["A"][day] = ScreenObservation("observed", D(2), inputs["decision_at"])
    # 2/3 is strictly below this finite decimal, although its rounded quotient
    # equals that decimal at the standard context precision.
    assert row(screen_synthetic(**inputs)).activity_pass is False


@pytest.mark.parametrize("size_failed", [False, True])
def test_next_day_classification_stops_full_pool_before_size_or_any_held_code_filter(size_failed):
    inputs = fixture()
    inputs["periods"] = (replace(inputs["periods"][0], known_on=inputs["calendar"][61]),)
    if size_failed:
        inputs["capitalization"]["A"] = PublishedValue(D(0), inputs["decision_at"])
    # The helper takes no held-code filter: the full supplied pool must resolve.
    with pytest.raises(ValueError, match="unknown or conflicting historical instrument identity"):
        screen_synthetic(**inputs)


@pytest.mark.parametrize("kind", ["missing", "overlap", "half_open_end"])
def test_missing_overlapping_or_ended_formation_identity_cannot_remove_a_candidate(kind):
    inputs = fixture()
    period = inputs["periods"][0]
    inputs["periods"] = (() if kind == "missing" else
                         (period, replace(period, source="conflicting toy")) if kind == "overlap" else
                         (replace(period, end=inputs["calendar"][60]),))
    with pytest.raises(ValueError, match="unknown or conflicting historical instrument identity"):
        screen_synthetic(**inputs)


def test_conflicting_required_prior_identity_is_rejected_even_if_formation_is_unique():
    inputs = fixture()
    period = inputs["periods"][0]
    inputs["periods"] += (replace(period, start=inputs["calendar"][59], end=inputs["calendar"][60]),)
    with pytest.raises(ValueError, match="conflicting operating-period history"):
        screen_synthetic(**inputs)


@pytest.mark.parametrize("source", ["formation_quote", "prior_quote", "liquidity", "capitalization"])
def test_every_separate_source_allows_exact_cutoff_and_refuses_one_second_late(source):
    inputs = fixture()
    assert screen_synthetic(**inputs)
    late = inputs["decision_at"] + timedelta(seconds=1)
    if source == "capitalization":
        inputs["capitalization"]["A"] = PublishedValue(CAP_FLOOR, late)
    else:
        mapping = inputs["liquidity"] if source == "liquidity" else inputs["quotes"]
        day = inputs["calendar"][60 if source == "formation_quote" else 59]
        mapping["A"][day] = replace(mapping["A"][day], public_available_at=late)
    with pytest.raises(ValueError, match="late synthetic public availability"):
        screen_synthetic(**inputs)


@pytest.mark.parametrize("available", [None, datetime(2020, 3, 2, 8, 30)])
def test_unknown_or_naive_publication_is_not_assumed_available(available):
    inputs = fixture()
    inputs["capitalization"]["A"] = PublishedValue(CAP_FLOOR, available)
    with pytest.raises(ValueError, match="synthetic public availability"):
        screen_synthetic(**inputs)


def test_known_excluded_control_needs_no_operating_screens():
    inputs = fixture()
    inputs["periods"] = (replace(inputs["periods"][0], common_stock=False),)
    inputs["capitalization"] = inputs["liquidity"] = {}
    result = screen_synthetic(**inputs)
    assert row(result).classification == "excluded"
    assert row(result).size_pass is row(result).activity_pass is None
    assert diagnostic(result).normal_dates == ()


def test_frozen_formation_is_not_a_present_nonfrozen_candidate():
    inputs = fixture()
    inputs["quotes"]["A"][inputs["calendar"][60]] = ScreenObservation("frozen", D(0), inputs["decision_at"])
    inputs["periods"] = ()
    assert screen_synthetic(**inputs).selection.candidates == ()


def test_calendar_truncation_is_not_a_verified_short_history():
    inputs = fixture()
    inputs["periods"] = (replace(inputs["periods"][0], start=inputs["calendar"][0] - timedelta(days=1)),)
    inputs["quotes"]["A"][inputs["calendar"][0]] = ScreenObservation("frozen", D(0), inputs["decision_at"])
    with pytest.raises(ValueError, match="truncated calendar cannot prove short"):
        screen_synthetic(**inputs)
    inputs = fixture(formation=59)
    inputs["listings"] = {"A": SyntheticListing(inputs["calendar"][1], inputs["calendar"][0])}
    with pytest.raises(ValueError, match="truncated calendar cannot supply the fixed"):
        screen_synthetic(**inputs)


def test_unused_earlier_and_future_observation_mutations_leave_earlier_output_unchanged():
    inputs = fixture(count=70, lookback=2)
    expected = screen_synthetic(**inputs)
    for day in (inputs["calendar"][0], *inputs["calendar"][61:]):
        inputs["quotes"]["A"][day] = ScreenObservation("missing", None, None)
    for day in inputs["calendar"][60:]:
        inputs["liquidity"]["A"][day] = ScreenObservation("missing", None, None)
    assert screen_synthetic(**inputs) == expected


@pytest.mark.parametrize("change", ["decision", "lookback", "floor", "missing_turnover"])
def test_invalid_required_inputs_fail_closed(change):
    inputs = fixture()
    if change == "decision":
        inputs["decision_at"] -= timedelta(minutes=1)
    elif change == "lookback":
        inputs["params"]["lookback"] = True
    elif change == "floor":
        inputs["liquidity_floor"] = D("NaN")
    else:
        inputs["quotes"]["A"][inputs["calendar"][59]] = ScreenObservation("observed", None, inputs["decision_at"])
    with pytest.raises(ValueError):
        screen_synthetic(**inputs)


def test_screen_outputs_feed_bb_replay_and_preserve_action_free_v1_accounting():
    dates = tuple(date(2026, 9, 8) + timedelta(days=i) for i in range(13))
    spec = Path(__file__).resolve().parents[2] / "configs/research/discovery/activity-calibration-v1.json"
    params = json.loads(spec.read_text())["parameters"]
    params.update(lookback=2, holding_sessions=3, slots=2, end=dates[10].isoformat())
    panel = {code: Series(array("d", [100] * 13), array("d", [100] * 13),
                          array("d", [10] * 13), bytearray(13), bytearray(13))
             for code in ("A", "B", "C")}
    for series in panel.values():
        series.turnover[2] = series.turnover[5] = 30
    periods = tuple(IdentityPeriod(code, dates[0], dates[-1] + timedelta(days=1), dates[0],
                                   True, "reviewed toy combined classification") for code in panel)
    selections = []
    for index in (2, 5):
        cutoff = datetime.combine(dates[index + 1], time(8, 30), KST)
        quotes = {code: {day: ScreenObservation("observed", D(str(series.turnover[i])), cutoff)
                         for i, day in enumerate(dates)} for code, series in panel.items()}
        liquidity = {code: {day: ScreenObservation("observed", LIQUIDITY_FLOOR, cutoff)
                            for day in dates} for code in panel}
        screen = screen_synthetic(
            dates, index, params, quotes, liquidity,
            {code: PublishedValue(CAP_FLOOR, cutoff) for code in panel}, periods, {},
            liquidity_lookback=2, liquidity_floor=LIQUIDITY_FLOOR,
            capitalization_floor=CAP_FLOOR, decision_at=cutoff)
        assert all(candidate.activity_pass == eligible(panel[candidate.code], index, 2, params["threshold"])
                   for candidate in screen.selection.candidates)
        selections.append(screen.selection)
    result = replay_synthetic(dates, panel, params, selections, dataset_sha256="a" * 64)
    legacy = simulate(list(dates), panel, params)
    assert list(map(float, result.navs)) == pytest.approx(legacy["navs"])
    assert dict(result.diagnostics) == legacy["diagnostics"]
    assert result.closed_trades == legacy["closed_trades"]
