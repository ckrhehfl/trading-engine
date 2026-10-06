# Research Direction Task AM — recover the pilot's complete holding intervals

Date: 2026-10-06. The operator requested autonomous continuation after Task AL.
The previous pilot records 245 entries, 237 closes and eight residual positions,
but does not persist the closed holding intervals. Auditing only the residuals
would leave corporate actions during the other holdings unexamined.

## Fixed scope before execution

Add opt-in observation of successful entries and actual exits to the existing
portfolio simulation. Retain every entered lot, including repeated entries of
the same code, with formation, entry, scheduled exit, actual exit (or null) and
audit end dates. Trace values must never feed selection, allocation, fills,
marks or cash. No simulated cash recovery or successor conversion is added.
The trace contains dates and identifiers, not return or price summaries.

A separate committed discovery audit specification replays Task AD's exact
parameters, window and dataset hash. Require all recorded accounting counters
to agree with the pilot and require the trace to reconcile to entries, closes
and residuals. Log a durable discovery start before loading the spent panel.
Do not bootstrap, run a comparison or recompute a detection floor. Merge the
reviewed implementation and specification before executing against real data.

Extend the existing metadata-only readiness inventory with an opt-in complete
code inventory, retaining current labels, missing identities and conflicts.
Do not use these contemporary labels as historical eligibility or remove codes.
The existing pinned read transactions and metadata hash cover those identities.
No additional price query is needed to export the full scan code universe.

Run only in the isolated GCP research checkout, with the established memory and
CPU limits, read-only collector database connections and its durable research
log. Do not touch the collector checkout, scheduler, credentials, reserved
pre-2019 prices, or forward price windows. Keep full run logs and audit artifacts
on GCP; bring back bounded accounting/coverage summaries only.

The resulting lot intervals are retrospective v1 diagnostic obligations. They
are neither point-in-time instrument classifications nor the lots of a future
corrected simulator. Joining the 115 previously investigated merger listing
events can identify overlaps and pre-entry history to investigate; absence of
a match cannot certify an interval free of corporate actions. Merger listing
dates must not be relabeled as merger effective dates. Full candidate-pool and
lookback classification, other corporate actions, successor lots, cash recovery
and raw/adjusted quantity compatibility remain separate gates in Task AG.

## Verification planned before the real replay

Synthetic comparisons must show identical original simulation output with and
without tracing, including delayed exits, missing/frozen bars, repeated entries
and unfilled attempts. Test trace reconciliation, changed dataset/spec refusal,
logging before load, and no performance fields in the audit output. Verify the
complete metadata inventory against missing/conflicting identities and database
byte preservation without requiring a price table. Run WSL project checks,
inspect the diff, resolve CodeRabbit findings and merge before execution.

The v1 comparison stop remains. This task makes no strategy conclusion and does
not change CLAUDE.md beyond the mechanical planning-document count.

## Pre-execution operational path correction

Read-only inspection found no `runs/experiments.jsonl` in the isolated research
checkout. The actual canonical GCP discovery log is
`/home/minjun4897/trading-engine/runs/experiments.jsonl`: four complete records
contain exactly the started/completed pairs for Task AD's
`13adac55-56b3-460a-842d-7299105b5eca` and Task AE's
`e66c47cb-967c-4cb5-8e9d-e9734637d0ee`, with their recorded code, specification
and dataset hashes. Its pre-run SHA-256 is
`c68b6ba05d6e6cf79248f6b5fac9a1f230f2795d24490820d9fa7b6314a53464`.
Earlier prose locating this log in the research checkout was inaccurate.
Append the new discovery pair to this existing log while executing code from
the isolated research checkout; do not copy or recreate the earlier records.
This append is a research-log operation, not a collector deployment or database
change. Retain the complete original byte prefix and verify it after execution.

## Bounded historical-classification source sample

Official-source reconnaissance found KIND's `listedIssueStatus` date selector
and its `listedissuestatusdetail.do` form. The 2019-01-02 query reports 44 KOSDAQ
SPAC companies and six KOSPI REIT companies. Fix a six-response metadata sample:
SPAC pages 1..5 and REIT page 1, each with the observed page size 10, query date
`20190102`, detail type `1`, method `searchListedIssueStatDetailSub` and forward
`listedissuestatdetail_sub`. Use `mktId=KSQ, secugrpId=SP` and
`mktId=STK, secugrpId=RT`, respectively. Preserve raw bodies, request parameters,
retrieval timestamps and SHA-256; refuse pagination/count disagreement rather
than increasing the acquisition silently. No prices or chart endpoints.

Rows expose explicit six-character codes and classifications, but the names
and status badges can describe the present. Preserve those as displayed labels
only. The source also describes company-level information: this sample cannot
certify all share classes, continuous historical intervals, original publication
availability or lack of retrospective corrections. Keep `known_on` unassigned
and do not use the sample as a portfolio filter. Compare its codes to the full
metadata inventory solely to quantify why current names cannot establish past
SPAC/REIT status. Source: https://kind.krx.co.kr/corpgeneral/listedIssueStatus.do?method=loadInitPage

## Initial synthetic verification

The independent lot-audit suite plus existing portfolio regressions passed
74 tests in WSL. Nine traced/non-traced scenarios compare the complete original
result and all input objects. Dates, delayed exits, same-day exit/re-entry,
unfilled attempts and unresolved lots have separate assertions. Corrupt trace
records and any changed reference accounting counter are rejected. CLI tests
use the real temporary durable log, verify it exists when the fake loader is
entered, and forbid calibration/bootstrap in audit mode. Removing the accounting
comparison in process memory made the negative control fail with `DID NOT
RAISE`; a fresh process with the unmodified source passed all 74 tests again.
Five metadata inventory tests passed separately, including full unknown and
conflicting identities and byte-preserved databases with no price columns.
The repository scanner and its 27 + 34 guardrail regression checks passed.
The local full-suite collection passed 4,225 tests with three existing skips
in 278.58 seconds; the independent test worker added six final cases after that
collection began, and all 74 focused portfolio/lot cases passed afterward.
Independent read-only implementation review found no actionable correctness
issue. Required CI will check the final collected test set before merge.

The fixed public sample completed with exactly six HTTP 200 responses and page
row counts `10, 10, 10, 10, 4, 6`: 44 SPACs and six REITs, 50 unique codes.
Local public artifacts are under
`var/public-kind-am/20261006T1318084577476Z/`. The parsed result SHA-256 is
`3433ff33c7eea1a4b529914c894a3c4da2cc708a95d31e02bffc44b4e7020b79`;
the six-response ledger SHA-256 is
`db5cdfe26b211cf06705e0d3a73915bc5194980d467ae67ef48578b5cac7942d`.
An initial PowerShell dictionary-grouping validation misreported duplicates;
it was corrected by rechecking the saved JSON and raw response rows, without
another request. The initial result remains preserved separately. Codes such
as `307180` (displayed 아이엘), `310200` (애니플러스) and `307750` (국전)
demonstrate the current-name/historical-SPAC mixture. No historical eligibility
intervals or contemporaneous publication time have been assigned.
