# Research Direction Task BA — resolve the remaining dated identity gaps

## Scope — 2026-10-09 KST

The operator continued after reviewing the distinction between source-link
progress and strategy validation. AZ's PR #256 merged as
`75f2e670173079df1314f4ad900036fb75109898` after CodeRabbit approved the
exact head with no actionable comments and all four CI workflows passed
(Python: 4572 passed, three skipped). The isolated GCP research checkout is
clean at that merge. The collector remained clean at
`4ce85d714890b87f1a57ae89d4942660e41c0483`.

The immutable AZ supplement has SHA-256
`273c1ae8685c4e533484455aed91ea02c491c1e237ce8ba2e54f76f88b96c1db`.
It contains all 944 rows, 917 targets, 914 source-point links, three unresolved
002790 observations and 27 controls. Preserve every row and the eight
AS-defined short-history pairs. Neither 914/917 nor a completed source
package is a strategy-completion percentage.

Start with three independent offline inventories:

1. Find explicit dated issuer/issue evidence for 002790 (2019-04-02,
   2019-10-04 and 2021-04-15). AZ's overview, cover and history checks did
   not establish the required `아모레G` alias. Reuse cached source metadata
   to choose the next smallest relevant section or fixed exchange query.
   Do not infer aliases from familiar names, transliteration or later names.
2. Select minimal identity/event sections for the two 383220 tender filings
   whose original KIND metadata AZ preserved. Distinguish F&F Holdings as
   submitter from F&F as the affected issue, and terms/proposals from completed
   outcomes. The whole tender bodies and later corrections remain unrequested.
3. Determine whether cached identity-only evidence already covers 011790 on
   2020-12-30 and 019170 on 2020-07-10. If not, identify a finite basic-info
   acquisition scope before any authenticated request. Observation date does
   not establish when KRX historically published the record.

Pin exact queries, versions, URLs or leaf boundaries before each acquisition.
Use serial bounded clients with exclusive private preservation, zero automatic
retries and the same recorded monotonic completion value for request spacing.
Preserve failures and unsupported shapes rather than widening a parser or
fetching a whole filing automatically. Source acquisition, content review,
whole-population linking and historical eligibility are separate claims.

No price/return series, trading database, reserved pre-2019 prices, full
pre-2019 filing, collector restart/redeployment, new universe selection or
portfolio replay is in this identity-gap phase. Keep continuous identity,
historical eligibility, normal baselines and universe readiness uncertified;
historical KRX publication and `known_on` remain unresolved. Any expanded
accounting/price-basis phase needs its own concrete scope after these results.
Do not clear the sizing/comparison gate based on source-link completeness.

Reconstruct completed results independently, preserve the private evidence
on GCP, and complete CodeRabbit/CI and merge before isolated research sync.
The operator's existing rules and numeric universe boundary remain unchanged.

## Fixed acquisition scope — registered before requests

The three offline triage outputs are pinned as follows:

- Amore: `802eb7823b48282eeb4426873426dc5efcaf228d661a36fc416233e7b3e96283`.
- Tender: `cfd1f3170f295a9fe7e9b55daaa78254d02b3f6bcd73c4b6eb0b5d6d85d2d990`.
- Earliest dates: `68a2b61f4830ed3797c035d44b28dc1c8edbdd02ca7e62c2b4a07a7145654900`.

These checks used saved metadata and identity projections, not new requests.
The Amore triage did not reopen the previously refused historical raw leaf;
the final check uses the saved content-review JSON. Missing earliest-date
projections are confirmed locally; AU's two/eleven frozen exclusions were
traced through existing records, not independently recomputed from trading
responses. None of these inventories certifies historical eligibility.

Authorize this finite identity-only acquisition under the existing discovery
permission, with no automatic expansion:

1. One KIND search POST to `https://kind.krx.co.kr/disclosure/details.do`:
   `002790`, `repIsuSrtCd=A002790`, `reportNm=상장`, `reportNmTemp=상장`,
   `fromDate=2018-04-02`, `toDate=2019-04-01`, `pageIndex=1`,
   `currentPageSize=100`. This separately registered 2018 scope is issuer/
   issue notice metadata only; AY's frozen 2019 date guard remains unchanged.
   Empty, over-100 or invalid results stop without another page, query or body.
   No reserved prices or full pre-2019 filing is accessed.
2. Two KIND GETs, limited to the exact TOC paths extracted from the saved
   contents documents (first `setPath` argument):
   `https://kind.krx.co.kr/external/2021/06/28/000152/20210628000367/00650_toc.htm`
   and
   `https://kind.krx.co.kr/external/2021/06/29/000582/20210629001422/00652_toc.htm`.
   A link to a whole body plus `#anchor` is not a bounded section; any next
   source requires inspection and a separate registered request scope.
3. Four KRX basic GETs: requested `basDd` 20200710 then 20201230, each with
   `stk_isu_base_info` then `ksq_isu_base_info`, under the fixed endpoint
   `https://data-dbg.krx.co.kr/svc/apis/sto/`. Project only exact codes
   019170 and 011790 respectively, checking uniqueness across both markets.
   Read the already authorized research `KRX_API_KEY` from the private GCP
   research key file only during this run; never print/copy it or headers.
   Preserve the secret-free basic responses and project only the established
   ten AX identity fields, including the explicit abbreviation/English names.
   Do not inspect numerical share/par-value fields or request trading services.
   Requested `basDd` is provenance, not proven historical publication time.

The maximum is seven HTTP requests: one POST and six GETs. Each client has
zero retries, no redirects, exclusive 0700 output and 0600 evidence files,
durable pre-request intent, and a single recorded completion clock value for
the following 1.1-second minimum interval. KIND is bounded to 2,000,000 bytes/
45 seconds; KRX to 8 MiB/20 seconds. Stop that client on transport, HTTP,
size, parser or persistence failure, preserving non-sensitive failure
evidence. Test fixed scopes and failure paths offline before execution.

Use new `large-liquid-ba-amore-search-20261009-v1` and
`large-liquid-ba-tender-toc-20261009-v1` roots in local private evidence, and
`large-liquid-ba-earliest-basic-20261009-v1` under GCP private research
evidence. Original evidence and all source-point/eligibility limitations
remain unchanged until a separately verified supplement is written.

## Initial metadata findings and Amore event-specific follow-up

The Amore POST returned HTTP 200 but the preserved 2,090-byte response
explicitly reports zero rows (`c1773be4778aebfce399585a1c23457592e1037328f119068b5f807046c14400`).
The positive-only parser correctly stopped; its failure is retained, with
SHA-256 `d2c42092cefd6f534b8d48d45ba0f8a1aae92569d6302f59e2601c9eafb7c666`.
There was no retry, body request, accepted alias or event-absence clearance.

Both tender TOCs returned HTTP 200; result SHA-256
`ad9da2679a72c34990ea1956acc62c973b9284703b4c8d3e551f5c03f6cf94d2`.
Their fifteen/two links point to their respective whole-body resources with
fragments, not server-bounded sections. No body was requested. A separately
registered DART metadata route is needed before selecting any report section.

The previously verified AZ content-review JSON records a March 2011 legal-name
change from Pacific to Amorepacific Group. This supplies a dated event lead,
not the missing common-share abbreviation bridge. Register one new KIND
metadata POST with the same fixed form contract, but `fromDate=2011-03-01`,
`toDate=2011-04-30`, `reportNm=변경상장`, `reportNmTemp=변경상장`, and exact
`searchCorpName=002790`, `repIsuSrtCd=A002790`. The two calendar months cover
the recorded change month and the following month for the exchange notice.
This is a distinct event-specific scope after inspecting the first failure,
not an automatic widening or weakening of the original client. Maximum one
page/100 rows/one POST; all prior transport/preservation bounds apply. Stop
on empty, overflow or unsupported results; no automatic additional period,
viewer or body. No old raw history is reopened, and no 2011 prices or full
filings are accessed. Preserve at the new private local root
`large-liquid-ba-amore-name-change-search-20261009-v1`.

