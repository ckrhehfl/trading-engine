# Research Direction Task AO — focus the next study on large, liquid common stocks

Date: 2026-10-07. The operator selected option 1, large and highly liquid
common stocks, after questioning why research had become dominated by SPACs
and delisted names. Samsung Electronics and SK Hynix illustrate the intended
kind of holding; they are not a retrospectively fixed list of winners.

## Scope decision and what it changes

The next activity study targets a dated large/liquid common-stock universe.
It is a new scope, not a corrected result for the old full-market pilot. Keep
the activity hypothesis as the starting question: does selecting unusually
active stocks add value relative to a suitable control in that same universe?
Neither a preferred company name nor size/liquidity alone establishes an edge.

Task AN's proposed expansion to classify the entire old pilot pool is no longer
the default next task. Its nine dated snapshots and Task AM's 245 lot intervals
remain evidence about v1. Preserve the old code, specifications and results;
do not rewrite them as large-cap research or declare that direction disproved.

The narrowed universe must use size and liquidity information available at each
historical selection time. Today's constituents, today's shares outstanding
multiplied by historical prices, and a current name filter backdated to 2019
are not substitutes. High traded value by itself does not establish large size.
No KOSPI-only restriction, particular index membership, numeric cutoff or
fixed ticker list was selected by the operator's answer.

Task AG's input and accounting obligations still apply to the new scope. A
complete dated selection source must establish the new candidate pool before
activity selection; missing records that could change eligibility or ranks
cannot silently disappear. Every admitted candidate and required lookback must
have verified common-stock identity. Every actual holding interval, including
later successor lots and subsequently delisted names, needs correct accounting.
The work may narrow only where evidence establishes non-membership under the
new rule, never because a name later failed or is difficult to investigate.

Task AN's approved post-conversion 60-observation rule remains applicable to
any converted SPAC that qualifies. A large/liquid screen is not proof that
there will be no corporate actions, suspensions or historical type changes.

## Source feasibility inspected in this task

Repository source inspection at base `795a4e9` found:

| input | existing representation | implication |
|---|---|---|
| daily price and activity | `scan_bars` stores code, date, OHLC, volume and turnover | supports the existing activity calculation, not historical market capitalization |
| identity snapshots | `krx_universe` / `krx_delisted` store dated code, market, name and ISIN; the former also stores group code | snapshots do not provide past size or automatically certify past classifications |
| size | no market-capitalization or shares-outstanding column in those three schemas | a separate dated source is needed for the proposed size screen |

This is a code/schema inspection, not a new scan of GCP databases or a claim
that no external archive exists. No price values or candidate ranks were read.

Official public documentation inspected on 2026-10-07 provides concrete leads:

- KRX's [service catalogue][catalogue] lists KOSPI and KOSDAQ daily trading
  information and stock basic information from 2010-01-04. This covers the
  declared post-2019 discovery era in the advertised date range. It does not
  establish complete records, field semantics, historical revisions or account
  access. The web reader did not populate the service pages' output-field
  tables, so no market-cap or classification response field is certified here.
- KRX's [access guide][access] requires an approved authentication key and
  approval for each requested API service. KIS credentials are separate. The
  four relevant service pages are [KOSPI trading][kospi-trade], [KOSDAQ
  trading][kosdaq-trade], [KOSPI basic][kospi-basic] and [KOSDAQ basic][kosdaq-basic].
- The [FSC stock-price service][fsc] is a possible alternative, not an acquired
  substitute. Its current page leaves the historical coverage field blank and
  explicitly describes publication after 13:00 on the following business day,
  despite a generic real-time label elsewhere on that page. Its availability
  must therefore be checked against the proposed formation/fill timing. It
  cannot silently replace the 2019 coverage or authorize public redistribution.

A direct WSL request for the public KOSPI documentation returned HTTP 403;
the remaining two planned document requests were not sent. No API data call,
successful raw-document archive, or service authorization is claimed from that
attempt. Public catalogue/access facts above were read with the web tool.

The operator reported an existing local `.env` and GCP environment setting.
Name-only checks found no KRX-named assignment in the primary local `.env`,
the existing collector `.env`, or the collector user's login environment.
An exact-name local check found `KIS_APP_KEY` and `KIS_APP_SECRET`, but none of
the checked KRX/generic API aliases. This does not prove a KRX key was never
issued or is absent elsewhere. Only variable names were emitted; no credential
was used for authentication, copied into this worktree, or changed. The
operator has been asked to identify the key type or its variable name/location,
without posting its value. Task AK's consumed KIS exception is not reused.

## Next bounded deliverables and completion conditions

1. **Establish source access and meaning.** Resolve the existing key's identity
   and applicable service permissions. Before requesting data, write a bounded
   probe for named post-2019 dates, required fields, controls and request limits.
   Verify instrument codes, units, observation dates, publication timing and
   completeness from official documentation and actual responses. Do not test
   the reserved pre-2019 period, shorten the discovery era, or introduce a paid
   service as an implicit fallback.
2. **Fix a reproducible universe rule before inspecting its outcome.** Specify
   the size measure and cutoff, trailing liquidity measure and cutoff,
   reselection cadence, decision-time lag, tie handling, minimum observations
   and missing-data policy. Freeze the markets and security classes explicitly.
   Size and liquidity must be distinct conditions. Fix the rule before reading
   ranks, candidate lists or returns; Samsung/SK Hynix are examples, not names
   that must be made to pass by changing thresholds.
