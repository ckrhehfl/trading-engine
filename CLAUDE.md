# CLAUDE.md

## Project Identity

Personal, institution-style BTC/USDT futures trading system. The system may
eventually place real trades. Treat all execution, risk, leverage, position
mode, exchange API, deployment, and key-management changes as high-risk.

## Current Scope (MVP)

- Exchange: BingX (first implementation, not a hardcoded assumption)
- Product: BTC/USDT USDT-M Perpetual Futures
- Direction: long and short
- Order types: limit and guarded market
- Timeframes: 15m base, 5m extension, 1h regime filter
- Single-user, VPS-oriented, not a SaaS

## Long-term Design Targets (shape the architecture now, not built now)

- Multi-exchange / multi-symbol / equities expansion **without refactoring**
  OMS, Risk Gateway, or Execution — achieved by keeping `ExchangeAdapter` an
  interface from the start. BingX is the first adapter, not a baked-in
  assumption.
- Target latency ~100-200ms round trip on a single non-colocated VPS. This
  is not HFT — achievable with the Java trading plane + WebSocket market
  data, no exotic messaging infra needed.
- Eventually fully unattended 24/7 operation. Requires real process
  supervision (restart recovery, health checks) first — not there yet.
- Eventually automatic parameter re-learning. Requires a scheduled
  retraining pipeline with validation gates before any auto-promotion — not
  there yet. Any live-affecting promotion still requires human approval
  (see LLM Usage Policy).

Non-goals regardless of the above: HFT/co-location/tick-level strategies,
multi-user SaaS, Kubernetes, Kafka/Aeron/Chronicle Queue.

## Architecture

```text
Python Research Plane
- data research, deterministic backtesting, strategy experiments
- feature engineering, ML training/evaluation, scheduled retraining (later)
- report generation, deployment candidate generation
- must not place live orders directly

Java Trading Plane
- OMS, Risk Gateway, Execution Service
- ExchangeAdapter interface (BingX is the first implementation)
- position reconciliation, kill switch, paper/live runtime
- all live orders must pass through the Java Risk Gateway
```

Java scope is intentionally narrow: OMS / Risk / Execution / Exchange
Adapter / Reconciliation / Kill Switch only. No Spring/Kafka/K8s/Aeron.
Start with Java 21 + Gradle + JUnit + Jackson + SLF4J only. Strategy
research, backtesting, ML, and reporting stay in Python.

A new venue or asset class means writing a new `ExchangeAdapter`
implementation, not modifying OMS/Risk/Execution. Shared schemas
(order-intent, risk-decision, etc.) should stay exchange- and
asset-class-agnostic where practical.

Reassess the Python/Java split if: solo-dev burden becomes excessive, a
Python prototype proves sufficient on its own, or Python/Java schema drift
keeps recurring.

**`OrderExecutor`/`ExchangeAdapter` layering rule** (added when the BingX
VST integration effort gave this a second real implementation to prove the
seam against — Paper Trading Bridge Tasks F-H, `.planning/paper-trading-f-
order-executor.md` through `-h-vst-integration.md`). `engine.runtime.
TradingLoop` depends only on `engine.execution.OrderExecutor` (`submit` +
`pollFills` + `pendingOrders` + `cancel`), never on a concrete
implementation. There are, and must only ever be, **exactly two**
implementations, full stop — no decorator/wrapper exception: `PaperBroker`
(the internal simulator — resolves fills synchronously from an injected
price) and `ExchangeOrderExecutor` (venue-agnostic, wraps the
`ExchangeAdapter` **interface**, never a concrete adapter — polls
`queryOrder` for real, asynchronous fills). **A new venue means writing a
new `ExchangeAdapter` implementation; it never means writing a new
`OrderExecutor` implementation.** A cross-cutting concern (e.g. durable
submission-outcome marking) is composed into `ExchangeOrderExecutor` via
an injectable collaborator interface (`engine.execution.
SubmissionListener`), not layered on top as a third `OrderExecutor`
implementation — an earlier version of Task H tried the wrapper/decorator
approach for exactly this (`engine.runtime.
PersistentSubmissionOrderExecutor`, since removed) and, on real CodeRabbit
review, found it genuinely violated this invariant; `SubmissionListener`
is the corrected design, not an exception carved out to keep it. See
`OrderExecutor`'s own Javadoc for the full "Extensibility invariant" and
`.planning/paper-trading-h-vst-integration.md` for the real review finding
and correction.

`engine.runtime.PaperTradingApp`'s `PAPER_TRADING_EXECUTION_MODE`
(`simulated` default | `bingx-vst`) selects which `OrderExecutor` gets
built at startup — `simulated` builds the exact `PaperBroker` graph this
project has always run; `bingx-vst` builds a real `BingXAdapter`-backed
`ExchangeOrderExecutor`, given a real `SubmissionListener` (`engine.
runtime.MarkerRecordingSubmissionListener`) for durable
`SUBMISSION_UNKNOWN` handling — see `.planning/paper-trading-h-vst-
integration.md`), pointed at a hardcoded VST-host Java constant with
**no environment variable, argument, or other configuration surface**
able to route it anywhere else. The two modes are meant to run as two
independent processes (distinct `PAPER_TRADING_REPORTS_DIR`, independent
`KillSwitch`), not a runtime toggle on one running process — see that
same planning doc for why, and for the still-open human decision on which
loop's clock counts toward the Paper Trading Pass Criteria below.

**KIS/KOSPI200 venue integration, Phase 1 — planned, not yet built**
(design committed here per `.planning/README.md`'s "a detailed design for
work that hasn't started yet lives directly in CLAUDE.md, in full, until
that work actually begins" rule; full task-level detail in the governing
plan file referenced at execution time). This is the first real test of
this file's own "Multi-exchange / multi-symbol / equities expansion
without refactoring OMS, Risk Gateway, or Execution" Long-term Design
Target: adding 한국투자증권(Korea Investment & Securities, "KIS")'s REST
API for **KOSPI200 index futures** as a **third** independent
paper-trading loop, alongside the two already-running BingX loops (own
process, own `PAPER_TRADING_REPORTS_DIR`, own `KillSwitch` — same pattern
`bingx-vst` already established relative to `simulated`).

**Narrowed to futures only, options explicitly deferred** (tightened on
real CodeRabbit review of the PR that added this section): `OrderIntent`/
`Order`/`Fill`/`SubmissionMarker` all identify an instrument with a single
free-form `String symbol`. A KOSPI200 **futures** contract is fully
identified by its expiry month alone, so a plain symbol string stays
sufficient — the "zero schema change" claim below holds. A KOSPI200
**option** additionally needs strike price, expiry, and call/put — none
of which a bare symbol string round-trips today, and defining a canonical
format plus parsing/validation/round-trip tests for that is real,
undesigned work. Rather than assume it away, options are out of scope for
this phase entirely; revisit as its own follow-up once a canonical
option-symbol format is designed and tested.

*Why KOSPI200 futures, not individual stocks or an ETF*: futures
structurally resemble BTC perpetuals far more than cash equities do — a
real margin account, both LONG and SHORT directions supported natively,
so the existing `Side.LONG`/`Side.SHORT` enum maps cleanly with zero
schema change (see the futures-only narrowing above for why this claim is
now scoped precisely). Individual KR stocks/ETFs would have forced a
`Side` schema change (cash equities are effectively BUY-only for a retail
account) — sidestepped by this choice.

*Why KIS, not Kiwoom/eBest/Toss* (researched, not assumed): KIS was the
first Korean broker to offer a REST (not Windows-only OCX/COM) API, has
by far the most mature Python/Java community tooling, and — the deciding
factor — its official GitHub repo (`koreainvestment/open-trading-api`)
confirms real 모의투자 (paper trading) support for domestic futures/
options specifically (a `domestic_futureoption/` example directory, a
`my_paper_future` config field), not just stocks. Kiwoom's new REST API
(2026) is a legitimate future alternative but too new to have the same
depth of real-world-verified documentation; Toss's new OpenAPI has no
confirmed paper-trading support at all, which disqualifies it outright
given this project's non-negotiable paper-trading-first rule.

*Scope, deliberately narrow*: infrastructure only — mirrors exactly how
the original BTC paper-trading loop was built and proven against
`DummySignalSource` before any validated BTC strategy existed
(Implementation Priority #6-8's own precedent). A real KOSPI200 strategy
is explicitly out of scope for this phase: it would need its own
walk-forward-validated research under this file's Strategy Research
Methodology, a separate future `Discuss`, not decided or started here.

*Codebase audit, confirmed by direct inspection before any task
breakdown*: the `OrderExecutor`/`ExchangeAdapter` seam above is already
fully venue-agnostic — `ExchangeOrderExecutor`, `Reconciler`,
`SubmissionMarkerResolver`, `MarkerRecordingSubmissionListener`, and the
shared `BalanceSnapshot`/`OrderStatus`/`PositionSnapshot`/`PositionMode`
records are already interface-typed / BingX-free, so writing
`KisAdapter implements ExchangeAdapter` alone reuses all of them
unmodified — no second `OrderExecutor` implementation needed, matching
this section's own invariant. What's genuinely missing: (1)
`ExchangeAdapter.setLeverage`/`setPositionMode` are perpetual-futures/
margin-account-specific with no obvious 1:1 KRX equivalent (KRX futures
margin is exchange-mandated, not a user-settable multiplier). **Neither
is a silent no-op** (tightened twice on real CodeRabbit review — first
for `setPositionMode`, then again for `setLeverage` on a second review
pass of the same PR: a caller, the KIS factory, or `KisPreflight` silently
treating a normal return from either as "protection successfully applied"
would let the KIS loop start trading believing a safeguard exists that
never actually ran). Both methods on `KisAdapter` must throw or otherwise
signal "unsupported here" explicitly, and neither the `forKisPaper()`
factory nor `KisPreflight` may treat that signal as a success condition.
Skipping the *exchange-side* leverage-setting call does not mean skipping
risk enforcement: `RiskGateway`'s own notional/margin limit — the
contract-multiplier conversion in the `RiskLimits` section below — is
what actually bounds this loop's exposure, and must keep applying in
full regardless of what `setLeverage`/`setPositionMode` do or don't do
on the exchange side; (2)
`RiskDecision`/`Order`'s `approvedLeverage` field is structurally
required end-to-end but functionally dead in the actual submit path
today (`BingXAdapter.submitOrder` never reads it — leverage is only
applied once, account-wide, by `VstPreflight`), so `KisAdapter` can
satisfy it with a fixed placeholder, no schema change needed — **this
placeholder satisfies the schema only; it is not itself a risk control,
see the `RiskLimits` section below**; (3) `engine.runtime.TradingLoop`
is hard-typed to the concrete class `BingXPriceFeed`, not an interface
— a real blocking prerequisite, structurally identical to the
`OrderExecutor` extraction already done once for `PaperBroker`; (4) no
market-hours/calendar concept exists anywhere — `TradingLoop.tick()` has
exactly one production call site (`PaperTradingApp.runTick()`, a plain
fixed-rate `ScheduledExecutorService`) with no internal scheduling
assumptions of its own to fight, but KOSPI200 futures' real regular
session — **08:45-15:45 KST, not the cash-equities 09:00-15:30 this
section originally and incorrectly stated** (corrected on real
CodeRabbit review, sourced against KRX's own official trading-hours page)
— needs new logic that does not need to touch `TradingLoop` itself.
**KOSPI200 futures also has a night session (18:00-06:00 KST) that this
phase's `KrxMarketCalendar` explicitly does not support** — Phase 1
covers the regular session only, disclosed here rather than silently
narrowed. **The regular session itself is shorter on each contract's
final trading day — 08:45-15:20 KST, not 08:45-15:45** (a second real
correction from a second CodeRabbit review pass, sourced against the
same KRX official page): `KrxMarketCalendar` must identify final trading
days and apply the shorter close, and — because that identification
depends on future contract-expiry-calendar data this phase doesn't yet
have a committed source for — **fail closed**: if a given date's
final-trading-day status can't be determined from whatever fixture
exists, treat the session as **closed** rather than defaulting to the
longer 15:45 close, and cover the boundary (a real final trading day at
15:20-15:45, and an unknown/undetermined date) with real tests, not just
the ordinary-day case. Night-session support and the exact
final-trading-day identification rule are future work — moving
lunar-calendar holidays are a separate, already-noted gap (`java.time`'s
built-in chronologies cannot express them); (5) `PaperTradingApp` hardcodes
BingX-specific env vars and a `forBingXVst()` factory — adding KIS means
an analogous new factory method and new KIS-named env vars, matching the
project's existing, accepted pattern, not a regression to fix.

*Task breakdown* (own `.planning/kis-a/-b/-c/-d-*.md` doc per task, own
PR each, **stop-and-ask merges** — Java runtime/exchange logic, same
auto-merge exclusion already applied to all OMS/Risk/Execution-adjacent
work regardless of CI/CodeRabbit status): **1)** extract an
`engine.runtime.PriceFeed` interface (`BingXPriceFeed implements
PriceFeed`, `TradingLoop` retypes to it) — mirrors the original
`OrderExecutor` extraction exactly, expected zero test-file diff,
confirmed not assumed (`TradingLoopTest` passes `BingXPriceFeed` by
reference, compiles unchanged; `PaperTradingAppTest` never references it
directly). **2)** `KisAdapter implements ExchangeAdapter` in
`java/exchange` (same module as `BingXAdapter`, no new Gradle dependency)
plus `KisTokenProvider` (KIS's OAuth2 App-Key/Secret → cached access
token — genuinely new, no `BingXSigner` precedent, since BingX's scheme
is stateless per-request HMAC) plus a `KisPriceFeed` decision (its own
class, mirroring `BingXPriceFeed`'s separateness from the authenticated
adapter, **or** folded into `KisAdapter` if KIS's quote endpoints turn
out to need the same OAuth2 token — verify during this task, don't
assume), all TDD'd against a hand-written fake KIS HTTP server (this
project's established no-mocking-framework convention), zero live
wiring. Exact KOSPI200 contract-symbol/TR-code/endpoint details verified
against real KIS docs during this task, not designed in advance. **3)**
a new `engine.runtime.TradingCalendar` interface
(`AlwaysOpenTradingCalendar` for `simulated`/`bingx-vst`, provably inert
via its own test; `KrxMarketCalendar` for real KST hours + a holiday
lookup against a small committed static fixture — no Korean-lunar
`Chronology` ships in the JDK and no calendar library exists in this
repo today, so a live per-tick network call is rejected in favor of a
fixture, sourced by hand from KRX's official calendar or exported once
from KIS's own holiday API after real paper credentials exist). **Same
fail-closed rule as the final-trading-day case above, stated explicitly
for the holiday lookup itself (third CodeRabbit review pass, same PR)**:
a date missing from the fixture, or any failure looking it up, resolves
to **closed**, never open — an undetermined session status must never be
treated as "market's open." `PaperTradingApp.runTick()` must not call
`tradingLoop.tick()` (and therefore never reach `submitOrder`) for any
date `KrxMarketCalendar` can't positively confirm as open. Tests must
cover the undetermined-status case and a holiday boundary explicitly,
confirming `submitOrder` is never invoked for either, not just the
ordinary open/closed cases. This `TradingCalendar` gates
only the `tradingLoop.tick()` call inside `PaperTradingApp.runTick()`
(recommended: in-process, not OS/cron-level, matching this project's
existing "the class that already owns the check gets it" pattern — a
real design fork, confirm before this task starts rather than deciding
unilaterally mid-implementation). **4)** `PaperTradingApp` wiring
(`PAPER_TRADING_EXECUTION_MODE=kis-paper`, `forKisPaper()`, `KIS_APP_KEY`/
`KIS_APP_SECRET` env vars, hardcoded `KIS_PAPER_BASE_URL` Java constant
with no env-var override — same no-config-surface security pattern as
`BINGX_VST_BASE_URL`) plus `KisPreflight`. **`KisPreflight` cannot mirror
`VstPreflight`'s specific gating logic**, only its shape:
`VstPreflight`'s core safety gate is "fail closed unless `balance.asset()`
is exactly `VST`," which works because BingX's demo accounts have a
textually distinct settlement asset. KIS has **no single response field**
that marks an account as paper — confirmed by real research (both KIS's
own official repo and independent sources) during CodeRabbit review of
the PR that added this section, replacing this item's original vague
"confirm during this task" placeholder with a concrete, required Task 4
acceptance contract instead:
`KIS_PAPER_BASE_URL` fixed to `https://openapivts.koreainvestment.com:29443`
with no live-URL or arbitrary-URL path possible; paper-only App Key, App
Secret, account number, and domestic-futures/options product code
(`ACNT_PRDT_CD`) used throughout; startup refuses to proceed on any
missing/malformed credential, any auth failure, or any config that
doesn't consistently point at the paper environment; `submitOrder` is
never reachable before `KisPreflight` passes; any preflight failure trips
`KillSwitch`; every one of these is covered by a `FakeKisServer` test
(missing config, malformed config, auth failure, environment mismatch,
order-call-blocked-pre-preflight) — real verification against KIS's
actual paper API is a separate, later integration check, not a
substitute for the fake-server coverage. Leverage enforcement is skipped
entirely (not called as a no-op). Real verification against KIS's actual
API is blocked on the user's own KIS 모의투자 registration + App
Key/Secret generation (not yet done as of this writing) — everything
else in Task 4 (building/testing against a fake server) is not blocked
by it.

