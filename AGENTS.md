# Trading engine — Codex entry point

Explain work to the operator in Korean. This is the existing trading-engine
project, not the separate agent-workflow product. Use the tools actually
available in the current Codex session; Claude tool names are not commands.

## Read before changing code

`CLAUDE.md` remains the single source of binding project rules, risk parameters,
research gates and standing decisions. Its name does not limit it to Claude.
Read its Current Scope, Architecture, Non-negotiable Rules, LLM Usage Policy,
Development Methodology, Code Review Gate, and Branch and Merge sections first.
Read the complete relevant research/exchange/implementation sections before
work in those areas. Do not replace this with a summary or copy its numeric
rules into another document.

Then read `docs/architecture.md`, `docs/codex-handoff.md`, and the relevant
documents indexed in `.planning/README.md`. Check actual Git and execution
state against the handoff; conversation transcripts and old plans are evidence,
not fresh authorization. Keep `.planning/` append-only and its index accurate.

## Local development

The supported Linux toolchain is **WSL2 Ubuntu-24.04**. Windows source paths
and their `/mnt/c/...` paths name the same files. Work in this chat's worktree,
not automatically in the primary `C:\Dev\trading-engine` checkout.

From PowerShell, use `scripts/dev.ps1 setup`, `scripts/dev.ps1 check`, or
`scripts/dev.ps1 java`. The wrapper resolves its own worktree and uses
`wsl -d Ubuntu-24.04`; it never chooses a different default distribution.
From WSL, run one command at a time:

```bash
bash scripts/dev.sh setup
bash scripts/dev.sh check
bash scripts/dev.sh java
```

- `setup`: synchronize this worktree's Python environment from `uv.lock`.
- `check`: run project guardrails, their regression tests, and the Python suite.
- `java`: build and test the Java modules with the repository Gradle wrapper.
- Commit using WSL Git so the existing gitleaks pre-commit hook can run.
- Do not share or copy another checkout's `.venv`; each worktree owns its own.

## Operational boundary

The GCP collectors and pre-2019 backfill are running independently of this
local migration. Do not stop, restart, redeploy, reschedule or replace them as
part of environment setup. Do not copy `.env`, credentials, trading databases
or research logs into a new worktree. A setup/test command must not call an
exchange, SSH into GCP, launch a paper loop, or run a research experiment.

The pre-2019 window remains reserved. Continue the recorded discovery work
only under `CLAUDE.md`'s research guards; access to a holdout needs the explicit
human checkpoint there. No live trading or order-capable MCP/plugin is enabled.

## Codex hooks and verification

`.codex/hooks.json` adapts the existing Claude guardrail functions to Codex
patch input. New/changed hooks require Codex's own review/trust flow (`/hooks`);
file presence and a passing script test do not prove that a running session
has loaded them. See `docs/codex-development.md` for setup and limitations.

Use `apply_patch` for repository edits so the pre-edit hook can inspect the
resulting files. Shell/MCP writes are outside that hook's coverage; run the
repository scanner before finishing and retain the Git/CI backstops. Never
use an alternative write path to get around a rejection.

Verify actual tests, inspect the diff, and follow `CLAUDE.md`'s branch/PR and
review policy. Do not push directly to main or change running collectors to
make a local check pass.
