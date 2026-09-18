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

## 1. The headline

> **Nothing is distinguishable from zero once the entry date is the unit
> of the test.** E0's apparent **+34.9R** is **+0.0261R per date, p =
> 0.511**. The largest policy gap in the table is real as a *comparison*
> and none of its endpoints is a result.

| policy | core | episodes | dates | total R | R/date | p | PF |
|---|---|---|---|---|---|---|---|
| **E0** | futures | 1,623 | 923 | **+34.9** | +0.0261 | 0.511 | 1.040 |
| E1 | futures | 1,335 | 740 | −54.6 | −0.0478 | 0.263 | 0.926 |
| E2 | futures | 1,363 | 723 | −3.0 | +0.0084 | 0.846 | 0.996 |
| E3 | futures | 851 | 579 | −119.3 | −0.1210 | 0.100 | 0.803 |
| E0 | spot | 1,623 | 923 | +4.6 | +0.0075 | 0.851 | 1.005 |
| E1 | spot | 1,335 | 740 | −74.3 | −0.0626 | 0.142 | 0.901 |
| E2 | spot | 1,363 | 723 | −23.1 | −0.0063 | 0.883 | 0.969 |
| E3 | spot | 851 | 579 | −131.6 | −0.1354 | 0.066 | 0.785 |

**Not one policy clears the profit-factor floor** of 1.3–1.5 pinned before
access; the best is **1.040**. So on its own terms every one of the eight
fails, and the comparison below is all this run legitimately produced.

**This is the first study run under the session-clustering rule added the
same day**, and the rule immediately did the work it was added for: total
R would have read as "E0 made +34.9R", and the date-clustered figure says
E0 made nothing measurable. Ten names enter on the same day and share
that day's market-wide move; 1,623 episodes are 923 dates.

## 2. What the comparison says — the part that is a finding

**Regime selection actively hurt, by about 90R on both cores.** E1 is the
only change from E0, and it is worse: −54.6 against +34.9 on futures,
−74.3 against +4.6 on spot.

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
directionally encouraging number in the run: E2 is −3.0R against E1's
−54.6R on futures, and −23.1R against −74.3R on spot. *"The thesis broke,
so the opposite thesis is now live"* is worth about **+51R** relative to
going flat. The registration recorded no prior for this, so it is the
genuinely new observation — **and it is not significant** (p = 0.846 /
0.883), so it is a direction to specify against, never a result.

## 3. The hedge loses on both cores, and the tax advantage does not rescue it

**E3 is the worst policy on both cores** — −119.3R and −131.6R — and this
is now the **third independent confirmation** of a finding Task C and
Task D each produced separately.

**What is new is that it holds where the economics favour the hedge.** The
registered prediction was split, and this was the most interesting line in
the design:

| | predicted | observed |
|---|---|---|
| futures core | **E3 < E2** by ~one round trip | **−116.2R — held** |
| spot core | E3 **may** beat E2 by the tax differential | **−108.5R — E3 still lost** |

On a KOSPI spot core, closing pays **20 bp** (5 bp 증권거래세 + 15 bp
농특세) where hedging with a single-stock future pays ~6.5 bp and no
transaction tax. **Hedging is ~13 bp cheaper than closing**, which is the
same magnitude P5 lost by on BTC where that gap is zero. A tax-driven
inversion would show as the hedge-vs-close gap moving toward zero on
spot. **It moved +7.8R against a −116R gap.** The structural cost
advantage is real and it is roughly two orders of magnitude too small to
matter.

**"May beat" had no falsifying outcome, and that is a flaw in the
registration rather than a result.** A prediction that cannot fail is not
evidence either way. The figure that *is* informative is the difference
between the cores, because the tax gap exists in one and not the other —
and the run reports it for that reason.

**The mechanism is visible in the win rate, and it is the classic
shape.** E3 wins **46.1%** of episodes against E2's 35.8% while losing far
more in total. The hedge converts a defined 1R loss into a small win more
often, and the episodes that go wrong go much more wrong, because the core
is no longer stopped — it runs to its time exit carrying the unhedged half.

## 4. Three things that confound the E3 comparison, disclosed

**E3 is not "E2 plus hedges" — it also trades much less.** 851 episodes
against 1,363, because a hedged core stays open and blocks re-entry in
that name for up to 10 sessions. Part of E3's deficit is therefore
*opportunity forgone* rather than *hedge cost*, and this run does not
separate the two.

**130 of 558 invalidations were too small to hedge** and fell back to
E2's behaviour. The registration required that count be reported rather
than absorbed, and it is 23% — so E3 is a blend, roughly 77% hedge and
23% alternative-scenario.

**65 alternatives still occurred under E3**, from those fallbacks. E3 is
not a clean arm.

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
**46.6M KRW** in costs to net +17.4M — so gross was ~64M and costs took
73% of it. At 1,623 episodes the 13 bp round trip compounds into the
dominant term, which is `rd-u` §5.1's point in reverse: the cost is small
against a day's *move* and large against this strategy's *edge*.

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
