# Research Direction Task K — stage 2 corrected: 0 of 12, and the effect is a property of the null

**Run 2026-09-15** against
[`rd-j-stage2-corrected-specification.md`](rd-j-stage2-corrected-specification.md),
committed at `a0ca36c` **before any forward return was measured under
it**. Modules: `research/stage2_shift_null.py`.

**Discovery mode**, on the designated discovery window (Binance futures
1m, 3,661,780 bars, 6.96 years, already closed to selection). **Nothing
here may be promoted, quoted as evidence of an edge, or reported as a
pass.**

---

## 1. The headline

> **0 of 12 advance.** Smallest p = 0.064 against a Benjamini–Yekutieli
> rank-1 threshold of 0.00269.
>
> And the reason is more useful than the verdict: **rd-j's specified null
> was still wrong, in a third distinct way.** It advanced a test at
> p = 5e-04, and that survived a seam check, a day-multiple check and a
> local-era check — and did not survive holding volatility fixed.

## 2. What the specified null said, and why it was not the answer

rd-j replaced rd-h's exclusion-based matched control with a **circular
block shift**. That fixed the bias rd-h found. Under it:

| Situation | h | observed | null | effect | p | verdict |
|---|---|---|---|---|---|---|
| **S3 abnormal activity** | **240** | +21.12 | +2.20 | **+18.93** | **5.0e-04** | **ADVANCE** |
| S3 abnormal activity | 60 | +8.03 | +0.51 | +7.52 | 1.0e-03 | sig, small |
| S3 abnormal activity | 15 | +4.78 | +0.14 | +4.64 | 1.0e-03 | sig, small |
| S3 abnormal activity | 1440 | +45.42 | +12.35 | +33.07 | 1.25e-02 | — |
| S1 support penetration | 240 | +10.50 | +2.05 | +8.45 | 1.55e-02 | — |
| *(seven more, all p > 0.02)* | | | | | | — |

The **hour-preserving day-multiple shift agreed to 0.01bp** (S3 h=240:
+18.94bp, p = 5.0e-04), so rd-j's prediction 3 held and the intraday
cycle is not the story.

### 2.1 The seam — caught by a registered suspicion, for the second run running

rd-j §6 registered: *"if a test does advance, the first suspicion is the
null — specifically the seam and the shift range."* A test advanced, so
the seam was checked first, and it was real.

The first implementation computed `exit = (entry + horizon) % n`, so an
entry near the end of the series collected a return that ran back to the
start — on this data **one −87.4% observation**, since BTC opens at
10,000 and ends near 79,367. It biased every null draw in proportion to
`horizon / n`:

| h | predicted bias | measured `null − unconditional` |
|---|---|---|
| 15 | −0.04 bp | −0.03 bp |
| 60 | −0.14 bp | −0.16 bp |
| 240 | −0.57 bp | −0.72 bp |
| 1440 | **−3.44 bp** | **−3.78 bp** |

rd-j §2 had bounded the *offsets* away from the seam and missed that the
*return series itself* wrapped. Fixed so only the lookup position wraps
and the exit index never does; `null_bias` then collapsed to ≤0.1bp at
every horizon except 1440's −0.64bp.

**The advance survived the fix** (+18.93bp, p = 5.0e-04), so the seam was
not the explanation. It is recorded because the mechanism that found it —
naming the suspicion before the number exists — is now 2 for 2.

### 2.2 The shift range — checked, and also not the explanation

rd-j §6's other named suspicion. A global shift breaks alignment
everywhere but does not control **where in calendar time** the events
sit, and drift is not uniform across 2019–2026. A **local** shift, with
offsets restricted to ±W days, keeps the null inside the event's own era:

| S3, h = 240 | effect | p |
|---|---|---|
| ±7 days | +19.71 bp | 5.0e-04 |
| ±30 days | +19.42 bp | 5.0e-04 |
| ±90 days | +18.86 bp | 5.0e-04 |
| global | +18.99 bp | 1.0e-03 |

**Flat from one week to global.** And S3's events are calendar-uniform —
its yearly shares (4.2 / 16.0 / 13.4 / 15.4 / 14.0 / 13.7 / 13.2 / 10.1%)
track the bars' own (4.5 / 14.4 × 5 / 9.3%). S1 and S2 *are* clustered
(2021–22 at 20.3% and 20.6% against 14.4% of bars) and neither is
significant.

## 3. What it was: S3 is a volatility condition wearing a volume name

| Situation | median volatility decile | share in the **top** decile |
|---|---|---|
| S1 support penetration | 7 | 24.3% |
| S2 resistance break | 6 | 16.3% |
| **S3 abnormal activity** | **9** | **97.3%** |

S3 fires on top-1% prior-60-bar **volume**, and volume and volatility are
the same thing in different clothes. A circular shift preserves drift,
autocorrelation and era — and still compares **the market's most volatile
hours against all hours.**

rd-h §4 named the construction that holds this fixed and left it
untested: **match on volatility decile and hour, with no neighbourhood
exclusion.** No exclusion is what keeps it from repeating rd-g's failure —
there is no complement to be directionally biased — while the matching
holds the confound. Run at B = 2000, with an event never drawn as its own
control:

