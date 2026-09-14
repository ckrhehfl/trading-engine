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

> **Zero of twelve survive.** One test cleared both gates as specified,
> and the placebo diagnostic that rd-g §6 **mandated in advance** shows it
> was an artifact of how the controls were built.

That mandate is the reason this document is not reporting a discovery.
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

## 4. What this establishes, and what it does not

**Establishes:**

- **Nothing about an edge, in either direction.** Eleven tests were
  never significant and the twelfth was an artifact. That is *not*
  evidence the situations are worthless — it is a failure to detect,
  under one specific placebo construction that has now been shown to be
  faulty.
- **That a directional exclusion rule biases a matched control.** This is
  a general result about the method, not about these three situations, and
  it applies to any future event study here.
- **That the pre-registration did its job.** The suspicion was registered
  before the number existed; without it, `t = 2.76, p = 0.0059,
  +15.55bp` on a rare, cost-feasible, mechanically-motivated situation is
  exactly the result that becomes a headline.

**Does not establish:**

- **That the symmetric exclusion is correct.** It is *less* obviously
  wrong — it removes the asymmetry and returns S2's control to near the
  unconditional mean — but "exclude every catalogued situation" is its own
  arbitrary choice, and it makes the control pool conditional on quietness.
  A third construction (no exclusion at all, accepting that ~15% of
  candidate bars sit near an event) has not been tested.
- **Any result under the symmetric rule.** The +0.96bp / t=0.17 figure
  above is a **diagnostic**, run to explain the artifact, and is not a
  stage-2 result. rd-g §4 forbids re-running the twelve with adjusted
  definitions and calling the output a result; a corrected family is a new
  specification document.

## 5. What follows

1. **A corrected stage-2 needs a new specification document**, fixing the
   control construction *and* justifying it, before it runs. The evidence
   in §3 is the input to that choice, not a licence to skip it.
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
