"""Invented-only high-volatility windows, temporal baselines and censoring."""
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_highvol_events as events
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs


D = Decimal
OPEN = datetime(2026, 3, 2, 9, tzinfo=timezone(timedelta(hours=9)))
PRIOR = tuple(OPEN - timedelta(days=index) for index in range(20, 0, -1))


def session(*, opening=OPEN, count=300, scale=D(1), flat=False, shares=D(10)):
    rows = {}
    for index in range(count):
        at = opening + index * MINUTE
        op = D(100 if flat else 100 + index)
        close = op if flat else op + 1
        prices = tuple(value * scale for value in (op, close + 2, op - 1, close))
        rows[at] = Minute("TARGET", at, at + 3 * MINUTE, prices, shares)
    return SessionInputs("TARGET", opening, opening + 390 * MINUTE, rows)


def changed(value, index, **updates):
    at = value.opening + index * MINUTE
    rows = dict(value.minutes)
    rows[at] = replace(rows[at], **updates)
    return replace(value, minutes=rows)


def measured(value=None, offset=15):
    value = session() if value is None else value
    return events.measure_window(value, offset, value.opening + (offset + 2) * MINUTE)


def historical(metric, opening):
    delta = opening - metric.opening
    return replace(metric, opening=opening, decision_at=metric.decision_at + delta,
                   price_available_at=None if metric.price_available_at is None else metric.price_available_at + delta,
                   volume_available_at=None if metric.volume_available_at is None else metric.volume_available_at + delta,
                   price_issues=tuple(replace(item, minute=item.minute + delta) for item in metric.price_issues),
                   volume_issues=tuple(replace(item, minute=item.minute + delta) for item in metric.volume_issues))


def classified(metric=None, baseline=None, expected=PRIOR):
    metric = measured() if metric is None else metric
    baseline = measured(session(flat=True), metric.offset) if baseline is None else baseline
    return events.classify_window(metric, expected, {opening: historical(baseline, opening) for opening in expected})


class PrefixOnly(Mapping):
    def __init__(self, rows, first, last):
        self.rows, self.first, self.last = rows, first, last

    def __getitem__(self, key):
        assert self.first <= key < self.last, "unrequested minute accessed"
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("whole-session iteration")

    def __len__(self):
        raise AssertionError("whole-session length")


def test_first_hour_exact_anchored_energy_direction_and_volume():
    metric = measured()
    assert metric.first_hour and metric.price_status == "available"
    assert metric.energy == Fraction(15, 10000)
    assert metric.signed_move == Fraction(15, 100)
    assert metric.direction_efficiency == 1
    assert metric.relative_range == Fraction(18, 100)
    assert metric.shares == 150 and metric.zero_path is False
    assert metric.price_available_at == metric.volume_available_at == OPEN + 17 * MINUTE
    assert not metric.price_issues and not metric.volume_issues


def test_each_window_uses_its_own_first_open_not_previous_close_or_future():
    original = session()
    bounded = replace(original, minutes=PrefixOnly(original.minutes, OPEN + 5 * MINUTE, OPEN + 20 * MINUTE))
    metric = measured(bounded, 20)
    assert metric == measured(original, 20)
    assert metric.energy == Fraction(15, 105 * 105)
    # A suffix and a bar before the requested window cannot affect this result.
    altered = changed(changed(original, 20, ohlc=(D(1), D(900), D(1), D(800))), 4,
                      ohlc=(D(999),) * 4)
    assert measured(altered, 20) == metric


def test_price_scale_invariance_and_decimal_context_independence():
    from decimal import localcontext

    original = session()
    base = measured(original)
    scaled = measured(session(scale=D("1000000000000000000000000")))
    assert (scaled.energy, scaled.signed_move, scaled.direction_efficiency, scaled.relative_range) == (
        base.energy, base.signed_move, base.direction_efficiency, base.relative_range)
    with localcontext() as context:
        context.prec = 2
        assert measured(original) == base


def test_direction_efficiency_separates_back_and_forth_from_net_move():
    value = session(flat=True)
    for index in range(15):
        close = D(101 if index % 2 == 0 else 100)
        value = changed(value, index, ohlc=(D(100), D(102), D(99), close))
    metric = measured(value)
    assert metric.energy == Fraction(15, 10000)
    assert metric.signed_move == Fraction(1, 100)
    assert metric.direction_efficiency == Fraction(1, 15)


def test_zero_path_is_not_undefined_price_or_zero_volume():
    metric = measured(session(flat=True, shares=D(0)))
    assert metric.energy == metric.signed_move == metric.shares == 0
    assert metric.zero_path is True and metric.direction_efficiency is None
    assert metric.relative_range == Fraction(3, 100)  # Wicks are a separate measure.
    decision = classified(metric, metric)
    assert decision.status == "not_high" and decision.threshold_tie is True
    assert decision.baseline_zero is True


