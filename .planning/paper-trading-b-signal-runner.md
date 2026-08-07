# Paper-trading bridge, Task B: daily production signal runner

Governing design: `.claude/plans/tender-finding-matsumoto.md` ("Paper-
trading bridge: daily-tsmom-ensemble signal → Java OMS (local run)"),
Task B. Independent of Task A (`SignalSource`/`FileSignalSource` in
`java/runtime`) and Tasks C/D/E — this task only had to produce a
correct, tested Python script that writes a signal file; nothing here
depends on the Java side existing yet.

Built: `python/live/generate_daily_signal.py` (+ `python/live/__init__.py`),
`python/tests/test_generate_daily_signal.py`, a `.gitignore` update, and
this doc. `research/strategies/daily_tsmom_ensemble.py` was read closely
but not modified.

## Package location: `python/live/`, not `python/research/`

Deliberate, and noted here per the task brief's own instruction. Every
existing module under `python/research/` exists to produce and log
*evidence about* a strategy — walk-forward runs, holdout confirmations,
eligibility checks, pre-registrations. Its whole discipline (holdout
enforcement, trial counting, DSR/PSR math) is built around the idea that
what happens there feeds a human research decision, not a live loop.
`generate_daily_signal.py` is the opposite: by the time it runs,
`daily-tsmom-ensemble` is not being evaluated, it is being *operated*
(under CLAUDE.md's "Paper Trading Policy Exception"). Putting production
code in `research/` would blur that boundary — a future reader scanning
`research/` for "things that might be research trials" would have to
specifically remember to exclude this file. A new top-level package
(`python/live/`) makes the boundary structural instead of a remembered
exception. See `python/live/__init__.py`'s own docstring for the same
reasoning restated at the point a reader would actually encounter it.

## Pipeline design

1. **Fetch** (`fetch_live_klines`): calls `data.backfill.sync_range`
   directly — the exact same idempotent, cache-first idiom
   `backfill.py`'s own CLI uses (`find_missing_ranges` → chunked
   `iter_klines_range` → `upsert_klines`) — against the shared
   `python/data/var/klines.sqlite3` store. Deliberately **not**
   `research.holdout.load_research_klines`/`load_holdout_klines`: those
   two structurally cannot serve today's data (`load_research_klines`
   unconditionally clamps to the holdout cutoff; `load_holdout_klines` is
   explicitly the untouched-validation-split door, single-access-claimed
   per `strategy_id`, and would either wrongly refuse a repeated daily
   call or wrongly consume the holdout claim). `fetch_live_klines` reads
   real, current market data through the same low-level fetch/cache path
   research code uses, but through neither of research's two gated front
   doors.
2. **Convert** (`_kline_row_to_kline`): a byte-for-byte-equivalent copy of
   `research/holdout.py`'s private `_kline_row_to_kline`, not an import of
   it. Per the task brief and this codebase's own established precedent
   (`research/holdout.py`'s own `_funding_row_to_rate` docstring names
   this as the precedent to follow): each module owns its small
   conversions rather than reaching into another module's `_`-prefixed
   internals.
3. **Run the strategy** (`generate_signal`): builds a fresh
   `DailyTsmomEnsembleTrainable` (unmodified) per invocation, calls
   `fit()` once, then replays the returned, freshly-bound `Strategy`
   bar-by-bar (`strategy([kline])` for each kline, oldest→newest) and
   keeps only the **last** call's return value. Confirmed by reading
   `DailyTsmomEnsembleStrategy.__call__` directly: it only ever reads
   `window[-1]`, so feeding it one kline at a time (rather than a growing
   window) is behaviorally identical to how `backtest.engine.run_backtest`
   would drive it, and matches the task brief's own description.
4. **Emit** (`write_signal_atomically`): `OrderIntent.model_dump_json()`
   written to a `.tmp` sibling, `flush()` + `os.fsync()`'d, then
   `os.replace()`'d onto the final path. `None` decisions never call this
   function at all — `main()` branches on `decision is None` before ever
   touching the signal path, so "no signal today" provably cannot produce
   a write of any kind, empty or otherwise.

### No cross-invocation strategy state

Every invocation constructs a brand-new `DailyTsmomEnsembleStrategy`
(inside `fit()`, and again as the fresh instance `fit()` returns) and
replays the full `fetch_bars`-bar trailing window from a cold, flat
start. There is no persisted strategy object between daily runs. This is
safe only because `DEFAULT_FETCH_BARS` (300) comfortably exceeds
`MIN_WARMUP_BARS` (253, below) — see the module docstring's "No
cross-invocation state" section for the full argument. `main()` logs a
warning (and is tested to) if `--fetch-bars` is ever set below the
warmup floor.

