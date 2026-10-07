# Research Direction Task AN — post-SPAC baseline and dated-source boundaries

Date: 2026-10-07. After reviewing the Task AM coverage findings, the operator
selected option 1: require 60 normal observations from the operating-company
period before a converted SPAC can enter the next corrected activity study.
Do not carry SPAC-period turnover into that baseline. This is a prospective
research-design choice, not a claim about returns or a change to the frozen v1
pilot. The existing comparison stop and Task AG data/accounting gates remain.

## Meaning of the selected policy

At formation, the issue must have verified eligible operating-company identity.
Its baseline uses the preceding 60 valid, non-frozen observations belonging to
that operating-company period. The formation observation is separate from the
60 preceding observations. Missing or frozen sessions do not count; a positive-
turnover limit-locked bar retains the project's existing distinction from a
halt. There is no fixed three-calendar-month shortcut: gaps and suspensions
can make the wait longer. Do not substitute observations from the predecessor
SPAC or from another code to reach 60. Insufficient valid history means no
entry under this policy; unknown/conflicting identity still stops the proposed
replay rather than silently excluding a name.

The effective boundary and information-publication time need their own evidence.
A merger listing date, legal merger date, registration date, name-change date
and resumption date are not interchangeable. Until that boundary is verified,
neither the first qualifying observation nor a first eligible entry date can
be assigned. This decision does not authorize assigning `known_on` from today's
download, ignoring the full preselection pool, or running corrected prices yet.

## Bounded source check, fixed before acquisition

Task AM found a public date-selectable KIND company classification table with
explicit stock codes but mixed current names. Check its date behavior around
two actual pilot examples before expanding to the entire panel. This is public
corporate metadata only, with no price/chart endpoints or credentials.

Use the same observed POST form at
`https://kind.krx.co.kr/corpgeneral/listedissuestatusdetail.do`, method
`searchListedIssueStatDetailSub`, forward `listedissuestatdetail_sub`,
`mktId=KSQ`, `secugrpId=SP`, `detailType=1`, and page size 10. Retain every page
of each SPAC snapshot, with a maximum of 20 pages per date; refuse inconsistent
pagination or excess size without quietly extending the protocol. Follow one
request at a time with at least one second between request starts. Preserve
request parameters, UTC times, raw response bytes and SHA-256; do not overwrite
existing evidence on interruption or retry. The nine fixed query dates are:

- `20210311`, `20210312`, `20210415`: around 현대무벡스 `319400`'s March 12
  merger listing and the day before the pilot's April 16 entry.
- `20220422`, `20220613`, `20220614`, `20220615`, `20220629`, `20220630`:
  원텍 `336570` before the April 25 entry and around Task AI's separately
  recorded June 14 merger day, June 15 registration and June 30 listing.

Record each target's presence or absence in the complete SPAC snapshot, total
codes and displayed labels. Absence from a SPAC list does not prove common-stock
eligibility, legal effect, continuous coverage or contemporary availability.
Do not use the current names to classify these historical records. Compare
the source's observed boundaries with already preserved notices; disagreement
or a shifted boundary is a finding to investigate, not a reason to override
the documentary dates or retroactively modify the pilot.

No new return experiment, price read, collector change, holding conversion or
paper/live promotion is part of this task. The first implementation remains
the reviewed specification and evidence record; a new portfolio runner must
wait for the full Task AG gates.

## Acquired result — 2026-10-07

All nine fixed snapshots completed: 54 requests, all HTTP 200, six pages per
date and 508 code/date rows in total. Every snapshot's row count matches its
reported total and has no duplicate codes. Recorded request starts were at
least 1,000 milliseconds apart. There were no additional requests or retries.
The companion `rd-an-dated-spac-evidence.json` contains the bounded result;
the complete raw tables and request/response ledger remain archived separately.

