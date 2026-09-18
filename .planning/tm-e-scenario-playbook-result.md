# Trade Management Task E — result: the response axis measured, and nothing survives the date

**Run 2026-09-18.** Contract:
[`tm-e-scenario-playbook-preregistration.md`](tm-e-scenario-playbook-preregistration.md).
Reproducible with

```
python -m research.analysis.tm_e_policy_run
```

**A comparison run and a discovery-mode run**, both declared before
access. So every policy's figures are reported including the losers', and
**no policy may be promoted, advanced to a holdout, or quoted as evidence
of an edge.** The promotion `N` is not incremented.

---

# CORRECTION, 2026-09-18 — four design errors, and what they cost

**Added after the operator asked whether a badly-designed structure had
been reported as a failed one. It had, in part.** Each item below was
verified against the code and against Task D's own source, not conceded
because the question was asked.

## C1. The run did not test the registered specification

**This is the finding that governs the rest.** §5.1 of the registration
says the management is *"Task D's P3 unchanged"* and lists **"time exit
at 10 sessions"** among its clauses. Task D's own code:

```python
# 4. Time exit — every policy except the trailing ones.
if self.policy in (Policy.BASELINE, Policy.STOP, Policy.PYRAMID):
```

**P3 and P2 are explicitly excluded from the time exit**, and Task D
declares no time constant at all. *"Trail the rest"* means let the runner
run. The 10 sessions was invented here and presented as adopted.

It was not cosmetic. Of the 791 positions that scaled at +1R, the runner
ended:

| | with the 10-session timer | Task D's actual P3 |
|---|---|---|
| **trail** | 57 (7.2%) | **288** |
| timer | 518 (65.5%) | — |
| stop | 215 | 181 |

**P3's entire stated edge is the trailing runner, and two thirds of them
were killed by a timer this project's own precedent excludes.**

## C2. The entry is not an opening-range breakout

§3 calls it *"Zarattini's 'Stocks in Play', taken as specified"* and lists
a `range` row. The implementation reads the **overnight gap** and fills at
the open — no range, no breakout, no waiting for a break:

```python
with_gap = 1 if open_px > prev_close else -1
```

An ORB is **not computable on daily bars at all**; it needs intraday. So
this was not a shortcut but a claim the chosen data could never honour.
**The literature support claimed in §3 does not transfer**: `rd-c`'s
finding is that Zarattini's *filter* produces Sharpe 2.81 on *his entry*,
and only the filter was carried over.

## C3. `COMPRESSION → fade` was asserted, not derived

S8's rule is *"do not run mean-reversion into an emerging trend"* — a
statement about **trend**, i.e. the ADX axis. ADX was dead, so volatility
was substituted. **Low volatility is not mean reversion**, and no
mechanism was given. §3's disclosure covered a different worry
(discretisation cost) and not this one. **E1's deficit may therefore be a
statement about an arbitrary mapping rather than about regime selection.**

## C4. E2 is a stop-and-reverse system, not the scenario that was asked for

The operator described a *judgement* — *"어? 이건 단기로 반대방향이
보이니까"*. What was implemented reverses on **every** stop,
unconditionally: no confirmation, no read, no condition. That is a known
and different mechanism. **This reproduces Task C's own confession
verbatim** — *what was tested is not the hypothesis that was described.*

## C5 (minor). Exposure ran past the risk limits and was never checked

Gross notional over equity: **median 1.36×, max 2.44×**, against Risk
Parameters' 2–5% per order and 1–2× canary leverage. Task D's
fixed-reference convention produces this and it does not invalidate a
research comparison, but the R figures come from a book carrying more
exposure than the limits allow.

## What the correction costs, measured rather than guessed

A **diagnostic** was run with the time exit removed — mechanism diagnosis
on a spent window, which selects nothing and produces no candidate:

| | with the timer | Task D's P3 | direction |
|---|---|---|---|
| E0 futures | +73.0 | +56.5 (p 0.459) | unchanged, still not significant |
| E1 futures | −29.5 | −46.9 | unchanged, still below E0 |
| E2 futures | +22.2 | +43.7 | unchanged, still above E1 |
| **E3 futures** | −100.3 | **−429.3** | unchanged in sign, **4× worse** |
| E2 − E1 gap | +51.7 | **+90.6** | unchanged, larger |

**So the defect was load-bearing for every magnitude and for no
direction.**

## What is withdrawn, and what stands

