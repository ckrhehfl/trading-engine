# Strategy Research Task AA: registering a Binance-virgin-window replication of the daily-TSMOM hypothesis

## Scope note, stated as plainly as `sr-u`'s was

This task commits **a holdout config and a pre-registration**. It does
**not** execute either against real Binance holdout data, and it does
**not** call `research.holdout.load_holdout_klines`. Execution is a
separate, later task, which will load
`configs/research/holdout_1d_binance_virgin.json`'s holdout klines via
that function and run this registration exactly once -- exactly
mirroring how `sr-u` (registration) and `sr-v` (execution) were two
separate tasks for the original BingX-sourced `1d` holdout.

**The strategy holdout itself (the mechanism `load_holdout_klines`
gates) was never accessed while writing this task.** What *was*
accessed, and disclosed here rather than glossed over: the raw Binance
kline **price data** inside this window, via a real backfill run and a
real `python/data/store.py` query, in order to verify the real bar
count and gap status this task's own brief required ("verify the real
stored bar count... do not estimate it, read it for real"). This is a
data-pipeline access, not a strategy-holdout access -- no signal was
computed, no strategy was run, no `runs/experiments.jsonl` record was
written, and `research.holdout`'s single-access claim mechanism was
never invoked. The distinction matters and is treated carefully in
"How virgin is virgin, really" below, including one real caveat that
was not previously stated as precisely as it should have been.

## Where this sits in the project's sequence

`sr-v` (2026-07-30) executed `sr-u`'s pre-registered zero-fitted-
parameter daily time-series-momentum ensemble against BingX's own `1d`
early-window holdout (2021-05-14 through 2024-04-26, 1,079 bars):
**INCONCLUSIVE** -- PSR 0.9367 against a 0.95 threshold, observed
annualized Sharpe 0.882 against a 0.9567 detection floor, 26 trades
against a 53-trade floor. Per that registration's own pre-committed
meta-consequence, this ended the BTC-only price-signal research program
as a *searching* line of work and named two live remedies: multi-symbol
expansion (still open, needs its own `Discuss`), or a genuinely
different data source (partially pursued by `sr-x`/`sr-y`'s macro-data
attempts, both `INCONCLUSIVE-DATA-LIMITED`).

`sr-z` (2026-08-05) built and ran the Binance data pipeline as
infrastructure-only research (no strategy touched), and found real,
computed evidence that Binance spot BTCUSDT's `1d` history reaches back
to 2017-08-17 -- about 3.75 years deeper than BingX's own `1d`
retention floor of 2021-05-14. Crucially, `sr-z` also found the
overlap-period daily-close correlation between the two venues is
**0.999955** -- meaning post-2021 Binance data is not a materially
independent price series from BingX's own. The genuinely new
information Binance's deeper history provides is specifically the
**pre-2021-05-14 window**, which BingX simply does not have at all.

This task (`sr-aa`) is authorized directly by its own governing brief
(human-approved, 2026-08-05) to register -- not yet execute -- a true
independent replication of `sr-u`/`sr-v`'s exact hypothesis against
that pre-2021 window. This is explicitly framed as a **replication**,
not a new search: same signal, same code, same zero free parameters,
applied to data no trial in this project's history has ever touched,
on any exchange.

## Real numbers, verified directly, not estimated

The task brief was explicit that the real bar count, gap status, and
detection/trade floors had to be read off real data and real project
code, not assumed from the brief's own ballpark figures ("roughly
1,370", "roughly 68"). This worktree started with no local kline cache
at all (`sr-z`'s own worktree, where the original Binance backfill ran,
was already cleaned up after merging -- gitignored data directories are
not shared across worktrees or preserved after a worktree is removed).
So the first real step was re-running the backfill for real, not
reading a stale report.

**Backfill re-run**, scoped to exactly the candidate holdout's own
range, against the live Binance production endpoint
(`https://api.binance.com`), on 2026-08-05:

```text
PYTHONPATH=python python/.venv/bin/python -m data.backfill_binance \
  --market spot --symbol BTCUSDT --interval 1d \
  --start 2017-08-17T00:00:00+00:00 --end 2021-05-14T00:00:00+00:00 \
  --db-path python/data/var/klines.sqlite3
```

