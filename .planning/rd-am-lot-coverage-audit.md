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

## Reviewed implementation and GCP preparation

PR #233 completed CodeRabbit review with approval at 2026-10-06T13:25:52Z,
no actionable findings and no review threads. All required checks succeeded;
final CI ran **4,231 passed, three existing skips** in 193.91 seconds. The
reviewed head was `99288e825804af2ee9d445d28d72fed50cfe4470`; merged commit
`fb153a7a8f8fc042e059f0f439522a8b77349278` has the identical file tree.

The isolated GCP research checkout was moved to that merged commit and passed
all 79 focused portfolio, lot-audit and metadata tests in 1.79 seconds before
the real replay. The collector checkout remained clean at
`4ce85d714890b87f1a57ae89d4942660e41c0483`; the existing pre-2019 collector
processes were still present. No collector source, schedule or process changed.

The nine public sample files are preserved at
`/home/minjun4897/research-evidence/activity-lot-coverage-20261006/classification-sample-20190102/`.
The 14,549-byte ZIP archive SHA-256 is
`e42cf4a75a00bad6cac21337837a95072d2fe1c63d8fabbdebf6eff648c7da73`.
All six raw response hashes, result hash and 50 unique codes were verified
again after storage on GCP.

## Completed replay and coverage result

The audit ran once on 2026-10-06, from 13:28:53.452058 to
13:31:06.557600 UTC, as discovery run
`d2e2568c-33dc-4181-945a-1e171f04a568`. Its specification SHA-256 is
`c21e81f1d88000538e254b00d7ccda4e50e39a1da858d1655829e6411c66c4e7`.
The price dataset hash matched the original pilot exactly:
`fc8a57eae72c33a84e31a3bc96b45110b1db2903c22c3c5b6fd3c67d0e2809e6`.
All accounting counters matched, not merely the three headline counts:

| observed quantity | count |
|---|---:|
| successfully entered lots | 245 |
| closed lots | 237 |
| residual lots | 8 |
| distinct held codes | 232 |
| invested sessions | 1,834 |
| missing/frozen position-sessions | 5,159 |
| pending-exit position-sessions | 4,408 |
| delayed exits | 2 |
| unfilled entry attempts | 2 |
| limit-locked fills | 1 |

Every successful entry now has a retained interval, including the 237 closed
lots and repeated entries of the same code. The measured command time was
133.47 seconds and peak child RSS was 173,400 KiB. The canonical log contains
exactly one new started/completed pair, with promotion disabled and promotion
trial increment zero. Its original 7,807-byte prefix is unchanged. Full log and
lot records remain on GCP; `rd-am-lot-coverage-summary.json` contains the bounded
coverage summary rather than a copied experiment log or price panel. On
2026-10-07 the stored artifact hashes and six complete log records were checked
again, without another replay or price read.

The metadata inventory retains all 3,043 completed codes. At acquisition its
latest live/delisted snapshot dates were both 2026-10-06 (16 and 12 snapshots,
respectively). Counts remain 2,826 current-filter passes, 212 SPAC labels, two
REIT labels, two other exclusions and one missing/multiple identity. Its
metadata hash is
`08301ba87620151eaca0cb731ddc5de8c291c2caa2093393cf4e8692b49eedbd`.
The pilot's 245 lots map to 237 current-filter passes and eight current SPAC
labels. These are current-label diagnostics: the eight SPAC-labeled lots are
not being identified with the eight residual lots, and neither count assigns
a historical instrument type.

### Already closed holdings also require investigation

Join each lot by exact six-character code to Task AL's 104 explicit listing
identities and both explicit parties of Task AI's eleven pairs. This gives
115 events and 126 event/code links. Compare the listing day with the inclusive
calendar interval from entry through actual exit, or audit cutoff if unresolved.
Keep events before entry and after exit too; their legal effect and relevant
lookback history are not inferred from the listing day.

Ten distinct closed lots match ten event/code links: eight listings precede
entry, one falls inside the holding interval, and one follows exit. The ten
rows, dates and evidence identifiers are retained in the companion summary.
This is a coverage join, not ten proven erroneous trades or ten complete
corporate-action audits.

- **원텍 `336570`**, held 2022-04-25 through 2022-10-28, spans the 2022-06-30
  merger listing. Task AI separately reports merger day 2022-06-14 and
  registration 2022-06-15. The held code is the surviving SPAC, formerly
  대신밸런스제8호; AI's `12.8635762` exchange ratio belongs to old operating
  code `216280` and must not be applied to this surviving-code lot. This
  concrete closed-lot case would be missed by investigating only residuals.
