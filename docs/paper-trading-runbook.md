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
`VstPreflight: real VST balance=...` and confirm `asset=VST`. When
`VstPreflight` is unhappy it says exactly why in the log, rather than the loop
starting silently broken — but **it has two different unhappy outcomes and
they look nothing alike from outside**:

| what it found | what you see |
|---|---|
| a balance asset that is not `VST` | it throws; **no process** |
| a pre-existing non-zero position | the loop **starts**, kill switch already tripped, and submits nothing until a human resets it |

So "did it start?" is the wrong question on its own. What each outcome
guarantees is a safety property and is stated in `CLAUDE.md`, not here.

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
# An exclusive, non-blocking flock (held for the marker check through
# the marker write) makes overlapping invocations safe too -- a run
# that's still in flight when the next tick fires is not duplicated;
# the second instance just exits immediately. See
# scripts/paper-trading-daily-signal.sh's own header comment for the
# full design (marker file, the lock, why a failed run isn't marked
# done).
*/5 * * * * /path/to/trading-engine/scripts/paper-trading-daily-signal.sh

# Process watchdog, every 5 minutes -- timezone-independent (a fixed
# interval, not a specific time of day)
*/5 * * * * /path/to/trading-engine/scripts/paper-trading-watchdog.sh

# Health check, every 15 minutes. Judges the loops against Gate A's own
# criteria and writes the verdict to var/live/health-alerts.jsonl. It
# does NOT notify anyone -- see section 7c for exactly what it does and
# does not do, and why there is no channel yet. Read-only with respect
# to the trading system: it cannot start, stop or signal either loop.
*/15 * * * * /path/to/trading-engine/scripts/paper-trading-health-check.sh
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

## 6b. Deploying a change — and why `git pull` alone is not enough

```bash
./scripts/vps-deploy.sh --check    # report only, change nothing
./scripts/vps-deploy.sh            # deploy, ask before restarting
./scripts/vps-deploy.sh --yes      # deploy and restart without asking
```

**Python and shell changes are live on their next cron tick.** Cron
starts a fresh process every time, so a merged change to
`live/generate_daily_signal.py`, `live/health_check.py` or any script
runs as soon as the pull lands.

**Java changes are not.** A JVM keeps the classes it loaded at startup,
so a fix to the OMS, Risk Gateway, an adapter or the trading loop sits in
the checkout doing nothing until the loop restarts.

Found on 2026-09-08 by the operator asking whether the fixes were
actually reaching the box. They were not: the checkout was current, the
classes had been rebuilt that morning, and both loops were still running
code from **two days earlier** — and no dashboard, watchdog or log line
could have said so.

`vps-deploy.sh` is idempotent and does the whole sequence: refuse to run
on an unclean tree, fast-forward, rebuild, and restart **only if the
running loops are older than the compiled classes**. It checks the VST
account for open positions first, and asks before restarting, because a
restart also resets that day's tick counters — which Gate A is measured
from.

`live.health_check` reports the same condition every 15 minutes as
`stale_running_code`, so the state is visible without anyone remembering
to look.

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

## 7b. Bringing the deployment's audit trail back into the repository

The deployment appends every live-signal decision to
`var/live/live_signals.jsonl` — gitignored, local to that machine. The
repository's copy, `runs/live_signals.jsonl`, is git-tracked and is the
**record**. Getting from one to the other is a deliberate, human-run
step:

```bash
set -Eeuo pipefail                       # so step 1 failing stops the rest

# 1. Pull the deployment's log to this machine, and verify the download.
#    The shell creates /tmp/from-vps.jsonl BEFORE the remote command
#    runs, so a failed ssh leaves an empty or partial file behind. A
#    partial file cut at a line boundary is still valid JSONL, and the
#    sync would then quietly merge a prefix and report success -- the
#    audit trail stays safe (the tool is append-only) but the operator
#    is told the sync is done when records are missing.
gcloud compute ssh paper-trading --zone=us-central1-a \
  --command='sudo -u minjun4897 cat ~/trading-engine/var/live/live_signals.jsonl' \
  > /tmp/from-vps.jsonl

REMOTE_LINES=$(gcloud compute ssh paper-trading --zone=us-central1-a \
  --command='sudo -u minjun4897 wc -l < ~/trading-engine/var/live/live_signals.jsonl')
LOCAL_LINES=$(wc -l < /tmp/from-vps.jsonl)
[ "$REMOTE_LINES" -eq "$LOCAL_LINES" ] || {
  echo "download is short: remote $REMOTE_LINES, local $LOCAL_LINES"; exit 1; }

# 2. See what it would add. Writes nothing.
PYTHONPATH=python python/.venv/bin/python -m live.sync_live_signals \
  /tmp/from-vps.jsonl --dry-run

# 3. Do it, then commit through the normal PR flow.
PYTHONPATH=python python/.venv/bin/python -m live.sync_live_signals \
  /tmp/from-vps.jsonl
git diff runs/live_signals.jsonl        # must show ONLY added lines
```

