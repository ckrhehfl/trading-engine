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

**KIS/KOSPI200 venue integration, Phase 1 — built and merged** (PRs
#103-#106). Full design record, every review-driven correction, and the
real-API verification account: `.planning/kis-phase1-venue-integration.md`.
The shared-account-ledger work that followed it is a separate effort,
recorded in `.planning/kis-ledger-a-*.md` through `-d-*.md`.

This was the first real test of the "multi-exchange / multi-symbol /
equities expansion **without refactoring** OMS, Risk Gateway, or
Execution" target above: 한국투자증권 (Korea Investment & Securities,
"KIS") REST API for KOSPI200 index futures, running as a third
independent paper-trading loop alongside the two BingX ones — own
process, own `PAPER_TRADING_REPORTS_DIR`, own `KillSwitch`, the same
pattern `bingx-vst` established relative to `simulated`.

**Scope: futures only; options explicitly deferred.** `OrderIntent` /
`Order` / `Fill` / `SubmissionMarker` identify an instrument with a
single free-form `String symbol`. A KOSPI200 futures contract is fully
identified by its expiry month, so that stays sufficient and the "zero
schema change" claim holds. An option additionally needs strike, expiry,
and call/put — none of which a bare symbol string round-trips — so
options need a canonical symbol format designed and tested first, and are
out of scope until then.

**What it added**: `KisAdapter implements ExchangeAdapter` and
`KisTokenProvider` (OAuth2 app-key/secret → cached access token —
genuinely new, with no `BingXSigner` precedent, since BingX's scheme is
stateless per-request HMAC) and `KisPriceFeed`; an
`engine.runtime.PriceFeed` interface, extracted because `TradingLoop` was
hard-typed to the concrete `BingXPriceFeed`; an
`engine.runtime.TradingCalendar` interface (`AlwaysOpenTradingCalendar`
for `simulated`/`bingx-vst`, `KrxMarketCalendar` for real KST hours); and
`PaperTradingApp` wiring — `PAPER_TRADING_EXECUTION_MODE=kis-paper`,
`forKisPaper()`, `KIS_APP_KEY`/`KIS_APP_SECRET`, an optional
`KIS_MARKET_DIVISION` (`INDEX_FUTURES` default | `STOCK_FUTURES`), and a
hardcoded `KIS_PAPER_BASE_URL` Java constant with no environment-variable
override, the same no-config-surface pattern as `BINGX_VST_BASE_URL` —
plus `KisPreflight`.

**Safety properties. Do not weaken any of these without reading the full
record first**:

- **`forKisPaper()` trips `KillSwitch` unconditionally at construction**,
  not only on a preflight or marker problem the way `forBingXVst()` does.
  It wires a real `FileSignalSource` at a real path, so the graph is
  order-submission-capable the moment any signal file appears there. The
  unconditional trip bounds that to "a human must actively choose to
  enable trading." Resetting it is a separate, explicit human decision,
  and the open gaps below are reasons not to yet.
- **`KisAdapter.setLeverage` / `setPositionMode` throw** rather than
  silently no-op'ing — KRX futures margin is exchange-mandated, not a
  user-settable multiplier. Neither `forKisPaper()` nor `KisPreflight`
  may treat that signal as success: a caller reading a normal return as
  "protection applied" would let the loop trade believing a safeguard ran
  that never did. Skipping the exchange-side call does **not** skip risk
  enforcement — `RiskGateway`'s own notional limit still applies in full.
- **`KrxMarketCalendar` fails closed.** Regular session 08:45-15:45 KST,
  shortened to 08:45-15:20 on a contract's final trading day. Any date
  whose holiday or final-trading-day status cannot be positively
  confirmed from the committed static fixture resolves to **closed**,
  never open. The night session (18:00-06:00 KST) is not supported.
  Moving lunar-calendar holidays remain a known gap — the JDK ships no
  chronology that expresses them. The calendar gates only new-signal
  processing: `pollFills` still runs every tick regardless of market
  hours, so a fill at or after the close is not left unreconciled.
- **`STOCK_FUTURES` refuses to start.** `resolveKisNotionalCalculator`
  throws before a `KillSwitch` is even constructed, because the real
  per-stock contract multiplier is still unconfirmed. `INDEX_FUTURES`
  does start (with the switch tripped) using the real KRX multiplier,
  ₩250,000 per index point, via `FixedMultiplierNotionalCalculator`.

**Three open gaps, none closed. All three belong on the same checklist as
any future decision to reset that kill switch**:

1. **Ambiguous-submission recovery has no answer for KIS.** KIS's order
   request carries no client-supplied idempotency key, unlike BingX's
   `clientOrderID` (which real VST verification confirmed gives genuine
   server-side duplicate rejection). A network failure after KIS accepts
   an order but before the response is observed leaves an `Order`
   `SUBMITTED` with no `exchangeOrderId`, which `KisAdapter.queryOrder`
   cannot resolve — it searches by exactly that id. A real recovery path
   must exist before KIS submits for real: matching a pending order
   against `inquire-ccnl` by symbol/side/quantity/time, or an explicit
   manual-confirmation step. It must never be "just resubmit."
2. **`GUARDED_MARKET` has no wire-level price guard**, for KIS or BingX.
   A null `limitPrice()` sends a real, unprotected market order on both.
   This is precisely what the Live Entry Criteria's "market-order guard
   enabled" line exists to gate; that verification has happened for
   neither adapter and needs its own `Discuss` before `GUARDED_MARKET` is
   used against a real account.
3. **A specified fail-closed validation was never built.** The design
   requires that the margin-rate input have a defined source and a
   staleness check, and that missing or stale price/margin data be a
   rejection rather than a silent fallback. It is not implemented:
   `PriceFeed#latestPrice` returns a bare `BigDecimal` with no timestamp
   (an interface-level property, so `BingXPriceFeed` is equally
   affected), `FixedMultiplierNotionalCalculator` has no staleness check
   and takes no margin rate at all, and `RiskGateway` uses `Instant.now()`
   only to stamp its own decisions. Latent rather than live today, purely
   because the unconditional kill-switch trip means no KIS order can
   reach `submitOrder` at all. Found during the 2026-08-26 documentation
   reorganization; fixing it touches `RiskGateway` and the `PriceFeed`
   interface, so it needs its own `Discuss` pass.

