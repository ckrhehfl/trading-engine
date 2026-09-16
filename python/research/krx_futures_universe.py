"""Select the Korean universe on FUTURES liquidity -- rd-q §8 item 2.

[`rd-q`](../../.planning/rd-q-which-korean-instrument.md) found that
single-stock futures beat spot for four of the KR-10 and lose for the
other six, and that the split is **futures** liquidity -- which `ms-e`
never ranked on, because KR-10 was selected on **spot** 거래대금. The
winners' futures traded 27x the cumulative volume of the losers' -- volume
that changed hands, not resting depth at the touch, which is a different
quantity and is not measured anywhere yet. So the universe is wrong, and
this re-selects it.

## The rule, fixed here before it is applied

`ms-e` handled survivorship by day-one selection plus a pre-defined exit
rule, and this reproduces that shape:

1. **Candidate pool**: every underlying with a listed single-stock future
   in KIS's own master -- 283 on 2026-09-16, all 10 shares a contract.
2. **Statistic**: the **median daily front-month futures 거래대금** over
   the ranking window. Median across *days within a name*, because one
   expiry-day volume spike should not decide a membership; this is not
   rd-q's median *across names*, which is the statistic that hid its
   finding.
3. **Ranking window**: `2026Q1`, the first full quarter of the
   front-month series that still exists (see §"What decays" below).
4. **Membership floor**: the name must **trade** on at least 90% of the
   window's sessions. A listed contract nobody trades is not tradeable,
   and a median over four prints is not a median.

   **Traded, not printed** -- and the distinction is the whole floor.
   KIS returns a bar for every session a contract is listed, carrying
   `acml_vol` 0 when nobody traded it, so counting *bars* gives every one
   of the 283 names 100% coverage and filters nothing at all. Found by
   watching the first 22 names of a real run come back at 186 of 186
   sessions each, not by reading the code.
5. **Size**: the top 10, so the result is directly comparable with KR-10.
6. **Exit rule**: a member leaves on a delisting announcement or a
   failure to resume -- never on "its futures got less liquid later",
   which would be selection on the outcome.

## Whether day-one selection means anything here, measured not assumed

A ranking window is only worth having if the ranking persists. So this
also ranks the **forward** window (2026Q2 to date) and reports the rank
correlation between the two. A low correlation would not be a nuisance --
it would say that no point-in-time futures-liquidity selection is
possible at all, which is a finding about the instrument.

## What decays, and why this is urgent rather than tidy

**KIS drops an expired contract's entire series at once.** Measured
2026-09-16: expiries 2026-01 and later answer; 2025-12 and earlier return
zero rows. Not truncation -- nothing. The oldest futures bar reachable
anywhere was **2025-10-10**, and the front-month series is reconstructible
only from **2025-12-12** (when the 2026-01 contract became front).

So this is a second decaying Korean window alongside `rd-o`'s intraday
one, and roughly one more month of it disappears every month. The
ranking-window measurements are therefore written to a committed
artifact, because they will not be re-fetchable.

## Contract notional is a separate filter and may bind harder

rd-q ruled KOSPI200 index futures out at ₩265M a contract against this
project's 2% max-order-notional canary limit. Single-stock futures are
10 shares, but 10 shares of an expensive name is not small: the account
each name implies is reported beside its liquidity, because a name can be
the most liquid future in Korea and still be unholdable.

Run:

    python -m research.krx_futures_universe                 # all 283
    python -m research.krx_futures_universe --limit 20      # a cheap smoke run
"""

from __future__ import annotations

import argparse
import calendar as calendar_module
import json
import os
import pathlib
import statistics
import sys
import time
from dataclasses import asdict, dataclass

from data.kis_futures import (
    CONTRACT_SHARES,
    FuturesBar,
    FuturesMaster,
    download_master,
    parse_master,
)
from data.kis_futures import daily_bars as _daily_bars
from data.kis_klines import KisKlinesError, KisSession

#: Every expiry whose contract still answers, oldest first. 2025-12 and
#: earlier return zero rows -- see the module docstring.
EXPIRIES = (
    "202601", "202602", "202603", "202604", "202605",
    "202606", "202607", "202608", "202609", "202610",
)

#: The ranking window and the forward window it is tested against.
RANKING_WINDOW = ("20260101", "20260331")
FORWARD_WINDOW = ("20260401", "20261231")

