# Research Direction Task C — the mechanism catalogue

**Status**: research synthesis. **No return of any kind was computed, no
strategy was run, no holdout was touched.** This document is a literature
review and a proposal, written to be corrected. It spends no `N`.

One thing here *was* measured after the first draft: §8's top action was
a read-only KIS capability probe, and it was run. Its result is
[`rd-c-kis-flow-probe-result.md`](rd-c-kis-flow-probe-result.md), and it
**corrected §7 in the favourable direction** — §6 and §7 below carry the
measured figures rather than the documentation's.

It exists because
[`rd-b-situations-not-formulas.md`](rd-b-situations-not-formulas.md) §4.1
said its own catalogue was the wrong end to start from — *"assembled by
reading rather than trading"* — and committed that the **mechanism column
must come from the operator**. This is the researcher's half done
properly: everything findable in the literature, sorted by how much it is
actually worth, so the operator is correcting a real draft rather than
filling a blank page.

Fifteen searches. What follows separates what is **measured** from what is
**asserted by someone selling a course**, because a catalogue that treats
those the same is worse than no catalogue.

---

## 1. The base rate, and the one fact that refutes the pessimistic reading

The operator's objection was that this project's conclusions imply every
retail strategy is worthless and every profitable trader is lying. That
objection is correct, and the literature settles it in the operator's
favour — **but not for the reason usually given.**

**Barber, Lee, Liu & Odean**, on the complete transaction record of the
Taiwan Stock Exchange, 3.7 billion transactions, 1992–2006, ~450,000 day
traders per year — the largest study of day trading ever conducted:

| | |
|---|---|
| Average day trader | **−23.9 bps/day net**, negative in 14 of 15 years |
| Same, **gross of costs** | **−7 bps/day** |
| **Share of the loss that is transaction costs** | **71%** |
| Profitable net in any given period | ~20% |
| Consistently profitable | ~5% |
| **Reliably, predictably profitable** | **<1%** (~4,000 people) |
| **The top 500** | **+37.9 bps/day**, and **performance persists year over year** |

Brazil (Chague, De-Losso & Giovannetti, 2020): of ~1,600 traders who
persisted past 300 sessions, **97% lost money and 1.1% out-earned the
minimum wage.** Survival in Taiwan: 44% / 24% / 15% at one / two / three
years.

**The last row of that table is the whole answer.** The top 500's
performance *persists across years*, which luck cannot produce. The paper
that establishes the 97%-lose figure is the same paper that establishes
skill is real. Both are true and neither cancels the other.

**Two things follow immediately, and both are actionable here:**

1. **71% of the average day trader's loss is transaction costs.** Gross
   −7 bps is a *nearly fair game*; the destruction is the fee. This is
   not a motivational aside — it is the highest-leverage design variable
   available, and it is the one this project can control absolutely.
   `scalp-s9` already found the same shape from the other direction: the
   BTC taker fee dominates the spread by ~330×. **Trade less, or trade
   where costs are lower, and you start ahead of the median participant
   by more than any signal in this document is worth.**
2. **Experience does not produce skill.** Barber et al.'s companion paper
   (*Do Day Traders Rationally Learn About Their Ability?*) found the most
   experienced day traders lose money, **~74% of day-trading volume comes
   from traders with a history of losses**, and a profitable trader is
   96.4% likely to trade again next year against 95.3% for an unprofitable
   one — performance barely affects behaviour. Skill in this population is
   **present from early on and concentrated**, not accumulated. Which means
   the winners are doing something *structurally different*, not the same
   thing better.

## 2. The convergent finding — the filter is the strategy, not the entry

This is the most important result in the entire review, and it comes from
**four independent sources that do not cite each other**.

**(a) Barber, Lee, Liu & Odean, *The Cross-Section of Speculator Skill***
— what actually predicts a day trader's future performance, out of
everything measurable in an exchange's full transaction record:

| Predictor | Finding |
|---|---|
| **Past performance** | by a large margin the best predictor |
| **Concentration of trading in a few stocks** | the second, *"consistent with successful day traders focusing narrowly to garner an informational advantage"* |
| Share of passive / liquidity-providing trades | **economically weak** — the winners are not simply market-making |

