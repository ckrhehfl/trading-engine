# Claude to Codex handoff

Recorded on 2026-10-05, against repository HEAD `4ce85d7` (PR #221).
This describes the handoff, not a new research conclusion or authorization.
Binding constraints remain in `CLAUDE.md`.

## Current research scope — operator decision 2026-10-07

The operator selected **large, highly liquid common stocks** for the next
activity study. Samsung Electronics and SK Hynix illustrate the intended
universe, not a fixed list backdated into the test. Read
`.planning/rd-ao-large-liquid-scope.md` for the decision, inspected source gaps,
and next completion conditions. Do not resume full-market SPAC classification
expansion from Task AN as the default next task.

The first activity pilot, its accounting diagnosis and Task AN's dated-source
check have been completed and preserved. No large/liquid candidate universe or
new return comparison has run. The immediate next step is to verify dated
size/type source meaning and coverage, then freeze a size-and-liquidity rule
before inspecting its candidate list or returns. The activity hypothesis,
post-SPAC baseline decision, accounting obligations and research gates remain.

Task AP (`.planning/rd-ap-krx-source-access.md`) records KRX key registration,
the GCP deployment split, official contracts and the bounded access probe.
Four initial requests returned HTTP 401, including one from local WSL; those
results remain preserved. The operator then corrected the earlier approval
report: key approval had been mistaken for individual service approval. After
applying for all four named services, the operator confirmed their approvals.
No new key was issued, and the previously registered key remains in use.

On 2026-10-07, under a scope persisted before execution, clean PR #241 source
ran one GCP check at **17:31:35 KST** and then the original 16-request matrix
at **17:31:57–17:32:40 KST**. All **17 requests returned HTTP 200** and passed
the probe's structural checks. The three trading dates contain 7,494 trading
rows, all joined unambiguously to basic information. Saturday trading is empty;
Saturday basic snapshots equal Friday's. These are source samples, not a
common-stock candidate pool or complete historical panel. AP contains private
evidence hashes and offline ledger/hash verification. Total AP requests are
21: four earlier 401s and seventeen new 200s.

GCP access with the existing key is now verified for all four services on the
fixed dates. Missing individual service permission is the likely explanation
of the earlier 401, not a proven internal rejection cause. A KRX rejection-log
inquiry is no longer a prerequisite; no inquiry was sent. The collector remains
unchanged at PR #221. Task AO deliverable **1 of 5 remains open on source
meaning and coverage**: units, publication timing, historical completeness,
point-in-time classification and revisions still need verification. Then fix
the dated size/liquidity rule before ranks or returns are inspected. No new
large/liquid universe selection, portfolio or comparison run has occurred.

Every operator report must distinguish the current work package's completion
from strategy validation and include the current stage/counts, next action,
and any blocker or decision. The sections below describe the original handoff.

## Where the previous work stopped

The local Claude trading-engine transcript ends after a request to continue
on 2026-10-03. The next assistant response reports Claude subscription access
disabled; no following research implementation is recorded there. The
transcript was inspected locally; raw conversations, credentials and tool
outputs are not copied into the public repository.

The last completed arc is documented in
`.planning/rd-aa-does-the-filter-condition-persist.md`. Earlier prerequisites
are `.planning/rd-z-can-the-reserved-window-answer-anything.md`,
`.planning/rd-y-the-full-universe-scan-result.md`, and the external review
record `.planning/xr-f-phase2-result.md`.

The activity-persistence measurement established that the selection condition
can persist across a longer holding period. It did **not** establish a return
advantage. Reusing it as evidence that a strategy makes money would change the
claim. The module is `python/research/turnover_persistence.py`.

## Next research work at the original handoff

The recorded next task is a discovery preregistration for a portfolio selected
by abnormal activity and held for months, compared with an appropriate
unfiltered control. It uses the already-spent post-2019 discovery data, under
the existing Discovery guards. Define the candidate family, controls, costs,
point-in-time universe, timing, logging and stopping rule before inspecting
returns; commit the specification before running the experiment.

Read the complete Strategy Research Methodology and the relevant research
documents before choosing those details. Frozen/halted bars and limit-locked
bars are not interchangeable; the standing distinction in `CLAUDE.md` and the
last measurement must carry into the specification. Discovery results cannot
be reported as a confirmation or used to promote a strategy.

## Collection and reserved data

The operator confirms GCP collection is running during this handoff. Local
development must not change that schedule, restart collectors, deploy code,
or start another writer. The pre-2019 backfill is reserved for the existing
confirmation process, not available for discovery. The earlier transcript's
completion estimate is historical, not a current completion measurement.

Data-quality prerequisites and the separate human holdout-access decision
still apply. Environment setup and regression tests do not authorize reading
reserved returns. The operational account/path and commands are documented
in `docs/paper-trading-runbook.md`; do not assume the SSH login owns the repo.

## Development handoff

Use `AGENTS.md` and `docs/codex-development.md` for Codex entry and local
verification. This migration changes development tooling only. It does not
change the exchange adapters, live/paper modes, risk limits, research registry,
spent-window ledger, experiment log or GCP deployment.
