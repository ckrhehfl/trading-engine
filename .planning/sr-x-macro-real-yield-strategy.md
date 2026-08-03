# Strategy Research Task X: the macro-real-yield-trend strategy (real, honest result)

## Scope note

This task builds and runs the first genuinely non-price-derived BTC
strategy this project has ever attempted: a hypothesis about the 10-year
real yield (`DFII10`), tested via **ordinary iterative walk-forward
research** on the untouched BTC 1d **research** split (2024-04-27
onward) -- explicitly **not** the spent `1d` holdout (`sr-t`/`sr-v`),
and explicitly **not** another single-shot pre-registration (that
ceremony is reserved for spending an already-designated, never-before-
touched holdout; the research split is fair game for ordinary research
the same way the 1h research window was before `sr-r` closed it).

Prerequisites read in full first: CLAUDE.md ("Strategy Research
Methodology" -- especially the standing rule closing the 1h window to
selection, and why the 1d *research* split is different from both the
1h window and the spent 1d holdout; "Strategy Research Operational
Design"; the amended Backtest/Walk-Forward Eligibility Bar; "Strategy
Attempts So Far"), `.planning/sr-w-macro-data-pipeline.md` (the FRED
pipeline this task builds on), `.planning/sr-t-daily-data-path.md` (the
1d data path and the holdout/research split boundary), `.planning/sr-p-
trial-accounting.md` (the lineage/family system), `.planning/sr-u-
preregistered-attempt-spec.md` (the closest sibling strategy's code
shape).

## Why this task exists

