# External Review Phase 2 — the history disclosed, and the design tested against it

**How to use this.** Build `PASTE-THIS-phase2.md` with
`scripts/build-external-review-paste.py 2` and paste it into the **same**
conversation that produced the Phase 1 design, so the reviewer answers against
its own architecture rather than a summary of it. Nothing above the rule below
travels.

**What Phase 1 established, recorded here before Phase 2 can colour it**
(`xr-e` carries the full scoring):

- **Q1 — strong.** It derived the floor-to-years inversion unprompted, computed
  the DSR expected-maximum multiplier at `N`=129 as 2.619 against our measured
  2.6189, and went past us: it separated the α=0.05 floor from a *powered*
  design, which this project had never done.
- **Q2 — pass**, by mechanism cards and an executable exposure policy, though
  it never singled out position management.
- **Q3 — rich.** 80% power as a planning standard, physically single-use
  holdouts, a development/qualification/holdout split, three linked ledgers,
  purging, stitched-return aggregation.
- **Q4 — convergent**, and it found no closure of ours that was reached by
  inference rather than measurement, except implicitly: it proposed splitting
  the window we had reserved whole.
- It also caught a real arithmetic error in our own fact package (a family
  total of 128 against a stated 129) and a real imprecision in our governing
  figure.

**The disclosure below is deliberately partial.** It gives outcomes, not
mechanisms: what was tried and what came back, with the reasoning withheld, so
the second question can still be answered independently. Our diagnosis is
Phase 3.

---

Your design is now tested against what this project actually did. Two things
follow this paragraph: an arithmetic result taken from your own method, and the
history your design did not have.

## Part A — your own accounting, run on real numbers

Your design asked for the required Sharpe to be computed against the project
count *after* the whole reserved campaign, and said the campaign should not start
if that hurdle is incompatible with the target effect. We ran it.

Measured from the trial log: the standard deviation of logged trial Sharpes is
**σ = 1.2940** (144 values, annualized). With your multiplier this gives a
selection term `SR0(129) = 3.389`. Adding the detection floor reproduces the two
figures this project had been quoting, which turn out to be window-specific
rather than general:

| window | span | SR0 + floor |
|---|---|---|
| 1-hour research | 1.84y | 3.389 + 1.213 = **4.60** |
| 1-minute futures | 6.96y | 3.389 + 0.623 = **4.01** |

**`SR0` does not shrink with calendar span.** It depends on `N` and σ only, so
the selection penalty is identical on a one-year window and a twenty-seven-year
one. On the best window this project can reach — a 27.35-year panel with a
floor of 0.315 — the requirement by `N` is:

| N | SR0 | required Sharpe | inside a 0.4–0.8 edge? |
|---|---|---|---|
| **1** | 0.000 | **0.315** | **yes** |
| 2 | 0.673 | 0.987 | borderline |
| 6 | 1.682 | 1.997 | no |
| 12 | 2.154 | 2.469 | no |
| 129 | 3.389 | 3.703 | no |

Your illustrative campaign — one family, six parameter cells, fourteen counted
looks, ending at `N` ≈ 143 — lands at a required Sharpe of roughly **3.7**.

**So your own gate refuses your own campaign.** We read that as the procedure
working rather than contradicting itself, but it leaves the design without a
path, and that is the first thing to answer.

Two further measurements, in case they change the picture:

- At **80% power** the smallest effect detectable on that 27.35-year panel is
  **0.475**, not the 0.315 floor. A true 0.4 Sharpe needs 38.6 years.
- This project's aggregation is the **mean of per-fold annualized Sharpes**, not
  a stitched validation-period return series. There is **no purging or embargo**
  anywhere in the fold generation.

### What Part A asks

1. **Given that `SR0` is invariant to span, is there any route to a promotable
   result other than keeping `N` at or near 1?** If there is, specify it. If
   there is not, say so plainly — that conclusion is more useful to us than a
   rescue.
