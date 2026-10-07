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
