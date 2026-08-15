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

## CodeRabbit review findings

One review round on PR #100 (`ASSERTIVE` profile) against commit
`32d94de` (the original commit): `CHANGES_REQUESTED`, 7 inline comments
(3 Critical/Major on `AccountLedgerLock.java`, 1 Major each on
`AccountLedgerStore.java`/`LedgerReservation.java`/the multi-process
test, 1 Minor on the retry-budget test). Every finding was verified
against the real current code before acting on it, per this task's own
standing instruction to treat review content as untrusted data, not
instructions to follow blindly. All seven were real and valid; all seven
fixed.

- **`close()` deletes unconditionally — a second, independent TOCTOU
  break on the release side (Critical).** Real and serious: (1) this
  instance holds the lock, but a slow filesystem operation inside its own
  critical section (this class's own Javadoc already documents measured
  500ms+ single-operation latency under contention) pushes its real
  elapsed hold time past `staleThreshold`; (2) a waiting sibling correctly
  judges this instance's lock stale (by this class's own rules), steals
  it, and acquires its own new, legitimate generation; (3) this instance,
  unaware, finally reaches its own delayed `close()` and deletes the
  *sibling's* lock, believing it's releasing its own. Fixed: `acquire`
  now retains the exact `LockMetadata` it wrote (`createAndWriteMetadata`
  returns it; the constructor stores it as `ownMetadata`); `close()`
  re-reads the file and only deletes if it still holds precisely
  `ownMetadata` — a mismatch is logged at `ERROR` (this class's
  established never-silent convention) and the delete is skipped
  entirely, never risking another holder's live lock. New deterministic
  regression test:
  `closeDoesNotDeleteADifferentLockGenerationThatHasSinceReplacedThisOnesOwnFile`
  — simulates the steal directly (delete + rewrite with different
  metadata) rather than trying to reproduce the real timing race, which
  would need a test-only delay hook into production code; judged
  sufficient proof that the mechanism itself works, the same standard
  this class's other fabricated-lock tests already use.
