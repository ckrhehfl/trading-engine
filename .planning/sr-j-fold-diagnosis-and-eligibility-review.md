# Strategy Research Task J: diagnosing the 8 remaining negative folds, and a proposed (not-yet-approved) revision to the Eligibility Bar's "positive Sharpe in every fold" criterion

## Scope note

`.planning/sr-i-ensemble-refinement.md`'s Configuration C (ADX threshold
recalibration + opt-in risk:reward grid search) is the strongest real
result in this project's history: mean Sharpe **+0.027** (positive for
the first time), 11/19 folds positive, mean profit factor 1.97, worst
drawdown 4.2% -- 4 of 5 Eligibility Bar criteria pass, only "positive
Sharpe in every fold" still fails (11/19, not 19/19). The human explicitly
declined to keep blindly tuning parameters to force the remaining 8 folds
positive, correctly naming the overfitting risk this project's own
MinBTL/parameter-sensitivity tooling (`.planning/sr-g-overfitting-
safeguards.md`) exists to catch. This task has two deliberately separate
parts, per its own brief: **Part 1 diagnoses** the 8 negative folds (no
new tuning code); **Part 2 researches** whether CLAUDE.md's literal
"100% of folds positive" bar is itself a statistically appropriate
criterion, and proposes a revision **for the human to approve or reject**
-- not applied here, exactly like the original Eligibility Bar itself was
presented for sign-off before being adopted.

Every number below is computed from a real re-run against real, cached
BingX 1h data (`python/data/var/klines.sqlite3`) or real published Python
statistics-library arithmetic (`math.comb`, `statistics.mean/stdev`) --
nothing is estimated or hand-derived.

---

## Part 1: diagnosing the 8 negative-Sharpe folds

### Reproduction methodology and verification

Reproduced sr-i's Configuration C exactly: `EnsembleMomentumTrainable`
with `adx_low=RECALIBRATED_ADX_LOW_THRESHOLD` (25),
`adx_high=RECALIBRATED_ADX_HIGH_THRESHOLD` (50), `fee_bps=5`,
`slippage_bps=2`, `params={"candidates": DEFAULT_RISK_REWARD_TENTHS_CANDIDATES}`
(`((15,), (20,), (25,), (30,))`), `train_bars=2160, validate_bars=720,
step_bars=720, bars_per_day=24`, data via
`research.holdout.load_research_klines` against
`configs/research/holdout_1h.json` (same config, same cached data sr-h/
sr-i used -- no re-fetch needed) -> **16,078 bars**
(`2024-04-27T10:00:00Z` -> `2026-02-26T07:00:00Z`, identical range to
sr-h/sr-i).

**Real re-run result (`run_id=a7f8185d-3de4-42ec-88c0-7b37ff7a542f`)
matches sr-i's Configuration C aggregate exactly**, confirming this is a
valid apples-to-apples reproduction, not a different config:

| metric | sr-i's reported Config C | this task's real re-run |
|---|---|---|
| mean Sharpe | +0.027 | +0.0271 |
| min Sharpe | -8.442 | -8.4418 |
| folds positive | 11/19 | 11/19 |
| worst drawdown | 4.23% | 4.2329% |
| mean total return | +0.21% | +0.2132% |
| total trades | 199 | 199 |
| mean profit factor | 1.967 | 1.9674 |
| min profit factor | 0.128 | 0.1282 |

