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
