# Research Direction Task AY — reconcile dated aliases and identity events

## Scope — 2026-10-08

The operator continued after AX, whose PR #254 was approved and merged at
`3d34699487d53846d9c3300463abfa0d1eeff88b`. Its complete Python CI passed
4572 tests with three skips; CodeRabbit withdrew its test-count finding after
checking the parametrized execution counts. The isolated GCP research checkout
was synced to that merge; the collector remained clean at `4ce85d7`.

Keep the fixed 944-row population, 917 target observations / 136 codes and
every short-history/control disposition. AX's 833 name/listing cross-references
remain immutable, alongside its 84 unresolved observations across 12 codes.
Reconcile those names and AW's separate 16 metadata differences across 13
codes. Preserve the six listed correction versions, the existing additional-
listing/tender-title leads and two unavailable earliest identity dates.
Do not mistake an ordinary issue, listing-title change or market-section label
for a change in issuer classification without inspecting the actual evidence.

Start with independent offline triage of the pinned AX/AW/AV evidence. Retain
literal before/after names, exact code/ISIN/share class, report corporation ID,
publication dates, source hashes and every unresolved date. Group differences
only to define the smallest source workload; current names and titles alone
cannot establish historical facts. No fuzzy matching or unquoted alias is
authorized. Append finite source requests, date bounds, report/section choices
and failure rules before any new issuer/event acquisition.

Any added evidence must preserve the distinction between issuer identity,
the exact issue, effective change date and public availability. A later
document cannot be treated as available at an earlier formation merely because
it describes that period. The actual historical KRX publication times remain
unknown. A present service guide or retrieval date cannot certify them.

This task does not read prices, run sizing/comparisons, access the reserved
pre-2019 price panel, change collectors or promote a strategy. All continuous-
identity, historical-eligibility, normal-baseline and universe-ready flags
remain false unless their separate complete contracts are met. Preserve
originals and incomplete attempts, verify the entire fixed workload
independently, archive private evidence and publish through review/CI before
syncing only the isolated research checkout.

## Search-form contract check

Before issuer queries, preserve one public KIND search-form response from
`https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain` to
identify the actual report-title and exact-code controls. This is one GET,
at most 2,000,000 response bytes, with no automatic retries or redirects.
Save its scope, intent/result and source bytes exclusively in a private
persistent directory. No company search, report body or chart is part of
this check. A failed or unexpected form does not authorize guessed controls.

## Fixed metadata acquisition — registered before issuer search

The one search-form probe returned HTTP 200 (273757 bytes), SHA-256
`e5ea856bf3bcd898972d74381c4ea810309cc92e59f6bfd91691d4ad2edda232`.
Its actual exact-code and report-title controls define this scope. The private
query file `var/ay-kind-query-scope.json` has SHA-256
`e546d363772089313a6409aeb101ae11c1e80c23532b763d798c33bfa8605fba`.
It supersedes the offline triage's generic search proposals; no alias-cover
or correction/title-body request is included yet.

| Query | Code | From (inclusive) | To (inclusive) | Literal title filter |
| --- | --- | --- | --- | --- |
| q01 | 000270 | 2020-10-13 | 2021-04-15 | 변경상장 |
| q02 | 003670 | 2022-10-27 | 2023-04-27 | 변경상장 |
| q03 | 005490 | 2021-10-20 | 2022-04-22 | 변경상장 |
| q04 | 006800 | 2020-10-13 | 2021-04-15 | 변경상장 |
| q05 | 009540 | 2019-04-02 | 2019-10-04 | 변경상장 |
| q06 | 009540 | 2022-10-27 | 2023-04-27 | 변경상장 |
| q07 | 021240 | 2019-04-02 | 2019-10-04 | 변경상장 |
| q08 | 021240 | 2019-10-04 | 2020-04-07 | 변경상장 |
| q09 | 022100 | 2023-11-03 | 2024-05-10 | 상장 |
| q10 | 034020 | 2021-10-20 | 2022-04-22 | 변경상장 |
| q11 | 066970 | 2023-04-27 | 2023-11-03 | 소속부변경 |
| q12 | 066970 | 2023-11-03 | 2024-05-10 | 상장 |
| q13 | 086520 | 2024-11-15 | 2025-05-27 | 소속부변경 |
| q14 | 196170 | 2024-11-15 | 2025-05-27 | 소속부변경 |
| q15 | 267250 | 2021-10-20 | 2022-04-22 | 변경상장 |
| q16 | 329180 | 2022-10-27 | 2023-04-27 | 변경상장 |
| q17 | 042660 | 2023-05-15 | 2023-08-03 | 변경상장 |
| q18 | 051900 | 2022-04-22 | 2022-10-27 | 변경상장 |

