# Research Direction Task CB — dated top-20 source ranking and stored-minute coverage

## 2026-10-20 — source investigation and implementation started

This follows the operator's selected CA package and explicit continuation.
It is source/coverage Discovery, not a return comparison, final historical
eligibility certificate, power waiver or trading activation. CA's actual
inventory is complete; its stored 18 names must not substitute for the
operator's historical leading-stock target.

### Fixed scope before actual reads

`configs/research/discovery/intraday-universe-sources-v1.json` fixes the
already-spent intraday dates and thirteen monthly reference dates from
2025-08-29 through 2026-08-31. A date-only KOSPI index read over
2025-08-01..2026-09-23 validates each reference date against the last stored
index session in that month and supplies the subsequent observed sessions.
The index read has a column authorizer, range and query budget; no index
price or later date is in scope. An observed calendar is not a certified
complete exchange calendar.

Reuse the approved KRX transport for exactly four services on thirteen dates:
at most **52 attempts**, one-second response-to-next-attempt spacing, twenty-
second request timeout, 8 MiB per response, 256 MiB total and zero retries.
The normal registered research credential comes from the process environment;
the module does not open a credential file. Existing AP registration of the
private GCP research environment remains the credential-loading authority.
No KIS diagnostic credential exception is reused.

Both markets' daily market capitalizations are ranked together, using exact
integers and deterministic short-code tie breaking. The provisional source
filter is `주권`, `보통주`, domestic `KR7` ISIN and ordinary issue position.
Take twenty before checking minute availability. Do not use the old five-
trillion boundary, shrink to stored survivors, rank on future returns, replace
missing names or silently certify historical business-company status.
Source labels alone do not resolve historical SPAC/REIT/foreign classification,
liquidity or revision history. Keep these certification flags false and retain
the selected/cutoff source rows for consequential later verification.

