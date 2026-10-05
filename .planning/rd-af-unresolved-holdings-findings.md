# Research Direction Task AF — residual holdings are not one kind of halt

## Result and scope

Investigated on 2026-10-05 under the operator's Task AE choice. The diagnostic
replay exactly reproduced the original dataset hash and all accounting counts:
245 entries, 237 exits and eight residuals. Every residual was already due for
sale by the 2026-09-18 cutoff. This is inventory/data diagnosis, not a new
comparison, strategy verdict or corrected return calculation.

The eight comprise **three share-succession events, one SPAC liquidation,
two audit-opinion delistings and two suspended names**. Six old codes cease
printing bars immediately before their official delisting dates; the two
suspended names continue printing frozen bars through the cutoff. Within each
holding's entry-to-final-observed-bar interval, comparison to the same KOSPI
calendar found **zero missing sessions**. This supports the event explanations
below; it does not certify all prices, the full universe, or cash recoveries.

## Replay provenance

| field | value |
|---|---|
| study | `activity-holdings-audit-v1` |
| run id | `e66c47cb-967c-4cb5-8e9d-e9734637d0ee` |
| durable start UTC | 2026-10-05T14:01:54.492751+00:00 |
| completion UTC | 2026-10-05T14:04:08.117551+00:00 |
| code, merged PR #225 | `e1a3db9ef9c6fe40935038c0521c087af4a0dbc8` |
| specification SHA-256 | `84b75b24df3ba82511ff09f564a8dd4be40558560e5511f49668d298e6917e9d` |
| dataset SHA-256 | `fc8a57eae72c33a84e31a3bc96b45110b1db2903c22c3c5b6fd3c67d0e2809e6` |
| source pilot | `13adac55-56b3-460a-842d-7299105b5eca` |
| price window | 2019-01-02 .. 2026-09-18 |
| promotion-trial increment | 0 |

The GCP discovery log was read back: one started/completed pair with these
identities. This replay does not replace the first pilot. It took 133.84 seconds
and 173,492 KiB peak child RSS under the same 384 MiB / nice +15 limits. The
collector checkout stayed at `4ce85d714890b87f1a57ae89d4942660e41c0483`.
No collector was restarted, changed or given an additional writer. No reserved
pre-2019 price or post-cutoff return series was read.

## What the book actually retained

Prices below are **split-adjusted stale marks, not recovery cash**. Names come
from the host's 2026-10-05 identity snapshots and are retrospective labels.

| code / name | entry | due sale | last non-frozen mark date | mark KRW | last observed bar | terminal state |
|---|---|---|---|---:|---|---|
| 033660 우리금융캐피탈 | 2021-04-16 | 2021-10-21 | 2021-08-05 | 11,500 | 2021-08-26 | missing |
| 050540 엠피씨플러스 | 2023-04-28 | 2023-11-06 | 2023-05-04 | 359 | 2023-05-04 | missing |
| 246250 에스엘에스바이오 | 2025-05-28 | 2025-12-02 | 2025-06-09 | 1,969 | 2026-09-18 | frozen |
| 276040 스코넥 | 2025-12-02 | 2026-06-11 | 2026-04-07 | 327 | 2026-09-18 | frozen |
| 322190 베른 | 2023-11-06 | 2024-05-13 | 2024-04-24 | 1 | 2024-04-24 | missing |
| 367480 유안타제8호스팩 | 2023-04-28 | 2023-11-06 | 2023-08-17 | 3,940 | 2023-09-07 | missing |
| 435380 유안타제10호스팩 | 2024-11-18 | 2025-05-28 | 2025-05-13 | 2,135 | 2025-05-13 | missing |
| 476470 KB제28호스팩 | 2025-05-28 | 2025-12-02 | 2025-06-19 | 2,195 | 2025-07-08 | missing |

