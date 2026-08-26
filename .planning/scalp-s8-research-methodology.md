# Scalping Strategy Research Task S8 — research methodology, rebuilt

Written 2026-08-26, after `vwap-mid-reversion` (S4) and `ofi-momentum`
(S6) both returned INCONCLUSIVE with catastrophic raw drawdowns, and
after the human operator pushed back on the framing that had produced
them. This document is the corrected methodology, plus an honest record
of what was wrong before — including a load-bearing analytical error made
during the conversation that produced this document.

Grounded in real external research (sources at the end), not memory. It
does **not** propose a strategy. It defines how the next one gets built.

---

## Part 1 — What was actually wrong

Not "the statistics were too strict". Every gate this project applies
(PSR/DSR, holdout single-access, trial counting) is standard practice and
stays. The failures were upstream of the statistics.

### 1.1 The strategy was never decomposed

Practitioner literature decomposes a systematic strategy into three
separable components: **direction** (a prediction of where price goes),
**entry/exit** (whether to actually act on that prediction, and when to
leave), and **sizing** (how much). Both failed candidates fused all three
into one mechanical threshold rule — "2 standard deviations from a
20-period mean, therefore trade, therefore this size". There was no
prediction step distinct from the trigger, and no place for a decision
not to act.

### 1.2 There was no regime layer, and that is the classic blowup

`vwap-mid-reversion` is a mean-reversion strategy with no concept of
market state. The regime literature names this exact failure mode
directly: running a mean-reversion system through an emerging trend is
one of the most reliable ways to produce a large loss in a short time.
That is not a retrospective rationalisation — it is the documented,
predictable outcome of the design that was shipped.

### 1.3 "Zero fitted parameters" was applied where it does not transfer

`daily-tsmom-ensemble` legitimately has zero fitted parameters: its
21/63/126/252 lookbacks come from Moskowitz-Ooi-Pedersen (2012), a
published result validated across decades and asset classes. The
parameter values arrived from **outside** the data they were tested on.

Scalping has no equivalent canonical parameterisation. So both candidates
took a paper's *mechanism* and invented their own numbers — a 20-period
window and a 2-SD band borrowed from Bollinger Bands, an entirely
unrelated context — then described the result as "zero fitted
parameters". That is not zero-fitted; it is **unfitted**, which is worse.
An arbitrary guess with rigor theatre around it, spent on an
irreplaceable single-access holdout.

### 1.4 Single-shot holdouts produced no learning

Each attempt yielded exactly one bit of information and permanently
consumed a window. Two attempts, two spent windows, near-zero knowledge
gained about whether the underlying mechanisms work. The DSR machinery
exists precisely so that honest searching is possible — search, count
trials, deflate. Avoiding search to keep `N` low avoided the penalty and
also avoided the learning.

### 1.5 A turnover sanity check would have caught both, instantly

`vwap-mid-reversion` traded 44,344 times in 631 days (~70/day);
`ofi-momentum` 56,441 times (~89/day). Risk-control practice states the
heuristic plainly: if the bot wants to make 100 trades today, something
is broken. Neither run had any turnover ceiling, and nothing flagged the
number as anomalous during review.

### 1.6 The risk budget was derived last instead of first

Both runs backtested first and looked at drawdown afterwards. The
inverted, recommended order is to fix the maximum tolerable drawdown and
the acceptable probability of hitting it **before** any strategy logic is
written, then require everything downstream to fit inside that budget —
and to drop ideas that cannot, rather than widening the budget.

### 1.7 Fixed-reference sizing understates drawdown (already partly fixed)

`compute_position_size` sizes every trade against a fixed
`reference_equity` constant rather than real shrinking equity. This is a
known, documented backtest distortion: fixed-size backtests do not shrink
positions during drawdowns the way percentage-based sizing does, so their
drawdown statistics consistently understate real-account damage. Task S7
added an insolvency floor (the circuit-breaker half); equity-compounding
sizing remains undone.

---

## Part 2 — A real analytical error made while diagnosing this

