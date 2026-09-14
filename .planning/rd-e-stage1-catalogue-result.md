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
| 4b | **abnormal activity, 60m volume top 1%** | 46,890 | 786 | **113** | **14%** | **feasible** |
| 2 | **resistance break, prior-1d high, 0.3%** | 2,389 | 1,170 | **168** | **20%** | **feasible** |
| 1 | **support penetration, prior-1d low, 0.3%** | 2,656 | 1,196 | **172** | **21%** | **feasible** |
| 7 | range expansion after compression | 5,325 | 2,902 | 417 | 50% | cost-hostile |
| 10 | absorption (volume top 10%, body bottom 10%) | 13,838 | 3,256 | 468 | 56% | cost-hostile |
| 4 | abnormal activity, 60m volume top 10% | 383,163 | 4,053 | 582 | 70% | cost-hostile |
| 1b | support penetration, prior-4h low, 0.1% | 20,774 | 5,259 | 755 | 91% | cost-hostile |
| 2b | resistance break, prior-4h high, 0.1% | 20,367 | 5,358 | 770 | 92% | cost-hostile |
| 3 | round-number touch (within 0.02% of a $1k level) | 70,316 | 6,207 | 892 | **107%** | **COST-INFEASIBLE** |
| — | taker imbalance extreme (top 5%) | 186,798 | 12,448 | 1,788 | **215%** | **COST-INFEASIBLE** (near-continuous) |
| 12b | fair value gap, bearish | 546,761 | 14,822 | 2,129 | **255%** | **COST-INFEASIBLE** (near-continuous) |
| 12 | **fair value gap, bullish** | 555,213 | 14,827 | **2,130** | **256%** | **COST-INFEASIBLE** (near-continuous) |

**These are the post-correction figures.** The first run of this table
computed every percentile threshold over the **whole** 6.96 years, so a
bar was classified as "top 1% of volume" using volume from after it —
lookahead, and exactly what CLAUDE.md's own clause forbids. Caught on
CodeRabbit review of PR #168 and replaced with a **trailing quantile**
recalibrated daily from the prior 30 days.

It moved real numbers. The five threshold-based rows all rose — abnormal
activity top 1% from 85 to 113/year, range expansion from 304 to 417,
taker imbalance from 1,516 to 1,788 — because a trailing threshold is
lower than a full-sample one during the market's growth phases and so
admits more events. **The seven purely price-based rows (1, 1b, 2, 2b, 3,
12, 12b) are unchanged**, which is the consistency check: the fix touched
exactly the rows that used a quantile and nothing else.

**No verdict changed category**, so §3's conclusion survives the
correction — but it was not safe to assume that in advance, and the
uncorrected figures are shown nowhere else.

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
- **"Cost-hostile" is not "dead."** Five situations sit between 50% and
  92% annual drag. They are excluded from the *first* stage-2 batch on
  cost grounds, not refuted, and a venue with a materially lower round
  trip moves them — the ceiling is a statement about κ, not about the
  situation. `feasibility()` takes `round_trip` as an argument for
  exactly that reason.
- **κ = 12bp is BTC-specific, and the Korean figure is now sourced.**
  This section originally repeated MS-F's "roughly 42bp", which was a
  **single-stock futures** figure and does not apply to the spot common
  stock decision B3 actually chose.
  [`rd-f`](rd-f-korean-cost-structure.md) measures it: **~32bp over the
  KR-10 window** and ~28bp today, of which **20bp is the 증권거래세 +
  농어촌특별세 charged on every sale** — raised to that level on
  2026-01-01 — plus a spread of 9.85bp full-window median (6.5bp today;
  the tick is a fixed 원 amount, so its cost in bp fell as Korean prices
  rose). Re-scored: **zero of the twelve are feasible at 32bp, and exactly
  one at 28bp.**

  **Both are one-tick FLOOR estimates** — a GCD of daily closes measures
  the minimum price increment, not the quoted bid-ask, so the true round
  trip is higher and the single 28bp survivor may not survive a measured
  spread. rd-f §1.2 says so explicitly.
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
3. **~~Source the KRX round trip~~ — done**, in
   [`rd-f`](rd-f-korean-cost-structure.md): ~27bp, tax-dominated, leaving
   exactly one survivor. What remains open there is the commission tier
   and the spot-versus-futures instrument choice, the latter being an
   operator decision.
