# Research Direction Task M — stage 3: what splits the branches, and why this instrument is better than stage 2's

**Committed 2026-09-15, before any separator's forward return is
measured.** Same ordering discipline as [`rd-g`](rd-g-stage2-specification.md)
and [`rd-j`](rd-j-stage2-corrected-specification.md): git history is the
proof, checkable against the run that follows.

**Discovery mode**, on the designated discovery window (Binance futures
1m, already closed to selection). Nothing produced under this may be
promoted, quoted as evidence of an edge, or reported as a pass.

---

## 1. Why stage 3 at all, when stage 2 returned 0 of 12

Two reasons, and the second is new as of [`rd-l`](rd-l-what-it-would-cost-to-know.md).

**The mean is the wrong statistic for the mechanism.** rd-b's 개미털기
story says a support penetration resolves into one of two opposite
outcomes — a shakeout that recovers, or a real breakdown that continues —
and *the mean is uninformative precisely because it mixes them*. A
situation with a mean of zero and two large opposite branches looks
identical, under a mean test, to a situation with nothing in it.

**And the cells where a separator can be tested are exactly the cells
where stage 2 excluded a tradeable mean.** rd-l found that the five
EXCLUDED cells are the short horizons, and those are the same cells whose
resolution can support a half-sample comparison. That looks like a
contradiction and is not:

> **An excluded mean is not an obstacle to stage 3. It is stage 3's own
> hypothesis.** 개미털기 predicts a mean near zero *because* two branches
> cancel. Testing a separator where the mean is excluded is the designed
> case, not a consolation.

The relationship is structural rather than lucky — "the interval lies
inside ±12bp" and "the study could resolve 12bp" are close to the same
condition — and it is stated here so a reader does not later mistake it
for a coincidence worth investigating.

## 2. The instrument, and why it is better than anything stage 2 had

```
for each situation S, horizon h, separator X:
    label each event of S by X(event) >= median_X(S)        # high / low
    difference = mean(forward | high) - mean(forward | low)
    for b in 1..B:
        PERMUTE THE LABELS among the same events
        recompute the same difference
    p = (1 + #{ |null_b| >= |difference| }) / (B + 1)
```

**The events are held fixed and only the labels move.** That single
property removes the entire class of problems that took this arc three
nulls to handle:

| stage 2's problem | how it arose | under a label permutation |
|---|---|---|
| directional exclusion bias (rd-h) | the control pool was the complement of the event set | **there is no control pool** |
| calendar-era drift (rd-j §2.2) | the null re-aligned events against other eras | both branches are drawn from the same events, same eras |
| the volatility stratum (rd-k §3) | events sat in the top decile, the null did not | both branches inherit the same stratum mix, up to the split |
| the seam (rd-k §2.1) | the return series wrapped | **nothing is shifted** |

Every confound that affects all of a situation's events **equally**
cancels in the difference. This is a genuinely stronger instrument, not
merely a different question — which is the substantive argument for going
here rather than building a fourth null.

**What it is not immune to**, and the guard for it is in §5: a confound
**correlated with the separator itself**. If taker-buy share happens to
be higher in high-volatility bars, and volatility predicts the forward
return, the split is a volatility split wearing an order-flow name — the
exact shape of rd-k's finding, one level down.

## 3. The family — 18 tests, fixed

**3 situations × 2 horizons × 3 separators. `m = 18`.**

Situations are the same three, frozen at `research/stage2_event_study.py`:
S1 support penetration, S2 resistance break, S3 abnormal activity. Their
definitions are unchanged and are all strictly backward-looking (the
prior-1440 extreme is taken over bars *strictly before* each bar; S3's
threshold is a trailing quantile), so an event is identifiable at its own
bar's close and entry remains the next bar's open.

**Horizons are 15 and 60 only, and 240/1440 are excluded by rd-l's rule
rather than by any result.** rd-l §8 item 2: *"a family whose detectable
effect exceeds its own cost floor cannot produce a candidate, and should
be re-specified or not run."* Projected from stage 2's own `null_sd`:

| situation | h | `se_full` | projected `se_diff` | detectable **difference** | detectable **branch mean** |
|---|---|---|---|---|---|
| S1 support penetration | 15 | 1.22 | 2.45 | **9.79** | 6.92 |
| S2 resistance break | 15 | 1.11 | 2.21 | **8.85** | 6.26 |
| S3 abnormal activity | 15 | 2.34 | 4.67 | **18.70** | 13.22 |
| S1 support penetration | 60 | 2.38 | 4.77 | **19.07** | 13.48 |
| S2 resistance break | 60 | 2.15 | 4.31 | **17.24** | 12.19 |
| S3 abnormal activity | 60 | 4.16 | 8.31 | **33.25** | 23.51 |
| *(S1 h=240, for contrast)* | 240 | 4.28 | 8.56 | *34.24* | *24.21* |

Basis points, at the m=18 rank-1 threshold and 80% power, median split
(`se_diff = 2 × se_full`, `se_branch = √2 × se_full`).

