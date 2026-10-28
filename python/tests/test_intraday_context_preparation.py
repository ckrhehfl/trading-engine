"""Invented values only: no market files, loaders, experiments or trial logs."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from pathlib import Path

import pytest

from research import intraday_context_preparation as ea
from research import intraday_pattern_events as core


D = Decimal
MINUTE = timedelta(minutes=1)
DAY = date(2026, 1, 5)
OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
CLOSE = OPEN + 390 * MINUTE
SYMBOL = "KRX:000001"
MEMBERS = tuple(f"KRX:{n:06d}" for n in range(1, 12))
PEERS = MEMBERS[1:]


def meta(end, *, available=None, final=None):
    return ea.Provenance(end, available or end + 2 * MINUTE, CLOSE + timedelta(days=1),
                         "invented_fixture", final, "synthetic-v1", "synthetic")


def target():
    return ea.Target(SYMBOL, DAY, "D+1", OPEN, CLOSE, "invented_comparable_KRW",
                     ea.PriceSource(CLOSE + timedelta(days=1), "invented_fixture", None,
                                    "synthetic-v1", "synthetic"))


def universe():
    fixed = OPEN - timedelta(days=1)
    formation = OPEN.replace(month=12, year=2025, day=31)
    return ea.MonthlyUniverse(DAY.replace(day=1), MEMBERS, fixed, meta(formation))


def bar(n, op="100", hi="112", lo="98", cl="110"):
    end = OPEN + 5 * n * MINUTE
    return core.Bar(end - 5 * MINUTE, end, end + 2 * MINUTE, D(op), D(hi), D(lo), D(cl))


def price_bars(setup="EA-A"):
    if setup == "EA-A":
        return [bar(n) for n in range(1, 13)] + [
            bar(13, "109", "110", "107", "109"),
            bar(14, "108", "109", "107", "108"),
            bar(15, "110", "111", "108", "110"),
        ]
    return [bar(n, "100", "110", "90", "100") for n in range(1, 7)] + [
        bar(7, "89", "90", "88", "89"),
        bar(8, "90", "90", "89", "90"),
        bar(9, "91", "92", "90", "91"),
    ]


def window(symbol, start, end, cl="101"):
    return ea.PriceWindow(symbol, DAY, start, end, D("100"), D(cl), meta(end),
                          "invented_comparable_KRW")


def volume_window(day, shares="1"):
    opening = OPEN.replace(year=day.year, month=day.month, day=day.day)
    return ea.VolumeWindow(SYMBOL, day, opening, tuple(
        ea.VolumeMinute(opening + n * MINUTE, D(shares), meta(opening + (n + 1) * MINUTE))
        for n in range(60)), "invented_comparable_shares")


def volumes(current="2", baseline="1"):
    dates = tuple(DAY - timedelta(days=n) for n in range(20, 0, -1))
    openings = {day: OPEN.replace(year=day.year, month=day.month, day=day.day) for day in dates}
    return ea.VolumeInputs(dates, openings, volume_window(DAY, current),
                           {day: volume_window(day, baseline) for day in dates})


def arguments(setup="EA-A", *, end=None):
    end = end or OPEN + (75 if setup == "EA-A" else 45) * MINUTE
    first_end = end if setup == "EA-A" else OPEN + 30 * MINUTE
    return {
        "target": target(), "universe": universe(),
        "first_window_peers": {p: window(p, OPEN, first_end, "101" if setup == "EA-A" else "99.5")
                               for p in PEERS},
        "last15_peers": {p: window(p, end - 15 * MINUTE, end, "100") for p in PEERS},
        "volume": volumes() if setup == "EA-A" else None,
    }


def prepare(setup="EA-A", *, bars=None, **overrides):
    kwargs = arguments(setup)
    kwargs.update(overrides)
    return ea.prepare_candidate(setup, price_bars(setup) if bars is None else bars, **kwargs)


def path(candidate, op="110", cl="110", lo="109"):
    return {candidate.entry_at + n * MINUTE: (D(op), D("112"), D(lo), D(cl))
            for n in range(61)}


@pytest.mark.parametrize("setup,stop,decision,entry", [
    ("EA-A", Fraction(107), 77, 80), ("EA-B", Fraction(88), 47, 50),
])
def test_true_positive_preserves_original_candidate_and_common_exit(setup, stop, decision, entry):
    result = prepare(setup)
    assert result.status == "selected"
    assert all(status == "selected" for status in result.arms.values())
    assert result.structural_stop == stop
    assert result.basic_candidate.decision_at == OPEN + decision * MINUTE
    assert result.entry_at == OPEN + entry * MINUTE
    assert result.cap_at == result.entry_at + 60 * MINUTE
    assert result.entry_at > result.basic_candidate.signal_bar_end
    assert result.common_context_observable
    assert result.fixed_peers == PEERS
    assert not result.actual_execution_authorized
    assert not result.historical_timing_certified
    assert not result.promotion_allowed


def test_a_features_use_exact_relative_returns_and_volume_ratio():
    result = prepare()
    assert result.features["relative_volume"].value == 2
    assert result.features["stock_minus_peer_return"].value == Fraction(9, 100)
    assert result.features["peer_last15_return"].value == 0
    assert len(result.features["relative_volume"].provenance) == 21 * 60


@pytest.mark.parametrize("setup,feature,close", [
    ("EA-A", "first_window_peers", "110"),
    ("EA-A", "last15_peers", "99.999"),
    ("EA-B", "first_window_peers", "99.5001"),
    ("EA-B", "last15_peers", "99.999"),
])
def test_each_market_rejection_keeps_basic_selected(setup, feature, close):
    kwargs = arguments(setup)
    kwargs[feature] = {p: replace(w, closing_price=D(close)) for p, w in kwargs[feature].items()}
    result = ea.prepare_candidate(setup, price_bars(setup), **kwargs)
    assert result.status == "rejected"
    assert result.arms["basic"] == "selected"
    assert result.common_context_observable


@pytest.mark.parametrize("current", ["0", "1.999999999999999999999999999"])
def test_zero_volume_is_observed_and_volume_rejection_is_separate(current):
    result = prepare(volume=volumes(current))
    assert result.status == "rejected"
    assert result.arms == {"basic": "selected", "volume_only": "rejected",
                           "market_relative_only": "selected", "both": "rejected"}
    assert result.features["relative_volume"].value == Fraction(D(current))


def test_missing_feature_is_unknown_even_when_another_feature_rejects():
    kwargs = arguments()
    kwargs["volume"] = None
    kwargs["last15_peers"] = {p: replace(w, closing_price=D("99")) for p, w in kwargs["last15_peers"].items()}
    result = ea.prepare_candidate("EA-A", price_bars(), **kwargs)
    assert result.status == "unknown"
    assert result.arms["market_relative_only"] == "rejected"
    assert result.arms["basic"] == "selected"
    assert not result.common_context_observable


def test_nonpositive_reference_median_is_unknown_without_imputation():
    result = prepare(volume=volumes(baseline="0"))
    assert result.status == "unknown"
    assert result.features["relative_volume"].reasons == ("nonpositive_reference_median",)


def test_even_median_is_exact_and_not_mean():
    inputs = volumes()
    refs = {day: volume_window(day, "1" if i < 10 else "3")
            for i, day in enumerate(inputs.expected_reference_dates)}
    assert prepare(volume=replace(inputs, references=refs)).features["relative_volume"].value == 1


@pytest.mark.parametrize("change", ["nineteen", "duplicate", "today", "unordered", "datetime"])
def test_reference_calendar_must_be_exact_twenty_unique_prior_dates(change):
    inputs = volumes()
    dates = inputs.expected_reference_dates
    modified = {"nineteen": dates[:-1], "duplicate": dates[:-1] + (dates[0],),
                "today": dates[:-1] + (DAY,), "unordered": tuple(reversed(dates)),
                "datetime": dates[:-1] + (OPEN,)}[change]
    with pytest.raises(ea.PreparationRefusal, match="exact_twenty_prior"):
        prepare(volume=replace(inputs, expected_reference_dates=modified))


def test_missing_reference_retained_as_unknown_not_replaced():
    inputs = volumes()
    refs = dict(inputs.references)
    del refs[inputs.expected_reference_dates[0]]
    result = prepare(volume=replace(inputs, references=refs))
    assert result.status == "unknown"
    assert "missing_volume_reference" in result.features["relative_volume"].reasons
    refs[DAY - timedelta(days=21)] = volume_window(DAY - timedelta(days=21))
    with pytest.raises(ea.PreparationRefusal, match="unexpected_reference_date"):
        prepare(volume=replace(inputs, references=refs))


@pytest.mark.parametrize("change", ["negative", "float", "nan", "unit", "basis", "date", "symbol", "gap"])
def test_invalid_volume_inputs_refuse(change):
    inputs = volumes()
    current = inputs.current
    if change in {"negative", "float", "nan", "gap"}:
        minute = current.minutes[0]
        minute = replace(minute, start_at=minute.start_at + MINUTE) if change == "gap" else replace(
            minute, shares={"negative": D("-1"), "float": 1.0, "nan": D("NaN")}[change])
        current = replace(current, minutes=(minute,) + current.minutes[1:])
    else:
        current = replace(current, **{"unit": {"unit": "contracts"},
                                     "basis": {"comparable_share_basis": "different"},
                                     "date": {"session_date": DAY - timedelta(days=1)},
                                     "symbol": {"symbol": PEERS[0]}}[change])
    with pytest.raises(ea.PreparationRefusal):
        prepare(volume=replace(inputs, current=current))


def test_short_volume_window_is_unknown_and_no_forward_fill():
    inputs = volumes()
    result = prepare(volume=replace(inputs, current=replace(inputs.current, minutes=inputs.current.minutes[:-1])))
    assert result.features["relative_volume"].reasons == ("incomplete_volume_window",)


def test_future_volume_availability_is_not_rescued_by_later_retrieval():
    inputs = volumes()
    last = inputs.current.minutes[-1]
    last = replace(last, provenance=replace(last.provenance, available_at=OPEN + 78 * MINUTE))
    changed = replace(inputs.current, minutes=inputs.current.minutes[:-1] + (last,))
    with pytest.raises(ea.PreparationRefusal, match="source_clock"):
        prepare(volume=replace(inputs, current=changed))


def test_reference_opening_must_match_frozen_calendar_not_arbitrary_intraday_window():
    inputs = volumes()
    refs = dict(inputs.references)
    day = inputs.expected_reference_dates[0]
    original = refs[day]
    shifted = original.opening + 120 * MINUTE
    shifted_minutes = tuple(replace(m, start_at=m.start_at + 120 * MINUTE,
                                    provenance=meta(m.provenance.observation_end + 120 * MINUTE))
                            for m in original.minutes)
    refs[day] = replace(original, opening=shifted, minutes=shifted_minutes)
    with pytest.raises(ea.PreparationRefusal, match="volume_session_clock_changed"):
        prepare(volume=replace(inputs, references=refs))
    openings = dict(inputs.expected_reference_openings)
    del openings[day]
    with pytest.raises(ea.PreparationRefusal, match="exact_reference_calendar_required"):
        prepare(volume=replace(inputs, expected_reference_openings=openings))


def test_universe_formation_and_fixed_clocks_are_distinct_and_causal():
    original = universe()
    assert original.provenance.observation_end < original.fixed_at
    assert prepare(universe=original).status == "selected"
    changed = replace(original, provenance=meta(OPEN - timedelta(days=1)))
    with pytest.raises(ea.PreparationRefusal, match="monthly_formation"):
        prepare(universe=changed)
    changed = replace(original, provenance=replace(original.provenance, available_at=original.fixed_at + MINUTE))
    with pytest.raises(ea.PreparationRefusal, match="source_clock"):
        prepare(universe=changed)


@pytest.mark.parametrize("change", ["self", "duplicate", "future", "month", "absent_target"])
def test_fixed_membership_identity_and_clock_refusals(change):
    args = arguments()
    members = args["universe"]
    if change == "self":
        args["first_window_peers"][SYMBOL] = window(SYMBOL, OPEN, OPEN + 75 * MINUTE)
    elif change == "duplicate":
        args["universe"] = replace(members, members=members.members + (PEERS[0],))
    elif change == "future":
        args["universe"] = replace(members, fixed_at=OPEN + MINUTE)
    elif change == "month":
        args["universe"] = replace(members, month=date(2026, 2, 1))
    else:
        args["universe"] = replace(members, members=PEERS)
    with pytest.raises(ea.PreparationRefusal):
        ea.prepare_candidate("EA-A", price_bars(), **args)


def test_nine_peers_is_unknown_even_with_every_supplied_peer_complete():
    args = arguments()
    args["universe"] = replace(universe(), members=MEMBERS[:-1])
    args["first_window_peers"].pop(PEERS[-1])
    args["last15_peers"].pop(PEERS[-1])
    result = ea.prepare_candidate("EA-A", price_bars(), **args)
    assert result.status == "unknown"
    assert len(result.fixed_peers) == 9
    assert result.features["peer_last15_return"].reasons == ("fewer_than_ten_fixed_peers",)


@pytest.mark.parametrize("feature", ["first_window_peers", "last15_peers"])
def test_incomplete_basket_retains_missing_member_in_denominator(feature):
    args = arguments()
    del args[feature][PEERS[0]]
    result = ea.prepare_candidate("EA-A", price_bars(), **args)
    assert result.status == "unknown"
    assert result.fixed_peers == PEERS
    assert not result.common_context_observable


@pytest.mark.parametrize("change", ["future", "end", "early_available", "early_retrieval", "naive", "final_type", "source", "vintage", "evidence", "evidence_type"])
def test_metadata_invalid_or_future_clocks_refuse(change):
    args = arguments()
    original = args["last15_peers"][PEERS[0]]
    provenance = original.provenance
    modifications = {"future": {"available_at": OPEN + 78 * MINUTE},
                     "end": {"observation_end": original.end_at - MINUTE},
                     "early_available": {"available_at": original.end_at - MINUTE},
                     "early_retrieval": {"retrieved_at": original.end_at - MINUTE},
                     "naive": {"available_at": provenance.available_at.replace(tzinfo=None)},
                     "final_type": {"is_final": 1}, "source": {"source": ""},
                     "vintage": {"data_vintage": ""}, "evidence": {"evidence_level": "certified"},
                     "evidence_type": {"evidence_level": []}}
    args["last15_peers"][PEERS[0]] = replace(original, provenance=replace(provenance, **modifications[change]))
    with pytest.raises(ea.PreparationRefusal):
        ea.prepare_candidate("EA-A", price_bars(), **args)


def test_unknown_and_provisional_finality_and_later_retrieval_are_preserved():
    args = arguments()
    window0 = args["last15_peers"][PEERS[0]]
    args["last15_peers"][PEERS[0]] = replace(window0, provenance=replace(window0.provenance, is_final=False))
    result = ea.prepare_candidate("EA-A", price_bars(), **args)
    assert result.status == "selected"
    assert result.features["peer_last15_return"].provenance[0].is_final is False
    assert result.features["peer_last15_return"].provenance[1].is_final is None
    assert result.target.price_source.is_final is None
    assert result.target.price_source.retrieved_at > result.basic_candidate.decision_at
    assert not result.historical_timing_certified


@pytest.mark.parametrize("change", ["symbol", "date", "datetime", "window", "unit", "price"])
def test_peer_identity_and_price_window_refusals(change):
    args = arguments()
    w = args["first_window_peers"][PEERS[0]]
    updates = {"symbol": {"symbol": SYMBOL}, "date": {"session_date": DAY - timedelta(days=1)},
               "datetime": {"session_date": OPEN}, "window": {"start_at": OPEN + MINUTE},
               "unit": {"unit": "USD"}, "price": {"closing_price": 101.0}}
    args["first_window_peers"][PEERS[0]] = replace(w, **updates[change])
    with pytest.raises(ea.PreparationRefusal):
        ea.prepare_candidate("EA-A", price_bars(), **args)


@pytest.mark.parametrize("updates", [
    {"session_date": OPEN}, {"session_date": DAY - timedelta(days=1)}, {"policy": "D+3"},
    {"symbol": "000001"}, {"opening": OPEN.replace(tzinfo=None)},
    {"closing": OPEN + timedelta(days=1)}, {"price_basis": ""}, {"price_source": None}, {"policy": []},
])
def test_target_type_date_and_policy_contract(updates):
    with pytest.raises(ea.PreparationRefusal):
        prepare(target=replace(target(), **updates))


def test_first_candidate_only_no_later_consumption_or_rescue():
    def stream():
        yield from price_bars()
        raise AssertionError("future poison was consumed")
    rejected = prepare(bars=stream(), volume=volumes("1"))
    unknown = prepare(bars=stream(), volume=None)
    assert rejected.status == "rejected"
    assert unknown.status == "unknown"
    assert rejected.basic_candidate == unknown.basic_candidate


def test_missing_price_prefix_is_unknown_and_initial_failure_is_known_rejection():
    assert prepare(bars=price_bars()[:5]).status == "unknown"
    no_candidate = prepare(bars=[bar(n, cl="100") for n in range(1, 13)])
    assert no_candidate.status == "rejected"
    assert no_candidate.basic_candidate.reason == "initial_condition_false"
    assert no_candidate.entry_at is None


def test_short_session_retains_price_candidate_but_cannot_trade_at_close_cap():
    result = prepare(target=replace(target(), closing=OPEN + 140 * MINUTE))
    assert result.basic_candidate.state == "signal"
    assert result.status == "unknown"
    assert result.features["clock"].reasons == ("same_day_cap_infeasible",)


def test_near_cutoff_uses_original_decision_and_never_consumes_after_cutoff():
    late_bars = [bar(n) for n in range(1, 45)] + [
        bar(45, "109", "110", "107", "109"),
        bar(46, "108", "109", "107", "108"),
        bar(47, "110", "111", "108", "110"),
    ]
    result = ea.prepare_candidate("EA-A", late_bars, **arguments(end=OPEN + 235 * MINUTE))
    assert result.status == "selected"
    assert result.basic_candidate.decision_at == OPEN + 237 * MINUTE
    assert result.entry_at == OPEN + 240 * MINUTE

    def no_signal():
        yield from [bar(n) for n in range(1, 48)]
        raise AssertionError("after-cutoff price was consumed")
    assert prepare(bars=no_signal()).basic_candidate.reason == "decision_cutoff"


def test_changed_original_bar_availability_refuses_instead_of_using_retrieval():
    bars = price_bars()
    bars[-1] = replace(bars[-1], available_at=bars[-1].end_at)
    with pytest.raises(core.PatternValidationError, match="invalid_source_clock"):
        prepare(bars=bars)


def test_price_prefix_retrieval_before_consumed_observation_refuses():
    changed = replace(target().price_source, retrieved_at=OPEN + 74 * MINUTE)
    with pytest.raises(ea.PreparationRefusal, match="price_retrieval_precedes"):
        prepare(target=replace(target(), price_source=changed))


def test_synthetic_cap_cost_and_no_samebar_fill():
    candidate = prepare()
    result = ea.synthetic_outcome(candidate, path(candidate), synthetic=True)
    assert result["status"] == "known"
    assert result["entry_at"] == OPEN + 80 * MINUTE
    assert result["exit_at"] == OPEN + 140 * MINUTE
    assert result["gross"] == 0
    assert result["primary_net"] == Fraction(-33, 10000)
    assert result["stress_net"] == Fraction(-43, 10000)
    assert not result["actual_fill_certified"]


def test_close_stop_is_delayed_and_gap_loss_uses_later_open_not_stop_level():
    candidate = prepare()
    values = path(candidate)
    values[candidate.entry_at] = (D("110"), D("112"), D("106"), D("106"))
    fill = candidate.entry_at + 4 * MINUTE
    values[fill] = (D("90"), D("92"), D("89"), D("91"))
    result = ea.synthetic_outcome(candidate, values, synthetic=True)
    assert result["stop_observation_end"] == candidate.entry_at + MINUTE
    assert result["exit_at"] == fill
    assert result["exit_open"] == 90
    assert result["gross"] == Fraction(-2, 11)
    assert result["reason"] == "delayed_structural_stop"


@pytest.mark.parametrize("stop_exit", [False, True])
def test_exit_open_does_not_inspect_that_minutes_later_hlc(stop_exit):
    candidate = prepare()
    values = path(candidate)
    exit_at = candidate.cap_at
    if stop_exit:
        values[candidate.entry_at] = (D("110"), D("112"), D("106"), D("106"))
        exit_at = candidate.entry_at + 4 * MINUTE
    expected = ea.synthetic_outcome(candidate, values, synthetic=True)
    values[exit_at] = (values[exit_at][0], None, object(), "future close poison")
    assert ea.synthetic_outcome(candidate, values, synthetic=True) == expected


@pytest.mark.parametrize("entry_open", ["107", "100"])
def test_invalidated_entry_does_not_inspect_its_later_hlc_or_future_path(entry_open):
    candidate = prepare()
    values = {candidate.entry_at: (D(entry_open), None, object(), "future close poison")}
    result = ea.synthetic_outcome(candidate, values, synthetic=True)
    assert result["status"] == "declined"
    assert result["reason"] == "invalidated_entry"
    assert result["entry_open"] == Fraction(D(entry_open))
    assert result["gross"] is None


@pytest.mark.parametrize("field", [1, 2, 3])
def test_held_entry_minute_still_validates_completed_ohlc(field):
    candidate = prepare()
    values = path(candidate)
    broken = list(values[candidate.entry_at])
    broken[field] = "required completed minute poison"
    values[candidate.entry_at] = tuple(broken)
    with pytest.raises(ea.PreparationRefusal, match="invalid_exact_value"):
        ea.synthetic_outcome(candidate, values, synthetic=True)


def test_low_touch_without_close_does_not_trigger_and_below_stop_entry_declines():
    candidate = prepare()
    assert ea.synthetic_outcome(candidate, path(candidate, lo="100"), synthetic=True)["reason"] == "time_cap"
    for op in ("107", "100"):
        result = ea.synthetic_outcome(candidate, path(candidate, op=op, lo="99"), synthetic=True)
        assert result["status"] == "declined"
        assert result["reason"] == "invalidated_entry"
        assert result["gross"] is None


def test_exact_stop_close_triggers_and_stop_includes_recovery_bar_low():
    candidate = prepare()
    values = path(candidate, cl="107", lo="107")
    assert ea.synthetic_outcome(candidate, values, synthetic=True)["exit_at"] == candidate.entry_at + 4 * MINUTE
    for setup in ea.SETUPS:
        bars = price_bars(setup)
        bars[-1] = replace(bars[-1], low=D("80"))
        assert prepare(setup, bars=bars).structural_stop == 80


@pytest.mark.parametrize("offset,reason", [(0, "entry_open_missing"), (1, "required_path_missing"), (60, "exit_open_missing")])
def test_missing_entry_path_and_exit_are_unknown(offset, reason):
    candidate = prepare()
    values = path(candidate)
    del values[candidate.entry_at + offset * MINUTE]
    result = ea.synthetic_outcome(candidate, values, synthetic=True)
    assert result["status"] == "unknown"
    assert result["reason"] == reason
    assert result["gross"] is None


def test_delayed_stop_missing_fill_never_shifts_forward_and_later_poison_unread():
    candidate = prepare()
    values = path(candidate)
    values[candidate.entry_at] = (D("110"), D("112"), D("106"), D("106"))
    del values[candidate.entry_at + 4 * MINUTE]
    result = ea.synthetic_outcome(candidate, values, synthetic=True)
    assert result["reason"] == "exit_open_missing"
    values[candidate.entry_at + 4 * MINUTE] = (D("100"),) * 4
    values[candidate.entry_at + 5 * MINUTE] = "future poison"
    assert ea.synthetic_outcome(candidate, values, synthetic=True)["status"] == "known"


def test_stop_delivery_is_capped_and_gap_after_trigger_stays_unknown():
    candidate = prepare()
    values = path(candidate)
    values[candidate.entry_at + 58 * MINUTE] = (D("110"), D("112"), D("106"), D("106"))
    result = ea.synthetic_outcome(candidate, values, synthetic=True)
    assert result["exit_at"] == candidate.cap_at
    assert result["reason"] == "time_cap"
    values[candidate.entry_at] = (D("110"), D("112"), D("106"), D("106"))
    del values[candidate.entry_at + MINUTE]
    assert ea.synthetic_outcome(candidate, values, synthetic=True)["reason"] == "required_path_missing"


def test_synthetic_guard_and_missing_stop():
    candidate = prepare()
    with pytest.raises(ea.PreparationRefusal, match="invented_fixtures_only"):
        ea.synthetic_outcome(candidate, path(candidate), synthetic=False)
    result = ea.synthetic_outcome(replace(candidate, structural_stop=None), path(candidate), synthetic=True)
    assert result["status"] == "unknown"
    assert result["reason"] == "structural_stop_missing"


def test_decimal_context_changes_cannot_change_selection_or_return():
    values = []
    for precision in (2, 7, 28, 80):
        with localcontext() as context:
            context.prec = precision
            candidate = prepare(volume=volumes("1.999999999999999999999999999"))
            label = ea.synthetic_outcome(candidate, path(candidate), synthetic=True)
            values.append((candidate.status, candidate.features["relative_volume"].value, label["primary_net"]))
    assert all(value == values[0] for value in values)


def test_default_registration_is_preparation_only_and_every_changed_field_refuses():
    spec_path = Path(__file__).resolve().parents[2] / "configs/research/discovery/intraday-context-preparation-v1.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    ea.validate_spec(spec)
    assert spec["actual_execution_authorized"] is False
    assert spec["actual_input_manifest_complete"] is False
    assert [setup["id"] for setup in spec["setups"]] == ["EA-A", "EA-B"]
    variants = []
    actual = deepcopy(spec)
    actual["actual_execution_authorized"] = True
    variants.append(actual)
    parameter = deepcopy(spec)
    parameter["setups"][0]["relative_volume_minimum"] = "1.9"
    variants.append(parameter)
    clock = deepcopy(spec)
    clock["common"]["stop_order_allowance_minutes"] = 0
    variants.append(clock)
    wrong_type = deepcopy(spec)
    wrong_type["version"] = True
    variants.append(wrong_type)
    extra = deepcopy(spec)
    extra["new_field"] = "not registered"
    variants.append(extra)
    for variant in variants:
        with pytest.raises(ea.PreparationRefusal, match="preparation_registration_changed"):
            ea.validate_spec(variant)


def test_cached_volume_matches_uncached_and_revalidates_earlier_decision(monkeypatch):
    inputs, cache = volumes(), ea.VolumeValidationCache()
    decision = OPEN + 77 * MINUTE
    expected = ea.relative_volume(inputs, target(), decision)
    original, calls = ea._volume_total, []
    def counted(*args):
        calls.append(args[0])
        return original(*args)
    monkeypatch.setattr(ea, "_volume_total", counted)
    assert ea.relative_volume(inputs, target(), decision, validation_cache=cache) == expected
    assert len(calls) == 21
    assert ea.relative_volume(inputs, target(), decision + MINUTE, validation_cache=cache) == expected
    assert len(calls) == 21
    with pytest.raises(ea.PreparationRefusal, match="unavailable_or_invalid_source_clock"):
        ea.relative_volume(inputs, target(), OPEN + 61 * MINUTE, validation_cache=cache)
    assert len(calls) == 22


@pytest.mark.parametrize("kind", ["zero", "partial", "missing"])
def test_cached_unknowns_and_zero_keep_exact_provenance(kind):
    inputs = volumes("0" if kind == "zero" else "2")
    if kind == "partial":
        inputs = replace(inputs, current=replace(inputs.current, minutes=inputs.current.minutes[:59]))
    elif kind == "missing":
        inputs = replace(inputs, references={})
    cache, decision = ea.VolumeValidationCache(), OPEN + 77 * MINUTE
    expected = ea.relative_volume(inputs, target(), decision)
    for _ in range(2):
        assert ea.relative_volume(inputs, target(), decision, validation_cache=cache) == expected


@pytest.mark.parametrize("kind", ["symbol", "day", "opening", "replaced_window"])
def test_cached_window_does_not_bypass_identity_or_unit_validation(kind):
    window, cache = volume_window(DAY), ea.VolumeValidationCache()
    decision = OPEN + 77 * MINUTE
    cache.total(window, SYMBOL, DAY, OPEN, decision)
    args = [window, SYMBOL, DAY, OPEN, decision]
    if kind == "symbol": args[1] = "KRX:999999"
    if kind == "day": args[2] = DAY - timedelta(days=1)
    if kind == "opening": args[3] = OPEN + MINUTE
    if kind == "replaced_window": args[0] = replace(window, unit="lots")
    with pytest.raises(ea.PreparationRefusal):
        cache.total(*args)


def test_mutable_timezone_is_validated_again_instead_of_cached():
    class MutableZone(tzinfo):
        hours = 9
        def utcoffset(self, dt): return timedelta(hours=self.hours)
        def dst(self, dt): return timedelta(0)
    zone, cache = MutableZone(), ea.VolumeValidationCache()
    window = volume_window(DAY)
    first = window.minutes[0]
    changed = replace(first.provenance,
                      available_at=first.provenance.available_at.replace(tzinfo=zone))
    window = replace(window, minutes=(replace(first, provenance=changed), *window.minutes[1:]))
    decision = OPEN + 77 * MINUTE
    assert cache.total(window, SYMBOL, DAY, OPEN, decision).value == 60
    assert not cache._entries
    zone.hours = 7
    with pytest.raises(ea.PreparationRefusal, match="unavailable_or_invalid_source_clock"):
        cache.total(window, SYMBOL, DAY, OPEN, decision)


def test_volume_cache_has_bounded_strong_references():
    cache, window = ea.VolumeValidationCache(), volume_window(DAY)
    for _ in range(513):
        cache.total(replace(window), SYMBOL, DAY, OPEN, OPEN + 77 * MINUTE)
    assert len(cache._entries) == 512
    assert len({id(saved[0]) for saved in cache._entries.values()}) == 512
