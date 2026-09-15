# Research Direction Task L — what it would cost to know: 5 of 12 are already answered, and the other 7 need decades

**Run 2026-09-15.** Module: `research/event_power.py`. Reproducible with

```
python -m research.event_power                          # BTC, 12bp round trip
python -m research.event_power --cost-floor 0.0033      # rd-f's Korean one
```

[`rd-k`](rd-k-stage2-corrected-result.md) §6 item 2 named this as the
next step and said why: *"At 786 events and p = 0.064, the question 'how
many events would settle this' has an answer, and it is cheaper to
compute than to run another null."* No new data was touched — this reads
the same twelve tests rd-k reported.

**Discovery mode**, on the designated discovery window. **Nothing here
may be promoted, quoted as evidence of an edge, or reported as a pass.**

---

## 1. The headline

> **rd-k's "0 of 12" was two different results wearing one label.**
>
> **5 of the 12 are settled**: their confidence interval lies entirely
> inside ±12bp, so the data are inconsistent with an effect large enough
> to pay for its own round trip. For trading purposes that is **shown
> absent**, which is strictly more than "not shown."
>
> **7 of the 12 were never answerable on this window**, and the arithmetic
> says so without reference to any result: at h = 240 the smallest effect
> this study could have detected is **20.2bp**, against a **12bp** cost
> floor. A tradeable effect was **below the instrument's resolution
> before the first event was counted.**

## 2. The instrument, in one line

A permutation test's null distribution is the sampling distribution of
its own statistic, so `null_sd` **is** the standard error — carrying the
overlapping forward windows, the matched strata and the draw structure
without anything having to be assumed about independence. From there:

```
n_required = n_observed × ( (z[1−α/2] + z[power]) × null_sd / effect )²
```

with **α = 0.002685**, the Benjamini–Yekutieli rank-1 threshold, because
that is the bar rd-j's decision rule actually sets. A nominal α = 0.05
would need **0.53×** these counts, so quoting one would understate the
bill by nearly half.

**The normal approximation is reported, not assumed.** `normal_p` sits
beside the real permutation p in every row, and across all twelve the
worst relative divergence is **8.3%** (S1 at h=15, 0.176 against 0.191);
the two leading cells agree to ~1% (6.40e-02 against 6.31e-02). Where
they had disagreed, the power figure beside them would have been the
number to distrust.

## 3. The twelve, priced

α = 0.002685, power 80%, cost floor 12bp, `matched` null.

| situation | h | events | effect | se | z | detectable | 99.73% CI | n@12bp | years | instruments | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 support penetration | 15 | 1,175 | +1.60 | 1.22 | 1.31 | 4.70 | [−2.07, +5.28] | 180 | 1.1 | 0.2 | **EXCLUDED** |
| S1 support penetration | 60 | 1,175 | +4.43 | 2.38 | 1.86 | 9.16 | [−2.73, +11.58] | 685 | 4.1 | 0.6 | **EXCLUDED** |
| S1 support penetration | 240 | 1,175 | +7.46 | 4.28 | 1.74 | 16.45 | [−5.39, +20.31] | 2,209 | 13.1 | 1.9 | UNDERPOWERED |
| S1 support penetration | 1440 | 1,175 | +3.40 | 9.93 | 0.34 | 38.15 | [−26.40, +33.19] | 11,875 | 70.4 | 10.1 | UNDERPOWERED |
| S2 resistance break | 15 | 1,165 | −0.50 | 1.11 | −0.45 | 4.25 | [−3.82, +2.82] | 146 | 0.9 | 0.1 | **EXCLUDED** |
| S2 resistance break | 60 | 1,165 | −0.30 | 2.15 | −0.14 | 8.28 | [−6.76, +6.17] | 555 | 3.3 | 0.5 | **EXCLUDED** |
| S2 resistance break | 240 | 1,165 | +1.53 | 4.16 | 0.37 | 15.99 | [−10.95, +14.02] | 2,068 | 12.4 | 1.8 | UNDERPOWERED |
| S2 resistance break | 1440 | 1,164 | +7.92 | 9.73 | 0.81 | 37.39 | [−21.28, +37.12] | 11,298 | 67.6 | 9.7 | UNDERPOWERED |
| S3 abnormal activity | 15 | 786 | +4.18 | 2.34 | 1.79 | 8.98 | [−2.84, +11.20] | 441 | 3.9 | 0.6 | **EXCLUDED** |
| S3 abnormal activity | 60 | 786 | +5.51 | 4.16 | 1.32 | 15.98 | [−6.97, +17.98] | 1,393 | 12.3 | 1.8 | UNDERPOWERED |
| **S3 abnormal activity** | **240** | **786** | **+13.61** | **7.33** | **1.86** | **28.15** | **[−8.37, +35.60]** | **4,326** | **38.3** | **5.5** | UNDERPOWERED |
| S3 abnormal activity | 1440 | 785 | +13.86 | 14.58 | 0.95 | 56.05 | [−29.92, +57.63] | 17,123 | 151.9 | 21.8 | UNDERPOWERED |

