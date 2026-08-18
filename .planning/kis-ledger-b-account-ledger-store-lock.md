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
  brief's literal text, not silently corrected. **Superseded by round 2**
  (see that round's own entry below): `Files.createFile` plus a separate
  `Files.writeString` turned out to have a real correctness gap of its
  own (a slow writer's own stale, path-based write could clobber a
  sibling's real metadata) — replaced with a single atomic
  `Files.newByteChannel(..., CREATE_NEW, WRITE)` call, metadata written
  through that same handle. Left here as an accurate record of the
  original judgment call and why it was made, not rewritten to pretend
  `Files.createFile` was never used — a real Minor finding, a further
  real CodeRabbit review round, flagged this bullet (and
  `AccountLedgerLock`'s own class Javadoc, separately corrected there) as
  having drifted out of sync with round 2's real code change; the class
  Javadoc is now corrected to describe the current mechanism accurately
  (a single atomic `Files.newByteChannel(..., CREATE_NEW, WRITE)` call,
  metadata written through that same handle), and this bullet gets this
  pointer rather than a silent rewrite.
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

### Round 5

CodeRabbit rate-limited a first request after round 4's push; the
governing coordinator directly checked in on this wait partway through
(the real ETA, ~39 minutes, was reported back rather than assumed clear
after time passed) — re-checked `@coderabbitai rate limit` on request,
confirmed genuinely clear ("Reviews are available now"), then requested
`full review` for real.

Against commit `aa2f171` (after round 4's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T12:31:15Z`): `CHANGES_REQUESTED`, 3
actionable inline comments. All three verified against the real current
code and fixed — including a real, self-introduced regression in round
4's own fix, caught here rather than shipped:

- **Round 4's own re-verify fix conflated `READ_FAILED` with a genuine
  generation mismatch, and that specific conflation is a real
  regression, not just an incompleteness (Major, "Heavy lift").** Traced
  through concretely: round 4 made `createAndWriteMetadata` return
  `null` ("lost the race") whenever the post-write re-read didn't
  `equals()` the metadata just written — but `readMetadataOrNull` can
  also return `READ_FAILED` for a transient read failure, which is
  never `equals()` to real metadata either, so it fell into the exact
  same branch. A transient read failure proves nothing about who owns
  the path -- most likely *this* holder still does, having just created
  and written the file inside the very same method call. Treating it as
  "lost the race" (the round-4 fix's own behavior) left this holder's
  own real, live lock file behind on disk while reporting failure to its
  own caller. Because that file genuinely holds this holder's own live
  pid and a just-recorded `acquiredAt`, the *next* `acquire` attempt's
  own `tryStealIfStale` call would correctly (by this class's own rules)
  judge it *not* stale — from the outside it looks like a perfectly
  healthy, fresh lock — so this process ends up self-blocked behind its
  own abandoned-in-place file until `staleThreshold` elapses, and every
  *other* waiter is blocked for that same window on a lock protecting a
  shared risk ledger, for no real reason. **Fixed**: `READ_FAILED` is now
  handled in its own branch, separate from a genuine mismatch — cleans
  up only this holder's own, re-verified generation (a new helper,
  `deleteIfStillOwnGeneration`, reusing the exact delete-only-if-content-
  still-matches-exactly discipline `tryStealIfStale` already established)
  before reporting the lost race, so the next attempt starts from a
  genuinely clean path rather than contending with its own ghost. No new
  dedicated test added for this exact interleaving, for the same reason
  given in round 4's own entry (the existing generation-safety test
  already exercises the real code path end to end, and reproducing this
  specific transient-read-failure timing deterministically would need a
  fault-injection seam this class doesn't have and this task didn't add
  elsewhere either) — the stress-harness re-run below is the load-bearing
  proof this fix doesn't regress anything, not a unit test claim alone.
- **`FAIL_ON_TRAILING_TOKENS` is disabled by default in Jackson, and
  `AccountLedgerStore`'s `MAPPER` never enabled it (Major).** Real,
  confirmed against Jackson 2.18.9's own documented default (disabled
  for backward compatibility across the whole 2.x line) rather than
  assumed: a corrupted ledger file holding a valid `AccountLedger` object
  followed by trailing garbage (or a second, different JSON value) would
  otherwise parse "successfully," silently ignoring everything after the
  first complete value — directly undermining this class's own
  fail-closed contract on corrupt input. Fixed: enabled `
  DeserializationFeature.FAIL_ON_TRAILING_TOKENS` on `AccountLedgerStore`'s
  `MAPPER`. **Proactively extended to `AccountLedgerLock`'s own,
  separately-configured `MAPPER` too** — not itself flagged by
  CodeRabbit this round, but the identical Jackson-default reasoning
  applies equally to `LockMetadata` parsing, and leaving it unfixed there
  after fixing it in the sibling class would have been a real,
  undisclosed inconsistency of exactly the kind this task's own review
  rounds keep finding and closing. New tests in
  `AccountLedgerStoreTest`: a valid ledger followed by trailing JSON
  `null`, and two concatenated valid ledger objects — both must fail
  closed.
- **The `build.gradle.kts` comment explaining the 2.18.9 version bump
  claimed this module "never calls readValue()," which Task B's own new
  code made false (Minor).** Real, and a genuine documentation-accuracy
  regression this task itself introduced without noticing: the comment's
  original claim was accurate when written (about `BingXPriceFeed`'s own
  parsing of untrusted external BingX JSON specifically, which still
  only ever calls `readTree()`, unchanged) but `AccountLedgerStore`/
  `AccountLedgerLock` do now call `readValue()` -- just never on
  untrusted external network input, only on this module's own internally
  -written JSON files, so the CVE-2026-54515 reasoning the comment exists
  to explain is unaffected; only the specific "never readValue()"
  sentence needed correcting. Fixed by narrowing that claim to
  `BingXPriceFeed` specifically and stating the correction explicitly
  (not silently rewritten) so a future reader isn't misled about this
  module's real Jackson call surface during some future security review
  — precisely the risk the reviewer named.

Re-ran after all three round-5 fixes: `./gradlew clean build` — still
green, **433 tests, 0 failures, 0 errors** project-wide (431 + 2 new: 2
in `AccountLedgerStoreTest`). Re-verified the real safety property
specifically, again given this round's fix touches `acquire()`'s own
control flow: `AccountLedgerLockMultiProcessTest` 3 more times
(`--rerun-tasks`, green every time) and a further clean 25-round raw
stress harness run (6 processes × 8 iterations, 48 expected per round) —
**25/25 rounds exactly correct** (155 clean stress rounds / 7,440
individual lock acquisitions across the whole task, zero lost updates in
any of them).

### Round 6

Two rate-limit waits happened between round 5 and round 6, both handled
per this task's own established discipline: checked `@coderabbitai rate
limit` for real rather than assuming clear from elapsed time (twice this
found it genuinely still limited with an ETA — reported back and
stopped, per the governing coordinator's own explicit instruction on
both occasions, rather than sleeping through a long wait), and twice
confirmed genuinely clear before re-requesting.

Against commit `78aa832` (after round 5's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T13:38:15Z`): `CHANGES_REQUESTED`, 2
actionable inline comments — both real, both fixed. The finding count
continuing to shrink each round (7 → 4 → 6 → 2 → 3 → 2) reflects the
fixable surface genuinely narrowing, not a plateau. **Correction, a
further real CodeRabbit review round**: this sequence originally read
"7 → 6 → 4 → 2 → 3 → 2" here, transposing rounds 2 and 3's own real
counts (4 and 6 respectively, confirmed directly against each round's
own "Actionable comments posted" line in this same document) — a real
transcription error in this summary sentence, not in either round's own
detailed entry above, which were always correct. Fixed in place.

- **`AccountLedger` didn't reject two reservations sharing the same
  `clientOrderId` (Trivial).** Real, and the same reasoning already
  applied to `LedgerReservation.notional`'s own positivity check: this
  record is the single structural enforcement point regardless of which
  identifier Task C eventually keys reservations by, and a duplicate is
  dangerous in either direction -- double-counted in
  `Σ(reservations.notional)` (understating available capital, blocking
  legitimate orders) if never resolved, or under-released (understating
  real committed exposure, the more dangerous direction) if only one of
  the two duplicate entries is removed on release. Fixed: the compact
  constructor now rejects a `reservations` list whose distinct-by-
  `clientOrderId` count doesn't match its size. New tests: a direct
  `AccountLedger` construction with two same-`clientOrderId` reservations
  throws `IllegalArgumentException`; a hand-written ledger *file* holding
  the same duplicate fails closed via `AccountLedgerStore.load` (proving
  the store needs no special-case duplicate-detection logic of its own,
  same pattern already proven for the notional check).
- **Two places' documentation still described the pre-round-2
  `Files.createFile` + separate `Files.writeString` mechanism as current,
  after round 2 replaced it with a single atomic `Files.newByteChannel`
  call (Minor).** Real: `AccountLedgerLock`'s own class-level Javadoc
  ("Primitive," "Lock file content," "Contention and staleness," and
  "Deviation" sections) was written in round 1 and never updated when
  round 2 changed the actual mechanism -- `createAndWriteMetadata`'s own
  (later-added) Javadoc was accurate, but the class-level doc a reader
  would see first was not, a real internal inconsistency. This document's
  own "Judgment calls" section had the identical staleness. Fixed: the
  class Javadoc's four affected sections now describe the real, current
  mechanism; this document's own affected bullet is deliberately **not**
  silently rewritten to pretend `Files.createFile` was never used (it
  really was, in round 1) -- it keeps its original text as an accurate
  historical record and gains a pointer to round 2's real supersession,
  matching this document's own established practice elsewhere (e.g. round
  2's own entry for round 1's original TOCTOU fix) of keeping superseded
  reasoning visible rather than erasing it.

Re-ran after both round-6 fixes: `./gradlew clean build` — still green,
**435 tests, 0 failures, 0 errors** project-wide (433 + 2 new, both in
`AccountLedgerStoreTest`). This round's changes were a record-level
validation and pure documentation, not a change to `AccountLedgerLock`'s
own lock-acquisition control flow, but the real safety property was
re-verified anyway rather than assumed unaffected:
`AccountLedgerLockMultiProcessTest` green (`--rerun-tasks`), part of the
full `clean build` run above.

### Round 7

One more rate-limit wait between round 6 and round 7, handled the same
way as the previous ones: checked `@coderabbitai rate limit` for real
(found genuinely still limited, reported the real ETA back and stopped
per the governing coordinator's own standing instruction), then
confirmed genuinely clear before re-requesting. Also directly observed,
this round, the exact failure mode this task's own verification
discipline exists to catch: right after requesting review of commit
`b53fc85`, the `CodeRabbit` commit-status check flipped to "Review
completed" within seconds, while the reviews API still showed no review
object for that commit at all -- confirming the commit-status badge is
not reliable evidence by itself, not just as a documented risk but as a
real, observed event during this task.

Against commit `b53fc85` (after round 6's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T14:40:37Z`): `CHANGES_REQUESTED`, 2
actionable inline comments, both real, both fixed:

- **A transient (non-`FileAlreadyExistsException`) `IOException` during
  lock creation used to be promoted straight to `IllegalStateException`
  on its very first occurrence, consuming none of `totalRetryBudget`
  (Trivial).** Real: this class's own Javadoc already documents measured
  500ms+ transient I/O latency on this repository's real drvfs mount
  under contention -- exactly the kind of environment characteristic a
  bounded retry, not an immediate hard failure, exists to absorb, and
  every *other* failure mode in this class's `acquire` loop (ordinary
  contention, a lost race, an abandoned-empty reclaim) already retries
  within budget rather than failing on the first occurrence. Fixed: a
  transient `IOException` now backs off and retries exactly like ordinary
  contention, consuming `totalRetryBudget` normally; only once the budget
  is genuinely exhausted does `acquire` throw, with the *real* last
  transient failure attached as the thrown `IllegalStateException`'s
  cause (via `initCause`) rather than a generic timeout message with no
  underlying reason. No dedicated new test added -- the existing
  retry-budget-exhaustion test already proves the budget-exhaustion path
  itself; reliably forcing a *transient* (not permanent, not
  `FileAlreadyExistsException`) `IOException` from a real filesystem
  operation deterministically would need a fault-injection seam this
  class doesn't have, and did not gain one elsewhere in this task either,
  for the same reasons already given for the narrower, similarly-shaped
  gap disclosed in `createAndWriteMetadata`'s own cleanup-on-failure path.
- **`readMetadataOrNull` never validated that a successfully-parsed
  `LockMetadata` was actually complete (Major).** Real, and a genuine gap
  in the same family as round 5's JSON-`null` fix: Jackson can silently
  fill a missing record component with its default/null instead of
  throwing -- `{"pid":123}` alone deserializes "successfully" to a
  `LockMetadata` with a `null` `hostname()`/`acquiredAt()`. Traced
  through concretely: `tryStealIfStale`'s own `Duration.between(metadata
  .acquiredAt(), Instant.now())` call would then throw a raw
  `NullPointerException` that -- unlike every deliberately-handled outcome
  in this file -- does not flow back through `acquire`'s own retry loop
  at all; it propagates straight out, crashing the whole acquisition
  attempt hard. Fixed: `readMetadataOrNull` now checks completeness
  immediately after a successful parse (`hostname()`/`acquiredAt()`
  non-null, and, defensively, `pid() > 0` -- a real PID is never zero or
  negative on Linux, and while `pid` is a primitive `long` so it can
  never literally be "null" the way the reference-typed fields can, a
  missing JSON field still silently defaults it to `0`, which this check
  also now catches) -- an incomplete result is treated as
  `EMPTY_OR_UNPARSEABLE` and routed through the exact same mtime-based
  `tryStealIfAbandonedEmpty` reclaim path a genuinely empty file already
  uses, rather than ever being treated as trustworthy metadata. New test:
  `acquireStealsAnAgedLockFileWithIncompleteMetadataRatherThanThrowingAnNpe`
  -- fabricates `{"pid":123}` (valid JSON, missing `hostname`/
  `acquiredAt` entirely), backdates it past `staleThreshold`, and confirms
  it's reclaimed cleanly (the point of the fix is precisely that this
  does *not* throw).

Re-ran after both round-7 fixes: `./gradlew clean build` — still green,
**436 tests, 0 failures, 0 errors** project-wide (435 + 1 new, in
`AccountLedgerLockTest`). Re-verified the real safety property
specifically, given this round's first fix touches `acquire()`'s own
retry-budget control flow directly:
`AccountLedgerLockMultiProcessTest` 3 more times (`--rerun-tasks`, green
every time) and a further clean 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct** (180 clean stress rounds / 8,640 individual lock acquisitions
across the whole task, zero lost updates in any of them).

### Round 8

One more rate-limit wait between round 7 and round 8 (checked
`@coderabbitai rate limit` for real, found genuinely still limited,
reported the real ETA back and stopped, then confirmed genuinely clear
before re-requesting — the same, by-now-established discipline).

Against commit `056e684` (after round 7's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T15:44:41Z`): `CHANGES_REQUESTED`, 2 fresh
actionable comments plus 1 **duplicate** comment (CodeRabbit's own label
for re-flagging a previously-raised, still-unresolved concern) — all
three real, all three addressed:

- **The duplicate: `createAndWriteMetadata`'s write-failure cleanup path
  still deleted by bare path, not by verified generation (Major) — a
  real, previously-disclosed-and-deliberately-declined residual gap,
  revisited and closed rather than declined again.** Round 2 disclosed
  this exact gap when it first built the cleanup-on-write-failure logic
  (`Files.deleteIfExists(lockPath)` unconditionally) and explicitly
  judged it acceptable to leave unguarded: closing it would have meant
  building new generation-verification machinery for a precondition (a
  genuine I/O failure during the write itself, not just slowness)
  materially rarer than the cases this file's other fixes address.
  CodeRabbit re-raised the identical concern this round, tracing the same
  real sequence round 2's own disclosure already named (a slow write
  fails, a sibling reclaims the still-empty file via
  `tryStealIfAbandonedEmpty`, this cleanup then deletes the sibling's
  real, live lock, a third process acquires immediately, two processes
  end up in the critical section at once — a real lost update on the
  shared risk ledger). **What changed the calculus, and what makes this
  a real re-evaluation rather than reflexive compliance**: round 5 built
  `deleteIfStillOwnGeneration` for an unrelated purpose (the `READ_FAILED`
  case in `createAndWriteMetadata`'s own post-write re-verify). That
  helper already implements exactly the "delete only if content still
  matches my own metadata exactly" rule this cleanup path needs — so the
  "would need new machinery" half of round 2's original reasoning no
  longer holds; reusing an already-built, already-tested helper costs
  essentially nothing, which is a materially different cost/benefit
  calculus than round 2 faced. Fixed: `metadata` is now declared outside
  the `try` block (assigned as soon as it's constructed, before the write
  even starts) so the `catch` block can see it; cleanup now calls
  `deleteIfStillOwnGeneration(lockPath, metadata)` instead of the bare
  `Files.deleteIfExists`, guarded by a `metadata != null` null check for
  the (currently unreachable, since nothing in `LockMetadata`'s own
  construction can throw) case where the exception happened before
  `metadata` was ever assigned.
- **A `try`/`catch` around `Files.newByteChannel` that only rethrows the
  exact same exception has no behavioral effect (Trivial).** Real and
  simple: the `catch (FileAlreadyExistsException e) { throw e; }` block
  added when this method's create step first moved to
  `Files.newByteChannel` (round 2) was never anything but a no-op wrapper
  — removed, with the same explanatory comment (why
  `FileAlreadyExistsException` is deliberately left to propagate
  unhandled here) preserved directly above the now-unwrapped call.
- **Two back-to-back Javadoc blocks in `AccountLedgerLockTest` both ended
  up attached to a field declaration, silently discarding the first
  one — leaving a test method with no rendered documentation at all
  (Trivial).** Real: round 7's own new `CONTENTION_RETRY_BUDGET` field
  Javadoc was inserted directly above the pre-existing method Javadoc for
  `acquireProvidesRealMutualExclusionAcrossManyThreads`, and Javadoc
  tooling attaches a run of consecutive comment blocks only to whichever
  declaration immediately follows the *last* one — so both blocks ended
  up documenting the field, and the method's own original Javadoc was
  silently orphaned. Fixed by moving the method's own Javadoc back to sit
  directly above the method (merged into one block with a note explaining
  the reordering, rather than left ambiguous about which paragraph
  belongs to which declaration).

Re-ran after all three round-8 fixes: `./gradlew clean build` — still
green, **436 tests, 0 failures, 0 errors** project-wide (unchanged from
round 7's count — this round's fixes were a refactor reusing an existing,
already-tested helper and two documentation/dead-code cleanups, not new
behavior needing new test coverage). Re-verified the real safety property
specifically, given the first fix directly changes
`createAndWriteMetadata`'s own cleanup behavior:
`AccountLedgerLockMultiProcessTest` 3 more times (`--rerun-tasks`, green
every time) and a further clean 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct** (205 clean stress rounds / 9,840 individual lock acquisitions
across the whole task, zero lost updates in any of them).

### Round 9

One more rate-limit wait between round 8 and round 9, same by-now-
established discipline (checked `@coderabbitai rate limit` for real,
found genuinely still limited, reported the real ETA back and stopped
per the governing coordinator's own standing instruction, confirmed
genuinely clear before re-requesting).

Against commit `edfd4aa` (after round 8's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T16:48:58Z`): `CHANGES_REQUESTED`, 2
actionable comments, both real, both fixed:

- **The delete/read-path helper methods (`tryStealIfStale`,
  `tryStealIfAbandonedEmpty`, `lastModifiedTimeOrNull`,
  `deleteIfStillOwnGeneration`) still wrapped a genuine I/O failure in
  `IllegalStateException` and threw it directly, bypassing `acquire`'s
  own retry budget entirely (Major) — the exact same class of bug round
  7 already fixed for the *creation* path, now found (correctly) to
  still be present on the *steal/cleanup* path.** Real, and a precise,
  consistent extension of round 7's own reasoning: `tryStealIfStale` is
  called from `acquire`'s `catch (FileAlreadyExistsException e)` block,
  a *sibling* of `acquire`'s own `catch (IOException e)` handler, not
  nested inside it -- so an `IllegalStateException` thrown from inside
  `tryStealIfStale` (or the helpers it calls) propagated straight out of
  `acquire()` on its very first occurrence, consuming none of
  `totalRetryBudget`, even though this class's own Javadoc already
  documents the exact same measured 500ms+ transient I/O latency on this
  repository's real drvfs mount that motivated round 7's fix for the
  creation path. Fixed: all four helper methods now declare
  `throws IOException` and let a genuine (non-`NoSuchFileException`)
  delete/read failure propagate as a real `IOException` instead of
  wrapping it; `acquire()`'s own `catch (FileAlreadyExistsException e)`
  block now wraps its `tryStealIfStale` call in its own
  `catch (IOException stealFailure)`, folding a caught failure into the
  exact same `lastTransientFailure`-tracking backoff-and-retry path
  round 7 already built for the creation side -- one shared mechanism
  for both halves of `acquire`'s own retry loop, not two divergent ones.
  `deleteIfStillOwnGeneration`'s two call sites inside
  `createAndWriteMetadata` needed no new handling of their own: one is
  already inside that method's own `try` block (so a propagated
  `IOException` is naturally caught by its existing enclosing `catch`),
  and the other (the cleanup-on-failure path itself) had its inner
  `catch (RuntimeException cleanupFailure)` narrowed to
  `catch (IOException cleanupFailure)` to match the helper's new,
  precise `throws` clause. No dedicated new test added, for the same
  reason already given for the structurally identical gap round 7 left
  undocumented by a dedicated test: reliably forcing a genuine
  (non-`NoSuchFileException`) delete/read failure from a real filesystem
  operation deterministically would need a fault-injection seam this
  class doesn't have anywhere; the 25-round raw stress harness re-run
  below is the real, load-bearing proof this refactor doesn't regress
  the measured safety property.
- **`acquireDoesNotStealAFreshEmptyLockFile` asserted only the thrown
  exception's *type*, not that it actually represented genuine retry-
  budget exhaustion specifically (Trivial).** Real: given
  `AccountLedgerLock`'s own helper methods could (before this same
  round's first fix) throw `IllegalStateException` from several
  genuinely different failure paths, asserting only
  `IllegalStateException.class` didn't actually prove this test's own
  named premise -- that this scenario correctly exhausts its retry
  budget without ever stealing, rather than failing for some unrelated
  reason that happens to also throw the same exception type. Fixed:
  now captures the thrown exception and asserts its message contains
  the real retry-budget-exhaustion wording and names the lock path --
  the same message-validation pattern
  `acquireThrowsRatherThanHangingWhenTheRetryBudgetIsExhausted` already
  uses.

Re-ran after both round-9 fixes: `./gradlew clean build` — still green,
**436 tests, 0 failures, 0 errors** project-wide (unchanged from round
8's count — this round's fixes were a signature/propagation refactor
across four existing helper methods plus a strengthened assertion on an
existing test, not new behavior needing a new test method). Re-verified
the real safety property specifically, given the first fix directly
changes `acquire()`'s own steal-path control flow:
`AccountLedgerLockMultiProcessTest` 3 more times (`--rerun-tasks`, green
every time) and a further clean 25-round raw stress harness run (6
processes × 8 iterations, 48 expected per round) — **25/25 rounds exactly
correct** (230 clean stress rounds / 11,040 individual lock acquisitions
across the whole task, zero lost updates in any of them).

### Round 10

Against commit `9cdfd70` (after round 9's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T17:55:41Z`): `CHANGES_REQUESTED`, 4
actionable comments, all four real, all four addressed -- including one
that led to a genuinely new empirical discovery deeper than what was
literally asked for:

- **A round-count summary sentence in this very document transposed
  rounds 2 and 3's own real counts (Trivial).** Real: "7 → 6 → 4 → 2 → 3
  → 2" should read "7 → 4 → 6 → 2 → 3 → 2" (round 2 had 4 actionable
  comments, round 3 had 6 -- confirmed directly against each round's own
  "Actionable comments posted" line, both already correctly recorded in
  each round's own detailed entry; only this one summary sentence had
  them swapped). Fixed in place, with a correction note rather than a
  silent edit, matching this document's own established practice.
- **`AccountLedgerStore.persist`'s own round-3 fix (overwriting a leftover
  `.tmp` file from an earlier interrupted attempt, instead of the naive
  `CREATE_NEW` that would reject it) was never actually exercised by a
  dedicated test proving that specific claim (Trivial).** Real: existing
  tests covered the ordinary case and the `AtomicMover`-failure fallback,
  but nothing wrote a stale `.tmp` file by hand first and confirmed
  `persist` still succeeds against it. New test:
  `persistOverwritesALeftoverTmpFileFromAnEarlierInterruptedAttempt`.
