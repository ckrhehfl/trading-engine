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

   **New disclosed cost of adopting DSR as a gate**: DSR requires an
   `N`, and `N` requires `research/lineage.py`'s curated family map to
   stay current. A new strategy family run without a `strategy_family=`
   argument and without a curated entry resolves to its own
   single-member family, which would understate `N`. `resolve_family`
   surfaces this as a visible note rather than silently, but gating on
   DSR makes keeping that map honest a real obligation rather than a
   diagnostic nicety. **Raised on review and deliberately not adopted
   here**: making `resolve_family` *fail closed* (refuse to produce a
   DSR at all for an unmapped `strategy_id`, rather than fall back to a
   single-member family) would harden this properly. It is a code change
   to `research/lineage.py` plus a new approval-gated rule, neither of
   which belongs in a documentation-only change — recorded as a named
   follow-up so the disclosed cost above is not mistaken for the last
   word on it.

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
test over trade outcomes has any power at all, and below which
per-trade statistics are dominated by their own estimation noise. The
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

What checking the trigger actually reads: condition (a) needs
`runs/experiments.jsonl` **plus** `research/lineage.py`'s curated family
map (family membership is not self-describing in the log for records
predating `sr-p`'s optional `strategy_family` key), and condition (b) is
`research/eligibility.py`. Both are mechanical; "computable by a script"
above means *no judgment call is required*, not *the JSONL alone
suffices*.

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
candidates against condition (a)'s ≥50); and no decision would change,
since every configuration is already rejected by five to eight orders of
magnitude.

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

### Strategy Attempts So Far (closed out 2026-07-29)

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

1. **The `1d` early-window holdout (`sr-t`).** ~2.95 years of data no
   trial in this project's history has touched, with a detection floor
   of **~0.96** — the only untouched window this project has whose
   floor sits below a plausible real edge. A strategy specification
   must be committed *before* that window is ever loaded.
2. **Multi-symbol expansion.** A meaningful share of the Sharpe
   reported by the institutional research benchmarked in `sr-g`
   plausibly comes from cross-symbol diversification a single-symbol
   design cannot access. A real architecture reconsideration (it
   touches the data pipeline's survivorship-bias handling, per Strategy
   Research Methodology) that deserves its own `Discuss` pass.
3. **Stop adding strategies and build the infrastructure instead**
   (Priorities #8-#10). Nothing about the paper-trading loop,
   supervision, or `ExchangeAdapter` work is blocked by the absence of
   a validated strategy — CLAUDE.md already says they can and should
   proceed on dummy signals.

**Retired**: the two funding-extremity follow-ups previously listed
here as live candidates (changing the edge-trigger rule; lowering
`entry_z_threshold`/`funding_zscore_lookback`). Both are more searching
on the spent 1h window — see the standing rule above.

## Tooling Stack

| Layer | Choice | Status |
|---|---|---|
| CLI foundation | ripgrep, gh, uv | as needed |
| Guardrails (hooks) | `dwarvesf/claude-guardrails` (Lite, global `~/.claude/settings.json`) | active now — brought forward from "when `.env` appears" because the repo is public |
| Guardrails (project-specific) | hook blocking live-flag activation and exchange live-order endpoints | not built yet — add before real exchange credentials appear |
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
