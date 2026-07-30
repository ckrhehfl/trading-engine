# Strategy Research Task U: the pre-registered daily TSMOM attempt (spec + registration)

## Scope note, stated as plainly as `sr-t`'s was

This task commits **the strategy implementation and its pre-registration**.
It does **not** execute either against the `1d` early-window holdout.
Execution is a separate, later task (`sr-v`), which loads
`configs/research/holdout_1d.json`'s holdout klines via
`research.holdout.load_holdout_klines` and runs this registration exactly
once. That separation is the entire point of pre-registering: the
specification (this task) must be committed, hashed and git-tracked
*before* the data (a later task) is ever loaded, or `N=1` is merely
asserted rather than provable.

**No BTC-USDT `1d` kline data — not the holdout window, not the research
split, not one bar — was loaded, queried, or plotted while writing this
task.** See "Confirmation the 1d window was not touched" below for the
itemized check.

## Where this sits in the project's sequence

`sr-r` (2026-07-29) closed the 1h research window to further selection: 0
of 18 configurations survive DSR against the project's 117-trial `N`, and
the window's own detection floor (~1.21 annualized Sharpe) sits above any
plausible real edge. `sr-p`/`sr-q` built the honest trial-accounting and
PSR/DSR machinery that `sr-r` used to reach that conclusion. `sr-s` then
built the pre-registration mechanism (`python/research/preregistration.py`,
`python/research/run_preregistered.py`) so a future attempt's `N=1` claim
would be *provable*, not merely asserted -- committed, hashed, and checked
against the human-approved Eligibility Bar's own constants. `sr-t` wired
the `1d` interval into the data pipeline and reserved its earliest window
(2021-05-14 through 2024-04-26, 1,079 bars) as an early-window holdout --
the only window in this project's history with a detection floor (~0.96)
below a plausible real edge, precisely *because* no trial has ever touched
it.

**Gate 2** (human decision, 2026-07-30): exactly one pre-registered
attempt against that window. Hypothesis: a zero-fitted-parameter,
literature-specified daily time-series-momentum ensemble. This task is
that attempt's specification and registration -- the first real user of
`sr-s`'s machinery.

## The strategy specification (decided at Gate 2, not redesigned here)

Restated from the task brief for a self-contained record:

- **Signal**: at each daily bar, for each lookback `L` in {21, 63, 126,
  252} trading days (Moskowitz-Ooi-Pedersen 2012's canonical 1/3/6/12-month
  set), `sign(close[t] - close[t-L])`. Position direction/strength = the
  average of the four signs.
- **Sizing**: constant-target volatility at 20% annualized, reusing
  `python/research/strategies/volatility_targeting.py` unmodified.
- **Deliberately absent**: no ADX regime gate, no ATR stop/target, no
  risk:reward grid, no funding signal driving the signal itself.
- **`fit()` does no search**: `total_candidates: 1`.
- Orders via `GUARDED_MARKET`, edge-triggered, self-contained state,
  look-ahead-safe.
- One open design decision: how a change in fractional strength maps to
  order emission. Decided below, before any data was seen, never revisited.

Implementation: `python/research/strategies/daily_tsmom_ensemble.py`
(`DailyTsmomEnsembleStrategy`, `DailyTsmomEnsembleTrainable`). Registration:
`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`.

## Design decisions, with reasoning -- written before the run, not after

### 1. Order-emission mapping: Option B, "only-on-sign-change"

The brief named two options: rebalance to the new target every time the
ensemble's fractional strength changes at all (e.g. `+0.5 -> +1.0`, same
sign, different magnitude), or fire only when the **sign category**
(`-1`/`0`/`+1`) changes.

**Chosen: only-on-sign-change.** A position, once opened, holds the
quantity computed at the moment it was opened until the ensemble's sign
category itself changes -- it is never resized purely because the
same-sign magnitude moved.

Reasoning, in order of weight:

