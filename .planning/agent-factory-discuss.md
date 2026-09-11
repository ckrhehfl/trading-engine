# Agent factory — what can be automated, and the one thing that gets worse if you do

**Status**: open. Nothing has been built. This is a `Discuss` on a
direction the operator has wanted since early in the project and
deliberately deferred until the current work was finished.

**Date**: 2026-09-11.

**The ask, in the operator's own framing**: an environment — a *factory* —
in which an agent does research and infrastructure development on its
own, not merely auto-tuning parameters. It must survive things that only
appear over years: a market regime that has no precedent in the data, and
competition from other bots as an edge is discovered by others.

**The answer this document argues for is not "yes, build it" or "no".**
It is that the two halves have **opposite** constraints, and that the
half which looks most valuable to automate is the one automation makes
worse.

---

## 1. The evidence this is built on

Not speculation about agents in general. Two specific bodies of evidence:
this project's own defect record, and a full day of agent-run work
(2026-09-11, ten merged PRs) measured as it happened.

### What actually caught defects, 2026-09-11

| source | count |
|---|---|
| CodeRabbit review | **34** |
| running the thing where it runs | ~5 |
| deliberately going to look | ~2 |
| the tests written alongside the code | **~0** |

**That last row is the finding, and it is not new.** CLAUDE.md already
recorded the same shape from an earlier session (~7 review, 3 deployment,
3 external observable, 1 reading, ~0 from tests). Two independent
measurements, months apart, agree.

The rule CLAUDE.md derived from the first still holds: *a verification
that shares an assumption with its implementation confirms the
misunderstanding rather than catching it.*

### Concrete instances from that day, because the ratio is easy to dismiss

- A guard was written to stop a check passing vacuously. **The guard was
  itself vacuous** — it asserted nothing that would fail if the detection
  rule were weakened. Caught by review.
- A design proposal was written to fix a defect. **Its proposed mechanism
  reproduced the same defect** — a single signed target cannot express
  hedge mode's two legs. Caught by review.
- A claim was written about how the venue computes liquidation prices.
  **The data contradicting it was quoted three paragraphs above, in the
  same document.** Caught by review.
- An attempt to prove a retry worked disabled the *sleep* rather than the
  retry. **The tests still passed**, which would have been a false
  all-clear. Caught by running it.
- `bash -n` passed a file with a function defined twice, because that is
  legal shell. Caught by running it.

None of these was caught by the agent reasoning about its own work.

---

## 2. The infrastructure half: automation works, with one condition

The same day produced real, verified infrastructure: a deploy tool that
reproduces cron's environment, three layers of staleness detection, a
startup retry for a real venue failure, a catalogue of simulation-versus-
venue divergences, and a defect fix verified end to end against a live
exchange account.

That half works because **its correctness is checkable against something
outside the agent**: a test suite, a real deployment, a venue's own API
response, a reviewer.

**The condition is that those external checks exist and are not
optional.** Remove review from that day and the merged result contains a
vacuous guard, a design that rebuilds the bug it fixes, and a factual
claim contradicted by its own evidence.

So for the infrastructure half the design question is not "can an agent
do this" — demonstrated — but **"what is the external check, and what
happens when it is unavailable?"** On that day CodeRabbit was rate-limited
repeatedly and once stopped responding entirely. A factory running
unattended for years will meet that condition regularly.

---

## 3. The research half: automation makes it arithmetically worse

This is the part of the ask that cannot be granted as stated, and the
reason is arithmetic rather than engineering.

Every configuration examined against a body of data raises that data's
selection count `N`. The Deflated Sharpe Ratio deflates any result
against `N`.

**`N` is not the only input, and the table below should not be read as
though it were.** DSR (Bailey & López de Prado 2014, implemented as
`research/eligibility.py::evaluate_deflated_sharpe`, derived in
`.planning/sr-q-deflated-sharpe.md`) also depends on the sample length
`T`, the skewness and kurtosis of the returns, and the variance of the
trial Sharpe estimates. The table is CLAUDE.md's own, computed by
inverting the benchmark **with those other inputs held at the values from
that computation** — so it isolates `N`'s effect rather than showing a
general law. What survives the caveat is the direction and the order of
magnitude, which is all §3's argument needs.

CLAUDE.md's table, reproduced:

| N | annualized Sharpe required to clear DSR 0.95 |
|---|---|
| 1 (a pre-registered holdout) | **0.63** |
| 5 | 2.17 |
| 50 | 3.56 |
| **127 (this project at that computation)** | **4.00** |

Credible institutional trend-following reports **0.4–0.8** — the range
CLAUDE.md uses throughout as its reference for a real edge, not a figure
derived here.

The project-level count has since moved past 127 (CLAUDE.md records 129
after Trade Management Task C, of which one is a test artifact). This
document says "~130" elsewhere for that reason; the table keeps 127
because that is the `N` the figures were actually computed at.

**An agent that autonomously generates and tests hypotheses spends the
only resource that makes a conclusion possible.** It does not get better
with more compute; it gets strictly worse, because `N` only rises. A
factory that "researches continuously" would, within weeks, place every
future result beyond the reach of its own significance bar.

### The three ways to get evidence without spending N, all scarce

1. **Data nobody has searched** — `N` starts at 1. This project has one
   such window left (Binance spot 1m) and it is single-use.
