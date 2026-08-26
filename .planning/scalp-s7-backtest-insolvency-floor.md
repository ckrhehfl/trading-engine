# Scalping Strategy Research Task S7 — backtest engine insolvency floor

Resolves the "circuit-breaker" half of the open, project-wide question
both `.planning/scalp-s4-vwap-mid-reversion-result.md` and
`.planning/scalp-s6-ofi-momentum-result.md` disclosed and left for a
future task: "should this project's backtest engine eventually gain a
real insolvency/circuit-breaker concept... or equity-compounding
sizing." This task builds the circuit-breaker half only. Full scope
boundary below.

## The real motivating problem

`python/backtest/engine.py::run_backtest` is a pure fill simulator with
zero knowledge of account equity or portfolio state — it calls
`strategy(visible_klines)` every bar and executes whatever `OrderIntent`
comes back, forever, even if a real account would have gone bankrupt
bars ago. Equity/P&L is only reconstructed **after the fact**,
downstream, by `python/metrics/metrics.py::compute_metrics`/
`build_equity_curve` — `run_backtest` itself never saw it.

Two real scalping strategy holdout backtests hit this for real, both
sizing every trade against a **fixed** `reference_equity` constant
(`research/strategies/risk_management.py::compute_position_size`,
`DEFAULT_REFERENCE_EQUITY=Decimal("10000")`) that never shrinks even as
real losses accumulate:

- `vwap-mid-reversion` (Scalping Task S4): a no-exit mean-reversion
  position held against a market that never reverted, raw `final_equity`
  **-$1,051,858** across 44,344 trades, 1.13% win rate.
- `ofi-momentum` (Scalping Task S6): 56,441 repeated open/close round
  trips at a 17.98% win rate against a ~33.3% breakeven need, raw
  `final_equity` **-$23,906,095** — this despite a real, working ATR
  stop bounding *each individual trade's* loss; the stop bounded no
  single trade's damage but did nothing to bound the *sum* of tens of
  thousands of trades against a persistently negative edge.

Both figures are honestly disclosed in their own result write-ups as
severity signals, not literal dollar amounts a real leveraged account
would ever reach (a real account would have been liquidated by its
exchange enormously earlier) — but the underlying engine gap that let a
strategy keep trading arbitrarily far past real insolvency is real, and
this task closes it.

## Scope: circuit-breaker only, not equity-compounding sizing