The 8 negative folds: **4, 5, 6, 10, 11, 12, 16, 17** (0-indexed, matching
`generate_folds`' fold ordering).

### Full per-fold breakdown (real data)

`recon_trades` = trade count from `metrics.position.reconstruct_trades`
applied to that fold's real `(filled_intents, fills)` -- confirms the
fold's own reported `num_trades` (which counts fills/force-closes) against
an independent trade-lifecycle reconstruction; the two differ by exactly
the folds with a still-open position force-closed at the fold boundary
(one fewer "closed trade" than "num_trades" whenever that happens, e.g.
fold 2: 7 vs 6).

| fold | sign | validate window | net price move | high/low range | Sharpe | trades | win rate |
|---|---|---|---|---|---|---|---|
| 0 | POS | 2024-07-26 -> 2024-08-25 | -5.09% | 21,148.8 | +4.283 | 8 | 62.5% |
| 1 | POS | 2024-08-25 -> 2024-09-24 | -0.54% | 12,568.7 | +2.522 | 10 | 40.0% |
| 2 | POS | 2024-09-24 -> 2024-10-24 | +5.30% | 10,673.9 | +4.685 | 6 | 50.0% |
| 3 | POS | 2024-10-24 -> 2024-11-23 | +47.02% | 34,090.4 | +2.208 | 5 | 60.0% |
| **4** | **NEG** | 2024-11-23 -> 2024-12-23 | -2.52% | 17,523.0 | **-2.207** | 14 | **28.6%** |
| **5** | **NEG** | 2024-12-23 -> 2025-01-22 | +8.96% | 20,933.0 | **-1.999** | 14 | **21.4%** |
| **6** | **NEG** | 2025-01-22 -> 2025-02-21 | -6.48% | 15,941.8 | **-6.722** | 12 | **16.7%** |
| 7 | POS | 2025-02-21 -> 2025-03-23 | -14.50% | 22,871.8 | +5.076 | 11 | 63.6% |
| 8 | POS | 2025-03-23 -> 2025-04-22 | +4.87% | 14,365.0 | +1.308 | 15 | 33.3% |
| 9 | POS | 2025-04-22 -> 2025-05-22 | +25.19% | 23,650.3 | +0.463 | 9 | 55.6% |
| **10** | **NEG** | 2025-05-22 -> 2025-06-21 | -6.29% | 11,613.6 | **-7.029** | 9 | **33.3%** |
| **11** | **NEG** | 2025-06-21 -> 2025-07-21 | +14.40% | 25,106.1 | **-0.804** | 8 | **37.5%** |
| **12** | **NEG** | 2025-07-21 -> 2025-08-20 | -4.17% | 12,720.3 | **-3.989** | 15 | **33.3%** |
| 13 | POS | 2025-08-20 -> 2025-09-19 | +2.36% | 10,664.6 | +3.041 | 13 | 30.8% |
| 14 | POS | 2025-09-19 -> 2025-10-19 | -7.85% | 24,610.6 | +4.608 | 9 | 33.3% |
| 15 | POS | 2025-10-19 -> 2025-11-18 | -15.28% | 27,218.1 | +5.374 | 8 | 50.0% |
| **16** | **NEG** | 2025-11-18 -> 2025-12-18 | -4.70% | 13,958.6 | **-2.349** | 13 | **23.1%** |
| **17** | **NEG** | 2025-12-18 -> 2026-01-17 | +9.07% | 13,497.7 | **-8.442** | 9 | **11.1%** |
| 18 | POS | 2026-01-17 -> 2026-02-16 | -27.47% | 35,799.9 | +0.489 | 5 | 20.0% |

### Do the negative folds cluster, or scatter?

**They form three contiguous runs, not isolated singletons scattered
through the sequence**: `[4,5,6]`, `[10,11,12]`, `[16,17]`. Written as a
POS/NEG sequence across all 19 folds:

```text
P P P P | N N N | P P P | N N N | P P P | N N | P
```

Visually, this is a striking, almost mechanically alternating ~3-on/~3-off
pattern. **Quantified rigorously, not just eyeballed**: a Wald-Wolfowitz
runs test on this exact 11-positive/8-negative sequence (`n1=11, n2=8,
N=19`) observes **7 runs**, against an expected `E[R] = 2*n1*n2/N + 1 =
10.263` under a null of random arrangement (`Var[R] = 4.252`, `SD[R] =
2.062`), giving **z = -1.582** (`(7 - 10.263) / 2.062`), **one-sided
p ~= 0.057, two-sided p ~= 0.114**.

**Honest reading of that number, not rounded up to "significant" or down
to "not real"**: this is *suggestive* of real clustering (fewer runs than
random chance would typically produce) but does **not** clear the
conventional 5% significance threshold -- it sits right at the boundary
(one-sided p=0.057, just above 0.05). With only 19 folds, this specific
statistical question (is the arrangement of positive/negative folds
distinguishable from random) itself has limited power -- the same kind of
small-sample caveat this project's own fold-count credibility floor (8-10
minimum) already exists to flag for the main walk-forward metrics. **Not
strong enough to assert "definitely a real, describable market regime,"
but also not weak enough to dismiss as "definitely scattered noise."**
This ambiguity is itself the honest finding, not a result to be resolved
by picking whichever framing supports further tuning.

### What actually discriminates negative folds from positive folds

Three candidate discriminators were tested against the real data. Two do
**not** discriminate; one does, clearly:

| candidate discriminator | NEG folds (n=8) | POS folds (n=11) | discriminates? |
|---|---|---|---|
| mean (high-low range) / abs(net move) ("choppiness ratio") | 2.836 | 5.689 | **No** -- and in the *opposite* direction a "choppy negative folds" story would predict (positive folds have the larger relative range, if anything) |
| mean ADX (continuous, computed once across the whole dataset, the same series the strategy's own regime filter reads) | 36.69 | 37.81 | **No** -- nearly identical |
| % of bars inside the recalibrated ADX "ramp" zone (25-50, neither fully suppressed nor full-conviction) | 52.4% | 52.6% | **No** -- essentially identical |
| **trade-level win rate** (`metrics.position.reconstruct_trades`, real closed trades) | **25.6%** | **45.4%** | **Yes** -- a large, consistent gap |

The whole-fold-level "how choppy did this 30-day window look in
aggregate" framing (the natural first hypothesis -- large round trips
without net progress) **does not hold up against the real data**: the
strategy's own real trade outcomes, not the fold's aggregate price
statistics, are what actually separate the two groups. The negative folds
are folds where entries were *followed through on* far less often (the
crossover signal fired, but price reversed and hit the stop before the
target, well below the fold's own implied breakeven rate in most cases --
e.g. fold 6's 16.7% win rate against a ~25% breakeven for that fold's
chosen 1:3 risk:reward, fold 17's 11.1% against the same ~25% breakeven),
not folds with an unusually large single loss or an unusually high trade
count. This is a genuine signal-quality collapse at the trade level, not
a cost or position-sizing artifact -- several negative folds' win rates
sit *below* even the raw, cost-free breakeven rate implied by that fold's
own risk:reward choice, a stronger and more direct form of the "raw
signal weakened" finding than sr-i's own aggregate-level diagnosis (which
found the *aggregate* raw edge only marginally positive before costs).