2. **A specification from outside the search** — Task D's entry came from
   published literature, so the 129 prior trials had not searched it.
   Scarce because credible outside specifications are finite, and an
   agent generating its own is by definition searching.
3. **A genuinely different question** — Task D varied *management* where
   every prior trial varied *entry formula*. Real, and also finite.

**None of the three is renewable by compute.** That is the crux: the
research half's bottleneck is independent evidence, which is an
*acquisition* problem, not an agent problem.

### The corollary, which is counterintuitive

**The research half should be the part that is automated least.** Its
value per run is high and its cost per run is permanent. A factory should
make research *rarer and better-registered*, not more frequent.

---

## 4. What the "decades" framing needs that does not exist

The operator named two long-horizon risks specifically. Measured against
what the repository actually contains:

| | status |
|---|---|
| **Regime change, measured retrospectively** | **Exists.** Task D reports per-year; it found 2020 supplied +73.5R of +79.2R, and 4 of 8 years positive. `research/strategies/regime_classifier.py` exists from the scalping arc. |
| **Regime change, detected live** | **Nothing.** `RegimeClassifier` is referenced only from `python/research/` and its own test; nothing under `python/live/` imports or calls it. |
| **Edge decay / live-versus-backtest divergence** | **Nothing.** `live/health_check.py` checks loop liveness, tick freshness, kill switch, daily reports, signal freshness, disk and deployment staleness. **None of them compares realised performance to what the backtest predicted.** |
| **Competition from other bots** | **Nothing, and not obviously measurable directly.** |

The third row is the honest proxy for the fourth: an edge being
arbitraged away by other participants *looks like* realised performance
decaying relative to backtest. That is measurable, and it is not measured.

**This is a concrete, buildable gap and it does not require any agent
autonomy at all.** It is the kind of thing the infrastructure half is
already demonstrated to be good at.

---

## 5. The shape this suggests

Two halves, opposite policies:

**An infrastructure agent that runs often.** Verification external by
construction — tests, real deployments, venue responses, review. Mistakes
recoverable.

**"Runs often" is not "may do anything", and the boundary has to be
written rather than assumed.** CLAUDE.md already restricts live-trading
enablement, Risk Gateway bypass, risk or leverage relaxation, credential
handling, and any live-affecting promotion — all of which require a human
regardless of how well the agent is doing. Raised by CodeRabbit on PR
#162, and it is the right correction: the earlier wording said this
"needs no new permission", which reads as though the existing rules
stretch to cover unattended operation. They do not; they were written for
a supervised agent.

So the infrastructure half's authority is an **allowlist of reversible
work**: writing and running tests, building and merging non-CODEOWNERS
changes that pass their gates, producing documents and measurements, and
deploying code whose restart it has verified.

And it needs a stated **stop condition**, because the external check it
depends on is not always available — on 2026-09-11 review was
rate-limited repeatedly and once went silent entirely. When external
verification is unavailable the agent must **not**:

- deploy, or restart a venue-connected loop;
- reset a kill switch, or change any trading state;
- promote anything to paper or live;
- take any action it cannot reverse.

It may continue to prepare work, and must leave it unmerged. That is the
same **halt** posture every other safety property here takes, applied to
the agent itself — and §7.3 is where its cost is acknowledged rather than
hidden.

**A research process that runs rarely, and is made *harder* to invoke,
not easier.** Pre-registration before data access, `N` accounted
honestly, holdout access single-use, the three human checkpoints intact.
An agent's contribution here is in preparation and execution — building
the harness, running the registered test, reporting honestly — **not in
deciding what to test.**

The connective tissue between them is the thing CLAUDE.md already calls
scarred checks: 16 in `conclusion_check.py` and `change_check.py`
combined, plus four test files that exist solely to guard a past
incident, out of 2,177 tests. Each carries the real incident that
motivates it, which is what stops a checklist becoming theatre.

**That substrate is the factory.** Not an orchestration framework — a
growing body of checks, each bought with a real mistake, that an agent
cannot talk its way past.

---

## 6. What would be worth building first

Deliberately ordered by "produces evidence about whether the rest is
worth doing", not by ambition.

1. **Live-versus-backtest divergence tracking.** The edge-decay proxy
   above. Needs no autonomy, closes a real blind spot, and is the only
   thing here that speaks to the bot-competition worry.
2. **An external check that survives review being unavailable.** On
   2026-09-11 the single external check that caught 34 defects was
   rate-limited repeatedly and once went silent. A factory depending on
   it has an unexamined single point of failure.
3. **`N` accounting an agent cannot circumvent.** Today `N` is computed
   from a log the agent also writes. That is fine under supervision and
   not fine unattended.

**What is explicitly not worth building first**: anything that lets an
agent choose its own research direction. Section 3 is the reason.

---

## 7. Open questions for the operator

1. **Is the goal continuous operation, or unattended operation?** They
   are different. §2 supports the first today. The second needs §6.2
   answered first.
2. **How much is the research half actually the point?** If the appeal is
   "the agent finds strategies", §3 is bad news and should be confronted
   before anything is built. If the appeal is "the system keeps itself
   alive and honest for years", §2 and §6 are largely achievable now.
3. **What is the acceptable failure mode?** A factory that halts when
   uncertain is very different from one that continues. Every safety
   property in this project so far has chosen halt, and that choice
   composes badly with unattended multi-year operation — something has to
   restart it.