def test_optional_volume_gap_does_not_remove_price_candidate():
    metric = measured(changed(session(), 0, shares=None, volume_state="transport_error"))
    assert metric.price_status == "available" and metric.energy is not None
    assert metric.shares is metric.volume_available_at is None
    assert [issue.reason for issue in metric.volume_issues] == ["volume_transport_error"]
    assert classified(metric).status == "high"


def test_unknown_price_retains_independent_known_volume():
    metric = measured(changed(session(), 0, ohlc=None, price_state="halt"))
    assert metric.price_status == "unavailable" and metric.energy is None
    assert metric.shares == 150 and not metric.volume_issues
    decision = classified(metric)
    assert decision.status == "unavailable" and decision.reason == "current_price_unavailable"
    assert decision.threshold == 0 and decision.threshold_tie is None


def test_absent_minute_is_missing_for_both_channels():
    value = session()
    rows = dict(value.minutes)
    del rows[OPEN + 7 * MINUTE]
    metric = measured(replace(value, minutes=rows))
    assert metric.energy is metric.shares is None
    assert metric.price_issues == metric.volume_issues
    assert metric.price_issues[0].reason == "missing_minute"


@pytest.mark.parametrize("updates", [
    {"ohlc": (D(100), D(99), D(101), D(100))},
    {"ohlc": (D(0), D(101), D(0), D(100))},
    {"ohlc": (D("NaN"),) * 4},
    {"price_state": "halt"},
    {"shares": D(-1)},
    {"shares": D("NaN")},
    {"volume_state": "missing"},
    {"available_at": OPEN + 18 * MINUTE},
    {"available_at": OPEN + MINUTE},
])
def test_malformed_or_noncausal_observation_is_not_silently_missing(updates):
    with pytest.raises(InputRefusal):
        measured(changed(session(), 0, **updates))


@pytest.mark.parametrize("offset", [True, 0, 14, 16, 305])
def test_unregistered_offsets_refused(offset):
    with pytest.raises(InputRefusal):
        measured(offset=offset)


def test_changed_decision_clock_refused_and_short_session_retains_coordinate():
    value = session()
    with pytest.raises(InputRefusal, match="decision_clock"):
        events.measure_window(value, 15, OPEN + 18 * MINUTE)
    short = replace(value, closing=OPEN + 60 * MINUTE)
    metric = measured(short, 65)
    assert metric.price_status == "outside_clock" and metric.energy is metric.shares is None
    assert metric.price_issues[0].reason == "outside_session_clock"


def test_strict_q80_ties_are_not_selected():
    metric = measured()
    decision = classified(metric, metric)
    assert decision.threshold == metric.energy
    assert decision.threshold_tie is True and decision.baseline_zero is False
    assert decision.status == "not_high"
    higher = measured(changed(session(), 14, ohlc=(D(114), D(118), D(113), D(117))))
    assert classified(higher, metric).status == "high"


def test_q80_is_sixteenth_of_exactly_twenty_prior_windows():
    metric = measured()
    prior = {}
    for index, opening in enumerate(PRIOR):
        # Equal net endpoints with a varying first close create exact varied energy.
        value = changed(session(), 0, ohlc=(D(100), D(125), D(99), D(101 + index)))
        prior[opening] = historical(measured(value), opening)
    decision = events.classify_window(metric, PRIOR, prior)
    assert decision.threshold == sorted(item.energy for item in prior.values())[15]
    assert decision.known_history_count == 20
    assert decision.prior_energies == tuple(prior[opening].energy for opening in PRIOR)


def test_less_than_twenty_original_dates_stays_insufficient_even_when_complete():
    decision = classified(expected=PRIOR[1:])
    assert decision.status == "unavailable" and decision.reason == "insufficient_original_history"
    assert decision.known_history_count == 19 and decision.threshold is None
    empty = classified(expected=())
    assert empty.known_history_count == 0 and empty.reason == "insufficient_original_history"


def test_missing_prior_date_is_not_replaced_and_its_source_reason_survives():
    metric, baseline = measured(), measured(session(flat=True))
    prior = {opening: historical(baseline, opening) for opening in PRIOR[1:]}
    decision = events.classify_window(metric, PRIOR, prior,
                                     absent_reasons={PRIOR[0]: "outside_stored_population"})
    assert decision.status == "unavailable" and decision.reason == "prior_history_unavailable"
    assert decision.known_history_count == 19 and decision.prior_energies[0] is None
    assert decision.history_issues[0].reason == "outside_stored_population"
    older = PRIOR[0] - timedelta(days=1)
    prior[older] = historical(baseline, older)
    with pytest.raises(InputRefusal, match="outside_calendar"):
        events.classify_window(metric, PRIOR, prior)


