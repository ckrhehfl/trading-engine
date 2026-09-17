"""Tests for `research.krx_tax_schedule`.

This module closes rd-f §1.1 item 3, the blocking prerequisite for any
Korean registration, so the properties that matter are the ones that
would put a wrong *cost* into a backtest without looking wrong:

- **The settlement lag is validated, not chosen.** Every statutory date
  is a 양도일; a backtest keys on the trade date; and the seam between
  them is not a fixed number of calendar days.
- **A boundary is derived from the real trading calendar**, never from
  calendar-day arithmetic, because where the holidays fall decides it.
- **No market silently inherits another's rate.** KONEX pays half of
  KOSPI's, and a default would hand a future universe the wrong one.

The calendars below are **real sessions**, copied from the KOSPI index
series in the store. They are fixtures so this suite does not need the
gitignored database, and they are real so the tests check the *rule*
rather than a calendar invented to suit it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from research.krx_tax_schedule import (
    KONEX,
    KOSDAQ,
    KOSPI,
    RD_F_FLAT_BP,
    SCHEDULE,
    SOURCES,
    SETTLEMENT_LAG_SESSIONS,
    eras_for,
    flat_rate_error,
    realised_schedule,
    total_bp,
    trade_date_boundary,
)


def _days(*iso: str) -> list[dt.date]:
    return [dt.date.fromisoformat(s) for s in iso]


#: Real KOSPI sessions around the 2019 cut. 2019-05-25/26 and 06-01/02 are
#: weekends, and 06-06 is 현충일.
_2019_SEAM = _days(
    "2019-05-23", "2019-05-24", "2019-05-27", "2019-05-28", "2019-05-29",
    "2019-05-30", "2019-05-31", "2019-06-03", "2019-06-04", "2019-06-05",
    "2019-06-07",
)

#: Real KOSPI sessions around the 2025 cut. The seam runs from the
#: 2024-12-27 boundary to the 2025-01-01 statutory date, and the
#: non-trading days inside it are the 12-28/29 weekend and KRX's year-end
#: 휴장일 on 12-31 — which is why two sessions come to five calendar days
#: here and four in 2019. Christmas is NOT a cause: 12-25 precedes the
#: boundary.
_2024_SEAM = _days(
    "2024-12-18", "2024-12-19", "2024-12-20", "2024-12-23", "2024-12-24",
    "2024-12-26", "2024-12-27", "2024-12-30", "2025-01-02", "2025-01-03",
    "2025-01-06",
)


# ------------------------------------- the lag, validated not assumed


@pytest.mark.parametrize(
    "statutory,published,calendar",
    [
        (dt.date(2019, 6, 3), dt.date(2019, 5, 30), _2019_SEAM),
        (dt.date(2025, 1, 1), dt.date(2024, 12, 27), _2024_SEAM),
    ],
)
def test_the_lag_reproduces_both_published_trade_dates(statutory, published, calendar):
    """**The validation the whole module rests on.** Both changes were
    announced with two dates — 양도일 2019-06-03 / 매매체결일 2019-05-30,
    and 양도일 2025-01-01 / 매도체결분 2024-12-27 — so there are two
    independent published answers to check the rule against, five and a
    half years apart."""
    assert trade_date_boundary(statutory, calendar) == published


@pytest.mark.parametrize("wrong_lag", [1, 3])
def test_no_other_lag_reproduces_both(wrong_lag):
    """T+1 and T+3 each miss both. Asserted so the lag reads as a measured
    quantity rather than a plausible constant somebody could 'tidy up'."""
    got = [
        trade_date_boundary(dt.date(2019, 6, 3), _2019_SEAM, lag=wrong_lag),
        trade_date_boundary(dt.date(2025, 1, 1), _2024_SEAM, lag=wrong_lag),
    ]
    assert got != [dt.date(2019, 5, 30), dt.date(2024, 12, 27)]


def test_the_lag_is_two_sessions():
    assert SETTLEMENT_LAG_SESSIONS == 2


# ------------------------------------------- the seam is not calendar days


def test_the_seam_is_not_a_fixed_number_of_calendar_days():
    """**Why the calendar is needed at all.** Both boundaries are two
    *sessions* before their statutory date, and that is 4 calendar days in
    2019 and 5 in 2024 — because a weekend falls inside the first seam and
    a weekend plus KRX's 12-31 휴장일 inside the second. A rule written in
    calendar days is right at one boundary and wrong at the other."""
    assert (dt.date(2019, 6, 3) - dt.date(2019, 5, 30)).days == 4
    assert (dt.date(2025, 1, 1) - dt.date(2024, 12, 27)).days == 5
    # Different calendar-day gaps, identical session gap.
    assert _2019_SEAM.index(dt.date(2019, 6, 3)) - _2019_SEAM.index(
        dt.date(2019, 5, 30)
    ) == 2
    assert _2024_SEAM.index(dt.date(2025, 1, 2)) - _2024_SEAM.index(
        dt.date(2024, 12, 27)
    ) == 2


def test_using_the_statutory_date_directly_would_be_wrong():
    """The negative control. A backtest keying on the statutory date
    charges the old rate to every trade in the seam — which is the error
    this module exists to prevent, so it is asserted rather than implied."""
    for statutory, calendar in [
        (dt.date(2019, 6, 3), _2019_SEAM),
        (dt.date(2025, 1, 1), _2024_SEAM),
    ]:
        assert trade_date_boundary(statutory, calendar) < statutory


def test_a_trade_in_the_seam_pays_the_rate_it_will_SETTLE_at():
    """2019-05-30 executes before the statutory date and settles after it,
    so it owes the new 25bp — not the 30bp in force on the day it traded."""
    assert total_bp(KOSPI, dt.date(2019, 5, 29), _2019_SEAM) == 30.0
    assert total_bp(KOSPI, dt.date(2019, 5, 30), _2019_SEAM) == 25.0


def test_an_era_already_in_force_starts_at_the_calendar_s_first_session():
    """The 2017 rate was in force when the window opened, so every trade
    in the calendar settles after it — the boundary is the first session,
    not `None`."""
    assert trade_date_boundary(dt.date(2017, 4, 1), _2019_SEAM) == _2019_SEAM[0]
    assert total_bp(KOSPI, _2019_SEAM[0], _2019_SEAM) == 30.0


def test_a_change_after_the_window_has_no_boundary_and_is_not_in_force():
    """**The opposite end, and the one that fails dangerously.** `None`
    means no trade in this calendar settles late enough. Treating it as
    `calendar[0]` — the obvious fallback — would render a future rate
    change as having applied the whole time."""
    assert trade_date_boundary(dt.date(2030, 1, 1), _2019_SEAM) is None
    spans = realised_schedule(KOSPI, _2019_SEAM)
    assert all(era.effective <= dt.date(2019, 6, 3) for _, _, era in spans)
    assert [era.total_bp for _, _, era in spans] == [30.0, 25.0]


def test_a_trade_outside_the_calendar_is_refused():
    """It would otherwise get a plausible answer from the wrong era: every
    boundary resolves to the calendar's own first session, so an earlier
    trade date compares false against all of them and silently receives
    the OLDEST rate in the schedule — 30bp, the highest there is."""
    with pytest.raises(ValueError, match="outside the calendar"):
        total_bp(KOSPI, dt.date(2018, 1, 2), _2019_SEAM)
    with pytest.raises(ValueError, match="outside the calendar"):
        total_bp(KOSPI, dt.date(2026, 1, 2), _2019_SEAM)


# --------------------------------------------------- the rates themselves


def test_both_markets_pay_the_same_total_in_every_era():
    """**A coincidence of policy, not a structural fact** — KOSPI's
    농특세 is fixed at 0.15% and its 거래세 was moved to keep the totals
    level. Asserted because a cost model is entitled to ignore the market
    only while it holds, and it stops holding the moment one of them
    moves alone."""
    kospi = {e.effective: e.total_bp for e in eras_for(KOSPI)}
    kosdaq = {e.effective: e.total_bp for e in eras_for(KOSDAQ)}
    assert kospi == kosdaq
    assert len(kospi) == 7


def test_the_two_taxes_are_kept_apart():
    """Different statutes, and only one of them moved. Pre-summing would
    lose the fact that 농특세 is constant."""
    assert {e.rural_bp for e in eras_for(KOSPI)} == {15.0}
    assert {e.rural_bp for e in eras_for(KOSDAQ)} == {0.0}
    assert [e.transaction_bp for e in eras_for(KOSPI)] == [15, 10, 8, 5, 3, 0, 5]


def test_konex_is_not_kospi():
    """Carried precisely so a future universe cannot inherit the wrong
    rate by default — KONEX pays half."""
    day = dt.date(2024, 12, 23)
    assert total_bp(KONEX, day, _2024_SEAM) == 10.0
    assert total_bp(KOSPI, day, _2024_SEAM) == 18.0


def test_an_unknown_market_raises_rather_than_defaulting():
    """A silent default to KOSPI is how a KONEX name would be charged
    twice its real tax, or a future market its neighbour's."""
    with pytest.raises(ValueError, match="no tax schedule"):
        total_bp("NASDAQ", dt.date(2024, 6, 3), _2024_SEAM)