#: The first date on which the front-month series is identifiable, and the
#: one figure here that is computed rather than read.
#:
#: The 2025-12 contract expired on the second Thursday of December 2025 --
#: 2025-12-11 -- so the 2026-01 contract was front from the next session.
#: `expiry_calendar` reads every other boundary from a contract's own last
#: bar precisely to avoid computing one, and cannot read this one: KIS no
#: longer serves the 2025-12 contract at all. Stated rather than silently
#: assumed, because bars before this date are **deferred-month** trades in
#: a contract that only later became front, and pooling them into a
#: front-month liquidity statistic would understate exactly the names
#: whose front month is where their volume lives.
FRONT_MONTH_SERIES_START = "20251212"

UNIVERSE_SIZE = 10
MIN_SESSION_COVERAGE = 0.90

#: The canary tier's max order notional (CLAUDE.md, Risk Parameters).
#: One contract must fit inside it, which is the arithmetic that ruled
#: out KOSPI200 index futures.
MAX_ORDER_NOTIONAL_FRACTION = 0.02

#: 005930 is the reference for the market-wide expiry calendar. KRX
#: expiries are market-wide (the second Thursday), but a thin name can
#: fail to print on one, so reading each name's own last bar would give
#: neighbouring names different front-month boundaries. Same reasoning as
#: `store.find_missing_ranges` taking its trading days from an index.
CALENDAR_REFERENCE = "005930"

ARTIFACT_PATH = "runs/krx_futures_liquidity.json"


@dataclass(frozen=True)
class Liquidity:
    """One underlying's front-month futures liquidity over one window."""

    underlying: str
    #: Sessions with `acml_vol > 0` -- sessions on which the contract
    #: actually traded. NOT the number of bars: KIS prints a bar for every
    #: session a contract is listed, so bar count is 100% for every name
    #: and measures listing rather than liquidity.
    traded_sessions: int
    #: Bars in the window, kept beside `traded_sessions` so the gap
    #: between listed and traded is visible rather than inferred.
    printed_sessions: int
    expected_sessions: int
    median_value_krw: float
    median_volume: float
    last_close: float

    @property
    def coverage(self) -> float:
        if not self.expected_sessions:
            return 0.0
        return self.traded_sessions / self.expected_sessions

    def contract_notional_krw(self, close: float | None = None) -> float:
        """One contract is 10 shares, at `close` or the window's own last.

        **Pass the latest close for an affordability question.** This row's
        own `last_close` is the last close *inside its window*, which is
        the right price for the window's statistics and the wrong one for
        "can this account hold one today": SK하이닉스 closed the 2026Q1
        ranking window at ₩807,000 and 2026-09-16 at ₩1,765,000, so the
        window's figure understates the account needed by 2.2x -- in the
        affordable-looking direction.
        """
        return (self.last_close if close is None else close) * CONTRACT_SHARES

    def min_account_krw(self, close: float | None = None) -> float:
        """The account one contract needs under the 2% canary limit."""
        return self.contract_notional_krw(close) / MAX_ORDER_NOTIONAL_FRACTION

    @property
    def eligible(self) -> bool:
        return self.coverage >= MIN_SESSION_COVERAGE and self.median_value_krw > 0


def _contract_window(expiry: str) -> tuple[str, str]:
    """A range narrow enough to stay under the silent 100-row cap.

    A contract is front month for about one month, so its last ~45
    calendar days are all this needs -- and a quarterly contract asked for
    its whole life comes back truncated at 100 rows with `rt_cd=0`, which
    `daily_bars` refuses rather than reads.
    """
    year, month = int(expiry[:4]), int(expiry[4:])
    start_month, start_year = month - 2, year
    if start_month <= 0:
        start_month += 12
        start_year -= 1
    end_day = calendar_module.monthrange(year, month)[1]
    return f"{start_year}{start_month:02d}01", f"{year}{month:02d}{end_day:02d}"


def _day_before(yyyymmdd: str) -> str:
    """A string strictly below `yyyymmdd` in the comparison below.

    The boundary test is `previous < date <= own`, so the first contract
    needs a predecessor. Decrementing the digits is enough -- no real date
    arithmetic is required, and none is implied.
    """
    return str(int(yyyymmdd) - 1)