Effects, standard errors and intervals in basis points. `n@12bp` is the
event count needed to detect an effect exactly at the round trip; `years`
converts it at the situation's own measured rate (113–169 events/year
over the window's 6.962 years); `instruments` expresses the same count as
independent series of this window's length.

**`EXCLUDED` is a claim about a *tradeable* effect and about nothing
else.** S1 at h=15 has an interval of [−2.07, +5.28]bp, which excludes
12bp and is perfectly consistent with a real +3bp effect that no one can
trade. The verdict answers *"could this pay for its round trip?"*, not
*"is this zero?"*

## 4. The structural finding: the horizon where the question stops being askable

| h | mean se | mean detectable effect | vs the 12bp floor | excluded |
|---|---|---|---|---|
| 15 | 1.56 bp | **5.98 bp** | resolution is **below** the floor | 3 of 3 |
| 60 | 2.90 bp | **11.14 bp** | roughly **at** the floor | 2 of 3 |
| 240 | 5.26 bp | **20.20 bp** | resolution is **above** the floor | 0 of 3 |
| 1440 | 11.41 bp | **43.86 bp** | far above | 0 of 3 |

> **The cost floor is a constant and the measurement's noise is not.**
> `se` grows roughly as `√h` (measured exponent 0.44 across the family
> mean), so there is a horizon above which a 12bp effect sits inside the
> error bars no matter how many events are collected in 6.96 years — and
> on this window it falls **between h = 60 and h = 240.**

Two consequences, and the second is the uncomfortable one:

1. **Everything rd-k reported at h = 240 and h = 1440 was
   inconclusive-by-construction**, before any null was chosen, before the
   seam bug, before the volatility confound. Three nulls were built and
   debugged to resolve cells whose resolution was never adequate to the
   question. That is not wasted work — the nulls are correct and the
   volatility finding is real — but the ordering was wrong, and the
   cheapest calculation in the arc came last.
2. **S3 at h = 240 is not "nearly significant."** Its own detectable
   effect is **28.15bp** against an observed **13.61bp**: the study would
   have reached the bar only if the true effect were more than **twice**
   what was measured. A p of 0.064 there is not a near miss to be pushed
   over the line with more permutations or a fourth null. It is a study
   that was never powered for the effect it found.

**This is rd-a's finding arriving at a second instrument.** rd-a measured
a 30-day fold Sharpe's standard error at 3.49 and concluded the
*instrument* could not see a real edge. The event study was built to
replace it and does so at short horizons — 5.98bp resolution against a
12bp floor is a genuinely capable measurement, and the reason three of
three h=15 cells are settled. It inherits the same problem at long ones.

## 5. What would settle the other seven, and what it costs

