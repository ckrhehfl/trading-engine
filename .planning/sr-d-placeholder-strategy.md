# Strategy Research Task D: placeholder MA-crossover `TrainableStrategy`

## Scope note

Fourth and last of four sequenced build tasks implementing CLAUDE.md's
"Strategy Research Operational Design (2026-07-25)" section — see that
section for the full design this task follows. Depends on Task A
(`python/data/`), Task B (`KlineWindow` + `python/metrics/`), and Task C
(`python/research/` — walkforward + holdout + experiment log), all merged
and read in full before starting. This doc covers
`python/research/strategies/ma_crossover.py` plus a small, necessary
extension to Task C's `python/research/walkforward.py`.

**This task proves the pipeline works end to end. It does not, and is not
trying to, produce a validated trading edge.** See
`ma_crossover.py`'s module docstring for the same disclaimer inline in
code, mirroring `java/runtime/.../DummySignalSource.java`'s pattern.

## The `parent_run_id` interface gap (CLAUDE.md flagged this as likely)

CLAUDE.md's design requires each grid-search candidate's train-only
backtest to be logged as its own `backtest_run` entry, using
`parent_run_id`/`candidate_index`/`total_candidates` to point back at the
overall walk-forward run. But Task C's `TrainableStrategy.fit(train_klines,
params) -> Strategy` protocol gave `fit()` no way to learn what `run_id`
to use as `parent_run_id` — `run_walk_forward` generated its own `run_id`
only at the very end (after the fold loop, right before its own
`log_run` call), by which point every fold's `fit()` call had already
happened.

**Resolved exactly as CLAUDE.md's brief anticipated**, not routed around:

- `TrainableStrategy.fit`'s signature widened to
  `fit(self, train_klines: list[Kline], params: Mapping[str, Any], *,
  parent_run_id: str) -> Strategy` (keyword-only, so every existing
  positional call site — there were none besides `run_walk_forward`
  itself — stays source-compatible other than the new required keyword).
- `run_walk_forward` now generates `run_id = str(uuid4())` **before** the
  fold loop (was: after it, right before its own final `log_run` call)
  and passes it as `strategy.fit(train_klines, params,
  parent_run_id=run_id)` on every fold. The final `log_run` call still
  uses the same `run_id` — unchanged from before, just generated earlier.
- A `fit()` implementation with nothing of its own to log (e.g. Task C's
  `_RecordingStrategy`/`_BuyAndHoldStrategy` test doubles,
  `python/tests/test_walkforward.py`) simply adds `*, parent_run_id=None`
  to its signature and ignores the value — a trivial, purely additive
  accommodation, not a behavior change. Both test doubles were updated
  this way.
- New test locking this in:
  `test_run_walk_forward_passes_its_own_run_id_to_fit_as_parent_run_id`
  (`python/tests/test_walkforward.py`) — asserts every fold's `fit()`
  call received the same `parent_run_id`, and that it equals
  `result.run_id` (the value actually written to the logged record).
  `test_walkforward.py` went from 27 to 28 tests; all 28 pass, including
  every pre-existing one, unmodified in behavior (only the two test
  doubles' signatures changed, not any assertion).

No alternative was seriously considered: generating the id earlier is a
minimal, purely additive change to a function only Task C's own module
and its tests call, and it's exactly the fix CLAUDE.md's own brief
described in advance ("generated once at the top of `run_walk_forward`
before the fold loop rather than at the end").

## What was built

- **`python/research/strategies/ma_crossover.py`** (new package,
  `python/research/strategies/`):
  - `MovingAverageCrossoverStrategy` — the bound `Strategy`. Stateful:
    holds a `deque[Decimal]` of the last `slow` closes and the last
    *established, non-zero* fast-vs-slow SMA regime sign, updated one bar
    at a time from `window[-1]` only (it does not re-read the rest of the
    growing window `run_backtest` hands it — the internal `deque` is the
    state). Emits `OrderIntent` only on a genuine sign flip relative to
    the last established regime — never on the bar that first establishes
    a baseline regime (nothing before it to have "crossed" from), never
    twice for the same regime, and an exact tie (`fast_sma == slow_sma`)
    counts as no signal and does not overwrite the tracked regime.
    `OrderType.GUARDED_MARKET`, fixed `quantity`/`symbol` given at
    construction.
  - `MACrossoverTrainable` — the `TrainableStrategy` implementation.
    `fit()` backtests every `(fast, slow)` candidate in
    `params["candidates"]` (default `DEFAULT_CANDIDATE_GRID`, 5 pairs)
    against `train_klines` only, via the existing
    `backtest.engine.run_backtest`, scores each by
    `compute_metrics(...).total_return`, logs **every** candidate as its
    own `backtest_run` entry (never only the winner), and returns a
    **fresh** `MovingAverageCrossoverStrategy` instance bound to the
    best-scoring `(fast, slow)` — never the instance used to score it.
- **`python/research/walkforward.py`** — `parent_run_id` threading (see
  above).
- **`python/tests/test_ma_crossover.py`** — 18 new tests (TDD: written
  and confirmed failing on `ModuleNotFoundError` before
  `ma_crossover.py` existed).
- **`python/tests/test_walkforward.py`** — 1 new test, 2 test doubles
  updated for the new `fit()` keyword.

Full suite: **240 passed** (was 221 after Task C — 240-221=19, matching
the 18+1 new-test count above exactly; nothing from Tasks A/B/C
regressed).

## Grid and scoring choices, and why

- **`DEFAULT_CANDIDATE_GRID`**: `(5,20), (8,21), (10,30), (13,34),
  (20,50)` — 5 fast/slow pairs spanning a "fast reaction" to "slow,
  smoother" range, loosely evocative of common technical-analysis
  defaults (5/20, 8/21 Fibonacci-ish, 10/30, 13/34 Fibonacci, 20/50)
  without claiming any of them is actually good — this is explicitly a
  placeholder grid, per CLAUDE.md's Task D brief ("don't overbuild this").
  Every pair satisfies `fast < slow` (enforced by
  `MovingAverageCrossoverStrategy.__init__`, `ValueError` otherwise).
- **Scoring metric: total return, not Sharpe.** `Metrics.sharpe_ratio`
  is `None` whenever a candidate trades fewer than twice or has
  zero-variance per-bar returns — a real possibility here (a `slow`
  window close to or exceeding a short `train_klines` length simply
  never fires at all, and did exactly this for the `(3, 100)`-style case
  exercised in `test_fit_picks_the_best_scoring_candidate_by_total_return`).
  Total return is always a defined `Decimal` for any non-empty
  `train_klines`, so `fit()`'s "pick the best" loop never has to invent a
  `None`-vs-number tie-break rule. This is a scoring-convenience decision
  for *candidate selection inside `fit()`* only — it has no effect on
  CLAUDE.md's Backtest/Walk-Forward Eligibility Bar, which is still
  expressed in terms of Sharpe/drawdown/profit-factor/trade-count and is
  evaluated (by a human, later) against the *validate*-fold metrics
  `run_walk_forward` already computes, not against `fit()`'s in-sample
  scoring.
- **Tie-break**: first strictly-greatest score wins (`score > best_score`,
  not `>=`), so a later candidate must beat, not merely match, the
  current best to replace it — deterministic, favors the
  earlier-listed/generally-faster-reacting candidate on an exact tie
  rather than an arbitrary "last one wins."
- **Candidate logging shape**: each candidate's `backtest_run` entry
  records a single-element `fold_results` list representing the *entire*
  `train_klines` window as both "train" and "validate" indices
  (`train_start_index=0, train_end_index=len(train_klines)`,
  `validate_start_index=0, validate_end_index=len(train_klines)`) — this
  is in-sample scoring, not a genuine train/validate split, and is
  labeled as such via a `"note"` field in `walk_forward_config`
  (`"in-sample candidate scoring inside MACrossoverTrainable.fit() --
  not itself a walk-forward run"`). `_data_range`/`_metrics_summary` are
  small, deliberately duplicated (not imported) from
  `research.walkforward`'s own module-private (leading-underscore)
  helpers of the same name — importing a private helper across modules
  would couple `ma_crossover.py` to `walkforward.py`'s internals for a
  ~10-line function; duplication here is cheaper than that coupling.

