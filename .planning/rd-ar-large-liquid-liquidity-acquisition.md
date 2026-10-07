# Research Direction Task AR — audit fixed liquidity histories before selecting a universe

## Discuss and boundary — 2026-10-07

The operator asked to continue after Task AQ. Work has started on the next
bounded input audit, before reading its trailing-value medians or outcomes.
AQ's size/liquidity rule, calendar geometry and missing-data policy are fixed.
This task implements that rule's liquidity diagnostic; it does not change the
cutoffs or establish domestic operating-company eligibility. CLAUDE.md and
Tasks AG/AN/AO/AQ retain their research and accounting gates.

Implement `data.krx_liquidity_audit` with a fresh-context task and synthetic
tests. Reuse the approved transport/private-evidence helpers and extract only
the necessary pure row validation from AQ, preserving AQ's original date
guards and wire behavior. Do not build a generic ingestion framework, alter
the old portfolio, copy databases, or modify collectors and credential files.

## Inputs and fixed request matrix

The CLI accepts only `--calendar-manifest`, `--formation-dir` and
`--output-dir`. These are private evidence locations, not a date/host/key
configuration surface. Pin the actual input bytes before using their contents:

| input | SHA-256 |
|---|---|
| AP calendar manifest | `ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882` |
| canonical calendar date list | `fceaf0d8748f203001afbf11a66c44aa756f7f55b21e22aaee04152186506ce4` |
| AQ successful formation report | `17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b` |
| AQ started receipt | `2bce0c4b916a1f67415e91dd1b4372ae8425e518d98624661725c58407ee48e9` |
| AQ request ledger | `f85c8a4403eaacde5d6fd0195054568262cbbb4cdab98ffdc846ab45933f2487` |

The existing manifest was rechecked read-only at the start of this task. It
contains 1898 `YYYYMMDD` calendar dates, the 14 AQ formation dates and 1736
proposed minimum request entries. Its `not_execution_authorization` marker
remains true: this new preregistration and reviewed implementation define the
actual acquisition, not that old inventory. Do not rewrite the input manifest.

Recompute AQ's formation geometry from the ordered unique valid dates and
require exactly the AQ dates. For each formation index `f`, select
`C[f-60:f]`. Their union is **840 distinct trading dates**, excludes every
formation date and lies inside the spent discovery interval. The new matrix
contains **1680 requests maximum**, date ascending, then `stk_bydd_trd` and
`ksq_bydd_trd` in that order. Check its membership against the old proposed
matrix without inheriting that inventory's different service sort order.
No basic-information service, extra session, pre-2019 date, date fallback,
retry or automatic resume is part of this run.

Reuse AQ's 56 saved formation trading/basic responses. Require its complete
successful report, started receipt and complete attempt/response ledger;
verify their fixed request matrix and every required raw response against the SHA in
that pinned report, using fixed expected filenames. Re-audit the 14 formation
days before requesting any new data. Reject missing, changed, malformed,
oversized or unsafe/symlinked inputs before the network. Inputs and output must
remain private and outside the checkout; files/directories use 0600/0700.
Other caches, including AP's initial samples, are not reused in this version.

The diagnostic target denominator is **all 944 AQ capitalization-pass
issue-date rows**, retaining the **926 common-label rows** as a separately
reported subset. Do not prefilter by a present-day name, final classification,
future performance or a desired candidate count. The same short code can move
between markets; matching across both markets on each date is allowed only
after rejecting duplicate codes across those markets. Another code cannot
provide a missing history observation.

## Acquisition and resource controls

The specification identifier is **`krx-large-liquid-liquidity-v1`**. Require a
clean committed checkout and persist its SHA, input hashes, complete matrix,
thresholds and limits before the first request. Keep the existing fixed host,
20-second timeout, 8 MiB response ceiling, environment authentication and
at least one second from a response to the next attempt. Record and sync each
attempt before sending it and record its response status afterwards. Retain
secret-free valid-envelope raw JSON before field validation; never persist
HTTP error bodies or emit exception text, headers or credentials.

Validate every returned row, not only target codes: requested date, short-code
format, required string fields, correct market, nonempty snapshots, numeric
bounds, integral volume/shares and exact raw-close-times-shares capitalization.
The extracted pure validator may validate a registered post-2019 lookback date;
it grants no permission to request arbitrary dates. AQ's executable still uses
only its original formation dates. Preserve the existing raw-price/regular-
session versus all-session value distinction; do not impose a VWAP/OHLC test.

