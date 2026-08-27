# Scalping Strategy Research Task S14 — the first walk-forward-validated scalping candidate, and why the S13 result did not survive it

Executed 2026-08-28. Two outcomes, and the second is the more important:

1. `selective-reversion`, the first scalping candidate built to S8's
   decomposed methodology and the first structured as CSTI, was run
   through this project's real walk-forward + Eligibility Bar machinery.
   **Verdict: REJECTED.** 83 folds, 721 trades, mean fold Sharpe −1.471.
2. Diagnosing why it failed found that **S13's own selectivity result was
   inflated by pseudo-replication** — overlapping 60-minute windows
   counted as independent observations. Correcting it collapses t from
   7.0–8.0 to 1.5–2.6 and halves the mean outcome.

The second finding retracts a claim made one day earlier in this same
arc. It is stated first in the summary below rather than buried, because
a reader who takes only the headline should take that one.

---

## 1. What was built

`python/research/strategies/selective_reversion.py`, 36 tests. The first
strategy in this project structured as **Condition / Setup / Trigger /
Invalidation**, the structure practitioner literature uses and which S4's
and S6's candidates both lacked — both fused direction, entry/exit and
sizing into a single threshold rule, which is exactly what S8 said to
stop doing.

| Layer | Rule | Where the number comes from |
|---|---|---|
| **Condition** | trailing percentile rank of absolute ATR/price ≥ 0.99 | S10 (absolute separates 5.21x, ratio only 1.22x) |
| **Setup** | `z(htf_ret_4h) + z(taker_buy_share)`, both trailing-1,440-bar | S11's two orthogonal groups (\|r\| ≤ 0.006) |
| **Trigger** | \|score\| ≥ 5.0, fade (both ICs negative) | S13 sweep — **selected** |
| **Invalidation** | stop at 2.65 ATR | S12's measured winners' MAE p80 |
| **R:R gate** | decline unless reward/risk ≥ 2.0 | practitioner floor (3:1 preferred, 2:1 minimum) |
| **Time exit** | 60 bars | S11 IC peak, S13 horizon sweep |

Two genuinely new mechanisms relative to every prior strategy here:

- **A stop derived from a measured distribution rather than convention.**
  S6 used 1.5 ATR and never measured it; on S12's sample that cuts 40.9%
  of winners.
- **An R:R qualification gate**, applied in the practitioner sequence —
  stop first, then reward against a structural target, then qualify, then
  size. A trade whose structure offers poor odds is *declined*, not
  resized. The structural target is the reversion the setup actually
  hypothesises: `htf_ret_4h` returning to its own trailing mean.

Both were the direct product of the methodology research the operator
asked for, and both are worth keeping regardless of this candidate's
result.

## 2. The walk-forward result

Data: Binance USDT-M futures BTCUSDT 1m, 3,661,780 bars, 2,543 days,
loaded through `load_research_klines` against a new
`configs/research/research_binance_futures_1m.json` so the holdout clamp
is structural. That config records why this window is research data (S6
spent it as a holdout; S8 reclassified it) and confirms the genuinely
reserved window — Binance **spot** 1m — is untouched and not even present
in the local store.

Geometry: 30 days train / 30 days validate / 30 days step, non-overlapping,
`bars_per_day = 1440`. **83 folds** — far above the 8–10 credibility floor.

```
folds                83
total trades        721
folds Sharpe > 0   30/83  (36.1%)
mean fold Sharpe    -1.4709
worst drawdown       12.44%
mean profit factor    1.2479
compounded return   -73.32%
```

Scored against the Eligibility Bar (`s14_eligibility.py`, reading the
logged record rather than re-running, so scoring cannot inflate `N`):

| Criterion | Result | |
|---|---|---|
| Fold consistency | 30/83 = 36.1% vs 80% | **FAIL** |
| Sign test | p = 0.996 | **FAIL** |
| Mean-Sharpe t-test | t = −3.688, p = 0.9998 | **FAIL** |
| DSR (N = 123) | 0.000 vs 0.95 | **FAIL** |
| Max drawdown | 12.44% vs 25% | PASS |
| Trade count | 721 vs floor 100 | PASS |
| Profit factor | 1.248 vs 1.3 | **FAIL** |

