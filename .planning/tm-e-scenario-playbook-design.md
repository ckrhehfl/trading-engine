# Trade Management Task E — scenarios as a state machine, not as prose

**Design draft, 2026-09-18. NOT yet a registration** — the open decisions
in §8 must be closed by the operator first, and nothing here touches data
until they are.

Opened at the operator's request, in their own words:

> 트레이더는 보통 이러한 여러 고지능적인 사고를 하잖아? … 시나리오도 단순
> 지금의 장만 보고 하는게 아니라 미래의 어떻게 움직일지 한두가지가 아니라
> 수가지 미리 생각하고 예측하면서 어떻게 대응할지 … 방향대로 안가면 아
> 이건 끝이네 하고 정리하고 나온다던지 아니면 어? 이건 단기로 반대방향이
> 보이니까 헷징을하자

This is the third time this operator has pushed on the same thing, and the
previous two produced the multi-leg `Book` (Task A) and the management
comparison (Task D). **Task D is the reason to take it seriously**: it is
the only study in this project's history where anything cleared Gate A,
and it did so by varying nothing but the response.

---

## 1. Why this is worth doing, in one table

Task D held the entry **byte-identical** across six policies and varied
only what happened after:

| policy | Gate A | Sharpe |
|---|---|---|
| **P3** scale out 50% at +1R, trail the rest | **PASS** | **+0.716** |
| **P5** the same reduction taken as a hedge | **PASS** | +0.617 |
| P0 / P1 / P2 / P4 | FAIL | withheld by the registration |

The whole spread — Gate A failure to Gate A pass — is a management
effect, and **none of the 129 prior selection trials varied this axis at
all.** P3's Sharpe is the only figure in project history above its own
window's detection floor.

`rd-u` then measured why that matters more than it looks: a KRX day moves
a median **131 bp against a 13 bp round trip**, and the best signal work
extracted **11.4 bp** of it. The prize is ten times the cost and we are
capturing under 9% of it. **Response is the lever that has moved; it is
also the only one this project has ever moved.**

## 2. What "thinking in scenarios" is, stated so it can be tested

A discretionary trader imagines several paths and a response to each.
Written down precisely, that is a **finite state machine over a position**,
and `metrics.book`'s legs (Task A) can already express it — closing *one*
leg and keeping the rest is exactly the vocabulary this needs.

```
  FLAT ──setup──▶ ARMED ──trigger──▶ OPEN(primary)
    ▲               │                     │
    │          setup decays               ├─ path A  target approached ─▶ scale + trail
    └───────────────┘                     ├─ path B  stalls            ─▶ time exit
                                          ├─ path C  adverse, tolerable ─▶ hold, thesis intact
                                          └─ path D  adverse, invalidating
                                                       ├─▶ FLAT              (E0/E1)
                                                       ├─▶ OPEN(alternative) (E2)
                                                       └─▶ OPEN(primary) + hedge leg (E3)
```

**The discipline that makes this rigorous rather than vibes: the paths
must be mutually exclusive and exhaustive.** At every bar exactly one
branch applies. A trader who says *"I'd consider a few things here"* has
an implicit branch for everything else; a state machine with a hole in it
silently does nothing, which is a real position taken by accident. Task C
is the precedent — its parameter-free exit pinned the holding period to
one hour *by accident* and so tested a different hypothesis than the one
described.

**This is also the honest form of what an LLM can contribute.** CLAUDE.md
forbids an LLM acting as the live trading decision maker, and that rule is
not being bent. What is allowed, and is in fact the stronger version, is
that the branch *enumeration* — the part that looks like thinking — is
research done offline, frozen into code, and backtested. Systematic
traders do exactly this: the deliberation happens once, in advance, and
what runs is the conclusion.

## 3. What the regime selects — and why S10's verdict does not forbid it

`research/strategies/regime_classifier.py` already exists and is complete:
two axes, hysteresis, a 14-bar minimum dwell, and contiguity checks that
fail closed across a gap.

