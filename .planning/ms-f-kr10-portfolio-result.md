# Multi-Asset TSMOM Task F — result: diversification worked, the signal was not there

**Executed 2026-09-14** against
[`configs/research/preregistrations/daily-tsmom-kr10-portfolio-holdout.json`](../configs/research/preregistrations/daily-tsmom-kr10-portfolio-holdout.json),
committed at `89e35be` before the runner existed.

## Verdict: **INCONCLUSIVE**, and **NOT POWERED TO CONFIRM**

| Criterion | Observed | Required | |
|---|---|---|---|
| PSR | **0.7698** | ≥ 0.95 | fail |
| Sharpe | **0.3263** | > 0.5942 (this window's floor) | fail |
| Max drawdown | **31.20%** | ≤ 20% | fail |
| Profit factor | **1.2761** | ≥ 1.3 | fail |
| Total trades | **1,044** | ≥ 94 | **pass** |

1,881 daily observations. **The only gate that passed is the one
diversification was guaranteed to fix.**

## 1. Per member

| Symbol | Name | Trades | Sharpe | Max DD | Return |
|---|---|---|---|---|---|
| 005930 | 삼성전자 | 73 | +0.537 | 31.23% | +67.34% |
| 068270 | 셀트리온 | 118 | −0.421 | 48.66% | −37.16% |
| 000660 | SK하이닉스 | 89 | +0.644 | 41.45% | +103.96% |
| 009150 | 삼성전기 | 106 | +0.788 | 57.94% | +225.09% |
| 207940 | 삼성바이오로직스 | 129 | −1.070 | 66.61% | −63.39% |
| 000720 | 현대건설 | 81 | +0.086 | 31.38% | +1.39% |
| 007390 | 네이처셀 | 132 | −0.549 | 78.73% | −60.66% |
| 028300 | HLB | 138 | −0.412 | 66.55% | −55.91% |
| 064350 | 현대로템 | 98 | +0.243 | 46.90% | +16.20% |
| 051910 | LG화학 | 80 | +0.338 | 22.80% | +16.13% |

**Mean member Sharpe +0.0184**, six positive and four negative, spanning
−1.07 to +0.79. Mean member drawdown 49.23%.

## 2. The decomposition, which is the actual finding

**Diversification did what it was predicted to do. It was applied to
almost nothing.**

| | Member mean | Portfolio | Ratio | MS-D predicted |
|---|---|---|---|---|
| Sharpe | +0.0184 | **+0.3263** | ×17.7 | — |
| Max drawdown | 49.23% | **31.20%** | **0.634** | 0.57 |

The drawdown multiplier came in at **0.634 against a predicted 0.57** —
the aggregation model was very nearly right. What was wrong was the
input: MS-D §4 held each constituent at `sr-ab`'s **20.135%** drawdown
and projected 11.8% for the portfolio. Real members averaged **49.23%**,
2.4× worse. Apply the *measured* 0.634 to the *measured* 49.23% and you
get 31.2% — exactly the observed figure.

**So the arithmetic MS-A and MS-D built the case on is validated. The
assumption they fed it is refuted.** The portfolio behaved as a portfolio
of these instruments should; these instruments were not what the
projection assumed.

The same holds on the return side. A mean member Sharpe of +0.018 is
indistinguishable from zero, and no weighting scheme turns zero into an
edge — diversification divides risk, it does not manufacture return. The
×17.7 improvement is real and is exactly why the portfolio thesis was
worth testing; it is also 17.7 times almost nothing.

## 3. What this establishes, and what it does not

**It does NOT show TSMOM fails on Korean equities.** The observed 0.3263
sits *below* this window's own detection floor of 0.5942, so it cannot be
distinguished from zero. A true edge of 0.3 would look like this, and so
would no edge at all.

**It DOES exclude a large one.** A 0.6–0.8 Sharpe — the band credible
institutional trend-following reports, and the band MS-A's whole case was
built on reaching — is **ruled out for this universe over this window.**

That is worth stating plainly because **it is the first time this project
has excluded anything.** Every prior INCONCLUSIVE was underpowered: the
1h window's floor was 1.21, the 15m window's 2.18, BingX 1d's 0.96. Those
runs could not have detected a real edge if one had been there, so their
negative results carried no information. This one could have, at 0.5942,
and did not.

**A well-powered null is a result.** It is not the result this task was
hoping for.

## 4. Two runner defects, both found by running it

Neither crashed. Both produced a confident wrong number, which is the
failure mode this project keeps meeting.

**PSR saturated at 1.0 and reported a spurious PASS.** `evaluate_psr`
takes a **per-observation** Sharpe; the runner fed it the **annualized**
one. Over 1,881 points an annualized 0.3263 read as per-observation gives
√1880 × 0.3263 ≈ 14.1, and Φ(14.1) = 1.0. `eligibility.py`'s own module
docstring names this exact trap, and there is a dedicated helper —
`psr_from_equity_curve` — that resamples to daily and derives the Sharpe,
the moments and T from one series so they cannot drift apart. The
corrected figure is **0.7698**, which fails the gate the bug had passed.

**The expected-bar-count check ran after the holdout claim was
consumed.** The registration's `end_ms` had been set to the last bar's
open time rather than the exclusive bound every other registration in the
directory uses, so the half-open fetch returned 1,881 bars against a
declared 1,882. The guard caught it correctly — and by then the one-shot
claim was already spent. `preflight_bar_counts` now issues a `COUNT(*)`
before the claim; counting rows reads no prices, the same category as
`verify_known_gaps`, which likewise runs before any load.

## 5. Three holdout accesses, two of them reclaims

On the record, because a re-run of a one-shot window is exactly the thing
that must not be quiet:

1. **Consumed and crashed** in the reporting layer (`AttributeError` on
   `prereg.declared_detection_floor_sharpe`; the accessor is
   `prereg.config[...]`). All ten member backtests had completed; no
   portfolio statistic was computed and no criterion evaluated.
2. **Reclaimed** with that reason. Produced the saturated PSR of §4.
3. **Reclaimed** for the metrics-bug fix — the case
   `load_holdout_klines`' own error message names. Produced the figures
   above.

**Disclosed rather than glossed**: the per-member figures were printed by
run 1 and are therefore known to the author. Nothing was changed in
response to them — between runs the only edits were an attribute name and
which PSR helper is called. The registration, universe, criteria, cost
constants and strategy module are byte-identical across all three, and
all were committed before the first.

**Where that record lives, and the gap in it.** All three
`holdout_access` entries are in `runs/experiments.jsonl`, which
`.gitignore` deliberately excludes (`runs/*`, with only
`runs/live_signals.jsonl` negated). So the machine-readable audit trail
for the one-shot claim is **local, not committed** — a pre-existing
project decision, not one made here, but it means this document is the
only durable record that three accesses happened and why. Recorded that
way on purpose rather than left implicit.

A fourth run was **not** needed for the `MIN_RETURNS_FOR_PSR` guard added
afterwards: it was shown a no-op above the threshold by computing both
code paths on an 1,881-point series and comparing the outputs directly,
rather than by spending the window again.

## 6. Costs, which were committed before the run and were not the problem

`fee_bps = 1`, `slippage_bps = 20`, a **42bps round trip** against BTC
perps' 12bps — 3.5× more expensive, entirely because a Korean
single-stock-futures tick is ~1000× wider in relative terms (measured:
5.91bps for SK하이닉스 to 19.49bps for 삼성전자).

At 104 trades per member that is a real drag, and it is **not** what
decided this. A mean member Sharpe of +0.018 does not become 0.6 at zero
cost.

## 7. What follows

Nothing about the strategy, and that is the honest position. Three things
are worth separating:

- **Do not re-run this window with adjusted anything.** The stopping rule
  in the registration forecloses it, and the window is spent.
- **The infrastructure is now real and reusable.** A KRX data pipeline
  with calendar-aware coverage, a resolved universe with a committed
  rule, a portfolio runner, and a window with the best detection floor
  this project has held. A *different* hypothesis on Korean equities
  would start from here rather than from nothing — under its own
  pre-registration, and counting toward `N`.
- **`daily-tsmom-ensemble` on BTC is untouched by this.** It runs under
  its own approved Paper Trading Policy Exception, and this is a
  different `strategy_id` on a different universe. What changes is only
  that the portfolio explanation for its two INCONCLUSIVEs — the one MS-A
  argued for at length — has now been tested and did not carry.
