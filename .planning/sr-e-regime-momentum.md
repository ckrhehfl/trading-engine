# Strategy Research: regime-filtered 15m momentum ("Task E")

## Scope note

First **real** strategy hypothesis in this project — unlike Task D's
`ma_crossover.py` (an explicit pipeline-validation vehicle making no edge
claim), this is a genuine, honest attempt at finding real edge, run
through the exact same walk-forward/holdout/experiment-log rigor
(`python/research/walkforward.py`, `holdout.py`, `experiment_log.py`,
all Task C, merged) on top of Task A's real BingX data pipeline
(`python/data/`) and Task B's metrics layer (`python/metrics/`). Depends
on all of A–D, all merged and read in full before starting. See
CLAUDE.md's "Strategy Research Methodology" section for the non-
negotiable rules this run had to follow (walk-forward validation, full
logging, holdout untouched, no lookahead), and its "Strategy Research
Operational Design" section for the pipeline this task runs through
without modifying.

**Result up front, for anyone who only reads this section**: the
strategy does **not** clear CLAUDE.md's Backtest/Walk-Forward
Eligibility Bar. All 3 folds have negative Sharpe, total trade count
(16) is far short of the 100 minimum, and profit factor (mean 0.21, min
0.0) is far below the 1.3–1.5 floor. Drawdown is the only criterion that
passes, and trivially so (see "Plausibility check" below — a function of
the deliberately tiny fixed position size, not evidence of genuine risk
control). This is a legitimate, informative negative result, not a bug —
see "Full honest real-world result" below for the complete numbers and
"Interpretation" for why this outcome isn't especially surprising given
the data depth and design constraints.

## What was built

- **`python/research/strategies/regime_momentum.py`** (new module,
  alongside Task D's `ma_crossover.py` in the same package):
  - `HourlyResampler` — aggregates a bar-by-bar 15m `Kline` stream into
    completed synthetic 1h candles, aligned to the hour (`open_time.minute
    == 0` starts a group; any leading bars before the first such bar are
    discarded, never folded into a short first group). OHLC aggregation:
    open = 1st bar's open, high = max of the 4 highs, low = min of the 4
    lows, close = 4th bar's close, volume = sum of the 4 volumes. Only
    returns a candle on the bar that completes a group of 4; `None`
    otherwise. Defensive against a gap in the 15m stream (not expected
    from this project's own gap-free `KlineWindow`-driven bar loop, but
    not assumed either): a bar whose `open_time` doesn't immediately
    follow the previous accumulated bar by exactly one 15m step discards
    the in-progress group and restarts alignment search from that bar.
  - `RegimeMomentumStrategy` — the bound, stateful `Strategy`. Same
    "reads only `window[-1]`, maintains its own incremental state" shape
    as `ma_crossover.MovingAverageCrossoverStrategy`. On each call: feeds
    the current bar into its own `HourlyResampler`; if a 1h candle just
    completed, updates a rolling `deque` of the last `regime_sma_length`
    completed 1h closes and recomputes the trend regime (`"up"` if the
    latest completed close is strictly above that SMA, `"down"` if
    strictly below, `None` for an exact tie or insufficient history).
    Then runs the same edge-triggered 15m fast/slow SMA crossover
    detection as `ma_crossover.py`, but only emits an `OrderIntent` when
    the fresh cross's direction matches the currently-active regime — a
    cross against the regime (or with no regime yet established)
    produces nothing at all, not a remembered/suppressed signal. The
    crossover-sign tracker used for edge-triggering still updates
    regardless of gating, so a later cross back the other way is
    correctly treated as "a fresh cross," not "the same cross firing
    again."
  - `RegimeMomentumTrainable` — the `TrainableStrategy` implementation.
    `fit()` grid-searches 15m `(fast, slow)` candidates only — the 1h
    regime SMA length is never read from `params`, always the value the
    `RegimeMomentumTrainable` was constructed with
    (`REGIME_SMA_LENGTH` by default) — against `train_klines` only,
    scores by total return, logs **every** candidate as its own
    `backtest_run` entry (`parent_run_id`/`candidate_index`/
    `total_candidates`), and returns a fresh bound instance for the
    winner. Structurally identical to `MACrossoverTrainable.fit`.
- **`python/tests/test_regime_momentum.py`** — 28 new tests (TDD:
  written and confirmed failing on `ModuleNotFoundError` before
  `regime_momentum.py` existed).

Full suite: **268 passed** (was 240 after Task D — 268-240=28, matching
the new-test count exactly; nothing from Tasks A–D regressed).

## Fixed 1h regime SMA length: `REGIME_SMA_LENGTH = 24`, and why

CLAUDE.md's design brief for this strategy explicitly calls for keeping
the number of tunable knobs low, given the thin real data (~3 usable
walk-forward folds at the provisional default windows) — so this value
is a judgment call fixed once here, deliberately kept **out** of
`fit()`'s grid search entirely (verified by a dedicated test,
`test_fit_uses_the_fixed_regime_sma_length_for_every_candidate_ignoring_params`,
that a `params["regime_sma_length"]` key has zero effect on what's
actually used or logged).