- **`close()` was not idempotent (Major).** Real: nothing prevented a
  caller from invoking `close()` twice on the same instance (this
  project's own try-with-resources convention never does so on its own,
  but the `AutoCloseable` contract itself doesn't forbid a caller from
  doing so directly, unlike `Closeable`'s own stricter one). A second
  call would re-examine `lockPath` -- and if a sibling had since
  legitimately acquired a brand new generation there (a real, plausible
  sequence: this instance's own first `close()` already ran and deleted
  its own file, vacating the path for anyone), the second call would log
  a real `ERROR` reading exactly like a genuine cross-process safety
  event, purely as an artifact of being called twice, even though the
  first call already completed correctly and nothing was ever actually
  wrong. Fixed: a `closed` field now short-circuits any call after the
  first to a pure no-op. Deliberately set only *after* the underlying
  `doClose()` helper returns normally, not up front -- if some genuinely
  unanticipated exception ever escaped uncaught (everything this class's
  own logic can throw is already handled inside it), a subsequent
  `close()` call must still be able to retry rather than silently,
  permanently no-op-ing on a cleanup that never actually happened. New
  test: `closeIsIdempotentAndNeverReExaminesTheFileOnARepeatCall`.
- **`staleThreshold` has no enforced relationship to real filesystem mtime
  precision, and the review's own suggested regression test should use a
  threshold below the observed precision rather than relying on the
  existing 60-second backdated-timestamp test (Major).** Investigated
  empirically rather than accepted or dismissed on the stated premise
  alone -- and the investigation found something real, but structurally
  different from (and more consequential than) the literal framing.
  First, the literal claim: a dedicated probe (repeated rapid writes to a
  real file under this repository's own `java/runtime/build/tmp/`)
  measured this drvfs mount's actual mtime precision at a real,
  positively-confirmed sub-3ms resolution -- 200 back-to-back writes with
  no artificial delay produced 199 *distinct* mtimes, zero collisions.
  This project's own realistic `staleThreshold` values (this task's own
  tests already use values as low as 50ms; the governing plan's proposed
  real default is ~30s) sit far above that measured floor, so the
  literal "mtime precision" framing does not describe a real, currently
  reachable risk on this project's actual filesystem or usage.

  **But testing the literal framing directly surfaced a real, different,
  more serious problem**: running the raw multi-process stress harness
  with a deliberately pathological `staleThreshold=1ms` against a real
  ~15ms hold time reliably lost real increments on every run (well below
  the expected 48 out of 48; several individual contender processes even
  exited with real errors). The actual mechanism has nothing to do with
  mtime precision at all: it is `acquire`'s own steal logic working
  exactly as documented -- when `staleThreshold` is shorter than a
  legitimate holder's own real critical-section duration, a waiter
  correctly-by-the-rules steals the still-live holder's lock out from
  under it, and both processes end up concurrently "inside the lock"
  from their own perspective. A genuine mutual-exclusion violation, not a
  bug in this class's own code -- a real, disclosed caller-contract
  requirement on `staleThreshold` itself. Reproduced deterministically
  (two real threads, controlled timing, not the raw harness's own timing
  variance) by a new test,
  `aStaleThresholdShorterThanARealHoldersOwnCriticalSectionCausesARealMutualExclusionViolation`,
  confirmed stable across 5 repeated runs. **Deliberately not "fixed"
  with an enforced minimum `staleThreshold`**: the real minimum any given
  deployment needs depends entirely on how long *that deployment's own
  real critical sections* can legitimately run -- a property only a
  caller (Task C, not yet built) can know, not something this primitive
  can validate in advance without inventing an arbitrary constant unmoored
  from any real, justified number (the same reasoning this document has
  already applied to declining a hard-coded fsync/backup guarantee in
  earlier rounds). Documented instead as an explicit, real caller
  contract in the class Javadoc, citing both the raw-harness discovery
  and the deterministic reproduction.

Re-ran after all four round-10 fixes: `./gradlew clean build` — still
green, **439 tests, 0 failures, 0 errors** project-wide (436 + 3 new: 1
in `AccountLedgerStoreTest`, 2 in `AccountLedgerLockTest`). Re-verified
the real safety property specifically, at this task's own *realistic*
threshold values (not the deliberately pathological one used only to
prove the new finding above): `AccountLedgerLockMultiProcessTest` 3 more
times (`--rerun-tasks`, green every time) and a further clean 25-round
raw stress harness run (6 processes × 8 iterations, 48 expected per
round, `staleThreshold=30s`) — **25/25 rounds exactly correct** (255
clean stress rounds / 12,240 individual lock acquisitions across the
whole task, zero lost updates in any of them, at every realistic
threshold this task has ever actually configured for correctness
testing).

### A real tooling mistake after round 10, caught by the governing coordinator, not by me

After pushing round 10's fixes (commit `ee7c8c4`), I posted
`@coderabbitai rate limit` twice (18:18:41Z and 18:28:27Z) and, both
times, concluded from my own polling that CodeRabbit had gone silent --
no reply after several minutes of checking. That conclusion was wrong,
and I reported it as a real finding ("CodeRabbit did not respond") when
it was actually my own tooling error. The coordinator checked
independently and found both queries had gotten real replies within
single-digit seconds (18:18:47Z and 18:28:31Z), exactly as every prior
round in this task had behaved.

**Root cause, confirmed by direct inspection, not guessed**: my polling
calls used `gh api repos/{owner}/{repo}/issues/100/comments` without
`--paginate`. That endpoint paginates at 30 items per page, oldest
first, and by this point in the task PR #100 had accumulated **69**
comments total across 10 review rounds. An unpaginated call therefore
returns only the **oldest** 30 -- the newest comments (including every
comment I had just posted, and every reply to them) were never in the
page I fetched at all. My `select(.id > X)` filter had nothing to search
against; it wasn't that the filter was wrong, it's that the reply I was
filtering for wasn't in the dataset to begin with. Verified directly:
`gh api repos/ckrhehfl/trading-engine/issues/100/comments --paginate --jq
'length'` returns 69; the same call without `--paginate` returns (at
most) 30, all older than both of my post-round-10 queries and their
replies.

This is a genuinely different failure mode from the earlier, already-
disclosed commit-status-badge unreliability (round 7's entry above) --
that was the *data source itself* (the badge) being unreliable evidence.
This is a case where I asked the *right* source (the reviews/comments
API, not the badge) and still missed the real answer, because of how I
queried it, not because the API lied. Both are now disclosed, for the same reason:
a future session reading this file should know both failure modes are
real and have actually happened on this task, not just be warned about
one of them in the abstract.

**Fix, applied going forward for the rest of this task**: every
`issues/.../comments` poll now uses `--paginate` (or otherwise confirms
it is reading the newest page, not assuming an unpaginated call is
sufficient) -- confirmed working immediately after this fix by
correctly finding a genuine, real rate-limit reply
(id=5303659046, 18:35:07Z, "next review available in 12 minutes") that
an unpaginated call would again have missed.

### Round 11

Against commit `1b3eda3` (after round 10's fixes plus the pagination-
mistake disclosure above were both pushed — a real review confirmed via
the GitHub reviews API to target this exact commit sha, `submitted_at:
2026-08-15T18:53:46Z`): `CHANGES_REQUESTED`, 3 actionable comments, all
three real, all three fixed:

- **A markdown heading in this very document broke MD022 (Trivial) —
  and, worse than the lint violation alone, the heading text itself was
  silently truncated.** Real, and a genuine self-inflicted mistake, not
  just a style nit: the round-10 pagination-mistake heading above
  (`### A real tooling mistake after round 10, caught by the governing
  coordinator, not by me`) had been wrapped across two source lines when
  written. Markdown headings are single-line constructs — only the first
  line (`### A real tooling mistake after round 10, caught by the
  governing`) was actually parsed as the heading; the second line
  (`coordinator, not by me`) silently became an ordinary paragraph
  immediately following it with no blank line, which is what MD022
  actually caught. Fixed by joining the heading onto one physical line
  with a blank line after it, matching every other heading in this
  document.
- **`tryStealIfStale`'s own Javadoc (around line 511) told callers to
  retry `Files#createFile` — a method this class stopped calling
  entirely when it moved to `Files.newByteChannel(lockPath, CREATE_NEW,
  WRITE)` (documented in this class's own Javadoc, lines 56/132-138).**
  Real: confirmed by grep that `createAndWriteMetadata` (the actual
  retry target) calls `Files.newByteChannel`, not `Files.createFile`,
  anywhere in this file. This specific reference was describing *current*
  retry behavior (what a caller should do right now), not — unlike the
  file's many other `Files#createFile` mentions — narrating past bug
  history from before that refactor, so it was a real, live inaccuracy
  and needed fixing; those other historical mentions were checked and
  left alone, since changing them would misrepresent what was actually
  investigated at the time. Fixed by pointing the Javadoc at
  `{@link #createAndWriteMetadata}` instead.
- **The round-10 deterministic mutual-exclusion test had a thin real
  timing margin (Minor, but a legitimate flakiness risk, not just
  style).** Real: `holderRealHoldMillis = 150` combined with the
  waiter's own 40ms wait before attempting to steal
  (`pathologicallySmallStaleThreshold` 10ms + a 30ms buffer) left only
  ~110ms for the entire steal operation — read metadata, judge
  staleness, delete, create + write new metadata, re-verify — to finish
  before the holder legitimately released. This class's own Javadoc
  already documents real, measured 500ms+ transient write latency on
  this project's actual drvfs mount under contention; a 110ms margin
  could plausibly be too thin on a slow or loaded machine, which would
  fail this test for a reason unrelated to the real bug it exists to
  prove. Fixed by raising `holderRealHoldMillis` to 1,000ms (a ~960ms
  margin), with a comment explaining why, rather than shortening the
  waiter's own wait (which would weaken the "holder is still genuinely,
  legitimately active" guarantee the test's whole premise depends on).

Re-ran after all three round-11 fixes: `./gradlew clean build` — still
green, **439 tests, 0 failures, 0 errors** project-wide (no new test
methods this round — a doc heading fix, a Javadoc reference fix, and a
timing-constant change to an existing test). Re-verified the real safety
property specifically: `AccountLedgerLockTest` (11/11, including the
retimed test) and `AccountLedgerLockMultiProcessTest` both green as part
of the same `clean build` run.

### A real gh api pagination mistake, again — this time caught by the
governing coordinator's own independent check, not by me

After round 11's fixes were pushed (commit `4a78e63`), I checked the
rate limit, saw a 47-minute ETA, and reported that back per the standing
"report a long ETA and stop" instruction. The coordinator independently
re-checked and found both my rate-limit queries had gotten real, fast
replies — the same class of mistake as the one disclosed above, not a
new one. This time the report itself was correct (the reported ETA and
ETA-derived ready time were accurate), so no correction to a false
"CodeRabbit went silent" claim was needed — but it's worth noting here
that the coordinator's own independent verification, not my own
process, is what has caught both pagination-related issues on this task
so far. The `--paginate` fix from the previous disclosure was already
in place for the actual round-12 rate-limit check below and worked
correctly (see its own entry).

### Round 12

Against commit `4a78e63` (after round 11's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T19:58:47Z`): `CHANGES_REQUESTED`, 1
actionable comment, real, fixed:

- **The class-level Javadoc and `createAndWriteMetadata`'s own Javadoc
  had accumulated substantial review-round narrative ("a further real
  CodeRabbit review round on this PR", "Critical finding, real
  CodeRabbit review of this PR, fixed here", etc.) baked directly into
  permanent production code documentation (Major, by CodeRabbit's own
  classification, though scoped as a documentation-only change).** Real,
  and a legitimate concern distinct from every previous round's fixes:
  this task's own established, disclosed convention of narrating each
  review round's findings directly in Javadoc (see essentially every
  method in this file) is genuinely useful for a reader of *this specific
  PR's history*, but is process meta-commentary, not part of the class's
  permanent behavioral contract — a future maintainer trying to
  understand what `AccountLedgerLock` actually does and why has to wade
  through "further real CodeRabbit review round on this PR" framing to
  extract the technical substance underneath it. This document
  (`.planning/kis-ledger-b-account-ledger-store-lock.md`) already carries
  the full round-by-round history in far more detail than the Javadoc's
  compressed version ever did, so nothing is lost by removing it from
  the class file specifically.

  Fixed, scoped exactly to what the finding named (the class Javadoc and
  `createAndWriteMetadata`'s own Javadoc only — deliberately **not** a
  file-wide pass over every other method's own similar narrative, per
  this project's own "touch only what the task requires" rule; the same
  pattern still exists elsewhere in this file, e.g. `tryStealIfStale`,
  `close`, `readMetadataOrNull` — left alone unless a future review
  round asks for those specifically too, rather than guessed at
  preemptively here): rewrote both Javadoc blocks to describe only the
  current behavioral contract, safety guarantees, and invariants, in
  plain present-tense technical prose, with zero runtime-behavior change.
  Every substantive technical fact was preserved, not merely
  deleted — including the real drvfs POSIX-unlink-semantics finding, the
  single-atomic-handle-vs-two-separate-operations reasoning, the
  re-verification-after-write mechanism, the `READ_FAILED`-vs-genuine-
  mismatch distinction, and the `staleThreshold`/critical-section-
  duration caller contract with its empirical backing and test pointer.
  Added one new sentence to the class Javadoc pointing future readers at
  this planning document for the full historical record, so the
  round-by-round detail is not lost, only relocated to where it
  belongs.

Re-ran after the round-12 fix: `./gradlew clean build` — still green,
**439 tests, 0 failures, 0 errors** project-wide (a documentation-only
change, zero new or modified test methods, exact count unchanged from
round 11). `./gradlew :runtime:compileJava` confirmed the rewritten
Javadoc's `{@link}`/`{@code}` cross-references all still resolve
correctly (no javadoc/compile errors).

### A real `gh api` comment-identity gotcha, caught myself before acting on it

While triaging round 13's findings, `gh api repos/.../pulls/100/comments`
(filtered by `commit_id` matching current HEAD) returned a batch of
**Korean-language** review comments describing findings already fixed in
much earlier rounds (e.g. `close()`'s unconditional-delete TOCTOU, fixed
in round 8; the `READ_FAILED`-vs-genuine-mismatch conflation, fixed in
round 5). Before treating any of this as a new round-13 finding, I
cross-checked each comment's `pull_request_review_id` against the actual
round-13 review's own id (`4944718393`) and found every one of the
Korean comments belonged to `pull_request_review_id` values from rounds
1-9 (`created_at` between 09:59Z and 16:48Z on 2026-08-15, all well
before round 13's real `submitted_at: 2026-08-15T21:04:47Z`). **Root
cause**: a review comment's `commit_id` field is not fixed at creation —
GitHub updates it to reflect the latest commit still containing the
comment's original diff context unchanged, so an old, already-resolved
comment anchored to code nobody has touched since can show a `commit_id`
matching current HEAD indefinitely. Filtering on `commit_id` alone is
therefore **not** a reliable way to find "what's new this round" — a
different failure mode from the earlier disclosed pagination mistake
(that one hid real new data; this one would have surfaced real old data
mislabeled as new). Recovered by filtering on `pull_request_review_id`
matching the specific review object instead, which is fixed and
reliable, and by treating the review's own `body` (its structured
"Actionable comments posted: N" summary) as the authoritative source for
what a given round's real findings are. No incorrect fix was made off
the Korean comments -- caught before acting, not after.

### Round 13

Against commit `0844e51` (after round 12's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T21:04:47Z`): `CHANGES_REQUESTED`, 3
actionable comments (all three tagged Trivial/nitpick by CodeRabbit's
own classification), two real and fixed, one real but declined with
reasoning:

- **Fixed: the two fail-closed `IllegalStateException`s in the missing-
  ledger-but-leftover-`.tmp`-exists branch of `load` discarded the
  original `NoSuchFileException`'s stack trace (PMD's PreserveStackTrace
  finding).** Real: `e` (the exception that routed execution into this
  branch in the first place) is the only record of which path was judged
  missing, and this exception only ever surfaces when a human must
  manually investigate the ledger — exactly the situation where
  preserving full diagnostic context matters most. Fixed by attaching
  `e` as a suppressed exception on the inner `tmpCheckFailure`-caused
  `IllegalStateException` (which already has its own, more specific
  cause), and as the direct cause on the outer "leftover .tmp exists"
  `IllegalStateException` (which previously had none at all). Behavior
  unchanged — same messages, same control flow, only the exception chain
  is richer.
- **Fixed: `AccountLedgerStore`'s class Javadoc and `persist`'s own
  Javadoc stated the `AccountLedgerLock` requirement as covering only a
  "read-modify-write cycle," not a standalone `persist` call.** Real, and
  worth closing before `SharedKisAccountLedger` (Task C, a different
  agent, not yet built) has to interpret this contract for real:
  `tmpPathFor` deliberately keeps a fixed, non-process-unique temp path
  (`<ledgerPath>.json.tmp`) — the interrupted-persist detection in `load`
  depends on that exact fixed name to recognize a crashed prior attempt's
  leftover file — but that same fixed path means two processes calling
  `persist` on the same `ledgerPath` **without** holding the lock, even a
  standalone call outside any `load` + mutate sequence, share one temp
  file and can race on it: one call's `TRUNCATE_EXISTING` open, write,
  and atomic-move sequence can interleave with another's, risking a
  partial or wrong write landing at `ledgerPath` and a real loss of
  another process's already-committed reservations. Fixed by stating the
  contract unconditionally ("every `persist` call, not only a
  read-modify-write cycle") in both the class Javadoc and `persist`'s own
  Javadoc, with the mechanism spelled out in `persist`'s copy so Task C
  has no ambiguity to resolve incorrectly. Documentation-only — the fixed
  `.tmp` naming itself is correct and deliberately preserved (the finding
  itself agreed, matching this document's own earlier round-3 reasoning
  for why that naming is fixed rather than per-process).
- **Declined, with reasoning, rather than fixed: consolidate Jackson
  versions onto a repo-wide BOM so `java/runtime` (2.18.9) and `java/
  schemas`/`java/exchange`/`java/risk` (2.18.2) stop drifting.** Real
  observation, but not actioned in this PR, for reasons independent of
  whether it's a good idea in the abstract: (1) CodeRabbit's own comment
  text explicitly frames this as belonging in "a separate follow-up
  task" ("별도 후속 작업에서 Jackson BOM을 적용해 모듈 간 버전 드리프트를
  방지하십시오"), not as something this PR itself must resolve, and the
  finding is tagged Trivial/nitpick, not Major/Critical; (2) fixing it
  for real means touching `java/exchange` and `java/risk`'s own
  `build.gradle.kts` files, both **explicitly named as out-of-scope** in
  this task's own governing brief ("explicitly told NOT to touch...
  `java/risk`, `java/oms`, `java/execution`, `java/exchange`"); (3) this
  exact 2.18.9-vs-2.18.2 split, and the reasoning for leaving it, is
  **already a deliberate, previously-reviewed decision** from a prior PR
  (#27) — the very comment block CodeRabbit's finding points at
  (`java/runtime/build.gradle.kts` lines 21-47) already documents this in
  full, including the line "a repo-wide, cross-module version bump...
  is a separate, dedicated follow-up rather than folded into this task's
  diff"; (4) no Jackson BOM exists anywhere in this repository today
  (confirmed by grep across every `build.gradle.kts` — the only
  `platform(...)` usage anywhere is `junit-bom` for tests) — introducing
  one would be a genuine, first-of-its-kind architectural change to this
  project's dependency-management approach, not a one-line version bump,
  and deserves its own dedicated task and its own review, not something
  folded into a lock/store-focused PR under review-cycle pressure.
  Matching this project's own "document declined suggestions with real
  reasoning" convention rather than silently ignoring a real review
  comment.

Re-ran after round 13's two real fixes: `./gradlew clean build` — still
green, **439 tests, 0 failures, 0 errors** project-wide (documentation
and exception-cause-chaining changes only, zero new or modified test
methods — no existing test asserts on the cause of either affected
`IllegalStateException`, confirmed by grep before making the change).

### Round 14

Against commit `76ef2a9` (after round 13's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T22:09:38Z`): `CHANGES_REQUESTED`, 2
actionable comments (both tagged Minor), both real, both fixed —
identified via `pull_request_review_id` filtering per the gotcha
disclosed above, not the unreliable `commit_id` filter:

- **Both steal paths (`tryStealIfStale`, `tryStealIfAbandonedEmpty`)
  logged their `ERROR` "stealing..." message *before* the
  re-verify-immediately-before-delete check that this task's own real
  TOCTOU finding put in place, not after.** Real: on the common, benign
  path where re-verification finds the lock no longer matches (a
  legitimate new holder already took it, or a sibling already stole it
  first) and the method correctly returns `false` without deleting
  anything, the `ERROR` log had *already* fired, falsely claiming "This
  is a real cross-process safety event" for something that never
  actually happened. This class's own Javadoc defines `ERROR` logging as
  the primary diagnostic signal for a real steal — a false positive here
  would send an operator investigating a "crash" that didn't occur, or
  worse, desensitize them to the real signal over time. Fixed in both
  methods by moving the `log.error(...)` call to after the re-verify
  passes, immediately before the actual `Files.delete` call — so it only
  ever fires when a delete is genuinely about to happen. No behavior
  change to the actual steal/no-steal decision itself, only to when the
  log line fires relative to it.
- **`AccountLedgerStore.load()` never validated `defaultAllocatedCapital`
  as positive, and never compared an existing ledger's stored `
  allocatedVirtualCapital` against the currently-configured default at
  all — only ever consulting it when bootstrapping a brand new ledger.**
  Real, and directly grounded in CLAUDE.md's own "never weaken risk
  limits... without explicit human approval" rule, cited explicitly in
  the review comment itself: an operator lowering `defaultAllocatedCapital`
  in configuration to reduce a risk budget would have that reduction
  silently ignored for as long as a larger, previously-persisted value
  remained on disk — the stored value would simply keep being used
  as-is, forever. Initially considered declining this one (like the
  Jackson BOM finding) as real reconciliation-policy work belonging to
  Task C/D rather than Task B's own storage-primitives scope — but on
  reading the review's own suggested fix, it turned out to be a much
  narrower, purely defensive change than that: a positivity check on
  `defaultAllocatedCapital` (`IllegalArgumentException` if
  non-positive), plus a **fail-closed** check, symmetric with the
  existing venue/accountId identity-mismatch check right next to it in
  the same method, that throws `IllegalStateException` if a loaded
  ledger's `allocatedVirtualCapital` exceeds the configured default.
  Deliberately **not** an auto-reduction (which would silently mutate
  stored state on this process's own initiative) — a human must resolve
  the mismatch explicitly, the same discipline already established for
  every other fail-closed case in this method. Deliberately says nothing
  about whether a *smaller* stored allocation should ever be raised back
  up to a larger configured default — that remains real reconciliation
  policy this class's own Javadoc already defers to Task C/D, untouched
  here. Documented in both the class-adjacent `load` Javadoc and inline;
  three new regression tests added
  (`loadRejectsAZeroOrNegativeDefaultAllocatedCapital`,
  `loadFailsClosedWhenAnExistingLedgersAllocatedCapitalExceedsTheConfiguredDefault`,
  `loadSucceedsWhenAnExistingLedgersAllocatedCapitalExactlyEqualsTheConfiguredDefault`
  — the last one proving the check is strictly "greater than," not
  "greater than or equal to," at the exact boundary). Fixing this also
  required repairing one **pre-existing** test
  (`loadReflectsTheLatestPersistedStateNotAStaleCachedOne`) that
  persisted `2000` then reloaded with a default of `1000` — a real
  conflict with the new check, unrelated to that test's own actual
  subject (proving `load` has no in-memory caching, not capital-
  reduction semantics); fixed by holding `defaultAllocatedCapital` fixed
  at `2000` (≥ both persisted values) throughout, preserving the test's
  original intent. Checked every other `load` call site in the test file
  by hand before concluding this was the only real conflict — every
  other site either uses matching/equal values, or expects an
  `IllegalStateException` from an earlier-checked, unrelated cause
  (missing file, corrupted JSON, identity mismatch, duplicate
  `clientOrderId`) that fires before this new check is ever reached.

Re-ran after both round-14 fixes: `./gradlew clean build` — still green,
**442 tests, 0 failures, 0 errors** project-wide (439 + 3 new tests, all
in `AccountLedgerStoreTest`). Re-verified the real safety property
specifically, given the log-repositioning change touches
`AccountLedgerLock`'s own steal paths (even though it changes no actual
branching/timing, only where a log statement sits relative to unchanged
control flow): `AccountLedgerLockMultiProcessTest` green 2 more times
(`--rerun-tasks`, on top of the one run already part of the full `clean
build` above).

### Round 15

Against commit `ac756a3` (after round 14's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-15T23:12:10Z`): `CHANGES_REQUESTED`, 1
actionable comment (Minor), real, fixed. This review's own footer also
surfaced a new, useful signal not seen in any prior round's response:
"Included review availability: 0 reviews are currently available...
included reviews refill at 1 per hour" — consistent with, and a more
precise explanation for, the ~40-47 minute ETAs this task has been
seeing on its last several rate-limit checks.

- **`persist`'s non-atomic `REPLACE_EXISTING` fallback is deliberately
  narrow — only `AtomicMoveNotSupportedException`/
  `FileAlreadyExistsException` trigger it, per its own existing `catch`
  clause (`AccountLedgerStore.java`, the `mover.move` call inside
  `persist`) — but nothing pinned that boundary with a test.** Real,
  and a legitimate test-coverage gap, not a production-code bug:
  verified directly against the actual implementation before writing
  anything — the `catch` clause is already correctly narrow, and any
  other `IOException` already propagates to the method's outer `catch
  (IOException e)`, which wraps it in `IllegalStateException` exactly as
  the finding wants. Without a test pinning this, a future change that
  accidentally widened the fallback's `catch` clause to plain `
  IOException` would make this class perform a non-atomic replace after
  an arbitrary I/O failure — risking another process's committed
  reservation being silently lost — while every existing test kept
  passing. Fixed by adding
  `anUnrelatedIoFailureDuringTheMovePropagatesInsteadOfFallingBack`,
  using a test `AtomicMover` that throws a generic `IOException` (not
  `AtomicMoveNotSupportedException`), asserting `persist` throws
  `IllegalStateException` and that `ledgerPath` is never created —
  proving the distinction between the two failure classes is real
  behavior, not merely an implementation detail nobody happens to have
  broken yet. No production code change — the review's own suggested
  test matched the actual implementation exactly.

Re-ran after the round-15 fix: `./gradlew clean build` — still green,
**443 tests, 0 failures, 0 errors** project-wide (442 + 1 new test, in
`AccountLedgerStoreTest`). A pure test-addition round with no production
code change, so no further multi-process/stress re-verification was
independently warranted beyond the full `clean build`'s own real
multi-process test run.

### Round 16

Against commit `ba363a6` (after round 15's fix was pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T00:17:57Z`): `CHANGES_REQUESTED`, 4
actionable comments, all four real, all four fixed — one of them a
genuine regression in round 10's own `close()` idempotency fix, not
merely a documentation nit:

- **(Trivial) `tryStealIfAbandonedEmpty`'s backstop Javadoc still
  referenced `Files#createFile`, a method this class stopped calling
  when it moved to `Files.newByteChannel(..., CREATE_NEW, WRITE)`** —
  the same class of stale reference already fixed for the class Javadoc
  and `tryStealIfStale` in round 12, missed for this one remaining spot.
  Fixed by pointing at the real creation path. The review's own comment
  also named a second location ("line 485") as needing the same fix;
  checked directly by grep before touching anything and found no
  `Files#createFile`/`Files.createFile` reference anywhere near that
  line in the current file (the only two other occurrences in the whole
  file are a legitimate historical comparison in `createAndWriteMetadata`'s
  own comment, and the class Javadoc's "Deviation from the governing
  plan's own literal code sketch" paragraph, which is correctly
  describing what the *plan's sketch* wrote, not this class's own
  implementation) — treated as a stale/incorrect line reference within
  the same comment rather than a second real issue, and only the one
  confirmed instance was fixed.
- **(Minor) A real regression in round 10's own `close()` idempotency
  fix: `doClose()` treated every "don't delete" outcome as final,
  including the genuinely transient ones.** Real, and the most
  consequential finding this round: round 10 made `close()` idempotent
  by setting `closed = true` unconditionally after `doClose()` returned
  without throwing — but `doClose()` returns normally (no exception) on
  *all* of its outcomes, including a transient `READ_FAILED`
  re-verification read and any non-`NoSuchFileException` `IOException`
  from the delete itself, neither of which round 10's own stated intent
  ("preserving retry-ability on a genuinely unexpected exception") was
  ever meant to make permanent. The practical consequence: a single
  transient filesystem read hiccup on `close()` would permanently mark
  this instance closed, with no way to ever retry releasing the still-
  live lock file — blocking every other waiter for a shared KIS account
  risk ledger lock until `staleThreshold` elapsed, regardless of how many
  times a caller called `close()` again. Fixed by changing `doClose()`'s
  signature to return `boolean`: `true` for a genuinely final outcome
  (deleted, confirmed already gone, or a confirmed alternate state --
  `EMPTY_OR_UNPARSEABLE` or a genuine generation mismatch -- that a retry
  could only re-observe, never resolve differently), `false` for a
  transient, recoverable outcome (`READ_FAILED`, or any other
  `IOException` from the delete) a later `close()` call might still
  resolve. `close()` itself is now `closed = doClose();` rather than
  `doClose(); closed = true;`. The uncaught-exception case (an
  exception that escapes `doClose` entirely) is unaffected and still
  correctly leaves `closed` unset, for the identical reason as before.
- **(Trivial) `acquireThrowsRatherThanHangingWhenTheRetryBudgetIsExhausted`
  used a plain, unsynchronized `ArrayList` for `holderFailures`, unlike
  every other test in this file using the same pattern.** Real: the
  holder thread writes to this list while the main thread reads it after
  only a *bounded* `join(10s)` wait, not a guaranteed-finished join --
  an unsynchronized data race if the holder thread hadn't actually
  finished within that window. Fixed by switching to
  `CopyOnWriteArrayList`, matching this file's own established
  convention (two other tests already use it for the identical purpose).
  The now-unused `java.util.ArrayList` import was removed.
- **(Minor) The round-10 deterministic mutual-exclusion test recorded
  `holderReleasedAtNanos` in a `finally` block, which runs only *after*
  try-with-resources has already invoked `close()` on the way out --
  not at the moment the holder's real critical section actually
  finished.** Real, and a genuine weakening of the test's own central
  claim, not merely a style issue: by the time this test's own steal has
  happened, the holder's `close()` call is not the fast path -- it
  re-reads the by-then-stolen lock file, finds a real generation
  mismatch, and logs `ERROR`, all subject to this repository's own
  documented 500ms+ single-operation drvfs latency. Including that time
  inside the "holder was still active" window meant the test's own
  assertion (`stolenAcquiredAtNanos < holderReleasedAtNanos`) could still
  pass even if the steal actually completed *after* the holder's real
  work had already finished -- exactly the failure mode this test exists
  to rule out. Fixed by recording `holderReleasedAtNanos` immediately
  after `Thread.sleep(holderRealHoldMillis)` inside the try block itself
  (i.e. genuinely at the moment the critical section ends, before
  `close()` ever runs), with a `compareAndSet(-1, ...)` fallback in the
  `catch` block covering only the case where an exception occurred
  before reaching that line (never overwriting a timestamp the try block
  already recorded). Re-ran the retimed test 5 times total (1 + 4 more
  explicit reruns) to confirm it remains stable, matching this task's own
  established discipline for timing-sensitive tests.

Re-ran after all four round-16 fixes: `./gradlew clean build` — still
green, **443 tests, 0 failures, 0 errors** project-wide (no new test
methods this round -- a Javadoc reference fix, a real behavioral fix to
`close()`/`doClose()`'s return-value contract, and two existing-test
repairs). Given the `close()`/`doClose()` change is a real behavioral
fix (not merely cosmetic, unlike rounds 11/12), re-verified the real
multi-process safety property specifically:
`AccountLedgerLockMultiProcessTest` green 2 more explicit reruns
(`--rerun-tasks`, on top of the one run already part of the full `clean
build` above) -- 3 total. Also re-ran the raw, non-Gradle stress
harness (unlike rounds 11-15, which judged the Gradle multi-process
test reruns sufficient on their own for a purely cosmetic/test-only
change) -- **25/25 rounds exactly correct** at this task's own
realistic `staleThreshold=30s` configuration (6 processes × 8
iterations = 48 expected per round, zero errors in any process's own
output across all 25 rounds), the extra rigor judged warranted here
specifically because this round's `close()`/`doClose()` fix is a real
change to the release-path's own retry-ability contract, not a
log-statement move or test-only change.

### Round 17

Against commit `136ae5e` (after round 16's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T01:23:18Z`): **`COMMENTED`, not
`CHANGES_REQUESTED`** -- the first review this whole task that did not
request changes -- with exactly one item, explicitly labeled by
CodeRabbit itself as a **"Duplicate comment"** (re-raising a point
already substantively discussed, not a fresh actionable finding), real
and fixed anyway:

- **The class Javadoc's caller-contract paragraph only documented
  `staleThreshold` needing to exceed the critical-section duration --
  never separately documented that it must also exceed the lock file's
  mount's own real mtime precision.** Real, and directly related to
  (but genuinely distinct from) round 10's own mtime-precision
  investigation: round 10 measured this repository's actual drvfs
  mount's mtime precision (sub-3ms, 200/200 distinct in a tight loop)
  and correctly concluded that specific number isn't a real risk at
  this project's own realistic `staleThreshold` values -- but the
  caller-contract *documentation* itself never stated mtime precision
  as its own separate requirement, only the critical-section-duration
  one. The two conditions are genuinely independent: a caller could
  satisfy "`staleThreshold` > critical section duration" (e.g. a 100ms
  critical section against a 500ms `staleThreshold`) while still
  failing "`staleThreshold` > mtime precision" on some future,
  coarser-precision mount (e.g. a 1-second mtime granularity) -- and
  `tryStealIfAbandonedEmpty`'s own re-verify-before-delete check relies
  specifically on a stolen generation's mtime differing from the
  abandoned one it replaced, so a too-coarse mtime precision relative to
  `staleThreshold` could defeat that re-verification. Fixed by adding a
  second, explicitly separate caller-contract paragraph documenting this
  requirement directly in the class Javadoc, citing round 10's own real
  measurement as evidence this project's actual mount and default
  (~30s) already clear it by a wide margin, while stating the general
  requirement plainly for any future deployment on a different mount.
  Documentation-only, zero behavior change.

  Treated as worth fixing despite being both a "duplicate" and posted
  under a non-blocking `COMMENTED` review, matching this task's own
  established discipline of evaluating every real finding on its merits
  rather than by how CodeRabbit classified it -- the point itself is
  correct and cheap to address, so declining it purely because the
  review didn't formally require action would have been declining for
  the wrong reason.

Re-ran after the round-17 fix: `./gradlew clean build` — still green,
**443 tests, 0 failures, 0 errors** project-wide (documentation-only
change, zero new or modified test methods, exact count unchanged from
round 16).

### Round 18

Against commit `8ab7a0b` (after round 17's fix was pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T02:25:19Z`): `CHANGES_REQUESTED` (back
from round 17's `COMMENTED`), 2 actionable comments, both real, both
fixed:

- **(Major) `persist` never cleaned up its own just-created `.tmp` file
  on a failure other than the supported fallback exceptions -- and a
  leftover `.tmp` alongside a never-successfully-persisted ledger
  permanently fails closed every future `load` call for that
  `(venue, accountId)`, not merely the interrupted one.** Real, and the
  most consequential finding since round 16's `close()` regression: the
  exact sequence is (1) `ledgerPath` doesn't exist yet (never
  successfully persisted), (2) `persist` creates its `.tmp` file and
  writes to it successfully, (3) `mover.move` throws something other
  than `AtomicMoveNotSupportedException`/`FileAlreadyExistsException`
  (propagates as `IllegalStateException`, per this method's own existing
  behavior, correctly pinned by round 15's own test), (4) the `.tmp`
  file is left on disk. The very next `load` call for that ledger then
  hits its own missing-ledger-plus-leftover-`.tmp` fail-closed check
  (round 3's own fix) -- indistinguishable from a genuinely interrupted
  `persist()`, even though nothing had ever actually succeeded for this
  ledger. Capital safety was never at risk (fail-closed is the correct
  direction for a real interrupted persist), but availability was
  needlessly and *permanently* lost -- every future `load` for that
  ledger fails until a human manually deletes the `.tmp` file. Fixed by
  hoisting the `tmp` variable outside the try block and cleaning it up
  (best-effort, `Files.deleteIfExists`, cleanup failure attached via
  `addSuppressed` rather than masking the original failure) in the outer
  `catch (IOException e)` block before re-throwing. Deliberately does
  **not** touch `load`'s own detection logic or weaken it: a `.tmp` file
  surviving a genuine crash (no Java exception ever thrown, so this
  `catch` block never runs at all) is completely untouched by this
  cleanup and still correctly fails closed -- this fix only prevents
  *this same process's own, already-caught* failure from leaving a
  leftover file behind, it does not and cannot address the harder crash
  case round 3's own check exists for.

  The existing test pinning the narrow-fallback boundary
  (`anUnrelatedIoFailureDuringTheMovePropagatesInsteadOfFallingBack`,
  from round 15) only checked that `ledgerPath` was never created --
  never checked the `.tmp` file itself, so it could not have caught this
  gap. Strengthened with an added assertion that the `.tmp` file is also
  gone, and a new, dedicated test
  (`persistCleansUpItsOwnTmpFileOnFailureSoASubsequentLoadStillBootstrapsFresh`)
  proving the actual practical consequence directly: a real failed
  `persist()` immediately followed by a real `load()` call, which must
  succeed as an ordinary fresh bootstrap rather than fail closed.
- **(Minor) The 12-thread mutual-exclusion test's shared counter was a
  plain, unguarded `int[]`, which gives no cross-thread visibility
  guarantee in the Java Memory Model.** Real: this test's own mutual
  exclusion is enforced entirely through file I/O
  (`AccountLedgerLock.acquire`/`close`), and file I/O establishes no
  happens-before edge between threads -- even with perfect mutual
  exclusion, one thread could fail to *observe* another thread's latest
  increment, which would fail the test's final count assertion for a
  reason having nothing to do with the lock's own correctness. Fixed by
  switching to `AtomicInteger`, using its plain `get()`/`set()`
  (deliberately **not** `incrementAndGet()`, which would make the
  increment itself atomic and defeat this test's whole point of
  detecting a non-atomic read-sleep-increment-write race) -- fixes the
  visibility gap while keeping the exact same race the test exists to
  catch. Re-ran 3 more times to confirm stability.

Re-ran after both round-18 fixes: `./gradlew clean build` — still
green, **444 tests, 0 failures, 0 errors** project-wide (443 + 1 new
test, in `AccountLedgerStoreTest`). Neither fix touches
`AccountLedgerLock`'s own acquire/steal control flow (one is entirely
in `AccountLedgerStore.persist`, the other is test-only), so a further
raw stress-harness round was not independently warranted this round,
matching the pattern already established for store-only/test-only
rounds.

### Round 19

Against commit `619dd42` (after round 18's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T03:28:06Z`): `CHANGES_REQUESTED`, 2
"Actionable comments" plus 1 shown separately as a **"Duplicate
comment"** — the duplicate label notwithstanding, it was the most
serious finding of the three, catching a real regression in round 18's
own fix. All three real, all three fixed:

- **(Major, shown as a "duplicate" but genuinely new and serious: a
  real regression in round 18's own tmp-cleanup fix) `persist`'s new
  cleanup unconditionally deleted `tmp` on any `IOException`, including
  one thrown by the non-atomic `REPLACE_EXISTING` fallback move
  itself — and that specific case can turn a safe fail-closed outcome
  into silent, permanent loss of every other process's real committed
  reservations.** Real, and directly grounded in CLAUDE.md's own "never
  weaken risk limits" principle (cited explicitly in the review): the
  round-18 fix correctly handled the case where `mover.move` itself
  fails before `ledgerPath` is ever touched (safe to delete `tmp`
  there — it's this process's own unpublished, orphaned write), but
  did not distinguish that from the fallback `Files.move(...,
  REPLACE_EXISTING)` failing. That fallback is **not** a single atomic
  operation and can fail partway through — possibly after it has
  already altered or removed a real, pre-existing `ledgerPath` (which
  could hold other processes' genuine committed reservations) but
  before `tmp`'s own content has actually landed at that path. In that
  specific sequence, `tmp` is the *only* remaining copy of valid data,
  and round 18's own cleanup would delete exactly the evidence `load`'s
  own missing-ledger-plus-leftover-`.tmp` fail-closed check needs to
  avoid silently bootstrapping an empty ledger over real, lost
  reservations. Fixed by wrapping the fallback `Files.move` call in its
  own `catch (IOException fallbackFailure)`, setting `tmp = null`
  (opting that failure class out of the outer cleanup — a plain local
  variable reassignment, not a lambda capture, so straightforwardly
  valid) before rethrowing. The originally-intended, narrower cleanup
  case (`mover.move` itself failing, before any fallback) is unaffected.

  Proven with a new test
  (`persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails`)
  at the level that is actually deterministic and portable to reproduce
  via public `java.nio.file` APIs, disclosed honestly rather than
  overclaimed: a real, non-empty directory at `ledgerPath`'s own path
  reliably makes the fallback `Files.move` throw a real
  `DirectoryNotEmptyException` — confirmed via a standalone probe
  *before* writing the test (not assumed) that this leaves both the
  source (`tmp`) and the target directory's own content untouched, so
  this specific setup does not reproduce the harder, genuinely timing-
  dependent "target already removed mid-move" sub-case verbatim. It
  does exercise the exact same code path (the fallback `Files.move`
  call and the new `catch` block around it) and prove the real
  consequence that actually matters: `tmp` is preserved, not deleted,
  when that call fails, and a subsequent `load` call still fails closed
  (via a different one of `load`'s several fail-closed branches here,
  since a directory at `ledgerPath` is itself unreadable as a file —
  but the same overall safety property) rather than silently
  bootstrapping an empty ledger.
- **(Minor) `createAndWriteMetadata`'s "Cleanup on write failure"
  Javadoc paragraph claimed unconditional deletion on write failure —
  the real mechanism (`deleteIfStillOwnGeneration`) only deletes if the
  file still holds exactly this holder's own metadata.** Real: the most
  common real shape a write failure takes is a partial write, which
  leaves truncated JSON that `readMetadataOrNull` reports as `
  EMPTY_OR_UNPARSEABLE` — that never `equals()` this holder's own
  metadata, so this cleanup does **not** delete it in that case, contrary
  to what the Javadoc claimed. Fixed by correcting the Javadoc to state
  the real, conditional guarantee, naming the partial-write case
  explicitly, and clarifying that `tryStealIfAbandonedEmpty` is the real
  backstop for that residual window (until `staleThreshold` elapses),
  not merely the narrower hard-crash case the Javadoc previously singled
  out. Documentation-only, zero behavior change — verified directly
  against `deleteIfStillOwnGeneration`'s own real implementation before
  rewriting the claim, not merely rephrased on the review's say-so.
- **(Trivial) Two steal tests (`acquireStealsAFabricatedLockWithADeadPid`,
  `acquireStealsAFabricatedLockWithAnExpiredTimestampEvenIfTheHolderIsAlive`)
  only asserted the lock file still existed after `acquire`, which
  cannot distinguish a real steal from `acquire` never having run at
  all** (the fabricated file already exists before `acquire` is ever
  called). Fixed by verifying the file's actual content changed —
  matching the discipline the file's own
  `acquireStealsAnAbandonedEmptyLockFileOlderThanStaleThreshold` test
  already applied: the dead-PID test now confirms the fabricated
  `stale-host` value is gone and the real process's own pid is present;
  the expired-timestamp test (whose fabricated pid is deliberately this
  same process's own real pid, so a pid check alone wouldn't
  differentiate) confirms only that `stale-host` is gone.

Re-ran after all three round-19 fixes: `./gradlew clean build` — still
green, **445 tests, 0 failures, 0 errors** project-wide (444 + 1 new
test, in `AccountLedgerStoreTest`; the two `AccountLedgerLockTest`
strengthenings modified existing methods rather than adding new ones).
None of round 19's fixes touch `AccountLedgerLock`'s own acquire/steal
mutual-exclusion control flow (the serious fix was entirely in
`AccountLedgerStore.persist`; the other two are Javadoc/test-only), so
a further raw stress-harness round was not independently warranted
this round.

### A real ~8-hour coordinator-side gap, not a task-side stall

Round 19's report was filed at a rate-limit ETA of 03:36 UTC (2026-08-16,
clearing ~04:19 UTC). The next real instruction did not arrive until
11:24 UTC the same day -- the governing coordinator's own scheduled
check-ins had stopped firing after a session reset on its side, not
because of anything wrong on this task's own end. Independently
confirmed before resuming (not assumed): HEAD was still exactly
`e95524e` (round 19's own commit, untouched for the entire gap), PR #100
still `OPEN`/`mergeStateStatus BLOCKED`, and the most recent real review
on record was still round 19's own `CHANGES_REQUESTED` against the
*prior* commit `619dd42` -- i.e. nothing had drifted, nothing needed to
be reconciled, and no review had silently landed and gone unread during
the gap. Resumed exactly where round 19 left off per the coordinator's
own explicit instruction, following the identical rate-limit-check
procedure as every prior round.

### Round 20

Against commit `e95524e` (after round 19's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T11:41:52Z`): `CHANGES_REQUESTED`, 1
actionable comment (Minor), real, fixed:

- **`aReservationWithZeroOrNegativeNotionalIsRejectedByLedgerReservationItself`'s
  own Javadoc claimed the non-positive-`notional` check was "exercised
  here through a real store round trip" -- it wasn't; the test calls
  `LedgerReservation`'s constructor directly and never touches
  `AccountLedgerStore` at all.** Real, and the inaccuracy masked a real
  coverage gap, not just a documentation slip: verified directly against
  the test's own code before touching anything, exactly as this
  inaccuracy claimed. The duplicate-`clientOrderId` invariant just below
  it in the same file has *both* a direct-constructor unit test and a
  file-based one
  (`loadFailsClosedWhenTheLedgerFileHoldsTwoReservationsWithTheSameClientOrderId`)
  proving `AccountLedgerStore.load` itself fails closed on a corrupted
  ledger file -- but the negative-`notional` invariant only ever had the
  direct-constructor test, with a Javadoc that incorrectly implied the
  file-level path was already covered. Real risk-limit relevance, not
  merely a data-integrity nicety: a negative `notional` would *increase*
  the derived `allocatedVirtualCapital - Σ(reservations.notional)`
  available capital -- exactly the direction CLAUDE.md's own "never
  weaken risk limits" rule exists to prevent, on a path (a corrupted or
  hand-edited ledger file) this record-level validation exists
  specifically to catch. Fixed by correcting the Javadoc and adding
  `loadFailsClosedWhenTheLedgerFileHoldsANegativeNotional`, matching the
  file-based pattern already established for the duplicate-`clientOrderId`
  case exactly. No production code change -- `AccountLedgerStore.load`
  already fails closed correctly via `MAPPER.readValue`'s own
  construction of `AccountLedger`/`LedgerReservation`, which already
  rejects the non-positive `notional` at the record level; this closes a
  real test-coverage gap around that existing, correct behavior.

Re-ran after the round-20 fix: `./gradlew clean build` — still green,
**446 tests, 0 failures, 0 errors** project-wide (445 + 1 new test, in
`AccountLedgerStoreTest`). Test-only, no production code change at all
this round, and no `AccountLedgerLock` involvement, so a further raw
stress-harness round was not independently warranted.

### Round 21

Against commit `c164c14` (after round 20's fix was pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T12:37:34Z`): `CHANGES_REQUESTED`, 1
actionable comment (Minor), real, fixed:

- **Round 19's own fallback-failure fix (`tmp = null; throw
  fallbackFailure;`) discarded the original `e`
  (`AtomicMoveNotSupportedException`/`FileAlreadyExistsException`, the
  exception that triggered entry into the fallback in the first place)
  -- only ever logged as a plain `e.toString()`, its real stack trace
  was lost once `fallbackFailure` propagated alone.** Real, and a
  legitimate PreserveStackTrace-class finding (PMD): this exact failure
  path already means `ledgerPath` may have been altered and a human
  must investigate directly, so losing `e`'s own stack trace loses real
  diagnostic value at precisely the moment it matters most. Fixed with
  a single `fallbackFailure.addSuppressed(e);` before the `tmp = null;`
  reassignment and rethrow, matching this same file's own established
  `addSuppressed`-not-swallowed convention (rounds 13 and 18 both
  applied the identical pattern elsewhere in this class). Behavior
  unchanged -- purely additive to the exception chain, no control-flow
  change.

Re-ran after the round-21 fix: `./gradlew clean build` — still green,
**446 tests, 0 failures, 0 errors** project-wide (unchanged count -- a
single `addSuppressed` line, no new or modified test methods; the
existing `persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails`
test from round 19 already exercises this exact code path and continues
to pass unchanged, since it asserts on `IllegalStateException` being
thrown and `tmp` surviving, neither of which this fix touches). No
`AccountLedgerLock` involvement, so a further raw stress-harness round
was not independently warranted.

### Round 22

Against commit `78165d0` (after round 21's fix was pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-16T15:19:11Z`): `CHANGES_REQUESTED`, 2
actionable comments (both Minor), both real, both fixed -- the fourth
consecutive round finding a real, narrower variant of the same
`persist()`-cleanup-scope theme rounds 18/19/21 already worked through:

- **`persist` assigned `tmp` (`tmpPathFor(ledgerPath)`) before calling
  `MAPPER.writeValueAsBytes(ledger)` -- if serialization itself failed
  (a real `JsonProcessingException`, an `IOException` subtype, is a
  real possibility, not hypothetical), the outer catch's cleanup would
  see a non-null `tmp` and delete whatever file existed at that path,
  even though this call never created or opened it via `FileChannel.open`.**
  Real, and the same silent-data-loss shape as round 19's own finding,
  one step earlier in the method: if a genuine crash from a different,
  earlier `persist()` attempt had left a real leftover `.tmp` at that
  exact path -- the exact evidence `load`'s own missing-ledger-plus-
  leftover-`.tmp` fail-closed check depends on -- a serialization
  failure on *this* call would incorrectly delete it. Fixed by
  reordering: serialization now completes before `tmp` is ever
  assigned, so the cleanup logic's scope is always exactly "a file this
  call itself opened," never a stray survivor from an earlier crash.
  No new test needed -- this is a pure statement-reordering fix with no
  new branch or observable behavior difference to pin; the existing
  fallback/cleanup tests from rounds 18-19 continue to prove the
  cleanup logic itself is correct, and the reordering doesn't change
  what that logic does, only when `tmp` becomes non-null relative to a
  failure that was already impossible to reach this specific way before
  (serialization of a real, already-validated `AccountLedger` record
  practically never fails in this codebase's own real usage -- this is
  defense-in-depth against a failure mode that hasn't been observed,
  matching this method's own established pattern of guarding against
  every theoretically-reachable failure point regardless of how
  unlikely, not just observed ones).
- **The round-10 deterministic mutual-exclusion test's
  `holderReleasedAtNanos.get() > 0` completion check used the wrong
  comparison against its own `-1` sentinel.** Real, though narrow:
  `System.nanoTime()`'s own documented contract guarantees only
  monotonicity from an arbitrary origin, not a positive value -- unlike
  epoch-millis-based timestamps, a real, legitimately-recorded
  `System.nanoTime()` result is not guaranteed to be `> 0`, so this
  check could in principle spuriously fail on an otherwise correctly-
  recorded timestamp. Fixed by checking `!= -1` against the test's own
  actual sentinel value directly, the semantically correct completion
  check rather than a coincidentally-similar positivity check. Re-ran
  the retimed test 3 more times to confirm continued stability.

Re-ran after both round-22 fixes: `./gradlew clean build` — still
green, **446 tests, 0 failures, 0 errors** project-wide (unchanged
count -- a reordering fix and an assertion correction, no new test
methods). Neither fix touches `AccountLedgerLock`'s own mutual-
exclusion control flow in a way that changes observable behavior (the
`persist` reordering is entirely in `AccountLedgerStore`; the
`nanoTime` fix is purely test-assertion-level), so a further raw
stress-harness round was not independently warranted.

### Round 23

Against commit `f78858e` (after round 22's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-17T03:43:33Z`): `CHANGES_REQUESTED`, 4
actionable comments across four different files, all four real, all four
fixed. The largest round by diff size since the original implementation:

- **(Minor, a real behavior change) `AccountLedger`'s compact constructor
  never enforced that `reconciliationAlarmTrippedAt`/`reconciliationAlarmReason`
  are only ever both-null or both-non-null -- the class's own Javadoc
  explicitly deferred this to Task D's `AccountLedgerReconciler` as a
  caller responsibility.** Initially
  weighed carefully against declining, the same way the Jackson-BOM
  finding was declined in round 13 -- but on reading the review's own
  reasoning, this is a pure structural invariant, not an alarm *policy*
  choice: whatever alarm policy Task D ends up implementing, a
  half-populated pair can never be a valid state, the identical logic
  already used to justify enforcing the duplicate-`clientOrderId` check
  in this same record. Real danger in both directions: `
  reconciliationAlarmTrippedAt` alone leaves an alarm's cause unknown on
  disk; `reconciliationAlarmReason` alone is the more dangerous
  direction, since a real, unresolved reconciliation mismatch would be
  read as "no alarm tripped" per this record's own `null`-means-no-alarm
  contract -- a real kill-switch-adjacent weakening. Fixed by adding the
  XOR-null check to the compact constructor and correcting the Javadoc's
  own "does not itself enforce" claim. Checked every existing `new
  AccountLedger(...)` call site in both main and test code by hand before
  applying the fix (all either populate both alarm fields together or
  leave both null) -- confirmed zero conflicts, unlike round 14's
  `defaultAllocatedCapital` finding which needed one existing test
  repaired. Proven with a new file-based regression test
  (`loadFailsClosedWhenTheLedgerFileHoldsAHalfPopulatedReconciliationAlarmPair`,
  covering both half-populated directions in one method), matching the
  file-based pattern already established for the duplicate-`clientOrderId`
  and negative-`notional` cases.
- **(Trivial) Three more stale Javadoc heading/API references, the same
  class of issue already fixed multiple times in earlier rounds (12, 16,
  17), found in spots those rounds missed.** `createAndWriteMetadata`'s
  own inline comments (and a cross-reference in `deleteIfStillOwnGeneration`)
  still pointed at old, round-history-named heading labels ("Third, still
  deeper Major finding" Javadoc, "Fourth finding" Javadoc) that no longer
  exist after round 12's rewrite consolidated them into
  "Re-verification after write, and why a lost race is not an error."
  `AccountLedgerLockTest.java` had two more `Files#createFile` references
  (in `acquireStealsAnAbandonedEmptyLockFileOlderThanStaleThreshold`'s
  own Javadoc pair) pointing at a creation API this class stopped using
  before this task began. Fixed by pointing all five at the real,
  current API/heading. Documentation-only, zero behavior change.
- **(Trivial, the largest-scope finding this round) The same review-
  process-narrative-in-Javadoc pattern round 12 first flagged for
  `AccountLedgerLock.java` was still present throughout
  `AccountLedgerStore.java`, `AccountLedgerStoreTest.java`, and
  `build.gradle.kts` -- three files, most of their substantive Javadoc.**
  Unlike round 12's narrowly-scoped two-block fix, this finding named
  three explicit ranges spanning nearly the entire main body of
  `AccountLedgerStore.java` (the class Javadoc, `load`'s own Javadoc and
  inline comments throughout, `persist`'s own Javadoc and every inline
  comment added across rounds 18/19/21/22), plus three ranges in the test
  file, plus the Jackson-version comment block in `build.gradle.kts` --
  effectively "clean up this whole file's worth of review narrative,"
  not a narrow subset. Honored at that scope, not expanded further (no
  changes to `AccountLedgerLock.java`/`AccountLedgerLockTest.java` beyond
  the separate stale-reference fixes above, matching this project's own
  "touch only what the task requires" rule): every "real Major/Minor/
  Trivial finding, real CodeRabbit review of this PR" / "a further real
  CodeRabbit review round" annotation in all three files was rewritten to
  plain, present-tense technical prose, preserving every substantive
  design fact (the drvfs empirical durability probe, the CVE-2026-54515
  applicability reasoning, the PMD PreserveStackTrace attribution, the
  exact failure sequences each fix closes) while dropping the round/
  severity/PR-review meta-commentary. For the test file specifically, the
  review's own instruction to preserve each test's "prevented outcome"
  description and (for the fallback-preservation test) its own honest
  reproduction-limit disclosure was followed precisely -- that paragraph
  was left completely untouched. For `build.gradle.kts`, shrunk a
  27-line comment to the two real facts that matter (the 2.18.9-vs-2.18.2
  version split, and why the CVE-2026-54515 range doesn't apply here),
  dropping the "PR #27 review discussion" pointer per the review's own
  explicit instruction that such pointers belong in commit history, not
  code. Verified via grep after editing that zero "CodeRabbit"/"real ...
  finding"/"further real" occurrences remain in any of the three files.
  Documentation-only, zero behavior change.

Re-ran after all four round-23 fixes: `./gradlew clean build` — still
green, **447 tests, 0 failures, 0 errors** project-wide (446 + 1 new
test, in `AccountLedgerStoreTest`). Checked individual file counts:
`AccountLedgerStoreTest` 30/30, `AccountLedgerLockTest` 11/11 (unchanged
-- Javadoc-only), `AccountLedgerLockMultiProcessTest` 1/1 (unchanged).
The one real behavior change this round (`AccountLedger`'s alarm-pair
invariant) touches neither `AccountLedgerLock`'s own mutual-exclusion
control flow nor anything the raw stress harness exercises, so a further
raw stress-harness round was not independently warranted.

### Round 24

Against commit `31d4a21` (after round 23's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-17T07:58:07Z`): `CHANGES_REQUESTED`, 1
actionable comment (Trivial), a two-part finding -- one part implemented,
one part declined with reasoning:

- **Implemented: add operational documentation for a human who actually
  hits the missing-ledger-plus-leftover-`.tmp` fail-closed exception,
  describing how to inspect the `.tmp` file and decide whether to
  recover or discard it.** Real, and cheap to address: the exception
  message itself already says "a human must investigate and manually
  resolve," but never spelled out what that investigation concretely
  looks like. Added a new Javadoc paragraph to `load`'s own contract
  documenting the actual procedure: (1) confirm the `.tmp` file's content
  parses as a well-formed `AccountLedger` (the same validation `load`
  itself would apply -- venue/accountId match, no duplicate `
  clientOrderId`, no negative `notional`, no half-populated
  reconciliation alarm pair, all from earlier rounds' own record-level
  invariants); (2) confirm venue/accountId actually match; (3) if both
  check out, it's very likely the real interrupted-persist content --
  rename it over the missing `ledgerPath` and retry; (4) if either check
  fails, don't guess -- exactly the ambiguous case this method already
  refuses to resolve automatically. Documentation-only, zero behavior
  change; automatic recovery remains explicitly out of scope, matching
  the review's own explicit "do not implement automatic recovery"
  instruction.
- **Declined, with reasoning: "emit an alert suitable for operational
  monitoring" for this same fail-closed path.** Real observation in the
  abstract (a permanently-failing ledger really would benefit from
  active alerting in a real deployment), but out of scope for this task
  for reasons grounded directly in this project's own already-committed
  documentation, not merely local judgment: (1) CLAUDE.md's own Tooling
  Stack "Future Tooling Watchlist" already lists "Monitoring/alerting
  (health checks, kill-switch alerts)" with an explicit revisit
  condition -- "Priority #8 (24/7 unattended operation)" -- and an
  explicit reason it isn't done now -- "Nothing runs unattended yet to
  monitor." That reasoning applies to this exact class precisely: `
  AccountLedgerStore` is itself still standalone and unwired (Task B's
  own explicit scope), so there is no running, unattended process yet
  for an alert to reach a human through, and no alerting mechanism
  exists anywhere in this codebase to hook into. (2) The finding's own
  severity (Trivial) and the absence of any committable suggestion --
  unlike essentially every other finding across 23 prior rounds -- is
  itself a signal that even the reviewer could not concretely specify
  what "an alert suitable for operational monitoring" should look like
  in a codebase with no alerting infrastructure yet; inventing one from
  scratch here would be real, undesigned, cross-cutting scope, not a
  local fix to a fail-closed exception message. Documented in the same
  new Javadoc paragraph as the implemented half, rather than silently
  dropped: the `IllegalStateException` message itself is named as this
  task's own real, load-bearing signal in the meantime -- not swallowed,
  and propagates to whatever caller Task C eventually wires in. Matching
  this project's own "document declined suggestions with real reasoning"
  convention, the same standard already applied to the Jackson-BOM
  finding in round 13.

Re-ran after the round-24 fix: `./gradlew clean build` — still green,
**447 tests, 0 failures, 0 errors** project-wide (documentation-only
change, zero new or modified test methods, exact count unchanged from
round 23). No `AccountLedgerLock` involvement, so a further raw
stress-harness round was not independently warranted.

### Round 25

Against commit `1f4d5a5` (after round 24's fix was pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-17T09:06:38Z`): `CHANGES_REQUESTED`, 2
"Actionable comments" plus 1 shown separately as a **"Duplicate
comment"** that was in fact the most consequential finding of the
three -- the fifth consecutive round to find a real, narrower variant of
the same `persist()`-tmp-cleanup-scope theme running since round 18. Two
fixed, one declined with reasoning:

- **(Major, shown as "duplicate" but a real, deeper gap rounds 19/21/22
  left open) `persist`'s tmp-cleanup scope was still not actually
  limited to "a file this call created" -- round 22's own fix (assigning
  `tmp` only after serialization succeeds) closed the serialization-
  failure case specifically, but not this one.** Real: `tmpPathFor`'s
  own fixed path means `FileChannel.open(..., CREATE, TRUNCATE_EXISTING,
  WRITE)` will happily open (and truncate) a genuine crash-leftover
  `.tmp` from an earlier, different `persist()` attempt -- exactly the
  evidence `load`'s own missing-ledger-plus-leftover-`.tmp` fail-closed
  check depends on. If *this* call's own `open`/`write`/`force` step
  then failed (disk full, permission change, filesystem error -- all
  real, not hypothetical), the outer catch's cleanup would delete that
  file, even though this call never created it and its content may
  already be unrecoverably destroyed by the truncate. Fixed by checking
  `Files.exists` on the candidate tmp path *before* ever opening it, and
  only enabling cleanup (assigning the actual `tmp` variable) when this
  call is genuinely the one creating the file fresh -- a pre-existing
  file, crash-leftover or otherwise, is now never touched by any
  failure-cleanup path in this method, regardless of which step fails
  afterward. Proven with a new test
  (`persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall`) at a
  level confirmed empirically deterministic and portable via public
  `java.nio.file` APIs: an *empty* pre-existing directory (not a
  non-empty one, per a standalone probe run before writing the test --
  `Files.deleteIfExists` refuses a non-empty directory regardless of
  this fix, which would have masked the exact property being tested)
  reliably makes `FileChannel.open` throw a real `FileSystemException`
  ("Is a directory") while remaining genuinely deletable -- proving the
  new code never even *attempts* the delete, not merely that some
  deletion attempt happens to fail for an unrelated reason.
- **(Trivial) `AccountLedgerLock`'s `closed` field had no documented
  thread-confinement contract.** Real, though explicitly not a current
  defect (confirmed no caller in this codebase shares an instance across
  threads): `closed` is a plain, non-volatile field mutated only in
  `close()`, so a hypothetical cross-thread sharing could let one thread
  fail to observe another's already-`true` value under the Java Memory
  Model, letting `close()`'s own idempotency guard run twice for real --
  precisely the misleading-log-noise problem that guard exists to
  prevent. Fixed by adding a third caller-contract paragraph to the class
  Javadoc (matching the existing `staleThreshold`-related caller-contract
  paragraphs' own established pattern) stating explicitly that an
  instance must only be used and closed by the thread that acquired it,
  so Task C never introduces cross-thread sharing without knowing this
  contract exists. Documentation-only, zero behavior change -- matching
  the review's own explicit "leave `close()` and `closed` unchanged"
  instruction.
- **(Trivial, "Heavy lift" per the reviewer's own tag) Declined, with
  reasoning: a dedicated test directly exercising `createAndWriteMetadata`'s
  own null-return (lost-race) branch through a real `acquire()` call,
  confirming the *original* caller retries rather than returning a bad
  lock.** Real gap in direct, isolated coverage -- the existing test
  covering the adjacent scenario
  (`aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen`)
  only proves a sibling's content survives a delayed, orphaned write; it
  never routes the *original* holder's own action through a real
  `acquire()` call at all, so it cannot observe that call's own retry
  behavior. Investigated seriously, not dismissed on sight: the specific
  internal window this branch needs (between `createAndWriteMetadata`'s
  own write-close and its own immediately-following re-verification
  read) is a single synchronous method call with no externally
  observable yield point and no injectable test seam (unlike
  `AccountLedgerStore`'s own `AtomicMover`, this class has no comparable
  fault/delay-injection hook for its internal write step). Every
  mechanism considered to hit this window deterministically from outside
  -- racing a second real thread against it, using a pathologically small
  `staleThreshold`, exploiting this mount's own documented 500ms+
  operation latency under contention -- reduces to a genuine race with no
  way to guarantee a single-shot result, the same "Heavy lift" the
  reviewer's own tag names, and the reviewer itself offered no concrete
  committable suggestion for this one, a recurring signal (already
  observed for the round-24 alerting-mechanism decline) that even the
  reviewer could not produce a concrete implementation. Deliberately not
  shipped as a probabilistic/flaky test, which this project's own TDD
  discipline does not accept as a substitute for a real, reliable one --
  and not addressed by adding a production-code test-only hook to
  `AccountLedgerLock` unilaterally, a real design decision affecting
  R3-risk-adjacent code that deserves its own deliberate consideration,
  not one improvised under review pressure on what was flagged as a
  nitpick. This exact mechanism already receives real, if indirect,
  evidence from this file's own extensive existing coverage --
  `acquireProvidesRealMutualExclusionAcrossManyThreads`'s real 12-thread
  contention, the raw stress harness's 255+ clean rounds, and
  `AccountLedgerLockMultiProcessTest`'s real second-JVM proof -- all of
  which exercise many real `acquire()` cycles under genuine contention on
  the real filesystem and would very likely surface a real defect in
  this branch (a wrong lock returned, or lost mutual exclusion) if one
  existed. Matching this project's own "document declined suggestions
  with real reasoning" convention, the same standard already applied to
  the Jackson-BOM finding (round 13) and the alerting-mechanism finding
  (round 24).

Re-ran after both round-25 fixes: `./gradlew clean build` — still green,
**448 tests, 0 failures, 0 errors** project-wide (447 + 1 new test, in
`AccountLedgerStoreTest`). The real behavior change this round (the
tmp-cleanup-scope fix) is entirely within `AccountLedgerStore.persist`,
not `AccountLedgerLock`'s own mutual-exclusion control flow, so a
further raw stress-harness round was not independently warranted.

### Round 26

Against commit `df7e71a` (after round 25's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, `submitted_at: 2026-08-17T12:32:32Z`): `CHANGES_REQUESTED`, 2
"Actionable comments" plus 1 shown separately as a **"Duplicate
comment"** -- a genuine re-raise of round 25's own declined "Heavy lift"
finding, not a new issue. Two real fixes, one decline reaffirmed
explicitly rather than left to look like an oversight:

- **(Reaffirmed decline, not a new finding) `createAndWriteMetadata`'s
  null-return branch still has no dedicated test exercising it through a
  real `acquire()` call.** This is the identical finding round 25's own
  entry above already evaluated in full and declined with real,
  documented reasoning (no externally observable yield point in the
  method's own internal write-close-to-reread window, no injectable test
  seam, every mechanism considered reduces to an unguaranteeable real
  race, the reviewer's own "Heavy lift" tag, no concrete suggestion
  offered either round). CodeRabbit's own re-raise here does not engage
  with or rebut that reasoning -- it restates the same request verbatim,
  consistent with its dup-detection tracking code changes, not
  planning-doc entries explaining a deliberate non-code decision. Re-
  examined once more before reaffirming (not simply repeated by default):
  reconsidered whether `channel.force(true)`'s own documented real
  latency on this drvfs mount under contention could be exploited to
  widen the window enough for a real sibling thread to reliably win the
  race, and concluded this still only shifts the *odds*, not the
  fundamental guarantee -- the window remains a genuine race with no way
  to force a deterministic outcome without either accepting real test
  flakiness or adding a production-code test-only hook, neither of which
  this task's own TDD discipline or scope permits unilaterally. The
  decline, and its full reasoning, stand unchanged from round 25.
- **(Minor) The tmp-ownership assignment (`tmp = candidateTmp`) sat
  inside the `FileChannel.open` try-with-resources body, only after
  `open` had already succeeded -- if `open` itself created the file
  (`CREATE` semantics) but then failed before the channel became usable,
  the just-created empty `.tmp` would be left uncleaned, since `tmp`
  never got assigned.** Real, and the same availability-loss shape as
  every prior round in this theme: for a ledger that had never
  successfully persisted before, that leftover would make every future
  `load` call fail closed permanently. Fixed by deciding ownership
  (`if (!tmpPreexisted) { tmp = candidateTmp; }`) *before* calling `open`,
  not after -- the same `tmpPreexisted` gate from round 25, now covering
  an `open()` failure itself, not only a later write/force failure. The
  core invariant (a pre-existing file is never assigned to `tmp`, so
  it's never a cleanup candidate) is unchanged; only the timing of the
  assignment moved earlier. No new test needed -- round 25's own
  `persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall` already
  exercises the `tmpPreexisted` branch this fix touches, and continues
  to pass unchanged.
- **(Minor) My own round-25 test Javadoc referenced `CREATE_EXISTING` --
  not a real `StandardOpenOption` constant.** Real, a genuine typo on my
  own part (not present in the reviewer's own round-25 suggested text,
  which I had adapted rather than copied verbatim): the actual option
  combination `AccountLedgerStore.persist` uses is `CREATE` + `
  TRUNCATE_EXISTING` + `WRITE`. Corrected directly. Documentation-only.

Re-ran after both round-26 fixes: `./gradlew clean build` — still green,
**448 tests, 0 failures, 0 errors** project-wide (unchanged count -- the
tmp-ownership-timing fix needed no new test, and the Javadoc fix was
documentation-only). The real behavior change this round is entirely
within `AccountLedgerStore.persist`, not `AccountLedgerLock`'s own
mutual-exclusion control flow, so a further raw stress-harness round was
not independently warranted.

### Round 27

Against commit `ae6889a` (after round 26's fixes were pushed). This
round's own verification hit a real, disclosed, non-PR-related anomaly
before the review result could even be checked: a full review request
made at `2026-08-17T13:41:20Z` UTC came back with an explicit `❌ Action
failed / Review failed` acknowledgment, and the primary review-
verification endpoint (`GET /repos/.../pulls/100/reviews`) then started
returning a real, reproducible `HTTP 500` -- confirmed by bypassing the
`gh` CLI entirely with a direct `curl` call, and confirmed independently
by the coordinator against GitHub's own status page, which showed
`API Requests`/`Pull Requests`/`Webhooks`/`Issues`/`Actions` all degraded
at the time, plus an unrelated small PR (#99) failing identically -- a
real GitHub-side outage, not anything caused by this PR's own review
volume or by anything done wrong here. Once the outage cleared (GitHub's
status page back to "All Systems Operational", the REST endpoint back to
`200`), the rate limit was re-confirmed clear via `@coderabbitai rate
limit` and a genuine fresh full review was requested and confirmed
triggered at `2026-08-18T04:12:46Z` UTC -- landing for real at
`2026-08-18T04:23:15Z` UTC, `commit_id` verified via the REST reviews API
to match current HEAD `ae6889a` exactly: `CHANGES_REQUESTED`, 2
actionable comments, no duplicate-comment re-raises this round. Both
addressed with real fixes and real tests:

- **(Minor) `persist()`'s own `tmpPreexisted` ownership check used
  `Files.exists(candidateTmp)`, which conflates "genuinely absent" with
  "existence could not be determined" (an I/O or permission error) into
  the same plain `false`.** Real, and the same class of bug `load()`'s
  own missing-ledger-plus-leftover-`.tmp` check was already hardened
  against in an earlier round -- this was the one remaining place in the
  file still using the weaker `Files.exists` check instead of `load()`'s
  own `Files.readAttributes` + `NoSuchFileException` convention. The
  practical consequence: if `Files.exists` returned `false` because of a
  transient I/O/permission failure rather than a genuinely absent file,
  `tmpPreexisted` would be wrongly recorded as `false`, `tmp` would be
  assigned to `candidateTmp`, and if this same call's own
  `FileChannel.open`/write/move then failed for any reason, the outer
  catch's cleanup would delete a file this call never actually created --
  potentially another process's real, undetermined leftover. Fixed by
  replacing the check with `Files.readAttributes(candidateTmp,
  BasicFileAttributes.class)`, treating only a genuine
  `NoSuchFileException` as "absent" (not pre-existing, this call owns
  it), and any *other* `IOException` as "pre-existing" -- deliberately
  the opposite fail-closed shape from `load()`'s own handling of the same
  ambiguity: `load()` aborts the whole call on a determination failure
  (it has nothing safe to fall back to), while `persist()` here lets the
  call proceed (the imports/`BasicFileAttributes`/`NoSuchFileException`
  it needs were already present in the file from `load()`'s own use) but
  simply refuses to treat the file as eligible for cleanup -- the
  narrower guarantee this specific check exists to provide, "never
  delete a file this call didn't create," without also aborting a
  persist attempt that might otherwise still succeed. Proven with a real,
  deterministic regression test, not asserted: a self-referential symlink
  (`Files.createSymbolicLink(candidateTmp, candidateTmp)`) is a real,
  reproducible way to force exactly this "exists but can't be resolved"
  state -- empirically confirmed via a standalone probe in the scratchpad
  *before* writing the test (this file's own established discipline):
  `Files.readAttributes` on such a path throws a genuine
  `FileSystemException` ("too many levels of symbolic links"), not
  `NoSuchFileException`, and `FileChannel.open` on the same path fails
  identically -- so `persist()` reliably reaches its outer cleanup catch
  with this as the active failure, letting the new test
  (`persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt`)
  assert the symlink is still present afterward
  (`Files.exists(candidateTmp, LinkOption.NOFOLLOW_LINKS)`), i.e. never
  touched by cleanup. Confirmed failing (red) against the pre-fix code
  first, then green after the fix; stable across 3 additional explicit
  reruns.
- **(Trivial) `AccountLedgerLockMultiProcessTest`'s own cleanup `finally`
  block called `Process::destroyForcibly` without ever waiting for the
  process to actually exit.** Real, though narrower than the fixes above
  -- this is a test-only reliability gap, not a production control-flow
  change. `destroyForcibly()` is documented as asynchronous: it requests
  termination and returns immediately, with no guarantee the process has
  actually died by the time the `finally` block (and therefore the test
  method) returns. If an early `fail(...)` fired above and a still-alive
  contender then recreated `lockPath`/`counterPath` after this method
  returned, it would race JUnit's own `@TempDir` cleanup -- a resulting
  directory-not-empty failure would mask this test's real result (its own
  assertions, or a genuine mutual-exclusion failure already reported)
  behind an unrelated secondary exception, defeating the exact "leave a
  precise, undisguised signal" purpose this `finally` block's own
  existing comment (from an earlier round) already describes. Fixed by
  waiting up to 10 seconds for each process's actual exit
  (`process.destroyForcibly().waitFor(10, TimeUnit.SECONDS)`), preserving
  the original per-process cleanup guarantee (every process is still
  destroyed and waited on regardless of whether an earlier `fail(...)`
  already fired) while closing the race window. No new test method --
  this is a change to the test's own cleanup reliability, not to
  `AccountLedgerLock`'s production behavior, so the existing
  `acquireProvidesRealMutualExclusionAcrossGenuinelySeparateOsProcesses`
  test (unchanged) continues to be the coverage; re-run 3 additional
  explicit times (`--rerun-tasks`) after the fix, all green, to confirm
  the added `waitFor` introduces no new flakiness or timeout risk under
  this task's own realistic configuration.

Re-ran after both round-27 fixes: `./gradlew clean build` -- still green,
**449 tests, 0 failures, 0 errors** project-wide (+1 from the new
`AccountLedgerStoreTest` regression test; the multi-process test fix
needed no new test method). The real production-code behavior change
this round (`persist()`'s ownership check) is entirely within
`AccountLedgerStore.persist`, not `AccountLedgerLock`'s own
mutual-exclusion control flow, so a further raw stress-harness round was
not independently warranted; the other fix is test-support-code-only.

### Round 28

Against commit `49d55ed` (after round 27's fixes were pushed) --
`CHANGES_REQUESTED`, landed at `2026-08-18T05:24:15Z` UTC, `commit_id`
verified via the REST reviews API to match HEAD exactly. 2 actionable
comments, no duplicate-comment re-raises. Both real, both fixed with
TDD:

- **(Minor) `persist()` had no identity check of its own -- it serializes
  `ledger` and replaces `ledgerPath` unconditionally, never confirming
  the existing file (if any) actually belongs to the same `venue`/
  `accountId`.** Real, and a genuine asymmetry with `load`,
  which already fails closed on exactly this mismatch on the read side.
  Unlike `load`, `persist` takes no separate "expected identity"
  parameter -- the reviewer's own suggested fix text ("derive the
  expected venue and accountId using the same contract as load") does
  not quite transfer as literally stated, since there is no
  "path-implies-identity" contract anywhere in this codebase for
  `persist` to derive from; the only real signal available to it is
  `ledgerPath`'s own existing content, if any. Implemented that way
  instead: a new private `verifyIdentityConsistency` helper, called at
  the top of `persist` before any tmp-file machinery runs, uses
  `Files.readAttributes` (round 27's own hardened convention, not
  `Files.exists`/`isRegularFile`, both of which swallow an I/O or
  permission error into a plain `false`) to distinguish three cases:
  genuinely absent (`NoSuchFileException`, nothing to check, ordinary
  first persist for this path); exists but is not a regular file (a
  directory left at this path by mistake was never a valid ledger to
  begin with -- deliberately **not** rejected by this check, since
  `persist`'s own existing write/move machinery already fails loudly on
  it, proven by the pre-existing
  `persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails`
  test, which uses exactly this fixture and had to keep passing
  unchanged); and exists as a regular file, which must then parse as a
  matching-identity `AccountLedger` or the call is rejected with
  `IllegalStateException`, leaving the existing file completely
  untouched. Any read/parse failure or a JSON-literal-`null` parse also
  fails closed (throws), matching `load`'s own treatment of those exact
  cases. Deliberately the **opposite** fail-closed direction from round
  27's own `tmpPreexisted` check in this same method -- that check
  treats an undetermined state as "don't delete" (its risk is wrongly
  destroying evidence), this one treats an undetermined state as "don't
  proceed" (its risk is silently overwriting another account's real,
  committed data) -- both documented explicitly in the new helper's own
  Javadoc so a future reader doesn't mistake the difference for an
  inconsistency. Proven with two real tests, not asserted: 
  `persistRefusesToOverwriteAnExistingLedgerForADifferentAccount`
  (persists once for `acct-1`, then attempts a second persist to the
  same path for `acct-2`, asserts the second call throws and the
  original `acct-1` ledger reloads unchanged) and
  `persistRefusesToOverwriteAnExistingFileWithUnparseableContent`
  (pre-writes non-JSON garbage at `ledgerPath`, asserts `persist` throws
  and the garbage is preserved byte-for-byte). Both confirmed failing
  (red) against the pre-fix code first, then green after the fix.
  **A real, self-caught risk during this fix, not left for a future
  round to find**: the obvious naive placement of this new check would
  have broken the pre-existing
  `persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails`
  test (its own fixture -- a non-empty directory at `ledgerPath` -- would
  otherwise have been rejected earlier by a cruder version of this check
  that didn't distinguish "not a regular file" from "unreadable/
  unparseable regular file," changing which code path the test actually
  exercises and failing its own tmp-survival assertion); traced through
  by hand before writing the fix, which is exactly why the check
  explicitly skips non-regular-file occupants rather than rejecting them
  too -- confirmed by re-running that specific test, still green,
  unchanged.
- **(Trivial) Round 27's new
  `persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt`
  test (the self-referential-symlink fixture) had no platform
  restriction -- `Files.createSymbolicLink` can fail with an
  `AccessDeniedException` on Windows without an elevated process or
  Developer Mode enabled, an OS-level privilege requirement this test
  cannot control.** Real, though this project's own CI runs on
  `ubuntu-latest` (confirmed by re-checking `.github/workflows/*.yml`),
  so this changes nothing there -- it only prevents a spurious failure
  for a contributor running this suite natively on Windows. Fixed by
  adding `@EnabledOnOs({OS.LINUX, OS.MAC})` to that one test method, per
  the reviewer's own explicit instruction left unrestricted
  (`persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall`, round
  25's directory-based fixture, relies on no POSIX-only behavior).
  Annotation-only; no behavior change on the platform this task's own
  build actually runs on.

Re-ran after both round-28 fixes: `./gradlew clean build` -- still green,
**451 tests, 0 failures, 0 errors** project-wide (+2 from the two new
`AccountLedgerStoreTest` regression tests; the `@EnabledOnOs` fix was
annotation-only, no new test method). `AccountLedgerStoreTest` reran 3
additional explicit times (`--rerun-tasks`), stable. Both real changes
this round are entirely within `AccountLedgerStore`/
`AccountLedgerStoreTest`, not `AccountLedgerLock`'s own mutual-exclusion
control flow, so a further raw stress-harness round was not
independently warranted.

### Round 29

Against commit `9b29f47` (after round 28's fixes were pushed), landed at
`2026-08-18T06:30:36Z` UTC, `commit_id` verified via the REST reviews API
to match HEAD exactly -- `CHANGES_REQUESTED`, **7 actionable comments,
the largest single-round count in this task** (the previous max was 3,
round 20). This is the first round to touch `AccountLedgerLock.java`
itself since round 25. 6 fixed with TDD, 1 declined with real,
documented reasoning:

- **(Trivial) `close()`'s `EMPTY_OR_UNPARSEABLE` branch was treated as a
  final, unretryable outcome -- the same treatment as a genuine
  different-holder mismatch.** Real, and specific to this project's own
  real drvfs mount: the reviewer's argument is that since `acquire`
  only ever returns a lock after re-verifying its own write, the only
  real ways this instance's *own* generation could later read back
  empty/unparseable on `close` are an external factor (a sibling's
  create-to-write window landing on an already-vacated path -- which
  really is a settled, different-holder situation, correctly final) or a
  transient filesystem read/visibility gap on this instance's own
  still-valid content -- and the second case is exactly the kind of
  thing this project has already measured as real on this specific
  mount (sub-3ms mtime precision, 500ms+ transient I/O latency under
  contention, both cited in this class's own class Javadoc). The two
  cases are not distinguishable from inside this branch, so treating
  both as final risks permanently stranding a lock this instance still
  legitimately owns. Fixed by returning `false` (retryable) here,
  matching `READ_FAILED`'s existing treatment exactly -- `closed` stays
  unset, so a later `close()` call gets a real chance to re-observe this
  instance's own genuine content and complete the delete. `close()`'s
  own top-level Javadoc (the paragraph explaining what `doClose`'s
  boolean return means) updated to match -- it previously grouped
  `EMPTY_OR_UNPARSEABLE` with the genuine-mismatch case as "both
  confirmed alternate states," which is no longer accurate for either
  the code or the reasoning. Proven with a real, deterministic test
  (`closeRetriesAfterObservingEmptyOrUnparseableContentOnAnEarlierAttempt`):
  acquires a real lock, fabricates empty content over its own file
  (simulating the undistinguishable-from-inside condition directly,
  the same technique this file's own existing empty/unparseable-content
  tests already use for `acquire`), calls `close()` once and asserts
  neither deletion nor permanent closure, restores the real content
  (simulating the transient condition resolving), calls `close()` again,
  and asserts the delete now succeeds. Confirmed failing (red) against
  the pre-fix code first, then green after the fix.
- **(Minor) `AccountLedgerLockTest`'s own steal-timing assertion compared
  two `System.nanoTime()` values with a direct `<`, which
  `System.nanoTime()`'s own documented contract does not guarantee is
  correct across a wraparound** (only a *subtraction* is guaranteed
  correct, per the JDK's own documentation -- confirmed by the
  reviewer's own web research, not just asserted). Real, low-probability
  in practice (a wraparound landing between exactly these two captured
  timestamps in one test run) but a real latent bug in test code that
  would fail confusingly if it ever did. Fixed: `stolenAcquiredAtNanos <
  holderReleasedAtNanos.get()` -> `stolenAcquiredAtNanos -
  holderReleasedAtNanos.get() < 0`, same assertion message, no new test
  needed (this is a correctness fix to an existing assertion's own
  comparison operator, not new behavior to cover).
- **(Trivial, doc-only) `load`'s Javadoc had a manual-resolution
  procedure for the missing-ledger-plus-leftover-`.tmp` case (round 24)
  but none for the different scenario of an *existing* `ledgerPath` file
  that exists but cannot be parsed or fails one of its own structural
  checks** (duplicate `clientOrderId`, negative `notional`, a
  half-populated alarm pair, etc.) -- a real, genuine availability loss
  the reviewer traced correctly: since round 28's `persist` identity
  check (`verifyIdentityConsistency`) also parses the existing file the
  same way, `persist` fails closed on this exact case too, meaning the
  only path forward is a human editing the file directly, with no
  documented procedure for how. Fixed, doc-only per the reviewer's own
  explicit instruction ("automatic recovery is deliberately not in
  scope, no code change needed"): a new Javadoc paragraph on `load`
  stating the 3-step procedure (back up, never delete outright;
  determine from other sources -- operational logs, a sibling process's
  state, a real exchange-side query -- whether the real committed
  reservations can be reconstructed; if they can, a human persists a
  deliberately-reconstructed ledger, if not, do not guess and do not let
  either method silently regenerate an empty ledger over unknown real
  exposure), explicitly cross-referencing `persist`'s own identity check
  as sharing the same fail-closed reasoning.
- **(Trivial) `load`'s own third missing-`.tmp`-determination branch
  (genuinely undetermined existence, not merely absent-vs-present) had
  no dedicated test** -- the reviewer's own argument, verified directly:
  the existing tests fix the "absent" and "present" branches, but a
  regression silently reverting the `catch (IOException tmpCheckFailure)`
  block to `return freshLedger(...)` would pass every existing test
  while silently bootstrapping an empty ledger over another process's
  real, committed reservations on a genuinely undetermined state -- the
  exact class of bug this whole file's fail-closed design exists to
  prevent, previously unguarded by a test. Fixed with a new test,
  `loadFailsClosedWhenTheTmpFilesExistenceCannotBeDetermined`, reusing
  this file's own established self-referential-symlink technique (round
  27) verbatim -- a self-referential symlink at the `.tmp` path forces
  the same real `FileSystemException` (not `NoSuchFileException`) that
  technique already relies on. `@EnabledOnOs({OS.LINUX, OS.MAC})` for
  the same reason round 28 added it to the original symlink test. This
  test passed immediately against the *existing* production code
  (confirming the underlying behavior was already correct, only
  previously uncovered) -- not a red-then-green fix, a genuine coverage
  gap closed.
- **(Minor) `AccountLedgerStoreTest` used `{@link FileAlreadyExistsException}`
  in two Javadoc blocks (round 15's and round 19's own test method
  Javadoc) without ever importing the class**, leaving those two
  cross-references unresolved. Real, and a genuine oversight that
  predates this round by several rounds -- simply never caught until
  now. Fixed: added the missing `import java.nio.file.
  FileAlreadyExistsException;`. Documentation-only, no behavior change.
- **(Trivial) `LockContenderMain.main` read `args[0]` through `args[5]`
  with no validation at all** -- a missing or malformed argument would
  surface as a raw `ArrayIndexOutOfBoundsException`/`NumberFormatException`,
  and because this class runs in a genuinely separate JVM (see its own
  class Javadoc), that raw trace is only ever visible in the launching
  test's own captured stderr file, not inline. Real, though purely a
  test-harness debuggability nicety, not a correctness issue (every
  actual call site in this codebase already passes exactly 6
  well-formed arguments). Fixed per the reviewer's own suggested shape:
  an explicit `args.length != 6` check with a clear usage message, plus
  numeric parsing wrapped in a single `try`/`catch (NumberFormatException)`
  that re-throws with the same usage message and the original malformed
  value's own message attached. No new test -- this only changes the
  error path for a launch configuration no real call site in this
  codebase ever produces; the existing
  `AccountLedgerLockMultiProcessTest` (which does exercise this class,
  with valid arguments) continues to pass unchanged, confirming no
  behavior change for every real caller.

**Declined, with real reasoning, not simply skipped**: **(Trivial /
Low value, the reviewer's own tags) the stale-steal success path in
`acquire`'s `catch (FileAlreadyExistsException e)` block retries
immediately (no backoff) whenever `tryStealIfStale` returns `true`, even
for its `metadata == null` sub-case (lines 577-583) where nothing was
actually deleted by this call** -- the file was merely observed already
gone. The reviewer's own concern: under a specific, tightly-interleaved
multi-process race (our create fails, a sibling deletes the file, our
read observes `null`, we retry-create immediately and lose again), this
could spin CPU with no artificial delay between attempts until the
retry budget -- which does still bound it -- is exhausted. Investigated
directly, not reflexively declined: (1) the current immediate-retry
behavior for a confirmed-gone lock is **deliberate, already-documented
design intent** (the method's own comment: "tells the caller to retry
the create loop immediately rather than back off waiting on a holder
that's already gone") -- the overwhelmingly common case this path
serves is a holder that legitimately released between our failed create
and our read, where immediate retry is the *correct*, desirable
behavior, not a bug; slowing it down to guard a rare pathological case
would impose a real cost on every ordinary contended acquire. (2) A
**genuinely correct fix is not the narrow one-line change the reviewer's
own suggested prompt implies.** Direct inspection of `tryStealIfStale`
found the `metadata == null` case is not the *only* `true`-returning
sub-path where nothing was deleted by this call: the real-steal path's
own `catch (NoSuchFileException e)` (around line 633-637, "another
waiter beat us to stealing it") is the identical shape -- `true` is
returned, but the deletion, if any, was performed by someone else, not
this call. The reviewer's finding text names only the first case, but a
fix addressing just that one would leave this second, equally-real one
with the exact same characteristic, an inconsistent, partial correction
rather than a real one. A correct, complete fix needs
`tryStealIfStale`'s own return contract redesigned (e.g. a tri-state
outcome distinguishing "this call performed a genuine deletion" from
"confirmed already gone via any means" from "not stale") -- real,
non-trivial production-code surface area for `AccountLedgerLock`, not a
quick win despite the label. (3) **No way to deterministically test
either version of this fix** was found: reproducing the exact race
(a sibling process or thread deleting the lock file in the narrow
window between this call's own failed create and its subsequent read)
has no externally observable yield point to hook into -- the identical
structural problem this task's own round 25/26 "Heavy lift" finding
already worked through and declined for a different internal race in
this same class (`createAndWriteMetadata`'s null-return branch), and the
same conclusion applies here for the same reason: accepting either real
test flakiness or adding a production-code test-only hook are the only
two ways to force it, and neither fits this task's own TDD discipline or
scope. Given a low-severity, low-value-tagged finding whose complete fix
is real, untested-without-hooks production surface area on
`AccountLedgerLock` -- not the file this round's other six findings
mostly concerned -- this is deferred as a real, disclosed, dedicated
follow-up rather than rushed into this round, matching this task's own
established precedent for handling exactly this shape of finding.

Re-ran after all six round-29 fixes: `./gradlew clean build` -- still
green, **453 tests, 0 failures, 0 errors** project-wide (+2 from the two
new tests -- `closeRetriesAfterObservingEmptyOrUnparseableContentOnAnEarlierAttempt`
in `AccountLedgerLockTest`, `loadFailsClosedWhenTheTmpFilesExistenceCannotBeDetermined`
in `AccountLedgerStoreTest`; the nanoTime, Javadoc-import, and
`LockContenderMain` fixes needed no new test methods, per each finding's
own reasoning above). `AccountLedgerLockMultiProcessTest`,
`AccountLedgerLockTest`, and `AccountLedgerStoreTest` all reran 3
additional explicit times (`--rerun-tasks`) together, stable. This is
the first round since round 16 with a real, non-Javadoc-only behavior
change inside `AccountLedgerLock.java` itself (`close()`'s
`EMPTY_OR_UNPARSEABLE` return value) -- the raw, non-Gradle stress
harness was **not** independently re-run this round, since the change is
confined to `close()`'s own re-verification-read-result handling, not
`acquire`'s create/steal control flow the harness actually exercises
(the harness only calls `acquire`+`close` in its own ordinary, successful
path -- it never fabricates the fail-closed states `close()`'s branches
distinguish); `AccountLedgerLockTest`'s own new, deterministic test is
judged sufficient re-verification for this specific change instead.

### Round 30

Against commit `63a48f5` (after round 29's fixes were pushed), landed at
`2026-08-18T07:32:27Z` UTC, `commit_id` verified via the REST reviews
API to match HEAD exactly -- `CHANGES_REQUESTED`, 2 actionable comments
plus 1 shown separately as a **"Duplicate comment"** -- a genuine,
recurring re-raise of the same review-history-in-comments pattern rounds
12/23 already swept (see those rounds' own entries), not a new finding
class. All three addressed:

- **(Duplicate comment, same recurring pattern as rounds 12/23) Review-
  process narrative ("a real X finding, real CodeRabbit review of this
  PR, round N's own fix...") had re-accumulated in code comments across
  four files since the last sweep** -- specifically in the tmp-ownership
  region of `AccountLedgerStore.persist` (round 27/28's own additions),
  `AccountLedger`'s alarm-pair-invariant and duplicate-`clientOrderId`
  paragraphs, `LedgerReservation`'s positive-`notional` paragraph, and
  two `AccountLedgerStoreTest` method Javadocs (the leftover-`.tmp`-
  overwrite test and the empty-directory-fixture test). Real, and the
  reviewer's own stated reasoning is sound and consistent with this
  project's own established practice for exactly this pattern: round
  numbers become impossible to cross-reference over time, design
  rationale belongs in the code, review history belongs in this
  planning doc or commit messages. Stripped from all four files,
  preserving every substantive invariant/rationale named explicitly in
  the finding (the two ownership/`NoSuchFileException` contracts in
  `AccountLedgerStore`, the alarm-pair invariant and duplicate-
  `clientOrderId` rationale in `AccountLedger`, why `notional` must be
  positive in `LedgerReservation`, and each test's own invariant
  including why the empty-directory fixture must specifically be
  deletable). **Two further instances found and swept during this same
  fix, not named in the finding's own cited line ranges**: the round-29
  manual-recovery-procedure Javadoc addition on `load` and the round-28
  `verifyIdentityConsistency` Javadoc's own "unlike round 27's own
  `tmpPreexisted` check" cross-reference -- both added earlier in this
  same review cycle, before this specific duplicate-comment finding's
  own cited ranges were computed, so CodeRabbit's dup-detection hadn't
  caught them yet; cleaned proactively rather than left for a future
  round to re-flag, since they are the identical pattern this finding is
  about. The `tmpPreexisted`-check cross-reference was kept (it is
  substantive design reasoning, not review-history noise) but reworded
  to drop the round-number reference specifically, replaced with a
  direct "(above)" pointer -- consistent with the reviewer's own stated
  reason for objecting to round numbers in the first place.
  Documentation-only; no behavior change in any of the four files.
- **(New) `AccountLedgerLock`'s own class Javadoc had three "separate
  parts" of its caller contract but was missing a fourth: that `close()`
  returns `void` and therefore signals neither success nor failure, so a
  caller with a real reason to confirm release must call `close()`
  again when a first call took one of `doClose`'s retryable paths.**
  Real, and a natural companion to round 29's own `EMPTY_OR_UNPARSEABLE`
  retry-ability fix earlier this same review cycle -- that fix made
  retrying meaningful, but nothing in this class's own public contract
  told a caller it might ever need to. Fixed with a new fourth
  caller-contract paragraph, explicitly naming that an un-released lock
  is safe to leave alone (it only delays a future waiter until
  `staleThreshold`, never causes incorrect behavior) -- a caller only
  needs to retry `close()` if it has a real reason to confirm release,
  not as a blanket requirement. Documentation-only.
- **(New) `AccountLedgerStoreTest` had a write-path test for `persist`'s
  own `verifyIdentityConsistency` determination-failure branch at the
  `.tmp` path (round 27's own symlink test, reused by round 28's finding
  4/5) but none at `ledgerPath` itself** -- round 28's own new check
  reads `ledgerPath`, not the `.tmp` path, so its own determination-
  failure branch had no dedicated coverage at the path it actually
  reads. Real, genuine coverage gap. Fixed with a new test,
  `persistFailsClosedBeforeWritingWhenTheLedgerPathsExistenceCannotBeDetermined`,
  reusing the same self-referential-symlink technique at `ledgerPath`
  itself rather than the `.tmp` sibling, and additionally asserting the
  candidate `.tmp` file is never created at all -- proving `persist`
  fails closed *before* any write-side machinery runs, not merely that
  it eventually throws. This test passed immediately against the
  *existing* production code (confirming the underlying behavior was
  already correct -- `verifyIdentityConsistency` runs before `Path tmp =
  null` is even declared -- only previously uncovered), the same "real
  gap closed, not a red-then-green fix" shape as round 29's analogous
  `load`-side coverage test.

Re-ran after all three round-30 fixes: `./gradlew clean build` -- still
green, **454 tests, 0 failures, 0 errors** project-wide (+1 from the new
`AccountLedgerStoreTest` regression test; the comment-stripping and
`AccountLedgerLock` Javadoc fixes were documentation-only, no new test
methods). `AccountLedgerStoreTest` reran 3 additional explicit times
(`--rerun-tasks`), stable. None of this round's three changes touch
`AccountLedgerLock`'s own acquire/steal control flow (the class Javadoc
addition is documentation-only), so a further raw stress-harness round
was not independently warranted.

### Round 31

Against commit `3697c24` (after round 30's fixes were pushed), landed at
`2026-08-18T08:34:51Z` UTC, `commit_id` verified via the REST reviews API
to match HEAD exactly -- `CHANGES_REQUESTED`, 3 actionable comments, no
duplicate-comment re-raises. All three real, all three fixed with TDD:

- **(Minor) `AccountLedger`'s compact constructor validated `allocatedVirtualCapital`
  for null but not sign -- a zero or negative stored value could pass
  straight through, including via `AccountLedgerStore.load`, whose two
  separate checks on this field (that a caller-supplied `defaultAllocatedCapital`
  is itself positive, and that a stored value doesn't exceed the
  configured default) do not by themselves reject it: either a zero or a
  negative stored value is always less than a positive default, so both
  checks pass.** Real, grounded directly in CLAUDE.md's own "never
  weaken risk limits... without explicit human approval" rule, and a
  genuine gap in this record's own Javadoc claim (already made for the
  duplicate-`clientOrderId` and alarm-pair invariants) of being "the
  single structural enforcement point" for exactly this class of
  invariant -- `LedgerReservation#notional` already enforces the
  analogous positivity requirement on itself for the identical reason. A
  corrupted or hand-edited ledger file can produce exactly this state.
  Fixed by adding the same `signum() <= 0` check `LedgerReservation`
  already uses, directly in `AccountLedger`'s own compact constructor --
  closing the gap structurally rather than adding a further special case
  to `load`. A new Javadoc paragraph documents the invariant, mirroring
  the existing duplicate-`clientOrderId`/alarm-pair paragraphs' own
  style. Proven with two new tests, mirroring the existing negative-
  `notional` test pair exactly:
  `anAllocatedVirtualCapitalOfZeroOrNegativeIsRejectedByAccountLedgerItself`
  (direct constructor test, zero and negative) and
  `loadFailsClosedWhenTheLedgerFileHoldsANegativeAllocatedVirtualCapital`
  (the same invariant proven through a real ledger file on disk). Both
  confirmed failing (red) against the pre-fix code first, then green
  after the fix. Checked every existing `new AccountLedger(...)` call
  site in the test suite by hand before applying (`grep` for all
  `allocatedVirtualCapital` argument values) -- zero conflicts; every
  other call site already uses a real positive value.
- **(Minor) `persist`'s own `tmpPreexisted` check tracks whether a
  pre-existing `.tmp` should be excluded from failure-cleanup, but does
  nothing to stop this call's own ordinary, successful write path from
  silently consuming that same pre-existing `.tmp` when `ledgerPath`
  itself is also missing** -- exactly the "missing ledger + leftover
  `.tmp`" state `load`'s own fail-closed check treats as evidence of an
  interrupted `persist()` requiring human resolution. Real, and a
  genuinely serious finding, not a nitpick: a real, reachable sequence
  given this class's own documented standalone-`persist`-call use case
  (this class's own Javadoc explicitly describes a caller invoking
  `persist` alone, not only as part of a `load` + mutate cycle) -- a
  host crash mid non-atomic-fallback-move can leave `ledgerPath`
  genuinely missing with its `.tmp` source still lingering, and a
  caller retrying `persist` alone after restart (without calling `load`
  first, which would have caught this) would silently destroy the one
  remaining copy of another process's real, committed reservations via
  the ordinary `CREATE + TRUNCATE_EXISTING` write. Fixed: when
  `tmpPreexisted` is `true`, a further `Files.readAttributes(ledgerPath,
  ...)` check now fails closed (throws `IllegalStateException`, before
  ever opening `candidateTmp` for writing) specifically when `ledgerPath`
  is confirmed absent (`NoSuchFileException`) -- any other outcome
  (`ledgerPath` exists, or its own existence can't be determined either)
  proceeds to the ordinary write path unchanged, preserving the normal
  retry case (`.tmp` pre-existing, `ledgerPath` also pre-existing) this
  method's own leftover-tmp-overwrite behavior exists to support. Proven
  with a new test,
  `persistFailsClosedWhenLedgerPathIsMissingButALeftoverTmpFileExists`
  (confirmed failing (red) against the pre-fix code first, then green
  after the fix) -- and, per the finding's own explicit instruction, the
  existing `persistOverwritesALeftoverTmpFileFromAnEarlierInterruptedAttempt`
  test (which never created `ledgerPath`, and would otherwise have
  newly collided with this fix) now seeds a real, valid prior ledger at
  `ledgerPath` first, so it continues to exercise the *normal* retry
  scenario the fix is careful not to disturb, not the new fail-closed
  one.
- **(Trivial) `verifyIdentityConsistency` distinguishes four failure
  directions for an existing file (determination failure, read failure,
  parse failure, and the JSON literal `null`), but only the parse-
  failure and determination-failure branches had dedicated write-side
  tests** -- the JSON-literal-`null` branch (round 28's own addition,
  where `MAPPER.readValue("null", ...)` returns a plain Java `null`
  without throwing) had no test proving it, even though the read-side
  analogue (`aFileContainingTheJsonLiteralNullFailsClosed`) already
  covers `load`'s own identical branch. Real, previously-uncovered gap
  with a real consequence if regressed: removing that branch's own null
  check would let `existing.venue()` throw a raw `NullPointerException`
  instead of this class's own `IllegalStateException` fail-closed
  contract, and every existing test would still pass. Fixed with a new
  test, `persistRefusesToOverwriteAnExistingFileHoldingTheJsonLiteralNull`,
  the write-side symmetric counterpart to the existing read-side test.
  This test passed immediately against the *existing* production code
  (confirming the underlying behavior was already correct, only
  previously uncovered) -- the same "real gap closed, not a red-then-
  green fix" shape as rounds 29/30's own analogous coverage tests.

Re-ran after all three round-31 fixes: `./gradlew clean build` -- still
green, **458 tests, 0 failures, 0 errors** project-wide (+4 from the four
new tests -- two for the `allocatedVirtualCapital` positivity invariant,
one for the missing-ledger-plus-leftover-`.tmp` write-side fail-closed
fix, one for the write-side JSON-literal-`null` coverage gap).
`AccountLedgerStoreTest` reran 3 additional explicit times
(`--rerun-tasks`), stable. None of this round's three changes touch
`AccountLedgerLock`'s own acquire/steal control flow at all (all three
are confined to `AccountLedger`/`AccountLedgerStore`/`AccountLedgerStoreTest`),
so a further raw stress-harness round was not independently warranted.

### Round 32

Against commit `bdafd0e` (after round 31's fixes were pushed), landed at
`2026-08-18T09:38:01Z` UTC, `commit_id` verified via the REST reviews API
to match HEAD exactly -- `CHANGES_REQUESTED`, 1 actionable comment plus 1
shown separately as a **"Duplicate comment"** -- the same recurring
`createAndWriteMetadata` null-return-branch coverage finding declined in
rounds 25, 26, and 29. One real fix, one decline reaffirmed a fourth
time with a new, permanent disclosure:

- **(Minor) `tryStealIfStale`'s dead-PID check
  (`ProcessHandle.of(metadata.pid())`) only ever consults THIS host's
  own process table -- meaningful only because this project's own
  documented deployment model has every process sharing a lock running
  on the same host. A lock recorded by a genuinely different host (a
  future multi-host deployment, or a misconfiguration) would almost
  certainly read as "not found" on this host regardless of whether its
  real, foreign holder is still alive.** Real, and a genuine gap in the
  class's own documented invariant, even though it does not affect this
  project's *current*, single-host use (every real process sharing a
  lock today calls the same `hostname()` method and resolves to the
  same string, so `holderDead`'s computation is unaffected for the only
  scenario this project actually exercises -- confirmed by the 25-round
  raw stress harness re-run below, not merely reasoned about). Fixed:
  `holderDead` now additionally requires the recorded `hostname` to
  match this host's own current hostname before trusting the
  PID-liveness result; a mismatch is treated as "not provably dead"
  (fails toward not stealing via this path) rather than guessed. The
  independent `expired` check (a lock older than `staleThreshold`) is
  deliberately unaffected -- a foreign-host lock remains reclaimable via
  that path alone, matching the reviewer's own explicit instruction.
  Class Javadoc's own "Contention and staleness" paragraph updated to
  match. Proven with two new tests:
  `acquireDoesNotStealAFabricatedDeadPidLockFromADifferentHostnameWhileFresh`
  (a fabricated dead-looking PID + mismatched hostname + fresh
  `acquiredAt` must NOT be stolen -- confirmed failing (red) against the
  pre-fix code first, then green after the fix) and
  `acquireStealsAFabricatedDeadPidLockFromADifferentHostnameOnceItsTimestampExpires`
  (the same mismatched-hostname lock, but expired, must still be stolen
  via the independent time-based path -- passed both before and after,
  proving that path's own independence directly). **A real, existing
  test would otherwise have broken by this fix, caught and fixed in the
  same pass**: `acquireStealsAFabricatedLockWithADeadPid` fabricated its
  lock with an arbitrary fake `"stale-host"` value, which the dead-PID
  path no longer trusts once hostname must match -- updated to use this
  test's own real, current hostname (via a new `realHostname()` test
  helper mirroring `AccountLedgerLock`'s own private `hostname()`
  method exactly), keeping it a genuine same-host dead-PID test rather
  than accidentally becoming a new fail-to-acquire case.
- **(Duplicate comment, 4th occurrence -- rounds 25, 26, 29, and now
  this one) `createAndWriteMetadata`'s own genuine-generation-mismatch
  `null`-return branch still has no test exercising it directly through
  a real `acquire()` call.** Re-examined fully and freshly, not
  reflexively re-declined -- specifically checked whether this round's
  own hostname-gate fix (immediately above) opened any new seam or
  observable yield point in this exact method; it does not, since that
  fix is entirely within `tryStealIfStale`, a different method with no
  call relationship to `createAndWriteMetadata`'s own internal write-
  then-re-read window. The reasoning is otherwise unchanged from rounds
  25/26/29: the race requires a real second thread or process to
  reclaim `lockPath` via `tryStealIfAbandonedEmpty` in the exact,
  unobservable gap between this method's own write completing and its
  immediately-following re-read -- two sequential lines inside one
  method call, no yield point either could be paused at from outside.
  Closing it for real means either accepting genuine timing-dependent
  test flakiness (against this codebase's own no-flaky-tests
  discipline) or adding a production-code, test-only synchronization
  hook solely to serve one test (real, unrequested production surface
  area this task does not add unilaterally). **New this round, per the
  coordinator's own explicit request given this is the fourth
  occurrence**: rather than let this rest on the planning doc alone
  (which CodeRabbit's own dup-detection tracks code changes against, not
  planning-doc entries, hence the repeat re-raises), a permanent, explicit
  disclosure now lives directly in the code itself -- a new "Known,
  permanent test-coverage gap" Javadoc paragraph on
  `createAndWriteMetadata` stating the gap, why it's declined, and both
  rejected alternatives by name, plus a shorter cross-referencing "What
  this test does NOT cover, by design, not oversight" paragraph on
  `aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen`
  (the closest existing, related test) pointing back to it -- so a
  future reader of either the production method or its closest test sees
  this was a deliberate, reconsidered decision on its own terms, not an
  oversight requiring a planning-doc cross-check to discover.
  Documentation-only; the decline itself, and its reasoning, are
  otherwise unchanged from rounds 25/26/29.

Re-ran after the round-32 fix: `./gradlew clean build` -- still green,
**460 tests, 0 failures, 0 errors** project-wide (+2 from the two new
`AccountLedgerLockTest` tests; the permanent-disclosure Javadoc additions
were documentation-only, no new test methods). `AccountLedgerLockTest`
and `AccountLedgerLockMultiProcessTest` reran 3 additional explicit times
together, stable. **Because this round's real fix is a genuine change to
`AccountLedgerLock`'s own acquire/steal control flow** (`tryStealIfStale`'s
`holderDead` computation) -- the first such change since round 16 -- the
raw, non-Gradle stress harness was re-run for real: **25/25 rounds exactly
correct** (6 processes × 8 iterations = 48 expected per round, the same
realistic configuration as every prior harness run in this task --
**1,200 individual lock acquisitions this round, zero lost updates**),
confirming the hostname gate does not disturb real mutual exclusion under
genuine multi-process contention on this project's actual same-host
deployment scenario, not merely by code inspection.

### Round 33

Against commit `d5778fd` (after round 32's fixes were pushed), landed at
`2026-08-18T10:38:25Z` UTC, `commit_id` verified via the REST reviews
API to match HEAD exactly -- `CHANGES_REQUESTED`, 1 actionable comment.
Real, and a genuine second instance of exactly the shadowing problem
round 31's own `persistOverwritesALeftoverTmpFileFromAnEarlierInterruptedAttempt`
fix already had to solve for a different test in that same round:

- **(Minor) Round 31's own new missing-ledger-plus-leftover-`.tmp`
  fail-closed check (`persist`'s `tmpPreexisted` branch) silently
  shadowed two older tests that never created `ledgerPath` before
  fabricating a `.tmp`-path fixture --
  `persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall` (round
  25, an empty-directory fixture at `candidateTmp`) and
  `persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt`
  (round 27, a self-referential symlink at `candidateTmp`).** Neither
  test ever created `ledgerPath` -- entirely reasonable when each was
  originally written, since round 31's check did not exist yet -- so
  once that check landed, both now hit it first (`tmpPreexisted` true,
  `ledgerPath` absent) and throw `IllegalStateException` *before* ever
  reaching `FileChannel.open`, the actual code each test was written to
  exercise. Both assertions (`assertThrows(IllegalStateException...)`
  plus "the tmp path still exists") kept passing regardless, for the
  wrong reason -- a real regression in the ownership-tracking logic
  either test actually targets would have gone undetected, since
  round 31's own, earlier check would mask it. Verified directly by
  tracing both code paths by hand (not just accepting the finding), and
  fixed the same way round 31's own analogous fix did: both tests now
  seed `ledgerPath` with a real, matching-identity ledger via a real
  `AccountLedgerStore.persist` call *before* fabricating the `.tmp`-path
  fixture, keeping round 31's check from firing and letting execution
  reach each test's own real subject. **Strengthened beyond the
  reviewer's own literal ask**: both tests now also assert the thrown
  exception's message contains `"failed to persist account ledger
  file"` (the real outer-catch message) and, implicitly, does not match
  round 31's own distinct `"refusing to persist to missing"` message --
  proving, not merely restoring by construction, that the intended
  deeper code path is what actually fired. The stale `FileChannel.open`
  comment in `persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt`
  (which, before this fix, described a code path execution no longer
  reached) is corrected to explain the seeding step's own purpose.

Re-ran after the round-33 fix: `./gradlew clean build` -- still green,
**460 tests, 0 failures, 0 errors** project-wide (unchanged count -- both
fixes modified existing test bodies in place, no new test methods).
`AccountLedgerStoreTest` reran 3 additional explicit times
(`--rerun-tasks`), stable. Both changes are confined to
`AccountLedgerStoreTest`'s own test fixtures, with zero production-code
change and zero touch on `AccountLedgerLock` at all, so a further raw
stress-harness round was not independently warranted.

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
  more new test); 19/19 after round 5's (2 more new tests); 21/21 after
  round 6's (2 more new tests); 21/21 after round 7's (no store-level
  changes that round); 21/21 after round 8's (no store-level changes that
  round either); 21/21 after round 9's (no store-level changes that round
  either); 22/22 after round 10's (1 more new test); 22/22 after round
  11's (no store-level changes that round either); 22/22 after round
  12's (a Javadoc-only change to `AccountLedgerLock`, no store-level
  changes); 22/22 after round 13's (a cause-chaining fix and a Javadoc
  contract clarification in `AccountLedgerStore` itself, but no new or
  modified test methods -- no existing test asserted on the affected
  exceptions' cause, confirmed by grep first); 25/25 after round 14's (3
  more new tests, plus one pre-existing test's `defaultAllocatedCapital`
  values repaired to avoid a real conflict with the new fail-closed
  check); 26/26 after round 15's (1 more new test); 26/26 after round
  16's (no store-level changes that round); 26/26 after round 17's (no
  store-level changes that round either); 27/27 after round 18's (1
  more new test, plus one existing test strengthened with an additional
  assertion); 28/28 after round 19's (1 more new test); 29/29 after
  round 20's (1 more new test, plus a Javadoc correction on an existing
  one); 29/29 after round 21's (no new or modified test methods -- a
  single `addSuppressed` line, no test-level change needed); 29/29
  after round 22's (no store-level test methods changed -- the
  `persist` reordering fix needed no new test, per that round's own
  reasoning); 30/30 after round 23's (1 more new test -- the new
  reconciliation-alarm-pair invariant -- plus a Javadoc-stripping sweep
  across three existing test-method Javadoc blocks, no other new
  methods); 30/30 after round 24's (no store-level test methods changed
  -- a Javadoc-only addition to `load`'s own contract, no test-level
  change needed); 31/31 after round 25's (1 more new test -- the deeper
  tmp-cleanup-scope fix -- the "Heavy lift" test was declined, see that
  round's own entry); 31/31 after round 26's (no new or modified test
  methods -- the tmp-ownership-timing fix reused round 25's own test;
  the reaffirmed "Heavy lift" decline still needed no test); 32/32 after
  round 27's (1 more new test -- the `Files.readAttributes`
  determination-failure fix, proven via a self-referential-symlink
  regression test); 34/34 after round 28's (2 more new tests -- the new
  `verifyIdentityConsistency` guard, proven via a mismatched-account
  rejection test and an unparseable-existing-content rejection test; the
  `@EnabledOnOs` addition was annotation-only, no new test method);
  35/35 after round 29's (1 more new test --
  `loadFailsClosedWhenTheTmpFilesExistenceCannotBeDetermined`, closing a
  real, previously-uncovered gap on `load`'s own third missing-`.tmp`-
  determination branch -- plus a missing-import fix, documentation-only);
  36/36 after round 30's (1 more new test --
  `persistFailsClosedBeforeWritingWhenTheLedgerPathsExistenceCannotBeDetermined`,
  closing the write-side analogue of round 29's `load`-side gap -- plus
  the comment-stripping sweep, documentation-only); 40/40 after round
  31's (4 more new tests -- two for the `allocatedVirtualCapital`
  positivity invariant, one for the missing-ledger-plus-leftover-`.tmp`
  write-side fail-closed fix, one for the write-side JSON-literal-`null`
  coverage gap); 40/40 after round 32's (no `AccountLedgerStore`-level
  changes that round -- both real changes were in `AccountLedgerLock`/
  `AccountLedgerLockTest`); 40/40 after round 33's (no new test methods
  -- two existing tests' fixtures corrected to re-seed `ledgerPath`
  first, restoring their own coverage of the ownership-tracking path
  round 31's new check had started shadowing).
- `./gradlew :runtime:test --tests "engine.runtime.AccountLedgerLockTest"`
  — green, 4/4, stable across 3 repeated full re-runs; 7/7 after round
  1's CodeRabbit fixes (3 new tests); 8/8 after round 2's (1 more new
  test); 8/8 after round 3's (existing tests modified, no new methods);
  8/8 after round 4's (existing production code changed, no new test
  methods — see round 4's own entry for why); 8/8 after round 5's (same
  reasoning, see round 5's own entry); 8/8 after round 6's (documentation
  only, no test changes); 9/9 after round 7's (1 more new test); 9/9
  after round 8's (a refactor reusing existing test coverage plus two
  doc/dead-code cleanups, no new methods); 9/9 after round 9's (a
  signature/propagation refactor plus a strengthened existing assertion,
  no new methods); 11/11 after round 10's (2 more new tests); 11/11 after
  round 11's (a Javadoc reference fix and a timing-constant change to an
  existing test, no new methods); 11/11 after round 12's (a Javadoc-only
  rewrite of two doc blocks, no test changes); 11/11 after round 13's (no
  `AccountLedgerLock`-level changes that round -- both real fixes were in
  `AccountLedgerStore`); 11/11 after round 14's (the two real fixes in
  this file were a log-statement repositioning in existing methods, not
  new behavior needing a new test -- both existing steal-path tests
  still fully exercise the delete/no-delete decision itself, which is
  unchanged); 11/11 after round 15's (no `AccountLedgerLock`-level
  changes that round -- the one real fix was a store-level test
  addition); 11/11 after round 16's (four real fixes -- a Javadoc
  reference, a real `close()`/`doClose()` behavioral fix, and two
  existing-test repairs -- but no new test methods, stable across 5
  repeated runs of the retimed deterministic test specifically); 11/11
  after round 17's (a Javadoc-only caller-contract addition, no test
  changes); 11/11 after round 18's (a test-only visibility fix to an
  existing test, no new methods here -- the new test this round was in
  `AccountLedgerStoreTest`), stable across 4 repeated runs (1 + 3 more
  explicit reruns) of the retimed contention test; 11/11 after round
  19's (two existing steal tests strengthened with content assertions,
  no new methods here -- the new test this round was in
  `AccountLedgerStoreTest`); 11/11 after round 20's (no
  `AccountLedgerLock`-level changes that round -- the one real fix was a
  store-level test addition and Javadoc correction); 11/11 after round
  21's (no `AccountLedgerLock`-level changes that round either -- the
  one real fix was entirely in `AccountLedgerStore.persist`); 11/11
  after round 22's (one real fix in this file -- the `nanoTime`
  sentinel-check correction on an existing test, no new methods --
  plus one more, unrelated real fix in `AccountLedgerStore.persist`),
  stable across 4 repeated runs (1 + 3 more explicit reruns) of the
  retimed deterministic test; 11/11 after round 23's (two stale
  `Files#createFile` Javadoc references corrected in this file --
  three more in `AccountLedgerLock.java` itself -- no new methods, no
  behavior change); 11/11 after round 24's (no `AccountLedgerLock`-level
  changes that round -- the one real change was a Javadoc addition in
  `AccountLedgerStore`); 11/11 after round 25's (a thread-confinement
  Javadoc addition to this file, and a declined test-coverage finding --
  see that round's own entry -- but no new methods here; the new test
  this round was in `AccountLedgerStoreTest`); 11/11 after round 26's
  (the "Heavy lift" decline reaffirmed, no `AccountLedgerLock`-level
  code change that round -- both real fixes were in
  `AccountLedgerStore`); 11/11 after round 27's (no changes to this file
  that round -- the two real fixes were in `AccountLedgerStore.java` and
  `AccountLedgerLockMultiProcessTest.java`, not this file); 11/11 after
  round 28's (no changes to this file that round either -- both real
  fixes were in `AccountLedgerStore.java` and
  `AccountLedgerStoreTest.java`); 12/12 after round 29's (1 more new test
  -- `closeRetriesAfterObservingEmptyOrUnparseableContentOnAnEarlierAttempt`
  -- plus the `System.nanoTime()` subtraction-comparison fix to an
  existing assertion, no new method for that one); 12/12 after round
  30's (a class-Javadoc-only fourth caller-contract addition, no
  test-level change); 12/12 after round 31's (no `AccountLedgerLock`-
  level changes that round either -- all three real fixes were in
  `AccountLedger`/`AccountLedgerStore`/`AccountLedgerStoreTest`); 14/14
  after round 32's (2 more new tests -- the hostname-mismatch dead-PID
  pair -- plus an existing test's fabricated hostname updated to stay
  meaningful under the new gate, and the permanent test-coverage-gap
  disclosure, documentation-only); 14/14 after round 33's (no
  `AccountLedgerLock`-level changes that round -- both real fixes were
  in `AccountLedgerStoreTest.java`).
