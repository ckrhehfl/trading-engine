# EN pre-entry value errors and policy-target reconciliation

2026-11-16. The operator registered a new local Goal after the completed EM
diagnostic and two holding comparisons. Source investigation found an important
contract distinction: EL's entry values predict capped-next-opening entry with
fixed structural/time liquidation, while the recent reference-limit/adaptive
exit result is a different policy. This is an identified definition difference,
not evidence yet that it caused the negative economic result. Fix the following
audit before opening its original prediction/target/cash joins.

## Population and finite scope

Keep C_FIRSTPULLBACK, TCN/common_chart_peer and the four original forty-session
evaluation folds. This is the same outcome-informed Discovery scope used by EM,
not a fresh model contest or confirmation sample. The first ninety-seven input
dates remain training-only. Keep the original 160 evaluation dates, every C
onset episode, all causal entry query wrappers, absent chart/context/prediction
support and zero-trade weeks. A held counterfactual query is not an entry query.
Do not select rows using confirmation, fill, positive prediction or known payoff.

Use authenticated original EL dataset and fold publications. Freeze causal
identity, states, scope support and stored predictions before reading labels or
cash. Read the existing comparison's fit diagnostics; do not load a native model,
refit, regenerate inference, build a new outcome or acquire market data. Existing
dataset and forecast/cash independent receipts establish those unchanged source
contracts; EN separately reconstructs joins, ratios, policy choices, accounting
differences and aggregate errors. Report that reuse rather than claiming a new
market-price or optimizer verification.

## Exact prediction question

Join query, prediction and label by key with session, symbol, original episode,
decision clock and fold checks. Use the entry query's training_labels semantics:
an inadmissible terminal state can have operational zero cash while its entry
value target is inapplicable. That zero must not become a calibration target.
Missing, undefined-zero-denominator and immature targets remain explicit.

The two entry heads share the ORIGINAL requested quantity times original guard
cap as denominator. It is not filled notional, delayed request notional or NAV.
Wait3 means one commitment to wait three minutes; reference/quantity and expiry
can change, but that first denominator remains fixed. It is not the value of
continuing to observe indefinitely. Reconstruct each ratio from the saved exact
net cash and denominator and check its admissibility/maturity against the saved
training label. Statistical predicted numbers retain their finite numeric
boundary; cash, sizes and cash decompositions use exact arithmetic.

For immediate and wait3 report support, prediction/observed mean, signed error
(prediction minus observation), MAE, RMSE, positive predicted value versus known
positive/zero/negative outcome, and the same error of a fixed zero-value baseline.
The zero baseline is a scoring reference, not an assertion that waiting is worth
zero. Separately report the joint-known paired head difference: predicted wait
advantage versus observed wait advantage, including direction agreement and ties.
No best threshold is selected from these tables.

## Situation, period and weighting

Fix these reporting strata now: whole sample, each original fold, causal phase,
fold by phase, and decision clock below sixty minutes / sixty to below one
hundred eighty / at least one hundred eighty minutes after original opening.
Onset means the query decision clock equals the original episode's onset clock;
otherwise distinguish waiting-pullback, pullback, confirmation and invalid/terminal
states. First retained query is not automatically onset after coalescing.

Within each head/group, preserve all supported/unsupported/unknown counts and
recompute date -> symbol/session cluster -> original episode -> frame equal
weights on the scored rows. Show counts of frames, episodes, clusters and dates.
Neither repeated frames nor simultaneous names are independent observations.
No unclustered significance test or strategy-promotion threshold follows.

Additionally show fixed prediction bins below -33bp, [-33bp,0), exactly zero,
(0,33bp], and above 33bp on the original requested-notional value scale. Those
are descriptive bins, not the realized filled-return cost threshold. Report
selected first-commit queries separately from all supported entry queries.
Pairwise head differences require both predictions and both actual targets;
do not silently use different missingness subsets to compare heads.

## One policy choice per original episode

