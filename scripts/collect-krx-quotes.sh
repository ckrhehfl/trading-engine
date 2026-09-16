#!/usr/bin/env bash
# Samples the top of the Korean order book -- the measurement rd-q §8 item
# 1 says must replace its single closing snapshot.
#
# rd-q measured the quoted spread ONCE, at one close, and said plainly
# that "one snapshot cannot carry a registration". A representative cost
# floor needs many samples across many sessions, and that is what a cron
# tick buys: nothing here computes anything, it only accumulates.
#
# IT ALSO COLLECTS DEPTH, which nothing in this project ever has. rd-q
# described its winners' futures books as "27x deeper" on a figure that
# was really a ratio of CUMULATIVE VOLUME (acml_vol); resting size at the
# touch sits on the same wire and had never been read. Caught on review of
# PR #176, corrected in rd-q §1, and closed properly here.
#
# NOT BACKFILLABLE, AND NOT EVEN ROLLING. An order book is not stored by
# anybody -- there is no historical endpoint to recover a spread from, at
# any price. Unlike intraday bars (a rolling ~250 sessions) or 투자자별
# 매매동향 (a rolling 30 rows), a sample not taken is gone the same second.
# That is the strongest version of the argument that already justifies the
# other two Korean collectors.
#
# DURING THE SESSION ONLY. KRX trades continuously 09:00-15:20 KST; the
# 15:20-15:30 closing call auction has no continuous book. KIS answers
# outside those hours anyway, with the LAST book -- verified directly at
# 23:00 KST, which returned 삼성전자 at 253,500/253,000 -- so an off-hours
# tick yields plausible numbers from a different market state. The module
# refuses by default and exits 0, so an out-of-hours cron tick is a no-op
# rather than a logged failure.
#
# RUNS LOCALLY, NOT ON THE INSTANCE, for the same reason as the other two
# Korean collectors: KIS works from either, and one database of record
# beats two that have to be reconciled.
#
# Read-only quotation TRs. No order can be placed from this path, enforced
# by python/tests/test_kis_probe_cannot_trade.py.
#
# Usage (cron), every 30 minutes through the session. The spread is not
# constant across a session -- it is widest at the open and tightest into
# the close -- so samples are spread across it rather than taken at one
# time of day, which would measure that hour and not the session:
#   */30 9-15 * * 1-5 /mnt/c/Dev/trading-engine/scripts/collect-krx-quotes.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/krx-quotes.log"
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

# The same union universe as the other two Korean collectors -- KR-10 plus
# rd-r's futures-liquidity top 10. A cost floor has to be measured for the
# names a strategy would actually trade.
UNIVERSE="005930,000660,000720,007390,009150,028300,051910,064350,068270,207940,005380,034020,006400,042700,035420,000270,402340,012450"

# Both instruments per name, because rd-q's whole finding is that the
# cheaper one differs BY NAME and the comparison needs both sides measured
# at the same instant.
#
# THE EXPIRY IS NOT PASSED IN. An earlier version hardcoded one, which
# degrades in the worst possible way once the contract rolls: an expired
# contract answers with an empty book, the sampler correctly reports "not
# quoted", and the collector keeps exiting 0 while silently recording spot
# alone. Nothing in this log would have said so. --futures resolves the
# front month from the master on every run and exits non-zero if futures
# were asked for and not one contract was quoted.
PYTHONPATH=python python/.venv/bin/python -m data.krx_quote_sampler \
    --symbols "$UNIVERSE" --futures >>"$LOG_FILE" 2>&1
