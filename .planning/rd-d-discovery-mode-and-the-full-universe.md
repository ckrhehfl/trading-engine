# Research Direction Task D — `Discuss`: discovery mode, and what a full-universe scan actually costs

**Status**: Discuss. Four operator decisions were taken on 2026-09-14 and
this document turns them into a design. **Nothing is built yet and no
strategy has been run.** The measurements here are API capability probes
and record counts — no forward return of anything.

The decisions, so the reasoning behind them is not lost:

| | Decision | Where it is recorded |
|---|---|---|
| **A2** | **Split discovery from confirmation** | CLAUDE.md, "Discovery and Confirmation — two modes" |
| **B3** | **Scan the full KOSPI + KOSDAQ universe** | §2 below |
| **C1** | **Start both collectors now** — investor flow and intraday | §3, §4 |
| **D1** | **First mechanism measurement on BTC 1m** | §5 |

---

## 1. Why the operator's objection was right, in one table

The objection was that the project's machinery does not fit what is now
being attempted. It does not, and the evidence is that **the two best
results in project history both died on bookkeeping rather than on
measurement**:

| | its own evidence | what killed it |
|---|---|---|
| **S16** | t = +2.388, p = 0.0098, PSR 0.9905, PF 6.44, DD 9.93%, 181 trades | DSR = 6.5e-11 against `N` = 127 |
| **TM Task D, P3** | Gate A **PASS**, Sharpe 0.716 > the window's 0.623 floor, PSR 0.9705 | the window was already spent |

Neither is a claim that those strategies work. It is a claim about the
**gate**: at `N` in the 120s the DSR-0.95 bar is an annualized Sharpe near
**4.00** against a credible institutional 0.4–0.8, so the gate's arithmetic
guarantees refusal regardless of what is fed to it. A measurement device
that can only return one answer is not measuring.

Two further mismatches, both already recorded elsewhere and both load-bearing:

- **The instrument cannot see the target.** A 30-day fold Sharpe has
  SE **3.49** (rd-a §2, corrected). And it is only *defined* for a
  continuously-invested strategy — while the thing being pursued is flat
  most of the time.
- **The search was aimed at the wrong object.** Four independent sources
  say the selection filter, not the entry formula, is where day-trading
  edge lives (rd-c §2). Every `strategy_id` in `runs/experiments.jsonl`
  asks which formula predicts direction.

## 2. B3 — the universe, measured rather than estimated

Counted from KIS's own master files, downloaded on the instance
2026-09-14 (`kospi_code.mst`, `kosdaq_code.mst`, 증권그룹구분코드 `ST`):

| Market | Records | **Common stock (`ST`)** | Other |
|---|---|---|---|
| KOSPI | 2,580 | **915** | EF 1,168 · EN 375 · BC 87 · RT 23 · SW 4 |
| KOSDAQ | 1,823 | **1,803** | FS 11 · DR 9 |
| **Total** | 4,403 | **2,718** | |

**2,718 names, not ~2,700 by guess.** The `ST` filter matters: over half of
the KOSPI file is ETFs and ETNs, which are not what a "stocks in play" scan
means and would swamp any relative-volume ranking.

### 2.1 The call budget, and why one line of it is impossible

| Job | Calls | At 1 call/s | Verdict |
|---|---|---|---|
| Investor flow, full universe, **per day** | 2,718 | 45 min | **feasible** |
| Daily OHLCV backfill, full universe, 1 year (100 rows/call) | ~8,200 | 2.3 h | **feasible**, one-off |
| Daily OHLCV backfill, full universe, 7 years | ~49,000 | 13.6 h | feasible, one-off, spread over days |
| **Intraday backfill, full universe, 250 sessions** | **~2,718,000** | **31 days** | **impossible** |

That last row is the design constraint, and it resolves itself into the
architecture rather than defeating it:

> **The scan is daily. The trade is intraday.**