| code | missing sessions while held | frozen sessions while held | pending-sale sessions |
|---|---:|---:|---:|
| 033660 | 1,238 | 14 | 1,204 |
| 050540 | 822 | 0 | 700 |
| 246250 | 0 | 315 | 196 |
| 276040 | 0 | 112 | 70 |
| 322190 | 584 | 24 | 574 |
| 367480 | 736 | 15 | 700 |
| 435380 | 332 | 1 | 322 |
| 476470 | 294 | 13 | 196 |

These terminal holdings account for 4,500 stale and 3,962 pending-sale
position-sessions. The original totals (5,159 and 4,408) also include holdings
that later exited, so the residual table should not be forced to equal them.
All eight still have `recovery_value: null` in the unchanged simulation.

## Official event and entitlement evidence

All sources below were inspected on 2026-10-05. Publication dates are distinct
from event dates. Evidence acquired now is not backdated into a trading signal.

| code | verified event | entitlement evidence and remaining gap |
|---|---|---|
| 033660 | KRX's 2021-08-20 notice gives delisting on **2021-08-27** following the **2021-08-10** share exchange. | Woori Financial Group's issuer notice gives **1.0567393 parent shares per capital share**, plus cash for fractions under the stated terms. This is a successor holding, not an 11,500-won cash exit. Raw-share basis, successor prices, availability date and fractional treatment must be joined before a corrected book can be computed. [W1], [W2], [W3] |
| 050540 | KRX's 2023 delisting list gives **2023-05-08**, audit disclaimer due to scope limitation. | The final 359-won bar is a historical quote, not cash available on the later November due date. No compulsory cash consideration or liquidation distribution was verified in the inspected evidence; ultimate recovery stays unknown. [D23] |
| 246250 | The 2025-07-21 KRX notice maintains suspension from **2025-06-10** pending the listing review. The 2025-10-31 notice grants improvement through **2026-08-31**, followed by submission/review deadlines. | This explains the frozen series; neither notice promises a resumption price or payout. The observed frozen series through September 18 and the October 5 listed snapshot do not settle the later review outcome. [S1], [S2] |
| 276040 | KRX's 2026-04-07 notice starts/extends suspension from **13:12 that day** for audit disclaimer; April 21 grants improvement through **2027-04-10**, with suspension continuing. | An intraday halt is consistent with a traded April 7 bar followed by frozen days. The 327-won mark is not verified realizable cash. [K1], [K2] |
| 322190 | KRX's 2024 delisting list gives **2024-04-25**, audit disclaimer due to scope limitation. | The final 1-won bar is not a zero-recovery legal determination or an executable sale on the later due date. No compulsory payout was verified in the inspected evidence. [D24] |
| 367480 | KRX gives **2023-09-08** delisting by SPAC absorption. Yulchon's November 29 filing records the completed merger effective **2023-08-22**, ratio **1 : 0.8665511** (Yulchon : SPAC). | Successor shares must be carried. The earlier May filing used 0.8646779; use the completed-event evidence, not the superseded proposal. Share basis, new-share availability and fractional cash remain prerequisites for valuation. [D23], [Y1] |
| 435380 | KRX gives **2025-05-14** delisting for failure to submit a merger listing application within the deadline. | The August 11 DART meeting notice schedules a **July 24** distribution record date, **September 2** distribution and **September 10** liquidation-report meeting. Those are announced dates, not verified payment receipts. A November 12 official filing reports **2,182 won per public share** in the sponsor's dissolution history; actual payment date, eligibility and gross/net basis still need reconciliation. [D25], [L1], [L2] |
| 476470 | KRX gives **2025-07-09** delisting by SPAC absorption. The June 25 corrected DART merger decision gives **June 24** effectiveness, **0.1832341 New Kids On shares per SPAC share**, and July 9 new-share listing schedule. | The consideration is stock, not an automatic cash redemption at the stale 2,195-won mark. The simulation did not exercise appraisal rights. Join the successor and share basis before valuing it. [D25], [N1] |

