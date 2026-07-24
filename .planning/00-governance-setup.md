# Governance Setup (pre-Implementation-Priority, retrospective summary)

**PRs**: #1–#8 (merged 2026-07-17)

Built the merge-governance structure before any trading-system code: local
`dwarvesf/claude-guardrails` (Lite), `.github/CODEOWNERS` risk-tiering,
branch protection on `main`, CodeRabbit Pro installed and verified.

Key finding, corrected mid-stream (see PR #5, #14's Auto-merge Policy
section in CLAUDE.md): GitHub's "Require review from Code Owners" does
not block merging when the PR author is also the sole code owner —
verified empirically with `enforce_admins` both `false` and `true`. The
CODEOWNERS boundary is enforced procedurally (ask before merging a
matched PR), not technically, until PR authorship moves to a bot/app
identity distinct from the repo owner.

Verified end-to-end: low-risk paths (not CODEOWNERS-matched) auto-merge
on CI + CodeRabbit alone; CODEOWNERS-matched paths require an explicit
go-ahead every time, which has held for every PR since (#9 onward).