Safe to run whenever, and safe to run twice — records are identified by
`run_id`, so a second sync reports "nothing to do" and does not touch
the file. It refuses rather than guesses if the same `run_id` carries
different content in the two logs, or if the tracked file changed while
the sync was planning.

**Why this is a separate step rather than the deployment committing for
itself**: giving a VPS push credentials to a public repository is a
credential-exposure surface, and CLAUDE.md forbids pushing to `main`
regardless. The cost of the manual step is that the repository's copy
lags until someone runs it — so run it before deleting or rebuilding
the instance, because until then that machine holds the only copy.

**Do this before any `git pull` on the deployment**, and never resolve a
dirty `runs/live_signals.jsonl` with `git checkout` — that silently
destroys operational history. It should no longer happen at all now that
the deployment writes elsewhere, but a checkout predating 2026-09-06
will still have the old behaviour until it is updated.

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

## 8b. Taking the pre-2019 KRX panel (the reserved confirmation window)

`CLAUDE.md`'s Discovery/Confirmation subsection reserves **KRX daily before
2019-01-02** as a confirmation window. This is how it is filled. Read that
clause first — the three conditions it attaches, including that a confirmation
there is a **cash-equity** claim, are not repeated here.

**It goes in its own database file, and the scan refuses to put it anywhere
else.** `scan_progress` keys on `code` alone, so a second panel in the shared
file would skip every code the first pass finished and fetch nothing;
`_refuse_a_second_panel` stops that. The separation is also the reservation: an
analysis pointed at the spent window cannot read the reserved one by forgetting
a date filter.

Run it from the instance, in a `tmux` session so a dropped connection does not
end it, outside the KRX session (the scan pauses itself if one opens):

**Pass the two credentials in the environment.** `data.krx_scan` needs
`KIS_APP_KEY` and `KIS_APP_SECRET` and nothing else:

```bash
tmux new -s krx-pre2019
cd ~/trading-engine/python
(
  read -rs -p 'KIS_APP_KEY: '    KIS_APP_KEY;    echo
  read -rs -p 'KIS_APP_SECRET: ' KIS_APP_SECRET; echo
  export KIS_APP_KEY KIS_APP_SECRET

  python3 -m data.krx_scan --scan \
    --panel-start 19910828 --panel-end 20181231 \
    --db-path data/var/krx_scan_pre2019.sqlite3 \
    --universe-db data/var/klines.sqlite3
)
```

Two details in that shape, both deliberate. `read -rs` keeps the value out of
shell history and off the screen, which a `KIS_APP_KEY=…` on the command line
would not. **The subshell scopes the credentials to the scan**: this session
outlives a six-to-nine-day run by definition, and an `export` in the interactive
shell would be inherited by everything typed in it afterwards. Re-entering them
on resume is the right cost — the same `--db-path` is what resumes, not the
environment.

**Why this reads nothing from `.env`, while the collectors do.** The collectors'
`.env` fallback is a deliberate, reaffirmed operator decision — `CLAUDE.md`
records it, and why it may only be revisited across all collectors at once — and
it exists for *cron*, which supplies no environment. This command is typed by a
human, so the environment is always available and the fallback buys nothing. Two
credential mechanisms in one procedure, where the one that differs is the one
nobody remembers, is the cost `CLAUDE.md` names; the cheapest way to avoid it in
a manual procedure is not to introduce a second one.

**There is a second mechanism on the instance, and it is disclosed here
rather than left to be found.** `~/.krx_pre2019_runner.sh` (mode `0700`) does
the same run non-interactively: it reads the two values with the collectors'
own `.env` fallback — never `source`, CRLF stripped, environment winning — and
`exec`s the scan. It exists because the command above cannot be typed by a
session that has no terminal, and putting `KIS_APP_KEY=…` on a `tmux
new-session` command line instead — which an earlier attempt did — puts the
real key in a process's argv where `ps` shows it to any local reader. So the
choice was between the collectors' documented fallback and argv exposure, and
this is the first of those.

It passes no `--page-days`, which is the point of deriving the width from the
panel: the runner needs no edit to pick the right one up.

