# Architecture — what the system looks like now

**This file is replaced, not appended to.** It answers one question: *what
is the structure today?* When the structure changes, this file changes with
it and the old text goes away — the record of what it used to be, and why it
changed, lives in `.planning/`.

That is the whole reason it exists. This project keeps three kinds of
document and they had been collapsed into two:

| question | kind | lives in |
|---|---|---|
| what may never be violated? | **invariant**, needed in every session | `CLAUDE.md` |
| what does the structure look like **now**? | **living**, replaced | **this file** |
| what was decided when, and why? | **append-only** log | `.planning/` (121 docs, indexed) |

`.planning/` is organised by work-arc and only ever grows, so it structurally
cannot answer "now". Before this file existed, nothing could: PR #105 had to
argue *inside `CLAUDE.md`* that adding a `NotionalCalculator` seam did not
violate `CLAUDE.md`'s own rule, because there was nowhere to keep the seam
list as a current fact.

**What is deliberately NOT here**: the safety properties, the three open KIS
gaps, and the Non-negotiable Rules. Those are invariants and they stay in
`CLAUDE.md`, which is the file an AI session is guaranteed to have read.
Where this document names one, it points rather than repeats — a number or a
rule written in two places is a contradiction waiting to happen, and this
project has already shipped one (a window called "unspent" in `CLAUDE.md`
while `runs/spent_windows.json` and four other paragraphs said otherwise).

**Five paragraphs of this document's own first draft broke that rule**, each
caught in review. The account is in `.planning/audit-2026-09-consolidation.md`
— not here, because how this file came to say what it says is append-only
record, and this file is replaced when the structure changes.

---

## 1. Two planes

```text
Python Research Plane
- data research, deterministic backtesting, strategy experiments
- feature engineering, ML training/evaluation, scheduled retraining (later)
- report generation, deployment candidate generation
- holds no venue order-placement code; it writes a signal file the trading
  plane reads

Java Trading Plane
- OMS, Risk Gateway, Execution Service
- ExchangeAdapter interface (BingX is the first implementation)
- position reconciliation, kill switch, paper/live runtime
- `engine.runtime.OrderPipeline` is the one path from an `OrderIntent` to an
  `Order`, and it builds one only from a `RiskGateway.evaluate()` decision
```

**Those two lines describe the code; the corresponding permissions are
`CLAUDE.md`'s.** That Python may never place live orders directly and that no
order may bypass the Risk Gateway are Non-negotiable Rules, stated there.

Java's scope is intentionally narrow: OMS / Risk / Execution / Exchange
Adapter / Reconciliation / Kill Switch only. Java 21 + Gradle + JUnit +
Jackson + SLF4J. **No Spring, Kafka, K8s or Aeron.** Strategy research,
backtesting, ML and reporting stay in Python.

**Reassess the split if** solo-dev burden becomes excessive, a Python
prototype proves sufficient on its own, or Python/Java schema drift keeps
recurring.

### Gradle modules

`:schemas` · `:oms` · `:risk` · `:execution` · `:exchange` · `:runtime`

The module graph has **no sibling dependencies** today. Priority #10's
`VerifiedRiskDecision` work will deliberately introduce the first one
(`:oms` → `:risk`); that design is written out in full in `CLAUDE.md`'s
Implementation Priority #10, because the work has not started and
`.planning/` only takes documents for work that has.

---

## 2. The seams — the list this file exists to keep

Each row below is a place where a venue-specific fact was lifted out of
OMS / Risk / Execution and behind an interface. **That this is how a venue
gets added is a rule, and the rule is `CLAUDE.md`'s**; the list of where it
has actually been done is this file's job, and is the thing that had nowhere
to live. Each seam cost exactly one one-time extraction; after it, every
further venue implements the interface with **zero** additional change to the
depending class.