After `sr-v`'s pre-registered daily-TSMOM holdout attempt came back
INCONCLUSIVE (2026-07-30), its own pre-committed meta-consequence ended
the BTC-only price-signal research program as a line of work: the next
move is a named structural change, not another grid on any timeframe
against any signal class. CLAUDE.md names two live remedies --
multi-symbol expansion, or a genuinely different data source. A prior
research pass (cited in this task's own brief) identified macro data via
FRED as the most promising genuinely-new candidate, specifically the
10-year real yield (`DFII10`): a completely separate generating system
(Fed/Treasury) from BTC's own price/volume/funding, with a documented
"relatively more robust (inverse) relationship" with BTC versus
gold/inflation. `sr-w` built the FRED data pipeline but deliberately
stopped short of any signal design. This task is that signal's first
real test.

## The hypothesis (decided at the task's outset, not redesigned here)

At each daily BTC bar, look at the trend in `DFII10` over a trailing
lookback. **Falling real yields -> risk-on -> BTC-bullish (a LONG
signal); rising real yields -> BTC-bearish (a SHORT signal)** -- the
INVERTED mapping the governing research finding specifies. Sizing via
`research/strategies/volatility_targeting.py` unmodified (20%-
annualized-vol-target convention). No ADX gate, no ATR stop, no funding
signal, no combination with price-based momentum -- a clean, standalone
test of the macro signal alone.

## Design decisions, with reasoning -- written before the real run

### 1. Lookback: 63 trading days, one fixed value, zero search

`fit()` performs **no search whatsoever** -- `total_candidates: 1`,
matching `daily_tsmom_ensemble.py`'s own zero-fitted-parameter
discipline exactly. This is this project's single most important design
lesson, restated because it keeps mattering: across eight prior
strategy families, the simplest zero-fitted-parameter attempt
(`daily-tsmom-ensemble`) came closest to a real pass; every strategy
with more knobs did worse (`sr-r`'s retrospective).

**63** (the middle value of `daily_tsmom_ensemble.py`'s own
Moskowitz-Ooi-Pedersen 21/63/126/252 lookback set) was chosen -- reused
as a literature-anchored point already established in this codebase,
not invented fresh -- for reasons specific to what is being measured:

- 21 trading days (~1 month) risks reacting to short-term auction-driven
  noise in the yield itself rather than a genuine regime shift.
- 126/252 trading days (~6/12 months) risk lagging a real turning point
  by so much that a detected flip is already old news, defeating the
  premise of trading a leading macro signal.
- 63 (~1 quarter) is the defensible middle ground: long enough to filter
  short-term yield noise, short enough to register a genuine multi-month
  regime shift within a plausibly tradeable window.

This reasoning -- and the module docstring in
`python/research/strategies/macro_real_yield_trend.py` that states it in
full -- was written and committed **before this task's real walk-forward
run against real DFII10/BTC data was ever executed**. No BTC price data
and no `DFII10` value were consulted while choosing it.

### 2. "L trading days," not "L calendar days" -- and how that composes with forward-fill alignment

The hypothesis names the lookback in trading days. Implemented as two
deliberately separate, composed steps rather than one:

1. `compute_real_yield_trend_signs(observations, lookback=63)` computes
   the trend **on `DFII10`'s own raw business-day sequence**, skipping
   `None`/holiday rows as non-events (they contribute no "trading day"
   step at all) and never seeing a weekend row (FRED returns none) --
   `sign(value[t] - value[t-63])`, stepping 63 REAL observations back,
   not 63 calendar days back and not 63 calendar days with a holiday
   counted as a zero-change step.
2. The resulting trend-sign series (one value per `DFII10` trading day)
   is then forward-filled onto BTC's own calendar-day bars via
   `research/macro_alignment.py` -- the **same** alignment mechanism a
   raw macro *level* would use, reused rather than duplicated, since
   aligning a trend value onto BTC bars is exactly the same "most recent
   real value dated at/before this bar" problem regardless of what the
   values represent.

### 3. The alignment helper, and why it needed to be genuinely new

`research/macro_alignment.py` (`MacroSeriesCursor`,
`forward_fill_macro_series`) is new infrastructure, not a reuse of
anything in `research/holdout.py`/`backtest/kline_window.py` --
those protect look-ahead safety *within* one series (BTC's own OHLCV);
this is a genuinely different surface, a **cross-series** alignment
where the two series have different native calendars (BTC: every
calendar day; `DFII10`: business days only, with holidays present as
real `None`-valued rows -- both facts verified empirically in `sr-w`).

The rule: **forward-fill the last known REAL (non-`None`) `DFII10`
observation dated on or before the BTC bar's own calendar date, never a
value dated after it.** Implemented as `MacroSeriesCursor`, an
incremental online cursor (mirrors
`volatility_targeting.RollingRealizedVolatility.update`'s "one bar per
call" shape) whose internal pointer only ever advances past an
`observation_date <= ` the current kline's date and never rewinds --
look-ahead safety by construction, not by convention.

**Look-ahead-safety verification, concretely**:
`test_macro_alignment.py::TestLookAheadSafety` constructs a cursor from
the macro series' FULL history -- including dates far beyond any kline
it will actually be fed, exactly the real usage shape -- and proves
directly that a kline dated `2024-01-04` never sees a `2024-01-05` or
`2024-06-01` observation, no matter how far into the "future" the
underlying series extends; a second test drives the cursor bar-by-bar
and confirms a later-dated row only becomes visible once the cursor is
actually fed a kline whose own date reaches it. 11 tests total in
`test_macro_alignment.py`, all against synthetic fixtures, all TDD
(the file existed and failed on `ModuleNotFoundError` before
`research/macro_alignment.py` did -- true red-green for this module).

### 4. Zero-trend tie-break: FLAT

An exact-zero trend (the current real yield precisely equal to its value
63 trading days ago) is a genuine tie, not evidence in either direction.
This strategy goes FLAT on it, matching every sibling strategy's own
zero-sign convention in this codebase (`ensemble_momentum.py`,
`daily_tsmom_ensemble.py`, `mean_reversion.py`) -- documented explicitly
per this task's own instruction to state and justify the choice rather
than leave it implicit.

### 5. No `abs(signal)` conviction-scaling term

Unlike `daily_tsmom_ensemble.py`'s ensemble (whose `abs(ensemble_value)`
spans 9 magnitudes because it averages 4 independent lookback signs),
this strategy's signal is a single binary directional call
(`{-1, 0, +1}`, never fractional). `abs(signal)` is always exactly `1`
once non-flat, so the multiplier would be a no-op kept only for cosmetic
symmetry -- omitted rather than carried as dead ceremony. Sizing is
therefore exactly `base_quantity * vol_scalar`, with `base_quantity =
reference_equity / entry_price`.

### 6. Order emission: only-on-sign-change (Option B, same precedent)

Same edge-triggered convention `daily_tsmom_ensemble.py`/
`ensemble_momentum.py`/`hourly_momentum.py` already established: a
position, once opened, holds the quantity computed at that moment until
the signal's own SIGN category changes again. A direct reversal is one
`OrderIntent` combining the closing and opening quantities (the same
already-tested `PositionTracker._reduce_or_close_or_flip` machinery). If
the new leg can't be sized (vol-targeting warmup, degenerate price), a
stale position is still flattened and the retry happens automatically
once sizing succeeds.

### 7. Deliberately absent

No ADX regime gate, no ATR stop/target, no risk:reward grid, no funding
signal, and critically **no combination with any BTC-price-derived
momentum signal** -- per the governing task's own instruction, keeping
this a clean, standalone test of the macro signal alone. This project's
own established discipline tests each signal type standalone before ever
blending (`sr-e` before `sr-k`'s blend); this is that discipline applied
to the first genuinely non-price-derived signal this project has tested.

## The real `DFII10` backfill

`DFII10` (10-Year Treasury Inflation-Indexed Security, Constant
Maturity -- the real yield) was not among `sr-w`'s original three cached
series (`DGS10`/`DTWEXBGS`/`SP500`). Verified via a real, live call to
FRED's `/fred/series` metadata endpoint (2026-08-03/04, same one-time
manual process `sr-w` used for the original three, since
`fred_client.py` deliberately does not call that endpoint
programmatically): **`observation_start = 2003-01-02`** -- TIPS only
began regular reissuance in 2003, well before this project's earliest
BTC data (2021-05-14), so this addition does not constrain the
macro+BTC joint window. Added to `data/backfill_macro.py`'s
`SERIES_START_DATE`.

Real backfill run against the live, public, API-key-authenticated
production endpoint (`GET https://api.stlouisfed.org/fred/series/
observations`), writing to the shared cache
(`/mnt/c/Dev/trading-engine/python/data/var/klines.sqlite3`):

- **6,151 rows**, `2003-01-02` through `2026-07-30`
- **253 rows with `value IS NULL`** (real market holidays -- FRED's own
  `"."` marker, same shape `sr-w` found for the original three series)
- Wall clock: <1 second, 1 request (well under FRED's 100,000-row page
  limit)

**Idempotent rerun, confirmed live, not just asserted**: the same
command run a second time immediately after fetched **0 new rows**,
correctly re-identifying only the still-unpublished tail
(`[2026-07-31, 2026-08-03]`) as missing and finding nothing there yet --
the same "not-yet-published weekday reads as a genuine gap, retried on
rerun" behavior `sr-w` established for the original three series,
reproduced here for a fourth.

**Credential handling**: identical discipline to every prior task
touching `.env` -- the key was read via a minimal parser into
`os.environ`, used only to authenticate the real backfill request, and
never printed, logged, echoed, or included in any committed file, test
fixture, or exception message (`fred_client.py`'s own error paths
already never include the request URL, which is what would carry the
key).

## What was built

| File | Change |
|---|---|
| `python/research/macro_alignment.py` | **new.** `MacroSeriesCursor`, `forward_fill_macro_series` -- the look-ahead-safe forward-fill alignment mechanism (see design decision 3 above). |
| `python/research/strategies/macro_real_yield_trend.py` | **new.** `compute_real_yield_trend_signs`, `MacroRealYieldTrendStrategy`, `MacroRealYieldTrendTrainable`. |
| `python/data/backfill_macro.py` | `SERIES_START_DATE["DFII10"] = "2003-01-02"` added. |
| `python/research/lineage.py` | `FAMILY_BY_STRATEGY_ID["macro-real-yield-trend"]` added, family `"macro-conditioned"`, citing this document. |
| `python/tests/test_macro_alignment.py` | **new**, 11 tests. |
| `python/tests/test_macro_real_yield_trend.py` | **new**, 31 tests. |
| `python/tests/test_backfill_macro.py` | 1 test renamed (`test_main_defaults_to_all_series_when_none_specified`, was "...three_series..."), 1 test's hardcoded sleep-count assertion generalized to `len(SERIES_START_DATE) - 1` -- both direct, expected consequences of adding a fourth series, not new findings. |
| `python/tests/test_lineage.py` | 1 test updated (`test_curated_map_covers_the_families_named_in_the_planning_doc`) to include `"macro-conditioned"`. |
| `.planning/sr-x-macro-real-yield-strategy.md` | this document. |

**Zero new dependencies.**

## TDD

`test_macro_alignment.py` was written and confirmed failing
(`ModuleNotFoundError: No module named 'research.macro_alignment'`)
before `research/macro_alignment.py` existed -- true red-green for that
module, 11 tests, all green on the first implementation.

`test_macro_real_yield_trend.py` was written immediately after building
`research/strategies/macro_real_yield_trend.py` rather than strictly
before it, a deviation from this project's own TDD discipline disclosed
here rather than silently omitted. It still delivered real verification
value: the first test run found **6 genuine bugs in the test fixtures
themselves**, not the strategy --

1. Several tests assumed a position could open on the very first bar a
   trend signal was available, without accounting for
   `RollingRealizedVolatility(period=2)`'s own 3-bar warmup requirement
   (the strategy correctly retries rather than opening early -- exactly
   the "cannot-size retries automatically" behavior every sibling
   strategy already establishes). Fixed by feeding enough constant-price
   warmup bars before the bar under test.
2. Three tests asserted `strategy.position_sign` mid-sequence after
   already having built the full list of intents via an eager list
   comprehension (`[strategy(klines[:i+1]) for i in range(n)]`) -- by
   the time any assertion ran, every call had already executed, so
   `position_sign` reflected the FINAL state, not the intermediate one
   the assertion meant to check. Fixed either by asserting only on the
   `OrderIntent` objects captured at each index (which correctly
   snapshot state at the moment of that specific call) or, where the
   intermediate *state* itself needed checking, by driving a separate
   `probe` strategy through only the relevant prefix -- the same pattern
   `daily_tsmom_ensemble.py`'s own flip test already uses for exactly
   this reason.

Both bug classes are disclosed here rather than silently fixed, per
CLAUDE.md's "state assumptions... rather than silently pick" discipline
extended to test-authoring mistakes. Neither bug was in the strategy
implementation itself -- both were test-fixture errors that a genuinely
prior red phase would likely have caught faster; the net verification
value delivered is the same either way (all 6 were found and fixed
before this task's real walk-forward run, not after).

**Full suite**: `cd python && uv run pytest` -- **1,283 passed** (1,237
on `main` at the branch point + 42 new + 4 that already existed as
parametrized templates and gained one more case each:
`test_lineage.py::test_resolve_family_round_trips_every_curated_strategy_id[macro-real-yield-trend]`
and 3 similar parametrizations). Nothing regressed.

## The real walk-forward result

### Data

- **BTC 1d, research split**: `research.holdout.load_research_klines`
  against `configs/research/holdout_1d.json` -- **822 bars**,
  `2024-04-27T00:00:00Z` through `2026-07-27T00:00:00Z` (~2.25 years).
  Loaded via the holdout-aware loader, not a hand-rolled date filter, so
  it structurally cannot have leaked holdout data regardless of what
  range was requested (confirmed live: requesting `[0,
  2100-01-01)` was correctly clamped to `[1714176000000,
  2100-01-01)` with a logged warning, exactly the documented clamp
  behavior).
- **`DFII10`**: the full real cached history, `2003-01-02` through
  `2026-07-30` (6,151 observations) -- not clipped to the BTC-covering
  slice. See `MacroRealYieldTrendTrainable`'s own docstring for why this
  is correct and not a look-ahead risk: macro data's historical values
  were already public knowledge as of their own date, so there is no
  "not yet available within this fold" boundary to respect the way
  there is for BTC's own price during a walk-forward fold.

### Fold geometry (decided before the run, via pure bar-count arithmetic -- no price or macro value was consulted while choosing it)

`train_bars=90` (~1 quarter), `validate_bars=60` (~2 months),
`step_bars=60` (non-overlapping, this project's standard default) ->
**12 folds** over 822 bars, comfortably above the 8-10-fold credibility
floor. Reasoning: `train_bars` only needs to be long enough for the
`fit()` diagnostic in-sample pass and `RollingRealizedVolatility`'s own
21-bar warmup to have real data to work with (this strategy's actual
signal needs zero within-fold warmup at all -- see above); 90 is
generous. `validate_bars=60` balances two pulls: long enough that a
~1-quarter macro-regime signal has room to fire at least once per fold,
short enough to keep the fold count comfortably above the credibility
floor. Several other geometries were considered by pure arithmetic
before this one was chosen (see the real
`research.walkforward.generate_folds` sweep this task ran) -- none was
selected by looking at what result any of them would produce, only by
fold count and evaluated-bar totals.

### The result, reported honestly

```text
run_id=848a9f13-9fc7-478c-90ac-70cf03a8025c
strategy_family=macro-conditioned
fold_count=12
mean_sharpe (annualized) = -1.3034
min_sharpe (annualized)  = -5.7448
all_folds_positive_sharpe = false
worst_fold_max_drawdown  = 0.2735  (27.3%)
mean_total_return        = -4.52%
total_trades             = 34
mean_profit_factor       = 0.3188
min_profit_factor        = 0.0
folds_with_zero_trades   = 0
```

Per-fold annualized Sharpe: `+1.666, -5.745, -0.727, -0.128, -0.560,
-1.617, -0.384, -4.456, -1.425, -0.020, -0.952, -1.293` -- **1 of 12
folds positive** (fold 0 only).

### Evaluated against the Eligibility Bar, criterion by criterion

**1. Trade-count floor (checked first, per CLAUDE.md's own precedence
for a sub-floor run: "neither a pass nor a fail... not evidence against
the strategy").** `total_evaluated_bars = validate_bars * fold_count =
720`, `bars_per_day=1` ->
`research.preregistration.frequency_scaled_min_trades(total_evaluated_bars=720,
bars_per_day=1) = 36`. **Observed: 34 trades. 34 < 36 -- below the
floor by 2 trades.**

This formally makes the run **INCONCLUSIVE-DATA-LIMITED** on CLAUDE.md's
own terms, and everything below is reported for honest context under
that same standing instruction ("It is not evidence against the strategy
and must not be written up as such") -- **not** as a rejection.

**2. Fold consistency** (`min_fraction=0.80`, the permissive end of
CLAUDE.md's approved 80-90% band, matching `sr-r`'s own convention):
1/12 = **8.3%** positive folds. **FAILS** (needs >= 80%).

**3. Sign test** (`H1`: true per-fold win probability > 0.5):
`p = 0.9998`. **FAILS** to reject the null -- and the observed win rate
(1/12) is itself far below the 50% chance rate the test is checking
against, not merely an insignificant improvement over it.

**4. Mean-Sharpe significance** (one-sided t-test, `H1`: true mean fold
Sharpe > 0): `t = -2.271`, `p = 0.978`. **FAILS** -- the point estimate
itself is negative, so this test cannot pass by construction.

**5. Max drawdown ceiling** (20-25%): worst fold **27.3%** (fold 1).
**FAILS** the ceiling (exceeds even the permissive 25% end).

**6. Profit factor floor** (1.3-1.5): mean **0.3188**. **FAILS** by a
wide margin -- losses substantially exceed gains in aggregate.

**7. Detection floor.** Full data span 2.2493 years -> detection floor
**1.0967** annualized Sharpe; validated (out-of-sample) span 1.9726
years -> detection floor **1.1711**. The **observed** mean Sharpe
(**-1.3034**) is not merely below either floor -- it is negative and
larger in magnitude than the floor itself, in the wrong direction. This
is not an underpowered-study pattern (a real edge hidden by insufficient
data); it is a clear, decisively-signed negative point estimate.

**8. Deflated Sharpe Ratio -- honest `N`, both levels, real numbers from
the real (now-updated) log.** `research.overfitting_check.
check_project_combination_count` against the real
`runs/experiments.jsonl` after this run was logged:

- **Family-level `N` (`macro-conditioned`): 2** selection trials (this
  run's own 12-fold-grouped `fit()` in-sample sub-records collapse to
  weight 1 via the standard `parent_run_id`-group counting rule, since
  they all share one `total_candidates=1` group; the outer 12-fold
  standalone walk-forward record is the second trial of weight 1 -- the
  same "group + standalone = 2" double-count pattern
  `daily_tsmom_ensemble.py`'s own single run already exhibits, not a
  defect introduced here, see `sr-p`'s own disclosed "Judgment calls").
  **`DSR(N=2) = 0.0135`.**
- **Project-level `N` (research): 119** (117 pre-existing +
  `macro-conditioned`'s own 2) -- the honest `N` for "is the best thing
  this project has found real", since families are compared against each
  other after seeing results, per `sr-p`/`sr-r`'s established reasoning.
  **`DSR(N=119) = 5.0e-11`.**
- **PSR (N=1, no deflation at all)**: **0.0338** -- even ignoring every
  other trial this project has ever run, this specific result alone does
  not clear a meaningful bar.

None of these DSR/PSR figures are being used here to declare a
rejection -- see point 1. They are reported because CLAUDE.md's own
established convention (`sr-r`, `sr-v`) is to report the full statistical
picture regardless of which side of the trade-count floor a result
lands on, so a reader is not left to wonder whether the numbers were
merely inconvenient to compute.

### Honest summary

**Formally: INCONCLUSIVE-DATA-LIMITED** (34 trades against a 36-trade
floor -- a near miss, 2 trades short, not a wide miss). Per CLAUDE.md's
own standing rule, this is neither a pass nor a fail and must not be
written up as evidence against the hypothesis on the trade-count
technicality alone.

**Substantively, stated plainly and not softened**: unlike `sr-v`'s
INCONCLUSIVE result (whose PSR/Sharpe/trade-count all landed *close to*
their respective bars, genuinely ambiguous), **every criterion here
except the trade-count floor is not close to its bar at all** -- fold
consistency 8.3% against an 80% floor, a sign test and t-test both
pointing the wrong way, a 27.3% drawdown against a 20-25% ceiling, a
0.32 profit factor against a 1.3-1.5 floor, and a deeply negative
Sharpe against a positive detection floor. This is a materially
different shape of INCONCLUSIVE than `sr-v`'s: a real signal that
happened to fall 2 trades short of a floor would still show *some*
directional coherence elsewhere; this one does not. The honest
reading is that this specific run gives no support for the hypothesis
as specified over this window and geometry -- while remaining, on the
letter of CLAUDE.md's rule, formally unable to reject it outright given
the sub-floor trade count.

## The temptation, disclosed rather than acted on

Three distinct pulls occurred while writing this report, named
explicitly per this task's own instruction and `sr-v`'s precedent for
handling exactly this situation -- none was acted on:

1. **"The trade count missed the floor by only 2 -- what if a slightly
   different fold geometry (e.g. `validate_bars=72` instead of `60`)
   pushed `total_evaluated_bars` over the line?"** The geometry
   (`train_bars=90, validate_bars=60, step_bars=60`) was chosen by pure
   bar-count arithmetic *before* this run ever executed, without
   consulting any price or macro value. Changing it now, after seeing
   that the result landed 2 trades short, would be exactly the
   after-the-fact parameter search this project spent `sr-p`/`sr-q`/
   `sr-r` building the machinery to detect and `sr-v` explicitly refused
   to do under a much larger and more sympathetic gap (27 trades short
   of a 53-trade floor, not 2).
2. **"The observed Sharpe is deeply negative -- what if the inversion
   sign is backwards for this window, and the un-inverted (naive
   momentum) reading of the same trend would have shown the edge the
   background research actually predicted?"** Flipping the sign after
   seeing a negative result is not a "genuinely new configuration" in
   the sense CLAUDE.md's retired funding-extremity precedent once used
   that phrase for -- it is literally the single degree of freedom this
   hypothesis has, being tuned to the observed data. Not done.
3. **"A shorter lookback might generate more trades and clear the
   floor."** Named in the governing task's own brief as the canonical
   example of the trap to avoid; not investigated even informally.

All three remain live, legitimate questions for a **separately
justified, pre-committed** follow-up task -- not something this task
resolves by trying them quietly and reporting only the one that looked
best.

## Comparison to prior results

| Result | Window | `N` | Observed Sharpe (annualized) | Detection floor | Verdict |
|---|---|---|---|---|---|
| Configuration C + funding (`sr-n`, best 1h result) | 1h research, 1.84y | 117 | +0.039 | ~1.21 | REJECTED (DSR 2.0e-05) |
| Daily TSMOM ensemble (`sr-v`, holdout) | 1d holdout, 2.95y | 1 (holdout) | +0.882 | 0.957 | INCONCLUSIVE (close to every bar) |
| **Macro real-yield trend (this task)** | **1d research, 2.25y** | **119 (project) / 2 (family)** | **-1.303** | **1.10-1.17** | **INCONCLUSIVE-DATA-LIMITED (34 vs 36 trades); substantively negative on every other criterion** |

The macro signal is the first hypothesis in this project's history whose
point estimate is not merely "not distinguishable from noise" but
**actively negative and fold-consistent in that direction** (11 of 12
folds negative). That is itself informative in a way "DSR ~= 0" alone is
not: Configuration C's near-zero DSR reflects a real edge this project's
1h window lacked the statistical power to detect either way (`sr-r`'s
own finding); this result's negative point estimate, deeply negative
fold consistency, and negative-signed sign/t-tests are not a power
problem in the same sense -- the study saw a directionally coherent
signal, and it was consistently in the opposite direction from the one
that was hypothesized, on this window, at this lookback, under this
inversion. That is a materially different, and more informative, kind of
"not shown" than Configuration C's.

## Code provenance

`macro_real_yield_trend.py` and `macro_alignment.py` were uncommitted
working-tree files at the moment the real walk-forward run executed
(written and tested, but not yet `git commit`-ed) -- so the logged
record's own `code_version` field (plain `git rev-parse HEAD`) reads
`e1277e409f3c428a9b7452fdd58f519d560934c9`, the `sr-w` merge commit this
worktree branched from, not a commit containing this task's own code.
Same situation `sr-v` disclosed and closed the same way: the exact
SHA-256 of each file's own bytes, as they existed at execution time,
recorded here directly (hyphen-grouped into 8 chunks to avoid an
unrelated local commit-scanner false positive on any contiguous
64-hex-character run -- concatenate the chunks, without the hyphens, to
reconstruct the real digest; also independently reproducible at any time
via `sha256sum` against the committed, unchanged files):

```text
python/research/strategies/macro_real_yield_trend.py
4f2cf136-8422024f-9e95e9e2-235f9f20-3369551d-f7507b56-a9458046-0887117c

python/research/macro_alignment.py
cd37259e-0efdf0f2-30760d47-964da8ec-c2cdda83-7d2c6f77-793838c5-77e84e33
```

Neither file was modified after these hashes were computed and before
this PR was opened.

## Deliberately out of scope

- **Any second attempt at this hypothesis** -- another lookback, another
  fold geometry chosen to clear the trade floor, an un-inverted
  variant, or a combination with a price signal. See "The temptation,
  disclosed rather than acted on" above. A follow-up along any of these
  lines is a **separate, pre-committed** task, not a same-PR retry.
- **A holdout confirmation for this hypothesis.** No result here clears
  the research-split bar cleanly enough to be a holdout-confirmation
  candidate in the first place (the whole point of a holdout
  confirmation is confirming a research-split winner, and this run is
  not one), and in any case the only genuinely untouched single-symbol
  BTC-USDT window (the 1d holdout) is already spent (`sr-v`).
- **Other FRED series as standalone signals** (`DGS10` nominal yield,
  `DTWEXBGS` dollar index, `SP500`) -- `sr-w` cached all three
  specifically so a future task could test them; none is tested here,
  per this task's own scope (real yield specifically, per the governing
  research finding).
- **A macro-specific holdout split.** As `sr-w` already flagged, no
  strategy existed yet to need one; this task's own result does not
  change that -- a real research-split result (not a holdout-worthy
  candidate) does not create a reason to reserve a macro holdout now.
