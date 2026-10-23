# Research Direction Task DE — recover two retest labels and conditionally compare gross and assumed-cost returns

## 2026-10-24 — performed intake and pre-access registration

The operator explicitly requests KIS recovery of the two original known-signal/
unknown-label coordinates,verification of recovered prices,and the unchanged
retest signal's gross/net comparison if recovery succeeds,through independent
verification,review,CI,normal merge and isolated GCP reflection. Intake HEAD is
the normal PR#347 merge. DD is complete;its sanitized completion is appended
to its ledger. Full private operational evidence stays local,not in public Git.
No DE actual source body,raw archive,new outcome,API or credential was read at
intake. The two protected operator untracked files stay outside all reads,
imports,tests and scans. No external inquiry or paid service is requested.

The issuing broker's official date-addressable minute example was read again
2026-10-24:maximum120rows for a real-account request,past-minute retention at
most1year. This is an upper horizon,not proof every date is available. Existing
project collector uses the paper quotation host;DE keeps that source-equivalent
host and does not silently switch accounts/hosts after rejection. Official
example:https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py

## Fixed inputs and original events

Only the previously independently verified DC report and original CC archive
are inputs. DC reportSHA256
8d2f0393f92f18094e4d6c0dc7150de0db512cca58ab884dc2ea100bf60c2b44;
DC spec4f7e85aaea3880789171287fe78fe6be0cd316cfc49a9f854f3663b637215b60.
CC compressedSHA256
4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de;
canonical consumed stream
d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da.
Private caller freezes the already registered exact source paths,output path,
canonical log,runtime and full executable dependency closure before access.

Preserve257reference sessions/56weeks/5140targets per policy/5158union targets,
original525/522signals,523/520known labels and2unknown labels per policy.
Derive only the2union coordinates with status=signal,label.status=unknown;
save their full immutable31minute entry-to-cap plan before auth/API access.
Stream/hash the entire original CC archive for identity,retain only those two
canonical chunks,and do not recompute any signal or acquire unknown prefixes.
No current DB lookup,extra code/date,threshold,entry,exit,horizon or peer filter.
The roughly51%unknown signal population remains in every report denominator.

## Two bounded quotation requests and privacy

Check each coordinate's observation date against actual request-time KSTdate
minus1calendar year(Feb29clips toFeb28). Outside dates remain unprobed and
unpromised by this endpoint;no all-provider impossibility claim. One GET per
eligible original coordinate,ending at second59of its unchanged exit minute,
maximum2GETs
and120rows/page,no paging,retry,date replacement,host fallback or full backfill.
Only fixed quotation path inquire-time-dailychartprice/TR FHKST03010230,
marketJ,past-dataY,fake-ticksN. A failed/empty/incomplete request is recorded,
not recoded as a halt,no-trade,zero price or recovered label.

The new request authorizes necessary existing KIS market-data authentication
for these two windows only. Read existing KIS_APP_KEY/KIS_APP_SECRET from the
GCP environment,with the existing GCP .env fallback for missing fields;never
source/execute it,modify it,print/copy its values or expose other settings.
Reuse an existing valid owner-only host-matched token read-only when available;
otherwise at most1token issuance POST to the fixed broker oauth2/tokenP path.
Token stays in memory,no cache/credential-file write. HTTPS only,no redirects,
bounded body/time and no HTTP/application retries. No account/order endpoint.
Persist only market response rt_cd/msg_cd/output2 allowlisted date,time,OHLC
fields plus request/retrieval metadata. Exclude output1,msg1,headers,keys,tokens
and raw authentication/error bodies. No credential/API access occurs in tests.

## Recovery validation before comparison

Retain original event,clock,label,flags and chunk vintage unchanged. In the
31minute region validate literal dates/times,minute buckets and unique finite
positive Decimal OHLC with correct high/low bounds. Preserve offsets,identical
duplicates and conflicts distinctly. Every existing valid original OHLC bucket
in that region must match API OHLC exactly. Fill only original missing buckets;
do not overwrite old quotes,normalize a changed series into agreement,guess
missing prices or use rows outside the registered date/window. A mixed-vintage,
conflict,invalid price,missing row or endpoint mismatch leaves a diagnostic and
comparison not run. This is overlap consistency,not a full vendor finality or
price-basis certificate. Retrieval is not historical availability.

