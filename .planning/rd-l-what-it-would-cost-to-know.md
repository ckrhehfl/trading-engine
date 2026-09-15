# Research Direction Task L — what it would cost to know: 3 of 12 are already answered, and the instrument was under-dispersed

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
> **3 of the 12 are settled**: their confidence interval lies entirely
> inside ±12bp, so the data are inconsistent with an effect large enough
> to pay for its own round trip. For trading purposes that is **shown
> absent**, which is strictly more than "not shown."
>
> **9 of the 12 were never answerable on this window**, and the arithmetic
> says so without reference to any result: at h = 240 the smallest effect
> this study could have detected is **21–32bp**, against a **12bp** cost
> floor. A tradeable effect was **below the instrument's resolution
> before the first event was counted.**
>
> **And a third finding, which arrived late and corrects the other two**:
> rd-k's matched null is **1.05× to 2.45× narrower than the statistic it
> judges**, so every stage-2 permutation p-value is too small. §4.1.

> **Corrected 2026-09-15, before this document merged.** The first version
> read *"5 of 12 … the other 7 need decades"* and used the **null's**
> spread as the standard error. That was wrong, by up to 2.45× in `se` and
> so up to 6× in every event count. The correction is recorded here rather
> than applied silently, and what found it was **rd-m's registered
> prediction 4** — see §4.1.

## 2. The instrument, in one line

The question is about the sampling variability of **the statistic that
was observed** — the event-arm mean — so the standard error is the event
arm's own, `σ_event / √n`:

```
n_required = n_observed × ( (z[1−α/2] + z[power]) × se / effect )²
```

**The first version of this document used `null_sd` there instead**, on
the reasoning that a permutation null's spread *is* the sampling
distribution of its statistic. That is true only of a null calibrated to
the arm it judges, and rd-k's is not. §4.1.

with **α = 0.002685**, the Benjamini–Yekutieli rank-1 threshold, because
that is the bar rd-j's decision rule actually sets. A nominal α = 0.05
would need **0.53×** these counts, so quoting one would understate the
bill by nearly half.

**The normal approximation is reported, not assumed** — and reporting it
is what made §4.1 visible. `normal_p` uses the corrected `se`; the
permutation p comes from the null's own spread. They agreed to within
8.3% while both used `null_sd`, and once the `se` was corrected they
diverged sharply — S1 at h=60 reads **6.40e-02** by permutation against
**2.62e-01** normal. **That gap is not a failure of the approximation; it
is the mis-calibration, measured.**

## 3. The twelve, priced

α = 0.002685, power 80%, cost floor 12bp, `matched` null.

| situation | h | events | effect | se | null sd | cal | 99.73% CI | n@12bp | years | instruments | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 support penetration | 15 | 1,175 | +1.60 | 2.94 | 1.22 | 0.42 | [−7.23, +10.43] | 1,043 | 6.2 | 0.9 | **EXCLUDED** |
| S1 support penetration | 60 | 1,175 | +4.43 | 3.82 | 2.38 | 0.62 | [−7.04, +15.89] | 1,759 | 10.4 | 1.5 | UNDERPOWERED |
| S1 support penetration | 240 | 1,175 | +7.46 | 5.47 | 4.28 | 0.78 | [−8.97, +23.89] | 3,611 | 21.4 | 3.1 | UNDERPOWERED |
| S1 support penetration | 1440 | 1,175 | +3.40 | 13.98 | 9.93 | 0.71 | [−38.57, +45.36] | 23,561 | 139.6 | 20.1 | UNDERPOWERED |
| S2 resistance break | 15 | 1,165 | −0.50 | 2.43 | 1.11 | 0.46 | [−7.79, +6.79] | 704 | 4.2 | 0.6 | **EXCLUDED** |
| S2 resistance break | 60 | 1,165 | −0.30 | 3.65 | 2.15 | 0.59 | [−11.27, +10.67] | 1,596 | 9.5 | 1.4 | **EXCLUDED** |
| S2 resistance break | 240 | 1,165 | +1.53 | 5.41 | 4.16 | 0.77 | [−14.69, +17.76] | 3,492 | 20.9 | 3.0 | UNDERPOWERED |
| S2 resistance break | 1440 | 1,164 | +7.92 | 13.89 | 9.73 | 0.70 | [−33.78, +49.63] | 23,046 | 137.8 | 19.8 | UNDERPOWERED |
| S3 abnormal activity | 15 | 786 | +4.18 | 3.61 | 2.34 | 0.65 | [−6.66, +15.02] | 1,052 | 9.3 | 1.3 | UNDERPOWERED |
| S3 abnormal activity | 60 | 786 | +5.51 | 5.53 | 4.16 | 0.75 | [−11.10, +22.11] | 2,466 | 21.8 | 3.1 | UNDERPOWERED |
| **S3 abnormal activity** | **240** | **786** | **+13.61** | **8.35** | **7.33** | **0.88** | **[−11.45, +38.67]** | **5,619** | **49.8** | **7.1** | UNDERPOWERED |
| S3 abnormal activity | 1440 | 785 | +13.86 | 18.21 | 14.58 | 0.80 | [−40.81, +68.52] | 26,707 | 236.9 | 34.0 | UNDERPOWERED |

