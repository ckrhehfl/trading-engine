# Research Direction Task AH — acquire merger history without inventing identities

## Scope

The operator continued Task AG on 2026-10-06. Acquire the observed KRX KIND
merger-listing source for the spent 2019-01-02 through 2026-09-18 window and
reconcile every returned name against both live and delisted identity snapshots.
This is metadata acquisition, not a new return trial, historical eligibility
certification, or a corrected sizing run. Task AG's readiness gates remain.

The [official merger-listing form](https://kind.krx.co.kr/listinvstg/mergeListingCompany.do?method=searchMergeListingCompMain)
exposes both SPAC-surviving and SPAC-disappearing types. Its metadata-only
seven-column response includes the company name, merger listing date, type,
security, industry, country and sponsor, plus opaque issuer/process identifiers.
No optional price columns are requested. A process identifier is not a filing
publication date; an issuer identifier is not a verified six-character code.
The source's statistics caveat from Task AG still prevents a completeness claim.

## Implementation and failure direction

`research.activity_mergers` saves the original response and its SHA-256, request
URL, retrieval timestamp, committed code version, and a reconciliation report
in a new directory. It refuses overwriting earlier evidence. A rejected source
is retained for diagnosis without a successful `result.json`.

Parsing requires the observed columns, identifiers, merger/security types,
requested date bounds, non-duplicate events, and a single page whose declared
total equals the parsed count. Empty/error/truncated responses are failures,
never an empty historical universe. This checks response completeness, not
whether KRX's underlying list covers every historical conversion.

Read-only database transactions select scan statuses and the latest live and
delisted identity snapshots. A wrong/reserved scan panel is refused before
identity queries. Prices are never selected. Every listing remains in output,
including unmatched names, conflicting live/dead candidates, failed scan codes
and codes absent from the scan. Even an exact unique name match is explicitly
a **candidate**; it neither backdates today's name nor grants eligibility.

The metadata hash fingerprints scan statuses and selected snapshot rows. The
source hash separately fingerprints the public response. No credential or
collector file is read, copied or modified. No automated schedule is added.

## Price-basis acquisition boundary

The isolated research SSH environment has neither `KIS_APP_KEY` nor
`KIS_APP_SECRET` (presence checked, values never read). The existing manual
backfill procedure in `docs/paper-trading-runbook.md` deliberately reserves
credential entry for the operator; the collector's `.env` fallback is not
authorization to create a new credential-reading helper. Thus this acquisition
uses the public KIND source and does not yet obtain raw quotes. Raw/adjusted
bridges and verified net payment evidence remain prerequisites to replay.

The public KRX Data Marketplace menu identifies individual stock history as
`MDC0201020103`. Opening its loader on 2026-10-06 returned a login/registration
requirement before any historical quote query; no price request was submitted.

## Initial verification

Focused local verification passed **57 tests, one existing skip**. Removing the
response-count equality guard made the partial-response case fail (`DID NOT
RAISE`); restoring it passed. Tests also exercise duplicate/conflicting events,
wrong dates/types, alphabetic issuer identifiers, ambiguous live/dead matches,
delisted and absent scan codes, read-only file purity, reserved-panel refusal,
dirty-source refusal and evidence overwrite refusal. A direct public-source
parse reproduced **115 rows: 62 SPAC-surviving, 53 SPAC-disappearing**.
