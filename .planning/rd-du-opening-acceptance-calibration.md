# Research Direction Task DU — calibrate the fixed next-bar opening-range acceptance

## Performed intake and pre-access registration — 2026-10-25

The operator approves the next step after DT: measure the already fixed
two-close acceptance hypothesis's signal frequency, weekly fifteen-opportunity
fit, missingness and newly timed own-label sample planning. Actual Git intake
is DT normal merge57a5bdd22e3d407c411d297e6c7eceaf20c6a7eb;tracked source is
clean. Branch `codex/du-opening-acceptance-calibration`. DT's sanitized completed
record is appended to its ledger in this ordinary work unit. No DU actual
market bodies, events, labels, counts or sizing have been read at registration.

Only original CB dated targets/calendar and the original saved CC raw gzip
are inputs. Reuse the reviewed pure calendar, session and planning helpers;
do not replay any completed parent experiment or reuse original breakout,
retest, DK, CG or120minute-complete outcome masks or labels. Retain the whole
original target grid rather than condition on a later outcome being present.
No acquisition, database, external API, market credentials, reserved or forward
data, collector change or trading operation is part of this task.

| Input | GCP path | SHA-256 |
| --- | --- | --- |
| original CB targets/calendar | /home/minjun4897/research-evidence/intraday-cb-universe-20261020-v1/report.json | f1ac24fed9ee3a54dfcac0d25ef1ff339be0a872d8915d849e4369e4d5c5eb91 |
| original CC gzip | /home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz | 4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de |
| canonical decompressed stream | same saved gzip | d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da |

Original identity assertions to recheck:257sessions,56ISO weeks including zero
weeks,5140targets per D policy,5158union chunks and1002211minute source rows.
These are historical package coordinates,not new DU measurements. Preserve
dated membership, all source metadata and exceptional-session assumptions.

## The fixed hypothesis and causal labels

Follow DT's `opening-range15-two-close-acceptance-v1` without a new threshold.
R is the initial15minute high. B is the original first subsequent completed
five-minute close strictly above R. W is exactly the next scheduled five-minute
bar after B:close>R triggers;close<=R cancels that name/session with no retry.
Do not require a touch,low,CLV,volume,impulse,volatility or peer condition.
Do not skip an absent W or replace the first B. Missing or invalid contiguous
causal prefixes remain unknown;later gaps do not erase a known decision.

Decision D=W.end+2minutes must be <=session.open+240minutes. Entry E is the
first five-minute OPEN strictly after D, giving the original three-minute wait.
If no W can become available within the cutoff, no later W is required to
establish the cutoff disposition;incomplete earlier causal prefixes stay unknown.
New label uses E to E+30minute cap OPEN,requiring all31valid unique minute
buckets including cap. Outcome availability is cap+3minutes and is never a
selection feature. Retain exact prices/Fractions;never substitute B/retest
labels or same-bar/touch fills. At most one signal per name/session.

Keep observation_date,available_at,retrieved_at,source,is_final,data_vintage,
evidence_level distinct. Historical minute-open semantics,bar-end availability,
exceptional schedules and later revisions remain assumptions or unknowns;
retrieval does not establish historical availability or finality. Distinguish
missing/conflicting/invalid buckets and genuine zeros without inventing a
prelisting,delisting,halt or supplier-failure cause. No zero fill.

## Measurements, not a performance comparison

Retain every target's signal/known-no-signal/unknown state,first breakout and
exact-next-bar decision,metadata,vintage and known/unknown newly timed label.
Report event counts,distinct dates,all56week/month counts,zero weeks,weekly
minimum/median/maximum and weeks attaining15. Separate observed signals per
week from price-known outcomes per week. At most one per unknown target yields
only a loose upper bound,not a signal estimate or executable trade promise.
D+1/D+2 remain overlapping daily-input availability assumptions,not holding
periods or independent replications.

