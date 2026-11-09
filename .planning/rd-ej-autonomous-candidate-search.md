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
