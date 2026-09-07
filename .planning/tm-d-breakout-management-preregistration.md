# Trade Management Task D — pre-registration: what does *managing* a fixed entry actually do?

**Status: pre-registration. Written before any data was loaded for this
question.** Committed first, run second, per the `sr-u` / `sr-aa`
precedent.

---

## 1. Why this exists, and why it is not another signal search

An audit of `runs/experiments.jsonl` on 2026-09-07 counted what this
project's 129 selection trials have actually been:

| strategy_id | runs |
|---|---|
| `ensemble-momentum` | 846 |
| `single-lookback-momentum` | 280 |
| `hourly_momentum` | 192 |
| `ensemble-momentum-configuration-c` | 154 |
| 20 others | 411 |

The top four are 1,472 of 1,883 runs — **78%** — and all four are
momentum-formula variants. More importantly, read the full list of
`strategy_id`s: momentum, mean-reversion, funding-extremity, obv-trend,
macro-trend, vwap-reversion, ofi-momentum. **Every one asks the same
question: which formula predicts direction?**

**Not one asks how to manage a position once it is open.**
`confluence-hedge` (2 runs) is the closest, and Task C's own result
document records that what it tested was not the hypothesis that had
been described — a parameter-free exit pinned its holding period to
about one hour, and "a one-hour hedge cannot express a pullback trade".

The operator has asked repeatedly for the trader's version of this —
add on strength, take partial profit and hold the rest, respond as
conditions change — and what came back each time was a signal search
wearing a trader's vocabulary. That is the gap this task addresses.

### What that means for `N`

`N` is not a property of the project. It is a property of the pair
(data window, search history): *how many configurations did I look at on
this data before choosing the one I am reporting?*

The 129 trials searched the space of **direction-predicting formulas**.
This task fixes the entry rule in advance, from published literature,
and searches the space of **management policies** — a space those 129
trials never entered.

So `N` for this question starts at 1 and counts up as policies are
evaluated. **The guard that keeps this honest rather than a loophole is
that the entry rule and the complete policy list are written here,
before any data access, and neither is extended after seeing results.**
Adding a policy later means a new pre-registration and a new `N`.

**What this explicitly does not claim**: that the knowledge from those
129 trials is gone. It is not. We know every price and momentum IC is
negative at the hour scale (S11), that intraday reversion dominates
(S13), that order flow is orthogonal to price (S11), that 2021 dominates
per-position statistics (S13/S16). That knowledge is real and it informs
this design. Resetting a counter does not unlearn it — which is exactly
why the entry rule here is taken from outside rather than chosen by us.

---

## 2. The entry rule, fixed in advance and not ours

**Larry Williams' volatility breakout**, published in the 1970s, in its
conventional form:

```
Range(d)   = High(d-1) - Low(d-1)
Trigger(d) = Open(d) + k * Range(d)        (long)
             Open(d) - k * Range(d)        (short)
k          = 0.5
Entry      = at Trigger, when price first touches it during day d
Baseline exit = Close(d)  (the day's final 1m bar)
```

**Signal time and fill time are different, and both are pinned.** Task C
published `+45` that was really `−97` because a signal-time book was
reported as an execution record, so this is not a formality:

| Event | Signal bar | Fill |
|---|---|---|
| Entry | the 1m bar whose high (long) / low (short) first touches `Trigger(d)` | the **next** 1m bar's open × (1 ± `SLIPPAGE_BPS`) |
| Baseline time exit | day `d`'s final 1m bar (23:59 UTC) | the next 1m bar's open × (1 ± `SLIPPAGE_BPS`) |
| Stop / trailing stop | the 1m bar that touches the stop level | the next 1m bar's open × (1 ± `SLIPPAGE_BPS`) |
| Scale-out target | the 1m bar that touches `+1R` | the next 1m bar's open × (1 ± `SLIPPAGE_BPS`) |
| Pyramid add | the 1m bar that touches the level | the next 1m bar's open × (1 ± `SLIPPAGE_BPS`) |

### Why every management event fills one bar late, and why that is the
### right choice rather than a limitation

An earlier draft of this document specified level-triggered fills *on
the touching bar*, at the level plus slippage. **That describes a stop
order, and this project's backtest engine has no stop-order fill
model.** `backtest.fill.simulate_fill` offers exactly two contracts,
both filling on the bar *after* the signal:

