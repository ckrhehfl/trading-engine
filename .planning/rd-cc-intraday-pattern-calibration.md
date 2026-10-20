# Research Direction Task CC — measure two intraday patterns without a performance claim

## 2026-10-20 — implementation and bounded calibration design

This work follows the operator's selected CA short-term direction and CB's
dated top-20 input contract. The operator's continuation authorizes preparation;
an actual read still requires the committed exact specification, separately
reviewed final source, required CI and normal merge. At this entry CB's final
CI is pending and no CC price, signal or outcome has been read.

### What this measures

Keep CB's whole dated source-labelled twenty-name target on every observed
reference session, separately for D+1 and D+2. Missing minutes remain unknown;
do not replace a target, silently shrink the population, reuse the old KR-10
survivors or interpret source labels as certified historical eligibility.
These timing variants concern availability of the monthly membership input,
not one-day versus two-day position holding periods.

The two separate hypotheses use the already implemented CA pure primitives:

- Opening-range continuation: after a completed initial thirty-minute range,
  the first later completed five-minute close strictly above its high.
- Post-drop recovery: the first low at least two percent below the high of the
  preceding completed thirty minutes, followed by a subsequent five-minute
  close at least half a percent above the latest trough. A new low resets the
  trough and cannot confirm recovery on the same bar. The first drop remains
  active until the decision cutoff; there is no optimized restart or timeout.

Use one first event per target/session/family and a decision cutoff four hours
after that session's assumed opening. These are newly fixed mechanism drafts,
not parameters selected by observing this price sample and not a final
comparison family. They do not promise the desired portfolio entry frequency.

### Explicit assumptions and observation boundaries

Raw source stamps are floored to an assumed minute-open label while their
original milliseconds and second offsets remain in the private input archive.
Five exact, valid minute buckets are required for each five-minute bar. Its
assumed availability is its end plus two minutes. These assumptions do not
prove the collector's historical timing semantics. Duplicate minute buckets,
nonfinite/nonpositive/inconsistent OHLC and absent minutes are distinct
diagnostics; no price or economic activity is filled with zero.

