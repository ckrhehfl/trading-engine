# Trade Management Task C — result: the confluence hedge is REJECTED

Executes `.planning/tm-c-confluence-hedge-specification.md`. Reproduce
with:

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.tmc_confluence_hedge_run

**No constant in the specification was changed.** The four values it
declared arbitrary in advance (funding percentile 0.80, flow z 1.0,
price z 1.0, activity rank 0.5) and the 0.5 hedge fraction are exactly
as registered, and the runner does not expose them as arguments — the
stopping rule is enforced by construction rather than by restraint.

## Two runs, and why the second is not a threshold search

The registration named **daily** bars. That run fired the four-condition
conjunction **twice** in 2,544 bars — INCONCLUSIVE-DATA-LIMITED against
its own floor of 20, which the specification had named in advance as the
single most likely outcome.

The legitimate response to "too few samples" is more samples. So the
identical specification was run on **hourly** bars, holding every
calendar-denominated constant fixed by scaling its bar count: lookbacks
`{21, 63, 126, 252}` days → `{504, 1512, 3024, 6048}` hours, the 90-day
trailing window → 2,160 bars. The ATR period stayed 14 bars, being
Wilder's own bar-denominated convention; rescaling it would have been a
change to the specification rather than a translation of it.

**This is still trial two of one hypothesis and is counted as such.** A
second timeframe is a second look. It is reported as a conclusion only
because the two runs agree; had they disagreed, the honest reading would
be that neither is established.

## The runs

Both aggregate Binance USDT-M futures 1m bars (2019-09-08 → 2026-08-25).
That series is used because the conjunction needs
`taker_buy_base_volume` and **BingX's wire carries no buyer/seller
breakdown at all**. An earlier daily run against BingX bars fired the
flow condition zero times and, by the strategy's own fail-closed rule,
opened no hedge whatsoever — the guard working, not a result.

| | daily (2,544 bars) | hourly (61,031 bars) |
|---|---|---|
| funding condition | 364 | 8,460 |
| flow condition | 348 | 7,674 |
| price condition | 479 | 11,425 |
| activity gate | 912 | 21,993 |
| **all four** | **2** | **166** |
| hedges opened | 2 | 150 (46 invalidated) |
| verdict | INCONCLUSIVE-DATA-LIMITED | **REJECTED** |

Hourly, core alone versus core plus overlay:

| | trades | return | max DD | PF | Sharpe |
|---|---|---|---|---|---|
| core alone | 453 | +173.1% | 18.80% | 3.18 | +0.917 |
| core + hedge | 452 | +168.6% | 18.80% | 3.17 | +0.903 |

**The overlay costs 4.49 percentage points of return and lowers Sharpe.**

## Why, precisely

The decomposition is the part no previous candidate in this project
could produce, because every earlier strategy held a single net position
and had no way to say which part of it earned what.

| | figure |
|---|---|
| tactical gross edge, 150 round trips | **+45** |
| fees the overlay added | **+353** |
| hedge win rate | 58.0% (87 of 150) |
| mean per hedge | +0.3 (t = **+0.29**, p = 0.772) |

Two separate failures, and the order matters:

1. **The edge is not distinguishable from zero.** t = +0.29. A 58% win
   rate on a mean of +0.3 is what a coin flip looks like at n=150.
2. **Even taken at face value it is 1/8 of its own fee bill.** +45 gross
   against +353 in fees. This is the S12 finding again in a new place:
   a gross figure that is real in sign and an order of magnitude too
   small to pay for itself.

`Book.realized_pnl` is `(exit − entry) × quantity` — net of slippage,
which is baked into the fill price, but **gross of fees**, which `Fill`
carries separately. Reporting the +45 without the +353 beside it would
have shown the overlay's raw edge as its outcome. The runner now prints
both, labelled.

## The mechanism: the hedge never lives long enough

**Median hold: 1 bar. Longest hold across all 150: 3 bars.**