**(b) Zarattini, Barbon & Aziz (2024), "Stocks in Play"** — 7,000+ US
stocks, 2016–2023. A plain opening-range breakout, but restricted to names
with *abnormal* activity that day; the top-20 portfolio returns **+1,600%
net, Sharpe 2.81, ~36% annualized alpha**, with an independent QuantConnect
replication at Sharpe 2.4, beta ≈ 0. *(Conflict of interest: the authors
are affiliated with Peak Capital Trading, which sells day-trading
education.)*

**(c) The contradicting ORB study** — a systematic falsification on MNQ
futures found **every** opening-range-breakout variant failed once 2 points
of friction were applied (ORB Long, bar+1: −0.82 pts, t = 1.17, 51.9% win
rate). Same entry rule. **No selection filter.**

**(d) This project's own record** — S11 found the conditional move clears
costs only in the **top 10% of activity** (1.09× round trip) and **top 1%**
(2.08×); S16's only configuration ever to clear a significance test
(t = +2.388, p = 0.0098) was the one gated at `|z| ≥ 6`, roughly the top
0.1%. The `entry_z = 5.0` cell — the one every earlier run used — was the
weaker of the two, and declaring the signal dead from it was CLAUDE.md's
recorded fifth instance of the same error.

**Read together: (b) and (c) are the same entry rule with and without a
selectivity filter, and the difference between them is the entire result.
(a) says the same thing about human traders. (d) says the same thing about
this project's own data.**

> **The catalogue below is therefore not a list of entries. It is a list
> of filters.** What this project has done 1,883 times is search for a
> better formula; CLAUDE.md's own audit of `runs/experiments.jsonl` states
> it plainly — *"every `strategy_id` in the log asks the same question:
> which formula predicts direction."* Four independent lines of evidence
> say that is the wrong question.

## 3. How to read the evidence column

A catalogue that ranks a *Journal of Finance* paper alongside a YouTube
indicator is not a catalogue. Every entry carries one of these:

| Tier | Meaning |
|---|---|
| **A** | Peer-reviewed empirical work on real transaction or price data |
| **B** | Practitioner whose *record* is externally verifiable (regulatory registration, audited results, a documented competition) |
| **C** | Practitioner-only. Coherent, widely taught, **no independent verification of the claim or the claimant** |
| **D** | **Tested and failed**, or tested and found to be an artifact |

Tier C is not worthless — it is where most trading vocabulary lives, and
Osler's tier-A work is essentially a test of a tier-C folk claim that
turned out to be right. But a tier-C mechanism is a **hypothesis to
measure**, never a reason to trade.

## 4. The unifying test — what makes something a mechanism at all

S8's standing rule is *"a hypothesis must name a mechanism — who is on the
other side and why they lose."* Reviewing fifteen sources, **every real
mechanism found reduces to exactly one of three things**, and this makes
the rule checkable rather than a matter of taste:

| # | Type | Why the other side loses |
|---|---|---|
| **F** | **Forced flow** | Someone must trade *now* and cannot choose the price. A stop order is an **involuntary market order**; a liquidation engine emits *price-insensitive* market orders; a margin call has no opinion about value. |
| **I** | **Informed flow** | Someone knows something. You are not beating them — you are identifying and following them, or avoiding being their counterparty. |
| **S** | **Structural** | A rule of the venue forces a price: an auction that must clear, a settlement cycle, a price limit, an index rebalance. |

**If a proposed situation is none of the three, it is a formula wearing a
costume.** That is the filter to apply to any addition to this catalogue —
including the operator's own.

The single sentence that generalises all of tier F:

> **Whoever must trade now pays. Whoever can wait gets paid.**

Every entry below marked **F** is a specific answer to "who must trade now,
and how do I know?"

## 5. The catalogue

`Held?` is measured against what is actually in `python/data/var/klines.sqlite3`
today (verified, §7). **"obtainable"** means the probe confirmed KIS serves
it but it is not collected yet — a backfill, not a research question.

