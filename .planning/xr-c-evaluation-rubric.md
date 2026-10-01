# External Review — how the Phase 1 reply gets judged, fixed before it exists

**Why this document is written now.** This project's own holdout rule says that
criteria chosen after seeing the data are not criteria. The same applies here: a
reply read without pre-committed standards gets judged "plausible", which is not
a judgement. So the four questions below are fixed before Phase 1 is sent, and
the answers are recorded against them verbatim.

**Committed 2026-10-01, before any reply was requested.**

---

## The four questions, and what each one tests

### Q1 — Does it independently reach the power problem?

The fact package gives the detection-floor formula and every window's floor. It
does **not** say whether those floors are high or low relative to an edge worth
having, and it does not perform the arithmetic that turns the selection-trial
count into a required Sharpe.

**Scored:** does the reply compute either of those, or otherwise identify that
the measurement instrument's resolution — not the strategies — is a binding
constraint?

| outcome | reading |
|---|---|
| derives it unprompted, with numbers | **strong independent confirmation** of our own diagnosis |
| notes power as a concern without quantifying | partial — the direction is right, the force is not |
| treats every window as equally usable | the reply is working from a weaker model of the problem than the data supports, and its stage ordering is probably wrong downstream |

This is the single most informative question, because we already believe the
answer and the belief is currently self-confirmed.

### Q2 — Does it search an axis other than the entry formula?

**Scored:** does the architecture contain a stage that varies something other
than "what predicts direction"? Candidate axes it could reach: which situations
or names to be present for, position management after entry, sizing, portfolio
construction, execution, horizon selection.

A design that is entirely about generating and filtering direction-predicting
formulas scores zero here regardless of how sophisticated its filtering is.

### Q3 — Does it propose something we have not tried?

**Scored:** count the distinct, concrete, runnable proposals that do not appear
anywhere in this project's record. "Runnable" means it names data we have (or can
collect), produces a measurable quantity, and could be registered under §5's
rules.

This is the output with the highest expected value, and it is the one a blind
design is most likely to produce — our own search history is the thing most
likely to have narrowed our imagination.

### Q4 — Does it propose something we abandoned, and was that abandonment sound?

**Scored:** list every proposal that matches a direction this project has already
closed. For each, classify our original closure:

- **closed by measurement** — we ran it and it failed. The proposal is answered,
  and Phase 2 will say so.
- **closed by inference** — we reasoned our way out without measuring. **These are
  the ones to reopen**, and this question exists to find them.
- **closed by scope** — ruled out by a non-goal or a cost, not by evidence.

A reply that lands on a by-inference closure is doing something our own review
cannot do for itself.

---

## Two things that explicitly do not count

**Eloquence about overfitting.** Any competent reviewer will warn about
data snooping, multiple testing and regime dependence. The fact package already
states those rules, so restating them is not a finding and must not be scored as
one. What counts is whether the *architecture* changes in response to them.

**Proposals that cannot be registered.** A stage whose output cannot be judged
under §5 — no measurable quantity, no stated effect size, no refutation
condition — is not a usable contribution however interesting it sounds. Note it,
do not score it.

---

## What we will do with the result, decided in advance

- **Q1 strong + Q2 non-zero**: the design is taken seriously as a whole and
  compared stage-by-stage against our current plan in Phase 3.
- **Q1 weak**: the reply is still read for Q3 and Q4, but its stage *ordering* is
  not treated as evidence, because an ordering derived without the power
  constraint is optimising the wrong thing.
- **Q3 non-empty**: each new proposal is written up as a candidate for
  pre-registration, independently of whether the surrounding architecture is
  adopted. These survive even if the design as a whole is rejected.
- **Q4 finds a by-inference closure**: that closure is reopened and re-decided on
  its own terms, which is an operator decision and not something the reply
  settles.

**None of the above authorises spending a holdout.** Every path here ends at a
specification that would need its own pre-registration and its own operator
approval, per the human checkpoint on holdout access.

---

## The known limitation of this whole exercise

The reviewer cannot execute anything against this project's data. Everything it
produces is a design or a hypothesis, never a validated result, and the entire
burden of confirmation stays on this side. A reply that reads as authoritative is
still a reply that has run nothing.

A second limitation is sharper and worth naming: a language model can generate
plausible-sounding strategy ideas without limit. That abundance is not the
scarce resource here and must not be mistaken for progress. The scarce resource
is **windows that can still be selected on**, and the only proposals that matter
are the ones that can be tested within what §5 leaves available.
