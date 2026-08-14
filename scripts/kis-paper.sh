#!/usr/bin/env bash
# Starts/stops/lists one independent kis-paper tmux session per KOSPI200
# futures symbol -- this project's established one-process-per-symbol
# pattern (see scripts/paper-trading-watchdog.sh's own simulated/bingx-vst
# sessions), extended to however many KIS symbols an operator wants to run
# side by side. Each session is its own real PaperTradingApp process with
# its own KisAdapter/KisPreflight/TradingLoop -- see CLAUDE.md's
# "KIS/KOSPI200 venue integration, Phase 1" section for the full design.
#
# Deliberately a new, separate script rather than an extension of
# scripts/paper-trading-watchdog.sh -- that script is BingX-specific
# (simulated + bingx-vst) and extending it for a third loop was explicitly
# named out of scope for KIS Phase 1. This script also does not attempt
# unattended health-check/restart supervision the way the watchdog does
# (no cron wiring here) -- it is a manual start/stop/status tool for an
# operator running real setup/testing, not a production supervisor. Revisit
# folding KIS sessions into the watchdog's own restart loop once KIS
# paper trading is further along.
#
# Safe by default even against real credentials: every kis-paper session
# this script starts inherits PaperTradingApp.forKisPaper()'s own
# unconditional KillSwitch trip at construction (see that method's
# Javadoc -- RiskGateway has no KOSPI200 contract-multiplier conversion
# yet, so the canary notional limit can't meaningfully bound a real KIS
# order's exposure). No session started by this script can submit a real
# order without a deliberate, separate human reset of that kill switch.
#
# Credential handling matches scripts/paper-trading-watchdog.sh's own
# already-reviewed approach exactly, for the same reason: get_env_var
# below never executes .env's content as shell code (no `source`), it
# only greps for one specific KEY= line prefix and returns the literal
# text after it, stripping the CRLF this project's .env has carried
# before (see that script's own header comment for the full history).
# Extracted values are passed to `tmux new-session` as separate argv
# elements via `env KEY=value ...`, never concatenated into a shell
# command string, so a value containing shell metacharacters still can't
# be reinterpreted as code.
#
# Usage:
#   scripts/kis-paper.sh start <SYMBOL> [<SYMBOL> ...]
#   scripts/kis-paper.sh stop <SYMBOL> [<SYMBOL> ...]
#   scripts/kis-paper.sh stop --all
#   scripts/kis-paper.sh status
#
# <SYMBOL> is a real KOSPI200 futures contract code (e.g. "101W09") --
# this script does not know or validate real KIS contract codes; check
# KIS's own app/HTS futures quote screen for the current front-month/
# next-month codes. PAPER_TRADING_REPORTS_DIR is derived per symbol
# (var/live/reports/kis-<SYMBOL>) so concurrent symbols never share a
# report directory -- PAPER_TRADING_SIGNAL_PATH is left at its own
# symbol-derived default (PaperTradingApp.resolveSignalPath) rather than
# overridden here, same reasoning.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SESSION_PREFIX="kis-paper-"

# Rejects a symbol that would break the tmux session name or escape
# var/live/reports/ when interpolated into a path below -- cheap, and
# closes the same class of issue CodeRabbit flagged in
# PaperTradingApp.resolveKisSubmissionMarkersPath (real review finding
# on the PR that fixed the per-symbol marker-path collision this
# script's own multi-symbol use case depends on).
validate_symbol() {
    local symbol="$1"
    if [[ -z "$symbol" || "$symbol" == *"/"* || "$symbol" == *"\\"* || "$symbol" == "." || "$symbol" == ".." ]]; then
        echo "ERROR: '$symbol' is not a safe symbol (must be non-empty, no path separators, not '.'/'..' )" >&2
        return 1
    fi
}

# See module header, "Credential handling" -- never executes .env's
# content, only extracts one KEY=VALUE pair's literal value. Last match
# wins (mirrors normal source/export semantics). Prints nothing if the
# key isn't present; callers must check for that themselves.
get_env_var() {
    local key="$1"
    tr -d '\r' <"$REPO_ROOT/.env" | grep -E "^${key}=" | tail -n1 | cut -d'=' -f2-
}

