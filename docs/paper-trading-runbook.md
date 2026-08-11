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

Required for paper trading:
- `BINGX_API_KEY` / `BINGX_API_SECRET` — a BingX API key. **Must be a
  VST (demo-trading) key, never a production key with real funds or
  withdrawal permission** — see CLAUDE.md's Non-negotiable Rules.
- `FRED_API_KEY` — only needed if re-running macro-data research
  scripts; not needed for the paper-trading loops themselves. Free,
  get one at <https://fred.stlouisfed.org/docs/api/api_key.html>.

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

Check it actually started cleanly:
```bash
tmux capture-pane -t paper-trading -p
tmux capture-pane -t paper-trading-vst -p
```

Look for `starting paper trading loop` and a `tick complete` line for
each. For the VST session specifically, look for
`VstPreflight: real VST balance=...` and confirm `asset=VST` — if
`VstPreflight` refuses to start, it will say exactly why (wrong asset,
a pre-existing position, etc.) rather than starting silently broken.

## 4. Scheduled jobs (cron)

Two separate jobs, both idempotent (safe to re-run, safe if already
running):

```
# Daily signal generation, 00:05 UTC (adjust the hour for your
# machine's local timezone — this project's own reference server runs
# on KST, so 00:05 UTC = 09:05 KST = "5 9 * * *")
5 9 * * * cd ~/trading-engine && PYTHONPATH=python BINGX_BASE_URL=https://open-api.bingx.com python/.venv/bin/python -m live.generate_daily_signal >> ~/trading-engine/var/live/cron.log 2>&1

# Process watchdog, every 5 minutes
*/5 * * * * ~/trading-engine/scripts/paper-trading-watchdog.sh
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

**What it does and doesn't cover**: it recovers from "the process died
but the machine is still on" (e.g. the `tmux` server itself crashing,
observed for real once during this project's own operation). It does
**not** make either loop survive a full machine reboot on its own —
`cron` itself needs the OS to be up for the watchdog to ever run, so a
machine that's off (not just asleep) still means both loops are down
until the machine (and `cron`) come back and the next 5-minute tick
fires.

Check `var/live/watchdog.log` for a history of when it's had to
restart something — an empty or absent log is a good sign (nothing's
been crashing).

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

```bash
tmux ls                                    # both sessions alive?
tmux capture-pane -t paper-trading -p | tail -20
tmux capture-pane -t paper-trading-vst -p | tail -20
cat var/live/cron.log | tail -20           # daily signal generation history
cat var/live/watchdog.log                  # any restarts needed?
ls var/live/reports/daily/                 # simulated loop's daily reports
ls var/live/reports/vst/                   # VST loop's daily reports
```

## 8. Stopping everything

```bash
tmux kill-session -t paper-trading
tmux kill-session -t paper-trading-vst
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
