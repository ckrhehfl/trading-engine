#!/usr/bin/env bash
# Local development only: no credentials, data collection, or deployment.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export UV_LINK_MODE=copy
# Do not leak a caller's Git repository/index into synthetic test repositories.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX
git rev-parse --show-toplevel >/dev/null
case "${1:-check}" in
    setup) cd python; uv sync --frozen ;;
    check)
        python3 scripts/codex_guardrails.py scan
        python3 -m unittest discover -s scripts/tests -p 'test_*.py'
        python3 .claude/hooks/test_vst_guardrail_check.py
        cd python
        uv run --frozen python -m pytest -q
        ;;
    java) cd java; bash ./gradlew build --no-daemon ;;
    git) shift; exec git "$@" ;;
    *) echo 'Usage: bash scripts/dev.sh setup|check|java|git [git arguments]' >&2; exit 2 ;;
esac