Effects, standard errors and intervals in basis points. `se` is the event
arm's own standard error — `σ_event/√n` where the forward windows are
disjoint, and a **moving-block bootstrap** at h=1440 where up to six
consecutive events share one (§4.2); `cal` is `null sd / se`, where 1.0 is a correctly
calibrated null. `n@12bp` is the event count needed to detect an effect
exactly at the round trip; `years` converts it at the situation's own
measured rate (113–169 events/year over the window's 6.962 years);
`instruments` expresses the same count as independent series of this
window's length.

**`EXCLUDED` is a claim about a *tradeable* effect and about nothing
else.** S1 at h=15 has an interval of [−7.23, +10.43]bp, which excludes
12bp and is perfectly consistent with a real +3bp effect that no one can
trade. The verdict answers *"could this pay for its round trip?"*, not
*"is this zero?"*

## 4. The structural finding: the horizon where the question stops being askable

| h | mean se | mean detectable effect | vs the 12bp floor | mean `cal` | excluded |
|---|---|---|---|---|---|
| 15 | 2.99 bp | **11.51 bp** | resolution is roughly **at** the floor | 0.51 | 2 of 3 |
| 60 | 4.34 bp | **16.66 bp** | **above** the floor | 0.66 | 1 of 3 |
| 240 | 6.41 bp | **24.63 bp** | well above | 0.81 | 0 of 3 |
| 1440 | 15.36 bp | **59.04 bp** | far above | 0.74 | 0 of 3 |

> **The cost floor is a constant and the measurement's noise is not.**
> `se` grows as `h^0.36` across the family mean, so there is a horizon
> above which a 12bp effect sits inside the error bars no matter how many
> events are collected in 6.96 years — and on the corrected figures it
> falls **at h = 15 to 60**, one step shorter than the first version of
> this document put it.

Two consequences, and the second is the uncomfortable one:

1. **Everything rd-k reported at h = 240 and h = 1440 was
   inconclusive-by-construction**, before any null was chosen, before the
   seam bug, before the volatility confound. Three nulls were built and
   debugged to resolve cells whose resolution was never adequate to the
   question. That is not wasted work — the nulls are correct and the
   volatility finding is real — but the ordering was wrong, and the
   cheapest calculation in the arc came last.
2. **S3 at h = 240 is not "nearly significant."** Its own detectable
   effect is **32.09bp** against an observed **13.61bp**: the study would
   have reached the bar only if the true effect were nearly **two and a
   half times** what was measured. A p of 0.064 there is not a near miss
   to be pushed over the line with more permutations or a fourth null. It
   is a study that was never powered for the effect it found.

**This is rd-a's finding arriving at a second instrument.** rd-a measured
a 30-day fold Sharpe's standard error at 3.49 and concluded the
*instrument* could not see a real edge. The event study was built to
replace it and does so only at the shortest horizon — 11.6bp resolution
against a 12bp floor is a measurement that barely clears its own question,
and it is the reason two of three h=15 cells are settled and nothing
beyond h=60 is. It inherits the same problem at long ones.

### 4.1 The instrument had a second fault, and it was in the null

**Found 2026-09-15 while running rd-m's stage-3 family, by the
registered prediction that was supposed to be a formality.** rd-m §7
prediction 4 said *"the realised `se_diff` will be within 20% of 2 ×
stage 2's `se_full`"*, as a check on this document's own power
projection. It came back at **2.5×**.

The cause is specific and it is not a coding error:

> **rd-k's matched null matches on _prior_ volatility, and the events are
> _forward_ volatility bursts.** A bar drawn from the same prior-60-bar
> volatility decile as a support penetration does not have that
> penetration's forward dispersion, so the control arm is systematically
> calmer than the event arm.

Measured directly — per-event forward-return σ against the matched null's
own spread:

| h | mean `cal` = null sd / se | event σ vs an unconditional bar's |
|---|---|---|
| 15 | **0.51** | 2.5–3.1× |
| 60 | 0.66 | 1.9–2.4× |
| 240 | 0.81 | 1.4–1.8× |
| 1440 | 0.74 | 1.2–1.4× |

The ratio recovers toward 1.0 through h=240, which is exactly the
mechanism showing its shape: the volatility burst decays, so the matched
control gets steadily closer to right. It falls back at h=1440 for a
**second, unrelated** reason — §4.2's overlap correction widens the `se`
there and so lowers the ratio.

**Three consequences, in the order they matter:**

1. **Every stage-2 permutation p-value is too small.** A reference
   distribution narrower than the statistic it judges makes an observed
   deviation look more extreme than it is.

   **The size of that is not recomputed here, and the table's `norm p`
   column must not be read as if it were.** Nothing recalibrates the
   matched-null permutation distribution; `norm p` is a *normal
   comparison*, `2Φ(−|effect| / se)`, computed from the corrected standard
   error. For rd-k's two leading cells it reads **0.10** (S3 h=240) and
   **0.26** (S1 h=60) against permutation values of 0.064 — an indication
   of the direction and rough scale of the bias, **not** a corrected
   p-value, and the permutation p of record remains 0.064.

   **The direction is what matters and it is safe**: rd-k's 0-of-12 is
   *more* comfortable than reported, not less. It would not have been safe
   for anything that had advanced, which is why the flag now prints.
2. **This document's first version understated every cost.** `n` scales
   as `se²`, so the counts were low by up to **6×** and the intervals
   narrow by up to **2.4×**. "5 of 12 EXCLUDED" was really **3 of 12**;
   S3 h=240's 38.3 total years is really **49.8**.
3. **`null_sd` is now a diagnostic rather than an input.**
   `null_calibration = null_sd / se` is reported per test, and a value
   below 0.9 prints a warning naming the direction of the resulting bias.
   The quantity that was silently wrong is now the one on display.

**What it does not change**: rd-k's verdict (0 of 12, and now by a wider
margin), rd-h's numbers, or any event count, effect or p-value in the
stage-2 modules — `event_sd` was added as a reported field only, and both
stage-2 commands produce byte-identical output before and after.

