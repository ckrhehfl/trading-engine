# Exchange API behaviour — what these venues actually did, when we looked

**This file is replaced, not appended to.** Every figure here was true when
observed and several are known to drift, so **verify before relying on any of
it**. Re-run the probe rather than trusting a number's age.

That drift is exactly why this belongs here rather than in `CLAUDE.md`. A
venue's row cap, retention depth or response shape is a **living** fact — it
changes when the venue changes — while `CLAUDE.md` holds what may never be
violated. Both were in `CLAUDE.md` until 2026-09-26.

**What is deliberately NOT here: the rules these measurements produced.** Every
trap below has a standing rule attached, and those live in `CLAUDE.md`'s
Exchange API Facts section, which is shorter than this file for that reason. §7
maps them. Where a rule needs a number, the number is here and the rule points.

Sources and their roles:

| venue | role |
|---|---|
| **BingX** | the first and only `ExchangeAdapter` with a paper/live path |
| **Binance** | read-only historical data for research. No credentials, no order placement, no plan to become a trading venue |
| **KIS** | the third paper-trading loop (KOSPI200 index futures) |
| **KRX OpenAPI** | read-only dated stock trading and basic information for universe research |

---

## 1. BingX — verified against the live public API

| Item | Value |
|---|---|
| Symbol | `BTC-USDT` |
| Recent trades | `GET /openApi/swap/v2/quote/trades` |
| Klines | `GET /openApi/swap/v3/quote/klines` |
| Range semantics | `startTime`/`endTime` half-open (`startTime <= t < endTime`), aligned to the interval grid (900,000 ms for 15m), max 1000 candles per request |
| `limit` | Not a count guarantee — a request over 1000 is silently capped |
| Over-limit capping | Keeps the **newest** rows, closest to `endTime` |

### Kline retention, and it is not monotonic in granularity

Re-measured rather than extrapolated. Expect every figure to drift forward.

| Interval | Earliest bar | Span / count | How |
|---|---|---|---|
| `1d` | 2021-05-14T00:00:00Z | 5.21 y, **1,901 bars, zero gaps** | full backfill (`sr-t`). Bars sit on the UTC-midnight 86,400,000 ms grid — BingX does not open its daily candle at a local offset |
| `1h` | 2024-04-27T10:00:00Z | 819.9 d, **19,678 bars, zero gaps** | full backfill (`sr-f`) |
| `1m` | 2024-11-30T16:00:00Z | 631.98 d, **910,040 bars, 2 real gaps** | full backfill (`scalp-s0-s3`); the binary-search estimate was exact, reproduced bar for bar |
| `15m` | ~2025-11-16 | ~8.3 months | probe (`sr-a`) |
| `5m` | ~2026-05-02 | ~3 months | probe only, never backfilled |

`1m` is **deeper than both `15m` and `5m` despite being finer**, which retires
the earlier "finer granularity means shorter retention" reading of the four
coarser intervals.

**The two real `1m` gaps**, half-open as everywhere else in this project:
`[2025-04-25T06:54:00Z, 2025-04-25T06:57:00Z)` (3 bars) and
`[2026-02-13T20:32:00Z, 2026-02-13T20:36:00Z)` (4 bars). Confirmed genuinely
absent via 5 consecutive retries each, not a fetch artifact. The delay each
introduces at a fill is 4 and 5 real minutes respectively.

### Funding rate

`GET /openApi/swap/v2/quote/fundingRate?symbol=BTC-USDT` (v2, public). Same
`{"code","msg","data"}` envelope as everything else **except an empty result is
`data: null`, not `[]`**. Newest-first, silently capped on an over-wide
request — but `limit` over 1000 is a hard server error (`code: 109400`), unlike
klines' silent clamp.

`data: null` is **flaky near the retention edge**: a range with known-good data
returned `null` on ~1-in-6 to 1-in-2 of identical repeated calls (2026-07-27),
and worse on re-probe a day later — 15 of 15 nulls for a range already cached
locally. Consistent with a rolling window genuinely moving, not just a flaky
server.

Real depth reaches **2020-11-29T12:00:00Z (6,199 rows)**, far deeper than
klines at any granularity. Three small gaps of 4–16 h sit at that earliest
boundary and survived 3+ reruns.

**Historical `fundingTime` is not always on the modern 8 h / 28,800,000 ms
grid** — a 2020-11-29 to 2021-01-05 stretch settles 4 h off it, plus one
isolated row.

Sign convention, from BingX's own docs: `fundingRate > 0` → longs pay shorts;
`< 0` → shorts pay longs. Detail: `sr-m`, `metrics/position.py`.

---

## 2. BingX — verified against the live VST (demo) API with a real key