## Fixed DART tender metadata lookup and earliest-date result

After the two TOCs established that their links are whole-body fragments,
register one DART POST to `https://dart.fss.or.kr/dsab001/search.ax` with
this exact ordered form: `currentPage=1`, `maxResults=100`, `maxLinks=10`,
`sort=date`, `series=desc`, `textCrpCik=` (empty), `pageGubun=corp`,
`textCrpNm=` (empty), `autoSearchCorp=Y`, `startDate=20210628`,
`endDate=20210629`, `publicType=D004`. Omit `finalReport` to retain versions.
The saved search-form contract has SHA-256
`e8c4b9e2ca5389e5e5a08761ecd02cf01bbcdabd13de2bb869ed9560204959a4`:
it identifies D004 as tender disclosures and permits category/date searches
without a company selector. The two-day scope avoids confusing the acquiring
submitter with the affected issue. Its actual response remains unverified.
Maximum one page/100 rows/one POST, same 2 MB/45-second and zero-retry bounds;
stop and preserve empty, overflow, ambiguous or unsupported metadata. No
wrapper, full body or section request follows automatically. Preserve at
`large-liquid-ba-tender-dart-search-20261009-v1` in local private evidence.
Together with the earlier seven requests and the distinct Amore follow-up,
the registered maximum is now nine requests, not seven for this whole phase.

The four GCP basic GETs completed with HTTP 200 and passed the fixed identity
schema. Result SHA-256:
`01c7dce35ad9d30bf28d2dfd6cc4aebe60fd5e9e163f7875e784ad4c85cc333e`.
Each requested date has exactly one target short-code/ISIN across the two
markets: 019170 at 20200710 and 011790 at 20201230. Their ISINs equal the
later observations. Independent raw reconstruction remains to be performed;
this acquisition does not substitute for missing trading observations,
prove continuous company classification or establish historical publication.

## Selected original Amore name-change viewer

The distinct March–April 2011 search returned two rows in one HTTP 200
response; result SHA-256
`2bf3c46bd28beab841c2b470187a8af4e27692197dd5fc0e5fea13debd05a9cf`.
Select only receipt `20110415000117`, `변경상장(상호변경)`, published
2011-04-15 17:31 KST by the market division. Exclude `20110419000147`,
whose title identifies an equity-linked warrant notice. The outer search
label is the current Amorepacific Holdings name; do not backdate that label.

Register one GET to
`https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20110415000117`
at new private root `large-liquid-ba-amore-notice-20261009-v1`.
Inspect original/corrected version metadata only, under the same 2 MB/45-second,
zero-retry bounds. Contents and body URLs must be selected from its actual
response and registered separately before requests. This is a historical
exchange identity notice, not permission for pre-2019 prices or full filings.
The cumulative registered maximum is ten HTTP requests at this point.

For the DART tender-search registration above, the compact form dates mean
2021-06-28 through 2021-06-29 inclusive. Both refer to original publication
days, not a later correction date or a trading-event effectiveness date.

## DART originals found; preserve the literal submitter difference

The single DART POST returned HTTP 200 and exactly two parsed rows. The
listing is preserved with SHA-256
`f16109d91bd5ac062cbd50274c5f834985ee3c627598a43f2b15d75a392f589a`.
Selection then stopped because the source literally says `F&F 홀딩스`,
where the initial selector expected KIND's `에프앤에프홀딩스`. Keep the
original failure (`8bcf350265cf579219e98c164507bd78f208afaedd2888c07cfe5ac09d670de3`)
and both labels; do not add a general abbreviation/translation matching rule.
The metadata identifies target-company ID 01568413, F&F, and these exact
original receipts, dates and titles. This supplies section-discovery leads,
not yet a verified legal-entity or completed-transaction conclusion.

