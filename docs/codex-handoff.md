# Claude to Codex handoff

Original handoff recorded on 2026-10-05 against HEAD `4ce85d7` (PR #221).
Current research scope and source status last updated on 2026-10-07, including
the operator's numeric-boundary decision and Task AR's liquidity-input work.
The later sections retain the original handoff context. These records are not
a new research conclusion or authorization.
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
new return comparison has run. Task AQ fixes the reproducible universe rule
before examining its outcome and implements the first formation-source audit.
Source coverage and dated classification still need verification. The activity
hypothesis, post-SPAC baseline decision, accounting obligations and research
gates remain.

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
the probe's structural checks. All trading rows across the three positive
dates joined unambiguously to basic information. Saturday trading is empty;
Saturday basic snapshots equal Friday's. These are source samples, not a
common-stock candidate pool or complete historical panel. AP contains private
evidence hashes and offline ledger/hash verification. Total AP requests are
21: four earlier 401s and seventeen new 200s.

GCP access with the existing key is now verified for all four services on the
fixed dates. Missing individual service permission is the likely explanation
of the earlier 401, not a proven internal rejection cause. A KRX rejection-log
inquiry is no longer a prerequisite; no inquiry was sent. The collector remains
unchanged at PR #221.

The subsequent offline audit verified every saved trading row's capitalization
arithmetic and matching basic-information share count. Current official units,
raw-price basis, publication timing and dated membership semantics are now in
`docs/exchange-api.md` section 6. These are sampled/documented facts, not full
historical certification. An existing same-date KIND comparison also proves
that absence from the API's SPAC section does not establish an operating
company; AP contains the complete discrepancy and private evidence hashes.
The audit made no new API requests.

The operator selected the numeric size/liquidity boundary recorded in Task AO's
latest decision. That choice is fixed before examining its candidates; it is
not permission to tune the cutoffs after seeing them. Task AO deliverable
**1 of 5 remains open on coverage and historical classification**; deliverable
2 now has its reproducible timing and missing-data specification in
`.planning/rd-aq-large-liquid-formation-acquisition.md`. AQ implements the
fixed formation-source request matrix derived from AP's calendar-only inventory.
PR #244 passed CI and CodeRabbit, was merged and deployed to the isolated GCP
research checkout. AQ then completed all 56 requests and all 14 formation-day
audits. The saved responses agree on membership, listed shares and capitalization
arithmetic; the result records aggregate size and common-label counts, not an
eligible universe. An independent offline pass verified hashes, the complete
request ledger, permissions and aggregate arithmetic. The collector stayed
unchanged. A preceding wrapper-format failure made no API request and remains
recorded separately in AQ.

Task AR (`.planning/rd-ar-large-liquid-liquidity-acquisition.md`) now registers
the fixed preceding-session liquidity acquisition and diagnostic. Implementation
and local regression verification are complete, reusing the pinned AQ responses
and AP calendar rather than changing the size boundary. PR #246 passed review
and CI, was merged and deployed to the isolated GCP research checkout. The
first run then stopped at a transport failure after 549 successful responses;
all saved responses and the ledger were verified offline. It published no
completed candidate artifact. AR records the immutable failed run and a bounded
recovery plan that reuses verified evidence. Recovery implementation, independent
review and full local regression verification are complete; CodeRabbit and merge
precede follow-up requests. No completed liquidity-coverage result is available
at this update.
After that diagnostic, finish positive dated operating-company evidence and
price-panel coverage.
These remain prerequisites for the new sizing runner. No new large/liquid
universe selection, portfolio or comparison run has occurred; readiness and
historical certification remain false. No new operator choice currently blocks
the next bounded data-validation work.

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