def test_every_era_cites_a_resolvable_source():
    """**A label is not a citation.** rd-f refused to guess this schedule
    and cited every figure it did publish; "금투세 도입 연계 단계 인하"
    names a policy, not a document, and an earlier version of this test —
    which only asserted the string was non-empty — passed for it.

    So every era's key must resolve, and every source it resolves to must
    carry a publisher, a date and a URL."""
    for era in SCHEDULE:
        assert era.sources, f"{era.market} {era.effective} cites nothing"
        for citation in era.citations():
            assert citation.publisher.strip()
            assert citation.published.strip()
            assert citation.url.startswith("https://"), citation.url


def test_a_source_key_that_does_not_resolve_is_a_hard_failure():
    """The negative control for the test above: a typo'd key must raise
    rather than silently cite nothing."""
    bad = SCHEDULE[0].__class__(
        dt.date(2019, 6, 3), KOSPI, 10.0, 15.0, ("no-such-source",), "x"
    )
    with pytest.raises(KeyError):
        bad.citations()


def test_every_declared_source_is_actually_cited():
    """A source nobody references is either a leftover or a figure that
    quietly lost its evidence."""
    cited = {key for era in SCHEDULE for key in era.sources}
    assert cited == set(SOURCES), f"unused: {set(SOURCES) - cited}"


