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

# What would a restart actually buy? Asked because the first real use of
# this script, 2026-09-08, found the answer was "nothing": the loops were
# 40 hours stale, and the only Java that had changed was a standalone
# diagnostic class with its own `main` that the loop never loads, plus a
# package-private accessor only that class calls.
#
# That matters because a restart is not free -- it resets the day's tick
# counters, which Gate A is measured from, and it re-arms a loop whose
# kill switch is in-memory. Restarting to pick up a change that cannot
# affect the running graph pays that cost for nothing.
#
# This lists the commits rather than judging them: deciding whether a
# diff reaches the loop's own object graph is a reading task, not a
# `grep`. Printing the list is what turns it from an investigation into
# a glance.
#
# Two ways to compute it, and they are not equally trustworthy.
#
# The first version used `git log --since=<loop start time>` alone, which
# compares commit *dates*. That is accurate for this repo's squash-merge
# workflow -- a squash commit's date is its merge time -- and silently
# wrong for a cherry-pick, a force-push, or a rebase that rewrites dates.
# CodeRabbit flagged it on PR #150 and was right: a commit missing from
# this list is a Java change that never reaches the loop, which is the
# exact failure this whole script exists to stop.
#
# So the watchdog now records the launch commit per session, and the
# range below is computed from that identity when it is available. The
# timestamp form remains only as a fallback for a loop started before
# that recording existed -- labelled, because an incomplete list read as
# a complete one is worse than no list.
LAUNCH_COMMIT=""
for s in "${SESSIONS[@]}"; do
    f="var/live/sessions/${s}.commit"
    [[ -r "$f" ]] || continue
    sha="$(tr -d '[:space:]' < "$f")"
    git cat-file -e "${sha}^{commit}" 2>/dev/null || continue
    # The oldest launch commit across the sessions is the conservative
    # choice: it can list a commit one session already has, never omit
    # one that some session is missing.
    if [[ -z "$LAUNCH_COMMIT" ]] || ! git merge-base --is-ancestor "$LAUNCH_COMMIT" "$sha" 2>/dev/null; then
        LAUNCH_COMMIT="$sha"
    fi
done

printf '\n  Java commits the running loops do not have:\n'
if [[ -n "$LAUNCH_COMMIT" ]]; then
    git log --oneline "${LAUNCH_COMMIT}..HEAD" -- java/ | sed 's/^/    /' \
        || say "(could not list them -- check by hand before restarting)"
    say ""
    say "(by recorded launch commit ${LAUNCH_COMMIT:0:7} -- exact)"
else
    git log --oneline --since="@$LOOP_START" -- java/ | sed 's/^/    /' \
        || say "(could not list them -- check by hand before restarting)"
    say ""
    say "WARNING: no launch commit was recorded for these sessions, so"
    say "this list is by TIMESTAMP and can MISS a commit whose date does"
    say "not reflect when it was merged. Treat it as a hint, never as"
    say "grounds to skip a restart. Restarting once more than needed is"
    say "cheap; running stale Risk Gateway or OMS code is not."
fi
printf '\n  Read that list before answering. If every entry is a test, a\n'
printf '  doc, or a class the loop never loads, a restart changes nothing\n'
printf '  and costs a tick-counter reset. If the list is empty and came\n'
printf '  from the timestamp fallback, that is not the same as "nothing\n'
printf '  changed" -- prefer restarting.\n'

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

# The hazard that is easiest to miss, because nothing on the box states
# it: `KillSwitch` is constructed with `new KillSwitch()` and is never
# persisted, and `OrderStore` is in-memory too. So a restart clears both
# -- a tripped switch comes back **untripped** unless `VstPreflight`
# independently decides to trip it (a pre-existing non-zero position) or
# an unresolved submission marker is found.
#
# Found on 2026-09-08: the VST loop was halted with its switch tripped,
# re-tripping every tick on an orphaned order. Restarting it would have
# cleared the orphan, come back untripped, and re-armed a loop whose
# quantity-precision defect was still unfixed -- turning a contained
# incident back into a live one, silently, as a side effect of a deploy.
if grep -q 'paper-trading-vst' <<<"${SESSIONS[*]}"; then
    printf '\n'
    say "NOTE: the kill switch and the order store are both in-memory."
    say "A restart clears them. If a loop is currently halted -- tripped"
    say "switch, orphaned order -- it comes back ARMED unless preflight"
    say "finds an open position or an unresolved marker."
    say "Do not restart a venue-connected loop to pick up an unrelated"
    say "change while the defect that halted it is still unfixed."
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
