# GitHub issue #74: deterministic test for `PaperTradingApp.stop()`'s shutdown-termination-confirmation logic

## Scope note

Closes GitHub issue #74, itself a round-4 CodeRabbit finding on PR #73
(Paper Trading Bridge Task D — daily reporting) that was accepted at the
time (2026-08-08) as a disclosed gap rather than fixed on the spot: the
finding's own text said to either build the real test or get explicit
human approval to decline it, and #74 is that explicit tracking (same
precedent as issues #58/#64/#70). R3-risk component (`java/runtime`) —
TDD discipline applied throughout, per CLAUDE.md's Development Methodology.

**Explicitly out of scope, per this task's own brief**: `stop()`'s actual
behavior/logic is unchanged — this task only adds real test coverage
proving the existing, already-correct logic, plus the minimal
constructor-injection surface needed to make it testable.
`DailyReportGenerator`, `Reconciler`, `VstPreflight` were not touched.

## What was built

1. A new package-private, test-only `PaperTradingApp` constructor overload
   accepting two `Duration` parameters (`gracefulShutdownTimeout`,
   `forcedShutdownTimeout`) — matching the existing `Clock`-injection
   overload's exact shape and status ("production defaults everywhere
   else, only this overload lets a test override it"). `stop()` itself
   is otherwise byte-for-byte the same control flow: `executor.shutdown()`
   → `awaitTermination(gracefulShutdownTimeout)` → if not terminated,
   `executor.shutdownNow()` → `awaitTermination(forcedShutdownTimeout)` →
   finalize iff terminated, else log `ERROR` and skip. The two new
   `Duration`s replace the previous hardcoded `10`/`TimeUnit.SECONDS` and
   `5`/`TimeUnit.SECONDS` literals; every other constructor (the 4-arg,
   5-arg, 6-arg `Clock`-only, and 7-arg `Clock`+`OrderExecutor` overloads)
   still resolves to the exact same historical values via two new
   constants, `DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT = Duration.ofSeconds(10)`
   and `DEFAULT_FORCED_SHUTDOWN_TIMEOUT = Duration.ofSeconds(5)` — zero
   behavior change for every real (non-test) caller, including
   `forBingXVst`/`fromEnvironment`/`main`.

   The 7-arg `OrderExecutor`-accepting constructor (used by `forBingXVst`
   and by tests that inject a fake/real `OrderExecutor`) deliberately did
   **not** also grow a `Duration`-accepting variant — issue #74's own
   scope is the shutdown-confirmation invariant itself, and the two new
   hang tests don't need the `bingx-vst` execution-mode path at all (see
   "Why no prior successful tick is needed" below). Growing that
   constructor's parameter list too would have been unrequested surface
   area on a constructor several existing Task H tests already call.

2. `FakeBingXTradesServer#hangForever()` (test-only, `java/runtime/src/
   test`): switches the existing shared fake server (already used by
   `BingXPriceFeedTest`/`TradingLoopTest`/`PaperTradingAppTest`) into a
   mode where it still accepts the connection and still updates
   `lastPath()` (so a test can confirm a request genuinely arrived) but
   the handler thread then blocks forever (`new CountDownLatch(1)
   .await()`) instead of ever writing a response. The server's own
   handler executor was changed from the JDK's implicit default to an
   explicit daemon `Executors.newCachedThreadPool(...)` (set via
   `setExecutor()` before `start()`), unconditionally — not just in hang
   mode — so a permanently-blocked handler thread can never keep the test
   JVM alive; `close()` additionally calls `handlerExecutor.shutdownNow()`
   before `server.stop(0)` as an extra (defense-in-depth, not load-
   bearing given the daemon threads) cleanup step.

3. Two new tests in `PaperTradingAppTest`, both real, end-to-end,
   scheduler-driven (`app.start()`/`app.stop()`, not a manually-invoked
   `tick()`) — see "Both proof cases" below.

## Judgment calls

### Verifying the actual interrupt behavior empirically before designing the test, not assuming it