def expiry_calendar(session: KisSession, master: FuturesMaster) -> dict[str, str]:
    """`{expiry: last trading day}`, read from the reference name's bars.

    Measured rather than computed. The expiry is the second Thursday of
    the month -- verified on the 2026-01/02/03/06/09 contracts -- but a
    holiday moves it, and KRX's moving lunar holidays are the gap
    `KrxMarketCalendar` still lists as unresolved.
    """
    calendar: dict[str, str] = {}
    for expiry in EXPIRIES:
        code = master.contract_code(CALENDAR_REFERENCE, expiry)
        bars = _daily_bars(session, code, *_contract_window(expiry))
        if not bars:
            raise KisKlinesError(
                f"the calendar reference {CALENDAR_REFERENCE} returned nothing for "
                f"{expiry}. Either that expiry has aged out of KIS's window -- in "
                f"which case EXPIRIES needs trimming and the ranking window has "
                f"moved -- or the reference itself is wrong. Not guessable."
            )
        calendar[expiry] = bars[-1].date
        time.sleep(0.35)
    return calendar


def front_month_series(
    by_expiry: dict[str, list[FuturesBar]], calendar: dict[str, str]
) -> dict[str, FuturesBar]:
    """`{date: bar}` taking each date from whichever contract was front.

    A contract is front month from the day after the previous expiry's
    last trading day through its own. Deferred-month bars are dropped:
    they are real trades, but a strategy trades the front month and its
    liquidity is the one that decides whether the name is usable.
    """
    ordered = sorted(calendar)
    series: dict[str, FuturesBar] = {}
    previous_expiry_day = _day_before(FRONT_MONTH_SERIES_START)
    for expiry in ordered:
        own_expiry_day = calendar[expiry]
        for bar in by_expiry.get(expiry, []):
            if previous_expiry_day < bar.date <= own_expiry_day:
                series[bar.date] = bar
        previous_expiry_day = own_expiry_day
    return series