- `GUARDED_MARKET` — next bar's open, adjusted by `slippage_bps`;
- `LIMIT` — next bar, at the limit price or better, **no slippage**,
  because a limit order's whole point is a price guarantee.

A stop is neither: unlike a limit it fills at its level *or worse*, and
unlike a market order it only fills when the level is touched.

**Building a same-bar executor for this task was considered and
rejected.** Two reasons, and the second is the one that decides it:

1. `fill.py`'s no-same-candle rule is a **structural look-ahead guard**,
   not a convenience. Carving an exception for the one task that would
   benefit from it weakens a guard this project relies on everywhere
   else.
2. **The one-bar lag is conservative in every direction it can act.** A
   breakout entry filled at the next open is worse when the break is
   fast. A stop filled at the next open is worse when the move against
   you continues. A scale-out target filled at the next open is worse
   when price retraces. A pyramid add filled at the next open is worse
   when the advance continues. Every management event this task studies
   is *penalised*, never flattered, by the existing contract.

   Building new machinery that would make the numbers better is
   optimising the measurement apparatus in the flattering direction,
   which is the failure this project keeps recording. **The apparatus
   stays as it is and the lag is disclosed as a known, one-sided cost.**

So all six policies are expressed entirely in `GUARDED_MARKET` intents
through the unmodified `run_backtest` / `simulate_fill` path. **No
change to the backtest engine is required by this task, and none may be
made for it.**

All P&L is rebuilt from real `Fill` objects via
`leg_manager.replay_fills`, never from the prices a policy *saw when
deciding*.

**`k = 0.5` is fixed and will not be swept.** Secondary sources give a
0.3–0.7 usable band with 0.5 as the most common convention; 0.5 is taken
because it is the stated convention, not because it was tested here.
Sweeping `k` would convert this from a management study into a signal
search and would put us straight back into the `N` problem this design
exists to escape.

### Verified against the reference implementation, not just summaries

Two web pages describing the rule were blocked by bot verification, so
the specification was instead checked against the **source code** of
`sharebook-kr/larry_simple` — the crypto-lineage implementation the
practitioner sources point at — read through the authenticated GitHub
API rather than the rendered page. It confirms every element:

```python
# larry_multi.py, TargetPriceWorker.run
low, high, last, volume = pykorbit.get_market_detail(coin_name)
target = last + (high - low) * 0.5
```

```python
# main.py — entry is a plain threshold cross, exit is a clock time
if self.cur_btc_price is not None and self.cur_btc_price > self.target:
    self.buy(krw_balance)
...
elif str_now == sell_target:
    self.try_sell(balances)
```

- `k = 0.5` — **literal in the code**, not a swept parameter
- range = previous session's high − low; anchor = the price at the daily
  reset, i.e. that day's open
- entry = first cross above target; exit = a fixed clock time
- **no stop loss anywhere**, and `self.buy(krw_balance)` commits the
  whole balance

Two consequences for this design, both load-bearing:

1. **P0 is a faithful reproduction** — no stop, time exit — so it is a
   real baseline rather than our guess at one. Every other policy is a
   genuine addition on top of a published rule.
2. **The reference makes the day boundary a user setting** (`timeEdit`
   in the Qt form), which is direct evidence for the overfitting surface
   the secondary sources warn about. It is fixed here for that reason.

**Still not consulted**: Larry Williams' own books. If the primary source
differs from both the practitioner summaries and this implementation,
this pre-registration is wrong about the entry and must be re-registered
rather than quietly adjusted.

**Also verified and deliberately not adopted**: `larry_multi.py` computes
a 5-day moving average (`get_ma5`) — the well-known Korean-community
"breakout + MA5 filter" variant. Reading the code shows it is
**display-only**: it is written into a table cell and never gates a buy.
Adopting it would therefore be *our* choice of filter rather than the
published rule's, which is a signal decision and out of scope here.

### `ATR`, and a unit error this document made and is correcting

**A first draft of this pre-registration set P1's stop at `2.65 * ATR`,
citing S12. That was wrong and is retracted here rather than shipped.**
S12's `2.65 ATR` was measured with `ATR(14)` computed on **1-minute
bars**, over a 60-minute maximum hold
(`s12_excursion_run.py`: `interval='1m'`, `MAX_HOLD = 60`). Reusing that
multiple on a daily-scale strategy silently changes what `ATR` means by
about two orders of magnitude — the same unit-mismatch mistake S12
itself made and documented (comparing a raw ATR figure against a
threshold denominated in `R`). Caught while pinning this section, before
any run.

