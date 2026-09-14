# Research Direction Task C — KIS flow & intraday probe result

**Run 2026-09-14 against the live KIS paper host, on the GCP instance**
(`paper-trading`, us-central1-a), per CLAUDE.md's *"Run it where it will
run."* Read-only, quotation TRs only, no order path, nothing written to
the kline store. Module: `python/data/kis_flow_probe.py`.

It answers the two questions
[`rd-c-mechanism-catalogue.md`](rd-c-mechanism-catalogue.md) §8 put first,
and **both turned out to be clocks.** One of the two is more urgent than
the catalogue assumed and the other is far less so.

---

## 1. The headline

| Question | Measured answer | Backfillable? |
|---|---|---|
| **투자자별 매매동향** (개인/기관/외국인 daily net buying, per stock) | **exactly 30 rows**, 2026-08-03 … 2026-09-14, **no date parameter exists** | **No.** Collector must start now |
| **Intraday minute bars, past sessions** | **~250 trading days**, rolling — boundary pinned to the day | **Yes, to ~1 trading year.** Then it rolls off |

**Both were unknown before this run.** The catalogue's §6 relayed "최근
30일" from vendor documentation; that is now measured. Its §7 said KRX
intraday retention "has never been measured" and treated every intraday
mechanism as untestable on Korean names; **that was too pessimistic** and
is corrected below.

## 2. 투자자별 매매동향 — `inquire-investor`

`GET /uapi/domestic-stock/v1/quotations/inquire-investor`, `tr_id`
`FHKST01010900`, `FID_COND_MRKT_DIV_CODE=J`.

| | 005930 삼성전자 | 000660 SK하이닉스 |
|---|---|---|
| `rt_cd` | `0` 정상처리 | `0` 정상처리 |
| rows | **30** | **30** |
| earliest → latest | 20260803 → 20260914 | 20260803 → 20260914 |

**The "30" is a horizon, not a page cap, and the distinction is what
matters.** The daily-chart endpoints cap at 100 (equity) and 50 (index)
but take `FID_INPUT_DATE_1`/`_2`, so a caller pages backwards through
history. **`inquire-investor` accepts no date parameter at all**, so
there is no request that reaches day 31. Whether some *other* KIS endpoint
serves deeper investor history is a separate question this probe did not
ask.

**The response is richer than net quantity**, which was not known either.
Every row carries, for each of 개인 / 외국인 / 기관 separately:

| Suffix | Meaning |
|---|---|
| `*_ntby_qty` | net buy **quantity** — `prsn_`, `frgn_`, `orgn_` all present |
| `*_ntby_tr_pbmn` | net buy **거래대금** (value) |
| `*_shnu_vol` / `*_seln_vol` | gross **buy** and **sell** volume, separately |
| `*_shnu_tr_pbmn` / `*_seln_tr_pbmn` | gross buy and sell value |

Gross buy and sell being separate, rather than only their difference, is
the part with research value: it makes **turnover by investor type**
observable, not just direction. Kelley & Tetlock's central finding —
that retail *market* orders and retail *limit* orders are two different
populations with opposite behaviour — needs exactly this kind of
decomposition to be approached at all.

`foreign-institution-total` (`FHPTJ04400000`) also returned `rt_cd=0`
with **30 rows**, and carries a finer institutional breakdown still —
`fund_`, `insu_`, `bank_`, `ivtr_`, `mrbn_`, `etc_orgt_`, `etc_corp_`.
That is the **cross-sectional** shape (one day, many stocks) against
`inquire-investor`'s **time-series** shape (one stock, many days).

## 3. Intraday — two different endpoints, and only one of them has history

### 3.1 `inquire-time-itemchartprice` (`FHKST03010200`) — current session only

30 rows per call; `FID_INPUT_HOUR_1=153000` returned 15:01 → 15:30 of
**2026-09-14**, the current session. **It takes a time but no date**, so
it can only ever describe today. Useful for a live collector, useless for
history.

### 3.2 `inquire-time-dailychartprice` (`FHKST03010230`) — the one with history

Takes `FID_INPUT_DATE_1`. **120 rows per call**, and the returned
`stck_bsop_date` matched the requested date on every successful call —
checked explicitly, because MS-B already hit a case where KIS served
something other than what was asked for.

**The retention boundary, pinned to the day:**