- `./gradlew :runtime:test --tests
  "engine.runtime.AccountLedgerLockMultiProcessTest"` — **failed for
  real** on the first run (19 vs. expected 20 — see "The real finding"
  above); green after the fix, confirmed **5 additional times**
  (`--rerun-tasks`) before round 1's review, **3 more times** after round
  1, **3 more times** after round 2, **3 more times** after round 3,
  **3 more times** after round 4, **3 more times** after round 5, once
  more (part of the full `clean build`) after round 6, **3 more times**
  after round 7, **3 more times** after round 8, **3 more times** after
  round 9, **3 more times** after round 10, once more (part of the full
  `clean build`) after round 11, once more (part of the full `clean
  build`) after round 12, once more (part of the full `clean build`)
  after round 13, **3 more times** after round 14 (1 part of the full
  `clean build`, plus 2 further explicit `--rerun-tasks` runs, given
  round 14's log-repositioning change touches this file's own steal
  paths even though it changes no branching/timing), once more (part of
  the full `clean build`) after round 15, **3 more times** after round
  16 (1 part of the full `clean build`, plus 2 further explicit
  `--rerun-tasks` runs, given round 16's `close()`/`doClose()` fix is a
  real behavioral change, not merely cosmetic), once more (part of the
  full `clean build`) after round 17, once more (part of the full
  `clean build`) after round 18, once more (part of the full `clean
  build`) after round 19, once more (part of the full `clean build`)
  after round 20, once more (part of the full `clean build`) after
  round 21, once more (part of the full `clean build`) after round 22,
  once more (part of the full `clean build`) after round 23, once more
  (part of the full `clean build`) after round 24, once more (part of
  the full `clean build`) after round 25, once more (part of the full
  `clean build`) after round 26, **4 more times** after round 27 (1 part
  of the full `clean build`, plus 3 further explicit `--rerun-tasks`
  runs, given round 27's own real change to this test file's own
  `finally`-block cleanup timing), once more (part of the full `clean
  build`) after round 28, once more (part of the full `clean build`,
  plus round 29's own 3 additional explicit combined reruns noted in the
  full-suite bullet below) after round 29, once more (part of the full
  `clean build`) after round 30, once more (part of the full `clean
  build`) after round 31, **4 more times** after round 32 (1 part of
  the full `clean build`, plus 3 further explicit `--rerun-tasks` runs,
  given round 32's own real change to `AccountLedgerLock`'s acquire/steal
  control flow), and once more (part of the full `clean build`) after
  round 33 (no `AccountLedgerLockMultiProcessTest`-level change that
  round -- both real fixes were in `AccountLedgerStoreTest.java`).
