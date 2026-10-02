# Research Direction Task Y, result — the full-universe scan completed, and a bar is emphatically not a tradeable session

**The pass `rd-y` left in flight has finished.** Panel `20190102..20260918`,
4,638 symbols recorded, **4,598,643 bars** over 1,895 trading days. Coverage run
2026-10-02 on the instance, which is the database of record.

**The headline is not the coverage figure.** It is that four symbols carry bars
for every session they were listed and **traded on none of them** — 578, 455,
335 and 284 bars respectively, 100% frozen. `rd-y` predicted the category and
this is its first measurement at the scale where it matters.

---

## 1. Coverage

| status | symbols | bars |
|---|---|---|
| **done** | 3,043 | 4,598,643 |
| `absent:outside_window` | 1,152 | 0 |
| `absent:never_served` | 434 | 0 |
| `failed:rejected` | 7 | 0 |
| `failed:transport` | 2 | 0 |

**The two absent categories hold together**: both carry exactly zero bars, which
is the consistency check that would catch a misclassification. A symbol filed as
"outside the window" while holding bars inside it would mean the pass had
recorded a name it also claimed never traded.

**The 9 failures are 0.19% of the pool and are retryable by construction** —
`failed:rejected` is the transient `rt_cd=1` the KIS index endpoint intermittently
returns, `failed:transport` a network fault. Codes, so a second pass needs no
re-derivation: `123160, 215580, 219860, 219960, 004090, 089170, 090990` and
`090540, 121950`.

**1,586 symbols produced no bars at all — 34.2% of the 4,638 recorded — and the
two reasons are not interchangeable.** `Absence` exists precisely because a single
`absent` bucket could not say which, and collapsing them here would undo that:

- **`absent:outside_window`, 1,152.** Bars exist for the code; none fall inside
  the panel. Normally a later listing, or a name that left before 2019. **An
  expected outcome rather than a gap**, since the delisted register reaches
  further back than the panel does.
- **`absent:never_served`, 434.** KIS serves no bar in **any** window — an
  over-wide `19900101`..today probe answered `rt_cd=0` with an empty `output2`.
  That is a statement about **data availability**, not about when the name was
  listed.

**The 434 are the residual survivorship exposure and should be read as such.** If
any of them traded inside the panel, the pool is missing them and no amount of
per-day membership logic recovers it: the price series is simply not served.

**What bounds the exposure is two controls, not one**, and the asymmetry between
them is the point:

- a **negative** control — a nonsense code — proves an empty answer is not simply
  what this endpoint returns for everything;
- a **positive** control, 005930, proves the *wide* request shape still works.
  005930 is served back to 1991-08-28, which is KIS's own floor rather than a
  listing date, so an empty answer for it cannot mean "no bars exist".

**The negative control alone would fail in the unsafe direction**, which is why
it is not sufficient: it exercises a narrow `_page` request, while the probe asks
`19900101`..today. If KIS ever answered every wide request with `rt_cd=0` and an
empty `output2` — a range limit, a date-format change, an `FID_ORG_ADJ_PRC`
interaction — the negative control would still pass, because empty is what it
expects, and **every `absent:unknown` would be recorded `NEVER_SERVED`**. That
removes real names from the pool as "KIS does not price this", which is precisely
the survivorship direction this universe exists to remove. An unresolvable
control leaves every symbol at `UNKNOWN` instead, which is the distinction
`Absence.UNKNOWN` refuses to guess at.

What would be a defect is the opposite case — a name that traded inside the panel
and is absent anyway — and the per-day membership rule is what guards against it.

## 2. The pool over time

| year | symbols | bars | bars per symbol |
|---|---|---|---|
| 2019 | 2,290 | 542,474 | 237 |
| 2020 | 2,352 | 562,652 | 239 |
| 2021 | 2,416 | 580,498 | 240 |
| 2022 | 2,488 | 589,167 | 237 |
| 2023 | 2,579 | 606,771 | 235 |
| 2024 | 2,664 | 624,372 | 234 |
| 2025 | 2,716 | 632,841 | 233 |
| 2026 | 2,681 | 459,868 | 172 |

**The pool grows 2,290 → 2,716, so listings outpaced delistings across the
panel** — a 18.6% increase over seven years. 2026's 172 bars per symbol is the
partial year (the panel ends 2026-09-18), not a collapse.

**Bars per symbol sits at 233-240 against ~245 trading days**, which is the
right order: the shortfall is names that listed or delisted mid-year plus halts.

### Listing depth is bimodal

| bars | symbols |
|---|---|
| 1-100 | 35 |
| 101-500 | 282 |
| 501-1,000 | 399 |
| 1,001-1,500 | 236 |
| 1,501-1,900 | **2,091** |
| 1,901+ | 0 |

**69% of the completed pool was listed for essentially the whole panel**, and the
716 names under 1,000 bars are where the survivorship exposure actually lives.
Nothing exceeds 1,900 against 1,895 trading days, which is the arithmetic
consistency check for this table: a symbol with more bars than there are sessions
would mean duplicate rows.

