# Strategy Research Task R: the statistical close-out

## Why this task exists

This project has logged **1,839 backtest runs across 8 strategy attempts**
and promoted nothing. Three sibling tasks then established that the
*measurement apparatus*, not the strategies, was the thing most worth
fixing next:

- **`sr-p`** — an honest trial count `N`, at family and project level,
  with parameter-sensitivity probes classified out of it and the
  strategy-rename loophole closed.
- **`sr-q`** — the Probabilistic and Deflated Sharpe Ratios, the
  statistically correct way to ask "is this best-of-N result real?".
- **`sr-t`** — a `1d` data path and an inverted (early-window) holdout,
  so a future attempt has untouched data to be judged on.

This task composes `sr-p` and `sr-q` and applies them, once, to **every
distinct multi-fold run in the real experiment log**. It computes nothing
new about any strategy. It re-judges what was already measured, under a
statistic that knows how much searching produced it.

Nothing here changes any strategy, any signal, or any backtest result. No
backtest was executed; `runs/experiments.jsonl` was read only.

## The composition seam: family `N` or project `N`?

`sr-p` deliberately reported **two** defensible counts and declined to
choose ("a judgment call the heuristic shouldn't make unilaterally");
`sr-q` took `num_trials` as a plain parameter for the same reason. Choosing
is this task's job, so it is made explicitly rather than by accident.

**Decision: both are computed and printed for every row, and the
`SURVIVES` verdict is decided on the project-level `N`.**

The argument is Bailey's own definition — `N` is *"the number of trials
from which this maximum was selected"*. Within `trend-momentum` this
project compared 97 momentum variants against each other. But it also
chose momentum over mean-reversion, over the momentum/reversion blend,
over volume, over funding — **after seeing all of their results**
(`sr-k`'s and `sr-l`'s own conclusions are literally "worse than
Configuration C on every metric", which is a selection statement). For the
question this retrospective asks — *is the best thing this project found
real?* — the selection pool is the project's whole research total.

The family-level figure is kept alongside, not discarded, for two reasons:
it is the correct number for the narrower question "is this family's
winner real *within that family*", and publishing only the larger number
would hide how little of the deflation any single research thread accounts
for. The table shows both; the gap between them is itself informative (see
the `funding` rows, where family `N`=8 gives DSR 0.378 and project `N`=117
gives 1.6e-05 — the same result, judged against two different admissions
of how much was searched).

`purpose` keeps the two pools separate: an `"infrastructure"` row (a
pipeline end-to-end demo that was never a paper-trading candidate) is
judged against the infrastructure `N`=19, not the research `N`=117.

## The verdict vocabulary — fixed before any number was printed

Written down and committed to code *before* the real log was run through
it, so the labels could not be fitted to the results.

| Verdict | Definition |
|---|---|
| `SURVIVES` | `DSR >= 0.95` at the project-level `N`. |
| `INCONCLUSIVE-DATA-LIMITED` | Below the trade-count floor. The study cannot conclude in either direction. |
| `REJECTED` | `DSR < 0.95` **and** point estimate `<= 0`. No edge shown, and the point estimate points the wrong way. |
| `REJECTED-UNDERPOWERED` | `DSR < 0.95`, point estimate `> 0`, but the study's own detection floor exceeds any plausible true edge. Means **"not shown"**, explicitly **not** "shown absent". |

Two things about this vocabulary that are decisions, not transcription:

**1. The trade-count floor is checked first, ahead of every other label —
`SURVIVES` included.** "This study cannot conclude" logically precedes both
"shown" and "not shown", so a 14-trade run is recorded neither as a
rejection nor as a survivor. This is a conscious strengthening of the plain
reading "`SURVIVES` = DSR ≥ 0.95 at the project `N`": for a system that may
eventually place real money, the direction that matters is never emitting a
promotion signal off a sample too thin to support one. The cost is named
rather than hidden — a genuinely strong but legitimately low-frequency
strategy gets parked in `INCONCLUSIVE-DATA-LIMITED` — and it is exactly the
tension CLAUDE.md already flags on the 100-trade floor. The fix is the
floor, not the precedence: **Gate 1 Proposal 3** below. On the real log the
ordering changes no row (the only data-limited family's DSR is ~0 either
way).

**2. The four labels leave one cell implicit**: `DSR < 0.95`, positive
point estimate, and **adequately powered** (`detection_floor <=
plausible_max_true_sharpe`). That is a genuine rejection — searched enough,
still nothing — so it returns `REJECTED` with a `reason` string that says
which of the two rejection routes it took. It does not arise on this
project's real log (every window's floor is above any plausible edge) but
must not silently fall through. Tested explicitly.

"Any plausible true edge" is operationalized as a parameter,
`plausible_max_true_sharpe`, defaulting to **1.0 annualized**. Deliberately
generous — the credible institutional trend-following research this project
benchmarked against (`sr-g`) reports programme-level Sharpes below it — and
generosity makes `REJECTED-UNDERPOWERED` *harder* to claim, not easier.

## The honesty requirement, made structural

Every verdict row carries **the study's own detection floor**: the smallest
true annualized Sharpe that data span could have distinguished from zero at
one-sided α=0.05,

```text
floor ~= Phi^-1(1 - alpha) / sqrt(years)      (Phi^-1(0.95) = 1.6448536...)
```

| Window | Span | Detection floor |
|---|---|---|
| 1h research window (16,078 bars) | 1.8353 y | **1.2142** |
| 15m window (19,870 bars) | 0.5670 y | **2.1843** |
| 1h *validated* span only (13,680 bars) | 1.5616 y | 1.3162 |

