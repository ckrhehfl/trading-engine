# Strategy Research Task V: the pre-registered daily TSMOM attempt, executed

## Scope note

This task executes exactly what `sr-u` pre-registered
(`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`)
against the real `1d` early-window holdout
(`configs/research/holdout_1d.json`), exactly once, and reports the true
result using the registration's own pre-committed language. Nothing here
was designed, chosen, or tuned after seeing the result -- the hypothesis,
parameters, criteria, and outcome-interpretation text all came from `sr-u`,
committed before any `1d` price data was ever loaded by this project.

## What was built: the execution glue

`sr-s`'s general-purpose `python -m research.run_preregistered` deliberately
refuses any `data.split == "holdout"` registration (a scope guard against
accidental holdout access). This task adds
`python/research/run_preregistered_holdout.py`, a small, dedicated,
single-purpose runner for exactly the opposite case: it refuses a
*research*-split registration and drives a *holdout*-split one, composing
the existing pieces directly rather than through
`research.walkforward.run_walk_forward`'s fold machinery (which does not
fit a single-window confirmation -- there is nothing to walk forward
across).

Pipeline: `load_preregistration` -> `research.holdout.load_holdout_klines`
(the real, single, enforced-once access) -> `research.run_preregistered
.build_strategy` (reused, not duplicated) -> `strategy.fit()` (no search,
`total_candidates: 1`) -> `backtest.engine.run_backtest` on the **same**
window `fit()` was given (there is no separate train/validate split at this
level -- a zero-fitted-parameter strategy has nothing to overfit to by
being evaluated on the window it was "fit" against) -> `metrics.metrics
.compute_metrics` -> `research.eligibility.psr_from_equity_curve` (measured
moments, not the normal-assumption fallback, on the daily-resampled -- here
a near no-op, since the native bar is already 1 day -- equity curve) ->
`evaluate_gating` (every one of the registration's own gating fields
compared against the observed result, reduced to the PASS/INCONCLUSIVE/FAIL
region) -> a direct `research.experiment_log.log_run` call with
`is_holdout_run=True`, `preregistration_id`, `preregistration_sha256`.

Two independent recomputation checks (`verify_trade_floor`,
`verify_detection_floor`) run before the holdout access, as a second,
execution-time check (not a duplicate of `research.preregistration`'s own
load-time validation) that this script's own understanding of the
registration's declared geometry agrees with what was committed. Both
matched exactly for the real registration (53-trade floor, 0.9567 detection
floor) -- confirmed directly in the real run's own log record and by two
dedicated unit tests reproducing CLAUDE.md's own published numbers for this
exact geometry (1,079 bars, `bars_per_day=1`).