The original AW queue stays intact. Full saved basic metadata narrows
021240's second bracket and 267250's bracket; these are observation bounds,
not inferred legal dates. 042660 adds the already-saved old-name filing to
first-trading-identity bracket; 051900 adds an English-name-only difference
that AW's earlier field set did not test. The two alias-only codes are not
silently covered by these queries.

POST only to KIND's inspected details endpoint, 100 rows/page, at most five
pages per query: 18 logical searches, at most 90 page requests and 270 wire
attempts. No final-only filter (correction/original titles must remain).
Require complete counts/pagination, exact query dates, receipt IDs and literal
title-filter matches. Preserve the opaque issuer identifier without treating
it as a stock code. No viewer, report body or chart request is allowed here.

Use exclusive private output, scope/source/test fingerprints and intent before
every attempt. Requests are serial with at least 1.1 seconds after each
response; cap each response at 2000000 bytes and refuse redirects. Retry only
transient pre-HTTP exceptions with zero received bytes, at most three lifetime
attempts with 10/30-second backoffs. Stop the batch on other failures or
unrecognized/empty response shapes, retaining partial evidence. A zero-result
or metadata title never establishes absence of a change or an effective date.
Review saved responses offline before registering any recovery or body scope.
All certification/readiness flags remain false.

## Fixed alias-cover acquisition — registered before requests

The offline name triage preserves all unresolved rows, including the three
009540 attempts that matched another issue. Its SHA-256 is
`fe87e98486c562ac6102e081c7996b5b82f89c12f5356b7b63ebc7fa12ec2c1f`.
Two saved report wrappers expose the following exact cover-only leaves:

| Code | Receipt | dcmNo | eleId | Offset | Length | Leaf |
| --- | --- | --- | --- | --- | --- | --- |
| 002790 | 20181114001310 | 6381524 | 1 | 582 | 4029 | 분 기 보 고 서 |
| 017670 | 20181114001765 | 6382558 | 1 | 583 | 4039 | 분 기 보 고 서 |

Check the pinned saved wrapper, original version and leaf boundaries before
requesting only these DART `report/viewer.do` leaves (`dtd=dart3.xsd`).
This is bounded issuer-identification metadata under the existing identity
work scope. No pre-2019 full report, history, prices or price/volume section
is authorized. The cover may or may not supply the needed identity/alias;
absence of that text leaves the case unresolved.

At most two logical GETs and six wire attempts, the same serial gap,
response cap, pre-HTTP-only retry/backoff and stop/preserve contract as above.
Default invocation does no requests or writes. Execute only to a new private
alias-cover root after preserving the exact scope and source/test hashes.
Validate the report/cover heading and absence of other outline sections;
reject a broadened or unexpected body. No additional report search or wrapper
request is included. A cover acquisition alone accepts no new alias and
changes no historical certification flag.

## Fixed selected-notice acquisition — registered after metadata review

All 18 queries completed in one page each: 18 logical/wire requests, 18 HTTP
200 responses, no retries or failed queries. Preserve every returned title,
including unrelated bond/ELW/ETN notices; exact-code search is not itself
proof that every result concerns the target common share.

The fixed notice selection `var/ay-kind-notice-scope.json` has SHA-256
`75d2916b4f96b189d700622ab1104a5ea86f1b0459fe40211c1088671acbc0c8`.
It pins each source-query result and exact row. Select only these 20 official
market-division receipts: 13 rename notices (one English), three section
notices and both exchange-side notices for each of two market transfers.
No reference-price guide, attachment or issuer's full periodic report is selected.