Result: **1,366 new rows inserted**, one gap fetched
(`[1502928000000, 1620950400000)`), ~0.6 seconds wall clock.

**Independent re-verification**, directly against `python/data/store.py`'s
own `fetch_klines`/`find_missing_ranges` (not a re-read of `sr-z`'s own
prior report -- a fresh query against this task's own freshly backfilled
local cache):

| Check | Result |
|---|---|
| Row count for `[1502928000000, 1620950400000)` | **1,366** |
| Expected count (half-open, `86,400,000ms` step) | 1,366 -- exact match |
| Earliest bar | `1502928000000` = **2017-08-17T00:00:00Z** |
| Latest bar | `1620864000000` = **2021-05-13T00:00:00Z** (one day before the exclusive cutoff, as expected for a half-open range) |
| Off-grid rows (`open_time_ms % 86,400,000 != 0`) | **0** |
| `find_missing_ranges` over the full span | **`[]`** (zero gaps) |
| Out-of-bounds rows (outside `[start_ms, end_ms)`) | **0** |
| Distinct consecutive-row time deltas | **`{86,400,000}`** exactly -- a third, independent confirmation of zero gaps and zero duplicates |

**1,366 bars, ~3.7425 years** (`1366 / 365`), matching the task brief's
own ballpark ("~1,370") closely but not exactly -- worth stating
precisely rather than silently reusing the brief's estimate, since the
whole point of this step was to stop estimating.

**Recomputed floors**, via this project's own functions (not
hand-derived): `research.retrospective.detection_floor_sharpe(3.74247)`
= **0.8502533257614421** (rounded to 0.8503, same 4-decimal convention
`sr-u` used); `research.preregistration.frequency_scaled_min_trades(
total_evaluated_bars=1366, bars_per_day=1)` = **68** (`floor(1366/20)
= 68`, inside the `[30, 100]` clamp). Both self-consistency checks
`research/run_preregistered_holdout.py` provides
(`verify_trade_floor`, `verify_detection_floor`) were run against the
committed registration after writing it and **both return `True`** --
confirming the registered `min_total_trades: 68` and
`declared_detection_floor_sharpe: 0.8503` are not just plausible but
exactly what the registration's own declared geometry recomputes to.

**Power derivation**, same normal-approximation method `sr-u`/`sr-r`
used: at `assumed_true_sharpe=1.0`, `power = Phi(sqrt(3.74247)*1.0 -
1.6448536) = Phi(0.28969) = 0.6140` (vs. `sr-u`'s own 0.5297 on the
shorter BingX window -- a real, mechanical consequence of the longer
span, not a new assumption). At `assumed_true_sharpe=2.0` (reported for
completeness, not the registered scalar): `Phi(2.22424) = 0.9869`.

## Cutoff-boundary choice, justified rather than assumed

The task brief suggested reusing `holdout_1d.json`'s own cutoff instant
(`1620950400000`, 2021-05-14T00:00:00Z) and asked that this be verified
as the right choice rather than taken on faith. Reasoning, checked
directly against the real project history rather than assumed:

1. **It is the earliest instant any trial in this project's history has
   ever accessed, on any exchange.** BingX's own `1d` retention begins
   there (`holdout_1d.json`'s own committed rationale); every one of
   this project's 1,839+ logged `backtest_run` records has
   `data_range.start_ms >= 1620950400000` (`sr-v`'s own holdout
   confirmation is the earliest, at exactly that instant). Choosing any
   *later* cutoff would needlessly discard real, still-virgin Binance
   data between 2021-05-14 and whatever later date was chosen. Choosing
   any *earlier* cutoff is structurally impossible -- 2017-08-17 is
   Binance's own real listing date for spot BTCUSDT (confirmed by `sr-z`
   and re-confirmed by this task's own backfill: a request for data
   before it returns nothing to fetch).
