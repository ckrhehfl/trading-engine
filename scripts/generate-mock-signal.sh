#!/usr/bin/env bash
#
# One mock order intent per invocation, for Gate A's >= 200 order-event
# requirement. Driven from cron on the same 5-minute cadence as the
# watchdog, so the floor is cleared inside a day and the remaining 14
# days are about uptime -- which is what they are for.
#
# Does nothing unless PAPER_TRADING_MOCK_SIGNALS=1, so installing the
# cron line is not the same as enabling it. The generator itself refuses
# to write anywhere inside a real strategy's signal tree; see
# `python/live/generate_mock_signal.py`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_FILE="var/live/mock-signal.log"
mkdir -p "$(dirname "$LOG_FILE")"

if [[ "${PAPER_TRADING_MOCK_SIGNALS:-0}" != "1" ]]; then
    exit 0
fi

PYTHONPATH=python python/.venv/bin/python -m live.generate_mock_signal >>"$LOG_FILE" 2>&1