## Warmup bar count: verified by reading the strategy, not assumed

`DailyTsmomEnsembleStrategy.__init__` (`research/strategies/
daily_tsmom_ensemble.py`, unmodified): `self._warmup_bars =
max(lookbacks) + 1`. With the strategy's own unmodified
`DEFAULT_LOOKBACKS = (21, 63, 126, 252)`, that's `252 + 1 = 253` —
`MIN_WARMUP_BARS` in `generate_daily_signal.py`. Separately,
`RollingRealizedVolatility(period=20, ...)` (the vol-targeting
estimator) warms up after only 21 closes (`vol_period + 1`), well before
the ensemble's own 253-bar gate — confirmed by reading
`volatility_targeting.py`'s `update()` directly. So 253 is the true,
binding minimum regardless of the vol estimator. `DEFAULT_FETCH_BARS =
300` is fetched (not 253) purely as a safety margin, per the task
brief's own suggested number — not independently derived, since the
brief's number already has the right property (comfortably above 253
without being needlessly large).

## Judgment calls, with citations

- **`fee_bps = Decimal("5")`, `slippage_bps = Decimal("2")`** — the exact
  values `configs/research/preregistrations/daily-tsmom-ensemble-1d-
  holdout.json`'s `procedure` section registered, and the exact values
  both real holdout confirmations logged (`sr-v`'s record:
  `"fee_bps": "5", "slippage_bps": "2"` in
  `.planning/sr-v-preregistered-attempt-result.md`; `sr-ab` used the same
  registration/config machinery). Reused for consistency with the
  backtested/holdout-confirmed configuration — not independently chosen
  for production.
- **`strategy_version = "v1"`** — matches both `sr-v`'s and `sr-ab`'s
  logged `"strategy_version": "v1"` records, and
  `daily-tsmom-ensemble-1d-holdout.json`'s own `"strategy_version": "v1"`.
  `research/strategies/daily_tsmom_ensemble.py` is unchanged since either
  run, so this stays correct; a comment in `generate_daily_signal.py`
  flags it to keep in sync if that ever changes.
- **`parent_run_id` format**: `f"live-{date:%Y-%m-%d}-{uuid4()}"`,
  e.g. `live-2026-08-07-9c6b9616-62db-4879-bca9-1b35240fc2dd` (the real
  value from this task's own live invocation, below). Date-stamped for
  human readability when scanning `runs/live_signals.jsonl`, with a UUID
  suffix so a same-day re-run (manual retry, or a cron misfire) never
  collides. This value only ever appears as a `parent_run_id` field on a
  logged record in the isolated live log — it plays no role in any
  DSR/PSR/trial-counting math, which only ever reads
  `runs/experiments.jsonl`.
- **Cron line** (documented per the task brief, **not installed** — out
  of scope for this task):

  ```
  5 0 * * *  cd /path/to/trading-engine && PYTHONPATH=python python/.venv/bin/python -m live.generate_daily_signal
  ```

  Shortly after the UTC daily candle closes, matching the invocation
  convention `sr-v`/`sr-ab` already established for this project's other
  `python -m ...`-style scripts (run from the repository root with
  `PYTHONPATH=python`, not `cd`'d into `python/` — see "Real live
  invocation" below for why this matters concretely, not just by
  convention).

## The `.gitignore` change, and a discrepancy worth disclosing

The task brief and the governing plan both say `runs/live_signals.jsonl`
must be git-committed, "same treatment as `runs/experiments.jsonl`."
**Checked directly rather than assumed, and the premise doesn't fully
hold**: `runs/experiments.jsonl` is *not* actually git-tracked in this
repo. It's excluded by the pre-existing blanket `runs/` line in
`.gitignore` (confirmed: `git check-ignore -v runs/experiments.jsonl` →
matches `.gitignore:13:runs/`; the real file in the primary checkout is
a 3.7MB, git-untracked, purely local artifact —
`research/experiment_log.py`'s own `DEFAULT_RUNS_PATH` docstring already
says as much: "`.gitignore` already has a `runs/` entry"). So the
literal comparison ("same treatment as X") is inaccurate as a factual
claim about X's current git status — X isn't committed at all.

That doesn't change what this task does, though: the instruction itself
("must be git-committed (not gitignored)") is explicit and unambiguous,
independent of whether its stated analogy holds. `runs/live_signals.jsonl`
*should* be tracked on its own merits — it's a low-volume (once/day),
append-only operational audit trail for a real (paper) trading loop,
categorically different from the research trial log's role (which
accumulates one record per backtest/walk-forward candidate across
100+ ad hoc research trials, and was never meant to be a reviewable git
artifact). Followed literally, flagged here rather than silently
reconciled by weakening the instruction to match the inaccurate
citation.

