# Research Direction Task AI — resolve the eleven merger code pairs

## Scope and result

The operator continued Task AH on 2026-10-06. Its eleven ambiguous names are
now connected to **eleven distinct old KONEX common-share codes and eleven
surviving SPAC codes**, using individual exchange delisting, additional-listing,
rename and completed issuance reports. Neither code is discarded. This resolves
the roles in those eleven pairs, not the historical classification of the full
candidate pool or the accounting readiness of a corrected portfolio.

The accompanying [evidence ledger](rd-ai-merger-identity-evidence.json) contains
the eleven mappings, exact decimal share ratios, separate dates, and 53 public
source versions with URLs, retrieval timestamps and SHA-256 hashes. It is a
curated research record, **not runner input**. `known_on` remains unassigned and
`identity_periods_ready` is false for every event. No code, price database,
experiment log, strategy specification or collector was changed by this step.

## Code roles and dates

The ratio is **new raw common shares for one old raw common share**. It does
not apply to adjusted-price units, preferred shares, or a SPAC shareholder's
existing shares. The exchange notices explicitly identify the six-character
codes; no code is manufactured from KIND's five-character issuer identifier.

`merger day` below transcribes the completed report's **합병기일**. It is not
a legal opinion that this date alone determines statutory effectiveness.
Registration and listed availability are separate facts. Listing dates agree
across the exchange notices and Task AH's historical list. They do not prove
that a particular holding could execute a sale on that day.

| name | old operating code → surviving code | merger day | registration | merger shares listed / old code delisted | raw ratio |
|---|---|---|---|---|---:|
| 포인트엔지니어링 | 176560 → 256630 | 2019-07-01 | 2019-07-02 | 2019-07-16 | 7.5000000 |
| 자비스 | 230400 → 254120 | 2019-10-31 | 2019-11-01 | 2019-11-15 | 3.4030000 |
| 한국비엔씨 | 226610 → 256840 | 2019-11-15 | 2019-11-19 | 2019-12-03 | 6.0305419 |
| 소프트캠프 | 210610 → 258790 | 2019-12-17 | **conflict: 12-18 / 12-19** | 2019-12-30 | 4.1300000 |
| 카이노스메드 | 220250 → 284620 | 2020-05-19 | 2020-05-20 | 2020-06-08 | 6.9000000 |
| TS트릴리온 | 284610 → 317240 | 2020-12-15 | 2020-12-17 | 2020-12-30 | 1.22300000 |
| 엠에프엠코리아 | 251960 → 323230 | 2020-12-15 | 2020-12-16 | 2020-12-30 | 1.2632207 |
| 원바이오젠 | 278380 → 307280 | 2021-01-22 | 2021-01-25 | 2021-02-09 | 10.1605000 |
| 휴럼 | 284420 → 353190 | 2021-07-13 | 2021-07-15 | 2021-07-27 | 6.1500000 |
| 원텍 | 216280 → 336570 | 2022-06-14 | 2022-06-15 | 2022-06-30 | 12.8635762 |
| 지슨 | 289860 → 446840 | 2025-07-29 | 2025-07-30 | 2025-08-14 | 2.8500190 |

Every row's `evidence` object identifies the exact source version for each role.
The old-code notices specify absorption as the delisting reason and name the
SPAC whose merger shares are to list. The additional-listing notices identify
that SPAC's common-share code; rename notices connect it to the operating name.
Completed reports supply the share-exchange direction and separately labelled
merger day. Later issuer reports corroborate registration where the completion
report still labels it as scheduled or applied for. Those later reports cannot
supply contemporaneous knowledge retroactively.

## Discrepancies retained instead of silently resolved

- **Xavis / BNC / Kainos:** retrospective summaries sometimes use the registration
  date as the merger day. The completed reports instead say 2019-10-31,
  2019-11-15 and 2020-05-19 respectively. Kainos's 2026 quarterly report itself
  contains both the separately labelled dates and a passage calling May 20 the
  merger day. These are field-level assertions, not interchangeable timestamps.
