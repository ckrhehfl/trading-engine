# Multi-Asset TSMOM Task C — the KRX daily pipeline, and the second silent cap

**Status**: built and verified against the live KIS paper host from the
GCP instance, 2026-09-13. No universe resolved, no strategy run, no
backfill committed to the store yet.

Builds on [`ms-b-kis-history-probe-result.md`](ms-b-kis-history-probe-result.md).
Design and universe rule: [`ms-a-multi-asset-universe-discuss.md`](ms-a-multi-asset-universe-discuss.md).

---

## 1. The finding: the index endpoint caps at 50, not 100

MS-B measured the equity endpoint's silent 100-row cap and this module
was built against it. **The index endpoint's cap is 50**, and the first
version of the pipeline sailed straight through a truncated series
because its guard was calibrated to the wrong number.

Measured directly, same date range, same call shape:

| Endpoint | Requested | Returned | Span returned |
|---|---|---|---|
| `inquire-daily-itemchartprice` (005930) | 2024-01-01…04-29 | **81 bars** | 01-02 … 04-29 — complete |
| `inquire-daily-indexchartprice` (0001) | 2024-01-01…04-29 | **50 bars** | **02-16** … 04-29 — the newest 50 |

`rt_cd=0` in both cases. Nothing in the response says the second was
truncated. `FID_PW_DATA_INCU_YN` makes no difference at `Y`, `N`, or
absent — that parameter was the obvious suspect and it is not the cause.

The truncation keeps the **newest** rows and drops the oldest — BingX's
direction, not Binance futures'. So a naive backfill would have silently
produced a KOSPI series missing its early history in every window, and
the error would compound with window width rather than announce itself.

**The general lesson, which is the transferable part**: a cap is a
property of an *endpoint*, not of a *venue*. This project already knew
BingX and Binance cap differently; it now knows two endpoints on the same
host cap differently. One shared constant is a bug waiting for the second
endpoint.

### 1.1 How it got through, and what it broke

Both halves of the safety net failed, each for its own reason, and they
failed in a way that made the result look clean:

1. **The cap guard was one constant.** `len(rows) >= 100` never fires on
   a 50-row page.
2. **The gap check compared one direction.** `missing_trading_days`
   returned `reference - present`, so with a truncated KOSPI reference it
   reported **zero missing days** for Samsung — while silently discarding
   the 31 dates Samsung traded and the reference did not. Those 31 dates
   were the proof the reference was broken, and the function threw them
   away.

**Half a diff is not a check.** A stock cannot trade on a day the market
index did not print; if it appears to, the reference is wrong, not the
stock. `missing_trading_days` now raises `ReferenceCalendarError` on that
condition rather than returning a reassuring empty list.

---

## 2. What was built

`python/data/kis_klines.py` — the durable client, as distinct from
`kis_probe.py`, which was the one-off Phase 0 investigation.

- **Per-endpoint caps and windows.** Equities 100 rows / 120-day pages;
  indices 50 rows / 60-day pages. A page that reaches its own cap raises
  rather than returning a partial series.
- **`adjusted` is required with no default.** `FID_ORG_ADJ_PRC` is `0`
  for 수정주가, `1` for 원주가, and KIS's own published sample defaults to
  raw. A caller must state which series it wants; there is no value the
  language will supply on its behalf.
- **Bounded retry.** `HTTP 500` from KIS is transient — MS-B's own first
  run concluded the KOSPI200 code was unsupported on the strength of one.