Envelope is `{"code": 0, "msg": "", "data": ...}`, sometimes with a top-level
`timestamp`.

| Call | Real observed shape |
|---|---|
| `GET /openApi/swap/v3/user/balance` | `data` is an **array** of per-asset objects (`userId`, `asset`, `balance`, `equity`, `unrealizedProfit`, `realizedProfit`, `availableMargin`, `usedMargin`, `frozenMargin`, `shortUid`) — not a single object. Parsing must index into it |
| `GET /openApi/swap/v2/user/positions` | Also an array; `[]` when flat |
| `GET`/`POST /openApi/swap/v1/positionSide/dual` | `dualSidePosition` came back `"true"` on a fresh key — hedge mode is the default, previously undocumented. Set it explicitly at startup anyway; a default can change |

### The first real order, 2026-08-09

Placed, filled and cancelled through the full OMS-mediated path. Detail:
`.planning/paper-trading-h-vst-integration.md`.

- `POST /openApi/swap/v2/trade/order`'s **submit** response already reported
  `"status":"FILLED"` for a market order. `ExchangeOrderExecutor.submit` never
  trusts that, so the ~1.5 s ack-to-fill latency observed reflects this
  project's own polling cadence, not real exchange latency.
- `queryOrder` carries a real **`commission`** field (e.g. `"-0.032441"`,
  negative = fee charged). For that trade it landed within ~5 bps of the
  modelled figure — 0.03244075 modelled against 0.032441 real. One data point,
  not proof the two always agree.
- `DELETE /openApi/swap/v2/trade/order` on an unfilled limit order returned the
  status token **`"CANCELLED"`** (double-L), confirming the REST half of the
  documented REST/WebSocket casing inconsistency. WebSocket's `"CANCELED"`
  remains unverified — no WS call has ever been made by this project.
- **Duplicate `clientOrderID` is rejected server-side**:
  `{"code":101400,"msg":"clientOrderID unique check failed"}`, from a genuinely
  separate graph simulating a restarted process. Observed, not officially
  documented.
- **Account-wide leverage read `"20X"`** on a fresh VST account, unenforced,
  because nothing called `POST /openApi/swap/v2/trade/leverage`.

**The clean-start preflight branch finally ran against the real API on
2026-08-26**, observed in a live VST startup log:
`VstPreflight: real VST balance=96224.4301 … no pre-existing non-zero positions
found, clean start … real exchange-side leverage for BTC-USDT set to 1x (LONG
and SHORT, hedge mode)`. It had been outstanding because the account still held
a position from the original run and this codebase's OMS path has no way to
close one — in hedge mode a `SHORT` opens a second position rather than closing
the `LONG`. The pre-existing-position branch remains fake-adapter-verified
only; it cannot be exercised without deliberately opening a position first.

### The truncation incident, 2026-09-08

`BTC-USDT`'s published contract spec, `GET /openApi/swap/v2/quote/contracts`,
re-verified live 2026-09-08:

| field | value |
|---|---|
| `size` | 0.0001 |
| `quantityPrecision` | 4 |
| `tradeMinQuantity` | 0.0001 |
| `tradeMinUSDT` | 2 |

