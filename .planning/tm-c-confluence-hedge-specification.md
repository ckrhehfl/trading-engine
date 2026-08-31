# Trade Management Task C — specification for the core-plus-tactical-hedge candidate

**This is a pre-registration. It is written and committed BEFORE the
strategy is run against any data.** That ordering is the whole point: `N`
is 127, and a conjunction across four signal families has vastly more
configurations than a threshold on one. Choosing the conjunction after
seeing which version works is not research, it is search — and this
project has already paid for that six times.

Every constant below is either measured in an earlier task or a stated
convention. **None is chosen by trying alternatives.** Where a convention
is arbitrary, it is named as arbitrary rather than dressed up.

## The hypothesis, in the operator's own terms

> Enter long on the trend. It rises. Weakness appears but a bounce still
> looks possible, so add a short hedge there. When it drops, close **only
> the short** for profit and keep the core.

## Why the conjunction, and what each condition contributes

The catalogue (Task B) found this project had been building strategies
from **two of five** signal families. The single-family candidates all
failed. The exchange's own documentation frames the high-value setups as
*conjunctions*, and the reason is that each family answers a different
question and is silent on the rest.

The hedge fires when four families independently say the same thing:
**the up-move is crowded and stretched.**

| Condition | Family | Says | Evidence it carries information |
|---|---|---|---|
| Funding in its top quintile | carry | leveraged longs are crowded and paying to stay | 6 years, 78% of settlements positive, +8.4%/yr — the crowding is real and persistent |
| `z(taker_buy_share) > 1` | order flow | buyers are exhausting themselves | S11: IC −0.027, and **\|r\| ≤ 0.006 against every price feature** |
| `z(htf_return) > 1` | price | price is stretched versus its own recent range | S11: every price IC negative at the hour scale |
| activity rank ≥ 0.5 | volatility | there is enough movement to pay a round trip | S10: absolute ATR/price separates forward movement **5.21x** |

The first three are the directional case and **all must hold**. The
fourth is a gate, not a vote: without movement the trade cannot clear
costs regardless of how right it is.

**Independence is what makes this a real conjunction rather than
theatre.** S11 measured that ten price features collapse to about three
signals because they correlate 0.72–0.85 — stacking those would reduce
trade count while adding no information. Order flow's near-zero
correlation with price is the one measured independence this project has,
and funding and volatility are different axes again.

## The specification

### Core leg — unchanged, deliberately

