# Multi-Asset TSMOM Task A — `Discuss`: why one asset was the missing piece, and the trap in picking Korean stocks by name

**Status**: Discuss. Nothing built, **no new market data fetched**, no
strategy code written. §2.3's correlations were computed by reading the
existing local store (`python/data/var/klines.sqlite3`) — reading data
this project already holds is not the same as acquiring new data, and the
distinction is stated rather than blurred.

**Opened**: 2026-09-13, at the operator's direction, after the arithmetic
in §2 was worked out in conversation.

This is the `Discuss` pass CLAUDE.md's Strategy Research Methodology
requires before multi-symbol expansion:

> Survivorship bias doesn't apply to the current single-symbol (BTC-USDT)
> scope — there's no universe-selection step for it to enter through.
> **Revisit before any multi-symbol expansion**: the market-data pipeline
> built for that must retain delisted/inactive symbols, not only
> currently-active ones, or backtests across that universe will be biased
> upward by construction.

and which "Two structural remedies remain open" names as remedy #1.

---

## 1. Why this is not another restart

Three prior efforts were opened as fresh directions and each ended the
same way. The pattern, stated so this one can be checked against it:

| Effort | What genuinely changed | What did not |
|---|---|---|
| 15m/1h price signals (`sr-e`…`sr-r`) | — | BTC price bars |
| Scalping (`scalp-s0`…`s16`) | The whole methodology — IC-first, regime layer, CSTI, cost gate. Real. | BTC price bars. Spent two 1m windows; `N` 117→127 |
| Trade management (`tm-a`…`tm-d`) | The axis — management, not entry. Real, and produced the largest measured effect. | BTC price bars, and an already-spent window, so inadmissible for promotion by construction |

**Each restart changed the question and kept asking it of the same finite
resource.** `N` is project-level and cumulative; data windows are
one-shot. So a new *method* competes for the same *evidence budget* as
the old one, and the budget was nearly gone before any of them started.

What makes MS-A structurally different is not that it is a better idea.
It is that it is the first one that **adds an evidence source** rather
than re-asking BTC price history. The check to apply later: if MS-A ends
up running against BTC bars, it has failed this test and is another lap.

## 2. The arithmetic that motivates it

### 2.1 What the strongest result on record actually failed

`sr-ab` (`daily-tsmom-ensemble`, Binance spot 2017-2021 holdout):

| Criterion | Observed | Required | |
|---|---|---|---|
| PSR | 0.9945 | 0.95 | **pass** |
| Sharpe vs detection floor | 1.305 | 0.8503 | **pass** |
| Profit factor | 7.68 | 1.3 | **pass** |
| Max drawdown | 20.135% | ≤20% | fail — by 0.135pp |
| Trade count | 64 | 68 | fail — by 4 |

**Statistical significance already passed.** The two misses are a
drawdown ceiling and a minimum evidence volume. Neither is evidence that
the edge is absent; both are what a *single-asset* portfolio looks like.

### 2.2 The literature was never single-asset

Moskowitz-Ooi-Pedersen (2012) reports TSMOM Sharpe ~0.8-1.2 for a
**58-instrument diversified portfolio** — 24 commodity, 12 bond, 9 equity
index, 13 FX futures. **Zero individual stocks.** Per-instrument Sharpes
are ~0.2-0.4.

This project applied it to one instrument and posted 0.882 (`sr-v`) and
1.305 (`sr-ab`) — *above* the per-instrument range in the literature. The
strategy was working. What was missing is the portfolio.

### 2.3 Correlations, measured rather than assumed

Computed from this repository's own local store while writing this
document (`python/data/var/klines.sqlite3`, daily log returns):

| Pair | Correlation | Sample |
|---|---|---|
| BTC-USDT vs ETH-USDT | **0.8454** | 1,930 days, 2021-05-14 … 2026-08-28 |
| BTC-USDT vs S&P 500 (`SP500`, FRED) | **0.3871** | 1,309 common trading days, 2021-05-14 … 2026-07-31 |