That is the real diagnosis, and it is structural rather than
incidental. The specification's exit rule — deliberately parameter-free
— is "the setup conditions no longer all hold". A conjunction of three
conditions is fragile in exactly the direction that matters here: any
one of them relaxing ends the hedge. So a rule designed to add no
parameters instead pinned the holding period to about an hour.

The operator's own description was:

> It rises. Weakness appears but a bounce still looks possible, so add a
> short hedge there. When it drops, close **only the short** for profit.

A one-hour hedge cannot express that. The pullback being traded takes
longer than the conjunction survives. **What was tested is not the
hypothesis that was described** — it is the strictest possible reading
of it, and the parameter-free exit is what made it so.

That is a real finding about the specification, not a licence to change
it. Naming a better exit now, after seeing this, is precisely the move
the stopping rule forecloses.

## Conclusion checks

Run automatically before the verdict prints; `require_no_blockers`
raises rather than warns.

- **Blockers: none.**
- **One warning, kept and disclosed**: the 150 hedge windows are
  genuinely disjoint, but 100 of 149 consecutive pairs sit under 24 bars
  apart. Disjoint is not independent — clustered positions carry
  overlapping information, so the effective sample is smaller than 150.
  **The direction of that correction is known: it can only shrink a
  t-statistic, never grow one.** t is already +0.29, so the conclusion
  is robust to it. Stated rather than corrected because a correction
  would only strengthen a verdict that already holds.

### A real gap this run exposed in the checklist itself

The first attempt at this check **blocked**, and it was right to,
because it was asked the wrong question. `check_non_overlapping` takes a
single `hold_bars` and can therefore only ask whether starts are far
enough apart for the *longest* hold — sufficient but not necessary. Fed
`hold_bars=3`, it flagged 29 one-bar hedges sitting two bars apart as
overlapping. They are not.

That became the wrong tool the moment `metrics.book` made
**variable-duration legs the normal case**. `check_disjoint_intervals`
was added for it: half-open `(start, end)` pairs, a real blocker on a
real overlap, plus an optional `clustering_gap` that reports proximity
as a **warning**. 12 tests, including one asserting that the uniform
form flags the case the interval form correctly clears.

This is a widening of the checklist's coverage, not a weakening to get
a result through. The interval form is strictly more precise, and the
substantive concern the uniform check was raising — clustering — is now
reported explicitly instead of being conflated with overlap.

## An unrelated defect this task surfaced: the test suite was writing to `N`

Running from the wrong directory produced a stray `python/runs/`, which
led to checking the real one. **`runs/experiments.jsonl` contains two
`strategy_id: "test-ofi-momentum"` records over 20 synthetic bars**,
appended 2026-08-26 and 2026-08-27 by
`tests/test_ofi_momentum.py::TestTrainableFit` run from the repository
root.

That log is the sole input to `check_project_combination_count`, whose
`research_selection_trials` is the **`N` every Deflated Sharpe Ratio in
this project is deflated against** — an Eligibility Bar gate. The leaked
records form their own single-member family contributing 1 selection
trial, so **the project's stated `N` of 127 is 126 real trials plus one
test artifact.**

**No past conclusion was wrongly passed.** An inflated `N` can only
lower a DSR, never raise it, so the error runs in the safe direction
throughout. The two records are therefore left in place: the log is
append-only by design, and rewriting history to correct a conservative
error is a worse precedent than carrying a documented one. Anyone
recomputing `N` should read 126.

**The leak itself is fixed**, in `python/tests/conftest.py` (new) with
`python/tests/test_conftest_isolation.py` (8 tests) asserting the fix
works rather than assuming it. Two false starts are recorded there
because both are instructive:

1. **Reassigning `DEFAULT_RUNS_PATH` is inert.** A default argument is
   evaluated once at `def` time, so the module attribute and every bound
   default are separate objects from then on. That version would have
   looked like isolation while isolating nothing.
