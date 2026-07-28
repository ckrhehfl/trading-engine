# Strategy Research Task T: the 1d data path and an early-window holdout

## Scope note

This task builds **only the data path** to BingX's daily klines, plus the
holdout-config mechanics needed to reserve the part of that data no trial
in this project has ever touched. **No strategy was written, run, or
evaluated against 1d data here**, and that is deliberate rather than
incidental: the strategy specification for this dataset is a separate,
later PR, and it must be committed *before* the virgin window is ever
loaded. If a strategy were designed after someone had already looked at
results on this data, the window would be contaminated exactly the way
the 1h window already is — which is the entire problem this task exists
to route around.

Prerequisites read in full first: CLAUDE.md (especially "Exchange API
Facts — BingX" and "Strategy Research Methodology"),
`.planning/sr-a-data-pipeline.md` (the pipeline this extends, including
its retention-probing methodology), `.planning/sr-c-walkforward-holdout.md`
and `.planning/sr-f-risk-management-and-1h-variant.md` (which set the two
existing holdout configs).

## Why this task exists

After eight strategy families and **1,839 logged backtest runs**, nothing
has cleared the Eligibility Bar. A diagnostic pass found something more
useful than another strategy idea:

**Every one of those 1,839 runs has `data_range.start_ms >=
1714212000000` (2024-04-27T10:00:00Z).** Verified directly against the
real `runs/experiments.jsonl` — 22 distinct `start_ms` values across the
whole log, the minimum being exactly the floor of BingX's 1h retention.
That is not a coincidence: `sr-f` moved primary research to 1h bars, and
1h retention starts there, so every trial since has been shaped by data
from that date onward and by *none* before it.

BingX retains materially more history at coarser granularity. At `1d`
there is real data going back years further — data no trial in this
project's history has ever seen. That window is a larger and
statistically cleaner holdout than the actual designated holdout:

| Window | Span | Detectable annualized Sharpe (`1.645/sqrt(years)`) |
|---|---|---|
| 1d early window (this task) | ~2.95 years | **~0.96** |
| 1h trailing holdout (`holdout_1h.json`) | 150 days (~0.41 y) | ~2.57 |

A holdout that can only confirm an edge above 2.57 annualized Sharpe
cannot confirm any realistic edge this project is likely to find. One
that resolves ~0.96 can.

## What was built

1. **`"1d": 86_400_000` in `python/data/_grid.py`** — the one-line
   addition `_grid.py`'s own docstring has anticipated since Task A
   (and which Task F already exercised once for `1h`), plus a docstring
   note recording the verified grid-alignment finding below.
2. **A real backfill run** against the live production endpoint, writing
   to the shared cache — numbers below.
3. **Optional `"holdout_side": "before" | "after"`** in the holdout
   config schema, handled by `python/research/holdout.py`'s loaders,
   defaulting to `"after"`.
4. **`configs/research/holdout_1d.json`** — `"holdout_side": "before"`,
   with the cutoff rule and its rationale written before any strategy
   result on this data existed (none does; see Scope note).

37 new tests (`test_grid.py` 12 — a new file, `test_backfill.py` +5,
`test_holdout.py` +20). Full suite: **737 passed**, up from 700, with one
pre-existing test updated (see "Consumer audit" below). Nothing
regressed.

## Real backfill: the actual numbers

Run against the live, public, unauthenticated production endpoint
(`GET /openApi/swap/v3/quote/klines`, `interval=1d`) on **2026-07-28**,
`--start 2018-01-01T00:00:00+00:00` (a sentinel well before any plausible
retention edge) through the default "now floored to the grid", writing to
the shared cache at `/mnt/c/Dev/trading-engine/python/data/var/klines.sqlite3`:

- **Earliest available bar: `2021-05-14T00:00:00Z`**
  (`open_time_ms = 1620950400000`)
- **Latest bar this run: `2026-07-27T00:00:00Z`** (`1785110400000` —
  "now" floored to the day grid excludes the still-forming 2026-07-28
  bar)
- **Total: 1,901 rows**
- **Internal gap count: 0** — the real number, not a probe estimate.
  Verified two independent ways: a direct diff of every consecutive
  `open_time_ms` pair against the 86,400,000ms step (zero pairs off by
  more than one step), and `store.find_missing_ranges` re-queried over
  `[earliest, latest+1d)` returning `[]`. The expected contiguous count
  for that span is 1,901, and 1,901 rows are stored — every single
  expected daily slot is present.
- **Zero rows off the UTC-midnight grid.**
- Wall clock: ~1.5 seconds, 4 requests. At 1d a 1000-candle page spans
  ~2.74 years, so the whole 8.5-year sentinel range is 4 pages — versus
  ~231 for the 15m run in `sr-a`.

### How that compares to CLAUDE.md's recorded probe-only figure

CLAUDE.md recorded `1d` as "back to ~2021-05-12 (~5 years)", explicitly
flagged as a **binary-search probe only** — the stronger
"full-backfill-with-zero-gap-count" verification had only ever been done
for `1h`.

**The probe was essentially right, and is now upgraded to the same
standard as `1h`:**

- Earliest date: probe said ~2021-05-12; the real backfill says
  **2021-05-14** — 2 days later. Given BingX retention is rolling and the
  probe ran 2 days earlier (2026-07-26), a 2-day forward drift is exactly
  what a fixed-length rolling window would produce. This is consistent
  with rolling retention *and* with ±2 days of probe imprecision; the two
  cannot be distinguished from a single pair of observations, and this
  doc does not claim to have distinguished them.
- Duration: probe said "~5 years"; real span is **5.21 years** (1,901
  bars). Confirmed, not corrected.
- **This is the opposite outcome from `1h`**, where the same style of
  binary-search probe got the earliest *date* right but undercounted the
  derived *duration* by ~1.8x (see `sr-f`). Worth recording: probe
  accuracy has now been checked against a real backfill at two
  granularities, and the failure mode found at `1h` did not recur here.
- Gaps: previously **unknown** for `1d` (a probe finds an edge, it can't
  find holes). Now measured: **zero**.

Honest negative findings: none. Depth and integrity both came back at
least as good as the probe suggested. The one thing worth flagging as a
cost rather than a finding is in "Disclosed costs" below.

## `holdout_side`: the design, and why `"before"` is correct here

### The mechanic

An optional config key, `"before"` or `"after"`, defaulting to `"after"`.
It names which side of `holdout_cutoff_ms` the **holdout** occupies;
research data is always the other side.

```
"after"  (default): holdout = [cutoff, inf), research = (-inf, cutoff)
"before":           holdout = (-inf, cutoff), research = [cutoff, inf)
```

- `load_research_klines` / `load_research_funding` clamp the requested
  range away from the holdout side — `end_ms` down to the cutoff under
  `"after"` (unchanged behavior), `start_ms` up to it under `"before"`.
  Both share one `_clamp_research_range` helper so the two loaders
  cannot drift apart; a research loader clamping the wrong end would
  silently serve holdout data, and no test a *caller* could write would
  notice.
- `load_holdout_klines`'s guard mirrors it: `"after"` rejects
  `start_ms < cutoff` (unchanged), `"before"` rejects `end_ms > cutoff`.
  It raises rather than trimming, because a caller asking the holdout
  door for research data has misunderstood which split they are in.
- Everything else on the holdout path is side-agnostic and untouched:
  the explicit `i_understand_this_is_holdout_data` keyword, the
  single-access claim per `strategy_id`, `force_reclaim_reason`.
- An invalid `holdout_side` fails loud in `load_holdout_config` at load
  time. A typo'd side would otherwise hand a caller the wrong half of
  the dataset while looking like it worked.
- `load_holdout_config` still returns the file's contents **unmodified** —
  no default is injected into the returned dict, so a config without the
  key reads back exactly as written and nothing downstream can start
  depending on a key the committed 15m/1h files don't have.

### Why `"before"` is not backwards

It looks wrong at a glance. The reflex is that a holdout must be the most
*recent* data, because the usual reason to hold data out is to simulate
"the future the model hasn't seen." That framing is right when the
alternative is training on the future and testing on the past. It is not
what a holdout is *for* in this project.

CLAUDE.md's Strategy Research Methodology defines the holdout by what has
been **done to** the data, not by where it sits on the calendar:

> A holdout data split must exist and stay untouched until a strategy is
> otherwise ready for paper trading — not used for iterative tuning.
> Touching it converts it from a validation check into just more training
> data.

The property being protected is *no decision in this project has been
informed by the contents of this data*. For the 1d dataset that property
holds for the **early** window and fails for the late one — 1,839 runs
have already been fitted, diagnosed and judged against 2024-04-27 onward.
Reserving a trailing 1d slice would reserve data this project has already
studied intensively through a shorter timeframe's lens. Choosing the
calendar-late window here would satisfy the convention and defeat the
purpose.

This reasoning is written out at length in `python/research/holdout.py`'s
module docstring as well as here, deliberately: a future reader hitting
`"holdout_side": "before"` cold will assume it is a bug unless the
argument is immediately in front of them.

### Backward compatibility, verified rather than asserted

`configs/research/holdout.json` (15m) and `configs/research/holdout_1h.json`
(1h) are unchanged on disk and silent on `holdout_side`, so both resolve
to `"after"`. Pinned by tests three ways: their committed files are
asserted to have no `holdout_side` key; omitting the key is asserted to
produce results *identical* to an explicit `"after"` config for all three
loaders; and every pre-existing `test_holdout.py` test passes unchanged.

One pre-existing behavior was pinned by a new test rather than changed:
under `"after"`, a research request lying wholly at/after the cutoff
clamps to an empty range and raises `ValueError` (via
`require_valid_range`) rather than returning `[]`. That was never
covered by a test before. It is now, alongside its `"before"`-side
mirror, so the symmetry is demonstrable rather than claimed.

## The 1d cutoff, and its rationale — written before any result existed

**`holdout_cutoff_ms = 1714176000000` (2024-04-27T00:00:00Z),
`holdout_side = "before"`.**

- **Where the boundary comes from**: the earliest instant any logged run
  has ever touched is 2024-04-27T10:00:00Z (the minimum `start_ms` across
  all 1,839 `backtest_run` records). The cutoff is that instant **floored**
  — not rounded up — to the 86,400,000ms daily grid.
- **Why floored**: the 2024-04-27 daily bar spans 10 hours that logged
  runs have seen. Flooring puts that partially-overlapping bar on the
  *research* side, so no bar inside the holdout covers any wall-clock
  minute any logged run has ever observed. Rounding up would have pulled
  a half-seen bar into the holdout.
- **Resulting split**: holdout **1,079 bars** (2021-05-14 → 2024-04-26
  inclusive, ~2.95 years); research **822 bars** (2024-04-27 →
  2026-07-27, ~2.25 years).
- **The holdout is larger than the research split**, deliberately
  breaking the "15-25% of available data" framing both other configs
  follow. That framing assumes a dataset whose non-holdout remainder is
  itself untouched, making the split a free choice about proportion.
  Here it is not a choice at all: the boundary is a historical fact about
  where contamination begins. Moving it in either direction either wastes
  virgin data or admits touched data into the holdout.
- **Ordering**: the cutoff and rationale were committed before any
  strategy was written, run or evaluated against 1d data. The 1d interval
  was only wired into `_grid.py` in this same task, and no backtest of
  any kind has ever run on it. That ordering is the whole point — a
  cutoff chosen after seeing a result on the data it splits is not a
  holdout. The same statement is recorded in the config's own committed
  `rationale` field, not just here.

### Disclosed costs

Recorded in the config's `rationale` too, so they travel with the
artifact:

1. **822 daily bars is thin for walk-forward at this granularity.** Fold
   sizing for 1d research is deliberately *not* decided here; it belongs
   to whichever task first runs a strategy on this data.
2. **Calendar overlap with the 1h holdout.** The 1d *research* window
   covers the same span `holdout_1h.json` reserves as the 1h holdout
   (2026-02-26T08:00:00Z onward). Different series, different configs —
   but a future task intending to run both holdout confirmations should
   know about the overlap in advance rather than discover it afterwards.
3. **Rolling retention shrinks this holdout over time.** The earliest 1d
   bar drifts forward on every future backfill, so the holdout gets
   smaller while the research side grows. The cutoff is fixed and must
   **not** be moved to chase it — moving it forward would silently admit
   already-touched data into the holdout.

## Consumer audit: what breaks at daily granularity

Every consumer of `_grid.INTERVAL_MS` was checked rather than assumed
safe.

- **`bingx_klines.iter_klines_range`** — `chunk_span = limit * step`
  becomes ~2.74 years per page. Safe, and in fact the property that makes
  BingX's silent newest-end capping unreachable: each request is sized to
  exactly `limit` candles, never more. No change.
- **`store.find_missing_ranges` / `fetch_klines`** — pure step
  arithmetic, dimensionally correct at any step. No change. Verified
  end-to-end at 1d against the fake server, including refetching exactly
  one genuinely-missing day.
- **`backfill.main`'s "now floored to the grid" default for `--end`** —
  at 1d that rounding is a whole day wide rather than 15 minutes. This is
  a *feature*, not a defect: it is what keeps the still-forming current
  daily bar out of the cache. Pinned by a new test asserting every
  `endTime` actually sent is day-aligned. No change.
- **`holdout._kline_row_to_kline`** — `open_time_ms // 1000` is exact for
  any multiple of 1000ms. No change.
- **The `(symbol, interval, open_time_ms)` primary key** — 1d rows
  coexist with the 15m and 1h rows already in the shared cache; a daily
  and an hourly bar sharing an open time are distinct rows. Verified by
  test *and* by the real cache, which now holds all three intervals at
  once.
- **`metrics.compute_metrics`'s `bars_per_day`** — already a parameter
  since `sr-f` (default 96 for 15m; 24 was passed for 1h). A 1d strategy
  will need `bars_per_day=1` or its reported Sharpe is inflated ~9.8x.
  **Not changed here** — nothing in this task computes a metric — but
  flagged loudly, because it is the one place where getting daily
  granularity wrong produces a plausible-looking wrong number instead of
  an exception.
- **`python/tests/test_bingx_klines.py::test_fetch_klines_page_rejects_unsupported_interval`**
  — the one real breakage: it used `"1d"` as its stand-in for an unwired
  interval. Switched to `"5m"` (still deliberately unwired, per
  `_grid.py`'s docstring), which plays exactly the same role. The
  assertion itself is unchanged.

## Verified empirical finding: the 1d grid is UTC midnight

Checked against the live endpoint before committing the constant, not
assumed: every returned daily bar's `time` is exactly a multiple of
86,400,000. BingX does **not** open its daily candle at a
local/exchange-timezone offset — which mattered, because an 8-hour
offset (plausible for a Singapore-headquartered venue) would have made
`86_400_000` produce a grid every real bar failed alignment against,
while still looking obviously correct in source.

## Judgment calls resolved without asking

- **`test_grid.py` is a new file.** `_grid.py` had no dedicated test
  module — it was only ever exercised through its three consumers. Adding
  a third interval is the point at which the grid's own arithmetic
  deserves direct tests, rather than being asserted as a side effect of a
  kline-fetch test.
- **`resolve_holdout_side` is public, `_clamp_research_range` is
  private.** The former is a question a caller can legitimately ask about
  a config it loaded; the latter is an internal invariant shared by two
  loaders.
- **`load_holdout_config` validates but does not normalize.** Validating
  at load time makes a typo'd side fail loudly and early; injecting a
  default into the returned dict would change what every existing caller
  sees. Both properties are pinned by tests.
- **Sentinel start date of 2018-01-01 for the backfill**, versus `sr-a`'s
  2020-01-01. Cheap insurance at 1d (one extra empty page, ~0.3s) against
  the retention edge being deeper than CLAUDE.md's probe suggested. It
  wasn't.
- **No `load_holdout_funding` added.** Still no caller needs one, exactly
  as `holdout.py`'s existing docstring says. `"before"` support would be
  a two-line addition there when a real task needs it.

## TDD

Red-green-refactor throughout, in three cycles:

1. `test_grid.py` written against the not-yet-added `"1d"` entry — 4
   failures (`ValueError: unsupported interval: '1d'`), then the one-line
   `_grid.py` addition, then green.
2. `test_backfill.py`'s five 1d consumer tests written next — 4 more
   failures for the same reason, green after the same one-line change
   (proving the consumers genuinely needed nothing else).
3. `test_holdout.py`'s `holdout_side` block written against the
   not-yet-existing feature — 9 failures, then the loader changes, then
   the committed `holdout_1d.json`, then green.

One test was rewritten *during* the red phase rather than after: the
`"before"`-side "entire request is holdout" case originally asserted
`[]`, and checking the pre-existing `"after"` path empirically (rather
than assuming) showed it raises `ValueError` instead. The test was
corrected to match the established behavior and the `"after"` mirror was
pinned alongside it — the symmetry was found by checking, not asserted.

## CodeRabbit review findings

One actionable finding on the first review pass, accepted and fixed:

- **`_clamp_research_range`'s `else` branch silently treated an unknown
  `side` as `"before"`.** Both real callers pre-validate via
  `resolve_holdout_side`, so there was no live path that could reach it —
  but an `if after: ... else: ...` shape means a typo'd side inverts
  which end of the range gets clamped, i.e. hands holdout data to a
  research caller. That is exactly the failure this module's fail-loud
  discipline exists to prevent, and the helper was contradicting the
  principle its own docstrings state three times over. Fixed with an
  explicit `elif HOLDOUT_SIDE_BEFORE` plus a raising `else`, and a test
  that fails without it (`DID NOT RAISE ValueError`, confirmed red
  first). A second test pins that the two valid sides clamp *opposite*
  ends — so the branch that was previously reachable-by-accident is now
  positively specified rather than just guarded. 739 tests total (was
  737), all green.

## Deliberately out of scope

- **Any strategy on 1d data.** See Scope note. Not "not done yet" — must
  not be done in this PR.
- **Walk-forward window sizing for 1d.** Belongs with the first strategy
  that actually runs on this data, which will also need to decide
  `bars_per_day=1`.
- **`5m`.** Still unwired. Still a one-line addition when something needs
  it.
- **A funding-rate counterpart for the 1d split.** Funding has no
  interval concept, and `holdout_1d.json`'s cutoff would govern both if
  a caller passed it to `load_research_funding` — which works today via
  the `"before"` clamp, but nothing does it yet.
- **Re-probing 5m/15m/1h retention.** This task re-verified `1d` only.
  The other three keep whatever verification status CLAUDE.md already
  records for them.