**`RiskGateway` gained a `NotionalCalculator` seam (PR #105), and that
does not violate this section's own rule.** `RiskGateway.java` genuinely
changed — a second constructor, a new dependency. The rule's real intent,
evidenced by every other seam here (`PriceFeed`, `TradingCalendar`,
`AccountStateProvider`, `OrderExecutor` itself), is "no per-venue branch
or hardcoded venue fact inside OMS/Risk/Execution's own logic," not "the
file's text may never be touched again." Each of those seams took exactly
one one-time interface extraction, after which every further venue
implements the interface with zero additional change to the depending
class. `RiskGateway` contains no KOSPI or KIS name, string, or number
anywhere; the real ₩250,000 lives in `PaperTradingApp` (`:runtime`), the
same layer `BINGX_VST_BASE_URL` already occupies. The original
one-argument constructor remains a zero-behavior-change delegation to
`SimpleNotionalCalculator`, used unchanged by every BTC-USDT loop.

**Still out of scope, still not done**: KOSPI200 options; the night
session; any real KOSPI200 strategy (this phase built infrastructure
only — a real strategy needs its own walk-forward-validated research
under Strategy Research Methodology below, and would be its own
`Discuss`); extending `scripts/paper-trading-watchdog.sh`, the
dashboards, or cron for a third loop; a KOSPI200-specific `RiskLimits`
tier with its own percentage numbers.

Real KIS API behaviour observed against the live paper endpoint —
response-casing quirks, `inquire-deposit` having no working paper TR id,
pagination and the `tr_cont` continuation bug, real latency, token rate
limits — is under "Exchange API Facts — KIS" below, not in the planning
doc.

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

**Revised 2026-08-29, human-approved, replacing a single combined gate.**
The previous wording was: *"Minimum 30 days (45 recommended), 50+ trades,
zero critical crashes, zero duplicate orders, zero position mismatches,
zero risk-gateway bypasses, no missing daily reports, kill switch verified
working, paper score 80+."* It is kept here verbatim because what follows
is a **relaxation of one clause**, and a relaxation must be readable
against what it replaced.

**Why it changed.** That gate mixed two different questions — *does the
plumbing work* and *does the strategy make money* — and scored them on one
calendar clock. Six of its eight clauses are about the system. Only the
50-trade clause is about the strategy, and for a low-frequency strategy it
is not merely hard but **unreachable**: `daily-tsmom-ensemble` traded 26
times in 2.95 years (`sr-v`) and 64 in 3.74 years (`sr-ab`), i.e. 9-17 per
year. 50 trades is **2.9 to 5.6 years**; 15 days is 0.4-0.7 trades. A
criterion that cannot be satisfied is not a criterion — the same reasoning
already applied on 2026-07-29 to the backtest trade-count floor.

### Gate A — Operational readiness

Proves the system, not the strategy. **Signal source is irrelevant here**
and a dedicated mock generator is explicitly allowed, per this file's own
standing permission to exercise the paper broker, `ExchangeAdapter` and
supervision loop "with dummy/mock signals independently of a validated
strategy."

- **15 consecutive days** of operation, **uptime ≥ 99%** measured from the
  daily reports' own `ticks_succeeded / ticks_attempted`
- **≥ 200 order events** through the full
  `OrderIntent → OrderPipeline → RiskGateway → Order → OrderExecutor` path
- **zero critical crashes** — a session death with no clean shutdown counts,
  and is only checkable because the session log is now persisted
- **zero duplicate orders**, **zero position mismatches**, **zero
  risk-gateway bypasses**
- **no missing daily reports** across those 15 days
- **kill switch verified** by a deliberate trip and recovery, not by waiting
- **ambiguous-submission recovery verified** — the `SUBMISSION_UNKNOWN` path

### Gate B — Strategy edge

Proves the strategy. **Not calendar-bound, because calendar time cannot
manufacture trades a strategy does not take.** Evidence comes from wherever
it legitimately can: pre-registered holdout confirmations, walk-forward
folds meeting the Eligibility Bar, or accumulated paper trades once there
are enough of them to mean something. Paper-trading trades are recorded and
reported honestly, and count toward this gate, but a strategy is never
blocked here for a trade count its own frequency makes impossible to reach.

For `daily-tsmom-ensemble` specifically, Gate B rests on what is already on
record: two disjoint pre-registered holdouts (`sr-v`, `sr-ab`) and `sr-ac`'s
combined significance (Stouffer Z = 2.914, Φ(Z) = 0.9982).

### The disclosed cost of this change, stated rather than glossed

**This does not make any strategy more proven.** It stops asking paper
trading to establish something it structurally cannot for a low-frequency
strategy, and moves that burden to where it actually sits.

The 50-trade clause was doing **more** evidentiary work than usual for
`daily-tsmom-ensemble` precisely because its own backtest fell short of the
trade-count floor — this file's Paper Trading Policy Exception says so
explicitly. Removing it leaves that strategy's edge resting on **90 backtest
trades across two holdouts**, and nothing more.

What bounds the resulting exposure is not this gate but the canary tier
itself: 1x base leverage, 2% max order notional, −0.5% daily loss limit,
−4% hard stop. Those are unchanged and are not weakened by this revision.

### "paper score 80+" — named here because this revision drops it

The old wording required a **paper score of 80+**. **No such score is
implemented anywhere in this repo** (verified 2026-08-29: no
`paper_score`/`paperScore` symbol in any Python, Java or shell source), and
no definition of it exists in this file either. It was an undefined
criterion, which means it could never have been evaluated and its
disappearance from Gate A is not a relaxation of anything real.

Recorded rather than deleted quietly, because the Live Entry Criteria below
still name it. **Until it is defined and implemented, "paper score 80+"
cannot gate anything**, and any future decision to keep it must define it
first. That definition would itself be a Risk-Parameter-class change.

**Both gates must pass before live consideration**, and the separate Live
Entry Criteria below still apply in full on top of them.

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

## Exchange API Facts

Operational reference. **Verify before relying on any of it** — every
figure here was true when observed and several are known to drift. Full
investigation detail for each finding lives in the `.planning/` doc
cited beside it.

Three venues, three different roles: **BingX** is the first and only
`ExchangeAdapter` with a paper/live path. **Binance** is a read-only
historical-data source for research — no credentials, no order
placement, and no plan to become a trading venue. **KIS** is the third
paper-trading loop (KOSPI200 index futures), kill-switch-tripped by
design.

### BingX — verified against the live public API

| Item | Value |
|---|---|
| Symbol | `BTC-USDT` |
| Recent trades | `GET /openApi/swap/v2/quote/trades` |
| Klines | `GET /openApi/swap/v3/quote/klines` |
| Range semantics | `startTime`/`endTime` half-open (`startTime <= t < endTime`), must align to the interval grid (e.g. 900,000ms for 15m), max 1000 candles per request |
| `limit` | **Not a count guarantee** — requests over 1000 are silently capped. Verify the returned count in code. |
| Over-limit capping | Keeps the **newest** rows (closest to `endTime`) |

**Historical kline retention is granularity-dependent, and *not*
monotonic in granularity** — a real BingX-side property, re-measured
rather than extrapolated. Expect every figure to drift forward; re-run
`backfill.py` rather than trust these as permanent.

| Interval | Earliest bar | Span / count | Verification |
|---|---|---|---|
| `1d` | 2021-05-14T00:00:00Z | 5.21 y, **1,901 bars, zero gaps** | full backfill (`sr-t`); bars on the UTC-midnight 86,400,000ms grid — BingX does *not* open its daily candle at a local offset |
| `1h` | 2024-04-27T10:00:00Z | 819.9 d, **19,678 bars, zero gaps** | full backfill (`sr-f`) |
| `1m` | 2024-11-30T16:00:00Z | 631.98 d, **910,040 bars, 2 real gaps** | full backfill (`scalp-s0-s3`); binary-search estimate was *exact*, reproduced bar for bar |
| `15m` | ~2025-11-16 | ~8.3 months | probe (`sr-a`) |
| `5m` | ~2026-05-02 | ~3 months | probe only, never backfilled |

`1m` is **deeper than both `15m` and `5m` despite being finer**, which
retires the earlier "finer granularity means shorter retention" reading
of the four coarser intervals. No extrapolation to an unmeasured
granularity is safe without its own probe.

**The two real `1m` gaps** (half-open, matching this project's
convention): `[2025-04-25T06:54:00Z, 2025-04-25T06:57:00Z)` (3 bars) and
`[2026-02-13T20:32:00Z, 2026-02-13T20:36:00Z)` (4 bars). Confirmed
genuinely absent via 5 consecutive retries each, not a fetch artifact.

**Gap-blindness, a real and still-open exposure**: neither
`research/walkforward.py`'s fold generation nor `python/backtest/`'s bar
iteration detects a timestamp gap — both are pure positional arithmetic,
so the bar after a gap is silently treated as one `interval_ms` step
later. Never an issue for the zero-gap `1d`/`1h` data, so `1m` introduces
it fresh. Bounded and disclosed rather than fixed: `simulate_fill`
selects the fill bar positionally (`signal_bar_index + 1`), so exactly
**one** signal position per gap is affected, with a computable delay (4
and 5 real minutes for the two gaps above); `_sharpe_ratio` sees 2
distorted observations; `resample_equity_to_daily` drifts bucket
boundaries by ≤7 minutes cumulatively. Holding-period tracking is
**not** affected — `ClosedTrade.entry_time`/`exit_time` are real
timestamps, not bar arithmetic. The guard is
`run_preregistered_holdout.py`'s `verify_known_gaps`, which fails closed
when the real gap set differs from a registration's declared one.

**Funding rate**: `GET /openApi/swap/v2/quote/fundingRate?symbol=BTC-USDT`
(v2, public). Same `{"code","msg","data"}` envelope as everything else
**except an empty result is `data: null`, not `[]`**. Newest-first,
silently capped on an over-wide request — but `limit` over 1000 is a hard
server error (`code: 109400`), unlike klines' silent clamp. `data: null`
is **flaky near the retention edge**: a range with known-good data
returned `null` on ~1-in-6 to 1-in-2 of identical repeated calls
(2026-07-27), and worse on re-probe a day later (15/15 nulls for a range
already cached locally) — consistent with a rolling window genuinely
moving, not just a flaky server. Real depth reaches **2020-11-29T12:00:00Z
(6,199 rows)**, far deeper than klines at any granularity; 3 small gaps
(4-16h) at that earliest boundary survived 3+ reruns and are treated as
genuinely gone. **Historical `fundingTime` is not always on the modern
8h/28,800,000ms grid** — a 2020-11-29 to 2021-01-05 stretch settles 4h
off it, plus one isolated row — so range validation for this endpoint
deliberately does not enforce grid alignment the way klines does. Sign
convention, verified against BingX's own docs: `fundingRate > 0` → longs
pay shorts; `< 0` → shorts pay longs. Implemented as
`payment = -sign(position_qty) × |position_qty| × markPrice × fundingRate`
using the funding row's own historical mark price. Detail: `sr-m`,
`metrics/position.py`.

### BingX — verified against the live VST (demo) API with a real key

Envelope is `{"code": 0, "msg": "", "data": ...}`, sometimes with a
top-level `timestamp`.

| Call | Real observed shape |
|---|---|
| `GET /openApi/swap/v3/user/balance` | `data` is an **array** of per-asset objects (`userId`, `asset`, `balance`, `equity`, `unrealizedProfit`, `realizedProfit`, `availableMargin`, `usedMargin`, `frozenMargin`, `shortUid`) — not a single object. Parsing must index into it. |
| `GET /openApi/swap/v2/user/positions` | Also an array; `[]` when flat |
| `GET`/`POST /openApi/swap/v1/positionSide/dual` | `dualSidePosition` came back `"true"` on a fresh key — **hedge mode is the default**, previously undocumented. Set it explicitly at startup anyway; a default can change. |

**A real order was placed, filled, and cancelled through the full
OMS-mediated path** (`OrderIntent → OrderPipeline → RiskGateway → Order →
ExchangeOrderExecutor → BingXAdapter`), 2026-08-09 — detail in
`.planning/paper-trading-h-vst-integration.md`:

- `POST /openApi/swap/v2/trade/order`'s **submit** response already
  reported `"status":"FILLED"` for a market order. `ExchangeOrderExecutor
  .submit` deliberately never trusts that (always returns
  `Optional.empty()`, resolving only via a later `pollFills`/`queryOrder`),
  so the ~1.5s ack-to-fill latency observed reflects **this project's own
  polling cadence**, not real exchange latency.
- `queryOrder` carries a real **`commission`** field (e.g.
  `"-0.032441"`, negative = fee charged), confirming a real fee figure is
  available on the wire. For that trade it landed within ~5bps of this
  project's modeled `FEE_BPS=5` (0.03244075 modeled vs 0.032441 real) —
  one data point, not proof the two always agree.
- `DELETE /openApi/swap/v2/trade/order` on an unfilled limit order
  returned the status token **`"CANCELLED"`** (double-L), confirming the
  REST half of the documented REST/WebSocket casing inconsistency.
  WebSocket's `"CANCELED"` remains unverified — no WS call has ever been
  made by this project.
- **Duplicate `clientOrderID` is rejected server-side**:
  `{"code":101400,"msg":"clientOrderID unique check failed"}`, from a
  genuinely separate graph simulating a restarted process. Real evidence
  BingX's own idempotency is an additional safety layer on top of — not a
  substitute for — this project's software-side protections. Observed,
  not officially documented.
- **Account-wide leverage was originally observed at `"20X"`** on a fresh
  VST account, unenforced by `RiskGateway`, because nothing called
  `POST /openApi/swap/v2/trade/leverage`. Since fixed: `VstPreflight` now
  sets leverage for both `LONG` and `SHORT` to
  `RiskLimits.canary().baseLeverage()` on every clean start. **Fails
  closed** — if a pre-existing non-zero position is found, leverage
  enforcement is skipped (exchanges commonly reject a change with a
  position open) and the kill switch starts tripped instead, requiring a
  deliberate human reset. A `setLeverage` failure propagates and refuses
  to start. **Real per-call HTTP verification is now done** (2026-08-26,
  observed directly in a live VST startup log while restoring the paper
  loops) — previously outstanding, because the account still held a
  position from the original run and this codebase's OMS path has no way
  to close one (in hedge mode a `SHORT` opens a second position rather
  than closing the `LONG`). That position is gone, so the clean-start
  branch finally executed against the real API:
  `VstPreflight: real VST balance=96224.4301 … no pre-existing non-zero
  positions found, clean start … real exchange-side leverage for BTC-USDT
  set to 1x (LONG and SHORT, hedge mode)`. The fail-closed
  pre-existing-position branch remains fake-adapter-verified only — it
  cannot be exercised without deliberately opening a position first.

**A real credential-handling incident, root-caused and fixed.** A
CRLF-terminated `.env` sourced naively left a trailing `\r` on
`BINGX_API_KEY`; the JDK's `HttpRequest.Builder#header` rejects a raw
`\r` (RFC 7230) with an exception **whose message embeds the offending
value verbatim** — writing the real key into a local gitignored scratch
log. A separate `cat -A` diagnostic similarly surfaced `FRED_API_KEY` in
a tool transcript. **Neither reached any committed file, git history, or
public surface.** Fixed at the root rather than merely disclosed:
`BingXAdapter`'s constructor now `.strip()`s both credentials, with a
regression test. `BINGX_API_SECRET` was never used as a header value
(HMAC only, never transmitted) and was confirmed unaffected. Rotating the
two exposed keys remains a cheap precaution — both are low-stakes
(VST-only, no withdrawal permission; and a free read-only data key).

### BingX — documented but NOT empirically verified

Read from BingX's docs, never called with a real key. Treat with less
confidence than everything above.

- **Base URLs**: `https://open-api.bingx.com` (production) vs
  `https://open-api-vst.bingx.com` (VST demo — virtual USDT, same signing
  scheme, real matching behaviour). A key made through the normal API
  Management flow authenticates against VST; whether the same key *also*
  works against production is untested and deliberately not tested.
- **Auth**: `X-BX-APIKEY` header + HMAC-SHA256 over all params including
  `timestamp`, sorted alphabetically, joined `key=value&…`, hex uppercase,
  appended as `&signature=…`. Requests must be within 5s of server time
  (`GET /openApi/swap/v2/server/time`).
- **Orders**: `POST /openApi/swap/v2/trade/order` (type field selects
  MARKET/LIMIT/etc.); `POST .../order/test` validates without executing;
  `DELETE /openApi/swap/v2/trade/order` cancels.
- **Position mode** is account-wide, not per-symbol, and cannot change
  while any position or open order exists. Leverage takes `side=BOTH` in
  one-way mode, `LONG`/`SHORT` in hedge mode.
- **Endpoint versions are mixed within the same family on purpose**:
  balance v3, positions/order/leverage v2, position-mode v1 — matching
  klines (v3) vs trades (v2).
- **Private WebSocket** shares the public market-data host with
  `?listenKey=…`, from `POST /openApi/user/auth/userDataStream` (1h TTL,
  `PUT` to refresh).
- **Rate limits** are per-account (UID): order place/cancel 10/s, order
  query 30/s, positions 10/s, balance 5/s, leverage 5/s. A changelog
  claims IP-based limits were removed 2025-12-16 while the docs UI still
  shows legacy numbers — trust neither without testing.
- **Known internal doc contradictions**, to test rather than trust: order
  status casing `CANCELLED` (REST) vs `CANCELED` (WS); the listen-key
  sample omits signature params its own metadata requires; the WS
  connection limit is stated as both 60/IP and 240/IP.

### Binance — data-research source only

**Not a trading venue and not planned to become one.** No credentials, no
order placement, read-only public klines (`data/binance_klines.py`,
`backfill_binance.py`; `sr-z`, `scalp-s5`). This section exists for the
same verify-before-relying reason as BingX's, not because a second live
surface is being added.

| Item | Value |
|---|---|
| Symbol | `BTCUSDT` (no dash — differs from BingX) |
| Spot klines | `GET https://api.binance.com/api/v3/klines` |
| USDT-M futures klines | `GET https://fapi.binance.com/fapi/v1/klines` |
| Response | A **bare JSON array of arrays, by position** — not an object envelope: `[open_time_ms, open, high, low, close, volume, close_time_ms, quote_asset_volume, num_trades, taker_buy_base_volume, taker_buy_quote_volume, ignore]`. Timestamps and `num_trades` are bare integers; OHLCV are quoted strings. |
| `endTime` | **INCLUSIVE**, not half-open — confirmed by a `startTime == endTime` request returning exactly one row. A real wire-level divergence from this project's `[start, end)` convention everywhere else, which `binance_klines.py` absorbs via `endTime = end_ms - 1`. |
| Over-limit capping | Keeps the **OLDEST** rows (closest to `startTime`) — the opposite of BingX. Consequently rows come back **ascending**, not newest-first. |
| Max `limit` | Spot **1000**, silently capped. Futures **1500**, enforced as a real `HTTP 400` (`-1130`) — futures rejects, spot does not. |
| Pre-listing range | Returns `[]`, not an error and not padded |
| Errors | A real non-2xx status with `{"code": <int>, "msg": "…"}` — never a `200` carrying an embedded error the way BingX works |

**Retention, by full backfill with independently verified gap counts**:

| Market / interval | Earliest bar | Count | Gaps |
|---|---|---|---|
| Spot `1d` | 2017-08-17T00:00:00Z | 3,275 | **0** |
| Futures `1d` | 2019-09-08T00:00:00Z | 2,523 | **0** |
| Futures `1m` | 2019-09-08T17:57:00Z | **3,661,780** | **1** (`[2019-09-08T19:00:00Z, 2019-09-08T19:01:00Z)`) |

Futures `1m` reaching essentially the market's own launch means it is
**not a rolling window at all** — a genuine structural difference from
every other retention figure in this file. Its 6.962-year span implies a
PSR/DSR detection floor of **~0.623**, the best this project has. Spot
`1d`'s ~8.97 years gives ~0.55, versus BingX's own best (`1d`, 5.21y) at
~0.72. All three sit **inside** the 0.4-0.8 credible-institutional-edge
range; the difference is where: BingX's ~0.72 makes only the range's top
sliver detectable, Binance's ~0.55 makes roughly its top two-thirds
detectable. Real power gain, not a change from undetectable to
detectable outright.

**`taker_buy_base_volume`/`taker_buy_quote_volume` (wire indices 9/10)**
are real, populated, order-flow-relevant fields, silently discarded by
`_parse_row` from `sr-z` until `scalp-s5` captured them into two additive
nullable `klines` columns (`NULL` for every BingX row — that wire has no
buyer/seller breakdown at all — and every pre-`scalp-s5` Binance row).
Non-`NULL` for all 3,661,780 futures `1m` rows, with zero
`taker_buy_base_volume > volume` violations. **Disclosed, unresolved
assumption**: this project does not trade on Binance, so using Binance
futures order flow as a proxy for BTC market-wide (or BingX-specific)
flow carries a real cross-venue transferability assumption.

**Rate limits are real, numeric, and live-confirmed** — a first for this
pipeline, fetched from each host's own `GET .../exchangeInfo`
`rateLimits`: spot `REQUEST_WEIGHT` **6000/min** per IP, futures
**2400/min** per IP. Per-request weight costs (spot flat 2; futures
tiered 1/2/5/10 by `limit` bucket) come from Binance's docs rather than
re-derived here, so are held to slightly lower confidence. **HTTP 418** is
Binance's documented temporary-IP-ban signal, distinct from `429`; not
observed live, and treated as non-retryable on the same "don't retry into
an active ban" principle.

**Computed statistics, load-bearing for how this data may be used** (not
API facts): Binance spot vs BingX daily closes over their full 1,909-day
overlap correlate at **1.000000**; daily log-returns at **0.999955**.
That shows the two **price series** are tightly linked — it does **not**
show a signal developed on one transfers profitably to the other, which
also depends on volume, funding, basis, execution costs, and timing, none
of which a price correlation measures. Binance spot-vs-futures basis over
2,523 common days: mean `(futures-spot)/spot` −0.0154%, stdev 0.0652%,
range −0.74% to +1.80%, narrowing over time — consistent with a maturing
derivatives market, not a data-quality problem.

### KIS — verified against the live paper (모의투자) API

First real contact 2026-08-21/24 (PR #103); everything in the Phase 1
design above was fake-server-verified only until then. Full account:
`.planning/kis-phase1-venue-integration.md`.

- **Response field-name casing is per-endpoint, not one convention.**
  `order` (submit) responds UPPERCASE (`ODNO`); `inquire-balance` and
  `inquire-ccnl` respond lowercase (`pdno`, `cblc_qty`, `odno`,
  `ord_qty`, `tot_ccld_qty`, …). Request parameters are always UPPERCASE
  regardless. The original implementation guessed uppercase for all
  three; the two wrong guesses parsed **every field as silently `null`**
  rather than throwing, since Jackson cannot distinguish a missing field
  from a wrong-cased one.
- **`inquire-deposit` (`CTRP6550R`) has no working paper TR id at all.**
  The real id returns `HTTP 500`/`EGW00205`; a `V`-prefixed variant
  following KIS's own real→paper convention returns `OPSQ0002` ("no such
  service code"). `getBalance()` no longer calls it — it reuses
  `inquire-balance`/`VTFO6118R` and reads `output2`.
- **`inquire-balance` requires `CTX_AREA_FK200`/`CTX_AREA_NK200` even on
  a call that never paginates** — omitting either gives `OPSQ2001`.
- **`inquire-balance` genuinely paginates** (max 20 rows per call).
  `getPositions()` originally read only the first page. It now follows a
  bounded continuation loop (`MAX_INQUIRE_PAGES = 10`, shared with
  `queryOrder`) and **fails closed** rather than return a silently
  incomplete position list.
- **`tr_cont` convention**: `"M"` means more pages; anything else,
  including the real observed `"F"`, means stop. A latent bug treated
  `"F"` as continue — masked in `queryOrder` by its own early exit, and
  surfaced only when `getPositions()` exhausted a fake server's queued
  responses. Both loops now continue only on `"M"`, and both fail closed
  on `"M"` paired with a blank continuation key.
- **Real observed latency is 7-10 seconds** for both `POST /oauth2/tokenP`
  and `inquire-balance` — the actual cause of intermittent
  `HttpTimeoutException`s against the original 10s timeout, since widened
  to 20s. Treat KIS's paper host as meaningfully slower than BingX's.
- **`/oauth2/tokenP` has a real rate limit** (`EGW00133`), triggered by
  repeated token requests within roughly a minute. `KisTokenProvider`
  caches per JVM process, so repeated restarts while debugging can
  exhaust it. Space restarts ~60-90s apart; it self-resolves and is not a
  credential or code problem.
- **`getBalance()`'s `output2` → `BalanceSnapshot` mapping** (KIS's own
  column names; no exact 1:1 semantic match for every field):
  `tot_dncl_amt` (총예수금액) → `balance`; `prsm_dpast_amt` (추정예탁자산금액)
  → `equity`; `ord_psbl_cash` (주문가능현금) → `availableMargin`;
  `mgna_tota` (증거금총액) → `usedMargin`; `evlu_pfls_amt_smtl`
  (평가손익금액합계) → `unrealizedProfit`. **`balance` is load-bearing, not
  informational**: `forKisPaper()` bootstraps `SharedKisAccountLedger`'s
  entire `allocatedVirtualCapital` from it, matching `BingXAdapter`'s own
  raw-balance convention.
- **Every response-parsing failure mode is fail-closed** (tightened over
  five real review rounds): a missing or malformed `output1`/`output2`, a
  missing `cblc_qty`/`pdno`/`ord_qty`/`tot_ccld_qty`/`qty`, or a mutually
  inconsistent `ord_qty`/`tot_ccld_qty`/`qty` triple all throw rather than
  produce a `null` amount, an empty symbol, or a misclassified status.
  **No exception message anywhere in `KisAdapter`/`KisPriceFeed` embeds
  raw response content** — a real KIS response carries account numbers and
  balances, and these exceptions land in a persisted `kis-paper.log`.
- **First real end-to-end run, 2026-08-24**, symbol `A01609`: real balance
  50,000,000 KRW, no pre-existing positions, ledger bootstrapped from that
  balance, clean reconciliation (`ledgerExposure=0 realExposure=0
  mismatch=0`), a real tick completed. **The kill switch starts tripped by
  design**, so no order was or could be submitted.

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

### Conclusion checks — mandatory before any research conclusion is reported

Added 2026-08-28 after a scalping arc that produced **nine** documented
errors, of which **zero** were caught by the author at the time of
writing. Eight of the nine happened inside narrow, well-scoped tasks, so
smaller scope is not the remedy — a verification step that runs against
the conclusion's own evidence is.

`python/research/conclusion_check.py` implements six checks. **Every one
exists because this project made that exact mistake, and each carries the
citation.** That is the design constraint: a checklist of things that
merely sound like good practice becomes theatre nobody runs.

| Check | Catches | Incident |
|---|---|---|
| `check_non_overlapping` | a t/p/stderr over overlapping windows | S13's t = 7-8, really 1.5-2.6 |
| `check_parameter_swept` | a domain judged from one setting | S14/S15 ran only `entry_z=5.0` |
| `check_criterion_attainable` | a bar unreachable at this sample size | 80% fold consistency at 2 trades/fold |
| `check_same_population` | two figures compared across different samples | S15's stop table's `none` row |
| `check_claim_monotonic` / `check_claim_universal` | prose contradicting its own table | "every delay is worse, monotonically" |
| `check_dsr_agrees` | a second DSR implementation drifting | fold variance fed where trial variance belongs |

`require_no_blockers` **raises**, and that is deliberate — S13's inflated
t-statistic was disclosed in prose and still became a headline number.

Two consequences that are already wired in and must not be undone:
`s14_eligibility.py` runs `check_criterion_attainable` **before** printing
a verdict and reports the fold-based lines as **UNINFORMATIVE** rather
than FAIL when the bar is unreachable (a criterion a good strategy clears
4.6e-05 of the time is evidence about the criterion, not the strategy);
and it **delegates** DSR/PSR/trial counts to `research/retrospective.py`
rather than keeping a second implementation.

**What these checks explicitly cannot do**, stated so they are not
over-trusted: they catch arithmetic and bookkeeping errors in a
conclusion's own evidence. They cannot catch **asking the wrong
question**. Every one of this project's largest errors that a human
caught — comparing costs to an *unconditional* move distribution, testing
a *directional* hypothesis by measuring *magnitude*, running 57 trades a
day and calling it "scalping" — passes all six cleanly. That failure mode
needs someone asking whether the measurement matches the world, which is
what the human checkpoints below are for.

### Human checkpoints — three moments, not a time interval

Also added 2026-08-28. Periodic check-ins were considered and rejected:
the expensive errors did not happen at time intervals, they happened at
**decision transitions**. Stop and get a human decision at exactly these
three, and at no others — everything else (code, tests, PRs, review
response, merges) stays delegated per the Auto-merge Policy.

1. **Before declaring a direction closed.** "This signal/approach does not
   work" is the single most expensive claim in this project, because it
   stops further work. S15 made it having tested one of two surviving
   parameter cells; the untested one was positive.
2. **Before spending a holdout.** One access, permanently. Binance spot
   1m is the last untouched 1m window this project has.
3. **Before a conclusion is written into CLAUDE.md.** Future sessions have
   only this file to go on and will treat what is here as settled.

The human's own contribution at these points is not review of the
arithmetic — the checks above cover that. It is the question the
measurement cannot ask itself: *does this match how the thing actually
works?* On the record for this arc, every time that question was asked it
found a real problem.

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

The mechanics the section above leaves open — experiment-tracking format,
walk-forward window sizing, holdout-split enforcement — were designed
2026-07-25 and built across four merged tasks: `python/data/` (BingX
kline pipeline, SQLite cache), `python/backtest/kline_window.py` +
`python/metrics/` (O(1) lookahead-safe iteration, position/equity/Sharpe/
drawdown reconstruction), `python/research/` (walk-forward harness,
holdout enforcement, the `runs/experiments.jsonl` log), and
`research/strategies/ma_crossover.py` (a placeholder proving the pipeline
end to end against live data — not a validated strategy). Build detail
and judgment calls: `.planning/sr-a-*.md` through `sr-d-*.md`.

**Default walk-forward windows** (defined in bars, the canonical unit,
not calendar months): train 8,640 bars (~90 days), validate 2,880 (~30
days), step = validate — rolling fixed-size sliding, not expanding,
non-overlapping by default. These are the 15m defaults.

**Per-timeframe fold geometry**, each derived from that timeframe's real
measured retention rather than assumed:

| Timeframe | Geometry | Folds | Notes |
|---|---|---|---|
| 15m | the defaults above | 3 | ~252 days retention (24,199 bars) — short of the Eligibility Bar's 8-10 fold floor, which is why primary research moved off it |
| 1h | `train_bars=2160`, `validate_bars=720`, `step_bars=720` | **19** | scaled down for the timeframe, not the same numbers on a different unit; the fold count behind every 1h result in "Strategy Attempts" below |
| 1d | `train_bars=90`, `validate_bars=60`, `step_bars=60` | 12 | used by the macro-conditioned attempts on the 1d research split |

**Detection floors, by window** — the annualized Sharpe below which a
result cannot be distinguished from noise at one-sided α=0.05, computed
as `1.6449/sqrt(years)` on daily-resampled returns (the floor depends on
**calendar span, not bar count**, which is why a finer timeframe does not
buy statistical power):

| Window | Floor |
|---|---|
| 15m research | ~2.18 |
| 1h research | ~1.21 |
| 1h trailing holdout | ~2.57 |
| 1d early-window holdout | ~0.96 |
| Binance spot 1d "virgin" holdout | ~0.85 |
| BingX 1m (full 631.98-day window) | ~1.25 |
| Binance futures 1m (full 6.96-year window) | **~0.62** — the best this project has, and still unspent |

**The `1d` holdout is inverted, deliberately** (`sr-t`): every logged
backtest run starts at or after 2024-04-27T10:00Z (1h retention's floor),
so 1d data *before* that date had been touched by zero trials.
`configs/research/holdout_1d.json` therefore uses the optional
`"holdout_side": "before"` key (default `"after"`, so 15m/1h configs are
unaffected), reserving the **earliest** 1,079 daily bars rather than a
trailing slice. That looks backwards and isn't — a holdout is data whose
contents have informed no decision, and here that is the early window.
Full reasoning: `.planning/sr-t-daily-data-path.md` and
`research/holdout.py`'s module docstring. That holdout has since been
spent, once, by `sr-v`.

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

### Strategy Attempts So Far

Every attempt below was walk-forward validated or holdout-confirmed
against real exchange data, and every result — including the negative
ones, which is most of them — is recorded honestly in its own
`.planning/` doc. **Nothing has cleared the Eligibility Bar outright.**
One strategy proceeded to paper trading anyway, under an explicit,
narrowly-scoped human policy exception documented immediately below this
subsection.

**The single most important finding is about the windows, not the
strategies.** The 1h research window's own detection floor is **~1.21**
annualized Sharpe (one-sided α=0.05 over 1.84 years); the 15m window's is
**~2.18**. A real edge of 0.4-0.8 Sharpe — the range credible
institutional trend-following actually reports — **could not have been
detected there by any strategy, however well specified.** So the
uniformly near-zero DSRs below mean **not shown**, emphatically not
**shown absent**. Configuration C would have needed an annualized Sharpe
of 4.6 to clear DSR 0.95 at that `N`, which says more about having
searched 117 times against 1.8 years of one symbol than about any
strategy. This is why the standing rule above closes the 1h window to
*selection* while leaving it open for reproduction, diagnosis, and
infrastructure testing.

| Line of work | Attempts | Outcome | Records |
|---|---|---|---|
| BTC-only price signals (15m/1h): SMA crossover, ATR-risk-managed crossover, multi-lookback ensemble with ADX regime weighting and vol targeting ("Configuration C"), regime-gated mean reversion, momentum/mean-reversion blend, on-balance-volume trend, funding-rate extremity | 8 strategies across 4 families; **18 distinct configurations** de-duplicated from 33 runs | **0 of 18 survive.** 12 `REJECTED`, 2 `REJECTED-UNDERPOWERED`, 4 `INCONCLUSIVE-DATA-LIMITED`. Best in project history — Configuration C with funding P&L, mean annualized Sharpe **+0.039** — reaches **DSR = 2.0e-05** against 117 research selection trials: indistinguishable from the best of 117 coin flips. | `sr-e` … `sr-o`; retrospective closeout `sr-r` |
| `daily-tsmom-ensemble` on the 1d early-window holdout (BingX 2021-2024) — zero-fitted-parameter Moskowitz-Ooi-Pedersen, specification and registration committed *before* any 1d data was accessed | 1 pre-registered access | **INCONCLUSIVE.** PSR 0.9367 (< 0.95); Sharpe 0.882 (< the window's own 0.9567 floor — "not powered to confirm"); 26 trades (< 53). Drawdown 12.0% and profit factor 2.87 both cleared comfortably. | `sr-s`, `sr-t`, `sr-u`, `sr-v` |
| Macro-conditioned signals on the untouched BTC 1d *research* split: 10-year real yield (`DFII10`, inverted), then S&P 500 trend (`SP500`, not inverted). Both zero-fitted-parameter, same 63-day lookback, same fold geometry for direct comparability | 2 attempts, pre-authorized individually | Both **INCONCLUSIVE-DATA-LIMITED** — 34 and 19 trades against a 36-trade floor. Remaining metrics are descriptive only and are **not** grounds for a directional conclusion either way. Named temptations (loosen the geometry, flip the inversion, shorten the lookback) were recorded and not acted on. | `sr-w`, `sr-x`, `sr-y` |
| Same-asset alternate-venue replication of the identical `daily-tsmom-ensemble` hypothesis, byte-for-byte unmodified code, against Binance spot's pre-2021 "virgin" window (2017-2021) | 1 pre-registered access | **INCONCLUSIVE**, but the closest anything has come: PSR 0.9945, Sharpe 1.305 (> the 0.8503 floor), profit factor 7.68 — three of five gates clear by wide margins, all stronger than `sr-v`'s. The two misses are very narrow: **64 trades vs. a 68 floor**, and **max drawdown 20.135% vs. a 20% ceiling**. | `sr-aa`, `sr-ab` |
| Retrospective meta-analysis of the two independent `daily-tsmom-ensemble` holdouts (no new data accessed, no new trial) | 0 new accesses | Combining two disjoint-sample significance tests via Stouffer's weighted Z gives **Z = 2.914, Φ(Z) = 0.9982** — genuinely stronger than either individual PSR. But a full PASS is **mathematically impossible**: for any chronological concatenation, combined max drawdown is provably `>= max(leg1, leg2) = 20.14%`, already over the 20% ceiling before the true figure is computed. Combined trades 90 vs. a recomputed 100 floor. | `sr-ac` |

**What the meta-analysis does and does not establish**, since it is the
strongest positive result this project has: the combined significance is
real evidence, not an artifact. It does **not** show a live-tradeable
strategy — the drawdown ceiling is a practical risk-control limit and the
trade-count floor a minimum-evidence-volume requirement, both independent
of whether a mean effect is statistically real, and neither is overridden
by a strong Z-score answering a different question.

**Two structural remedies remain open, and neither has been chosen** —
that choice is a human `Discuss`, not something any of the above decided
on its own authority:

1. **Multi-symbol expansion with survivorship-safe data.** A meaningful
   share of the Sharpe that institutional research reports plausibly
   comes from cross-symbol diversification a single-symbol design cannot
   access. Touches the data pipeline's survivorship-bias handling.
   Currently deprioritized on a practical judgment — a survivorship-safe,
   comparably-liquid universe beyond BTC/ETH is not readily available
   from this project's current sources — which is a sequencing choice,
   not an architectural reversal of the multi-symbol design targets above.
2. **A genuinely different data source or asset class.** Two macro data
   points (`sr-x`, `sr-y`) are a first probe, not an exhaustive test.
   `DGS10` and `DTWEXBGS` remain cached but untested; testing either
   needs its own fresh authorization the way each macro hypothesis did.
   On-chain data has been named but never attempted.

**Explicitly not a live option: another search, threshold, or lookback
set, on any timeframe, against any signal class.** `sr-u`'s
pre-registration committed to that stopping rule *before* its run, and
`sr-v`'s INCONCLUSIVE result triggered it: the only legitimate remedy is
more calendar time or more data. Same-asset alternate-venue replication
is also now closed off for the daily-TSMOM hypothesis specifically —
Binance's pre-2021 window was the last independent-ish BTC price source,
and BingX/Binance daily closes correlate at 0.999955.

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
requires the Paper Trading Pass Criteria in force — which were **revised
2026-08-29** into Gate A (operational readiness) + Gate B (strategy edge);
the wording quoted in the rest of this paragraph is the pre-revision one and
is retained because the exception was granted against it — before any live
consideration — and given the backtest
evidence itself fell short of the trade-count floor, paper trading's own
50+-trade requirement is doing more evidentiary work than usual here, not
less); waive the separate Live Entry Criteria; relax any Risk Parameter
(canary tier limits apply in full); or loosen the Eligibility Bar, the
walk-forward-validation requirement, or the standing rule against further
parameter searching for any OTHER strategy. A future strategy citing this
exception without a comparably strong multi-window independent
replication AND zero fitted parameters is not following this precedent
correctly.

### Scalping Strategy Research — Tasks S0-S16; the 1m window is now closed to selection, and the reason is arithmetic

A second research direction alongside `daily-tsmom-ensemble`, opened
2026-08-24 at the human operator's request: **retail scalping** (minutes
to tens of minutes holding period) on BTC-USDT, explicitly asked for as a
methodology-first effort after several ad hoc "find me a strategy"
attempts had failed. Full records — `.planning/scalp-s0-s3-methodology.md`
(the design, the real 1m retention probe, the cost gate, the statistical
design decision, plus the investigation that ruled out two candidates),
then `scalp-s4-vwap-mid-reversion.md` and its `-result.md`,
`scalp-s5-binance-1m-orderflow-infra.md`,
`scalp-s6-ofi-momentum.md` and its `-result.md`, and
`scalp-s7-backtest-insolvency-floor.md`.

**Scope, decided and not to be widened silently**: 1-minute bars and
coarser only. No tick or trade-level data, no true HFT — this stays
inside the permanent "HFT / co-location / tick-level strategies" non-goal
above. "Tens of seconds" was considered and rejected for this phase; it
would need a trade/tick collection layer this project does not have.

**Standing constraints these tasks set. These are rules, not history —
a future scalping candidate must obey them or justify a change here
first**:

- **`bars_per_day = 1440`** wherever the Eligibility Bar's formulas need
  it, following the same one-constant-per-timeframe convention
  `DEFAULT_BARS_PER_DAY` already establishes (96 = 15m, 24 = 1h, 1 = 1d).
- **`FEE_BPS = 5`** — BingX's and Binance's own published VIP0 taker fee
  for USDT-M perpetual futures, re-verified for this use rather than
  inherited from the daily strategy. (Their *spot* VIP0 taker fee is
  0.10% = 10bps; irrelevant to this project's futures-only scope, but it
  did surface that `sr-ab`'s Binance **spot** holdout used 5bps and so
  understated its own costs. That access is spent and its result already
  disclosed; editing the spent config now would misrepresent history.)
- **`SLIPPAGE_BPS = 1`** for scalping `GUARDED_MARKET` preregistrations,
  **revised down from 10 on a real measurement (Task S9,
  `.planning/scalp-s9-slippage-measurement.md`)**. S2's original 10 was
  ~2.5x a cited ~4bps "typical BTC-USDT spread", disclosed at the time
  as a reasoned estimate rather than a measurement. Measured against
  public Binance `aggTrades` across three days spanning a 22x volatility
  range (1.5M direction-flip observations), **the median direction-flip
  price difference on BTCUSDT perpetual futures is 0.014-0.015bps — one
  tick — in every regime**, with the 99th percentile on the most
  volatile day still only 2.4bps. Stated as the observed statistic
  rather than as "the true effective spread" deliberately: the estimator
  is not a guaranteed upper bound, since price movement between the two
  trades can offset the spread as easily as add to it. It is
  corroborated independently by the tick-multiple distribution (98.3%
  of pairs at exactly one tick on the quiet day) and by live BingX
  quotes at 0.026-0.038bps, neither of which uses that estimator. So the
  cited ~4bps figure does not describe this instrument on a major venue;
  it was two to three orders of magnitude too wide. Market impact is
  separately negligible **on Binance** at canary size (0.03 BTC against
  a minimum ±0.20% depth of 101 BTC on the most volatile day); **on
  BingX it is about one tick**, since the live samples showed a thin
  best ask (0.010-0.028 BTC) that a 0.03 BTC order can clear — a real
  venue difference, not merged into the Binance figure.
  `SLIPPAGE_BPS = 1` is still ~65x the measured half-spread,
  deliberately, to absorb the BingX-vs-Binance gap and regime variation
  three days cannot capture. Revising either constant needs its own
  justification here, never silent per-strategy tuning to make a
  marginal candidate pass.
- **Consequence: the taker fee now dominates the cost structure
  entirely** — 5bps against a ~0.015bps spread, roughly 330x. Round trip
  is **~12bps, not 30**. Two things follow. First, *reducing the number
  of round trips* matters far more than improving execution precision;
  both prior candidates traded ~70 and ~89 times per day. Second, the
  taker-vs-maker fee gap becomes a first-order design question rather
  than a detail, which is a real argument for revisiting the
  `GUARDED_MARKET`-only restriction below — though that restriction
  exists because of `fill.py`'s optimistic limit-fill model, which this
  measurement does not address and which would have to be hardened
  first.
- **`GUARDED_MARKET` execution only.** `fill.py` applies slippage
  *exclusively* to `GUARDED_MARKET` orders; a `LIMIT` order fills 100% at
  the exact limit price the instant a bar's high/low touches it,
  completely unaffected by `slippage_bps`. Declaring a higher
  `slippage_bps` for a limit-order candidate would silently do nothing
  and manufacture false conservatism. This is a **policy exclusion, not
  an engine limitation** — `simulate_fill` has a real, working `LIMIT`
  branch; this project chooses not to trust its optimistic
  fill-on-touch model for scalping validation until that model is
  hardened. It is also a research-scope decision only, and never a
  live-or-paper submission approval: the Live Entry Criteria's separate,
  still-unverified "market-order guard enabled" requirement stands
  regardless, and every real order still passes through the Java
  `RiskGateway` in full.
- **Cost gate, evaluated before or alongside statistical significance.**
  A candidate must show mean profit factor **> 1.0** (not merely
  positive — profit factor is a ratio of two non-negative magnitudes, so
  "positive" excludes nothing) and positive mean Sharpe under those
  constants. For a research-split candidate the gate runs on the research
  folds *before* any walk-forward/DSR test; for a single pre-registered
  holdout there is no prior access to spend, so the same criteria are
  evaluated **from that one access**, alongside the Eligibility Bar's
  single-window checks. Failing it is reported as **cost-disqualified**
  and takes reporting priority over a PSR-based pass — a high PSR on a
  cost-disqualified run is an artifact of the assumed fee/slippage
  figures, not evidence of a tradeable edge. A cost-disqualified run is
  still logged via `log_run` and still counts toward the project-level
  selection-trial `N`: it was a real attempt to find a viable
  configuration, and excluding it would understate how much searching
  happened.
- **1-minute research uses a single whole-window pre-registered
  holdout**, not a research/validate split — decided 2026-08-25 with the
  human operator, on a real computed finding. PSR/DSR resample to *daily*
  granularity before computing significance, so the detection floor
  depends on **calendar span, not bar count** (~`1.6449/sqrt(years)`).
  BingX's full 631.98-day 1m window therefore floors at ~1.25 — barely
  better than the already-spent 1h window's ~1.21 — and any split would
  push the holdout's own floor higher still. A single access, evaluated
  once under the Eligibility Bar's Holdout confirmation (single-window
  variant), follows `daily-tsmom-ensemble`'s own `sr-u`/`sr-v`/`sr-aa`/
  `sr-ab` precedent rather than inventing a mechanism. Such a holdout
  contributes **zero** to the project-level `N`, not one —
  `overfitting_check.py` excludes holdout runs and their children by
  design, since a holdout was never searched over.