**TDD**: 26 tests in `python/tests/test_run_preregistered_holdout.py`, all
against synthetic `tmp_path` fixtures (a fixture sqlite cache, a fixture
`holdout_side: "before"` config, a fixture experiment log) -- covering CLI
argument handling, refusal of a research-split registration, refusal of a
second holdout-claim attempt for the same `strategy_id` (both via a
hand-seeded `holdout_access` record and via a real first-call-then-second-
call sequence through the module's own claim mechanism), the frequency-
scaled trade-floor and detection-floor recomputations, every branch of
`evaluate_gating` (PASS / three independent INCONCLUSIVE triggers / two FAIL
triggers / the zero-losing-trades profit-factor edge case), and that a
`force_reclaim_reason` is honored only when a caller explicitly supplies
one -- never on the module's own initiative. Full suite: **1,172 passed**
(1,146 on `main` at the branch point + 26 new), nothing regressed.

## Execution: cache state, a bug hit and fixed, the real invocation

**Cache state found**: `python/data/var/klines.sqlite3` already had the
complete registered `1d` window -- **1,079 bars, zero gaps**, exactly
`start_ms=1620950400000` through the last bar at `1714089600000` (one day
short of the registered exclusive `end_ms=1714176000000`, as expected for a
half-open range), confirmed by a direct SQL query before touching the
holdout-access machinery at all. **No backfill was needed** -- `sr-t`'s
2026-07-28 backfill already covered this exactly.

**One bug hit, fixed, and re-run -- not a second attempt at the
hypothesis.** The first real invocation ran with the process's working
directory inside `python/`, using a relative path
(`../configs/research/preregistrations/...`) for the registration file
itself. That resolved fine, but the registration's own
`data.holdout_config_path` field (`"configs/research/holdout_1d.json"`, a
relative path baked into the committed file, by design, matching every
other holdout config path in this project) then failed to resolve against
that same working directory, raising `FileNotFoundError` inside
`load_holdout_config`, **before** `research.holdout.load_holdout_klines`
ever reached the point where it loads klines or writes the `holdout_access`
claim record. Confirmed directly against the real log: the claim was not
burned by this failure (`runs/experiments.jsonl` carries exactly one
`holdout_access` record for `strategy_id="daily-tsmom-ensemble"`, from the
successful second invocation only). Fixed by re-invoking with the process's
working directory at the repository root instead (where every relative
path in this project's configs is written to resolve from), with
`PYTHONPATH` pointing at `python/` so the package still imports. This is
exactly the "a bug in your glue code... fix it and re-run" case the task
brief explicitly distinguishes from "another attempt at the hypothesis" --
no code, parameter, or data was touched between the failed and the
successful invocation, only the invocation's own working directory.

**The real, successful, complete invocation**, in its canonical,
repository-root-relative form (run from the repository root, with
`python/` on `PYTHONPATH` so the package imports; both `--runs-path` and
`--db-path` default to exactly these relative paths, so they are shown
explicitly here only for clarity, not because a real clone needs to
override them):

```text
PYTHONPATH=python python/.venv/bin/python -m research.run_preregistered_holdout \
  configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json \
  --runs-path runs/experiments.jsonl \
  --db-path python/data/var/klines.sqlite3
```

**What this task actually ran, disclosed rather than glossed over**: this
agent operates inside an isolated git worktree with no `runs/` or
`python/data/var/` of its own (see "IMPORTANT — worktree isolation" in the
governing task brief) -- the real shared cache and log live only in the
main checkout. The command actually executed therefore pointed
`--runs-path`/`--db-path` at the main checkout's absolute paths
(`/mnt/c/Dev/trading-engine/runs/experiments.jsonl` and
`/mnt/c/Dev/trading-engine/python/data/var/klines.sqlite3`) while running
the worktree's own copy of the registration file and code -- behaviorally
identical to the canonical form above (both flags accept any path, absolute
or relative; `research.holdout.load_holdout_klines` neither knows nor cares
which checkout it was invoked from), but worth stating exactly rather than
presenting a tidied-up command that was not the one that actually produced
the real, logged result.

Ran exactly once, to completion, with no `--force-reclaim-reason` (this
script never supplies one on its own initiative -- see the module's own
docstring). Confirmed directly against the real log after the run:
**exactly one** `holdout_access` record exists for
`strategy_id="daily-tsmom-ensemble"` in the entire, real
`runs/experiments.jsonl`.

**`code_version` provenance, disclosed precisely.** The real logged
record's `code_version` field (`research.experiment_log._git_head_sha`,
a plain `git rev-parse HEAD`) reads `7ebb6ac30770653ec491b59a7aececaccc7697a7`
-- `sr-u`'s own merge commit, the branch point this task's worktree was
created from. That is **not** a commit containing this task's own runner
script: `python/research/run_preregistered_holdout.py` was uncommitted
working-tree code at the moment the real run executed (written and
tested, but not yet `git commit`-ed), so `git rev-parse HEAD` at that
instant could not and does not reflect it. This is a real, honest gap in
what the log record alone can reconstruct -- not fixed by re-running the
holdout (which would be a second, unauthorized access) but closed here by
recording, directly, the exact artifact that ran: the SHA-256 of
`run_preregistered_holdout.py`'s own bytes **as they existed at execution
time, before any post-review fix in this same PR touched the file**, begins
`8c28955e3fecfc24...` (truncated for the same commit-scanner reason as
every other digest in this document). `python/research/strategies/
daily_tsmom_ensemble.py` (the strategy itself) was never modified by this
task at all -- it is byte-for-byte the version committed at `7ebb6ac...`,
so `code_version` correctly identifies the code that mattered most (the
registered strategy's own signal/sizing/order-emission logic); only the
thin execution-glue script's own provenance needed this separate note.

## The real result

Real record: `run_id=8143a525-3159-447b-991d-2f11a0ef790b`,
`preregistration_id="daily-tsmom-ensemble-1d-holdout"`,
`preregistration_sha256` begins `23d6b378425a9e2e...` (truncated here
deliberately -- the full 64-hex-character digest is a legitimate SHA-256,
not a secret, but is elided from this git-tracked document to avoid an
unrelated local commit-scanner false positive that blocks on any
contiguous 64-hex-character string "by design"; the full value is
independently reproducible at any time via `sha256sum` against the
committed, unchanged
`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`,
and is recorded in full on the real log record itself, matching the file
on disk), `is_holdout_run=true`, `strategy_family="daily-tsmom"`,
`code_version=7ebb6ac30770653ec491b59a7aececaccc7697a7` (the `sr-u` merge
commit this worktree branched from). Full record archived below the
table (with the same field similarly truncated).

| Quantity | Registered requirement | Observed | Cleared? |
|---|---|---|---|
| PSR (daily-resampled, zero benchmark, measured moments) | >= 0.95 | **0.936674** | **No** |
| Observed annualized Sharpe vs. declared detection floor | > 0.9567 | **0.882054** | **No** |
| Total trades | >= 53 | **26** | **No** |
| Max drawdown | <= 0.20 | **0.119917** (~12.0%) | Yes |
| Profit factor | >= 1.3 | **2.866617** | Yes |

Secondary, reported per the registration's own `secondary_reported_not_gating`
list (context, not gating):

- **Total return**: +43.18% over the 1,079-bar (~2.95-year) window
  (`starting_equity=10000` -> `final_equity=14318.31...`).
- **Win rate**: 46.15% (12 of 26 closed trades).
- **Return skewness**: 0.5082, **raw kurtosis**: 14.966 (both measured from
  the real equity curve, not assumed -- fat-tailed and right-skewed, which
  is exactly why `psr_from_equity_curve` was run with measured moments
  rather than the normal-assumption fallback).
- **`num_returns`**: 1,078 (one fewer than the 1,079 bars, as expected --
  `per_bar_returns` needs a prior observation).
- **No fold-level breakdown** applies -- this is the single-window holdout
  confirmation variant; `fold_count: 1` in the log record reflects that
  literally (one window, not one fold of many).

## Outcome: INCONCLUSIVE

Per `evaluate_gating`'s mechanical determination (PASS requires all five
checks above to clear; FAIL requires a non-positive or undefined PSR; this
result is neither): **INCONCLUSIVE**, and independently by three separate
routes at once -- PSR is positive but below the 0.95 threshold, the
observed Sharpe does not exceed the window's own declared detection floor,
and the trade count falls short of the frequency-scaled floor. Any one of
these alone would already be enough to land here; this result hits all
three simultaneously.

The registration's own pre-committed `outcome_interpretation.INCONCLUSIVE`
text, quoted in full, verbatim, exactly as committed at `sr-u`:

> PSR positive but below 0.95, OR the observed Sharpe fails to exceed the
> window's own 0.9567 detection floor (reported 'not powered to confirm'
> per clause 3), OR total trades fall below the 53-trade floor (reported
> INCONCLUSIVE-DATA-LIMITED — neither a pass nor a fail, and not evidence
> against the strategy). Park the hypothesis; the only legitimate remedy is
> more calendar time or more data, explicitly NOT another search, another
> threshold, or another lookback set. Meta-consequence, pre-committed here
> rather than decided after seeing the result: an INCONCLUSIVE outcome on
> this attempt ends the BTC-only price-signal research program as a line of
> work — the next move is a named structural change (multi-symbol expansion
> with survivorship-safe data, or a genuinely different data source
> entirely), not another grid, on any timeframe, against any signal class.

