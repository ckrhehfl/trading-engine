#!/usr/bin/env bash
# Collects the latest KRX minute bars, which CANNOT be backfilled past a
# rolling ~250 trading days.
#
# `inquire-time-dailychartprice` serves a window that advances one session
# every session, so a session not collected is permanently lost. The
# one-time backfill (.planning/rd-o-krx-intraday-backfill.md) secured the
# window as it stood; this keeps it from decaying back into the same
# problem, which is the only reason a one-time backfill was ever needed.
#
# Same family as collect-krx-flow.sh: a Korean series whose history has an
# expiry date. The difference is that flow cannot be backfilled AT ALL
# (30 rows, no date parameter) while intraday can be, for about a year --
# which makes a missed day here recoverable for a while and permanent
# after that.
#
# RUNS LOCALLY, NOT ON THE INSTANCE, for the same reason as
# collect-krx-flow.sh: KIS works fine from the instance, and this is kept
# local so there is a single database of record rather than two that must
# be reconciled.
#
# AFTER THE CLOSE. KRX closes 15:30 KST and the last page of a session is
# only complete once it has. Unlike 투자자별 매매동향 these bars are not
# 가집계, so the timing is about completeness rather than provisionality.
#
# IDEMPOTENT AND CHEAP TO REPEAT. A session already spanning the day costs
# zero API calls, so a re-run after a failure fetches only what is missing
# and a run on a holiday fetches nothing. That is what makes it safe to
# schedule daily rather than only on trading days.
#
# Read-only quotation TRs. No order can be placed from this path, enforced
# by python/tests/test_kis_probe_cannot_trade.py.
#
# Usage (cron), 16:20 KST on weekdays -- after the close, and ten minutes
# after collect-krx-flow.sh so the two do not contend for the same SQLite
# write lock:
#   20 16 * * 1-5 /mnt/c/Dev/trading-engine/scripts/collect-krx-intraday.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/krx-intraday.log"
mkdir -p "$(dirname "$LOG_FILE")"

# CREDENTIALS COME FROM THE ENVIRONMENT IF IT SUPPLIES THEM. The .env read
# below is a fallback for an interactive or cron invocation that does not,
# so an operator can inject secrets from a manager, a systemd unit, or a
# wrapper without editing this script.
#
# The fallback never executes .env as shell code (no `source`) and strips
# the CRLF this repo's .env carries. Values are never echoed; only
# presence is checked.
get_env_var() {
    [ -r "$REPO_ROOT/.env" ] || return 0
    tr -d '\r' <"$REPO_ROOT/.env" | grep -E "^${1}=" | tail -n1 | cut -d'=' -f2-
}

KIS_APP_KEY="${KIS_APP_KEY:-$(get_env_var KIS_APP_KEY)}"
KIS_APP_SECRET="${KIS_APP_SECRET:-$(get_env_var KIS_APP_SECRET)}"
if [ -z "$KIS_APP_KEY" ] || [ -z "$KIS_APP_SECRET" ]; then
    echo "$(date -Is) ERROR: KIS_APP_KEY/KIS_APP_SECRET are neither in the environment nor in .env -- refusing to run (values never logged)" >>"$LOG_FILE"
    exit 1
fi
export KIS_APP_KEY KIS_APP_SECRET

# The daily bars first: they are the reference calendar the intraday
# coverage check is judged against, so a stale calendar would silently
# stop the newest sessions from being attempted at all. Cheap -- one call
# per symbol for a short window.
KR10="005930,000660,000720,007390,009150,028300,051910,064350,068270,207940"
START="$(date -d '10 days ago' +%Y%m%d)"
END="$(date +%Y%m%d)"

PYTHONPATH=python python/.venv/bin/python -m data.backfill_kis \
    --symbols "$KR10" --index 0001 --start "$START" --end "$END" \
    --adjusted 0 >>"$LOG_FILE" 2>&1

# Then the minute bars, across the WHOLE rolling window rather than the
# last few sessions.
#
# `--sessions N` limits the candidate dates *before* the already-collected
# check, so a small N is not a cap on work -- it is a cap on what can ever
# be caught up. An earlier version passed `--sessions 5`, which meant that
# any gap longer than five trading days (a holiday week, a machine left
# off, a failed run nobody noticed) became PERMANENT: those sessions would
# never be candidates again, and the rolling window would carry them off.
# For a collector whose entire purpose is to stop exactly that, it was the
# wrong default. Caught on review of PR #174.
#
# The full window costs almost nothing extra: a session already spanning
# the day is skipped on a local index lookup with no API call, so the
# added work is ~2,500 SQLite range queries -- under a second -- and the
# API calls are only for sessions genuinely missing.
PYTHONPATH=python python/.venv/bin/python -m data.backfill_kis_intraday \
    --symbols "$KR10" >>"$LOG_FILE" 2>&1
