# Research Direction Task CX — audit stored activity fields and recoverable minute coverage

## 2026-10-23 — performed investigation and pre-access registration

The operator requested existing DB volume/value availability, minute-gap
recoverability and one feasible next hypothesis, through independent checks,
reviews, CI, normal merge and isolated GCP research reflection. Static source
investigation has begun; no CX actual DB, schema or saved market body has been
read at this entry. The preceding CW completion is appended to its original
ledger in this work unit; its original reports remain immutable.

### Confirmed source facts and remaining distinctions

`data.kis_intraday.parse_row` stores `cntg_vol` as exact Decimal volume and
intentionally leaves `quote_volume` absent: the source `acml_tr_pbmn` is a
cumulative amount, not per-minute turnover. `data.store` stores numeric text,
with nullable additive `quote_volume`; schema capability is not proof of actual
content. The original CC archive selected eight OHLC/time/retrieval columns,
omitting volume and turnover. Missing archive fields do not imply missing DB
fields. The daily decoder stores actual `acml_tr_pbmn`; daily turnover cannot
substitute for per-minute turnover. Do not estimate traded value as close times
volume or infer an absent cumulative series by differencing stored prices.

The existing collector script fixes eighteen names, whereas CB has thirty-one
distinct historically selected names. This static mismatch can explain some
missingness but does not yet measure it or prove every absent date's cause.
Its span-based skip accepts a session reaching morning and afternoon; that is
not a certificate that all internal minutes are present. Do not change the
collector, schedule, skip logic or running process in this research audit.

