# Research Direction Task B — `Discuss`: situations, not formulas

**Status**: Discuss. Nothing built, nothing registered, no strategy run.
The counts in §2 and §4 come from the local store and are **event counts
and volatility statistics only** — no forward return of any candidate was
measured, so nothing here spends the window for selection.

Supersedes §5–6 of
[`rd-a-why-it-failed-and-what-to-measure.md`](rd-a-why-it-failed-and-what-to-measure.md),
whose diagnosis (§1–4) stands unchanged.

---

## 1. The correction rd-a needed

rd-a diagnosed the instrument correctly and then prescribed the wrong
remedy. The operator's objection, and it is right:

> *"연구가 불가능하다는 결론에 자꾸 도달하는 것 같다. 그러면 개인 투자자들이 세우는
> 전략은 다 쓸모없다, 수익 낸 사람은 구라다 라는 셈인데 말이 안 되잖아."*

The error is specific. rd-a said *the measurement was too noisy* and then
slid into *therefore you need enormous samples* — which is a property of
an **always-on** strategy scored by a Sharpe. It is not a property of what
a trader does.

| | Instrument | n | t for a small effect |
|---|---|---|---|
| What 1,883 runs were scored on | 30-day fold Sharpe | 30 | **0.28** |
| What a conditional setup needs | event study | **1,196** | **5.2** |

**A conditional setup is easier to measure than an always-on strategy,
not harder.** rd-a said the opposite by implication.

Power for an event study, `t = m/(s/√n)`, over a 7-year window:

| Setup fires | n over 7y | m=0.3%, s=2% | m=0.5%, s=2% |
|---|---|---|---|
| 5/year | 35 | 0.89 | 1.48 |
| **20/year** | 140 | **1.77** | **2.96** |
| 50/year | 350 | 2.81 | 4.68 |
| 100/year | 700 | 3.97 | 6.61 |

A setup firing twenty times a year is measurable. Nothing in the record
ever required otherwise; the project simply never measured a setup.

**And rd-a's own prescription was still the formula paradigm.** IC asks
*does this number predict returns*. It never asks *does this situation
have a skewed outcome*. Making the formula screen cheaper is not the
change that was needed.

### 1.1 What the record does and does not say about discretionary trading

It says nothing against it. What it establishes is that **this project's
method could not have detected an edge if one were there** — 1,883
always-on backtests on a unit with SE 2.9.

Two specific claims that are *not* supported by anything in the record,
named because they were drifting into the conclusions:

- *"Short-term trading cannot win."* S16's scalping candidate reached
  **t = +2.388, p = 0.0098**, PSR 0.9905, profit factor 6.44, and was not
  regime-concentrated. It failed on accumulated `N`, not on the edge.
- *"A 1% win-rate edge is not worth having."* At 2:1 payoff, 51% wins is
  +0.53 expectancy per unit risked; even at 1:1 it is +0.02, which over
  100 trades is +2R. The asymmetry does the work, exactly as S8 already
  wrote: *"40% at 3:1 is profitable, 60% at 0.5:1 loses."*

## 2. Translating what a trader actually says

> *"여기 지지선을 보고 이런 의미가 있겠구나, 이게 뚫리면 이런 시나리오, 아니 이건
> 개미털기다, 그러니까 이렇게 하자."*

Every clause is mechanically definable:

| Trader's word | Mechanical definition | Mechanism claim (who loses) |
|---|---|---|
| 지지선 | lowest low over the prior K bars | a price where resting orders and protective stops accumulate |
| 뚫림 | a bar's low trades below it | those stops fire — **forced** selling, not chosen selling |
| 개미털기 | price returns above the level shortly after | the forced flow was absorbed; the seller had no choice, the buyer did |
| 시나리오 | branch structure with a prepared response per branch | — |

Counted on 3,661,780 Binance-futures 1m bars, 6.96 years:

| Prior low | Penetration | Recovery window | Events | Recovered | Rate | Per year |
|---|---|---|---|---|---|---|
| 4h | 0.1% | 1h | 7,952 | 6,717 | 84.5% | 1,142 |
| 4h | 0.3% | 4h | 2,414 | 1,984 | 82.2% | 347 |
| 1d | 0.1% | 4h | 1,988 | 1,756 | 88.3% | 286 |
| **1d** | **0.3%** | **4h** | **1,196** | **968** | **80.9%** | **172** |

**The situation the operator described is abundant and countable.** That
alone is further than this project has ever got on a trader-shaped
hypothesis.

## 3. Three constraints, each measured rather than asserted

These are the reasons a naive version of this fails, and each has a
number attached.

### 3.1 The baseline must be matched, and the confound is large

Support penetrations do not happen at random moments. Measured on the
1,196-event set:

| | 60-minute realised volatility, median |
|---|---|
| All bars | 0.000558 |
| **At an event** | **0.001101 — 1.97×** |

**Events sit in the top 14% of the volatility distribution.** Any
comparison against an unconditional forward-return distribution reads a
volatility difference as an edge.

This is not a hypothetical risk. It is the exact error CLAUDE.md already
records as one of this project's largest: *"comparing costs to an
**unconditional** move distribution"* produced the false conclusion that
minutes-scale trading was arithmetically impossible.

**The instrument is a stratified placebo**, not an unconditional
baseline: draw matched non-event timestamps from the same volatility
stratum (and the same hour-of-day, since crypto has a real intraday
cycle), measure the identical forward statistic, and compare. A matched
null by construction is harder to get wrong than an explicit adjustment.

### 3.2 The branch is an outcome, not a condition — so 개미털기 is not tradeable as stated

"It recovered within four hours" is known four hours later. **개미털기 is
a label applied in hindsight.** A strategy conditioned on it is
look-ahead, and the 80.9% recovery rate is not an edge — it is largely
definitional, since price oscillates across a level it is sitting on.

