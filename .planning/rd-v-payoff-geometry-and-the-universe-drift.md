# Research Direction Task V — the universe drifts at 54%/yr, and what an asymmetric payoff can and cannot fix

**Measured 2026-09-19.** Module: `research/krx_payoff_geometry.py`.
Reproducible with

```
python -m research.krx_payoff_geometry
```

**Discovery mode.** The promotion `N` is not incremented and nothing here
may be promoted, quoted as evidence of an edge, or reported as a pass.

Opened by an operator question about the mathematician-trader framing —
*positive expectancy through an asymmetric payoff*, and specifically a
rule they had seen described: **hold ten names, sell at +15%, sell at
−5%.** Measuring it produced a result that looked like a success and was
not one, and finding out why produced the more important number.

---

## 1. The headline, and it bounds three earlier documents

> **The `rd-r` ten returned +694% equal-weight over the panel — +54%/yr —
> against KOSPI's +131% (+19%/yr). A selection premium of 3.4× the index,
> or +35%/yr.**
>
> **Any long-only rule on this basket profits, and that is not evidence
> about the rule.**

| code | | first open | last close | total | per yr |
|---|---|---|---|---|---|
| 012450 | 한화에어로스페이스 | 43,140 | 1,101,000 | **+2452%** | +96% |
| 000660 | SK하이닉스 | 118,000 | 1,766,000 | **+1397%** | +76% |
| 042700 | 한미반도체 | 17,175 | 228,000 | +1228% | +71% |
| 402340 | SK스퀘어 | 77,900 | 1,018,000 | +1207% | +71% |
| 034020 | 두산에너빌리티 | 19,352 | 86,300 | +346% | +37% |
| 005930 | 삼성전자 | 73,200 | 255,000 | +248% | +30% |
| 005380 | 현대차 | 202,000 | 364,000 | +80% | +13% |
| 000270 | 기아 | 80,500 | 121,400 | +51% | +9% |
| 006400 | 삼성SDI | 697,071 | 544,000 | −22% | −5% |
| 035420 | NAVER | 391,000 | 200,000 | −49% | −13% |

**Even the median name is +297%.** This is not a data error — these are
the Korean defence and AI-memory booms, and they are real.

**The mechanism is circular and that is the whole point.** `rd-r` selected
these on **2026Q1 single-stock-futures liquidity**, and a name becomes
liquid enough to carry a listed future *because* it has gone up 10–25×.
The selection criterion and the return are the same fact.

**What this bounds:**

- [`rd-t`](rd-t-korean-signal-ic.md) — cross-sectional, so less exposed,
  but its universe is this one.
- [`rd-u`](rd-u-do-conjunctions-beat-their-parts.md) — its h=5 block
  already carried a `*** CONFOUNDED ***` banner saying the universe
  "correlates with having risen." **That banner was right and understated
  by an order of magnitude**; it did not name +54%/yr.
- [`tm-e`](tm-e-scenario-playbook-result.md) — every long-side figure sits
  on this drift.

The h=1 intraday framing those documents prefer is the correct defence —
its unconditional baseline is ~0 — and it is a defence against the
*level*, not against the dispersion.

## 2. How it was found: a rule that passed its own test

The operator's rule, implemented exactly — hold until +15% or −5%, one
position per name at a time, 13bp round trip:

| | trades | win | E[trade] net | median hold | dates | p |
|---|---|---|---|---|---|---|
| **LONG** | 1,291 | **30.0%** | **+0.8654%** | 5 sessions | 668 | 0.0003 |
| SHORT | 1,157 | 21.5% | −0.8258% | 5 sessions | 665 | 0.0000 |

**The arithmetic works exactly as the framing promises.** Breakeven at 3:1
is 25.0%; the long side achieves 30.0%. Five points of margin on 1,291
non-overlapping trades, p = 0.0003 clustered by entry date.

**And the short leg is what says it is the basket.** A mirror-image rule
loses almost precisely what the long side makes. Sum of the two
directions **+0.0396%**, against the **−0.2600%** a driftless process must
give — that being just the two round trips.

So the decomposition is roughly **85% drift, 15% genuine asymmetry.**

**Nothing but the short control could have caught this.** The long side's
win rate, expectancy, trade count, holding period and p-value are all
individually reasonable. This is the fifth entry in this project's
"the measurement passed every check and answered the wrong question"
list, and it is the first where the control was run *before* the write-up
rather than after.

## 3. The asymmetry that is genuinely there, isolated

Sweeping the payoff ratio with a 1 ATR stop, and reading the **sum** of
long and short, which removes drift by construction:

| payoff | LONG | SHORT | sum | vs the driftless −2×cost |
|---|---|---|---|---|
| 1:1 | −0.006R | −0.073R | **−0.079R** | **exactly drift** |
| 2:1 | +0.070R | −0.071R | −0.001R | +0.071 |
| 3:1 | +0.125R | −0.093R | +0.033R | +0.105 |
| 5:1 | +0.192R | −0.104R | **+0.088R** | **+0.160** |

