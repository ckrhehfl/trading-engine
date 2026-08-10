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
4. **`engine.runtime.VstPreflight`** — a real, tested startup check (see
   its own Javadoc for the full contract): fails closed
   (`IllegalStateException`, never a silent fallback to simulated mode)
   unless `getBalance().asset()` is exactly `"VST"`; logs the real balance
   and warns (informational only, never a hard gate) if it looks small
   relative to canary-tier sizing; calls `getPositions()` and returns
   `killSwitchShouldStartTripped=true` if any non-zero position exists
   (this process has no restart-recovery/reconciliation-against-real-
   positions story anywhere else), skipping the leverage step below
   entirely in that case; when starting clean, actively sets real
   exchange-side leverage (`LONG` and `SHORT`, hedge mode) to
   `RiskLimits.canary().baseLeverage()` — **added after the original
   implementation, in response to a real CodeRabbit review finding**, see
   "CodeRabbit review findings" below; never logs API key/secret anywhere
   (structurally guaranteed — the class is constructed with only an
   `ExchangeAdapter` reference, which exposes no credential accessor).
   14 tests (`VstPreflightTest`) against a hand-written `FakeExchangeAdapter`
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

Confirmed against the real VST host during the original verification run
(see below): `getBalance()` returned `asset="VST"` (passed the fail-closed
check), a real balance, and (at that time) `getPositions()` returned an
empty array (no pre-existing positions, so the kill switch stayed
clean/untripped at that startup).

**Leverage enforcement was added later, in response to CodeRabbit review,
after the original real verification run had already happened -- see
"CodeRabbit review findings" below.** A second, real, minimal verification
run was made specifically to check it (a separate throwaway driver,
`ManualLeverageVerification`, that only constructs `PaperTradingApp
.fromEnvironment()` in `bingx-vst` mode and stops -- no order placed).
**Real result: the account still held the 0.001 BTC LONG position from the
original run** (see "Real state left on the VST account" below -- it was
never closed), so `VstPreflight` correctly found a non-zero position and
skipped leverage enforcement entirely, exactly as designed:

```
VstPreflight: non-zero position(s) found at startup ... Skipping leverage
enforcement -- moot until a human resets the kill switch.
positions=[PositionSnapshot[symbol=BTC-USDT, positionSide=LONG,
positionAmt=0.0010, avgPrice=64881.5, leverage=20, ...]]
```

This is real, valuable, independent confirmation of a *different* part of
the design (`VstPreflight` correctly detecting a real pre-existing
position across a real process restart and tripping the kill switch a
second time, independently) -- but it means **the leverage-enforcement
step's own real `setLeverage` HTTP call has not been independently
verified against the real BingX API in this task** -- only unit-tested
against `FakeExchangeAdapter`. Stated plainly rather than glossed over:
this is a real gap in this task's own verification coverage, not
something to claim otherwise. Re-verifying it for real would require the
account to hold no position first.

