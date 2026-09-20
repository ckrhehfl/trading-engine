#!/usr/bin/env bash
# Collects KRX 투자자별 매매동향 and the listed-universe snapshot -- both of
# which CANNOT be backfilled.
#
# `inquire-investor` returns exactly 30 rows and accepts no date parameter,
# so there is no request that reaches day 31. The master files list
# currently-listed symbols only, so a snapshot not taken today can never be
# reconstructed. Same argument as collect-positioning.sh, different venue.
#
# RUNS ON THE INSTANCE. This header said the opposite until 2026-09-20 --
# "RUNS LOCALLY, NOT ON THE INSTANCE ... kept local so there is a single
# database of record" -- which was true when written and was overtaken by
# the 2026-09-17 decision that moved ALL KRX/KIS collection to the
# instance and left nothing scheduled locally. The single-database-of-
# record argument is unchanged and is now satisfied the other way round:
# the instance owns KRX/KIS, and a local session reads a copy via
# scripts/sync-krx-from-instance.sh. Corrected rather than deleted,
# because a comment that contradicts the crontab is how a future session
# ends up running a second writer.
#
# AFTER THE CLOSE, deliberately. 투자자별 매매동향 is 가집계 (provisional)
# during the session and finalised only afterwards; a mid-session snapshot
# records a number that will change, and INSERT OR IGNORE would then keep
# the provisional one forever. KRX closes 15:30 KST.
#
# Read-only quotation TRs. No order can be placed from this path, enforced
# by python/tests/test_kis_probe_cannot_trade.py.
#
# Usage (cron), 16:10 KST on weekdays -- after the close, before midnight:
#   10 16 * * 1-5 /mnt/c/Dev/trading-engine/scripts/collect-krx-flow.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/krx-flow.log"
mkdir -p "$(dirname "$LOG_FILE")"

# CREDENTIALS COME FROM THE ENVIRONMENT IF IT SUPPLIES THEM. The .env read
# below is a fallback for an interactive or cron invocation that does not,
# so an operator can inject secrets from a manager, a systemd unit, or a
# wrapper without editing this script. Raised on review of PR #168.
#
# The fallback itself never executes .env as shell code (no `source`) and
# strips the CRLF this repo's .env carries -- the same accessor
# scripts/kis-paper.sh uses, which was reviewed for exactly this purpose.
# Values are never echoed; only presence is checked.
get_env_var() {
    [ -r "$REPO_ROOT/.env" ] || return 0
    # `|| true` is load-bearing, not defensive noise. Under `set -euo
    # pipefail`, grep finding no match fails the pipeline, and a failing
    # command substitution inside an assignment kills the script THERE --
    # before the explicit "credentials missing" check below ever runs. The
    # one case that check exists for (a key rotated, renamed or removed)
    # would therefore exit silently with no log line saying why, on a
    # collector whose series cannot be backfilled. Found on review of
    # PR #178; reproduced before fixing.
    tr -d '\r' <"$REPO_ROOT/.env" | grep -E "^${1}=" | tail -n1 | cut -d'=' -f2- || true
}

KIS_APP_KEY="${KIS_APP_KEY:-$(get_env_var KIS_APP_KEY)}"
KIS_APP_SECRET="${KIS_APP_SECRET:-$(get_env_var KIS_APP_SECRET)}"
if [ -z "$KIS_APP_KEY" ] || [ -z "$KIS_APP_SECRET" ]; then
    echo "$(date -Is) ERROR: KIS_APP_KEY/KIS_APP_SECRET are neither in the environment nor in .env -- refusing to run (values never logged)" >>"$LOG_FILE"
    exit 1
fi
export KIS_APP_KEY KIS_APP_SECRET

# The universe snapshot first: the flow collector's --universe mode reads
# it, so a fresh clone must not depend on run order.
PYTHONPATH=python python/.venv/bin/python -m data.krx_universe --snapshot >>"$LOG_FILE" 2>&1

# KR-10 while Gate A is in progress; widen to --universe once it completes.
# The 30-trading-day rolling window means widening later loses nothing from
# the widening date forward, and a 2,718-call burst colliding with a
# kis-paper tick is uptime Gate A cannot recover.
# See .planning/rd-d-discovery-mode-and-the-full-universe.md section 3.
# THE UNIVERSE IS THE UNION of KR-10 (ms-e, ranked on SPOT 거래대금) and
# the futures-liquidity top 10 (rd-r, ranked on median daily front-month
# futures 거래대금 over 2026Q1). Eighteen names: the two lists share only
# 005930 and 000660.
#
# The union, not a replacement, for two separate reasons:
#
#  - the eight KR-10 names that are not futures-liquid are still tradeable
#    as SPOT, at rd-q's measured ~35.6bp round trip. Dropping them would
#    throw away a year of collected history to save nothing -- a session
#    already stored costs zero API calls to skip.
#  - the eight new names have NO intraday history here at all, and the
#    endpoint only reaches back a rolling ~250 trading days. Every day
#    they are not collected is a session that cannot be recovered later.
#
# rd-r's ranking is a POINT-IN-TIME selection with an exit rule (a member
# leaves on a delisting announcement or a failure to resume, never on
# "it got less liquid later"), so this list changes only for those
# reasons -- not because a later quarter reshuffles the ranking.
UNIVERSE="005930,000660,000720,007390,009150,028300,051910,064350,068270,207940,005380,034020,006400,042700,035420,000270,402340,012450"

PYTHONPATH=python python/.venv/bin/python -m data.kis_investor_flow \
    --symbols "$UNIVERSE" >>"$LOG_FILE" 2>&1

# The delisted universe LAST, and allowed to fail. Two deliberate
# differences from everything above it, both about not letting a
# nice-to-have take down the thing that cannot be backfilled:
#
#  - IT RUNS LAST. Under `set -euo pipefail` any step that fails aborts
#    the script, so putting this first or in the middle would mean a KRX
#    portal outage silently costing that day's 투자자별 매매동향 -- a
#    series with a 30-row rolling horizon and no way back.
#  - IT MAY FAIL. `|| echo` keeps the exit status clean, because unlike
#    every other series here this one IS re-fetchable: the finder
#    publishes the whole delisted history every day and only grows. A
#    missed day costs a dated row, not data.
#
# Why snapshot it at all, then: the finder carries no delisting date, so
# the first date a name appears here is the only bound this project has
# on when KRX published it as delisted -- which matters for a name KIS
# has stopped serving. See .planning/rd-w-the-delisted-universe.md.
PYTHONPATH=python python/.venv/bin/python -m data.krx_delisted --snapshot \
    >>"$LOG_FILE" 2>&1 \
    || echo "$(date -Is) WARNING: krx_delisted snapshot failed; re-fetchable tomorrow, not a data loss" >>"$LOG_FILE"
