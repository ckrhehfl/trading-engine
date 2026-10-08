# Research Direction Task AZ — verify remaining issuer aliases and event evidence

## Scope — 2026-10-08

The operator continued after AY. PR #255 merged as
`e1c0ec82e097afccfb6882aee88d3bccbb1acb8e` after all four CI workflows
passed (Python: 4572 passed, three skipped) and CodeRabbit approved the
reviewed head. CodeRabbit withdrew the file-count comment after checking the
existing append-only correction; no unresolved review conversation remained.
The isolated GCP research checkout was synced to that merge, while the
collector stayed clean at `4ce85d714890b87f1a57ae89d4942660e41c0483`.

AY's immutable result has SHA-256
`0897411e05502e884cdcb4e67e43c1e796ab92ba2d684b00502087c139501b2a`.
It links 900 of 917 target observations and leaves 17 unresolved: three for
002790 and fourteen for 017670. All sampled observations link for 134 of 136
codes. Preserve all 944 original rows, 27 controls, insufficient histories,
original source attempts and the completed links. This is a source-point
identity workload, not a change to the fixed large/liquid selection rule.

Start with offline inspection of the two codes' exact dated KRX names,
ISIN/share class, saved DART overviews and covers, and available section
metadata. The earlier two covers supplied no accepted aliases. Familiar
abbreviations, transliteration, current issuer information and fuzzy matches
are not substitutes for dated explicit evidence. An acquisition scope must
name its exact queries or saved document leaves before any new request.

Independently inventory the six preserved correction versions, seven
issuance/tender-title leads and two unavailable earliest identity dates.
Separate what cached evidence can establish from gaps needing a new finite
source request. Titles alone establish neither a legal event nor absence of
one; stock-code continuity does not carry operating-company classification
through restructuring.

No market-price series, return, sizing, new universe selection, reserved
pre-2019 price data, full pre-2019 report, trading database or collector change
is part of this task. Existing identity-only source boundaries remain. Keep
actual historical KRX availability and `known_on` unresolved; continuous
identity, historical eligibility, normal baseline and universe readiness stay
uncertified. Preserve failed attempts and original artifacts. Any new source
client must record the same monotonic completion value used for its request
spacing, addressing AY's disclosed timing-proof limitation without changing
AY evidence. Completed results require independent verification, private
evidence preservation, CodeRabbit/CI and merge before isolated research sync.

## Fixed two-leaf alias check — before any new request

Offline triage reproduced the unchanged population and verified 16 source
pins. Its result SHA-256 is
`abcca3206f671fdf56db16afd3d55851975f9de83ac65f88354f2561a2b51f17`.
Neither saved cover nor overview supplies an explicit stock code/ISIN. The
SK name found in a large affiliate table is not accepted as a labelled issuer
identity. The following original report leaves are the next bounded sources;
their contents and ability to resolve an alias are not known in advance.

| Code | Receipt | dcmNo | eleId | Offset | Length | Exact leaf |
| --- | --- | --- | --- | --- | --- | --- |
| 002790 | 20181114001310 | 6381524 | 5 | 46261 | 12930 | 2. 회사의 연혁 |
| 017670 | 20181114001765 | 6382558 | 5 | 171498 | 34321 | 2. 회사의 연혁 |

Use only `https://dart.fss.or.kr/report/viewer.do` with these exact saved
parameters and `dtd=dart3.xsd`. Revalidate the original receipt, corporation
ID, section tree and leaf bounds against the cached wrappers before execution.
Wrapper SHA-256 values respectively:
`02ae89fcf04de2ce0ce7622e8b8335a9c0d325765121a424fdd0a12ba4343b6f`
and `ee68c6f8ba94f5257b6ca0eaab4e4993829e1d8da109ca8b228ec90685073ba4`.
Each leaf ends before its next section. No wrapper/search/full report or
additional section is requested. This separately registered issuer-history
check follows AY's completed cover-only scope; it does not enlarge or rewrite
that older scope and does not access reserved price data.

