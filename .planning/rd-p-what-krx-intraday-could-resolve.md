# Research Direction Task P — what KRX intraday could resolve, measured before anything is specified

**Run 2026-09-16.** Module: `research/krx_intraday_power.py`. Reproducible
with

```
python -m research.krx_intraday_power                       # KRX, 30bp round trip
python -m research.krx_intraday_power --cost-floor 0.0012   # BTC's, for contrast
```

[`rd-n`](rd-n-stage3-separator-result.md) §8 item 1 named this as the next
step: *"a power calculation from the event arm's own dispersion, before
any family is specified."* [`rd-l`](rd-l-what-it-would-cost-to-know.md) §8
item 2 states the same rule as two separate claims — a statistical one and
a **pre-registration policy**.

Applied here to the series [`rd-o`](rd-o-krx-intraday-backfill.md)
collected. **It commits nothing**: no situation is defined, no threshold
chosen, no window designated. It measures the instrument, not the market.

---

## 1. The headline

> **The Korean cost floor, which rd-f framed as a 2.5–2.8× handicap, is
> an advantage for *resolution* and a handicap for *plausibility* — and
> those are two different things that have to be read together.**
>
> Resolving a 30bp effect on KRX intraday needs **26 to 665 events**
> depending on horizon, against BTC's **1,099 to 15,585** for 12bp. But
> every one of those KRX cells demands a **larger per-event effect**:
> 0.15σ to 0.75σ, against BTC's 0.03σ to 0.12σ.
>
> **The one cell where both halves are favourable is h = 240**: 0.15σ is
> the size rd-b's own framing calls detectable, and the event count is
> reachable from the cross-section — but only at the optimistic end of a
> band that spans 958 to 6,394.

## 2. The prerequisite: KRX's tape breaks five different ways

Before any figure below, a structural fact that invalidates reusing the
BTC machinery. Measured on the real 005930 series:

| gap | count | what it is |
|---|---|---|
| **11 minutes** | **249** | the closing call auction, 15:20–15:29 — no continuous trade, so no bar |
| 17.5 hours | 186 | overnight |
| 65.5 hours | 46 | a weekend |
| 30–31 minutes | 9 | a late open (수능일, the year's first session) |
| 2 minutes | 2 | genuinely missing |

Within a session the series is clean — **99.998% of intra-session steps
are exactly 60 seconds.** Between sessions it is not, and neither is the
close.

`stage2_event_study.forward_return` indexes **positionally**, because BTC
1m runs continuously: `open[i + 1 + h]` really is `h` minutes later. On
KRX that is false **510 times per symbol**, and the failure is silent — it
produces entirely plausible returns that happen to include an overnight
gap, which is the largest single move in an equity series.

> **A KRX event study that reuses the BTC machinery unchanged is wrong,
> and wrong in a way that turns an intraday study into an overnight one
> without changing a line of the write-up.**

`session_forward_return` keeps a pair only when entry and exit sit in the
same **contiguous run of 60-second bars**. The constraint costs real
sample: 99.3% of bars survive at h = 1 and only **32.0%** at h = 240,
against a 381-bar session. That shrinkage is evidence, not an
inconvenience, and it is why h = 1440 is absent from this document
entirely — a day-long horizon has no meaning inside one session.

This project already records gap-blindness as a real open exposure on
BTC, where it affects **two** signal positions in 3.66M bars. Here it
would affect every session boundary. Same class of bug, three orders of
magnitude more of it.

### 2.1 The first implementation got this wrong, and the document was complicit

**Corrected on review of PR #175.** The first version labelled bars by
their **KST trading date** — and 15:19 and 15:30 share a date, so a return
spanning the 11-minute closing auction survived the check and was recorded
as an `h`-minute return when it was `h + 10`.

It is not that the auction gap was unknown. **The table above lists it
first**, in the same document, written by the same pass that wrote the
date-label code. The documentation and the implementation shared one wrong
mental model, and the test suite — written from that same model — agreed
with both. That is the exact failure this project's Change checks name:
*a verification that shares an assumption with its implementation confirms
the misunderstanding rather than catching it.*

What it cost, measured on 005930: **249 auction gaps, 260 contaminated
pairs at h = 1**, and σ inflated by up to **13%** at h = 240 — 231.3bp
against the corrected 201.3bp, which moved the h=240 event count from 878
to 665 and the required effect from 0.13σ to 0.15σ. Every figure in this
document is the corrected one.

The replacement is a **contiguous-run rule** rather than a longer list of
special cases: a block breaks wherever consecutive bars are not exactly
60 seconds apart. One condition covers all five rows of the table above,
so none of them has to be remembered — including the next one nobody has
found yet.

## 3. What the instrument can resolve

Unconditional dispersion pooled across the ten KR-10 names, 2,493
symbol-sessions, α = 0.002685 (BY rank-1 at m = 12), power 80%, cost floor
**30bp** (rd-f).

| h (min) | observations | σ | **effect / σ** | n (lower bound) | n at 1.2× | n at 3.1× |
|---|---|---|---|---|---|---|
| 5 | 921,585 | 40.0 | **0.75** | 26 | 38 | 253 |
| 15 | 885,221 | 64.5 | **0.47** | 68 | 98 | 656 |
| 30 | 836,324 | 86.4 | 0.35 | 122 | 176 | 1,177 |
| 60 | 746,864 | 115.3 | 0.26 | 218 | 314 | 2,095 |
| 120 | 585,934 | 150.2 | 0.20 | 370 | 533 | 3,557 |
| **240** | **301,915** | **201.3** | **0.15** | **665** | **958** | **6,394** |

σ and the floor in basis points.

**The `n` columns are a band, not a number, and the lower bound is not
the answer.** The measured dispersion is *unconditional* — over all bars.
rd-l §4.1 found event forward returns run **1.2× to 3.1×** more dispersed
than unconditional ones, because events are volatility bursts. In events
that is **1.4× to 9.6×**, which is the difference between reachable and
not. Quoting the unconditional figure alone is precisely the mistake that
cost rd-l its first headline, and it is not repeated here.

## 4. The trade-off, which is the actual finding

A higher cost floor does two opposite things at once:

| | BTC 1m, 12bp floor | KRX intraday, 30bp floor |
|---|---|---|
| events to resolve the floor | 1,099 – 15,585 | **26 – 665** (lower bound) |
| per-event effect demanded | 0.03σ – 0.12σ | **0.15σ – 0.75σ** |

> **Cheap to settle, hard to satisfy.** rd-l §6 found the first half on
> BTC — *"a harsher cost floor makes the question cheaper to settle, not
> harder"* — and this is the same arithmetic on a real Korean instrument,
> with the second half attached: what you buy in sample size you pay for
> in required effect.

So the horizons split into two useless regimes and one interesting one:

- **h = 5–15 demands 0.47σ to 0.75σ.** Few events needed, and a
  conditional mean shift of that size is not a weak signal — it is a
  different claim from anything in the microstructure literature or in
  this project's own record. Cheap to test and almost certainly negative.
- **h = 240 demands 0.15σ**, which is exactly the size rd-b's framing
  calls detectable (*"an event study over 1,196 episodes detects a 0.15σ
  effect at t ≈ 5"*). The cost is 958–6,394 events.
- h = 30–120 interpolates.

## 5. Is h = 240 reachable? Genuinely maybe, for the first time

**2,493 symbol-sessions are collected**, and the collector adds ten more
every trading day.

| a situation firing | events |
|---|---|
| once per symbol-session | 2,493 |
| once per two symbol-sessions | 1,246 |
| once per five | 498 |
| once per ten | 249 |

Against h = 240's band of **958 – 6,394**:

- at the optimistic end, a situation firing **once per five symbol-sessions**
  clears it, and once per symbol-session clears it two and a half times over;
- at the pessimistic end, nothing available clears it;
- the honest statement is that **it depends on a quantity nobody has
  measured yet** — the event dispersion for a *Korean* situation, where
  1.2×–3.1× is borrowed from BTC.

**That is still the strongest position this project has been in.** Every
prior arithmetic of this kind returned a number nobody could reach: BTC
stage 2's open half needed 5,619 events or 49.8 more years; stage 3's
detectable difference exceeded anything observed. This one returns
*maybe*, and names exactly which measurement would settle it.

## 6. What this does and does not establish

**Does not establish that anything is there.** No situation, no
threshold, no forward return conditioned on anything. The dispersion
measured here is a property of the series, like volatility.

**Does not designate a window.** Whether KRX intraday becomes a discovery
window is an operator decision under the Discovery/Confirmation split.
Measuring an instrument's resolution selects nothing, which is why it
could be done without one.

**Does not import BTC's event-dispersion ratio as fact.** 1.2×–3.1× was
measured on BTC 1m (rd-l §4.1) and is carried here as a **band with its
provenance stated**. The first Korean family that runs will measure its
own, and that figure is the one that decides §5.

**Does not clear the survivorship problem.** KR-10 is ten
currently-listed names fixed by a day-one rule (`ms-e`) — bounded, not
removed. A full-universe intraday scan reopens
[`rd-d`](rd-d-discovery-mode-and-the-full-universe.md) §2.2 unchanged, and
rd-o measured that scan at roughly 43 days of continuous collection.

**Does not settle which cost floor applies.** 30bp is the today's-rates
end of rd-f's 30–33bp range and the more favourable assumption. rd-f also
records that the **direction** of its single-rate simplification's bias is
not established — which remains a prerequisite for any Korean
registration, and is now blocking a live question rather than a
hypothetical one.

## 7. What follows

1. **Any Korean family is specified at h ≥ 120, or not at all.** Below
   that the required effect is implausible, and rd-l §8 item 2's policy
   half says a family that cannot ask an answerable question should not
   be run. That is a constraint available *before* a specification exists,
   which is the whole point of computing this first.
2. **The first thing such a family must report is its own event
   dispersion**, because §5's answer turns on it and BTC's 1.2×–3.1× is
   borrowed. `null_sd / se` is already mandatory on every permutation
   test (CLAUDE.md, Discovery and Confirmation).
3. **Session-aware returns are not optional.** `session_forward_return`
   exists so the next study does not rediscover §2 the expensive way.
4. **The cross-section keeps growing.** BTC's window was fixed at 6.96
   years; this one gains ten symbol-sessions a trading day, so a count
   that is out of reach this quarter may not be next year. That is a new
   shape of answer for this project, and it argues for keeping the
   collector healthy over specifying a family in a hurry.