**This is stated plainly, not softened**: per the registration's own
pre-committed meta-consequence, this INCONCLUSIVE result **ends the
BTC-only price-signal research program as a line of work.** The only
legitimate next move this registration names is a structural change
(multi-symbol expansion with survivorship-safe data, or a genuinely
different data source), not another grid, threshold, or lookback set, on
any timeframe. That decision-of-what-to-do-next is not this task's to make
-- it belongs to a human `Discuss`, and no next research task is proposed
here.

## The temptation, disclosed rather than acted on

Every one of the three failing numbers is close to its bar, not far below
it: PSR 0.9367 against a 0.95 threshold; observed Sharpe 0.8821 against a
0.9567 floor; 26 trades against a 53 floor (almost exactly half). Seeing
numbers this close creates a real, specific pull -- "what if one more year
of calendar time pushed the Sharpe over the floor," "what if the Option-B
sign-only trigger rule (disclosed as a real risk in `sr-u`) is what capped
the trade count at half the requirement, and a different trigger rule would
have cleared it." Both thoughts occurred while writing this report. Neither
was acted on: the registration's own `stopping_rule` and
`outcome_interpretation` were written specifically so that "the numbers are
close" would not be grounds for a second attempt, a threshold adjustment,
or a parameter change after seeing a real result -- and the task's own
instructions are explicit that noticing this pull and reporting it is the
correct response, not quietly resolving it by trying something else. Both
candidate remedies above ("more calendar time," "a different trigger rule")
are, not coincidentally, exactly the two things the registration's own
`INCONCLUSIVE` text and `sr-o`'s prior "genuinely new configuration, not
tuning-after-the-fact" framing would classify as separate, new, human-
gated research decisions -- not something this task did, or should do,
on its own.