| # | Situation | Mechanical definition | Who is forced / informed | Type | Tier | Held? |
|---|---|---|---|---|---|---|
| 1 | **Stop run / spring / liquidity sweep / turtle soup** | prior-K low broken by p%, price returns above within N | protective stops below an obvious level — **involuntary market orders** | F | **A** (mechanism) / C (rule) | **yes** (BTC 1m); KRX **obtainable**, ~250 sessions |
| 2 | **Failed breakout / upthrust** | prior-K high broken, closes back inside within N | breakout buyers, whose own stops now sit under the level — forced twice | F | C | as above |
| 3 | **Round-number stop clustering** | distance to the nearest round price, and which side of it | take-profit **limit** orders cluster *at* round numbers; stop-loss **market** orders cluster *just past* them | F | **A** — Osler | **yes**, fully |
| 4 | **Abnormal-activity conditioning** | today's volume / relative volume vs its own recent distribution | not forced flow — *participation*. Abnormal volume means a real information event, so moves persist rather than mean-revert | I | **A** (a), B/C (b) | **yes** |
| 5 | **Opening range** | first N minutes' high/low; break = entry, with a range-size filter | positions taken into the open are wrong-footed; the IB frames the day's auction | F/S | **B** contested, **D** unfiltered | **obtainable** — KRX intraday, ~250 sessions |
| 6 | **Previous-day high/low pivot** | approach to yesterday's extreme; **branch**: test-and-reverse *or* push-through-with-continuation | the most-watched level in the market, so the densest stop cluster | F | **B** — Raschke | **yes** daily; KRX intraday **obtainable** |
| 7 | **Range expansion after contraction** | volatility compression (NR7, narrow band), then a decisive break | every position built inside the range is wrong on the break; volatility sellers must hedge | F | **B** — Raschke ("the market alternates between range expansion and contraction") | **yes** |
| 8 | **First pullback after a new high** | new K-bar high, then a retracement that holds | late entrants shaken out; trend followers add. **Weak mechanism** — no one is truly forced | — | B | **yes** |
| 9 | **Second entry (H2 / L2)** | second failed attempt to pull back against the trend | traders stopped out on the *first* attempt, now chasing | F | C — Brooks | **yes** |
| 10 | **Absorption** | large volume at one price with no price progress | the **aggressor** is forced (must trade now); the passive side chose the price. The purest statement of tier F | F | C (vendor); the underlying trade-sign literature is A | partial — taker flow on Binance 1m only |
| 11 | **Value area / POC reversion** | price outside the prior session's 70% volume band, reverting toward the POC | **no forced party identified.** A statistical regularity claim | — | C, vendor backtests only | **obtainable** (intraday); no volume-at-price, only bar volume |
| 12 | **Fair value gap / imbalance** | a 3-bar gap where wick and wick do not overlap | claims unfilled institutional orders. **Unverified who** | ? | C, one vendor test at 64.8% mitigation | **yes** mechanically |
| 13 | **Order block** | last opposing candle before a displacement move | claims institutional accumulation. **Unverified who** | ? | **D** on daily bars | **yes** mechanically |
| 14 | **Liquidation cascade air pocket** | forced liquidations execute against a thinned book | **the liquidation engine itself** — literally price-insensitive forced market orders. The most unambiguous tier F that exists | F | **A** (mechanism), **D** (prediction) | **no** — liquidation feed not collected |
| 15 | **Overnight gap → intraday reversal** | close-to-open return predicts open-to-close return, negatively | opening-auction overpricing from attention-driven retail buying that cannot be cancelled late in the auction | S | **A** (CN, IN, KR) | **partial** — needs KRX open/close split, which daily bars have |
| 16 | **Investor-type flow imbalance** | daily net buy by 개인 / 기관 / 외국인, per stock | **informed institutional flow in small caps**; retail limit orders earn a **liquidity-provision premium** in large caps | I | **A** — see §6 | **30 trading days only, and this is the finding** |

### 5.1 The entries that deserve more than a table row

**#3 — Osler (2003, NY Fed Staff Report 150; 2005, *JIMF*). The one
peer-reviewed foundation the whole tier-F family has.** Minute-by-minute
USD/DEM, USD/JPY, USD/GBP, Jan 1996–Apr 1998, bootstrap methodology
against real order data. Findings: price trends are **unusually rapid**
after rates cross documented stop-loss clusters; trends **reverse** at
take-profit clusters; and the stop-loss response is **larger and longer-
lasting** than the take-profit response.

The part that makes it *testable rather than merely suggestive* is the
**asymmetry**: take-profit clusters sit **at** round numbers, stop-loss
clusters **just past** them. Two different order types, two opposite
predicted price behaviours, at two measurably different distances from the
same reference price. That is a hypothesis with a built-in falsification.