Stop on the first HTTP, transport, schema, arithmetic, duplicate-identity,
input/evidence-write or resource failure. HTTP 429 is a stop, not permission
to loop. Preserve all partial evidence and counts, with no automatic restart.
The [current KRX terms][krx-terms], checked 2026-10-07, specify a per-key daily
limit of 10000 requests. The bounded request count does not claim knowledge
of unrelated use of the same key; a service rejection still stops this run.

Before each request, require at least **2 GiB free** on the output filesystem;
refuse before recording/sending the attempt if below that stop threshold.
Use a single stream of requests, not parallel fetches. Retain only current-day
market rows and the target histories needed for the fixed medians; do not load
every full-market response into memory. The GCP invocation uses the existing
low-priority research process with a 384 MiB address-space cap. No background
collector is restarted, replaced, rescheduled or redeployed.

## Liquidity and missing-history semantics

For each formation/target, require exactly one observation for each of its
60 preceding sessions. Use `ACC_TRDVAL` exactly, with Decimal arithmetic and
enough precision to compute the mean of sorted positions 30 and 31 without
rounding at the default context precision. Pass at or above AQ's fixed
**KRW 10000000000** boundary. The formation observation cannot enter that
median. Retain a verified historical zero; do not shorten or extend the window
to collect nonzero/normal observations. This is distinct from AN's normal
post-conversion activity baseline.

A target missing from an otherwise valid market-day response has **unresolved
history**, not zero turnover. Record all missing dates, separating those before
and on/after the basic record's reported `LIST_DD` as diagnostic counts only.
That field alone does not certify an initial listing or conversion date, so
even a missing-before-listing case is not silently declared a verified
ineligible new listing. Do not calculate a median or a liquidity pass from
fewer observations, borrow a predecessor code, or remove the issue from the
denominator. A missing target does not invalidate the entire payload: finish
the fixed matrix unless a structural/acquisition failure occurs, then report
the incomplete-history denominator separately. Final eligibility stays blocked.

For each complete window, report counts of observed zero turnover, equal OHLC
with zero turnover, and zero open/high/low values separately. A zero OHLC field
is not proof of a legal trading suspension. The formation tradability proxy
requires positive OHLC and excludes equal OHLC with zero turnover. A positive-
turnover limit-locked formation is retained at this screen; the later fill
simulation has its own gate. No extra positive-volume eligibility threshold
is introduced. Report liquidity and formation tradability independently so a
formation failure does not hide a missing liquidity history.

## Outputs and completion conditions

Write private per-target diagnostics to `candidates.json`, including formation,
code, source common label, observed/missing dates and counts, the three zero/
frozen diagnostics, exact median only for complete histories, liquidity pass
and formation tradability/reasons. This artifact is a diagnostic input, not an
eligible universe. The final report records its hash and only aggregate
per-date/total counts for all size passes and the common-label subset.
No names, codes, per-issue market caps or per-issue median values go to stdout
or the public result document. Raw responses remain private KRX evidence.

Publish artifacts atomically and fail closed on publication errors. Partial
acquisition cannot publish a completed candidate artifact; a report failure
cannot be returned as success. Preserve the original failure and actual
request counts even when a final evidence write also fails.

`universe_ready`, `historical_coverage_certified` and
`historical_eligibility_certified` remain **false on success too**. Distinguish
successful acquisition/diagnosis from complete target histories, and both from
historical operating-company eligibility. The report may finish with
unresolved histories; that is an observed blocker for selection, not an audit
success that makes missing data disappear. There is no new portfolio, return
comparison, experiment-log trial, promotion or reserved-window access here.

Test exact/even median, equality at the cutoff, formation exclusion, genuine
zero versus missing data, market transfers/duplicates, no predecessor borrowing,
input/hash refusal before requests, unchanged AQ bounds, the fixed request cap,
durable event order, stop/no-retry behavior, disk limits, partial errors,
artifact publication failure and secret-safe aggregate output. Run actual
WSL tests, inspect the diff, complete CodeRabbit and CI, and merge before real
acquisition. Deploy only the isolated research checkout after checking its
clean base and allowed diff. Verify saved hashes, ledger order, limits and
aggregate/median arithmetic offline before appending the observed result.

At registration: implementation is in progress. New AR requests and liquidity
outcomes inspected are zero. AQ's observed size counts are the declared input,
not a reason to change its already selected boundary. After this diagnostic,
positive dated type evidence and price-panel coverage remain the next gates
before integrating the new sizing runner.

[krx-terms]: https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp

## Implementation verification — 2026-10-07, before acquisition

