# Research Direction Task DZ — cost audit and fixed autonomous five-hypothesis round

## Performed intake and authorized scope — 2026-10-26

The operator activated a persistent Goal: audit costs/completed evidence, fix
at most five intraday hypotheses, then autonomously implement, experiment,
independently verify, review, merge and reflect research code on GCP. Report
after all five or a promising follow-up specification. Paid spending, changed
universe/risk limits, reserved data and trading still need the operator.
This is the actual start of planning and public-source investigation, not a
claim that a new market experiment has run. Starting HEAD is normal DY merge
06faf256760637987a586b7ccfc2a150ddd79dab. New branch
`codex/dz-autonomous-research-round`; tracked files clean at intake. The two
protected untracked activity-component originals stay outside reads, imports,
tests, scans and staging. Use WSL Ubuntu-24.04 in this worktree.

Read-only delegated investigations covered cost evidence, completed aggregate
inventory and reusable input contracts. No new event/price body, DB, archive,
credential, API or GCP read was used to select these hypotheses. The original
CB target list/CC archive are named by existing static committed contracts only.
Completed published aggregates are documentary evidence, not new subset means.

## Cost provenance — facts, inferences and assumptions

CC's original registration99–107 and
`python/research/intraday_pattern_calibration.py` explicitly define:

| Component | Calculation | bp of entry price |
|---|---|---:|
| Assumed commission | buy1.5 + sell1.5 | 3 |
| Assumed execution slippage | buy5 + sell5 | 10 |
| Assumed sale tax including applicable surtax | sell only | 20 |
| Fixed price-return deduction | 3+10+20 | 33 |

This is NOT a measured account fee, quoted spread, full notional-by-side tax
calculation or historical cash PnL. Existing net means subtract33/10000 from
gross return. No old output is rewritten. The new round retains33bp as PRIMARY
for comparable exploration, and uses the same33bp own-sizing reference.

Official sources actually inspected2026-10-26:

- [KIS official fee/tax table](https://securities.koreainvestment.com/main/customer/guide/_static/TF04ae010000.jsp),
  online fee section dated2025-10-27: BanKIS KRX HTS/web/mobile0.0140527% per
  side; the branch-account online rate is separately0.147%. Actual account,
  preferential event and REST-channel applicability are unknown. A separate
  related-institution-cost entry does not prove automatic addition to the
  ordinary BanKIS rate. The old rd-f "on top of" assertion is qualified by an
  append, not erased. NXT rates are not substituted for KRX observations.
- [2025-12-23 brokerage tax notice](https://www.hanwhawm.com/main/bbs/noticeViewPop.cmd?mode=center&nn_id=68002):
  KOSPI total sale levy15bp in2025 (including15bp agricultural surtax),20bp
  under the2026 rule; KOSDAQ15bp then20bp. The stated settlement boundary is
  2026-01-02, corresponding to trades from2025-12-29. Do not split by trade
  year alone. The [official decree amendment](https://law.go.kr/LSW/lsRvsDocListP.do?chrClsCd=010102&lsId=005028)
  records2025-12-31 promulgation/2026-01-01 effect for applicable transfers.

Inference: the flat20bp tax component over the entire2025-09-04..2026-09-23
window is a scenario, not precise historical taxation. Keeping the assumed
fees/slippage gives28bp before the specified trade-date boundary and33bp from
it. This arithmetic does not recalculate an old strategy. The planned secondary
`tax_date_only` scenario applies those values to new event dates. A second
fixed43bp scenario increases assumed slippage by5bp each side. Neither secondary
scenario can qualify a follow-up marker or replace the original primary basis.
Actual fee and fill measurement remains a later validation obligation, not a
reason to ask for credentials or halt this exploratory round.

## Existing published evidence — documentary reuse, no new experiment

DT already authenticated six completed sources and retained44 OPEN rows plus
eight separate CLOSE rows. Its report is
991d9b7cb8832dffa619acf0046a595e1048a45861eba62596b1610af8826c39;
completed audit91b3d24b82c75f3e63342537e7d80778b3c0fdcff6c67bbf146bc438d3b0a42b;
local original transport`var/dt-candidate-update-ssh.stdout` SHA256
cd57fd7bff3f4993dba3272c0fdc567aa677a9f53cfbb5a7df859aa27f62af35.
DY report4da3524e17938a0937fd1f179639e693a68c50b80ba77f696bff1733f1842200 and
audit5ae44db932f6ad7839ededfab1df6ad266efd325e1fcc6af0aafd5d309654367 add the
latest selection/timing diagnosis. Do not replay parent math to reproduce this
table. Values below are rounded transcriptions, not a pooled leaderboard.

| Original scope / condition | Gross % D+1 / D+2 | Net33bp % D+1 / D+2 | Known opportunities/week D+1 / D+2 |
|---|---|---|---|
| CS continuation base | 0.00065 / -0.00013 | -0.32935 / -0.33013 | 14.05 / 14.02 |
| CS continuation context | 0.03331 / 0.03400 | -0.29669 / -0.29600 | 10.30 / 10.25 |
| CS recovery base | 0.00002 / -0.00089 | -0.32998 / -0.33089 | 6.73 / 6.71 |
| CS still-depressed recovery | 0.10298 / 0.10298 | -0.22702 / -0.22702 | 2.32 / 2.32 |
| CW basic15 breakout | 0.00986 / 0.00841 | -0.32014 / -0.32159 | 20.96 / 20.93 |
| CW basic30 breakout | 0.00561 / 0.00598 | -0.32439 / -0.32402 | 17.50 / 17.45 |
| DA volume-observable base | -0.04435 / -0.04468 | -0.37435 / -0.37468 | 9.93 / 9.95 |
| DA volume2x | -0.19876 / -0.19876 | -0.52876 / -0.52876 | 1.16 / 1.16 |
| DH common-complete base | 0.00585 / 0.00439 | -0.32415 / -0.32561 | 20.88 / 20.84 |
| DH first retest | 0.03718 / 0.03432 | -0.29282 / -0.29568 | 9.34 / 9.29 |
| DI opening range-size condition | same as DH base | same as DH base | 20.88 / 20.84 |
| DK managed exit | -0.01038 / -0.01019 | -0.34038 / -0.34019 | 20.88 / 20.84 |
| DN upper-quarter close | 0.03772 / 0.03589 | -0.29228 / -0.29411 | 15.98 / 15.95 |
| DS peer-observable base | 0.00612 / 0.00466 | -0.32388 / -0.32534 | 20.82 / 20.79 |
| DS positive peer context | -0.00018 / 0.00170 | -0.33018 / -0.32830 | 15.82 / 15.77 |
| DX/DY next-bar acceptance | 0.01353 / 0.00896 | -0.31647 / -0.32104 | 14.39 / 14.34 |
| DY selected original entry B | 0.01423 / 0.01244 | -0.31577 / -0.31756 | 14.39 / 14.34 |
| DY selected delay-only C | 0.03178 / 0.02813 | -0.29822 / -0.30187 | 14.39 / 14.34 |

CS retains an old120minute-complete/CG-selected subset. CW uses its own known
30minute labels. DA is the volume-computable subset;DH/DI/DK/DN share the original
basic/retest common-complete population;DS additionally needs observable peers.
DX/DY keep CT/DU's own intersection and five unknown DU outcomes each. They are
not interchangeable populations. B is hindsight selection, C a mechanical
counterfactual. The separate CLOSE endpoint comparisons are not pooled here.
DT's52 rows are overlapping comparisons, not52 independent strategies. The
approximately50.8% original signal unknowns and old underpower exceptions remain.

Planning implication, not a direction-closure conclusion: repeated filters on
the same early breakout have not supplied a cost-sized observed average and
often reduce frequency. The next round examines different price paths over a
single60minute holding horizon; it does not search more thresholds on old masks.
No external study establishes profitability for these Korean-stock rules.

## Fixed round — all five before new outcome access

Executable registration is
`configs/research/discovery/intraday-autonomous-round1-v1.json`. No parameter
sweep, result-dependent replacement or sixth hypothesis. Same original monthly
CB top20, both availability policies, 257sessions/56weeks, original source
vintages; long-only cash prices, no overnight, derivatives or leverage.
CC's retained fields are OHLC/timestamps/provenance, not volume, turnover,
orderbook or news. Gap-to-previous-close/VWAP/news rules would need other inputs
and are not quietly approximated from this archive.

Common signal bars are contiguous valid5minute bars. Their availability is
bar.end+2minutes (assumed). H1–H4 decisions end by session.open+240minutes;
entry is first5minute OPEN strictly after decision, giving3minutes wait on
ordinary complete bars. Exit is entry+60minute OPEN; all61 minute buckets from
entry through cap must be valid, otherwise label unknown. Both endpoints must
stay in the same modeled session. Maximum one event/name/session/hypothesis.
Do not impose an old future-complete mask. Later gaps cannot erase a known event.

1. **H1 morning trend, pullback and reclaim.** Initial60minute last close must
   exceed its first open. Starting with the next bar, identify the FIRST two
   consecutive falling closes (the first comparison may use initial60's last
   close). Following that pair, the next THREE scheduled bars are the only
   trigger opportunities: first close above its immediately preceding bar's
   high. Any post-initial60 close strictly below the initial60 high/low midpoint
   before the trigger invalidates the day; timeout/cutoff does too. Invalidation
   precedes the trigger check on a given bar. No second pullback setup.
2. **H2 morning decline, midpoint recovery.** Initial60 last close below both
   first open and initial60 high/low midpoint. First later close strictly above
   that fixed midpoint triggers. No crossing by cutoff means no signal.
3. **H3 failed downside break.** Initial30 low is fixed. First later close
   strictly below it starts a setup. Only the next THREE scheduled bars can
   trigger the first close back strictly above that low; timeout/cutoff cancels
   permanently for that name/day. An absent scheduled bar is unknown, not skipped.
4. **H4 compression then expansion.** Initial30 high-low must be positive.
   From bar ends at least90minutes after open, use the preceding SIX completed
   bars excluding the current one. Their high-low must be at most HALF the
   initial30 range; first current close above their high triggers. No extra
   volume/market-strength condition or narrower-range optimization.
5. **H5 late-session morning strength.** Fixed entry at session.close-75minutes,
   decision3minutes earlier, exit close-15minutes (60minute hold). Initial30
   close must exceed its first open, and latest bar available at the fixed
   decision must close above initial30 high. False condition means no trade;
   no replacement time. Continuous prefix must reach the required decision bar.
   This clock is intentionally later than H1–H4, within the same approved
   intraday scope. The modeled exit avoids using the closing-auction price.

These are hypotheses, not empirically selected winners. H1/H4 investigate
resumption/expansion, H2/H3 recovery, H5 sustained early information. The prior
CA primary literature supplies background only. For H5, [Gao et al.2018](https://profiles.wustl.edu/en/publications/market-intraday-momentum/)
studies US ETFs, previous-close-to-first-half-hour and last-half-hour returns;
our same-day open and earlier exit are explicit different measurements, not a
replication or proof for Korean stocks. Numeric rules above are fixed research
choices, not estimates of an optimal horizon/threshold.

## Fixed control, statistics and permissible autonomous decisions

At each event's decision choose ONE OTHER original CB member with a complete
causal prefix, by nearest absolute ORIGINAL CB rank distance; ties lower rank,
then literal code. Rank is already known from dated membership. Never inspect
future labels before this choice, replace a peer whose outcome is missing or
choose a control by eventual return. The control uses exactly the event's entry
and exit clocks and the same cost. No causal peer means comparison unknown;
the event result remains separate. Preserve all full target states, original
eligibility/availability caveats and missing reasons. Report event-only observed
means and pair-complete means/contrasts separately; do not compare their different
denominators as the same book.

For every hypothesis, compute own sizing BEFORE return means for both event
and control/matched outcome populations actually reported. Use existing exact
Fraction prices, maximum naive/session-cluster/circular5session error,
2000draws/seed0, all257 sessions and zero-event dates. Retain actual MDE and
defined/failed status. Per the new operator delegation, failed MDE does not
prevent a descriptive mean, but does prevent a follow-up marker. Missing labels
are retained and observed-only comparison allowed without another question.
Empty/undefined cases yield no fabricated mean/interval and continue to the next
fixed hypothesis. No source-integrity waiver, forward access or inferred no-data
reason. Paired contrasts use exact event-minus-fixed-peer outcomes and corrected
session dependence; intervals are descriptive, not proof of edge. Report the
corrected-to-naive error ratio. Fixed calendar halves and name contributions
describe concentration without choosing new subsamples.

Freeze this **conditional follow-up marker**, separate from the Eligibility Bar:
both policies' required own sizing passes; positive event net33bp mean and
positive paired mean; event net33bp lower bound above zero using maxSE with
two-sided99.5% normal interval (Bonferroni reference over at most5x2 primary
views); at least15 known event opportunities per full56weeks. No smaller-cost
scenario can meet the marker instead. D policies overlap and do not constitute
independent confirmations. Even the marker is a spent-sample follow-up proposal,
not familywise-confirmed edge: previous unbounded discovery remains relevant.
If reached, stop this round and report a separate validation proposal; do not
read reserved data. Otherwise finish all5 and report each, including unavailable
or uninformative results. No new sweep to force a winner or15/week.

## Engineering work and completion evidence

First unit: this evidence/authorization/design plus a narrow pure registry
validator and mutation tests. Validation must freeze every parameter and
binding text, not just accept any nonempty description. This unit reads no
new market input and is NOT an actual detector run. Reuse completed reports
for the documentary table; do not build another aggregate pipeline to copy it.

Next unit: a small shared session detector/label/control implementation and
producer-independent reconstruction. Reuse CC parsing, bars, calendars and
existing statistics as appropriate; no generic agent platform or trading engine.
Register exact full sources/private helpers/runtime/pins and exclusive output
before EACH actual hypothesis unit. Each unit includes both policies, production,
independent verification, identity rechecks and substantive encoding under ONE
CPU300s/AS512MiB/wall1200s/nice10 budget. Full geometry invented GCP profiling,
separate source/integrity reviews, required CI and normal identical-tree merge
precede each first actual read. No partial adoption/retry/reset or second verifier
allowance. Per-hypothesis units are fixed here, not split after a budget failure.

Routine questions are replaced by the explicit missingness/underpower policy;
qualification criteria are not lowered. Paid services, universe/risk changes,
reserved/forward inputs, trading and material outside-scope actions remain human
decisions. Preserve collectors/processes/cron/env/credentials/DB. Advance only
the isolated GCP research checkout. New research findings are recorded here;
writing settled conclusions into CLAUDE.md retains its human checkpoint.

Progress at this append: cost evidence and completed-result inventory done;
five designs fixed; registry implementation/review underway; NEW hypotheses
actually measured0/5; new follow-up markers0. Goal remains active. The next
progress append must distinguish source delivery from actual experiments and
name run/verification/PR/deployment evidence for each completed unit. Final goal
completion requires the authorized round's actual outcomes or documented real
input limitations, independent checks, merged source, GCP reflection and Korean
result/remaining-risk/next-decision report; a plan alone cannot complete it.

## Registration delivery and shared execution implementation — 2026-10-26

PR370 completed normal merge292eed0ecfdca61851aed67bf682c37c9be6cbd8 from
reviewed sourcefe0e027955c56a1f9f4135c1bb7152092d79b886; identical whole tree
c8a38d160eeafce2022696aef15779d7201248b1. Separate source and research-integrity
reviews passed with no findings. All four required check types passed on both
push and PR. Python runs40298280631 and40298333986 each recorded10708passed,
3existing skips and169subtests; actual elapsed2140.24s and2481.45s respectively.
Final gate record: PR370 comment6413271691; reflection completion6413299389.

The reviewed pure-registry helper advanced only the isolated GCP research
checkout to the normal merge and validated all five IDs. Collector HEAD stayed
4ce85d714890b87f1a57ae89d4942660e41c0483 and tracked-clean before/after. Market
input reads, DB/API/credential reads, collector log and process changes were0.
Private reflection receipt`var/dz-registration-reflection.json` SHA256
bc3a197a69526a433bedb53e72c16636390b3d3e871c87a2388567d1f76a1c63.
This completes registration delivery, not an actual hypothesis: measured0/5.

Shared execution work starts from this merge on
`codex/dz-five-hypothesis-experiments`. Public pure modules separate fixed
signals, fixed61bucket labels/causal peer choice, and one streamed experiment.
The producer retains one day's source sessions, every original policy target,
shared seven-field source metadata and exact chunk vintage. Source references
link each event and preselected peer to their own original chunk; original
membership clocks remain attached by the immutable policy/day index. Missing
event/peer outcomes never cause peer replacement or erase the known signal.
All four populations' own sizing for BOTH policies precedes reported means.

The four populations are event-only observed, pair-complete event,
pair-complete peer and the same-pair difference. Each sizing record retains
unknown counts. Fixed calendar halves split the registered calendar-date span
at its integer-day midpoint; symbol contribution summaries include every target
symbol. These are descriptions, never outcome-selected trading filters. Zero
exact dispersion is explicitly uninformative even if floating bootstrap
arithmetic leaves a tiny positive residue: keep the measured legacy values,
do not publish an artificial confidence interval or qualify the marker.

The stdlib-only private verifier independently reconstructs minute buckets,
five-minute prefixes, all five state machines, original-rank peer choice,
labels, own plans, statistics and every published row. It imports no producer
or project statistical implementation. Exact prices, fractions, identities,
counts and booleans compare exactly; independently recomputed statistical
floats retain the narrow declared numeric tolerance. After that comparison it
authenticates the exact published-summary bytes by SHA256, while preserving its
own recomputed summaries in the receipt. A redundant byte-equality check of
floating calculations was caught by invented integration tests and removed;
this does not relax exact price or source checks.

An early separate source review caught the inherited Python4300decimal-digit
conversion ceiling for accumulated exact Fraction denominators. The dedicated
execution process uses the same finite100000digit ceiling as the preceding DY
implementation, authenticates its readback in the runtime manifest, and shares
it with the independent verifier. CPU300s/AS512MiB/wall1200s/nice10 remain
unchanged. Regression fixtures use many distinct denominators rather than a
few repeated prices. This is an engineering serialization setting, not changed
return precision, statistical policy or a resource-budget extension.

Private execution is fixed to one hypothesis and both policies. Each full
invented GCP profile uses257dates,56weeks,5140targets per policy,5158chunks and
1002211rows, distinct invented prices and signal/no-signal/unknown plus
known/unknown event/peer labels. Its scientific registration bytes remain the
original registry; only separately identified invented input pins differ.
Source/runtime closure, exact generated program, complete producer+verifier+
serialization budget and durable invented completion must pass before actual
access. Actual outputs are exclusive
`/home/minjun4897/research-evidence/intraday-dz-round1-hN-20261026-v1`, N1..5.
Units after H1 authenticate every prior completed publication and refuse to
continue after a conditional follow-up marker. There is no automatic retry.

This append records implementation and invented verification work in progress.
No new market hypothesis has been measured. Final source/integrity reviews,
all required CI, normal merge, exact GCP full profiles and individual actual
execution receipts remain separate obligations, not inferred from unit tests.

## H1 completed; H2 execution repair — 2026-10-26

The preceding implementation-progress snapshot is superseded by actual
completion evidence, not by a changed research design. PR371 completed normal
merge5f33b09cc52771f1a98b40fb09f18a83f2589c89 from reviewed source
68b4eb059866b27d022fd2dc828b26ab662e16c2; identical whole tree
b541a616613e578a001074361180645f68528070. Separate source and integrity reviews
passed. All four required check types passed on push and PR; Python runs
40304765370 and40304821772 each recorded10827passed,3existing skips and
169subtests. The isolated GCP research checkout reflected the merge; collector
HEAD4ce85d714890b87f1a57ae89d4942660e41c0483 stayed tracked-clean and unchanged.
Review/merge evidence: PR371 comments6413913084 and6414166718.

H1 actual run796daddc-f4a0-4b25-a78a-8f9522be11dc completed production and
producer-independent raw reconstruction in one shared budget:126.583214248CPU
seconds,128.852863485wall seconds,108605440address-space bytes. Its report SHA256
is c4e1ddcc06f32f15a23106dce4cf8c3cf62d6697df418267f9665b9e9173c92a;
local immutable publication`var/dz-h1-publication.json` SHA256
eb086a47fc250cbb640ee98e74787a87a6ca84da035821238469e7943158d8c6.
Remote evidence is under
`/home/minjun4897/research-evidence/intraday-dz-round1-h1-20261026-v1`.
Canonical started/completed span offsets6146786..6156951 contains2records,
SHA25631aa270cc5536de23e4eefb88ef23149cfa5c61ec4751a688fcede08de6bdb1c.
Completion and the subsequent saved-output evidence audit are recorded in
PR371 comments6414299483 and6414694181. That later evidence audit did not
rerun raw inputs or add a second mathematical-verification allowance.

| H1 saved result | D+1 | D+2 |
|---|---:|---:|
| Original targets |5140|5140|
| Signals |285|284|
| Known outcomes / complete pairs |279|278|
| Unknown outcomes |6|6|
| Unknown signal states |2569|2571|
| Known opportunities per full56weeks |4.982143|4.964286|
| Mean gross price return, percent |−0.148721|−0.158079|
| Mean net33bp price return, percent |−0.478721|−0.488079|
| Paired mean difference, percentage points |+0.003328|+0.003099|

All eight own-label sizing plans passed, but neither policy met the positive
net-return or15/week conditions. The conditional follow-up marker is false.
This is an observed-only exploratory result, not account PnL, direction
closure or strategy PASS. D+1/D+2 are overlapping daily-information-availability
scenarios, not independent confirmations or holding periods. Missing source
states, unproved historical availability and assumed execution costs remain.

H1's first invented profile failed CPU and was retained. Reviewed equivalent
private-verifier improvements passed the new exclusive H1v2 full GCP profile
at270.000632201CPU seconds before H1 actual access. H2's separate full GCPv2
invented qualification then failed at300.076326819CPU seconds during independent
raw reconstruction. H2 actual inputs have not been read. No partial result is
adopted, no old failure is overwritten, and H1 must not be rerun for this repair.

Invented-only diagnostic evidence is separate from qualification. The32day
GCP diagnostic completed at13.726946061pipeline CPU seconds. The subsequent
full257day instrumented diagnostic exhausted its shared300second CPU allowance
in the producer; its cProfile overhead is included, so its elapsed figure is
not an uninstrumented runtime estimate. GC accounted for about5.52wall seconds;
the profile also identifies expensive deep copies of outcome inputs that the
streamed producer immediately discards. Neither diagnostic reads actual market
inputs or qualifies an actual experiment. Preserve their stdout/stderr and
exclusive directories. Branch`codex/dz-replay-efficiency` investigates an
equivalent repair under unchanged science, price precision, fixture diversity,
geometry and resource limits, followed by final separate reviews, CI, normal
merge and fresh exact full GCP qualification before H2 actual access.

Current actual progress is1/5, conditional follow-up markers0. H2..H5 remain
unmeasured; the persistent Goal remains active.

The targeted repair adds an explicit`retain_inputs=True` default to
`match_outcomes`. Existing callers still receive isolated input snapshots.
The streamed producer requests`False` because it already stores compact
labels/selection separately and discarded those three deep copies immediately.
Both paths retain every identity, timing and unknown-label check and the same
exact return arithmetic. No detector, statistical estimator, source parser or
independent verifier changes. The final report shape remains unchanged.

A full-geometry local invented H2 candidate comparison retained identical
report and independent-verification bytes: report SHA256
30ccc3c1e8aa6f7d8a2c8b38fb94b8c32548409e0fbdbfa6d189df3981b9dfa7,
verification SHA256a4078663a387ab00ec3fb62d57a8b96c760e221e64a5fe08986b0d8cbd82f596.
Producer CPU fell17.022340873 to15.299668225seconds; complete pipeline CPU
fell27.909228953 to26.184139964seconds, about6.18percent. Callback counts,
1002211rows and7549digit exact denominators remained. These are local diagnostic
timings with memory IO adapters, not exact GCP qualification or guaranteed GCP
savings. Saved diagnostic:`var/dz-performance-pair-snapshot-results.json`.

Fresh qualification uses exclusive`dz-round1-hN-profile-20261026-v3` remote
folders and corresponding local`profile-v3` programs/evidence. Builder, runner,
preflight and invocation agree onv3 and rejectv1/v2 paths. All prior generated
programs, failures and H1 actual artifacts stay intact. The scientific registry
SHA256 remains580f4aea5776a3147fc5f6aa30a8fd267d14316e0f229acf27d9fcbd08da923d.

## H2/H3 completed; authenticated H4 execution failure — 2026-10-26

PR372 completed normal mergebbf596a5241cb46e919a54dfad9fcd70086189d0 from
reviewed sourcee146da75834ae81478fbd6edbb715c730360bb8e; identical tree
6f24e94a3c3fd188cf8475161370367fd99f294b. Separate source/integrity reviews and
all eight push/PR checks passed. Python runs40316477248/40316522661 each
recorded10841passed,3existing skips,169subtests. Only the isolated research
checkout reflected the merge; collector4ce85d714890b87f1a57ae89d4942660e41c0483
remained tracked-clean. Final merge gate: PR372 comment6415856839.

H2's fresh v3 full invented qualification passed before actual access. H3's
v3 qualification failed CPU and was preserved. An equivalent private timestamp
repair changed only fixed-KST minute indexing, preserving out-of-range errors;
separate final reviews and fresh H3v4 qualification passed. These repairs did
not change the scientific registration, exact prices, fixtures or resource
limits. Source/qualification details: PR372 comments6415112153,6415206878,
6415375845,6416150172 and6416239747. Local diagnostic timings are not evidence
of the cause of GCP timing variation.

| Completed actual unit | H2 | H3 |
|---|---|---|
| Run ID |c7796767-ced8-4c76-bc27-2f24d3ed20e3|2ff141a1-f459-4f19-8134-d53688c4e72a|
| Report SHA256 |91d4a403fab998cbae5d82e9e7de55ff0a8d710a923d79fe10fe49df694f54c1|cffe895f4a483ff2df0840726feadf6d71da56a994c5f88e542d37cabf404a3d|
| Immutable publication SHA256 |8b0f6daea62eafd1c2eb215108c1962bc2218cfa52c9d65e95ff5268081032e9|ca7c44afc0540937371d1e8d940fb2efc10c90f9ff747da1eb1eacc00f24808e|
| Canonical started/completed span |6156951..6167094|6167094..6177241|
| Canonical span SHA256 |c5317888d93eb3fa8ac3f3e2e8d80293e813dbcb3112e7fa28aeb2e47f5ebbba|fb05979778af65e436c46da73dfc525e4138f01c08d3859570cff077df7c8598|
| Absolute process CPU seconds |136.297086026|256.4233637|
| Wall seconds |138.651918225|258.574905298|

Both completed producer and independent raw reconstruction in one unchanged
CPU300s/AS512MiB/wall1200s/nice10 budget. Separate saved-output audits found no
required fixes and performed no second raw replay. Completion/audit records:
PR372 comments6416107772,6416358694 and6416393744. Saved publications do not
separately expose prior-publication config pins; those audits checked the frozen
caller's enforcement and did not claim remote prior-registration readback.

| Saved observed result | H2 D+1 | H2 D+2 | H3 D+1 | H3 D+2 |
|---|---:|---:|---:|---:|
| Signals |530|530|481|481|
| Known event outcomes |529|529|471|471|
| Unknown event outcomes |1|1|10|10|
| Complete pairs |529|529|467|467|
| Unknown pairs |1|1|14|14|
| Unknown signal states |2593|2595|2594|2596|
| Gross mean, percent |+0.036173|+0.034869|+0.058560|+0.058560|
| Net33bp mean, percent |−0.293827|−0.295131|−0.271440|−0.271440|
| Paired mean, percentage points |−0.034815|−0.029861|−0.001466|−0.004164|
| Known opportunities per full56weeks |9.446429|9.446429|8.410714|8.410714|

H2's eight own-sizing plans passed. H3's six event/peer plans failed sizing;
only its two paired-difference plans passed. H3 remains an authorized descriptive
comparison, never a sizing pass. Both hypotheses' conditional follow-up markers
are false; all four paired95percent intervals include zero. These are observed
price returns, not account PnL, direction closure or strategy approval.

H4v4 invented qualification failed at absoluteCPU300.307067963s. A second
equivalent private repair reuses validated immutable Decimal OHLC tuples only
inside one session, keyed by the exact four strings after type checking.
Invalid values are not cached; source/timestamp/fetched-at, missing/conflict
states, exact arithmetic and budgets remain. Full local invented H4 report and
verification bytes were identical, with198 edge comparisons. This is a measured
local optimization, not proof of the cause of the earlier GCP timeout. Separate
reviews passed;341named tests,1existing skip,161subtests and the scoped guard
passed. Reviewer directly ran23changed tests and11subtests. Details and final
private pins: PR372 comment6416823743.

Fresh H4v5 full GCP qualification passed: absoluteCPU209.98868177s,
wall215.046827527s, evidence64631d1f9ca88511e33871e2fb358b1f71365e01bf0efce08a5f16d988edb9df,
manifest9ae7bfcd574dd00ce862a9fd2cd96f79f53825db1ff3ef0367f6f99f5f243357.
This did not guarantee the actual run's runtime. Actual H4 run
5dfdfe5f-30fe-4a18-8b0c-bc1da1e45970 subsequently FAILED during independent raw
reconstruction at absoluteCPU300.021552118s, wall302.186154820s and address
space164483072bytes. Source data had been read, but independent verification
did not finish. No verified publication exists; no partial report contents,
mean, sizing result or follow-up verdict were adopted. This is an operational
failure, not evidence that H4 loses money. Record: PR372 comment6417131625.

Bounded failure evidence `var/dz-h4-actual-failure-evidence.json` SHA256
1ec924792f15f966a1695cf926546ab3b21b5ab637c0e77896da927920cebbd4 preserves
only failure/registration/source-manifest and the exact started/failed pair:

- Failure receipt SHA2565676deda9e757820a7b0bf87ae498a73e273dd067348098f89fac9fbe169642c.
- Registration SHA2564689992c3baa46e93fe877b5c2b025536d95e1aa6c188a677d90e25bef5ce4ae.
- Source manifest SHA256038d332fd2662e1070a4f0f888bc00ea4cada117aa94a31ece4ca6230a0cc2c0.
- Canonical span6177241..6185877,2records, SHA256ff9905f046df7855dcc4373378ed52ab125ec91f12b089c0ee3dd444f4255051.

Post-failure read-only state check confirmed collector unchanged/tracked-clean,
research checkout atbbf596a/tracked-clean, and H4 verified publication absent.
No partial report or raw market body was read during this failure audit.

## Narrow terminal-failure contract for H5's first unit — work started 2026-10-26

The original five-hypothesis delegation includes reporting unavailable outcomes
and proceeding through the fixed hypotheses. However, the original execution
gate above and its caller require every predecessor to have an independently
verified publication. That blocks H5 after an authenticated H4 runtime failure.
This append records a narrow engineering correction within that delegation,
not an H4 retry/reset, a new hypothesis or a changed scientific criterion.

For H5 ONLY, retain exact authenticated H1/H2/H3 completed publications and
their verified false follow-up markers. Accept ONLY the identified H4
started/failed pair plus its pinned registration, source-manifest, failure
receipt and post-failure state evidence as a distinct terminal predecessor.
Authenticate the original run/spec/input/source identities and shared CPU300
independent_raw_replay BudgetError. Missing, active, different-run, different
failure, source-integrity or changed-budget evidence must fail closed. H4's
follow-up marker remains UNEVALUABLE, never false or a scientific negative.
Do not use any partial H4 statistic to make the stop decision.

Derive H5 ordinal5 and its original exclusive output path from the fixed
registry, not the count of verified prior publications. Preserve the H4 failed
output directory and every earlier qualification failure. H4 is not rerun,
resumed or given a second verifier allowance. Expose the terminal predecessor
as separate provenance in the H5 execution evidence; do not mislabel H4 as a
completed verified unit. All other predecessor and follow-up-stop checks stay.

The amended private source closure must receive separate source/integrity
reviews, negative gate/ordinal tests and fresh exact full invented GCP
qualification under new exclusive v6 paths after this normal reviewed/CI
documentation merge. Old v5 qualification cannot authorize changed code.
Scientific registry SHA256580f4aea5776a3147fc5f6aa30a8fd267d14316e0f229acf27d9fcbd08da923d,
original inputs, H5 signal/clocks/controls/costs/statistics and resource limits
remain unchanged. No new market source, DB/API/credential or collector change.

Current state is THREE independently verified hypotheses, ONE failed actual
attempt with no adopted result, and H5 unrun. A CPU failure is not a real-input
limitation and cannot establish completion of all five research results.
Complete H5's already-authorized first unit before reporting the remaining H4
decision; do not silently redefine the Goal or mark it complete on this append.
