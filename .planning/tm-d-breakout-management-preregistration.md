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
Baseline exit = Close(d)  (the day's final bar)
```

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
| **P0** | **Baseline** | Full size at trigger. Exit at `Close(d)`. No stop. The literature default, and the control everything else is measured against. |
| **P1** | **Stop only** | P0 plus a stop at `2.65 * ATR(14)` from entry, the boundary S12 measured from winners' MAE p80 on this venue. Exit at close or stop, whichever first. |
| **P2** | **Trail only** | P1, but the time exit is replaced by a `3 * ATR(14)` trailing stop, allowed to carry past the daily close. Sources say trailing beats partial-close for capturing large trends; this is that claim's test. |
| **P3** | **Scale out** | P1 plus: close **50% at +1R**, move the stop on the remainder to entry, trail the rest at `3 * ATR(14)`. `1R` = the P1 stop distance. The standard practitioner structure. |
| **P4** | **Pyramid** | P1 plus: each time price advances a further `1R` in favour, add a layer at **half the previous layer's size**, and move the stop to the newest layer's entry minus `1R`. Maximum 3 layers. Follows the three conventional rules — add only on strength, each add smaller, stop rises with each add. |
| **P5** | **Partial hedge** | P3, except the 50% reduction is taken by **opening an opposing leg** rather than closing half. Included as a **control with a predicted outcome**, see below. |

**Position sizing is identical across all six** and is not a variable
here: risk `0.5%` of equity per trade against the P1 stop distance,
equity-aware via `backtest.engine.EquityObserver` (built in S15).
Holding sizing constant is what makes the comparison a management
comparison.

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
- **No parameter sweeps within a policy.** `2.65 ATR` and `3 ATR` come
  from S12's measurement and the practitioner convention respectively,
  and are fixed here.
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

**This run cannot produce a pass.** The window is spent, so any result
here is deflated against a project-level `N` in the 120s and will fail
DSR whatever it shows. That is expected and is not the point.

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
| Max drawdown | ≤ 20–25% |
| Profit factor | ≥ 1.3 |
| Net of costs | mean R per trade > 0 after 12 bps round trip |
| Trade count | ≥ 100 (the frequency-scaled floor at 2,543 evaluated days) |

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

At `N = 6` the DSR-0.95 requirement is roughly 2.2 annualized Sharpe.
Nothing is expected to clear it. **Reporting it anyway is the point** —
the comparison between policies is informative even when no policy is
individually significant, because they share an entry, a window and a
cost model, and differ only in the thing being studied.

### Significance discipline

Positions are non-overlapping by construction (at most one entry per
day, exited by the next day's open in P0/P1/P3; P2 and P4 may carry, and
**any overlapping position will be deduplicated before any t-statistic,
p-value or standard error is reported**, or the figure will be labelled
as uncorrected). This is S14's lesson, where t = 7–8 collapsed to
1.5–2.6 on correction.

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
