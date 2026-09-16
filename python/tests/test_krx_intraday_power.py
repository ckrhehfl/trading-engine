"""Tests for `research.krx_intraday_power`.

One property carries this module and the rest support it:

**A forward return must never cross a session boundary.** The BTC
machinery indexes positionally because BTC 1m runs continuously; KRX does
not, and the same code there would silently span an 11-minute closing
auction, a 17.5-hour night or a 65.5-hour weekend. It would produce
entirely plausible numbers while doing so, which is what makes it worth a
test rather than a comment.

The rest exist so the power figures cannot be read as more favourable
than they are: the measured dispersion is **unconditional**, and rd-l
§4.1 measured event returns at 1.2-3.1x that — so the headline count is a
lower bound and has to say so.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from research.event_power import alpha_rank1
from research.krx_intraday_power import (
    EVENT_DISPERSION_HIGH,
    EVENT_DISPERSION_LOW,
    HORIZONS,
    KR10,
    KRX_ROUND_TRIP,
    HorizonPower,
    session_forward_return,
    session_labels,
    report,
)


def _power(sigma=0.0066, horizon=15, obs=1000, floor=KRX_ROUND_TRIP):
    return HorizonPower(
        horizon=horizon, observations=obs, sigma=sigma,
        cost_floor=floor, alpha=alpha_rank1(), power=0.80,
    )


# ------------------------------------------------- the session boundary


def test_a_forward_return_never_crosses_a_session():
    """**The whole point.** Session A holds a 100x jump at its boundary
    into B; every pair that would span it is dropped, and the one pair
    that survives is wholly inside B.

    Checked by value rather than by count: a cross-session pair here would
    be a +100% return, so a single leak is unmissable."""
    px = np.array([1.0, 1.0, 1.0, 100.0, 101.0, 102.0])
    sessions = np.array(["A"] * 3 + ["B"] * 3)
    out = session_forward_return(px, sessions, 2)
    assert out.size == 1
    assert out[0] == pytest.approx((102.0 - 100.0) / 100.0)  # wholly inside B


def test_a_within_session_pair_survives():
    px = np.array([100.0, 100.0, 110.0, 999.0])
    sessions = np.array(["A", "A", "A", "B"])
    out = session_forward_return(px, sessions, 1)
    assert out.size == 1 and out[0] == pytest.approx(0.10)


def test_the_overnight_jump_would_dominate_if_it_were_not_excluded():
    """Not a style point. The close-to-open gap is the largest single move
    in an equity series, so spanning it turns an intraday study into an
    overnight one without changing a line of the write-up."""
    px = np.array([100.0, 100.0, 100.0, 150.0, 150.0])
    sessions = np.array(["A", "A", "A", "B", "B"])
    out = session_forward_return(px, sessions, 2)
    assert np.all(np.abs(out) < 1e-12), "a 50% overnight jump leaked in"


def test_the_count_shows_what_the_constraint_costs():
    """At h=240 against a 381-bar session the real series keeps 37% of
    bars, and that shrinkage is evidence rather than an inconvenience."""
    px = np.arange(1.0, 401.0)
    sessions = np.array(["A"] * 200 + ["B"] * 200)
    # h=1: entries 1..398, all but the one straddling index 199/200.
    assert session_forward_return(px, sessions, 1).size == 397
    # h=150: entries 1..49 stay inside A (49) and 200..249 inside B (50).
    assert session_forward_return(px, sessions, 150).size == 99


def test_a_horizon_longer_than_the_series_is_empty_not_an_error():
    px = np.arange(1.0, 6.0)
    assert session_forward_return(px, np.array(["A"] * 5), 10).size == 0


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_horizon_is_refused(bad):
    with pytest.raises(ValueError, match="at least 1 bar"):
        session_forward_return(np.arange(10.0), np.array(["A"] * 10), bad)


def test_sessions_are_labelled_in_kst_not_utc():
    """A UTC date would split the session at 09:00 KST — the middle of it.
    09:00 KST is 00:00 UTC, so the first bar of a session is exactly the
    date boundary and the choice is not cosmetic."""
    import datetime as dt

    from data.kis_intraday import KST

    open_kst = dt.datetime(2026, 9, 11, 9, 0, tzinfo=KST)
    close_kst = dt.datetime(2026, 9, 11, 15, 30, tzinfo=KST)
    ms = np.array([int(open_kst.timestamp() * 1000), int(close_kst.timestamp() * 1000)])
    assert list(session_labels(ms)) == ["20260911", "20260911"]


# ----------------------------------------------------- the arithmetic


def test_the_effect_is_expressed_in_dispersion_units():
    """The number that decides whether the question is worth asking: a
    0.13σ conditional shift is the size rd-b calls detectable, and 0.75σ
    is a different claim entirely."""
    assert _power(sigma=0.0066).effect_in_sigmas == pytest.approx(0.0030 / 0.0066)
    assert _power(sigma=0.0231).effect_in_sigmas == pytest.approx(0.13, abs=0.005)


def test_a_larger_cost_floor_needs_fewer_events_and_a_bigger_effect():
    """rd-l §6's finding, reproduced on a different instrument: a harsher
    round trip makes the question cheaper to settle in events, because it
    only asks you to rule out a larger effect."""
    cheap = _power(floor=0.0012)
    dear = _power(floor=0.0030)
    assert dear.events_unconditional < cheap.events_unconditional
    assert dear.effect_in_sigmas > cheap.effect_in_sigmas


def test_events_scale_with_the_inverse_square_of_the_floor():
    a = _power(floor=0.0030).events_unconditional
    b = _power(floor=0.0015).events_unconditional
    assert b / a == pytest.approx(4.0)


def test_the_unconditional_count_is_reported_as_a_lower_bound():
    """rd-l §4.1 measured event forward returns at 1.2-3.1x the
    unconditional dispersion, because events are volatility bursts.
    Quoting the unconditional figure alone is the mistake that cost rd-l
    its first headline."""
    p = _power()
    lo, hi = p.events_band
    assert lo == pytest.approx(p.events_unconditional * EVENT_DISPERSION_LOW**2)
    assert hi == pytest.approx(p.events_unconditional * EVENT_DISPERSION_HIGH**2)
    assert lo > p.events_unconditional


def test_the_band_is_wide_enough_to_change_a_decision():
    """Not a rounding allowance: 1.2x to 3.1x in dispersion is 1.4x to
    9.6x in events, which is the difference between reachable and not."""
    p = _power()
    lo, hi = p.events_band
    assert hi / lo == pytest.approx((EVENT_DISPERSION_HIGH / EVENT_DISPERSION_LOW) ** 2)
    assert hi / p.events_unconditional > 9


def test_a_zero_dispersion_needs_no_events_rather_than_dividing_by_zero():
    assert _power(sigma=0.0).effect_in_sigmas == math.inf


# ------------------------------------------------------- the constants


def test_the_horizons_stop_inside_one_session():
    """A KRX session is 381 bars, so h=1440 has no meaning inside one and
    spanning sessions is what `session_forward_return` exists to prevent."""
    assert max(HORIZONS) <= 240


def test_the_universe_is_the_ten_kr10_names():
    assert len(KR10) == 10 and "005930" in KR10


def test_the_default_floor_is_the_korean_round_trip():
    """rd-f puts it at 30-33bp; 30 is the today's-rates end and the more
    favourable assumption, so a harsher run must be a deliberate flag."""
    assert KRX_ROUND_TRIP == 0.0030


# ------------------------------------------------------------ report


def test_the_report_states_that_nothing_is_committed(capsys):
    """This measures an instrument. A reader must not be able to take it
    for a designation or a result."""
    report([_power()])
    out = capsys.readouterr().out
    assert "No situation is defined" in out
    assert "LOWER" in out and "BOUND" in out


def test_an_empty_measurement_reports_rather_than_indexing(capsys):
    report([])
    assert "no horizons" in capsys.readouterr().out
