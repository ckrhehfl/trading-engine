"""Invented-only ED-H1 causal inputs, exact arithmetic and shared schema."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import importlib.util
import math
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from research import intraday_ed_inputs as ed
from research.intraday_pattern_events import Bar


KST = timezone(timedelta(hours=9))
OPEN = datetime(2026, 1, 30, 9, tzinfo=KST)
D = Decimal


def session(symbol="TARGET", opening=OPEN, *, break_end=30, shares=D("10"), close=D("100"), count=60):
    rows = {}
    for index in range(count):
        at = opening + index * ed.MINUTE
        terminal = D("102") if index == break_end - 1 else close
        rows[at] = ed.Minute(symbol, at, at + 3 * ed.MINUTE,
                             (D("100"), max(D("101"), terminal), D("99"), terminal), shares)
    return ed.SessionInputs(symbol, opening, opening + 390 * ed.MINUTE, rows)


def change(value, index, **fields):
    at = value.opening + index * ed.MINUTE
    rows = dict(value.minutes)
    rows[at] = replace(rows[at], **fields)
    return replace(value, minutes=rows)


def references(current, *, increasing=False):
    expected = tuple(OPEN - timedelta(days=20 - index) for index in range(20))
    prior = {opening: session(current.symbol, opening, break_end=0,
                              shares=D(index + 1) if increasing else D("10"))
             for index, opening in enumerate(expected)}
    return expected, prior


def peers(*, count=5, close=D("100")):
    names = tuple(f"PEER{index}" for index in range(count))
    return names, {name: session(name, break_end=0, close=close) for name in names}


def context(current, *, state="ELIGIBLE_ORIGINAL", types=("ORIGINAL_ANNUAL",)):
    if state != "ELIGIBLE_ORIGINAL":
        return ed.OpeningContext(current.symbol, current.opening, state,
                                 None if state == "UNKNOWN" else current.opening)
    return ed.OpeningContext(current.symbol, current.opening, state, current.opening,
                             types, ("invented-receipt",), current.opening)


def parent(current):
    result = ed.first_h1_parent(current, through=current.opening + 122 * ed.MINUTE)
    assert result.status == "parent"
    return result.parent


def built(current, **overrides):
    names, other = peers()
    expected, prior = references(current, increasing=True)
    arguments = dict(original_peers=names, peers=other, expected=expected, references=prior,
                     opening_context=context(current))
    arguments.update(overrides)
    return ed.build_h1_window_inputs(current, parent(current), **arguments)


class PrefixOnly(Mapping):
    """Fail on suffix access or whole-session iteration."""

    def __init__(self, rows, boundary):
        self.rows, self.boundary = rows, boundary

    def __getitem__(self, key):
        if key >= self.boundary:
            raise AssertionError("future suffix accessed")
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("whole-session iteration")

    def __len__(self):
        raise AssertionError("whole-session length")


def test_first_parent_is_existing_bar_on_first_grid_and_never_reads_suffix():
    current = session()
    current = replace(current, minutes=PrefixOnly(current.minutes, OPEN + 30 * ed.MINUTE))
    result = ed.first_h1_parent(current, through=OPEN + 122 * ed.MINUTE)
    assert result.status == "parent"
    assert isinstance(result.parent.trigger, Bar)
    assert isinstance(result.parent, ed.EDParent)
    assert result.parent.trigger.end_at == OPEN + 30 * ed.MINUTE
    assert result.parent.decision_at == OPEN + 32 * ed.MINUTE
    assert len(result.parent.preceding) == 5
    assert ed.first_h1_parent(current, through=OPEN + 31 * ed.MINUTE).status == "known_no_parent"


def test_rolling_five_bar_high_excludes_trigger_and_old_opening_range():
    current = change(session(break_end=35), 0, ohlc=(D("100"), D("200"), D("99"), D("100")))
    current = change(current, 34, ohlc=(D("100"), D("102"), D("80"), D("102")))
    found = parent(current)
    assert found.trigger.end_at == OPEN + 35 * ed.MINUTE
    assert found.structural_stop == D("99")


def test_equal_high_is_not_break_and_first_parent_survives_later_break():
    current = change(session(break_end=35), 29, ohlc=(D("100"), D("101"), D("99"), D("101")))
    assert parent(current).trigger.end_at == OPEN + 35 * ed.MINUTE
    current = change(current, 39, ohlc=(D("100"), D("1000"), D("99"), D("1000")))
    assert parent(current).trigger.end_at == OPEN + 35 * ed.MINUTE


def test_missing_first_grid_prevents_later_first_parent_certification():
    current = session(break_end=60)
    rows = dict(current.minutes)
    del rows[OPEN + ed.MINUTE]
    result = ed.first_h1_parent(replace(current, minutes=rows), through=OPEN + 62 * ed.MINUTE)
    assert result.status == "parent_identity_unknown"
    assert result.parent is None
    assert result.issues == (ed.Issue("TARGET", OPEN + ed.MINUTE, "missing_minute"),)


def test_known_no_parent_requires_complete_price_prefix():
    current = session(break_end=0)
    assert ed.first_h1_parent(current, through=OPEN + 62 * ed.MINUTE).status == "known_no_parent"
    current = change(current, 4, ohlc=None, price_state="transport_error")
    result = ed.first_h1_parent(current, through=OPEN + 62 * ed.MINUTE)
    assert result.status == "parent_identity_unknown"
    assert result.issues[0].reason == "price_transport_error"


def test_final_grid_requires_full_cap_before_continuous_liquidation_guard():
    current = session(break_end=310, count=390)
    assert ed.first_h1_parent(current, through=current.closing).parent.trigger.end_at == OPEN + 310 * ed.MINUTE
    late = session(break_end=315, count=390)
    late = replace(late, minutes=PrefixOnly(late.minutes, OPEN + 310 * ed.MINUTE))
    assert ed.first_h1_parent(late, through=late.closing).status == "known_no_parent"


def test_price_future_and_volume_missing_do_not_select_a_different_parent():
    current = session()
    expected = parent(current)
    current = change(current, 30, ohlc=(D("NaN"),) * 4, shares=D("NaN"))
    current = change(current, 29, shares=None, volume_state="missing")
    assert parent(current) == expected
    value = built(current)
    assert value.parent == expected
    assert value.dynamic is None
    assert "volume_missing" in {issue.reason for issue in value.issues}
    assert ed.flatten_h1_features(value) is None
    assert ed.h1_rule_decision(value, without_filing=True) == "unknown"


def test_availability_is_checked_at_first_eligible_grid_and_preserved():
    current = change(session(), 0, available_at=OPEN + 31 * ed.MINUTE)
    found = parent(current)
    assert found.preceding[0].available_at == OPEN + 31 * ed.MINUTE
    late = change(current, 29, available_at=OPEN + 33 * ed.MINUTE)
    with pytest.raises(ed.InputRefusal, match="minute_unavailable_at_decision"):
        parent(late)


def test_later_valid_parent_cannot_be_substituted():
    current = session()
    original = parent(current)
    trigger = replace(original.trigger, start_at=OPEN + 30 * ed.MINUTE,
                      end_at=OPEN + 35 * ed.MINUTE, available_at=OPEN + 37 * ed.MINUTE)
    names, other = peers()
    expected, prior = references(current)
    with pytest.raises(ed.InputRefusal, match="not_the_first_causal_parent"):
        ed.build_h1_window_inputs(current, replace(original, trigger=trigger), original_peers=names,
                                  peers=other, expected=expected, references=prior,
                                  opening_context=context(current))


def test_actual_zero_range_and_volume_are_observed_tagged_values():
    current = change(session(), 0, ohlc=(D("100"),) * 4, shares=D("0"))
    value = built(current)
    assert (len(value.dynamic), len(value.dynamic[0])) == (30, 6)
    assert value.dynamic[0][1:5] == (Fraction(0), Fraction(0), Fraction(1), Fraction(0))
    assert ed.flatten_h1_features(value)[2:5] == (0.0, 1.0, 0.0)
    missing = change(current, 0, shares=None, volume_state="missing")
    assert built(missing).dynamic is None
    assert built(missing).parent == value.parent


@pytest.mark.parametrize("fields", [
    {"ohlc": (D("100"), D("99"), D("98"), D("100"))},
    {"ohlc": (100.0,) * 4}, {"shares": D("NaN")}, {"shares": D("-1")},
    {"shares": 0.0}, {"shares": D("0"), "volume_state": "missing"},
])
def test_invalid_exact_values_are_integrity_failures(fields):
    with pytest.raises(ed.InputRefusal):
        ed.window(change(session(), 0, **fields), 0, 1, OPEN + 32 * ed.MINUTE, volume=True)


@pytest.mark.parametrize("state", ["missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"])
def test_distinct_unknown_volume_states_are_preserved(state):
    value = built(change(session(), 0, shares=None, volume_state=state))
    assert value.dynamic is None
    assert value.issues[0].reason == f"volume_{state}"


def test_decimal_context_cannot_round_source_ratios_medians_or_variance():
    current = change(session(), 0, shares=D("1.234567890123456789"))
    with localcontext() as decimal_context:
        decimal_context.prec = 3
        value = built(current)
    assert value.dynamic[0][4] == Fraction(D("1.234567890123456789")) / Fraction(21, 2)
    scalars = dict(value.static_exact)
    assert scalars["target_30m_return"] == Fraction(1, 50)
    assert scalars["last5_share_volume_ratio"] == Fraction(20, 21)
    assert scalars["past20_sameclock5_share_volume_median"] == Fraction(105, 2)
    assert scalars["fraction_of_session_elapsed"] == Fraction(32, 390)
    assert value.past20_return_variance == 0


def test_expected_twenty_calendar_is_never_twenty_successful_sessions():
    current = session()
    expected, prior = references(current, increasing=True)
    result = ed.volume_baseline(current, expected, prior, offset=25, count=5, decision_at=OPEN + 32 * ed.MINUTE)
    assert result.median == Fraction(105, 2)
    assert result.totals == tuple(Fraction(index * 5) for index in range(1, 21))
    del prior[expected[0]]
    missing = ed.volume_baseline(current, expected, prior, offset=25, count=5, decision_at=OPEN + 32 * ed.MINUTE)
    assert missing.totals[0] is None
    assert missing.median is None
    extra = expected[0] - timedelta(days=1)
    prior[extra] = session(opening=extra, break_end=0)
    with pytest.raises(ed.InputRefusal, match="reference_outside_expected_calendar"):
        built(current, expected=expected, references=prior)


def test_prior_missing_and_zero_denominator_remain_different():
    current = session()
    expected, prior = references(current)
    prior[expected[0]] = change(prior[expected[0]], 28, shares=None, volume_state="missing")
    missing = ed.volume_baseline(current, expected, prior, offset=25, count=5, decision_at=OPEN + 32 * ed.MINUTE)
    assert missing.issues[0].reason == "volume_missing"
    zeros = {opening: session(opening=opening, break_end=0, shares=D("0")) for opening in expected}
    result = ed.volume_baseline(current, expected, zeros, offset=25, count=5, decision_at=OPEN + 32 * ed.MINUTE)
    assert result.totals == (Fraction(0),) * 20
    assert result.median is None
    assert result.issues[0].reason == "nonpositive_volume_denominator"
    assert built(current, expected=expected, references=zeros).dynamic is None


def test_future_and_unavailable_reference_inputs_are_refused():
    current = session()
    expected, prior = references(current)
    prior[expected[-1]] = change(prior[expected[-1]], 0, available_at=OPEN + 33 * ed.MINUTE)
    with pytest.raises(ed.InputRefusal, match="minute_unavailable_at_decision"):
        built(current, expected=expected, references=prior)
    wrong = expected[:-1] + (OPEN,)
    with pytest.raises(ed.InputRefusal, match="invalid_expected_prior_calendar"):
        built(current, expected=wrong, references={})
    with pytest.raises(ed.InputRefusal, match="twenty_expected_sessions_required"):
        built(current, expected=expected[:-1], references={})


def test_late_prior_opening_uses_its_own_session_relative_minute():
    current = session()
    expected, prior = references(current)
    old = expected[0]
    late = old + 60 * ed.MINUTE
    expected = (late,) + expected[1:]
    del prior[old]
    prior[late] = session(opening=late, break_end=0)
    result = ed.volume_baseline(current, expected, prior, offset=25, count=5, decision_at=OPEN + 32 * ed.MINUTE)
    assert result.median == 50


def test_one_common_five_peer_set_supplies_every_channel_and_both_windows():
    current = session()
    names, other = peers(count=6, close=D("110"))
    excluded = change(other["PEER5"], 0, ohlc=None, price_state="missing")
    other["PEER5"] = change(excluded, 29, ohlc=(D("100"), D("1000"), D("99"), D("1000")))
    result = ed.peer_context(current, parent(current), names, other)
    assert result.common_peers == names[:5]
    assert (result.minute_returns[0], result.minute_returns[-1], result.return30, result.last15_return) == (Fraction(1, 10),) * 4
    value = built(current, original_peers=names, peers=other)
    assert dict(value.static_exact)["common_peer_count_over_original_peer_count"] == Fraction(5, 6)
    assert ed.flatten_h1_features(value) is not None


def test_four_peers_are_unknown_and_target_cannot_replace_fifth():
    current = session()
    names, other = peers()
    other[names[-1]] = None
    assert built(current, original_peers=names, peers=other).dynamic is None
    with pytest.raises(ed.InputRefusal, match="invalid_original_peers"):
        ed.peer_context(current, parent(current), tuple(sorted(names + (current.symbol,))), other)


def test_unavailable_peer_is_integrity_failure():
    current = session()
    names, other = peers()
    other[names[0]] = change(other[names[0]], 29, available_at=OPEN + 33 * ed.MINUTE)
    with pytest.raises(ed.InputRefusal, match="minute_unavailable_at_decision"):
        built(current, original_peers=names, peers=other)


def test_future_price_volume_and_filing_changes_cannot_change_earlier_features():
    current = session()
    original = built(current)
    current = change(current, 30, ohlc=(D("NaN"),) * 4, shares=D("NaN"))
    assert built(current) == original
    late_filing = replace(context(current), available_at=OPEN + 33 * ed.MINUTE)
    with pytest.raises(ed.InputRefusal, match="context_not_available_at_opening"):
        built(current, opening_context=late_filing)


def test_all_206_feature_names_and_values_have_one_shared_frozen_order():
    current = session()
    expected, prior = references(current)
    for index, opening in enumerate(expected):
        if index >= 10:
            prior[opening] = change(prior[opening], 29, ohlc=(D("100"), D("110"), D("99"), D("110")))
    value = built(current, expected=expected, references=prior)
    flat = ed.flatten_h1_features(value)
    assert value.past20_return_variance == Fraction(1, 400)
    assert len(value.static_exact) == 10
    assert len(ed.FEATURE_NAMES) == len(set(ed.FEATURE_NAMES)) == len(flat) == 206
    assert ed.FEATURE_NAMES[:6] == tuple(f"t00__{name}" for name in ed.DYNAMIC_CHANNELS)
    assert ed.FEATURE_NAMES[174:180] == tuple(f"t29__{name}" for name in ed.DYNAMIC_CHANNELS)
    assert ed.FEATURE_NAMES[180:191] == ed.STATIC_NUMERIC_NAMES
    assert ed.FEATURE_NAMES[191:195] == tuple(f"event_type__{name}" for name in ed.EVENT_TYPE_NAMES)
    assert ed.FEATURE_NAMES[195:] == tuple(f"event_age__{name}" for name in ed.EVENT_AGE_NAMES)
    assert flat[186] == 0.05  # sqrt(1/400), not the variance payload.
    assert flat[:180] == tuple(float(number) for row in value.dynamic for number in row)
    assert all(type(number) is float and math.isfinite(number) for number in flat)
    assert dict(value.static_exact)["last5_share_volume_ratio"] == 1


def test_zero_variance_is_observed_and_float64_overflow_is_refused():
    value = built(session())
    assert ed.flatten_h1_features(value)[186] == 0.0
    with pytest.raises(ed.InputRefusal, match="nonfinite_float64"):
        ed.flatten_h1_features(replace(value, past20_return_variance=Fraction(10 ** 1000)))
    with pytest.raises(ed.InputRefusal, match="invalid_exact_variance"):
        ed.flatten_h1_features(replace(value, past20_return_variance=Fraction(-1)))
    malformed = ((D("1"),) + value.dynamic[0][1:],) + value.dynamic[1:]
    with pytest.raises(ed.InputRefusal, match="exact_fraction_required_at_numeric_boundary"):
        ed.flatten_h1_features(replace(value, dynamic=malformed))


def test_known_no_event_has_explicit_undefined_age_and_only_full_rule_declines():
    current = session(shares=D("20"))
    value = built(current, opening_context=context(current, state="KNOWN_NO_WHITELISTED_LIST_EVENT"))
    assert value.event_age_minutes is None
    assert value.event_type_bits == (1, 0, 0, 0)
    assert value.event_age_bits == (1,) + (0,) * 10
    assert ed.flatten_h1_features(value)[191:] == (1.0, 0.0, 0.0, 0.0, 1.0) + (0.0,) * 10
    assert ed.h1_rule_decision(value) == "declined"
    assert ed.h1_rule_decision(value, without_filing=True) == "accepted"


def test_unknown_context_cannot_be_zero_imputed_even_in_information_removed_rule():
    current = session(shares=D("20"))
    value = built(current, opening_context=context(current, state="UNKNOWN"))
    assert value.dynamic is not None  # Preserve independently observed price channels.
    assert value.event_type_bits is value.event_age_bits is value.event_age_minutes is None
    assert ed.flatten_h1_features(value) is None
    assert ed.h1_rule_decision(value) == ed.h1_rule_decision(value, without_filing=True) == "unknown"


def test_known_fixed_vocabulary_unseen_in_training_is_observed_input():
    current = session(shares=D("20"))
    annual = built(current)
    quarter = built(current, opening_context=context(current, types=("ORIGINAL_QUARTER",)))
    assert ed.flatten_h1_features(annual) is not None
    assert ed.flatten_h1_features(quarter)[191:195] == (0.0, 0.0, 0.0, 1.0)
    assert quarter.event_age_minutes == 32
    assert quarter.event_age_bits == (0, 1) + (0,) * 9
    assert ed.h1_rule_decision(quarter) == "accepted"
    multiple = built(current, opening_context=context(current, types=("ORIGINAL_ANNUAL", "ORIGINAL_QUARTER")))
    assert multiple.event_type_bits == (0, 1, 0, 1)


@pytest.mark.parametrize("end,age,bucket", [(55, 57, 1), (60, 62, 2)])
def test_event_age_uses_decision_clock_and_frozen_half_hour_bucket(end, age, bucket):
    value = built(session(break_end=end))
    assert value.event_age_minutes == age
    assert value.event_age_bits == tuple(Fraction(int(index == bucket)) for index in range(11))
    assert sum(ed.flatten_h1_features(value)[195:]) == 1.0


@pytest.mark.parametrize("updates", [
    {"available_at": OPEN + ed.MINUTE}, {"event_opening": OPEN - timedelta(days=1)},
    {"original_types": ("OTHER",)}, {"receipt_ids": ()},
    {"opening": OPEN + ed.MINUTE}, {"state": "UNKNOWN"},
])
def test_caller_opening_judgment_is_typed_frozen_and_identity_checked(updates):
    current = session()
    with pytest.raises(ed.InputRefusal):
        built(current, opening_context=replace(context(current), **updates))


def test_rules_use_strict_exact_thresholds_without_float_sign_loss():
    current = session(shares=D("20"))
    assert ed.h1_rule_decision(built(current)) == "accepted"
    assert ed.h1_rule_decision(built(session(shares=D("10.5")))) == "declined"  # ratio exactly1.
    names, other = peers(close=D("102"))
    assert ed.h1_rule_decision(built(current, original_peers=names, peers=other)) == "declined"  # strength0.
    # Decimal source difference much smaller than float64 absolute resolution
    # still has an exact positive sign after target-minus-peer subtraction.
    names, other = peers(close=D("101.999999999999999999999999999999"))
    value = built(current, original_peers=names, peers=other)
    assert dict(value.static_exact)["target_minus_peer_30m_return"] > 0
    assert ed.h1_rule_decision(value) == "accepted"


def test_naive_clocks_and_static_reordering_are_rejected():
    current = session()
    with pytest.raises(ed.InputRefusal, match="aware_clock_required"):
        ed.first_h1_parent(current, through=OPEN.replace(tzinfo=None))
    value = built(current)
    with pytest.raises(ed.InputRefusal, match="static_numeric_order_changed"):
        ed.flatten_h1_features(replace(value, static_exact=value.static_exact[::-1]))


def test_module_body_has_no_io_model_or_private_prototype_dependency():
    source = Path(ed.__file__)
    code = compile(source.read_text(encoding="utf-8"), str(source), "exec")
    spec = importlib.util.spec_from_file_location("ed_inputs_purity_clone", source)
    clone = importlib.util.module_from_spec(spec)
    original_import = __import__

    def allowed_import(name, *args, **kwargs):
        assert name in {"__future__", "collections.abc", "dataclasses", "datetime", "decimal", "fractions",
                        "math", "typing", "research.intraday_pattern_events"}
        return original_import(name, *args, **kwargs)

    with patch.dict(sys.modules, {spec.name: clone}), \
         patch("builtins.__import__", side_effect=allowed_import), \
         patch("builtins.open", side_effect=AssertionError("open")), \
         patch.object(Path, "open", side_effect=AssertionError("Path.open")), \
         patch.object(Path, "resolve", side_effect=AssertionError("Path.resolve")), \
         patch.object(os, "open", side_effect=AssertionError("os.open")), \
         patch.object(subprocess, "run", side_effect=AssertionError("process")):
        exec(code, clone.__dict__)
    assert clone.Bar is Bar