Register two GETs in this order:

1. `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20210628000095`,
   2021-06-28 `공개매수신고서`.
2. `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20210629000516`,
   2021-06-29 `공개매수설명서`.

Select these exact rows from the preserved listing after inspecting the
failure; do not repeat the POST. Preserve wrapper version/tree metadata at
`large-liquid-ba-tender-wrappers-20261009-v1`, using the same private,
2 MB/45-second, zero-retry and minimum-gap contract. Do not request any full
body or automatically follow a section. Inspect original/corrected versions
and register any exact minimal section bounds separately. The cumulative
registered maximum is twelve HTTP requests at this point.

## Exact Amore contents pointer selected from the acquired viewer

The Amore viewer returned one original version: document `20110415000315`,
`변경상장 (2011.04.15)`, with publication day 2011-04-15. Viewer result
SHA-256: `a70f01208767eae6a1bc9984d4808918a68aa3e68f5b16c69b82b0d92088b727`;
raw SHA-256: `b74ca6be86cb9e5fbfd49ae596280f73696d49a3516bed7598267b3eee5fb404`.
Register one GET to
`https://kind.krx.co.kr/common/disclsviewer.do?method=searchContents&docNo=20110415000315`,
preserved at `large-liquid-ba-amore-contents-20261009-v1`. Verify the exact
parent receipt/document/version and inspect only the returned body pointer.
No body is followed automatically. Reuse the pinned KIND transport's
stricter 30-second timeout within the registered 45-second maximum, with
the same 2 MB, no-retry and private preservation limits. The cumulative
registered maximum is thirteen HTTP requests at this point.

Preservation review identified a limit in the reused AZ save helper: it
fsyncs file contents but not their directory entries. Successful original
Amore acquisitions retain their exact files and hashes; do not repeat them
or claim a demonstrated power-loss recovery guarantee. Before further
requests, strengthen the new wrapper's save path with directory fsync
without changing frozen helper files or weakening any scope guard. Verify
intent-preservation failures stop before the wire. Final archive/read-back
verification remains separate from any crash-durability claim.

## Fixed identity notice and minimal tender sections

The Amore contents response returned the exact original notice pointer;
result SHA-256 `489ef265d943cbb059bbd3f92029137387eea258606e40a594ddd6314de84360`.
Register one GET to
`https://kind.krx.co.kr/external/2011/04/15/000117/20110415000315/68155.htm`
at `large-liquid-ba-amore-body-20261009-v1`. This is the selected exchange
name-change notice, not a periodic filing or a price series. Check its
literal legal issuer, issue code/ISIN and abbreviations separately before
accepting a source-point link. No other notice or full filing is selected.

Both original tender wrappers returned HTTP 200; result SHA-256
`b3e3166df2db31ba70647d5545143bdfb7ed300a68c94d44edffdbe19c68fb71`.
Each wrapper selects its original June receipt and retains the unrequested
July 23 correction separately, after the first July 20 formation. Their
actual node trees supply these five minimal section GETs, in this order:

1. `https://dart.fss.or.kr/report/viewer.do?rcpNo=20210628000095&dcmNo=8122476&eleId=1&offset=646&length=2632&dtd=dart3.xsd`
   — original declaration cover; next node offset 3282.
2. `https://dart.fss.or.kr/report/viewer.do?rcpNo=20210628000095&dcmNo=8122476&eleId=2&offset=3282&length=3681&dtd=dart3.xsd`
   — summary; next offset 6967.
3. `https://dart.fss.or.kr/report/viewer.do?rcpNo=20210628000095&dcmNo=8122476&eleId=11&offset=254755&length=3067&dtd=dart3.xsd`
   — purpose and future plans; next offset 257826.
