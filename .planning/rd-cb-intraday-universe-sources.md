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