Issue #74's own text says the only portable way to force a tick to hang
past a configured timeout is a fake server that never responds, since
`BingXPriceFeed` already has a real, fixed 10s `HttpRequest` timeout.
That much is straightforward. What's *not* obvious in advance is what
happens when `stop()`'s own `executor.shutdownNow()` then tries to
interrupt the thread blocked inside that hung `HttpClient.send()` call —
if the JDK's blocking HTTP client doesn't respond to `Thread.interrupt()`
promptly (or at all), the "termination eventually confirmed" proof case
becomes flaky or impossible; if it responds *too* reliably, the "can't be
confirmed" proof case becomes hard to construct without racing.

This was checked directly rather than assumed (matching this project's
standing "checked, not assumed" convention — see CLAUDE.md's BingX
section for the same discipline applied elsewhere): a small throwaway
probe (`ServerSocket` that accepts but never writes, a worker thread
blocked in `HttpClient.send()`, `Thread.interrupt()` called from the main
thread after a real 300ms delay) confirmed `HttpClient.send()` throws
`InterruptedException` and unblocks in **~4ms** on this project's own dev
environment (JDK 21.0.11), consistently. This one empirical fact shaped
both proof cases below:

- **Proof case (a)** (finalization eventually runs) needs a
  `forcedShutdownTimeout` comfortably larger than that observed recovery
  time — 3 seconds was chosen, ~750x headroom over the observed ~4ms, to
  stay robust against a slower/loaded CI runner without making the test
  itself slow.