1. **This is the codebase's own established convention, not a new one.**
   Every sibling ensemble/crossover strategy already in this package fires
   only on a sign transition and is silent on same-sign magnitude changes:
   `ensemble_momentum.py`'s net-sign `_combined_sign`, `hourly_momentum.py`'s
   `_crossover_sign`, `mean_reversion.py`/`funding_extremity.py`'s
   `_signal_state`. `ensemble_momentum.py`'s own docstring names this exact
   tradeoff explicitly for its own net-sign combination: "discarding *how
   strongly* each pair agrees... a real, accepted simplification, not an
   oversight." Choosing the same convention here is consistency with
   precedent, not a default reached for lightly.
2. **It matches the strategy's own exit philosophy.** This module has no
   ATR stop/target at all (per the governing spec). The standard
   pure-trend-following exit convention the cited literature itself uses is
   "hold until the signal reverses," with no separate stop-loss layer. A
   position that resizes on every same-sign wobble is a different, noisier
   design than "ride the trend until it ends."
3. **It is simpler to reason about and test.** A position's lifecycle
   under Option B is exactly "opened once, held, closed once" (or flipped
   directly) -- the same shape every sibling strategy's `OpenPosition`
   lifecycle already has, just without a stop/target trigger driving the
   close. Option A (continuous rebalancing on every magnitude change) would
   require deciding, separately, how a *partial* resize interacts with
   `metrics.position.PositionTracker`'s trade-lifecycle reconstruction
   (does a same-sign resize count as continuing one trade, or as closing
   and reopening?) -- a real design question Option B never has to answer.

**Disclosed cost, not engineered around, per the brief's own instruction.**
Two consequences, named plainly:

- A held position's realized size can lag the ensemble's *current* true
  conviction once a sign is established -- e.g. a position opened at
  `|value|=0.25` stays at that size even if the ensemble later strengthens
  to `|value|=1.0` within the same sign, until the sign itself changes.
- Trade count is bounded by how often the ensemble's **sign** (not
  magnitude) flips over the registered window -- materially fewer events
  than "rebalance on any magnitude change" would produce. The registered
  `min_total_trades` floor for this window is **53**
  (`research.preregistration.frequency_scaled_min_trades(total_evaluated_bars=1079,
  bars_per_day=1)` = `floor(1079/1/20)` clamped to `[30,100]` = 53). This is
  a real, disclosed risk under Option B, not resolved in either direction
  here -- if the run comes in below 53 trades, the pre-registration's own
  `INCONCLUSIVE` region already accounts for it (`INCONCLUSIVE-DATA-LIMITED`).

**Explicitly not chosen for the wrong reason.** Option A would very likely
produce *more* trades (vol-adjusted resizing on every ensemble-level
change), which could look, superficially, like the "safer" choice against
the 53-trade floor. That is exactly the kind of reasoning the brief warns
against ("declare it, don't engineer around it after the fact") -- Option
B was chosen on the three grounds above, before weighing which option
produces a friendlier trade count, and the trade-count risk is disclosed
rather than resolved by picking the other option.

### 2. Warmup: all four lookbacks must be simultaneously warm

`DailyTsmomEnsembleStrategy` requires `max(lookbacks) + 1` (253, for the
default set) closes before computing *any* signal -- not a signal computed
from whichever subset of lookbacks happens to already be warm.