**Two more real gaps found while actually implementing `KisAdapter`
(Task 2), explicitly deferred to Task 4 rather than fixed in Task 2 —
Task 2 has zero live wiring and cannot itself exercise either path, but
Task 4 must resolve both before real submission ever happens**: (a)
**ambiguous-submission recovery has no real answer for KIS.** KIS's
order request carries no client-supplied idempotency key at all (unlike
BingX's own `clientOrderID`, confirmed via this project's real VST
verification to give BingX genuine server-side duplicate-submission
rejection) — if a network failure happens after KIS genuinely accepts an
order but before the response is observed, the resulting `Order` is
`SUBMITTED` with no `exchangeOrderId`, and `KisAdapter.queryOrder`
cannot resolve it (it searches by `exchangeOrderId`, which doesn't exist
yet in this scenario). Before KIS is wired into a live-submitting
`ExchangeOrderExecutor`, a real resolution path must exist — matching a
pending order against `inquire-ccnl`'s result set by symbol/side/
quantity/time rather than by ID, or an explicit manual-confirmation step
— and must never be "just resubmit." (b) **`GUARDED_MARKET` has no
wire-level price guard for KIS, same as BingX already has none.** When
`limitPrice()` is null, `KisAdapter` sends a real, unprotected market
order (`UNIT_PRICE="0"`) — mirroring `BingXAdapter`'s own already-shipped
`"MARKET"` mapping exactly, so this is a pre-existing characteristic of
this project's order-guard design as a whole, not something Task 2
introduces fresh. It is exactly what the Live Entry Criteria's own
"market-order guard enabled" line exists to gate — that verification has
not happened for either adapter yet and needs its own dedicated
`Discuss` before `GUARDED_MARKET` is ever used against a real account
through either adapter, not just KIS's.

