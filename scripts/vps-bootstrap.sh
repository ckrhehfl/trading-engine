#!/usr/bin/env bash
#
# Bring a fresh Linux VPS to the point where the paper-trading loops run
# unattended. Idempotent -- safe to re-run after a reboot, a git pull, or
# a partial failure.
#
#     ./scripts/vps-bootstrap.sh --mode simulated
#     ./scripts/vps-bootstrap.sh --mode simulated --install-cron
#
# ## Why this exists
#
# Gate A of the Paper Trading Pass Criteria needs **15 consecutive days**
# at **>= 99% uptime**, measured from the daily reports' own
# ticks_succeeded / ticks_attempted. Measured on the previous host (WSL2
# on a personal Windows machine) on 2026-08-29: 67 ticks against 288
# expected, i.e. **23%**, with a 13-hour unbroken gap overnight and one
# daily report produced in three days. The loop needs the machine awake
# at UTC midnight to roll a report over, and a desktop is not awake at
# UTC midnight.
#
# No amount of strategy work changes that number. This script is the
# thing that does.
#
# ## What it deliberately does NOT do
#
# - **It never handles a secret.** No key is read, written, echoed, or
#   passed as an argument. A mode that needs credentials checks that
#   `.env` exists and is non-empty, and stops with instructions if not.
#   Getting `.env` onto the box is a human step, by design.
# - **It never enables trading.** The kill switch's persisted state is
#   left exactly as found. `forKisPaper()` still trips unconditionally.
# - **It provisions nothing.** Creating the instance, its firewall and
#   its SSH access is the operator's job and costs money.
#
set -euo pipefail

MODE="simulated"
INSTALL_CRON=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'USAGE'
usage: vps-bootstrap.sh [--mode MODE] [--install-cron]

  --mode simulated   the internal PaperBroker loop. Needs NO credentials.
                     This is what Gate A requires -- Gate A explicitly
                     allows a mock signal source, so operational
                     readiness can be proven without a venue key on the
                     box at all. Default, and the recommended first move.

  --mode bingx-vst   additionally runs the BingX VST demo loop. Requires
                     a real .env with BINGX_API_KEY / BINGX_API_SECRET.
                     Places real orders against the demo host only --
                     PaperTradingApp's VST base URL is a hardcoded Java
                     constant with no environment override.

  --install-cron     append this repo's cron lines if absent. Left off by
                     default so the script can be run once to check the
                     machine before anything is scheduled on it.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="${2:?--mode needs a value}"; shift 2 ;;
        --install-cron) INSTALL_CRON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$MODE" in
    simulated|bingx-vst) ;;
    *) echo "--mode must be 'simulated' or 'bingx-vst', got '$MODE'" >&2; exit 2 ;;
esac

say() { printf '\n=== %s ===\n' "$1"; }
ok()  { printf '  ok    %s\n' "$1"; }
warn() { printf '  WARN  %s\n' "$1"; }
die() { printf '\n  FAIL  %s\n\n' "$1" >&2; exit 1; }

say "environment"
[[ "$(uname -s)" == "Linux" ]] || die "this script targets Linux; found $(uname -s)"
ok "linux $(uname -r)"

# --- timezone -------------------------------------------------------
# Everything in this system is UTC: the daily report rolls on a UTC day
# boundary, kline grids are UTC, and KrxMarketCalendar converts from UTC
# explicitly. A box on local time makes the report boundary land at the
# wrong moment and is the kind of bug that only shows up in the data.
if [[ "$(date +%Z)" != "UTC" ]]; then
    warn "system timezone is $(date +%Z), not UTC"
    warn "fix with: sudo timedatectl set-timezone UTC"
else
    ok "timezone UTC"
fi

# --- clock ----------------------------------------------------------
# BingX rejects a request whose timestamp is more than 5s from its server
# time. A VPS without NTP drifts, and the failure looks like an auth
# error rather than a clock error.
if command -v timedatectl >/dev/null 2>&1; then
    if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
        ok "clock NTP-synchronised"
    else
        warn "clock is NOT NTP-synchronised -- BingX rejects requests >5s off"
        warn "fix with: sudo timedatectl set-ntp true"
    fi
fi