The KRX [basic-information contract](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=PiwgMdTwmsenXhmqqxuj)
was checked again on 2026-10-20. It identifies stock class and security group,
but has no returned observation-date field. AP retains the full inspected
four-service contracts and field/unit evidence. The issuing broker's
[2025 year-end notice](https://securities.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=45922)
directly identifies December 30 as the last 2025 trading day and December 31
as the closure. This is calendar evidence, not historical OpenAPI timing.

### Timing, missingness and direct output

Keep **D+1** and **D+2** membership variants: snapshot D is assumed available
at 08:30 KST on respectively the first and second subsequent reference session.
This is an explicit research assumption, not historical proof. At each day's
08:30 selection, use only a snapshot whose `available_at <= selected_at`;
retain the older snapshot until the new one is available. Original observation,
retrieval, source, unknown finality, response vintage and assumed evidence
level travel with the selected membership.

Join these dated targets to CA's exact hash-pinned saved aggregate output.
No second minute DB query is required. Report stored rows versus no stored
rows and retain unknown absence reasons; neither state certifies complete
bars, absence of trading, halt, VI, supplier failure or API error. Do not
replace missing values with zero economic activity. No minute price, event,
forward return, order endpoint or reserved series is read.

The immediate result is the actual overlap and gaps, by date/month and timing
variant. It will determine the smallest bounded input acquisition needed for
the two selected price-pattern calibrations. Event frequency, own dispersion,
costs and final comparisons follow under existing rules; no daily-study
underpower or omitted-asset waiver transfers. No external inquiry is a blocker.

### Execution and verification

Reviewed committed source, four required CI checks, normal merge and isolated
research-only deployment precede actual execution. Register and fsync the
canonical started record before the calendar, saved actual output or network
reads. Preserve each HTTP attempt and successful secret-free response privately;
stop on the first failure and retain partial evidence. Pin source, dependencies,
specification, runtime and input bytes before completion. The unchanged
collector is not stopped, restarted, deployed or rescheduled. Resource limits
are recorded in the JSON. A source review, research-integrity review and
producer-independent actual-output verification are distinct requirements.

At this entry implementation has begun, but no CB actual source requests,
calendar reads, dated joins, strategy outcomes or GCP execution have occurred.

## 2026-10-20 — implementation and synthetic checks

`research.intraday_universe_sources` implements the registered source/coverage
task, reusing the existing KRX transport and pure formation validators. Its
explicit synthetic test file passed **58 tests in 8.72 seconds** under WSL
Ubuntu-24.04. These include a full mocked 52-response acquisition, large-integer
ranking/ties, cross-market and source/date/share defects, D+2's month-transition
behavior, column/range/query budgets, unknown coverage gaps, durable starts
before data/credentials and failures at evidence writes or completion pins.
Runtime and the complete local source/dependency/specification closure are
fingerprinted in the durable started record before actual input access.

The eight explicit document/index/count tests and ten synthetic private
transport/parser checks passed together, **18 tests in 5.55 seconds**. This
does not yet cover the subsequently added producer-to-independent-verifier
integration test. Private helper review, that integration, final source and
integrity review, actual CI, normal merge and actual execution are separate
remaining steps. No actual CB input or new strategy result was examined.

## 2026-10-20 — full CI found a handoff-document ownership defect

On source `a28c0e1a1c85e43e8b99d01881fc25a735b3833e`, the PR Python CI
completed with **1 failed, 8,571 passed and 3 skipped** in 1,672.14 seconds.
The sole failing case was the handoff document's repeated comma-formatted
inventory count, caught by `test_docs_do_not_duplicate_claude_md`. The exact
observed count already survives in CA's append-only completion record.
The handoff now points to that owner instead of repeating the measurement.
The test was not weakened and the number was not reformatted to evade it.
No GCP data access occurred while CI was incomplete or failed.

The same PR also includes CA's already completed pure detector primitives
and tests, rather than a separate source-only delivery cycle. They remain
unused by this CB acquisition and authorize no price/outcome access. The
final expanded diff needs its own separate source/integrity delta review and
all required CI checks before normal merge. The earlier review commit is
preserved as earlier evidence, not approval of the changed final source.

The explicit document-ownership test module now passes locally: **54 tests in
1.56 seconds**. CI also runs that existing guard before its full suite, so a
repeat fails early instead of waiting for the entire suite; the full suite,
network/log isolation checks and all four required checks remain mandatory.

## 2026-10-20 — actual source/coverage run completed

Final source `ece31aa0855fded314464476fc8cb0b147c5446b` passed all four
required checks on both push and PR. PR #319 merged normally at 11:54:12 UTC
as `4ca92c95630b90565262e65b0df7d17b2683ea04`; the reviewed and merged
trees are identical. The PR full Python suite passed 8,633 cases with three
skips in 1,449.56 seconds. The isolated GCP research checkout advanced to this
merge; the collector stayed clean at its existing pinned source before and
after the one registered run.

Run `56203ac9-fda3-4977-bc2a-3a0a1c53cfcd` completed with thirteen monthly
snapshots and the fixed fifty-two responses, without retries. Its private
report SHA256 is
`f1ac24fed9ee3a54dfcac0d25ef1ff339be0a872d8915d849e4369e4d5c5eb91`;
local publication receipt SHA256 is
`4fc0b834eb3db270531a32c33b80b24e5733434dcdd592ad67cc297fcb994512`.
The frozen producer-independent verifier recomputed the saved-response
joins, exact cap/share arithmetic, ranks, timing variants and stored-coverage
counts on GCP without a second database or API query.

Both timing variants contain **257 observed reference sessions**, twenty
targets each, totaling **5,140 target symbol/sessions**, with **31 unique
target codes** over the period. D+1 has **2,644 stored** and **2,496 missing**
symbol/sessions; D+2 has **2,642 stored** and **2,498 missing**. Each variant
has twelve distinct codes with at least one stored target/session and
twenty-five codes with at least one missing target/session. Those two code
sets overlap; do not sum them as disjoint populations. Stored presence is not
proof of complete or valid intraday prices, tradability or final eligibility.
No stored rows remains an unknown absence reason, not a zero-return trade.

Observed wall time was 133.870631 seconds, CPU 7.067282 seconds and peak RSS
163,324 KiB, at nice 10 within the registered bounds. The source-calendar and
CA aggregate lineage checks passed, while calendar completeness, historical
availability/classification/revision, liquidity and performance certification
remain false. The report is a source/coverage result, not a signal result.

The actual gap does not justify replacing missing leaders with stored
survivors. Task CC will preserve this complete matrix, explicitly unknown
where input is absent, for a bounded first frequency and conditional own-label
dispersion calibration. It cannot certify the full target family's power or
profitability from the roughly half-covered input. Separate receipt arithmetic
and interpretation review is pending at this entry.

## 2026-10-20 — separate actual receipt review completed

The separate research-integrity reviewer re-read only the local publication
receipt and independently recomputed the thirteen monthly sums, each
stored-plus-missing identity, the full target count and the non-disjoint code
counts. The reviewer returned PASS without blocking findings and confirmed
the receipt's own byte SHA, supplied source/specification/helper/verifier
lineage, run/merge identities, false certifications and unchanged collector
state. This receipt-only review did not independently hash absent original
report/response bytes, requery a database/API or certify extraction and
historical membership truth. The frozen remote verifier performed the separate
saved-raw-byte calculations; these two evidence scopes remain distinct.