So the stop is taken from the **same published tradition as the entry**
instead of borrowed across timeframes:

```text
side = +1 for a long, -1 for a short

1R  = |Entry - (Extreme(d-1) + Entry) / 2|      a positive DISTANCE
      where Extreme(d-1) = Low(d-1)  for a long
                           High(d-1) for a short

stop level   = Entry - side * 1R          (below entry long, above short)
level k hit  when  side * (Price - Entry) >= k * 1R
```

**`1R` is a positive distance, so every use of it must carry `side`.**
Writing "entry minus 1R" would put a short's stop on the *favourable*
side of entry — no protection at all, and the `1.0R` cap silently
broken. Direction is therefore expressed as `side * 1R` throughout this
document, never as a bare subtraction.

the midpoint between the previous session's extreme and the entry — the
conventional Larry Williams stop, parameter-free, defined by the same
`Range` that defines the trigger. **No number was chosen by us.**

**Every remaining `ATR` in this document is `ATR(14)`, Wilder's, on
UTC-midnight daily bars**, and only trailing stops use it.

| Property | Fixed value |
|---|---|
| Input bars | daily, aggregated from 1m on the UTC-midnight grid |
| Observation point | computed from days `d-14 … d-1` — **completed days only**, so day `d` never sees itself |
| Update cadence | recomputed once per completed day; a trail set on day `d` uses the value fixed at `d`'s open and does not move intraday |
| Trail distance | `3 * ATR(14)`, the practitioner convention for a daily-swing trail |

### Same-bar ordering, fixed so a tie cannot be resolved after the fact

Within one 1m bar, events are evaluated in this order, and this order is
**pessimistic by construction**:

1. **Stop / trailing stop** — always first. S8 §3.7 already pins
   stop-wins on a same-bar tie; a bar that touches both the stop and a
   profit level is recorded as the stop.
2. **Scale-out target**
3. **Pyramid add**
4. **Time exit**

An entry and its stop cannot both occur on the entry bar: a position
opened at `Trigger(d)` is checked for stops from the **following** bar
onward. Otherwise a single volatile bar could be read as both a fill and
an immediate stop-out, which is a fill-model artefact rather than a
market event.

### The day boundary is fixed to UTC midnight, and this is the one real
### specification risk

BTC trades 24/7, so "the day" is a choice, not a fact. Sources warn
explicitly that UTC-midnight versus exchange-local close **materially
changes results**, which makes it a live overfitting surface.

It is therefore **fixed to UTC midnight in advance** — the grid this
project already uses everywhere (CLAUDE.md records that BingX's own
daily candles sit on the UTC-midnight 86,400,000 ms grid) — and it will
**not** be swept. A sweep over the day boundary would be precisely the
overfitting the sources warn about.

### Why this entry, given what the project already knows

S13 and S11 found mean reversion at the 15–60 minute scale and this
task's own diagnostic (2026-09-07, on the spent Binance futures 1m
window) found the sign flips at a one-day hold: 9 of 9 cells positive
across a 3×3 lookback × threshold grid, versus 20 of 24 negative at
15–60 minutes. A daily-scale breakout with a same-day exit sits on the
side of that flip where continuation appeared, rather than against it.

**That diagnostic is not evidence for this strategy.** It measured
direction persistence after volatility expansion, with no entry rule, no
stop and no sizing; its own net Sharpe was 0.333 against the window's
0.623 detection floor and its profit factor 1.062. It is the reason this
horizon was chosen over 15 minutes, and nothing more.

---

## 3. The policies. Six, named and complete, and no others

Every policy shares the identical entry above. They differ **only** in
management. Each is fully determined — no policy has a parameter that
will be chosen after looking at results.

