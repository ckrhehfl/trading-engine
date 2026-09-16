"""Tests for `research.krx_futures_universe`.

The module picks a universe, so the properties that matter are the ones
that decide membership for the wrong reason:

- **The front-month boundary.** A contract is listed for months before it
  becomes front, and its deferred-month volume is a fraction of its front
  month's. Pooling the two understates exactly the names whose volume
  lives in the front month — i.e. the names the universe is for.
- **The ranking window must stay inside the served window.** KIS drops an
  expired contract's whole series, so a window reaching further back than
  the data silently ranks on fewer sessions for some names than others.
- **Coverage is a floor, not a tiebreak.** A median over four prints is
  not a median.
- **Rank persistence is the evidence that day-one selection means
  anything here at all**, so the correlation has to be a real one.
"""

from __future__ import annotations

import pytest

from research.krx_futures_universe import (
    FRONT_MONTH_SERIES_START,
    MAX_ORDER_NOTIONAL_FRACTION,
    MIN_SESSION_COVERAGE,
    RANKING_WINDOW,
    Liquidity,
    _contract_window,
    _day_before,
    front_month_series,
    measure_window,
    spearman,
)
from data.kis_futures import CONTRACT_SHARES, FuturesBar


def _bar(date, volume=100, value=1_000_000, close=250_000.0):
    return FuturesBar(
        date=date, open=close, high=close, low=close, close=close,
        volume=volume, value=value,
    )


def _liquidity(traded=60, expected=60, value=1e9, close=250_000.0, printed=None):
    return Liquidity(
        underlying="005930", traded_sessions=traded,
        printed_sessions=expected if printed is None else printed,
        expected_sessions=expected,
        median_value_krw=value, median_volume=100.0, last_close=close,
    )


# ------------------------------------------------- the request window


def test_a_contract_is_asked_only_for_its_front_month_period():
    """Wide enough to cover the ~1 month a contract is front, narrow
    enough to stay under the silent 100-row cap. A quarterly contract
    asked for its whole life comes back truncated with `rt_cd=0`."""
    assert _contract_window("202603") == ("20260101", "20260331")
    assert _contract_window("202610") == ("20260801", "20261031")


def test_the_window_rolls_back_across_a_year_boundary():
    assert _contract_window("202601") == ("20251101", "20260131")


@pytest.mark.parametrize(
    "expiry,last_day", [("202602", "28"), ("202604", "30"), ("202612", "31")]
)
def test_the_window_ends_on_a_real_last_day_of_month(expiry, last_day):
    """`20260231` is not a date. It would be sent verbatim to KIS, and
    what an invalid date does there has never been measured."""
    assert _contract_window(expiry)[1].endswith(last_day)


# ------------------------------------------------- the front-month rule


def test_only_front_month_bars_enter_the_series():
    """**The rule the statistic depends on.** The 2026-02 contract traded
    from 2025-11 as a deferred month; only its bars after the 2026-01
    expiry count, because that is when a trader would have been in it."""
    calendar = {"202601": "20260108", "202602": "20260212"}
    by_expiry = {
        "202601": [_bar("20251215"), _bar("20260105"), _bar("20260108")],
        # Deferred-month bars, then its own front-month run.
        "202602": [_bar("20251215"), _bar("20260105"), _bar("20260109"), _bar("20260212")],
    }
    series = front_month_series(by_expiry, calendar)
    assert sorted(series) == ["20251215", "20260105", "20260108", "20260109", "20260212"]
    # 2026-01-05 is claimed by the 2026-01 contract, not the 2026-02 one.
    assert series["20260105"] is by_expiry["202601"][1]


def test_a_deferred_month_does_not_shadow_the_front_month():
    """The negative control for the test above. If the boundary were
    ignored, the later contract's bar would overwrite the front one and
    the series would carry the wrong name's liquidity."""
    calendar = {"202601": "20260108", "202602": "20260212"}
    front = _bar("20260105", volume=10_000)
    deferred = _bar("20260105", volume=3)
    series = front_month_series(
        {"202601": [front], "202602": [deferred]}, calendar
    )
    assert series["20260105"].volume == 10_000