The fresh-context implementation task completed the fixed audit and 88 new
synthetic liquidity tests, plus six tests preserving AQ's bounds. Its initial
test collection failed before the module existed. Removing the capitalization
and cross-market relationship guard in a separate test process made both
associated tests fail; the original module then passed the focused regression.
No source mutation remained from that check.

An independent reviewer found no blocking mismatch with this preregistration
and ran 30 related synthetic tests successfully. Local WSL `scripts/dev.ps1
check` then passed the repository scanner, 27 Codex guardrail tests, 34 VST
guardrail tests and **4473 Python tests**, with three existing skips, in
452.65 seconds for the Python suite. The pure AQ validator extraction preserves
its original acquisition dates; no dependency or collector file changed.

CodeRabbit/CI completion, merge, isolated research deployment and the bounded
acquisition are still pending at this entry. No AR request has been made.
The external wrapper will enforce and inspect the 384 MiB process limit;
synthetic tests do not establish real-input memory usage or source coverage.

## First execution and preserved transport stop — 2026-10-07

PR #246 passed all four CI workflows. CodeRabbit approved head
`e2b0ad399fbffd7eca04090e467bfd964eac5934` at 13:55:51 UTC with no actionable
comments or review threads. Its nonblocking docstring-coverage warning was
answered with the public-entry-point/test-helper distinction. The squash merge
is `e7c13b33b2b0754b031796e88881baf742d2049f`. The isolated GCP research checkout
was deployed cleanly at 13:58:09 UTC; the collector stayed clean at
`4ce85d714890b87f1a57ae89d4942660e41c0483` with its three known processes present.

The first AR run lasted **13:58:27–14:18:34 UTC**. It stopped at logical request
550, `ksq_bydd_trd` for `20210310`, with `transport_failure`. Its response event
has no HTTP status after 20.136205 seconds, consistent with the configured
timeout; the saved fixed error category cannot distinguish DNS, connection,
TLS or read failure. Do not assert an authentication rejection or a KRX server
root cause. No automatic retry occurred. There were **549 HTTP 200 responses**,
549 valid saved payloads and **274 complete two-market days**; the next day's
KOSPI half is saved but has no completed partner. The other 1130 requests were
never attempted. No `candidates.json` was published, and all readiness flags
remain false. Partial aggregate history counters include uncollected sessions
and must not be presented as a completed coverage result or selected universe.

The actual child process had a 384 MiB address-space limit and niceness 15.
A during-run check observed approximately 36 MiB resident memory, sufficient
free disk and all three collector processes. The final invocation receipt
confirmed the research checkout and collector were clean and unchanged.

An offline verification made zero API requests and rechecked all **632071 raw
rows** in the 549 saved payloads, their hashes, fields and capitalization
relations. All 1100 ledger events match the attempted matrix; the minimum
response-to-next-attempt gap is 1.02972 seconds. Candidate absence and private
permissions were verified. Evidence remains outside the checkout in
`/home/minjun4897/research-evidence/krx-liquidity-20261007-v1`.

| preserved item | SHA-256 |
|---|---|
| scope | `c99d0a731174ba51848378a511a3fb57eafa31788c9a752c5dcff8e76c1a56da` |
| started receipt | `34b0abbd41bd360a9309114b6cdb34dd30516bff747586d7190cc6d8acb0a79f` |
| failed report | `53effb672a849084148511be55cda1e92e90d181d0d6ee47291545ecdf12b612` |
| request ledger | `8b2c2367f1ac6e0d3ca5d8f07dd2cf9380bb99a593b0fd769d5e8495fb8da622` |
| canonical saved-raw manifest | `0b5c2860d0930c91efc9a3017e3683d256c84f991781428b7f1e2a1263906540` |
| invocation receipt | `a69ffcb359f98397b6863e40d750f4dd7d3e0bdc53e47d35620e32695df1aba2` |
| partial offline verification | `bd99815de666bdb22b715f83b0b67e914b10dc8b0b53f88bae77ef67391dbde9` |

## Reviewed recovery plan — registered before any follow-up request

The first run remains failed and immutable. The operator's standing instruction
to complete the work covers this engineering recovery; no research threshold,
universe, period, missing-data meaning, outcome gate or source is changed.
The initial stop rule was followed, not silently relaxed in the running job.
Implement, independently review, test, CodeRabbit-review and merge the following
separate bounded invocation before any follow-up API request.

