# Shared KIS account risk ledger, Task B: `AccountLedgerStore` + `AccountLedgerLock`

## Scope note

This is **Task B** of the 4-task "Shared KIS account risk ledger" plan
(`.claude/plans/tender-finding-matsumoto.md`'s "Shared KIS account risk
ledger (multi-process risk budget)" section — governing brief), which
follows and depends on Task A (`AccountStateProvider` extraction, PR #99,
merged as `8ff2db5`). Task B builds the ledger's durable storage
(`AccountLedgerStore`) and cross-process mutex (`AccountLedgerLock`) in
complete isolation — **standalone and unwired**. Nothing in this codebase
calls either class yet; `SharedKisAccountLedger` (Task C) is the first
real caller. R3-risk-adjacent component (`java/runtime`, real
cross-process concurrency infrastructure that will eventually gate order
submission) — TDD discipline applied throughout, per CLAUDE.md's
Development Methodology.

### GSD phase status

- **Discuss**: resolved by the governing brief and the plan file it
  points at — the record shapes, the store's read/write contract, the
  chosen locking primitive (atomic file creation, not `FileLock`) and the
  reasoning behind it, and the required test list were all specified
  directly in the task brief. The one thing genuinely **not** resolved in
  advance — whether the chosen primitive actually holds on this
  repository's real drvfs mount under real concurrent load — is exactly
  what this task's own required real-process test exists to answer. It
  did not simply confirm a yes/no; it surfaced a real bug (below), which
  this document treats as the central finding of this task, not a
  footnote.
- **Plan**: the governing plan file above is the plan; this doc's "What
  was built" and "The real finding" sections record how the real code
  matched or deviated from it.
- **Execute**: this document's "What was built", "TDD", and "The real
  finding" sections.
- **Verify**: this document's "Verification" section — real local test
  runs (`./gradlew clean build`, plus extensive real-process stress runs
  outside Gradle), not a claim that tests would pass.
- **Ship**: **pending.** PR to be opened, CI green, CodeRabbit reviewed —
  per the governing task brief's own explicit instruction and CLAUDE.md's
  Auto-merge Policy (Java runtime/Risk-Gateway-adjacent code is excluded
  from delegated auto-merge regardless of CI/CodeRabbit status), **this PR
  is not to be merged by an LLM session.**

## What was built

Four new production files, one new test-support class, three new test
files, one `build.gradle.kts` dependency addition — zero existing files
touched otherwise:

- **`engine.runtime.LedgerReservation`** (record,
  `LedgerReservation.java`) — `clientOrderId` (`UUID`), `symbol`,
  `processId` (`long`), `hostname`, `notional` (`BigDecimal`),
  `reservedAt` (`Instant`). Compact constructor `Objects.requireNonNull`s
  every reference-typed field, matching `AccountState`/`RiskLimits`'s own
  validation style in `java/risk`.
- **`engine.runtime.AccountLedger`** (record, `AccountLedger.java`) —
  `venue`, `accountId`, `allocatedVirtualCapital`,
  `lastReconciledDailyPnlPercent`/`WeeklyPnlPercent`/`MonthlyPnlPercent`,
  `lastReconciledAt` (nullable `Instant`),
  `reconciliationAlarmTrippedAt`/`reconciliationAlarmReason` (nullable,
  paired), `reservations` (`List<LedgerReservation>`, defensively copied
  via `List.copyOf` in the compact constructor, matching
  `TradingLoop.fillHistory()`'s own established immutable-snapshot
  convention). Both records are package-private, matching the governing
  plan's own "2. The shared ledger (`java/runtime`, package-private
  except the [`AccountStateProvider`] interface)" section header.