Maximum two serial GETs, no automatic retries, at least 1.1 seconds from the
recorded monotonic completion timestamp to the next intent, 2,000,000 bytes
per response, no redirects. Retain intent before the wire request and every
bounded received prefix, response, script/test/source hash and selected scope
in a new private persistent root. Stop and preserve the batch on transport,
HTTP, persistence or unexpected structure. Default preflight performs no
request and no write. Reject broader report/price-volume sections. Review
the actual labelled identity/history text separately; acquisition accepts no
alias or continuous-eligibility claim by itself.

## Fixed pending-event metadata — before any request

The existing AY event inventory, SHA-256
`4ee94d3d36ebe828200808b253e18888d70e63ed4597f2c587398ec99bd7e192`,
already names these six later correction receipts and seven title leads.
Request each exact DART wrapper or KIND viewer once, without a new search.

| Code | DART correction receipt | Displayed publication day | Original receipt | Period |
| --- | --- | --- | --- | --- |
| 022100 | 20230831000743 | 2023-08-31 | 20230515000277 | 2023.03 |
| 022100 | 20230921000138 | 2023-09-21 | 20230515000277 | 2023.03 |
| 028260 | 20190314001170 | 2019-03-14 | 20181114001214 | 2018.09 |
| 033780 | 20190328002128 | 2019-03-29 | 20181114002469 | 2018.09 |
| 105560 | 20210427000402 | 2021-04-27 | 20181114002418 | 2018.09 |
| 207940 | 20190314001332 | 2019-03-14 | 20181113000442 | 2018.09 |

Each wrapper URL is `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=` plus
the listed correction receipt. Preserve every version and section boundary;
do not replace the original anchor with a later correction. In particular,
033780's receipt prefix differs from the displayed publication day. The latter
remains the source's date; an exact release time is still unknown. No leaf or
full report is requested by this six-GET phase.

| Code | KIND receipt | Search publication time (KST) | Exact displayed title |
| --- | --- | --- | --- |
| 352820 | 20201029000513 | 2020-10-29 18:03 | 추가상장(주식의종류변경) |
| 352820 | 20201030000296 | 2020-10-30 15:54 | [정정] 추가상장(주식의종류변경) |
| 352820 | 20201102000552 | 2020-11-02 17:35 | [정정] 추가상장(주식의종류변경) |
| 377300 | 20211207000102 | 2021-12-07 17:31 | 추가상장(스톡옵션행사) |
| 377300 | 20211221000100 | 2021-12-21 17:24 | 추가상장(스톡옵션행사) |
| 383220 | 20210628000152 | 2021-06-28 11:23 | 공개매수신고서 |
| 383220 | 20210629000582 | 2021-06-29 16:44 | 공개매수설명서 |

Each viewer URL is
`https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=` plus
the listed receipt. This seven-GET phase preserves version metadata only;
there is no contents pointer, body, attachment or redirect follow-up. Later
versions remain visible but are not backdated or fetched automatically. Pin
exact document IDs and the smallest relevant body/leaf scope after inspection.

Both phases use the two-leaf phase's serial gap, response limit, exclusive
private preservation and stop-on-failure rules, with zero automatic retries.
Their separate maxima are six and seven requests. Metadata acquisition cannot
close a correction or classify an issuance/tender event. The two unavailable
earliest identity dates remain a separate inventory; no KRX API query, trading
series or credential access is authorized by these wrapper/viewer phases.

## Seven viewer results and fixed contents-pointer scope

All seven KIND viewers returned HTTP 200 in seven wire requests, with no
retry or body request. Result SHA-256:
`1ad3f03e4a9dcf7067164131e8820c87469a96fd1d2c4e71bc2c6d97b6692442`.
The three 352820 viewers expose the same three-document original/correction
family. Preserve every receipt and version, but request a shared document
only once. The two 383220 viewers also expose corrections dated 2021-07-23;
they fall after the original search end of 2021-07-19 and cannot replace the
original evidence available before the first formation on 2021-07-20.

