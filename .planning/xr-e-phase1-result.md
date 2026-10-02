# External Review Phase 1 result — scored against xr-c, and what it corrected in this project

**Reply received 2026-10-02.** Scored against `xr-c`'s four questions, which were
fixed before the reply existed. Every numeric claim the reply made about this
project was checked against the repository rather than taken at face value;
where it was right, the reproduction is shown.

---

## 1. Scoring

### Q1 — did it independently reach the power problem? **Strong, and it went past us.**

The fact package gave the detection-floor formula and every window's floor, and
withheld both the comparison to a realistic edge and the arithmetic converting
the trial count into a required Sharpe. The reply produced:

- the floor-to-years inversion, `Y = (1.6449 / S)²`, unprompted;
- the DSR expected-maximum multiplier at `N` = 129 as **2.619**, against
  **2.6189** measured from `runs/experiments.jsonl` here;
- and the separation this project had never made: **a detection floor is not a
  powered design.** It added the power quantile, `Y₈₀ = ((1.6449 + 0.8416)/S)²`,
  a factor of 2.28 more years than the floor demands.

It also stated, correctly, that the required Sharpe **cannot be determined from
`N` alone** — it needs the dispersion of the trial Sharpes and the candidate
window's span.

### Q2 — did it search an axis other than the entry formula? **Pass.**

Its stage 2 produces "mechanism cards" requiring an economic proposition naming
who is on the other side and why they lose, and its deliverable is an executable
*exposure policy* rather than a signal. Universe construction, sizing and
execution are separate axes. It explicitly warns against searching features,
portfolio construction, stops and regimes simultaneously in one campaign.

**It did not single out position management**, which is the one axis this
project has measured a real effect on. That remains ours.

### Q3 — did it propose anything we have not tried? **Yes, ten things.** Ranked by what they would change here:

1. **80% power as the planning standard**, not the floor. Changes which windows
   are worth running at all.
2. **Physically single-use holdouts** — flip `SEALED` → `CONSUMED` atomically
   *before* the first read, and leave it consumed if the job then crashes. We
   have the policy and no mechanism.
3. **A development / qualification / holdout split**, separating candidate
   generation from promotable evidence. We have research/holdout only.
4. **Three linked ledgers** — research, evidence-access, statistical — with
   contamination ancestry and a permanent consumption state.
5. **Purging and embargo**: training labels whose outcomes cross a validation
   boundary must be removed. **Confirmed absent here** (no match anywhere in
   `walkforward.py`).
6. **Aggregate from stitched, non-duplicated validation-period returns**, not
   the mean of per-fold annualized Sharpes. **Confirmed**: `mean_sharpe` is
   `sum(defined)/len(defined)`.
7. **Fold wins are not independent merely because validation intervals are
   disjoint** — shared fitting history and persistence have to be examined
   before a sign test is quoted.
8. **A registered executable shadow policy for every screening statistic**, so
   an IC-style measurement carries real daily returns rather than an invented
   IC-to-Sharpe conversion.
9. **Quarantine pre-2000 separately** on the unverified-quality ground.
10. **A holdout is not a precondition for paper trading** under our own Gate B
    wording — correct reading, and we had been treating it as one.

### Q4 — did it propose anything we abandoned? **Convergent, with one implicit reopening.**

CSCV/PBO deferral (same reason: no retained per-candidate curves), no options
symbol format, no single-stock futures for shorting, no limit-order validation
on a fill-on-touch model — all four match our own closures, and all four of ours
were closed by measurement.

The one closure it pushed on was **reserving the 27-year panel whole**: it
proposed splitting it three ways. Ours was reached by analogy to an earlier
decision rather than by measurement, so it qualified for reopening — and §3
below is the measurement that settles it.

### What xr-c pre-committed not to score

Eloquence about overfitting: the reply restates the independence rules at
length, and `xr-c` said in advance that this does not count because the rules
were given to it. What counts is whether the architecture changed in response,
and it did — the three-way split, the ledgers and the atomic consumption are all
structural answers rather than warnings.

---

## 2. What it corrected in this project

### A real arithmetic error in the fact package

It reported that the per-family counts summed to **128** against a stated `N` of
**129**. Verified: the document listed seven families and the eighth was a
single-member **test artifact** — a suite that once appended to the live
research log, which `CLAUDE.md` already records and which is counted
deliberately, since an inflated `N` can only lower a later DSR. The document was
incomplete, not the count. Fixed, with the family anonymised so its name does
not disclose a signal class.

### A real imprecision in this project's governing figure

