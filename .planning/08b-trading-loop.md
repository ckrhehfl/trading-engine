# Implementation Priority #8, Task B: the paper trading runtime loop

## Scope note

This is **Task B** of Implementation Priority #8. Task A
(`engine.runtime.OrderPipeline`, PR #24, see `.planning/08-order-pipeline.md`)
built the one real `OrderIntent → RiskGateway.evaluate() → Order` pipeline
in the codebase; this task builds the actual running loop that drives it
against `PaperBroker` on a polled price feed. Paper-only throughout — no
`BingXAdapter`/live wiring anywhere in this task, see "Deliberately out of
scope" below.

## What was built

Four new classes, all in the existing `java/runtime` module (package
`engine.runtime`), plus a `build.gradle.kts` update adding
`implementation(project(":execution"))` (for `PaperBroker`) and
`implementation(project(":exchange"))` (for `ExchangeException` only —
not `BingXAdapter`/`BingXSigner`) and
`implementation("com.fasterxml.jackson.core:jackson-databind:2.18.9")`
(for parsing the price-poll response — 2.18.9, not the 2.18.2 used
elsewhere in this repo; see "CodeRabbit review findings" below for why
this module specifically was bumped past a CVE fixed in 2.18.9).

- **`DummySignalSource`** — test/demo scaffolding, explicitly not a
  strategy (see "Why `DummySignalSource` is not strategy research"
  below). Constructor: `(String symbol, Side side, OrderType orderType,
  BigDecimal quantity, BigDecimal limitPrice, int everyNthCall)`. One
  method, `Optional<OrderIntent> nextSignal()`, returning a fresh
  `OrderIntent` (new random `intentId` every time — each firing is a new
  signal, not a retry) on every `everyNthCall`-th call (counting from 1),
  `Optional.empty()` otherwise.
- **`BingXPriceFeed`** — polls BingX's public, unauthenticated recent-trades
  endpoint (`GET /openApi/swap/v2/quote/trades`) for the latest traded
  price. Constructor takes `baseUrl` as a plain string, matching
  `BingXAdapter`'s "caller decides the host" pattern. One method,
  `BigDecimal latestPrice(String symbol)`. Throws
  `engine.exchange.ExchangeException` (reused, not a parallel type) on any
  non-2xx response, I/O failure, malformed/empty body, non-zero `code`, or
  an empty/missing trades array.
- **`KillSwitch`** — wraps a single `AtomicBoolean`. `trip()`, `reset()`,
  `isTripped()`. In-process only — see "Deliberately out of scope".
- **`TradingLoop`** — the actual loop. Constructor:
  `(OrderPipeline, PaperBroker, DummySignalSource, BingXPriceFeed,
  KillSwitch, String symbol)`. One method, `tick()`, meant to be called
  repeatedly by an external scheduler (not built here — see below) but
  directly, deterministically callable from tests. Also exposes
  `lastTickAt()` (`Instant`), `lastError()` (`Throwable`, nullable), and
  `currentEquity()` (`BigDecimal`) as queryable state.

## `tick()`'s five steps, and why they're ordered this way

1. Check `killSwitch.isTripped()`. If the tripped state changed since the
   last tick, log it once (not every tick — avoids log spam on a loop that
   might run for weeks). Tripped state alone does **not** yet skip
   anything — it's read once here and used to gate step 3, but step 2
   below runs unconditionally.
2. Fetch `priceFeed.latestPrice(symbol)` and call
   `paperBroker.onPriceUpdate(symbol, price)` — **regardless** of whether
   a new signal fires this tick, and regardless of kill-switch state. This
   is deliberate: a paper position must not get stuck forever just because
   the switch tripped or because no new signal happened to fire this tick.
   Any `Fill`s returned feed into equity tracking (see below).
3. Only if not tripped: ask `signalSource.nextSignal()`. If present, build
   an `AccountState` from the loop's own running equity (see below), call
   `orderPipeline.submitIntent(intent, price, account)`, and if that
   returns a present `Order`, call `paperBroker.submit(order, price)` —
   any resulting `Fill` also feeds equity tracking.
4. The whole body of steps 1–3 is wrapped in a single `try/catch
   (Exception e)` inside `tick()` itself. A caught exception is logged and
   stored in `lastError`; it never propagates to whatever is calling
   `tick()` on a schedule. This is the actual substance of "24/7
   supervision" at this stage — see "Deliberately out of scope" for what
   it deliberately is *not*.
5. `lastTickAt` is updated in a `finally` block (so it advances even on a
   failed tick — a caller polling for liveness needs to know the loop is
   still alive and ticking, not just that it's succeeding). Both
   `lastTickAt` and `lastError` are plain `volatile` fields with plain
   (non-synchronized) getters, since a health-check caller may read them
   from a different thread than the one driving `tick()`.

`tick()` itself is `synchronized` (a single method, no cross-lock
deadlock risk) so overlapping calls from two threads can't interleave —
not exercised by any test here (all tests call `tick()` from one thread),
but cheap insurance given a real scheduler could plausibly double-invoke
under some misconfiguration.

## Equity tracking: what it is and, more importantly, what it deliberately is not

`RiskGateway.evaluate()` needs a real `AccountState` — Task A's tests
always hand-built one as a fixture; `TradingLoop` is the first real caller
that has to produce one from something other than a test constant. The
brief was explicit that this "doesn't need to be a full PnL engine, just
enough for `RiskGateway.evaluate()` to have something real to evaluate
against instead of a hardcoded constant every tick" — so:

- `equity` starts at a fixed `INITIAL_EQUITY` (100,000, an internal
  constant — not a constructor parameter, since the constructor's
  parameter list was specified exactly in the task brief and equity
  wasn't part of it).
- On every `Fill` — from a fresh `submit()` or from a price-driven
  `onPriceUpdate()` reconciliation — `equity` is adjusted **down by the
  fee only**. Fee is a real, realized cost regardless of whether the
  resulting position is still open; nothing else about a fill is
  realized PnL in any sense this class tracks.
- `AccountState`'s daily/weekly/monthly PnL percent fields are all set to
  the *same* single number: `(equity - INITIAL_EQUITY) / INITIAL_EQUITY`.
  There is no calendar-day/week/month bucketing here at all.

This is a real simplification, not an oversight — flagged explicitly here
the same way `RiskGateway`'s own Javadoc flags its monthly/hard-stop/
emergency-stop simplification. There is no unrealized PnL, no
mark-to-market against current price, no per-position tracking, and no
actual PnL calendar. It exists purely so the risk checks have a real,
moving input rather than a constant; a genuine PnL engine (position
tracking, realized/unrealized split, calendar-correct daily/weekly/monthly
windows) is out of scope for this task and not currently scheduled against
any specific priority number.

## Why `DummySignalSource` is not strategy research

CLAUDE.md's "Strategy Research Methodology" section explicitly carves out
Priorities #6–#8: "paper broker, `ExchangeAdapter`, supervision loop
skeletons ... can and should be built and tested with dummy/mock signals
independently of a validated strategy." `DummySignalSource` is exactly
that dummy signal — a fixed, deterministic shape (same
symbol/side/type/quantity/limit-price every time it fires, on a simple
every-Nth-call schedule) with a fresh random `intentId` per firing. Its
Javadoc says this explicitly and points back at that CLAUDE.md section, so
a future reader who finds this class doesn't mistake it for a real,
validated signal. None of the walk-forward-validation / holdout-data /
look-ahead-bias rules in that section apply to it, because it is not
strategy research — it never claims to have found an edge, it exists only
to exercise the pipeline end-to-end.

## Verifying the BingX recent-trades response shape

This endpoint (`GET /openApi/swap/v2/quote/trades`) had never been called
from Java before this task, and CLAUDE.md's "Exchange API Facts" only
names the path, not the field shape. Rather than guess from the endpoint
name alone, the real public endpoint was called directly
(`curl https://open-api.bingx.com/openApi/swap/v2/quote/trades?symbol=BTC-USDT`,
2026-07-25) and the actual response inspected:

```json
{"code":0,"msg":"","data":[
  {"time":1784961606475,"isBuyerMaker":false,"price":"63983.0","qty":"0.0003","quoteQty":"19.19","fillId":"698643300","ts":1784961606475},
  ...
]}
```

Two things confirmed empirically that `BingXPriceFeed`'s implementation
and tests both depend on:

- The envelope matches the rest of BingX's API
  (`{"code","msg","data"}`) — consistent with every other endpoint
  already documented in CLAUDE.md.
- `data` is ordered **oldest-first / newest-last**: `time` (and the
  separate `fillId`) strictly increase across the array. A request with no
  `limit` param returned exactly 1000 entries — consistent with CLAUDE.md's
  existing note that `limit` isn't a reliable count guarantee and requests
  are silently capped. This means the latest trade — the one
  `BingXPriceFeed.latestPrice` needs — is the **last** element, not the
  first; `BingXPriceFeedTest.latestPriceReturnsTheMostRecentTradesPriceNotTheFirstElement`
  exists specifically to catch a wrong-index regression on this point,
  since it's the one assumption here most worth re-verifying if BingX's
  behavior is ever in doubt.

This verification call was made directly against the live production host
for research purposes only (a one-off, read-only, unauthenticated `curl`
run outside the codebase) — the resulting field-shape knowledge is
encoded in `BingXPriceFeed`'s Javadoc and tests, but the production
hostname string itself is never written into `java/` or `python/` source,
consistent with `.github/workflows/bingx-hostname-guard.yml`.

## TDD

Tests were written first for every new class and confirmed to fail to
*compile* (51 errors — the classes referenced didn't exist yet) before any
production code was written; minimum code was then added to make
everything pass on the first attempt, no red→green→red cycles needed
beyond that initial compile-fail state.

23 new tests:

- `KillSwitchTest` (4) — starts not tripped, `trip()`/`reset()` toggle
  state, `trip()` is idempotent.
- `DummySignalSourceTest` (6) — fires on every call when `everyNthCall=1`;
  stays empty except on the Nth call otherwise; emitted intent matches the
  configured shape; each firing gets a distinct `intentId`; constructor
  rejects a non-positive `everyNthCall`; constructor rejects a
  LIMIT/no-limit-price combination (delegated to, and proven to actually
  delegate to, `OrderIntent`'s own validation — see "Judgment calls"
  below).
- `BingXPriceFeedTest` (9), against a real local HTTP server
  (`com.sun.net.httpserver.HttpServer`, JDK built-in, not a mock — same
  technique as `BingXAdapterTest`, shared here via a new
  `FakeBingXTradesServer` test helper used by both this class and
  `TradingLoopTest`): correct latest-vs-first price extraction, correct
  path/query param, and `ExchangeException` on non-2xx status, empty body,
  malformed JSON, empty `data` array, non-zero `code`, missing `code`
  field, and a trade missing its `price` field.
- `TradingLoopTest` (4) — real `RiskGateway`/`OrderStore`/`OrderPipeline`/
  `PaperBroker` instances wired together throughout (this codebase has no
  mocking framework, so these are closer to small integration tests than
  narrow unit tests, matching `BingXAdapterTest`'s and `OrderPipelineTest`'s
  existing style):
  - a signal-present tick submits a real order through the pipeline,
    observable via `PaperBroker.pendingOrders()` (a LIMIT order
    deliberately not marketable at the test's price, so its presence in
    `pendingOrders()` after `tick()` is direct proof `OrderPipeline.
    submitIntent` actually ran, not just "the tick didn't throw");
  - a no-signal tick (via a large `everyNthCall`) still reconciles an
    already-pending order (seeded directly through `OrderPipeline`/
    `PaperBroker` before the loop is constructed, simulating a restart)
    against the price update, while proving no spurious new order was
    also created (the dummy signal's own limit price is chosen
    deliberately unmarketable at the reconciling price, so if it had
    erroneously fired it would remain visibly pending instead of being
    indistinguishable from "did nothing");
  - a `BingXPriceFeed` pointed at a fake server returning HTTP 500 causes
    `tick()` to catch the exception, record it in `lastError`, and *not*
    propagate — a subsequent `tick()` against a fixed response succeeds
    and clears `lastError`;
  - tripping the kill switch mid-run blocks a subsequent identical signal
    from creating a second pending order (proven via an exact
    `pendingOrders().size()` count, not just presence/absence — see
    inline test comments for why value equality between the blocked
    signal and the real one made a naive presence check insufficient),
    while a price update in the same tripped state still fills the order
    that was already pending from before the trip.

Full suite (`./gradlew clean test`): **164 tests**, up from 140 before
this task, all green (23 from the initial TDD pass, plus one more —
`tickSkipsSignalSubmissionWhenEquityIsDepletedButStillReconcilesAnExistingPendingOrder`
— added during CodeRabbit review, see below).

## Judgment calls resolved without asking

- **`DummySignalSource` validates eagerly, in its constructor, by
  building (and discarding) one throwaway `OrderIntent`** rather than
  duplicating `OrderIntent`'s own LIMIT/limitPrice consistency check.
  This means a misconfigured `DummySignalSource` (e.g. `LIMIT` with a
  null `limitPrice`) fails fast at construction rather than only on
  whichever future `nextSignal()` call happens to be a firing one —
  and there's exactly one place `OrderIntent`'s validation rule lives,
  not two.
- **`BingXPriceFeed` re-implements its own `requireCode` shape-check**
  (has/isInt on the `code` field, same defensive reasoning as
  `BingXAdapter`'s private method of the same name) rather than sharing
  code with `BingXAdapter` — this class deliberately does not depend on
  `BingXAdapter`/`BingXSigner` at all (see task brief: those are for
  authenticated write endpoints, this is public and unauthenticated), so
  there's no shared class to call into without creating that dependency
  just for one helper method.
- **A new shared test helper, `FakeBingXTradesServer`**, was added in
  `java/runtime/src/test` rather than duplicating the fake-server
  boilerplate in both `BingXPriceFeedTest` and `TradingLoopTest`
  separately (the way `BingXAdapterTest`'s equivalent stays private to
  that one file, since nothing else in `java/exchange` needed it yet).
  Package-private, test-scope only.
- **The kill-switch test's "no spurious order" proof** needed more care
  than an initial "check `pendingOrders()` is empty afterward" design:
  since `DummySignalSource` emits an identical shape every time it fires,
  a genuine pre-trip pending order and a hypothetical erroneous post-trip
  one would use the *same* limit price and therefore become marketable
  (or not) together — making "did it fill" and "did suppression work"
  indistinguishable by price-driven fills alone. Resolved by checking the
  exact `pendingOrders().size()` immediately after the tripped tick
  (before the reconciling price arrives), while price is still held at a
  level unmarketable for that shape — a spurious second order would be
  countable there even though it can't be distinguished later.
- **Equity floor**: the initial version left `equity` unguarded against
  being driven to zero or below by cumulative fees. CodeRabbit review
  flagged this (see "CodeRabbit review findings" below) and it was fixed
  in this PR — see that section for what changed and why.

## CodeRabbit review findings

Three actionable findings on the first review pass (one round-trip; a
rate-limit wait of ~18 minutes was hit and handled per CLAUDE.md's
rate-limit procedure — queried `@coderabbitai rate limit` for the exact
ETA rather than blind-retrying, waited it out, retried once).

**Fixed, both in this PR:**

- **jackson-databind 2.18.2 is in the CVE-2026-54515 range** (case-
  insensitive deserialization can restore properties `@JsonIgnoreProperties`
  should have excluded — fixed in 2.18.9). Verified two things before
  deciding how to respond, rather than accepting or dismissing on the
  finding's text alone: (1) a repo-wide grep for the actual vulnerable
  pattern (`ACCEPT_CASE_INSENSITIVE_PROPERTIES` combined with
  `@JsonIgnoreProperties`) returns zero hits anywhere in this codebase —
  the vulnerability's specific trigger condition isn't exercised today;
  (2) `BingXPriceFeed` itself only ever calls `readTree()` (untyped tree
  walking), never `readValue()` (typed POJO deserialization) — the one
  place `readValue()` *is* used is `schemas`/`risk` test code, parsing
  this project's own trusted fixtures, not untrusted network input.
  Bumped anyway, in `java/runtime/build.gradle.kts` only: this module is
  the one place in the repo parsing untrusted external (BingX) JSON over
  the network, so patching costs nothing even though the specific
  exploit path isn't reachable. Deliberately **not** bumped in
  `schemas`/`risk`/`exchange` (`java/exchange` in particular was
  explicitly out of bounds for this task) — those three still declare
  `2.18.2`, so this is a scoped fix, not a repo-wide one; confirmed via
  `./gradlew :runtime:dependencies` and `./gradlew :exchange:dependencies`
  that Gradle's normal highest-version-wins conflict resolution upgrades
  `:runtime`'s own classpath to `2.18.9` (`2.18.2 -> 2.18.9` in the
  dependency tree) without touching `:exchange`'s independently-resolved
  classpath at all (`:exchange` still resolves `2.18.2` on its own, as
  before). A repo-wide version bump (all four `build.gradle.kts` files,
  ideally via a shared version — a Gradle version catalog or platform BOM
  import — rather than four independent literals) is a reasonable
  follow-up, just a separate, dedicated PR rather than folded into this
  task's diff.
- **No floor on `equity`.** Confirmed by tracing the actual failure mode:
  `AccountState`'s constructor rejects non-positive equity with
  `IllegalArgumentException`; without a guard, a depleted account would
  hit that on every future signal-firing `tick()`, forever, indistinguishable
  in the logs from a real, transient failure (both just show up as
  `lastError` via the catch-all) rather than the expected, permanent-until-
  restart condition it actually is. Fixed by skipping signal submission
  (not price-update reconciliation — that still runs) whenever
  `equity.signum() <= 0`, logging once (not every tick, matching the
  kill-switch trip-state log's existing pattern) rather than on every
  subsequent tick. Regression test
  (`tickSkipsSignalSubmissionWhenEquityIsDepletedButStillReconcilesAnExistingPendingOrder`)
  had to force the private `equity` field directly via reflection rather
  than reach the condition through the real order flow — traced through
  why first, rather than assumed: `RiskGateway`'s own per-order notional
  clamp is a fraction of *current* equity, recomputed every tick, so
  repeated legitimate fills shrink equity asymptotically toward zero
  without a finite number of ticks ever actually reaching it (or below).

**Declined in this PR, with reasoning, tracked as an open item:**

- **`OrderStore` orphan on a `PaperBroker.submit` failure.** The finding:
  `orderPipeline.submitIntent()` already registers the `Order` in
  `OrderStore` (that registration happens atomically with
  `RiskGateway.evaluate()` — the entire point of `OrderPipeline`, see
  `.planning/08-order-pipeline.md`) before `paperBroker.submit()` is ever
  called; if the broker call throws, the `Order` is left registered in
  `OrderStore` but never reaches `PaperBroker.pendingOrders()`, so it will
  never receive a fill. This is a real gap, confirmed by tracing the
  actual code paths (not just accepted on description) — but the two
  structural fixes CodeRabbit's own suggestion names (roll back the
  stored order, or defer persistence until broker submission succeeds)
  both require changing `OrderStore`'s or `OrderPipeline`'s public
  contract: `OrderStore` has no removal/rollback method today, and
  deferring persistence would mean splitting `OrderPipeline`'s
  evaluate-and-register step apart — the exact atomicity Task A built
  specifically to close the provenance gap named repeatedly in Priority
  #7's review. Redesigning that now, under review pressure on an
  unrelated PR, is exactly what CLAUDE.md's Development Methodology
  warns against for R3-risk components (OMS/Risk Gateway/Execution):
  `Discuss` "must not be skipped," and the Auto-merge Policy reserves
  Java OMS/Risk Gateway/Execution logic changes for explicit human
  sign-off regardless of checks passing. This mirrors the exact
  resolution already used for the closely related (not identical)
  `OrderStore.createOrder` visibility gap tracked in
  `.planning/08-order-pipeline.md` and CLAUDE.md's Priority #8/#10
  entries — deferred there with @ckrhehfl's direct sign-off, not silently
  dropped.

  What *was* done, in scope, in this PR: `TradingLoop.submitToBroker`
  (new private helper, extracted from `tick()`'s body) catches a
  `PaperBroker.submit` failure, logs the orphaned order's `clientOrderId`
  explicitly so the condition is diagnosable from logs instead of silent,
  and rethrows so it still surfaces through `tick()`'s existing
  `lastError` path — no behavior change to `OrderStore`/`OrderPipeline`,
  just better diagnostics for an already-existing gap.

  Current blast radius, traced rather than assumed: `PaperBroker.submit`
  only throws for a non-positive price (`TradingLoop` always passes the
  same price it just used successfully for `onPriceUpdate()` moments
  earlier, so this needs `BingXPriceFeed` to return a non-positive
  decimal — a separate, prior data-quality concern, not something this
  code path introduces) or a duplicate `clientOrderId` (`DummySignalSource`
  mints a fresh random UUID per firing, so this is unreachable through
  this task's actual signal source). Real, but not reachable through
  today's normal operation — worth tracking for whenever a future,
  non-dummy signal source or `OrderStore`/`OrderPipeline` hardening pass
  revisits this area, not urgent enough to block this PR on a redesign of
  already-reviewed R3-risk code.

## Deliberately out of scope

- **Live `BingXAdapter` wiring.** `TradingLoop` only ever talks to
  `PaperBroker`. No caller anywhere in this task constructs a
  `BingXAdapter` or reads a live/paper flag from the environment — that
  remains true after this PR, same as after Priority #7's.
- **WebSocket market data.** `BingXPriceFeed` polls
  `GET /openApi/swap/v2/quote/trades` on a schedule the caller drives; the
  private/public WebSocket streams named in CLAUDE.md's research notes
  stay deferred, as already noted in `.planning/07-exchange-adapter.md`.
- **A full HTTP health endpoint.** `lastTickAt()`/`lastError()`/
  `currentEquity()` are plain queryable Java state, not served over HTTP
  anywhere. CLAUDE.md's Future Tooling Watchlist already names
  monitoring/alerting as a Priority #8 revisit point generally — this
  task only builds the in-process surface a future health endpoint would
  read from, not the endpoint itself.
- **OS-level process supervision (systemd or equivalent).** `tick()`'s
  own internal exception handling is the entire "supervision" story at
  this stage — restarting a crashed *process* is a deployment concern for
  a later priority, not this task's job. Nothing here assumes or requires
  any particular process manager.
- **Persisted/durable `OrderStore` or `PaperBroker` state.** Both stay
  in-memory, exactly as built in earlier priorities. `TradingLoop`
  explicitly does not cache anything from before its own construction —
  it reads `PaperBroker.pendingOrders()` fresh via `onPriceUpdate` every
  tick — which is the right behavior for an in-memory paper loop and
  deliberately keeps the door open for a future live loop to instead
  reconcile from `ExchangeAdapter.getPositions()`/`getBalance()` as
  ground truth (see "Equity tracking" above and inline class Javadoc).
- **A real trading strategy.** `DummySignalSource` is explicitly not
  one — see "Why `DummySignalSource` is not strategy research" above.
- **Scheduling.** `TradingLoop.tick()` is designed to be driven by an
  external `ScheduledExecutorService` (or equivalent), but no such
  scheduler is wired up in this task — every test calls `tick()`
  directly. Wiring an actual always-on scheduling loop (and whatever
  entrypoint/`main()` would run it) is left for whichever later step
  turns this into something that actually runs unattended.
- **The `OrderStore.createOrder` bypass gap** tracked in
  `.planning/08-order-pipeline.md` and CLAUDE.md's Implementation
  Priority #8/#10 entries — unchanged by this task, still explicitly
  deferred to before Priority #10's live wiring, per @ckrhehfl's prior
  sign-off on that timeline.

## Post-merge verification: real scheduled shakedown run (2026-07-25)

This task's own scope named "no such scheduler is wired up" above and
this PR's own review discussion had planned a follow-up manual run
against a real `ScheduledExecutorService` and real BingX data (not just
`tick()` called directly in fast unit tests) — that run had not actually
happened until this note. Closed the gap: a throwaway JUnit test
(`ManualShakedownRun`, deleted immediately after — not part of the
regular suite) wired `TradingLoop` to a real
`ScheduledExecutorService.scheduleAtFixedRate` (5s interval) against the
real `open-api-vst.bingx.com` public trades endpoint, `DummySignalSource`
firing every 6th tick, and ran for a real 3 wall-clock minutes.

Result: **37 real ticks, 6 real signal firings (ticks 6/12/18/24/30/36,
exactly matching the configured interval), zero exceptions
(`lastError` stayed `null` for the entire run), equity decreasing by
the expected fee amount on each firing** (`100000` →
`99999.61549164190` after 6 fills at 10bps fee on ~0.001 BTC notional
each). Confirms `TradingLoop` genuinely runs continuously under a real
scheduler against live network data, not just in isolated, synchronous
test calls — the actual substance of what "24/7 supervision" needs to
hold up under, beyond what the unit test suite alone can prove.
