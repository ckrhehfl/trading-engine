# Research Direction Task AS — listing and positive classification evidence

## Scope fixed before the metadata work — 2026-10-08

Continue Task AO's chosen large/liquid scope after AR's completed acquisition.
The size/liquidity rule, 14 formation dates and all 944 capitalization-pass
issue-formation rows remain fixed. The 926 source-common-label rows span 138
codes; this is an audit population, not a certified eligible universe.
No price/return experiment, threshold change, reserved-window access or
collector change is part of this step.

The first metadata-only inventory was registered on the clean isolated GCP
research checkout at `fafd190c6da3dec21346320f99569c40a6522f57` before reading
the inputs. It joins the pinned AR candidates and AQ basic responses by
formation and explicit short code, retaining all 944 rows. It extracts only
identity/type/listing fields and the already-computed missing-history fields;
it opens no database and makes no API request. The record completed at
**11:05:35 KST** in
`<LOCAL_RESEARCH_ROOT>/large-liquid-listing-inventory-20261008-v1`.

| private inventory item | SHA-256 |
|---|---|
| scope | `6a77a97855b99e5fd49839043315493e3b3ba1f8467109e8bf6ed84a1b156394` |
| inventory | `21b0dc69ebe771b6e6f1f4dc0c31ac953672eb24cc652e97336931e5ffa4b88c` |
| AR candidates input | `09a942994bf2d4eec746a5cfb2337cf0bc47fb3d4f271d21339fb97f7a1a95d6` |
| AR report input | `f23e37b0b6613a4720c21949b63f8c3ba1ba0a6c97546478b9abae522ca983ad` |
| AQ report input | `17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b` |

Two bounded public-metadata checks follow. First, retrieve official exchange
listing notices for the eight incomplete histories, searching from 14 calendar
days before each reported listing through that listing date. Preserve search,
viewer, version selector and body evidence, and distinguish publication time
from listing effectiveness. A missing observation before a verified new issue's
listing is insufficient history under the existing rule, not a zero and not a
license to borrow predecessor observations.

Second, inspect KIND's positive `주권` category at the same 14 formation dates
in both markets. Use its observed official download form, with source totals
checked against each day's summary. Preserve every source record, but restrict
the candidate join to the existing audit population. These date-filtered source
observations cannot by themselves certify original publication time or the
continuous classification intervals required by AG/AN. Do not infer eligibility
from absence in the SPAC table or backdate current names/market/listing fields.

## What the initial inventory establishes

| AQ source label | issue-formation rows |
|---|---:|
| KOSPI / 주권 / 보통주 | 861 |
| KOSDAQ / 주권 / 보통주 | 56 |
| KOSPI / 주권 / 구형우선주 | 14 |
| KOSPI / 주권 / 신형우선주 | 4 |
| KOSPI / 사회간접자본투융자회사 / 보통주 | 8 |
| KOSDAQ / 주식예탁증권 / 보통주 / 외국기업(소속부없음) | 1 |
| total | 944 |

The nine non-ordinary-security labels are 맥쿼리인프라 `088980` in eight
formations and 코오롱티슈진 `950160` in the last formation. They demonstrate
why the share-class label alone cannot implement the domestic operating-company
rule. Retain them with explicit evidence/disposition rather than silently
shrinking the original denominator. The other 917 common-label rows are not
certified by this subtraction.

The eight incomplete rows have 254 missing session observations, all before
their basic source's reported listing date. The initial inventory alone does
not establish new-listing/relisting history; the following notice check must
supply that evidence before changing their unresolved-history interpretation.

## Separate collector operational diagnosis

The existing pre-2019 collection pass ended at **08:28:27 KST on October 8**
with `done=2545`, `absent=896`, `failed=5`, `skipped=929`. These are pass
counters, not a cumulative success count; skipped includes previously completed
and absent states. The five failures are classified as transport (three) and
rejected (two). The scanner finishes its candidate loop and returns exit 1 when
failures remain. These facts do not establish a DNS, authentication or provider
root cause: the saved failure classes omit the original exception/`msg_cd`.

