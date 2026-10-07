# Research Direction Task AQ — freeze the large/liquid rule and audit formation sources

## Discuss and scope — 2026-10-07

The operator asked to continue after selecting Task AO's first numeric option.
Implementation has started for the first bounded acquisition, before inspecting
its capitalization counts, names or returns. This record fixes the rule and
Stage A request matrix prospectively. It carries forward CLAUDE.md's research
guards and Tasks AG/AN/AO/AP; it does not replace their outstanding evidence
requirements. The existing full-market pilot and its comparison stop remain.

The immediate executable is `data.krx_formation_audit`. It acquires formation
day trading/basic-information snapshots and audits their internal agreement.
It does not implement a selector, liquidity-history acquisition or a replay.
Task AO deliverable 2's reproducible universe rule is specified below; source
coverage and historical classification in deliverables 1 and 3 remain open.

## Rule fixed before observing the new pool

- **Universe:** KOSPI and KOSDAQ domestic operating-company common shares at
  the historical decision time. Current names or present index membership
  cannot establish past eligibility. A positive, dated classification interval
  and its evidence-availability date must satisfy Task AG. `보통주` alone and
  absence from a SPAC section are insufficient, as Task AP demonstrated.
- **Size:** the common issue's formation-day `MKTCAP` must be at least
  **KRW 5,000,000,000,000**, inclusive. This is issue-level capitalization, not
  issuer-level common-plus-preferred capitalization. Do not combine today's
  shares with historical adjusted prices. Stage A validates the source's exact
  raw close times listed shares arithmetic before applying this diagnostic.
- **Liquidity:** the median of source `ACC_TRDVAL` over exactly the preceding
  **60 market trading sessions** must be at least **KRW 10,000,000,000**,
  inclusive. For sorted observations, the even-sample median is the arithmetic
  mean of positions 30 and 31. If formation has calendar index `f`, the window
  is `C[f-60:f]`; it excludes formation itself. Units and session coverage are
  those verified in `docs/exchange-api.md` section 6, including KRX's documented
  aftermarket aggregation change. Never substitute close times volume.
- **Zeros, halts and missingness:** a verified historical zero remains zero in
  that fixed-calendar absolute-liquidity median; it cannot raise the median.
  Missing, uncollected or unparsable data is unresolved, never zero. The
  formation observation must separately be non-frozen and tradeable under the
  existing rule; equal OHLC with positive turnover is not a halt. Report the
  historical zero/frozen counts and this policy with the window statistic.
  This does not create a new all-60-sessions-tradeable gate or extend the
  liquidity window to collect 60 non-frozen observations.
- **Activity baseline:** Task AN's 60 normal observations within a verified
  post-conversion operating-company period remain a separate requirement.
  Neither SPAC-period observations nor another code can supply that baseline.
  Verified insufficient history fails the rule; unknown or conflicting history
  is an unresolved input and blocks readiness. The same distinction applies
  to a newly listed issue's incomplete liquidity history.
- **Timing:** formation information is modeled as usable at **08:30 KST on the
  next market session**, with no fill before that session's regular open in a
  later simulator. This lag is based on the currently documented next-business-
  day 08:00 publication, not a claim that historical release timestamps have
  been certified. Dated classification must be known by the modeled decision.
- **Cadence and ties:** retain v1's reference formation geometry, with no
  return-based adjustment: calendar `C` comes from the existing index series,
  the discovery end is 2026-09-18, and formation indices are
  `range(60, end_index - 126, 126)`. The threshold screen admits every verified
  pass; it has no ranking, top-N quota or tie-break. Subsequent activity/hash
  selection, holding periods, slots and costs belong to a reviewed replay
  specification, not this source audit. No v1 parameter is being optimized.

Unknown inputs that could affect the eligible pool block its completion; they
must not silently remove names. Before any replay, map the source denominator
to the completed panel, resolve type/history coverage, and satisfy Task AG's
corporate-action and raw/adjusted-basis accounting gates for all held intervals.
Structural API agreement alone does not certify historical source completeness,
availability or absence of later revisions. No confirmation window is opened,
no return outcome is inspected, and no new trial is added to `N` by this audit.

## Calendar provenance and fixed Stage A matrix

Task AP's calendar-only inventory established these dates without selecting
stock price values or candidate names. The inventory recorded 14 formations
and 854 distinct dates in the union of formation and preceding liquidity
windows. Preserve its private manifest; this new tool does not open a database.

| provenance item | SHA-256 |
|---|---|
| canonical index calendar | `fceaf0d8748f203001afbf11a66c44aa756f7f55b21e22aaee04152186506ce4` |
| private calendar manifest | `ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882` |

The specification identifier is **`krx-large-liquid-formations-v1`**. In this
exact date order, request the four existing approved services in this order:
`stk_bydd_trd`, `ksq_bydd_trd`, `stk_isu_base_info`, `ksq_isu_base_info`.

```
20190402 20191004 20200407 20201013 20210415 20211020 20220422
20221027 20230427 20231103 20240510 20241115 20250527 20251201
```