| seam | module | production implementations | extracted because |
|---|---|---|---|
| `ExchangeAdapter` | `:exchange` | `BingXAdapter`, `KisAdapter` | the original design premise — BingX is the first adapter, not a baked-in assumption |
| `OrderExecutor` | `:execution` | `PaperBroker`, `ExchangeOrderExecutor` | see §3 — this one carries a hard count limit |
| `SubmissionListener` | `:execution` | `MarkerRecordingSubmissionListener` | durable `SUBMISSION_UNKNOWN` marking, composed **into** `ExchangeOrderExecutor` rather than wrapped around it |
| `PriceFeed` | `:runtime` | `BingXPriceFeed`, `KisPriceFeed` | `TradingLoop` was hard-typed to the concrete `BingXPriceFeed` |
| `TradingCalendar` | `:runtime` | `AlwaysOpenTradingCalendar`, `KrxMarketCalendar` | crypto trades 24/7 and KRX does not |
| `AccountStateProvider` | `:runtime` | `SyntheticAccountStateProvider`, `SharedKisAccountLedger` | a shared brokerage account is not one strategy's balance |
| `NotionalCalculator` | `:risk` | `SimpleNotionalCalculator`, `FixedMultiplierNotionalCalculator`, `SteppedNotionalCalculator` | a KRX index-futures contract's notional is a fixed multiplier per index point, not price × quantity |

Test doubles (`FakePriceFeed`, `FakeExchangeAdapter`,
`FakeAccountStateProvider`, `RecordingSubmissionListener`, …) live in the
`src/test` source sets and are deliberately not counted here.