4. `https://dart.fss.or.kr/report/viewer.do?rcpNo=20210628000095&dcmNo=8122476&eleId=12&offset=257826&length=15652&dtd=dart3.xsd`
   — conditions; next offset 273482.
5. `https://dart.fss.or.kr/report/viewer.do?rcpNo=20210629000516&dcmNo=8125239&eleId=1&offset=561&length=1827&dtd=dart3.xsd`
   — original explanatory-document cover; next offset 2392.

Preserve these at `large-liquid-ba-tender-sections-20261009-v1`. The target
company's 62,282-length overview, trading-situation section and explanatory
document's 323,102-length whole body are not selected. Identify the parties
and security, whether participation is voluntary, and which terms are only
proposed. Contractual offer terms are not a market-price series or evidence
of actual completion, cash payment, compulsory conversion or stockholder
eligibility. Do not model a transaction or infer that later corrections are
irrelevant. These narrow sections do not establish comprehensive event
coverage. Retain false historical/continuous-eligibility and readiness flags.

Each new client must preserve script, exact scope and parent source pins
before its fixed requests, including the strengthened directory sync. All
earlier size, timeout, retry, output and no-auto-expansion constraints apply.
The cumulative registered maximum is nineteen HTTP requests at this point.

## Offline supplemental reconciliation scope

The acquired Amore exchange notice has raw SHA-256
`ba31900bb991c016ee5fa97c1a3213c59c592aeac77295fd6bcee8b011ffd851`
and body-result SHA-256
`8fa7009ae5298828f050686f59530782d8eba93069f217ea72fce0512bd38c46`.
It explicitly changes the common-share name from Pacific to AmoreG and
binds that common issue to `KR7002790004` and `A002790`. Its two preferred
issues have separate ISINs/codes and must not enter the common-share match.
Publication is 2011-04-15; exchange name-change listing is 2011-04-20.
Neither is a replacement for the month-only legal-name change in the
already verified issuer history.

Before writing a supplement, test the exact chain against AZ's frozen
content-review JSON (`f4794fa493f7c45b9e5a36b00a4560467645d7c3a45b041d217cd3b2f25eadb9`):
its Amore issuer-only name-change table records Pacific to Amorepacific
Group in March 2011. Reuse that previously verified finding as such; do not
reopen the refused old history raw, borrow the following subsidiary's name,
or claim this phase independently reread the old source. Bind the receipt
and legal issuer to AZ's original anchor, then verify the new literal issue
names/code/ISIN against the complete 35,271-row basic population. Require
one exact common issue per formation, source publication and effective
listing strictly before each observation, and only the established limited
legal-suffix/whitespace normalization. No fuzzy translation or current-name
backdating is allowed.

The only rows eligible for a new source-point status are 002790 on
20190402, 20191004 and 20210415. Preserve all other 914 target rows verbatim,
all 27 controls and the eight short-history code/date pairs. Preserve all
earlier fields within the three rows, recording prior status and the added
proof separately. Pin the complete prior AZ result rather than recursively
duplicating its already archived top-level nested history. Retain 917
formation targets/136 codes; the two newly acquired earliest basic identities
are a separate supplement, not two additional formations or repaired price
observations. Keep every certification false and actual publication/known-on
unknown. This is not a continuous-eligibility or normal-baseline decision.

Write only after offline tests at the new exclusive private root
`large-liquid-ba-identity-reconciliation-20261009-v1`. Make no HTTP, trading-
database or return request. A fresh independent verifier must reconstruct
the new row fields, the complete result and the preserved population before
the three additional source-point links can be reported as completed.

## Date-role correction found during content review

The earlier AZ/BA narrative and derivative `after_first_formation` labels
incorrectly called 2021-07-20 F&F's first formation. The pinned AV workload
(`a242000a35165907fcbb12ef27a4127cb2ceabc90716a9334fef9c102b90d79e`)
explicitly calls it `first_required_observed_date` for 383220. The immutable
AZ result lists the actual formations as 20211020, 20220422, 20221027 and
20230427. Thus the first formation is 2021-10-20.