Mechanically: `runs/` (bare, trailing-slash directory pattern) cannot be
partially un-ignored — git does not descend into an excluded directory to
evaluate per-file negations inside it (documented gitignore behavior).
Changed to:

```gitignore
runs/*
!runs/live_signals.jsonl
```

`runs/*` excludes directory *contents* one level deep (not the directory
entry itself), so git still looks inside `runs/` and the negation takes
effect. Verified directly, not just reasoned about — `git status --short
--ignored -uall runs/` with both a real `runs/experiments.jsonl` and a
real `runs/live_signals.jsonl` present shows:

```
?? runs/live_signals.jsonl
!! runs/experiments.jsonl
```

(`??` untracked-but-trackable, `!!` ignored) — exactly the intended
split. `var/live/` was added as a new gitignore line, mirroring
`python/data/var/`'s existing one-line treatment (verified the same way:
`var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json` shows `!!`
under the same check).

## `runs_path` isolation: the evidence, not just the claim

Three layers, from unit tests up to the real live run:

1. **`test_generate_signal_never_touches_the_default_research_runs_path`**
   (`python/tests/test_generate_daily_signal.py`) — `monkeypatch.chdir`
   into a `tmp_path`, call `generate_signal(..., runs_path=LIVE_RUNS_PATH)`,
   then assert `tmp_path / "runs/live_signals.jsonl"` **exists** and
   `tmp_path / "runs/experiments.jsonl"` (i.e.
   `research.experiment_log.DEFAULT_RUNS_PATH`) **does not exist at
   all** — not merely "doesn't contain this record," the file is never
   even created. Also asserts the isolated log's one record has the
   right `strategy_id`/`strategy_version`/`fee_bps`/`slippage_bps`.
2. **`test_generate_signal_default_runs_path_is_the_isolated_live_log`**
   — same assertions, but calling `generate_signal` *without* passing
   `runs_path` at all, proving the isolation holds even if a future
   caller forgets to override the parameter (because `generate_signal`'s
   own default is `LIVE_RUNS_PATH`, never
   `experiment_log.DEFAULT_RUNS_PATH`).
3. **`test_main_writes_a_real_signal_file_end_to_end`** — full `main()`
   invocation against a fake BingX server, asserting exactly one record
   lands in the caller-supplied `--runs-path`.

And the real, non-test evidence — this task's actual live invocation
(below) — confirms the same thing against the genuine
`DailyTsmomEnsembleTrainable`/`experiment_log.log_run` code path, not a
mock: `runs/live_signals.jsonl` in this worktree now contains exactly
one real record (`strategy_id: "daily-tsmom-ensemble"`,
`is_holdout_run: false`), and `runs/experiments.jsonl` was never created
in this worktree at all (this worktree started with no `runs/`
directory) — the strongest available proof, since a real
`research.experiment_log.DEFAULT_RUNS_PATH`-directed write would have
had to create that file from scratch for it to appear, and it didn't.

## Real live invocation

Run from the worktree root (repo-root-relative default paths, matching
`sr-v`/`sr-ab`'s own established invocation convention — `cd`-ing into
`python/` first breaks those same relative defaults, the exact
class of bug `sr-v`/`sr-ab` each hit and documented):

```
PYTHONPATH=python BINGX_BASE_URL=https://open-api.bingx.com python/.venv/bin/python -m live.generate_daily_signal
```

Real, unedited output:

```
2026-08-07 18:50:24,880 INFO fetching missing range [1760140800000, 1786060800000) for BTC-USDT/1d
2026-08-07 18:50:25,141 INFO range [1760140800000, 1786060800000): fetched 300 rows (total newly inserted so far: 300)
2026-08-07 18:50:25,155 INFO fetched 300 klines for BTC-USDT/1d (most recent open_time=2026-08-06T00:00:00+00:00)
2026-08-07 18:50:25,432 INFO wrote signal: side=LONG quantity=0.06205492332296113800946681418 symbol=BTC-USDT intent_id=9111ddd3-193c-4c88-ad91-f766f1617feb -> var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json
```