| # | Policy | Specification |
|---|---|---|
| **P0** | **Baseline** | Full size at trigger. Exit at `Close(d)`. **No stop** — a faithful reproduction of the reference implementation, and the control everything else is measured against. |
| **P1** | **Stop only** | P0 plus the conventional Larry Williams stop (`1R`, defined above). Exit at the time exit or the stop, whichever comes first. |
| **P2** | **Trail only** | P1, but the time exit is replaced by a `3 * ATR(14)` trailing stop that may carry past the daily close. Sources claim trailing beats partial-close for capturing large trends; this is that claim's test. |
| **P3** | **Scale out** | P1 plus: close **50% at +1R**, move the remainder's stop to entry, trail the rest at `3 * ATR(14)`. The standard practitioner structure. |
| **P4** | **Pyramid** | P1 plus: at each further `+1R` in favour, add a layer of **half the previous layer's size**, and move the whole position's stop to `newest layer entry - side * 1R`. **Maximum 3 layers** (1 + 1/2 + 1/4 = 1.75x the initial layer). **At most one layer per 1m bar** — see below. |
| **P5** | **Partial hedge** | P3, except the 50% reduction is taken by **opening an opposing leg** rather than closing half. A control with a prediction registered below. |

### P4: what is held constant, what is deliberately not

"Sizing identical across all six" means the **initial layer** is
identical: `0.5%` of equity risked against the `1R` stop distance,
equity-aware via `backtest.engine.EquityObserver`.

P4's later layers are added *by the management rule*, so its aggregate
exposure is intentionally larger than the others'. That is the thing
being studied, not a confound — but it has to be measurable separately
from a management effect, so:

- **`R` is normalised to the initial layer's planned risk for every
  policy, including P4's later layers.** A layer opened at half size
  that gains `1R` of price contributes `0.5R`. Without this, P4's
  numbers would be denominated in a different unit and `total R` would
  not compare across policies.
- **Aggregate risk cap: `1.0R` of *planned, pre-cost* risk at all
  times.** Because the stop moves to `newest layer entry - side * 1R`
  with each add, the position's planned worst case never exceeds one
  initial-layer risk unit — that is what "raise the stop with each add"
  buys, and it is stated as a cap rather than left as a hoped-for
  consequence.

  **The cap is on planned risk, not on realised net `R`, and the
  distinction is load-bearing.** `R` is defined above as net of fees and
  slippage, so an ordinary P4 stop-out will realise *worse* than
  `-1.0R` — the stop level plus adverse slippage plus two legs of fees.
  Voiding those would delete exactly the losing tail from P4's sample
  and bias the comparison in its favour.

  With next-bar-open fills the realised loss on a stop-out is **expected**
  to exceed `1.0R` — the move that triggered the stop usually continues
  into the next bar's open. That is a real market cost, not a defect, so
  it cannot be the void test.

  **The void test is instead an identity on the fill itself**, evaluated
  per stop-triggered exit and fully decidable:

  ```text
  fill_price == next_bar.open * (1 +/- SLIPPAGE_BPS / 10000)
  ```

  Any deviation means the executor did something other than the
  contract, which can only be an implementation bug. **A run containing
  one is void.** This is checkable directly against `simulate_fill`
  rather than against a market judgement, which is what makes it a
  guard rather than an opinion.

  Two things are **reported, never voided**, because they are real:

  - **realised excursion beyond `1.0R`** on stop-outs — the cost of the
    one-bar lag, and a number worth seeing per policy;
  - a **timestamp gap** (bars missing from the series; this window has
    one known, `[2019-09-08T19:00Z, 2019-09-08T19:01Z)`) — flagged on
    the episode, because the intervening path is unobserved.

  Reported alongside: the **net** loss distribution of P4 stop-outs, so
  the fee-and-slippage cost of pyramiding's more frequent stop
  adjustments is visible rather than buried inside `total R`.
- P4 reports **peak aggregate exposure** alongside its returns, so an
  exposure effect cannot be silently read as a management effect.

### P4: one layer per bar, and why not two

A single 1m bar can span `+1R` and `+2R` at once. Whether that adds one
layer or two changes aggregate exposure and `total R`, so it is fixed
here rather than left to the implementation:

**At most one layer is added per 1m bar**, at the **smallest un-crossed
`R` multiple** the bar reached — `side`-aware, so for a short the `+1R`
level is a *lower* price than `+2R` and is still taken first. Filled on
the **next** bar per the contract above. The stop moves to
`newest layer entry - side * 1R` immediately, and the next level is only
eligible from the **following** bar.

Adding two layers on one bar would require assuming the price visited
`+1R` before `+2R` within that bar — a path assumption 1m OHLC cannot
support. It is the same reasoning that stops a position from being
stopped out on its own entry bar: where the intrabar path is unknown,
the registration takes the conservative reading rather than the
flattering one.