## Known confound, restated rather than laundered

The registered window (2021-05-14 through 2024-04-26) spans the 2021
bull-market top, the 2022 LUNA/FTX-driven bear market, and the 2023
recovery -- an unusually trend-friendly stretch by BTC's own multi-year
history, exactly as `sr-u`'s registration disclosed in advance. Even on
this favorable-for-trend-following window, the strategy's real annualized
Sharpe (0.88) did not clear the window's own detection floor. That is worth
stating plainly: this is not a case of a real edge being obscured by an
unfavorable test window: the window was, if anything, more favorable to a
trend-following signal than a typical multi-year BTC stretch would be.

## Full real log record

Archived here verbatim (re-serialized for readability; the real record is
one line in `runs/experiments.jsonl`) so this document does not depend on
the log file remaining unchanged to be checkable:

```json
{
  "record_type": "backtest_run",
  "run_id": "8143a525-3159-447b-991d-2f11a0ef790b",
  "logged_at": "2026-07-30T05:18:09.117469+00:00",
  "strategy_id": "daily-tsmom-ensemble",
  "strategy_version": "v1",
  "strategy_family": "daily-tsmom",
  "is_holdout_run": true,
  "preregistration_id": "daily-tsmom-ensemble-1d-holdout",
  "preregistration_sha256": "23d6b378425a9e2e...(truncated -- see note above the table)",
  "code_version": "7ebb6ac30770653ec491b59a7aececaccc7697a7",
  "fee_bps": "5",
  "slippage_bps": "2",
  "total_candidates": 1,
  "data_range": {"start_ms": 1620950400000, "end_ms": 1714089600000, "num_bars": 1079},
  "walk_forward_config": {
    "train_bars": 1079, "validate_bars": 1079, "step_bars": 0, "fold_count": 1,
    "bars_per_day": 1,
    "note": "single-window holdout confirmation (Strategy Research Task V, research.run_preregistered_holdout) -- not a walk-forward run"
  },
  "aggregate_metrics": {
    "mean_sharpe": 0.882053986435707,
    "min_sharpe": 0.882053986435707,
    "worst_fold_max_drawdown": "0.1199166660258215147953222484",
    "mean_total_return": "0.431831051855892001613995287",
    "total_trades": 26,
    "mean_profit_factor": 2.8666166454829294,
    "return_skewness": 0.508188864252769,
    "return_kurtosis": 14.966394214912976,
    "num_returns": 1078,
    "declared_detection_floor_sharpe": 0.9567,
    "outcome_region": "INCONCLUSIVE",
    "psr": {
      "psr": 0.9366738652161312,
      "sharpe_ratio": 0.04616881575165129,
      "benchmark_sharpe": 0.0,
      "num_observations": 1078,
      "skewness": 0.508188864252769,
      "kurtosis": 14.966394214912976,
      "moments_source": "observed",
      "z_score": 1.5274374516384879,
      "sampling": "daily"
    },
    "gating_checks": {
      "psr": {"required": 0.95, "observed": 0.9366738652161312, "passed": false},
      "max_drawdown": {"required": "0.20", "observed": "0.1199166660258215147953222484", "passed": true},
      "min_total_trades": {"required": 53, "observed": 26, "passed": false},
      "profit_factor": {"required": "1.3", "observed": 2.8666166454829294, "passed": true},
      "sharpe_above_detection_floor": {"required": 0.9567, "observed": 0.882053986435707, "passed": false}
    }
  }
}
```