- A raw, non-Gradle stress harness (`LockContenderMain` launched directly
  via `ProcessBuilder`-equivalent manual invocation, bypassing Gradle's
  own test-launch overhead to run many more real-process rounds in
  reasonable wall-clock time) — **30/30 rounds exactly correct** after the
  original TOCTOU fix, **25/25 more (clean)** after round 1's CodeRabbit
  fixes, **25/25 more** after round 2's, **25/25 more** after round 3's,
  **25/25 more** after round 4's, **25/25 more** after round 5's,
  **25/25 more** after round 7's, **25/25 more** after round 8's,
  **25/25 more** after round 9's, **25/25 more** after round 10's, and
  **25/25 more** after round 16's (6
  processes × 8 iterations = 48 expected per round every time, at this
  task's own realistic `staleThreshold` values: **280 clean rounds
  total, 13,440 individual lock acquisitions, zero lost updates** across
  the whole task's real, non-Gradle stress testing at realistic
  configuration — round 6 was documentation/record-validation only and
  did not touch `AccountLedgerLock`'s own acquisition control flow, so it
  did not independently warrant a further raw stress-harness round on
  top of the full `clean build`'s own real multi-process test run; round
  10 *separately* also produced a real, reproducible, deliberately
  pathological-configuration failure — see that round's own entry — which
  is the point of that round's new dedicated deterministic test, not a
  regression in this realistic-configuration count; round 11 made no
  control-flow change to `AccountLedgerLock`'s acquisition logic (a
  Javadoc reference fix and a timing-constant change to an existing test
  only), so it did not independently warrant a further raw stress-harness
  round beyond the full `clean build`'s own real multi-process test run;
  round 12 was a Javadoc-only rewrite with zero runtime-behavior change,
  for the same reason; round 13's two real fixes were both in
  `AccountLedgerStore` (an exception-cause-chaining fix and a Javadoc
  contract clarification), not `AccountLedgerLock`'s own acquisition
  control flow, so it likewise did not independently warrant a further
  raw stress-harness round; round 14's `AccountLedgerLock` fix
  repositioned two `log.error` statements relative to unchanged
  delete/no-delete control flow -- no new branching or timing for the
  raw harness to exercise differently, so the `AccountLedgerLockMultiProcessTest`
  reruns above were judged sufficient re-verification without a further
  raw stress-harness round on top of them; round 15's one real fix was a
  new `AccountLedgerStore` test with no `AccountLedgerLock` involvement
  at all, so it likewise did not independently warrant a further raw
  stress-harness round; round 16's `close()`/`doClose()` fix, by
  contrast, is a real change to the release-path's own retry-ability
  contract -- extra rigor judged warranted here, so the raw harness was
  re-run for the first time since round 10; round 17 was Javadoc-only
  with zero behavior change, so it did not independently warrant a
  further raw stress-harness round, matching the pattern already
  established for every other purely-documentation round; round 18's
  two real fixes were entirely in `AccountLedgerStore.persist` and a
  test-only visibility fix, neither touching `AccountLedgerLock`'s own
  acquire/steal control flow, so it likewise did not independently
  warrant a further raw stress-harness round; round 19's most serious
  fix was also entirely in `AccountLedgerStore.persist` (the tmp-
  cleanup regression), with the other two being Javadoc/test-only, so
  it likewise did not independently warrant a further raw
  stress-harness round; round 20 was a single test-only fix (plus a
  Javadoc correction) with zero production code change at all, and no
  `AccountLedgerLock` involvement, so it likewise did not independently
  warrant a further raw stress-harness round; round 21's one real fix
  was also entirely in `AccountLedgerStore.persist` (an
  `addSuppressed` addition, purely additive to the exception chain, no
  control-flow change), so it likewise did not independently warrant a
  further raw stress-harness round; round 22's two real fixes were a
  statement-reordering fix in `AccountLedgerStore.persist` (no new
  branch, no observable behavior change) and a test-assertion
  correction in `AccountLedgerLockTest` (no production code touched at
  all), so it likewise did not independently warrant a further raw
  stress-harness round; round 23's one real behavior change (the
  `AccountLedger` alarm-pair invariant) touches neither
  `AccountLedgerLock`'s own mutual-exclusion control flow nor anything
  the raw harness exercises, and the Javadoc-sweep findings changed no
  behavior at all, so it likewise did not independently warrant a
  further raw stress-harness round; round 24 was Javadoc-only with zero
  behavior change and no `AccountLedgerLock` involvement, so it
  likewise did not independently warrant a further raw stress-harness
  round; round 25's one real behavior change (the deeper tmp-cleanup-
  scope fix) is entirely within `AccountLedgerStore.persist`, not
  `AccountLedgerLock`'s own mutual-exclusion control flow, so it
  likewise did not independently warrant a further raw stress-harness
  round; round 26's one real code change (the tmp-ownership-timing fix)
  is likewise entirely within `AccountLedgerStore.persist`, so it
  likewise did not independently warrant a further raw stress-harness
  round; round 27's two real fixes were the `Files.readAttributes`
  determination-failure fix (also entirely within
  `AccountLedgerStore.persist`) and a cleanup-timing fix confined to
  `AccountLedgerLockMultiProcessTest`'s own test code, neither touching
  `AccountLedgerLock`'s own acquire/steal control flow, so it likewise
  did not independently warrant a further raw stress-harness round --
  the 4 additional `AccountLedgerLockMultiProcessTest` reruns above were
  judged sufficient re-verification for that round's own test-cleanup
  change instead; round 28's two real fixes (the new
  `verifyIdentityConsistency` guard and the `@EnabledOnOs` annotation)
  are both entirely within `AccountLedgerStore`/`AccountLedgerStoreTest`,
  with zero touch on `AccountLedgerLock`'s own acquire/steal control
  flow, so it likewise did not independently warrant a further raw
  stress-harness round; round 29's one real `AccountLedgerLock.java`
  behavior change (`close()`'s `EMPTY_OR_UNPARSEABLE` return value) is
  confined to `doClose`'s own re-verification-read-result handling, not
  `acquire`'s create/steal control flow the raw harness actually
  exercises -- the harness's own contenders only ever call
  `acquire`+`close` along the ordinary, successful path, never
  fabricating the fail-closed states `close()`'s branches distinguish --
  so it likewise did not independently warrant a further raw
  stress-harness round; the round's other five fixes (a test-assertion
  operator fix, a doc-only Javadoc addition, a new `AccountLedgerStore`-
  level test, a missing test import, and `LockContenderMain`'s own
  argument validation) touch no `AccountLedgerLock` control flow at
  all; round 30's three fixes -- a comment-stripping sweep (documentation
  only, across four files), a class-Javadoc-only fourth caller-contract
  addition to `AccountLedgerLock`, and a new `AccountLedgerStore`-level
  test -- likewise touch no `AccountLedgerLock` acquire/steal control
  flow, so it likewise did not independently warrant a further raw
  stress-harness round; round 31's three fixes (the `AccountLedger`
  `allocatedVirtualCapital` positivity check, the `persist`-side missing-
  ledger-plus-leftover-`.tmp` fail-closed fix, and the write-side
  JSON-literal-`null` coverage test) are all confined to `AccountLedger`/
  `AccountLedgerStore`/`AccountLedgerStoreTest`, with zero touch on
  `AccountLedgerLock` at all, so it likewise did not independently
  warrant a further raw stress-harness round; round 32's real fix (the
  `holderDead` hostname gate in `tryStealIfStale`), by contrast, IS a
  real change to `AccountLedgerLock`'s own acquire/steal control flow --
  the first since round 16 -- so the raw harness WAS re-run for real
  this round: **25/25 rounds exactly correct** (6 processes × 8
  iterations = 48 expected per round, this task's own realistic
  configuration -- **1,200 individual lock acquisitions this round,
  zero lost updates**), bringing the task-wide raw-harness total to
  **305 clean rounds, 14,640 individual lock acquisitions, zero lost
  updates** across every round this harness has actually been re-run.
  The decline (the `createAndWriteMetadata` null-return-branch coverage
  gap, reaffirmed a fourth time) is documentation-only and needed no
  further raw-harness verification on its own. Round 33's two fixes are
  both confined to `AccountLedgerStoreTest`'s own test fixtures (no
  production-code change at all), so it likewise did not independently
  warrant a further raw stress-harness round.
