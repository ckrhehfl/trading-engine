# Scalping Strategy Research Task S11 — per-feature IC, and how many signals we actually have

S8 Part 4 steps 3 and 4, executed 2026-08-26. **The first positive
result in this research arc**, and the first time this project has
measured features as features rather than jumping to a strategy.

Measurement only: no entry rule, no exit, no sizing, no P&L, nothing
logged to `runs/experiments.jsonl`, and run on already-spent windows.

## What was built

`python/research/ic.py` — a reusable information-coefficient harness.
27 tests.

The design decisions that matter, all of them things a naive IC harness
gets wrong in the direction of manufacturing significance:

- **Time-series IC, not cross-sectional.** The conventional quant-equity
  IC correlates a feature across many assets at one instant. This project
  trades one symbol, so the correlation runs across many *instants*
  instead. That is not cosmetic: in `IR ~= IC * sqrt(breadth)`, breadth
  then comes from independent decisions **in time**, bounded by holding
  period, not from a universe of symbols.
- **Rank IC (Spearman) by default**, Pearson reported alongside. Crypto
  returns are fat-tailed and one outlier bar can dominate a linear
  correlation; a large gap between the two is a warning that outliers are
  doing the work, and is visible rather than hidden.
- **Non-overlapping samples.** Sampling every bar while measuring an
  `h`-bar forward return makes consecutive observations share `h-1` bars.
  `sample_indices` steps by the horizon. Even so they are non-overlapping,
  **not independent** — volatility clustering guarantees residual serial
  dependence — so reported t-statistics are an upper bound on confidence.
- **Benjamini-Hochberg across the whole sweep**, not per horizon.
  Twenty features at two horizons is forty tests; at alpha=0.05 two false
  positives are expected from noise alone. Correcting per horizon would
  understate the test count, which is precisely the loophole the
  correction exists to close. What that guarantees is that **the family
  corrected is the family actually tested**, and a test asserts exactly
  that by comparing `measure_all`'s flags against one BH pass over the
  whole sweep's p-values.

  **It does not guarantee that more hypotheses make the bar stricter**,
  and an earlier draft of this document claimed it did. BH re-ranks the
  entire family whenever it changes, so adding a hypothesis with a very
  small p-value raises the step-up cutoff and can make a previously
  rejected result significant: `[0.03, 0.9]` yields no discoveries while
  `[0.001, 0.03, 0.9]` yields two. That is BH working as designed — it
  controls the false *discovery rate* across a family, not each member's
  individual threshold. A regression test pins the counterexample so the
  false invariant cannot be reintroduced.
- **`None` features are skipped, never imputed.** Substituting a zero or
  a mean invents data and biases the correlation toward the substitute.

## Result: 10 of 26 clear both bars

Binance USDT-M futures BTCUSDT, 3,661,780 bars, warmup 1,500,
horizons 15 and 60 minutes. "Both bars" means `|rank IC| >= 0.02` (S8's
own usability calibration) **and** surviving FDR correction.

| Feature | Horizon | n | rank IC |
|---|---|---|---|
| `htf_ret_4h` | 60 | 61,004 | **−0.0508** |
| `htf_ret_4h` | 15 | 244,018 | **−0.0374** |
| `htf_ret_1d` | 60 | 61,004 | **−0.0325** |
| `struct_dist_prior_high` | 60 | 61,004 | **−0.0301** |
| `struct_pos_in_prior_range` | 60 | 61,004 | **−0.0268** |
| `flow_taker_share` | 15 | 244,000 | **−0.0268** |
| `flow_taker_share` | 60 | 61,001 | **−0.0228** |
| `flow_ofi_15` | 15 | 243,976 | **−0.0225** |
| `flow_ofi_15` | 60 | 60,995 | **−0.0212** |
| `struct_dist_prior_low` | 60 | 61,004 | **−0.0206** |

Measured and **not** usable: volume-vs-its-own-mean ratio (|IC| < 0.003),
distance to the nearest 1,000-dollar round number (< 0.004), weekday
(< 0.004). Hour-of-day and session survive FDR but sit below the 0.02
usability line, as does absolute ATR as a *signed*-return predictor —
unsurprising, since S9/S10 established it predicts *magnitude*.

**Every single price and momentum IC is negative.** Recent strength
predicts subsequent weakness at both horizons: this is mean reversion at
the hour scale, consistently signed across four structurally different
formulations of "how far has price run".

