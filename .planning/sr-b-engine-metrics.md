# Strategy Research Task B: `KlineWindow` performance fix + `python/metrics/`

## Scope note

This is **Task B** of the "Strategy Research Operational Design
(2026-07-25)" section in CLAUDE.md (build sequencing: A → B → C → D).
Independent of Task A (`python/data/`), Task C (`python/research/` —
walk-forward + holdout + experiment log), and Task D (the placeholder
`TrainableStrategy`) — nothing here reads from or depends on any of
those. Two parts, both scoped exactly as CLAUDE.md's design section
describes them:

1. Replace `engine.py`'s O(n) `klines[: i + 1]` copy (O(n²) over a full
   run) with an O(1) `KlineWindow` view, without weakening the existing
   lookahead-safety guarantee.
2. A new top-level `python/metrics/` package (deliberately not inside
   `backtest/` — see `backtest/engine.py`'s own `run_backtest` docstring:
   "Deterministic fill simulator — not a P&L/portfolio backtester")
   reconstructing position/trade history and computing equity-curve-based
   summary statistics.

## What was built

### Part 1 — `python/backtest/kline_window.py`

`KlineWindow(klines, length)` holds a reference to the original list plus
an `int` length — O(1) construction, O(1) bounds-checked `__getitem__`.
Implements the `Sequence[Kline]` protocol directly (`__len__`,
`__getitem__` for both `int` and `slice`, `__iter__`) rather than
subclassing `collections.abc.Sequence` — see "Judgment calls" below for
why.

`engine.py` changes:
- `klines[: i + 1]` → `KlineWindow(klines, i + 1)` in `run_backtest`'s
  loop.
- `Strategy` widened from `Callable[[list[Kline]], OrderIntent | None]`
  to `Callable[[Sequence[Kline]], OrderIntent | None]`, using
  `from typing import Callable, Sequence` (matching the file's existing
  `from typing import ...` style — it does not use `collections.abc`
  anywhere, so this doesn't switch that convention).
- `BacktestResult` gains `filled_intents: list[OrderIntent] =
  field(default_factory=list)`, index-aligned with `fills` (populated in
  the same loop iteration a fill is appended). Purely additive — `Fill`
  has no `side` field, so Part 2's position reconstruction needs the
  originating `OrderIntent` to know direction.

### Part 2 — `python/metrics/`

- **`position.py`**: `PositionTracker` walks `(OrderIntent, Fill)` pairs
  one at a time via `apply()`, maintaining a running signed
  `position_qty` (positive long, negative short, zero flat) and
  `avg_entry_price` (size-weighted), and returns a `ClosedTrade` whenever
  a lifecycle (0 → nonzero → 0) fully closes — aggregating P&L across any
  intermediate partial-reduction fills into one record, not one row per
  fill. `force_close(price, time)` closes whatever's currently open
  (used by `metrics.py` for the final-bar force-close rule). A
  convenience `reconstruct_trades(filled_intents, fills)` runs a tracker
  over a whole list and returns just the closed trades, for callers that
  don't need the bar-by-bar interleaving `metrics.py` does.
- **`metrics.py`**: `build_equity_curve(klines, filled_intents, fills,
  starting_equity)` walks `klines` bar by bar, applies every fill whose
  `fill_time` has been reached, marks the open position to that bar's
  close, and force-closes anything still open at the final bar.
  `compute_metrics(...)` wraps that and reduces it to a `Metrics` record:
  `total_return`, `sharpe_ratio`, `max_drawdown`, `win_rate`,
  `num_trades`, `profit_factor`, plus `starting_equity`/`final_equity`/
  `equity_curve`/`closed_trades` for introspection.

## TDD

Both parts: tests written first, confirmed to fail on `ModuleNotFoundError`
(module didn't exist yet — not a "wrote the wrong assertion" failure, an
actual "nothing to import" failure), then minimum code added.

- `kline_window.py`: 14 tests confirmed failing (`ModuleNotFoundError:
  No module named 'backtest.kline_window'`) before the class existed,
  then all 14 passed on the first implementation attempt.
- `metrics/position.py`: 12 tests confirmed failing the same way before
  implementation, then passed on the first attempt.
- `metrics/metrics.py`: 17 tests confirmed failing before implementation.
  One genuine red→green cycle here, not just the initial
  `ModuleNotFoundError`: the first implementation attempt failed
  `test_equity_curve_reflects_realized_pnl_and_fees_after_a_close`
  because the *test's own expected value* was wrong (it assumed bar 0's
  close was 105 when the fixture actually set it to 100, so the correct
  unrealized P&L at bar 0 was 0, not 5) — traced by hand rather than
  patched blindly, confirmed the implementation was right and the test
  assertion was the bug, fixed the assertion, reran green.

