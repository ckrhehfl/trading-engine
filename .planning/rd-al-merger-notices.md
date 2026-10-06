# Research Direction Task AL — verify merger candidates against exchange notices

Date: 2026-10-06. The operator continued Task AK, starting with public evidence
for historical instrument identities. This task investigates the 104 unique-name
candidates from Task AH (51 SPAC-surviving and 53 SPAC-disappearing events).
The separate eleven ambiguous pairs already investigated in Task AI are not
overwritten. This is public corporate metadata work, not a return experiment.

## Fixed acquisition and interpretation

`rd-al-merger-notice-candidates.json` preserves the 104 input rows, including
Task AH's opaque issuer/process identifiers, listing date and the contemporary
candidate code/ISIN. Its provenance identifies the exact prior result file.
A name match remains unverified until a dated exchange notice establishes the
actual common-share code and the relevant listing event. Never construct a
six-character stock code from an opaque five-character issuer identifier.

For every candidate, search the public KIND disclosure form by its candidate
code from 45 calendar days before its merger listing through seven days after,
clipped to 2019-01-02 .. 2026-09-18. Request 100 rows per page, retain all pages
when paginated, and refuse incomplete, conflicting or malformed results. This
search is a bounded discovery aid, not evidence of complete corporate-action
coverage. Do not follow chart/quotation links. Fetch only the exchange's
additional-listing, name-change listing, and SPAC-disappearance listing notices
relevant to that event. Other filings remain references in the search response.

Preserve the original search response, receipt viewer, exact document-version
resolution and external body with their request parameters, UTC retrieval times
and byte hashes. The viewer may expose later corrections: retain their separate
publication dates and do not attach their contents to the original publication
time. The receipt number and body document number are distinct identifiers.
Bodies are restricted to the observed official KIND external-document host/path.
Existing evidence is never overwritten; an interrupted acquisition must remain
recognizably incomplete and cannot publish a completed result.

The two observed shapes require different evidence:

- A surviving SPAC's additional-listing notice supplies the common-share code,
  company name, merger issuance reason and listing date. Its name-change notice
  connects that pre-change corporate name with the operating-company name. A
  name-change notice alone need not contain a code and cannot certify one.
- A disappearing SPAC's merger-listing notice supplies the operating company's
  common-share code and ISIN, listing date and absorbed SPAC name. It does not
  automatically supply the absorbed SPAC's code, exchange ratio or effective day.

Parse code fields under their actual table headers, not arbitrary numbers in
the body. Compare the event date and preserve discrepancies with the source
list and current candidate identity. Retain every input row, including failures.
An unavailable source is unresolved, never an exclusion from the research pool.

The acquired record establishes only the facts its particular notice says.
Do not convert it into ready `IdentityPeriod` inputs or backdate a later-known
fact. Initial instrument history, pre-conversion lookbacks, legal/event timing,
full-pool coverage and all held-lot corporate actions still need Task AG's gates.
No raw/adjusted-price request, credential read, database access, corrected sizing,
comparison, holdout access, collector change or trading promotion is included.

## Implementation and verification record

Implementation, actual acquisition counts and review results are appended here
as they complete; the specification above is not a claim that acquisition has
already succeeded.

The implementation is `research.kind_notices` (pure parsers) and
`research.activity_merger_notices` (the fixed public acquisition and offline
replay command). The initial parser verification passed 64 new cases and 28
existing merger cases under WSL. Removing its multiple-security rejection in
memory made three counterexample tests fail; the saved production source was
not changed by that mutation check. Real Neosem additional/name-change and
NewKidsOn SPAC-disappearance listing pages were inspected before the parser
was generalized. Their common-share fields, not current-name search labels,
are the independent source for each asserted code.

Response publication uses exclusive, fully written files. The aggregate result
is created only after all 104 candidate records; an unexpected transport or
filesystem failure leaves an explicit incomplete record. Offline replay uses
only saved requests and byte hashes and cannot fall back to the network.
An unsupported filing or a changed identity/date in a correction keeps the
candidate unresolved. The safe error direction is retaining an unknown input,
not selecting it out of the pool or granting historical eligibility.

### First acquisition and bounded parsing repair

The first 104-candidate acquisition, code `273b9ff`, saved 323 public responses:
51 listing identities verified and 53 unresolved. Of the latter, 52 search
pages contain KIND's exact `icn_t_jung.gif` marker, whose alt text says that
a later correcting report exists. The marker must be preserved separately
from the title and the viewer must expose the correction; it is not a reason
to ignore the row. One remaining search title is the observed dated variant
`SPAC소멸합병상장(2023.2.17)` for FineCircuit, receipt `20230215000891`.

