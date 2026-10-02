# External Review Phase 2 result — two structural obstructions, and no promotion route under the current rules

**Reply received 2026-10-02**, in the same conversation as Phase 1, against
`xr-d`. Every numeric claim it made was checked here before being accepted.

**The headline: the reviewer withdrew its own campaign and its own three-way
split, and then found a second obstruction we had not computed.** Its closing
sentence is the honest summary of where this project stands: *"it does not yet
have a demonstrated promotion path."* That is a finding, not a failure of the
exercise.

---

## 1. What it withdrew

Both unprompted, both for the reason Part A supplied:

- **the illustrative campaign** (one family, six cells, fourteen looks, ending
  at `N` ≈ 143) — refused by its own gate at a required Sharpe of ~3.7;
- **the three-way split of the reserved panel** — each third raises the
  80%-power requirement from 0.475 to 0.824, and development and qualification
  blocks carry the project `N` anyway, so splitting converts one usable asset
  into three unusable ones.

It restated the reason in a form sharper than ours: **the obstruction is a sign,
not a margin.** DSR's numerator carries `observed − SR0`, so where `SR0` exceeds
the observed Sharpe the numerator is negative before uncertainty enters, and a
longer window shrinks the uncertainty term without touching the sign. *"Collect
more data" is not a weak remedy there; it is not a remedy.*

**Both it and our first write-up overstated the range**, caught on review of
PR #216: at `N` = 2 the measured `SR0` is 0.658, so an observed 0.8 sits **above**
it. The sign argument covers the whole 0.4–0.8 band only from **`N` = 3** (`SR0`
= 1.080); `N` = 2 turns the bottom of the range negative and not the top.

## 2. What it found that we had not

### The fold-consistency clause is a second obstruction, independent of DSR

Verified here, and this is the most consequential thing in either phase. A
fold's sign is a coin flip tilted by the true effect, and over a 60-session
validation window at 245 sessions a year the tilt is small:

| true annualized Sharpe | P(fold Sharpe > 0) |
|---|---|
| 0.4 | 57.8% |
| 0.8 | 65.4% |
| 1.0 | 69.0% |
| 2.0 | 83.9% |

**The 80% floor needs a true Sharpe of 1.701; the 90% floor needs 2.590.** Both
are far outside the range this project cites as realistic, so the clause cannot
be satisfied by a realistic edge at this fold geometry — **whatever happens to
`N`**, and whatever window is used.

`sr-j` set 80–90% to replace a literal 100% sweep, arguing that "even a
genuinely strong, real 80%-true-edge strategy clears a literal 19/19 sweep only
~1.4% of the time." **That argument is about a win rate, not a Sharpe**, and the
substituted figure was never checked against an effect size. It is
`check_criterion_attainable`'s own failure mode one level up — unreachable for
reasons of effect size rather than sample size.

### A holdout confirmation does not satisfy the walk-forward requirement

Gate B admits holdout evidence. But the Strategy Research Methodology's first
non-negotiable says *no strategy is eligible for paper trading without
walk-forward validation (rolling train/validate windows)*, and Gate B does not
waive it. The reviewer read this more strictly than we had and is right:
**the two are reconciled today by exactly one thing — an operator exception.**

`daily-tsmom-ensemble`'s Paper Trading Policy Exception says so in its own
words: the strategy "has never been walk-forward validated via rolling
train/validate folds on any 1d data", and its two single-window confirmations
"stand in place of rolling folds here." So the precedent is a granted
exception, not a general route.

### A provenance error in our own corrected figure

It asked why **144 logged Sharpe values** sat beside a count of **129**. The
answer is a real trap: `trial_sharpe_ratios` returns a value per counted trial
**including the `infrastructure` purpose**, while the `N` this gate uses is
`research_selection_trials`, which excludes it — and `build_retrospective`
pools by purpose for exactly that reason. Recomputed on the set `N` actually
counts:

    sigma_trials = 1.2665   over 126 defined research trial Sharpes
    SR0(129)     = 2.619 x 1.2665 = 3.317        (not 3.389)

126 rather than 129 because `trade-management`'s two trials and the test
artifact have no defined Sharpe. **Units turned out not to matter** — the code
de-annualizes before taking the variance and that scaling is linear, so `SR0`
annualizes back identically. The population was wrong, not the units.

The conclusion is unchanged (3.3 is as unreachable as 3.4), and the corrected
`N` table is in `CLAUDE.md`.

### Two smaller corrections, both verified

- At 27.35 years a true Sharpe of **0.4 has 67.3% power**, not 80%. 0.475 gives
  79.9%, which is where our 80%-power figure comes from.
- Adding the power allowance to the selection term gives **3.317 + 0.475 =
  3.792** as the planning requirement, stiffer than the 3.632 observed-Sharpe
  threshold.

## 3. Where it declined to claim credit

Asked whether its architecture predicts the history, it answered **no** — "not
in the strong empirical sense you asked for" — and said it supplied neither a
return-generating model nor candidate-generation rules specific enough to
predict those results. It distinguished what its gates could have stopped early
(designs whose criteria were unattainable, campaigns whose end-state `N` already
excluded the target) from what still costs a real evaluation (every actual
performance number).

It also noted that the history already contains several routes it might
otherwise have recommended: literature-derived fixed rules, pre-registration,
independent replication, portfolio expansion, mechanism-first event research.
**"Recommending those categories again is not a substantive next step."**

The one revision the history forced on it: **consumed-data diagnosis of the
management axis becomes a justified source of candidate development** — our
Task D result moved its allocation, while leaving its statistical conclusions
intact.

## 4. Where it is weaker than our record

- **It treats the management result as merely exploratory**, which is correct
  procedurally, but it does not engage with the fact that none of 129 prior
  trials varied that axis at all. That is a statement about search coverage, and
  coverage is the thing its own architecture is supposed to manage.
- **Its "mechanism card" stage is unfalsifiable as specified.** Requiring an
  economic proposition naming who loses is good discipline; nothing in the stage
  says how a card is rejected for having a bad mechanism rather than a bad
  statistic.
- **It leaves purging unresolved in both directions**, correctly noting that a
  past-only lookback is not automatically leakage, but offering no test for
  which of our candidates would be affected.

## 5. What this leaves

**There is no promotion route under the rules as written.** Three doors, and all
three are operator decisions:

| door | what it costs |
|---|---|
| **An operator exception**, as `daily-tsmom-ensemble` received | Rests the candidate's edge on holdout evidence alone, which is what that exception already discloses as its own weakness |
| **Amend the fold-consistency clause** — longer folds, a pooled statistic over stitched returns, or an explicit Sharpe floor below which it reports UNINFORMATIVE | A Risk-Parameter-class change to the Eligibility Bar, needing explicit approval; longer folds also cost fold count |
| **Establish a defensibly lower `N`** for a genuinely unsearched axis, as Task D's registration argued | The argument exists and is recorded; whether it extends past one task is not settled |

**None of the three is a session's call**, and the first two are checkpoint-#3
material. What is settled, and now written into `CLAUDE.md`, is that the
obstructions are real, independent of each other, and not fixed by more data.

**Nothing here authorises spending the reserved panel.** The reviewer's own
closing position is the one to hold: the next work is to establish what a
confirmatory result could authorise *before* spending the window that would
produce it.
