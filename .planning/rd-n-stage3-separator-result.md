# Research Direction Task N — stage 3 result: 0 of 18, the null was finally right, and the family was still too small

**Run 2026-09-15** against
[`rd-m-stage3-separator-specification.md`](rd-m-stage3-separator-specification.md),
committed at `40cb0e8` **before any separator's forward return was
measured under it**. Module: `research/stage3_separator.py`.

**Discovery mode**, on the designated discovery window (Binance futures
1m, 3,661,780 bars, 6.96 years, already closed to selection). **Nothing
here may be promoted, quoted as evidence of an edge, or reported as a
pass.**

---

## 1. The headline

> **0 of 18 advance.** Smallest p = **0.0405** against a
> Benjamini–Yekutieli rank-1 threshold of **0.001590** — short by a factor
> of 25.
>
> **The instrument was finally right.** Null calibration ran **0.980 to
> 1.017** across all eighteen, against **0.42** for the stage-2 matched
> null that [`rd-l`](rd-l-what-it-would-cost-to-know.md) §4.1 had just
> exposed. rd-m §2 argued a label permutation would be self-calibrating
> by construction; it is, and now that is a measurement.
>
> **And the family was still underpowered — including by rd-m's own
> table, which inherited rd-l's fault.** The real detectable difference is
> **19.4–44.2bp**, not the projected 8.85–33.25, and **no cell in the
> family was powered for a tradeable branch at all.**
>
> **The composition guard did the most work.** **14 of 18** splits would
> have been vetoed as SPLIT-CONFOUNDED had they been significant,
> including **6 of 6** for the separator whose whole design was supposed
> to prevent it.

## 2. The eighteen

`m = 18`, BY q = 0.10, rank-1 threshold 0.001590, B = 2000, branch floor
12bp. Branch means and differences in basis points; `cal` is
`null_sd / analytic se`.

| situation | h | separator | n hi/lo | hi | lo | diff | sd | cal | p | TVDvol (99%) | TVDhr (99%) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 support penetration | 60 | X2 extremity | 598/598 | −3.21 | +12.51 | **−15.72** | 7.84 | 1.00 | **4.05e-02** | 0.468 (0.110) | 0.135 (0.154) |
| S3 abnormal activity | 15 | X1 taker-buy share | 393/393 | −2.58 | +12.14 | **−14.72** | 7.27 | 1.01 | **4.15e-02** | 0.008 (0.028) | 0.117 (0.183) |
| S1 support penetration | 15 | X1 taker-buy share | 598/598 | +6.52 | −3.91 | +10.43 | 6.09 | 1.02 | 8.05e-02 | 0.219 (0.114) | 0.137 (0.154) |
| S3 abnormal activity | 60 | X3 prior trend | 393/393 | +0.20 | +15.87 | −15.67 | 10.88 | 0.98 | 1.49e-01 | 0.003 (0.028) | 0.170 (0.181) |
| S3 abnormal activity | 15 | X3 prior trend | 393/393 | +0.31 | +9.25 | −8.93 | 7.13 | 0.99 | 2.12e-01 | 0.003 (0.028) | 0.170 (0.178) |
| S2 resistance break | 60 | X1 taker-buy share | 585/585 | +4.18 | −3.34 | +7.52 | 7.14 | 0.98 | 2.98e-01 | 0.234 (0.116) | 0.209 (0.156) |
| S2 resistance break | 15 | X2 extremity | 585/585 | −2.58 | +1.93 | −4.51 | 4.83 | 1.00 | 3.47e-01 | 0.402 (0.115) | 0.179 (0.150) |
| S2 resistance break | 60 | X2 extremity | 585/585 | +3.70 | −2.86 | +6.56 | 7.23 | 0.99 | 3.51e-01 | 0.402 (0.113) | 0.179 (0.152) |
| S1 support penetration | 60 | X1 taker-buy share | 598/598 | +7.33 | +1.97 | +5.36 | 7.96 | 1.02 | 5.07e-01 | 0.219 (0.110) | 0.137 (0.152) |
| S1 support penetration | 15 | X2 extremity | 598/598 | −0.54 | +3.16 | −3.70 | 6.02 | 1.01 | 5.35e-01 | 0.468 (0.114) | 0.135 (0.152) |
| S1 support penetration | 60 | X3 prior trend | 598/598 | +3.07 | +6.23 | −3.16 | 7.86 | 1.00 | 6.80e-01 | 0.351 (0.112) | 0.132 (0.151) |
| S1 support penetration | 15 | X3 prior trend | 598/598 | +2.44 | +0.18 | +2.25 | 6.04 | 1.01 | 6.97e-01 | 0.351 (0.109) | 0.132 (0.151) |
| S2 resistance break | 15 | X1 taker-buy share | 585/585 | +0.53 | −1.17 | +1.70 | 4.92 | 1.02 | 7.20e-01 | 0.234 (0.115) | 0.209 (0.154) |
| S2 resistance break | 15 | X3 prior trend | 585/585 | +0.40 | −1.04 | +1.44 | 4.90 | 1.01 | 7.24e-01 | 0.262 (0.113) | 0.115 (0.152) |
| S3 abnormal activity | 60 | X1 taker-buy share | 393/393 | +6.20 | +9.86 | −3.67 | 10.99 | 0.99 | 7.47e-01 | 0.008 (0.031) | 0.117 (0.178) |
| S3 abnormal activity | 15 | X2 extremity | 393/393 | +5.83 | +3.73 | +2.10 | 7.14 | 0.99 | 7.50e-01 | 0.033 (0.031) | 0.122 (0.178) |
| S2 resistance break | 60 | X3 prior trend | 585/585 | +1.40 | −0.56 | +1.95 | 7.19 | 0.99 | 7.88e-01 | 0.262 (0.115) | 0.115 (0.154) |
| S3 abnormal activity | 60 | X2 extremity | 393/393 | +8.65 | +7.41 | +1.24 | 11.11 | 1.00 | 9.29e-01 | 0.033 (0.031) | 0.122 (0.181) |

