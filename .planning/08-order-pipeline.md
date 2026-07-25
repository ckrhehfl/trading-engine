# Implementation Priority #8, Task A: `OrderPipeline`

## Scope note

This is **Task A only** of Implementation Priority #8. Task B — the actual
paper trading loop (`DummySignalSource`, `BingXPriceFeed`, `TradingLoop`,
`KillSwitch`, 24/7 restart/health-check supervision) — is separate, later
work, deliberately not built here. This task exists specifically so the
Risk Gateway provenance gap named repeatedly in Priority #7's review (see
below) could land on its own, get real scrutiny, and not be rushed as a
side effect of building the trading loop.

## What was built

New Gradle module `java/runtime` (package `engine.runtime`), containing a
single class: `OrderPipeline`. Constructor takes a `RiskGateway` and an
`OrderStore` (both pre-existing, from `:risk` and `:oms`). One method:

```java
public Optional<Order> submitIntent(OrderIntent intent, BigDecimal referencePrice, AccountState account)
```

Calls `riskGateway.evaluate(intent, referencePrice, account)`; if the
result is APPROVED or MODIFIED, hands that exact `RiskDecision` to
`orderStore.createOrder(intent, decision)` and returns the resulting
`Order`. If REJECTED, logs the reason at INFO and returns
`Optional.empty()` — no `Order` is created.

## Why this exists

`Order.fromApprovedDecision(OrderIntent, RiskDecision)` — the only way to
construct an `Order` — only ever checked that the `RiskDecision` it was
given said APPROVED/MODIFIED and that its `intentId` matched. It had no
way to tell whether that `RiskDecision` actually came from a real
`RiskGateway.evaluate()` call or was hand-built by whoever called it. This
was flagged five times during Priority #7's review (PR #21, escalating to
"Critical" severity by the third pass), and each time the correct answer
was "real gap, but it's #8's job to close, not #7's" — closing it requires
an actual `OrderIntent → RiskGateway.evaluate() → Order` pipeline, which
didn't exist anywhere in the codebase before this PR. `OrderPipeline` is
that pipeline.

The safety property this class provides isn't enforced by any check
*inside* `Order` (it still has no way to know where a `RiskDecision` came
from — that's structurally impossible to check locally, and out of scope
to change here). It's enforced by `OrderPipeline`'s own shape:
`submitIntent` is the *only* thing in this new module that ever calls
`Order.fromApprovedDecision` (via `OrderStore.createOrder`), and it does
so with the exact `RiskDecision` instance `evaluate()` just returned two
lines above — never a copy, never a caller-supplied one, no other path or
overload exists that would let a caller substitute its own `RiskDecision`.

## TDD

Tests were written first (`OrderPipelineTest`) and confirmed to fail to
*compile* — the normal red state for a statically-typed language, since
`OrderPipeline` didn't exist yet — before any production code was written.
Minimum code was then added to make all tests pass; no refactor pass
beyond that was needed given the class's small size.