### P5: the hedge leg's full lifecycle

Left undefined, P5 is not reproducible and its comparison with P3 is
meaningless. So:

| Property | Fixed value |
|---|---|
| Opened | when P3 would close 50%: at `+1R`, same 1m bar, same price, plus adverse slippage |
| Size | exactly 50% of the current position — a full offset of the fraction P3 would have closed |
| Direction | opposite to the core leg |
| Its own stop | **none.** Its purpose is to offset, and giving it a stop would make it a second strategy |
| Closed | at whichever comes first: (a) the core leg's exit, at which point **both legs close on the same bar**; (b) the core leg's trailing stop being hit |
| Fees | `FEE_BPS` charged on **both** legs, on open and on close — this is the cost the prediction is about |
| Funding | not modelled; the run is on a spent window where funding was not collected for this period. **Disclosed: this understates P5's real cost**, so the prediction that P5 loses to P3 is, if anything, conservative |
| Accounting | the hedge leg is part of the same **trade episode** as its core, and its P&L is included in that episode's `total R` |

Holding the initial-layer sizing constant is what makes this a
management comparison rather than a sizing comparison.

### P5 exists to be refuted, and the prediction is recorded now

Research into hedging (§8) is unambiguous: **a hedge that offsets an
existing position is economically equivalent to being flat, plus a
second spread, plus double margin, plus financing on both legs.** The
defensible uses are all cases where closing is impossible or costly —
tax events, delivery obligations, illiquid contracts. None applies to
one symbol on one venue in hedge mode.

So on this venue a partial hedge **is** a partial exit, priced higher.

**Registered prediction: P5 will underperform P3 by approximately the
extra round-trip cost on the hedged fraction, and by nothing else.** It
is run anyway because the operator asked for it specifically, because a
measured refutation is worth more than an argued one, and because if it
*doesn't* behave that way, something in this project's cost model is
wrong and that is worth knowing. This also gives Task C's negative
result a mechanism rather than leaving it as an unexplained loss.

### What is deliberately NOT in this list

- **No entry-signal variants.** Not a second `k`, not a different range
  definition, not a filter. The entry is the fixed constant.
- **No regime filter.** S10 measured the structure axis as carrying no
  information; adding one would be a new signal search.
- **No parameter sweeps within a policy.** The stop is the published
  Larry Williams midpoint rule (no number of ours); the trail is
  `3 * ATR(14)` daily, the practitioner convention. Both fixed here.
- **No scenario tree.** The operator asked for "predict several
  scenarios and respond" — P3, P4 and P5 are the testable core of that
  idea. A branching decision tree with more free choices per branch is a
  larger `N` for the same data, and is out of scope until a simple
  version has been measured.

---

## 4. Data, costs, and what this run is and is not

| | |
|---|---|
| Window | Binance futures BTCUSDT 1m, 2019-09-08 → 2026-08-25, 3,661,780 bars, 6.96 years |
| Status of that window | **Spent.** CLAUDE.md closed it to *selection* while keeping it open for reproduction, diagnosis and infrastructure testing. |
| Daily bars | Aggregated from 1m on the UTC-midnight grid; intraday path from the 1m bars, so stops and scale-outs trigger on real intra-day prices rather than daily OHLC guesses |
| Known gaps | 1 (`[2019-09-08T19:00Z, 2019-09-08T19:01Z)`), verified fail-closed before loading |
| Costs | `FEE_BPS = 5`, `SLIPPAGE_BPS = 1` — this project's own measured constants, unchanged |
| Execution | `GUARDED_MARKET` only, per the standing policy exclusion on `fill.py`'s optimistic limit model |
| Sizing | 0.5% equity risk per trade, equity-aware, identical across policies |

**This run cannot produce a pass — for a procedural reason, not a
mathematical one.** The window is spent, so a result from it is not
admissible as evidence for promotion, whatever it shows. That
restriction stands on its own.

What was *not* true, and is corrected here: an earlier draft said the
result "will fail DSR whatever it shows". It will not, necessarily. A
high `N` raises the DSR-0.95 requirement to roughly 4.0 annualized
Sharpe; it does not forbid clearing it. **DSR is computed and reported
either way**, against both `N`s (see below). A result that cleared it
would be genuinely interesting — and still would not be a pass here,
because of the procedural restriction above.