**This is the finding that selects the direction.** Crypto-internal
diversification is nearly worthless (0.845). Cross-asset-class
diversification is real (0.387). Korean equities are the first
low-correlation asset class this project can actually reach, because the
venue integration already exists.

### 2.4 What diversification does to the two failing gates

**These are projections under assumed correlations, not measurements.**
Two things are assumed and neither is yet measured: the Korean
constituents' internal correlation, and their correlation with crypto —
for which §2.3's BTC/S&P-500 figure of 0.387 stands in as a **sensitivity
assumption**, because no Korean data exists in this project yet. Both are
measured at MS-C, before MS-F scores anything, and this table is recomputed
against the real figures then.

Sharpe scales with the volatility multiplier. **Drawdown does not**, and
an earlier draft of this table wrongly assumed it did. Max drawdown is
path-dependent — it turns on when losses align, not only on how variance
adds — so the drawdown column comes from a Monte Carlo (Gaussian copula
for the correlation structure, marginals bootstrapped from real BTC daily
returns so the tails are not normal, 1,929 days, 200 trials, median).

**The simulator was validated against the one case with real data**: an
equal-weight BTC+ETH portfolio over 1,929 days has a measured drawdown
ratio of **0.978** against the single-asset average; the simulator says
**0.977**. The volatility multiplier says 0.960 — optimistic by 1.8%, in
the dangerous direction.

Per-constituent Sharpe 1.305 and drawdown 20.135% held at `sr-ab`'s
observed values; equal weight.

| Structure | Vol mult | Sharpe | DD mult (simulated) | Portfolio DD |
|---|---|---|---|---|
| BTC alone (today) | 1.000 | 1.305 | 1.000 | 20.1% — **fails** |
| BTC+ETH | 0.960 | 1.359 | 0.978 | 19.7% |
| Korea ×10, ρ_int=0.60 | 0.800 | 1.631 | 0.808 | 16.3% |
| Korea ×10, ρ_int=0.75 (pessimistic) | 0.880 | 1.482 | 0.895 | 18.0% |
| Korea ×10 + BTC/ETH, effective ρ≈0.65 | 0.824 | 1.583 | 0.856 | 17.2% |
| Korea ×10 + BTC/ETH, pessimistic ρ≈0.72 | 0.862 | 1.514 | 0.894 | 18.0% |

Trade count scales with K: 64 → ~768 at K=12, against a floor near 100.

**Under these assumptions every structure clears both gates that `sr-ab`
missed** — the pessimistic row by 2pp on drawdown rather than the 3.6pp
the volatility multiplier would have claimed. Stated as a conditional,
because the conditions are assumptions: if the measured Korean
correlations come in materially above 0.75, this conclusion does not hold
and the direction is reassessed rather than argued for.

### 2.5 What this arithmetic assumes, and why §4 exists

It assumes **all K constituents are actually held**. A portfolio Sharpe
near 1.6 across 12 instruments requires 12 simultaneous positions. Proving
the phenomenon on 12 instruments while being able to trade only one
yields the single-asset result, not the portfolio one. The universe must
therefore be tradeable, with shorts, on a venue this project has. That
constraint drives §4 and rules out the most obvious universe.

---

## 3. The trap in the requested universe, stated plainly

The operator's opening suggestion was "삼성전자, SK하이닉스 등 몇 가지와
코스피지수 등, 한 10가지". **Two of those three choices are a problem, and
one of them would invalidate the whole effort.**

### 3.1 Selection bias — the fatal one

Choosing 삼성전자 and SK하이닉스 *in 2026* to backtest *2021-2026* is
choosing names whose 2021-2026 performance is already known. SK하이닉스 in
particular is among the best-performing Korean large caps of that window
on the HBM/AI cycle. A momentum backtest that includes it will look
excellent and will carry **no information**, because the name was picked
knowing the outcome.

This is *selection* bias, and it is worse than the *survivorship* bias
CLAUDE.md's methodology clause warns about:

