# Research Direction Task Y — the full-universe scan, and what "common stock" turned out to mean

**Status**: universe construction complete and merged (PRs #189, #192);
the collection pass is **in flight** on the GCP instance and its coverage
report will land in `rd-y-…-result.md`.

**Mode**: data collection. No measurement here is promotable — KRX daily
was spent by `ms-f` on 2026-09-13, so everything downstream of this pass
is Discovery mode under CLAUDE.md's five guards.

---

## 1. Why a full-universe scan at all

`rd-c` §2's finding, from four sources that do not cite each other, is
that **the selection filter is the strategy**: Zarattini gets Sharpe 2.81
from a plain opening-range breakout *restricted to abnormally active
names*, and the same entry rule fails outright with no filter.
`runs/experiments.jsonl` says every `strategy_id` this project has ever
logged asks which formula predicts direction, and none asks which
situations are worth being present for.

`rd-x` then measured what happens when you approximate that with a fixed
universe: ranking a pool on a date *inside* the window is worth **+20%/yr
of pure artefact** (−2%/yr from a 2019-01 ranking against +20%/yr from a
2026Q1 one, source and pool held fixed). A **per-day** selection rule is
the honest version, and it cannot be run without the whole pool's daily
history. Hence this pass.

## 2. What the pool is, which took three corrections

This is the part that is finished, and it was wrong three times in three
days. Each correction was found by measuring against an independent
label, never by reasoning about the rule.

### 2.1 `ST` is not common stock

KIS's 증권그룹구분코드 `ST` carried **114 preferred lines** among its
2,718 rows: 삼성전자 `005930` and 삼성전자우 `005935` both carry `ST`.
Any turnover or relative-volume ranking over an `ST` pool ranks an
issuer's preferred line beside the issuer.

**The 12-character 표준코드 (ISIN) says, and was being parsed over.**
Position 8 is the issue type, `0` = 보통주:

```
삼성전자   005930  KR7 00593 0 00 3
삼성전자우 005935  KR7 00593 1 00 1
```

Validated against the Korean name across every live and delisted issue,
with every disagreement explained: 56 + 56 "ISIN says common, name
contains 우" are false positives *of the name test* (다우기술, LX하우시스,
AP우주통신 — 우 as an ordinary syllable); 22 "ISIN says non-common, no 우"
are foreign-domiciled listings carrying a Hong Kong or Cayman ISIN, where
position 8 means nothing — which is why the `KR` prefix is part of the
rule; 24 live rows carry a *letter* at position 8 (`KR700088K015` 한화3우B)
and all 24 are preferred, so "not `0`" classifies them without needing to
know what the letter means.

### 2.2 The ISIN's third character is the instrument class

Measured by joining 증권그룹구분코드 onto 표준코드 across all 4,398 live
rows. Every letter was exclusive to its group except `7`:

| char | class | group codes carrying it |
|---|---|---|
| `7` | stock-like | `ST` 2,718 + `EF` 1,172 + `RT` 23 |
| `G` | ETN | `EN` 369 |
| `5` | fund | `BC` 84 |
| `8` | DR | `DR` 10 |
| `A` | warrant | `SW` 4 + `SR` 1 |
| non-`KR` | foreign | — |

**This is what the delisted side has instead of a group code** — KRX's
delisted finder publishes no 증권그룹구분코드 at all — and it is what
removes ETNs, funds and DRs from a pool `plain_codes` alone keeps. It
does **not** separate 주식 from ETF or REIT; those share `KR7`.

### 2.3 A SPAC passes every structural filter

The one that cost a day. A SPAC is legally a 주식회사, so it carries `ST`
**and** a `KR7…0` ISIN, and **70 live names were being returned as common
stock** by the filter shipped one day earlier, with 179 more in the
delisted pool.