| Code | Receipt | Displayed publication day | Exact title |
| --- | --- | --- | --- |
| 000270 | 20210331000115 | 2021-03-31 | 변경상장(상호변경) |
| 003670 | 20230329002129 | 2023-03-29 | 변경상장(상호변경) |
| 005490 | 20220314001324 | 2022-03-14 | 변경상장(상호변경) |
| 006800 | 20210401000779 | 2021-04-01 | 변경상장(상호변경) |
| 009540 | 20190610000182 | 2019-06-10 | 변경상장(상호변경) |
| 009540 | 20230419000046 | 2023-04-19 | 변경상장(상호변경) |
| 021240 | 20190402001016 | 2019-04-02 | 변경상장(상호변경) |
| 021240 | 20200218000318 | 2020-02-18 | 변경상장(상호변경) |
| 022100 | 20231227000754 | 2023-12-27 | 신규상장((주)포스코디엑스, 상장일 2024.01.02) |
| 022100 | 20231226000858 | 2023-12-26 | 상장폐지(유가증권시장 상장) |
| 034020 | 20220418000187 | 2022-04-18 | 변경상장(상호변경) |
| 066970 | 20230428001065 | 2023-04-28 | 소속부변경 |
| 066970 | 20240125000543 | 2024-01-25 | 신규상장((주)엘앤에프, 상장일 2024.01.29) |
| 066970 | 20240124000603 | 2024-01-24 | 상장폐지(유가증권시장 상장) |
| 086520 | 20250430001241 | 2025-04-30 | 소속부변경 |
| 196170 | 20250430001198 | 2025-04-30 | 소속부변경 |
| 267250 | 20220406000092 | 2022-04-06 | 변경상장(상호변경) |
| 329180 | 20230411000094 | 2023-04-11 | 변경상장(상호변경) |
| 042660 | 20230608000085 | 2023-06-08 | 변경상장(상호변경) |
| 051900 | 20220623000210 | 2022-06-23 | 변경상장(영문상호변경) |

For each receipt, request one exact KIND viewer and preserve every version
label/date/doc ID. Bodies are limited to versions whose displayed dates are
on/after that source row's publication day and on/before its fixed query end,
with a maximum of three versions per case. Preserve original and correction
versions separately; do not silently use a future correction as an earlier
fact. A missing original, excess eligible versions, unrelated title/version,
unknown structure or failure stops the batch and requires offline review.
For each selected doc ID, request only its contents pointer and the one exact
official external notice URL validated from that pointer. Maximum 140 GETs
for this fixed batch, no automatic retries; every attempt and bounded partial
response is retained. Apply the same serial gap, 2000000-byte response limit,
redirect refusal and private/exclusive evidence contract. Default is offline
preflight. Extract no market-price series, return or portfolio statistic.
The notice acquisition itself neither certifies an event nor clears any row.

### Viewer-label contract correction — before any notice request

Keep the original selection file intact. The executable v2 selection adds
one exact `viewer_title` per case; no case, receipt or date scope changes.
`var/ay-kind-notice-scope-v2.json` SHA-256:
`2d7f0de23216162abe64c782d5cedeec45d05e393d403b37c823c965b8a51b45`.
For the two new listings only, the expected viewer title is `신규상장`.
For every other case it equals the full search title. This is an exact mapping,
not a prefix/fuzzy match. The saved AS viewer `20190211000659` establishes
this abbreviated new-listing label (SHA-256
`1489c2eaf4953da72eaecf790869ebb1576072cc56134d8d8be0a5a8cdd28f40`),
while its saved search carries the company/date-qualified title (SHA-256
`8dc5a6f5c946d994535ca6a19869a7dd89cc72eaefbe3a24aeb9964b4ac8d7a6`).
The actual selected viewer must still bind the requested receipt, retain every
version and satisfy the fixed date/correction checks. Unknown labels stop.

## Completed offline triage and search verification

The name triage preserved all 84 unresolved AX rows: 61 rename-observation
candidates, 17 abbreviation/transliteration candidates and six English-name
change candidates. These are investigation categories, not accepted aliases.
Its reconstruction preserves all original source attempts, including three
009540 attempts whose old name matched issue 329180 instead. No original
AX result is overwritten. The independent event queue retains 31 items
across 22 codes (16 metadata differences, six correction versions, seven
title leads and two unavailable earliest dates); those categories do not
count distinct legal events. Event-queue SHA-256:
`4ee94d3d36ebe828200808b253e18888d70e63ed4597f2c587398ec99bd7e192`.

The search client's 27 synthetic tests passed. A separate standard-library
HTML verifier, without importing the acquisition parser, reconstructed all
77 rows across the 18 saved pages and checked 80 artifacts. The 20 selected
rows agree with the source receipts/titles/publication times; the remaining
57 rows remain preserved. All requests returned HTTP 200, with no retries;
the minimum response-to-next-attempt gap was 1.100032052 seconds. Eleven
mutations were refused. Private verification receipt SHA-256:
`23ecf6a02acae1a90193701ee07fa89fa54352e4a8fdc4e549c29360b032e968`.

