# Research Direction Task AE — identify the eight residual holdings

## Decision and scope

On 2026-10-05 the operator selected option 1 from Task AD: investigate the
eight unresolved holdings and their data/recovery evidence first. This is not
the alternative approval to run a descriptive comparison despite low power.
The previous choice to carry holdings and defer unavailable sales still applies.

The initial pilot retained only an aggregate residual count. The diagnostic
replay adds terminal inventory observations to the same accounting engine;
these fields never affect selection, cash, fills or valuation. Its committed
specification is `configs/research/discovery/activity-holdings-audit-v1.json`.
All pilot parameters, its specification hash, its dataset hash and its full
accounting counts are pinned. A mismatch refuses the diagnostic result and
leaves a failed attempt in the append-only discovery log.

## Evidence protocol

First identify every residual's code, entry, scheduled sale, last observed bar,
last non-frozen mark and missing/frozen/pending position-sessions. A live name
still inside its intended holding period must not be called a failed exit.
The terminal state is only `missing`, `frozen` or `non_frozen`; these are data
observations, not legal-status classifications. Marks are split-adjusted and
must not be equated with raw recovery cash.

Then inspect these names' existing completed scan and identity snapshots and
official KRX/KIND, DART or issuer disclosures. Distinguish publication dates,
effective dates, suspension/resumption, delisting and corporate-action terms.
Check the share/entitlement basis before quoting a recovery amount. A final
bar, a delisting label or an announced tender price alone does not prove the
portfolio received cash. Unverified recoveries remain unknown. Evidence found
after the simulated date is retrospective diagnosis, never information supplied
back into the trading rule.

No comparison, parameter selection, performance verdict or data correction is
part of this replay. It does not solve Task AD's power limitation or authorize
holdout access. Any substantive change justified by the investigation needs a
new written protocol rather than silently rewriting the original pilot.

## Execution and verification

The replay is logged as a new discovery attempt before loading prices. It uses
the isolated GCP research checkout, the same read-only spent scan/calendar and
the host's discovery log. That log contains the new discovery attempts only;
the historical backtest/holdout log in the primary local checkout is retained
separately and must not be replaced or used interchangeably when calculating N.
Neither log, database nor credential files are copied to this worktree.

Synthetic tests distinguish frozen quotes from missing bars, preserve the
original accounting, refuse changed replay inputs and keep performance fields
out of the diagnostic output. Code and specification are reviewed before the
real replay. Results and documentary findings will be appended in a separate
task record; this document makes no claim that an investigation has run yet.

Before opening the PR, the focused portfolio/log/index suite passed 64 tests
with one existing skip. Repository guardrails and their 27 + 34 regression
checks passed. The full Python suite and required CI remain merge gates.
