# Research Direction Task H — stage 2 result: zero of twelve, and the one that looked real was the placebo

**Run 2026-09-14** against
[`rd-g-stage2-specification.md`](rd-g-stage2-specification.md), committed
at `ea494cb` **before any forward return was measured**. Module:
`python/research/stage2_event_study.py`.

**Discovery mode**, on the designated discovery window (Binance futures
1m, already closed to selection). Under CLAUDE.md's Discovery /
Confirmation split **nothing here may be promoted, quoted as evidence of
an edge, or reported as a pass.**

---

## 1. The headline

> **Zero of twelve survive, and the instrument is unsound.** One test
> cleared both gates as specified; the placebo suspicion rd-g §6
> **pre-specified** showed it was an artifact of how the controls were
> built. Repairing a *second* defect — overlapping forward windows — made
> that artifact **larger**, which is what exposed the design problem:
> **on this series, at every one of the twelve tests, a matched placebo
> built by excluding a directional situation's neighbourhood carried a
> biased control arm — and the bias grew with the exclusion radius that
> disjointness demands.**

That pre-specified suspicion is the reason this document is not reporting
a discovery. rd-g named the *suspicion*, not this particular diagnostic —
the symmetric-exclusion comparison below was chosen after the fact, and
only the direction to look was fixed in advance.
rd-g §6 registered three predictions, and the one that mattered read:

> *"The effect will be small enough to fail condition 2 … **If prediction
> 2 is wrong — if a difference exceeds 12bp — the first suspicion is the
> placebo, not a discovery.** The matching rule in §3 is where such a
> result would most likely come from, and it gets re-examined before
> anything else."*

Prediction 2 was wrong. The suspicion was correct.

## 2. All twelve, as specified

Reported in full because rd-g §4 requires it — *"a sweep that reports only
its winners is not a sweep."*

| Situation | h | events | ctrl | drop | event bp | ctrl bp | **diff bp** | t | p | BH |
|---|---|---|---|---|---|---|---|---|---|---|
| **S2 resistance break** | **240** | 1,165 | 5,825 | 5 | +3.82 | **−11.73** | **+15.55** | **2.76** | **5.9e-03** | **yes** |
| S1 support penetration | 1440 | 1,175 | 5,875 | 21 | +22.23 | +40.52 | −18.30 | −1.51 | 1.3e-01 | no |
| S2 resistance break | 60 | 1,165 | 5,825 | 5 | +0.41 | −5.11 | +5.52 | 1.46 | 1.4e-01 | no |
| S2 resistance break | 1440 | 1,164 | 5,820 | 5 | +24.62 | +7.78 | +16.84 | 1.43 | 1.5e-01 | no |
| S1 support penetration | 240 | 1,175 | 5,875 | 21 | +11.23 | +19.23 | −8.01 | −1.39 | 1.6e-01 | no |
| S3 abnormal activity | 1440 | 785 | 3,925 | 0 | +45.42 | +25.71 | +19.71 | 1.21 | 2.3e-01 | no |
| S3 abnormal activity | 15 | 786 | 3,930 | 0 | +4.78 | +1.20 | +3.58 | 0.97 | 3.3e-01 | no |
| S3 abnormal activity | 240 | 786 | 3,930 | 0 | +21.12 | +15.55 | +5.57 | 0.64 | 5.2e-01 | no |
| S2 resistance break | 15 | 1,165 | 5,825 | 5 | −0.21 | −1.66 | +1.46 | 0.59 | 5.6e-01 | no |
| S3 abnormal activity | 60 | 786 | 3,930 | 0 | +8.03 | +5.87 | +2.16 | 0.38 | 7.0e-01 | no |
| S1 support penetration | 60 | 1,175 | 5,875 | 21 | +5.51 | +5.33 | +0.19 | 0.05 | 9.6e-01 | no |
| S1 support penetration | 15 | 1,175 | 5,875 | 21 | +1.86 | +1.85 | +0.00 | 0.00 | 1.0e+00 | no |

Only the top row clears BH at `q = 0.10, m = 12` (rank-1 threshold
0.00833), and it also clears the 12bp effect floor. **As specified, that
is one ADVANCE.**