- **`engine.runtime.AccountLedgerStore`** (package-private final class,
  `AccountLedgerStore.java`) — `static AccountLedger load(Path, String
  venue, String accountId, BigDecimal defaultAllocatedCapital)` and two
  `static void persist(...)` overloads (3-arg with an injectable
  `AtomicMover`, 2-arg convenience delegating to the real default mover).
  Direct structural template: `SubmissionMarkerStore` (same temp-file +
  `ATOMIC_MOVE`-with-fallback write pattern, same
  missing-file-is-fine/anything-else-fails-closed read contract). One
  **deliberate divergence**, per the brief: no caching, no instance state
  at all — every method is `static` and re-reads/re-writes the file on
  disk every call, because (unlike `SubmissionMarkerStore`, single-writer-
  per-process) this file will be shared by multiple independent OS
  processes once Task C wires it in.
- **`engine.runtime.AccountLedgerLock`** (package-private final class
  implementing `AutoCloseable`, `AccountLedgerLock.java`) — `static
  AccountLedgerLock acquire(Path lockPath, Duration staleThreshold,
  Duration totalRetryBudget)` and instance `void close()`. Atomic-file-
  creation mutex (`Files.createFile`, not `FileLock` — see the governing
  plan's own reasoning, restated in this class's Javadoc). Contains **the
  one substantive design correction found and fixed during this task** —
  see "The real finding" below, the actual point of this document.
- **`engine.runtime.LockContenderMain`** (test-support class, not a
  test itself, `src/test/java/engine/runtime/LockContenderMain.java`) —
  a standalone `main()` entry point launched as a genuine second (third,
  fourth, ...) JVM by `AccountLedgerLockMultiProcessTest` via
  `ProcessBuilder`. Repeatedly acquires the shared lock and performs a
  deliberately non-atomic read-sleep-increment-write cycle on a shared
  counter file while holding it — the actual mechanism by which real
  cross-process mutual exclusion gets proven or disproven.
- **`java/runtime/build.gradle.kts`** gains one new dependency:
  `com.fasterxml.jackson.datatype:jackson-datatype-jsr310:2.18.9`. See
  "Judgment calls" below — checked, not assumed, that this was actually
  needed.

## TDD

1. Wrote `AccountLedgerStoreTest.java` first (10 cases: fresh-bootstrap,
   round-trip including reservations, round-trip of a tripped alarm,
   "no stale in-memory cache" proof, corrupt-file fail-closed, valid-JSON-
   but-missing-required-field fail-closed, parent-directory creation,
   atomic-write-no-leftover-tmp, the `AtomicMover` fallback seam, and the
   real production default path) against not-yet-existing
   `AccountLedger`/`LedgerReservation`/`AccountLedgerStore` — confirmed
   red via `./gradlew :runtime:compileTestJava` (17 real "cannot find
   symbol" compile errors, all pointing at `AccountLedgerStore` /
   `AccountLedger` / `LedgerReservation`).
2. Implemented the two records, then `AccountLedgerStore`. Ran
   `./gradlew :runtime:test --tests
   "engine.runtime.AccountLedgerStoreTest"` — green on the first real
   attempt, but with a compiler deprecation warning
   (`SerializationFeature.WRITE_BIGDECIMAL_AS_PLAIN` — added defensively,
   not requested by the brief). Removed it rather than suppress the
   warning: real round-trip tests already passed without it (this
   project's realistic monetary figures never trigger `BigDecimal`
   scientific notation), so it was a speculative addition with no test
   actually depending on it — cleaner to drop than to carry a deprecated
   API for a case that doesn't arise. Re-ran clean, zero warnings.
3. Wrote `AccountLedgerLock.java` and its in-process tests
   (`AccountLedgerLockTest.java`: threaded mutual exclusion via an
   unguarded shared counter, dead-PID steal, expired-timestamp steal,
   retry-budget exhaustion) together, since the API shape and the
   staleness-steal algorithm were specified in enough concrete detail by
   the brief that writing them independently first would have meant
   guessing at internal method boundaries the brief didn't actually leave
   open. Compiled clean, all 4 tests green on the first run, stable
   across 3 repeated full re-runs (`--rerun-tasks`).