2. **Patching the callers does not scale.** **25 sites** bind
   `= experiment_log.DEFAULT_RUNS_PATH` as a default, including the
   `__init__` of every `Trainable` in `research/strategies/`. A version
   listing `log_run` and `run_walk_forward` was written, and
   `OfiMomentumTrainable.__init__`'s own bound default leaked straight
   past it on the first verification run — caught only because the
   verification compared the real log's sha256 before and after instead
   of trusting that the fixture worked.

3. **Keying the rule to the experiments log was still too narrow.**
   `live/generate_daily_signal.py` writes a *different* committed file,
   `runs/live_signals.jsonl`, through the same `log_run`, passing that
   path explicitly. A wrapper that redirected only the experiments path
   let it through, and the full suite recreated `python/runs/` on the
   very next run — caught, again, only by checking for the directory
   rather than by the tests passing.

The fix wraps the **write functions** in `experiment_log`, the single
point every path converges on, and redirects **any relative
`runs_path`** — that being the property behind every incident, since a
relative path resolves against whatever directory pytest was started in.
An absolute path is always deliberate (`tmp_path`) and is honoured
exactly; the filename is preserved so the two logs stay
distinguishable. A test asserts that no module bypasses the wrapper with
a direct `from research.experiment_log import`.

Verified end to end: the full suite runs from both the repository root
and `python/` leaving **both** `runs/experiments.jsonl` and
`runs/live_signals.jsonl` byte-identical, and creating no stray
`python/runs/`.

**Three false starts, each caught by an external observable rather than
by a passing test, is the transferable lesson.** All three versions
passed their own new tests. What failed them was a sha256 comparison and
an `ls`.

## What this does and does not establish

**Establishes**: this conjunction, at these thresholds, on BTC futures
at daily and hourly resolution, produces a tactical overlay whose gross
edge is indistinguishable from zero and far below its own fee bill.

**Does not establish**: that hedging is useless, that confluence is
useless, or that the operator's described trade does not exist. One
specification was tested at two timeframes. The exit rule alone —
forced parameter-free, and consequently ~1 hour long — is enough to
account for the result without any claim about the underlying idea.

## The specification's own named failure modes, scored

The specification named four ways this could fail, in advance. Three
fired:

- **"Too rare to measure."** Fired exactly as predicted at daily
  resolution. Resolved by more bars, not by looser thresholds.
- **"Fees."** Fired, and decisively — 353 against 45.
- **"The hedge cuts the core's own edge."** Fired: −4.49pp. The
  specification called this a **direct test of whether a selective,
  conjunction-gated reduction behaves differently from an unconditional
  one** (S15 and S17 both found unconditional reduction destroys
  trend-following returns). It does not. **That is now three independent
  confirmations of the same finding**, and it is the most transferable
  thing here: for this core, reducing exposure during an adverse
  excursion loses money whether the reduction is blind or carefully
  gated.
- **"The conditions turn out to be correlated."** Did *not* fire, and
  this is the one genuinely positive result. Four conditions firing
  364/348/479/912 times individually and only **2** times jointly on
  2,544 daily bars is far below what correlated conditions would
  produce. They are close to independent, which was the design premise.
  **The conjunction is real; what it selects for is not tradeable at
  these thresholds.**

## What follows, under the pre-committed stopping rule

The rule forecloses adjusting a threshold and re-running. It permits
two things:

1. **Accept the result.** The signal is not there at this specification.
2. **Wait for the positioning data now being collected** (open interest,
   long/short ratios — `python/data/binance_positioning.py`, on cron
   since this task) and specify a *different* conjunction using families
   this one could not include. Task B's catalogue found this project had
   been building from **two of five** signal families; this candidate
   used three. Positioning is the fourth, and it is accumulating now.

The holding-period finding is a legitimate input to a *future*
specification, provided that specification is registered before it is
run, as this one was.