**Under Benjamini–Yekutieli it is not even that.** rd-g justified BH's
positive-dependence condition for the four horizons on one event set, but
S1, S2 and S3 use **different event sets and different control draws**, so
PRDS across all twelve was asserted rather than shown — raised on review
of PR #169. BY controls FDR under *arbitrary* dependence at a `Σ1/i`
cost, which at m = 12 is 3.1032 and drops the rank-1 threshold from
0.00833 to **0.002685**. The top row's `p = 0.00587` does not clear it.

**Both corrections give the same answer here**, which is what makes
adopting the stricter one after seeing the result safe. The module now
reports BY by default and a future specification must fix the choice in
advance regardless.

## 3. Why it is not one

**The tell was visible in the table before any diagnostic ran.** The
control means are wildly inconsistent across situations drawn from the
*same series* and the *same volatility and hour strata*: S1's controls at
h=240 average **+19.23bp** while S2's average **−11.73bp**. A 31bp spread
between two supposedly comparable baselines is not something the market
did; it is something the construction did.

The mechanism is in rd-g §3's own exclusion rule — *"a control must be
≥240 bars from any episode of **the same situation**"*:

- **S2 is an upward breakout.** Excluding its neighbourhood removes
  precisely the periods when price was rising, so what remains is biased
  **down**.
- **S1 is a downward break.** Excluding its neighbourhood removes the
  falling periods, so what remains is biased **up**.

Measured directly, against the unconditional 4-hour forward return of
**+2.17bp**:

| h = 240 | control mean, **own-situation** exclusion | control mean, **all-situations** exclusion | event mean |
|---|---|---|---|
| S1 support penetration | **+19.40** | +4.15 | +10.50 |
| S2 resistance break | **−13.01** | +2.86 | +3.94 |
| S3 abnormal activity | +16.38 | +16.28 | +21.12 |

**S3 barely moves** (+16.38 → +16.28) because abnormal volume is not a
directional condition — which is exactly the discriminating prediction.
S1 and S2 move by 15bp and 16bp in *opposite* directions.

Re-testing the surviving cell under the symmetric exclusion, changing
nothing else:

| S2, h = 240 | as specified | symmetric exclusion |
|---|---|---|
| event | +3.82bp | +3.82bp |
| **control** | **−11.73bp** | **+2.86bp** |
| **difference** | **+15.55bp** | **+0.96bp** |
| **t** | **2.76** | **0.17** |
| **p** | **0.0059** | **0.863** |

The event arm is **identical**. Every part of the "effect" came from the
control arm. **Zero of twelve survive.**

## 3.5 A second defect, in the specification itself

**rd-g fixed an episode cooldown of 240 bars and horizons up to 1,440,
then applied a two-sample t-test.** Those are mutually inconsistent: a
t-test is a statement about *independent* observations, and two events
300 bars apart share 1,140 bars of a 1,440-bar forward window.

Found on CodeRabbit review of PR #169, measured on the real series:

| horizon | overlapping event pairs | of |
|---|---|---|
| H1 (15) | **0** | — |
| H2 (60) | **0** | — |
| H3 (240) | 1 – 37 | 786 – 1,196 |
| **H4 (1,440)** | **308 – 531** | **785 – 1,196 (39–44%)** |

