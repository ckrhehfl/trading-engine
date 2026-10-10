# Claude to Codex handoff

Original handoff recorded on 2026-10-05 against HEAD `4ce85d7` (PR #221).
Current research scope and source status last updated on 2026-10-09 KST,
including the operator's numeric-boundary decision, Task AR's completed
liquidity acquisition, Task AS's listing/classification-source evidence and
Task AT's date-only price-panel mapping, Task AU's bar-state audit and
Task AV's bounded historical company-overview acquisition and Task AW's
metadata reconciliation and issuer-content review, followed by Tasks AX-BA's
dated issue, notice and history-source links and bounded tender-content review.
Task BB inventories the remaining replay gates and starts synthetic lot-book
integration; it does not certify eligibility or execute a new strategy study.
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

Task BB (`.planning/rd-bb-large-liquid-replay-readiness.md`) fixes eight explicit
completion gates and independently checked local BA/workload identities. Its
one bounded GCP saved-metadata read preserves the complete 944-row population,
917 target windows and 27 controls. BB records the required listing-bounded
code/session inventory and separately preserves all verified pre-listing
search references, with no change to AU's original.
This does not require one filing per date: a verified period can cover many.
Actual holding/action-lot denominators remain undefined, not zero, and historical
eligibility, publication, operating-period baselines and readiness remain open.

`research.activity_book` composes AG's existing arithmetic into immutable cash
and acquisition lots with atomic compulsory exchanges/final payouts, event-ID
reuse refusal and forward-only accounting cutoffs. Synthetic checks passed;
the adversarial review's mutable dataset-ID case was fixed at the new boundary.
This component has no data loader or full replay. Independent reconstruction
matched every inventory field and rejected 23 mutations; all ten private GCP
archive files were read back with matching hashes and permissions. Finish
review/CI and publication, then continue the minimal F&F
correction/outcome scope and the required historical intervals while preparing
full synthetic selection/fill/mark/exit/event ordering. Do not wait for all
source acquisitions before doing the synthetic integration work; real prices
still require every data/replay gate and reviewed preregistration. New sizing
and comparison executions remain zero.

During the scheduled review wait, BB also prepared `research.activity_replay`
for pure synthetic full-session integration. It combines events, opening due
sales, next-session entries and closing marks without changing v1. Explicit
toy selector dispositions keep the two history screens separate; classification
must be known by formation, and unknown classification stops the whole selection
before hashing, while known excluded controls need no operating baseline.
Date-only toy knowledge is not proof of
the actual morning publication cutoff. Event/quote conflicts stop processing;
absent or frozen entries retain cash without substitution. Real selector
calculations, source adapters and certified evidence remain open, and this
component cannot register or execute an actual large/liquid sizing study.

Information availability concerns what was public at the historical decision,
not whether the current OpenAPI server served the same response then. Original
filing publication and classification effectiveness must remain separate. Market
observations need a supported historical release bound before the modeled
decision; today's FAQ alone does not certify that bound for earlier years.
No blanket historical availability date or completed gate follows from this
distinction. BB records the remaining work and the separate classification
formation cutoff.

The operator selected further historical-publication verification instead of
adopting the proposed 08:30 historical-information assumption. Keep that timing
gate open; BB registers a bounded official-document follow-up. Current synthetic
code review and publication can finish independently of that evidence work.

BB publication is complete: PR #258 merged and the isolated GCP research
checkout was verified at its merge commit, with the collector unchanged.
BC (`.planning/rd-bc-large-liquid-release-timing.md`) continues the operator's
additional-verification choice and prepares the missing pure screen arithmetic.
Its bounded official-document audit found the exact KOSCOM closing-information
connection-standard route, but the list requires login. Existing account status
is unknown; no authentication, signup, purchase or inquiry was performed.
The two inspected official PDFs do not supply the missing historical release
bounds. BC retains R3 and the other actual-data gates, and includes an unsent
clarification draft. Do not resume broad timing searches automatically or treat
the current product description as a verified historical schedule.

BC's pure `activity_screen.py` calculates BB-compatible toy selections with
separate AQ calendar-liquidity and AN normal-operating histories, explicit
source publication times and formation classification. Its local focused
suite passes 120 tests, including 32 new screen cases. This prepares arithmetic
only: the actual loader/evidence adapter, historical timing verification and
actual sizing/comparison gates remain open. No new actual study was run.

BC publication is complete in PR #259; the isolated GCP research checkout
was verified at `b19b8d259aeeb0732d8cfce99f1621a71a72f9f9`, with the
collector unchanged. BD (`.planning/rd-bd-large-liquid-tender-outcomes.md`)
continues only BA/BB's named F&F correction/outcome obligation. Its first
registered acquisition is two exact July 23 DART wrappers, followed only by
separately recorded minimal source bounds. This does not reopen the timing
search or establish complete R5 event coverage or an actual holding denominator.

BD's fixed source acquisition is complete: the two July corrections and the
August 19 result report were read through eight registered requests. The
result clause reports F&F common shares tendered and purchased; the exact
quantities and source locations remain in the BD research record. It
states September 2 as planned Holdings new-share delivery, unlike the July
correction's August 19 proposed date; actual delivery, fractional cash and
research-account participation remain unverified. This elective tender does
not justify a compulsory conversion of every F&F holder. The offer ended
before the first October 20 formation; July 20 is only the first required
lookback observation. Independent final content checks and the private GCP
archive's 335-file readback are complete; PR review/merge and isolated research
sync remain pending at this entry. The collector stayed unchanged. New actual
sizing and comparison counts remain zero.

After BD publication, the next bounded work is an offline evidence/gap map
over BB's exact required-date union, reusing existing reviewed issue anchors,
listing and change evidence. Identify unsupported/conflicting periods before
registering only the specific missing source bounds. Do not forward-fill
source-point identity links into certified operating-company periods. Keep
document/name/market changes separate from actual continuous operating periods:
splitting an `IdentityPeriod` on every rename or document version would reset
the AN baseline artificially. R2/R4 certification, R3 historical publication,
R5 actual lot coverage and real integration/preregistration still precede sizing.

BD publication is complete in PR #260. The isolated GCP research checkout
was verified clean at `fef98717fd4307ea9d2aed5844ca4460fef2ced3`, with the
collector unchanged. BE (`.planning/rd-be-large-liquid-interval-gaps.md`)
starts the offline gap map from the fixed saved summaries. It must preserve
the exact required dates and separate source-point links and content candidates
from continuous operating eligibility. No additional source acquisition or
actual sizing/comparison follows from this mapping alone.

BE's offline map has now been independently reconstructed and archived in
GCP. Its owning record identifies the remaining content-review group and
exact required dates, while retaining all original populations and unknown
eligibility fields. The next bounded task is to register the named content
gaps against existing issuer/listing sources. Continuous eligibility and
historical field publication remain open; no real strategy trial has run.

BF (`.planning/rd-bf-large-liquid-listing-content.md`) reviewed the remaining
saved listing/relisting notices and independently checked their literal
claims. The notices give issue identity, listing facts and headquarters, but
no own/consolidated/holding-business statement. The owning record preserves
these business-content gaps without adding a mandatory country-field gate.
The exact evidence package is archived and independently read back in GCP;
the collector remains unchanged. The next bounded task is to identify and
register one missing issuer-business document/leaf, starting with 0126Z0.
Historical publication and continuous eligibility remain open; this content
review creates no new strategy validation.

BG (`.planning/rd-bg-large-liquid-input-integration.md`) follows the revised
work order: prepare the actual-input code boundary alongside a concrete route
for the historical-publication question. `activity_inputs.py` reads the spent
scan schema without float conversion or NULL-to-zero substitution and joins a
formation against an independently established complete population. Its output
uses the existing screen, Decimal replay boundary and portfolio accounting;
temporary synthetic SQLite fixtures exercise the chain. Actual source periods,
listing bounds and publication times must still be supplied and adjudicated.
The scan digest covers its calendar/progress/bars, not the separate source
values, periods or availability inputs; R7 still needs the full frozen manifest.

The public KOSCOM contact table identifies the KRX daytime FTP closing-data
recipient. BG preserves a concrete inquiry about field definitions, effective
release schedules and corrections, but it is unsent; no email-sending connector
is exposed. An official contact route is not historical release evidence.
This implementation closes part of the R6 code gap only. R2/R4 continuous
eligibility, R3 publication, R5 actual lot/event coverage, actual integration
verification and R7 remain open before R8 sizing. Actual sizing/comparisons
remain zero. The BF business-content follow-up is still outstanding.

Every operator report must distinguish the current work package's completion
from strategy validation and include the current stage/counts, next action,
and any blocker or decision. The sections below describe the original handoff.

The operator's 2026-10-09 instruction now supersedes BB/BC/BG's historical-
release-proof-first choice. Task BH (`.planning/rd-bh-large-liquid-timing-sensitivity.md`)
continues official public research and an explicit assumed-availability D+1/D+2
Discovery sensitivity; historical publication uncertainty alone no longer
blocks actual experiments. Do not ask the operator to email/telephone external
institutions as a prerequisite. Keep confirmed/inferred/assumed evidence and
observation/publication/retrieval separate; current historical coverage is not
historical availability. The full policy is in CLAUDE.md's Discovery subsection.
Timing metadata, policy-based replay and the saved-input preflight runner were
merged in PR #264 and deployed only to the isolated research checkout at
`380af85a6d8ced4d8156643e1a714823c25f1e3f`. BH's actual saved-input preflight
completed with all registered files hash-verified: 41 of 917 windows have independently
reproduced liquidity/short-history failures; 876 remain an unresolved upper
bound before activity calculation. Its first private-parent failure read no
inputs; the separately registered recovery and failed receipts are preserved.
Task BI's exact activity-failure preflight was reviewed, merged in PR #265,
deployed to the isolated research checkout and run successfully. Its registered
outcome and verification limits are in
`.planning/rd-bi-large-liquid-activity-failures.md`. The narrowed issuer work is
Task BJ (`.planning/rd-bj-large-liquid-survivor-evidence.md`), preserving earlier
failures and the complete formation population. Neither D+1 nor D+2 performance
is complete. Eligibility/baseline,
consequential price/action accounting,
actual replay integration and frozen logged preregistration remain four open
work groups. Derive holding requirements in a registered preflight before
demanding their complete coverage; do not create a circular first-run gate.

BJ's pinned source reconciliation and independent review now supply business
content for every potential-window code, including the two previously missing
issuer bundles. This is source content, not certified continuous eligibility.
Its evidence ledger records the exact sources, failed/omitted routes and review
limits. The acceptance standard is formation-known positive historical-period
evidence plus relevant identity/change reconciliation; earlier-than-baseline
publication and proof of every public search's completeness are not new gates.
BJ's separately logged conservative original-issue price/action scope was
reviewed in PR #266, merged as `a450d6cd3254958bf3e0faf9583396c6400acbbc`,
deployed to the isolated GCP research checkout and executed once. Independent
new-output verification reproduced 36 potential intervals per arm / 31 codes /
the complete original-issue code/date union. Its intervals extend through the fixed evaluation end
to cover possible failed exits. They are investigation upper bounds, with
successor/settlement/basis/no-event coverage still open, and must not be called
actual selected holdings or complete strategy coverage. The collector remains
clean and unchanged. Actual receipts and verification limits are appended to
BJ's ledger; paired performance counts remain zero.

Task BK (`.planning/rd-bk-large-liquid-evidence-adjudication.md`) starts the
positive issuer/issue/business-period adjudication for those potential windows
and inspects the engineering route for reuse of saved official raw OHLC.
Its initial summary-only allowlist authorizes no market-input read or automatic
period certification; follow its appended source registration before access.

BK's bounded content review now accepts the 31 codes / 36 required intervals
using positive issuer/issue/business evidence and reconciled changes, with
continuity explicitly labeled inference. The 165 raw-checked passages and
per-code decisions are preserved in its private decision ledger, whose pin is
in BK. HYBE's actual 2021-04-14 common-share rename, domestic Doosan Bobcat
issuer context and SK Innovation's actual holding business close the three
specific content questions. This is not full-universe or normal-input
certification. PR #267 is reviewed, merged and deployed to the isolated GCP
research checkout. BK's actual cached KRX/KIS comparison and producer-free new
output verification completed; its final publication section owns the precise
counts, hashes and limits. Turnover agrees on comparable cached pairs; OHLC
differences remain consequential price-basis evidence, not an action explanation.
Task BL now restores the unchanged BI activity snapshot and connects accepted
periods to actual normal-screen inputs, preserving all source/proof partitions.
Both performance counts remain zero at this checkpoint.

BL recovery-v2 now completed after PR #270's reviewed merge and isolated GCP
research deployment. Independent output-only verification reproduced the full
partition and both timing arms' signal lists without unresolved normal inputs.
The exact successful output pins, preserved earlier failures and verification
limits are in BL's ledger. Normal-input connection is complete; prices/actions,
actual replay integration and final frozen logged performance remain open.
Task BM's pure selection/holding/replay seams and bounded holding-price runner
shipped with the same reviewed merge. Its separately pinned actual specification
and execution protocol are recorded in BM's ledger; implementation
alone is not an actual price read or performance result. Publication/vintage
uncertainty remains an explicitly assumed paired-timing sensitivity question,
not an external-contact blocker. The operational collector remains unchanged.
The same registration PR also adds a pure audit-to-holding-input restoration
seam, tested against actual producer code over synthetic data. It avoids a
second database read when verified price evidence is later supplied to replay;
it does not establish event coverage or complete a performance study.
Its pure paired-report continuation computes the requested descriptive metrics
from existing immutable replay results, preserving initial-NAV losses and
separating signal overlap, targets, filled entries, ordinary sales and open lots.
These pure helpers do not themselves establish completed actual replay.

PR #271's substantive CodeRabbit approval, final CI and normal merge completed.
The first BM diagnostic failed before opening prices: BL's integer slippage
literal and the reference's equal float literal failed an overly strict package
comparison. Its immutable failure/output/log receipts are preserved in BM's
ledger. Recovery changes only this numeric-representation boundary, retaining
the costs, upstream bytes and new-spec validator. One fresh recovery output and
separate independent wrapper are registered there. The provisioned BL
verification receipt remains unchanged.

PR #272 passed exact-head CI and substantive CodeRabbit approval, merged
normally, and reached the clean isolated research checkout at
`74ae8aae8e687f13efe6a4ccd4daa54f076eb028`. Its single registered recovery
diagnostic completed at 2026-10-10T04:45:12Z; separate producer-free verification
passed for new-output typed rows, scope and internal lineage. The exact requested
union contains observed, frozen and absent requested rows, with no unresolved
observed rows; its counts are recorded in BM's ledger. Missing causes remain unknown;
they are not automatic delistings or zero recoveries. The successful pins,
snapshot identities, verification receipt and limits are in BM's ledger.
Original failed evidence is preserved, and the collector remains clean at its
unchanged PR #221 head. A separate committed provisional ordinary-exit preflight
now reuses that full snapshot and recomputes each potential entry's conditional
evidence bounds. It shortens no quote artifact and certifies no actual exit or
no-event period. The pure paired connection preserves the original full path;
shorter coverage requires the separately recomputed child proof and reviewed
no-event evidence. That registered preflight has now completed and passed its
independent coordinate/package check after normal review, merge and isolated
research deployment; see the BM ledger for its immutable receipts. The operator
also selected original-investment grouping for compulsory spin-off components,
as recorded in CLAUDE.md. Grouped slots shipped in PR #274 and reached the clean
isolated research checkout. The continuation connects compulsory two-component
allocation to the same book/replay and reviewed-action decoder, with original
due dates, pending rights, explicit carry assumptions and exact NAV aggregation.
Fresh review found and repaired rounding and detached-rights-sale defects.
All 34 fixed company-history leaves have received content dispositions: 33 bounded
inferred no-compulsory-action intervals and one relevant LG spin-off. These are
separate from executable declarations and source certification. LG's actual raw ratios/record/effect/listing dates are
confirmed in the selected official annual-history leaf. Delivery and event-specific
price-unit connections remain separate evidence obligations.
Prices/action coverage, actual paired replay connection
and final frozen logged study remain three open performance groups. No actual
D1/D2 performance study has completed. Next, use the registered metadata
inventory and existing reviewed issuer evidence for exact holding-period action
coverage while connecting the existing book/report components.

The subsequent BQ basis diagnostic shipped through PR #278 and completed in the
isolated research checkout. Independent output verification passed in a fresh
registered recovery after repairing an empty-source-file manifest rejection;
the failed first attempt remains preserved. This verifies the saved anchor
arithmetic and links, not an economic unit bridge. Component scope and its logged
caller are implemented as source-only continuations, with actual-inventory
checks in the existing paired connector and no committed actual proof inputs
yet. Root must review genuine event/date/unit declarations before registering
that diagnostic; source tests cannot replace that evidence. The operator then
selected KRX raw prices and official allotment ratios for the LG/LX episode,
after the April 28 raw 126500/adjusted 119500 difference lacked a verified economic
unit interpretation. PR #279 now batches the generic core with the related raw
decoder, acquisition plan/caller, immutable projection and explicit paired/replay
connection. The 26-input adjusted-anchor caller remains an unregistered local
prototype outside that continuation. The price-free plan registers 128 sessions,
229 target code-dates, 63 saved envelopes and 65 new KRX requests. Those are
request geometry, not yet independently verified raw observations or exits.
Actual raw acquisition follows normal review, merge and isolated deployment;
the final raw-aware action proof/caller remains a separate obligation. See BQ and the
private evidence ledger for immutable receipts. Actual D1/D2 performance remains
zero/zero and the same three performance groups remain open.

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
