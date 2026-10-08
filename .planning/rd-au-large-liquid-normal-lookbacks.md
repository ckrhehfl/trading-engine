# Research Direction Task AU — bound normal-observation lookbacks before classification

## Discuss and scope — 2026-10-08

The operator continued after Task AT. Its date mapping is complete, but a
present date does not establish a normal observation. Determine the actual
preceding non-frozen observation dates before acquiring classification anchors,
so the latter work covers the required history rather than an arbitrary
three-month interval or every company's entire history. Tasks AG/AN/AQ and
CLAUDE.md retain their requirements; this does not certify an eligible universe.

Preserve all **944 issue-formation rows / 140 codes / 14 formations**, including
preferred-share controls and explicitly nonordinary-security records. Reuse
the immutable AP/AR/AS/AT evidence; do not filter to liquidity passes. The
one-off audit is a data-quality and lookback diagnostic, not a discovery return
trial. It computes no activity ratio, median turnover, rank, portfolio, return,
dispersion, power estimate or strategy selection. The v1 runner is unchanged.

## Fixed access boundary

Before DB access, preserve the exact script and synthetic tests, input hashes,
repository dependency commit, output location, allowed SQL columns and fixed
per-code bounds. Use a new private evidence directory outside the checkout.
No credential, exchange request, collector change or reserved DB is needed.

Use the existing read-only helper, `query_only` and a read transaction. The
first data query checks `scan_panel` and must match `20190102..20260918` before
any candidate query. Read progress metadata for each of the fixed codes and
require agreement with AT. For bar-state inspection, read only date, OHLC and
turnover between **20190102 and that code's latest fixed formation**, inclusive.
The latter is never later than **20251201**. No volume, later prices or current
identity tables are requested. Match every returned date against the bounded
AT date set; a changed source is an input discrepancy, not a silent refresh.

Unlike AT, this diagnostic necessarily reads price/turnover fields internally
to validate them and distinguish frozen bars. Persist states, dates and a
fingerprint of consumed values, not the values themselves. That fingerprint
does not identify the whole database or certify its price basis. Read one code
at a time, with a 384 MiB address-space cap and the existing 2 GiB free-space
floor. Do not copy the database to the local worktree.

## Observation contract

Use v1's finite, strictly positive OHLC and finite, nonnegative turnover
requirements. A valid equal-OHLC, zero-turnover bar is frozen. Equal OHLC with
positive turnover is limit-locked and counts as non-frozen; no executable-fill
claim follows. A non-flat zero-turnover bar also counts as non-frozen under
v1's rule and is reported separately, not silently reclassified.

V1's `NULL` turnover-to-zero substitution is not evidence of a real zero.
Here, missing, malformed, non-finite or invalid values remain unresolved.
Keep their states and affected rows; never use SQL numeric casts to turn bad
text into zero. An unknown observation encountered in the required interval
prevents claiming that its 60-observation baseline is resolved.

For each formation, identify the latest **60 valid non-frozen observations
strictly before formation**, in chronological order in the result. Report
formation state separately, skipped frozen sessions, unresolved records and
missing dates in the required interval. Fewer than 60 is a descriptive shortage
in the stored history, not proof of a corporate identity or permission to use
SPAC/predecessor observations. Never extend before the spent-window boundary.

These are **candidate lookback dates before historical classification**. Even
60 observed non-frozen bars do not satisfy AN until their operating-company
period is verified. Preserve the needed interval and date sets for that next
join, with classification and normal-baseline certification still false.

## Verification and next evidence boundary

Synthetic tests cover reserved-panel refusal before values, source-date or
progress drift, malformed/NULL values, frozen versus locked bars, formation
exclusion, missing controls and alphanumeric codes. Verify that removing a
guard produces a failing test. Independently reconstruct row lookbacks from
saved state metadata without a second database read. This verifies derivation,
not the truth of unretained raw inputs.

For classification, reuse positive dated KIND ST observations and KRX common-
share/ISIN fields. The intended next evidence workload is an official identity
anchor available before each code's first required observation plus relevant
subsequent classification/listing changes, reconciled to the fixed formation
snapshots. A contemporary name or a missing SPAC-list entry cannot supply it.
Inspect public source contracts and version/section selection before acquiring
anchors. In particular, do not open reserved pre-2019 price/volume tables merely
because an earlier corporate filing also contains useful identity metadata.

Report actual counts and unresolved inputs before proceeding to a separately
registered sizing runner. CodeRabbit review, required CI, merge and isolated
GCP research-checkout deployment complete publication of this work; existing
collectors and schedules stay unchanged.

## Executed result — 2026-10-08

The bounded read completed at **15:52:06 KST** against the isolated research
checkout at `e981f8735e3a9b0373d5f24e54a0c975ffdbae77`. Scope and four executable
artifacts were persisted before database access. The exact spent-panel guard,
AT progress/date comparisons and all 12 synthetic tests passed. The scan
returned **187,858** code/date observations through each code's last formation;
later observations in AT's date inventory were not read for values.