def test_the_2019_rates_are_cited_to_a_source_that_states_all_three_markets():
    """KOSPI, KOSDAQ and KONEX all moved on the same 시행령, and the same
    press release carries all three — so the KONEX row is not an
    extrapolation from the other two."""
    for market in (KOSPI, KOSDAQ, KONEX):
        era = next(
            e for e in eras_for(market) if e.effective == dt.date(2019, 6, 3)
        )
        assert "seoul-2019" in era.sources


# ------------------------------------------- what rd-f's flat rate costs


def test_a_flat_rate_understates_the_window_on_net():
    """rd-f said the direction was not established and named both
    possibilities. It is both — understated early, overstated late — and
    the net is understatement."""
    err = flat_rate_error(KOSPI, _2019_SEAM + _2024_SEAM)
    assert err["flat_bp"] == RD_F_FLAT_BP
    assert err["sessions_understated"] > 0
    assert err["sessions_overstated"] > 0


def test_the_error_is_measured_against_the_real_rates_not_asserted():
    """A two-era calendar where the arithmetic is checkable by hand: seven
    sessions at 30bp and four at 25bp against a flat 20."""
    err = flat_rate_error(KOSPI, _2019_SEAM)
    assert err["sessions"] == 11
    assert err["sessions_understated"] == 11, "every 2019 session exceeds 20bp"
    assert err["max_understatement_bp"] == 10.0
    # Five sessions before the 05-30 boundary, six from it.
    assert err["mean_real_bp"] == pytest.approx((30.0 * 5 + 25.0 * 6) / 11)
    assert err["error_bp"] < 0, "flat 20bp understates"
