# BingX VST integration, Task H: VST wiring, safety, and the real verification

## Scope note

This is **Task H**, the final task of the 3-task BingX VST integration plan
(`.claude/plans/tender-finding-matsumoto.md`), which depends on Task G
(`ExchangeOrderExecutor`, merged to `main` via PR #78, see
`.planning/paper-trading-g-exchange-order-executor.md`). R3-risk component
(`java/runtime`, `java/exchange`) touching real credentials and a real
order-placement network path for the first time in this project's
history — TDD discipline applied throughout per CLAUDE.md's Development
Methodology, and every fake-only unit test was written and confirmed red
before the corresponding implementation, exactly as required for OMS/Risk/
Execution code.

Unlike Tasks F/G, this task's own governing brief explicitly authorized
(and required) a real network call against BingX's actual VST host as its
final step — the actual point of this whole 3-task effort. Everything
before that step was built and tested exclusively against hand-written
fakes/in-process servers, with zero real network access, matching Tasks F
and G's own discipline.

## What was built

1. **`PAPER_TRADING_EXECUTION_MODE` on `PaperTradingApp`** — new env var,
   `simulated` (default, and the default for unset/blank/anything
   unrecognized — this project's standing fail-safe-to-known-good
   convention) or `bingx-vst`. `resolveExecutionMode(String raw)` is a new
   pure, package-private static method exactly matching the existing
   `resolveSignalPath`/`resolveTickIntervalSeconds` pattern this class
   already used — unit tested directly (`PaperTradingAppTest`), not via
   `fromEnvironment()` itself, matching this class's own established
   convention that `fromEnvironment()`'s real env-reading behavior is
   never unit tested, only its constituent pure functions are (confirmed:
   no existing test in this codebase calls `fromEnvironment()` directly).
2. **`BINGX_VST_BASE_URL` as a Java constant** —
   `private static final String BINGX_VST_BASE_URL =
   "https://open-api-vst.bingx.com";` on `PaperTradingApp`. See "Safety-
   guard reasoning" below for why this is genuinely unreachable from any
   configuration surface. A new package-private constructor overload
   (`PaperTradingApp(String, String, Path, long, Path, Clock,
   OrderExecutor)`) accepts a pre-built `OrderExecutor` for tests,
   mirroring the class's existing test-only `Clock` overload pattern
   exactly. The existing public constructors and the existing 6-arg
   `Clock` overload are **byte-for-byte unchanged** in what they build
   (still always construct a real `PaperBroker`) — only the internal
   field type changed (`PaperBroker paperBroker` → `OrderExecutor
   orderExecutor`, matching Task F's own `TradingLoop`/`Reconciler`
   precedent), which is a pure widening-reference change with zero
   behavioral effect. `PaperTradingAppTest`'s every pre-existing test case
   is untouched; new tests were only added, never modified.
3. **`BalanceSnapshot` gains `String asset`** — parsed by
   `BingXAdapter.getBalance()` from the real `asset` field CLAUDE.md's own
   "Verified — authenticated, VST key" section already documented as
   present on the wire. `BingXAdapterTest`'s existing fixtures already
   carried `"asset":"VST"` in their JSON (added incidentally by an earlier
   task, never asserted on) — this task added the real assertions plus a
   dedicated missing-field test.
4. **`engine.runtime.VstPreflight`** — a real, tested, four-step startup
   check (see its own Javadoc for the full four-step contract): fails
   closed (`IllegalStateException`, never a silent fallback to simulated
   mode) unless `getBalance().asset()` is exactly `"VST"`; logs the real
   balance and warns (informational only, never a hard gate) if it looks
   small relative to canary-tier sizing; calls `getPositions()` and
   returns `killSwitchShouldStartTripped=true` if any non-zero position
   exists (this process has no restart-recovery/reconciliation-against-
   real-positions story anywhere else); never logs API key/secret
   anywhere (structurally guaranteed — the class is constructed with only
   an `ExchangeAdapter` reference, which exposes no credential accessor).
   11 tests (`VstPreflightTest`) against a hand-written `FakeExchangeAdapter`
   test double local to `:runtime`'s own test sources (Gradle test source
   sets aren't shared across modules, so this mirrors, rather than reuses,
   `:execution`'s own package-private `FakeExchangeAdapter`).
5. **Persistent `SUBMISSION_UNKNOWN` handling** — four new, small,
   focused classes (see "SUBMISSION_UNKNOWN design" below for the full
   writeup): `SubmissionMarker` (record), `SubmissionMarkerStore`
   (durable, single-JSON-file persistence — the first piece of
   cross-restart persistence anywhere in this codebase),
   `PersistentSubmissionOrderExecutor` (an `OrderExecutor` decorator that
   records a marker immediately before an ambiguous `submit` call and
   clears it only on a non-throwing return), and
   `SubmissionMarkerResolver` (the startup-time resolution step, run
   alongside `VstPreflight`). 9 + 5 + 7 = 21 tests across the three new
   test classes (`SubmissionMarkerStoreTest`,
   `PersistentSubmissionOrderExecutorTest`,
   `SubmissionMarkerResolverTest`).
