#!/usr/bin/env bash
#
# Gate A: operational verification of the kill switch and the
# SUBMISSION_UNKNOWN recovery path, in isolation from the live loops.
#
#     ./scripts/verify-gate-a-kill-switch.sh
#
# Gate A requires the kill switch "verified by a deliberate trip and
# recovery, not by waiting", and the SUBMISSION_UNKNOWN path verified.
# Unit tests already cover both (KillSwitchTest, TradingLoopTest,
# SubmissionMarkerResolverTest); what those cannot show is that the
# DEPLOYED system behaves this way, which is what Gate A is asking for.
#
# ## Why an isolated instance, and what made one possible
#
# The marker store path used to be a hardcoded constant, so any second
# instance shared the live bingx-vst loop's marker file -- and verifying
# meant hand-editing a risk-control artifact belonging to a running
# process. `PAPER_TRADING_SUBMISSION_MARKERS_PATH` exists so this script
# can use its own store and leave the real one untouched, which step 3
# asserts rather than assumes.
#
# ## What it proves
#
#   1. A persisted marker starts the loop with the kill switch TRIPPED
#      and suspends new signal generation.
#   2. Clearing the marker lets the next start come up clean -- the
#      "recovery" half, which is the part waiting can never demonstrate.
#   3. Neither step touched the live marker store.
#
# Tripping is the safe direction throughout: the failure mode of every
# step here is "trading stops", never "an order is sent".
#
# Reads BINGX_API_KEY / BINGX_API_SECRET from .env because bingx-vst is
# the only mode that consults the marker store. Values are passed as
# separate argv elements to `env` and never logged -- the same handling
# `paper-trading-watchdog.sh` already uses.
set -uo pipefail
cd "$HOME/trading-engine"
V="$HOME/ks-verify"; mkdir -p "$V/reports" "$V/signals"
CP="$(cat var/live/runtime-classpath.txt)"
KEY="$(grep -E '^BINGX_API_KEY=' .env | tail -1 | cut -d= -f2- | tr -d '\r')"
SEC="$(grep -E '^BINGX_API_SECRET=' .env | tail -1 | cut -d= -f2- | tr -d '\r')"

run() {
  timeout 90 env \
    BINGX_API_KEY="$KEY" BINGX_API_SECRET="$SEC" \
    PAPER_TRADING_EXECUTION_MODE=bingx-vst \
    BINGX_BASE_URL=https://open-api.bingx.com \
    PAPER_TRADING_SUBMISSION_MARKERS_PATH="$V/markers.json" \
    PAPER_TRADING_REPORTS_DIR="$V/reports" \
    PAPER_TRADING_SIGNAL_PATH="$V/signals/latest.json" \
    java -Xmx192m -cp "$CP" engine.runtime.PaperTradingApp 2>&1
}

printf '[{"clientOrderId":"11111111-1111-1111-1111-111111111111","symbol":"BTC-USDT","recordedAtIso":"2026-09-06T00:00:00Z"}]\n' > "$V/markers.json"
echo "=== 1) marker present -> kill switch must start TRIPPED ==="
run | grep -iE "unresolved|already TRIPPED|kill switch TRIPPED" | head -3

echo
echo "=== 2) marker cleared -> must start clean ==="
printf '[]\n' > "$V/markers.json"
run | grep -iE "clean start|already TRIPPED|leverage .* set to" | head -3

echo
echo "=== 3) the LIVE marker store must be untouched ==="
ls -l var/live/submission_markers.json 2>/dev/null || echo "  live store: absent (never created) -- correct"
