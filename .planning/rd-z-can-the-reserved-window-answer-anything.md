# Research Direction Task Z — what the reserved window can and cannot be asked

**The question, asked before the window is spent rather than after.** The
pre-2019 KRX panel is this project's only unspent window with usable power, it
can be accessed once, and `N` must stay at 1 for the access to mean anything.
So the first thing to establish is not *what* to test there but **which shapes
of question it can answer at all**.

`CLAUDE.md` already requires this — *compute the family's detectable effect
before specifying it* — and this project has repeatedly specified first. The
arithmetic is now a module (`research/window_feasibility.py`) so a later
pre-registration computes rather than quotes.

**Nothing here proposes a strategy, and nothing here authorises an access.**

---

## 1. Three constraints, pulling against each other

| | what it bounds | direction |
|---|---|---|
| **statistical** | how small an effect the span can find | longer window → smaller effect |
| **economic** | how often it can trade before costs eat the edge | tighter budget → fewer round trips |
| **evidential** | how many trades the Eligibility Bar requires | longer window → **more** trades needed |

The second and third pull opposite ways, which is what makes the intersection
worth computing rather than assuming.

## 2. Statistical: cutting the window for data quality is expensive

`CLAUDE.md` records that **pre-2000 data quality is unverified** beyond a handful
of probes, so truncating the panel is a live option. What it costs:

| start | years | floor (α=0.05) | smallest effect at 80% power | power for a true 0.5 | for 0.6 |
|---|---|---|---|---|---|
| **1991-08** | 27.35 | **0.315** | **0.475** | **83%** | **93%** |
| 2000-01 | 19.0 | 0.377 | 0.570 | 70% | 83% |
| 2005-01 | 14.0 | 0.440 | 0.665 | 59% | 73% |
| 2008-01 | 11.0 | 0.496 | 0.750 | 51% | 63% |

**A 2008 start turns a 0.5 edge into a coin flip** (50.5%). Even 2000 — the
conservative cut this project's own quality caveat implies — drops 13 points of
power. The full panel is the only start that detects the lower half of the
0.4–0.8 range with any confidence, and **even it cannot reach 0.4** (67.3%).

So the quality question is not a detail to settle later: **it decides whether
the window can see a realistic edge at all.** Verifying pre-2000 data is
therefore a prerequisite to the access, not a caveat on it.

## 3. Economic: the cost ceiling is a holding-period floor

Korean cash equities round trip at **30–33 bp**, of which 20 bp is 거래세. Drag
is linear in round trips, so at 33 bp:

| round trips / year | annual drag | share of a 0.5-Sharpe gross return (7.5%) |
|---|---|---|
| 1 | 0.33% | 4% |
| 2 | 0.66% | 9% |
| 6 | 1.98% | 26% |
| 12 | 3.96% | **53%** |
| 24 | 7.92% | **106%** |

**At 24 round trips a year the costs exceed the entire gross return.** Spending
at most a quarter of gross allows about **5.7 round trips**, which is a mean
holding period of roughly **43 sessions — two months.**

This rules out a whole class of question. Anything resembling the project's
earlier work — dozens of trades a day, or even weekly rebalancing — cannot be
asked here, whatever its signal looks like.

## 4. Evidential: the trade floor is 100, and it pulls the other way

`max(30, min(100, sessions / 20))` clamps at its ceiling on this panel: 6,701
sessions give 335 before the clamp, so **the floor is 100 trades**.

A holding period of two months and a floor of 100 trades look incompatible —
100 trades across 27 years is 3.7 a year, and a two-month holding period implies
about 6.

## 5. They are compatible, and the reason is the universe size

**Trade count and turnover are separable.** A round trip in one of `n` equally
weighted names moves `1/n` of capital, so `round_trips_per_year` equals
`trades_per_name_per_year` and **does not depend on `n` at all**. Widening the
universe multiplies trades while leaving turnover untouched.

Shapes clearing both constraints on the full panel, at 33 bp with a quarter of a
0.5-Sharpe gross return as the cost budget:

| names | trades / name / yr | total trades | round trips / yr | drag | budget used | holding |
|---|---|---|---|---|---|---|
| **1** | 4.0 | 109 | 4.0 | 1.32% | **70%** | 61 sessions |
| 5 | 1.0 | 137 | 1.0 | 0.33% | 18% | 245 |
| 5 | 2.0 | 274 | 2.0 | 0.66% | 35% | 122 |
| 10 | 2.0 | 547 | 2.0 | 0.66% | 35% | 122 |
| 20 | 2.0 | 1,094 | 2.0 | 0.66% | 35% | 122 |
| 30 | 2.0 | 1,641 | 2.0 | 0.66% | 35% | 122 |

**A single name is feasible, and barely** — 109 trades against a 100 floor is
nine spare, and it spends 70% of the cost budget. A first draft of this analysis
claimed a portfolio was *required*; the test that checks the feasible set
refuted it. What is true is weaker and more useful: **a portfolio reaches the
same floor at a third of the turnover**, so it is the shape to prefer rather
than the only one available.

## 6. What this leaves askable

Putting the three together, the reserved window can answer questions of roughly
this shape and no other:

- **a portfolio**, 5–30 names, rather than a single instrument;
- **holding months, not days** — 1–2 round trips a year per name;
- **long-only cash equities** — no retail shorting in this era, and single-stock
  futures did not exist for most of it, so this is a cash-equity claim with its
  own cost structure;
- **the full 27-year panel**, which makes pre-2000 data verification a
  prerequisite;
- and an effect of **0.5 or above**, since 0.4 is not reachable at 80% power even
  here.

## 7. What it does not answer, and what comes next

**This says nothing about which question to ask.** Three things are still open
and none is a session's to decide:

- **Where the specification comes from.** `N` must stay at 1, so the candidate
  cannot be chosen by searching this window. It has to arrive from a spent
  window's discovery, from literature, or from a mechanism argument — and
  `CLAUDE.md` records that the project's 129 trials all searched entry formulas,
  so the axes that have never been varied are the interesting ones.
- **Whether pre-2000 data holds up.** Section 2 makes this load-bearing rather
  than a caveat. It is a measurement, and the backfill now running is what makes
  it possible.
- **Whether to spend the window at all.** Human checkpoint #2, and nothing here
  changes that.

**The backfill is the gate on all three**: at 25% as of 2026-10-02, completing
around 10-08.