**Two more candidate explanations were checked and ruled out**:

- **An overfit in-sample risk:reward pick driving the losses.** Each
  fold's winning `risk_reward_tenths` candidate and its Task G
  parameter-sensitivity verdict (`is_robust`) were extracted via
  `sensitivity_extractor`. Negative folds are robust 5/8 of the time
  (62.5%); positive folds 8/11 (72.7%) -- a mild difference, not a clean
  separator. The negative folds are not disproportionately driven by a
  spiky, overfit-looking in-sample risk:reward selection.
- **A single catastrophic event inside a cluster.** Inspecting the real
  trade ledger for each negative fold (available in this task's
  now-deleted diagnostic script's output, not reproduced verbatim here to
  keep this document a reasonable length) shows a consistent pattern of
  *many* small-to-moderate losing trades interspersed with a few real
  winners, not one dominant catastrophic trade -- e.g. fold 4's worst
  single loss is -$44.36 against 8 losing trades total; fold 17's worst
  is -$87.24 against 6 losing trades total. This is a base-rate problem
  (too many stopped-out entries), not a single blown-up position.

### Verdict: describable at the surface, but not a narrow, honestly-justifiable fix

**Neither "clean, fixable structural gap" nor "pure scattered noise."**
The three negative clusters correspond to real, contiguous stretches of
this project's real BTC price history where this specific trend-following
signal's follow-through rate collapsed well below its own breakeven --
that is a real, evidenced, describable proximate mechanism (not
speculation), and the runs-test result (borderline, not clearly random)
is consistent with, though doesn't conclusively prove, this being more
than coincidence. But:

1. **The one instrument built specifically to detect this exact failure
   mode -- ADX, already recalibrated to BTC's own empirical distribution
   in Task I -- does not discriminate these periods from the winning
   ones on this data** (36.69 vs 37.81 mean ADX; 52.4% vs 52.6% ramp-zone
   occupancy). There is no existing strategy input available today that
   would have flagged these windows in advance, leakage-free.
2. **Only three distinct episodes exist in ~20 months of real data.**
   Designing and validating a new rule specifically to catch these three
   known episodes, with no fourth or fifth instance to check it against,
   is not meaningfully different from fitting parameters to the exact
   folds already known to be bad -- precisely the blind-tuning trap the
   human explicitly asked this task to avoid, not a narrow, defensible
   fix.
3. **The choppiness-ratio hypothesis, the most natural "market condition"
   explanation, is directly contradicted by the real data** (opposite
   direction from what it would need to show). Whatever is actually
   happening operates at a finer time granularity (individual crossover
   entries getting stopped out) than any of this diagnosis's whole-fold
   descriptive statistics can characterize with the tools built so far.

**This is closer to "an inherent, real characteristic of how this
specific trend-following signal interacts with BTC's actual regime-
cycling behavior in this particular historical window" than either a
one-off bug or contentless random noise** -- but per this task's explicit
brief, that is not the same as a real, narrow, honestly-justifiable fix
ready to implement. None is proposed here. If a future task wants to
pursue this further, the concrete, narrow next step suggested by this
diagnosis (not attempted here) would be a leakage-safe, trade-level
follow-through/false-breakout rate estimator (distinct from ADX's
trend-*strength* framing) -- but that is a new indicator requiring its
own design, testing, and walk-forward evaluation, not a parameter tweak,
and should go through its own `Discuss` pass rather than being bolted on
under this diagnosis task's brief.

---

## Part 2: is "positive Sharpe in every fold" a statistically appropriate bar?

### The question, precisely

CLAUDE.md's Eligibility Bar currently requires "positive annualized
Sharpe in every fold (not just on average)." Configuration C clears every
other criterion but fails this one (11/19). Is 100%-of-folds-positive a
meaningful statistical requirement, or stricter than what credible
institutional/academic practice actually uses?

### Research, with credibility grading