3. **Produce the dated candidate and coverage audit.** Report the denominator
   from the authoritative source, coverage of inputs and required histories,
   and unresolved records that could affect selection. Map candidates to the
   completed price panel without treating fetch completion as eligibility.
   Apply the full type/lookback contract within the new pool, then audit
   corporate actions for all holdings a replay would create. Unknown inputs
   stop the replay; missing data is not permission to substitute a smaller pool.
4. **Integrate and register the new sizing run.** Reuse tested primitives where
   appropriate, validate cash/slot/share conservation and event ordering, and
   commit a new specification and CodeRabbit-reviewed runner before prices run.
   Explicitly carry forward or justify changes to v1's reference geometry;
   none of its parameter values is evidence of an optimal strategy.
5. **Run sizing, then make the comparison decision.** Log the new trial and
   apply the existing detectable-effect/cost-floor stop. Narrowing the universe
   does not establish that this gate will pass. The old stop remains for v1;
   no descriptive comparison exception, confirmation access or promotion is
   implied. If permitted by the gate, preregister the comparison in the same
   dated large/liquid universe before executing that separate experiment.

The immediate next output is a source-access/field-verification result, followed
by a concrete universe specification. Full-market SPAC collection is not the
default work while waiting for that input. No new selector, unused abstraction,
price acquisition or strategy replay was added merely to make progress visible.

## Progress reporting

Always distinguish the current engineering/research work package from strategy
validation. Include what finished, the current stage with an observed count
and denominator when one exists, the next action and any required decision.
Do not report task completion or test pass counts as strategy completion.

At this record: the scope decision and source-feasibility assessment are
complete, while actual new-source data access is unverified. New dated-universe
samples acquired: zero; large/liquid portfolio or comparison runs: zero.
The five execution deliverables above have not cleared their completion gates.
This is a researched scope/feasibility record, not a strategy specification
ready to execute. No collector, deployment, schedule or secret was changed.

## Credential clarification — 2026-10-07

The operator confirmed that the previously reported credential was the KIS
key. That resolves the ambiguity; it does not establish KRX API access. For
the KRX route assessed here, the remaining operator action is to sign in at
KRX OpenAPI and obtain an approved key plus approvals for the four named
trading/basic-information services. No account was created or application
submitted by this task. Do not post a key value in chat or put it in a PR;
confirm approval status first and arrange its use in the research environment
without changing collector credentials. An unavailable source must be reported
as a data-access blocker, not filled with current constituents or turnover-only
size proxies.

## Verification before publication

The WSL planning-index suite passed 30 tests with one existing skip. A separate
in-memory SQLite inspection executed the three schema declarations extracted
from repository source and confirmed the documented columns without opening a
research database. The repository scanner and diff whitespace check passed.
Independent read-only review found no actionable issue in the five changed
documents, checking scope, evidence limits, preserved research gates and honest
progress reporting. That review did not independently fetch the external
sources or inspect credential files. No return experiment was part of these
checks; full CI and CodeRabbit remain the PR's merge requirements.

[catalogue]: https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
[access]: https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp
[kospi-trade]: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC
[kosdaq-trade]: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=hZjGpkllgCBCWqeTsYFj
[kospi-basic]: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=PiwgMdTwmsenXhmqqxuj
[kosdaq-basic]: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=CifLHplnUFMgpHIMMPXs
[fsc]: https://www.data.go.kr/data/15094808/openapi.do

## Numeric universe boundary — operator decision 2026-10-07

Before inspecting size/liquidity ranks, candidate counts or returns for this
new universe, the operator selected option 1:

- **Market capitalization at least KRW 5 trillion** at the dated selection
  reference point, using that date's capitalization rather than today's shares.
- **Median daily traded value at least KRW 10 billion (100억 원)** over the
  preceding **60 trading days**.
- KOSPI and KOSDAQ domestic operating-company common stocks at the relevant
  historical time. Neither today's winners nor an index's current constituents
  define the past universe.

The alternative KRW 1 trillion boundary was not selected and is not a parallel
trial. No candidate count or period-wide inclusion of Samsung Electronics or
SK Hynix has been verified. Do not tune the boundary to force those examples
to pass or to increase the number of observations after seeing the outcome.

This fixes the operator's scope choice, not every implementation detail in
deliverable 2. A reproducible specification must still pin the selection clock,
liquidity window endpoints, re-selection cadence, incomplete histories,
missing observations and type-evidence policy before examining the outcome.
The liquidity window's trading-day count is distinct from Task AN's normal
post-conversion observation requirement; neither silently replaces the other.

Task AP now establishes access, sampled capitalization consistency and the
current documented source timing. It also establishes that the SPAC section
label alone cannot certify an operating company. Next finish the bounded
acquisition/coverage specification and audit the new candidate scope, rather
than expand classification of the old full-market pilot. Deliverables 1 and 2
remain in progress; no new universe selection or strategy replay has run.

### Earlier status records superseded

The initial scope paragraph's unselected numeric cutoff describes that earlier
decision only. The operator decision above supersedes it with the stated
capitalization and liquidity boundaries; the original record is preserved.

Likewise, the earlier source-feasibility and credential-clarification sections
describe access before individual service approval. Task AP's section
"Individual service approval and successful GCP access — 2026-10-07" records
the operator's corrected approval report and **17 HTTP 200, schema-valid
responses**, including all four services, at **17:31:35–17:32:40 KST**.
Those observed results supersede the earlier unverified-access status.
The completed fixed access matrix does not certify full historical coverage.