Final: `cd python && uv run pytest -q` → **98 passed** (53 pre-existing +
45 new: 14 `kline_window` + 2 new `engine.py`/`BacktestResult` tests + 12
`position` + 17 `metrics`).

## Confirming the design's "zero existing test changes" claim

**Holds, verified by actually running the tests, not assumed:**

- `python/tests/test_fill.py`: `git diff` on this file is **empty** —
  byte-for-byte untouched. Nothing in Part 1 changes `fill.py` or
  anything `test_fill.py` exercises.
- `python/tests/test_engine.py`: diff is **purely additive** — two new
  test functions (`test_filled_intents_is_index_aligned_with_fills`,
  `test_no_fill_means_no_entry_appended_to_filled_intents`) appended,
  covering the new `filled_intents` field. Every pre-existing line,
  including the lookahead-safety test
  (`test_strategy_only_ever_sees_bars_up_to_and_including_the_current_one`),
  is unmodified and still passes against the `KlineWindow`-based
  implementation with zero edits.

This matches the design's claim: `list[Kline]` already satisfies
`Sequence[Kline]`, so widening `Strategy`'s type is behaviorally
transparent to every existing caller, and swapping the copy for a view
doesn't change any observable behavior the existing tests check.

## Judgment calls resolved without asking

- **`KlineWindow` implements the `Sequence[Kline]` protocol directly
  rather than subclassing `collections.abc.Sequence`.** The task
  description names exactly three methods (`__len__`, `__getitem__` for
  both `int`/`slice`, `__iter__`); subclassing the ABC would pull in
  `__contains__`/`__reversed__`/`index`/`count` mixins that nothing in
  this codebase calls today, and this class's entire job is being a
  small, fully-auditable safety boundary — smaller surface area is
  better for a class whose only reason to exist is "prove lookahead bias
  is structurally impossible," not "be a fully general Sequence." There
  is no `isinstance(x, Sequence)` check anywhere in this codebase that
  this decision would break; `typing.Sequence[Kline]` as the `Strategy`
  parameter's type hint doesn't require nominal ABC membership to work
  at runtime (Python doesn't check type hints at call time), only for a
  static type checker — and this repo has no mypy/ruff CI gate today
  (confirmed: `.github/workflows/` has no such job), so this is a
  documentation-only tradeoff, not a functional one.
- **`KlineWindow.__init__` validates `length` (rejects negative, rejects
  `> len(klines)`) even though the task's literal wording ("bounds-checked
  O(1) indexed access") only names indexed *access* as needing bounds
  checking, not construction.** Kept the construction-time check anyway:
  it converts a caller bug in `engine.py` (or any future caller) from a
  silent wrong-answer or a confusing downstream `IndexError` into an
  immediate, obviously-located `ValueError` at the point of misuse. Cheap
  insurance for a safety-critical class; a test for both directions
  (`test_construction_rejects_length_greater_than_underlying_list`,
  `test_construction_rejects_negative_length`) is included.
- **`ClosedTrade.quantity` is defined as total quantity *opened* over the
  lifecycle, not exit quantity or peak position size.** These are
  identical for a simple open-then-fully-close trade, but the scaling-out
  test (`test_scaling_out_in_pieces_aggregates_into_one_trade_not_one_per_fill`)
  exercises the case where they'd diverge if defined differently (a single
  entry of 3, closed via three separate exit fills of 1 each — "quantity
  opened" and "quantity closed" happen to still match here since nothing
  scales *in*; the scaling-in test separately confirms the opened-quantity
  definition against multiple entry fills). Chose "opened" because it
  answers "how big was this trade," which is what a win-rate/profit-factor
  consumer actually wants; "closed" would give the same number for every
  fully-closed trade anyway by conservation, so the choice only matters
  narratively, not numerically, for any trade that isn't force-closed
  mid-fill.
- **`ClosedTrade.exit_price` is the size-weighted average across every
  closing fill in the lifecycle**, computed the same way
  `avg_entry_price` is for entries (mirrors the design's explicit
  size-weighted-average rule for entries, applied symmetrically to exits
  — the design doesn't specify an exit-price field at all, so this was
  the natural extension, and useful for anyone auditing a trade record
  later).
