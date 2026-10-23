# Research Direction Task DD — audit required retest gaps and current timestamp recovery candidates

## 2026-10-23 — performed investigation and pre-access registration

After DC completed,the operator explicitly selected option2:investigate missing
data recovery before any return comparison. This is no observed-label waiver.
Current intake is DC normal merge ea4e304284e60a4dfeb117a9c224472edbd2dc98.
DC's full actual receipt and the choice are appended to its ledger here. The
two protected untracked operator files remain outside reads/imports/tests/scans.
No DD actual saved report,DB,typed snapshot or new coverage count has been read.

Static investigation:the existing collector fixes18names while the historical
research targets contain31distinct names. This mismatch is a possible source
of gaps,not a verified explanation for every missing minute. Its morning/
afternoon span skip does not certify all internal minute buckets. Neither
collector source,configuration,schedule nor process is changed by this audit.
CX's earlier2026-10-23snapshot found no newly stored buckets;that historical
result does not replace the fresh bounded current timestamp check in DD.

The issuing broker's current official
[date-addressable minute example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py)
was read2026-10-23. It states real-account requests return at most120rows and
past-date minute data are retained at most one year. This is an upper horizon,
not a certificate that every stock/date is available,not paper/live equivalence,
not successful recovery and not proof of historical availability. No API request,
token or credential read is in DD. No external inquiry or paid source is requested.

## Fixed sources and price-free consequential scope

Only saved DC report:
/home/minjun4897/research-evidence/intraday-dc-opening-retest-20261023-v1/report.json,
SHA2568d2f0393f92f18094e4d6c0dc7150de0db512cca58ab884dc2ea100bf60c2b44;
source specSHA2564f7e85aaea3880789171287fe78fe6be0cd316cfc49a9f854f3663b637215b60.
Preserve original257sessions/56weeks,5140targets per policy and5158union
coordinates,dated membership,metadata,source flags and unknown causes. The
earlier independent raw reconstruction remains provenance,not a DD raw replay.
Do not inspect price_change or use prices/outcomes to select repair requests.

Derive the exact required-gap plan from every original target before querying:

- Signal unknown:conservative full contiguous decision prefix [open,open+235min),
  covering all47five-minute bars available before the240minute cutoff.
- Known signal with unknown label:its unchanged [entry,exit+1min),all31buckets.
- Known signal with known label,or known-no-signal:retain source state without
  a repair query. No new event is detected or label/dispersion/mean recalculated.
- Intersect required regions with original missing/conflict/invalid_price gaps;
  union exact minutes across policies at each original stock/date. Preserve
  reasons and partial source causes. Current queries are needed only for
  required original missing minutes. Conflict/invalid price needs OHLC evidence,
  not a timestamp-presence claim,and stays separately unresolved.

Save the newly derived plan durably before its first DB query. No name/date
replacement,extra baseline,unregistered target,new input window or old-report
mutation. Unknown causes do not become prelisting,delisting,halt,supplier error
or actual zero. DD never rebuilds DC from changed data or overwrites its result.

## Bounded current timestamp capture and interpretation

Only existing source DB:
/home/minjun4897/trading-engine/python/data/var/klines.sqlite3.
Use read-only URI,query_only,trusted_schema off,one short snapshot,zero lock/
contention retries and an allowlist restricted to SELECT and klines symbol,
interval,open_time_ms,fetched_at. Parameterized exact required-coordinate minute
queries read only timestamps/retrieval over the original modeled session.
No OHLC,volume,trade value,account,other table,full scan,DB copy,hash of a mutable
DB,journal/checkpoint or data write. A SQL/file error fails the audit,never a
market gap. Cap6000queries/600rows per coordinate/3.6million rows and10seconds/
5million VMsteps per query. Preserve int/string/null types;unsupported SQLite
types fail capture. Preserve duplicate/invalid timestamp counts separately.

Archive canonical typed chunks with session,symbol,retrieved_at and exact
open_time_ms/fetched_at rows. Hash consumed stream and compressed bytes;close
the DB snapshot before analysis. Current timestamp presence is only a candidate
for later price acquisition. It does not establish valid OHLC,tradability,
repaired signal/label,additional frequency or vendor revision equivalence.
Report original required missing minutes,current-present candidates and
still-missing minutes by union,policy,coordinate,symbol,date and source reason.
Known source signals/labels and their previous missingness remain unchanged.

Classify source dates against snapshot_started_at's KSTdate minus one calendar
year(Feb29 clips to Feb28). Inside/equal is within a documented upper horizon
but unprobed,outside is not promised through this endpoint;neither proves all
sources impossible. Request-time eligibility must be checked again before any
later API probe. A possible recovery is not reported as completed recovery.
Preserve snapshot,retrieval,observation and assumed historical availability
as distinct times. No historical finality/publication certificate is created.

## Execution and completion boundary