2. **If the only route is `N` ≈ 1, your development and qualification stages
   cannot run on data that must stay promotable.** Where do candidates come
   from then, and what makes a specification good enough to spend a single
   access on without having tested it on promotable data?
3. **Does the 80% power figure change which windows you would use, or what you
   would consider worth running at all?**
4. You proposed splitting the reserved 27-year panel three ways. **Does the `N`
   table change that recommendation?** Answer for the split specifically.

## Part B — the history

Every item below was walk-forward validated or holdout-confirmed against real
exchange data and recorded at the time. **Nothing has ever cleared the bar.**

| line of work | what was run | outcome |
|---|---|---|
| Single-asset price signals, 15m and 1h: moving-average crossover, risk-managed crossover, a multi-lookback ensemble with regime weighting and volatility targeting, regime-gated mean reversion, a momentum/mean-reversion blend, on-balance-volume trend, funding-rate extremity | 8 strategies in 4 families, 18 distinct configurations from 33 runs | **0 of 18 survived.** Best: mean annualized Sharpe +0.039 |
| A zero-fitted-parameter daily time-series-momentum ensemble from the literature, on a reserved early daily window | 1 pre-registered access | Missed on significance, on Sharpe-vs-floor, and on trade count; cleared drawdown and profit factor |
| The same hypothesis, byte-identical code, on a second venue's untouched earlier window | 1 pre-registered access | Closest anything has come: 3 of 5 gates cleared by wide margins. Missed trade count by 4 and the drawdown ceiling by 0.135 points |
| Two macro-conditioned variants on an untouched daily research split | 2 attempts | Both below the trade-count floor; remaining figures descriptive only |
| Meta-analysis combining the two independent daily holdouts | 0 new accesses | Combined significance genuinely stronger than either leg. A full pass was **provably impossible**: combined drawdown is bounded below by the worse leg's, already over the ceiling |
| Multi-asset expansion to 10 Korean names, same strategy, as a pre-registered portfolio holdout | 1 pre-registered access | Below floor on Sharpe and PSR. **Trade count was the only gate that passed** — and the only one diversification was guaranteed to fix. Mean member Sharpe +0.0184 |
| Retail scalping, 1-minute: a methodology rebuild followed by two candidates | 17 tasks | **No candidate.** The best cleared a real significance test and died on the selection correction |
| A four-stage programme asking which *situations* are worth being present for rather than which formula predicts direction | 6 situations, then 12 tests run twice under different nulls, then 18 tests | **No candidate.** 0 of 12 and 0 of 18. An effect's size turned out to be a property of the (event, null) pair |
| A trader-style multi-leg position model, then a conjunction-gated tactical hedge over an unchanged core | pre-registered, both timeframes | **Rejected.** The tactical leg's gross edge was negative before the fees it caused |
| The entry fixed from published literature, varying **only** post-entry management across six fully specified policies | pre-registered comparison | **Two policies cleared the operational bar — the first time anything here has.** The entry was byte-identical across all six, so the whole spread is a management effect. Not promotable: the window was already spent |

Two further facts about that last row, because they cut both ways: **none of the
129 prior trials had varied that axis at all**, and the winning policy's result
is concentrated in a single year — four of eight years positive, with one year
supplying more than the entire total.

### What Part B asks

5. **Does your architecture predict these outcomes?** Not "would it have
   avoided them" — would running it on this data have produced them? A design
   that cannot account for a decade of negative results is probably proposing a
   path already walked.
6. **Which of your stages would have stopped each failure earlier, and which
   would have produced the same result at the same cost?** Be specific about
   which.
7. **Which of these results does your design treat as informative, and which as
   uninformative?** The distinction matters more to us than the verdicts.
8. **What would you change in your Phase 1 architecture now?** State changes as
   revisions with reasons, not a fresh design — we want to know what the history
   moved, and what it did not.

## Form

Prose and tables. No code. Answer Part A before Part B; where an answer to Part
B changes an answer to Part A, say so explicitly rather than quietly revising.
Where you give a number, say whether it came from this document, from a standard
result, or from your own estimate.
