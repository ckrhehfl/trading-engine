# Claude to Codex handoff

Original handoff recorded on 2026-10-05 against HEAD `4ce85d7` (PR #221).
Current research scope and source status last updated on 2026-10-09 KST,
including the operator's numeric-boundary decision, Task AR's completed
liquidity acquisition, Task AS's listing/classification-source evidence and
Task AT's date-only price-panel mapping, Task AU's bar-state audit and
Task AV's bounded historical company-overview acquisition and Task AW's
metadata reconciliation and issuer-content review, followed by Tasks AX-BA's
dated issue, notice and history-source links and bounded tender-content review.
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
check have been completed and preserved. No certified large/liquid eligible
universe or new return comparison has run. Task AQ fixed the reproducible
universe rule before examining its outcome and implemented the first formation-
source audit. Later audits below verify the bounded source/date/bar coverage;
historical classification and accounting remain uncertified. The activity
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
**1 of 5 remains open on historical classification**; deliverable
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
recovery plan that reuses verified evidence. PR #247 passed CodeRabbit and all
four CI workflows, was merged and deployed to the isolated research checkout.
Its bounded recovery completed at 01:12:54 KST on October 8: 1131 new successful
requests, no transport retries, and 549 revalidated cached responses complete
all 1680 logical responses across 840 dates. The preserved first failure makes
the combined wire-attempt count 1681. The collector stayed clean and unchanged.
An independent offline pass verified every raw hash, both ledgers, exact medians
and aggregate counts across 2107688 source rows.

The full 944 capitalization-pass issue-formation rows remain in the diagnostic.
Of the 926 common-label rows, 918 have complete histories: 879 pass the liquidity
screen and 39 fall below it. The original AR diagnostic retained eight
incomplete rows with unresolved liquidity status and 254 missing
issue-session values, all before the basic source's reported listing date.
That field does not itself verify initial-listing or conversion history.
The common-label subset spans 138 distinct issue codes; these row counts are
not counts of distinct stocks or proof of operating-company eligibility.
There are 878 common-label rows passing both liquidity and the separate formation
tradability proxy; they are not a completed eligible universe.

Task AS (`.planning/rd-as-large-liquid-listing-evidence.md`) resolves the cause
of all eight short histories using official exchange notices: seven new
listings and one split relisting. All 254 missing observations precede the
verified current-issue listing. They remain in the original population with
insufficient-history dispositions; no zero fill, shorter window or predecessor
history was substituted. Original AR results remain immutable.

AS also acquired and independently verified 28 positive KIND ST company tables
for all 14 formation dates, matching their company counts to source summaries.
The 926 common-label observations split into 917 ST-positive rows (136 codes)
and nine rows carrying explicit API investment-company/foreign-DR labels.
Of the 917, 909 have complete liquidity histories, 876 pass liquidity and 875
also pass the separate formation tradability proxy. These are diagnostic
partitions, not an eligible universe. All 944 original capitalization-pass
rows remain, including preferred-share controls. Historical queries display
some current market/listing-date fields, so those fields cannot be backdated.
Full classification intervals and their original publication times remain
uncertified; the separate notice publication timestamps do not certify them.

Task AT (`.planning/rd-at-large-liquid-panel-coverage.md`) now maps all 944
rows / 140 codes to the existing spent scan using dates and progress metadata
only. All 917 ST-positive common-label rows have formation dates; 909 have
complete preceding 60-session windows and eight retain exactly AS's 254
pre-listing gaps. Those 917 partitions agree with AR. The four codes absent
from the store account for 18 preferred-share controls and nine explicitly
typed investment-company/foreign-DR observations; all remain in the audit.
The 136 stored codes have consistent completed progress metadata. Samsung
Electronics and SK Hynix each have complete date coverage at all 14 formations.
Independent offline recomputation passed. No price values, API or reserved
database were accessed, and date presence does not certify normal observations.

Task AU (`.planning/rd-au-large-liquid-normal-lookbacks.md`) now checks stored
bar states for all 944 rows, reading observations only through each code's
last formation. The 917 ST-positive rows contain 909 complete preceding
non-frozen candidate windows and the same eight short AS histories. Nine
windows extend beyond 60 market sessions after skipping frozen bars. No
invalid observations were found; one formation (`010620`, `20251201`) is
frozen and stays separately flagged. Samsung and SK Hynix each have complete
candidate windows and non-frozen formation bars at all 14 dates. Independent
saved-state recomputation passed; this does not certify historical identity.

The eight short histories' expanded search bounds are not a demand to classify
the current issue before its verified listing. A separate AS reconciliation
preserves the original results and bounds the next classification acquisition
to observed history: 52 of 136 ST-positive codes start at 20190102 and 84 later.
All 27 controls/explicitly typed records and all insufficient histories remain.

Task AV (`.planning/rd-av-large-liquid-identity-anchors.md`) verified a DART
route returning only a company-overview leaf and acquired 122 dated regular-
report sections. The six remaining regular-report queries were officially
empty within their fixed date bounds; official KIND new-listing/relisting
notices supply their starting identity sources. Together with AS's eight
existing listing notices, this covers the 136-code acquisition workload.
Samsung and SK Hynix's saved reports contain dated company names, Korean
offices and operating businesses. The full 944-row population and all original
short/control dispositions remain intact. Source acquisition does not certify
the exact issue link or continuous operating-company status for every code.
AV preserves initial failures, source-format corrections, request counts and
independent checks. Lost temporary pilot evidence is explicitly excluded;
subsequent evidence uses persistent private archives.

Task AW (`.planning/rd-aw-large-liquid-identity-reconciliation.md`) preserves
all 944 rows and reconciles the 917 target rows / 136 codes without certifying
historical intervals. The 134 available earliest trading-identity observations
and 16 adjacent-formation metadata differences across 13 codes are saved;
the two unavailable earliest dates remain unresolved. The prior AL/AI merger
ledgers have no exact-code overlap and cannot clear this pool's event history.
Actual issuer-content review found omissions in acquired reports. Eleven
bounded supplementary overviews bring the reviewed DART total to 133 source
records, with sufficient-content candidates for 120 of the original 122 DART
codes. 005070 and 196170 remain content gaps after supplementation. Fourteen
KIND listing anchors remain separate. Neither a content candidate nor a
mechanical name match is a certified issue bridge. AW records source hashes,
two transport failures, bounded recoveries and independent verification.

Task AX (`.planning/rd-ax-large-liquid-dated-issue-bridges.md`) resolves both
remaining content gaps using three fixed report leaves. All 122 DART codes now
have issuer-content candidates, alongside the 14 KIND anchors. Exact quoted
names and explicit aliases are checked against the complete saved dated KRX
basic-information population; AX records the exact source counts. The literal
first pass remains intact;
a separately registered English legal-form normalization resolves 56 further
observations. The result has 736 dated name links, 97 explicit listing-issue
links and 84 unresolved observations across 12 codes. All sampled observations
link for 124 of 136 codes. All 944 original rows and their short/control
dispositions remain. These are source-point links, not certified continuous
history or proof of the actual historical KRX publication time.
The initial bridge contract's availability requirement remains unmet.

Task AY (`.planning/rd-ay-large-liquid-identity-events.md`) adds dated official
rename notices for 67 previously unresolved observations. Its supplementary
result retains AX's 736 name links and 97 listing links, leaving 17 unresolved
observations for 002790 and 017670. All sampled observations now link for 134
of 136 codes. It preserves all original rows and controls. The selected notices
also distinguish two market transfers and three section changes from business
conversion; the planning record owns the source counts, exact dates and hashes.
Two added report covers supplied no acceptable new aliases. The first viewer
title mismatch remains preserved alongside its bounded metadata/body recovery.

Task AZ (`.planning/rd-az-large-liquid-remaining-identity.md`) adds fourteen
017670 links using the original report's explicitly labelled issuer history
and legal-name provenance. The supplemental result retains AY's 900 links,
all 944 original rows and 27 controls. It leaves three unresolved 002790
observations: 2019-04-02, 2019-10-04 and 2021-04-15. Thus 914 of 917 targets
link, with all sampled observations linked for 135 of 136 codes. The selected
002790 history supplied no acceptable abbreviation evidence. AZ also reviews
all six correction summaries and five additional-listing bodies. Their
eighteen correction items, version-specific wording and actual listing dates
are preserved in the planning record; this does not certify event absence.
Its first history-parser failure is preserved alongside a bounded recovery
that reused the cached response and fetched only the remaining history leaf.

Task BA (`.planning/rd-ba-large-liquid-identity-gaps.md`) supplies the dated
AmoreG common-issue abbreviation/code/ISIN notice and connects it to the
previously verified issuer-history finding. Its supplement adds the three
002790 links, preserving all earlier row fields, the 914 existing links,
27 controls and eight short-history pairs. All 917 sampled targets now have
source-point links across 136 codes; these are 917 issue-date observations,
not 917 distinct stocks or certified continuous eligibility intervals.
The legal-name change month (March 2011), notice publication (April 15) and
exchange name-change listing (April 20) remain distinct. The old history raw
was not reread in BA; its previously verified content-review JSON was reused.
Four basic-info responses also resolve the requested-date identity projections
for 011790 on 2020-12-30 and 019170 on 2020-07-10. They add no formation rows
and do not repair missing trading observations or historical publication time.

BA's five bounded original tender sections identify F&F Holdings as offeror,
F&F common shares as target, and proposed share consideration for participating
shareholders, with a fractional-share cash provision. They do not establish
automatic conversion of all holdings, actual participation or completion.
The date-role erratum matters for the next step: 2021-07-20 is 383220's first
required lookback observation, whereas its first formation is 2021-10-20.
The July 23 corrections therefore precede that formation and require review.
Old derivative `after_first_formation` labels are preserved but superseded by
this correction; the four actual formation dates did not change.

Next reconcile those two July 23 tender corrections and relevant outcome
evidence with the originals under a separately fixed minimal-source scope,
then establish continuous eligible-company intervals and their limitations.
The six earlier correction summaries and five additional-listing bodies have
already been reviewed; do not restart their acquisition by default. The
observed metadata differences have notice evidence, but the bounded title
search does not prove complete event coverage. Do not carry an older DART business classification
through restructuring merely because the stock name and code link. Actual
historical KRX publication and continuous eligible-company intervals remain
uncertified. Current names, unchanged sampled metadata and absence of SPAC
wording cannot substitute for those checks.
Then finish AN's post-conversion baseline and AG price-basis/corporate-action checks.
The bar-state/date diagnostic is complete; historical identity and accounting
remain prerequisites for the new sizing runner. No new large/liquid
universe selection, portfolio or comparison run has occurred; readiness and
historical certification remain false. No new operator choice currently blocks
the next bounded data-validation work. The separate pre-2019 collector pass
finished with five failures; AS records its read-only operational diagnosis and
existing retry schedule without accessing reserved prices or changing it.

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
