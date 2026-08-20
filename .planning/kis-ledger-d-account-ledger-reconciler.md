# KIS Ledger Task D: `AccountLedgerReconciler`

Final task of the 4-task "Shared KIS account risk ledger" plan (see
`tender-finding-matsumoto.md`'s top section, "3. `AccountLedgerReconciler`").
Task A (PR #99) extracted `AccountStateProvider`. Task B (PR #100)
built `AccountLedger`/`LedgerReservation`/`AccountLedgerStore`/
`AccountLedgerLock`. Task C (PR #101) built `SharedKisAccountLedger` and
wired it into `TradingLoop`/`PaperTradingApp`. This task adds the periodic
real-account reconciliation the whole design depends on to ever detect
(not just prevent) a real divergence between the shared virtual ledger and
the actual KIS account.

## What was verified before writing any code

- `SharedKisAccountLedger.reserveForIntent` (already merged, Task C)
  really does check `ledger.reconciliationAlarmTrippedAt() != null` on
  every reload and returns a floored-near-zero-equity snapshot without
  creating a reservation when it's set -- confirmed by reading the real
  code (not assumed from the design doc). So this task does not need to
  build any new cross-process signaling: writing the two alarm fields
  onto the shared `AccountLedger` file, under the same lock, is
  sufficient for every other process sharing that file to observe it on
  their own next `reserveForIntent`/`confirmReservation`/
  `releaseReservation` call.
- `AccountLedger` already carries nullable `reconciliationAlarmTrippedAt`/
  `reconciliationAlarmReason` (Task B), with a compact-constructor
  invariant that the two are only ever both-null or both-non-null.
- `KillSwitch.trip()`'s own Javadoc already states it "reaches no other
  process" -- confirming the plan's reasoning for why account-wide
  propagation happens through the shared ledger file, not a second
  cross-process mechanism.
- `PositionSnapshot` (in `engine.exchange`) has `positionAmt` and
  `avgPrice` fields, both `BigDecimal`, both potentially `null` in
  practice (`KisPreflight.run` already guards `positionAmt() != null`
  before checking its sign) -- so exposure computation here has to
  tolerate a null field defensively, not just multiply blindly.
- `FakeExchangeAdapter` (`java/runtime/src/test/java/engine/runtime/`)
  already scripts `getBalance()`/`getPositions()` via
  `willReturnPositions`/`willFailPositionsWith` -- reused as-is, no new
  fake adapter built.

## Design decisions specific to this task

**Exposure formula.** Ledger side: `Σ reservations[].notional]` (already
what Task C's `reserveForIntent` populates -- `quantity × price`, still
contract-multiplier-unaware, unchanged here). Real side:
`Σ |positionAmt| × avgPrice` across every position `adapter.getPositions()`
returns for the account (no symbol filtering -- the plan's own wording,
"across this account's open positions," and the fact that one real KIS
account is shared by several per-symbol `kis-paper` processes, means the
real side has to be the whole account's exposure, not just the calling
process's own symbol).

**Missing/null position data fails closed, not silently to zero.** If any
position returned by `getPositions()` has a null `positionAmt` or
`avgPrice`, the real exposure figure cannot be trusted -- rather than
silently treating that position as contributing zero (which would
understate real exposure and could mask a genuine problem), this is
treated as its own reconciliation failure: the alarm trips immediately
with a reason naming the incomplete data, without ever computing (or
comparing against) a possibly-wrong numeric mismatch. This mirrors the
"missing or stale data is a rejection, never a silent fallback" principle
CLAUDE.md already states for the (separate, still-unbuilt)
contract-multiplier conversion -- applied here because the same failure
shape (silently-zeroed real exposure) would otherwise undermine this
class's entire purpose.

**Threshold**: `|ledgerExposure - realExposure| > 0.10 × allocatedVirtualCapital`
(strict `>`, matching the plan's "exceeding 10%" wording). Already
human-approved per the plan; not re-litigated.

**Never auto-clears.** A clean reconciliation pass never clears a
pre-existing alarm -- `reconciliationAlarmTrippedAt`/`Reason` are only
ever copied forward unchanged from the loaded ledger when this pass finds
no new problem. Matches `SubmissionMarkerResolver`'s own established
"never silently auto-resolve an ambiguous safety state" precedent (per
the governing task brief) -- no clearing mechanism exists anywhere in this
class; a human must edit the ledger file directly.

**`lastReconciledAt` is always updated on a completed pass**, clean or
alarmed -- matches `AccountLedger`'s own Javadoc ("there is no meaningful
'reconciled at' instant before [the first pass]").

**Cadence, two call sites, no scheduler of its own** (per the governing
task brief):
- `runStartupReconciliation()` -- called once from `forKisPaper()`, right
  after `SharedKisAccountLedger.bootstrapOrLoad` and before the ledger is
  handed to `PaperTradingApp`'s constructor. Runs the pass unconditionally
  and seeds this instance's own day-tracking state to "today" so the very
  next `runOnUtcDayBoundary()` call (same day, from the first scheduled
  tick) does not immediately re-run.
- `runOnUtcDayBoundary()` -- called from `PaperTradingApp.runTick()` on
  every tick. Mirrors `DailyReportGenerator.beforeTick()`'s own exact
  day-boundary technique (`LocalDate.ofInstant(clock.instant(),
  ZoneOffset.UTC)`, compared against a locally tracked `currentDay`): the
  very first call ever made (only reachable if a test calls this directly
  without first calling `runStartupReconciliation()`) just seeds tracking
  state without reconciling, matching `DailyReportGenerator`'s own "first
  call just seeds" behavior; a later call where "today" has advanced runs
  the pass. **Deliberately its own separate day-tracking field**, not
  shared with `DailyReportGenerator`'s internal state -- same "two
  separate classes for two separate concerns" pattern already established
  between `DailyReportGenerator` and `PendingDailyReportStore` (per the
  governing task brief).
- **If `reconcileNow()` throws, the tracked day is *not* advanced** -- so
  a transient failure (e.g. `getPositions()` erroring) is retried on
  every subsequent tick until a pass actually completes, rather than
  silently going unreconciled for the rest of that day. This is the
  opposite choice from `DailyReportGenerator`'s own "day tracking
  advances regardless of write success" rule -- deliberately, because an
  unreconciled account-exposure check is a materially higher-stakes gap
  than one day's report being late.
- `runTick()`'s new branch runs **unconditionally**, not gated on
  `tradingCalendar.isOpen()` -- reconciliation is an account-health check,
  independent of whether new-signal processing happened that tick, and
  must not go unattempted indefinitely just because a given day is a
  KRX non-trading day. Wrapped in its own `try`/`catch` (mirrors
  `runTick()`'s existing market-closed-branch treatment of
  `TradingLoop.pollPendingFills()`) so a reconciliation failure can never
  crash the `ScheduledExecutorService`'s periodic task (an uncaught
  exception from a `scheduleAtFixedRate` task silently cancels all future
  executions -- confirmed against the JDK's own documented behavior, not
  assumed) or otherwise stop the loop.

## `PaperTradingApp` wiring judgment call: a new 11-arg constructor + externally-built `KillSwitch`

The requirement "trip this process's own `KillSwitch` immediately" only
means something if `AccountLedgerReconciler` and `TradingLoop` share the
literal same `KillSwitch` instance. Every existing `PaperTradingApp`
constructor builds its own `KillSwitch` internally
(`this.killSwitch = new KillSwitch();`), and the plan requires the
reconciler's startup pass to run *before* the ledger (and therefore
before `TradingLoop`) is constructed -- so the `KillSwitch` has to be
built externally, in `forKisPaper()`, and threaded into both
`AccountLedgerReconciler`'s constructor and a new `PaperTradingApp`
constructor overload.

Rather than modify the existing 9-arg `PriceFeed`+`AccountStateProvider`
constructor (Task C's own, still directly exercised by several
`PaperTradingAppTest` cases), this task adds a new 11-arg overload
(same 9 params + `KillSwitch` + `AccountLedgerReconciler`), matching this
codebase's own established "new overload, not a modified existing one"
precedent (used for every prior extraction: `OrderExecutor`, `PriceFeed`,
`AccountStateProvider` itself). The 9-arg constructor is untouched in
signature and behavior; `forKisPaper()` is updated to call the new 11-arg
one instead.

`PaperTradingApp` gains one new field, `Optional<AccountLedgerReconciler>
accountLedgerReconciler`. Every constructor except the new 11-arg one
sets it to `Optional.empty()` -- a one-line, purely additive change to
each existing constructor body, not a signature or behavior change.
`runTick()`'s new branch is `accountLedgerReconciler.ifPresent(...)`, so
for `simulated`/`bingx-vst` (and the two other KIS-mode constructors that
predate this task) it is a provably inert no-op: there is no code path by
which those constructors could ever populate a non-empty `Optional`.

## Real gap found while implementing: `AccountLedgerReconciler`'s own `defaultAllocatedCapital`

`AccountLedgerStore.load` requires a `defaultAllocatedCapital` argument on
every call (used only to bootstrap a *brand new* ledger, and to fail
closed if a stored value somehow exceeds it). `SharedKisAccountLedger`
exposes no accessor for the value it resolved during its own
`bootstrapOrLoad` call (by design -- Task C's class is off-limits to
modify for this task). `forKisPaper()` already has the one value that is
safe to reuse here: `preflight.balance().balance()`, the exact real
balance passed into `bootstrapOrLoad` itself. Since `bootstrapOrLoad`
already enforces (and, by returning normally, already proved) that the
ledger's real persisted `allocatedVirtualCapital` does not exceed that
balance, reusing the same value for `AccountLedgerReconciler`'s own
`AccountLedgerStore.load` calls is safe by construction -- it can never
trigger that method's "stored value exceeds configured default" fail-closed
check under any real production path.

## Tests

`AccountLedgerReconcilerTest` (new): clean reconciliation (no alarm,
`lastReconciledAt` updated); large mismatch trips this process's
`KillSwitch` + persists both alarm fields; incomplete position data
(null `positionAmt`/`avgPrice`) trips the alarm with its own distinct
reason rather than silently computing a wrong number; a clean pass never
clears a pre-existing alarm; a second, independently-constructed
`SharedKisAccountLedger` pointed at the same file observes the
reconciler-tripped alarm on its own next `reserveForIntent` call and
returns a floored near-zero equity snapshot with no new reservation
(proves account-wide propagation end-to-end, not just that the reconciler
writes the fields); the day-boundary trigger fires exactly once per UTC
day under an injected `Clock` (mirrors `DailyReportGeneratorTest`'s own
`MutableClock` technique); a failed pass does not advance the tracked day
(retried on the next call).

`PaperTradingAppTest` additions: every non-KIS-ledger-reconciler
constructor leaves `accountLedgerReconciler()` `Optional.empty()`; the new
11-arg constructor populates it and `runTick()` actually invokes
`AccountLedgerReconciler` through it across a real day boundary (proves
the gate is reachable, not just present); a reconciliation failure inside
`runTick()` is caught and logged, not propagated (the scheduled loop must
survive it).

## Verification

`./gradlew build` run project-wide before opening the PR -- see the PR
description for the real, captured output and test counts.
