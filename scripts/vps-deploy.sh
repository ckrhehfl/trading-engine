#!/usr/bin/env bash
#
# Bring the deployment to the current `origin/main`, rebuild, and restart
# the loops **only if the running code is actually out of date**.
#
#     ./scripts/vps-deploy.sh            # deploy, ask before restarting
#     ./scripts/vps-deploy.sh --yes      # deploy, restart without asking
#     ./scripts/vps-deploy.sh --check    # report only, change nothing
#
# ## Why this exists
#
# Until 2026-09-08 the deploy procedure was `git pull`, and nothing more.
# That is enough for Python and shell — cron starts a fresh process every
# time, so a merged change is live on its next tick. It is **not** enough
# for Java: a JVM keeps the classes it loaded at startup, so an OMS, Risk
# Gateway or adapter fix has no effect until the loop restarts.
#
# The gap was found by the operator asking whether the fixes were
# actually reaching the box. They were not: the checkout was current, the
# classes had been rebuilt that morning, and both loops were still
# running code from two days earlier. **Nothing in the system could have
# reported that** — no dashboard, no watchdog, no log line.
#
# ## Why a restart is not automatic
#
# The watchdog's job is "the process died, start it". This is a different
# judgement: "the code changed, switch to it". Doing it automatically
# would restart a trading loop at an arbitrary moment, possibly while a
# position is open or an order is pending, and a restart also resets that
# day's tick counters — which Gate A is measured from.
#
# So detection is automatic (`live.health_check` reports stale running
# code every 15 minutes) and the switch-over is deliberate. This script
# is the deliberate half, and it refuses to restart silently while a
# position is open.

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECK_ONLY=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

CLASS_FILE="java/runtime/build/classes/java/main/engine/runtime/PaperTradingApp.class"
SESSIONS=(paper-trading paper-trading-vst)

fail() { printf '\n  FAIL  %s\n\n' "$1" >&2; exit 1; }
say()  { printf '  %s\n' "$1"; }

class_mtime() { [[ -f "$CLASS_FILE" ]] && stat -c %Y "$CLASS_FILE" || echo 0; }

oldest_loop_start() {
    # Epoch seconds of the earliest-started loop JVM, or empty if none run.
    local oldest=""
    for pid in $(pgrep -f 'engine.runtime.PaperTradingApp' || true); do
        local started
        started="$(date -d "$(ps -o lstart= -p "$pid" 2>/dev/null)" +%s 2>/dev/null || true)"
        [[ -z "$started" ]] && continue
        if [[ -z "$oldest" || "$started" -lt "$oldest" ]]; then oldest="$started"; fi
    done
    echo "$oldest"
}

# ---------------------------------------------------------------- state

printf '\n=== before ===\n'
say "HEAD          $(git rev-parse --short HEAD)"
git fetch -q origin main
say "origin/main   $(git rev-parse --short origin/main)"

DIRTY="$(git status --porcelain)"
if [[ -n "$DIRTY" ]]; then
    printf '%s\n' "$DIRTY" | sed 's/^/    /'
    fail "the deployment has uncommitted changes. A deploy on top of them is not
        reproducible, and discarding them here could destroy something
        deliberate. Resolve them by hand first."
fi

BEFORE_CLASS="$(class_mtime)"
LOOP_START="$(oldest_loop_start)"
if [[ -n "$LOOP_START" ]]; then
    say "loops started $(date -u -d "@$LOOP_START" '+%Y-%m-%d %H:%M:%SZ')"
    say "classes built $(date -u -d "@$BEFORE_CLASS" '+%Y-%m-%d %H:%M:%SZ')"
    if [[ "$BEFORE_CLASS" -gt "$LOOP_START" ]]; then
        say "STALE: the running loops predate the compiled classes"
    fi
else
    say "no loop is running"
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    printf '\n  --check: nothing was changed.\n\n'
    exit 0
fi

# ----------------------------------------------------------------- pull

