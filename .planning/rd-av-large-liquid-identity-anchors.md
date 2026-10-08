# Research Direction Task AV — acquire bounded historical identity anchors

## Scope — 2026-10-08

The operator continued Task AU. Establish reproducible official identity
anchors before each of its 136 ST-positive codes' first required observed
dates. This acquisition does not certify continuous intervals, calculate
signals or run a portfolio. Keep the full 944-row source population and
the existing short/control/explicit-type dispositions.

Use AU's immutable next-acquisition workload, SHA-256
`96123b6f10c8ae673515acfe9963d35de28f01a753521881d73a476bd105f882`,
and AS's join, SHA-256
`4fd4f0d34fee86a0547270fbe60f0b1e43d905ef8cf2583150db663e09e4aeb8`.
The local metadata-only export adds the AS formation-row count and distinct
ISINs per code; all 136 currently have one such ISIN. That observation does
not prove unchanged corporate identity. No trading database is copied or read.

Eight exact short-history issue-formations retain AS's already preserved
official new-listing/split-relisting notices. Their histories remain
insufficient. The other 128 codes receive a bounded regular-report search,
irrespective of liquidity pass/fail. No difficult or unmatched code may be
removed to make the acquisition look complete.

## Source route verified before bulk requests

The generic web reader again returned a connection failure for DART search.
The official browser search UI was usable, and subsequent direct WSL requests
also returned HTTP 200. No authentication, proxy change or access-control
workaround was needed.

A search for Samsung Electronics code `005930`, on 2018-11-14 with the
latest-report-only filter disabled, exposed original DART receipt
`20181114001530`. The official wrapper selected that receipt, document
`6382016`, and a child heading `1. 회사의 개요`. Selecting that child requests
`/report/viewer.do` with explicit receipt, document, element, offset, length
and DTD parameters. It returned the company-overview section, rather than the
entire report. The company name, incorporation/listing history, Korean office
and operating businesses are present. The parent `I. 회사의 개요` is broader
and is not the chosen request.

The raw wrapper and search form are preserved locally with their hashes.
A separate WSL exact-code 2018 regular-report list returned four rows and one
page, including the same Q3 receipt. These source-contract probes precede
the formal pilot/bulk archives; they are not silently counted as pilot
requests or a completed historical identity join.

## Fixed acquisition protocol

1. Run a two-code source-preservation pilot for `005930` and `000660`.
   After verifying its parser, selected versions and section boundary, run
   the already fixed 128-code regular-anchor workload in a new archive.
   Reuse or repeat pilot evidence only with explicit provenance and counts.
2. For each code, search the 365 calendar days ending the day before its
   first required observed date. Use the public DART form's regular-report
   types, retain originals and corrections, and preserve every result page
   (100 rows/page, at most three pages). Empty, ambiguous or excess results
   stay unresolved rather than expanding dates silently.
3. Select the latest reported period available within that bound, then the
   latest receipt for that period available before the first observation.
   Preserve all returned list records, the selected main-report version and
   its official receipt date. A future correction must not acquire an older
   publication date. This is an anchor acquisition rule, not a return-based
   choice or a statement about the completeness of event history.
4. Fetch only the selected wrapper and its unique child company-overview
   section using source-provided parameters. Never guess document offsets,
   request the parent section, download the whole filing, or follow links to
   price/volume sections. A missing/ambiguous child is an unresolved source
   shape, not permission to broaden the request.
5. Persist scope, workload/script/test hashes before requests. Request one
   response at a time, wait at least 1.1 seconds after each response, reject
   redirects and responses over 1 MiB, and make no automatic transport retry.
   Preserve exclusive raw files, intent/response timestamps, hashes and
   partial failures. No cookies, API key, credential, DB, collector or
   trading-mode change is part of this work.
6. Validate exact receipt linkage, pagination, corporation-ID consistency,
   selected main version, child bounds and returned heading. Save the
   section text for evidence review, with no automatic eligible-universe
   verdict. Test boundary refusals before the real run and verify saved
   evidence independently afterwards.

