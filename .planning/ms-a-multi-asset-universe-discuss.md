# Multi-Asset TSMOM Task A — `Discuss`: why one asset was the missing piece, and the trap in picking Korean stocks by name

**Status**: Discuss. Nothing built, no data fetched, no code written.
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

Equal-weight, per-constituent Sharpe 1.305 and drawdown 20.135% held
constant at `sr-ab`'s observed values; block-correlated structure.

| Structure | Vol multiplier | Portfolio Sharpe | Portfolio DD |
|---|---|---|---|
| BTC alone (today) | 1.000 | 1.305 | 20.1% — **fails** |
| BTC+ETH | 0.960 | 1.359 | 19.3% |
| Korea ×10 only, internal ρ=0.60 | 0.800 | 1.631 | 16.1% |
| Korea ×10 only, internal ρ=0.75 (pessimistic) | 0.880 | 1.482 | 17.7% |
| BTC+ETH + Korea ×10, ρ_int=0.60, ρ_cross=0.387 | 0.760 | 1.717 | 15.3% |
| BTC+ETH + Korea ×10, ρ_int=0.75, ρ_cross=0.387 | 0.819 | 1.593 | 16.5% |

Trade count scales with K: 64 → ~768 at K=12, against a floor near 100.

**Every scenario, including the pessimistic one, clears both gates that
`sr-ab` missed.** That is the case for this direction.

### 2.5 What this arithmetic assumes, and why §4 exists

It assumes **all K constituents are actually held**. A portfolio Sharpe
of 1.72 across 12 instruments requires 12 simultaneous positions. Proving
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

### 4.2 The recommended universe rule

**A rule, committed before data access, not a list of names.**

> **KR-10**: the ten single-stock-futures underlyings with the largest
> KOSPI market capitalisation **as of 2019-01-02** (the window start),
> subject to (a) a continuous single-stock-futures listing across the
> whole window, and (b) **no more than three from any one KRX sector
> classification**, taking the largest by that date's market cap where
> the cap binds. Plus **KOSPI200 futures** and **KOSDAQ150 futures** as
> two index members. Target K = 12.

Properties, stated against §3:

- **Selection bias**: substantially reduced, not zero. The rule is
  point-in-time and could have been executed in 2019. Residual exposure
  is that the author knows roughly how 2019's Korean large caps fared;
  disclosed rather than claimed away, in the same spirit as `sr-aa`'s own
  disclosed price-level caveat.
- **Survivorship bias**: condition (a) is a survivorship filter and
  therefore *introduces* bias — it excludes names that stopped trading.
  The honest handling is to **record which underlyings the rule selects
  and then drop for failing (a)**, and report the count. If it is zero or
  one, the bias is negligible and provably so. If it is several, the
  result is reported with that exposure named.
- **Concentration**: the three-per-sector cap is what stops the list
  becoming a semiconductor basket. It is a real constraint on the
  operator's suggestion and the reason 삼성전자 and SK하이닉스 will likely
  both appear but with eight non-semiconductor names beside them.
- **Shortable**: single-stock futures, so yes.

### 4.3 Rejected alternatives, and why

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

1. **Does KIS serve historical daily OHLCV at all, and from which host?**
   The believed endpoints are
   `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`
   (stocks/derivatives, ~100 rows per call) and
   `…/inquire-daily-indexchartprice` (indices). **Both are unconfirmed
   against this project and read from documentation, not called.**
2. **Does it work on the 모의투자 host?** CLAUDE.md already records a case
   where it does not: `inquire-deposit` (`CTRP6550R`) "has no working
   paper TR id at all" — the real id returns `HTTP 500`/`EGW00205` and
   the `V`-prefixed variant returns `OPSQ0002`. Assume nothing.
   **If quotations require the production host, that is a security
   decision, not a detail — see §7.**
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
5. **Single-stock-futures contract multiplier and continuity.** The fact
   that already blocks `STOCK_FUTURES` from starting. Also: single-stock
   futures roll quarterly, so a continuous back-adjusted series has to be
   *constructed*, and how it is constructed is a real design decision
   that changes returns. This project has never handled a rolling
   contract.