## Real end-to-end run (2026-07-26, real cached BingX data)

Ran via an uncommitted script (`/tmp/.../run_task_d_e2e.py` — same
convention Task C's own real verification run used: "inline in the
verification script, not committed"), from the repo root, against the
already-cached SQLite data from Task A/C's real BingX backfill
(`python/data/var/klines.sqlite3`, 24,191 bars — no re-fetch needed, the
cache already covered the full required range).

- **Research klines loaded**: 19,870 bars
  (`2025-11-16T03:45:00Z` → `2026-06-11T03:00:00Z`), via
  `load_research_klines(0, holdout_cutoff_ms)` — matches Task C's own
  real-run finding exactly (same cached data, same cutoff).
- **Fold count: 3** (train=8,640, validate=2,880, step=2,880) — as
  flagged in advance by the task brief and by `sr-c-walkforward-
  holdout.md`, short of the 8-10 credibility floor. Expected, not a bug.
- **`fee_bps=5, slippage_bps=2`** (matches Task C's real verification
  run's convention).
- **`params`**: `{"candidates": DEFAULT_CANDIDATE_GRID, "quantity":
  "0.001", "symbol": "BTC-USDT"}`.

**Per-fold winning `(fast, slow)` and validate-fold metrics** (honest,
unembellished — this is a losing placeholder, as expected for an
untuned, in-sample-selected single grid over 3 folds):

| fold | train range (bar idx) | validate range (bar idx) | winner (fast,slow) | trades | total_return | sharpe | max_drawdown | win_rate | profit_factor |
|---|---|---|---|---|---|---|---|---|---|
| 0 | [0, 8640) | [8640, 11520) | (13, 34) | 51 | -0.0774% | -3.55 | 0.095% | 39.2% | 0.745 |
| 1 | [2880, 11520) | [11520, 14400) | (13, 34) | 56 | -0.0608% | -3.19 | 0.084% | 33.9% | 0.861 |
| 2 | [5760, 14400) | [14400, 17280) | (20, 50) | 31 | -0.0639% | -4.96 | 0.079% | 38.7% | 0.552 |

**Aggregate**: `fold_count=3`, `mean_sharpe=-3.90`, `min_sharpe=-4.96`,
`all_folds_positive_sharpe=False`, `worst_fold_max_drawdown≈0.095%`,
`mean_total_return≈-0.0674%`, `total_trades=138`,
`mean_profit_factor≈0.719`, `min_profit_factor≈0.552`,
`folds_with_zero_trades=0`.

**Plausibility check (not a strategy-quality claim)**: return/drawdown
magnitudes are tiny (fractions of a percent) — consistent with the
deliberately small fixed `quantity=0.001` BTC against a $10,000 notional
starting equity, not a parsing bug (a genuine bug here would more likely
show as an absurd ±unbounded number, not a small, internally consistent
one). Negative Sharpe/return across all 3 folds with 31-56 trades each is
an unsurprising, plausible outcome for an untuned MA-crossover whipsawing
through real 15m BTC-USDT price action with real fees/slippage applied
— not evidence of a bug, and (per this module's own docstring) not
evidence that MA-crossover is a bad *category* of strategy either; it's
simply what this one small, unoptimized grid produced on this data. No
attempt was made to improve on this result — doing so would be exactly
the in-sample-fitting-on-limited-data trap this task's docstring warns
against, and this strategy must never be promoted toward paper/live
based on these numbers regardless.

**Experiment-log verification**: `run_id=8d31ddfe-88ac-4c41-9738-
ed6e87e3e854`. **16 new `backtest_run` records** written to the real
`runs/experiments.jsonl`: **15 candidate records**
(`fold_count × total_candidates = 3 × 5 = 15`, confirmed by direct count,
each with `parent_run_id == result.run_id`, `total_candidates == 5`,
`candidate_index` spanning 0-4 per fold) **+ 1 final record**
(`run_id == result.run_id`, `parent_run_id == null`, matching every other
standalone `run_walk_forward` call's shape). `runs/experiments.jsonl`
went from 2 lines (Task C's own real verification: 1 `backtest_run` + 1
`holdout_access`) to 18 (2 + 16). No holdout access was made by this
task — `load_holdout_klines` was never called; the holdout split remains
untouched, per CLAUDE.md's non-negotiable rule (this placeholder strategy
is nowhere near paper-trading-ready, so there is nothing legitimate to
confirm against holdout data yet).

## TDD

`python/tests/test_ma_crossover.py` written first (18 tests), confirmed
failing on `ModuleNotFoundError: No module named 'research.strategies'`
before `python/research/strategies/__init__.py` or `ma_crossover.py`
existed, then the minimum implementation was added to turn every test
green. Covers: constructor validation (`fast`/`slow` positivity and
`fast < slow`); no-signal-during-warmup; crossing detection cross-checked
bar-for-bar against an independently-written, deliberately naive
from-scratch reference implementation (`_reference_signal_sequence`,
recomputes both SMAs from the full closes-so-far prefix every bar, no
incremental state) over an engineered zigzag series exercising both
bullish and bearish crossings; edge-triggered-not-level-triggered (a
sustained post-cross regime of dozens of bars must fire exactly once,
not once per bar — the specific bug shape CLAUDE.md's brief called out);
emitted-intent shape (`GUARDED_MARKET`, given quantity/symbol, no limit
price); `fit()` provably only ever calls `run_backtest` with the exact
`train_klines` object (`monkeypatch`-spied, identity-checked, not just
equality); `fit()` picks the correct best-scoring candidate (engineered
trough-shaped price series so exactly one candidate can fire a profitable
trade and the other structurally cannot); `fit()` returns a *fresh*
instance, not the one used to score the winner (`bars_seen == 0` on the
returned object, immediately after a full 80-bar scoring backtest already
ran against the winning parameters); per-candidate logging correctness
(record count, `parent_run_id`/`candidate_index`/`total_candidates`,
per-candidate `params`); `fit()` alone logs *only* the per-candidate
records (no stray "overall" entry); and a full `run_walk_forward` +
`MACrossoverTrainable` integration test at small synthetic scale
confirming the exact expected record counts and lineage end to end.

One test-design mistake caught and fixed during this same TDD pass, not
after: the first draft of
`test_fit_picks_the_best_scoring_candidate_by_total_return` used a purely
monotonic 80-bar ramp, which — worked through by hand after the test
unexpectedly failed — never actually fires *any* signal for an SMA
crossover strategy (a strictly monotonic series never produces a sign
flip after the first bar that establishes the baseline regime, by this
strategy's own correct edge-triggered design). Replaced with a
decline-then-rise ("trough") shape that genuinely exercises a crossing
before any production code was changed to work around the flawed test.

## Judgment calls resolved without asking

- **`parent_run_id` threading** — see above; this is the one CLAUDE.md's
  own brief explicitly anticipated and pre-authorized ("this is a real,
  legitimate interface extension").
- **Total return over Sharpe for in-fit candidate scoring** — see
  "Grid and scoring choices" above.
- **`DEFAULT_QUANTITY = Decimal("0.001")`** — small, arbitrary,
  documented in-code as a backtest-only placeholder, explicitly not a
  risk-sized position (nowhere near the Java Risk Gateway).
- **`_data_range`/`_metrics_summary` duplicated, not imported, from
  `research.walkforward`'s module-private helpers of the same name** —
  see "Grid and scoring choices" above.
- **Candidate `backtest_run` entries represent the in-sample window as a
  single self-referential "fold"** (`train == validate` indices) rather
  than inventing a new schema shape — keeps every `backtest_run` record
  in `runs/experiments.jsonl` structurally uniform (a future
  aggregator/audit tool querying `fold_results` doesn't need a
  special case for candidate-scoring records), with the in-sample nature
  called out explicitly via a `"note"` field rather than left implicit.
- **`MovingAverageCrossoverStrategy` reads only `window[-1]` per call,
  maintaining its own rolling state, rather than recomputing SMAs from
  the full growing window each call.** Both are valid implementations of
  the same `Strategy` interface; this one was chosen because (a) it
  matches CLAUDE.md's own framing of the bound strategy as genuinely
  "stateful," and (b) it makes the "fresh instance for the returned
  winner" requirement load-bearing and testable (`bars_seen`) in a way a
  purely-stateless, window-derived implementation wouldn't need at all —
  which in turn makes explicit, in both code and tests, exactly why
  `fit()` must not hand back the same object used for in-sample scoring.

## Deliberately out of scope

- **Evaluating CLAUDE.md's Backtest/Walk-Forward Eligibility Bar against
  this run's results.** Not this task's job (same as Task C) — and this
  run's own numbers (`all_folds_positive_sharpe=False`) would fail it
  immediately regardless, which is expected and fine for a
  pipeline-validation run.
- **Any holdout confirmation run.** `load_holdout_klines` was never
  called by this task. A strategy this far from clearing the Eligibility
  Bar has nothing legitimate to confirm against holdout data — touching
  it now would just be spending the one-shot holdout access for no
  reason.
- **Tuning the candidate grid, the scoring metric, or the strategy logic
  to produce a better-looking result.** Explicitly against this task's
  brief ("don't inflate it or work around it") and against CLAUDE.md's
  Strategy Research Methodology in spirit (iterating against the same
  data repeatedly to chase a better number is exactly the untracked-
  variation-count / data-snooping risk the experiment log exists to make
  visible, not something to do informally in a verification script).
- **Trimming CLAUDE.md's "Strategy Research Operational Design" section
  to a summary + pointer.** `.planning/README.md`'s stated rule is that
  this becomes safe "once the corresponding `.planning/sr-*.md` files
  exist" — true as of this file landing (all of sr-a/b/c/d now exist).
  Left untouched here because CLAUDE.md is a CODEOWNERS-matched,
  human-review-reserved path and this task's brief didn't ask for it;
  flagged in the PR/final report as a candidate human-approved follow-up
  rather than actioned unilaterally.
- **5m/1h intervals, multi-symbol.** Same as Tasks A/B/C — out of the
  current single-symbol, single-interval scope.