A filing overview is corporate identity metadata, not permission to inspect
reserved historical prices. The receipt wrapper and selected child exclude
the stock-price/volume sections. Company-group financial figures incidentally
present in a company overview do not become strategy features or outcomes.

## Acceptance and remaining boundary

An accepted source bundle must connect the official corporate identity to the
exact stock code/ISIN/share class and positively establish domestic operating-
company status. An acquired filing is not automatically an accepted bundle.
Where the overview lacks an issue identifier, the dated KRX fields and official
cross-reference/change records must supply that link.

The next continuous-interval step must reconcile classification-affecting
changes from each anchor through the code's last formation against AS's 28
dated ST tables and existing listing/merger evidence. The ST tables serve as
dated consistency observations; their retrieval dates are not historical
publication dates. No-event search output alone does not certify unchanged
identity, and no need for every full annual report is inferred.

AG and AN remain the governing identity/accounting contracts. Unknown or
conflicting classification stops replay; known insufficient history retains
its disposition. Historical interval, normal-baseline and universe-readiness
certifications remain false. No large/liquid sizing/comparison run is included.

## Execution clarification before pilot or bulk requests

An archived response that fails a listing/selection/section parser produces a
code-level unresolved record. The fixed batch may continue to the next code;
it neither retries that request nor broadens that code's source scope. A
transport, HTTP, response-size, persistence or permission failure stops the
whole run and preserves its partial evidence. Acquired and unresolved codes
must together account for the fixed workload. A pilot with an unresolved code
is not passed merely because its process completes. This distinction avoids
repeating successful requests to discover independent source-shape gaps.

## Pilot v1 transport stop and bounded v2 recovery

The first formal pilot stopped on its first listing connection with a TLS
connection reset, before receiving any HTTP response or completing any code.
Its scope, request intent, failed response record and zero-byte raw file remain
immutable. No automatic retry occurred.

Inspection found a transport difference from the successful contract probes:
the new client explicitly disabled environment proxy handling, whereas the
probes used urllib's existing environment/default route. Remove that override,
retaining redirect refusal and all source bounds. No proxy setting, credential
or machine configuration is changed or printed. After offline tests, run the
same two-code pilot once into a separate v2 root. Count the failed v1 attempt
separately; it is not a source response or completed anchor. The connection's
internal rejection cause remains unproven.

## Pilot evidence-retention failure and v3 scope

The v2 process reported six requests and two parser-unresolved codes, both
rejected by the unrelated-section marker. A subsequent command read both
unresolved records, but later WSL calls could no longer find either v1 or v2
directory under `/tmp`. No removal command was issued; the cause is unknown.
The earlier claim of preserved v1 evidence describes the write at execution,
not durable retention. Neither missing archive can now be independently
verified or counted as an acquired anchor. The execution outputs remain the
only record of the one failed v1 attempt and six v2 responses. No complete
pilot passed and no bulk request has occurred.

Before another source request, create a private persistent WSL user directory
and verify its marker through a separate invocation. That persistence check
passed. Run the identical two-code scope once as pilot v3 there, keeping
the current parser unchanged so its exact refusal can be inspected from saved
source. Any justified parser correction must first be checked offline against
that source; it does not authorize another pilot fetch or a broader section.
Archive the durable evidence to GCP before claiming source preservation.

## Durable pilot result and offline parser correction

Pilot v3 retained all six HTTP 200 responses across separate WSL invocations.
Both receipts select element 4 as the bounded company-overview leaf. The
parser's Roman-number pattern falsely recognized an overseas address beginning
`V.le` in SK Hynix and a subsidiary name beginning `I.P.S.G.` in Samsung as
unrelated report headings. The requested section bounds themselves were valid.
An independent verifier reproduced both original refusals, checked raw hashes,
selection/version/pagination/coordinates and the request ledger, and rejected
three in-memory corruptions. The original result remains two unresolved codes.

