"""The per-era Korean transaction-tax schedule -- rd-f §1.1 item 3.

[`rd-f`](../../.planning/rd-f-korean-cost-structure.md) sourced the tax
that dominates the Korean round trip, applied **today's 20bp across the
whole 2019-2026 window**, and said plainly what that was:

> *"The rate has changed repeatedly across the KR-10 window, and the
> direction of the resulting bias is NOT established here … applying
> today's 20bp across 2019-2026 is a stated simplification, not a
> conservative one: it may understate the early years and overstate the
> late ones. **A per-era schedule must be sourced before any Korean
> registration**; this document does not have one and does not guess."*

This is that schedule. It closes the item rd-f named as blocking, and it
establishes the direction: rd-f's guess about which way it cuts was
right, and the **net** over the window is that a flat 20bp
**understates** the real cost.

## The rates

Korea taxes the **sale**, not the gain, so a losing trade pays in full.
The statutory base rate is 0.35%; what actually applies is the 탄력세율
set per market by 증권거래세법 시행령 §5. KOSPI additionally carries
농어촌특별세 at a **constant 0.15%** throughout this window; KOSDAQ
carries none and its own rate absorbs the difference.

**The consequence is that both markets pay the same total in every era
of this window** -- the government moved them together deliberately. So
for a cost model the market does not matter, only the date. That is
asserted rather than assumed (`test_both_markets_pay_the_same_total_in_every_era`),
because it is the kind of coincidence that stops being true the moment
someone changes one of them.

## The trap: every statutory date is a SETTLEMENT date

This is the part rd-f could not have known without sourcing it, and it is
the reason this module needs a trading calendar at all.

증권거래세 is levied on 양도 -- the transfer -- which in Korea settles on
the **second trading day after execution**. Every rate change is
therefore announced with *two* dates, and the press reported both:

- the 2019 cut applied to **양도일 2019-06-03**, i.e. **매매체결일
  2019-05-30**;
- the 2025 cut applied to **양도일 2025-01-01**, i.e. **매도체결분
  2024-12-27** -- *five* calendar days earlier, because 12-25 and 12-31
  are not trading days.

**A backtest keys on the trade date.** Using the statutory date directly
would apply the wrong rate to every trade in the two-session seam, and
that seam is **not a fixed number of calendar days**: two sessions came
to 4 calendar days across the 2019 change and 5 across the 2024 year end,
entirely because of where the holidays fell.

So the boundary is **derived** from the real trading calendar rather than
computed from a rule about calendar days -- `trade_date_boundary` walks
the sessions an index actually printed, which is the same
index-as-calendar trick `store.find_missing_ranges` needs for KRX and for
the same reason: an index prints exactly when the market is open, so it
needs no holiday table and covers the moving lunar holidays
`KrxMarketCalendar` still lists as unresolved.

**The T+2 lag is validated, not assumed.** It is the only lag of T+1,
T+2, T+3 that reproduces **both** published trade-date boundaries --
2019-05-30 and 2024-12-27 -- from their statutory dates. Two independent
answers, five and a half years apart, one of them across a year end with
two holidays in the seam.

## What this does not cover

**KONEX is in the table and is not 0.20%** -- it went 0.30% to 0.10% in
2019 and has stayed there. No name in any universe this project uses is
KONEX-listed, but it is carried so that a future universe cannot silently
inherit the KOSPI rate.

**Nothing here is the 금융투자소득세 (금투세).** It was legislated,
repeatedly deferred, and then abolished -- the 2026 increase is the
government replacing its forgone revenue at the transaction stage. A
backtest over this window never owes it.

**Nothing here covers single-stock futures**, which pay **no**
증권거래세 at all. That is rd-q's whole finding and the reason the
instrument matters; this module is about the spot leg.

Run:

    python -m research.krx_tax_schedule
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from dataclasses import dataclass

from data._paths import DEFAULT_DB_PATH

#: The index whose sessions define the trading calendar. KOSPI: it prints
#: exactly when the market is open.
CALENDAR_SYMBOL = "KRX-INDEX:0001"

#: Trading days between execution and 양도 (settlement). **Validated**
#: against both published trade-date boundaries rather than assumed --
#: see the module docstring, `test_the_lag_reproduces_both_published_trade
#: _dates` and `test_no_other_lag_reproduces_both`.
SETTLEMENT_LAG_SESSIONS = 2

KOSPI, KOSDAQ, KONEX = "KOSPI", "KOSDAQ", "KONEX"


@dataclass(frozen=True)
class TaxEra:
    """One era of the schedule, keyed on the **statutory 양도일**.

    `transaction_bp` is 증권거래세 and `rural_bp` is 농어촌특별세; they are
    kept apart rather than pre-summed because they are different taxes
    with different statutes, and only one of them moved.
    """

    #: The 양도일 (settlement date) from which this rate applies. NOT a
    #: trade date -- see the module docstring.
    effective: dt.date
    market: str
    transaction_bp: float
    rural_bp: float
    source: str

    @property
    def total_bp(self) -> float:
        return self.transaction_bp + self.rural_bp


#: Sourced 2026-09-17. Each era carries the citation it came from.
#:
#: The pre-2019 row is the rate in force when this project's KRX window
#: opens (2019-01-02), not the start of the tax -- 증권거래세 dates to
#: 1963 and the 0.30% level to 2017-04-01.
SCHEDULE: tuple[TaxEra, ...] = (
    # --- KOSPI: 농특세 is 0.15% in every era and never moves.
    TaxEra(dt.date(2017, 4, 1), KOSPI, 15.0, 15.0, "namu/증권거래세; in force at the window's open"),
    TaxEra(dt.date(2019, 6, 3), KOSPI, 10.0, 15.0, "시행령 개정 2019-05-28; 국무회의 05-21"),
    TaxEra(dt.date(2021, 1, 1), KOSPI, 8.0, 15.0, "금투세 도입 연계 단계 인하"),
    TaxEra(dt.date(2023, 1, 1), KOSPI, 5.0, 15.0, "금투세 도입 연계 단계 인하"),
    TaxEra(dt.date(2024, 1, 1), KOSPI, 3.0, 15.0, "금투세 도입 연계 단계 인하"),
    TaxEra(dt.date(2025, 1, 1), KOSPI, 0.0, 15.0, "금투세 폐지 전 최종 인하; 체결일 2024-12-27"),
    TaxEra(dt.date(2026, 1, 1), KOSPI, 5.0, 15.0, "2025 세제개편안; 금투세 폐지분 환원"),
    # --- KOSDAQ: no 농특세, so its own rate carries the whole total.
    TaxEra(dt.date(2017, 4, 1), KOSDAQ, 30.0, 0.0, "namu/증권거래세; in force at the window's open"),
    TaxEra(dt.date(2019, 6, 3), KOSDAQ, 25.0, 0.0, "시행령 개정 2019-05-28"),
    TaxEra(dt.date(2021, 1, 1), KOSDAQ, 23.0, 0.0, "금투세 도입 연계 단계 인하"),
    TaxEra(dt.date(2023, 1, 1), KOSDAQ, 20.0, 0.0, "금투세 도입 연계 단계 인하"),
    TaxEra(dt.date(2024, 1, 1), KOSDAQ, 18.0, 0.0, "금투세 도입 연계 단계 인하"),
    TaxEra(dt.date(2025, 1, 1), KOSDAQ, 15.0, 0.0, "금투세 폐지 전 최종 인하"),
    TaxEra(dt.date(2026, 1, 1), KOSDAQ, 20.0, 0.0, "2025 세제개편안; 금투세 폐지분 환원"),
    # --- KONEX: carried so a future universe cannot inherit KOSPI's rate.
    TaxEra(dt.date(2017, 4, 1), KONEX, 30.0, 0.0, "namu/증권거래세"),
    TaxEra(dt.date(2019, 6, 3), KONEX, 10.0, 0.0, "시행령 개정 2019-05-28; 0.30 -> 0.10"),
)

#: What rd-f applied across the whole window, and what this module exists
#: to measure the error of.
RD_F_FLAT_BP = 20.0


def trading_days(db_path: str = DEFAULT_DB_PATH) -> list[dt.date]:
    """Every session the KOSPI index printed, oldest first."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT open_time_ms FROM klines WHERE symbol = ? AND interval = '1d' "
            "ORDER BY open_time_ms",
            (CALENDAR_SYMBOL,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError(
            f"{CALENDAR_SYMBOL} has no daily bars, so there is no trading calendar "
            f"to derive a trade-date boundary from. Refusing to fall back on "
            f"calendar-day arithmetic: the seam is 4 days at one boundary and "
            f"2 at another, entirely because of where the holidays fall."
        )
    return [dt.datetime.fromtimestamp(ms / 1000, dt.UTC).date() for (ms,) in rows]


def trade_date_boundary(
    statutory: dt.date, calendar: list[dt.date], lag: int = SETTLEMENT_LAG_SESSIONS
) -> dt.date | None:
    """The first **trade date** whose settlement falls on or after `statutory`.

    For an era already in force when the calendar opens this is the
    calendar's own first session -- correct, since every trade in it
    settles after the change.

    `None` means the opposite end: **no** trade in this calendar settles
    late enough, i.e. the change takes effect after the window ends. A
    caller must treat that as "not yet in force" and not as "in force
    throughout", which is the direction an `or calendar[0]` fallback would
    silently get wrong for a future-dated era.
    """
    for i, day in enumerate(calendar):
        settles = i + lag
        if settles < len(calendar) and calendar[settles] >= statutory:
            return day
    return None


def eras_for(market: str) -> list[TaxEra]:
    return sorted(
        (era for era in SCHEDULE if era.market == market), key=lambda e: e.effective
    )


def total_bp(market: str, trade_date: dt.date, calendar: list[dt.date]) -> float:
    """The tax owed on a sale **executed** on `trade_date`, in basis points.

    Takes a trade date because that is what a backtest has. The statutory
    boundary is converted to its trade-date equivalent first, so a trade
    in the two-session seam before a change is charged the rate that will
    actually apply when it settles.
    """
    eras = eras_for(market)
    if not eras:
        raise ValueError(
            f"no tax schedule for market {market!r}. Refusing to fall back on "
            f"KOSPI's rate -- KONEX's is half of it, and a silent default is how "
            f"a future universe inherits the wrong cost."
        )
    if not calendar:
        raise ValueError("an empty trading calendar cannot date a boundary")
    if not calendar[0] <= trade_date <= calendar[-1]:
        # A trade outside the calendar gets a plausible answer from the
        # wrong era: every boundary resolves to the calendar's own first
        # session, so an earlier trade date compares false against all of
        # them and silently receives the OLDEST rate in the schedule.
        raise ValueError(
            f"trade date {trade_date} is outside the calendar "
            f"({calendar[0]} .. {calendar[-1]}), so no boundary in it can date "
            f"the trade. Pass the calendar that covers the trade."
        )
    applicable = eras[0]
    for era in eras[1:]:
        boundary = trade_date_boundary(era.effective, calendar)
        if boundary is not None and trade_date >= boundary:
            applicable = era
    return applicable.total_bp


def realised_schedule(market: str, calendar: list[dt.date]) -> list[tuple]:
    """`(from_trade_date, to_trade_date, era)` over the calendar's span."""
    eras = eras_for(market)
    spans = []
    for i, era in enumerate(eras):
        start = trade_date_boundary(era.effective, calendar)
        if start is None:
            # Takes effect after this window ends. Skipped rather than
            # defaulted to the calendar's start, which would render a
            # future rate change as having applied the whole time.
            continue
        end = calendar[-1]
        for later in eras[i + 1 :]:
            nxt = trade_date_boundary(later.effective, calendar)
            if nxt is not None and nxt > start:
                end = min(end, _previous(nxt, calendar))
                break
        if start <= end:
            spans.append((start, end, era))
    return spans


def _previous(day: dt.date, calendar: list[dt.date]) -> dt.date:
    i = calendar.index(day)
    return calendar[i - 1] if i else day


def flat_rate_error(
    market: str, calendar: list[dt.date], flat_bp: float = RD_F_FLAT_BP
) -> dict[str, float]:
    """What assuming one flat rate across the window costs, in bp.

    **Session-weighted, and that is a stated assumption rather than a
    neutral one.** It answers "what would a strategy trading uniformly
    across the window have paid", which is the right question for a
    schedule-level bias and the wrong one for any particular strategy: a
    strategy that traded more in 2019 is understated by more. A real
    registration applies `total_bp` per trade and does not use this.
    """
    real = [total_bp(market, day, calendar) for day in calendar]
    mean = sum(real) / len(real)
    return {
        "sessions": float(len(calendar)),
        "mean_real_bp": mean,
        "flat_bp": flat_bp,
        "error_bp": flat_bp - mean,
        "sessions_understated": float(sum(1 for r in real if r > flat_bp)),
        "sessions_overstated": float(sum(1 for r in real if r < flat_bp)),
        "sessions_exact": float(sum(1 for r in real if r == flat_bp)),
        "max_understatement_bp": max(real) - flat_bp,
        "max_overstatement_bp": flat_bp - min(real),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)

    calendar = trading_days(args.db_path)
    print(
        f"trading calendar from {CALENDAR_SYMBOL}: {len(calendar):,} sessions, "
        f"{calendar[0]} .. {calendar[-1]}\n"
    )

    for market in (KOSPI, KOSDAQ):
        print(f"=== {market} -- by TRADE date (what a backtest keys on) ===")
        print(f"{'from':<12} {'to':<12} {'거래세':>7} {'농특세':>7} {'total':>7}  statutory 양도일")
        print("-" * 78)
        for start, end, era in realised_schedule(market, calendar):
            print(
                f"{start!s:<12} {end!s:<12} {era.transaction_bp:>6.1f} "
                f"{era.rural_bp:>7.1f} {era.total_bp:>7.1f}  {era.effective}"
            )
        err = flat_rate_error(market, calendar)
        print(
            f"\nagainst rd-f's flat {err['flat_bp']:.0f}bp: real session-weighted mean "
            f"**{err['mean_real_bp']:.2f}bp**\n"
            f"  the flat figure is off by {err['error_bp']:+.2f}bp "
            f"({'UNDERSTATES' if err['error_bp'] < 0 else 'OVERSTATES'} the real cost)\n"
            f"  understated on {err['sessions_understated']:,.0f} sessions "
            f"(up to {err['max_understatement_bp']:+.0f}bp), "
            f"overstated on {err['sessions_overstated']:,.0f} "
            f"(up to {err['max_overstatement_bp']:.0f}bp), "
            f"exact on {err['sessions_exact']:,.0f}\n"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
