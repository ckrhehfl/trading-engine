# Shared KIS account risk ledger, Task C: `SharedKisAccountLedger` + wiring

## Scope note

This is **Task C** of the 4-task "Shared KIS account risk ledger" plan
(`.claude/plans/tender-finding-matsumoto.md`'s "Shared KIS account risk
ledger (multi-process risk budget)" section). Task A (`AccountStateProvider`
extraction, PR #99) and Task B (`AccountLedgerLock`/`AccountLedgerStore`,
PR #100) are complete and merged to `main`. Task C composes both into a
real `AccountStateProvider` implementation and wires it into `TradingLoop`'s
new 7-arg constructor and `PaperTradingApp`'s `kis-paper` mode. Task D
(`AccountLedgerReconciler` — startup bootstrap-from-real-balance is
reused here, but the *ongoing*, cadenced reconciliation pass and the
alarm-*tripping* logic are not) is explicitly out of scope and not
attempted.

R3-risk component (`java/runtime`, the seam feeding `RiskGateway.evaluate`
via `OrderPipeline`/`TradingLoop`) — TDD discipline applied throughout, per
CLAUDE.md's Development Methodology.

### GSD phase status

- **Discuss**: resolved by the governing task brief, which specified the
  exact behavior contract for all three `AccountStateProvider` methods,
  the bootstrap decision, the file-path convention, and the wiring
  sequence. The one design question the brief left to this task's own
  judgment — real multi-*process* test vs. multi-*threaded* — is recorded
  below.
- **Plan**: the governing task brief is the plan; this doc's "What was
  built" and "Judgment calls" record how the real code matched or
  deviated from it.
- **Execute**: this document's "What was built" section.
- **Verify**: this document's "Verification" section — real local test
  runs (`./gradlew build`), not a claim that tests would pass.
- **Ship**: **pending.** PR to be opened, CI to run, CodeRabbit review
  requested — per the governing task brief's own explicit instruction and
  CLAUDE.md's Auto-merge Policy (Java runtime/Risk-Gateway-adjacent code
  is excluded from delegated auto-merge regardless of CI/CodeRabbit
  status), **this PR is not to be merged by this session.**

## What was built

Two new files, one modified production file, one modified test file
(gaining new test methods only — zero modifications to any existing test):

