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
# Credential handling, revised on real CodeRabbit review of this same
# script: the original version did `source <(tr -d '\r' < .env)`, which
# executes the *entire file's content as shell code*, not just parses
# KEY=VALUE pairs -- a real, correctly-identified risk for something run
# automatically and unattended (a human manually typing the equivalent
# `source` command once, with eyes on it, is a materially different risk
# profile than a cron job re-running it every 5 minutes indefinitely).
# `get_env_var` below never executes `.env`'s content -- it only greps
# for one specific `KEY=` line prefix and returns the literal text after
# it. The extracted values are then passed to `tmux new-session` as
# separate argv elements (via `env KEY=value ...`), not concatenated into
# a shell command string, so a value containing shell metacharacters
# still can't be reinterpreted as code either.
#
# Usage: scripts/paper-trading-watchdog.sh
# Logs to var/live/watchdog.log (created if missing). Never logs a
# credential value.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/watchdog.log"
SESSION_LOG_DIR="var/live/sessions"
mkdir -p "$(dirname "$LOG_FILE")" "$SESSION_LOG_DIR"

log() {
    printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$1" >>"$LOG_FILE"
}

# Persist a session's pane output to a file so a crash leaves evidence.
#
# Why this is needed at all: a tmux pane's scrollback dies with the
# session, so every restart this watchdog performed was erasing the only
# record of WHY the app had stopped. The watchdog log proves the sessions
# died roughly daily (2026-08-26 through 2026-08-28); not one of those
# deaths is diagnosable, because the output went nowhere durable.
#
# `pipe-pane` is used rather than a shell redirection inside the
# `new-session` command deliberately: the command below is a fixed
# string with no credential in it, so this keeps the credential-safety
# property the start functions were written for (values reach `env` as
# separate argv elements and are never re-parsed by a shell).
#
# Requires `--console=plain` on the gradlew invocations. Without it
# Gradle redraws a progress spinner about once a second and the capture
# is ~86,000 lines a day of ANSI escapes with the app's real log lines
# buried in them -- verified by doing exactly that before fixing it.
pipe_session_log() {
    local session="$1"
    tmux pipe-pane -o -t "${session}:0.0" \
        "cat >> '$REPO_ROOT/$SESSION_LOG_DIR/${session}.log'" 2>/dev/null \
        || log "WARNING: could not attach a session log for $session"
}

# Extracts one KEY=VALUE pair's value from .env WITHOUT ever executing
# the file's content as shell code (see module header). .env has CRLF
# line endings (a real, previously-disclosed issue -- see
# .planning/paper-trading-h-vst-integration.md's "credential-handling
# incident" section); tr -d '\r' strips it before matching. Last match
# wins if the key appears more than once (mirrors normal `source`/
# `export` semantics). Prints nothing (empty string) if the key isn't
# present -- callers must check for that themselves, this function never
# fails loudly on a missing key on its own.
get_env_var() {
    local key="$1"
    tr -d '\r' <"$REPO_ROOT/.env" | grep -E "^${key}=" | tail -n1 | cut -d'=' -f2-
}

start_simulated() {
    log "starting simulated session (was not running)"
    tmux new-session -d -s paper-trading -c "$REPO_ROOT/java" \
        env BINGX_BASE_URL=https://open-api.bingx.com \
        ./gradlew -q --console=plain :runtime:runPaperTradingApp
    pipe_session_log paper-trading
}

start_vst() {
    local api_key api_secret
    api_key="$(get_env_var BINGX_API_KEY)"
    api_secret="$(get_env_var BINGX_API_SECRET)"
    if [[ -z "$api_key" || -z "$api_secret" ]]; then
        log "ERROR: BINGX_API_KEY/BINGX_API_SECRET missing or empty in .env -- refusing to start bingx-vst session (value itself never logged)"
        return 1
    fi
    log "starting bingx-vst session (was not running)"
    # Each KEY=value below is its own argv element to `env` (tmux
    # new-session's trailing arguments form an argv array here, not a
    # single string re-parsed by a shell) -- so even a credential value
    # containing shell metacharacters is passed through literally, never
    # reinterpreted as code.
    tmux new-session -d -s paper-trading-vst -c "$REPO_ROOT/java" \
        env \
        "BINGX_API_KEY=$api_key" \
        "BINGX_API_SECRET=$api_secret" \
        PAPER_TRADING_EXECUTION_MODE=bingx-vst \
        BINGX_BASE_URL=https://open-api.bingx.com \
        PAPER_TRADING_REPORTS_DIR=var/live/reports/vst \
        ./gradlew -q --console=plain :runtime:runPaperTradingApp
    pipe_session_log paper-trading-vst
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