Both fixed alias covers were obtained with two GETs and no failed attempt.
The cover client's 17 synthetic tests and real saved-wrapper preflight passed.
The actual company-name labels repeat the legal names already known:
`(주)아모레퍼시픽그룹` and `에스케이텔레콤주식회사`. They do not supply
an additional explicit alias or registration identifier for the missing
link. Website domain names are not accepted as company-name aliases. The
two acquisitions therefore accept zero new aliases and do not clear any of
the 17 abbreviation/transliteration observations.

## Offline reconciliation contract — before interpreting notice bodies

Preserve AX's entire fixed result and every original row/source attempt.
Review notice bodies by their explicit labelled fields, keeping raw hashes,
quoted before/after names, common-share code/ISIN, effective date and each
version's publication date. Market transfers and section changes are separate
event records, not name aliases or evidence of a business-company conversion.
An identity date must not be inferred from the search bracket or receipt ID.

A supplementary dated notice link can be accepted only where the body
explicitly identifies the target common issue (code and ISIN), its after-name
is an unambiguous match in the entire saved dated KRX basic-information
population under AX's existing literal/English legal-form rules, the version
was public before the formation day and its effective day is not later than
that formation. No new acronym expansion, transliteration or fuzzy rule is
introduced. Missing explicit code/ISIN/share type, ambiguous names, a later
version or an unexpected form leaves the row unresolved. Source-point links
cannot certify the older DART business classification through a restructuring.

Only original unresolved rows may receive a supplementary notice-link status;
all already linked rows and controls remain intact. Record attempts/reasons
for every remaining row, and keep actual historical KRX publication, continuous
identity, eligibility, normal-baseline and universe-readiness flags unknown or
false. The original correction-version/title/earliest-date queues remain
separately visible; this title-filtered search is not complete event coverage.

## Preserved viewer refusal and bounded recovery

The first notice attempt returned HTTP 200 but the source viewer used the
exact label `변경상장 (2021.03.31)`, not the registered full search title.
The batch stopped after one GET, before any contents/body request. Keep its
scope, raw response, parsed versions and failure unchanged. Raw SHA-256:
`c1cd18b7be4e405d1c9fbf9033ccc88fdd39f274c48e47af23527837f3d6edd9`;
the displayed original document ID is `20210331000263`. The initial notice
client passed 19 synthetic tests; these did not establish unseen source labels.

Recovery phase one reuses that exact cached viewer and requests only the
remaining 19 viewers from the already-fixed 20 receipts, in the same order.
No receipt/date/title-filter expansion and no contents/body request occurs in
this phase. Preserve every version label and explicit date/doc ID, without
accepting a renamed/abbreviated label as a body selection. Maximum 19 GETs,
no retries, same private/exclusive 1.1-second/2000000-byte/redirect-refusal
contract. A parse/transport failure stops and preserves the batch. After
offline review, register exact selected document IDs and labels before the
separate body-only recovery. No original viewer is requested twice.

### Exact body recovery — registered after all viewer metadata

The remaining 19 viewer GETs all returned HTTP 200; combined with the cached
first viewer, there are 20 receipt-bound viewers. Each contains one plain
original version on the original search-row day. Preserve the initial title
refusal as a failure, not a successful initial batch. Viewer recovery result
SHA-256: `5c64d109f22f2cc7dcf34143cbee58a7e59d10f36e7faef2f7f6268810765128`.

The body-only scope `var/ay-kind-body-recovery-scope.json` has SHA-256
`25ac31d1473bedd55d690850bb410541bf9ef93ffc0ad08ad5571b6ce0af53ad`.
It supersedes v2's attempted full-title body selector with the following
inspected exact labels/documents. Match every stored viewer byte hash and
receipt again; no guessed title, correction selection or extra version is used.