**Conditioning on the top 10% of activity — the moments S9 established
are the only ones where movement clears cost — strengthens most of
them**, rather than washing them out:

| Feature | 60m IC, all bars | 60m IC, top-10% activity |
|---|---|---|
| `struct_dist_prior_high` | −0.0301 | **−0.0466** |
| `htf_ret_1d` | −0.0325 | **−0.0465** |
| `struct_dist_prior_low` | −0.0206 | **−0.0334** |
| `htf_ret_4h` | −0.0508 | −0.0493 |

That is the useful direction: the signals are not artefacts of untradeable
quiet periods.

## Orthogonality: 10 features are about 3 signals

Grinold's law counts **independent** bets, so the feature count is
meaningless until this is measured. Rank correlation between survivors,
on the same non-overlapping sample:

| Cluster | Members | Internal correlation |
|---|---|---|
| Daily price structure | `htf_ret_1d`, `struct_dist_prior_high`, `struct_dist_prior_low`, `struct_pos_in_prior_range` | **0.72 – 0.85** |
| 4-hour momentum | `htf_ret_4h` | 0.33 – 0.41 against the cluster above |
| Order flow | `flow_taker_share`, `flow_ofi_15` | 0.284 with each other |

**The finding worth keeping: order flow is essentially uncorrelated with
every price-based feature — |r| <= 0.006.** Not "weakly correlated";
indistinguishable from zero across four different price formulations.
That is exactly the condition Grinold's law needs, and it is the first
time this project has had two genuinely independent information sources
in hand at once.

Effective breadth is therefore roughly **3**, not 10. Four of the ten
survivors are one signal wearing four costumes.

## What this retroactively says about `ofi-momentum` (Task S6)

S6 traded order-flow imbalance as **momentum** — buy when OFI is
strongly positive. The IC measured here for `flow_ofi_15` is
**negative** at both horizons. If that sign holds, S6 was systematically
on the wrong side of a real, if small, effect, which is consistent with
its 17.98% win rate against a ~33.3% breakeven need.

Stated carefully rather than as a triumph: S6 also had a catastrophic
cost structure and non-compounding sizing, either of which could sink a
correctly-signed strategy, and a −0.02 IC is far too small to explain a
17.98% win rate on its own. This is a plausible contributing cause with
a measurement behind it, not a diagnosis.

## Honest limits

- **IC is not profit.** A 0.05 IC is a real edge and still says nothing
  about whether it survives 12bps round-trip costs, a stop, or sizing.
  Those are steps 5 and 6.
- **Non-overlapping is not independent.** Volatility clustering leaves
  serial dependence, so the t-statistics are optimistic and the FDR
  correction inherits that optimism.
- **The features were chosen, not discovered.** Roughly twenty
  formulations across seven categories were computed and measured; that
  is a search, it is disclosed here, and the FDR correction is applied
  across the whole sweep for exactly that reason.
- **One asset, one venue, two spent windows.** Nothing here has been
  checked on the reserved Binance spot 1m holdout, and nothing should be
  until a candidate is actually specified.
- **The 0.02 usability line is S8's own convention**, drawn from the
  practitioner literature. It is a calibration, not a law.

## What follows

1. **Combine the three independent signals**, not the ten features —
   price structure (pick one representative, not four), 4-hour
   reversion, and order flow. Grinold gives `sqrt(3)` as the ceiling on
   what combining buys over the best single one, and the law is known to
   overstate, so treat it as an upper bound.
2. **The sign is mean-reverting.** Any candidate built from these must
   fade recent strength, not chase it — the opposite of what S6 did.
3. **MAE/MFE next** (S8 step 5) to place stops and targets from data
   rather than convention, before any full backtest.
4. Only then a strategy, the risk budget, and walk-forward with DSR.

## Reproduction

`research.ic.measure_all` over features built from the local
`BINANCE-FUTURES:BTCUSDT` 1m table, warmup 1,500 bars, horizons 15 and
60, Benjamini-Hochberg at alpha=0.05 across all 26 results.
Prior-day levels use the **prior** UTC day; rolling windows exclude the
current bar where the current bar would otherwise be compared against
itself. Order-flow features are `None` for bars lacking
`taker_buy_base_volume` and are skipped rather than imputed.