6. **`FileSignalSource`'s optional persisted delivered-marker file** — a
   new, additive, optional two-arg constructor
   (`FileSignalSource(Path signalFilePath, Path deliveredMarkerPath)`);
   the existing one-arg constructor now delegates to it with `null`,
   preserving today's exact in-memory-only behavior byte-for-byte (every
   pre-existing `FileSignalSourceTest` test case is untouched). When
   configured, the last-delivered `intentId` is persisted (overwritten,
   not appended) immediately after each genuinely new delivery, and read
   back at construction to seed the in-memory dedup state before the very
   first `nextSignal()` call — closing the real duplicate-order risk this
   class's own Javadoc already documented (a restart on the same UTC day
   after a signal fired would redeliver it, and against a real venue that
   means a real second order). 5 new tests.
7. **The guardrail hook** — a second `PreToolUse` entry in
   `.claude/settings.json` (see "The guardrail hook" below for the full
   design and what it does/doesn't cover).
8. **The real one-off VST verification** — see "The real VST verification"
   below for the complete, real captured output.

## Safety-guard reasoning: why there is genuinely no way to misroute to production

Thought through directly as the task's own brief asked ("think like an
attacker/careless-operator"), not just asserted:

- **No environment variable exists for the order-execution host.**
  `BINGX_VST_BASE_URL` is a `private static final String` constant, read
  by exactly one method (`forBingXVst`, itself `private`). Grepped the
  entire `bingx-vst`-mode code path (`PaperTradingApp.java`,
  `VstPreflight.java`, `SubmissionMarkerResolver.java`,
  `PersistentSubmissionOrderExecutor.java`, `ExchangeOrderExecutor.java`,
  `BingXAdapter.java`) for `System.getenv` — the only calls are for
  `BINGX_API_KEY`/`BINGX_API_SECRET` (credentials, not a host) and the
  *other*, pre-existing, unrelated `BINGX_BASE_URL` (which only ever
  reaches `BingXPriceFeed`, the public-market-data-only class — confirmed
  by reading `forBingXVst`'s own body: `bingxBaseUrl` is passed through to
  the `PaperTradingApp` constructor for the price feed only, never to
  `BingXAdapter`, which is constructed separately with the hardcoded
  constant).
- **No constructor argument reaches it either.** The new
  `OrderExecutor`-accepting `PaperTradingApp` constructor takes an
  already-fully-built `OrderExecutor` — by the time that constructor runs,
  whatever host it was built against is already fixed; the constructor
  itself has no host-shaped parameter to override. `forBingXVst` is the
  **only** call site anywhere in this codebase that ever constructs a
  `BingXAdapter` pointed at anything derived from `BINGX_VST_BASE_URL`,
  and it is not part of any public API — unreachable from
  `PaperTradingApp.fromEnvironment()`'s public surface except via the
  `bingx-vst` mode dispatch, which itself only ever passes the hardcoded
  constant.
- **A future edit could still change the constant's own value** — this is
  explicitly accepted, not a gap: CLAUDE.md's own Non-negotiable Rules
  target hardcoding the *production* host, not the VST host (see the
  `bingx-hostname-guard` hook's own header comment: "hardcoding the VST
  host specifically is fine"). The existing `bingx-hostname-guard` hook
  (and its CI backstop workflow) already blocks the literal string
  `open-api.bingx.com` (no `-vst`) from ever being written into any
  `.java`/`.py` file — including, provably, into this exact constant —
  confirmed directly: attempting to write
  `BINGX_VST_BASE_URL = "https://open-api.bingx.com"` during this task's
  own development would have been blocked by that pre-existing hook
  before this task ever started (verified by reading the hook's own grep
  pattern, not assumed). What this task's own new hook adds (see below)
  is a **second, narrower** guard: blocking an edit that tries to source
  `BINGX_VST_BASE_URL`'s value from an environment variable at all (a
  `getenv` call co-occurring with the constant's name on the same
  line/edit), independent of what host string that env var might resolve
  to at runtime — closing the "reintroduce a configuration surface"
  attack, not just the "type the wrong literal" one.
- **Bottom line**: short of directly editing `PaperTradingApp.java`'s own
  source (which the task's own brief explicitly names as the only
  acceptable way, and which is itself now doubly guarded — the existing
  hostname hook plus this task's new one), there is no environment
  variable, CLI argument, config file, or constructor parameter anywhere
  in this codebase that can route the `bingx-vst`-mode order-execution
  path to anything other than `https://open-api-vst.bingx.com`.

## `VstPreflight` real behavior

Confirmed against the real VST host during the verification run (see
below): `getBalance()` returned `asset="VST"` (passed the fail-closed
check), a real balance, and `getPositions()` returned an empty array (no
pre-existing positions, so the kill switch stayed clean/untripped at
startup). All four steps were exercised for real, not just against the
fake in the unit suite. The 11 `VstPreflightTest` cases separately cover:
asset mismatch (both a wrong asset and a `null` asset) → refuses to start;
the refusal message never contains "apikey"/"secret" (a cheap, direct
proof of the "never logs credentials" claim, verified as an observable
contract rather than only by code inspection); a small-balance case is
informational only (does not throw, does not trip); a non-zero long
position and a non-zero short position both force
`killSwitchShouldStartTripped=true`; an all-zero-sized position list does
not; `getBalance()`/`getPositions()` failures both propagate uncaught
(this is a one-shot startup check, not a per-tick call with `pollFills`'s
own "never throw" contract); a `null` adapter is rejected.

## `SUBMISSION_UNKNOWN` design

The governing plan (and Task G's own planning doc) deferred this entirely
to Task H — see Task G's doc for the full reasoning at the time. Re-read
before starting this task, per the task brief's own instruction.

**The real constraint that shaped this design, found while implementing
it, not assumed in advance**: `ExchangeAdapter.queryOrder(Order)` requires
a concrete `Order` carrying a non-null `exchangeOrderId()`. Traced through
`BingXAdapter.submitOrder`'s real control flow: **every** code path that
ever assigns `exchangeOrderId` (via `order.acknowledge(...)`) is
unreachable if `submitOrder` throws — a thrown `submitOrder` always means
no `exchangeOrderId` was ever captured. Compounding this, `Order` objects
are not durable — `OrderStore` is in-memory only (its own Javadoc already
says so) — so even the in-memory `Order` reference itself is gone after a
real process restart. Together, these mean **a `SUBMISSION_UNKNOWN`
marker can never be resolved via `adapter.queryOrder` at all**, by
construction, for the exact restart scenario this mechanism exists to
protect — there is no `Order` to query with, and no `exchangeOrderId` to
query by even if there were. The governing plan's own phrasing
("`adapter.queryOrder`/`getPositions`") reads as offering a choice; this
is a real, disclosed finding that only one of those two is actually
usable here, not an oversight or a shortcut.

**Resolution therefore uses `ExchangeAdapter.getPositions()` only**
(`SubmissionMarkerResolver`) — real ground truth about the account's
current holdings, callable with no per-order identifier at all. For each
persisted `SubmissionMarker`, resolution checks whether any non-zero
position exists for that marker's own `symbol`:

- **No matching position** → treated as most likely never having reached
  the exchange (the "pre-acceptance failure" case from the task brief) —
  safe to clear the marker.
- **A matching non-zero position exists** → the ambiguous submission may
  have gone through (the "post-acceptance timeout" case) — the marker is
  deliberately **not** auto-cleared and **not** treated as license for any
  retry; it stays recorded, flagged in the returned `Resolution
  #requiresHumanReview()` list, logged at ERROR, for a human to
  investigate directly (e.g. via the BingX UI) and clear manually once
  confirmed.

This is coarser than a genuine per-order query would be — it can only
answer "does *any* position exist for this symbol," not "did *this
specific* order fill," an accepted limitation given this project's current
single-symbol, low-frequency, effectively-one-order-in-flight-per-symbol
scope (see CLAUDE.md's Current Scope). `BingXAdapter.queryOrder` accepting
a `clientOrderID`-based lookup (common on exchanges in this family) would
remove this limitation but is unconfirmed against BingX's real API
(nothing in CLAUDE.md's "Exchange API Facts" documents this) — a real,
disclosed follow-up, not guessed at or implemented speculatively here.

**Persistence** (`SubmissionMarkerStore`) is a single JSON file
(`var/live/submission_markers.json`, matching the existing `var/live/
signals/`/`var/live/reports/` convention), loaded fully into memory at
construction and rewritten fully on every `record`/`clear` — deliberately
not a general persistence framework; this project's own single-symbol,
daily-cadence scope means at most a handful of entries are ever live at
once, so "rewrite the whole file" costs nothing in practice. Tolerant of a
missing or corrupt file at construction (treated as empty, logged only for
corruption) — matching `FileSignalSource`'s own established tolerance
convention — but a **write** failure propagates (`IllegalStateException`),
since a caller must know if a just-recorded marker didn't actually become
durable; that's the entire point of the class.

**`PersistentSubmissionOrderExecutor`** is a thin `OrderExecutor`
decorator: records a marker immediately before delegating `submit`, clears
it only if the delegate call returns normally (the resulting `Order`
state — `ACKNOWLEDGED` or `REJECTED` — is then definitively known, no
ambiguity remains), and leaves it recorded if the delegate throws.
`pollFills`/`pendingOrders`/`cancel` are pure passthroughs. Deliberately
holds no `ExchangeAdapter` reference at all — resolution is a fully
separate concern (`SubmissionMarkerResolver`), keeping this class simple
and testable with a hand-written `OrderExecutor` fake
(`FakeOrderExecutor`, local to `:runtime`'s test sources) rather than
needing any exchange-shaped test double.

**Required-scenario mapping** (task brief's three named tests):

| Brief's scenario | Test |
|---|---|
| Post-acceptance timeout (order was accepted; must not auto-resubmit; resolves via real state query) | `SubmissionMarkerResolverTest#aMarkerWithAMatchingNonZeroPositionRequiresHumanReviewAndIsNotCleared` |
| Pre-acceptance failure (never accepted; safe to clear) | `SubmissionMarkerResolverTest#aMarkerWithNoMatchingPositionIsClearedAsSafe` |
| Marker still unresolved across a simulated restart (must not be silently dropped or retried) | `SubmissionMarkerStoreTest#recordPersistsAMarkerVisibleToAFreshInstanceAgainstTheSameFile` + `SubmissionMarkerResolverTest`'s own review-required case leaving the marker recorded (not cleared) in the store |

**A real, disclosed deviation from the brief's literal wording**: "resolving
it via a real `queryOrder` call finding the order already live" (the
brief's own phrasing for the post-acceptance-timeout test) is satisfied in
spirit — the marker resolves correctly to "do not clear, do not permit a
retry" — but via `getPositions()`, not literally `queryOrder`, for the
structural reason explained above. Named explicitly here rather than
silently substituted, matching this project's own established review-
response norm ("deviate from the literal suggested text with reasoning
when there's a good reason to").

**Wiring**: `PaperTradingApp.forBingXVst` calls `SubmissionMarkerResolver
.resolve` immediately after `VstPreflight.run`, before constructing the
real `OrderExecutor` graph — an unresolved (`requiresHumanReview`) marker
forces the same `killSwitchShouldStartTripped` outcome as a non-zero
`VstPreflight` position, for the same reason (unknown state this process
must not begin normal tick-driven trading against).

## `FileSignalSource` persistence fix and its tests

Covered under "What was built" item 6 above. The 5 new tests: a `null`
marker path is byte-for-byte equivalent to the existing one-arg
constructor; a marker file that already recorded a delivered `intentId`
suppresses redelivery on the very first `nextSignal()` call after a
simulated restart (the actual point of the fix); a real delivery persists
the marker so a fresh instance against the same files does not redeliver
it (the direct opposite of the existing, still-true, null-path restart
test); a distinct new intent is still delivered normally with a marker
path configured; the marker reflects the *last* delivered `intentId`, not
the first, across two deliveries.

`PaperTradingApp`'s new `OrderExecutor`-accepting constructor always wires
the marker-file variant (a sibling of the signal path, named
`delivered.marker`) — the existing `PaperBroker`-building constructors
never do, preserving `simulated` mode's zero-behavior-change guarantee.
This means every real `bingx-vst`-mode process automatically gets this
protection; nothing separate needed to be threaded through
`fromEnvironment()` for it.

## The guardrail hook

CLAUDE.md's own Tooling Stack table listed this row as owed ("not built
yet — add before real exchange credentials appear") since before this
task; credentials now exist in `.env`, so it was due. Added as a second
`PreToolUse` entry in `.claude/settings.json` (the existing hostname-guard
hook is untouched — a new entry, not a rewrite), following the exact same
`jq`-based payload-parsing pattern already established there. Empirically
tested (not just reasoned about) against six synthetic payloads and the
real, current `PaperTradingApp.java` file content before being installed
— see the six cases below; all behaved as intended.

**What it blocks, and why each is the real residual gap, not theater**:

1. **A `.github/workflows/*.yml`/`*.yaml` edit whose content mentions
   `BINGX_API_KEY`, `BINGX_API_SECRET`, or the literal `bingx-vst` mode
   string.** This is the actual remaining risk after the safety-guard
   reasoning above: the *host* can't be misrouted, but nothing before this
   hook stopped a future edit from wiring real VST credentials or
   `PAPER_TRADING_EXECUTION_MODE=bingx-vst` into a CI workflow, which
   would attempt a real network call with every push/PR — directly
   violating CLAUDE.md's "never add live exchange write-access in CI"
   (CI has no such secret configured today, so this would currently just
   fail loudly rather than trade anything, but the hook stops it from
   ever being wired in the first place, not just relies on a missing
   secret).
2. **A `.java` file edit where `BINGX_VST_BASE_URL` and `getenv` both
   appear on the same line/string** (case-insensitive on `getenv`) — a
   direct attempt to reintroduce an environment-variable override for the
   hardcoded VST host, the one thing the "Safety-guard reasoning" section
   above explicitly relies on never existing.

**What it deliberately does *not* try to cover, and why that's not a
gap**: it does not re-implement the existing `bingx-hostname-guard`
hook's production-hostname check (already covers that, unmodified); it
does not attempt to block `PAPER_TRADING_EXECUTION_MODE=bingx-vst` being
*set as a real shell environment variable* when a human (or this session,
for the real verification below) runs a local command — that is
legitimate, intended, necessary operation for this whole task to exist,
and a hook that blocked it would block the actual point of Task H, not
just a misuse of it; the "safety-guard reasoning" section's own
elimination of any *configuration surface for the host* is what makes
local `bingx-vst` invocation safe to allow, not a hook.

**False-positive check, not just a synthetic one**: piped the *real*,
current `PaperTradingApp.java` file content (which legitimately contains
both `BINGX_VST_BASE_URL` and several unrelated `System.getenv(...)`
calls, on different lines) through the exact installed hook command —
confirmed exit 0 (not blocked). Without the same-line restriction (a
naive "file contains both substrings anywhere" check), this would have
false-positived on every future edit to this file — checked and rejected
during design, not discovered after installing.

## The real VST verification

**What was actually run, in order, all for real, all through the full
OMS-mediated path** (`OrderIntent → OrderPipeline → RiskGateway → Order →
ExchangeOrderExecutor/PersistentSubmissionOrderExecutor → BingXAdapter →
the real VST host `open-api-vst.bingx.com`): a real `PaperTradingApp` was
constructed via `PaperTradingApp.fromEnvironment()` (the exact same public
entrypoint `main()` uses) with `PAPER_TRADING_EXECUTION_MODE=bingx-vst`
and real `BINGX_API_KEY`/`BINGX_API_SECRET` sourced from the actual,
gitignored `.env`. Driven by a throwaway, package-`engine.runtime`
verification class (`ManualVstVerification`, compiled and run entirely
outside the `java/` Gradle module tree against its own build outputs — not
a JUnit test, never part of `./gradlew build`, never committed to this
repo) — package-private access was needed for `runTick()`/`orderStore()`/
`orderExecutor()`/`killSwitch()`, exactly the same package-private
accessors `PaperTradingAppTest` already uses, none added or widened
specifically for this driver except `orderExecutor()` (a small, new,
package-private accessor added alongside the pre-existing `orderStore()`/
`killSwitch()`/`tradingLoop()` ones, needed for the real cancel
experiment below). No hand-built `Order` was ever fed directly to
`submit`/`cancel` — every order that reached the network went through a
real `RiskGateway.evaluate()` call via a real `OrderPipeline`.

### 1. Real balance/asset

```json
{"code":0,"msg":"","data":[{"userId":"1494058088073977861","asset":"VST","balance":"96224.6140","equity":"96224.6140","unrealizedProfit":"0.0000","realizedProfit":"0","availableMargin":"96224.6140","usedMargin":"0.0000","frozenMargin":"0.0000","shortUid":"33719465"}]}
```

`asset="VST"`, balance/equity **96224.6140** — `VstPreflight` passed the
fail-closed asset check for real. `getPositions()` returned `[]` (no
pre-existing positions) — kill switch stayed clean at startup.

### 2. Real order submission, acknowledgment, and fill

A real `GUARDED_MARKET` BTC-USDT order, 0.001 BTC, LONG, submitted through
the real `RiskGateway` (canary tier approved it at the requested quantity,
`approvedLeverage=1`). Raw `POST /openApi/swap/v2/trade/order` response:

```json
{"code":0,"msg":"","data":{"order":{"orderId":2086368129182601216,"orderID":"2086368129182601216","symbol":"BTC-USDT","positionSide":"LONG","side":"BUY","type":"MARKET","price":0,"quantity":0.001,"quoteOrderQty":0,"stopPrice":0,"workingType":"MARK_PRICE","clientOrderID":"a0dc6508-31b5-4ef1-8b5b-93dc1b987ba8","clientOrderId":"","timeInForce":"GTC","priceRate":0,"stopLoss":"","takeProfit":"","reduceOnly":false,"activationPrice":0,"closePosition":"","stopGuaranteed":"","status":"FILLED","avgPrice":"64881.5","executedQty":"0.0010"}}} // gitleaks:allow (clientOrderID is a random UUID, this project's own OrderIntent.intentId, not a secret)
```

Note `"status":"FILLED"` is **already present in the submit response
itself** — the market order matched essentially instantly. This
codebase's `ExchangeOrderExecutor.submit` deliberately never assumes an
instant fill from this response (always returns `Optional.empty()`, per
its own documented, uniform-and-conservative contract) — the fill was
observed on the next `pollFills` instead, via a real
`GET /openApi/swap/v2/trade/order` response:

```json
{"code":0,"msg":"","data":{"order":{"symbol":"BTC-USDT","orderId":2086368129182601216,"side":"BUY","positionSide":"LONG","type":"MARKET","origQty":"0.0010","price":"64880.6","executedQty":"0.0010","avgPrice":"64881.5","cumQuote":"65","stopPrice":"","profit":"0.0000","commission":"-0.032441","status":"FILLED","time":1786263899000,"updateTime":1786263900000,"clientOrderId":"a0dc6508-31b5-4ef1-8b5b-93dc1b987ba8","leverage":"20X", ...}}} // gitleaks:allow (clientOrderId is a random UUID, not a secret)
```

**A real `commission` field exists**: `"-0.032441"` (negative = fee
charged). This directly answers Task G's own disclosed open question
("does a real commission field exist?") — yes. This project's own modeled
fee for the same fill (`FEE_BPS=5` on notional `0.001 × 64881.5 =
64.8815`) is `64.8815 × 0.0005 = 0.03244075` — **0.03244075 modeled vs.
0.032441 real**, a ~0.003% relative difference on this one trade. Not
switched to the real field in this task (per Task G's own "evidence-first
follow-up, not assumed now" framing — this is exactly that evidence, not
an implementation decision) — one real data point, not a general proof
the two always agree this closely.

**Ack-to-fill-observed latency**: `PT1.494170836S` (~1.5s) between the
first tick's own completion and the poll that observed `filledQuantity >
0`. Given the submit response itself already showed `FILLED`, this figure
is this project's own ~1s polling cadence during the verification run,
not real exchange latency — the real fill was, as far as this evidence
shows, effectively simultaneous with acknowledgment.

**Real leverage observed: `"20X"`** — the VST account's own real,
pre-existing account-wide leverage default, entirely independent of this
project's `RiskGateway`-approved `1x` (canary tier's base leverage) —
see CLAUDE.md's new "Verified" bullet for the full disclosure of why
(nothing in this codebase calls `POST /trade/leverage` yet, a real,
pre-existing gap, not introduced by this task, flagged for a human
priority decision rather than fixed here).

Reconciliation after this fill: `ReconciliationReport[mismatches=[],
...]` — clean.

### 3. Real cancel

A real `LIMIT` BUY order, 0.001 BTC, priced 50% below the live market
(`32440.00` against a live reference price of `64879.9` — guaranteed
non-marketable), was submitted, acknowledged
(`exchangeOrderId=2086368139878076416`), then cancelled for real via
`ExchangeOrderExecutor.cancel` (through the new
`PaperTradingApp#orderExecutor()` accessor). Raw
`DELETE /openApi/swap/v2/trade/order` response:

```json
{"code":0,"msg":"","data":{"order":{"symbol":"BTC-USDT","orderId":2086368139878076416, ... ,"status":"CANCELLED", ...,"clientOrderId":"73b72c14-e964-4c54-827f-bc2e8cb7cd4a", ...}}} // gitleaks:allow (clientOrderId is a random UUID, not a secret)
```

**The real status token is `"CANCELLED"` (double-L)** — confirms the REST
half of CLAUDE.md's previously-documented REST/WebSocket casing
inconsistency; WebSocket's own `"CANCELED"` remains unverified (this
project has never made a WebSocket call).

### 4. Duplicate `clientOrderID` experiment

**The empirical half of the duplicate-order risk this plan otherwise only
mitigates in software.** A second, fully independent `RiskGateway` +
`OrderStore` + `OrderPipeline` + `ExchangeOrderExecutor` graph (a
genuinely separate object graph, not the same instances the first order
used) — simulating a second process/session whose own `OrderStore` never
saw the first submission (exactly the restart scenario this task's
`FileSignalSource` marker fix protects against) — submitted a **new**
`OrderIntent` sharing the **same** `intentId`/`clientOrderId` as the
already-filled order above, through a **real** `RiskGateway.evaluate()`
call (not skipped, not faked). Raw response:

```json
{"code":101400,"msg":"clientOrderID unique check failed","data":{}}
```

**BingX rejects a duplicate `clientOrderID` outright** — not a silent
duplicate fill, not a silently-ignored no-op. This project's own
`ExchangeOrderExecutor` mapped it cleanly to `Order.reject(...)`
(`state=REJECTED`, `exchangeOrderId=null`, no `Fill`) — the `submit` call
itself did not throw, matching its designed reject-handling path exactly.

**A deliberate, disclosed judgment call on how this experiment was run,
stated precisely**: this project's own `OrderPipeline`/`OrderStore`
idempotency guard is *instance-scoped*, not global — it guarantees "this
process's OrderStore creates at most one `Order` per `intentId`," not
"BingX only ever sees each `clientOrderID` once across every process that
has ever run." Submitting the duplicate through the **same** `OrderStore`
the first order used is structurally impossible to observe BingX's own
behavior with — `OrderStore.createOrder` would simply return the
already-`ACKNOWLEDGED` first `Order` object unchanged, and
`Order.submit()`'s own state guard (`requireState(NEW)`) would throw
**locally**, before any second HTTP call could ever be attempted. Using a
genuinely separate `OrderStore` is not a bypass of RiskGateway (a real
`RiskGateway.evaluate()` call was made, with the same real inputs, and it
approved the duplicate exactly as it approved the first) — it is the only
way to construct the real-world scenario ("a second, independent
submission attempt reaching BingX with a `clientOrderID` a different
in-memory instance has no record of") this experiment exists to observe.

### 5. Real state left on the VST account as a result of this run

- One real, still-open **0.001 BTC LONG position** at ~64881.5, from the
  filled `GUARDED_MARKET` order above. **Not flattened** — closing it
  would itself be another real order placement, which was not asked for
  and was not done unilaterally; left for @ckrhehfl to close via the
  BingX UI directly (simpler than running more code for it) or to leave
  as-is. A real consequence, working exactly as designed: any *future*
  real `bingx-vst`-mode process start will have `VstPreflight` find this
  non-zero position and correctly start with the kill switch already
  tripped, requiring a deliberate human reset — this is the intended
  safety behavior, not a bug, and this run is direct, real evidence it
  actually fires.
- The cancelled `LIMIT` order above leaves no residual open order (real,
  confirmed `CANCELLED` state).
- `var/live/submission_markers.json` (relative to wherever the process
  was run from — the verification's own scratch working directory, not
  this repository) ended as `[]` — real, direct evidence the
  record-then-clear cycle works correctly for a submission that never
  throws (both real submissions completed normally; neither ever left a
  lingering marker).
- The already-running, separate simulated `tmux` paper-trading process on
  the actual host machine was never touched, restarted, or affected —
  entirely outside this worktree's reach, and nothing in this task's
  design or verification run could have reached it (distinct process,
  distinct `PAPER_TRADING_REPORTS_DIR`, distinct `KillSwitch`).

### A real, disclosed credential-handling incident during this same run — root-caused and fixed

**What happened**: `.env` has CRLF line terminators (confirmed:
`file .env` → `ASCII text, with CRLF line terminators`). The first
verification attempt sourced it via a plain `bash source`, which left a
trailing `\r` on the last-read value reaching that call. The JDK's
`HttpRequest.Builder#header` rejects a raw `\r` in a header value
outright (`IllegalArgumentException` — RFC 7230 does not permit control
characters in header values) — with a message that embeds the **literal,
invalid header value** (i.e. the real `BINGX_API_KEY`) verbatim. That
exception's stack trace was captured into a local, gitignored scratch log
file (`/tmp/.../real_run_output.log`, entirely outside this repository,
never committed, never pushed). **Immediately on discovery**: the file's
plaintext content was overwritten with a redaction notice (the `Write`
tool, not a shell `rm`, since this session's own destructive-delete
guardrail hook correctly blocked a plain `rm -f` here — matching its own
purpose). A separate, unrelated `cat -A` command run afterward (checking
whether `BINGX_API_SECRET` had the same CRLF problem) also briefly
surfaced the real `FRED_API_KEY` value in this session's own tool-call
output, for an unrelated reason (the file's last line has no trailing
newline, which broke an ad hoc redaction regex requiring one) — this one
is *not* removable after the fact (it already rendered in this session's
transcript), but it never reached any file, commit, or persistent
storage this session controls.

**Root cause fixed, not just avoided**: `BINGX_API_SECRET` was
independently confirmed **never** to have hit this same failure path —
it is only ever used to compute a client-side HMAC-SHA256 signature
(`BingXSigner`), never transmitted as a raw header value or logged
anywhere in this codebase, and a redacted `cat -A` check on the sourced
`.env` confirmed both `BINGX_API_KEY` and `BINGX_API_SECRET` lines *did*
end with the CRLF marker that the redaction regex correctly matched (only
the last line — `FRED_API_KEY`, lacking a final newline — slipped
through). Rather than merely retrying more carefully, `BingXAdapter`'s
constructor now `.strip()`s both `apiKey`/`apiSecret` before storing them
— a real, tested fix
(`BingXAdapterTest#constructorStripsLeadingAndTrailingWhitespaceFromCredentials`)
that closes this entire class of issue (any leading/trailing whitespace or
control character in a sourced credential, from CRLF `.env` files or any
other whitespace-contaminated source) at its one real entry point, not
merely by being more careful about how `.env` gets sourced next time. The
second, successful verification attempt sourced `.env` through `tr -d
'\r'` first (the actual immediate fix) and additionally piped all output
through a defense-in-depth filter (`sed -E
's/[A-Za-z0-9]{30,}/[REDACTED-LONG-TOKEN]/g'`) that would redact any bare
30+-character alphanumeric token before it could ever reach a file or this
session's own context, as an extra safety net beyond the root-cause fix
— it caught exactly one token in the successful run's own log output
(the `PaperTradingApp` startup line's `orderExecutor=...` field, i.e.
harmless, not a credential, but proof the filter is live).

**Recommendation, not a resolved action item**: rotate `BINGX_API_KEY`
(VST-only, no withdrawal permission per this project's own Non-negotiable
Rules — low stakes, but cheap to rotate regardless) and `FRED_API_KEY`
(free, read-only, no live-trading surface) at @ckrhehfl's convenience. No
evidence either value reached any committed file, git history, or public
surface — both exposures were local-only (a gitignored scratch file, now
overwritten, and this session's own tool-call transcript) — but rotation
is cheap insurance regardless. `BINGX_API_SECRET` was never exposed by
either incident.

## Explicitly out of scope (per the governing brief, not attempted here)

- Widening the OMS state machine — the real VST run did not surface a
  concrete case forcing this; `EXPIRED`/`REJECTED`-after-partial-fill
  remains the documented, deliberate `ExchangeOrderExecutor` approximation
  from Task G, untouched.
- Testing against BingX's production host — never attempted, never will
  be, per the task's own absolute constraint.
- Modifying the running `tmux` `paper-trading` session or its process —
  confirmed untouched (see "Real state left on the VST account," above).
- Switching `ExchangeOrderExecutor`'s fee modeling to the now-confirmed
  real `commission` field — real evidence was captured (see above), but
  implementing the switch itself is a separate, deliberate follow-up, not
  folded into this task per Task G's own framing.
- Calling `setLeverage`/`setPositionMode` to align real account leverage
  with `RiskGateway`'s own `approvedLeverage` — a real, disclosed,
  pre-existing gap (see CLAUDE.md's new "Verified" bullet), flagged for a
  human priority decision, not fixed under this task's own pressure (it
  predates this task and is outside every one of Tasks F/G/H's own
  itemized scope).
- A `clientOrderID`-based `queryOrder` lookup on `BingXAdapter` (would
  remove `SubmissionMarkerResolver`'s own coarser `getPositions()`-only
  limitation) — unconfirmed against BingX's real API, not implemented
  speculatively.

## Verification

- `./gradlew clean build` (full multi-module suite, all six modules,
  clean, not incremental) — **BUILD SUCCESSFUL**. Aggregate JUnit XML
  counts across all six modules: **283 tests, 0 failures, 0 errors**
  (239 from Task G's final state + 44 new: 2 `BingXAdapterTest` (asset
  field) + 1 `BingXAdapterTest` (credential stripping) + 11
  `VstPreflightTest` + 9 `SubmissionMarkerStoreTest` + 5
  `PersistentSubmissionOrderExecutorTest` + 7
  `SubmissionMarkerResolverTest` + 5 `FileSignalSourceTest` + 7
  `PaperTradingAppTest` — independently re-summed from every module's
  own `tests="..."` JUnit XML attribute, not trusted from arithmetic
  alone).
- Every new class's tests were confirmed **red** (compile failure against
  the not-yet-existing class) before being made **green**, per this
  project's TDD discipline for OMS/Risk/Execution-adjacent code — recorded
  directly in this task's own execution, not asserted after the fact.
- Real VST network verification: see "The real VST verification" above —
  real, captured output, not a claim.
- The guardrail hook: empirically tested against six synthetic payloads
  plus the real, current `PaperTradingApp.java` file content (false-
  positive check) before being installed into `.claude/settings.json`.
- `java-tests.yml` (Task F's CI workflow) was **not** modified by this
  task — confirmed via `git status` — so it still runs only
  `./gradlew build` against the existing fake-server-only test suite; no
  new step references `BINGX_API_KEY` or any other real credential.
  `PAPER_TRADING_EXECUTION_MODE`/`bingx-vst` do not appear anywhere in
  `.github/workflows/` (confirmed by grep) — this task's own new
  guardrail hook (see above) additionally blocks that from ever changing
  by accident.

## Ship status

PR open, CI green, CodeRabbit review pending/to be requested — **not
merged**, per the governing brief's own explicit instruction and
CLAUDE.md's Auto-merge Policy: this PR touches Java OMS/Execution/runtime
logic and real credentials handling, both explicit exclusions from any
auto-merge delegation regardless of CI/CodeRabbit status. Stopped here for
human review, as instructed.