The existing wrapper runs ordinary `--scan` every hour at minute 17 and skips
new starts during the weekday 09:00–15:30 KST continuous session. The observed
09:17 and 10:17 entries are start skips. If schedule/state remain unchanged,
the first outside-session tick is 16:17 KST. Ordinary scan skips done/absent
states and retries failed candidates that remain in its source universe; the
transport/rejected allowlist applies to the separate `--second-pass`, which
this cron does not invoke. HTTP-level retries do not imply the scan applies
`fetch_daily_page`'s application-layer `OPSQ0003` retry logic.

Read-only code review compared the deployed collector commit's wrapper/scan
with the inspected source. No collector code, process, schedule, credential,
reserved price database or data scope was changed. A later check may inspect
only the next pass's operational timestamps and fixed counters. Diagnosing a
persistent rejection further would require information the current failure
record does not preserve.

## Official listing evidence and the eight history dispositions

The bounded notice acquisition completed at **11:13:59 KST**: 33 requests,
all HTTP 200, comprising one UI inspection, eight search pages and 24
viewer/contents/body requests. The eight search pages contain 29 rows in total.
No retry or search-range expansion occurred. Five search pages passed the
existing strict parser. Three pages (302440, 329180 and 373220) contained
unrelated bond markers that the parser correctly refused; their complete 1/1
pages and selected listing rows were inspected manually. The refusal records
remain, and no production parser was weakened.

Each selected exchange notice identifies the current issue's common share
class, short code, ISIN and listing date. Every displayed publication time
precedes its listing and the relevant formation. Seven are new listings;
0126Z0 is expressly a relisting following an ownership-proportional split
(`재상장-인적분할`). Each viewer exposed one main-document version, and the
selected search rows had no later-correction marker. This is what those pages
exposed when acquired, not proof that no separately filed correction exists.

