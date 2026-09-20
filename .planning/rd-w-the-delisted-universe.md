# Research Direction Task W — the survivorship blocker is not a blocker

**Measured 2026-09-20.** Module: `data/krx_delisted.py`. Reproducible with

```
python -m data.krx_delisted --snapshot
```

Opened by [`rd-v`](rd-v-payoff-geometry-and-the-universe-drift.md)'s
closing item: *"a drift-free test needs names that were not chosen for
having risen. Both point at the survivorship-safe full universe."*

---

## 1. The headline

> **Korean delisted names are both enumerable and priced.** KRX's own
> portal publishes 4,182 delisted issues, and KIS serves their daily bars
> right up to the final session — 한진해운 ends **2017-03-06 at 12 KRW**,
> down from 3,540 the year before.
>
> **The delisting date needs no table.** KIS caps a daily equity request
> at 100 rows and keeps the newest, so an over-wide window returns a dead
> name's *last* 100 sessions. The last bar is the last trading day.

This closes, as a data-availability question, what CLAUDE.md records as
open: *"KIS's master files enumerate **currently-listed symbols only**,
which is the open survivorship problem for a full-universe scan"*, and
`rd-d` §2.2's *"until a delisted-symbol source exists, a full-universe
result is survivorship-contaminated."* A source exists.

**It does not close the research question.** Having the data is not having
run anything. §6.

## 2. What was measured, in the order it was measured

**KIS first, because it decides whether the list is worth having.** A list
of dead names is useless if their prices are gone.

| code | | 2016Q1 rows | `hts_kor_isnm` |
|---|---|---|---|
| 005930 | 삼성전자 — live control | 80 | `'삼성전자'` |
| 000660 | SK하이닉스 — live control | 80 | `'SK하이닉스'` |
| 117930 | 한진해운 — delisted 2017-02 | **80** | `''` |
| 103130 | 웅진에너지 — delisted | **80** | `''` |
| 999999 | nonsense — negative control | 0 | `''` |
| ZZZZZZ | nonsense — negative control | 0 | `''` |

**The negative control is not optional here**, and CLAUDE.md says why: a
dead or unknown instrument answers `rt_cd=0` with zero rows, which is
indistinguishable from "no data in this window". Without `999999` in the
same run, a zero-row delisted name would have read as "KIS drops them".

**A first version of this probe got HTTP 500 on a live control** and would
have concluded the opposite. `kis_klines._get_with_retry` exists precisely
because this project once declared an instrument unsupported on the
strength of a single 500; the probe had bypassed it. Fixed, and the same
calls then succeeded.

### 2.1 The series stops exactly where the company did

한진해운 `117930`, quarter by quarter:

| window | rows | span | close |
|---|---|---|---|
| 2016Q1 | 60 | 20160104..20160331 | 3,540 → 3,215 |
| 2016Q2 | 61 | 20160401..20160630 | 3,120 → 2,005 |
| 2016Q3 | 62 | 20160701..20160930 | 2,015 → 1,160 |
| 2016Q4 | 63 | 20161004..20161229 | 1,110 → **367** |
| 2017Q1 | 43 | 20170102..**20170306** | 371 → **12** |
| 2017Q2 | **0** | — | — |
| 2018Q1, 2020Q1 | **0** | — | — |

삼성전자 returns 62 rows for the same 2017Q1 window, so the window works
and the zeros are the company.

**This is the whole point of a survivorship-safe universe, visible as a
price path.** The 99.7% collapse from 3,540 to 12 is exactly the
observation that disappears when a universe is built from names that are
still listed — and `rd-v` §1 measured what that deletion is worth on this
project's own data: the `rd-r` ten returned **+54%/yr** against KOSPI's
+19%.

## 3. The list: `finder_listdelisu`, verified by content

`http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd`,
`bld=dbms/comm/finder/finder_listdelisu`.

| finder | rows |
|---|---|
| `finder_stkisu` (listed) | 2,869 |
| `finder_listdelisu` (delisted) | **4,182** |
| **overlap** | **0** |

유가증권 2,133 / 코스닥 1,928 / 코넥스 121.

**Verified by asking for a specific known-dead code, not by trusting the
endpoint's name.** `117930` is present and named 한진해운; `005930` is
absent. That distinction is load-bearing — the zero overlap is what makes
this list *delisted-only* rather than a union, and a union would have been
silently wrong in a direction no downstream check would catch.

**Three plausible sibling ids answer HTTP 200 with an HTML body**
(`finder_dellistisu`, `finder_delisu`, `finder_deallistisu`). A status-code
check alone would have taken any of them for data. This is the KIS
contract-code trap in a second venue, and the module now rejects a
non-JSON 200 explicitly.

**The statistics endpoints do not work this way.** Every `MDCSTAT*` `bld`
tried answers `HTTP 400` with the body `LOGOUT`, with or without a primed
`JSESSIONID`. The finders work with just the session cookie. So this
document claims the *finder* path and nothing about the rest of the
portal.