- **Gap-aware pre-access check, fail-closed.** `1m` data has real
  timestamp gaps, and neither `walkforward.py`'s fold generation nor the
  backtest engine's bar iteration detects one — both are pure positional
  arithmetic. A registration declaring `data.known_gaps` is verified by
  `run_preregistered_holdout.py`'s `verify_known_gaps` *before* any data
  loads, and fails closed if the real gap set differs from the declared
  one. Not "fail on any gap" — the known gaps are real and permanent —
  but a guard against an *unexpected* one appearing.

**Both candidates ran for real. Both came back INCONCLUSIVE, for
genuinely different reasons — an informative pair, not two redundant
failures**:

- **`vwap-mid-reversion` (S4)**, 20-period VWAP with a 2-SD band, no risk
  control at all: `PSR 0.999999` but max drawdown 10,619%, profit factor
  0.00117, and Sharpe 0.392 below the window's own 1.250 detection floor.
  44,344 trades at a **1.13% win rate**. The honest lesson: **"zero free
  parameters" and "zero risk controls" are different disciplines.**
  `daily_tsmom_ensemble`'s hold-until-reversal convention is defensible
  for trend-following, where a big move *is* the thesis; it does not
  transfer to mean-reversion, whose core risk is precisely that the
  market does not revert.