Only the regulated name betrays it (`…스팩`, `…스팩N호`,
`…기업인수목적…`), so the pool needs a name rule alongside the structural
ones — and a name rule is weaker evidence, which is why `is_spac` and
`is_reit` are kept separate from `classify`/`instrument_class` in
`data.krx_instrument` rather than composed into one function.

**Both name rules had to be anchored, and both anchors have a real name
behind them.**

- A bare `리츠` match is **unusable**: 116 live hits of which 23 are
  REITs, 75 ETNs, 14 ETFs *named* 리츠, plus 메리츠종금 — the same
  substring failure 다우기술 showed for 우선주. The anchored form returns
  25 hits covering all 23 live REITs.
- A bare `스팩` match takes **아스팩오일**, a 코넥스 oil company. But the
  obvious anchor drops **미래에셋대우스팩 5호**, whose 호수 is preceded by
  a *space*. And `\s` as the separator spans a **newline**, so
  `아스팩\n5호` matches — the failure direction being the one that drops a
  name from the pool, i.e. survivorship bias reintroduced by the rule
  written to prevent it.

Final form: `스팩[ \t]*$|스팩[ \t]*[0-9]|기업인수목적`. Measured
2026-09-22: identical to the substring form on all 4,403 live rows (72
hits either way) and releasing exactly 아스팩오일 on the 4,185 delisted
ones (179 → 178).

### 2.4 The counts

Each step against the pool the step before it left, 2026-09-22:

| | live | delisted |
|---|---|---|
| raw | 4,403 rows | 4,185 issues |
| group code `ST` / plain 6-digit | 2,719 | 2,353 |
| ISIN issue type `0` | 2,605 | 2,039 |
| ISIN class stock-like | (already `ST`) | 2,033 |
| less SPACs and REITs | **2,533** (−72) | **1,841** (−178, −14) |

**Combined pool 4,374.** The live side drifts by a name or two a day as
KRX lists and delists; what is stable is the chain, not the integers.

**An arithmetic error worth recording rather than quietly fixing**: the
figure shipped on 2026-09-21 was 1,846, because the write-up applied the
name rules to the 2,039 the *issue type* left and skipped the instrument
class's six. `common_stock()` applied four filters while its own docstring
counted three.

### 2.5 What is still open in the pool

- **An unbranded delisted ETF would still pass.** Disclosed rather than
  closed. The evidence it is a small residue: **zero** of the 2,335 KR7
  plain delisted names carry any ETF-shaped word, where **569 of 1,172**
  live ETFs do.
- **The delisted finder publishes no instrument type**, so everything
  above rests on the ISIN plus two name rules there. `plain_codes` remains
  documented as a floor, not a filter.

## 3. The design, and why each property is forced by the cost

**~4,400 candidates × ~24 pages = ~105,000 real API calls**, at the
0.5–0.7/s `rd-x` measured after the KRX close. That is two to three days
of continuous fetching, and every property below follows from that number
rather than from taste.

- **Resumable.** Coverage is read back from the database, so a re-run
  fetches only what is missing. `rd-x` lost two and a half hours to one
  `KisKlinesError` before its ranking was made resumable; this pass is
  twenty times that one.
- **A failure does not abort the run** — the opposite of
  `krx_dayone_drift`, deliberately. There a missing name made the
  measurement wrong, so it refused; here partial progress is the point,
  and what protects the reader is the coverage report.
- **It refuses to start during the KRX continuous session, and pauses if
  it reaches one.** The instance's collectors share this app key and
  collect series that cannot be backfilled, and throughput was measured
  dropping to 0.3/s in-session. A start-up guard alone would have
  protected only the first eight hours of three days.
- **It does not write the KRX record.** CLAUDE.md's one-writer-per-series
  rule makes the instance the only writer of `KRX:` klines, and
  `sync-krx-from-instance.sh` is `INSERT OR IGNORE`, so a row written
  elsewhere would never be corrected. This writes a separate research
  database.

**Three traps, named before the scan met them:**

1. **A zero-row answer is not "not listed."** A dead name and a code that
   never existed answer identically (`rt_cd=0`, zero rows). Negative
   controls run first and the whole scan refuses if they do not answer
   empty.