**A 29-fractional-digit quantity was accepted, then filled at exactly that
value truncated to 4 dp** — BingX silently truncates an over-precise quantity
rather than rejecting one. The order reported `FILLED` at the venue while this
project's own approved quantity was larger, so the executor refused to
reconcile the two, the `Reconciler` found `ORPHANED_IN_BROKER`, and the kill
switch tripped and stayed tripped. Full account:
`.planning/quantity-precision-discuss.md` (issue #151, PRs #153–#155).

**Verified end to end against the real venue on 2026-09-09**, both directions:
the incident's own 29-digit quantity is now rejected before any venue call, and
a step-valid `0.0001` order reached `FILLED` with BingX's own record
(`amt=0.0001 avgPrice=79380.8 leverage=1`) identical to the approved quantity.
Second real order this project has placed, and the first where our state and
the venue's were confirmed to agree.

### `code=109400 msg=timestamp is invalid` was not clock drift

Observed 2026-09-09 when both loop JVMs cold-started together on the 955 MB
instance while the previous Gradle JVMs were still winding down. The clock was
fine — 392 ms from BingX's own `serverTime`, NTP synchronised — and the
identical `getBalance` call succeeded seconds later once load eased. BingX's 5 s
signing window was simply lost to startup contention.

### A credential-handling incident, root-caused

A CRLF-terminated `.env` sourced naively left a trailing `\r` on
`BINGX_API_KEY`; the JDK's `HttpRequest.Builder#header` rejects a raw `\r`
(RFC 7230) with an exception **whose message embeds the offending value
verbatim**, writing the real key into a local gitignored scratch log. A
separate `cat -A` diagnostic similarly surfaced `FRED_API_KEY` in a tool
transcript. **Neither reached any committed file, git history, or public
surface.** `BINGX_API_SECRET` was never used as a header value — HMAC only,
never transmitted — and was confirmed unaffected.

---

## 3. BingX — documented but NOT empirically verified

Read from BingX's docs, never called with a real key. Lower confidence than
everything above.

- **Base URLs**: `https://open-api.bingx.com` (production) against
  `https://open-api-vst.bingx.com` (VST demo — virtual USDT, same signing
  scheme, real matching behaviour). A key made through the normal API
  Management flow authenticates against VST; whether the same key *also* works
  against production is untested and deliberately not tested.
- **Auth**: `X-BX-APIKEY` header plus HMAC-SHA256 over all params including
  `timestamp`, sorted alphabetically, joined `key=value&…`, hex uppercase,
  appended as `&signature=…`. Requests must be within 5 s of server time
  (`GET /openApi/swap/v2/server/time`).
- **Orders**: `POST /openApi/swap/v2/trade/order` (a type field selects
  MARKET/LIMIT/etc.); `POST .../order/test` validates without executing;
  `DELETE /openApi/swap/v2/trade/order` cancels.
- **Position mode** is account-wide, not per-symbol, and cannot change while
  any position or open order exists. Leverage takes `side=BOTH` in one-way
  mode, `LONG`/`SHORT` in hedge mode.
- **Endpoint versions are mixed within one family on purpose**: balance v3,
  positions/order/leverage v2, position-mode v1 — matching klines (v3) against
  trades (v2).
- **Private WebSocket** shares the public market-data host with `?listenKey=…`,
  from `POST /openApi/user/auth/userDataStream` (1 h TTL, `PUT` to refresh).
- **Rate limits** are per-account (UID): order place/cancel 10/s, order query
  30/s, positions 10/s, balance 5/s, leverage 5/s. A changelog claims IP-based
  limits were removed 2025-12-16 while the docs UI still shows legacy numbers —
  trust neither without testing.
- **Known internal doc contradictions**, to test rather than trust: order
  status casing `CANCELLED` (REST) against `CANCELED` (WS); the listen-key
  sample omits signature params its own metadata requires; the WS connection
  limit is stated as both 60/IP and 240/IP.

---

## 4. Binance — data-research source only

No credentials, no order placement, read-only public klines
(`data/binance_klines.py`, `backfill_binance.py`; `sr-z`, `scalp-s5`).

| Item | Value |
|---|---|
| Symbol | `BTCUSDT` — no dash, differs from BingX |
| Spot klines | `GET https://api.binance.com/api/v3/klines` |
| USDT-M futures klines | `GET https://fapi.binance.com/fapi/v1/klines` |
| Response | A **bare JSON array of arrays, by position** — not an object envelope: `[open_time_ms, open, high, low, close, volume, close_time_ms, quote_asset_volume, num_trades, taker_buy_base_volume, taker_buy_quote_volume, ignore]`. Timestamps and `num_trades` are bare integers; OHLCV are quoted strings |
| `endTime` | **INCLUSIVE**, not half-open — confirmed by a `startTime == endTime` request returning exactly one row. A real wire-level divergence from this project's convention, absorbed via `endTime = end_ms - 1` |
| Over-limit capping | Keeps the **OLDEST** rows, closest to `startTime` — the opposite of BingX. Rows therefore come back ascending, not newest-first |
| Max `limit` | Spot **1000**, silently capped. Futures **1500**, enforced as a real `HTTP 400` (`-1130`) — futures rejects, spot does not |
| Pre-listing range | Returns `[]` — not an error, not padded |
| Errors | A real non-2xx status with `{"code": <int>, "msg": "…"}`, never a `200` carrying an embedded error the way BingX works |

### Retention, by full backfill with independently verified gap counts

| Market / interval | Earliest bar | Count | Gaps |
|---|---|---|---|
| Spot `1d` | 2017-08-17T00:00:00Z | 3,275 | **0** |
| Futures `1d` | 2019-09-08T00:00:00Z | 2,523 | **0** |
| Futures `1m` | 2019-09-08T17:57:00Z | **3,661,780** | **1** (`[2019-09-08T19:00:00Z, 2019-09-08T19:01:00Z)`) |

Futures `1m` reaching essentially the market's own launch means it is **not a
rolling window at all** — a structural difference from every other retention
figure here. Spot `1d` spans ~8.97 years.

### The two order-flow columns

`taker_buy_base_volume` / `taker_buy_quote_volume`, wire indices 9 and 10, are
real, populated and order-flow-relevant. They were silently discarded by
`_parse_row` from `sr-z` until `scalp-s5` captured them into two additive
nullable `klines` columns — `NULL` for every BingX row, that wire having no
buyer/seller breakdown at all, and for every pre-`scalp-s5` Binance row.
Non-`NULL` for all 3,661,780 futures `1m` rows, with zero
`taker_buy_base_volume > volume` violations.

### Rate limits, live-confirmed

Fetched from each host's own `GET .../exchangeInfo` `rateLimits`: spot
`REQUEST_WEIGHT` **6000/min** per IP, futures **2400/min** per IP. Per-request
weight costs — spot a flat 2; futures tiered 1/2/5/10 by `limit` bucket — come
from Binance's docs rather than being re-derived here, so are held to slightly
lower confidence. **HTTP 418** is Binance's documented temporary-IP-ban signal,
distinct from `429`; not observed live.

### Binance geo-blocks the GCP instance entirely

Verified 2026-09-14 from `paper-trading` (us-central1-a): **every** Binance
endpoint returns **HTTP 451** — not just `/futures/data/` but plain
`fapi/v1/klines` too — with *"Service unavailable from a restricted
location"*, and the instance's egress IP geolocates to **US**. KIS/KRX is the
opposite from the same host: `HTTP 200`, re-verified 2026-09-17.

### Computed statistics, not API facts

Binance spot against BingX daily closes over their full 1,909-day overlap
correlate at **1.000000**; daily log-returns at **0.999955**. That shows the two
**price series** are tightly linked; it does not show a signal developed on one
transfers profitably to the other, which also depends on volume, funding,
basis, execution costs and timing, none of which a price correlation measures.

Binance spot-against-futures basis over 2,523 common days: mean
`(futures-spot)/spot` −0.0154%, stdev 0.0652%, range −0.74% to +1.80%,
narrowing over time — consistent with a maturing derivatives market rather than
a data-quality problem.

---

## 5. KIS — verified against the live paper (모의투자) API

First real contact 2026-08-21/24 (PR #103); the Phase 1 design was
fake-server-verified only until then. Full account:
`.planning/kis-phase1-venue-integration.md`.

### Response shape

- **Field-name casing is per-endpoint, not one convention.** `order` (submit)
  responds UPPERCASE (`ODNO`); `inquire-balance` and `inquire-ccnl` respond
  lowercase (`pdno`, `cblc_qty`, `odno`, `ord_qty`, `tot_ccld_qty`, …). Request
  parameters are always UPPERCASE regardless. The original implementation
  guessed uppercase for all three; the two wrong guesses parsed **every field as
  silently `null`**, since Jackson cannot distinguish a missing field from a
  wrong-cased one.
- **`inquire-deposit` (`CTRP6550R`) has no working paper TR id at all.** The
  real id returns `HTTP 500`/`EGW00205`; a `V`-prefixed variant following KIS's
  own real→paper convention returns `OPSQ0002` ("no such service code").
  `getBalance()` reuses `inquire-balance`/`VTFO6118R` and reads `output2`.
- **`inquire-balance` requires `CTX_AREA_FK200`/`CTX_AREA_NK200` even on a call
  that never paginates** — omitting either gives `OPSQ2001`.
- **`inquire-balance` genuinely paginates**, max 20 rows per call.
  `MAX_INQUIRE_PAGES = 10` bounds the continuation loop, shared with
  `queryOrder`.
- **`tr_cont`**: `"M"` means more pages; anything else, including the real
  observed `"F"`, means stop.

### `output2` → `BalanceSnapshot`, KIS's own column names

No exact 1:1 semantic match for every field:

| KIS column | maps to |
|---|---|
| `tot_dncl_amt` (총예수금액) | `balance` |
| `prsm_dpast_amt` (추정예탁자산금액) | `equity` |
| `ord_psbl_cash` (주문가능현금) | `availableMargin` |
| `mgna_tota` (증거금총액) | `usedMargin` |
| `evlu_pfls_amt_smtl` (평가손익금액합계) | `unrealizedProfit` |

### Latency and rate limits

- **Real observed latency is 7–10 seconds** for both `POST /oauth2/tokenP` and
  `inquire-balance` — the cause of intermittent `HttpTimeoutException`s against
  the original 10 s timeout, since widened to 20 s. KIS's paper host is
  meaningfully slower than BingX's.
- **`/oauth2/tokenP` has a real rate limit** (`EGW00133`), triggered by
  repeated token requests within roughly a minute. `KisTokenProvider` caches per
  JVM process, so repeated restarts while debugging can exhaust it. Space
  restarts ~60–90 s apart; it self-resolves and is not a credential or code
  problem.

### First real end-to-end run, 2026-08-24

Symbol `A01609`: real balance 50,000,000 KRW, no pre-existing positions, ledger
bootstrapped from that balance, clean reconciliation (`ledgerExposure=0
realExposure=0 mismatch=0`), a real tick completed. No order was or could be
submitted.

### Daily history

Available on the paper host; this is the data path Multi-Asset Task B/C opened
(`.planning/ms-b-kis-history-probe-result.md`,
`.planning/ms-c-kis-data-pipeline.md`).

| what | endpoint | `tr_id` | division |
|---|---|---|---|
| equities | `GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | `FHKST03010100` | `J` |
| indices | `.../inquire-daily-indexchartprice` | `FHKUP03500100` | `U` |

Index codes: `0001` KOSPI, `1001` KOSDAQ, `2001` KOSPI200. Same `tr_id` on
paper and production, unlike the trading TRs' `V`-prefix convention.

**The INDEX endpoint intermittently rejects a valid request and the equity one
did not.** A real `HTTP 200` carrying `rt_cd=1 msg_cd=OPSQ0003` for a window
that answers normally on the next attempt. Measured 2026-09-22 with 15
identical calls per window: **index 3 of 15 on one window, 0 of 15 on another;
equity 0 of 15 on the same range** — per-call and random rather than a property
of the window, and 15 equity calls is not proof the equity endpoint never does
it. A 47-call reference backfill at that rate completes with probability
**~3e-5**, and the first real attempt died on its 32nd call.

**Row caps differ per endpoint and both truncate silently**: equities **100**
rows per call, indices **50**. Both return `rt_cd=0` and keep the **newest**
rows, dropping the oldest. A 120-day KOSPI request came back with 50 bars
covering only the newest 73 days of it. `FID_PW_DATA_INCU_YN` is not the cause
and makes no difference at `Y`, `N`, or absent.

**`FID_ORG_ADJ_PRC` measured across 삼성전자's 50:1 split (2018-05-04)**: `0` =
수정주가 gives 53,000 → 51,900; `1` = 원주가 gives 2,650,000 → 51,900, i.e. a
−98% single day. **KIS's own published Python sample defaults to `1`.**

**Depth reaches 1991-08-28** for both 삼성전자 and 현대차 — the same date to the
day, so that is KIS's own floor rather than a listing date. SK하이닉스 begins
2000-08-01, NAVER 2005-08-09. A ~35-year span, though a backtest assuming
single-stock-futures execution cannot start before those futures existed.

**A KRX trading date maps exactly onto UTC midnight.** The continuous session
opens 09:00 KST and KST is UTC+9, so a bar dated `20240502` opens at
`2024-05-02T00:00:00Z`. An equality, not a rounding — verified across a real
121-bar fetch.

**`acml_tr_pbmn` (거래대금) is returned on the same call as the prices.** Stored
in `klines.quote_volume`, a nullable column added additively for it — `NULL`
for every pre-existing row, migration verified against a copy of the real
4,620,925-row database.

### Halted names print bars

`O == H == L == C` with zero volume and zero turnover, repeated for as long as
the halt lasts. Measured 2026-09-21 across the full-universe scan: 신라젠
carries **604 consecutive** such sessions, 좋은사람들 **832**. They are stored,
because they are what the tape said, and counted separately in the scan's
coverage report (`data/krx_scan.py`) so a reader can subtract them.

### `store.find_missing_ranges` against KRX

It diffs against an arithmetic sequence, so a market trading ~245 days a year
shows ~116 false gaps per symbol per year — 58 measured over six months.
Expected trading days come from an index series instead: an index prints
exactly when the market is open. Comparing one direction only once reported
"zero gaps" against a reference that was itself missing 31 days.

### 투자자별 매매동향

The one data source Korea has that crypto and US equities do not — KRX
*mandatorily discloses* daily per-stock buying and selling by 개인 / 기관 /
외국인, where the entire US literature on retail order flow exists because
researchers there had to **infer** it. All measured 2026-09-14
(`.planning/rd-c-kis-flow-probe-result.md`, `data/kis_investor_flow.py`).

- `GET /uapi/domestic-stock/v1/quotations/inquire-investor`, `tr_id`
  `FHKST01010900`, `FID_COND_MRKT_DIV_CODE=J`. **Returns exactly 30 rows and
  accepts no date parameter at all**, so 30 is a *horizon*, not a page cap —
  unlike the daily-chart endpoints, which cap at 100/50 but page backwards
  through years. The window is a rolling lookback, so a collector starting
  within ~30 trading days loses nothing from its start date forward.
- **`tr_pbmn` (거래대금) is denominated in 백만원, not 원, and KIS documents this
  nowhere.** Confirmed two independent ways on 삼성전자: the implied price
  `value × 1e6 / qty` lands within a few percent of that day's close on every
  overlapping day (259,516 against 261,000; 266,670 against 266,000), and the
  three types' summed buy value is a steady **86–90%** of the same day's
  `klines.quote_volume`, which is in 원. Read as 원 it is off by a factor of a
  million — an error that does not look wrong, merely small.
  `kis_investor_flow.py` converts to 원 on ingest.
- **The three types do not sum to market turnover.** The residual **10–14%** is
  기타법인 / 내국인 / 국가·지자체, which this endpoint does not break out.
  `foreign-institution-total` (`FHPTJ04400000`) returns the cross-sectional
  counterpart with a finer institutional breakdown (`fund_`, `insu_`, `bank_`,
  `ivtr_`, …).

### Intraday bars — two endpoints, one with history

| endpoint | `tr_id` | rows | date parameter |
|---|---|---|---|
| `inquire-time-itemchartprice` | `FHKST03010200` | 30 | none — current session only |
| `inquire-time-dailychartprice` | `FHKST03010230` | 120 per call | `FID_INPUT_DATE_1` |

The second serves past sessions back **~250 trading days, rolling** — the
boundary pinned to the day on 2026-09-14: **2025-09-03 served 120 bars,
2025-09-02 served none.** ~251 trading days, so it is a **trading-day count,
not a calendar cutoff**, and the far edge advances one session per session.

A full regular session is ~380 bars — 390 minutes less the ~10-minute
15:20–15:30 closing call auction, during which there is no continuous trade and
so no bar — i.e. **~4 calls per symbol-session**.

**Bar timestamps are not uniformly on the minute grid**: 2026-01-02 returned
`:11`-second stamps where every other probed date returned `:00`.

### The listed universe

Full record, including the two corrections this rule took after it was first
written: `.planning/rd-y-the-full-universe-scan.md`.

Real counts, 2026-09-22, each step taken against the pool the step before it
left:

| side | chain |
|---|---|
| live | 2,719 `ST` → **2,605** common → **2,533** excluding 72 SPACs |
| delisted | 2,353 plain → 2,039 after issue type → 2,033 after instrument class → **1,841** excluding 178 SPACs and 14 REITs |
| **combined pool** | **4,374** |

The live side drifts by a name or two a day as KRX lists and delists, so treat
these as a dated measurement rather than a constant. What is stable is the
*chain* — an earlier version of this line quoted 1,846 by skipping the
instrument-class step's six.

**The evidence behind the filters**, measured across all 4,398 live rows by
joining 증권그룹구분코드 onto 표준코드:

- 삼성전자 `005930` is `KR7005930003`; 삼성전자우 `005935` is `KR7005931001`.
  **114 of the original 2,718 `ST` rows are 우선주.**
- The ISIN's third character gives instrument class: `7` 주식+ETF+리츠, `G` ETN,
  `5` 펀드, `8` DR, `A` 신주인수권, non-`KR` foreign.
- KODEX 200 is `KR7069500007`, issue type `0` — so issue type does not separate
  stock from ETF. **70 live SPACs** carried both `ST` and a `KR7…0` ISIN.
- The issue-type rule was validated against an independent label with every
  disagreement explained: 112 issues contain 우 as an ordinary syllable
  (다우기술, LX하우시스, AP우주통신) and 22 are foreign-domiciled listings
  carrying a Hong Kong or Cayman ISIN.
- A bare `리츠` match takes **116 live hits of which 23 are REITs**, 75 ETNs, 14
  ETFs, plus 메리츠종금; the anchored form takes 25 hits, all 23 live REITs. A
  bare `스팩` takes **아스팩오일**, a 코넥스 oil company; the obvious anchor
  drops **미래에셋대우스팩 5호**, whose 호수 is preceded by a space.
- An unbranded delisted ETF would still pass the pool filters. The evidence it
  is a small residue: **zero** of the 2,335 KR7 plain delisted names carry any
  ETF-shaped word, where **569 of 1,172 live ETFs** do.

**The master files**: KOSPI 915 + KOSDAQ 1,803 was the `ST` split. Over half
the KOSPI file is ETFs and ETNs (`EF` 1,168, `EN` 375). **The two files carry
fixed tails of different lengths — KOSPI 228 bytes, KOSDAQ 222** — so one
shared offset silently reads the wrong two characters for one market and every
group code comes back as whitespace, while the download, the unzip and the row
count all look perfect.

**Stock codes are no longer all numeric**: KOSDAQ now issues alphanumeric codes
such as `0001A0`. KIS prices them normally — measured 2026-09-22, `0001A0`,
`0004V0`, `0004Y0` each return a full page from the daily endpoint — but all
**80** of them are 2026 listings with zero bars before that year. The files
list currently-listed symbols only.

### The delisted universe

Measured 2026-09-20; `data/krx_delisted.py`, full account
`.planning/rd-w-the-delisted-universe.md`.

- **KRX's own portal, not KIS**:
  `POST http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd`,
  `bld=dbms/comm/finder/finder_listdelisu`, answering in `block1`. **4,182
  delisted issues** — 유가증권 2,133 / 코스닥 1,928 / 코넥스 121 — against
  `finder_stkisu`'s 2,869 listed ones, with **zero overlap**, so it is
  delisted-only.
- No credentials; it needs a `JSESSIONID` from the loader page, and **every
  request without one answers `HTTP 400` with the body `LOGOUT`**, which reads
  like an auth failure and is not. Every `MDCSTAT*` statistics `bld` still
  answers `LOGOUT` *with* a session, so only the finders are reachable this way.
- **An unknown `bld` answers HTTP 200 with an HTML body**, not an error —
  `finder_dellistisu`, `finder_delisu` and `finder_deallistisu` all did while
  probing.
- **KIS serves a dead name's daily bars to its last session.** 한진해운
  `117930` runs to **2017-03-06, final close 12 KRW** — from 3,540 a year
  earlier — then returns nothing at all for any later window. **21 of 25**
  randomly sampled plain 6-digit codes are retained, final bars spanning 2000 to
  2026; all four misses were 신주/우선주 legacy instruments rather than common
  stock.
- The 100-row cap keeps the newest rows, so an over-wide window such as
  `19900101`..`20261231` returns a dead name's last 100 sessions and the final
  bar is its last trading day.
- **A zero-row answer is ambiguous**: `999999` and `ZZZZZZ` both return
  `rt_cd=0` with zero rows, identical to a name KIS has dropped.
- Delisting is not failure: 루트로닉 left at 36,700 and 락앤락 at 8,660
  (take-privates); 조흥은행 and 우리은행 through merger.

### Single-stock futures

Full record: `.planning/rd-r-futures-liquidity-universe.md`.

| what | endpoint | `tr_id` | division |
|---|---|---|---|
| daily history | `GET /uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice` | `FHKIF03020100` | `JF` |
| asking price | `.../inquire-asking-price` | `FHMIF10010000` | `JF` |
| last price | `.../inquire-price` | `FHMIF10000000` | `JF` |

`F` is *index* futures and returns `rt_cd=0` with zero rows for a stock
contract.

- **283 underlyings** carry a listed single-stock future, **every one 10 shares
  a contract**, from `fo_stk_code_mts.mst.zip` (cp949, pipe-delimited) on
  2026-09-16. The same file also holds calls (`B`), puts (`C`) and calendar
  spreads (`D`) for those names.
- **A contract code has no arithmetic relation to its underlying.** 삼성전자
  `005930` is `A11610`; SK하이닉스 `000660` is `A50610`. The encoding is `A` plus
  a two-character KIS issue id plus the year's last digit plus the month, and
  **the issue id exists nowhere else in this project's data**, so the master is
  the only source.
- **KIS drops an expired contract's ENTIRE series, not its oldest bars.**
  Measured 2026-09-16: expiries **2026-01 and later answer, 2025-12 and earlier
  return nothing at all** — contract-level, not a rolling bar window, since the
  2025-12 contract has bars well inside the range the 2026-01 contract is still
  served over. The oldest bar reachable was **2025-10-10**; the **front-month**
  series is reconstructible only from **2025-12-12**. Roughly one more month
  goes every month, so `runs/krx_futures_liquidity.json` is committed because it
  is the only copy.
- The row cap is 100 and silent, as for equities. **`acml_tr_pbmn` is 원 here**,
  not the 백만원 that 투자자별 매매동향 uses.
- **A bar is printed for every session a contract is LISTED**, carrying
  `acml_vol` 0 when nobody traded it — so all 283 names show 100% coverage. KRX
  lists these in batches: 24 names first traded 2026-04-27 and 17 more on
  2026-09-14.

### The order book

| book | endpoint | `tr_id` | division | answers in |
|---|---|---|---|---|
| spot | `.../inquire-asking-price-exp-ccn` | `FHKST01010200` | `J` | `output1` |
| futures | `.../inquire-asking-price` | `FHMIF10010000` | `JF` | `output2` |

On the futures book the **prices carry the `futs_` prefix and the quantities do
not**: `futs_askp1` is the price, `askp_rsqn1` is the size beside it, and
`futs_askp_rsqn1` — the symmetric guess — is `None` at every level. A caller
that coalesces would record a book with prices and no size.

Resting size at the touch had never been read by this project at all until
`data/krx_quote_sampler.py`, which is how `rd-q` came to describe a ratio of
*cumulative volume* (`acml_vol`) as books being "27× deeper" — two different
quantities. The sampler stores the four primitives; the spread is derived,
never stored.

**KIS answers a quote request outside market hours with the LAST book**, not an
empty one — verified at 23:00 KST, which returned 삼성전자 at 253,500/253,000.
An off-hours sample is therefore plausible numbers from a different market
state.

---

## 6. KRX OpenAPI — documented semantics and saved-sample checks

Checked 2026-10-07. The current [official FAQ][krx-faq] describes:

| item | documented meaning |
|---|---|
| units | OHLC and traded value in KRW; volume in shares |
| price basis | original, unadjusted prices; no retroactive corporate-action adjustment |
| publication | previous-day data updated at 08:00 on the next business day; no same-day or intraday data |
| membership at `basDd` | issues listed at that day's close, including suspended issues and issues subsequently delisted; market follows the queried date |
| session coverage | OHLC uses the regular session; volume/value include KRX after-hours trading, including the aftermarket introduced on 2026-09-14 |

These are current documented contracts, not a historical publication archive
or a promise that erroneous records are never corrected. Next-session use
must account for the publication lag. Acquisition time is not a historical
availability timestamp. Total value divided by total volume need not lie
within regular-session OHLC, and the aggregation regime changes during the
discovery era.

Offline checks of the Task AP samples found **7,494/7,494** trading rows with
`MKTCAP == TDD_CLSPRC * LIST_SHRS` exactly, and identical listed-share counts
in the corresponding trading/basic rows. This supports KRW market-cap units
in those samples; it does not certify the entire history. Trading `ISU_CD`
is a short code, whereas basic information provides ISIN `ISU_CD` and short
code `ISU_SRT_CD`. Saturday basic snapshots repeat Friday's even when trading
responses are empty; basic records carry no observation-date field.

Instrument group, share class and market section describe different things.
`보통주` alone includes investment vehicles and SPACs. On 2022-06-30 the
already saved KIND SPAC list contains **58** codes, while the API's SPAC
section contains **57**; their intersection is 57. The remaining code,
`340120`, is present in API basic information as `주권` / `보통주` with
section `관리종목(소속부없음)`. An issue absent from the SPAC section is
therefore not established to be an operating company. This is a distinction
between fields, not evidence that the API misclassified it. `LIST_DD` also
does not identify a SPAC conversion date: control `336570` retains its original
listing date after conversion.

Full scope, hashes and limits: `.planning/rd-ap-krx-source-access.md`.
Continuous coverage, classification at decision time and revision behaviour
remain uncertified. No selected large/liquid universe has been produced.

[krx-faq]: https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd

---

## 7. Where the rules are

Every trap above has a standing rule, and the rules are in `CLAUDE.md`'s
Exchange API Facts section rather than here. This table is the index, not the
rules themselves.

| measurement here | the rule it produced |
|---|---|
| equities cap 100, indices 50 | a cap is a property of an endpoint, not of a venue |
| `tr_pbmn` in 백만원, `acml_tr_pbmn` in 원 | so is a unit convention |
| `FID_ORG_ADJ_PRC` across the 50:1 split | `adjusted` is a required argument with no default; what comes back is a price return, not a total return |
| `OPSQ0003` at 3-in-15 | retry from an allowlist; every other non-zero `rt_cd` this project has met is permanent |
| `999999` and `ZZZZZZ` answer like a dead name | a zero-row answer needs a nonsense-code negative control in the same run |
| an unknown `bld` answers HTTP 200 with HTML | a non-JSON 200 is rejected explicitly |
| 604 and 832 frozen sessions | a frozen bar may never make a name eligible; what a return across a halt means is open |
| a bar per session a futures contract is listed | counting bars measures listing, not liquidity |
| the last book off-hours | quote collection gates on the continuous session |
| no historical book endpoint | an order book is not backfillable at any price |
| `futs_askp1` against `askp_rsqn1` | do not coalesce a futures book's field names |
| the 29-digit truncation | the guard belongs upstream of `Order` construction |
| HTTP 451 from the instance | a collector's home is chosen per venue, not once for the project |
| ~116 false gaps a year | expected trading days come from an index series, checked both directions |
| the CRLF `\r` in an exception message | credentials are stripped at the adapter boundary |
| the two `1m` gaps | a registration declares them and the pre-access check verifies |
| `tr_cont` `"F"` observed | continue only on `"M"` |

Operational state — which collector runs where, and which host is the database
of record — is in `CLAUDE.md`'s Exchange API Facts section too, because it
changes on operator decision rather than on venue behaviour.
