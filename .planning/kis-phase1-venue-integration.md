# KIS / KOSPI200 venue integration, Phase 1 — retrospective design record

**Retrospective consolidation, not an original planning document.** Read
`.planning/README.md`'s "Where does a design belong: CLAUDE.md, or here?"
first — this file exists because that rule's second half was never
carried out for this effort.

The rule says a detailed design for not-yet-started work lives in
CLAUDE.md in full, and that **once the work actually starts** a
`.planning/` file should be created for it, at which point CLAUDE.md's
own entry can be trimmed to a summary plus a pointer. CLAUDE.md's own
KIS text even names the intended files — "own `.planning/kis-a/-b/-c/-d-
*.md` doc per task, own PR each". Those files were never created. The
work went ahead and merged anyway (PRs #103, #104, #105, #106, plus the
later shared-ledger effort recorded separately in
`.planning/kis-ledger-a..d-*.md`), so from then until this file was
written, CLAUDE.md was the **only** record of this design anywhere in the
repo.

This file closes that gap. It is the same category of artifact as
`.planning/00-*.md` through `06-*.md` — a retrospective record written
after the fact, explicitly labelled as such rather than presented as a
contemporaneous planning document.

**Fidelity**: everything below the horizontal rule is the KIS/KOSPI200
section of CLAUDE.md reproduced **verbatim**, extracted mechanically
(`sed -n '108,648p' CLAUDE.md`) rather than retyped or summarized, so no
wording, hedge, correction, or disclosed-gap can have been lost or
softened in transit. It is preserved in its accumulated form: the
original pre-implementation design, plus every correction and disclosure
layered onto it across roughly six real CodeRabbit review rounds and the
real 2026-08-21/24 verification against KIS's live paper API. Reading it
top to bottom therefore reads as a design with amendments, not a clean
final spec — that is the real history and is deliberately not tidied up
here.

**What is NOT here**, because it lives elsewhere and was never in this
section:

- Real KIS API behaviour discovered by calling the live paper API —
  response casing, `inquire-deposit`'s missing paper TR id, pagination,
  the `tr_cont` continuation bug, real observed latency, token rate
  limits. That is CLAUDE.md's "Exchange API Facts — KIS" section, which
  stays in CLAUDE.md as durable operational reference.
- The later shared-account-ledger effort (`AccountStateProvider`,
  `AccountLedgerStore`, `SharedKisAccountLedger`, `AccountLedgerReconciler`)
  — `.planning/kis-ledger-a-*.md` through `-d-*.md`.
- Anything about promoting a real KOSPI200 strategy, which was explicitly
  out of scope for this phase and never happened.

**Status at the time of extraction (2026-08-26)**: Phase 1 is built and
merged. A `kis-paper` loop for `A01609` (KOSPI200 index futures) reached
a real, successful end-to-end run against KIS's live paper API. Its
`KillSwitch` starts tripped by deliberate design and no order has ever
been submitted through it. The `INDEX_FUTURES` contract-multiplier
conversion exists (PR #105); `STOCK_FUTURES` deliberately fails closed.
The two gaps named below — ambiguous-submission recovery, and
`GUARDED_MARKET` having no wire-level price guard — remain open.

**Read the extract as a historical document, not a status report.** It
opens with the words "planned, not yet built" and, near its end, still
describes credential provisioning as blocked on a KIS registration that
has since completed. Both were true when written and are false now; they
are preserved because this file's whole purpose is faithful preservation,
and correcting them in place would destroy the byte-identical property
that makes the preservation trustworthy. The paragraph immediately above
is the authoritative current status. The extract also carries one
orphaned sentence fragment (`by it.` on its own line, an editing artifact
already present in CLAUDE.md before this extraction); it is preserved for
the same reason and disappears from CLAUDE.md when that block is replaced
in the next phase of this cleanup.

## A third open gap, found *by* this extraction (2026-08-26)

Not previously recorded anywhere, and not created by this PR — surfaced
by real CodeRabbit review of the extraction itself, then verified
directly against the code. **CLAUDE.md specified a fail-closed validation
that was never built, and nothing reconciled the two when the
implementation landed.**

The extract below states, as a requirement on the contract-multiplier
conversion:

> the margin-rate input has a defined source and a staleness check; …
> **missing or stale price/margin data is a rejection (fail closed),
> never a silent fallback**

What the code actually does, confirmed by direct inspection:

- `engine.runtime.PriceFeed#latestPrice` returns a bare `BigDecimal` with
  **no timestamp** — an interface-level property, so this affects
  `BingXPriceFeed` equally, not only `KisPriceFeed`. `KisPriceFeed`'s own
  Javadoc already discloses this and argues the separation of concerns is
  deliberate: knowing *when* it is safe to act on a price is
  `engine.runtime.TradingCalendar`'s job, which gates whether
  `TradingLoop.tick()` runs at all outside market hours. That argument is
  reasonable for market-hours gating; it does not provide a staleness
  check *within* an open session, and it is not the fail-closed rejection
  the requirement above describes.