Reconstruct the existing causal selection from stored predictions and query
admission. Both heads are required. The first eligible frame with positive
maximum commits once; choose immediate on ties, otherwise the larger head.
Unknown chart/peer/heads abstain, terminal and late-session admission rules retain
priority. Do not use future labels to alter the selected key. Cross-check the
archived model policy trace and every selected ledger candidate/action.

At the same selected key keep these three original policies:

1. model + capped next opening + fixed exit (the head's target);
2. model + reference limit + fixed exit;
3. model + reference limit + forecast-full exit.

Retain confirmation + reference limit + forecast-full as a separate historical
control on the whole episode roster, without calling its difference a pure
model-selection effect. It can enter at a different time and size.

Apply archived head availability to the raw fallback ledgers. Unsupported model
policies stay unavailable, and immature outcome fields override raw execution
cash. Reconstruct known ledger cash and costs from saved purchase/sale legs.
Check selected capped/fixed target cash, initial denominator and known value
against the selected head's saved target; retain mismatched support separately
where original semantics genuinely differ rather than coercing unknown to zero.

On common known selected rows with positive denominator D and prediction q,
verify exactly:

`reference/full net - D*q = (cap/fixed net - D*q)
 + (reference/fixed net - cap/fixed net)
 + (reference/full net - reference/fixed net)`.

This is an accounting decomposition under frozen models, not identification of
market causation or queue adverse selection. Preserve the original opportunities,
known no-entry/no-fill, unknown cash/fill and unavailable policy counts in every
arm. Report gross/net cash, returns on settled filled notional, all-calendar
weekly fills, weeks with at least fifteen fills and date/name concentration.
These nominal episode books are not a simultaneous funded account.

## Learning evidence and next hypothesis

Copy only the authenticated original training diagnostics and head/fit statuses.
TCN normalized training loss is not return MSE; lower training loss alone does
not establish convergence or economic predictability. Compare saved initial/final
training diagnostics with chronological evaluation error and policy residuals.
Separate supported findings from causes that this saved record cannot isolate.
Select one next hypothesis from demonstrated evidence; no new fit, new trading
rule, parameter sweep or second economic comparison is executed in this Goal.

## Release and actual completion

Keep the existing local envelope and bounded technical recovery in CLAUDE.md.
One closed EN-C-VALUE-AUDIT-1 unit produces all four-fold summaries, followed by
a separate-process independent audit. New pure calculations need invented edge
cases and a bounded full-size invented join/aggregation qualification, audited
before original reads. Test terminal-zero exclusion, missing heads/context,
paired-known support, wait denominator, first-positive selection/ties, original
calendar zeros, immature cash, unsupported fallback policies, exact residual
identity and tampering/duplicate/missing-key failures.

Separate final-source and research-integrity reviews, all four required CI checks
and a normal merge precede actual execution. Durable starts, original source/input
identities, exclusive outputs and canonical completion/audit receipts remain
required. Actual status is read from authenticated native publications, never
inferred from module presence. No new source release merely to publish private
economic completion. Public GitHub changes contain reviewed source, documents
and invented tests only; private results, execution hashes/metadata, originals,
model artifacts and credentials stay local. No GCP, new acquisition, reserved
or forward data, orders, observer activation, resources/risk increase or promotion.

### Source-contract clarification before outcome joins

A selected wait can legitimately commit and then cancel before constructing an
entry request. Its ledger entry-decision offset can therefore be absent. Also
keep any saved target-versus-capped-ledger support or numeric disagreement as
an audit finding; do not rewrite historical outputs to make it disappear.
On jointly known rows, expand the first residual into saved-label error plus
capped-ledger-minus-saved-label cash. The original three-term identity remains
valid, but its first term is pure forecast error only when that contract gap
is zero. Otherwise report the four-term identity and the unresolved discrepancy.
This clarification changes no policy or payoff and precedes original EN reads.

The copied training diagnostics belong to the original shared-family fit, not
to a C-only learner. They cannot establish C-specific convergence or explain
which family dominated a joint training loss. No additional training-history
claims are inferred from a missing family-specific diagnostic.
