# Research Direction Task AX — link dated issuer evidence to exact issues

## Scope — 2026-10-08

The operator continued after AW's reviewed metadata/content audit. Keep the
same 944 rows, 917 target rows / 136 codes, all short histories and explicit
types. Resolve the remaining issuer-content gaps and create inspectable dated
issuer-to-issue links. Do not run prices, sizing, return comparisons or touch
collectors. AG/AN and AV/AW's source/identity contracts continue to apply.

Start offline with AW's pinned evidence and unchanged AV source archives.
Extract the issuer's literal legal name and explicitly stated aliases from
its own report text, preserving exact snippets, corporate ID, receipt and
publication date. Join only unique dated KRX issue observations, keeping their
short code, ISIN, class, market and source dates distinct. Mechanical cleanup
may remove explicit corporation markers and whitespace; fuzzy/current-name
matching or a subsidiary's name cannot substitute for the issuer. A same-code
request parameter does not establish the response's historical issue identity.

Each accepted source-point link must identify the exact source bundle and the
date on which its necessary facts were available. Later observations cannot
be backdated to the first required lookback date. Retain missing names,
unmatched aliases, observation gaps and changes as explicit unresolved rows.
An accepted source-point link is not a continuous classification interval.
All interval, normal-baseline, eligibility and universe-ready flags remain
false until their separate contracts are actually satisfied.

For 005070 and 196170, first inspect already saved report-wrapper trees for
the smallest relevant leaf that could provide the missing issuer statement.
Fix report/corporation ID, leaf title and offset/length/version before any new
request. Do not repeat a report search, silently broaden to a full report,
or replace an insufficient source with an unregistered later report. Any new
finite source scope and failure/retry contract must be appended before it runs.

Preserve previous originals, omissions and failures. Independent verification
must reconstruct names and links from their source bytes, check the complete
population and reject false publication dates, changed issue IDs and improper
certification. Archive the result separately, publish through review/CI and
sync only the isolated GCP research checkout after merge.

## Fixed source work before execution

Saved-wrapper inspection, without source requests, selected exactly three
additional leaves from the already reviewed post-2019 reports:

| code / receipt | document / element | title | offset / length |
|---|---|---|---|
| 005070 / 20220816000364 | 8767952 / 10 | 1. 사업의 개요 | 62497 / 2948 |
| 196170 / 20200330003371 | 7199355 / 1 | 사 업 보 고 서 (cover only) | 796 / 4014 |
| 196170 / 20200330003371 | 7199355 / 10 | II. 사업의 내용 | 91898 / 178870 |

The last business node has no declared or nested child. Its source interval
ends before the next financial-statement node; it is not the whole filing.
196170's separate company-history node is not selected. The wrapper tree
cannot guarantee which statements are inside a not-yet-read leaf. Review the
returned text before accepting issuer evidence; incidental product/financial
figures do not become prices, strategy features or outcomes.

Pin the preflight JSON at
`341869b928f93beccee8f6d1ff0301372b613661c661f4f3d6c94d3da37105a0`.
Reuse its verified wrappers without fetching them again. Permit only these
three exact GET URLs: three logical requests, at most nine wire attempts.
Allow up to three attempts per logical request solely for pre-response
transient transport errors, with 10/30-second backoffs. Reject redirects,
oversize responses (1 MiB), wrong document/element/heading, persistence errors
or exhausted retries and stop the batch with the partial archive intact.
Wait at least 1.1 seconds after each response. Persist scope, selected nodes,
input/script/test hashes and request/response records before source use in a
new exclusive private directory. No search, wrapper, sibling or full-report
request is implicitly authorized. An insufficient leaf stays unresolved.

For identity matching, reuse all **28 already acquired AQ basic-information
responses at its 14 fixed formation dates**, not just the 944 screened rows.
Verify AQ report hash
`17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b`
and every referenced raw response. Export only ISIN, short code, official
name/abbreviation/English name, listing date, market and classification labels.
Do not export par value, share counts or prices. Full-market identity rows
allow same-date name ambiguity checks; this is not universe expansion or new
API acquisition. Preserve source/request dates and hashes, enforce unique
identifiers within each dated market union and reconcile target rows back to
AW. AQ's next-session availability is a modeling convention, not a certified
historical release timestamp; AX must keep that distinction in each link.