This is **56 requests maximum**, all to the existing fixed KRX host and paths.
The CLI accepts only a fresh private output directory, not dates, a URL or a
key. Reuse the approved probe's environment credential and HTTP helper without
changing secret storage, collectors, schedules or order-capable code. Require
a clean committed source tree. Persist the source SHA, full matrix and these
provenance hashes before the first attempt. Each attempt must be durable before
the corresponding network call, with response metadata persisted afterwards.

Keep a 20-second timeout, 8 MiB response cap and at least one second after the
previous response before the next request. There is no retry, alternate host,
automatic continuation or expansion on an error. Stop on HTTP/transport/schema,
arithmetic, identity or evidence-write failure. Preserve partial evidence and
counts. Report only fixed error categories, never exception text, HTTP error
bodies, authentication headers or keys. Preserve valid, secret-free JSON bytes
even if a subsequent field/day audit fails. Publish the final report atomically;
a failure to persist it must not be represented as a successful completion.

## Stage A validation and report contract

Validate every returned row, including below-threshold rows, before producing
size counts. Each service/date must be nonempty. Trading rows must carry the
requested `BAS_DD`; basic-information rows have no observation-date field, so
their date provenance is the saved request, not an invented response field.
Require the service's documented fields and string types; trading short codes
have six uppercase alphanumeric characters, and basic rows also have an ISIN
matching the existing format check plus a short code of that same form.
Reject duplicate identities within a market or across markets on the same day.
Require the exact observed market labels `KOSPI` and `KOSDAQ` in trading
`MKT_NM` and basic `MKT_TP_NM`. A basic listing date must be valid and no later
than the query date; group/share-class labels must be nonempty. An empty KOSPI
section label is permitted, without interpreting it as operating-company proof.

The trading/basic join must match **both directions**, one to one by short
code, with matching listed-share counts. Require positive raw close, listed
shares and capitalization, integral shares, and exact Decimal equality
`MKTCAP == TDD_CLSPRC * LIST_SHRS`. Raw open/high/low, volume and traded value
must be nonnegative, with integral volume. Zero open/high/low is allowed for
halted rows; this source audit is not a tradability pass. Do not impose a
regular-session OHLC bound on an all-session traded-value/volume ratio.
Malformed or unknown capitalization is a fatal audit error, never a zero or
an excluded small issue. Further prices/tradability checks remain replay gates.

The report contains per-date source totals and inclusive size-pass/below counts.
Any additional common-label count is explicitly only a source-label count,
not an eligible operating-company count. `universe_ready`,
`historical_coverage_certified` and `historical_eligibility_certified` remain
**false even on success**. Do not print names, codes, row prices or row market
caps. Retain complete raw responses privately for later reconstruction; no
extra public candidate file is required. Evidence directories/files use modes
0700/0600 outside the checkout and never replace an existing execution.

## Execute, verify and stop boundary

Follow GSD with a fresh-context implementation task and synthetic tests before
implementation. Test threshold equality, malformed rows, future listing dates,
both join directions, cross-market identity conflicts, exact share/cap math,
fixed request count, no retries, durable request ordering, partial failure,
secret-safe errors and final-report publication failure. Run the repository
checks and inspect the diff; complete CodeRabbit review and merge before real
requests. Deploy only the isolated GCP research checkout, verifying its clean
base and allowed diff while leaving the running collector checkout unchanged.

After execution, verify the private ledger, response hashes, permissions and
aggregate counts independently; append the observed result without rewriting
this registration. A failure stops this fixed run and becomes a reported input
problem, not permission to loosen validation after observing it.

Later liquidity acquisition needs its own reviewed bounded manifest. AP's
minimum matrix was 1708 trading plus 28 basic-information requests, **1736
total before cache reuse**, including these 56; additional classification
evidence is outside that estimate. Stage A success alone does not authorize
claiming those remaining histories, a finalized universe or strategy validity.

At registration: implementation/testing is in progress; Stage A actual requests
are zero, new selected universes and portfolio/comparison runs are zero.

## Implementation verification before acquisition

The new module's 83 synthetic tests passed in WSL, and the combined new and
existing probe suite passed 145 tests. Tests first failed while the module was
absent. Removing join/duplicate checks in memory caused three relevant tests
to fail, then restoring them passed. Independent read-only review found no
actionable blocker in the implementation or registration and separately ran
the 83 new tests successfully. These checks used no real API or database.

The first documentation check found the planning count in CLAUDE.md still at
145 after the new document was added; that count was corrected to 146 without
changing a research rule. The scanner and diff whitespace check passed.
The full WSL check then passed: 27 Codex guardrail tests, 34 VST guardrail
tests, and **4379 Python tests with three existing skips** in 351.69 seconds.
CI and completed CodeRabbit review remain merge requirements; the real
acquisition has not run at this point.

## Reviewed source, deployment and actual acquisition — 2026-10-07

