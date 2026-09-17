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
  2024-12-27** -- *five* calendar days earlier.

**A backtest keys on the trade date.** Using the statutory date directly
would apply the wrong rate to every trade in the seam between the two,
and that seam is **not a fixed number of calendar days**:

| change | boundary trade date | its T+2 settlement | statutory 양도일 | trade -> statutory |
|---|---|---|---|---|
| 2019 | 2019-05-30 | 2019-06-03 | 2019-06-03 | **4 days** |
| 2025 | 2024-12-27 | 2025-01-02 | 2025-01-01 | **5 days** |

The lag is always **two sessions**; what moves is the calendar. Note the
two rows differ in kind as well as length: in 2019 the settlement session
lands *exactly on* the statutory date, while in 2024 it lands the day
after it, because 2025-01-01 is itself a holiday. The rule is therefore
"the first trade date whose settlement falls **on or after** the statutory
date", not "two sessions before it" -- those coincide in 2019 and do not
in 2024.

The non-trading days inside the 2024 seam are the 12-28/29 weekend and
KRX's year-end 휴장일 on 12-31. (Christmas is *outside* it -- 12-25
precedes the 12-27 boundary -- and an earlier draft of this paragraph
wrongly named it as a cause.)

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
answers, five and a half years apart, one of them across a year end whose
seam holds a weekend and KRX's 12-31 휴장일.

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
import bisect
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
class Source:
    """One citation, resolvable rather than described.

    rd-f refused to guess this schedule and cited every figure it did
    publish. A replacement that carried only a label -- "금투세 도입 연계
    단계 인하" names a policy, not a document -- would be a guess wearing a
    citation's clothes, and a test asserting the label is non-empty would
    pass for one.
    """

    publisher: str
    published: str
    url: str


SOURCES: dict[str, Source] = {
    "namu": Source(
        "나무위키 — 증권거래세 (탄력세율 조항 및 세율 변천표)",
        "n.d., read 2026-09-17",
        "https://namu.wiki/w/%EC%A6%9D%EA%B6%8C%EA%B1%B0%EB%9E%98%EC%84%B8",
    ),
    "seoul-2019": Source(
        "서울신문 — 증권거래세 30일부터 인하…코스피·코스닥 0.05%P↓ "
        "(KOSPI 0.15->0.10, KOSDAQ 0.30->0.25, KONEX 0.30->0.10)",
        "2019-05-22",
        "https://www.seoul.co.kr/news/economy/securities/2019/05/22/20190522024015",
    ),
    "hankookilbo-2019": Source(
        "한국일보 — 내달 3일 결제분부터 증권거래세 인하 "
        "(the 양도일/체결일 pair, stated explicitly)",
        "2019-05-21",
        "https://www.hankookilbo.com/news/article/201905211157035366",
    ),
    "kofia-2019": Source(
        "금융투자협회 — 증권유관기관 공동보도자료: 오늘부터 증권거래세가 인하됩니다",
        "2019-05-30",
        "https://www.kofia.or.kr/npboard/m_18/view.do?nttId=122270"
        "&bbsId=BBSMSTR_000000000203&page=29",
    ),
    "kbthink": Source(
        "KB — 국내 주식 세금 총정리: 양도소득세, 배당소득세, 증권거래세",
        "2024-10",
        "https://kbthink.com/main/asset-management/wealth-manage-tip/"
        "kbthink-original/202410/kr-stocktax.html",
    ),
    "ds-2025": Source(
        "DS투자증권 — 2025년 증권거래세율 인하 적용안내 "
        "(체결일 기준 2024-12-27부터)",
        "2024-12",
        "https://ds-sec.co.kr/bbs/board.php?bo_table=sub06_10&wr_id=751&page=1",
    ),
    "taxtimes-2026": Source(
        "한국세정신문 — 내년 1월부터 증권거래세율 코스피 0.05%, 코스닥 0.20%로 상향",
        "2025-12",
        "https://taxtimes.co.kr/news/article.html?no=272624",
    ),
}


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
    #: Keys into `SOURCES`. More than one where two documents independently
    #: carry the same figure, which is why it is a tuple rather than a str.
    sources: tuple[str, ...]
    note: str

    @property
    def total_bp(self) -> float:
        return self.transaction_bp + self.rural_bp

    def citations(self) -> list[Source]:
        return [SOURCES[key] for key in self.sources]


