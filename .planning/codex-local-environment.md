# Codex migration — shared WSL toolchain and preserved research handoff

Investigated and implemented on 2026-10-05, starting from `4ce85d7`.
The operator authorized the migration, consolidation onto `Ubuntu-24.04`,
backup/removal of the older `Ubuntu`, and reading the Claude work history.
GCP collection remains outside the migration's write scope.

## Decisions

- Keep the existing project and historical `CLAUDE.md` as the authority.
  Add a short `AGENTS.md` entry point; do not blindly rename the long rule file
  or introduce a competing copy of its research/risk parameters.
- Reuse the existing Claude venue analyzers through a strict Codex patch
  adapter. Add final-tree and staged-index scans, regression tests, and CI.
  Codex hook trust is a separate product step; script verification cannot
  establish that an already-running session has loaded/trusted a new hook.
- Keep source in Windows and tools in the existing `Ubuntu-24.04` distribution.
  Each worktree gets its own Python environment from the existing lock file.
  Add LF checkout attributes for shell scripts and the Gradle wrapper.
- Do not add another workflow framework. GitHub and Context7 are already
  available; repeatable checks are ordinary repository scripts.
- Preserve the previous research endpoint in `docs/codex-handoff.md`. The last
  continuation stopped on a Claude subscription error. It did not authorize
  a pre-2019 access or establish a return edge from activity persistence.

## The Windows worktree failure and correction

The Codex-created `.git` pointer originally contained a Windows absolute path,
which WSL Git could not resolve. The first development wrapper exported
`GIT_DIR` and `GIT_WORK_TREE`; an isolated-repository test then inherited them.
Its `git init` wrote an unintended `core.worktree` into the common Git config.
That added value was removed, both Windows checkouts were checked again, and
the primary checkout remained clean. No source changes were lost.

The final wrapper instead converts only the current worktree's `.git` pointer
to an equivalent relative path. Windows and WSL resolve it to the same Git
directory; the common repository's Windows backlink remains unchanged. Git
environment variables are removed before running tests, and synthetic Git
tests explicitly use a clean Git environment. The scanner separately rejects
an inherited foreign Git directory by selecting its own repository.

## Local machine housekeeping

The older distribution named `Ubuntu` was exported using WSL's compressed tar
format. The entire tar member stream was read and the selected original-file
hashes and lengths were compared before unregistering that exact distribution.
The archive, checksum manifest and restoration instructions are in the user's
private backup directory, outside this public repository. No credentials or
raw Claude transcript are included in this commit.

`Ubuntu-24.04`, its agent-workflow installations/state, Windows source trees,
and the GCP deployment were retained. The obsolete global Claude tool-name
mapping was backed up and replaced with tool-independent working agreements.

## Verification scope

The Codex adapter is tested from actual Windows and WSL hook commands,
including nested working directories, allowed edits, denied edits and malformed
payloads. Staged-index tests distinguish candidates from different working-tree
content. Separate probes disable each of the three venue checks and require
its regression test to fail. Existing Claude tests are retained.

Local Python regression and Java build results are recorded in the change's
delivery report. No remote deployment, collector restart, exchange call,
research trial, holdout access or promotion is part of these tests.

## Review corrections

CodeRabbit's review of PR #222 identified three local-tooling issues. Both
native hook commands now turn root-discovery and handler-loading exceptions
into a blocking exit with a sanitized reason. Windows uses the same ancestor
search as Linux, removing the bootstrap's dependency on a separate Git process.
Regression cases cover an absent repository, missing handler and failed handler,
alongside the existing allowed/denied edit checks.

The development wrapper and standalone scanner also clear inherited common
Git directory, object-store and prefix variables. Regression tests inject a
foreign repository environment and verify the intended worktree/index is used.
The WSL instructions now show each command separately and say to run one at a
time, so copying them cannot accidentally create a shell pipeline.

A subsequent review caught an important distinction: Git supplies a temporary
candidate index to pre-commit for partial commits. Clearing it makes the hook
inspect the wrong staged content. Ordinary scans still discard inherited index
settings; the dedicated pre-commit mode instead verifies that the candidate is
a file in the current worktree's Git directory before reading it. Integration
tests execute the real hook in isolated clones and linked worktrees, exercising
both forbidden partial commits and safe partial commits with excluded staged
changes. The tests stub only gitleaks, not Git or the venue scanner.