"Stocks in Play" works exactly this way — the selection statistic
(abnormal relative volume) is a **daily** quantity, and only the selected
names are traded intraday. So intraday bars are needed only for the names
the rule actually picks:

| | Calls |
|---|---|
| 250 sessions × ~20 selected names × 4 calls/session | **~20,000** |
| At 1 call/s | **5.6 hours**, one-off |

**Feasible, and it is the same shape as the strategy.** The full-universe
daily scan is what makes the intraday budget small.

### 2.2 The survivorship problem, which is not solved

**KIS master files enumerate currently-listed symbols only.** A per-day
selection rule needs every name that traded *on that day*, including ones
since delisted, or the scan is biased upward by construction — the
delisted names are disproportionately the ones that collapsed, and
excluding them makes any strategy look better than it was.

This is materially worse than KR-10's exposure. KR-10 (MS-E) selects on
day one from 2018 information and applies a pre-defined exit rule, so the
future cannot leak into the selection. A **per-day** rule re-selects 250
times, and each of those selections silently excludes the day's casualties.

**Until a delisted-symbol source exists, any full-universe backtest result
is survivorship-contaminated and must be reported as such.** Candidate
sources, none yet probed: KRX's own 상장폐지 disclosures, a KIND export, or
retaining master-file snapshots going forward (which fixes the future and
not the past). **Retaining a daily master-file snapshot from today costs
essentially nothing and should start immediately** — it is the same
"cannot be backfilled" argument as the flow data, one level up.

## 3. C1a — investor flow, and why waiting for Gate A is free

Measured in [`rd-c-kis-flow-probe-result.md`](rd-c-kis-flow-probe-result.md):
`inquire-investor` returns **exactly 30 rows and takes no date parameter**,
so it cannot be backfilled past ~30 trading days.

**The window is a rolling lookback, and that changes the urgency in a way
worth stating precisely.** Calling on day *T* returns roughly *T−30* to
*T*. So a collector that starts within 30 trading days of today loses
**nothing from today forward** — it only forgoes the pre-today backlog.
The urgency is therefore about *starting within weeks*, not *starting
within hours*.

That matters because of a real conflict:

> **Gate A is at 9 of 15 days** (daily reports 2026-09-05 … 2026-09-13) and
> needs **≥99% uptime** over the remaining six. `kis-paper` runs 288 ticks
> a day — one per five minutes — and every one of them shares this
> account's KIS quota. A 2,718-call burst that collides with a tick could
> fail it, and a failed tick is uptime Gate A cannot recover.

**So: the full-universe collector does not start until Gate A completes.**
This costs nothing (6 days ≪ 30), and it is the difference between an
irreversible data decision and an irreversible *operational* one.

What runs in the meantime, because it proves the pipeline at negligible
risk:

| Phase | Scope | Calls/day | Risk to Gate A |
|---|---|---|---|
| **now** | KR-10, ten names | **10** | nil — 3% of kis-paper's own tick count |
| **after Gate A** | full universe, 2,718 names | 2,718 | throttled, off-hours |

### 3.1 Two constraints the schedule must respect

1. **Collect after the KRX close.** 투자자별 매매동향 is **가집계
   (provisional) during the session** and only finalised after the close.
   A mid-session snapshot records a number that will change, which is a
   silent data-quality failure of exactly the kind this project has been
   bitten by. Run after 15:30 KST (06:30 UTC).
2. **Throttle to 1 call/second and run off-hours.** Not because the limit
   is known to be 1/s — it is not measured — but because the cost of
   being wrong is Gate A, and the job has 45 minutes of slack in an
   overnight window.

## 4. C1b — intraday

`inquire-time-dailychartprice` (`FHKST03010230`) serves past sessions at
120 rows/call, back **~250 trading days, rolling** — boundary pinned at
2025-09-03 (serves) / 2025-09-02 (empty). One session is ~380 bars after
removing the 15:20–15:30 closing call auction, so **~4 calls per
symbol-session**.

