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
- Never add live exchange write-access in CI.
- Never commit raw trading logs containing secrets or account identifiers.
- Never run untrusted install scripts (`curl | sh`, `wget | bash`).
- The repo is public (chosen for free GitHub Actions minutes). Treat GitHub
  push-protection/secret-scanning failures as blocking, not advisory — a
  leak here is immediate and irreversible, not just a private mistake.

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
standing constraint that applies whenever strategy work happens (likely
woven through Priorities #6–#8, none of which name it explicitly — that
absence is exactly why this is written down rather than left implicit).

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
   checks) — promoted priority, needed for the unattended-operation target
9. Auto-retraining pipeline (scheduled retrain, validation, promotion gate)
   — promoted priority, needed for the auto-learning target; promotion to
   paper/live still requires human approval
10. Canary live preparation

None of the above names "build a strategy" explicitly, but #6–#8 can't
happen without one — see Strategy Research Methodology for the
non-negotiable principles that apply whenever that work starts.

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