Without that column, "DSR ≈ 2e-05" reads as a far stronger claim than the
evidence supports. It means **indistinguishable from best-of-N luck**. It
does **not** mean **proven to lose money**. Every strategy in this table was
measured on a window whose detection floor is 1.21–2.18 annualized Sharpe;
a real edge of, say, 0.4 would have been invisible here regardless of how
carefully it was searched for.

This is enforced in code rather than by convention:
`retrospective.render_markdown_table` emits the floor as a fixed column
immediately before the verdict, there is no parameter that omits it,
`VerdictRow.to_dict()` always includes it, `VerdictResult` carries the floor
on the same frozen object as the verdict string, and a test asserts every
rendered row has a non-empty floor cell. A reader cannot obtain a verdict
from this module without the floor it was reached under.

**Independent cross-check of the floor arithmetic**: the validated-span
figure computed here from `z/sqrt(years)` is **1.31624**. `sr-q` derived
the same quantity by an entirely different route — fixed-point iteration on
PSR itself at `T=13,680` under normal moments — and published **1.3164**.
Two unrelated derivations agreeing to four significant figures.

## What the real log actually contains

All figures re-derived directly from the untouched
`/mnt/c/Dev/trading-engine/runs/experiments.jsonl` (read-only).

| Quantity | Real value | Brief's estimate |
|---|---|---|
| records | 1,840 (1,839 `backtest_run` + 1 `holdout_access`) | 1,839 records ✓ |
| multi-fold runs | **33** | "~25" — **low** |
| distinct configurations after de-duplication | **18** | "~14" — **low** |
| re-runs collapsed away | 15 | — |
| records sharing `mean_sharpe = 0.02713372192476914` | **8** | "six" — **low** |

