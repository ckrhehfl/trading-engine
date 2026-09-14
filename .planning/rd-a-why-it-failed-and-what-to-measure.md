# Research Direction Task A — `Discuss`: the failure was an instrument, not a strategy

**Status**: Discuss. Nothing built, nothing run, no data accessed beyond
re-reading `runs/experiments.jsonl` and the local store — both already
this project's own.

Opened 2026-09-14 at the operator's direction: *stop executing, research
the direction; learn from the failures; decide what other data belongs;
and approach it the way a trader finds strategies rather than the way
this project has been.*

Everything in §1 is computed from the log in this repository. Nothing is
recalled from a summary.

---

## 1. The audit

### 1.1 What was actually scored

1,883 `backtest_run` records. 78% are four momentum `strategy_id`s, which
CLAUDE.md already notes. But the parameter keys say something the
`strategy_id` count hides — **management was searched, heavily**:

| Parameter | Runs |
|---|---|
| `stop_multiplier` / `target_multiplier` / `atr_period` | 1,681 |
| `vol_period` / `target_annualized_vol` / `min,max_vol_scalar` | 1,484 |
| `adx_period` / `adx_high` / `adx_low` | 1,381 |
| `lookback_pairs` | 1,026 |

So "every run asked which formula predicts direction" is **not quite
right**. Stops, targets, sizing and regime gates were varied in most
runs. What was never done until Task D is vary them with **the entry held
fixed** — co-varying everything means no axis can be attributed.

### 1.2 The distribution nobody looked at

| | |
|---|---|
| n | 1,817 runs with a Sharpe |
| mean | **−0.5612** |
| σ | **2.4897** |
| max | **+7.824** |
| positive | 43.9% |

**The best number in this project's history is what noise predicts.**
Under `E[max] ≈ μ + σ·Φ⁻¹(1 − 1/n)`:

| n | E[max] under pure noise |
|---|---|
| 1,817 | **+7.564** |
| 454 | +6.529 |
| 100 | +5.231 |

Observed max **+7.824** against a noise expectation of **+7.564**. The
gap is well inside the sampling error of an extreme-value estimate.

*(Caveat, stated because it cuts against the argument's strength: the
1,817 rows are not independent draws — many are folds of one run over
shared data — so the effective n is smaller and E[max] correspondingly
lower. The point survives directionally and should not be quoted as an
exact equality.)*

### 1.3 Two hypotheses tested, one discarded

**Discarded — "trading more is worse."** The median Sharpe falls
monotonically across trade-count buckets (+0.60 → −0.51 → −0.11 → −1.17 →
−2.46), which looks like a cost signature. **It is confounding.** The
buckets are dominated by different `strategy_id`s (`hourly_momentum`,
median −2.33, owns the 51–200 bucket). Within a single strategy the
effect is inconsistent in *sign*: ensemble-momentum −1.06, single-lookback
−0.30, hourly_momentum **+0.35**. Not a finding.

**Confirmed — the scoring unit could not measure anything.** The 846
`ensemble-momentum` rows are not parameter candidates. Their own
`walk_forward_config` reads `total_candidates: 1`, `fold_count: 0`, *"no
grid search"* — they are **per-fold scores of one fixed configuration**.
So the dispersion is fold-to-fold variation of the *same strategy with
the same parameters*:

| | |
|---|---|
| Observed fold σ (median over 34 groups of ≥10 folds) | **2.15** |
| Theoretical SE of an annualized Sharpe from a 30-day fold | **2.90** |

`SE(SR) ≈ √252 · √((1 + SR²/2)/T)`, with `T = 30` daily observations in a
720-bar 1h fold.

**The observed dispersion is *below* what pure estimation noise predicts.
Nothing about regime change or strategy quality is needed to explain the
fold-to-fold spread.** A true edge of 0.8 appears in such a fold as
0.8 ± 2.9; the *sign* is near a coin flip (P(positive) = Φ(0.8/2.9) =
0.61).

`sr-j` and S16 each derived a neighbouring fact — that an 80–90%
fold-consistency floor is unreachable — and both framed it as *the
criterion being too strict*. The instrument reading says something
stronger and more useful: **a 30-day fold cannot estimate a Sharpe at
all**, so every fold-based criterion was applied to coin flips, and
relaxing the criterion did not fix it.

### 1.4 The measurement ladder

| Unit | T | SE(Sharpe) |
|---|---|---|
| **1h × 30-day fold — what 1,883 runs were scored on** | 30 | **2.898** |
| BTC 1d holdout (2.95 y) | 743 | 0.582 |
| Binance futures 1m (6.96 y) | 1,754 | 0.379 |
| **KRX 1d (7.66 y) — built 2026-09-13** | 1,882 | **0.366** |
| BTC 1m IC, 60-min horizon, non-overlapping | 61,004 | **0.004** |

## 2. The asymmetry that should drive everything

`python/research/ic.py` **does not import `experiment_log`.** IC
measurements are never written to `runs/experiments.jsonl` and therefore
**do not count toward the project-level `N`**.

