# Research Direction Task O — the KRX intraday backfill, because the window is expiring

**Built and run 2026-09-15.** Modules: `data/kis_intraday.py`,
`data/backfill_kis_intraday.py`.

**This is data preservation, not research.** No return of any kind is
computed here and nothing downstream is waiting for it. It was done now
because of one property of the source, established in
[`rd-c`](rd-c-kis-flow-probe-result.md) §3.2 and confirmed again here:

> `inquire-time-dailychartprice` serves a **rolling ~250 trading days**
> that advances **one session every session**. Intraday history that is
> not collected is not merely inconvenient to obtain later — it is
> **gone**, permanently, one day at a time.

`rd-d` listed this as item 2 and it had not been done. At the time of
writing the store held **zero** KRX minute bars, so roughly a year of
Korean intraday history was sitting in a window that closes a little
every day.

---

## 1. Why it is worth the calls now, and not later

[`rd-l`](rd-l-what-it-would-cost-to-know.md) §5 established that the only
arithmetically reachable route to settling the open half of stage 2 is a
**cross-section**, not more calendar time on one instrument.
[`rd-n`](rd-n-stage3-separator-result.md) §8 put the same conclusion in
events: resolving a 12bp branch mean on those situations needs 1.3× to
6.8× the events, and that is a universe rather than a wait.

Korean intraday is the only cross-section this project can actually
assemble — and it is the only one with an expiry date. The KRX daily
window was spent by `ms-f` on 2026-09-13, and Binance spot 1m is a
single-instrument holdout being deliberately saved. So this is both the
scarcest and the most perishable material available.

**It commits nothing.** Collection is not selection: no situation is
defined, no threshold is chosen, and no window is designated for anything
until a specification says so.

## 2. What was measured, and the seven things the earlier probe did not have

Every figure below is from the live paper host on 2026-09-15, against
real KR-10 symbols.

### 2.1 A session is read backwards, page by page

The endpoint returns the **120 bars ending at `FID_INPUT_HOUR_1`**, so a
session is tiled backwards. For 005930 on 2026-09-11:

| `FID_INPUT_HOUR_1` | rows | of the requested date | span |
|---|---|---|---|
| `153000` | 120 | 120 | 13:21–15:30 |
| `132000` | 120 | 120 | 11:21–13:20 |
| `112000` | 120 | 120 | 09:21–11:20 |
| `092000` | 120 | **21** | 09:00–09:20 |

**381 bars for a full session** — 120 + 120 + 120 + 21 for the requested
date. Three pages would be cheaper and wrong —
`153000`/`132000`/`112000` leaves **09:00–09:20 missing**, which reads as
an ordinary quiet open rather than a hole.

### 2.2 The fourth page overruns into the previous session

**rd-c did not record this.** Requesting `092000` returns 120 rows of
which only 21 belong to the requested date; the other 99 are the previous
session's tail. Probed directly: `FID_INPUT_DATE_1=20260911`,
`FID_INPUT_HOUR_1=090000` returned `20260910132200 → 20260911090000`.

Those rows are **discarded**, not kept. They are real bars, correctly
stamped, and folding them in would write data under a session nobody
asked about — after which "which sessions have I fetched" stops having an
answer. `fetch_page` filters on `stck_bsop_date` so no caller can
accidentally keep them.

### 2.3 `acml_tr_pbmn` is cumulative here, not per-bar

On one session it runs **2,373,467,841,750 at 13:21 to
3,602,177,532,500 at 15:30** — a running total since the open, not the
bar's traded value. `KlineRow.quote_volume` is documented as *the bar's*
traded value, so **this column is not stored at all.**

It is recoverable by differencing consecutive bars of a **complete**
session. It is not done here because on a *partial* session the
difference across the hole is wrong and looks entirely plausible — the
class of error that survives every test that does not specifically look
for it. A disclosed gap is better than a plausible fabrication.

### 2.4 `output1` describes *now*, not the requested date

A 2026-09-15 call for the 2026-09-11 session returned
`output1.stck_prpr` = **250,500** against that session's real 15:30 close
of **259,500**. Only `output2` is read. Anything reading `output1` on a
historical fetch is reading today's quote.

### 2.5 A minute with no trade has no bar

007390 (네이처셀) returned its 120 rows spanning **13:15–15:30** where
005930's spanned 13:21–15:30 — sixteen minutes of that window simply had
no trades. Confirmed at session scale in the first real run: **005930
gives 381 bars a session, 007390 gives 351.**

