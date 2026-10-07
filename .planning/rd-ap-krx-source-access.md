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