- **Survivorship bias** = delisted names are missing. Bounded, and for
  Korean large caps over 2021-2026 it is genuinely small — almost none
  delisted.
- **Selection bias** = the names were chosen with hindsight. Unbounded,
  and not fixed by better data.

**No amount of statistical machinery corrects for it.** DSR deflates for
how many configurations were searched; it has no term for "the universe
was chosen after seeing which members did well."

The fix is mechanical, not statistical: **the universe must come from a
rule that could have been applied at the window's start without knowing
the future, and the rule and the resulting list must be committed to a
pre-registration before any price data is fetched.**

### 3.2 Concentration — 삼성전자 + SK하이닉스 + 코스피지수 is close to one bet

Both named stocks are semiconductors; their mutual correlation is
plausibly 0.7-0.8. And 삼성전자 alone is roughly a fifth of KOSPI by
weight, with SK하이닉스 a further large share — so the index is not an
independent third holding, it is substantially a combination of the first
two.

§2.4's benefit comes from ρ_internal staying near 0.6. A
semiconductor-heavy list pushes it toward 0.8 and gives most of the gain
back. **Sector spread is a design requirement, not a nicety.**

### 3.3 The short leg

`daily-tsmom-ensemble` goes long *and* short — the short half is not
optional, it is half the strategy. Korean retail short selling on
individual **spot** equities is heavily restricted, so a spot-stock
universe cannot express the strategy that is being tested.

This is not a blocker, but it decides the instrument type: the universe
must be **futures** (개별주식선물 / 지수선물), where both directions are
available, not spot shares or ETFs.

---

## 4. Universe design

### 4.1 What is actually tradeable with shorts on KIS

| Instrument class | Shortable | Count | In this repo today |
|---|---|---|---|
| KOSPI200 index futures | yes | 1 | `KisAdapter`, `INDEX_FUTURES`, verified live |
| KOSDAQ150 index futures | yes | 1 | same code path, unverified |
| Single-stock futures (개별주식선물) | yes | ~140 underlyings incl. 삼성전자, SK하이닉스 | `KIS_MARKET_DIVISION=STOCK_FUTURES` **exists and refuses to start** — the per-stock contract multiplier is unconfirmed (CLAUDE.md) |
| Spot equities | effectively no | — | not integrated |
| Sector indices (업종지수) | **not tradeable** | ~20 | not integrated |

Two things follow. First, the existing `STOCK_FUTURES` refusal is
directly on this path and must be closed — the multiplier is exactly the
unconfirmed fact that blocks it. Second, **sector indices are attractive
for research and useless for execution**, which is the §2.5 trap.

### 4.2 The universe rule — decided 2026-09-13

The operator chose the rule over the named list, noting the names were
only used because the symbols were unfamiliar, and asked for **turnover**
as the ranking criterion and for **SK하이닉스 to be included**.

> **KR-10**: rank the candidate pool (§4.4) by **2018 full-calendar-year
> traded value (거래대금)** — the year *before* the window opens, so the
> ranking uses no information from the scored window at all. Take the top
> ten subject to **no more than three from any one KRX sector
> classification**, dropping down the ranking where the cap binds. Plus
> **KOSPI200 futures** and **KOSDAQ150 futures**. Target K = 12.
>
> **Selection uses only information available on the window's first day.**
> There is no continuous-listing condition and no survivorship filter.
> A name is selected on its 2018 traded value alone, and what happens to
> it afterwards is handled by the exit rule below, not by excluding it in
> advance.
>
> **Exit rule, fixed here rather than discovered later.** A position is
> only ever closed at a price that was **observable and executable at the
> moment of the decision**:
>
> - **Announced** delisting, futures discontinuation or scheduled halt →
>   exit at the **first executable bar after the announcement date**.
> - **Unannounced** halt → the position is **held through the halt** and
>   exited at the **first bar after trading resumes**, or at the official
>   **cash-settlement price** if it never resumes.
> - Weight is redistributed across survivors **only from the bar after the
>   exit actually happens**, never from the halt date.
>
> The portfolio is not rebalanced back to K=12 by substituting a new name
> — substituting would reintroduce a selection decision mid-window.

