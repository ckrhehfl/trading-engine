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
| **LONG** | 1,291 | **30.0%** | **+0.7155%** | 5 sessions | 668 | 0.0016 |
| SHORT | 1,157 | 21.5% | −1.0577% | 5 sessions | 665 | 0.0000 |

**The arithmetic works exactly as the framing promises.** Breakeven at 3:1
is 25.0%; the long side achieves 30.0%. Five points of margin on 1,291
non-overlapping trades, p = 0.0016 clustered by entry date.

**And the short leg is what says it is the basket.** A mirror-image rule
loses rather more than the long side makes: the sum of the two directions
is **−0.3422%**, against the **−0.2600%** a driftless process must give —
that being just the two round trips.

**So the rule has no asymmetry of its own at all.** The residual beyond
drift and cost is **−0.0822%**, i.e. on the wrong side of zero. An earlier
version of this document read **+0.0396%** here and called the split "85%
drift, 15% genuine asymmetry"; that 15% was an artifact of filling a
gapped stop *at the stop price*. Charging the real open (§2.1) removes it
entirely.

**And a second, independent control agrees.** Running the identical rule
on a panel with each name's own mean log drift divided out —
`drift_removed_panel`, a diagnostic that uses the future by construction
and is therefore disqualified as a feature, never a tradeable path:

| drift-removed | trades | win | E[trade] net |
|---|---|---|---|
| LONG | 1,302 | 26.0% | −0.0944% |
| SHORT | 1,141 | 26.2% | −0.1295% |

**Both legs land on the 25.0% breakeven and both lose after costs**, and
the sum (−0.2239%) sits essentially on the −0.2600% anchor. Take the drift
away and the rule has nothing left.

**That control is not redundant with the short leg, and this is the
methodological point worth carrying.** *The long/short sum cancels drift
only for a **linear** payoff.* A barrier rule is not linear: upward drift
makes a long's distant target reachable while pushing a short's distant
target out of reach, so drift survives the subtraction *disguised as
asymmetry*. §3 is where that would have bitten.

**Nothing but a control could have caught any of this.** The long side's
win rate, expectancy, trade count, holding period and p-value are all
individually reasonable. This is the fifth entry in this project's
"the measurement passed every check and answered the wrong question"
list, and it is the first where the control was run *before* the write-up
rather than after.

### 2.1 A gapped stop does not fill at the stop, and it moved every number here

The first version of this module recorded exactly `−5%` whenever a bar
traded through the stop, including when it **opened** below it. S8 §3.7
requires the real fill. Charging the open instead moved the long side from
+0.8654% to **+0.7155%** and the short from −0.8258% to **−1.0577%** — and
it is what flipped the residual above from positive to negative.

It shows up in the sweep too, in the one row built to be diagnostic: at
1:1 the sum sits **0.055R below** the driftless anchor rather than on it.
A symmetric 1:1 barrier system on a martingale must return exactly minus
two round trips; this one loses about **0.028R per leg more than that**,
which is the gap overshoot and nothing else. **A 1-ATR stop in this market
does not cost 1R — it costs about 1.03R.**

## 3. The asymmetry that is genuinely there, isolated

Sweeping the payoff ratio with a 1 ATR stop, on the real panel and on the
drift-removed one side by side. The anchor is the two round trips **the
sweep's own entries** paid (−0.076R), not a panel-median conversion:

| payoff | LONG | SHORT | sum | vs anchor | | drift-removed LONG | SHORT | vs anchor |
|---|---|---|---|---|---|---|---|---|
| 1:1 | −0.025R | −0.107R | −0.131R | **−0.055** | | −0.090R | −0.040R | **−0.055** |
| 2:1 | +0.044R | −0.113R | −0.069R | +0.007 | | −0.062R | −0.004R | +0.009 |
| 3:1 | +0.102R | −0.133R | −0.031R | +0.045 | | −0.033R | +0.003R | +0.045 |
| 5:1 | +0.168R | −0.146R | +0.023R | **+0.099** | | +0.017R | +0.000R | **+0.092** |

**The right-hand block is what makes the left-hand one readable, and
without it this section would have said something false.** Two things only
the control shows:

1. **The rise is real.** It survives removing the drift almost unchanged —
   −0.055 → +0.092 against −0.055 → +0.099. Whatever it is, the basket's
   +54%/yr is not it.
2. **But on the real panel it is one-sided, and that one-sidedness is the
   drift.** The long leg rises +0.193R across the sweep while the short leg
   *deteriorates* by 0.039R. Drift-removed, **both legs improve**
   (+0.107R and +0.040R). A draft of this section claimed the rise
   "appears in both directions" while its own table showed the short leg
   falling — the claim was right about the mechanism and wrong about this
   panel, and only the control separates them.

**So "letting winners run" pays here, and it is thin** — about +0.15R
across the 1:1→5:1 span, roughly 1.2× the round trip, present in both
directions once the basket's drift is out of the way.

> **A caveat on these rows specifically.** This sweep enters every name
> every session with a 10-session hold, so positions from different entry
> dates overlap heavily — S13's error, reproduced here deliberately and
> **labelled rather than fixed**, because the rows are what the document
> reports. **No p-value is computed for it at all**, in the module or
> here; the *directions* are the finding and no significance is claimed.
> §2's figures do not share the defect: one position per name at a time,
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
4. **Nothing further is measured on this universe without BOTH controls
   beside it** — the mirrored short leg *and* the drift-removed panel.
   §2 is the demonstration that one is not enough: every single-sided
   statistic looked sound, and the short leg alone still left a "15%
   genuine asymmetry" on the page that was not there. **A long/short sum
   cancels drift only for a linear payoff, and nothing this project
   trades is linear** — every barrier, stop, target and time exit breaks
   it. `drift_removed_panel` is cheap and belongs in every future
   measurement on a basket that rose.
5. **A stop is not worth 1R, and a backtest that says it is has not read
   the open.** §2.1: the gapped fill costs ~0.03R per trade here, which
   is a quarter of a round trip and arrives specifically on the days that
   hurt most.
6. **The universe remains the binding constraint**, and §1 is the
   sharpest statement of it this project has. A strict filter needs
   enough names to still leave a sample, and a drift-free test needs
   names that were not chosen for having risen. Both point at the
   survivorship-safe full universe.