| population | issue-formation rows | complete observed non-frozen 60 | insufficient/unresolved |
|---|---:|---:|---:|
| original capitalization-pass population | 944 | 909 | 35 |
| AS positive ST/common-label subset | 917 | 909 | 8 |
| preferred-share controls | 18 | 0 | 18 |
| explicitly typed investment-company/foreign-DR records | 9 | 0 | 9 |

These are observation candidates before historical identity certification,
not eligible stocks or trading signals. All 140 codes, 14 formations and
original AS metadata remain in the output. The 27 absent control/explicit-type
rows remain unknown in this panel; they were not silently removed.

Across all consumed observations, **187,637** were non-flat/non-frozen,
**214** were equal-OHLC/zero-turnover frozen, and **7** were equal-OHLC/positive-
turnover locked. There were **zero invalid/NULL/non-finite observations** and
**zero non-flat zero-turnover observations**. No locked observation fell in
the selected preceding-60 candidates. These flags describe stored bar patterns;
they do not prove legal trading-halt status or executable fills.

The 917 ST-positive formation bars split into **916 non-frozen and one frozen**:
`010620` at `20251201`. The frozen formation remains separately flagged even
though 60 preceding non-frozen candidates can be found. It is not an entry
signal. No activity-ratio, liquidity re-screen, return or portfolio was computed.

**Samsung Electronics `005930` and SK Hynix `000660` each have 14/14 complete
preceding non-frozen candidate windows and 14/14 non-frozen formation bars**,
with no missing/invalid/frozen observations inside those windows. This closes
their bar-state/date mapping, not historical classification or price basis.

### Nine windows require more than 60 market sessions

Skipping frozen observations extends these windows. The 90 skipped references
are included in the consumed-state inventory above, not additional DB reads.

| code | formation | first preceding candidate | skipped frozen observations |
|---|---|---|---:|
| `019170` | 20201013 | 20200710 | 2 |
| `011790` | 20210415 | 20201230 | 11 |
| `035720` | 20210415 | 20210113 | 3 |
| `383220` | 20220422 | 20220118 | 3 |
| `009830` | 20230427 | 20221227 | 23 |
| `086520` | 20240510 | 20240124 | 11 |
| `012450` | 20241115 | 20240719 | 18 |
| `010620` | 20251201 | 20250828 | 2 |
| `207940` | 20251201 | 20250806 | 17 |

A later post-conversion identity join may still require a different baseline.
None of these dates grants permission to use pre-conversion SPAC observations.

### Short histories: search bounds are not classification-acquisition bounds

The same eight AS issue-formations remain short, with candidate counts
**33, 20, 47, 45, 18, 56, 2 and 5**, respectively, in AS order. The algorithm
searches to the spent-panel start when it cannot find 60; its
`required_classification_range` then retains that conservative **search**
bound. It is not a claim that the current issue needs classification before
its verified listing. AU's eight short search spans contain **6,302** missing
issue-formation-session references, whereas AS/AT's fixed preceding-60-market-
session spans contained **254**. The changed denominator creates no new
collection failure. All 6,302 precede the exact AS-verified current-issue
listing; none is an internal gap in those eight observed histories.

A separate saved-state reconciliation preserves the original result and
records a narrower **next evidence-acquisition** start at each of those eight
first observed candidate dates, which equal AS's verified listing dates.
It does not certify an operating-company start date or a first eligible date.
In particular, `0126Z0` remains an insufficient split-relisting history;
no predecessor or other code is spliced in.

This yields a bounded **136-code** classification workload: **52** codes first
need observed-history classification from `20190102`; the other **84** begin
later. Each needs an official anchor public before its first required
observation and relevant subsequent changes through its last formation.
The 18 preferred-share and nine explicitly typed rows remain in the full
944-row disposition table. This workload is an acquisition plan, not a new
selection rule or a certification of any interval.

## Independent verification and provenance

At **15:53:04 KST**, a separate verifier imported no audit code and opened no
database. It checked input/artifact hashes and private permissions, retained
all 944 identities and AS fields, and recomputed each candidate date list,
unknown/frozen partition, formation state, interval union and aggregate from
saved state flags. All matched. This verifies derivation from the captured
flags, not raw prices or the consumed-row fingerprint, whose source values
were deliberately not retained.

Local and GCP synthetic audit suites both passed **12 tests**. Removing the
spent-panel guard in memory makes its boundary test fail. The independent
verifier also passed a synthetic 944-row/140-code reconstruction and rejected
seven mutations to dates, unknowns, formation state, interval counts, source
metadata, certification and aggregates. No real DB was used in those tests.

Private evidence is stored under
`<LOCAL_RESEARCH_ROOT>/large-liquid-normal-lookbacks-20261008-v1`, with root
0700 and preserved files 0600. The one-off audit/test/wrapper/verifier are
archived there; they do not modify the production runner. Supplemental offline
derivations and their source are preserved separately from the original scope.