**A second draft got this wrong too**, in a way worth naming because it
is the subtler of the two. It said the position closes "at the last
available price on the last day it trades." **Which day is the last is
not knowable on that day** — it is only visible afterwards, so exiting
there is lookahead, and it also assumes a fill at a price that may have
had no liquidity behind it. Both errors flatter the result. The rule
above is written so every exit decision uses only information that
existed when it was made.

**An earlier draft got this wrong**, and the error is worth keeping
visible because it is subtle. That draft required "a continuous
single-stock-futures listing across the whole window" and proposed
*reporting* how many names the condition dropped. **Reporting a
survivorship filter does not remove it.** The condition can only be
evaluated with knowledge of the whole window, so it is future information
entering the selection step — and it would also silently shrink K below
12, invalidating §2.4's arithmetic. Selecting on day-one information and
handling exits by rule is the standard construction and the correct one.

**On the SK하이닉스 request.** It is very likely a no-op: SK하이닉스 was
the second-largest Korean stock by both market capitalisation and traded
value in 2018, so the rule should select it unaided. **The resolution is
therefore: run the rule first, and report where SK하이닉스 lands.**

- If the rule selects it — no conflict exists and nothing was overridden.
- If it does not, that is itself a finding worth reporting, and the
  operator decides then, with the trade-off in front of them rather than
  assumed away.

Forcing it in *before* checking would convert a probably-harmless
preference into a documented hindsight selection for no gain.

**Why 2018 turnover rather than market cap.** Turnover measures the thing
§2.5 actually requires — that the position can be held. Market cap does
not: a large, thinly-traded name breaks the portfolio arithmetic. Using
the calendar year *preceding* the window keeps the ranking strictly
point-in-time.

### 4.3 Signal on spot, execute on futures — this removes the largest new risk

The original sketch had MS-D building continuous back-adjusted
single-stock-futures series. **Drop that.** Instead:

- **Signal** is computed on the **underlying's adjusted spot daily close**
  — a continuous series with no roll and therefore no back-adjustment
  freedom.
- **Execution** is the single-stock future, with the quarterly roll
  treated as a **cost**, not as a signal input.

This matters because a back-adjusted series has real discretion in it
(ratio vs difference adjustment, roll date, roll trigger), and a strategy
that only works under one convention has not been shown to work. §8
listed that as a way this fails; this design removes it rather than
managing it. The assumption it substitutes — that daily futures returns
track spot returns net of carry — is small, well understood, and
measurable at MS-C.

### 4.4 The candidate pool, and its one unavoidable compromise

The rule needs a **point-in-time list of what was listed and futures-
eligible in 2018**. Whether KIS serves one is unknown (§5 item 7).

If it does not, the fallback is to rank **today's KOSPI200 constituents**
by 2018 turnover and **disclose that the candidate pool is
survivorship-filtered**. That bias is real: KOSPI200 membership turns
over roughly 10%/year, so a 7-year window has meaningful churn, and names
that fell out are absent. It is also bounded and reportable — record how
many of the ten selected are 2018-vintage large caps that would obviously
have qualified, versus names whose presence depends on the filter.

**Ranking by turnover necessarily reads price/volume rows for the
candidate pool.** That is disclosed rather than glossed: the *rule* is
fixed before the pull, the *selection* uses only 2018 traded value and
never returns from the scored window, and the resolved list is committed
to the pre-registration before MS-F scores anything. This is the same
handling `sr-aa` gave its own disclosed prior-access caveat.

Properties, stated against §3:

- **Selection bias**: substantially reduced, not zero. The rule is
  point-in-time and could have been executed in 2019. Residual exposure
  is that the author knows roughly how 2019's Korean large caps fared;
  disclosed rather than claimed away, in the same spirit as `sr-aa`'s own
  disclosed price-level caveat.
