# Research Direction Task DK — fixed basic-breakout managed exit

## Operator request and registration — 2026-10-24

Compare one preregistered take-profit,stop-loss and time exit with the same
basic breakout's existing thirty-minute exit. Account for fill feasibility and
unknown within-minute ordering;complete implementation,independent replay,
separate reviews,normal merge and isolated GCP research reflection without
routine permission prompts. No new market source body has been read at this
registration. Binding numerical and governance rules remain in CLAUDE.md.

This is one deliberately spent-window discovery study,not a qualification,
confirmation,account return,edge claim or direction closure. The design reuses
the CM close-trigger convention rather than fitting thresholds to DJ extrema.
No new official market evidence is asserted. Minute availability,execution and
round-trip costs remain assumptions;retrieval does not establish historical
availability. D+1/D+2 are overlapping universe-input availability variants,not
holding periods or independent replications.

## Fixed population and source boundary

Use only the pinned completed full DJ report and the original saved CC gzip
archive. Keep all original target rows,unknowns,source metadata,vintages,
selection masks and original labels unchanged. The inherited DH-common-complete
basic OR15 entries number1169/1167;each policy retains5140targets,257sessions
and56reference weeks. No new volatility subset or reselection is allowed.
Only these two saved artifacts are inputs;external calls and storage queries
are prohibited. Future-collected and reserved intervals,including pre-2019
history,remain outside scope. Authentication material is never opened.

The caller pins DJ SHA256
`e68e63fffd0338e99c5f360364128a77f1dfb7405df52875beeec191349af89f`,
DJ specification SHA256
`aada6bd387de85185d34811e01e70319c0ece81c105376619f9622d9abfc2913`,
original compressed archive SHA256
`4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de`
and uncompressed stream SHA256
`d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da`.
The archive's5158chunks/1002211rows are fully validated,including unselected
rows. Prior DJ statistics are retained evidence,not newly re-estimated tests.
Actual storage locations and receipts stay in ignored execution helpers.

## One fixed exit policy and executable-information sequence

Preserve each original three-minute-wait entry OPEN E and time-cap OPEN X=E+30.
Both arms use the same hypothetical event cash budget and33bp round-trip cost.
The control exits at X. The managed arm first evaluates completed minute CLOSE
at minute-end T in E+1 through X−4 inclusive. Trigger at return >=+0.01 or
<=−0.005,equality included. T's close becomes available at T+2 by explicit
assumption;an additional minute for execution yields OPEN F=T+3,strictly before
X. The first eligible trigger wins;later reversals do not cancel it. A fill
equal to X uses the preplanned time cap. With no eligible trigger both arms
must have identical exits and returns. Trigger information is never used before
its assumed available_at,and decisions precede execution.

No sale at the threshold,minute high/low or trigger CLOSE is claimed. Actual
subsequent OPEN prices can gap,overshoot a stop or reverse below a take-profit
threshold;report these outcomes and the fixed delay. No spread,queue depth,
partial fills,halts or broker execution is observed,so this remains a price
comparison with assumed fills and costs,not proven executable profitability.

Report minutes touching both TP and SL thresholds as within-minute order
unknown. The rule deliberately depends on completed CLOSE,so its decision does
not require inventing which high/low happened first. Distinguish this from a
resting stop/limit order simulation;do not resolve ambiguity in favor of profit.
Internal path or anchor contradictions refuse the run;no row is dropped and
no missing price becomes zero. Financial ratios use exact integer/Fraction
math. Preserve metadata and future-label availability separately from selection.

## Calibration before conditional comparison

Both exit arms in both policies receive their own new-label dispersion sizing
before any new mean or paired-return comparison. Use all257reference sessions,
including zero-event dates;maximum naive,session-cluster and circular five-
session block-bootstrap standard errors,2000draws,seed0. Apply the unchanged
33bp planning threshold to all four finite defined MDE values. Any failed,
undefined or nonfinite cell yields calibration-only output,explicit not-run
reasons and an operator choice;prior exploratory waivers do not transfer.

Only if all four pass,activate exactly one comparison family. Report gross and
33bp-assumed-cost net event means,positive-net counts,paired managed-minus-time
changes and descriptive uncertainty with whole-session resampling and corrected/
naive SE ratios. Compare identical event coordinates and show exit reasons,
durations,fill threshold overshoots/reversals and original weekly opportunity
counts including zero weeks. No threshold grid,subgroup winner selection,
compounding overlapping events,account MDD,promotion or direction closure.

## Engineering and single-run boundary

Fresh implementation workers own public builder/specification/tests and a
separate supplied-byte stdlib verifier. The latter independently parses gzip,
raw identity/prices,DK labels,sizing and any activated comparison without
producer imports. Preserve all upstream rows canonically. Tests include equality,
time boundaries,first trigger,reversal,gap fills,dual touches,zero-event dates,
gate-before-means,precision,missingness and source/transport mutation refusals.

