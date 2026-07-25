# Planning Artifacts

Durable record of GSD `Discuss`/`Plan` output — see CLAUDE.md's
"Development Methodology". One file per Implementation Priority (or other
non-trivial change), added in the same PR as the work it plans — that's
the rule for every *new* plan from `07` onward. `00`–`06` are the one
deliberate exception: retrospective backfill, added after the fact
because no real-time version of them was ever committed. See below.

Why this exists: earlier work in this project (governance setup and
Implementation Priorities #1–#6) used Claude Code's own session-local plan
file instead of a repo-committed artifact. That file gets overwritten by
each new plan, so those original planning documents no longer exist in
their original form. `00`–`06` below are lightweight retrospective
summaries reconstructed from commit messages and PR descriptions, not the
original planning documents — good enough to orient a future reader, not
a faithful reproduction. Everything from `07` onward is written live,
during planning, before the corresponding code exists.

## Where does a design belong: CLAUDE.md, or here?

Decided 2026-07-25, after CLAUDE.md's Priority #10 entry grew a large
inline design (`VerifiedRiskDecision`) and it wasn't obvious in the
moment whether that was the right place for it. The rule, now explicit
rather than judgment-called case by case:

- **A file here can only be created for work that has actually
  happened** (or been genuinely investigated, like `09`) — "same PR as
  the work" is the rule above, and a not-yet-started priority has no PR
  to attach a planning doc to yet.
- So a **detailed design for work that hasn't started** has nowhere to
  live but **CLAUDE.md itself**, in full — not a summary, the actual
  design, since CLAUDE.md is the one artifact guaranteed to survive to
  whenever that work actually starts (a future session, possibly with
  no memory of how the design was derived, has only this file to go
  on — see CLAUDE.md's Development Methodology section).
- **Once that work actually starts**, the new `.planning/NN-*.md` file
  for it should reference CLAUDE.md's original design and record
  whether implementation followed it exactly or deviated (and why). At
  that point — and not before — CLAUDE.md's own entry can be safely
  trimmed to a short summary plus a pointer to the `.planning/` file,
  since the full design is now durably preserved there instead.

This is what already happened correctly for the `VerifiedRiskDecision`
design (full detail in CLAUDE.md's Priority #10 entry, since Priority
#10 hasn't started) — this section just makes the reasoning explicit so
it doesn't need to be re-derived, or second-guessed as maybe-misplaced,
next time the same shape of question comes up.
