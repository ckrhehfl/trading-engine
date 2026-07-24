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
  push-protection/secret-scanning is configured (`enabled` per the repo
  API), but **empirically did not fire** across four independent tests
  (2026-07: two AWS-key-shaped strings, a valid PKCS#8 RSA key, a valid
  legacy PKCS#1 RSA key). No alert was ever created. Do not treat
  GitHub's server-side scanning as a working safety net until this is
  re-verified — the actual first line of defense right now is the local
  `dwarvesf/claude-guardrails` hook, which blocks known secret patterns
  before a commit/push tool call runs at all. A separate, account-level
  "push protection for users" GitHub setting may explain this and is
  worth @ckrhehfl checking manually in personal GitHub security settings
  — not checkable via API.

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

- Symbol: `BTC-USDT`
- Recent trades: `GET /openApi/swap/v2/quote/trades`
- 15m klines: `GET /openApi/swap/v3/quote/klines`, interval token `15m`
- Historical range: `startTime`/`endTime` are half-open
  (`startTime <= t < endTime`), must align to the 900,000ms (15m) grid, max
  span 1000 candles per request
- `limit` is not a reliable count guarantee — requests over 1000 are
  silently capped; verify actual returned count in code
- Only public, unauthenticated read endpoints have been verified. Private /
  account / order endpoints are unverified.

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

Deliberately not specified yet — design these once real strategy research
starts, not before: experiment-tracking tooling/format, walk-forward
window sizing, holdout-split mechanics. Specifying implementation
mechanics ahead of a first real strategy attempt risks the same
premature-process trap named in "Why this is more than a bare CLAUDE.md."

## Tooling Stack

| Layer | Choice | Status |
|---|---|---|
| CLI foundation | ripgrep, gh, uv | as needed |
| Guardrails (hooks) | `dwarvesf/claude-guardrails` (Lite, global `~/.claude/settings.json`) | active now — brought forward from "when `.env` appears" because the repo is public |
| Guardrails (project-specific) | hook blocking live-flag activation and exchange live-order endpoints | not built yet — add before real exchange credentials appear |
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
  boundary is enforced procedurally, not technically: whoever/whatever is
  operating this repo (including an LLM agent) must treat a CODEOWNERS-
  matched PR as requiring @ckrhehfl's explicit go-ahead before merging,
  and must not rely on GitHub to block it. Branch protection stays on
  regardless — it still stops a future bot/app identity or a second
  collaborator from merging those paths unreviewed, which is real
  protection, just not against the current sole operator.

## Implementation Priority

1. Shared schemas (exchange/asset-class-agnostic where practical)
2. Java OMS state machine skeleton
3. Java Risk Gateway skeleton
4. Python deterministic backtest skeleton
5. Schema compatibility tests
6. Paper broker
7. `ExchangeAdapter` skeleton (BingX as first implementation)
8. Paper trading loop + 24/7 runtime supervision (restart recovery, health
   checks) — promoted priority, needed for the unattended-operation target.
   Must also verify at this stage that the only real code path from
   `OrderIntent` to `Order` goes through `RiskGateway.evaluate()`:
   `Order.fromApprovedDecision()` today only checks that the
   `RiskDecision` handed to it says APPROVED/MODIFIED, not that it was
   actually produced by a real `evaluate()` call — nothing wires these
   together yet, so this can't be tested until this priority builds that
   wiring.
9. Auto-retraining pipeline (scheduled retrain, validation, promotion gate)
   — promoted priority, needed for the auto-learning target; promotion to
   paper/live still requires human approval
10. Canary live preparation

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
