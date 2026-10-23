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