Read it before trusting it, and prefer the typed form above when there is a
terminal. Two credential mechanisms in one directory is the cost `CLAUDE.md`
names, and this is one; what keeps it honest is that it is the *same* mechanism
the collectors already use, not a third one.

An earlier draft of this section told the operator to **source `.env`
wholesale**. Named without repeating it, so nobody copies it back out: that
executes the whole file, exports everything in it, and passes a CRLF-bearing key
straight through — the last of those being what once put a real key into a JDK
exception message. Recorded because the pattern reached a *document* only after
it had already been typed at a prompt.

It is **resumable** — `already_done` reads the database, not a sidecar — so
detaching, rebooting or killing it costs only the code in flight.

**One exception to that, and it is the one this backfill actually hit.** A code
recorded `failed:capped` is **not** retried by a second pass —
`RETRYABLE_FAILURES` holds only `failed:rejected` and `failed:transport`,
because an identical request returns an identical capped answer, so retrying
one is a request that cannot succeed. That is right when the cap was hit on the
data, and wrong when it was hit on the *width*: fixing the width does not
un-skip a code already recorded against the old one. So a run that failed this
way leaves rows that resumption will honour as decided.

After a width change, clear the failures before restarting — and read the bar
count first, because that is what decides whether clearing is free:

```bash
sqlite3 data/var/krx_scan_pre2019.sqlite3 \
  'SELECT (SELECT count(*) FROM scan_bars) AS bars,
          status, count(*) FROM scan_progress GROUP BY status;'
```

With `bars = 0` the file holds no data at all and deleting it is the simplest
thing that can be correct. With bars present, delete only the failed rows —
`DELETE FROM scan_progress WHERE status LIKE 'failed:%'` — never the completed
ones, which are the days of fetching you are keeping.

### What it costs, derived rather than guessed

**90 calendar days per request**, which `default_page_days` returns for any
panel reaching before 2000 — so the width below is what the command above
already uses, and passing `--page-days 120` here would override it back to the
value that fails. Getting it wrong is not a slow run but a failed one.

KRX traded **Saturdays** until 2000, so a 120-day page returns up to **99**
sessions there against ~81–84 from 2001 on, against a silent **100-row cap**;
`validated_output2` refuses a page at or over it, and `failed:capped` is
deliberately **not** retryable because an identical request returns an identical
capped answer.

**What that cost the first attempt, stated as measured rather than as feared**:
14 of 16 recorded codes `failed:capped`, **0 bars**. Not every 120-day page in
that era breaches the cap — only the dense ones do, and 99 is the worst measured
page, not the typical one. One is enough: the fetch loop `break`s a code at its
first failed page, so a single dense stretch anywhere in 1991–2018 discards that
code's whole window. That is why the failure is per *code* and near-total, from a
page-level breach that is only occasional.

At 90 days, 25 probed pages across 1991–1999 returned at most **76** rows.

So **111 pages per code** against the existing panel's 24, over the **4,371**
codes the pool resolves to: **~485,000 requests**, at a throughput measured at
0.5–0.7/s after the close — **roughly 190–270 hours, eight to eleven days**. The
scan prints its own estimate, derived from the width it will actually use, and it
is resumable, so that is wall time rather than a single sitting.

### A 53% shortcut exists, was measured, and is deliberately NOT taken

The existing scan already knows a lot about these codes, and three of its
recorded states look like they rule out earlier history:

| recorded state | count | looks skippable because |
|---|---|---|
| `done`, `first_date` after 2019-01-02 | 866 | its earliest served bar is later than the old panel's start |
| `absent:outside_window` | 1,152 | its bars lie entirely after the old panel |
| `absent:never_served` | 434 | KIS does not price the code at all |

That is **2,452 of 4,638, and skipping them would halve the run**. Only the last
group may be skipped, and the reason is the difference between a measurement and
an inference.

`never_served` came from the **wide probe**, which spans 1990 to today and
returned nothing — a statement about every era. The other two rest on a request
**that was never made**: the old panel *started* at 2019-01-02, so it never asked
about 1995, and "no bars before 2022" is an inference from silence. `CLAUDE.md`'s
survivorship rule is explicit that an absent bar is not evidence of absence
unless the fetch that produced it is known complete, and a code that traded in
the 1990s, delisted, and was later reissued is exactly the case that inference
drops.

**Dropping one real name from a survivorship-safe pool defeats the only reason
this window is worth having**, so the full pool is scanned and the days are
paid. Skipping the 434 alone saves about 8% and is not worth a special case.

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