6. **Corporate actions.** Splits, dividends, mergers. `삼성전자`'s 50:1
   split (2018-05) sits inside a 10-year window and an unadjusted series
   would show a −98% single-day return that a momentum signal would read
   as a crash. **Whether KIS returns adjusted or raw prices must be
   confirmed empirically, per symbol type.** This is the most likely
   source of a silent, catastrophic, plausible-looking result.

**Item 6 is the one most likely to produce a wrong answer that looks
right.** It is the Korean-market analogue of the quantity-precision
incident: the venue does something silently and nothing downstream
notices.

---

## 6. Task breakdown

Each its own PR, per GSD. **Nothing past MS-B starts until MS-B's
findings are on record.**

| Task | What | Gate |
|---|---|---|
| **MS-A** | this document | operator sign-off on §4.2 and §7 |
| **MS-B** | Phase 0 probes (§5), read-only, no strategy code. Report every finding honestly including "not available". | **go/no-go.** If daily history is unavailable or shallow, the direction changes here, and that is a real possible outcome |
| **MS-C** | `python/data/kis_klines.py` + store schema for KRX symbols; full backfill with independently verified gap/holiday counts, matching the standard every other granularity in this project was held to | a real backfill with a real gap count, not a probe estimate |
| **MS-D** | Continuous-contract construction for single-stock futures (roll rule), and corporate-action verification. Its own design note — this is genuinely new territory here | correctness shown against a known split |
| **MS-E** | Universe resolution: apply §4.2's rule, record the selected list, the dropped-for-(a) count, and the sector distribution. **Committed to a pre-registration before MS-F touches price data for scoring** | pre-registration filed |
| **MS-F** | Portfolio TSMOM: `daily_tsmom_ensemble.py` unchanged per constituent, equal-weight aggregation, measured ρ reported against §2.3's assumptions | the Eligibility Bar in force at the time, pinned before access |

`daily_tsmom_ensemble.py` must not be modified. Its zero-fitted-parameter
property is what the Paper Trading Policy Exception rests on, and the
portfolio layer belongs above it, not inside it.

---

## 7. Open decisions — operator's, not mine

1. **§4.2's universe rule.** It overrides the requested "삼성전자,
   SK하이닉스 등" with a mechanical rule. 삼성전자 and SK하이닉스 will very
   likely still be selected; the difference is that the rule chose them.
   **If the named-list approach is preferred anyway, that is a legitimate
   call, but the result then cannot support a promotion decision and the
   write-up must say so.**

2. **The production-host question (§5 item 2).** If quotations only work
   against KIS's real host, this project would run a credentialed
   read-only research client against a production endpoint for the first
   time. Binance is read-only *without* credentials; KIS quotes need an
   app key. Proposed constraint if it comes to that: the Python client is
   structurally incapable of order placement (no order path compiled in,
   no account number, quotation TR ids only), and it never shares a
   module with anything that can submit. **This needs explicit approval;
   it is adjacent to "never let Python place live orders directly."**

3. **An `N` budget, committed in advance.** The one process change that
   would actually break the pattern in §1:

   > This window gets **at most N trials**, declared in the
   > pre-registration before first access. When the budget is spent the
   > window is closed to selection, whatever the results.

   Every prior window closed by *post-hoc accounting* — the 1h window
   ended at 117 because someone counted afterward, not because a budget
   was set. **Proposed for MS-F: N = 5.** Low enough that DSR 0.95 needs
   ~2.17 Sharpe rather than ~4.00, which §2.4's projections can plausibly
   reach; high enough for a genuine mistake to be corrected once.

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
- **It works and is still not promotable**, because the Live Entry
  Criteria's separate gates — market-order guard, stale-data check,
  ambiguous-submission recovery for KIS (all three open per CLAUDE.md) —
  remain unmet. **This is the expected outcome of a successful MS-F**,
  and it should surprise nobody: research passing is not deployment.