**`RiskGateway` gaining the `NotionalCalculator` seam (PR #105) did not
violate the no-refactoring rule, and the seam list is why that is now
checkable rather than arguable.** `RiskGateway.java` genuinely changed — a
second constructor, a new dependency. The rule's real content, evidenced by
every row above, is *"no per-venue branch or hardcoded venue fact inside
OMS/Risk/Execution's own logic"*, not *"the file's text may never be touched
again"*. `RiskGateway` contains no KOSPI or KIS name, string or number
anywhere; the real multiplier lives in `PaperTradingApp` (`:runtime`), the
same layer `BINGX_VST_BASE_URL` already occupies — and its **value** is
stated once, in `CLAUDE.md`'s safety properties, not here. The original one-argument
constructor remains a zero-behaviour-change delegation to
`SimpleNotionalCalculator`, used unchanged by every BTC-USDT loop.

---

## 3. The `OrderExecutor` / `ExchangeAdapter` layering rule

**The binding rule is `CLAUDE.md`'s layering invariant and is not restated
here** — how many implementations may exist, and that no decorator or wrapper
is an exception to that count, are normative and belong to the file every AI
session has read. What follows is only what the code looks like today.

`engine.runtime.TradingLoop` depends on `engine.execution.OrderExecutor`
(`submit` + `pollFills` + `pendingOrders` + `cancel`) and on no concrete
implementation. Two exist, both in `:execution`:

- **`PaperBroker`** — the internal simulator. Resolves fills synchronously
  from an injected price.
- **`ExchangeOrderExecutor`** — venue-agnostic. Wraps the `ExchangeAdapter`
  **interface**, never a concrete adapter, and polls `queryOrder` for real,
  asynchronous fills.

Durable submission-outcome marking is why the difference between composing a
concern **in** and layering it **on** is visible in the code rather than only
in the rule: an earlier version of Paper Trading Task H did it as a decorator
(`engine.runtime.PersistentSubmissionOrderExecutor`, since removed), real
CodeRabbit review found that violated the invariant, and the shipped design
is the `SubmissionListener` collaborator injected into
`ExchangeOrderExecutor` instead.

The count is machine-checked by
`java/execution/src/test/java/engine/execution/OrderExecutorImplementationCountTest.java`,
because a rule that has already been broken once and is enforced only by
prose will be broken again. Full record:
`.planning/paper-trading-h-vst-integration.md`.

---

## 4. Execution modes

`engine.runtime.PaperTradingApp`'s `PAPER_TRADING_EXECUTION_MODE` selects
which graph is built at startup. They are meant to run as **independent
processes** — distinct `PAPER_TRADING_REPORTS_DIR`, independent
`KillSwitch` — never as a runtime toggle on one running process.

| mode | executor | venue host |
|---|---|---|
| `simulated` (default) | `PaperBroker` | none |
| `bingx-vst` | `ExchangeOrderExecutor` → `BingXAdapter` | `BINGX_VST_BASE_URL` |
| `kis-paper` | `ExchangeOrderExecutor` → `KisAdapter` | `KIS_PAPER_BASE_URL` |

**Each mode's kill-switch behaviour at construction, and the three open gaps
bearing on it, are safety properties recorded in `CLAUDE.md`'s Architecture
section** — including in this sentence, which is why it names no behaviour.

Both venue hosts are Java constants — `BINGX_VST_BASE_URL` and
`KIS_PAPER_BASE_URL`, in `:runtime`. **That they may have no configuration
surface at all is an invariant and it is `CLAUDE.md`'s**; what is here is only
that the code reads constants today. A project-specific `PreToolUse` hook
blocks edits that would source `BINGX_VST_BASE_URL` from an environment
variable.

Current operational state — which of these is actually running — is in
`CLAUDE.md`'s Current Scope, because it changes on operator decision rather
than on structure. **Deliberately not restated here**: a state that changes
by decision, written in two files, becomes two different answers.

---

## 5. Instrument identity, and what it can and cannot carry

`OrderIntent` / `Order` / `Fill` / `SubmissionMarker` identify an instrument
with a single free-form `String symbol`. Shared schemas stay exchange- and
asset-class-agnostic.

That is sufficient for **futures**, because one string can carry the two
facts a futures contract needs: **the underlying and the delivery month.**
Stated that way because the shorter version — "identified by its expiry
month" — is true of index futures and false of single-stock futures, where
삼성전자 and SK하이닉스 at the same expiry are different contracts and the
KIS codes (`A11610`, `A50610`) encode an issue id the month alone does not
give. So the "zero schema change" claim holds across BTC-USDT perpetuals and
both kinds of KRX futures.

It is **not** sufficient for options, which need **three** facts — strike,
expiry and call/put — none of which a bare symbol string round-trips. So the
current model does not represent an option at all, and carrying one would need
a canonical symbol format designed and tested first. **Whether options are in
scope is a scope decision, and it is `CLAUDE.md`'s.**

On the Python side the same question has a second answer worth keeping
beside this one: a **price basis** is part of a storage symbol's identity
(`KRX:005930` split-adjusted vs `KRX-RAW:005930` raw), because sharing one
primary key between the two silently corrupts a series. See
`python/data/kis_klines.py` and `.planning/audit-2026-09-consolidation.md`
decision D4.

---

## 6. What a new venue actually costs

Stated concretely, because "without refactoring" is the architecture's
central claim and KIS Phase 1 (PRs #103–#106) was its first real test.

**Written new**: an `ExchangeAdapter` implementation; a token/auth
mechanism if the venue's scheme differs (`KisTokenProvider` was genuinely
new — OAuth2 app-key/secret → cached token, with no `BingXSigner`
precedent, since BingX signs statelessly per request); a `PriceFeed`; a
`TradingCalendar` if the venue is not 24/7; a `NotionalCalculator` if
notional is not price × quantity; wiring in `PaperTradingApp`.

**Not touched**: `RiskGateway`'s logic, `OrderPipeline`, `OrderStore`,
`TradingLoop`, `Reconciler`, `KillSwitch`, or any shared schema.

The one seam that had to be *created* rather than implemented was
`NotionalCalculator`, and §2 records why that is the rule working rather
than an exception to it.

---

## 7. Where the rest is

| you want | go to |
|---|---|
| safety properties, open gaps, risk parameters, gates | `CLAUDE.md` |
| what was decided, when, and what was rejected | `.planning/README.md` (indexed, test-enforced) |
| how to run the paper-trading loops | `docs/paper-trading-runbook.md` |
| verified exchange API behaviour | `CLAUDE.md`'s Exchange API Facts |