So **381 is an upper bound, not an expectation**, and any completeness
check keyed to a bar count is wrong for the illiquid half of a universe.
(381 rather than 380: an earlier draft carried the *minute* arithmetic —
390 minutes less a ~10-minute closing auction — which this task's own
first measurement contradicts.)
Completeness here is therefore a **span** — bars reaching from ≤ 09:30 to
≥ 15:00 — which is what actually distinguishes "all four pages landed"
from "the run died halfway" while tolerating a late first trade.

### 2.6 KRX sessions are not all 09:00–15:30, and a fixed tiling loses the difference

**Found by the coverage report flagging two sessions inside the window**,
which is what the split-by-cause reporting exists for. Both 005930 and
068270 were missing 2026-01-02 and 2025-11-13 — and "missing" turned out
to mean *stored, and wrong*:

| session | why | real span | fixed tiling stored |
|---|---|---|---|
| **2025-11-13** | 수능, the national exam | **09:59 → 16:29** | 09:59 → 15:29, **60 bars lost** |
| 2026-01-02 | the year's first trading day | 09:59 → 15:29 | complete, by luck |
| an ordinary day | | 09:00 → 15:30 | complete |

KRX opens an hour late on 수능일 and on the year's first trading day, and
on 수능일 it **closes an hour late too**. A tiling anchored at `153000`
therefore never asked for 15:30–16:29, and what it stored looked exactly
like an ordinary session that happened to finish early. Nothing in a bar
count, a gap check or a span check would have shown it — only a
*calendar-relative* expectation, which is precisely what the reference
calendar gave.

So the hour boundaries are **computed from what came back**: paging starts
above any close (16:30) and each request after the first ends one minute
before the earliest row the previous returned, stopping when a page adds
nothing new for the date. Asking for a later hour than the market reached
costs nothing — an ordinary session asked at 16:30 still returns rows
ending 15:30.

**Verified by repair**, not by reasoning: forcing a refetch of 2025-11-13
took 005930 from `(321 bars, 09:59:05 … 15:29:05)` to
`(381, 09:59:05 … 16:29:05)`, recovering exactly the 60 lost bars, while
2026-01-02 stayed at 321 — so the fix does not over-fetch an ordinary
late-open day either.

**The seconds matter in the arithmetic.** That session's stamps carry
`:05`, and the next request has to end at `:05` too; zeroing them would
re-request the boundary bar and advance the walk by 59 seconds instead of
a minute.

### 2.7 A halted stock produces legitimately empty sessions

207940 (삼성바이오로직스) collected 234 sessions where every other symbol
collected 251, and the first coverage report called the difference 17
missing sessions inside the window.

It is not missing. **The stock was halted for 17 consecutive sessions,
2025-10-30 to 2025-11-21** — its daily bars carry `volume = 0` and a close
frozen at 1,796,999, and the intraday endpoint correctly returns nothing
for a day on which nothing traded.

So the pipeline was right and the *report* was wrong, which is the more
dangerous of the two: **a coverage report that cries wolf seventeen times
for one symbol is a report nobody reads the eighteenth time.** A missing
session is now classified three ways rather than two —

| class | meaning |
|---|---|
| older than the rolling window | expected, permanent |
| **halted** | the stock did not trade; there is nothing to fetch |
| inside the window, traded, absent | **a real failure** |

— with the halt read from the daily bars' own `volume = 0`, which is the
only local evidence that distinguishes "no trades" from "no fetch". After
the split, 207940 reports **233 collected + 17 halted, and zero real
gaps.**

This matters more for a full-universe scan than for KR-10: ten liquid
names produce one halted stretch, and 2,718 names would produce enough to
drown the signal entirely.

### 2.8 Two figures that make the whole thing affordable

- **Latency averages 0.57s**, not the 7–10s CLAUDE.md records for
  `/oauth2/tokenP` and `inquire-balance`. Measured across all ten KR-10
  symbols. The quotation endpoints are in a different performance class
  from the account ones.
- **All ten KR-10 symbols work.** rd-c §5 disclosed that every intraday
  probe had used 005930 and the other nine were assumed. They are now
  measured: 120 rows each.

At ~3s per session, **10 symbols × ~251 sessions ≈ 2.1 hours**.

## 3. The design, and the one thing it refuses to do

```
python -m data.backfill_kis_intraday --symbols <codes> --sessions 260
python -m data.backfill_kis_intraday --symbols <codes> --verify
```

**Resumable by construction.** A session already spanning the day is
skipped without an API call, so an interrupted run costs only the session
it died on.

**Oldest session first, and all symbols abreast of each other.** In a
rolling window it is the *oldest* session that expires next, so an
interrupted run must have secured those and may safely leave the newest —
they will still be there tomorrow. Iterating date-outer keeps every symbol
at one frontier, so an interruption leaves ten partial symbols covering a
common range rather than four complete symbols and six empty ones.