4. Wrote `LockContenderMain.java` and
   `AccountLedgerLockMultiProcessTest.java` — the real second-JVM test,
   the actual point of this task per the governing brief. **This is where
   TDD earned its keep**: the first real run of this test failed for
   real, with a real, reproducible correctness bug — see "The real
   finding" below, which supersedes the rest of this normally-
   chronological TDD narrative for this one test.

## The real finding (the actual point of this task)

The governing brief was explicit: *"If this test reveals the primitive
genuinely does NOT work reliably on this filesystem, stop and report that
back to me rather than pushing forward with a broken primitive."* This
section is that honest account — the primitive (atomic file creation)
turned out to be reliable; **this implementation's own staleness-steal
logic was not**, and the real-process test is exactly what caught it.

### The failure, as first observed

`AccountLedgerLockMultiProcessTest` (4 real JVM processes, 5 iterations
each, `holdMillis=20`, expected final counter 20) failed on its first
real run: **final counter 19, not 20** — a genuine lost update, meaning
two real OS processes really were inside the lock-protected critical
section at the same time at some point during that run.

### Root-causing it for real, not guessing

Rather than assume "drvfs doesn't support atomic create" (the scarier,
more surface-plausible explanation given the governing brief's own
framing of drvfs as the risk) and redesign around a different primitive,
this was investigated directly: reran the exact scenario standalone
(outside Gradle, via `ProcessBuilder`-equivalent manual `java` process
launches) with temporary, fine-grained `System.err` timestamp
instrumentation added at every internal step of `acquire`/
`tryStealIfStale` (before-create, after-create-succeeded, metadata-built,
metadata-write-complete, read-raw-content, staleness-verdict). 15
additional raw repro runs were needed before a steal event recurred
(intermittent, not every run) with the diagnostics attached.

The captured, real timeline (`pid=39113`, one concrete instance among
several observed) showed:

- `39113` successfully created the lock file and captured
  `acquiredAt=...550673724Z` at `t=08.541`–`08.550`.
- Its own `Files.writeString(lockPath, ...)` for that tiny JSON blob did
  not complete until `t=09.059` — **a real, measured ~509ms delay for a
  single small write**, under genuine 4-process concurrent contention on
  the same path on this repository's actual `/mnt/c` drvfs mount.
  (`nproc` reports 12 real CPUs available, ruling out plain CPU
  oversubscription as the cause — this reads as real filesystem-level
  contention latency, not scheduler starvation.)
- `39113` then went on to legitimately finish that iteration, and iterate
  through its remaining 4 iterations entirely normally (`09.080`,
  `09.105`, `09.126`, `09.147` — each a normal, fast create-write-release
  cycle), then exited.
- Meanwhile, a **sibling** process (`39112`) had, at `t=09.072`,
  successfully read `39113`'s **iteration-1** lock content (already
  fully written by then) and cached it locally as the candidate to judge
  for staleness. It did not get around to actually *evaluating* that
  judgment (`ProcessHandle.of(39113)...isAlive()` etc.) until
  **`t=09.598`** — a **526ms gap between the read and the verdict**, on
  the same process, for reasons not further isolated (plausibly the same
  filesystem-contention pressure manifesting as I/O-wait time on this
  thread's own subsequent operations, though this was not pinned down
  further since it wasn't necessary to fix the bug).
- By `09.598`, `39113` really had exited (it finished all 5 iterations
  and the process itself was gone — `ProcessHandle.of(39113).isPresent()
  == false`, genuinely, not a false reading — a dedicated isolated
  two-process `ProcessHandle` sanity check confirmed the JDK's process
  APIs themselves behave correctly on this system). So `39112` correctly
  concluded "the pid I read is dead" — **but the fact it read is 526ms
  stale**: `39113`'s iteration-1 lock (the one `39112` was actually
  judging) had already been legitimately deleted by `39113` itself around
  `09.080`, on its way to iteration 2. The **unconditional** `Files.delete
  (lockPath)` that followed a "stale" verdict in the original
  implementation would delete *whatever currently exists* at `lockPath`
  at the moment of the delete call — which, 526ms after the stale read,
  could easily be (and, in the failing run, evidently was) a **different,
  brand-new, legitimately-held lock generation** belonging to some other
  process that had acquired it in the interim. That is a real, direct
  break of mutual exclusion: this process deletes a live sibling's lock
  out from under it, both believe they hold it, and a lost update
  results.