`RiskLimits.canary()`'s existing percentage-based limits are reused
unmodified for this phase, but **only after a real contract-multiplier
conversion is added — not as-is** (tightened on real CodeRabbit review,
which sourced KRX's own official contract specification: a KOSPI200
futures contract is valued at index points × ₩250,000, the exchange's
own official multiplier). `RiskGateway` today computes notional as a
plain `quantity × price`; applied to KOSPI200 futures without the real
₩250,000 multiplier, that number is not the position's actual notional
value, so `RiskLimits.canary()`'s percentage limits would be checked
against a meaningless figure — "reused unmodified" was true for the
*numbers* but glossed over needing this conversion to exist at all
first. Task 2/4 must define and test the real quantity → notional
conversion for KOSPI200 futures (contract count × index price ×
₩250,000) before `RiskLimits.canary()`'s percentages mean anything for
this loop. **The conversion's own rules, made concrete on a second
CodeRabbit review pass rather than left as "define during the task"**:
quantity must be a positive integer contract count; the price source is
the order's own limit price for a limit order, else a defined current/
reference price for a market order (exact source confirmed during Task
2/4, not invented here); all arithmetic uses `BigDecimal` with exposure
always **rounded up, never down** (rounding down could understate a
position's real notional and let an over-limit order through); the
margin-rate input has a defined source and a staleness check; **missing
or stale price/margin data is a rejection (fail closed), never a
silent fallback**; and this entire conversion runs **before**
`RiskLimits.canary()`'s own percentage check, not after or in parallel —
ordering matters, since the percentage check is meaningless against a
number this conversion hasn't yet produced correctly. Fake-KIS-server
tests must cover max-quantity, rounding direction, insufficient-margin,
missing/stale price-or-margin input, and limit-exceedance behavior — not
just the happy path. The fixed `approvedLeverage` placeholder noted above
satisfies the schema only — it is not itself a risk control and must not
be treated as one. A
KOSPI200-specific `RiskLimits` *tier* (new percentage numbers) remains
future, Strategy-Research-gated work per this file's own non-negotiable
rule against weakening risk limits without approval — not decided or
invented here; the contract-multiplier conversion above is a
prerequisite for the existing canary numbers to be meaningful at all,
which is different from, and needed regardless of, that future tier
question.

**Task 4, as actually implemented, did not build the contract-multiplier
conversion above** — flagged explicitly here (real CodeRabbit review of
the Task 4 PR) rather than left silently unresolved by this section's own
"Task 2/4 must define and test" requirement quietly going unmet. Task 4's
scope turned out to be the wiring layer only (`forKisPaper()`,
`KisPreflight`, the `kis-paper` execution mode) — the conversion itself
still needs its own `Discuss` pass and its own task, matching this
project's Development Methodology's mandatory-`Discuss`-for-R3-risk rule
rather than being improvised under review pressure on a wiring task. The
practical consequence, stated plainly: **`RiskLimits.canary()`'s 2%
order-notional limit does not meaningfully bound a real KIS order's
exposure today** — it is checked against `quantity × price`, off from
the real notional by the ₩250,000 contract multiplier.

**Correction to this disclosure's own first version, caught on a second
CodeRabbit review pass of the same PR**: it originally claimed this gap
was "inert... because it still runs against `DummySignalSource`." That
was simply false — `forKisPaper()` has always wired a real
`FileSignalSource` pointed at a real `signalPath`, the same as
`forBingXVst()` does, so this graph is fully order-submission-capable the
moment *any* file resembling a signal appears at that path, accidentally
or otherwise. **Real mitigation, not a full fix**: `forKisPaper()` now
unconditionally trips `KillSwitch` at construction — not only on a
preflight/marker problem as `forBingXVst()` does, but always, specifically
because of this gap — so no signal can result in a submitted order
without a deliberate human reset first, regardless of how clean preflight
and marker state look. This bounds the risk to "a human must actively
choose to enable trading," it does not fix the underlying gap; the
contract-multiplier conversion must still be built — as its own dedicated
task, per the requirement above — before that reset is ever performed
against a `kis-paper` process pointed at a real strategy signal.

**A second Task 4 finding, same review, now fixed**: `FileSignalSource`'s
delivered-marker file (see Task H's own "durable, cross-restart dedup"
design) could have collided between `bingx-vst` and `kis-paper` if an
operator ever explicitly overrode `PAPER_TRADING_SIGNAL_PATH` to the same
value for both processes — `KIS_SUBMISSION_MARKERS_PATH` (Task 4's own
separate submission-marker file) solves a different problem and never
prevented this. Not a collision in practice even before this fix —
`resolveSignalPath`'s default path is derived from `symbol`, and the two
modes trade different symbols (a KOSPI200 futures contract vs.
`BTC-USDT`), so their default paths never matched — but cheap enough to
close outright rather than merely disclose: the `kis-paper` constructor
now writes to a KIS-specific marker filename (`kis-delivered.marker`),
not the shared `delivered.marker` name the `bingx-vst` path uses, so the
two venues' delivery state can never collide even under a forced
same-path misconfiguration.

**A third Task 4 finding, flagged twice across two review rounds before
being fixed rather than left deferred**: `PaperTradingApp.runTick()`'s
`TradingCalendar` gate used to skip `OrderExecutor.pollFills` along with
everything else in `TradingLoop.tick()` while the market was closed — a
real fill, cancel, or expiry at the exchange right at/after close would
not have been reflected in this process's own `OrderStore`/
`OrderExecutor` state until the market reopened and a tick ran again.
`reconcile()` runs every tick regardless, but only checks internal
consistency between this process's own records, not against the
exchange's live state, so it could not have caught this staleness
either. This gap was new, not pre-existing — `simulated`/`bingx-vst`
always use `AlwaysOpenTradingCalendar`, so their `tick()` (and therefore
`pollFills`) always ran; `kis-paper` is the first mode whose calendar can
actually report closed. Initially disclosed and deferred as "real
surgery on `TradingLoop`, a core class shared by all three modes, not
something to rush under review pressure" — CodeRabbit pushed back a
second time citing this project's own stated Java Trading Plane scope
("partial fill handling, cancel/replace, … position reconciliation"),
and on reinspection the actual fix turned out to be small and additive,
not the redesign originally assumed: `TradingLoop.pollPendingFills()` is
a new public method containing the same price-fetch-and-`pollFills` two
lines `tick()` already ran as its own first step (left in place there
unchanged, not rewritten to call the new method, since `tick()` also
needs the fetched price again later for signal submission) — `runTick()`
now calls this directly from its market-closed branch, wrapped in its
own `try`/`catch` matching this class's "a single tick's failure must
never propagate" convention. Pending-order reconciliation now runs on
every tick regardless of market hours; only new-signal processing is
gated by `TradingCalendar`. Proven, not just implemented: a new
`PaperTradingAppTest` case seeds a real pending order, closes the
calendar, and confirms the order still fills on `runTick()` while
`TradingLoop.lastTickAt()` stays `null` throughout (proving `tick()`'s
own new-signal path genuinely never ran).

**A fourth Task 4 finding, disclosed and deliberately left deferred
(unlike the third above, this one is not being fixed)**:
`FileSignalSource.nextSignal()` marks a signal delivered — updates its
own in-memory pointer, persists the durable marker if configured — the
moment it reads a genuinely new signal, before the caller (`TradingLoop
.tick()`) has done anything with it. If price lookup, risk evaluation,
order construction, or exchange submission then fails anywhere
downstream, the signal is already marked delivered — a restart cannot
recover it, and with a durable marker configured, neither can a retry
within the same process. A real fix means giving `SignalSource` its own
acknowledgment contract (mark-delivered only after `OrderPipeline`
successfully hands off, not merely on being read) — a genuine
interface-level change spanning `SignalSource`, `FileSignalSource`,
`DummySignalSource`, and `TradingLoop.tick()`'s own control flow itself,
not a local fix. **This is not new to `kis-paper` or this PR** —
`FileSignalSource` has carried this characteristic since Paper Trading
Bridge Task H, and it applies identically, right now, to the real,
currently-running `bingx-vst` production loop. Deliberately not
attempted under review pressure here: a change to `FileSignalSource`'s
own delivery semantics — a component already in continuous, real
(if paper-account) operation — deserves its own `Discuss` pass and
careful testing against the live loop, not a rushed fix bundled into a
KIS wiring task. Full disclosure in `FileSignalSource`'s own Javadoc.

Explicitly out of scope this entire phase: **KOSPI200 options** (a
canonical strike/expiry/multiplier-preserving symbol format is undesigned
— see the futures-only narrowing above); the KOSPI200 futures night
session (18:00-06:00 KST); any real KOSPI200 strategy or promotion off
`DummySignalSource`; real contract-symbol/TR-code specifics (verified
during implementation, not designed now); extending
`scripts/paper-trading-watchdog.sh`/the dashboards/cron for a third loop;
a new `RiskLimits` *tier* with new percentage numbers (the
contract-multiplier conversion above is required regardless — that's a
prerequisite for the existing canary numbers to mean anything, not a new
tier); `.env`/credential provisioning (blocked on the user's own KIS
registration).

## Non-negotiable Rules

- Never enable live trading without explicit human approval.
- Never hardcode API keys, secrets, passwords, tokens, or private keys.
- Never modify `.env` or real credential files.
- Never weaken risk limits or increase leverage limits without explicit
  human approval.
- Never bypass the Java Risk Gateway.
- Never let Python place live orders directly.
- Never connect an MCP server, skill, or plugin capable of placing
  exchange orders to any AI coding session operating on this repo — it's
  the same Risk Gateway bypass as direct order placement, just through a
  different door. Read-only/market-data tools are fine.
- Never add live exchange write-access in CI.
- Never commit raw trading logs containing secrets or account identifiers.
- Never run untrusted install scripts (`curl | sh`, `wget | bash`).
- The repo is public (chosen for free GitHub Actions minutes). GitHub
  push-protection/secret-scanning is `enabled` at both repo and account
  level (verified 2026-07), but **only covers "Provider patterns"**
  (AWS/Stripe/GitHub-style tokens with a fixed, recognizable format) on
  the free tier. "Generic patterns" (RSA/SSH private keys, generic
  API keys, connection strings) are a separate GitHub feature
  (`secret_scanning_non_provider_patterns`) gated behind the paid
  "GitHub Secret Protection" product ($19/mo/active committer) or an
  Organization/Enterprise security configuration — neither applies to a
  personal-account public repo, and it is not something the REST API can
  enable for one (confirmed: `PATCH .../security_and_analysis` returns
  200 but silently no-ops; the repo Settings UI has no such toggle for a
  personal account either). This is why four independent 2026-07 tests
  with real RSA private keys (PKCS#8 and legacy PKCS#1) were never
  blocked or alerted on — not a misconfiguration, a tier limit. Two
  AWS-key-shaped test strings also went undetected, most likely because
  synthetic test values didn't match AWS's exact key format, not because
  Provider-pattern coverage is broken.
  Given that gap, generic secrets (the private-key/credential case this
  project actually cares about) are caught locally instead: the
  `.githooks/pre-commit` hook runs `gitleaks` against every staged commit
  and blocks on a match, fails closed if `gitleaks` isn't installed, and
  fires regardless of whether the commit is made by an AI coding session
  or manually (unlike the `dwarvesf/claude-guardrails` hook, which only
  fires on tool calls inside an AI coding session). One-time setup per
  clone: `git config core.hooksPath .githooks`. Since that setup step is
  easy to forget on a fresh clone (or skippable via `--no-verify`), a
  `.github/workflows/gitleaks.yml` CI job backstops it — scans every push
  and PR regardless of local configuration. GitHub push protection still
  stands as a further layer for Provider-pattern secrets (exchange API
  keys, when they arrive in Priority #7).

## Risk Parameters (defaults — changing these needs explicit human approval)

**Canary live**: base leverage 1x, max 2x, max order notional 2%, daily
loss limit -0.5%, weekly -1.5%, monthly -3%, hard stop -4%.

**Stable live**: base leverage 2x, max 3x, max order notional 5%, daily
loss limit -1%, weekly -3%, monthly -6%, hard stop -8%, emergency stop
-10%.

## Paper Trading Pass Criteria

Minimum 30 days (45 recommended), 50+ trades, zero critical crashes, zero
duplicate orders, zero position mismatches, zero risk-gateway bypasses, no
missing daily reports, kill switch verified working, paper score 80+.

## Live Entry Criteria

Paper trading passed + paper score 80+ + all hard gates passed + VPS
operation + IP-restricted API key + no withdrawal permission + manually
approved live flag + leverage hard max 2x + market-order guard enabled +
kill switch verified.

This 2x is the initial paper→live entry gate, which runs under the
canary tier — itself already capped at 2x per Risk Parameters. It is not
a ceiling the later-stage stable tier (documented max 3x) must also
respect; those are two different points in the system's lifecycle, not a
contradiction. Enforced in code via `RiskLimits.ABSOLUTE_MAX_LEVERAGE`
(see its Javadoc).

## Exchange API Facts — BingX (first adapter, verify before relying on them)

### Verified (called the live public API directly and observed the response)

- Symbol: `BTC-USDT`
- Recent trades: `GET /openApi/swap/v2/quote/trades`
- 15m klines: `GET /openApi/swap/v3/quote/klines`, interval token `15m`
- Historical range: `startTime`/`endTime` are half-open
  (`startTime <= t < endTime`), must align to the 900,000ms (15m) grid, max
  span 1000 candles per request
- `limit` is not a reliable count guarantee — requests over 1000 are
  silently capped; verify actual returned count in code
- **Historical kline retention on the live production endpoint
  (`open-api.bingx.com`) is granularity-dependent, not a single fixed
  window** — confirmed 2026-07-26 via direct binary-search probing (all
  four granularities) plus a real, full `backfill.py` run for `1h` and
  (2026-07-28, Task T) `1d` — the two granularities this project's
  strategy research actually depends on, so those two got the stronger
  verification: a complete fetch with an independently-confirmed
  zero-gap count, not just an earliest-bar probe. `1d` back to
  **2021-05-14T00:00:00Z exactly** (confirmed by a real backfill:
  **1,901 daily bars, zero internal gaps**, latest 2026-07-27, as of
  2026-07-28 — a 5.21-year span, and the interval token is `1d` with
  every bar's `time` on the UTC-midnight 86,400,000ms grid, i.e. BingX
  does *not* open its daily candle at a local/exchange-timezone offset.
  Gap count for `1d` was previously simply unknown — a binary search
  finds an edge, it cannot find holes. Unlike `1h` below, `1d`'s
  earlier probe-only estimate turned out **accurate**: it said
  ~2021-05-12 / "~5 years" and the real backfill says 2021-05-14 /
  5.21 years, a 2-day difference over 2 elapsed days, consistent with
  both rolling retention and ±2 days of probe imprecision — the two
  can't be told apart from one pair of observations. See
  `.planning/sr-t-daily-data-path.md`); `1h` back to
  **2024-04-27T10:00:00Z exactly** (confirmed by the real backfill: **819.9
  days / 19,678 hourly bars, zero internal gaps**, as of 2026-07-26 —
  note this is the true span from that earliest date to "now," roughly
  **1.8x longer** than an earlier same-day binary-search estimate of "~15
  months" for `1h`, which undercounted; the earliest-date finding itself
  was correct and reproduced exactly by the real backfill, only the
  derived duration was off — see `.planning/sr-f-risk-management-and-1h-
  variant.md` for the full arithmetic); `15m` back to ~2025-11-16 (~8.3
  months, matching `.planning/sr-a-data-pipeline.md`'s independent
  finding); `5m` back to ~2026-05-02 (~3 months, binary-search estimate
  only, not re-verified via a full backfill). Finer granularity has
  materially shorter retention — not a fluke specific to `15m`, a real
  BingX-side pattern across all four. Like the `15m` depth before it,
  expect these numbers to keep drifting forward on every future run
  (rolling retention, not a fixed archive) — re-run `backfill.py` rather
  than trust any of these as permanent.
- **Funding rate**: `GET /openApi/swap/v2/quote/fundingRate?symbol=BTC-
  USDT` (`v2`, public, unauthenticated). Envelope matches every other
  BingX endpoint (`{"code","msg","data"}`) except empty-result `data` is
  `null`, not `[]` — confirmed both for a genuinely out-of-retention
  range and an in-retention range with zero funding events in it.
  Ordering newest-first within a page, silently capped to the newest
  rows on an over-wide request — same shape as klines — but `limit` over
  1000 is a hard server error (`code: 109400`), not a silent clamp,
  unlike klines. **`data: null` is flaky/non-deterministic near the
  retention edge**, confirmed two ways: `sr-m`'s original 2026-07-27
  probing found a range independently known to have real data returning
  `null` on ~1-in-6 to 1-in-2 of repeated identical calls; re-probed
  2026-07-28 during this same task and found the flakiness had gotten
  *worse* at the same boundary (15/15 consecutive `null`s for a range
  the local cache already had real, previously-fetched rows for) —
  consistent with a rolling retention window genuinely moving forward
  hour to hour, not just a flaky server. A real, resumable
  `backfill_funding.py` run (2026-07-27/28) confirms actual depth back
  to **2020-11-29T12:00:00Z** (**6,199 rows** through 2026-07-27T16:00Z,
  re-run multiple times to converge) — far deeper than funding's 8h
  cadence would suggest is needed for a useful signal, and much deeper
  than klines' own retention at any granularity. 3 small gaps (4-16h
  each) remain unresolved right at that earliest boundary after 3+
  reruns (15+ null-retries each) — treated as genuinely gone, same
  "consistently null after 10-15 trials = really gone" standard as
  klines' retention-edge probing. Real historical `fundingTime` values
  are **not** always aligned to the modern 8h/28,800,000ms grid (a
  2020-11-29 through 2021-01-05 stretch settles 4h off the grid every
  later row uses, plus one isolated one-off row) — range validation for
  this endpoint deliberately does not enforce grid alignment the way
  klines does. Sign convention (verified against BingX's own official
  docs): `fundingRate > 0` → longs pay shorts; `fundingRate < 0` →
  shorts pay longs. Implemented as `payment = -sign(position_qty) ×
  |position_qty| × markPrice × fundingRate` — `position_notional`
  (`|position_qty| × markPrice`) is always the unsigned magnitude; the
  position's own long(+1)/short(-1) sign is what actually flips the
  payment direction between "pays" and "receives" for a given
  `fundingRate` sign, using the funding row's own historical
  `markPrice`. See `.planning/sr-m-funding-rate-pipeline.md` for the
  full investigation and `python/metrics/position.py`'s module
  docstring for the implementation-level detail.

### Verified — authenticated, VST key (2026-07-24, @ckrhehfl's demo-trading
API key against `open-api-vst.bingx.com`)

- Response envelope is `{"code": 0, "msg": "", "data": ...}` (sometimes
  with a top-level `timestamp` too) — confirmed on balance, positions,
  and position-mode calls.
- **Balance** (`GET /openApi/swap/v3/user/balance`): `data` is an
  **array** of per-asset objects, not a single object as assumed pre-
  verification — `[{"userId", "asset", "balance", "equity",
  "unrealizedProfit", "realizedProfit", "availableMargin", "usedMargin",
  "frozenMargin", "shortUid"}]`. `BalanceSnapshot` parsing must index
  into the array (one element per asset — just `VST` for a demo
  account) rather than treat `data` as the balance object directly.
- **Positions** (`GET /openApi/swap/v2/user/positions`): `data` is also
  an array (empty `[]` with no open positions) — consistent envelope
  pattern with balance, not a one-off.
- **Position mode default on a fresh key resolved**: `dualSidePosition`
  came back `"true"` (**hedge mode**) without ever having been set —
  this was flagged as undocumented pre-verification; hedge mode is
  confirmed as the default, not one-way. OMS should still set it
  explicitly on startup rather than rely on this (a default can change),
  but "undocumented" is no longer the reason to do so.
- **Real order placement, fill, cancel, and duplicate-`clientOrderID`
  behavior (2026-08-09, Paper Trading Bridge Task H's real VST
  verification — full raw JSON and narrative in `.planning/paper-
  trading-h-vst-integration.md`)**: a real `GUARDED_MARKET` BTC-USDT
  order (0.001 BTC, LONG), submitted through the full, real, OMS-mediated
  path (`OrderIntent → OrderPipeline → RiskGateway → Order →
  ExchangeOrderExecutor → BingXAdapter`), was acknowledged with a real
  `exchangeOrderId` and observed `FILLED` — `POST
  /openApi/swap/v2/trade/order`'s own **submit** response already
  reported `"status":"FILLED"` (a market order matched essentially
  instantly against the demo book), even though this codebase's own
  `ExchangeOrderExecutor.submit` deliberately never assumes an instant
  fill from that response (always returns `Optional.empty()`, resolved
  only via a later `pollFills`/`queryOrder`) — so the ~1.5s "ack-to-fill"
  latency actually observed reflects this project's own polling cadence,
  not real exchange latency, which is at or near the same round trip as
  acknowledgment itself.
  - **A real `commission` field exists** on `queryOrder`'s response
    (`GET /openApi/swap/v2/trade/order`) — e.g. `"commission":"-0.032441"`
    (negative = fee charged) — confirming (not yet acted on; a real,
    evidence-first follow-up per `ExchangeOrderExecutor`'s own Javadoc,
    Paper Trading Bridge Task G) that a real fee figure is available on
    the wire, not modeled-only. For this trade it was extremely close to
    this project's own modeled `FEE_BPS=5` estimate (`0.03244075`
    modeled vs. `0.032441` real, ~5bps either way) — one real data
    point, not a general proof the two always agree this closely.
  - **Cancel confirmed real**: `DELETE /openApi/swap/v2/trade/order`
    against a real, unfilled `LIMIT` order (priced far from market)
    returned the real status token `"CANCELLED"` (double-L) — confirms
    the REST-casing half of the previously-documented REST/WebSocket
    casing inconsistency; WebSocket's own `"CANCELED"` casing remains
    unverified (no WebSocket call has ever been made by this project).
  - **Duplicate `clientOrderID` is rejected server-side, not silently
    accepted or ignored**: a second, independent submission (a genuinely
    separate `RiskGateway`+`OrderStore`+`OrderPipeline`+
    `ExchangeOrderExecutor` graph — simulating a second process/session
    whose own `OrderStore` never saw the first submission, e.g. exactly
    the restart scenario Task H's own `FileSignalSource` marker-file fix
    protects against) carrying the **same** `clientOrderID` as the
    already-filled order above returned
    `{"code":101400,"msg":"clientOrderID unique check failed"}` — a real,
    definitive rejection (mapped cleanly by this project's own
    `ExchangeOrderExecutor`/`Order.reject()` path), not a silent
    duplicate fill. This is real evidence BingX's own server-side
    idempotency is a genuine additional safety layer on top of (not a
    substitute for) this project's own software-side protections —
    unconfirmed before this run, and not something to rely on exclusively
    given it is observed, not officially documented, behavior.
  - **Real account-wide leverage originally observed: `"20X"`** on a fresh
    VST account's BTC-USDT position, independent of and unenforced by
    `RiskGateway`'s own approved leverage — because at the time of that
    original observation, **nothing in this codebase called `POST
    /openApi/swap/v2/trade/leverage`** (confirmed by grep — `setLeverage`
    existed on `ExchangeAdapter` and was implemented by `BingXAdapter`,
    but had no caller anywhere). **Since fixed, on real CodeRabbit review
    of this same PR**: `VstPreflight` (see below) now actively calls
    `ExchangeAdapter#setLeverage` for both `LONG` and `SHORT` (hedge mode)
    to `RiskLimits.canary().baseLeverage()` on every clean start (no
    pre-existing position found) — closing the gap the `20X` observation
    above exposed. **Fails closed**: if a pre-existing non-zero position
    is found, leverage enforcement is skipped entirely (a leverage change
    while a position is open is commonly rejected by exchanges) and the
    kill switch starts already tripped instead, requiring a deliberate
    human reset before any new signal is submitted — so this process
    never proceeds to normal trading believing leverage is constrained
    when a stale, unconstrained position might still exist. If either
    `setLeverage` call itself fails, that propagates uncaught and refuses
    to start, the same fail-closed treatment as the asset check above.
    Real per-call HTTP verification against the live BingX VST API is
    still outstanding as of this note — confirmed only against a
    hand-written fake `ExchangeAdapter` (`VstPreflightTest`) — because the
    account still held a real position from the original verification run
    and this codebase's OMS-mediated order path has no way to close/reduce
    a position at all (submitting `Side.SHORT` in hedge mode opens a
    second, independent position rather than closing the existing `LONG`
    one) — a separate, real, disclosed gap, out of scope for this task.
    Full detail on both: `.planning/paper-trading-h-vst-integration.md`.
  - **A real, disclosed credential-handling incident during this same
    verification, fixed at the root cause**: the real `BINGX_API_KEY`
    value was briefly written to a local, gitignored scratch log file
    (never committed, never pushed) after a CRLF-terminated `.env`
    (confirmed: `file .env` reports `ASCII text, with CRLF line
    terminators`) was sourced naively via `bash source`, leaving a
    trailing `\r` on the value; the JDK's own `HttpRequest.Builder
    #header` rejects a raw `\r` in a header value (RFC 7230) with an
    `IllegalArgumentException` whose message embeds the literal
    (invalid) value — i.e. the real credential — verbatim. The exposed
    scratch file was overwritten immediately on discovery; a separate,
    unrelated `cat -A` diagnostic command (checking for the same CRLF
    issue) also briefly surfaced the real `FRED_API_KEY` value in a tool
    transcript for the same reason (a missing final `.env` newline broke
    an ad hoc redaction filter). Root-caused and fixed for real, not just
    disclosed: `BingXAdapter`'s constructor now `.strip()`s both
    `apiKey`/`apiSecret` before storing them, closing this class of issue
    at its one real entry point rather than relying on every future
    credential source to be pre-sanitized — regression test
    `BingXAdapterTest#constructorStripsLeadingAndTrailingWhitespaceFromCredentials`.
    **Neither exposure reached any committed file, git history, or a
    public surface** — both were local-only (a gitignored scratch file
    and this session's own tool-call transcript) — but both values
    should still be treated as potentially compromised out of caution;
    rotating `BINGX_API_KEY` (VST-only, no withdrawal permission per this
    project's own Non-negotiable Rules) and `FRED_API_KEY` (free,
    read-only, no live-trading surface) is a cheap, low-stakes precaution
    a human can take at their convenience. `BINGX_API_SECRET` was never
    used as an HTTP header value anywhere in this codebase (only for a
    client-side HMAC signature, never transmitted or logged in plaintext)
    and was independently confirmed, via a redacted diagnostic check, to
    never have hit this same failure path — no evidence it was ever
    exposed.

### Documented, not yet empirically verified (2026-07-24 research pass —
read from BingX's official docs site, not tested against a live key yet;
treat with less confidence than the section above until someone actually
calls these with real credentials)