The next phase requests only the contents pointer for these seven unique
documents, using the pinned KIND contents URL contract. Exact document IDs,
labels and displayed publication dates must match the saved viewer bytes.

| Code | Document ID | Exact viewer label | Publication day |
| --- | --- | --- | --- |
| 352820 | 20201029001341 | 추가상장 (2020.10.29) | 2020-10-29 |
| 352820 | 20201030000684 | [정정]추가상장 (2020.10.30) | 2020-10-30 |
| 352820 | 20201102001177 | [정정]추가상장 (2020.11.02) | 2020-11-02 |
| 377300 | 20211207000197 | 추가상장 (2021.12.07) | 2021-12-07 |
| 377300 | 20211221000166 | 추가상장 (2021.12.21) | 2021-12-21 |
| 383220 | 20210628000367 | 공개매수신고서 (2021.06.28) | 2021-06-28 |
| 383220 | 20210629001422 | 공개매수설명서 (2021.06.29) | 2021-06-29 |

Maximum seven serial GETs, no automatic retries or body/attachment request,
same size/gap/private-preservation contract. Reuse all cached viewers. Keep
an unsupported pointer structure unresolved with its raw response intact;
never follow an entire tender filing automatically. Inspect returned exact
URLs before registering any notice body or bounded DART leaf acquisition.

## Six correction wrappers and bounded correction summaries

All six DART wrappers returned HTTP 200, with six requests, no retry and no
leaf request. The result SHA-256 is
`d3a6f79eb46b039c0b3680ee3c8bd924d10816ce25d9dce215d2ab3d96d92805`.
Their original/correction families and actual displayed dates remain separate.
Only the following first, bounded `정 정 신 고 (보고)` leaves are selected
next, before any summary request:

| Code | Receipt | dcmNo | eleId | Offset | Length |
| --- | --- | --- | --- | --- | --- |
| 022100 | 20230831000743 | 9415375 | 1 | 736 | 2265 |
| 022100 | 20230921000138 | 9438207 | 1 | 736 | 1893 |
| 028260 | 20190314001170 | 6546788 | 1 | 630 | 90851 |
| 033780 | 20190328002128 | 6581112 | 1 | 631 | 48609 |
| 105560 | 20210427000402 | 8044434 | 1 | 635 | 8077 |
| 207940 | 20190314001332 | 6547353 | 1 | 637 | 65256 |

Use exact DART `report/viewer.do` parameters with `dtd=dart3.xsd` after
revalidating all saved wrapper hashes, version dates and successor bounds.
Maximum six GETs and zero retries, same serial gap/size/private preservation
and stop contract. These leaves identify the corrected items and reasons;
references to another chapter in a correction table do not authorize fetching
that chapter. No full filing, financial chapter or price/volume section is
selected. Review identity-relevant changes without interpreting financial
performance. A later correction retains its own publication day and never
overwrites the original report or an earlier observation.

## Preserved history-parser failure and one-request recovery scope

The first history request returned HTTP 200 (15,329 bytes), but the broad
`거래량` rejection matched a sentence explaining a preferred-share delisting
in the requested company-history leaf. The batch stopped before requesting
017670. Preserve the failed execution, original client and response unchanged.
Raw SHA-256: `1bf96ea796004845c840404a29e1fb7495042c264c61dc4a38d9a779ff1d16d0`;
failure SHA-256: `8768de66592bcf31499051ab421ecd95d07ca9207e345bb5d25f081ede35ba4b`.
Independent offline triage (`c962d8603f9a551ff5138de59d13558ff6295cf9a85a8d77dbbe92b477b75e0d`)
verified the single history boundary and the only trigger at raw line 194:
`- 2014. 07. 2우선주 유가증권시장 상장폐지 (유가증권시장 상장규정 제65조 및 제155조에 따른 거래량요건 미달사유)`.
It is a delisting-reason sentence, with no price/volume time-series table.

