#!/usr/bin/env bash
# Pulls the KRX series down from the GCP instance that now collects them.
#
# WHY THIS DIRECTION. On 2026-09-17 KRX/KIS collection moved to the
# instance, because this machine is not reliably on during the KRX
# session (09:00-15:20 KST) and an order-book sample not taken is gone
# the same second -- there is no historical endpoint for a spread at any
# price. But RESEARCH still runs here: the instance is a 955MB e2-micro
# already carrying two paper-trading JVMs, and a backtest does not fit on
# it. So collection goes up and data comes back.
#
# ONE WRITER PER SERIES, which is what actually stops two databases
# drifting:
#   - the INSTANCE owns KRX/KIS  (klines KRX:*, positioning KRX:*, krx_universe)
#   - THIS MACHINE owns Binance  (collect-positioning.sh)
# Binance cannot move and this is not a preference: the instance is
# geo-blocked, every endpoint returning HTTP 451, re-verified 2026-09-17.
# CLAUDE.md's own rule covers it -- "a collector's home is chosen per
# venue, not once for the project."
#
# ADDITIVE ONLY. `data.sync_krx` uses INSERT OR IGNORE and never UPDATEs
# or DELETEs, and copies only KRX-prefixed rows. The instance's file
# still holds a frozen copy of this machine's Binance rows from the
# migration snapshot; copying those back would resurrect stale
# positioning over a series that has advanced here ever since.
#
# SAFE TO RE-RUN. Idempotent -- a second run moves nothing.
#
# Run it before any research that reads KRX data.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

INSTANCE="${KRX_INSTANCE:-paper-trading}"
ZONE="${KRX_INSTANCE_ZONE:-us-central1-a}"
REMOTE_REPO="${KRX_REMOTE_REPO:-/home/minjun4897/trading-engine}"
REMOTE_USER="${KRX_REMOTE_USER:-minjun4897}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "$(date -Is) taking a consistent snapshot on $INSTANCE"

# `.backup` rather than `cp`: the instance's DB is written by cron
# collectors and a raw copy of a live SQLite file can tear.
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --command "
  sudo -u $REMOTE_USER bash -lc '
    cd $REMOTE_REPO/python
    PYTHONPATH=. .venv/bin/python -c \"
import sqlite3
from data._paths import DEFAULT_DB_PATH
src = sqlite3.connect(\\\"file:\\\" + DEFAULT_DB_PATH + \\\"?mode=ro\\\", uri=True)
dst = sqlite3.connect(\\\"/tmp/krx-sync.sqlite3\\\")
src.backup(dst)
dst.close(); src.close()
\"
    gzip -1 -f /tmp/krx-sync.sqlite3
    chmod 644 /tmp/krx-sync.sqlite3.gz
  '
" >/dev/null

echo "$(date -Is) downloading"
gcloud compute scp "$INSTANCE:/tmp/krx-sync.sqlite3.gz" "$WORK/snap.gz" --zone "$ZONE" >/dev/null

gunzip -c "$WORK/snap.gz" > "$WORK/snap.sqlite3"

# Verify before merging: a truncated download is a plausible-looking file.
python3 -c "
import sqlite3, sys
c = sqlite3.connect('file:$WORK/snap.sqlite3?mode=ro', uri=True)
if c.execute('pragma integrity_check').fetchone()[0] != 'ok':
    sys.exit('downloaded snapshot failed its integrity check')
"

echo "$(date -Is) merging KRX rows into the local database"
PYTHONPATH=python python/.venv/bin/python -m data.sync_krx --source "$WORK/snap.sqlite3"
