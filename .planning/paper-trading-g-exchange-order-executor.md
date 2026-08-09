# BingX VST integration, Task G: `ExchangeOrderExecutor`

## Scope note

This is **Task G** of the 3-task BingX VST integration plan (see
`.claude/plans/tender-finding-matsumoto.md` — not yet mirrored into
`.planning/`, referenced here as the governing brief), which depends on
Task F (`OrderExecutor` interface extraction + retrofit, merged to `main`
via PR #77, see `.planning/paper-trading-f-order-executor.md`). Task H
(VST wiring, safety, real verification) depends on this task and is not
touched here. R3-risk component (`java/execution`) — TDD discipline applied
throughout per CLAUDE.md's Development Methodology.

Unlike Task F, this task is **new behavior**, not a provably-inert
refactor: a second, real `OrderExecutor` implementation, built and tested
entirely against a hand-written `ExchangeAdapter` test double — zero live
surface (no credentials, no wiring into `PaperTradingApp`, no real network
beyond the existing in-process patterns this codebase already uses).

## What was built

- **`engine.execution.ExchangeOrderExecutor implements OrderExecutor`**
  (`java/execution/src/main/java/engine/execution/ExchangeOrderExecutor.java`),
  constructor `(ExchangeAdapter adapter, BigDecimal feeBps)`. Wraps the
  `ExchangeAdapter` **interface** only — verified directly (see "Venue-
  agnostic design, verified" below).
- **`:execution`'s `build.gradle.kts`** gained
  `implementation(project(":exchange"))`. Verified no dependency cycle
  before adding it: `:exchange`'s own `build.gradle.kts` depends only on
  `project(":oms")` and `project(":schemas")` — it has no dependency on
  `:execution`, so `:execution` → `:exchange` is a one-way edge.
- **`engine.execution.FakeExchangeAdapter`** (test-only,
  `java/execution/src/test/java/engine/execution/FakeExchangeAdapter.java`)
  — a hand-written `ExchangeAdapter` test double, following this
  codebase's established no-mocking-framework convention (confirmed:
  grepped every module's `build.gradle.kts` for `mockito`/`Mockito`/
  `EasyMock`/`jmock` — zero hits anywhere in the repo). Scripts
  `queryOrder` responses per client order id as a FIFO queue (script the
  whole multi-poll sequence up front; each call consumes the next entry;
  an exhausted queue throws loudly rather than repeating or fabricating a
  response), plus simple one-shot hooks to make `submitOrder`/`cancelOrder`
  reject or throw. `getPositions`/`getBalance`/`setLeverage`/
  `setPositionMode` all throw `UnsupportedOperationException` — asserting,
  by construction, that `ExchangeOrderExecutor` never calls any of them
  (it doesn't need to for anything in this task's scope).
- **`engine.execution.ExchangeOrderExecutorTest`** — 26 tests (7 required
  scenarios + 19 additional coverage tests, the latter including 6 added
  during CodeRabbit review — see "CodeRabbit review findings" below), all
  against `FakeExchangeAdapter`, no real network access anywhere.

## Venue-agnostic design, verified

The governing brief's single load-bearing constraint: `ExchangeOrderExecutor`
must depend only on the `ExchangeAdapter` **interface**, never a concrete
adapter, and never anything BingX-specific beyond the interface's own data
types. Verified directly, not just by construction discipline:

```
$ grep -n "^import" ExchangeOrderExecutor.java
import engine.exchange.ExchangeAdapter;
import engine.exchange.OrderStatus;
... (rest are java.*/org.slf4j only)
```

Exactly two `engine.exchange` imports: the interface itself and its own
plain-DTO status type. No `BingXAdapter`, no `BalanceSnapshot`/
`PositionSnapshot`/`PositionMode` (this task never calls
`getPositions`/`getBalance`/`setLeverage`/`setPositionMode` at all — those
are Task H/`VstPreflight` concerns), no `ExchangeException` import (it's
never constructed or caught by name — every adapter exception is caught
generically as `RuntimeException`, so a future `ExchangeAdapter`
implementation whose failures don't happen to be `ExchangeException`
still works correctly). The one textual mention of "BingX" anywhere in
the file is a code comment describing real, already-established
BingX behavior (`Order.java`'s state machine, the REST/WebSocket
`CANCELLED`/`CANCELED` casing difference already documented in CLAUDE.md)
— prose context for *why* the class behaves as it does, not a code
dependency. A third exchange means writing a new `ExchangeAdapter`
implementation; zero code in `ExchangeOrderExecutor` would need to change.

## Status-mapping table, as actually implemented

| Exchange status | Delta applied? | `Order` transition | Removed from pending? |
|---|---|---|---|
| `NEW` / `PENDING` | if any | none | no |
| `PARTIALLY_FILLED` | if any | none (already `PARTIALLY_FILLED` via `fill()`) | no |
| `FILLED` | if any | `FILLED` via `fill()` | yes |
| `CANCELLED` / `CANCELED` | if any, first | `requestCancel()`/`confirmCancel()` (or just `confirmCancel()` if already `CANCEL_PENDING` — see "cancel path state guard" below) | yes |
| `EXPIRED`, order `state()==ACKNOWLEDGED` | n/a (no fill possible pre-ACK) | `expire()` | yes |
| `EXPIRED`, order `state()!=ACKNOWLEDGED` (i.e. `PARTIALLY_FILLED`) | if any, first | approximated via the cancel path, logged at ERROR | yes |
| `REJECTED` / `FAILED` | if any, first | **always** approximated via the cancel path, logged at ERROR (see below) | yes |
| anything else | no | none | no (stays pending, logged WARN) |

**`OPEN_STATUSES = {"NEW", "PENDING"}`.** Only `"NEW"` is an empirically
confirmed BingX token in this codebase (`BingXAdapterTest`'s own
`queryOrder` fixture uses `"status":"NEW"`; no test or CLAUDE.md passage
anywhere uses `"PENDING"`). `"PENDING"` is included defensively per the
governing brief's own explicit "NEW/PENDING" phrasing — costs nothing if
BingX never actually emits it (the branch is simply unreached), and
avoids misclassifying it as "unrecognized" if some order type/response
shape this project hasn't observed yet does use it. Documented here as a
judgment call, not silently assumed.

**`REJECTED`/`FAILED` always takes the cancel path, not "only when
illegal."** The governing brief describes this case as "arriving when the
order is not in a state where `expire()`/`reject()` are legal" — implying
a conditional check mirroring the `EXPIRED` case. For `REJECTED`/`FAILED`
specifically, that condition is **always** true for any order this method
is ever called with: `Order.reject()` requires exactly `SUBMITTED`, and
`submit()`'s own pending-tracking guard (`state()==ACKNOWLEDGED &&
exchangeOrderId()!=null`) means a pending order is *always* at least
`ACKNOWLEDGED` by the time it could ever be polled — it can never be
`SUBMITTED` while tracked. So there is no reachable "legal" branch to
special-case; the implementation reflects this directly (always
cancel-path, with a comment explaining *why* rather than a dead
conditional that would never take the other branch). This was verified,
not assumed: re-read `Order.java`'s `reject()`/`requireState` and
`submit()`'s own guard before writing this branch.

**Cancel-path state guard, added beyond the brief's literal text.** All
three cancel-path call sites (`CANCELLED`/`CANCELED`, the `EXPIRED`
fallback, the `REJECTED`/`FAILED` fallback) go through a shared
`approximateCancel(order)` helper that skips `requestCancel()` — calling
only `confirmCancel()` — when `order.state()` is already `CANCEL_PENDING`.
This matters for a real, reachable sequence: if a prior `cancel(order)`
call's `adapter.cancelOrder` threw (BingX rejected the cancel — see
`cancel`'s own contract below), the order is left in `CANCEL_PENDING`
**and still tracked as pending** (deliberately, per the brief). A later
`pollFills` call for that same order could then observe a status this
table maps to the cancel path — calling `requestCancel()` unconditionally
there would throw (`CANCEL_PENDING` is not in `Order`'s own
`CAN_REQUEST_CANCEL` set), turning a legitimate retry-after-failed-cancel
into a spurious per-order "state corruption" drop. The guard avoids that;
not required by any of the 7 named test scenarios, but directly
motivated by re-reading `Order.java`'s real preconditions the way the
task brief asked.

## `submit`/`cancel`, as actually implemented

- **`submit`**: delegates to `adapter.submitOrder(order)` uncaught (no
  try/catch, no retry — per `OrderExecutor`'s own "may throw" contract
  for submit, and the brief's explicit instruction not to add retry logic
  here). Tracks pending only if `order.state()==ACKNOWLEDGED &&
  order.exchangeOrderId()!=null` — confirmed via a real test
  (`submitThatResultsInRejectedNeverEntersPendingTracking`) and via the
  submit-throws test (`submitThatThrowsPropagatesAndLeavesOrderUntracked`)
  that a throwing/rejecting submit never enters `pendingOrders()`. Always
  returns `Optional.empty()`.
- **`cancel`**: `adapter.cancelOrder(order)` (may throw), then removes
  from pending tracking **only on success** — the removal line is
  unreached if `cancelOrder` throws, so a BingX-rejected cancel leaves the
  order both in `CANCEL_PENDING` (state) and still in `pendingOrders()`
  (tracking), verified by
  `cancelRejectedByTheExchangeThrowsAndLeavesOrderInCancelPendingStillTracked`.
  This is deliberate, not an oversight: `Order.CAN_FILL` includes
  `CANCEL_PENDING`, so a later `pollFills` can still legitimately resolve
  this order (a real fill landing while a cancel is in flight, or the
  cancel-path state guard above eventually confirming it) rather than
  the order becoming permanently unreachable.

## Fee-modeling disclosure

`OrderStatus` carries no commission field (confirmed against
`OrderStatus.java` and `BingXAdapter.queryOrder`'s real parsing — only
`exchangeOrderId`/`status`/`filledQuantity`/`avgPrice`). Fee is computed
from each fill increment's own notional using the constructor's `feeBps`,
via the exact same formula and rounding `PaperBroker.tryFill` already
uses (`notional.multiply(feeBps).divide(BPS_DIVISOR)`, `BPS_DIVISOR =
10000`, no explicit scale/rounding needed since 10000 is a "nice"
divisor that preserves termination for any terminating input) — chosen
for direct comparability between the two loops' daily-report equity
series, per the governing brief. This is a modeled approximation, stated
loudly in the class Javadoc, not a claim that it matches what BingX
actually charges. Evaluating a real commission field against a captured
VST response is Task H's job (the governing plan's own "capture the raw
`queryOrder` JSON for a real filled order" verification step).

## Incremental-price derivation and its scale/rounding convention

`incrementPrice = (newCumulativeNotional - previousCumulativeNotional) /
delta`, computed with `PRICE_SCALE = 8` decimal places and
`RoundingMode.HALF_UP`. No existing "price scale" convention exists
elsewhere in this codebase to reuse directly — the closest precedent is
`RiskGateway.QUANTITY_SCALE = 8` (used for a *quantity* division, not
price) and `TradingLoop`/`DailyReportGenerator`'s shared use of
`RoundingMode.HALF_UP` for their own money-math divisions. `PRICE_SCALE=8`
with `HALF_UP` matches both of those precedents as closely as the
codebase's own conventions allow. This division needs an explicit
scale/rounding (unlike the fee division above) because `delta` is
real venue-reported data with no guarantee of producing a terminating
decimal — documented here as a judgment call rather than picked silently.

## The naive-reuse bug, proven caught

The core anti-bug test,
`secondPartialFillUsesIncrementalPriceNotNaiveCumulativeAvgPriceReuse`,
scripts a two-step partial fill: 30% (3 of 10 units) at avgPrice 100,
then 100% (10 of 10) at cumulative avgPrice 135 (i.e. the true price of
the remaining 7 units is 150: `3×100 + 7×150 = 1350`, `1350/10 = 135`).
The test asserts the second increment's own `Fill.price()` is **150**,
not 135.

Verified this test actually distinguishes the two implementations, not
just asserts a number: temporarily replaced the fix
(`incrementPrice = incrementNotional.divide(delta, PRICE_SCALE,
RoundingMode.HALF_UP)`) with the naive reuse
(`incrementPrice = avgPrice`), leaving everything else unchanged, and
re-ran only this test:

```
ExchangeOrderExecutorTest > secondPartialFillUsesIncrementalPriceNotNaiveCumulativeAvgPriceReuse() FAILED
    org.opentest4j.AssertionFailedError at ExchangeOrderExecutorTest.java:35
```

Reverted immediately, re-ran the full `ExchangeOrderExecutorTest` class
(20/20 green) and the full multi-module suite (233/233 green) to confirm
the revert was clean.

## The `SUBMISSION_UNKNOWN` judgment call

The task brief asked for a fresh read of the governing plan's Task H
section (updated after Task F's PR #77 round-2 CodeRabbit review, which
explicitly declined to design this on Task F's own PR and pointed to
"Task G" by name) to decide: does persistent `SUBMISSION_UNKNOWN`
handling belong here (the executor itself) or in Task H (wiring/safety)?

**Decision: entirely Task H, nothing built here.** Reasoning:

1. **The governing plan's own Task H section already names this
   explicitly**, including implementation-level detail
   (`VstPreflight`, kill-switch-reset-time resolution, restart
   durability) that is unambiguously wiring/safety-layer work, not
   `OrderExecutor` logic. Task F's round-2 CodeRabbit finding that first
   raised this was declined *there* specifically because it was "design
   work for a class that hasn't been written yet" — that class has now
   been written (this task), but the persistence/resolution-on-restart
   *protocol* is still a different, later concern than the executor
   itself.
2. **No persistence mechanism exists anywhere in this codebase.**
   `PaperBroker`, `OrderStore`, and now `ExchangeOrderExecutor` are all
   pure in-memory objects. Building a durable, restart-surviving marker
   store is a new architectural capability whose shape (a marker file?
   keyed how? resolved by what startup step?) is exactly the kind of
   "real design choice... deliberately left open" this project's own
   CLAUDE.md already treats with caution elsewhere (see the CSCV
   equity-curve-retention discussion) — not something to improvise as a
   side effect of building the executor.
3. **The mechanism the plan describes is achievable entirely from
   *outside* `ExchangeOrderExecutor`, without any hook built into it.**
   The plan's own phrasing — "a persistent... marker recorded
   before/around the ambiguous `adapter.submitOrder` call" — is fully
   satisfiable by a Task H-built `OrderExecutor` **decorator** (e.g. a
   `PersistentSubmissionOrderExecutor implements OrderExecutor` that
   wraps any `OrderExecutor`, including this one, recording a marker
   immediately before delegating to the wrapped `submit()` and clearing
   it after) — the same composition pattern this project already uses
   for `PaperBroker`/`ExchangeOrderExecutor` sharing one interface.
   Nothing about that design requires `ExchangeOrderExecutor` to expose
   any special extension point, so building nothing here does not
   complicate or block Task H's design.
4. **The task brief's own explicit scope excludes it**: "Do not wire
   this into `PaperTradingApp`... that's Task H" already rules out the
   startup-resolution half; a persisted marker with no wiring to
   actually resolve it on restart would be inert, so building only half
   of a two-part mechanism here would be worse than building neither
   half.

Today, an ambiguous `submit` (a thrown `adapter.submitOrder`) is still
caught by the mechanism `OrderExecutor`'s own Javadoc already documents
and this class's own Javadoc restates: the order never reaches
`pendingOrders()`, `Reconciler#check` flags it `ORPHANED_IN_BROKER`, and
`PaperTradingApp.reconcile()` trips the kill switch — a real, working,
already-tested reactive safety net, just not (yet) the proactive
resolve-before-retry protocol Task H is expected to add.

## TDD

1. Wrote `FakeExchangeAdapter` first (the test double every test would
   need).
2. Wrote `ExchangeOrderExecutorTest` in full (7 required scenarios + 13
   additional coverage tests) against a not-yet-existing
   `ExchangeOrderExecutor` class.
3. Confirmed red: `./gradlew :execution:compileTestJava` — 37 compile
   errors, all `cannot find symbol: class ExchangeOrderExecutor`. This
   also confirmed the `:exchange` Gradle dependency resolved correctly
   (no cycle errors; `OrderStatus`/`ExchangeException` types resolved).
4. Implemented `ExchangeOrderExecutor`. Ran
   `./gradlew :execution:test --tests
   "engine.execution.ExchangeOrderExecutorTest"` — green, 20/20.
5. Proved the anti-bug test is load-bearing (see "The naive-reuse bug,
   proven caught" above) — fails under the naive implementation, passes
   under the real fix.
6. Ran the full multi-module suite: `./gradlew clean build` — green.

### Required-scenario to test-method mapping

| # | Brief's scenario | Test method |
|---|---|---|
| 1 | Ack-then-fill-on-next-poll | `submitAcknowledgesThenFirstPollProducesFillWhenExchangeReportsFilled` |
| 2 | Two-step partial fill, correct incremental price | `secondPartialFillUsesIncrementalPriceNotNaiveCumulativeAvgPriceReuse` |
| 3 | `queryOrder` throwing for one order doesn't lose others' fills / doesn't propagate | `queryOrderThrowingForOnePendingOrderDoesNotLoseOtherOrdersFillsOrPropagate` |
| 4 | Rejected submit never enters pending | `submitThatResultsInRejectedNeverEntersPendingTracking` |
| 5 | `EXPIRED` after partial fill maps to cancel path | `expiredArrivingAfterAPartialFillMapsToTheCancelPathInsteadOfThrowing` |
| 6 | Unrecognized status stays pending, doesn't throw | `unrecognizedStatusLeavesOrderPendingAndDoesNotThrow` |
| 7 | Fee computed via constructor's `feeBps` | `feeIsComputedFromIncrementNotionalUsingConstructorFeeBps` |

Additional coverage tests, not individually required by the brief but
matching this codebase's own established rigor (`PaperBrokerTest`'s own
breadth): `expiredArrivingWhileStillAcknowledgedUsesTheRealExpirePath`
(the real, non-approximated `EXPIRED` path, for contrast with #5);
`dataIntegrityGuardRefusesToFabricateAPriceWhenAvgPriceIsMissingThenRecoversOnRetry`
and `dataIntegrityGuardAlsoAppliesToANonPositiveAvgPrice` (the null/
non-positive `avgPrice` guard, named explicitly in the brief but not one
of the 7 numbered scenarios);
`unexpectedOverfillFromBadVenueDataDropsOrderFromPendingWithoutPropagating`
(the "state corruption" per-order-failure branch, distinct from #3's
"transient-looking" `queryOrder`-throws branch);
`submitThatThrowsPropagatesAndLeavesOrderUntracked`;
`pollFillsIgnoresPendingOrdersForOtherSymbols` (parity with
`PaperBrokerTest`'s own equivalent test); `cancelRemovesFromPendingTrackingOnSuccess`
and `cancelRejectedByTheExchangeThrowsAndLeavesOrderInCancelPendingStillTracked`;
constructor/argument-validation tests
(`constructorRejectsNullAdapter`/`NullFeeBps`/`NegativeFeeBps`,
`submitRejectsZeroOrNegativeReferencePrice`,
`pollFillsRejectsZeroOrNegativeReferencePrice`).

## CodeRabbit review findings

One review round on PR #78 (`ASSERTIVE` profile), against commit
`fe875c5` (the original push) — confirmed via the GitHub reviews API to
target this exact commit sha (`GET .../commits/fe875c5.../status` showed
`CodeRabbit`/`success` for that sha specifically, and
`GET .../pulls/78/reviews` showed the review object's own `commit_id`
matching it), not a stale/rate-limited status. 2 actionable comments,
both real, both fixed — no finding declined.

**Finding 1 (Critical): per-order bookkeeping not atomic across the three
separate maps.** The original implementation kept `pendingOrders`,
`cumulativeFilledQty`, and `cumulativeNotional` as three independent
`ConcurrentHashMap`s. Two concurrent `pollFills` calls for the same order
could each read the same stale `previousQty`, both compute the same
`delta`, and both call `order.fill(delta)` — a real double-fill risk
`Order`'s own internal `synchronized` methods do not prevent (they
protect `Order`'s own field consistency, not the caller's read-then-write
sequence across three separate maps). `submit()` also had a narrower
publish race: nothing stopped a caller from observing the order in
`pendingOrders()` before its cumulative-tracking entries existed.

Fixed by consolidating all three into one `PendingOrderState` object
(the `Order` reference plus its own cumulative quantity/notional) stored
in a single `Map<UUID, PendingOrderState>`, mutated only from within a
`pendingOrders.computeIfPresent(id, ...)` callback in `pollFills` —
mirroring `PaperBroker.pollFills`'s own established `computeIfPresent`
pattern and its identical reasoning (serializes every invocation for the
same key; concurrent calls on different orders remain fully parallel).
`submit()` now publishes one fully-constructed `PendingOrderState` in a
single `put`, closing the narrower race too. `ExchangeAdapter#queryOrder`
— a real network call in production — is deliberately kept **outside**
the atomic block: holding a `ConcurrentHashMap` bin lock across blocking
I/O would be its own hazard, and it isn't needed for correctness, since
the delta computation inside the atomic block always reads the map's
*current* cumulative state at the moment it runs, not a value captured
before the lock — a second, slightly-stale concurrent poll for the same
order computes a smaller (often zero) delta against the already-updated
state rather than double-crediting it. This reasoning is stated in the
class Javadoc ("Atomicity and concurrency"), not just asserted here.

CodeRabbit's own suggested alternative (`synchronized` on all three
public methods) was not taken — it would fully serialize calls across
*every* order and symbol, not just concurrent access to the *same*
order, a strictly worse design than the per-key atomicity above, and
inconsistent with `PaperBroker`'s own established non-`synchronized`,
per-key pattern in this exact codebase.

Added the explicitly-requested regression test,
`concurrentPollFillsForTheSamePendingOrderFillItExactlyOnce` (20 threads,
a `CountDownLatch` start barrier, all polling the same pending order
scripted via the new `FakeExchangeAdapter.alwaysRespond` steady-state
helper) — asserts exactly one fill is produced in total across every
thread. `FakeExchangeAdapter` itself was made thread-safe as part of
this fix (its maps converted to `ConcurrentHashMap`, its
per-order-queue `poll()` calls synchronized) — it is now itself
exercised under real concurrent access, not just used from a single
thread.

**Finding 2 (Major): cumulative execution data wasn't validated before
being trusted.** Three real gaps, all in `resolveOne`/`applyStatusMapping`:
(a) a `null` `filledQuantity` was silently treated as zero regardless of
prior recorded progress — a genuine *regression* (the venue previously
reported real progress, then reports nothing) was indistinguishable from
"hasn't filled yet"; (b) nothing checked that a positive quantity delta
corresponded to a positive notional delta — a cumulative `avgPrice`
report inconsistent with previously-recorded notional could silently
produce a negative `incrementPrice`/fee; (c) a `FILLED` status removed
the order from pending tracking unconditionally, without checking that
`Order.state()` actually reached `FILLED` — trusting the status *string*
alone over the OMS's own derived truth.

Fixed all three, each routed through the state-corruption path (throw,
caught by the containing method, order dropped from pending) rather than
silently accepted or endlessly retried:

- `filledQuantity == null` **with previously-recorded nonzero progress**
  now throws (a real regression, never legitimate). `filledQuantity ==
  null` **with zero prior progress** (a fresh order's first poll(s)) is
  still treated as "no evidence of any fill yet" (equivalent to zero) —
  a deliberate, disclosed narrowing of CodeRabbit's own literal suggested
  diff (which throws on `null` unconditionally). Reasoning for the
  narrowing: this project has no empirical confirmation (from a real VST
  call — that's explicitly Task H's job) of whether BingX reports an
  unfilled order's `executedQty` as an explicit `"0"` or omits/nulls the
  field; unconditionally throwing on `null` risks a severe regression if
  the latter turns out to be true in production — every single freshly
  submitted order would be dropped from pending tracking on its very
  first poll. Throwing only on a *regression from known-nonzero progress*
  keeps the same protection CodeRabbit's finding is actually about (a
  quantity that was once known can never legitimately become unknown)
  without that production risk, and every one of this task's own tests
  that model "hasn't filled yet" already use an explicit `BigDecimal.ZERO`
  rather than relying on this fallback, so nothing in this task's own
  test suite depends on the softer treatment either way. Posted as a
  reply on the review thread rather than silently deviating.
- A positive delta whose `incrementNotional` is not strictly positive now
  throws (`nonPositiveIncrementNotionalIsTreatedAsCorruptDataAndDropsTheOrder`).
- The `FILLED` branch now throws if `order.state() != OrderState.FILLED`
  after this poll's own delta was applied
  (`filledStatusReportedWhileOurOwnStateNeverReachedFilledIsCorruptDataButPreservesTheAlreadyAppliedFill`).

**A refinement beyond CodeRabbit's own suggested diff, on the same
principle Task F's own PR #77 review response already established
("fix the underlying concern, deviate from the literal suggested diff
with reasoning when there's a good reason to")**: CodeRabbit's example
diff has the `FILLED`-state-mismatch throw propagate all the way out of
`pollFills` for that order, which — traced through the *original*
control flow — would have discarded any fill legitimately computed
earlier in the very same `resolveOne` call (e.g. a real 6-of-10-unit
delta that was already applied to `order.fill(6)` before the mismatch
against `FILLED` was even checked). That would silently under-count
`TradingLoop`'s own equity/fill-history accounting for a real execution
that genuinely happened — a new problem CodeRabbit's own suggested fix
would have introduced while fixing the one it found. Restructured
`resolveOne` so a status-mapping validation failure (this one, and any
future one added to `applyStatusMapping`) is caught locally, still
returning any fill already produced earlier in the same call, while
still flagging the order for removal from pending tracking — the two are
different concerns ("did money/quantity legitimately move, and must it
be reported" vs. "should this order keep being polled") and this keeps
them separately correct. Exceptions from `order.fill(...)` itself (state
corruption with nothing yet computed to preserve) are unaffected and
still propagate normally to `pollFills`'s outer catch. Verified this
distinction with a dedicated test,
`filledStatusReportedWhileOurOwnStateNeverReachedFilledIsCorruptDataButPreservesTheAlreadyAppliedFill`,
which asserts the 6-unit fill **is** returned even though the order is
also dropped.

Six new tests added for this finding:
`filledStatusReportedWhileOurOwnStateNeverReachedFilledIsCorruptDataButPreservesTheAlreadyAppliedFill`,
`filledQuantityDecreasingFromAPreviousPollIsTreatedAsCorruptDataAndDropsTheOrder`,
`nonPositiveIncrementNotionalIsTreatedAsCorruptDataAndDropsTheOrder`,
`nullFilledQuantityAfterAPreviousNonZeroFillIsTreatedAsCorruptDataAndDropsTheOrder`,
`nullFilledQuantityOnAFreshOrderWithNoPriorProgressIsNotAnErrorAndStaysPending`
(proving the deliberate narrowing above), and the concurrency test
counted under Finding 1. Re-verified the naive-reuse anti-bug test
(`secondPartialFillUsesIncrementalPriceNotNaiveCumulativeAvgPriceReuse`)
still fails under a temporarily-reintroduced naive implementation and
passes under the real fix, since this fix rewrote the surrounding method
substantially enough to warrant re-checking rather than assuming it
still held.

**Final state**: 26 tests in `ExchangeOrderExecutorTest` (up from the
original 20), full multi-module suite **239 tests, 0 failures, 0
errors** (213 pre-existing + 26 new — exact sum, re-confirmed the same
way as the original count). Pushed as a single follow-up commit rather
than several small ones, per this project's own CodeRabbit rate-limit
guidance ("batch fixes for multiple review findings into one push
before requesting re-review").

## Explicitly out of scope (per the governing brief, not attempted here)

- Wiring into `PaperTradingApp`, `BINGX_*` environment variables, VST
  host constants, the safety preflight (Task H).
- Any change to `BingXAdapter`/`BalanceSnapshot`/`ExchangeAdapter`
  themselves (Task H's `BalanceSnapshot.asset` addition included).
- `PaperBroker`, `TradingLoop`, `Reconciler`, `DailyReportGenerator` —
  nothing consumes `ExchangeOrderExecutor` yet; this task only adds a
  second `OrderExecutor` implementation.
- Real network calls to BingX (VST or production) — everything tested
  against `FakeExchangeAdapter` only.
- Persistent `SUBMISSION_UNKNOWN` handling — see the judgment-call
  section above.
- CLAUDE.md edits — not named in this task's own itemized scope (same
  reasoning Task F's doc already recorded for the same question).

## Verification

- `./gradlew :execution:compileTestJava` (before implementation existed)
  — failed with 37 "cannot find symbol: class ExchangeOrderExecutor"
  errors, confirming red and confirming the new `:exchange` Gradle
  dependency itself resolved cleanly.
- `./gradlew :execution:test --tests
  "engine.execution.ExchangeOrderExecutorTest"` — green, 20 tests, 0
  failures, 0 errors.
- Anti-bug regression check: temporarily reverted the incremental-price
  fix to the naive `avgPrice` reuse, re-ran the one test — failed as
  expected (`AssertionFailedError`); reverted back, re-ran — green. Full
  multi-module suite at this point: **233 tests, 0 failures, 0 errors**
  (213 from Task F's final state + 20 new from this task).
- After the CodeRabbit review round (see "CodeRabbit review findings"
  above): re-ran the same anti-bug regression check against the
  substantially-rewritten `resolveOne` — still fails under a
  temporarily-reintroduced naive implementation, still passes under the
  real fix. `./gradlew :execution:test --tests
  "engine.execution.ExchangeOrderExecutorTest"` — green, **26** tests
  (up from 20), 0 failures, 0 errors.
- `./gradlew clean build` (full multi-module suite, all six modules,
  clean, not incremental), final state after the CodeRabbit fixes —
  **BUILD SUCCESSFUL**. Aggregate JUnit XML counts across all six
  modules: **239 tests, 0 failures, 0 errors** (213 from Task F's final
  state + 26 from this task's final state — exact sum, independently
  confirmed by re-summing every module's own `tests="..."` XML attribute
  rather than trusting the arithmetic).
- Venue-agnostic design verified directly via `grep -n "^import"` against
  the real file (see "Venue-agnostic design, verified" above), not
  asserted from having written it carefully.
- `git diff --stat` against `main` before opening the PR: exactly one
  line changed in `java/execution/build.gradle.kts`
  (`implementation(project(":exchange"))`), plus three new files
  (`ExchangeOrderExecutor.java`, `ExchangeOrderExecutorTest.java`,
  `FakeExchangeAdapter.java`) — no other file touched, matching the
  task's own narrow scope.
- PR opened, not merged — per the governing brief and CLAUDE.md's
  Auto-merge Policy, this is Java Execution code and requires explicit
  human sign-off regardless of CI/CodeRabbit status.
