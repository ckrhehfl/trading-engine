# Research Direction Task AT — map the fixed candidates to stored session dates

## Discuss and bounded plan — 2026-10-08

After Task AS, the operator asked to continue. This task checks whether the
existing spent price panel contains the dates needed by the fixed candidate
population. It does not inspect price values, calculate activity, choose a
universe or run returns. CLAUDE.md and Tasks AG/AN/AO/AQ retain their gates.

The input population remains all **944 issue-formation rows / 140 codes**,
including the **926 common-label rows**, 18 preferred-share controls, eight
short histories and nine explicitly nonordinary-security observations. Do not
reduce it to the 875 rows passing AS's diagnostic screens. Join by exact code
and formation, carrying AS's ISIN as source metadata; a code/date match in the
price store does not independently certify that store's historical ISIN.

Use the pinned AP calendar, immutable AR candidates and AS source join. The
one-off auditor and its synthetic tests are retained with the private evidence;
this does not add a scheduled job or a new research framework. Persist the
exact scripts, input hashes, source commit, SQL allowlist, actual DB path and
scope before opening the database. Preserve a failed attempt rather than
overwriting its output. No API call or credential access is needed.

## Read boundary and interpretation

Open the existing spent scan through `research.activity_portfolio.readonly`,
with a read transaction. The first data query must be:

```sql
SELECT start,end FROM scan_panel WHERE id=1;
```

Require exactly `20190102..20260918`, refusing any other panel before querying
candidate data. For each of the fixed 140 codes, the only subsequent queries
read `scan_progress`'s code, status, first/last date, bar count and fetch time,
and `scan_bars.bsop_date` bounded inclusively by that spent interval. Do not
select OHLC, volume, turnover, frozen counts, current identity tables or any
reserved database. There is no `status='done'` filter that can hide a code.

Compare each candidate with the exact preceding `C[f-60:f]` sessions and its
formation date. Retain discrepancies with AR's observed/missing date sets;
the two sources' date coverage need not agree. Record progress consistency
against the returned dates, including absent/non-done rows. Missing dates are
unknown, never zero, proof of non-listing, a halt or a reason to drop a name.
Only AS's eight verified listing notices can explain their already bounded
pre-listing gaps; do not extend that explanation to other gaps by inference.

Also preserve all stored spent dates per code and compare the full calendar
envelope before every formation. This lets the next step reconstruct longer
lookbacks without replacing AN's **60 normal observations** with 60 market
sessions. Presence alone establishes neither a normal/tradeable observation
nor a verified operating-company interval. Future stored dates cannot certify
eligibility at formation. No predecessor history is substituted.

## Verification and completion conditions

Use synthetic databases to verify rejection of the reserved panel before any
candidate query, refusal of value-column reads, preservation of absent/partial
progress, exact window endpoints and alphanumeric codes. Independently
recompute the saved result from its date-only evidence and pinned metadata;
do not rerun a return experiment or copy the source database locally.

Report complete and unresolved date coverage with the original denominator,
keeping data presence separate from classification, basis and corporate-action
accounting. Historical certification and universe readiness stay false. Any
needed repair must first identify its cause and bounded acquisition scope;
this audit does not silently repair or overwrite the collector's database.

The collector checkout and schedules remain unchanged. After evidence and
documentation checks, complete CodeRabbit review, CI and merge, then update
only the isolated GCP research checkout. The next research gate is the dated
classification/normal-observation and accounting input audit, followed by a
separately registered sizing run. Large/liquid return runs remain zero here.

## Executed date inventory and result

The GCP audit completed at **12:48:05 KST on 2026-10-08**, using repository
dependencies at clean commit `02162d596d69d2baed82495924db0bef08c26f25` and the
preserved one-off script hashes below. Its synthetic tests also ran on GCP:
**9 passed in 1.040 seconds**. No API request or new price-value read occurred.

The source panel passed the exact spent-window guard. The result preserves
**239,764 distinct code/date observations**, the **1,895-session** spent
calendar and every original candidate. Of the 140 queried codes, **136 have
`done` progress records**, with first date, last date and bar count all matching
the bounded date rows; **four have neither progress nor stored dates**. There
are no duplicate or non-calendar dates among the returned observations.

| diagnostic population | issue-formation rows | codes | formation present | preceding 60 complete | same date partition as AR |
|---|---:|---:|---:|---:|---:|
| original capitalization-pass population | 944 | 140 | 917 | 909 | 917 |
| common-label subset | 926 | 138 | 917 | 909 | 917 |
| common label plus positive KIND ST observation | 917 | 136 | 917 | 909 | 917 |
| preferred-share controls | 18 | 2 | 0 | 0 | 0 |
| explicitly typed investment-company/foreign-DR records | 9 | 2 | 0 | 0 | 0 |

The common-label row contains the following ST-positive and explicitly typed
rows; it must not be added to them as an independent population. These remain
source-label partitions, not certified historical operating-company classes.

All **917 ST-positive rows** agree exactly with AR's date partitions. Their
only preceding-session gaps are the **254 values in AS's eight verified
short-listing histories**. These remain insufficient histories, not completed
windows. No new missing date was found in that subset relative to AR.

The four entirely absent codes account for **27 rows**:

| code | retained source classification | rows | AR-observed dates absent from panel |
|---|---|---:|---:|
| `005387` | preferred share | 4 | 240 |
| `005935` | preferred share | 14 | 840 |
| `088980` | infrastructure investment company | 8 | 480 |
| `950160` | foreign depositary receipt | 1 | 60 |

Thus **1,620** AR-observed issue-session values are absent from the panel;
all belong to those 27 retained records. There are **zero** reverse conflicts
where AR is missing but the panel has a date. Total preceding-window missing
values are **1,874 = 1,620 + 254**. These are issue-formation-session counts,
not distinct missing market dates. The absence of the four codes is not used
to classify them, infer a halt or declare a collection failure: their source
labels were already retained independently by AS. This audit made no repair
request and did not change the source population.

The full spent-calendar prefix before formation is complete for **798 rows**.
The other **119 ST-positive rows** have dates missing before the first stored
date, with **zero internal prefix gaps**. This shape alone does not verify a
listing/conversion boundary for those 119 rows. Their preceding 60-session
windows are already counted above; extending the activity lookback requires
the separate normal-observation and classification checks. The remaining 27
prefixes have no stored dates at all and stay unknown as date coverage.

For the operator's examples, **Samsung Electronics `005930` and SK Hynix
`000660` each have 14/14 formation dates, 14/14 complete preceding windows
and 14/14 complete spent-calendar prefixes**, agreeing with AR throughout.
This confirms available dates for their intended research role, not an
activity signal, executable price, historical eligibility or return advantage.

## Reproducibility and independent verification

Private evidence is preserved under
`<LOCAL_RESEARCH_ROOT>/large-liquid-panel-coverage-20261008-v1`, with directory
mode 0700 and files 0600. The scope records the actual source DB path, fixed
input hashes, allowed columns, population, source commit and script hashes
before the database is opened. The database itself was not copied or hashed;
the result hash identifies date-only evidence, not the underlying price data.

| evidence | SHA-256 |
|---|---|
| AP calendar input | `ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882` |
| AR candidate input | `09a942994bf2d4eec746a5cfb2337cf0bc47fb3d4f271d21339fb97f7a1a95d6` |
| AS source-join input | `4fd4f0d34fee86a0547270fbe60f0b1e43d905ef8cf2583150db663e09e4aeb8` |
| audit script | `b338bfe31f1af1edf45f40acc691854ea31bb1506b48a8ffe6f8567e09ed6129` |
| synthetic tests | `6cf8a66cc212a5f39a5949faa3cd5098470518b00b34a179efdf8a914e434c8c` |
| execution wrapper | `0d503c2c84dab810352607c435a93e77bb2eab4f8a382db9cb7b52bbc65cb256` |
| scope | `4292e3f8b9d0990ca2067e4d26abc063b6e0fa770c9079d338bb9f8cf277ce68` |
| complete date-only result | `30e17cb88531e648b9a0ec135c28cd393f2ff0a1713bc15f3f28625fd67b872b` |
| independent verifier | `94f03f5e2cb0e6f2d6a0438dac6151302044318aa6ea620e544f6500f097610f` |
| independent verification receipt | `343ea7e8eebeed690a83ef667f33987e4713445dd8f3f5c95d70e118c6ca7c21` |
| cohort/date diagnostic breakdown | `0590e1b7ce4fb06e0c10e3f71e9f78b06a97d87985a65dcc8c95f99124d470cc` |
| supplemental offline verification/breakdown invocation | `3de72abc3e852e881dcffeef34defc31753b545f271484855a2cc45299baf4e4` |

The separate verifier completed at **12:48:47 KST**, imported no audit code
and opened no DB. It verified
input/script hashes and permissions, then recomputed each saved date set,
progress consistency, preceding-window partition, full prefix and aggregate.
All matched. This checks derivation from captured metadata; it is not a second
independent database acquisition or verification of values that were never read.

Local synthetic tests also passed all nine cases. In-memory mutation checks
made the reserved-panel test fail when its guard was removed, and six value-
column tests fail when the authorizer permitted those columns. Restoring the
original code passed. The synthetic read-only test compared DB bytes before
and after; the actual audit used SQLite read-only mode, `query_only` and a
read transaction, without making a full DB copy for such a comparison.

## Completion and next action

The **944/944 date mapping** and independent recomputation are complete.
No new date gap was found within the 917 ST-positive observations beyond AS's
known eight short histories. The broader required-lookback gate remains open:
dates alone cannot establish normal bars or continuous historical identity.

Next resolve dated domestic operating-company intervals and their public
availability for the bounded pool and relevant lookbacks, then verify normal
observations and AG's price-basis/corporate-action inputs before registering
the new sizing runner. Retain the 27 controls/explicit types and the eight
insufficient histories with their evidence; do not infer eligibility from
their presence or absence in this DB. No new operator decision is needed for
that bounded evidence work. Large/liquid sizing and comparison runs remain
**zero**, and all historical certification/readiness flags remain false.

The collector checkout was clean and unchanged at `4ce85d7` before and after
execution. No collector, schedule, credential, experiment log or trading mode
was modified. The publication diff consists only of this record, its index
and document-count updates, and the living handoff.

Local documentation checks passed **84 tests with one existing skip**. The
repository guardrail scanner and whitespace check passed. Independent review
found no material discrepancy in the result arithmetic, denominators, source
boundaries or documentation. CodeRabbit and required CI remain the merge gate.
