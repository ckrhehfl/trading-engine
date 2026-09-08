# Trade Management Task D — result: management is the biggest single effect this project has measured, and two policies clear Gate A

Executes `.planning/tm-d-breakout-management-preregistration.md`, which
merged before the implementation existed. Every threshold, policy and
reporting choice below was fixed in that document before any data was
loaded.

**Run**: Binance USDT-M futures BTCUSDT 1m, 3,661,780 bars, 2,543 days
(2019-09-08 → 2026-08-25). `FEE_BPS = 5`, `SLIPPAGE_BPS = 1`, starting
equity 100,000, 0.5% initial-layer risk per episode. Reproduce with
`research.analysis.tm_d_policy_run`.

**Fill-contract guard: zero breaches across all six policies.** Every
fill matched `klines[signal_bar_index + 1].open * (1 ± SLIPPAGE_BPS)`,
with the signal bar taken from `OrderIntent.created_at` and the timing
asserted as exactly signal + 1.

---

## 1. Gate A, evaluated first

| policy | Gate A | maxDD ≤ 20% | PF ≥ 1.3 | episodes ≥ 100 |
|---|---|---|---|---|
| P0 baseline | **FAIL** | no | no | yes |
| P1 stop | **FAIL** | no | no | yes |
| P2 trail | **FAIL** | no | no | yes |
| **P3 scale-out** | **PASS** | yes | yes | yes |
| P4 pyramid | **FAIL** | no | no | yes |
| **P5 partial hedge** | **PASS** | yes | yes | yes |

**Two policies clear Gate A. That is the first time any candidate in
this project has.** Statistics for the four failing policies are
withheld from the report and from its JSON output, per the registration
— the ordering exists because the scalping arc repeatedly produced high
PSR figures on runs that were already cost-disqualified.

**All six policies' figures are reported below, under the comparison-run
category added to CLAUDE.md on 2026-09-07** in response to this run.
That category's three conditions apply here and are stated so they can
be checked rather than assumed:

1. this run varies **management only** — the entry is byte-identical
   across all six — which is what makes it a comparison;
2. **no policy here may be promoted, advanced to a holdout, or put
   forward as a candidate on the strength of this run.** The window is
   spent, so that is true by construction as well as by rule;
3. a policy later proposed as a candidate must clear Gate A in its own
   registration, and **this run counts toward its `N`**.

§6 records how the category came about, including that the earlier,
stricter reading was applied first and that two drafts violated it.

## 2. All six, with the two that cleared Gate A marked

| | Gate A | episodes | return | maxDD | PF | Sharpe | win% | **total R** | mean R | peak qty |
|---|---|---|---|---|---|---|---|---|---|---|
| P0 baseline | | 1,778 | +12.5% | 37.8% | 1.154 | +0.187 | 49.7% | +42.1 | +0.024 | 26.3 |
| P1 stop | | 1,779 | +47.2% | 22.4% | 1.252 | +0.517 | 42.6% | +88.2 | +0.050 | 22.2 |
| P2 trail | | 312 | +98.0% | 38.3% | 1.288 | +0.569 | 20.1% | **+188.6** | +0.605 | 22.0 |
| **P3** scale out 50% at +1R, trail the rest | **✓** | 464 | +61.8% | **13.0%** | **1.542** | **+0.716** | 47.7% | +79.2 | +0.171 | **14.3** |
| P4 pyramid | | 1,779 | −6.9% | 34.7% | 1.186 | −0.014 | 44.1% | **−1.3** | −0.001 | **29.5** |
| **P5** same reduction as a hedge | **✓** | 464 | +50.6% | 14.6% | 1.528 | +0.617 | 28.6% | +64.8 | +0.140 | 21.3 |

**A ✓ means the policy cleared Gate A. A policy without one is reported
for comparison and is not a candidate** — see the three conditions in
§1. Reproduced verbatim from `runs/tm-d/run.log`; every ratio quoted in
§3 is derived from this table and checked against it.

`total R` is the registered headline, not win rate — sources are
explicit that scale-out raises win rate while capping the right tail, so
ranking on win rate would have favoured P3 for the wrong reason.

**P3's Sharpe of +0.716 is above this window's own detection floor of
0.623** (6.96 years, one-sided α=0.05), the only policy for which that
is true. PSR 0.9705.

**The two figures cover different sets, and neither is adjusted to
match the other.**

- **`total R` covers the 464 closed episodes only.** An episode still
  open at the last bar has no closing fill, so there is nothing to
  rebuild its R from.
- **`total_return` covers those 464 *plus* the one still open**, which
  `build_equity_curve` force-closes at the final bar.

So `total_return` is computed over one more episode than `total R` is.
Reported with their scopes rather than reconciled, because forcing them
to agree would mean either inventing a closing fill or discarding a real
one.

### Per year, because a pooled statistic alone hides regime concentration

| policy | +yrs | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| P3 | 4/8 | −1.6 | **+73.5** | +11.1 | −15.0 | +8.2 | +13.7 | −4.8 | −5.8 |
| P5 | 4/8 | −2.1 | **+71.7** | +10.2 | −16.2 | +4.8 | +11.5 | −7.3 | −7.7 |

**Four positive years out of eight, and 2020 supplies more than the
whole total.** Excluding 2020, P3 is +5.8R across seven years — barely
positive. The last two years are both negative. This is the same
concentration pattern S13 recorded, and it is why the registration
required per-year reporting beside any pooled figure.

## 3. What the comparison says, which is the point of the task

The entry is byte-identical across all six. Everything below is a
management effect.

