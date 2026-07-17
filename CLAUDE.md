# CLAUDE.md

## Project Identity

Personal, institution-style BTC/USDT futures trading system. The system may
eventually place real trades. Treat all execution, risk, leverage, position
mode, exchange API, deployment, and key-management changes as high-risk.

## Current Scope

- Exchange: BingX
- Product: BTC/USDT USDT-M Perpetual Futures
- Direction: long and short
- Order types: limit and guarded market
- Timeframes: 15m base, 5m extension, 1h regime filter
- Single-user, VPS-oriented, not a SaaS

Non-goals for now: HFT, co-location, multi-user SaaS, fully unattended live
trading, multi-exchange live trading, Kubernetes, Kafka/Aeron/Chronicle Queue.

## Architecture

```text
Python Research Plane
- data research, deterministic backtesting, strategy experiments
- feature engineering, ML training/evaluation
- report generation, deployment candidate generation
- must not place live orders directly

Java Trading Plane
- OMS, Risk Gateway, Execution Service, BingX Adapter
- position reconciliation, kill switch, paper/live runtime
- all live orders must pass through the Java Risk Gateway
```

Java scope is intentionally narrow: OMS / Risk / Execution / Exchange
Adapter / Reconciliation / Kill Switch only. No Spring/Kafka/K8s/Aeron.
Start with Java 21 + Gradle + JUnit + Jackson + SLF4J only. Strategy
research, backtesting, ML, and reporting stay in Python.

Reassess this split if: solo-dev burden becomes excessive, a Python
prototype proves sufficient on its own, or Python/Java schema drift keeps
recurring.

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

## BingX API Facts (verified by direct observation, re-verify before relying on them)

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
verification and human approval like any other change.

## Development Workflow

1. Read this file and the relevant code before changing anything.
2. Plan before non-trivial or cross-cutting changes.
3. Ask for explicit approval before high-risk changes (live trading,
   credentials, risk limits, leverage, kill switch).
4. Make small, scoped changes; add or update tests.
5. Run verification commands before claiming a task is done.
6. Report: files changed, tests added/run, results, remaining risks, and
   whether human approval is needed.

## Branch and Merge

- Never commit or push directly to `main`.
- Changes go through a branch and a PR.
- Self-review and verify before opening a PR.

## Deliberately not carried over from the previous attempt

A prior version of this project accumulated 15 cross-referenced docs, a
5-level risk-classification system, a 16-step PR lifecycle, 5 read-only
reviewer subagents + 5 wrapper skills + a policy hook, and a dual
CodeRabbit/CI merge gate — before a single continuously-running paper
trading loop existed. That process outpaced the working system and became
the bottleneck itself. Add any of this back only when a concrete, recurring
problem justifies it — not preemptively.