Recorded because it changed a conclusion, and because the same mistake is
easy to repeat.

The cost analysis that produced the claim **"minutes-scale scalping is
arithmetically impossible"** compared the round-trip cost (~30bps) to the
**unconditional** median move — i.e. the move you would get entering at a
uniformly random moment. At 1 minute that median is 3.2bps, giving the
apparently decisive 9x-cost-to-move ratio.

That is the wrong comparison. A trader does not enter at random moments;
they enter when something is happening. The relevant distribution is
conditional on the entry criterion.

Measured on the real Binance USDT-M futures BTCUSDT 1m window (3,661,780
bars), bucketing by "activity" (sum of absolute 1-minute returns over the
prior 30 minutes, known at decision time):

| Holding | All moments | Top 25% | Top 10% | Top 5% | Top 1% |
|---|---|---|---|---|---|
| 15 min | 11.9bps (0.40x) | 24.3 (0.81x) | **32.8 (1.09x)** | **40.2 (1.34x)** | **62.5 (2.08x)** |
| 30 min | 16.4 (0.55x) | **32.9 (1.10x)** | **44.1 (1.47x)** | **54.2 (1.81x)** | **82.3 (2.74x)** |
| 1 hour | 22.9 (0.76x) | **45.1 (1.50x)** | **59.3 (1.98x)** | **72.3 (2.41x)** | **105.4 (3.51x)** |
| 2 hour | **32.3 (1.08x)** | **62.4 (2.08x)** | **80.9 (2.70x)** | **98.8 (3.29x)** | **142.8 (4.76x)** |

(bps, and as a multiple of the 30bps round-trip cost. Bold = clears cost.)

**What this does and does not establish**, stated precisely because the
first draft of this document overstated it and the overstatement was
caught on review.

It establishes only that **15-minute holding is not excluded on cost
grounds** once entries are restricted to elevated-activity moments: the
typical absolute move there is large enough that the round trip could be
covered. The original "arithmetically impossible" conclusion was an
artefact of the unconditional framing, and that much is genuinely
retired.

It does **not** establish that 15-minute holding is tradeable. An
absolute move is unsigned. Covering costs requires **direction**, and
this measurement says nothing about whether direction is predictable in
those moments — that is a separate question, unanswered here, and the
subject of steps 2-4 of the work order in Part 4. Nor does it account
for realised fill costs (see the slippage sensitivity immediately
below), post-cost expectancy, or out-of-sample behaviour. **"Viable" is
reserved for a candidate that has cleared signed-return evidence, a
measured win rate, real execution costs, and out-of-sample
validation.** Nothing here has.

The conditioning itself is not a data-mined coincidence: **volatility
clustering** — large moves followed by large moves — is among the most
robust empirical facts in finance (Mandelbrot 1963; Engle's ARCH, 1982,
Nobel 2003), so recent realised activity is a legitimate predictor of
near-future activity. That supports the *magnitude* conditioning only,
not any directional claim.

**But the conclusion is fragile to the slippage assumption**, and in
exactly the wrong direction: the strategy would deliberately enter during
volatile moments, which is precisely when spreads widen and depth thins.
`SLIPPAGE_BPS = 10` was calibrated against a ~4bps *typical* spread.
Sensitivity, using the same medians:

| One-way slippage | Round trip | 15m top 1% | 15m top 5% | 1h top 5% |
|---|---|---|---|---|
| 10bps (current) | 30bps | 2.08x | 1.34x | 2.41x |
| 15bps | 40bps | 1.56x | 1.01x | 1.81x |
| 20bps | 50bps | 1.25x | 0.80x | 1.45x |
| **30bps** | 70bps | **0.89x** | 0.57x | 1.03x |
| 50bps | 110bps | 0.57x | 0.37x | 0.66x |

At 30bps one-way slippage, 15-minute holding fails even in the top 1%,
while 1-2 hour holding survives. **Shorter horizons are structurally more
exposed to the slippage assumption**, so the assumption must be measured
rather than assumed before any short-horizon candidate is trusted.