- **`engine.runtime.SharedKisAccountLedger`** (new, package-private,
  `java/runtime/src/main/java/engine/runtime/SharedKisAccountLedger.java`)
  — `implements AccountStateProvider`. Composes `AccountLedgerLock` +
  `AccountLedgerStore`:
  - `reserveForIntent(intent, referencePrice)`: acquire lock → load →
    if `reconciliationAlarmTrippedAt` is non-null, return a
    floored-near-zero-equity snapshot (equity = `BigDecimal.ONE`) without
    creating a reservation → else resolve the effective price (mirrors
    `RiskGateway.evaluate`'s own `intent.limitPrice() != null ?
    intent.limitPrice() : referencePrice` logic) → if that price is
    `<= 0`, return the ledger's ordinary current snapshot without creating
    a reservation → else create/merge a `LedgerReservation` sized to
    `intent.quantity() × price`, monotonically (never shrinking an
    existing reservation for the same `intentId` — see `reserveMonotonic`)
    → persist → return `equity = allocatedVirtualCapital −
    Σ(reservations.notional)`, floored at `BigDecimal.ONE`.
  - `confirmReservation(intentId, approvedQuantity, price)`: acquire lock
    → load → find the matching reservation by `clientOrderId == intentId`
    → shrink it to `approvedQuantity × price` (or drop it entirely if that
    product is `<= 0`, since `LedgerReservation`'s own compact constructor
    rejects a non-positive `notional`) → persist. A miss (no matching
    reservation) is logged and treated as a no-op, not an error — see
    "Judgment calls" below for why this is safe.
  - `releaseReservation(intentId)`: acquire lock → load → remove the
    matching reservation entirely → persist. Same no-op-on-miss tolerance
    as `confirmReservation`.
  - `static bootstrapOrLoad(ledgerPath, venue, accountId, realBalance)`:
    the one-time (per process) bootstrap-or-reuse step described in the
    governing task brief. Acquires the lock once, calls
    `AccountLedgerStore.load` with `realBalance` as the configured
    default, then **unconditionally** calls `AccountLedgerStore.persist`
    on whatever `load` returned (safe even when the file already existed
    — see "Judgment calls"), and returns a `SharedKisAccountLedger` whose
    own `defaultAllocatedCapital` is fixed to whatever `load` determined
    (the fresh `realBalance` on first creation, or the untouched
    already-persisted value on reuse). This fixed value is what every
    later `reserveForIntent`/`confirmReservation`/`releaseReservation`
    call passes to `AccountLedgerStore.load` for the rest of this
    instance's lifetime — the real KIS balance is never re-queried
    per-tick, matching the governing plan's own "virtual ledger + periodic
    reconciliation, not a live balance check on every risk decision"
    model.
  - No explicit extra `AccountLedgerLock#requireHeld()` call anywhere in
    this class — see "Judgment calls" below.

- **`PaperTradingApp` changes**
  (`java/runtime/src/main/java/engine/runtime/PaperTradingApp.java`):
  - New `static Path resolveAccountLedgerPath(String venue, String
    accountId)` — `var/live/{venue}-{accountId}-account_ledger.json`,
    same traversal-guard validation as the existing
    `resolveKisSubmissionMarkersPath(symbol)`, but deliberately keyed by
    `(venue, accountId)`, **not** `symbol` — this ledger is meant to be
    *shared* across every `kis-paper` process trading different symbols
    against the same real account, unlike the per-symbol submission-
    markers path.
  - New 9-arg `PaperTradingApp` constructor overload — the same eight
    parameters as the existing `PriceFeed`-accepting (KIS-specific) 8-arg
    constructor, plus a trailing `AccountStateProvider accountStateProvider`
    threaded through to `TradingLoop`'s new 7-arg constructor. The
    existing 8-arg overload is **completely untouched** — still builds
    `TradingLoop` via the 6-arg constructor exactly as before.
  - `forKisPaper()`: after `KisPreflight.run(adapter)` succeeds and
    before the `SubmissionMarkerStore`/`SubmissionMarkerResolver` step,
    now resolves the ledger path and calls
    `SharedKisAccountLedger.bootstrapOrLoad(accountLedgerPath, "KIS",
    accountNo, preflight.balance().balance())`, then passes the result
    into the new 9-arg constructor instead of the old 8-arg one. The
    trailing **unconditional** `app.killSwitch.trip()` call and its log
    message are **byte-for-byte unchanged** — confirmed by direct diff
    inspection, not just by intent (see "Verification").

- **Tests** (`PaperTradingAppTest.java`, new methods only):
  `resolveAccountLedgerPathIsKeyedByVenueAndAccountNotSymbol`,
  `resolveAccountLedgerPathRejectsPathSeparatorsAndTraversalSegments`,
  `nineArgKisConstructorRejectsNullAccountStateProvider`,
  `constructingWithNineArgKisConstructorUsesTheInjectedAccountStateProvider`,
  `unconditionalKillSwitchTripStillBlocksNewSignalSubmissionWithASharedLedgerPresent`
  (the Task C regression test — see "Judgment calls" for why it can't
  literally invoke `forKisPaper()`).

- **`SharedKisAccountLedgerTest.java`** (new, 15 tests) — unit coverage of
  all three `AccountStateProvider` methods in isolation, the bootstrap/
  reuse/fail-closed paths, the equity floor, the monotonic-reservation
  rule, and the real multi-threaded concurrency stress test (see below).

## Judgment calls

### 1. Multi-threaded, not multi-process, for the concurrency proof

The governing task brief left this explicitly open ("a real second-JVM
test via `ProcessBuilder`... your call, but justify it either way").
**Decision: real threads, not a real second JVM.**

Reasoning: Task B's own `AccountLedgerLockMultiProcessTest` already
proved, for real, that the underlying atomic-file-creation primitive
(`AccountLedgerLock.acquire`/`close`) provides genuine mutual exclusion
across *separate JVM processes* on this repository's real drvfs mount —
that is not a property this task needs to re-prove. What Task C adds on
top is a **new composition layer**: `SharedKisAccountLedger`'s own
read-modify-write logic (load → mutate the in-memory snapshot → persist)
and its wiring into `TradingLoop`. A bug in *that* layer — e.g.
forgetting to hold the lock across the whole read-then-write cycle, or a
non-monotonic reservation merge that lets a concurrent caller's update get
silently lost — would be caught identically by many real threads racing
on the same ledger file as by many real separate processes, because
`AccountLedgerLock`'s own mutual-exclusion logic has **no in-process
vs. cross-process distinction whatsoever** — exclusivity comes entirely
from `Files.newByteChannel(..., CREATE_NEW)` on a real file, which is
exactly as exclusive across threads in one JVM as across separate JVMs.
Real threads also make the test's own failure mode (a lost update
producing a final committed-notional sum over `allocatedVirtualCapital`)
fast and deterministic to run (the whole `SharedKisAccountLedgerTest`
suite runs in ~3.3s; the concurrency test itself in under 1s), without
needing to duplicate Task B's own `ProcessBuilder`/classpath-wiring
machinery for a property this task doesn't need to re-establish.

### 2. No extra `requireHeld()` call needed in `SharedKisAccountLedger`

The task brief asked me to verify, from the real merged code, whether
`SharedKisAccountLedger`'s own reserve/confirm/release methods need an
explicit `lock.requireHeld()` call between `AccountLedgerStore.load` and
`AccountLedgerStore.persist`, matching the "re-verify immediately before
trusting" discipline `AccountLedgerStore.persist` already applies
internally.

Reading the real merged code (`AccountLedgerStore.java`): **both `load`
and `persist` already call `lock.requireHeld()` internally on every
invocation** — `load` once, `persist` twice (once at its own entry, once
again immediately before the actual truncating write, specifically to
narrow the TOCTOU window between validating the lock and the write
happening). This is exactly the "hold lock across a read then a later
write" pattern the task description described — but it's already fully
enforced *inside* `AccountLedgerStore` itself, not something a caller
needs to additionally invoke. Adding a third `requireHeld()` call in
`SharedKisAccountLedger` between its own `load` and `persist` calls would
be redundant (three checks instead of two, narrowing nothing further,
since the in-memory mutation between them touches no disk state and takes
negligible time) — so none was added. `SharedKisAccountLedgerTest`'s own
tests exercise the real `load`→persist cycle end-to-end and pass, which is
the practical confirmation that the existing two-call discipline is
sufficient for this composition.

### 3. New `PaperTradingApp` constructor overload, not a modified existing one

The existing 8-arg `PriceFeed`-accepting constructor (added in KIS/KOSPI200
Phase 1 Task 4) has six existing tests exercising it directly (`TradingCalendar`
gating, `OrderExecutor` injection, the KIS-specific delivered-marker
filename). Widening that constructor's signature to require an
`AccountStateProvider` would have broken every one of those six call
sites for no benefit — none of them are testing ledger behavior, they're
testing `TradingCalendar`/`OrderExecutor`/`PriceFeed` wiring, which is
completely orthogonal to whether a real or synthetic `AccountStateProvider`
is behind it. Adding a **new** 9-arg overload instead — matching this
codebase's own established "new overload, not a modified existing one"
precedent (the exact same pattern `TradingLoop`'s Task A 7-arg constructor
itself used relative to its own existing 6-arg one) — means all six
existing tests needed **zero changes**, confirmed by running the full
`PaperTradingAppTest` suite unmodified except for new test *additions*.
`forKisPaper()` is the only real production caller of the new overload;
the old 8-arg overload remains fully reachable (it isn't dead code — no
existing call site was removed) for any future caller that genuinely
wants `TradingLoop`'s default synthetic provider with a `PriceFeed`/
`TradingCalendar` wired in.

### 4. `bootstrapOrLoad` always persists, even when the file already existed

Rather than doing a separate `Files.exists(ledgerPath)` check to decide
whether to persist (which has its own subtlety — see `AccountLedgerStore
.load`'s own Javadoc on why `Files.exists` swallows I/O/permission errors
into a misleading plain `false`), `bootstrapOrLoad` always calls
`AccountLedgerStore.persist` on whatever `AccountLedgerStore.load` just
returned, unconditionally. This is provably safe: `persist`'s own
identity-consistency check compares the ledger being persisted against
whatever is already on disk and rejects only a genuine *increase* in
`allocatedVirtualCapital` or a genuine identity mismatch — persisting back
the *exact same object* `load` just returned (same venue/accountId,
same `allocatedVirtualCapital`, same reservations) always passes both
checks trivially (the comparisons are `>`, not `>=`). This removes a
whole class of potential existence-check-vs-load-result race entirely,
rather than trying to reason about it.

### 5. `confirmReservation`/`releaseReservation` tolerate a missing reservation as a no-op

The task brief's contract description doesn't explicitly say what should
happen if the reservation being confirmed/released isn't found. Analysis
of `TradingLoop.tick()`'s own real call sequence shows this is a **real,
reachable, and entirely legitimate** case, not just defensive
programming: `reserveForIntent` deliberately skips creating a reservation
in exactly two cases — a tripped reconciliation alarm, or a non-positive
effective price. In both cases, `RiskGateway.evaluate` is guaranteed to
reject the resulting intent (a floored near-zero equity makes the clamped
quantity round to zero; a non-positive price is rejected outright before
equity is even consulted) — so `TradingLoop.tick()`'s own `else` branch
always calls `releaseReservation(intent.intentId())` for exactly the
`intentId` that was never reserved. Treating this as a hard failure would
turn an expected, safe skip into a spurious tick error. Logged (`WARN` for
`confirmReservation`, `DEBUG` for `releaseReservation` — the former is
the more surprising of the two, since it's only supposed to fire on an
approved/modified decision), not thrown, and verified directly by
`confirmReservationIsANoOpWhenNoMatchingReservationExists`/
`releaseReservationIsANoOpWhenNoMatchingReservationExists`.

### 6. Equity floor value: `BigDecimal.ONE`

The task brief says "floored at a small fixed positive constant." Chosen
`BigDecimal.ONE` (1, in whatever currency unit `allocatedVirtualCapital`
is denominated — real KRW, for the eventual real KIS account) rather than
something like `0.01`: any real KOSPI200 index-futures or individual-
stock-futures contract's notional (index points, or a real per-share
price, times a real per-contract multiplier — see CLAUDE.md's own
disclosed, still-open contract-multiplier gap) is many orders of magnitude
larger than 1 KRW, so `RiskGateway`'s own existing "clamped quantity
rounds to zero → reject" path is guaranteed to fire regardless of which
small positive floor is chosen; `1` was picked for readability over an
arbitrarily smaller fraction with no added protective value.