| Code | Receipt | Exact document ID | Exact viewer label |
| --- | --- | --- | --- |
| 000270 | 20210331000115 | 20210331000263 | 변경상장 (2021.03.31) |
| 003670 | 20230329002129 | 20230329004657 | 변경상장 (2023.03.29) |
| 005490 | 20220314001324 | 20220314002992 | 변경상장 (2022.03.14) |
| 006800 | 20210401000779 | 20210401001844 | 변경상장 (2021.04.01) |
| 009540 | 20190610000182 | 20190610000423 | 변경상장 (2019.06.10) |
| 009540 | 20230419000046 | 20230419000082 | 변경상장 (2023.04.19) |
| 021240 | 20190402001016 | 20190402002454 | 변경상장 (2019.04.02) |
| 021240 | 20200218000318 | 20200218001116 | 변경상장 (2020.02.18) |
| 022100 | 20231227000754 | 20231227001810 | 신규상장 (2023.12.27) |
| 022100 | 20231226000858 | 20231226002823 | 상장폐지 (2023.12.26) |
| 034020 | 20220418000187 | 20220418000461 | 변경상장 (2022.04.18) |
| 066970 | 20230428001065 | 20230428001160 | 소속부변경 (2023.04.28) |
| 066970 | 20240125000543 | 20240125001541 | 신규상장 (2024.01.25) |
| 066970 | 20240124000603 | 20240124001736 | 상장폐지 (2024.01.24) |
| 086520 | 20250430001241 | 20250430001314 | 소속부변경 (2025.04.30) |
| 196170 | 20250430001198 | 20250430001035 | 소속부변경 (2025.04.30) |
| 267250 | 20220406000092 | 20220406000221 | 변경상장 (2022.04.06) |
| 329180 | 20230411000094 | 20230411000223 | 변경상장 (2023.04.11) |
| 042660 | 20230608000085 | 20230608000247 | 변경상장 (2023.06.08) |
| 051900 | 20220623000210 | 20220623000512 | 변경상장 (2022.06.23) |

Only the exact contents pointer and validated official body URL for each of
these 20 IDs may be requested: 40 GETs maximum, no new search/viewer/attachment
request, no automatic retry, same gap/size/redirect/private evidence contract.
Unexpected transport, pointer or structure stops and preserves the batch.
Retain all original evidence and scope versions. Contents/body acquisition is
not a classification, and only the separately reviewed labelled identity
fields may contribute to the offline reconciliation contract above.

## Completed notice review and supplementary dated links

The exact body recovery completed 40 GETs with 40 HTTP 200 responses and no
retries. It followed no new viewer/search/attachment endpoint. Result SHA-256:
`b602f7322cc29f3d3689c5de21b1e46d907904b36469fea63ed6e2360ba7e7f5`.
The reviewed fields comprise 13 rename notices, three section notices and
two matching entry/exit pairs for market transfers. All 13 renames explicitly
identify their common-share code and ISIN. Preferred-share rows in 006800
and 051900 were retained in source and excluded from common-share names.
Only printed English names and the labelled common-share abbreviations are
used; no Korean transliteration or abbreviation expansion was added.

The PRE-form notices have `html/head/pre` rather than a `body` element. The
offline readable helper was extended only for that inspected exact shape;
the original parser and raw responses were not modified. Content review
SHA-256: `2eba8cc5fc55402fa7c5324e1d4e7d5463f47b775d55875c270eb67e8b3ed9d0`.

022100's KOSDAQ exit and KOSPI entry have the same effective date,
2024-01-02; 066970's pair has 2024-01-29. Both exit notices explicitly say
`유가증권시장 상장`; they do not establish company extinction. The three
section notices give 066970's 중견기업부→우량기업부 change on 2023-05-02,
086520's 우량기업부→중견기업부 and 196170's 기술성장기업부→우량기업부
changes on 2025-05-02. Their bodies lack an explicit issue code/ISIN, so
those section records retain the exact-code search context and labelled
company name without claiming an explicit-issue bridge or business conversion.

The offline supplementary result preserves all 944 rows, 917 target
observations, 136 codes and 27 controls. AX's 736 name links and 97 listing
links remain unchanged. Exactly 67 former unresolved rows gain a separately
labelled `unique_dated_notice_name_link`; 17 remain unresolved, comprising
002790's three observations and 017670's fourteen. In total 900 target
observations link and 134 codes have every sampled observation linked.
Every original AX source attempt, including the three wrong-issue matches,
remains available. Result SHA-256:
`0897411e05502e884cdcb4e67e43c1e796ab92ba2d684b00502087c139501b2a`.
The reconciliation's 17 synthetic tests and real offline preflight passed;
an independent complete recomputation is tracked separately below.

