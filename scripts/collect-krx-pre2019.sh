#!/usr/bin/env bash
# Backfills the RESERVED pre-2019 KRX daily panel (1991-08-28 .. 2018-12-31).
#
# RUNS ON THE INSTANCE. ~485,000 requests, ~225 hours at the measured
# 0.5-0.7/s, and resumable -- `already_done` reads the scan database, not a
# sidecar, so a reboot or a dropped session costs only the code in flight.
#
# ## Why this is a script rather than a typed command
#
# The runbook's §8b typed procedure works and needs no `.env`, but it puts a
# human on every restart of a nine-day run. The failure mode is not a lost
# session; it is nobody noticing for a day. So this exists to be invoked by
# cron, which supplies no environment -- the same reason the `.env` fallback
# in the other collectors exists, and CLAUDE.md's standing answer applies
# unchanged: an env-only policy is a better posture and must be taken across
# all collectors at once, never one script at a time. Operator decision,
# 2026-09-30, after being shown both options and their costs.
#
# ## What it deliberately does NOT do
#
# It holds no session logic of its own. `data.krx_scan` already refuses to
# START during the KRX continuous session and PAUSES a run that reaches one,
# which is the behaviour that matters for a multi-day pass. Reimplementing
# either here would be a second copy that drifts.
#
# It writes no completion marker. A finished scan re-invoked simply skips
# every code it has done and exits in seconds, so the marker would only add
# a state file that can be wrong. It also keeps a second pass's remaining
# `failed:rejected` work visible instead of declaring the panel done.
#
# The panel is HARDCODED. `scan_progress` keys on `code` alone, so a second
# panel in this database would skip every finished code and collect nothing;
# `claim_panel` refuses it. A `--panel-start` argument here would only make
# that refusal reachable by typo.
#
# Read-only quotation TRs. No order can be placed from this path, enforced
# by python/tests/test_kis_probe_cannot_trade.py.
#
# Usage (cron), hourly -- it is a no-op while a pass is already running or
# while KRX is open:
#   17 * * * * /home/minjun4897/trading-engine/scripts/collect-krx-pre2019.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/krx-pre2019.log"
LOCK_FILE="var/krx-pre2019.lock"
SCAN_DB="python/data/var/krx_scan_pre2019.sqlite3"
UNIVERSE_DB="python/data/var/klines.sqlite3"
PANEL_START=19910828
PANEL_END=20181231

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$SCAN_DB")"

log() { echo "$(date -Is) $1" >>"$LOG_FILE"; }

PY=(env PYTHONPATH=python python/.venv/bin/python)

# ONE WRITER, ENFORCED RATHER THAN ASSUMED. Two passes against one scan
# database both write `scan_progress`, which keys on `code`, so the second
# would record outcomes for codes the first is still fetching. Hourly cron
# makes an overlap the normal case rather than an unlucky one.
#
# `-E 200` SEPARATES CONTENTION FROM A BROKEN LOCK, and without it the two are
# indistinguishable. `flock` returns its own non-zero status for its own
# failures -- measured: a bad file descriptor gives **65** -- so treating every
# non-zero as "already running" would turn an unusable lock file into a silent
# exit 0 for ever, on a pass nobody is watching. With `-E 200`, contention is
# exactly 200 and anything else is a real failure that says so.
#
# Exit 0 on contention, though: an already-running pass is this script working,
# not failing, and hourly cron would otherwise mail the operator every hour.
# `|| lock_rc=$?`, NOT `if ! flock ...; then rc=$?`. The second form reads
# naturally and throws the status away: `$?` there is the status of `! flock`,
# which is 0 whenever flock failed. Caught by running it -- the first version of
# this block reported `rc=0` for both contention and a bad descriptor.
exec 9>"$LOCK_FILE"
lock_rc=0
flock -n -E 200 9 || lock_rc=$?
if [ "$lock_rc" -eq 200 ]; then
    log "a pass already holds the lock -- nothing to do"
    exit 0
elif [ "$lock_rc" -ne 0 ]; then
    log "ERROR: flock failed with $lock_rc (not contention) -- refusing to run without the lock"
    exit "$lock_rc"
fi

# CREDENTIALS COME FROM THE ENVIRONMENT IF IT SUPPLIES THEM. The .env read
# is the fallback for cron, which supplies none. Never `source`d, so nothing
# in .env can execute; the CRLF this repo's .env carries is stripped, because
# a trailing \r on a key once reached a JDK exception message with the real
# value inside it. Values are never echoed -- only presence is checked.
get_env_var() {
    [ -r "$REPO_ROOT/.env" ] || return 0
    # `|| true` is load-bearing. Under `set -euo pipefail` a grep that
    # matches nothing fails the pipeline, and a failing command substitution
    # inside an assignment kills the script THERE -- before the explicit
    # "credentials missing" check below can say why. Same finding as
    # PR #178's on the other collectors.
    tr -d '\r' <"$REPO_ROOT/.env" | grep -E "^${1}=" | tail -n1 | cut -d'=' -f2- || true
}

KIS_APP_KEY="${KIS_APP_KEY:-$(get_env_var KIS_APP_KEY)}"
KIS_APP_SECRET="${KIS_APP_SECRET:-$(get_env_var KIS_APP_SECRET)}"
if [ -z "$KIS_APP_KEY" ] || [ -z "$KIS_APP_SECRET" ]; then
    log "ERROR: KIS_APP_KEY/KIS_APP_SECRET are neither in the environment nor in .env -- refusing to run (values never logged)"
    exit 1
fi
export KIS_APP_KEY KIS_APP_SECRET

# ASKED DIRECTLY, NOT INFERRED FROM AN EXIT CODE. `krx_scan` exits 2 when it
# refuses to start during the session -- and argparse also exits 2 on a bad
# argument, so treating 2 as "skipped, fine" would make a typo in the flags
# above read as a clean skip for ever. One extra call removes the ambiguity
# and lets every non-zero status below be a real failure.
if "${PY[@]}" -c 'import sys
from data.krx_scan import in_continuous_session
sys.exit(0 if in_continuous_session() else 1)'; then
    log "KRX continuous session -- not starting a pass (the collectors share this app key)"
    exit 0
fi

log "starting a pass over ${PANEL_START}..${PANEL_END}"

# NO `--page-days`. The width is a property of the panel's era -- KRX traded
# Saturdays until 2000, so a 120-day page lands ON the 100-row cap there and
# `failed:capped` is deliberately not retryable. `default_page_days` resolves
# 90 for this panel; passing a width here could only override it wrongly.
set +e
"${PY[@]}" -m data.krx_scan --scan \
    --panel-start "$PANEL_START" --panel-end "$PANEL_END" \
    --db-path "$SCAN_DB" \
    --universe-db "$UNIVERSE_DB" >>"$LOG_FILE" 2>&1
rc=$?
set -e

if [ "$rc" -ne 0 ]; then
    log "pass exited $rc -- see the lines above; the next cron tick resumes"
    exit "$rc"
fi
log "pass exited cleanly"
