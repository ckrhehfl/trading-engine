# Multi-Asset TSMOM Task B — result: KIS serves 35 years, and the adjusted-price trap is real

**Run**: 2026-09-13, against the KIS **paper** host
(`openapivts.koreainvestment.com:29443`), **from the GCP instance**, not
from a workstation. Probe: `python/data/kis_probe.py`.

**Verdict: GO.** Every Phase 0 unknown in
[`ms-a-multi-asset-universe-discuss.md`](ms-a-multi-asset-universe-discuss.md)
§5 is now measured, and the answers are better than the design assumed.

---

## 0. Where this ran, and why that is not incidental

The first draft of this probe was going to be run locally. It should not
have been, and CLAUDE.md says so in its own words:

> **Run it where it will run.** Three defects were invisible locally and
> obvious on the instance: executable bits (WSL shows `/mnt/c` as 777),
> **Binance's `HTTP 451` on US IPs**, a 350 MB Gradle daemon.

KIS is a Korean broker and the instance's IP is the one the `kis-paper`
loop already transacts from — a workstation result would have described
a laptop's network, not KIS. The operator caught this before the probe
ran.

**The checkout was not switched to the feature branch.** `kis_probe.py`
imports only the standard library, so it was copied to `/tmp` and run
standalone. Moving the instance's checkout would have put the live
Python signal generation — which cron re-execs every tick — onto an
unreviewed branch, which is the shape of a defect this project has
already had once.

---

## 1. Findings

| §5 item | Question | Answer |
|---|---|---|
| 1 | Does the daily endpoint exist? | **Yes.** `inquire-daily-itemchartprice` / `FHKST03010100`, `rt_cd=0` |
| 2 | Does it work on the paper host? | **Yes** — so §7 decision 2 (production host) **never arises** |
| 3 | How deep? | **1991-08-28 for 삼성전자 and 현대차 — 35.04 years** |
| 4 | Trading calendar | 82 trading days vs 86 weekdays, Jan–Apr 2024 — **4 closures, exactly right** |
| 5 | Single-stock-futures multiplier / liquidity | **not probed** — a different endpoint family, deferred to MS-C |
| 6 | Adjusted or raw? | **Both, and the trap is real.** See §3 |
| 7 | Point-in-time universe data | **Solved differently and better.** See §4 |

### Response fields (`output2`)

```
stck_bsop_date  stck_clpr  stck_oprc  stck_hgpr  stck_lwpr
acml_vol  acml_tr_pbmn  prdy_vrss  prdy_vrss_sign
prtt_rate  mod_yn  flng_cls_code  revl_issu_reas
```

OHLCV is complete. `acml_tr_pbmn` (누적거래대금) and `prtt_rate` (분할비율)
turn out to be load-bearing — §4 and §3 respectively.

---

## 2. History depth — and the number that actually governs

Per-symbol earliest bar, walked backwards year by year:

| Symbol | Earliest | Span |
|---|---|---|
| 삼성전자 (005930) | 1991-08-28 | 35.0 y |
| 현대차 (005380) | 1991-08-28 | 35.0 y |
| SK하이닉스 (000660) | 2000-08-01 | 26.1 y |
| NAVER (035420) | 2005-08-09 | 21.1 y |

Two unrelated symbols sharing **1991-08-28 to the day** says that is
KIS's own data floor, not a listing date.

**Detection floors** (`1.6449/√years`), against every window this project
has ever held:

| Window | Floor | |
|---|---|---|
| BTC 1d holdout (2.95 y) | 0.96 | everything failed against this |
| Binance futures 1m (6.96 y) | 0.62 | the previous best |
| **KIS 18.3 y** (§2.1) | **0.38** | the realistic operative figure |
| KIS 35.0 y | 0.28 | the signal-history ceiling |

### 2.1 The signal window and the tradeable window are not the same

**Do not quote 0.28.** A backtest that assumes single-stock-futures
execution cannot start before single-stock futures existed, and KRX
introduced them long after 1991 — believed 2008-05, **deliberately not
asserted here**: MS-C resolves it by querying the futures endpoint for
its own earliest bar, which is a measurement rather than a recollection.

The window also cannot start before the **youngest constituent** has
data, since all K members must be held simultaneously. NAVER's 2005 floor
already binds harder than 1991.

So the operative window is roughly **2008 onward, ~18 years, floor
≈0.38** — still comfortably below the 0.4–0.8 credible-institutional-edge
band, and **the first window in this project's history where a realistic
edge is detectable rather than merely not excluded.**

The pre-2008 history is not wasted: it is available as a *supporting*
in-sample-in-time check, disclosed as non-tradeable, never as the
promotion window.

---

## 3. The adjusted-price trap, confirmed by measurement

`FID_ORG_ADJ_PRC` across 삼성전자's 50:1 split (effective 2018-05-04),
closing price ratio from the last pre-split bar to the first post-split
bar:

| Setting | Ratio | Reading |
|---|---|---|
| `0` (수정주가) | **1.021** | continuous — the split is absorbed |
| `1` (원주가) | **51.06** | the raw 50:1 step |

