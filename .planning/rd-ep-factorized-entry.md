# EP matched direct and action-factorized entry values

2026-11-18. The operator registered this local successor after EO's actual
saved-ranking audit and independent verification. Source investigation found
that original EL labels retain action-specific fill, value, status and maturity
although the learner exposes only immediate fill as an auxiliary output.
This is an outcome-informed Discovery comparison, not confirmation or promotion.

## One hypothesis and fixed comparison

At the same causal chart/peer inputs, does explicitly separating execution
probability and conditional net payoff improve expected-value calibration,
within-context ranking and the original first-positive policy over direct net
regression? For action a, retain Y_a = original net cash / original requested
guard-cap notional D0. Known no-entry/no-fill has F_a=0 and Y_a=0; a filled
position may also have Y_a=0. Unknown fill/cash is not either of those cases.
The factorization is E[Y_a|X] = P(F_a=1|X) E[Y_a|F_a=1,X] under that zero contract.
This identity motivates an estimator; it does not demonstrate that zero mixing
caused the old errors or that factorization adds predictive information.

The existing saved model estimates expected value, not probability of profit.
Positive-versus-nonpositive AUC is an additional diagnostic, not the training
objective or proof of expected profit. Do not invert a score, optimize a trading
threshold, pick the best period, or claim that higher AUC is higher net return.

Use two matched new arms, direct and factorized, at each of the original four
expanding folds. At most eight new fitted artifacts, one seed and one fixed
configuration. Keep the previously saved five-head baseline intact as historical
evidence; do not confuse it with the new matched direct control.

Both new arms use the same six-head network: original path, immediate fill,
immediate value, wait3 value, held remaining-gross, plus wait3 fill. Adding the
same auxiliary output to both avoids attributing its supervision to factorization.
Only the value-loss support/normalization and final value composition differ.
No extra classification head for positive profit is fitted in this comparison.

## Same inputs, chronology and training population

Use the authenticated nine EL recovery shards and original 257-session calendar.
The original first 97 dates are initially training-only, with expanding stops
97, 137, 177 and 217 and forty evaluation dates per fold. No new collection,
reserved data or post-discovery dates. Common chart/peer support is determined
from causal queries, never full-session completeness or outcomes.

Preserve all original families and held queries in shared training. C first
pullback is the evaluation focus, not a new C-only training restriction. Keep
original features, family/state conditioning, target maturity and held/path
auxiliary targets. Decode training targets only for the causal query-derived
prefix; do not decode the first evaluation target to discover the boundary.

For each entry query require the original admissibility, positive D0 and target
semantics. Joint training cohort J has both original net targets and both
action fills known and mature before the outer evaluation date. Report original
head support, additional missing fill loss, and joint support. A received wait
invalidation or late delayed expiry can be a known wait no-entry; an initially
inadmissible query remains outside entry learning. Fill-known/cash-unknown and
undefined D0 remain explicit. Check saved label ratios and status/maturity
without regenerating market outcomes or borrowing immediate fill for wait3.

Compute original date -> symbol/session cluster -> original episode -> frame
weights w on J once. Both fill losses and direct value losses use J and w.
Conditional value losses use w F_a / sum(w F_a); do not recompute hierarchy
weights on the filled subset. Keep filled exact-zero payoffs in that subset.
All inference uses the entire original causal evaluation cohort without an
actual-fill or outcome-availability filter. Unsupported components stay unknown.

## Matched optimization and bounded support

Use the original causal TCN encoder and feature transform, and the same six-head
initialization order for both arms. Keep width16, kernel3, dilations1/2/4/8/16/32,
200 updates, batch64, Adam .001, seed20261111, float32, one deterministic CPU
thread, gradient-norm clip1, and the admitted CPU Torch runtime. No native install
or GPU use. Training-only feature normalization is identical across arms.
Value target normalization uses the corresponding direct or conditional training
weights and is recorded; it is part of the changed estimation method.

The common sampling proposal is the mixture of the six unmasked base cohorts,
independent of conditional masks and whether a head is constant/unsupported.
Keep the same sampled indices and initialization digest. Apply conditional
importance corrections in losses only. Use the same six-slot loss denominator.
Preserve per-head original/joint/filled support, weighted fill mass, constants,
unsupported heads, actual fit attempts, native updates and training diagnostics.
Small support is a limitation, not a recurring approval request. No known
conditional support does not authorize inventing a conditional payoff.