def measure_window(
    underlying: str, series: dict[str, FuturesBar], window: tuple[str, str],
    expected_sessions: int,
) -> Liquidity:
    start, end = window
    bars = [bar for date, bar in sorted(series.items()) if start <= date <= end]
    # The medians run over every printed session including the untraded
    # ones, deliberately: a name that is listed all quarter and trades on
    # a third of it HAS a low median, and hiding the zeros would report it
    # as liquid on the days it happened to trade.
    return Liquidity(
        underlying=underlying,
        traded_sessions=sum(1 for b in bars if b.volume > 0),
        printed_sessions=len(bars),
        expected_sessions=expected_sessions,
        median_value_krw=statistics.median([b.value for b in bars]) if bars else 0.0,
        median_volume=statistics.median([b.volume for b in bars]) if bars else 0.0,
        last_close=bars[-1].close if bars else 0.0,
    )


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, ties averaged. Stdlib only, like every other
    statistic in this project."""
    if len(a) != len(b):
        raise ValueError(f"unequal lengths: {len(a)} and {len(b)}")
    if len(a) < 2:
        raise ValueError("a rank correlation needs at least two observations")

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    var_a = sum((x - mean_a) ** 2 for x in ra)
    var_b = sum((y - mean_b) ** 2 for y in rb)
    if var_a <= 0 or var_b <= 0:
        raise ValueError("a rank correlation is undefined when one side has no spread")
    return cov / (var_a * var_b) ** 0.5


def collect(
    session: KisSession, master: FuturesMaster, underlyings: list[str],
    calendar: dict[str, str], *, pause_s: float = 0.35,
) -> dict[str, dict[str, FuturesBar]]:
    """`{underlying: front-month series}`, one call per contract.

    Unserved contracts are skipped, which is the decaying-window fact
    rather than an error. A name whose every contract is unserved ends up
    with an empty series and fails the coverage floor, which is the
    correct outcome and is reported as such.
    """
    out: dict[str, dict[str, FuturesBar]] = {}
    for n, underlying in enumerate(underlyings, 1):
        by_expiry: dict[str, list[FuturesBar]] = {}
        for expiry in EXPIRIES:
            try:
                code = master.contract_code(underlying, expiry)
            except KisKlinesError as exc:
                print(f"  {underlying}: {exc}", file=sys.stderr)
                break
            bars = _daily_bars(session, code, *_contract_window(expiry))
            if bars:
                by_expiry[expiry] = bars
            time.sleep(pause_s)
        out[underlying] = front_month_series(by_expiry, calendar)
        print(
            f"[{n}/{len(underlyings)}] {underlying}: "
            f"{len(out[underlying])} front-month sessions",
            file=sys.stderr,
        )
    return out


def report(
    ranking: list[Liquidity], forward: dict[str, Liquidity],
    latest_close: dict[str, float] | None = None,
) -> list[Liquidity]:
    eligible = [row for row in ranking if row.eligible]
    eligible.sort(key=lambda row: row.median_value_krw, reverse=True)
    chosen = eligible[:UNIVERSE_SIZE]

    # An ineligible name is ineligible for one of two completely different
    # reasons, and reporting one number conflates them. A contract with no
    # bars at all in the window did not EXIST yet -- KRX lists these in
    # batches -- which is not a statement about its liquidity and must not
    # be read as one.
    unlisted = [row for row in ranking if row.printed_sessions == 0]
    thin = [
        row for row in ranking
        if row.printed_sessions > 0 and not row.eligible
    ]
    print(
        f"\nranking window {RANKING_WINDOW[0]}-{RANKING_WINDOW[1]}, "
        f"{len(ranking)} candidates, {len(eligible)} clearing the "
        f"{MIN_SESSION_COVERAGE:.0%} traded-session floor\n"
        f"  {len(unlisted)} had no contract listed during the window at all "
        f"-- not a liquidity fact\n"
        f"  {len(thin)} were listed and failed the floor\n"
    )
    print(
        f"{'#':>3} {'code':<8} {'median 거래대금':>18} {'median vol':>11} "
        f"{'traded':>8} {'contract ₩':>13} {'min account ₩':>15} {'fwd rank':>9}"
    )
    print("-" * 92)
    # Ranked within the same fixed sample, for the same reason.
    ranking_eligible = {row.underlying for row in eligible}
    forward_order = sorted(
        (row for row in forward.values() if row.underlying in ranking_eligible),
        key=lambda row: row.median_value_krw, reverse=True,
    )
    forward_rank = {row.underlying: i for i, row in enumerate(forward_order, 1)}
    closes = latest_close or {}
    for i, row in enumerate(chosen, 1):
        close = closes.get(row.underlying)
        print(
            f"{i:>3} {row.underlying:<8} {row.median_value_krw:>18,.0f} "
            f"{row.median_volume:>11,.0f} "
            f"{row.traded_sessions}/{row.expected_sessions:<4}"
            + f" {row.contract_notional_krw(close):>13,.0f} "
            f"{row.min_account_krw(close):>15,.0f} "
            f"{forward_rank.get(row.underlying, 0) or '-':>9}"
        )

    # **The sample is fixed by RANKING-window eligibility alone.** Requiring
    # forward eligibility too would filter the persistence measurement on
    # the future -- dropping exactly the names whose liquidity collapsed,
    # which are the ones that would lower the correlation. Selection on the
    # outcome, inside the statistic that exists to justify selecting on the
    # ranking window.
    shared = [row.underlying for row in eligible if row.underlying in forward]
    if len(shared) >= 2:
        by_name = {row.underlying: row for row in eligible}
        rho = spearman(
            [by_name[u].median_value_krw for u in shared],
            [forward[u].median_value_krw for u in shared],
        )
        overlap = len(
            {r.underlying for r in chosen}
            & {r.underlying for r in forward_order[: len(chosen)]}
        )
        print("-" * 92)
        print(
            f"\nrank persistence across {len(shared)} names: Spearman "
            f"**{rho:+.3f}**\ntop-{len(chosen)} overlap between the ranking "
            f"and forward windows: **{overlap} of {len(chosen)}**"
        )
        print(
            "\nA low correlation would say no point-in-time futures-liquidity\n"
            "selection is possible at all -- a finding about the instrument,\n"
            "not a nuisance."
        )
    return chosen


def write_artifact(
    path: pathlib.Path, ranking: list[Liquidity], forward: dict[str, Liquidity],
    calendar: dict[str, str], series: dict[str, dict[str, FuturesBar]],
) -> None:
    """Committed, because the ranking window will stop being fetchable.

    KIS drops an expired contract's whole series, so within months no one
    can reproduce this measurement from the API. An artifact is the only
    form in which the selection stays checkable -- the same reasoning
    behind `runs/spent_windows.json`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # `indent=2` puts every element of every 4-element row on its own
    # line, which is 4.1MB for what compacts to well under a quarter of
    # that. The metadata stays indented because a human reads it; the
    # series rows are placeheld and substituted back compact.
    placeholders = {
        name: f"@@{name}@@"
        for name in series
    }
    body = json.dumps(
            {
                "measured": "2026-09-16",
                "ranking_window": RANKING_WINDOW,
                "forward_window": FORWARD_WINDOW,
                "expiry_calendar": calendar,
                "min_session_coverage": MIN_SESSION_COVERAGE,
                "universe_size": UNIVERSE_SIZE,
                "ranking": [asdict(row) for row in ranking],
                "forward": [asdict(row) for row in forward.values()],
                # The raw front-month series, not just the statistics
                # computed from it. KIS drops an expired contract's whole
                # series, so within months this is the ONLY copy -- and a
                # median cannot be re-derived into a different statistic.
                # Compact positional rows: [date, volume, value, close].
                "series": {
                    name: placeholders[name] for name in sorted(series)
                },
            },
            indent=2, ensure_ascii=False, sort_keys=True,
    )
    for name, name_series in series.items():
        rows = json.dumps(
            [
                [bar.date, bar.volume, bar.value, bar.close]
                for _, bar in sorted(name_series.items())
            ],
            separators=(",", ":"),
        )
        body = body.replace(f'"{placeholders[name]}"', rows)
    path.write_text(body + "\n", encoding="utf-8")