- **Survivorship bias**: absent from the *selection* step by construction
  — nothing about a name's fate after 2019-01-02 affects whether it is
  chosen, and the exit rule handles what happens to it. What remains is
  the **candidate pool** exposure in §4.4, which is a different and
  narrower problem: the pool says what there was to rank, not which
  survivors to prefer.
- **Concentration**: the three-per-sector cap is what stops the list
  becoming a semiconductor basket. It is a real constraint on the
  operator's suggestion and the reason 삼성전자 and SK하이닉스 will likely
  both appear but with eight non-semiconductor names beside them.
- **Shortable**: single-stock futures, so yes.

### 4.5 Rejected alternatives, and why

- **Sector indices (20+, zero selection bias)** — the cleanest research
  universe available and rejected on §2.5: not tradeable, so it proves
  the phenomenon without delivering the portfolio. Worth keeping as a
  *supporting* diagnostic if the data turns out to be free to fetch,
  never as the promotion candidate.
- **Names chosen by the operator** — §3.1.
- **All ~140 single-stock futures** — better statistically, but the
  pre-registration must enumerate the universe, and 140 underlyings means
  140 data pulls, 140 multiplier confirmations, and a much larger build
  before the first result. K=12 already delivers §2.4's benefit; the
  marginal gain from 12→140 at ρ≈0.6 is small (vol multiplier 0.800 →
  0.776).

---

## 5. What is not verified, and must be before anything is built

**Nothing below is known. Every item is an assumption this project would
be repeating its own worst habit by acting on.** The precedent is
`scalp-s1`'s retention probe and `sr-t`'s: measure, then design.

### Phase 0 probes, in order — this is a real go/no-go gate

**Documentation review, 2026-09-13.** Items 1, 2 and 6 were researched
against KIS's own developer materials before writing this. What follows
is *read*, not *called* — it sharpens the probes and replaces none of
them. This project's own record has a case of documented behaviour being
wrong in practice (KIS response casing, three endpoints, two guessed
wrong).

1. **The endpoint appears to exist.**
   `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`,
   `tr_id` `FHKST03010100`, parameters `FID_COND_MRKT_DIV_CODE` (`J` =
   주식/ETF/ETN), `FID_INPUT_ISCD` (6-digit code), `FID_INPUT_DATE_1` /
   `_2` (YYYYMMDD range), `FID_PERIOD_DIV_CODE` (`D`/`W`/`M`/`Y`),
   `FID_ORG_ADJ_PRC`. Response splits `output1` (summary) / `output2`
   (the daily rows). **Max 100 rows per call**, so a 10-year pull is ~25
   calls per symbol — 300 calls for K=12, which is trivially within any
   rate limit. Indices are believed to use
   `…/inquire-daily-indexchartprice` (`FHKUP03500100`, market division
   `U`, `0001` KOSPI / `1001` KOSDAQ / `2001` KOSPI200) — **materially
   less well attested than the stock endpoint and squarely a probe
   target.**
2. **모의투자 appears to be supported for this quotation TR** — the same
   `tr_id` on the paper host, unlike the trading TRs' `V`-prefix
   convention. KIS's own guidance nonetheless recommends the production
   host for bulk historical pulls, because the paper host's throughput
   limits are tighter. **Still verify**: CLAUDE.md records
   `inquire-deposit` (`CTRP6550R`) as having no working paper TR at all —
   the real id returns `HTTP 500`/`EGW00205`, the `V`-variant `OPSQ0002`.
   **If the paper host works, use it and §7's production-host decision
   never arises.**
3. **How deep is the history?** This is the single highest-leverage
   unknown. The detection floor is `1.6449/√years`:

   | Window | Floor | Comparison |
   |---|---|---|
   | 5 years | 0.74 | ≈ this project's ETH window |
   | 10 years | 0.52 | **better than anything this project has** |
   | 20 years | 0.37 | comfortably below the credible 0.4-0.8 edge band |

   Korean daily equity history plausibly runs decades. If it does, this
   window is by a wide margin the best-powered data this project has ever
   held, and that changes the whole calculus.
