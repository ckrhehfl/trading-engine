# Paper-trading bridge, Task E: minimal internal reconciliation

## Scope note

This is **Task E** of the 5-task paper-trading bridge plan governing
`daily-tsmom-ensemble`'s human-approved move to paper trading (see
CLAUDE.md's "Paper Trading Policy Exception" and
`.claude/plans/tender-finding-matsumoto.md`, the governing plan — same
reference convention `.planning/paper-trading-b-signal-runner.md` and
`.planning/paper-trading-c-scheduler-entrypoint.md` already use). Depends
on Task C (`PaperTradingApp`, merged, PR #71). R3-risk component
(`java/runtime`) — TDD discipline applied throughout, per CLAUDE.md's
Development Methodology. Task D (daily reporting) had not landed and no
PR for it existed at the time this task started (checked `git log --all`
and `gh pr list` directly) — this task's public interface
(`ReconciliationReport`/`ReconciliationMismatch`, `PaperTradingApp
.reconcile()`/`.lastReconciliationReport()`) is deliberately clean and
self-contained so Task D can call it later without a merge conflict, but
nothing here assumes Task D's code exists.

## What this is, and what it explicitly is not

A minimal **internal** consistency check between `OrderStore`'s own order
bookkeeping and `PaperBroker`'s own simulated-fill bookkeeping — catching
bugs in this project's own two independent in-process data structures.
**Not** real-exchange reconciliation: there is no `BingXAdapter` anywhere
in the paper-trading loop (that's Priority #7/#10 territory, deferred).
This is what CLAUDE.md's "zero duplicate orders, zero position mismatches"
Paper Trading Pass Criteria can concretely mean before a real exchange is
in the loop at all.

## Reading the real code first: what's actually queryable

Per this task's own brief, design started by reading `OrderStore.java`,
`Order.java`, and `PaperBroker.java` directly rather than assuming their
shape.

- **`OrderStore`** is a `ConcurrentHashMap<UUID, Order>` keyed by client
  order id, with exactly two public methods: `createOrder(intent,
  decision)` (idempotent — returns the existing `Order` for a repeat id,
  or throws if the repeat doesn't match) and `findByClientOrderId(UUID)`.
  **There is no enumeration method** — no way to ask "what orders do you
  have" without already knowing every id to ask about.
- **`PaperBroker`** tracks `pendingOrders` (a `Map<UUID, Order>` of
  currently-open, not-yet-filled orders, exposed read-only via
  `pendingOrders()`) and a private `seenClientOrderIds` set (defense-in-
  depth against a caller bypassing `OrderStore`, not exposed at all).
  **It also has no full-history enumeration** — `pendingOrders()` only
  shows what's currently open; a filled or cancelled order simply
  disappears from it with nothing else in `PaperBroker` retaining a
  record.

Per the task brief's explicit instruction ("don't invent checks against
data that isn't actually available"), neither class was modified to add
new query surface — both are on this task's own "do not touch ...
internals unless a genuine, disclosed bug is found" list, and no bug was
found. Everything below is built strictly against the two classes'
existing public API.

## The load-bearing finding: `Order` is one shared mutable object, not two independent copies

Reading `OrderPipeline.submitIntent` and `TradingLoop.tick()`/
`submitToBroker` together surfaces something not obvious from either
class's Javadoc alone: `OrderPipeline.submitIntent` registers an `Order`
in `OrderStore` and returns that *exact* object; `TradingLoop` then hands
that *exact same reference* to `PaperBroker.submit`, which calls
`order.submit()`/`order.acknowledge()`/`order.fill()` directly on it.
`OrderStore`'s map and `PaperBroker`'s `pendingOrders` map, whenever they
both hold an entry for the same id, are holding **the same object in
memory**, not two independent representations of the same order.

This matters because it rules out the most obvious design: a naive
"does `OrderStore`'s state string equal what `PaperBroker` thinks the
state is" check would be trivially, uselessly true by construction —
there is only one `state` field, read twice. Similarly, a quantity/price
cross-check between `Order.filledQuantity()` and a `Fill`'s own
quantity/price was considered and rejected for the identical reason:
`PaperBroker.tryFill` computes a `Fill` using the exact same `BigDecimal`
it passes to `order.fill(...)`, and with today's fill-or-nothing (no
partial-fill) logic in `PaperBroker`, that comparison is also
structurally guaranteed to match. Implementing either would be a check
that always passes and tests nothing.

## What's actually independently checkable, given that finding

Two failure modes remain genuinely independent even with the shared-
reference design, and both are checked:

1. **An order exists in one structure's bookkeeping but never reached
   the other at all.** `TradingLoop.submitToBroker`'s own Javadoc already
   names this as a real, if rare, failure mode: `PaperBroker.submit`
   throwing after the order was already registered in `OrderStore`
   leaves an orphan — registered, but never handed to the broker, so it
   will never fill or cancel. The reverse (something reaching
   `PaperBroker` that was never registered in `OrderStore`) is also
   checked, symmetrically, as defense-in-depth against a caller
   bypassing `OrderPipeline` entirely.
2. **The same client order id gets submitted through the pipeline more
   than once.** Not caught by the shared-reference property at all — it's
   a question about *how many times* `submitIntent` was called with a
   given id, which nothing in `OrderStore`/`PaperBroker`'s own state
   retains once the second attempt fails.

### The four mismatch types (`ReconciliationMismatchType`)

| Type | Detects |
|---|---|
| `ORPHANED_IN_BROKER` | `OrderStore` has an order in a still-open state (`NEW`/`SUBMITTED`/`ACKNOWLEDGED`/`PARTIALLY_FILLED`/`CANCEL_PENDING`), but `PaperBroker.pendingOrders()` has no record of it. |
| `DUPLICATE_SUBMISSION_ATTEMPT` | The same client order id appears more than once in the known-submission history. |
| `MISSING_FROM_ORDER_STORE` | A known-submitted id has no `OrderStore` record at all — structurally shouldn't happen given `OrderPipeline`'s own contract, included as a cheap sanity check, same spirit as `PaperBroker`'s own `seenClientOrderIds` defense-in-depth. |
| `UNTRACKED_IN_BROKER` | `PaperBroker.pendingOrders()` has an id the known-submission history never recorded — the symmetric counterpart to `MISSING_FROM_ORDER_STORE`. |

A legitimately **filled** or **cancelled** order correctly disappearing
from `pendingOrders()` is the expected, designed behavior (`PaperBroker
.tryFill`/`.cancel` both remove it) — every terminal `OrderState`
(`FILLED`, `CANCELLED`, `REJECTED`, `EXPIRED`) is deliberately excluded
from the "should still be pending" check, so a normal fill is never
flagged. `ReconcilerTest.aLegitimatelyFilledOrderCorrectlyAbsentFrom
PendingOrdersIsNotAMismatch` proves this directly.

### What "position mismatch" means here

No `Position` class exists anywhere in this codebase yet (confirmed —
the governing plan itself says so). `PaperBroker` has no independent net-
position or quantity ledger to compare an `OrderStore`-derived position
against, so a real position-level reconciliation (net quantity per
symbol) is not buildable from what currently exists — only an
order-bookkeeping-level one is. This check operationalizes CLAUDE.md's
"zero position mismatches" Paper Trading Pass Criterion as order-state
consistency between the two structures that actually exist today, which
is what that criterion can concretely mean before a real exchange (and a
real `Position` concept) is in the loop — matching this task's own brief
almost verbatim.

## Where the "known submitted order ids" come from

Since neither `OrderStore` nor `PaperBroker` enumerates its own full
history, something has to supply the list of ids to check. `TradingLoop`
is the one place that actually creates orders (via `OrderPipeline
.submitIntent`), so it's the natural, minimal owner: a new `private final
List<UUID> submittedOrderIds` field is appended to inside `tick()`
whenever `submitIntent` returns a real `Order`, and exposed read-only via
a new `submittedOrderIds()` accessor — the same "health-check surface"
pattern `TradingLoop` already uses for `lastTickAt()`/`lastError()`/
`currentEquity()`.

**Judgment call: this required a small, disclosed touch to `TradingLoop`**
(field, one line inside `tick()`, one new accessor method) — `TradingLoop`
is not on this task's "do not touch ... internals" list (unlike
`RiskGateway`/`OrderPipeline`/`PaperBroker`/`OrderStore`/`KillSwitch`),
and no alternative avoids it: the only other place that ever sees a
freshly-created `Order` is `TradingLoop.tick()` itself. Two alternatives
were considered and rejected:

- **Add an enumeration method to `OrderStore`** (e.g. `allOrders()`,
  mirroring `PaperBroker.pendingOrders()`'s own existing convention).
  Rejected: `OrderStore` is explicitly on the "do not touch" list, and
  the task brief's own framing ("based on what's really queryable from
  both sides — don't invent checks against data that isn't actually
  available") reads as a deliberate instruction to work within the
  existing public API rather than extend it.
- **A separate shadow-tracking wrapper** decorating `OrderPipeline` to
  record ids without touching `TradingLoop`. Rejected: `OrderPipeline` is
  also on the "do not touch" list, and more fundamentally, a shadow
  registry built independently of `OrderStore`'s own bookkeeping would
  reintroduce exactly the "two independent, driftable copies of the same
  fact" problem this feature exists to catch — if the shadow registry
  itself had a bug, the reconciliation check would be comparing itself to
  itself, not to `OrderStore`.

The submission recorded is the **attempt**, not the outcome — the id is
appended *before* `submitToBroker` is called, deliberately, so that a
duplicate whose second `PaperBroker.submit` call fails (see below) is
still recorded twice and still catchable.

The list is unbounded and never pruned. Disclosed as an intentional non-
issue for this project's actual current usage (a daily-cadence strategy
producing at most a handful of entries per day over a 30-45 day paper-
trading run), not a design meant to support a much higher-frequency
signal source without revisiting.

## Where the check runs

`Reconciler.check(...)` itself is a pure, static, side-effect-free-except-
logging function — no `PaperTradingApp`/`TradingLoop`/`KillSwitch`
dependency, callable directly (as `ReconcilerTest` does) with any
`OrderStore`/`PaperBroker` pair.

`PaperTradingApp.reconcile()` is the real wiring point: it calls
`Reconciler.check(tradingLoop.submittedOrderIds(), orderStore,
paperBroker)`, records the result in a new `lastReconciliationReport`
field, and — on a real mismatch — trips the kill switch (see below).
`PaperTradingApp.runTick()` (driven by the existing `Scheduled
ExecutorService`, per Task C) calls `reconcile()` unconditionally after
every `tradingLoop.tick()`, **regardless of whether that tick itself
succeeded**. This was a deliberate choice: a tick failure and an internal-
bookkeeping inconsistency are orthogonal signals, and running the check
even after a failed tick can reveal exactly *why* it failed (the
duplicate-submission scenario below is a direct example — the tick itself
fails with a `PaperBroker` exception, and the very next reconciliation
pass independently flags the same id as a duplicate).

`reconcile()` is also `public`, so it can be called directly — by a test
(as this task's own tests do), or later by Task D's daily report, which
can either read `lastReconciliationReport()` (side-effect-free) or call
`reconcile()` itself to force a fresh check. This satisfies the task
brief's "expose a clean, simple, reusable check result type/method Task D
could call later" without assuming or depending on Task D's own code.

**Why periodic-inside-the-scheduler rather than only on-demand**: the
task's own design question offered both options. Running it every tick
(every 5 minutes, per Task C's default) is cheap (a handful of map
lookups, no I/O) and maximizes how quickly a real bug gets caught and
halts further automated action, rather than waiting for a human or a
once-daily report to notice. On-demand (`reconcile()` itself) was kept
too, specifically so nothing about this design blocks or conflicts with
Task D calling it independently.

## The kill-switch decision

**`PaperTradingApp.reconcile()` trips `KillSwitch` on any detected
mismatch.** Reasoning:

- CLAUDE.md's Paper Trading Pass Criteria list "zero duplicate orders,
  zero position mismatches" as **hard gates**, not soft warnings. A real
  detected mismatch during paper trading is exactly the kind of signal
  that should halt further automated action for human review, not
  silently continue operating on top of a bookkeeping system that has
  already shown drift.
- Tripping the switch is a safe, non-destructive action here: per
  `KillSwitch`'s own Javadoc, it only stops `TradingLoop` from
  **generating new signals** — it does not stop already-pending orders
  from continuing to reconcile against price updates. So tripping it on
  a mismatch cannot itself cause a stranded position or a lost fill; it
  only pauses new order submission until a human investigates and calls
  `KillSwitch.reset()`.
- The task brief explicitly suggested leaning conservative when unsure,
  and named this exact behavior ("a real, defensible safety behavior for
  a genuine internal-consistency violation") as the preferred direction.

**Design choice: the trip decision lives in `PaperTradingApp.reconcile()`,
not inside `Reconciler.check()` itself.** `Reconciler` doesn't take a
`KillSwitch` parameter at all. This keeps the pure comparison logic
(mechanism) separate from the policy decision of what to do about a
finding (tripping the switch) — `Reconciler.check()` stays a stateless,
side-effect-free (except logging) function usable by a future caller
(e.g. Task D's report, or a one-off diagnostic script) that might want to
inspect a `ReconciliationReport` without triggering a real kill-switch
trip as a side effect of merely reading a status.

## Real local run: actual output

The regular, committed test suite already produces real output for both
scenarios (not a throwaway harness — these are permanent tests, matching
`PaperTradingAppTest`'s own established "small integration test, real
components" style). Ran via `./gradlew :runtime:test --tests
"PaperTradingAppTest.reconcileReportsCleanAfterANormalFillAndDoesNotTrip
TheKillSwitch" --tests
"PaperTradingAppTest.reconcileDetectsARealOrphanedOrderAndTripsTheKillSwitch"
--info` to surface the real SLF4J log lines (suppressed by Gradle's
default test-logging config for a passing test, same reason Task C's own
shakedown run used `--info`/a throwaway harness to capture output).

**Clean scenario** (`reconcileReportsCleanAfterANormalFillAndDoesNotTrip
TheKillSwitch` — a real `GUARDED_MARKET` intent flows through a real
`RiskGateway`/`OrderPipeline`/`PaperBroker`, fills immediately):

```text
[Test worker] INFO engine.runtime.PaperTradingApp - PaperTradingApp constructed: symbol=BTC-USDT bingxBaseUrl=http://127.0.0.1:38035 signalPath=/tmp/junit-1993478649687568625/latest.json tickIntervalSeconds=60 riskTier=canary
```

(No `ERROR`/mismatch line — the clean-state branch logs at `DEBUG`,
which `slf4j-simple`'s default threshold doesn't print; `report.isClean()`
was asserted `true` directly, and `app.killSwitch().isTripped()` asserted
`false`.)

**Caught-mismatch scenario**
(`reconcileDetectsARealOrphanedOrderAndTripsTheKillSwitch` — a real
`Order` is registered in the app's own `OrderStore` via a real second
`OrderPipeline`/`RiskGateway.evaluate()` call, deliberately never handed
to `PaperBroker`, with `TradingLoop`'s own tracked-submission history
seeded via reflection to simulate what a real `tick()` would have
recorded — see "Judgment calls" below for why reflection was used here):

```text
[Test worker] INFO engine.runtime.PaperTradingApp - PaperTradingApp constructed: symbol=BTC-USDT bingxBaseUrl=http://127.0.0.1:38243 signalPath=/tmp/junit-1351641301828871005/latest.json tickIntervalSeconds=60 riskTier=canary
[Test worker] ERROR engine.runtime.Reconciler - internal consistency mismatch detected: type=ORPHANED_IN_BROKER orderId=d4bd5597-dc03-4a0c-934d-39a9a70bc6b6 detail=OrderStore has this order in state NEW (still open), but PaperBroker has no pending record of it -- it will never receive a fill or cancel confirmation
[Test worker] ERROR engine.runtime.PaperTradingApp - internal consistency check found 1 mismatch(es); tripping kill switch -- see the individual mismatch log line(s) above for detail
```

Both tests passed (`BUILD SUCCESSFUL`); the second's assertions confirm,
beyond the log lines above, that `report.mismatches()` contains exactly
one `ORPHANED_IN_BROKER` entry for the manufactured order's id, and that
`app.killSwitch().isTripped()` is `true` afterward.

`TradingLoopTest.submittedOrderIdsRecordsARepeatWhenTheSameIntentIsSubmitted
OnTwoSeparateTicks` separately demonstrates the `DUPLICATE_SUBMISSION_
ATTEMPT` scenario end-to-end through real ticks (not reflection): a
permissive lambda `SignalSource` returns the same `OrderIntent` on two
consecutive ticks (standing in for "if `FileSignalSource`'s own
id-based dedup ever broke") — the first tick submits successfully, the
second tick's `PaperBroker.submit` throws on its own `seenClientOrderIds`
guard (caught by `tick()`'s catch-all, recorded as `lastError`), and
`TradingLoop.submittedOrderIds()` correctly ends up with the same id
twice — the exact signature `Reconciler`'s `DUPLICATE_SUBMISSION_ATTEMPT`
check is built to catch.

## Judgment calls

1. **Extending `TradingLoop` with submission tracking**, discussed above
   under "Where the known submitted order ids come from" — the one
   necessary touch to a class outside the checker itself, deliberately
   minimal (one field, one line, one accessor) and not on this task's
   "do not touch" list.
2. **Extending `PaperTradingApp` with retained `orderStore`/`paperBroker`/
   `killSwitch` fields.** Task C's original constructor kept these as
   local variables (only `tradingLoop` was retained as a field) — Task E
   needs direct access to wire `reconcile()`. `PaperTradingApp` is not on
   the "do not touch" list either, and this is purely additive (no
   existing field, method, or constructor signature changed).
3. **Two new package-private test-only accessors on `PaperTradingApp`**
   (`orderStore()`, `killSwitch()`), matching the existing precedent
   `tradingLoop()` already set in Task C for exactly this purpose.
4. **Reflection to seed `TradingLoop`'s `submittedOrderIds` field directly
   in one test** (`reconcileDetectsARealOrphanedOrderAndTripsTheKillSwitch`).
   An orphan can't currently be produced by driving `PaperTradingApp`
   through its real, normal signal-file path — `FileSignalSource`'s own
   dedup and `PaperBroker`'s own duplicate-id guard both make the
   underlying failure mode (`PaperBroker.submit` throwing *after*
   `OrderStore` registration) effectively unreachable through today's
   actual code paths. The task's own instructions explicitly anticipated
   this ("if practical, also manufacture a real inconsistent scenario
   (e.g. by directly manipulating `OrderStore` state in a test)"), and
   `TradingLoopTest` already has an established precedent for the exact
   same technique (`tickSkipsSignalSubmissionWhenEquityIsDepletedButStill
   ReconcilesAnExistingPendingOrder` reflects into `TradingLoop`'s private
   `equity` field for an equally hard-to-reach-through-legitimate-use
   edge case). Everything else in that test is real: a real `OrderStore`,
   a real `RiskGateway.evaluate()` call via a real second `OrderPipeline`,
   a real registered `Order` — reflection is used only to seed the one
   piece of bookkeeping (`TradingLoop`'s own submission history) that a
   full `tick()` call would otherwise have needed to populate, which
   would have required defeating `FileSignalSource`'s dedup or
   `PaperBroker`'s duplicate guard by some other, more contrived means
   anyway.
5. **No new `build.gradle.kts` change was needed** — `:runtime` already
   depends on everything used here (`:oms`, `:risk`, `:schemas`,
   `:execution`).

## Verification

- `./gradlew :runtime:compileTestJava` against `ReconcilerTest` failed
  with 25 real "cannot find symbol" errors (`Reconciler`,
  `ReconciliationReport`, `ReconciliationMismatch`,
  `ReconciliationMismatchType` all not yet existing) before any
  production code was written.
- One real, non-vacuous test failure during development: an initial draft
  of `ReconcilerTest` used a LIMIT price of 70000 against a 60000
  reference price intending "unmarketable" — backwards for a LONG LIMIT
  order (marketable when `currentPrice <= limitPrice`, so 60000 <= 70000
  is actually marketable). Caught by
  `aPendingBrokerOrderNeverRecordedAsSubmittedIsReportedAsUntracked`
  failing for a real reason (`report.isClean()` was `true`, expected
  `false`, because the order filled immediately instead of staying
  pending) — fixed by using 40000 (genuinely below the 60000 reference
  price) for every "should stay pending" fixture in that file.
- `./gradlew :runtime:compileTestJava` against the two new
  `TradingLoopTest` additions and the three new `PaperTradingAppTest`
  additions each failed first with real "cannot find symbol" errors
  (`submittedOrderIds()`, `orderStore()`, `killSwitch()`, `reconcile()`,
  `lastReconciliationReport()`) before the corresponding production code
  existed.
- `./gradlew :runtime:test --tests ReconcilerTest` — 8/8 pass.
- `./gradlew :runtime:test --tests TradingLoopTest` — 10/10 pass (8
  pre-existing + 2 new).
- `./gradlew :runtime:test --tests PaperTradingAppTest` — 16/16 pass (13
  pre-existing + 3 new).
- `./gradlew clean test` (full multi-module suite): **200 tests, 0
  failures, 0 errors** across all six `java/` modules — up from 187 after
  Task C (187 + 8 `ReconcilerTest` + 2 `TradingLoopTest` + 3
  `PaperTradingAppTest` = 200).
- Real local run — see "Real local run: actual output" above, captured
  from the permanent, committed test suite itself (not a throwaway
  harness) via `--info`.
- PR opened, not merged — per the governing plan and CLAUDE.md's
  Auto-merge Policy, this is Java runtime code (adds new OMS/execution-
  adjacent bookkeeping-comparison logic and a kill-switch-tripping code
  path) and requires explicit human sign-off regardless of CI/CodeRabbit
  status.