**One round-trip during review changed the provenance test's design** (see
"CodeRabbit review findings" below): the first version made `RiskGateway`
non-final so a test-only subclass could capture the literal `RiskDecision`
object `evaluate()` returns. CodeRabbit correctly flagged that as
reopening the exact bypass this task exists to close — a `RiskGateway`
subclass overriding `evaluate()` to always approve, handed to
`OrderPipeline`, would look legitimate. `RiskGateway` was reverted to
`final` (byte-for-byte back to its Priority #3 form) and the provenance
test rebuilt using two techniques that need no change to `:risk` at all —
see "The provenance tests" below.

Seven tests in `OrderPipelineTest`:

- `approvedIntentCreatesOrderRegisteredInStoreInNewState` — APPROVED path,
  confirms registration via `OrderStore.findByClientOrderId`, state `NEW`.
- `modifiedIntentCreatesOrderWithRiskGatewayClampedQuantityNotTheRequestedQuantity`
  — MODIFIED path, using the same notional-clamp scenario `RiskGatewayTest`
  already establishes (`RiskGatewayTest.notionalOverLimitIsModifiedAndClamped`):
  requested quantity 0.1 clamps to 0.03333333 (maxNotional 2000 / price
  60000). Asserts the resulting `Order`'s `approvedQuantity()` is the
  *clamped* value, genuinely different from `requestedQuantity()` — not
  just "an order got created."
- `rejectedIntentReturnsEmptyAndCreatesNoOrder` — daily-loss-limit breach
  (same fixture shape as `RiskGatewayTest.dailyLossLimitBreachRejectsOrder`),
  confirms `Optional.empty()` and that `OrderStore` has no entry for that
  intent id afterward.
- `identicalRetrySameIntentIdReturnsTheOriginalOrderWithoutCreatingASecondOne`
  — calling `submitIntent` twice with the same `OrderIntent` object (and
  thus a fresh but value-equal `RiskDecision` each time) confirms
  `OrderStore`'s existing idempotency (`Order.matches`) isn't broken or
  bypassed by going through `OrderPipeline` — `assertSame` on the two
  returned `Order` references.
- `conflictingRetrySameIntentIdDifferentQuantityThrowsAndDoesNotBypassOrderStoreConflictDetection`
  — same intent id, genuinely different requested quantity on the second
  call. Confirms `OrderStore`'s existing conflict detection
  (`IllegalStateException`) still fires through `OrderPipeline`, and that
  the original `Order` is left untouched in the store.
- `approvedIntentQuantityAndBaseLeverageFlowThroughByReferenceNotByValueCopy`
  and `orderPipelineSourceNeverFabricatesARiskDecisionAndPassesTheSingleEvaluateResultDirectlyToOrderStore`
  — the two-part provenance test, see below.

### The provenance tests

`RiskGateway` and `OrderStore` both stay exactly as they were before this
module existed: `final`, unmocked (no mocking framework exists in this
codebase), unsubclassable. That constraint (see "CodeRabbit review
findings" for why it's non-negotiable) means a runtime capture of the
*exact* `RiskDecision` object `evaluate()` produces inside `submitIntent`
isn't achievable — so provenance is proven two different ways instead,
each covering what the other can't:

**Part 1 (dynamic), `approvedIntentQuantityAndBaseLeverageFlowThroughByReferenceNotByValueCopy`:**

```java
BigDecimal quantity = new BigDecimal("0.01");
OrderIntent intent = limitIntent(quantity, new BigDecimal("60000"));
...
assertSame(quantity, order.approvedQuantity());
assertSame(limits.baseLeverage(), order.approvedLeverage());
```

`RiskGateway.evaluate()`'s APPROVED branch returns `intent.quantity()`
completely unchanged (verified by reading `RiskGateway.java` directly, not
assumed) — so the *only* way `order.approvedQuantity()` is the same
object (`==`, not `.equals()`) as the `BigDecimal` this test constructed
is if that reference flowed, untouched, through `evaluate()` →
`RiskDecision` → `Order.fromApprovedDecision`. `approvedLeverage()` gets
the same treatment against `limits.baseLeverage()`, a fixed field on the
`RiskLimits` instance the test constructed, reused unchanged by every
`evaluate()` call against it. This catches real regressions: reimplementing
the clamp/approval logic locally in `OrderPipeline`, or substituting the
wrong source value (e.g. `intent.quantity()` where `decision.approvedQuantity()`
was meant) — either produces a `BigDecimal` that's a different object
(and, in the wrong-source case for a MODIFIED scenario, a different value
too), and `assertSame` catches it even when a value-only test wouldn't.

**Part 2 (structural), `orderPipelineSourceNeverFabricatesARiskDecisionAndPassesTheSingleEvaluateResultDirectlyToOrderStore`:**
reads `OrderPipeline.java`'s own source at test time and asserts: it never
contains `new RiskDecision(`; it calls `riskGateway.evaluate(` exactly
once; it calls `orderStore.createOrder(` exactly once; and the local
variable assigned from `evaluate()`'s return value is the literal
identifier passed into `createOrder(intent, ...)` — not reassigned, not
rebuilt in between.

**Why two tests, and why part 2 exists at all:** part 1's dynamic check
has one provable blind spot — a hypothetical future refactor that
reconstructs a *new* `RiskDecision` purely from
`decision.approvedQuantity()`/`decision.approvedLeverage()` *getters*
(`new RiskDecision(decision.intentId(), decision.decision(), decision.reason(),
decision.approvedQuantity(), decision.approvedLeverage(), Instant.now())`)
rather than passing the original object through. Java doesn't defensively
copy `BigDecimal` on read, so that reconstruction would still carry the
identical object references and pass every `assertSame` in part 1 — no
observation of `Order`'s public state (it only ever stores
`approvedQuantity`/`approvedLeverage` by reference, the only two
`RiskDecision` fields it retains at all) can distinguish "passed the
literal object through" from "faithfully reconstructed an equivalent
one." Part 2 closes exactly that gap, directly, by checking the code
shape instead of runtime behavior — a `new RiskDecision(` call anywhere
in `OrderPipeline.java`, or a reassignment between `evaluate()` and
`createOrder()`, fails it regardless of whether the reconstructed values
happen to be correct.

## Judgment call resolved during review: `RiskGateway` stays `final`

The first version of this PR made `RiskGateway` non-final so a test-only
subclass (`RecordingRiskGateway`) could capture the literal `RiskDecision`
object `evaluate()` returns. CodeRabbit's first-pass review (see below)
correctly rejected this: making `RiskGateway` subclassable means *any*
caller — not just the test — can hand `OrderPipeline` a `RiskGateway`
subclass whose overridden `evaluate()` always approves, and `OrderPipeline`
has no way to tell the difference from the real thing. That's the exact
provenance bypass this task exists to close, reopened one level up. The
counter-argument originally written here — "a malicious caller could
already bypass `RiskGateway` by hand-building a `RiskDecision` and calling
`OrderStore.createOrder` directly, so subclassing doesn't add a new hole"
— is true as far as it goes, but misses that `OrderPipeline`'s entire
value proposition is being the thing *other, honest* code can trust
without re-verifying; making `RiskGateway` overridable weakens that trust
even where no bypass was previously reachable through `OrderPipeline`
itself. `RiskGateway` is reverted to `final`, byte-for-byte back to its
Priority #3 form, and the provenance test rebuilt as the two-part test
above.

## CodeRabbit review findings

One Critical finding on the first review pass (Korean original in the PR
review comment; summarized here): removing `final` from `RiskGateway`
lets a subclass override `evaluate()` to always approve, and `OrderPipeline`
can't tell that apart from the real thing; separately, `OrderStore.createOrder(intent,
decision)` staying public means code can still call it directly with a
hand-built `RiskDecision`, bypassing `OrderPipeline` entirely.

**Fixed:** the `RiskGateway`-finality half — reverted, see "Judgment call
resolved during review" above. This was unambiguously correct and fully
within this task's scope to fix (it was this PR's own regression, not a
pre-existing condition).

**Not fixed in this PR, flagged instead:** the `OrderStore.createOrder`
half. Making that call structurally impossible to reach with a hand-built
`RiskDecision` — e.g. restricting its visibility to a designated "trusted"
package, or adding some capability/token scheme to `RiskDecision` that
only `RiskGateway` can mint and `Order.fromApprovedDecision` verifies — is
a real, correctly-identified gap, but it's a larger redesign of already-
shipped, already-reviewed OMS/Risk public API (`OrderStore` and `Order`
date to Priority #2 and #3, both merged and built on by #6 and #7 since).
This is precisely the same finding CodeRabbit raised five times during
Priority #7's review of `BingXAdapterTest` (see `.planning/07-exchange-adapter.md`'s
"CodeRabbit review findings" → declined items), resolved there by explicit
deferral to Priority #8 with @ckrhehfl's direct sign-off on that exact
tradeoff (2026-07-24). The same resolution applies here: `OrderPipeline`
existing at all, and being the only sanctioned path in this new module, is
the "the honest path is real and exercised" half of the fix Priority #8's
own CLAUDE.md wording calls for; making the dishonest path structurally
unreachable — not just unused — is a bigger, cross-module architectural
decision this task's scope (a new, additive module, explicitly *not*
touching `java/execution`/`java/exchange`, and per the task brief not
meant to redesign `:oms`/`:risk`'s existing public surface) doesn't cover,
and CLAUDE.md's Auto-merge Policy reserves changes to OMS/Risk Gateway
logic for explicit human sign-off regardless. Tracked here as an open item
for whoever picks up Task B or a future hardening pass, not silently
dropped.

## What's deliberately out of scope / deferred

- **Task B of Priority #8** (the actual trading loop): `DummySignalSource`,
  `BingXPriceFeed`, `TradingLoop`, `KillSwitch`, 24/7 restart/health-check
  supervision. Not built here — separate, later work.
- **`java/execution` and `java/exchange`**: untouched. `OrderPipeline`
  does not depend on either module (no `PaperBroker`/`ExchangeAdapter`
  wiring) — this task only closes the `OrderIntent → Order` gap, not the
  `Order → exchange` side, which Task B's `TradingLoop` will need to wire
  up separately.
- **Persistence**: `OrderStore` is still in-memory only (unchanged,
  pre-existing scope note from Priority #2).
- **Retry/backoff, position reconciliation**: unchanged from Priority #7's
  deferred list — still Task B / later priorities.
- **The full `Order.fromApprovedDecision()` → real-`evaluate()` wiring
  CLAUDE.md's Priority #8 entry calls for** is now closed for the
  `OrderIntent → RiskGateway → Order` half. The other half named in that
  same entry — verifying that *every* live order-placement path in the
  eventual trading loop actually goes through `OrderPipeline` rather than
  calling `Order.fromApprovedDecision` some other way — is Task B's job,
  once `TradingLoop` exists to check that against.
