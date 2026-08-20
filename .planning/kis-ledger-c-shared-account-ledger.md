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
    → calculate the hypothetical combined notional across all reservations
    after that merge → if it would exceed `allocatedVirtualCapital`,
    return a floored-near-zero-equity snapshot **without persisting** the
    candidate (see "Judgment calls" #9 — added on CodeRabbit review, not
    part of the original submission) → otherwise persist → return `equity
    = allocatedVirtualCapital − Σ(reservations.notional)`, floored at
    `BigDecimal.ONE`.
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

- **`SharedKisAccountLedgerTest.java`** (new, 18 tests) — unit coverage of
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

### 9. Real CodeRabbit review finding, fixed: the lock alone doesn't cap the combined total against `allocatedVirtualCapital`

CodeRabbit's first-round review (state `CHANGES_REQUESTED`, commit
`58cd08e`) found a genuine functional gap in the original submission's
`reserveForIntent`: it unconditionally added the pessimistic candidate
reservation and persisted, relying solely on the equity floor
(`MIN_EQUITY`) plus `RiskGateway`'s own downstream clamped-quantity-
rounds-to-zero rejection to eventually correct an over-commitment. The
concrete failure this misses: `AccountLedgerLock` only prevents a *lost
update* (two racing writers clobbering each other) — it does **not**
prevent a *sequence* of independently-valid, sequentially-persisted
pessimistic reservations from summing past `allocatedVirtualCapital`,
since each `reserveForIntent` call is its own independent critical
section and each reservation is deliberately sized to the full,
un-clamped `intent.quantity()`. CodeRabbit's own example: capital
100,000, three processes each pessimistically reserve 50,000 — even with
perfect lock serialization (no lost update), the on-disk total could
genuinely reach 150,000 for the window between the third persist and its
eventual `releaseReservation` call. Given CLAUDE.md's own "never weaken
risk limits... without explicit human approval" rule, and that this is
literally the safety property the whole 3-task effort exists to prove
("combined committed notional never exceeds `allocatedVirtualCapital`"),
this was accepted as a real, valid finding, not dismissed.

**Fix**: before persisting, `reserveForIntent` now computes the
hypothetical combined total the candidate reservation would produce
(after `reserveMonotonic`'s own merge) and, if that total would exceed
`allocatedVirtualCapital`, declines to persist it at all — same treatment
as the existing alarm-tripped/non-positive-price cases (a floored near-
zero-equity snapshot, no reservation recorded, `RiskGateway`'s own
unmodified rejection path doing the actual rejecting). This does **not**
conflict with `AccountStateProvider`'s frozen Task A Javadoc ("must size
the reservation to `intent.quantity()`... the full requested quantity,
not any anticipated clamp") — that describes how a reservation is *sized
when one is created*, not that one must always be created; declining
outright when there's no room is the same pattern the alarm/non-positive-
price cases already established, extended to a third condition.

**A real, deliberate consequence for the original stress test**: the
original `concurrentTradingLoopsSharingOneLedgerFileNeverExceedAllocatedVirtualCapital`
test used an absurdly large fixed quantity (pessimistic notional
100,000,000 against a 1,000,000 budget) specifically to force
`RiskGateway`'s 2% clamp on every attempt. With the fix, that same
request is declined **before ever reaching `RiskGateway`** — a single
request whose own pre-clamp notional already exceeds the entire budget
can never fit, by design. This is not a test bug to work around; it's
the fix working correctly. The test was updated to a more realistic
pessimistic notional (10% of capital — large enough to still trigger
`RiskGateway`'s 2% clamp meaningfully, small enough to comfortably fit
the reservation-stage check on its own) so it continues to exercise the
intended "many concurrent orders, each aggressively clamped and shrunk"
stress pattern rather than a degenerate "every attempt declined
immediately" one.

**New, more direct tests added**, since the full-`TradingLoop`-pipeline
stress test alone doesn't isolate the reservation-stage cap from
`RiskGateway`'s own separate, much narrower (2% per order) clamp:
`reserveForIntentDeclinesAReservationThatWouldPushCombinedTotalOverAllocatedCapital`
(sequential, single-call proof), `reserveForIntentAcceptsAReservationThatExactlyFillsTheRemainingBudget`
(off-by-one boundary — exactly filling the budget must still be
accepted, not just "not exceeding" declined too aggressively), and
`reserveForIntentNeverPersistsACombinedTotalOverBudgetUnderRealConcurrentAccess`
(direct `SharedKisAccountLedger`-level concurrency test, bypassing
`TradingLoop`/`RiskGateway` entirely — 5 threads each attempt a fixed
40,000-notional reservation against a 100,000 budget; the arithmetic is
deterministic regardless of arrival order, so exactly 2 of 5 must
succeed, asserted precisely rather than just "does not exceed").

CodeRabbit's other two findings from the same review, also fixed:
(1) a markdownlint MD040 warning (this doc's own `./gradlew build` fence
now specifies `sh`); (2) a real account-identifier logging concern —
`bootstrapOrLoad`'s (and every other method's) log lines used to include
`ledgerPath` (which embeds the real KIS account number in its filename)
and/or `accountId` directly. Per CLAUDE.md's Non-negotiable Rules ("never
commit raw trading logs containing secrets or account identifiers"), all
such log statements in `SharedKisAccountLedger` now omit both — `venue`
alone (not account-identifying) is logged freely. Documented as a
standing rule in the class's own Javadoc so a future addition to this
class doesn't reintroduce it.

