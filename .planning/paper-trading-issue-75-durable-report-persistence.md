# GitHub issue #75: durable pending-daily-report persistence

## Scope note

Fixes GitHub issue #75: `DailyReportGenerator.pendingReports` (the
in-memory retry queue for a daily report that failed to write) was
in-memory only -- a process restart while a report was still pending
permanently lost it, with no durable record anywhere that it had ever
been pending. Flagged by CodeRabbit during review of PR #73 (Paper
Trading Bridge Task D), human-approved as an accepted gap at the time
(2026-08-08), revisited now because real paper trading's evidence-
gathering window has started and a lost report during that window is a
real, permanent gap in the evidence -- directly threatens CLAUDE.md's
Paper Trading Pass Criteria ("no missing daily reports"). R3-risk
component (`java/runtime`) -- TDD discipline applied throughout, per
CLAUDE.md's Development Methodology.

## What was built

Two new files, one modified:

- **`PendingDailyReportStore.java`** (new) -- the durable, single-JSON-
  file outbox itself. Package-private, `engine.runtime`.
- **`PendingDailyReportStoreTest.java`** (new) -- 12 standalone unit
  tests for the store in isolation, mirroring `SubmissionMarkerStoreTest`'s
  own structure.
- **`DailyReportGenerator.java`** (modified) -- `pendingReports`
  (`ArrayDeque<DailyReport>`, in-memory only) replaced with
  `pendingReportStore` (a `PendingDailyReportStore`); `beforeTick()`,
  `finalizeCompletedDayOnShutdown()`, `flushPendingReports()`, and the
  `pendingReportCount()` test accessor updated to delegate to it. New
  package-private static `pendingReportsFilePath(Path reportsDirectory)`
  helper. Class Javadoc's "Write failures" section updated to describe
  the fix and reference the new durable-persistence section.
- **`DailyReportGeneratorTest.java`** (modified) -- 4 new tests added,
  the 11 existing tests left byte-for-byte untouched and still pass
  unmodified (see "No behavior change for the common case" below).

No `PaperTradingApp.java` change was needed at all -- see "Why zero
`PaperTradingApp` wiring changes" below.

## Design: mirrors `SubmissionMarkerStore`, not a new pattern

Per this task's own brief, `SubmissionMarkerStore` (Paper Trading Bridge
Task H -- the first durable, cross-restart persistence in this codebase,
`.planning/paper-trading-h-vst-integration.md`) was read closely first
and its design mirrored deliberately rather than inventing something new:

| | `SubmissionMarkerStore` | `PendingDailyReportStore` |
|---|---|---|
| Format | single JSON file, loaded fully into memory on construction, rewritten fully on every mutation | same |
| Atomicity | temp file + `ATOMIC_MOVE`, non-atomic-replace fallback for `AtomicMoveNotSupportedException`/`FileAlreadyExistsException` | same, byte-for-byte identical fallback logic |
| Testability seam | package-private `AtomicMover` functional-interface constructor overload | same pattern, own separate `AtomicMover` interface (not shared -- matches the existing precedent that `DailyReportGenerator` and `SubmissionMarkerStore` already each define their own copy rather than sharing one) |
| Shape | `Map<UUID, SubmissionMarker>` keyed by `clientOrderId`, unordered | `List<DailyReport>`, ORDERED -- see "One deliberate divergence: ordered, not keyed" below |
| Missing file | treated as empty (`NoSuchFileException` caught, ordinary steady state) | same |
| Corrupt/unreadable file | **fails CLOSED** (throws `IllegalStateException`) | **fails SAFE** (starts empty, logs `ERROR`) -- see "The other deliberate divergence: fail safe, not closed" below |
| Write (persist) failure | **throws** (`IllegalStateException`) | **fails SAFE** (logs `ERROR`, does not throw) -- see same section |

### One deliberate divergence: ordered, not keyed

`SubmissionMarkerStore` is keyed by `clientOrderId` because markers have
no inherent delivery-order requirement -- any marker can resolve
independently. `DailyReportGenerator`'s pending queue is a FIFO by
design: oldest-first delivery is a load-bearing invariant, directly
tested by the pre-existing
`aStillPendingOlderReportBlocksAYoungerOneFromWritingOutOfOrder` (an
older, still-failing report must block a newer, independently-writable
one from landing out of order). So `PendingDailyReportStore` persists an
ordered `List<DailyReport>` and preserves that order across a reload,
rather than de-duplicating/keying by date. In practice at most one
`DailyReport` per calendar date is ever appended (a
`DailyReportGenerator` only ever builds one report per day), but the
store does not itself assume or enforce that -- it just persists
whatever order it's given, which is what `DailyReportGenerator`'s own
`flushPendingReports()` already relies on.

