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

# The daily bars first: they are the reference calendar the intraday
# coverage check is judged against, so a stale calendar would silently
# stop the newest sessions from being attempted at all. Cheap -- one call
# per symbol for a short window.
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
# **Not a few days.** A ten-day start was a cap on what could ever be
# caught up, not a cap on work -- the same defect `--sessions 5` was for the
# minute bars, fixed in PR #174, and the daily half kept it. A daily hole
# older than ten days (a holiday week plus an outage, a failed run nobody
# noticed, or a malformed row) was never a candidate again, and nothing
# reported it: the collector re-exec'd faithfully every day and looked only
# at a window the hole had already left.
#
# It costs almost nothing for the 18 symbols, for the same measured reason
# the minute-bar comment below gives: `backfill_symbol` skips a window whose
# expected trading days are already stored **without an API call**, so the
# added work there is a handful of SQLite range queries.
#
# **The reference index is the one real cost, and the arithmetic is stated
# rather than estimated** (asked for on review of PR #202). It is fetched
# with no `reference_days` to compare against, so every one of its windows is
# re-requested daily. Its window is `DEFAULT_INDEX_WINDOW_DAYS = 60`, not the
# equities' 120, so 400 days is **7 pages/day against the previous 1 -- six
# extra calls**, not the ~4 an earlier version of this comment guessed at.
#
# Six calls a day is inside the headroom by a wide margin: this same script
# already makes hundreds (18 symbols x ~380 minute-bars per session at 120
# rows per call, plus the daily pages), and the limit that actually bites on
# this key is `/oauth2/tokenP`'s EGW00133 -- roughly three issuances a minute
# -- which these six do not touch at all, because `KisSession` resolves one
# token through the cache for the whole run.
#
# 400 days rather than the whole reachable history because the endpoint
# pages back to 1991 and this is a daily catch-up, not a backfill: an
# outage long enough to outlast a year is a deliberate re-run
# (`data.backfill_kis --start`), not something a cron job should quietly
# repair. Daily bars are re-fetchable at any time, unlike the minute bars
# and the order book, so the horizon is a convenience rather than a
# deadline.
START="$(date -d '400 days ago' +%Y%m%d)"
END="$(date +%Y%m%d)"

PYTHONPATH=python python/.venv/bin/python -m data.backfill_kis \
    --symbols "$UNIVERSE" --index 0001 --start "$START" --end "$END" \
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
    --symbols "$UNIVERSE" >>"$LOG_FILE" 2>&1
