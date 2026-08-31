# Moving the paper-trading loops to a VPS

**Why this is the highest-value action available**, stated before any
cloud detail, because the detail only matters if this part is right.

Gate A of the Paper Trading Pass Criteria requires **15 consecutive
days** at **≥99% uptime**, measured from the daily reports' own
`ticks_succeeded / ticks_attempted`, with **no missing daily reports**.
Measured on the current host (WSL2 on a personal Windows machine) on
2026-08-29:

| Gate A requires | Actual |
|---|---|
| 15 consecutive days of daily reports | **1** (`2026-08-27`) |
| uptime ≥ 99% | **23%** — 67 ticks against 288 expected |
| zero critical crashes | watchdog restarted the loops **4×/day** |

The largest single gap ran **2026-08-28T17:00Z → 2026-08-29T06:00Z**, 13
hours, with no watchdog restart logged during it — the watchdog was dead
too, which means the machine itself was asleep.

The daily report is written when a tick observes a **UTC day boundary**.
A desktop that sleeps overnight is never running at 00:00 UTC, so it
cannot produce yesterday's report at all. **This is not a bug to fix in
code.** No amount of strategy research changes it, and Gate A's clock
cannot start until it is fixed.

## What actually has to move

Three cron entries, all path-relative (each derives `REPO_ROOT` from
`BASH_SOURCE`), so they work from any checkout location without edits:

| every | script | needs credentials |
|---|---|---|
| 5 min | `paper-trading-daily-signal.sh` | no |
| 5 min | `paper-trading-watchdog.sh` | only for `bingx-vst` |
| 30 min | `collect-positioning.sh` | no |

Plus the 796 MB SQLite kline store, and — for the chosen `bingx-vst`
mode — a real `.env`.

## The measurement that decides the instance size

Both loops running on the development host, real RSS:

| | memory |
|---|---|
| 2 × `PaperTradingApp` JVM (the actual application) | **267 MB** |
| 2 × Gradle wrapper JVM | 221 MB |
| 3 × Gradle **daemon** | **1,349 MB** |
| total as currently launched | **1,837 MB** |

**About 6× the application is build tooling**, kept resident only because
the watchdog starts the loops with `./gradlew :runtime:runPaperTradingApp`.
A machine that only runs the app never needs any of it.

That single fact decides the whole cost question: 267 MB fits a 1 GB
always-free instance; 1.8 GB does not.

### The change that unlocks it, and its verification

- `java/runtime/build.gradle.kts` gains **`printRuntimeClasspath`** — a
  task that prints the classpath Gradle already computes, and nothing
  else. No new dependency, no existing task changed, no OMS/Risk/
  Execution logic touched.
- `scripts/paper-trading-watchdog.sh` gains **`PAPER_TRADING_LAUNCHER`**
  (`gradle` default, `java` opt-in) and `PAPER_TRADING_HEAP` (192m
  default). The classpath is generated once and cached atomically at
  `var/live/runtime-classpath.txt`. **The default is unchanged**, so no
  existing host behaves differently unless it opts in.
- Heap is capped explicitly rather than left to the JVM default of ¼ of
  physical RAM: two loops defaulting on a 1 GB box would claim 256 MB of
  heap each on top of metaspace and stacks, which is how a small
  instance starts swapping instead of ticking.

**Verified, not assumed.** `printRuntimeClasspath` emits 13 entries,
identical to the running process's own `-cp`, all present on disk. A
loop launched as `java -Xmx192m -cp … engine.runtime.PaperTradingApp` in
an isolated working directory constructed correctly and completed a real
tick:

    PaperTradingApp constructed: … executionMode=simulated
    starting paper trading loop: tickIntervalSeconds=300
    tick complete: lastTickAt=2026-08-31T07:34:17Z equity=100000

Note `BINGX_BASE_URL` is **required** even in `simulated` mode — the
first attempt failed on exactly that, which is the hostname guard
working as designed (never hardcoded in source; see
`.github/workflows/bingx-hostname-guard.yml`).

## Cost — the recommendation is the free one

Prices are us-central1, on-demand Linux, from third-party aggregators
rather than Google's own page. **Check the official pricing page and the
calculator before committing budget** — cloud pricing moves, and free
tiers have been silently cut before (Oracle halved theirs in June 2026
with no announcement at all).

| option | vCPU / RAM | cost | fits? |
|---|---|---|---|
| **GCP e2-micro, Always Free** | 2 shared / **1 GB** | **$0** | **yes, with `PAPER_TRADING_LAUNCHER=java`.** 267 MB of app + OS. Not with the Gradle default. |
| Hetzner CX22 | 2 / 4 GB | ~€3.79-4.59/mo | comfortably, either launcher |
| GCP e2-small | 2 shared / 2 GB | ~$12.23/mo | comfortably, either launcher |
| GCP e2-medium | 2 shared / 4 GB | ~$24.46/mo | far more than needed |

