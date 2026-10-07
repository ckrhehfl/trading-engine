# Research Direction Task AP — bound KRX source access before universe selection

Date: 2026-10-07. This continues Task AO's first deliverable, source access
and meaning, following the operator's instruction to continue and verify GCP
deployment. It specifies a source diagnostic, not a universe or return test.
The new large/liquid scope, post-SPAC history rule, accounting obligations,
v1 comparison stop and reserved pre-2019 boundary remain unchanged.

## Credential and deployment facts before acquisition

The operator clarified that the old key was KIS, subsequently obtained a KRX
key, placed `KRX_API_KEY` in the primary local `.env`, and explicitly requested
its registration on GCP. At 2026-10-07T03:36:53Z the exact KRX assignment was
transferred in memory over SSH to the research user's private
`~/.config/trading-engine/research/krx.env`. The file is mode 0600, its research
directory 0700, and a fresh process loaded the variable. No key value, key hash,
other local assignment or collector credential was emitted or copied into a
worktree. This establishes storage and loading, not API approval or access.

At 03:39:33Z the collector checkout was clean at `4ce85d7` (PR #221), with
the pre-2019 collector and its `data.krx_scan` process still present. The
isolated research checkout was clean at `fb153a7` (PR #233). That is the code
used for Task AM's actual audit; GitHub main had advanced through the result
and scope documents in PRs #234-236 to `8ad44e8`.

At 04:06:04Z the isolated research checkout was fast-forwarded to `8ad44e8`
after checking its clean state, ancestry and exact fetched main SHA. It was
clean afterward. Collector HEAD and clean state were unchanged; zero processes
were restarted. Since PR #221 the shared KIS reader gained optional diagnostic
callbacks; existing callers omit them and retain the previous request behavior.
No Java change occurred in that interval. These facts do not mean all GCP
checkouts run the latest main. The collector remains pinned separately.

## Official contract inspected

All four official service pages and their downloadable `API Spec` documents
were inspected through the public KRX site on 2026-10-07. The page's rendered
tables supply fields that the earlier text-only web reader omitted. All four
documents name the production host `https://data-dbg.krx.co.kr` and the paths
below. The visible request example uses GET, `basDd=YYYYMMDD`, and an
`AUTH_KEY` request header. Its public demonstration key is not used.

| service | path beneath `/svc/apis/sto/` | official page BO_ID | downloaded DOCX SHA-256 |
|---|---|---|---|
| KOSPI daily trading | `stk_bydd_trd` | `JvJFzlAENzZlPBDNGAWC` | `33de972288e7ad79eea41e07c36047167434fa746d8080f16e323032af636c0e` |
| KOSDAQ daily trading | `ksq_bydd_trd` | `hZjGpkllgCBCWqeTsYFj` | `2993f3e1f57fd2edddc9573b7bbd1eff13e2437dc80f3b19220111aee9169573` |
| KOSPI stock basic information | `stk_isu_base_info` | `PiwgMdTwmsenXhmqqxuj` | `1008e9826d42ebf80ac7b41905eac1daaf36894072f23540ccc80b3e7d7db295` |
| KOSDAQ stock basic information | `ksq_isu_base_info` | `CifLHplnUFMgpHIMMPXs` | `7789912c32ca83c0d55eabb022a99913d2f5eb67283ae5de01349ecbaedf1c85` |

Page URL prefix:
`https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=`.
The documents remain private acquisition evidence, not a redistributed API
dataset or documentation copy in this repository.

The four files and their hash manifest were preserved at
`/home/minjun4897/research-evidence/krx-openapi-contracts-20261007/` on GCP;
the directory is 0700 and each of its five files is 0600.

Both trading contracts contain `BAS_DD`, `ISU_CD`, `ISU_NM`, `MKT_NM`,
`SECT_TP_NM`, OHLC, previous-close change, change rate, `ACC_TRDVOL`,
`ACC_TRDVAL`, `MKTCAP` and `LIST_SHRS`. Basic information contains `ISU_CD`
(standard code), `ISU_SRT_CD` (short code), Korean/full/English names,
`LIST_DD`, `MKT_TP_NM`, `SECUGRP_NM`, `SECT_TP_NM`,
`KIND_STKCERT_TP_NM`, `PARVAL` and `LIST_SHRS`. All are documented as strings
inside `OutBlock_1`. **Basic information has no returned observation-date
field.** A dated request alone cannot certify that its classification is the
historical state or when it became knowable.

Neither the four field lists nor their samples establish numeric units,
revision policy, publication time or complete historical membership. KRX's
separate paid data-feed schedule is not evidence of OpenAPI publication time.
No unit, same-day availability, or current type backfill is silently assumed.
The [official access guide][access] separately requires key approval and
service approval; key storage does not certify either.

## Fixed first acquisition, before any response is inspected

`python/data/krx_openapi_probe.py` implements one bounded run:

- Three positive dates: **2019-01-02**, the beginning of the designated spent
  daily window; **2022-06-30**, the previously investigated conversion date;
  and **2026-09-18**, the completed scan's end. These test early, middle and
  late source availability, not interesting returns or preferred holdings.
- One negative control: **2026-09-19**, a Saturday. Trading responses must be
  empty; basic information may return a weekend snapshot and is reported as
  such rather than automatically rejected. A zero-row positive-date response
  does not establish that no securities existed.
- Date-major order, the four services in the order of the table above. At
  most **16 HTTP attempts**, including the controls, separated by at least
  one second. No retry, pagination guessing, date expansion or fallback host.
  A request has a 20-second timeout and an 8 MiB response ceiling.
- A first HTTP/access, transport, envelope or validation failure stops the
  run and leaves the remaining requests unattempted. A failure in the first
  service cannot be reported as a test of the other three. A non-200 response
  does not by itself distinguish an invalid key from pending service approval.
- Only the fixed HTTPS host/path matrix is available. All redirects are
  refused, and environment proxies are disabled. The credential comes only
  from the process environment; no CLI key, URL or date option exists.

The clean reviewed commit is deployed to the isolated GCP research checkout
before acquisition. The operator's registered research environment is loaded
in that new process. No collector `.env`, process, database, cron entry or
reserved price panel is changed or read. No KIS credential exception is reused.

## Evidence and interpretation

Require a new private output directory outside the checkout. Before a network
request, persist and fsync the source SHA, fixed request matrix and start
record; persist each attempt before sending it. Preserve successful raw
responses privately with SHA-256 and UTC receipt times, and emit aggregate
counts only. No key, request header, upstream exception, response body or
security-level price/rank list belongs in terminal output or the public PR.
If the response echoes the key, refuse to persist it. Error bodies are omitted.

Check required fields/types, positive-date nonemptiness, returned trading date,
code shape/uniqueness, numeric presence and validity, and basic/trading join
coverage using the actual short-code versus standard-code fields. Do not join
different identifier types because both fields happen to be called `ISU_CD`.
Numeric missing/invalid records remain defects, not dropped candidates. Basic
snapshots with identical hashes across different dates are flagged; different
hashes alone would not prove complete point-in-time history either.

The aggregate report distinguishes attempts, successful responses and failed
or unattempted work. `universe_ready` and historical-coverage certification
remain false even if every request validates. This is a source-access record,
not a discovery return trial, and does not increment `N` or modify the
canonical experiment log. There is no selector, portfolio replay, sizing or
confirmation access in this diagnostic.

## Next completion conditions

Publish the bounded run result after CodeRabbit review and CI, including any
access refusal and unresolved semantics. If permissions are missing, the
operator can inspect the key and four service applications in KRX's account UI;
do not try alternate keys, services or routes to bypass refusal. A later
approved rerun needs its own recorded attempt, preserving the first evidence.

Task AO deliverable 1 is not complete until source meaning and coverage are
adequate for a reproducible rule. Then freeze size and liquidity cutoffs,
markets, cadence and decision lag before candidate ranks or returns are read.
Actual new large/liquid portfolio/comparison runs at this preregistration: zero.

## Offline verification before acquisition

The probe's 53 synthetic tests pass in WSL, including error responses,
credential echoes, duplicate JSON keys, date/identifier/numeric failures,
redirect refusal, request limits, private evidence, dirty source refusal and
partial-run accounting. The planning-index suite passes 30 tests with its one
existing skip; the repository scanner passes. Removing the redirect refusal
in memory made its regression test fail, and restoring it made the test pass.
No real API request was used for these checks.

Independent review reproduced a final evidence-write failure that discarded
the CLI's request counts. The correction preserves source SHA, attempted,
successful and unattempted counts and the original API failure. Reports are
fsynced under a pending name before publication; a failed post-rename directory
sync attempts to withdraw the published file and discloses any uncertain
cleanup. Such a run exits as failure, even when every HTTP call succeeded.
The reviewer independently passed the four failure-path tests and reported
no remaining actionable finding. Full CI and CodeRabbit are separate merge
requirements, and none of these checks establishes real service access.

[access]: https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp

## First GCP execution result — 2026-10-07

PR #237's exact head `8da38ad` received CodeRabbit approval at 04:23:48Z,
with no actionable comments or unresolved threads. All four CI workflows
passed; Python CI reports **4,284 passed, 3 existing skips**. The docstring
coverage advisory was assessed and answered in the PR, not hidden as a test
failure. The reviewed tree equals merge commit
`530fcb42f576bedf01f65948a53a345a1e95c566`.

At 04:26:25Z the isolated GCP research checkout was verified clean at that
merge. Its own environment passed all 53 probe tests in 2.27 seconds. No
collector checkout or running process was changed. A first local launcher
had a Python quoting syntax error before SSH or any request; correcting that
launcher did not retry a network request or change the committed probe.

The single actual run started at **04:27:50.936959Z** and finished at
**04:27:51.577947Z**, with nice level 15 and a 384 MiB virtual-memory ceiling:

| measure | observed result |
|---|---|
| first request | `stk_bydd_trd`, `basDd=20190102`, official HTTPS production path |
| HTTP response | **401** |
| attempted / maximum | **1 / 16** |
| HTTP 200 / validated responses | **0 / 0** |
| remaining requests | **15 unattempted**; the other services and controls were not tested |
| diagnostic exit | 1, `access_failure`; stderr empty |
| report publication | persisted successfully |
| acquired raw responses / universe samples | **0 / 0** |

The run stopped as specified. No error body or authentication header was read
into an artifact. Evidence is private at
`<private-evidence-root>/krx-source-access-20261007-v1/`:

| artifact | SHA-256 |
|---|---|
| `started.json` | `ab9921d25652dd69e1caf0b7d45533437e3d0f2a08fa6b02ac9b56aad852a5bf` |
| `requests.jsonl` | `5294815ff8465e6f4a9ccef9a7096a24295dfc503d1ee2315f23ca8959444363` |
| `report.json` | `1d69b4546c6f29ca32c370cb6b4f5aa7f9c9635289336405f366654e5edc6566` |
| `execution.json` | `ecc8172051f90af30e45ed67436386f90148c15b181f2b5721c53b3b4be41778` |

All four files were verified 0600. Execution records retain research HEAD
`530fcb4` and collector HEAD `4ce85d7`, both clean. At 04:28:59Z the same two
pre-2019 wrappers and `data.krx_scan` process were present. Process presence
does not independently certify collection quality or completion.

## Diagnosis after the refusal

The operator reports that **the key and all four services are approved**.
This is operator-provided account information, not an authenticated account
screen inspected by the agent. At 04:30:43Z an in-memory comparison confirmed
that the exact primary-local KRX assignment equals the value in a fresh GCP
process sourced from the research environment. It is nonempty and contains
no header-unsafe whitespace/control characters. A separate boolean comparison
excluded confusion with the public demo key shown in the official example.
Neither check sent an API request, emitted a key/hash, changed a credential,
or read KIS credential values. There has been no second API attempt.

The official [KRX FAQ][faq] was then inspected in the rendered browser:

- `basDd` selects the market's listings at that day's close, including
  subsequently delisted or then-suspended listings; transfers use the dated
  market. This describes membership, not a proof that every type field is
  historically unrevised.
- OHLC and traded value use KRW; volume uses shares. The answer does not
  explicitly specify the market-cap field's unit.
- Data is updated at 08:00 on the following business day; same-day,
  pre-update and holiday queries are unavailable. Treat this as the current
  documented schedule, not independently observed historical publication times.
- The 401 answer lists service approval, using personal keys on sample routes,
  superseded keys after renewal, and key typos/whitespace. The 403 answer adds
  URL correctness and HTTPS. This run used the documented non-sample HTTPS
  path. The FAQ's separate crawler/IP-block warning does not establish that
  this GCP address is blocked.

The remaining account-side check is whether the currently issued key matches
the local assignment and whether today's date falls within the approved
service period. Only the operator can inspect that signed-in screen in the
current session. A 401 does not certify either an invalid key or a GCP network
restriction, and the operator's approval report must not be rewritten as
"approval pending." Preserve this failed attempt before planning any further
bounded diagnostic; do not expand dates, switch IPs or repeat requests blindly.

The source-access diagnostic and research deployment have been performed;
source availability is **not established**. Task AO deliverable 1 remains open,
dated-universe samples and new large/liquid portfolio/comparison runs remain
zero. Next: resolve this authentication discrepancy, obtain the fixed samples,
then freeze the dated size/liquidity rule before selection outcomes are read.

[faq]: https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd

## Operator evidence and header-spelling diagnostic — 2026-10-07

The operator confirms that the stored value is the currently issued key. The
supplied account screenshot shows an issue/renewal date of **2026-10-07** and
key validity **2026-10-07 through 2027-10-06**. No key characters or screenshot
are retained here. Together with the earlier equality/environment checks and
reported four service approvals, this closes the request to reconfirm key
copying or expiry. It does not turn the observed 401 into successful access.

An offline inspection of the installed Python transport found a concrete
request difference: `urllib.request.AbstractHTTPHandler.do_open` title-cases
header names, sending `Auth_Key` where the official example writes `AUTH_KEY`.
The [Python documentation][urllib-headers] describes this normalization. HTTP
field names are case-insensitive under [RFC 9110 section 5.1][http-fields];
there is no evidence yet that KRX treats these spellings differently. This is
a bounded compatibility hypothesis, not a diagnosed vendor defect.

Before any further API request, register this separate diagnostic:

- Run the reviewed `--header-case-check` mode once in the same isolated GCP
  research checkout, with the same registered key and network origin.
- Send **one GET at most**, to the same production host and first request:
  `stk_bydd_trd`, `basDd=20190102`. Preserve the literal header name `AUTH_KEY`
  at serialization. Other request behavior, verified TLS, proxy/redirect
  refusal, timeout and response ceiling retain the existing probe's rules.
- No retry, alternate key, IP, host, service or date. Both success and failure
  end this diagnostic; it cannot start the remaining 15 matrix requests.
- Use specification `krx-openapi-auth-header-case-v1` and a new private output
  directory. Persist source SHA, the one-entry matrix and attempt ledger before
  network activity, and retain the original failed run. Keep the existing
  evidence permissions, aggregate-only output and credential-echo refusal.
  Error bodies remain unread and unrecorded.
- A validated 200 establishes this single request's access at execution time,
  not all four services or historical semantics. Because the earlier 401 was
  at a different time, it would not alone prove header spelling caused the
  change. Another 401 rules out literal spelling as a sufficient fix in that
  attempt; it does not identify a bad key or an IP restriction.

The normal 16-request mode is unchanged. Any subsequent acquisition needs its
own recorded execution decision; the one-request diagnostic is not permission
for automatic retries. No collector, credential storage, research database,
experiment count, universe rule or pre-2019 access changes. New large/liquid
portfolio and comparison runs remain zero; Task AO deliverable 1 remains open.

[urllib-headers]: https://docs.python.org/3/library/urllib.request.html#urllib.request.Request
[http-fields]: https://www.rfc-editor.org/rfc/rfc9110.html#section-5.1

### Header diagnostic verification before execution

The WSL offline probe suite passes **65 tests**. A fake socket runs the real
urllib/http.client serialization and verifies that the only wire difference
is `Auth_Key` becoming `AUTH_KEY`; certificate/hostname verification remains
enabled. Removing the spelling correction in memory makes that test fail.
The tests cover the one-entry ledger/report, a 401 and five redirect statuses
without retry, unread error bodies, ambient proxy refusal, credential echoes,
dirty source refusal and failure to persist an attempt. The original full
matrix tests still pass. The repository scanner and diff whitespace check
pass. These tests use synthetic credentials/responses and make no API call;
CI, CodeRabbit and actual GCP execution remain separate checks.

## Literal-header execution result — 2026-10-07

PR #239's exact head `f8b85f4` received CodeRabbit approval at **05:22:50Z**,
with no actionable comments or unresolved threads. The docstring-coverage
advisory was assessed in the PR. All four CI workflows passed; Python passed
**4,296 tests with 3 existing skips** both in WSL (384.07 seconds) and CI
(195.44 seconds). The reviewed tree equals merge commit
`f4ac38fd573a5272e9986ff1a7ac6163a537e8ab`.

The isolated GCP research checkout was fast-forwarded to that merge and its
65 probe tests passed in **3.19 seconds**. The collector checkout remained
clean at `4ce85d7` before and after deployment and execution. No collector
process, schedule, credential or database was changed.

The diagnostic started at **05:25:43.855266Z** and finished at
**05:25:44.174607Z** (14:25:44 KST), using nice level 15 and a 384 MiB
virtual-memory ceiling:

| measure | observed result |
|---|---|
| specification | `krx-openapi-auth-header-case-v1` |
| request | `stk_bydd_trd`, `basDd=20190102`, same official HTTPS production path |
| serialized authentication field | literal `AUTH_KEY`; value not recorded |
| HTTP response | **401** |
| attempted / maximum | **1 / 1** |
| HTTP 200 / validated responses | **0 / 0** |
| diagnostic exit | 1, `access_failure`; no timeout or stderr output |
| report publication | persisted successfully |
| acquired raw responses / universe samples | **0 / 0** |

The other services/dates were not called. Error bodies remained unread. The
first failed run is preserved separately; cumulative actual KRX attempts
across the two runs are **2**, both returning 401. No successful data sample
exists. Private evidence is at
`<private-evidence-root>/krx-auth-header-case-20261007-v1/`, with all four files
verified 0600:

| artifact | SHA-256 |
|---|---|
| `started.json` | `70bc3bb116bc3fc3cf2cc22aa4f84238e51983170ed2979cc8867048b9037303` |
| `requests.jsonl` | `bf435975a921091d86d2c0c0f44f567571183ec7357b382f2b9613c0993bdeaa` |
| `report.json` | `68ef04941037ef87f12e5153a7a48367107cdb5841939d697d939d9ee0f4e58a` |
| `execution.json` | `b6c530916499431eaa2b5a2c85578ca086ac30e453f0abd5b3529ad45e5f5a55` |

### Interpretation and next external dependency

Literal header spelling is **not a sufficient fix** for this observed refusal.
The diagnostic does not identify the vendor's internal rejection reason.
Do not recast the operator-confirmed key and approvals as invalid or pending,
infer an IP block from 401, rotate the key speculatively, switch network origin
to bypass refusal, or expand the request matrix. The next useful evidence is
an explanation of the rejection from KRX's account/service authentication
records, using the exact request times and endpoint above. No support message
has been sent on the operator's behalf, and no automatic retry is scheduled.

For a private inquiry to KRX, provide the key issue/validity dates, the four
operator-confirmed service approvals, production GET path and `basDd`, the
two request times (13:27:51 and 14:25:44 KST on 2026-10-07), and HTTP 401.
Ask KRX to check account/service authorization linkage and the rejection
reason for these requests. The public FAQ footer lists `krxdata@krx.co.kr`;
do not include the key value, `.env`, screenshot or raw environment in the
inquiry. An approval-propagation delay or network restriction is a question
for KRX, not a confirmed diagnosis.

Additional official FAQ entries inspected while awaiting review state that
OpenAPI provides no bulk trading-calendar/holiday or corporate-action-history
API/separate dataset, and no suspension-status field. For suspensions KRX
directs users to Data Marketplace's dated suspension history and KIND for
exact effective times. These are source limitations, not a reason to reopen
full-market SPAC research or skip existing corporate-action accounting.

The diagnostic engineering, review, merge and research deployment are complete.
Source availability remains **unestablished**: Task AO is still at deliverable
1 of 5, with zero new dated-universe samples and zero new large/liquid
portfolio/comparison runs. After the authorization discrepancy is resolved,
record a bounded acquisition decision, establish source semantics and then
freeze the size/liquidity universe rule before inspecting its outcome.

## Local/GCP access comparison — 2026-10-07

The operator requested a check of whether GCP itself prevents use. Read-only
instance metadata identifies the existing GCP zone as `us-central1-a`;
[Google's region documentation][gcp-zones] places it in Iowa, USA. The inspected
[KRX terms][krx-terms] and [FAQ][faq] contain no explicit blanket prohibition
on GCP. The FAQ's IP-block warning concerns excessive unofficial crawling;
it does not establish that this instance is blocked. Neither the inspected
documents nor an HTTP 401 establish an unrestricted-network guarantee.

Before either request, the comparison scope was persisted and fsynced in
private evidence at **2026-10-07 16:53:42 KST** (07:53:42Z), specification
`krx-location-comparison-20261007-v1`. This was a private prerequest scope
record, not a new Git preregistration or strategy trial. It reused the
unchanged, previously reviewed one-request CLI at clean source
`1b757eacb7bc693beb271ec5aff4a8628eb91caa` in both environments. The registered
matrix allowed one local WSL request and one existing-GCP request, at most
two total, with no retries or infrastructure changes. Both used `stk_bydd_trd`,
`basDd=20190102`, the same production HTTPS path and literal `AUTH_KEY`.
Local and freshly loaded GCP keys were again compared in memory and equal;
no key value or key hash was printed or retained.

The prerequest interpretation distinguished location correlation from cause:
local success/cloud failure would implicate environment without proving a
blanket GCP policy; two failures would not establish GCP-specific failure;
two successes would not explain the earlier failures.

| environment | response time, 2026-10-07 KST | attempts / cap | result |
|---|---|---|---|
| local WSL Ubuntu-24.04 | 16:54:27 | 1 / 1 | HTTP 401 |
| existing GCP, `us-central1-a` | 16:55:01 | 1 / 1 | HTTP 401 |

Both reports were persisted, exited 1 with `access_failure`, and contain zero
HTTP 200 or schema-validated responses. Error bodies remained unread; there
are no raw response samples, observations or joined universe data. Both runs
used a 384 MiB memory ceiling; the GCP run also used nice level 15. The GCP
collector checkout stayed clean at `4ce85d7` before and after. No collector,
schedule, credentials or databases were changed. Cumulative actual KRX
requests are now **4: 1 local and 3 GCP, all HTTP 401**. No other service/date
in the original matrix has been attempted.

Private evidence roots use
`<private-evidence-root>/krx-location-comparison-20261007-v1/`, with `local/`
and `gcp/` run directories on their respective hosts. Directories are 0700
and the following files were verified 0600. The identical prerequest scope
was mirrored to GCP; the preflight record is local.

| artifact | SHA-256 |
|---|---|
| `scope.json` | `9ac8902dd955ce0d2482c471b23b32deef1d6c414ed8ca4d7f2209eba89be3e2` |
| `preflight.json` | `b9d97491ef9698f9d35aaff1e915e0e38cd5327ba658ffa2c4d3bd492e092a98` |
| `local/started.json` | `3ecea06cdaa43ce057a0db547dde7f251c551fccc9953fbc196b5806c7b60942` |
| `local/requests.jsonl` | `9ae5900dee19169eb16e2f066f495014bbbc95629414af941ede70b5a8fd5c17` |
| `local/report.json` | `40f1beabf06d3f45d98d0ebc6e96993455e04f6dfdb0e5755ce3d6f08b0883f2` |
| `local/execution.json` | `61d19703b73cc8d58fc4e28391246511409b41ed3a4a08e1e5e54cdad88ae65c` |
| `gcp/started.json` | `43ec7bcb5ea5f5f6b3356d123e3331483623460dc96ad4632bfbd5833ffcf52a` |
| `gcp/requests.jsonl` | `0ba41a8d4ac52cc884d5f4bf43acb4c6ca7e1132ffe22327c361f0faaa49741f` |
| `gcp/report.json` | `27e7d4430edee21b39a9e396b88679ba41b66e470549930cf3302aeda5081d0a` |
| `gcp/execution.json` | `327e23706f927d6fa09c45f3a37fd4f9ce33d4b67227ec231c533c95ae67e99c` |

### Result and next action

This comparison does **not support a GCP-only failure**. It does not identify
the vendor's exact rejection reason or rule out coexisting network restrictions.
The HTTP response shows that both calls reached an HTTP responder; it is not
evidence of a simple connection timeout. The operator-confirmed valid key and
service approvals must not be relabeled as incorrect or pending.

Keep the current infrastructure. The next useful step is KRX's inspection of
account/key/service authorization linkage and the rejection records for the
two new request times, in addition to the earlier timestamps above. Ask whether
any source-network restriction applies to these requests, rather than assume
one or move the server speculatively. No inquiry has been sent on the operator's
behalf and no further API request is scheduled. After a concrete explanation
or service-side change, record the next bounded verification decision.

This operational comparison is complete. Source access remains unestablished;
Task AO deliverable **1 of 5** remains open, with zero new dated-universe
samples or large/liquid portfolio/comparison runs. The experiment count and
pre-2019 reservation are unchanged.

[gcp-zones]: https://docs.cloud.google.com/compute/docs/regions-zones
[krx-terms]: https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp

## Individual service approval and successful GCP access — 2026-10-07

The operator corrected the earlier approval report: the key approval had
been mistaken for individual API approval. The operator then applied for the
four named services and confirmed their approvals, without receiving a new
key. This supersedes the earlier account-status premise; preserve the four
observed 401 responses as actual results, not as evidence of a GCP ban.

### Recorded execution decision

Before any new request, private scope `krx-approved-services-recheck-v1` was
persisted and fsynced at **17:31:18 KST** (08:31:18Z). This was a new execution
record for the already reviewed probe, not a code change or strategy trial.
Both runs used clean source `9c57589c0e24bbb92e55cc146f55589466aa7131`,
the existing GCP origin and the previously registered research key.

- First, allow one `--header-case-check` request for
  `stk_bydd_trd/20190102`. Continue only if exit status is zero, the report
  is persisted, and exactly one HTTP 200 response passes schema validation.
- Conditional on that success, run the unchanged normal 16-request matrix
  registered above: three positive dates and one Saturday across four services.
  The normal mode retains its original header serialization. The maximum
  across both phases is **17 attempts**, with no retries, date expansion,
  alternate keys or fallback hosts. A validation/access/evidence failure stops
  the run. No pre-2019 data, ranks or returns are requested.

### Actual results

The first phase completed at **17:31:35 KST**, returning HTTP 200 and 901
validly structured rows. The normal matrix ran **17:31:57–17:32:40 KST**:
**16/16 HTTP 200, 16/16 schema-valid responses, zero unattempted requests**.
Both processes exited zero, with no timeout or stderr, and persisted reports.

| requested date | KOSPI trading rows | KOSDAQ trading rows | KOSPI basic rows | KOSDAQ basic rows |
|---|---:|---:|---:|---:|
| 2019-01-02 | 901 | 1,325 | 901 | 1,325 |
| 2022-06-30 | 941 | 1,565 | 941 | 1,565 |
| 2026-09-18 | 942 | 1,820 | 942 | 1,820 |
| 2026-09-19, Saturday | 0 | 0 | 942 | 1,820 |

These are source row counts, including security types beyond the intended
common-stock universe; they are not selected candidate counts. Across the
three trading dates, **7,494 trading rows** join one-to-one by short code to
basic information, with zero unmatched or ambiguous rows. Required fields,
identifier shape/uniqueness and the probe's checked numeric fields pass.
Returned trading dates match the requests. Basic listing dates are valid and
none is later than the requested date.

Both Saturday trading responses are empty, as registered. Saturday basic
responses equal Friday's for each market. Basic payloads differ across the
three positive dates, so they are not one identical snapshot for all requests;
this alone does not certify point-in-time classification, publication timing
or historical completeness. Basic responses still have no observation-date
field. No candidate ranks, price-level values or security names were emitted.

The single literal-header response and the normal-mode first response have
the same SHA-256. Both spellings succeeded after the service applications,
using the existing key and GCP instance. Access is now established for this
fixed matrix. The approval correction and subsequent success support missing
individual service permission as the likely explanation of the earlier 401;
without KRX's internal logs, they do not prove its exact historical cause.
The earlier recommendation to seek a rejection-log explanation is no longer
a prerequisite to this source-access work. No support inquiry was sent.

Cumulative KRX attempts across Task AP are **21: 4 earlier HTTP 401 and 17
new HTTP 200** (20 GCP, 1 local). This run produced 17 private response files,
including the repeated first request and two empty controls; it did not
acquire a continuous historical panel. Both runs used nice level 15 and a
384 MiB memory ceiling. The collector checkout stayed clean at `4ce85d7`
before and after; no collector process, schedule, credential or database changed.

### Evidence verification and remaining work

Private evidence is at
`<private-evidence-root>/krx-approved-services-20261007-v1/`, with `single/`
and `matrix/` subdirectories. A separate offline verification at
**17:34:15 KST** matched the saved scope, start records, attempt/response
ledger and all 17 raw-response hashes, checked 0700 run directories and 0600
files, and verified zero unmatched/ambiguous joins. The matrix's minimum gap
from the preceding response to the next attempt was **1.007221 seconds**.
This verification made zero API requests; its aggregate record is also private.

| artifact | SHA-256 |
|---|---|
| `scope.json` | `10696d9093f347e1288c73458a2c30048e0cf116c2f37ee2c91ad6a4f928649d` |
| `single/started.json` | `742353cb36164a3450f705d460a7f3e3cd85110e9b971e7e37b657b8f7230543` |
| `single/requests.jsonl` | `0eb48b166738b93cd84dd1f1294186a8d903b51fe939e2f6468ec6dfa98ec159` |
| `single/report.json` | `cf63844f4a081bfe366d74d3f32b761c129a815082c05d1cf09b5bd37c1ddac9` |
| `single/execution.json` | `ff509c69af9d07856ca644e8df40850b4a9365aacd95eecbe6d44595d2e6a4db` |
| `matrix/started.json` | `c15ad740b14b59d3f4afa9481644d035d3824fd33e2db35638563c3c77db58b8` |
| `matrix/requests.jsonl` | `cf99d7595ea1bed12b45b241510202e486857dfacb04e5197d0994ea43676161` |
| `matrix/report.json` | `21144ad40cf648e0201f762a1b86970d2610f8e81d10979a449a01b3aacd5042` |
| `matrix/execution.json` | `8ef7a235cc772900e1b767fad2e5fd55809290ce5dcb47d64a3166936e90ca36` |
| `verification.json` | `aac1468913e162ee3bafd17fe4d18a724226f715dc4ae3e2bdc4242d54f004e0` |

The access and structural sample checks are complete. Task AO deliverable
**1 of 5 remains open on source meaning and coverage**, not authentication:
verify units, publication/decision-time availability, historical completeness,
point-in-time classifications and revisions. `universe_ready` and
`historical_coverage_certified` remain false. Next resolve those source
semantics, then freeze the dated size/liquidity rule before inspecting ranks
or returns. New large/liquid portfolio and comparison runs remain zero;
the experiment count and pre-2019 reservation are unchanged.

## Saved-sample semantics and classification audit — 2026-10-07

This follow-up used clean source `a4fb3a1f9baa86add304990ead958b931f806712`
and only already acquired private evidence. It made **zero new HTTP requests**,
read no credentials or research database, and inspected no size/liquidity
ranks or strategy returns. Cumulative Task AP API attempts remain 21.

### Scope recorded before each inspection

Private scope `krx-saved-sample-semantics-v1` was persisted and fsynced at
**17:56:15 KST**. It named the 16 matrix responses above and preregistered
input-hash verification, row-wise capitalization arithmetic, matching listed
shares, aggregate class counts, and same-code identity changes between the
three sampled trading dates. The fixed identity-only controls were the
operator's examples `005930` and `000660`, plus Task AN's already investigated
conversion control `336570`; none was selected by size or return.

A separate scope, `krx-saved-classification-crosscheck-v1`, was persisted
before comparing the API's section labels with Task AN's already saved,
complete **2022-06-30** KIND SPAC snapshot. It required reporting every set
difference and the conversion control's membership. It authorized no new
SPAC requests, price inspection or expansion of Task AN's old universe.

### Arithmetic and identity results

The first audit finished at **17:57:16 KST**. All 16 input hashes matched the
matrix report. Across the three trading dates, every one of **7,494** trading
rows had exact `MKTCAP == TDD_CLSPRC * LIST_SHRS`; the corresponding basic
record had the same listed-share count, with **zero mismatches** for either
check. These are consistency checks of the sampled source, not independent
verification of each issuer's shares or a historical coverage certificate.

| date | KOSPI rows checked | KOSDAQ rows checked | arithmetic / cross-source share mismatches |
|---|---:|---:|---:|
| 2019-01-02 | 901 | 1,325 | 0 / 0 |
| 2022-06-30 | 941 | 1,565 | 0 / 0 |
| 2026-09-18 | 942 | 1,820 | 0 / 0 |

Basic information distinguishes instrument groups such as `주권`, foreign
shares, depositary receipts, REITs and other investment vehicles, and share
classes such as common and preferred. Counts are source labels, not eligible
candidate counts. The two operator examples are KOSPI `주권` / `보통주`
at every sampled trading date; no claim is made that either passes the new
size/liquidity rule throughout the discovery era.

Matched short codes show date-sensitive names, market and section labels:

| sample transition | common codes | earlier-only / later-only | name changes | market changes | section changes |
|---|---:|---:|---:|---:|---:|
| 2019-01-02 to 2022-06-30 | 2,120 | 106 / 386 | 278 | 5 | 512 |
| 2022-06-30 to 2026-09-18 | 2,308 | 198 / 454 | 318 | 7 | 593 |

An earlier-only code is not by itself a verified delisting. Different
snapshots do not prove contemporaneous classification or absence of revisions.
Control `336570` has name `원텍` and labels `주권` / `보통주` on both later
dates, with listing date **2019-12-19**. That original listing date cannot
substitute for the separately evidenced post-SPAC baseline reset date.

### What the same-date classification cross-check establishes

The second audit finished at **18:00:55 KST**. KOSDAQ SPAC-section counts
are 43, 57 and 66 on the three sampled dates. For 2022-06-30, KIND's complete
SPAC snapshot contains **58** codes, the API SPAC section **57**, and their
intersection **57**. The only KIND-only code is `340120`; there are no
API-only codes. API basic information contains it as `주권` / `보통주`,
named `하이제5호기업인수목적`, but with section `관리종목(소속부없음)`.
Control `336570` is present in API basic information and absent from both
SPAC sets, consistent with Task AN's recorded boundary.

Thus **not in the SPAC section does not establish an operating company**.
The two sources expose different classifications; this does not demonstrate
an API error. The existing example is enough to reject that shortcut without
investigating its trading outcome or restarting full-market SPAC research.
Unknown candidate classifications still need evidence under Task AO's rule.

### Current documentation and remaining limits

The live official FAQ was inspected on 2026-10-07. Its unit, price-basis,
publication, historical-membership and session-coverage contracts are recorded
with the source link in `docs/exchange-api.md` section 6. That resolves the
previously undocumented meaning and timing at the documentation level; it
does not establish an archive of historical release times or revisions.
Record actual acquisition separately from any inferred historical availability.

Neither snapshot variation nor the arithmetic proves full-period completeness,
point-in-time classifications, corporate-action accounting or revision policy.
`universe_ready`, `historical_coverage_certified` and
`historical_eligibility_certified` remain false. Task AO deliverable **1 of 5**
remains open on coverage and historical classification. The operator's size
and liquidity decision is appended to Task AO; deliverable 2 still needs the
complete reproducible timing, missing-data and acquisition specification.
No universe, portfolio or comparison run occurred; experiment count, collector
state and the reserved pre-2019 window are unchanged.

### Private provenance

Outputs are under `<private-evidence-root>/krx-semantics-20261007-v1/`.
The source matrix report hash is recorded in the preceding result. The KIND
input is Task AN's saved `result.json`, hash
`ee8a1da02c9981b5917ca35e14a58233067a04d9e040fc247bb68a873a839762`;
its existing source archive hash is
`b246b10230800f3f7302e725ac571308a5b0226c145e9e0c92d9fa399d74a096`.

| new artifact | SHA-256 |
|---|---|
| `scope.json` | `067bc05fa2cb9fdda3b89cd5eab55ba2d6e607b031439acbe847a1bb6ac3cb04` |
| `result.json` | `72790e367338340f3619403577f0f0a802f5385e82dea99ff8fdac9ccb5ca782` |
| `classification-scope.json` | `6451cf0d13187a55ab8e6775c1076329ab7916497210b5f059eec8c725609f64` |
| `classification-result.json` | `63061af881bd8295abef7bd5121b7c12e03a090fe943f545c2e880b7652be976` |

Before publication, a separate read-only check matched all four hashes and
verified 0600 files under the 0700 output directory. The WSL documentation
and planning-index suites passed **84 tests with one existing skip**; the
repository scanner and diff whitespace check passed. Fresh-context read-only
review found no actionable issue in the four-document diff. It did not
independently inspect the private inputs or external FAQ. Full CI and
CodeRabbit remain merge requirements.