| formation | issue and official body | listed on | publication (KST) | observed / missing sessions |
|---|---|---|---|---:|
| 20190402 | [316140 우리금융지주](https://kind.krx.co.kr/external/2019/02/11/000659/20190211002089/68152.htm) | 2019-02-13 | 2019-02-11 18:28 | 33 / 27 |
| 20210415 | [302440 SK바이오사이언스](https://kind.krx.co.kr/external/2021/03/16/001975/20210316004960/68152.htm) | 2021-03-18 | 2021-03-16 17:55 | 20 / 40 |
| 20211020 | [323410 카카오뱅크](https://kind.krx.co.kr/external/2021/08/04/000380/20210804000905/68152.htm) | 2021-08-06 | 2021-08-04 17:13 | 47 / 13 |
| 20211020 | [259960 크래프톤](https://kind.krx.co.kr/external/2021/08/06/000476/20210806001111/68152.htm) | 2021-08-10 | 2021-08-06 17:30 | 45 / 15 |
| 20211020 | [329180 현대중공업](https://kind.krx.co.kr/external/2021/09/15/000400/20210915001176/68152.htm) | 2021-09-17 | 2021-09-15 16:13 | 18 / 42 |
| 20220422 | [373220 LG에너지솔루션](https://kind.krx.co.kr/external/2022/01/25/000697/20220125001917/68152.htm) | 2022-01-27 | 2022-01-25 17:32 | 56 / 4 |
| 20240510 | [443060 HD현대마린솔루션](https://kind.krx.co.kr/external/2024/05/03/000722/20240503001523/68152.htm) | 2024-05-08 | 2024-05-03 17:59 | 2 / 58 |
| 20251201 | [0126Z0 삼성에피스홀딩스](https://kind.krx.co.kr/external/2025/11/20/000725/20251120002094/68156.htm) | 2025-11-24 | 2025-11-20 17:32 | 5 / 55 |

The offline join confirms every missing date precedes the corresponding
verified current-issue listing; **226 observed + 254 missing = 8 × 60**.
The disposition is now **insufficient history since verified current-issue
listing**, for all eight rows. This explains the gap without fabricating
observations or replacing the issue with a predecessor. Their original AR
`liquidity_pass=null` and candidate rows remain unchanged. The disposition
means no entry at those formations under the fixed full-window rule; it does
not certify their operating-company intervals or all other input coverage.

## Positive source observations across the fixed formations

KIND's [historical classification screen](https://kind.krx.co.kr/corpgeneral/listedIssueStatus.do?method=loadInitPage)
links separate `ST` (주권), foreign, investment-company and SPAC categories.
The inspected official download uses `searchListedIssueStatDetailSub`,
`forward=listedissuestatdetail_down`, `currentPageSize=3000`, `pageIndex=1`,
`detailType=1`, and the requested date/market/category. Its rows are **company
records**, checked against the summary's company count, not its security count.

The 14 fixed dates yielded 28 complete ST company tables, with **32,008
company-date-market source rows** in total. These are source totals across
both markets, not our candidate denominator. The acquisition has 42 logical
sources (14 summaries and 28 details). Its completed v3 run reused three
responses and made 39 new requests. Including five initial UI/structure probes
and one new response from v1, the type-source wire count is **45**. Together
with the 33 notice requests, AS made **78 public metadata requests**, all
recorded HTTP 200, and no authenticated KRX request or research DB read.

Two interrupted local interpretations remain preserved. V1 stopped on a
historical KOSDAQ table whose displayed market was current KOSPI. V2 stopped
on a provenance-filename formatting error before any new request. V3 retains
current display fields separately rather than certifying them historically.
Independent inspection found **78 source rows** whose displayed market differs
from the query market and whose displayed listing date is later than the query
date. For example, the 2019-04-02 KOSDAQ table displays 엘앤에프's 2024-01-29
KOSPI transfer date. Thus these display fields cannot reconstruct historical
market or initial-listing history. The query filter and displayed fields must
not be conflated.

Joining by formation and explicit common-issue code produces:

| diagnostic partition within the unchanged population | rows |
|---|---:|
| source-common-label rows in the original population | 926 |
| positive KIND ST observations, across 136 distinct codes | 917 |
| common-label rows absent from ST, with the explicit API types above | 9 |
| ST-positive rows with complete liquidity histories | 909 |
| ST-positive rows with verified short listing histories | 8 |
| complete ST-positive rows passing liquidity | 876 |
| complete ST-positive rows below liquidity | 33 |
| ST-positive rows passing liquidity and the formation tradability proxy | 875 |

The partitions reconcile: **917 + 9 = 926**, **909 + 8 = 917** and
**876 + 33 = 909**. There are no API-group/requested-market conflicts among
the 917 ST-positive rows. All nine ST-absent rows are the previously identified
eight investment-company observations and one foreign DR observation. Their
absence alone is not used as classification evidence; the explicit API labels
remain attached. The 18 preferred-share rows also remain in the full 944-row
artifact. No eligible-universe output or historical interval was created.

These positive date-filtered observations improve source coverage. They do
**not** establish original publication times or continuous operating-company
history over the activity baseline. The notice timestamps are KST publication
times displayed by the archived KIND searches, not independently certified
historical dissemination times or a full classification ledger. Classification
`known_on` remains unset and certification/readiness remain false.

## Evidence preservation and verification

Public raw evidence, request records and acquisition scripts are preserved in
`<LOCAL_RESEARCH_ROOT>/large-liquid-kind-evidence-20261008-v2`.
The archive contains 127 files. The preceding v1 archive attempt stopped at
the 7.25 MB parsed result exceeding its raw-response size cap; its partial
files remain. V2 reused the identical archive, applying the larger limit only
to that identified aggregate JSON. No source query was repeated for archiving.

The candidate join is in
`<LOCAL_RESEARCH_ROOT>/large-liquid-listing-join-20261008-v2`.
Its first attempt retained a scope but no result after refusing the unrecognized
relisting enum spelling. V2 used the actual inspected enum, without broadening
the evidence condition. It verified all 127 archived file hashes, the pinned
private inputs, code/ISIN/listing matches and the population identities. It
completed at **11:22:39 KST**, making no API request or DB read.

| evidence item | SHA-256 |
|---|---|
| notice scope | `56d254d81413b2cb4293b95280e661a45715e2940847c3ddbf1488c8f6b959d8` |
| notice request ledger | `43db6155e39401f7fd6123bb7f610e4c30781c159972ec2c60bb1be921942ce7` |
| verified notice result | `44186f066bfa0127ca8358b271d0dcc98a631f82060ac68d35974b869e163001` |
| completed type-source scope | `62646716166132ea5ad53254384b09058d973015e85174c8cc87610b398c9297` |
| completed type-source ledger | `8c381b4eaed96251ef4de1b7e44c9a7baa8b5e7117203e65fcdf8c360183d709` |
| completed type-source result | `daccb0c3c6380d443d91aa25151358bb1201e658c8582108fda8a6ab89a88fa2` |
| public archive | `3e2b03955dc5ef3242d1e6082c8a550241c741be6c30c14de1921f9dc22ae100` |
| GCP archive manifest | `826ee4e571fe0c476c06eb59cee14d862cb581ed42bea825ab79ab76d3ebfb2c` |
| candidate-join scope | `448dad70805e65e4696e48252a9326c4102918ad4bd817bdc7424502ff0d862d` |
| candidate-join result | `4fd4f0d34fee86a0547270fbe60f0b1e43d905ef8cf2583150db663e09e4aeb8` |
| separate offline candidate/calendar verification | `34074afcecd34455b2ffe5939c925676e452f37364ba50b326079d5fc81943ac` |
| independent public-evidence verification | `66ed25ec17529dac640a9a900aca907ff376551506b75d88a6894ab58659c2be` |

An independent offline verifier used a separate stdlib HTML parser and
recomputed all notice links/fields, source hashes, request bounds, pagination,
versions, the three strict-parser refusals and all 32,008 type rows. It found
no discrepancy. The minimum notice response-to-next-request gap is 1.100045
seconds; the type run's minimum recorded start-to-start gap is 1.050055
seconds. The type ledger has no response-completion timestamps, so it cannot
verify a response-to-next-start gap or the final response's exact finish time.
This independent pass did not access GCP/private candidate inputs. Its final
result and verifier remain in the local ignored `var/public-kind-as-verification/`
directory; the verifier SHA-256 is
`991e46823bc6d57fc8d8d05f4921fc2d8fca4b08c67ebc065db85cf7d4d6a576`.

A separate offline recomputation on GCP at **11:26:07 KST** independently
formed each preceding-session window from the pinned AP calendar. For all
eight cases, the missing-date set equals exactly the window dates before the
verified listing, and the complementary count equals the observed count.
It also recomputed the population partitions from the pinned candidates and
public metadata and checked that every joined classification remains
uncertified. It opened no DB, made no request and did not repeat AR's raw-price
median verification. Its receipt is `verification.json` in the join root.

## Completion and next gate

AS completes the **8/8 short-history explanations** and **28/28 positive source
tables**, retaining the complete original population. This closes AR's unknown
cause for those eight missing histories; it does not make their histories
complete. AO's source-semantics/classification gate and full dated candidate
audit remain open. The fixed rule is unchanged.

Next establish the remaining candidates' dated domestic operating-company
intervals and when their evidence was public, then map the full required
lookbacks to the price panel. The 136-code ST-positive subset is a bounded
evidence workload, not permission to ignore the nine explicitly typed records
or the preferred-share controls. Preserve AN's distinct post-conversion
baseline and AG's corporate-action accounting before registering a new sizing
runner. Large/liquid sizing and comparison runs remain **zero**. Review, merge
and isolated research-checkout deployment are the final publication steps for
this record; the collector remains unchanged.

### Local publication checks

WSL documentation regression passed **84 tests with one existing skip** after
correcting the new index entry to match the document title exactly. The
repository guardrail scanner and diff whitespace check passed. Independent
document review corrected an overstatement about historical dissemination
times and distinguished immutable AR status from AS's later gap explanation;
the final recheck found no remaining issue. The tracked change contains only
this evidence record, its index/count updates and the living handoff. No
runtime, collector, risk setting or trading behavior was modified.