def test_bars_before_the_identifiable_start_are_dropped():
    """The 2025-12 contract is gone from KIS, so before 2025-12-12 the
    front month cannot be identified at all. Including the 2026-01
    contract's earlier bars would silently label deferred trades as
    front-month ones."""
    calendar = {"202601": "20260108"}
    series = front_month_series(
        {"202601": [_bar("20251110"), _bar("20251211"), _bar("20251212")]}, calendar
    )
    assert sorted(series) == ["20251212"]


def test_the_series_start_is_the_day_after_the_last_unserved_expiry():
    assert FRONT_MONTH_SERIES_START == "20251212"
    assert _day_before(FRONT_MONTH_SERIES_START) == "20251211"
    assert _day_before(FRONT_MONTH_SERIES_START) < FRONT_MONTH_SERIES_START


def test_the_ranking_window_starts_after_the_series_does():
    """A ranking window reaching before the identifiable start would rank
    some names on deferred-month volume and others on nothing."""
    assert RANKING_WINDOW[0] > FRONT_MONTH_SERIES_START


# ----------------------------------------------------- the measurement


def test_the_statistic_is_a_median_across_days_not_a_sum():
    """A sum lets one expiry-day volume spike decide a membership. This is
    the median *within* a name — not rd-q's median *across* names, which
    is the statistic that hid its own finding."""
    series = {d: _bar(d, value=v) for d, v in
              [("20260105", 1), ("20260106", 2), ("20260107", 3),
               ("20260108", 4), ("20260109", 10_000_000)]}
    row = measure_window("005930", series, ("20260101", "20260131"), 5)
    assert row.median_value_krw == 3


def test_bars_outside_the_window_do_not_count():
    series = {"20251215": _bar("20251215", value=99), "20260105": _bar("20260105", value=7)}
    row = measure_window("005930", series, RANKING_WINDOW, 1)
    assert row.printed_sessions == 1 and row.median_value_krw == 7


def test_a_name_that_barely_prints_fails_the_coverage_floor():
    """A listed contract nobody trades is not tradeable, and a median over
    four prints is not a median."""
    assert not _liquidity(traded=4, expected=60).eligible
    assert _liquidity(traded=60, expected=60).eligible
    assert _liquidity(traded=int(60 * MIN_SESSION_COVERAGE), expected=60).eligible


def test_coverage_counts_TRADED_sessions_not_printed_bars():
    """**The floor's whole content.** KIS prints a bar for every session a
    contract is listed, carrying volume 0 when nobody traded it -- so bar
    count is 100% for all 283 names and filters nothing. Found by watching
    a real run report 186 of 186 sessions for every name, not by reading
    the code."""
    listed_but_untraded = _liquidity(traded=3, expected=60, printed=60)
    assert listed_but_untraded.printed_sessions == 60
    assert listed_but_untraded.coverage == pytest.approx(3 / 60)
    assert not listed_but_untraded.eligible


def test_an_untraded_session_lowers_the_median_rather_than_vanishing():
    """A name listed all quarter that trades on a third of it HAS a low
    median. Dropping the zero-volume bars would report it as liquid on the
    days it happened to trade, which is the flattering direction."""
    series = {d: _bar(d, volume=v, value=v) for d, v in
              [("20260105", 0), ("20260106", 0), ("20260107", 0),
               ("20260108", 900), ("20260109", 1000)]}
    row = measure_window("005930", series, ("20260101", "20260131"), 5)
    assert row.traded_sessions == 2 and row.printed_sessions == 5
    assert row.median_value_krw == 0


def test_a_name_with_no_turnover_at_all_is_ineligible():
    """Distinct from thin coverage: it printed every session and traded
    nothing. Both are real and neither belongs in a universe."""
    assert not _liquidity(value=0.0).eligible