| route | S3 h=240 | S3 h=1440 | comment |
|---|---|---|---|
| **more BTC time** | 38.3 more years | 151.9 more years | not a route |
| **more instruments** | 5.5 independent series | 21.8 | the only arithmetically available one |
| **a lower cost floor** | — | — | not available; 12bp is already the measured BTC round trip (`scalp-s9`) |
| **a longer horizon** | — | — | moves the wrong way; se grows and the floor does not |

**Only the cross-section is reachable**, and the word carrying the weight
is *independent*. Crypto majors move together — this project has measured
0.999955 daily log-return correlation between two venues for the same
asset, a different pair but the same warning — so 5.5 correlated series
buy materially less than 5.5 independent ones. The nominal count is an
upper bound on what a universe delivers, never a forecast.

This is the first quantitative argument this project has produced for
[`rd-d`](rd-d-discovery-mode-and-the-full-universe.md)'s full-universe
scan, and it is worth being precise about what it does and does not
support. It says **a single-instrument event study cannot settle the open
half of stage 2, and a cross-section is the only route that can.** It
does **not** say a KRX universe would settle it: those are different
instruments, different mechanisms, different costs, and rd-d §2.2's
survivorship problem is still open and still unsolved.

## 6. The Korean reading, and why it moves the opposite way to intuition

Same twelve tests, same data, `--cost-floor 0.0033` ([`rd-f`](rd-f-korean-cost-structure.md)):

| cost floor | EXCLUDED | UNDERPOWERED |
|---|---|---|
| 12bp (BTC, `scalp-s9`) | 5 | 7 |
| 33bp (KRX, rd-f) | **8** | 4 |

> **A harsher cost floor makes the question *cheaper* to settle, not
> harder.** Ruling out a 33bp effect takes `(12/33)² = 0.13×` the events
> of ruling out a 12bp one, because a bigger effect is easier to see. Only
> the four h=1440 and S3 h=240 cells stay open at the Korean floor.

The trap in reading that as good news, stated because it is easy to
misread: **this is BTC data.** It says a BTC effect of Korean-tradeable
size is excluded for 8 of 12 — not that a Korean situation is excluded.
What transfers is the *method*, not the verdict: when costs are high, the
honest question is "can I rule out an effect big enough to matter?", and
that is a question a modest sample can often answer.

## 7. What this does and does not establish

**Does not establish that any situation has no edge**, for the five
EXCLUDED cells or the seven open ones. The five exclude an effect *at or
above the round trip* at the family's own confidence level. A real,
sub-cost effect is entirely consistent with every row in §3.

**Does not establish that 4,326 events would settle S3 at h=240.** The
`1/se²` scaling holds only if the added events carry the same dispersion
and stratum composition. rd-k §2.2 already showed S1 and S2 are clustered
in 2021–22 and thin in 2025–26 — a longer window does not deliver more of
the same thing, and a wider universe delivers something else again.

**Does not produce a candidate.** No entry, exit, size or branch rule.

**Does not retire the situations.** A situation with no *mean* shift can
still have a tradeable branch structure — rd-b's own point about 개미털기,
where the mean is uninformative precisely because it mixes two opposite
outcomes. That is stage 3, and this document is an argument for going
there rather than for running a fourth null: **a fourth null would be a
fourth attempt to resolve cells whose resolution is the problem.**

## 8. What follows

1. **Stage 3, as rd-k §6 already said**, and this strengthens the case
   rather than changing it. A separator test asks a different question of
   the same events, and §4 shows the mean test has nothing left to give at
   the horizons where the mean looked interesting.
2. **Run this calculation *before* the next family, not after.** The
   transferable rule: **a family whose detectable effect exceeds its own
   cost floor cannot produce a candidate, and should be re-specified or
   not run.** That is checkable from `null_sd` and a round trip alone —
   no result required, and therefore no result to be tempted by.
3. **The cross-section argument is now quantitative**, and belongs in the
   rd-d discussion rather than being re-derived there.