**What it can produce** is the answer to a question nobody has asked:
*given a fixed entry, how much does management change the outcome, and
in which direction?* That answer determines whether a Phase 2 holdout
access is worth requesting at all — and Phase 2 is a **separate
document, separate human approval**, not an option this registration
grants.

---

## 5. What gets reported, and the criteria — pinned now

Per CLAUDE.md's rule that criteria are "pinned before access, not chosen
after", against the file as of 2026-09-07.

### Reported for every policy, pass or fail

Total return, **total R**, mean R per trade, trade count, win rate,
profit factor, max drawdown, Sharpe (net, annualized, daily-resampled),
Sortino, Calmar, expectancy, MAE/MFE, MFE capture rate, turnover, order
rate, and **per-year results** — CLAUDE.md requires the number of
positive years beside any pooled statistic.

**Total R is the headline comparison, not win rate.** Sources are
explicit that scale-out raises win rate while capping the right tail
where trend-following edges live, so a win-rate comparison would rank
P3 above P0 for the wrong reason. This is registered now so the ranking
statistic cannot be chosen after seeing which policy it favours.

### Gate A — would this survive being traded? Evaluated first.

| Criterion | Threshold |
|---|---|
| Max drawdown | **≤ 20%** |
| Profit factor | ≥ 1.3 |
| Net expectancy | **mean R per episode > 0** |
| Trade count | ≥ 100 episodes (the frequency-scaled floor at 2,543 evaluated days) |

**Drawdown is pinned at a single 20%, not the Eligibility Bar's 20–25%
band.** A range is a judgement the Bar leaves open; a pre-registration
has to remove it, or a 22% result gets adjudicated after the fact. 20%
is the stricter end, chosen because this is leveraged futures.

**Costs are applied exactly once, and `R` is net.** `Fill.fee` is
tracked separately from `ClosedTrade.realized_pnl`, and slippage is
already inside `fill_price` — so computing `R` from `realized_pnl` gives
a **gross-of-fee** number while computing it from equity gives a net
one. Deciding after the fact is how a round trip gets deducted twice, or
zero times.

**Fixed here: every `R` in this task is computed from the equity curve,
i.e. net of both fees and slippage.** Gate A's threshold is therefore a
plain `> 0`, with no further 12 bps subtraction — the 12 bps is already
in the number.

**A policy failing Gate A is reported as failing, and no statistical
result is quoted in its favour.** This ordering is deliberate: the
scalping arc repeatedly produced high PSR figures on runs that were
already cost-disqualified.

### Gate B — could we tell this from luck? Evaluated second, and expected to fail.

Detection floor on this window is **0.623** annualized Sharpe. PSR and
DSR reported via `research/retrospective.py` (never a second
implementation — the S16 defect). `N` for this question is **6**, the
number of policies here; the project-level `N` is also reported, and the
larger of the two governs any claim.

At `N = 6` the DSR-0.95 requirement is roughly 2.2 annualized Sharpe;
at the project-level `N` it is roughly 4.0. Neither is expected to be
cleared — *expected*, not *precluded*. **Reporting both anyway is the
point** —
the comparison between policies is informative even when no policy is
individually significant, because they share an entry, a window and a
cost model, and differ only in the thing being studied.

### Significance discipline

**The unit of observation is the trade episode**, fixed here: one
episode runs from the initial entry to the exit of its final leg, and
every scale-out tranche, pyramid layer and P5 hedge leg belongs to the
episode that spawned it. Episodes, not legs and not entries, are what
significance figures are computed over — otherwise P4 would appear to
have three times the sample size of P0 for doing the same thing once.

**Episodes are non-overlapping by construction, because a trigger that
fires while an episode is open is skipped.** Not added to, and not
opened as a parallel episode. P0/P1/P3 exit within the day so this
rarely binds; P2 and P4 may carry for days, and on those days their
trigger is simply not taken.

That choice has a real cost and it is disclosed rather than hidden: **a
carrying policy takes fewer entries than P0 on the same data**, so trade
counts will differ between policies and `total R` is compared per
policy, not per trade. The alternative — allowing parallel episodes —
would make the significance figures depend on how often a policy happens
to overlap itself, which is S14's exact lesson, where t = 7–8 collapsed
to 1.5–2.6 once overlapping windows were deduplicated.

