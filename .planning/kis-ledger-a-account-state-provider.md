# Shared KIS account risk ledger, Task A: `AccountStateProvider` extraction

## Scope note

This is **Task A** of the 4-task "Shared KIS account risk ledger" plan
(`.claude/plans/tender-finding-matsumoto.md`'s "Shared KIS account risk
ledger (multi-process risk budget)" section — governing brief, not yet
mirrored into `.planning/`), which follows and depends on the now-complete
KIS/KOSPI200 Phase 1 work (PRs #93, #95-#98). Task A is the **foundation**
Tasks B (lock/store), C (`SharedKisAccountLedger` + wiring), and D
(reconciler) will depend on — none of those is touched here. R3-risk
component (`java/runtime`, the seam feeding `RiskGateway.evaluate` via
`OrderPipeline`) — TDD discipline applied throughout, per CLAUDE.md's
Development Methodology.

Same "provably inert plus one new extension point" framing as the earlier
`PriceFeed` extraction (KIS/KOSPI200 Phase 1 Task 1) and, before that,
the `OrderExecutor` extraction (BingX VST integration Task F): extract an
interface from an existing private method, retrofit the existing behavior
onto a default implementation of it, and widen exactly one constructor's
worth of call sites. Unlike those two, this task adds one genuinely new
piece of runtime behavior on top of the pure extraction —
`confirmReservation`/`releaseReservation` calls at `tick()`'s existing
order-submission call site — but that behavior is a no-op for every
caller that exists in production today (`simulated`, `bingx-vst`, both
still using the unchanged 6-arg constructor), so the "zero behavior
change for existing callers" claim from those two prior tasks still holds
here, proven below, not just asserted.

### GSD phase status

- **Discuss**: resolved by the governing brief and the plan file it
  points at (`.claude/plans/tender-finding-matsumoto.md`, "Shared KIS
  account risk ledger" section, "1. `AccountStateProvider` interface") —
  the interface shape, the constructor-overload approach, the inner-class
  default implementation, and the exact `tick()` call-site changes were
  all specified directly in the task brief. No open design ambiguity
  remained to discuss before starting.
- **Plan**: the governing plan file above is the plan; this doc's "What
  was built" and "Judgment calls" record how the real code matched or
  deviated from it.
- **Execute**: this document's "What was built" and "TDD" sections.
- **Verify**: this document's "Verification" section — real local test
  runs (`./gradlew clean build`), not a claim that tests would pass.
- **Ship**: **pending.** PR opened, CI green, CodeRabbit reviewed — but
  per the governing task brief's own explicit instruction and CLAUDE.md's
  Auto-merge Policy (Java runtime/OMS/Risk-Gateway-adjacent code is
  excluded from delegated auto-merge regardless of CI/CodeRabbit status),
  **this PR is not merged and must not be merged by an LLM session.**

## What was built

Two new files, one modified file (plus one test file gaining new test
methods, zero modifications to existing ones):

- **`engine.runtime.AccountStateProvider`** (new interface,
  `java/runtime/src/main/java/engine/runtime/AccountStateProvider.java`)
  — three methods: `AccountState reserveForIntent(OrderIntent,
  BigDecimal referencePrice)`, `void confirmReservation(UUID intentId,
  BigDecimal approvedQuantity, BigDecimal price)`, `void
  releaseReservation(UUID intentId)`. Javadoc states, per the governing
  brief: why three methods rather than one (`RiskGateway.evaluate` can
  only ever clamp a requested quantity down or reject it, never approve
  more than requested — so a reservation must be sized pessimistically to
  the full pre-clamp `intent.quantity()` up front, then corrected down or
  released once the real outcome is known), the exactly-one-of-
  `confirmReservation`-or-`releaseReservation` pairing contract, and that
  the default implementation (`TradingLoop`'s own private
  `SyntheticAccountStateProvider`) is synchronous/process-local/mostly-
  no-op, with a real shared/durable implementation being future work
  (Task C, explicitly not built here).
- **`TradingLoop`** (`java/runtime/src/main/java/engine/runtime/
  TradingLoop.java`):
  - New field `private final AccountStateProvider accountStateProvider`.
  - **Existing 6-arg constructor's signature is unchanged** — gained
    exactly one new line in its body:
    `this.accountStateProvider = new SyntheticAccountStateProvider();`.
  - **New 7-arg constructor overload** — the same six parameters plus
    `AccountStateProvider accountStateProvider` as the 7th, same
    `Objects.requireNonNull` convention as every other field.
  - New **private, non-static inner class**
    `SyntheticAccountStateProvider implements AccountStateProvider` —
    `reserveForIntent` returns `buildAccountState()` (the exact,
    completely unchanged existing private method, called directly since
    the inner class is non-static); `confirmReservation`/
    `releaseReservation` are empty-bodied no-ops.
  - `tick()`'s signal-submission branch: the old
    `orderPipeline.submitIntent(intent, price, buildAccountState())` call
    became `accountStateProvider.reserveForIntent(intent, price)` feeding
    `orderPipeline.submitIntent(intent, price, reservedAccount)`. Inside
    the existing `if (order.isPresent())` branch, `accountStateProvider
    .confirmReservation(intent.intentId(), order.get().approvedQuantity(),
    price)` was added right after `submittedOrderIds.add(...)` and before
    `submitToBroker(...)` — confirming the reservation before attempting
    the broker call, matching the existing ordering rationale already
    documented at that call site (`DUPLICATE_SUBMISSION_ATTEMPT` cares
    about the submission *attempt*, not whether the broker call itself
    succeeds — the reservation-confirmation logic is analogous: the risk
    decision was already made and is already final by this point,
    independent of whatever the broker does next). A new `else` branch
    (Risk Gateway rejected the intent, `order` is empty) calls
    `accountStateProvider.releaseReservation(intent.intentId())`.
  - `buildAccountState()`, `equity`, `applyFills()`, `currentEquity()`,
    `INITIAL_EQUITY` — **completely untouched**, byte-for-byte identical
    to before this task.
  - Accessor name check (per the governing brief's own explicit
    instruction to verify rather than assume): the brief's draft code
    used `order.get().quantity()`, which does not exist on `engine.oms
    .Order` — confirmed by reading `Order.java` directly. The real
    accessor for the risk-gateway-approved size is `approvedQuantity()`
    (there's also `requestedQuantity()`, the pre-clamp size — not what
    `confirmReservation` needs). Used `approvedQuantity()`, the correct
    accessor for "the real approved size," matching `AccountStateProvider
    #confirmReservation`'s own Javadoc contract exactly.
- **`FakeAccountStateProvider`** (new test double,
  `java/runtime/src/test/java/engine/runtime/FakeAccountStateProvider.java`)
  — hand-written, matching this codebase's established no-mocking-
  framework convention and the existing `FakePriceFeed`/
  `FakeTradingCalendar`/`FakeExchangeAdapter` naming and doc style: a
  fixed `AccountState` (set at construction) returned from every
  `reserveForIntent` call, with every method's arguments and call count
  recorded for test assertions.
- **`TradingLoopTest.java`** — zero changes to any existing test method
  (see "Expected test-file diff" below); four new test methods and two
  new imports (`assertSame`, `java.lang.reflect.Method`) added.

## TDD

1. Wrote `AccountStateProvider` first (the new contract).
2. Wrote `FakeAccountStateProvider`.
3. Wrote all four new test methods in `TradingLoopTest.java`, each using
   the new 7-arg constructor — which did not exist yet.
4. Ran `./gradlew :runtime:compileTestJava` and confirmed the expected
   red state: 3 compile errors, "constructor TradingLoop in class
   TradingLoop cannot be applied to given types... found:
   ...,FakeAccountStateProvider... reason: actual and formal argument
   lists differ in length" — one per new test using the not-yet-existing
   7-arg constructor (the fourth new test, the zero-behavior-change
   reflection proof, uses the existing 6-arg constructor and reflection
   only, so it didn't independently contribute a 4th compile error at
   this stage — it depends on the `accountStateProvider` field, which
   also didn't exist yet, but that surfaces as a *runtime* reflection
   failure, not a compile error, so it wasn't part of this specific red
   signal).
5. Implemented `TradingLoop`'s changes (field, both constructors, the
   inner class, the `tick()` call-site changes). Ran
   `./gradlew :runtime:test` — green, 13/13 (9 pre-existing + 4 new).
6. Ran the full multi-module suite (`./gradlew clean build`) — green.

## Expected test-file diff: confirmed as designed

The governing brief's acceptance bar: every existing `TradingLoopTest`
case must pass with **zero modification**. Confirmed directly, not just
by absence of a diff hunk: all 9 pre-existing test methods
(`tickWithSignalPresentSubmitsAnOrderToPaperBroker`,
`tickWithNoSignalStillAppliesPriceUpdateToExistingPendingOrders`,
`tickRecoversAfterAPriceFeedExceptionOnANextTick`,
`killSwitchTrippedMidRunBlocksNewSignalsButStillReconcilesAnAlreadyPendingOrder`,
`tickSkipsSignalSubmissionWhenEquityIsDepletedButStillReconcilesAnExistingPendingOrder`,
`tickRejectsASignalWithAMismatchedSymbolWithoutSubmittingItOrFailingTheTick`,
`tickSubmitsASignalWithAMatchingSymbolNormally`,
`submittedOrderIdsTracksEveryOrderCreatedThroughThePipelineAcrossTicks`,
`submittedOrderIdsRecordsARepeatWhenTheSameIntentIsSubmittedOnTwoSeparateTicks`)
still construct `TradingLoop` via the unchanged 6-arg constructor and
pass, appearing unchanged in the JUnit XML report alongside the 4 new
ones (13 total). Only the file's import block gained two new imports
(`assertSame`, `java.lang.reflect.Method`); no existing test method body
changed by even one character.

## New tests added

Four, all in `TradingLoopTest.java`, all using `FakeAccountStateProvider`
and/or reflection, per the governing brief's own required coverage list:

1. **`tickCallsReserveForIntentWithTheExactIntentAndReferencePriceItReceived`**
   — a plain lambda `SignalSource` (same technique the existing symbol-
   match tests already use) returning a fixed `OrderIntent` instance, so
   `assertSame` can prove `reserveForIntent` received the *exact same*
   object `tick()` itself received, not merely an equal one; asserts the
   reference price matches the fake price-feed server's response too.
2. **`tickConfirmsReservationWithApprovedQuantityWhenRiskGatewayModifiesTheIntent`**
   — forces a real MODIFIED decision using `RiskLimits.canary()`'s own 2%
   max-order-notional clamp (equity 100000 → max notional 2000; a 1 BTC
   request at a 50000 limit price requests 50000 notional, clamped to
   0.04 = 2000/50000). Asserts `confirmReservation` fires exactly once,
   carrying the real *approved* (clamped) quantity — not the original
   requested quantity — and that `releaseReservation` never fires.
3. **`tickReleasesReservationAndNeverConfirmsWhenRiskGatewayRejects`** —
   forces a REJECTED decision via a deeply negative daily PnL percent
   (-10%, breaching canary's -0.5% daily loss limit via `RiskGateway
   .checkLossLimits` before notional is ever considered). Asserts
   `releaseReservation` fires exactly once and `confirmReservation` never
   fires, and that the rejection is a defined skip (`lastError()` stays
   null), not a tick failure.
4. **`syntheticAccountStateProviderReserveForIntentMatchesTheOldInlineBuildAccountStateCall`**
   — the "zero behavior change" claim, proven rather than asserted: via
   reflection (same technique the pre-existing equity-depletion test
   already uses on `TradingLoop`'s private `equity` field), invokes the
   private `buildAccountState()` method directly, then separately reaches
   the private `accountStateProvider` field and calls its
   `reserveForIntent(...)` — with no fill/equity change happening between
   the two calls, asserts the two `AccountState` results are
   `equals()`-identical (a plain Java record, so `equals()` is a real
   field-by-field structural comparison, not identity).

## Judgment calls

- **`confirmReservation` placement: before `submitToBroker(...)`, not
  after.** The governing brief left exact placement to this task's
  judgment ("your call on exact placement"). Chose *before* — the risk
  decision (and therefore the correct reservation size) is already final
  the moment `order.isPresent()` is true; whatever happens next at the
  broker (success, throw-and-orphan per `submitToBroker`'s own Javadoc)
  doesn't change what was actually approved. Confirming first means the
  reservation is corrected to its real size even in the orphan case
  (`submitToBroker` throws, propagates through `tick()`'s catch-all) —
  the *order* is left orphaned in that case (a pre-existing, disclosed
  gap, unrelated to this task), but the *capital reservation* accounting
  stays correct regardless, which seemed like the safer default for a
  seam whose entire purpose is bounding aggregate exposure.
- **Used `approvedQuantity()`, not the brief's draft `quantity()`.**
  See "What was built" above — verified against the real `Order.java`
  rather than assumed, per the governing brief's own explicit
  instruction to check.
- **Test file, not a new file, for the four new tests.** The governing
  brief allowed either; put them in the existing `TradingLoopTest.java`
  rather than a new file, matching how every other `TradingLoop`
  behavior (kill switch, equity depletion, symbol validation,
  submitted-order tracking) already lives in that one file as
  `TradingLoop`-level integration tests, not split by feature area.
- **`SyntheticAccountStateProvider` given its own class-level Javadoc**
  explaining why `confirmReservation`/`releaseReservation` are no-ops
  (no reservation state exists to correct/release, since
  `reserveForIntent` reads live equity fresh every call) rather than
  leaving the empty method bodies to speak for themselves — matches this
  codebase's general preference for stating a "why is this empty"
  reasoning explicitly rather than leaving a reader to infer it (compare
  `PaperBroker`'s and `AlwaysOpenTradingCalendar`'s own Javadoc for the
  same pattern applied to trivial implementations elsewhere in this
  codebase).
- **No ledger, lock, or store class built** — per the governing brief's
  explicit "do not build" instruction, `SyntheticAccountStateProvider` is
  the only implementation of `AccountStateProvider` that exists after
  this task.

## CodeRabbit review findings

One review round on PR #99 (`ASSERTIVE` profile) against commit `7a4a5c9`
(the original commit): `CHANGES_REQUESTED`, 1 inline comment + 1 "outside
diff range" comment. Both verified against the real current code and
fixed, this PR:

- **Ambiguous `submitIntent` failure left the reservation unresolved (Major,
  "outside diff range" comment on `TradingLoop.java` lines 270-295).**
  Verified genuinely reachable, not theoretical: `OrderStore#createOrder`'s
  own conflicting-retry guard throws `IllegalStateException` for a reused
  `intentId` whose details/decision don't match what's already stored
  (`orders.computeIfAbsent` returns the existing `Order`, then `order
  .matches(intent, decision)` fails) -- confirmed by reading `OrderStore
  .java` directly. Before this fix, that exception propagated straight out
  of the `if/else` block that calls `confirmReservation`/
  `releaseReservation`, so neither ever fired for that attempt --
  `tick()`'s own catch-all still recorded `lastError`, but the reservation
  itself was silently left dangling.

  CodeRabbit's own suggested fix ("release only if no order was
  registered, otherwise look up order state via `intentId` or perform
  reconciliation") would require either giving `TradingLoop`/
  `AccountStateProvider` direct `OrderStore` query access (a real interface
  shape decision belonging to Task C, not this task) or building real
  reconciliation logic (explicitly Task D's job -- the governing plan
  already defers the analogous "reservation doesn't perfectly track every
  order-lifecycle edge case" problem to Task D's periodic reconciliation).
  Building either now would be premature, undesigned R3-risk architecture
  work under review pressure -- exactly what this project's Development
  Methodology says needs its own `Discuss` pass instead.

  **What was actually fixed, safely, without that infrastructure**: wrapped
  the `orderPipeline.submitIntent(...)` call in a `try/catch
  (RuntimeException)` that deliberately does **not** call
  `releaseReservation` on catch -- releasing would risk *understating*
  committed exposure if the order actually was registered before the
  throw, the dangerous direction (the opposite of this interface's own
  "reserve pessimistically" principle). The reservation is left at its
  pessimistic, already-conservative size instead; a WARN log records the
  ambiguity, and the exception is rethrown unchanged so `tick()`'s existing
  catch-all/`lastError`/retry-next-tick behavior is completely unaffected.
  This closes the gap in the safe direction with zero new surface area,
  rather than declining it outright. Documented in `AccountStateProvider`'s
  own Javadoc ("A real, disclosed gap, left open rather than fixed here")
  so this is a stated contract limitation, not a silent one. New regression
  test:
  `tickLeavesTheReservationUnresolvedWhenSubmitIntentThrowsAfterAReservationWasMade`
  -- forces the real `IllegalStateException` via a genuine conflicting
  retry (same `intentId`, different requested quantity across two ticks),
  asserts `releaseReservation` never fires for that attempt,
  `confirmReservation`'s count stays at its prior value, and the exception
  still surfaces as `lastError`.

- **Idempotency contract undocumented (inline comment on
  `AccountStateProvider.java` lines 73-96).** Real, valid: the original
  Javadoc never stated what a real implementation must do if
  `reserveForIntent`/`confirmReservation`/`releaseReservation` is somehow
  called more than once for the same `intentId`. Fixed with a new Javadoc
  paragraph ("Idempotency contract, per `intentId`") stating the
  requirement explicitly: a repeated `reserveForIntent` for an `intentId`
  with an already-live reservation must replace, not add to, it; the
  default `SyntheticAccountStateProvider` satisfies this trivially since it
  carries no reservation state at all. **Declined the reviewer's specific
  suggestion to "add or update a stateful test double... verify net
  reservation reflected once in resubmission scenarios"** -- building a
  stateful fake that tracks cumulative per-`intentId` reservation amounts
  would mean designing and half-implementing Task B/C's own reservation
  data structure prematurely, against a governing plan that explicitly
  scopes Task A to "do not build any ledger, lock, or store class." The
  documented contract is real and testable once a real stateful
  implementation exists to test it against (Task C); asserted here as a
  contract requirement future implementations must satisfy, not proven
  against a fake built only to satisfy this one review comment.

Re-ran `./gradlew clean build` after both fixes: still green, now **405
tests, 0 failures, 0 errors** (404 + 1 new regression test).

## Explicitly out of scope (per the governing brief, not attempted here)

- `AccountLedger`/`LedgerReservation`/`AccountLedgerStore`/
  `AccountLedgerLock` (Task B).
- `SharedKisAccountLedger` and any wiring of it into `TradingLoop`/
  `PaperTradingApp` (Task C).
- `AccountLedgerReconciler`, startup bootstrap-from-real-balance, the
  10%-mismatch alarm (Task D).
- `java/risk`, `java/oms`, `java/execution`, `java/exchange`,
  `PaperTradingApp.java` — none touched; all Tasks B/C/D's job, not
  Task A's.

## Verification

- `./gradlew :runtime:compileTestJava` (before implementing `TradingLoop`
  changes) — failed with exactly 3 compile errors, the expected red
  state (see "TDD" above).
- `./gradlew :runtime:test` (after implementing the original `TradingLoop`
  changes, before the CodeRabbit-review fixes below) —
  **BUILD SUCCESSFUL**, all `:runtime` tests green, including
  `TradingLoopTest` at **13 tests, 0 failures, 0 errors** (9 pre-existing
  + 4 new).
- `./gradlew clean build` (same point, full multi-module suite, all six
  modules, clean, not incremental) — **BUILD SUCCESSFUL**. Summed real
  JUnit XML reports across every module (`schemas`, `oms`, `risk`,
  `execution`, `exchange`, `runtime`): **404 tests, 0 failures, 0 errors**.
- **After the CodeRabbit review round's two fixes** (see "CodeRabbit
  review findings" below) — `./gradlew :runtime:test`: green,
  `TradingLoopTest` now **14 tests, 0 failures, 0 errors** (9 pre-existing
  + 4 original new + 1 new regression test for the ambiguous-`submitIntent`
  -failure fix). `./gradlew clean build` (full multi-module suite, clean):
  **BUILD SUCCESSFUL**, **405 tests, 0 failures, 0 errors** project-wide.
  This is the final state of the PR.
- PR opened, not merged — per the governing task brief and CLAUDE.md's
  Auto-merge Policy, this is Java runtime/OMS/Risk-Gateway-adjacent code
  and requires explicit human sign-off regardless of CI/CodeRabbit
  status.