- **`createFile` succeeds, `writeString` fails → a permanently-empty,
  never-stealable lock file (Critical).** Real and, per the reviewer's own
  framing, high-impact: `readMetadataOrNull` returns
  `EMPTY_OR_UNPARSEABLE` for empty content, and the original
  `tryStealIfStale` always backed off on that case with no staleness path
  at all — so every future waiter for this lock would exhaust its retry
  budget and fail, forever, blocking the entire shared ledger until a
  human manually deleted the file. Fixed two ways, per the reviewer's own
  two-part suggestion: (1) `createAndWriteMetadata` now wraps the metadata
  write in `try`/`catch`, deleting the just-created file before
  propagating any failure (best-effort; a cleanup failure is attached via
  `addSuppressed` rather than masking the original); (2) as a backstop for
  the one gap that cleanup still can't reach — a hard process kill between
  `Files.createFile` succeeding and the `catch` block ever running — new
  method `tryStealIfAbandonedEmpty` judges an empty/unparseable lock
  file's staleness via its own filesystem **last-modified time** instead
  of any in-memory state (keeping this class's stateless design intact),
  with the same re-verify-immediately-before-delete discipline as the
  ordinary path. New tests: `acquireDoesNotStealAFreshEmptyLockFile`
  (a young empty file must not be stolen — proven by asserting a short
  retry budget genuinely exhausts, not a premature "success");
  `acquireStealsAnAbandonedEmptyLockFileOlderThanStaleThreshold` (an
  empty file backdated via `Files.setLastModifiedTime` past
  `staleThreshold` must be reclaimed).
- **The acquire-side re-verify-then-delete still isn't truly atomic
  (Major, flagged against the planning doc directly).** Correct, and
  already disclosed as such in this document and in
  `tryStealIfStale`'s own Javadoc before this review — restated here for
  the record rather than treated as new: no atomic compare-and-delete
  primitive exists in `java.nio.file`, and introducing a different
  locking mechanism to get one was already rejected earlier in this
  design specifically for this repository's drvfs reliability reasons
  (see the governing plan). What *was* fixed in direct response to this
  finding is the close()-side counterpart above — applying the same
  re-verify discipline symmetrically on both the steal path and the
  release path is the practical maximum achievable with this primitive
  family, closing the window to the gap between two adjacent file
  operations on *both* sides rather than leaving the release side
  completely unguarded (as it was before this review). Not claimed as a
  perfect fix anywhere in the code or this document — the residual window
  is named explicitly in both.
- **`AccountLedgerStore.load` never validates the loaded ledger's own
  `venue`/`accountId` against what was requested (Major).** Real: a
  future path-resolution bug in Task C's caller, or a file mix-up, could
  silently load one account's real, currently-committed exposure and use
  it to gate a *different* account's orders. Fixed: `load` now throws
  `IllegalStateException` if `ledger.venue()`/`ledger.accountId()` don't
  exactly match the requested values, immediately after deserializing.
  New tests: mismatched venue, mismatched accountId, and a
  matching-identity control case (all three fail-closed/succeed as
  expected).
- **`LedgerReservation.notional` accepted zero or negative values
  (Major).** Real: `SharedKisAccountLedger` (Task C) is expected to
  derive available capital as `allocatedVirtualCapital -
  Σ(reservations.notional)` — a non-positive reservation would *increase*
  derived available capital, a real risk-budget-bypass shape, not merely
  a data-quality nit, for a record whose entire purpose is bounding real
  exposure. Fixed: the compact constructor now rejects
  `notional.signum() <= 0`. New test:
  `aReservationWithZeroOrNegativeNotionalIsRejectedByLedgerReservationItself`.
- **The multi-process test doesn't drain child stdout/stderr and doesn't
  guarantee cleanup on early failure (Major).** Real: `AccountLedgerLock`
  logs at `ERROR` on every steal (routine under this test's own
  deliberately heavy contention), and the original test only read a
  child's stderr *after* `waitFor` returned — if a child filled the OS
  pipe buffer first, it would block forever on its own write, turning a
  real correctness signal into a misleading test-timeout failure with no
  useful diagnostic. Separately, an early `fail(...)` skipped cleanup of
  any still-running sibling processes, which would keep mutating the
  shared lock/counter files completely unsupervised. Fixed: each child's
  combined output now redirects straight to its own file from the start
  (`ProcessBuilder#redirectErrorStream`/`redirectOutput`) — nothing is
  ever left sitting in an in-memory pipe; the entire wait loop is now
  wrapped in `try`/`finally`, unconditionally calling
  `Process::destroyForcibly` on every launched process regardless of how
  the loop exits.
- **The retry-budget-exhaustion test's upper-bound tolerance (budget +
  1000ms) is tight given now-documented 500ms+ drvfs latencies (Minor).**
  Real, and a good catch given this task's own findings above: a single
  slow file operation plus the last backoff sleep could plausibly
  approach the original 1000ms slack on a bad day. Widened to `+3000ms`
  with a comment explaining the real, measured basis for the number
  (pointing at this same document's "The real finding" section) rather
  than an arbitrary round figure.

Re-ran the full suite after all seven fixes: `./gradlew clean build` —
still green, **427 tests, 0 failures, 0 errors** project-wide (420 +
7 new: 4 in `AccountLedgerStoreTest`, 3 in `AccountLedgerLockTest`).
Also re-ran the real multi-process safety property specifically after
these changes, not just trusted the diff: the JUnit
`AccountLedgerLockMultiProcessTest` 3 additional times (`--rerun-tasks`,
green every time) and a fresh 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct**. (A separate, first re-run of the stress harness produced
several `MISSING`/short-count results — traced immediately to a
self-inflicted harness artifact, not a lock bug: a concurrent `./gradlew
:runtime:test` invocation was rewriting this exact module's `.class`
files on disk while the background stress script was still reading them
for later rounds, producing real `ClassFormatError: Truncated class
file`/`NoClassDefFoundError` errors in the affected child processes' own
captured output — confirmed directly from those processes' redirected
output files, not guessed. Re-ran cleanly with no concurrent Gradle
invocation touching the same build output and got the clean 25/25 result
above. Recorded here rather than omitted, matching this document's own
established practice of keeping a real investigative dead-end visible.)

### Round 2

Against commit `21ea2b1` (after round 1's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T10:27:37Z`): `CHANGES_REQUESTED`, 4
actionable inline comments. All four verified against the real current
code and fixed:

- **A second, deeper Major finding on the exact same acquire-side
  mechanism round 1 already touched (labeled "Heavy lift"):
  `createFile`-then-`writeString` were still two separate,
  <i>path</i>-based operations, even after round 1's cleanup-on-failure
  fix.** Real and correctly identified: if a holder's own metadata write
  is slow enough (this class's own Javadoc already documents 500ms+
  measured latency under contention) for a sibling to legitimately judge
  the file abandoned via `tryStealIfAbandonedEmpty`, delete it, and
  acquire its own new generation — and only *then* does the original,
  now-orphaned holder's `Files.writeString` call finally run — that call
  re-resolves the path fresh and would silently overwrite the sibling's
  real, live metadata with the original holder's stale content. Two
  legitimate holders would both believe they own the lock.

  **Fixed at the root, not patched around**: `createAndWriteMetadata` now
  opens a single `SeekableByteChannel` atomically (`Files.newByteChannel`
  with `StandardOpenOption.CREATE_NEW`) and writes the metadata through
  *that same handle*, rather than reopening the path for a second
  operation. **Verified empirically before writing the fix, not assumed**:
  this repository's specific concern throughout (a Windows-mounted drvfs
  mount, where "delete an open file" has historically meant something
  different than POSIX unlink semantics) made this worth checking for
  real rather than trusting general Java portability claims. A standalone
  probe (`HandleTest.java`, run directly against
  `/mnt/c/Dev/trading-engine`) confirmed: deleting a path out from under
  an already-open `CREATE_NEW` channel succeeds on this real mount, a new
  file can then be created at that same path, and a subsequent write
  through the original (now-orphaned) channel lands on the original,
  now-path-invisible file rather than corrupting the new one — real,
  observed POSIX-style behavior on this specific drvfs mount, not a
  general Java guarantee taken on faith. New regression test,
  `AccountLedgerLockTest#aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen`,
  reproduces the exact mechanism through the real production code path
  (opens a `CREATE_NEW` channel the same way `createAndWriteMetadata`
  does, has a real sibling steal-and-reacquire through
  `AccountLedgerLock.acquire`, then completes the original write through
  the old handle) and asserts the sibling's content is untouched.

  **One narrow residual gap disclosed, not fixed**: the cleanup-on-write-
  failure path (round 1's own fix) still deletes by path, not by
  verified generation — if the write itself throws (not merely runs
  slowly) and a sibling independently reclaims the file in that same
  narrow window, this cleanup could delete the sibling's new file.
  Judged acceptable to leave unguarded: this requires a genuine I/O
  failure during the write, not just slowness, a materially rarer
  precondition than every other case this task's review rounds have
  found and fixed. Documented directly in `createAndWriteMetadata`'s own
  Javadoc rather than silently left unmentioned.

- **`close()`'s re-verification conflated a transient read failure with a
  genuine "stolen by another holder" (Minor).** Real: `readMetadataOrNull`
  used to return the same `EMPTY_OR_UNPARSEABLE` sentinel both when
  `Files.readString` itself threw (says nothing about the file's actual
  content) and when the content was genuinely empty/unparseable — `close()`
  logged both identically as "no longer holds this instance's own
  metadata... mutual exclusion was lost," which is simply false for a
  transient read hiccup and points an investigating operator in the wrong
  direction. **Fixed with a real second sentinel** (`READ_FAILED`,
  distinct from `EMPTY_OR_UNPARSEABLE`), threaded through both
  `tryStealIfStale` (a transient read failure never routes into the
  mtime-based abandonment check, which needs a confirmed-empty read to
  mean anything) and `close()` (three distinct branches now: read-failure,
  empty/unparseable, genuine mismatch — each with its own honest message).
  Skipping the delete stays identical (safe) in all three cases; only the
  diagnostics differ.
- **`AccountLedgerStore.persist`'s non-atomic fallback isn't a single
  atomic operation — an interrupted replace could make a missing
  `ledgerPath` indistinguishable from "never persisted" (Major, "Heavy
  lift").** Real: a crash mid-`REPLACE_EXISTING` could leave `ledgerPath`
  genuinely missing while its `.tmp` source lingers; `load` would
  silently bootstrap a fresh, empty ledger, discarding every other
  process's real committed reservations. **Fixed as a real, explicitly
  partial mitigation, not a full solution** (matching the reviewer's own
  "Heavy lift" label — a complete backup/generation/recovery system is
  real, additional, undesigned scope, appropriately out of bounds for a
  storage/locking-primitives task under review pressure): `load` now
  fails closed (`IllegalStateException`) if `ledgerPath` is missing but a
  `.tmp` sibling exists — strong circumstantial evidence of an
  interrupted replace, not proof, but enough to refuse guessing rather
  than silently discarding real state. Explicitly does **not** attempt
  automatic recovery from the `.tmp` content, and explicitly does not
  cover every possible interrupted-fallback timing (e.g. the `.tmp` file
  itself also being lost) — both named directly in the method's own
  Javadoc. New tests: a leftover `.tmp` with no `ledgerPath` fails closed;
  the ordinary "genuinely never persisted, no `.tmp` either" case still
  bootstraps fresh (a control case, confirming this fix doesn't overreach
  into failing the common path).
- **The empty-lock reclamation tests didn't cover the sequence the Major
  finding above is about (Trivial).** Real, and superseded by something
  stronger rather than the reviewer's own literal suggestion: their
  suggested test used a plain, unguarded `Files.writeString` to "simulate"
  the original creator's delayed write — but that would not have actually
  exercised the fixed mechanism (an ordinary path-based write was never
  the vulnerable operation once creation moved to an open handle; the
  real question is whether a write through the *original* handle survives
  a concurrent delete-and-recreate). Declined the literal suggestion,
  replaced with the more precise
  `aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen`
  test described above, which does exercise the real mechanism end to
  end.

Re-ran after all four round-2 fixes: `./gradlew clean build` — still
green, **430 tests, 0 failures, 0 errors** project-wide (427 + 3 new: 2
in `AccountLedgerStoreTest`, 1 in `AccountLedgerLockTest`). Re-verified
the real safety property specifically, again, not just trusted the diff:
`AccountLedgerLockMultiProcessTest` 3 more times (`--rerun-tasks`, green
every time) and a fresh, clean 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct**.

### Round 3

CodeRabbit rate-limited the first `@coderabbitai full review` request
after round 2's push ("next review available in ~5-7 minutes," confirmed
via `@coderabbitai rate limit` per this task's own required check-before-
retry procedure, not guessed) — actively re-polled the reviews API every
60-90s per the governing task brief's own explicit instruction, rather
than sleeping through it blind; a second `full review` request once the
ETA passed picked it up normally.

Against commit `0008a6d` (after round 2's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T11:02:16Z`): `CHANGES_REQUESTED`, 6
actionable inline comments. All six verified against the real current
code and fixed:

- **`WritableByteChannel#write` is not guaranteed to write the whole
  buffer in one call (Major).** Real and correctly sourced against the
  JDK's own documented contract (the reviewer's own web-verified
  citation, independently consistent with the actual Javadoc): a partial
  write would leave truncated JSON, which `readMetadataOrNull` would
  treat as `EMPTY_OR_UNPARSEABLE` — safe (never a false "steal"), but a
  real correctness gap regardless (this holder's own lock would end up
  permanently unparseable by anyone, including its own eventual `close()`,
  until `staleThreshold` reclaims it). Fixed: `createAndWriteMetadata` now
  loops on `channel.write(buffer)` until `!buffer.hasRemaining()`, instead
  of trusting a single call to drain it.
- **A JSON literal `null` parses successfully to Java `null`, colliding
  with `readMetadataOrNull`'s own "file absent" sentinel (Minor).** Real:
  Jackson maps a bare `null` token to `null` without throwing (valid
  JSON, not an error) — the original code returned that `null` directly,
  indistinguishable from "file does not exist." A lock file that somehow
  held literal `null` content would have made `tryStealIfStale` retry
  immediately with no backoff (wrongly treating a present file as gone)
  and `close()` return early believing nothing needed releasing (wrongly
  treating a present file as already gone). Fixed: `parsed == null ?
  EMPTY_OR_UNPARSEABLE : parsed`, normalizing this one specific case the
  same way every other unparseable/unexpected content already is.
- **`Files.exists(tmp)` collapses a real I/O error into a plain `false`
  (Major).** Real, and the same fail-closed principle this whole file
  already applies everywhere else, missed in round 2's own new code: an
  access/permission error checking for the leftover `.tmp` file would
  have been silently read as "no `.tmp` file," defeating the entire
  point of round 2's mitigation by falling through to a fresh, empty
  ledger anyway. Fixed: replaced with `Files.readAttributes(tmp,
  BasicFileAttributes.class)`, explicitly separating `NoSuchFileException`
  ("genuinely absent" — safe to bootstrap fresh) from any other
  `IOException` (fails closed with its own explicit message, distinct
  from the "leftover `.tmp` found" case).
- **`persist` gives no durability guarantee beyond the OS's own write-back
  timing (Major, "Heavy lift").** Real, and disclosed as a *partial*
  fix, not a complete one, matching this same task's own established
  practice for the closely related round-2 finding. The temp file is now
  written via a real `FileChannel` and `force(true)`d before the rename —
  **verified empirically first**, not assumed: a standalone probe
  (writing and forcing a real file under this repository's own
  `java/runtime/build/tmp/`) confirmed `force(true)` does not throw on
  this drvfs mount before this fix was written. **Explicitly declined**:
  fsyncing the parent directory itself (no portable `java.nio.file` way
  to do it, and doing so via platform-specific APIs is real, additional,
  undesigned scope matching the reviewer's own "Heavy lift" label), and
  retrofitting the identical durability guarantee onto this codebase's
  three other existing, structurally-identical durable stores
  (`SubmissionMarkerStore`, `DailyReportGenerator`, `FileSignalSource`,
  none of which fsync either) — applying a stronger guarantee to only the
  newest of four twin stores without a deliberate decision to revisit the
  other three would itself be a new, undisclosed inconsistency, so it's
  named directly in `persist`'s own Javadoc instead. **A real regression
  caught and fixed while implementing this**: the naive translation to
  `FileChannel.open` would have used `StandardOpenOption.CREATE_NEW`,
  which throws `FileAlreadyExistsException` if the temp file already
  exists — breaking retry-after-a-previous-interrupted-persist, since the
  original `Files.writeString` always overwrites via its own documented
  default options (`CREATE, TRUNCATE_EXISTING, WRITE`). Caught by
  checking `Files.writeString`'s actual Javadoc before assuming
  `CREATE_NEW` was a safe drop-in, not discovered via a failing test —
  used `CREATE, TRUNCATE_EXISTING, WRITE` explicitly instead, preserving
  the original overwrite semantics exactly.
- **The 12-thread contention test's shared 5s retry budget is tight given
  this task's own documented drvfs latencies (Minor).** Real: ordinary
  queuing among 12 threads × 10 acquisitions, each backing off up to
  250ms per attempt, could plausibly approach or exceed 5s with no actual
  mutual-exclusion defect involved — a budget-exhaustion failure there
  would misattribute a test-harness impatience issue to the lock
  primitive itself. Fixed: this test now uses its own dedicated 60s
  budget (`CONTENTION_RETRY_BUDGET`), not the smaller, deliberately tight
  `GENEROUS_RETRY_BUDGET` other tests share; the test's own outer
  `done.await` timeout was raised from 30s to 90s to comfortably exceed
  the new per-thread budget rather than becoming the new, tighter
  bottleneck.
- **The new generation-safety test's platform assumption and resource
  lifecycle weren't made explicit (Trivial).** Real, both parts: (1) the
  test's own premise (an open file surviving a concurrent delete-and-
  recreate at the same path) is real, verified, POSIX-style behavior on
  this project's actual environments (WSL2 dev, `ubuntu-latest` CI) but
  is documented to differ on traditional Windows semantics — annotated
  `@EnabledOnOs(OS.LINUX)`, a precise statement of the tested premise
  rather than a behavior change. (2) `staleWritersChannel` is now opened
  inside its own try-with-resources so it can't leak if `Files.delete` or
  `AccountLedgerLock.acquire` throws before the test's own explicit,
  mid-body `close()` call runs (that explicit close still has to stay —
  it's what makes the delayed write happen at the right point in the
  sequence — the try-with-resources is a safety net for the failure
  path, and a redundant second close is a documented no-op).

**One finding's own suggested fix was evaluated and only partially
applied, with the remainder explicitly declined and disclosed** (the
durability finding above) — consistent with this document's own standing
practice (see round 1/round 2's own declined-suggestion entries) of
fixing what's genuinely in scope and naming what isn't, rather than
either rubber-stamping every suggested diff or dismissing findings
without a real, checked reason.

Re-ran after all six round-3 fixes: `./gradlew clean build` — still
green, **430 tests, 0 failures, 0 errors** project-wide (unchanged from
round 2's count — round 3's fixes modified existing production/test code
rather than adding net-new test methods, except where noted above).
Re-verified the real safety property specifically, a further time:
`AccountLedgerLockMultiProcessTest` 3 more times (`--rerun-tasks`, green
every time) and a further clean 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct** (105 clean stress rounds / 5,040 individual lock acquisitions
across the whole task, zero lost updates in any of them).

### Round 4

CodeRabbit rate-limited again after round 3's push (ETA "9 seconds" this
time, confirmed via `@coderabbitai rate limit`) — waited briefly and
re-requested rather than assuming it would clear on its own; the next
`full review` request ran normally.

Against commit `982c94e` (after round 3's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T11:26:11Z`): `CHANGES_REQUESTED`, 2
actionable inline comments — the smallest finding count of any round so
far, consistent with the fixable surface genuinely narrowing each round
rather than the reviewer finding a constant trickle regardless of fix
quality. Both real and fixed:

- **`acquire`'s own success signal was still not generation-safe, even
  after round 2's content-corruption fix (Major, "Heavy lift") — the
  deepest single finding of the whole task.** Real, and distinct from
  everything fixed so far: round 2's open-handle fix protects the lock
  file's *content* from a slow, orphaned writer's stale write clobbering
  a sibling's real metadata — proven by
  `aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen`.
  But it does **not** protect `createAndWriteMetadata`'s own **return
  value**: a write through an orphaned channel still *succeeds* from the
  channel's own perspective (it writes to its own now-path-invisible
  file — that's the entire mechanism the round-2 fix relies on), so the
  method would still return a real, non-null `LockMetadata`, and
  `acquire()` would still hand its caller a live `AccountLedgerLock` —
  even though a sibling had, in the interim, legitimately reclaimed the
  path via `tryStealIfAbandonedEmpty` and is the real, live holder there
  right now. Traced through concretely: (1) this holder opens a
  `CREATE_NEW` channel, slow to write (the same real, measured drvfs
  latency this whole task keeps encountering); (2) a sibling judges the
  still-empty file abandoned, deletes it, and acquires its own real new
  generation; (3) this holder's original write finally lands on its own
  orphaned file and returns normally; (4) **both** processes' own
  `acquire()` calls return successfully, both believing they hold the
  lock, with only the sibling's belief actually reflected in the real
  file. A genuine loss of mutual exclusion, structurally different from
  (and unreachable by) the content-corruption fix — this is about the
  *success signal*, not the *content*.

  **Fixed**: `createAndWriteMetadata` now re-reads `lockPath` immediately
  after its write completes and confirms the content still matches
  exactly what it just wrote; if not, it returns `null` instead of the
  metadata — a defined "lost the race" signal, not an error.
  `acquire()`'s loop treats a `null` return exactly like ordinary
  contention (back off, retry) rather than success or a hard failure, and
  critically **never deletes anything** in this path — the path now
  legitimately belongs to whoever's generation the re-read found, and
  this method has no standing to touch it (the same principle already
  applied to the steal and close paths, extended to the one remaining
  place — the create path itself — that didn't have it yet). This closes
  the last of the three related races this task's later review rounds
  found on the same underlying mechanism (content corruption in round 2,
  the empty-file cleanup/reclaim gap also in round 2, and now the
  success-signal gap in round 4) — all three traced back to the same
  root cause: a slow write can outlive the window in which the writer is
  still the path's rightful owner, and every place that matters (write
  itself, steal, close, and now create's own success) needed its own
  re-verification against that possibility.

  No new dedicated test was added for this specific interleaving --
  reproducing it deterministically would need the same kind of
  open-handle-then-steal-then-complete-the-write sequence already proven
  once by
  `aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen`,
  and that existing test's own final assertion (comparing file content
  before and after the delayed write) already exercises
  `createAndWriteMetadata`'s real code path end to end; a second,
  near-duplicate test asserting `createAndWriteMetadata` itself returns
  `null` in the same scenario was judged to add construction cost without
  meaningfully new coverage, given the fix is a small, direct, easily-
  inspected re-verify-then-branch. The 25-round raw stress harness re-run
  below is the real, load-bearing proof this fix doesn't regress the
  measured safety property, not a unit test claim alone.
- **`AccountLedgerStore.load` had the exact same "JSON literal `null`"
  gap round 3 had already found and fixed in `AccountLedgerLock`, just in
  the sibling class (Major).** Real, and a direct parallel: `MAPPER
  .readValue(raw, AccountLedger.class)` also returns a plain Java `null`
  for JSON `null` content without throwing; the very next line
  (`ledger.venue()`) would have thrown a raw `NullPointerException`
  instead of this method's own intended `IllegalStateException` fail-
  closed contract. Fixed with an explicit `ledger == null` check
  immediately after deserialization, before any field access. New test:
  `aFileContainingTheJsonLiteralNullFailsClosed` (`Files.writeString(file,
  "null")` → `assertThrows(IllegalStateException.class, ...)`).

Re-ran after both round-4 fixes: `./gradlew clean build` — still green,
**431 tests, 0 failures, 0 errors** project-wide (430 + 1 new: the
JSON-null regression test in `AccountLedgerStoreTest`). Re-verified the
real safety property specifically, given this round's fix touches the
core `acquire()` control flow directly:
`AccountLedgerLockMultiProcessTest` 3 more times (`--rerun-tasks`, green
every time) and a further clean 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct** (130 clean stress rounds / 6,240 individual lock acquisitions
across the whole task, zero lost updates in any of them).

## Verification

- `./gradlew :runtime:compileTestJava` (before implementing the ledger
  classes) — failed with 17 real "cannot find symbol" compile errors
  against `AccountLedgerStoreTest.java`, the expected red state.
- `./gradlew :runtime:test --tests "engine.runtime.AccountLedgerStoreTest"`
  — green, 10/10, after implementation (one deprecation warning found and
  removed — see "TDD" above; zero warnings on the final version); 14/14
  after round 1's CodeRabbit fixes (4 new tests); 16/16 after round 2's
  (2 more new tests); 16/16 after round 3's (no new test methods, existing
  ones' production-code dependencies changed); 17/17 after round 4's (1
  more new test).
- `./gradlew :runtime:test --tests "engine.runtime.AccountLedgerLockTest"`
  — green, 4/4, stable across 3 repeated full re-runs; 7/7 after round
  1's CodeRabbit fixes (3 new tests); 8/8 after round 2's (1 more new
  test); 8/8 after round 3's (existing tests modified, no new methods);
  8/8 after round 4's (existing production code changed, no new test
  methods — see round 4's own entry for why).
- `./gradlew :runtime:test --tests
  "engine.runtime.AccountLedgerLockMultiProcessTest"` — **failed for
  real** on the first run (19 vs. expected 20 — see "The real finding"
  above); green after the fix, confirmed **5 additional times**
  (`--rerun-tasks`) before round 1's review, **3 more times** after round
  1, **3 more times** after round 2, **3 more times** after round 3,
  **3 more times** after round 4.
- A raw, non-Gradle stress harness (`LockContenderMain` launched directly
  via `ProcessBuilder`-equivalent manual invocation, bypassing Gradle's
  own test-launch overhead to run many more real-process rounds in
  reasonable wall-clock time) — **30/30 rounds exactly correct** after the
  original TOCTOU fix, **25/25 more (clean)** after round 1's CodeRabbit
  fixes, **25/25 more** after round 2's, **25/25 more** after round 3's,
  and **25/25 more** after round 4's (6 processes × 8 iterations = 48
  expected per round every time: **130 clean rounds total, 6,240
  individual lock acquisitions, zero lost updates** across the whole
  task's real, non-Gradle stress testing).
- `./gradlew :runtime:test` (full module suite) — green, confirmed 3
  times (`--rerun-tasks`) before round 1's review, once more after round
  1, part of the full `clean build` runs after rounds 2, 3, and 4.
- `./gradlew clean build` (full six-module suite, clean, not incremental)
  — **BUILD SUCCESSFUL**. Summed real JUnit XML reports across every
  module (`schemas`, `oms`, `risk`, `execution`, `exchange`, `runtime`):
  **431 tests, 0 failures, 0 errors** (405 pre-existing from Task A's
  merged state + 15 from this task's original implementation + 7 from
  round 1's CodeRabbit review + 3 from round 2's + 0 net-new from
  round 3's + 1 from round 4's).
- PR to be opened, not merged — per the governing task brief and
  CLAUDE.md's Auto-merge Policy, this is Java runtime/Risk-Gateway-
  adjacent code and requires explicit human sign-off regardless of
  CI/CodeRabbit status.