- `engine.risk.FixedMultiplierNotionalCalculator` performs
  `quantity × price × multiplier` with rounding, and rejects a
  non-positive-integer contract count. It has **no staleness check and no
  margin-rate input at all** — the margin-rate half of the requirement
  describes something that was never built rather than something built
  incorrectly.
- `engine.risk.RiskGateway` uses `Instant.now()` only to stamp its own
  decisions; it performs no freshness validation on its inputs.

**Severity, stated precisely rather than either inflated or waved away**:
latent, not live. `PaperTradingApp.forKisPaper()` trips `KillSwitch`
unconditionally at construction, so no KIS order can reach `submitOrder`
today regardless. The gap matters at exactly the moment a human considers
resetting that switch — which is also when the two other open gaps are
supposed to be reviewed. It belongs on that same checklist.

**Deliberately not fixed here.** Adding freshness/margin validation
touches `RiskGateway` and the `PriceFeed` interface — R3-risk Java
trading-plane code, which CLAUDE.md's Development Methodology requires a
`Discuss` pass for and explicitly warns against changing under review
pressure. Bolting it onto a documentation-extraction PR that adds no code
would be exactly that. Recorded here so the next `Discuss` on the KIS
loop starts from an accurate picture instead of trusting a requirement
paragraph that was never implemented.

---

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
by it. **Update 2026-08-21/24: no longer blocked** — the user completed
registration (including a genuinely separate 국내 선물옵션 모의거래 이수
certification the account also needed, confirmed real via a distinct
provisioned account number), and real verification actually happened.
Several real bugs this fake-server-only build could not itself have
caught were found and fixed as a result — see "Exchange API Facts — KIS"
below for the full account, kept separate from this section per this
file's own established pattern for BingX/Binance.
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

