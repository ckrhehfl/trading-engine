# Task EJ autonomous local cost-net candidate discovery

## Standing operator decision, 2026-11-09

The operator requested an ongoing Goal focused on a cost-net strategy suitable
for eventual paper observation, authorized structure changes, asked to avoid
repeating unchanged tests, and required a durable approval record. This document
records that decision. The exact operational boundaries and resource envelope
are in CLAUDE.md. No income, successful search, or completion date is guaranteed.

Interpret "stop the cost problem" as stopping repeated cost-component research
and routine resource-approval loops. Do not silently set trading costs to zero.
Retain the 33 bp nominal round-trip assumption, and show both gross and net.
There is no authorized paid purchase or removal of execution/risk controls.

## Investigated reuse and changed workflow

The completed EI ledgers and sealed decisions already contain the original
five chronological folds, integer quantities, baseline entry requests, actual
modeled minute-open receipts and baseline exits. The saved census independently
reconciles those quantities and costs. Reuse its authenticated parent and row
reader instead of retraining models or reconstructing the entire forecast.
The original CC archive supplies minute prices missing from the sparse saved
open receipts. Existing source decoders and byte-index audit can supply these.
The five-minute management callback cannot represent a first one-minute profit
trigger without a new calculation; implement that small pure calculation.

One reviewed implementation serves a batch. Changes to source require relevant
tests, final source/integrity reviews, required CI and normal merge. Unchanged
code does not get a new implementation PR, full CI, or fabricated qualification
for every setting. Each registered trial keeps its source/input hashes, durable
start, independent price/cash check, all outcomes, failure evidence and missing
observations. Actual data are not used in tests. Source and original artifacts
remain separate. A new mechanism is not smuggled in as a parameter change.

## Work queue and transition rules

1. First isolate exit giveback using the same original entries. Compare the
   original structural/time exit with the fixed 66 bp close trigger described
   below. Preserve original opportunity reservations even if an early exit
   would free cash. This isolates exits; it is not a new concurrent account.
2. If net is still negative, distinguish insufficient favorable movement,
   delayed observation/fill, downside loss, and unstable situation selection.
   Register one causal follow-up axis based on that decomposition. Do not run
   a broad Cartesian sweep or choose later highs as executions.
3. Next families, only when their inputs exist, are pre-entry volatility and
   payoff room, time of day, relative activity within the same stock, and
   trend-versus-failed-breakout state. Keep training-only choice of thresholds
   for learned selectors. Measure what was excluded as well as what remains.
4. A promising entry/exit pair gets same-input cost/delay sensitivity, session
   and symbol concentration, and unchanged-rule chronological fold reports.
   These remain reused discovery data, including the nominal later folds.
5. Only after positive whole-policy economics and adequate stability, freeze
   a candidate specification and a no-order shadow-observation interface.
   Concurrent cash/risk/account effects must be evaluated before account-return
   claims. No broker loop starts and no reserved sample is spent by this Goal.
6. If no candidate survives, append the evidence and choose another finite
   mechanism batch. Do not mark the Goal complete or claim a direction closed.

Each batch contains at most four newly specified policies and one named
mechanism. This is a review/reporting checkpoint, not a demand for operator
permission or a total lifetime trial limit. Preserve the cumulative number of
policies, fits and data-dependent choices across batches. Inconclusive support
is not a scientific loss. Two consecutive technical failures trigger a change
of method under the existing caps, with all failed records intact.

## Batch 1, fixed before original archive access

Use both audited EI baseline populations and their original A/B/other labels,
entries, quantities, cost, stop structure and absolute expiry. The original
ledger is the baseline, including no-entry, no-fill, unsupported, overlaps,
unresolved outcomes and every zero-trade week. The only changed axis is exit.