| Activity | Cost in `N` | SE |
|---|---|---|
| Backtest one configuration | **+1 selection trial** | 2.9 on a fold |
| Measure one feature's IC | **0** | 0.004 |

**This project spent its scarcest resource on its noisiest instrument,
1,883 times, and used its cheapest and sharpest instrument once.**

**Not free, differently priced — stated so this is not read as a
loophole.** Measuring twenty features and choosing three *is* selection;
it simply is not the kind DSR is built for. S11 handled it correctly and
said so: *"The features were chosen, not discovered… that is a search, it
is disclosed here, and the FDR correction is applied across the whole
sweep for exactly that reason."* Benjamini-Hochberg over 40 tests is the
right instrument for a measurement sweep; DSR over 127 trials is the
right one for a selected performance statistic. **The asymmetry is that
FDR over a sweep costs far less than DSR over a search — not that
measuring is unpriced.**

## 3. What actually worked, when measured with the right instrument

Three results, and they are the only three in the record that survive
scrutiny:

**S11 — signal, measured as a signal.** `htf_ret_4h` rank IC **−0.0508**
at n=61,004 (t ≈ 12.5). **Every price and momentum IC negative**, across
four independent formulations — mean reversion at the hour scale. Order
flow **orthogonal to every price feature**, |r| ≤ 0.006. These are by a
wide margin the best-measured facts this project holds.

**S16 — harvest, after the entry was held fixed.** `|z|≥6`, top 0.1%
activity, **no stop**, compounding sizing: mean fold Sharpe +0.8993,
66.2% folds positive, compounded +32.42%, **t = +2.388, p = 0.0098**,
PSR 0.9905, drawdown 9.93%, profit factor 6.44, 181 trades, and **not
2021-dependent** (+26.12 of +32.42 survives excluding 2021).

It fails on one number: **DSR 6.46e-11 against N = 127**. At `N = 1` the
requirement is 0.63 and the observed is 0.899.

**Task D — management, entry byte-identical.** The full spread from Gate A
failure to Gate A pass came from management alone. The largest single
effect this project has measured.

### 3.1 The conversion failure, and why it is the one real strategy lesson

S11's IC was real and S14 built on it and produced **mean fold Sharpe
−1.471**. A good signal did not become a good strategy.

The cause is now known and is not a signal problem: **the stop was
cutting off the edge.** Removing it moved the full-window result from
−134% to −0.20%; no width from 1.5 to 12 ATR helps, because at every
width the stop realises a *larger* loss than the position would have
taken alone. CLAUDE.md states it as *"the adverse excursion is not a cost
paid before the edge; it **is** the edge."*

So the failure decomposes, and the two halves are not the same kind of
thing:

1. **Instrument failure** (§1) — dominant, explains "nothing ever
   passed", and is not a fact about any strategy.
2. **Harvest failure** — the single real strategy-level lesson, and it
   was *found and fixed* the moment the signal was held fixed and only
   the harvest varied.

## 4. What a trader does, and why the statistics agree with it

A trader does not run 1,883 backtests. The loop is:

1. Notice a **recurring situation**, and name who is on the other side
   and why they keep losing there.
2. Take it a few times, small.
3. Find that the entry was roughly right and **the management was wrong**
   — cut too early, sized wrong, held through the wrong thing.
4. Fix the management. Repeat.

Step 3–4 is where a trader spends most of the effort, and **this
project's own measurements say that is where the effect is**: S15's
no-stop result (4.4× on mean Sharpe, the largest single improvement in
the scalping arc) and Task D's entire Gate-A spread both come from the
harvest layer with the signal held fixed.

Step 1's "name the mechanism" is already a written rule (S8) — *"who is
on the other side and why they lose. '20-period VWAP, 2 SD' is a formula,
not a hypothesis"* — and IC is exactly the cheap screen for it.

**The trader framing and the instrument diagnosis point the same way.**
That agreement is the strongest reason to believe the direction, because
they were derived independently: one from how discretionary traders
actually work, one from `√252·√(1/T)`.

## 5. The proposed program

**One principle: match the instrument to the question, and spend `N` only
where it is unavoidable.**

| Layer | Question | Instrument | `N` cost | SE |
|---|---|---|---|---|
| **1 Signal** | Is there information at all? | rank IC, non-overlapping, FDR-corrected | **0** (FDR applies) | 0.004 |
| **2 Harvest** | Can it be extracted after costs? | paired comparison, **entry fixed** | comparison-run rules (already human-approved) | — |
| **3 Portfolio** | Does breadth help? | aggregated curve, no selection | 0 | 0.37 |
| **4 Confirm** | Is it real? | PSR, one pre-registered window | **1** | window's own |

**The sequencing rule: never move to a more expensive layer until the
cheaper one has answered.**

Every past failure is a layer-skip, and the taxonomy is checkable rather
than rhetorical:

| Arc | Skip |
|---|---|
| 15m/1h research, 1,883 runs | Layer-4-style scoring applied to a Layer-1 question |
| S14 | Had Layer 1, skipped Layer 2 — assumed a 1.5 ATR stop |
| Task C | Named a mechanism, but a parameter-free exit silently pinned the hold to ~1 hour |
| Task D | **Did Layer 2 properly** → biggest measured effect |
| MS-F (2026-09-13) | **Did Layer 3 properly** → aggregation model validated (DD multiplier 0.634 predicted 0.57) |