Use existing own-label SD,naive SE,session-cluster SE,circular whole-five-session
bootstrap2000draws/seed0/maxSE and corrected-to-naive ratio over all257dates.
Compare MDE with assumed33bp cost. Unknown labels,undefined sizing or MDE>33bp
remain explicit later comparison checkpoints;they do not stop completing this
requested calibration. Below15/week is operating fit,never a reason to force
trades,retune thresholds,close a direction or promote. No previous waiver
transfers. No new mean,net return,win fraction,cost-exceeding fraction,contrast,
pvalue,final comparison family,strategy qualification or promotion in DU.

## Execution and delivery

Fresh producer owns new public module/tests/specification;separate independent
worker reconstructs targets,bars,first B/exact W,new labels,metadata,counts and
own sizing from supplied original bytes without producer math. A third worker
owns the bounded caller/profile/preflight. Main owns registration,completion
audit,source/CI/normal-merge checks and reporting. All actual input access waits
for distinct final source and integrity reviews,required exact-head CI,normal
merge with identical whole tree and integrated frozen preflight.

Study `intraday-opening-acceptance-calibration-v1`;mode
`fixed-opening-acceptance-own-label-frequency-calibration`. Exclusive output
`/home/minjun4897/research-evidence/intraday-du-opening-acceptance-20261025-v1`.
Canonical log `/home/minjun4897/trading-engine/runs/experiments.jsonl`.
Durable started precedes original market bodies. Freeze complete transitive
public/private/helper/runtime closure. Exact final representative full-geometry
invented pipeline on GCP exercises intake,producer,independent reconstruction,
all source/output rechecks and full substantive encoding before budget success
and canonical completion. Configuration must survive sorted-JSON roundtrip
without generated-program identity drift;private declarations retain fixed order.

One unchanged CPU300seconds/AS512MiB/wall1200seconds/nice10 budget contains the
whole actual process. No automatic retry,resume,partial-result adoption,clock
reset,raised limits or overwrite. Preserve failures. Only completed-output
algebra/prose auditing follows,not raw/statistical replay. Only isolated research
checkout advances. Collector stays tracked-clean at4ce85d7;no pull,restart,
redeploy,reschedule,environment,configuration,credential,DB,risk or order change.
The two protected operator untracked originals remain outside all reads,
imports,tests,scans and staging. Named local checks and staged scanner only;
the full CI suite runs in a clean checkout. No paid tool,automation or inquiry.

Report in Korean:fixed rule,counts/weekly15fit/gaps/own sizing,both timing
policies,verified facts versus assumptions,code risks removed and remaining,
actual prerequisite conditions reduced/remaining,review/CI/merge/GCP evidence
and next concrete step. Four delivery groups:implementation and invented
verification;separate reviews and CI/normal merge;actual isolated calibration
and independent output verification;completed-output audit/report. No operator
decision is pending for this measurement. New DU performance remains unmeasured.

## Completed measurement and next operator choice — 2026-10-26

Normal PR365 merge completed after distinct source/integrity PASS reviews and
all8required checks;both full Python runs passed10515tests,3existing skips and
169subtests. One isolated actual calibration and separately authored raw
reconstruction completed within unchanged limits. Completed-output algebra/prose
audit passed with0warnings. Public sanitized delivery evidence:
https://github.com/ckrhehfl/trading-engine/pull/365#issuecomment-6399183838 .

D+1/D+2 observed812/809signals and807/804known newly timed outcomes over the
same257sessions/56weeks. Each policy retains5unknown outcomes. Observed weekly
signals14.50/14.45,28of56weeks reach15;own-label MDE planning13.96/13.91bp is
below assumed33bp cost. This is not an observed return or final family readiness.
About50.8%of original targets remain signal-unknown. No mean,comparison,signal
replacement,old waiver,strategy pass or promotion followed. Collector source
and tracked-clean state remained unchanged;private exact evidence stays local.

The operator explicitly selected investigating these new unknown price windows
before a return comparison. DV registers that bounded recovery diagnostic and
does not adopt an observed-label exception or rewrite the completed DU record.
