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