**Zero events dropped anywhere.** Every event carried a defined separator
and its own forward window.

## 3. The instrument was right, and that is the one clean win

| null | calibration (`null_sd` / the statistic's own se) |
|---|---|
| rd-g exclusion-matched | not measured; retired for a different fault |
| rd-k matched, no exclusion | **0.42 – 0.88** (rd-l §4.1) |
| **rd-n label permutation** | **0.980 – 1.017** |

rd-m §2 predicted this from the construction: both branches are drawn
from the same events, so the permutation distribution **is** the sampling
distribution of the difference, and there is no second arm whose
dispersion could differ. Every confound that affects a situation's events
equally — drift, era, the volatility stratum, the seam — cancels in the
difference rather than needing to be modelled.

**Four nulls in, that question is closed.** What remains is not a null
problem.

## 4. What the composition guard found, which is the substantive result

**14 of 18** splits exceeded their own permutation's 99th-percentile
composition distance, and would have been vetoed as SPLIT-CONFOUNDED had
any reached significance.

| separator | confounded | smallest p | what it splits on |
|---|---|---|---|
| **X2 extremity** | **6 of 6** | 0.0405 | volatility, TVD **0.402–0.468** against a 0.11 null |
| X1 taker-buy share | 4 of 6 | 0.0415 | volatility on S1/S2 (0.219, 0.234); **clean on S3** (0.008) |
| X3 prior trend | 4 of 6 | 0.1494 | volatility on S1/S2 (0.262, 0.351); **clean on S3** (0.003) |

> **X2 was explicitly designed not to do this and did it anyway.** rd-m §4
> normalises penetration depth by prior realised volatility precisely so
> that "how far past the level" is not "how volatile the market is", and
> §5 said the guard would *check whether that succeeded rather than
> assume it*. It did not succeed: a TVD of 0.468 against a 0.110 null is
> the most confounded split in the family, on the separator with the
> explicit anti-confound design.

**And the clean splits are clean for an uninteresting reason.** S3 is the
only situation whose splits pass, and rd-k §3 already measured why: S3
sits in the **top volatility decile 97.3%** of the time, so there is
almost no volatility variation left for a separator to accidentally sort
on. Its composition distance is *below* the null (0.008 against 0.028)
because its events are nearly homogeneous.

So the guard's real message is not "S3's separators are better." It is:
**on a directional situation, every observable tried here is substantially
a volatility measurement.** That is rd-k's finding one level down —
rd-k found S3 *is* a volatility condition wearing a volume name; rd-n
finds the separators are volatility conditions wearing three other names.

## 5. The family was underpowered, and rd-m's own table said otherwise

rd-m §3 projected the detectable difference from stage 2's `null_sd`,
which rd-l §4.1 then showed is **1.05–2.45× too narrow**. The projection
inherited the fault. Recomputed from the realised dispersion:

| situation | h | n/branch | event σ | realised se(diff) | **detectable difference** | rd-m projected | **detectable branch mean** | powered for a 12bp branch? |
|---|---|---|---|---|---|---|---|---|
| S1 support penetration | 15 | 598 | 103.5 | 5.99 | **23.94** | 9.79 | 16.93 | **no** |
| S2 resistance break | 15 | 585 | 82.9 | 4.85 | **19.39** | 8.85 | 13.71 | **no** |
| S3 abnormal activity | 15 | 393 | 101.3 | 7.22 | **28.89** | 18.70 | 20.43 | **no** |
| S1 support penetration | 60 | 598 | 135.4 | 7.83 | **31.32** | 19.07 | 22.15 | **no** |
| S2 resistance break | 60 | 585 | 124.6 | 7.28 | **29.14** | 17.24 | 20.60 | **no** |
| S3 abnormal activity | 60 | 393 | 155.1 | 11.06 | **44.24** | 33.25 | 31.28 | **no** |

The largest difference actually observed anywhere in the family is
**15.72bp**, below every one of those thresholds. So:

> **No cell in this family could have detected a difference of the size it
> actually observed, and none was powered to establish a tradeable branch
> at all.** rd-m §3 stated that only S1 and S2 at h=15 would be powered
> for a tradeable branch; the corrected figures say **none** were.

**The uncomfortable part, stated rather than buried.** rd-l §8 item 2 laid
down the rule — *"a family whose detectable effect exceeds its own cost
floor cannot produce a candidate, and should be re-specified or not
run"* — and rd-m applied it, excluding h=240 and h=1440 on exactly those
grounds. **It applied the rule with the wrong numbers.** The rule was
right and the input to it was not, which is a more useful failure than
either half alone: a correct procedure fed a bad estimate still produces
a family that cannot answer its question.

## 6. The registered predictions, scored honestly

rd-m §7's five, and the one that failed is worth more than the four that
did not.

| # | prediction | outcome |
|---|---|---|
| 1 | most likely 0 of 18 | **right** |
| 2 | if anything separates it will be X1, not X2/X3 | **half.** X2 has the smallest raw p (0.0405) by a hair over X1 (0.0415) — but X2 is confounded 6 of 6 and the only *clean* near-significant split in the family is X1 on S3. On the confound-adjusted reading it holds; on the raw p it does not, and both are reported |
| 3 | S3 will separate least, being non-directional | **wrong.** S3 holds the second-smallest p and the only clean splits; **S2** separated least (smallest p 0.298). The reasoning — no level, so no structure for a branch — did not survive contact |
| 4 | realised `se_diff` within 20% of 2 × stage 2's `se_full` | **wrong, by 2.5×** — and it is the most valuable wrong prediction this arc has produced. It found rd-l §4.1, a fault that had survived three nulls and two result documents |
| 5 | S1 and S2 at h=15 show \|difference\| above their detectable 9.79 / 8.85bp | **half.** S1 reaches 10.43; S2 reaches 4.51. And the thresholds were both wrong — see §5 |

**Prediction 4 is the third defect a registered prediction has caught**
(rd-j §6 → the seam; rd-g §6 → the placebo artifact; rd-m §7 → the
under-dispersed null). The practice is now three for three at finding
instrument faults its author did not suspect, and each time the
prediction that fired was the one written as a formality.

## 7. What this does and does not establish

**Does not establish that no observable splits these branches.** The
family is six situation×horizon cells and three separators, and §5 shows
none of them was powered for a tradeable branch. A negative from an
underpowered study is **not shown**, not **shown absent** — the same
distinction rd-l made quantitative.

**Does establish that the nulls are no longer the bottleneck.**
Calibration 0.98–1.02. Four nulls of work ends here; the remaining
problem is sample size and confounding, not construction.

**Does establish that three plausible separators are mostly volatility.**
14 of 18 splits, including 6 of 6 for the one designed against it. Any
future separator on a directional situation must carry this guard, and
normalising by prior volatility is demonstrably not sufficient to pass it.

**Does not produce a candidate.** No entry, exit, size, invalidation or
decline rule, and the in-sample median split could not be a decision rule
even if one of these had separated.

**Is not a Korean result.** BTC 1m.
[`rd-f`](rd-f-korean-cost-structure.md) puts the Korean round trip
2.5–2.8× higher, so the 12bp branch floor is the wrong floor there.

**Does not retire 개미털기.** The mechanism predicts a branch structure,
and this family could not resolve one at the size it would have to have.
That is a statement about 786–1,196 events on one instrument.

## 8. What follows, and the stopping rule binds

rd-m §8 forecloses re-running these eighteen with adjusted separators,
thresholds, horizons or split rules. **That rule binds here**, and the
temptation it forecloses is concrete: X1 on S3 at h=15 sits at p = 0.0415
with a clean split, and re-testing it alone with a different threshold is
exactly what the rule exists to prevent.

The permitted responses:

1. **A power calculation before the next family, from the event arm's own
   dispersion.** rd-l §8 item 2's rule, with §5's correction to its input.
   The 19.4–44.2bp resolution measured here is the number a successor
   specification has to design against. Resolving a **12bp branch mean**
   on these six cells needs **1.3× to 6.8×** the events (median **2.9×**,
   cheapest at S2 h=15, dearest at S3 h=60) — i.e. the cross-section rd-l
   §5 already priced, not more BTC time.
2. **A separator that survives the composition guard on a directional
   situation.** Nothing here did except on S3, and S3 passes only because
   it has no volatility variation to confound with. That is an open design
   problem, and it is more specific than "find a better feature."
3. **Not a fourth-generation null.** §3 closes that line: the label
   permutation is calibrated, and nothing further is bought by rebuilding
   it.

**Reproducible with one command:**

```
python -m research.stage3_separator
```