**The recommendation is e2-micro at $0**, and it is not a compromise
version of the paid one — 267 MB of application in 1 GB is a real fit
with room, once the Gradle daemon is not resident. GCP is, as of
2026-08, the only major cloud with a *permanent* free VM; AWS's and
Azure's free VMs expire after 12 months. Re-verified 2026-08-31: still
listed on Google's own free-tier page, no announced change, last altered
in 2021 when e2-micro replaced f1-micro.

**If a paid box is ever wanted, it is Hetzner at ~€4, not GCP
e2-small at $12.23** — a quarter the price for twice the RAM. The only
reason to prefer GCP once paying is keeping one provider.

### Oracle Cloud Always Free was considered and rejected

On specs it wins easily — 2 OCPU / 12 GB, free. Three things rule it out
for *this* use:

- **The allowance was halved on 2026-06-15**, from 4 OCPU / 24 GB, with
  no blog post or customer notification; users found out when instances
  were stopped. A tier that changes silently is a poor foundation for a
  15-consecutive-day measurement.
- **"Out of host capacity" is routine** for ARM shapes in busy regions,
  and Always Free resources are pinned to the home region, which cannot
  be changed later. Resizing behaves like a fresh launch, so people have
  shrunk an instance and then been unable to start it again.
- **Idle instances are reclaimed.** The usual workaround is a cron job
  that burns CPU to look busy. This system genuinely is idle between
  5-minute ticks, so it is exactly the profile that gets reclaimed.

Any of the three costs a day of reports, and Gate A's clock restarts on
a single missing one.

### Spot VMs are disqualified, not merely cheaper

Spot cuts cost roughly in half and is **preempted by design**. Gate A
needs 15 *consecutive* days and zero missing daily reports; a preemption
across a UTC midnight loses that day's report and restarts the clock.
The whole point of this move is eliminating unplanned downtime.

### Latency is a non-issue here

The loop ticks every 300 seconds. US-to-BingX round trip is irrelevant
at that cadence, and the project's own ~100-200 ms target applies to
future live execution, not to this.

## Handover — what only the operator can do

Steps 1-3 need a Google account, payment method, and SSH keys. I have
none of these and should not.

**1. Create the instance**

    gcloud compute instances create paper-trading \
      --machine-type=e2-micro \
      --zone=us-central1-a \
      --image-family=ubuntu-2404-lts-amd64 \
      --image-project=ubuntu-os-cloud \
      --boot-disk-size=30GB \
      --boot-disk-type=pd-standard

`pd-standard` and the region are the two free-tier conditions above; both
are easy to lose by accepting a default.

**2. Firewall: leave it closed.** This box makes only outbound requests.
It needs no inbound rule beyond SSH, and GCP's `default-allow-ssh`
already covers that. Do not open anything else.

**3. Base packages**

    sudo timedatectl set-timezone UTC
    sudo timedatectl set-ntp true
    sudo apt-get update
    sudo apt-get install -y git openjdk-21-jdk tmux util-linux cron
    sudo systemctl enable --now cron

UTC and NTP both matter concretely: the daily report rolls on a UTC day
boundary, and **BingX rejects any request whose timestamp is more than 5
seconds from its server time** — a drifting clock surfaces as an auth
error, not a clock error.

**4. Clone and bootstrap**

    git clone https://github.com/ckrhehfl/trading-engine.git
    cd trading-engine
    git checkout <the reviewed, merged commit>
    git rev-parse HEAD          # confirm it matches before going further
    ./scripts/vps-bootstrap.sh --mode bingx-vst

**Pin the commit rather than taking whatever `main` holds.** The tick
verification above, and every uptime figure Gate A will be judged on, are
statements about a specific tree. A bare clone silently deploys whatever
landed since, which would make a later "15 consecutive days at 99%" a
claim about code nobody checked.

`vps-bootstrap.sh` is idempotent and checks everything above, reporting
what is missing rather than guessing. It **never reads, writes, echoes or
receives a secret** — for `--mode bingx-vst` it checks only that `.env`
exists and is non-empty, then `chmod 600`s it.

**5. Credentials — operator only, and rotate first**

CLAUDE.md records a real prior exposure: a CRLF-terminated `.env` left a
trailing `\r` on `BINGX_API_KEY`, and the JDK's `HttpRequest.Builder`
rejected it with an exception **whose message embedded the key
verbatim**. Nothing reached a committed file or any public surface, and
the root cause is fixed (`BingXAdapter` strips both credentials), but the
keys were exposed to a local log.

**Rotate both before putting them on a new machine.** Then create `.env`
by hand on the box — never commit it, never paste it into a terminal
whose output is being captured, never pass a key as a command argument.
Write the file with a heredoc and no trailing whitespace:

    cat > .env <<'EOF'
    BINGX_API_KEY=...
    BINGX_API_SECRET=...
    EOF
    chmod 600 .env

The VST loop can only ever reach the demo host: `PaperTradingApp`'s VST
base URL is a hardcoded Java constant with **no environment variable,
argument, or other configuration surface** able to route it elsewhere.

**6. Move the kline store**

796 MB. Copying beats re-backfilling — a full re-backfill re-requests
years of 1m data and is rate-limited, while the file transfers in
minutes:

**Do not `cp`/`gzip` the file while anything is writing to it.**
`collect-positioning.sh` runs every 30 minutes and writes to this exact
database, so a plain copy can catch it mid-transaction and carry a
main file whose journal or WAL state is missing — a torn snapshot that
opens fine and is wrong. Use SQLite's own consistent-snapshot path:

    # local, safe with the collector still running:
    python3 -c "
    import sqlite3
    src = sqlite3.connect('python/data/var/klines.sqlite3')
    dst = sqlite3.connect('/tmp/klines-snapshot.sqlite3')
    with dst: src.backup(dst)
    "
    gzip -c /tmp/klines-snapshot.sqlite3 > /tmp/klines.sqlite3.gz
    gcloud compute scp /tmp/klines.sqlite3.gz paper-trading:~/ --zone us-central1-a
    # on the box, BEFORE its own cron is installed:
    gunzip -c ~/klines.sqlite3.gz > trading-engine/python/data/var/klines.sqlite3
    python3 -c "
    import sqlite3
    print(sqlite3.connect('trading-engine/python/data/var/klines.sqlite3')
              .execute('PRAGMA integrity_check').fetchone()[0])
    "

`Connection.backup` takes its snapshot through the same locking the
writers use rather than reading bytes underneath them. **Python's stdlib
rather than the `sqlite3` CLI on purpose** — the CLI is not installed on
this project's own dev box and may not be on a minimal cloud image,
while `python3` is a hard dependency already. The `integrity_check` on
arrival costs seconds and turns a silent corruption into a loud one.

Measured on the real 796 MB store: **14.5 s**, `integrity_check` ok,
4,620,920 kline rows and 31,828 positioning rows preserved.

The **positioning** table is the one that cannot be re-fetched — its
endpoints retain ~30 days — so copy the file rather than starting empty,
or that history is gone.

**7. Start, watch, then schedule**

    export PAPER_TRADING_LAUNCHER=java     # the 1 GB instance needs this
    ./scripts/paper-trading-watchdog.sh
    tail -f var/live/sessions/paper-trading.log

Once ticks appear every 5 minutes:

    ./scripts/vps-bootstrap.sh --mode bingx-vst --install-cron

Put `PAPER_TRADING_LAUNCHER=java` in the crontab environment, above the
job lines, or the watchdog reverts to the Gradle default on its next
scheduled run and the box runs out of memory.

**8. Retire the old host**

Only after the VPS has produced daily reports for **two consecutive UTC
days**. Running both writes two sets of reports for the same days, and
the resulting `ticks_attempted` is not the figure Gate A wants. Remove
the three cron lines locally (`crontab -e`) and stop the tmux sessions.

## What to watch, and what would make this fail

**The one metric.** `var/live/reports/daily/` must gain exactly one file
per UTC day. Not the strategy's P&L — Gate A is about the plumbing, and
its clock restarts on any missing report.

    ls var/live/reports/daily/ | wc -l
    python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['ticks_succeeded'],'/',d['ticks_attempted'])" \
        var/live/reports/daily/$(date -u -d yesterday +%F).json

**Named failure modes, so they are recognised rather than debugged from
scratch:**

- **1 GB is genuinely tight.** Two JVMs at 192 MB heap plus metaspace,
  stacks and the OS leaves little slack. An OOM-killed loop shows up as
  a watchdog restart, not an error — check `dmesg | grep -i oom` before
  suspecting anything subtler. The fix is e2-small, not tuning.
- **A Gradle build on the box is a separate memory event.** The first
  `vps-bootstrap.sh` run compiles Java and will use far more than the
  loops do. Run it before starting the loops, not alongside them.
- **`PAPER_TRADING_LAUNCHER` not exported into cron.** cron does not read
  your shell profile. This is the most likely single mistake.
- **Free-tier hours are per billing account.** A second always-free
  e2-micro anywhere on the same account silently makes both billable.

## What this does not do

It does not make any strategy more proven, and it is not a step toward
live trading. It closes the gap between "the system is ready to be
evaluated" and "the system is being evaluated" — Gate A only. Gate B,
the Live Entry Criteria, and every Risk Parameter are untouched, and the
three open KIS gaps stay open.

## Sources

Pricing and free-tier terms, retrieved 2026-08-31 and **to be verified
against Google's own pages before committing**:

- [Compute Engine free tier — Google Cloud](https://cloud.google.com/free/docs/compute-getting-started)
- [Free Trial and Free Tier — Google Cloud](https://cloud.google.com/free)
- [Oracle quietly halves free-tier Ampere A1 limits — InfoQ](https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)
- [Top 5 cheap VPS providers in 2026 — Sliplane](https://sliplane.io/blog/top-5-cheap-vps-providers)
- [e2-micro pricing — Economize](https://www.economize.cloud/resources/gcp/pricing/compute-engine/e2-micro/)
- [e2-small pricing — Economize](https://www.economize.cloud/resources/gcp/pricing/compute-engine/e2-small/)
- [e2-medium specs and pricing — CloudPrice](https://cloudprice.net/gcp/compute/instances/e2-medium)