Before any follow-up acquisition, extend the parser only for these observed
forms, with rejection tests for other markers. Add an explicit `--resume-dir`
mode which verifies and reuses these saved public responses and fetches only
missing requests from the same fixed scope, into a fresh output directory.
The existing `--source-dir` stays strictly offline and missing responses remain
a fatal incomplete replay. A source hash mismatch never triggers a refetch.
This repair neither expands the candidate/date scope nor adds a price endpoint.

The second acquisition, code `13952b1`, reused all 323 prior responses and
obtained 271 missing public responses (594 unique responses in total). It
verified 96 candidates and retained eight unresolved because correcting bodies
prepend an additional correction section. All 104 searches now parse: 1,982
search rows, of which 91 carry the later-correction marker (including unrelated
filings whose bodies are not requested).

The next repair is offline only: validate the observed correction preamble,
read that version's following listing table, and retain the preamble's correction
date and original-submission date as separate provenance. Compare the correction
date with the viewer's document publication date. Never reconstruct an original
version from the preamble's before/after text or assign a correction the original
submission date. Do not compare provenance differences as identity differences;
continue to reject changes in the asserted identity fields. Preserve both
acquisitions and create a separate offline interpretation result.

The first offline interpretation (`6247f00`, zero network requests) parsed all
164 distinct bodies, including nine corrections. It verified 102 candidates;
Proicheon (321260) and Lycom (388790) remained name conflicts. In both cases the
original additional-listing notice uses the SPAC name, while a later correction
uses the operating-company name. Their separately acquired name-change notices
explicitly link those exact names on the same listing date; code, date and all
other extracted listing fields agree across versions.

Resolve only that observed case: a correction published on/after the listing
and after the linking rename notice may use the exact verified after-name.
An arbitrary new name, an earlier correction, any code/date/other field change,
or a conflicting rename remains unresolved. Include the later correction in
the selected evidence and use the latest publication date of that whole set,
so this reconciliation never backdates availability. This remains notice-level
identity evidence with `known_on=null`, not historical interval certification.

## Completed evidence and remaining gates

Final offline interpretation `b162d29` verified **all 104 fixed candidates**:
51 SPAC-surviving and 53 SPAC-disappearing listings, with no parse failures.
It made zero network requests. Across the two acquisitions there were 594
unique public response requests, including 164 distinct notice bodies and nine
correcting bodies. The saved source ledgers and all response hashes were checked
again before publication. No raw/adjusted quotation or return computation was
performed. The separate eleven Task AI pairs and their unresolved dates are
unchanged.

`rd-al-merger-notice-evidence.json` contains every candidate, its selected and
reviewed document numbers, exact extracted corporate fields, per-document byte
hashes, correction provenance, and hashes/counts for all four preserved runs.
It keeps every `known_on` null and every `historical_intervals_ready` false.
For Proicheon and Lycom, the evidence availability dates are conservatively
2021-11-12 and 2023-02-16 respectively, including the later name-bearing
correction rather than backdating that reconciliation to the original notice.

The public evidence is preserved on GCP under
`/home/minjun4897/research-evidence/activity-merger-notices-20261006/`, with
`first/`, `resumed/`, `initial_interpretation/`, and `final/` subdirectories.
All 2,537 archived files and the per-response ledgers were verified there.
The 4,151,857-byte archive SHA-256 is
`3b405be695b698a6af91aca7127c253a3488d73cdccb76e5ae261084d87c72ba`;
the final result SHA-256 is
`bbb7ee826d342eec24c3d045bbe8db9a7ba40efa1a7a56efa63baf0f895bdc31`.
These are newly acquired public documents, not copied trading databases, logs
or credentials. The collector checkout remained clean at `4ce85d7`; the
existing pre-2019 collection script processes were observed separately.

This closes the 104 **candidate-to-listing-notice identity checks**, not the
full historical security-master or corporate-action ledger. A disappearing
SPAC's name still does not prove its old stock code, exchange ratio, effective
date or net payout. Initial instrument classifications, pre-conversion
lookbacks, contemporaneous availability, the full bar-eligible pool, all held
intervals (including closed/successor lots), vendor price-basis semantics and
actual dataset compatibility remain prerequisites. Task AK's consumed
credentialed diagnostic approval was not reused. Task AD's cost-floor stop,
the reserved window, and the runner/preregistration gates are unchanged.

Final code review included independent counterexamples to the dated rename
bridge; all nine rejected inputs remained unresolved. The local full suite
collected before its last ten bridge cases passed 4,171 tests with three
existing skips in 301.78 seconds. The final focused suite passed 234 tests with
one existing skip, including those ten cases. Both guardrail suites passed
(27 and 34 tests), and the scanner and diff whitespace check passed. Applicable
change checks (`require_no_blockers`) passed for the demonstrated guard mutation,
allowlist boundary, isolated evidence writer, actual-record counts and declared
safe error direction. Final GitHub CI and CodeRabbit results follow the PR.