- **Proof case (b)** (finalization is skipped) needed a way to prove
  "termination not confirmed" that does **not** race against that same
  recovery time, since racing a real interrupt's completion against an
  arbitrary short timeout (e.g. "set the second wait to 10ms and hope the
  recovery takes longer") would be genuinely flaky on a slow/fast CI
  runner either direction. `Duration.ZERO` is the resolution: `ExecutorService
  #awaitTermination(0, ...)` (verified against `ThreadPoolExecutor`'s own
  implementation) checks current state and returns immediately without
  waiting at all. Calling `shutdownNow()` and then *immediately*, same
  thread, no yield in between, checking `awaitTermination(0, ...)` cannot
  possibly observe a state where the just-requested interrupt has already
  been scheduled, processed by a different thread, and fully unwound back
  through `TradingLoop.tick()`'s catch block and the rest of `runTick()`
  — there is no interleaving of the JVM's own thread scheduler that makes
  "recovery in the same instant as the check, with no scheduling gap at
  all" a real possibility. This makes proof case (b) deterministic by
  construction rather than by a timing margin.

### Why no prior successful tick is needed to seed a day boundary

The existing test `stopFinalizesADayThatEndedBeforeTheNextScheduledTick
WouldHaveNoticed` seeds a day's data via one real, successful, manually-
driven tick (`app.runTick()`) before advancing the clock. The two new
hang tests don't need that: `DailyReportGenerator.beforeTick()`'s very
first-ever call only *seeds* `currentDay` (no boundary to finalize yet)
— so the scheduler's own immediate (`delay=0`) first tick is exactly the
one that gets hung, and its `beforeTick()` call (which runs *before*
`tradingLoop.tick()` blocks) is enough to give `currentDay` a real,
non-null value. Advancing the injected `MutableClock` past midnight while
that first tick is still blocked, then calling `stop()`, exercises
exactly the same "a day ended and nothing but `stop()`'s own finalize
call will ever notice" scenario the existing test proves for the clean-
shutdown case — no second tick is ever scheduled during either new test's
short real-wall-clock lifetime (`tickIntervalSeconds=300` is used
specifically so no second real tick fires and races the first).

### Proving order, not just eventual outcome, for proof case (a)

"Finalization does not run before an in-flight tick actually terminates"
could be argued to follow automatically from `ExecutorService`'s own
`awaitTermination` contract (a `true` return is *defined* as "every task
has completed") — true, but the task asks for this deterministically
*proven*, not asserted from the JDK's own documentation. The strongest
direct evidence available without adding a new synchronization primitive
to production code: `DailyReportGenerator.afterTick()` is what populates
a day's `ticksAttempted`/`ticksSucceeded`/`errors` fields, and it is only
ever called (from `PaperTradingApp.runTick()`) *after* `TradingLoop.tick()`
itself has returned. `stopFinalizesOnlyAfterAGenuinelyInFlightTick
ActuallyTerminatesViaForcedShutdown` reads the written report back
(`mapper.readValue(reportFile.toFile(), DailyReport.class)`) and asserts
`ticksAttempted == 1`, `ticksSucceeded == 0`, `errors.size() == 1` — if
finalization had run concurrently with or before the in-flight tick's own
completion, the report would show a day with zero attempted ticks
instead. Combined with `app.tradingLoop().lastError()` being non-null
after `stop()` returns (confirming the specific in-flight tick really did
complete, via the interrupt, not merely "some later unrelated tick"),
this is a real, content-based proof of ordering, not just "the file
exists eventually."

### No log-content assertion

`stop()`'s `ERROR` log on the ambiguous/can't-confirm path isn't
independently asserted by proof case (b) — this module has no log-capture
framework (`runtime/build.gradle.kts` only declares `slf4j-simple` as a
runtime/test dependency, no Logback `ListAppender` or equivalent), and
adding one is out of scope for this task's own minimal-surface mandate.
The test instead asserts the two externally-observable effects the log
message is describing: the report file was never written, and
`DailyReportGenerator.currentDay()` never advanced past the day that was
still in flight (i.e. `finalizeCompletedDayOnShutdown()`'s own
`startNewDay` call never ran).

### Flakiness check

Both new tests were run 8 consecutive times via a fresh (`--rerun`)
Gradle invocation each time; all 8 runs passed with no observed
variance in outcome (`stopFinalizesOnlyAfterAGenuinelyInFlightTick
ActuallyTerminatesViaForcedShutdown` consistently ~0.28-0.4s,
`stopSkipsFinalizationAndLeavesDayTrackingUnadvancedWhenTerminationCannot
BeConfirmed` consistently ~0.3-0.4s) before this was considered done.

## Both proof cases

- **`stopFinalizesOnlyAfterAGenuinelyInFlightTickActuallyTerminatesVia
  ForcedShutdown`**: `gracefulShutdownTimeout=200ms`,
  `forcedShutdownTimeout=3s`. The graceful wait alone is insufficient
  (server never responds); the forced `shutdownNow()` interrupt resolves
  the hang (confirmed empirically fast, ample headroom in 3s);
  `terminated` ends up `true`; the day-D report is written and its
  content proves it reflects the completed (interrupted) tick, not a
  premature snapshot.
- **`stopSkipsFinalizationAndLeavesDayTrackingUnadvancedWhenTermination
  CannotBeConfirmed`**: `gracefulShutdownTimeout=200ms`,
  `forcedShutdownTimeout=Duration.ZERO`. Both waits fail to confirm
  termination (the second deterministically, by construction — see
  above); `terminated` ends up `false`; no report is ever written and
  `currentDay()` never advances.

## Verification

- `./gradlew :runtime:compileTestJava` failed first, for the expected
  reasons (missing 8-arg constructor, missing `hangForever()`, missing
  `assertNotNull` import) — confirmed red before any production code
  changed.
- `./gradlew :runtime:test --tests engine.runtime.PaperTradingAppTest`:
  all 25 tests in the class pass (23 pre-existing + 2 new).
- `./gradlew clean build` (full multi-module suite, matching
  `.github/workflows/java-tests.yml`): **302 tests, 0 failures, 0
  errors, 0 skipped**, across all 6 modules.
- The two new tests specifically re-run 8 consecutive times with
  `--rerun` (forcing real re-execution, not Gradle's cached UP-TO-DATE
  skip) — no flakiness observed.