1. **Management is the largest single effect this project has measured.**
   The same entry, managed differently, spans the full range from a
   Gate A failure to a Gate A pass. Nothing in the 129 prior selection
   trials varied this axis at all — every one of them searched for a
   better entry formula.

2. **The operator's own description won.** P3 is "take half off at +1R
   and let the rest run" — the thing asked for repeatedly and never
   tested until now. It has the best profit factor, the lowest
   drawdown and the only Sharpe above the detection floor.

3. **P5 lost to P3, as registered in advance.** Same 464 entries, same
   signal and fill bars, differing only in whether the 50% reduction
   was taken by closing or by opening an opposing leg: **+79.2R vs
   +64.8R**, a gap of 14.4R over 464 episodes (≈0.031R each). That is
   the second spread plus the second set of fees, which is what the
   prediction said it would be.

   This gives Task C's negative result a mechanism rather than leaving
   it an unexplained loss: a hedge is a costlier way to reduce exposure
   than reducing it.

4. **Pyramiding (P4) was the worst policy of the six.** It did not clear
   Gate A, and it is the only policy with a **negative total R (−1.3)**,
   while holding the **largest peak exposure (29.5** against P3's
   **14.3)**. "Add on strength" cost money on this entry, and the extra
   exposure bought nothing.

5. **Trailing alone (P2) produced by far the largest total R (+188.6)
   and failed Gate A on drawdown (38.3%).** That is exactly the
   trade-off the practitioner sources describe: trailing captures the
   right tail that scale-out caps, and pays for it in drawdown.

   **Both halves of that claim are visible in one run, on one dataset,
   with one entry** — P2 has 2.4x P3's total R and 2.9x its drawdown.
   This is the clearest confirmation of a practitioner claim this
   project has produced, and reporting it is the reason the
   comparison-run category exists.

## 4. What this is not

**It is not a pass, and cannot be.** The window is spent — Task S6
confirmed a holdout on it and S8 reclassified it as research data — so a
result from it is not admissible as evidence for promotion, whatever it
shows. That restriction is procedural and stands on its own; it is not a
claim that DSR is unclearable.

**Against the project-level `N` (~130), DSR remains far below 0.95** for
both passing policies, as it does for anything measured on this window.
The registration said to expect that and to report it anyway.

**Two positive years carry the result.** A strategy that is negative in
2022, 2025 and 2026 and clears its gates on 2020 has not been shown to
work in the regime it would actually be traded in.

**No walk-forward was run.** The registration scoped this as a
whole-window policy comparison; per-fold behaviour is unmeasured.

## 5. What was found while building it, and outlives this result

Five defects in the implementation and its own tests, each caught by
deleting the rule and watching the suite stay green rather than by
reading the code:

| defect | why it mattered |
|---|---|
| the time exit could **never fire** — it peeked at `window[i + 1]`, whose index is always out of range | the baseline policy had no exit at all |
| the entry quantity was computed twice from **different equity**, so the position model diverged from what the engine filled | invalidated an entire completed run, which was discarded |
| a pyramid add **swallowed the same bar's time exit** | P4 held overnight, and the safety net's stated invariant was false |
| the fill guard compared the price against **the bar the engine chose**, so same-candle execution would have passed | the guard checked the price and never the timing |
| the fill guard paired fills to intents **by position** | order divergence would validate a fill against another intent's side |

Plus three tests that could not fail: one asserting on a bar where no
add can occur, one whose `>` comparison held whether management started
at `t+1` or `t+2`, and one masked by a safety net that closed the
position anyway.

**The transferable part is the method, not the list.** Every one was
found by breaking the rule deliberately and checking that something went
red — never by review of the code, and never by the tests passing.

## 6. How the reporting rule got resolved, and why it needed a human

**This run is the reason the comparison-run category exists**, so the
sequence is recorded rather than smoothed over.

Gate A's rule — a failing policy is reported as failing, with no
statistic quoted in its favour — was written for a *candidate
evaluation*. Applied to a comparison it blocked the finding: four of six
policies failed, so the run answered its commissioned question and the
rule forbade most of the answer.

**Two drafts of this document violated the rule before that was
noticed**, both caught in review: P4's peak exposure quoted against
P3's, and P2's total R described as the largest of the six. The second
was the clearer breach — the registration's words are "no statistical
result is quoted **in its favour**", and "the largest total R" is
exactly that.

**The first fix invented a distinction that was not in the
registration** — "the direction is reportable, the number is not" — and
kept the sentences that suited the write-up. The second fix complied
strictly and stated the cost instead of arguing the clause.

**Why it went to a human rather than being reinterpreted here**: I wrote
the rule, so the looser reading was the one I had an interest in after
seeing which reading kept the findings intact. That is not a position
from which to reinterpret it.

The operator chose the option that fixes the condition **in the
pre-registration, before any result exists**, over the alternative that
required a judgement call at write-up time about whether a failing
policy was being "described as a candidate" — precisely because this run
demonstrated that such a judgement gets made by an interested party.

The category and its three conditions are in CLAUDE.md. Under it, §3's
figures for P2 and P4 are reportable, and none of the six may be
promoted or taken to a holdout on the strength of this run.

## 7. What this does not decide

Whether P3 justifies spending the Binance spot 1m holdout is a separate
human decision, in its own document, and is explicitly not granted by
this result. The arithmetic that makes it tempting — `N = 1` requires
0.63 and P3 observed 0.716 — is stated in the registration alongside the
reason it is not sufficient: `N = 1` removes the selection penalty, not
the replication question, and this project's own precedent is
`daily-tsmom-ensemble`, which got **two** disjoint pre-registered
confirmations and remained INCONCLUSIVE.

The 4-of-8 positive years, and 2020 carrying the total, are the first
things any such decision has to weigh.