- **`PositionTracker` exposes running state as properties
  (`position_qty`, `avg_entry_price`, `realized_pnl`,
  `cumulative_fees`) rather than only returning a final aggregate.**
  `metrics.py::build_equity_curve` needs the position's *current* state
  between fills (to mark-to-market every bar, not just the bars where a
  fill happens to land) — a single "run the whole list and give me the
  end result" function (which `reconstruct_trades` still provides, for
  callers that only want the trade list) can't support that interleaved
  walk. Chose to expose a stateful class as the primary API and layer the
  batch convenience function on top, rather than the reverse.
- **`force_close`'s effect on `equity_curve[-1]` is exactly zero by
  construction, not approximately zero** — verified explicitly by
  `test_still_open_position_is_force_closed_at_the_final_bars_close_price`,
  which asserts the post-force-close equity curve's last value equals
  `starting_equity + realized_pnl` with no separate "before force-close"
  value to compare against, since `build_equity_curve` always marks the
  final bar to market at that bar's close *before* checking whether to
  force-close — force-closing at that identical price only reclassifies
  an already-counted unrealized amount as realized, it doesn't add a new
  amount. This was checked by tracing the code path, not assumed from
  the CLAUDE.md wording alone.
- **Two defensive guards beyond the letter of the design, both
  documented inline rather than left as silent behavior:**
  - `_sharpe_ratio` skips (rather than raising `ZeroDivisionError` on) any
    per-bar return whose denominator (`previous` equity) is exactly zero.
    CLAUDE.md's design doesn't name equity reaching exactly zero as a
    scenario to handle — the degenerate cases it does name are zero
    trades and zero-variance returns — but nothing prevents a
    sufficiently bad backtest from reaching zero equity, and silently
    skipping an unusable data point is safer than crashing metrics
    computation over it.
  - `_max_drawdown` only computes a drawdown ratio when `peak > 0`,
    guarding the same zero/negative-equity edge for the same reason.
  - `compute_metrics` raises `ValueError` if `starting_equity <= 0` — an
    explicit precondition rather than a silent `ZeroDivisionError` inside
    `total_return`'s `final_equity / starting_equity`.