The tradeable question is the one a trader is actually claiming to answer
in the moment:

> **What, observable at the instant of penetration, separates the ones
> that recover from the ones that do not?**

That is what *"아니 이건 개미털기다"* asserts — a real-time read, not a
retrospective label. And it is testable:

| Branch split | n | t for a 0.5% difference (s=2%) |
|---|---|---|
| 50 / 50 | 598 / 598 | **4.32** |
| 30 / 70 | 358 / 838 | **3.96** |

**Candidate separators, all observable at penetration time**: depth of
penetration; speed (bars from level to low); taker buy/sell imbalance on
the penetrating bar; volume relative to its own recent distribution;
whether the level had already been tested and held; distance to the next
higher-timeframe level; time of day.

This is where a genuinely different kind of IC returns — **conditional on
the situation**, over a search space of a dozen separators rather than an
unconditional sweep over everything.

### 3.3 The outcome must be measured from an executable entry

Task C reported +45 and the real figure from actual fills was −97 — a
sign flip, and 3× the effect being measured — because the book was built
from the prices the strategy *saw when deciding* rather than from fills.
Any event study here measures from the **next bar's executable price with
costs applied**, never from the event bar's close.

## 4. The programme

**Four stages, cheapest first, and no stage begins until the previous one
has answered.** Each has its own instrument, and this is what rd-a §5 got
structurally right and mis-populated.

| Stage | Question | Instrument | Cost |
|---|---|---|---|
| **1 Catalogue** | Which situations even occur often enough? | event counts | none |
| **2 Conditional outcome** | Does the situation shift the outcome distribution vs a matched placebo? | stratified placebo + FDR across the catalogue | FDR, not DSR |
| **3 Separator** | What observable at decision time splits the branches? | conditional comparison within the surviving situations | FDR within a small family |
| **4 Harvest** | What is the prepared response, and does it survive costs? | entry FIXED, management varied — Task D's comparison-run category | comparison-run rules |

Only a candidate that survives all four is a candidate at all, and only
then does a pre-registered confirmation on an unspent window make sense.

### 4.1 The starting catalogue — the researcher's list, to be corrected by the trader

Each entry needs a **mechanism**, per S8's standing rule that *"a
hypothesis must name a mechanism — who is on the other side and why they
lose."* A situation with no named loser is a formula wearing a costume.

| Situation | Mechanical definition | Who is forced |
|---|---|---|
| **Support penetration / recovery** | prior-K low broken by p%, recovery within N | protective stops below the level |
| **Failed breakout** | prior-K high broken, closes back below within N | breakout buyers with stops at the level |
| **Pullback in trend** | established trend, retracement to a prior level that holds | late entrants shaken out; trend followers add |
| **Absorption** | volume spike with no price progress | one side is filling size against the other |
| **Level retest** | a level that already held once is approached again | the first defence proved a real order cluster |
| **Range expansion** | volatility compression, then a decisive break | positions built inside the range are wrong-footed |

**This list is a starting point and is explicitly the wrong end to start
from.** It was assembled by reading rather than trading. The operator sees
these situations directly, and a catalogue built from what they actually
watch will be more precise than one assembled from convention. **The
mechanism column is the part that must come from them** — the definitions
can be mechanised afterwards.

## 5. Data: what is needed, what is missing, what to start collecting today

Stage 3's separators are almost all questions about **who is on the other
side**, and that is exactly where this project's data is thinnest.

| Needed for | Held? |
|---|---|
| Level definition, penetration, recovery | **yes** — 1m OHLCV, 6.96 y |
| Volume-based separators | **yes** — `taker_buy_base_volume` on every Binance 1m row |
| **Liquidations** — the literal forced flow the mechanism names | **no** |
| **Order book depth** — where the resting orders actually are | **no** |
| Open interest, long/short ratio | 37 days, growing |

**Liquidations and depth cannot be backfilled**, exactly as `positioning`
could not — which is why `binance_positioning.py` was put on cron in Task
B. The same argument applies now and is time-sensitive: **a collector
started today has a year of data in a year; one started after stages 1–3
finish has nothing.**

That said, **stages 1–3 do not need them.** Price plus taker flow over
6.96 years is enough to define the situations, measure their conditional
outcomes, and test volume- and price-based separators. The missing data
would make stage 3 sharper, not possible.

## 6. What this does not claim

- **No strategy has been found**, and nothing here is evidence of one.
- **The 80.9% recovery rate is not an edge.** §3.2 says why: it is a
  hindsight label on a definitionally frequent event, measured against no
  baseline.
- **The situation counts are not a result.** They establish that the
  instrument has power here, which is a statement about feasibility, not
  about profit.
- **rd-a's diagnosis is unaffected.** The 1,883 runs were still scored on
  a unit that could not measure anything; this document changes what to
  measure next, not what happened.

## 7. Open questions — the operator's

1. **The catalogue's contents.** §4.1 is a reader's list. Which
   situations do you actually watch, and — the part that matters — **who
   do you think is on the other side in each?**
2. **Whether to start the liquidation and depth collectors now.** Cheap,
   irreversible if skipped, and not needed until stage 3.
3. **Whether stage 2 needs a pre-registration.** rd-a §7's line still
   holds: measuring to diagnose is diagnosis, measuring twenty things to
   choose one is selection. A catalogue sweep is the second, so the
   catalogue and the FDR family should be committed before the first
   measurement — the way MS-E committed its thresholds.
4. **Whether the S16 candidate proceeds in parallel.** It already sits at
   `t = 2.388` and needs only a confirmation window, which is a separate
   human decision (CLAUDE.md checkpoint #2) and not in competition with
   this programme.