## Literal pass and explicit English legal-form normalization

The first offline pass retains 680 dated name links, 97 explicit listing-issue
links and 140 unmatched observations across 19 codes. All observations link
for 117 codes. Its immutable result hash is
`62c3ba468cd0ed31440f92202e28fc5dee97d9309d96e5b02312cd7f4132d137`.
This first matcher removed only Korean corporation markers and whitespace;
it deliberately left even English legal-form/capitalization differences open.

Before a separate second pass, permit one finite text-cleaning extension for
**the official KRX English-name field only**: compare explicitly quoted ASCII
English names case-insensitively, removing whitespace and a terminal legal
suffix (`Co., Ltd.`, `Corp.`/`Corporation`, `Inc.`/`Incorporated`, `Limited` or
`Ltd.`) when separated from the name by whitespace or a comma. Punctuation
inside the substantive name remains significant. Do not transliterate Korean,
expand an acronym, strip `Group`/`Holdings`, use edit distance or rename an
issuer. Recheck uniqueness against the entire dated common-share snapshot
after normalization. Preserve source literals and mark the exact matching
rule in every accepted link. Keep the strict first result and scripts intact.

This handles directly equivalent forms such as `CJ Corporation`/`CJ Corp.`
and `NAVER Corporation`/`NAVER`; it does not turn `AMOREPACIFIC Group` into
`AmoreG`. Inspect already saved issuer text for explicit additional aliases
of 002790 and 017670, preserving any absence. No new source request follows
from this normalization or from an unmatched row.

## Acquired content and dated-link result

The three fixed leaves completed with three logical requests, three wire
attempts and three HTTP 200 responses, without retries or failures. The saved
result is `de7412f2bd57b94eec77883ae8532ac3f8b799364ac68108f54690f7bc12096b`.
005070's business overview explicitly describes its current manufacturing
activity; its name and domestic location remain sourced to the already saved
overview from the same receipt. 196170's cover identifies the legal issuer and
domestic head office, and its business introduction describes its own business.
The content review reads those identity and business statements in context;
it does not claim a line-by-line review of the entire 1,873-line business leaf.
Both remaining gaps are resolved at the **source-content level**. All 122 DART
codes now have content candidates; the 14 KIND anchors remain separate.

The literal-name review preserves all 133 prior DART source records, their
omissions, exact quotations, corporate IDs, receipts and source hashes. The
new 196170 cover supplies the previously absent name. Inspection of the saved
002790 and 017670 texts adds no acceptable issuer-self alias. In particular,
an abbreviated name appearing only in a large affiliate list is insufficient.

The complete KRX identity projection contains 35,271 rows across 28 saved
responses / 14 formation dates. It exports only the ten registered identity
fields and preserves request/source provenance. Its hash is
`823d8a884a533b6fae755e13dfd4c4eb492479f4a8d3cebda341c64ca2e02d25`.
Every target and retained control reconciles to AW and the full dated snapshot.
No new KRX request, price read or database query is needed for this projection
or either matching pass.

| disposition | strict literal pass | explicit English legal-form pass |
|---|---:|---:|
| unique dated issuer-name link | 680 | 736 |
| explicit listing-issue link | 97 | 97 |
| unresolved dated name | 140 | 84 |
| total target observations | 917 | 917 |
| codes with all sampled observations linked | 117 | 124 |

The second pass resolves 56 additional observations under the registered
English-name rule. Its immutable result hash is
`51f925b1defbb1c844865e4f3539322ae7a5404b20075eedd9a494f1cc0db67a`.
The full 944-row population, all 136 target codes, eight insufficient histories
and 27 retained controls/explicit types remain intact. Linked observations
are dated source cross-references, not an approved investment universe.

The remaining 84 observations span the following 12 codes. Counts are
unresolved sampled dates, not missing stocks or a reason to remove a stock.