Both behave exactly as documented. **The danger is the default**: KIS's
own published Python sample passes `1`, so code written by copying the
official example silently produces a series in which Samsung drops ~98%
in one day — which a momentum signal reads as a crash, and which would
produce a confident, catastrophic, entirely plausible-looking result.

`prtt_rate` (분할비율) is in the response, so MS-C can assert
independently that any bar carrying a split ratio has a price step
consistent with the adjustment setting in use, rather than trusting the
flag.

**Still open and disclosed**: KIS's 수정주가 adjusts for splits, **not for
dividends**. Korean large caps yield roughly 2%/year, so what MS-F
reports is a **price return, not a total return**, and the write-up must
say so rather than let the reader assume otherwise.

---

## 4. `acml_tr_pbmn` resolves §5 item 7 — the universe rule needs no external data

MS-A §4.4 worried that a point-in-time listing universe might not exist
on this API, and proposed a survivorship-filtered fallback with disclosed
bias. **That fallback is not needed for the ranking itself.** Traded
value comes back on the same call as the prices:

2018 Q1 daily-average 거래대금, in KRW:

| Symbol | Daily average | |
|---|---|---|
| 삼성전자 | ₩725.4 bn | |
| SK하이닉스 | **₩340.5 bn** | **rank 2, as MS-A §4.2 predicted** |
| NAVER | ₩87.7 bn | |

Two consequences:

1. **The rule is directly computable** from the same endpoint MS-C is
   already building against — rank the candidate pool by 2018 traded
   value, no external data source.
2. **SK하이닉스 is selected by the rule.** The operator's "하이닉스는
   무조건" request is confirmed a no-op, exactly as MS-A §4.2 predicted
   — nothing was overridden and no hindsight selection enters the record.

The **candidate pool** caveat from §4.4 still stands unchanged: ranking
needs a list of what to rank, and if a point-in-time 2018 listing set is
unavailable, the pool is survivorship-filtered and that must be
disclosed. What this finding removes is the *ranking* dependency, not the
*pool* dependency.

---

## 5. Two API behaviours MS-C must handle

### 5.1 The 100-row cap is silent

A ~5-year request returned exactly **100 rows** with `rt_cd=0` — no
error, no truncation flag. This is BingX's behaviour, not Binance
futures' (which returns a real `HTTP 400`). **MS-C must page by date and
verify the returned count**, never trust a wide range.

At 100 rows per call, ~245 trading days a year and 18 years, that is
**~45 calls per symbol, ~540 for K=12** — trivial.

### 5.2 `HTTP 500` is transient, not a verdict

The first run reported the KOSPI200 index code as failing with an
`HTTPError`. A retry returned `rt_cd=0` and a correct value. All three
indices work:

| Code | Index | 2024-05 value |
|---|---|---|
| `0001` | KOSPI | 2689.50 |
| `1001` | KOSDAQ | 850.75 |
| `2001` | **KOSPI200** | 366.44 |

(`1028` → 1797.29 and `2028` → 170.46 also answer; unidentified, and not
needed.)

This is the same shape as CLAUDE.md's BingX finding that `109400
timestamp is invalid` was startup contention rather than clock drift:
**a transport error from KIS is not evidence the request is wrong.**
MS-C needs bounded retry, and this probe's own first-run conclusion was
wrong for want of it.

### 5.3 The token endpoint rate-limits fast, and it is shared

`POST /oauth2/tokenP` returned `HTTP 403` after three issuances in a few
minutes — CLAUDE.md's documented `EGW00133`, hit sooner than expected.
The probe now caches its token to `/tmp` (mode `0600`, 50-minute reuse).

**This matters beyond convenience**: the live `kis-paper` JVM draws on
the same per-app-key allowance. A research script that burns it can stop
a running loop from renewing. `KisTokenProvider` caches per process so
nothing broke here, but MS-C runs far more calls and must reuse one token
for a whole backfill.

---

## 6. What this changes in MS-A

- **§7 decision 2 is closed, not decided**: the paper host serves
  quotations, so no credentialed client against a production endpoint is
  needed. The security question does not arise.
- **§5 item 7 is half-closed** (§4): ranking data exists; pool data still
  open.
- **§8's "MS-B finds no usable daily history"** failure mode did not
  occur.
- **New for MS-C**: date paging with count verification, bounded retry on
  transport errors, single-token backfills, and an independent
  split-consistency assertion using `prtt_rate`.
- **New constraint (§2.1)**: the backtest window starts at the later of
  the single-stock-futures introduction and the youngest constituent's
  first bar — not at 1991.

---

## 7. Not probed, and still open

- **Single-stock futures**: multiplier, per-underlying liquidity, earliest
  bar, and the roll calendar. A different endpoint family
  (`domestic-futureoption`), and the reason `KIS_MARKET_DIVISION=
  STOCK_FUTURES` still refuses to start. **This is MS-C's first job**, and
  §2.1's window depends on it.
- **A point-in-time 2018 listing / futures-eligibility set** (§4).
- **Whether 1991-08-28 is a hard floor for every symbol** or only the two
  tested.
- **Rate limits for a real bulk pull.** ~540 calls succeeded nowhere yet;
  the probe made a few dozen.