- **와이즈버즈 `273060`**, held 2019-10-07 through 2020-04-20, has a later
  merger listing on 2020-08-05. The 2019-01-02 public sample classifies this
  code as a SPAC. These observations motivate reconstruction of its entry and
  lookback classification; they do not establish a continuous interval by
  themselves, nor does the later listing certify an event-free earlier lot.
- **현대무벡스 `319400`** entered 2021-04-16 after a 2021-03-12 merger listing.
  Checking entry type alone cannot certify the preceding 60 non-frozen
  observations used by the signal. Their actual dates and types remain open.

Task AL does not supply the old disappearing-SPAC codes for its 53 events.
Thus the absence of residual matches in this particular join does not clear
those lots: Task AF already establishes successor events for residual codes
`367480` and `476470`, among its other findings. No unmatched interval has
been certified free of corporate actions.

### Historical sample versus current labels

All 44 SPAC codes in the 2019-01-02 company-level sample are present in the
completed scan. Only 17 retain a current SPAC label; **27 pass the current
name/ISIN filter**. Those 27 codes and displayed names are retained in the
summary. This quantifies the failure of current labels as a substitute for
historical classification; it is not a count of invalid pilot entries.

Of six historical REIT codes, three are outside the completed scan, two are
currently labeled REIT and one passes the current filter. The last is delisted
`140890`, 트러스제7호, ISIN `KR7140890005`: its current name does not match the
REIT name heuristic, while the dated KIND row explicitly says 부동산투자회사.
The source is company-level and contains current names, so none of these rows
is assigned an original publication time or continuous eligibility interval.
No collector filter or portfolio input was changed using this sample.

### Retained evidence and remaining work

The GCP directory `activity-lot-coverage-20261006` contains the complete audit
record, full metadata inventory, execution metadata, public sample and
`coverage-summary.json`. The latter's SHA-256 is
`51bbbb3dd4aa8085a4724076cea4a986fe6f044297826b7b2a6998fe1254c183`;
the initially imported repository summary had identical JSON content (formatting
was independent). The field-meaning correction below supersedes that import.
Hashes of its input evidence and full GCP artifacts are embedded in the summary.

Next acquire and cross-check dated instrument classifications for the complete
preselection pool and the actual lookback observation dates, starting with
the source's date behavior and the concrete transitions above. Complete the
missing old-SPAC identities and systematic corporate-action obligations for
all held intervals, including lots a corrected runner would create. Raw versus
adjusted quantities, successor availability and verified net cash recovery
remain Task AG gates. Only after those and runner integration can a newly
registered sizing trial be considered. The original comparison stop remains;
no strategy direction is closed, holdout spent, or conclusion added to CLAUDE.md.

## Result verification and field-meaning correction — 2026-10-07

Local arithmetic and source checks matched every linked code and listing date
to the preserved AI/AL ledgers. On GCP, an independent in-memory SQL join of
all 245 lot intervals and all 126 event/code links reproduced exactly the ten
reported links and their before/during/after categories. The original accounting
still matched the committed reference. `check_same_population` verified the
lot-ID partition, and `check_claim_universal` verified that every traced code
is in the full metadata inventory and all ten linked lots are closed; no
blockers remained. No independence, return or significance claim is made, so
the statistical power/DSR/overlap checks are not applicable to these counts.
Planning-index verification passed 30 tests with one existing skip.

Independent evidence review found one semantic defect in the first summary:
the AL event's merger type had been put in the `role` field. This could make
`380540` (옵티코어) and `298830` (슈어소프트테크) look like disappearing SPAC
codes, although the verified notices identify them as surviving operating
companies. The disappearing SPACs are KB20 and NH22, respectively. These
relationships were rechecked in the locally archived, hash-verified listing
notices `20221227003039` and `20230426002191`.

Preserve the first GCP summary unchanged and write `coverage-summary-v2.json`.
Version 2 separates `merger_type` (`spac_survives` / `spac_disappears`) from
the participant `role` (`surviving_spac` / `surviving_operating_company`, with
`old_operating_company` reserved for that actual role). Counts, dates, code
matches and replay output are unchanged. The repository companion summary now
contains this corrected version, not the initial import.

The v2 raw SHA-256 is
`761d5ac0539e141f2a1eabe8a8d744f0e10582fa177ffac29a17857eb6db5236`.
Canonical JSON SHA-256 (UTF-8, sorted keys, no insignificant spaces) is
`c73211e82191f58cb89c40ab15959d8c82bb3737b7568a203a2592fbb4eaefa2`.
The GCP archive and repository summary were compared using that canonical form.
The independent review found no other actionable discrepancy; it did not itself
rerun the GCP experiment or inspect all raw lot records.
