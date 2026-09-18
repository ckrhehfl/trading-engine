# Research Direction Task U — do conditions COMBINED beat their parts?

**Measured 2026-09-18.** Module: `research/krx_conjunction.py`. Reproducible
with

```
python -m research.krx_conjunction
```

**Discovery mode**, so the promotion `N` is not incremented and nothing
here may be promoted, quoted as evidence of an edge, or reported as a
pass. The only legitimate output is a written specification.

This is the question [`rd-t`](rd-t-korean-signal-ic.md) did not ask.
rd-t measured **eight single features** and closed the door on seven of
them. It is tempting to read that as *"Korea does not work"* — and this
project has a name for that mistake: **never conclude about a domain from
one parameter setting.** A trader does not use one feature; a trader uses
a conjunction, and a conjunction is a different object from its parts.

It also answers the operator's own question directly ("여러 조합을 조합해서
복합적으로"), and it was the cheapest thing left to run: the data was
already collected, the conditions are all adopted rather than searched,
and discovery mode means no penalty attaches.

---

## 1. The headline, in two parts that point opposite ways

> **The conjunction really does beat its parts — by about 2.8× — and it
> still does not clear the cost floor, and after the independence
> correction nothing survives multiple-testing at all.**

| | excess over the unconditional mean |
|---|---|
| best single condition | **−4.1 bp** |
| best pair | **−11.4 bp** |
| all four together | **−10.4 bp** |
| the bar (rd-q's round trip) | **13.0 bp** |

The structural claim is real and is the transferable part: **combining
conditions multiplies the effect rather than averaging it.** The best pair
is not merely better than its better half (−4.1), it is larger than the
**sum** of both halves (−4.1 and −3.2, so −7.3) by a factor of 1.56. That
is superadditivity, and nothing in the 1,883 logged trials could have seen
it, because every one of them measured a single formula.

The verdict is equally real: **−11.4 bp against a 13 bp round trip is a
loss**, and `0 of 11` combinations survive Benjamini-Hochberg. Two
independent reasons to stop, in the same table.

## 2. Where the conditions came from — none of them from a search

**Nothing here was searched for**, which is what keeps this at one
hypothesis rather than a new search, and is the same discipline Task D
used when it took its entry rule from Larry Williams.

| condition | source | what it says |
|---|---|---|
| `overnight_strong_1m` | **Lou, Polk & Skouras (2019)**, *JFE* 134(1):192-213 | above-median mean overnight return over the last month |
| `intraday_weak_1m` | the same paper, the other leg | below-median mean intraday return over the last month |
| `recent_laggard_5d` | `rd-t` (cross-sectional reversal) | below-median total return over 5 days |
| `vol_low_21d` | `scalp-s10` (the continuous conditioner) | below-median 21-day realised volatility |

**Lou-Polk-Skouras is the reason to run this in Korea specifically.** Their
"tug of war" is between investor clienteles — retail trades near the open,
institutions near the close — and the two components carry **opposite
signs**, with a strategy's whole profit sitting in one of them. They had
to *proxy* the clienteles (institutional active weight, "comomentum").
**KRX mandatorily discloses 개인/기관/외국인 per stock per day.** The entire
US literature on retail order flow exists because researchers there had to
infer what Korea publishes.

**Their construction does not transfer as written, and that is stated
rather than glossed.** They sort thousands of names into deciles and trade
the extreme tenths. Ten names make a "decile" one name, which is noise. So
the decomposition is measured as a **panel** question — does the overnight
component predict what follows — rather than as a decile long-short.

Two further literature findings that bear on how to read §3:

- **KOSPI Market Intraday Momentum** (MDPI, 2022) and **Seok, Cho & Ryu
  (2019)** both study Korean overnight returns directly.
- **Asian markets are documented to flip the US sign.** So the direction
  here is measured, never assumed — which is why the module prints the
  decomposition before anything else.

## 3. The decomposition, measured

| | |
|---|---|
| mean **overnight** move (`open_t / close_{t-1} − 1`) | **+20.48 bp/day** |
| mean **intraday** move (`close_t / open_t − 1`) | **−2.94 bp/day** |

**Essentially the entire drift of these ten names over the panel
happened while the market was shut.** A position taken at the open and
closed at the close captured −2.94 bp a day on average; the +20.48 bp went
to whoever held overnight.

That is Lou-Polk-Skouras' shape — the profit sitting in one component —
and it has a blunt practical consequence: **the leg a day-trading futures
position can actually capture is the one with the negative mean.** Every
h=1 figure in §5 is measured on that leg, which is why its baseline is
−3.0 bp rather than ~0.

## 4. Two design errors from Task C, both fixed, plus one new one found here

**Fix 1 — median splits, not extreme thresholds.** Task C's four
conditions fired 364/348/479/912 times individually and **2 times jointly
in 2,544 bars**, which is unmeasurable. Every condition here is a
cross-sectional median split, so each fires on ~50% of the universe by
construction and the four-way conjunction fires on **7.3%** of name-days.
A median split also has **no free parameter** — nothing to search, nothing
to overfit, nothing added to `N`.

**Fix 2 — the holding period is declared, not implied.** Task C's exit was
"the setup no longer holds", which any one of three conditions relaxing
could end, so its holding period was pinned to ~1 hour *by accident* and
it tested something other than the hypothesis described. Here the horizon
is stated up front (1 and 5 days) and the position held exactly that long.

**Fix 3 — a day is one observation, not ten. This one was found here and
it changed the verdict.** A cross-sectional study has an independence
problem a single-symbol one does not: ten names on the same day share a
market-wide move, so pooling 11,740 name-days as 11,740 draws understates
the standard error. This is **S13's overlapping-excursion error wearing
different clothes**, and CLAUDE.md already carries the standing rule for
it — *deduplicate before reporting a t-statistic, or state that you did
not.* Every statistic here is therefore computed over **dates**, each date
contributing the mean across whichever names fired.

**The correction was not a uniform haircut and was not predictable from
the naive figures:**

| combination | p pooled by name-day | p clustered by date |
|---|---|---|
| `overnight_strong_1m & intraday_weak_1m` | 0.016 | **0.182** |
| `intraday_weak_1m & recent_laggard_5d` | 0.113 | **0.039** |
| `overnight_strong_1m & vol_low_21d` | 0.002 | **0.008** |
| `intraday_weak_1m & vol_low_21d` (excess) | −4.7 bp | **−0.7 bp** |

It moves in **both directions**, and it took the count of BH survivors
from **1 to 0**. Had this study been written up before the correction, its
headline would have been a surviving pair at p = 0.002.

**The same defect was found independently in rd-t, on review, at the same
time** — [`rd-t` §5.1](rd-t-korean-signal-ic.md). Two tasks arriving at it
by different routes within a day is the reason to treat it as a standing
construction rule rather than a fix to one module: **a p-value computed
over observations that share a session is not a significance test.** rd-t
also records the asymmetry — the correction is large on a construction
that pools names at one instant and near-nil on one that collapses each
instant to a single number first, which is the mechanism behind why it
bites this study at all.

## 5. The clean read: held 1 day, entered at the next open

Baseline (unconditional) **−3.0 bp**, 1,174 dates. The bar is
`|excess| > 13 bp`, since a round trip is paid in either direction.

| combination | name-days | dates | mean bp | excess | p | BH |
|---|---|---|---|---|---|---|
| `overnight_strong_1m` | 5,765 | 1,153 | −7.1 | −4.1 | 0.178 | no |
| `intraday_weak_1m` | 5,765 | 1,153 | −6.9 | −4.0 | 0.154 | no |
| `recent_laggard_5d` | 5,845 | 1,169 | −4.5 | −1.5 | 0.353 | no |
| `vol_low_21d` | 5,765 | 1,153 | −6.2 | −3.2 | 0.134 | no |
| `overnight_strong_1m & intraday_weak_1m` | 3,221 | 1,150 | −8.6 | −5.6 | 0.182 | no |
| `overnight_strong_1m & recent_laggard_5d` | 2,542 | 1,132 | −8.6 | −5.6 | 0.198 | no |
| **`overnight_strong_1m & vol_low_21d`** | 2,360 | 1,139 | **−14.4** | **−11.4** | **0.008** | no |
| `intraday_weak_1m & recent_laggard_5d` | 3,567 | 1,153 | −11.2 | −8.2 | 0.039 | no |
| `intraday_weak_1m & vol_low_21d` | 3,247 | 1,145 | −3.7 | −0.7 | 0.464 | no |
| `recent_laggard_5d & vol_low_21d` | 3,060 | 1,139 | −3.7 | −0.7 | 0.463 | no |
| all four | 861 | 696 | −13.3 | −10.4 | 0.104 | no |

**Every excess is negative**, i.e. every combination selects names that go
on to *underperform* — so the tradeable expression is a **short**. That
is stated because an earlier version of this module could not see it: it
tested `mean − base > COST_FLOOR` and so recognised a long edge only,
reporting nothing while the entire result was short. The test is now on
the **magnitude** of the excess (`abs`), and a guard-removal check
confirms it fails when that `abs` is taken out.

## 5.1 "Does not clear the cost floor" is NOT a statement about costs

Added 2026-09-18 after the operator asked whether the round trip was
beating us because this project lacks an institution's infrastructure.
The answer is measurable and it is no, and the measurement reframes §1.

Every figure in this section is produced by `research/krx_cost_context.py`
— `python -m research.krx_cost_context` — on the same panel, rather than
by a one-off script. That module was written *because* this section was
first drafted from figures with no committed generating path.

**A KRX day moves about ten times the cost of trading it.** Absolute
intraday move (`open → close`), 11,550 usable name-days:

| | move | vs the 13 bp round trip |
|---|---|---|
| p25 | 59.7 bp | **4.59×** |
| **median** | **131.2 bp** | **10.09×** |
| p75 | 251.1 bp | 19.32× |
| p99 | 908.5 bp | 69.88× |

Even the **quietest decile** of days, by prior-month realised volatility,
has a median move of 75.2 bp — **5.8×** the round trip. There is no
regime in this data where the cost is the binding constraint.

**So the failure is direction, not cost.** The best combination extracted
**11.4 bp of a 131 bp median move — 8.7% of what was available**, where
10% would have paid for the trade. Stating it as *"it did not clear the
cost floor"* is arithmetically true and invites exactly the wrong
inference; the honest form is **"it predicted almost none of a move that
is enormous relative to its cost."**

**What this says about infrastructure, scoped to what was measured.** The
moves above are computed from **printed prices** — open to close — with no
slippage, no spread, no borrow and no size-dependent execution effect
modelled anywhere, while the 13 bp is `rd-q`'s measured round trip. So the
claim this evidence supports is: **there is no sign in this data that
execution cost is the binding constraint, and therefore no evidence that
cheaper execution would have rescued the result.** It does **not**
establish that execution quality is irrelevant, because execution quality
was never measured here — an earlier draft of this section said latency
and colocation "buy nothing", which is a stronger claim than the
measurement can carry, and it was removed on review.

**The institutional advantage this analysis does point at is breadth, not
speed** — `IR ≈ IC × √breadth` over ~2,700 names rather than 10 — and what
blocks that here is the delisted-symbol gap in KIS's master files
(`rd-d` §2.2). That is a data problem, and it is the one the arithmetic
above actually implicates.

**The obvious remedy was tested and does not work.** If costs are not
binding, the natural move is to trade only the large, volatile moments —
which is the operator's own instinct and matches `scalp-s8`'s retraction
(conditioning on activity moved a 0.40× cost ratio to 2.08×) and
Zarattini's "Stocks in Play". Measured here on the reversal signal, one
cross-sectional IC per date, 1,153 dates:

| prior-month volatility | dates | IC | p | median move that day |
|---|---|---|---|---|
| quiet | 384 | **+0.0177** | 0.370 | 106 bp |
| middle | 384 | −0.0053 | 0.793 | 124 bp |
| volatile | 385 | −0.0008 | 0.966 | 170 bp |

**The move triples and the predictability does not follow it** — if
anything the (insignificant) ordering runs the other way. Volatility buys
a bigger prize and no better odds on it.

**This closes one cell, not the domain**, and the distinction is this
project's most-repeated mistake. What was tested is *one* signal
(cross-sectional 5-day reversal), on *one* universe (10 names), at *one*
horizon (daily), conditioned on *one* volatility measure. Zarattini's
filter is abnormal **turnover**, not realised volatility, and rd-t §4
records that per-bar 거래대금 is exactly what KIS does not serve — so the
literature's strongest filter still has not been tested here at all.

## 6. h = 5 is confounded, and the module says so in the output

At h=5 the baseline is **+66.4 bp** and *eleven of eleven* combinations
"survive" BH, with six "clearing" the cost floor. **None of that is a
finding**, and the module prints a `*** CONFOUNDED ***` banner above the
table rather than leaving it to a reader:

- a 5-day window contains **4 overnight gaps**, and §3 showed the drift
  lives entirely in those gaps;
- this universe was selected by `rd-r` on **2026Q1 futures liquidity**,
  which correlates with having risen over 2021-2026.

So an "excess" there is largely a measure of **which names drifted**, not
of what a condition predicts. Concretely: *"`vol_low_21d` clears the cost
floor at −32.5 bp"* reduces to *"low-volatility names rose less than
semiconductors did over 2021-2026"*, which is close to a tautology.

The h=1 block is the clean read precisely because its baseline is near
zero, so its excesses are signal rather than composition.

## 7. The conjunction is overlapping here, which is the opposite of Task C

| | |
|---|---|
| all four fire together on | **7.321%** of name-days |
| if they were independent | **5.896%** |

**1.24× the independent product**, so these four conditions fire together
*more* often than chance — they share information. Task C's one positive
result was the reverse (2 firings against what correlated conditions would
have produced), and its independence premise held.

Both facts matter and they are not in tension: **the conditions overlap,
and their conjunction is still superadditive in effect.** Overlap caps how
much `IR ≈ IC × √breadth` can be claimed — four overlapping conditions are
not four independent bets — while the measured effect compounding beyond
the sum of its parts is a separate, real observation about conjunctions.

## 8. What this does not establish

**It is not evidence of an edge and may not be quoted as one.** Discovery
mode's first guard.

**The result is negative on its own terms, twice over.** −11.4 bp against
a 13 bp round trip, and `0 of 11` surviving BH. Neither number is close
enough to a pass to describe this as marginal.

**Costs are the floor, not the whole cost model.** rd-q's ~13 bp is a
round trip for the futures-liquid names. A cross-sectional long-short pays
it on both legs, and nothing here models slippage, borrow, or the
single-stock-futures spread at size.

**The panel is 1,176 dates × 10 names, spanning 2021-11-29 → 2026-09-17
(4.80 years).** An earlier draft of this document said "2019-2026" and
"seven years" in three places, carried over from `rd-r`'s description of
the KR-10 universe; this panel is shorter because the inner join is bound
by the eight newer names' listing dates. Corrected 2026-09-18. The inner join on date drops 718
dates, and — measured, not assumed — **none of them lies inside the common
span**; every drop is an end effect from the eight newer names listing
later. `interleaved_sessions` now refuses a run where a session *is*
dropped mid-span, because the overnight legs either side of such a hole
would span two nights and be reported as one. That is rd-p's gap rule,
applied on the daily axis, and it had never been checked.

**The universe carries rd-t's mild look-ahead**, unchanged: rd-r selected
these ten on 2026Q1 futures turnover, inside the measurement window.
Bounded rather than removed — rank persistence is Spearman +0.954.

**투자자별 매매동향 is still absent, and it is the whole reason to prefer
Korea.** §2's mechanism is about *who* is trading at the open versus the
close, and Korea publishes exactly that. Collection started 2026-09-14 and
**cannot be backfilled**, so there is one month of it. Everything measured
here used prices only — i.e. the proxy, not the observable.

## 9. What follows

1. **The conjunction shape is confirmed as worth using and this
   particular conjunction is not.** Carry the finding, drop the four
   conditions.
2. **The obvious next conjunction is the one this could not include.**
   §2's mechanism is a claim about investor clienteles; the direct
   measurement of it needs 매매동향 depth. That is the single highest-value
   thing to wait for, and waiting is productive rather than idle.
3. **h=1 on the intraday leg is the right measurement frame** and should
   be reused: its baseline is near zero, so it does not confound
   composition with prediction, and it is the leg a futures position can
   capture.
4. **Any future combination study must cluster by date.** §4's Fix 3 is
   not specific to these conditions — it applies to every cross-sectional
   measurement this project will make from here, and it changed this
   study's verdict.
5. **Then a specification, committed before any confirmation run**, with
   the entry taken from outside where possible and the management policy
   adopted from Task D rather than re-searched.

## Appendix — the guard-removal check

Per CLAUDE.md's Change checks, each guard was removed and the suite
re-run. **Seven of seven produce a failure**, verified rather than read:

| guard | removed by | result |
|---|---|---|
| entry at the **next** open | `o[t+1]` → `o[t]` | 2 failed |
| a median split needs 3+ names | `if ok.sum() < 3` → `if False` | 1 failed |
| the cost test is sign-agnostic | `abs(excess)` → `excess` | 1 failed |
| the overnight leg gaps from the previous row | compare against `prev_close` | 1 failed |
| an end effect is not a hole | drop the `lo < d < hi` bound | 1 failed |
| no combination measured twice | `sorted({2, n})` → `(2, n)` | 1 failed |
| a date is one observation | pool name-days | 2 failed |

**The fourth row is the one worth keeping.** The refusal was originally
written as *"the two legs must rebuild the close-to-close move"* — and
that is an **algebraic identity**: `(open/pc)·(close/open) − 1 = close/pc
− 1` holds for any `prev_close` whatsoever. A test written to watch it
fail returned `2.2e-16` on a deliberately broken panel, which is how the
inert guard was found. Comparing against `close_px` shifted one row
instead asks the question that can actually be wrong. This is the same
class as `conftest.py`'s three inert isolation fixtures, caught one step
earlier than usual — by writing the failing test first rather than by an
external observable afterwards.