**The first implementation did the exact opposite** — newest-first,
symbol-outer — while its own docstring argued for perishability. The
reasoning was right and the loop contradicted it; caught on review of
PR #174.

**An empty result is reported, never interpreted.** A date outside the
rolling window returns `rt_cd=0` with zero rows — the same convention an
expired futures contract returns — so a backfill treating a successful
call as success records nothing and reports a clean run. `fetch_session`
returns `[]` and says nothing about why; whether that means "not a trading
day", "older than the window" or "something broke" is a question only a
calendar can answer.

**The calendar is the index daily series** (`KRX-INDEX:0001`), the method
`backfill_kis.py` and MS-C already established: an index prints exactly
when the market is open, so it needs no holiday table and covers the
moving lunar holidays `KrxMarketCalendar` still lists as unresolved.
`store.find_missing_ranges` remains unusable here — it diffs against an
arithmetic sequence and would report ~116 false gaps per symbol-year.

**Coverage splits missing sessions three ways**, and that split is the
point: *older than the rolling window* is expected and permanent,
*halted* means the stock did not trade and there is nothing to fetch, and
only *inside the window, traded, and absent* is a failure. Reporting them
together buries the third inside the first two. The horizon is taken from
the data — the oldest date anything was actually collected for — rather
than from the nominal 250, because the real boundary moves daily; the
halt is read from the daily bars' own `volume = 0`.

**No grid alignment is asserted.** 2026-01-02 really returned `:11`
seconds on every bar where every other probed date returned `:00`, so
stamps are stored exactly as given. A minute-alignment check copied from
the daily path would reject real data — the same reasoning that keeps the
funding endpoint's range validation from enforcing alignment.

## 4. What this does and does not establish

**Does not establish that Korean intraday contains anything.** No return,
no situation, no threshold, no test. This is a capability and a store.

**Does not designate a window.** Whether KRX intraday becomes a discovery
window is a decision under CLAUDE.md's Discovery/Confirmation split and
belongs to the operator, not to the act of collecting. Collection commits
nothing precisely because it selects nothing.

**Does not fix survivorship.** The KR-10 universe is ten currently-listed
names fixed by a day-one rule (`ms-e`), which bounds the exposure and does
not remove it. A full-universe intraday scan would reopen
[`rd-d`](rd-d-discovery-mode-and-the-full-universe.md) §2.2 in the same
unsolved form.

**Does not capture per-bar traded value**, for the reason in §2.3. If
거래대금 per minute is later needed, it comes from differencing a session
verified complete — not from what this endpoint hands over.

**Says nothing about the real host.** Paper only, like every KIS probe
this project has run. MS-C found the daily TRs identical across both
hosts, which makes the same plausible here and not verified.

## 5. Result

Run started 2026-09-15, ten KR-10 symbols, 260-session request against a
reference calendar spanning **2025-08-22 … 2026-09-15**.

**The run is in flight as this is committed, and the numbers below are
what it has produced so far rather than a final coverage table.** Said
plainly rather than left as a placeholder: a result section that reads as
complete when it is not is the failure this project keeps writing guards
against.

First measurements from the live run, confirming §2.5 at session scale:

| symbol | bars per session |
|---|---|
| 005930 삼성전자 | **381** |
| 007390 네이처셀 | **351** |

Sustained throughput is lower than the burst measurement in §2.6 — about
**25 minutes per symbol** rather than the ~13 the 0.57s average implied,
so the full ten is roughly **4 hours**, not 2.1. The estimate was taken
from a short burst and did not survive contact with a sustained run;
recorded because it is the figure anyone sizing the next collection will
reach for.

The final coverage table, per symbol and split by cause, is produced by:

```
python -m data.backfill_kis_intraday --symbols <codes> --verify
```

and is appended to this section when the run completes.

## 6. What follows

1. **Keep it current.** The window advances one session per session, so a
   collector that runs after each close is what stops this from decaying
   into the same problem again. The backfill is already idempotent and
   resumable, so a cron entry is the whole of it.
2. **The investor-flow series is the other perishable one**, and is
   already collecting (`rd-c`, `scripts/collect-krx-flow.sh`) — a rolling
   30-row horizon with no date parameter, so it cannot be backfilled at
   all. Intraday bars and investor flow are the two Korean series where
   *not collecting today* is a permanent loss.
3. **Nothing here proposes a study.** rd-n §8's stopping rule stands, and
   the next research step remains what it says: a power calculation from
   the event arm's own dispersion, before any family is specified.