Tighten only the Roman-heading boundary: the dot must precede whitespace,
Korean text or the line end; English abbreviation letters do not form a Roman
heading. Continue rejecting both spaced and unspaced actual Korean report
headings. Regression-test those examples before using the fixed parser.
Reparse the six saved v3 responses into a separate supplemental archive with
source hashes and zero new requests. Two successfully reviewed supplemental
sections satisfy the source-route pilot gate; they do not rewrite v3's result
or certify historical identity. The full 128-code run then requests its own
evidence, including the two pilot codes, with that repetition counted openly.

## Full v1 transport stop and fixed recovery protocol

The full run acquired five sections and retained seventeen successful HTTP
responses, then stopped on wire attempt 18 with another TLS connection reset
before a response. No automatic retry occurred. Its persistent archive is
retained unchanged. This recurrence also means the earlier pilot's proxy
override cannot be claimed as the proven cause of connection resets.

Before recovery requests, preserve a separate scope and executable hashes:

- Revalidate all full-v1 intent/response/raw hashes and reuse its seventeen
  successful responses. Keep its failed attempt separately; do not redownload
  successful logical requests or count cached inputs as new wire requests.
- Replay the same fixed 128 codes, query bounds, selection rule and bounded
  leaf contract. Change no candidate, threshold or report/body scope.
- A remaining logical request has at most three new transport attempts.
  Only connection/reset/timeout failures before an HTTP response qualify;
  wait 10 seconds and then 30 seconds before those explicit retries. Every
  attempt has a distinct preserved intent/response record. This is a new,
  bounded recovery protocol, not a retroactive claim that v1 allowed retries.
- Never retry an HTTP error, redirected/oversized/malformed response or
  persistence/permission failure. Those stop recovery. Three exhausted
  transport attempts retain that code as unresolved and proceed to the next
  fixed code. Parser refusals likewise remain explicit unresolved records.
- Reconcile cached logical responses, new attempts, failed attempts, acquired
  sections and unresolved codes independently. Certify neither eligibility
  nor continuous identity intervals from acquisition completion.

The source is public and no new credential, proxy setting, paid service,
database or collector operation is introduced by this recovery.

The recovery implementation tightens the wire cap further: the existing
failed eighteenth attempt counts toward that logical request's three-attempt
lifetime cap, so it has at most two additional attempts. Other unrequested
logical responses retain the three-attempt cap. The second/third lifetime
attempts use the respective 10/30-second backoffs.

## Source-shape supplement fixed during recovery

Saved unresolved responses exposed two narrow UI/TOC distinctions, without
requiring broader source requests. Preserve the recovery's original verdicts.

- Some company cells contain one exact corporate-ID anchor plus one plain
  `IR` link to an external company site. The five inspected codes' 27 rows
  each have a single consistent corporation ID. A separate parser may ignore
  at most one extra anchor only when its visible text is `IR`, its URL is
  absolute HTTP(S), and it has no onclick action. Do not visit that link.
  Unknown extra anchors and multiple corporate-ID anchors remain refusals.
- In `006260` and `007660`, the numbered overview leaf appears both beneath
  `I. 회사의 개요` and inside financial-statement notes. Select only a unique
  numbered overview leaf fully contained within the unique non-leaf `I.`
  company-overview node's source-provided interval in the same document.
  Keep the existing exact receipt, positive-coordinate, leaf, successor and
  body-boundary checks. Never fetch the parent or financial-note alternative.

Test these distinctions in a separate pinned parser, keeping the running
recovery source unchanged. After recovery completes, fix a supplement scope
from its saved unresolved code list. Reuse already saved responses; request
only missing wrappers/overview leaves under the original date/section bounds.
Use the same finite pre-response transport retry cap and preservation rules.
Unsupported or genuinely ambiguous source shapes remain unresolved. This is
source-format repair, not a change to company eligibility or the universe.