**Withdrawn:**

- every **level** in §1's table, and the profit factors — they belong to
  a policy set that is not the registered one
- *"E0's +73.0R"* read as a statement about **P3** — it is not P3
- *"regime selection actively hurt"* read as a statement about **regime
  selection** — it is a statement about this mapping (C3)
- E2's +52R read as the value of a **trader's alternative scenario** — it
  is the value of unconditional stop-and-reverse (C4)
- the entry's claimed literature support (C2)

**Stands:**

- **nothing is distinguishable from zero.** True in both versions.
- **the hedge is worst on both cores, including where hedging is ~13bp
  cheaper than closing.** It is a within-run comparison whose only
  difference is the hedge, it agrees with Task C and Task D
  independently, and the diagnostic makes it *more* negative, not less.
- **E2 > E1 by ~+51R to +91R on both cores** — the most robust comparison
  in the study, holding under both exit rules.
- both implementation defects in §5, and the classifier's weekend reset,
  which are data facts.

## The structural criticism that outranks all four

**Task E tested a scenario *response* on top of a maximally
*unselective* entry.** The activity filter is a median split — half the
universe — and the run took **1,620 entries over 921 dates, nearly two a
day, always on.**

`rd-c` §2's finding is that **the filter is the strategy**, and
Barber/Lee/Liu/Odean found that *concentration in a few names* is the
second-best predictor of day-trader skill. A trader who thinks in
scenarios takes **few** trades; that selectivity is most of what makes
the scenarios worth anything. This study kept the entry promiscuous and
varied only what happened afterwards — which is the opposite of the thing
being emulated, and no amount of fixing C1–C4 addresses it.

---

## 1. The headline

> **Nothing is distinguishable from zero once the entry date is the unit
> of the test.** E0's apparent **+73.0R** is **+0.0486R per date, p =
> 0.221**. The largest policy gap in the table is real as a *comparison*
> and none of its endpoints is a result.

| policy | core | episodes | dates | total R | R/date | p | SEx | PF |
|---|---|---|---|---|---|---|---|---|
| **E0** | futures | 1,620 | 921 | **+73.0** | +0.0486 | 0.221 | 1.15 | 1.087 |
| E1 | futures | 1,335 | 740 | −29.5 | −0.0291 | 0.496 | 1.16 | 0.959 |
| E2 | futures | 1,363 | 723 | +22.2 | +0.0269 | 0.533 | 1.18 | 1.031 |
| E3 | futures | 851 | 579 | −100.3 | −0.0986 | 0.180 | 1.17 | 0.831 |
| E0 | spot | 1,620 | 921 | +11.7 | +0.0108 | 0.786 | 1.15 | 1.013 |
| E1 | spot | 1,335 | 740 | −74.3 | −0.0626 | 0.142 | 1.16 | 0.901 |
| E2 | spot | 1,363 | 723 | −23.1 | −0.0063 | 0.883 | 1.18 | 0.969 |
| E3 | spot | 851 | 579 | −128.1 | −0.1314 | 0.074 | 1.17 | 0.790 |

**Not one policy clears the profit-factor floor** of 1.3–1.5 pinned before
access; the best is **1.087**. So on its own terms every one of the eight
fails, and the comparison below is all this run legitimately produced.

**`SEx` is the date-clustered standard error over the naive per-episode
one**, 1.15–1.18 throughout: pooling episodes would have understated the
error by about 16%. CLAUDE.md requires that ratio beside the figure for
the same reason the permutation rule requires `null_sd / se` — the size of
the correction is itself the finding. **An earlier version of this run
reported only the corrected p, violating the rule in its first
application**, and that was caught on review.

**This is the first study run under the session-clustering rule added the
same day**, and the rule immediately did the work it was added for: total
R would have read as "E0 made +73.0R", and the date-clustered figure says
E0 made nothing measurable. Ten names enter on the same day and share
that day's market-wide move; 1,620 episodes are 921 dates.

## 2. What the comparison says — the part that is a finding

**Regime selection actively hurt, by about 100R on futures and 86R on
spot.** E1 is the only change from E0, and it is worse: −29.5 against
+73.0 on futures, −74.3 against +11.7 on spot.

