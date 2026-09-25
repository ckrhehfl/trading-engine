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

```text
java/        OMS · Risk Gateway · Execution · ExchangeAdapter · Reconciler · KillSwitch
python/      research plane — data collection, backtesting, metrics, live signal runner
schemas/     cross-language wire schemas (JSON fixtures, mirrored in both planes)
configs/     research and risk configuration
scripts/     collectors, deployment, health checks
runs/        experiment log and committed research artifacts
```

## Safety

**`CLAUDE.md`'s Non-negotiable Rules are the binding text.** The three
below are an orientation list — enough to know what kind of project this is
before opening it — and are not the rule: where the two differ, `CLAUDE.md`
is right and this file is stale.

Said that way because the first draft claimed not to summarise the rules and
then summarised three of them in the next sentence. Caught on review of
PR #207. A front door has to name them; what it must not do is pretend the
naming is authoritative.

- Live trading is never enabled without explicit human approval.
- Every live order passes through the Java Risk Gateway. Python never places
  one.
- No MCP server, skill or plugin capable of placing exchange orders is
  connected to any AI session operating on this repo.

Secrets are caught locally by a `gitleaks` pre-commit hook
(`git config core.hooksPath .githooks`, one-time per clone) and by a CI job
that backstops it. The repo is public.

## Merge policy

`.github/CODEOWNERS` **names** the high-risk paths — `java/`, `schemas/`,
`configs/`, `.github/`, `CLAUDE.md`, `.coderabbit.yaml` — and everything else
auto-merges once CI and CodeRabbit pass. That is all six; the list read five
until review of PR #207 caught the review-rules file missing from it.

**It is not a server-side gate today, and saying it "gates" them would
overstate the protection.** GitHub does not raise a required-review when the
PR author is also the sole code owner, which is the situation here; that was
tested empirically. What actually blocks a merge is
`required_conversation_resolution` — an unresolved CodeRabbit thread — plus
any standing `CHANGES_REQUESTED`. So the CODEOWNERS boundary is enforced
**procedurally** on those paths, and branch protection stays on because it
does bind a future second collaborator or bot identity. Full mechanism, and
how to diagnose a `BLOCKED` PR that looks green: `CLAUDE.md`'s Branch and
Merge section.