### The other deliberate divergence: fail safe, not closed

This task's own brief was explicit: "what happens if the durable file
itself is corrupted/unreadable -- should fail safe, not crash the whole
app." That's the opposite of `SubmissionMarkerStore`'s choice, and the
reasoning genuinely differs by class, not just by preference:

- `SubmissionMarkerStore` fails closed because silently starting empty
  there could let a live order get resubmitted -- a real duplicate-order
  risk, and CLAUDE.md treats duplicate orders as a hard Paper Trading
  Pass Criterion failure on its own.
- `PendingDailyReportStore` fails safe because the stakes point the
  other way: refusing to let the whole paper-trading process start over
  one corrupt local bookkeeping file would forfeit not just the
  possibly-already-lost pending report(s) in that file, but every LATER
  day's report too -- directly working against the very "no missing
  daily reports" goal this store exists to serve. There's also a sharper
  mechanical reason: `PaperTradingApp` drives the tick loop via
  `ScheduledExecutorService#scheduleAtFixedRate`, and an uncaught
  exception escaping a scheduled task **silently cancels all future
  executions of that task**, not just the one tick it happened during.
  If `PendingDailyReportStore` ever threw from inside `beforeTick()`/
  `afterTick()` (construction happens once, outside the tick loop, but
  a *persist* failure happens inside it), that would silently kill the
  entire trading loop, not just daily reporting -- a categorically worse
  outcome than losing one already-degraded local file's own resume
  state.

Concretely: a missing file (`NoSuchFileException`) OR any other
read/parse failure (corrupt JSON, a path whose parent unexpectedly isn't
a directory, etc.) at construction time is treated identically -- logged
loudly at `ERROR`, in-memory queue starts empty, never thrown. The same
applies to a *write* (persist) failure: caught and logged, never
propagated, matching the same "must never crash the scheduler" contract
`DailyReportGenerator#writeReport` itself already established for the
primary report write. A persist failure never rolls back the in-memory
mutation that triggered it, so the process's own within-lifetime retry
behavior (the only guarantee that existed before issue #75) is preserved
even when durability itself is degraded (e.g. a disk-full condition) --
durability is a best-effort enhancement layered on top of that
pre-existing guarantee, not a replacement for it. Both fail-safe paths
are directly tested:
`PendingDailyReportStoreTest#aCorruptFileFailsSafeByStartingEmptyRatherThanThrowing`,
`PendingDailyReportStoreTest#anUnreadablePathFailsSafeByStartingEmptyRatherThanThrowing`,
`PendingDailyReportStoreTest#aPersistFailureDoesNotThrowAndTheInMemoryQueueStaysCorrect`.

## File path convention

`var/live/reports/pending_daily_reports.json` for the default
`reportsDirectory` (`var/live/reports/daily`) -- computed
deterministically as `reportsDirectory.resolveSibling
("pending_daily_reports.json")`, i.e. a **sibling of the `daily/`
report directory**, inside `var/live/reports/` but **not** inside
`var/live/reports/daily/` where the actual completed per-day report
files (`<date>.json`) live.

This reads the acceptance criteria's "alongside `var/live/reports/`,
not inside it, so it's clearly distinct from the actual completed
reports" as: distinct from the actual completed report FILES (which
live in `daily/`), not necessarily a sibling of the `reports/` directory
itself at the `var/live/` level (which is where `submission_markers.json`
lives). Both readings satisfy "clearly distinct from the actual
completed reports" -- the chosen one was picked for a concrete mechanical
reason, not a coin flip: deriving the path automatically from
`reportsDirectory` (rather than threading a wholly independent new path
constant down through `PaperTradingApp`, the way `SUBMISSION_MARKERS_PATH`
is threaded for `SubmissionMarkerStore`) means **zero new
`PaperTradingApp` wiring is needed at all** -- see the next section.

## Why zero `PaperTradingApp` wiring changes

`PendingDailyReportStore`'s constructor is never called with an
explicit, separately-configured path. `DailyReportGenerator`'s own
4-arg constructor derives it internally from the `reportsDirectory`
parameter it already receives, via the new package-private static
`pendingReportsFilePath(Path reportsDirectory)` helper. Every one of
`DailyReportGenerator`'s existing public/package-private constructor
signatures is **unchanged** -- no new parameter added anywhere -- so
`PaperTradingApp`'s two call sites
(`new DailyReportGenerator(tradingLoop, reportsDirectory, clock)`, in
both its `simulated`- and `bingx-vst`-mode constructors) needed **zero**
edits.

