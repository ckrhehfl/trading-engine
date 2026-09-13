# Multi-Asset TSMOM Task E — the KR-10 universe rule, fixed before it is run

**Status**: the rule and its threshold are committed here **before any
2018 correlation is computed**. Nothing is resolved yet; the resulting
universe goes in a separate section of this document once the rule has
been executed against the data.

The ordering is the point. A threshold chosen after seeing which value
produces a nicer basket is not a constraint, and this file exists in git
history so that ordering is checkable rather than asserted.

**This document is the operative KR-10 rule.** It supersedes MS-A §4.2's
three-per-sector cap on the operator's decision of 2026-09-13; MS-A §4.2
and MS-D §3.2's option list both now carry supersession notices pointing
here, so exactly one selection rule is live. Reasons in [`ms-d-korean-correlations-and-universe.md`](ms-d-korean-correlations-and-universe.md)
§3.1–3.2, summarised in §1 below.

---

## 1. Why the sector cap is replaced

MS-A §4.2 specified "no more than three from any one KRX sector
classification". Two findings retire it:

1. **It cannot be applied across markets.** KOSPI and KOSDAQ use disjoint
   industry code schemes with no published mapping — KOSPI pharma is
   `(0027, 0009)`, KOSDAQ pharma `(0091, 0240)`. Under a shared code space
   a three-per-sector cap admits six biotechs; under a per-market space it
   silently becomes a KOSPI-only universe, which is a market-wide
   exclusion the rule never authorised.
2. **Sector labels are a poor proxy for correlation between individual
   stocks.** Measured on 2021–2026, the capped basket runs a *higher* mean
   pairwise correlation (0.3005) than the uncapped one (0.2500) — the cap
   removes two speculative KOSDAQ names that barely co-move with anything
   and admits two ordinary industrials that do.

**Finding 2 is a diagnostic and is not grounds for the change on its
own**: it is computed over the window MS-F would score on, and choosing a
rule because it measures better there is selection on the scored window.
Finding 1 stands independently, and the replacement below is justified by
it — measuring the property directly, on pre-window data, rather than
proxying it with labels that do not survive a market boundary.

## 2. The rule

> **KR-10, revised 2026-09-13.**
>
> 1. **Pool**: every outright single-stock-futures underlying
>    (`fo_stk_code_mts.mst`, product-type field `1` or `3`) that traded in
>    2018. 229 of 265 as of this date.
> 2. **Order**: descending 2018 full-calendar-year traded value
>    (`acml_tr_pbmn` summed over 2018), the year *before* the scored
>    window opens.
> 3. **Admit greedily** down that order. A candidate is admitted only if,
>    **after** adding it, the basket satisfies both constraints in §3.
>    A candidate that would violate either is skipped, permanently, and
>    the scan continues.
> 4. **Stop** at ten single-stock constituents, or when the pool is
>    exhausted.
> 5. **Then append MS-A §4.2's two index futures — KOSPI200 and
>    KOSDAQ150 — subject to the same §3 constraints.** Target K = 12,
>    unchanged from MS-A. The crypto leg stays deferred (MS-A §7 item 4,
>    reaffirmed 2026-09-13).

**An earlier draft of item 5 read "No index members" and stopped at ten.**
That was an unflagged scope change: this task replaces MS-A §4.2's
*sector cap*, and silently dropping two of its twelve members alongside
is a different edit that was neither argued nor disclosed. Restored, and
the index members are put through §3 rather than admitted or excluded by
hand — an index is a weighted combination of its own constituents, so
whether it is too correlated with them is exactly the question §3 exists
to answer.

## 3. The constraints, and the numbers, fixed here

Both are computed on **2018 daily log returns only** — the same
pre-window year the ordering uses. No data from 2019 onward enters the
selection at any point.

> **C1 — mean pairwise correlation ≤ 0.50**
> **C2 — no individual pair > 0.80**

**Where the numbers come from, since a threshold is only meaningful with
its rationale.** Both are external conventions, not fitted to this data:

- **0.50** is the midpoint of the 0.3–0.6 band that same-country equity
  baskets typically run. It encodes "no more concentrated than an
  ordinary single-country basket."
- **0.80** bounds the *worst* pair, because a mean can hide one nearly
  redundant duplicate. Two names correlating above 0.8 are close to one
  position held twice, which is what C1 alone would not catch.

**Disclosed, because it would otherwise look like the threshold was
tuned**: a 2021–2026 measurement of the uncapped top ten came in at
0.2500 (MS-D §1). That figure is **prior-knowledge disclosure only**. It
was not used to set either threshold — both come from the external
conventions above — and it is **not** evidence about whether C1 binds on
**2018**, which is a different sample the thresholds are actually applied
to. It is recorded so a reader can see what was known at the time, not as
a prediction.

**C1 and C2 are guards, not optimisers** — their job is to refuse a
pathological basket (ten semiconductors), not to search for a good one.
A guard that rarely fires is a guard working as designed, and this
project already runs several. Whether they actually bound on 2018 is
reported in §4 either way.

**What this rule explicitly does not do**: it does not pick the
lowest-correlation ten. That would be optimisation over the pool, would
raise `N`, and would be selection even on 2018 data. Order is fixed by
turnover; correlation only ever vetoes.