- **`ofi-momentum` (S6)**, 15-bar order-flow imbalance with a 2-SD band
  and a real ATR stop/target reused unmodified from `risk_management.py`:
  Sharpe 0.640 **did** clear its own 0.623 detection floor — but that is
  a statement about *power*, not significance, and the actual
  significance test, `PSR 0.823` against the registered 0.95, **failed**.
  Max drawdown 239,161% and profit factor 0.055 also failed. 56,441
  trades at a 17.98% win rate against the ~33.3% breakeven the 1:2
  risk:reward needs. The lesson, deeper than S4's: **a per-trade stop is
  necessary but demonstrably not sufficient.** It bounded each trade to
  ~$100 and still could not bound the *sum* across tens of thousands of
  trades against a negative edge, because `compute_position_size` sizes
  against a **fixed** `reference_equity` constant rather than real
  shrinking equity — a project-wide characteristic shared by every
  strategy using that module.

Both registrations scoped their own outcome narrowly (the `sr-ab`
precedent): each parks **that specific hypothesis**, and neither ends the
scalping direction nor affects any other strategy's logged result.
Re-running either spec, or grid-searching its constants, is foreclosed by
its own `stopping_rule`.

**Task S7** then closed the shared-infrastructure half of what S4 and S6
exposed: `run_backtest` gained an opt-in `starting_equity` insolvency
floor (reusing `metrics.position.PositionTracker`, so the engine's check
and the downstream equity curve cannot drift apart), permanently stopping
new fills once equity reaches or drops below zero, and now wired into
both `walkforward.py` and `run_preregistered_holdout.py`. **This is the
circuit-breaker half only** — making a strategy's own sizing equity-aware
is a separate, larger, still-undone direction, and no strategy's sizing
changed.