This also directly produces the restart property issue #75 asks for,
for free: `PaperTradingApp.fromEnvironment()` always resolves
`reportsDirectory` from the same `PAPER_TRADING_REPORTS_DIR`
environment variable (default `var/live/reports/daily`) on every
process start. Two `DailyReportGenerator` instances constructed against
the same `reportsDirectory` -- exactly what a real restart looks like --
automatically derive the same `pendingReportsFilePath` and therefore
share the same durable file, with no separate "pass the same durable
path across restarts" configuration to get right or get wrong.

## No behavior change for the common case

The overwhelmingly common case -- a process with nothing ever pending --
is provably unaffected: `PendingDailyReportStore`'s constructor only
ever calls `Files.readString` (caught, handled) at load time; it never
creates a file or directory purely from being constructed. A fresh
`DailyReportGenerator` pointed at a `reportsDirectory` with no
pre-existing sibling `pending_daily_reports.json` behaves identically to
before this change. Directly tested:
`aFreshGeneratorWithNoPriorDurableFileBehavesExactlyAsBeforeThisFeature`.

All 11 of `DailyReportGeneratorTest`'s pre-existing tests were run
**completely unmodified** and stayed green, including the two tests
most likely to interact with this change in a subtle way:

- `anAtomicMoveFailureFallsBackToANonAtomicReplaceAndStillCompletesTheWrite`
  asserts an exact atomic-move-attempt count (`1`) against the
  `AtomicMover` injected into `DailyReportGenerator`'s own 4-arg test
  constructor. `PendingDailyReportStore` deliberately uses its own,
  always-default `AtomicMover` -- completely independent of that
  injected seam -- specifically so this existing assertion stays valid
  without modification. Reusing the same seam for both purposes would
  have meant this test's hardcoded `assertEquals(1, ...)` breaking the
  moment the durable outbox's own writes also went through it.
- `aStillPendingOlderReportBlocksAYoungerOneFromWritingOutOfOrder`
  exercises `flushPendingReports()`'s iterate-a-snapshot-then-remove
  logic under exactly the multi-entry, partially-obstructed scenario
  this refactor's correctness depends on -- traced by hand against the
  new store-backed implementation before running it, and confirmed
  passing unmodified.

## An important test-construction pitfall discovered and avoided

The pre-existing `aReportThatFailsToWriteIsRetriedOnALaterTickRatherThanDiscarded`
test forces a write failure by pointing `reportsDirectory` at a path
whose *parent* is a plain file (`blockingFile.resolve("daily")`).
Deriving `pendingReportsFilePath` as `reportsDirectory.resolveSibling(...)`
means that test's own obstruction would **also** obstruct the durable
outbox's own parent directory (both resolve under the same blocked
path) -- so reusing that exact technique for a new "prove durable
persistence really happened" test would have silently proven nothing
(the durable persist would fail too, caught internally, and the test
would still pass for the wrong reason). All of this task's new tests
instead reuse the **narrower** obstruction technique the pre-existing
`aStillPendingOlderReportBlocksAYoungerOneFromWritingOutOfOrder` test
already established -- a directory sitting at exactly ONE date's own
target file path, leaving `reportsDirectory` itself (and therefore its
sibling durable file) completely normal and writable. This is called
out explicitly in the new tests' own comments so a future reader doesn't
reintroduce the same trap.

## The real restart-scenario test

`aReportStillPendingAfterAWriteFailureIsResumedAndCompletedByAFreshInstanceAfterARestart`
is the test issue #75 exists to prove, and is a genuinely separate test
from the "durable persistence happens" and "successful retry clears
both" tests (rather than folding the restart proof into one of those),
so each test proves exactly one property:

1. Constructs `DailyReportGenerator` #1 with its own complete
   `TradingLoop`/`PaperBroker`/`OrderPipeline`/`KillSwitch`/fake-BingX-
   server graph, pointed at `reportsDir` with `2026-08-07.json`'s target
   path obstructed. Ticks twice, crosses the day boundary -- the write
   fails, the report is queued (`pendingReportCount() == 1`) and
   confirmed durably persisted on disk BEFORE the simulated crash.
2. **"The process crashes"**: generator #1 and its entire object graph
   (loop, broker, pipeline, fake server) are simply abandoned inside
   their own `try`-with-resources block, closed, and never touched
   again -- nothing further is ever called on any of them.