- **`rt_cd != "0"` raises**, and every missing or unparseable field
  raises rather than becoming `None`. KIS's per-endpoint field casing
  already made three endpoints parse as silent nulls in this project once
  (`KisAdapter`, PR #103); `.get()` is the Python shape of the same
  mistake.
- **OHLC internal consistency** is asserted per bar.
- **One token per session.** `KisSession` holds it, so a whole backfill
  is structurally one issuance. `EGW00133` fires after roughly three in a
  few minutes and the allowance is shared with the live `kis-paper` JVM.
- **Storage symbols** are `KRX:005930` and `KRX-INDEX:2001`, following
  the existing `BINANCE:BTCUSDT` convention. Separate namespaces because
  they are separate endpoints.

### 2.1 A KRX trading date is exactly UTC midnight

KRX's continuous session opens 09:00 KST; KST is UTC+9. So a bar dated
`20240502` opens at `2024-05-02T00:00:00Z` — an equality, not a rounding.
That is why `1d`'s existing 86,400,000 ms grid alignment holds for KRX
with no special case, and it was verified rather than assumed: every bar
of a real 121-bar fetch satisfies `open_time_ms % 86_400_000 == 0`.

### 2.2 `quote_volume`, and why the store gained a column

The KR-10 universe rule ranks on 2018 traded value (거래대금), which KIS
returns as `acml_tr_pbmn` on the same call as the prices. `close ×
volume` is **not** a substitute — traded value is price times quantity
summed across the day's trades, not the closing price times the day's
total.

`klines` therefore gains a nullable `quote_volume`, through the same
additive `_ensure_klines_columns` mechanism the two order-flow columns
already use. `NULL` for every existing row. **Verified against a copy of
the real production database**: 4,620,925 rows before and after, one
column added, existing bars read back unchanged.

Binance's own wire format carries the same quantity at index 7
(`quote_asset_volume`) and currently discards it. Retrofitting that is
deliberately **not** done here — it is a separate change with its own
backfill implications.

---

## 3. Verified against the real API

From the GCP instance, paper host, 2024-01-01…06-30:

| Check | Result |
|---|---|
| 삼성전자, paged | **121 bars**, 2024-01-02…06-28, ascending, no duplicates |
| Every bar UTC-midnight aligned | yes |
| `quote_volume` present | ₩1,356,958,225,913 on the first bar |
| KOSPI index, same range | **121 bars** — matching exactly, after the cap fix (was 90 before) |
| Gap check, both directions | **0 missing, 0 impossible** |
| An arithmetic grid would have reported | **58 false gaps in six months** (~116/year) |

**The adjusted-price trap, on real data:**

| | 2018-05-03 | 2018-05-04 |
|---|---|---|
| 수정주가 (`FID_ORG_ADJ_PRC=0`) | 53,000 | 51,900 |
| 원주가 (`=1`) | **2,650,000** | 51,900 |

The raw series shows a −98% single day across Samsung's 50:1 split. A
momentum signal reads that as a crash. This is the concrete form of the
danger MS-A §5 item 6 named, and the reason `adjusted` has no default.

---

## 4. Guards, each shown to fail when removed

Per CLAUDE.md's change checks — "remove the guard and watch the test
fail before believing it", because reading one is not enough and this
repository has had three inert fixtures that all read fine.

| Guard removed | Result |
|---|---|
| equity row-cap check | 1 test fails |
| page-boundary de-duplication | 1 test fails |
| OHLC consistency | 1 test fails |
| `rt_cd` check | 1 test fails |
| index cap reverted to 100 | 2 tests fail |
| index window widened to 120 days | 1 test fails |
| reverse direction of the reference check | 1 test fails |
| 4xx fast-fail (post-review) | 3 tests fail |
| invalid-calendar-date conversion (post-review) | 5 tests fail |
| `ImportFrom` half of the import guard (post-review) | 4 tests fail |

49 tests in `python/tests/test_kis_klines.py` and 26 in
`python/tests/test_kis_probe_cannot_trade.py`; the credentialed-client guard there now covers `kis_klines.py` as well
as the probe, so the order-capability and credential-sink contracts apply
to both.

---

## 4.1 Corrections made on review

Four findings on PR #165, all valid, and two of them were wrong claims
rather than missing hardening:

- **`_get_with_retry` retried 4xx.** A `400`, `403` or `429` is the
  server saying the request itself is wrong; retrying cannot fix it, and
  retrying a `403` spends more of the token allowance the live
  `kis-paper` JVM shares. Only 5xx and transport faults are retried now.
- **Indices do carry a traded value, and the code claimed they did not.**
  The docstring asserted index bars have no `acml_tr_pbmn` and hardcoded
  `quote_volume = None`. Checked against the live endpoint: index
  `output2` is exactly `[stck_bsop_date, bstp_nmix_{oprc,hgpr,lwpr,prpr},
  acml_vol, acml_tr_pbmn, mod_yn]` — the same volume pair as an equity.
  A real field was being discarded on an untrue claim. **The units
  differ** (KOSPI 2024-05-02 `acml_vol` 613,718 against 삼성전자's
  26,198,776), so the two namespaces must never be ranked or summed
  together, and that is now stated where the parsing happens.
- **`trading_date_to_ms` leaked `ValueError`** for eight digits that are
  not a real date (`20241332`), and `iter_daily_range` leaked it for a
  short bound. Shape is not validity; both raise `KisKlinesError` now.
- **The trading-path import guard was a substring scan** and passed six
  of eight real spellings — `import execution`, `from oms import x`,
  `from . import execution` among them. Replaced with an `ast` walk over
  `Import`/`ImportFrom`, with all eight as known-bad fixtures. This is
  the second bypass found in that one test file; the first was a
  triple-quoted string. **A guard written as text matching keeps losing
  to the language's own flexibility.**

## 4.2 The futures symbol master, and a CDN that will not be relied on

KIS publishes symbol masters as public ZIPs at
`new.real.download.dws.co.kr/common/master/`, no credentials. Two matter
here, and **both were downloaded and inspected successfully**:

`fo_stk_code_mts.mst` (14,701 rows) and `fo_idx_code_mts.mst` (8,241),
both pipe-delimited and CP949. Their first lines:

```
1|1GNW04|KR41GNW40002|금양       F 202504 (  10)| |00000.00|1|001570|금양
1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200
```
 The layout answers two open questions at once:

- **Field [3] carries the contract multiplier in parentheses** — `(  10)`,
  ten shares per contract for that name. This is the fact that has been
  blocking `KIS_MARKET_DIVISION=STOCK_FUTURES` from starting since PR
  #105, and it is published rather than needing to be inferred.
- **Field [7] is the underlying equity code** (`001570`), field [8] its
  name. So the file *is* a futures-eligibility universe: every underlying
  with a listed single-stock future, which is precisely the candidate
  pool MS-A §4.4 needed.
- The index master maps a futures code to its underlying index
  (`A01612` → `2001` KOSPI200), and `A016` + `YYMM` is the same shape as
  `A01609`, the symbol the live `kis-paper` loop already trades.

**The download is unreliable and must be cached, not fetched on demand.**
Two successful pulls were followed by `HTTP 404` on every subsequent
attempt — six retries with backoff over two minutes, from both a
workstation and the instance. The file plainly exists; the CDN simply
stops serving it for a while. This is the same shape as BingX's
funding-rate endpoint returning `data: null` on repeated identical calls.
**Consequence for MS-E**: fetch the master once, commit or cache the
parsed result, and never put a live download on the critical path of a
universe resolution.

**Not yet established**, because the retries failed: the count of unique
underlyings, the multiplier distribution across them, and whether any
underlying carries more than one multiplier. The parse is written; it
needs one successful download.

## 5. Still open

- **The backfill itself.** Nothing is in the store yet. That needs the
  universe resolved (MS-E) or at least a candidate pool.
- **The candidate pool** (MS-A §4.4, MS-B §7). KIS's symbol master
  (`kospi_code.mst`) is a **current** snapshot, so a pool drawn from it
  is survivorship-filtered. It does carry 상장일자, which removes the
  forward direction — a name that listed after 2019 can be excluded — but
  not the backward one.
- **Single-stock futures**: multiplier, per-underlying liquidity, and the
  earliest bar that sets §2.1's tradeable window. Still the fact that
  blocks `STOCK_FUTURES` from starting, and still unmeasured.
- **Measured Korean correlations**, which MS-A §2.4 assumed and owes.
  Cheap once a pool is backfilled, and it decides whether §2.4's
  conclusion holds.