### A second, smaller measurement error in the same session

The first test of the "key levels matter" hypothesis measured the
**magnitude** of the subsequent move near prior-day highs/lows and found
nothing (0.91x — slightly *less* movement). That was also the wrong
question: the hypothesis is about **directional predictability**, not
size. Re-measured directionally on the same data:

| Level | 30 min | 2 hour | 4 hour |
|---|---|---|---|
| Baseline (all moments) up-rate | 50.37% | 50.88% | 51.03% |
| Near prior-day **low** (support) | 53.05% (+2.68pp) | 54.87% (+3.99pp) | **56.15% (+5.13pp)** |
| Near prior-day **high** (resistance) | 49.08% (−1.29pp) | 48.45% (−2.44pp) | **46.91% (−4.12pp)** |

Correctly signed (support → up, resistance → down) and monotonically
increasing with horizon — a pattern a single-timeframe indicator design
cannot see.

**This is an observed association, not an established signal**, and the
distinction is load-bearing rather than pedantic. The nominal sample
counts (64k/86k) are **not** independent observations: every 1-minute
bar was used as an observation while the forward windows are 30, 120 and
240 minutes long, so consecutive observations overlap almost completely
and are heavily autocorrelated. The effective sample size is far smaller
than the nominal one, and the usual standard errors do not apply.
Separately, six comparisons were made (two levels × three horizons) with
no multiple-testing correction — a correction this project applies
rigorously elsewhere via DSR, and which was simply skipped here.

Before this is described as a signal, it needs: non-overlapping samples
or HAC/block-bootstrap inference to handle the dependence, and an
explicit multiple-testing correction across levels and horizons. Until
then it is a reason to investigate, not a result.

**Honest limit even if it survives testing**: at 4h support the median
gain is +9.2bps against a 30bps round trip — **not tradeable on its
own**.
Also note mean +0.05bps vs median +9.23bps: a left-skewed distribution,
many small gains and occasional large losses. Win rate alone would be
actively misleading here, and a stop is structural rather than optional.

---

## Part 3 — The corrected methodology

### 3.1 Hypothesis must name a mechanism

A hypothesis must be a testable conjecture that specifies a cause or an
observable preceding state, and must define its expected outcome and how
it will be verified. "Stops cluster above prior-day highs, so a sweep
through that level triggers forced liquidation and a short-term
overshoot" is a mechanism. "20-period VWAP, 2 SD" is a formula.

Practical filter: if the answer to *"who is on the other side of this
trade and why are they willing to lose?"* is unknown, it is not yet a
hypothesis.

### 3.2 Decompose: direction, entry/exit, sizing

Three separate research problems, researched separately. Most of the
work below is about **direction**; entry/exit is largely a cost and
MAE/MFE question; sizing is derived from the risk budget (3.6).

### 3.3 Regime layer, before signals

A minimum viable regime model is **two-axis**: directional state ×
volatility state. Trend-vs-range on its own is insufficient — a quiet
uptrend and a volatile news-driven rally are different environments.

- Candidate classifiers: ADX (>25 trending, <20 ranging), ATR ratio
  (current vs 20-period average; ≥1.5x expansion, ≤0.8x compression),
  return autocorrelation (positive → trending, negative → mean-reverting),
  Hurst exponent, MA slope.
- **Hysteresis is mandatory**: two thresholds per boundary (a higher one
  to enter a state, a lower one to leave it) so the label does not
  flicker at the boundary, plus a minimum dwell time.
- **Regime lag is the dominant failure mode** of retail regime systems —
  labels must be computed only from information available at bar close.
- Volatility estimator: the efficiency ladder is close-to-close <
  Parkinson < Garman-Klass < Rogers-Satchell < Yang-Zhang (~14x
  close-to-close for YZ). **But this project trades a 24/7 market with no
  overnight gap**, which is the specific problem YZ's extra terms solve,
  so Parkinson or Garman-Klass are likely sufficient here. Sources
  disagree on whether crypto "sessions" still exhibit gap-like behaviour
  — resolve empirically on our own data rather than adopting YZ by
  reputation.