The original search ending 2021-07-19 remains the documented search for an
anchor before the first lookback observation. The July 23 corrections are
after that observation and before the October 20 formation. They cannot
replace information available on July 20, and cannot be dismissed as later
than formation. Their exclusion above applies only to this expressly
original-document acquisition phase. A subsequent interval/terms/accounting
review must reconcile those corrections and actual outcomes at their own
publication/effectiveness dates. Do not silently certify coverage while
they remain unreviewed.

Preserve original source files, hashes and the wrongly named derivative
fields for audit. Add this semantic correction to the independent review
and handoff; do not rewrite old responses, rerun queries, change the four
formation dates or reinterpret any return result. This corrects an evidence
label, not the operator's numeric universe or research gates.

## Completed acquisition and independent content checks

All nineteen registered HTTP requests completed with HTTP 200. Two
post-response failures remain preserved: the first Amore search's explicit
zero rows, and the tender metadata selector's literal submitter-name mismatch.
Neither caused a retry or retrospective alteration of the original response.
The completed ten acquisition roots retain their exact request scopes and
versions. The five tender-section result has SHA-256
`e88150a6457d4b441e8682bf18e512c5a50de0c7ce4022fce324f6746045debc`.

The independent acquisition verifier (`ba-independent-evidence.py`, SHA-256
`0ee2a5930cb871df10e9665f2ae453da78e118b79ba98b3ead5e374be2f0324e`)
checked 389 input hashes, ten frozen root inventories, actual request
ledgers, permissions, selected versions, raw HTML, tree boundaries and
within-client request intervals. Thirty mutation cases were refused, and
poisoning the unused KRX numeric fields left the identity result unchanged.
Its receipt SHA-256 is
`9cd044f1f6e4ef97af670d6e08a71097dc7c969a49d398af5fd888b647afb8ef`.
It explicitly retains the date-role erratum and the earlier Amore save
helper's directory-fsync limitation; successful hash verification does not
prove crash recovery. Monotonic clocks from separate client runs are not
compared to each other.

The separate tender content reviewer used its own raw HTML/text reader,
reconstructed all five visible leaves, and retained sixteen source quotes
with HTML line and table positions. It checked 97 source inputs and 66
artifact pins, refused all 97 input mutations and passed five reader cases.
Script SHA-256:
`102941c70d74864a09d79f06da822fdecda4e6ef300b85f134c0932e269fde3a`;
receipt SHA-256:
`70276c03363d53642f5ab57bb9ed5dc57d1daf7b24fbc97a1cea3e592becf52c`.

The original June documents describe F&F Holdings as offeror and F&F common
shares as the target. They propose an in-kind contribution by shareholders
who respond to the tender, with newly issued Holdings common shares as
consideration and a fractional-share cash provision. This does not establish
automatic conversion for every holder, actual participation, final quantities,
final terms, completion or cash flows. The explanatory cover's written date
is June 28 whereas its DART receipt is June 29; neither date replaces the
other. The May relisting/split is a separate event. July 23 corrections remain
unreviewed and must be included in the subsequent interval/accounting phase.

The earliest identity supplement was independently reconstructed from all
four raw basic responses: market row counts 909/1416 for 20200710 and
917/1471 for 20201230. Exact code and ISIN are unique across both markets,
with 019170 at KOSPI source row 469 and 011790 at row 86 (one-based).
All ten identity fields match the saved projections. These requested-date
identities neither add formation rows nor repair missing trading observations.
Their actual historical publication and continuous eligibility remain unknown.

## Completed source-point supplement and independent reconstruction

