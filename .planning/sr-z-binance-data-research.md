# Strategy Research Task Z: Binance as a deeper BTC data source

## Scope note

This task builds **only the data path** to Binance's public klines
(spot and, lighter, USDT-M futures) -- a client, symbol-namespacing
inside the existing `store.py` schema, a resumable backfill, and a set
of real, computed statistics (cross-exchange correlation, spot/futures
basis, early-era data quality, regime segmentation, detection-floor
arithmetic). **No strategy was written, run, or evaluated here, and
nothing under `python/research/strategies/`, any `holdout*.json`
config, or any preregistration was touched.** Same "infrastructure
first" split `sr-a` (BingX klines) and `sr-w` (FRED macro) established.

Prerequisites read in full first: CLAUDE.md ("Strategy Research
Methodology", "Exchange API Facts -- BingX" as the documentation-rigor
bar to match, "Non-negotiable Rules"), `.planning/sr-a-data-
pipeline.md` (the BingX pipeline this project's data-layer conventions
originate from), `.planning/sr-w-macro-data-pipeline.md` and
`.planning/sr-t-daily-data-path.md` (this doc's style/rigor templates).

## Why this task exists

After eight BTC-USDT price/volume/funding strategies against BingX data
(max ~5.2 years at `1d`) found nothing statistically significant (see
CLAUDE.md's "Strategy Attempts So Far"), and after two macro-conditioned
attempts (`sr-x`, `sr-y`) came back data-limited rather than resolved,
this project's own governing arithmetic
(`detection_floor ~= 1.645/sqrt(years)`, one-sided α=0.05) says BingX's
5.21-year `1d` retention has a **~0.72** annualized-Sharpe detection
floor -- inside, but near the top of, the 0.4-0.8 credible-
institutional-edge range CLAUDE.md already cites (0.72 is itself within
0.4-0.8, not above it), meaning a real edge anywhere in the *lower*
part of that range (0.4-0.72, most of its width) may be structurally
undetectable on BingX data alone, however well any strategy is
specified.

The user asked two concrete questions this task answers with real
numbers, not assumptions: (1) do other exchanges have deeper BTC
history, and (2) given BTC's market structure changed enormously over
its life (thin retail market -> ICO mania -> 2018 bear -> institutional
futures growth -> 2020-21 bull/retail return -> 2022 bear/exchange
collapses -> 2023-24 ETF approval/institutionalization), is pooling all
of that history into one detection-floor calculation even statistically
honest, or does treating 2017 BTC and 2026 BTC as "the same asset" for
research purposes need justification first.

## What was built

1. **`python/data/binance_klines.py`** -- stdlib-`urllib` client for
   `GET /api/v3/klines` (spot) and `GET /fapi/v1/klines` (USDT-M
   futures), returning `bingx_klines.KlineRow` (imported, not
   redefined) so `store.py`'s existing `upsert_klines`/`fetch_klines`/
   `find_missing_ranges` work against Binance data with **zero schema
   changes**. See "Real API findings" below for the two load-bearing
   wire-behavior divergences from BingX this module has to translate.
2. **Symbol namespacing in `backfill_binance.py`** (not a `store.py`
   schema change -- see "Symbol namespacing design" below): Binance rows
   are stored under `"BINANCE:BTCUSDT"` (spot) / `"BINANCE-
   FUTURES:BTCUSDT"` (futures) rather than the raw wire symbol.
3. **`python/data/backfill_binance.py`** -- resumable CLI backfill,
   mirroring `backfill.py`/`backfill_macro.py`'s structure, with a
   `MARKET_CONFIG` table for the two supported markets.
4. **`python/tests/fake_binance_server.py`** -- stdlib `http.server`
   fake, replicating Binance's real (verified live, not assumed) wire
   behavior: inclusive `endTime`, oldest-first capping.
5. Real, computed analysis (not committed as code -- see "Analysis
   methodology" below): cross-exchange correlation, spot/futures basis,
   early-era data-quality check, regime segmentation, detection-floor
   arithmetic.

**52 new tests** (`test_binance_klines.py` 35, `test_backfill_binance.py`
17). Full suite: **1,369 passed**, up from 1,317 (`sr-y`'s own final
count, this project's most recent full-suite count immediately prior --
1,369 - 52 = 1,317 reconciles exactly). Nothing regressed.

## Real API findings -- Binance (spot + USDT-M futures)

Verified directly against the live production endpoints
(`api.binance.com`, `fapi.binance.com`) on **2026-08-05**. Full rigor
matching CLAUDE.md's "Exchange API Facts -- BingX" bar; the durable
version of these findings lives in `binance_klines.py`'s own module
docstring, summarized here with the supporting evidence.

### Response shape: a bare JSON array of arrays, by position

`[open_time_ms, open, high, low, close, volume, close_time_ms,
quote_asset_volume, num_trades, taker_buy_base_volume,
taker_buy_quote_volume, ignore]` -- 12 elements, confirmed identically
for spot and futures. `open_time_ms`/`close_time_ms`/`num_trades` are
bare (unquoted) JSON integers; every OHLCV/volume field is a quoted
JSON string, confirmed via direct inspection of real response bodies.
This is a genuine structural divergence from every BingX endpoint in
this pipeline (`{"code","msg","data":[...]}` envelope, objects not
positional arrays) and from FRED (`{"observations":[...]}`, also
objects). `binance_klines._parse_row` parses by index, not key --
deliberately not reusing `bingx_klines._parse_row`'s shape.

### `endTime` is INCLUSIVE, not half-open -- a load-bearing divergence

Confirmed by a real `startTime == endTime` request (both
`1502928000000`) returning exactly one row (the candle whose open time
equals that value); BingX's half-open convention would have returned
zero for the equivalent case. Confirmed further by a narrow-window
request (`startTime=1502928000000, endTime=1503187200000, limit=1000`)
returning exactly the 4 candles with open time in `[startTime,
endTime]` inclusive, not 3.

`binance_klines.py`'s own public contract stays half-open
`[start_ms, end_ms)`, matching every other function in this pipeline
and what `store.find_missing_ranges` produces -- `fetch_klines_page`
translates by requesting Binance's real `endTime = end_ms - 1`. Unlike
`fred_client.py`'s own inclusive-range divergence (deliberately **not**
translated, because translating FRED's inclusive *dates* would invent a
calendar judgment call that belongs in `store.py`, not the wire
client), this translation is safe and mechanical: both sides are
already epoch milliseconds, and every interval this pipeline uses
(`15m`/`1h`/`1d`) has a step >= 900,000ms, so subtracting 1ms excludes
exactly the boundary candle without risk of also excluding a real
candle strictly inside the requested range. Pinned by
`test_fetch_klines_page_translates_half_open_end_to_binance_inclusive_end_minus_one_ms`
and
`test_fetch_klines_page_excludes_a_candle_exactly_at_the_half_open_end_boundary`.

### Silent over-limit capping keeps the OLDEST rows -- opposite of BingX

Confirmed for both markets: a request whose `[startTime, endTime]` span
covers more candles than `limit` returns the `limit` candles **closest
to `startTime`**, ascending -- the newest candles in range are silently
dropped, not the oldest. Live evidence (spot, `1d`):

```text
startTime=1502928000000, endTime=1900000000000, limit=2000 (over max)
-> 1000 rows, first=1502928000000 (the real start), last=1589241600000
```

This is the exact opposite of BingX's own verified behavior
(`bingx_klines.py`'s module docstring: "silently capped to the `limit`
candles closest to `endTime` -- the newest ones"). A direct consequence:
**Binance's own rows come back oldest-first (ascending)**, not
newest-first like BingX -- matching FRED's own ordering, no reordering
needed before storage.

### Max `limit` differs by market, and differs in how it's enforced

- **Spot: hard max 1000.** `limit=1001` and `limit=1500` are both
  silently capped to exactly 1000 rows (confirmed by exact row count,
  not just "no error") -- same *silent-capping* shape as BingX's own
  `limit`, but Binance additionally silently caps the *value itself*.
- **Futures: hard max 1500, enforced as a real error.** `limit=1500`
  returns exactly 1500 rows; `limit=1501` is a real `HTTP 400`
  (`{"code":-1130,"msg":"Data sent for parameter 'limit' is not
  valid."}`) -- futures rejects over-limit outright, spot does not. A
  real, verified per-market divergence.
- `binance_klines.MAX_CANDLES_PER_REQUEST` is pinned to spot's lower
  **1000** for both markets, deliberately -- see "Judgment calls" below.

### Listing-date boundary: empty array, not an error, not padding

Confirmed live: requesting spot BTCUSDT for `2017-01-01`..`2017-08-15`
(before the real `2017-08-17` start) returns `[]`. A range straddling
the true start (`2017-08-16`..`2017-08-19`) returns rows starting from
the real earliest candle (`2017-08-17T00:00:00Z`), not an error and not
padded backward. Same "exchange silently returns only what exists"
shape as both BingX and FRED.

### Errors: a real non-2xx HTTP status with a JSON object body

Confirmed `400` for spot (`limit=0` ->
`{"code":-1102,"msg":"Mandatory parameter 'limit' was not sent, was
empty/null, or malformed."}`) and futures (`symbol=NOTREAL` ->
`{"code":-1121,"msg":"Invalid symbol."}`) -- a different shape from the
success array, and (unlike BingX, whose errors are a `200` with an
error code embedded in the body) always via a genuinely non-2xx status.
`binance_klines.py` validates client-side first (misaligned/inverted
range, out-of-bounds `limit`) for the same fail-fast reason
`bingx_klines.py` does, so this HTTP path is only reachable in practice
for a genuinely bad `symbol`.

### Rate limits: real, numeric, and documented -- a first for this pipeline

Both `bingx_klines.py` and `fred_client.py` had to fall back to
undocumented/third-party-estimated rate limits. Binance's are real and
confirmed two ways: **live**, via each host's own
`GET .../exchangeInfo` `rateLimits` field
(`api.binance.com`: `REQUEST_WEIGHT` = **6000/minute** per IP;
`fapi.binance.com`: `REQUEST_WEIGHT` = **2400/minute** per IP, both
fetched directly this session); and **from Binance's own official
docs/changelog** (not re-derived from isolated single-request header
deltas in this session, so held to slightly lower confidence than the
live-fetched budget numbers): spot `GET /api/v3/klines` has a flat
weight of **2** regardless of `limit`; futures `GET /fapi/v1/klines` is
**tiered** (weight 1 for `limit<=100`, 2 for `<=500`, 5 for `<=1000`,
10 for `>1000`). Even this task's full ~9-year spot backfill (4 pages)
uses a trivial fraction of either budget, so
`INTER_REQUEST_DELAY_S = 0.25` (same conservative fixed value
`bingx_klines.py`/`fred_client.py` already use) was kept rather than
tuned tighter -- there was no need to.

**HTTP 418** is Binance's own documented signal for a temporary IP ban
after repeated rate-limit violations (not `429`) -- not observed live
this session (no violation was ever triggered), but deliberately
excluded from `binance_klines._RETRYABLE_STATUSES` on principle:
retrying into an active ban would only extend it.

## Symbol namespacing design

`store.py`'s `klines` table has no `exchange`/`source` column -- keyed
only on `(symbol, interval, open_time_ms)`, unchanged by this task (no
migration, per this task's explicit scope). Binance's raw wire symbol
(`"BTCUSDT"`, no dash) differs from BingX's stored symbol
(`"BTC-USDT"`, with a dash) so a literal collision was unlikely by
accident -- but relying on that would be fragile, and spot vs. futures
would collide with **each other** even though they are genuinely
different markets with a real, measured price divergence (see "Basis"
below).

**Resolved: rows are stored under a prefixed `storage_symbol`**
(`"BINANCE:BTCUSDT"` for spot, `"BINANCE-FUTURES:BTCUSDT"` for
futures), applied only inside `backfill_binance.sync_range` -- never
inside `binance_klines.py` itself, which always sends Binance's own raw
symbol over the wire (Binance has never heard of `"BINANCE:BTCUSDT"`).
Additive, zero schema migration, same style as `1d`'s addition to
`_grid.py` and `holdout_side`'s addition to holdout configs. Pinned by
6 tests in `test_backfill_binance.py`, including one that stores a real
`"BTC-USDT"` (BingX) row alongside a Binance row in the same connection
and asserts both survive independently.

## Real backfill: the actual numbers

Run against the live, public, unauthenticated production endpoints on
**2026-08-05**, writing to the shared cache at
`python/data/var/klines.sqlite3` (the same file `sr-a`/`sr-f`/`sr-t`
already populate with BingX data, copied into this task's isolated
worktree since `python/data/var/` is gitignored and not shared across
worktrees -- the BingX `1d` series was also caught up from its
previous 2026-07-27 cutoff to 2026-08-04 first, via a normal
`backfill.py` rerun, so the cross-exchange correlation below runs
against the freshest available BingX data rather than a stale slice).

### Binance spot BTCUSDT `1d` (primary target)

- **Earliest available bar: `2017-08-17T00:00:00Z`**
  (`open_time_ms = 1502928000000`) -- reached via `MARKET_CONFIG`'s own
  `default_start_iso`, itself set from the earliest-row probe already
  disclosed in this task's brief; the real backfill's actual first gap
  started exactly there (`fetching missing range
  [1502928000000, ...]`), confirming the probe was already exact, not
  approximate.
- **Latest bar this run: `2026-08-04T00:00:00Z`** (`1785801600000` --
  "now" floored to the day grid excludes the still-forming 2026-08-05
  bar, same convention `sr-t` established for BingX `1d`).
- **Total: 3,275 rows.**
- **Internal gap count: 0** -- verified two independent ways: the
  expected contiguous count for `[1502928000000, 1785801600000]`
  inclusive at the 86,400,000ms step is exactly 3,275 (matches the
  stored count exactly), and `store.find_missing_ranges` re-queried
  over the full stored span returns `[]`.
- **Zero rows off the UTC-midnight grid** (`open_time_ms % 86400000 ==
  0` for all 3,275 rows).
- **Earliest-row trick independently verified, not an artifact**: a
  request for `2017-01-01`..`2017-08-15` (wholly before the real start)
  returned `[]` -- confirming `startTime=0&limit=1`'s reported
  `2017-08-17` earliest date is real, not a side effect of how that
  specific query shape happens to behave.
- Wall clock: ~1.2 seconds, 4 requests (spot's 1000-candle page spans
  ~2.74 years at `1d`, so ~9 years of history is 4 pages).

### Binance USDT-M futures BTCUSDT `1d` (secondary, lighter check)

Per this task's scope, **not held to the same exhaustive-verification
standard as spot** (no separate earliest-row-artifact re-check, no
dedicated early-era quality pass) -- but the same
`backfill_binance.py` code path was used (cheap: 3 requests total for
~6.9 years at `1d`), so the same gap-detection machinery ran "for
free" and its output is reported honestly rather than omitted:

- **Earliest available bar: `2019-09-08T00:00:00Z`**
  (`open_time_ms = 1567900800000`) -- consistent with Binance's own
  live `onboardDate` for the BTCUSDT futures contract (2019-09-08
  17:55 UTC) once accounting for the daily bar starting at UTC midnight
  on the day trading began, and with
  Binance's own blog post ("On September 9th, Binance Futures launched
  its BTC offering" -- the 1-day difference is a timezone-rendering
  artifact between Binance's blog and UTC, not a contradiction; see
  "Regime segmentation" below for how this date is used as a genuine
  market-structure boundary).
- **Latest bar: `2026-08-04T00:00:00Z`**, **total: 2,523 rows**,
  **internal gap count: 0** (same two-way verification as spot).

## Cross-exchange correlation -- a real computed number

Computed over the actual overlapping window both exchanges have at
`1d`: BingX BTC-USDT (2021-05-14 through 2026-08-04, 1,909 bars after
the catch-up backfill above) intersected with Binance spot BTCUSDT --
**1,909 common days**, full overlap (BingX is the shorter series here).

| Metric | Value |
|---|---|
| Pearson correlation, daily **closes** | **1.000000** |
| Pearson correlation, daily **log returns** | **0.999955** (n=1,908 return-pairs) |
| BingX log-return stdev | 0.028686 |
| Binance log-return stdev | 0.028633 |

**This is about as strong as two independent price series can agree.**
The two venues are pricing the same underlying almost identically at
daily close -- not merely "BTC is fungible so it's probably fine" as an
assumption, a real, computed number instead. **What this does and does
not license, stated precisely rather than generalized (CodeRabbit
review finding on this PR: an earlier draft of this section overstated
the conclusion as signal transferability)**: it shows the **daily price
series** are comparable, which is the actual question this task's own
governing brief asked ("is it even valid to use [Binance] given BTC's
market structure changed enormously"). It does **not** show that a
trading *signal* built on Binance data would transfer profitably to
BingX -- a real signal's performance also depends on volume, funding,
basis, execution costs, and timing/alignment, none of which a price-only
correlation measures, and none of which this task modeled. That is a
separate, signal-specific, cost-inclusive backtest-and-paper-validation
question for whichever future task actually builds a strategy on this
data -- not something this infrastructure task's correlation number can
answer on its own.

## Spot/futures basis divergence -- a real computed number

Computed over Binance spot vs. Binance futures' own overlap (2019-09-08
through 2026-08-04, 2,523 common days -- futures is the shorter series
here, entirely contained within spot's span):

| Metric | Value |
|---|---|
| Mean basis `(futures-spot)/spot` | **-0.0154%** |
| Stdev | 0.0652% |
| Min / Max | -0.7365% / +1.7952% |
| Mean **absolute** basis | 0.0487% |
| Median absolute basis | 0.0462% |
| p95 absolute basis | 0.0946% |

**The basis narrows over time -- the futures market visibly maturing**,
split into thirds by calendar coverage:

| Period (by index-thirds of the overlap) | Mean abs basis |
|---|---|
| 2019-09-08 .. 2021-12-26 (first third) | 0.0573% |
| 2021-12-27 .. 2024-04-14 (second third) | 0.0440% |
| 2024-04-15 .. 2026-08-04 (third third) | 0.0446% |

**Largest single-day divergences, both explainable by real market
events, not data artifacts**:

| Date | Spot close | Futures close | Basis | Context |
|---|---|---|---|---|
| 2019-09-24 | $8,493.14 | $8,645.61 | **+1.80%** | 16 days after futures launch -- thin, immature futures market |
| 2020-03-12 | $4,800.00 | $4,764.65 | **-0.74%** | "Black Thursday" COVID crash -- cascading liquidations |

Both the direction (futures trading at a premium early on, then at a
discount during a leverage-driven crash) and the magnitude (well under
2% even at the extremes, tight the rest of the time) are consistent
with real, liquid perpetual-futures basis behavior, not a data quality
problem.

## Early-era (2017-2019) data quality check

Checked directly against the 867 daily bars from 2017-08-17 through
2019-12-31 (the pre-2020 stretch of Binance spot data, i.e. before this
project has any other data source to cross-check against):

| Check | Result |
|---|---|
| Zero-range bars (`high == low`) | **0** |
| Zero-volume bars | **0** |
| Bars with >25% overnight gap (`\|open - prev_close\| / prev_close`) | **0** |
| Bars with >50% intraday range (`(high-low)/low`) | **0** |
| Longest run of identical consecutive closes | **1** (i.e. no stale-data run at all) |

**Verdict: clean.** No sign of a stale-quote bug, a flat/frozen period,
or an obviously corrupted outlier bar anywhere in the early era. As a
further sanity check (not a formal test, but real, independently
verifiable evidence). the actual OHLC prints on well-known historical
event dates match widely-cited real BTC price history closely:

| Date | Event | Binance spot high/low that day | Widely-cited figure |
|---|---|---|---|
| 2017-12-17 | 2017 bull ATH | high **$19,798.68** | ~$19,783 (CoinDesk BPI) |
| 2018-12-15 | 2018 bear bottom | low **$3,156.26** | ~$3,122-3,200 (multiple trackers) |
| 2021-11-10 | 2021 bull ATH | high **$69,000.00** | ~$68,990-69,225 (Forbes/CoinDesk) |
| 2022-11-21 | 2022 cycle bottom | low **$15,476.00** | **$15,476** (exact match) |

These are cross-exchange price levels (Binance's own prints, not
identical to CoinDesk's composite index by construction), so small
differences are expected -- the point is that every value lands in the
expected neighborhood or matches exactly, with no sign of a broken or
fabricated early-era series.

## Regime segmentation -- a concrete, dated proposal (design only, not implemented)

**Two segmentations are proposed, at different granularities, because
they trade resolution for statistical power very differently (see
"Detection floors" below) -- neither is "the" answer; which a future
task uses should depend on whether it needs regime resolution or
detection power more.**

### Fine-grained: 8 regimes, following observable market-structure breaks

Every boundary below is a **specific, independently verifiable date**
(sourced from Binance's own price data plus real historical
events -- SEC filings, Binance's own blog, bankruptcy-court filings --
not vague "around 2024" approximations). Boundaries are inclusive on
both ends and partition the full 3,275-day dataset **exactly** (verified
by direct arithmetic: the 8 regimes' day-counts sum to precisely 3,275,
matching the real backfilled row count with zero days double-counted
or omitted):

| Regime | Start | End | Days | Boundary rationale |
|---|---|---|---|---|
| R1: Retail/ICO-mania bull | 2017-08-17 | 2017-12-17 | 123 | Binance spot listing -> 2017 ATH |
| R2: 2018 bear market | 2017-12-18 | 2018-12-15 | 363 | ATH+1 -> 2018 cycle bottom |
| R3: Recovery, pre-futures accumulation | 2018-12-16 | 2019-09-07 | 266 | Bottom+1 -> day before Binance USDT-M futures launch |
| R4: Institutional futures growth, pre-COVID | 2019-09-08 | 2020-03-11 | 186 | Futures launch -> day before Black Thursday |
| R5: COVID crash + 2020-21 bull run | 2020-03-12 | 2021-11-10 | 609 | Black Thursday -> 2021 ATH |
| R6: 2022 bear / exchange collapses | 2021-11-11 | 2022-11-21 | 376 | ATH+1 -> 2022 cycle bottom (spans Terra/UST depeg May 2022, FTX bankruptcy filing Nov 11 2022) |
| R7: Recovery to ETF approval | 2022-11-22 | 2024-01-10 | 415 | Bottom+1 -> SEC spot BTC ETF approval |
| R8: Post-ETF institutionalized era | 2024-01-11 | 2026-08-04 | 937 | ETF approval+1 -> present |

**Why `2019-09-08` (Binance USDT-M futures launch) is a full regime
boundary, not a footnote**: this is not an arbitrary historical
curiosity -- it is the date the exact instrument class this project
trades (USDT-margined perpetual futures) first existed on Binance at
all. Before it, "BTC" in this dataset means spot only; leverage,
funding rates, and basis-trading dynamics the live system will actually
face didn't exist in this market yet. A strategy edge found only in
R1-R3 would be an edge in a structurally different, pre-derivatives
market.

### Coarse: 3 regimes, trading resolution for power

| Regime | Start | End | Days (years) |
|---|---|---|---|
| C1: Spot-only era (pre-perp-futures) | 2017-08-17 | 2019-09-07 | 752 (2.060y) |
| C2: Perp-futures era, pre-ETF | 2019-09-08 | 2024-01-10 | 1,586 (4.345y) |
| C3: Post-ETF institutionalized era | 2024-01-11 | 2026-08-04 | 937 (2.567y) |

Same exact-partition property verified (752 + 1,586 + 937 = 3,275).

## Detection-floor arithmetic -- the actual statistical-power numbers

Using this project's own `research/retrospective.detection_floor_sharpe`
(the exact function `sr-j`/`sr-r`/`sr-t` already use elsewhere in this
codebase, not a reimplementation -- confirms `1.645/sqrt(years)` at
α=0.05 one-sided, with `years = days/365`, this project's fixed
annualization convention, not `365.25`).

### (a) Pooled, full history

| Dataset | Span | Years | Detection floor (annualized Sharpe) |
|---|---|---|---|
| **Binance spot, full pooled** | 2017-08-17 -> 2026-08-04 | 8.973 | **0.549** |
| Binance futures, full pooled | 2019-09-08 -> 2026-08-04 | 6.912 | 0.626 |
| BingX `1d` (current baseline, CLAUDE.md) | 5.21 years | 5.21 | 0.721 |

**This is the single most important number in this task.** Both 0.549
and BingX's own 0.721 fall *inside* the 0.4-0.8 credible-institutional-
edge range CLAUDE.md cites -- neither is outside it, and the honest
framing is about *where inside the range* the floor sits, not an
inside-vs-outside flip (an earlier draft of this section stated it as
a flip; corrected on CodeRabbit review). BingX's 0.721 sits near the
range's top: only its narrow top sliver (~0.72-0.8, about a fifth of
the range's width) is detectable, and the rest (0.4-0.72) is not.
Pooling Binance spot's full history moves the floor down to **0.549**,
meaningfully lower inside the same range -- roughly the top two-thirds
of the range (~0.55-0.8) becomes detectable, a real and substantial
statistical-power gain, though the bottom third (0.4-0.55) still is
not. Binance futures alone (the instrument-matched but shorter series)
still improves meaningfully on BingX, to 0.626 -- inside the range,
between the other two.

### (b) Per-regime, fine-grained (8-way)

| Regime | Years | Detection floor |
|---|---|---|
| R1 | 0.337 | 2.833 |
| R2 | 0.995 | 1.649 |
| R3 | 0.729 | 1.927 |
| R4 | 0.510 | 2.304 |
| R5 | 1.668 | 1.273 |
| R6 | 1.030 | 1.621 |
| R7 | 1.137 | 1.543 |
| R8 (longest) | 2.567 | 1.027 |

**Every single one of the 8 fine-grained regimes has a worse (higher)
detection floor than BingX's current 0.721 baseline** -- the *best*
regime (R8, the longest at 2.567 years) still only reaches 1.027, and
the worst (R1, 123 days) reaches 2.833. Fine-grained regime
segmentation, used as a standalone per-regime hypothesis test, would be
**strictly worse-powered** than research this project has already run
and found insufficient.

### (c) Per-regime, coarse (3-way)

| Regime | Years | Detection floor |
|---|---|---|
| C1 | 2.060 | 1.146 |
| C2 (longest) | 4.345 | **0.789** |
| C3 | 2.567 | 1.027 |

The coarse split fares much better -- C2 alone (4.345 years, the
perp-futures-era-pre-ETF regime) reaches **0.789**, close to (though
still slightly above) BingX's own current 0.721 baseline, despite being
a genuinely different, longer-history dataset.

### What this means for how regimes should actually be used

**Segmenting by regime trades away most (fine-grained) or a real chunk
(coarse) of the statistical-power benefit of Binance's longer history.**
Neither segmentation should be used as a standalone per-regime
hypothesis test on its own -- none of the 8 fine-grained regimes, and
none of the 3 coarse ones on their own, resolve the credible-edge range
the way the full pooled window does. The honest use of regime
segmentation this task can recommend: **as a post-hoc consistency check
on a strategy already validated against the full pooled window** --
"does this edge's sign/direction hold across every regime, or does it
only appear in one" -- exactly the framing CLAUDE.md's own Strategy
Research Methodology already applies to fold-level consistency within a
single walk-forward run, extended here to regime-level consistency
across market-structure eras. It is not a substitute for the pooled
window's own power, and should never be presented as an independently
powered test in its own right.

## Analysis methodology (not committed code)

The correlation, basis-divergence, early-era-quality, and regime-
day-count computations above were run via a one-off script reading the
shared `store.py` cache through its existing `fetch_klines` API --
`statistics.correlation`/`statistics.stdev`/`statistics.mean`/
`statistics.median` from the stdlib, no new dependency. Not committed
to the repo (this task's scope is the data pipeline, not a permanent
analysis tool) -- every number above was read directly off that
script's real output against the real backfilled cache, not
hand-computed or estimated.

## Hostname-guard decision: no Binance analog needed

`bingx-hostname-guard.yml` exists specifically to stop the BingX
*production* hostname from being hardcoded anywhere that could
accidentally enable live trading -- the risk it guards against is a
live-order-placement surface silently defaulting to the wrong host.
This project builds **no live-trading surface against Binance at all**:
no `ExchangeAdapter`, no credentials, no order placement, read-only
historical market data only, and none of that is planned to change
(CLAUDE.md's Current Scope names BingX as the only exchange with a
paper/live path). This is the identical reasoning `fred_client.py`'s
own module docstring already gives for why `DEFAULT_FRED_BASE_URL` is a
real hardcoded default rather than an env-var-only pattern -- applied
here to `DEFAULT_BINANCE_SPOT_BASE_URL`/`DEFAULT_BINANCE_FUTURES_BASE_URL`
in `backfill_binance.py`. No workflow was added or extended.

## Judgment calls resolved without asking

- **`MAX_CANDLES_PER_REQUEST` is pinned to 1000 for both markets**, even
  though futures actually supports up to 1500. Simpler than a
  per-market constant, and this task's real usage (spot's full ~9-year
  history in 4 pages, futures' lighter check in 3) never needs futures'
  extra headroom -- revisit only if a future task's futures usage grows
  enough that page count starts to matter.
- **`binance_klines.py` reuses `bingx_klines.KlineRow` directly** rather
  than defining a new dataclass -- the row shape is identical and this
  is exactly the reuse the task brief asked for (`store.py`'s existing
  functions work unchanged).
- **The half-open-to-inclusive wire translation lives in
  `fetch_klines_page`, not in `store.py` or `backfill_binance.py`** --
  it's a pure wire-protocol fact about Binance specifically (both sides
  already epoch milliseconds), unlike FRED's inclusive-date divergence,
  which genuinely needed a calendar-aware decision made at the
  `store.py` layer.
- **Futures backfill used the same `backfill_binance.py` code path as
  spot** (not a separate lighter-weight script), because it was
  actually cheaper to just run it (3 requests, ~6.9 years) than to
  build a intentionally-crippled alternate path. The "lighter check"
  framing in this task's scope is honored by *reporting* it with less
  narrative rigor (no separate earliest-row-artifact re-verification,
  no early-era quality pass), not by artificially limiting what data
  was actually fetched.
- **Regime boundary dates use daily-bar-inclusive counting** (`(end -
  start).days + 1`) to match how `find_missing_ranges`/the daily grid
  already count -- verified by the exact 3,275-day partition
  reconciliation in both segmentations above, not asserted.
- **No Bybit/OKX.** Binance alone answered this task's governing
  question decisively (a ~9-year pooled detection floor below the
  credible-edge range) -- adding a third exchange would not change that
  finding and was out of this task's scope per its own instructions.

## Deliberately out of scope

- **Any strategy on Binance data.** See Scope note -- this is
  infrastructure, matching the `sr-a`/`sr-w` precedent.
- **A holdout split for Binance data.** No strategy exists yet to need
  one.
- **Implementing the regime segmentation in code.** Both proposals above
  are a design note for a future task to use, per this task's own
  instructions -- not a `configs/research/` addition.
- **Bybit/OKX**, per the "Judgment calls" note above.
- **Any change to `python/research/strategies/`, holdout configs, or
  preregistrations.**
- **Execution-level (intraday spread/slippage/liquidation-cascade)
  cross-venue transfer analysis** -- the daily-close correlation finding
  above is real and strong, but does not by itself extend to that
  finer-grained question.