Unreduced binary logit loss permits explicit row weighting as documented in
the [PyTorch 2.7 reference](https://docs.pytorch.org/docs/2.7/generated/torch.nn.BCEWithLogitsLoss.html).
No class-balancing positive weight is introduced: it would change the intended
fill-probability target. Squared error remains the value loss. Optimizer source
checks and invented behavior tests are separate from actual inference replay;
do not claim an independently reimplemented native optimizer.

## Causal selection, scores and fixed policy

Seal both fitted artifacts, all predictions/components and original C causal
policy choices before parsing evaluation outcomes. Predicted values passed to
the unchanged policy have the original five-output interface; factorized values
are explicit same-action probability times conditional payoff. Never multiply
an existing unconditional direct value by fill probability a second time.

For descriptive phase comparisons, retain each episode at onset, waiting for
pullback, pullback and confirmation. Choose the earliest causal admissible,
positive-D0, chart/peer-supported query in each phase before reading targets.
Selection does not depend on either new model's score sign or availability;
missing model outputs remain missing. This differs explicitly from EO's finite
saved-head eligibility and is not a claim of identical EO selected support.
Retain absent/unsupported phase records for the original episodes.

Primary paired metrics use both action targets and both arms' two values known
on these fixed representatives; also retain each arm/head support. Within each
fold/phase report predicted/actual mean, signed error, MAE/RMSE, zero-reference
error, net-positive AUC and continuous ordering, plus same-date ranking. Use
date/cluster/episode weights, exclude same-cluster ranking pairs and preserve
ties. Reuse the verified EO producer/independent pair algorithms where their
contract fits. Five-date deletion ranges are sensitivity, not confidence intervals.
Separate fill-probability scores from value scores; never call value a probability.

Keep one original first-positive commitment per episode: both values required,
strictly positive maximum, immediate on a tie, otherwise the original wait3
commitment. Preserve structural/late/context abstention and no repeated wait.
Use original capped next-opening entry and fixed exit, unchanged integer sizing
and costs. The saved audited action CashTargets already define these exact
outcomes; joining them avoids a new market-price/execution experiment. Report
known modeled fills, unknowns, exact nominal net cash, original-request value,
all original calendar weeks including zeros, and paired known differences.
No filled-notional return or gross cash is invented from missing fields. These
are nominal per-event books, not a simultaneous funded account or actual fills.

## Release, execution and independent verification

Create isolated EP per-fold publications and a four-fold summary; preserve all
EL/EN/EO publications. Each fold contains both registered arms with separate
durable fit starts and shared unchanged resource limits. Keep CLAUDE.md's local
EM/EJ envelope and bounded same-input technical recovery. No resource increase.

Reuse authenticated original invented shards, then qualify the largest fold
through both complete fits, seals, statistics, publication and independent audit.
This admits smaller folds only under unchanged source/runtime/input contracts.
Public Git checks precede the no-new-process restriction. Each actual start
precedes private input receipt/content admission; evaluation labels follow
both-arm prediction/policy seals. No arbitrary path or hyperparameter override.

Relevant invented tests must exercise missing/zero/status/maturity, action
misalignment, conditional weights, identical proposals, output composition,
chronology, target-free inference, policy tie/one-commit behavior, exact target
cash, zero-calendar weeks and tamper rejection. Separate actual audits replay
stored inference without refitting, independently reconstruct action labels,
support/weights/composition, causal policy choices, saved cash joins and report
statistics. Existing dataset/execution audits cover unchanged underlying price
and target reconstruction; state that reuse accurately.

Distinct final source and research-integrity reviewers, four required CI checks
and normal merge precede actual reads. Public changes contain only reviewed code,
this registration and invented tests. Private source/input execution pins,
fitted artifacts, results and credentials remain local. Completion requires
authenticated actual publications and audits, not source presence. No GCP,
observer activation, order path or promotion. Protect both untracked originals.

## Finite conclusion

Report all four periods and four phases, including unfavorable/unsupported
cells. Distinguish source gaps, training support, prediction availability,
ranking, calibration and policy cash. A positive reused-period subgroup cannot
be promoted. If factorization fails to improve, report that result without
declaring the chart-information direction closed. Choose one subsequent
information/target/learning hypothesis from the evidence; no second sweep is
automatically appended to this finite EP comparison.

## Pre-execution review clarification

The fitted estimand is the weighted joint-known training population:
E_w[Y_a | X, J=1] = P_w(F_a=1 | X, J=1)
times E_w[Y_a | F_a=1, X, J=1]. J requires both action cash values and fills
known, so it can exclude a known fill whose exit cash is missing. Matched J
controls the comparison between arms; it does not remove missingness selection.
Interpretation over the entire causal evaluation population requires an
unverified missingness/transport assumption. Record each action's fill-known,
cash-unknown and joint-excluded counts; retain all evaluation unknowns.

After two consecutive technical timeouts, the same implementation cannot run
a third identical attempt. Preserve both failures and use a tested, reviewed
implementation correction or smaller equivalent diagnostic under the same
resource and scientific contracts, as required by the inherited EJ rule.