The completed recovery fixes that supplemental population at 32 codes:
22 corporate-link refusals, four duplicate-overview refusals and six missing
listing-container refusals. Its immutable result is SHA-256
`ec3a44d56c731d8c4d2b7dc98cc25b3bd4a03344c75ee324f8c86044c392002e`.
All 128 codes were attempted: 96 sections acquired, 324 logical requests,
17 cached responses, 308 new wire attempts and no exhausted transport retry.
Together with full v1, those are 326 wire attempts. The supplemental parser
must replay all 32, including the six container refusals; an empty source is
not permission to widen dates or substitute another company. It reuses the
36 saved responses and adds only missing source stages. Unresolved containers
stay unresolved if the unchanged container contract still rejects them.

## Six empty regular-report searches: official listing-anchor fallback

The six saved responses are HTTP 200 official empty-result tables, each with
`조회 결과가 없습니다.`, not an error or access-denial page. The same bounded
regular-report query therefore supplies no anchor for these six codes. This
does not mean the companies were absent or ineligible.

Before additional requests, fix a metadata-only KIND fallback for exactly
`326030`, `352820`, `361610`, `377300`, `383220` and `402340`. Reuse AS's
official disclosure-search and listing-notice viewer/contents/body contracts.
Keep each code's same preceding 365-day interval ending before its first AU
observation; do not widen dates. Search at 100 rows/page, at most five pages
per code, and preserve every page. Select only official new-listing or
split-relisting notices for the exact current issue, then follow their
source-provided main-document versions available before that first date.
Require explicit short code, ISIN, common-share type, listing date and
publication evidence; preserve any unsupported or ambiguous result.

Use a separate private persistent archive with a scope and source hashes
saved before requests, a hard 60-request cap, at least 1.1 seconds between
responses and subsequent requests, a bounded response size and no automatic
retry. No price/chart endpoint, database, credential or collector is involved.
Do not relabel the six empty DART searches as successful periodic reports.
This uses an alternate official identity source within the same six codes and
date bounds; continuous domestic operating-company intervals remain a separate
claim requiring the subsequent change ledger.

## Completed DART acquisition and independent verification

The shape supplement completed with 26 additional sections, giving **122 of
128 regular-report anchors acquired**. Its 84 logical requests comprise 36
reused responses and 48 new HTTP 200 responses: 22 wrappers and 26 overview
leaves. No listing request was repeated and no supplemental retry was needed.
All six remaining sources are the official empty-result shape above, with
shared response SHA-256
`8512555b551412e580fc15ed049bcc87a818698f096a1083c9ed44bc254bb9e4`.

The independent verifier did not import any acquisition/recovery module. It
recomputed original versus corrected source interpretations, selected report
period/version, corporate-ID consistency, exact leaf coordinates, returned
body/readable text, all cache/source hashes and every new request. It checked
the 128-code and 32-code partitions and all false certification flags.
Minimum response-to-next-request gaps were 1.10011959 seconds in full v1,
1.100002678 in recovery and 1.10012338 in the shape supplement. The two
recovery backoffs (inherited failure and one new reset) were verified. No
transport request exhausted its cap. Three in-memory mutations to the shape
evidence (raw hash, certification, new listing request) were all rejected.

The one-off sparse-cache wrapper could in principle request a listing on a
cache miss. This was identified in read-only review after its fixed inputs
had been supplied. The observed scope contains exactly 32 cached listings and
four cached wrappers, and independent reconciliation confirms **zero** new
listing requests. The result certifies this pinned execution, not a reusable
cache-miss policy for future workloads. No archived execution source was
retroactively edited to imply a stronger pre-request guard.

Offline tests passed: 26 acquisition/parser cases, 15 bounded-recovery cases,
14 source-shape cases and four sparse-cache cases. Removing the section
boundary check in memory made its four relevant cases fail. These checks
neither called a source nor read a research database.