This directly follows `ensemble_momentum.py`'s own precedent and stated
reasoning ("All 3 pairs must be simultaneously warmed up... rather than
combining whichever subset of pairs happens to be warm. This avoids the
ensemble's effective character silently changing over the warmup period").
The alternative (partial-ensemble signals during the first 231 bars, as
each lookback comes online one at a time) would mean the strategy is
effectively a 1-lookback, then 2-lookback, then 3-lookback, then
4-lookback ensemble at different points in the same run -- a silently
changing instrument, not a fixed one. Simpler to reason about and test, at
the cost of not trading at all during the first 253 daily bars (~8.3
months) of the 1,079-bar holdout window -- leaving roughly 826 usable bars
(~2.26 years) in which the strategy can actually signal.

Tested directly: `TestWarmup` in
`python/tests/test_daily_tsmom_ensemble.py` confirms no signal (and no
state change) before all lookbacks are warm, and that a screaming
3-lookback-worth-of-signal price move does not fire early on a shorter
subset.

### 3. Funding accounting: not wired in for this attempt

The brief left this open: "Funding P&L may be included in accounting if
the registration says so (your call — decide and register it; note the
funding data's own retention starts 2020-11-29 so it covers the window),
but funding must not gate or size the signal."

**Decided: `funding_included: false`.** Reasoning:

1. **No `load_holdout_funding` loader exists yet.** `sr-t`'s own
   "Deliberately out of scope" list names this explicitly: "No
   `load_holdout_funding` added. Still no caller needs one... `'before'`
   support would be a two-line addition there when a real task needs it."
   Building and testing that loader is real infrastructure work, and this
   task's scope is the strategy specification and registration, not a new
   data-loading code path.
2. **Building/testing it now would itself risk touching the reserved
   window ahead of schedule.** A `load_holdout_funding` implementation can
   only be *validated* by actually calling it against the real funding
   table for the reserved date range -- which is precisely the kind of
   pre-run access this whole apparatus exists to prevent. Safer to leave it
   for `sr-v` (which is already the task responsible for the one real
   access) to build alongside the actual execution, where it can be
   verified against the real call it will actually make.
3. **The effect is genuinely unknown for this strategy's holding-period
   profile, which is worth naming rather than assuming small.** `sr-n`
   found funding P&L's effect on Configuration C (a ~19-hour average hold,
   1h strategy) was small and mostly neutral (+0.027 to +0.039 mean
   Sharpe). This strategy's positions can be held for weeks to months
   (governed by when the 4-lookback sign flips, not a fixed exit), so
   funding compounding could plausibly matter far more than it did for a
   19-hour hold -- an honest "not measured for this profile" is more
   defensible than assuming `sr-n`'s small-effect finding transfers.
4. **Keeping this attempt's scope minimal reduces the surface a bug could
   hide in.** This is a single, one-shot, human-approved attempt; adding a
   new, unexercised-until-holdout-time data path is exactly the kind of
   addition that increases risk without adding confidence, given point 2
   above means it cannot be tested against the real target data by this
   task.

`DailyTsmomEnsembleTrainable` still accepts an optional `funding_rates`
constructor parameter (default `None`), purely additive and unused by the
registered configuration -- symmetry with every sibling `Trainable`'s
`compute_metrics` call, and a a future, *separately* pre-registered attempt
could opt in without a constructor-signature change. It is never read by
the signal itself, only (if ever supplied) by P&L accounting, matching the
brief's own "must not gate or size the signal" constraint regardless of
whether it is ever wired in.

### 4. Discrepancies noted, not silently resolved

The brief's own prose describes the achievable ensemble-value range as
`{-1, -0.5, 0, +0.5, +1} up to ties/zeros`. That enumeration is not
exhaustive: averaging 4 values each in `{-1, 0, +1}` produces all 9
multiples of `0.25` in `[-1, 1]` (`-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5,
0.75, 1`), not only the 5 named. `DailyTsmomEnsembleStrategy` implements
the literal specified formula ("average of the four signs") rather than
silently restricting the range to the smaller named set -- restricting it
would be a redesign the brief did not ask for, and the literal formula is
unambiguous on its own. Documented in the module's own docstring and
tested directly (`TestEnsembleSignalComputation::
test_partial_agreement_produces_fractional_values`,
`test_single_lookback_disagreement_produces_quarter_step`), so a future
reader hitting a `0.25`/`0.75` reading does not mistake it for a bug.

## The registration, field by field

`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`.
Validated by `research.preregistration.validate_preregistration` (also
covered generically by `test_preregistration.py`'s
`test_every_committed_registration_validates`, which globs every file in
the directory) and by this task's own `TestPreregistration` class in
`python/tests/test_daily_tsmom_ensemble.py`, which checks content specific
to this registration (entry-point resolution, family resolution, the exact
data window, the trade-floor/detection-floor arithmetic).

- **`strategy_family: "daily-tsmom"`** -- a new family, added to
  `research/lineage.py`'s curated map in this same PR
  (`FAMILY_BY_STRATEGY_ID["daily-tsmom-ensemble"]`), `purpose="research"`,
  citation pointing at this document. It resolves both ways: via the
  logged `strategy_family` field a real run will carry (`source="logged"`),
  and via the curated map as a fail-closed fallback if a future runner ever
  omits the explicit argument (`source="curated_map"`) -- satisfying the
  amended Eligibility Bar's fail-closed clause 2 regardless of which path
  `sr-v` ends up using.
- **`data`**: `symbol="BTC-USDT"`, `interval="1d"`, `split="holdout"`,
  `holdout_config_path="configs/research/holdout_1d.json"`. The window is
  `start_ms=1620950400000` (2021-05-14T00:00:00Z) through
  `end_ms=1714176000000` (2024-04-27T00:00:00Z, exclusive -- matching
  `holdout_1d.json`'s own `holdout_cutoff_ms` exactly, since `holdout_side:
  "before"` makes the holdout `(-inf, cutoff)`), `expected_bars=1079`.
  Cross-checked against `configs/research/holdout_1d.json`'s own committed
  `rationale` field ("Holdout: 1,079 daily bars, 2021-05-14 through
  2024-04-26 inclusive") -- by reading that **config file** (a cutoff
  timestamp and prose, not price data), never by loading kline rows.
- **`parameter_grid`**: `[{"lookbacks": [21, 63, 126, 252]}]` -- one
  explicit candidate. `total_candidates: 1`, `free_parameter_count: 0`
  (the four lookbacks are literature-specified, never fitted on this
  project's own data).
- **`procedure`**: `fee_bps: "5"`, `slippage_bps: "2"` (this project's
  standing convention, `.planning/sr-i-ensemble-refinement.md`'s "used in
  every real walk-forward call around" -- not re-chosen for this attempt),
  `bars_per_day: 1`, `funding_included: false` (see decision 3 above). No
  fold-geometry keys (`train_bars`/`validate_bars`/`step_bars`) -- a
  single-window holdout confirmation has no folds, and the schema does not
  require them for `kind: "holdout_psr"`.
- **`primary_criterion`**: `kind: "holdout_psr"` (the single-window holdout
  variant -- PSR, not DSR, since this window has never been searched over
  and `N=1` makes DSR identical to PSR anyway). `threshold: 0.95`
  (`ELIGIBILITY_BAR_DSR_THRESHOLD`, the human-approved floor).
  `max_drawdown_ceiling: "0.20"`, `profit_factor_floor: "1.3"`,
  `min_total_trades: 53` (exactly the frequency-scaled floor for this
  geometry -- see the trade-count table below), `criteria_pinned_at_claude_md_revision:
  "2026-07-29"` (the amended Bar's own revision date, current as of this
  task), `require_sharpe_above_detection_floor: true` (mandatory `true` for
  a `holdout_psr` registration -- clause 3 of CLAUDE.md's single-window
  variant: a PSR pass with an observed Sharpe below this window's own
  detection floor is reported "not powered to confirm," not a pass).

  **A note on where drawdown/trade-count/profit-factor sit, reconciling
  two readings of the brief.** The brief's own instructions describe these
  three as belonging in `secondary_reported_not_gating` ("reported, not
  gating, per the amended Bar"). CLAUDE.md's actual holdout single-window
  variant (clause 2) lists them as criteria "the holdout run must instead
  clear" -- i.e. gating -- and `research/preregistration.py`'s schema
  structurally *requires* `max_drawdown_ceiling`/`min_total_trades`/
  `profit_factor_floor` inside `primary_criterion` for every registration,
  holdout or walk-forward, with no path to make them optional or
  non-gating. Given that conflict, this registration follows the code and
  CLAUDE.md's actual Bar text (gating, inside `primary_criterion`) rather
  than the brief's looser paraphrase, and separately honors the brief's
  intent by listing all three in `secondary_reported_not_gating` too -- as
  *descriptive* entries explaining that their **observed values** will be
  reported for context regardless of which side of the (gating) floor they
  land on, not as a claim that clearing them is optional. This is stated
  here explicitly per CLAUDE.md's "state assumptions and ask rather than
  silently pick between valid interpretations" -- there was no one to ask
  mid-task, so the interpretation and its reasoning are written down for
  review instead.

- **`declared_detection_floor_sharpe: 0.9567`** -- recomputed independently
  in this task, not copied from CLAUDE.md's rounded "~0.96":
  `1.6448536269514715 / sqrt(1079/1/365)` = `1.6448536269514715 /
  sqrt(2.9561643835616438)` = **0.9566717874687587**, rounded to 0.9567.
  `1.6448536269514715` is `statistics.NormalDist().inv_cdf(0.95)`
  (`Phi^-1(1-alpha)` at `alpha=0.05`), matching every other detection-floor
  figure in this project's history (`sr-r`, `sr-q`, the example
  registration). Cross-checked by a dedicated test
  (`test_declared_detection_floor_matches_recomputation`).
- **`declared_power`**: `assumed_true_sharpe: 1.0`, `probability: 0.5297`.
  Full arithmetic (both the registered `1.0` case and the `2.0` case the
  brief also asked for, the latter reported in the `derivation` string
  since the schema's `declared_power` object carries exactly one scalar
  point):

  ```text
  years = 1079 / 1 / 365 = 2.95616
  power(SR_true) = Phi(sqrt(years) * SR_true - Phi^-1(1-alpha))

  SR_true=1.0: Phi(sqrt(2.95616)*1.0 - 1.6448536) = Phi(1.71932 - 1.64485)
             = Phi(0.07447) = 0.5297   (registered)
  SR_true=2.0: Phi(sqrt(2.95616)*2.0 - 1.6448536) = Phi(3.43864 - 1.64485)
             = Phi(1.79379) = 0.9636   (reported for completeness)
  ```

  Same normal-approximation method `sr-r` used for the 1h/15m windows.
  Reading: even a real Sharpe-1.0 edge (well above what credible
  institutional trend-following typically reports) would only have been
  detected about 53% of the time on this window; a real Sharpe-2.0 edge
  (an extraordinary claim) would be detected about 96% of the time. Both
  numbers are written down *before* the run, which is the entire point --
  none of the 117 prior 1h-window trials ever did this.

- **`outcome_interpretation`**: three regions (PASS/INCONCLUSIVE/FAIL),
  each carrying the meta-consequence verbatim in spirit in the two regions
  it applies to (INCONCLUSIVE and FAIL): either outcome "ends the BTC-only
  price-signal research program as a line of work -- the next move is a
  named structural change (multi-symbol expansion with survivorship-safe
  data, or a genuinely different data source entirely), not another grid
  anywhere, on any timeframe, against any signal class." PASS is
  explicitly *not* an automatic promotion -- it is "a genuine candidate for
  paper trading, subject to the rest of CLAUDE.md's Backtest/Walk-Forward
  Eligibility Bar and its separate Paper Trading Pass Criteria / Live Entry
  Criteria."
- **Known confounds**, stated in the registration's `notes` field rather
  than discovered after a result exists: 2021-05-14 through 2024-04-26
  spans the 2021 bull-market top, the 2022 LUNA/FTX-driven bear market, and
  the 2023 recovery -- an unusually trend-friendly stretch by BTC's own
  multi-year history. A PASS is real evidence but weaker evidence for a
  2026-forward edge than the detection-floor/power arithmetic alone
  suggests. Also disclosed: the Option-B trade-count risk (decision 1
  above).
- **`stopping_rule`**: exactly one execution, enforced structurally by
  `research.holdout.load_holdout_klines`'s single-access-per-`strategy_id`
  claim mechanism -- a `force_reclaim_reason` is an exception under that
  machinery's own rules, not license for a second real attempt under this
  hypothesis.

### Trade-count floor, reproduced exactly

| Geometry | Evaluated bars | bars/day | days ÷ 20 | Floor after clamp |
|---|---|---|---|---|
| This registration (1,079-bar 1d holdout) | 1,079 | 1 | 53 | **53** |

`research.preregistration.frequency_scaled_min_trades(total_evaluated_bars=1079,
bars_per_day=1)` returns `53` directly, and this registration's own
`_validate_trade_floor` check (run as part of `validate_preregistration`)
confirms `min_total_trades: 53` is not laxer than that floor.

## `fit()`, and what "no search" means concretely

`DailyTsmomEnsembleTrainable.fit()` performs exactly one in-sample
backtest per call (against whatever `train_klines` it is given), logs it
as a single `record_type: "backtest_run"` entry (`candidate_index=0,
total_candidates=1`), and returns a **fresh**
`DailyTsmomEnsembleStrategy` instance -- never the one used for in-sample
scoring, which would carry leftover internal state (rolling closes, the
vol estimator, an open position). This mirrors every sibling
`Trainable`'s "every `fit()` call leaves a trace" convention exactly, and
makes `research.overfitting_check`'s "1 combination tried" accounting an
honest count rather than an artifact of omission.

**One pre-existing, unchanged-by-this-task property worth flagging for
`sr-v`**: `_log_candidate` always passes `is_holdout_run=False` to
`experiment_log.log_run`, matching every sibling strategy module's
identical in-sample-scoring `_log_candidate` call, regardless of whether
the `train_klines` `fit()` was actually given happen to be holdout data.
This is not new to this module -- it is the established shape of every
`Trainable.fit()` in this codebase. Whoever builds `sr-v`'s actual driver
is responsible for logging the *outer*, real single-window run's own
top-level record with `is_holdout_run=True` (the record that matters for
`research.holdout`'s single-access tracking); `fit()`'s internal in-sample
scoring pass is a diagnostic sub-record, not that outer record, exactly as
it is for every other strategy already in this package.

## Confirmation the 1d window was not touched

Itemized, matching `sr-s`'s own precedent for this section:

- **No test in `python/tests/test_daily_tsmom_ensemble.py` reads real
  data.** Every `Kline` is hand-built via `_daily_kline`/`_klines`/
  `_flat_klines`. No test imports `data.store`, `data.backfill`, or
  `research.holdout.load_holdout_klines`/`load_research_klines`.
- **`configs/research/holdout_1d.json` is read only as a config file, in
  exactly one place**: `TestPreregistration::
  test_data_window_matches_holdout_1d_config` cross-checks this
  registration's `start_ms`/`end_ms`/`expected_bars` against the numbers
  already published in `holdout_1d.json`'s own committed `rationale`
  field and in CLAUDE.md -- a cutoff timestamp and prose, not price data,
  and explicitly permitted by this task's own brief ("Reading the config
  file `configs/research/holdout_1d.json` is fine — it contains a cutoff
  timestamp, not price data").
- **No `python/data/var/klines.sqlite3` access anywhere in this PR.** The
  worktree this task was built in has no `python/data/var/` directory at
  all (gitignored, absent from a fresh worktree) -- there was nothing to
  accidentally open even if a test had tried.
- **The registration itself (`daily-tsmom-ensemble-1d-holdout.json`)
  declares `data.split: "holdout"`, which `research.run_preregistered.
  run_preregistered` structurally refuses to drive** (`ValueError` at its
  own entry point) -- this task never ran that runner against this file,
  and could not have accidentally executed the holdout confirmation even
  by invoking existing machinery incorrectly.
- **The strategy implementation
  (`research/strategies/daily_tsmom_ensemble.py`) was designed, written and
  documented entirely from the specification and from reading sibling
  strategy modules already in this codebase** -- never against any
  empirical BTC price behavior, real or synthetic-but-representative. Every
  synthetic price sequence in its test suite was hand-constructed to
  exercise a specific code path (warmup, a sign flip, a tie, a degenerate
  price), not chosen to resemble real BTC-USDT behavior.

## TDD

Tests were written and run against the strategy module as it was built,
red before green at each stage (`ModuleNotFoundError` for the whole file
before `research/strategies/daily_tsmom_ensemble.py` existed; individual
assertion failures while the order-emission/sizing composition tests were
being refined against real, hand-verified arithmetic -- see below).

**Two real corrections made during development, disclosed rather than
quietly fixed:**

1. `TestSizingComposition::test_weaker_conviction_produces_smaller_quantity`
   initially asserted exact `Decimal` equality between a "full-conviction"
   quantity divided by 2 and a "half-conviction" quantity computed via a
   genuinely different multiplication chain (`base_quantity * Decimal(1) *
   vol_scalar` vs. `base_quantity * Decimal("0.5") * vol_scalar`). These
   can legitimately differ in their last few digits under Python's ambient
   28-significant-digit `Decimal` context, because the two chains round at
   different intermediate steps -- not a strategy bug. Fixed by comparing
   via `pytest.approx` at a `rel=1e-12` tolerance (i.e. as floats), which
   is far below the point where the difference could ever be meaningful in
   the real strategy's own economics.
2. `TestCannotSizeRetriesAutomatically::
   test_degenerate_price_flattens_a_stale_position_and_retries` initially
   assumed a single valid bar after a degenerate (`close=0`) bar would
   immediately re-enable sizing. It does not: `RollingRealizedVolatility`'s
   own return-pair computation skips any pair straddling a zero price (see
   that class's own "degenerate... skip rather than divide by zero"
   comment), which means one of the two return slots its `period=2` window
   needs stays empty until the zero-price bar itself has fully rolled out
   of the window -- a real, correctly-designed interaction between two
   independently-correct pieces of code (the zero-price guard and the
   rolling window), not a bug in either. The test was corrected to drive a
   short sequence of valid recovery bars and assert the retry eventually
   succeeds, which is what the strategy's own retry design promises
   ("retried automatically as soon as sizing succeeds"), not "succeeds on
   the very next bar unconditionally."

**Full suite**: `cd python && uv run pytest` -- **1,146 passed** (1,108 on
`main` at the branch point, confirmed via `git stash -u` + a clean
collect-only run; **+38**: the 36 new tests in
`test_daily_tsmom_ensemble.py`, plus 2 tests that already existed as
*parametrized templates* and simply gained one more case each from this
task's additions -- `test_lineage.py::
test_resolve_family_round_trips_every_curated_strategy_id[daily-tsmom-ensemble]`
(parametrized over every key in `FAMILY_BY_STRATEGY_ID`) and
`test_preregistration.py::
test_every_committed_registration_validates[daily-tsmom-ensemble-1d-holdout]`
(parametrized over every file in `configs/research/preregistrations/`)).
Nothing regressed, nothing skipped or xfailed. One pre-existing,
non-parametrized test updated as a direct, expected consequence of this
task's own change:
`test_lineage.py::test_curated_map_covers_the_families_named_in_the_planning_doc`
pins the exact *set* of families in `FAMILY_BY_STRATEGY_ID` -- it now
includes `"daily-tsmom"`, which is exactly what that test exists to catch
(a documentation-of-the-map regression guard, not a design decision this
task made).

## What was built

| File | Change |
|---|---|
| `python/research/strategies/daily_tsmom_ensemble.py` | **new.** `DailyTsmomEnsembleStrategy`, `DailyTsmomEnsembleTrainable`, `_average_of_lookback_signs`, `_sign`. |
| `configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json` | **new.** The registration itself. |
| `python/research/lineage.py` | `FAMILY_BY_STRATEGY_ID["daily-tsmom-ensemble"]` added, citing this document. |
| `python/tests/test_daily_tsmom_ensemble.py` | **new**, 36 tests (construction, signal computation, warmup, order emission, sizing composition, retry-on-unsizeable, `fit()`/no-search, a synthetic `run_walk_forward` integration smoke test, and registration-content checks). |
| `python/tests/test_lineage.py` | 1 test updated (`test_curated_map_covers_the_families_named_in_the_planning_doc`) to include the new family -- a direct, expected consequence of the lineage-map addition. |
| `.planning/sr-u-preregistered-attempt-spec.md` | this document. |

**Zero new dependencies.**

## Deliberately out of scope

- **Executing this registration against real `1d` holdout data.** That is
  `sr-v`, a separate, later task.
- **Building `research.holdout.load_holdout_funding`.** Not needed by this
  attempt (`funding_included: false`); left for `sr-v` if a future attempt
  wants it, per decision 3 above.
- **Evaluating the criterion.** `sr-v` (or whichever task actually runs
  this) reports the numbers; `research/eligibility.py` and
  `research/retrospective.py` judge them against the pre-committed
  `outcome_interpretation` regions. This task assigns no verdict, because
  none exists yet.
- **Amending CLAUDE.md or the Eligibility Bar.** Nothing here changes
  either; the registration restates the Bar's own constants at least as
  strictly as approved (`research/preregistration.py`'s own conformance
  check enforces this at load time).