**The skeptical counter, which must be carried alongside it**: the
acceleration may come from the book **thinning** at rejected prices — the
*absence* of opposing limit orders — rather than from any deliberate stop
hunt. Both stories predict acceleration; they differ on whether anyone is
doing it on purpose. And there is a real identification problem: stop
locations are unobservable, so round numbers are only a *proxy*. Osler had
actual order data; we would not.

**#1/#2 — one footprint, four vocabularies.** Wyckoff's **spring** and
**upthrust**, the modern **liquidity sweep** / **stop run**, Raschke's
**turtle soup**, and ICT's **liquidity grab** are the same mechanical
event described by four communities that mostly do not read each other.
That convergence is mild evidence the pattern is real and strong evidence
that *none of them has independently verified it*.

Three groups are forced at once: holders stopped out below the level,
breakout sellers who entered on the break, and late entrants chasing.
Practitioner figures cited — UTAD volume at 100–200% of average, the
confirming test occurring only 40–60% of the time — come from teaching
material, **not from microstructure research**.

**The discriminator is acceptance versus rejection, and it is only
resolvable after the fact.** This is exactly rd-b §3.2's finding, arrived
at independently: *"개미털기 is a label applied in hindsight"*, the 81.0%
recovery rate is not an edge, and the real question is what is observable
**at the instant of penetration**. The literature does not answer it
either. That makes it a genuinely open research question rather than
something already solved and merely unread.