These links do not certify continuous identity, domestic operating-company
status after restructuring, actual historical KRX availability, normal
baseline or a usable universe. No price/return comparison, sizing run or
strategy promotion occurred. The remaining alias, correction-version,
issuance/tender, earliest-date and availability queues are not silently closed.

## Independent source verification and a timing-proof limitation

A separate verifier checked 424 artifacts across the preserved first failure,
viewer recovery and body recovery: every receipt, displayed version, original
search row, contents pointer, official URL, byte hash and file permission
agrees. There is one cached viewer, 19 new viewers and 40 contents/body GETs;
no duplicate or additional request was made. Fourteen mutations were refused.
The initial failure and all 15 original v2 viewer-title mismatches remain.
Verification receipt SHA-256:
`06b0d247a8a967359a0643d629c3899235b8cf882708666dc4030d0f30946446`.

The body's minimum stored response-stamp to next intent-stamp gap is
1.099965169 seconds (request 26), 34.831 microseconds below the nominal
1.1-second contract. The client sleeps from an earlier internal completion
timestamp, then records the response stamp after hashing, so these two clocks
do not prove an actual response-completion spacing violation. Nevertheless,
strict ledger-only spacing certification remains false. Keep the evidence and
this limitation; do not re-fetch bodies or edit timestamps to manufacture a
pass. Any next acquisition must preserve the same monotonic completion value
used by the spacing calculation. Source completeness and URL/hash verification
are separate from that timing-proof limitation.

The local documentation/index/Codex guardrail suites passed 111 tests with
one skip and 30 passing subtests. The initial search/notice/body-recovery
clients respectively passed 27, 19 and 11 synthetic tests; the cover client
and offline reconciliation each passed 17. These tests do not stand in for
the separately recorded real-source checks or independent row recomputation.

## Independent complete recomputation and fixed archive

A fresh verifier reconstructed the fields and citations of all 20 notices
using its own HTML reader, without importing or executing the producer or AX
matcher. It checked 101 file hashes and 15,686 assertions against the complete
35,271-row, 14-date basic population. It independently recovered exactly 67
new links and 17 unresolved rows, while preserving the original 833 links,
27 controls and all 944 rows. All sampled observations link for 134 of 136
codes. Ten mutations covering dates, preferred shares, codes, unsupported
aliases and population collisions were refused. Certification/readiness stay
false; actual historical availability and `known_on` stay null. No HTTP, SSH,
database or price access occurred in the verification.

Verifier SHA-256:
`a236c8ea839256e73302ff90704294e111d9b4deab8916ba948316d8711dd336`.
Exclusive private verification receipt SHA-256:
`9c2d205e89f76b874944f032dbc7975b5fa7ba82834e2092a5668e63e491a090`.

The fixed archive covers the form, search, covers, preserved initial notice
failure, viewer recovery, body recovery, reconciliation, scripts/scopes and
three independent verification receipts. The local preflight verified 593
payload files, 41,081,827 expanded bytes and 2,837,516 compressed bytes, with
zero SSH requests or filesystem writes. Fixed pin configuration SHA-256:
`06306dced0822a29977086cfd071d76393ee4969e049a356c28f41ab9453a31d`;
manifest SHA-256:
`586eae3792808d355bcd28300de9e9a44bad4785e0b6ab871630c98723ab0a35`.
The previously verified bounded receiver will create only the new private
GCP evidence root
`/home/minjun4897/research-evidence/large-liquid-identity-events-20261008-v1`,
with exclusive creation, regular files, exact manifest verification, 0700
directories and 0600 files. No credential, trading database or collector
configuration is included. Transfer and separate read-back are recorded below
only after execution.

The transfer and a separate SSH read-back both completed successfully with
the same manifest SHA-256. Clarification of the preflight wording above:
593 is the total file count, comprising 592 payload files plus `manifest.json`.
All recorded byte lengths, hashes, exact file membership and 0700/0600
permissions match. The collector checkout remained clean at
`4ce85d714890b87f1a57ae89d4942660e41c0483`; it was not changed or restarted.

The next bounded research package starts with explicit issuer/abbreviation
evidence for 002790 and 017670, then the preserved correction-version,
issuance/tender and earliest-date queues. This completes AY's selected
acquisition and reconciliation workload, not the continuous-eligibility,
availability, post-conversion baseline or price-basis prerequisites. Those
remain required before a new sizing/backtest comparison can begin.
