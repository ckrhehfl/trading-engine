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
# already held exits 0 immediately (not an error -- another instance is
# already handling this tick's work).
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
# lifetime -- released automatically on exit (including the early skip
# below and any failure), no explicit unlock needed.
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    exit 0
fi

TODAY_UTC="$(date -u +%Y-%m-%d)"
LAST_RUN_DATE="$(cat "$MARKER_FILE" 2>/dev/null || true)"

if [[ "$LAST_RUN_DATE" == "$TODAY_UTC" ]]; then
    exit 0
fi

{
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') paper-trading-daily-signal: running for $TODAY_UTC (last completed run: ${LAST_RUN_DATE:-none})"
    PYTHONPATH=python BINGX_BASE_URL=https://open-api.bingx.com python/.venv/bin/python -m live.generate_daily_signal
} >>"$LOG_FILE" 2>&1

# Only reached if the block above exited 0 (set -e aborts the script
# before this line on any failure) -- marks today as genuinely done.
# Atomic: written to a temp file in the same directory first, then
# `mv`'d into place -- `mv` within one filesystem is a rename, atomic
# on POSIX, same guarantee generate_daily_signal.py's own
# `write_signal_atomically` relies on for the signal file itself. The
# trap cleans up the temp file if anything between its creation and the
# rename fails.
TMP_MARKER="$(mktemp "${MARKER_FILE}.XXXXXX")"
trap 'rm -f "$TMP_MARKER"' EXIT
echo "$TODAY_UTC" >"$TMP_MARKER"
mv "$TMP_MARKER" "$MARKER_FILE"
trap - EXIT
