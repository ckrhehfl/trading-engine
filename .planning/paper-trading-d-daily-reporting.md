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
- `./gradlew :runtime:test --tests DailyReportGeneratorTest` -- 9/9 pass
  (6 from the original TDD pass + 3 added responding to CodeRabbit
  findings 1 and 2).
- `./gradlew :runtime:test --tests PaperTradingAppTest` -- 14/14 pass
  (13 unmodified from Task C + 1 added responding to CodeRabbit finding 2).
- `./gradlew :runtime:test --tests TradingLoopTest` -- 7/7 pass
  (unmodified from Task C).
- `./gradlew clean test` (full multi-module suite) -- **197 tests, 0
  failures, 0 errors** across all six `java/` modules.
- Real local run against the real BingX VST endpoint, through a real
  simulated day boundary, with a real fill -- see above; actual report
  file contents shown, not asserted.
- Local `gitleaks protect --staged` pre-commit hook passes clean (see
  "Also fixed, unrelated to CodeRabbit" above).
- A real CodeRabbit review (state `CHANGES_REQUESTED`, verified via the
  GitHub reviews API against the exact HEAD sha, not just a green status
  check -- the status check alone was misleadingly green while rate-
  limited) was obtained and responded to; a second review is expected
  after the fix-up commit, per CLAUDE.md's "batch fixes into one push
  before requesting re-review" guidance.
- PR opened, not merged -- per the governing plan and CLAUDE.md's
  Auto-merge Policy, this is Java runtime code (extends `TradingLoop`
  and `PaperTradingApp`, both R3-risk-adjacent) and requires explicit
  human sign-off regardless of CI/CodeRabbit status.
