# Strategy Research Task G: overfitting safeguards (parameter sensitivity + MinBTL-style combination tracking)

## Scope note

This task follows a deep, credibility-graded research pass into how
institutional-grade systematic crypto strategies avoid overfitting. That
research isn't itself written up anywhere else in this repo, so this
document is its durable home (per CLAUDE.md's Strategy Research
Methodology preamble and `.planning/README.md`'s "a file here can only be
created for work that has actually happened" rule — this is that file,
for work that has now happened). Two findings from that research pass are
what this task implements; a third finding (full CSCV/PBO) is explicitly
assessed and deferred, not built.

## Finding 1: the "30 trades per parameter" rule has no rigorous origin — the real framework is MinBTL, and it measures a different ratio

The rule commonly repeated in trading blogs and forums — "you need at
least 30 trades per tunable parameter, or your backtest is overfit" — does
not trace back to any rigorous statistical derivation. It's folklore: a
plausible-sounding heuristic borrowed loosely from frequentist statistics'
"n=30 for the CLT to kick in" convention, applied to a completely
different question (parameter count vs. sample size for a *classifier* or
*point estimate*, not "how many backtest configurations were tried before
one was picked as the best").

The actual rigorous framework for this class of problem is Bailey,
Borwein, López de Prado, Zhu, **"The Probability of Backtest Overfitting"**
(2016, *Journal of Computational Finance*), and their related **Minimum
Backtest Length (MinBTL)** concept. Critically, it is about a **different
ratio entirely**: not "trades per parameter", but **the number of
independent parameter combinations tried (N) versus how much historical
data (in years) is actually available**. The paper's core intuition:
selecting "the best" result out of N independently-tried configurations
is itself a statistical procedure with its own sampling distribution —
the more configurations you try, the higher the expected best-of-N result
under pure noise (no real edge at all), so a backtest's apparent quality
has to be judged relative to how many other configurations were tried to
find it, not in isolation. A strategy family with many tunable knobs
generating many candidate combinations (loosely, `2^k` for `k`
roughly-independent binary-ish choices, or more precisely the count of
genuinely distinct configurations actually evaluated), run against a
short data history, is a red flag by this framework — even if the single
"best" combination individually looks clean.

This project's own existing strategies already generate exactly this
kind of countable trail: `ma_crossover.py`/`regime_momentum.py`/
`regime_momentum_risk_managed.py`/`hourly_momentum.py`'s `fit()`
implementations already log every grid-search candidate as its own
`backtest_run` entry (`parent_run_id`/`candidate_index`/`total_candidates`,
per CLAUDE.md's "Build sequencing" Task D design) — this task's Finding-1
implementation (`python/research/overfitting_check.py`) is the first thing
in this project to actually *read* that trail back and reason about it in
aggregate, rather than only writing it.

## Finding 2: parameter sensitivity testing — cheap, valuable, and not done yet

Independent of MinBTL, one of the most valuable, cheapest overfitting
checks available for a small grid search is **parameter sensitivity
testing**: after a grid search picks a winning parameter combination,
perturb each tunable parameter by roughly ±10% and ±25% from the winner
and check whether those *nearby* values also perform reasonably on the
same in-sample data used for selection. A "flat" performance surface
(the winner and its neighbors are all roughly similarly good) is a sign
of a real, structurally robust signal. A "spiky" surface (only the exact
winning value works; every nearby value fails or performs far worse) is a
classic curve-fitting signature — the "winner" is more likely to be noise
that happened to fit this specific historical window than a genuine edge.

This project had no version of this check before this task, despite
already having four `TrainableStrategy` implementations whose `fit()`
methods grid-search a small set of `(fast, slow)`-shaped candidates —
exactly the shape this check is designed for.

## Finding 3 (explicitly deferred, not built): full CSCV/PBO

Bailey et al.'s same paper also describes **Combinatorially Symmetric
Cross-Validation (CSCV)** and the resulting **Probability of Backtest
Overfitting (PBO)** estimate — the complete, statistically rigorous
version of overfitting detection this research pass surfaced. It works by
partitioning the backtest into a number of equal-sized blocks, forming
every combinatorially symmetric split into in-sample/out-of-sample
halves, and estimating, empirically, how often the in-sample "best"
configuration's out-of-sample rank is *below* the median — that
probability *is* the PBO.

**Deliberately assessed as too heavy for this project's current stage and
not implemented here.** Reasons, stated explicitly rather than left
implicit:

- **Scale mismatch.** CSCV's statistical power comes from having enough
  blocks and enough candidate configurations for the combinatorial
  splitting to be meaningful. This project has a single symbol
  (BTC-USDT), a handful of strategy families (`ma_crossover`,
  `regime_momentum`, `regime_momentum_risk_managed`, `hourly_momentum`),
  and grids of 5 candidates each. CSCV run against that scale would mostly
  be measuring noise in the splitting procedure itself, not a genuine
  overfitting signal.
- **Real complexity cost.** A correct CSCV/PBO implementation needs the
  full grid of candidates' equity-curve-level performance across every
  combinatorial split, a defined "worse than median" test, and a
  logistic-transform aggregation step — this is a meaningfully larger
  piece of machinery than either Finding 1 or Finding 2's implementations,
  and CLAUDE.md's own engineering discipline (TDD for anything
  non-trivial, "touch only what the task requires") argues against
  building it speculatively ahead of an actual need.
- **Explicit revisit trigger, not an open-ended "someday".** CLAUDE.md's
  Implementation Priority #9 (auto-retraining pipeline) is the point
  where this project's actual hyperparameter-search scale grows enough
  (a scheduled retraining loop trying many more configurations, likely
  across more strategy families) for CSCV/PBO's statistical power to
  become meaningful rather than noise. Revisit there, not before.

This task implements only the two lighter-weight additions (Findings 1
and 2) as instructed — full CSCV/PBO is deferred per the above, not
attempted in any partial form.

## What was built

### 1. Parameter sensitivity check — `python/research/robustness.py`

Two layers:

- **`perturb_candidate(winning_candidate, fractions=(0.10, 0.25),
  min_value=1) -> list[PerturbedCandidate]`** — a pure, low-level neighbor
  generator. Given a winning candidate tuple (e.g. `(fast=13, slow=34)`),
  perturbs **one position at a time** (every other position held fixed at
  the winning value) by each `±fraction`, rounds **half-up** (not
  Python's builtin `round()`, which is round-half-to-even/banker's
  rounding — several of this project's real candidate grids land on an
  exact `.5` tie under `±25%`, e.g. `10 × 1.25 = 12.5`, and half-up is the
  more predictable, easier-to-explain convention for a human reading a
  perturbation's resulting values), clamps to `>= min_value` (every real
  candidate value in this project is a positive bar-count window length;
  0 or negative is structurally meaningless), and deduplicates (a
  perturbation that rounds back to the winner itself, or to a candidate
  already produced by a different `(position, fraction, sign)`
  combination, is not repeated).
- **`check_parameter_sensitivity(strategy, base_params, winning_candidate,
  train_klines, *, fee_bps, slippage_bps, ...) -> ParameterSensitivityResult`**
  — evaluates the winning candidate (re-derived for an apples-to-apples
  comparison, not trusted from the caller) and every generated neighbor,
  all against the **same `train_klines`** used for selection (never
  `validate_klines` or holdout data — this checks the shape of the
  in-sample optimization surface, not a second out-of-sample test).

**Generic over any `TrainableStrategy` following this project's
established `params["candidates"]` convention** — a list of numeric
tuples that `fit()` grid-searches and picks one from. Every strategy in
this project (`ma_crossover.py`, `regime_momentum.py`,
`regime_momentum_risk_managed.py`, `hourly_momentum.py`) already follows
this convention, so evaluating one specific candidate is just calling
`strategy.fit(train_klines, {**base_params, "candidates": [candidate]},
parent_run_id=...)` — the exact same single-candidate mechanism every one
of those `fit()` implementations already uses internally per grid point.
This means `check_parameter_sensitivity` reuses each real strategy's own
construction/validation/logging path rather than reimplementing it, and
works with all four strategies unmodified, by construction. Demonstrated
directly against the real `ma_crossover.MACrossoverTrainable` in tests
(`test_check_parameter_sensitivity_works_with_real_ma_crossover_trainable`)
and against real BingX data below.

`robustness.py` does not import `research.walkforward.TrainableStrategy`
directly — doing so would create a circular import, since
`research/walkforward.py` imports `check_parameter_sensitivity` to wire it
in optionally (see below). It defines its own minimal, structurally
identical `_CandidateGridTrainable` Protocol instead — duplicated, not
shared, matching this project's existing precedent for the same tradeoff
(`ma_crossover.py`'s `_data_range`/`_metrics_summary` docstrings explain
the identical reasoning applied elsewhere in this codebase).

**Robustness rule, documented explicitly rather than left implicit**: if
the winning candidate itself isn't profitable in-sample, "robust" isn't a
meaningful claim to make about it either way — `is_robust=False` with a
reason that says so explicitly, distinct from a genuinely-tested-and-
failed spiky surface. Otherwise, `is_robust=True` iff at least **50%**
(`DEFAULT_MIN_POSITIVE_NEIGHBOR_FRACTION`) of the successfully-evaluated
neighbors are *also* profitable in-sample — "most nearby parameter values
also work" (flat) vs. "only the exact winner works" (spiky). This 50%
threshold is a deliberately simple, explicitly documented choice, not a
rigorously derived statistic — see the same "no fake precision" reasoning
as the MinBTL tiering below. A neighbor that couldn't be evaluated at all
(a structurally invalid candidate for the wrapped strategy, e.g.
`fast >= slow`) is excluded from that fraction entirely — caught via a
broad `except Exception` around each candidate's evaluation, so one bad
neighbor degrades to "this neighbor couldn't be evaluated" rather than
crashing the whole check; "invalid" is a different claim than "valid but
unprofitable" and the result's `NeighborEvaluation.error` field preserves
that distinction (`total_return=None`, not `0`).

**Optional, additive integration into `run_walk_forward`**
(`python/research/walkforward.py`): a new `sensitivity_extractor:
Callable[[Strategy], Sequence[int]] | None = None` parameter (default
`None` — every existing caller/test is byte-for-byte unaffected) and
`sensitivity_fractions` (default `(0.10, 0.25)`, passed straight through).
When given, `run_walk_forward` calls it on each fold's `bound_strategy`
(e.g. `lambda s: (s.fast_window, s.slow_window)` for
`ma_crossover`/`regime_momentum`-shaped strategies, which already expose
these as properties) to recover the winning candidate tuple, runs
`check_parameter_sensitivity` against that fold's `train_klines`, and
attaches the result (`.to_dict()`) to `FoldResult.parameter_sensitivity`
(a new, `None`-defaulted field) and to the logged
`fold_results[i]["parameter_sensitivity"]` key — **only present when
non-`None`**, so an existing reader of `runs/experiments.jsonl` sees the
exact same fold-record shape as before this field existed unless it opts
in.

**A real wiring bug found and fixed by testing against real accumulated
data, not by design review alone**: the first implementation passed each
fold's own `run_id` through to `check_parameter_sensitivity` as
`parent_run_id`, reasoning that this would attribute the sensitivity
check's `fit()` calls to the same walk-forward run. Running this for real
against the live BingX-backed database (see "Real-data results" below)
immediately surfaced the problem via `check_combination_count`'s own
inconsistency-detection: that fold's real 5-candidate grid search *also*
shares that exact `run_id` as its `parent_run_id` (per
`research/walkforward.py`'s existing design — `run_id` is generated once,
before the fold loop, specifically so every fold's `fit()` call can use it
as `parent_run_id`), so passing it through a second time for the 9
single-candidate sensitivity `fit()` calls (winner + 8 neighbors) put them
in the exact same `parent_run_id` group as the real grid, each reporting
its own tiny `total_candidates=1` next to the real grid's
`total_candidates=5` — an inconsistent-`total_candidates`-within-one-group
situation `check_combination_count` is built to flag defensively (see
below), and it did, immediately, on the very first real run. **Fix**:
`run_walk_forward`'s wiring now deliberately leaves
`check_parameter_sensitivity`'s `parent_run_id` at its default (`None`),
so every sensitivity-driven `fit()` call logs as its own standalone
record instead of joining the real grid's group. This is a considered
design decision, not a workaround: sensitivity-check evaluations never
influence which candidate is selected (they're a pure post-selection
diagnostic), so grouping them with the real grid search would have
conflated two conceptually different kinds of "combinations tried" under
one `parent_run_id`. Documented in both
`check_parameter_sensitivity`'s and the wiring call site's own
docstrings/comments so a future session doesn't reintroduce it.

### 2. MinBTL-style combination-count tracking — `python/research/overfitting_check.py`

`check_combination_count(strategy_id, runs_path=...) ->
OverfittingCheckResult` reads `runs/experiments.jsonl` (via
`research.experiment_log.read_records`, the same tolerant-of-a-truncated-
final-line reader every other consumer of this file already uses) and
computes:

- **Total independent combinations tried**, per this task's own explicit
  counting instruction: for each *distinct* `parent_run_id` observed among
  this `strategy_id`'s `backtest_run` records, count `total_candidates`
  **once** (not once per child record) and sum across all distinct
  `parent_run_id`s; each `backtest_run` record with `parent_run_id is
  None` (a standalone run) counts as exactly 1. `record_type:
  "holdout_access"` entries are ignored entirely.
  - **Why "once per distinct parent_run_id", not "once per record"**: a
    real walk-forward run's per-fold grid search logs one candidate
    record per `(fold, candidate)` pair, but every one of those records
    across every fold shares the *same* `parent_run_id` (the walk-forward
    run's own `run_id` — see `run_walk_forward`'s docstring). Naively
    summing `total_candidates` per record would count the same 5-
    candidate grid as "tried again" once per fold (e.g. 15 for 3 folds ×
    5 candidates) rather than recognizing it as the same 5-candidate
    search evaluated across different data windows, which is walk-forward
    validation working as intended, not additional data-snooping.
- **Data span, in years**: the widest `[start_ms, end_ms]` range observed
  across *any* of this `strategy_id`'s matching records' `data_range`
  fields, converted via the project's existing fixed 365-day/year
  convention (`metrics/metrics.py`'s `_DAYS_PER_YEAR`, not 365.25).
  **Known, documented limitation**: this is derived purely from the
  experiment log, not an independent query against
  `data/store.py`/the sqlite kline store — so "data span available" here
  really means "the widest span this `strategy_id`'s own logged runs have
  actually touched so far", which in practice is usually the full
  `load_research_klines()` result a strategy was run against, but is not
  guaranteed identical to BingX's true retention depth for the
  symbol/interval. Kept this way deliberately, to keep the function a
  pure reader of the experiment log (no DB coupling, easier to test, and
  matches the task's own literal instruction — "reads the experiment
  log") rather than because the DB-query alternative wasn't considered.
- **Risk tier**: `total_combinations / data_span_years`, tiered into
  `"low"` (≤10/year), `"moderate"` (≤30/year), `"high"` (>30/year), or
  `"unknown"` (nothing logged yet, or no usable data span could be
  computed).

**Explicitly a documented approximation of the MinBTL spirit, not a
literal reproduction of Bailey et al.'s exact statistical test** — the
real MinBTL statistic involves the expected maximum Sharpe ratio under
N independent trials (an extreme-value-theory argument, roughly
`E[max Sharpe under pure luck] ≈ √(2 ln N)`) compared against an observed
Sharpe, not a flat combinations-per-year ratio. This task's own brief was
explicit that inventing a precise numeric formula without being able to
confidently justify it is worse than a simple, clearly-labeled heuristic,
so that's what this is: a transparent ratio with tier boundaries
documented as **a defensible, not rigorously-derived, convention** —
chosen conservatively (flagging risk earlier rather than later) given
this project's own thin real data depth (~0.57 years for the 15m
strategies, ~1.84 years for the 1h `hourly_momentum` strategy, per
`.planning/sr-c-walkforward-holdout.md`/`.planning/sr-f-risk-management-
and-1h-variant.md`) and its still-small strategy family count. The
property actually asserted and tested (rather than the exact tier
boundaries) is **monotonicity**: more combinations tried over the same
data span must never report a *lower* risk tier
(`test_check_combination_count_risk_level_ordering_is_monotonic_in_combinations_per_year`)
— that has to hold regardless of exactly where the boundaries are drawn,
and is the property that actually matters for a heuristic like this one.

**Warning, not a hard block** — matching this project's existing
established pattern (`research/holdout.py`'s single-access-with-override
enforcement is the precedent CLAUDE.md's own design explicitly named for
this task): `check_combination_count` never raises or prevents anything.
It returns a result whose `warning` field states the finding in plain
language, always populated (not only when risk is elevated), so a
solo researcher with a legitimate reason to run more combinations than
the heuristic likes can still do so — the tool's job is to make that
visible, not to silently prevent it.

**Defensive aggregation, not silent guessing**: if a `parent_run_id`
group's child records report inconsistent `total_candidates` values
(a real anomaly, not an expected state), the conservative max is used and
a human-readable note is appended to `OverfittingCheckResult.notes`
(never silently resolved one way or the other). If a group's records
report no `total_candidates` at all (e.g. older/malformed data), the
function falls back to the observed record count for that group, again
with a note. Neither case crashes the function.

## Real-data results

Ran against the real, accumulated `runs/experiments.jsonl` produced by
Tasks D/E/F (274 records before this task) plus two real verification
runs this task added.

### MinBTL-style tracker against the real accumulated log (pre-existing strategy_ids, before this task's own demo runs)

| `strategy_id` | combinations tried | data span (years) | combos/year | risk level |
|---|---|---|---|---|
| `hourly_momentum` | 12 | 1.84 | 6.5 | **low** |
| `regime_momentum` (v2, risk-managed) | 12 | 0.57 | 21.2 | **moderate** |
| `regime-momentum-btc-15m` (v1) | 12 | 0.57 | 21.2 | **moderate** |
| `ma-crossover-task-d-e2e` | 6 | 0.57 | 10.6 | **moderate** |
| `task-c-e2e-verification` | 1 | 0.57 | 1.8 | **low** |

This is a genuinely sensible result, not just a working demo: the two
`regime_momentum` strategy_ids (v1 and v2, both real hypotheses tested on
the thin ~0.57-year 15m dataset) land in **moderate** risk despite a
fairly modest 12 combinations tried — exactly the "thin real 15m data
depth" concern already flagged independently in
`.planning/sr-c-walkforward-holdout.md`/`.planning/sr-e-regime-
momentum.md`/`.planning/sr-f-risk-management-and-1h-variant.md`, now
surfaced quantitatively rather than only qualitatively. `hourly_momentum`,
tested against far more real 1h data (1.84 years), lands in **low** risk
despite an identical 12-combination count — directly illustrating why the
right MinBTL-spirit denominator is data span, not trade count or a flat
per-strategy budget.

### Parameter sensitivity check against a real live walk-forward run (`ma_crossover`, real cached BingX 15m data)

Run via `research.holdout.load_research_klines(1763264700000,
1785035700000)` → 19,870 bars (`2025-11-16T03:45:00Z` →
`2026-06-11T03:00:00Z`, correctly clamped to the holdout cutoff), the
same provisional windows every prior task used (`train_bars=8640,
validate_bars=2880, step_bars=2880` → 3 folds), `fee_bps=5,
slippage_bps=2`, `sensitivity_extractor=lambda s: (s.fast_window,
s.slow_window)`. `strategy_id="ma-crossover-task-g-sensitivity-demo-v2"`
(the corrected, authoritative run — see "the real wiring bug" above for
why a `-v2` id; the pre-fix run, `strategy_id=
"ma-crossover-task-g-sensitivity-demo"`, is preserved as-is in the local
log per this project's established "don't clean up the audit trail"
convention, same as `.planning/sr-f-risk-management-and-1h-variant.md`'s
own precedent for a pre-bugfix run):

| fold | winning `(fast, slow)` | winning total_return | is_robust | reason |
|---|---|---|---|---|
| 0 | (13, 34) | +0.1179% | **True** | 7/8 valid neighbors (88%) also profitable in-sample |
| 1 | (13, 34) | -0.0655% | **False** | winning candidate itself not profitable in-sample — robustness not meaningful to claim |
| 2 | (20, 50) | +0.0635% | **True** | 7/8 valid neighbors (88%) also profitable in-sample |

Folds 0 and 2 show a genuinely flat, robust-looking in-sample surface
around their respective winners (nearby `(fast, slow)` values are mostly
also profitable, not just the exact winner) — a real, if modest, positive
signal about *these two folds'* selection process specifically (not a
claim that `ma_crossover` itself has edge; it remains the pipeline-
validation placeholder it always was, per `ma_crossover.py`'s own
docstring). Fold 1's winner isn't profitable in-sample at all, so the
check correctly declines to call it either robust or spiky — there's no
"good performance" here for nearby values to be flat or spiky *around*.

**A real, honest second-order finding from running this for real**:
enabling the sensitivity check substantially increases the MinBTL
combination count for the run it's attached to — this demo run alone
logged **43 records** (3 folds × 5 real grid candidates = 15, plus 3
folds × 9 sensitivity evaluations [1 winner re-check + 8 neighbors] = 27,
plus 1 final standalone walk-forward record = 43), and
`check_combination_count("ma-crossover-task-g-sensitivity-demo-v2")`
reports:

```
total_combinations_tried: 33  (5 real grid candidates + 28 standalone sensitivity/final records)
data_span_years: 0.567
combinations_per_year: 58.2
risk_level: HIGH
```

This is a deliberate, documented consequence of the design choice above
(sensitivity-check evaluations count toward the MinBTL total as their own
standalone combinations, since they are genuine additional configurations
backtested against the same real data, even though they never influence
selection) — not a bug. It's worth stating plainly since it's a real
interaction between this task's two features: **turning on the parameter-
sensitivity check on a strategy with thin real data will itself measurably
raise that strategy's MinBTL-style risk tier**, simply by virtue of
evaluating more configurations against the same limited history. This is
the correct, conservative behavior for an overfitting-risk heuristic, but
a future reader should not be surprised that enabling this diagnostic
raises the other diagnostic's own reported risk.

## TDD

Every new module's tests were written first and confirmed failing
(`ModuleNotFoundError` on `research.robustness`/`research.overfitting_check`)
before the corresponding production code existed — verified directly by
temporarily moving both new production modules out of the tree and
re-running the full new test selection, which failed exactly as expected
during this task, before being restored.

- **`test_robustness.py`** (14 tests): `perturb_candidate`'s arithmetic in
  isolation (default ±10%/±25% neighbor generation, holding other
  positions fixed, half-up rounding on an exact `.5` tie, clamping to
  `min_value`, deduplication of rounding collisions, rejecting empty
  fractions); `check_parameter_sensitivity` against a fully synthetic
  `TrainableStrategy` double with a controlled scoring surface — both a
  constructed "flat" (every neighbor also profitable) and "spiky" (only
  the winner profitable) surface, and the winner-itself-unprofitable
  degenerate case; invalid-neighbor handling (a double that raises
  `ValueError` for `fast >= slow`, proving one bad neighbor doesn't crash
  the whole check); a real integration test against
  `ma_crossover.MACrossoverTrainable`; `ParameterSensitivityResult.to_dict()`
  shape and JSON-serializability.
- **`test_overfitting_check.py`** (13 tests): counting/aggregation
  correctness (standalone-runs-count-as-one, summing `total_candidates`
  once per distinct `parent_run_id`, summing across multiple distinct
  `parent_run_id`s, ignoring other `strategy_id`s and `holdout_access`
  records, computing data span from the *widest* observed range across
  multiple runs); risk-tiering (`"low"` for few combinations over much
  data, `"high"` for many over little data, and the monotonicity property
  described above); defensive fallback handling (missing `total_candidates`,
  inconsistent `total_candidates` within one group); `to_dict()`
  JSON-serializability.
- **`test_walkforward.py`** (+2 tests, purely additive to the existing
  file): `sensitivity_extractor` defaults to `None` and leaves
  `FoldResult.parameter_sensitivity`/the logged record's key both absent
  (proving the new parameter doesn't change existing behavior at all);
  passing `sensitivity_extractor` attaches a real per-fold result to both
  the in-memory `FoldResult` and the logged record.

Full suite: **359 passed** (was 330 immediately before this task's branch
point, which exactly matches `.planning/sr-f-risk-management-and-1h-
variant.md`'s own final count — confirming no other work landed on `main`
between sr-f and this task's branch point). This task adds exactly
**29 new tests** (14 + 13 + 2, matching the counts above), 330 + 29 = 359.
Nothing from any prior task regressed — confirmed by running the
complete, unfiltered `uv run pytest` suite, not just the new test files.

## Judgment calls resolved without asking

- **`check_parameter_sensitivity` reuses each strategy's own `fit()`
  mechanism (single-candidate grid override) rather than requiring a
  separate "build a `Strategy` directly from params" factory function.**
  Chosen because every real strategy in this project already exposes
  exactly this mechanism (the `params["candidates"]` convention), so no
  strategy needs new code to support this check, and because it
  automatically reuses each strategy's own construction/validation/
  logging path rather than risking a second, possibly-diverging
  implementation of "build a bound Strategy from these params".
- **A perturbation is evaluated by re-running the actual backtest
  (`run_backtest` + `compute_metrics`) on the `Strategy` `fit()` returns,
  not by reading back some score `fit()` might have computed
  internally.** `fit()`'s return type is just a bound `Strategy` (no
  score attached) — re-scoring is the only generically correct way to get
  a comparable number back, and it also means `check_parameter_
  sensitivity` is scoring the winner the exact same way it scores every
  neighbor (apples-to-apples), rather than trusting a caller-supplied
  winning score computed by a possibly-different code path.
- **Round-half-up, not Python's builtin round-half-to-even**, for
  perturbation arithmetic — see `perturb_candidate`'s docstring; this is
  a predictability choice, not a correctness one (both are defensible
  roundings), but half-up is easier for a human to reason about by hand
  when auditing a specific perturbation.
- **`is_robust` requires the winner to be profitable in-sample as a
  precondition**, rather than treating "not robust" as the only signal a
  losing winner could produce. Distinguishing "winner failed, so
  robustness doesn't apply" from "winner succeeded but its neighbors
  didn't" preserves a meaningful distinction a reviewer would otherwise
  have to reconstruct by hand from the raw numbers every time.
- **Sensitivity-check-driven `fit()` calls log as standalone records
  (`parent_run_id=None`), not grouped under the enclosing walk-forward
  run's own `run_id`.** See "A real wiring bug found and fixed" above —
  this was the corrected outcome of a real bug, not an initial design
  choice, but it's the right one on its own merits: grouping them would
  conflate two conceptually different kinds of "combinations tried"
  (selection-affecting grid candidates vs. post-selection diagnostic
  checks) under one `parent_run_id`, corrupting
  `check_combination_count`'s per-group `total_candidates` assumption.
- **Sensitivity-check evaluations DO count toward the MinBTL combination
  total** (each as its own standalone +1), even though they never
  influence which candidate gets selected. Considered excluding them
  (arguably more "pure" to the MinBTL framework's actual concern —
  selection bias specifically) but rejected: they are still genuine
  additional configurations backtested against the same limited real
  data, and this project's overfitting-safeguard philosophy (see the
  holdout re-access override, the "warning not a block" pattern) already
  favors flagging risk earlier and more conservatively over a narrower
  reading that would under-count real evaluation activity.
- **MinBTL tier boundaries (10/30 combinations-per-year) are a documented
  heuristic, deliberately not derived from Bailey et al.'s actual
  formula.** See Finding 1/`overfitting_check.py`'s module docstring for
  the full reasoning; the property actually tested is monotonicity, not
  the exact boundary values.
- **`check_combination_count` reads only the experiment log, never
  queries `data/store.py` directly**, even though that would give a more
  literally accurate "data available" figure. Chosen to keep the function
  a pure, simply-testable reader of one artifact (`runs/experiments.jsonl`)
  and because the task's own brief said to read the experiment log
  specifically — documented as a known limitation, not silently assumed
  away.
- **Full CSCV/PBO deliberately not attempted in any partial form** — see
  Finding 3 above.

## Deliberately out of scope

- **Retrofitting `sensitivity_extractor` onto every existing strategy's
  own production call sites** (e.g. adding it to whatever script produced
  `regime_momentum`'s/`hourly_momentum`'s real sr-e/sr-f results). The
  task brief explicitly said demonstrating the utility generically with
  at least one strategy is sufficient; not required to retrofit all four.
- **Changing CLAUDE.md's Backtest/Walk-Forward Eligibility Bar, or
  making either of this task's checks part of that bar's pass/fail
  logic.** Both remain purely diagnostic/advisory (a `parameter_
  sensitivity` field attached to fold results, a `check_combination_count`
  warning) — CLAUDE.md's Eligibility Bar itself is unchanged and this
  task doesn't touch it.
- **Full CSCV/PBO** — see Finding 3.
- **A precise, literally-derived MinBTL numeric formula** — see Finding 1
  and the "documented approximation" framing throughout.
- **Any change to `runs/experiments.jsonl`'s existing schema fields** —
  both additions are strictly new, optional keys (`parameter_sensitivity`
  on a fold record); every existing field/record shape is untouched.