#: Sourced 2026-09-17. Each era carries the citation it came from.
#:
#: The pre-2019 row is the rate in force when this project's KRX window
#: opens (2019-01-02), not the start of the tax -- 증권거래세 dates to
#: 1963 and the 0.30% level to 2017-04-01.
SCHEDULE: tuple[TaxEra, ...] = (
    # --- KOSPI: 농특세 is 0.15% in every era and never moves.
    TaxEra(dt.date(2017, 4, 1), KOSPI, 15.0, 15.0, ("namu",), "in force at the window's open"),
    TaxEra(dt.date(2019, 6, 3), KOSPI, 10.0, 15.0, ("seoul-2019", "hankookilbo-2019", "kofia-2019"), "시행령 개정 2019-05-28"),
    TaxEra(dt.date(2021, 1, 1), KOSPI, 8.0, 15.0, ("namu", "kbthink"), "금투세 연계 단계 인하"),
    TaxEra(dt.date(2023, 1, 1), KOSPI, 5.0, 15.0, ("namu", "kbthink"), "금투세 연계 단계 인하"),
    TaxEra(dt.date(2024, 1, 1), KOSPI, 3.0, 15.0, ("namu", "kbthink"), "금투세 연계 단계 인하"),
    TaxEra(dt.date(2025, 1, 1), KOSPI, 0.0, 15.0, ("namu", "ds-2025"), "체결일 2024-12-27부터"),
    TaxEra(dt.date(2026, 1, 1), KOSPI, 5.0, 15.0, ("taxtimes-2026", "namu"), "2025 세제개편안; 금투세 폐지분 환원"),
    # --- KOSDAQ: no 농특세, so its own rate carries the whole total.
    TaxEra(dt.date(2017, 4, 1), KOSDAQ, 30.0, 0.0, ("namu",), "in force at the window's open"),
    TaxEra(dt.date(2019, 6, 3), KOSDAQ, 25.0, 0.0, ("seoul-2019", "kofia-2019"), "시행령 개정 2019-05-28"),
    TaxEra(dt.date(2021, 1, 1), KOSDAQ, 23.0, 0.0, ("namu", "kbthink"), "금투세 연계 단계 인하"),
    TaxEra(dt.date(2023, 1, 1), KOSDAQ, 20.0, 0.0, ("namu", "kbthink"), "금투세 연계 단계 인하"),
    TaxEra(dt.date(2024, 1, 1), KOSDAQ, 18.0, 0.0, ("namu", "kbthink"), "금투세 연계 단계 인하"),
    TaxEra(dt.date(2025, 1, 1), KOSDAQ, 15.0, 0.0, ("namu", "ds-2025"), "금투세 폐지 전 최종 인하"),
    TaxEra(dt.date(2026, 1, 1), KOSDAQ, 20.0, 0.0, ("taxtimes-2026", "namu"), "2025 세제개편안; 금투세 폐지분 환원"),
    # --- KONEX: carried so a future universe cannot inherit KOSPI's rate.
    TaxEra(dt.date(2017, 4, 1), KONEX, 30.0, 0.0, ("namu",), "in force at the window's open"),
    TaxEra(dt.date(2019, 6, 3), KONEX, 10.0, 0.0, ("seoul-2019",), "0.30 -> 0.10"),
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
            f"calendar-day arithmetic: the trade-to-statutory gap is 4 days at "
            f"one boundary and 5 at another, entirely because of where the "
            f"non-trading days fall."
        )
    return [dt.datetime.fromtimestamp(ms / 1000, dt.UTC).date() for (ms,) in rows]


def trade_date_boundary(
    statutory: dt.date, calendar: list[dt.date], lag: int = SETTLEMENT_LAG_SESSIONS
) -> dt.date | None:
    """The first **trade date** whose settlement falls on or after `statutory`.

    For an era already in force when the calendar opens this is the
    calendar's own first session -- correct, since every trade in it
    settles after the change.

    `None` means the opposite end: **no session in this calendar settles
    on or after the statutory date**, i.e. the change takes effect after
    the window ends. A caller must treat that as "not yet in force" and
    not as "in force throughout", which is the direction an
    `or calendar[0]` fallback would silently get wrong.

    **`None` is also returned when the calendar merely stops too early to
    see a change that did happen**, and those two cases are
    indistinguishable from here -- which is exactly why `total_bp`
    refuses a trade whose own settlement lies beyond the calendar rather
    than asking this function about it.
    """
    settles_at = bisect.bisect_left(calendar, statutory)
    if settles_at == len(calendar):
        return None
    return calendar[max(settles_at - lag, 0)]


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
    position = bisect.bisect_left(calendar, trade_date)
    if position == len(calendar) or calendar[position] != trade_date:
        # Not a session at all -- a weekend, a holiday, or outside the
        # calendar entirely. An outside date is the dangerous one: every
        # boundary resolves to the calendar's own first session, so an
        # earlier trade date compares false against all of them and
        # silently receives the OLDEST rate in the schedule.
        raise ValueError(
            f"{trade_date} is not a session in this calendar "
            f"({calendar[0]} .. {calendar[-1]}), so no boundary in it can date "
            f"the trade. Pass the calendar that covers the trade."
        )
    if position + SETTLEMENT_LAG_SESSIONS >= len(calendar):
        # **The rate is genuinely indeterminate here, not merely awkward.**
        # This trade settles beyond the calendar's last session, so
        # whether a change applies to it cannot be read from this data --
        # and the failure is silent and data-dependent: the identical
        # trade on 2024-12-27 pays 15bp against a calendar that reaches
        # 2025-01-02 and 18bp against one that stops at 2024-12-30,
        # because the 2025 boundary becomes unfindable and the era is
        # skipped. Refusing is the only answer that does not depend on
        # where the data happens to end.
        raise ValueError(
            f"a trade on {trade_date} settles {SETTLEMENT_LAG_SESSIONS} sessions "
            f"later, beyond this calendar's last session ({calendar[-1]}), so its "
            f"rate cannot be determined. Pass a calendar extending at least "
            f"{SETTLEMENT_LAG_SESSIONS} sessions past the trade."
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

    The final `SETTLEMENT_LAG_SESSIONS` sessions are **excluded and
    counted**, not silently dropped: their settlement lies beyond the
    calendar, so their rate is indeterminate. `undatable_sessions` reports
    how many, so a caller can see the aggregate is not over the whole
    window.
    """
    datable = calendar[: len(calendar) - SETTLEMENT_LAG_SESSIONS]
    if not datable:
        raise ValueError(
            f"a calendar of {len(calendar)} sessions is too short to date any "
            f"trade: every one of them settles beyond its end."
        )
    real = [total_bp(market, day, datable + calendar[len(datable):]) for day in datable]
    mean = sum(real) / len(real)
    return {
        "sessions": float(len(datable)),
        "undatable_sessions": float(len(calendar) - len(datable)),
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
