# EL causal scenes, forecasting and action comparison

2026-11-11. This records investigated and implemented next-stage work under
the active EL Goal, following `rd-el-event-first-scenarios.md`. No EL event,
fitted actual model or economic comparison was inspected when these definitions
were fixed. The support release was normally merged in PR #456 at
`3474352a7e08e63cad55a7c6cc4d1cac57942d3f`; whole invented and actual support
production and their independent reconstruction completed. Private results and
source bodies are not part of this public source release.

## Scope and new evidence

Keep the original dated CB populations, their union and the full 257-session
calendar. The two membership clocks are highly overlapping sensitivity arms,
not two independent samples. Retain every original unsupported coordinate.
Cross-session economic price units, quotes, spread, depth, aggressor side and
news remain unsupported. No equation may manufacture those missing channels.

The support census separates a full regular session from complete continuous
trading. The current [KRX official VI description](https://regulation.krx.co.kr/contents/RGL/03/03020407/RGL03020407.jsp)
distinguishes continuous trading until 15:20 from the 15:20–15:30 closing call.
This supports the following **inferred scheduling assumption**, not a certified
historical channel/calendar: the continuous portion ends ten minutes before
each original declared close. Preserve original regular-session metadata, but
scan events and require modeled entries and scheduled exits strictly within
that continuous portion. Do not infer a halt or a zero-volume trade from absent
auction-minute candles. Short original sessions retain their own opening/close.

An observed stored numeric bar is not proof of historical receipt, finality,
economic price units, continuous market status or a fill. Existing minute
start+3 availability remains an explicit end+2 assumption. A late supplied bar
is not backdated. No new acquisition or original-input replacement is needed
for this exploratory question; unsupported branches stay explicit.

## Causal event definitions before outcome access

The independent event engine and reference implement three proposed operational
definitions. These are our testable hypotheses, not a named trader's verified
formula. Every scale is frozen before the impulse: median twenty scheduled
minute true ranges, requiring the previous close and no skipped missing minute.
A missing or zero scale is unsupported. All ratios remain exact before the
learner boundary.

| Family | Onset | Confirmation and competing failure |
|---|---|---|
| A_DOWNFAIL | Three-minute close decline at least max(three scales, 66 bp of the impulse-start close); freeze impulse low | At ages 3–12, retest the fixed low within a quarter scale and close above the previous two highs. Further low extension beyond a quarter scale invalidates first. |
| B_ORL30 | After the complete opening 30-minute low is known, a fresh close crossing below it by a quarter scale; breach bar starts strictly after level availability | Reclaim the same level plus a quarter scale within 20 minutes; expiry and gaps remain distinct. This is the opening low, not yesterday's low. |
| C_FIRSTPULLBACK | Five-minute close advance at least max(three scales, 66 bp of the start close) | First half-scale close pullback from the running peak within 10 minutes; freeze peak, retain half the advance, and close above the previous two highs at pullback ages 2–10. Failure overrides same-bar confirmation. |

The 66 bp **past-move floor** was adopted before EL event/outcome reads. It
ensures an economically meaningful historical displacement, not another 66 bp
of available future profit. Preserve broad three-scale observations below this
floor separately. Their minute counts are not independent episode counts.
B's opening-range-to-cost ratio is descriptive, never an added selection gate.

Keep onset, each three scheduled minutes, first pullback and terminal updates.
Expiry without confirmation, no first pullback, structural failure, input gaps
and session end remain distinct. A completed episode needs five reset minutes
before another onset. At the same receipt clock, retain the latest known state
once per family/series; never enter on a stale onset when its failure is already
known. Overlapping families share their symbol/session cluster.

## Dataset, missingness and example choice

Use deterministic half-open calendar shards of at most 32 sessions: [0,32),
[32,64), [64,96), [96,128), [128,160), [160,192), [192,224), [224,256),
[256,257). This is a computational partition, not a sample reduction. Each
actual shard authenticates the complete original source after its durable start
and retains all original coordinates belonging to its calendar range.

Each shard seals its coordinate support, event support, broad observations,
states and **target-free queries first**. It then seals the first three onset
identities per family under a fixed SHA256 ordering and the associated before
cards, before writing separate future labels and after cards. Global three
examples are the three smallest hashes of the union of shard minima. This
identity-only algorithm is frozen for all shards and never reads terminal
state or return to choose a case. The seal order is shard-local, not a claim
that all 257 days were materialized before any future label was computed.

Do not inspect economic summaries, choose examples manually, fit a model or
change scientific parameters until all nine shards have independently audited
manifests bound to the same source and specification. Technical equivalent
recovery retains failed attempts and its explicit lineage. A scientific change
closes this batch and needs a new registered comparison; it is not a repair.
Selected examples may fail, remain unconfirmed or be unobservable. Do not force
one attractive success into the roster. Any later outcome-balanced teaching
display is marked as such and cannot replace the inference population.

Store support as exact per-coordinate status counts and run-length intervals,
not millions of repetitively encoded minute lines. The reference reconstructs
every minute and checks run boundaries, all records and EOF. Missing price,
unknown volume and actual zero stay separate. Source publication, retrieval,
vintage, finality and evidence level remain attached to the original coordinate
and its exact chunk identity. Full-session completeness is an offline diagnostic
and must not determine a decision-time entry or model feature.

## Chart, context and targets

The own-chart sequence is 64 ordered minutes with thirteen channels: exact
OHLC distances to the current known reference divided by the frozen scale,
body, range, close change, known-price and previous-price flags, pre-session
flag, within-session reported-volume ratio and two volume support flags.
Sixteen fixed static values describe clock, event age, frozen scale, level and
structure distances, reference age, support, recent net moves/ranges, efficiency,
compression, range location, time above the level and volume change. These are
numeric bar descriptions; none is certified sell flow or a financial statement.

An optional context has observed peer median 5/15-minute returns and positive
15-minute fraction. Freeze the same-day intersection of the original S1/S2
rosters, exclude self, require at least five complete peers at common scheduled
endpoints. Missing context is not a neutral zero and never removes the primary
own-chart population. Compare own-chart and own-chart-plus-peer models after
refitting both on an identical context-supported training/evaluation roster.
Describe this as observed peers, not full-market breadth.

Separate five statistical targets:

1. Path class: lower-first, neither barrier, or upper-first during minutes
   decision+1 through decision+30. The upper barrier is reference plus
   max(three scales, 66 bp of reference), and lower is the frozen structural
   stop. Same-minute touches of both barriers and preceding gaps stay unknown.
   A high touch is not an executable profit. This proposed future-path target
   is separate from the earlier past-impulse eligibility floor.
2. Modeled entry fill at the fixed next opening under the capped entry model.
3. Immediate net cash divided by the initial requested guard-cap notional.
4. Three-minute fixed-wait net cash on that **same initial denominator**.
   Recompute the known reference at the delayed decision, retain original
   scale/structure, and refuse after a received structural-close failure.
   This is not the most profitable future waiting time or a zero no-trade value.
5. For a causally still-held position at entry+10/+20, remaining **gross** return
   from the next modeled opening to its original expiry or later causal stop.
   Add held age, remaining time and current-reference distance from entry.
   Do not re-charge sunk entry cost or substitute a new 30-minute entry target.

Entry heads are inapplicable to held-only queries, and the remaining-gross head
is inapplicable before entry. Keep independent maturity/support per target.
Known non-admissible entry cash remains in the ledger, but those states do not
become zero-valued entry-head training opportunities. The path target remains
separate. Report forecast quality at onset, active waiting, confirmation and
already-invalid/terminal states separately; recognizing a failure already
received must not be called successful advance prediction.
Known non-entry/non-fill is zero cash on the original opportunity; unknown fill
or return is not zero. A zero allocation cannot define a filled return ratio.
No classifier trains on a label that was not mature before its outer test date.

## Finite model comparison and evaluation

Freeze 97 initial training sessions and four expanding outer tests of 40
sessions each, covering the remaining 160 original dates. Train on earlier
dates only, with all transforms learned there. This repeatedly inspected
period remains Discovery; chronological splitting does not make it a virgin
confirmation sample. No first-pass tuning grid or test-set threshold search.

Compare three shared family/state-conditioned learners: empirical hierarchical
base rates with shrink strength 20, fixed shallow LightGBM, and a compact causal
TCN over the ordered chart. The registered LightGBM has depth3/leaves8,
minimum leaf20, learning rate .05, L2 10 and 100 rounds. The TCN has width16,
kernel3, dilations1/2/4/8/16/32, fixed 200 updates, batch64 and Adam .001.
Use seed20261111, one CPU and deterministic settings; unsupported numerical
operations must fail explicitly. No GPU or installation follows from this plan.
Existing installed libraries may be admitted only after their runtime identity
and bounded invented execution are checked; this does not import old strategies.

Preserve per-head known population and date→symbol/session cluster→episode→frame
weights. A series cannot be split into multiple nominally independent clusters.
Count rows, candidate episodes, counterfactual positions, dates and independent
clusters separately. Small support is disclosed; it does not trigger another
permission loop. A computationally empty target remains unsupported, and a
single-class constant is not counted as a successfully trained classifier.

The two entry-value heads use the same **joint-known** immediate/wait target
roster for training. Retain each original target's known/missing counts and the
additional paired-support loss. Separate known cohorts could confound action
value with different missingness. This is still a selected observed-outcome
training sample, not a proof of missing-at-random. Inference remains target-free
and must never filter a decision by its later label completeness.

Primary model scopes are full own-chart, common-support own-chart, and the
same common-support chart plus peers. Across four folds and three learners,
there are at most 36 fitted artifacts before follow-ups. Count all actual fits,
native head attempts/failures, constants and unsupported heads separately;
a TCN's joint optimizer is not five independent sample replications.
Preserve initial/final loss on a fixed training-only diagnostic sample, loss
summaries over the already registered updates, and sampled row/cluster counts.
These diagnostics add no fit or tuning pass and help distinguish insufficient
learning from a genuinely uninformative input. A completed optimizer loop alone
does not establish convergence or useful probabilities.

Compare path/entry Brier and log scores, class calibration and support, and
price/cash outcomes separately. Show each hypothesis, date blocks, names,
drawdown of event cash only, concentration, fill/miss/unknown counts, gross/net
cash and return per initial opportunity and per fill. Weekly opportunities use
all original calendar weeks including zero weeks. Date-cluster uncertainty
and low precision are reported; 15 trades per week is not a forced quota.

## Execution and held management

The two initial execution assumptions are capped next-minute opening and
one-shot reference-price-or-better at that same opening. They reserve the same
integer size from decision-time guard cap (reference plus frozen scale), under
the existing capital/notional/first-risk rules. A later intrabar low never
manufactures a limit fill. Quote-free minute prices remain modeled fills.
Existing cost/risk constraints remain in CLAUDE.md; they are not increased.

Controls are onset entry, confirmation entry and the model's sequential
observe/commit/enter decisions on the same candidate roster. At the first frame
where either entry value is positive, commit once to immediate entry or to the
three-minute delayed entry, whichever value is greater (ties choose immediate).
The delayed commitment follows the registered wait target's causal structure,
price and expiry checks; it cannot repeatedly re-predict and postpone entry.
When neither is positive, continue observing without committing. The value of
that uncommitted future observation is not claimed to be zero or the wait3
target. Missing heads abstain. Preserve one attempt per episode; do not sum
every frame as another simultaneous trade.
Structural invalidation, missing required observations and time expiry have
priority over an adaptive holding proposal.

Match initial entry/quantity when comparing fixed structural/time exit,
remaining-gross full exit, and half reduction then full exit after the next
consecutive nonpositive update. Receipts must match the decision clock. Odd
shares, partial cash, single charged cost, strict next-open execution and
unknown remaining positions are reconstructed independently. These nominal
event books are not a simultaneous funded account or a deployment approval.

At most one reset reentry belongs to a separately registered, evidence-led
follow-up if the primary results identify premature exits. Reentry must use a
new causal episode/reset, available information, new costs and the existing
campaign risk limits; it cannot erase the first loss. No silent unlimited
averaging down or retrospective reentry date selection.

## Advancement, observation and remaining work

The dataset registration implements the next actual boundary. Pure learner,
execution and observation modules are tested in this source wave, but actual
fitting and action replay need their separately bound runtime/dataset manifest
before execution. Do not claim that pure implementation is an actual result.
The input-support phase is complete; event/case production and model/payoff
results are not yet complete when this document is created.

The no-order observation contract records source/model identity, data vintage,
clock/support, probabilities, proposed action and its reason. Stale/unknown
inputs abstain, revisions require a new vintage, and proposed actions do not
mutate positions or claim a fill. Prepare and test this with offline invented
messages only. No feed subscription, new-period collection, broker credential,
paper order, GCP or live activation is authorized by preparation.

Carry the existing local envelope, independent verification and bounded
technical recovery. Required reviews/CI/normal merge apply to source releases,
not every unchanged fit. Each supported actual stage needs its retained start,
immutable source/input/specification and independent result checks. Allow at
most two evidence-led follow-up bundles per hypothesis under the parent plan;
do not spend them until a specific measured bottleneck is identified. Completion
is the finite requested investigation and observation preparation, not a promise
of positive profit or permission to search until a favorable number appears.

## Final-review corrections before actual event reads

The research-integrity review identified two implementation defects. At the
registered minute-start+3 receipt deadline, absent and not-yet-received bars
now both make held progress unknown. A future bar's eventual existence cannot
make an otherwise identical current received prefix a supported position.
Producer and independent reference preserve the same receipt-prefix invariant;
scheduled liquidation and an already-due liquidation retain their priority.

Keep each counterfactual book's unique query identity, but preserve its original
causal episode separately. Head weights and known-episode counts group by that
original episode, while counterfactual books have their own count. Creating
more possible entries within one event cannot increase that event's total mass.
These corrections precede all actual EL event/model/economic reads.

## Dataset geometry correction after PR 457

The normal PR 457 merge passed whole invented qualification and independent
reconstruction. The first actual shard stopped at population admission before
constructing any event or label. The synthetic population had at most 21 names
on a day. The audited original support census establishes a maximum of 23 in
the union of the two daily memberships. The ceiling must admit that union.

The recovery retains the original population and splits, scientific contract,
clocks, costs, and CPU, memory and wall-time envelope. Invented qualification
now exercises the largest daily population inside the original 32-day shard
shape. A separate recovery unit binds the original failed journal and empty
output slot, uses fresh paths and requires its own exact-release qualification
and audit before processing the first actual shard. No case, fit or cash result
exists from the failed attempt.

## Model and cash comparison registration

The downstream model registration fixes 36 units. Three learners (an empirical
conditional baseline, LightGBM and a causal temporal convolution) use four
expanding chronological folds and three feature scopes. Full chart support
describes the largest eligible own-chart cohort. Only the two matched
common-support scopes identify the incremental effect of adding peer features.
Forecast scores retain per-head scored counts by family and phase. Scores on
different scored rows are descriptive, even on the same original query roster.

For each original episode, compare onset, rule confirmation and model action.
Each control compares two entry fill models and three management policies.
Management comparisons share the same entry request. The held model is trained
on immediate-entry counterfactuals; outcomes after a delayed entry test transfer
to a different holding state. Count fills from eligible arms only. Preserve
unknown cash, missing labels and unsupported model policies in the denominator.
An operational no-order decision has zero cash even when its information was
incomplete. Report missing causal frames, chart support and required context
separately; that zero does not estimate the missing opportunity's payoff.
The primary comparison has a single original entry commitment. A reset and
reentry policy needs a separately registered branch based on primary evidence,
including a new executable entry, a new cost and the original loss retained.

Invented dataset shards are release-bound. Independent receipts bind their
raw archives, index and output. The six full-size model qualifications cover
each learner on the largest chart-only cohort and on the largest expanding
peer scope. Smaller folds reuse those exact-release receipts. The matched
chart-only scope uses the same chart transform on the peer-supported subset.
The largest fold development run retains the
same rows, normalization and training limits, with block-sized tabular
normalization and only the applicable LightGBM matrix resident. A separate
fixed-prediction invented witness verifies action mechanics. It establishes
neither learned behavior nor profitability. A controlled timeout can authorize
one same-release, pre-fit retry with recorded failure and no ambiguous output.
Any attempt that crossed the durable fit boundary requires reconciliation.

The locally audited recovery data and outcome-blind case report precede actual
fold reads. Actual models write a durable start before opening input content,
and seal the fitted artifact and predictions before evaluation targets are
parsed. The audit replays saved inference and independently reconstructs cash
without refitting. The pooled report retains the 36 fold audit and manifest
identities in registered order.

The offline observation schema records both receipt clocks and the previous
held forecast supplied by the causal replay. Half-position exit requires two
consecutive nonpositive forecasts. A stale observation abstains while retaining
the audit trail of an earlier wait commitment. This remains a passive local
decision record, with no order-capable adapter or new-period feed.

The initial forecast heads estimate first-touch class, fill probability and
conditional point payoffs. They do not estimate time to target/invalidation
or an entire conditional payoff distribution. Those targets need their own
registration and maturity checks before later use. Fixed archived minute bars
also do not identify queue position, historical executable spread or market
impact. The comparison therefore reports modeled execution under each frozen
price rule, with cost stress and account claims reserved for separate evidence.