printf '\n=== pull ===\n'
git merge --ff-only origin/main
say "now at $(git rev-parse --short HEAD)"

# ---------------------------------------------------------------- build
#
# Gradle rewrites a class file only when its content actually changes --
# verified on this box: `touch` on a source file leaves the class mtime
# alone, and an UP-TO-DATE task does not touch its outputs. So comparing
# the mtime before and after is a real content signal, not a proxy.

printf '\n=== build ===\n'
( cd java && ./gradlew --no-daemon -q :runtime:classes )
AFTER_CLASS="$(class_mtime)"

if [[ "$AFTER_CLASS" -eq "$BEFORE_CLASS" ]]; then
    say "Java unchanged — no restart needed"
    JAVA_CHANGED=0
else
    say "Java rebuilt ($(date -u -d "@$AFTER_CLASS" '+%Y-%m-%d %H:%M:%SZ'))"
    JAVA_CHANGED=1
fi

# Python and shell need nothing: cron starts a fresh process each tick, so
# the pull above already deployed them.
say "Python/shell changes are live already (cron re-execs each tick)"

NEEDS_RESTART=0
if [[ -n "$LOOP_START" && "$AFTER_CLASS" -gt "$LOOP_START" ]]; then NEEDS_RESTART=1; fi

if [[ "$NEEDS_RESTART" -eq 0 ]]; then
    printf '\n  Nothing to restart. Deploy complete.\n\n'
    exit 0
fi

# -------------------------------------------------------------- restart

printf '\n=== restart needed ===\n'
say "the running loops are older than the compiled classes"

# A restart while a position is open is a different risk from a restart
# while flat: the loop comes back and reconciles against a venue state it
# did not create. Surfaced rather than blocked, because refusing outright
# would make a genuine fix undeployable exactly when it matters most.
if [[ -n "${BINGX_API_KEY:-}" && -n "${BINGX_API_SECRET:-}" && -s var/live/runtime-classpath.txt ]]; then
    say "checking the VST account for open positions ..."
    if ! java -cp "$(cat var/live/runtime-classpath.txt)" \
            engine.runtime.VstAccountInspector 2>/dev/null | sed 's/^/    /'; then
        say "(inspector unavailable — continuing, but check the account by hand)"
    fi
else
    say "BINGX_API_KEY/SECRET not exported, so the account was NOT checked."
    say "Export both and re-run if you want the position check before restarting."
fi

if [[ "$ASSUME_YES" -eq 0 ]]; then
    printf '\n  Restart both loops now? A restart resets today'"'"'s tick counters,\n'
    printf '  which Gate A is measured from. [y/N] '
    read -r reply
    [[ "$reply" == "y" || "$reply" == "Y" ]] || { printf '\n  Left running. Deploy stopped before restart.\n\n'; exit 0; }
fi

printf '\n=== restarting ===\n'
for s in "${SESSIONS[@]}"; do
    # `=name` forces an exact match -- a bare name is prefix-matched by
    # tmux, and `paper-trading` is a prefix of `paper-trading-vst`.
    tmux kill-session -t "=$s" 2>/dev/null && say "stopped $s" || say "$s was not running"
done
sleep 3
./scripts/paper-trading-watchdog.sh
sleep 20

printf '\n=== after ===\n'
NEW_START="$(oldest_loop_start)"
[[ -n "$NEW_START" ]] || fail "no loop came back up — run scripts/paper-trading-watchdog.sh and read its log"
say "loops started $(date -u -d "@$NEW_START" '+%Y-%m-%d %H:%M:%SZ')"
[[ "$NEW_START" -ge "$AFTER_CLASS" ]] \
    || fail "the loops are still older than the compiled classes -- the restart did not take"
for s in "${SESSIONS[@]}"; do
    tmux has-session -t "=$s" 2>/dev/null || fail "$s is not running after the restart"
done
say "both sessions up and newer than the build"
printf '\n  Deploy complete.\n\n'