def test_prior_price_gap_differs_from_prior_volume_gap():
    current = measured()
    missing_price = measured(changed(session(), 0, ohlc=None, price_state="missing"))
    missing_volume = measured(changed(session(), 0, shares=None, volume_state="missing"))
    assert classified(current, missing_price).reason == "prior_history_unavailable"
    assert classified(current, missing_volume).status == "not_high"


@pytest.mark.parametrize("expected", [PRIOR[::-1], PRIOR[:-1] + (PRIOR[-2],), PRIOR[1:] + (OPEN,)])
def test_bad_prior_calendar_refused(expected):
    with pytest.raises(InputRefusal, match="prior_calendar"):
        classified(expected=expected)


@pytest.mark.parametrize("change", [
    {"symbol": "OTHER"}, {"offset": 20}, {"decision_at": OPEN},
    {"price_available_at": OPEN + 20 * MINUTE}, {"energy": None},
])
def test_prior_identity_and_availability_are_validated(change):
    metric, baseline = measured(), measured(session(flat=True))
    prior = {opening: historical(baseline, opening) for opening in PRIOR}
    prior[PRIOR[0]] = replace(prior[PRIOR[0]], **change)
    with pytest.raises(InputRefusal):
        events.classify_window(metric, PRIOR, prior)


def grid(states):
    rising, flat = session(), session(flat=True)
    decisions = []
    for offset in events.OFFSETS:
        state = states.get(offset, "not_high")
        current = measured(rising if state == "high" else flat, offset)
        if state == "unavailable":
            current = measured(changed(flat, offset - 1, ohlc=None, price_state="missing"), offset)
        decisions.append(classified(current, measured(flat, offset)))
    return tuple(decisions)


def test_known_not_high_creates_resets_and_exact_confirmed_onsets():
    summary = events.group_episodes(grid({20: "high", 25: "high", 35: "high"}))
    assert summary.confirmed_onsets == summary.exact_onset_count == 2
    assert summary.left_censored_clusters == 0 and summary.observed_high_windows == 3
    assert [item.high_offsets for item in summary.episodes] == [(20, 25), (35,)]
    assert not any(item.right_censored for item in summary.episodes)


def test_first_grid_high_and_postgap_high_are_left_censored():
    summary = events.group_episodes(grid({15: "high", 25: "unavailable", 30: "high"}))
    assert summary.confirmed_onsets == 0 and summary.left_censored_clusters == 2
    assert summary.exact_onset_count is None


def test_unknown_between_highs_does_not_invent_a_reset_or_new_onset():
    summary = events.group_episodes(grid({20: "high", 25: "unavailable", 30: "high", 35: "unavailable"}))
    assert len(summary.episodes) == 1 and summary.confirmed_onsets == 1
    episode = summary.episodes[0]
    assert episode.high_offsets == (20, 30) and episode.unknown_bridge_offsets == (25,)
    assert episode.trailing_unknown_offsets == (35,) and episode.right_censored is True
    assert summary.unavailable_offsets == (25, 35) and summary.exact_onset_count is None


def test_unobserved_window_can_hide_episode_even_without_observed_high():
    summary = events.group_episodes(grid({30: "unavailable"}))
    assert summary.episodes == () and summary.confirmed_onsets == summary.observed_high_windows == 0
    assert summary.exact_onset_count is None
    fully_observed = events.group_episodes(grid({}))
    assert fully_observed.exact_onset_count == 0


def test_final_high_is_right_censored_but_onset_count_is_known():
    summary = events.group_episodes(grid({300: "high"}))
    assert summary.exact_onset_count == 1
    assert summary.episodes[0].right_censored and summary.episodes[0].confirmed_onset


def test_partial_reordered_or_mixed_identity_grid_refused():
    values = grid({20: "high"})
    for bad in (values[:-1], values[::-1], values[:1] + values[:1] + values[2:]):
        with pytest.raises(InputRefusal):
            events.group_episodes(bad)
    changed_first = replace(values[0], current=replace(values[0].current, symbol="OTHER"))
    with pytest.raises(InputRefusal):
        events.group_episodes((changed_first,) + values[1:])


def test_false_high_and_mutated_observation_are_refused():
    values = grid({})
    with pytest.raises(InputRefusal, match="decision_status"):
        events.group_episodes((replace(values[0], status="high"),) + values[1:])
    metric = measured()
    with pytest.raises(FrozenInstanceError):
        metric.energy = Fraction(99)
