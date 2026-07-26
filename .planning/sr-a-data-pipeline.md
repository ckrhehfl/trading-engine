# Strategy Research, Task A: the historical BingX kline data pipeline

## Scope note

First of four sequenced build tasks implementing CLAUDE.md's "Strategy
Research Operational Design (2026-07-25)" section — see that section for
the full design this task follows. Task A is independent of Tasks
B/C/D (`KlineWindow`/`python/metrics/`, `python/research/`, the
placeholder `TrainableStrategy`) and was built and verified against the
live BingX endpoint before any of them, per the design's "Build
sequencing" note. This doc covers `python/data/` only.

## What was built

Four new modules under `python/data/` (new package), plus a shared test
helper:

- **`_grid.py`** — interval-to-milliseconds mapping (`15m` only, wired
  as a dict so `5m`/`1h` are a one-line addition later) and
  `require_valid_range`/`require_aligned`, shared by all three other
  modules so "fail loud on misaligned input" is enforced identically
  everywhere rather than reimplemented three times.
- **`bingx_klines.py`** — the BingX client. `fetch_klines_page` (one
  request) and `iter_klines_range` (paginates a large range in
  `<= limit`-candle chunks, cursor always derived from actual returned
  data). Stdlib `urllib` only, `json.loads(..., parse_float=Decimal)`,
  retry-with-backoff on 429/5xx, a fixed 0.25s delay between page
  requests.
- **`store.py`** — SQLite cache. `connect`, `upsert_klines` (`INSERT OR
  IGNORE`), `find_missing_ranges` (single pass over stored rows, diffed
  against the expected arithmetic sequence).
- **`backfill.py`** — CLI + `sync_range(symbol, interval, start_ms,
  end_ms, *, conn, base_url)`.
- **`python/tests/fake_bingx_server.py`** — stdlib `http.server`-based
  fake, ported in *philosophy* (not code) from
  `java/runtime/src/test/java/engine/runtime/FakeBingXTradesServer.java`.
  Replicates BingX's real newest-first-capping behavior so pagination
  bugs are caught the same way a wrong assumption about the Java
  `/v2/quote/trades` ordering would have been.

56 new tests across `test_bingx_klines.py` (27), `test_store.py` (18),
`test_backfill.py` (11), all written before their corresponding
production code (see "TDD" below).

## Key empirical findings (verified against the live, public,
unauthenticated endpoint 2026-07-25/26 — none of this was assumed from
docs)

CLAUDE.md's "Exchange API Facts" already marked `GET
/openApi/swap/v3/quote/klines` as verified for symbol/interval/pagination
basics, but not field shape or ordering. Called directly
(`curl https://open-api.bingx.com/openApi/swap/v3/quote/klines?...`)
before writing any parsing code, same discipline
`.planning/08b-trading-loop.md` used for `/v2/quote/trades`:

- **Response shape**: `{"code":0,"msg":"","data":[{"open":"...",
  "high":"...","low":"...","close":"...","volume":"...","time":<int
  ms>}]}`. OHLCV fields are quoted strings already; `time` is a bare
  int. `parse_float=Decimal` is still used per the task brief, as a
  defensive measure — it doesn't currently change behavior for OHLCV
  fields (they're never bare numbers in practice) but would matter the
  moment BingX ever sent one.
- **Ordering is newest-first (descending `time`) within a page** —
  confirmed for both an unbounded request and one with explicit
  `startTime`/`endTime`. This is the **opposite** of `/v2/quote/trades`,
  which `BingXPriceFeedTest.java` already established is oldest-first.
  `iter_klines_range` never assumes either order — its cursor is always
  `max(row.open_time_ms) + step`, not array position.
- **The critical one**: a request whose `[startTime, endTime)` span
  covers more candles than `limit` is silently capped to the `limit`
  candles closest to `endTime` — the *newest* ones, not the oldest.
  Confirmed with a live call: a 1500-candle-wide range with
  `limit=1000` returned exactly 1000 rows, spanning
  `[endTime - 1000*step, endTime)` — the oldest ~500 candles in the
  requested range were silently dropped. This is a stronger and more
  specific claim than CLAUDE.md's existing "`limit` isn't a reliable
  count guarantee" note, and it directly shaped `fetch_klines_page`'s
  design: it does **not** try to guard against or guess at a too-wide
  span (an earlier draft did, and it was wrong to — see "Judgment
  calls" below). It just sends the request and returns whatever comes
  back. Safety against ever triggering this comes entirely from
  `iter_klines_range` always sizing each request to `<= limit` candles
  before sending it, `limit` itself capped at 1000 (BingX's documented
  true maximum) — never from trying to detect capping after the fact.

## Real BingX historical retention depth (the actual point of the
"Verify" step — this is the number Task C's walk-forward harness needs)

Ran `backfill.py` for real against the live production endpoint,
`BTC-USDT`/`15m`, `--start 2020-01-01T00:00:00+00:00` through "now"
(no `--end`, so it defaulted to now floored to the grid). Wall-clock
time: **95 seconds** (00:52:34 → 00:54:09 UTC). One `find_missing_ranges`
gap covering the whole requested span (empty store), walked in ~231
chunks — the empty pre-retention stretch (2020-01-01 through
2025-11-15, ~207 chunks of nothing) plus ~24 chunks of real data. No
429s or 5xx hit during this run; the fixed 0.25s inter-page delay
was sufficient.

**Result** (`SELECT COUNT(*), MIN(open_time_ms), MAX(open_time_ms) FROM
klines`):

- **Earliest available bar: `2025-11-15 14:00:00 UTC`**
  (`open_time_ms = 1763215200000`)
- **Latest bar this run: `2026-07-25 15:30:00 UTC`**
  (`open_time_ms = 1784993400000` — "now" at run time)
- **Total: 24,199 bars**
- **Zero internal gaps** — verified by diffing consecutive
  `open_time_ms` against the 900,000ms step across the whole stored
  range; every single expected grid slot in
  `[earliest, latest]` is present. `find_missing_ranges` independently
  agrees (returns `[]` when re-queried over the same span).
- **Depth: ~252 days (~8.3 months)**, not years. BTC-USDT itself has
  presumably traded on BingX since well before 2020, but this REST
  kline endpoint's usable history for backtesting purposes is far
  shorter than the contract's actual listing history — most likely a
  rolling retention window server-side, not a fixed archive. That
  means **these exact numbers will drift on every future run**: re-
  running this same backfill next month will show a later earliest
  bar (old data ages out) and a larger total count (new data
  accumulates) — this section records the 2026-07-25/26 snapshot, not
  a permanent constant. A future session relying on "how much history
  do we actually have" should re-run `backfill.py` rather than trust
  this number as still current.

**Direct implication for Task C** (flagged here since CLAUDE.md's
walk-forward design explicitly says to revisit its window sizing once
this number was known): the provisional windows (train=8,640 bars
[~90d], validate=2,880 bars [~30d], step=2,880) need at least
`8,640 + 8*2,880 = 31,680` bars to hit the eligibility bar's "minimum
8-10 folds" with a single train window shared across folds, or more if
each fold gets its own re-trained window. **24,199 bars supports at
most ~5 non-overlapping validate folds** after one 8,640-bar train
window (`(24199 - 8640) // 2880 = 5`, with 1,159 bars left over unused).
This is short of the credibility floor as specified. Task C will need
to make an explicit, documented call here (per the design's own
"shrink windows for more folds or accept fewer folds and weight the
eligibility bar more conservatively" — this doc doesn't make that call,
it just hands Task C the real number so it isn't guessing).

## Judgment calls resolved without asking

- **`fetch_klines_page` doesn't pre-emptively reject a too-wide
  `[start,end)` span.** An earlier draft raised `ValueError` if
  `(end_ms - start_ms) // step > limit`, reasoning that BingX's
  newest-end capping made a too-wide request actively misleading.
  Dropped once the real capping behavior was confirmed empirically
  (see above): rejecting it would make it impossible to write a direct,
  faithful test of BingX's actual behavior (see
  `test_fetch_klines_page_silently_caps_to_newest_rows_when_range_spans_more_than_limit`
  in `test_bingx_klines.py`), and CLAUDE.md's own instruction is to
  verify the *actual returned count* rather than guess in advance —
  guessing-and-rejecting is the same anti-pattern as guessing-and-
  trusting, just inverted. The function now does the minimum: validate
  its own contract (alignment, `start<end`, `limit` in `1..1000`), send
  exactly what was asked, and return exactly what came back, however
  much that is. All completeness/correctness guarantees live in
  `iter_klines_range` instead.
- **`sync_range` batches its upserts instead of buffering a whole gap
  in memory** — found and fixed *during* this task's own real
  verification run, not anticipated in the original design. First
  implementation did `rows = list(iter_klines_range(...))` then one
  `upsert_klines` call per gap. For a store that starts empty,
  `find_missing_ranges` returns exactly one gap covering the *entire*
  requested range — meaning the real 2020-2026 sentinel backfill would
  have bought zero interrupt-safety in practice: a crash 90 seconds
  into a multi-minute run would have lost all 24,199 rows, not just
  the unfetched tail, directly contradicting the "safe to interrupt/
  rerun" requirement. Caught by noticing the first live run's log
  output stayed silent for the entire ~95s (only two log lines total,
  none in between) and realizing why. Fixed before the real run was
  redone: rows are now upserted every `_UPSERT_BATCH_SIZE` (1000,
  matching one page) rather than once per gap.
  `test_sync_range_persists_each_batch_before_fetching_the_next` proves
  rows are visible in the connection immediately after each batch, not
  only after `sync_range` returns.
- **`base_url` is always a required, caller-supplied argument, never a
  literal anywhere in `python/`.** Matches `BingXAdapter`/
  `BingXPriceFeed`'s existing "caller decides the host" pattern in
  `java/`, and is required by `.github/workflows/bingx-hostname-
  guard.yml`, which greps `java/` and `python/` for the literal
  production hostname string. `backfill.py`'s CLI reads it from
  `--base-url` or `$BINGX_BASE_URL`, defaulting to neither — a missing
  base URL is a loud `SystemExit`, not a silent fallback to some
  hardcoded default (there isn't one to fall back to).
- **`sync_range`'s signature**: the task brief named
  `sync_range(symbol, interval, start_ms, end_ms)` literally, but a
  working implementation obviously also needs a DB connection and a
  base URL. Resolved as
  `sync_range(symbol, interval, start_ms, end_ms, *, conn, base_url)` —
  the four named positional args stay exactly as specified, with the
  two infrastructure dependencies as required keyword-only args, so the
  call site still reads like the brief's signature at a glance.
- **CLI default DB path**: `python/data/var/klines.sqlite3`, with
  `python/data/var/` added to `.gitignore`. `store.connect` creates the
  parent directory if missing (a fresh clone's first backfill run
  shouldn't fail on a missing directory before ever reaching BingX).
  Not specified in the design doc; chosen to keep the runtime cache
  file physically separate from source inside the same package rather
  than inventing a repo-root-level `data/` directory that would be
  confusable with the `python/data/` package itself.
- **`main()`'s "now" default for `--end` is the one place this pipeline
  rounds instead of failing loud.** Every explicit, user-supplied
  `start_ms`/`end_ms` (whether via the CLI or called as a library) is
  validated by `_grid.require_valid_range` and rejected if misaligned —
  per the task's explicit "fail loud on bad input" instruction. But
  `datetime.now()` is never grid-aligned by construction, and there's
  no sensible "misaligned now" error to raise on our own computed
  default — so `main()` floors it to the grid instead. This is a
  narrow, deliberate exception scoped to exactly one line, not a
  precedent for relaxing the rule elsewhere.

## Concurrent work note

This task's branch was created from a `main` checkout that had
uncommitted, in-progress changes belonging to Task B
(`python/backtest/kline_window.py`, `python/metrics/`, related test
files) sitting in the working tree — evidence of a concurrent Task B
agent running in the same shared filesystem, per the design's "Task B
... can run in parallel" sequencing note. Those files were never
touched, read for content, or staged by this task; only files under
`python/data/`, the three new `python/tests/test_*.py` files, the new
`python/tests/fake_bingx_server.py` helper, and the `.gitignore`
addition were ever `git add`ed here (never `git add -A`/`.`). By the
time this task's work was verified, that concurrent WIP had already
disappeared from the working tree (either committed elsewhere or
reset) — confirmed via `git status`/`git diff` showing a clean tree
for every file this task doesn't own.

## TDD

Tests were written first for every module (56 tests total), following
red-green-refactor: each test file was written against the not-yet-
existing module (collection/import failures = red), then the minimum
implementation was added to make it pass. `fetch_klines_page`'s
no-upfront-span-guard design (see "Judgment calls" above) is a genuine
example of a design that changed *because* a test written first
(the silent-capping test) made the wrong design's test un-writable in
a faithful way — the test came before the fix, not after.

## CodeRabbit review findings

One actionable finding on the first review pass (the pass itself was
delayed by CodeRabbit's adaptive rate limit — see CLAUDE.md's "Rate
limits" section; the exact ETA it posted was waited out, then
`@coderabbitai review` was used to trigger the actual review, matching
the documented procedure rather than polling blindly).

- **`_get_with_retry` didn't catch a read-time `OSError`.** A
  connection can be reset (or otherwise fail) *after* `urlopen()`
  already succeeded in establishing the response — e.g. mid-body-read —
  which surfaces as a raw `OSError` (`ConnectionResetError` etc.), not
  `urllib.error.URLError`. `URLError`/`HTTPError` are themselves
  `OSError` subclasses (confirmed via their `__mro__` before writing
  the fix, not assumed), so this needed its own `except OSError` clause
  positioned *after* the two more specific ones — Python tries `except`
  clauses top-to-bottom, so the already-handled `HTTPError`/`URLError`
  cases never reach the new clause; only a genuine read-time failure
  does. Real gap: this function is the one making every one of the
  ~231 real network requests the live verification run issued, and a
  single dropped connection mid-read would previously have propagated
  straight out of `fetch_klines_page` uncaught, aborting the entire
  backfill instead of retrying like every other transient failure this
  function already handles. Fixed, plus a new test
  (`test_fetch_klines_page_retries_on_a_read_time_os_error_then_succeeds`)
  that makes the first `urlopen()` call return a fake response whose
  `.read()` raises `ConnectionResetError`, with the second call going
  through to the real fake server — proving the retry loop issues a
  genuinely fresh request rather than just not crashing. 110 tests
  total (was 109), all green.

## Deliberately out of scope

- **`5m`/`1h` intervals.** `_grid.py`'s `INTERVAL_MS` only has `15m`
  wired up — CLAUDE.md's Current Scope names `5m`/`1h` too, but nothing
  in this task's brief asked for them and no caller needs them yet.
  Adding a second entry to the dict is the entire cost when they're
  actually needed.
- **Any strategy-research code.** This task is pure data plumbing —
  fetch, cache, resync. No feature engineering, no lookahead-safety
  logic (that's `KlineWindow`, Task B), no walk-forward/holdout
  mechanics (Task C). CLAUDE.md's Strategy Research Methodology section
  explicitly doesn't gate infrastructure-only work like this.
- **Retry/backoff tuning based on real rate-limit behavior.** The real
  verification run never hit a 429, so the 5-retry/exponential-backoff/
  0.25s-inter-page-delay parameters remain exactly the conservative,
  untuned defaults the design called for — not validated against an
  actual rate limit, because none was encountered.
- **Multi-symbol support.** `store.py`'s schema already has `symbol` in
  its primary key (proven by
  `test_find_missing_ranges_is_scoped_to_symbol_and_interval`/
  `test_upsert_klines_scopes_rows_to_the_given_symbol`), but nothing
  in this task exercises more than one real symbol end-to-end, and
  CLAUDE.md's survivorship-bias note (Strategy Research Methodology)
  explicitly flags multi-symbol expansion as a separate concern to
  revisit later, not something this task needed to solve.
