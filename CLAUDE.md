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

No strategy exists in this project yet — these are principles for when
one is built, written now because retrofitting research rigor onto a
strategy already believed "validated" isn't realistic once research is
underway. This is not itself an Implementation Priority item; it's a
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

### Strategy Research Operational Design (2026-07-25)

Real strategy research is starting, so the mechanics the paragraph above
deliberately left open now need to be fixed. Written before any of it is
built — see `.planning/README.md`'s "Where does a design belong" for why
it lives here in full rather than as a summary; once the corresponding
`.planning/sr-*.md` files exist, this section should be trimmed to a
short pointer.

**Data pipeline** (`python/data/`): BingX historical klines (`GET
/openApi/swap/v3/quote/klines`), paginated (1000-candle cap, half-open
range aligned to the 900,000ms/15m grid, actual returned count always
verified rather than trusting `limit`), cached locally in SQLite
(`python/data/store.py`) keyed on `(symbol, interval, open_time_ms)` —
`INSERT OR IGNORE` gives resumable/idempotent fetches for free. Decimals
stored as exact text, not floats (same reasoning as `schemas/_types.py`'s
`PositiveDecimalString`). Zero new dependencies — stdlib
`sqlite3`/`urllib`/`json` (with `parse_float=Decimal` to avoid a float
round-trip at parse time) suffice at this data scale (single symbol, a
few years of 15m bars is a few MB); numpy/pandas deferred until a real
(non-placeholder) strategy's feature engineering actually needs
vectorized ops, not added speculatively now. BingX's actual historical
retention depth for this contract is unverified — the backfill CLI
(`python/data/backfill.py`) doubles as the discovery mechanism (walks
back from now until it hits an empty response) rather than assuming a
number.

**Lookahead-safety performance fix** (`python/backtest/kline_window.py`):
`engine.py`'s `klines[:i+1]` is a full copy every bar (O(n²) over a full
run) — replaced with a `KlineWindow(klines, length)` view class (O(1)
construction, bounds-checked access) that preserves the exact same
structural guarantee (a strategy cannot index or iterate past the
current bar) without copying. `Strategy`'s signature widens from
`Callable[[list[Kline]], ...]` to `Callable[[Sequence[Kline]], ...]` —
behaviorally identical for every existing caller.

**Portfolio/metrics layer** (new `python/metrics/`, not inside
`python/backtest/` — `backtest/` stays scoped to fill simulation only,
per its own docstring): reconstructs a single-symbol net position from
`(OrderIntent, Fill)` pairs (`BacktestResult` gains an additive
`filled_intents: list[OrderIntent]` field, index-aligned with `fills`,
since `Fill` itself has no `side`), tracks a mark-to-market equity
curve, and computes total return, Sharpe ratio (simple per-bar returns,
sample stdev, annualized via `sqrt(96 * 365)` for 15m bars, 0%
risk-free rate), max drawdown, win rate, trade count, and profit
factor — trade-level (full 0→nonzero→0 position lifecycles), not
fill-level. **Known gap, not solved by this design**: perpetual
funding-rate P&L is not modeled anywhere in this pipeline — flagged so
it isn't silently forgotten, not addressed now.

