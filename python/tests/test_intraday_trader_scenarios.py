"""Invented-only tests for the registered EE candidate and information boundary."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from research import intraday_pattern_events as core
from research import intraday_trader_scenarios as ee


OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
CLOSE = OPEN + timedelta(minutes=390)
D = Decimal
SYMBOL = "KRX:ABC123"


def bar(n, *, cl="100", hi="110", lo="90", op=None):
    end = OPEN + n * ee.FIVE
    return core.Bar(end - ee.FIVE, end, end + ee.LAG,
                    D(op if op is not None else cl), D(hi), D(lo), D(cl))


def opening():
    return [bar(n, cl="106" if n == 3 else "108" if n == 6 else "100")
            for n in range(1, 7)]


def a_path():
    return opening() + [
        bar(7, cl="116", hi="120", lo="111"),
        bar(8, cl="114", hi="118", lo="112"),
        bar(9, cl="115", hi="117", lo="113"),
        bar(10, cl="118", hi="119", lo="114"),
    ]


def b_path():
    return opening() + [bar(7, cl="88", hi="150", lo="80")] + [
        bar(n, cl="91", hi="95", lo="82") for n in range(8, 12)
    ] + [bar(12, cl="96", hi="97", lo="83")]


def c_path():
    return opening() + [bar(7, cl="111", hi="112", lo="99")]


def facts():
    available = OPEN - timedelta(days=3)
    return (
        ee.ProfitFact(SYMBOL, "original-current", "invented-source", 2025, 3,
                      D("100"), "KRW", "million", "consolidated",
                      date(2025, 9, 30), available, True),
        ee.ProfitFact(SYMBOL, "original-prior", "invented-source", 2024, 3,
                      D("-10"), "KRW", "million", "consolidated",
                      date(2024, 9, 30), available, True),
    )


def run(scenario, bars, **kwargs):
    return ee.detect_bars(scenario, bars, symbol=SYMBOL, opening=OPEN,
                          closing=CLOSE, **kwargs)


def previous(family="EE-A", *, terminal_n=10, target="120"):
    return ee.PreviousTerminal(family, SYMBOL, OPEN, 1, OPEN + 7 * ee.FIVE,
                              OPEN + terminal_n * ee.FIVE + ee.LAG, "closed", D(target))


def kinds(result):
    return [transition.kind for transition in result.trace]


def test_a_exact_first_pullback_higher_low_and_later_confirmation():
    result = run("EE-A", a_path())
    assert result.state == "candidate"
    candidate = result.candidate
    assert candidate.parent == a_path()[6]
    assert candidate.signal == a_path()[9]
    assert candidate.available_at == OPEN + timedelta(minutes=52)
    assert candidate.stop == D("112")
    assert candidate.target == D("120")
    assert candidate.key_level == D("110")
    assert candidate.context_closes == (D("106"), D("108"))
    assert candidate.eligible is True
    assert candidate.attempt_number == 1
    assert candidate.previous_terminal is None
    assert result.original_parent_end_at == OPEN + timedelta(minutes=35)
    assert kinds(result) == ["opening_range_frozen", "parent", "pullback",
                            "impulse_target_frozen", "higher_low", "confirmation"]
    assert [item.bar_end_at for item in result.trace[1:]] == [
        OPEN + n * ee.FIVE for n in (7, 8, 8, 9, 10)]
    with pytest.raises(FrozenInstanceError):
        candidate.stop = D("1")


def test_a_latest_completed_15m_blocks_are_frozen_at_parent():
    bars = opening() + [bar(7), bar(8), bar(9, cl="109"),
        bar(10, cl="116", hi="120", lo="111"),
        bar(11, cl="114", hi="118", lo="112"),
        bar(12, cl="115", hi="117", lo="113"),
        bar(13, cl="118", hi="119", lo="114")]
    actual = run("EE-A", bars).candidate
    assert actual.context_closes == (D("108"), D("109"))
    assert actual.information.state == "pass"


def test_a_parent_on_15m_boundary_includes_that_block_but_not_later_blocks():
    bars = opening() + [bar(7), bar(8),
        bar(9, cl="116", hi="120", lo="111"),
        bar(10, cl="114", hi="118", lo="112"),
        bar(11, cl="115", hi="117", lo="113"),
        bar(12, cl="118", hi="119", lo="114")]
    assert run("EE-A", bars).candidate.context_closes == (D("108"), D("116"))


def test_a_context_decline_and_control_share_the_exact_price_candidate():
    bars = a_path()
    bars[2] = bar(3, cl="108")
    selected = run("EE-A", bars).candidate
    control = run("EE-A-WITHOUT-15M-CONTEXT", bars).candidate
    assert selected.information.state == control.information.state == "decline"
    assert selected.eligible is False
    assert control.eligible is True
    assert (selected.parent, selected.signal, selected.stop, selected.target) == (
        control.parent, control.signal, control.stop, control.target)


def test_a_rising_15m_closes_still_decline_when_latest_does_not_exceed_session_open():
    bars = a_path()
    bars[2] = bar(3, cl="95")
    bars[5] = bar(6, cl="99")
    selected = run("EE-A", bars).candidate
    control = run("EE-A-WITHOUT-15M-CONTEXT", bars).candidate
    assert bars[0].open == D("100")
    assert selected.context_closes == control.context_closes == (D("95"), D("99"))
    assert selected.information.state == control.information.state == "decline"
    assert selected.eligible is False
    assert control.eligible is True
    for candidate in (selected, control):
        assert candidate.parent == bars[6]
        assert candidate.parent.end_at == OPEN + timedelta(minutes=35)
        assert candidate.signal == bars[9]
        assert candidate.signal.end_at == OPEN + timedelta(minutes=50)
        assert candidate.available_at == OPEN + timedelta(minutes=52)
        assert candidate.stop == D("112")
        assert candidate.target == D("120")


def test_a_first_higher_low_is_strict_and_its_high_never_moves():
    bars = a_path()[:8] + [
        bar(9, cl="115", hi="119", lo="112"),  # low equality: not higher-low
        bar(10, cl="114", hi="119", lo="113"),  # close equality: not higher-low
        bar(11, cl="115", hi="118", lo="113"),
        bar(12, cl="116", hi="117", lo="114"),  # later lower high must not replace118
        bar(13, cl="118", hi="120", lo="114"),  # equal close is not a break
        bar(14, cl="119", hi="120", lo="114"),
    ]
    result = run("EE-A", bars)
    assert result.state == "candidate"
    assert result.candidate.signal == bars[-1]
    assert [t.bar_end_at for t in result.trace if t.kind == "higher_low"] == [OPEN + 11 * ee.FIVE]


@pytest.mark.parametrize("change,reason", [
    (dict(cl="110", hi="118", lo="109"), "key_level_lost"),
    (dict(cl="115", hi="117", lo="111"), "pullback_low_lost"),
])
def test_a_structural_cancellation_cannot_restart_or_widen_stop(change, reason):
    bars = a_path()
    bars[8] = bar(9, **change)
    actual = run("EE-A", bars)
    assert actual.state == "known_no_candidate"
    assert actual.reason == reason
    assert actual.observed_through == OPEN + 9 * ee.FIVE


def test_a_cancel_on_first_pullback_at_key_and_impulse_includes_pullback_high():
    bars = a_path()
    bars[7] = bar(8, cl="110", hi="130", lo="109")
    assert run("EE-A", bars).reason == "key_level_lost"
    bars[7] = bar(8, cl="114", hi="130", lo="112")
    assert run("EE-A", bars).candidate.target == D("130")


def test_a_sixth_bar_can_confirm_but_seventh_cannot_revive_expired_attempt():
    bars = a_path()[:9] + [bar(n, cl="115", hi="117", lo="113") for n in range(10, 15)]
    bars[-1] = bar(14, cl="118", hi="119", lo="114")
    assert run("EE-A", bars).state == "candidate"
    bars[-1] = bar(14, cl="117", hi="119", lo="114")
    bars.append(bar(15, cl="118", hi="119", lo="114"))
    result = run("EE-A", bars)
    assert result.reason == "confirmation_timeout"
    assert result.observed_through == OPEN + 14 * ee.FIVE


def test_b_excludes_wide_parent_from_four_bar_base_and_has_three_confirmations():
    result = run("EE-B", b_path())
    assert result.state == "candidate"
    assert result.candidate.stop == D("80")
    assert result.candidate.target == D("110")
    assert result.candidate.signal == b_path()[-1]
    assert [t.bar_end_at for t in result.trace if t.kind == "base_bar"] == [
        OPEN + n * ee.FIVE for n in range(8, 12)]
    assert next(t.level for t in result.trace if t.kind == "base_high_frozen") == D("95")


def test_b_early_reclaim_control_has_same_parent_and_different_clock():
    selected = run("EE-B", b_path()).candidate
    control = run("EE-B-EARLY-RECLAIM", b_path()).candidate
    assert selected.parent == control.parent
    assert selected.original_parent_end_at == control.original_parent_end_at
    assert (selected.stop, selected.target) == (control.stop, control.target)
    assert control.signal == b_path()[7]
    assert control.available_at < selected.available_at


@pytest.mark.parametrize("scenario", ["EE-B", "EE-B-EARLY-RECLAIM"])
def test_b_post_parent_new_low_cancels_before_a_same_bar_reclaim(scenario):
    bars = b_path()
    bars[7] = bar(8, cl="96", hi="100", lo="79")
    result = run(scenario, bars)
    assert result.reason == "parent_low_lost"
    assert result.state == "known_no_candidate"


def test_b_base_equal_parent_low_is_valid_but_close_equal_base_high_is_not_break():
    bars = b_path()
    bars[7] = bar(8, cl="91", hi="95", lo="80")
    bars[-1] = bar(12, cl="95", hi="96", lo="83")
    bars += [bar(13, cl="95", hi="96", lo="83"), bar(14, cl="96", hi="97", lo="83")]
    assert run("EE-B", bars).candidate.signal == bars[-1]
    bars[-1] = bar(14, cl="95", hi="96", lo="83")
    assert run("EE-B", bars).reason == "confirmation_timeout"


def test_b_confirmation_must_clear_key_even_if_base_high_is_lower():
    bars = opening() + [bar(7, cl="88", hi="100", lo="80")] + [
        bar(n, cl="85", hi="88", lo="82") for n in range(8, 12)
    ] + [bar(12, cl="90", hi="91", lo="83"), bar(13, cl="91", hi="92", lo="83")]
    assert run("EE-B", bars).candidate.signal == bars[-1]


def test_c_same_parent_price_target_and_original_profit_predicate():
    selected = run("EE-C", c_path(), profit_facts=facts(), event_session_admitted=True).candidate
    control = run("EE-C-WITHOUT-PROFIT-CONTENT", c_path(),
                  profit_facts=facts(), event_session_admitted=True).candidate
    assert selected.signal == selected.parent == control.parent
    assert selected.stop == control.stop == D("90")
    assert selected.target == control.target == D("153")
    assert selected.eligible is control.eligible is True
    assert selected.profit_facts == facts()


@pytest.mark.parametrize("current,prior,passes", [("1", "0", True), ("0", "-1", False),
                                                  ("-1", "-2", False), ("1", "1", False)])
def test_c_comparable_negative_profit_is_decline_and_control_removes_only_sign(current, prior, passes):
    pair = (replace(facts()[0], value=D(current)), replace(facts()[1], value=D(prior)))
    selected = run("EE-C", c_path(), profit_facts=pair, event_session_admitted=True).candidate
    control = run("EE-C-WITHOUT-PROFIT-CONTENT", c_path(),
                  profit_facts=pair, event_session_admitted=True).candidate
    assert selected.eligible is passes
    assert selected.information.state == ("pass" if passes else "decline")
    assert control.eligible is True


@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
@pytest.mark.parametrize("pair", [None, (None, None), (facts()[0], None)])
def test_c_missing_facts_remain_unknown_for_both_arms(scenario, pair):
    result = run(scenario, c_path(), profit_facts=pair, event_session_admitted=True)
    assert result.state == "candidate"
    assert result.candidate.eligible is None
    assert result.candidate.information.reason == "missing_profit_facts"
    assert result.candidate.target == D("153")


@pytest.mark.parametrize("admitted", [None, False])
@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
def test_c_common_event_session_cannot_be_removed_by_control(admitted, scenario):
    candidate = run(scenario, c_path(), profit_facts=facts(),
                    event_session_admitted=admitted).candidate
    assert candidate.eligible is admitted


@pytest.mark.parametrize("change,reason", [
    (dict(symbol="OTHER"), "invalid_profit_identity_or_provenance"),
    (dict(receipt_id=""), "invalid_profit_identity_or_provenance"),
    (dict(source_id=""), "invalid_profit_identity_or_provenance"),
    (dict(is_original=False), "invalid_profit_identity_or_provenance"),
    (dict(fiscal_year=2024), "incomparable_profit_period"),
    (dict(fiscal_quarter=2), "incomparable_profit_period"),
    (dict(fiscal_quarter=0), "invalid_profit_period"),
    (dict(unit="won"), "incomparable_profit_basis"),
    (dict(currency="USD"), "incomparable_profit_basis"),
    (dict(consolidation_basis="separate"), "incomparable_profit_basis"),
    (dict(available_at=OPEN + timedelta(minutes=38)), "profit_future_availability"),
    (dict(observation_end=date(2027, 1, 1)), "invalid_profit_observation_clock"),
    (dict(receipt_id="original-prior"), "profit_originals_not_independently_identified"),
    (dict(value=D("NaN")), "invalid_profit_value"),
])
@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
def test_c_malformed_future_or_incomparable_facts_refuse_both_arms(change, reason, scenario):
    pair = (replace(facts()[0], **change), facts()[1])
    result = run(scenario, c_path(), profit_facts=pair, event_session_admitted=True)
    assert result.state == "invalid"
    assert result.reason == reason
    assert result.parent == c_path()[-1]
    assert result.candidate is None


def test_c_decision_equality_is_admitted_but_no_facts_are_used_before_price_parent():
    pair = (replace(facts()[0], available_at=c_path()[-1].available_at), facts()[1])
    candidate = run("EE-C", c_path(), profit_facts=pair).candidate
    assert candidate.information.state == "pass"
    assert candidate.eligible is None
    pair = (replace(facts()[0], available_at=OPEN), replace(facts()[1], available_at=OPEN))
    assert run("EE-C", c_path(), profit_facts=pair, event_session_admitted=True).candidate.eligible is True
    assert run("EE-C", opening(), profit_facts=(object(), object())).state == "unknown"


def test_c_exact_2r_target_does_not_round_in_tiny_decimal_context():
    bars = c_path()
    bars[-1] = bar(7, cl="111.123456789012345678901234567890", hi="112", lo="99")
    with localcontext() as context:
        context.prec = 2
        candidate = run("EE-C", bars, profit_facts=facts(), event_session_admitted=True).candidate
    assert candidate.target == D("153.370370367037037036703703703670")


@pytest.mark.parametrize("scenario,fixture", [("EE-A", a_path), ("EE-B", b_path),
                                             ("EE-B-EARLY-RECLAIM", b_path), ("EE-C", c_path)])
def test_terminal_event_never_consumes_any_suffix_or_future_profit_fact(scenario, fixture):
    def stream():
        yield from fixture()
        raise AssertionError("consumed data after completed event")
    assert run(scenario, stream()).state == "candidate"
    assert run(scenario, fixture() + [object()]).state == "candidate"


@pytest.mark.parametrize("scenario", ["EE-A", "EE-B", "EE-C"])
def test_full_prefix_proves_no_parent_and_cutoff_does_not_pull_unavailable_bar(scenario):
    def stream():
        yield from [bar(n) for n in range(1, 48)]
        raise AssertionError("end240 plus lag2 is beyond decision cutoff240")
    result = run(scenario, stream())
    assert result.state == "known_no_candidate"
    assert result.reason == "decision_cutoff"
    assert result.decision_at == OPEN + timedelta(minutes=240)
    assert result.observed_through == OPEN + timedelta(minutes=235)


@pytest.mark.parametrize("scenario", ["EE-A", "EE-B", "EE-C"])
def test_missing_and_gapped_prefix_are_unknown_not_known_absence(scenario):
    assert run(scenario, opening()[:5]).reason == "missing_prefix"
    result = run(scenario, [bar(1), bar(3)])
    assert result.state == "unknown"
    assert result.reason == "noncontiguous_prefix"
    assert result.decision_at == OPEN + timedelta(minutes=12)
    assert result.observed_through == OPEN + timedelta(minutes=5)


@pytest.mark.parametrize("bad", [replace(bar(1), available_at=bar(1).end_at),
                                  replace(bar(1), close=D("NaN")),
                                  replace(bar(1), high=D("80")), object()])
def test_invalid_consumed_source_is_an_explicit_refusal(bad):
    assert run("EE-A", [bad]).state == "invalid"


def test_duplicate_bar_is_invalid_and_unusable_invocation_raises_before_consumption():
    assert run("EE-A", [bar(1), bar(1)]).reason == "invalid_source_sequence"
    def stream():
        raise AssertionError("invalid invocation consumed bars")
        yield
    with pytest.raises(core.PatternValidationError, match="invalid_scenario"):
        run("UNREGISTERED", stream())
    with pytest.raises(core.PatternValidationError, match="timezone-aware"):
        ee.detect_bars("EE-A", stream(), symbol=SYMBOL, opening=OPEN.replace(tzinfo=None), closing=CLOSE)


def test_equal_close_and_high_only_are_not_new_parents():
    a = opening() + [bar(7, cl="110", hi="130"), bar(8, cl="109", hi="140")]
    b = opening() + [bar(7, cl="90", lo="70")]
    c = opening() + [bar(7, cl="110", hi="130")]
    for scenario, bars in [("EE-A", a), ("EE-B", b), ("EE-C", c)]:
        result = run(scenario, bars)
        assert result.state == "unknown"
        assert result.parent is None


def test_a_reentry_requires_fresh_cross_after_terminal_and_retains_original_parent():
    bars = a_path() + [
        bar(11, cl="119", hi="120", lo="118"),
        bar(12, cl="121", hi="130", lo="119"),
        bar(13, cl="123", hi="126", lo="122"),
        bar(14, cl="122", hi="125", lo="121"),
        bar(15, cl="123", hi="124", lo="122"),
        bar(16, cl="125", hi="126", lo="122"),
    ]
    result = run("EE-A", bars, previous=previous())
    candidate = result.candidate
    assert candidate.parent == bars[11]
    assert candidate.original_parent_end_at == bars[6].end_at
    assert candidate.attempt_number == 2
    assert candidate.previous_terminal == previous()
    assert candidate.stop == D("121")
    assert candidate.target == D("130")


def test_a_staying_above_previous_target_is_not_a_reset_and_comparison_can_precede_terminal():
    bars = opening() + [bar(n, cl="121", hi="130", lo="119") for n in range(7, 13)]
    assert run("EE-A", bars, previous=previous()).parent is None
    # A terminal between old and new completion permits using the older close as comparator.
    prior = replace(previous(), terminal_at=OPEN + timedelta(minutes=56))
    bars[10] = bar(11, cl="120", hi="130", lo="119")
    assert run("EE-A", bars, previous=prior).parent == bars[11]


def test_b_reentry_requires_actual_downward_cross_after_terminal():
    bars = b_path() + [bar(13, cl="88", hi="94", lo="81")] + [
        bar(n, cl="89", hi="94", lo="82") for n in range(14, 18)
    ] + [bar(18, cl="96", hi="97", lo="83")]
    result = run("EE-B", bars, previous=previous("EE-B", terminal_n=12))
    assert result.candidate.parent == bars[12]
    assert result.candidate.original_parent_end_at == bars[6].end_at
    assert result.candidate.attempt_number == 2
    bars[11] = bar(12, cl="88", hi="94", lo="82")
    assert run("EE-B", bars, previous=previous("EE-B", terminal_n=12)).parent is None


@pytest.mark.parametrize("family", ["EE-A", "EE-B"])
def test_new_cross_whose_completion_equals_terminal_is_not_eligible(family):
    bars = a_path() if family == "EE-A" else b_path()
    if family == "EE-A":
        bars += [bar(11, cl="121", hi="130", lo="119")]
    else:
        bars[-1] = bar(12, cl="88", hi="94", lo="82")
    prior = replace(previous(family), terminal_at=bars[-1].end_at)
    assert run(family, bars, previous=prior).parent is None


@pytest.mark.parametrize("change", [dict(attempt_number=2), dict(symbol="OTHER"),
                                     dict(frozen_target=None), dict(kind="open")])
def test_invalid_reentry_evidence_raises_before_any_data_consumption(change):
    with pytest.raises(core.PatternValidationError):
        run("EE-A", (), previous=replace(previous(), **change))


def test_c_has_no_automatic_reentry_and_cancelled_first_attempt_does_not_auto_restart():
    with pytest.raises(core.PatternValidationError, match="previous_terminal"):
        run("EE-C", c_path(), previous=previous())
    bars = a_path()
    bars[8] = bar(9, cl="110", hi="117", lo="109")
    bars += [bar(n, cl="121", hi="130", lo="119") for n in range(11, 15)]
    result = run("EE-A", bars)
    assert result.reason == "key_level_lost"
    assert result.attempt_number == 1


def test_irreversible_cancellation_does_not_consume_malformed_or_absent_suffix():
    def stream():
        yield from a_path()[:8]
        yield bar(9, cl="110", hi="117", lo="109")
        raise AssertionError("cancelled first attempt consumed suffix")
    assert run("EE-A", stream()).reason == "key_level_lost"


def test_c_uses_previous_five_not_opening_range_or_signal_bar_low():
    bars = opening() + [bar(7, cl="109", hi="110", lo="98"),
                        bar(8, cl="111", hi="112", lo="1")]
    bars[0] = bar(1, hi="200", lo="1")
    # Opening high200/low1 are outside the preceding five at the parent.
    result = run("EE-C", bars, profit_facts=facts(), event_session_admitted=True)
    assert result.candidate.parent == bars[-1]
    assert result.candidate.stop == D("90")
    assert result.candidate.target == D("153")


def test_a_pullback_low_equality_after_setup_does_not_cancel_or_widen_stop():
    bars = a_path()
    bars[-1] = bar(10, cl="118", hi="119", lo="112")
    result = run("EE-A", bars)
    assert result.state == "candidate"
    assert result.candidate.stop == D("112")


def test_a_parent_and_confirmation_stay_exact_under_small_decimal_context():
    bars = a_path()
    bars[6] = bar(7, cl="110.0000000000000000000000000001", hi="120", lo="110")
    bars[7] = bar(8, cl="110.00000000000000000000000000005", hi="118", lo="110")
    bars[8] = bar(9, cl="115", hi="117", lo="111")
    with localcontext() as context:
        context.prec = 2
        result = run("EE-A", bars)
    assert result.state == "candidate"
    assert result.candidate.stop == D("110")


@pytest.mark.parametrize("symbol", [" KRX:ABC123", "KRX:ABC123 ", "NASDAQ:ABC123",
                                    "ABC123", "KRX:abc123", "KRX:ABC12",
                                    "KRX:ABC1234", "KRX:ABC123\n"])
def test_invalid_symbol_raises_without_consuming_market_prefix(symbol):
    def stream():
        raise AssertionError("invalid symbol consumed market prefix")
        yield
    with pytest.raises(core.PatternValidationError, match="invalid_scenario_or_symbol"):
        ee.detect_bars("EE-C", stream(), symbol=symbol, opening=OPEN, closing=CLOSE,
                       profit_facts=facts(), event_session_admitted=True)


@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
@pytest.mark.parametrize("late_index", [0, 1])
def test_c_known_session_refuses_either_fact_first_available_after_opening(scenario, late_index):
    pair = list(facts())
    pair[late_index] = replace(pair[late_index], available_at=OPEN + timedelta(minutes=1))
    def stream():
        yield from c_path()
        raise AssertionError("invalid event admission consumed future market suffix")
    result = run(scenario, stream(), profit_facts=tuple(pair), event_session_admitted=True)
    assert result.state == "invalid"
    assert result.reason == "profit_unavailable_at_opening"
    assert result.parent == c_path()[-1]
    assert result.observed_through == result.parent.end_at


@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
@pytest.mark.parametrize("admitted", [None, False])
def test_c_unadmitted_or_unknown_session_keeps_intraday_facts_as_separate_context(scenario, admitted):
    pair = (replace(facts()[0], available_at=OPEN + timedelta(minutes=1)), facts()[1])
    result = run(scenario, c_path(), profit_facts=pair, event_session_admitted=admitted)
    assert result.state == "candidate"
    assert result.candidate.information.state == "pass"
    assert result.candidate.eligible is admitted


@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
@pytest.mark.parametrize("fact_index", [0, 1])
@pytest.mark.parametrize("year_offset", [-2, 2, 74])
def test_c_refuses_implausible_fiscal_year_on_either_original_fact(scenario, fact_index, year_offset):
    pair = list(facts())
    fact = pair[fact_index]
    pair[fact_index] = replace(fact, fiscal_year=fact.observation_end.year + year_offset)
    result = run(scenario, c_path(), profit_facts=tuple(pair), event_session_admitted=True)
    assert result.state == "invalid"
    assert result.reason == "implausible_profit_fiscal_year"
    assert result.parent == c_path()[-1]


@pytest.mark.parametrize("scenario", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
@pytest.mark.parametrize("year_offset", [-1, 1])
def test_c_allows_fiscal_year_within_one_year_without_calendar_quarter_mapping(scenario, year_offset):
    pair = tuple(replace(fact, fiscal_year=fact.observation_end.year + year_offset,
                         observation_end=date(fact.observation_end.year, 5, 17))
                 for fact in facts())
    result = run(scenario, c_path(), profit_facts=pair, event_session_admitted=True)
    assert result.state == "candidate"
    assert result.candidate.eligible is True
    assert result.candidate.profit_facts == pair