## 6. Which data belongs, on a criterion rather than a wish

Grinold: `IR ≈ IC × √breadth`, breadth = **independent** bets. So the
question for any source is not "is it interesting" but **"does it raise
breadth, or is it another view of price?"** S11 already established the
only proven orthogonal pair this project holds: order flow versus price,
|r| ≤ 0.006.

| Source | Held | Orthogonal to price? | Measured as IC? |
|---|---|---|---|
| **Binance futures 1m** | 3.66M bars, 6.96 y | — (it *is* price) | yes (S11) |
| **KRX 1d ×10 + index** | 1,882 × 11, 7.66 y | — | **never** |
| **`funding_rates`** | 6,199 rows, 2020-11→ | **plausibly** — crowding, not price | **never** — `sr-n` measured its P&L, never its IC |
| **`positioning`** | 37 days, 10 metrics, growing | **yes by construction** — participant state | **never** (too short yet) |
| **macro** `DGS10` (1962→), `DTWEXBGS`, `DFII10`, `SP500` | 6k–17k rows | **yes** — different asset class | **never** — `sr-x`/`sr-y` tested two as *strategies* and got INCONCLUSIVE-DATA-LIMITED on 34 and 19 trades |

**Three of the five have never been measured with the cheap instrument,
and two of those were tested with the expensive one and died of sample
size.** That is the same mistake §1 diagnoses, repeated on the data axis.

### 6.1 The KRX universe changes the breadth term structurally

S11 noted the limit: *"breadth here comes from independent decisions in
time, so it is bounded by how long a bet is held, not by how many symbols
are traded."* One asset caps breadth at time-diversification.

Ten names at a **measured** 2018 internal correlation of 0.157 give an
effective breadth of `10 / (1 + 9×0.157) ≈ 4.1`, so `√4.1 ≈ 2.0×` on IR
for the same IC. **That is a different and better argument for the KRX
universe than MS-A's**, which reasoned only about volatility and
drawdown.

The honest constraint: panel IC on KRX daily is thin at long horizons.
`1,882 days / horizon × 10 names`:

| Horizon | n | SE(IC) | t for IC = 0.05 |
|---|---|---|---|
| 5 days | 3,764 | 0.0163 | **3.07** |
| 10 days | 1,882 | 0.0231 | 2.17 |
| 20 days | 941 | 0.0326 | 1.53 |

So a daily-horizon IC screen on KRX is usable at 5–10 days and marginal
beyond. Stated now rather than discovered later.

## 7. The line that must not be blurred

The spent windows (1h research, BingX 1m, Binance futures 1m) stay
**closed to selection** and open for *"reproduction, diagnosis and
infrastructure testing."*

**Measuring an IC to diagnose a mechanism is diagnosis. Measuring twenty
ICs to choose which feature to build on is selection.** The two look
identical at the keyboard and differ entirely in what may be concluded.
The distinction has to be pinned in a registration *before* the sweep —
declaring the feature list, the horizons, and the FDR family — exactly as
MS-E pinned its thresholds before running the rule, or it is unfalsifiable
after the fact.

## 8. Open questions — the operator's, not mine

1. **Which mechanism goes first.** §3 leaves one candidate already at
   `t = 2.388` on a spent window (S16) and one unmeasured family with
   plausible orthogonality (funding, macro). The first is a *confirmation*
   decision — it needs the last unspent 1m window and CLAUDE.md's human
   checkpoint #2. The second is a *measurement* decision and costs almost
   nothing. **They are not alternatives and the order matters.**
2. **Whether the S16 candidate is taken to confirmation at all.**
   CLAUDE.md is explicit that `N = 1` removes the selection penalty and
   not the replication question, and that `daily-tsmom-ensemble` got two
   disjoint confirmations and still read INCONCLUSIVE.
3. **Whether an IC sweep is pre-registered.** §7 says it must be if its
   output is to justify building anything. That is a real process cost
   and it is the operator's call whether to pay it now or treat the first
   sweep as pure diagnosis with nothing built on it.
4. **Whether any of this goes into CLAUDE.md.** §1's instrument finding
   contradicts the framing in two existing sections (`sr-j` and S16 both
   read the fold problem as a criterion problem). Writing it there makes
   it settled for future sessions — CLAUDE.md's own human checkpoint #3.

## 9. What this document does not claim

- **No strategy has been found.** Nothing here changes that.
- **The instrument diagnosis does not resurrect any past result.** A run
  scored on a noisy unit is not secretly good; it is unmeasured. Every
  rejected configuration stays rejected.
- **IC is not profit.** S11 says so itself, and S14 is the proof: a real
  −0.05 IC produced −1.471 mean fold Sharpe when harvested badly.
- **§5 is a proposal, not a decision.** It reorganises which instrument
  answers which question; it does not promise that a better-measured
  search finds an edge that is not there.