Register a separate recovery artifact: revalidate all immutable hashes and
read the cached 002790 response offline, exempting only that exact sentence
on those exact raw bytes from the keyword guard. Retain all other structure
and price/volume guards. Do not re-request 002790. Request only the previously
registered, still-unrequested 017670 history leaf (one GET maximum, zero
retries), using the same size/timeout/private-output contract. If its parser
fails, retain the response and stop. This recovery records a new outcome and
does not relabel the original failure or accept any issuer alias by itself.

## Contents-pointer results and five additional-listing bodies

Seven serial contents requests returned HTTP 200 and valid official pointers;
no body was requested. Result SHA-256:
`ca0f8a9fa9ac0d5c6c606c72dca25eb64b16024af5be75fa20c15810cbd2f46a`.
Select only the following five short additional-listing notice bodies next,
with exact URLs reproduced from the preserved pointers:

| Code | Document ID | Exact body URL |
| --- | --- | --- |
| 352820 | 20201029001341 | https://kind.krx.co.kr/external/2020/10/29/000513/20201029001341/68154.htm |
| 352820 | 20201030000684 | https://kind.krx.co.kr/external/2020/10/30/000296/20201030000684/68154.htm |
| 352820 | 20201102001177 | https://kind.krx.co.kr/external/2020/11/02/000552/20201102001177/68154.htm |
| 377300 | 20211207000197 | https://kind.krx.co.kr/external/2021/12/07/000102/20211207000197/68154.htm |
| 377300 | 20211221000166 | https://kind.krx.co.kr/external/2021/12/21/000100/20211221000166/68154.htm |

Maximum five GETs, zero retries, same private preservation, 2 MiB bound,
timeout, completion-clock spacing and stop rules. Review the stated share
class, issuer, issuance reason and effective/listing dates without treating
the title alone as a restructuring event. Preserve all correction versions.
The two 383220 pointers resolve to whole tender filings (`00650.htm` and
`00652.htm`); do not request them in this body phase. Their metadata remains
open pending selection of a smaller identity/event section. No later tender
correction, full tender filing or price/volume series is requested.

## Completed bounded acquisition

The recovery completed with one cached history and one new HTTP 200 response.
Its result SHA-256 is
`fc0c4ef595ba772e856a08af6cf14ab405d9521a8684b0f20e4c11c8f4d8677b`.
The original first-history parser failure remains immutable; its response was
not requested again. Both requested history leaves are now readable under
the separately registered exception, with no alias acceptance performed by
the client.

All six correction summaries returned HTTP 200 and passed their bounded
structure checks. Result SHA-256:
`fc7e5a08a113b7f35811bababcbe08e80ea5e2cde7495683af322308a3605720`.
All five additional-listing bodies returned HTTP 200 and passed their source
structure checks. Result SHA-256:
`7400846ec928506edbe394a62eb8d2f333ec91ba0c05051dd972f530126f1c97`.

The seven acquisition roots contain 33 wire requests in total: one original
history request, one recovery request, six correction wrappers, six summaries,
seven KIND viewers, seven contents pointers and five notice bodies. All 33
responses were HTTP 200; this transport count does not erase the preserved
first-history parsing failure. No retry, original-history refetch or tender
body request occurred. The selected content workload is two histories, six
correction summaries and five additional-listing bodies. Acquisition alone
does not complete content review, source verification or identity linking.

## Conditional exact-name reconciliation from the saved history

Independent content inspection found `[SK텔레콤]` labels followed by `당사`
issuer narration in 017670's original, bounded company-history leaf, distinct
from the subsequent affiliate subsections. Its 2018 report wrapper and
overview retain the original corporation identity. Treat this as a candidate
explicit self-label, subject to the completed content verification; do not
infer an alias merely from an affiliate's mention or transliteration.

Register an offline supplemental reconciliation of only AY's fourteen
unresolved 017670 observations. Require the verified report/self-label
citations, publication strictly before each formation, the original KRX
common-share/ISIN identity and an unambiguous exact normalized name match in
the complete 35,271-row, fourteen-date basic population. Use the existing
limited legal-suffix/whitespace normalization contract; no fuzzy matching,
new abbreviation rule, guessed stock code or English token concatenation.
If the source or uniqueness checks fail, keep the corresponding row open.