| query date | total SPAC codes | 원텍 `336570` present | 현대무벡스 `319400` present |
|---|---:|---|---|
| 2021-03-11 | 56 | yes | yes |
| 2021-03-12 | 55 | yes | no |
| 2021-04-15 | 57 | yes | no |
| 2022-04-22 | 55 | yes | no |
| 2022-06-13 | 56 | yes | no |
| 2022-06-14 | 56 | yes | no |
| 2022-06-15 | 56 | yes | no |
| 2022-06-29 | 59 | yes | no |
| 2022-06-30 | 58 | no | no |

The source responds to the requested historical day, while retaining current
company names: rows already display 원텍 and 현대무벡스 before their respective
merger listings. The displayed listing-date fields remain 2019-12-19 and
2019-05-08 when those rows are present. These fields are preserved as displayed
listing dates, not relabeled as merger dates or baseline reset dates.

For 현대무벡스, the observed SPAC-list change is between March 11 and March 12,
2021, consistent with Task AL's merger listing on March 12. For 원텍, the
observed change is between June 29 and June 30, 2022, consistent with Task AI's
June 30 listing. Crucially, the latter remains in this source's SPAC category
on the separately reported June 14 merger day and June 15 registration date.
The source's category boundary therefore cannot be substituted for statutory
merger effectiveness, nor can those legal dates alone choose the 60-observation
baseline boundary. These are distinct facts the eventual ledger must retain.

The April 22, 2022 row places `336570` in the source's SPAC category immediately
before the pilot's April 25 entry. This is additional diagnostic evidence for
the closed-lot issue from Task AM. The April 15, 2021 absence of `319400` does
not certify that its preceding 60 observations belong to a verified operating-
company period. No first eligible date or revised pilot entry count has been
computed from these nine metadata snapshots.

## Evidence preservation and verification

Local public artifacts are under
`var/public-kind-an/20261007T0114326730084Z/`. All 56 source files (54 raw HTML
responses, one 108-event request/response ledger and one parsed result) were
archived to
`/home/minjun4897/research-evidence/activity-post-spac-baseline-20261007/`.
The archive was transferred through WSL SSH, with no credential contents or
research databases included. Every raw response's byte count and SHA-256, the
result and ledger hashes, nine complete snapshots and 508 rows were checked
again on GCP.

| artifact | SHA-256 |
|---|---|
| parsed complete result | `ee8a1da02c9981b5917ca35e14a58233067a04d9e040fc247bb68a873a839762` |
| request/response ledger | `91b4528215aecaf49621e73c57891f6edc3c4f14df1e34dfc87d456035f6da42` |
| 99,217-byte ZIP archive | `b246b10230800f3f7302e725ac571308a5b0226c145e9e0c92d9fa399d74a096` |

The selected policy passed independent review against Task AG: formation and
baseline observations remain distinct, missing/frozen sessions do not count,
and insufficient history is distinguished from unknown identity. Planning
index checks passed 30 tests with one existing skip before result publication.

The next data work is to establish positive operating-company classification
and public evidence for each transition, then extend that contract across the
full preselection pool and required observation dates. A SPAC-list absence is
not a positive ordinary-share record. Keep `known_on=null` and historical
intervals unready in this acquisition; do not backdate the retrieval or silently
fill the ledger. Task AG's corporate actions, quantity bases, recoveries and
runner-integration gates still apply before any newly registered sizing run.

Independent result review parsed all 54 preserved HTML responses and reproduced
the 508 rows and target-presence table without finding a discrepancy. Separate
WSL checks matched the repository summary to the saved source JSON, verified
all response statuses and the date population with `conclusion_check`, and
measured a minimum recorded start interval of 1,004.4397 milliseconds. The
repository scanner and diff whitespace check passed. This verification read
only saved public metadata; it did not rerun prices or acquire another response.

## Scope update — 2026-10-07

The operator subsequently selected large, highly liquid common stocks as the
next study's target. Task AO (`rd-ao-large-liquid-scope.md`) records that decision
and its source-feasibility inspection. It replaces this task's proposed default
next step of expanding classification across the old full-market pilot pool.
The post-conversion baseline decision and acquired evidence remain valid; the
new scope still needs dated eligibility and correct accounting for every
candidate/holding relevant to its own declared rule.
