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
neither `OrderPipeline` nor the test's `RecordingRiskGateway` test double
could exist yet (the latter needs `RiskGateway` to be non-final, see
below) — before any production code was written. Minimum code was then
added to make all six tests pass; no refactor pass beyond that was needed
given the class's small size (see "Judgment calls" for the one thing that
did need touching outside `java/runtime`).

Six tests in `OrderPipelineTest`:

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
- `submitIntentPassesTheLiteralRiskDecisionEvaluateReturnedIntoTheOrderNotAReconstruction`
  — the provenance test, see below.

### The provenance test

**What it asserts, verbatim from the test:**

```java
RiskDecision recorded = gateway.lastDecision;
...
Order order = result.get();
assertSame(recorded.approvedQuantity(), order.approvedQuantity());
assertSame(recorded.approvedLeverage(), order.approvedLeverage());
```

`gateway` is a `RecordingRiskGateway` — a small test-only subclass of
`RiskGateway` that overrides `evaluate()` to delegate to the real
`super.evaluate()` and capture the exact `RiskDecision` object it returns,
before handing that same object back unchanged. The scenario is
deliberately a MODIFIED case (requested quantity 0.1, clamped to
0.03333333): `RiskGateway.evaluate()` computes the clamped quantity via a
fresh `BigDecimal#divide()` call on *every* invocation — never cached,
never reused — so it is never reference-equal to anything `OrderPipeline`
could have obtained from elsewhere (`intent.quantity()`, a value it
recomputed itself, or any other source). The only way `order.approvedQuantity()`
can be `==` (not just `.equals()`) to `recorded.approvedQuantity()` is if
`OrderPipeline` genuinely passed the literal `RiskDecision` instance
`evaluate()` returned all the way through to `Order.fromApprovedDecision`,
unmodified. Same reasoning for `approvedLeverage()` (`RiskLimits.baseLeverage()`,
a fixed field reused across calls — weaker signal on its own, included as
a second, corroborating check rather than the primary one).

This test is designed to fail if a future refactor reimplements the clamp
locally in `OrderPipeline` instead of delegating to `RiskGateway`, or
accidentally substitutes `intent.quantity()` for `decision.approvedQuantity()`
(exactly the class of bug this whole task exists to prevent) — either
would produce a `BigDecimal` that's value-different (for the wrong-source
case) or a distinct object even when value-equal (for a locally
reimplemented clamp), and `assertSame` would catch it.

**One thing it deliberately cannot catch, disclosed rather than silently
assumed away:** a hypothetical future refactor that reconstructs a brand
new `RiskDecision` using `decision.approvedQuantity()`/`decision.approvedLeverage()`
*getters* (i.e. `new RiskDecision(decision.intentId(), decision.decision(),
decision.reason(), decision.approvedQuantity(), decision.approvedLeverage(),
Instant.now())`) rather than passing the original object through. Because
Java doesn't defensively copy `BigDecimal` on read, that reconstruction
would still carry the identical `BigDecimal` object references, so
`assertSame` would still pass. This is a genuine, provable limit —
`Order` only ever stores `approvedQuantity`/`approvedLeverage` by
reference (see `Order`'s private constructor and `fromApprovedDecision`),
and those are the only two `RiskDecision` fields it retains, so no
observation of `Order`'s public state can distinguish "passed the literal
object through" from "faithfully reconstructed an equivalent one" — the
two are behaviorally identical from every angle `Order` exposes. Judged
acceptable: a refactor that faithfully preserves every field including by
reference isn't a behavioral regression in any way this system can
observe or that matters to correctness; the regressions that *do* matter
(wrong source value, reimplemented logic, stale/cached decision) are all
real bugs this test does catch.

## Judgment call: `RiskGateway` is no longer `final`

`RiskGateway` was `public final class RiskGateway` before this PR. Writing
the provenance test above requires intercepting the literal object
`evaluate()` returns, and there is no way to do that from outside
`engine.risk` without either (a) a mocking framework (none exists in this
codebase — nothing beyond JUnit 5 + slf4j is a project dependency anywhere
in `java/`) or (b) subclassing. `OrderStore` (`:oms`) is also `final` and
was left untouched — it isn't the class whose return value needs
capturing here, so there was no equivalent need to touch it.

Removing `final` is a structural-only change: nothing about risk
evaluation logic changed, `evaluate()`'s body is untouched, and no
production subclass of `RiskGateway` exists anywhere in this codebase —
only `OrderPipelineTest`'s package-private `RecordingRiskGateway`, which
delegates to `super.evaluate()` for the real logic and only adds
capture-and-return-unchanged behavior around it. This doesn't reopen the
provenance gap this task exists to close: a malicious caller who wants an
`Order` without going through real risk evaluation was never blocked by
`RiskGateway`'s finality in the first place — they could already
hand-construct a `RiskDecision` directly and call
`Order.fromApprovedDecision` (or `OrderStore.createOrder`) themselves,
bypassing `RiskGateway` entirely, exactly the pre-existing gap this PR is
about. `RiskGateway`'s finality was never a real security boundary for
that threat; `OrderPipeline`'s own code shape (the "only ever calls
`fromApprovedDecision` with the object two lines above" discipline) is
what actually closes it.

Flagged here explicitly per CLAUDE.md's "state assumptions and ask rather
than silently pick between valid interpretations" — this is a cross-module
touch (`java/risk`, not just the new `java/runtime` module this task was
otherwise scoped to), made because the task's own suggested testing
technique ("make a test double / subclass of `RiskGateway`") requires it
and there was no less invasive way to achieve genuine (not merely
value-based) provenance testing.

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
