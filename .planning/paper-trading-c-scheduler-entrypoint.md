# Paper-trading bridge, Task C: Java scheduler + `main()` entrypoint

## Scope note

This is **Task C** of the 5-task paper-trading bridge plan governing
`daily-tsmom-ensemble`'s human-approved move to paper trading (see
CLAUDE.md's "Paper Trading Policy Exception" and
`/home/minju/.claude/plans/tender-finding-matsumoto.md`, the governing
plan). Depends on Task A (`SignalSource`/`FileSignalSource`, merged, PR
#68) and Task B (`python/live/generate_daily_signal.py`, merged, PR
#69). R3-risk component (`java/runtime`) — TDD discipline applied
throughout, per CLAUDE.md's Development Methodology.

This task also closes **GitHub issue #70** and the symbol-match-
validation release gate recorded in
`.planning/paper-trading-a-signal-source.md`'s "CodeRabbit review
findings" section — see "Symbol-match validation: layer choice and why"
below.

## What was built

Two files touched, two new:

- **`TradingLoop.java`** (modified) — `tick()` now checks
  `intent.symbol().equals(symbol)` before handing a signal to
  `OrderPipeline.submitIntent`. A mismatch is a logged, defined skip
  (`log.warn`, no exception, `lastError` untouched) — the same treatment
  `tick()` already gives the kill-switch-tripped and equity-depleted
  skip paths. Class Javadoc gained a new "Symbol-match validation"
  section explaining the layer choice.
- **`FileSignalSource.java`** (doc-only) — one new Javadoc paragraph
  pointing at `TradingLoop` for the symbol check, so a reader landing on
  this class first isn't left wondering whether it validates the
  symbol itself (it doesn't).
- **`PaperTradingApp.java`** (new) — the `main()` entrypoint. Wires
  `RiskGateway.canary()`, `OrderStore`, `OrderPipeline`, `PaperBroker`,
  `FileSignalSource`, `BingXPriceFeed`, `KillSwitch`, and `TradingLoop`
  together, then drives `tick()` on a `ScheduledExecutorService`.
- **`PaperTradingAppTest.java`** (new) — 13 tests covering construction,
  config resolution (`resolveSignalPath`/`resolveTickIntervalSeconds`/
  `requireNonBlank`/`firstNonBlank`), a manufactured single-tick
  real-signal-to-real-fill scenario, and a real (short,
  1-second-interval) `start()`/`stop()` scheduler lifecycle.
- **`TradingLoopTest.java`** (modified) — 2 new tests:
  `tickRejectsASignalWithAMismatchedSymbolWithoutSubmittingItOrFailingTheTick`
  and `tickSubmitsASignalWithAMatchingSymbolNormally`.

No `build.gradle.kts` change was needed — `:runtime` already depends on
everything `PaperTradingApp` needs (`:oms`, `:risk`, `:schemas`,
`:execution`, `:exchange`, Jackson, SLF4J).

## Symbol-match validation: layer choice and why

Issue #70's acceptance criteria named two candidate layers for option
(a): inside `FileSignalSource` itself, or "centrally in
`PaperBroker`/`OrderPipeline`". This task's own governing brief added a
third real option after reading `TradingLoop.tick()`'s actual flow:
centrally inside `TradingLoop` itself. `PaperBroker`/`OrderPipeline`
were ruled out immediately — both are on this task's own "do not touch
... internals" list, and CLAUDE.md's Development Methodology requires a
`Discuss` pass before any behavioral change to R3-risk components, which
this task's own brief did not open for those two classes specifically.

That left two real candidates: `FileSignalSource` (the brief's
suggested "natural" default) and `TradingLoop` (what this task actually
picked). Reasoning, read directly against `TradingLoop.tick()`'s real
code (not assumed):

1. **`TradingLoop` already holds the single source of truth for "what
   symbol is this loop actually configured for."** Its `symbol` field
   is what `priceFeed.latestPrice(symbol)` is polled with two lines
   above the signal check, and what `orderPipeline.submitIntent` and
   `paperBroker.onPriceUpdate` are driven with elsewhere in the same
   method. The check just compares against a value the class already
   has — it does not need a new constructor parameter threaded through
   from somewhere else.
2. **Centralizing in `TradingLoop` protects every current and future
   `SignalSource` implementation uniformly**, not just
   `FileSignalSource`. `DummySignalSource` happens to always agree with
   its caller's configured symbol today (every existing test keeps them
   in sync by construction), but nothing enforced that — a future third
   `SignalSource` implementation (an HTTP push source, a multi-symbol
   router, anything) would need to remember to add its own check if the
   validation lived in `FileSignalSource` alone. Putting it in
   `TradingLoop` means the invariant is enforced exactly once, at the
   one place that actually knows what "correct" means.
3. **Separation of concerns**: `FileSignalSource`'s job is reading,
   parsing, and deduplicating a file — not knowing what symbol some
   particular `TradingLoop` instance happens to be configured for. Its
   constructor (`Path signalFilePath`) stays exactly as Task A left it;
   this task did not touch its signature or behavior at all, only added
   a documentation pointer.
4. **Testability**: the check is exercised directly against
   `TradingLoop` using a trivial lambda `SignalSource` (see
   `TradingLoopTest`'s two new tests below) — no file I/O needed to
   prove the invariant holds, matching this test file's own existing
   "small integration test, real components, no mocking framework"
   style.

`FileSignalSource` remains untouched behaviorally — its Javadoc was
extended with one new paragraph making the "I don't validate symbol,
`TradingLoop` does" fact explicit for a future reader, but its
constructor signature and `nextSignal()` contract are byte-for-byte
what Task A shipped.

### The exact change

```java
Optional<OrderIntent> signal = signalSource.nextSignal();
if (signal.isPresent()) {
    OrderIntent intent = signal.get();
    if (!intent.symbol().equals(symbol)) {
        log.warn(
                "signal {} has symbol {} but this loop is configured for {}; rejecting,"
                        + " not submitting",
                intent.intentId(), intent.symbol(), symbol);
    } else {
        Optional<Order> order = orderPipeline.submitIntent(intent, price, buildAccountState());
        if (order.isPresent()) {
            submitToBroker(order.get(), price);
        }
    }
}
```

A mismatch is treated exactly like the kill-switch-tripped and
equity-depleted skips already in this method: logged, `lastError` left
untouched, `tick()` returns normally. It is a defined operational
condition, not a bug — an external process (Task B's Python runner)
writing an unexpected symbol into the wrong `FileSignalSource`'s path is
a real, if unlikely, failure mode this check exists specifically to
catch (per issue #70's own background: `FileSignalSource` reads a file
an external, independently-scheduled process writes — a real TOCTOU/
misconfiguration surface `DummySignalSource`'s fixed, caller-chosen
symbol never exercised).

## TDD

Both new `TradingLoopTest` tests were written and run first, confirming
a real, meaningful failure (not a compile error — `TradingLoop` and
`SignalSource` already existed): the mismatch test failed because,
pre-fix, the wrong-symbol `LIMIT` intent was submitted and became
visibly pending in `PaperBroker.pendingOrders()` exactly as if it had
been a legitimate signal — proving the test could actually detect the
missing check, not just pass vacuously. (An earlier draft of both tests
used `GUARDED_MARKET`, which fills immediately regardless of symbol —
that shape could not have distinguished "correctly rejected" from
"incorrectly filled," so both tests were rewritten to use an
unmarketable `LIMIT` order, the same observability technique every
other test in this file already uses.) The fix above made both pass.

`PaperTradingAppTest` was written entirely before `PaperTradingApp`
existed — confirmed a real compile failure (`cannot find symbol: class
PaperTradingApp`, referenced from every test method) before any
production code was written. One test initially failed on real logic
(not a compile error) after the class was implemented:
`aManuallyDrivenTickReadsARealSignalFileAndProducesARealFill`'s
hand-computed expected fee (`0.3`) didn't account for
`PaperBroker.tryFill`'s slippage adjustment on `GUARDED_MARKET` fills
(`fillPrice = currentPrice * (1 + slippageBps/10000)`, not
`currentPrice` directly) — fixed by computing the expected fee with the
same formula `PaperBroker` itself uses, rather than a hand-typed
decimal, so the test can't silently drift out of sync with that
formula again.

Full suite after all changes (`./gradlew clean test`): **187 tests, 0
failures, 0 errors** across all six `java/` modules — up from 172 after
Task A (172 + 2 `TradingLoopTest` + 13 `PaperTradingAppTest` = 187).

## `PaperTradingApp` design

### Construction: explicit config, not `System.getenv()`, in the constructor

`public PaperTradingApp(String symbol, String bingxBaseUrl, Path
signalPath, long tickIntervalSeconds)` takes fully-resolved values.
`fromEnvironment()` is a separate, thin static factory that reads
`System.getenv()` and calls the constructor. This codebase has no
env-var mocking framework (grepped — confirmed absent), so keeping the
constructor itself free of any `System.getenv()` call is what makes
wiring/construction logic directly unit-testable without needing one.
The env-var *parsing* logic that would otherwise be untestable is
itself broken out into small, pure, package-private static helpers
(`resolveSignalPath`, `resolveTickIntervalSeconds`, `requireNonBlank`,
`firstNonBlank`), each tested directly against string inputs.

Constructing `PaperTradingApp` never makes a network call —
`BingXPriceFeed`'s own constructor only stores `baseUrl`, connecting
only when `TradingLoop.tick()` actually calls `latestPrice()` — so
tests can construct against an unreachable or fake `bingxBaseUrl`
freely.

### `RiskGateway` tier: canary, confirmed

`RiskLimits.canary()` — CLAUDE.md's Risk Parameters name canary as the
conservative tier (1x base leverage, 2x max, 2% max order notional,
-0.5%/-1.5%/-3%/-4% daily/weekly/monthly/hard-stop) and this task's own
brief says explicitly "this is paper trading, use the conservative
tier." No live wiring exists in this class or anywhere it's called from
(no `BingXAdapter`, no live/paper flag) — matches `TradingLoop`'s and
`08b-trading-loop.md`'s own "paper-only" precedent exactly.

### `PaperBroker` fee/slippage: reused, not independently chosen

`FEE_BPS = 5`, `SLIPPAGE_BPS = 2` — the exact same values
`python/live/generate_daily_signal.py` documents using "for consistency
with the backtested/holdout-confirmed configuration" (`sr-v`/`sr-ab`'s
own `FEE_BPS`/`SLIPPAGE_BPS`). `PaperBroker`'s fee/slippage simulation
is a conceptually separate thing from the strategy's own backtested
cost assumptions (one models the paper venue, the other models
historical backtest costs), but reusing the same numbers keeps the
whole bridge internally consistent end-to-end rather than introducing a
third, independently-chosen pair of constants with no citation. Both
are `static final BigDecimal` constants, not configurable — this task's
brief didn't ask for that, and CLAUDE.md's Risk Parameters don't cover
simulated-broker fee/slippage the way they cover leverage/notional/loss
limits, so there's no policy-level reason to expose them as env vars
yet.

### Signal path resolution: assumes CWD = repo root, same as Python

Default: `var/live/signals/<symbol>/daily-tsmom-ensemble/latest.json`,
computed via `resolveSignalPath(raw, symbol)` — deliberately mirrors
`python/live/generate_daily_signal.py`'s own `default_signal_path()`
byte-for-byte (same relative path shape, same `daily-tsmom-ensemble`
strategy-id segment). Resolved via `Path.of(...)`, which is relative to
the JVM's working directory when the path itself is relative (the
default is).

**Judgment call, stated explicitly**: this means `PaperTradingApp` must
be launched with its working directory set to the repository root —
exactly the same assumption Task B's Python runner already makes for
this identical path (`generate_daily_signal.py`'s own documented
scheduling example is `cd /path/to/trading-engine && ... python -m
live.generate_daily_signal`). This is not a new risk this task
introduces; it's the same convention applied symmetrically to the Java
side. `PAPER_TRADING_SIGNAL_PATH` (or the constructor's `signalPath`
argument directly) is the escape hatch for any launch context where
that assumption doesn't hold (e.g. an absolute path).

**Why no Gradle `application` plugin / `./gradlew :runtime:run`
support was added**: considered and rejected. Gradle's `application`
plugin's `run` task working directory defaults to the *module's* own
project directory (`java/runtime`), not the repository root two levels
up — making the default signal path resolve to the wrong place unless
the `run` task's `workingDir` were separately overridden, which is its
own source of confusion for a one-line entrypoint. Rather than take on
that build-config complexity, this task followed
`.planning/08b-trading-loop.md`'s own established precedent exactly: a
throwaway JUnit test driving the real class for the "real local run"
verification (see below), deleted immediately after its output was
captured, not part of the regular suite. A real deployment invokes
`PaperTradingApp.main()` via a plain `java -cp <classpath>
engine.runtime.PaperTradingApp` (or an equivalent launch script) from
the repository root — installing that launch script is not part of
this task's scope, matching Task B's own "installing the actual
crontab entry is out of scope" precedent for the same reason.

### Tick interval: 5 minutes default, configurable

`DEFAULT_TICK_INTERVAL_SECONDS = 300`. Configurable via the
`PAPER_TRADING_TICK_INTERVAL_SECONDS` env var (parsed by
`resolveTickIntervalSeconds`, which fails closed — throws
`IllegalStateException` on a non-positive or non-numeric value rather
than silently falling back to the default) or directly via the
constructor's `tickIntervalSeconds` parameter (what tests use). 5
minutes matches the governing plan's own stated reasoning verbatim:
"plenty frequent for a 1d-bar strategy; the signal itself only changes
once a day, this cadence is for price feed/kill-switch/equity tracking
liveness." `start()` schedules the first tick with **zero initial
delay** — deliberate, so a signal file already sitting on disk when the
process starts (e.g. after a restart) is picked up immediately rather
than waiting up to a full interval.

### `BINGX_BASE_URL`: mandatory env var, no code default

Matches every Python script in this project (`backfill.py`,
`backfill_funding.py`, `generate_daily_signal.py` — all read
`BINGX_BASE_URL` with no fallback and raise if unset) rather than the
CI guard comment's looser "defaulting to the VST demo host in code"
aspiration, which none of those scripts actually implement. Consistent
with the existing convention: `requireNonBlank` throws a clear
`IllegalStateException` naming the env var if it's missing or blank,
pointing at `bingx-hostname-guard.yml`/CLAUDE.md's Non-negotiable
Rules. No BingX hostname string (production or VST) appears anywhere in
`PaperTradingApp.java`'s source — confirmed by the same hostname-guard
CI job that already covers the rest of `java/`.

### Graceful shutdown

`main()` registers a JVM shutdown hook (`Runtime.getRuntime()
.addShutdownHook(new Thread(app::stop, ...))`) before calling
`app.start()`. `stop()` calls `executor.shutdown()` (no new tick
scheduled), then `awaitTermination(10, TimeUnit.SECONDS)`, falling back
to `shutdownNow()` if the in-flight tick (if any) doesn't finish in
time. `stop()` is safe to call more than once (idempotent —
`ExecutorService.shutdown()`/`awaitTermination()` are themselves
idempotent-safe) and safe to call even if `start()` was never invoked,
covering a shutdown hook racing an explicit `stop()` call elsewhere.
Verified directly: `startSchedulesRecurringTicksAndStopShutsDownCleanly`
calls `stop()` twice in a row without incident.

### `start()` can only be called once per instance

A defensive `IllegalStateException` guard, not requested by the task
brief but cheap and prevents a real misuse (double-scheduling the same
`TradingLoop` onto the same executor). Tested directly
(`startCannotBeCalledTwice`).

## Explicitly out of scope (per the governing brief, not attempted here)

- **OS-level process supervision** (systemd, restart-recovery). This
  process runs locally under manual/`tmux` supervision — the governing
  plan's own explicit "runs locally, not VPS-provisioned" decision.
  `stop()`'s graceful-shutdown handling is real and tested, but nothing
  here restarts a crashed *process*; that remains a later, VPS-deploy-
  time concern, matching `08b-trading-loop.md`'s identical "Deliberately
  out of scope" entry for `TradingLoop` itself.
- **Daily reporting** (Task D) and **position reconciliation** (Task
  E) — separate tasks, depend on this one, not started here.
- **An HTTP health endpoint** — matches `08b-trading-loop.md`'s own
  precedent exactly (`TradingLoop.lastTickAt()`/`lastError()`/
  `currentEquity()` stay plain queryable Java state; `PaperTradingApp`
  adds no HTTP surface on top of them). Real, structured SLF4J logging
  on every tick (info on success, warn with the error's `toString()` on
  failure) is what "24/7 supervision" means at this stage, same as
  `TradingLoop`'s own class Javadoc already states.
- **No changes to `RiskGateway`, `OrderPipeline`, `PaperBroker`,
  `KillSwitch`, or `OrderStore` internals** beyond constructing them
  with the intended arguments — confirmed by reading this diff: none of
  those five files were touched at all in this task.

## Real local run: actual output

Ran a throwaway JUnit test (`ManualPaperTradingAppShakedownRun`, not
committed — deleted immediately after this output was captured, same
precedent as `08b-trading-loop.md`'s own shakedown run) that constructed
a real `PaperTradingApp` against the real, live, public BingX VST demo
host (`https://open-api-vst.bingx.com` — no credentials, public market
data only), with a real 3-second tick interval and a real
`ScheduledExecutorService`. First wrote a real, `SchemaObjectMapper`-
serialized `OrderIntent` JSON file with a deliberately mismatched
symbol (`ETH-USDT`, loop configured for `BTC-USDT`), started the loop,
waited past two real ticks, then overwrote the same file with a
matching-symbol (`BTC-USDT`) signal and waited past another real tick
before stopping. Actual captured log output (SLF4J via `slf4j-simple`,
real timestamps):

```
[Test worker] INFO engine.runtime.PaperTradingApp - PaperTradingApp constructed: symbol=BTC-USDT bingxBaseUrl=https://open-api-vst.bingx.com signalPath=/tmp/junit-.../latest.json tickIntervalSeconds=3 riskTier=canary
=== STEP 1: writing a MISMATCHED-symbol signal (ETH-USDT) ===
wrote intent_id=89d5e4eb-6a5c-45e4-a87f-b32c92331c2a symbol=ETH-USDT
[Test worker] INFO engine.runtime.PaperTradingApp - starting paper trading loop: tickIntervalSeconds=3
[paper-trading-loop] WARN engine.runtime.TradingLoop - signal 89d5e4eb-6a5c-45e4-a87f-b32c92331c2a has symbol ETH-USDT but this loop is configured for BTC-USDT; rejecting, not submitting
[paper-trading-loop] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T13:36:23.242865738Z equity=100000
[paper-trading-loop] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T13:36:25.891627321Z equity=100000
=== after mismatched signal: equity=100000 lastError=null ===
=== STEP 2: writing a MATCHING-symbol signal (BTC-USDT) ===
wrote intent_id=135767e9-62ad-45ea-8a8a-8cc6ef2451e5 symbol=BTC-USDT
[paper-trading-loop] INFO engine.risk.RiskGateway - order 135767e9-62ad-45ea-8a8a-8cc6ef2451e5 approved: quantity=0.001
[paper-trading-loop] INFO engine.oms.Order - order 135767e9-62ad-45ea-8a8a-8cc6ef2451e5 -> NEW
[paper-trading-loop] INFO engine.oms.Order - order 135767e9-62ad-45ea-8a8a-8cc6ef2451e5 -> SUBMITTED
[paper-trading-loop] INFO engine.oms.Order - order 135767e9-62ad-45ea-8a8a-8cc6ef2451e5 -> ACKNOWLEDGED
[paper-trading-loop] INFO engine.oms.Order - order 135767e9-62ad-45ea-8a8a-8cc6ef2451e5 -> FILLED
[paper-trading-loop] INFO engine.execution.PaperBroker - order 135767e9-62ad-45ea-8a8a-8cc6ef2451e5 filled at 65229.64332 (fee=0.03261482166)
[paper-trading-loop] INFO engine.runtime.PaperTradingApp - tick complete: lastTickAt=2026-08-07T13:36:28.795324656Z equity=99999.96738517834
[Test worker] INFO engine.runtime.PaperTradingApp - stopping paper trading loop
=== FINAL STATE ===
lastTickAt=2026-08-07T13:36:28.795324656Z
lastError=null
equity=99999.96738517834
```

This confirms, against real components and (for the price feed) a real
network call, every link the task brief asked for: the mismatched
signal was read and correctly rejected with a logged warning without
touching equity or raising an error; the matching signal was read,
passed symbol validation, flowed through a real `RiskGateway.evaluate()`
(logged `approved: quantity=0.001`), and produced a real `Order` state
machine walk (`NEW -> SUBMITTED -> ACKNOWLEDGED -> FILLED`) and a real
`PaperBroker` fill at a real, live BingX VST market price
(`65229.64332`) with the correct slippage-adjusted fee
(`0.03261482166`), reflected exactly in the equity drop
(`100000 -> 99999.96738517834`). Clean shutdown logged
(`stopping paper trading loop`), no exception anywhere in the run.

This is one real, short run (roughly 8 seconds of real wall-clock
scheduling across 3 ticks) — evidence against the specific failure
modes it exercises (real network I/O to price feed, real scheduled
ticking, both symbol-validation branches, a real fill), not a
demonstration of multi-hour/multi-day resilience, matching
`08b-trading-loop.md`'s own identical caveat for its shakedown run.

## Verification

- `./gradlew :runtime:compileTestJava` against `PaperTradingAppTest`
  failed with the expected "cannot find symbol: class PaperTradingApp"
  before `PaperTradingApp` existed.
- `./gradlew :runtime:test --tests TradingLoopTest` — both new tests
  failed for a real, meaningful reason before the fix (see "TDD"
  above), passed after.
- `./gradlew :runtime:test --tests PaperTradingAppTest` — 13/13 pass.
- `./gradlew clean test` (full multi-module suite) — **187 tests, 0
  failures, 0 errors**.
- Real local run against the real BingX VST endpoint — see above.
- PR opened, not merged — per the governing plan and CLAUDE.md's
  Auto-merge Policy, this is Java runtime code (R3-risk-adjacent:
  wires `RiskGateway`/`OrderPipeline`/`PaperBroker`, plus a real
  behavioral change to `TradingLoop.tick()`) and requires explicit
  human sign-off regardless of CI/CodeRabbit status.