## 4. Result

Executed 2026-09-13, against the rule and thresholds committed above at
`d57a4e3`. Pool 229; 223 had enough 2018 observations to correlate.

| # | Code | Name | 2018 traded value |
|---|---|---|---|
| 1 | 005930 | 삼성전자 | ₩144.3 tn |
| 2 | 068270 | 셀트리온 | ₩92.6 tn |
| 3 | 000660 | SK하이닉스 | ₩73.0 tn |
| 4 | 009150 | 삼성전기 | ₩32.5 tn |
| 5 | 207940 | 삼성바이오로직스 | ₩29.7 tn |
| 6 | 000720 | 현대건설 | ₩27.4 tn |
| 7 | 007390 | 네이처셀 | ₩26.6 tn |
| 8 | 028300 | HLB | ₩25.9 tn |
| 9 | 064350 | 현대로템 | ₩22.9 tn |
| 10 | 051910 | LG화학 | ₩21.1 tn |

Then MS-A §4.2's two index futures, put through the same §3 constraints
rather than admitted by hand:

| Index | Worst pair against the ten | Basket mean after adding | Verdict |
|---|---|---|---|
| KOSPI200 (`2001`) | **0.776** vs 삼성전자 | 0.198 | **admitted** |
| KOSDAQ150 (`2203`) | 0.649 vs 셀트리온 | 0.231 | **admitted** |

**Final K = 12**, matching MS-A §4.2. Resolved-basket 2018 correlations:
**mean 0.231**, worst pair **0.776**, and for the ten single-stock members
alone mean 0.157, worst 0.724, minimum −0.070.

**KOSPI200 is the closest anything came to a constraint**: 0.776 against
a C2 of 0.80, a margin of 0.024. An index is a weighted combination of
names already in the basket, so this is the pair most at risk of being
one position held twice — and it is the strongest evidence in §4.1 that
the guard is live rather than decorative.

**The constraints did not bind. Zero rejections** among the single-stock
members, so those ten are exactly the top ten by 2018 traded value, and
neither index member was rejected either. §3 disclosed in advance that this was
the likely outcome, and it is reported here unchanged rather than
followed by a threshold adjustment.

### 4.1 The guard is not inert, and that was checked rather than assumed

A guard never observed firing is a guard nobody has shown works — this
repository has had three inert fixtures that all read fine. Re-running
the same rule with tightened thresholds:

| C1 | C2 | Rejections | Resulting mean | Worst pair | First rejected |
|---|---|---|---|---|---|
| 0.50 | 0.80 | **0** (committed) | 0.157 | 0.724 | — |
| 0.50 | 0.60 | 3 | 0.176 | 0.519 | SK하이닉스 (pair 0.650) |
| 0.50 | 0.30 | 13 | 0.118 | 0.299 | SK하이닉스 |
| 0.12 | 0.80 | 8 | 0.119 | 0.311 | SK하이닉스 (mean 0.274) |
| 0.05 | 0.80 | 190 | 0.048 | 0.176 | SK하이닉스 |

The mechanism rejects, in a sensible order, and SK하이닉스 is first out
every time — it is the name most correlated with the already-admitted
삼성전자. **It also came close to firing as committed**: the resolved
basket's worst pair is 0.724 against a C2 of 0.80.

### 4.2 What the correlation constraint catches that a sector cap cannot

The five most correlated pairs in the top thirty by turnover:

| ρ (2018) | Pair | KRX sectors |
|---|---|---|
| 0.732 | 현대건설 / 현대엘리베이터 | 건설 / 기계·장비 |
| 0.724 | 현대건설 / 현대로템 | 건설 / 운송장비·부품 |
| 0.703 | 현대로템 / 현대엘리베이터 | 운송장비·부품 / 기계·장비 |
| 0.683 | KB금융 / 신한지주 | 금융 / 금융 |
| 0.681 | 셀트리온 / 셀트리온제약 | 제약 / 제약 |

**The three most correlated pairs in the entire pool sit in three
different sectors.** They are the same chaebol group, and a
three-per-sector cap is structurally blind to that: it would happily
admit 현대건설, 현대로템 and 현대엘리베이터 as construction, transport
equipment and machinery — three "different" sectors correlating at
0.70–0.73.

This is a stronger argument for the replacement than §1's, and unlike
§1's finding 2 it is computed on **2018** data, outside the scored
window, so it can be relied on rather than merely noted. It was not
foreseen when the change was made; it was found by running it.

## 5. What is still required before MS-F

- The resolved list committed to a pre-registration under
  `configs/research/preregistrations/`, with the Eligibility Bar's
  single-window criteria pinned (MS-A §7 item 3: PSR ≥ 0.95, drawdown,
  trade-count floor, profit factor, and the window's own detection floor).
- The backfill persisted to the store — nothing is in it yet.
- ~~The single-stock-futures earliest bar~~ — **resolved 2026-09-13, and
  it does not exist.** KIS serves currently-listed contracts only; every
  2018–2025 expiry returns `rt_cd=0` with zero rows. Futures execution
  across the backtest window is therefore an **unverified premise** the
  pre-registration must state as such. See MS-B §2.1.