| code | unresolved observations |
|---|---:|
| 000270 | 10 |
| 002790 | 3 |
| 003670 | 6 |
| 005490 | 8 |
| 006800 | 6 |
| 009540 | 13 |
| 017670 | 14 |
| 021240 | 1 |
| 034020 | 8 |
| 042660 | 5 |
| 051900 | 6 |
| 267250 | 4 |

Unresolved aliases and observed name changes require explicit evidence; a
current familiar name cannot fill them. The 16 adjacent-formation metadata
differences across 13 codes recorded in AW remain a separate event queue,
including differences on codes whose English names happen to match. The two
unavailable earliest trading-identity dates also remain unresolved. A later
formation observation is never backdated to a first lookback date.

Each link retains the source receipt/publication date, exact code, ISIN,
share class, matching literals/rule and dated KRX response provenance.
`known_on` and actual historical KRX publication time remain null: AQ's
08:30-next-session convention is a model, not proof of historical publication.
Every continuous-identity, historical-eligibility, normal-baseline and
universe-readiness flag remains false.

Next fix a finite alias/rename/event-evidence scope for the unresolved dates
and AW's event queue, preserving successful source-point links. Continuous
domestic operating-company intervals and publication availability still need
their own verification, followed by AN's normal-baseline and AG's price-basis /
corporate-action checks. No return comparison or new sizing run has occurred.

## Verification and evidence preservation

The issuer-name review is pinned at
`a4148b6022a69c2ea30ead91f598eb2110e4fa88a4ae26beb6c7871397e76744`;
the three-leaf content review at
`3aa73c34baf9bb2659055f46d6ce106d1e61d4746f0c92bba99424e52579a204`;
the additional-alias review (zero accepted additions) at
`70a1770c4b0d24fdfec34e3bb7a83d53cc0cf0785dbfe47be50449d301d5abc1`.

An independent verifier checks the three fixed leaves against their wrappers,
raw bodies, readable text and complete request ledger. It independently
reprojects all 35,271 rows from the 28 original GCP basic responses, without
importing the acquisition or extraction implementation. Source hashes and
originals remain unchanged, all classification flags remain false, and 14
in-memory corruption cases are rejected. Its receipt is
`12bfe45b7abb4504946af74a9267bc08de63dfbe9320c375d6107084212ea83f`.

The acquisition, complete-basic export, literal matcher and English-form
matcher pass 11, 24, 11 and 12 synthetic tests respectively. The planning index
and Codex guardrail checks pass 57 tests and 30 subtests, with one skip. The
repository scanner and whitespace check pass. These checks exercise bounded
transport, source provenance, matching ambiguity, chronology and false
certification; they are not tests of strategy returns.

The matching dispositions above complete the name/issue cross-reference step
only. The availability part of the initial bridge contract remains unmet;
`linked` must not be read as acceptance of that entire contract. This also
applies to the 124 codes with every sampled observation linked.

A separate independent bridge verifier reconstructs both matching passes
using its own complete-market reverse indexes, without importing the matcher,
assembler, exporter or acquisition code. It checks all 944 dispositions,
134 quoted source-name records, 182 explicit aliases and the 14 raw KIND
notice bodies, including source-result dates and corporate IDs. Both result
tables agree exactly. Ten in-memory corruption cases are rejected and nine
normalization boundary checks pass, with no HTTP, SSH or database access.
Its private receipt is
`c5bf49a99b3ba257b799f66f73ee744495bfa733a308e49d378cc2301956a3a4`.
An independent document review also confirms the 12-code unresolved table
and the explicit distinction between matching and the unmet availability
requirement.

AX evidence is preserved in the private GCP archive
`/home/minjun4897/research-evidence/large-liquid-dated-issue-bridges-20261008-v1`:
72 files including the manifest, with manifest SHA-256
`9a40d8d2bebe3ae4766e78b2195b77cda3526fe4532b13a0799ebd3716d25e37`.
It contains the three-leaf run, complete-basic identity projection, both
matching passes, source/alias reviews, scripts, tests and independent receipts.
The original AV/AW/AS/AQ evidence remains in its previously pinned archives.
The receiver verifies every hash; a separate SSH readback verifies the exact
file population, all hashes and directory/file permissions (0700/0600).
Only the result documentation and planning index belong in the public PR.