| axis | from | S10's measurement |
|---|---|---|
| volatility | **absolute** ATR | **5.21×** separation — works |
| structure | ADX | carries nothing, on both axes it could have |

CLAUDE.md records S8's rule: *"Use the continuous absolute-volatility
measure as a conditioner, not the discrete label — discretising costs most
of the signal (5.2× becomes ~1.5×)."*

**That rule is about using the label as a PREDICTION INPUT, and this task
uses it for something else.** Discretising a continuous variable before
predicting with it throws away information — an ordinary statistical fact.
Selecting *which playbook runs* is not a prediction; it is a branch, and a
branch is inherently discrete. You cannot run 37% of a mean-reversion
playbook.

So the S10 finding constrains a different use, and **regime-as-playbook-
selector has never been tested here.** That is stated as a distinction to
be checked rather than assumed: E1 below exists precisely to find out, and
S10's result is a real reason to expect it may not help.

**The structure axis is used only as a tie-break, never alone**, since it
has now failed twice.

## 4. The entry, fixed from outside — and it absorbs the turnover filter

Task D's discipline, reproduced verbatim because it is what keeps `N` at
1: **the entry rule and the complete policy list are committed before any
data access, and neither may be extended after seeing results.**

The entry is an **opening-range breakout restricted to abnormally active
names** — Zarattini's "Stocks in Play", taken as specified and not tuned.
`rd-c` §2 records why: that filter takes a plain breakout from failing
entirely to Sharpe 2.81, and the filter is the strategy.

**This is possible only because the filter is computable daily.** `rd-t`
§4 records that KIS serves no per-minute 거래대금 (`acml_tr_pbmn` is
cumulative), which cost that study its strongest prior. Measured
2026-09-18: **18,222 of 18,222 daily bars carry it, 100%.** So the
literature's best filter is available at exactly the frequency this task
operates at, and the operator's item ② is absorbed here rather than
deferred.

## 5. The four policies

Entry identical across all four. Initial-layer sizing held constant, with
`R` normalised to that layer, exactly as Task D did.