def load_artifact(path: pathlib.Path) -> tuple[dict[str, dict[str, FuturesBar]], dict[str, str]]:
    """`(series, expiry calendar)` from a previously written artifact.

    **The reason the artifact carries raw bars rather than only the
    statistics.** KIS drops an expired contract's whole series, so within
    months this is the only way the ranking can be recomputed at all --
    and a different question (a different window, a mean instead of a
    median, a turnover floor) needs the bars, not the medians.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    series = {
        name: {
            row[0]: FuturesBar(
                date=row[0], open=row[3], high=row[3], low=row[3], close=row[3],
                volume=row[1], value=row[2],
            )
            for row in rows
        }
        for name, rows in payload["series"].items()
    }
    return series, payload["expiry_calendar"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="only the first N underlyings")
    ap.add_argument("--artifact", default=ARTIFACT_PATH)
    ap.add_argument(
        "--from-artifact",
        help="recompute from a previously written artifact, with no API calls. "
             "The only way this ranking stays reproducible once KIS has dropped "
             "the contracts it was measured from.",
    )
    args = ap.parse_args(argv)

    if args.from_artifact:
        series, calendar = load_artifact(pathlib.Path(args.from_artifact))
    else:
        key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
        if not key or not secret:
            print("KIS_APP_KEY / KIS_APP_SECRET are not set", file=sys.stderr)
            return 2

        master = parse_master(download_master())
        session = KisSession(key, secret)
        calendar = expiry_calendar(session, master)
        print(f"expiry calendar from {CALENDAR_REFERENCE}: {calendar}", file=sys.stderr)

        underlyings = (
            master.underlyings[: args.limit] if args.limit else master.underlyings
        )
        series = collect(session, master, underlyings, calendar)

    # The denominator is every date on which ANY name's front month
    # traded -- the market's own session list, the same
    # reference-series trick `store.find_missing_ranges` needs for KRX.
    # A date on which every contract printed a zero-volume bar was not a
    # session anybody could have traded, and counting it would penalise
    # every name equally for a market-wide fact.
    def _sessions(window: tuple[str, str]) -> int:
        return len({
            date
            for name_series in series.values()
            for date, bar in name_series.items()
            if window[0] <= date <= window[1] and bar.volume > 0
        })

    ranking_sessions = _sessions(RANKING_WINDOW)
    forward_sessions = _sessions(FORWARD_WINDOW)
    ranking = [
        measure_window(u, s, RANKING_WINDOW, ranking_sessions) for u, s in series.items()
    ]
    forward = {
        u: measure_window(u, s, FORWARD_WINDOW, forward_sessions)
        for u, s in series.items()
    }

    latest = {
        name: max(name_series.items())[1].close
        for name, name_series in series.items() if name_series
    }
    report(ranking, forward, latest)
    write_artifact(pathlib.Path(args.artifact), ranking, forward, calendar, series)
    print(f"\nwrote {args.artifact}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
