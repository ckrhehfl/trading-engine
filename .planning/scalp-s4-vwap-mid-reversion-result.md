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
bars evaluated               : 910,040
observed annualized Sharpe   : 0.39177869182677105  (bar-level, annualized)
declared detection floor     : 1.250042
PSR                          : 0.9999990069208105
max drawdown                 : 106.1858812217089971014340498  (i.e. 10,619%)
total trades                 : 44,344
win rate                     : 0.0113205845210175  (1.13%)
profit factor                : 0.0011658624713518876
starting equity               : 10,000
final equity (see caveat below) : -1,051,858.81
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

**PSR's own input series, documented precisely (real CodeRabbit review
finding — a first version of this document conflated two different
statistics, corrected here rather than left wrong):**
`psr_from_equity_curve` resamples `metrics.equity_curve` to **daily**
granularity (`bars_per_day=1440`, `sampling="daily"`) before computing
PSR — a **different, smaller** series than the 910,039 raw bar-level
returns `return_skewness`/`return_kurtosis` describe. The real,
logged `psr` sub-object (`runs/experiments.jsonl`,
`run_id=2e492b52-b004-4da9-8d41-8e320cb3cdce`, reproduced verbatim in
`.planning/scalp-s4-vwap-mid-reversion-result-records.jsonl`):

```text
psr.num_observations : 630        (daily-resampled points, not 910,039)
psr.sampling          : "daily"
psr.benchmark_sharpe   : 0.0
psr.moments_source     : "observed"
psr.sharpe_ratio       : 0.060432567693992356   (DAILY Sharpe -- not the
                                                   0.392 annualized figure
                                                   above, a different
                                                   quantity entirely)
psr.skewness           : 23.679371604107992
psr.kurtosis           : 584.3496557847623
psr.z_score            : 4.754827593755083
psr.psr                : 0.9999990069208105
```

The bar-level `return_skewness=-618.82`/`return_kurtosis=550780.21`
(910,039 raw per-bar observations) are a **separate, real, logged**
statistic — genuinely computed, not invented — but they are **not**
what PSR was evaluated against; an earlier version of this document
incorrectly cited them as PSR's own moments. Both series are real; they
describe different things, and only the daily-resampled one feeds PSR.

## Honest interpretation — the mechanical label undersells the severity

Per `evaluate_gating`'s own pre-committed, mechanical precedence (`PASS`
iff all five checks clear; `FAIL` iff PSR is undefined or `<= 0`;
`INCONCLUSIVE` otherwise), this result lands in `INCONCLUSIVE` because
PSR is technically positive (extremely close to 1.0, on a 630-point
daily-resampled return series with real skewness 23.68/kurtosis 584.35
— see the exact figures above). A PSR this close to 1.0 alongside a
catastrophic drawdown is a real illustration of PSR alone being an
incomplete picture, not a contradiction — this is exactly why the
Eligibility Bar's single-window variant requires all five checks, not
PSR alone.

**A real, separate caveat on `final_equity`/`max_drawdown`, tightened on
the same review pass**: `backtest/engine.py::run_backtest` never passes
equity or any portfolio state to the `Strategy` callable at all (its own
type signature: `Callable[[Sequence[Kline]], OrderIntent | None]`) — so
`VwapMidReversionStrategy` has no way to know real cumulative P&L, and
keeps sizing every new position off the **fixed** `reference_equity`
constant regardless of how deep prior losses went. There is no
insolvency or margin-call concept anywhere in this engine — a real,
disclosed, pre-existing characteristic of every strategy in this
package, not unique to this one. **Consequence for how to read
`final_equity=-$1,051,858`**: this is not "the literal dollar loss a
real leveraged account would have sustained" — a real account would
have been liquidated by its exchange long before paper equity went
negative, well short of this raw figure. It is the backtest's raw,
uncapped cumulative P&L sum, useful as a **severity signal** (how bad
the aggregate edge is, in aggregate) rather than a literal number. The
qualitative conclusion is unaffected — a real account trading this
exact spec would still have been wiped out, just earlier and by a
smaller, margin-bounded amount — but the number itself needed this
precise a framing, not the more literal one an earlier version of this
document used.

**Stated plainly, not softened by the mechanical label, and precise
about what the raw number does and doesn't show (see the engine caveat
above)**: this was not a mild underperformance. The strategy's raw,
uncapped paper equity fell from $10,000 to **-$1,051,858** — a severity
signal, not a literal dollar figure a real leveraged account would have
reached, since this engine has no margin/insolvency concept and a real
account would have been liquidated, and this trading stopped, long
before reaching this point. Win rate was **1.13%** across 44,344 trades
(roughly one trade every 20 real minutes over the 632-day window,
matching the 20-period reversion band's own expected trigger frequency).
Profit factor of 0.0012 means gross losses outweighed gross profits by
roughly 860-to-1 — a ratio, unlike the raw equity figure, that stays
meaningful regardless of the margin caveat.

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
- **Independently auditable without trusting this document's own prose**
  (real CodeRabbit review finding, closed by precedent — `runs/
  experiments.jsonl` itself is gitignored, same as every prior holdout
  result in this project, so a committed, de-identified extract is the
  established way to make a result independently checkable; mirrors
  `.planning/sr-y-appended-log-records.jsonl`'s own precedent for the
  same reason): `.planning/scalp-s4-vwap-mid-reversion-result-records.jsonl`
  carries the real, complete, losslessly normalized `holdout_access` record and both
  real `backtest_run` records (the outer holdout confirmation and its
  inner diagnostic sub-record) this document's numbers were read from —
  no raw trading logs, secrets, or account identifiers anywhere in it
  (pure research metadata: strategy hyperparameters, metrics, timestamps,
  the git commit SHA, and the preregistration's own SHA-256).