300 real daily BTC-USDT klines fetched from the live BingX production
endpoint (public, unauthenticated, no credentials — matching this
project's existing precedent for real verification), most recent closed
bar `2026-08-06T00:00:00Z` (today is 2026-08-07 per this environment's
clock, so "now" floored to the `1d` grid correctly excludes today's
still-forming bar). The ensemble produced a real, non-`None` decision on
this run: **LONG, quantity ≈0.0621 BTC**. Written signal file
(`var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json`, gitignored,
not committed):

```json
{"intent_id":"9111ddd3-193c-4c88-ad91-f766f1617feb","symbol":"BTC-USDT","side":"LONG","order_type":"GUARDED_MARKET","quantity":"0.06205492332296113800946681418","limit_price":null,"signal_timeframe":"1d","created_at":"2026-08-06T00:00:00Z","schema_version":"1.0"}
```

Corresponding real log record (`runs/live_signals.jsonl`, git-committed
as part of this PR): `run_id=f7dde3a6-a6f3-4deb-a9d7-8b790130bb7f`,
`parent_run_id=live-2026-08-07-9c6b9616-62db-4879-bca9-1b35240fc2dd`,
`strategy_id=daily-tsmom-ensemble`, `strategy_version=v1`,
`fee_bps="5"`, `slippage_bps="2"`, `is_holdout_run=false`,
`data_range={"start_ms": 1760140800000, "end_ms": 1785974400000,
"num_bars": 300}`. This is `fit()`'s own in-sample diagnostic scoring
pass over the 300-bar fetch window — not a claim about the strategy's
real edge (that question is already closed for this strategy via
`sr-v`/`sr-ab`/`sr-ac`, per CLAUDE.md's "Paper Trading Policy
Exception"); it exists purely so every `fit()` call leaves a trace, per
this project's own established logging convention.

**This live run does not touch any holdout or pre-registration data.**
`data.backfill.sync_range`/`data.bingx_klines.iter_klines_range` were
called directly, never `research.holdout`'s loaders — and the fetched
window (2025-10-11 through 2026-08-06) postdates every existing
holdout/research split in this project by construction (it's today's
real market data).

Not re-run a second time for this doc: `research.holdout`'s
single-access discipline is specific to *holdout* data and doesn't apply
here, but re-running gratuitously would still create a second, redundant
log record for no reason — one real, disclosed invocation is what the
task asked for and what's shown above.

## Test coverage

`python/tests/test_generate_daily_signal.py`, 16 tests, all against the
project's existing `FakeBingXKlinesServer` (stdlib `http.server`, the
same fixture `test_backfill.py` uses — no mocking framework, matching
this codebase's established Python test philosophy) or `tmp_path`:

- `_kline_row_to_kline` field/timestamp conversion.
- `fetch_live_klines`: ascending order via the shared cache; excludes
  today's still-forming bar; cache-first on a rerun (no redundant network
  calls).
- `generate_signal`: `None` on a flat (no sign-change) series; a real
  `OrderIntent` on a genuine sign change; the two `runs_path` isolation
  tests described above.
- `write_signal_atomically`: creates parent dirs and writes valid JSON;
  fully replaces stale existing content; leaves no leftover `.tmp` file;
  uses `os.replace()` for the final move (spied, not just inferred).
- `main()`: full end-to-end signal write against a fake server; a
  pre-existing signal file is left byte-for-byte untouched when no
  signal fires; exits with an error when `BINGX_BASE_URL` is missing;
  warns when `--fetch-bars` is set below the warmup floor.

Full suite (`cd python && uv run pytest -q`): **1407 passed** (1391
pre-existing + 16 new), 0 failures, run both before this task's changes
(baseline) and after.

## Explicitly not done here (per task scope)

- No Java code (`SignalSource`, `FileSignalSource`, scheduler) — Task A/C,
  separate/parallel tasks.
- `daily_tsmom_ensemble.py` itself untouched — read closely, no bug found
  worth flagging.
- No crontab installed — the line above is guidance only.
- `configs/research/holdout*.json` and every pre-registration file
  untouched; nothing in this script's code path can reach holdout data.

## Merge

Not merged by this task, per the governing plan's explicit "Merge
authority" section and this task's own brief: this is operational code
driving a real (paper) trading loop, treated the same as R3-risk code for
merge purposes. PR opened for human review, not auto-merged even once
CI/CodeRabbit are green.