**At 1:1 the sum is exactly minus two round trips** — the signature of a
driftless coin, and the anchor that makes the rest readable. **The sum
then rises monotonically with the payoff ratio**, and that rise is not
drift: it appears in *both* directions.

**So "letting winners run" pays here, in both directions, and it is
small** — about **+0.15% per trade**, roughly 1.2× the round trip.
Positive, real, and thin.

> **A caveat on these four rows specifically.** This sweep entered every
> name every day with a 10-session hold, so positions from different
> entry dates overlap heavily and **its p-values are not admissible** —
> S13's error, reproduced here and caught before publication. The
> *directions* are reported; no significance is claimed for them. §2's
> figures do not share the defect: one position per name at a time,
> verified with `check_disjoint_intervals`.

## 4. Why a fixed percentage is the wrong barrier

**"−5%" is a round distance, and S8 already forbids that**: *"a stop
belongs at a level that invalidates the thesis, not at a round distance
or a convenient ATR multiple."*

The panel's median ATR(14) is **3.60% of price**, so −5% is ~1.4 ATR for a
median name — but roughly **0.7 ATR on 한화에어로스페이스 and ~3 ATR on
NAVER.** The same written rule is a completely different trade per name:
on the volatile ones it is a noise stop, on the quiet ones it is a
position that rarely resolves.

A barrier pair must be scaled to the name — by ATR, or better by an
actual structural level — or the rule's behaviour is decided by whichever
names happen to be in the basket.

## 5. Where an asymmetric payoff can exist at all: the horizon decides

At 3:1, expectancy is `p·3 − (1−p)·1 − cost_R`, so breakeven is
`p = (1 + cost_R)/4`. **With no costs that is 25% at every horizon — the
payoff ratio is scale-free.** A fixed round trip is what breaks the scale,
because the move available shrinks with the horizon while the cost does
not.

Measured on the real KRX 1-minute tape (955,273 bars, moves confined to
one contiguous block so none spans the overnight break or the closing
auction), with the target set at about one typical move and the stop at a
third of it:

| horizon | median move | stop (1R) | cost in R | breakeven @ 3:1 |
|---|---|---|---|---|
| 5 min | 17.7 bp | 5.9 bp | **2.21R** | **80.2%** |
| 15 min | 26.7 bp | 8.9 bp | 1.46R | **61.6%** |
| 30 min | 37.5 bp | 12.5 bp | 1.04R | 51.0% |
| 60 min | 50.8 bp | 16.9 bp | 0.77R | 44.2% |
| 120 min | 71.6 bp | 23.9 bp | 0.54R | 38.6% |
| 240 min | 104.9 bp | 35.0 bp | 0.37R | 34.3% |
| **1 day** (1 ATR) | 1080 bp | 360 bp | **0.04R** | **25.9%** |

**This is the answer to whether the frame transfers to 단타, and it is
quantitative rather than a preference.** At five minutes the round trip
alone costs **2.21R** — more than twice the whole stop — and a 3:1 payoff
would need an **80% win rate**. The entire appeal of an asymmetric payoff
is that it works at a *low* win rate, and a short horizon attacks exactly
that property.

**It is a gradient, not a cliff**, and the intraday rows are not all
equal: 240 minutes needs 34.3% against the daily 25.9%, which is a real
handicap and not an impossibility. This also agrees with `scalp-s8`'s own
retraction — *"minutes-scale is arithmetically impossible"* was false, and
what is true is that the required win rate climbs steeply as the horizon
falls.

**What this does not say**: that a 34.3% win rate is unattainable at 240
minutes, or that 25.9% is attainable daily. It sets the bar; it says
nothing about whether any signal clears it.

## 6. A fifth omission in Task E, found by the same question

`scenario_playbook.py` has **no reward-to-risk gate**. S8 requires the
risk decision be sequenced *"stop first, then reward-to-risk against a
structural target, then qualify, then size"*, and that **a trade whose
structure offers poor odds is declined, never resized into.** Task E
declined nothing on those grounds — it took every eligible gap.

So the project's own asymmetry rule was not applied in the one study
built to test trader-style structure. That is the fifth item on
[`tm-e`'s correction list](tm-e-scenario-playbook-result.md), and it is
the same shape as the other four: a rule that exists in CLAUDE.md and was
not carried into the implementation.

## 7. What follows

1. **The asymmetric-payoff frame is adopted and was already ours.** The
   next entry design carries a real R:R gate, and a setup that fails it
   is **declined**. That is a change to how entries are specified, not a
   new hypothesis.
2. **Barriers are scaled to the name**, by ATR or by structure — never a
   fixed percentage (§4).
3. **Daily or multi-hour, not minutes.** §5 gives the cost of each
   horizon in the only unit that matters, and the daily row is ~9× cheaper
   than the 240-minute one.
4. **Nothing further is measured on this universe without the short
   control beside it.** §2 is the demonstration: every single-sided
   statistic looked sound.
5. **The universe remains the binding constraint**, and §1 is the
   sharpest statement of it this project has. A strict filter needs
   enough names to still leave a sample, and a drift-free test needs
   names that were not chosen for having risen. Both point at the
   survivorship-safe full universe.