Before access,the static API review identified documented prior-date page
spillover and actual historical nonzero-second row stamps. Positively older
dates and modeled-preopen rows are excluded with explicit counts,never used;
malformed/future dates remain invalid. Querying through cap-minute second59
includes a possible cap row stamped at:11without requesting any later minute.
The original assumed cap-minute-open price,entry/exit and availability clocks
are unchanged;this is request coverage,not a later strategy exit or fill claim.

Both windows must be complete,all original effective labels known,and fresh
own-label sizing defined and MDE<=33bp under BOTH D+1/D+2 before any new mean.
Use original257dates including zero-event dates,SD,naive/session-cluster/circular
five-session2000draw seed0/bootstrap maximumSE as in DC. Preserve failures and
require a new material operator choice if a new unknown/undefined/failed sizing
condition occurs. No inherited exploratory exception or silent label exclusion.

Only if these conditions pass,one newly authorized descriptive family compares
the original observed retest signal's exact price returns with the unchanged
assumed33bp roundtrip cost. Fractions/Decimal remain exact until statistics.
Report count,gross/net mean/median,positive/cost-exceedance rates,uncertainty,
weekly frequency and the two repaired cases' contribution,under each overlapping
timing policy. A before/after recovery count is not a new-selection experiment.
No extra threshold/cost sweep,winner,matched-placebo or incremental-edge claim,
portfolio PnL,actual fills,full-universe result,direction closure or strategy pass.
Public historical timing,revision,eligibility and cost assumptions remain.

## Execution and completion gates

Study intraday-retest-recovery-v1. One exclusive private output named
intraday-de-retest-recovery-20261024-v1;existing canonical experiment log.
Durable started precedes every actual saved body/archive/auth/API read. Freeze
reviewed committed source/specification,private caller/verifier and runtime.
Fresh producer and separate stdlib verifier reconstruct new requests,recovery,
own sizing and conditional comparison from the same authenticated supplied bytes,
without project/producer math imports or a second API/DB acquisition.

Named invented tests,representative full geometry and complete substantive
publication encoding before durable successful budget and canonical completed
must pass before actual intake. Final fresh source and integrity reviews,
exact-head required CI,normal whole-tree merge and isolated research deployment
precede the single actual invocation. One unchanged300CPU/512MiB/1200wall/nice10
process includes source capture,API,producer,independent verification,rechecks,
publication preparation and budget success. Zero automatic retries/resets/
increased limits;partial/failure evidence survives. No source body replay after
completion;only mechanical completed-publication receipt/conclusion checks.

Only the isolated research checkout advances. No collector pull/restart/
redeploy/schedule/data/config change,credential-file mutation,pre2019/reserved/
holdout/forward access,paper/live/order enablement or risk change. Full private
operational receipt remains local;public PR reports sanitized development facts.
Four delivery groups:implementation/invented verification;fresh review/CI/merge;
actual bounded recovery/independent conditional comparison;report/progress/next.

## Frozen invented verification before final reviews

Final named regression:196passed,1existing skip,46subtests in19.65s. Explicit
files only:DE producer,private publication,private independent,research budget,
experiment log and planning index. Separate producer/DC/DD regression153passed;
standalone stdlib independent39tests passed. No actual body/API/key access.

Fullgeometry invented profilev2 passed with257dates/56weeks,5140targets per
policy,5158chunks/1002211rows,525/522original signals and2shared unknown labels.
It exercised the actual capture literal,full source-stream identity,two fake
quotation GETs and one fake token POST with isolated invented cache,producer,
standalone independent verification,source rechecks,full substantive publication
preparation and durable successful budget before canonical completed. Source
17760177bytes,report19809238bytes;absolute CPU21.261176437s,wall19.862585432s,
peakRSS216936KiB,finalAS144199680bytes,nice10,unchanged300CPU/512MiB/1200wall
limits,violations empty. All25profile source hashes stayed unchanged. Detailed
private closure hashes/receipt remain local for reviewers,not public operations
metadata. An earlier invented v1 failed because its synthetic DC fixture omitted
the publication specification metadata;failed evidence is preserved. The v2
fixture correction changed no market-data or strategy rule and raised no limit.

Final fresh source/integrity reviews,exact-head CI,normal merge and actual
single GCP invocation remain pending. No DE actual input has been read.