2. **The 100-row cap truncates silently and keeps the newest rows.** Pages
   are sized at 120 calendar days (~82 trading days) and **a page at the
   cap is a failure, not data**.
3. **KIS prints a bar for every session a halted name is listed** — see §5.

## 4. The run (in flight)

Launched on the instance 2026-09-21T11:51:40Z, detached.

| | |
|---|---|
| panel | 2019-01-02 … 2026-09-18 |
| at 2026-09-22T00:00Z | 1,524 symbols, **2,631,579 bars** |
| throughput | ~126 symbols/h ≈ 0.035 sym/s ≈ **0.84 pages/s** |
| failures | 64 of 1,524 (**4.2%**) — 61 `KisKlinesError`, 3 `IncompleteRead` |
| session pause | fired as designed at 00:00–06:30 UTC |

**The failures are transient, checked rather than assumed.** 000050 경방,
000370 한화손해보험 and 000500 가온전선 are actively-listed KOSPI names;
re-probed by hand the next day, every one answered normally at both ends
of the panel. They are retried automatically on the next pass, since
`already_done` covers only `done` and `absent`.

**Two defects the running scan surfaced in its own construction**, both
fixed in PR #192 and neither reaching the pass in flight:

- **The pool was half-filtered.** `candidates()` takes the live side
  through `fetch_krx_universe(common_stock_only=True)`, so the SPAC rule
  reached it the moment it landed — while the delisted side still applied
  only the ISIN's issue type. Worse than no filter, because the pool then
  looks filtered.
- **A pause was reported as a rate.** The log carried
  `PAUSED for the 0.00 sym/s eta 20786666666.7h`, once every 300s poll,
  for the whole session: the pause reused the per-symbol progress line,
  and the rate divided by wall time almost entirely spent waiting. A
  reported figure taken from the wrong denominator — the same shape as
  Task C's `+45` that was really `−97`.

## 5. Halted names print bars, and they look like quiet days

`O == H == L == C`, zero volume, zero turnover, for as long as the halt
lasts. **신라젠 carries 604 consecutive such sessions; 좋은사람들 832.**

This is most dangerous exactly where it is least visible: a per-day
turnover ranking reads a legitimate zero, and a returns series reads a
flat stretch as low volatility. They are stored — they are what the tape
said — and counted separately in the coverage report.

**Decided, because it follows directly from the measurement**: a frozen
bar may never make a name eligible. Every selection, ranking or liquidity
screen — anything answering *was this tradeable* — excludes it.

**Left open, deliberately**: what a return computed across a halt means.
Booking the gap on the resumption bar and treating the flat stretch as
zero-volatility are wrong in different directions, and choosing is a
research decision with its own `Discuss`, not a data-layer default. Until
then, any statistic over a window containing frozen bars states which side
it took.

## 6. What this unblocks, and what it does not

It unblocks the per-day selection-rule research `rd-c` §2 argues for: a
pool that exists on every day it actually traded, delisted names included,
with no continuous-listing filter anywhere in it.

It does not make anything measured on KRX daily promotable. That window
was spent by `ms-f`, so this is Discovery mode: unlimited looking, every
trial logged, and the only legitimate output is a written specification to
be confirmed on a window no decision has touched.

## 7. Files

| | |
|---|---|
| `python/data/krx_instrument.py` | `IssueType`/`classify`, `InstrumentClass`/`instrument_class`, `is_spac`/`is_reit` |
| `python/data/krx_universe.py` | live master parsing, `standard_code` capture, dated snapshots |
| `python/data/krx_delisted.py` | KRX finder, `plain_codes` (a floor), `common_stock` (the four filters) |
| `python/data/krx_scan.py` | the pool, the pass, coverage |
| PRs | #188 (survivorship clause), #189 (ISIN issue type), #191 (the collector), #192 (instrument class + SPAC) |