**VERDICT: REJECTED.**

**This is the first conclusive scalping result this project has
produced.** S4 and S6 both returned INCONCLUSIVE on trade count. 721
trades clears the floor by 7x, so this is a real rejection with evidence
behind it, not a shrug.

## 3. Why it failed — the diagnosis, and the retraction it forced

S13 measured **+25.10bps gross per position** at `|z|≥5`, top 1%,
60-bar horizon, against a 12bps round trip, at t = 7.96. Those entries
are the entries this strategy trades. Both numbers are real, so something
between them does the damage.

### 3a. The stop is destructive here, and the R:R gate is coupled to it

Isolating each mechanism (`s14_stop_diagnosis.py`, `run_backtest` directly
so nothing is logged — diagnosis, not selection):

| Variant | Trades | Win% | PF | Return | stop/target/time exits | Declined |
|---|---|---|---|---|---|---|
| as shipped | 783 | 46.5 | 0.987 | −134.5% | 375 / 28 / 380 | 75 |
| no R:R gate | 796 | 47.0 | 0.991 | −133.7% | 378 / 30 / 388 | 0 |
| **no stop + no gate** | **527** | **55.8** | **1.056** | **−0.20%** | 0 / 34 / 493 | 0 |
| stop 5 ATR | 361 | 50.7 | 0.902 | −49.1% | 105 / 9 / 247 | 1,492 |
| stop 8 ATR | 38 | 57.9 | 1.820 | +3.1% | 4 / 1 / 33 | 2,831 |

Three things fall out, and the third was not anticipated:

1. **The stop converts a break-even strategy into a badly losing one.**
   Removing it moves the result from −134% to −0.20%. That is the
   opposite of what a stop is supposed to do, and it is a real property
   of mean reversion rather than a bug: the edge lives precisely in the
   adverse excursion the stop cuts off. S12 said as much without drawing
   the conclusion — "winners digging 1.86 ATR on average says the entry
   is early — it fades a move that keeps going before it reverts."
2. **The target is nearly unreachable.** 28–34 target exits against
   380–493 time exits. The design in the docstring is not the design that
   ran: it is effectively stop-or-timeout, not stop-or-target. Consistent
   with S12's median MFE capture of 0.129.
3. **A wider stop mechanically fails the R:R gate**, because the gate's
   denominator *is* the stop distance. At 8 ATR it declines 2,831 of
   2,869 setups. "Widen the stop for safety" silently becomes "stop
   trading" — a real interaction between two independently sensible
   rules, worth knowing before anyone tunes one of them. The `stop 8 ATR`
   row's +3.07% on 38 trades is therefore not evidence of anything; it is
   38 survivors of a filter that rejected 98.7% of candidates.

### 3b. The real cause: S13's sample was not independent

Even with the stop and gate removed, the result is −0.20%, not the
+25bps/trade S13 implies. The gap is sampling.

S13's sweep took **every bar** meeting its criteria as a separate
position. Extreme readings cluster — a violent minute is usually next to
other violent minutes — so a single event contributed many overlapping
60-minute observations, each treated as independent. Deduplicating to
non-overlapping positions (the discipline `research/ic.py` already
enforces for IC sampling, and which the excursion sweep did not inherit):

| Cell | All obs | mean | t | Non-overlapping | mean | t |
|---|---|---|---|---|---|---|
| \|z\|≥4, top 0.1% | 3,717 | +19.31bps | 7.04 | 775 | **+7.81bps** | **1.50** |
| \|z\|≥5, top 1% | 2,994 | +25.10bps | 7.96 | 526 | **+13.97bps** | **1.95** |
| \|z\|≥6, top 1% | 1,233 | +41.30bps | 7.31 | 236 | **+30.87bps** | **2.56** |
| \|z\|≥2, top 10% | 145,568 | +0.95bps | 3.39 | 11,554 | **−0.84bps** | −0.98 |

