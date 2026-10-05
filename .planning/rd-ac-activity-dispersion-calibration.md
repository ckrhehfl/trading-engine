# Research Direction Task AC — size the activity portfolio before a comparison family

## Discuss

The next question in `rd-aa` is whether activity-selected, months-held cash
equities differ from an unfiltered or matched control. Before specifying that
family, CLAUDE.md requires an event-arm dispersion measurement. The operator
authorized the work on 2026-10-05, and explicitly chose to retain halted
holdings, delay sales and disclose unresolved inventory rather than filter out
future casualties. That choice applies to this study, not to live trading.

## Committed sizing protocol

`configs/research/discovery/activity-calibration-v1.json` fixes a reference
geometry before any returns are loaded: rd-aa's activity definition, a
20-slot cash-funded book, 126-session holding, and deterministic hash selection.
It is a dispersion pilot, not the comparison family or a candidate registration.
The existing `preregistrations/` schema is for walk-forward/holdout candidates;
discovery's distinct mode has no fake Eligibility Bar or folds.

The runner checks the specification against HEAD, records its content hash and
a durable discovery start, then reads the named completed scan panel. The
scan's panel metadata must match the spent era before any price query. The
separate reserved database is never used. Index dates determine sessions and
settlement; both directions of calendar/scan coverage are checked.

## Portfolio accounting

- A formation uses only that day's close and preceding non-frozen observations.
  The next open is the first possible fill. Missed entries remain cash.
- Fractional shares, no leverage, and at most 20 holdings. Each entry is capped
  at 1/20 of then-current equity, including its entry costs. Existing frozen
  holdings occupy slots; no fresh budget is invented at a rebalance.
- A due sale waits for a present, non-frozen bar. A missing or frozen price is
  marked at the last observable price without turning it into cash. The report
  counts pending-exit and stale-position sessions, delayed sales and unresolved
  terminal holdings. The last observed bar is never retroactively an exit.
- Limit-locked bars remain in eligibility. Daily-bar fills at their open are an
  unverified queue/capacity proxy and their count is disclosed. This pilot cannot
  establish executable profitability.
- Prices are adjusted for splits but not dividends. The existing per-era tax
  module dates sales using T+2 index sessions. Commission and slippage are fixed
  modeling assumptions, not estimated from the pilot's returns.

## Sizing and stopping

The sampling unit is a portfolio session, not a pool of correlated stock-days.
The pilot outputs dispersion only, with no mean performance, Sharpe, p-value,
candidate ranking or promotion result. A fixed-seed circular block bootstrap
reports three block lengths and their correction relative to iid session SE.
The largest SE gives a normal-approximation 80%-power planning effect. This is
not an exact power calculation, especially with only a few annual blocks.

The committed stop compares that daily effect with the modeled round-trip cost
amortized over the holding period, as required by the standing family-sizing
rule. If the floor is not resolvable, a comparison family is not silently run:
the measured limitation and choices go to the operator. It does not close the
activity direction or spend a holdout. Missing-symbol/type uncertainty and
stale valuations survive any favorable arithmetic.

## Verification and execution boundary

Synthetic tests check next-open timing, real cash costs, frozen baseline
exclusion, limit-locked inclusion, delayed exits, disappearing names, failed
entries, block dependence and reserved-panel refusal before any price query.
The source and specification receive CodeRabbit review before the real pilot.
Execution uses an isolated GCP code checkout and the existing read-only data;
it does not replace the collector checkout, environment, processes or schedule.

Before the PR: 220 focused tests passed (one existing skip), including the
new accounting, sizing, registration and planning-index coverage. The earlier
full-suite run and the logger mutation check are recorded in Task AB. No real
return data has been loaded for this study at this point.

Pre-run clarification: the delisted finder includes KONEX issues, and the scan
does not retain point-in-time market membership. KOSPI/KOSDAQ's common total
tax schedule is therefore an upper-bound cost model here, not an exact tax
claim for each name; KONEX's lower rate is not silently inherited. The sizing
specification states this explicitly. A zero-variance/all-cash book is refused
rather than reported as perfectly powered, and invested-session counts are
reported beside the dispersion.

CodeRabbit review found that a clean specification alone did not identify the
actual executing code. The CLI now requires a repository-root invocation and
refuses staged/unstaged/untracked changes to its six repository source inputs,
including transitive imports and package initializers. Real temporary Git
repositories test each dependency in staged and unstaged states, while an
unrelated edited file remains allowed. No real pilot has run during these fixes.
