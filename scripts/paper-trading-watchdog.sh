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

# --- launcher: gradle, or a plain JVM on a small box -------------------
#
# `./gradlew :runtime:runPaperTradingApp` keeps a Gradle **daemon** and a
# wrapper JVM alive for the whole life of the loop. Measured on the
# development host with both loops running: the two PaperTradingApp JVMs
# together used 267 MB, while the Gradle daemons and wrappers used
# 1,570 MB -- roughly 6x the application itself, entirely build tooling
# that a machine which only runs the app has no use for.
#
# That difference decides which VPS is viable: 267 MB of application fits
# a 1 GB always-free instance, 1.8 GB does not.
#
# Set PAPER_TRADING_LAUNCHER=java to run the built classes directly. The
# default stays `gradle`, so no existing host changes behaviour unless it
# opts in. The classpath is generated once by Gradle and cached; a build
# is still required before first use, it is simply not kept resident.
PAPER_TRADING_LAUNCHER="${PAPER_TRADING_LAUNCHER:-gradle}"
CLASSPATH_CACHE="$REPO_ROOT/var/live/runtime-classpath.txt"

# Heap is capped explicitly rather than left to the JVM's default of a
# quarter of physical RAM. Two loops defaulting on a 1 GB box would each
# claim 256 MB of heap on top of metaspace and stacks, which is how a
# small instance starts swapping instead of ticking.
PAPER_TRADING_HEAP="${PAPER_TRADING_HEAP:-192m}"

runtime_classpath() {
    if [[ ! -s "$CLASSPATH_CACHE" ]]; then
        log "generating runtime classpath (one-off; cached at $CLASSPATH_CACHE)"
        mkdir -p "$(dirname "$CLASSPATH_CACHE")"
        # Under the SAME lock `vps-bootstrap.sh` uses, and into a
        # `mktemp` file rather than a shared `.tmp` name. This runs from
        # cron every 5 minutes while the bootstrap may be running by
        # hand, and with a shared temp name the bootstrap's `mv` can land
        # between this function's write and its own size check -- after
        # which this run concludes generation failed, moves a perfectly
        # good cache to `.stale`, and refuses to start the session.
        (
            exec 9>"$CLASSPATH_CACHE.lock"
            flock -w 120 9 || exit 3
            # Another holder of the lock may have just written it.
            [[ -s "$CLASSPATH_CACHE" ]] && exit 0
            cd "$REPO_ROOT/java" || exit 4
            ./gradlew -q --console=plain :runtime:classes >/dev/null 2>&1 || exit 5
            tmp="$(mktemp "$CLASSPATH_CACHE.XXXXXX")"
            if ./gradlew -q --console=plain :runtime:printRuntimeClasspath >"$tmp" 2>/dev/null \
                   && [[ -s "$tmp" ]]; then
                mv -f "$tmp" "$CLASSPATH_CACHE"
                exit 0
            fi
            rm -f "$tmp"
            exit 6
        ) || {
            log "ERROR: could not generate the runtime classpath (rc=$?); keep PAPER_TRADING_LAUNCHER=gradle"
            return 1
        }
    fi
    tr -d '\n' <"$CLASSPATH_CACHE"
}

# Sets the global array LAUNCH_ARGV to the command that starts the app.
#
# Sets a global rather than printing, because a caller doing
# `mapfile -t argv < <(launch_argv)` cannot see the failure: process
# substitution reports the *redirection's* status, not the command's, so
# a failed classpath lookup would arrive as a clean EOF and leave `argv`
# empty. `tmux` would then run `env` with no command after it -- a
# session that exists, logs nothing, and never starts PaperTradingApp,
# while the watchdog reports success. Failing loudly here is the point.
#
# It also sets LAUNCH_DIR, and the two launchers genuinely differ.
# `runPaperTradingApp` sets `workingDir = rootDir.parentFile`, i.e. the
# repo root, overriding wherever gradlew was invoked from. A bare
# `java -cp ...` inherits the caller's directory instead. Starting both
# from `$REPO_ROOT/java` therefore gives the two launchers *different*
# working directories, and PaperTradingApp resolves every path it uses
# relative to that -- signal file, reports directory, kill-switch state.
# Under the java launcher they would all land under `java/`, silently.
#
# So: gradle runs from `java/` because that is where `gradlew` lives, and
# java runs from the repo root because that is what the Gradle task was
# already doing.
LAUNCH_ARGV=()
LAUNCH_DIR=""
launch_argv() {
    if [[ "$PAPER_TRADING_LAUNCHER" == "java" ]]; then
        local cp
        cp="$(runtime_classpath)" || return 1
        [[ -n "$cp" ]] || { log "ERROR: runtime classpath is empty"; return 1; }
        LAUNCH_ARGV=(java "-Xmx$PAPER_TRADING_HEAP" -cp "$cp" engine.runtime.PaperTradingApp)
        LAUNCH_DIR="$REPO_ROOT"
    else
        LAUNCH_ARGV=(./gradlew -q --console=plain :runtime:runPaperTradingApp)
        LAUNCH_DIR="$REPO_ROOT/java"
    fi
}

start_simulated() {
    log "starting simulated session (was not running)"
    launch_argv || { log "ERROR: could not build launch argv; not starting"; return 1; }
    tmux new-session -d -s paper-trading -c "$LAUNCH_DIR" \
        env BINGX_BASE_URL=https://open-api.bingx.com \
        "${LAUNCH_ARGV[@]}"
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
    launch_argv || { log "ERROR: could not build launch argv; not starting"; return 1; }
    log "starting bingx-vst session (was not running)"
    # Each KEY=value below is its own argv element to `env` (tmux
    # new-session's trailing arguments form an argv array here, not a
    # single string re-parsed by a shell) -- so even a credential value
    # containing shell metacharacters is passed through literally, never
    # reinterpreted as code.
    tmux new-session -d -s paper-trading-vst -c "$LAUNCH_DIR" \
        env \
        "BINGX_API_KEY=$api_key" \
        "BINGX_API_SECRET=$api_secret" \
        PAPER_TRADING_EXECUTION_MODE=bingx-vst \
        BINGX_BASE_URL=https://open-api.bingx.com \
        PAPER_TRADING_REPORTS_DIR=var/live/reports/vst \
        "${LAUNCH_ARGV[@]}"
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