The brief asked to verify rather than trust these, and all three of its
figures were low. The 8-record `0.0271` cluster reproduces `sr-p`'s own
independent finding exactly ("The brief said six; the real count is
eight").

### De-duplication: fingerprint on results, not metadata

`sr-p` established that the configuration under test is **never in the
log** — it lives in the `Trainable`'s constructor arguments, which
`run_walk_forward` never sees. So `params` and `strategy_version` cannot
identify a configuration.

What can, far more sharply, is the **per-fold result vector**: two runs
agreeing on `(sharpe_ratio, num_trades, total_return)` in all 19 folds over
the same `data_range` are the same computation by any standard. The chance
of two genuinely different configurations colliding on 57 floats is nil.

This fingerprints **narrower** than `sr-p`'s `_standalone_fingerprint`,
which also keys on `strategy_version` and therefore keeps re-runs under a
new version string apart. That conservatism is right for `N` (never merge
away a trial you might have to answer for) and wrong for a report whose
unit is *one row per distinct configuration*. **Both views coexist**: `N`
still counts all 33 runs' worth of trials; this table prints 18 rows.

Two consequences worth stating plainly:

- It correctly separates `sr-o`'s fold-boundary fix (7 → 14 trades) from
  the pre-fix run, which `sr-p`'s params-based heuristic explicitly
  disclosed it *could not* see. Results-fingerprinting fixes that specific
  disclosed false positive.
- It correctly merges `sr-o`'s own *control* re-run
  (`v2-fold-boundary-seeded`, run `2efc832b`) into the pre-fix row: it
  produced a byte-identical 19-fold result vector, so it was the same
  computation under a new version string.

### The two `hourly_momentum` rows differ by exactly 2.0x — and that is real

`14a9334c` reports fold Sharpes exactly **2.0×** `5af7bcc2`'s, fold for
fold, on identical trade counts. That is `sr-f`'s documented Sharpe
annualization bug ("`_sharpe_ratio` annualized via a hardcoded" 15m factor
— 1h returns annualized as if they happened 4× more often, i.e. `sqrt(4)`
= 2× too large). The pre-fix run is genuinely superseded.

**It was not removed.** Excluding a logged result after the fact because
this analysis judges it defective is precisely the kind of post-hoc
data selection this project's methodology forbids. It is disclosed instead,
with its direction of effect: the spurious −5.70 inflates `V_hat`, which
raises `SR_0`, which *lowers* every DSR — the conservative direction.

### `V_hat`: one Sharpe per counted trial

Bailey's `V_hat` is "the variance across the **N** trials of the estimated
Sharpe", so the trial set must be the *same set* `N` counts. Constructed to
be exactly that:

- A grid group logs one record per (fold, candidate) pair and `sr-p` counts
  it once with weight `total_candidates` — so the trial unit is the
  **candidate**, and its Sharpe is the mean of that candidate's own per-fold
  Sharpes: one value per `(parent_run_id, candidate_index)`.
- A standalone multi-fold run is one trial of weight 1, Sharpe = its
  aggregate mean fold Sharpe.
- `TrialKind.SENSITIVITY_PROBE` records are excluded, exactly as from `N`.

**This reconciles exactly with `sr-p` for every family** — 97 / 8 / 8 / 4 /
19 trial Sharpes against `sr-p`'s 97 / 8 / 8 / 4 / 19 `selection_trials`.
`build_retrospective` re-checks the reconciliation at run time and records a
note if it ever breaks; on the real log it emits **no notes**.

| family | trial Sharpes | `sr-p` `N` | annualized stdev |
|---|---|---|---|
| `trend-momentum` | 97 | 97 | 1.3470 |
| `mean-reversion` | 8 | 8 | 0.6751 |
| `funding` | 8 | 8 | 0.1662 |
| `volume` | 4 | 4 | 0.6174 |
| `infrastructure` | 19 | 19 | 1.4129 |

## Units: every Sharpe here is daily

PSR/DSR are defined on per-observation Sharpe. Everything is de-annualized
to a **daily** scale, for two independent reasons:

1. `sr-q`'s: PSR assumes iid returns; per-bar returns inside a multi-bar
   holding period are autocorrelated, which inflates effective `T` and makes
   PSR anti-conservative. Daily resampling is the standard remedy and costs
   well under 1% of detection power.
2. This task's own: a family's trial Sharpes span **two timeframes**
   (`trend-momentum` covers both the 15m and 1h eras). A per-bar scale would
   put 15m and 1h trial Sharpes on different axes and make `V_hat`
   meaningless. Per-day is the same axis for both.

**`sr-q`'s verification anchor reproduced, both ways:**

| Convention | `SR_hat` | `T` | PSR(N=1) | `sr-q` published |
|---|---|---|---|---|
| per-bar | 4.17388859e-4 | 13,680 | **0.5194673** | 0.5194673 ✓ |
| daily (shipped) | 2.04477946e-3 | 570 | **0.5194509** | 0.5194509 ✓ |

The independent t-test cross-check also reproduces exactly: this module
reports `t_test_p = 0.48445841638` for Configuration C against `sr-q`'s
`0.484458`.

`bars_per_day` is not on any historical record (`sr-q` added it going
forward), so it is derived arithmetically from `data_range`:
`(end_ms - start_ms) / (num_bars - 1)` is the bar interval. On this log that
is **exact** — 3,600,000 ms and 900,000 ms with no remainder — which is why
it is preferred over `sr-q`'s noted fallback of reverse-engineering
`train_bars` (2160 ⇒ 1h, 8640 ⇒ 15m), a coincidence of two chosen window
sizes rather than a measurement.

Return moments are **not** recoverable for any historical record
(`_metrics_summary` dropped `equity_curve` until `sr-q`; verified: zero of
the 1,839 records carry one). Every row therefore falls back to the normal
assumption and says so — all 18 rows report
`moments_source = "normal_assumption"`. `sr-q` measured the cost at this
project's magnitudes: ~1e-7 on PSR.

# The verdict table

`min_fold_consistency = 0.80` (the permissive end of CLAUDE.md's approved
80–90% range; it affects only the reported pass flag, never the verdict).
`min_total_trades = 100`, `plausible_max_true_sharpe = 1.0`,
`dsr_threshold = 0.95`. Reproduce with:

```bash
cd python && uv run python -m research.retrospective \
    --runs-path ../runs/experiments.jsonl --min-fold-consistency 0.80
```

| family | strategy_id | version | run_id | folds | mean Sharpe | fold consistency | sign-test p | t-test p | PSR(N=1) | family N | project N | DSR(family N) | DSR(project N) | detection floor | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| infrastructure | task-c-e2e-verification | v1 | b61a3736 | 3 | n/a | 0.0% | 1.000 | n/a | n/a | 19 | 19 | n/a | n/a | 2.18 | INCONCLUSIVE-DATA-LIMITED |
| infrastructure | ma-crossover-task-d-e2e | v1 | 8d31ddfe | 3 | -3.9011 | 0.0% | 1.000 | 0.991 | 0.0283 | 19 | 19 | 0.000679 | 0.000679 | 2.18 | REJECTED |
| trend-momentum | regime-momentum-btc-15m | v1 | f0aefd04 | 3 | -2.2823 | 0.0% | 1.000 | 0.895 | 0.1307 | 97 | 117 | 0.00261 | 0.00289 | 2.18 | INCONCLUSIVE-DATA-LIMITED |
| trend-momentum | regime_momentum | v2-risk-managed | 535acc89 | 3 | -1.4091 | 33.3% | 0.875 | 0.862 | 0.2436 | 97 | 117 | 0.00893 | 0.00975 | 2.18 | REJECTED |
| trend-momentum | hourly_momentum | v1 | 14a9334c | 19 | -5.7009 | 21.1% | 0.998 | 0.997 | 0.0000 | 97 | 117 | 0 | 0 | 1.21 | REJECTED |
| trend-momentum | hourly_momentum | v1 | 5af7bcc2 | 19 | -2.8505 | 21.1% | 0.998 | 0.997 | 0.0002 | 97 | 117 | 4.44e-15 | 8.49e-15 | 1.21 | REJECTED |
| trend-momentum | single-lookback-momentum | v1 | fee7e17c | 19 | -2.1003 | 15.8% | 1.000 | 0.997 | 0.0045 | 97 | 117 | 3.97e-12 | 7.04e-12 | 1.21 | REJECTED |
| trend-momentum | ensemble-momentum | v1 | f055735a | 19 | -1.3469 | 42.1% | 0.820 | 0.940 | 0.0465 | 97 | 117 | 1.69e-09 | 2.78e-09 | 1.21 | REJECTED |
| trend-momentum | ensemble-momentum | v1 | 350f00bb | 19 | -0.6274 | 47.4% | 0.676 | 0.724 | 0.2168 | 97 | 117 | 2.58e-07 | 3.96e-07 | 1.21 | REJECTED |
| trend-momentum | ensemble-momentum | v2 | c1569b01 | 19 | -1.0110 | 36.8% | 0.916 | 0.898 | 0.1036 | 97 | 117 | 1.94e-08 | 3.09e-08 | 1.21 | REJECTED |
| trend-momentum | ensemble-momentum | v2 | e75c91e2 | 19 | +0.0271 | 57.9% | 0.324 | 0.489 | 0.5135 | 97 | 117 | 1.31e-05 | 1.88e-05 | 1.21 | REJECTED-UNDERPOWERED |
| mean-reversion | mean-reversion | v1 | cdf9ff71 | 19 | -1.8589 | 26.3% | 0.990 | 0.986 | 0.0103 | 8 | 117 | 0.000198 | 5.21e-11 | 1.21 | REJECTED |
| mean-reversion | momentum-reversion-blend | v1 | 7c6e6c22 | 19 | -1.3991 | 36.8% | 0.916 | 0.987 | 0.0405 | 8 | 117 | 0.00148 | 1.88e-09 | 1.21 | REJECTED |
| mean-reversion | mean-reversion | v1 | 2d88a4e9 | 19 | -1.7699 | 31.6% | 0.968 | 0.981 | 0.0137 | 8 | 117 | 0.000299 | 1.07e-10 | 1.21 | REJECTED |
| volume | obv-trend | v1 | a61cadd6 | 19 | -2.8548 | 21.1% | 0.998 | 0.996 | 0.0002 | 4 | 117 | 6.77e-06 | 8.16e-15 | 1.21 | REJECTED |
| trend-momentum | ensemble-momentum-configuration-c | task-n-with-funding | 0fbc11cc | 19 | +0.0391 | 57.9% | 0.324 | 0.484 | 0.5195 | 97 | 117 | 1.4e-05 | 2.01e-05 | 1.21 | REJECTED-UNDERPOWERED |
| funding | funding-extremity-contrarian | v1 | 393c5acc | 19 | -0.0054 | 15.8% | 1.000 | 0.501 | 0.4973 | 8 | 117 | 0.378 | 1.58e-05 | 1.21 | INCONCLUSIVE-DATA-LIMITED |
| funding | funding-extremity-contrarian | v2-fold-boundary-seeded | b14ee1ea | 19 | -0.4839 | 21.1% | 0.998 | 0.594 | 0.2729 | 8 | 117 | 0.182 | 9.75e-07 | 1.21 | INCONCLUSIVE-DATA-LIMITED |

**Tally: 12 `REJECTED`, 2 `REJECTED-UNDERPOWERED`, 4
`INCONCLUSIVE-DATA-LIMITED`, 0 `SURVIVES`.**

## What this actually concludes

**Nothing survives.** Not one of the 18 distinct configurations this
project has ever run reaches DSR ≥ 0.95, at either level of `N`. The best
result in the project's history — Configuration C with real funding P&L,
mean annualized Sharpe **+0.0391** — lands at **DSR = 2.01e-05** at the
project `N` of 117, and **1.40e-05** at the family `N` of 97.

But the more important sentence is the one the detection-floor column
exists to force:

> **Configuration C's DSR of 2e-05 does not mean the strategy loses money.
> It means a strategy that good is indistinguishable from the best of 117
> coin flips — on a window that could not have detected a real edge of
> 1.21 annualized Sharpe or below in the first place.**

Three arithmetic facts make the point concretely:

- To reach `DSR = 0.95` at the project `N`, Configuration C would need an
  annualized Sharpe of **4.645** — about **119×** its actual +0.039, and
  **3.8×** the window's own detection floor. That target is not a
  reasonable ask of a real strategy; it is an artefact of having searched
  117 times against 1.8 years of one symbol.
- Its undeflated PSR is **0.5195** — i.e. "very slightly better than a coin
  flip that the true Sharpe exceeds zero", which is the honest reading of
  +0.039 on this much data, before any correction for search at all.
- Its own pre-existing t-test agrees: `p = 0.484`. The three statistics
  (t-test, PSR, DSR) tell the same story with increasing rigour, and none
  of them was the thing standing between this project and a validated
  strategy. **The window was.**

The four `INCONCLUSIVE-DATA-LIMITED` rows are equally worth stating
plainly: `task-c-e2e-verification` (0 trades) and
`regime-momentum-btc-15m` (16 trades) are early infrastructure/first-attempt
runs, and both funding-extremity rows (7 and 14 trades) sit far below the
100-trade floor. **These are not negative findings.** `sr-o` already said
so about the 14-trade result, and this analysis does not upgrade it to a
rejection.

### Honest discrepancy against the brief's expectation

The brief expected DSR ≈ **8e-7** at family `N`=97; this implementation
produces **1.40e-05**, ~17× higher. Investigated rather than forced into
agreement: the entire difference is the **`V_hat` trial-set choice**, and it
spans three orders of magnitude.

| `V_hat` trial set | n | annualized stdev | `SR_0` | DSR at N=97 |
|---|---|---|---|---|
| **A (shipped)** one Sharpe per counted trial | 97 | 1.3470 | 0.17767 | **1.40e-05** |
| B distinct multi-fold results only | 10 | 1.6887 | 0.22273 | 7.04e-08 |
| C all multi-fold runs incl. reproductions | 19 | 1.4902 | 0.19655 | 1.75e-06 |

The brief's 8e-7 sits between B and C, consistent with a `V_hat` taken over
a multi-fold-run set. **A is shipped because it is the only one that is
self-consistent**: it uses the *same* trial set `N` counts, which is what
`V_hat` means in Bailey's formula, and it reconciles exactly with `sr-p`'s
count for all five families. B and C claim `N`=97 while measuring the
dispersion of a 10- or 19-element set — a different pool from the one being
deflated against.

Worth noting which way the choice cuts: **A is the most permissive of the
three** (highest DSR by 1–3 orders of magnitude). The shipped number is not
the most damning one available; it is the most defensible one, and it is
still five orders of magnitude below the bar. **No verdict changes under
any of the three.**

---

# Gate 1 proposals — PROPOSED, NOT APPLIED

CLAUDE.md's Backtest/Walk-Forward Eligibility Bar is **human-approval-gated,
same status as Risk Parameters**. CLAUDE.md says so explicitly, `sr-q`
deferred to it, and this task does too. **Nothing below has been applied.**
No file in this PR edits the Eligibility Bar's wording. Each proposal is
written in the form it would take if adopted, so approval is a copy-paste
rather than a re-derivation.

---

## Proposal 1 — replace the mean-Sharpe t-test with the Deflated Sharpe Ratio

**Status: PROPOSED. Requires @ckrhehfl's approval.**

**Current wording** (Eligibility Bar, clause 2): the fold Sharpes must
reject "no real edge" via *both* a binomial sign test *and* "a significance
check on the mean fold Sharpe against zero (one-sample t-test as the
immediately implementable stdlib-only version; the more rigorous
Probabilistic Sharpe Ratio upgrade is assessed and deferred…)".

**Proposed replacement for the second half of clause 2:**

> …*and* a **Deflated Sharpe Ratio** (Bailey & López de Prado 2014,
> `research/eligibility.py::evaluate_deflated_sharpe`) of at least **0.95**,
> computed on daily-resampled returns, against the **project-level**
> selection-trial count `N` from
> `research/overfitting_check.py::check_project_combination_count`
> (`research_selection_trials`) and the variance of that same trial set's
> Sharpe estimates. The one-sample t-test it replaces may still be reported
> for continuity but is no longer a pass criterion.
>
> The project-level `N`, not the family-level one, because strategy
> families in this project were compared against each other after their
> results were known — see `.planning/sr-r-retrospective-closeout.md`.
>
> `sr-j`'s **disclosed open cost** — "the t-test's exact p-value needs
> either `scipy` or an accepted approximation" — is now closed twice over:
> `sr-k` implemented the exact t-distribution p-value via the regularized
> incomplete beta function, and DSR needs only `statistics.NormalDist`.
> Both are stdlib-only.

**Why.** The t-test asks "is this mean fold Sharpe distinguishable from
zero?" and has *no notion whatever of how much searching produced it*. With
117 logged research trials against 1.8 years of a single symbol, that is
the wrong question. DSR is the standard, published correction and is now
implemented and verified (`sr-q` reproduced its anchor to 4 decimals
against an independent method). This is not a tightening for its own sake —
it is the difference between a statistic that could have been passed by
searching hard enough and one that cannot.

**What it would have changed retrospectively: nothing.** Every run already
fails the t-test too. The proposal buys correctness for future runs, not a
different verdict on past ones — which is the right time to adopt it,
before it can be accused of being fitted to a result.

**Disclosed cost of adopting it.** DSR requires an `N`, and `N` requires
`research/lineage.py`'s curated family map to stay current. A new strategy
family run without a `strategy_family=` argument and without a curated
entry resolves to its own single-member family, which would understate
`N`. `resolve_family` surfaces this as a visible note rather than silently,
but adopting DSR as a gate makes keeping that map honest a real
obligation rather than a diagnostic nicety.

---

## Proposal 2 — a single-window Bar variant for holdout confirmation runs

**Status: PROPOSED. Requires @ckrhehfl's approval.**

**Current wording**: "The holdout confirmation run must clear the same bar
(single-window version) and must be the only holdout access on record for
that `strategy_id`."

"(single-window version)" is undefined. Fold-based criteria — fold
consistency, and the binomial sign test over folds — have no meaning on a
single window.

**Proposed replacement:**

> **Holdout confirmation (single-window variant).** A holdout run is a
> single evaluation window, so the fold-based clauses do not apply and are
> not to be simulated by chopping the holdout into pseudo-folds. The
> holdout run must instead clear:
>
> 1. **PSR ≥ 0.95** (`evaluate_psr` against a zero benchmark) on
>    daily-resampled holdout returns, using measured return moments.
>    Deliberately **PSR, not DSR**: the holdout was never searched over —
>    one access, one run, on data no decision has touched — so there is no
>    selection bias to deflate, and `N`=1 makes DSR identical to PSR
>    anyway.
> 2. The **non-fold criteria unchanged**: max drawdown ≤ 20–25%, the trade
>    count floor in force at the time, and the profit-factor floor of
>    1.3–1.5.
> 3. The run's observed Sharpe must exceed the **holdout window's own
>    detection floor** (`retrospective.detection_floor_sharpe`), stated
>    explicitly in the confirmation report. If it does not, the holdout is
>    reported as **not powered to confirm**, and clearing the other
>    criteria does not constitute confirmation.
>
> **Explicitly NOT required: any fold-count, fold-consistency, or sign-test
> criterion.**

**Why the fold criteria must be dropped rather than scaled down.** This is
the concrete arithmetic, and it is the same error `sr-j` already identified
and corrected once:

> At n=5 folds the **only** sign-test outcome clearing α=0.05 is a literal
> **5/5 sweep** (p = 0.03125). 4/5 gives p = 0.1875 — not close. So
> applying the fold-based bar to a 5-window holdout would demand a literal
> 100% sweep: **stricter than the 19-fold bar** (which `sr-j` set at 80–90%
> precisely because demanding literal 100% "mostly measures luck, not
> edge"), and an exact reproduction of the error `sr-j` was written to fix.

**Detection floors to expect**, so a future confirmation run is not
surprised by them: the 1h trailing holdout is **~2.57** annualized Sharpe
(`sr-q`), the 1d early-window holdout **~0.96** over ~2.95 years (`sr-t`).
The 1d holdout is by a wide margin this project's best-powered untouched
window — the only one whose floor sits below a plausible real edge.

---

## Proposal 3 — a frequency-appropriate trade-count floor

**Status: PROPOSED. Requires @ckrhehfl's approval.**

**Current wording**: "minimum 100 total trades across all folds (flagged
tension: may unfairly penalize a legitimately low-frequency strategy —
apply judgment, don't treat as absolute)".

CLAUDE.md already flags this as possibly unfair. This proposal resolves it
concretely and **in advance of any daily run**, rather than after one fails
on a technicality — which is the only time such a change can be made
without it being tuning-after-the-fact.

**Proposed replacement:**

> **Minimum trade count, scaled to the strategy's own frequency.** The
> floor is
> `max(30, min(100, floor(total_evaluated_bars / bars_per_day / 20)))` —
> i.e. roughly one trade per 20 evaluated days, clamped to `[30, 100]`.
> Concretely, at the three geometries this project actually uses:
>
> | Timeframe | Evaluated bars | Evaluated days | days ÷ 20 | Floor after clamp |
> |---|---|---|---|---|
> | 1h, 19 folds × 720 | 13,680 | 570 | 28 | **30** |
> | 15m, 3 folds × 2,880 | 8,640 | 90 | 4 | **30** |
> | 1d, 822 research bars (`sr-t`) | 822 | 822 | 41 | **41** |
>
> The **absolute floor of 30** is the binding constraint at every timeframe
> this project actually uses, so in practice this proposal reads: **the
> floor becomes 30, not 100**, and the 100 cap only re-engages for a
> strategy trading far more often than any attempted so far.
>
> A run below the floor is reported `INCONCLUSIVE-DATA-LIMITED` — neither a
> pass nor a fail. It is not evidence against the strategy and must not be
> written up as such.

**Why 30, and why it is not a weakening.** 30 is not folklore borrowed from
"n=30 for the CLT" (`sr-g` correctly demolished the *"30 trades per
parameter"* rule as having no rigorous origin — note that is a different
claim, about *per-parameter* counts). It is the point at which a sign test
over trade outcomes has any power at all, and below which per-trade
statistics are dominated by their own estimation noise. The real
justification for choosing it here is empirical and specific: **a daily
strategy over `sr-t`'s 822-bar 1d research window yields roughly 30–60
trades**, so a 100-trade floor would reject every possible daily strategy
*on frequency alone, before looking at its returns* — a criterion that
cannot be satisfied is not a criterion.

**What it would have changed retrospectively.** Two rows move from
`INCONCLUSIVE-DATA-LIMITED` to a real verdict — but *not* the ones that
matter: `regime-momentum-btc-15m` (16 trades) and both funding-extremity
rows (7 and 14) all stay below 30. Only `task-c-e2e-verification` (0
trades) is unaffected either way. **The funding-extremity result remains
genuinely inconclusive under the proposed floor as well**, which is worth
stating because it removes any suspicion that this proposal was reverse-
engineered to resolve that specific open question. It does not.

---

## Proposal 4 — a falsifiable, script-checkable CSCV/PBO trigger

**Status: PROPOSED. Requires @ckrhehfl's approval. Recommendation:
continue to defer, but on a checkable condition.**

**Current wording** (`sr-g`, Finding 3): revisit CSCV/PBO at "CLAUDE.md's
Implementation Priority #9 (auto-retraining pipeline)… the point where this
project's actual hyperparameter-search scale grows enough".

That is not falsifiable. "Enough" is unmeasured, and Priority #9 is a
milestone whose arrival says nothing about search scale.

**Proposed replacement:**

> **CSCV / PBO revisit trigger (checkable, not a milestone).** Implement
> Combinatorially Symmetric Cross-Validation and the Probability of Backtest
> Overfitting when **both** hold:
>
> **(a)** one research family has evaluated **≥ 50 candidates over one
> common fold geometry**, with **per-candidate equity curves retained**; and
>
> **(b)** that family's winner reaches **DSR ≥ 0.90 at the family-level
> `N`**.
>
> Both are computable from `runs/experiments.jsonl` by a script; neither
> requires a judgment call. (b) exists so CSCV is spent on a candidate that
> has already survived the cheaper test — PBO answers "is this winner's
> out-of-sample rank better than median?", which is only an interesting
> question about something that already looks good.

**Why continue to defer — three concrete, current reasons, not a vibe:**

1. **It cannot run on the existing log at all.** CSCV needs per-candidate
   *equity curves* across combinatorial splits. `walkforward._metrics_summary`
   discarded `equity_curve` before logging until `sr-q`. Verified directly:
   **zero** of the 1,839 records carry one. This is not "would be hard" —
   it is arithmetically impossible without re-running every backtest.
2. **The historical trials are not commensurable.** They span **2
   timeframes** (15m and 1h), **4 distinct `walk_forward_config` shapes**,
   and **5 lineage families**. CSCV's splitting assumes one common
   evaluation geometry across the candidate set; pooling these would measure
   the pooling, not overfitting. And the largest single grid this project
   has *ever* run is **6 candidates** — against the ≥50 condition (a),
   that is not a near miss.
3. **No decision would change.** At DSR ≈ 2e-05 (project `N`) — or 7e-08
   under the most conservative `V_hat` choice — every configuration is
   already rejected by five to eight orders of magnitude. PBO would be
   spending real implementation effort to re-reject things.

---

## Proposal 5 — rewrite CLAUDE.md's "Strategy Attempts So Far"

**Status: PROPOSED. Requires @ckrhehfl's approval.** The current section is
~130 lines that accumulated one task at a time and now leads with a "Current
best result" that this retrospective shows is not distinguishable from
noise. Proposed replacement below, intended to substitute for the whole
section from its heading through the "Neither funding-rate avenue clears the
bar" paragraph.

> ### Strategy Attempts So Far (closed out 2026-07-29)
>
> Eight strategy attempts across **four research families** (plus
> infrastructure demos) were built and walk-forward validated against real
> BingX data in Tasks E–L and N–O: naive SMA crossover, ATR-risk-managed
> crossover (15m and 1h), a multi-lookback ensemble with ADX regime
> weighting and volatility targeting (refined into "Configuration C"),
> regime-gated mean-reversion, a momentum/mean-reversion blend, an
> on-balance-volume trend strategy, and a funding-rate-extremity contrarian
> strategy. Per-task detail and honest negative findings:
> `.planning/sr-e-*.md` through `.planning/sr-o-*.md`.
>
> **`sr-r` closed this line of research out statistically.** Every distinct
> multi-fold run in the log — **18 configurations de-duplicated from 33
> runs** — was re-judged under `sr-p`'s honest trial count and `sr-q`'s
> Deflated Sharpe Ratio. Full table:
> `.planning/sr-r-retrospective-closeout.md`.
>
> **Result: nothing survives. 0 of 18.** The best result in the project's
> history (Configuration C with funding P&L, mean annualized Sharpe
> **+0.039**) reaches **DSR = 2.0e-05** against the project's **117**
> research selection trials — indistinguishable from the best of 117 coin
> flips. Twelve configurations are `REJECTED`, two
> `REJECTED-UNDERPOWERED`, four `INCONCLUSIVE-DATA-LIMITED` (below the
> trade-count floor: both funding-extremity runs at 7 and 14 trades, and
> two early runs).
>
> **The single most important finding is about the window, not the
> strategies.** The 1h research window's own **detection floor is ~1.21
> annualized Sharpe** (one-sided α=0.05 over 1.84 years); the 15m window's
> is **~2.18**. A real edge of 0.4–0.8 Sharpe — the range credible
> institutional trend-following actually reports — **could not have been
> detected here by any strategy, however well specified**. So "DSR ≈ 0"
> across the board means **not shown**, and emphatically **not shown
> absent**. Configuration C would have needed an annualized Sharpe of
> **4.6** to clear DSR 0.95 at this `N` — a target that says more about
> having searched 117 times against 1.8 years of one symbol than about any
> strategy.
>
> **Consequence: the 1h research window is spent** (see the standing rule
> below). Further searching on it cannot produce a defensible result,
> because every additional trial raises the `N` that any future winner must
> be deflated against. The live options are therefore about *changing the
> evidence base*, not the signal:
>
> 1. **The `1d` early-window holdout (`sr-t`).** ~2.95 years of data no
>    trial in this project's history has touched, with a detection floor of
>    **~0.96** — the only untouched window this project has whose floor sits
>    below a plausible real edge. A strategy specification must be committed
>    *before* that window is ever loaded.
> 2. **Multi-symbol expansion.** A meaningful share of the Sharpe reported
>    by the institutional research benchmarked in `sr-g` plausibly comes
>    from cross-symbol diversification a single-symbol design cannot access.
>    A real architecture reconsideration (it touches the data pipeline's
>    survivorship-bias handling, per Strategy Research Methodology) that
>    deserves its own `Discuss` pass.
> 3. **Stop adding strategies and build the infrastructure instead**
>    (Priorities #8–#10). Nothing about the paper-trading loop, supervision,
>    or `ExchangeAdapter` work is blocked by the absence of a validated
>    strategy — CLAUDE.md already says they can and should proceed on
>    dummy signals.
>
> **Retired**: the two funding-extremity follow-ups previously listed here
> as live candidates (changing the edge-trigger rule; lowering
> `entry_z_threshold`/`funding_zscore_lookback`). Both are more searching on
> the spent 1h window — see the standing rule below.

---

## Proposal 6 — a standing rule: the 1h research window is spent

**Status: PROPOSED. Requires @ckrhehfl's approval.** Proposed as a new
bullet under **Strategy Research Methodology**'s "Non-negotiable once
strategy research begins" list.

> - **No further parameter searching on the `BTC-USDT` 1h research window
>   (2024-04-27T10:00Z → 2026-02-26T07:00Z, 16,078 bars).** 117 research
>   selection trials
>   have been run against it and its detection floor is ~1.21 annualized
>   Sharpe (`sr-r`). Every additional trial raises the `N` any future winner
>   must be deflated against while adding no new evidence, so further search
>   there is *strictly* value-destroying: it can only lower a future DSR,
>   never raise one. This window remains valid for **reproducing** a
>   previously logged result, for **diagnosing** a mechanism (as `sr-o`
>   did), and for **infrastructure** testing — none of which select a
>   configuration. It is closed to *selection*.
>
>   New strategy work goes to a window with usable statistical power: the
>   `1d` path and its early-window holdout (`sr-t`), or a multi-symbol
>   universe (which needs its own `Discuss` pass first, per the
>   survivorship-bias clause above).

**What this explicitly retires**, named so the decision is not silently
reversed later by a reader of the old text:

- **The funding-extremity edge-trigger rule change** (fire on any crossing
  into extreme, rather than requiring a flip to the opposite extreme).
- **Lowering `entry_z_threshold` / `funding_zscore_lookback`** to generate
  more trades.

CLAUDE.md currently frames both as "genuinely new configurations, not
tuning" — and that framing was *correct on its own terms*: neither has been
run, so neither is a retry. **The reason to retire them is different and
does not contradict it.** It is not that they would be tuning; it is that
the window they would be run on can no longer support a conclusion in
either direction. Running them would produce a 19-fold result deflated
against `N` = 118 or 119, on a window with a 1.21 detection floor, and this
document would say exactly the same thing about the outcome.

If the funding-extremity trigger design is still believed in, the honest
way to test it is on the `1d` window under Proposal 2's holdout protocol,
with the specification committed first.

---

# What was built

`python/research/retrospective.py` (new, ~700 lines including docstrings).
Composes `research/lineage.py`, `research/overfitting_check.py`, and
`research/eligibility.py`; adds no new dependency (stdlib `argparse`,
`json`, `math`, `statistics`, `dataclasses`, `decimal`, `pathlib`).

| Name | Purpose |
|---|---|
| `detection_floor_sharpe(years, *, alpha)` | `Phi^-1(1-alpha)/sqrt(years)`; `None` for a non-positive span |
| `infer_bars_per_day(record)` | logged value, else exact derivation from `data_range`; `None` rather than a guess |
| `distinct_multi_fold_runs(records)` | de-duplication on the per-fold result vector; first-wins, duplicates named |
| `trial_sharpe_ratios(records)` | one Sharpe per counted selection trial, per family — `V_hat`'s input |
| `assign_verdict(...)` / `VerdictResult` | the four labels; carries the detection floor on the same frozen object |
| `build_retrospective(...)` / `VerdictRow` / `RetrospectiveReport` | the full report |
| `render_markdown_table(report)` | fixed columns; the floor cannot be omitted |
| `main(argv)` | `python -m research.retrospective`, markdown or JSON |

Every dataclass is frozen with a `to_dict()`, every consciously-chosen
argument is keyword-only, and every degenerate input returns `None` rather
than raising or fabricating — matching the conventions of
`research/eligibility.py` and `research/overfitting_check.py`.

`min_fold_consistency` is a **required** keyword with no default, matching
`eligibility.evaluate_eligibility`'s deliberate refusal to pick a point
value inside CLAUDE.md's human-approved 80–90% range on a caller's behalf.
It affects only the reported pass flag; the verdict is DSR-driven.

## TDD

Tests were written first and confirmed failing against the unmodified tree
(`ModuleNotFoundError: No module named 'research.retrospective'`), then
implemented. One test genuinely drove a design decision rather than
confirming one: `test_the_trade_floor_is_checked_before_any_dsr_label`
failed against the first implementation, which had put `SURVIVES` ahead of
the trade-count check — the failure is what forced the precedence question
to be settled explicitly (see "The verdict vocabulary" above) instead of
inherited from the order the labels happened to be written in.

**52 new tests** in `python/tests/test_retrospective.py`, all against
**synthetic fixtures** — the real log is never read by a test, matching
`test_overfitting_check.py`/`test_eligibility.py`. Coverage: the detection
floor (closed form, monotonicity, α sensitivity, degenerate spans);
`bars_per_day` inference at 5m/15m/1h/1d and its four refusal cases;
de-duplication (identical folds collapse first-wins, different folds/ranges/
families stay distinct, single-fold and `holdout_access` records excluded,
logged `strategy_family` beats the curated map); trial-Sharpe derivation
(one per candidate averaged across folds, probes excluded both ways, `None`
preserved not dropped); **every verdict boundary** including exact
threshold equality, a point estimate of exactly zero, the implicit
adequately-powered cell, and the deliberate precedence; and the structural
honesty requirement (the rendered table's floor column is located by header
name and asserted non-empty on every row).

**Full suite: 902 passed** (850 on `main` at the branch point + 52 new).
Nothing regressed.

## Deliberately out of scope

- **Amending CLAUDE.md's Eligibility Bar, or its "Strategy Attempts So
  Far".** Human-approval-gated. Every proposal above is written out ready
  to adopt; none is applied, and no file in this PR touches CLAUDE.md.
- **Re-running any strategy.** The log was read only; no backtest executed.
- **Touching the `1d` holdout.** `sr-t` reserved it, and Proposal 2 defines
  the protocol for eventually using it. Loading it now would spend it.
- **Backfilling equity curves / return moments onto historical records.**
  Impossible without re-running every backtest; the normal-moment fallback
  with its explicit `moments_source` provenance is precisely the mechanism
  for evaluating those records honestly, and `sr-q` measured the cost at
  ~1e-7.
- **Implementing CSCV/PBO.** See Proposal 4 — deferred on a now-checkable
  condition rather than built speculatively.
- **Removing the superseded pre-annualization-fix `hourly_momentum` run**
  from `V_hat`. Post-hoc exclusion of a logged result is exactly what this
  project's methodology forbids; disclosed instead, with its (conservative)
  direction of effect.
