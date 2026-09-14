# Research Direction Task E — stage 1 result: the ceiling, not the floor

**Run 2026-09-14 on the designated discovery window** (Binance futures
1m, 3,661,780 bars, 6.96 years), under CLAUDE.md's new Discovery /
Confirmation split. Module: `python/research/situation_catalogue.py`.

**Counts only. No forward return of any situation was computed**, so this
is feasibility and not evidence. It spends no `N` and no holdout —
[`rd-b`](rd-b-situations-not-formulas.md) §4 stage 1: *"Which situations
even occur often enough? — instrument: event counts — cost: none."*

---

## 1. The answer to the question rd-b asked, and why it does not matter

rd-b's stage 1 asks whether each catalogue situation fires often enough to
be measurable, and set the bar at **~20 events/year** (t = 1.77 over seven
years for a 0.3% effect against 2% dispersion).

**Every situation clears it, most by an order of magnitude.** The least
frequent fires 85 times a year; the most frequent 2,130.

So **stage 1 as specified does not discriminate at all**, and that is the
finding. The binding constraint was never the floor.

## 2. The ceiling rd-b did not have

A situation traded `F` times a year at a round-trip cost `κ` burns
**`F × κ` a year in costs before any edge exists.** When that figure is an
implausible fraction of annual return, **no stage-2 measurement can rescue
it** — the per-event effect would have to be smaller than the spread it
must cross twice.

At `κ = 12bp` (`scalp-s9`'s measured BTC perpetual round trip, where the
taker fee dominates the spread ~330×):

| # | Situation | raw | **episodes** | /yr | **cost/yr** | verdict |
|---|---|---|---|---|---|---|
| 4b | **abnormal activity, 60m volume top 1%** | 36,618 | 594 | **85** | **10%** | **feasible** |
| 2 | **resistance break, prior-1d high, 0.3%** | 2,389 | 1,170 | **168** | **20%** | **feasible** |
| 1 | **support penetration, prior-1d low, 0.3%** | 2,656 | 1,196 | **172** | **21%** | **feasible** |
| 7 | range expansion after compression | 3,938 | 2,118 | 304 | 37% | cost-hostile |
| 10 | absorption (volume top 10%, body bottom 10%) | 12,849 | 2,883 | 414 | 50% | cost-hostile |
| 4 | abnormal activity, 60m volume top 10% | 366,172 | 3,573 | 513 | 62% | cost-hostile |
| 1b | support penetration, prior-4h low, 0.1% | 20,774 | 5,259 | 755 | 91% | cost-hostile |
| 2b | resistance break, prior-4h high, 0.1% | 20,367 | 5,358 | 770 | 92% | cost-hostile |
| 3 | round-number touch (within 0.02% of a $1k level) | 70,316 | 6,207 | 892 | **107%** | **COST-INFEASIBLE** |
| — | taker imbalance extreme (top 5%) | 183,063 | 10,552 | 1,516 | **182%** | **COST-INFEASIBLE** (near-continuous) |
| 12b | fair value gap, bearish | 546,761 | 14,822 | 2,129 | **255%** | **COST-INFEASIBLE** (near-continuous) |
| 12 | **fair value gap, bullish** | 555,213 | 14,827 | **2,130** | **256%** | **COST-INFEASIBLE** (near-continuous) |

Episodes are collapsed with a **240-bar (4h) cooldown**, the same rule
rd-b §2 now states explicitly. The ceiling that rule implies is
**15,257 episodes (2,192/year)**; anything near it is not a condition at
all — it fires essentially always. **Both fair-value-gap variants sit at
97% of that ceiling.**

## 3. What this establishes

**Three situations survive, and they are the three rarest.** That is the
whole result, and it is worth stating plainly because it inverts the
intuition that a frequently-firing signal is a better one:

> **Selectivity is not merely statistically convenient. It is the only
> regime in which a per-event effect can be large enough to clear costs.**

This reaches [`rd-c`](rd-c-mechanism-catalogue.md) §2's conclusion —
*the filter is the strategy, not the entry* — **from arithmetic rather
than from the literature.** Two independent routes to the same place:
Barber/Zarattini/S11/S16 on one side, `F × κ` on the other.

**It also makes the ICT critique quantitative.** rd-c recorded that an
independent event study ran 648 backtests of ICT's four entries and none
showed a significant edge, with 0 of 648 beating buy-and-hold. This says
*why* for at least one of them: **a fair value gap fires 2,130 times a
year on a single instrument.** It is not that the pattern carries no
information — it is that it is not selective enough to pay for itself at
any realistic cost, and no amount of refining the entry changes a 256%
annual cost drag.

## 4. What it does not establish

- **Nothing about whether the three survivors have an edge.** No forward
  return was computed. "Feasible" means *"costs do not rule it out in
  advance"*, which is the weakest possible positive statement.
- **"Cost-hostile" is not "dead."** Four situations sit between 37% and
  92% annual drag. They are excluded from the *first* stage-2 batch on
  cost grounds, not refuted, and a venue with a materially lower round
  trip moves them — the ceiling is a statement about κ, not about the
  situation. `feasibility()` takes `round_trip` as an argument for
  exactly that reason.
- **κ = 12bp is BTC-specific.** MS-F recorded the KRX round trip at
  roughly 42bp, which is 3.5× worse — so on Korean equities the ceiling
  bites over three times harder and only the very rarest situations
  survive. That number needs its own sourcing before any Korean stage 2
  (MS-F §2 says so, and it is still not done).
- **The situation definitions are one parameterisation each.** Penetration
  depth, lookback and percentile thresholds were chosen to match rd-b's
  existing figures, not swept. CLAUDE.md's standing rule — *never conclude
  about a domain from one parameter setting* — applies: a nearby
  parameterisation moves the frequency, and frequency is what the ceiling
  scores.

## 5. What follows

1. **Stage 2 runs on the three feasible situations only**, and needs a
   pre-registration first — rd-b §7 Q3 committed that a catalogue sweep is
   selection, so the catalogue and the FDR family are fixed before the
   first forward return is measured.
2. **The cost ceiling belongs in stage 1 permanently**, not just in this
   run. It is implemented as `situation_catalogue.feasibility()` and
   `rd-b` §4's stage-1 row should be read as having both a floor and a
   ceiling from here on.
3. **Source the KRX round trip** before any Korean stage 2. At 42bp the
   table above would leave roughly one survivor.
