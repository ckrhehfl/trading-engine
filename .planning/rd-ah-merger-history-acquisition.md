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

## Real-host transport correction

At committed code `1c3f3f5`, all 23 new tests passed on GCP, but the real KIND
request returned HTTP 403. The public query worked locally. The first attempt
left an empty evidence directory and no successful report. No reason for the
403 was established, and no claim of GCP acquisition success is made.

The command now also accepts an explicitly saved public response, paired with
its original timezone-aware retrieval timestamp. It hashes and retains those
exact bytes and records `saved_html` as the transport. This imports public
metadata into the isolated research directory; it does not copy trading
databases out of GCP or change credentials. The fixed query URL and timestamp
are acquisition provenance, not facts an HTML parser can independently prove.

## Acquired evidence and reconciliation result

The successful GCP audit ran committed code
`a05581a943e46a6507fa5099488555cf7e71cde1`. The original public response was
retrieved locally at `2026-10-06T07:17:27.381210+00:00`, transferred as public
metadata, and its SHA-256 checked before and after import:
`25d1e082fceada74fcf1899fba20a4fcca9979ce61d0538db96443748ec0564e`.
The durable evidence is under
`/home/minjun4897/research-evidence/activity-mergers-20261006-0717/`
(`kind-mergers.html`, `result.json`). The metadata hash is
`996a155f109f0d477564fea1d9d1b254ef3b4df0870a09aa5cc6088dae76125a`.

Both identity snapshots had advanced to **2026-10-06** while the collectors
continued normally. The successful report contains **115 events, 104 unique
name candidates, 11 ambiguous events, zero unmatched**. All 126 candidate
identity rows have `done` scan status; this does not resolve which code the
event names or establish the classification of its historical bars.

| merger type | candidate shape | events |
|---|---|---:|
| SPAC survives | one live identity | 49 |
| SPAC survives | one delisted identity | 2 |
| SPAC survives | live and delisted identities, different codes | 10 |
| SPAC survives | two delisted identities, different codes | 1 |
| SPAC disappears | one live identity | 53 |

The ambiguous rows are substantive identity collisions, not duplicate copies of
one ISIN. Preserve both sides pending completed-event filings:

| name | merger listing | candidate codes (no choice implied) |
|---|---|---|
| 포인트엔지니어링 | 2019-07-16 | 256630, 176560 |
| 자비스 | 2019-11-15 | 254120, 230400 |
| 한국비엔씨 | 2019-12-03 | 256840, 226610 |
| 소프트캠프 | 2019-12-30 | 258790, 210610 |
| 카이노스메드 | 2020-06-08 | 284620, 220250 |
| TS트릴리온 | 2020-12-30 | 317240, 284610 |
| 엠에프엠코리아 | 2020-12-30 | 251960, 323230 |
| 원바이오젠 | 2021-02-09 | 307280, 278380 |
| 휴럼 | 2021-07-27 | 353190, 284420 |
| 원텍 | 2022-06-30 | 336570, 216280 |
| 지슨 | 2025-08-14 | 446840, 289860 |

Task AG's three names absent from the live-only join are now accounted for:
비올 (`335890`) and 제이시스메디칼 (`287410`) each have a unique delisted
candidate; 엠에프엠코리아 has the two above. The
[Jeisys issuer filing](https://kind.krx.co.kr/external/2021/08/13/001338/20210813003733/11012.htm)
corroborates its SPAC merger and 2021-03-31 new-share listing. The
[MFM issuer filing](https://kind.krx.co.kr/external/2024/08/14/003055/20240814009704/11012.htm)
distinguishes the original SPAC listing from its 2020-12-30 merger listing.
These are retrospective corroboration; their later publication dates cannot
be backdated as contemporaneous `known_on` evidence. The
[ViOL delisting notice](https://kind.krx.co.kr/external/2025/11/27/000986/20251127002053/70769.htm)
identifies `A335890` and delisting on 2025-12-10. No payout is inferred from it.

This acquisition exposes another reason not to repair only the eight residual
lots: a former operating-company code can coexist in the scan with the renamed
SPAC code. Both need their own classification and corporate-action history.
The report deliberately does not output `IdentityPeriod`, effective dates,
publication dates, exchange ratios, or a runner-ready eligibility list.

GCP passed all **27 tests** of the final acquisition module in 0.47 seconds.
Collector HEAD stayed `4ce85d714890b87f1a57ae89d4942660e41c0483`, with a clean
working tree before and after the successful audit. The local full suite,
collected before the four offline-import cases were added, passed **3,920 tests,
three existing skips** in 336.24 seconds; all four additional cases passed in
the final focused run (57 passed, one existing skip). Both guardrail suites
passed (27 and 34 tests). No return trial, reserved price access, portfolio
replay, power estimate or collector deployment occurred.