Degenerate-input handling, fixed here so it doesn't vary by
implementation: a fold with zero closed trades reports `sharpe_ratio:
null`, `win_rate: null`, `profit_factor: null` (not zero — a flat,
trade-less fold is "no evidence," not "bad evidence") and fails the
eligibility bar's per-fold-positive-Sharpe requirement by definition;
zero-variance per-bar returns (Sharpe's denominator) likewise yield
`sharpe_ratio: null`, never a division-by-zero crash or an inflated
value; a fold with only winning trades and zero losing trades yields
`profit_factor: null` (already specified above), not `inf`. Any
position still open at a fold's final bar is force-closed at that bar's
close price for P&L/trade-count/return purposes — marks it realized so
partial-period exposure isn't silently dropped from the metrics, at the
cost of not reflecting what would actually happen if the position ran
past the fold boundary (an accepted simplification for fold-scoring
purposes, not a live-accounting rule).

**Walk-forward harness** (`python/research/walkforward.py`): rolling
(fixed-size sliding, not expanding) train/validate folds, non-overlapping
by default (step = validate length). A `TrainableStrategy.fit(train_klines,
params) -> Strategy` protocol, evaluated by running the *existing*
`run_backtest` against `validate_klines` — deliberately not a monolithic
`fit_and_evaluate`, so every strategy (rule-based now, ML later) is
scored through the one code path already proven lookahead-safe, rather
than allowing a strategy-specific evaluation loop to bypass it.
Provisional default windows (not empirically tuned to this asset yet,
defined in bars — the canonical unit — with the calendar-month figures
given only as an approximate description, since 8,640/2,880 bars don't
correspond to any actual calendar-month boundary): train = 8,640 bars
(~90 days), validate = 2,880 bars (~30 days), step = validate (2,880
bars). Fold boundaries are computed purely by bar-count arithmetic
against the ordered kline sequence, never by calendar-date splitting.
Revisit once the data pipeline's real BingX depth number is known — if
depth is thin, shrink windows for more folds or accept fewer folds and
weight the eligibility bar (below) more conservatively.

**Holdout-split mechanics** (`python/research/holdout.py` +
`configs/research/holdout.json`): a git-tracked cutoff timestamp in its
own config file (not a buried constant — changes are visible in `git
log -p` on one small file). Two differently-named loaders:
`load_research_klines()` (default path, silently clamps to before the
cutoff, logs when it does) vs. `load_holdout_klines(...,
i_understand_this_is_holdout_data=True)` (loud, explicit). This second
loader **enforces** single access per `strategy_id`, not just logs it —
and derives that enforcement directly from `runs/experiments.jsonl`
itself, not a separate persisted claim table: before proceeding, it
scans the log for an existing `holdout_access` record for this
`strategy_id`; if one exists, it raises immediately instead of silently
succeeding; otherwise it proceeds and appends its own `holdout_access`
record before returning data. One store, not two, means claim state and
audit trail can never diverge by construction — there's nothing for a
second table to get out of sync with. This enforcement's soundness
depends entirely on the single-writer assumption in the Durability
paragraph below: under genuine concurrent calls for the same
`strategy_id` (not realistic for solo, sequential research — the
scenario this design targets), the scan-then-append isn't atomic and a
race could let two calls both see "unclaimed." Not mitigated now (e.g.
via a lock file held for the scan+append duration) since nothing in
this project's actual usage pattern exercises that race; add it if that
assumption ever stops holding. A genuinely legitimate re-run (e.g. a
metrics-bug fix discovered after the first holdout run) requires a
separately-named, explicit override (`force_reclaim=True`) that itself
gets its own loud log entry — friction on the rare path, not silent
tolerance either way.

**Experiment-tracking format**: one append-only file,
`runs/experiments.jsonl` (already anticipated — `.gitignore` has had
`logs/`/`runs/` entries since before this design existed). One
`record_type: "backtest_run"` entry per `run_walk_forward` call —
written automatically as that function's last action, not a separate
manual step — capturing `strategy_id`/`strategy_version` (id = family,
version = logic changes; params capture parameter changes separately),
full params, per-fold and aggregate metrics, data range, `code_version`
(git HEAD sha, captured automatically), and `is_holdout_run`.
`record_type: "holdout_access"` entries interleave in the same file for
the audit trail described above.

Durability and its real limits, stated plainly rather than overclaimed:
each `log_run`/holdout-access write is one `write()` call of a single
complete JSON object plus `\n`, followed by an explicit `flush()` +
`os.fsync()` before the call returns — so a call that returns
successfully (including a holdout claim) is actually durable on disk,
not just buffered, before its caller proceeds. What this does **not**
provide: safe interleaving of writes from multiple concurrent
processes. The actual mitigation for that is a **single-writer
assumption** (one research process appending to this file at a time),
not file locking — nothing in this project's real usage pattern needs
concurrent writers yet. Add real locking (e.g. `flock` on a sentinel
file held for the write's duration) if that assumption is ever
violated; don't assume today's single-`write()`-call approach already
covers concurrent writers, because it doesn't. A write interrupted
mid-line (crash/kill) leaves at most one truncated trailing line; any
reader of this file (including the holdout claim-scan above) must skip
a final line that fails to parse as JSON rather than fail loudly — the
record it would have described never completed and has no other side
effects to reconcile. `runs/` is gitignored by design (raw research
iteration, not a reviewed artifact) and is not currently backed up
anywhere — an accepted gap, not solved here; revisit with a periodic
copy to a backed-up location if losing local `runs/` history ever
actually happens.

**Backtest/Walk-Forward Eligibility Bar** (defaults — same status as
Risk Parameters: changing these needs explicit human approval; approved
as part of this design's 2026-07-25 sign-off): positive annualized
Sharpe in every fold (not just on average); minimum 8-10 folds for the
result to be considered credible (revisit once real data depth is
known); max drawdown ceiling 20-25% per-fold and aggregate; minimum 100
total trades across all folds (flagged tension: may unfairly penalize a
legitimately low-frequency strategy — apply judgment, don't treat as
absolute); profit factor floor 1.3-1.5 (cushion for backtest-to-live
slippage/fee mismodeling and the funding-rate gap above). A fold's
`profit_factor: null` is interpreted according to why it's null (both
cases already specified above): if null because the fold had zero
closed trades, that fold already fails eligibility via the
Sharpe/trade-count requirements regardless of profit factor; if null
because every closed trade won (zero losing trades), the profit-factor
floor is trivially satisfied — there's no evidence of a poor
risk/reward ratio to reject — and does not itself fail the fold. The
holdout confirmation run must clear the same bar (single-window
version) and must be the only holdout access on record for that
`strategy_id`.

**Build sequencing** (fresh-context subagent per task, GSD Execute
pattern): Task A (`python/data/`, independent, run for real against live
BingX immediately after merging to get the real depth number) → Task B
(`KlineWindow` + `BacktestResult.filled_intents` + `python/metrics/`,
independent of A, can run in parallel) → Task C (`python/research/` —
walkforward + holdout + experiment log; depends on B, benefits from A's
real depth number) → Task D (MA-crossover placeholder `TrainableStrategy`
whose `fit()` picks the best of a small grid via train-only
backtesting — **each candidate's train-only backtest is itself logged
as its own `backtest_run` entry**, not only the final winner's
validate-fold result, so the grid search doesn't quietly become exactly
the untracked-variation-count problem the logging rule exists to
prevent. Each candidate's entry carries `parent_run_id` (pointing at the
overall grid-search run), plus `candidate_index`/`total_candidates`, so
the full attempted-variation count is directly queryable from the log
rather than merely inferable. Then a real end-to-end run against real
BingX data producing real `runs/experiments.jsonl` entries; depends on
A+B+C — validates the pipeline, deliberately makes no edge claim, same
spirit as `DummySignalSource`).

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