4. **Gaps and holidays.** KRX trades ~245 days/year with lunar-calendar
   holidays. `KrxMarketCalendar` already documents moving lunar holidays
   as a known unresolved gap ("the JDK ships no chronology that expresses
   them"). Bar iteration in `python/backtest/` is **pure positional
   arithmetic with no gap detection** (CLAUDE.md's disclosed
   gap-blindness). A five-day Chuseok closure is not a data gap, but the
   pipeline must record the real trading calendar so `verify_known_gaps`
   has something true to check against.
5. **Single-stock-futures contract multiplier**, and their **liquidity
   distribution**. The multiplier is the fact that already blocks
   `STOCK_FUTURES` from starting. Liquidity is the newer worry: KRX
   single-stock-futures volume concentrates heavily in a few underlyings,
   and if only three or four are genuinely liquid then K=12 is not
   achievable *in tradeable form* and §2.5's trap has been walked into
   from the other side. **Measure the real futures turnover per
   underlying, not just the spot's.** Continuity is no longer a probe
   target — §4.3 signals on spot and never constructs a back-adjusted
   futures series.
6. **Corporate actions — and the documentation says the trap is armed by
   default.** `FID_ORG_ADJ_PRC` selects adjusted versus raw: **`0` =
   수정주가 (adjusted), `1` = 원주가 (raw)**. KIS's own published Python
   sample **defaults to `1`** — so code written by copying the official
   example returns *unadjusted* prices. 삼성전자's 50:1 split (2018-05)
   sits at the edge of a 10-year window and an unadjusted series shows a
   ~−98% single-day return, which a momentum signal reads as a crash.
   **Verify by calling it across a known split and checking the printed
   number, not by trusting the parameter.**

   Also documented and load-bearing: **KIS's 수정주가 adjusts for splits
   but not for dividends.** Korean large caps yield roughly 2%/year, so a
   7-year price series understates total return by a meaningful margin.
   For a *direction* signal this is second-order — dividends are small
   against daily volatility — but it must be disclosed, and it means the
   reported return is a price return, not a total return.
7. **A point-in-time listing / futures-eligibility universe for 2018.**
   §4.4 needs it and it may not exist on this API. If it does not, §4.4's
   survivorship-filtered fallback applies with its bias disclosed. This
   item was added after §4.4 was written; it is the rule's real
   dependency and was not in the original list.

**Item 6 is the one most likely to produce a wrong answer that looks
right**, and the documentation review made it worse rather than better:
the default is the wrong value. It is the Korean-market analogue of the
quantity-precision incident — the venue does something silently and
nothing downstream notices.

---

## 6. Task breakdown

Each its own PR, per GSD. **Nothing past MS-B starts until MS-B's
findings are on record.**

| Task | What | Gate |
|---|---|---|
| **MS-A** | this document | operator sign-off on §4.2 and §7 |
| **MS-B** | Phase 0 probes (§5), read-only, no strategy code. Report every finding honestly including "not available". | **go/no-go.** If daily history is unavailable or shallow, the direction changes here, and that is a real possible outcome |
| **MS-C** | `python/data/kis_klines.py` + store schema for KRX symbols; full backfill with independently verified gap/holiday counts, matching the standard every other granularity in this project was held to. **Also reports the measured Korean internal and Korea-vs-crypto correlations**, so §2.4 is recomputed against real figures before MS-F | a real backfill with a real gap count, not a probe estimate |
| **MS-D** | Corporate-action verification (§5 item 6) and the futures-vs-spot basis/roll cost model. **No longer builds a back-adjusted futures series** — §4.3 removed that | the adjusted series reproduces a known split correctly, checked against the printed number |
| **MS-E** | Universe resolution: apply §4.2's rule, record the selected list, the sector distribution, **where SK하이닉스 lands**, and the §4.4 pool caveat. **Committed to a pre-registration before MS-F scores anything** | pre-registration filed |
| **MS-F** | Portfolio TSMOM as a **single pre-registered holdout confirmation**: `daily_tsmom_ensemble.py` unchanged per constituent, equal-weight aggregation, measured ρ reported against §2.4's assumptions | the Eligibility Bar's **single-window variant** — PSR ≥ 0.95 and the non-fold criteria, all pinned before access (§7 item 3) |

`daily_tsmom_ensemble.py` must not be modified. Its zero-fitted-parameter
property is what the Paper Trading Policy Exception rests on, and the
portfolio layer belongs above it, not inside it.

---

## 7. Open decisions — operator's, not mine

1. ~~**§4.2's universe rule.**~~ **DECIDED 2026-09-13**: the rule, ranked
   by 2018 traded value, with a three-per-sector cap. SK하이닉스 is
   expected to be selected by the rule; MS-E reports where it lands
   rather than forcing it. See §4.2.

2. **The production-host question (§5 item 2).** If quotations only work
   against KIS's real host, this project would run a credentialed
   read-only research client against a production endpoint for the first
   time. Binance is read-only *without* credentials; KIS quotes need an
   app key. Proposed constraint if it comes to that: the Python client is
   structurally incapable of order placement (no order path compiled in,
   no account number, quotation TR ids only), and it never shares a
   module with anything that can submit. **This needs explicit approval;
   it is adjacent to "never let Python place live orders directly."**

3. **A trial budget, committed in advance — and *not* a way to lower the
   DSR bar.** The one process change that would actually break §1's
   pattern:

   > This window gets **at most N trials**, declared in the
   > pre-registration before first access. When the budget is spent the
   > window is closed to selection, whatever the results.

   Every prior window closed by *post-hoc accounting* — the 1h window
   ended at 117 because someone counted afterward, not because a budget
   was set.

   **An earlier draft of this section then claimed the budget lowers the
   DSR-0.95 requirement from ~4.00 to ~2.17. That was wrong**, and it was
   an instance of precisely the reasoning CLAUDE.md flags as "most open to
   abuse." The Eligibility Bar defines its DSR against the **project-level**
   `research_selection_trials`, deliberately, "because strategy families in
   this project were compared against each other after their results were
   known." A budget declared for MS-F does not retire that history, and
   a family-level `N` is a supplementary report, never the gate.

   **The correct structure needs no gate change at all.** MS-F should run
   as a **pre-registered holdout confirmation under the Eligibility Bar's
   single-window variant**, which this project has already used four times
   (`sr-u`/`sr-v`, `sr-aa`/`sr-ab`) and which is explicit about why:

   > Deliberately **PSR, not DSR**: the holdout was never searched over —
   > one access, one run, on data no decision has touched — so there is no
   > selection bias to deflate, and `N`=1 makes DSR identical to PSR
   > anyway.

   Korean equity data satisfies that condition literally — zero trials in
   `runs/experiments.jsonl` have ever touched it. So MS-F's bar is **PSR ≥
   0.95**, plus the non-fold criteria (drawdown ≤20–25%, the trade-count
   floor, profit factor ≥1.3–1.5), plus the requirement that observed
   Sharpe exceed the window's own detection floor — all **pinned before
   access**, per the "in force at the time means pinned before, not chosen
   after" clause.

   The budget's job is therefore what it says and nothing more: it caps
   how much searching may happen, and **a run that spends more than one
   trial is not a single-access holdout confirmation any more.** So either
   MS-F is one pre-registered run at `N`=1 under PSR, or it is a research
   study whose results cannot be promoted — and those two must not be
   blurred after seeing which is more flattering.

   **Recommendation: one pre-registered run, `N` = 1.** The budget concept
   still binds anything that follows it.

4. **Scope of the crypto leg.** §2.4's best rows include BTC+ETH
   alongside the Korean members. Adding ETH means spending its untouched
   window (§2.3) — a holdout decision, i.e. CLAUDE.md human checkpoint
   #2. It can equally be deferred: Korea ×10 alone clears both gates.
   **Recommendation: defer.** Prove the Korean leg first; the crypto leg
   is a later, separate, cheaper addition and keeping it unspent
   preserves an independent confirmation source.

---

## 8. How this fails

Named in advance so the outcome is judged against them rather than
rationalised afterward.

- **MS-B finds no usable daily history**, or only paper-host access with
  a short window. Then the direction is wrong and MS-A's arithmetic never
  gets tested. Real possibility.
- **Corporate actions turn out to be unadjusted** and the first result is
  spectacular and false (§5 item 6).
- **Korean internal correlation is 0.85, not 0.60.** §2.4's pessimistic
  row assumed 0.75; 0.85 at K=10 gives a vol multiplier of 0.93 and a
  drawdown of 18.7% — still passing, but the Sharpe gain mostly
  disappears. Measurable at MS-C, before any strategy runs.
- **The roll construction (MS-D) turns out to drive the returns.** A
  back-adjusted series has real freedom in it, and a strategy that only
  works under one roll convention has not been shown to work.
- **Single-stock-futures liquidity is concentrated** (§5 item 5) and only
  three or four underlyings can actually be held, collapsing K.
- **It works and is still not promotable**, because the Live Entry
  Criteria's separate gates — market-order guard, stale-data check,
  ambiguous-submission recovery for KIS (all three open per CLAUDE.md) —
  remain unmet. **This is the expected outcome of a successful MS-F**,
  and it should surprise nobody: research passing is not deployment.

---

## 9. "There's more information here than for Bitcoin, isn't there?"

The operator's question, and the answer is **yes, substantially — and
that is a hazard at least as much as an advantage.**

### What is genuinely available here and absent for BTC

| | BTC | Korean equities |
|---|---|---|
| History | 5.3 y (BingX 1d), 9.0 y (Binance spot) | plausibly decades — **the single biggest gain, see below** |
| Fundamentals (earnings, book value, dividends) | none exist | full, and long |
| Cross-sectional structure | 1 asset | 10+, so *relative* strategies become expressible at all |
| Investor-type daily flow (외국인 / 기관 / 개인) | no equivalent at this quality | published by KRX daily |
| Sector / index membership | none | structural, and stable |

**The history depth is the one that changes the arithmetic**, because the
detection floor is `1.6449/√years` and nothing else in this project moves
it:

| Window | Floor | |
|---|---|---|
| BTC 1d holdout (2.95 y) | 0.96 | everything failed against this |
| Binance futures 1m (6.96 y) | 0.62 | this project's current best |
| 10 y | **0.52** | better than anything held today |
| 20 y | **0.37** | comfortably below the 0.4–0.8 credible edge band |

At a 20-year floor of 0.37, a real institutional-grade edge is
*detectable* rather than merely *not excluded* — which has never once
been true in this project's history. That, not the extra feature columns,
is the reason this direction is worth the build.

### Why the extra features are a hazard

`N` is what killed every prior direction. More available inputs means
more configurations that *could* be tried, and the failure mode is not
running out of ideas — it is spending the window's evidentiary value on
them. Fundamentals plus flow plus cross-section is a search space orders
of magnitude larger than "which momentum lookback", and this project's
own record is 1,472 of 1,883 runs being variants of one question.

**So the extra information is explicitly out of scope for MS-F.** MS-F
runs `daily_tsmom_ensemble.py` unchanged, on price only, under §7's N=5
budget. The other sources are catalogued here so they are not
rediscovered later as if new, and any use of them is a **separate task
with its own pre-registration and its own budget** — never an extension
of MS-F after seeing its result. Extending a registration after results
is the specific thing CLAUDE.md's stopping rule forbids.

The honest summary: **more data buys statistical power, and it buys
nothing at all if the power is spent searching.** The budget is what
converts one into the other.