The provisional schedule is 09:00–15:30 KST, except 2025-11-13 at
10:00–16:30 and 2026-01-02 at 10:00–15:30. A dated issuing-broker
[November notice](https://www.myasset.com/myasset/customer/notice/CU_0201000_P2.cmd?SEQ=202511100903560000000004&gubun=norNotice)
directly supports the former exceptional stock-market hours. The inspected
[KIS year-end notice](https://securities.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=45922)
places the latter stock-hours table in an image; this text-only investigation
has not directly verified that table. The K-OTC text is a different market
and was not used as stock-market evidence. Keep the full schedule explicitly
assumed and calendar certification false; neither uncertainty requires an
external inquiry or blocks this descriptive calibration.

Signal evaluation consumes only the contiguous valid prefix available by the
cutoff. A later gap cannot erase an earlier observed signal. An exhausted or
truncated prefix is not automatically a known absence of a signal. A no-signal
classification requires the full required decision prefix; otherwise it is
unknown. Keep all unknown target/session records in denominators and monthly
and weekly counts.

Each known event receives a sampled price-change label from the first
five-minute boundary strictly after its decision to two hours after that
boundary: the first minute's open to the last minute's close. All intervening
minute buckets must be valid. A missing future minute preserves the event but
makes its label unknown. Exact Decimal/Fraction arithmetic is retained until
the statistical boundary. This is an observed same-session price label, not
an executable fill, a self-financing position, a realized return or total PnL.
No overnight unit bridge, halt liquidation or omitted-asset exception is
implicitly approved. Future-label availability is never used to select events.

Observation date, unknown full-session availability, this query's retrieval,
source, unknown finality, input-chunk vintage and assumed evidence level are
distinct. Membership observation/availability stay separately named; each
event carries its own assumed decision availability and parameter hash. The
collector's stored fetched_at is a retrieval stamp, not proof that the minute
was available to a historical trader at that time.

### Own-arm sizing, not a final comparison or a waiver

Report counts of signals, known no-signal and unknown inputs, with weekly and
monthly totals. Candidate counts are lower bounds when inputs are unknown,
not executed portfolio entries or independent statistical observations.
Conditional on known labels, report individual-label standard deviation,
naive standard error, session-cluster error and a circular five-session
whole-day block bootstrap with two thousand draws and a fixed seed. Include
zero-event reference days and preserve unknown counts separately. The block
length is a new one-week planning assumption, not evidence of independence
or a copied daily-study certificate. No parameter/block search is authorized.

The design error is the maximum of those three error estimates; MDE uses the
two-sided five-percent normal critical value plus the eighty-percent-power
quantile. Report corrected-to-naive error ratios. Fewer than two dates with
known labels cannot supply date replication: leave corrected error and MDE
unknown even if several stocks produced labels on one day. The known-label
subset is conditional; its MDE does not certify the incomplete target family.

A round-trip **33bp planning assumption** consists of 1.5bp fee and 5bp
slippage on each side plus 20bp sell tax. It is not this operator's verified
commission, observed spread/slippage or a date-correct historical net-return
calculation. The enacted
[2026 tax amendment](https://law.go.kr/LSW/lsRvsDocListP.do?chrClsCd=010102&lsId=005028)
and issuing-broker
[settlement-date notice](https://www.hanwhawm.com/main/bbs/noticeViewPop.cmd?mode=center&nn_id=68002)
show why the historical tax seam must be handled explicitly in a later cost
model. Do not report this fixed planning value as the sample's actual cost.
An MDE above that assumed floor retains the ordinary pre-family operator
choice requirement; no BR/BT/BZ underpower exception transfers. A smaller MDE
alone does not resolve unknown inputs, unverified eligibility, price basis,
costs or any other comparison prerequisite.

No mean/net-return winner, p-value, final comparison, direction closure,
paper/live promotion or reserved-date access is in this work unit. Full-family
and historical eligibility, liquidity, revision, calendar, price-basis and
availability certifications remain false. The next actual result should
determine the minimum remaining input remedy and a separately registered
comparison, not reopen unlimited classification work before measurement.

### Bounded source read and automated evidence

The candidate specification reuses only the already-spent CA window and the
exact successful CB report, whose actual hash is still to be registered.
Read only symbol, interval, timestamp, four TEXT OHLC fields and fetched_at
from the explicit existing collector SQLite database. Use read-only URI mode,
query_only, trusted_schema off, a column authorizer and time/VM/row budgets.
Read each unique target/session once for both timing variants, ending each
short SELECT before the next pair so the collector is not held behind one
long historical snapshot. No index price, volume, fabricated turnover/VWAP,
API, credential, newer date or reserved database is in scope.

The proposed hard bounds are 260 unique codes, 6,000 unique target/session
queries, 600 rows per query, 3.6 million total source rows, 512MiB compressed
input and 1GiB free disk before starting. The parent execution wrapper must
enforce 512MiB address space, 300 CPU seconds, 1,200 wall seconds, nice 10,
OOM preference 1000 and no automatic retry. These are limits, not a request
to consume them. Preserve failures and partial evidence; a failed attempt
does not authorize a blind rerun or scope expansion.

The implementation registers/fsyncs started before upstream/price reads,
streams private gzip JSONL source chunks with their original values, and pins
compressed/stream/chunk bytes, sources, specification, upstream and runtime
before completion. Exclusive outputs and file/directory fsync preserve both
complete and failed attempts. The parent wrapper fast-forwards only isolated
research source, leaves the collector source/processes unchanged, verifies
the exact canonical started/completed span and privately preserves just those
new rows with SHA and offsets. It never copies the historical experiment log.

A separately implemented frozen verifier is being prepared to replay saved
input chunks, event/label arithmetic and aggregate/dispersion calculations
without a second source query or producer/core import. This verifies supplied
bytes and calculations, not extraction completeness, historical tradability,
counterfactual fills or strategy edge. Source review, integrity review, actual
CI, actual execution and actual-output verification are separate stages.

### Work actually verified so far

The new calibration module and its explicit synthetic test file were built
without actual input access. The author's initial test run passed fifty cases.
Separate source and research-integrity prechecks reproduced that run. The
integrity reviewer identified the single-known-date replication defect above;
the parent corrected it and added the counterexample. The updated explicit
file passed **51 tests in 2.21 seconds**; the source reviewer independently
reproduced **51 passing tests in 2.31 seconds**.

The source reviewer also found that the first private wrapper checked but did
not persist its canonical two-row span. The parent now preserves those exact
bytes and offsets and compares both full completed content and stable starting
parameters. Its private synthetic tests cover changed results/parameters and
concurrent extra records, transport failure/timeout preservation and blocked
retries: **nine passed in 2.50 seconds**. These tests perform no SSH, database,
credential or actual study access. Final specification/commit/whole-diff review,
required CI, normal merge and actual CC execution are still pending.

## 2026-10-20 — successful CB input pinned before CC price access

CB completed its one registered run and automatic saved-byte verification.
Its actual counts and provenance are owned by CB's appended completion record;
they show that a material part of the full target matrix has no stored rows.
The CC registration preserves every target and every observed session with
unknown states, rather than reducing to the available names or labeling
missing outcomes as zero. This authorizes no whole-population performance
claim, new underpower exception or omitted-asset accounting exception.

The new JSON specification fixes exactly the design above and pins successful
CB report SHA256
`f1ac24fed9ee3a54dfcac0d25ef1ff339be0a872d8915d849e4369e4d5c5eb91`.
No CC price query, signal count or sampled price label has yet been examined.
The proposed disk/query/runtime limits are now fixed by that specification;
final source/integrity review, required CI and normal merge remain before
the one actual run. CB's separate receipt review is still pending here.

The separately authored private output verifier is complete. It imports
neither producer nor detector, replays raw gzip input and recomputes both
families, exact labels, metadata, gaps, monthly/weekly totals and conditional
dispersion without another database/API read. Its explicit synthetic tests
passed **34 cases in 11.79 seconds**. The source reviewer independently ran
those tests plus the private wrapper tests, **43 passed in 13.70 seconds**;
the integrity reviewer independently reproduced **34 passed in 12.84 seconds**.
These are code/precheck facts, not actual-output verification.

Reviewed private verifier SHA256 is
`a4915af577b3b31534e5ea6a197151caabeef0db3a261992f5f02506d8c3f7bc`;
its test SHA256 is
`efa99244c5f8eb953af611b240420f775267228edcb82cb7ad02da92814bc85c`.
Private wrapper SHA256 is
`66092c21f4329c9573ac59b06961ad10d07a43b32c47e8e6836f132dc84e7514`;
its test SHA256 is
`685763c1e30294768142dbded9b281f429fee182b78333b5c16f635bb6005e95`.
Changes to that closure require renewed review; no actual invocation is implied
by merely having these files or passing their synthetic tests.

## 2026-10-20 — input receipt and registration preflight completed

CB's separate receipt arithmetic/lineage review returned PASS, with its
receipt-only limitations preserved in CB's completion entry. The CC JSON
was parsed and accepted by the implementation's exact `validate_spec`
without any actual upstream/price read. The two explicit document/index
test modules passed **84 cases with one skip in 2.10 seconds**. Final
committed whole-diff source/integrity review and required CI are still pending;
these preflights are not an actual calibration or a performance result.

## 2026-10-20 — actual calibration and saved-byte verification completed

PR #320 merged normally as `fdf12fdd45e88c6f23d50d9b54d391672eae114a`
after separate final source/integrity reviews and all required push/PR checks.
The isolated GCP research checkout ran the fixed calibration once:
`2738b771-70db-4124-a179-f4be58995867`. The frozen independent verifier
replayed the saved raw input successfully, followed by a separate receipt
arithmetic/interpretation review. The collector stayed clean at `4ce85d7`.
The actual execution and verification record is
https://github.com/ckrhehfl/trading-engine/pull/320#issuecomment-6291161267.

Report SHA256 is
`33c642ebbcda1ccca4c68de111629bdff19bc2b58897e71be737b7350522c8a7`;
compressed input SHA256 is
`4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de`;
decompressed stream SHA256 is
`d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da`.
Private evidence is under
`/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1`.

Each timing variant retained 5,140 targets across 257 reference sessions.
D+1/D+2 continuation produced 988/985 signals, with 968/965 known labels;
recovery produced 1,308/1,305 signals, with 1,261/1,258 known labels.
Unknown signal states were 2,618/2,620 for continuation and 2,572/2,574 for
recovery. These are candidate events and conditional labels, not filled trades
or a complete target population. Continuation's conditional MDE was about
27.22/27.20bp, recovery's 35.96/36.09bp versus the assumed 33bp planning floor.
No mean performance, winner or strategy verdict was measured in CC.

The operator subsequently selected one limited both-pattern exploration,
explicitly accepting recovery's power limitation and the observed-data claim
scope. CLAUDE.md and Task CD record that new decision. CC is complete and
must not be rerun to supply the comparison; CD reuses these saved inputs.