Chose **24** (24 completed hourly candles = one full calendar day of
higher-timeframe closes) over the more common technical-analysis
folklore numbers (20, 50) specifically because it's easy to explain and
reason about — "a day" is a legible, non-arbitrary unit, not a round
number pulled from indicator-default convention — while costing
negligible usable history either way: at the provisional walk-forward
windows, even the 30-day/720-hour validate fold gives >29x the warmup
this SMA needs, and the ~90-day/2,160-hour train fold gives >90x. Given
that margin, 20 vs. 24 vs. 50 would not have meaningfully changed how
much of any fold gets "spent" on warmup — the choice mattered for
legibility, not for squeezing out usable data.

## Candidate grid searched

`DEFAULT_CANDIDATE_GRID = ((5, 15), (8, 20), (10, 25), (13, 30), (20, 40))`
— 5 `(fast, slow)` 15m SMA window pairs, same grid *size* as Task D's
placeholder (5 candidates) for the same reason (CLAUDE.md's brief: don't
overbuild, keep tunable knobs low), but different, slightly
shorter-window-biased numbers than `ma_crossover.py`'s grid (max slow=40
here vs. 50 there). Reasoning, not empirical: entries here are already
filtered by the 1h regime gate, so a faster-reacting 15m trigger is a
more plausible choice for the entry signal itself than it would be as a
standalone (ungated) crossover — a directional judgment call, not a
tuned/backtested choice (tuning the grid itself against these results
would be exactly the data-snooping problem the design's "few tunable
knobs" constraint exists to avoid).

## TDD, and one genuinely instructive test-design detour

`test_regime_momentum.py` written first, confirmed failing on
`ModuleNotFoundError` before `regime_momentum.py` existed, then the
minimum implementation was added to turn every test green (mirroring
Task D's process exactly).

Coverage, per the task brief's explicit emphasis areas:

- **`HourlyResampler` correctness**: OHLC aggregation (open/high/low/
  close/volume all independently verified, not just close), correctly
  returns `None` for the 3 still-forming bars before a group completes,
  correctly discards leading bars before the first hour-aligned bar
  (engineered to mirror real BingX depth, which starts at `:45`, not
  `:00`), correctly starts a fresh group immediately after completing
  one, and correctly resets on a simulated gap in the 15m stream.
- **Regime-gating logic** (the task brief's explicitly flagged
  highest-risk area): two hand-verified scenarios with the full bar-by-
  bar arithmetic worked out by hand and recorded inline in the test file
  and this doc (see below) — one proving a genuine bearish cross against
  an "up" regime produces nothing, one proving the same scenario extended
  further shows a *matching*-direction cross firing correctly (gating is
  direction-sensitive, not "block everything"). A third test proves an
  exact tie between a completed hourly close and its own SMA resets the
  regime to `None`, not a stale prior value.
- **Edge-triggered firing**: exactly one signal fires across a run that
  sustains the same crossover-and-regime state for many bars afterward.
- **Regime warmup**: a scenario with a real underlying crossover but too
  few completed hourly candles for `regime_sma_length` to ever be
  satisfied produces zero signals throughout, confirmed against an
  independent "was there really a raw cross here" reference so the test
  isn't trivially true.
- **`fit()` never touches anything but `train_klines`**: `monkeypatch`-
  spied, identity-checked (`is`, not `==`), same technique as Task D.
- **Property-based cross-check**: an independent, from-scratch reference
  implementation (separate resampling logic, separate regime logic,
  separate crossover logic — no code shared with the production module)
  is compared bar-for-bar against the real `RegimeMomentumStrategy` over
  a long, multi-phase synthetic price series.

### The property-based test's construction was itself a real finding

The first two attempts at the multi-phase zigzag scenario for the
property-based cross-check (`test_full_signal_sequence_matches_
independent_reference_over_a_long_zigzag`) produced a passing equality
check (`actual == expected`) but *zero* matching-direction fires in
either direction — every single raw crossover happened to land during
the *opposite* regime. Investigating why (rather than just deleting the
"must fire in both directions" sanity assertions to make the test pass)
surfaced a real, structurally accurate dynamic: in a smooth, single-
reversal-per-phase zigzag, the (fast) 15m crossover always flips **at**
each turning point, while the (slower) 1h regime SMA only catches up
**after** it — so a lone reversal cross is, by construction, always
still fighting the *old* regime. A genuine matching (gated-through) fire
requires a *second*, same-direction re-cross that happens only *after*
the regime has already caught up — i.e. a shallow, short countertrend
wiggle within an already-long, already-regime-confirmed trend, not a
bare reversal. The final scenario (long rise/fall establishing a firm
regime, each followed by a shallow short dip/bounce-and-resume) was
engineered specifically to produce that shape, and does: two real
matching fires (one LONG, one SHORT) alongside several genuinely-
suppressed opposite-direction crosses, all cross-checked equal to the
independent reference. This same dynamic (a fast trigger reverses before
a slower filter confirms) is a real, well-known property of trend-
following-with-a-confirmation-filter designs generally, not an artifact
specific to this test — recorded here since it surfaced from test
construction, not from reading about the strategy shape in advance.

### Hand-verified regime-gating arithmetic (for the record)

`fast=2, slow=4, regime_sma_length=2`, closes =
`[97, 98, 99, 100, 105, 107, 109, 110, 95, 98, 115]`:

- Bar idx 3: 15m baseline crossover sign established (+1, bullish) — no
  signal (nothing to have crossed from yet). 1st synthetic 1h candle
  completes here too, close=100.
- Bar idx 7: 2nd synthetic 1h candle completes, close=110. SMA(2) of
  `[100, 110]` = 105 < 110 → regime flips to `"up"`.
- Bar idx 8 (close=95): 15m sign flips to −1 (bearish) — a genuine fresh
  cross — but regime is `"up"` → **no signal** (correctly suppressed).
- Bar idx 10 (close=115): 15m sign flips back to +1 (bullish) — a
  genuine fresh cross — regime is still `"up"` (no 3rd hourly candle has
  completed yet) → **LONG intent fires**, with the expected
  `GUARDED_MARKET`/quantity/symbol/no-limit-price shape.

## Judgment calls resolved without asking

- **`REGIME_SMA_LENGTH = 24`, `HourlyResampler`'s gap-reset defense, and
  the candidate grid's specific numbers** — see sections above.
- **`RegimeMomentumStrategy`/`RegimeMomentumTrainable` accept
  `regime_sma_length` as a real constructor parameter** (default
  `REGIME_SMA_LENGTH`), rather than hardcoding the constant directly
  inside the class. This is what makes the fixed-length hand-verified
  tests above tractable at a small, legible size instead of requiring
  hundreds of bars to exercise — the same testability trade-off Task D's
  own module docstring reasons about for `MovingAverageCrossoverStrategy`'s
  stateful design. `fit()` never reads this value from `params`, so it
  stays structurally non-tunable via the grid search regardless of the
  class supporting it as a parameter.
- **Regime update happens before the 15m crossover check within the same
  bar**, not after — so a 1h candle that happens to complete exactly on
  a given bar is immediately eligible to gate that same bar's entry
  decision, not only the next one. Chosen because "fully completed" is
  true as of that bar (the 4th 15m bar's own close is real, observed
  data at that point in the backtest's bar-by-bar walk), and this is the
  more natural reading of the design brief's "only uses fully-completed
  1h candles" — the still-*forming* hour is excluded, not the
  just-*completed* one.
- **Exact tie handling**: an exact tie between the latest completed 1h
  close and its own SMA resets the regime to `None` (explicitly
  "undefined trend"), rather than leaving the previous regime value in
  place. The design brief only specifies the above/below cases;
  treating an exact tie as "no defined trend" (rather than "keep
  whatever was true before") was chosen as the more defensible reading
  of "if above, up; if below, down" taken literally, and is directly
  tested (`test_regime_resets_to_none_on_an_exact_tie_after_being_
  previously_defined`).
- **`_data_range`/`_metrics_summary` duplicated, not imported, from
  `research.walkforward`'s/`ma_crossover.py`'s own module-private
  helpers of the same name** — identical reasoning to Task D's own
  identical decision (see `sr-d-placeholder-strategy.md`): importing a
  private helper across modules for a ~10-line function is worse
  coupling than duplicating it.
- **Candidate `backtest_run` log entries now also record
  `regime_sma_length`** in their `params` field (Task D's equivalent
  entries have no such field, since `ma_crossover.py` has no regime
  concept) — included for audit completeness even though the value never
  varies across candidates within one `fit()` call, so a future reader of
  `runs/experiments.jsonl` doesn't have to cross-reference source code to
  know what fixed value was actually used for a given logged run.

## Real end-to-end run (2026-07-26, real cached BingX data)

Ran via an uncommitted script (same convention as Tasks C/D's own real
verification runs: "inline in the verification script, not committed"),
from the repo root, against the already-cached SQLite data from Task
A/C/D's real BingX backfill (`python/data/var/klines.sqlite3`, 24,191
bars — no re-fetch needed, the cache already covered the full required
range; confirmed no backfill call was made by this task).

- **Research klines loaded**: 19,870 bars
  (`2025-11-16T03:45:00Z` → `2026-06-11T03:00:00Z`), via
  `load_research_klines(0, holdout_cutoff_ms)` — matches Tasks C/D's own
  real-run findings exactly (same cached data, same cutoff).
- **Fold count: 3** (train=8,640, validate=2,880, step=2,880) — as
  expected, per Task C's real-data finding, short of the 8–10
  credibility floor (see "Eligibility bar evaluation" below for how this
  is handled).
- **`fee_bps=5, slippage_bps=2`** (matches Tasks C/D's own real
  verification runs' convention).
- **`params`**: `{"candidates": DEFAULT_CANDIDATE_GRID, "quantity":
  "0.001", "symbol": "BTC-USDT"}`.

### Per-fold results (honest, unembellished)

| fold | train range (bar idx) | validate range (bar idx) | winner (fast,slow) | trades | total_return | sharpe | max_drawdown | win_rate | profit_factor |
|---|---|---|---|---|---|---|---|---|---|
| 0 | [0, 8640) | [8640, 11520) | (5, 15) | 2 | -1.161% | -4.75 | 1.375% | 0.0% | 0.0 |
| 1 | [2880, 11520) | [11520, 14400) | (10, 25) | 7 | -0.060% | -0.66 | 0.296% | 14.3% | 0.516 |
| 2 | [5760, 14400) | [14400, 17280) | (8, 20) | 7 | -0.148% | -1.44 | 0.396% | 28.6% | 0.126 |

**Aggregate**: `fold_count=3`, `mean_sharpe=-2.28`, `min_sharpe=-4.75`,
`all_folds_positive_sharpe=False`, `worst_fold_max_drawdown≈1.375%`,
`mean_total_return≈-0.457%`, `total_trades=16`, `mean_profit_factor≈0.214`,
`min_profit_factor=0.0`, `folds_with_zero_trades=0`.

### Eligibility bar evaluation (CLAUDE.md's Backtest/Walk-Forward Eligibility Bar, excluding the fold-count floor)

Per this task's brief: the 8–10 fold credibility floor is already known
structurally unreachable at the current real BingX depth (only 3 folds
possible at the provisional windows — the same finding Tasks C and D
already recorded) — that criterion is not re-litigated here, it's simply
excluded from the pass/fail evaluation below rather than silently
counted as a pass or a fail.

| Criterion | Requirement | Actual | Result |
|---|---|---|---|
| Positive Sharpe, every fold | Sharpe > 0 in all folds | -4.75, -0.66, -1.44 | **FAIL** — every fold negative |
| Max drawdown ceiling | ≤ 20–25%, per-fold and aggregate | per-fold: 1.375%, 0.296%, 0.396%; aggregate (`worst_fold_max_drawdown` — this walk-forward harness scores folds independently, per CLAUDE.md's design, so the worst per-fold figure *is* the only "aggregate" drawdown concept this pipeline computes, not a separate, missing calculation): 1.375% | **PASS** (see plausibility caveat below) |
| Minimum total trades | ≥ 100 across all folds | 16 | **FAIL** — far short |
| Profit factor floor | 1.3–1.5 | mean 0.214, min 0.0 | **FAIL** — far short |

**This strategy does not clear the eligibility bar.** Three of four
evaluated criteria fail outright, including the two (Sharpe, profit
factor) most directly tied to "is there an edge here." This is a
negative, and honest, result — not a bug, not a reason to re-tune the
grid or the fixed regime length and re-run to chase a better number
(CLAUDE.md's Strategy Research Methodology treats exactly that pattern
as the untracked-variation-count/data-snooping risk the experiment log
exists to make visible).

### Plausibility check (not a strategy-quality claim)

Same caveat Task D's own results section recorded: return/drawdown
magnitudes here are small (fractions of a percent), consistent with the
deliberately tiny fixed `quantity=0.001` BTC against a $10,000 notional
starting equity, not a parsing bug (a genuine bug would more plausibly
show as an absurd, unbounded number, not a small, internally consistent
one). The drawdown ceiling "pass" above is therefore not meaningfully
informative about risk control — it would pass for almost any
directionally-wrong strategy at this position size. The trade-count and
profit-factor failures, and the uniformly negative Sharpe, are the more
substantive signal here.

### Interpretation, stated carefully

The regime gate did what it was designed to do mechanically — trade
frequency dropped sharply versus Task D's ungated MA-crossover placeholder
on a similar-sized grid over the same 3 folds (16 trades here vs. 138
there), consistent with a slower, trend-confirmation-gated entry firing
less often than a bare crossover. That mechanical effect is confirmed;
it did not translate into a positive result. Fold 0 in particular traded
only twice — a sample far too small to draw any real statistical
conclusion from on its own (Sharpe -4.75 on 2 trades is mostly noise,
not evidence). Folds 1 and 2 have more trades (7 each) and still show
negative Sharpe, a losing profit factor, and a low win rate. Taken
together: this run does not provide evidence of a real edge in this
strategy shape, at these parameters, on this data. It also does not rule
one out definitively — 3 folds and 16 total trades is thin evidence in
either direction, which is precisely why CLAUDE.md's design already
flags the fold-count floor as unmet and treats results at this depth
with proportional skepticism regardless of which way they point.

### Experiment-log verification

`run_id=f0aefd04-7524-4dbb-b8d8-48037e860b92`. **16 new `backtest_run`
records** written to the real `runs/experiments.jsonl`: **15 candidate
records** (`fold_count × total_candidates = 3 × 5 = 15`, confirmed by
direct count, each with `parent_run_id == result.run_id`,
`total_candidates == 5`, `candidate_index` spanning 0–4 per fold) **+ 1
final record** (`run_id == result.run_id`, `parent_run_id == null`,
matching every other standalone `run_walk_forward` call's shape).
`runs/experiments.jsonl` went from **18 lines** (Task D's ending state)
**to 34** (18 + 16). No holdout access was made by this task — `load_holdout_klines` was never
called (confirmed: the file's single `holdout_access` record, from
Task C's own real verification run, is unchanged) — this strategy is
nowhere near clearing the eligibility bar, so there is nothing
legitimate to confirm against holdout data yet, per CLAUDE.md's
non-negotiable "holdout stays untouched" rule.

## CodeRabbit review findings

One review pass, four actionable findings, all accepted (all low-risk,
non-CODEOWNERS Python research code / docs):

- **A zero-trade candidate could win `fit()`'s selection over a
  genuinely-losing (but real, evidence-backed) candidate.** A candidate
  that never fires scores an exactly-`0` total return; on a grid where
  every *real* candidate is net-losing, `0 > any negative number` would
  make the never-fired candidate the "winner" — quietly turning a losing
  fold into a validate-fold run that never trades at all, and reporting
  that as if it were a legitimate outcome. Fixed: candidates with
  `num_trades == 0` are now excluded from winner selection (still
  logged, same as every other candidate) unless literally every
  candidate in the grid has zero trades, in which case the first-listed
  candidate is returned as a deterministic fallback rather than raising.
  Three new tests
  (`test_fit_never_picks_a_zero_trade_candidate_over_a_genuinely_losing_one`,
  `test_fit_falls_back_to_the_first_candidate_when_every_candidate_has_zero_trades`,
  `test_fit_passes_fee_and_slippage_through_to_candidate_scoring` — the
  last one a separate but related CodeRabbit finding, see below).
  **Verified this fix changes nothing about the real run's already-
  reported results above**: the real run's fold 1 winner has a
  *negative* logged training-window `total_return` (-0.63%), which is
  only possible if no zero-trade candidate existed among that fold's 5
  candidates (a zero-trade candidate, scoring exactly `0`, would have
  beaten it under the old code and been reported as the winner instead —
  it wasn't). Re-ran the real end-to-end script after the fix to confirm
  directly rather than rely on that argument alone: every run-ID-
  independent value (per-fold trade counts, total_return, Sharpe,
  drawdown, win rate, profit factor, aggregate metrics, and every fold's
  winning `(fast, slow)` pair) was identical before and after — the only
  fields that legitimately differed were the fresh `run_id` (a new
  `uuid4()` each invocation, by design) and each record's `logged_at`
  timestamp, neither of which was ever part of the reported result.
- **No test verified `fee_bps`/`slippage_bps` actually reach `fit()`'s
  scoring** — every existing `fit()`/walk-forward test used
  `fee_bps=slippage_bps=0`, so a regression that silently dropped cost
  params before they reached `run_backtest` would have gone undetected
  (a real risk called out by `.coderabbit.yaml`'s own Python review
  instructions: missing fee/slippage modeling). Added
  `test_fit_passes_fee_and_slippage_through_to_candidate_scoring`
  (same candidate scored at zero vs. a large nonzero fee; logged score
  must be strictly worse with the fee applied).
- **A test helper's `l` parameter name** (`_kline`'s low-price argument)
  is Ruff's E741 (ambiguous variable name, error-level) — renamed to
  `lo`; call sites are all positional, so the rename alone was
  sufficient.
- **A split Markdown heading** in this doc (the "Eligibility bar
  evaluation" section title) rendered incorrectly across two lines —
  joined onto one line.

Also cleaned up two adjacent Ruff nitpicks in the same review pass
(missing `**overrides: Any` annotation, unnecessary `dict()` call
rewritten as a literal) in `test_regime_momentum.py`'s `_trainable`
helper, since it was already being touched for the fee/slippage test
above.

All fixes batched into one follow-up push (not one push per finding,
per CLAUDE.md's rate-limit-avoidance guidance) before requesting
re-review. Full suite after fixes: **271 passed** (was 268; +3 matching
the three new tests above exactly).

**Second review pass** (after the above push), three more findings, all
accepted:

- **`**overrides: Any` (the previous pass's own suggested fix for the
  ANN003 finding) itself trips Ruff's ANN401** (`Any` disallowed in a
  function-parameter annotation). Changed to `**overrides: object` per
  CodeRabbit's own suggested diff — the local `kwargs: dict[str, Any]`
  variable annotation a few lines below is unaffected (ANN401 targets
  parameter/return annotations, not local variable ones).
- **"Byte-for-byte identical" (this doc's own wording, describing the
  post-fix real-run re-verification above) overclaimed**: each run
  generates a fresh `run_id` (`uuid4()`) and `logged_at` timestamp by
  design, so the two runs' *raw* output could never be literally
  byte-for-byte identical. Reworded to state precisely what was actually
  compared and found identical (every run-ID-independent value) versus
  what legitimately differs by design (`run_id`, `logged_at`) — the
  underlying verification claim was accurate, only its phrasing wasn't
  precise.
- **The Eligibility-bar table's drawdown row cited only
  `worst_fold_max_drawdown` (1.375%) as "Actual," against a requirement
  stated as "per-fold and aggregate."** This walk-forward harness scores
  folds independently (per CLAUDE.md's design — no continuous
  cross-fold equity curve is ever computed), so `worst_fold_max_drawdown`
  *is* this pipeline's only aggregate-drawdown concept, not a missing
  calculation standing in for one — but the table didn't say that
  explicitly, reading as if a genuine aggregate figure had simply been
  left out. Reworded the row to list all three per-fold values plus the
  aggregate figure with an inline note explaining why they're the same
  underlying number by design.

## Deliberately out of scope

- **Tuning the candidate grid, the fixed regime SMA length, or the
  strategy logic to produce a better-looking result.** Explicitly against
  this task's brief ("report the real result honestly... do not tune,
  cherry-pick, or otherwise nudge the result toward looking better than
  it honestly is") and against CLAUDE.md's Strategy Research Methodology
  in spirit.
- **Any holdout confirmation run.** Nothing here is close to clearing the
  eligibility bar, so there is nothing legitimate to spend the one-shot
  holdout access on.
- **Changing the provisional walk-forward window sizes or the
  eligibility bar's own thresholds.** Both remain explicitly
  human-approved defaults per CLAUDE.md; this task evaluates against
  them, it doesn't change either.
- **5m extension timeframe.** CLAUDE.md's Current Scope names "15m base,
  5m extension, 1h regime filter" — this task builds the 1h regime piece
  on top of the existing 15m base, per its own explicit brief. The 5m
  extension remains unbuilt, same as before this task.
- **Modifying `backtest/engine.py` or `research/walkforward.py` to
  support a genuine multi-timeframe kline feed.** Per the task brief:
  the 1h regime is computed by internal resampling inside the strategy
  itself, deliberately avoiding that infrastructure change.