Starting with the first minute beginning at or after the modeled entry, inspect
each completed minute close only at its assumed availability, minute start plus
three minutes. At the first known close >= 1.0066 times entry, request a full
sale for the next minute open. An original structural or time exit requested
earlier or at the same clock has priority. Never fill at the threshold, high,
or close. Keep the original expiry. The threshold is two times the fixed cost,
a design value, not an optimized historical value. Additional entry/reentry or
new capital from the earlier sale is forbidden in this matched comparison.

A missing/invalid/duplicate earlier required close leaves first-trigger timing
unknown. Missing requested sale open is unresolved. Do not replace either with
the original profitable exit or scan ahead for a favorable quote. Separately
report the whole original population, known original outcomes, new-policy
support, common supported outcomes, and newly unresolved paths. Unresolved
original holdings are retained separately; do not claim a resolved incremental
comparison from them. Any early-profit exit on such an original holding needs
a separate fully specified reconstruction and is not assumed in Batch 1.

Report mean/median gross and net return, exact nominal net cash, win/loss/equal
cost counts, profit factor when defined, paired exit effect, entry and
cost-clearing frequency per original week, five folds, A/B/other, symbols and
sessions. Event cash is not account NAV or portfolio drawdown. S1 and S2 are
overlapping roster-availability assumptions, not independent replications.

## Screening and interpretation

Count a cost-clearing trade only when its modeled realized gross return is
strictly above 33 bp. Do not confuse 15 profitable trades a week with 15 total
entries or positive expected net profit. Weekly frequency is a secondary goal,
not a requirement to force trades. A lower-frequency positive result stays
visible as such. Do not report the best subgroup as the full policy result.

For a prospective-observation shortlist require positive pooled net expectancy
and nominal net cash under the frozen cost, positive net in at least three of
five chronological folds, no sign reversal after removal of the single largest
positive session, and positive base performance in both availability scenarios.
These are declared engineering screening choices, not a significance test or
proof of profitability. Report confidence/precision by independent sessions,
missingness and trial history. A failing screen does not close a whole family.

The original discovery window has already informed many choices. Calling its
later folds untouched validation would be false. Truly new-period results need
a separately frozen specification and an explicit data boundary. Preparation
of order-free observation is permitted; activation/data access is separate.

## Research grounding

