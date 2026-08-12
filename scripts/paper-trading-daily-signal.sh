#!/usr/bin/env bash
# Ensures `live.generate_daily_signal` has run at least once for the
# current UTC calendar day, catching up automatically if the machine
# was asleep/off through the originally-scheduled window instead of
# silently skipping that day -- a real gap observed for real during
# this project's own operation (docs/paper-trading-runbook.md section
# 4's old fixed-time "5 9 * * *" example had no way to recover from a
# machine that wasn't awake at that exact minute; standard cron does
# not run missed jobs retroactively).
#
# Safe to run every few minutes via cron instead of once a day: the
# underlying script is explicitly documented as idempotent/safe to
# re-run (see live/generate_daily_signal.py's own module docstring,
# "No cross-invocation state") -- every invocation re-derives today's
# decision from scratch from the real, current kline history, so
# running it once vs. retrying it several times before it first
# succeeds on a given UTC day produces the identical decision. This
# script only avoids needlessly re-running it many times *after* a
# clean success, via the marker file below.
#
# `fetch_live_klines` floors its fetch window to the most recent UTC
# midnight before "now" regardless of what time of day it actually
# runs, so any run during UTC day D always sees the identical, fully
# up-to-date input data -- catching up at, say, 08:00 UTC instead of
# the originally-intended 00:05 UTC produces the exact same decision,
# not a stale or partial one.
#
# Marker file (var/live/last_signal_run_date.txt) records the last UTC
# date this script completed a successful run for -- only written
# AFTER the python invocation exits 0, so a failed run (network error,
# etc.) leaves no marker and gets retried on the very next cron tick
# rather than being silently marked "done" for the day. Written
# atomically (temp file + `mv` within the same directory, mirroring
# generate_daily_signal.py's own `write_signal_atomically`) so a
# process killed mid-write can never leave a truncated/corrupted
# marker on disk -- worst case a stale-but-intact date, which just
# triggers a normal, safe re-run rather than any ambiguity.
#
# Concurrency: an exclusive, non-blocking `flock` (held from before the
# marker check through after the marker write) makes overlapping
# invocations safe. Without it, a single run that happens to take
# longer than 5 minutes (a slow/hanging BingX fetch, a retry) could
# still be in flight when the next cron tick fires; both instances
# would read the same not-yet-updated marker, both decide "not done
# today," and both call live.generate_daily_signal concurrently against
# the shared SQLite kline cache. A second instance that finds the lock
# already held (flock's own exit 1) exits 0 immediately -- not an
# error, another instance is already handling this tick's work. Any
# OTHER non-zero flock exit (a real syscall/filesystem problem, not
# contention) is logged and propagated instead of being silently
# treated the same as ordinary contention.
#
# `insufficient data` handling: live.generate_daily_signal itself
# exits 0 for two DIFFERENT reasons -- a genuine "no signal today"
# decision, or a data problem (too few klines to warm up the strategy;
# its own module explicitly logs this as "NOT a real 'no signal today'
# decision"). Marking the marker done in the second case would leave a
# real data gap silently un-retried for the rest of the UTC day. This
# script greps its own captured output for that exact log phrase to
# tell the two apart, rather than trusting exit code 0 alone.
#
# Usage: scripts/paper-trading-daily-signal.sh
# Logs to var/live/cron.log (created if missing), same file the old
# once-daily cron line used, and the dashboard already reads.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/cron.log"
MARKER_FILE="var/live/last_signal_run_date.txt"
LOCK_FILE="var/live/.daily-signal.lock"
mkdir -p "$(dirname "$LOG_FILE")"

# Exclusive, non-blocking lock on fd 200 for the rest of this script's
# lifetime -- released automatically on exit (including any early exit
# below), no explicit unlock needed. `if flock ...; then/else` (rather
# than `flock ...; $?`) keeps this compatible with `set -e`, which
# would otherwise abort the script on flock's own non-zero exit before
# FLOCK_EXIT could ever be inspected.
exec 200>"$LOCK_FILE"
if flock -n 200; then
    FLOCK_EXIT=0
else
    FLOCK_EXIT=$?
fi
if [[ $FLOCK_EXIT -eq 1 ]]; then
    exit 0
elif [[ $FLOCK_EXIT -ne 0 ]]; then
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') paper-trading-daily-signal: flock failed unexpectedly (exit $FLOCK_EXIT), not ordinary contention" >>"$LOG_FILE"
    exit "$FLOCK_EXIT"
fi

TODAY_UTC="$(date -u +%Y-%m-%d)"
LAST_RUN_DATE="$(cat "$MARKER_FILE" 2>/dev/null || true)"

if [[ "$LAST_RUN_DATE" == "$TODAY_UTC" ]]; then
    exit 0
fi

# `set +e` around the invocation itself: this script wants to inspect
# the real exit code (and log it) rather than have `set -e` abort
# mid-script before that's possible.
set +e
RUN_OUTPUT="$( {
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') paper-trading-daily-signal: running for $TODAY_UTC (last completed run: ${LAST_RUN_DATE:-none})"
    PYTHONPATH=python BINGX_BASE_URL=https://open-api.bingx.com python/.venv/bin/python -m live.generate_daily_signal
} 2>&1 )"
PYTHON_EXIT=$?
set -e

echo "$RUN_OUTPUT" >>"$LOG_FILE"

if [[ $PYTHON_EXIT -ne 0 ]]; then
    exit "$PYTHON_EXIT"
fi

if grep -q "insufficient data" <<<"$RUN_OUTPUT"; then
    # A real data problem, not a genuine "no signal today" decision --
    # do NOT mark today done; retry on the next cron tick instead.
    exit 0
fi

# Only reached on a genuine successful run (a real decision, "no
# signal today" included) -- marks today as done. Atomic: written to a
# temp file in the same directory first, then `mv`'d into place --
# `mv` within one filesystem is a rename, atomic on POSIX, same
# guarantee generate_daily_signal.py's own `write_signal_atomically`
# relies on for the signal file itself. The trap cleans up the temp
# file if anything between its creation and the rename fails.
TMP_MARKER="$(mktemp "${MARKER_FILE}.XXXXXX")"
trap 'rm -f "$TMP_MARKER"' EXIT
echo "$TODAY_UTC" >"$TMP_MARKER"
mv "$TMP_MARKER" "$MARKER_FILE"
trap - EXIT