- `./gradlew :runtime:test` (full module suite) — green, confirmed 3
  times (`--rerun-tasks`) before round 1's review, once more after round
  1, part of the full `clean build` runs after rounds 2, 3, 4, 5, 6, 7,
  8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
  26, 27, 28, 29, 30, 31, 32, and 33 (plus round 27's own 4 additional
  explicit `AccountLedgerLockMultiProcessTest` reruns and 3 additional
  explicit `AccountLedgerStoreTest`-new-test reruns; round 28's own 3
  additional explicit `AccountLedgerStoreTest` reruns; round 29's own 3
  additional explicit combined reruns of `AccountLedgerLockMultiProcessTest`,
  `AccountLedgerLockTest`, and `AccountLedgerStoreTest` together; round
  30's own 3 additional explicit `AccountLedgerStoreTest` reruns; round
  31's own 3 additional explicit `AccountLedgerStoreTest` reruns; round
  32's own 3 additional explicit combined reruns of
  `AccountLedgerLockTest` and `AccountLedgerLockMultiProcessTest`
  together, plus the 25-round raw stress harness re-run; round 33's own
  3 additional explicit `AccountLedgerStoreTest` reruns, all noted
  above).
- `./gradlew clean build` (full six-module suite, clean, not incremental)
  — **BUILD SUCCESSFUL**. Summed real JUnit XML reports across every
  module (`schemas`, `oms`, `risk`, `execution`, `exchange`, `runtime`):
  **460 tests, 0 failures, 0 errors** (405 pre-existing from Task A's
  merged state + 15 from this task's original implementation + 7 from
  round 1's CodeRabbit review + 3 from round 2's + 0 net-new from
  round 3's + 1 from round 4's + 2 from round 5's + 2 from round 6's + 1
  from round 7's + 0 net-new from round 8's + 0 net-new from round 9's +
  3 from round 10's + 0 net-new from round 11's + 0 net-new from round
  12's + 0 net-new from round 13's + 3 from round 14's + 1 from round
  15's + 0 net-new from round 16's + 0 net-new from round 17's + 1 from
  round 18's + 1 from round 19's + 1 from round 20's + 0 net-new from
  round 21's + 0 net-new from round 22's + 1 from round 23's + 0
  net-new from round 24's + 1 from round 25's + 0 net-new from round
  26's + 1 from round 27's + 2 from round 28's + 2 from round 29's + 1
  from round 30's + 4 from round 31's + 2 from round 32's + 0 net-new
  from round 33's).
- PR to be opened, not merged — per the governing task brief and
  CLAUDE.md's Auto-merge Policy, this is Java runtime/Risk-Gateway-
  adjacent code and requires explicit human sign-off regardless of
  CI/CodeRabbit status.
