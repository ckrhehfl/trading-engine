# Paper-trading bridge, Task D: daily reporting

## Scope note

This is **Task D** of the 5-task paper-trading bridge plan governing
`daily-tsmom-ensemble`'s human-approved move to paper trading (see
CLAUDE.md's "Paper Trading Policy Exception" and
`.claude/plans/tender-finding-matsumoto.md`, the governing plan -- same
reference convention `.planning/paper-trading-b-signal-runner.md` and
`.planning/paper-trading-c-scheduler-entrypoint.md` already use). Depends
on Task C (`PaperTradingApp`, merged, PR #71) -- this task extends that
same running process, it does not build anything standalone. R3-risk
component (`java/runtime`) -- TDD discipline applied throughout, per
CLAUDE.md's Development Methodology.

## What was built

Two files modified, three new:

- **`TradingLoop.java`** (modified) -- gained two small, read-only
  accessors for `DailyReportGenerator` to consume: `fillHistory()` (every
  `Fill` this loop has ever applied, in order, as an unbounded in-memory
  list -- appended to inside the existing `applyFills` method) and
  `killSwitchTripped()` (delegates to the loop's own `KillSwitch
  .isTripped()`). No other behavior changed.
- **`DailyReport.java`** (new) -- a record: `date`, `startingEquity`,
  `endingEquity`, `trades` (`List<Fill>`), `errors`
  (`List<DailyReport.TickError>`), `killSwitchTripped`, `ticksAttempted`,
  `ticksSucceeded`, `uptimeFraction`. `TickError` is a nested record
  (`occurredAt`, `message`).
- **`DailyReportGenerator.java`** (new) -- the actual UTC-day-boundary
  detector and report writer. `beforeTick()`/`afterTick()` bracket every
  real `TradingLoop.tick()` call; see "Design questions" below for the
  full mechanics.
- **`PaperTradingApp.java`** (modified) -- wires a `DailyReportGenerator`
  alongside the existing `TradingLoop`; `runTick()` (now package-private,
  not `private`) calls `beforeTick()` / `tradingLoop.tick()` /
  `afterTick()` in that order; two new constructor overloads (`Path
  reportsDirectory` public, `Clock clock` package-private/test-only);
  new `PAPER_TRADING_REPORTS_DIR` env var, `resolveReportsDirectory`
  helper; new package-private `dailyReportGenerator()` test accessor.
- **`DailyReportGeneratorTest.java`** (new) -- 6 tests, listed under
  "TDD" below.

No `build.gradle.kts` change was needed -- `DailyReport`/
`DailyReportGenerator` only need what `:runtime` already depends on
(`engine.execution.Fill`, `engine.schemas.SchemaObjectMapper`, Jackson,
SLF4J).

## Design questions (from this task's own brief)

### 1. How does a "day boundary" get detected in a process that ticks every 5 minutes?

**Checked on every tick, via a small stateful accumulator
(`DailyReportGenerator`), not a separate scheduled task.** Concretely:
`beforeTick()` computes `LocalDate.ofInstant(clock.instant(),
ZoneOffset.UTC)` ("today") and compares it against the day this
generator is currently tracking (`currentDay`). Three cases:

- **First call ever** (`currentDay == null`): just seeds tracking state
  (`dayStartEquity`, a fill-count baseline, zero'd tick counters) --
  there is no prior day to finalize yet.
- **`today` has advanced past `currentDay`**: a boundary was crossed.
  Finalizes and writes the report for `currentDay` (the day that just
  ended) using state as it stood at this exact instant, then re-seeds
  tracking state for `today`.
- **`today` has NOT advanced** (the common case, ~288 times/day at the
  5-minute default interval): no-op.

**Why `beforeTick()`, not a separate scheduled task, and why the check
runs *before* `tradingLoop.tick()` rather than after:** `TradingLoop`
itself has no timer -- `tick()` is "the entire unit of work, meant to be
invoked repeatedly by an external scheduler" (its own class Javadoc).
`DailyReportGenerator` follows the identical shape rather than
introducing a second `ScheduledExecutorService` alongside
`PaperTradingApp`'s existing one purely to poll a clock -- one scheduler,
one cadence, is simpler and there's no requirement anywhere that day-
boundary detection run on a different cadence than the trading tick
itself. Running the check in `beforeTick()` (i.e. before the upcoming
`tick()` call) rather than in `afterTick()` matters for correctness, not
just style: if the boundary were detected *after* `tick()` ran, that
tick's own price/fill/error effects -- which chronologically belong to
the *new* day -- would already be baked into `TradingLoop`'s state
(`currentEquity()`, `fillHistory()`, `lastError()`) by the time the
"outgoing" day's report is built, misattributing them to the day that
just ended. Detecting the boundary first and finalizing the outgoing
day's report using state *as it stood at that instant* -- strictly
before the new day's first tick has any chance to run -- is what keeps
each day's report an honest slice of exactly that day's activity.

Concretely, `PaperTradingApp.runTick()` (now package-private, reused by
both the real scheduler and tests) is:

```java
void runTick() {
    dailyReportGenerator.beforeTick();
    tradingLoop.tick();
    dailyReportGenerator.afterTick();
    ...existing logging...
}
```

### 2. What happens on a restart mid-day?

**The report covers activity from the actual process (re)start, not the
true UTC day start -- explicitly documented as a known limitation, not
silently under-delivered.** Neither `TradingLoop` nor the new
`DailyReportGenerator` persists any state (`TradingLoop`'s own class
Javadoc already says so: "This class does not assume any prior state...
'start clean' is the only state there is" -- same story Task C already
established and disclosed). A restart mid-day therefore produces a
*new* `DailyReportGenerator` instance whose very first `beforeTick()`
call seeds `dayStartEquity` from whatever `TradingLoop.currentEquity()`
reads **at that moment** -- which, from this generator's own
perspective, is indistinguishable from the calendar day simply starting
late. Trading activity from earlier in that same UTC day, before the
restart, is lost along with the prior process's `TradingLoop` instance,
and will not appear in the eventual report for that day.

This is directly, explicitly tested --
`aFreshGeneratorSeedsDayStartEquityFromWhateverTheTradingLoopReadsAtConstructionNotTrueMidnight`
manually sets a `TradingLoop`'s equity to a non-default value (simulating
"trading already happened earlier today, in a now-gone prior process")
*before* constructing a fresh `DailyReportGenerator` well into the middle
of the day, then confirms the eventual report's `startingEquity` reflects
that restart-moment value, not the untraceable "true" start-of-day value.

A second, related limitation, also disclosed rather than silently
accepted: **a day during which the process never ran at all produces no
report for that day.** Nothing calls `beforeTick()`/`afterTick()` for a
day the process is down the whole time, so no boundary-crossing check
ever happens for it. Both limitations trace to the same root cause
(no durable state) as `FileSignalSource`'s own already-disclosed
cross-restart dedup caveat and Task C's own "no OS-level process
supervision" scope cut -- fixing either would need real state
persistence, which does not exist anywhere in this codebase yet
(`OrderStore`/`PaperBroker` are both in-memory only too). Not attempted
here; out of this task's scope per its own brief.

### 3. Where does "trades executed that day" come from?

**Neither `TradingLoop` nor `OrderPipeline`/`PaperBroker` exposed a
queryable fill history before this task** -- checked directly:
`TradingLoop.applyFills` previously only ever *subtracted* each fill's
fee from `equity` and discarded the `Fill` object itself; `PaperBroker
.pendingOrders()` only shows orders still awaiting a fill, not a fill
history; `OrderStore` tracks `Order` state transitions, not `Fill`
records. So this task added `TradingLoop.fillHistory()`: an unbounded,
in-memory `List<Fill>` appended to inside the existing (already-
`synchronized`) `applyFills` method, exposed as an immutable snapshot
copy.

`DailyReportGenerator` doesn't need `Fill` timestamps to attribute trades
to a day -- it tracks `fillHistory().size()` as a baseline when a day
starts, and reports everything from that index onward as "this day's
trades" when the day ends. This works because the list only ever grows
(fills are never removed), so a later `size()` minus the baseline is
exactly that day's slice, cheaply and without needing to parse/compare
each `Fill#filledAt()` against day boundaries separately. Tested directly
by `tradesFilledDuringTheDayAppearInThatDaysReportInApplicationOrder`.

### 4. How are "errors caught during the day" tracked?

**A small accumulator, fed by `PaperTradingApp` (via `DailyReportGenerator
.afterTick()`) as errors occur -- exactly the option this task's own
brief named as acceptable.** `TradingLoop.lastError()` only ever exposes
the single most recent tick's outcome, confirmed by reading `tick()`'s
own `finally`/catch-all: `lastError` is overwritten (to `null` on success,
to the caught exception on failure) every single tick, with no history
kept anywhere in `TradingLoop`. `DailyReportGenerator.afterTick()` --
called immediately after every real `tick()` -- reads `lastError()` at
that moment and, if non-null, appends a `DailyReport.TickError(
tradingLoop.lastTickAt(), error.toString())` to a day-scoped list;
`afterTick()` also increments `ticksAttempted` (every call) and
`ticksSucceeded` (only on a null `lastError()`), which is what
`uptimeFraction` (`ticksSucceeded / ticksAttempted`, scale 6,
`HALF_UP`) is computed from -- the only uptime signal derivable from
what `TradingLoop` already tracks (`lastTickAt()`/`lastError()`), per
this task's own brief. Nothing here required touching `TradingLoop`
itself for errors (unlike trades, which needed `fillHistory()`) --
`lastError()` alone, read once per tick from the outside, was
sufficient.

Directly tested by
`aDayWhereEveryTickFailsStillProducesAReportRatherThanSilentlySkippingTheWrite`:
forces every tick to fail (a fake BingX server returning HTTP 500) for
an entire simulated day and confirms the resulting report has one
`TickError` per failed tick, `ticksSucceeded=0`, `uptimeFraction=0`, and
-- critically -- still gets written at all, not silently skipped because
"nothing succeeded."

## Critical property: a quiet day still produces a report

Directly tested by
`crossingADayBoundaryWithNoSignalsOrErrorsStillWritesAZeroActivityReportForTheDayThatEnded`:
three ticks with a `DummySignalSource` that never fires, real
price-feed calls that all succeed, no kill-switch trip -- confirms the
report file for that day still gets written, with `trades: []`,
`errors: []`, `killSwitchTripped: false`, `ticksAttempted: 3`,
`ticksSucceeded: 3`, `uptimeFraction: 1.000000`, and
`startingEquity == endingEquity == 100000`. `DailyReportGenerator`'s
write path has no early-return/skip branch conditioned on "was anything
interesting" -- `beforeTick()` unconditionally builds and writes a
report the instant a boundary is detected, regardless of what
accumulated during the day. An empty day and a missing day are
structurally different outcomes in this design, not two names for the
same thing.

## Report path convention

`var/live/reports/daily/<date>.json` (`date` is `LocalDate.toString()`,
i.e. exactly `YYYY-MM-DD`), resolved relative to the JVM's working
directory when the `PAPER_TRADING_REPORTS_DIR` env var is unset --
mirrors Task C's `PAPER_TRADING_SIGNAL_PATH`/`resolveSignalPath`
convention byte-for-byte (`resolveReportsDirectory`, same null/blank ->
default fallback shape), which itself mirrors
`python/live/generate_daily_signal.py`'s own default-path convention.
`var/live/` is already gitignored (Task B), so no change needed there.
Writes are atomic: a `.tmp` file is written first, then moved into place
via `Files.move(..., ATOMIC_MOVE, REPLACE_EXISTING)` -- the same
`.tmp`-then-rename idiom `generate_daily_signal.py` already uses for its
own signal file (via Python's `os.replace()`), so a reader never
observes a half-written report.

## Write-failure handling

A failure while writing the report file (e.g. a disk error) is caught,
logged at `ERROR` (not `WARN` -- "no missing daily reports" is a hard
Paper Trading Pass Criterion, so a silent write failure here is exactly
the failure mode that criterion exists to catch, and it needs to be
maximally visible in logs), and swallowed -- never rethrown. This matches
`TradingLoop.tick()`'s own "never propagate out of a scheduled cycle"
contract: a report-write failure must not crash the scheduler any more
than a bad tick should.

## TDD

`DailyReportGeneratorTest.java` was written and confirmed to fail to
compile (`cannot find symbol: class DailyReportGenerator` /
`class DailyReport`, 22 real compile errors) before either production
class existed. After writing `DailyReport.java` and
`DailyReportGenerator.java`, all 6 tests passed on the first run (no
red-then-green cycle was needed on the logic itself, since the classes
were designed directly against the already-written tests) --
`./gradlew :runtime:test --tests DailyReportGeneratorTest`: **6 tests,
0 failures, 0 errors**, confirmed via the real JUnit XML report (test
names: `theFirstEverBeforeTickDoesNotWriteAReportAndOnlySeedsDayTrackingState`,
`crossingADayBoundaryWithNoSignalsOrErrorsStillWritesAZeroActivityReportForTheDayThatEnded`,
`tradesFilledDuringTheDayAppearInThatDaysReportInApplicationOrder`,
`aDayWhereEveryTickFailsStillProducesAReportRatherThanSilentlySkippingTheWrite`,
`killSwitchTrippedStateAtDayEndIsReflectedInThatDaysReport`,
`aFreshGeneratorSeedsDayStartEquityFromWhateverTheTradingLoopReadsAtConstructionNotTrueMidnight`).

`PaperTradingAppTest`'s existing 13 tests and `TradingLoopTest`'s
existing 7 tests were re-run unmodified after the `PaperTradingApp`/
`TradingLoop` changes and stayed green -- the 4-arg `PaperTradingApp`
constructor those tests call is untouched (it now delegates to a new
5-arg overload with a default `reportsDirectory`, itself delegating to a
new package-private 6-arg overload with a default `Clock.systemUTC()`),
and `TradingLoop`'s two new accessors are additive only.

Full suite after all changes (`./gradlew clean test`, all six `java/`
modules): **193 tests, 0 failures, 0 errors** -- up from 187 after Task
C (187 + 6 new `DailyReportGeneratorTest` = 193).

## Real local run: actual output

Ran a throwaway JUnit test (`ManualDailyReportShakedownRun`, not
committed -- deleted immediately after this output was captured, same
precedent as Task C's own shakedown run) that constructed a real
`PaperTradingApp` against the real, live, public BingX VST demo host
(`https://open-api-vst.bingx.com` -- no credentials, public market data
only), using the package-private `Clock`-injecting constructor overload
to control day-boundary timing without waiting on real wall-clock time.
Sequence: two quiet ticks on 2026-08-07, then a real signal file written
(`GUARDED_MARKET`, fills immediately), a third tick that picks it up and
fills against a real live BingX VST price, then the clock advanced past
midnight UTC into 2026-08-08 and a fourth tick -- which must finalize
and write 2026-08-07's report before doing anything with the new day.

Actual captured output (one value -- `client_order_id` inside the JSON
block only -- is shown as `<intent_id above>` rather than repeated
literally a second time: gitleaks' `generic-api-key` rule matches on the
`client_order_id` key name (it contains `client`, a keyword the rule
treats the same as `api`/`token`/`secret`) combined with a high-entropy
UUID value in `"key" : "value"` shape, and flags it as a possible
credential every time, regardless of which real UUID fills that slot --
confirmed by generating two different real UUIDs via two separate real
runs and having both flagged identically. It is not a secret -- the
identical value is already fully visible, unredacted, in the plain-text
log lines directly above (`wrote intent_id=...`, `order ... -> FILLED`,
`order ... filled at ...`), which don't match the rule's `"key" :
"value"` shape and were never flagged. Elided here only to keep this
real commit passing the repo's own local `gitleaks protect` pre-commit
hook without weakening or reconfiguring it):

```text
=== DAY 1: two quiet ticks (no signal file yet) ===
[Test worker] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T14:13:45.996942327Z equity=100000
[Test worker] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T14:13:46.406742351Z equity=100000
after day-1 ticks: lastTickAt=2026-08-07T14:13:46.406742351Z equity=100000 lastError=null
=== writing a real signal (GUARDED_MARKET, fills immediately) ===
wrote intent_id=f4fefd22-d3ed-4cab-b367-e5c682690b4a
=== DAY 1: one more tick, should pick up and fill the real signal ===
[Test worker] INFO engine.risk.RiskGateway - order f4fefd22-d3ed-4cab-b367-e5c682690b4a approved: quantity=0.001
[Test worker] INFO engine.oms.Order - order f4fefd22-d3ed-4cab-b367-e5c682690b4a -> NEW
[Test worker] INFO engine.oms.Order - order f4fefd22-d3ed-4cab-b367-e5c682690b4a -> SUBMITTED
[Test worker] INFO engine.oms.Order - order f4fefd22-d3ed-4cab-b367-e5c682690b4a -> ACKNOWLEDGED
[Test worker] INFO engine.oms.Order - order f4fefd22-d3ed-4cab-b367-e5c682690b4a -> FILLED
[Test worker] INFO engine.execution.PaperBroker - order f4fefd22-d3ed-4cab-b367-e5c682690b4a filled at 64883.17404 (fee=0.03244158702)
[Test worker] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T14:13:47.269916522Z equity=99999.96755841298
after real fill tick: lastTickAt=2026-08-07T14:13:47.269916522Z equity=99999.96755841298 lastError=null fillHistorySize=1
=== CROSSING INTO DAY 2 (2026-08-08) -- must finalize+write 2026-08-07's report ===
[Test worker] INFO engine.runtime.DailyReportGenerator - wrote daily report for 2026-08-07 to /tmp/junit-4810365698026477288/reports/2026-08-07.json: trades=1 errors=0 ticksAttempted=3 ticksSucceeded=3 killSwitchTripped=false
[Test worker] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T14:13:47.754432394Z equity=99999.96755841298
report file exists: true at /tmp/junit-4810365698026477288/reports/2026-08-07.json
=== REPORT FILE CONTENTS ===
{
  "date" : "2026-08-07",
  "starting_equity" : 100000,
  "ending_equity" : 99999.96755841298,
  "trades" : [ {
    "client_order_id" : "<intent_id above>",
    "symbol" : "BTC-USDT",
    "price" : 64883.17404,
    "quantity" : 0.001,
    "notional" : 64.88317404,
    "fee" : 0.03244158702,
    "filled_at" : "2026-08-07T14:13:47.269659494Z"
  } ],
  "errors" : [ ],
  "kill_switch_tripped" : false,
  "ticks_attempted" : 3,
  "ticks_succeeded" : 3,
  "uptime_fraction" : 1.000000
}
```

This confirms, against real components and a real network call to a
real live exchange: the report is written exactly on the day-boundary
tick, not before (no file existed for `2026-08-07` until the fourth
tick, which was already dated `2026-08-08`); it correctly captured all
three of day 1's ticks (`ticks_attempted: 3`, all succeeded); it
correctly captured the one real fill (`trades` has exactly one entry,
matching the real `PaperBroker` fill log line's own price/fee exactly);
and `ending_equity` reflects exactly that fill's fee subtracted from
the starting 100000. This is one real, short run exercising the exact
mechanics this task's tests already exercise synthetically -- it adds
"this also works through the real `PaperTradingApp` wiring, against a
real exchange, not just against a hand-assembled `TradingLoop`" as
independent evidence, matching Task C's own shakedown run's stated
scope and caveats (real network I/O, a real fill, real scheduling logic
-- not a demonstration of multi-day resilience).

## Judgment calls

- **`runTick()` changed from `private` to package-private**, purely a
  visibility widening (no behavior change) so tests/manual-run harnesses
  can drive a full tick+report cycle directly, mirroring the existing
  `tradingLoop()` test-accessor precedent from Task C.
- **Two new `PaperTradingApp` constructor overloads, not a single
  breaking signature change.** The original 4-arg public constructor is
  untouched (still delegates through to a default `reportsDirectory` and
  `Clock.systemUTC()`), so none of Task C's 13 existing
  `PaperTradingAppTest` tests needed any edits -- per CLAUDE.md's "touch
  only what the task requires," this avoided rewriting tests that had
  nothing to do with reporting. The new 5-arg public overload
  (`Path reportsDirectory`) is real production API (an operator may want
  to point reports somewhere other than the default); the 6-arg
  overload (`Clock clock`) is package-private and test/manual-run-only,
  same status as `tradingLoop()`.
- **`uptimeFraction` is `ticksSucceeded / ticksAttempted`, not derived
  from wall-clock coverage** (e.g. "5-minute ticks expected, how many
  were actually observed"). `TradingLoop` exposes no notion of
  "expected" tick cadence, and inferring one would mean
  `DailyReportGenerator` either hardcoding the scheduler's own interval
  (duplicating a fact `PaperTradingApp` already owns) or being handed it
  explicitly for a marginal precision gain. Counting observed
  success/failure per tick is what `lastTickAt()`/`lastError()` actually
  support today, per this task's own brief ("build on that rather than
  inventing new instrumentation from nothing").
- **`DailyReport`/`TickError` are plain Java records serialized via the
  existing `SchemaObjectMapper` conventions** (snake_case, ISO-8601
  timestamps), for consistency with every other JSON artifact in this
  codebase -- but they are NOT added to the `engine.schemas` module or
  `schemas/fixtures/`. They are a Java-side-only operational reporting
  artifact (nothing in Python ever needs to parse a daily report per the
  governing plan), unlike `OrderIntent`/`RiskDecision`, which are real
  cross-language wire types covered by `SchemaCompatTest`. Living in
  `engine.runtime` alongside `DailyReportGenerator` reflects that.
- **No "paper score" computation anywhere in this task** -- per the
  governing plan's own explicit "deliberately NOT defined now" decision
  and this task's brief's "Explicitly out of scope." `DailyReport` is
  raw ingredients only.
- **Did not touch `RiskGateway`, `OrderPipeline`, `KillSwitch`, or
  `OrderStore` internals** -- confirmed by reading this diff: the only
  cross-class additions are two new read-only accessor methods on
  `TradingLoop` (`fillHistory()`, `killSwitchTripped()`), which read
  existing state rather than adding any new behavior to any of those
  four classes.
- **Did not touch Task E's territory.** Task E (minimal internal
  reconciliation, parallel/independent per the governing plan) is not
  started here, and this task's diff does not add or modify anything
  that looks like reconciliation logic -- no `Position` class, no
  `OrderStore`/`PaperBroker` cross-check. The two `TradingLoop`
  accessors added here (`fillHistory()`, `killSwitchTripped()`) are
  generic, read-only state exposure that a reconciliation task could
  plausibly also want to read from -- flagged here in case Task E's own
  implementation benefits from reusing them rather than adding
  duplicate accessors, but nothing about them is reconciliation-specific
  and no coordination was needed to add them.

## CodeRabbit review response (PR #73)

The first real review (state `CHANGES_REQUESTED`, targeting commit
`76e764d`) raised 4 actionable findings. Each was individually assessed
against this codebase's own actual scope/scale rather than blindly
autofixed, per CLAUDE.md's "review and apply the fix manually" guidance
for Java runtime code:

1. **Major -- write failures discarded the completed report** (`DailyReportGenerator`
   line ~215-219): valid, real bug. Pre-fix, `beforeTick()` called
   `writeReport(...)` and then unconditionally `startNewDay(...)`
   regardless of whether the write succeeded -- a single transient disk
   error on exactly a day-boundary tick would silently and permanently
   lose that day's entire report. **Fixed**: `writeReport` now returns a
   success `boolean`; a report that fails to write is kept in a new
   in-memory `pendingReports` queue (oldest first) and retried at the
   start of every subsequent `beforeTick()` call -- not only at the next
   day boundary -- until it actually succeeds. Day tracking still
   advances immediately regardless of write success (a broken write path
   must not stall day-boundary detection itself, only that one day's
   report delivery). New test:
   `aReportThatFailsToWriteIsRetriedOnALaterTickRatherThanDiscarded`
   forces a real `IOException` (not a mock -- this codebase has none) by
   pointing `reportsDirectory` under a path whose parent already exists
   as a plain file, confirms the report is retained (`pendingReportCount()
   == 1`) and NOT written, then removes the obstruction and confirms a
   later ordinary same-day tick (no new boundary) retries and succeeds
   with the ORIGINAL day's data intact.
2. **Major -- a day that ends between the last tick and process shutdown
   was never reported** (`PaperTradingApp` line ~283-286): valid, real
   gap. Day-boundary detection only ever ran inside `beforeTick()`, which
   only runs immediately before a real `tick()` -- so if `stop()` is
   called after a UTC day has already ended but before the next
   scheduled tick would have noticed, nothing was left to ever detect
   that boundary, and the day's report would never be written at all.
   **Fixed**: new `DailyReportGenerator.finalizeCompletedDayOnShutdown()`
   -- the same boundary-check-and-write logic `beforeTick()` already had,
   callable without requiring an actual tick to accompany it -- wired
   into `PaperTradingApp.stop()`, called after the executor has fully
   stopped. Idempotent (safe to call more than once, matching `stop()`'s
   own existing idempotency) and a safe no-op if nothing was ever
   tracked (e.g. stopped before the first tick). New tests:
   `finalizeCompletedDayOnShutdownWritesADayThatEndedBeforeTheNextTickWouldHaveNoticed`
   and `finalizeCompletedDayOnShutdownIsANoOpWhenNothingWasEverTracked`
   in `DailyReportGeneratorTest` (the isolated mechanism), plus
   `stopFinalizesADayThatEndedBeforeTheNextScheduledTickWouldHaveNoticed`
   in `PaperTradingAppTest` (proving the real end-to-end wiring through
   `app.stop()`).
3. **Major -- `TradingLoop.fillHistory()` grows unboundedly for the
   process's lifetime** (`TradingLoop` line 119): a real, generically-
   valid observation, but **not implemented** as a code fix -- a
   deliberate judgment call, not an oversight. CodeRabbit's own suggested
   fix (a "consume unreported fills, retain unwritten-report-only fills"
   handoff) would require `TradingLoop` to learn about report write
   success/failure, reopening exactly the day-boundary/report coupling
   this task's own design deliberately keeps out of `TradingLoop` (see
   "Trade attribution" above -- `DailyReportGenerator` alone owns any
   notion of "day," `TradingLoop` does not, and CLAUDE.md's Development
   Methodology says touch only what the task requires). At this
   project's actual current scale the risk is negligible, not
   theoretical-but-real: the only strategy with a paper-trading policy
   exception (`daily-tsmom-ensemble`) is a daily-bar, single-symbol
   strategy producing at most a handful of fills per day, evaluated over
   the Paper Trading Pass Criteria's own 30-45 day window -- tens of
   `Fill` records over the entire evaluation, not thousands, each a
   small record (a UUID, a symbol string, four `BigDecimal`s, an
   `Instant`). Disclosed explicitly, not silently dropped: a new
   Javadoc paragraph on `TradingLoop` ("Unbounded growth, considered and
   deliberately not addressed here") names the CodeRabbit finding, the
   rejected fix and why, and the condition under which to revisit
   (a materially higher-frequency strategy or materially longer-lived
   process than this project's current scope) -- plus a reply posted
   directly on that review comment explaining the same reasoning, so the
   decision is visible to both CodeRabbit and any future human reader,
   not silently resolved.
4. **Trivial -- the trade-order test only checked count, not order**
   (`DailyReportGeneratorTest` line ~194): valid, cheap, no design risk.
   **Fixed**: `tradesFilledDuringTheDayAppearInThatDaysReportInApplicationOrder`
   now captures `loop.fillHistory()` before the boundary crossing and
   asserts `report.trades()` matches it `clientOrderId`-by-`clientOrderId`
   at every index, not just in count -- a report that silently reordered
   trades would now fail this test.

Full suite after these fixes (`./gradlew clean test`): **197 tests, 0
failures, 0 errors** -- up from 193 (3 new `DailyReportGeneratorTest` +
1 new `PaperTradingAppTest`, plus the 1 existing test strengthened for
finding 4).

### Second review round (commit `2ff03bb`, state `CHANGES_REQUESTED` again)

Pushing the fix-up commit above triggered a second, real, full review
(verified against the reviews API, not just the status check -- the
status check briefly showed "pass" for an interim reply-only review
before the actual full review of the new diff landed a few minutes
later). It raised 3 more findings against the round-1 fixes themselves:

1. **Major -- a newly-completed report could write out of order ahead of
   an older, still-pending one** (`DailyReportGenerator`, the round-1
   `enqueueAndAttemptWrite`): valid, real bug in the round-1 fix itself.
   That method attempted a direct write of the newly-completed report
   FIRST, only falling back to the pending queue if that direct attempt
   failed -- so if an older report was already stuck in the queue (e.g.
   from an earlier still-unresolved failure) while the newer report's own
   target path happened to be independently writable, the newer one could
   get written before the older one, breaking this class's own promised
   chronological delivery order. **Fixed**: `enqueueAndAttemptWrite` is
   gone; every completed report is now unconditionally enqueued first
   (`pendingReports.addLast(...)`), and `flushPendingReports()` -- which
   drains strictly oldest-first, stopping at the first still-failing
   report -- is now the ONLY method that ever calls `writeReport`, called
   unconditionally at the end of every `beforeTick()`/
   `finalizeCompletedDayOnShutdown()` call. New test:
   `aStillPendingOlderReportBlocksAYoungerOneFromWritingOutOfOrder`
   obstructs only 2026-08-07's own target file (a directory sitting where
   that one file needs to go) while leaving 2026-08-08's own future
   target path completely normal, and confirms 2026-08-08's report stays
   queued rather than writing ahead of the stuck 2026-08-07 one --
   `pendingReportCount() == 2`, `2026-08-08.json` does not exist -- until
   the obstruction clears and both write, in order.
2. **Major -- `stop()` could finalize while a straggling tick was still
   in flight** (`PaperTradingApp`, outside the diff range):
   `ExecutorService.shutdownNow()` attempts to interrupt an in-flight
   task but does not guarantee it actually stops before the call
   returns; the round-1 `stop()` called
   `finalizeCompletedDayOnShutdown()` unconditionally right after
   `shutdownNow()`, with no confirmation the straggling tick had really
   ended. A tick still running concurrently could reach its own
   `afterTick()` on either side of the finalize, either silently missing
   from the finalized report or getting counted against day-tracking
   state that had already reset. **Fixed**: `stop()` now performs a
   second, shorter (5s) `awaitTermination` after `shutdownNow()` and only
   calls `finalizeCompletedDayOnShutdown()` if termination is actually
   confirmed; if it can't be confirmed, finalization is skipped entirely
   and logged at `ERROR` -- a day staying unwritten in this rare case is
   strictly safer than risking a wrong one. **Not accompanied by a new
   automated test**, disclosed rather than silently skipped: reliably
   forcing this exact race requires a task that genuinely hangs past a
   real 10s+5s timeout, which would mean either a slow/flaky ~15-second
   test or injectable shutdown-timeout durations added purely for
   testability (no mocking framework exists in this codebase to fake
   `ExecutorService` termination behavior otherwise). Given
   CodeRabbit's own "Heavy lift" effort label on this finding and this
   project's standing "touch only what the task requires" guidance, the
   correctness fix was made (it is unambiguously more correct and
   strictly safer than before, and costs nothing at runtime) but the
   matching slow/complex test infrastructure was not built for this task
   -- flagged here explicitly as a real, disclosed test gap rather than
   silently passed over.
3. **Major -- durable, restart-recoverable persistence for
   `pendingReports`** (`DailyReportGenerator`): a generically valid
   suggestion, **declined as out of scope**, not implemented. This asks
   for exactly the kind of durable cross-restart persistence that
   nothing in this codebase has anywhere yet (`OrderStore`/`PaperBroker`
   are both in-memory only, `FileSignalSource`'s own dedup pointer
   doesn't survive a restart either -- all already-disclosed, pre-
   existing precedents, not oversights specific to this task). Building
   one is real, scoped follow-on work if this project's restart-
   persistence story is ever revisited wholesale, not a piece to bolt
   onto one queue in one class as a drive-by addition. The in-memory-only
   limitation this finding is about was already explicitly disclosed in
   round 1's own Javadoc addition (`DailyReportGenerator`'s "Write
   failures never propagate..." section: "a process restart while a
   report is still pending loses it, same as any other unwritten state
   this class holds") -- the class Javadoc was extended further to name
   this specific finding and the reasoning for declining it, and a reply
   was posted on the review thread pointing back to that existing
   disclosure rather than treating it as newly discovered.

Finding 3 from the first round (`fillHistory` unbounded growth) was
independently withdrawn by CodeRabbit itself after reading the round-1
Javadoc disclosure and reply (`review_comment_withdrawn`, 2026-08-07):
*"확인했습니다. 현재 paper-trading 범위에서는 `fillHistory`의 무제한 보관이 실질적인
운영 위험이 되지 않습니다... 이 finding은 철회합니다"* ("Confirmed. At the current
paper-trading scope, `fillHistory`'s unbounded retention is not a
practical operational risk... withdrawing this finding") -- independent
confirmation the round-1 judgment call was sound, not just asserted by
this task's own author.

Full suite after the second round's fixes (`./gradlew clean test`):
**198 tests, 0 failures, 0 errors** -- up from 197 (1 new
`DailyReportGeneratorTest`).

### Merging Task E (PR #72) and a third review round

Between the second round's fixes and this section, Task E ("minimal
internal reconciliation between OrderStore and PaperBroker", the
governing plan's own parallel-with-D task) merged to `main` as PR #72
-- confirmed via `gh pr view 73 --json mergeable` reporting
`CONFLICTING` once that landed, since Task E independently modified the
exact same two files this task's own core change touches
(`TradingLoop.java`: adds `submittedOrderIds()`/tracking, orthogonal to
this task's `fillHistory()`/`killSwitchTripped()`; `PaperTradingApp.java`:
adds `reconcile()`/`OrderStore`/`KillSwitch` fields and a
`runTick()`-tail call, orthogonal to this task's `beforeTick()`/
`afterTick()` wiring and `stop()` changes). Resolved via a real `git
merge origin/main` (not a rebase, to avoid a force-push) -- both
`TradingLoop.java` conflict hunks (class Javadoc, one field
declaration, one accessor-method block) and `PaperTradingApp.java`'s
four conflict hunks (class Javadoc, field declarations, the
`tradingLoop`/`dailyReportGenerator` construction lines, and the
trailing test accessors) were resolved by keeping both tasks' additions
side-by-side, since none of Task E's changes and this task's own
changes actually overlap in *behavior*, only in *file location* --
`runTick()` itself merged cleanly with no conflict marker at all
(`beforeTick() -> tick() -> afterTick() -> log -> reconcile()`, exactly
the right combined order: daily-report bookkeeping and reconciliation
both need to observe the tick's real outcome, and neither depends on
the other's result). `PaperTradingAppTest.java`'s own conflict (both
tasks' new tests were inserted at the identical point in the file, right
after `startCannotBeCalledTwice`) needed a full manual reconstruction
rather than a hunk-by-hunk resolution, since the two sides' dangling
brace boundaries were textually ambiguous to `git merge`'s own 3-way
algorithm -- resolved by writing out the complete, correctly-ordered
file directly (MutableClock class, this task's `stopFinalizes...` test,
then Task E's three reconciliation tests) rather than trusting the
conflict markers' exact split points. `TradingLoopTest.java` had no
conflict at all -- this task never touched that file.

Full suite after the merge (`./gradlew clean test`): **211 tests, 0
failures, 0 errors** across all six `java/` modules -- 198 (this task's
own) + 13 (Task E's: 8 new `ReconcilerTest`, 2 new `TradingLoopTest`, 3
new `PaperTradingAppTest`), confirming the merge is not just
syntactically valid but semantically correct: both tasks' functionality
and every one of their own tests pass together.

**Considered, and deliberately not done as part of this merge: making
the daily report also surface `PaperTradingApp.reconcile()`'s own
`ReconciliationReport`** (e.g. a `reconciliationClean`/mismatch-count
field on `DailyReport`). This would be a real, plausible improvement --
"zero position mismatches" is itself one of CLAUDE.md's Paper Trading
Pass Criteria, and a reconciliation-aware daily report would make that
criterion directly auditable from the same artifact as everything else.
Not implemented here because: (1) it is genuinely new scope beyond
either task's own governing brief -- Task D's brief asked for equity,
trades, errors, kill-switch state, and uptime; Task E's own reporting
is `lastReconciliationReport()`, a separate, already-complete accessor,
not something Task D's brief asked this class to absorb; (2) the
`Reconciler`/`ReconciliationReport` types live in `engine.runtime`
alongside this task's own classes (no import boundary would need
crossing), so this is a real design option, not blocked by anything
structural -- which is exactly why it deserves its own `Discuss` pass
rather than a drive-by addition made only because a merge happened to
put both classes in front of the same session; and (3) `DailyReportGenerator`
already has a real, working, independently-useful contract today
(`beforeTick()`/`afterTick()`, called once per tick, building a report
from `TradingLoop`-only state) -- adding a `PaperTradingApp`-level
dependency (reconciliation only exists on `PaperTradingApp`, not on
`TradingLoop`) would mean either passing `ReconciliationReport` in from
outside (a `DailyReportGenerator` API change) or having
`DailyReportGenerator` reach up into `PaperTradingApp` (an inverted,
backwards dependency this class's whole design avoids elsewhere). Flagged
here, not silently dropped, exactly per CLAUDE.md's "flag pre-existing
[or newly-surfaced] scope instead of grabbing it unasked" spirit -- a
future task (most naturally scoped as its own small follow-up, not
folded into either D or E after the fact) can pick this up with a real
`Discuss` pass on the two design options in (3) above.

A **third, real, full CodeRabbit review** landed on the merge commit
(`2c59507`, state `CHANGES_REQUESTED`) with 2 more findings, both fixed:

1. **Major, Quick win -- no `AtomicMoveNotSupportedException` fallback.**
   `Files.move(tmp, target, ATOMIC_MOVE, REPLACE_EXISTING)` was the only
   move attempted; on a filesystem that doesn't support atomic move at
   all (some network mounts, certain cross-volume setups), that
   condition never changes between retries, so a report would fail this
   exact way forever rather than actually recovering via the pending-
   queue retry mechanism round 1 built. **Fixed**: catches
   `AtomicMoveNotSupportedException` specifically, logs a warning (the
   guarantee is genuinely weaker without atomicity -- a reader could in
   principle observe a moment where neither file exists -- so this is
   disclosed, not silent), and retries with a plain `REPLACE_EXISTING`
   move. Existing `IOException` handling (log at ERROR, return `false`,
   let the caller's pending-queue retry take over) is unchanged for
   failures of either move attempt. Not accompanied by a new dedicated
   test -- forcing a real `AtomicMoveNotSupportedException` deterministically
   needs a filesystem/mount configuration this test environment doesn't
   have (unlike the earlier write-failure tests, which use an ordinary
   file-vs-directory conflict any filesystem raises identically); the
   existing `DailyReportGeneratorTest` suite continues to exercise the
   normal `ATOMIC_MOVE` path and the ordinary-`IOException` fallback
   path, both unaffected by this change.
2. **Nitpick -- markdownlint MD029 (ordered-list prefix consistency).**
   This document's own "Second review round" subsection restarted its
   findings list at "5." (continuing round 1's own "1.-4." numbering
   across two visually-separated lists) rather than starting its own
   list fresh at "1." -- fixed by renumbering that list to "1.-3." (with
   the one cross-reference to it in this document's own "Verification"
   section updated to match).

Full suite after the third round's fix (`./gradlew clean test`): **211
tests, 0 failures, 0 errors** (unchanged count from the merge above --
this round's only code change was the `AtomicMoveNotSupportedException`
fallback, which adds no new test).

### Fourth review round (commit `48cd8a2`, `CHANGES_REQUESTED` again)

One new finding, plus two re-raised from round 2 -- this round CodeRabbit
pushed back harder on both re-raised ones rather than accepting the
round-2 disclosures as final. Each handled on its own terms:

1. **Major, Quick win, NEW -- the round-3 `AtomicMoveNotSupportedException`
   fallback had no dedicated test, and (per CodeRabbit's own cited,
   verified Java 21 `Files.move` documentation) pre-creating the target
   file doesn't reliably force that path anyway.** Checked, not assumed:
   `Files.move`'s own Javadoc states that under `ATOMIC_MOVE`,
   `REPLACE_EXISTING` is ignored, and whether an existing target is
   replaced or rejected is implementation-specific. A real, throwaway
   probe against this project's own dev/CI environment confirmed the
   practical consequence directly: moving an existing plain target file
   under `ATOMIC_MOVE` + `REPLACE_EXISTING` **succeeds** here (not an
   assumption -- observed output: `move succeeded; target now contains:
   new`), so a test that merely pre-creates the target would pass for
   the wrong reason (the atomic path itself, never touching the
   fallback) rather than actually exercising the fallback -- exactly the
   gap CodeRabbit's finding named. **Fixed two ways**: (a) also catch
   `FileAlreadyExistsException` alongside `AtomicMoveNotSupportedException`,
   since Java's own docs name it as the implementation-specific existing-
   target failure mode; (b) the real fix for testability -- a new
   package-private `AtomicMover` functional-interface seam (same
   pattern, same justification, as the existing `Clock` injection
   overload): production code defaults to the real `Files.move`, and a
   new 4-arg test-only constructor overload lets a test supply a mover
   that deterministically throws on demand. New test
   `anAtomicMoveFailureFallsBackToANonAtomicReplaceAndStillCompletesTheWrite`
   forces the atomic attempt to fail unconditionally and confirms the
   real (non-injected) non-atomic fallback move actually completes the
   write, with the original day's data intact.
2. **Major, Heavy lift, RE-RAISED -- `stop()`'s shutdown-termination-
   confirmation logic (round-2 finding 2) still has no deterministic
   test.** CodeRabbit's round-4 ask is specific and goes beyond
   documentation: inject the `ExecutorService` or the termination-wait
   durations, and deterministically prove both (a) finalization doesn't
   run before an in-flight tick actually terminates, and (b)
   finalization is skipped when termination can't be confirmed.
   **Still not built, on the same "Heavy lift" cost/benefit judgment as
   round 2, now made concrete rather than asserted**: building this for
   real needs injectable `Duration` timeouts on `PaperTradingApp`
   (another test-only constructor overload) paired with a way to make a
   real tick block deterministically past a short configured timeout --
   `BingXPriceFeed` already has a real 10s `HttpRequest` timeout
   (checked directly in its source), so the only portable way to force
   a *longer*-than-configured hang without relying on OS/network-
   specific behavior is a fake server that never responds, combined with
   a short enough injected timeout to keep the test fast. This is
   buildable, but is real additional production surface (two more
   timeout parameters threaded through `PaperTradingApp`'s constructor
   chain) for a class whose actual logic is already conservative and
   fail-safe by construction (skips finalization, logs at `ERROR`,
   rather than risking a wrong report, exactly when termination can't be
   confirmed) -- the code path is safe by design even though it isn't
   yet proven by an automated test. **This is the point CodeRabbit is
   pressing on and this task is not resolving unilaterally**: its own
   finding text says to either build the fix or get explicit human
   approval to accept the gap. Flagged explicitly for the human
   reviewing this PR, not re-declined a second time on this task's own
   authority alone.
3. **Major, Heavy lift, RE-RAISED -- durable, restart-recoverable
   persistence for `pendingReports` (round-2 finding 3).** CodeRabbit's
   round-4 wording is now explicit that a documentation-only decline is
   not sufficient for this PR: "do not leave the restart-loss limitation
   merely documented or declined... if this limitation is genuinely
   meant to be out of scope, state the possibility of pending-report
   loss before restart in the PR objective and operational limitations,
   and get explicit human approval." **Still declined on this task's own
   authority, for the same reasons as round 2** (this codebase has no
   durable cross-restart persistence anywhere -- `OrderStore`/
   `PaperBroker` are both in-memory only, `FileSignalSource`'s own
   dedup doesn't survive a restart either -- and CodeRabbit's own round-2
   withdrawal of the analogous `fillHistory` finding, after running real
   verification against this exact codebase, already confirms that
   reasoning holds). **The explicit-human-approval ask itself is
   surfaced here, not silently assumed granted**: this exception --
   accepting that a report can be permanently lost if the process
   restarts while that report is still queued after a write failure --
   needs the human reviewing this PR to actually see and approve it, not
   an unstated assumption. Both this and finding 2 above are called out
   together in this task's final report back, precisely because they're
   the two places CodeRabbit is asking for a decision only a human can
   make for this project, not something this task should decide alone.

Full suite after this round's fix (`./gradlew clean test`): **212 tests,
0 failures, 0 errors** (211 + 1 new `DailyReportGeneratorTest`).

### Fifth review round (commit `1916cc9`, `CHANGES_REQUESTED` again)

One small, trivial finding -- the two human-decision-flagged items from
round 4 were not re-raised, consistent with CodeRabbit's own "does not
re-review already reviewed... commits" incremental-review note:

1. **Trivial -- the new round-4 test never confirmed the injected
   `AtomicMover` was actually invoked.** `anAtomicMoveFailureFallsBackToANonAtomicReplaceAndStillCompletesTheWrite`
   asserted only the end state (file exists, `pendingReportCount() == 0`)
   -- a real gap, since a bug that skipped the atomic attempt entirely
   and went straight to the fallback would have passed the test
   identically. **Fixed**: the injected lambda now increments an
   `AtomicInteger` on every call, and the test asserts exactly one
   invocation before asserting the end state, confirming the atomic path
   was genuinely attempted and genuinely failed, not merely that things
   turned out fine some other way.

Full suite after this round's fix (`./gradlew clean test`): **212 tests,
0 failures, 0 errors** (unchanged count -- this round strengthened an
existing test's assertions rather than adding a new one).

**Also fixed, unrelated to CodeRabbit**: the local `.githooks/pre-commit`
hook (`gitleaks protect --staged`) flagged this very document's own real
local-run JSON output as a `generic-api-key` false positive -- gitleaks'
default rule matches the `client_order_id` key name (it contains
`client`, a keyword the rule treats the same as `api`/`token`/`secret`)
combined with any sufficiently high-entropy value in `"key" : "value"`
JSON shape, confirmed by generating two different real UUIDs via two
separate real runs and having both flagged identically -- not a fluke of
one unlucky value. A `.gitleaksignore` entry was tried first (the
standard gitleaks mechanism for exactly this) and confirmed, via direct
testing, to work for `gitleaks detect` but NOT for `gitleaks protect
--staged` (a real, version-specific gitleaks limitation, gitleaks 8.16.0
-- confirmed empirically, not assumed) -- so it was removed again rather
than left as a dead, misleading entry. The actual fix: the real JSON
block's `client_order_id` value is elided to `<intent_id above>` (with an
inline explanation of why) rather than repeated a second time in the one
shape that trips the rule -- the identical, real, unredacted value is
still fully visible in the plain-text log lines directly above it in the
same output, which don't match the rule's shape and were never flagged.
No security tooling was reconfigured or weakened to get past this; the
only change is which of two already-real, already-shown renderings of
the same value appears in the doc.

## Verification

- `./gradlew :runtime:compileTestJava` against `DailyReportGeneratorTest`
  failed with 22 real "cannot find symbol" compile errors
  (`DailyReportGenerator`, `DailyReport`) before either class existed.
- `./gradlew :runtime:test --tests DailyReportGeneratorTest` -- 11/11
  pass (6 from the original TDD pass + 3 responding to round-1 findings
  1 and 2 + 1 responding to round-2 finding 1 + 1 responding to round-4
  finding 1).
- `./gradlew :runtime:test --tests PaperTradingAppTest` -- 17/17 pass
  (13 unmodified from Task C + 1 responding to round-1 finding 2 + 3
  from Task E, merged in -- see "Merging Task E" above).
- `./gradlew :runtime:test --tests TradingLoopTest` -- 9/9 pass (7
  unmodified from Task C + 2 from Task E, merged in).
- `./gradlew :runtime:test --tests ReconcilerTest` -- 8/8 pass (Task E's
  own test file, untouched by this task, merged in).
- `./gradlew clean test` (full multi-module suite, after merging Task E
  and all four review rounds' fixes) -- **212 tests, 0 failures, 0
  errors** across all six `java/` modules.
- Real local run against the real BingX VST endpoint, through a real
  simulated day boundary, with a real fill -- see above; actual report
  file contents shown, not asserted.
- Local `gitleaks protect --staged` pre-commit hook passes clean (see
  "Also fixed, unrelated to CodeRabbit" above).
- Four real, full CodeRabbit reviews obtained and responded to, each
  verified via the GitHub reviews API against the exact HEAD sha at the
  time -- not just a green status check, which was repeatedly misleading
  (rate-limited-but-green after PR open; a reply-only review transiently
  showing "pass" before a real full review actually landed; a stale
  `APPROVED` review auto-`DISMISSED` by GitHub once the Task E merge
  commit was pushed, exactly as expected -- an approval is only ever
  valid for the exact sha it was submitted against). Round 1 (commit
  `76e764d`, `CHANGES_REQUESTED`, 4 findings), round 2 (commit
  `2ff03bb`, `CHANGES_REQUESTED`, 3 more findings against the round-1
  fixes themselves), round 3 (commit `2c59507`, the post-merge commit,
  `CHANGES_REQUESTED`, 2 more findings), and round 4 (commit `48cd8a2`,
  `CHANGES_REQUESTED`, 1 new finding fixed plus 2 re-raised findings
  handled as explicit human-decision flags, not re-declined
  unilaterally) are all addressed above.

**Two items require human review-time input, not resolved by this task
on its own authority** (both from round 4, "Fourth review round"
above): (1) `PaperTradingApp.stop()`'s shutdown-termination-confirmation
logic has no deterministic automated test proving the concurrency
invariant it's designed around -- the logic itself is conservative and
fail-safe by construction, but CodeRabbit is asking for either real test
coverage or an explicit accept of that gap; (2) `DailyReportGenerator
.pendingReports` is in-memory-only, so a process restart while a report
is queued after a write failure loses that report permanently --
CodeRabbit is asking for either a durable outbox or an explicit accept
of that gap. Both declines are technically defensible on this task's own
stated reasoning (matches this codebase's existing in-memory-only
precedents, and CodeRabbit itself independently verified and withdrew
the closely analogous `fillHistory` finding in round 2), but a
CodeRabbit reviewer insisting twice on the same R3-risk-adjacent gap is
exactly the situation CLAUDE.md's Auto-merge Policy already reserves for
a human decision, not an LLM agent's own authority.

- PR opened, not merged -- per the governing plan and CLAUDE.md's
  Auto-merge Policy, this is Java runtime code (extends `TradingLoop`
  and `PaperTradingApp`, both R3-risk-adjacent) and requires explicit
  human sign-off regardless of CI/CodeRabbit status.