2. **It is not a fresh judgment call being made under this task's own
   incentive to find a favorable window.** The instant already exists,
   fixed, in a sibling committed config (`holdout_1d.json`), chosen for
   an unrelated reason (BingX's own retention floor) over a week before
   this task existed. Reusing it here is the *opposite* of cutoff
   shopping.
3. **It cleanly partitions real data with no edge-case ambiguity.**
   Verified directly (see the table above): the real backfill's last row
   lands exactly one day before the cutoff, with zero off-grid rows and
   zero gaps on either side of it.

One real subtlety worth naming: unlike `holdout_1d.json`'s own cutoff
(which sits inside a single continuous BingX series, splitting it into
a "before" and "after" half), this config's own cutoff sits at the
*start* of Binance data BingX-timeframe research has *also* already
studied (2021-05-14 onward, via BingX). The "after" side of this new
config is therefore not itself virgin -- see "Research side of this
config" below, and the config's own `rationale` field, for why this
does not matter for what this config is actually used for.

## How virgin is virgin, really -- a caveat stated more precisely than before

The task brief's framing ("this ~3.7-year window has never been touched
by any trial in this project's history, on any exchange") is correct
for the sense that matters most: no strategy backtest, no signal
computation, no research-selection trial, and no holdout-claim access
has ever touched this window. That is the property `research.holdout`'s
single-access mechanism actually protects, and it holds.

**But it is not literally true that zero bytes of price information
from this window have ever been read by this project**, and stating
that more precisely is worth doing now rather than letting a future
reader discover the gap. `sr-z`'s own early-era (2017-2019) data-quality
check -- built to verify the newly-backfilled Binance series was not
corrupted, stale, or artifactually flat before relying on it for
anything -- directly read real OHLC values from inside this exact span
and cross-referenced four of them (the 2017-12-17 ATH high, the
2018-12-15 bear-bottom low, the 2021-11-10 ATH high [technically just
past this window's own end, included in `sr-z`'s check as a boundary
sanity point], the 2022-11-21 cycle-bottom low) against independently
known historical BTC price levels. Those four figures are committed in
plain, readable text in `.planning/sr-z-binance-data-research.md`.

Judged, on reflection, **not to compromise this holdout for this
specific hypothesis**, for three reasons, all stated in the new
holdout config's own `rationale` field so they travel with the config
rather than living only here:

1. It was a **data-integrity** check (gap/stale-quote/plausibility
   verification) -- no strategy signal, parameter, threshold, or
   trading decision was chosen, tuned, or adjusted based on it. Nothing
   about the daily-TSMOM strategy's own design (its lookback set, its
   sizing convention, its order-emission rule) postdates or responds to
   that check in any way -- it was designed and committed at `sr-u`,
   before `sr-z` (and therefore before any Binance data of any kind)
   existed in this project.
2. The registered strategy is **zero-fitted-parameter and
   literature-specified** (Moskowitz-Ooi-Pedersen 2012's own canonical
   lookback set). There is no free parameter in this hypothesis that
   four spot-price levels could have informed even in principle -- the
   usual mechanism by which "peeking" compromises a holdout (a
   researcher nudging a threshold or lookback toward what they saw) has
   no surface to act on here.
3. The four disclosed price levels are themselves **independently,
   publicly well-known facts about BTC's own price history** (matched
   in `sr-z`'s own report against CoinDesk/Forbes-cited public figures),
   not information this project's own analysis discovered. Knowing "BTC
   hit ~$19,800 in December 2017" is common knowledge; it carries no
   information about this window's own sign pattern of 21/63/126/252
   trading-day returns, which is what the registered signal actually
   depends on.

This is disclosed as a real, honest caveat rather than omitted -- and
disclosed **twice**, once in the holdout config's own `rationale` (so it
travels with the artifact a future loader actually reads) and once here
(so the reasoning behind judging it non-disqualifying is fully
recorded, not just the fact of the caveat).

## Research side of this config -- also disclosed, not left implicit

`configs/research/holdout_1d_binance_virgin.json` defines both a
holdout side (before the cutoff) and, structurally, a research side (at
or after it) -- every holdout config does, by the shape of
`research.holdout`'s clamp logic. This config's own "research" side
(Binance data from 2021-05-14 onward) is **not virgin** and must never
be treated as such if a future task is tempted to call
`load_research_klines` against this config: `sr-z`'s own cross-exchange
correlation computation already read real Binance rows across exactly
that span (1,909 common days, 2021-05-14 through 2026-08-04). Nothing
in this project currently calls `load_research_klines` against this
config, and nothing should without first re-confirming that side's own
touched status. Stated in the config's own `rationale` field as well,
for the same "travels with the artifact" reason as the caveat above.