| DART phase | new wire attempts | HTTP responses | retained interpretation |
|---|---:|---:|---|
| three source-contract probes | 3 | 3 successful | saved fixtures, before formal pilot |
| pilot v1 | 1 | 0 | transport failure; temporary archive lost |
| pilot v2 | 6 | 6 reported successful | temporary archive lost; not independently verifiable |
| persistent pilot v3 | 6 | 6 successful | original two parser refusals; offline reparse acquired both |
| full v1 | 18 | 17 successful | five sections, then preserved transport failure |
| full recovery | 308 | 307 successful | 96 sections and 32 explicit source refusals, reusing 17 responses |
| source-shape supplement | 48 | 48 successful | 26 additional sections and six empty regular-report searches |

These are 390 recorded command-line wire attempts, including three failures
and the seven attempts whose temporary archives were lost. Browser UI
reconnaissance is separate and was not instrumented as a complete wire ledger;
390 is not a claim about all browser/network activity. Successful pilot sources
were deliberately repeated in the fixed full run, not deduplicated in this
wire count. The offline reparse and independent verifiers made no requests.

| retained evidence | SHA-256 |
|---|---|
| local metadata-only workload export | `a242000a35165907fcbb12ef27a4127cb2ceabc90716a9334fef9c102b90d79e` |
| original pilot v3 result | `7985456096687c870237aa59ea9f6a728846244a04ebabb0cd0a7a0ded74e5ff` |
| pilot offline reparse result | `3bd7be1bb408934c1382a2887522a5b7f960770ba33e0d422e13ded5ab062577` |
| full v1 failure record | `0106afc76968d94183d2dbc319ef6a9f9fe685332fcb8daae5c510c2f7c0d845` |
| full recovery result | `ec3a44d56c731d8c4d2b7dc98cc25b3bd4a03344c75ee324f8c86044c392002e` |
| shape supplement result | `171da3a5376e1222078db8fc5e404c5c1d4bb52ba2332bad02eec1a18f8a610d` |
| independent pilot v3 receipt using final verifier | `ab393e3d07b4f932ffc7137aba56cc9852fcc161beadb0874e8a26ba6819a95a` |
| independent pilot reparse receipt | `1f7f3a02e8f1426602ada08a879058c0b4731e3344ce40b3b2e2d9812f9b85b6` |
| independent full-v1 partial receipt | `ef72bbcca0121af93b2b3f607202b502b51802afdcc1586cb732b19076deea43` |
| independent recovery receipt | `36844c7f918da486940e6da51cf83d605d3fb82a36f5c294bb4a184c9636c2ce` |
| independent shape receipt | `e6f3cc5ca36fd2e57fe83087a74469977ed51dff9d6f444e717a46181580bfb1` |

## Completed six-notice fallback and full source partition

The fallback completed at **17:56:43 KST** with 24 requests, all HTTP 200:
six complete search pages (107 rows) and six viewer/contents/body triples.
There were no transport failures, retries, redirects or date expansions.
The minimum independently measured response-to-next-attempt gap was
1.100378 seconds by UTC and 1.10043882 seconds by monotonic clock.