Should any overlap survive implementation, the figure will be
deduplicated before any t-statistic, p-value or standard error is
reported, or labelled explicitly as uncorrected.

---

## 6. Stopping rule

**If every policy fails Gate A**: the volatility-breakout entry does not
work on this venue at this scale, and that is the finding. The permitted
responses are to accept it, or to specify a *different* entry from
literature in a new pre-registration. **Not permitted: adjusting `k`,
the day boundary, the ATR multiples, or the policy list and re-running.**

**If some policy clears Gate A**: report which, by how much, and against
P0. That result is a *development* finding on a spent window. Whether it
justifies spending the Binance spot 1m holdout is a separate human
decision, in a separate document, and is explicitly not granted here.

**If P5 does not behave as predicted in §3**: that is a finding about
this project's cost model, and it gets its own investigation before
anything else in this document is trusted.

---

## 7. Why this design should not repeat the arc's five known errors

CLAUDE.md records five instances of concluding about a domain from one
parameter setting. This design's guard is structural rather than
promissory: the six policies **are** the sweep, over the axis being
studied, fixed in advance, with the other axes held constant by
construction.

The other recorded failure shapes, and how each is handled here:

| Shape | Handling |
|---|---|
| overlapping windows treated as independent | positions are non-overlapping by construction; carried positions deduplicated before any significance figure |
| a statistic from intent rather than fills | all P&L rebuilt from real `Fill`s via `leg_manager.replay_fills`, which Task C had to build after publishing +45 that was really −97 |
| a guard nobody proved can fail | the gap check, the cost gate and the Gate A ordering each get a deliberate failure test before the real run |
| look-ahead through a global percentile | no percentile conditioning in any policy; ATR is trailing by construction |
| reported figures that were never computed | every number in the result document comes from the run's own output |

---

## 8. Sources

Practitioner sources, used for the entry specification and the
management conventions. Named here rather than in the result document,
so that what was read *before* the run is on the record.

- Larry Williams volatility breakout rules and the `k` convention:
  [QuantifiedStrategies](https://www.quantifiedstrategies.com/larry-williams-volatility-strategy/),
  [MQL5 Market Secrets Part 5](https://www.mql5.com/en/articles/20745),
  [WH SelfInvest](https://www.whselfinvest.com/en-lu/trading-platform/free-trading-strategies/tradingsystem/56-volatility-break-out-larry-williams-free)
- Crypto lineage of the same rule, and the reference implementation this
  specification was verified against line by line (read via the GitHub
  API; the rendered page and `quantifiedstrategies.com` were both blocked
  by bot verification):
  [sharebook-kr/larry_simple](https://github.com/sharebook-kr/larry_simple)
- Pyramiding rules (add on strength, smaller adds, rising stop; the
  volatility-increment add trigger):
  [Concretum Group](https://concretumgroup.com/position-sizing-in-trend-following-comparing-volatility-targeting-volatility-parity-and-pyramiding/),
  [TradersPost](https://blog.traderspost.io/article/pyramiding-trading-strategies-guide)
- Scale-out geometry, and the warning that it raises win rate while
  capping total R:
  [QuantStrategy.io](https://quantstrategy.io/blog/backtesting-partial-close-strategies-does-scaling-out/),
  [Metriclan](https://www.metriclan.com/blog/partial-profit-taking)
- Hedging as economically equivalent to flat plus costs:
  [Capital.com](https://capital.com/en-int/analysis/what-is-hedging-mode-and-how-to-use-it-in-trading),
  [TradersPost](https://blog.traderspost.io/article/trading-strategies-navigating-opposite-positions-simultaneously)
- Retail day-trading base rates, for calibration on how rarely this
  works at all:
  [Financial Analysts Journal (2003)](https://www.tandfonline.com/doi/abs/10.2469/faj.v59.n6.2578),
  [QuantifiedStrategies day-trading statistics](https://www.quantifiedstrategies.com/day-trading-statistics/)

**Source-quality note, stated once and applying to all of the above**:
these are practitioner and vendor pages, not peer-reviewed work. They
are used for *what the conventional rule is* — a question about
convention, which they can answer — and not for *whether it is
profitable*, which they cannot. Every profitability claim on those pages
is treated as unverified marketing until this project measures it.