> **Amended 2026-09-15, after the run, and amended rather than edited.**
> **Every projected figure in the table above is too small**, because
> `se_full` is stage 2's `null_sd` — and this specification's own
> prediction 4 is what then showed that quantity is **1.05× to 2.45×
> narrower** than the event arm's true standard error
> ([`rd-l`](rd-l-what-it-would-cost-to-know.md) §4.1).
>
> Recomputed from the realised dispersion, the detectable difference is
> **19.4–44.2bp**, not 8.85–33.25, and **no cell in this family was
> powered for a tradeable branch mean** — where this section claims S1 and
> S2 at h=15 were. The corrected table is
> [`rd-n`](rd-n-stage3-separator-result.md) §5.
>
> The text is left standing because **a pre-registration rewritten after
> its own run is not a pre-registration**, and because the error is the
> more instructive half of the result: rd-l §8's power rule was applied
> here exactly as written, to the wrong input. That is recorded in rd-n §5
> rather than repaired out of existence here.

A branch difference of 10–33bp is an entirely ordinary size for a real
two-branch structure on BTC at these horizons, so h=15 and h=60 ask an
answerable question. At h=240 the same arithmetic gives 34–59bp, which is
the regime rd-l diagnosed — **including it would repeat the mistake rd-l
was written to name.**

**Stated in advance so a positive result is not over-read**: only **S1
and S2 at h=15** are powered for a *tradeable branch mean* (6.92 and
6.26bp detectable against the 12bp floor). Everywhere else a significant
separation would establish that the observable carries information, and
**not** that either branch can be traded.

**Multiplicity.** Benjamini–Yekutieli at q = 0.10, `m = 18`, penalty
`Σ1/i = 3.4951`, **rank-1 threshold 0.001590**. BY rather than BH because
the three situations use different event sets. `B = 2000`, so the
smallest attainable p is 1/2001 = 5.0e-4, comfortably under the
threshold; **B ≥ 629 is the floor** below which no test could clear the
rule at all.

## 4. The three separators, each with a mechanism

S8's standing rule: *"a hypothesis must name a mechanism — who is on the
other side and why they lose."* A separator with no named loser is a
feature wearing a costume.

### X1 — taker-buy share at the event bar

`taker_buy_base_volume / volume` on the event bar itself.

**Mechanism.** It observes directly which side is *aggressing*. After a
support penetration, a high taker-buy share means buyers are lifting
offers into the break — someone is absorbing the stop flow, which is the
shakeout signature. A low share means sellers are still hitting bids, and
the flow is real distribution.

**Why it has the best prior of the three.** S11 found order flow is the
only source in this project orthogonal to every price feature
(`|r| ≤ 0.006`), and that every price IC is negative. A separator built
from price is largely re-reading what the mean test already saw; this one
is not.

**Data.** Verified 2026-09-15 on the real store: non-NULL for all
3,661,780 rows, zero `taker_buy_base_volume > volume` violations, share
distribution median 0.4969 / mean 0.4973 / p1 0.0827 / p99 0.9139 —
symmetric and well-behaved. **526 bars have `volume = 0`**; an event on
one of those has no defined share and is **dropped and counted**, never
imputed.

> **A trap found while verifying this, worth recording**: `klines` stores
> OHLCV as **TEXT** (deliberately — exact decimal round-trip), so a SQL
> comparison on those columns is **lexicographic**. `WHERE
> taker_buy_base_volume > volume` reports 1,186,178 violations because
> `'9.005' > '13.102'` as a string. Cast first. No read path in the repo
> does this — every one selects the column and converts in Python — but an
> ad-hoc query will, and it looks entirely plausible when it does.

### X2 — extremity: how far past its own trigger the event fired

Per situation, because S3 has no level for a depth to be measured against:

- **S1**: `(prior_low − low) / prior_low`, the penetration depth, divided
  by the prior-60-bar realised volatility.
- **S2**: `(high − prior_high) / prior_high`, likewise normalised.
- **S3**: `vol60 / trailing_quantile(vol60, 0.99)`, how far past the
  volume threshold it fired.

**Mechanism.** A marginal trigger is noise brushing a level; a decisive
one is a repricing or a real flush. The normalisation is what stops this
from being a volatility measurement in disguise — and §5's guard checks
whether it succeeded rather than assuming it.

### X3 — prior trend: the context the event fires into

`(close[i] − close[i−240]) / close[i−240]`, the prior 4 hours ending at
the event bar's close.

**Mechanism.** rd-b's CSTI *Condition* layer. A support penetration
against a prevailing uptrend is a pullback that buyers are waiting for;
the same penetration inside a downtrend is continuation. The branch is
supposed to be decided by which of those it is.

### The split rule, and its one honest limitation

Each separator is split at the **median over that situation's own
events**. That is an in-sample threshold, and a trading rule could not use
it — stage 4 would have to re-specify it as a trailing quantile.

**It is deliberately the optimistic case**, and the asymmetry is what
makes it the right choice here:

- a separator that carries **no** information at its in-sample median
  will carry none through a trailing one, so a negative result is
  conclusive;
- a separator that **does** separate has to be re-specified and re-tested
  with a decision-time threshold before it means anything tradeable.

