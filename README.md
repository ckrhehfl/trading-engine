# personal-trading-engine

Personal, institution-style algorithmic trading system. Python research
plane + Java trading plane, built so a new venue means writing a new
`ExchangeAdapter` rather than changing OMS, Risk Gateway or Execution.

**Current scope: Korean domestic equities (KRX/KIS).** Narrowed from
BTC/USDT futures on 2026-09-17 by operator decision — BTC is set aside, not
abandoned, and nothing about it is deleted. **No real-money trading has ever
happened, and none is enabled.** Every loop that exists is paper trading, and
the Korean one starts with its kill switch tripped by design.

## Where things are

This project keeps three kinds of document, split by **lifetime** rather
than by topic. Knowing which is which is the fastest way to find anything.

| | what it answers | how it changes |
|---|---|---|
| **[`CLAUDE.md`](CLAUDE.md)** | what may never be violated — rules, risk parameters, promotion gates, verified exchange API facts | edited in place; loaded into every AI coding session |
| **[`docs/`](docs/)** | what things look like **now** | **replaced** when reality changes |
| **[`.planning/`](.planning/README.md)** | what was decided when, and what was rejected | **append-only**; 121 documents, index enforced by a test |

- [`docs/architecture.md`](docs/architecture.md) — the two planes, the seam
  list, execution modes, what a new venue actually costs
- [`docs/paper-trading-runbook.md`](docs/paper-trading-runbook.md) — how to
  set up and operate the loops on a machine

`.planning/` is organised by work-arc and only ever grows, so it cannot
answer "what is it now" — that is what `docs/` is for. Conversely `docs/`
holds no rule and no measured figure that `CLAUDE.md` owns: a number written
in two places is a contradiction waiting to happen, and this project has
already shipped one.

## Layout

```
java/        OMS · Risk Gateway · Execution · ExchangeAdapter · Reconciler · KillSwitch
python/      research plane — data collection, backtesting, metrics, live signal runner
schemas/     cross-language wire schemas (JSON fixtures, mirrored in both planes)
configs/     research and risk configuration
scripts/     collectors, deployment, health checks
runs/        experiment log and committed research artifacts
```

## Safety

The rules are in `CLAUDE.md` and are not summarised here, because a summary
of a safety rule is a second copy of it. The three that shape everything
else:

- **Live trading is never enabled without explicit human approval.**
- **Every live order passes through the Java Risk Gateway.** Python never
  places one.
- **No MCP server, skill or plugin capable of placing exchange orders** is
  connected to any AI session operating on this repo.

Secrets are caught locally by a `gitleaks` pre-commit hook
(`git config core.hooksPath .githooks`, one-time per clone) and by a CI job
that backstops it. The repo is public.

## Merge policy

`.github/CODEOWNERS` gates high-risk paths — `java/`, `schemas/`,
`configs/`, `.github/`, `CLAUDE.md` — behind owner review; everything else
auto-merges once CI and CodeRabbit pass. Details and the real blocking
mechanism (unresolved review threads, not CODEOWNERS) are in `CLAUDE.md`'s
Branch and Merge section.