3. Constructs `DailyReportGenerator` #2 with a **completely
   independent** `TradingLoop`/`PaperBroker`/`OrderPipeline`/
   `KillSwitch`/fake-server graph (different fake price, different
   clock start date) -- pointed at the SAME `reportsDir`, matching how
   a real restart reuses the same configured `PAPER_TRADING_REPORTS_DIR`.
4. **The core assertion**: `generator2.pendingReportCount() == 1`
   immediately after construction, BEFORE `generator2.beforeTick()` has
   ever been called -- proving the resume happens from durable state at
   construction time, not from anything generator #1 ever did at
   runtime (which is gone).
5. Clears the obstruction, calls `generator2.beforeTick()` once (an
   ordinary tick, not even a day-boundary crossing) -- confirms the
   resumed report is retried and succeeds: `pendingReportCount()` drops
   to `0`, `2026-08-07.json` now exists on disk, its content is the
   ORIGINAL day's data (`ticksAttempted() == 2`, matching generator #1's
   real activity, not anything generator #2 ever did), and the durable
   file itself is confirmed empty by reading it directly.

Result: **passed** on first run against the real implementation (no
red-then-green cycle needed on this specific test once the store and
generator wiring were both already written and unit-tested
independently first).

## TDD

`PendingDailyReportStoreTest` (12 tests) was written and run against the
already-written `PendingDailyReportStore` implementation (written
directly against the tests, mirroring Task D's own precedent of "no
red-then-green cycle needed on the logic itself since the class was
designed directly against the already-written tests" -- confirmed via a
real run before `DailyReportGenerator` was touched at all:
`./gradlew :runtime:test --tests "engine.runtime.PendingDailyReportStoreTest"`
-- **12 tests, 0 failures, 0 errors**, verified via the real JUnit XML
report). `DailyReportGenerator`'s existing 11 tests were re-run
unmodified after the integration and stayed green
(`./gradlew :runtime:test --tests "engine.runtime.DailyReportGeneratorTest"`
before the 4 new tests were added -- confirmed no regression from the
refactor alone, in isolation from any new coverage). The 4 new
`DailyReportGeneratorTest` tests were then added and confirmed passing
together with the 11 existing ones:
`./gradlew :runtime:test --tests "engine.runtime.DailyReportGeneratorTest"`
-- **15 tests, 0 failures, 0 errors**.

Full suite after all changes (`./gradlew clean build`, all six `java/`
modules): **316 tests, 0 failures, 0 errors** (verified by summing every
module's real JUnit XML report, not a claim).

## Judgment calls

- **`PendingDailyReportStore` is its own file/class, not logic folded
  directly into `DailyReportGenerator`.** Matches `SubmissionMarkerStore`'s
  own precedent (a standalone, independently-unit-testable durable
  store, not persistence logic embedded in the class that uses it) and
  keeps `DailyReportGenerator`'s own responsibility (day-boundary
  detection, report building, write-retry orchestration) separate from
  "how pending reports survive a restart."
- **`pendingReportsFilePath` is package-private static, not private.**
  Lets `DailyReportGeneratorTest` independently compute the exact
  durable-file path to assert real JSON content against, without
  needing a getter that would otherwise have no other real caller.
  Mirrors this codebase's existing precedent of small package-private
  static helpers being directly unit-testable (`PaperTradingApp
  .resolveSignalPath`/`resolveReportsDirectory`).
- **The store's own `AtomicMover` is entirely independent of
  `DailyReportGenerator`'s existing `AtomicMover` seam**, rather than
  threading the same injected instance through both. This was a
  deliberate choice made specifically to avoid breaking
  `anAtomicMoveFailureFallsBackToANonAtomicReplaceAndStillCompletesTheWrite`'s
  existing exact-invocation-count assertion (see "No behavior change
  for the common case" above) -- not an oversight. The store gets its
  own, separately-tested atomic-move-fallback coverage in
  `PendingDailyReportStoreTest` instead.
- **No `PaperTradingApp` change beyond nothing** -- confirmed by `git
  diff` showing zero lines touched in that file. This was possible
  specifically because of the deterministic sibling-path derivation
  choice above; it was not assumed going in, and the alternative
  (an explicit new constructor parameter mirroring
  `SUBMISSION_MARKERS_PATH`) was seriously considered and rejected in
  favor of the simpler, zero-wiring option once it became clear the
  restart property held either way.
- **Fail-safe (not fail-closed) on a corrupt/unreadable durable file**,
  the one deliberate, disclosed divergence from `SubmissionMarkerStore`'s
  own pattern -- see "The other deliberate divergence" section above for
  the full reasoning, required directly by this task's own brief.