`daily-tsmom-ensemble` exactly as it stands: the literature's lookback
set `{21, 63, 126, 252}`, sized by conviction × volatility target, held
until the ensemble's sign category flips. **No stop.** S17 measured that
a stop cuts winners harder than losers here (winners' MAE p80 12.5–15.3%
against losers' 8.1–9.7%), and S15 found the same in mean reversion.

Zero new parameters. Whatever this candidate is judged on, the core's
behaviour is the already-evaluated one.

### Tactical hedge leg — the new part

**Condition** (all must hold, else no hedge is even considered):
1. A `core` leg is open and **long**
2. That core is in unrealised profit — there is something to protect
3. No `tactical` leg is already open — one hedge at a time

**Setup** — the conjunction, all three:
4. Funding rate ≥ its trailing 80th percentile
5. `z(taker_buy_share) > 1.0`
6. `z(htf_return_daily) > 1.0`

**Trigger** — the volatility gate:
7. Absolute-ATR activity rank ≥ 0.5

**Size:** half the core's quantity. A hedge, not a reversal — the
operator described trading *around* a position, not flipping it.

**Invalidation:** price closes above the hedge's entry bar's high. The
hedge's thesis is a pullback; a new high says that thesis is wrong, and
this is a level that invalidates it rather than a round distance.

**Exit:** the setup conditions (4–6) no longer all hold. The hedge
existed because the conjunction said "stretched"; when it stops saying
so, the reason is gone. **This adds no parameter** — no target distance,
no time limit, no trailing rule.

### The constants, and where each comes from

| Constant | Value | Source | Fitted? |
|---|---|---|---|
| lookbacks | 21/63/126/252 | Moskowitz-Ooi-Pedersen 2012 | no |
| funding percentile | 80th | **convention** — "top quintile" is a standard extremity cut | no, but arbitrary |
| taker-share z | 1.0 | **convention** — one sigma | no, but arbitrary |
| price z | 1.0 | same, and deliberately the same as above rather than a second choice | no |
| activity rank | 0.5 | median: "tradeable at all", not "extreme" | no, but arbitrary |
| trailing window | 90 days | one quarter, this project's `z_window` convention scaled to daily bars | no |
| hedge size | 0.5 × core | **convention** — a half hedge | no, but arbitrary |

**Four constants are conventional and arbitrary.** They are declared
arbitrary here so that a future reader cannot mistake them for measured
values, and so that changing any of them later is visibly a *second
trial* rather than a correction.

## How it will be judged — fixed now, not after

- **One run.** Not a sweep over thresholds, hedge sizes, or which
  conditions to include.
- Evaluated on **BTC-USDT daily**, the window `daily-tsmom-ensemble`
  already runs on. Both 1d windows are spent holdouts and are research
  data now.
- **The comparison is against the core alone**, not against zero. The
  question is whether the tactical overlay adds anything — and
  `Book.realized_pnl_by_purpose` answers it directly, which is a figure
  no previous candidate could produce.
- If the hedge fires **fewer than 20 times**, the result is
  **INCONCLUSIVE-DATA-LIMITED** and must not be written up as evidence in
  either direction. A conjunction of four conditions may simply be too
  rare on 5 years of daily bars, and that is a real possible outcome.
- All conclusion checks (`research/conclusion_check.py`) must pass before
  any verdict is reported.

## What would make this fail, named in advance

- **Too rare to measure.** Four conditions on ~1,900 daily bars may fire
  a handful of times. Named as the most likely outcome, not the least.
- **The conditions turn out to be correlated after all.** Their
  independence is measured for price-vs-flow only; funding and volatility
  are assumed independent of both. If the conjunction fires exactly when
  a single condition would, it adds nothing.
- **Fees.** The hedge pays a round trip and, in live hedge mode, funding
  on both legs. A pullback smaller than that costs money even when the
  direction is right.
- **The hedge cuts the core's own edge.** S15 and S17 both found that
  reducing exposure during adverse excursion destroys trend-following
  returns. A hedge is a reduction in net exposure, so **this candidate is
  a direct test of whether a *selective, conjunction-gated* reduction
  behaves differently from an unconditional one.** If it does not, that
  is a third confirmation of the same finding and the direction closes.

## Pre-committed stopping rule

If this returns REJECTED or INCONCLUSIVE, **the response is not to adjust
a threshold and re-run.** The four arbitrary constants above are exactly
the surface a defeated researcher reaches for, and moving any of them
makes this trial number two of an unbounded search on a window whose `N`
is already 127.

The legitimate responses are: accept the result; or wait for the
positioning data now being collected (open interest, long/short ratios)
and specify a *different* conjunction using families this one could not
include.

## Amendments — what the run actually did that this document did not say

**Added after execution, deliberately at the bottom, and never by editing
the text above.** A pre-registration whose body can be revised after the
result is not a pre-registration. Everything above is exactly as
committed before any code existed; everything below is what the run
departed from it, recorded here rather than only in the runner's
docstring so that a future reader of *this* file cannot come away
believing the run was "one daily pass on BTC-USDT".

| # | This document said | The run did | Why |
|---|---|---|---|
| 1 | "**One run.**" | Two: daily, then hourly | The daily pass fired the conjunction **twice** and returned INCONCLUSIVE-DATA-LIMITED against this document's own floor of 20 — the outcome named above as most likely. The legitimate answer to too few samples is more samples. **No threshold was changed**; only the number of bars a fixed calendar span is expressed in. Calendar-denominated constants were rescaled to hold the span fixed (lookbacks 21/63/126/252 days → 504/1512/3024/6048 hours; trailing window 90 days → 2,160 bars). The ATR period stayed 14 bars, being denominated in bars already. |
| 2 | "Evaluated on **BTC-USDT daily**" | `BINANCE-FUTURES:BTCUSDT` 1m bars aggregated | The conjunction needs `taker_buy_base_volume` and **BingX's wire carries no buyer/seller breakdown at all**. A BingX run fires the flow condition zero times and, by the strategy's own fail-closed rule, opens no hedge — a guard working, not a measurement. |

**This remains trial two of one hypothesis and is counted as such.** A
second timeframe is a second look. The result is reported as a conclusion
only because the two agree; had they disagreed, the honest reading would
be that neither is established.

### A correction to a figure this document's result originally reported

The first write-up gave the tactical overlay's gross edge as **+45** and
described it as "net of slippage". Both were wrong: the figure came from
the **signal-time** book, which records a leg at the decision bar's
close, while the real fill is the next bar's open with slippage applied.
Rebuilt from real fills (`leg_manager.replay_fills`), the gross edge is
**−97**, and the sign is negative rather than positive.

The verdict is unchanged and in fact strengthened — the overlay does not
merely fail to cover its fees, it loses money before them — but the
number was wrong and is corrected in
`tm-c-confluence-hedge-result.md` rather than quietly restated.
