# Codex local development

The Windows Codex app edits this chat's worktree. Linux tools run in the
existing WSL distribution named in `AGENTS.md`. The primary checkout stays at
`C:\Dev\trading-engine`; do not accidentally test that checkout when editing
a worktree. `AGENTS.md` is the Codex entry point and references `CLAUDE.md` as
the existing authority. No research or trading policy is changed here.

## Commands

From the worktree root in PowerShell:

```powershell
./scripts/dev.ps1 setup
./scripts/dev.ps1 check
./scripts/dev.ps1 java
```

The wrapper resolves the worktree directory and passes it to
`wsl -d <configured-distribution> --cd <worktree>`. It does not depend on WSL's default
distribution. `setup` uses the existing uv lock file and creates an independent
Linux virtual environment in `python/.venv` for this worktree. Do not run it
with Windows Python or copy the primary checkout's environment.

Inside WSL, the equivalent is `bash scripts/dev.sh setup|check|java`.
Use `./scripts/dev.ps1 git <arguments>` for WSL Git operations and commits;
Windows currently lacks the secret scanner required
by `.githooks/pre-commit`. The clone already has `core.hooksPath=.githooks`.
No separate global Gradle installation is needed; use `java/gradlew`.

Windows-created worktrees contain a Windows absolute path in their `.git`
pointer. Linux Git cannot resolve that path by itself. The PowerShell wrapper
changes only this worktree's `.git` pointer to an equivalent relative path;
the shared repository and its Windows backlink stay in place. Both Git
implementations can then use the same checkout. It does not export `GIT_DIR`
into tests, which create independent temporary repositories. Shell files have
LF checkout attributes so a Windows-created worktree runs correctly in WSL.

## Editing guardrails

Review `.codex/hooks.json` and `scripts/codex_guardrails.py`, then review/trust
the hook through Codex's `/hooks` interface in the session that will use it.
Changing a hook definition requires renewed trust. A successful test of the
handler does not prove a running Codex app has loaded/trusted it.

The adapter consumes Codex's `PreToolUse` / `apply_patch` payload and rebuilds
the candidate files without modifying them. It reuses the existing checks in
`.claude/hooks/vst_guardrail_check.py`; the production-host check preserves
the existing Claude/CI policy. Unknown, ambiguous or fuzzy patch input is
rejected rather than assumed safe. Use an exact, unambiguous context hunk.
The existing Claude hooks remain available for Claude sessions.

This hook covers `apply_patch`, including calls made from code mode. It does
not inspect arbitrary shell commands, MCP writes or edits outside Codex.
`python3 scripts/codex_guardrails.py scan` checks the final repository state;
`staged` checks the actual Git index, not the possibly different working tree.
The Git pre-commit hook and CI call these checks as independent backstops.
The existing Java lexer's documented limitations still apply. These are
secondary guardrails, not an OS sandbox or a substitute for code review.

Official references: [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[hooks and trust](https://learn.chatgpt.com/docs/hooks),
[WSL](https://learn.chatgpt.com/docs/windows/wsl).

## Skills and plugins

GitHub and Context7 are available in Codex. Use Context7 when current library
documentation is needed. The built-in review tools and existing repository
review process are sufficient for this migration; no second workflow framework
or extra model/API service is required. Repeated commands live in the scripts
above instead of a duplicate skill with another copy of the same policy.

Windows Codex settings and WSL Codex CLI settings are separate user profiles.
Project guidance is shared through Git. The dedicated CLI installations and
protected state used by `agent-workflow` are separate from this project and
must not be upgraded, removed or repurposed by these development scripts.
