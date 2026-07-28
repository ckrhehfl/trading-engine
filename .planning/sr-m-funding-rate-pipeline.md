# Strategy Research Task M: funding-rate data pipeline + funding P&L modeling

## Scope note

Infrastructure task, not a strategy task — mirrors how Task A (the
historical kline data pipeline) preceded Task D (the first real
strategy). This task builds the two pieces CLAUDE.md's Strategy Research
section had repeatedly flagged as a disclosed gap ("perpetual funding-rate
P&L is not modeled anywhere in this pipeline"): a BingX funding-rate data
pipeline (fetch/store/backfill, mirroring `python/data/bingx_klines.py`,
`store.py`, `backfill.py`) and additive/opt-in funding P&L attribution in
`python/metrics/`. Building the actual funding-rate-based strategy
candidate on top of this is a separate, deliberately deferred follow-up
task — see "Deliberately out of scope" below.

**Session continuity note**: this task's real, working implementation was
mostly built by a prior agent session that was interrupted by the user
mid-task (not due to any problem with the work) before it committed
anything, wrote this planning doc, or ran a real backfill/verification
pass. This document is written by the session that picked the work back
up — see "What was already done vs. what this session completed" below
for the exact split.

## What was already done vs. what this session completed

**Already done, found on disk as uncommitted changes on
`feat/strategy-funding-rate-pipeline` when this session started** (all
code, all tests, `cd python && uv run pytest` was already green — 639
tests passed on first run):

- `python/data/bingx_funding.py` — the BingX funding-rate client
  (`fetch_funding_page`, `iter_funding_range`, `FundingRow`), stdlib
  `urllib` only, mirroring `bingx_klines.py`'s structure. Already
  included, from real investigation: the `data: null` empty-result
  convention (vs. klines' `[]`), the flaky-null-near-retention-edge
  finding with a dedicated `_MAX_NULL_RETRIES` mitigation, the
  `limit > 1000` hard-error-not-silent-clamp finding, and the decision to
  **not** enforce grid alignment on `start_ms`/`end_ms` (unlike klines)
  because real historical `fundingTime` values aren't always aligned to
  the modern 8h grid.
- `python/data/store.py` additions — `funding_rates` table (`PRIMARY KEY
  (symbol, funding_time_ms)`, `TEXT`-stored `Decimal` fields, same
  discipline as `klines`), `upsert_funding_rates`,
  `find_missing_funding_ranges`, `fetch_funding_rates` — structurally
  identical to the klines equivalents, both tables sharing one
  `connect()`/one cache file.
- `python/data/backfill_funding.py` — resumable CLI (`sync_funding_range`
  + `main()`), idempotent via `find_missing_funding_ranges` +
  `INSERT OR IGNORE`, same batched-write-per-page resumability guarantee
  as `backfill.py`.
- `python/metrics/funding.py` — `FundingRate` (datetime-keyed internal
  type, mirrors `Kline`'s relationship to `KlineRow`).
- `python/metrics/position.py` — `PositionTracker(funding_rates=...)`
  (opt-in, default `None`/`()` behaves byte-for-byte as before),
  `apply_funding_through(cutoff_time)` (cursor-based, safe to call
  redundantly), `ClosedTrade.funding_pnl` (broken out, already included
  in `realized_pnl`), and the entering-vs-exiting-at-exact-funding-
  timestamp judgment call (see "Funding P&L sign-convention verification"
  below).
- `python/metrics/metrics.py` — `build_equity_curve`/`compute_metrics`
  gained an opt-in `funding_rates` parameter, threaded through to a fresh
  `PositionTracker`, with `apply_funding_through` called once per bar
  (not only once per fill) so the equity curve reflects a funding payment
  starting at the exact bar it settles on.
- Full test coverage for all of the above: `test_bingx_funding.py` (new),
  `test_backfill_funding.py` (new), `fake_bingx_funding_server.py` (new
  test helper), plus additions to `test_store.py`, `test_position.py`
  (including the explicit sign-convention tests the task brief called
  out as an easy trap — long-pays-on-positive, long-receives-on-negative,
  short-receives-on-positive, short-pays-on-negative, the entering-vs-
  exiting-at-T boundary, scaling-position notional correctness, flat-
  period skip correctness), and `test_metrics.py`.

**What this session actually did, on top of the above** (the prior
session's work was reviewed line-by-line — diffs, full new-file reads,
and a real `pytest` run — before touching anything, per this task's
explicit instruction not to redo or discard it):

1. Re-verified `cd python && uv run pytest` — 639 passed, 0 failed, 0
   errors, confirming the inherited work was genuinely complete and
   correct, not merely present.
2. Ran a **real, resumable `backfill_funding.py` run against the live
   BingX production endpoint** (the prior session's DB already had
   funding data from an earlier real run of its own — this session's run
   found and closed most of the remaining gaps in it, then confirmed the
   rest as genuinely unavailable via repeated reruns — see "Real BingX
   funding-rate historical depth" below).
3. Independently re-probed the flaky-null behavior directly against the
   live endpoint with `curl` (not through this project's code), including
   a new finding not in the prior session's investigation: the same
   boundary range got *more* flaky over the ~18 hours between the two
   sessions' probing, consistent with a rolling retention window (see
   below).
4. Wrote and ran a **real end-to-end demonstration**: loaded real
   pre-holdout 1h research klines, ran the actual `HourlyMomentumStrategy`
   (Strategy Research Task F) through the actual `run_backtest`, fed the
   real fills through `reconstruct_trades` twice (with and without the
   real funding series loaded from the now-backfilled cache), and found
   a real trade held across a real funding settlement — see "Funding P&L
   sign-convention verification" below for the exact numbers.
5. Updated CLAUDE.md: added a "Funding rate" bullet to "Exchange API
   Facts — BingX"'s Verified section, and updated the "Strategy Attempts
   So Far" section's stale "funding P&L is not modeled anywhere" /
   "queued next, not yet started" language to reflect that the
   infrastructure now exists (the strategy itself is still queued).
6. Wrote this document.

## Real BingX funding-rate historical depth (the point of the "Verify" step)

Ran `backfill_funding.py` for real against `https://open-api.bingx.com`,
`BTC-USDT`, starting from `2020-11-01T00:00:00Z` (a sentinel date chosen
to be before the pre-existing cache's earliest row, to double-check that
boundary rather than just trust it) through "now" (default, floored to
the funding grid). Re-ran three additional times to let the resumability
mechanism (`find_missing_funding_ranges` + idempotent re-fetch) converge
on the flaky-null gaps a single run's fixed retry budget doesn't
guarantee closing.

**Result** (`SELECT COUNT(*), MIN(funding_time_ms), MAX(funding_time_ms)
FROM funding_rates`, 2026-07-28):

- **6,199 rows**, `2020-11-29T12:00:00Z` through `2026-07-27T16:00:00Z`
  (2,066 days / ~5.7 years span) — an order of magnitude deeper than the
  task brief's own pre-verification estimate ("roughly 11 months," which
  the brief itself flagged as needing rigorous re-verification rather
  than being trusted). Funding-rate history on BingX is materially
  deeper than kline history at any granularity this project has measured
  (`1h`: ~820 days; `15m`: ~252 days; `5m`: ~90 days) — a real,
  independently-confirmed BingX-side pattern, not a fluke: funding rows
  are ~140 bytes and settle 3x/day vs. klines' 96 or 288x/day, so
  retaining years of funding history costs relatively little.
- **3 gaps remain**, all clustered right at the earliest boundary, each
  confirmed empty across 3+ separate backfill reruns (15+ null-retries
  each, well past the "10-15 consecutive nulls = genuinely gone"
  threshold `bingx_funding.py`'s own docstring already established for
  the out-of-retention case):
  - `2020-11-01T00:00:00Z` – `2020-11-29T12:00:00Z` (pre-boundary, no
    data at all — expected, this is what confirms 2020-11-29T12:00:00Z
    as the real earliest point, not an artifact of where the sentinel
    start happened to land)
  - `2020-12-01T00:00:00Z` – `2020-12-01T04:00:00Z` (a single missing
    settlement, 4h wide)
  - `2020-12-23T04:00:00Z` – `2020-12-23T12:00:00Z` (a single missing
    settlement, 8h wide)
  - `2021-01-05T12:00:00Z` – `2021-01-05T16:00:00Z` (a single missing
    settlement, 4h wide)
  These read as genuinely missing/unarchived individual settlements near
  BingX's own retention edge, not a pipeline bug — the exact same
  `sync_funding_range` call that left them missing successfully closed
  every other gap in the same run (see next finding), including a
  51-day gap (`2022-09-27` – `2022-11-17`) and a 70-day gap (`2025-06-24`
  – `2025-09-02`) that were present in the prior session's original
  backfill and closed cleanly on this session's first rerun — those
  were transient flaky-null misses, not real data holes, which is
  exactly the resumability design working as intended.
- **New finding this session, not in the prior session's investigation**:
  re-probed the earliest-boundary range directly with `curl` (bypassing
  this project's retry logic entirely) and found the flakiness had
  gotten *worse*, not stable, over the ~18 hours between the two
  sessions' work — 15/15 consecutive `null` responses for a range this
  project's own local cache already had real, previously-fetched rows
  for (proof the range genuinely has data — it's already stored). A
  clearly-recent, clearly-in-retention range (`2024-01-01`) returned
  real data reliably (3/3) in the same probing session. This is
  consistent with genuinely **rolling** retention (the window's edge
  moving forward continuously, not just being flaky at a fixed boundary)
  — the same caveat CLAUDE.md's kline retention section already carries
  ("expect these numbers to keep drifting forward on every future run")
  now has direct evidence for funding rate too, gathered independently
  rather than assumed from the klines precedent.

## Funding P&L sign-convention verification

**Documentation-level verification** (inherited from the prior session,
independently plausible and consistent with every general perpetual-
futures funding convention, not just assumed from generic crypto-exchange
knowledge): BingX's own docs state `fundingRate > 0` → longs pay shorts;
`fundingRate < 0` → shorts pay longs. Implemented as `payment =
-sign(position_qty) * abs(position_qty) * mark_price * funding_rate` in
`PositionTracker.apply_funding_through` — a long (`sign = +1`) with a
positive rate gets a negative payment (pays); a short (`sign = -1`) with
a positive rate gets a positive payment (receives).

**Unit-level verification** (inherited, `test_position.py`): four explicit
tests cover all four sign combinations (long/positive, long/negative,
short/positive, short/negative) plus the entering-vs-exiting-at-exact-T
boundary, multi-settlement summation, scaling-position notional
correctness, and the flat-period-must-be-skipped case.

**Real-data verification (this session, new)**: ran the actual
`HourlyMomentumStrategy` (`research/strategies/hourly_momentum.py`,
`fast=10, slow=30`) through the actual `backtest.engine.run_backtest`
against real pre-holdout 1h `BTC-USDT` klines (loaded via
`research.holdout.load_research_klines` against
`configs/research/holdout_1h.json` — research-window data only, holdout
untouched), producing 906 real fills / 453 real closed trades. Ran
`reconstruct_trades` twice on the identical fill sequence — once with no
`funding_rates` (existing behavior) and once with the real funding series
fetched from the now-backfilled cache for the same window (2,009 real
rows) — and located a trade whose `funding_pnl` was nonzero, i.e.
genuinely held open across a real funding settlement:

- Trade: **LONG** `0.1281663966017595415304898710 BTC`, entered
  `2024-04-29T23:00:00Z` @ `63862.87002`, exited (flattened) SHORT
  `2024-04-30T06:00:00Z` @ `63271.94308` — a real signal from a real
  strategy against real market data, not a synthetic fixture.
- One real funding settlement fell inside that window:
  `2024-04-30T00:00:00Z`, `fundingRate = 0.00009560`,
  `markPrice = 63825.0`.
- Price-only `realized_pnl` (funding_rates omitted): `-75.73697655470416449241529617`.
- `funding_pnl` (funding_rates supplied): `-0.7820290571530581417703441312`.
- Combined `realized_pnl` (funding_rates supplied):
  `-76.51900561185722263418564030`, confirmed equal to price-only P&L +
  `funding_pnl` (the designed additive relationship).
- Manually recomputed outside the pipeline as a cross-check:
  `notional = qty * mark_price = 0.1281663966017595415304898710 *
  63825.0 = 8180.220263107302738183516017`; `payment = -notional *
  funding_rate = -0.7820290571530581417703441312` — exact match to
  `funding_pnl` above, and negative (a **charge**) for a **long** paying
  a **positive** funding rate, exactly as the sign convention requires.

This confirms the sign convention end-to-end: real strategy → real
backtest fills → real `PositionTracker` reconstruction → real funding
data from the real backfilled cache, not just isolated unit-test
fixtures.

## Design notes worth stating explicitly (judgment calls already made in
the inherited code, recorded here rather than left implicit)

- **`mark_price` source**: funding payment uses the funding row's own
  historical `markPrice` (returned alongside `fundingRate` by the same
  endpoint call), not the position's entry price or a kline's close —
  this is what makes the computed payment match what BingX itself
  actually settles, since a real exchange computes funding off its own
  mark price at that instant, not off any particular position's cost
  basis.
- **Entering-vs-exiting-at-exact-T**: a position opened at the exact
  instant of a funding settlement is not charged for that settlement (it
  didn't exist going into the snapshot); a position closed at that exact
  instant is charged (it was open going into the snapshot going into the
  close). Implemented by calling `apply_funding_through(event_time)`
  *before* mutating position size for that event, inside both `apply()`
  and `force_close()`.
- **Cursor-based, redundant-call-safe design**: `apply_funding_through`
  only ever advances its internal cursor forward, so it's safe to call it
  both once per bar (`metrics.metrics.build_equity_curve`, so the equity
  curve reflects funding at the bar it settles on rather than only once a
  trade eventually closes) and once per fill (`PositionTracker.apply`/
  `force_close`, so a `ClosedTrade`'s `funding_pnl` is correct even
  without ever calling the bar-level hook) without any double-counting.
- **Fully additive/opt-in**: every function gaining a `funding_rates`
  parameter defaults it to `None`, and every pre-existing call site in
  this codebase omits it — verified both by dedicated
  "omitting-vs-explicit-None" tests and by the full suite passing
  unchanged.

## TDD / test results

`cd python && uv run pytest`: **639 passed**, 0 failed, 0 errors (full
suite, including all pre-existing tests — confirms the funding addition
didn't regress anything already in place). New/modified test files:
`test_bingx_funding.py`, `test_backfill_funding.py`,
`fake_bingx_funding_server.py` (new), `test_store.py`,
`test_position.py`, `test_metrics.py` (all modified, additive only).

## Deliberately out of scope

- **Wiring funding data into `research/holdout.py`'s loaders or the
  walk-forward harness** (`research/walkforward.py`). No
  `load_research_funding`/`load_holdout_funding` equivalent, and no
  `TrainableStrategy` currently threads `funding_rates` through to
  `compute_metrics`. This mirrors the project's own precedent: Task A
  (the kline data pipeline) shipped without any `research/` wiring either
  — that wiring was Task C's job, a separate, later task. The funding-
  rate signal follow-up task (queued next per CLAUDE.md) is the
  appropriate place to decide that wiring's shape, informed by whatever
  that strategy actually needs (e.g. whether funding-rate *level* itself
  becomes a feature, not just a P&L adjustment, changes what the loader
  should look like).
- **Re-running any of the six already-attempted strategies with real
  funding P&L included.** Nothing in "Strategy Attempts So Far"'s
  reported figures reflects funding P&L yet — this task only proves the
  machinery is correct, it doesn't retroactively apply it. Whether
  funding P&L would move Configuration C's Sharpe enough to matter is an
  open, interesting question but not this task's job to answer.
- **A CI job that runs `backfill_funding.py` automatically.** Same
  status as `backfill.py` — a manually-invoked research tool, not
  something CI runs (no live/scheduled data pipeline exists yet for
  either klines or funding).
- **Funding-rate *level* as a standalone predictive feature** (as
  opposed to funding P&L as a cost/benefit adjustment to a position
  already opened for other reasons) — that's the actual content of the
  follow-up strategy task, not this infrastructure task.
