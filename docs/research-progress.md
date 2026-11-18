# Research requirements and current evidence

Updated 2026-11-18. EQ is the current Goal. Permissions and numeric risk/resource
limits remain solely in CLAUDE.md; this ledger describes delivered evidence.

| Operator requirement | Completed evidence | Remaining work |
| --- | --- | --- |
| Event-first design without mandatory old strategy reuse | EL introduced separate scenario, feature, learner, execution and held-state modules | Same admitted data and operational assumptions still constrain interpretation |
| Three explicit chart situations | EL causal dataset production and all nine shard audits completed | Operational definitions do not cover every meaning of a trader's setup |
| Outcome-blind chart examples | Nine original examples selected by identity before outcome display | No claim that nine examples validate trader-style setup fidelity |
| Chart-only versus observed-peer context | All 36 registered EL learner/scope/fold units and independent audits completed | Reused discovery period, unsupported full-market channels |
| Reduction and full exit with matched entries | EL fixed/full/half policies replayed and audited | No reset/reentry comparison was executed |
| Multi-scenario decisions from entry through exit | Finite path probabilities, immediate/wait values and held-return predictions implemented | Full future-path distributions, target timing and unrestricted sequential action planning are absent |
| Order-free observation | Offline schema, stale-input abstention and invented messages verified | No real-time feed or observer activated |
| Actual portfolio execution | Integer quantities and nominal per-event cash reconstructed | Quotes, queue fills and simultaneous funded-account validation absent |
| Explain negative outcomes before adding conditions | EM original-roster/fill/path/exit diagnostic and independent audit completed after PR #462; two successor holding kernels and independent verifiers implemented | Successor execution state comes from the authenticated local publications below; implementation alone is not a result |
| Reconcile pre-entry forecasts with the exact target and different policy payoffs | EN saved-prediction, target and policy-cash audit completed independently after PR #464 | Average errors do not establish within-situation ranking or profitable selection |
| Rank net-positive opportunities in the same situation and period | EO sixteen-cell actual ranking and separate audit completed after PR #466 | Descriptive repeated-period ranking does not establish profitable selection |
| Separate execution probability from conditional payoff | EP final release qualification, eight actual fits, four independent audits and authenticated summary completed after PR #467 | These repeated-period results do not establish improvement over the training situation's base rate |
| Test added information beyond the training situation's mean | EQ mean control, three-arm reports, separate verifier and closed runner implemented; focused invented tests passed | Release qualification and four actual comparisons with independent audits remain pending |

EL source releases through PR #461 were normally merged after separate reviews
and the required checks. The completed model comparison release is PR #459;
the two subsequent C screens are PR #460 and PR #461. Completed computation is
not proof of a profitable strategy or a promotion decision. Private numerical
reports remain in the local evidence store and ignored `var/` report files.

The source coverage census measures absent original coordinates. Completion of
all nine computational shards does not mean those missing prices were restored.
All reported weekly trading frequencies use the original calendar and include
zero-fill weeks. Frequency refers to all fills, not only profitable fills.

The active plan is [EQ training-situation mean](../.planning/rd-eq-situation-mean.md).
It follows completed [EP matched entry values](../.planning/rd-ep-factorized-entry.md),
[EO within-context ranking](../.planning/rd-eo-within-context-ranking.md)
and [EN entry-value audit](../.planning/rd-en-entry-value-audit.md).
The completed [EM loss attribution](../.planning/rd-em-causal-attribution.md)
and [two holding comparisons](../.planning/rd-em-exit-horizon.md) motivate it.
This ledger must be updated with observed phase completion, and must not call
an implemented-but-unrun branch complete. The original append-only plans retain
their initial-state wording as dated history.

For execution status after the source release, consult the native evidence
store's `output/EM-C-HOLDING-FOLLOWUP-1/` publication. `start.json` records a
start, `completion.json` binds the production report and canonical journal,
and `audit.json` must authenticate that same report before calling either
successor complete. A missing receipt or a failure file does not count as a
completed result. Check hashes/source/input identity with the released runner;
file existence alone is insufficient. Private outcomes stay outside Git.
This prevents a pre-execution source snapshot from being mistaken for the
latest execution state or requiring a new source release merely to restate it.

For EN, use the same authenticated completion rule at native
`output/EN-C-VALUE-AUDIT-1/`: production, canonical journal and matching independent
audit must agree. Source implementation, an invocation or an unaudited report
does not count as completed EN findings. Private economics and execution
digests/metadata remain local. EN adds no refit or new strategy comparison.

EO uses the same authenticated completion rule at native
`output/EO-C-RANK-AUDIT-1-retry1/`. Its `representatives.jsonl` must be sealed before the
original EN measured stream is opened. A matching independent audit reconstructs
selection, exact targets and ranking; private execution pins and outputs remain
local. The date-block deletion range is a sensitivity diagnostic, not a confidence
interval. This finite audit adds no learner, trading threshold or policy return.
The original EO publication is preserved; the dedicated technical retry checks
its closed failure and fixes Git authentication before process creation is
disabled. Source completion and required CI do not themselves prove a completed
actual retry. Private execution details remain in the local evidence store.

EP uses `output/EP-C-FACTORIZED-1-f0-actual1/` through fold 3, with an explicit
`actual2` successor only after a supported, retained technical timeout. Check
each production completion and its matching independent audit before the next
fold. The fixed `output/EP-C-FACTORIZED-1-summary/` publication authenticates
all four audits, preserves all sixteen phase/fold cells and independently
checks the pooled calendar and policy cash. Native fit attempts, completed
fits and extra starts in retained failures are distinct counts. A source
release or an invented qualification alone is not an actual comparison.

EQ uses `output/EQ-C-PHASE-MEAN-1-f0-actual1/` through fold 3, with the same
bounded dedicated retry convention. Its mean artifact, forecasts, policies and
representatives are sealed before each target join. Each new completion and
independent audit must agree; the fixed summary authenticates all four new
audits and the predecessor EP summary. Actual counts and private economics are
reported from those receipts, never inferred from source implementation alone.