### 3.4 Measure signals as signals (IC), not as strategies

Do not jump from idea to full backtest. First measure the relationship
between a candidate feature and forward returns — the information
coefficient. This is far more data-efficient and far less prone to
backtest overfitting, because a full strategy adds entry, exit and sizing
choices that each multiply the search space.

Calibration: real, usable ICs are small — **0.02-0.05 is a genuinely
useful signal**. Expect individual features to look unimpressive. The
+5.13pp directional edge measured in Part 2 is a normal-sized real effect,
not a disappointment.

### 3.5 Combine weak, uncorrelated signals — this is the core insight

Grinold's Fundamental Law: **IR ≈ IC × √breadth**, where breadth is the
number of *independent* decisions. The implication is that the goal is not
to find one strong signal but to combine many weak ones — and that
**orthogonality, not individual signal strength, is the binding
constraint**. Ten correlated moving averages are one signal with extra
steps; price structure + order flow + session + funding are genuinely
different information sources.

Known limitations to respect rather than ignore: the law systematically
**overstates** achievable IR, largely because practitioners equate breadth
with universe size instead of counting genuinely independent decisions
(Clarke et al.'s transfer coefficient exists to account for this leakage).
Treat `IC × √breadth` as an upper bound and a design principle, never as a
performance forecast.

Candidate feature categories, all computable from data already held:

| Category | Features | Source |
|---|---|---|
| Price structure | prior day/week high-low, round numbers, session open, opening range, swing points | 1m klines |
| Higher timeframe | 5m/15m/1h/4h/1d trend and location | resampled 1m |
| Order flow | taker buy/sell imbalance, volume spikes, cumulative delta | Binance 1m (Task S5) |
| Volatility | realised vol, vol-of-vol, expansion/contraction | 1m klines |
| Time | Asia/Europe/US session, hour, weekday | timestamps |
| Derivatives | funding rate, basis | 6,199 funding rows |
| Macro | real yield, S&P 500, dollar index | FRED cache |

### 3.6 Risk budget first, sizing derived

Fix the constraint before writing strategy logic:

1. **Ruin threshold.** Recovery is asymmetric (a 50% drawdown needs a
   100% gain; 25% needs 33%). Behavioural ruin also precedes mathematical
   ruin — most traders abandon a system well before it is mathematically
   dead. A 25-30% threshold is defensible; 50% is too late.
2. **Acceptable risk of ruin.** Institutional practice is **<1%**;
   **>5% means reduce size before trading live**.
3. **Solve for risk per trade — using the formula that matches the
   sizing model, which is not optional.** The two differ, and picking
   the wrong one silently understates risk:
   - **Fixed dollar risk per trade** (this codebase today, via
     `compute_position_size`'s fixed `reference_equity`): risk units are
     additive, `N = Threshold / RiskPerTrade`, and
     `RoR ≈ (LossRate / (WinRate × R:R)) ^ N`.
   - **Equity-compounding risk** (a fixed *fraction* of live equity):
     losses compound multiplicatively, so the unit count is logarithmic —
     `N = ln(1 − Threshold) / ln(1 − RiskFraction)` — and the additive
     form above **overstates** the number of units the account can
     absorb.

   Both closed forms assume i.i.d. trades with a fixed payoff ratio.
   Where payoffs are variable, trades are serially dependent, or fees
   and slippage are material — all true here — the closed form is a
   first screen only and the real number comes from **Monte Carlo
   simulation over the actual trade distribution**, not from either
   formula.

   The evaluation contract must also be pinned before use, or the same
   backtest yields different position sizes: what counts as the ruin
   event (peak-to-trough drawdown, not loss from starting capital),
   over what evaluation horizon, on **net** returns including fees and
   slippage, how serial dependence is handled, and at what confidence
   level. Record these alongside the resulting size.
4. **Divide by stop distance to get quantity.** Stop distance and size
   are *jointly* determined by a fixed risk budget, which is what
   allows a wider stop without more risk.
5. **Deflate for correlation** — the closed forms assume i.i.d. trades,
   and correlated simultaneous positions raise effective per-trade risk.
6. **Reject any strategy that cannot live inside the budget.** The budget
   does not move. If measured RoR exceeds the threshold in (2), size is
   reduced until it does not — the strategy is not re-tuned to fit.

Sizing is exponentially more decisive than edge: in the closed forms
above, per-trade risk sits in the exponent while edge sits in the base,
so halving per-trade risk moves RoR far more than doubling edge does.

Kelly is a **ceiling, not a target**. The quantitative claims usually
attached to it are results under specific model assumptions — continuous
rebalancing, known and stationary edge, i.i.d. outcomes — none of which
hold here, so they are cited as motivation for fractional Kelly rather
than as numbers to compute against. What survives the assumptions and is
worth acting on: full Kelly maximises long-run growth only when the edge
is known exactly, it is highly sensitive to estimation error in that
edge, overbetting is strictly worse than underbetting (lower growth *and*
higher variance), and the growth curve is flat near the optimum so
betting below Kelly costs little growth while materially reducing
variance. Hence the practitioner convention of half- or quarter-Kelly.

Below ~50 trades, win-rate and payoff estimates are too noisy for any
Kelly fraction derived from them to be meaningful.

### 3.7 Place stops and targets with MAE/MFE, not convention

**Maximum Adverse Excursion** is the worst unrealised loss a trade
experiences before closing — measured on *every* trade, winners included.
**Maximum Favorable Excursion** is the best unrealised gain.

Method (Sweeney, *Campaign Trading*, 1996): plot MAE against final P&L
across 100+ trades. Winners cluster below a threshold; losers keep
extending past it. That boundary is where the stop belongs. Normalise to
R-multiples — a trade with 1.8x the planned risk in adverse excursion is
1.8R.

Diagnostics worth acting on:
- Winners averaging ~0.3R MAE against a 1R stop means the stop is ~3x
  wider than needed; tightening preserves nearly all winners while cutting
  average loss materially.
- **Winners with MAE ≥0.7R is a warning**, not a success: those are
  rescue trades that nearly failed, and a small worsening of conditions
  converts them into losses. This is a structural fragility signal.
- **MFE capture rate** (realised ÷ MFE) measures exit quality. Typical
  retail is 35-55%; above 0.5 is healthy.
- Needs **50+ trades per setup category** for clustering to be
  meaningful; 100+ for a clean read.

Task S6 chose `stop_multiplier=1.5` / `target_multiplier=3.0` by
convention and never measured whether they fit. That is exactly the gap
this replaces.

**Calculation contract — must be pinned before any MAE/MFE study runs.**
Without this, the same trades yield different R-multiples and therefore
different stop boundaries. It must align with the contracts this
codebase already has, not invent parallel ones:

| Question | Rule, and the existing contract it must match |
|---|---|
| Measurement start | The **fill bar**, not the signal bar. `backtest.fill.simulate_fill` fills at the *next* bar's open (`signal_bar_index + 1`), so excursion is measured from the actual fill price, and the signal bar itself is excluded |
| Reference price | The realised fill price including modelled fee and slippage — the same price `PositionTracker` uses, so MAE/MFE and P&L share one basis |
| Gross vs net | **Net.** Excursions are measured on the same net basis as reported P&L; an MAE computed gross and compared against a net R would be inconsistent |
| Intrabar path | Bar high/low, the only intrabar information available at 1m resolution. This is an approximation — true tick path is unobserved — and it makes MAE a *lower* bound on the real worst excursion. Disclose, do not silently treat as exact |
| Same-bar stop and target | Stop wins, matching `research.strategies.risk_management.check_exit_trigger`'s existing tie-break. Any other choice would make the study disagree with the engine it informs |
| R denominator | The **planned** risk at entry (entry-to-stop distance), fixed at entry and never re-based, so R-multiples stay comparable across trades |
| Still-open trades | Force-closed at the final bar, matching `metrics.metrics.build_equity_curve`'s existing rule, and **flagged as censored** — a forced close is not a real exit and its MFE capture rate is not meaningful |
| Gaps | A gap through the stop is recorded at the actual traded price, not the stop price, so MAE reflects real adverse excursion rather than the intended one |

Where these rules and the engine ever diverge, the engine is the
authority and this document is wrong.

### 3.8 Expanded evaluation metrics

Keep everything already in use (Sharpe, max drawdown, profit factor, win
rate, PSR/DSR, the frequency-scaled trade floor). Add:

| Metric | Why it is needed here |
|---|---|
| **Sortino** | Penalises only downside deviation — directly relevant to the left-skewed distributions measured in Part 2 |
| **Calmar** | Return ÷ max drawdown; the natural objective when drawdown is the binding constraint |
| **Expectancy** | Average P&L per trade — the number that survives when win rate misleads |
| **MAE / MFE distributions** | Stop and target placement (3.7) |
| **MFE capture rate** | Exit quality |
| **Turnover** | A sanity ceiling. Both prior failures would have tripped it |
| **Risk of ruin** | Computed *before* live consideration, against the 3.6 budget |

### 3.9 Research split, not single-shot holdout

For this line of work, replace the single-access holdout design with a
genuine research split plus a reserved holdout:

- **Research data**: the two already-spent 1m windows (BingX BTC-USDT,
  spent by `vwap-mid-reversion`; Binance futures BTCUSDT, spent by
  `ofi-momentum`). They cannot serve as clean holdouts again, so
  converting them to exploration data costs nothing.
- **Reserved holdout**: Binance **spot** 1m, never backfilled, never
  touched.
- Search freely on the research split; count every trial honestly; deflate
  with DSR. That is the machinery working as designed.

Note for the record: the measurements in Part 2 were taken on
already-spent windows and informed hypothesis selection. Any future
strategy built on them must disclose that as a form of selection, even
though no clean holdout was consumed.

### 3.10 Live-side controls that must exist before any real order

Distinct from backtest assumptions. Currently missing or unverified:

- **Slippage / price-reasonability guard.** `GUARDED_MARKET` currently
  maps to a plain `"MARKET"` order on the wire with no price cap — the
  guard is a name only. This matters far more for a strategy that
  deliberately enters during volatile moments.
- **Stale-data check.** A bot acting on stale prices is creating risk,
  not managing it. Already identified as a real gap on the KIS path
  (`PriceFeed#latestPrice` returns a bare value with no timestamp).
- **Turnover ceiling** and **order-rate anomaly trigger** — two distinct
  metrics, not one under two names. **Turnover** is traded notional (or
  absolute position change) divided by capital over a period, i.e. an
  *exposure* measure. **Order rate** is orders or trades per unit time,
  i.e. a *runaway-loop* measure. A strategy can breach either without the
  other, so each needs its own threshold and its own trigger.
- **Drawdown circuit breakers** at daily/weekly/total resolution, halting
  automatically and requiring **manual review before restart**.
- **Run all pre-trade checks, do not short-circuit** — stopping at the
  first failure means the audit log records only the first failure.
- **No override flag.** The most expensive incidents come from manual
  overrides that became habitual.

---

## Part 4 — What runs next, in order

1. **Measure slippage for real.** Determines whether 15-minute horizons
   survive at all (Part 2 sensitivity table). 1m OHLCV cannot show
   spread, so this needs either public order-book/quote data — the
   preferred route, since it involves no order at all — or, only if that
   proves insufficient, a **BingX VST demo fill experiment against
   virtual funds**, which requires its own human approval and is bounded
   as follows:

   - **Demo host only** (`open-api-vst.bingx.com`, virtual USDT). No
     production endpoint, under any circumstance.
   - **Through the full OMS path** — `OrderIntent → OrderPipeline →
     RiskGateway → Order → ExchangeOrderExecutor → BingXAdapter` — never
     a hand-built order or a direct adapter call. This is a standing
     project rule, not a per-task choice.
   - **Existing risk controls verified working first**, and the live-side
     controls listed in 3.10 either implemented or explicitly accepted as
     absent for this bounded experiment by the human operator.
   - Nothing in this step authorises `GUARDED_MARKET` against a real
     account, or any relaxation of the Live Entry Criteria.
2. **Build the regime classifier** (two-axis, hysteresis, dwell time) and
   characterise the research windows by regime.
3. **Measure IC per feature category** (3.5 table), by regime.
4. **Check orthogonality** among surviving features — correlated features
   do not add breadth.
5. **MAE/MFE study** on whatever entry criteria survive, to place stops
   and targets from data.
6. **Derive the risk budget** (3.6) and size from it.
7. **Assemble, walk-forward, deflate** using the existing machinery.
8. **Reserved holdout**, once, at the end.

Steps 2-5 are measurements, not strategy selections, and run on
already-spent data.

---

## Sources

External research, 2026-08-26. Practitioner sources are cited for
practice and convention; where a claim is empirical, the primary
reference is named.

- QuantInsti, *Systematic Trading* — strategy decomposition, pipeline
  https://www.quantinsti.com/articles/systematic-trading/
- Kumiega & Van Vliet, *A Software Development Methodology for Research
  and Prototyping in Financial Markets* (arXiv:0803.0162) — stage-gate
  process for trading system development
- QuantifiedStrategies, *MAE and MFE Explained* — Sweeney's method
  https://www.quantifiedstrategies.com/maximum-adverse-excursion-and-maximum-favorable-excursion/
- LuxAlgo, *MAE/MFE-informed Management*
  https://www.luxalgo.com/library/concept/mae-mfe-informed-management/
- Traders Second Brain, *MAE and MFE: How to Read Trade Excursion Data*
  https://traderssecondbrain.com/guides/mae-mfe-analysis
- Sweeney, J., *Campaign Trading* (1996) and *Maximum Adverse Excursion*
  (Wiley, 1996) — primary source for MAE/MFE
- Banerjee, K., *Detecting Volatility Regimes in Crypto Markets using
  Realized Volatility Structure and Normalized Momentum* (SSRN 5920642)
- Traders Second Brain, *Market Regime Identification* — two-axis regime
  https://traderssecondbrain.com/guides/market-regime-identification
- FractalCycles, *Market Regime Detection* — regime lag, hysteresis
  https://fractalcycles.com/guides/market-regime-detection
- Portfolio Optimizer, *Range-Based Volatility Estimators*
  https://portfoliooptimizer.io/blog/range-based-volatility-estimators-overview-and-examples-of-usage/
- Yang & Zhang (2000) — the drift- and gap-aware OHLC variance estimator
- Grinold, R. (1989), *The Fundamental Law of Active Management*, JPM;
  Grinold & Kahn, *Active Portfolio Management* 2nd ed., ch. 6
- Clarke, de Silva & Thorley (2002) — transfer coefficient
- Ding & Martin (2017), *The Fundamental Law of Active Management: Redux*,
  Journal of Empirical Finance
- Quod Financial, *Pre-Trade Risk Controls*
  https://www.quodfinancial.com/pre-trade-risk-controls-in-electronic-trading-guardrails-before-the-order-hits-the-wire/
- FIA, *Best Practices for Automated Trading Risk Controls and System
  Safeguards*
  https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
- KlawTrade, *Algorithmic Trading Risk Management: The 14-Check Gate*
  https://klawtrade.com/blog/algorithmic-trading-risk-management-guide
- QuantifiedStrategies, *Risk of Ruin in Trading*
  https://www.quantifiedstrategies.com/risk-of-ruin-in-trading/
- Traders Second Brain, *Risk of Ruin: The Math That Keeps Accounts Alive*
  https://traderssecondbrain.com/guides/risk-of-ruin-math
- Astute Investor's Calculus, *Kelly Criterion Position Sizing*
  https://astuteinvestorscalculus.com/kelly-criterion-position-sizing/
- LuxAlgo, *What Is Overfitting in Trading Strategies*
  https://www.luxalgo.com/blog/what-is-overfitting-in-trading-strategies/
- Harvey, Liu & Zhu — t-statistic hurdle of 3.0 for new factors
- Bailey & López de Prado — deflated Sharpe ratio (already implemented
  here as `research/eligibility.py`)
- Mandelbrot (1963); Engle (1982), ARCH — volatility clustering
- Moskowitz, Ooi & Pedersen (2012) — time-series momentum, the source of
  `daily-tsmom-ensemble`'s lookbacks
- *Explainable Patterns in Cryptocurrency Microstructure*
  (arXiv:2602.00776) — 1-second Binance perpetual order-book study
- Concretum Group, *Seasonality in Bitcoin Intraday Trend Trading*
  https://concretumgroup.com/seasonality-in-bitcoin-intraday-trend-trading/

### Reproduction

The Part 2 measurements were produced by throwaway analysis scripts run
against the local `python/data/var/klines.sqlite3` (gitignored). No
holdout was accessed and no `runs/experiments.jsonl` record was written,
because no strategy configuration was selected or scored.

**Provenance of the data as read on 2026-08-26:**

| Symbol | Interval | Bars | Range (UTC) | Duplicate `open_time_ms` |
|---|---|---|---|---|
| `BINANCE-FUTURES:BTCUSDT` | 1m | 3,661,780 | 2019-09-08T17:57:00Z → 2026-08-25T15:37:00Z | 0 |
| `BTC-USDT` (BingX) | 1m | 910,040 | 2024-11-30T16:00:00Z → 2026-08-24T15:26:00Z | 0 |

Both windows are already spent (`ofi-momentum` and `vwap-mid-reversion`
respectively). Repository state at the time of measurement:
`027278a879c9e8a7ced7bfd1eca7ad33c850316d`.

**Computation rules, stated so a re-run is unambiguous:**

- **Returns**: simple, on `close`, in bps —
  `(close[i+h] − close[i]) / close[i] × 10_000`. Bars with
  `close[i] <= 0` are skipped; none were encountered.
- **Missing bars are not interpolated.** Indexing is positional, so the
  known real gaps (2 in the BingX window, 1 in the Binance window — see
  CLAUDE.md's Exchange API Facts) mean a handful of "h-bar" horizons
  span slightly more wall-clock time than nominal. Bounded and
  disclosed; not corrected.
- **Cost horizon table**: non-overlapping samples, stepping `h` bars at
  a time — these observations are **non-overlapping**, which is not the
  same as independent. No two share a bar, but serial dependence
  survives regardless: volatility clustering, the very property this
  document relies on elsewhere, guarantees it. Any confidence interval
  or test computed on these still needs HAC or block-bootstrap
  inference.
- **Activity buckets and the directional level test**: **overlapping**,
  every bar used as an observation. This is the dependence problem
  flagged above; the nominal counts are not effective sample sizes.
- **Activity** = rolling sum of `|1m return|` over the prior 30 bars,
  computed from bars strictly before the decision bar.
- **Levels** = prior UTC calendar day's high/low, built from the same 1m
  bars; proximity threshold 10bps; the first day (no prior day) is
  excluded.
- **No fees or slippage are applied to these numbers.** They are gross
  price movements, compared *against* a separately stated 30bps
  round-trip cost assumption (`FEE_BPS=5` + `SLIPPAGE_BPS=10`, one way,
  doubled). This is why the tables report a ratio rather than a P&L.

The scripts were deliberately not committed: they select no
configuration, produce no logged run, and re-implementing them from the
rules above is the point of stating the rules. If any Part 2 number is
ever used to justify a strategy decision rather than to direct research,
it should be recomputed by committed code first.