- Base URLs: `https://open-api.bingx.com` (production) vs
  `https://open-api-vst.bingx.com` (**VST demo trading** — virtual USDT,
  same signing scheme, real order-matching behavior against simulated
  funds). VST's existence matters a lot for how #7 gets built: it means
  the write-side (order placement) can be built and tested for real
  without live-money risk, not just designed against docs. Confirmed
  2026-07-24: an API key created through the normal API Management flow
  (no separate "demo account" step) authenticates against the VST host
  successfully. Still unverified: whether that same key *also*
  authenticates against the production host — not worth testing on
  purpose given the project isn't going live.
- Auth: `X-BX-APIKEY` header + `HMAC-SHA256` signature over all request
  params (incl. `timestamp`) sorted alphabetically and joined as
  `key=value&...`, hex-encoded uppercase, appended as `&signature=...`.
  Requests must be within 5s of server time
  (`GET /openApi/swap/v2/server/time` for clock sync).
- Order placement: `POST /openApi/swap/v2/trade/order` (all types via a
  `type` field: MARKET/LIMIT/etc.); a `POST .../order/test` variant
  validates without executing. Cancel: `DELETE /openApi/swap/v2/trade/order`.
- Position mode (`GET`/`POST /openApi/swap/v1/positionSide/dual`) is
  **account-wide** (not per-symbol) and can't change while any position
  or open order exists. One-way mode = one net position per symbol;
  hedge mode = simultaneous LONG + SHORT. Default-on-a-fresh-key is now
  verified (see above) — hedge mode. Leverage (`POST .../trade/leverage`)
  takes `side=BOTH` in one-way mode, `LONG`/`SHORT` in hedge mode.
- Endpoint versions are mixed within the same API family on purpose, not
  a one-off: balance is `v3` (`GET /openApi/swap/v3/user/balance`),
  positions/order/leverage are `v2`, position-mode is `v1`. Matches the
  same pattern already noted above for klines (v3) vs trades (v2).
- Private WebSocket (order/position push) shares the public market-data
  WS host with a `?listenKey=...` query param; the key comes from
  `POST /openApi/user/auth/userDataStream` (1hr TTL, refresh via `PUT`).
  Relevant to the long-term ~100-200ms latency target.
- Rate limits are per-account (UID), not shared across endpoints: order
  place/cancel 10/s, order query 30/s, positions 10/s, balance 5/s,
  leverage 5/s. IP-based limits on these were reportedly removed
  2025-12-16 per a changelog entry, but the docs UI still shows legacy
  numbers — don't trust either without testing.
- Known internal doc inconsistencies to test rather than trust blindly:
  order status casing differs between REST (`CANCELLED`) and WebSocket
  (`CANCELED`) samples; listen-key generation's code sample omits the
  signature params its own metadata says are required; WS connection
  limit is stated as both 60/IP and 240/IP in different parts of the
  same docs bundle.

Only public, unauthenticated read endpoints have been called against the
live API so far. Everything under "Documented, not yet empirically
verified" needs a real API key (VST is fine) before Priority #7 code
that depends on it is trusted.

## Exchange API Facts — Binance (data-research source only, not an `ExchangeAdapter`)

