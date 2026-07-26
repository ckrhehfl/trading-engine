# Strategy Research Task C: walk-forward harness, holdout-split enforcement, experiment log

## Scope note

Third of four sequenced build tasks implementing CLAUDE.md's "Strategy
Research Operational Design (2026-07-25)" section — see that section for
the full design this task follows. Depends on Task A (`python/data/`,
merged) and Task B (`KlineWindow` + `python/metrics/`, merged), both read
in full before starting. This doc covers `python/research/` (new package:
`walkforward.py`, `holdout.py`, `experiment_log.py`) plus a small, deliberate
addition to Task A's `python/data/store.py` (see "Judgment calls" below).

## What was built

- **`python/research/experiment_log.py`** — append-only
  `runs/experiments.jsonl`. `log_run(...)` writes one `record_type:
  "backtest_run"` entry with every formal schema field from CLAUDE.md's
  design (`run_id`, `strategy_id`/`strategy_version`, `params`,
  `fold_results`, `aggregate_metrics`, `data_range`,
  `walk_forward_config`, `fee_bps`/`slippage_bps`, `is_holdout_run`,
  `code_version` via `git rev-parse HEAD`, `parent_run_id`/
  `candidate_index`/`total_candidates` for Task D's grid search).
  `log_holdout_access(...)` writes one `record_type: "holdout_access"`
  entry. Both go through one `_append_record` helper: one `write()` call
  of a complete JSON line, then `flush()` + `os.fsync()` before
  returning — durable on disk when the call returns, not just buffered.
  `read_records(...)` is a generator that yields every parsed record in
  write order, tolerating a truncated *final* line (skip, don't raise —
  the documented interrupted-write signature) while still raising on a
  malformed *non-final* line (real corruption, not the documented case).
  `Decimal`/`datetime`/dataclass values are handled transparently via a
  `json.dumps(..., default=...)` hook rather than requiring every call
  site to pre-stringify.
- **`python/research/holdout.py`** — `load_holdout_config` reads the
  git-tracked cutoff config. `load_research_klines(start_ms, end_ms)`
  unconditionally clamps `end_ms` to `min(end_ms, holdout_cutoff_ms)`,
  warning when it actually clamps. `load_holdout_klines(start_ms, end_ms,
  *, strategy_id, i_understand_this_is_holdout_data, force_reclaim_reason=None)`
  raises unless the keyword is explicitly `True`, rejects `start_ms`
  before the cutoff, and enforces single access per `strategy_id` by
  scanning `runs/experiments.jsonl` for an existing `holdout_access`
  record before proceeding — raising `HoldoutAlreadyClaimedError` if one
  exists and no non-blank `force_reclaim_reason` override is given. The
  claim (`log_holdout_access`) is only written after a successful read,
  so a failed call (bad range, etc.) never burns the one-shot claim.
- **`python/research/walkforward.py`** — `generate_folds(n_bars,
  train_bars, validate_bars, step_bars)` produces rolling, fixed-size
  fold boundaries by pure bar-count arithmetic; `step_bars ==
  validate_bars` (the documented default) gives non-overlapping,
  contiguous validate windows, but the function itself has no opinion on
  overlap — it works generically for whatever sizes are passed, and
  returns `[]` (not an error) when the data is too short for even one
  fold. `TrainableStrategy` (a `Protocol` with `fit(train_klines,
  params) -> Strategy`). `run_walk_forward(...)` generates folds, calls
  `strategy.fit` once per fold on exactly that fold's train window, scores
  the returned `Strategy` via the *existing* `backtest.engine.run_backtest`
  against that fold's validate window, reduces each `BacktestResult` to a
  `Metrics` via `metrics.metrics.compute_metrics`, aggregates across
  folds, and calls `experiment_log.log_run` as its last action —
  unconditionally, including for zero folds — before returning a
  `WalkForwardResult`.
- **`configs/research/holdout.json`** — git-tracked cutoff config, real
  numbers from the 2026-07-26 real BingX run (see "Real verification
  run" below), not placeholders.
- **`python/data/store.py::fetch_klines`** — small, deliberate addition
  (see "Judgment calls").

63 new tests: `test_store.py` (+7, `fetch_klines`), `test_experiment_log.py`
(14), `test_holdout.py` (15), `test_walkforward.py` (27 — 24 from the
initial implementation + 3 added responding to CodeRabbit's review, see
"CodeRabbit review findings" below). Full suite: **221 passed** (was 158
before this task — 221-158=63, matching the new-test count above exactly,
confirming nothing from Task A/B regressed).

## Holdout-cutoff reasoning (the real judgment call this task required)

Task A's real backfill found only ~252 days of actual BingX retention for
`BTC-USDT`/`15m` (24,199 bars, run on 2026-07-25/26) — far short of what
CLAUDE.md's design assumed when it set the provisional walk-forward
windows. This task re-ran the backfill for real on 2026-07-26 (see below)
and got a very similar number, confirming the retention window is real
and rolling (drifted forward by ~12h in the one day between the two
runs, as `sr-a-data-pipeline.md` predicted it would).

CLAUDE.md's design offered two framings for the holdout size, given this
thin depth: "the most recent 15-25% of available data" or "a fixed
~30-45 day trailing window." At ~252 days total, these don't fully
overlap — 15% is 37.8 days, so a 30-day holdout satisfies the fixed-
window framing but falls short of the percentage floor. **45 days is the
value that satisfies both framings simultaneously** (45/252 = 17.9%,
within 15-25%, and at the upper bound of the 30-45 day range) — chosen
over a smaller holdout (e.g. 30 days) specifically because a holdout's
job is to be a genuine, materially-sized, untouched final check against
everything that will be iterated on with the research split; shrinking
it to preserve one more walk-forward fold on the research side would be
optimizing for a number that's already going to be short of the
credibility floor regardless (see below), at the cost of the thing the
holdout actually exists to protect against.

**Trade-off, stated plainly**: a 45-day holdout leaves ~207 days
(19,870 bars) of research data, which supports only **3** non-overlapping
folds at the provisional default windows (train=8,640, validate=2,880,
step=2,880) — down from 5 if the full 24,199-bar depth were used
undivided (locked in as
`test_generate_folds_matches_the_real_bingx_depth_finding_from_task_a`).
A 30-day holdout would have preserved 4. This task does not resolve that
trade-off — it's the same "5 folds vs. 8-10 required" tension CLAUDE.md's
design already flagged as a human decision, now compounded by the
holdout carve-out. Recorded here, not resolved.

Committed value: `holdout_cutoff_ms: 1781147700000`
(`2026-06-11T03:15:00Z`), reserving `2026-06-11T03:15:00Z` onward as
holdout (4,320 bars / 45 days) and `2025-11-16T03:45:00Z` –
`2026-06-11T03:00:00Z` as research data (19,870 bars / ~207 days). See
`configs/research/holdout.json`'s own `rationale` field for the same
reasoning in the git-tracked artifact itself.

## Real verification run (2026-07-26, live BingX)

Ran `python -m data.backfill --symbol BTC-USDT --interval 15m --start
2025-11-01T00:00:00+00:00 --base-url https://open-api.bingx.com` for
real (no cached data existed locally — `python/data/var/` is gitignored
and had never been populated in this checkout). 24,191 new rows in ~16s.

**Actual real depth as of this run**: 24,191 bars,
`2025-11-16T03:45:00Z` → `2026-07-26T03:15:00Z`, ~252.0 days — consistent
with Task A's 2026-07-25 finding (24,199 bars) modulo the ~1-day gap
between runs and the retention window's rolling drift.

Then, for real, against `load_research_klines`'s output (bounded by the
committed cutoff above) with the provisional default windows (train=8,640,
validate=2,880, step=2,880), fee_bps=5, slippage_bps=2, and a trivial
always-`None` placeholder `TrainableStrategy` (inline in the verification
script, not committed — Task D owns the real placeholder strategy):

- **Research klines loaded: 19,870 bars** (`2025-11-16T03:45:00Z` –
  `2026-06-11T03:00:00Z`), confirming the clamp engaged correctly against
  real data.
- **Fold count: 3.** All three folds have `num_trades: 0`,
  `sharpe_ratio: null`, `profit_factor: null` (as expected — the
  placeholder strategy never trades, and `metrics.metrics`'s degenerate-
  input rules make that `null`, never `0`/`inf`). A real
  `record_type: "backtest_run"` entry was written to
  `runs/experiments.jsonl` with `run_id`, full `walk_forward_config`
  (`fold_count: 3`), `code_version` (real git HEAD sha, 40 hex chars,
  confirmed), and all other schema fields populated as designed.
- **`load_holdout_klines` for real**: returned 4,320 bars
  (`2026-06-11T03:15:00Z` – `2026-07-26T03:00:00Z`), correctly all
  at/after the cutoff. Logged a real `holdout_access` record.
- **Single-access enforcement for real**: a second `load_holdout_klines`
  call for the same `strategy_id` (`task-c-e2e-verification-holdout`)
  raised `HoldoutAlreadyClaimedError` immediately, without touching the
  database — confirmed by the error message quoting the first call's
  real `accessed_at` timestamp.

**Honest finding, flagged not resolved**: at the current real BingX
depth, the provisional default walk-forward windows produce **3 folds**
on the research split (5 on the undivided full depth) — well short of
the "minimum 8-10 folds for the result to be considered credible"
eligibility-bar floor CLAUDE.md's design specifies. `run_walk_forward`
and `generate_folds` were built to work correctly for *whatever* fold
count results (including zero), per this task's explicit brief, rather
than assume the floor will be met — so the harness itself isn't blocked
by this. But no strategy run through it today can credibly clear the
eligibility bar's fold-count requirement as currently specified. This is
recorded here for a human decision (shrink the windows for more folds,
accept fewer folds with a more conservative bar, or wait for more BingX
history to accumulate) — not something this task resolves unilaterally,
per its brief.

## Judgment calls resolved without asking

- **`store.py::fetch_klines` added.** Task A's `store.py` had no read
  path — only `upsert_klines`/`find_missing_ranges` (a gap-diffing
  helper, not a data reader). `load_research_klines`/`load_holdout_klines`
  need to actually read typed klines back out of the cache, so a minimal
  `fetch_klines(conn, symbol, interval, start_ms, end_ms) -> list[KlineRow]`
  was added — the direct read-path counterpart to `upsert_klines`,
  following the exact same query-scoping/`Decimal`-parsing conventions
  already established by `find_missing_ranges`/`upsert_klines`. 7 new
  tests, TDD (confirmed failing on `ImportError` before implementation).
  Not a CODEOWNERS-matched path; a strictly additive, minimal, required
  extension — not a refactor of anything Task A shipped.
- **`load_holdout_klines` takes a required `strategy_id` keyword,
  despite CLAUDE.md's Build-section code snippet for this function only
  showing `i_understand_this_is_holdout_data` explicitly.** The single-
  access enforcement described in the same design section is defined in
  terms of `strategy_id` ("scans the log for an existing `holdout_access`
  record for this `strategy_id`"), so it must be a real parameter — the
  snippet is abbreviated, not exhaustive. Documented in `holdout.py`'s
  own module docstring, not silently added.
- **Loader signatures gained keyword-only `db_path`/`holdout_config_path`/
  `runs_path` parameters** beyond the design's literal
  `(start_ms, end_ms)` snippets — same pattern Task A used for
  `sync_range` (brief's positional args preserved exactly; infrastructure
  dependencies as keyword-only args with sensible defaults) so tests can
  inject `tmp_path` fixtures without needing a real SQLite file or a real
  `runs/` directory, while the defaults (`data.backfill.DEFAULT_DB_PATH`,
  `"configs/research/holdout.json"`, `"runs/experiments.jsonl"`) still
  make the plain call site work when actually run from the repo root.
- **`symbol`/`interval` are not loader parameters.** `load_research_klines`/
  `load_holdout_klines` take only `(start_ms, end_ms, ...)`, reading
  `symbol`/`interval` from the holdout config itself. Matches CLAUDE.md's
  existing single-symbol (`BTC-USDT`) scope decision (Strategy Research
  Methodology section) — no caller in this project passes a different
  symbol today, and the config file already carries these fields per the
  design's own JSON shape, so plumbing them through as separate
  parameters would be surface area with no current use.
- **`runs/experiments.jsonl`'s persisted `fold_results` entries are
  scalar-only summaries** (`starting_equity`, `final_equity`,
  `total_return`, `sharpe_ratio`, `max_drawdown`, `win_rate`,
  `num_trades`, `profit_factor`) — deliberately omitting each fold's full
  `equity_curve`/`closed_trades` arrays. CLAUDE.md's design says the log
  should capture "per-fold and aggregate metrics," which reads as the
  summary numbers the Eligibility Bar is expressed in terms of, not the
  full underlying time series (which would make the log file grow
  unboundedly with every walk-forward run at this data scale). The full
  `Metrics` (including `equity_curve`/`closed_trades`) stays available on
  the in-memory `WalkForwardResult.folds[i].metrics` for any caller
  (e.g. Task D) that needs it — only the persisted log is summarized.
- **`FoldResult` is a flat dataclass** (`fold_index`,
  `train_start_index`, ..., `metrics`, `backtest_result`) rather than
  nesting a separate `Fold` object under a `.fold` attribute — found
  during TDD (an early draft nested `Fold`, and the first test-writing
  pass immediately wanted `fold_result.train_start_index` directly, not
  `fold_result.fold.train_start_index`); changed before any production
  code shipped, not after. `generate_folds` itself still returns `Fold`
  objects (used internally and directly tested for boundary arithmetic).
- **`_aggregate_metrics`'s `None`-handling for zero folds** returns
  `all_folds_positive_sharpe: False` (not `None`/omitted) for the empty-
  folds case — an empty `all()` over zero folds is vacuously `True` in
  Python, which would be actively misleading here (a strategy that
  couldn't even be evaluated should never read as "every fold positive"),
  so this is handled as an explicit special case rather than left to
  Python's default `all([])` behavior.

## TDD

Every module: tests written first, confirmed failing (`ModuleNotFoundError`/
`ImportError` — nothing to import yet, not a wrong-assertion failure),
then minimum implementation to pass, in this order: `store.fetch_klines`
→ `experiment_log` → `holdout` → `walkforward` (matching each module's
real dependency order). `walkforward.py`'s `FoldResult` flattening (see
above) is a genuine example of TDD driving a design change before any
production code existed to make it awkward to change.

## CodeRabbit review findings

First review pass was delayed by the adaptive rate limit (see CLAUDE.md's
"Rate limits" section) — `@coderabbitai rate limit` gave an 18-minute
ETA, which was waited out before `@coderabbitai review` was requested,
matching the documented procedure rather than polling blindly. Five
actionable findings on that pass, all accepted (all low-risk, non-
CODEOWNERS-matched Python research code):

- **Doc test-count arithmetic was wrong.** This doc originally claimed
  "61 new tests" / "218 passed (was 157...)"; the real collected counts
  were 7+14+15+24=60, not 61, and the actual pre-Task-C baseline was 158,
  not 157 (`218 - 60 = 158`, confirmed by re-collecting each file).
  Fixed in the "What was built" section above (now reflects the final
  counts after the fixes below, not the original wrong ones).
- **`experiment_log._append_record` didn't fsync the containing
  directory on first-ever creation of `runs/experiments.jsonl`.**
  `os.fsync(f.fileno())` only guarantees the file's own bytes are
  durable — if that call is what created the file, the directory entry
  pointing to it is separate metadata a crash could still lose (the
  well-known POSIX "fsync doesn't sync the directory" gap). Real stakes
  here: this log is the *only* basis for the holdout single-access
  claim, so a lost directory entry after the very first write could in
  theory let a duplicate holdout access go unnoticed. Fixed: on first
  creation, open the parent directory read-only and `os.fsync` its fd
  too, best-effort (`except OSError: pass`) since directory-fd fsync is
  POSIX-only and this durability layer is already a bonus on top of the
  file-level fsync that always runs regardless.
- **`run_walk_forward`'s `train_bars`/`validate_bars`/`step_bars`/
  `fee_bps`/`slippage_bps` were positional**, unlike CLAUDE.md's Build-
  section signature snippet's literal ordering intent but exactly the
  shape (three adjacent bare `int`s, then two adjacent `Decimal`s) a
  transposed-argument bug hides in undetected by any type checker. Made
  keyword-only (`*` moved before them) — zero-regression, since every
  call site already used keyword args. New test
  `test_run_walk_forward_requires_train_validate_step_fee_slippage_to_be_keyword_only`
  locks this in.
- **`fee_bps`/`slippage_bps` had no non-negative validation** before
  being passed to `run_backtest`/`compute_metrics` — `generate_folds`
  already fails loud on non-positive `train_bars`/`validate_bars`/
  `step_bars`, but a negative fee/slippage would have silently produced
  an over-optimistic backtest result, directly undermining CLAUDE.md's
  Eligibility Bar. Added the same fail-loud `ValueError` pattern; two new
  tests (`test_run_walk_forward_rejects_negative_fee_bps`/
  `..._slippage_bps`).
- **A test's name didn't match what it tested.**
  `test_run_walk_forward_rejects_non_positive_fee_or_slippage_config_is_not_required`
  actually only tested that a `generate_folds` `ValueError` (from
  `train_bars=0`) propagates — it never touched `fee_bps`/`slippage_bps`
  at all, which could have misled a future reader into believing fee/
  slippage validation was already covered. Renamed to
  `test_run_walk_forward_propagates_generate_folds_valueerror` and given
  a `match="train_bars"` assertion for precision (also resolves a Ruff
  PT011 nitpick the same review pass surfaced on the same test).

All fixes pushed in one follow-up commit (batched, not one push per
finding, per CLAUDE.md's rate-limit-avoidance guidance) before
requesting re-review. Final count after fixes: `test_walkforward.py`
went from 24 to 27 tests (3 added); full suite 221 (was 218 at the first
review pass, 221 after).

## Deliberately out of scope

- **Evaluating CLAUDE.md's Eligibility Bar.** `run_walk_forward` computes
  and logs every figure the bar is expressed in terms of (per-fold
  Sharpe, fold count, drawdown, trade count, profit factor) but does not
  itself decide pass/fail — per this task's explicit brief.
- **A real `TrainableStrategy`.** The real verification run's placeholder
  always returns `None` and lives only in an uncommitted ad-hoc script.
  Task D builds the real (MA-crossover) placeholder and the grid-search
  orchestrator that uses `parent_run_id`/`candidate_index`/
  `total_candidates`.
- **Resolving the fold-count-vs-credibility-floor tension**, or changing
  the provisional window sizes / eligibility bar's fold-count floor —
  both are explicitly human-approved defaults per CLAUDE.md; this task
  flags the real number, it doesn't change either.
- **Locking (`flock` etc.) for concurrent `runs/experiments.jsonl`
  writers.** Single-writer assumption only, per CLAUDE.md's design and
  `experiment_log.py`'s own docstring — nothing in this project's actual
  usage pattern needs concurrent writers yet.
