# `OrderStore.createOrder` interim hardening — investigated, not shipped

## Scope note

This is **not** Implementation Priority #10 itself — it's a small, standalone
investigation carried out ahead of it, prompted by @ckrhehfl asking whether a
smaller, honest runtime protection could be added to `OrderStore.createOrder`
*now*, as an interim measure, without doing the full `:oms`→`:risk`
dependency change that CLAUDE.md's Priority #10 entry already describes as
the complete fix. The complete fix itself is **unchanged** by this
investigation — still deferred to Priority #10's live-wiring work, for the
reasons already recorded there. This document exists to record why the one
concrete interim mechanism investigated (a `java.lang.StackWalker`-based
caller check) was rejected, with real evidence rather than a hand-wave, so a
future session doesn't re-attempt it as a "quick win" without first
understanding why it doesn't work in this codebase's actual test suite.

## Background — don't re-derive this, it's already established

`OrderStore.createOrder(OrderIntent, RiskDecision)` is `public`, so code can
call it directly with a hand-built `RiskDecision`, bypassing
`engine.runtime.OrderPipeline` (the one real
`OrderIntent → RiskGateway.evaluate() → Order` path, built in Priority #8
Task A, PR #24). This was flagged five times during Priority #7's CodeRabbit
review (`.planning/07-exchange-adapter.md`) and tracked as an open item since
(`.planning/08-order-pipeline.md`, `.planning/08b-trading-loop.md`, and
CLAUDE.md's Priority #8/#10 entries). There is no live-order code path in the
repo today, so there is no active exploit window either way — this is a
hardening-for-the-future exercise, not an incident response.

The real root, confirmed by reading the actual source rather than assumed:
`Order.fromApprovedDecision(OrderIntent, RiskDecision)` is what actually
constructs an `Order`; `OrderStore.createOrder` is a thin wrapper around it
(`orders.computeIfAbsent(intent.intentId(), id -> Order.fromApprovedDecision(intent, decision))`).
Fully closing the gap means changing `Order.fromApprovedDecision`'s
signature, not just `OrderStore`'s — that's the complete fix CLAUDE.md's
Priority #10 entry describes (`engine.risk.VerifiedRiskDecision`, a
capability type only `RiskGateway` can construct). This document is only
about whether a smaller, StackWalker-based interim measure inside
`OrderStore.createOrder` alone is worth shipping before that.

## What was investigated: a `StackWalker`-based caller check

The mechanism named as plausible when this task was scoped:
`java.lang.StackWalker` (built into the JDK since 9, no new dependency) to
inspect the immediate caller of `createOrder` and reject (throw) any call
whose caller class isn't `engine.runtime.OrderPipeline`.

This was not evaluated on paper only — it was actually implemented as a
real, working prototype in `OrderStore.java`:

```java
private static final String ALLOWED_CALLER = "engine.runtime.OrderPipeline";

public Order createOrder(OrderIntent intent, RiskDecision decision) {
    StackWalker walker = StackWalker.getInstance();
    String caller =
            walker.walk(s -> s.skip(1).findFirst().map(StackFrame::getClassName).orElse(""));
    if (!ALLOWED_CALLER.equals(caller)) {
        throw new SecurityException(
                "OrderStore.createOrder must only be called by " + ALLOWED_CALLER + ", got " + caller);
    }
    Order order = orders.computeIfAbsent(...);
    ...
}
```

Then `./gradlew clean test` was run against the whole repo with this
prototype in place, to see the real effect rather than guess at it.

## Concrete finding: it breaks the existing, established `OrderStoreTest` suite

Result, verbatim from the run:

```
OrderStoreTest > duplicateCreateOrderReturnsTheSameOrderInstance() FAILED
    java.lang.SecurityException at OrderStoreTest.java:61

OrderStoreTest > findByClientOrderIdReturnsCreatedOrder() FAILED
    java.lang.SecurityException at OrderStoreTest.java:126

OrderStoreTest > retryWithNowRejectedDecisionIsRejectedAsConflicting() FAILED
    java.lang.SecurityException at OrderStoreTest.java:111

OrderStoreTest > retryWithDifferentRequestedQuantityIsRejectedEvenIfApprovedQuantityCoincidentallyMatches() FAILED
    java.lang.SecurityException at OrderStoreTest.java:85

OrderStoreTest > retryWithDifferentApprovedQuantityIsRejectedAsConflicting() FAILED
    java.lang.SecurityException at OrderStoreTest.java:72

OrderStoreTest > createOrderReturnsNewOrderForNewClientOrderId() FAILED
    java.lang.SecurityException at OrderStoreTest.java:49

OrderStoreTest > concurrentCreateOrderWithSameIdProducesExactlyOneOrderForEveryCaller() FAILED
    java.lang.AssertionError at OrderStoreTest.java:175
        Caused by: java.util.concurrent.ExecutionException at OrderStoreTest.java:173
            Caused by: java.lang.SecurityException at OrderStoreTest.java:149

30 tests completed, 7 failed
```

7 of `OrderStoreTest`'s 8 existing tests fail immediately. The 8th
(`findByClientOrderIdIsEmptyForUnknownId`) is unaffected only because it
never calls `createOrder` at all. Every one of the 7 failures is because
`OrderStoreTest` itself — a test class in package `engine.oms`, the same
package `OrderStore` lives in — calls `store.createOrder(intent, decision)`
**directly**, exactly the same call shape the task's own required proof-test
("constructs a fresh `OrderStore` and attempts to call `createOrder`
directly from the test itself, not through `OrderPipeline`, asserting it
throws") would also have to exercise.

## Why this is a structural conflict, not a fixable edge case

This isn't a matter of tweaking the allowlist or writing a smarter check.
From `StackWalker`'s point of view, the currently-passing `OrderStoreTest`
tests and the new required test are **indistinguishable**: same class
(`OrderStoreTest`), same package (`engine.oms`), same direct-call shape.
Any rule that makes the new test's call throw necessarily makes all 7 old
tests' calls throw too — they're the same call. Conversely, any rule
carved out to spare the old tests (e.g. exempting the `engine.oms` package,
or exempting the `OrderStoreTest` class by name) also spares the new test's
call, since it's made from that exact same class and package — which
defeats the entire point of writing the new test, and defeats the check's
purpose generally (a "protection" that doesn't fire for code sitting right
next to the method it's supposed to protect isn't much of a protection).

This wasn't a corner case stumbled into by accident. `OrderStoreTest`
calling `createOrder` directly — bypassing whatever the "real" pipeline
would be — is a **deliberate, established pattern** in this codebase, used
specifically to unit-test `OrderStore`'s own idempotency and conflict-
detection logic (duplicate-id reuse, conflicting-retry rejection, the
now-rejected-decision case) in isolation, without needing a real
`RiskGateway`/`OrderPipeline` wired up for every test. It is the *exact
same pattern*, one level up, as `Order.fromApprovedDecision` being called
directly (not via `OrderStore`, let alone `OrderPipeline`) from `OrderTest`,
`PaperBrokerTest`, and `BingXAdapterTest` — confirmed by grep:

```
java/oms/src/test/java/engine/oms/OrderTest.java:39,69,242,270: Order.fromApprovedDecision(...)
java/execution/src/test/java/engine/execution/PaperBrokerTest.java:40,56,225,226: Order.fromApprovedDecision(...)
java/exchange/src/test/java/engine/exchange/BingXAdapterTest.java:66: Order.fromApprovedDecision(...)
```

A caller-identity check is structurally incompatible with this codebase's
existing, correct testing approach — not just inconvenient for it. That
approach (construct the OMS primitive directly, from whichever module needs
to unit-test something built on top of it, without forcing every test
through the one "real" end-to-end pipeline) is sound engineering practice,
already used four times over across three modules, and not something this
task should be quietly breaking or working around just to land an interim
check.

## Other mitigations considered and also rejected

- **Package-scoped allowlist** (`engine.oms` OR `engine.runtime.OrderPipeline`
  as allowed callers, instead of only the latter). Rejected: this exempts
  exactly the class/package the new required test calls from, so the new
  test would no longer throw — same problem as above, just relocated. It
  also doesn't stop a bypass placed inside `engine.oms` itself, which is
  precisely the trust boundary the deferred fix's `:oms`→`:risk` dependency
  and `VerifiedRiskDecision` capability type are meant to police — a
  same-package exemption quietly reopens the exact gap being closed.
- **Advisory / log-only** (log a warning on an unexpected caller, don't
  throw). Rejected on two grounds: (1) it doesn't provide the "fails
  loudly" property that's the entire stated value of a runtime deterrent —
  a WARN line nobody is watching in real time is not a meaningful
  protection; (2) it would still fire on every one of `OrderStoreTest`'s 7
  legitimate, expected direct calls on every single test run, drowning any
  real signal in constant, expected noise — degrading the check to the
  point of being actively misleading rather than merely unhelpful.
- **`RiskDecision.timestamp()` staleness check** (reject a decision whose
  timestamp isn't within some small recent window). Rejected as pure
  theater: any hand-built `RiskDecision` can trivially set
  `Instant.now()` as its own timestamp (the existing `OrderStoreTest`
  fixture helper already does exactly this), so the check would provide
  zero actual protection against the thing it's supposed to catch — a
  fake sense of security is explicitly worse than no check per this
  task's own framing.
- **In-process nonce/registry** (e.g. `RiskGateway.evaluate()` registers
  each produced decision's `intentId` in a shared, in-memory set;
  `OrderStore.createOrder` checks membership and consumes it once).
  Rejected: this requires `engine.oms` to reference something
  `engine.risk` writes, which is exactly the deferred `:oms`→`:risk`
  Gradle dependency this task was explicitly told not to add here.
- **JPMS module boundaries** (`exports engine.oms to engine.runtime`).
  Rejected: confirmed via `find java -name module-info.java` returning
  zero results anywhere in the repo. Introducing JPMS now, solely to
  enforce one caller-identity check on one method, is a large, invasive,
  repo-wide change disproportionate to a scoped interim hardening.
- **Package-private visibility on `createOrder`**. Already ruled out
  before this task started (recorded in CLAUDE.md's existing Priority #8
  entry): `OrderPipeline` lives in a different package
  (`engine.runtime`), so package-private visibility wouldn't grant it
  access either — reconfirmed here, not new information.

## Why the deferred, complete fix doesn't have this problem

Worth spelling out, since it's easy to assume "the small fix is hard, so the
big fix must be even harder" — that's backwards here. The complete fix
(`engine.risk.VerifiedRiskDecision`, see CLAUDE.md's Priority #10 entry) is
a **capability/type-based** check — it verifies *what* was passed (a
`RiskDecision` that genuinely went through `RiskGateway.evaluate()`), not
*who* called. Test code in any module can legitimately obtain a real
`VerifiedRiskDecision` by making a real `RiskGateway.evaluate()` call — and
this is not blocked by any circular-dependency problem: `:risk`'s
`build.gradle.kts` depends only on `:schemas`, not `:oms`, so a
`testImplementation(project(":risk"))` addition to `:oms`'s test sourceset
(needed for `OrderStoreTest` to construct a real `RiskGateway`) is not
circular, confirmed by inspecting `java/risk/build.gradle.kts` directly.
Once obtained, any test in any module can legitimately construct an
`Order`/`OrderStore` entry with it — the check doesn't care which class or
module is doing the constructing, only whether the capability it's holding
is genuine. This is fully compatible with the existing, correct pattern of
constructing OMS primitives directly across modules for isolated unit
testing (`OrderStoreTest`, `OrderTest`, `PaperBrokerTest`,
`BingXAdapterTest` all keep their current calling shape; only their fixture
-construction step changes, from a hand-built `RiskDecision` to a real
`evaluate()`-derived `VerifiedRiskDecision`). This isn't new information
that changes the complete fix's design — it's confirmation, arrived at from
a different direction, that the complete fix is the right shape of
solution and worth doing once, rather than approximating with a
caller-identity shortcut that this investigation shows doesn't actually
work in this codebase.

## Decision

No code change ships in `java/oms` (or anywhere else) from this
investigation. `OrderStore.java`, `Order.java`, and every existing test are
byte-for-byte unchanged from `origin/main` — the `StackWalker` prototype
described above was written, run to get the real evidence in this document,
and then fully reverted (confirmed via `git status` showing a clean working
tree afterward). Shipping the package-scoped or advisory variants
considered and rejected above would have created false confidence in a
protection that either doesn't actually cover the risk it's named for, or
degrades into ignored noise — worse than shipping nothing, per this task's
own explicit framing.

The complete fix stays exactly as already described in CLAUDE.md's
Implementation Priority #10 entry, unchanged by this investigation — see
CLAUDE.md for the full design, the reasoning for why it's the right shape,
the confirmed test-fixture impact across `:execution`/`:exchange`/`:runtime`,
and the timing decision (bundled with Priority #10's live-wiring work,
not done as an isolated refactor beforehand).

## Verification

`./gradlew clean test` (from a clean checkout of this branch, no production
code touched): **164 tests, all green** — identical to the pre-existing
baseline on `main`, since no production code changed. The `StackWalker`
prototype's real failure mode (7 of 8 `OrderStoreTest` tests failing) was
observed and is quoted verbatim above, then reverted before this branch's
actual diff (this document plus the corresponding CLAUDE.md update) was
committed.