- Bailey, Borwein, Lopez de Prado and Zhu, *The Probability of Backtest
  Overfitting* (2015), [author PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
  Repeated selection on the same sample produces false discoveries. Keep the
  search ledger and freeze a specification before future confirmation.
- Zarattini, Barbon and Aziz, *A Profitable Day Trading Strategy for the U.S.
  Equity Market* (2024), [university archive](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content).
  Its activity-based stock selection motivates a causal activity family. Its
  U.S. universe, execution and cost results do not establish a Korean edge or
  validate this project's 66 bp exit. That exit is our proposal.

## Reporting

Each completed batch reports what actually ran, technical failures separately,
data support, gross/net economics, original and paired denominators, trial count,
progress toward an observation specification, and one next mechanism with its
reason. Source CI and a successful process are not a successful strategy.

## Batch 2 registration — pre-entry payoff room

Batch 2 follows the completed, independently audited same-entry comparison and
varies pre-entry selection.
The original feature's fixed target `T` is a geometric price level. The original
prepared order's maximum entry price `Pmax` is known before fill. Admit an
original enter request only if `(T - Pmax) / Pmax >= 66 / 10000`, using exact
arithmetic. The threshold uses twice the declared round-trip cost. Its equality
boundary is included. No actual entry price or realized outcome determines the
selection. Existing unsupported and reserved questions stay in the population.

Seal the decisions on both S1 and S2 before reading the audited outcome rows.
Use the identical gate with original structural/time exits and with the earlier
66 bp profit exit. Compare each gated arm to its matching ungated prior arm.
Preserve the original order limit, sizing, fee, expiry and reservation even on
declined opportunities. A decline is a known no-entry outcome for the gated
arm. Unresolved original outcomes and missing pre-entry information have their
own states. Report selection, known and unresolved outcomes, gross/net returns,
event cash, profitable and total weekly entries, and chronology and concentration
with the existing denominators. Price room does not certify future target
attainment. The completed first batch and its audits are pinned in the new
registration. Only saved decision and ledger artifacts are needed for Batch 2.

## Batch 3 registration — relative activity before entry

Compare one activity gate with the same ungated original enter requests as in
Batch 1. Do not stack the room gate. Use the thirty stored minute volumes for
the same stock in the sealed decision feature. At the decision, require all
thirty observations available on the existing causal clock and a positive mean
in the first twenty-five minutes. Keep the original enter request when the
last five-minute mean is at least twice the preceding twenty-five-minute mean.
Compare exact sums at the inclusive threshold. Missing or ambiguous volumes
and a zero reference mean leave the new policy unresolved. A known zero may
contribute to the sums. The ratio uses a common stored unit within one window.

Seal both scenario selections before reading the original outcome ledgers.
Run the identical selection with the original and the fixed 66 bp exits. For
each, compare matched known cash including a zero for declined entries. Keep
the complete original roster, reservations, size, cost and unresolved outcomes.
Report support by session and fold, performance per filled trade, aggregate
losses, weekly frequency and concentration. A smaller loss from fewer trades
alone does not establish improved selection. The preregistered two-times
threshold is a design value. The study needs only existing saved inputs.

## Batch 4 registration — direction at the activity gate

Use Batch 3 activity-only selection as the control. At each original enter
request selected by that gate, require the latest available close to exceed
the close exactly five minutes earlier. Compare the two sealed normalized
closes on the same positive reference range. A flat or falling close declines
the combined request. An invalid or unavailable comparison is unresolved.
All other activity decisions carry through unchanged. The comparison uses no
minimum price increase beyond strict positivity.

Seal both scenario choices, including the activity-only decisions, before
opening the audited outcome rows. Project both selection rules under each
fixed exit and compare the incremental gate on common known outcomes, with
zero cash for a declined request. Keep all original coordinates, order bounds,
integer quantities, reservations, costs and weekly denominators. Compare the
reconstructed activity-only summary with the receipt-authenticated prior
audited run and require exact equality before publishing or auditing a result.
Reuse the saved decisions and the pinned summary.

## Batch 5 registration — adverse close before the original exit

Keep the exact activity-only entries and the Batch 3 profit-exit control.
For a resolved activity entry, evaluate the first available post-entry-start
minute close at or below 0.9967 times the modeled entry price. The minute is
available three minutes after its start. If the trigger becomes available
strictly before the saved exit request, request a full exit at the next
minute open. The saved earlier or tied exit, original absolute expiry,
reservations, entry quantity and round-trip cost keep their precedence.

Retain all 3,140 coordinates, originally unknown outcomes and nonentries.
A missing earlier close or requested open leaves the new outcome unresolved.
Use the audited activity outcomes, original minute archive and prior read-only
price index pinned by the registration. Audit the independent reconstruction
and same-entry comparison for both scenarios. Report trigger return, next-open
gap and realized return on the same triggered, filled subset, along with
overall and common-known economic results. The 33 bp adverse threshold is a
single fixed design value equal in magnitude to the declared cost assumption.

## Batch 6 registration — elapsed-time exit and price-path diagnostic

Use the same audited Batch 3 activity-plus-profit66 entries and control as
Batch 5. Compare one elapsed-time rule. Request a full exit nine minutes after
the modeled entry fill, to execute at the ten-minute open, only if that request
strictly precedes the saved request. An earlier or tied saved request keeps
precedence. This clock requires no intervening close. Preserve the original
entry, quantity, expiry, reservations, round-trip cost and unresolved controls.
A missing timer open leaves the new outcome unresolved.

Separately, on each resolved control trade, measure the retrospective maximum
of its saved exit open and all earlier archived opens from entry plus four
minutes through the minute before that exit. Four minutes is the first possible
open under the prior post-entry close availability and next-open convention.
Any missing required open leaves the diagnostic unresolved. This calculation
uses future prices and has no role in entries, exit decisions or eligibility.
Report cost-clearing counts on the fully observed diagnostic support alongside
known control and timer outcomes on that same support, with weekly frequency,
entry-time breakdowns and separate scenario results. Keep all original roster
coordinates and denominators. In the independent replay, derive raw-price
admission from the saved entry and exit clocks and reconstruct the capacity
report from the verified coordinates using separate aggregation code. No
unseen outcome can qualify as a candidate.

## Batch 7 registration — pre-entry range expansion through resistance

Use original Batch 1 entry requests, independently of the Batch 3 volume
condition. On the sealed decision, compare the first twenty-five consecutive
available minute bars with the final five. Keep an original request only when
the final five mean high-minus-low range is at least twice the first twenty-five
mean and the latest observed close is strictly above the highest high of the
first twenty-five bars. The two-times multiple is fixed before evaluation.
Validate exact price geometry, positive shared normalization, minute clocks,
and the latest known close at the original decision. Each bar becomes available
three minutes after its start. Missing or invalid support and a zero reference
mean are unresolved. An individual observed zero range contributes zero.

Freeze both scenario selections before opening the audited Batch 1 payoffs.
Project each selection under the original exit and the existing 66 bp early
profit exit, comparing with each corresponding unfiltered policy. Preserve
original entries, reservations, integer quantities, fee, all coordinates, and
unresolved outcomes on retained trades. Measure requests, fills, realized
gross/net, known common outcomes and zero-activity weeks, chronology and
concentration on the original support. Existing audit receipts bind the two
control outcomes and their independently reconstructed prices. A failed gate
has no entry at that opportunity and cannot initiate a later trade.

## Batch 8 registration — opening laggards and the observed basket

Use the original twenty dated members of each S1/S2 session, independently
of prior entry, anchor or economic outcome. At actual opening plus sixty-two
minutes, rank exact same-session first-minute-open to sixtieth-minute-close
returns of observed members. At least ten valid pairs are required. Each
minute's numeric values become observable three minutes after its start.
Missing or invalid pairs are unresolved, and days below quorum are unresolved.
Order ascending by return and literal symbol. Consider the first floor of
one fifth of observed names and select only strictly negative returns.
Compare this rule with the same rule applied only on dates where the exact
median observed member return is zero or positive. Neither arm replaces a
missing or declined member. The observed median is not a market index.

For a selected name, use its decision close as maximum entry price and the
minimum low of the latest fifteen valid available minute bars as structural
level. Missing geometry is unresolved, and a level equal to the limit produces
no order. Freeze size at the worst permitted entry price under the existing
integer risk and order caps. Attempt one entry at opening plus sixty-three
minutes only if that observed open is above the level and at most the limit.
Use the existing exact cost ledger. After entry, sell all at the next open
when an available post-entry close is at or below the level, otherwise at
entry plus thirty minutes. A tied structural request uses the same expiry
open. Preserve any earlier unknown structural evidence or requested open.
No replacement or reentry. Each event uses an isolated study book.

Read only the sealed roster from the earlier decision files, not their
prior economic ledgers. Reuse the audited read-only CC byte index. Raw chunk
authentication includes the original chunk envelope. Numeric selection
projection exposes only the first minute and the final fifteen minutes of the
first hour. Seal both scenarios before projecting entry or holding prices.
Use separate reference projections, rank calculation, accounting replay and
support aggregation in the audit. Keep all 3,140 coordinates per scenario and
distinguish endpoint support, ranking, order geometry, fill, resolved payoff
and strict cost clearance. On common known coordinates, count an observed
market-gate rejection as zero cash against its known rank-only outcome.
Keep this distinct from the equal-entry subset where both trades closed.
Report all sessions and weeks including zero, chronological folds and
concentration. Event cash is not concurrent NAV.

## Batch 9 registration — delayed entry and pre-entry stability

Keep the audited rank-only selection and immediate trade from Batch 8. For
every originally eligible order geometry, compare waiting five minutes with
the same wait plus a stability condition. The two delayed arms each use one
request at actual opening plus sixty-seven minutes for the sixty-eighth
minute offset's open. Their original +63 fill status is not an input. Keep
the original limit, stop, integer quantity formula and absolute +93 expiry.
The delayed decision is earlier than its fill; the absolute expiry shortens
the possible holding time by five minutes.

The stability condition uses only bars starting at opening +62, +63 and +64,
each available three minutes after its start. By +67, all three observed
closes must be strictly above the original stop, and the third close must
be at least the first. Missing or invalid required observations are unknown.
An observed decline means no order in the stability arm. Hold the original
support, immediate book, cost, one-attempt rule and end clock. For delayed
fills, use the same structural close rule after the new fill and sell at
the next open. Unknown relevant prices stay unresolved.

Read the pinned Batch 8 selection files. For both scenarios, seal the three
bars' decisions before opening immediate ledger rows or projecting delayed
execution prices. Independently reconstruct both new decisions and cash.
Compare immediate versus waiting and waiting versus stability on the same
3,140 coordinates per scenario. Count common known outcomes with an observed
decline at zero cash, and keep the matched-closed diagnostic separate. Report
weekly opportunity and fill frequency, payoff, costs, all folds and largest
session sensitivity. No new data or change to the risk envelope is involved.

## Batch 10 registration — laggard profit protection and path capacity

Pin the audited Batch 8 rank-only immediate rows via the parent receipts
already used in Batch 9. Keep their entry, quantity, structure, absolute
expiry, costs and every original coordinate. For an already resolved trade,
read the first valid post-entry close at least 66 bp above its entry. A bar
starting at or after the entry is usable three minutes after its start. Act
only when that availability strictly precedes the original exit request;
sell the unchanged quantity at the next-minute open. An earlier or tied
original request has priority. A required earlier close or replacement open
that is unavailable makes the candidate exit unresolved. Existing nonfills,
nonentries and unresolved records carry forward without a new trade.

Separately calculate the retrospective best of the original exit open and
all archived opens from entry plus four minutes through the minute before
the original sale. Missing required prices leave capacity unresolved.
This path diagnostic is hindsight-only and cannot select an order, change a
payoff or satisfy candidate criteria. Report it separately on its complete
and common support. Reuse the original price index, exact fee, independent
reconstruction and the previous capacity aggregators. Compare whole and
common known economics, all five folds, largest-session sensitivity and
weekly fills and cost clearance on the original 34 weeks.

## Batch 11 registration — late-session leader spillover

Reconcile the original S1/S2 evaluation rosters with the existing CB rank
report using its pinned bytes. Keep all 3,140 symbol-sessions in each timing
scenario. Fix dated ranks 1–5 as information sources and ranks 6–20 as the
targets. Do not substitute a leader or let a target inform its own gate.

Decide 76 minutes before scheduled close. Use the first open and last close
of the fifteen consecutive minutes starting from decision minus 17 through
minus 3. Each bar is assumed available three minutes after its start and
requires a valid observed price. For the leader gate require at least four
complete leaders and their exact return median strictly above 33 bp. Keep
four- and five-leader support distinct. Fewer than four is unresolved. For
both policies consider a target only when its own full-window return is
between 0 and 33 bp inclusive. The baseline uses that target condition; the
candidate additionally applies the leader gate. Preserve missing target
paths and unresolved leader decisions on otherwise eligible targets.

Seal both sets of decisions before projecting execution prices. Keep the
decision close as maximum entry price and the minimum low of the fifteen
observed bars as the structural level. Use the existing worst-price integer
sizing and existing risk and order caps. Request one next-minute open entry,
strictly above the structure and at or below the limit. Exit at the next
open after the first available structural close, or at entry plus 30 minutes.
Preserve all missing and declined results. Apply 33 bp and use an isolated
book for each event. Audit selections, execution and aggregates with the
independent projection and payoff calculation. Report support, requests,
fills, cost clearance, common known cash and zero-event weeks. This is an
incremental leader-gate comparison on the existing discovery period.

## Batch 12 registration — first price-shock repair and volume context

Preserve the original S1/S2 rosters and their 157 sessions, 3,140 coordinates
per scenario and 34 weeks. Inspect only the three fixed times 62, 122 and
182 minutes after scheduled opening. At decision D, use five valid bars
starting D−12 through D−8 for the shock and five starting D−7 through D−3
for repair. Each bar is available three minutes after its start. Require a
shock return at or below −66 bp and the latest repair close to recover at
least one third of the price decline while remaining below the shock open.
Use the first observed qualifying price event. Record earlier unknown clocks
even when a later known event qualifies. With no observed event and one or
more unknown clocks, keep the symbol-day unresolved.

At that same first price event, the comparison arm also requires the five
shock bars' mean stored volume to be at least twice the preceding 25 bars'
mean. Read only volume for this comparison. A missing value or zero reference
mean is unresolved. Retain observed zero individual volumes. Neither arm
selects a later event after the first price event because of the volume test
or because an order did not fill. Stored volume describes relative activity.

Use the minimum low of the ten bars as structure and the latest close as
entry limit. Reuse worst-price integer sizing, fixed risk and order caps,
next-minute open strictly above structure and at most the limit, one attempt,
the next open after a structural close or entry plus 30 minutes, and 33 bp.
Seal both scenarios' price and volume choices before execution projection.
Reuse the original read-only CC/CX index from the pinned EI publication and
its saved audit. Authenticate archive framing and project prices and volumes
separately. Independently reconstruct choices, payoffs, support and weekly
aggregates, keeping no trade, missing data and uncertain payoffs distinct.

## Batch 13 registration — opening breadth and late-session continuation

Retain each original S1/S2 roster and all 157 sessions, 3,140 coordinates
and 34 weeks. Test whether broad opening strength adds information to
same-stock late momentum. Gao et al. study the first and last half-hour
returns of the US market ETF, measuring the first return from the previous
close. This experiment uses Korean stock opening prices and continuous-session
exits before the closing auction.
[Gao et al.](https://doi.org/10.1016/j.jfineco.2018.05.009)
[KRX closing call auction](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T2.jsp)

Decide 46 minutes before scheduled close. Both arms consider a member only
when the return from the first open to the last close of the 15 consecutive
bars starting D−17 through D−3 is strictly positive. Missing or invalid bars
make the late condition unknown. The baseline uses that late condition alone.
The candidate also requires a leave-one-out median opening return strictly
above 33 bp, computed from at least 10 complete peer endpoint pairs among the
other 19 roster members. Each opening return uses the first minute's open
and opening +29 minute's close. Both endpoints require valid observed OHLC
and the existing three-minute availability lag. Do not fill missing peers
or substitute a previous close. Keep the own endpoints, peer count, median
and unknown state auditable separately.
Apply the peer gate only to a known late-positive opportunity. When the late
condition is unknown both policies retain that state, while the independent
opening evidence is still reported for the coordinate.

Seal both full scenario plans before projecting outcomes. Freeze the last
observed close as limit and the 15-bar minimum low as structure. Reuse the
worst-price integer risk and notional caps. Attempt only the next-minute
open, above structure and at or below the limit. Sell at the next open
after the first available structural close, or at entry plus 30 minutes.
On standard days those entry and expiry clocks are 14:45 and 15:15 KST.
Apply 33 bp, retaining known nontrades and unresolved outcomes distinctly.

Reuse the pinned earlier price index, sealed dated rosters and independent
payoff replay. Report original, common known and matched-closed denominators,
opening gate coverage, pre-entry opportunities and trades per original week,
cost-clearance rates, five chronological folds and the largest positive
session removal. No new collection or change in cost, capital or risk limits.

## Batch 14 registration — holding time after the opening-laggard entry

Reuse the audited Batch 8 rank-only immediate ledger and its original S1/S2
rosters, 157 sessions, 3,140 coordinates, 34 weeks and five folds. Keep the
exact original 30-minute policy as the control. Add two policies with maximum
holding times of 60 and 90 minutes after the same modeled entry. Preserve the
original instrument, entry clock and price, structure, quantity, capital and
risk limits, 33 bp round-trip cost, and every earlier unresolved or nonfilled
outcome. No replacement entries, reentry or new cash reservation.

Retain a structural exit already executed before the original 30-minute
expiry. For an original time exit, continue with the same structural level
and use the first available close at or below that level to request the next
minute's open. The first newly needed close begins at entry plus 26 minutes,
is available at entry plus 29, and can trigger an exit at the old 30-minute
expiry open. Use only the unchanged prefix from the audited parent. If the
first structural exit would be at the new cap, the time exit uses that same
open. Each extended cap is a fixed entry-relative open, at opening plus 123
or 153 minutes, respectively. Read no future path to decide whether to extend.
An unknown earlier required close or requested open leaves the extended
outcome unresolved. The original exit receipt is never a replacement quote.

Authenticate the pinned Batch 8 receipts and reuse the pinned read-only price
index. Reconstruct both extended exits and the exact unchanged entry and cost
independently. Report all original coordinates, common-known outcomes and
common closed trades, incremental effects on original time exits, all weeks
including zeroes, folds, and net cash after removing the largest positive
session. The event ledger does not measure concurrent account performance.

## Batch 15 registration — opening-laggard path at the original decision

Use the audited Batch 14 60-minute policy as the control. Retain the
Batch 8 rank-only entry, original S1/S2 rosters, integer quantities,
structure, exact entry and exit prices, 33 bp round-trip cost, and all
157 sessions, 3,140 coordinates per scenario, 34 weeks and five folds.
The comparison changes only entry admission at the original decision.

At opening plus 62 minutes, classify original rank-selected, order-ready
members using the first minute's open and closes of the minutes starting
at opening plus 29 and 59. Each price requires valid observed OHLC and
availability at minute start plus three minutes. Reconcile the final close
with the sealed limit and the first-to-final return with the sealed negative
ranking return. The rebound arm retains a ready order only if the final
close is strictly above the middle close. The weak arm retains it only if
the final close is at or below the middle close. A missing required price
leaves both path decisions unresolved. Keep prior nonselected, unready and
unresolved entry decisions in both arms. On a known path, retain the
original payoff state, including any unresolved outcome, in the matching
arm. The opposite arm is a known nontrade. There is no new entry, delay
or replacement.

Authenticate the original Batch 8 selection files and reuse the audited
read-only price index. Seal both scenario path decisions before opening the
Batch 14 payoff rows. Project the exact prior 60-minute outcomes for the
retained path and independently rebuild the decisions and matched economics.
Report all original weeks, path support and missingness, whole policies,
common-known opportunities and matched-closed trades, exact net returns and
cash, cost clearance, folds and largest positive session sensitivity. The
two path arms are separate policies on the same discovery population.

## Batch 16 registration — relative weakness among the weak path

Use the audited Batch 15 weak-or-flat 60-minute policy as the sole control.
Retain the exact orders, fill and exit clocks, structure, quantity, prices,
33 bp, original S1/S2 rosters, 157 sessions, 3,140 coordinates, 34 weeks and
five chronological folds. In each scenario, only original order-ready
decisions classified as weak by its sealed Batch 15 path are eligible.
Weak means the final close was at or below the middle close for a member
already selected by the original negative one-hour rank.

At opening plus 62 minutes compare the target's return from the close of
the minute starting at opening plus 29 to the close of the minute starting
at opening plus 59 with the median of the same returns among other original
members on that day. Exclude the target from its own peer median. Require
valid observed OHLC and minute start plus three-minute availability at
both endpoints. Reconcile the target's endpoints with the sealed Batch 15
path. Require at least eight complete peer pairs among the other nineteen.
The weaker arm retains the order when the target is strictly below the
peer median. The other arm retains the order when at or above it. Missing
required prices or quorum are unresolved. A known opposite classification
is a known nontrade, and original ineligible rows retain their prior state.

Authenticate both earlier scenario seals and the read-only price index.
Seal both new decisions before accessing the prior payoff rows. Independently
reconstruct observed peer support and the exact retained 60-minute payoffs.
Report original and common-known coordinates, matched closed trades, missing
support, all weeks including zeroes, exact cash and returns, five folds, and
cash after excluding the largest positive session and the largest positive
symbol. The arms are separate policies on the same discovery population.

## Batch 17 registration — one retry after an initial open above the limit

Use the audited Batch 15 weak-or-flat policy as the control. Preserve all
original first attempts and all original fills, the integer sizing, limit
and structure, 33 bp, absolute expiry, both S1/S2 rosters, 157 sessions,
3,140 coordinates per scenario, 34 weeks and five chronological folds.
Only original order-ready weak decisions with positive frozen quantity can
receive an additional request. Classify from the original entry plan and
sealed path, without reading earlier payoff rows.

After the original open at opening plus 63 minutes, make a second decision
at opening plus 67. Use the observed minutes starting at offsets 63 and 64,
each available at start plus three minutes. Require the first open strictly
above the original limit and both closes strictly above the original stop.
Retain an observed original fill or open at or below the stop, zero quantity,
and original noneligible coordinates. An unavailable required observation
leaves the retry path unresolved. An observed above-limit open and two
observed closes with either at or below the stop yields no retry.

Seal both scenario retry decisions before reading the prior economic rows
or projecting prices from opening plus 68 onward. Authenticate and reconcile
the first attempt and quantity. For an eligible retry, attempt only the
open at offset 68, above structure and at or below the original limit,
for the same quantity. Exit at the next open after the first available close
at or below the stop, or the original absolute expiry open at offset 123.
The expiry open has priority on a tie. No third attempt or extra fill on
an already filled coordinate.

Reconstruct decisions and cash independently. Report initial and added
requests separately, added fills and outcomes, all original weeks, common
known and matched closed outcomes, folds, and the largest positive session
and symbol removals. This is a simulated event ledger with the existing
cash and risk envelope.

## Batch 18 registration — opening strength while peers decline

Use the original S1/S2 rosters, 157 sessions, 3,140 coordinates per
scenario, 34 weeks and five chronological folds. At opening plus 62
minutes both policies consider a member only if its return from the
first minute open to the close of the minute starting at offset 59
is at least 66 bp and that final close is at or above the close at
offset 29. The own rule uses three observed prices. Missing required
own prices leave both decisions unknown. Do not use the earlier
laggard rank or realized outcome as an admission input.

The candidate additionally requires a strictly negative median return
among other original members from the offset 29 close to the offset 59
close. Exclude the target and require at least eight valid peer pairs.
An unknown peer condition is unresolved for an own-eligible target.
An observed nonnegative median is a known nontrade in the gated policy.
Use valid observed OHLC available at minute start plus three minutes.

For an eligible order, freeze the offset 59 close as limit and the
minimum low across all fifteen bars starting at offsets 45 through 59
as structure. Missing structure is unresolved when required. Equal
structure and limit means no order. Use the existing worst-price
integer sizing and fixed risk and nominal caps. Attempt only the
offset 63 open, strictly above structure and at or below the limit.
Exit at the next open after the first available structural close or
at the offset 123 open, which has priority on a tie. Retain 33 bp
and no additional attempt.

Reuse the authenticated original rosters and read-only price index.
Seal both scenarios before projecting execution prices. Independently
reconstruct price support, peer decisions and exact payoffs. Report
original, common known and matched closed support, all weeks, five
folds, and net cash after removing the top positive session and symbol.