This is a genuine **TOCTOU (time-of-check-to-time-of-use) bug** in the
original `tryStealIfStale` design, not a filesystem atomicity failure.
`Files.createFile`'s own exclusivity was never contradicted by any
evidence gathered — every observed anomaly traced back to the *gap*
between reading old metadata and later acting on it, a gap this
filesystem's real (if unusually high, under contention) latency made
large enough to matter.

### The fix

`tryStealIfStale` now **re-reads the lock file immediately before
deleting it**, and only proceeds with the delete if the file's content is
*still exactly* the `LockMetadata` originally judged stale (`record`
`equals()` — `pid`, `hostname`, and `acquiredAt` to nanosecond precision
all match). If the re-read shows different content (a new generation) or
the file is now simply gone, the delete is skipped and the method returns
`false` — the caller's normal backoff-and-retry loop re-evaluates fresh
next attempt rather than ever acting on stale information about what the
file currently holds. This shrinks the exploitable race window from
"however long staleness evaluation happens to take" (500ms+, measured) to
the gap between two adjacent file operations — not zero, but the same
order of magnitude of residual risk this codebase already accepts
elsewhere (e.g. tolerating `NoSuchFileException` on a delete as "someone
else already reclaimed it" in `AccountLedgerStore`'s own temp-file-move
pattern), and disclosed as such in the method's own Javadoc rather than
overclaimed as a complete fix.

Implementation detail: `readMetadataOrNull` was factored out (used both
for the original read and the pre-delete re-verification) returning
`null` for "file absent" and a private sentinel constant
(`EMPTY_OR_UNPARSEABLE`, compared by reference identity) for "present but
unparseable right now" — replacing the original inline try/catch so the
same three-way outcome (absent / unparseable / real metadata) could be
computed identically at both call sites without duplicating the
read-and-parse logic.

### Verifying the fix actually holds, not just re-running the test once

A single subsequent green run would not have been convincing given the
bug was intermittent (roughly 1-in-3 to 1-in-15 raw repro runs surfaced a
steal event at all, and only some fraction of *those* would race badly
enough to lose an update). After the fix:

- The real `AccountLedgerLockMultiProcessTest` (via `./gradlew`) was run
  **5 additional times** (`--rerun-tasks`, forcing a genuine re-execution
  each time, not a cached result) — green every time.
- A raw, non-Gradle stress script launched **30 consecutive rounds** of 6
  real, independent JVMs racing 8 iterations each (48 expected increments
  per round, 1,440 total across the run) directly via `LockContenderMain`
  — **every round produced exactly the expected count, 48/48, 30/30
  rounds**, including rounds that did trigger real steal events (steal
  logging was still observed, confirming the staleness path was actually
  exercised under this harder stress load, not merely avoided) with no
  lost updates in any of them.
- The full `:runtime:test` suite was independently re-run **3 more
  times** (`--rerun-tasks`) — green every time.

This is treated as strong, not merely adequate, evidence: the original
bug reproduced within single-digit repro attempts before the fix; after
the fix, 35 total real multi-process runs (5 via the JUnit test + 30 via
the raw stress harness) covering roughly 1,460 individual lock
acquisitions produced zero lost updates.

## Judgment calls