**CLAUDE.md already records this exact error shape** — S13's `t = 7–8`
was really 1.5–2.6 once overlap was removed — and already records the
lesson: *"building the right tool does not protect you if the next
analysis does not use it."* `research.conclusion_check
.check_disjoint_intervals` is that tool, and it was not applied.

**Two things limit the damage, and neither excuses it.** Overlap inflates
significance, so the as-specified run erred toward **false positives**,
which cannot manufacture the "zero of twelve" conclusion. And the one
apparently-significant cell was **H3 with a single overlapping pair out of
1,170** — so overlap is not what produced it; §3's placebo is.

### The corrected run, and what fixing it exposed

Re-run with forward windows forced disjoint (episode spacing
`max(240, h+1)`, drawn controls held that far apart, **and the control
pool itself excluding that radius** — an event and a control 300 bars
apart otherwise have overlapping 1,440-bar windows), plus the other
review fixes: a fail-closed continuity check on gap **position and size**,
the `≥0.3%` boundary the specification actually specified, thin-then-draw
control sampling instead of draw-then-thin, fractional Welch df, and
Benjamini–Yekutieli in place of BH.

**Fixing the overlap made the artifact dramatically worse, and that is
the real finding.**

| Situation | h | events | **pool %** | event bp | ctrl bp | baseline | **ctrl bias** | diff bp | t | p |
|---|---|---|---|---|---|---|---|---|---|---|
| S2 resistance break | 1440 | 200 | **42%** | +57.45 | **−73.05** | +14.71 | **−87.77** | **+130.50** | **3.51** | **5.4e-04** |
| S2 resistance break | 240 | 1,162 | 84% | +3.57 | −8.56 | +2.27 | −10.83 | +12.13 | 2.16 | 3.1e-02 |
| S1 support penetration | 1440 | 204 | 43% | +40.34 | **+94.61** | +17.43 | **+77.18** | −54.27 | −1.92 | 5.6e-02 |
| S3 abnormal activity | 15 | 497 | 89% | +6.56 | +1.17 | +0.57 | +0.60 | +5.39 | 1.03 | 3.0e-01 |
| S3 abnormal activity | 1440 | 151 | 58% | +1.92 | +41.60 | +31.51 | +10.10 | −39.68 | −1.00 | 3.2e-01 |
| S1 support penetration | 240 | 1,149 | 84% | +9.42 | +15.06 | +3.48 | +11.59 | −5.65 | −0.99 | 3.2e-01 |
| S2 resistance break | 60 | 1,163 | 84% | +0.07 | −3.60 | +0.74 | −4.34 | +3.67 | 0.98 | 3.3e-01 |
| S3 abnormal activity | 240 | 497 | 89% | +21.74 | +10.78 | +7.34 | +3.43 | +10.96 | 0.92 | 3.6e-01 |
| S3 abnormal activity | 60 | 500 | 89% | +11.95 | +7.10 | +2.37 | +4.73 | +4.86 | 0.62 | 5.4e-01 |
| S2 resistance break | 15 | 1,163 | 84% | −0.28 | −1.78 | +0.25 | −2.03 | +1.49 | 0.60 | 5.5e-01 |
| S1 support penetration | 60 | 1,150 | 84% | +4.70 | +6.64 | +0.97 | +5.67 | −1.94 | −0.49 | 6.2e-01 |
| S1 support penetration | 15 | 1,152 | 84% | +1.56 | +1.66 | +0.22 | +1.44 | −0.10 | −0.03 | 9.7e-01 |

**`baseline` is strata-weighted, and that matters.** `match_controls`
draws inside each event's `(volatility decile, hour)` stratum, so
comparing the control to the *whole-series* mean would charge the events'
own stratum composition to the control construction. Raised on review of
PR #169; the baseline is now the series mean reweighted to the kept
events' strata.

**It changed one row substantially, and that row is the discriminator
again.** S3's apparent bias at h=1440 falls from **+28.61 to +10.10** —
most of it *was* stratum composition. S1's and S2's barely move
(+81.62 → +77.18, −86.05 → **−87.77**), because theirs is the exclusion
rule, not the strata. **The non-directional situation is the one whose
bias dissolves under a fair baseline.**

**0 of 12 advance. One is significant and vetoed.** S2 at h=1440 posts
`+130.50bp, t = 3.51, p = 5.4e-04` — clearing even Benjamini–Yekutieli's
0.002685 threshold — on a control arm sitting **87.8bp below its own strata-matched
baseline.** The claimed effect is smaller than the control's own
displacement.

### The design is unsound, not the parameters

The tension is structural and the numbers make it exact. Disjoint windows
at H4 need a ±1,441-bar exclusion; applied around a **directional**
situation, that is most of the series, and what survives is the opposite
of the situation:

| h = 1440, control pool | share of series | its own forward return |
|---|---|---|
| S1 support penetration (down-break) | 43.0% | **+113.38bp** |
| S2 resistance break (up-break) | 43.2% | **−91.24bp** |
| S3 abnormal activity (non-directional) | 59.1% | +20.15bp |
| *unconditional* | 100% | **+13.00bp** |

Excluding a two-day neighbourhood around every new 1-day high leaves
mostly sustained downtrends, and vice versa. **S3 moves least because it
is the only non-directional situation** — the same discriminator as §3.

And the `ctrl bias` column shows it is not confined to H4: **the control
arm deviates from the unconditional baseline in all twelve tests**, and in
most of them by more than the difference being measured.

> **The complement of "near an up-move" is "a down-move."** That is the
> mechanism, and it was observed in **all twelve tests here** — the bias
> growing with the exclusion radius disjointness requires, so the two
> constraints pull against each other.

**Stated at the scope the evidence covers**, rather than as a theorem:
this is twelve tests on one instrument over one 6.96-year window. It is
not a proof that every exclusion-based control is biased under every
data-generating process — a series with no persistent drift, or a
situation whose neighbourhood is uncorrelated with forward returns, need
not show it. What it is: **enough to stop using this instrument here**,
and enough to make the baseline check mandatory anywhere it is used.

**So the instrument needs replacing, not retuning.** A control drawn from
the same series by *excluding* the event partitions the series by the
event itself. A null that does not — a block bootstrap preserving
autocorrelation, or a circular block shift preserving unconditional
drift — does not have this failure mode. That is what the corrected
specification must adopt.

### The veto, now in the code

`EventTest.control_suspect` refuses to advance any test whose control arm
sits further from the unconditional baseline than half the difference
claimed, and the report prints `uncond` and `ctrl bias` for every test.
**This is §6's rule made executable**: the failure was invisible in the
p-value, the effect size, the sample size and the multiple-testing
correction, and visible immediately in the baselines.

## 4. What this establishes, and what it does not

**Establishes:**

- **Nothing about an edge, in either direction.** Eleven tests were
  never significant and the twelfth was an artifact. That is *not*
  evidence the situations are worthless — it is a failure to detect,
  under one specific placebo construction that has now been shown to be
  faulty.
- **That a directional exclusion rule biased the control in all twelve
  tests here.** The mechanism is general; the *demonstration* is one
  instrument, one window, three situations. Enough to abandon the
  instrument and to make the baseline check mandatory — not a theorem.
- **That the pre-specified suspicion did its job.** It was registered
  before the number existed; without it, `t = 2.76, p = 0.0059,
  +15.55bp` on a rare, cost-feasible, mechanically-motivated situation is
  exactly the result that becomes a headline.

**Does not establish:**

- **That the symmetric exclusion is the fix.** It removes the *asymmetry*
  between S1 and S2 at h=240, and §3.5 shows it does not remove the
  underlying problem: any exclusion-based control is partitioned by the
  event, and the bias grows with the radius that disjointness demands.
  Exclusion is the wrong family of instrument, not the wrong parameter.
- **Any result under the symmetric rule.** The +0.96bp / t=0.17 figure
  above is a **diagnostic**, run to explain the artifact, and is not a
  stage-2 result. rd-g §4 forbids re-running the twelve with adjusted
  definitions and calling the output a result; a corrected family is a new
  specification document.

## 5. What follows

1. **A corrected stage-2 needs a new specification document**, fixing the
   control construction *and* justifying it, before it runs. The evidence
   in §3 is the input to that choice, not a licence to skip it. **It must
   replace the instrument rather than retune it** — §3.5 shows exclusion
   partitions the series by the event, and that disjointness (which the
   t-test requires) makes it worse. A **block bootstrap** preserving
   autocorrelation, or a **circular block shift** preserving unconditional
   drift, has no such failure mode and also answers the dependence
   objection: episodes that merely do not overlap are still not
   independent, which is why `check_disjoint_intervals` reports clustering
   as a warning.
2. **The three situations are not refuted.** rd-b §4 stage 3 asks a
   different question — *what, observable at decision time, separates the
   branches?* — and a situation with no mean shift can still have a
   tradeable branch structure. That is rd-b's own point about 개미털기:
   the mean of a level break is uninformative precisely because it mixes
   two opposite outcomes.
3. **Do not re-run these twelve hoping for a different placebo.** rd-g's
   stopping rule is explicit and it binds here.

## 6. The methodological result, which outlives the numbers

**A matched placebo can manufacture a significant, correctly-signed,
cost-clearing effect out of nothing**, and the failure is invisible in the
headline statistic. What exposed it was not the p-value, the effect size,
the sample size, or the multiple-testing correction — all four looked
fine. It was **one control arm disagreeing with another control arm that
should have matched it**.

So the check belongs in the method, not in this document:

> **Report every arm's baseline, and compare baselines across tests that
> should share one. A control mean that differs between situations drawn
> from the same series is a construction fault until shown otherwise.**