- **`win_rate`/`profit_factor` return `0.0` (not `None`) when there are
  closed trades but literally zero winners (or, for profit factor, are
  computed normally when there's a genuine positive denominator)** —
  this is deliberate and matches CLAUDE.md's design precisely: `None`
  means "no evidence" (zero trades), `0.0` means real evidence of a 0%
  win rate. Only "zero *losing* trades" maps to `profit_factor: None`
  (not `inf`), per the design's explicit carve-out; an all-loser fold's
  `profit_factor` computes as a real (small or zero) number, not `None`.

## CodeRabbit review findings

First review pass hit the rate limit (status `success`/"Review rate
limited" on the initial commit status). Followed CLAUDE.md's documented
procedure: commented `@coderabbitai rate limit`, got an exact ETA ("your
next review will be available in 22 minutes"), waited it out rather than
guessing or polling tightly, then triggered a fresh review with
`@coderabbitai review`. The real review came back with **3 actionable
comments, all in one pass** (`request_changes_workflow: true`, so the PR
review state was `CHANGES_REQUESTED`), batched and fixed together in one
push rather than round-tripping per finding.

**Fixed, both cheap and genuinely valid:**

- **`metrics.py::build_equity_curve` could silently drop fills.** The
  fill-consumption `while` loop assumes every fill's `fill_time` lines up
  with some kline's `open_time` in the same list (true by construction
  for anything coming out of `run_backtest` — `fill.py` always sets
  `fill_time = next_bar.open_time` for a bar in the same `klines` list).
  If that contract were ever violated (a fill later than the last kline),
  the loop would finish with `fill_index < n_fills` and just silently
  never apply the remaining fills — wrong equity/trade-count numbers with
  no signal anything went wrong. Fixed by asserting `fill_index ==
  n_fills` after the loop, exactly as suggested. Test written first
  (`test_a_fill_after_the_last_klines_open_time_raises_instead_of_silently_vanishing`),
  confirmed it failed (no exception raised — the bug CodeRabbit
  described, reproduced) before adding the assertion, then confirmed
  green.
- **`position.py::reconstruct_trades`'s `zip(filled_intents, fills)`
  silently truncates on a length mismatch** (also independently flagged
  by Ruff as `B905`). Fixed with `zip(..., strict=True)`, exactly as
  suggested — `metrics.py::build_equity_curve` already enforces the same
  index-alignment contract via direct indexing (`filled_intents[
  fill_index]`, which raises `IndexError` on mismatch), so this closes an
  inconsistency between the two, not just a standalone gap. Test written
  first
  (`test_reconstruct_trades_raises_on_mismatched_length_inputs_instead_of_silently_truncating`),
  confirmed failing, then green.

**Declined, with reasoning — the substantive one, given real thought
rather than blindly accepted or blindly dismissed:**

- **`KlineWindow`'s `_klines` attribute holds a live reference to the
  *entire* underlying list, not just the visible prefix — so
  `window._klines[length]` reads a real future bar, bypassing the
  bounds-checked `Sequence` protocol entirely.** This is a correct,
  accurate finding, not a false positive: the class's guarantee ("a
  strategy cannot index or iterate past the current bar") holds for every
  access path through `__getitem__`/`__iter__`/`len()` — the actual
  `Sequence[Kline]` interface every `Strategy` is written against — but
  not against code that deliberately reaches around that interface via
  the "private" (single-underscore, convention-only) attribute.
  CodeRabbit's own suggested fixes were a truncated copy per bar or a
  process-isolation boundary for strategy execution, and it tagged the
  finding itself "🏗️ Heavy lift" with no concrete diff offered — i.e. it
  also didn't have a cheap fix in mind.

  Traced why neither suggested fix is actually right for this codebase,
  rather than picking one under review pressure:
  - **A truncated copy per bar reintroduces the exact O(n²) cost this
    entire class exists to eliminate** — the task this PR implements.
    Doing that would satisfy the review comment while quietly reverting
    the PR's actual purpose.
  - **Process-isolating strategy execution** has no precedent anywhere in
    this codebase (no such boundary exists for any other component
    either) and is wildly disproportionate to the actual threat model:
    `Strategy` implementations in this codebase are trusted, first-party,
    TDD'd research code, not third-party or adversarial plugins. The
    realistic failure mode this class defends against is an *accidental*
    lookahead bug in otherwise-normal strategy code (e.g. a strategy
    naively assuming it has the full list and over-indexing) — which the
    `Sequence` protocol bound already catches structurally, with a clean
    `IndexError`.
  - **Also considered and rejected: renaming `_klines` to `__klines`**
    (Python name-mangling) to specifically defeat the exact
    `visible._klines[length]` spelling CodeRabbit used. Rejected because
    it doesn't actually close anything — `window._KlineWindow__klines`
    still works, so this only moves the bar from "obvious" to "one grep
    of this file away." Shipping that would be exactly the kind of
    false-confidence security theater this project already explicitly
    rejected once before, for an unrelated component, in
    `.planning/09-order-store-hardening.md` ("every other interim
    mechanism considered failed for the same structural reason or
    amounted to false-confidence theater").

  What *was* done instead, in this PR: the class docstring gained an
  explicit "Scope of the guarantee" section spelling out exactly what is
  and isn't covered and why (see `python/backtest/kline_window.py`), and
  a new test,
  `test_the_sequence_protocol_is_the_only_bounds_checked_access_path_not_the_backing_reference`,
  documents the limitation directly — it does not claim the bypass is
  prevented (it isn't), it demonstrates and comments the accepted gap so
  a future reader hits documentation instead of rediscovering this from
  scratch. Replied on the CodeRabbit review thread with this same
  reasoning, condensed, so the finding is visibly addressed rather than
  silently dropped.

## Deliberately out of scope

- **Task A's data pipeline, Task C's walk-forward/holdout harness, Task
  D's placeholder strategy** — independent tasks per the build-sequencing
  note in CLAUDE.md's design section; nothing here depends on or was
  blocked by any of them.
- **Perpetual funding-rate P&L** — CLAUDE.md's design explicitly flags
  this as a known gap not solved by this design; `metrics.py`'s module
  docstring repeats the flag so it isn't silently forgotten by a future
  reader who only sees this file.
- **`numpy`/`pandas`** — zero new dependencies, as required; all
  arithmetic is stdlib `decimal`/`statistics`/`math`, matching the
  "no numpy/pandas until a real strategy's feature engineering needs
  vectorized ops" stance already in CLAUDE.md's Strategy Research
  Operational Design section (data-pipeline paragraph).
- **Any change to `fill.py`, `kline.py`, or fill-simulation behavior** —
  Part 1 only touches `engine.py` (loop body + `Strategy`/
  `BacktestResult` type) and adds `kline_window.py`; Part 2 only reads
  `Fill`/`Kline`, never constructs or mutates them differently than
  `backtest/` already does.