This project's earlier deep research passes (`.planning/sr-g-overfitting-
safeguards.md`, `.planning/sr-h-ensemble-regime-voltargeting.md`) already
established a credibility-grading habit (source type, cost inclusion,
out-of-sample validation, reproducibility, conflict of interest,
survivorship bias) without ever writing that rubric down as its own
durable artifact -- applied here explicitly, per-source, for the first
time as a named table.

**Honest correction to this task's own brief**: the brief states "this
project's own earlier research already found Walk-Forward Efficiency and
CSCV/PBO as more rigorous alternatives." A repo-wide search confirms
**CSCV/PBO is genuinely already documented** (`sr-g`, Finding 3, citing
Bailey/Borwein/López de Prado/Zhu directly) -- but **Walk-Forward
Efficiency (WFE) does not appear anywhere in this repo before this
document.** Treated here as newly researched and documented for this
project, not retrieved from an existing writeup, same "don't invent a
citation trail that isn't real" discipline sr-h's own "honest citation-
completeness gap" paragraph established.

| # | Source | Source type | Cost inclusion | OOS validation | Reproducibility | Conflict of interest | Survivorship bias | Grade |
|---|---|---|---|---|---|---|---|---|
| 1 | Pardo, *The Evaluation and Optimization of Trading Strategies* (Wiley, 2008; concept originates 1992) -- Walk-Forward Efficiency (WFE = OOS performance / IS performance) | Practitioner book, industry-standard reference, not a peer-reviewed journal | Metric-agnostic (works on cost-inclusive Sharpe/return/PF, doesn't itself model costs) | Yes -- WFE *is* an OOS-vs-IS ratio, the entire point of the metric | High -- a simple, well-defined ratio, trivially computable from data this project already logs per fold | Mild -- Pardo sells a methodology book, but the technique is independently adopted industry-wide well beyond his own commercial interest | N/A -- a validation *procedure*, not an asset-universe claim | **Solid practitioner-standard**, not independently verified against primary text this session (retrieved via search synthesis, same disclosed-gap convention as sr-h) |
| 2 | Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting," *Journal of Computational Finance* 20(4):39-69 (DOI 10.21314/jcf.2016.322) -- CSCV/PBO | Peer-reviewed academic journal | N/A (methodology paper about overfitting risk, not a specific strategy) | Yes -- the paper's entire subject is OOS overfitting risk | High -- open, published method, already the basis of this project's own `research/overfitting_check.py` | Low -- academic/quant-research authors; the paper's finding (multiple-trial overfitting risk) argues against easy edge claims, not for them -- self-limiting, not self-promoting | N/A | **High** -- already this project's trusted primary source for MinBTL (sr-g) |
| 3 | Binomial/sign test on fold-level win/loss counts (e.g. observed win rate vs. null 50%, `math.comb`-computable) | Standard classical statistics (any introductory statistics text), not a single novel paper -- a specific worked example ("41% observed vs 50% null, p=0.89") was found via search synthesis but its original source could not be independently pinned down | N/A -- operates on already-cost-inclusive per-fold results | Directly tests whether observed fold outcomes could arise from a no-edge (p=0.5) process | Very high -- exact, closed-form, `math.comb` (stdlib, already used in this diagnosis), no new dependency | None -- generic statistical method, no authorship incentive either way | N/A | **Sound as technique** (textbook statistics), **weak as a specific citation** -- presented here as a general method, not attributed to one authoritative paper, matching this project's "don't fabricate precision" discipline |
| 4 | Harvey & Liu, "Backtesting," SSRN/published (2015) -- multiple-testing "haircut Sharpe ratio" (Bonferroni/Holm/BHY corrections) | Peer-reviewed-adjacent (SSRN, later journal placement); Harvey is a highly-cited finance academic (Duke) | N/A (methodology, not a specific backtest) | Directly about correcting in-sample-selected Sharpe ratios for how many configurations were tried -- same spirit as MinBTL | High -- documented method, open-source implementations exist (`quantstrat::SharpeRatio.haircut`) | Mild -- Harvey holds industry advisory roles, but the paper's finding (discount reported Sharpes) is conservative/self-limiting, not self-serving | N/A | **High** -- independently corroborates this project's existing MinBTL approach from a different, complementary angle |
| 5 | Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier" (*Journal of Risk* 15(2), 2012) and "The Deflated Sharpe Ratio" (*Journal of Portfolio Management* 40(5), 2014) -- Probabilistic Sharpe Ratio (PSR) / Deflated Sharpe Ratio (DSR) | Peer-reviewed academic journals, same author lineage as source #2 | N/A (methodology; operates on whatever Sharpe series is fed in, cost-inclusive or not) | PSR directly answers "is this observed Sharpe statistically distinguishable from a benchmark (e.g. zero)," correcting for non-normal (skewed/fat-tailed) returns -- exactly the shape of a stop/target strategy's real trade distribution | Moderate -- formula is public and documented, but a correct implementation (skew/kurtosis-adjusted variance of the Sharpe estimator) is meaningfully more work than a plain t-test; not implemented in this task | Low, same reasoning as #2 | N/A | **High** -- same trusted lineage as this project's existing MinBTL work, and the most statistically appropriate tool of everything reviewed here for the specific "is the aggregate Sharpe real" question |

### The binomial framing, computed exactly (not estimated)

If a strategy has a genuine, structural per-fold win probability `p`
(the "true" chance any given fold's Sharpe comes out positive), the
probability that **all 19** folds are positive purely by chance is `p^19`
-- and the probability of **at least 11 of 19** (Configuration C's actual
result) is the standard binomial tail sum. Computed exactly via
`math.comb` (stdlib, no new dependency):

| assumed true per-fold win probability `p` | P(all 19/19 positive) | P(>=11/19 positive) | P(>=17/19 positive) |
|---|---|---|---|
| 0.50 (no real edge, coin flip) | 0.0002% | 32.380% | 0.036% |
| 0.60 | 0.0061% | 66.748% | 0.546% |
| 0.65 | 0.0279% | 81.451% | 1.696% |
| 0.70 | 0.1140% | 91.608% | 4.622% |
| 0.75 | 0.4228% | 97.125% | 11.134% |
| 0.80 | **1.4412%** | 99.334% | 23.689% |
| 0.85 | 4.5599% | 99.916% | 44.132% |
| 0.90 | 13.5085% | 99.996% | 70.544% |
| 0.95 | 37.7354% | 100.000% | 93.345% |

**The concrete point**: a strategy with a genuinely strong, real 80%
true per-fold win probability -- a very good trend-following strategy by
any reasonable standard -- still only produces a literal 19/19 clean
sweep **1.44% of the time** in a single real 19-fold sample. Requiring
100% effectively demands a true per-fold reliability north of ~95% before
even a coin-flip's chance of passing exists at all (37.7% at p=0.95, and
climbing further only above that) -- a bar no realistic BTC systematic
strategy, and (per source #1's grading above -- a practitioner reference
retrieved via search synthesis, *not* independently verified against
Pardo's primary text this session) no strategy under Pardo's own WFE
convention either, which is commonly reported as treating **50-60%**
*out-of-sample-vs-in-sample performance retention* (a different
quantity from "% of folds positive," but the same order-of-magnitude
message: real, credible practitioner bars for OOS consistency sit far
below 95-100%) as "good," plausibly clears. **The literal 100% bar is
not measuring "is this a good strategy" so much as "did this specific
19-fold sample get lucky enough to look flawless,"** which is a
materially different (and much rarer, even for a genuinely good
strategy) event.

**A load-bearing statistical caveat on all of the above, not to be
glossed over**: every binomial calculation in this section assumes each
fold's positive/negative outcome is an **independent, identically
distributed (i.i.d.) Bernoulli trial** with a single fixed true
probability `p`. Part 1's own diagnosis found the opposite is at least
plausible: the 8 negative folds cluster into 3 contiguous runs (a
borderline-significant runs-test result, one-sided p~=0.057 -- see
above), consistent with adjacent folds' outcomes being **serially
correlated** rather than independent draws (e.g. a multi-month
market regime bleeding across more than one 30-day fold boundary). If
real, that dependence would make the true sampling variance of "number
of positive folds out of 19" *larger* than the simple i.i.d. binomial
model above assumes -- meaning the exact percentages in the table (and
the false-positive/power figures below) are a **first-order
approximation, not an exact result**, and are likely mildly
*optimistic* about how cleanly a percentage threshold separates real
edge from chance under this specific strategy's actual (possibly
regime-correlated) fold-to-fold behavior. A block permutation test or
block bootstrap (resampling contiguous *runs* of folds rather than
individual folds, preserving whatever serial dependence is really
present) would be the statistically correct refinement here -- named as
a known limitation and a concrete next step for whoever implements this
proposal, not built in this task (research/proposal only, no code).

### Applying this project's own real result honestly -- does the proposed bar just let Configuration C pass?

**No** -- and this is worth stating plainly, since a criterion revision
proposed by the same task that diagnoses a strategy just short of the old
bar invites exactly that suspicion. Two significance checks, computed
against Configuration C's real 19 fold-level Sharpe values. **The same
i.i.d. caveat raised above for the binomial framing applies equally to
the t-test below, and to this section's own conclusion** -- a plain
one-sample t-test also assumes 19 independent observations; if the fold
outcomes are really serially correlated (Part 1's borderline-significant
clustering finding), the true standard error of the mean fold Sharpe is
understated by the naive `sd / sqrt(19))` formula, meaning the real
t-statistic's significance is *more* uncertain than the number below
states at face value, not less. Both numbers below are therefore i.i.d.
reference results, not a fully dependence-robust confirmation -- a real
implementation of this proposal should compute both checks (sign test
and mean-Sharpe significance) via a block permutation or block bootstrap
over contiguous fold runs rather than the closed-form i.i.d. formulas
used here for a first-pass, transparent illustration:

- **Binomial sign test** (H0: true per-fold win probability = 0.5, i.e.
  no real edge): observed 11/19 positive. `P(X >= 11 | n=19, p=0.5) =
  32.38%` -- nowhere near the conventional 5% significance threshold.
  **11/19 alone is statistically indistinguishable from a coin flip.**
- **One-sample t-test on the 19 fold Sharpe values** (H0: true mean
  Sharpe = 0): mean = +0.027, sample stdev = 4.318, `t = mean / (sd /
  sqrt(19)) = 0.0274` -- far below the ~2.10 critical value a two-sided
  5% test at 18 degrees of freedom would need. **The aggregate Sharpe is
  not statistically distinguishable from zero either.**

The same two checks against sr-h's **original** (pre-refinement) ensemble
result, for a second, independent data point: 8/19 positive, sign-test
`P(X >= 8 | n=19, p=0.5) = 82.04%` (also indistinguishable from chance,
in the *unfavorable* direction this time), mean Sharpe -1.347, `t =
-1.631` (also short of significance).

**Neither this project's original ensemble nor its most-refined
Configuration C would pass a revised bar that required genuine
statistical significance, not just a raw fold-count percentage.** This is
the honest, load-bearing finding of Part 2: the proposed revision below
is not a mechanism for retroactively passing Configuration C -- it is a
*more correct* statistical question that Configuration C, on the real
evidence, still does not answer affirmatively. It would, however, no
longer reject Configuration C's *specific* fold-count profile (11/19,
57.9%) purely for falling short of a mathematically near-unreachable
literal 100%, while still correctly rejecting it on the grounds that
actually matter (no evidence yet of a real, non-chance edge). One
reassurance on the i.i.d. caveat above, specific to this pair of numbers:
both `t` values (0.0274 for Configuration C, -1.631 for the original)
sit so far from the ~2.10 significance threshold that understated
variance under real serial dependence -- which would only *widen*, never
narrow, the true standard error -- can only push both further from
significance, not accidentally past it. The "not yet statistically
distinguishable from zero" conclusion for both is robust to this
specific caveat, even though the exact numeric `t`/p-values above are
not.

### Proposed revision (for human approval -- NOT applied to CLAUDE.md)

Replace the single clause "Positive annualized Sharpe in every fold (not
just on average)" with **two clauses, both required**:

1. **Fold consistency**: at least **80-90%** of folds show positive
   Sharpe (exact value within this range left for the human to pin, same
   convention CLAUDE.md already uses for its other ranged thresholds --
   drawdown 20-25%, profit factor 1.3-1.5 -- rather than a single point
   value this task would be overstepping to choose unilaterally). At
   this project's current 19-fold walk-forward, an "at least X%" floor
   maps to a minimum passing fold count of `ceil(X% * 19)`: 80% -> >=16,
   85% -> >=17, 90% -> >=18. Computed exactly (`math.comb`, correcting an
   earlier draft of this section that mistakenly reused the `P(>=11/19)`
   figure from the reference table above instead of computing power at
   the actual candidate thresholds):

   | candidate floor | min fold count | power at true `p=0.80` | power at true `p=0.90` | i.i.d. `p=0.50` sign-null reference rate |
   |---|---|---|---|---|
   | >=80% | 16/19 | 45.51% | 88.50% | 0.221% |
   | >=85% | 17/19 | 23.69% | 70.54% | 0.036% |
   | >=90% | 18/19 | 8.29% | 42.03% | 0.004% |

   The rightmost column is deliberately labeled a *sign-null reference
   rate*, not a "false-positive rate" outright: it is the exact
   probability of clearing that floor **only** under the idealized
   assumption of 19 independent folds each with exactly a 50% chance of
   a positive Sharpe -- not a general claim about how often a no-edge
   strategy would clear it in practice. A real strategy's actual
   false-positive behavior can differ from this idealized number for
   reasons this simple model doesn't capture: skewed per-fold Sharpe
   distributions, materially different trade counts across folds
   (a fold with 5 trades and a fold with 20 trades don't carry the same
   evidentiary weight, but the binomial model treats every fold's
   positive/negative outcome as one equally-weighted coin flip), and the
   same serial-dependence concern raised immediately below. Treat this
   column as a clean, worked reference point for reasoning about the
   *shape* of the tradeoff, not a literal, calibrated estimate of this
   strategy's real false-positive rate.

   **Read honestly, this table complicates the case for picking the
   *high* end of the 80-90% range**, not just the low end: at 19 folds,
   even the loosest candidate floor (>=16/19) only gives a genuinely
   strong 80%-true-edge strategy a **45.5% chance** of actually clearing
   it in one real sample -- barely better than a coin flip for detecting
   a real, strong edge -- and power drops further at the stricter
   end of the range (8.3% at >=18/19). What every one of these
   candidate floors *does* deliver, even at the loose end, is a sharply
   low false-positive rate against a no-edge strategy (0.22% down to
   0.004%) -- a dramatically better separation than literal 100%
   provides in the other direction (100% still lets a strong 90%-true-
   edge strategy through 13.5% of the time -- see the reference table
   above -- while itself demanding near-impossible ~95%+ reliability).
   **The practical implication**: at this project's current, still-thin
   19-fold depth, no single percentage floor in this range is
   simultaneously high-power *and* low-false-positive -- this is exactly
   why clause 2 (an aggregate significance check using the full
   continuous Sharpe values, not just a binarized win/loss count, which
   carries more statistical information per fold) is proposed as a
   **required second check**, not an optional add-on. Given the low
   power even at the loose end, this task's own lean (not a hard
   recommendation) is toward the **lower** end of the 80-90% range (a
   >=16/19-style floor) specifically so the percentage clause doesn't
   become the dominant, power-starved bottleneck -- leaving clause 2 to
   do the real statistical work of confirming genuine edge. The
   fold-count credibility floor itself (8-10 minimum, unchanged by this
   proposal) also directly bears on this: more real folds (as data depth
   grows) would tighten every number in this table in the proposal's
   favor, independent of anything this task can do today.
2. **Aggregate significance**: the full set of per-fold Sharpe ratios
   must reject the null hypothesis of "no real edge" via **both** (a) a
   binomial sign test on the fold win/loss count against `p=0.5` and (b)
   a significance check on the mean fold Sharpe against zero (a plain
   one-sample t-test as the immediately implementable stdlib-only
   version -- consistent with this project's existing "no numpy/pandas/
   scipy until actually needed" discipline, CLAUDE.md's data-pipeline
   section; the Probabilistic Sharpe Ratio, source #5 above, is the
   more statistically correct upgrade once/if this criterion is actually
   adopted and implemented, deferred the same way CSCV/PBO was deferred
   in sr-g -- assessed and named, not built speculatively), both at
   conventional `p < 0.05` (one-sided: "better than chance," not merely
   "different from chance in either direction"). **Both checks required,
   not either**: they test different failure modes -- the sign test
   catches "wins no more often than a coin flip"; the Sharpe-significance
   test catches "wins slightly more often than a coin flip, but the
   aggregate risk-adjusted return is still noise" (e.g. many small wins
   erased by a few disproportionate losses) -- a case a fold-count
   percentage alone, even at 80-90%, would not by itself rule out.

All other Eligibility Bar criteria (fold-count credibility floor,
drawdown ceiling, minimum trade count, profit-factor floor) are
**unchanged** by this proposal -- only the "positive Sharpe every fold"
clause is addressed.

**Known open cost of this proposal, disclosed rather than glossed over**:
implementing clause 2(b)'s exact p-value requires either a t-distribution
CDF (not in Python's stdlib `statistics` module -- would need `scipy` or
a hand-rolled incomplete-beta-function implementation, both real, new
costs this task does not resolve) or accepting a critical-value lookup
table for small, fixed degrees-of-freedom rather than an exact p-value.
Clause 2(a) (the binomial sign test) has no such gap -- `math.comb` gives
an exact result already, as demonstrated above. This asymmetry is worth
weighing when/if this proposal moves to implementation: the sign test is
immediately, exactly implementable with zero new dependencies; the
Sharpe-significance check is not, without either a new dependency or an
accepted approximation.

**This is a proposal only.** No change has been made to CLAUDE.md's
Eligibility Bar section. Per this task's explicit brief and this
project's own established precedent for Risk Parameters and the original
Eligibility Bar itself, that edit requires the human's explicit sign-off
before it takes effect.

---

## CodeRabbit review findings

Two review passes, 6 actionable findings total. **All 6 accepted and
fixed** -- none declined.

### First pass: 4 findings

- **A real arithmetic/citation error in the fold-consistency power
  calculation.** The original draft justified a `>=16/19` floor by citing
  "99.3%... the same `math.comb` method" -- but 99.334% is actually
  `P(X>=11 | n=19, p=0.80)` from the reference table above (a different
  `k`), not the power of a `>=16/19` floor, which is genuinely 45.5%
  (`P(X>=16 | n=19, p=0.80)`). Recomputed properly for `k=16/17/18`
  against `p=0.80/0.90` and the `p=0.50` false-positive rate -- see the
  corrected table in "Proposed revision" above. This changed the
  document's actual recommendation: the low power at every candidate
  floor (even the loosest, 45.5% at >=16/19) is now stated plainly, and
  the previously-unstated "which end of 80-90% is better" question is
  now answered with real numbers (leaning toward the *lower* end, not
  left unaddressed) -- a substantive correction, not just a wording fix.
- **A markdown table row broken by an unescaped literal pipe** (`|net
  move|` parsed as an extra cell boundary). Fixed to `abs(net move)` --
  same value, valid table syntax.
- **The binomial framing was presented without flagging its i.i.d.
  assumption against Part 1's own clustering finding.** A real, valid
  internal-consistency gap: Part 1 finds the negative folds cluster
  (even if only borderline-significant), which is in tension with the
  independent-trials assumption every binomial number in Part 2 relies
  on. Fixed by adding an explicit caveat paragraph naming the tension,
  explaining its direction (likely makes the simple binomial numbers
  mildly optimistic about separation power under real serial
  dependence), and naming block permutation/block bootstrap as the
  statistically correct refinement -- flagged as a known limitation for
  a future implementer, not resolved in this research-only task.
- **The Pardo "50-60% out-of-sample retention" figure was stated as if
  independently verified**, inconsistent with its own credibility-table
  entry (source #1), which already disclosed it was retrieved via search
  synthesis, not checked against Pardo's primary text. Fixed by adding
  an inline pointer back to that caveat at the point the figure is
  actually used, rather than only in the table where a reader might miss
  it.

### Second pass (after pushing the first pass's fixes): 2 more findings, both accepted

- **The fold-consistency table's rightmost column, labeled "false-
  positive rate at p=0.50 (no edge)," overstated what that number
  actually is.** A real, valid precision gap: it's the exact probability
  under an *idealized i.i.d. sign-null* model specifically, not a general
  claim about a real strategy's practical false-positive rate (which
  skew, uneven per-fold trade counts, and serial dependence could all
  push away from this clean number). Fixed: renamed the column to
  "i.i.d. `p=0.50` sign-null reference rate" and added a paragraph
  immediately below the table stating explicitly what the column is and
  is not a claim about.
- **The serial-correlation/i.i.d. caveat, added in the first pass, was
  scoped only to the binomial fold-consistency numbers -- not extended to
  the one-sample t-test or to the section concluding "Configuration C
  doesn't pass either check."** A real, valid consistency gap: a t-test's
  standard-error formula makes the exact same independence assumption the
  binomial calculations do. Fixed: extended the caveat to the t-test
  explicitly (real serial dependence would understate its true standard
  error, making the reported significance level directionally optimistic
  the same way the binomial numbers are), and added one further, specific
  point that *is* robust to this caveat: both real t-values (0.0274 for
  Configuration C, -1.631 for the original) sit far enough from the
  ~2.10 significance threshold that a wider true standard error under
  dependence can only push them further from significance, never
  accidentally past it -- so the qualitative "not yet statistically
  distinguishable from zero" conclusion holds regardless of the i.i.d.
  caveat, even though the exact numeric t/p-values do not.

## Process notes

### Diagnostic tooling: all throwaway, none kept

Three ad hoc scripts (`python/_task_j_diagnosis.py`,
`_task_j_diagnosis2.py`, `_task_j_diagnosis3.py`) were written, run, and
their output transcribed into this document, then deleted -- same
"written once, run once, results transcribed, then deleted, never
committed" convention every prior real-data task in this project has
used (sr-h, sr-i). None were committed; `git status` is clean of them.
None used TDD discipline (per this task's own brief, throwaway diagnostic
tooling doesn't require it) -- they are pure read/compute scripts against
already-tested production code (`run_walk_forward`,
`reconstruct_trades`, `AverageDirectionalIndex`), not new production
logic.

### A real, disclosed inefficiency in how this diagnosis was run

Because the investigation was split across three incremental scripts
(basic per-fold table -> choppiness/ADX/win-rate analysis ->
risk:reward-candidate/robustness extraction) rather than one single
instrumented pass, `run_walk_forward` was called **four times** in total
against the identical, deterministic Configuration C setup (the three
scripts above, plus one more standalone invocation to compute the
t-test), each logging its own real `backtest_run` entries to
`runs/experiments.jsonl` per this project's unconditional-logging design.
Re-running an already-deterministic configuration doesn't change any
conclusion (results were byte-identical across all four calls, confirmed
by the matching aggregate numbers), but it did add real, avoidable weight
to `strategy_id="ensemble-momentum"`'s own MinBTL-style combination
count: `research.overfitting_check.check_combination_count
("ensemble-momentum")`, re-run after this task, now reports
`total_combinations_tried=224` (up from sr-i's own final 109),
`combinations_per_year=122.1` (up from 59.4), `risk_level` remaining
`"high"` (already high before this task). Disclosed here plainly, same
as sr-i's own equivalent disclosure -- and flagged as a concrete lesson
for a future diagnostic task: consolidate into one instrumented
`run_walk_forward` call (capturing per-fold trades, ADX series, and
sensitivity results together) rather than several incremental re-runs of
an unchanged, deterministic configuration.

### Judgment calls resolved without asking

- **Used the same `strategy_id="ensemble-momentum"` for this diagnosis's
  real re-runs**, rather than a separate diagnostic-only `strategy_id`.
  This is a literal reproduction of an already-tried configuration (not a
  new candidate), so attributing it to the same strategy's own audit
  trail is more honest than inventing a separate identity to keep the
  "real" strategy's MinBTL count artificially lower -- consistent with
  CLAUDE.md's "every backtest run... must be logged" rule taken
  literally, at the real, disclosed cost described above.
- **Reported the runs-test result at its actual borderline p-value
  (~0.057 one-sided) rather than rounding it into either "significant"
  or "not significant."** The task explicitly warned against manufacturing
  a narrative either toward "definitely fixable" or "definitely noise" --
  an honest borderline number is the correct output here, not a forced
  binary call.
- **Did not attempt to build or test a trade-level follow-through-rate
  indicator**, even though Part 1's diagnosis identifies it as the most
  plausible concrete next step. Per this task's explicit brief ("leave
  implementing it to a future task"), naming it precisely is as far as
  this task goes.
- **Chose not to pin an exact percentage within the proposed 80-90% fold-
  consistency range.** Matches CLAUDE.md's own established convention of
  presenting ranges for exactly this kind of threshold (drawdown,
  profit factor) and leaving the specific value inside that range to the
  human's sign-off, rather than this task unilaterally deciding it.
- **Presented the Probabilistic Sharpe Ratio (source #5) as the
  recommended future upgrade rather than implementing it now**, mirroring
  sr-g's own explicit "assessed, not built" treatment of full CSCV/PBO --
  the same "don't build speculatively ahead of an actual adoption
  decision" discipline, now applied to a second, related piece of the
  same Bailey/López de Prado toolkit.

## Deliberately out of scope

- **Any new strategy-tuning code responding to Part 1's diagnosis.**
  Explicitly excluded by this task's own brief -- diagnosis and research
  only.
- **Editing CLAUDE.md's Eligibility Bar wording.** Part 2's proposal is
  presented for the human to approve or reject, exactly like the original
  bar was -- not applied here.
- **Implementing the proposed binomial-sign-test / Sharpe-significance
  eligibility check in code.** This document is the proposal; a future
  task would implement it only after human sign-off, per this task's
  explicit brief.
- **A full Probabilistic Sharpe Ratio / Deflated Sharpe Ratio
  implementation.** Researched and named as the recommended eventual
  upgrade over a plain t-test; not built, same deferral reasoning as
  sr-g's CSCV/PBO treatment.
- **Building a new trade-level follow-through/false-breakout indicator**
  to address Part 1's identified gap in ADX's discrimination power. Named
  as the concrete next step a future task could pursue, not attempted
  here.
- **Re-running Configurations A (risk:reward grid alone) or B (ADX
  recalibration alone) for a full binomial/significance evaluation.**
  Only Configuration C (this task's actual diagnostic target, per the
  brief) and the original (already fully tabulated in sr-h, reused
  directly here) were evaluated under the proposed criterion.
