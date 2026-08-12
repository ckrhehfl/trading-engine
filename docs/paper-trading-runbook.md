# Paper trading runbook

How to set up and operate this project's paper-trading loops (both the
internal simulated one and the real BingX VST one) on a machine — this
one, or a fresh laptop later. Written for a human operator, not for an
AI coding session — this is *how to run it*, not *how it's built*
(that's `.planning/paper-trading-*.md` and CLAUDE.md).

**Scope**: this covers paper trading only (simulated + VST demo funds).
Nothing here enables real-money trading — that's a separate, much
higher-bar decision gated by CLAUDE.md's Live Entry Criteria.

## 1. Prerequisites

- Java 21 (the Gradle wrapper in `java/gradlew` handles the rest —
  don't need Gradle installed separately)
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for the Python virtual environment
- `tmux`
- `cron` (or an equivalent scheduler if setting up on a non-cron
  system — the two scheduled jobs below are what actually matter, not
  cron specifically)
- `git`

## 2. First-time setup on a new machine

```bash
git clone <repo-url> trading-engine
cd trading-engine

# Secret-scanning pre-commit hook — one-time per clone (see CLAUDE.md,
# "repo is public" section, for why this exists)
git config core.hooksPath .githooks

# Python environment
cd python && uv sync && cd ..

# Java build (also confirms the toolchain is set up correctly)
cd java && ./gradlew clean build && cd ..
```

Copy `.env.example` to `.env` and fill in real values:

```bash
cp .env.example .env
```

Required only for the BingX VST loop (the simulated loop needs no
BingX API credentials at all — see §6):

- `BINGX_API_KEY` / `BINGX_API_SECRET` — a BingX API key. **Must be a
  VST (demo-trading) key, never a production key with real funds or
  withdrawal permission** — see CLAUDE.md's Non-negotiable Rules.

Optional, only for research scripts (not needed for either paper-trading loop):

- `FRED_API_KEY` — only needed if re-running macro-data research
  scripts. Free, get one at
  <https://fred.stlouisfed.org/docs/api/api_key.html>.

**Never commit `.env`. Never paste its contents into a chat session or
anywhere else.** `.env.example`'s own `BINGX_BASE_URL` default
(`open-api-vst.bingx.com`) is not actually read by the VST order-
execution path — see §6 for why, and what `BINGX_BASE_URL` is actually
used for.

## 3. Starting both loops

Two independent processes, run as separate `tmux` sessions so a
problem in one (e.g. a `KillSwitch` trip) can never affect the other.

**Simulated (internal fill simulator, no real network writes)**:

```bash
tmux new-session -d -s paper-trading -c ~/trading-engine/java \
    env BINGX_BASE_URL=https://open-api.bingx.com \
    ./gradlew -q :runtime:runPaperTradingApp
```

**BingX VST (real demo-trading network calls, virtual funds)** — do
this by hand at least once so you actually see the startup log
(confirms the account is really a VST account, confirms no leftover
position, confirms leverage got set) before relying on the watchdog to
restart it silently later:

```bash
tmux new-session -d -s paper-trading-vst -c ~/trading-engine/java \
    env BINGX_API_KEY="$(grep -E '^BINGX_API_KEY=' ~/trading-engine/.env | cut -d= -f2-)" \
        BINGX_API_SECRET="$(grep -E '^BINGX_API_SECRET=' ~/trading-engine/.env | cut -d= -f2-)" \
        PAPER_TRADING_EXECUTION_MODE=bingx-vst \
        BINGX_BASE_URL=https://open-api.bingx.com \
        PAPER_TRADING_REPORTS_DIR=var/live/reports/vst \
    ./gradlew -q :runtime:runPaperTradingApp
```

(In practice, easier to just run `scripts/paper-trading-watchdog.sh`
once by hand — it does exactly this, for both sessions, only starting
whichever isn't already running. See §5.)

Check it actually started cleanly (`=name` forces an exact session
match — see §5's note on why a bare, unprefixed target is unsafe here):

```bash
tmux capture-pane -t =paper-trading -p
tmux capture-pane -t =paper-trading-vst -p
```

Look for `starting paper trading loop` and a `tick complete` line for
each. For the VST session specifically, look for
`VstPreflight: real VST balance=...` and confirm `asset=VST` — if
`VstPreflight` refuses to start, it will say exactly why (wrong asset,
a pre-existing position, etc.) rather than starting silently broken.

## 4. Scheduled jobs (cron)

Two separate jobs, both idempotent (safe to re-run, safe if already
running), both timezone-independent (fixed 5-minute intervals, not a
specific time of day).

```cron
# Daily signal generation, catch-up-capable -- every 5 minutes, checks
# whether live.generate_daily_signal has already completed for the
# CURRENT UTC CALENDAR DAY (tracked in
# var/live/last_signal_run_date.txt) and runs it if not, retrying every
# 5 minutes until it succeeds. Replaces an earlier fixed-time-of-day
# entry (e.g. "5 9 * * *" on a KST machine, for the intended 00:05 UTC)
# that had a real, observed failure mode: a machine that's
# asleep/off/suspended at that exact minute causes standard cron to
# silently and PERMANENTLY skip that day -- cron never retroactively
# runs a missed job. This is safe to run every 5 minutes instead of
# once a day specifically because live.generate_daily_signal is
# documented idempotent and stateless across invocations (see that
# module's own docstring, "No cross-invocation state") -- it produces
# the identical decision no matter what time of day it actually runs
# during a given UTC date, so "catch up whenever the machine next wakes
# up" is exactly as correct as "run at the originally-intended minute."
# See scripts/paper-trading-daily-signal.sh's own header comment for
# the full design (marker file, why a failed run isn't marked done).
*/5 * * * * /path/to/trading-engine/scripts/paper-trading-daily-signal.sh

# Process watchdog, every 5 minutes -- timezone-independent (a fixed
# interval, not a specific time of day)
*/5 * * * * /path/to/trading-engine/scripts/paper-trading-watchdog.sh
```

Install with `crontab -e` (or `(crontab -l; echo "...") | crontab -`
to append without clobbering existing entries).

## 5. The watchdog

`scripts/paper-trading-watchdog.sh` checks both `tmux` sessions every 5
minutes and restarts whichever one isn't running — see the script's own
header comment for the full design and the real credential-handling
history (a genuine security finding from code review, fixed: it never
executes `.env`'s content, only extracts the two specific credential
values it needs and passes them as literal subprocess arguments).

It checks session existence with `=paper-trading`/`=paper-trading-vst`
(the `=` forces an **exact** name match), not a bare, unprefixed name —
a real bug caught during testing: `tmux`'s target-session matching is
prefix-based by default, so a bare `-t paper-trading` check can falsely
report success against the differently-named `paper-trading-vst`
session (and vice versa is not a concern here since `paper-trading` is
a prefix of `paper-trading-vst`, not the other way around) — meaning
the simulated session could stay dead indefinitely while the check kept
reporting it healthy. Every example command in this runbook that
targets a specific session uses the same `=name` form for the same
reason — don't drop the `=` when copying them.

**What it does and doesn't cover**: it recovers from "the process died
but the machine is still on" (e.g. the `tmux` server itself crashing,
observed for real once during this project's own operation). It does
**not** make either loop survive a full machine reboot on its own —
`cron` itself needs the OS to be up for the watchdog to ever run, so a
machine that's off (not just asleep) still means both loops are down
until the machine (and `cron`) come back and the next 5-minute tick
fires.

Check `var/live/watchdog.log` for a history of when it's had to
restart something. **An empty or absent log does NOT by itself prove
the watchdog is running** — a missing cron job, a wrong path, a
permissions problem, or the watchdog script itself failing to launch
would all produce the same "no log entries" result as genuinely
healthy, nothing-ever-crashed operation. Confirm the cron job is
actually firing first (e.g. `grep CRON /var/log/syslog` on a system
that logs cron invocations, or temporarily add a harmless
`echo "$(date -Is)" >> "$REPO_ROOT/var/live/watchdog-heartbeat.log"`
line to the script — an absolute path via the script's own already-
computed `$REPO_ROOT`, not a relative one, since cron does not
guarantee a working directory — while first setting this up) — only
once that's confirmed does an
empty `watchdog.log` mean "nothing's been crashing" rather than "this
isn't running at all."

## 6. Where things read credentials/hosts from, precisely

- `BINGX_API_KEY` / `BINGX_API_SECRET`: read from `.env` by the
  watchdog script (and by the Python signal-generation cron job, via
  its own env), used only for VST (demo) authentication.
- `BINGX_BASE_URL`: used **only** for the public, unauthenticated price
  feed (`BingXPriceFeed`) and by the Python signal script's own kline
  fetch — always the real production host
  (`open-api.bingx.com`), which is fine here since it's read-only
  public market data, no credentials involved.
- The VST **order-execution** host is a hardcoded Java constant
  (`open-api-vst.bingx.com`) — there is deliberately **no environment
  variable or argument** that can change it. This is intentional
  (CLAUDE.md's "Safety guard: eliminate the configuration surface,
  don't validate it") — don't try to make it configurable.

## 7. Checking on things day-to-day

The fastest way to see both loops at a glance -- running status, return%
vs the shared 100,000 internal-equity baseline, per-day equity trend,
recent trades, tick-error summaries, and (for the VST loop) a real BingX
balance cross-check -- is the dashboard:

```bash
cd python && .venv/bin/python -m live.dashboard        # human-readable
cd python && .venv/bin/python -m live.dashboard --json # machine-readable
```

It's read-only and makes no exchange call of its own -- it only reads
data that already exists (`DailyReport` JSON files, each loop's `tmux`
pane output, `watchdog.log`/`cron.log`, and the standing signal file if
present). The VST balance figure it shows
is `VstPreflight`'s own real balance query from that session's last
startup, not a fresh live-refreshed call -- see the module docstring
(`python/live/dashboard.py`) for the full detail and disclosed
limitations (e.g. that figure disappears from the dashboard once enough
ticks scroll it out of `tmux`'s history buffer between restarts).

Both loops are described by `live.dashboard.LOOPS` (a list of
`LoopConfig`, one entry per loop) rather than hardcoded individually --
adding a future loop (a different symbol, asset class, or venue) is one
more entry there, not a rewrite of this dashboard or the two tools below.

### Watching continuously, not just checking once

Neither of the above is "always on" by itself -- each one prints a
snapshot when you run it and stops. Two ways to get a continuously
updating view instead, both read-only, both optional (the dashboard
command above is always available as a fallback):

**A 4-pane `tmux` view** -- both loops' live logs, an auto-refreshing
dashboard, and the watchdog/cron log tail, side by side in one terminal:

```bash
scripts/paper-trading-monitor.sh
```

Opens (or re-attaches to, if already running) a separate `paper-trading-
monitor` session with 4 panes: `paper-trading` (read-only), `paper-
trading-vst` (read-only), the dashboard refreshed every 30s via `watch`,
and a `tail -f` on `watchdog.log`/`cron.log`. The two loop panes attach
with `-r` (read-only) -- this view can never send input into either
trading loop, no matter what gets typed into it. Detaching
(`tmux` prefix + `d`) only detaches your view; it does not stop either
loop, and running the script again while it's already up just re-attaches
instead of creating a second copy.

**A graphical, auto-refreshing web dashboard** (Streamlit) -- the same
stock-app-style "current value + vs.-yesterday %" cards, a per-loop
equity chart, and a recent-trades table, all in a browser tab that
refreshes itself every 30 seconds:

```bash
cd python && .venv/bin/streamlit run live/web_dashboard.py
```

Then open the printed `http://127.0.0.1:8501` URL. Binds to
`127.0.0.1` only (`python/.streamlit/config.toml`) -- never reachable
from outside this machine. Like the CLI dashboard, it's read-only and
reuses that same module's data-gathering functions rather than parsing
anything itself -- see `python/live/web_dashboard.py`'s module docstring
for detail and for why the refresh is a plain page reload rather than a
`streamlit`-internal rerun loop.

For raw detail beyond what the dashboard summarizes:

```bash
tmux ls                                     # both sessions alive?
tmux capture-pane -t =paper-trading -p | tail -20
tmux capture-pane -t =paper-trading-vst -p | tail -20
cat var/live/cron.log | tail -20            # daily signal generation history
cat var/live/watchdog.log                   # any restarts needed?
ls var/live/reports/daily/                  # simulated loop's daily reports
ls var/live/reports/vst/                    # VST loop's daily reports
```

## 8. Stopping everything

```bash
tmux kill-session -t =paper-trading
tmux kill-session -t =paper-trading-vst
```

Remove the two crontab lines (`crontab -e`) if you want the watchdog to
stop bringing them back.

**A real, open position on the VST account is not closed by stopping
these processes** — this codebase has no way to close a position
programmatically (see `.planning/paper-trading-h-vst-integration.md`
for why: hedge mode means submitting the opposite side opens a second
position rather than closing the first). Close via the BingX app/site
directly if needed.

## 9. Known, disclosed limitations (not blocking, but worth knowing)

- No OS-level process supervision beyond the watchdog above — a full
  machine/OS restart needs a human (or an OS-level `cron`/systemd
  startup entry, not set up here) to get things running again.
- `PaperTradingApp.stop()`'s shutdown-termination-confirmation logic
  has no deterministic automated test (tracked: issue #74) — the logic
  itself is conservative/fail-safe by design.
- `DailyReportGenerator`'s pending-report retry queue is in-memory
  only — a report can be lost if the process restarts while a write
  retry is still pending (tracked: issue #75).
- The VST-host guardrail hook (`.claude/hooks/vst_guardrail_check.py`)
  has a known, disclosed bypass shape it can't currently detect
  (cross-statement variable aliasing) — tracked: issue #80. The
  underlying safety property (no config surface for the VST host in
  the real shipped code) is unaffected; this is defense-in-depth on
  top of that, not the guarantee itself.
- Rotate `BINGX_API_KEY`/`FRED_API_KEY` if you haven't since the
  credential-handling incident disclosed in
  `.planning/paper-trading-h-vst-integration.md` — cheap insurance, no
  confirmed public exposure, but real local exposure did happen once.