PR #244's exact head `a23943fa7be68cdbf16f96e67ff50d027417297d` passed all
four CI workflows and received CodeRabbit approval without actionable comments
at 10:28:25 UTC. The advisory docstring-percentage warning was explicitly
answered; there were no unresolved review threads. The merged source is
`d3010fbd33c98ee3eb2de1f98311b337c7b7f217`. The isolated research checkout
was fast-forwarded to that clean source at 10:29:37 UTC after checking its
base and exact six-file diff. The collector stayed clean at `4ce85d7`, with
the previously observed collection processes still present.

The first orchestration scope, `<private-evidence-root>/krx-formations-20261007-v1`,
stopped before starting the module: its environment parser did not accept the
existing `export KRX_API_KEY=...` assignment form. Only `scope.json` existed;
there was no run directory, attempt log or API request. Preserve that scope
and its zero-request preflight result. The wrapper was corrected without
changing the credential file or the reviewed module. A fresh v2 scope retained
the same dates, services, source and request cap; this was not an API retry.

The v2 run started at **19:31:03 KST** and finished at **19:33:04 KST**.
All **56 requests returned HTTP 200 and passed schema validation**. All
**14 dates / 28 market joins** passed the full row and two-way membership
checks. Across those dates, **35,271 issue-date trading rows** matched their
basic-information rows and passed capitalization/share checks. Of these,
**944 issue-date rows** meet the size boundary and **34,327** fall below it.
Among the size passes, **926 issue-date rows** have the source's `보통주`
label. These are repeated observations across dates, not distinct companies
or a final eligible-pool count.

| formation | joined trading rows | size passes, all classes | size passes with common label |
|---|---:|---:|---:|
| 2019-04-02 | 2238 | 54 | 53 |
| 2019-10-04 | 2272 | 48 | 47 |
| 2020-04-07 | 2331 | 41 | 40 |
| 2020-10-13 | 2357 | 54 | 53 |
| 2021-04-15 | 2421 | 64 | 63 |
| 2021-10-20 | 2456 | 73 | 72 |
| 2022-04-22 | 2494 | 74 | 73 |
| 2022-10-27 | 2538 | 56 | 55 |
| 2023-04-27 | 2584 | 72 | 71 |
| 2023-11-03 | 2637 | 68 | 67 |
| 2024-05-10 | 2683 | 78 | 76 |
| 2024-11-15 | 2729 | 72 | 70 |
| 2025-05-27 | 2761 | 84 | 82 |
| 2025-12-01 | 2770 | 106 | 104 |

This is Stage A completion only. The common-label count ranges from 40 to 104
per formation, but liquidity, positive historical operating-company identity,
panel/history coverage and accounting readiness have not been checked by this
run. No named example was forced into the pool. All three readiness/certification
flags remain false. No strategy return, new portfolio, comparison or reserved
window was inspected. Task AP's earlier 21 requests are separate; Task AQ made
exactly 56 real requests, with no retries or unattempted matrix entries.

## Private evidence and independent reconciliation

Evidence remains under
`<private-evidence-root>/krx-formations-20261007-v2/`, outside the checkout.
An independent offline pass at 10:33:29 UTC checked the complete matrix against
the **112 attempt/response ledger events**, all **56 raw response files**,
every recorded response hash, per-market/per-date size counts and total sums.
The shortest response-to-next-attempt interval was **1.032052 seconds**.
It also verified directory/file modes 0700/0600 and the false certification
flags. The invocation returned zero with empty stderr, a persisted report and
a clean executing checkout; the collector's SHA and clean state matched before
and after. Verification made no API requests and opened no research database.

| artifact | SHA-256 |
|---|---|
| orchestration scope | `1da9312ffce1c42740b0cbe5fb83d8618ef27499722226ccbed4299d9eb3630e` |
| module started manifest | `2bce0c4b916a1f67415e91dd1b4372ae8425e518d98624661725c58407ee48e9` |
| final report | `17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b` |
| request ledger | `f85c8a4403eaacde5d6fd0195054568262cbbb4cdab98ffdc846ab45933f2487` |
| canonical raw-file manifest | `9e43284f9a5b6e8680f88b673fae5630c9ebb9b92c049b9d300e816203140e6e` |
| invocation receipt | `fba41ccab688640b9984de2fcad33524bc2873f42c8d327a89b610f47061610e` |
| independent verification | `ca5e4fef1355e144ac3e6f3b2501bcd7a537ed11bcec1b611229913485ac4b23` |

The next bounded work is the preceding-session liquidity acquisition and dated
type/coverage audit within this fixed large/liquid specification. The minimum
matrix still has **1680 trading requests** beyond the 56 completed here, before
any other verified cache reuse; classification evidence is additional. Register
that manifest and finish its implementation/review before acquisition. Task AO
deliverable 2's rule is fixed; deliverables 1 and 3 are partially evidenced,
while the new sizing runner and sizing/comparison decision remain unexecuted.
No operator decision is currently blocking that next data-validation work.

The result-only documentation check passed 84 tests with one existing skip;
scanner and diff checks passed. Independent read-only review recomputed the
table totals/range and remaining request count, finding no actionable issue.
It did not independently access the private GCP evidence; the offline
reconciliation above is the executed evidence check.
