# Research Direction Task J — stage 2, corrected: a null that does not partition the series

**Committed 2026-09-15, before any forward return is measured under it.**
Same ordering discipline as [`rd-g`](rd-g-stage2-specification.md), which
this supersedes: git history is the proof, checkable against the run that
follows.

[`rd-h`](rd-h-stage2-result.md) ran rd-g's family and got **0 of 12**,
then showed the one apparently-significant cell was an artifact of the
control construction — and that **repairing a second defect made the
artifact larger**. That is why this is a new specification rather than a
re-run: rd-g §4's stopping rule forbids re-running the twelve with
adjusted definitions, and rd-h §5 requires the replacement to **replace
the instrument, not retune it**.

**Discovery mode**, on the designated discovery window (Binance futures
1m, already closed to selection). Nothing produced under this may be
promoted, quoted as evidence of an edge, or reported as a pass.

---

## 1. What was wrong, in one paragraph

rd-g drew controls from the same series by **excluding** the tested
situation's neighbourhood. For a directional situation that is a
directional condition: the complement of "near an up-move" is "a
down-move." Measured at h=1440, the surviving control pool's own forward
return was **+113bp (S1)** and **−91bp (S2)** against an unconditional
**+13bp** — and the exclusion radius that disjointness requires is
exactly what makes it worse. The two constraints pull against each other,
so no radius fixes it.

## 2. The replacement: a circular block shift

**The event arm is unchanged. Only the null changes.**

```
for each situation S and horizon h:
    observed = mean( forward_return(event_i, h) )   over the episodes of S
    for b in 1..B:
        shift the RETURN SERIES circularly by a random offset
        recompute the same statistic at the same event timestamps
    p = (1 + #{ |null_b| >= |observed| }) / (B + 1)
```

**Why this and not the alternatives**:

- **It does not partition the series by the event.** Every bar stays in
  the sample; nothing is excluded, so there is no complement to be
  biased. That is the specific failure rd-h found, removed by
  construction rather than by a better radius.
- **It preserves what a matched control was trying to preserve.** A
  circular shift keeps the return series' own autocorrelation, volatility
  clustering and unconditional drift intact — it only destroys the
  *alignment* between events and returns, which is precisely the thing
  under test.
- **It answers the dependence objection too.** Overlapping forward
  windows inflate a t-test's significance; they do not inflate a
  permutation p-value, because the null draws are built from the same
  overlapping structure. rd-g needed a disjointness repair *and* a
  correction; this needs neither.

**Shift offsets** are drawn uniformly from `[H_max, n − H_max)` so no
shift is near-identity and none wraps a forward window across the seam.
`B = 2000`, seeded `numpy.random.default_rng(20260915)`.

**The known limitation, stated rather than discovered later**: a circular
shift breaks the series at one seam, and it destroys any *time-of-day*
alignment. rd-b measured a real intraday cycle in crypto, so a situation
that fires disproportionately at one hour is compared against a null that
has smeared those hours. **A day-multiple shift** — offsets restricted to
integer multiples of 1,440 bars — preserves hour-of-day, and both are run.
Disagreement between them **is** the hour-of-day effect and is reported.

## 3. The family — 12 tests, unchanged, and that is deliberate

Same three situations (S1 support penetration, S2 resistance break, S3
abnormal activity) and same four horizons (15, 60, 240, 1440).
**`m = 12`.**

Keeping the family identical is the point: **the only thing that changes
between rd-g's result and this one is the null.** A different family
would make the comparison uninterpretable, and the comparison is most of
the value — if the answer moves, the instrument was the story.

Definitions are frozen at `research/situation_catalogue.py` and
`research/stage2_event_study.py` as of this commit, including the `>=0.3%`
boundary and the trailing-quantile thresholds.

## 4. What is measured, and the two guards that survive

