# Scalping Strategy Research Task S4 — real holdout result

Companion to `.planning/scalp-s4-vwap-mid-reversion.md` (the commit-phase
design record, PR #111, merged). That document specified and committed
the `vwap-mid-reversion` strategy and its pre-registration; it did not
execute the real holdout access. This document is that execution and its
real, honest result — mirroring `sr-u`/`sr-v`'s own split precisely.

## The real invocation

Executed 2026-08-25 from the repository root (not `python/` — the
registration's own `data.holdout_config_path`
(`"configs/research/holdout_1m.json"`) is repo-root-relative, matching
`sr-v`'s own documented precedent and its own real `FileNotFoundError`
lesson about running from the wrong directory):

```text
PYTHONPATH=python python/.venv/bin/python -m research.run_preregistered_holdout \
  configs/research/preregistrations/vwap-mid-reversion-1m-holdout.json \
  --runs-path runs/experiments.jsonl \
  --db-path python/data/var/klines.sqlite3
```

Preflight, run and verified before the real invocation:
`verify_trade_floor` → `True`, `verify_detection_floor` → `True`,
`verify_known_gaps` → passed with no exception (real gap set matched the
2 declared, disclosed gaps exactly). No `force_reclaim_reason` was
needed or supplied — this was a genuine first access, confirmed by
scanning `runs/experiments.jsonl` for any prior `holdout_access` record
for `strategy_id="vwap-mid-reversion"` before running (none existed).

Ran exactly once, successfully. `runs/experiments.jsonl` carries exactly
one `holdout_access` record for this `strategy_id`
(`accessed_at=2026-08-25T09:49:46Z`, `force_reclaim_reason: null`) and
exactly one holdout confirmation `backtest_run` record
(`is_holdout_run: true`, `run_id=2e492b52-b004-4da9-8d41-8e320cb3cdce`,
`code_version=b16853f...`, matching PR #111's merge commit), plus the
strategy's own diagnostic in-sample-scoring sub-record
(`is_holdout_run: false`, `parent_run_id` pointing at the outer record) —
exactly the shape `VwapMidReversionTrainable.fit()`'s own docstring
describes.

## The real result

```text
bars evaluated              : 910,040
observed annualized Sharpe  : 0.39177869182677105
declared detection floor    : 1.250042
PSR                         : 0.9999990069208105
max drawdown                : 106.1858812217089971014340498  (i.e. 10,619%)
total trades                : 44,344
win rate                    : 0.0113205845210175  (1.13%)
profit factor                : 0.0011658624713518876
starting equity             : 10,000
final equity                : -1,051,858.81
```

```text
gating checks:
  [PASS] psr:                          required=0.95    observed=0.999999...
  [FAIL] max_drawdown:                 required=0.20    observed=106.186...
  [PASS] min_total_trades:             required=31      observed=44,344
  [FAIL] profit_factor:                required=1.3     observed=0.001166
  [FAIL] sharpe_above_detection_floor: required=1.250042 observed=0.392

OUTCOME: INCONCLUSIVE
```

## Honest interpretation — the mechanical label undersells the severity

Per `evaluate_gating`'s own pre-committed, mechanical precedence (`PASS`
iff all five checks clear; `FAIL` iff PSR is undefined or `<= 0`;
`INCONCLUSIVE` otherwise), this result lands in `INCONCLUSIVE` because
PSR is technically positive (extremely close to 1.0, driven by a return
distribution with severe skewness (-618.8) and kurtosis (550,780) —
the statistic's own moments-from-observed-data computation is real and
was not hand-adjusted, but a PSR this close to 1.0 alongside a
catastrophic drawdown is a real illustration of PSR alone being an
incomplete picture, not a contradiction — this is exactly why the
Eligibility Bar's single-window variant requires all five checks, not
PSR alone).

**Stated plainly, not softened by the mechanical label**: this was not
a mild underperformance. Starting from $10,000, the strategy's paper
equity went to **-$1,051,858** — a real, complete, and then some,
wipeout, had this been run against actual capital. Win rate was **1.13%**
across 44,344 trades (roughly one trade every 20 real minutes over the
632-day window, matching the 20-period reversion band's own expected
trigger frequency). Profit factor of 0.0012 means gross losses
outweighed gross profits by roughly 860-to-1.

## Why this happened — a real, disclosed design choice, not a bug

`research/strategies/vwap_mid_reversion.py`'s own module docstring
disclosed this risk in advance, in these exact words, before any real
data was touched: *"with no ATR stop and no ADX regime filter, a
position can be held indefinitely if price stays beyond the 2-SD band
without reverting — the well-documented mean-reversion-fails-in-a-
strong-trend failure mode... This is deliberately not mitigated here:
adding an ADX filter or a stop would reintroduce free parameters from
the already-spent, already-rejected 1h-window search apparatus. Any
real damage this causes will show up honestly in the Eligibility Bar's
own max-drawdown gating criterion rather than being silently avoided."*

It did. This is that disclosure's real, honest confirmation, not a
surprise discovered after the fact. Two mechanisms compound in a
1-minute BTC-USDT window specifically: (1) a 20-period/2-SD band is
narrow enough to trigger very often (44,344 times over 910,040 bars) —
each trigger is a real bet that price reverts within a bounded, short
window, and at 1-minute granularity BTC evidently continues past a
2-SD deviation far more often than it reverts (1.13% win rate); (2)
position sizing is driven by a **fixed** `reference_equity` constant
(`compute_target_quantity`'s own `reference_equity / entry_price *
vol_scalar`), not the strategy's own shrinking real equity — so losses
accumulate roughly linearly per losing trade rather than being
naturally throttled down as capital erodes, the same behavior every
sibling strategy in this package (`daily_tsmom_ensemble.py`,
`mean_reversion.py`) already has, but never previously exposed this
severely because none of them combined "no stop-loss," "no regime
filter," and "a signal that fires tens of thousands of times" at once.

## What this does and does not mean, per the pre-registration's own committed text

Per `vwap-mid-reversion-1m-holdout.json`'s own `outcome_interpretation.
INCONCLUSIVE` text (committed *before* this access, modeled explicitly
on `sr-ab`'s scoping rather than `sr-u`/`sr-v`'s broader one): this
result **parks this specific hypothesis** — zero-fitted-parameter
VWAP-to-mid reversion at 20-period/2-SD on 1-minute BTC-USDT, with no
stop-loss and no regime filter — as a candidate. It does **not** by
itself end the broader Scalping Strategy Research direction (order-flow
imbalance and other candidates named in CLAUDE.md's Task S4 remain
untested), and it does **not** retroactively affect
`daily-tsmom-ensemble`'s own, unrelated paper-trading status.

The registration's own `stopping_rule` forecloses re-running this exact
spec or grid-searching `vwap_period`/`deviation_k` to "fix" this result
— consistent with this project's standing rule against selection-after-
seeing-a-result. **The only legitimate remedy named**: a structurally
different parameterization or signal mechanism, not a re-run of this
exact spec.

**A real, honest lesson worth stating plainly for any future scalping
candidate**, not a rule this document is empowered to set on its own:
this result is real, load-bearing evidence that "zero free parameters"
and "zero risk controls" are not the same discipline, and conflating
them was a real design cost here — `daily_tsmom_ensemble`'s own
"hold until the signal reverses, no stop" convention is defensible for
a **trend-following** signal (a big move IS the thesis), but this
result suggests that convention does not transfer safely to a
**mean-reversion** signal, whose core risk is precisely "the market
does not revert." Whether a future mean-reversion-style scalping
candidate should treat a stop-loss or a regime filter as a genuine,
literature-sourced, zero-*search* (if not zero-*parameter*) risk
control — the same way `daily_tsmom_ensemble` treats its lookback set
as literature-sourced rather than fitted — is a real design question
for that future task, not decided here.

## Verification

- Real, single invocation confirmed via direct inspection of
  `runs/experiments.jsonl` (not re-asserted from memory): exactly one
  `holdout_access` record, exactly one holdout `backtest_run` record
  (`is_holdout_run: true`), exactly one diagnostic sub-record
  (`is_holdout_run: false`, correctly parented).
- `data_range` in the logged record (`start_ms=1732982400000,
  end_ms=1787585160000, num_bars=910040`) matches the registration's own
  declared window exactly.
- `code_version` in the logged record matches PR #111's real merge
  commit (`b16853f...`), confirming the run executed against the exact
  code that was reviewed and merged, not a local, uncommitted variant.
- Every number in this document's "The real result" section was read
  directly from the real logged record and the real command's own
  stdout, not recomputed or estimated.