**t collapses from 7–8 to 1.5–2.6**, and the mean falls by 25–60% —
so the duplicated observations were also the *better* ones. Against a
15-cell search, a Bonferroni-corrected two-sided threshold is |t| ≈ 2.94;
nothing reaches it. `|z|≥4` no longer clears the 12bps cost at all, and
the `|z|≥2` cell flips negative.

**S13's "gross outcome crosses the cost line, t = 7.0–8.0" is therefore
retracted as a significance claim.** What survives is weaker and still
real: gross outcome does rise monotonically with selectivity, and the
sign (mean reversion, not trend-following) is confirmed. What does not
survive is that any of it is distinguishable from noise once the
observations are counted correctly.

This also fully explains the walk-forward: a strategy takes
*non-overlapping* trades by construction, so it experiences the
deduplicated economics — +13.97bps gross against a 12bps cost, i.e.
essentially nothing, which the stop then pushes decisively negative.

## 4. The error pattern, now with a fourth instance

S13 recorded three errors sharing one shape — a result from one arbitrary
configuration generalised to a whole domain. This is a **different**
shape, and it is worth naming separately:

> **A statistic computed over overlapping windows is not a statistic over
> independent observations.** The tooling to avoid this already existed
> in this repo (`research/ic.py`'s non-overlapping sampling, built in
> S11 and documented as necessary). It was not applied to the excursion
> sweep in S12/S13, and the resulting t-statistics were inflated roughly
> threefold.

Building the right tool does not protect you if the next analysis does
not use it. That is the transferable lesson, and it is why the rule below
is written as a property of *any* overlapping-window measurement rather
than as a note about one script.

## 5. What this does and does not close

**Closed**: the specific `selective-reversion` v1 configuration, and the
S13 selectivity result as a significance claim. Neither is worth
retrying; re-running the same spec or grid-searching its constants is
foreclosed the same way S4's and S6's registrations foreclosed theirs.

**Not closed, and deliberately so**:

- **The CSTI structure and the R:R gate are not implicated.** They worked
  as designed; the signal underneath them was not there. Both should be
  reused.
- **The stop finding is a real, reusable result about mean reversion**,
  not a defect in the stop. A mean-reversion entry that is systematically
  early needs either a later entry or a risk control that is not a fixed
  adverse-excursion stop — position-level sizing, time-based scaling, or
  accepting the excursion and sizing for it. That is a genuine design
  question, not a tuning knob.
- **Order flow remains the one genuinely orthogonal signal this project
  has** (S11, \|r\| ≤ 0.006). Nothing here disconfirms it; this run
  simply did not find enough in the pair to clear costs at 1m.

**Explicitly not a live option**: another threshold or lookback set on
this window without a mechanism-level reason to expect something
different. `N` is already 123.

## 6. Artefacts

| File | What it is |
|---|---|
| `python/research/strategies/selective_reversion.py` | the strategy (36 tests) |
| `python/tests/test_selective_reversion.py` | tests, weighted to the R:R gate and the two rolling calculators |
| `python/research/analysis/s14_walkforward_run.py` | the logged 83-fold walk-forward |
| `python/research/analysis/s14_eligibility.py` | scores the logged record; does not re-run |
| `python/research/analysis/s14_stop_diagnosis.py` | the stop/target/gate isolation |
| `python/research/analysis/s13_selectivity_sweep.py` | S13's sweep, now also the pseudo-replication demonstration |
| `configs/research/research_binance_futures_1m.json` | declares the window research data, structurally |
| `python/research/lineage.py` | `selective-reversion` → `btc-scalping`, citing this doc |

Logged run: `591a6b6d-cac7-4783-b419-04c0713b6729`, `is_holdout_run=false`,
`strategy_family=btc-scalping`. Counts toward the project-level `N`, now
**123** — which is the point of S8's "search freely, count every trial,
deflate with DSR".