[W1]: https://kind.krx.co.kr/external/2021/08/20/000408/20210820001083/68051.htm
[W2]: https://www.woorifg.com/kor/pr/announcement/view.do?seq=25
[W3]: https://kind.krx.co.kr/external/2021/08/10/000068/20210810000100/10810.htm
[D23]: https://kind.krx.co.kr/investwarn/delcompany.do?currentPageSize=100&fromDate=2023-01-01&method=searchDelCompanySub&pageIndex=1&toDate=2023-12-31
[D24]: https://kind.krx.co.kr/investwarn/delcompany.do?currentPageSize=100&fromDate=2024-01-01&method=searchDelCompanySub&pageIndex=1&toDate=2024-12-31
[D25]: https://kind.krx.co.kr/investwarn/delcompany.do?currentPageSize=100&fromDate=2025-01-01&method=searchDelCompanySub&pageIndex=1&toDate=2025-12-31
[S1]: https://kind.krx.co.kr/external/2025/07/21/000477/20250721001137/70798.htm
[S2]: https://kind.krx.co.kr/external/2025/10/31/000828/20251031001713/70780.htm
[K1]: https://kind.krx.co.kr/external/2026/04/07/001164/20260407003068/70798.htm
[K2]: https://kind.krx.co.kr/external/2026/04/21/000694/20260421000819/70780.htm
[Y1]: https://kind.krx.co.kr/external/2023/11/29/000382/20231129001046/11013.htm
[L1]: https://dart.fss.or.kr/report/viewer.do?rcpNo=20250811000009&dcmNo=10754713&eleId=11&offset=23198&length=9227&dtd=dart4.xsd
[L2]: https://kind.krx.co.kr/external/2025/11/12/000379/20251112000874/11013.htm
[N1]: https://dart.fss.or.kr/report/viewer.do?rcpNo=20250625000308&dcmNo=10697862&eleId=3&offset=18987&length=40969&dtd=dart4.xsd

## The additional universe defect

`activity_portfolio.load_panel` reads **every `scan_progress.status='done'`
code**, exactly as the original specification declared. That is not the same
as the current common-stock candidate set. The scan keeps previously fetched
rows; `krx_scan.main` explicitly reports recorded codes outside its current
selection rather than deleting them. The three SPACs above remain done rows,
and all three return false from `is_common_stock_issue` using their October 5
names and ISINs. This is a confirmed path by which the pilot admitted SPACs.
It does not establish how many other non-candidate rows the full scan contains.

The current filter is not itself point-in-time history. Simply applying today's
name or deleting only the three terminal SPACs would not fix historical membership;
a SPAC can become an operating company under the same code. Future ordinary-
equity studies need an explicit instrument/market identity rule at formation
and cache eligibility separate from fetch completion. The KONEX residual also
confirms why Task AC's sale tax is an upper-bound model, not exact per-name tax.

## What is now justified, and what remains

The choice to keep halted inventory successfully exposed unavailable exits.
The implementation is nevertheless incomplete for corporate actions: three
positions should be represented by successor entitlements, and one has cash
distribution evidence requiring settlement verification. Waiting for the old
codes to resume indefinitely is not correct accounting for those events.

No prices, universe members or recoveries were patched into the old pilot.
Next, write an explicit successor/cash-entitlement and point-in-time instrument
protocol; establish raw/adjusted share conversion and payout evidence; then
implement and validate that accounting before a newly logged sizing run.
Do not remove future casualties from the pool or manufacture final-bar exits.
The original 10.69 bp detectable-effect number remains a result of the old
stale-mark, mixed-instrument model, not a validated floor for a corrected book.
The comparison stop remains, and the activity direction is not declared closed.

Verification: PR #225 passed CodeRabbit with no actionable findings and all
required CI. Local full Python: **3,871 passed, three existing skips**; GCP
focused synthetic suite: **34 passed**. The replay's source/hash/accounting
checks passed, and metadata/date-only follow-up reconciled all eight rows.
