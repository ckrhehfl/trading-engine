# Research Direction Task X — the +54%/yr was the selection date, and moving it removes the premium

**Measured 2026-09-20/21.** Modules:
`research/krx_dayone_universe.py`, `research/krx_dayone_drift.py`.
Reproducible with

```
python -m research.krx_dayone_universe --rank
python -m research.krx_dayone_drift --fetch
python -m research.krx_dayone_drift --measure
python -m research.krx_dayone_drift --compare
```

**Discovery mode.** KRX daily was spent by `ms-f` on 2026-09-13. Nothing
here may be promoted, quoted as evidence of an edge, or reported as a
pass; the output is a universe definition and a diagnosis.

Opened by [`rd-v`](rd-v-payoff-geometry-and-the-universe-drift.md) §1,
which measured the `rd-r` ten at **+54%/yr** against KOSPI's +19% and
named the cause: they were ranked on **2026Q1** turnover, a date inside
the window they are then measured over. This is the test of that
diagnosis, made possible by [`rd-w`](rd-w-the-delisted-universe.md)'s
delisted source.

---

## 1. The headline

> **Move only the selection date — same turnover field, same candidate
> pool, same window, same construction — and the premium over KOSPI goes
> from −2%/yr to +20%/yr, the median member from +8% to +290%.**

| arm | ranked on | source | pool | equal-weight | per yr | vs KOSPI | median name |
|---|---|---|---|---|---|---|---|
| **A** | **2019-01** | spot | 4,643 candidates | 2.86x | +15% | **0.85x, −2%/yr** | **+8%** |
| **B** | **2026Q1** | spot | A's 2,183 ranked | 11.21x | +37% | **3.33x, +20%/yr** | **+290%** |
| `rd-r` | 2026Q1 | **futures** | 283 futures names | 16.55x | +44% | 4.92x, +27%/yr | +797% |
| KOSPI | — | — | — | 3.36x | +17% | — | — |

**A against B is the controlled comparison**, and it decomposes the
premium into two parts rather than one:

- **the selection date is worth −2 → +20%/yr.** The input pool, the
  turnover field, the window and the construction are identical; only the
  ranking date differs. **What is *not* identical is the effective set**:
  A ranked 2,183 of its pool and B ranked 1,931 of that same 2,183,
  because 247 names had stopped trading or gone thin by 2026Q1. §2.1
  measures whether that matters — it does not.
- **the remaining +20 → +27%/yr is the source and the pool.** `rd-r`
  ranked **futures** turnover among the 283 names that carry a listed
  single-stock future, which is a further concentration on top of the
  date.

`rd-v` §1's diagnosis is confirmed and now quantified: the drift is
overwhelmingly the selection date, with a real but smaller contribution
from ranking on futures liquidity.

**The day-one basket slightly *under*performs the index**, which is what
an equal-weight basket of 2019's most-traded names should do: 2019's
turnover leaders included the speculative names that then collapsed —
six of the thirty lost 70% or more. **The 2026Q1 arm picks that era's
winners instead**: 두산에너빌리티, 한화에어로스페이스, 에코프로,
한미반도체, 알테오젠, HD현대일렉트릭. **Only 11 of 30 members are common
to the two arms.**

## 2. What had to be held fixed, and why each one

**An earlier version of this document compared A against `rd-r` directly
and called it "only the selection date moved". That was wrong**, and the
error is instructive: `rd-r` ranks **futures** turnover and A ranks
**spot** turnover, so two things differed. Ranking 2019 futures turnover
is not possible — KIS drops an expired contract's entire series and the
oldest reachable bar is 2025-10 (`rd-r` §3) — so the control runs the
other way: **arm B ranks 2026Q1 spot turnover**, which holds the field
fixed and moves only the date.

**The pool is held fixed too**, via `--pool-from`. Without it a later
window additionally admits every name that listed in between — `402340`
SK스퀘어 first traded 2021-11-29 — so the arms would differ in
membership as well as ranking date. **The honest cost, stated because it
cuts the other way**: a genuine 2026 selection *would* include those
later listings, as `rd-r`'s did, so the pool-matched arm **understates**
how different a real late selection is. The measured date effect is
therefore a lower bound.