Study intraday-retest-gap-readiness-v1;mode
fixed-retest-required-gap-timestamp-readiness-only. Exclusive output
/home/minjun4897/research-evidence/intraday-dd-retest-gap-readiness-20261023-v1.
Canonical log remains /home/minjun4897/trading-engine/runs/experiments.jsonl.
Freeze source/spec/dependency/caller/verifier/runtime closure. Durable canonical
started precedes every saved body or DB read. Fresh producer and distinct
stdlib verifier reconstruct the new plan/typed-archive math independently;
the verifier imports no producer/project math and makes no second DB query.

Named invented tests,representative full geometry,staged-only scan,final fresh
source/integrity reviews,exact-head required CI and normal whole-tree merge
precede one actual isolated invocation. Capture,producer,independent supplied-
byte verification,source/runtime/input/output rechecks,full substantive
publication encoding and durable budget success precede canonical completion
in one unchanged300CPU-second/512MiB/1200wall-second/nice10process. Only
mechanical canonical/transport joins follow. Zero automatic retries;preserve
failures/partial evidence,never reset or raise limits.

Only isolated research checkout advances;collector remains tracked clean at
4ce85d714890b87f1a57ae89d4942660e41c0483. No collector pull/restart/redeploy,
schedule/config/data/credential change,new performance family,old waiver,
strategy pass,direction closure,risk/order/paper/live,reserved/pre2019/holdout/
forward access. Report verified facts vs assumptions,timestamp candidates vs
unprobed API possibility,remaining risks and actual next feasible step.
At intake four delivery groups remain:reviewed implementation/invented checks;
exact-head CI/normal merge;actual bounded audit/independent verification;
completion/progress/next-step report. DC is complete;new return comparison is
explicitly deferred. No DD actual data or new recovery count has been read.

## Frozen invented verification before final review

Named regression:228passed,1existing skip,46subtests in23.84s,using only
test_intraday_retest_gap_readiness,test_dd_retest_gap_publication,
test_dd_retest_gap_independent,test_research_budget,test_experiment_log and
test_planning_index. Producer51tests,caller59tests and independent24tests are
included. Operator untracked preflight files were not read or collected.

Full geometry profile v1 passed with257sessions/56weeks/5140targets per policy,
5158union coordinates,20,971,459invented source bytes,2572actual invented
SQLite queries and1,002,211captured timestamp rows. It exercised the unchanged
capture literal,producer,independent reconstruction,input/output rechecks and
complete substantive publication preparation before durable budget success
and canonical completion. Absolute CPU75.385839378s,wall delta82.415004501s,
peakRSS336040KiB,finalAS214757376bytes,nice10,violations empty. No budget reset
or increased limit. All13profile source hashes remain unchanged afterwards.

Private reviewed executable closure:callerSHA256
87ecc88cfc591c37675b72f74100d8ae13d5130944dc67da288ce1b7e5cd8738;
caller-testsSHA256c9b7903e428eae44761803ce3616837d07ec5cb9c64cf56ac09a33e3a084a105;
independentSHA2567fd8c0408f088212c9642d8119555bc5342a944006501fca66c4cc3282fd91b4;
independent-testsSHA25681e2f0e4ac04f4d100176b14dfe925f3d3130f80785254ca03b1033cc929d6b6;
profileSHA256501c3993761ea3172f81077b76e5efdaeaac811ff954bdb24eb8c5ab2b8b9a8c.
Profile receiptSHA256
1e4fa3a2544e4cfca17190e0cadc5a97a49bd1e8a243d7ee33f350210dca9d93.
These private artifacts stay outside Git and contain invented evidence only.
Final fresh reviews,exact-head CI/normal merge and actual audit remain pending.

## 2026-10-24 — completed DD,recorded in the following DE work unit

DD completed through normal PR#347 merge,exact-head eight successful CI runs,
fresh source/integrity PASS reviews and independently verified isolated audit.
Public development receipt:
https://github.com/ckrhehfl/trading-engine/pull/347#issuecomment-6368754167 .
Full private operational receipt is retained in ignored var/dd-actual-completion.md;
automatic approval review refused its detailed public transmission,so private
GCP paths/run identifiers/new artifact hashes are not duplicated here.

Original257dates/56weeks/5140targets per policy/5158union targets survived.
2635required stock/date queries captured45239existing timestamp rows and found
zero candidates in591730original required missing minutes. The2known-signal/
unknown-label coordinates still lack4minute buckets;2633unknown-signal union
coordinates retain591726missing buckets. D+1/D+2 signals remain525/522,known
labels523/520,unknown labels2each. No return comparison or new signal followed.
As-of2026-10-24,2294required coordinates are within the documented maximum
one-year horizon but unprobed;341are outside this endpoint's promise. No API,
credential or OHLC was read by DD. All4DD delivery groups completed;0market
minutes recovered. Remaining unknowns and source qualifications are unchanged.
The operator's new DE request authorizes recovery of the2label windows and a
conditional same-signal gross/net comparison;it is not an old waiver transfer.