start_symbol() {
    local symbol="$1"
    # Explicit check, not a bare `validate_symbol "$symbol"` statement --
    # start_symbol is called as `start_symbol "$symbol" || status_code=1`
    # below, and bash's own `set -e` semantics do not propagate into a
    # function invoked inside a `||`/`&&` chain (a real, confirmed-by-
    # testing gotcha found while building this script: an invalid symbol
    # printed validate_symbol's own error and then execution continued
    # into tmux new-session anyway). An explicit `return 1` here does not
    # depend on that fragile propagation.
    if ! validate_symbol "$symbol"; then
        return 1
    fi
    local session="${SESSION_PREFIX}${symbol}"

    if tmux has-session -t "=$session" 2>/dev/null; then
        echo "[$symbol] already running (session '$session') -- skipping"
        return 0
    fi

    local app_key app_secret account_no account_product_code
    app_key="$(get_env_var KIS_APP_KEY)"
    app_secret="$(get_env_var KIS_APP_SECRET)"
    account_no="$(get_env_var KIS_ACCOUNT_NO)"
    account_product_code="$(get_env_var KIS_ACCOUNT_PRODUCT_CODE)"
    if [[ -z "$app_key" || -z "$app_secret" || -z "$account_no" ]]; then
        echo "[$symbol] ERROR: KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT_NO missing or empty in .env -- refusing to start (values never logged)" >&2
        return 1
    fi

    echo "[$symbol] starting session '$session'"
    # KIS_ACCOUNT_PRODUCT_CODE is optional -- PaperTradingApp already
    # defaults it to "03" when unset, so it's only passed through here
    # when .env actually has it, rather than this script inventing its
    # own copy of that default.
    local env_args=(
        "KIS_APP_KEY=$app_key"
        "KIS_APP_SECRET=$app_secret"
        "KIS_ACCOUNT_NO=$account_no"
        "PAPER_TRADING_EXECUTION_MODE=kis-paper"
        "PAPER_TRADING_SYMBOL=$symbol"
        "PAPER_TRADING_REPORTS_DIR=var/live/reports/kis-$symbol"
    )
    if [[ -n "$account_product_code" ]]; then
        env_args+=("KIS_ACCOUNT_PRODUCT_CODE=$account_product_code")
    fi

    tmux new-session -d -s "$session" -c "$REPO_ROOT/java" \
        env "${env_args[@]}" \
        ./gradlew -q :runtime:runPaperTradingApp
}

stop_symbol() {
    local symbol="$1"
    local session="${SESSION_PREFIX}${symbol}"
    if tmux has-session -t "=$session" 2>/dev/null; then
        tmux kill-session -t "=$session"
        echo "[$symbol] stopped (session '$session')"
    else
        echo "[$symbol] not running -- nothing to stop"
    fi
}

# Anchored prefix match (^) on the raw session-name list -- same
# "exact-match, don't accidentally sweep in an unrelated similarly-named
# session" discipline paper-trading-watchdog.sh's own header comment
# already establishes for this exact class of bug (that script's own
# `=name` exact-match finding). SESSION_PREFIX has no regex
# metacharacters, so this anchored basic-regex match is exact.
list_kis_sessions() {
    tmux list-sessions -F '#{session_name}' 2>/dev/null | grep "^${SESSION_PREFIX}" || true
}

stop_all() {
    local sessions
    sessions="$(list_kis_sessions)"
    if [[ -z "$sessions" ]]; then
        echo "no kis-paper sessions running"
        return 0
    fi
    while IFS= read -r session; do
        tmux kill-session -t "=$session"
        echo "stopped session '$session'"
    done <<<"$sessions"
}

status() {
    local sessions
    sessions="$(list_kis_sessions)"
    if [[ -z "$sessions" ]]; then
        echo "no kis-paper sessions running"
        return 0
    fi
    echo "running kis-paper sessions:"
    echo "$sessions" | sed "s/^${SESSION_PREFIX}/  - symbol: /"
}

usage() {
    cat <<EOF
Usage:
  $0 start <SYMBOL> [<SYMBOL> ...]
  $0 stop <SYMBOL> [<SYMBOL> ...]
  $0 stop --all
  $0 status
EOF
}

cmd="${1:-}"
[[ "$#" -gt 0 ]] && shift

case "$cmd" in
    start)
        if [[ "$#" -eq 0 ]]; then
            echo "ERROR: start requires at least one SYMBOL" >&2
            usage
            exit 1
        fi
        status_code=0
        for symbol in "$@"; do
            start_symbol "$symbol" || status_code=1
        done
        exit "$status_code"
        ;;
    stop)
        if [[ "$#" -eq 0 ]]; then
            echo "ERROR: stop requires at least one SYMBOL, or --all" >&2
            usage
            exit 1
        fi
        if [[ "$1" == "--all" ]]; then
            stop_all
        else
            for symbol in "$@"; do
                stop_symbol "$symbol"
            done
        fi
        ;;
    status)
        status
        ;;
    *)
        usage
        exit 1
        ;;
esac