**Why the still-open position was not closed to enable that
re-verification: a real, disclosed finding, not a missed opportunity.**
This project's OMS-mediated order path has **no way to close or reduce an
existing position today.** `engine.schemas.Side` has exactly two values,
`LONG`/`SHORT`, and `BingXAdapter` maps each directly to BingX's own
`side`/`positionSide` pair for *opening* exposure in that direction
(`LONG` → `side=BUY, positionSide=LONG`; `SHORT` → `side=SELL,
positionSide=SHORT`) -- confirmed directly against `BingXAdapter
.bingxSide`/`bingxPositionSide`. In hedge mode (this account's own real,
confirmed mode), submitting `Side.SHORT` against a symbol that already has
an open `LONG` position would **not** close or reduce that position -- it
would open a **second, independent SHORT position** alongside it (hedge
mode allows simultaneous LONG and SHORT positions in the same symbol),
leaving the account in a *worse*, more confusing state, not a flatter one.
No `reduceOnly` parameter is sent by `BingXAdapter.submitOrder` either.
Attempting to "flatten" the position through this codebase's own
OMS-mediated path was therefore correctly recognized as something that
would make things worse, not better, and was not attempted -- the
position can only be closed today by a human acting directly (e.g. via the
BingX UI), not through any code path this project currently has. Flagged
here as a real, disclosed gap for a human priority decision (closing
positions is a real capability this project's OMS will eventually need),
not fixed under this task's own pressure -- matches the "flag rather than
redesign" guidance for an out-of-scope discovery.

The 14 `VstPreflightTest` cases (unit, against `FakeExchangeAdapter`)
separately cover: asset mismatch (both a wrong asset and a `null` asset)
→ refuses to start; the refusal message never contains "apikey"/"secret"
(a cheap, direct proof of the "never logs credentials" claim, verified as
an observable contract rather than only by code inspection); a
small-balance case is informational only (does not throw, does not trip);
a non-zero long position and a non-zero short position both force
`killSwitchShouldStartTripped=true` and skip leverage enforcement
entirely; an all-zero-sized position list does not trip, and does proceed
to leverage enforcement; `getBalance()`/`getPositions()` failures both
propagate uncaught (this is a one-shot startup check, not a per-tick call
with `pollFills`'s own "never throw" contract); a `null` adapter/symbol is
rejected; a clean start calls `setLeverage` for both `LONG` and `SHORT`
with the real `RiskLimits.canary().baseLeverage()` value; a `setLeverage`
failure propagates uncaught (fails closed, same as the asset check).

## `SUBMISSION_UNKNOWN` design

The governing plan (and Task G's own planning doc) deferred this entirely
to Task H — see Task G's doc for the full reasoning at the time. Re-read
before starting this task, per the task brief's own instruction.

**This section describes the design as it stands after a real, correctly-
identified CodeRabbit review finding (marked Critical) corrected it — see
"CodeRabbit review findings" below for the original design and exactly
what was wrong with it.** The original version auto-cleared a marker
whenever no matching position existed; that reasoning was wrong (an
accepted-but-still-open order also produces zero matching position, so
auto-clearing on that basis could have permitted a real duplicate order).
The corrected design below never auto-clears at all.

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

**Resolution (`SubmissionMarkerResolver`) never auto-clears any marker.**
Every marker present at startup is always reported unresolved, and the
kill switch always starts tripped when any exist. `ExchangeAdapter.
getPositions()` is still called and logged alongside each marker, but
purely as diagnostic context for whichever human ends up investigating
("was there a matching position at the moment of this restart or not") —
it no longer drives any clear/no-clear decision, because there is no
currently-available signal that can positively rule out "the order was
accepted and is still open/unfilled" (a real position only exists once
quantity is actually filled; an open `LIMIT` order sitting away from the
market produces zero matching position, identically to an order that
never reached the exchange at all). A real resolution requires a human
directly confirming the order's fate (e.g. via the BingX UI) and then
deliberately clearing the marker — not something this class attempts to
automate. `getPositions()`'s own failure is now tolerated (caught,
logged, treated as "no diagnostic context available") rather than
propagated — since it no longer feeds a safety-relevant decision, its own
failure must not block this process from reaching its own safe (tripped)
startup state.

This is coarser than a genuine per-order query would be — it can only
report "does *any* position exist for this symbol," not "did *this
specific* order fill," an accepted limitation given this project's current
single-symbol, low-frequency, effectively-one-order-in-flight-per-symbol
scope (see CLAUDE.md's Current Scope). `BingXAdapter.queryOrder` accepting
a `clientOrderID`-based lookup (common on exchanges in this family) would
let a future version resolve markers for real, but is unconfirmed against
BingX's real API (nothing in CLAUDE.md's "Exchange API Facts" documents
this) — a real, disclosed follow-up, not guessed at or implemented
speculatively here.

**Persistence** (`SubmissionMarkerStore`) is a single JSON file
(`var/live/submission_markers.json`, matching the existing `var/live/
signals/`/`var/live/reports/` convention), loaded fully into memory at
construction and rewritten fully on every `record`/`clear` — deliberately
not a general persistence framework; this project's own single-symbol,
daily-cadence scope means at most a handful of entries are ever live at
once, so "rewrite the whole file" costs nothing in practice. **Fails
closed on any read/parse failure** — only a genuinely missing file is
treated as empty; a corrupt-but-present file throws (`IllegalStateException`)
rather than silently starting empty, since a corrupt file could otherwise
silently discard real, still-unresolved `SUBMISSION_UNKNOWN` state (a real,
correctly-identified CodeRabbit review finding, doubly important once
resolution never auto-clears — losing a marker to a bad read would be the
one remaining way to silently bypass this mechanism entirely). **Writes
are atomic** (temp file + `ATOMIC_MOVE`, with a non-atomic-replace
fallback for `AtomicMoveNotSupportedException`/`FileAlreadyExistsException`),
mirroring `DailyReportGenerator`'s own already-established `.tmp`-then-move
convention in this exact codebase (matched deliberately, not reinvented,
and not CodeRabbit's own literal suggested `FileChannel.force(true)`
addition — see "CodeRabbit review findings" below for why matching the
established convention was chosen instead).

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
needing any exchange-shaped test double. This is a **third class
implementing `OrderExecutor`**, alongside `PaperBroker` and
`ExchangeOrderExecutor` — a real CodeRabbit review finding correctly
caught this against the "exactly two implementations" invariant `
OrderExecutor`'s own Javadoc states; resolved by explicitly documenting a
venue-agnostic-decorator exception to that invariant (see "CodeRabbit
review findings" below and `OrderExecutor`'s own updated Javadoc), not by
restructuring this class.

**Required-scenario mapping** (task brief's three named tests):

| Brief's scenario | Test |
|---|---|
| Post-acceptance timeout (order was accepted; must not auto-resubmit) | `SubmissionMarkerResolverTest#aMarkerWithAMatchingNonZeroPositionIsAlsoUnresolvedAndNotCleared` |
| Pre-acceptance failure (never accepted) — see below for why this no longer auto-clears | `SubmissionMarkerResolverTest#aMarkerWithNoMatchingPositionIsStillTreatedAsUnresolvedNeverAutoCleared` |
| Marker still unresolved across a simulated restart (must not be silently dropped or retried) | `SubmissionMarkerStoreTest#recordPersistsAMarkerVisibleToAFreshInstanceAgainstTheSameFile` + `SubmissionMarkerResolverTest`'s own unresolved-marker cases, none of which ever clear the store |

**A real, disclosed deviation from the brief's literal wording, revised
after CodeRabbit review**: the brief names a "pre-acceptance failure...
safe to clear" case, distinct from "post-acceptance timeout." The
corrected design (this section) treats both identically — always
unresolved, never cleared — because, as explained above, there is no
reliable signal available to this codebase today that actually
distinguishes the two cases. The brief's own named distinction was a
reasonable design target that real analysis (prompted by CodeRabbit's
review) found isn't actually achievable safely with `getPositions()` as
the only available signal; treating both as unresolved is the honest,
safe resolution, not a shortcut.

**Wiring**: `PaperTradingApp.forBingXVst` calls `SubmissionMarkerResolver
.resolve` immediately after `VstPreflight.run`, before constructing the
real `OrderExecutor` graph — any unresolved marker forces the same
`killSwitchShouldStartTripped` outcome as a non-zero `VstPreflight`
position, for the same reason (unknown state this process must not begin
normal tick-driven trading against).

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
ExchangeOrderExecutor/PersistentSubmissionOrderExecutor → BingXAdapter`)
against the real VST host `open-api-vst.bingx.com`: a real `PaperTradingApp` was
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
{"code":0,"msg":"","data":[{"userId":"<redacted-real-account-id>","asset":"VST","balance":"96224.6140","equity":"96224.6140","unrealizedProfit":"0.0000","realizedProfit":"0","availableMargin":"96224.6140","usedMargin":"0.0000","frozenMargin":"0.0000","shortUid":"<redacted-real-account-id>"}]}
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
  filled `GUARDED_MARKET` order above. **Not flattened** — and, per the
  real finding recorded above under "`VstPreflight` real behavior," this
  codebase's OMS-mediated order path currently has **no way to close or
  reduce a position at all** (attempting it via `Side.SHORT` would open a
  second, independent position in hedge mode, not close this one) — so
  this was never a live option through this project's own code, only
  directly via the BingX UI. Left for @ckrhehfl to close that way, or to
  leave as-is. A real consequence, confirmed **twice**, working exactly as
  designed: both the original run and a later, separate real re-run (made
  to check the leverage-enforcement fix below) independently found
  `VstPreflight` correctly detecting this non-zero position and starting
  with the kill switch already tripped, requiring a deliberate human
  reset — the intended safety behavior, not a bug, now confirmed
  reproducible across two independent real process starts, not a
  one-off.
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
- **`setLeverage` was originally deferred here, then actually built** —
  see "CodeRabbit review findings" below: a real CodeRabbit review finding
  correctly identified the real leverage gap as worth fixing now rather
  than only flagging, and `VstPreflight` now calls it for real on every
  clean `bingx-vst` start. `setPositionMode` remains genuinely out of
  scope — hedge mode is already the real, confirmed default (see
  CLAUDE.md's "Verified" section), so there is nothing to actively set.
- Building a position-closing/reducing order path — a real, disclosed,
  newly-confirmed gap (see "`VstPreflight` real behavior" above): this
  codebase's OMS-mediated path can only *open* exposure in either
  direction (`Side.LONG`/`Side.SHORT`), never close or reduce an existing
  position. Flagged for a human priority decision, not built under this
  task's own pressure — this task's own real VST run needed it (to
  re-verify leverage enforcement against a flat account) but correctly
  did not attempt it once the real risk of making things worse (opening a
  second, independent hedge-mode position instead of closing the first)
  was found.
- A `clientOrderID`-based `queryOrder` lookup on `BingXAdapter` (would
  remove `SubmissionMarkerResolver`'s own coarser `getPositions()`-only
  limitation) — unconfirmed against BingX's real API, not implemented
  speculatively.

## CodeRabbit review findings, round 1

One review round on PR #79 (`ASSERTIVE` profile), 12 actionable comments
against the original push. Per this project's own established practice:
verified each finding against the real current code before deciding, fixed
what was real, declined the rest with recorded reasoning — nothing
accepted or dismissed on the review's word alone.

**Fixed, this PR, with reasoning:**

- **(Critical) `SubmissionMarkerResolver` auto-cleared a marker on "no
  matching position," which is not actually proof the order never
  reached the exchange.** The single most important finding in this
  round — see "`SUBMISSION_UNKNOWN` design" above for the full corrected
  design. An accepted-but-still-open order (e.g. a real `LIMIT` order
  resting away from the market) also produces zero matching position,
  identically to an order that never reached the exchange at all — there
  is no currently-available signal that distinguishes the two. Auto-
  clearing on "no position" could therefore have permitted a fresh
  resubmit of an order that was, in fact, already live — a real
  duplicate-order risk, the exact failure mode this whole mechanism
  exists to prevent. Fixed by removing the auto-clear path entirely:
  every persisted marker is now always reported unresolved, and
  `getPositions()` is retained purely as diagnostic logging, no longer a
  decision input. `SubmissionMarkerResolverTest` rewritten accordingly
  (8 tests, all verifying "unresolved, never cleared" across every
  scenario the original tests covered plus a `getPositions()`-failure-
  tolerated case).
- **`SubmissionMarkerStore` treated a corrupt/unreadable marker file as
  empty, and writes were not atomic.** Doubly important once the
  critical finding above means a marker is never auto-cleared — losing a
  marker to a bad read would become the one remaining way to silently
  bypass the whole mechanism. Fixed: only a genuinely missing file
  ({@code NoSuchFileException}) is now treated as empty; any other read
  or parse failure throws (`IllegalStateException`), matching this
  class's own existing write-failure convention rather than introducing
  a new one. Writes now go through a temp-file-then-`ATOMIC_MOVE`
  sequence with a non-atomic-replace fallback for
  `AtomicMoveNotSupportedException`/`FileAlreadyExistsException` —
  **deliberately matching `DailyReportGenerator`'s own already-
  established convention in this exact codebase**, not CodeRabbit's own
  literal suggested `FileChannel.force(true)` addition (an fsync this
  project's one existing precedent for atomic writes doesn't do either —
  matching established convention was judged more valuable than a
  stricter-but-novel-in-this-codebase guarantee). `SubmissionMarkerStoreTest`
  gained an `AtomicMover` test seam (mirroring `DailyReportGenerator`'s
  own identical pattern) and tests for: fail-closed on corruption, no
  leftover `.tmp` file after a successful write, and the non-atomic
  fallback still persisting correctly.
- **`FileSignalSource`'s own delivered-marker write was not atomic** (the
  same class of issue as above, for a different file). Fixed identically
  — temp file + `ATOMIC_MOVE` + fallback, a new `AtomicMover` test seam,
  and tests for no-leftover-tmp-file and fallback-still-persists.
- **Real exchange-side leverage was never enforced, confirmed by this
  task's own real VST run to sit at `20X` against `RiskGateway`'s own
  `1x`-approved canary tier.** `VstPreflight` now actively sets real
  leverage (`LONG` and `SHORT`, hedge mode) to `RiskLimits.canary()
  .baseLeverage()` on every clean start, skipping this step entirely (and
  logging why) when a non-zero position already exists. Fails closed
  (propagates) on a `setLeverage` failure, matching the asset check's own
  precedent. See "`VstPreflight` real behavior" above for the honest
  account of what was and wasn't independently re-verified against the
  real API for this specific fix (the account's own still-open position
  from the original run blocked a clean re-verification — a real,
  disclosed, newly-found gap of its own: this codebase's OMS-mediated
  path cannot close or reduce a position at all).
- **`PersistentSubmissionOrderExecutor` is a third class implementing
  `OrderExecutor`, against `OrderExecutor`'s own documented "exactly two
  implementations" invariant.** A real, correctly-identified inconsistency
  — this task's own new code violated a rule this same task's planning
  doc restates. Resolved by amending the invariant, not restructuring the
  code: `OrderExecutor`'s own Javadoc and CLAUDE.md's Architecture section
  now both explicitly carve out an approved exception for venue-agnostic
  **decorators** (a class implementing `OrderExecutor` purely to add a
  cross-cutting concern around *any* wrapped `OrderExecutor`, with zero
  venue knowledge of its own) — the original invariant's real purpose was
  preventing venue-specific submission logic from leaking outside
  `ExchangeAdapter`, which a venue-agnostic decorator does not do.
  Restructuring `PersistentSubmissionOrderExecutor` to avoid implementing
  the interface at all (CodeRabbit's own suggested fix — folding it into
  `ExchangeOrderExecutor` itself) was considered and declined: it would
  require moving persistence classes across the `:execution`/`:runtime`
  module boundary and coupling `ExchangeOrderExecutor` to a concrete
  persistence mechanism it has no other reason to know about, a larger
  and riskier change than documenting the real, narrower distinction the
  invariant was actually protecting. `FakeOrderExecutor` (test-only, in
  `:runtime`'s own test sources, never shipped) was left as-is on the
  same reasoning — it is not a production implementation at all.
- **The real VST balance response pasted into this planning doc included
  real account identifiers (`userId`, `shortUid`).** A real, valid
  finding — CLAUDE.md's own Non-negotiable Rules prohibit committing raw
  trading logs with account identifiers. Fixed: both replaced with
  `<redacted-real-account-id>` in the captured JSON above. The real
  balance/equity figures themselves were kept — CLAUDE.md's own Task H
  brief explicitly names the balance number itself as safe to log/record
  ("not the credentials — the balance number itself is fine to log, it's
  account state, not a secret"), and it is real evidence this task's own
  brief asked to capture, not an identifier.
- **The guardrail hook's workflow-path match didn't normalize Windows-
  style backslash paths, and its VST-host-env-var check only inspected
  this one edit's own diff fragment, not the resulting file** (so a
  sensitive assignment split across two separate Edit calls could evade
  it). Both real, valid robustness gaps. Fixed: backslashes are now
  normalized to forward slashes before the workflow-path match; the
  env-var check now reconstructs the actual resulting file content (the
  current on-disk file with `old_string` replaced by `new_string`, or
  `content` directly for a `Write`) and checks for `BINGX_VST_BASE_URL`/
  `getenv` co-occurring within the same semicolon-delimited Java
  statement — not a fixed line-window (which was tried first and found to
  false-positive against this project's own real code: `BINGX_VST_BASE_URL`'s
  real declaration in `PaperTradingApp.java` legitimately sits only 1-2
  lines from unrelated `getenv` calls for `BINGX_API_KEY`/
  `BINGX_API_SECRET` in the same construction method) and not a naive
  whole-file check (which false-positived on this class's own Javadoc,
  which legitimately discusses both terms in prose). Comments are now
  stripped before analysis for the same reason. Given the resulting
  script's real complexity (an embedded Python analysis step), it was
  moved out of the single-JSON-string-command pattern the existing
  `bingx-hostname-guard` hook uses into two real files
  (`.claude/hooks/vst-guardrail.sh`, `.claude/hooks/vst_guardrail_check.py`)
  — triple-escaping this much logic into one JSON string was judged a
  real correctness risk of its own, not a style preference. Re-tested
  against all original scenarios plus the new false-positive cases (the
  real file, a genuine 2-call split-edit attack, a normal unrelated edit)
  before installing.

**Declined, with reasoning, not attempted here:**

- **"Wire the persisted marker path into the VST runtime — `PaperTradingApp`'s
  path uses `new FileSignalSource(signalPath)` [the 1-arg, no-marker
  constructor]."** Verified against the real, current code and found to
  be a false positive: `forBingXVst` constructs `PaperTradingApp` via the
  `OrderExecutor`-accepting 7-arg constructor (not the 6-arg `Clock`
  overload), which — confirmed by direct reading, not just this
  assertion — always builds `FileSignalSource` **with** the marker-file
  variant (`new FileSignalSource(signalPath,
  signalPath.resolveSibling("delivered.marker"))`). Only the `simulated`-
  mode path (the 6-arg `Clock` overload, never reached from
  `forBingXVst`) uses the no-marker constructor, which is the correct,
  zero-behavior-change requirement for that mode. Additionally confirmed
  by a real, already-passing test,
  `PaperTradingAppTest#orderExecutorAcceptingConstructorPersistsTheDeliveredSignalMarkerFile`.
- **"`FileSignalSource.nextSignal()` records the delivered marker before
  `OrderPipeline.submitIntent()`/`RiskGateway` evaluation completes, so a
  Risk-Gateway-rejected (or process-crash-interrupted) intent is
  permanently suppressed even though no order was ever created."** Real
  and correctly identified as a genuine scope question, but declined as
  a fix here, for two reasons. First, it is not a new behavior this task
  introduced: the pre-existing, unchanged in-memory `lastDeliveredIntentId`
  pointer already has this exact property (set inside `nextSignal()`,
  before `TradingLoop` ever processes the result) — this task's own
  change only makes that same, already-accepted scope **durable**, not
  different in kind. Second, and more importantly: unlike the critical
  finding above, this is a **correctness/opportunity-cost** concern (a
  legitimate signal that could have been retried after a transient
  rejection is instead lost until the next real signal), not a **safety**
  concern (no scenario here produces a real duplicate order — the whole
  point of the fix in "What was built" item 6 is durability specifically
  for the *accepted-and-submitted* case). A real fix would require
  extending the `SignalSource` interface itself (shared by `FileSignalSource`
  and `DummySignalSource`) with a "confirm processed" callback `TradingLoop`
  would need to call after a successful submission — real, valid,
  "Heavy lift"-scoped (CodeRabbit's own label) design work touching a
  shared interface, not a narrow bug fix, and not something to take on
  under this task's own review-response pressure. Flagged here as a real,
  disclosed, scoped follow-up rather than silently dropped.

**Final state, after all fixes above, same push (not separately re-
requested per-fix, per this project's own CodeRabbit rate-limit
guidance):** `./gradlew clean build` — **293 tests, 0 failures, 0 errors**.

## CodeRabbit review findings, round 2

A second, real review landed against commit `481eecf` (round 1's own
fixes) — 3 new actionable comments, all real. This round's own process
went further than round 1's: every still-open thread from round 1 (not
just the 3 new ones) was re-verified against the real current code, and
every genuinely-fixed thread was replied to with real evidence and
explicitly resolved via the GitHub API — not just described here. Full
detail below; the concrete thread-by-thread record (comment IDs, replies)
lives in the PR itself, not duplicated here.

**Fixed, this round, with reasoning:**

- **(Critical) The guardrail hook's comment-stripping regex stripped `//`
  even inside a string literal**, e.g. `"https://" + System.getenv(
  "BINGX_VST_BASE_URL")` — the old `//[^\n]*` regex treated the `//` in
  `"https://"` as a line-comment start, erasing the real `getenv(...)`
  call and the `BINGX_VST_BASE_URL` string that followed, causing a false
  `NOMATCH` (a real bypass of Guardrail B). Fixed by replacing the regex
  with a real, minimal state-machine lexer (`strip_java_comments` in
  `vst_guardrail_check.py`) that tracks string/char-literal state
  (respecting `\"`/`\'` escapes) before ever treating `//`/`/*` as
  comment-introducing. A new file, `test_vst_guardrail_check.py` (22
  tests, stdlib `unittest`, zero extra dependencies — run via `python3
  .claude/hooks/test_vst_guardrail_check.py`), covers this directly
  (`test_the_real_bypass_payload_from_the_coderabbit_finding_is_now_blocked`
  uses the exact payload shape from the review comment) plus escaped
  quotes, char literals, multiline block comments, and confirms real
  comments/Javadoc prose are still correctly stripped. Re-verified the
  false-positive check against the real, current `PaperTradingApp.java`
  — still not blocked.
- **(Critical) Guardrail A (the CI-workflow check) only ever checked the
  raw `new_string` diff fragment, not the resulting file** — a real,
  correctly-identified gap: an earlier round already gave Guardrail B
  (the VST-host check) candidate-reconstruction (reading the real current
  file and applying `old_string`→`new_string`), but Guardrail A had not
  received the same treatment, so a forbidden token split across two
  separate `Edit` calls into a workflow file would have passed both
  individually. Fixed by unifying both guardrails into one Python script
  sharing a single `reconstruct_candidate()` path — `vst-guardrail.sh` is
  now a thin wrapper interpreting `vst_guardrail_check.py`'s
  `OK`/`BLOCK_WORKFLOW`/`BLOCK_JAVA` output, rather than two independently
  -evolving code paths that could drift apart again. Given the resulting
  script's real complexity (a real lexer, shared reconstruction logic), it
  no longer fits safely as a single triple-escaped JSON string command —
  moved entirely into two real files (`vst-guardrail.sh`,
  `vst_guardrail_check.py`), a correctness choice, not a style one.
  Regression test:
  `ReconstructCandidateSplitEditTest#test_a_forbidden_token_split_across_two_edits_is_detected_on_the_second`,
  which writes a real temp `.github/workflows/check.yml`, simulates a
  first `Edit` already applied to disk, then confirms a second, separate
  `Edit` completing the forbidden pattern is caught against the
  reconstructed full file.
- **(Minor) `SubmissionMarkerStoreTest#defaultAtomicMoverUsesRealAtomicMove`
  didn't actually exercise the production default** — it re-implemented
  the same `ATOMIC_MOVE` logic as `SubmissionMarkerStore::defaultAtomicMove`
  via a lambda injected through the `AtomicMover` test seam, so the
  production default path itself was never called; if `ATOMIC_MOVE` were
  ever accidentally removed from `defaultAtomicMove`, this test would
  still have passed. Fixed exactly as suggested: renamed to
  `defaultAtomicMoverPersistsWithoutTheTestSeam`, now uses the 1-arg
  constructor (the real production path), with the no-leftover-`.tmp`-file
  assertion added and the now-unused `StandardCopyOption` import removed.

**A fourth, real architectural finding raised again in round 2 (repeating
round 1's own already-fixed-by-documented-exception item, this time
correctly rejecting that resolution) drove a genuine redesign, not just a
reply:** `PersistentSubmissionOrderExecutor` and the test-only
`FakeOrderExecutor` were both still flagged as violating `OrderExecutor`'s
"exactly two implementations" invariant — round 1's own response had
amended the invariant's documentation to carve out a "venue-agnostic
decorator" exception rather than change the code. On renewed pressure
(via this session's own coordinating review, citing the same real
CodeRabbit finding), that resolution was re-examined and found to be the
wrong call: a documented exception is a weaker guarantee than a design
that makes the violation structurally impossible, and the actual fix was
not as costly as first estimated. **Real redesign, this round:**

- New `engine.execution.SubmissionListener` interface (`beforeSubmit`/
  `afterSubmitSucceeded`) — lets a caller observe (and durably record)
  submission-outcome ambiguity without `ExchangeOrderExecutor` needing to
  know anything about persistence or any specific venue.
- `ExchangeOrderExecutor` gained a new 3-arg constructor overload
  (`ExchangeAdapter`, `feeBps`, `SubmissionListener`) — the existing 2-arg
  constructor is byte-for-byte unchanged in behavior (delegates to the new
  one with a `NO_OP` listener), so every one of Task G's own existing
  `ExchangeOrderExecutorTest` cases needed zero changes. `submit()` now
  calls `listener.beforeSubmit(order)` immediately before
  `adapter.submitOrder`, and `listener.afterSubmitSucceeded(order)`
  immediately after it returns normally — **never** if it throws, which is
  exactly the ambiguity a marker recorded in `beforeSubmit` needs to
  survive. Five new tests in `ExchangeOrderExecutorTest` cover the full
  contract, including a real submit-throws case (via `FakeExchangeAdapter
  #throwOnNextSubmit`) proving `afterSubmitSucceeded` is correctly
  skipped.
- New `engine.runtime.MarkerRecordingSubmissionListener` — a plain
  collaborator (implements `SubmissionListener`, **not** `OrderExecutor`)
  that delegates directly to `SubmissionMarkerStore#record`/`#clear`. Four
  tests cover its own delegation contract (all the real
  persistence/atomicity/fail-closed logic is already covered by
  `SubmissionMarkerStoreTest`, so these are intentionally thin).
- `PersistentSubmissionOrderExecutor.java` and its test file were
  **deleted entirely** (`git rm`), not kept alongside the new design.
  `PaperTradingApp.forBingXVst` now constructs `new ExchangeOrderExecutor
  (adapter, FEE_BPS, new MarkerRecordingSubmissionListener(markerStore))`
  directly as the `OrderExecutor` — no wrapping decorator.
- `FakeOrderExecutor.java` (test-only, `:runtime`) was **also deleted**.
  `PaperTradingAppTest`'s two tests that used it now inject a real
  `PaperBroker` instance (one of the two canonical `OrderExecutor`
  implementations) and assert reference identity
  (`assertSame(injectedExecutor, app.orderExecutor())`) instead of a hand-
  written fake's own call count — proving the exact injected instance is
  used without needing a third test-only implementation at all.
- `OrderExecutor.java`'s own Javadoc and CLAUDE.md's "`OrderExecutor`/
  `ExchangeAdapter` layering rule" paragraph were both **reverted** to the
  strict, unqualified "exactly two implementations, full stop" wording —
  the "approved exception: venue-agnostic decorators" paragraph added in
  round 1 is gone; `SubmissionListener` is documented as the corrected
  design, not a carved-out exception to keep.

**Declined, with reasoning, not attempted here (repeated from round 1,
re-verified and re-affirmed in round 2, thread left open — not
resolved):**

- **"Wire the persisted marker path into the VST runtime."** Re-verified
  against the real, current code a second time and confirmed, again, to
  be a false positive: `forBingXVst` uses the `OrderExecutor`-accepting
  constructor, which always wires the marker-file variant of
  `FileSignalSource`. Replied on the GitHub thread with the exact code
  reference and test name, and resolved the thread via the API (round 1
  had only recorded this reasoning in this document, not on the actual
  PR thread — a real gap in round 1's own process, corrected this round).
- **"Don't record the delivered marker before order submission
  completes."** Same reasoning as round 1 (see above) — replied on the
  GitHub thread with the same reasoning, restated, and **left the thread
  open** (not resolved) since it's a genuine, acknowledged trade-off, not
  something to assert closed by fiat.

**A real, disclosed process gap from round 1, corrected this round:**
round 1's own fixes were only ever described in this planning document —
none of round 1's 12 review threads were replied to or resolved via the
GitHub API itself, leaving them all showing as open/unaddressed in the
PR's own UI regardless of what this document said. This round replied to
and resolved (or, for the one genuinely-declined item, replied to and
deliberately left open) every one of round 1's own threads in addition to
the 3 new ones — 14 of 15 total review threads on this PR are now
resolved, with the one remaining open thread carrying a real, recorded
reply explaining why.

**Final state, after all round 2 fixes, one push (not separately
re-requested per-fix):** `./gradlew clean build` — **297 tests, 0
failures, 0 errors**; `python3 .claude/hooks/test_vst_guardrail_check.py`
— **22 tests, 0 failures, 0 errors**.

## Verification

- `./gradlew clean build` (full multi-module suite, all six modules,
  clean, not incremental) — **BUILD SUCCESSFUL**. Aggregate JUnit XML
  counts across all six modules: **300 tests, 0 failures, 0 errors**
  (239 from Task G's final state + real, net new/changed tests across all
  three CodeRabbit review rounds; independently re-summed from every
  module's own `tests="..."` JUnit XML attribute after all fixes, not
  trusted from arithmetic alone). `PersistentSubmissionOrderExecutorTest`
  and `FakeOrderExecutor`/its own indirect test coverage no longer exist
  (both deleted, round 2) — replaced by `ExchangeOrderExecutorTest`'s eight
  new `SubmissionListener`-contract tests (five from round 2, three more
  from round 3's `afterSubmitSucceeded`-ordering fix) and
  `MarkerRecordingSubmissionListenerTest`'s four tests.
- `python3 .claude/hooks/test_vst_guardrail_check.py` (stdlib `unittest`,
  zero extra dependencies) — **27 tests, 0 failures, 0 errors** (22 from
  round 2 + 5 from round 3: the `getProperty`/chained-`getenv().get()`
  cases and the unrecognized-payload fail-closed cases), covering the
  comment-stripping lexer, both guardrails, the split-edit
  candidate-reconstruction fix, and (round 3) the `getProperty` bypass fix
  and the fail-closed-on-unrecognized-payload fix directly, not just via
  the shell-level end-to-end scenarios below.
- Every new/revised class's tests were confirmed **red** (compile failure
  against the not-yet-existing class or not-yet-changed method signature,
  or a real failing assertion against a real thrown exception for round
  3's `afterSubmitSucceeded`-ordering fix) before being made **green**,
  per this project's TDD discipline for OMS/Risk/Execution-adjacent code —
  recorded directly in this task's own execution, in the original
  implementation and in every CodeRabbit-review fix across all three
  rounds, not asserted after the fact.
- Real VST network verification: see "The real VST verification" and
  "`VstPreflight` real behavior" above — real, captured output (including
  a second, later real run specifically re-checking the leverage-
  enforcement fix), not a claim.
- The guardrail hook: empirically tested against the original six
  synthetic payloads, the real, current `PaperTradingApp.java` file
  content (false-positive check, re-run after each rewrite), a real 2-call
  split-edit attack scenario for both guardrails, and a normal unrelated
  real edit (a second false-positive check) — all before installing into
  `.claude/settings.json`, on top of the 22 unit tests above.
- All 15 CodeRabbit review threads on the PR were individually verified
  against real current code; 14 replied-to-and-resolved via the GitHub
  API with real evidence (code references, test names, or real command
  output), 1 replied-to-and-deliberately-left-open with recorded
  reasoning — not just described in this document.
- `java-tests.yml` (Task F's CI workflow) was **not** modified by this
  task — confirmed via `git status` — so it still runs only
  `./gradlew build` against the existing fake-server-only test suite; no
  new step references `BINGX_API_KEY` or any other real credential.
  `PAPER_TRADING_EXECUTION_MODE`/`bingx-vst` do not appear anywhere in
  `.github/workflows/` (confirmed by grep) — this task's own new
  guardrail hook (see above) additionally blocks that from ever changing
  by accident.
- `gitleaks detect` — clean against the full worktree and every commit on
  this branch (one real false-positive round-tripped and fixed during
  this task: the real `clientOrderID` UUID values captured in this
  planning doc's raw JSON evidence tripped gitleaks' `generic-api-key`
  heuristic on entropy alone; resolved with inline `gitleaks:allow`
  markers plus an explanatory comment on each flagged line, not by
  altering or removing the real captured evidence).

## CodeRabbit review findings, round 3

A third, real CodeRabbit review landed against the exact round-2 HEAD
commit (`63b0427`, verified via the GitHub reviews API's own
`commit_id` field, not assumed) — `CHANGES_REQUESTED`, 5 actionable
comments. Each was independently verified against current code before
any fix, not taken on faith:

1. **CLAUDE.md's leverage-enforcement text was genuinely stale**
   (Major). It still read "nothing in this codebase calls `POST
   /openApi/swap/v2/trade/leverage` yet" and "`VstPreflight`'s four
   steps... deliberately never call `setLeverage`" — both were true
   when originally written but became false the moment round 1 added
   real leverage enforcement to `VstPreflight` (now 5 steps, not 4,
   confirmed by reading the current file). Fixed by rewriting the
   passage to describe the real current behavior: `VstPreflight` sets
   `LONG`/`SHORT` leverage to `RiskLimits.canary().baseLeverage()` on
   every clean start, skips enforcement entirely (and trips the kill
   switch) when a pre-existing position is found, and fails closed if
   either `setLeverage` call itself fails. Also disclosed, newly and
   explicitly in this same passage: the real HTTP call itself is still
   unverified against the live BingX API (only against a hand-written
   fake in `VstPreflightTest`), because the account still holds the
   original verification run's position and this codebase's OMS has no
   way to close/reduce it.

2. **A real orphan-risk bug in `ExchangeOrderExecutor.submit`** (Major,
   data integrity). `submissionListener.afterSubmitSucceeded(order)` ran
   *before* `pendingOrders.put(...)` — so if a real
   `MarkerRecordingSubmissionListener`'s marker-clear failed (a real
   `IllegalStateException` path in `SubmissionMarkerStore.clear()` on
   I/O failure), the exception propagated out of `submit()` before the
   already-exchange-acknowledged order was ever registered for
   poll/fill/cancel tracking — and `TradingLoop#submitToBroker` logs
   exactly that scenario as "orphaned, will never receive a fill" and
   rethrows, which `Reconciler`/`PaperTradingApp#reconcile()` would then
   treat as `ORPHANED_IN_BROKER` and trip the kill switch, for an order
   that was in fact real, live, and fillable — only its own durable
   marker failed to clear. Fixed for real: `pendingOrders.put(...)` now
   runs immediately after `adapter.submitOrder` returns (before
   `afterSubmitSucceeded`), and a thrown `afterSubmitSucceeded` is now
   caught and logged at ERROR rather than rethrown, since by that point
   the order is already known-live and already tracked. `SubmissionListener`'s
   own Javadoc rewritten to state this throwing contract precisely (it
   previously said "not expected to throw", which was never actually
   true of a real persistence-backed implementation). Three new tests in
   `ExchangeOrderExecutorTest`: `afterSubmitSucceededThrowingDoesNotPreventTheOrderFromEnteringPendingTracking`,
   `afterSubmitSucceededThrowingIsLoggedButDoesNotPropagateOutOfSubmit`,
   `afterSubmitSucceededThrowingStillAllowsANormalPollToFillTheOrder` —
   confirmed red (the exact `IllegalStateException` from the throwing
   test double propagated uncaught) before the fix, green after.

3. **`System.getProperty("BINGX_VST_BASE_URL")` was a real, undetected
   bypass of Guardrail B** (Major, part of the "outside diff range"/path
   findings). The pre-fix check only searched for the literal substring
   `"getenv"`, so `System.getProperty(...)` — a real, different JVM
   configuration surface (settable via a `-D` flag) that CLAUDE.md's own
   "no environment variable, argument, or other configuration surface"
   wording already covers in spirit — passed through untouched. Fixed by
   checking for either `"getenv"` or `"getproperty"` (case-insensitive)
   co-occurring with `BINGX_VST_BASE_URL` in the same statement. The
   already-suspected-covered "chained `System.getenv().get(...)` form"
   was verified, not assumed, to already have been caught by the
   existing same-statement substring check (both substrings already
   co-occur) — its own new regression test
   (`test_still_blocks_the_chained_system_getenv_get_form`) confirms
   this rather than leaving it asserted only in prose.

4. **`reconstruct_candidate` silently returned `""` for a payload shape
   it could not recognize** (Major/quick-win). A hypothetical multi-edit
   `edits`-array payload, or any `tool_input` with none of `content`/
   `old_string`+`new_string`, fell through to `new_string or ""` —
   returning an empty string both guardrails then check and always pass
   against, i.e. the guardrail silently no-opped rather than failing
   closed for a shape it could not actually analyze. Fixed:
   `reconstruct_candidate` now returns `None` for any such shape, and
   `main()` prints a new `BLOCK_UNRECOGNIZED_PAYLOAD` decision that
   `vst-guardrail.sh` now handles with its own explicit message
   (previously would have hit its already-existing fail-closed catch-all
   case with a more generic message — still correct, now clearer).
   Matches this project's own established fail-closed convention (e.g.
   `SubmissionMarkerStore.load()`) rather than a new one invented for
   this fix. Three new tests in a new `ReconstructCandidateUnrecognizedPayloadTest`
   class, including one that runs the real script as a subprocess and
   asserts its stdout is never `"OK"` for this shape.

5. **The Python test module's own documented run command didn't
   actually work** (minor/quick-win). The docstring said `python3 -m
   unittest .claude/hooks/test_vst_guardrail_check.py`; running it for
   real produces `ValueError: Empty module name`, because `-m unittest`
   treats its argument as a dotted module/test name, not a file path.
   Fixed by correcting the docstring to the form that actually works and
   that `vst-guardrail.sh`'s own comment already documents: `python3
   .claude/hooks/test_vst_guardrail_check.py`.

**Not acted on** (from the review's own "Autofix" prompt block, which
lists every open comment regardless of whether it's independently
verified as still valid — same standard applied in round 2): nothing
this round was found invalid on inspection: all 5 actionable comments
were verified as real and fixed as described above, not partially or
selectively.

**Final state, after all round-3 fixes:** `./gradlew clean build` — **300
tests, 0 failures, 0 errors** (297 + 3 new `ExchangeOrderExecutorTest`
cases); `python3 .claude/hooks/test_vst_guardrail_check.py` — **27 tests,
0 failures, 0 errors** (22 + 5 new: 2 `getProperty`/chained-form cases, 3
unrecognized-payload cases). `npx markdownlint-cli2` re-run against this
same planning doc after these edits: the MD018 (heading misparse) and all
5 MD038 (space-inside-code-span, actually one multi-line unclosed-code-span
bug producing 5 cascaded false positives, root-caused and fixed with a
single edit) findings are resolved; the remaining MD013/MD040/MD060
findings are the same pre-existing project-wide norms already confirmed
present in Task G's own accepted planning doc.

## Ship status

Round 3 (this section, current): a third, real CodeRabbit review landed
against the round-2 HEAD commit with 5 actionable comments, all
independently verified as real (not assumed from the review's own
prose) and fixed for real per "CodeRabbit review findings, round 3"
above, TDD throughout. Round-3 changes are being committed and pushed
next; a fresh CodeRabbit review against the new HEAD is the remaining
step before this task is reportable as genuinely, fully green. PR
remains **not merged**, per the governing brief's own explicit
instruction and CLAUDE.md's Auto-merge Policy: this PR touches Java
OMS/Execution/runtime logic and real credentials handling, both
explicit exclusions from any auto-merge delegation regardless of
CI/CodeRabbit status. Stopped here for human review, as instructed
originally and reaffirmed by the coordinator's round-2 message.
