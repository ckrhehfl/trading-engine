# ES chronological profitable-fill ranking

2026-11-19. The operator approved a local comparison after the completed ER
calibration study. The comparison asks whether a directly learned profitable-fill
indicator can rank opportunities in the same situation on the same trading day
better than the original EP direct value and the situation's historical success
rate. This is repeated Discovery. Ranking is the comparison result.

## Fixed targets, models and training support

- Use the original authenticated nine EL recovery shards, 257-session calendar,
  common chart/peer support and four chronological 40-session EP outer folds.
  Their prior training prefixes end at indices 97, 137, 177 and 217.
- Keep the original immediate and committed three-minute-wait action price rules,
  requested-notional definition, integer size, fixed structural/time exit and
  recorded cost. No regeneration of prices or execution. Given original positive
  notional, the sign of the stored exact value equals the sign of net cash.
- The positive binary target is a known modeled fill and strictly positive exact
  cost-net cash. A known no-fill/no-entry with verified zero cash, a filled exact
  zero and a filled loss are distinct known non-success categories. An unknown
  fill or unknown cash remains unknown, including outside the common pair support.
- Learn on the original J: both action targets and fill states known and mature,
  before the first evaluation session. Retain all original families and held
  queries and the same path, fill and held-return auxiliary targets. The two new
  binary responses replace the two direct value responses inside J. Read only
  the causal training-label prefix. Preserve independent original support counts.
- Use the unchanged EP training code and six-head TCN feature/preset/runtime for
  one direct fit per fold. Squared error on the binary response estimates the
  indicator's conditional mean under that loss. The model's raw continuous score
  is used for ranking, even when outside [0,1]. No clipping, class reweighting,
  calibration, probability threshold, hyperparameter search or native package
  changes. Other heads, input/draw geometry and original preset stay fixed.
- Estimate the training-only historical success rate separately for each of the
  four original C phases and both actions. Compute the EP date/cluster/episode/
  frame weights on full J, then condition on the phase. Preserve original onset
  precedence, use exact arithmetic and keep absent phase weight unknown. No
  outcome-based exclusions, fallbacks or new support/precision gates.

## Same-day comparison

Use original C episode/phase representatives, first causally admissible,
positive-denominator, common-context query in each phase. Seal their identities,
the historical rate, native artifact and all three score streams before safely
decoding current evaluation labels. The admission of stored EP predictions uses
its authentic outcome-free receipts and unchanged original fold support. Each
fold compares new raw success scores, stored EP direct net-value scores and
historical success rates. A past rate is constant within a phase and therefore
ties within that control group.

The matched sample requires both original action outcomes and both scores from
each of the three arms; disclose own and common support separately. Compare one
success and one known non-success of different symbol/session clusters, in the
same date, phase and action. A higher score for the success receives credit 1 and
a tie receives 1/2. Balance row weights within each date/phase by cluster then
episode. Divide weighted credit by weighted eligible pair mass for each date,
then average eligible dates equally for each phase/fold/action. Retain all dates
and report days without eligible pairs, class counts and exact pair counts.
The all-period action headline first averages eligible phases within each date,
then averages eligible dates equally. This prevents active dates and phases with
more stock pairs dominating the result. Show all 32 cells. Five-consecutive-date
omission ranges describe sensitivity. They are not significance intervals.

The comparisons use scores at their actual causal query clocks, but an entire
date's hindsight pair list is a measurement device. Do not apply the former
positive-value commitment rule to the new indicator scale or claim a trade
frequency, deployable timing or economic policy based on retrospective ranks.
Report modeled observed outcomes and known positive frequency as evidence scope.

## Source and execution completion

Carry the existing EM/EJ resource envelope and bounded same-input technical
recovery. Use one isolated fit and report per fold, preserving durable starts and
all failed attempts. Admit private receipts after the start and before any row
content. Verify original sources, pinned predecessor and original data audits.
Independent audit reconstructs training labels/rates, fixed causal representatives,
known cash signs and same-date pair credits. Replay native inference from saved
weights and check original training normalization, weighting and draw identity.
Independent verification reuses audited underlying label-to-cash semantics and
does not claim to reimplement the optimizer. A four-fold summary binds all checks.

Use invented regression tests, distinct final source/integrity reviewers, four
required CI checks and normal PR merge. Qualify the released source on the largest
invented fold with the native fit, full forecast/label join and independent audit
before actual reads. Standing approval covers reviewed source/docs/invented tests
on `codex/es-*`. Keep archived originals, protected untracked files, fitted
artifacts, local results and execution pins private. No GCP, new data, observer or
orders, new market cost assumptions, holdout access, risk increase or promotion.
Report the finite comparison, support/precision and next evidence-led hypothesis.