`CLAUDE.md` quoted "at `N` in the 120s the DSR-0.95 bar is an annualized Sharpe
near **4.00**". Reproduced from the log:

    sigma_trials = 1.2940   (144 logged trial Sharpes, annualized)
    SR0(129)     = 2.619 x 1.2940 = 3.389
    required     = SR0 + 1.6449 / sqrt(Y)

| window | Y | SR0 + floor | the figure this file had quoted |
|---|---|---|---|
| 1h research | 1.84y | 4.60 | "would have needed an annualized Sharpe of 4.6" |
| Binance futures 1m | 6.96y | 4.01 | the **4.00** row |

So **4.00 was the 1-minute window's number**, not the project's, and the column
heading hid both other inputs. Corrected in `CLAUDE.md`.

### A leak this check did not catch

§5 carried *"this moved one p-value from 0.016 to 0.182"* and *"took the count
surviving Benjamini-Hochberg from 1 to 0"* — past results, in a document whose
purpose is to withhold them. The hand-run scan passed them because it looked for
strategy names and verdict words, and those sentences contain neither.

**The reviewer had already read them, so Phase 1 ran with a partial leak.** The
affected material is this project's independence corrections; Q1, which is the
load-bearing score, is unaffected because it rests on the floor and `N` figures
that were given deliberately. Recorded rather than quietly fixed.

The remedy is `python/research/external_review_leakcheck.py`, now in the suite
with a `MEASURED-OUTCOME` bucket keyed on **shape** rather than name — a
measured quantity with a concrete value, or a transition between two values.
Six mutations, six caught; one of those only after the required-facts check was
given synthetic input, since `assert not missing` passes on an empty list however
that list was produced.

---

## 3. The finding neither side had: the selection term is invariant to span

`SR0` depends on `N` and `sigma_trials`. **It does not depend on the window's
length**, so the selection penalty is identical on a one-year window and a
twenty-seven-year one. On the reserved pre-2019 panel (27.35y, floor 0.315):

| N | SR0 | required Sharpe | inside a realistic 0.4–0.8 edge? |
|---|---|---|---|
| **1** | 0.000 | **0.315** | **yes** |
| 2 | 0.673 | 0.987 | borderline |
| 6 | 1.682 | 1.997 | no |
| 12 | 2.154 | 2.469 | no |
| 129 | 3.389 | 3.703 | no |

Three consequences, and they resolve the one disagreement:

1. **Reserving that window for a single pre-registered confirmation was
   right**, and for a stronger reason than the one recorded. `N` = 1 is the only
   setting at which the window's own power decides the outcome; at `N` = 2 the
   requirement is already outside the plausible range.
2. **The reply's three-way split fails here.** Development and qualification
   stages carry the project `N`, so a six-year qualification segment needs
   **4.06**. Splitting the panel converts the one usable asset into three
   unusable ones.
3. **The reply's own campaign is refused by the reply's own gate.** One family,
   six cells, fourteen counted looks ends at `N` ≈ 143 and a requirement of
   ~3.7. It had instructed exactly this check and said the campaign should not
   start if the hurdle is incompatible — so the procedure is working, and it
   leaves itself without a path. That is Phase 2's first question.

**And the route that remains is narrower than we had recorded.** At 80% power
the smallest effect detectable on that panel is **0.475**, not 0.315: a true 0.4
Sharpe needs 38.6 years against the 27.35 available. The lower half of the
0.4–0.8 range this project keeps citing is still not visible, even on its best
window.

---

## 4. What was changed as a result

| change | where |
|---|---|
| Family total 128 → 129, artifact family anonymised, `sigma_trials` added | `xr-a` §5 |
| Past results stripped from the statistical rules | `xr-a` §5 |
| `4.00`'s window-specificity and `SR0`'s span-invariance recorded, with the `N` table | `CLAUDE.md`, scalping arithmetic |
| 80%-power line recorded beside every detection floor | `CLAUDE.md`, the KRX windows |
| Leak check committed, in the suite, mutation-verified | `python/research/external_review_leakcheck.py` |
| Paste bundle moved to the repository root and gitignored | `scripts/build-external-review-paste.py` |

**Not changed, and deliberately**: purging/embargo and the fold-aggregation
method. Both are real findings, and both are changes to the evaluation
machinery rather than to a document — they need their own pass, and whether
purging binds at all depends on whether a candidate's labels look forward,
which none of this project's current strategies do.

**Nothing here authorises spending a holdout.** Every path still ends at a
specification needing its own pre-registration and operator approval.
