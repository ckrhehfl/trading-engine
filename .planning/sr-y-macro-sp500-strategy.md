# Strategy Research Task Y: the macro-sp500-trend strategy (real, honest result)

## Scope note

This task is the direct structural sibling of Strategy Research Task X
(`.planning/sr-x-macro-real-yield-strategy.md`): the second, and — per
this task's own governing brief, an explicit human decision — **the
last planned macro-data attempt** before this project's research line
either pivots to on-chain data or pauses. It tests the S&P 500 (`SP500`,
FRED)'s own trend, via ordinary iterative walk-forward research on the
same untouched BTC 1d **research** split `sr-x` used (2024-04-27
onward) — explicitly not the spent `1d` holdout, and explicitly not a
pre-registration (that ceremony is reserved for spending an
already-designated, never-before-touched holdout; the research split is
fair game for ordinary research).

Prerequisites read in full first: CLAUDE.md ("Strategy Research
Methodology," "Strategy Attempts So Far" including the `sr-x` entry this
task mirrors), `.planning/sr-x-macro-real-yield-strategy.md` (the
sibling this task's code, tests, and process structure closely follow),
`.planning/sr-w-macro-data-pipeline.md` (confirms `SP500` was cached
alongside `DGS10`/`DTWEXBGS` specifically so a future task could test
it), `python/research/strategies/macro_real_yield_trend.py` and
`python/research/macro_alignment.py` (read closely per this task's own
governing brief — this task reuses the alignment infrastructure
unmodified and mirrors the strategy module's shape).

## Why this task exists

A deep credibility-graded research pass (cited in this task's own
governing brief) ranked real yield as the single most-supported macro
variable (tested by `sr-x`, INCONCLUSIVE-DATA-LIMITED) and S&P 500 /
risk-asset correlation as the second most-supported — multiple
institutional and peer-reviewed sources found real, if regime-dependent,
0.4-0.7 correlation between BTC and risk assets (Nasdaq/S&P) since 2020,
strengthening during risk-off/stress periods. Dollar index (`DTWEXBGS`)
was ranked weaker and more sporadic than conventional wisdom suggests per
a peer-reviewed wavelet-analysis paper the same research pass found —
which is why this task tests S&P 500, not the dollar index.

## The hypothesis (decided at the task's outset, not redesigned here)

At each daily BTC bar, look at the trend in `SP500` over a trailing
**63-trading-day lookback** — the SAME lookback `sr-x` used, reused
deliberately for direct comparability between the two macro attempts
and to avoid introducing a second "which lookback" decision (see
"Design decisions" below). **NOT inverted**: rising S&P 500 (risk-on) ->
BTC-bullish (a LONG signal); falling S&P 500 (risk-off) -> BTC-bearish
(a SHORT signal). This is a same-direction co-movement hypothesis,
the opposite structural shape from `sr-x`'s inverse real-yield
relationship. Sizing via `research/strategies/volatility_targeting.py`
unmodified (20%-annualized-vol-target convention). No ADX gate, no ATR
stop, no funding signal, no combination with price-based momentum or
with the sibling's real-yield signal — a clean, standalone test of the
S&P 500 signal alone.

## Design decisions, with reasoning — written before the real run

### 1. Lookback: 63 trading days, reused from the sibling, not re-derived

`fit()` performs **no search whatsoever** — `total_candidates: 1`, same
zero-fitted-parameter discipline as every strategy in this codebase.
Unlike `sr-x`, which derived 63 from first principles (the middle value
of `daily_tsmom_ensemble.py`'s own Moskowitz-Ooi-Pedersen 21/63/126/252
set, chosen for real-yield-specific reasons), this task's own governing
brief is explicit that the SAME 63 should be reused here, "chosen
deliberately for direct comparability between the two macro attempts and
to avoid introducing a second 'which lookback' decision." This is a
deliberate, disclosed departure from re-deriving each new lookback from
first principles: the point is that any difference between this run's
result and `sr-x`'s isolates the effect of *which macro variable* (S&P
500 vs. real yield) and *which sign convention* (same-direction vs.
inverse), not a third, confounding "and also a different lookback"
variable. `macro_real_yield_trend.py`'s own module docstring still
carries the full first-principles reasoning for why 63 specifically (not
21, not 126/252) is defensible for a macro-regime signal generally — not
re-derived a second time here.

### 2. The sign, verified explicitly — the single highest-risk line in this task

The governing brief flagged this as the one place a copy-paste from the
sibling could silently introduce the sibling's inversion by mistake.
`MacroSp500TrendStrategy.__call__` reads:

```python
btc_signal = raw_trend_sign  # NOT INVERTED -- see module docstring
```

— a direct, un-negated assignment, deliberately NOT
`btc_signal = -raw_trend_sign` (the sibling's own line). Verified three
ways, not just by inspection:

1. **Diff against the sibling.** `macro_real_yield_trend.py` line 332 is
   `btc_signal = -raw_trend_sign  # INVERSION`; this module's
   corresponding line has no negation. A direct visual diff of the two
   `__call__` methods confirms this is the only semantic difference
   between the two strategies' signal-to-side mapping.
2. **`TestNonInversionAndDirection`**, this module's own test class
   (mirroring `test_macro_real_yield_trend.py::TestInversionAndDirection`
   structurally, with every assertion swapped to match the opposite
   mapping): `test_a_rising_sp500_trend_produces_a_long_signal` feeds a
   `+1` trend observation and asserts `Side.LONG`; `test_a_falling_
   sp500_trend_produces_a_short_signal` feeds `-1` and asserts
   `Side.SHORT`. Both pass. A `test_flip_from_short_to_long_when_the_
   underlying_trend_reverses` test additionally drives a real sign
   reversal (falling -> opens SHORT; then rising -> flips LONG) end to
   end, confirming the mapping holds under the strategy's own
   edge-triggered order-emission logic, not just in isolation.
3. **Real-data sanity check**, run before the real walk-forward (see
   below): across the `SP500` trend series restricted to
   2024-01-01 onward (647 real trading-day observations at
   lookback=63), the sign distribution is 553 positive / 94 negative (10
   sign flips) — consistent with the real, well-known fact that the S&P
   500 has been in a predominantly rising trend since 2024. Under the
   NON-inverted mapping this predicts the strategy spends most of its
   time LONG, which is exactly what the real walk-forward run shows (see
   below: 7 of 12 folds hold a position opened by a rising-trend LONG
   signal, matching the sign distribution's own imbalance) — an
   independent, data-grounded cross-check that the mapping direction is
   the one actually implemented, not just the one the tests assert.

### 3. The alignment helper: reused unmodified, exactly as instructed

`research/macro_alignment.py` (`MacroSeriesCursor`) is imported and used
completely unmodified — zero changes to that file in this task's diff.
The governing brief was explicit that this infrastructure is not to be
reinvented, and `SP500` has the exact same shape of gaps (weekends,
holidays represented as real `None`-valued FRED rows) that `DFII10`
had, so the "forward-fill the last known REAL observation dated on or
before the BTC bar's own calendar date" rule applies identically. No new
alignment tests were written — `sr-x`'s own `test_macro_alignment.py`
(11 tests, all still passing, unchanged) already proves this mechanism's
look-ahead safety for any FRED-shaped series, `SP500` included; this
task's own tests instead verify *correct use* of the cursor (a `SP500`
trend row dated day 0 forward-fills, a later-dated flip only becomes
visible once the cursor is fed a kline that reaches it), not re-proving
the cursor's own guarantee a second time.

### 4. `compute_sp500_trend_signs`: a new, self-contained function, not a cross-import

`macro_sp500_trend.py` defines its own `compute_sp500_trend_signs`,
structurally identical to (byte-for-byte the same algorithm as)
`macro_real_yield_trend.compute_real_yield_trend_signs`, rather than
importing and reusing that function directly. This was a deliberate
choice, not an oversight: every strategy module in this codebase
(`daily_tsmom_ensemble.py`, `ensemble_momentum.py`,
`macro_real_yield_trend.py` itself) owns its own self-contained
signal-computation logic rather than cross-importing near-identical sign
arithmetic from a sibling strategy module — `research/macro_alignment.py`
is genuinely shared, cross-series-alignment *infrastructure* (the
governing brief's own "reuse unmodified" instruction targets exactly
this), whereas a strategy's own trend-sign computation is that
strategy's private signal logic, matching the established convention.
Reusing `compute_real_yield_trend_signs` directly (it is generic in
implementation despite its real-yield-specific name) was considered and
rejected: it would leave `macro_sp500_trend.py` importing a function
named for a different macro variable, and it would touch
`macro_real_yield_trend.py`'s effective API surface (adding an implicit
dependent) for a module CLAUDE.md's own "touch only what the task
requires" discipline says should stay untouched. `macro_real_yield_
trend.py` has zero changes in this task's diff.

### 5. Sizing, order emission, zero-trend tie-break: identical to the sibling, not re-justified

`RollingRealizedVolatility`/`compute_vol_scalar` (unmodified), the same
`base_quantity * vol_scalar` composition, the same deliberate omission of
an `abs(signal)` conviction multiplier, the same Option B
(only-on-sign-change) order emission, the same automatic-retry-on-next-bar
behavior when a transition can't be sized, and the same FLAT tie-break on
an exact-zero trend reading. None of these differ from the sibling; see
`macro_real_yield_trend.py`'s own module docstring for the full
reasoning behind each, not repeated a second time in this module's
docstring (which points back to it explicitly).

### 6. Deliberately absent

No ADX regime gate, no ATR stop/target, no risk:reward grid, no funding
signal, no combination with any BTC-price-derived momentum signal, and
critically **no combination with the sibling's real-yield signal** —
per the governing task's own instruction, a clean, standalone test of
the S&P 500 signal alone.

## `SP500` cache verification (no new backfill needed)

Verified directly against the real shared cache
(`python/data/var/klines.sqlite3`, gitignored, populated by
`data/backfill_macro.py`) before writing any code, per this task's own
instruction to check rather than assume:

```text
SP500:   2,610 rows, 2016-08-01 through 2026-07-31, 96 null (holiday) rows
DFII10:  6,151 rows, 2003-01-02 through 2026-07-30, 253 null rows
DGS10:   6,934 rows, 2000-01-03 through 2026-07-30, 287 null rows
DTWEXBGS: 5,365 rows, 2006-01-02 through 2026-07-24, 211 null rows
```

`SP500` was fully cached by `sr-w` (`data/backfill_macro.py`'s
`SERIES_START_DATE["SP500"] = "2016-08-01"`) specifically so a future
task could test it as a signal — confirmed still current, comfortably
covering the BTC research split (2024-04-27 through 2026-07-27) plus
far more than 63 trading days of warmup before it. **No backfill was run
for this task** — `FRED_API_KEY` was never read or needed.

## What was built

| File | Change |
|---|---|
| `python/research/strategies/macro_sp500_trend.py` | **new.** `compute_sp500_trend_signs`, `MacroSp500TrendStrategy`, `MacroSp500TrendTrainable` — structural sibling of `macro_real_yield_trend.py` with the corrected, non-inverted sign. |
| `python/research/lineage.py` | `FAMILY_BY_STRATEGY_ID["macro-sp500-trend"]` added, same `"macro-conditioned"` family as `macro-real-yield-trend`, citing this document. |
| `python/tests/test_macro_sp500_trend.py` | **new**, 32 tests, TDD (written and confirmed failing on `ModuleNotFoundError: No module named 'research.strategies.macro_sp500_trend'` before the strategy module existed — true red-green). |
| `.planning/sr-y-macro-sp500-strategy.md` | this document. |
| `CLAUDE.md` | "Strategy Attempts So Far" updated with this attempt. |

**Zero new dependencies. `research/macro_alignment.py` and
`research/strategies/macro_real_yield_trend.py` have zero changes.**

## TDD

`test_macro_sp500_trend.py` was written in full, and confirmed failing
(`ModuleNotFoundError`), before `research/strategies/macro_sp500_trend.py`
existed — true red-green for this module. All 32 tests passed on the
first implementation attempt (no fixture bugs of the kind `sr-x`
disclosed finding in its own first run — the sibling's file already
absorbed those lessons, e.g. every direction/flip test feeds >= 3
constant-price warmup bars before asserting on a trend-driven opening,
and every test that needs an intermediate `position_sign` reading uses a
separate `probe` strategy driven only through the relevant prefix rather
than reading state after an eager list comprehension has already run
every call).

Full suite: `cd python && uv run pytest` — **1,317 passed** (1,284 on
`main` at the branch point + 32 new + 1 pre-existing parametrized
template
(`test_lineage.py::test_resolve_family_round_trips_every_curated_strategy_id[macro-sp500-trend]`)
gaining one more case). Nothing regressed.

## A real, disclosed, non-hypothesis-related bug hit during execution

Matching `sr-v`/`sr-x`'s own precedent of disclosing execution bugs
honestly rather than silently fixing them: the real walk-forward driver
script constructed `MacroSp500TrendTrainable` without passing
`runs_path=` explicitly, so it fell back to
`experiment_log.DEFAULT_RUNS_PATH` ("runs/experiments.jsonl", relative
to cwd) instead of the real shared `runs/experiments.jsonl` at the
repository root (gitignored; the shared runtime data cache every prior
strategy-research task has logged to). The outer
`run_walk_forward(...)` call in the same script DID pass `runs_path=`
explicitly, so the single 12-fold aggregate record (the one that matters
for every metric reported below) landed in the correct shared log
immediately. Only the 12 **per-fold in-sample diagnostic** sub-records
(one `_log_candidate` call inside each fold's own `fit()`, mirroring
`macro-real-yield-trend`'s own 12 sub-records under its parent run) went
to a stray local file
(`python/runs/experiments.jsonl`, inside the gitignored worktree
`runs/` path) instead.

**Fix, not a re-run**: these 12 records are a byte-for-byte faithful
account of a real, already-computed, deterministic diagnostic (no search,
no randomness — `total_candidates: 1` — so a second logging destination
changes nothing about their content). Re-running the walk-forward to fix
a pure logging-destination bug would have created an unjustified second
real trial against research data for no statistical reason. Instead, the
12 stray lines were appended verbatim to the real shared
`runs/experiments.jsonl` (verified: `parent_run_id` on all 12 correctly
points at `e0abfeaa-...`, the real outer run already logged there,
`candidate_index=0`/`total_candidates=1` on all 12, matching the
sibling's own shape exactly), and the stray local file was deleted.

**Reproducibility of the appended records, without a git-tracked log
artifact.** `runs/` is deliberately `.gitignore`d project-wide (it is
the shared runtime data cache, not source) — no prior strategy-research
task (`sr-v` through `sr-x`) has ever committed a copy of
`runs/experiments.jsonl` or a slice of it to git, and this task does not
introduce that as a new precedent. The verification trail instead relies
on what this project already uses for run identity: each of the 12
appended records carries its own real `run_id`
(`9d5f680b-4867-4e49-b465-b71a06e4b68f` through
`38bbe556-805b-4444-a77a-b8e67251811b`, all with `parent_run_id` pointing
at the real outer run `e0abfeaa-cfb3-49b5-b247-955a54789baa`, matching
the sibling's own already-logged shape record-for-record), and — as one
further, concrete, independently reproducible check specific to this
disclosure — the SHA-256 of the 12 lines exactly as appended (concatenated
in file order, computed from the real shared log after the append):

```text
82996276-189578f4-f321b3bd-09cef39f-ce74c6fb-15a3ec54-9c598fd2-670c7b15
```

reproducible via
`sha256sum` (or the equivalent) against those 12 `run_id`s' lines in the
real `runs/experiments.jsonl` by anyone with access to that file — the
same "reproducible from the real artifact, not asserted" standard this
document's own code-provenance section (below) applies to the source
files.

**Consequence for trial accounting, corrected before any DSR/PSR number
below was computed**: before this fix, `research.overfitting_check.
check_project_combination_count` undercounted the `macro-conditioned`
family at `selection_trials=3` (missing the sp500 fit-group's own
weight-1 contribution entirely) and the project-level research `N` at
120. After the fix: **family N = 4, project N = 121** — both used
throughout this document. This is exactly the fail-closed-`N`
consequence CLAUDE.md's amended Eligibility Bar clause 2 was written to
prevent (understating `N` would have made this task's own DSR
artificially, incorrectly generous), so the fix was made and verified
before any figure below was read off.

## The real walk-forward result

### Data

- **BTC 1d, research split**: `research.holdout.load_research_klines`
  against `configs/research/holdout_1d.json` — **822 bars**,
  `2024-04-27T00:00:00Z` through `2026-07-27T00:00:00Z` — identical
  count and range to `sr-x`'s own run, re-verified live against the
  current cache rather than assumed (BingX `1d` retention has not moved
  the research split's boundary since `sr-x`).
- **`SP500`**: the full real cached history, `2016-08-01` through
  `2026-07-31` (2,610 observations) — not clipped to the
  BTC-covering slice, same reasoning as `sr-x`'s `DFII10` load (macro
  data's historical values were already public knowledge as of their own
  date, so there is no "not yet available within this fold" boundary to
  respect the way there is for BTC's own price during a walk-forward
  fold).

### Fold geometry: identical to the sibling, per this task's own instruction

`train_bars=90, validate_bars=60, step_bars=60` -> **12 folds** over 822
bars — the exact same geometry `sr-x` used, for direct comparability
(this task's governing brief: "Use the same fold geometry the sibling
task used... for direct comparability"). Re-verified accurate against
the real, current data before the run (822 bars, same range as `sr-x`'s
own load) rather than assumed.

### The result, reported honestly

```text
run_id=e0abfeaa-cfb3-49b5-b247-955a54789baa
strategy_family=macro-conditioned
fold_count=12
mean_sharpe (annualized) = -0.2837
min_sharpe (annualized)  = -5.4505
all_folds_positive_sharpe = false
worst_fold_max_drawdown  = 0.1438  (14.38%, fold 3)
mean_total_return        = -0.274%
total_trades             = 19
mean_profit_factor       = 0.2065
min_profit_factor        = 0.0
folds_with_zero_trades   = 0
```

Per-fold annualized Sharpe: `+1.6662, +5.8418, +0.8364, -1.2120, -5.4505,
+2.1014, -0.3837, -4.4565, +1.4508, -5.3544, +0.1523, +1.4042` — **7 of
12 folds positive** (folds 0, 1, 2, 5, 8, 10, 11).

Per-fold profit factor: `None, None, None, 0.0763, 0.0, None, 0.0, 0.0,
None, 0.0258, 1.1367, None` (`None` means zero losing closed trades in
that fold — CLAUDE.md's own documented interpretation: "not evidence of
a poor risk/reward ratio to reject," not a missing measurement).
`mean_profit_factor` above (0.2065) is the mean of the six **defined**
values only, per `metrics.metrics._profit_factor`'s own `None`
convention — four of those six folds (4, 6, 7, and effectively 9) had a
losing trade with essentially no offsetting winner in the same fold
(`pf=0.0` three times), which is what pulls the mean this low; the six
`None` folds each held a single, wholly winning trade for their entire
duration and are excluded from that average rather than counted as
zero.

### Evaluated against the Eligibility Bar, criterion by criterion

**1. Trade-count floor (checked first, per CLAUDE.md's own precedence: "neither a pass nor a fail... not evidence against the strategy").**
`total_evaluated_bars = validate_bars * fold_count = 720`,
`bars_per_day=1` ->
`research.preregistration.frequency_scaled_min_trades(total_evaluated_bars=720,
bars_per_day=1) = 36`. **Observed: 19 trades. 19 < 36 — below the floor
by 17 trades**, a wider miss than `sr-x`'s own 2-trade shortfall.

This formally makes the run **INCONCLUSIVE-DATA-LIMITED** on CLAUDE.md's
own terms — the same verdict category as `sr-x`, reached by a wider
margin on this specific criterion. Everything below is reported for
honest context under that same standing instruction ("It is not evidence
against the strategy and must not be written up as such") — **not** as a
rejection, and **not** as evidence the strategy is closer to a real edge
just because some of the descriptive numbers below look less extreme
than `sr-x`'s. A sub-floor run is not powered to support either reading.

**2. Fold consistency** (`min_fraction=0.80`): 7/12 = **58.3%** positive
folds. **FAILS** (needs >= 80%).

**3. Sign test** (`H1`: true per-fold win probability > 0.5):
`p = 0.3872`. **FAILS** to reject the null — 7-of-12 is well within what
12 fair coin flips would produce by chance (P(X>=7|n=12,p=0.5)=38.7%),
neither confirming nor disconfirming an edge.

**4. Mean-Sharpe significance** (one-sided t-test, `H1`: true mean fold
Sharpe > 0): `t` corresponding to `p = 0.6119`. **FAILS** — the point
estimate is negative, so this test cannot pass by construction, but the
p-value is far from decisive in either direction (contrast `sr-x`'s
`p = 0.978`).

**5. Max drawdown ceiling** (20-25%): worst fold **14.38%** (fold 3).
**PASSES** comfortably — the one criterion (besides the trade floor
itself) where this run's descriptive numbers differ qualitatively from
`sr-x`'s (27.3%, over the ceiling).

**6. Profit factor floor** (1.3-1.5): mean **0.2065**. **FAILS** by a
wide margin — wider than `sr-x`'s own 0.3188 on this specific metric
(see the per-fold breakdown above for why: several folds' single losing
trade lost essentially everything with nothing to offset it).

**7. Detection floor.** Full data span 2.2493 years (identical window to
`sr-x`) -> detection floor **1.0967** annualized Sharpe; validated span
1.9726 years -> detection floor **1.1711**. The observed mean Sharpe
(**-0.2837**) is below both floors and negative, but — unlike `sr-x`'s
**-1.3034**, whose magnitude exceeded the floor itself in the wrong
direction — this point estimate's magnitude is smaller than the floor,
consistent with an underpowered study that cannot distinguish a small
true effect (positive, negative, or zero) from noise, rather than a
decisively-signed negative result. This is a numerical observation about
where the point estimate falls relative to the floor, not a claim about
what the true effect is — the run is not powered to make that claim
either way.

**8. Deflated Sharpe Ratio — honest `N`, both levels, real numbers from
the real (corrected) log**, via `research.overfitting_check.
check_project_combination_count` and `research/retrospective.py`'s
`build_retrospective` (the same tool `sr-r` built and this project's
retrospective evaluations use) against the real, corrected
`runs/experiments.jsonl`:

- **Family-level `N` (`macro-conditioned`): 4** selection trials — 2
  from `macro-real-yield-trend` (unchanged from `sr-x`'s own count) + 2
  from this run (`macro-sp500-trend`'s own fit-group + its standalone
  12-fold record, the same "group + standalone = 2" pattern every prior
  zero-search strategy in this project exhibits). **`DSR(N=4) =
  0.1374`.**
- **Project-level `N` (research): 121** (117 pre-`sr-x` + 2 from
  `macro-real-yield-trend` + 2 from `macro-sp500-trend`). **`DSR(N=121) =
  2.20e-07`.**
- **PSR (N=1, no deflation at all)**: **0.3453** — noticeably higher
  than `sr-x`'s own PSR(N=1) of 0.0338, consistent with this run's less
  extreme point estimate, but still well short of the 0.95 threshold
  even before any deflation for search.

**A disclosed side effect on `sr-x`'s own retrospective numbers**:
because this task's real run adds two new trials to the shared
`macro-conditioned` family, re-running `check_project_combination_count`
now also changes `macro-real-yield-trend`'s own family-level and
project-level DSR from what `sr-x` originally reported (`DSR(N=2) =
0.0135`, `DSR(N=119) = 5.0e-11`) to `DSR(N=4) = 0.00586`,
`DSR(N=121) = 4.76e-11` — both **lower**, monotonically, exactly as
CLAUDE.md's own standing rule about the (unrelated, spent) 1h window
predicts ("every additional trial raises the `N` any future winner must
be deflated against... can only lower the DSR of any given result, never
raise it"). Named here as a concrete, real illustration of that dynamic
in action, not because it changes `sr-x`'s own already-decisive
INCONCLUSIVE-DATA-LIMITED verdict in any practical sense.

None of these DSR/PSR figures are being used here to declare a pass or a
rejection — see point 1. They are reported because CLAUDE.md's own
established convention (`sr-r`, `sr-v`, `sr-x`) is to report the full
statistical picture regardless of which side of the trade-count floor a
result lands on.

### Honest summary

**Formally: INCONCLUSIVE-DATA-LIMITED** (19 trades against a 36-trade
floor — a wider miss than `sr-x`'s 2-trade near-miss, not a close call).
Per CLAUDE.md's own standing rule, this is neither a pass nor a fail and
must not be written up as evidence against the hypothesis on the
trade-count technicality alone.

**The remaining metrics are reported descriptively only, not as a
directional conclusion.** Stated as a purely factual/statistical
observation, not a verdict: several of this run's descriptive numbers
read less extreme than `sr-x`'s own (fold consistency 58.3% vs. 8.3%;
sign-test/t-test p-values near 0.4-0.6 vs. near-1.0; max drawdown 14.4%
vs. 27.3%, comfortably inside the ceiling; a point estimate whose
magnitude sits below its own detection floor rather than decisively
past it) — while one reads more extreme (mean profit factor 0.2065 vs.
0.3188). This mixed, non-uniform pattern is recorded here as a numeric
fact for whoever designs a follow-up task to have the full picture. It
is explicitly **not**, and must not be read as, a claim that this
hypothesis is closer to (or further from) a real edge than the sibling's
— the run is 17 trades short of the floor that would be needed to make
either claim, a wider shortfall than `sr-x`'s, and CLAUDE.md's own
standing rule that a sub-floor result is "not evidence against the
strategy" applies with at least equal force to "not evidence for it, or
for a comparative ranking against another sub-floor result."

## The temptation, disclosed rather than acted on

Three distinct pulls, named explicitly per this task's own instruction
and `sr-x`/`sr-v`'s own precedent for handling exactly this situation —
none was acted on:

1. **"19 trades is well short of 36 — what if a different fold geometry
   generated more trades?"** The geometry was fixed by this task's own
   governing brief specifically *to match the sibling's*, for direct
   comparability — not chosen freely and therefore not something to
   revisit after seeing the trade count come in low. Changing it now
   would be exactly the after-the-fact parameter search this project's
   `sr-p`/`sr-q`/`sr-r` machinery exists to detect, and would also
   destroy the direct-comparability property the fixed geometry was
   chosen to preserve.
2. **"The observed Sharpe is negative — what if the un-inverted mapping
   is wrong and the INVERTED (real-yield-style) reading would show the
   edge instead?"** Flipping the sign after seeing a negative result
   would tune the single degree of freedom this hypothesis has to the
   observed data — precisely the trap named in this task's own governing
   brief ("do not copy the inversion logic by reflex, that would
   silently encode the wrong hypothesis") turned inside-out into a
   post-hoc correction. Not done.
3. **"A shorter lookback might generate more sign flips and clear the
   trade floor, especially since the S&P 500's real trend rarely
   flipped sign at 63 trading days (10 flips in ~2.5 years, per the sign
   check in Design decision 2 above) — a shorter lookback would
   mechanically produce more flips and more trades."** This is the
   single most concrete, most tempting version of this pattern seen in
   this project's macro-attempts line so far — the mechanism for why a
   shorter lookback would help is directly visible in this task's own
   sign-distribution sanity check. Precisely because the mechanism is so
   legible, acting on it would be the clearest possible instance of
   tuning after seeing a result. Not investigated even informally beyond
   the sanity check already reported above (which was run, and its
   result recorded, *before* the real walk-forward — see Design decision
   2 — not derived from the walk-forward's own trade count).

All three remain live, legitimate questions for a **separately
justified, pre-committed** follow-up task — not something this task
resolves by trying them quietly and reporting only the one that looked
best.

## Comparison to prior results

| Result | Window | `N` (family / project) | Observed Sharpe (annualized) | Detection floor | Trades vs. floor | Verdict |
|---|---|---|---|---|---|---|
| Configuration C + funding (`sr-n`, best 1h result) | 1h research, 1.84y | -- / 117 | +0.039 | ~1.21 | (n/a, 1h criteria) | REJECTED (DSR 2.0e-05) |
| Daily TSMOM ensemble (`sr-v`, holdout) | 1d holdout, 2.95y | 1 (holdout) | +0.882 | 0.957 | 26 / 53 | INCONCLUSIVE (close to every bar) |
| Macro real-yield trend (`sr-x`) | 1d research, 2.25y | 4 / 121 (recomputed, see above) | -1.303 | 1.10-1.17 | 34 / 36 | INCONCLUSIVE-DATA-LIMITED (2 short) |
| **Macro S&P 500 trend (this task, `sr-y`)** | **1d research, 2.25y** | **4 / 121** | **-0.284** | **1.10-1.17** | **19 / 36** | **INCONCLUSIVE-DATA-LIMITED (17 short)** |

Reported descriptively, not as a directional conclusion (see "Honest
summary" above): both macro attempts land in the same formal verdict
category, on the same window, at the same fold geometry, evaluated
against the same bar. Neither this task nor `sr-x` individually, nor the
two read together, settles whether S&P 500 trend or real-yield trend
carries a real BTC signal — both runs are underpowered by trade count,
for structurally different reasons (real yield trends and flips
relatively often; the S&P 500's own trend at this lookback rarely
flipped sign over this specific 2.25-year window, which happened to be a
predominantly rising market).

## Code provenance

`macro_sp500_trend.py`, `test_macro_sp500_trend.py`, and the
`lineage.py` addition were uncommitted working-tree files at the moment
the real walk-forward run executed (written and tested, but not yet
`git commit`-ed) — the same situation `sr-x`/`sr-v` disclosed and closed
the same way. The logged record's own `code_version` field (plain `git
rev-parse HEAD`) reads `e059b9d3225fc38918eb594f1b191c86ff13f726`, the
`sr-x` merge commit this worktree branched from, not a commit containing
this task's own code. Exact SHA-256 of each new/changed file's own
bytes, as they existed at execution time (hyphen-grouped into 8 chunks
per this project's established convention, to avoid a local
commit-scanner false positive on a contiguous 64-hex-character run —
concatenate the chunks, without the hyphens, to reconstruct the real
digest; independently reproducible via `sha256sum` against the
committed, unchanged files):

```text
python/research/strategies/macro_sp500_trend.py
79d17a19-628e2212-7c8d7c1f-5efec885-60fb9e7e-e3ed3edf-d0e9a6a7-e1356683

python/tests/test_macro_sp500_trend.py
26f09378-d899ca9c-2f23f354-70161056-82b931d5-3fb5c22f-ebde404d-a25abdd6

python/research/lineage.py
05830181-c5b489f5-9eb1911f-74321145-1ca55b1c-195ebf33-9ef1e1fd-c4b74da9
```

None of these files were modified after these hashes were computed and before
this PR was opened. (Full, unambiguous digests are also independently
reproducible at any time via `sha256sum` against the final committed
files, which is the authoritative source — the values above are a
disclosure of the pre-commit state, not the sole record of it.)

## Deliberately out of scope

- **Any second attempt at this hypothesis** — another lookback, another
  fold geometry chosen to clear the trade floor, an inverted variant, or
  a combination with a price signal or with the real-yield signal. See
  "The temptation" above.
- **A holdout confirmation for this hypothesis.** No result here clears
  the research-split bar cleanly enough to be a holdout-confirmation
  candidate, and the only genuinely untouched single-symbol BTC-USDT
  window (the 1d holdout) is already spent (`sr-v`).
- **`DGS10` (nominal 10-year yield) and `DTWEXBGS` (dollar index) as
  standalone signals** — both remain cached and untested. Per this
  task's own governing brief, this was **the last planned macro
  attempt** before this project's research line either pivots to
  on-chain data or pauses — testing either of these two remaining series
  is not currently planned and would need its own fresh authorization,
  the same way `sr-x`'s real-yield hypothesis and this task's S&P 500
  hypothesis were each separately authorized before being built, not
  something this document schedules on its own authority.
- **A macro-specific holdout split.** As `sr-w`/`sr-x` already flagged,
  no strategy result yet warrants one; this task's own INCONCLUSIVE
  result does not change that.
- **Resolving the open (2)-vs-(3) "multi-symbol expansion" vs. "a
  genuinely different data source" comparison** CLAUDE.md's "Strategy
  Attempts So Far" section names as still open. This task pursued one
  more instance of (3); it does not decide between them.