say "dependencies"
missing=()
command -v git >/dev/null 2>&1 || missing+=(git)
command -v python3 >/dev/null 2>&1 || missing+=(python3)
command -v java >/dev/null 2>&1 || missing+=("openjdk-21-jdk")
command -v flock >/dev/null 2>&1 || missing+=(util-linux)
command -v tmux >/dev/null 2>&1 || missing+=(tmux)
if ((${#missing[@]})); then
    die "install first:  sudo apt-get update && sudo apt-get install -y ${missing[*]}"
fi
ok "git $(git --version | awk '{print $3}')"
ok "python $(python3 --version | awk '{print $2}')"

java_major="$(java -version 2>&1 | head -1 | sed -E 's/.*"([0-9]+).*/\1/')"
[[ "$java_major" -ge 21 ]] || die "Java 21+ required, found $java_major"
ok "java $java_major"
ok "tmux, flock"

say "secret scanning"
if command -v gitleaks >/dev/null 2>&1; then
    ok "gitleaks $(gitleaks version 2>/dev/null || echo present)"
else
    # The pre-commit hook fails closed without it, so committing from
    # this box would be blocked entirely. That is the correct behaviour,
    # but it should be a known state rather than a surprise.
    warn "gitleaks not installed -- .githooks/pre-commit fails closed, so"
    warn "commits from this box will be BLOCKED until it is installed"
fi
git -C "$REPO_ROOT" config core.hooksPath .githooks
ok "core.hooksPath -> .githooks"

say "python environment"
# Dependencies live in python/pyproject.toml and the existing venv was
# built by `uv` -- it has no `pip` in it at all, which is normal for uv
# and which an earlier version of this script assumed away. Use uv when
# it is present (the documented tool, per CLAUDE.md's Tooling Stack) and
# fall back to stdlib venv + pip only when it is not.
VENV="$REPO_ROOT/python/.venv"
if command -v uv >/dev/null 2>&1; then
    ( cd "$REPO_ROOT/python" && uv sync --quiet ) || die "uv sync failed"
    ok "uv sync"
else
    warn "uv not installed -- falling back to venv + pip"
    warn "install it with: curl -LsSf https://astral.sh/uv/install.sh | less"
    warn "(read it before running it -- never pipe an install script to sh)"
    if [[ ! -x "$VENV/bin/python" ]]; then
        python3 -m venv "$VENV" || die "could not create venv (need python3-venv?)"
    fi
    if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
        "$VENV/bin/python" -m pip install --quiet --upgrade pip
        "$VENV/bin/python" -m pip install --quiet -e "$REPO_ROOT/python" \
            || warn "editable install failed; check python/pyproject.toml"
    else
        # A uv-built venv has no pip. Re-creating it would be the wrong
        # move on a box that is already working, so say so and stop
        # rather than silently doing nothing.
        die "the venv at $VENV has no pip and uv is not installed.
        Install uv (preferred), or delete that venv and re-run to get a
        pip-based one."
    fi
fi
[[ -x "$VENV/bin/python" ]] || die "no interpreter at $VENV/bin/python"
"$VENV/bin/python" -c "import pydantic" 2>/dev/null \
    && ok "python imports resolve" \
    || die "pydantic not importable -- the environment is not usable"

say "java build"
( cd "$REPO_ROOT/java" && ./gradlew -q --console=plain :runtime:classes ) \
    || die "gradle build failed"
ok "runtime classes built"

say "credentials"
ENV_FILE="$REPO_ROOT/.env"
if [[ "$MODE" == "bingx-vst" ]]; then
    # Presence and non-emptiness ONLY. This script never reads a value,
    # and must never be changed to: an exception message that embeds a
    # header value has already leaked a real key in this project once.
    [[ -s "$ENV_FILE" ]] || die \
        ".env is missing or empty at $ENV_FILE, and --mode bingx-vst needs it.
        Create it on this box by hand -- never commit it, never paste it into
        a terminal that is being logged, and never pass a key as an argument.
        It needs BINGX_API_KEY and BINGX_API_SECRET.
        Consider rotating both first: CLAUDE.md records a real prior exposure."
    chmod 600 "$ENV_FILE"
    ok ".env present, mode 0600 (contents never read by this script)"
else
    ok "mode 'simulated' needs no credentials"
    [[ -e "$ENV_FILE" ]] && chmod 600 "$ENV_FILE" && ok ".env found anyway; tightened to 0600"
fi

say "runtime directories"
mkdir -p "$REPO_ROOT/var/live/sessions" \
         "$REPO_ROOT/var/live/reports/daily" \
         "$REPO_ROOT/python/data/var"
ok "var/live, data store directory"

say "market data"
DB="$REPO_ROOT/python/data/var/klines.sqlite3"
if [[ -f "$DB" ]]; then
    ok "kline store present ($(du -h "$DB" | cut -f1))"
else
    warn "no kline store at $DB"
    warn "the daily signal runner backfills what it needs on first run,"
    warn "but research history must be copied or re-backfilled separately"
fi

say "cron"
CRON_LINES=(
    "*/5 * * * * $REPO_ROOT/scripts/paper-trading-daily-signal.sh"
    "*/5 * * * * $REPO_ROOT/scripts/paper-trading-watchdog.sh"
    "*/30 * * * * $REPO_ROOT/scripts/collect-positioning.sh"
)
if ((INSTALL_CRON)); then
    current="$(crontab -l 2>/dev/null || true)"
    added=0
    for line in "${CRON_LINES[@]}"; do
        if ! grep -Fqx "$line" <<<"$current"; then
            current="${current:+$current$'\n'}$line"
            added=$((added + 1))
        fi
    done
    if ((added)); then
        printf '%s\n' "$current" | crontab -
        ok "installed $added cron line(s)"
    else
        ok "cron already up to date"
    fi
    # cron must actually be running. On a minimal cloud image it often is
    # not, and the failure mode is total silence -- which is exactly how
    # the previous host lost 16 days without anyone noticing.
    if command -v systemctl >/dev/null 2>&1; then
        systemctl is-active --quiet cron 2>/dev/null || systemctl is-active --quiet crond 2>/dev/null \
            && ok "cron daemon is running" \
            || warn "cron daemon is NOT running: sudo systemctl enable --now cron"
    fi
else
    ok "cron not touched (pass --install-cron); would install:"
    printf '          %s\n' "${CRON_LINES[@]}"
fi

say "ready"
cat <<EOF
  mode: $MODE

  Next, in order:
    1. Verify by hand once:   $REPO_ROOT/scripts/paper-trading-watchdog.sh
    2. Watch a tick appear:   tail -f $REPO_ROOT/var/live/sessions/paper-trading.log
    3. Re-run with --install-cron once step 2 looks right.
    4. Check uptime after 24h: the daily report's
       ticks_succeeded / ticks_attempted must be >= 0.99 for Gate A.

  Gate A needs 15 CONSECUTIVE days. The clock restarts on any missing
  daily report, so the thing to watch is not the strategy -- it is
  whether var/live/reports/daily/ gains exactly one file per UTC day.
EOF