Profile invented full geometry before actual access. Freeze all public and
private transitive source,verifier,caller,profile and runtime bytes. Verify
public working bytes equal reviewed Git blobs before fixture creation and again
in mandatory caller-integrated preflight before SSH. Separate fresh source and
research-integrity reviews read the complete final diff and execution closure.
All required CI checks and a normal merge/exact tree precede one actual run.

Durable discovery start precedes input bodies. One unchanged300CPU/512MiBAS/
1200wall/nice10 budget contains bounded input reads,raw parsing,production,
independent replay,statistical work,identity rechecks and full Fraction-aware
publication encoding before success/completion. No retry,resume,extra verifier
budget,raised limits or overwrite. Preserve failed/partial evidence. After
completion only mechanical publication/prose algebra audits may run.

Only the existing isolated research checkout advances;collectors,loaded loops,
schedules,configuration,environment,credentials,DBs,orders and risk controls stay
outside scope. Named local tests/staged scanner only;full CI in clean checkouts.
Protected operator untracked files are outside all content reads/imports/scans.

## Reporting and progress

Report verified facts versus assumptions,code risks removed,remaining risks,
actual-run conditions passed/reduced/remaining,D+1/D+2 status and next step.
Track four delivery groups:fixed design/implementation;invented verification and
separate review;CI/normal merge;actual replay/GCP reflection/audited report.
The original approximately51percent unknown signals,retrospective common-complete
population,source/vintage/classification limitations and repeated discovery
remain. A better exit does not establish positive cost-net returns or an edge.

## 2026-10-24 — implementation and named author verification

The fresh public implementation worker completed the pure builder,exact
specification and invented-data regressions. The source requires the same
caller-enriched DJ input contract as the actual parent. It reports dual touches
over the original thirty-minute diagnostic scope separately from its executable-
information rule,and separates TP/SL fill overshoot and reversal descriptions.
Failed sizing explicitly sets calibration-only scope and suppresses comparison.

Final public author command in WSL:

```text
python/.venv/bin/python -B -m pytest -q
  python/tests/test_intraday_breakout_exit_policy.py
  python/tests/test_intraday_breakout_path_diagnostics.py
  python/tests/test_intraday_exit_policy.py
129 passed in23.86s
```

The main author separately ran the two named document guards:84passed/one
existing skip in3.35s after correcting the new index title,count and duplicated
handoff figure. The caller worker's invented transport/preflight/completed-
publication algebra suite passed66tests in4.88s. These are engineering checks,
not actual DK market results. Independent supplied-byte integration,full-scale
invented profiling,separate final reviews,required CI,normal merge and frozen
actual execution remain pending at this record. No protected original contents,
actual DK input,API,DB,credentials or SSH were accessed by these tests.

## 2026-10-24 — completed comparison and isolated research reflection

PR#354 reviewed source56a287df4a07fc4e10c265d4ac3620f92b6c2932 normally merged
asbf100c2363c63c5133d112861c8f8eaec83c0674. Separate final source/integrity
reviews found no remaining findings. Final named author bundle passed266tests/
one existing skip;both full Python CI jobs passed10105tests/three skips and
30subtests. All eight push/PR required checks passed,with no unresolved review
threads or further page and CLEAN state before normal merge.
[Review record](https://github.com/ckrhehfl/trading-engine/pull/354#issuecomment-6380392455).

An invented full-scale profile exposed redundant whole-report normalization
exceeding the memory limit. That failure remains preserved. Removing the copy
and streaming the canonical digest preserved all independent field checks;the
corrected profile and final67-file closure were separately reviewed. There was
no actual scientific retry or resource waiver.

One actual execution completed production,independent raw/new-math replay,
four fresh own-label gates,one conditional comparison and full publication
encoding within unchanged shared limits. No new API/token/DB request or market
authentication-material read occurred. Only the isolated research checkout
advanced;the collector stayed tracked-clean at4ce85d7. Detailed actual receipts
and execution identifiers remain private.

| Same basic entries | D+1 | D+2 |
| --- | ---: | ---: |
| Fixed cases |1169|1167|
| Time-only gross mean percent |+0.0058486099|+0.0043926162|
| Managed gross mean percent |−0.0103828246|−0.0101850098|
| Time-only cost-net mean percent |−0.3241513901|−0.3256073838|
| Managed cost-net mean percent |−0.3403828246|−0.3401850098|
| Managed minus time mean bp |−1.6231434489|−1.4577626032|
| TP/SL/time-cap counts |227/535/407|227/533/407|
| SL triggers with fill proxy below threshold |315/535|314/533|

Both descriptive paired intervals include zero,with corrected/naive SE ratios
1.5306648047/1.5459767951. Original weekly opportunities remain20.875/20.8392857,
48of56weeks at least15cases. Average managed duration18.603935/18.623822minutes
does not establish portfolio risk improvement. The full original30minute path
has one dual-touch minute event per policy;touches never become fills.

Minute OPEN proxies,assumed availability/cost,original signal gaps and repeated
discovery limitations remain. No strategy qualification,account return or
direction closure occurred. The operator next authorized same-book loss
concentration and one next fixed entry hypothesis,registered separately as DL.