- **Softcamp:** the [2019-12-18 completion report](https://kind.krx.co.kr/external/2019/12/18/000168/20191218000540/10810.htm)
  reports registration on December 18; the [2020-03-06 earnings notice](https://kind.krx.co.kr/external/2020/03/06/000947/20200306002134/70443.htm)
  says it completed on December 19. `registered_on` stays null. Exchange
  issuance/rename dates of December 18 do not independently settle registration.
- **TS Trillion:** the [original 2020-12-29 listing notice](https://kind.krx.co.kr/external/2020/12/29/000952/20201229002398/70791.htm)
  and [2021-08-30 correction](https://kind.krx.co.kr/external/2021/08/30/000547/20210830001434/70791.htm)
  are both preserved. The correction changes lock-up details, not the code or
  listing day. A viewer opened with the original receipt also exposes the later
  version. Selecting its latest document and retaining the old publication date
  would introduce future information.
- **Wonbiogen:** one retrospective paragraph says the SPAC disappears. The
  exchange notices and completed report identify the retained SPAC code. That
  contradictory prose is recorded, not used to reverse the verified code roles.
- **GITSN:** the completion report reduces merger-share issuance after 2,574
  appraisal-acquired treasury shares receive no new shares. Public common-share
  entitlement uses the explicit 2.8500190 ratio, not the quotient of total new
  and total old shares. Its proposed fractional-share cash payment is not proof
  of actual net cash paid to a research holding.

The Softcamp registration discrepancy is an unresolved evidence item, not a
reason to discard its old code, assume zero recovery, or move a holding to cash.
Before using any of these events, verify the accounting trigger, applicable
share class, historical knowledge and daily tradability under Task AG.

## Acquisition and preservation

The public KIND [detailed disclosure search](https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain)
was queried by each six-character code over the months around its merger.
The observed POST fields were `method=searchDetailsSub`, `forward=details_sub`,
`currentPageSize=100`, `pageIndex=1`, `orderMode=0`, `orderStat=D`,
`searchCodeType=number`, `searchCorpName=<code>`, `repIsuSrtCd=A<code>`,
`fromDate` and `toDate`. Search results are discovery aids, not a completeness
claim about all filings or corporate actions. No price/chart link was followed.

Each observed receipt was opened in KIND's disclosure viewer. Its document
number was resolved through `searchContents`; the actual external filing body,
not its table of contents, was saved. Correction versions remain separate.
The document's publication date is distinct from retrieval on 2026-10-06 and
from all event dates. No intraday availability or earliest announcement date
is inferred from these sources.

Public response bytes and a copy of the ledger are preserved outside the
collector checkout at
`/home/minjun4897/research-evidence/activity-merger-identities-20261006/`.
The source hashes in the ledger address those exact bytes. This transfers only
public filings into GCP; it transfers no trading database, log or credential.

## Raw/adjusted quotation acquisition prepared next

No raw quotes were fetched here. Task AH's isolated research environment lacks
KIS credentials, and the existing manual-entry boundary in
`docs/paper-trading-runbook.md` still applies. A new helper that reads the
collector's `.env` is not authorized by this metadata investigation.

The first bounded acquisition should test the already identified three bridges
from Task AF before any full-history download:

| old code / last observed mark | successor / listed availability |
|---|---|
| 033660 / 2021-08-05 | 316140 / 2021-08-27 |
| 367480 / 2023-08-17 | 146060 / 2023-09-08 |
| 476470 / 2025-06-19 | 462310 / 2025-07-09 |

This is a concrete acquisition specification, **not an implemented CLI or a
successful price-basis verification**. Its implementation and review precede
the operator's credential-entry step:

1. Use one existing `KisSession` against `PAPER_HOST`, with the approved manual
   environment-entry procedure. Reuse the existing protected token cache.
   Do not change credentials, hosts, collectors or schedules. Run outside the
   protected KRX session with serial requests and existing pacing/retry limits.
2. For each of the six fixed anchor dates, request that exact day in both
   `ADJUSTED=0` and `RAW=1`. For the same request shapes, first check nonsense
   codes `999999`, `ZZZZZZ`, `000000` answer empty and positive control `005930`
   answers with exactly that day. A failed control stops the batch.
3. The initial matrix is 60 logical page calls: six dates times five codes
   (three negative, one positive, one target) times two bases. Repeat the twelve
   target pages once, after the first matrix, for 72 logical calls total.
   Existing transport retries may make the HTTP count larger; persist both
   counts and stop on exhausted retries. No adaptive date expansion or wide
   probe into the reserved window is permitted.
4. Reject duplicate/out-of-range days, silent caps, non-finite or non-positive
   prices and inconsistent OHLC; retain rejected-run diagnostics. An empty
   target is unresolved, never zero recovery. Require target repeat equality.
   Save only quotation data, request parameters, host, UTC acquisition times,
   committed code and input/output hashes, never headers, tokens or secrets.
   Create a fresh evidence directory without touching collector databases.
5. Compare each fresh adjusted anchor with the archived adjusted scan value
   used by the lot. A current raw/adjusted pair alone cannot certify a bridge
   against an older adjusted snapshot. Any mismatch stops use of that basis;
   it must be explained with the vendor's adjustment convention and intervening
   corporate actions. Do not equate a repeated answer with independent proof.
6. Record the diagnostic purpose and fixed matrix before price access. This
   acquisition computes no returns, rankings, sizing or parameter selection.
   The later corrected portfolio still needs its own reviewed preregistration,
   durable started/completed trial records, and Task AG's complete input gates.

Current `data.backfill_kis` supports explicit bases and a separate database, but
does not implement this fixed control matrix, date-response checks and evidence
bundle. Running it broadly now would not satisfy this specification. The next
implementation must remain covered by `test_kis_probe_cannot_trade.py`; placing
a credentialed client outside its inventory is not an acceptable shortcut.

## Remaining research work

These eleven resolved pairs seed the historical security master. The other
104 name candidates in Task AH are still candidates, and the full bar-eligible
pool needs dated instrument-type evidence, including pre-conversion lookbacks.
All held intervals, including closed lots and newly created successor lots,
need a systematic action audit. Raw-price bridges, unknown liquidation recovery
and the Softcamp discrepancy remain open. The v1 cost-floor stop remains in
force; no comparison, corrected sizing run, holdout access or strategy promotion
occurred in this task.

## Verification

Local verification passed **80 tests, one existing skip**, covering the planning
index and existing accounting/acquisition modules. The repository scanner and
both guardrail suites passed (27 and 34 tests). A separate check found all
eleven source chains, all 22 explicit stock codes in the appropriate exchange
notice bodies, and all eleven exact ratios in the completed report bodies.
These checks establish internal traceability, not vendor-price correctness or
completeness of the historical security master.

GCP preserved 54 files (53 source bodies plus `ledger.json`) and verified all
53 source hashes. The ledger SHA-256 is
`1a4fef05b68e6b9071a59a1499885ed4621a8bcabf2a42b29a046c29f9482434`.
Collector HEAD remained `4ce85d714890b87f1a57ae89d4942660e41c0483`, with a clean
working tree before and after evidence preservation. No collector deployment,
schedule change or research checkout update was needed for this metadata step.