| evidence | SHA-256 |
|---|---|
| pinned AT date mapping | `30e17cb88531e648b9a0ec135c28cd393f2ff0a1713bc15f3f28625fd67b872b` |
| pinned AR candidates | `09a942994bf2d4eec746a5cfb2337cf0bc47fb3d4f271d21339fb97f7a1a95d6` |
| pinned AS identity/listing join | `4fd4f0d34fee86a0547270fbe60f0b1e43d905ef8cf2583150db663e09e4aeb8` |
| audit script | `a7d9c1040df9ade41db32b51217629c035a80f5ba6ddd2e78109680c76d8c0f8` |
| synthetic tests | `6c8970812c63aeebdc8458a38a753f3e67390f87145bf6c267b12d9b85de7846` |
| execution wrapper | `4bb40777c7c341a1e360ffe38cb413215e76218ea42063ca256fc648f3fb3fc4` |
| scope | `4faa83c2660fca8a3877215e7d29a3cfbbc8d02978209aa95659f5954b205272` |
| complete result | `eb95349f4a5555a45363b98ae907c6a7791e12297a3fee17fa7729a492ce1fa4` |
| consumed-row fingerprint | `29aadc5741b5b489dc732cd24a1387be53b67c92d2daadb8952aae261b462981` |
| independent verifier | `aab8d42105d57d03610ff6276beb6c696ca19438cf2f56bf9b29fa99691ab075` |
| verification receipt | `5827da7cc21c5902c78bc777ce88ec8567cf5ec24e1cdb348854cee9774aafed` |
| cohort/window breakdown | `a7347436f5d687c2c802e346216ef6499da73e96d6fd629c6e8d3a38a1c67ae8` |
| supplemental verification/breakdown invocation | `a0f80ea2859799e4c0fe66555cc258e68248a0fc2999249f545eaaf98c9900e1` |
| next-acquisition workload | `96123b6f10c8ae673515acfe9963d35de28f01a753521881d73a476bd105f882` |
| workload reconciliation source | `6e13fd9df34c7a86f479dc94055d5359017ec86d37273e5bf261afa53e79dbeb` |

The consumed fingerprint hashes typed SQLite-returned values in sorted
code/date order, not the entire database. It cannot certify price adjustment
basis. The collector checkout was clean and unchanged at `4ce85d7` before
and after the read; no collector, schedule, credential or experiment log was
changed. No reserved DB, new market-data API or return trial was accessed.

## Classification source reconnaissance and next action

This reconnaissance established request contracts, not certified company
intervals. Its representative public responses were not archived as final
evidence and must be acquired with the normal preservation/version procedure
before use in a historical join.

- [KIND listed-issue status](https://kind.krx.co.kr/corpgeneral/listedIssueStatus.do?method=loadInitPage)
  distinguishes ST domestic shares from foreign-share, SPAC and investment-
  company categories in that source's UI. Combine company-level ST with the
  exact KRX issue code/ISIN/common-share fields; do not transfer the meaning
  to another provider or infer continuous historical coverage.
- [KIND detailed disclosure search](https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain)
  supports exact-code `searchDetailsSub` requests with
  `disclosureType05=0501|0502|0503|` for regular reports. Omit
  `lastReport=T` to retain original and corrected versions. Its current UI
  restricts type-filtered date spans to at most one year; split event searches
  accordingly and reconcile them with the existing listing/merger evidence.
  No historical completeness guarantee was found for these type filters.
- Metadata searches found Q3 2018 candidates for
  [Samsung](https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20181114001445)
  and [SK Hynix](https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20181114002319),
  displayed at 16:03 and 17:08 KST on 2018-11-14, respectively. These are
  anchor candidates, not completed eligibility evidence.
- Samsung's sampled KIND table of contents links to fragments of a full
  report, not a server-returned company-overview section. The pre-2019 full
  body was not fetched. DART public search access failed during reconnaissance,
  so a section-only DART request contract remains unverified. The next small
  technical step is to establish a safe company-overview section route, or
  another official identity-only anchor, before collecting those older anchors.
  A new paid source or credential is not assumed necessary.

The **944/944 bar-state/date derivations and independent recomputation are
complete**. Next close historical domestic operating-company intervals and
their publication evidence over this bounded workload, preserving AN's
post-conversion rule. Then complete AG price-basis/corporate-action accounting
before registering the large/liquid sizing runner. Sizing/comparison runs are
still **zero**; historical classification, normal-baseline certification and
universe readiness remain false. No operator decision currently blocks the
next bounded evidence work.

Local publication checks passed **84 documentation tests with one existing
skip**, the repository guardrail scanner and whitespace check. CodeRabbit
review and required CI remain the publication gate before merge and isolated
research-checkout deployment.