**The transferable rule**, and it is the fourth of its kind in this arc:
**a permutation null's spread is the standard error of its own statistic,
not of the arm it is being compared against.** They coincide only when the
null is calibrated to that arm, and "matched on a prior-window statistic"
does not calibrate a null to an event that is defined by a burst in that
same statistic. Report the ratio; do not assume it.

### 4.2 Two further corrections, both found on review of this document

**(a) `σ_event / √n` is an _independent-samples_ standard error, and at
h=1440 the samples are not independent.** Episodes are collapsed to one
per 240 bars, so a forward window of `h` bars is shared by up to
`ceil(h/240)` consecutive events — **1 at h=15, 60 and 240, and 6 at
h=1440.** Ignoring that covariance understates the error, and therefore
understates the cost, which is the unsafe direction.

The `se` is now a **moving-block bootstrap** over the event sequence
wherever the block exceeds 1, with the block length derived from the two
constants rather than chosen. The correction is exactly where the
arithmetic says it should be:

| h | block | closed form | bootstrap | ratio |
|---|---|---|---|---|
| 15 / 60 / 240 | 1 | — | identical | **1.00** |
| 1440 (S1) | 6 | 11.30 | 13.98 | 1.24 |
| 1440 (S2) | 6 | 11.05 | 13.89 | 1.26 |
| 1440 (S3) | 6 | 15.24 | 18.21 | 1.19 |

No verdict moved — the h=1440 cells were UNDERPOWERED either way — but
their price did: S3 h=1440 goes from 151.9 to **236.9 total years**. It
does not claim to capture dependence *beyond* the shared window;
volatility clustering correlates even disjoint neighbours, and that
remains uncorrected and disclosed.