On 2026-10-23 the issuing broker's official
[date-addressable minute sample](https://github.com/koreainvestment/open-trading-api/blob/d8c7f7936793f85dc1e9c364ba5dc66832353f8c/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py)
was checked against its public history (last path change2025-09-17): date/time
parameters, up to120rows/request on the real-account interface and at most
one year retained. Its official
[column mapping](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/chk_inquire_time_dailychartprice.py)
names `cntg_vol` trade volume and `acml_tr_pbmn` cumulative trade value.
These facts describe source fields/retention, not a guarantee of coverage for
every code/date, paper/live equivalence, historical API publication time or
successful current recovery. A gap older than the documented upper horizon
cannot be promised recoverable through this endpoint. A gap inside it is only
a possible later bounded acquisition, not recovered data. No API or credential
read is authorized by this audit; no external inquiry is a blocker.

### Fixed actual input scope

Use only the original CB dated-target union from its pinned report:
`/home/minjun4897/research-evidence/intraday-cb-universe-20261020-v1/report.json`,
SHA256 `f1ac24fed9ee3a54dfcac0d25ef1ff339be0a872d8915d849e4369e4d5c5eb91`.
Retain both D+1/D+2 policies, all257observed dates and5140targets/policy;
the union has5158stock/date coordinates. These policies delay membership
information, not a holding day or an independent replication.

Read the saved original CC archive once, solely for bounded timestamp coverage:
`/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz`,
compressed SHA256 `4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de`,
decoded-stream SHA256 `d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da`.
Do not replace or amend old archive/report identities with newly retrieved rows.

The only DB is the existing record
`/home/minjun4897/trading-engine/python/data/var/klines.sqlite3`,
read-only URI, query-only and allowlisted SQLite access. Inspect only `klines`
schema and parameterized exact-symbol/date ranges inside2025-09-04..2026-09-23.
Read minute timestamp, volume, quote_volume and fetched_at; at the same original
coordinates read the daily volume/value/retrieval row for a separate daily
inventory. No price-return query, new baseline coordinate, other table, account,
reserved/pre2019, forward or holdout read. Preserve original SQLite value types
and exact numeric text; null, actual zero, invalid, negative and positive values
remain separate. Missing rows remain unknown, never zero activity, halt,
unlisted/delisted, API failure or provider omission without evidence.

Cap6000unique target days,12000queries,600minute rows/day and one daily row/day,
3.6million minute rows plus bounded daily rows,10seconds/5million VMsteps per
query; no unbounded scan or database copy. Short single read transaction with
readonly connection, zero contention retries, no checkpoint/journal/schema
change. Fingerprint consumed typed rows and schema, not a changing live DB file.
Keep typed private archive and per-coordinate receipt with retrieved_at distinct
from observation_date and assumed historical availability. No true finality or
historical publication claim is created by present retrieval.

Report saved/current bucket-set intersection, newly stored missing buckets and
regressions by original coordinate and policy; current presence is not valid
OHLC, tradability, a repaired old experiment or proof that API recovery succeeds.
Check full first15/30minute volume windows and the fixed preceding20reference
sessions, without substituting twenty observed sessions for twenty required
sessions or expanding the original target-coordinate scope. Baseline/window
missingness and nonpositive median stay unavailable. Special-opening handling
must be fixed before access. No ratio selection, signal outcome, return mean,
power or candidate-performance calculation is part of this readiness audit.

### One next hypothesis, fixed without new return inspection

Investigate **abnormally active opening-range breakout**: retain the original
unfiltered fifteen-minute OR first signal, three-minute entry wait and
entry+30minute cap-open exit; add only first15minute volume at least twice the
median of the preceding20required sessions' corresponding opening windows.
Require causal complete positive-volume baseline availability, with no zero
fill or missing-day replacement. No CV price-impulse conjunct, second range,
threshold sweep, target replacement or frequency-forcing. Both membership
policies and33bp assumed cost remain. Current activity availability is audited
before subsequent event/frequency/own-label calibration; ordinary comparison
prerequisites and operator decisions still apply. This is a testable hypothesis,
not a profitable candidate or permission to promote. Final exact timing and
usable-window evidence are recorded before any later family run.

### Execution contract and completion definition

Opening-window convention fixed before access: both the current session and
all twenty required prior reference sessions must share the same modeled
opening HH:MM. Use10:00 on2025-11-13/2026-01-02 and09:00 otherwise, retaining
the existing calendar assumptions. Any mismatch is baseline unavailable; do
not compare a late opening with ordinary mid-morning flow or skip that prior
session. Actual zero minute volume remains observed zero and is permitted;
the exact twenty-window median must be positive. Existing share-unit changes
and revisions can still distort raw-share volume ratios; this audit does not
certify corporate-action adjustment or an economic activity shock.

At audit_as_of2026-10-23, classify unrecovered coordinate dates earlier than
2025-10-23 as outside the documented one-year upper horizon, and later/equal
dates as inside that upper horizon but unprobed. This is a source-retention
planning classification, not a demonstrated endpoint floor or successful
recovery. There are zero API attempts in this task. No absence is converted
into a halt, pre-listing, delisting, provider failure or actual zero.

The JSON specification and private caller fix pins, resource ceilings and
exclusive output before access. Reviewed committed source, full invented
geometry profile, fresh separate source/integrity reviews, required exact-head
CI, normal merge and isolated GCP reflection precede one actual invocation.
Durable canonical started record precedes all actual input/schema/DB reads.
Producer, independent typed-input reconstruction, authentication and full
substantive publication preparation share one unchanged process with CPU300s,
AS512MiB,wall1200s,nice10,0automatic retries. Durably finish the budget receipt
before canonical completion; only mechanical transport finalization follows.
Retain failure/partial artifacts; no budget reset or silent rerun.

Collector checkout remains tracked clean at4ce85d714890b87f1a57ae89d4942660e41c0483;
only `/home/minjun4897/research-checkouts/activity-discovery-20261005` advances.
Protect both local operator-owned untracked files from content access, import,
testing or scanning. Use named tests and staged-only guard scans. Source tests
and readiness counts do not remove historical classification/calendar/revision,
price-basis, missingness, execution-cost or fill uncertainty. No direction
closure, conclusion insertion in CLAUDE.md, reserved access or trading change.

At intake four delivery conditions remain: reviewed bounded implementation and
invented/independent checks; exact-head CI/normal merge; authenticated isolated
actual audit with independent reconstruction; evidence-based hypothesis and
completion report. This work has not yet reduced those four. Prior D+1/D+2
price comparisons are complete; new activity-family performance has not started.

## 2026-10-23 — actual completion appended in the following CY work unit

Task CX completed2026-10-23KST: existing minute/daily activity inventory,
saved/current minute coverage and recovery planning classification, one fixed
next hypothesis, independent actual reconstruction, separate reviews, exact-head
CI, normal merge and isolated GCP research reflection. No signal selection or
new performance family was run.

## Identity and verification

- Reviewed source64efcbecdabbcc9e96e865e2b669237dfbb35ddc; normal PR#341 merge
  fe681ee0be8cad9ae5c7db6dbf8c6e02188cdc00 at2026-10-23T05:56:34Z.
  Reviewed source is the second parent and complete trees match. Local/remote
  origin-main ancestry and source/runtime closure checks passed.
- Separate source/integrity PASS, resolved timeout finding and frozen private
  hashes/profile: https://github.com/ckrhehfl/trading-engine/pull/341#issuecomment-6358111127.
  Final gate: https://github.com/ckrhehfl/trading-engine/pull/341#issuecomment-6358481917.
  All8exact-head required push/PR checks SUCCESS. Both Python runs9703passed,
  3skipped,21subtests: push2099.97s,PR2090.52s. No experiment-log creation,
  live_signals mutation or stray python/runs. No protection bypass.
- Actual run8fc70de5-8a4e-4fea-9875-4e0479289a97 completed
  2026-10-23T15:01:11.121127+09:00. Report
  `/home/minjun4897/research-evidence/intraday-cx-activity-readiness-20261023-v1/report.json`,
  SHA2561a4d41204d421685e3f1ad2b8b93cb40b57240204e1918e965c0576585d2a2e6.
  Specification SHA25669c7c35c2fcf07d3bd0ee916edce67b5cffb91acc88c2d94f01448717bd55d7a.
  Local publication SHA25654e64bfee98fec73d35be47cd6441bb6553c8020257c60c28d0839b5dae1db67.
- Canonical started/completed span5745054..5756187,11133bytes,
  SHA2567a203793bfa2f5aa857e3219584aade94c2a05e3dba77f571aaac45e4cdd7f11.
  Durable start preceded every actual input/schema/DB read. Exactly10316queries
  cover5158original union coordinates, one bounded minute and one daily query
  per coordinate. One read-only snapshot closes before saved-input analysis;
  after-database checkpoint is17.346781597s from pipeline baseline, CPU7.307793s.
  This checkpoint is not the exact snapshot duration.
- One unchanged PID1593294 covers capture, producer, stdlib independent
  reconstruction, source/input/output rechecks and substantive publication
  preparation through durable budget success before canonical completion.
  CPU208.926193775s,wall221.027830296s,peakRSS141728KiB,finalAS116973568bytes,
  nice10,CPU300s/AS512MiB/wall1200s,violations[],no reset/retry. Only mechanical
  canonical/transport finalization follows completion.
- Typed compressed archive SHA256
  bb481b84832e13cd8690bb52dcf2e0047bbfd7ce207561eec71e4956e22644e6;
  typed stream SHA256e2097fc2f2b6ac5128ec4a1f7493dde2652ca5e3bba26391e5a842fb7047dad9.
  Original CB report and compressed/decoded CC pins all matched. The frozen
  independent verifier41600d036d654e034c715435063e052b31c54b918d017d66c1ee0fa7798dfe15
  rebuilt all new readiness rows/aggregates from original supplied CB bytes,
  saved CC timestamps and archived typed query rows, without producer imports
  or another database read. It correctly leaves source/runtime authentication
  to the separately authenticated caller; no historical source truth is implied.
  Supplemental local receipt algebra and four mandatory conclusion checks
  passed; actual_new_math_recomputed:false for that supplemental script.
- Isolated GCP research checkout equals the normal merge. Collector before/after
  stayed tracked clean at4ce85d714890b87f1a57ae89d4942660e41c0483. No collector,
  schedule, process, credential, order or risk change. No API, other DB,
  pre2019/reserved/forward/holdout access. Protected local untracked originals
  remain outside content reads/imports/tests/scans.

## Verified actual activity and coverage

Window2025-09-04..2026-09-23,257observed reference dates, original5140targets
per policy,5158union stock/date coordinates. A coordinate is not a trade or
independent observation. This is the original minute-source DB, not a claim
about every other project database.

| Scope | Observed rows | Volume | Trade value |
| --- | ---: | --- | --- |
| minute |1002211|all1002211positive exact stored values|all1002211null quote_volume|
| daily, same coordinates |2674|2657positive,17actual zero|2657positive,17actual zero|

Both columns exist. There are no invalid/negative observed values in this
bounded inventory. The17daily zeros are observations, not missing values or
proof of a halt. Missing daily rows remain missing. Daily value is not minute
value; neither price-times-volume nor cumulative-value interpolation was used.
Minute volume was omitted from the old CC archive rather than absent from the
source DB. Official KIS mapping and the intentionally omitted cumulative field
remain documented in the preregistration.

Saved/current unique minute bucket sets are exactly equal:1002211intersection,
0new buckets,0lost buckets. Of2511original empty union coordinates, none became
available in the current DB. By the fixed2026-10-23planning date,330are outside
the documented one-year upper horizon and2181are inside it but unprobed.
The official broker documentation supports at most one year, not complete
coverage of every date. No API request or recovery was performed. These are
possible future acquisition coordinates, never promised recoverable data;
outside this endpoint's upper horizon does not establish impossibility from
every other source. Actual absence causes remain unknown.

First15minute volume windows are complete at2596union coordinates; first30
at2592. With all20required prior reference sessions, same opening HH:MM,
complete valid windows and positive exact median, feature-readiness is1222
union coordinates. D+1has1219ready/3921unknown;D+2has1222ready/3918unknown.
Counts retain all5140targets in each policy. These are measurable feature
contexts, not threshold-passing signals, orders, weekly frequency or profits.
Do not sum the two policies as independent cases. Baseline coordinates outside
the original queried union are unqueried/unknown, not proven absent from DB.

## Fixed next hypothesis and remaining limits

`OR15-initial15-volume-versus-prior20-same-clock-median-ge2-v1` is fixed in the
committed specification and actual report: same dated top20universe, original
OR15first completed five-minute close above initial high, add first15minute
volume>=2times previous20required same-opening-window median. Keep assumed
two-minute bar availability, original three-minute wait, entry+30minute cap-open
exit,240minute cutoff,one first signal/name/session,33bp assumed round-trip cost
and separate D+1/D+2 membership policies. No CV price-impulse conjunct, parameter
sweep, name replacement or forced15/week trading. The threshold was fixed before
this audit, not selected by performance. No ratio, signal count, label sizing or
mean was calculated in CX.

Confirmed: source-field presence/content and saved/current timestamp coverage
over the registered scope. Inferred only: the fixed18collector names versus
31historical target names can explain some gaps; this is not each gap's verified
cause or a current cron-configuration certificate. Assumed/unverified: minute
availability lag, original membership D+1/D+2 timing, complete calendar/type and
revision history, volume units across share changes, consistent future feature/
price vintage and actual costs/fills. D+2 is sensitivity, not historical proof.
Code removes DB-write/unbounded-query, float-loss, zero-fill, missing-day
substitution, scope-expansion and premature-completion risks; it cannot create
missing observations or validate investable returns. No institutional inquiry
is needed or requested.

All4registered delivery-condition groups for this request are resolved,0remain,
4reduced in this work unit. Both Dpolicies completed the actual input audit;
new activity-family performance is unrun in both. No operator decision is
pending for the completed task, and no strategy is qualified or direction closed.

Next: reuse the saved typed volume archive and original CC price archive with
explicit coordinate/retrieval/basis linkage; first measure this one rule's signal
frequency, unknowns and own-label sizing, then register the original OR15-versus-
volume-filter performance comparison if its prerequisites permit. Preserve the
full target denominator and all new unknowns; no earlier underpower/missing-label
waiver transfers. Existing sparse targets do not justify another unrelated
price-only filter or claiming that missing data must first be fully repaired.

Append this actual completion to the CX ledger in the next ordinary work unit,
rather than creating an additional CI-only documentation PR. The immutable
actual report/publication/canonical receipts and this PR comment own completion.
