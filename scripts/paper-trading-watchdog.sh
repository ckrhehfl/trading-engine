#!/usr/bin/env bash
# Ensures both paper-trading tmux sessions (simulated + bingx-vst) are
# running, restarting whichever one is missing. Meant to be run
# periodically via cron -- this project has no OS-level process
# supervision otherwise (a deliberate, disclosed limitation of the local
# ("not VPS-provisioned") run phase, see
# .planning/paper-trading-c-scheduler-entrypoint.md and
# .claude/plans/tender-finding-matsumoto.md). This script does not make
# either loop survive a full machine/WSL restart on its own -- it only
# closes the gap between "the tmux server died but the machine is still
# on" and "someone notices and manually restarts it," which is exactly
# the failure mode observed for real during this project's own local-run
# phase (both sessions silently died together when the tmux server
# itself went away, discovered only when a human happened to check).
#
# Idempotent: safe to run every few minutes via cron. Does nothing if
# both sessions are already alive.
#
# Usage: scripts/paper-trading-watchdog.sh
# Logs to var/live/watchdog.log (created if missing).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/watchdog.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$1" >>"$LOG_FILE"
}

start_simulated() {
    log "starting simulated session (was not running)"
    tmux new-session -d -s paper-trading -c "$REPO_ROOT" \
        "cd '$REPO_ROOT/java' && BINGX_BASE_URL=https://open-api.bingx.com ./gradlew -q :runtime:runPaperTradingApp"
}

start_vst() {
    log "starting bingx-vst session (was not running)"
    # .env has CRLF line endings (a real, previously-disclosed issue --
    # see .planning/paper-trading-h-vst-integration.md's "credential-
    # handling incident" section); tr -d '\r' strips it before sourcing.
    # BingXAdapter's own constructor also .strip()s credentials as a
    # second, independent layer, but stripping at the source is cheaper
    # and avoids relying on that alone.
    tmux new-session -d -s paper-trading-vst -c "$REPO_ROOT" \
        "cd '$REPO_ROOT' && set -a && source <(tr -d '\r' < .env) && set +a && cd '$REPO_ROOT/java' && PAPER_TRADING_EXECUTION_MODE=bingx-vst BINGX_BASE_URL=https://open-api.bingx.com PAPER_TRADING_REPORTS_DIR=var/live/reports/vst ./gradlew -q :runtime:runPaperTradingApp"
}

# `=name` forces an EXACT session-name match. Without the `=`, tmux's
# target-session matching is prefix/fnmatch-based, so a bare
# `-t paper-trading` check was found (during real testing of this
# script) to falsely report success against the *other*,
# similarly-prefixed `paper-trading-vst` session -- i.e. the simulated
# session could silently stay dead forever because the check thought it
# saw it alive. Exact match closes this for real, not just for these two
# specific names, in case a third session with a similar prefix is ever
# added later.
if tmux has-session -t "=paper-trading" 2>/dev/null; then
    :
else
    start_simulated
fi

if tmux has-session -t "=paper-trading-vst" 2>/dev/null; then
    :
else
    start_vst
fi