**That was the registered prior and it held.** S10 measured the structure
axis carrying nothing and discretising costing most of the volatility
axis (5.21× separation becoming ~1.5×). The registration said *"if regime
branching only reproduces what a continuous conditioner already does,
E1 ≈ E0 and the machinery is cost without benefit."* The observed
outcome is worse than that: the branching is not neutral, it is
subtractive. Two mechanisms are visible and not separated by this run —
E1 takes **288 fewer entries** (warm-up declines them), and in
COMPRESSION it inverts the direction, which is a different bet rather
than a filtered one.

**The alternative scenario recovered most of it**, and this is the one
directionally encouraging number in the run: E2 is +22.2R against E1's
−29.5R on futures, and −23.1R against −74.3R on spot. *"The thesis broke,
so the opposite thesis is now live"* is worth about **+52R** relative to
going flat, on both cores. The registration recorded no prior for this, so
it is the genuinely new observation — **and it is not significant**
(p = 0.533 / 0.883), so it is a direction to specify against, never a
result.

## 3. The hedge loses on both cores, and the tax advantage does not rescue it

**E3 is the worst policy on both cores** — −100.3R and −128.1R — and this
is now the **third independent confirmation** of a finding Task C and
Task D each produced separately.

**What is new is that it holds where the economics favour the hedge.** The
registered prediction was split, and this was the most interesting line in
the design:

| | predicted | observed |
|---|---|---|
| futures core | **E3 < E2** by ~one round trip | **−122.5R — held** |
| spot core | E3 **may** beat E2 by the tax differential | **−105.0R — E3 still lost** |

On a KOSPI spot core, closing pays **20 bp** (5 bp 증권거래세 + 15 bp
농특세) where hedging with a single-stock future pays ~6.5 bp and no
transaction tax. **Hedging is ~13 bp cheaper than closing**, which is the
same magnitude P5 lost by on BTC where that gap is zero. A tax-driven
inversion would show as the hedge-vs-close gap moving toward zero on
spot. **It moved +17.5R against a −122.5R gap.** The structural cost
advantage is real, it moves the comparison in the predicted direction, and
it is roughly an order of magnitude too small to cross zero.

**"May beat" had no falsifying outcome, and that is a flaw in the
registration rather than a result.** A prediction that cannot fail is not
evidence either way. The figure that *is* informative is the difference
between the cores, because the tax gap exists in one and not the other —
and the run reports it for that reason.

**The mechanism is visible in the win rate, and it is the classic
shape.** E3 wins **47.0%** of episodes against E2's 36.0% while losing far
more in total. The hedge converts a defined 1R loss into a small win more
often, and the episodes that go wrong go much more wrong, because the core
is no longer stopped — it runs to its time exit carrying the unhedged half.

## 4. Three things that confound the E3 comparison, disclosed

**E3 is not "E2 plus hedges" — it also trades much less.** 851 episodes
against 1,363, because a hedged core stays open and blocks re-entry in
that name for up to 10 sessions. Part of E3's deficit is therefore
*opportunity forgone* rather than *hedge cost*, and this run does not
separate the two.

**65 of 493 invalidations were too small to hedge** and fell back to E2's
behaviour — 13%, so E3 is a blend, roughly 87% hedge and 13%
alternative-scenario. The registration required that count be reported
rather than absorbed.

**That figure was 130 in the first write-up, and it was double-counted**:
the alternative episode copied the original's `hedge_fallback` flag and
`PolicyResult.fallbacks` counted both records for one event. Caught on
review. 65 alternatives occurred under E3, one per fallback — so **E3 is
not a clean arm.**

**E3's hedge is priced off SPOT closes, not futures**, and that is a data
limitation rather than a choice. `runs/krx_futures_liquidity.json` — the
only copy, since KIS drops an expired contract's entire series — spans
**2025-12-12 onward, 188 dates against this panel's 1,176 (16%)**. There
is no front-month series for 2021-2025 at any price. So basis moves, roll
P&L and roll costs are all absent. **The omitted roll costs flatter E3**,
which loses anyway, so the sign is safe and the magnitude is an
approximation; basis change over a ≤10-session hedge is small against a 1R
move and roughly mean-zero.

## 5. Two implementation defects, both found by the run rather than by reading

**The regime classifier resolved 0 of 1,176 bars, and nothing in its own
tests could have caught that.** `regime_classifier` infers its bar
interval from the first two bars and resets on anything else — correct for
1-minute crypto, which really is continuous. KRX daily spacing is 1
calendar day 902 times, **3 over a weekend 220 times**, and 2/4/5/6/7/8
across holidays. Measured: **273 discontinuities, 0 bars resolved.** It
reset every weekend and could never accumulate enough readings to leave
warm-up.