### 7. The `forKisPaper()` regression test is a proxy, not a literal invocation — disclosed, not silently assumed

The task brief asked for "a regression test [that] must confirm
`forKisPaper()`'s unconditional `KillSwitch.trip()` still fires with the
ledger present." Investigated directly before writing this test: `forKisPaper()`
is `private`, reads real environment variables via `System.getenv()`
(this codebase has no env-var mocking framework — confirmed by
`PaperTradingAppTest`'s own class Javadoc, which states this explicitly
for `forBingXVst()`, the pre-existing analogous case), and constructs a
real `KisAdapter`/`KisTokenProvider` pointed at the **hardcoded**
`KIS_PAPER_BASE_URL` Java constant (`private static final String`, a true
compile-time constant inlined by `javac` at every use site within the same
class — confirmed this cannot be redirected via reflection, since
`forKisPaper()`'s own bytecode never re-reads the field at runtime). This
is the exact same, already-established untestability `forBingXVst()` has
always had (confirmed: no test in this codebase has ever called
`forBingXVst()` directly either) — not a gap introduced by this task, and
not something a mocking-framework-free codebase can practically close
without either a real network call to the real KIS host or reflection
tricks this codebase's own conventions don't use elsewhere.

Given that, the regression test
(`unconditionalKillSwitchTripStillBlocksNewSignalSubmissionWithASharedLedgerPresent`)
instead: (1) constructs a real `PaperTradingApp` via the new 9-arg
constructor, with a **real**, temp-file-backed `SharedKisAccountLedger`
(via `bootstrapOrLoad` — the file is genuinely created and verified
present on disk, not a fake `AccountStateProvider`), a `FakePriceFeed`, a
`FakeTradingCalendar(true)`, and a real `PaperBroker`; (2) replicates
`forKisPaper()`'s own trailing statement verbatim —
`app.killSwitch().trip()`; (3) runs a real `runTick()` against a real,
already-written signal file; and (4) asserts the kill switch stays
tripped, `tick()` itself still ran (fill polling continues — `lastTickAt()`
is non-null), but **no order was ever created** (`submittedOrderIds()`
empty, and the intent's `clientOrderId` was never registered in
`OrderStore`) — proving the trip genuinely blocks the real new-signal path
all the way through `TradingLoop`, `OrderPipeline`, and
`SharedKisAccountLedger.reserveForIntent`, with a real ledger file
genuinely present throughout. This is the closest achievable proxy given
this codebase's real testability boundary, not a claim that
`forKisPaper()`'s own source was executed. `forKisPaper()`'s own edit was
kept to the smallest possible diff (only the ledger bootstrap insertion
and the constructor-call/log-line additions) specifically so the
untouched trip statement could also be confirmed correct by direct code
reading, not testing alone.

### 8. `LedgerReservation.clientOrderId` is keyed by `intentId`, confirmed, not assumed

`AccountStateProvider`'s methods take `intentId`/`OrderIntent`, while
`LedgerReservation` (Task B) carries `clientOrderId` and explicitly defers
"which identifier a real implementation keys by" to this task. Confirmed
by reading `engine.oms.Order` directly: `Order.fromApprovedDecision`
constructs `clientOrderId` from `intent.intentId()` (asserted via
`intent.intentId().equals(decision.intentId())` and used directly as the
order's `clientOrderId`) — so `clientOrderId` and `intentId` are always
the exact same `UUID` value for any real order this codebase produces.
`SharedKisAccountLedger` keys every `LedgerReservation` by
`intent.intentId()` directly (passed as the `clientOrderId` constructor
argument), matching `TradingLoop.tick()`'s own call sites (which pass
`intent.intentId()` to `confirmReservation`/`releaseReservation`, and
`order.get().clientOrderId()` — the same value — when recording
`submittedOrderIds`).

## Verification

Full project build, from `/mnt/c/Dev/trading-engine/java`:

```
./gradlew build
```

Result: **BUILD SUCCESSFUL**, all modules green (`runtime`, `exchange`,
`execution`, `oms`, `risk`, `schemas`). Per-module test counts (all
passing, 0 failures, 0 errors, 0 skipped):

| Module | Tests |
|---|---|
| `runtime` | 291 |
| `exchange` | 63 |
| `execution` | 51 |
| `oms` | 30 |
| `risk` | 28 |
| `schemas` | 31 |

`SharedKisAccountLedgerTest` alone: 15/15 passing, full suite in 3.311s
(the concurrency stress test itself: 0.985s).

Confirmed directly (not just by running the suite):
- `AccountStateProvider.java`, `AccountLedgerLock.java`,
  `AccountLedgerStore.java`, `AccountLedger.java`,
  `LedgerReservation.java`, `RiskGateway.java`, `RiskLimits.java`,
  `AccountState.java` are **untouched** (`git diff --stat -- java/`
  confirms only `PaperTradingApp.java`/`PaperTradingAppTest.java` modified
  plus the two new files).
- `forKisPaper()`'s existing unconditional `app.killSwitch.trip()` call
  and its explanatory `log.error(...)` are byte-for-byte unchanged —
  confirmed by direct diff/reading, not only by the regression test in
  "Judgment calls" #7.
- `simulated`'s 4/5/6/8-arg constructors and `bingx-vst`'s
  `OrderExecutor`-accepting 7-arg constructor are untouched in signature
  and body — every existing `PaperTradingAppTest`/`TradingLoopTest` test
  using them passes unmodified.