The offline supplement at
`large-liquid-ba-identity-reconciliation-20261009-v1/result.json` has SHA-256
`e871aa7388c046197b4b89e7185ff85f2b5ec93940165865ce663154d9f08c95`.
It adds only the three registered 002790 source-point links, retaining all
earlier fields within those rows plus explicit prior status and new proof.
The 914 earlier linked rows, 27 controls and eight short-history pairs remain
unchanged. All 917 targets across 136 codes now have source-point links; the
total population remains 944. Status counts are 736 dated-name, 97 explicit
listing-issue, 67 notice-name, 14 history-name and three rename-issue links.
The earliest two identities are separate supplements, not new formations.

The producer (`ba-reconcile-identity.py`, SHA-256
`932ad82fa7b83f63336b68cf97747f2189e1e1a44285f4a109aade9346107109`)
passed 28 mutation tests before exclusive writing. Test receipt SHA-256:
`2336336487750eaf08f86a004a04f9522fc86bd4ac940ef57ff8fe980fc5b56a`.
The unchanged prior AZ result is pinned rather than duplicated recursively.

A fresh independent verifier used a separate literal PRE reader, the prior
verified history JSON and all 35,271 basic rows across fourteen dates to
reconstruct the entire new result, including every added field, code summary,
preserved row and ten-field earliest identity. Its canonical result exactly
equals the saved producer result and SHA-256 above. It checked 88,958 factual
assertions, refused 73 negative mutations and passed two positive reader
controls. All eight output files and private permissions were verified.
Verifier SHA-256:
`144629a9ed24312311f25978ec7f8ff32a857487cef6f50d360d9abbedf9889a`;
receipt SHA-256:
`eda593940228230dd430dd21dbb48563d55f4e00e746957dea613e3897031e98`.

Neither producer nor verifier requested a source, opened a trading database
or price series, or reopened the old history raw. All certification/readiness
flags remain false, with historical KRX publication and `known_on` null.
The 917/917 count closes this sampled source-link inventory, not continuous
business eligibility, normal baselines, accounting or strategy validation.

## Frozen private archive

The offline archive preflight verified all eleven BA source/result/failure
roots and the clients, tests and independent receipts. The fixed package
contains 385 files: 384 payloads (including the inventory) plus its manifest.
Expanded size is 9,190,737 bytes; compressed size is 1,266,255 bytes.
Inventory SHA-256:
`dddc8c826caebc4c2ba55c2f425d2debddef751ff7704c07b5207740b5adef80`;
manifest SHA-256:
`14905acf7925114c3eced9a5d22d2f883a9c975de228b4928ba8331b7e9e4362`.
The preflight made no SSH request or filesystem write. An initial inventory
construction stopped on a relative verification-script path before producing
an inventory; supplying absolute paths then passed the unchanged canonical-
path guard. No source file or existing evidence was altered by this correction.

Transfer only this package to the new exclusive private GCP root
`/home/minjun4897/research-evidence/large-liquid-ba-identity-gaps-20261009-v1`
using the previously verified bounded receiver. Verify exact membership,
bytes, hashes and 0700-directory/0600-file permissions again through a separate
SSH read-back, and verify that the collector remains clean at its recorded
commit. This archive contains no credentials or trading database. Earlier
AZ/AX evidence remains separately archived and hash-referenced; this package
does not duplicate the entire predecessor archive.

Transfer succeeded, followed by an independent SSH read-back at
2026-10-08 17:43:02 UTC (2026-10-09 KST). All 385 members, bytes, hashes
and permissions equal the frozen manifest. The collector remained clean at
`4ce85d714890b87f1a57ae89d4942660e41c0483`, with no restart, redeployment
or schedule change. The isolated research checkout is synchronized only after
this documentation PR passes CodeRabbit/CI and merges.

The selected BA acquisition, content-review and sampled identity-link package
is complete. The next bounded work is to reconcile the two July 23 tender
corrections and relevant outcome evidence, then establish continuous eligible-
company intervals, post-conversion baselines and the price-basis/corporate-
action accounting required before a new sizing runner. No new large/liquid
sizing, portfolio comparison, confirmation access or strategy promotion has
occurred. No additional operator choice is required to finish this package.