**The window is held fixed at 2019-01-02 .. 2026-09-18, 7.71 years.**
`rd-v` §1's own figures (7.94x, +54%/yr, 3.4x the index) come from the
`rd-r` ten's inner-join panel, 2021-11-29 to 2026-09-17, **4.80 years**.
Quoting +54% beside +15% would change the window as well, so `--compare`
re-measures the same ten here — which is why the table reads +44%/yr for
`rd-r` rather than +54%. Both are correct about their own window; only
one set is a comparison.

A name that listed after 2019-01 starts at its own first bar, the same
treatment a delisted member gets at the other end.

### 2.1 The effective sets differ, and it changes nothing

Holding the *input* pool fixed does not make the *ranked* sets equal: A
ranks 2,183 and B ranks 1,931 of those, the 247-name gap being names that
stopped trading or went thin by 2026Q1. So "identical candidates" would
be too strong.

Recomputing arm A over only the 1,931 both arms ranked swaps exactly one
member — 셀트리온헬스케어 out, 롯데케미칼 in — and moves the result by
almost nothing:

| arm A | equal-weight | per yr | vs KOSPI | median name |
|---|---|---|---|---|
| full ranked set (2,183) | 2.86x | +15% | **0.85x, −2%/yr** | +8% |
| common ranked set (1,931) | 2.83x | +14% | **0.84x, −3%/yr** | +1% |

**So the date effect is −3 → +20%/yr rather than −2 → +20%/yr**, and the
conclusion is insensitive to which set is used.

**The common-set version is reported as a sensitivity check and not as
the headline, deliberately.** Restricting arm A to names that were still
rankable in 2026Q1 conditions the 2019 selection on *having survived to
2026* — which is the survivorship bias this whole document exists to
remove. It is the cleaner comparison in one respect and a contaminated
one in another, so both are given and neither is hidden.

**Arm B is reproducible from committed inputs and is therefore not
committed itself**: its pool comes from `runs/krx_dayone_universe.json`,
which is, and the 2026Q1 bars are stable.

```
python -m research.krx_dayone_universe --rank \
    --window 20260102..20260331 \
    --pool-from runs/krx_dayone_universe.json --out <arm-b.json>
python -m research.krx_dayone_drift --fetch   --universe <arm-b.json>
python -m research.krx_dayone_drift --measure --universe <arm-b.json>
```

## 3. The universe, and what selecting in 2019 actually picks

`krx_dayone_universe.py`: rank every common-stock candidate by median
거래대금 over **2019-01**, take the top 30, freeze.

| | |
|---|---|
| candidates | **4,643** (2,604 live + 2,039 delisted common stock, from `rd-w`) |
| ranked | 2,183 |
| absent from the window | 2,458 |
| too thin to rank (<15 valid turnover values) | 2 |
| **errors** | **0** |

Arm B, over the 2,183 A ranked: **1,936 ranked, 240 absent, 7 thin, 0
errors** — and **0 of its thirty delisted during the window**, which is
what a late selection does by construction.

The members a 2026-informed rule would never have chosen are the point:
신라젠, 헬릭스미스, 아난티, 좋은사람들, 신화프리텍, 한국내화 — all
2019 turnover leaders, all down 70-98%.

**Only one member (셀트리온헬스케어, merged into 셀트리온 2024-01-11)
delisted during the window**, and that is worth stating plainly: at the
top of the 2019 turnover ranking, survivorship is a *small* correction.
The large correction is the selection date. `rd-w`'s delisted source
matters much more for a wider or longer scan, where the delisted fraction
is not 1-in-30 — 2,036 of the 4,640 candidates here are delisted names,
and 2,458 candidates were absent from the 2019 window entirely.

## 4. A wire fact this turned up, and it is a hazard for any KRX backtest

> **KIS prints a bar for every session a HALTED name is listed**, with
> `O == H == L == C` at the last traded price and `acml_tr_pbmn` **0**.

Measured on 신라젠 `215600`: **604 consecutive sessions, 2020-05-04 to
2022-10-12**, 603 of them with all four prices equal at 7,757 and zero
turnover. The bar after it opens at that same 7,757 and closes at
**10,043 — a +29% resumption gap**. 좋은사람들 `033340` carries **832**
such sessions.

