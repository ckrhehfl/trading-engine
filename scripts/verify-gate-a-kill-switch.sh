#!/usr/bin/env bash
#
# Gate A: operational verification of the kill switch and the
# SUBMISSION_UNKNOWN recovery path, in isolation from the live loops.
#
#     export BINGX_API_KEY=...  BINGX_API_SECRET=...
#     ./scripts/verify-gate-a-kill-switch.sh
#
# Gate A requires the kill switch "verified by a deliberate trip and
# recovery, not by waiting", and the SUBMISSION_UNKNOWN path verified.
# Unit tests already cover both (KillSwitchTest, TradingLoopTest,
# SubmissionMarkerResolverTest); what those cannot show is that the
# DEPLOYED system behaves this way, which is what Gate A is asking for.
#
# ## What it proves
#
#   1. A persisted marker starts the loop with the kill switch TRIPPED
#      and suspends new signal generation.
#   2. Clearing the marker lets the next start come up clean -- the
#      "recovery" half, which waiting can never demonstrate.
#   3. The live marker store is byte-identical afterwards.
#
# Tripping is the safe direction throughout: every step's failure mode
# is "trading stops", never "an order is sent".
#
# ## Credentials come from the environment, never from .env
#
# This script does not read, stat or parse `.env`. CLAUDE.md forbids it,
# and the fact that `paper-trading-watchdog.sh` predates that rule does
# not extend it to new scripts. Export both variables yourself:
#
#     read -rs BINGX_API_KEY && export BINGX_API_KEY
#     read -rs BINGX_API_SECRET && export BINGX_API_SECRET
#
# `read -rs` keeps them out of shell history. They are passed to `java`
# as separate argv elements to `env` and never printed.
#
# ## Isolation, and why it needs an acknowledgement
#
# `PAPER_TRADING_SUBMISSION_MARKERS_PATH` points the marker store at a
# fresh `mktemp -d`, so the live store is untouched. That override is
# NOT fail-safe on its own -- aiming it at an empty location means a
# real unresolved marker in the default store is never seen and the
# process starts with the kill switch clear. So it is refused unless
# PAPER_TRADING_ALLOW_ISOLATED_MARKERS carries the acknowledgement
# below, which is why this script sets both.
#
set -Eeuo pipefail

ACK="i-understand-this-bypasses-marker-review"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LIVE_MARKERS="var/live/submission_markers.json"
CLASSPATH_CACHE="var/live/runtime-classpath.txt"

fail() { printf '\n  FAIL  %s\n\n' "$1" >&2; exit 1; }
ok()   { printf '  ok    %s\n' "$1"; }

[[ -n "${BINGX_API_KEY:-}" && -n "${BINGX_API_SECRET:-}" ]] || fail \
  "export BINGX_API_KEY and BINGX_API_SECRET first. This script never reads .env.
        Use 'read -rs BINGX_API_KEY && export BINGX_API_KEY' to keep them out of
        shell history. bingx-vst is the only mode that consults the marker store."

[[ -s "$CLASSPATH_CACHE" ]] || fail \
  "no runtime classpath at $CLASSPATH_CACHE -- run ./scripts/vps-bootstrap.sh first"
CP="$(cat "$CLASSPATH_CACHE")"

# A fresh directory per run, never a reused fixed path. A stable
# location could already hold a symlink pointing at the live marker
# store, and writing "through" it would corrupt the very file step 3
# checks -- after the damage was done.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/gate-a-verify.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
mkdir -p "$WORK/reports" "$WORK/signals"
MARKERS="$WORK/markers.json"

# Fingerprint the live store BEFORE anything runs. Listing it afterwards
# proves nothing: the file could have existed, been rewritten, and still
# be listed.
live_fingerprint() {
    if [[ -e "$LIVE_MARKERS" ]]; then
        sha256sum "$LIVE_MARKERS" | cut -d' ' -f1
    else
        echo "ABSENT"
    fi
}
BEFORE="$(live_fingerprint)"

run() {
    local log="$1"
    # `timeout` returns 124 when it does its job, which is the expected
    # outcome here -- the loop runs forever by design. Any other non-zero
    # code is a real failure and is surfaced by the pattern checks below.
    timeout 90 env \
        BINGX_API_KEY="$BINGX_API_KEY" \
        BINGX_API_SECRET="$BINGX_API_SECRET" \
        PAPER_TRADING_EXECUTION_MODE=bingx-vst \
        BINGX_BASE_URL=https://open-api.bingx.com \
        PAPER_TRADING_SUBMISSION_MARKERS_PATH="$MARKERS" \
        PAPER_TRADING_ALLOW_ISOLATED_MARKERS="$ACK" \
        PAPER_TRADING_REPORTS_DIR="$WORK/reports" \
        PAPER_TRADING_SIGNAL_PATH="$WORK/signals/latest.json" \
        java -Xmx192m -cp "$CP" engine.runtime.PaperTradingApp >"$log" 2>&1 || true
}

expect() {
    local log="$1" pattern="$2" what="$3"
    grep -qiE "$pattern" "$log" || {
        printf '\n  --- last 20 lines of %s ---\n' "$log" >&2
        tail -20 "$log" >&2
        fail "$what"
    }
    ok "$what"
}

printf '\n=== 1) a persisted marker must start the loop TRIPPED ===\n'
printf '[{"clientOrderId":"11111111-1111-1111-1111-111111111111","symbol":"BTC-USDT","recordedAtIso":"2026-01-01T00:00:00Z"}]\n' \
    > "$MARKERS"
run "$WORK/tripped.log"
expect "$WORK/tripped.log" "unresolved" "the marker was reported unresolved"
expect "$WORK/tripped.log" "unresolvedSubmissionMarkersRequiringReview=true" \
    "the kill switch started TRIPPED because of it"
expect "$WORK/tripped.log" "kill switch TRIPPED.*suspended" \
    "new signal generation was suspended"

printf '\n=== 2) clearing it must allow a clean start ===\n'
printf '[]\n' > "$MARKERS"
run "$WORK/clean.log"
expect "$WORK/clean.log" "clean start" "preflight reported a clean start"
grep -qi "already TRIPPED" "$WORK/clean.log" && \
    fail "the kill switch was still tripped after the marker was cleared"
ok "the kill switch was NOT tripped"

printf '\n=== 3) the live marker store must be unchanged ===\n'
AFTER="$(live_fingerprint)"
[[ "$BEFORE" == "$AFTER" ]] || fail \
  "the live marker store changed during verification: $BEFORE -> $AFTER"
ok "live store identical ($BEFORE)"

printf '\n  Gate A: kill switch trip + recovery and SUBMISSION_UNKNOWN verified.\n\n'