| | playbook selection | on invalidation |
|---|---|---|
| **E0** | one playbook always (Task D's P3 management) | flat |
| **E1** | **regime selects** the playbook | flat |
| **E2** | regime selects | **alternative scenario** — the opposite thesis is now live |
| **E3** | regime selects | alternative taken as a **hedge leg**, core kept |

E0 is the baseline and is not a strawman: it is the configuration that
actually cleared Gate A on another market.

## 6. Registered predictions — the part that makes this falsifiable

Task D's value came from P5 carrying a prediction that it would lose, and
losing by the predicted mechanism and roughly the predicted amount. Same
here.

**E1 vs E0 — genuinely uncertain, and the prior leans negative.** S10
found the structure axis carries nothing and that discretising costs most
of the volatility axis. If regime branching only reproduces what a
continuous conditioner already does, E1 ≈ E0 and the added machinery is
cost without benefit.

**E2 vs E1 — no prior at all, and this is the question nobody has asked.**
Whether "the thesis broke, so the opposite thesis is now live" is worth
anything is untested here and, as far as `rd-c`'s catalogue goes,
untested in the literature this project has read.

**E3 vs E2 — predicted to LOSE on futures, and possibly to WIN on a spot
core. This is the most interesting line in the document.**

Task D measured P5 losing to P3 by 14.4R over 464 identical entries
(≈0.031R each), and gave the mechanism: *a hedge is a costlier way to
reduce exposure than reducing it* — a second spread plus a second set of
fees, with double margin. Task C found the same thing independently.

**But both were measured on BTC-USDT, where no transaction tax exists**,
and Korea is structurally different:

| reducing a KOSPI **spot** long | cost |
|---|---|
| sell the spot | 증권거래세 5 bp + 농특세 15 bp = **20 bp**, sell side only |
| short a single-stock future against it | **~6.5 bp** (half of rd-q's 13 bp round trip), **no 증권거래세** |

So on a **spot** core, hedging is roughly **13 bp cheaper than closing**,
which is the exact quantity P5 lost by on a market where that gap is zero.
**The registered prediction is therefore split:**

- core in **futures**: E3 < E2, by about one futures round trip. P5 should
  replicate, and if it does not, this project's cost model is wrong and
  that is investigated before anything else here is trusted.
- core in **spot**: E3 may beat E2 by roughly the tax differential. If it
  does, the operator's hedging intuition is correct *for this market and
  for a structural reason that does not exist on BingX* — which is a
  materially different claim from the one Task C and Task D refuted.

## 7. What this cannot establish, stated before it is run

**Discovery mode.** KRX daily was spent by `ms-f` on 2026-09-13. Under
CLAUDE.md's amended rule a spent window is closed to selecting anything
for promotion and open to generating hypotheses confirmed elsewhere. So
**nothing here may be promoted, quoted as evidence of an edge, or reported
as a pass**, and the only legitimate output is a written specification.

**There is no clean confirmation window for Korean equities yet, and that
is a real gap this task does not close.** KRX daily is spent; KRX intraday
is a decaying ~250-session window; 매매동향 has 33 dates. Confirming
whatever Task E produces needs either the full survivorship-safe universe
(the operator's item ①) or more calendar time. **Building the
specification first is still the right order** — it is the artifact that
has to exist before any confirmation is possible — but it should not be
mistaken for evidence.

**Scenario branching does not create predictive edge.** If the IC is zero,
branching on it is branching on noise. What this task can do is change the
*shape* of the payoff — cap the loss when wrong, extend the gain when
right — which is how P3 beat its alternatives without any better
prediction, and is what `rd-u` §5.1 implies is the binding lever given the
move is 10× the cost.

**A comparison run.** Declared as one here, before any data access, naming
the axis varied (response) and what is held fixed (entry, sizing), so that
every policy's figures may be reported including the losers'. No policy
may be promoted on the strength of it.

## 8. Open decisions — these belong to the operator, and nothing runs until they are closed

1. **Spot core, futures core, or both?** This decides whether the E3
   question above is even live. Running both doubles the policy count from
   4 to 6 and is the only way to test the tax-inversion prediction;
   running futures-only is cheaper and forecloses the most interesting
   registered prediction in §6. **Recommendation: both**, because the
   inversion is the one place this project's accumulated negative result
   on hedging might not transfer, and it is the operator's own idea.
2. **Which universe?** The `rd-r` ten, or wait for the survivorship-safe
   full universe. The ten carry a known mild look-ahead (selected on
   2026Q1 futures liquidity, Spearman +0.954 rank persistence) and give a
   4.80-year panel. **Recommendation: the ten now**, with the full
   universe as the confirmation vehicle later.
3. **Does the alternative scenario get its own risk budget, or share the
   primary's?** Sharing keeps total exposure constant and makes `R`
   comparable; a separate budget lets the alternative be sized on its own
   merit and changes what is being measured. **Recommendation: shared**,
   matching Task D's constant-initial-layer rule.

## 9. Sequence once those are closed

1. Write the state machine over `metrics.book`, with the exhaustiveness
   property as a real test — a bar that matches no branch must raise, not
   silently hold.
2. Port `regime_classifier` to the daily KRX panel and verify the dwell
   and hysteresis behave on real Korean data rather than on 1-minute BTC.
3. Implement the four (or six) policies with the entry fixed.
4. Commit the registration — entry, policy list, predictions, stopping
   rule — **before any run**.
5. Run, report every policy including losers, and produce the
   specification.

**The stopping rule, committed now**: the permitted responses to the
result are to accept it, or to specify a *different* state machine in a
*new* registration. Adjusting a branch and re-running is foreclosed.
