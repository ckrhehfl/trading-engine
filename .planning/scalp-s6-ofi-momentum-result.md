# Scalping Strategy Research Task S6 — real holdout result

Companion to `.planning/scalp-s6-ofi-momentum.md` (the commit-phase
design record, PRs #114/#115, merged). That document specified and
committed the `ofi-momentum` strategy and its pre-registration; it did
not execute the real holdout access. This document is that execution
and its real, honest result — mirroring `sr-u`/`sr-v` and
`vwap-mid-reversion`'s own commit/execution split precisely.

## The real invocation

Executed 2026-08-26 from the repository root (same working-directory
discipline `vwap-mid-reversion`'s own execution already established —
`data.holdout_config_path` is repo-root-relative):

```text
PYTHONPATH=python python/.venv/bin/python -m research.run_preregistered_holdout \
  configs/research/preregistrations/ofi-momentum-binance-1m-holdout.json \
  --runs-path runs/experiments.jsonl \
  --db-path python/data/var/klines.sqlite3
```

Confirmed a genuine first access before running (scanned
`runs/experiments.jsonl` for any prior `holdout_access` record for
`strategy_id="ofi-momentum"` — none existed). Ran exactly once,
successfully. `runs/experiments.jsonl` carries exactly one
`holdout_access` record for this `strategy_id` and exactly one holdout
confirmation `backtest_run` record (`is_holdout_run: true`,
`run_id=5a30cfd2-8ff6-466f-99b8-5df3f885a656`, `code_version=73c757c...`,
matching PR #115's merge commit).

## The real result

```text
bars evaluated               : 3,661,780
observed annualized Sharpe   : 0.6399235918134146  (bar-level, annualized)
declared detection floor     : 0.6231732616841021
PSR                          : 0.8231000423093435
max drawdown                 : 2391.609498950343165691311138  (i.e. 239,161%)
total trades                 : 56,441
win rate                     : 0.1797629382895413  (17.98%)
profit factor                : 0.05485159726437846
starting equity                : 10,000
final equity (see caveat below): -23,906,094.99
```

```text
gating checks:
  [FAIL] psr:                          required=0.95    observed=0.8231
  [FAIL] max_drawdown:                 required=0.20    observed=2391.6...
  [PASS] min_total_trades:             required=100     observed=56,441
  [FAIL] profit_factor:                required=1.3     observed=0.0549
  [PASS] sharpe_above_detection_floor: required=0.6232   observed=0.6399

OUTCOME: INCONCLUSIVE
```

**PSR's own input series, stated precisely from the start this time**
(the corrected lesson from `vwap-mid-reversion`'s own result writeup,
where an earlier draft conflated two different statistics): `psr_from_
equity_curve` resamples to **daily** granularity before computing PSR —
a 2,541-point series, not the 3,661,779 raw bar-level returns
`return_skewness`/`return_kurtosis` describe. The real, logged `psr`
sub-object:

```text
psr.num_observations : 2541       (daily-resampled points)
psr.sampling          : "daily"
psr.sharpe_ratio       : 0.027543411357110508   (DAILY Sharpe -- not the
                                                   0.640 annualized figure
                                                   above)
psr.skewness           : -33.566741108859105
psr.kurtosis           : 1670.6229978016574
psr.psr                : 0.8231000423093435
```

(The bar-level `return_skewness=-655.02`/`return_kurtosis=1,304,258.86`
over 3,661,779 observations are a separate, real, logged statistic —
not what PSR was evaluated against.)

## Honest interpretation — two real, distinct causes, both disclosed precisely

Unlike `vwap-mid-reversion`'s own result (INCONCLUSIVE only on the
*practical* gates, Sharpe below its own detection floor), **this run's
Sharpe (0.640) genuinely clears its own detection floor (0.623)**.
Clearing the detection floor and being statistically significant are
two different claims, kept separate here rather than conflated: the
detection floor states that an effect of this size is *large enough to
be detectable at all* on a window of this calendar length — it is a
power statement, not a significance test. The actual significance test
is PSR against the pre-registered 0.95 threshold, and **that test
failed** (PSR = 0.8231). So the honest framing is that this window was
powered to detect an edge of this size, not that a real edge was
thereby confirmed — the PSR miss is not one practical gate among
several alongside drawdown/profit-factor, it is the significance result
itself coming up short. This is still a *different* failure shape from
`vwap-mid-reversion`'s (there, Sharpe itself fell below the detection
floor, so the run wasn't even powered to speak to significance) —
worth stating precisely rather than treating both results as
interchangeable "it lost money" outcomes, without overstating this run
into a confirmed finding it isn't.

**Cause 1 — the observed win rate is far below this structure's
breakeven point.** With `stop_multiplier=1.5`/`target_multiplier=3.0`
(a 1:2 risk:reward ratio), the strategy needs roughly a 33.3% win rate
to break even before costs (ignoring the modest asymmetry fees/slippage
add) — this arithmetic is exact, not run-specific. The real observed
win rate **in this one holdout run** is **17.98%** — well below that
threshold. Stated as precisely as the significance discussion above
requires: this is a real, disclosed observation from this specific
run, not an independently statistically-confirmed structural property
of OFI at this horizon (the PSR gate above did not pass, so this run
alone cannot carry that claim). It is, however, **consistent with** —
not a confirmation of — the real cited paper's own honest caveat: Kim &
Hansen (2026, arXiv:2607.09426) found order-flow imbalance's real
effect is "much weaker at finer clock-time frequencies" than its own
4-12-hour finding, stated there as a qualitative warning. This holdout
gives one real, quantified data point in that same direction
(17.98% vs. a ~33.3% breakeven need) — one observation aligned with the
paper's caveat, not a statistical test of it.

**Cause 2 — a real, disclosed, project-wide sizing characteristic let a
negative-edge signal compound to a catastrophic aggregate result, even
though the risk control that was supposed to prevent exactly this kind
of outcome (`vwap-mid-reversion`'s own missing stop-loss) was genuinely
present this time.** `compute_position_size` sizes every single trade
against a **fixed** `reference_equity` constant (10,000), never the
strategy's own real, shrinking equity — by design, per that function's
own documented rationale (no live equity feedback into a `Strategy`,
matching every sibling strategy in this codebase, including
`hourly_momentum.py`, whose own established pattern this module
directly mirrors). The ATR stop genuinely bounds any *single* trade's
loss to `reference_equity × risk_fraction` ≈ $100 — a real, working fix
for `vwap-mid-reversion`'s own unbounded-single-trade risk. But bounding
each trade individually does not bound the *sum* of 56,441 trades: with
a real, negative edge (Cause 1) and no account-level circuit breaker or
equity-compounding sizing, losses accumulate arithmetically across a
very large trade count, producing a raw, uncapped `final_equity` of
**-$23,906,095**. **Precisely characterized, not overstated** (same
correction already applied to `vwap-mid-reversion`'s own result writeup,
applied here from the start): this is not a literal dollar figure a
real leveraged account would reach — a real account would have been
liquidated by its exchange enormously earlier, likely within the first
few hundred losing trades — it is a severity signal reflecting how
persistently negative this signal's edge was over the full window, not
a claim about real capital. The qualitative lesson survives the
correction: a real, structurally-bounded-per-trade risk control is
necessary but **not sufficient** to prevent a catastrophic backtest
outcome when (a) the signal's own edge is genuinely negative and (b) a
large number of trades compound against a fixed, non-account-aware
sizing baseline. This second point is a real, disclosed characteristic
of `research/strategies/risk_management.py`'s own design, shared by
every strategy in this codebase that uses it (`hourly_momentum.py`,
`regime_momentum_risk_managed.py`, and now `ofi_momentum.py`) — not
unique to this candidate, and not previously stress-tested at this
trade count or this poor a win rate by any prior strategy in this
project's history.

## What this does and does not mean, per the pre-registration's own committed text

Per `ofi-momentum-binance-1m-holdout.json`'s own `outcome_interpretation.
INCONCLUSIVE` text (committed *before* this access, modeled on
`sr-ab`'s narrow scoping, the same precedent `vwap-mid-reversion`'s own
registration used): this result **parks this specific hypothesis** —
15-bar/2-SD order-flow-imbalance momentum with ATR 14/1.5/3.0 risk
control on Binance futures 1-minute BTCUSDT — as a candidate. It does
**not** by itself end the broader Scalping Strategy Research direction,
and it does **not** retroactively affect `vwap-mid-reversion`'s or
`daily-tsmom-ensemble`'s own already-logged results, which stand
unaffected on their own terms.

The registration's own `stopping_rule` forecloses re-running this exact
spec or grid-searching any of its six external-convention constants —
consistent with this project's standing rule against
selection-after-seeing-a-result.

**Real, honest lessons for any future candidate, not rules this
document is empowered to set alone**:

1. A structurally-bounded-per-trade stop-loss (this task's own real,
   deliberate fix for `vwap-mid-reversion`'s disclosed gap) is
   necessary but demonstrably **not sufficient** on its own — a
   negative-edge signal traded tens of thousands of times against
   fixed, non-compounding position sizing can still produce a
   catastrophic aggregate result. A future candidate with a real risk
   control should not treat that alone as "risk solved."
2. Order-flow imbalance's real-world transfer to a scalping-scale
   (15-minute lookback, tens-of-minutes-to-hours holding) horizon for
   BTC is, on this one holdout run, weak — the PSR significance gate
   did not pass, so this is a real, disclosed data point consistent
   with the cited literature's own caveat, not an independent
   statistical confirmation of it. A real, quantified number for
   whoever next considers an order-flow-based candidate at a similar
   timescale: 17.98% win rate against a ~33.3% breakeven need, from
   this one run.
3. Whether this project's backtest engine should eventually gain a
   real, disclosed insolvency/circuit-breaker concept (halting further
   trading once cumulative losses pass some threshold, or sizing
   against real running equity rather than a fixed reference constant)
   is a real, open, project-wide design question this result makes
   more concrete than `vwap-mid-reversion`'s own already did — not
   decided or scoped here, a genuine candidate for a future,
   dedicated task given it would touch shared infrastructure
   (`backtest/engine.py`, `research/strategies/risk_management.py`)
   used by every strategy in this codebase, not just scalping
   candidates.

## Verification

- Real, single invocation confirmed via direct inspection of
  `runs/experiments.jsonl`: exactly one `holdout_access` record, exactly
  one holdout `backtest_run` record (`is_holdout_run: true`), plus the
  strategy's own diagnostic in-sample-scoring sub-record
  (`is_holdout_run: false`, `parent_run_id` pointing at the outer
  record) -- exactly the shape `OfiMomentumTrainable.fit()`'s own
  docstring describes.
- `data_range` in the logged record (`start_ms=1567965420000,
  end_ms=1787672220000, num_bars=3661780`) matches the registration's
  own declared window (`start_ms=1567965420000, end_ms=1787672280000`)
  under each field's own semantics, not literal equality: the
  registration's `end_ms` is an exclusive upper bound on the query,
  while the logged `data_range.end_ms` is the `open_time` of the actual
  last bar returned — one bar interval (60,000ms) earlier by
  construction for a half-open `[start, end)` range. The 60-second gap
  between the two values is this intended semantic difference, not a
  discrepancy.
- `code_version` in the logged record matches PR #115's real merge
  commit (`73c757c...`), confirming the run executed against the exact
  code that was reviewed and merged.
- Every number in this document's "The real result" section was read
  directly from the real logged record and the real command's own
  stdout, not recomputed or estimated.
- A committed, de-identified extract of the real logged records
  (`holdout_access` + `backtest_run`) lives in
  `.planning/scalp-s6-ofi-momentum-result-records.jsonl`, mirroring
  `.planning/scalp-s4-vwap-mid-reversion-result-records.jsonl`'s own
  established precedent for independent auditability despite
  `runs/experiments.jsonl` itself being gitignored.
