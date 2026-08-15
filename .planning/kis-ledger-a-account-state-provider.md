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
  `confirmReservation`-or-`releaseReservation` pairing contract — **scoped
  precisely to the normal, non-throwing `OrderPipeline#submitIntent` path**
  (fourth CodeRabbit review round, see below: a thrown `submitIntent`
  deliberately calls neither, leaving the reservation unresolved, which is
  not a violation of the pairing rule but the one case it excludes by
  design) — and that the default implementation (`TradingLoop`'s own
  private `SyntheticAccountStateProvider`) is synchronous/process-local/
  mostly-no-op, with a real shared/durable implementation being future work
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

### Round 2

Against commit `e11d95f` (after round 1's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha): `CHANGES_REQUESTED`, 1 inline comment. Fixed, this PR:

- **The round 1 idempotency-contract fix's own "must replace, not add to"
  wording was itself unsafe.** A real, sharp, correctly-identified gap in
  round 1's own fix, not a pre-existing issue: `reserveForIntent`
  runs *before* `OrderPipeline#submitIntent` can determine whether a given
  `intentId` is a genuine conflict. Concretely — order for `intentId` X is
  registered and `confirmReservation`d at quantity `0.001`; a later
  conflicting retry for the *same* `intentId` X requests a *smaller*
  quantity, `0.0005`. Under round 1's unqualified "replace" wording,
  `reserveForIntent` would shrink the already-confirmed `0.001`
  reservation down to `0.0005` *before* `submitIntent` discovers the
  conflict and throws — at which point round 1's own exception-handling
  fix ("leave the reservation at its pessimistic size, don't release")
  would preserve that already-shrunk `0.0005` value, not the real
  `0.001` a still-live `Order` actually represents. A real, still-live
  order's exposure would be under-recorded on the ledger, silently
  weakening the exact risk limit this interface exists to help enforce —
  the dangerous direction, and exactly the class of bug round 1's own fix
  was trying to prevent, reintroduced by round 1's own new documentation.

  **Verified this was purely a documentation defect, not a shipped
  behavior defect**: nothing in this PR implements the unsafe "replace"
  semantic — `SyntheticAccountStateProvider` (the only implementation
  that exists) ignores `intentId` entirely and carries no reservation
  state to shrink, confirmed by re-reading `TradingLoop.java` directly.
  So no runtime behavior changed to fix this; only `AccountStateProvider
  .java`'s own Javadoc did.

  **Fix**: rewrote the "Idempotency contract, per `intentId`" paragraph.
  The corrected rule: a real implementation must track, per `intentId`,
  whether its current reservation is merely pessimistic-and-unconfirmed
  or already `confirmReservation`d; a repeated `reserveForIntent` call may
  freely replace an *unconfirmed* reservation (nothing real is backing it
  yet), but against an already-*confirmed* one it must never record less
  than what was last confirmed, until that confirmed reservation is
  itself resolved via a subsequent `confirmReservation` or
  `releaseReservation` call. This also makes the round 1 fix's own claim
  ("the reservation is left at its pessimistic, already-conservative
  size") actually true as stated, rather than true only by accident of
  no implementation existing yet to violate it.

  **Declined the reviewer's repeated specific suggestion to "add a retry
  with lower quantity/price... verify through the stateful provider
  double that exposure is not reduced"** — same reasoning as round 1's
  analogous decline: `SyntheticAccountStateProvider` has no per-`intentId`
  reservation state at all, so "exposure is not reduced" is vacuously,
  trivially true for the one implementation this PR actually ships — there
  is nothing a stateful test double would be verifying about *this PR's
  own code* that isn't already proven by the existing "ignores `intentId`
  entirely" design. Building a stateful fake that tracks per-`intentId`
  confirmed-vs-unconfirmed state would mean designing and half-
  implementing Task C's own reservation data structure now, against the
  same "do not build any ledger, lock, or store class" scope boundary
  round 1 already declined this exact category of ask under. The
  corrected contract is real and will need real tests once a real
  stateful implementation exists to test it against (Task C) — asserted
  here as a forward-looking requirement, not proven against a fake built
  only to satisfy this one review comment.
- Confirmed no `TradingLoop.java`/`TradingLoopTest.java` change was
  actually needed despite the review comment also flagging those files:
  re-read `TradingLoop.java` lines 274-304 directly — its own code
  comment's claim ("the reservation is left at its pessimistic,
  already-conservative size") depends on `AccountStateProvider`'s
  contract guaranteeing that, which the fix above now does; there is no
  reservation-shrinking logic inside `TradingLoop.java` itself to correct
  (it never manipulates reservation state directly, only calls the
  interface's three methods).

Re-ran `./gradlew :runtime:test` after this fix (Javadoc-only, no
production code changed): still green, `TradingLoopTest` unchanged at
**14 tests, 0 failures, 0 errors** — this round's fix added no new test
because it changed no runtime behavior to test.

### Round 3

Against commit `2d18bdb` (after round 2's fix was pushed — a real review
confirmed via the GitHub reviews API to target this exact commit sha):
`CHANGES_REQUESTED`, 1 inline comment. Fixed, this PR — this time by
simplifying the rule rather than patching another edge case into it:

- **Round 2's own "confirmed vs. unconfirmed" distinction had exactly the
  gap its own name suggests.** A real, correctly-identified third-round
  finding: `TradingLoop.tick()`'s ambiguous-`submitIntent`-failure path
  (round 1's fix) deliberately never calls `confirmReservation` — so
  under round 2's rule, a reservation left in that ambiguous state is
  classified as merely "unconfirmed," *even when a real `Order` genuinely
  was registered before the throw*. A **later**, independent conflicting
  retry for the same `intentId` then hits round 2's "may freely replace an
  unconfirmed reservation" clause and can shrink it — reproducing round
  1's original understatement bug one exception-path hop later, this time
  hiding behind round 2's own new vocabulary rather than being fixed by
  it. Verified real, not hypothetical, by tracing the exact 3-tick
  sequence: tick 1 confirms a real order at `0.001`; tick 2 (a conflicting
  retry) calls `reserveForIntent` again, which — because tick 1's
  reservation is genuinely `confirmReservation`d — cannot shrink below
  `0.001` per round 2's rule, but then `submitIntent` throws and
  `confirmReservation` never re-fires for *this* attempt, leaving the
  reservation newly "unconfirmed" again; tick 3 (a second, smaller
  conflicting retry) then legally shrinks it under round 2's own rule,
  even though the real order from tick 1 is still live at `0.001`.

  **Fix, this time structural rather than another patch**: rewrote the
  idempotency paragraph a third time, dropping the confirmed/unconfirmed
  distinction entirely in favor of one single monotonic rule: a repeated
  `reserveForIntent` call for an `intentId` that already has **any**
  recorded reservation — confirmed, unconfirmed, or left unresolved after
  an ambiguous failure, no exceptions — must never *reduce* the recorded
  exposure; it may only hold it steady or raise it. Only
  `confirmReservation` or `releaseReservation` — each tied to a
  definitively known outcome — may ever reduce what's recorded for a
  given `intentId`. This needs no notion of "which state was this
  reservation in," which is exactly the missing piece that let both prior
  versions' edge cases through. The full three-version history (including
  *why* each earlier version failed, not just the final rule) was kept in
  the Javadoc itself rather than silently overwritten, on the theory that
  a future reader extending this contract benefits more from seeing why
  the simpler rule was chosen than from a clean-looking paragraph that
  hides two real, already-found failure modes.
  - Kept the flagged planning-doc language honest here too: this document
    itself (round 2's write-up above) still describes the now-superseded
    "unconfirmed reservation ... nothing real is backing it yet" framing
    as what was built *at that time* — left as an accurate historical
    record of round 2's real fix, not rewritten, since this "Round 3"
    entry is what supersedes it; the current, load-bearing contract is
    whatever `AccountStateProvider.java` says today, not any prior
    round's write-up here.
  - **Declined the reviewer's repeated suggestion to add a Task C test
    plan / stateful test double for this** — consistent with rounds 1 and
    2's own reasoning: `SyntheticAccountStateProvider` has no per-
    `intentId` reservation state to exhibit this bug in the first place,
    so there is nothing in *this PR's shipped code* for a stateful fake to
    verify. The Javadoc's own closing sentence now explicitly names the
    round-2 failure scenario as a required test case for whatever Task C's
    real stateful implementation turns out to be, which is judged
    sufficient forward-looking documentation without building that
    implementation's test infrastructure prematurely here.

Re-ran `./gradlew :runtime:test` after this fix (again Javadoc-only, no
production code changed): still green, `TradingLoopTest` unchanged at
**14 tests, 0 failures, 0 errors**.

### Interim status after round 3's fix, commit `1259808` — corrected by "Round 4" below

The round-3 fix was pushed and CodeRabbit's own **commit status** (the
`CodeRabbit` GitHub check, a separate object from a **PR review**)
transitioned to `success`/"Review completed" against this exact commit
sha — confirmed directly via `GET /repos/.../commits/1259808.../status`.
At the time, no new PR review object had been posted for this commit
either (`GET /repos/.../pulls/99/reviews` showed the latest still
targeting commit `2d18bdb`, round 3's own commit), and this was
**incorrectly read as CodeRabbit having found zero actionable issues in
the round-3 diff** — an inference this document stated here, and reported
to this task's own coordinator, before it was actually verified against a
real review object for that specific diff.

**That inference was wrong, corrected on the same PR before merge, not
after — flagged by this task's own coordinator, who checked `gh api
repos/.../pulls/99/reviews` directly rather than trusting this document's
prior claim, and found no review object existed for either `1259808` or
the next commit at the time.** A commit-status `success` with no
superseding review object is genuinely ambiguous between two different
real situations — "reviewed, found nothing" (the correct reading for the
Task F precedent this document originally cited) and "not yet
(re-)reviewed at all" (what had actually happened here, confirmed once a
real review of the cumulative diff finally landed and found a real,
valid, new issue in code that had existed since round 1 — see "Round 4"
below). **The commit-status check alone does not distinguish these two
cases; only a review object whose `commit_id` matches the exact commit in
question does.** This document's own stated verification discipline
("verify via `gh api` that the latest review's `commit_id` matches `git
rev-parse HEAD` exactly... and its `state` is `APPROVED`") already says
this correctly — the error was in this section's own execution of that
discipline, treating a green commit status as satisfying it when it does
not. Left here, not deleted, as an accurate record of the mistake and its
correction, not smoothed over.

### Round 4

Against commit `079fe91` (a further, doc-only commit recording round 3's
outcome — see "Interim status" above for why that section's original
framing of that commit needed correcting) — a real review, confirmed via
the GitHub reviews API to target this exact commit sha
(`4942951453`, `submitted_at: 2026-08-15T05:24:21Z`), reviewing the full
cumulative diff from the PR's base: `CHANGES_REQUESTED`, 1 inline
comment, labeled by CodeRabbit itself `⚡ Quick win` (unlike rounds 1-3's
`🏗️ Heavy lift`). Fixed, this PR:

- **The class-level "exactly one of `confirmReservation`/
  `releaseReservation` must follow" bullet, and `reserveForIntent`'s own
  method Javadoc, stated an unqualified rule that the rest of this same
  file's "real, disclosed gap" paragraph already contradicted.** Real and
  correctly identified: since round 1, this Javadoc has documented that
  `TradingLoop.tick()` deliberately calls *neither* method when {@code
  OrderPipeline#submitIntent} throws — but the interface's own foundational
  contract statement (the bullet list right after "Why three methods") and
  `reserveForIntent`'s per-method Javadoc both still read as an absolute,
  unconditional "exactly one must follow," with no pointer to the
  exception it doesn't actually cover. A reader implementing this
  interface from the normative contract section alone (not the later,
  separately-titled disclosure paragraph) could reasonably conclude the
  exception case doesn't exist, then build a stateful implementation that
  incorrectly treats a `submitIntent` throw as "one of the two must still
  have happened" — silently clearing or shrinking a real, still-live
  order's exposure.

  **Fix**: qualified both spots explicitly. The class-level bullet now
  reads "must follow, for every call to `reserveForIntent` that returns
  normally **and** whose corresponding `submitIntent` call also returns
  normally," with an explicit pointer to the "real, disclosed gap"
  paragraph for the excluded case. `reserveForIntent`'s own Javadoc gained
  the same qualification plus an explicit statement that the excluded case
  "is not a violation of the pairing rule, it is the one case the rule
  excludes by design." Also fixed, per the reviewer's own specific file
  list: `FakeAccountStateProvider`'s class Javadoc (added the same
  qualification, plus a pointer to
  `tickLeavesTheReservationUnresolvedWhenSubmitIntentThrowsAfterAReservationWasMade`
  as the test that already proves this against real recorded call counts,
  not just against a documentation claim) and this planning document's own
  "What was built" section (added the same qualification where it first
  describes the pairing contract).
- **No runtime behavior changed** — same as rounds 2 and 3, this is a
  precision fix to documentation that was already inconsistent with
  `TradingLoop.java`'s actual, already-correct, already-tested behavior;
  `TradingLoop.java` itself was not touched by this round.

**A correction to this document's own process, not just the code**: the
"Interim status" section immediately above originally claimed round 3's
commit (`1259808`) had been reviewed clean based on a green CodeRabbit
*commit status* with no new review object yet posted. Round 4's own
review — landing later, against a subsequent commit, but covering the
full cumulative diff including everything in `1259808` — found a real
issue that had existed since round 1, proving that inference wrong: a
green commit status is not equivalent to "reviewed and found nothing,"
and this document (and a status report to this task's own coordinator)
stated it as if it were. Corrected in place above rather than silently
rewritten, per this document's own established practice of keeping wrong
turns visible, not just right answers.

Re-ran `./gradlew clean build` after this fix: still green, **405 tests,
0 failures, 0 errors** project-wide, `TradingLoopTest` unchanged at 14.

### Final CodeRabbit status (after round 4's fix, commit `a986ea6`) — genuinely verified, not inferred

Pushed round 4's fix as commit `a986ea6`. Learning directly from the
"Interim status" mistake above, verification this time used **only** the
reviews API (`GET /repos/.../pulls/99/reviews`), checking each result's
own `commit_id` against `git rev-parse HEAD` exactly, and never treated
the `CodeRabbit` commit-status check alone as sufficient — even though
that check again turned green almost immediately, before any real review
object existed for this commit (confirmed by polling the reviews API
directly and seeing no entry for `a986ea6` yet at that point). Requested
`@coderabbitai full review` explicitly and polled the reviews API every
90 seconds rather than assuming completion.

**A real, new review object landed** (`id 4943415282`,
`commit_id: a986ea6d358777e41d69540d2f6b7b92a5e32e2e` — matching `git
rev-parse HEAD` exactly — `submitted_at: 2026-08-15T08:55:08Z`):
**`APPROVED`**, empty body (no actionable comments). Confirmed no new
inline comment was posted alongside it (`GET /repos/.../pulls/99/comments`
— same 4 comments as before, all from rounds 1-4, all already addressed
above). GitHub's own native PR state reflects this cleanly now too,
checked directly rather than assumed: `reviewDecision: ""` (no longer
`CHANGES_REQUESTED` — a genuine `APPROVED` review superseded the prior
stale one, unlike the Task F precedent where no new review object ever
did), `mergeStateStatus: "CLEAN"`, `mergeable: "MERGEABLE"`.

This is the real, fully verified final state of this PR: CI green
(`bingx-hostname-guard`, `gitleaks`, `java-tests` ×2 job matrix, all
`pass`), CodeRabbit `APPROVED` against the exact current HEAD with zero
outstanding actionable comments, `./gradlew clean build` green at 405
tests / 0 failures / 0 errors. Not merged — per the governing task brief
and CLAUDE.md's Auto-merge Policy, this remains Java runtime/OMS/Risk-
Gateway-adjacent code requiring explicit human sign-off regardless of
CI/CodeRabbit status.

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