## 3. The frozen bars, which are the result that changes a selection rule

**192,292 frozen sessions — 4.18% of every bar in the panel — across 1,260
symbols.**

**What the count actually tests**, stated precisely because the prose elsewhere
in this project is looser than the code: `record_progress` counts a bar frozen
when `open = high AND high = low AND low = close` and `turnover` is `0` or
`NULL`. **`volume` is not in the predicate.**

Measured rather than assumed, on review of PR #217:

| | bars |
|---|---|
| `O == H == L == C` | 208,492 |
| …of which `turnover = 0` → **counted frozen** | **192,292** |
| …of which `turnover IS NULL` | **0** |
| …of which turnover is neither → **not frozen** | **16,200** |

Three things follow, and the third was not expected.

**The `NULL` branch has never fired.** No bar in the panel has a null turnover
or a null volume, so the predicate's conflation of "halted" with "turnover
failed to parse" is latent rather than realised. It is still a conflation — the
stored row cannot distinguish them — and worth knowing before the predicate is
reused on a series where parsing can fail.

**Adding `volume` to the predicate would change nothing here.** `volume != 0 AND
turnover = 0` selects **zero** bars, so the turnover-only condition and the
"zero volume and zero turnover" description pick the same 192,292. The looser
prose was not producing a wrong figure; it was producing an unverifiable one.

**And `O == H == L == C` is not synonymous with a halt.** 16,200 sessions have
equal OHLC *and* real turnover — a price pinned at its daily limit, which KRX
caps at ±30%, where the name traded actively at one price all day. **Those are
tradeable sessions and must not be excluded from a liquidity screen**, which is
exactly what an OHLC-only frozen test would have done. The turnover term is what
separates "nobody traded" from "everybody traded at the limit", and that is the
reason it is in the predicate rather than an implementation detail.

**Four symbols are 100% frozen**, and four more are above 99%:

| code | frozen / bars | |
|---|---|---|
| 120780 | 578 / 578 | **100.0%** |
| 271780 | 455 / 455 | **100.0%** |
| 368030 | 335 / 335 | **100.0%** |
| 311840 | 284 / 284 | **100.0%** |
| 194510 | 416 / 418 | 99.5% |
| 149940 | 374 / 376 | 99.5% |
| 050320 | 835 / 842 | 99.2% |
| 111820 | 671 / 678 | 99.0% |

**This is the measurement behind `CLAUDE.md`'s standing rule**, which until now
rested on a probe rather than on the pool: *a frozen bar may never make a name
eligible — any selection, ranking or liquidity screen must exclude it.* A screen
that counts bars reads 120780 as having 578 sessions of history. A screen that
sums turnover reads it as zero. **Both are correct about different questions**,
and a per-day selection rule that uses the first is choosing a name that could
not have been traded on any of those days.

The 1,901+ row being empty and these eight rows existing are the same fact seen
twice: coverage measures listing, and nothing in a coverage number measures
whether a price moved.

### A reporting defect found while writing this up

`coverage()` prints *"N symbols carry >20 frozen sessions … M in total"*, where
**M is the sum over those same `>20` symbols rather than over the pool.** The
report therefore said 186,939 where the real total is **192,292**, understating
by 5,353 sessions spread across 554 symbols holding 1-20 frozen bars each.

The `>20` filter is deliberate and useful — a handful of halted days is noise, a
sustained stoppage is a property of the name — but the sentence reads as a pool
total. Fixed to print both, since the decision the report informs ("is this name
eligible?") is per-symbol and the pool figure is what a reader quotes.

## 4. What this closes, and what it does not

**Closed**: the collection half of the full-universe survivorship problem. A
per-day pool for 2019-2026 now exists with delisted names present on the days
they actually traded, and the halt count is available for any statistic that
needs to state which side of the halt question it took.

**Not closed, and unchanged from `rd-y` §2.5:**

- **Instrument type for delisted names.** The register publishes no type field,
  so `plain_codes` remains a floor rather than a filter and the pool carries
  preferred shares and SPACs. A common-stock-only historical universe still needs
  a source for this.
- **What a return across a halt means.** Booking the gap on the resumption bar
  and treating the flat stretch as zero volatility are wrong in different
  directions. The count is now available to state which was chosen; the choice is
  still a research decision with its own `Discuss`.
- **The 9 retryable failures.** A second pass would clear them; at 0.19% of the
  pool nothing waits on it.
- **The first alphanumeric delisting.** `plain_codes` filters on `isdigit()`,
  correct today and silently wrong for the first KOSDAQ alphanumeric code that
  delists.

**And one thing this result cannot do**, stated because the temptation is
immediate: the panel is the **spent** KRX daily window. Nothing measured here is
admissible as evidence for promoting anything. Coverage is a data-quality
measurement rather than a selection, so it increments no trial count — but the
pool it describes is open to generating hypotheses and closed to choosing one
for promotion.