Add an explicit `--recovery-dir` mode to the same diagnostic, identified as
**`krx-large-liquid-liquidity-recovery-v1`**. It accepts only the preserved first
AR run: pin the failed report, started receipt and ledger hashes above, verify
their source SHA, fixed matrix, terminal failure, 550 attempts, 549 successful
observations and absence of completed candidates. Every cached raw response
must match its pinned report hash and pass the original row/relation checks.
Reject a different, incomplete, changed, symlinked or nonprivate cache. This is
not a general resume mechanism and cannot recursively resume a failed recovery.

Reconstruct histories by streaming the **549 saved responses** in original
logical order before the first new network request. Reuse the old `20210310`
KOSPI half with the newly acquired KOSDAQ half and validate their relation.
Do not discard a partial day's valid half, copy the original evidence into the
checkout, reacquire successful items or select caches by their liquidity result.
Keep all original bytes untouched and record their provenance separately from
new response files. The complete logical matrix remains **1680 items**.

The new request matrix is exactly logical items **550 through 1680**, hence
**1131 required new responses**. Within this new invocation permit at most
**two extra transport-failure attempts in total**, with a **30-second cooldown**
before each retry of the same item. Thus its hard wire-attempt cap is **1133**;
combined with the failed run, the maximum is 1683 wire attempts for the same
1680 logical observations. This includes one deliberate follow-up attempt of
the original failed item. An exhausted retry allowance stops immediately.

Only the existing fixed `transport_failure` category permits these two extra
attempts. Every HTTP error (including 401, 403, 429 and server errors), redirect,
unexpected response, malformed/oversized payload, schema/arithmetic/identity
failure, input/evidence-write problem or resource limit still stops immediately.
The request date/service, host, timeout, response ceiling, environment helper,
disk and process limits remain fixed. No parallel requests, alternative host,
credential change, collector change or further automatic recovery is allowed.

Persist the full logical matrix, reused-prefix provenance, new matrix, attempt
budget, retry policy and clean source SHA before requests. A new durable ledger
uses a monotonically increasing wire-attempt number and the original logical
number, retaining every failed attempt and response. New raw filenames use the
logical number; a transport failure never creates a successful response file.
Report new HTTP/schema counts, reused valid counts, combined logical completion,
remaining logical items, retries used and unused wire-attempt budget distinctly.
Do not equate an unused retry allowance with missing source observations.

Only all 1680 revalidated/new logical items together can publish the diagnostic
candidates. Preserve the original target denominator, median/zero/missing and
type-certification rules. The ordinary no-recovery invocation must retain its
original first-error stop and counters. Test cache tampering before network,
the partial-day join, successful-item reuse, retry order/cooldown/global cap,
nonretryable failures, evidence-write failures and fail-closed publication.
Verification of the eventual combined evidence must independently reconcile
both ledgers, origins, hashes and exact medians. No follow-up request has been
made at this registration.

### Recovery error-classification clarification — before follow-up requests

Independent review reproduced a local socket-buffer exhaustion error being
treated as retryable by the first implementation draft. The draft used only
synthetic inputs; no recovery network request had occurred. Tighten the recovery
allow-list to typed timeouts, connection errors, explicit network errno values
(`ENETDOWN`, `ENETRESET`, `ENETUNREACH`, `EHOSTDOWN`, `EHOSTUNREACH`,
`ECONNABORTED`, `ECONNREFUSED`, `ECONNRESET`, `ETIMEDOUT`, `EPIPE`) and DNS
`EAI_AGAIN`. Reject known local resource errors first, including nested
`URLError.reason` causes. Generic TLS/certificate errors, other DNS errors,
string reasons and unclassified OS errors stop with a fixed safe category;
do not infer retryability from exception message text. The original non-recovery
invocation still stops on its first error with its existing behavior. This
clarification narrows error recovery without changing the data or research rule.

### Recovery implementation verification — 2026-10-08 KST

The implementation adds 99 synthetic recovery tests to the original 88 in the
liquidity test file. Final WSL `scripts/dev.ps1 check` passed the scanner,
27 Codex and 34 VST guardrail tests, and **4572 Python tests**, with three
existing skips, in 1416.77 seconds for the Python suite. This full run includes
the final conservative retry classifier; a redundant worker-focused run was
stopped and was not counted as completed verification.

The independent reviewer reproduced the draft's socket-buffer exhaustion bug,
verified its fix and passed **24 tests** on the final module, including complete
prefix validation, the partial-day join, the wire-attempt cap, a read timeout
after HTTP 200 and the original no-recovery behavior. Replacing the classifier
with an always-retry function in a separate process made the resource-error
regression fail as expected; no source mutation remained. No unresolved review
finding or new research decision remains. CodeRabbit, merge and deployment of
this recovery change still precede any follow-up acquisition.
