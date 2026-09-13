# Multi-Asset TSMOM Task E — the KR-10 universe rule, fixed before it is run

**Status**: the rule and its threshold are committed here **before any
2018 correlation is computed**. Nothing is resolved yet; the resulting
universe goes in a separate section of this document once the rule has
been executed against the data.

The ordering is the point. A threshold chosen after seeing which value
produces a nicer basket is not a constraint, and this file exists in git
history so that ordering is checkable rather than asserted.

Supersedes MS-A §4.2's three-per-sector cap, on the operator's decision
of 2026-09-13. Reasons in [`ms-d-korean-correlations-and-universe.md`](ms-d-korean-correlations-and-universe.md)
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
> 4. **Stop** at ten constituents, or when the pool is exhausted.
> 5. No index members. The crypto leg stays deferred (MS-A §7 item 4,
>    reaffirmed 2026-09-13).

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
tuned**: the uncapped top ten measures 0.2500 over 2021–2026, so it is
already known that C1 is unlikely to bind. That is intentional. **C1 and
C2 are guards, not optimisers** — their job is to refuse a pathological
basket (ten semiconductors), not to search for a good one. A guard that
rarely fires is a guard working as designed, and this project already
runs several. Whether they actually bound is reported in §4 either way.

**What this rule explicitly does not do**: it does not pick the
lowest-correlation ten. That would be optimisation over the pool, would
raise `N`, and would be selection even on 2018 data. Order is fixed by
turnover; correlation only ever vetoes.

## 4. Result

*Not yet run. This section is filled in by the execution commit, which
must come after this one in git history.*

## 5. What is still required before MS-F

- The resolved list committed to a pre-registration under
  `configs/research/preregistrations/`, with the Eligibility Bar's
  single-window criteria pinned (MS-A §7 item 3: PSR ≥ 0.95, drawdown,
  trade-count floor, profit factor, and the window's own detection floor).
- The backfill persisted to the store — nothing is in it yet.
- The single-stock-futures earliest bar, which sets the tradeable window
  start (MS-C §2.1).