Liquidation cascades, the one remaining named candidate from the original
list, was investigated and found to lack a usable foundation: the real
papers propose no trading rule, their OHLCV-computable early-warning
signal is silent in 2 of 7 studied cascades, they sweep 39 configurations
with no reusable convention, and no post-cascade reversion pattern is
documented anywhere in that literature.

**Task S8 (2026-08-26) rebuilt the research methodology** rather than
attempting a third candidate under the same one. Full document, with
external sources: `.planning/scalp-s8-research-methodology.md`. It exists
because the human operator pushed back on a framing that was producing
mechanical single-indicator candidates, and the pushback was correct.

**Two prior claims in this section were wrong and are corrected here.**

1. **"Minutes-scale scalping is arithmetically impossible after costs"
   was an artefact of comparing costs to the *unconditional* median
   move** — the move from entering at a uniformly random moment. A
   strategy does not enter at random. Measured on the real Binance
   futures 1m window (3,661,780 bars), conditioning on recent activity
   (rolling 30-bar sum of |1m returns|, known at decision time), the
   median absolute move as a multiple of the 30bps round trip is:
   15 min → **1.09x in the top 10% of activity, 2.08x in the top 1%**
   (0.40x unconditionally); 1 hour → 1.98x / 3.51x (0.76x
   unconditionally). **So 15-minute holding is not excluded on cost
   grounds** once entries are restricted to elevated-activity moments —
   which retires the "impossible" claim, and is all it does. An absolute
   move is unsigned: this shows the move is large enough that a round
   trip *could* be covered, not that direction is predictable, and
   proves neither post-cost expectancy nor out-of-sample behaviour.
   **"Viable" stays reserved for a candidate that has cleared
   signed-return evidence, a measured win rate, real execution costs,
   and out-of-sample validation.** The conditioning itself rests on
   volatility clustering (Mandelbrot 1963; Engle 1982), one of the most
   robust empirical facts in finance — which supports the *magnitude*
   conditioning only, not any directional claim.
2. **That conclusion is fragile to `SLIPPAGE_BPS`, in the wrong
   direction.** Such a strategy deliberately enters when spreads widen
   and depth thins, but `SLIPPAGE_BPS = 10` was calibrated against a
   ~4bps *typical* spread. At 30bps one-way slippage, 15-minute holding
   fails even in the top 1% of activity (0.89x) while 1-2 hour holding
   survives. **Shorter horizons are structurally more exposed to this
   assumption**, so real slippage must be measured before any
   short-horizon candidate is trusted — it is the first item in S8's
   own work order, ahead of any signal research.

**Task S9 executed that measurement (2026-08-26), and it resolved the
fragility in the favourable direction** — see the revised
`SLIPPAGE_BPS` entry above and
`.planning/scalp-s9-slippage-measurement.md`. No order was placed; the
public-data route S8 named as preferred was sufficient, so the bounded
VST demo fallback was not needed. With the measured ~12bps round trip
rather than the assumed 30bps, the horizons excluded on cost grounds
shrink sharply: 15-minute holding moves from 0.40x to **0.99x
unconditionally** and 1.09x to **2.73x** in the top 10% of activity;
30-minute clears at **1.37x even unconditionally**. **This still says
nothing about direction** — the S8 language above stands unchanged,
these remain unsigned absolute moves, and "viable" remains reserved for
a candidate that has cleared signed-return evidence, a measured win
rate, real execution costs, and out-of-sample validation. What changed
is only which horizons are ruled out before that work begins.

**What S8 changes, binding on any future scalping candidate**:

- **Decompose the strategy** into direction / entry-exit / sizing and
  research them separately. Both failed candidates fused all three into
  one threshold rule.
- **A hypothesis must name a mechanism** — who is on the other side and
  why they lose. "20-period VWAP, 2 SD" is a formula, not a hypothesis.
- **A regime layer comes before signals**: two-axis (direction ×
  volatility), with hysteresis and a minimum dwell time to stop label
  flicker, computed only from information available at bar close.
  Running mean-reversion into an emerging trend is the documented
  classic blowup, and is exactly what `vwap-mid-reversion` did.
  **Task S10 built exactly this, measured it, found the first version
  carried no information, isolated why, and fixed half of it.** The
  original volatility axis used an ATR *ratio* (current ATR over its own
  trailing mean) and separated forward 15-minute movement only **1.22x**
  top-1%-vs-all. Swapping to **absolute** ATR as a fraction of price,
  changing nothing else, gives **5.21x** — matching the independent
  rolling-sum-of-|returns| activity measure's 5.25x on the same
  3.6M bars. A ratio to a recent mean discards exactly the absolute level
  a cost-versus-move decision needs: a market that doubles its volatility
  and stays there reads 1.0. `VolatilityAxis.ABSOLUTE` is now the
  default; `RATIO` is retained only so the negative result stays
  reproducible.
  **Two things did not get fixed, and both bind on anyone using this.**
  First, the **structure axis (ADX) still carries nothing** — within a
  volatility state the two structures are indistinguishable (15.6 vs
  15.1bps in expansion, 10.0 vs 10.4 in compression), and its
  trailing-vs-forward return correlation is under 0.015 everywhere,
  below even the 0.02-0.05 band named as usable two bullets down. ADX
  has now failed on both axes it could have carried information on.
  Second, **discretising costs most of the signal**: the classifier
  separates ~1.5x where the continuous measure underneath it separates
  5.2x, the expected price of two states plus hysteresis plus a 14-bar
  dwell. So: **use the continuous absolute volatility measure as a
  conditioner, not the discrete label** — including for the per-feature
  IC work below — and reach for the label only where a genuinely
  discrete state is required, without relying on its structure axis. The
  diagnosis of `vwap-mid-reversion` stands throughout; what failed was a
  particular operationalisation of the remedy, and only partly. Full
  result: `.planning/scalp-s10-regime-classifier.md`.
- **Measure signals as signals (IC) before assembling a strategy.**
  Usable ICs are small — 0.02-0.05 is genuinely useful, so individual
  features will look unimpressive and that is normal. **Task S11 built
  the harness (`research/ic.py`, time-series rank IC, non-overlapping
  samples, Benjamini-Hochberg across the whole sweep) and ran it: 10 of
  26 feature×horizon combinations clear both bars.** Strongest are
  `htf_ret_4h` (−0.051 at 60m), `htf_ret_1d` (−0.033), distance to the
  prior-day high (−0.030) and taker-buy share (−0.027 at 15m).
  **Every price and momentum IC is negative** — mean reversion at the
  hour scale, consistently signed across four different formulations —
  and conditioning on the top 10% of activity *strengthens* most of them
  rather than washing them out. Nothing usable from volume ratio, round
  numbers, or weekday. Full result: `.planning/scalp-s11-feature-ic.md`.
