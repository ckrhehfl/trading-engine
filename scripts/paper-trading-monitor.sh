#!/usr/bin/env bash
# Opens a 4-pane tmux session for watching both paper-trading loops live,
# side by side, instead of switching between `tmux attach`/log tails by
# hand. Purely a viewing convenience: creates its own session
# ("paper-trading-monitor"), distinct from the two trading sessions
# (`paper-trading`, `paper-trading-vst`) -- never touches trading state,
# never sends input to either trading loop (both loop panes attach with
# `-r`, tmux's read-only client flag).
#
# Panes:
#   1) paper-trading      -- live log, read-only
#   2) paper-trading-vst  -- live log, read-only
#   3) `python -m live.dashboard`, auto-refreshed every 30s via `watch`
#   4) `tail -f` on watchdog.log + cron.log
#
# Each loop pane (1-2) re-attaches in a retry loop rather than attaching
# once -- so a loop that hasn't started yet, or that dies and gets restarted
# later by scripts/paper-trading-watchdog.sh, doesn't leave that pane
# permanently blank. This mirrors the same real flakiness (a `tmux` session
# briefly not existing between a crash and the next watchdog cycle) this
# project has already observed and designed the watchdog itself around.
#
# Idempotent: running this again while the monitor session is already up
# just attaches to it instead of creating a second one.
#
# Usage: scripts/paper-trading-monitor.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONITOR_SESSION="paper-trading-monitor"

if tmux has-session -t "=$MONITOR_SESSION" 2>/dev/null; then
    echo "Monitor session '$MONITOR_SESSION' is already running -- attaching."
    exec tmux attach -t "=$MONITOR_SESSION"
fi

# Builds the retry-attach loop for one target session, as a single shell
# command string (tmux runs whatever string it's given via the user's
# default shell). `-r` is read-only: this pane can never send keystrokes
# into the trading loop's own process, no matter what gets typed into it.
# `unset TMUX` is required, not cosmetic: this command itself runs inside a
# pane of THIS session (`paper-trading-monitor`), so its shell always has
# `$TMUX` set pointing back at that same enclosing session -- without
# unsetting it first, tmux treats any `attach` from in here as a nested
# attach and refuses ("sessions should be nested with care..."), regardless
# of the target session being different. `|| sleep 5` after a failed
# attach (any reason, not just "not found") avoids a tight busy-loop.
watch_pane_cmd() {
    local target="$1"
    printf 'while true; do if tmux has-session -t "=%s" 2>/dev/null; then unset TMUX; tmux attach -t "=%s" -r || sleep 5; else echo "[%s] session not found -- waiting..."; sleep 5; fi; done' \
        "$target" "$target" "$target"
}

# `=session:` (trailing colon), not a bare `=session` -- `split-window`
# needs a pane-level target the same way `capture-pane` does (see
# `live/dashboard.py`'s `capture_pane` docstring for the same finding);
# a bare session name fails with "can't find pane", confirmed empirically
# while building this script.
tmux new-session -d -s "$MONITOR_SESSION" -c "$REPO_ROOT" "$(watch_pane_cmd paper-trading)"
tmux split-window -t "=$MONITOR_SESSION:" -c "$REPO_ROOT" "$(watch_pane_cmd paper-trading-vst)"
tmux split-window -t "=$MONITOR_SESSION:" -c "$REPO_ROOT/python" \
    "watch -n 30 .venv/bin/python -m live.dashboard"
tmux split-window -t "=$MONITOR_SESSION:" -c "$REPO_ROOT" \
    "mkdir -p var/live && touch var/live/watchdog.log var/live/cron.log && tail -f var/live/watchdog.log var/live/cron.log"
tmux select-layout -t "=$MONITOR_SESSION:" tiled

echo "Started '$MONITOR_SESSION' with 4 panes: paper-trading (read-only), paper-trading-vst (read-only),"
echo "auto-refreshing dashboard (30s), watchdog/cron log tail. Attaching now."
echo "Detach with the tmux prefix + d -- this only detaches your view, it does NOT stop either trading loop."
sleep 1
exec tmux attach -t "=$MONITOR_SESSION"