### 10. CodeRabbit pre-merge check finding: loss-limit enforcement is inert for `kis-paper` until Task D — real, disclosed, deliberately not fixed here

CodeRabbit's repo-configured "No Risk Or Leverage Relaxation" pre-merge
check (`.coderabbit.yaml`, distinct from its inline review comments)
failed on this PR's second commit, with a real and precisely correct
observation: `SharedKisAccountLedger`'s `AccountState` always carries
`lastReconciledDailyPnlPercent`/`...Weekly...`/`...Monthly...` exactly as
stored on the ledger, which is `BigDecimal.ZERO` until a reconciliation
pass (Task D, not built) ever runs — meaning `RiskGateway.checkLossLimits`
compares a permanently-`0` PnL against `RiskLimits.canary()`'s negative
thresholds forever, so the daily/weekly/monthly loss-limit and hard-stop
checks can never actually trip for `kis-paper`.

**Verified this is a real behavioral change relative to `kis-paper`
before this PR, not merely a pre-existing characteristic restated**:
before Task C, `forKisPaper()` built `TradingLoop` via the 6-arg
constructor, which uses the private `SyntheticAccountStateProvider` ->
`TradingLoop.buildAccountState()` -> a **live**, fee-driven `pnlPercent
= (equity - INITIAL_EQUITY) / INITIAL_EQUITY` that moves every tick a
fill accrues a fee (confirmed by reading `TradingLoop.java` directly,
lines 460-463). That number was never *real* P&L (a synthetic $100,000
baseline, fees only, no real KIS balance) — but it did move, and could in
principle cross a loss-limit threshold given enough fee accumulation.
`SharedKisAccountLedger`'s PnL percents, by contrast, are structurally
incapable of ever moving until Task D exists. **This is a genuine, if
narrow, reduction in loss-limit responsiveness for `kis-paper`
specifically, introduced by this PR** — accurately what CodeRabbit's
check is pointing at, not a false positive.

**Not fixed here, deliberately, not silently accepted either.** The
governing task brief itself explicitly pre-approved exactly this
tradeoff, in the same words used to scope this task: *"PnL percentages...
do NOT recompute these live... This is a deliberate, disclosed
simplification — real reconciliation that keeps these fresh is Task D's
job, not yours... you are not the one who ever sets that field... Do not
build a reconciler, a cadence, or an alarm-tripping mechanism — those are
explicitly Task D's job, out of scope here."* Building any part of Task
D's reconciler under review pressure, to silence this one pre-merge
check, would itself violate that explicit scope boundary and this
project's own TDD-for-R3-risk discipline (a reconciler deserves its own
`Discuss`/`Plan`, not a rushed addition here) — the same reasoning
CLAUDE.md's own KIS design applies to `forKisPaper()`'s unconditional
`KillSwitch.trip()` (a real, disclosed gap mitigated by a blunt
instrument, not silently fixed, until its own dedicated fix lands).

**Real, bounding mitigation already in place, unaffected by this gap**:
`forKisPaper()`'s existing unconditional `killSwitch.trip()` (KOSPI200
contract-multiplier gap, untouched by this PR — see "Judgment calls" #7)
already means no order can be submitted through `kis-paper` at all
without a deliberate human reset, regardless of this separate loss-limit
gap. This does not make the loss-limit gap irrelevant (a human resetting
the kill switch for the contract-multiplier reason would also, without
realizing it, be resetting into a state where loss limits don't work
either) — surfaced explicitly here so that reset decision is made with
full information, not decided or hidden by this task on its own
authority.

**Left for the human to decide**: whether to accept this PR with the gap
disclosed (matching the task brief's own explicit sign-off), or to
require some interim mitigation (e.g. fail-closed on a `null`
`lastReconciledAt` — reject rather than silently treat as "0% PnL,
always passes") before merging. Not decided here — this PR does not
attempt to check the CodeRabbit pre-merge "Ignore" box, and this session
does not merge regardless (see top-level instructions).

## Verification

Full project build, from `/mnt/c/Dev/trading-engine/java`:

```sh
./gradlew build
```

Result: **BUILD SUCCESSFUL**, all modules green (`runtime`, `exchange`,
`execution`, `oms`, `risk`, `schemas`). Per-module test counts (all
passing, 0 failures, 0 errors, 0 skipped) — re-run after the CodeRabbit-
findings fix below (see "Judgment calls" #9):

| Module | Tests |
|---|---|
| `runtime` | 294 |
| `exchange` | 63 |
| `execution` | 51 |
| `oms` | 30 |
| `risk` | 28 |
| `schemas` | 31 |

`SharedKisAccountLedgerTest` alone: 18/18 passing, full suite in 3.2s.

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
