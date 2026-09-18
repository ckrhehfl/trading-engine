"""Tests for `research.strategies.scenario_playbook`.

The registration names five things that would make a Task E run **void
rather than negative**, and three of them are properties of this module.
So these are not coverage tests; each one is a condition the run is not
allowed to violate:

- **a state-machine hole** — any bar matching no branch
- **the look-ahead test failing** — the activity filter reading
  `quote_volume[t]`, which is cumulative over the whole session and so
  includes everything traded after the entry
- **an interior hole in the panel** — a session some name traded that the
  inner join dropped from inside the span
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from research.strategies.scenario_playbook import (
    ATR_PERIOD,
    CONTRACT_SHARES,
    FUTURES_ROUND_TRIP_BP,
    HEDGE_FRACTION,
    SCALE_AT_R,
    STOP_AT_R,
    TIME_EXIT_SESSIONS,
    TRAIL_ATR_MULT,
    TURNOVER_LOOKBACK,
    Branch,
    Core,
    DailyPanel,
    Playbook,
    Policy,
    Position,
    StateMachineHole,
    classify_branch,
    close_cost_bp,
    eligible,
    entry_direction,
    hedge_contracts,
    relative_turnover,
    wilder_atr,
)


def _panel(days: int = 60, names: int = 6, seed: int = 5) -> DailyPanel:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.015, (days, names)), axis=0)
    open_ = close * (1 + rng.normal(0, 0.005, (days, names)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, (days, names))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, (days, names))))
    qv = np.abs(rng.lognormal(20, 0.6, (days, names)))
    return DailyPanel(
        [i * 86_400_000 for i in range(days)],
        [f"S{i}" for i in range(names)],
        open_, high, low, close, qv,
    )


# ============================================================= look-ahead
# The registration's §3 requires this test by name.


def test_the_activity_filter_CANNOT_read_todays_turnover():
    """**Required by the registration, and the single most important test
    here.** `acml_tr_pbmn` is cumulative within the session, so a daily
    bar's 거래대금 includes everything traded *after* an opening-range
    entry. Ranking on it and then entering at the open would pick the
    trade using its own outcome.

    Setting `quote_volume[t]` to an extreme for every name must leave the
    selection untouched. A filter that reads forward cannot survive this;
    one that merely looks correct can.
    """
    p = _panel()
    before = eligible(p)

    poisoned = p.quote_volume.copy()
    poisoned[30] = np.array([1e18, 1e-18] * (p.shape[1] // 2))
    after = eligible(
        DailyPanel(p.dates, p.codes, p.open_px, p.high_px, p.low_px, p.close_px, poisoned)
    )
    assert np.array_equal(before[30], after[30]), "day 30's selection read day 30"


def test_poisoning_yesterday_DOES_move_the_selection():
    """The negative control. Without it the test above passes for a filter
    that reads nothing at all."""
    p = _panel()
    before = eligible(p)
    poisoned = p.quote_volume.copy()
    poisoned[29] = np.array([1e18, 1e-18] * (p.shape[1] // 2))
    after = eligible(
        DailyPanel(p.dates, p.codes, p.open_px, p.high_px, p.low_px, p.close_px, poisoned)
    )
    assert not np.array_equal(before[30], after[30]), "the filter is not reading t-1"


def test_relative_turnover_uses_the_previous_bar_over_its_own_median():
    p = _panel(days=40, names=3)
    rel = relative_turnover(p)
    t = 30
    med = np.median(p.quote_volume[t - TURNOVER_LOOKBACK : t], axis=0)
    assert rel[t] == pytest.approx(p.quote_volume[t - 1] / med)


def test_the_window_before_the_lookback_is_undefined():
    p = _panel(days=40, names=3)
    rel = relative_turnover(p)
    assert np.isnan(rel[: TURNOVER_LOOKBACK + 1]).all()


def test_eligibility_is_a_median_split_with_no_threshold():
    """Half the cross-section by construction, fixed by the data rather
    than chosen — nothing to overfit and nothing to add to `N`."""
    p = _panel(days=60, names=6)
    elig = eligible(p)
    rates = [elig[t].sum() for t in range(TURNOVER_LOOKBACK + 2, p.shape[0])]
    assert all(r == 3 for r in rates), f"6 names must split 3/3, got {set(rates)}"


def test_a_cross_section_too_small_to_rank_selects_nothing():
    p = _panel(days=40, names=2)
    assert not eligible(p).any()


# ======================================================= the state machine


def _pos(direction: int = 1, entry: float = 100.0, risk: float = 2.0, **kw) -> Position:
    return Position(
        code="S0", direction=direction, entry_px=entry, entry_index=0,
        risk_per_share=risk, shares=100.0, playbook=Playbook.BREAKOUT, **kw
    )


def test_a_bar_with_no_prices_RAISES_rather_than_holding():
    """**The hole guard.** Holding through an unclassifiable bar is a
    position taken by accident, which is Task C's failure exactly — its
    parameter-free exit pinned the holding period to one hour without
    anyone choosing it. The registration names a hole as voiding the run.
    """
    with pytest.raises(StateMachineHole, match="cannot be classified"):
        classify_branch(_pos(), 1, float("nan"), 99.0, 1.0)


def test_exactly_one_branch_fires_and_it_is_the_first_match():
    p = _pos()
    # adverse 1R away: 100 - 2.0 = 98.0
    assert classify_branch(p, 1, 101.0, 98.0, 1.0) is Branch.STOP
    # favourable 1R: 100 + 2.0 = 102.0, no adverse
    assert classify_branch(p, 1, 102.0, 99.5, 1.0) is Branch.SCALE
    assert classify_branch(p, 1, 101.0, 99.5, 1.0) is Branch.HOLD


def test_the_stop_wins_a_same_bar_tie_against_the_scale():
    """S8 §3.7 pins this: the pessimistic resolution is the honest one, and
    it is fixed so the same trades cannot yield two different answers."""
    p = _pos()
    both = classify_branch(p, 1, 102.0, 98.0, 1.0)     # both thresholds met
    assert both is Branch.STOP


def test_the_scale_fires_once():
    p = _pos(scaled=True, best_px=105.0)
    assert classify_branch(p, 1, 102.0, 99.5, 0.01) is not Branch.SCALE


def test_the_trail_only_applies_after_the_scale():
    """P3 scales 50% then trails the *remainder*. A trail before the scale
    would be a different policy — P2, which did not clear Gate A."""
    unscaled = _pos(best_px=110.0)
    assert classify_branch(unscaled, 1, 100.5, 100.0, 1.0) is Branch.HOLD
    scaled = _pos(scaled=True, best_px=110.0)
    # trail sits at 110 - 3*1.0 = 107.0; low of 100 is through it
    assert classify_branch(scaled, 1, 100.5, 100.0, 1.0) is Branch.TRAIL


def test_the_trail_is_three_atr_from_the_best_price():
    p = _pos(scaled=True, best_px=120.0)
    atr = 2.0
    trail = 120.0 - TRAIL_ATR_MULT * atr        # 114.0
    assert classify_branch(p, 1, 121.0, trail + 0.01, atr) is Branch.HOLD
    assert classify_branch(p, 1, 121.0, trail - 0.01, atr) is Branch.TRAIL


def test_the_time_exit_can_actually_fire():
    """Task D found a time exit that could never fire, caught only by
    deleting the rule and watching the suite stay green."""
    p = _pos()
    assert classify_branch(p, TIME_EXIT_SESSIONS - 1, 100.5, 99.9, 1.0) is Branch.HOLD
    assert classify_branch(p, TIME_EXIT_SESSIONS, 100.5, 99.9, 1.0) is Branch.TIME


def test_a_short_position_is_classified_on_the_mirrored_side():
    """A short's adverse excursion is the HIGH, not the low. Getting this
    backwards would make every short look like it never stopped out."""
    s = _pos(direction=-1)
    assert classify_branch(s, 1, 102.0, 99.0, 1.0) is Branch.STOP
    assert classify_branch(s, 1, 100.5, 98.0, 1.0) is Branch.SCALE


def test_r_multiple_is_signed_by_direction():
    assert _pos(direction=1).r_multiple(102.0) == pytest.approx(1.0)
    assert _pos(direction=-1).r_multiple(98.0) == pytest.approx(1.0)


# =============================================== regime selects the SIGN


def test_the_playbook_inverts_the_entry_direction():
    """**The whole content of regime selection here.** EXPANSION goes with
    the gap, COMPRESSION against it — so the same gap produces opposite
    trades under the two playbooks, and that is what E1 is testing."""
    up_gap = (105.0, 100.0)
    assert entry_direction(Playbook.BREAKOUT, *up_gap) == +1
    assert entry_direction(Playbook.FADE, *up_gap) == -1
    down_gap = (95.0, 100.0)
    assert entry_direction(Playbook.BREAKOUT, *down_gap) == -1
    assert entry_direction(Playbook.FADE, *down_gap) == +1


def test_no_gap_is_no_signal_under_either_playbook():
    for pb in Playbook:
        assert entry_direction(pb, 100.0, 100.0) == 0


def test_an_unusable_price_is_no_signal_rather_than_a_guess():
    for pb in Playbook:
        assert entry_direction(pb, float("nan"), 100.0) == 0
        assert entry_direction(pb, 100.0, 0.0) == 0


# ===================================================== the hedge's terms


def test_the_hedge_is_whole_contracts_of_ten_shares():
    """`rd-r` measured 10 shares per contract, uniform across all 283
    listed names. A fractional contract does not exist."""
    assert CONTRACT_SHARES == 10
    assert hedge_contracts(1000) == 50      # 1000 * 0.5 / 10
    assert hedge_contracts(215) == 10       # floor(10.75)


def test_a_core_too_small_to_hedge_yields_zero_not_a_fraction():
    """**And the caller must read 0 as a fallback, not as a hedge of size
    zero.** A policy that silently cannot act on some episodes is not
    being tested on them — Task C reported an aggregate before anyone
    noticed its hedge fired twice in 2,544 bars."""
    assert hedge_contracts(19) == 0
    assert hedge_contracts(0) == 0
    assert hedge_contracts(float("nan")) == 0


def test_the_hedge_ratio_matches_task_ds_p5():
    """Fixed at P5's 50% so the comparison to Task D's measured 14.4R loss
    is meaningful rather than approximate."""
    assert HEDGE_FRACTION == 0.5


# ===================================== the two cores are different experiments


def test_closing_spot_costs_the_era_tax_and_futures_does_not():
    """**The registered prediction's whole mechanism.** Task D's P5 lost
    to P3 by about one round trip on BTC, where the transaction tax is
    zero. In Korea closing spot pays 증권거래세 and hedging with a future
    does not, and that gap is the same magnitude."""
    calendar = [dt.date(2026, 1, 2) + dt.timedelta(days=i) for i in range(300)]
    trade = dt.date(2026, 6, 15)
    spot = close_cost_bp(Core.SPOT, trade, calendar)
    futures = close_cost_bp(Core.FUTURES, trade, calendar)
    assert futures == FUTURES_ROUND_TRIP_BP
    assert spot > futures, f"spot {spot}bp must exceed futures {futures}bp"
    assert spot == pytest.approx(20.0), "KOSPI 2026: 5bp 증권거래세 + 15bp 농특세"


def test_the_spot_cost_is_era_correct_rather_than_flat():
    """rd-s's whole finding: a flat rate understates. 2024 and 2026 differ,
    so reading one for the other is a real error."""
    calendar = [dt.date(2023, 1, 2) + dt.timedelta(days=i) for i in range(1400)]
    y2024 = close_cost_bp(Core.SPOT, dt.date(2024, 6, 3), calendar)
    y2026 = close_cost_bp(Core.SPOT, dt.date(2026, 6, 3), calendar)
    assert y2024 != y2026


# ------------------------------------------------------------ ATR + constants


def test_atr_is_undefined_until_its_period_has_passed():
    p = _panel(days=40, names=3)
    atr = wilder_atr(p)
    assert np.isnan(atr[:ATR_PERIOD]).all()
    assert np.isfinite(atr[ATR_PERIOD:]).all()


def test_atr_is_positive_where_defined():
    p = _panel(days=60, names=4)
    atr = wilder_atr(p)
    assert (atr[ATR_PERIOD:] > 0).all()


def test_the_management_constants_are_task_ds_p3():
    """Adopted unchanged — the only policy in project history to clear
    Gate A, and re-searching it would be a new search with a new `N`."""
    assert (SCALE_AT_R, STOP_AT_R, TRAIL_ATR_MULT, TIME_EXIT_SESSIONS) == (
        1.0, 1.0, 3.0, 10,
    )


def test_the_policy_and_core_lists_are_closed():
    """The registration commits the complete list. Adding one means a new
    pre-registration, so the count is pinned here."""
    assert [p.value for p in Policy] == ["E0", "E1", "E2", "E3"]
    assert [c.value for c in Core] == ["futures", "spot"]
    assert len(list(Policy)) * len(list(Core)) == 8


# ============================================ E3 keeps the core; E2 does not
# A real defect, and the shape of it is the lesson: E2 and E3 came out
# byte-identical on the first run, because the invalidation branch closed
# the core before the policy was consulted. Identical results for two
# policies is what exposed it — the verdict on the registered prediction
# was meaningless, not merely wrong.


def test_a_hedged_core_does_NOT_stop_again():
    """**This is the E3 hypothesis, not a convenience.** E3's claim is
    "I do not want to close here, I want to neutralise and see what
    happens": the hedge supersedes the stop and the core runs to its time
    exit. Letting STOP fire again would close the core and make E3
    identical to E2, which is exactly what the first version did."""
    hedged = _pos(hedge_contracts=5)
    assert classify_branch(hedged, 1, 101.0, 98.0, 1.0) is Branch.HOLD

    unhedged = _pos()
    assert classify_branch(unhedged, 1, 101.0, 98.0, 1.0) is Branch.STOP


def test_a_hedged_core_still_reaches_its_time_exit():
    """Superseding the stop must not make the position immortal — without
    a reachable exit the hedge would run to the end of the window on every
    episode, which is a different policy than the one registered."""
    hedged = _pos(hedge_contracts=5)
    assert classify_branch(hedged, TIME_EXIT_SESSIONS, 101.0, 98.0, 1.0) is Branch.TIME


def test_a_hedged_core_can_still_scale_and_trail():
    """The rest of P3's management is unchanged by hedging — only the stop
    is superseded."""
    hedged = _pos(hedge_contracts=5)
    assert classify_branch(hedged, 1, 102.0, 99.5, 1.0) is Branch.SCALE
    scaled = _pos(hedge_contracts=5, scaled=True, best_px=110.0)
    assert classify_branch(scaled, 1, 100.5, 100.0, 1.0) is Branch.TRAIL