| code and official notice | listing date | publication (KST) | source type |
|---|---|---|---|
| [326030 에스케이바이오팜](https://kind.krx.co.kr/external/2020/06/30/000798/20200630001561/68152.htm) | 2020-07-02 | 2020-06-30 17:59 | new listing |
| [352820 빅히트엔터테인먼트](https://kind.krx.co.kr/external/2020/10/13/000559/20201013001470/68152.htm) | 2020-10-15 | 2020-10-13 18:06 | new listing |
| [361610 에스케이아이이테크놀로지](https://kind.krx.co.kr/external/2021/05/07/000691/20210507001469/68152.htm) | 2021-05-11 | 2021-05-07 17:40 | new listing |
| [377300 카카오페이](https://kind.krx.co.kr/external/2021/11/01/000548/20211101001116/68152.htm) | 2021-11-03 | 2021-11-01 17:35 | new listing |
| [383220 에프앤에프](https://kind.krx.co.kr/external/2021/05/18/000357/20210518000880/68156.htm) | 2021-05-21 | 2021-05-18 16:54 | ownership-proportional split relisting |
| [402340 에스케이스퀘어](https://kind.krx.co.kr/external/2021/11/25/000535/20211125001109/68156.htm) | 2021-11-29 | 2021-11-25 16:34 | ownership-proportional split relisting |

Each selected notice exposes one main-document version and explicitly links
the short code, ISIN, common-share class and listing date. Publication precedes
the listing and the code's first required observation. The search display now
calls 352820 하이브, while the dated notice says 빅히트엔터테인먼트; the
literal `KR7352820005` / `A352820` pair supplies the issue link, not name
similarity or a backdated current display field.

Three body formats were refused by the first extractor: 361610 and 402340 have
a hyphen before 보통주; 383220 has a space before the short-code colon. The
original extractor, saved bodies and refusal records remain unchanged.
Separately preserved manual extraction reads those exact identity rows, and
an independent parser revalidated them without importing the acquisition
code. The verifier checked all 60 source/metadata files, full pagination,
versions/URLs, scope chronology, permissions and every request; it also
rejected body-hash, certification and missing-page corruptions. No source
request was made by that verification or manual extraction.

| final evidence | SHA-256 |
|---|---|
| six-notice scope | `2ef863cedfe7cc3da1848d228407c5611c3bcd1dd38db27fa5f2d8e452735bce` |
| six-notice request ledger | `106bb77a2f7c9051d8ea430516a4a9b79f0b606f195538b3cb994a23229117dc` |
| six-notice result | `a8ca29c67173e2ce088a7c75e13e946d4a30045cdcab55addfa1b6680e74ffc4` |
| independent six-notice receipt | `65a63f8d1971e58d1fca65fd4ad7841b90c693f5833a52650b03cb778ba3de78` |
| fixed-code reconciliation | `241b05b58995e3bfa0d6ec52179cd429eecf7aa6c6c3695f9b55e7fab1baafaa` |
| GCP archive manifest | `35f9a3787fc1c9a4ca009f0483c02cc46142525491e36c85b7467349be0570f3` |

The exact disjoint partition is **122 DART overviews + six new KIND listing
anchors + eight reused AS listing anchors = 136 codes**. The reconciliation
checks the pinned inputs, set equality and anchor-publication-before-first-
observation ordering. All 944 original rows remain; AS's eight insufficient
histories and the existing controls/explicit types are not reclassified or
discarded. Acquired DART corporate anchors still need exact issue linkage and
positive classification review before they become accepted interval evidence.

Durable evidence, executed scripts, tests and independent verifiers were
archived under GCP's private research-evidence directory
`large-liquid-dart-anchors-20261008-v1`: **3154 files including the manifest**,
exclusive creation, directories 0700 and files 0600, with every transferred
byte hash rechecked. The lost temporary pilot files are not in that archive.
The six-notice fallback adds 24 attempts to the DART ledger above: 414 recorded
command-line attempts overall, with the same browser/unretained-evidence
limitations. No credentials, trading database or research-return log was
copied. The pre-deployment Git check found both checkouts clean: research
`db3324f3ac7bd6572ea946c517ea5711005806c4`, collector
`4ce85d714890b87f1a57ae89d4942660e41c0483`.

**This bounded source-acquisition package is complete.** Historical
operating-company intervals, normal-baseline certification and eligible-
universe readiness remain false; large/liquid sizing/comparison runs remain
zero. Next reconcile each anchor's exact issue and classification-affecting
events through its last formation, reusing the existing listing/merger ledger
before requesting further bounded evidence. Then complete AN's post-conversion
baseline and AG's price-basis/corporate-action checks before registering the
new sizing runner. No operator choice currently blocks that work, and no
reserved prices or full pre-2019 report body was accessed in this package.

Local publication checks passed in this worktree's WSL environment: the
planning-index and documentation-rule suites reported 84 passed and one
existing skip, the repository guardrail scanner passed, and Git's whitespace
check passed. These are local checks; PR review/CI and post-merge research-
checkout synchronization are separate publication steps.