CLAUDE.md's Task S6 section named two different possible remedies:
"halting further trading once cumulative losses pass some threshold, or
sizing against real running equity rather than a fixed reference
constant." This task builds only the first. `research/strategies/
risk_management.py::compute_position_size`, `DEFAULT_REFERENCE_EQUITY`,
and every strategy module's own sizing logic are **untouched** — a
strategy's actual position-sizing dynamics are completely unchanged by
this task. Making sizing equity-aware is a bigger, more invasive change
(it touches how every strategy computes quantity, not just when the
engine stops accepting new fills) and remains a separate, undone, open
direction.

## The exact algorithm implemented

`backtest.engine.run_backtest` gains one new **keyword-only** parameter:

```python
def run_backtest(
    klines: list[Kline],
    strategy: Strategy,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    *,
    starting_equity: Decimal | None = None,
) -> BacktestResult:
```

Keyword-only deliberately: several real call sites in this codebase pass
`fee_bps`/`slippage_bps` positionally, so a positional `starting_equity`
could silently collide with a future positional argument at some call
site. `starting_equity=None` (the default) is structured to be a true,
structural no-op — see "Primary regression guarantee" below.

When `starting_equity` is supplied:

1. Validated `> 0`, else `raise ValueError(f"starting_equity must be
   positive, got {starting_equity}")` — the exact message format
   `metrics.metrics.compute_metrics`'s own identical check already uses.
2. A `metrics.position.PositionTracker` is constructed (no
   `funding_rates` — `run_backtest` has never been funding-aware, and
   this feature does not change that) and reused directly, not
   hand-rolled a second time.
3. At the **top** of each loop iteration `i` (before calling `strategy`
   for that bar), if not already insolvent: every not-yet-applied fill
   with `fill.fill_time <= klines[i].open_time` is applied to the
   tracker (advancing an internal `fill_cursor`), then

   ```
   unrealized = 0 if tracker.position_qty == 0 else
                tracker.position_qty * (klines[i].close - tracker.avg_entry_price)
   equity = starting_equity + tracker.realized_pnl - tracker.cumulative_fees + unrealized
   ```

   is compared against `Decimal("0")`. This is the exact same formula,
   in the exact same per-bar catch-up-then-mark-to-market order, that
   `metrics.metrics.build_equity_curve` already uses downstream — not a
   reinvented computation.
4. If `equity <= 0`: this is the insolvency point. `insolvent_at_index`
   is recorded (the first time only — never overwritten once set) and a
   permanent `insolvent = True` flag is set. A real liquidated account
   does not un-liquidate itself on a later bar's favorable
   mark-to-market, so once triggered this state never resets, even if a
   later bar's naive mark-to-market would show a recovery.
5. `strategy(visible)` is **still called every remaining bar**, insolvent
   or not — this preserves a stateful strategy's own internal
   state-machine continuity (several real strategies, e.g.
   `ofi_momentum.py`, are stateful via closures). What changes once
   `insolvent` is `True`: whatever `OrderIntent` the strategy returns is
   **silently discarded** — `simulate_fill` is never called for it, and
   nothing is appended to `fills`/`filled_intents`.

`BacktestResult` gains one new additive field, `insolvent_at_index: int
| None = None` — the 0-based index into the `klines` list passed to
`run_backtest` at which equity first reached the floor, or `None` if it
never did (including whenever the feature wasn't enabled at all). No
existing `BacktestResult(...)` construction site in this codebase names
this field (confirmed by grep before writing this task — there is
exactly one construction site anywhere, `run_backtest`'s own `return`
statement), so the additive default is a real, verified no-op for every
pre-existing reader.

## Why the floor is hardcoded at exactly zero, not a tunable parameter

Deliberate, not an oversight. A non-zero (maintenance-margin-based)
floor would need a real margin-rate input this engine has no source for
anywhere — no strategy module, config, or data pipeline in this
codebase carries a margin rate. Adding a tunable knob here without that
real input would just be inventing a number, and would open a real
margin-modeling design question ("what maintenance margin rate, sourced
from where, updated how often") this task is not trying to answer.
Zero is the one floor that needs no external input to justify: below it,
`equity <= 0` means the tracked reconstruction shows the account can no
longer cover its own tracked losses, full stop.

## Why no liquidation fill is synthesized

Also deliberate. Synthesizing a realistic liquidation fill would need
real, undesigned decisions about price (last close? some worse
slippage-adjusted price?), timing (same bar, next bar?), and whose
"fault" the timing is (the engine's, for not checking sooner? the
strategy's, for not exiting?) — none of which this task attempts to
answer. Instead, the already-open position (if any) at the insolvency
point is simply left alone by the engine — exactly like any other
position still open at the end of a run. `metrics.metrics.
build_equity_curve`'s existing final-bar force-close logic (unchanged by
this task) handles it downstream, the same way it already handles every
other still-open-at-the-end position. A real, practical consequence
worth naming precisely: because the position isn't force-closed *at* the
insolvency point, and the market can keep moving against it all the way
to the final bar, `compute_metrics`'s downstream `final_equity` can still
read far more negative than the equity level that actually triggered
insolvency — the fix bounds how much *new* exposure a strategy can add
after going insolvent, not the eventual reported magnitude of the loss
on whatever position was already open when it did. This is intentional,
not a bug: see "Verification" below for a real example of this exact
shape.

## The real layering change, named rather than buried

Enabling this feature makes `backtest.engine` import and use
`metrics.position.PositionTracker` — **inverting this module's
previously clean layering**. Before this task, `backtest/` never
imported anything from `metrics/`; the dependency only ever ran the
other way (`metrics/` consuming `backtest/`'s `BacktestResult` output).
With `starting_equity` supplied, `backtest/` now optionally depends on
`metrics/` for this one feature. No circular import results —
`metrics.position` imports only from `backtest.fill` and
`schemas.order_intent`, never from `backtest.engine`, confirmed by
direct inspection before writing any code and re-confirmed by actually
importing `backtest.engine` after the change (`python -c "import
backtest.engine"` succeeds cleanly) — but the boundary itself has moved,
and `run_backtest`'s own docstring says so explicitly rather than
silently updating its "not a P&L/portfolio backtester" claim without
flagging that. `run_backtest` is still fundamentally a fill simulator:
Sharpe, drawdown, profit factor, and the full equity *curve* remain
exclusively `metrics.metrics`'s job, computed downstream and unchanged
by this feature. Reusing `PositionTracker` rather than hand-rolling a
second, parallel P&L tracker was a deliberate choice, not a
convenience — a second tracker with even a slightly different rounding
or ordering convention than `build_equity_curve`'s own would have made
the engine's internal insolvency check and the downstream reported
equity curve two independently-computed numbers that could silently
drift apart from each other over time. See "Verification" below for the
real test that checks the two agree.

## Call sites updated

Two real production call sites now thread `starting_equity` all the way
into `run_backtest`, not only into `compute_metrics` as before:

- `python/research/walkforward.py::run_walk_forward` — the
  `run_backtest(validate_klines, bound_strategy, fee_bps, slippage_bps)`
  call (per-fold, inside the fold loop) now also passes
  `starting_equity=starting_equity`. Each fold already gets an
  independent, non-compounding-across-folds `starting_equity` (matching
  how `compute_metrics` already treats folds independently), so this
  requires no other change to fold semantics — the insolvency floor is
  simply now enforced during each fold's own fill simulation, not only
  reported after the fact.
- `python/research/run_preregistered_holdout.py::run_preregistered_holdout`
  — the `run_backtest(klines, bound_strategy, prereg.fee_bps,
  prereg.slippage_bps)` call now also passes
  `starting_equity=starting_equity` (already a parameter of the
  enclosing function, defaulting to `_DEFAULT_STARTING_EQUITY`).

`python/research/run_preregistered.py` was checked and confirmed to have
**no separate `run_backtest` call site of its own** — it delegates
entirely to `research.walkforward.run_walk_forward` (via `run_kwargs`
and a direct call, not re-passing `starting_equity` explicitly, so it
inherits `run_walk_forward`'s own default), so it automatically benefits
from this fix with zero additional code change, confirmed by reading the
module directly rather than assumed.

`python/research/robustness.py::check_parameter_sensitivity` has its own
`run_backtest(train_klines, bound_strategy, fee_bps, slippage_bps)` call
site (used only by the opt-in, diagnostic-only parameter-sensitivity
overfitting check) — deliberately **not** touched by this task, matching
the brief's explicit scope. It stays at `starting_equity=None`, i.e.
unaffected, same as before this task.

## What's explicitly out of scope, restated

- `research/strategies/risk_management.py` (`compute_position_size`,
  `DEFAULT_REFERENCE_EQUITY`) — completely untouched. Sizing logic is
  unchanged by this task; "equity-compounding sizing" remains a
  separate, different, undone direction.
- `metrics/metrics.py`/`metrics/position.py` — `PositionTracker` reused
  exactly as it already existed; no changes made or needed there.
- Every strategy module (`vwap_mid_reversion.py`, `ofi_momentum.py`,
  `daily_tsmom_ensemble.py`, `hourly_momentum.py`,
  `regime_momentum_risk_managed.py`, `mean_reversion.py`,
  `ma_crossover.py`, and every other `research/strategies/*.py` module)
  — zero strategy code changes.
- `runs/experiments.jsonl`, `.planning/scalp-s4-*`,
  `.planning/scalp-s6-*` — the two already-logged INCONCLUSIVE scalping
  holdout results are historical, immutable, spent single-access
  records. Neither holdout was re-run, and neither file was touched by
  this task. This task adds engine capability for *future* runs; it does
  not re-litigate either past result.
- `research/robustness.py`'s own `run_backtest` call (parameter-
  sensitivity diagnostic path) — see above.

## Tests

New dedicated test module, `python/tests/test_engine_insolvency_floor.py`
(11 tests), covering:

- `starting_equity` is genuinely keyword-only with a `None` default
  (structural check via `inspect.signature`, not just a claim).
- A strategy that never approaches insolvency produces **identical**
  `fills`/`filled_intents` whether `starting_equity` is supplied (a
  comfortably large value) or omitted — proves the feature is a true
  no-op when it never triggers.
- A hand-built fixture, mirroring `vwap-mid-reversion.py`'s own real
  shape (opens a position, never emits a signal that closes it), against
  a hand-crafted price path (`open == close` per bar, so equity is
  exactly hand-verifiable): confirms fills stop being produced once
  equity crosses the zero floor, `insolvent_at_index` is set to the
  correct bar, the strategy callable is still invoked every remaining
  bar (call counter) but produces no further fills, and the flag stays
  permanently set even when a later bar's price would show a naive
  "recovery" (bars 3-5 of the fixture jump back to a strongly positive
  price; `insolvent_at_index` stays at bar 2 regardless, and the recovery
  bars are never even re-checked).
- Boundary: a fill that brings equity to **exactly** `Decimal("0")`
  triggers insolvency (not only strictly negative) — and, as the mirror
  case, a fill that leaves equity at exactly `Decimal("1")` (one unit
  short of zero) does **not** trigger it.
- `starting_equity=Decimal("0")` and two distinct negative values all
  raise `ValueError` with the documented message (parametrized).
- Cross-layer consistency: for the same insolvency-triggering fixture,
  `metrics.metrics.compute_metrics` is called on the exact
  `filled_intents`/`fills` `run_backtest` actually produced, and its
  `equity_curve[insolvent_at_index]` is confirmed `<= Decimal("0")` —
  the engine's internal check and the downstream reported equity curve
  agree with each other on the same fills, not two independently
  computed numbers that could silently drift apart.
- A direct `BacktestResult()` construction (no existing call site does
  this anywhere in the codebase, confirmed by grep) still defaults
  `insolvent_at_index` to `None`, proving the additive-field guarantee
  the task brief calls out explicitly.

Two further integration tests, one per production call site, added to
the existing per-module test files (not a new file, matching this
project's established one-test-file-per-module convention):

- `python/tests/test_walkforward.py::
  test_run_walk_forward_threads_starting_equity_into_run_backtest_bounding_a_runaway_fold`
  — a `TrainableStrategy` fixture (`_RepeatedLosingRoundTripStrategy`)
  that opens and closes a fixed-size position every bar against a market
  that only ever moves against it (mirroring the real repeated-round-trip
  mechanism behind both `vwap-mid-reversion`'s and `ofi-momentum`'s real
  catastrophic results, not the single-runaway-position shape used in the
  dedicated engine-level tests above), run through the real
  `run_walk_forward` path. Confirms the fold's own
  `backtest_result.insolvent_at_index` is set and its fill count is
  capped far below one-per-bar, then independently recomputes what the
  *same* strategy/klines would have produced **without** the floor
  (`run_backtest(..., starting_equity=None)` + `compute_metrics` at the
  same `starting_equity`) and confirms that unbounded reference is
  dramatically more negative (`> Decimal("20000")` worse) than the
  bounded fold's own `final_equity` — proving the bounded result is
  bounded *because of* the fix, not a coincidence of the fixture.
- `python/tests/test_run_preregistered_holdout.py::
  test_run_preregistered_holdout_threads_starting_equity_into_run_backtest_bounding_a_runaway_result`
  — the identical fixture and comparison, driven through
  `run_preregistered_holdout`'s own `strategy=`/`klines=` test-injection
  path instead of a real database, confirming the same threading and the
  same bounded-vs-unbounded contrast through the holdout runner
  specifically.

## Test results

Full suite (`cd /mnt/c/Dev/trading-engine/python && .venv/bin/python -m
pytest tests/ -q`): **1550 passed** (up from **1535** before this task —
the last recorded count, PR #115). Zero pre-existing test files had
their *assertions* modified — the only changes to pre-existing test
files were the new tests and fixture/import changes described above and
in "Real CodeRabbit review findings" below (the two integration tests
appended to `test_walkforward.py`/`test_run_preregistered_holdout.py`,
the two new logging-behavior tests, and the fixture consolidation into
`insolvency_fixtures.py`), plus the new dedicated `test_engine_
insolvency_floor.py`. The full, unmodified-assertion suite passing is
this task's own primary regression guarantee for the `starting_equity=
None` no-op claim — not merely asserted, actually run (first at 1548,
confirmed again at 1550 after the review-driven fixes below added two
more tests).

## Real CodeRabbit review findings on this PR, and how each was handled

Six actionable findings on the initial PR, each verified against the
actual code before deciding fix vs. decline — two declined with a
written rebuttal (not silently ignored), four fixed for real.

**Declined 1 (Major) — "`kline.close` gates same-bar orders on
unavailable data, a same-candle lookahead."** The reviewer's own claim:
using `klines[i].close` inside the insolvency check at the top of
iteration `i` (before calling `strategy` for bar `i`) blocks bar `i`'s
own order using a price "not yet known" at bar `i`'s start, and argued
`build_equity_curve` using the identical formula is fine only because
it's a post-hoc report, not a live gating decision. **Investigated and
declined, not silently overridden**: this codebase's own established
lookahead-safety model, stated explicitly in three independent places,
treats bar `i`'s *entire* OHLC — close included — as legitimately
visible the moment bar `i` is the "current" bar: (1)
`backtest.kline_window.KlineWindow`'s own guarantee is that nothing can
observe `klines[length]` or beyond — a window of length `i+1` includes
index `i` (bar `i`) in full, there is no partial-`Kline`
open-only-visibility concept anywhere in this codebase, a `Kline` is
always seen whole or not at all; (2) `backtest.fill.simulate_fill`'s own
docstring: intents are "generated while looking at `klines[:
signal_bar_index + 1]`" (bar `i` fully included) and fill "against the
*next* bar only... (no same-candle **execution**)" — the restriction is
on execution *price*, never on decision-time *visibility*; (3) CLAUDE.md's
own Strategy Research Methodology states the rule directly: "a strategy
is only ever shown bars up to and including the current one" — the
current bar, including its close, is exactly what "up to and including"
means. The strategy's own `OrderIntent` for bar `i` is *already* computed
using `klines[i].close` (via `visible[-1].close`) before the insolvency
gate ever runs — so the gate re-reading the exact same, already-legitimate
data the strategy itself just used is not a new lookahead source, it's
the same information at the same point in the loop. Switching to
`kline.open` (the suggested fix) would not remove a lookahead risk; it
would introduce a *new* inconsistency — gating a decision computed from
bar `i`'s close against a stale, bar-`i`-open equity snapshot that lags
the very information the decision was based on. This is also the exact
formula the task brief explicitly required mirroring from
`metrics.metrics.build_equity_curve` (itself unchanged, long-shipped,
and behind every Sharpe/drawdown/PSR/DSR figure this project has ever
logged) — not a new pattern this PR invented. Responded on the PR with
this full reasoning rather than applying the suggested diff.

**Declined 2 (Minor) — "funding P&L should be applied to the insolvency
gate in `run_walk_forward`."** Real and correctly diagnosed (a
funding-inclusive fold's engine-level insolvency check today evaluates
price-only equity, not funding-adjusted equity), but **directly
contradicts this task's own explicit, deliberate brief**: "Do not pass
any `funding_rates` to the `PositionTracker()` you construct here...
`run_backtest` has never had funding awareness (that's `metrics.py`-only,
opt-in there) and this task does not add it." Implementing the suggested
change would silently widen this task's own scope boundary rather than
respect it. Declined the code change; **disclosed instead** — a new
comment block next to `research/walkforward.py`'s own
`_DEFAULT_STARTING_EQUITY` (see "Call sites updated" above) names the gap
precisely and notes it has zero practical effect today (no currently-
registered scalping candidate uses `funding_included: true`).

**Fixed 3 (Major) — `insolvent_at_index` was computed but never logged
into the holdout confirmation record.** Real, valid gap: a holdout is a
single, un-repeatable access (`research.holdout`'s own single-access
enforcement), so a low-`num_trades` result with no recorded
`insolvent_at_index` would leave a future reader unable to tell "the
strategy rarely signaled" apart from "the circuit breaker cut the run
short" — permanently, since the access can't be repeated to find out.
Fixed for real, not just at the log line the finding pointed at:
`_log_holdout_confirmation` gained an optional `insolvent_at_index`
parameter, included in `aggregate_metrics` only when not `None`
(matching this file's own established "field only appears when the
feature is actually used" convention, e.g. `funding_pnl_included`); the
in-memory `HoldoutConfirmationResult` dataclass — not named in the
reviewer's own suggested diff, but the same real gap — also gained the
field, so a caller doesn't have to re-read the log to learn a fact the
function it just called already computed. Two new tests
(`test_run_preregistered_holdout_logs_insolvent_at_index_when_triggered`,
`..._omits_insolvent_at_index_when_never_triggered`) prove both the
presence-when-triggered and field-omitted-not-null-when-never-triggered
cases against the real logged record, not just the in-memory result.

**Fixed 4 (Minor) — stale docstring cross-reference.**
`test_engine_insolvency_floor.py`'s module docstring referenced
`test_full_suite_passes_unmodified_confirms_the_byte_for_byte_no_op_claim`,
a test renamed to `test_starting_equity_is_keyword_only_with_a_none_default`
during writing but left un-updated in the docstring's own reference.
Corrected.

**Fixed 5 (Trivial nitpick) — duplicated fixtures across the two
integration test files.** `_crashing_klines` and the repeated-round-trip
strategy double existed as two independent, identically-behaved copies in
`test_walkforward.py` and `test_run_preregistered_holdout.py`. Real risk
named correctly: the two copies could silently drift apart over a future
edit, leaving both integration tests claiming to prove the same circuit
breaker while actually exercising two different scenarios, with nothing
surfacing the divergence. Consolidated into a new shared module,
`python/tests/insolvency_fixtures.py` (`crashing_klines`,
`RepeatedLosingRoundTripStrategy`), following this test suite's existing
`fake_*_server.py` precedent for a shared, non-`test_`-prefixed helper
module — imported by both test files as `tests.insolvency_fixtures`,
matching how `fake_binance_server.py` is already imported elsewhere in
this suite.

**Fixed 6 (Trivial nitpick) — hardcoded fold-boundary slice literals in
the walk-forward integration test.** `klines[2:202]`/`klines[:2]` were
correct only because they happened to match `train_bars=2`/
`validate_bars=200` by construction; a future change to
`generate_folds`' own boundary arithmetic would not fail this test, it
would silently compare the bounded fold result against a *different*
window than the fold actually used. Replaced with
`klines[fold.train_start_index : fold.train_end_index]`/
`klines[fold.validate_start_index : fold.validate_end_index]`, derived
from the real `Fold` object the test already had in scope — the two can
no longer diverge.

Full suite re-run after all four fixes: **1550 passed** (see "Test
results" above for the final, authoritative count and its derivation).

## Files created/modified

- `python/backtest/engine.py` — `BacktestResult` gains `insolvent_at_index`;
  `run_backtest` gains keyword-only `starting_equity`, the insolvency-
  floor algorithm, and a rewritten, honest docstring naming the layering
  change.
- `python/research/walkforward.py` — `run_walk_forward`'s per-fold
  `run_backtest` call now also passes `starting_equity=starting_equity`;
  a comment added near `_DEFAULT_STARTING_EQUITY` explaining the dual use
  and (added on review) disclosing the funding-P&L gap named in "Declined
  2" below.
- `python/research/run_preregistered_holdout.py` — same one-line change
  at its own `run_backtest` call site, same comment pattern near its own
  `_DEFAULT_STARTING_EQUITY`; `_log_holdout_confirmation` and
  `HoldoutConfirmationResult` both gained `insolvent_at_index` (added on
  review, "Fixed 3" below).
- `python/tests/test_engine_insolvency_floor.py` — new, 11 tests.
- `python/tests/insolvency_fixtures.py` — new (added on review, "Fixed 5"
  below): `crashing_klines`/`RepeatedLosingRoundTripStrategy`, shared by
  both integration test files.
- `python/tests/test_walkforward.py` — one new integration test, now
  importing its fixtures from `insolvency_fixtures.py` and deriving its
  reference window from the real `Fold` object rather than hardcoded
  slice literals (review fixes 5 and 6).
- `python/tests/test_run_preregistered_holdout.py` — one new integration
  test plus two new tests proving `insolvent_at_index` is logged when
  triggered and omitted when not (review fix 3), all importing their
  shared fixtures from `insolvency_fixtures.py` (review fix 5).
- `.planning/scalp-s7-backtest-insolvency-floor.md` — this document.

## What this does and does not resolve, restated

This resolves only the **circuit-breaker** half of the open question
both `scalp-s4` and `scalp-s6` result write-ups disclosed. It does not:
make any strategy's sizing equity-aware (a strategy still sizes every
trade against a fixed reference constant, exactly as before); synthesize
a realistic liquidation fill or otherwise change what a still-open
position's *final reported* loss can look like once the engine stops it
from growing further; revisit either already-spent scalping holdout
result; or decide anything about a third scalping candidate. All of
those remain open, separate, undecided questions for future work.