`observed` is the **mean forward return over episodes**, entered at the
bar *after* the event at its open, exit `h` bars later at the open. No
costs — they are a constant on both arms of a difference, and stage 1's
cost ceiling already tested the level.

Episodes use the **base 240-bar cooldown**, not rd-g's horizon-scaled one.
The disjointness repair existed to make a t-test valid; a permutation test
does not need it, and dropping it restores the event counts rd-g's repair
had cut by up to two thirds at h=1440.

**Both guards from rd-h are kept and both still bind:**

1. **Benjamini–Yekutieli at q = 0.10**, `m = 12`, rank-1 threshold
   **0.002685**. BY rather than BH because the three situations use
   different event sets, so positive dependence across the family is
   asserted rather than shown.
2. **The effect floor**: `|observed − null mean| > 12bp`, the measured BTC
   round trip. A difference real but smaller than the cost of capturing
   it is a fact about market structure, not a candidate.

**`control_suspect` is retired here, and its replacement is stated.** It
vetoed on a control arm drifting from its baseline; there is no control
arm now. The equivalent sanity check is that **the null distribution's
mean must sit near the unconditional forward return** — if a shift-null's
centre is far from it, the shift is not doing what it claims. Reported
per test as `null_mean` beside `unconditional`, and a gap larger than the
observed effect is a **NULL-SUSPECT** veto on the same logic.

## 5. The decision rule

A test **advances to stage 3** only if all three hold:

1. BY-significant at q = 0.10 over `m = 12`;
2. `|effect| > 12bp`;
3. **not** NULL-SUSPECT.

**Every one of the 12 is reported** with its observed mean, null mean,
unconditional mean, effect, permutation p, BY rank and verdict — winners
and losers alike, per rd-g §4's rule that a sweep reporting only its
winners is not a sweep.

## 6. Predictions, recorded so they can be wrong

rd-g's registered predictions caught the artifact, so the practice earns
its place.

1. **Fewer significant cells than rd-g's naive run, not more.** The
   circular-block null is stricter than a biased control precisely
   because it is unbiased; rd-g's 15.55bp came from the control arm.
2. **S3 will move least between the two nulls.** It moved least under
   every previous diagnostic — it is the only non-directional situation —
   and if that stops being true, the shift is suspect before the finding
   is.
3. **The day-multiple and free shifts will agree within ~2bp.** Crypto's
   intraday cycle is real but small at these horizons. **A disagreement
   larger than the effect floor means the hour-of-day channel is the
   result**, which would be a finding about the situations' timing rather
   than their direction, and is reported as such rather than as an edge.
4. **The most likely outcome is 0 of 12 again**, and that is not a reason
   to weaken anything. rd-c §1's base rate and rd-e's cost ceiling both
   say a tradeable per-event effect on a 6.96-year liquid instrument is
   the exception.

**If a test does advance**, the first suspicion is the null, exactly as
rd-g §6 aimed it at the placebo — specifically the seam and the shift
range, checked before anything is written up as a discovery.

## 7. What this cannot produce

- **Not a candidate.** It answers *"does the situation shift the outcome
  distribution"*, and produces no entry, exit, size or branch rule.
- **Not a Korean result.** BTC 1m. [`rd-f`](rd-f-korean-cost-structure.md)
  shows the Korean ceiling is 2.5–2.8× harsher, so a BTC-significant
  situation may still be untradeable there.
- **Not a directional claim.** Two-sided by construction.
- **Not independence.** A permutation test handles the *dependence* in
  the null correctly; it does not make 1,196 overlapping episodes into
  1,196 independent observations, and the effective sample is smaller
  than the count.

## 8. Order of work

1. Implement the shift null in `research/stage2_event_study.py` alongside
   the existing matched-control path — **the old path stays**, so rd-h's
   numbers remain reproducible.
2. Run all 12 under both shift variants. Report all 12.
3. Write `.planning/rd-k-stage2-corrected-result.md`, whatever it says.