## 4. Retention, measured over a random sample

25 random plain 6-digit delisted codes, each asked over 1990–2026 so the
row cap returns its final sessions:

| | |
|---|---|
| retained by KIS | **21 / 25** |
| last-bar year | 2000s ×4, 2010s ×8, 2020s ×9 |
| the 4 missing | `016835` 한미은행(1우B), `007121` 국제전자공업1신, `006891` 태경화학1신, `013691` 뉴코아1신 |

**All four misses are 신주/우선주 legacy instruments, not common stock.**
So the plain-code filter is doing real work and the retention rate for
ordinary equities is higher than 21/25 suggests.

**Depth reaches at least 2000-07-21** (사파이어성장, last close 3,660) —
consistent with CLAUDE.md's existing KIS finding that daily history
reaches 1991 for live names.

### 4.1 Delisting is not failure, and a naive rule gets this backwards

| code | | last bar | final close |
|---|---|---|---|
| 085370 | 루트로닉 | 2023-10-26 | 36,700 |
| 115390 | 락앤락 | 2024-12-06 | 8,660 |
| 220630 | 맘스터치 | 2022-05-30 | 62,000 |
| 282690 | 동아타이어 | 2024-10-07 | 13,500 |
| 085680 | 모빌탑 | 2009-11-24 | **5** |
| 341310 | 이앤에치 | 2026-02-23 | **2** |
| 012090 | 성원건설 | 2010-04-30 | **55** |

The first four are take-privates and tender offers; the last three are
collapses. The list's oldest entries make the same point differently —
`000010` 조흥은행 and `000030` 우리은행 left through merger and holding-company
conversion.

**So a backtest that books −100% on delisting is wrong in the opposite
direction to survivorship bias**, and by a large margin on a
tender-offer name. The exit price has to come from the price series,
which is available, rather than from the fact of delisting.

## 5. What was built

`data/krx_delisted.py` + `krx_delisted` in `data/store.py`, mirroring
`krx_universe`'s conventions (dated snapshots, `INSERT OR IGNORE`, a
coverage report).

**Four decisions worth stating, because each could reasonably have gone
the other way:**

1. **A separate table, not a `delisted` flag on `krx_universe`.** They
   answer different questions from different sources. Absence from a
   `krx_universe` snapshot means "not listed then", which is *not* the
   claim "delisted", and one column would let the two be read as each
   other.
2. **No delisting-date column.** KRX does not publish one in this finder,
   and the price series answers membership directly: a name was in the
   pool on day `D` exactly when it has a bar on day `D`. Storing a date
   nobody published would mean inventing one.
3. **The overlap check fails closed and costs a second request.** It is
   the only thing standing between this record and the failure mode where
   the endpoint quietly starts returning listed names too.
4. **`plain_codes` is a floor, not a filter**, and its test says so
   explicitly: it drops rights, 신주 and fund classes, and it **keeps**
   preferred shares and SPACs, which are indistinguishable from common
   stock by code alone. The finder publishes no instrument-type field.

**Snapshotted rather than fetched once**, even though the list only grows:
a dated record makes "when did KRX first publish this name as delisted"
answerable, which bounds the delisting date for any name KIS no longer
serves.

Real run: **4,182 rows, 2,350 plain 6-digit codes**; a second run writes 0.

## 6. What this does NOT establish

**No result, no strategy, nothing run.** This is a data-availability
finding. `rd-v` §1 remains the standing statement about the `rd-r` ten,
and every figure in `rd-t`, `rd-u` and `tm-e` still sits on that universe.

**The instrument-type gap is real and unsolved.** 2,350 plain codes
include preferred shares and SPACs. KIS's own master files carry
증권그룹구분코드 for *live* names; nothing here supplies it for dead ones,
so a common-stock-only historical universe needs another source or an
inference rule, and that rule needs its own verification.

**Coverage is measured on 25 names, not 2,350.** 21/25 is an estimate with
a real confidence interval, and it was not stratified by delisting decade
— the sample happened to spread 2000–2026, which is reassuring and not by
design.

**A full-universe scan is a much bigger piece of work than this list.**
Fetching daily history for ~2,350 dead names plus ~2,700 live ones, at
KIS's 100-row cap and observed 7–10s latencies, is its own engineering
task with its own rate-limit budget. Nothing here has fetched it.

**Nothing about `투자자별 매매동향` changes.** That series has a 30-row
rolling horizon and cannot be backfilled for any name, live or dead.

## 7. What follows

1. **Schedule the snapshot** alongside `krx_universe`'s, on the instance.
   Cheap, and the dated record only becomes more useful.
2. **Solve instrument type before any scan**, not during one. A universe
   that silently includes preferred shares ranks them by turnover beside
   their own commons.
3. **A delisted-inclusive universe is the precondition for the one thing
   `rd-v` says is binding** — a test on names not selected for having
   risen. That is a new pre-registration, not a continuation of anything
   already run.
4. **Any exit price comes from the last bar**, never from the delisting
   event (§4.1).