| Situation | h | events | observed | null | **effect** | **p** |
|---|---|---|---|---|---|---|
| S1 support penetration | 60 | 1,175 | +5.51 | +1.09 | +4.43 | **6.4e-02** |
| **S3 abnormal activity** | **240** | 786 | +21.12 | **+7.51** | **+13.61** | **6.4e-02** |
| S3 abnormal activity | 15 | 786 | +4.78 | +0.60 | +4.18 | 7.5e-02 |
| S1 support penetration | 240 | 1,175 | +11.23 | +3.77 | +7.46 | 8.5e-02 |
| S1 support penetration | 15 | 1,175 | +1.86 | +0.25 | +1.60 | 1.8e-01 |
| S3 abnormal activity | 60 | 786 | +8.03 | +2.53 | +5.51 | 1.9e-01 |
| S3 abnormal activity | 1440 | 785 | +45.42 | +31.56 | +13.86 | 3.6e-01 |
| S2 resistance break | 1440 | 1,164 | +24.62 | +16.69 | +7.92 | 4.1e-01 |
| S2 resistance break | 15 | 1,165 | −0.21 | +0.30 | −0.50 | 6.5e-01 |
| S2 resistance break | 240 | 1,165 | +3.82 | +2.29 | +1.53 | 7.1e-01 |
| S1 support penetration | 1440 | 1,175 | +22.23 | +18.83 | +3.40 | 7.4e-01 |
| S2 resistance break | 60 | 1,165 | +0.41 | +0.71 | −0.30 | 8.8e-01 |

**0 of 12.** The null mean for S3 h=240 rises from **+2.20 to +7.51bp**
once volatility is held fixed, the effect falls from **+18.93 to
+13.61bp**, and p moves from **5e-04 to 0.064** — two orders of
magnitude, from the same 786 events and the same forward returns.

## 4. The finding that outlives the verdict

> **The "effect" is a property of the (event, null) pair, not of the
> event.** S3 at h = 240, one event set, four comparisons:
>
> | compared against | effect | p |
> |---|---|---|
> | the unconditional mean | +18.95 bp | — |
> | a global circular shift | +18.93 bp | 5e-04 |
> | a ±7-day local shift | +19.71 bp | 5e-04 |
> | **volatility + hour matched** | **+13.61 bp** | **0.064** |
>
> **You cannot report "the effect of S3" without naming the null**, and
> the honest form of any future statement here is *"X bp against null Y."*

This is the third null this arc has built, and the pattern is consistent:
**each fixed its predecessor's flaw while carrying one of its own.**

| | controls | misses |
|---|---|---|
| rd-g exclusion-matched | volatility, hour | biased by directional exclusion (rd-h) |
| rd-j circular shift | drift, autocorrelation, era | **the volatility stratum** |
| matched, no exclusion | volatility, hour, no complement | the next one, presumably |

That is not a counsel of despair — it is why the baseline is reported
beside every arm, and why `null_suspect` vetoes on it. Both guards fired
usefully here: `null_bias` is what made the seam visible in the first
table rather than after publication.

## 5. What this does and does not establish

**Does not establish that these situations have no edge.** S3's
volatility-matched effect is **+13.61bp, which still exceeds the 12bp
cost floor**, at p = 0.064 over 786 events. That is **not shown**, not
**shown absent** — the distinction this project's own record insists on.

**A pattern worth naming without over-reading**: 10 of 12 effects are
positive, and the four smallest p-values cluster at 0.064–0.085. That is
what an underpowered weak signal looks like. It is also what noise looks
like, and nothing here separates the two.

**Does not establish that the matched null is correct.** It is the third
construction and the first to hold both the exclusion problem and the
volatility problem. Its own blind spots are not known.

**Does not produce a candidate.** No entry, exit, size or branch rule.

**Is not a Korean result.** [`rd-f`](rd-f-korean-cost-structure.md): the
Korean round trip is 2.5–2.8× harsher, so even a confirmed +13.61bp would
not clear it.

**rd-j's registered predictions, scored honestly**: (1) *fewer
significant cells* — **wrong**, the shift null produced more; (2) *S3
moves least between the two shift nulls* — **right**, 0.01bp; (3) *the
two agree within 2bp* — **right**; (4) *the most likely outcome is 0 of
12* — **right, but only after a third null.**

## 6. What follows

1. **Stage 3 is the honest next step, not a fourth null.** rd-b §4's
   stage 3 asks what separates the *branches* at decision time, and a
   situation with no mean shift can still have a tradeable branch
   structure — rd-b's own point about 개미털기, where the mean is
   uninformative precisely because it mixes two opposite outcomes.
2. **Power, before another mean test.** At 786 events and p = 0.064, the
   question "how many events would settle this" has an answer, and it is
   cheaper to compute than to run another null.
3. **The nulls stay in the tree.** `stage2_event_study` (matched,
   exclusion-based) and `stage2_shift_null` (shift + matched) are both
   kept so rd-h's and this document's numbers remain reproducible.