Every fixture in `test_regime_classifier.py` is synthetic minute bars, one
interval apart by construction, so the module's own suite was structurally
incapable of seeing this. **CLAUDE.md's "run it where it will run", fourth
instance.** Fixed by adding an optional `session_calendar`: contiguity
becomes *"the next session"*, a date absent from the calendar is still a
discontinuity, and passing it alongside `expected_interval` raises rather
than letting one definition silently win. `None` keeps the interval
behaviour byte-for-byte, so every 1-minute caller is unaffected. With it,
**9,068 of 11,760 name-days resolve (77.1%)**, of which 52.7% EXPANSION.

**E2 and E3 came out byte-identical on the first run, because E3 was
never implemented.** The invalidation branch closed the core *before* the
policy was consulted, so E3 was E2 wearing a label — and the verdict it
printed on the registered prediction was meaningless rather than merely
wrong. **What exposed it was two policies producing identical numbers**,
which is the kind of external observable CLAUDE.md's change-checks are
about; reading the code did not.

The fix is the E3 hypothesis stated properly: **a hedged core does not
re-stop.** E3's claim is *"I do not want to close here, I want to
neutralise and see what happens"*, so the hedge supersedes the stop and
the core runs to its time exit. Letting STOP fire again closes the core
and collapses E3 into E2 by construction.

## 6. What this does not establish

**Nothing here is evidence of an edge.** Discovery mode's first guard, and
independently true: every p ≥ 0.066.

**A negative comparison is not a closed direction.** What was tested is
*one* entry (opening-range breakout on previous-session relative
turnover), *one* regime construction (the volatility axis, two branches),
*one* management (Task D's P3), on *one* universe of ten names over 4.80
years. That is one cell. Declaring "scenario logic does not work" from it
would be this project's most-repeated error, committed at the stage where
it is most expensive.

**The entry may simply not be there.** `rd-t` found one usable signal and
`rd-u` found its conjunctions capture 8.7% of the available move. The
registration predicted in advance that none of the eight would produce a
promotable candidate, for exactly this reason: **Task E changes the shape
of the payoff, not the prediction.** A response layer cannot rescue an
entry with no edge, and the registered expectation was that it would not.

**Costs are large relative to everything measured.** E0-futures paid
**31.0M KRW** in costs to net +36.5M — so gross was ~67M and costs took
46% of it; on the spot core the same entries paid **61.6M** to net 5.9M,
i.e. 91%. At 1,620 episodes the round trip compounds into a first-order
term, which is `rd-u` §5.1's point in reverse: the cost is small against a
day's *move* and large against this strategy's *edge*.

**An earlier version of this run charged a futures round trip of 19.5 bp
rather than the registered 13** — half on entry and the *whole* figure
again on close. Caught on review. Fixing it moved every futures-core
number materially (E0 +34.9R → +73.0R, E2 −3.0R → +22.2R) and changed no
qualitative conclusion, which is the only reason the write-up above did
not have to be rebuilt.

**The window is spent and the panel carries `rd-r`'s selection
contamination**, unchanged from `rd-t`/`rd-u`: the ten were chosen on
2026Q1 futures turnover, inside the window.

## 7. What follows

1. **The response axis is measured and it is not the binding
   constraint here.** Task D found management worth a Gate A pass on an
   entry that had one. On this entry, the best response is worth
   `p = 0.511`. That is consistent with Task D rather than contradicting
   it, and it relocates the problem back to the entry.
2. **The two implementation fixes outlive the result.**
   `regime_classifier`'s `session_calendar` makes the classifier usable on
   *any* daily market, which every future Korean study needs. And the
   E3-keeps-the-core semantics is what "hedge" has to mean for the
   comparison to be about hedging.
3. **The alternative-scenario branch is the only thing worth carrying
   forward** — +51R over going flat, unsignificant, and never measured
   before. It belongs in a future specification as a *hypothesis*, with
   its own registration.
4. **Do not re-run this with a different branch or constant.** The
   stopping rule forecloses it: the permitted responses are to accept the
   result, or to specify a different state machine in a new
   pre-registration.
5. **The entry is where to go next**, which is the operator's items ①
   (survivorship-safe universe, so breadth is real) and the 매매동향 axis
   once it has depth — the one information source Korea has that the
   mechanism behind `rd-u` §2 is actually about.