The strategy's own `fit()` call also logged its established, unrelated-to-
this-task diagnostic sub-record (`run_id=a96ad242-c9cd-4ea0-ab25-
fe5d6ee66a23`, `parent_run_id=8143a525-...`, `is_holdout_run=false`, no
`preregistration_id`) -- exactly the pre-existing, disclosed-in-`sr-u`
behavior every sibling `Trainable.fit()` in this package has, not a defect
in this task's own logging. It is not the record that matters for the
holdout single-access claim; the outer record above is.

## A real gap this task's own execution exposed, not fixed here

CodeRabbit's review of this PR raised the nested sub-record above as a
"Major" finding: it carries `is_holdout_run=false` and no
`preregistration_id`, even though the data `fit()` actually scored was the
real holdout window -- and, correctly, flagged that this could pollute
`research/overfitting_check.py`'s selection-trial (`N`) accounting if that
module ever counts it. Checked directly against the real, current
`overfitting_check.py`: **`is_holdout_run` is never referenced anywhere in
that module.** Neither this nested child record NOR this task's own outer
`is_holdout_run=true` standalone record (`parent_run_id=None`, which
`check_project_combination_count`'s own documented `SELECTION` classification
covers -- "a genuine `parent_run_id`... or is a multi-fold standalone
walk-forward run") is excluded from trial counting today. Both would be
swept into a future `check_project_combination_count`/DSR calculation for
the `daily-tsmom` family exactly as if they were genuine, searched-over
research trials.

**Verified this is new, not a regression**: a direct scan of the real,
complete `runs/experiments.jsonl` (1,841 `backtest_run` records) found
`is_holdout_run=true` on **exactly one** record -- this task's own -- so
this gap has never been exercised before and changes nothing about any
prior computed result (in particular, `sr-r`'s 117-trial/DSR-2.0e-05
close-out is completely unaffected either way).

**Not fixed in this PR, deliberately, for two reasons.** First, the
narrowest fix CodeRabbit's own suggested diff proposed (making
`daily_tsmom_ensemble.py`'s `fit()` propagate holdout metadata into its
nested log call) would not actually close the gap: this task's OWN outer
standalone record has the identical exposure and that diff does not touch
it, so `overfitting_check.py` would still double-count the same
zero-search evaluation as two trials instead of the true one. A complete
fix needs an `is_holdout_run` exclusion inside `overfitting_check.py`
itself (a module whose own docstring documents `check_combination_count`
as "kept byte-for-byte behaviourally unchanged... it has tests and
callers") -- a real, scoped, low-risk change (provably a no-op against
every record that existed before this task, since none of them carry
`is_holdout_run=true`), but a large enough one, touching a different and
more central module than this task's own runner, that it deserves its own
deliberate change and review rather than a same-PR patch bolted onto an
execution task already carrying a real data-derived result. Second,
fixing only `daily_tsmom_ensemble.py`'s logging now would not change
anything about the ALREADY-LOGGED, immutable real record this task
produced -- the fix (whatever form it eventually takes) only matters for a
future run, and this strategy's own registration stopping rule means there
will not be one. Flagged here, explicitly, as a genuine follow-up
candidate for a future task -- not silently patched, not silently ignored.

## Design ambiguity noted, not silently resolved

`sr-u`'s registration reconciled a tension between the task brief's prose
(drawdown/trade-count/profit-factor "reported, not gating") and CLAUDE.md's
actual holdout single-window variant text and `research/preregistration
.py`'s schema (both treat them as gating, inside `primary_criterion`) by
following the code and CLAUDE.md's literal Bar text. This task inherited
that reading and found one further gap while implementing `evaluate_gating`,
worth naming explicitly per CLAUDE.md's "state assumptions and ask rather
than silently pick" rule: the registration's own `outcome_interpretation`
text names `PASS` as requiring all five checks, and names three specific
`OR` conditions for `INCONCLUSIVE` (PSR-below-threshold, Sharpe-below-floor,
trades-below-floor) -- but does not literally name what happens if *only*
the max-drawdown or profit-factor checks fail while the other three clear.
`evaluate_gating` treats that case as `INCONCLUSIVE` too (since it is
neither the literal `PASS` condition nor the literal, specifically-`PSR`-
keyed `FAIL` condition), which is the conservative reading consistent with
the rest of the document's framing. **This did not end up mattering for the
real result**: both max drawdown (12.0% against a 20% ceiling) and profit
factor (2.87 against a 1.3 floor) cleared comfortably. Flagged here for the
record, not because it changed anything about this run's outcome.