**Update 2026-08-24 (PR #105): the contract-multiplier conversion named
above is now built.** A new `engine.risk.NotionalCalculator` interface,
injected into `RiskGateway` via a second constructor (the original
one-argument constructor stays a zero-behavior-change delegation to the
new `SimpleNotionalCalculator`, used by every BTC-USDT loop unchanged),
supplies the real `quantity × price × multiplier` conversion for KIS —
`FixedMultiplierNotionalCalculator`, generic and KOSPI/KIS-name-free,
enforces a positive integer contract count, and rounds notional up
(never down, so rounding can't understate exposure and let an
over-limit order through) with the inverse clamp rounding quantity down
to a whole contract.

**Why this doesn't violate "a new venue means a new `ExchangeAdapter`,
never a change to `RiskGateway`/OMS/Execution" — addressed explicitly
here rather than left implicit, on real CodeRabbit review**: `RiskGateway
.java` genuinely did change (a new constructor, a new dependency).
That rule's real intent, evidenced by every other extensibility seam
already in this codebase (`PriceFeed`, `TradingCalendar`,
`AccountStateProvider`, `OrderExecutor` itself), is "no per-venue branch
or hardcoded venue fact inside OMS/Risk/Execution's own logic" — not
"the file's text may never be touched again." Each of those seams
required exactly one, one-time interface-extraction change to a
previously-concrete class, after which every further venue implements
the interface with zero additional change to the class that depends on
it. `NotionalCalculator` follows the identical shape: `RiskGateway`
itself contains no KOSPI/KIS-specific name, string, or number anywhere
— the real `₩250,000` value lives in `PaperTradingApp`
(`KIS_KOSPI200_INDEX_FUTURES_MULTIPLIER`, in `:runtime`), the same
module/layer BingX-specific constants like `BINGX_VST_BASE_URL` already
live in. A hypothetical future venue needing its own notional shape
implements `NotionalCalculator` the same way `KisAdapter` implements
`ExchangeAdapter` — zero further change to `RiskGateway.java` itself,
which is the actual property this rule protects. `PaperTradingApp
.forKisPaper()` resolves it via `resolveKisNotionalCalculator`:
`INDEX_FUTURES` gets the real ₩250,000 multiplier; `STOCK_FUTURES` fails
closed (refuses to start) rather than guess at its own real, different,
still-unconfirmed per-stock multiplier (the gap named in "Still fully
unbuilt" below remains open specifically for that product). **This does
not, by itself, change whether `kis-paper` can submit a real order**:
`forKisPaper()`'s unconditional `KillSwitch.trip()` (immediately below)
was deliberately left in place rather than removed — a real decision
made with the human operator, not a side effect of this task — because
the conversion is verified only by unit tests so far, not against KIS's
live API, and the two gaps named two paragraphs below (ambiguous-
submission recovery, no wire-level `GUARDED_MARKET` guard) remain open
regardless of this fix. Resetting the kill switch stays a separate,
explicit human choice.

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
against a `kis-paper` process pointed at a real strategy signal. **Now
built for `INDEX_FUTURES` (see the 2026-08-24/PR #105 update above) —
but stated precisely, its being built is necessary, not sufficient, for
that reset**: real-API verification and the two still-open gaps named in
that same update remain, independent of this specific conversion.

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
(unlike the third above, this one is not being fixed) — precision
corrected on a second review pass of the same finding**:
`FileSignalSource.nextSignal()` marks a signal delivered — updates its
own in-memory pointer, persists the durable marker if configured — the
moment it reads a genuinely new signal, before the caller (`TradingLoop
.tick()`) has done anything with it. If price lookup, risk evaluation,
order construction, or exchange submission then fails anywhere
downstream, the signal is already marked delivered within that process.
**Severity depends on which constructor built the instance**: the
marker-free one-arg constructor keeps this in-memory only, so a restart
forgets it and the same signal is read again as new — lost only until
the next restart, not permanently. The two-arg constructor with a
durable `deliveredMarkerPath` — what both `forBingXVst` and
`forKisPaper` actually use — persists across a restart too, so the
signal really is permanently lost there; neither a restart nor a
same-process retry recovers it. A real fix means giving `SignalSource`
its own acknowledgment contract (mark-delivered only after
`OrderPipeline` successfully hands off, not merely on being read) — a
genuine interface-level change spanning `SignalSource`,
`FileSignalSource`, `DummySignalSource`, and `TradingLoop.tick()`'s own
control flow itself, not a local fix. **This is not new to `kis-paper`
or this PR** — `FileSignalSource` has carried this characteristic since
Paper Trading Bridge Task H, and it applies identically, right now, to
the real, currently-running `bingx-vst` production loop (which uses the
durable-marker constructor, so it has the permanent-loss version of this
gap, not the milder one). Deliberately not attempted under review
pressure here: a change to `FileSignalSource`'s own delivery semantics —
a component already in continuous, real (if paper-account) operation —
deserves its own `Discuss` pass and careful testing against the live
loop, not a rushed fix bundled into a KIS wiring task. Full disclosure
in `FileSignalSource`'s own Javadoc.

**A fifth Task 4 gap, found after Task 4 merged (real operational
discovery, not a CodeRabbit finding) — fixed**: `KIS_SUBMISSION_MARKERS_PATH`
was a single hardcoded constant with no `symbol` in it at all. That was
enough to keep `bingx-vst` and `kis-paper` from colliding with each other
(they trade different symbols, so their marker files already differed),
but did nothing to stop two `kis-paper` processes trading two different
KOSPI200 symbols from colliding with *each other* — a real scenario once
an operator actually runs more than one KIS symbol at a time (this
project's established one-process-per-symbol pattern). Fixed by deriving
the path from `symbol` (`var/live/{symbol}-kis_submission_markers.json`,
`PaperTradingApp.resolveKisSubmissionMarkersPath`), same reasoning as
`resolveSignalPath`'s own `symbol`-derived default — no environment-
variable override, matching this path's established no-config-surface
precedent.

**Scope extension beyond KOSPI200 index futures: individual KRX stock
futures (실제 개별주식선물), confirmed real and added — 계약승수/quote
market-division handling still only partially verified.** Researched
directly against KIS's own official `koreainvestment/open-trading-api`
GitHub source (its real, publicly-downloadable symbol master files,
`stocks_info/domestic_index_future_code.py` and
`domestic_stock_future_code.py` — both fetched and parsed for real,
2026-08-14) rather than assumed: KRX genuinely lists futures contracts on
283 individual large-cap stocks (Samsung Electronics/삼성전자 front-month
`A11609`, SK Hynix/SK하이닉스 front-month `A50609`, confirmed as real,
current, live short codes as of that date — KOSPI200 index futures itself
uses the same short-code shape, front-month `A01609`), not just the
KOSPI200 index. KIS's own official `order`/`order_rvsecncl`/
`inquire_ccnl`/`inquire_balance`/`inquire_deposit` example functions are
**generic across every domestic futures/options product** — same
endpoint, same `tr_id`, distinguished only by the `SHTN_PDNO`/`PDNO`
symbol value, confirmed directly from KIS's own example docstrings (`선물
6자리 (예: 101W09)` used identically regardless of underlying) — so
`KisAdapter`'s order/cancel/query/balance/positions methods needed **no
code change at all** to support an individual-stock-futures symbol; they
were already venue-generic, not KOSPI200-specific, despite this class's
own Javadoc historically saying "KOSPI200 index futures specifically."

**What did need a real code change: `KisPriceFeed`'s quote lookup.** KIS's
own official source documents a genuinely different
`FID_COND_MRKT_DIV_CODE` value per instrument type on the sibling
`inquire-asking-price` endpoint's own parameter comment — `F` for index
futures, `JF` for individual-stock futures (confirmed, not inferred) —
and `KisPriceFeed` used to hardcode `F` unconditionally. Fixed with a new
`KisPriceFeed.MarketDivision` enum (`INDEX_FUTURES`/`STOCK_FUTURES`), now
a **required** constructor argument (no default at that layer — an
already-established project principle: never silently assume a
possibly-wrong default for something this consequential). `PaperTradingApp`
exposes this as a new optional env var, `KIS_MARKET_DIVISION` (default
`INDEX_FUTURES`, matching this phase's original, only-ever-tested scope;
must be typed exactly as the enum constant name or the process refuses to
start, same "fail loud on an unrecognized value" discipline
`resolveExecutionMode` already established) — and `scripts/kis-paper.sh`
exposes it as a `--stock-futures` flag applying to every symbol in one
`start` invocation (no per-symbol mixing within a single call; run the
script twice for a mixed group). **Real, disclosed, still-open
uncertainty, not silently assumed away**: KIS's own docs establish `F`
vs `JF` for `inquire-asking-price` specifically, but `KisPriceFeed`
actually calls the sibling `inquire-price` endpoint, whose own official
docstring only ever mentions `F`/`O` (index futures/options), never `JF`
— this could mean the omission is real (that specific endpoint doesn't
need the distinction) or merely an incomplete doc comment in KIS's own
examples repo. Not guessable from documentation alone; needs a real call
against a real individual-stock-futures symbol (e.g. `A11609`) to settle,
which is deliberately not asserted as already-confirmed anywhere in code
or here.

**Updated 2026-08-24 (PR #105) — index-futures side now built, stock-
futures side still fully unbuilt.** The RiskGateway KOSPI200 contract-
multiplier conversion disclosed earlier in this section now exists for
`INDEX_FUTURES` (the real ₩250,000/index point multiplier, via
`FixedMultiplierNotionalCalculator`), but still does not — and, per that
same update, deliberately refuses to guess at — a stock-futures
multiplier (a real, different, per-stock contract multiplier — the
symbol master file's own `한글종목명` field shows a `(  10)` suffix per
stock-futures row, plausibly a 10-shares-per-contract multiplier, not
yet independently confirmed against KIS's own contract-specification
docs). The two products are protected two different ways, not the same
mitigation applied twice (corrected on real CodeRabbit review, which
caught this section's own first version blurring the distinction):
`resolveKisNotionalCalculator` fails closed for `STOCK_FUTURES` by
throwing before `forKisPaper()` ever constructs a `KillSwitch` at all —
the process refuses to start, full stop, not "starts with a switch
already tripped." `INDEX_FUTURES` does construct the app (its own real
conversion exists now) and is protected by `forKisPaper()`'s own
unconditional `KillSwitch` trip instead — the trip was kept in place
even for `INDEX_FUTURES` once its own conversion was built (see
the update above for why).

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
