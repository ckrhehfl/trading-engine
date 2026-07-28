# Strategy Research Task O: funding-extremity fold-boundary state-seeding
fix

## Scope note

Strategy Research Task N built the first funding-rate-based strategy in
this project (`FundingExtremityStrategy`/`FundingExtremityTrainable`,
`.planning/sr-n-funding-rate-strategy.md`) and ran it through a real
19-fold walk-forward: only **7 trades** fired despite **71 real crossings**
into an extreme funding reading. Task N diagnosed the mechanism precisely
but explicitly did not fix it ("Not fixed in this task... flagged as a
concrete, scoped candidate follow-up instead"). This task's job is to fix
that mechanism and get a fair, honest re-read of the strategy on a larger,
more representative trade sample -- not to force a better-looking number.

Every real number below comes from an actual walk-forward run against the
real cached BingX 1h data this project's other 1h strategies already use
(`configs/research/holdout_1h.json`: 16,078 research klines,
2024-04-27T10:00:00Z -> 2026-02-26T07:00:00Z; 5,744 funding rows, same
cutoff), `train_bars=2160, validate_bars=720, step_bars=720` (19 folds),
`fee_bps=5, slippage_bps=2, bars_per_day=24` -- the exact same
configuration Task N used, for a valid before/after comparison. Nothing
here is estimated or hand-derived.

## Step 1: re-deriving the mechanism from real data

Task N's own diagnosis (`sr-n`, "Diagnosing the 7-trade result") named the
cause as "the edge-trigger design... requires the signal to flip all the
way to the opposite extreme, starting from a fresh (`None`) state at the
beginning of every fold's 30-day validate window." This task's brief
summarized this one level up, as "the rolling funding z-score's state
resets at each walk-forward fold's start." Before touching any code, this
task re-derived the mechanism directly against real data (throwaway
diagnostic script, same "written once, run once, deleted" convention as
every real-run script in this project) to confirm PRECISELY which piece of
state is actually cold -- because "the z-score's state" and "the
edge-trigger tracker's state" are two different fields in
`FundingExtremityStrategy`, and conflating them would risk fixing the
wrong thing.

**Finding: the z-score's own rolling window is NOT cold at any fold
boundary.** `research.walkforward.run_walk_forward` builds a brand-new
`FundingExtremityStrategy` for every fold via
`FundingExtremityTrainable.fit()` (`_build_strategy()`), and that fresh
instance is evaluated ONLY against that fold's own `validate_klines` --
never `train_klines`. But `RollingFundingZScore` is always constructed
with the FULL historical `funding_rates` series (per `sr-n`'s own design,
loaded via `load_research_funding(start_ms=0, ...)`), and its internal
cursor fast-forwards through all of that real history on the very first
`update()` call, regardless of how "fresh" the instance is. Checked
directly: at every one of the 19 real fold boundaries, a brand-new
`RollingFundingZScore` instance returns a real, non-`None` z-score reading
immediately -- e.g. fold 0's validate window begins 2024-07-26T10:00:00Z
and its very first z-score reading is `0.560...`; fold 18's begins
2026-01-17T10:00:00Z with first reading `-0.842...`. Zero folds show a
cold/`None` first reading. The rolling window was never actually the
problem.

**Finding: `_signal_state` (the edge-trigger tracker of "the last
established nonzero extremity direction") IS cold at every fold
boundary, and this is the real cause.** A fresh `FundingExtremityStrategy`
always starts `_signal_state` at `None` (`FundingExtremityStrategy.
__init__`), discarding real knowledge of whichever direction funding was
most recently extreme in before this fold began -- even though that
knowledge is fully determinable from real history and would not be a
look-ahead violation to use. Checked directly: replaying the real
funding-derived signal continuously (never resetting) across the entire
research window shows a legitimate, well-defined, non-`None` "would-be"
`_signal_state` exists at literally every one of the 19 fold boundaries
(e.g. `+1` at fold 0's start, `-1` at fold 3's, ...) -- today's code
discards all 19 of them and starts every fold from scratch.

**Finding: this confirms the bottleneck is a state-reset problem, not a
longer-natural-cycle-time problem** (the brief's other candidate
explanation). Re-running Task N's own "raw crossings vs. opposite-
direction flips" diagnostic with the real per-fold-reset boundaries
reproduces its published numbers exactly -- **71 raw crossings, 7
opposite-direction flips** -- confirming this diagnostic methodology is
faithfully understood before changing anything. The 71-to-7 gap is
explained entirely by the reset: the FIRST crossing within every fold's
30-day validate window is always wasted just re-establishing a baseline
(no prior state to differ from), and only a flip to the OPPOSITE extreme
within the REMAINING span of that SAME fold can ever fire a trade --
funding's demonstrated regime-persistence (`sr-n`) makes that rare. This
is purely a harness/state-management artifact, not evidence about
crossing frequency or natural cycle time -- the 71 real crossings
themselves are frequent (~3.7/fold on average); it's the fresh-`None`
reset consuming most of them as "baseline-only" that suppresses trades.

## The fix

`initial_signal_state` (new, optional `FundingExtremityStrategy`
constructor parameter, default `None` -- preserves byte-for-byte identical
behavior for any caller that doesn't pass it, including every existing
test) lets a caller seed `_signal_state` at construction time, instead of
always starting fresh.

`compute_seed_signal_state` (new module-level function in
`funding_extremity.py`) computes the CORRECT seed value from real history:
it replays `RollingFundingZScore`/`_funding_signal` over `funding_rates` up
to (and including) a `known_through` cutoff, returning the last nonzero
direction observed -- exactly what a strategy that had been running
continuously since real funding history began would have accumulated by
that point, using nothing but genuinely earlier real settlements (never
fabricated, never the fold's own future data).

`FundingExtremityTrainable.fit()` calls this once per fold:
`train_klines[-1].open_time` (the last bar of THIS fold's own train
window) as `known_through` for the strategy it returns for real
`validate_klines` evaluation -- since validate begins immediately after
train ends, this captures all real history through the moment evaluation
begins, including crossings that happened during the fold's own train
window (not just history from before the fold). `train_klines[0].
open_time` is used for the separate in-sample `scoring_strategy` (used
only for `_log_candidate`'s diagnostic logging), so even that pass reflects
genuine warmup rather than a cold start -- consistent with Task N's
existing "honest in-sample figures" precedent for funding P&L.

**Look-ahead safety.** `fit()` never receives `validate_klines` at all
(`TrainableStrategy.fit`'s own docstring already requires "must not read
`validate_klines` in any way"), so the seed can only ever reflect data
already known by the time evaluation begins. Whether the `known_through`
boundary treats an exactly-coincident settlement as included or excluded
does not matter in practice: any settlement at or after a strategy's own
first evaluated bar gets independently (and identically) reprocessed by
that strategy's real per-bar `RollingFundingZScore.update()` call anyway,
so the seed and the real evaluation loop always converge on the same
`_signal_state` regardless of which side of the boundary it's counted on.
Verified directly by a dedicated test proving a settlement timed after
`train_klines[-1].open_time` never changes the seed `fit()` computes for
the strategy it returns, even when `self._funding_rates` (deliberately, per
this module's design) extends arbitrarily far beyond it.

## TDD

Written first, confirmed to fail against the pre-fix code (collection
error: `compute_seed_signal_state` did not exist), then implemented, then
confirmed to pass:

- `TestComputeSeedSignalState` (4 tests): warmup/no-crossing `None`
  handling, "returns the LATEST nonzero direction, not the first",
  look-ahead safety (a settlement after `known_through` must not affect
  the result).
- `TestInitialSignalStateSeeding` (4 tests): default `None` is backward-
  compatible; the exact fold-boundary bug reproduced directly at the
  strategy level (a lone crossing never fires unseeded, but fires
  immediately once seeded with the correct opposite prior state); seeding
  with the SAME direction does not spuriously fire.
- `TestFitSeedsFromRealPreFoldHistory` (3 tests): `fit()`'s returned
  strategy is seeded from real pre-fold history ending at `train_klines[-1]
  .open_time`; a settlement at/after that bar is provably ignored
  (look-ahead safety, at the `fit()` integration level, not just the
  `compute_seed_signal_state` unit level); the in-sample scoring pass is
  seeded consistently too (verified via a constructor spy).

One real gotcha found and fixed while writing these: a rolling
**sample**-stdev z-score of an "n-1 identical values + 1 outlier" window
has outlier magnitude capped at EXACTLY `(n-1)/sqrt(n)`, regardless of how
extreme the outlier's actual value is (a real property of the sample-stdev
formula, not a test design mistake) -- for `n=3`, that cap is `~1.155`,
mathematically incapable of ever crossing this project's `threshold=2`
tests no matter the magnitude used. Early test fixtures using
`lookback_settlements=3` failed for exactly this reason (not a logic bug
in the fix); switched to `lookback_settlements=10` (cap `~2.846`,
comfortable margin) once diagnosed. Documented directly in the new test
helper (`_single_crossing_rows`)'s docstring so it isn't rediscovered.

Full suite: **700 passed** (was 689 immediately before this task, per
`sr-n`'s own final count after its CodeRabbit review pass; 689 + 11 new
tests = 700, exact match). No pre-existing test needed modification.

```text
$ cd python && uv run pytest -q
........................................................................ [ 10%]
........................................................................ [ 20%]
........................................................................ [ 30%]
........................................................................ [ 41%]
........................................................................ [ 51%]
........................................................................ [ 61%]
........................................................................ [ 72%]
........................................................................ [ 82%]
........................................................................ [ 92%]
....................................................                     [100%]
700 passed in 61.32s
```

## Real before/after walk-forward comparison

**Reproduction check first** (same precedent as `sr-n`'s own Configuration
C comparison): re-ran the UN-fixed code (via a git stash of just the
production fix) through the identical real walk-forward -- reproduced
Task N's own published figures byte-for-byte (mean Sharpe
`-0.005415198890017692` vs. published `-0.005415`; min Sharpe
`-5.97914359662677` vs. `-5.979144`; 7 total trades, both; 14 folds with
zero trades, both; per-fold Sharpe values identical to 12+ decimal places).
Confirms no code/data drift since `sr-n` -- the comparison below is a
genuine isolated fix-only delta.

| metric | before (unfixed, reproduction) | after (fold-boundary fix) | delta |
|---|---|---|---|
| run_id | `2efc832b-0701-42a7-b505-204b68bfe058` | `b14ee1ea-b02a-4ed2-9d48-13ba4fc25944` | |
| fold count | 19 | 19 | unchanged |
| **total trades** | **7** | **14** | **+7 (2.0x)** |
| folds with >=1 trade | 5/19 (26.3%) | 8/19 (42.1%) | +3 folds |
| folds with zero trades | 14/19 | 11/19 | -3 folds |
| mean Sharpe | -0.005415 | -0.483941 | worse |
| min Sharpe | -5.979144 | -7.979062 | worse |
| folds positive Sharpe | 3/19 (15.8%) | 4/19 (21.1%) | +1 fold (still far below floor) |
| worst-fold max drawdown | 0.6268% | 1.1442% | worse (still trivial vs. the 20-25% ceiling) |
| mean total return | 0.08658% | 0.10305% | +0.0165pp |
| mean profit factor | 1.18774 | 1.06681 | worse |
| min profit factor | 0.0 | 0.0 | unchanged |

**Eligibility bar: still FAILS outright, at all three candidate floors
(80/85/90%), both before and after.**

| check | before | after |
|---|---|---|
| fold consistency (@80/85/90%, all three) | FAIL (15.8% < any floor) | FAIL (21.1% < any floor) |
| sign test (`p`, `n=19`) | FAIL (p=0.99964) | FAIL (p=0.99779, marginally less bad, still nowhere near significant) |
| Sharpe significance (one-sided t-test) | FAIL (p=0.50089) | FAIL (p=0.59361, WORSE) |
| minimum trade count (100) | FAIL (7) | FAIL (14) |
| profit factor floor (1.3-1.5) | FAIL (1.188) | FAIL (1.067, worse) |
| max drawdown ceiling (20-25%) | PASS (0.63%) | PASS (1.14%) |
| **overall** | **FAIL** | **FAIL** |

## Honest verdict

**The mechanical fix worked exactly as diagnosed**: total trades roughly
doubled (7 -> 14), and the number of folds that fire at least one trade
increased from 5 to 8 -- confirming the root cause really was the
fold-boundary state reset, not a fundamental rarity of tradeable crossings.

**The larger, more representative trade sample does NOT support a more
favorable read of the underlying signal -- if anything, the opposite.**
Every headline metric that could move (mean Sharpe, min Sharpe, mean
profit factor, Sharpe-significance p-value) got WORSE after the fix, not
better, reported plainly rather than cherry-picked. Fold consistency and
raw fold-win-count both improved marginally (3/19 -> 4/19) but remain
catastrophically short of the 80-90% floor either way. This is a
legitimate, non-cherry-picked outcome: the fix corrected a real mechanical
bug and produced a fairer test, and the fairer test still fails decisively
-- now on more evidence, not less.

**14 trades is still far short of the Eligibility Bar's 100-trade
floor.** This result remains data-limited in an absolute sense (14 trades
cannot support a confident statistical verdict on its own), but it is no
longer "inconclusive due to an obvious, fixable harness bug" the way
Task N's 7-trade result was -- the specific, previously-identified
mechanical cause of the shortfall has now been fixed, and what's left is
either genuine low frequency of the underlying signal's real tradeable
opportunities at this threshold/timeframe, or a genuinely weak signal, or
both. Distinguishing those further (e.g. a lower `entry_z_threshold` to
generate more trades) would require a new, deliberately-scoped follow-up
tuning task -- not a silent adjustment made under result pressure inside
this one (see "Deliberately out of scope" below).

## Judgment calls resolved without asking

- **Seeding `_signal_state` only, not restructuring the walk-forward
  harness to run each fold's bound strategy over train_klines too** -- a
  more invasive alternative (feeding the real per-fold strategy
  `train_klines` first, discarding any intents, before evaluating
  `validate_klines` for real) would have ALSO correctly warmed
  `_signal_state` (and, redundantly, the already-warm z-score), but would
  require ATR/vol-scalar state to warm up identically too and would change
  `run_walk_forward`'s generic contract for every `TrainableStrategy`, not
  just this one. Seeding via a `fit()`-local, strategy-specific mechanism
  is more surgical, scoped to the actual strategy that has the bug, and
  doesn't touch the shared walk-forward harness at all.
- **The seed replay ignores ATR/vol-scalar filter interactions** (the
  `entry_rejected_by_filters` mechanism that withholds a real per-bar
  `_signal_state` update when a live flip-attempt is rejected by
  downstream filters) -- deliberately, because `_signal_state`'s
  definition is fundamentally "the last raw nonzero extremity direction
  observed," updated unconditionally whenever `current_signal != 0`,
  independent of whether any trade attempt succeeded. Replicating the
  filter-rejection nuance in the seed replay would require kline data the
  seed doesn't have access to (ATR/realized-vol need price data,
  `compute_seed_signal_state` only ever sees `funding_rates`) and isn't
  needed for correctness -- confirmed by direct test coverage.
- **Same `known_through` semantics (inclusive) for both the scoring pass
  and the returned strategy**, rather than hand-tuning an exclusive
  boundary for one and inclusive for the other -- justified directly in
  the fix's own design section via the idempotency argument (a settlement
  at/after a strategy's own first real evaluated bar gets reprocessed
  identically regardless of whether the seed also saw it).
- **No change to `entry_z_threshold`/`funding_zscore_lookback` or any
  other existing tunable constant** -- this task's scope is the harness
  bug only; changing the threshold now, having already seen the after-fix
  result, would be tuning-after-the-fact, the exact discipline Task N's
  own "not fixed in this task" judgment call was protecting against.

## Deliberately out of scope

- **Lowering `entry_z_threshold` or `funding_zscore_lookback` to generate
  more trades** -- flagged as a real, scoped candidate follow-up (same
  status Task N gave the edge-trigger redesign this task fixed), not
  attempted here.
- **Redesigning the edge-trigger rule itself** (e.g. firing on ANY
  extreme reading rather than requiring an opposite-direction flip) --
  Task N flagged this as a candidate; still not attempted, since it would
  change the strategy's actual trading logic, not just its harness-level
  fairness.
- **Restructuring `run_walk_forward` to warm every `TrainableStrategy`
  through `train_klines` before evaluating `validate_klines`** -- see
  "Judgment calls" above; would be a generic harness change affecting
  every strategy in this project, not a scoped fix for this one bug.
- **Any holdout access** -- this result fails the Eligibility Bar
  decisively both before and after the fix; nothing legitimate to spend
  the funding dataset's one-shot holdout access confirming.