Stage 3 asks *"does this observable carry information at all?"*, and the
in-sample median answers exactly that question and no more.

## 5. The guard: SPLIT-CONFOUNDED, self-calibrating

The one confound a label permutation cannot cancel is one **correlated
with the separator**. So, for each test, the composition of the two
branches is compared on the same two axes rd-k found decisive:

- **volatility decile** (`volatility_decile`, prior-only, 10 bins)
- **hour of day** (24 bins)

measured as the **total variation distance** between the branches' two
distributions.

**The threshold is not a chosen constant.** The same label permutation
that produces the null difference also produces a null TVD, so the
threshold is that null's own **99th percentile**. A split is
**SPLIT-CONFOUNDED** when either observed TVD exceeds it — i.e. when the
branches differ in composition by more than random labelling of these
same events would produce.

This is `null_suspect`'s successor and it is better in the one way that
matters: rd-j's veto needed a magic fraction ("half the effect") that
rd-k then had to amend because the specification and the implementation
read it differently. This one has no constant to disagree about.

**The TVD is reported for every test whether it vetoes or not**, on the
standing rule that a guard's value is visible only when its number is.

## 6. The decision rule

A test **advances to stage 4** only if all three hold:

1. **BY-significant** at q = 0.10 over `m = 18` (rank-1 threshold
   0.001590);
2. **`max(|mean_high|, |mean_low|) > 12bp`** — at least one branch clears
   the measured BTC round trip. The floor is on the *branch*, not the
   difference, because a strategy trades one branch and pays one round
   trip to be in it. A real separation between two sub-cost branches is a
   fact about market structure, not a candidate;
3. **not SPLIT-CONFOUNDED.**

**All 18 are reported** — difference, both branch means, both branch
counts, both TVDs and their null 99th percentiles, permutation p, BY rank
and verdict — winners and losers alike, per rd-g §4's rule that a sweep
reporting only its winners is not a sweep.

## 7. Predictions, recorded so they can be wrong

rd-g's and rd-j's registered predictions each caught a real defect, so the
practice has earned its place twice.

1. **The most likely outcome is 0 of 18.** rd-c §1's base rate and rd-e's
   cost ceiling both say a harvestable per-event structure on a 6.96-year
   liquid instrument is the exception, and nothing about moving from a
   mean to a difference changes that prior.
2. **If anything separates, it will be X1, not X2 or X3.** Order flow is
   the only genuinely orthogonal source this project holds (S11,
   `|r| ≤ 0.006`); the two price separators largely re-read what the mean
   test already saw, and every price IC S11 measured was negative.
3. **S3 will separate least.** It is non-directional by construction —
   it fires on volume with no level, so there is no structure for a
   directional branch to form around. If S3 separates *more* than S1 and
   S2, the separator is suspect before the finding is.
4. **The realised `se_diff` will be within 20% of 2 × stage 2's
   `se_full`.** This checks §3's power projection against reality. A
   larger gap means the projection was wrong and every "detectable"
   figure above has to be re-read before any verdict is.
5. **If the 개미털기 mechanism is real at all, S1 and S2 at h=15 show
   |difference| above their detectable 9.79 and 8.85bp.** Those are the
   best-resolved cells in the family. A null result there is the strongest
   evidence against the branch hypothesis this data can produce.

**If a test does advance**, the first suspicion is the split — X1's
correlation with volatility specifically — checked before anything is
written up as a discovery. That is rd-g §6 and rd-j §6's practice, aimed
at this design's own weakest point.

## 8. Stopping rule

**The 18 may not be re-run with adjusted separator definitions,
thresholds, horizons or split rules.** A different separator, a different
split, or a fourth horizon is a **new specification**, registered before
it runs. This is rd-g §4's rule, unchanged, and it is what stops a
negative result from being retried until it turns.

Permitted without a new registration: fixing an implementation defect
found by a guard in this specification, with the defect, the correction
and the before/after both reported — as rd-k did for the seam.

## 9. What this cannot produce

- **Not a candidate.** It answers *"does an observable at decision time
  split the outcomes?"* It produces no entry, exit, size or invalidation
  rule, and the in-sample median split means even a positive result is not
  yet a decision rule.
- **Not a Korean result.** BTC 1m. [`rd-f`](rd-f-korean-cost-structure.md)
  puts the Korean round trip at 2.5–2.8× harsher, so clause 2's 12bp floor
  is the wrong floor there and a separation clearing it here may clear
  nothing in Seoul.
- **Not independence.** A label permutation is exact under its own null
  and still does not make overlapping forward windows into independent
  observations; the effective sample is smaller than the event count.
- **Not a rescue of the seven underpowered cells.** rd-l's open half was
  at h = 240 and 1440, and this family deliberately does not go there.

## 10. Order of work

1. Implement in a new `research/stage3_separator.py`. The stage 2 modules
   are **not** modified — rd-h's and rd-k's numbers stay reproducible.
2. Run all 18. Report all 18.
3. Write `.planning/rd-n-stage3-separator-result.md`, whatever it says.