**#14 — the mechanism is settled, one specific prediction attempt is
settled negative, and these are different claims.** The 2025-10-10/11 event
liquidated ~$19B across 1.6M traders with BTC −14%. Garcia Seuma,
[arXiv:2607.27070](https://arxiv.org/abs/2607.27070) and
[arXiv:2608.03616](https://arxiv.org/abs/2608.03616), examine **seven major
BTC liquidation cascades, 2022–2025**, sweeping **39 configurations per
variable per event** over the standard critical-slowing-down early-warning
set — **rolling variance and lag-1 autocorrelation**, both computable from
OHLCV. They find **no variable is event-invariant** across the seven;
critical slowing down was *absent* precisely where the shock was most
abrupt; and the branching ratio ran deeply subcritical (~0.2).

**Stated at the scope the evidence actually covers**, rather than as a
general verdict: *the OHLCV-computable critical-slowing-down early-warning
variables tested did not give an event-invariant signal across those seven
cascades.* That is narrower than "early warning does not work" — it says
nothing about order-book, funding, or open-interest predictors, none of
which were tested, and nothing about an eighth cascade. It is enough to
stop this project pursuing the tested family, which is what CLAUDE.md
already records.

But the mechanism statement survives and is unusually clean: *"the
liquidation engine emits forced price-insensitive sell orders"* which
*"execute against deeply discounted resting limit orders, creating air
pockets."* Cheng et al. (2021), on BitMEX with a generalised extreme value
model, quantify the population: **3.51% of long and 1.89% of short
positions face forced liquidation on a given day, at an average leverage
among liquidated positions of 60×.** That is a standing, measurable
population of counterparties who cannot choose their price.

**#13 — the one tier-D entry with a real test behind it.** An independent
event study codified ICT's four core entries mechanically and ran **648
backtests** on SPY / QQQ / DIA / IWM against three textbook entries, a coin
flip, and buy-and-hold. **None showed a statistically significant forward-
return edge**; the best (order blocks on SPY) reached **t = +1.22**; the
largest effect was +0.121% over 5 days across 736 events; and **0 of 648
beat simply holding the index.** The author's own stated limitation is
decisive and must be quoted with the result: this tests **daily bars**,
while ICT is taught intraday. So the honest verdict is *falsified at daily
scale, untested rigorously at the scale it is actually taught*.

A separate vendor-side audit reports mechanical SMC win rates of 38–48%
with double-digit losing streaks, FVG mitigation at 64.8%, sweep follow-
through at 58.2%, and **standalone order blocks at 43.1%** — i.e. an order
block alone does not hold price. Their own conclusion is that an order
block only acquires predictive value **combined with displacement leaving
a substantial gap**, which is a conjunction claim, not a level claim.

**The methodological obstacle is worth stating on its own**, because it
applies to everything in tier C: most published descriptions are ambiguous
enough to support several incompatible rule sets, and those rule sets do
not produce the same results. **"Does ICT work" is not a testable
question. "Does *this* fully-specified rule work" is.** Which is precisely
what rd-b's stage-1 catalogue is for.

## 6. What Korea gives that Bitcoin could not — and there is a clock on it

The operator asked, when the universe moved to KRX: *"비트코인때와 다르게
더 많은 정보들이 있는 거 아녀?"* **Yes. One thing specifically, and it is
larger than it sounds.**

> **The Korea Exchange mandatorily discloses, daily and per stock, the net
> buying of 개인 (individuals), 기관 (domestic institutions) and 외국인
> (foreigners).**

There is no crypto equivalent. There is no *US equity* equivalent either —
the entire US literature on retail flow exists because researchers had to
**infer** it: Boehmer, Jones, Zhang & Zhang's celebrated 2021 *Journal of
Finance* method identifies retail trades from sub-penny price improvements
because the data is not published. Kelley & Tetlock (2013, *JF*) needed
proprietary data from a single wholesaler. **In Korea it is a legal
disclosure.** An arXiv microstructure paper (2512.18648) makes exactly this
point and uses it: 2020–2024 daily institutional order flow, 2,570 stocks
× 1,231 trading days = **2.11 million stock-day observations**.

**What the peer-reviewed work says the data contains:**

| Source | Finding |
|---|---|
| Kelley & Tetlock (2013, *JF*) | Retail **market** orders predict monthly returns *and* earnings surprises — they carry cash-flow information. Retail **limit** orders follow negative returns and profit from reversal — liquidity provision. **Two different populations under one label.** |
| Boehmer, Jones, Zhang & Zhang (2021, *JF*) | Net retail buying predicts ~**+10 bps over the following week**; less than half is order-flow persistence, and the rest is not explained by contrarian trading or news sentiment. *(A recent arXiv replication on a later period reaches different conclusions — carry that caveat.)* |
| 김영희 · 엄찬영 (한양대) | **In KOSPI 200**, retail net buying has **positive** short-horizon predictive power, read as compensation for liquidity provision. **In non-KOSPI 200 (small/mid caps)**, institutional net buying reflects **informed** trading, and retail counterparties lose over long horizons — with partial reversal in the first 20 days. |
| 박경인 · 배기홍 (고려대) | Individuals and foreigners are **negatively correlated** in trading behaviour. Counter-intuitively, individuals paid the **lowest** implicit transaction costs in large- and mid-cap portfolios. |

**This inverts the folk story in a way that matters for design.** 개미털기
as usually told says retail is the systematic victim. The Korean evidence
says: **in large caps, retail short-horizon flow is positively predictive
and is being *paid* for supplying liquidity; the losses are concentrated in
small caps, taken by institutions who are actually informed.** The KR-10
universe is, by construction, the ten largest-turnover names — the
**large-cap regime**, the side where the evidence is favourable.

**The clock — measured 2026-09-14, not quoted from documentation.**
`/uapi/domestic-stock/v1/quotations/inquire-investor` (`tr_id`
`FHKST01010900`) returned, for both 삼성전자 and SK하이닉스, **exactly 30
rows** covering 2026-08-03 → 2026-09-14, and **the endpoint accepts no
date parameter at all.** So the 30 is a *horizon*, not a page cap: unlike
the daily-chart endpoints, which cap at 100/50 but page backwards through
years, there is no request that reaches day 31.

> **This data cannot be backfilled. A collector started today has a year
> of history in a year; one started after the research finishes has
> nothing.**

It is `binance_positioning.py`'s situation exactly, and Task B's argument
applies verbatim and with more force, because here the data is
peer-reviewed to carry signal.

**The response is also richer than net quantity**, which the literature
makes directly relevant. Each row carries, per investor type, the net
quantity *and* the net value *and* **gross buy and gross sell volume
separately** (`*_shnu_vol` / `*_seln_vol`). Kelley & Tetlock's finding —
that retail market orders and retail limit orders are two different
populations behaving oppositely — cannot even be approached without that
decomposition. `foreign-institution-total` (`FHPTJ04400000`) returns the
cross-sectional counterpart, with a finer institutional breakdown
(`fund_`, `insu_`, `bank_`, `ivtr_`, …).

**Starting that collector is the most time-critical item in this
document**, and it is now a measured deadline rather than a suspicion.

## 7. What is actually held, verified against the store

Read from `python/data/var/klines.sqlite3` directly rather than from
CLAUDE.md's narrative:

| Series | Interval | Bars | Span |
|---|---|---|---|
| `BINANCE-FUTURES:BTCUSDT` | 1m | 3,661,780 | 2019-09-08 … 2026-08-25 |
| `BTC-USDT` | 1m / 1h / 15m / 1d | 910,040 / 19,678 / 24,191 / 1,940 | various |
| `ETH-USDT` | 1d | 1,930 | 2021-05-14 … 2026-08-28 |
| **`KRX:*` (10 names) + `KRX-INDEX:0001`** | **1d only** | **1,882 each** | **2019-01-02 … 2026-09-01** |

**The gap this exposes — and the probe's correction to it.**

The store holds **no intraday KRX data at all**, so every intraday
mechanism in §5 (#1, #2, #5, #10, #11, and the intraday half of #6 and
#15) is untestable on Korean names *today*. The first draft of this
document stopped there and concluded they were untestable, period. **The
probe shows that was too pessimistic.**

`inquire-time-dailychartprice` (`FHKST03010230`) serves **past sessions'
minute bars, 120 rows per call, back about 250 trading days** — the
boundary pinned exactly: 2025-09-03 returns 120 bars, 2025-09-02 returns
zero. That is a **rolling trading-day count, one trading year**, so the far
edge moves forward every session.

| | |
|---|---|
| Obtainable now, per symbol | ~250 sessions × ~380 bars ≈ **95,000 bars** |
| Cost, KR-10 | ~4 calls/symbol/session ≈ **10,000 calls** |
| Detection floor at one year | ~1.645 — **poor** for a Sharpe-scored always-on strategy |
| Events for rd-b's instrument | a setup firing 20×/symbol-year → **n ≈ 200** across ten names → t ≈ 2.1 on a 0.3% effect |

**That last two rows are the whole point.** One year is thin for the
instrument this project spent 1,883 runs on and adequate for the one rd-b
proposes. The window fits the question *because the question changed*.

So, three things follow, and none is "give up":

1. **Backfill the ~250 sessions, and soon.** Not urgent the way §6 is —
   but the edge rolls off daily, so waiting costs history permanently.
2. **Mechanism research can still run on BTC 1m and transfer.** 6.96 years,
   3.66M bars, taker buy/sell volume on every row — the best instrument
   this project has for *establishing whether a situation shifts the
   outcome distribution at all*. Transfer to KRX then becomes an explicit,
   testable assumption rather than a hidden one.
3. **Daily-horizon Korean mechanisms are testable today.** #3, #4, #7, #15,
   #16 and the daily form of #6 all work on the 1,882 daily bars, and #16
   works *only* in Korea.

## 8. What this suggests doing, in order

Not a plan — rd-b's four stages are the plan, and stage 1 has not started.
This is what §1–7 imply about how to populate them.

| | Action | Why now | Cost | State |
|---|---|---|---|---|
| **0** | **Probe KIS investor-flow retention and intraday depth**, on GCP, read-only | Both answers determine what must start *today*; neither is recoverable later | no `N`, no holdout | **done** — [result](rd-c-kis-flow-probe-result.md) |
| **1** | **Start an investor-flow collector** | **~30 trading days of runway, measured.** Strictly irreversible if skipped, and §6 is the one piece of data Korea has and crypto does not | small, ongoing | **urgent** |
| **2** | **Backfill ~250 sessions of KR-10 intraday, then keep it current** | ~10,000 calls. The far edge rolls off one session per session | moderate, one-off + ongoing | high |
| **3** | **Re-scope the catalogue with the operator (§10)** | rd-b committed the mechanism column comes from them, and §4's three-type test gives them something concrete to push against | conversation | blocked on operator |
| **4** | **Stage 1 on BTC 1m — count events for the surviving catalogue** | Feasibility only. Establishes which situations fire ≥20×/year (rd-b §1: that is the measurability threshold) | none — counts, no forward returns | after 3 |
| **5** | **Commit the catalogue and the FDR family before any stage-2 measurement** | rd-b §7 Q3: a catalogue sweep *is* selection, so it is pre-registered the way MS-E's thresholds were | pre-registration | after 4 |

Items 1 and 2 do not depend on item 3 and should not wait for it — they
are data capture, and the catalogue's final contents change nothing about
whether the data exists to test it.

**Deliberately not on this list**: anything that searches for a better
entry formula. §2 is the reason.

## 9. What this document does not claim

- **No strategy has been found, and nothing here is evidence of one.**
  Every number in §1–6 comes from someone else's data.
- **No mechanism here has been tested by this project.** Tier A means
  *someone* tested it, usually on a different market in a different decade.
- **Tier C is not endorsement.** Wyckoff, ICT, Market Profile and Al Brooks
  appear because they are widely used and mechanically specifiable, not
  because they are supported. Two of them have failed the only real tests
  found.
- **The Korean investor-flow finding is about a data source, not a
  strategy.** That the data is uniquely available and peer-reviewed to
  carry signal does not mean a tradeable rule exists in it.
- **§2's convergence is suggestive, not proof.** Four sources agreeing that
  selection matters more than entry is a strong prior; it is not a measured
  result on this project's data, and (b) has a disclosed conflict of
  interest while (d) is this project's own underpowered work.
- **The KR-10 universe holds no intraday data today**, though the probe shows ~250 sessions are obtainable (§7). Nothing intraday has been collected, let alone tested.

## 10. Open questions — the operator's, and these are the ones that matter

rd-b §7 asked four; two are answered, and these replace them.

1. **Which of the sixteen do you actually watch, and — the part only you
   can answer — is your read of "who is forced" the same as §5's?** Where
   it differs, yours is the one to test. §4's three-type test (forced /
   informed / structural) is offered as a way to make the answer concrete:
   for each situation you name, **which of the three is it, and how would
   you know at the moment of entry?**
2. **Does anything belong here that no source mentions?** The literature is
   Anglophone and mostly US/FX. Korean-market situations — 상한가 따라잡기,
   동시호가, 공매도 금지 구간, 프로그램 매매, 수급 주체 전환 — are
   under-represented above **because the searches found little, not because
   they are absent.** #15 and #16 are the only two that reached the table.
3. **Given §1 — 71% of the average day trader's loss is cost, and the
   winners are distinguished by *concentration*, not by a better entry —
   should this programme narrow to one or two names rather than the KR-10
   ten?** This runs against MS-D/E's diversification logic, which was built
   for a portfolio of always-on daily signals. **Both can be right for
   different strategy shapes**, and choosing is the operator's call, not a
   derivation.
4. **~~Intraday Korean data: probe first, or accept daily-only?~~
   Answered — ~250 sessions are obtainable.** The live question is now
   narrower and is a real trade-off: **is one trading year of KR-10
   intraday worth ~10,000 API calls and a collector to maintain, given the
   same mechanisms can be measured on 6.96 years of BTC 1m first?** The
   argument for doing it anyway is that the far edge rolls off daily, so
   the option expires; the argument against is that it is the wrong order
   — measure on the deep instrument, then port to the shallow one. **My
   recommendation: backfill it now and measure on BTC first.** The
   backfill is cheap and irreversible-if-skipped; the measurement order is
   a separate decision the data does not force.
5. Still open from rd-b: **whether the S16 candidate (t = 2.388) proceeds
   to a confirmation window in parallel.** Unchanged, and a separate
   CLAUDE.md checkpoint-#2 decision.

---

## Sources

**Tier A — peer-reviewed**

- Osler, C. — *Currency Orders and Exchange-Rate Dynamics*, [NY Fed Staff Report 150 (2003)](https://www.newyorkfed.org/research/staff_reports/sr150.html); *Stop-loss orders and price cascades*, **JIMF** (2005)
- Barber, Lee, Liu & Odean — [*The Cross-Section of Speculator Skill: Evidence from Day Trading*](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf), **J. Financial Markets**
- Barber, Lee, Liu & Odean — [*Do Day Traders Rationally Learn About Their Ability?*](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trading%20and%20Learning%20110217.pdf)
- Chague, De-Losso & Giovannetti (2020) — Brazilian day-trader study
- Kelley & Tetlock — [*How Wise Are Crowds? Insights from Retail Orders and Stock Returns*](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12028), **Journal of Finance** 68(3), 2013
- Boehmer, Jones, Zhang & Zhang — [*Tracking Retail Investor Activity*](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13033), **Journal of Finance** 76(5), 2021; [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2822105); [replication caveat (arXiv 2403.17095)](https://arxiv.org/html/2403.17095v1)
- 김영희 · 엄찬영 (한양대) — [한국 주식시장 개인투자자의 장단기 투자 성과 분석](https://www.kdajdqs.org/bbs/presentation/585/download/1172)
- 박경인 · 배기홍 (고려대) — [한국 증권시장의 투자자 유형에 따른 성과분석](https://www.e-kjfs.org/upload/pdf/kjfs-2006-35-3-41.pdf)
- [*Optimal Signal Extraction from Order Flow* (arXiv 2512.18648)](https://arxiv.org/pdf/2512.18648) — the KRX investor-type disclosure, 2.11M stock-day observations
- Cheng et al. (2021) — BitMEX forced-liquidation GEV study
- Holmberg, Lönnbark & Lundström (2013), **Finance Research Letters** — opening-range breakout on crude oil futures
- [*Do Overnight Returns Truly Measure Firm-Specific Investor Sentiment in the KOSPI Market?*](https://mdpi.com/2071-1050/11/13/3718/htm)
- [Buy-side divergence of opinion and stock returns: evidence from call auctions](https://www.sciencedirect.com/science/article/abs/pii/S1544612326004563) — opening-auction overpricing and same-day reversal
- Locke & Mann — *Professional trader discipline and trade disposition* — floor futures traders hold losses longer than gains **with no measured cost**; discipline predicts success

**Tier B — verified record**

- Linda Bradford Raschke — registered CTA/CPO; *New Market Wizards*; ranked 17 of 4,500 on five-year performance. Four mechanizable principles; **she has stated many of these setups now have limited edge**
- Zarattini, Barbon & Aziz (2024) — [*Can Day Trading Really Be Profitable?* / "Stocks in Play"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622) — COI: Peak Capital Trading

**Tier C / D — practitioner and vendor**

- Mark Fisher, *The Logical Trader* (2002) — ACD. Anecdotal; **Fisher himself reportedly said strict ABCD following makes you a net loser**
- Al Brooks — [second entry](https://trasignal.com/blog/learn/al-brooks-2nd-entry-setup/), [core principles](https://trasignal.com/blog/forex/price-action-trends-by-al-brooks/), [trading ranges glossary](https://dl.kohanfx.com/pdf/Al-Brooks-Trading-Price-Action-Ranges-\(KohanFx.com\).pdf) — claims **>80% of trading-range breakouts fail**; unverified
- [StatOasis — *I Backtested ICT / Smart Money Concepts: What Survives*](https://statoasis.com/overfit/research/ict-backtest-what-survives) — 648 backtests, best t = +1.22, 0/648 beat buy-and-hold (**daily bars only**)
- [FXNX — SMC component backtest](https://fxnx.com/en/blog/smart-money-concepts-work-backtest-evidence); [Build Alpha — how to specify ICT rules testably](https://www.buildalpha.com/backtest-ict-and-smc/); [LuxAlgo — SMC reference](https://www.luxalgo.com/library/concept/smart-money-concepts/)
- Market Profile / auction theory — [value area guide](https://www.quantvps.com/blog/value-area-trading-strategy-guide), [TPO reference](https://crosstrade.io/learn/technical-indicators/market-profile-tpo), [volume profile for ES/NQ](https://www.futureshive.com/blog/volume-profile-trading-strategy-2025). 80% rule independently tested at **67%**; all figures vendor-sourced
- Order flow / CVD / absorption — [Bookmap on CVD](https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy), [footprint charts](https://www.litefinance.org/blog/for-beginners/trading-strategies/order-flow-trading-with-footprint-charts/). **No peer-reviewed backing found for CVD divergence**; the trade-sign classification literature underneath it (Lee-Ready, bulk volume classification) is tier A but is not the same claim

**API reference**

- [KIS Open API portal](https://apiportal.koreainvestment.com/apiservice-category) — `inquire-investor` (`FHKST01010900`), `foreign-institution-total` (`FHPTJ04400000`), `inquire-time-itemchartprice` (`FHKST03010200`), `inquire-time-dailychartprice` (`FHKST03010230`); [official samples](https://github.com/koreainvestment/open-trading-api). **The portal documents the endpoints but publishes no row cap and no retention depth for any of them** — every such figure in §6 and §7 is this project's own measurement, from [`rd-c-kis-flow-probe-result.md`](rd-c-kis-flow-probe-result.md), not a quoted spec.