**(b) The dispersion was taken over the wrong events under the matched
null.** `matched_null` drops an event whose `(decile, hour)` stratum has
no eligible bar, and `observed` is computed without it — but `event_sd`
was computed over the pre-drop set, i.e. a different population from the
statistic it is the error of. 21 events for S1 at h=15, which moved that
cell's `se` from 3.02 to **2.94**. `matched_null` now returns the kept
indices rather than a count, so there is one source for "the events of
this test."

**And a third thing, caught by the verification rather than by review.**
The bootstrap first drew from the *same* generator the nulls use, which
silently re-rolled every shift offset and matched control after its first
call — moving S3 h=1440 from 33.07bp/p=1.25e-02 to 32.13bp/p=6.50e-03 and
**turning a non-result into an ADVANCE.** A reported diagnostic must not
perturb the stream the result depends on; it now has its own. What found
it was diffing both stage-2 commands against their pre-change output, not
reading the code — the project's own "verify against an external
observable" rule, doing exactly what it is for.

## 5. What would settle the other nine, and what it costs

| route | S3 h=240 | S3 h=1440 | comment |
|---|---|---|---|
| **more BTC time** | **49.8 total years** (42.8 more) | **236.9 total** (229.9 more) | not a route |
| **more instruments** | 7.1 independent series | 34.0 | the only arithmetically available one |
| **a lower cost floor** | — | — | not available; 12bp is already the measured BTC round trip (`scalp-s9`) |
| **a longer horizon** | — | — | moves the wrong way; se grows and the floor does not |

**Only the cross-section is reachable**, and the word carrying the weight
is *independent*. Crypto majors move together — this project has measured
0.999955 daily log-return correlation between two venues for the same
asset, a different pair but the same warning — so 7.1 correlated series
buy materially less than 7.1 independent ones. The nominal count is an
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
| 12bp (BTC, `scalp-s9`) | 3 | 9 |
| 33bp (KRX, rd-f) | **8** | 4 |

> **A harsher cost floor makes the question *cheaper* to settle, not
> harder.** Ruling out a 33bp effect takes `(12/33)² = 0.13×` the events
> of ruling out a 12bp one, because a bigger effect is easier to see. Only
> the **three** h=1440 cells and S3 h=240 stay open at the Korean floor —
> four in total, and the count of h=1440 cells is three because there are
> three situations.

The trap in reading that as good news, stated because it is easy to
misread: **this is BTC data.** It says a BTC effect of Korean-tradeable
size is excluded for 8 of 12 — not that a Korean situation is excluded.
What transfers is the *method*, not the verdict: when costs are high, the
honest question is "can I rule out an effect big enough to matter?", and
that is a question a modest sample can often answer.

## 7. What this does and does not establish

**Does not establish that any situation has no edge**, for the three
EXCLUDED cells or the nine open ones. The three exclude an effect *at or
above the round trip* at the family's own confidence level. A real,
sub-cost effect is entirely consistent with every row in §3.

**Does not establish that 5,619 events would settle S3 at h=240.** That
is a *total*, of which the 786 already collected are part. The
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
   transferable rule, stated as two separate claims because they are two
   different kinds of thing:

   - **The statistical fact**: a family whose `detectable` exceeds its own
     cost floor **cannot reliably detect a cost-floor-sized effect at the
     chosen power.** It says nothing about whether a larger effect exists
     — a study too coarse to see 12bp would see 40bp perfectly well.
   - **The policy**: such a family should be **re-specified or not run**,
     and that is a pre-registration decision made before any data is seen,
     not a conclusion drawn from one. Its justification is that a family
     which cannot resolve the smallest *useful* effect can only ever return
     "not shown", at the cost of spending `N` and inviting a
     winner's-curse reading of whatever it does return.

   Both are checkable from the event arm's own dispersion and a round trip
   alone — no result required, and therefore no result to be tempted by.
   **Not from `null_sd`**, which §4.1 is about.
3. **The cross-section argument is now quantitative**, and belongs in the
   rd-d discussion rather than being re-derived there.
4. **Report `null_calibration` on every permutation test from here on.**
   §4.1's fault was invisible for three nulls and two result documents,
   and one printed ratio would have caught it at rd-h. It costs nothing to
   compute and it is now a column.
