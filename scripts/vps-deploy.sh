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
# Per session, never merged into one range. An earlier version took the
# "oldest" launch SHA across sessions using `merge-base --is-ancestor`,
# which is only meaningful when the two are on the same line of history.
# If they are not -- one session started from a branch, the other from
# main -- neither is an ancestor of the other, the loop picks one
# arbitrarily, and the commits the *other* session is missing vanish from
# the list. CodeRabbit raised this on PR #150.
#
# Printing per session is also simply more useful: it says which loop is
# missing what, rather than a union that names neither.
printf '\n  Java commits each running loop does not have:\n'
ANY_RECORDED=0
for s in "${SESSIONS[@]}"; do
    f="var/live/sessions/${s}.commit"
    sha=""
    if [[ -r "$f" ]]; then
        sha="$(tr -d '[:space:]' < "$f")"
        git cat-file -e "${sha}^{commit}" 2>/dev/null || sha=""
    fi

    if [[ -z "$sha" ]]; then
        say ""
        say "$s: no launch commit recorded, falling back to TIMESTAMP"
        git log --oneline --since="@$LOOP_START" -- java/ | sed 's/^/      /' \
            || say "      (could not list them -- check by hand)"
        continue
    fi

    ANY_RECORDED=1
    say ""
    say "$s (launched at ${sha:0:7}):"
    if [[ -z "$(git log --oneline "${sha}..HEAD" -- java/)" ]]; then
        say "      (none)"
    else
        git log --oneline "${sha}..HEAD" -- java/ | sed 's/^/      /'
    fi
done

if [[ "$ANY_RECORDED" -eq 0 ]]; then
    say ""
    say "WARNING: no launch commit was recorded for any session, so the"
    say "lists above are by TIMESTAMP and can MISS a commit whose date"
    say "does not reflect when it was merged. Treat them as a hint, never"
    say "as grounds to skip a restart. Restarting once more than needed is"
    say "cheap; running stale Risk Gateway or OMS code is not."
fi

say ""
say "NOTE: a recorded SHA is the checkout at launch, which is not the same"
say "thing as what the JVM loaded -- classes built from an older commit"
say "would make it optimistic. health_check's own unbuilt_java_source"
say "covers that gap separately. These lists are a hint for the question"
say "below; the restart decision itself is made from the class file's own"
say "mtime, which is artifact-based and unaffected by any of this."

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

# Start the loops under the SAME environment cron gives them, not this
# shell's. Found the hard way on 2026-09-09, the first real use of this
# script: it invoked the watchdog directly, so the restart ran without
# `PAPER_TRADING_LAUNCHER=java` and came back on the Gradle launcher --
# which on a 1 GB instance is the configuration this project measured at
# 6x the application's own memory and deliberately moved off. No
# `PaperTradingApp` JVM ever appeared and the verification below failed,
# correctly.
#
# The same crontab also carries `PAPER_TRADING_MOCK_SIGNALS=1`, and
# missing that one is worse than missing the launcher: without it the
# simulated loop reads the real daily-signal file instead of its own mock
# one, putting both loops on a single shared file -- the exact shape of
# `check_no_shared_mutable_state`'s recorded incident.
#
# `crontab -l` is read rather than a copy kept here, because the crontab
# is what actually defines the running environment. A second copy would
# drift, and the drift would be invisible until a restart behaved
# differently from every cron tick.
# Only assignments that appear BEFORE the watchdog's own cron entry, and
# for a repeated key only the last one before it -- that is precisely the
# subset cron itself applies to that job. Collecting the whole crontab
# would hand the restart a value cron never gives the watchdog; a later
# `PAPER_TRADING_LAUNCHER=gradle` meant for some other job would silently
# undo the very thing this block exists to get right. CodeRabbit raised
# it on PR #155.
declare -A CRON_ENV_BY_KEY=()
while IFS= read -r raw; do
    # Normalise the way cron reads a line before deciding anything about
    # it. crontab(5) allows leading whitespace, spaces around the `=`, and
    # a quoted value; matching on the raw line gets all three wrong.
    # CodeRabbit raised it on PR #155 -- and the first of the three is the
    # one that bites hardest: an INDENTED comment mentioning the watchdog
    # would have ended the collection early, so the real assignments after
    # it would be dropped and the loops would start from this shell's
    # environment, which is the very failure this block exists to prevent.
    line="${raw#"${raw%%[![:space:]]*}"}"     # strip leading whitespace
    [[ "$line" == \#* ]] && continue           # a comment, wherever it was indented
    [[ -z "$line" ]] && continue

    # The watchdog's own entry ends the region that applies to it.
    if [[ "$line" == *"paper-trading-watchdog.sh"* ]]; then
        break
    fi

    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        value="${value%"${value##*[![:space:]]}"}"   # strip trailing whitespace
        # One layer of matching quotes, which cron uses to preserve
        # whitespace inside a value.
        if [[ "$value" == \"*\" && "${#value}" -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" == \'*\' && "${#value}" -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        fi
        CRON_ENV_BY_KEY["$key"]="$key=$value"
    fi
done < <(crontab -l 2>/dev/null || true)

CRON_ENV=()
CRON_ENV_NAMES=()
for k in "${!CRON_ENV_BY_KEY[@]}"; do
    CRON_ENV+=("${CRON_ENV_BY_KEY[$k]}")
    CRON_ENV_NAMES+=("$k")
done

if [[ "${#CRON_ENV[@]}" -eq 0 ]]; then
    say "WARNING: no environment assignments precede the watchdog's cron entry,"
    say "so the loops are starting with this shell's environment instead. If they"
    say "come back on the Gradle launcher, or the simulated loop starts reading"
    say "the real signal file, that is why."
    ./scripts/paper-trading-watchdog.sh
else
    # Names only. A crontab is not expected to hold a credential -- this
    # project keeps those in .env -- but a deploy log is exactly where one
    # would be least welcome, and this repository already has one real
    # incident of a credential reaching a local log through an error
    # message. The values still reach `env` unchanged.
    say "starting under the crontab's own environment: ${CRON_ENV_NAMES[*]}"
    env "${CRON_ENV[@]}" ./scripts/paper-trading-watchdog.sh
fi
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