def test_an_empty_window_does_not_divide_by_zero():
    assert _liquidity(traded=0, expected=0).coverage == 0.0


# ----------------------------------------------- notional, the other filter


def test_the_account_a_contract_implies_is_reported():
    """rd-q ruled KOSPI200 index futures out at ₩265M a contract against
    the 2% canary limit. A single-stock future is 10 shares, but 10 shares
    of an expensive name is still not small — SK하이닉스 at ₩1.765M is
    ₩17.65M a contract, so ₩882.5M of account."""
    row = _liquidity(close=1_765_000.0)
    assert row.contract_notional_krw() == 1_765_000.0 * CONTRACT_SHARES
    assert row.min_account_krw() == pytest.approx(882_500_000.0)


def test_affordability_must_be_asked_at_the_LATEST_price_not_the_window_s():
    """**A real 2.2x error, in the affordable-looking direction.**
    SK하이닉스 closed the 2026Q1 ranking window at ₩807,000 and 2026-09-16
    at ₩1,765,000. Reporting the window's own last close answers "can this
    account hold one?" with ₩403.5M when the real figure today is ₩882.5M.
    The window's close is right for the window's statistics and wrong for
    this question, so the price is an argument rather than a field."""
    row = _liquidity(close=807_000.0)
    assert row.min_account_krw() == pytest.approx(403_500_000.0)
    assert row.min_account_krw(1_765_000.0) == pytest.approx(882_500_000.0)
    assert row.min_account_krw(1_765_000.0) > 2 * row.min_account_krw()


def test_a_not_yet_listed_name_is_not_an_illiquid_one():
    """**The two reasons a name fails the floor are unrelated**, and one
    number for both would have reported 42 names as too illiquid when 41
    of them simply had no contract yet — KRX lists these in batches, and
    24 arrived on 2026-04-27 with 17 more on 2026-09-14. Both are
    ineligible; only one is a statement about liquidity."""
    not_listed = _liquidity(traded=0, printed=0, expected=59, value=0.0)
    listed_and_dead = _liquidity(traded=0, printed=16, expected=59, value=0.0)
    assert not not_listed.eligible and not listed_and_dead.eligible
    assert not_listed.printed_sessions == 0
    assert listed_and_dead.printed_sessions > 0


def test_the_notional_fraction_is_the_canary_limit():
    assert MAX_ORDER_NOTIONAL_FRACTION == 0.02


# ------------------------------------------------------- rank persistence


def test_a_preserved_order_correlates_perfectly():
    assert spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) == 1.0


def test_a_reversed_order_correlates_negatively():
    assert spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]) == -1.0


def test_it_is_a_rank_correlation_not_a_linear_one():
    """The statistic has to survive the scale: futures turnover spans four
    orders of magnitude across the universe, so Pearson on the raw values
    would be decided by the top two names alone."""
    assert spearman([1.0, 2.0, 3.0], [1.0, 10.0, 1e9]) == 1.0


def test_ties_are_averaged_rather_than_ordered_arbitrarily():
    """Two names with identical turnover must not have their order decided
    by dictionary iteration, which would make the correlation depend on
    the collection order rather than the data."""
    assert spearman([1.0, 1.0, 2.0], [5.0, 5.0, 9.0]) == 1.0


def test_a_side_with_no_spread_is_refused_rather_than_returning_zero():
    """All-equal values make the correlation undefined, not zero — and
    zero would read as 'the ranking does not persist', which is the
    finding this measurement exists to be able to report."""
    with pytest.raises(ValueError, match="no spread"):
        spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])


def test_one_observation_cannot_carry_a_correlation():
    with pytest.raises(ValueError, match="at least two"):
        spearman([1.0], [1.0])


def test_unequal_lengths_are_refused():
    with pytest.raises(ValueError, match="unequal lengths"):
        spearman([1.0, 2.0], [1.0])