Preserve all 944 rows, 27 controls, AY's 900 completed links, eight short
histories and the three unresolved 002790 observations. Any new match is an
additional source-point link only. Do not certify continuous identity,
historical eligibility, post-conversion baselines, actual historical KRX
availability or universe readiness. This phase makes no HTTP or database
request and inspects no prices or returns. A fresh verification must
reconstruct any added links independently before they are reported complete.

## Independent content findings

The independent content review checked 24 files, 88 exact source citations
and all eighteen rows in the six correction tables using its own HTML
reader. Two reader tests passed and fourteen evidence mutations were refused.
Frozen review SHA-256:
`f4794fa493f7c45b9e5a36b00a4560467645d7c3a45b041d217cd3b2f25eadb9`;
verifier SHA-256:
`264f72faed35154b0d546f6b47691e7002203aa70d909465bdf393e4ef0330a7`.

The correction tables identify omitted officer information/account labels
(022100), accounting-treatment changes (028260 and 207940), an investment
asset's corporation name/initial acquisition date (033780), and a subsidiary's
RBC ratio calculation (105560). These stated corrections provide no accepted
new issuer-identity/classification change. That bounded finding is not proof
that no other corporate event occurred. Preserve 033780's body date of
2019-03-28 separately from its displayed publication day of 2019-03-29.

The three 352820 notices describe the same additional listing: 1,777,568
common shares, issued 2020-10-15 and listed 2020-11-03, with the same code
and ISIN across versions. The corrections alter the custody/voluntary
holding-commitment wording, not those dates. Preserve the source's spelling
change in the holder name without silently repairing it. The two 377300
notices concern stock-option exercise: issuance/listing dates are respectively
2021-11-24/2021-12-10 and 2021-12-09/2021-12-24. These notices do not by
themselves establish operating-company continuity or conversion history.

For 017670, the history's `[SK텔레콤]` subsection also explicitly identifies
`에스케이텔레콤(주)` in its issuer narration before the next affiliate
subsection. The independent reader bound that subsection to the original
report receipt, corporation ID and overview's legal name. It accepted the
self-label within that source; the separate whole-population reconciliation
still determines whether observations can link. For 002790, the requested
history supplied no accepted `아모레G` self-label; its three observations
remain unresolved. No unrequested source was acquired to force a match.

## Independent source and transport verification

A separate verifier reconstructed the KIND versions/contents pointers and
DART section bounds/publication dates without importing the acquisition
parsers. It verified all 328 files in the seven acquisition roots, 166
artifact pins, and all 33 distinct actual GET URLs and HTTP 200 responses.
The minimum within-run response-completion to next-intent gap was
1.100067180 seconds. Inspection of the preserved client code also checked
that the recorded completion value was the value used for the next wait.
The two one-request history executions are separate runs; their clocks were
not joined to manufacture a spacing assertion. Twenty-one mutation tests
passed, including URL, hash, date, bounds, orphan-file and timing checks.

The original parser failure remains a failure, and its one cached response
was reused without refetching. Content conclusions remain the separate
review above. Verifier SHA-256:
`110b92580013bc8ec83a4d2e4f5e5d0b71fce0ec97a7a97dda6deca498570b76`.
The parent reran the independent verifier and exclusively preserved its
private receipt, SHA-256:
`75b5bf1ce0975866f5b381de8b9c1dd32bf40d600d81b063446094d33250a4ae`.

The seven acquisition/recovery client suites passed 111 tests in total.
Separately, the local document/index and Codex guardrail suites passed 111
tests with one skip. These tests supplement, and do not replace, the actual
source and content verifications recorded above.

## Supplemental history-link result

The offline producer completed with zero source requests. It added fourteen
017670 source-point name links while retaining AY's 900 links, all 944 rows,
27 controls and the original short-history dispositions. The status counts
are 736 dated-name links, 97 explicit listing-issue links, 67 notice-name
links, fourteen history-name links and three unresolved observations.
Thus 914 of 917 targets link; all sampled observations link for 135 of 136
codes. The unresolved code is 002790, on 2019-04-02, 2019-10-04 and
2021-04-15. Result SHA-256:
`273c1ae8685c4e533484455aed91ea02c491c1e237ce8ba2e54f76f88b96c1db`.