| Requested session | Rows |
|---|---|
| 20260102, 20251215, 20251201, 20251117, 20251103, 20251015, 20251001 | 120 |
| 20250915, 20250912, 20250911, 20250910, 20250909, 20250908, 20250905, 20250904 | 120 |
| **20250903** | **120** |
| **20250902** | **0** |
| 20240902 | 0 |

Every date above is a real KRX trading day, taken from the KR-10 series
already in the store — an empty result therefore cannot be an accidentally
requested holiday.

**2025-09-03 is the floor. From the 2026-09-14 run date that is 376
calendar days and ~251 trading days.** ~251 rather than ~376 is the
informative number: the window is a **rolling trading-day count of about
250 — one trading year — not a calendar cutoff.**

**The consequence is the one that matters**: the far edge moves forward
by one session every session. Intraday history that is not collected is
not merely inconvenient to obtain later — **it is gone.** Roughly 250
trading days are available right now and will not be available in a year.

### 3.3 How many calls a backfill costs

The 13:21 → 15:30 window returned **120 rows for 130 minutes**. The
10-bar shortfall lines up exactly with **15:20–15:30, the closing call
auction (동시호가)**, during which there is no continuous trading and so
no minute bar. Consistent, explainable, and not a gap in the data.

So a full regular session is 09:00–15:30 = 390 minutes, less the ~10-minute
closing auction ≈ **380 bars**, at 120 per call ≈ **4 calls per symbol per
session**.

| | |
|---|---|
| One symbol, ~250 sessions | ~1,000 calls |
| **KR-10's ten symbols** | **~10,000 calls, ~95,000 bars per symbol** |

## 4. Two traps a pipeline built on this must handle

1. **An empty result is `rt_cd=0`, not an error.** 20250902 and 20240902
   both returned 정상처리 with zero rows — the same convention every
   expired futures contract returned in MS-B §2.1. A backfill that treats
   `rt_cd=0` as success and moves on would **silently record nothing** for
   every out-of-range date and report a clean run. The coverage check must
   be against the index calendar, exactly as `backfill_kis.py` already
   does for daily bars.
2. **Bar timestamps are not uniformly on the minute grid.** 2026-01-02
   returned `132011` … `152911` — seconds `:11` — while every other date
   probed returned clean `:00`. Whatever the cause, **`stck_cntg_hour`
   cannot be assumed to be a clean `HHMM00`**, and a grid-alignment check
   copied from the daily path would reject real data. This is the same
   class of finding as the funding endpoint's off-grid `fundingTime`
   stretch, which is why that one's range validation deliberately does not
   enforce alignment.

## 5. What this probe did not establish

- **Paper host only.** Whether the real host serves deeper history was not
  asked. MS-C found the daily TRs are identical across both hosts, unlike
  the trading TRs' `V`-prefix convention, so identical behaviour is
  *plausible* here — and plausible is not verified.
- **One symbol for intraday.** All `FHKST03010230` probes used 005930. The
  other nine KR-10 names are assumed, not measured.
- **Index intraday is unprobed.** KOSPI/KOSPI200 minute bars would be the
  natural reference calendar for an intraday backfill, exactly as the daily
  index is today, and no call was made for one.
- **Whether any other endpoint serves investor history beyond 30 days.**
  Only `inquire-investor` and `foreign-institution-total` were tried.
- **Nothing about whether any of this data contains an edge.** This is a
  capability measurement. No return of any kind was computed.

## 6. What follows

| | Action | Urgency |
|---|---|---|
| **1** | **Start an investor-flow collector.** ~30 trading days of runway; every day not collected is permanently absent. Same argument, same shape, as `binance_positioning.py` | **highest — days** |
| **2** | **Backfill ~250 sessions of KR-10 intraday** (~10,000 calls), then keep it current | high — the far edge rolls off daily |
| **3** | Probe the gaps in §5 before building either pipeline — real host, remaining symbols, an index for the calendar | before, not during |

**What this changes in the catalogue**: §7's claim that *"every intraday
mechanism is currently untestable on the Korean universe"* is now wrong in
the favourable direction. About one trading year of KR-10 intraday is
obtainable, which is roughly 95,000 bars per symbol across ten symbols.
That is **thin for a Sharpe-scored always-on strategy** — a one-year window
implies a detection floor near 1.645 — and **adequate for the event study
rd-b actually proposes**: a situation firing 20× per symbol-year gives
n ≈ 200 across ten names, which is a t of about 2.1 on a 0.3% effect. The
instrument fits the question; it would not have fit the old one.