## The force_reclaim_reason pre-justification

`research.holdout.load_holdout_klines`'s single-access enforcement
(`_find_holdout_access`) scans `runs/experiments.jsonl` for **any**
`holdout_access` record matching a `strategy_id`, regardless of which
`holdout_config_path` was used. `sr-v` already recorded exactly one such
record for `strategy_id="daily-tsmom-ensemble"`. A future execution of
this registration will therefore hit `HoldoutAlreadyClaimedError` and
require a non-blank `force_reclaim_reason`.

Per this task's own instruction, that justification is pre-committed
**now**, inside the registration's own `stopping_rule` field, rather
than left for the execution task to invent after the fact:

> "Second holdout access for strategy_id=daily-tsmom-ensemble,
> deliberate and disclosed. This is a REPLICATION of the identical
> zero-fitted-parameter hypothesis sr-u/sr-v already ran once, against
> a DIFFERENT, INDEPENDENT, NEVER-BEFORE-TOUCHED data window... The
> strategy_id is intentionally reused rather than minting a fresh one,
> because this genuinely is the same hypothesis being replicated on
> independent data, not a second, different attempt dressed up to dodge
> the single-access guard."

**Why `strategy_id` was reused rather than minted fresh** (the task
brief's own instruction, reasoned through rather than just followed):
minting a new `strategy_id` would have sidestepped
`HoldoutAlreadyClaimedError` entirely -- no reclaim reason would ever be
required, and a reviewer scanning `runs/experiments.jsonl` in the
future would see two unrelated-looking single-access holdout claims
rather than one strategy's two disclosed attempts. That would be
technically easier and substantively dishonest: this really is the same
hypothesis (same code, same parameters, same signal) being tested a
second time. The honest path is the one that costs more friction
(a mandatory, logged, reviewable reclaim justification), not the one
that avoids the guard.

## Outcome-interpretation reconsideration -- the real judgment call this task had to make

This is the one piece of the registration that could not simply clone
`sr-u`'s own wording, and deserves its reasoning recorded in full
(per the task brief's own request).

`sr-u`'s original `INCONCLUSIVE`/`FAIL` text said that outcome "ends the
BTC-only price-signal research program as a line of work -- the next
move is a named structural change (multi-symbol expansion... or a
genuinely different data source entirely)." That sentence cannot simply
be repeated verbatim in this registration, because **this attempt
already is an instance of that named structural change** -- copying the
sentence unmodified would have this registration point at itself as its
own remedy, which is circular and says nothing.

The reasoning applied instead, recorded here so it is auditable rather
than asserted:

1. **What is actually being exhausted by this specific attempt.** This
   registration is not an open-ended instance of "try a different data
   source" -- it is specifically "try the same asset's price series on a
   different venue." `sr-z`'s own correlation finding (0.999955 daily-
   return correlation over the two venues' overlap) means that, for any
   *future* calendar period, Binance and BingX are not meaningfully
   independent data sources for this purpose -- a third exchange would
   very likely correlate just as tightly, for the same underlying-asset
   reason. What made *this* attempt genuinely new evidence was not "a
   different exchange" in the abstract; it was "an exchange whose
   history happens to reach into a calendar period BingX's own retention
   cannot reach at all." That specific opportunity is a one-time,
   spend-once resource: once this window is scored (in either
   direction), there is no other exchange this project could add that
   would extend the *pre-2021* record further, because Binance's own
   2017-08-17 listing date is very likely close to the practical floor
   for exchange-recorded BTC/USDT-denominated daily data at all.
2. **What this attempt does NOT exhaust.** `sr-u`'s original two named
   remedies were (2) multi-symbol expansion with survivorship-safe data,
   and (3) a genuinely different data source entirely. This attempt is
   *inside* remedy (3), narrowly construed as "alternate venue, same
   asset, same signal class." It says nothing about remedy (2)
   (expanding to other symbols), and nothing about a genuinely different
   *asset class or signal class* (on-chain data, which `sr-y`'s own
   closing text named as the next possible pivot). Both stay open,
   undecided, human-`Discuss` questions -- this registration's own
   `outcome_interpretation` says so explicitly, in both the
   `INCONCLUSIVE` and `FAIL` regions, so a future reader does not
   over-read either outcome as closing more than it actually does.
3. **What a PASS would mean, reasoned symmetrically.** A `PASS` here,
   combined with `sr-v`'s own near-miss (PSR 0.9367, Sharpe 0.882 --
   both just below their own bars, not far below them), would be
   materially stronger evidence than either alone: the same
   zero-parameter signal showing a positive, statistically credible
   result in two structurally different market regimes (2017-2019
   thin-liquidity/retail/ICO-mania-to-crypto-winter vs. 2021-2024
   institutional-bull-to-bear) is a real consistency check a single
   window's own PSR cannot provide by itself -- the same logic this
   project already applies to fold-level consistency within one
   walk-forward run, extended here across two independent holdout
   windows. This is stated in the registration's own `PASS` text, and
   is explicitly still not automatic promotion, matching `sr-u`'s own
   framing for its `PASS` region.

## A real, disclosed labeling consequence found while reading the strategy code closely

The task brief asked that `daily_tsmom_ensemble.py` be read closely
enough to flag a genuine bug if one turned up, "unlikely" though that
was expected to be. No bug was found, but reading `fit()` and
`Preregistration.run_params()` together surfaced a real, worth-recording
consequence of this registration's own `data.symbol` choice, not
present in `sr-u`'s original attempt.

`data.symbol` does **not** control which klines get loaded for a
holdout run -- that is entirely the holdout config's own `symbol` field
(`load_holdout_klines` reads `config["symbol"]`, from
`holdout_config_path`, never from the registration's `data` block
directly). `data.symbol` instead flows, via
`Preregistration.run_params()`'s `"symbol"` key, into
`DailyTsmomEnsembleTrainable.fit()`'s `params.get("symbol",
DEFAULT_SYMBOL)`, and from there into `DailyTsmomEnsembleStrategy`'s own
constructor -- meaning it ends up as the `symbol` field on every
`OrderIntent` (and, downstream, every `Fill`) a real execution of this
registration would emit.

This registration sets `data.symbol = "BINANCE:BTCUSDT"`, matching the
holdout config's own `symbol` field exactly (the same consistency
`sr-u`'s original registration had between its own `data.symbol` and
`holdout_1d.json`'s `symbol`, both `"BTC-USDT"`). The consequence: a
real execution of this registration will produce `OrderIntent`/`Fill`
records labeled `"BINANCE:BTCUSDT"`, not `"BTC-USDT"` the way every
other run in this project's history is labeled.

**Confirmed this is a labeling-only consequence, not a computational
one**: `symbol` is never read, compared, or filtered on anywhere in
`backtest/engine.py` or `metrics/*.py` (checked directly --
`grep -rn '\.symbol\b'` across both returns nothing). It cannot change
any computed Sharpe, P&L, drawdown, or trade count.

**Judged correct, not merely harmless, and left as-is rather than
overridden to `"BTC-USDT"` for cosmetic consistency**: this backtest is,
literally, scoring Binance's own price series, not BingX's. Labeling
its resulting fills `"BTC-USDT"` would misrepresent which real market
data actually produced them -- a false consistency with `sr-u`/`sr-v`'s
own BingX-labeled runs, precisely the kind of appearance-over-substance
choice this project's disclosure conventions exist to avoid. A future
reader of this attempt's logged record (once executed) should expect
`"BINANCE:BTCUSDT"` in its `params`/`OrderIntent` fields and not read
that as an error.

## What was built (and what deliberately was not)

| File | Change |
|---|---|
| `configs/research/holdout_1d_binance_virgin.json` | **new.** The holdout config: `symbol="BINANCE:BTCUSDT"`, `interval="1d"`, `holdout_side="before"`, `holdout_cutoff_ms=1620950400000` (reused from `holdout_1d.json`), with a `rationale` field matching that config's own rigor -- real verified numbers, the cutoff-boundary justification, the "how virgin is virgin" caveat, and the "research side is not virgin" disclosure. |
| `configs/research/preregistrations/daily-tsmom-ensemble-binance-virgin-holdout.json` | **new.** The registration: same `strategy_id`/`strategy_entry_point`/`parameter_grid`/`total_candidates`/`free_parameter_count` as `sr-u`'s original (the code is unchanged), real recomputed `declared_detection_floor_sharpe` (0.8503) and `primary_criterion.min_total_trades` (68) for this window's own real bar count, a rewritten `hypothesis` framing this as a replication, and a reasoned (not copy-pasted) `outcome_interpretation`. |
| `.planning/sr-aa-binance-virgin-window-registration.md` | this document. |

**No code was written.** The bar-count/gap verification used
`python/data/store.py`'s existing `fetch_klines`/`find_missing_ranges`
and `python/data/backfill_binance.py`'s existing CLI directly, via a
one-off interactive script -- the same "not committed to the repo, this
task's scope is configuration/registration, not a permanent analysis
tool" precedent `sr-z`'s own "Analysis methodology" section already
established for exactly this kind of one-time verification. No test
file was added, because no new production code exists for a test to
cover; the two new JSON artifacts are exercised entirely by this
project's existing, generic validation machinery (see "Verification"
below) exactly the way every other committed registration and holdout
config already is.

**Not modified**: `research/strategies/daily_tsmom_ensemble.py` (the
strategy code -- read closely, no bug found, reused byte-for-byte),
`research/lineage.py` (the `"daily-tsmom-ensemble"` family entry already
exists, citing `sr-u`; this task's own strategy_id is unchanged, so no
new curated entry is needed), `configs/research/holdout_1d.json` (the
original BingX holdout config), and `runs/experiments.jsonl` (never
opened for writing by this task -- only read, indirectly, by the
pre-existing `verify_trade_floor`/`verify_detection_floor` sanity checks
below, which don't touch it at all since they recompute purely from the
registration's own declared fields).

## Verification actually run

- **`load_preregistration('configs/research/preregistrations/daily-tsmom-ensemble-binance-virgin-holdout.json')`**
  -- loads and validates cleanly (`validate_preregistration` runs
  internally; a malformed or under-strict registration would raise
  `PreregistrationError`). Confirmed: `preregistration_id`,
  `strategy_id="daily-tsmom-ensemble"`,
  `strategy_family="daily-tsmom"`, `total_candidates=1`,
  `criterion_kind="holdout_psr"`, `expected_fold_count=None` (correct
  for a holdout registration -- no folds), `is_holdout_confirmation=True`.
- **`load_holdout_config('configs/research/holdout_1d_binance_virgin.json')`**
  -- loads and validates cleanly (`resolve_holdout_side` runs
  internally). Confirmed `holdout_side="before"`,
  `symbol="BINANCE:BTCUSDT"`, `interval="1d"`,
  `holdout_cutoff_ms=1620950400000`.
- **`research.run_preregistered_holdout.verify_trade_floor(prereg)`** ->
  `True` -- the registered `min_total_trades: 68` matches the
  recomputed frequency-scaled floor for this registration's own declared
  geometry exactly.
- **`research.run_preregistered_holdout.verify_detection_floor(prereg)`**
  -> `True` -- the registered `declared_detection_floor_sharpe: 0.8503`
  matches the recomputed value for this registration's own declared
  geometry within tolerance.
- **`cd python && uv run pytest`** -- full suite, including
  `test_preregistration.py::test_every_committed_registration_validates`,
  which globs every file in `configs/research/preregistrations/` and
  picks up this task's new file automatically (parametrized by its
  filename stem). Real result: **1,370 passed**, up from `sr-z`'s own
  final count of 1,369 -- exactly the `+1` expected from the one new
  parametrized case this task's new registration file adds, and nothing
  else. Nothing regressed, nothing skipped or xfailed. (This worktree
  had no prior venv or local kline cache -- `sr-z`'s own worktree, where
  the original Binance backfill ran, was already cleaned up after
  merging -- so both had to be rebuilt from scratch in this task before
  any of the above could run for real: `uv sync` inside `python/`, then
  the real backfill described above.)

## CodeRabbit review: two real findings, both fixed in the registration text

CodeRabbit's review of this PR (targeting the exact commit that added
these two files) returned `CHANGES_REQUESTED` with two findings. Both
were checked against the real code before accepting them (per this
project's standing "verify each finding against current code" review
convention), found genuinely valid, and fixed by editing the
registration's own prose -- neither required a code change.

1. **`INCONCLUSIVE` and `FAIL` were not mutually exclusive as written.**
   The original `INCONCLUSIVE` text's second and third `OR` clauses
   ("Sharpe fails to exceed the detection floor", "trades fall below the
   floor") did not explicitly require PSR to be positive, so a
   non-positive-PSR result could, read literally, satisfy both the
   `INCONCLUSIVE` and `FAIL` prose simultaneously. Checked directly
   against `research/run_preregistered_holdout.py::evaluate_gating`'s
   real code (reproduced above) to confirm what the actual precedence
   is rather than guessing: `elif psr_result.psr is None or
   psr_result.psr <= 0: outcome = OUTCOME_FAIL` runs **before** the
   `else: outcome = OUTCOME_INCONCLUSIVE` branch -- so a non-positive PSR
   is *always* `FAIL` in the real code, regardless of the other four
   checks. **Fixed** by rewording `INCONCLUSIVE` to open with "PSR is
   POSITIVE (> 0) but..." and adding an explicit "MUTUALLY EXCLUSIVE
   WITH FAIL BY CONSTRUCTION, matching `evaluate_gating`'s own real
   precedence" sentence to both regions. This is the same ambiguity
   `sr-u`'s own original `1d`-holdout registration also had (verified by
   re-reading it) -- not introduced by this task, but worth fixing here
   regardless of where it first appeared, since a registration's whole
   value is being unambiguous before a result exists.
2. **"Exactly one execution" overstated what the mechanism actually
   guarantees.** `research.holdout.load_holdout_klines`'s
   `force_reclaim_reason` is deliberately permissive (declined-to-tighten,
   tracked as issue #58 per `sr-v`'s own account) -- it accepts any
   non-blank reason, including one reclaiming an access that already
   completed normally. So a *third* access to this `strategy_id`,
   beyond the second one this registration itself represents, remains
   mechanically possible, not blocked. **Fixed** by adding an explicit
   paragraph to `stopping_rule` naming this limit directly (mirroring
   how `sr-u`'s own `stopping_rule` already handled the *first*-to-second
   version of this same honesty requirement: "any such reclaim is itself
   a second access, which this registration's own stopping rule treats
   as already having happened, not as license for a second real
   attempt") -- extended here one level further, to a hypothetical third
   access.

Both fixes are prose-only; neither changes `declared_detection_floor_
sharpe`, `min_total_trades`, the data window, or any other field
`verify_trade_floor`/`verify_detection_floor` check -- both re-run clean
after the edit (`True`/`True`), and `load_preregistration` still loads
and validates without error (new `sha256`, since the file's bytes
changed: `1349013a...`).

## Deliberately out of scope (restated from the task brief, for the record)

- **Executing this registration against real Binance holdout data,**
  i.e. ever calling `research.holdout.load_holdout_klines` with
  `i_understand_this_is_holdout_data=True` against this config. That is
  a separate, later task, exactly mirroring `sr-u` -> `sr-v`.
- **Modifying `daily_tsmom_ensemble.py`.** Read closely during this
  task (per the brief's own instruction to check for a genuine bug
  before assuming none exists) -- no bug found; the module is reused
  entirely unchanged.
- **Modifying `configs/research/holdout_1d.json`** (BingX's own config)
  or any other existing holdout/preregistration file.
- **Touching `runs/experiments.jsonl`** directly, in any way.
- **Resolving the broader (2) multi-symbol-expansion vs. (3)
  genuinely-different-data-source question.** This task is one
  specific, pre-authorized instance inside (3); the broader choice
  remains a human `Discuss`, unresolved here, exactly as `sr-x`/`sr-y`
  left it.