**Binance is not, and has no plan to become, a live-trading venue in
this project** — BingX remains the only exchange with a paper/live
path (Current Scope, `ExchangeAdapter`, Priority #7). Binance is used
exclusively as a deeper historical-data source for strategy research
(`python/data/binance_klines.py`, `backfill_binance.py` — Strategy
Research Task Z, `.planning/sr-z-binance-data-research.md`): no
credentials, no order placement, read-only public klines only. This
section exists for the same reason BingX's own does — verify before
relying on these facts — not because a second live-trading surface is
being added.

### Verified (called the live public API directly and observed the response, 2026-08-05)

- Symbol: `BTCUSDT` (no dash — differs from BingX's `BTC-USDT`).
  Spot: `GET https://api.binance.com/api/v3/klines`. USDT-M futures:
  `GET https://fapi.binance.com/fapi/v1/klines`.
- Response is a **bare JSON array of arrays, by position**, not an
  object envelope: `[open_time_ms, open, high, low, close, volume,
  close_time_ms, quote_asset_volume, num_trades,
  taker_buy_base_volume, taker_buy_quote_volume, ignore]`.
  `open_time_ms`/`close_time_ms`/`num_trades` are bare (unquoted)
  integers; OHLCV/volume fields are quoted strings — confirmed
  identically for spot and futures.
- **`endTime` is INCLUSIVE, not half-open** — confirmed by a real
  `startTime == endTime` request returning exactly one row. This
  project's own pipeline convention is half-open `[start, end)`
  everywhere else (BingX, and internally for Binance too via
  `binance_klines.py`'s `endTime = end_ms - 1` translation) — a
  genuine, load-bearing wire-level divergence to remember if calling
  this endpoint directly outside that module.
- **Silent over-limit capping keeps the OLDEST rows (closest to
  `startTime`), the opposite of BingX's verified newest-closest-to-
  `endTime` capping.** Confirmed for both spot and futures. A direct
  consequence: Binance's own rows come back **oldest-first
  (ascending)**, not newest-first like BingX.
- **Max `limit` differs by market and by enforcement style**: spot's
  hard max is **1000**, silently capped (no error) for anything
  higher, confirmed by exact row count for `limit=1001` and
  `limit=1500` alike. Futures' hard max is **1500**, enforced as a
  real `HTTP 400` (`{"code":-1130,"msg":"Data sent for parameter
  'limit' is not valid."}`) for `limit=1501` — futures rejects
  over-limit outright, spot does not.
- **A range starting before a symbol's real listing date returns an
  empty array** (`[]`), not an error and not padded — confirmed for
  spot BTCUSDT requesting 2017-01-01 through 2017-08-15 (before the
  real 2017-08-17 start).
- **Errors are a real non-2xx HTTP status with a JSON object body**
  (`{"code": <int>, "msg": "..."}`) — confirmed `400` for both a bad
  `limit` (spot) and a bad `symbol` (futures) — never a `200` with an
  embedded error code the way BingX works.
- **Real historical depth, confirmed via a full backfill with
  independently-verified zero internal gaps** (same "earliest-bar
  probe alone is not enough, a full backfill with a real gap count is"
  standard this file already applies to BingX `1h`/`1d`): spot BTCUSDT
  `1d` back to **2017-08-17T00:00:00Z exactly** (**3,275 daily bars,
  zero internal gaps**, latest bar 2026-08-04 — a ~8.97-year span, and
  the interval token is `1d` with every bar on the UTC-midnight
  86,400,000ms grid, same as BingX's own `1d`); USDT-M futures BTCUSDT
  `1d` back to **2019-09-08T00:00:00Z** (**2,523 daily bars, zero
  internal gaps** — verified to the same gap-detection standard as
  spot, though not independently re-verified for listing-date-artifact
  or early-era data quality the way spot was — see
  `.planning/sr-z-binance-data-research.md` for the full "lighter
  check" disclosure). Both are materially deeper than BingX's own best
  `1d` retention (5.21 years) — pooling Binance spot's full ~8.97-year
  history drops this project's own `1.645/sqrt(years)` detection floor
  to **~0.55** annualized Sharpe. **Both this and BingX's own 5.21-year
  floor (~0.72) fall inside, not outside, the 0.4-0.8
  credible-institutional-edge range this file already cites** — the
  real difference is *where* inside that range: BingX's ~0.72 sits
  near the range's top, so only its narrow top sliver (~0.72-0.8) is
  detectable and the rest (0.4-0.72, most of the range) is not;
  Binance's ~0.55 moves that boundary meaningfully lower, so roughly
  the top two-thirds of the range (~0.55-0.8) becomes detectable. Real
  power gain, not a change from "undetectable" to "detectable" outright
  — the low end of the credible range (below ~0.55) still isn't. Like
  every other retention figure in this file, expect this to keep drifting
  forward on future runs — re-run `backfill_binance.py` rather than
  trust this as permanent.
- **Rate limits are real, numeric, and confirmed live** — a first for
  this pipeline (BingX and FRED both rely on undocumented/third-party
  estimates). Live-fetched via each host's own `GET .../exchangeInfo`
  `rateLimits` field: spot `REQUEST_WEIGHT` = **6000/minute** per IP;
  futures `REQUEST_WEIGHT` = **2400/minute** per IP. Per-request weight
  costs (spot: flat 2 regardless of `limit`; futures: tiered 1/2/5/10
  by `limit` bucket) are sourced from Binance's own official
  docs/changelog rather than re-derived from isolated request-header
  deltas this session, so held to slightly lower confidence than the
  live-fetched budget numbers themselves. **HTTP 418** is Binance's own
  documented signal for a temporary IP ban after repeated rate-limit
  violations (distinct from `429`) — not observed live (no violation
  was triggered), deliberately treated as non-retryable in
  `binance_klines.py` on the same "don't retry into an active ban"
  principle BingX's own non-retryable statuses already follow.

### Verified — real, computed statistics (not an API fact, but load-bearing for how this data should be used)

- **Binance spot BTCUSDT vs. BingX BTC-USDT daily-close correlation
  over their full overlap (2021-05-14 through 2026-08-04, 1,909 common
  days): 1.000000.** Daily log-return correlation: **0.999955**
  (n=1,908). This shows the two venues' **daily price series** are
  extremely tightly linked — not merely assumed from "BTC is
  fungible." It does **not** by itself show that a trading *signal*
  developed on Binance data would transfer profitably to BingX: a real
  signal's performance also depends on volume, funding, basis,
  execution costs, and timing/alignment, none of which a price-only
  correlation measures. That question needs its own signal-specific,
  cost-inclusive backtest and paper validation — not asserted here.
- **Binance spot vs. futures basis, over their full overlap
  (2019-09-08 through 2026-08-04, 2,523 common days)**: mean
  `(futures-spot)/spot` = -0.0154%, stdev 0.0652%, range -0.74% to
  +1.80% — tight, and narrowing over time (mean absolute basis 0.057%
  in the first third of the overlap vs. ~0.044% in the later two
  thirds), consistent with a maturing derivatives market rather than a
  data-quality issue.

Only public, unauthenticated read endpoints (klines only) have been
called against the live API. No authenticated Binance endpoint has
ever been called by this project, and none is planned — see the
data-research-only framing above.

## LLM Usage Policy

Allowed: coding assistance, research support, backtest interpretation, log
summarization, risk review, documentation.

Not allowed: acting as the live trading decision maker. LLM-suggested
signals, risk changes, or order logic must go through backtest/paper
verification and human approval like any other change. This includes
future auto-retraining: a model retraining automatically is fine, that
model being auto-promoted to paper/live without human approval is not.

## Development Methodology

Use the GSD phase loop for anything beyond a trivial change: **Discuss →
Plan → Execute → Verify → Ship**. `Discuss` resolves ambiguity before any
code is written — for R3-risk components (OMS, Risk Gateway, Execution)
this step must not be skipped. `Execute` uses fresh-context subagents per
task so a multi-month, multi-exchange project doesn't degrade into the
context rot that broke the previous attempt at this project. `Verify` must
include actual test runs, not a claim that tests would pass.

A detailed design for work that hasn't started yet lives directly in
this file (in full, not summarized) until that work actually begins —
see `.planning/README.md`'s "Where does a design belong" for why, and
when it's safe to trim back down to a summary + pointer.

State assumptions and ask rather than silently pick between valid
interpretations — `Discuss` makes this mandatory for R3-risk work; treat
it as the default for everything else too, since a future session has
only this file, not this session's judgment, to go on.

Touch only what the task requires — no drive-by reformatting or adjacent
refactors. This matters most in CODEOWNERS-matched paths (`java/`,
`schemas/`, `configs/`, `.github/`), where an unrelated change makes an
already high-stakes diff harder to review — and just as much on
low-risk paths, which auto-merge on CI + CodeRabbit alone, meaning a
scope-creep change may never get a second look from anyone. Clean up
only the dead code your own change orphaned; flag pre-existing dead
code instead of removing it unasked.

TDD discipline (red-green-refactor: failing test → minimum code to pass →
refactor) is required for OMS, Risk Gateway, and Execution code — not
optional. This rule is adopted directly, without installing a separate
framework for it.

Anthropic's official Agent Teams feature is available but not enabled by
default — GSD's own subagent-per-task orchestration already covers this
project's parallel-execution needs. Turn Agent Teams on only if a concrete
need appears that GSD's model doesn't cover.

## Strategy Research Methodology

Several strategies have now been attempted for real (see "Strategy
Attempts So Far" below) — none has yet cleared the Eligibility Bar, so
nothing has been promoted to paper trading. These principles were
written before real strategy research began, because retrofitting
research rigor onto a strategy already believed "validated" isn't
realistic once research is underway — that's still the reasoning for
having them, even though "no strategy exists yet" is no longer true.
This is not itself an Implementation Priority item; it's a
standing constraint on strategy *research and validation* specifically —
it does not block building or testing the surrounding infrastructure
(paper broker, `ExchangeAdapter`, supervision loop skeletons in
Priorities #6–#8 can and should be built and tested with dummy/mock
signals independently of a validated strategy). What it does gate is
paper-trading *eligibility, operation, and promotion* for any strategy
run through that infrastructure — none of #6–#8 name that gate
explicitly, which is exactly why it's written down here rather than left
implicit.

Non-negotiable once strategy research begins:

- No strategy is eligible for paper trading without walk-forward
  validation (rolling train/validate windows), not a single train/test
  split — a single split can't distinguish a real edge from a result
  that happened to fit one historical window.
- Every backtest run against a given strategy/parameter set must be
  logged (parameters, results, timestamp). The number of variations
  tried is part of judging whether a result is genuine edge or data
  snooping; an untracked count makes that judgment impossible after the
  fact.
- A holdout data split must exist and stay untouched until a strategy is
  otherwise ready for paper trading — not used for iterative tuning.
  Touching it converts it from a validation check into just more
  training data.
- Look-ahead-bias protection already structural in `python/backtest/`
  (a strategy is only ever shown bars up to and including the current
  one) extends to feature engineering: no feature may be computed using
  statistics — mean, std, min/max, or similar — derived from data
  outside what would actually have been available at that point in
  time.
- Survivorship bias doesn't apply to the current single-symbol
  (BTC-USDT) scope — there's no universe-selection step for it to enter
  through. Revisit before any multi-symbol expansion: the market-data
  pipeline built for that must retain delisted/inactive symbols, not
  only currently-active ones, or backtests across that universe will be
  biased upward by construction.
- **No further parameter searching on the `BTC-USDT` 1h research window
  (2024-04-27T10:00Z → 2026-02-26T07:00Z, 16,078 bars).** (Added
  2026-07-29, human-approved; derivation in
  `.planning/sr-r-retrospective-closeout.md`.) 117 research selection
  trials have been run against it and its detection floor is ~1.21
  annualized Sharpe (`sr-r`). Every additional trial raises the `N` any
  future winner must be deflated against while adding no new evidence,
  so further search there is *strictly* value-destroying: raising `N`
  can only lower the DSR of any given result, never raise it. (Stated
  precisely, at CodeRabbit's prompting on the PR that applied this
  rule: the monotonicity is in `N` **at a fixed observed Sharpe**. A
  new trial could of course post a higher raw Sharpe and become the new
  winner — the argument against searching here is the *other* half of
  that sentence, "adding no new evidence": on a window with a ~1.21
  detection floor, such a result would be indistinguishable from luck
  regardless.) This window remains valid for
  **reproducing** a previously logged result, for **diagnosing** a
  mechanism (as `sr-o` did), and for **infrastructure** testing — none
  of which select a configuration. It is closed to *selection*.

  New strategy work goes to a window with usable statistical power: the
  `1d` path and its early-window holdout (`sr-t`), or a multi-symbol
  universe (which needs its own `Discuss` pass first, per the
  survivorship-bias clause above).

  **What this explicitly retires**, named so the decision is not
  silently reversed later by a reader of the old text: (a) the
  funding-extremity **edge-trigger rule change** (fire on any crossing
  into extreme, rather than requiring a flip to the opposite extreme),
  and (b) **lowering `entry_z_threshold` / `funding_zscore_lookback`**
  to generate more trades. CLAUDE.md previously framed both as
  "genuinely new configurations, not tuning" — and that framing was
  *correct on its own terms*: neither has been run, so neither is a
  retry. **The reason to retire them is different and does not
  contradict it.** It is not that they would be tuning; it is that the
  window they would be run on can no longer support a conclusion in
  either direction. Running them would produce a 19-fold result
  deflated against `N` = 118 or 119, on a window with a 1.21 detection
  floor. If the funding-extremity trigger design is still believed in,
  the honest way to test it is on the `1d` window under the holdout
  confirmation protocol below, with the specification committed first.

### Strategy Research Operational Design

The mechanics the paragraph above left deliberately open (experiment-
tracking format, walk-forward window sizing, holdout-split mechanics)
were designed on 2026-07-25 and built across four sequenced tasks, now
all merged to `main`: `python/data/` (historical BingX kline pipeline,
SQLite cache), `python/backtest/kline_window.py` + `python/metrics/`
(O(1) lookahead-safe iteration, position/equity/Sharpe/drawdown
reconstruction), `python/research/` (walk-forward harness, holdout
enforcement, the `runs/experiments.jsonl` experiment log), and
`python/research/strategies/ma_crossover.py` (a placeholder MA-crossover
`TrainableStrategy` proving the pipeline end-to-end for real against
live BingX data — not a validated strategy, see its docstring). Full
build detail, judgment calls, and any deviation from the original
design live in `.planning/sr-a-data-pipeline.md`,
`.planning/sr-b-engine-metrics.md`, `.planning/sr-c-walkforward-holdout.md`,
and `.planning/sr-d-placeholder-strategy.md` — this
section is trimmed to a pointer per `.planning/README.md`'s "Where does
a design belong" rule now that the work has actually happened.

Provisional default walk-forward windows (defined in bars, the
canonical unit, not calendar months): train = 8,640 bars (~90 days),
validate = 2,880 bars (~30 days), step = validate (2,880 bars) —
rolling (fixed-size sliding, not expanding), non-overlapping by
default.

**Walk-forward depth**: a real BingX backfill (2026-07-25/26) found only
~252 days of actual historical retention for `BTC-USDT`/`15m` (24,199
bars) — only 3 non-overlapping folds at the windows above, short of the
Eligibility Bar's "8-10 folds" floor. This was practically addressed,
not resolved as a one-time human decision: `sr-f` found `1h` bars have
~27 months of real BingX history (vs. ~8.3 months at `15m`, 16,078
research bars after the holdout cutoff) and moved primary strategy
research to `1h` bars with its own, smaller windows (`train_bars=2160`,
`validate_bars=720`, `step_bars=720` — distinct from the `15m`
defaults above, scaled down for the `1h` timeframe, not the same
numbers on a different unit), yielding **19** real folds — the fold
count behind every `1h`-timeframe result in "Strategy Attempts So Far"
below (the earlier `15m` runs counted there have 3).

**A third timeframe, `1d`, and an inverted holdout (`sr-t`,
2026-07-28)**: every one of the 1,839 logged backtest runs starts at or
after 2024-04-27T10:00:00Z (1h retention's floor — verified directly
against `runs/experiments.jsonl`), so **1d data before that date has
been touched by zero trials in this project's history**. `sr-t` wired
`1d` into the data pipeline and reserved that early window as a holdout:
`configs/research/holdout_1d.json` uses a new, optional, backward-
compatible `"holdout_side": "before"` config key (default `"after"`, so
the 15m/1h configs are unaffected), making the holdout the **earliest**
1,079 daily bars rather than a trailing slice. That looks backwards and
isn't — a holdout is data whose contents have informed no decision, and
here that is the early window; full reasoning in
`.planning/sr-t-daily-data-path.md` and `python/research/holdout.py`'s
module docstring. Detection floor ~0.96 annualized Sharpe over ~2.95
years, vs. ~2.57 for the 1h trailing holdout. **No strategy has been
written, run, or evaluated against 1d data** — deliberately, the
specification must be committed before that window is ever loaded.

**Backtest/Walk-Forward Eligibility Bar** (defaults — same status as
Risk Parameters: changing these needs explicit human approval; approved
as part of this design's 2026-07-25 sign-off, **fold-consistency clause
revised 2026-07-27** — full statistical derivation in
`.planning/sr-j-fold-diagnosis-and-eligibility-review.md`, summarized
here — and **clause 2's significance test, the minimum trade count, the
holdout single-window variant, and the CSCV/PBO revisit trigger all
revised 2026-07-29**, human-approved, derivation in
`.planning/sr-r-retrospective-closeout.md`): the original "positive
annualized Sharpe in every fold" wording
was found to be statistically stricter than intended — even a
genuinely strong, real 80%-true-edge strategy clears a literal 19/19
sweep only ~1.4% of the time, so demanding literal 100% mostly measures
luck, not edge. Replaced with two required checks:
1. **Fold consistency**: at least 80-90% of folds show positive
   annualized Sharpe (not literal 100%).
2. **Aggregate significance**: the full set of per-fold Sharpe ratios
   must reject "no real edge" via *both* a binomial sign test (fold
   win/loss count against p=0.5) *and* a **Deflated Sharpe Ratio**
   (Bailey & López de Prado 2014,
   `research/eligibility.py::evaluate_deflated_sharpe`) of at least
   **0.95**, computed on daily-resampled returns, against the
   **project-level** selection-trial count `N` from
   `research/overfitting_check.py::check_project_combination_count`
   (`research_selection_trials`) and the variance of that same trial
   set's Sharpe estimates. The one-sample t-test it replaces may still
   be reported for continuity but is no longer a pass criterion.
   Both required, not either — they catch different failure modes
   (win-rate-only noise vs. aggregate-risk-adjusted-return noise a
   fold-percentage alone wouldn't rule out).

   The project-level `N`, not the family-level one, because strategy
   families in this project were compared against each other after
   their results were known — see
   `.planning/sr-r-retrospective-closeout.md`.

   The t-test asks "is this mean fold Sharpe distinguishable from
   zero?" and has *no notion whatever of how much searching produced
   it*. With 117 logged research trials against 1.8 years of a single
   symbol, that is the wrong question. DSR is the standard, published
   correction. This is not a tightening for its own sake — it is the
   difference between a statistic that could have been passed by
   searching hard enough and one that cannot. Adopted 2026-07-29 rather
   than after a future run, so it can never be accused of being fitted
   to a result: retrospectively it changes nothing, because every run
   in the log already fails the t-test too.

   `sr-j`'s **disclosed open cost** — "the t-test's exact p-value needs
   either `scipy` or an accepted approximation" — is now closed twice
   over: `sr-k` implemented the exact t-distribution p-value via the
   regularized incomplete beta function, and DSR needs only
   `statistics.NormalDist`. Both are stdlib-only.

   **The `N` this gate is computed against must fail closed** (tightened
   on review of the applying PR; this preserves the approved 0.95
   threshold's intent rather than changing it). DSR requires an `N`, and
   `N` requires `research/lineage.py`'s curated family map
   (`FAMILY_BY_STRATEGY_ID`) to stay current. A strategy run without a
   `strategy_family=` argument and without a curated entry resolves to
   its own single-member family (`FamilyResolution.source ==
   "unmapped"`), which **understates** `N` — and understating `N` makes
   this gate *weaker* than 0.95 intends, because a smaller `N` inflates
   DSR. So: **a DSR computed against an `"unmapped"` family resolution —
   or against any resolution whose `note` indicates an under-counted `N`
   — is not admissible as an Eligibility Bar pass.** The curated entry
   (with its required
   `.planning/` citation) or an explicit `strategy_family=` must exist
   *first*; absent either, the run is reported as unevaluable for
   aggregate significance — never passed on a fallback count.
   `resolve_family` already surfaces both conditions rather than hiding
   them (`source`, `note`); this clause makes acting on that signal
   mandatory rather than optional, which is what turns "keep the map
   honest" from a diagnostic nicety into a real obligation.

   One asymmetry, deliberate and *not* treated the same way: an
   unrecognized **logged** `strategy_family` defaults its `purpose` to
   `"research"`, which **overstates** selection bias rather than
   understating it. That fails in the safe direction — it can only lower
   DSR — so it proceeds, carrying its note.

All other criteria are unchanged by the 2026-07-27 revision: minimum
8-10 folds for the result to be considered credible (met since `sr-f`
moved primary research to `1h` bars — see "Walk-forward depth" above);
max drawdown ceiling 20-25% per-fold and
aggregate; the **minimum total trade count, itself revised 2026-07-29 —
see immediately below**; profit factor floor 1.3-1.5
(cushion for backtest-to-live slippage/fee mismodeling and the
funding-rate gap — perpetual funding-rate P&L was not modeled anywhere
in this pipeline when this floor was set; `sr-m` built additive/opt-in
funding P&L modeling, and `sr-n`
(`.planning/sr-n-funding-rate-strategy.md`) then actually threaded a real
`funding_rates` series through Configuration C's own 19-fold walk-
forward: the real effect was small — mean Sharpe +0.027 to +0.039, mean
profit factor 1.967 to 1.968 — so the profit-factor floor's slippage/fee
cushion is still doing real work here, not funding P&L; this is one real
data point, not a general proof the funding-rate gap never matters for a
strategy with different (e.g. much shorter, funding-settlement-spanning)
holding-period characteristics). A fold's
`profit_factor: null` is interpreted according to why it's null: a
zero-trade fold already fails eligibility via the Sharpe/trade-count
requirements regardless of profit factor; a fold where every closed
trade won (zero losing trades) trivially satisfies the floor — there's
no evidence of a poor risk/reward ratio to reject.

**Minimum trade count, revised 2026-07-29** (replacing the flat
"minimum 100 total trades across all folds" and its "may unfairly
penalize a legitimately low-frequency strategy — apply judgment"
hedge). Resolved concretely and **in advance of any daily run**, rather
than after one fails on a technicality — which is the only time such a
change can be made without it being tuning-after-the-fact:

> **Minimum trade count, scaled to the strategy's own frequency.** The
> floor is
> `max(30, min(100, floor(total_evaluated_bars / bars_per_day / 20)))` —
> i.e. roughly one trade per 20 evaluated days, clamped to `[30, 100]`.
> Concretely, at the three geometries this project actually uses:
>
> | Timeframe | Evaluated bars | Evaluated days | days ÷ 20 | Floor after clamp |
> |---|---|---|---|---|
> | 1h, 19 folds × 720 | 13,680 | 570 | 28 | **30** |
> | 15m, 3 folds × 2,880 | 8,640 | 90 | 4 | **30** |
> | 1d, 822 research bars (`sr-t`) | 822 | 822 | 41 | **41** |
>
> The **absolute floor of 30** is the binding constraint at every
> timeframe this project actually uses, so in practice this reads:
> **the floor becomes 30, not 100**, and the 100 cap only re-engages
> for a strategy trading far more often than any attempted so far.
>
> A run below the floor is reported `INCONCLUSIVE-DATA-LIMITED` —
> neither a pass nor a fail. It is not evidence against the strategy
> and must not be written up as such.

**Why 30, and why it is not a weakening.** 30 is not folklore borrowed
from "n=30 for the CLT" (`sr-g` correctly demolished the *"30 trades
per parameter"* rule as having no rigorous origin — that is a different
claim, about *per-parameter* counts). It is the point at which a sign
test over trade outcomes can be expected to have *usable* power, and
below which per-trade statistics are dominated by their own estimation
noise. Stated that way deliberately, on review: **not** "the point at
which a sign test first has any power at all", which would contradict
this same section's own holdout arithmetic below — at n=5 a literal 5/5
sweep does reach p = 0.03125. The point is that below ~30 nothing short
of a near-sweep is detectable, which is a floor on *usable* power, not
on power existing. The
real justification for choosing it here is empirical and specific: **a
daily strategy over `sr-t`'s 822-bar 1d research window yields roughly
30-60 trades**, so a 100-trade floor would reject every possible daily
strategy *on frequency alone, before looking at its returns* — a
criterion that cannot be satisfied is not a criterion. Retrospectively
it resolves nothing convenient either: `regime-momentum-btc-15m` (16
trades) and both funding-extremity runs (7 and 14) all stay below 30,
so **the funding-extremity result remains genuinely inconclusive under
the new floor as well** — worth stating because it removes any
suspicion that this change was reverse-engineered to resolve that
specific open question. It does not.

**Holdout confirmation.** The holdout confirmation run must be the only
holdout access on record for that `strategy_id`, and must clear the
following single-window variant of this bar (defined 2026-07-29,
replacing the previously-undefined "(single-window version)" — the
fold-based clauses have no meaning on one window):

> **Holdout confirmation (single-window variant).** A holdout run is a
> single evaluation window, so the fold-based clauses do not apply and
> are not to be simulated by chopping the holdout into pseudo-folds.
> The holdout run must instead clear:
>
> 1. **PSR ≥ 0.95** (`evaluate_psr` against a zero benchmark) on
>    daily-resampled holdout returns, using measured return moments.
>    Deliberately **PSR, not DSR**: the holdout was never searched
>    over — one access, one run, on data no decision has touched — so
>    there is no selection bias to deflate, and `N`=1 makes DSR
>    identical to PSR anyway.
> 2. The **non-fold criteria unchanged**: max drawdown ≤ 20-25%, the
>    trade count floor in force at the time, and the profit-factor
>    floor of 1.3-1.5.
> 3. The run's observed Sharpe must exceed the **holdout window's own
>    detection floor** (`retrospective.detection_floor_sharpe`), stated
>    explicitly in the confirmation report. If it does not, the holdout
>    is reported as **not powered to confirm**, and clearing the other
>    criteria does not constitute confirmation.
>
> **Explicitly NOT required: any fold-count, fold-consistency, or
> sign-test criterion.**

**Clause 2's "in force at the time" means pinned *before* access, not
chosen after** (added on review of the applying PR; the approved wording
left it ambiguous, and the ambiguity is exploitable). The trade-count
floor, the drawdown ceiling, and the profit-factor floor must be written
into the confirmation record — with the CLAUDE.md revision date they
come from — **before the holdout is loaded**, and a later change to any
of them is not applied retroactively to a holdout already spent. This is
the same discipline the `1d` window is already reserved under ("the
specification must be committed before that window is ever loaded"),
stated here so it covers the *criteria* too, not just the strategy
specification. A holdout judged against criteria selected after seeing
it is not a holdout.

The fold criteria are dropped rather than scaled down because scaling
them down reproduces the exact error `sr-j` already identified and
corrected once: at n=5 folds the **only** sign-test outcome clearing
α=0.05 is a literal **5/5 sweep** (p = 0.03125); 4/5 gives p = 0.1875,
not close. Applying the fold-based bar to a 5-window holdout would
therefore demand a literal 100% sweep — **stricter than the 19-fold
bar**, which `sr-j` set at 80-90% precisely because demanding literal
100% "mostly measures luck, not edge". **Detection floors to expect**,
so a future confirmation run is not surprised by them: the 1h trailing
holdout is **~2.57** annualized Sharpe (`sr-q`), the 1d early-window
holdout **~0.96** over ~2.95 years (`sr-t`). The 1d holdout is by a
wide margin this project's best-powered untouched window — the only one
whose floor sits below a plausible real edge.

**CSCV / PBO revisit trigger** (added 2026-07-29, replacing `sr-g`
Finding 3's "revisit at Implementation Priority #9 … the point where
this project's actual hyperparameter-search scale grows enough", which
was not falsifiable — "enough" was unmeasured, and Priority #9 is a
milestone whose arrival says nothing about search scale). The
recommendation itself is unchanged — **continue to defer** — but now on
a condition a script can check:

> **CSCV / PBO revisit trigger (checkable, not a milestone).**
> Implement Combinatorially Symmetric Cross-Validation and the
> Probability of Backtest Overfitting when **both** hold:
>
> **(a)** one research family has evaluated **≥ 50 candidates over one
> common fold geometry**, with **per-candidate equity curves
> retained**; and
>
> **(b)** that family's winner reaches **DSR ≥ 0.90 at the family-level
> `N`**.
>
> Both are computable from `runs/experiments.jsonl` by a script;
> neither requires a judgment call. (b) exists so CSCV is spent on a
> candidate that has already survived the cheaper test — PBO answers
> "is this winner's out-of-sample rank better than median?", which is
> only an interesting question about something that already looks good.

**What checking the trigger actually reads**, named concretely on review
of the applying PR — a trigger nobody can evaluate is not a trigger:

- **Family membership**: `runs/experiments.jsonl`'s optional
  `strategy_family` key (`lineage.STRATEGY_FAMILY_KEY`, written by
  `experiment_log.log_run` only when non-`None`, so *absent* means
  "attribute via the curated map"), else
  `research/lineage.py::FAMILY_BY_STRATEGY_ID`. Which of the two answered
  is recorded on `FamilyResolution.source`
  (`logged`/`curated_map`/`unmapped`) — and an `unmapped` resolution is
  inadmissible here for the same fail-closed reason as in clause 2 above.
- **Lineage-map version**: the map carries no version field, deliberately
  — what pins it is git history plus the required per-entry `.planning/`
  citation (15 entries as of 2026-07-28, one per `strategy_id` that has
  ever appeared in the log). A trigger evaluation must therefore record
  the `research/lineage.py` commit sha it ran against, or the candidate
  count it produces is not reproducible.
- **Per-candidate equity curves**: **stored nowhere today.** This is the
  hard blocker below, not a lookup — `Metrics.equity_curve` exists in
  memory only and nothing persists it per candidate. Where retention
  should live (an `equity_curve` field back in `_metrics_summary`, versus
  a sidecar artifact keyed by `run_id`/`candidate_index` that avoids
  putting a 720-point series on every log line) is a real design choice
  and is **deliberately left open** rather than settled in a
  documentation change. Condition (a) cannot be evaluated at all until
  one of them is built — that prerequisite is part of the trigger, not a
  footnote to it.
- **Condition (b)**: `research/eligibility.py::evaluate_deflated_sharpe`,
  against the family-level `N` from
  `research/overfitting_check.py::check_project_combination_count`.

"Computable from `runs/experiments.jsonl` by a script" above means *no
judgment call is required* — **not** *the JSONL alone suffices*, which it
does not.

Three concrete current reasons to keep deferring, not a vibe (full
detail in `.planning/sr-r-retrospective-closeout.md`): **CSCV needs
per-candidate equity curves and nothing in this repo retains them** —
`walkforward._metrics_summary` deliberately omits `equity_curve` (and
**still does after `sr-q`**, which added `return_skewness`/
`return_kurtosis`/`num_returns` *instead*, precisely so a logged record
stays PSR/DSR-evaluable without one), so zero of the 1,839 logged
records carry one and re-judging them under CSCV is arithmetically
impossible without re-running every backtest; the historical trials are
not commensurable (2 timeframes, 4 `walk_forward_config` shapes, 5
lineage families, and the largest single grid ever run is **6**
candidates against condition (a)'s ≥50); and no decision would change.
That last one, stated precisely on review rather than as `sr-r`'s looser
"every configuration is already rejected": the **14**
`REJECTED`/`REJECTED-UNDERPOWERED` rows are rejected by five to eight
orders of magnitude, so PBO would be spending real implementation effort
to re-reject them, and the **4** `INCONCLUSIVE-DATA-LIMITED` rows are
inconclusive for a *sample-size* reason PBO does not address — it
measures whether a winner's out-of-sample rank beats median, it does not
manufacture trades a 7- or 14-trade run never had. CSCV would leave that
second group exactly where it is.

**Correction to `sr-r`'s own wording, verified against
`python/research/walkforward.py` on 2026-07-29** (found on review of the
applying PR): `sr-r` says `_metrics_summary` "discarded `equity_curve`
before logging **until** `sr-q`", which reads as though `sr-q` fixed it.
It did not — `_metrics_summary` still drops `equity_curve` by design, and
its own docstring says so. The consequence is load-bearing for this
trigger rather than cosmetic: condition (a)'s "per-candidate equity
curves retained" is **not satisfiable by today's logging at all**, so
equity-curve retention has to be built before the trigger can ever fire.
That is a further reason this is a deferral rather than a near-term plan,
not a reason to weaken the condition.

### Strategy Attempts So Far (closed out 2026-07-29; BTC-only price-signal research line ended 2026-07-30, `sr-v`; first macro-conditioned attempt `sr-x`, 2026-08-03; second and, per explicit human decision, last planned macro-conditioned attempt `sr-y`, 2026-08-04)

Eight strategy attempts across **four research families** (plus
infrastructure demos) were built and walk-forward validated against real
BingX data in Tasks E-L and N-O: naive SMA crossover, ATR-risk-managed
crossover (15m and 1h), a multi-lookback ensemble with ADX regime
weighting and volatility targeting (refined into "Configuration C"),
regime-gated mean-reversion, a momentum/mean-reversion blend, an
on-balance-volume trend strategy, and a funding-rate-extremity
contrarian strategy. Per-task detail and honest negative findings:
`.planning/sr-e-*.md` through `.planning/sr-o-*.md`.

**`sr-r` closed this line of research out statistically.** Every
distinct multi-fold run in the log — **18 configurations de-duplicated
from 33 runs** — was re-judged under `sr-p`'s honest trial count and
`sr-q`'s Deflated Sharpe Ratio. Full table:
`.planning/sr-r-retrospective-closeout.md`.

**Result: nothing survives. 0 of 18.** The best result in the project's
history (Configuration C with funding P&L, mean annualized Sharpe
**+0.039**) reaches **DSR = 2.0e-05** against the project's **117**
research selection trials — indistinguishable from the best of 117 coin
flips. Twelve configurations are `REJECTED`, two
`REJECTED-UNDERPOWERED`, four `INCONCLUSIVE-DATA-LIMITED` (below the
trade-count floor: both funding-extremity runs at 7 and 14 trades, and
two early runs).

**The single most important finding is about the window, not the
strategies.** The 1h research window's own **detection floor is ~1.21
annualized Sharpe** (one-sided α=0.05 over 1.84 years); the 15m
window's is **~2.18**. A real edge of 0.4-0.8 Sharpe — the range
credible institutional trend-following actually reports — **could not
have been detected here by any strategy, however well specified**. So
"DSR ≈ 0" across the board means **not shown**, and emphatically **not
shown absent**. Configuration C would have needed an annualized Sharpe
of **4.6** to clear DSR 0.95 at this `N` — a target that says more
about having searched 117 times against 1.8 years of one symbol than
about any strategy.

**Consequence: the 1h research window is spent** (see the standing rule
under "Non-negotiable once strategy research begins" above). Further
searching on it cannot produce a defensible result, because every
additional trial raises the `N` that any future winner must be deflated
against. The live options are therefore about *changing the evidence
base*, not the signal:

1. **~~The `1d` early-window holdout (`sr-t`)~~ — spent, INCONCLUSIVE
   (`sr-v`, 2026-07-30; full result below).** This was the only
   untouched single-symbol BTC-USDT price window this project had with
   a detection floor below a plausible real edge; it no longer is.
2. **Multi-symbol expansion, with survivorship-safe data.** A
   meaningful share of the Sharpe reported by the institutional
   research benchmarked in `sr-g` plausibly comes from cross-symbol
   diversification a single-symbol design cannot access. A real
   architecture reconsideration (it touches the data pipeline's
   survivorship-bias handling, per Strategy Research Methodology) that
   deserves its own `Discuss` pass — now the pre-registration's own
   named remedy for the INCONCLUSIVE result below, not just an item on
   a list.
3. **A genuinely different data source entirely** — the
   pre-registration's other named remedy, alongside (2); neither is
   chosen yet, and choosing between them is a human `Discuss`, not
   resolved here.
4. **Stop adding strategies and build the infrastructure instead**
   (Priorities #8-#10). Nothing about the paper-trading loop,
   supervision, or `ExchangeAdapter` work is blocked by the absence of
   a validated strategy — CLAUDE.md already says they can and should
   proceed on dummy signals. Unlike (2)/(3), this does not require
   resolving the BTC-only price-signal question first.

**Explicitly NOT a live option**: another search, threshold, or
lookback set, on any timeframe, against any signal class — the
pre-registration's own pre-committed stopping rule for this exact
outcome (see below).

**Retired**: the two funding-extremity follow-ups previously listed
here as live candidates (changing the edge-trigger rule; lowering
`entry_z_threshold`/`funding_zscore_lookback`). Both are more searching
on the spent 1h window — see the standing rule above.

**`sr-s`/`sr-t`/`sr-u`/`sr-v` spent the `1d` holdout option above
(2026-07-30).** `sr-s` built the pre-registration mechanism
(`python/research/preregistration.py`) so a single attempt's `N=1`
claim would be provable, not merely asserted, rather than another
untracked trial. `sr-t` wired the `1d` interval into the data pipeline
and reserved its early window as the holdout (see "A third timeframe,
`1d`" above). `sr-u` then committed the full specification — a
zero-fitted-parameter Moskowitz-Ooi-Pedersen (2012) daily
time-series-momentum ensemble (the literature's own canonical
21/63/126/252-trading-day lookback set, constant 20%-annualized-
vol-target sizing, no ADX gate, no ATR stop, no funding signal —
`free_parameter_count: 0`) and its registration
(`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`)
**before** any `1d` price data was ever accessed. `sr-v` then executed
it — exactly once, for real, against the registered window
(2021-05-14 through 2024-04-26, 1,079 daily bars):

- **PSR 0.9367** — positive, but below the registered 0.95 threshold.
- **Observed annualized Sharpe 0.882** — below the window's own 0.9567
  detection floor ("not powered to confirm", clause 3).
- **26 trades** — below the 53-trade frequency-scaled floor.
- Max drawdown (12.0%) and profit factor (2.87) both cleared
  comfortably, but PASS requires all five checks, and this result hits
  three separate INCONCLUSIVE triggers at once.

**Verdict: INCONCLUSIVE** — not a rejection, not a pass. Real, full
result, the logged record
(`run_id=8143a525-3159-447b-991d-2f11a0ef790b`), and an honest account
of the one (non-hypothesis-related) invocation bug hit and fixed during
execution: `.planning/sr-v-preregistered-attempt-result.md`.

**Per the pre-registration's own pre-committed meta-consequence**
(written before the run, not decided after seeing it): this
INCONCLUSIVE result **ends the BTC-only price-signal research program
as a line of work.** "The only legitimate remedy is more calendar time
or more data, explicitly NOT another search, another threshold, or
another lookback set" — the next move is a named structural change,
options (2) and (3) above, not another grid on any timeframe against
any signal class. Whether and how to pursue either is a human
`Discuss`, not resolved here.

**`sr-x` (2026-08-03) is the first real pursuit of remedy (3) named
above ("a genuinely different data source entirely") — authorized
directly via this task's own brief, which pre-decided the specific
hypothesis before any code was written ("decided, not yours to
redesign"); that authorization is the human `Discuss` remedy (3)'s
listing called for, for this one specific instance (FRED macro data,
`DFII10` specifically), not a resolution of the broader (2)-vs-(3)
comparison, which remains open.** It ran the first genuinely
non-BTC-price-derived signal this project has ever tested (precise
wording, not "non-price-derived" generally — `sr-y` below tests `SP500`,
itself a price index, just not BTC's own): a 10-year
real-yield (`DFII10`, via FRED) trend, INVERTED (falling real yields →
BTC-bullish/long; rising →
BTC-bearish/short), against the untouched BTC 1d **research** split
(2024-04-27 onward — never the spent 1d holdout), via ordinary
iterative walk-forward, not a pre-registered holdout attempt.
Zero-fitted-parameter (`total_candidates: 1`, a single pre-committed
63-trading-day lookback — the middle of `daily_tsmom_ensemble.py`'s own
literature-sourced 21/63/126/252 set), sizing via the standing 20%-vol-
target convention, no ADX/ATR/funding/price-momentum combination — a
clean, standalone test. `train_bars=90, validate_bars=60, step_bars=60`
→ 12 folds over 822 bars, chosen by bar-count arithmetic alone before
the run, matching this project's own detection-floor-driven fold-sizing
precedent.

**Result (`run_id=848a9f13-9fc7-478c-90ac-70cf03a8025c`,
`strategy_family=macro-conditioned`): mean annualized Sharpe −1.303**
(1 of 12 folds positive), **34 trades against a 36-trade frequency-
scaled floor** (60 validate bars × 12 folds = 720 evaluated bars, not
the full 822 research bars — 2 trades short), worst-fold drawdown 27.3%
(over the 20-25% ceiling), mean profit factor 0.32 (under the 1.3-1.5
floor), sign test p=0.9998 and one-sided t-test p=0.978, PSR (N=1)
0.034, DSR 0.0135 at the family's own N=2, DSR
5.0×10⁻¹¹ at the project-level research N=119 (117 prior + this
family's 2).

**Verdict: INCONCLUSIVE-DATA-LIMITED** (below the 36-trade floor — per
the standing rule above, "neither a pass nor a fail... not evidence
against the strategy"). The remaining metrics above are reported
descriptively only; because the run is below the trade-count floor,
they do not constitute a pass, a fail, or evidence against the
strategy, and are not grounds for a directional conclusion or a
follow-up change. Three temptations (loosen the fold geometry to clear
the trade floor; flip the inversion; shorten the lookback) were named
and not acted on, matching `sr-v`'s own precedent for handling a
near-miss honestly. Full result, statistical detail, and the real
`DFII10` backfill (6,151 rows, 2003-01-02 onward): `.planning/sr-x-
macro-real-yield-strategy.md`.

This does not by itself close off the macro-data-source remedy the way
`sr-v` closed off BTC-only price signals — one lookback/inversion/
geometry combination on one FRED series is a first data point, not an
exhaustive test of "is macro data useful at all". `sr-y` (immediately
below) is the second data point, on the same window and fold geometry.

**`sr-y` (2026-08-04) is the second and, per explicit human decision
made at that task's own outset, last planned macro-conditioned
attempt.** It tests the S&P 500 (`SP500`, via FRED)'s own trend, NOT
INVERTED (rising S&P 500/risk-on → BTC-bullish/long; falling → BTC-
bearish/short — the opposite structural shape from `sr-x`'s inverse
real-yield relationship), against the same untouched BTC 1d
**research** split `sr-x` used, via ordinary iterative walk-forward.
Same zero-fitted-parameter discipline (`total_candidates: 1`), the SAME
63-trading-day lookback `sr-x` used — reused deliberately for direct
comparability between the two macro attempts rather than re-derived —
same 20%-vol-target sizing, same Option B order emission, no ADX/ATR/
funding/price-momentum/real-yield combination. Same fold geometry as
`sr-x` (`train_bars=90, validate_bars=60, step_bars=60` → 12 folds over
822 bars), chosen for direct comparability per this task's own brief.

**Result (`run_id=e0abfeaa-cfb3-49b5-b247-955a54789baa`,
`strategy_family=macro-conditioned`): mean annualized Sharpe −0.284**
(7 of 12 folds positive, 58.3% fold consistency), **19 trades against
the same 36-trade frequency-scaled floor** (17 trades short — a wider
miss than `sr-x`'s 2-trade near-miss), worst-fold drawdown 14.4%
(comfortably inside the 20-25% ceiling, unlike `sr-x`'s 27.3%), mean
profit factor 0.21 (under the 1.3-1.5 floor, and lower than `sr-x`'s
own 0.32), sign test p=0.387 and one-sided t-test p=0.612 (both far
less extreme than `sr-x`'s near-1.0 values, but still failing to reject
the null), PSR (N=1) 0.345, DSR 0.137 at the family's own N=4, DSR
2.20×10⁻⁷ at the project-level research N=121 (117 pre-`sr-x` + 2 from
`sr-x`'s own strategy + 2 from this run). A real, disclosed logging bug
(a driver-script omission sent 12 of 13 records to a stray local file
instead of the shared log) was found and fixed — by appending the real,
already-computed records to the correct log, not by re-running — before
any figure above was read off; full account in
`.planning/sr-y-macro-sp500-strategy.md`. That fix also recomputed
`sr-x`'s own family/project-level DSR downward (`N=2→4`, `N=119→121`:
`DSR 0.0135→0.00586`, `DSR 5.0×10⁻¹¹→4.76×10⁻¹¹`) — a real, disclosed
instance of this project's own "every additional trial lowers the DSR
of an existing result" rule in action, not a change to `sr-x`'s already-
decisive verdict.

**Verdict: INCONCLUSIVE-DATA-LIMITED** (below the 36-trade floor by a
wider margin than `sr-x`). The remaining metrics are reported
descriptively only and are not grounds for a directional conclusion —
several read less extreme than `sr-x`'s (fold consistency, sign/t-test
p-values, drawdown, and a point estimate whose magnitude sits below its
own detection floor rather than past it in the wrong direction) while
one reads more extreme (profit factor); this mixed pattern is not
evidence that S&P 500 trend is closer to (or further from) a real edge
than real yield trend — both runs are simply underpowered by trade
count. Three temptations (loosen the fold geometry to clear the trade
floor; flip to the inverted mapping after seeing a negative result;
shorten the lookback, whose mechanism a real sign-distribution check
made unusually legible — the S&P 500 trend at 63 trading days flipped
sign only 10 times in ~2.5 years of the research window) were named and
not acted on. Full result, statistical detail, the sign-correctness
verification, and the disclosed logging-bug account:
`.planning/sr-y-macro-sp500-strategy.md`.

**Per this task's own governing brief, this was the last planned macro
attempt** before this project's research line either pivots to on-chain
data or pauses — `DGS10` (nominal 10-year yield) and `DTWEXBGS` (dollar
index) remain cached (`sr-w`) but untested, and testing either is not
currently scheduled; it would need its own fresh authorization the same
way the real-yield and S&P 500 hypotheses each were. The broader (2)
multi-symbol-expansion vs. (3) genuinely-different-data-source choice
named earlier in this section remains open — two data points within
option (3) do not resolve it, and resolving it is a human `Discuss`, not
something either macro task decided on its own authority.

**`sr-aa`/`sr-ab` (2026-08-05) is the second, independent same-asset-
alternate-venue replication of `sr-u`/`sr-v`'s identical zero-fitted-
parameter daily-TSMOM hypothesis** — same code
(`research/strategies/daily_tsmom_ensemble.py`, byte-for-byte
unmodified), same 21/63/126/252-trading-day lookback set, same 20%-vol-
target sizing, `free_parameter_count: 0` — this time against Binance
spot BTCUSDT's own pre-2021 "virgin" window (2017-08-17 through
2021-05-13, 1,366 daily bars, `configs/research/holdout_1d_binance_
virgin.json`), rather than another search, threshold, or lookback set.
`sr-aa` registered the hypothesis and holdout config
(`configs/research/preregistrations/daily-tsmom-ensemble-binance-virgin-
holdout.json`) before any access; `sr-ab` executed it for real. Two
real, disclosed, non-hypothesis infrastructure bugs were hit and fixed
during execution — a relative-path invocation gotcha (the same class
`sr-v` already documented) and a missing Binance backfill in the
shared, gitignored `klines.sqlite3` (the original `sr-z`/`sr-aa`
backfill lived in a since-cleaned-up isolated git worktree) — both
fixed by re-running the existing, unmodified `backfill_binance.py` for
real against the live Binance API, not by touching any strategy,
registration, or config file. Because the empty-result attempt this
caused had already technically consumed the single-access claim (per
`research/holdout.py`'s own pre-disclosed "the claim is written once
the read completes, even on an empty result" behavior, and exposed
zero real information about the window), completing the real access
required a second, disclosed `force_reclaim_reason` — full accounting
in `.planning/sr-ab-binance-virgin-holdout-result.md`.

**Result (`run_id=a84d52ba-5f5d-43bd-a528-3d5cd494208a`,
`strategy_family=daily-tsmom`): PSR 0.9945** (above the 0.95
threshold), **observed annualized Sharpe 1.305** (above the window's
own 0.8503 detection floor), **profit factor 7.68** (above the 1.3
floor) — three of five gating checks clear cleanly, all three by wide
margins and all three stronger than `sr-v`'s own corresponding numbers
(PSR 0.9367, Sharpe 0.882, profit factor 2.87). The remaining two
checks miss by very narrow margins: **64 trades against a 68-trade
floor** (4 short), and **max drawdown 20.135% against the 20%
ceiling** (0.135 percentage points over).

**Verdict: INCONCLUSIVE** (2 of 5 gating checks fail). Per the
registration's own pre-committed `outcome_interpretation.INCONCLUSIVE`
text — reconsidered honestly rather than copied from `sr-u`'s older
wording, which this registration deliberately superseded for this
specific attempt: this result **parks the zero-parameter daily-TSMOM-
on-BTC-spot-price hypothesis specifically** — the only remaining
legitimate remedy for this hypothesis is a structurally different
signal class or a structurally different asset universe, not another
exchange's price series for the same instrument — and does **not**
retroactively validate or invalidate `sr-v`'s own BingX-window
INCONCLUSIVE result, which stands unaffected on its own terms. It
**does** close off same-asset alternate-venue replication specifically
as a further remedy for this hypothesis — Binance's pre-2021 window
was this project's last remaining independent-ish BTC-price data
source (Binance/BingX daily closes correlate at 0.999955, per `sr-z`).
It does **not** close off CLAUDE.md's remedy (2) (multi-symbol
expansion with survivorship-safe data) or remedy (3) (a genuinely
different, non-price-index asset class or data source, e.g. on-chain
data, named in `sr-y`'s own closing text) — both remain open,
undecided, human-`Discuss` questions neither `sr-aa` nor `sr-ab`
resolves. The temptation to read significance into how narrow both
misses are was named and not acted on, matching `sr-v`'s own precedent
for handling a near-miss honestly. Full result, the two infrastructure-
bug accounts, and a side-by-side comparison with `sr-v`'s own numbers:
`.planning/sr-ab-binance-virgin-holdout-result.md`.

**`sr-ac` (2026-08-05) is a retrospective statistical meta-analysis of
`sr-v` and `sr-ab` together — not a new strategy attempt, not a new
trial, and not a new pre-registration.** No holdout data was accessed to
produce it; everything is computed from the two runs' own already-logged
summary statistics. It answers the human operator's reasonable question
— given the pattern of two narrow INCONCLUSIVE misses on the same
zero-fitted-parameter hypothesis, how strong is the combined case,
really — as honestly and completely as already-logged data allows.

Two independent, disjoint-sample significance tests for the same null
("true Sharpe ≤ 0") can be combined via Stouffer's (weighted) Z-score
method (Stouffer et al. 1949; sample-size weighting per Whitlock 2005),
using each run's own already-logged `psr.z_score`
(`sr-v`: 1.5274, n=1078; `sr-ab`: 2.5410, n=1365) — a new, tested
function, `research.meta_analysis.combine_z_scores`. **Result: combined
Z = 2.914, corresponding probability Φ(Z) = 0.9982 — clears the
project's standing 0.95 convention comfortably**, stronger than either
individual PSR (`sr-v`: 0.9367; `sr-ab`: 0.9945).

**That significance result does not, and cannot, produce a formal PASS,
for a separate, purely mathematical reason established independently of
it.** For any chronological concatenation of two return series, the
combined max drawdown is provably `>= max(leg 1's own max drawdown, leg
2's own max drawdown)` — proved directly (the combined running peak at
any point can only be raised, never lowered, by the other leg's own
levels) and confirmed numerically against 2,000 synthetic equity-curve
pairs. Concatenating `sr-ab` (2017-2021) then `sr-v` (2021-2024) in real
calendar order: `combined_drawdown >= max(0.1199, 0.2014) = 0.2014` —
**already over the registered 0.20 ceiling**, before the true combined
figure (which could only be higher, never lower) is even considered. A
full PASS on the combined series' drawdown criterion is therefore
mathematically impossible regardless of the significance result above.

Combined trade count sums exactly (no path-dependency, unlike drawdown):
`26 + 64 = 90`. The real combined frequency-scaled floor, recomputed via
`research.preregistration.frequency_scaled_min_trades` against the
combined 2,445 bars (not hand-arithmetic): **100** — so 90 combined
trades falls 10 short. Profit factor is comfortably clear on both legs
individually (2.87, 7.68, both far above the 1.3 floor) but a rigorous
*pooled* figure is not reconstructable from logged fields alone (gross
win/loss sums aren't separately logged, only the ratio) — not forced as
an approximation, and not the binding constraint either way.

**Bottom line, stated as plainly as the numbers allow**: the combined
significance is real and meaningfully stronger than either individual
result — genuine evidence, not an artifact of this analysis. It does
**not** show a live-tradeable strategy: the drawdown ceiling is a
practical risk-control limit and the trade-count floor is a
minimum-evidence-volume requirement, both independent of whether a mean
effect is statistically real, and neither is overridden by a strong
Z-score answering a different question. It does **not** resolve which of
CLAUDE.md's two live options — multi-symbol expansion (remedy 2) or a
human policy-exception decision to proceed toward paper trading despite
not formally clearing every gate — is the right next step; that remains
an open human `Discuss`, fed by these numbers, not decided by them. Such
a policy exception, if ever granted, would be a decision to proceed
DESPITE the unmet drawdown/trade-count gates — it would not retroactively
satisfy them, would not waive this section's own non-negotiable rolling
walk-forward validation requirement (a single-window holdout, combined or
not, is still not that), and would not substitute for the Eligibility
Bar's holdout single-window variant itself. Full derivation, the
drawdown-bound proof, and the complete numerical detail:
`.planning/sr-ac-combined-holdout-meta-analysis.md`.

**Paper Trading Policy Exception — `daily-tsmom-ensemble` (human-approved
2026-08-05).** CLAUDE.md's standing rule requires clearing the
Backtest/Walk-Forward Eligibility Bar, including rolling walk-forward
validation, before paper-trading eligibility. Neither is true here in the
usual sense, stated precisely rather than glossed over:

1. Neither of `daily-tsmom-ensemble`'s two independent pre-registered
   holdout confirmations (`sr-v`, BingX 2021-2024; `sr-ab`, Binance
   2017-2021) formally cleared all five gating criteria. `sr-v` missed
   PSR (0.9367<0.95), Sharpe-vs-detection-floor (0.882<0.9567), and trade
   count (26<53) — passing only drawdown and profit factor. `sr-ab`
   missed drawdown (20.135%>20%) and trade count (64<68) — passing PSR,
   Sharpe-vs-floor, and profit factor. **The two did not fail in the same
   way**: `sr-v` missed on statistical significance itself, not merely on
   practical gates.
2. `sr-ac`'s retrospective meta-analysis (no new data access) found that
   combining both independent, disjoint-sample significance tests via
   Stouffer's weighted Z-score method yields Z=2.914, Φ(Z)=0.9982 —
   genuinely strong, and stronger than either individual PSR. This is
   meta-analysis working as intended: a real small-to-moderate effect can
   fail to reach significance in either individually underpowered sample
   while still being real and detectable once the independent samples are
   properly combined. `sr-ac` also proved the combined drawdown
   mathematically must be ≥20.14% (already over the 20% ceiling) and
   found combined trades (90) fall short of the recomputed combined floor
   (100).
3. This strategy has never been walk-forward validated via rolling
   train/validate folds on any 1d data — deliberately: to protect the
   holdout, no 1d price data was accessed before either pre-registered
   confirmation (`sr-u`'s own design). The two single-window holdout
   confirmations, on two structurally different, disjoint multi-year
   eras, stand in place of rolling folds here. This is judged acceptable
   specifically **because** the strategy has zero fitted parameters
   (`free_parameter_count: 0`, the literature's own canonical
   Moskowitz-Ooi-Pedersen lookback set) — the failure mode rolling
   walk-forward exists to catch (a result that fits one window because a
   free parameter was tuned or selected against it) has no foothold in
   `daily-tsmom-ensemble`'s own design, and **neither holdout was ever
   searched over or iteratively adjusted** (single-access enforced by
   `research.holdout`, `total_candidates: 1` on both registrations). This
   is a narrower, more precisely bounded claim than "this project never
   engaged in any selection at all" — it plainly did: 117 research trials
   across 8 strategy families (see "Strategy Attempts So Far" above)
   preceded the decision to pursue TSMOM specifically, and that decision
   was itself informed by watching those other families fail. What zero
   fitted parameters actually rules out is narrower and still real:
   nothing *within* `daily-tsmom-ensemble` itself was tuned or selected
   against either holdout, or against any 1d data at all. A strategy
   with any fitted or selected internal parameter would not qualify for
   this specific reasoning at all. Separately, and disclosed here rather
   than folded into the "zero fitted parameters" claim: the project-level
   selection history above (117 trials, 8 families, TSMOM chosen only
   after the others failed) is a real form of selection distinct from
   parameter-fitting, and this exception rests on the latter being absent
   — not on a claim that the former never happened.

Given this evidence pattern — genuine combined statistical significance,
missing only on practical risk-control (drawdown) and
minimum-evidence-volume (trade count) gates, on a strategy with no
overfitting surface to protect against — the human operator explicitly
approved proceeding to paper trading as the next evidence-gathering step,
rather than requiring further backtest research (e.g. multi-symbol
expansion) first. Multi-symbol expansion was deprioritized for now on a
separate, practical judgment (human-stated, not re-derived here): a
survivorship-safe, comparably-liquid multi-symbol universe beyond BTC/ETH
is not readily available from this project's current data sources.
CLAUDE.md's own multi-symbol architecture goals (see "Long-term Design
Targets") are unaffected by this — it is a near-term sequencing choice,
not an architectural reversal.

**Scope, stated precisely so this is not read more broadly than
intended**: this exception applies ONLY to `daily-tsmom-ensemble` v1
(`research/strategies/daily_tsmom_ensemble.py`, unchanged) proceeding to
PAPER trading. It does NOT: waive the Paper Trading Pass Criteria (still
requires a minimum of 30 days (45 recommended), 50+ trades, zero critical crashes/duplicate
orders/position mismatches/risk-gateway bypasses, paper score 80+, kill
switch verified, before any live consideration — and given the backtest
evidence itself fell short of the trade-count floor, paper trading's own
50+-trade requirement is doing more evidentiary work than usual here, not
less); waive the separate Live Entry Criteria; relax any Risk Parameter
(canary tier limits apply in full); or loosen the Eligibility Bar, the
walk-forward-validation requirement, or the standing rule against further
parameter searching for any OTHER strategy. A future strategy citing this
exception without a comparably strong multi-window independent
replication AND zero fitted parameters is not following this precedent
correctly.

## Tooling Stack

| Layer | Choice | Status |
|---|---|---|
| CLI foundation | ripgrep, gh, uv | as needed |
| Guardrails (hooks) | `dwarvesf/claude-guardrails` (Lite, global `~/.claude/settings.json`) | active now — brought forward from "when `.env` appears" because the repo is public |
| Guardrails (project-specific) | hook blocking CI-workflow references to VST order-execution credentials/mode and Java edits that would source `BINGX_VST_BASE_URL` from an environment variable | active now (added Paper Trading Bridge Task H, `.claude/settings.json`'s second `PreToolUse` hook — see `.planning/paper-trading-h-vst-integration.md` for exactly what it does and does not cover) |
| Secret scanning (local) | `gitleaks` via `.githooks/pre-commit` (`git config core.hooksPath .githooks`) | active now — covers generic secrets (private keys, etc.) that GitHub push protection's free tier doesn't (see "repo is public" note above) |
| Methodology | GSD (`.planning/` artifacts) + TDD rule above | active now |
| MCP | Context7, GitHub MCP | add when useful, not urgent |
| CI/CD | `claude-code-action` | not wired to repo events yet — public-repo triggers are a separate, deliberately deferred decision (prompt-injection surface); PRs currently opened via authenticated `gh` sessions |
| Merge governance | `.github/CODEOWNERS` + branch protection on `main` (see Branch and Merge) | active now |
| Code review | CodeRabbit Pro (see below) | active now — GitHub App installed, verified posting reviews, its `CodeRabbit` commit status is a required check on `main` |
| Multi-agent orchestration | Anthropic Agent Teams (official) | standby, off by default |

## Future Tooling Watchlist

Candidates identified but deliberately not adopted yet — written down so
they don't depend on conversational memory to resurface at the right
time (see "Why this is more than a bare CLAUDE.md").

| Candidate | Revisit when | Why not now |
|---|---|---|
| BingX-specific MCP/skills (e.g. BingX-API org's own skill library) | Start of Priority #7 (`ExchangeAdapter`) | No exchange-integration code exists yet to benefit from it; reference/coding-assistance use only — never order-execution-capable, per Non-negotiable Rules |
| Monitoring/alerting (health checks, kill-switch alerts) | Priority #8 (24/7 unattended operation) | Nothing runs unattended yet to monitor |
| RAG / conversational log search | After Priority #8 generates real operational history | No logs/reports exist yet to search |
| Secrets manager beyond `.env` | Reassess at Priority #7 if VPS + `.env` + guardrails prove insufficient | Minimal single-VPS deployment likely doesn't need it |

## Code Review Gate

CodeRabbit Pro reviews every PR — see `.coderabbit.yaml` for the actual
rules (no live-trading enablement, no secrets, no risk/leverage relaxation,
Python cannot place live orders, Risk Gateway cannot be bypassed).

CodeRabbit Pro's Autofix is fine to accept for lint, formatting, docs, and
low-risk Python research code. For Java OMS / Risk Gateway / Execution,
anything touching credentials, or anything live-trading-related: review and
apply the fix manually. Do not accept an automated fix on high-risk code
without reading it.

### Rate limits

Not a fixed "N reviews/hour" bucket — confirmed 2026-07-25 via
`@coderabbitai rate limit` (this query itself doesn't consume a review):
CodeRabbit uses an adaptive, usage-percentile-based limit ("your recent
PR review activity is in the 95th percentile or higher... adaptive
limits apply"), triggered by requesting re-reviews too many times in a
short window on one PR — exactly what happened after 6 re-review
requests on a single PR in one session. The response includes an exact
ETA ("next review available in N minutes"). When blocked: comment
`@coderabbitai rate limit` to get that ETA instead of guessing or
polling blindly, wait it out, then retry once. Also reduces the
underlying cause: batch fixes for multiple review findings into one
push before requesting re-review, rather than re-requesting after each
small fix.

## Branch and Merge

- Never commit or push directly to `main`.
- Changes go through a branch and a PR.
- Self-review and verify before opening a PR.
- CodeRabbit review must complete (not pending) before merge.

### Auto-merge Policy

`.github/CODEOWNERS` is the intended source of truth for which paths
require a human decision before merging. It is backed by branch protection
on `main` (require PR, require review from Code Owners, 0 required
approvals otherwise, `enforce_admins` on). This narrows the human's role
in day-to-day development to three things: overall direction, approving
high-risk changes, and deciding on anything that costs money (new paid
tools/services, subscription changes).

- **Not CODEOWNERS-matched** (Python research/backtest code, docs, tests,
  most of the repo by volume): CI + CodeRabbit passing is sufficient —
  merges without any human review, via GitHub's native auto-merge. Verified
  working end-to-end, including with the `CodeRabbit` commit status as a
  required branch-protection check (`README.md` / cleanup PRs merged with
  zero manual action once CodeRabbit's review posted success).
- **CODEOWNERS-matched** (`java/`, `schemas/`, `configs/`, `.github/`,
  `CLAUDE.md`, `.coderabbit.yaml`): **verified NOT to be a hard server-side
  gate right now.** GitHub's "Require review from Code Owners" does not
  block merging when the PR author is also the sole code owner — tested
  empirically with both `enforce_admins: false` and `true`; both merged
  instantly with no review, no queued/waiting state. Self-approval is
  blocked, but GitHub simply doesn't raise the requirement at all rather
  than blocking, since there is no one else who could satisfy it. This is
  a solo-author-repo limitation of GitHub CODEOWNERS, not a config mistake.
- **Until PR authorship moves to a bot/app identity distinct from
  @ckrhehfl** (out of scope for now — see Tooling Stack), the CODEOWNERS
  boundary is enforced procedurally, not technically. Branch protection
  stays on regardless — it still stops a future bot/app identity or a
  second collaborator from merging those paths unreviewed, which is real
  protection, just not against the current sole operator.
- As of 2026-07-24, @ckrhehfl has delegated day-to-day merge judgment for
  most CODEOWNERS-matched PRs too: once required checks (CI + CodeRabbit)
  are genuinely green — not pending, not rate-limited — an LLM agent
  operating this repo may merge without asking first, for CODEOWNERS
  paths whose content is *not* itself high-risk (e.g. `.github/`
  workflow/tooling changes like `gitleaks.yml`, `schemas/` additions,
  non-risk `configs/`, most CLAUDE.md documentation edits).
  This delegation explicitly does **not** extend to "approving high-risk
  changes" (still one of the three things reserved for the human, per
  above) — still stop and ask before merging when the PR touches: Java
  OMS/Risk Gateway/Execution logic, live-trading/leverage/risk-limit/kill-
  switch behavior, credentials or `.env`/secrets handling, or paper/live
  promotion of a model/risk/order-logic change — regardless of checks
  passing, matching the Non-negotiable Rules above (e.g. "never weaken
  risk limits... without explicit human approval"). Also still stop and
  ask when (a) CodeRabbit is rate-limited/unusable rather than passing
  (see "Rate limits" under Code Review Gate below for the check-before-
  retry procedure — don't just retry blindly), (b) the change has cost/
  subscription implications, or (c) the task requires @ckrhehfl to do
  something only they can do (a GitHub UI setting, an account
  credential, entering a password).

## Implementation Priority

1. Shared schemas (exchange/asset-class-agnostic where practical)
2. Java OMS state machine skeleton
3. Java Risk Gateway skeleton
4. Python deterministic backtest skeleton
5. Schema compatibility tests
6. Paper broker
7. `ExchangeAdapter` skeleton (BingX as first implementation) — this is
   where a real order-placement-capable path first exists in the
   codebase, so from this priority on: `ExchangeAdapter` may only ever be
   invoked from OMS-mediated flows, never called directly with a
   hand-built order, including for testing or demos. The full
   provenance check (below, #8) can't be *written* until #8's wiring
   exists, but the discipline of never opening a direct-call shortcut
   starts here.
8. Paper trading loop + 24/7 runtime supervision (restart recovery, health
   checks) — promoted priority, needed for the unattended-operation target.
   Must also verify at this stage that the only real code path from
   `OrderIntent` to `Order` goes through `RiskGateway.evaluate()`:
   `Order.fromApprovedDecision()` today only checks that the
   `RiskDecision` handed to it says APPROVED/MODIFIED, not that it was
   actually produced by a real `evaluate()` call — nothing wires these
   together yet, so this can't be tested until this priority builds that
   wiring. **Half closed 2026-07-25**: `engine.runtime.OrderPipeline` is
   now the one real `OrderIntent → RiskGateway.evaluate() → Order` path
   (`java/runtime`) — but `OrderStore.createOrder(intent, decision)`
   itself is still public, so code bypassing `OrderPipeline` entirely
   with a hand-built `RiskDecision` remains *possible*, just not done by
   anything in the codebase today. Closing that (visibility restriction
   doesn't trivially work — `OrderPipeline` already needs cross-package
   access to `OrderStore`; likely needs either moving `OrderPipeline`
   into `engine.oms` or a capability/token scheme touching the shared
   `RiskDecision` schema) is deliberately deferred — see #10.
9. Auto-retraining pipeline (scheduled retrain, validation, promotion gate)
   — promoted priority, needed for the auto-learning target; promotion to
   paper/live still requires human approval
10. Canary live preparation. Before any live wiring: close the
    `OrderStore.createOrder` bypass noted in #8 — a genuine hardening gap,
    not urgent while no live order path exists, but non-negotiable before
    one does (CLAUDE.md's own "never bypass the Java Risk Gateway" rule is
    about live orders specifically).
    - **Root cause**: `Order.fromApprovedDecision(OrderIntent, RiskDecision)`
      is what actually constructs an `Order`; `OrderStore.createOrder` just
      wraps it. Closing the gap means changing `Order.fromApprovedDecision`
      itself, not just `OrderStore`.
    - **Design**: introduce `engine.risk.VerifiedRiskDecision` (name
      negotiable), a final class wrapping a `RiskDecision`, with a
      package-private constructor — only code inside `engine.risk` (i.e.
      `RiskGateway`) can construct one. `RiskGateway.evaluate(...)`'s
      return type changes from `RiskDecision` to `VerifiedRiskDecision`
      (still wrapping/exposing the same `RiskDecision` via an accessor for
      read access — fields/validation logic on `RiskDecision` itself, the
      cross-language wire schema, do not change). `Order.fromApprovedDecision`
      and `OrderStore.createOrder` are changed to require
      `VerifiedRiskDecision` instead of raw `RiskDecision`.
    - **Why this design, not the alternatives**: `RiskGateway` must stay
      `final` (already litigated and settled — Priority #8 Task A's
      review; a subclassable `RiskGateway` reopens the exact bypass this
      closes, via an always-approving override). No JPMS exists in this
      repo (confirmed — zero `module-info.java` files), so there's no
      `exports ... to` mechanism finer than Gradle's `project(":...")`
      dependency graph. Package-private visibility alone doesn't work
      across the current `engine.oms`/`engine.runtime` module split, and
      moving `OrderPipeline` into `engine.oms` wouldn't avoid the
      `:oms`→`:risk` dependency anyway (it would still need `RiskGateway`
      from `:risk`) — so some form of `:oms` depending on `:risk` is
      unavoidable for a structural (not procedural) fix. `RiskDecision`
      itself must not change shape — it's a genuine, tested cross-language
      wire type (`schemas/fixtures/risk_decision_*.json`, mirrored in
      `python/schemas/risk_decision.py`, enforced by `SchemaCompatTest`) —
      so the capability lives in a separate, Java-only wrapper type, never
      serialized, not the `RiskDecision` record itself.
    - **Confirmed impact, don't rediscover this**: `:oms`'s
      `build.gradle.kts` gains `implementation(project(":risk"))` — the
      module graph's only sibling relationship becomes a dependency,
      deliberately, for the first time since Priority #2/#3. Every test
      helper that currently does
      `Order.fromApprovedDecision(intent, new RiskDecision(...))` with a
      hand-built `RiskDecision` breaks and needs rework — confirmed
      present in (at least) `PaperBrokerTest.java` (`:execution`),
      `BingXAdapterTest.java` (`:exchange`), `OrderPipelineTest.java`
      (`:runtime`). Each needs either (a) switching to a real
      `RiskGateway.evaluate()` call to obtain a legitimate
      `VerifiedRiskDecision` for its fixtures, or (b) a deliberate,
      clearly-scoped test-only construction path (e.g. a `testFixtures`
      Gradle source set exposing a factory) — decide which per-module at
      execution time, don't assume one answer covers all three.
    - **When**: bundle with Priority #10's live-wiring work specifically —
      don't do it as an isolated refactor separate from that, since #10
      touches this exact order-construction path anyway (wiring
      `BingXAdapter` into whatever supersedes/extends `TradingLoop` for
      live mode). Needs its own `Discuss` pass at that time per GSD (this
      is R3-risk architecture), not a rushed fix under review pressure.
    - **2026-07-25**: `OrderStore.createOrder` was investigated for a
      scoped, interim hardening in the meantime, ahead of this priority —
      a `java.lang.StackWalker`-based caller check was built as a real
      prototype and tested, not just reasoned about on paper. Rejected:
      it broke 7 of `OrderStoreTest`'s own 8 existing tests, because a
      caller-identity check can't structurally distinguish this
      codebase's established, deliberate pattern of unit-testing OMS
      primitives via direct calls from the exact bypass the check is
      meant to reject. Every other interim mechanism considered failed
      for the same structural reason or amounted to false-confidence
      theater. No interim hardening shipped; `OrderStore.createOrder`
      remains byte-for-byte unchanged — this is not the complete fix
      above and was never intended to be. Full investigation, including
      every rejected alternative and why: `.planning/09-order-store-hardening.md`.

None of the above names "build a strategy" explicitly, but running any
of #6–#8's infrastructure with a real trading strategy — not just testing
it with dummy/mock signals — can't happen without one. See Strategy
Research Methodology for the non-negotiable principles that gate that,
without blocking the infrastructure work itself.

## Why this is more than a bare CLAUDE.md, but still not the old system

Guardrails, GSD, and CodeRabbit are here because concrete requirements
justify each one *now*: guardrails because the repo is public and real
secrets will exist soon, GSD because this project is genuinely
multi-month and will span multiple
exchanges (context rot is a real risk here, not a hypothetical one),
CodeRabbit because it's already paid for and reviewing every PR is cheap.
Still deliberately excluded: a second full methodology framework running
alongside GSD, a multi-agent reviewer fleet, a multi-document
cross-referenced spec system, and Agent Teams until GSD's built-in
orchestration proves insufficient. A prior attempt at this project
accumulated 15 cross-referenced docs, a 5-level risk-classification system,
a 16-step PR lifecycle, and 5 reviewer subagents before a single
continuously-running paper trading loop existed — that process outpaced
the working system and became the bottleneck itself. Add anything beyond
this file only when a concrete, recurring problem justifies it.