This is the equity counterpart of the single-stock-futures fact CLAUDE.md
already records (*"a bar is printed for every session a contract is
LISTED, carrying `acml_vol` 0 when nobody traded it"*), and it is more
dangerous on three counts:

1. **A return series reads 603 zeros**, which deflates realised
   volatility. Whether a Sharpe computed over it comes out *higher* also
   depends on the return sample, the annualisation constant and whether
   the resumption gap is included — **none of which is measured here**, so
   the claim is that it may inflate, not that it does.
2. **A backtest holds a position it could not have exited for two and a
   half years**, and nothing in the bar stream says so.
3. **The resumption gap is taken as a tradeable one-day return.** It was
   not tradeable; it was the price of two years of being locked in.

**A bar is not evidence the name was tradeable — only that it was
listed.** It is detectable (`O==H==L==C` and zero turnover) and
`krx_dayone_drift.frozen_sessions` does so; it is **counted and reported
rather than filtered**, because what to do about it depends on the
measurement. A first-open-to-last-close ratio is unaffected — a holder
really was stuck — so §1's figures stand.

## 5. A defect the fail-closed reporting caught

The first measurement printed *"KOSPI unavailable — no selection premium
quoted"*. The index had been fetched (1,895 bars) and stored as all-NULL
prices: **the index endpoint returns `bstp_nmix_*` where equities return
`stck_*`**, on an otherwise identical row. `kis_klines._parse_row`
already documents this; this module read the equity names against the
index response.

**Nothing threw.** Every price stored as `NULL`, the `open IS NOT NULL`
filter then removed every row, and the series was silently empty. What
surfaced it was the one line that refuses to substitute a guess for a
figure it could not compute — `rd-v`'s own convention, carried over.
`store_series` now raises when every row parses to NULL prices, because
that is a wrong endpoint mapping rather than missing data.

## 6. What this does NOT establish

**No strategy, no edge, nothing run.** This is a diagnosis of a
measurement artefact and the construction of a cleaner universe.

**A cleaner universe is not an unbiased one.** Ranking on 2019-01
turnover is still a choice, made once, and the top of a turnover ranking
is a particular kind of name. What it removes is the *look-ahead* in the
ranking date; it does not make the basket representative of the market.

**One month is a short ranking window.** 2019-01 was chosen because it is
the start of the panel, not because a month is the right amount of data
to rank liquidity on. A longer pre-window would be a different
specification and is not tested here.

**The halt fact is measured on three names**, not systematically across
the 4,640. The rate at which KRX names carry frozen stretches is unknown.

**The comparison arms re-use selections made from inside the window.**
That is the whole point, and it also means neither +37%/yr nor +44%/yr is
a claim about anything except what a late selection rule produces on data
it can already see.

**The date effect is a lower bound, not a point estimate.** Holding the
pool fixed excludes post-2019 listings from arm B, and a real 2026
selection would include them — `rd-r`'s did. It is stated as a bound
rather than corrected because correcting it would reintroduce the
membership difference the control exists to remove.

**Two arms is not a dose-response curve.** 2019-01 and 2026Q1 are two
points; nothing here measures how the premium behaves at dates in
between, or whether it is monotone in the gap between ranking and
measurement.

## 7. What follows

1. **Every future KRX measurement on a selected basket states its
   selection date**, and if that date is inside the measurement window,
   reports the premium the way §1 does. `rd-t`, `rd-u` and `tm-e` all
   sit on a 2026-selected basket and are bounded by `rd-v` §1 accordingly.
2. **Frozen-bar detection belongs in the pipeline, not in one module.**
   `frozen_sessions` is a query over two columns; any KRX study that
   computes returns should run it and report the count.
3. **The index field map is a per-endpoint property**, like the row cap
   before it. Reading one endpoint's names against another's gives NULLs,
   not errors.
4. **Run a scan after the close, not during the session.** Sustained
   throughput against KIS's quotation endpoints was measured across one
   day at **10.5/s in a short burst, 1.4/s sustained, and 0.3/s during
   the KRX session**, recovering to **0.5-0.7/s within minutes of the
   15:20 KST close. The instance's own collectors share this app key**,
   and they collect series that cannot be backfilled, so a long scan
   during market hours degrades the thing that matters more than it.
5. **A long pass must be resumable before it is started.** One
   `KisKlinesError` at candidate 2,844 of 4,643 discarded two and a half
   hours of real calls, because the artifact guard — correctly — refuses
   to write an incomplete universe. `--cache` plus a retry pass is the
   answer, and the full-universe scan is roughly twenty times this.
6. **The full-universe scan is the next step and is now unblocked** —
   4,643 survivorship-safe common-stock candidates, the throughput
   measured above, and the traps in §4 and §5 named before it starts.
