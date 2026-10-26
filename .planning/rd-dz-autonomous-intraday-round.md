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