- **Orthogonality is what decides how many signals you have, and S11
  measured it: those 10 features are about 3 signals.** Daily price
  structure collapses to one (four members correlated 0.72-0.85);
  4-hour momentum is partially distinct (0.33-0.41); and **order flow is
  essentially uncorrelated with every price-based feature, |r| ≤ 0.006**
  — indistinguishable from zero, which is exactly the condition
  Grinold's law needs and the first time this project has held two
  genuinely independent information sources at once. Combine the three,
  not the ten, and treat `sqrt(3)` as an upper bound rather than a
  forecast.
- **Combine weak, uncorrelated signals.** Grinold's `IR ≈ IC × √breadth`
  makes **orthogonality, not individual signal strength, the binding
  constraint** — treated as a design principle and upper bound, never a
  performance forecast (the law is known to overstate achievable IR).
- **Place stops and targets from MAE/MFE distributions**, not
  convention. S6's `stop_multiplier=1.5`/`target_multiplier=3.0` were
  never measured against anything. **Task S12 built the machinery
  (`research/excursion.py`) and measured it: on 106,361 provisional
  positions, a 1.5 ATR stop would have destroyed 40.9% of eventual
  winners.** The distribution puts Sweeney's boundary near **2.65 ATR**
  (winners' 80th percentile), which still truncates 72.7% of losers.
  **The bigger finding is not the stop.** That entry — fading `htf_ret_4h`
  and taker-buy share, the two signals S11 measured as orthogonal,
  equal-weighted with no fitting — has a gross mean outcome of only
  **+0.95bps per position** and loses **−11.05bps net**. No significance
  test was run on it, so it is a provisional, unvalidated figure rather
  than a demonstrated signal. An earlier draft put that
  gross figure at +4.28bps; **roughly 80% of it was look-ahead**, because
  the activity filter selecting the entries used a percentile computed
  over the whole dataset. Replaced with a trailing rank
  (`excursion.trailing_percentile_rank`). The **taker fee alone (10bps
  round trip) is 10.5x that figure**, the full 12bps round trip 12.6x,
  and — the part that forecloses a remedy — **even a maker round trip of
  ~2bps still loses (−1.05bps)**. Moving to maker execution was the one
  route that looked like it might close the gap; it does not, without
  needing to harden `fill.py`'s limit model first. +0.95bps is also small
  enough that it should not be called an edge without a significance
  test.

  **The same look-ahead affects one S11 claim**: the "conditioning on the
  top 10% of activity strengthens most ICs" table used
  `research.ic.conditional_ic`, whose quantile is also global. S11's
  unconditional ICs are unaffected and stand; the tradeability
  conclusion drawn from the conditional table is withdrawn pending a
  trailing-rank re-measurement. That is the S9 fee-dominance finding
  measured against a real signal rather than an unconditional price
  move, and **it is the bar every future scalping candidate has to
  clear**. Median MFE capture is 0.129, well below the 35-55% band, so a
  pure time-based exit leaves most of the available move on the table.
  Sweeney's fragility test does *not* fire (0.637R against that stop) --
  an earlier draft said it did, by comparing raw ATR against a threshold
  denominated in R, which silently assumed 1R = 1 ATR. Full result:
  `.planning/scalp-s12-mae-mfe.md`; reproduce with
  `research.analysis.s12_excursion_run`.
- **Derive the risk budget first**: ruin threshold (25-30%, not 50% —
  recovery is asymmetric) → acceptable risk of ruin (<1% institutional,
  >5% means reduce size) → risk per trade → quantity via stop distance.
  Reject any strategy that cannot live inside the budget rather than
  widening it. Both prior runs backtested first and looked at drawdown
  afterwards. **The risk-of-ruin calculation is only defined once its
  contract is pinned** — which closed form (the additive one for fixed
  dollar risk, the logarithmic one for equity-compounding risk; they
  differ, and using the additive form for compounding overstates
  survivable units), what counts as the ruin event (peak-to-trough
  drawdown, not loss from starting capital), the evaluation horizon,
  that returns are **net** of fees and slippage, how serial dependence is
  handled, and the confidence level. Both closed forms assume i.i.d.
  trades with a fixed payoff ratio; where payoffs vary or trades are
  dependent — true here — the closed form is a first screen and the real
  number comes from Monte Carlo over the actual trade distribution. See
  S8 §3.6.
- **Add Sortino, Calmar, expectancy, MAE/MFE, MFE capture rate,
  turnover, order rate, and risk of ruin** to the metrics already in use.
  **Turnover and order rate are two different metrics, not one under two
  names**: turnover is traded notional (or absolute position change) over
  capital, an *exposure* measure; order rate is orders/trades per unit
  time, a *runaway-loop* measure. Each needs its own threshold, and
  **neither threshold is defined yet** — doing so requires naming what is
  counted (orders or filled trades), the measurement window, the limit,
  and the fail-closed action on breach. Stated precisely rather than
  overclaimed: the prior failures ran ~70 and ~89 trades per day, which
  is the kind of figure an order-rate metric exists to surface for
  **investigation**; it does not by itself breach the practitioner
  heuristic often quoted as ~100 trades/day, and an earlier version of
  this section wrongly implied it did. MAE/MFE also
  needs a pinned calculation contract (measurement starts at the fill
  bar, net of costs, stop-wins on a same-bar tie, planned-risk R
  denominator, forced closes flagged as censored) so the same trades
  cannot yield different stop boundaries — S8 §3.7.
- **Structure a candidate as Condition / Setup / Trigger /
  Invalidation (CSTI)**, and sequence the risk decision as **stop first,
  then reward-to-risk against a structural target, then qualify, then
  size**. A trade whose structure offers poor odds is *declined*, never
  resized into. Added after the 2026-08-28 practitioner-methodology
  research and first implemented in S14's `selective-reversion`, where
  the R:R gate worked as designed even though the underlying signal did
  not. Two corollaries that research made concrete: a stop belongs at a
  level that **invalidates the thesis**, not at a round distance or a
  convenient ATR multiple; and **asymmetry substitutes for win rate** —
  40% at 3:1 is profitable, 60% at 0.5:1 loses — so a win-rate figure
  quoted without its payoff ratio says nothing. Floor 2:1, prefer 3:1.
  **Check the coupling before tuning either side**: S14 found that
  widening the stop mechanically fails an R:R gate whose denominator is
  that same stop distance, silently turning "safer stop" into "stop
  trading".
- **Judge stability by year, not only in aggregate.** Report the number
  of positive years alongside the pooled statistic, and treat a result
  whose edge is concentrated in one regime as unconfirmed until it is
  shown outside that regime — S13's own candidate drew ~60% of its edge
  from 2021 and was negative in the most recent year. Where the sample
  supports it, prefer worst-regime performance (the "Minimum Regime
  Performance" framing, Alexander & Fabozzi 2026) over the mean as the
  figure a sizing decision is made against, and bootstrap the year-level
  spread rather than quoting a single pooled standard error.
- **Deduplicate overlapping windows before reporting any significance
  figure** — see the fourth error shape recorded under Task S13/S14 above.
  This is a hard requirement, not a preference: it cost this arc a
  threefold-inflated t-statistic and a retracted conclusion.
- **Use a research split, not another single-shot holdout.** The two
  spent 1m windows become research data (they cannot be clean holdouts
  again); Binance **spot** 1m stays reserved and untouched. Search
  freely, count every trial, deflate with DSR — that is what the
  machinery is for. Avoiding search to keep `N` low avoided the penalty
  and also avoided all learning.

**Live-side controls S8 names as prerequisites — these are hard gates,
not a wishlist.** All are currently missing or unverified, and they are
distinct from backtest cost assumptions. Each is an additional required
item alongside the Live Entry Criteria's existing "market-order guard
enabled" and "kill switch verified" lines, and **all must be implemented
and verified before live activation, and before `GUARDED_MARKET` is used
against any real account. Absent or unverified means fail closed — no
live order.** Every live order still passes through the Java Trading
Plane and the Java Risk Gateway in full, per the Non-negotiable Rules;
nothing here creates an exception to that.

1. **Wire-level price guard.** `GUARDED_MARKET` currently maps to a
   plain `"MARKET"` order with no price cap on both `BingXAdapter` and
   `KisAdapter` — the guard is a name only today.
2. **Stale-data check.** `PriceFeed#latestPrice` returns a bare value
   with no timestamp (interface-level, so every venue is affected).
   Acting on a stale price creates risk rather than managing it.
3. **Turnover ceiling** and **order-rate anomaly trigger** — separate
   thresholds on separate metrics, per the distinction above.
4. **Drawdown circuit breakers** at daily/weekly/total resolution that
   halt automatically and require **manual review before restart**.
5. **Pre-trade checks run in full without short-circuiting**, so the
   audit log records every failure rather than only the first.

A bounded exception exists for measuring real slippage (S8 Part 4 item
1): a **BingX VST demo** fill experiment against virtual funds, through
the full `OrderIntent → OrderPipeline → RiskGateway → Order →
ExchangeOrderExecutor → BingXAdapter` path, with its own human approval
and an explicit acceptance of which of the above are still absent. It
authorises nothing on a production endpoint and relaxes no Live Entry
Criterion.

**Task S13 (2026-08-28) swept the holding period, then swept selectivity
— and the second sweep overturned the first's conclusion.** Full account:
`.planning/scalp-s13-horizon-sweep-and-closeout.md`.

**Holding period does not help.** On the S12 entry rule, gross mean
outcome peaks at one hour (+0.95bps) and inverts at two (−0.05), reaching
−5.67bps at eight hours — exactly what S11's mean-reverting ICs predict:
hold past the reversion window and the edge is spent. Win rate rises
monotonically 37.3% → 47.6% while expectancy falls and turns negative,
which is why S8 put expectancy on the metric list rather than trusting
win rate.

**Selectivity does.** That S12 entry fired **57.2 times per day**. The
operator pointed out this was still "dozens of trades a day" rather than
"once or twice, only on a genuinely big signal" — roughly 29x more
selective, and an operating point never tested. Sweeping it on the
identical signals:

Counting **non-overlapping** positions only (see the retraction below):

| \|z\| ≥ | Activity | Indep. trades/day | Gross mean | vs 12bps | t |
|---|---|---|---|---|---|
| 2.0 | top 10% | 4.54 | −0.84bps | ✗ | −0.98 |
| 4.0 | top 0.1% | 0.30 | +7.81bps | ✗ | 1.50 |
| 5.0 | top 1% | 0.21 | +13.97bps | ✓ | 1.95 |
| 6.0 | top 1% | 0.09 | +30.87bps | ✓ | 2.56 |
| 6.0 | top 0.1% | 0.08 | +45.83bps | ✓ | 2.90 |

Gross outcome rises monotonically with selectivity and crosses the cost
line at the top end. Trend-**following** returns exactly the negative at
every cell, so mean reversion remains the correct sign. But a
Bonferroni-corrected threshold for this 15-cell search is |t| ≈ 2.94,
which **nothing reaches**.

**Not an edge, and S14 established that with a second, larger problem
than the one S13 itself found.** S13 reported the result as
concentrated in 2021 (~60% of the edge from ~10% of the trades in every
promising cell, and 2026 negative in all three) but statistically strong
at t = 7.0-8.0. **That t-statistic is retracted.** It was computed over
*overlapping* 60-minute windows counted as independent observations;
extreme readings cluster, so one event contributed many correlated
samples. Deduplicating to non-overlapping positions collapses t to
**1.50 / 1.95 / 2.56** and cuts the mean 25-60% (+19.31 -> +7.81, +25.10
-> +13.97, +41.30 -> +30.87 bps), so the duplicates were also the better
observations. Against a 15-cell search, a Bonferroni threshold is
|t| ~= 2.94; nothing reaches it, `|z|≥4` no longer clears the 12bps cost
at all, and the `|z|≥2` cell flips negative. What survives is only that
gross outcome rises monotonically with selectivity and that the sign
(mean reversion) is right.

**The error pattern this exposed, recorded so it stops recurring**: three
of this arc's largest errors share one shape — a result from a single
arbitrary configuration generalised to a whole domain (costs vs. the
*unconditional* move distribution → "minutes-scale impossible"; a
*directional* hypothesis tested by *magnitude* → "levels carry nothing";
one operating point at 57 trades/day → "scalping cannot clear costs").
All three were false and none was caught by reasoning alone. **Never
conclude about a domain from one parameter setting — sweep it first.**

**A fourth, different shape, added by S14 and binding on any future
measurement here**: *a statistic computed over overlapping windows is not
a statistic over independent observations.* S13's t = 7.0-8.0 came from
overlapping 60-minute excursions treated as independent, and correcting
it cut t roughly threefold. The tooling to avoid this already existed in
this repo — `research/ic.py` enforces non-overlapping sampling and S11
documented why — and was simply not applied to the excursion sweep.
**Building the right tool does not protect you if the next analysis does
not use it**: any overlapping-window measurement must either deduplicate
to non-overlapping samples before reporting a t-statistic, p-value or
standard error, or state explicitly that its significance figures are
not corrected for overlap.

**Task S14 (2026-08-28) built the candidate, ran it through the real
walk-forward + Eligibility Bar machinery for the first time in this
arc, and it was REJECTED.** Full account:
`.planning/scalp-s14-selective-reversion.md`.

`selective-reversion` is the first strategy in this project structured
as **Condition / Setup / Trigger / Invalidation** — the practitioner
structure S8 called for and which S4's and S6's candidates both lacked,
having fused direction, entry/exit and sizing into one threshold rule.
Condition: S10's absolute-ATR activity rank ≥ 0.99. Setup: S11's two
orthogonal features (`z(htf_ret_4h) + z(taker_buy_share)`). Trigger:
|score| ≥ 5, faded. Invalidation: a **2.65 ATR stop taken from S12's
measured winners' MAE p80**, not a convention. Plus an **R:R
qualification gate** applied in the practitioner sequence — stop first,
then reward against a structural target, then qualify, then size; a
trade whose structure offers poor odds is declined outright rather than
resized.

83 folds (30d train / 30d validate / 30d step on 3,661,780 Binance
futures 1m bars), **721 trades — the first scalping run in this project
to clear the trade-count floor, so this is a conclusive REJECTED rather
than another INCONCLUSIVE**. Mean fold Sharpe **−1.471**, 36.1% of folds
positive, DSR 0.000 against N = 123, profit factor 1.248 vs the 1.3
floor. Drawdown (12.44%) and trade count passed; everything else failed.

**Two findings from the diagnosis are reusable and outlive the
candidate**:

1. **The stop is what made it badly negative, and that is a property of
   mean reversion rather than a defect.** Removing it moves the full-window
   result from −134% to −0.20%: the edge lives precisely in the adverse
   excursion the stop cuts off. S12 had already observed the mechanism
   ("winners digging 1.86 ATR on average says the entry is early")
   without drawing the conclusion. A systematically-early mean-reversion
   entry needs a later entry or a risk control that is not a fixed
   adverse-excursion stop — a real design question, not a tuning knob.
2. **A wider stop mechanically fails the R:R gate**, because the gate's
   denominator *is* the stop distance — at 8 ATR it declines 2,831 of
   2,869 setups. "Widen the stop for safety" silently becomes "stop
   trading". A real interaction between two independently sensible rules,
   worth knowing before either is tuned.

The CSTI structure and the R:R gate are **not** implicated and should be
reused; the signal underneath them was not there.

**Task S15 (2026-08-28) measured all three remedies S14's diagnosis
named. Two were wrong, one is real infrastructure, and the candidate is
still REJECTED.** Full account:
`.planning/scalp-s15-entry-risk-and-sizing.md`.

- **(a) Enter later — no.** Delaying entry lowers the adverse excursion
  exactly as predicted (mean MAE 4.65 → 2.23 ATR at +60 bars) and takes
  the outcome with it (+30.87 → +18.92 → +7.55bps, then wandering near
  zero: −2.18 at +30 bars, +3.93 at +60). The decline is **not**
  monotonic past the first few steps — that reads as a spent edge plus
  sampling noise — so the claim that survives is the weaker and
  sufficient one: **every delay tested is worse than immediate entry, on
  gross, on net and on t, and none recovers.** **The adverse excursion
  is not a cost paid before the edge; it is the edge.** A confirmation
  entry (wait for a 25-50% retrace of the adverse move) keeps ~76-79% of
  the gross and is still strictly worse than immediate.
- **(b) A better stop — no, and the obvious diagnosis was false.** S14's
  2.65 ATR came from S12's p80 measured on a ~29x less selective
  population, so "wrong population" looked right; re-running
  `recommend_stop` on the correct population gives **2.71/3.36 ATR**,
  essentially the same. (What differs is the *mean* MAE, 4.65 ATR, which
  is dominated by losers — median loser MAE 5.0-5.4 against median
  winner MAE 1.0-1.2. Conflating the mean with the p80 is what made the
  hypothesis plausible.) The real question is different: **at every
  width from 1.5 to 12 ATR, in both cells, the stop realises a larger
  loss than the position it catches would have taken on its own.** It
  manufactures losses rather than avoiding them, and "no stop" beats
  every width. The "cuts 83% of losers" figure that makes a stop look
  good is measuring the wrong thing — those positions were mostly
  heading for a *small* loss.
- **(c) Equity-aware sizing — built, and it closes a real gap.**
  `backtest.engine.EquityObserver` is an optional, duck-typed protocol:
  `run_backtest` already reconstructs mark-to-market equity every bar
  for its S7 insolvency floor, and now hands that value to a strategy
  that asks, *before* the strategy is called and built only from fills
  at or before the bar's open (so look-ahead safety is structural). This
  closes the half S7 explicitly left open — `compute_position_size`
  sizing against a **fixed** `reference_equity` is the mechanism behind
  S6's −239,161% run. A strategy in `compounding` mode **fails closed**:
  given no equity it refuses to trade rather than silently reverting to
  the constant while reporting itself as compounding.

**The combined result**, 83 folds, same geometry and costs as S14:

| | S14 (2.65 ATR stop) | no stop + compounding | no stop + fixed |
|---|---|---|---|
| mean fold Sharpe | −1.4709 | **−0.3326** | −0.2954 |
| folds Sharpe > 0 | 36.1% | 45.8% | 45.8% |
| mean profit factor | 1.248 ✗ | 2.513 ✓ | 2.553 ✓ |
| worst drawdown | 12.44% | 22.55% | 23.09% |
| verdict | REJECTED | **REJECTED** | REJECTED |

Removing the stop is worth **4.4x on mean Sharpe** — the largest single
improvement in this arc — and flips profit factor from fail to pass. It
is still not an edge. Compounding sizing is ~neutral here, which is the
expected result for a strategy that never compounds far in either
direction and is evidence that sizing was not what was wrong.

**Two of those three remedies moved the result the right way and the
third was neutral — but the sentence that followed here was wrong and is
retracted. See Task S16 immediately below.** `N` after S15 was **125**.

**A scoring weakness this run exposed, and a reporting fix rather than a
gate change.** The no-stop cell clears the profit-factor floor on a
**mean of 2.55 while its median fold is 1.18**; S14's mean was 1.25
against a median of 0.91. A profit factor is a ratio of two non-negative
magnitudes, so one fold with almost no losing trades can drag the mean
across the floor by itself. CLAUDE.md sets the floor without naming
which statistic it applies to and `walkforward` aggregates the mean, so
the mean remains what is scored — changing that is a gate change needing
its own approval. `s14_eligibility.py` now prints the median beside it
and flags **FRAGILE** whenever the mean passes and the median does not.

**Task S16 (2026-08-28) audited S15's own conclusion, at the operator's
request, because it was not trusted — and found three real defects in
it.** The REJECTED verdict survives; S15's stated *reason* for it does
not. Full account: `.planning/scalp-s16-audit-of-s15.md`.

1. **The conclusion was drawn from the weaker of the two surviving
   cells.** S13 left `|z|≥5` (+13.97bps, t=1.95) and `|z|≥6` (+30.87bps,
   t=2.56) standing. **Every walk-forward S14 and S15 ran used
   `entry_z=5.0`**; the `|z|≥6` cell was never tested, and "the signal is
   not there" was declared anyway. This is the **fifth** instance of the
   error pattern this section already records as a rule, committed at the
   most expensive stage to commit it. Running the missing cells reverses
   the sign: `|z|≥6, top 1%` gives mean fold Sharpe **+0.456** and
   **+13.97% compounded**; `top 0.1%` gives **+0.899** and **+32.42%**,
   against `|z|≥5`'s −0.333 and −50.6%.
2. **Fold-based criteria were applied to a strategy holding ~2 trades per
   fold.** At `|z|≥6` the median fold holds 2 trades, at `|z|≥5` six. A
   fold's sign is then near a coin flip, and the 80% floor is not merely
   hard but unreachable: a strategy whose folds are positive 60% of the
   time (a *good* edge) clears 67-of-83 with probability **4.6e-05**.
   `sr-j` already made this argument for fold *counts*; it had not been
   made for trades *per fold*. **Below roughly 20-30 trades per fold,
   fold consistency and the sign test are uninformative in both
   directions and must not be reported as evidence either way.**
3. **The DSR input was wrong, in the strict direction.**
   `s14_eligibility.py` fed `trial_sharpe_variance` its own run's
   **per-fold** Sharpes; the benchmark wants the variance across the
   other **trials**, each itself an average over folds. This inflated the
   selection benchmark and pushed every DSR that scorer reported toward
   zero. The scorer now **delegates DSR/PSR/trial counts to
   `research/retrospective.py`** outright rather than keeping a second
   implementation, and matches it exactly.

**What the corrected evaluation says about the best configuration**
(`|z|≥6`, top 0.1%, no stop, compounding sizing): mean-Sharpe t-test
**t = +2.388, p = 0.0098 — PASS, the first significance test any
scalping candidate here has cleared**; drawdown 9.93%, 181 trades and
profit factor 6.44 all pass; PSR **0.9905**; observed Sharpe **+0.899**
against the window's own **0.623** detection floor, so the window *is*
powered for it. Six of eight years positive, and **not**
2021-dependent (+26.12% of the +32.42% survives excluding 2021) — which
contradicts S13's concentration warning, because S13 measured mean bps
per position where 2021's violent moves dominate, while the walk-forward
compounds equity under ATR-inverse sizing that gives those same moves a
*small* position. **Risk-based sizing neutralised the regime
concentration.**

**And it still fails, for the one reason that now governs everything
here: DSR = 6.46e-11 against N = 127.** Inverting the benchmark gives the
annualized Sharpe a result must post to clear DSR 0.95:

| N | required Sharpe |
|---|---|
| 1 (a pre-registered holdout) | **0.63** |
| 5 (this family) | 2.17 |
| 50 | 3.56 |
| **127 (this project today)** | **4.00** |

Credible institutional trend-following reports 0.4-0.8. **At N = 127 no
realistic edge can clear this bar on this data, whatever it is.** This is
the same arithmetic that closed the 1h window (Configuration C needed
4.6), and the standing rule written there now applies verbatim to the
**Binance futures 1m window**: it stays open for *reproduction,
diagnosis and infrastructure testing*, and is **closed to selection**,
because raising `N` can only lower the DSR of any future result there.

**The one number that changes the sequencing**: at `N = 1` the
requirement is **0.63**, and the observed research-window Sharpe is
**0.899**. Binance **spot** 1m has never been touched and is not in the
local store; a single pre-registered confirmation there faces `N = 1` by
construction.

**But `N = 1` removes the selection penalty, not the replication
question, and this must not be policy-ised into "one holdout confirms a
strategy."** A lone clean result is one draw. This project's own
precedent settles it: `daily-tsmom-ensemble` got **two** disjoint
pre-registered confirmations plus a combined-significance meta-analysis
and the verdict was still INCONCLUSIVE. Options — none chosen here, each
needing its own `Discuss` and a pre-registration committed before any
data is fetched: define a **research split on an unsearched window**
first (the ordinary discipline, and it does not spend a one-shot
resource); find an **independent replication source**; or spend the
spot-1m holdout, accepting it can at best say "not contradicted here".
Spot-vs-perpetual microstructure differences (fee schedule, no funding,
different participants) make that last one a *replication attempt*
rather than a continuation. Nothing here affects
`daily-tsmom-ensemble`, which trades at a frequency where a 12bps round
trip is negligible and remains in paper trading under its own approved
exception.

**Out of scope this phase**: tick/trade-level data, true HFT,
co-location; promoting any scalping strategy to paper trading (the
Eligibility Bar and human-approval discipline apply in full first);
rebuilding `fill.py`'s fill model with order-book depth and
partial-fill/queue-position awareness (a disclosed possible follow-up,
never committed to).

### Trade Management Tasks A-C — a position model that can express trader-style management, and the first candidate built on it, REJECTED

Opened 2026-08-28 at the operator's request, after sustained and correct
pushback that this project kept validating *mechanical threshold rules*
while they were describing something else entirely:

> Enter long on the trend. It rises. Weakness appears but a bounce still
> looks possible, so add a short hedge there. When it drops, close **only
> the short** for profit and keep the core.

**The framework could not express that, and that was the real finding.**
Every strategy in this project held a single net position, so "close
only the short" had no representation at all — not a missing feature, a
missing vocabulary. Records: `.planning/tm-a-trader-style-position-model
.md`, `-b-signal-and-data-catalogue.md`, `-c-confluence-hedge-
specification.md` and its `-result.md`.

**Task A built the vocabulary.** `metrics.book` (`Leg`, `LegPurpose`,
`Book`) plus `research.strategies.leg_manager`. Three conventions,
decided once and binding:

- **Legs close by explicit id**, never FIFO/LIFO — the whole point is
  closing *that* leg and leaving the rest.
- **Exposure is reported gross as well as net**, because margin is not
  netted between a long and a short in hedge mode.
- **`Book.reconcile` fails closed** against the venue's own per-side
  totals, since **both Binance and Bybit track exactly one position per
  direction**. Multi-leg is client-side bookkeeping over a
  single-position-per-side venue reality, and the liquidation price is
  computed by the exchange on the netted whole. Any leg abstraction that
  forgets this is lying about risk.

`backtest.engine`'s `Strategy` type now additionally accepts a
**sequence** of intents, because a leg-scoped strategy routinely acts
twice on one bar and splitting that across two bars would change the
prices it acts at. `OrderIntent` itself is deliberately unchanged — it
is a tested cross-language wire schema, and which leg an order belongs
to is strategy-side bookkeeping the venue has no concept of.

**Task B catalogued what the exchange actually offers**, with a third
column recording *what this project had measured*. The answer:
**two of five signal families** (price, flow — never positioning, depth,
or forced-flow). `python/data/binance_positioning.py` now collects open
interest and long/short ratios on cron, because that data **cannot be
backfilled** — it exists only from the moment collection starts.

**Task C was pre-registered before implementation and REJECTED.** A
four-family conjunction gating a tactical hedge over the unchanged
`daily-tsmom-ensemble` core. Daily: the conjunction fired **twice** in
2,544 bars → INCONCLUSIVE-DATA-LIMITED, the outcome the specification
had named in advance as most likely. Hourly (the identical
specification, calendar constants rescaled, **no threshold touched**):
166 conjunctions, 150 hedges, and

| | trades | return | max DD | PF | Sharpe |
|---|---|---|---|---|---|
| core alone | 453 | +173.0% | 18.80% | 3.18 | +0.916 |
| core + hedge | 452 | +168.5% | 18.80% | 3.17 | +0.902 |

Tactical gross edge **−97** against **+353** in fees it caused, t=−0.62,
p=0.535. Two independent failures: not distinguishable from zero, and
negative before the fees even arrive.

**Four findings outlive the candidate:**

1. **The conjunction is real; what it selects for is not tradeable at
   these thresholds.** Four conditions firing 364/348/479/912 times
   individually and **2** times jointly is far below what correlated
   conditions would produce. The independence premise held. This is the
   one positive result.
2. **A parameter-free exit pinned the holding period to ~1 hour**
   (median hold 1 bar, longest 3). The exit rule "the setup conditions
   no longer all hold" is fragile by construction — any one of three
   relaxing ends the hedge. **So what was tested is not the hypothesis
   that was described**: a one-hour hedge cannot express a pullback
   trade. Avoiding a parameter is not free; here it silently chose the
   most important one.
3. **Third independent confirmation that reducing exposure during an
   adverse excursion loses money for this core.** S15 found it for a
   fixed stop, S17 for a measured one, and now a *selective,
   conjunction-gated* reduction behaves no differently from an
   unconditional one. This is the most transferable result here.
4. **A signal-time book is not an execution book, and reporting the
   first as the second flipped a sign.** `Book.realized_pnl` was computed
   from the prices the strategy *saw when deciding* (the signal bar's
   close); the real fill is the next bar's open with slippage.
   `leg_manager` had always documented that approximation as safe "since
   reported P&L comes from real fills" — **true until this task made the
   signal book a reported figure.** The gap came to −141, over 3x the
   edge being measured: the first write-up said +45, the real figure is
   −97. Fixed structurally — `LegAction` records what each intent meant
   and `replay_fills` rebuilds an execution book from real `Fill`s,
   keyed by `Fill.intent_id`. Also note `Book.realized_pnl` is **gross
   of fees**, `Fill.fee` being separate.

**Two infrastructure defects were found and fixed, both real:**

- **`check_disjoint_intervals`** added to `research/conclusion_check.py`.
  `check_non_overlapping` takes a single `hold_bars` and so can only ask
  whether starts clear the *longest* hold — sufficient but not
  necessary, and the wrong tool once `metrics.book` made
  variable-duration legs normal. The new form takes `(start, end)` pairs,
  blocks on a real overlap, and reports proximity via an optional
  `clustering_gap` as a **warning**: disjoint is not independent, and
  the honest handling is disclosure, since correcting for clustering can
  only shrink a t-statistic.
- **The test suite was appending to the real research log.**
  `runs/experiments.jsonl` carries two `test-ofi-momentum` records over
  20 synthetic bars, so the pre-Task-C `N` of 127 was **126 real trials
  plus one test artifact**. The error direction is safe (an
  inflated `N` only lowers DSR), so no past conclusion was wrongly
  passed and the append-only log is **not** rewritten — but anyone
  recomputing `N` should read 126. Fixed in `python/tests/conftest.py`:
  it wraps `experiment_log`'s **write functions** and redirects **any
  relative `runs_path`**, that being the property behind every incident
  (a relative path resolves against whatever directory pytest started
  in). An absolute path is always deliberate and honoured exactly.
  `test_conftest_isolation.py` (10 tests) asserts the fix works,
  including that no module bypasses the wrapper with a direct
  `from research.experiment_log import`.

  **Three false starts, and all three passed their own new tests** —
  what caught each was an external observable (a sha256 comparison, an
  `ls`), which is the transferable part: (a) patching
  `DEFAULT_RUNS_PATH` is **inert**, since defaults bind at `def` time;
  (b) patching callers does not scale — **25 sites** bind it, including
  every `Trainable.__init__`, and a version listing two of them leaked
  past immediately; (c) keying the rule to `runs/experiments.jsonl` was
  still too narrow, because `live/generate_daily_signal.py` writes the
  separately-committed `runs/live_signals.jsonl` through the same
  `log_run`. **A guard that silently does nothing is worse than no
  guard**: verify one against something outside its own code before
  trusting it.

**`N` after Task C is 129** — 128 real research selection trials plus
that one test artifact. Task C's two runs are logged under a new
`trade-management` family (`research/lineage.py`, cited to the
pre-registration); an earlier version of the runner called `run_backtest`
directly and logged **nothing**, which understates `N` and so runs in the
**unsafe** direction, since a smaller `N` inflates every DSR computed
against it. Both timeframes are counted, on the same reasoning CLAUDE.md
already applies to cost-disqualified runs: each was a real attempt to
find a viable configuration. This does not change the arithmetic that
governs — at `N` in the 120s the DSR-0.95 bar is still an annualized
Sharpe near **4.00**, against the 0.4-0.8 credible institutional range.

**The stopping rule forecloses adjusting a threshold and re-running.**
The permitted responses are to accept the result, or to wait for the
positioning data now accumulating and specify a *different* conjunction
using the families this candidate could not include. The holding-period
finding is a legitimate input to a future specification **provided that
specification is registered before it is run**, as this one was.

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
  `CLAUDE.md`, `.coderabbit.yaml`): **not a hard server-side gate — but
  `required_conversation_resolution` is, and that is what will actually
  stop a merge** (see two bullets down; learned on PR #135). GitHub's "Require review from Code Owners" does not
  block merging when the PR author is also the sole code owner — tested
  empirically with both `enforce_admins: false` and `true`; both merged
  instantly with no review, no queued/waiting state. Self-approval is
  blocked, but GitHub simply doesn't raise the requirement at all rather
  than blocking, since there is no one else who could satisfy it. This is
  a solo-author-repo limitation of GitHub CODEOWNERS, not a config mistake.
- **The real merge blocker is unresolved review conversations, not
  CODEOWNERS** (recorded 2026-08-31 after PR #135 sat `BLOCKED` for an
  hour while this was misdiagnosed). Branch protection has
  `required_conversation_resolution: true`, and CodeRabbit opens a thread
  per finding. A PR can therefore show **every required check green, no
  `CHANGES_REQUESTED`, `mergeable: MERGEABLE`** and still report
  `mergeStateStatus: BLOCKED`, with the reason appearing neither in the
  UI nor in `gh pr checks`.

  Diagnose it directly rather than guessing at CODEOWNERS or reaching for
  `--admin` — query `reviewThreads` via GraphQL and count the ones with
  `isResolved: false`.

  **Resolve a thread only after its finding is actually addressed or
  answered.** Resolving *is* the record that it was dealt with, so
  clearing threads to get a green light falsifies that record. On PR #135
  two of the five open threads were real, unfixed bugs — a partially
  closed leg vanishing from the execution book, and a paginator stepping
  over an empty page and so making a transient failure permanent on a
  non-backfillable series. Every review **summary** had stopped
  mentioning both; the threads were the only place they still showed.

  A second, separate mechanism: **CodeRabbit submits a review object only
  when it has comments.** A clean re-review updates the check status but
  leaves an earlier `CHANGES_REQUESTED` standing, which blocks on its
  own. Dismiss those explicitly
  (`PUT /pulls/N/reviews/ID/dismissals`, `event=DISMISS`) with a message
  naming the commit that addressed them — that satisfies the gate, where
  `--admin` would bypass it.

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
  ask when (a) the change has cost/subscription implications, or (b) the
  task requires @ckrhehfl to do something only they can do (a GitHub UI
  setting, an account credential, entering a password).

- **A rate-limited CodeRabbit is a wait, not a question** (revised
  2026-08-31, human-instructed, replacing "still stop and ask when
  CodeRabbit is rate-limited/unusable rather than passing"). Rate
  limiting is a scheduling fact with a published ETA attached, not a
  judgment call, and handing it to the human converts a five-minute wait
  into an unbounded one. The agent waits it out itself:

  1. Comment `@coderabbitai rate limit` — that query does not itself
     consume a review — and read the exact ETA out of the reply.
  2. **Compute the wake time from the reply's own `created_at`**, not
     from when the ETA was read. The two differ by however long the
     round trip took, and sleeping the stated minutes from the wrong
     origin lands early and burns the retry.
  3. Sleep until then plus a small margin, then comment
     **`@coderabbitai full review`** once. Not `@coderabbitai review` —
     that one is rejected unless automatic reviews are *paused*, and a
     commit pushed while rate-limited is never picked up automatically
     afterwards, so without this the PR sits with no completed review and
     cannot pass the merge gate.
  4. Batch every outstanding fix into one push *before* requesting, per
     the Rate limits section below — re-requesting per fix is what
     causes this in the first place.

  **A second rate limit carrying its own ETA is still a wait, not an
  escalation.** Observed on PR #135: three review rounds in one session
  hit the limit twice, at 28 and then 42 minutes, and both cleared
  exactly when stated. The allowance is a rolling 7-day window over
  review *attempts*, so an active branch will hit it repeatedly and the
  waits lengthen — that is the policy working as designed, not a fault.
  Loop: read the ETA, sleep, retry, and only stop when the signal stops
  being a schedule.

  Escalate to the human only when it is **no longer a wait**: the reply
  carries no ETA at all, CodeRabbit reports an error rather than a limit,
  the quoted wait is long enough to be a real scheduling decision rather
  than a pause (roughly a few hours or more), or no review appears well
  past a stated ETA.

  The merge gate itself is unchanged — a **completed** review is still
  required, and waiting for one is never the same as proceeding without
  one.

  Three mechanical details, each learned by getting it wrong here:

  - **`@coderabbitai review` is rejected unless automatic reviews are
    *paused*.** It is not the retry command.
  - **A push made *while* rate-limited is not retried when the limit
    clears.** CodeRabbit has already processed and declined that commit,
    so waiting produces nothing. The command that works is
    **`@coderabbitai full review`**, which answers "Full review
    triggered" and re-examines the whole PR — expect more findings than
    an incremental pass, since it looks at everything rather than the
    latest diff.
  - **The ETA in `gh pr checks`' summary column goes stale** and will
    keep reading "Review rate limited" long after the bot itself says
    "available now". Always read status from the bot's newest comment.

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