Per §2.1 this is collected **only for selected names**, which makes it a
~20,000-call one-off rather than a 2.7-million-call impossibility. But the
selection rule does not exist yet, so the honest sequencing is:

1. Daily OHLCV + flow for the full universe (§2.1 rows 1–2).
2. Define the selection rule, **committed before it is run**.
3. Fetch intraday only for what it selects.

**One thing must not wait for that sequence**: the intraday edge rolls off
at one session per session. Every day between now and step 3 is a session
permanently lost from the far end. That is an argument for moving quickly
through steps 1–2, not for fetching 2.7 million calls of bars that will
mostly never be used.

### 4.1 Two traps already measured, which the pipeline must handle

- **An empty result is `rt_cd=0`, not an error** — the same convention
  every expired futures contract returned in MS-B §2.1. A backfill that
  treats `rt_cd=0` as success would silently record nothing and report a
  clean run. Coverage is checked against an index calendar, as
  `backfill_kis.py` already does for daily bars.
- **Bar timestamps are not uniformly on the minute grid.** 2026-01-02
  returned `:11`-second stamps where every other probed date returned
  `:00`. A grid-alignment check copied from the daily path would reject
  real data.

## 5. D1 — the first measurement goes on BTC 1m, and why that is not a detour

BTC 1m (Binance futures, 3,661,780 bars, 6.96 years, taker buy/sell volume
on every row) is now a **designated discovery window** under CLAUDE.md's
new subsection. It was already closed to selection, so designating it
costs nothing and buys the ability to look freely.

| | BTC 1m | KRX intraday |
|---|---|---|
| depth | **6.96 years** | ~1 year |
| bars | 3.66 M | ~95 k/symbol |
| order flow | **taker buy/sell on every row** | none |
| investor-type flow | none | **yes, 30-day rolling** |
| collected? | **yes** | **no** |
| status | **discovery (spent)** | **unspent** |

**The argument for measuring on BTC first is that it keeps KRX clean.**
Stage 1–3 of rd-b's programme is a sweep across a catalogue, which is
selection; running it on KRX would spend the one Korean window before a
candidate exists. Running it on an already-spent window costs nothing and
leaves KRX for confirmation.

**The cost, stated:** a mechanism established on BTC perpetuals and then
confirmed on Korean equities carries a **cross-market transfer
assumption** — different participants, session structure, fee regime,
price limits, and no funding. That assumption is explicit and testable
rather than hidden, which is the most that can be said for it. Where a
mechanism is *specifically* Korean (investor-type flow, the opening call
auction, price limits) it cannot be studied on BTC at all and waits for
KRX data.

## 6. What this does not claim

- **No strategy has been found, and nothing here is evidence of one.**
- **The two-mode split does not make any result more true.** It permits
  looking; it does not lower the confirmation bar, which is unchanged.
- **The full-universe scan is survivorship-contaminated** until §2.2 is
  solved, and any result from it must say so.
- **The call budgets are arithmetic, not measurements.** The per-second
  rate limit on KIS's paper host has not been measured, which is exactly
  why the throttle is set conservatively rather than at a discovered edge.
- **The selection rule does not exist yet.** §2.1's "~20 selected names"
  is a placeholder for budgeting, not a decision.

## 7. Order of work

| | | Blocked by |
|---|---|---|
| 1 | CLAUDE.md two-mode split | — **done** |
| 2 | Master-file snapshot retention, daily | — starts immediately, §2.2 |
| 3 | Investor-flow collector, **KR-10 only**, after-close cron | — |
| 4 | Full-universe daily OHLCV backfill | throttle design |
| 5 | **Widen the flow collector to 2,718 names** | **Gate A completing** |
| 6 | Stage 1 on BTC 1m — event counts for the rd-c catalogue | — runs in parallel |
| 7 | Selection rule, committed before it is run | 4, 6 |
| 8 | Intraday fetch for selected names | 7 |