- **`Files.createFile(lockPath, StandardOpenOption.CREATE_NEW)`, as
  written in the governing brief's own code sketch, does not compile** —
  `Files.createFile` takes `FileAttribute<?>...` varargs, not a
  `StandardOpenOption`, and is already atomic/exclusive-create by itself
  (throws `FileAlreadyExistsException` if the target exists — no
  `CREATE_NEW` option is needed or accepted for that specific overload).
  Used the real, correct API, `Files.createFile(lockPath)`, to get the
  identical semantics the sketch intended. Documented directly in
  `AccountLedgerLock`'s own class Javadoc as a named deviation from the
  brief's literal text, not silently corrected.
- **`jackson-datatype-jsr310` needed adding to `:runtime`'s
  `build.gradle.kts` — verified, not assumed.** The brief's own
  instruction ("check ... before assuming you need to add a dependency")
  was followed literally: a throwaway class importing
  `com.fasterxml.jackson.datatype.jsr310.JavaTimeModule` was compiled
  against `:runtime` *before* adding the dependency and failed with
  "package does not exist." Also found, and worth recording since the
  brief's own premise here was subtly off: `SubmissionMarker` (the
  brief's cited example of "other classes in this module already
  serialize `Instant`") in fact does the **opposite** — its own Javadoc
  explicitly states it avoids `jackson-datatype-jsr310` by storing
  `Instant` as a plain ISO-8601 `String` field instead, specifically to
  avoid this exact dependency for one timestamp field. `AccountLedger`/
  `LedgerReservation` have three real `Instant`-typed fields apiece
  carrying real meaning, per the brief's own record shapes — a
  `String`-only workaround at every read/write site was judged worse than
  adding the real dependency, so it was added, with the actual
  compile-classpath-vs-transitive-runtime-classpath distinction that made
  it necessary (jackson-datatype-jsr310 was already transitively present
  on `:runtime`'s *test*/*runtime* classpath via `:schemas`'s own
  `implementation` dependency on it, but Gradle's `implementation`/`api`
  separation does not leak that to `:runtime`'s own *compile* classpath)
  documented directly in the `build.gradle.kts` comment.
- **`AccountLedgerLock` given its own separate, independently-configured
  Jackson `ObjectMapper`, not a shared one with `AccountLedgerStore`.**
  Considered a small `sharedMapper()` accessor on `AccountLedgerStore` to
  avoid the ~3-line duplication; reverted in favor of each class keeping
  its own copy, extending the same "each durable-store class keeps its
  own copy" convention the brief explicitly named for the `AtomicMover`
  interface specifically ("do not 'fix' this by extracting a shared
  interface, that would be an unrequested refactor") to the closely
  analogous `ObjectMapper`-configuration case, on the judgment that the
  same reasoning applies: keeping these primitives structurally
  independent of each other is worth a few duplicated lines.
- **`SerializationFeature.WRITE_BIGDECIMAL_AS_PLAIN` added, then
  removed.** See "TDD" step 2 above — added defensively, found deprecated
  by the compiler, removed once confirmed no test actually needed it for
  this project's realistic monetary-figure ranges.
- **No log-capture test convention exists anywhere in this codebase**
  (checked via `grep` for `ListAppender`/`LoggerContext`/`TestAppender`/
  similar before writing the stale-lock tests, per the brief's own
  explicit instruction not to invent one unasked) — the dead-PID and
  expired-timestamp steal tests assert on the resulting *behavior*
  (the lock is successfully re-acquired) rather than on log output
  directly.
- **Split the required test list across two files**, not one: 
  `AccountLedgerLockTest.java` (threaded mutual exclusion, both stale-lock
  variants, retry-budget exhaustion — all in-process, fast) and
  `AccountLedgerLockMultiProcessTest.java` (the real second-JVM test —
  slow, and the one this task's brief itself calls out as needing its own
  careful, unhurried treatment). Matches this codebase's own precedent of
  separating a materially slower/heavier test class from faster
  same-subject ones.
- **`LockContenderMain` lives in `src/test/java`, package
  `engine.runtime`**, not a separate module or a public class — it needs
  package-private access to `AccountLedgerLock.acquire`, and putting it
  in the test source set (rather than, say, main) keeps it out of any
  real production classpath while still being reachable via
  `ProcessBuilder` using this JVM's own `java.class.path` (verified
  empirically, before writing the real test, that Gradle's `Test` task on
  this repository/Gradle version genuinely exposes a real, literal,
  usable classpath via that system property rather than a Windows-style
  "pathing jar" indirection — confirmed via a throwaway diagnostic test
  printing every classpath entry, then removed).

## Explicitly out of scope (per the governing brief, not attempted here)

- `SharedKisAccountLedger implements AccountStateProvider` and any wiring
  of it into `TradingLoop`/`PaperTradingApp` (Task C) — the concurrent-
  processes safety-property test proving *combined committed notional*
  never exceeds `allocatedVirtualCapital` under real load belongs there,
  not here; this task's own real-process test proves a narrower thing
  (the lock itself provides mutual exclusion), which Task C's ledger-level
  logic will depend on but does not itself build.
- `AccountLedgerReconciler`, startup bootstrap-from-real-balance, the
  10%-mismatch alarm (Task D).
- `resolveAccountLedgerPath(venue, accountId)` and any `var/live/` file-
  naming convention for the real ledger/lock file pair — not part of the
  brief's own file list for this task; `load`/`persist`/`acquire` all
  take a `Path` directly, and path resolution is left to Task C, matching
  the brief precisely.
- `java/risk`, `java/oms`, `java/execution`, `java/exchange`,
  `TradingLoop.java`, `PaperTradingApp.java`, `AccountStateProvider.java`
  — none touched.

## Verification

- `./gradlew :runtime:compileTestJava` (before implementing the ledger
  classes) — failed with 17 real "cannot find symbol" compile errors
  against `AccountLedgerStoreTest.java`, the expected red state.
- `./gradlew :runtime:test --tests "engine.runtime.AccountLedgerStoreTest"`
  — green, 10/10, after implementation (one deprecation warning found and
  removed — see "TDD" above; zero warnings on the final version).
- `./gradlew :runtime:test --tests "engine.runtime.AccountLedgerLockTest"`
  — green, 4/4, stable across 3 repeated full re-runs.
- `./gradlew :runtime:test --tests
  "engine.runtime.AccountLedgerLockMultiProcessTest"` — **failed for
  real** on the first run (19 vs. expected 20 — see "The real finding"
  above); green after the fix, confirmed **5 additional times**
  (`--rerun-tasks`).
- A raw, non-Gradle stress harness (`LockContenderMain` launched directly
  via `ProcessBuilder`-equivalent manual invocation, bypassing Gradle's
  own test-launch overhead to run many more real-process rounds in
  reasonable wall-clock time) — **30/30 rounds exactly correct** after
  the fix (6 processes × 8 iterations = 48 expected per round).
- `./gradlew :runtime:test` (full module suite) — green, confirmed 3
  times (`--rerun-tasks`).
- `./gradlew clean build` (full six-module suite, clean, not incremental)
  — **BUILD SUCCESSFUL**. Summed real JUnit XML reports across every
  module (`schemas`, `oms`, `risk`, `execution`, `exchange`, `runtime`):
  **420 tests, 0 failures, 0 errors** (405 pre-existing from Task A's
  merged state + 15 new: 10 `AccountLedgerStoreTest` + 4
  `AccountLedgerLockTest` + 1 `AccountLedgerLockMultiProcessTest`).
- PR to be opened, not merged — per the governing task brief and
  CLAUDE.md's Auto-merge Policy, this is Java runtime/Risk-Gateway-
  adjacent code and requires explicit human sign-off regardless of
  CI/CodeRabbit status.
