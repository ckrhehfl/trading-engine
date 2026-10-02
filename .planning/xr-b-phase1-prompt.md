# External Review Phase 1 — the blind request for a search-system architecture

**How to use this.** Paste the section below the rule, followed by the entire
contents of `xr-a-external-review-fact-package.md`, into a fresh conversation
with the external reviewer. Nothing else — no summary of what we tried, no hint
of what we believe. The reply is then evaluated against `xr-c`, which was
written before the reply existed.

---

You are reviewing a real, working automated trading system built by a single
operator over several months. The document that follows this prompt describes its
resources, its measured facts about the markets and venues it touches, and the
evaluation rules it binds itself to. Read it fully before answering.

**Your task: design the system by which this project should search for a
tradeable strategy.**

Not a strategy. A *search procedure* — how candidates get generated, in what
order they get tested, against which data, with what stopping rules, and how a
result is judged. If your design happens to imply specific strategies, that is
fine, but the strategies are not the deliverable and a list of trading ideas is
not an answer to this question.

### What the answer has to contain

1. **The architecture.** The stages of the search, what each one consumes, what
   it produces, and what moves a candidate from one stage to the next. Be
   concrete enough that someone could implement it without asking you what you
   meant.

2. **Why that order.** For each stage, why it comes where it does. A design whose
   stages could be freely reordered has not made a real claim.

3. **How the search accounts for itself.** Read §5 carefully on this point: every
   look at the data raises the count a later result must be deflated against, and
   a holdout can be accessed exactly once, ever. A procedure that ignores this
   cannot be run here. Say explicitly how your design spends and conserves that
   budget, and what it does when the budget and the question conflict.

4. **What the design assumes, and how it would be caught being wrong.** For each
   load-bearing assumption, name the measurement that would refute it and say
   what you would do with the refutation. An assumption with no refutation
   condition is a belief, and this project's rules treat those differently.

5. **What has to be built first.** If a stage needs infrastructure the document
   says does not exist, name it, say what it must do, and say why the stage
   cannot proceed without it. Do not design around a missing piece by pretending
   a weaker substitute is equivalent.

6. **What you would do first, and what you would deliberately not do.** The
   second half matters as much as the first: name the attractive-looking moves
   your own design rules out, and why.

### Constraints that are not negotiable

- The permanent non-goals in §1. A design requiring tick data, co-location, or
  sub-minute bars is out of scope, not a suggestion to reconsider.
- The costs in §3 and the market facts in §4. These were each measured, usually
  after a real error. Do not assume a figure more favourable than the one given.
- The evaluation rules in §5 in full, including walk-forward, the Eligibility
  Bar, holdout single-access, and the statistical rules about independence.
- Infrastructure that does not exist may be assumed buildable, provided you say
  what it needs and why. Data that does not exist may not be assumed collectable
  — several series in §2 are documented as impossible to backfill.

### How to handle what the document does not tell you

The document deliberately withholds this project's own strategy attempts, their
outcomes, and its diagnosis of them. **Do not ask for them and do not try to
infer them** — that comes later, and your design is more useful to us if it was
reached without them.

If a fact you need is genuinely absent rather than withheld, say so and state
the assumption you are proceeding under, clearly enough that we can tell you
whether it holds. Do not silently fill a gap.

### Form

Prose and tables. No code. Length as the content requires — a thorough answer is
expected, but padding is worse than brevity. Where you give a number, say where
it came from: the document, a standard result, or your own estimate.
