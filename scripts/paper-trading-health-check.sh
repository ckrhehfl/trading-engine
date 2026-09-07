#!/usr/bin/env bash
#
# Runs `live.health_check` on a schedule and keeps a bounded log.
#
#     */15 * * * * /path/to/trading-engine/scripts/paper-trading-health-check.sh
#
# Every 15 minutes rather than 5: the conditions it checks for persist
# (a wedged loop, a tripped kill switch, a missing report), so three
# times the watchdog's cadence is plenty to notice, at a third of the
# log volume.
#
# ## What this does, and what it deliberately does not
#
# It judges and it writes the verdict down. It does **not** reach anyone
# who is not looking -- `live.health_check` ships with no outbound
# channel, because every channel needs an account and a credential on
# this box, and the operator's decision (2026-09-06) is to add one when
# there is P&L worth pushing rather than for infrastructure warnings.
#
# Stated plainly rather than implied, because a monitoring script
# believed to alert and not alerting is worse than none at all.
#
# What it buys today is the thing that is actually missing: a **durable
# history** in var/live/health-alerts.jsonl. The three dashboards all
# show the state right now; none of them can answer "what happened
# overnight", which is the question that matters for a system running
# unattended and the one an operator (or an AI session with SSH access)
# arrives asking.
#
# ## Why it is safe to run unattended
#
# Strictly read-only with respect to the trading system: it reads the
# dashboard's view (tmux panes, report files, logs) and writes only its
# own two files under var/live/. It cannot start, stop, restart or
# signal either loop, and it places no order. If this script fails, the
# loops are exactly as they were.

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/health-check.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Keep the log bounded. This runs 96 times a day forever on a 30 GB
# disk, and one of the conditions it checks for is that disk filling up
# -- a monitor that causes the problem it watches for would be its own
# kind of joke. One rotation, not a chain: the JSONL alert log is the
# durable record, this is just the run transcript.
MAX_LOG_BYTES=$((2 * 1024 * 1024))
if [[ -f "$LOG_FILE" ]] && [[ "$(wc -c <"$LOG_FILE")" -gt $MAX_LOG_BYTES ]]; then
    mv "$LOG_FILE" "$LOG_FILE.1"
fi

# `--quiet` so a healthy run prints nothing and this log only grows when
# something is wrong. The exit code still reports the real state, so
# errexit would abort here on any open condition -- hence capturing it
# rather than letting `set -e` act on it. Restored immediately after,
# so the rest of the script stays fail-closed.
set +e
OUTPUT="$(PYTHONPATH=python python/.venv/bin/python -m live.health_check --quiet 2>&1)"
CHECK_EXIT=$?
set -e

if [[ -n "$OUTPUT" ]]; then
    {
        printf '%s health-check exit=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$CHECK_EXIT"
        printf '%s\n' "$OUTPUT"
    } >>"$LOG_FILE"
fi

exit "$CHECK_EXIT"
