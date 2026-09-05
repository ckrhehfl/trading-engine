#!/usr/bin/env bash
# Collects the Binance positioning series that CANNOT be backfilled.
#
# The /futures/data/ endpoints retain roughly 30 days. Whatever this does
# not capture is gone permanently -- unlike klines, a later backfill
# cannot recover it. That is the entire reason this runs on a schedule
# before any strategy needs the data.
#
# Read-only public market data: no credentials are read, no key is sent,
# and nothing here can place an order.
#
# Idempotent, so a re-run over an overlapping window inserts nothing. A
# 30-minute cadence is far inside the 1000-requests/5-minutes limit and
# leaves plenty of overlap for missed runs to self-heal from.
#
# Usage (cron):
#   */30 * * * * /mnt/c/Dev/trading-engine/scripts/collect-positioning.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/positioning.log"
mkdir -p "$(dirname "$LOG_FILE")"

PYTHONPATH=python python/.venv/bin/python -m data.collect_positioning >>"$LOG_FILE" 2>&1
