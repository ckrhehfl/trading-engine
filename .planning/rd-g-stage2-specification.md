# Research Direction Task G — the stage-2 specification, fixed before it runs

**Committed 2026-09-14, before any forward return of any situation has
been measured.** That ordering is the whole point and it is checkable
against git history, the way MS-E's thresholds were committed at `d57a4e3`
before being executed at `85cdfab`.

[`rd-b`](rd-b-situations-not-formulas.md) §7 Q3: *"measuring to diagnose is
diagnosis, measuring twenty things to choose one is selection. A catalogue
sweep is the second, so the catalogue and the FDR family should be
committed before the first measurement."*

---

## 0. Why this is a specification and not a pre-registration

`configs/research/preregistrations/` holds **holdout claims** — one-shot
accesses to an untouched window, enforced by `research.holdout`. Stage 2 is
neither: it runs in **discovery mode** on a designated discovery window
(Binance futures 1m, already spent), where CLAUDE.md's split permits
unlimited looking and forbids any result from being promoted or quoted.

**So nothing here is being protected from over-access.** What is being
protected is the *meaning of the FDR number*. False-discovery-rate control
is defined over a **family**, and a family assembled after seeing p-values
is not a family — it is a selection. Fixing `m` in advance is a statistical
requirement, not a governance one, and it is the only thing this document
locks.

## 1. The family — exactly 12 tests, and no more

Situations are the three that cleared stage 1's cost ceiling on BTC
([`rd-e`](rd-e-stage1-catalogue-result.md) §2). Definitions are frozen at
`python/research/situation_catalogue.py` as of this commit:

| | Situation | Definition | /yr |
|---|---|---|---|
| **S1** | support penetration | prior-1,440-bar low broken by ≥0.3% | 172 |
| **S2** | resistance break | prior-1,440-bar high broken by ≥0.3% | 168 |
| **S3** | abnormal activity | prior-60-bar volume ≥ its trailing 99th percentile | 113 |

Forward horizons, in 1m bars:

| | h |
|---|---|
| **H1** | 15 (15 minutes) |
| **H2** | 60 (1 hour) |
| **H3** | 240 (4 hours — rd-b's own recovery window) |
| **H4** | 1,440 (1 day) |

> **m = 3 × 4 = 12.** This number is fixed. A thirteenth test requires a
> new specification document, and re-running any of the twelve with a
> changed definition makes it a new test rather than a repetition.

**The four horizons on one event set are positively correlated**, which
Benjamini–Hochberg tolerates (it controls FDR under positive regression
dependence). It is recorded rather than corrected because the alternative —
Benjamini–Yekutieli — costs a `ln(m)` factor of power for a dependence
structure that is known to be positive here.

## 2. What is measured

**One statistic per test: the difference in mean forward return between
events and a matched placebo.**

```
for each situation S and horizon h:
    event_returns[i]   = (open[i+1+h] - open[i+1]) / open[i+1]   for each episode i of S
    placebo_returns[j] = the same, for each matched non-event timestamp j
    statistic          = mean(event_returns) - mean(placebo_returns)
    test               = Welch two-sample t-test, TWO-SIDED
```

Three properties of that definition, each deliberate:

1. **Entry is the bar *after* the event, at its open.** Task C's `+45` was
   `−97` from real fills because the book was built from prices the
   strategy saw when deciding. rd-b §3.3 made this a rule; this is it.
2. **Two-sided.** A situation that shifts the mean *down* is as much a
   finding as one that shifts it up — which branch to take is stage 3's
   question, not this one.
3. **No costs are applied, and that is not an oversight.** Costs are a
   constant subtracted from both arms, so they cancel exactly in the
   difference. **Stage 2 tests the difference; stage 1's cost ceiling
   already tested the level.** Conflating them would double-count.

## 3. The matched placebo, which is the instrument

rd-b §3.1 measured that events sit at the **76th volatility percentile** —
elevated, so an unconditional baseline reads a volatility difference as an
edge. That is this project's single most expensive recorded error, in the
form that produced *"minutes-scale trading is arithmetically impossible."*

**Matching rule, fixed here:**

- For each event at bar `i`, draw **K = 5** control timestamps.
- A control must share the event's **volatility decile** — deciles from
  `trailing_quantile` on the prior-60-bar realised volatility, so
  prior-only.
- A control must share the event's **hour of day** (UTC). Crypto has a real
  intraday cycle and events are not uniform across it.
- A control must be **≥ 240 bars from any episode of the same situation**,
  so a "control" is never a near-event.
- A control must have a full forward window (`i + 1 + h < n`).
- Controls are drawn **without replacement** within a test, seeded
  `numpy.random.default_rng(20260914)`, so the run is reproducible.

**If a stratum has fewer than K eligible controls, the event is dropped
from that test and the count is reported.** Silently reusing controls
would understate the placebo's variance.

## 4. The decision rule

**Benjamini–Hochberg at q = 0.10** across all m = 12, computed on the
two-sided p-values.

A test **advances to stage 3** only if **both** hold:

1. it is **BH-significant at q = 0.10**; and
2. `|statistic| > κ`, where **κ = 12bp** is the BTC round trip. A
   difference that is real but smaller than the cost of capturing it is a
   finding about market structure and not a candidate.

**Reported regardless of outcome**: every one of the 12 statistics, its
p-value, its BH rank, its event and control counts, and the number of
events dropped for want of controls. A sweep that reports only its winners
is not a sweep.

**If zero tests advance**, that is the result and is written up as such.
The permitted responses are to specify a *different* family in a new
document, or to take the three situations to stage 3's separator question
anyway on the grounds that a mean shift is not the only way a situation can
be tradeable — **not** to re-run these twelve with adjusted definitions.

## 5. What this run cannot produce

- **Not a candidate.** Stage 2 answers *"does the situation shift the
  outcome distribution"*. It does not produce an entry, an exit, a size, or
  a branch rule, and CLAUDE.md's discovery guard 1 forbids quoting any of
  this as evidence of an edge.
- **Not a Korean result.** It runs on BTC 1m. Transfer to KRX is an
  explicit assumption, and [`rd-f`](rd-f-korean-cost-structure.md) shows
  the Korean cost ceiling is 2.4–2.7× harsher — so a BTC-significant
  situation may still be untradeable there.
- **Not a directional claim per situation.** Two-sided by construction.

## 6. Predictions, recorded so they can be wrong

Registering a prediction costs nothing and makes a surprise legible.

1. **S1 and S2 will show a non-zero difference at H3 (4h)** — this is the
   horizon rd-b's recovery statistic already lives at, and level breaks are
   the most-watched event in the catalogue.
2. **The effect will be small enough to fail condition 2.** rd-b's own
   arithmetic says a 172/year situation would need >12bp per event to be
   tradeable, which over 6.96 years is a large amount of money to have been
   left on an instrument this liquid.
3. **S3 will separate most cleanly from its placebo** — abnormal volume is
   the one situation whose *defining condition* is not a price level, so it
   is least likely to be absorbed by volatility matching.

**If prediction 2 is wrong — if a difference exceeds 12bp — the first
suspicion is the placebo, not a discovery.** The matching rule in §3 is
where such a result would most likely come from, and it gets re-examined
before anything else.

## 7. Order of work

1. Implement the matched-placebo event study as
   `python/research/stage2_event_study.py`, with tests.
2. Run all 12. Report all 12.
3. Write the result to `.planning/rd-h-stage2-result.md` whatever it says.