The producer checked the verified self-label and original legal-name/corp-ID
provenance, the strict-before publication boundary and exact-name uniqueness
in every full basic snapshot. Twenty-five tests passed. All certification
flags remain false and historical publication/`known_on` remain null. These
links do not carry the 2018 business classification through subsequent
restructuring. Independent result reconstruction is recorded separately below.

## Independent complete reconstruction and fixed archive

A fresh verifier independently rebuilt every added field from the original
raw history, legal-name overview, wrapper and full 35,271-row basic population
across fourteen dates. It did not import the producer or AX name matcher.
The full reconstructed result equals the saved producer result, including
its original AY nesting, all 944 rows, prior 900 links, three unresolved rows
and 27 controls. The eight AS-defined short-history code/formation pairs are
unchanged; this is a preservation check, not a new liquidity or price audit.
Fifteen source files and 24,560 assertions passed; twenty mutations were
refused. Independent receipt SHA-256:
`23e6e6c091f6610eec8e4507fa0729568591e964eb9961c95afcf69babcdf3fb`;
verifier SHA-256:
`fed0ce33b2cb7756fc11355278e52bc43e712e30443dec422b7dd709043aa61a`.

The frozen archive contains 374 files total: 373 payload files (including
the fixed inventory) plus `manifest.json`. Its verified expanded size is
55,497,220 bytes and compressed size is 3,818,126 bytes. The offline
preflight made no SSH request or filesystem write. Inventory SHA-256:
`5404ac17c1bc57618d784eb1d8499370247099eef6fb03ee462efbcb6556c15b`;
manifest SHA-256:
`6aed2232b345d211f92cb989cf487d39a527cd11f52cbe17b976ca1a98d1006e`.
The previously verified bounded receiver is selected for exclusive creation
at `/home/minjun4897/research-evidence/large-liquid-remaining-identity-20261008-v1`,
with regular files, exact member/hash verification, 0700 directories and
0600 files. The package includes all eight result/failure roots and the
clients, scopes, tests and independent checks. It contains no credentials or
trading database. Transfer and separate read-back are recorded only after
completion below.

Transfer and a separate SSH read-back both completed with the fixed manifest
above. All 374 files, exact membership, byte lengths, hashes and 0700/0600
permissions agree. The separate read-back finished at
2026-10-08 15:15:23 UTC (2026-10-09 KST). The collector checkout remained
clean at `4ce85d714890b87f1a57ae89d4942660e41c0483`; it was not redeployed,
restarted or rescheduled.

This completes AZ's selected acquisition, content review and supplemental
source-point reconciliation. Next establish 002790's dated abbreviation
bridge, inspect minimal identity/event sections of the two 383220 tender
filings, and resolve the two earliest-date identity gaps. Continuous eligible
company intervals, actual historical availability, post-conversion normal
baselines and price-basis/corporate-action obligations remain open. No new
large/liquid eligible universe, sizing run, portfolio comparison or strategy
promotion has occurred. The current work package's completion must not be
reported as strategy completion.

## BA erratum — F&F date role, 2026-10-09 KST

The earlier phrase "first formation on 2021-07-20" is incorrect. The
preserved AV workload identifies 20210720 as 383220's first required
lookback observation. AZ's own immutable 917-target result gives its first
formation as 20211020, followed by 20220422, 20221027 and 20230427.
The 2021-07-23 tender corrections are after the lookback's start but before
the first formation. They were outside the original search ending July 19
and were not acquired in AZ; this does not make them irrelevant to later
eligibility or accounting. BA records the source hashes and requires their
subsequent reconciliation. Original artifacts, including the derivative
`after_first_formation` label, are retained unchanged and must be interpreted
with this correction. No source-point count or return experiment changes.
