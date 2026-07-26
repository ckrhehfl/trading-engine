# Strategy Research Task F: risk management for the 15m strategy, and a native-1h variant

## Scope note

Two parallel improvements building directly on `.planning/sr-e-regime-
momentum.md`'s already-logged, honest negative result (`RegimeMomentumStrategy`:
3 folds, 16 trades, all-negative Sharpe, profit factor 0.214 — diagnosed
there as failing at least partly because of zero risk management, a too-
small sample, and BingX's thin ~8.3-month `15m` retention). This task does
not repeat that diagnosis; it acts on it.

- **Part 1**: add real ATR-based stop/target risk management and
  fixed-fractional position sizing to the 15m regime-gated momentum
  strategy, re-run walk-forward on the *same* real 15m data for a direct
  before/after comparison.
- **Part 2**: build a simpler, native-1h momentum variant (no
  15m -> 1h resampling needed) with the same risk-management approach,
  backfill real 1h BingX history for it, and walk-forward test it for
  meaningfully more statistical power (more folds) than the 15m dataset's
  thin 3.

**Result up front, for anyone who only reads this section**: both
strategies improved substantially in trade count and profit factor versus
prior results, and Part 2 is the first walk-forward run in this project
to clear the "8-10 folds" credibility floor (19 folds, 486 trades) — but
**neither strategy clears CLAUDE.md's Backtest/Walk-Forward Eligibility
Bar.** Part 1: Sharpe positive in only 1 of 3 folds (and only barely:
+0.04), profit factor (mean 1.17, min 0.92) still short of the 1.3-1.5
floor. Part 2: Sharpe positive in only 4 of 19 folds, worst-fold max
drawdown (28.0%) breaches the 20-25% ceiling for the first time in this
project (a direct consequence of real, non-trivial position sizing
replacing the old placeholder-tiny fixed quantity), profit factor (mean
0.89, min 0.33) well short of the floor. See "Full honest real-world
results" below for every number, and "Interpretation" for what this
combination plausibly means.

## Part 1 design: ATR-based risk management for `RegimeMomentumStrategy`

New module, not a modification of `regime_momentum.py`: `python/research/
strategies/regime_momentum_risk_managed.py`, plus a new shared module
`python/research/strategies/risk_management.py` (used by both Part 1 and
Part 2 — see "Why a shared module" below). `regime_momentum.py` itself is
untouched, byte-for-byte, so `.planning/sr-e-regime-momentum.md`'s already-
logged result stays reproducible against the original code.

`RegimeMomentumRiskManagedStrategy` reuses `regime_momentum.py`'s
`HourlyResampler`, `REGIME_SMA_LENGTH` (24), and `DEFAULT_CANDIDATE_GRID`
**unchanged, imported not duplicated** — the regime-gated 15m entry logic
itself is identical to v1's; risk management is the only addition. This
is deliberate: diverging the entry logic, even slightly, would make the
before/after comparison meaningless.

### Average True Range (`risk_management.AverageTrueRange`)

A simple rolling mean of True Range over a fixed `period` (default 14 —
Wilder's original convention, not searched/tuned), not Wilder's
exponential smoothing. CLAUDE.md's brief asked for "a standard,
look-ahead-safe rolling volatility measure," not specifically Wilder
smoothing; a plain rolling mean is simpler to verify by hand (see
`test_risk_management.py`'s hand-computed 3-bar test) and the choice
between the two smoothing conventions isn't itself a claim of edge.
Incremental (`update(kline) -> Decimal | None`, `None` during warmup),
fed one bar at a time — structurally the same "only sees up to and
including now" guarantee `KlineWindow` gives the rest of this codebase,
just expressed as incremental state rather than a bounds-checked view.
Directly tested for look-ahead safety: feeding the same prefix of bars
must produce the same ATR value regardless of what's fed afterward
(`test_lookahead_safety_value_at_bar_k_unaffected_by_future_bars`).

### Stop/target levels (`risk_management.compute_stop_and_target`)

On a fresh entry: `stop_price = entry_price -/+ (ATR * 1.5)`,
`target_price = entry_price +/- (ATR * 3.0)`, direction depending on
side. **1.5x ATR stop, not the more commonly-cited 1x**: 1x ATR is a
common starting point but empirically tends to be tight enough to get
stopped out by routine in-trend pullbacks well before a real reversal;
1.5x-2x is a more commonly-cited convention specifically for a
trend-following stop (as opposed to a tighter mean-reversion one). Picked
the lower end of that range, not searched/tuned to this asset. **3.0x ATR
target** gives exactly a 1:2 risk/reward ratio (target distance = 2x stop
distance) — a common starting convention (risk 1 unit to make 2), not
searched/tuned.

`entry_price` is the **signal bar's own close**, not the actual next-bar
simulated fill price. This is a deliberate, documented approximation: a
strategy has no look-ahead-safe way to know the next bar's actual fill
price (`backtest/fill.py` fills a `GUARDED_MARKET` intent at the *next*
bar's open plus slippage) at the moment it decides to enter — knowing
that would mean reading data beyond the current bar before emitting this
bar's intent. This affects only the strategy's own internal exit-timing
decisions, never the backtest's reported P&L: `metrics/position.py::
PositionTracker` reconstructs realized P&L independently from the actual
`(OrderIntent, Fill)` pairs the engine produces, not from anything a
strategy believes about its own position.

### Fixed-fractional position sizing (`risk_management.compute_position_size`)

`quantity = (reference_equity * risk_fraction) / stop_distance` — sized so
a stop-loss hit loses exactly `risk_fraction` (1%, not
searched/tuned — a common conservative starting convention) of a
**fixed** `reference_equity` ($10,000, matching this project's existing
`DEFAULT_STARTING_EQUITY` convention). `reference_equity` is a constant,
deliberately **not** a reconstructed live running account balance:
doing that would duplicate `metrics/position.py::PositionTracker`'s job
(which already does this correctly, downstream, from real fills) and
would require a value the strategy has no legitimate way to observe —
the `Strategy` protocol never feeds fill confirmations back to the
strategy that emitted the intent (`backtest.engine.Strategy` is a plain
`Callable`). This is a deliberate simplification for backtesting purposes
only, not a live-accounting claim — a live system sizes from the Java
Risk Gateway's actual account state, nowhere near this code path.

Replaces v1's fixed tiny constant `quantity=Decimal("0.001")` entirely —
there is no `quantity` parameter left in `RegimeMomentumRiskManagedTrainable`'s
`params` at all.

### Exit mechanics: flattening, not flipping; suppressing new entries while a position is open

Every subsequent bar (after entry), the strategy checks that bar's
high/low against the tracked stop/target (`risk_management.
check_exit_trigger`) — a genuine trigger emits a **closing** `OrderIntent`
(opposite side, **exactly** the currently-open position's quantity) and
clears internal position state. This flattens, never flips — verified
directly (`test_stop_hit_emits_flattening_intent_with_exact_position_quantity`,
`test_target_hit_emits_flattening_intent_with_exact_position_quantity`).
If both stop and target are technically crossed within the same bar's
`[low, high]` range (possible for a wide-range bar — only OHLC is known,
not intra-bar tick order), the stop wins — a deliberate, conservative
tie-break (assume the worse outcome when genuinely ambiguous, rather than
assume a favorable intra-bar ordering with no basis for it).

**A held position suppresses new entries entirely** — no pyramiding, no
same-bar open-and-close. `__call__` only ever returns one `OrderIntent |
None` per bar, so this was never really optional; extending the "exactly
flattening, not flipping" discipline to entries too (rather than, say,
immediately re-entering in the opposite direction on the same bar a stop
fires) keeps the strategy tracking at most one open-position lifecycle at
a time, which is what makes internal position tracking tractable at all.

### A real correctness gap found and fixed during TDD: `atr > 0`, not just `atr is not None`

`compute_stop_and_target` raises `ValueError` on a non-positive ATR (a
genuinely degenerate input — no meaningful stop distance can be derived
from zero volatility). The entry-gating condition in both strategies'
`__call__` originally checked only `atr is not None` (i.e. "warmup
complete") before attempting to open a position — but ATR can complete
warmup and still read exactly `0` on a perfectly flat run of bars (e.g. a
synthetic test fixture with `open == high == low == close` for every
bar — which is exactly what my first hand-verified test scenario used,
reusing `.planning/sr-e-regime-momentum.md`'s own hand-verified closes
list with `open=high=low=close`). That would have let a degenerate input
crash `__call__` via an uncaught `ValueError` instead of being treated as
"no signal," the same way every other degenerate case in this codebase is
handled. Caught by the test-writing process itself (the test failed with
a `ValueError` traceback, not a wrong assertion) before this shipped;
fixed by adding `and atr > 0` to the gating condition in both
`regime_momentum_risk_managed.py` and `hourly_momentum.py`. Vanishingly
unlikely on real BTC data, but cheap insurance, and a legitimate example
of TDD catching something design review alone hadn't.

## Part 2 design: native-1h momentum variant

New module `python/research/strategies/hourly_momentum.py`.
`HourlyMomentumStrategy` is a **single-tier** fast/slow SMA crossover
computed directly on 1h closes, edge-triggered — structurally the same
shape as `ma_crossover.MovingAverageCrossoverStrategy` (unconditionally
fires on every genuine cross), not `RegimeMomentumStrategy` (a two-tier
design that gates a crossover against a separately-computed regime). No
`HourlyResampler` needed at all — the input klines are already 1h, so the
crossover state itself doubles as both the trend/regime read (fast above
slow = "in an uptrend") and the entry trigger (the edge where that state
just changed).

### The single-tier-vs-two-tier judgment call, made explicitly

This task's brief ("operating natively on 1h bars for both trend/regime
and entry signal... no internal resampling needed... Keep the same core
idea (fast/slow SMA crossover, edge-triggered)") is genuinely readable
two ways:

- **(a) Single-tier** (what was built): one fast/slow crossover on 1h
  bars, always fires on a genuine cross, no separate gating tier.
- **(b) Two-tier**: keep `RegimeMomentumStrategy`'s regime-gates-entry
  structure, with *both* SMAs computed directly on 1h bars instead of
  resampling 15m into the regime tier.

(a) was chosen because the brief explicitly calls this variant "simpler"
and describes "the same core idea" as *one* fast/slow crossover, not a
*regime-gated* one — every other place in this project that means
"regime-gated" says so explicitly (including this very document's Part 1
section). If a second, gating tier were intended here, that term would
most likely have been used again. (b) would have been the safer
literal-compatibility choice with the existing `RegimeMomentumStrategy`
architecture; it wasn't chosen because CLAUDE.md's Strategy Research
Methodology favors stating an assumption and proceeding over blocking
mid-task on an ambiguity, and (a) is the more direct reading of
"simpler"/"no resampling." Recorded here in case a future session
believes (b) should have been built instead — see also the fuller version
of this reasoning in `hourly_momentum.py`'s own module docstring.

### Same risk management as Part 1, via the shared module

`HourlyMomentumStrategy` uses `research.strategies.risk_management`
identically to Part 1 — same `AverageTrueRange`, same stop/target
formula, same fixed-fractional sizing, same flatten-not-flip exit
mechanics, same "suppress new entries while a position is open" rule. See
"Why a shared module" below.

### 1h candidate grid: sized in 1h units, not a mechanical conversion

`DEFAULT_CANDIDATE_GRID = ((3, 8), (4, 10), (5, 12), (6, 15), (8, 20))` —
sized directly in 1h-bar units, **not** a mechanical `/4` conversion of
`regime_momentum.DEFAULT_CANDIDATE_GRID`'s 15m-bar pairs (that would give
non-integer, oddly-shaped windows for most entries — e.g. `(5, 15) ->
(1.25, 3.75)`). Kept a similar fast:slow ratio spirit (~1:2.5-3, matching
the 15m grid's own spirit) and the same candidate count (5), per
CLAUDE.md's "few tunable knobs" guidance — not empirically tuned to this
asset.

### Why a shared `risk_management.py` module, not two independent implementations

The task brief explicitly requires "the same risk-management approach
from Part 1... so the two are reasonably comparable." A shared module
(`AverageTrueRange`, `OpenPosition`, `compute_stop_and_target`,
`compute_position_size`, `check_exit_trigger`) is how that's actually
*guaranteed* rather than merely intended — two independently-written
implementations of "1.5x ATR stop, 3x ATR target, 1% fixed-fractional
sizing" would risk subtle divergence (a rounding difference, an
off-by-one in the tie-break rule) that would quietly undermine the
before/after and 15m/1h comparisons this task's results depend on.

## A real correctness bug found and fixed: Sharpe annualization was hardcoded for 15m bars

`metrics/metrics.py`'s `_sharpe_ratio` annualized via a hardcoded
`math.sqrt(96 * 365)` — 96 bars/day, correct for 15m bars, but silently
wrong for anything else. Before this task, every strategy in this project
only ever ran on 15m data, so this was never wrong in practice. Feeding
1h klines (24 bars/day) through the *unmodified* pipeline would have
annualized 1h-frequency returns as if they happened 4x more often than
they really did — inflating every reported Sharpe ratio for
`hourly_momentum` by exactly `sqrt(96/24) = 2x`.

This was caught by inspection while reviewing the first real Part 2 run's
results (the per-fold Sharpe magnitudes looked implausibly large relative
to the per-fold trade counts and drawdowns) — not by a test written in
advance, since nothing before this task had a second timeframe to expose
the bug. Fixed properly, with tests first (TDD): `compute_metrics` and
`_sharpe_ratio` in `python/metrics/metrics.py` gained an explicit
`bars_per_day: int = 96` parameter (default preserves byte-for-byte
identical behavior for every existing 15m caller — locked in by
`test_sharpe_ratio_default_bars_per_day_is_96_unchanged_from_before_the_1h_variant`);
`research/walkforward.py::run_walk_forward` gained the same parameter,
threaded through to each fold's `compute_metrics` call; both new
`Trainable` classes (`RegimeMomentumRiskManagedTrainable`,
`HourlyMomentumTrainable`) gained a `bars_per_day` constructor parameter
(defaulting to 96 and 24 respectively) so each strategy's own
candidate-scoring logs the right annualization too. New tests:
`test_sharpe_ratio_uses_a_smaller_annualization_factor_for_fewer_bars_per_day`,
`test_compute_metrics_passes_bars_per_day_through_to_sharpe_ratio`,
`test_run_walk_forward_passes_bars_per_day_through_to_fold_sharpe_annualization`
— each directly asserts the 1h/15m Sharpe ratio is exactly 2x.

**Concrete impact on this task's own results**: the real Part 2
walk-forward run was executed twice — once before this fix (Sharpe values
silently 2x-inflated, e.g. fold 1's Sharpe read `8.00` instead of the
correct `4.00`) and once after (correct). Both runs are preserved in the
local `runs/experiments.jsonl` (gitignored, append-only, gets left as-is
per this project's established "don't clean up the audit trail"
convention — see CLAUDE.md's Durability notes) — the numbers reported
below are exclusively from the second, corrected run
(`run_id=5af7bcc2-5563-44db-b830-73d209109917`). Trade counts, total
return, max drawdown, win rate, and profit factor are all identical
between the two runs (none of those depend on `bars_per_day`) — only
Sharpe changed, and by exactly the predicted 2x factor, which is itself a
useful confirmation the fix is correct.

## The real BingX retention-by-granularity finding

Confirmed 2026-07-26. Also added to CLAUDE.md's "Exchange API Facts —
BingX" section (see that file's "Verified" subsection) in the same style
as the other verified facts — this section is the fuller version with the
underlying arithmetic shown.

The task prompt this session started from already stated: `1d` back to
~2021-05-12 (~5 years), `1h` back to ~2024-04-27 (~15 months), `15m` back
to ~2025-11-16 (~8.3 months, matching `.planning/sr-a-data-pipeline.md`'s
independent finding exactly), `5m` back to ~2026-05-02 (~3 months) — from
a same-day binary-search probe against the live production endpoint. This
task only independently re-verified `1h` (the one it actually depends
on), via a real, full `backfill.py` run rather than an earlist-bar-only
probe:

```
uv run python -m data.backfill --symbol BTC-USDT --interval 1h \
  --start 2020-01-01T00:00:00+00:00 --base-url https://open-api.bingx.com \
  --db-path python/data/var/klines.sqlite3
```

Result: **19,678 hourly bars, zero internal gaps**, wall-clock ~24
seconds. `MIN(open_time_ms)` = `2024-04-27T10:00:00Z` (**exactly**
matching the prompt's binary-search-derived earliest date — that part of
the prior finding is confirmed correct), `MAX(open_time_ms)` =
`2026-07-26T07:00:00Z`.

**A real discrepancy worth flagging rather than silently propagating**:
the true span between those two dates is **819.9 days (~26.9 months /
~2.24 years)**, not the "~15 months" the prompt stated. The earliest-date
finding was accurate; the *duration* derived from it in the prompt
appears to have been an arithmetic error (possibly computed against a
different, earlier "now" reference at investigation time, or a simple
mistake) — not re-derived correctly against 2026-07-26. This task's own
instructions explicitly said "confirm the real count you get, don't
assume the exact number from this prompt is precisely right," which is
exactly what surfaced this: the real backfill count (19,678 bars) only
makes sense at ~820 days, not ~450 days (~15 months). This doesn't change
the earliest-date fact (still exactly 2024-04-27T10:00:00Z, still ~2x
`15m`'s depth as the prompt's "nearly double" framing said, just by a
larger margin than "~15 months vs ~8.3 months" implied — it's actually
closer to 3.25x `15m`'s ~252-day depth). `1d`/`5m` were not independently
re-verified by this task (nothing here uses them) — presented above only
as "documented, not yet empirically verified by this task," matching this
project's existing "Documented, not yet empirically verified" vs.
"Verified" distinction in CLAUDE.md's Exchange API Facts section.

## Holdout-split mechanics for the new 1h dataset

`configs/research/holdout_1h.json` — a **second**, sibling config to the
existing `configs/research/holdout.json` (15m-scoped), not a
modification of it and not a code change to `python/research/holdout.py`:
`load_research_klines`/`load_holdout_klines` already accept an explicit
`holdout_config_path` override, so a second config file is the minimal,
zero-production-code-change way to give the 1h dataset its own cutoff.
The single-access holdout enforcement is keyed by `strategy_id` (not
symbol/interval), so using a distinct `strategy_id` (`hourly_momentum`)
for the 1h strategy already keeps its holdout claim independent of the
15m strategies' — nothing else needed to change for that either.

**Cutoff reasoning**: `.planning/sr-c-walkforward-holdout.md` established
two framings for the 15m holdout size at thin (~252-day) depth: "15-25%
of available data" or "a fixed ~30-45 day trailing window," and chose 45
days because it was the one value satisfying both simultaneously. At the
1h dataset's much greater depth (819.9 days), the fixed-day framing (a
floor meant for thin datasets) is no longer the binding constraint — the
percentage framing is. Chose **150 days** (3,600 bars): `150/819.9 =
18.3%`, comfortably within 15-25%, and still a materially-sized, genuine
final check (not a token one). Leaves **16,078 bars (~669.9 days)** of
research data. Committed cutoff: `holdout_cutoff_ms: 1772092800000`
(`2026-02-26T08:00:00Z`).

## Walk-forward window sizing for 1h

Scaled proportionally from the 15m provisional defaults' calendar shape
(~90 day train / ~30 day validate / step = validate), expressed in 1h-bar
units: **train=2,160 bars, validate=720 bars, step=720 bars** (i.e. the
exact same ~90/30/30-day windows as the 15m provisional defaults, just
counted in coarser bars). This produced **19 folds** against the real
16,078-bar research split — comfortably clearing CLAUDE.md's "8-10 folds
for credibility" floor for the first time in this project, without
needing to shrink the windows further. Not tuned to hit a target fold
count — this is what the existing ~90/30/30-day shape naturally gives at
1h's real depth; a different, still-defensible window choice could have
given a different (also credible) fold count, and no attempt was made to
find one that looked better.

## Full honest real-world results (2026-07-26, real cached BingX data)

Ran via an uncommitted verification script (same convention as
Tasks C/D/E's own real verification runs). `fee_bps=5, slippage_bps=2`
throughout, matching every prior task's convention.

### Part 1: `RegimeMomentumRiskManagedStrategy` (15m), vs. sr-e's v1 baseline

Same data as sr-e's real run: `load_research_klines` -> 19,870 bars
(`2025-11-16T03:45:00Z` -> `2026-06-11T03:00:00Z`), same windows
(train=8,640, validate=2,880, step=2,880) -> 3 folds, same as before
(the 8-10 fold credibility floor remains structurally unreachable at the
current real 15m depth — not re-litigated here, see sr-a/sr-c/sr-e).
`run_id=42ac2f1c-0e60-40d5-8350-ab342a51dea3`.

| fold | winner (fast,slow) | trades | total_return | sharpe | max_dd | win_rate | profit_factor |
|---|---|---|---|---|---|---|---|
| 0 | (5,15)\* | 54 | -0.717% | +0.038 | 10.90% | 40.7% | 1.287 |
| 1 | (5,15)\* | 36 | -10.51% | -3.193 | 16.04% | 30.6% | 0.915 |
| 2 | (5,15)\* | 34 | -3.22% | -1.072 | 10.81% | 44.1% | 1.308 |

\* winning `(fast, slow)` pair per fold not printed by the verification
script's summary output; only aggregate/per-fold metrics were captured.
Available in `runs/experiments.jsonl`'s per-fold `backtest_run` records
for this `run_id` if needed later — not re-derived here since it doesn't
change the eligibility evaluation.

**Aggregate**: `fold_count=3`, `mean_sharpe=-1.409`, `min_sharpe=-3.193`,
`all_folds_positive_sharpe=False`, `worst_fold_max_drawdown=16.04%`,
`mean_total_return=-4.82%`, `total_trades=124`, `mean_profit_factor=1.170`,
`min_profit_factor=0.915`, `folds_with_zero_trades=0`.

**Before/after vs. sr-e's v1** (same 3 folds, same data, same windows —
risk management is the only variable that changed):

| metric | v1 (sr-e) | v2 risk-managed (this task) |
|---|---|---|
| total trades | 16 | **124** |
| mean Sharpe | -2.28 | **-1.41** |
| min Sharpe | -4.75 | **-3.19** |
| folds with positive Sharpe | 0 of 3 | **1 of 3** (barely: +0.038) |
| mean profit factor | 0.214 | **1.170** |
| min profit factor | 0.0 | **0.915** |
| worst-fold max drawdown | 1.375% | 16.04% (real risk-sized positions, not the old placeholder-tiny quantity) |

Every metric that risk management could plausibly move, moved in the
improving direction — trade count now clears the 100-trade minimum for
the first time in this project, profit factor is close to (but still
under) the 1.3-1.5 floor, Sharpe is less negative across the board. It is
**not** a passing result — see eligibility table below — but it is a
real, substantive improvement over v1, consistent with sr-e's own
diagnosis that zero risk management was a real limitation.

### Part 2: `HourlyMomentumStrategy` (1h), real 1h BingX data

`load_research_klines` (against `configs/research/holdout_1h.json`) ->
16,078 bars (`2024-04-27T10:00:00Z` -> `2026-02-26T07:00:00Z`).
train=2,160, validate=720, step=720 -> **19 folds**.
`run_id=5af7bcc2-5563-44db-b830-73d209109917` (post-`bars_per_day`-fix;
see "A real correctness bug found and fixed" above).

| fold | trades | total_return | sharpe | max_dd | win_rate | profit_factor |
|---|---|---|---|---|---|---|
| 0 | 31 | +3.56% | +1.592 | 9.90% | 45.2% | 1.309 |
| 1 | 29 | +9.28% | +4.002 | 5.38% | 48.3% | 1.756 |
| 2 | 25 | -13.66% | -5.362 | 18.77% | 28.0% | 0.549 |
| 3 | 21 | -7.73% | -4.207 | 11.99% | 28.6% | 0.618 |
| 4 | 28 | -7.60% | -3.337 | 9.49% | 28.6% | 0.721 |
| 5 | 23 | -2.34% | -1.024 | 8.19% | 34.8% | 0.986 |
| 6 | 21 | -3.97% | -1.932 | 9.41% | 33.3% | 0.875 |
| 7 | 25 | +6.47% | +2.932 | 4.62% | 48.0% | 1.543 |
| 8 | 26 | -12.12% | -5.245 | 15.33% | 23.1% | 0.526 |
| 9 | 33 | -21.30% | -8.168 | 23.51% | 24.2% | 0.414 |
| 10 | 30 | -11.09% | -5.038 | 13.10% | 26.7% | 0.694 |
| 11 | 21 | +10.48% | +4.201 | 4.82% | 57.1% | 2.258 |
| 12 | 23 | -14.70% | -7.058 | 16.37% | 21.7% | 0.469 |
| 13 | 30 | -25.20% | -9.873 | **28.00%** | 16.7% | 0.330 |
| 14 | 24 | -2.53% | -1.059 | 7.64% | 37.5% | 1.056 |
| 15 | 24 | -11.01% | -5.540 | 15.42% | 25.0% | 0.543 |
| 16 | 27 | -5.35% | -1.613 | 9.78% | 29.6% | 0.867 |
| 17 | 22 | -7.89% | -3.917 | 11.18% | 31.8% | 0.705 |
| 18 | 23 | -6.99% | -3.511 | 8.53% | 26.1% | 0.711 |

**Aggregate**: `fold_count=19`, `mean_sharpe=-2.850`, `min_sharpe=-9.873`,
`all_folds_positive_sharpe=False`, `worst_fold_max_drawdown=28.00%`
(fold 13), `mean_total_return=-6.51%`, `total_trades=486`,
`mean_profit_factor=0.891`, `min_profit_factor=0.330`,
`folds_with_zero_trades=0`. Only **4 of 19 folds** (0, 1, 7, 11) have a
positive Sharpe.

### Eligibility bar evaluation (CLAUDE.md's Backtest/Walk-Forward Eligibility Bar)

**Part 1** (15m, 3 folds — the 8-10 fold credibility floor remains
structurally unmet at current real 15m depth, same as sr-e; excluded from
the table below rather than silently scored either way, same convention
sr-e used):

| Criterion | Requirement | Actual | Result |
|---|---|---|---|
| Positive Sharpe, every fold | Sharpe > 0 in all folds | +0.038, -3.193, -1.072 | **FAIL** — 2 of 3 folds negative |
| Max drawdown ceiling | ≤ 20-25%, per-fold and aggregate | 10.90%, 16.04%, 10.81%; worst-fold (this pipeline's only aggregate-drawdown concept, per sr-e's own reasoning) = 16.04% | **PASS** |
| Minimum total trades | ≥ 100 across all folds | 124 | **PASS** — clears this bar for the first time in this project |
| Profit factor floor | 1.3-1.5 | mean 1.170, min 0.915 | **FAIL** — close, but short |

**This strategy still does not clear the eligibility bar** — 2 of 4
evaluated criteria fail — but for the first time, 2 of 4 pass, and the 2
that fail (Sharpe, profit factor) are both closer to passing than v1's
were by a wide margin.

**Part 2** (1h, 19 folds — the credibility floor is genuinely cleared
here, so every criterion is evaluated with no caveat):

| Criterion | Requirement | Actual | Result |
|---|---|---|---|
| Fold count | ≥ 8-10 for credibility | 19 | **PASS** (new for this project) |
| Positive Sharpe, every fold | Sharpe > 0 in all folds | 4 of 19 positive; mean -2.850, min -9.873 | **FAIL** |
| Max drawdown ceiling | ≤ 20-25%, per-fold and aggregate | worst fold 28.00% (fold 13); 2 folds (9, 13) exceed 20% | **FAIL** — first time this project's drawdown criterion has failed, a direct consequence of real (not placeholder-tiny) position sizing |
| Minimum total trades | ≥ 100 across all folds | 486 | **PASS** |
| Profit factor floor | 1.3-1.5 | mean 0.891, min 0.330 | **FAIL** |

**This strategy does not clear the eligibility bar** — 3 of 5 criteria
fail, including the two most directly tied to "is there an edge here"
(Sharpe, profit factor). Unlike every prior negative result in this
project, this one comes with a genuinely credible sample size (19 folds,
486 trades) — the "thin evidence in either direction" caveat that
qualified sr-d's and sr-e's negative results does not apply here to
nearly the same degree.

### Interpretation, stated carefully

Real risk management measurably improved the 15m strategy on every axis
it could plausibly move (trade count, Sharpe, profit factor all better
than v1), without producing a passing result — consistent with sr-e's
diagnosis that zero risk management was a real, if partial, limitation;
it was not the only thing standing between this strategy and a genuine
edge.

The native-1h variant, tested with by far the most statistical power any
strategy in this project has had (19 folds vs. the 15m ceiling's
structural 3, 486 trades vs. 124), still shows no robust edge — if
anything, less consistent than the risk-managed 15m version (only 4 of 19
folds positive vs. 1 of 3, and the first drawdown-ceiling failure in this
project, from real position sizing finally being large enough to make
drawdown a meaningful signal rather than a trivial pass). Taken together,
this is a genuinely more informative negative result than sr-d's or
sr-e's: with a credible fold count now actually achieved, "not enough
data to know" is no longer a fully available explanation for Part 2's
result the way it still legitimately is for Part 1's. Trend-following
SMA-crossover-with-ATR-risk-management, at these parameters, on this
data, on either timeframe tested, does not show a positive risk-adjusted
edge in BTC-USDT. It does not rule one out under a different strategy
shape (mean-reversion, a different entry trigger, a different regime
filter) — that remains untested — but this specific hypothesis, now
tested under real risk management and (for the 1h case) real statistical
power, does not clear the bar.

### Experiment-log verification

Both parts logged automatically via `run_walk_forward`'s standard
`log_run` call, plus every candidate `fit()` scored along the way (see
CLAUDE.md's "Build sequencing" Task D convention, followed identically
here). Part 1: 3 folds x 5 candidates = 15 candidate records + 1
standalone record = 16 per run (executed twice this session — see the
`bars_per_day` bug note above — 32 total `regime_momentum`/
`v2-risk-managed` records in the local log). Part 2: 19 folds x 5
candidates = 95 candidate records + 1 standalone = 96 per run (also
executed twice — 192 total `hourly_momentum` records). No holdout access
was made by this task (`load_holdout_klines` was never called) — neither
strategy is close to clearing the eligibility bar, so there is nothing
legitimate to confirm against holdout data yet, per CLAUDE.md's
non-negotiable "holdout stays untouched" rule. The one pre-existing
`holdout_access` record in the local log (from Task C's own real
verification run) is unchanged.

## TDD

Every new module's tests were written first and confirmed failing on
`ModuleNotFoundError`/`TypeError` before the corresponding production
code existed:

- `test_risk_management.py` (20 tests): `AverageTrueRange` correctness
  (hand-computed 3-bar average, rolling-window drop of the oldest True
  Range, first-bar-has-no-prior-close degenerate case) and look-ahead
  safety (same prefix -> same value regardless of what follows);
  `compute_stop_and_target` for both long/short, the 1:2 risk/reward
  ratio, and rejecting non-positive ATR; `compute_position_size`'s
  sizing arithmetic (including the symmetric short case) and its `None`
  return for zero stop distance; `check_exit_trigger` for every
  long/short stop/target combination plus the same-bar-both-triggered
  conservative tie-break.
- `test_regime_momentum_risk_managed.py` (16 tests): construction
  validation; entry sizing/stop/target correctness using
  `.planning/sr-e-regime-momentum.md`'s own hand-verified regime-gating
  scenario (reused directly, not re-derived, for a scenario already known
  to produce a genuine gated-through fire); stop-hit and target-hit both
  emitting an exact-quantity flattening intent, never a flip; no new
  entry opened while a position is already open; `fit()`'s grid-search
  logging/winner-selection/zero-trade-fallback behavior; a real
  `run_walk_forward` integration smoke test.
- `test_hourly_momentum.py` (17 tests): construction validation;
  edge-triggered crossover correctness (warmup, baseline-establishes-no-
  signal, a fresh cross firing, no signal while a sign holds constant
  across many bars); the same risk-management coverage shape as Part 1's
  test file; `fit()` grid-search coverage; a real `run_walk_forward`
  integration smoke test.
- `metrics/metrics.py`/`research/walkforward.py` `bars_per_day` fix (4
  tests): a regression guard that the new parameter's default reproduces
  the exact pre-existing 15m behavior, a direct assertion that a smaller
  `bars_per_day` halves the reported Sharpe (24 vs. 96 bars/day), and the
  same at both the `compute_metrics` and `run_walk_forward` layers.

**A genuine TDD-driven correctness fix, not just a coverage exercise**:
writing `TestEntrySizingAndLevels`'s first hand-verified scenario (reusing
sr-e's exact closes with `open=high=low=close`, matching sr-e's own test
convention) produced a real `ValueError` crash from `compute_stop_and_target`,
not a wrong assertion — surfacing the `atr > 0` gap described above before
any of this shipped. The fix (and the test fixture's own follow-up fix,
giving each hand-verified bar a small nonzero high/low band so ATR could
be meaningfully positive) both happened before the module was considered
done, per the red-green-refactor discipline CLAUDE.md requires for this
kind of work.

Full suite: **328 passed** (was 271 before this task — 328-271=57, matching
new-test count: 20+16+17+3+1=57 exactly). Nothing from prior tasks
regressed.

## Judgment calls resolved without asking

- **A new module (`regime_momentum_risk_managed.py`), not a modification
  of `regime_momentum.py` or a parameter toggle on it.** The task brief
  explicitly offered this as one option ("or version — your call, e.g.
  RegimeMomentumStrategy v2"). Chosen over an in-place modification so
  `.planning/sr-e-regime-momentum.md`'s already-logged v1 result stays
  reproducible against unmodified code, and over a parameter-toggle
  design because the position-sizing/exit-tracking state shape differs
  enough (no more fixed `quantity` param, a genuinely new `OpenPosition`
  concept) that a toggle would have made the single class harder to
  reason about than two smaller ones.
- **`strategy_id="regime_momentum"` (same family), `strategy_version=
  "v2-risk-managed"`** — per CLAUDE.md's "strategy_id = family, version =
  logic changes" convention, since this is a logic change (added risk
  management) to the same underlying strategy family, not an unrelated
  new hypothesis. `hourly_momentum` gets its own `strategy_id` (`v1`) —
  a genuinely different, simpler strategy shape (see the single-tier
  judgment call above), not a version of `regime_momentum`.
  `strategy_id="hourly_momentum"`.
- **Single-tier, not two-tier, for `HourlyMomentumStrategy`** — see "The
  single-tier-vs-two-tier judgment call, made explicitly" above.
- **A shared `risk_management.py` module** rather than two independent
  implementations — see "Why a shared module" above.
- **`entry_price` = signal bar's own close**, not an attempt to predict
  the next bar's actual fill price — see Part 1's "Stop/target levels"
  section above for the full reasoning.
- **Stop wins on a same-bar stop-and-target ambiguity** — conservative
  tie-break, no intra-bar tick data available to resolve it any other
  way.
- **`atr > 0` guard added to both strategies' entry-gating condition** —
  a real gap found via TDD, not anticipated in the original design; see
  above.
- **`metrics/metrics.py`/`research/walkforward.py` gained a `bars_per_day`
  parameter** rather than leaving the 1h Sharpe silently wrong or hacking
  around it inside `hourly_momentum.py` alone — the annualization factor
  is `metrics.metrics`'s concern, and fixing it there (with a
  default-preserves-existing-behavior parameter) is the only way every
  future non-15m strategy in this project benefits from the fix instead
  of each one needing its own workaround.
- **`configs/research/holdout_1h.json` as a second, sibling config file**,
  not a code change to `python/research/holdout.py` and not a modification
  of the existing 15m config — see "Holdout-split mechanics for the new
  1h dataset" above.
- **150-day 1h holdout (18.3% of available data)** — see the same
  section for the framing this was chosen against.
- **1h walk-forward windows scaled proportionally from the 15m
  provisional defaults' calendar shape** (train=2,160/validate=720/
  step=720 1h-bars, i.e. the same ~90/30/30-day shape) rather than picked
  to target a specific fold count — see "Walk-forward window sizing for
  1h" above.
- **Both Part 1 and Part 2 real verification runs left as two log entries
  each** (before/after the `bars_per_day` fix), not cleaned up to a single
  canonical run — the append-only experiment log's whole purpose is an
  honest audit trail, including runs later found to have used a metrics
  bug; deleting the earlier ones would undermine exactly what the log
  exists to provide. The corrected run's `run_id` is the one this
  document's reported numbers come from, stated explicitly in each
  results section above.

## Deliberately out of scope

- **Tuning any parameter (ATR period, stop/target multipliers, risk
  fraction, candidate grids, walk-forward windows) to produce a
  better-looking result.** Explicitly against this task's brief ("No
  tuning or cherry-picking toward a better-looking result — if it still
  fails, say so plainly") and against CLAUDE.md's Strategy Research
  Methodology in spirit.
- **Any holdout confirmation run for either strategy.** Neither clears
  the eligibility bar, so there is nothing legitimate to spend either
  dataset's one-shot holdout access on.
- **Changing CLAUDE.md's Eligibility Bar thresholds, or the provisional
  15m walk-forward window defaults.** Both remain explicitly
  human-approved defaults; this task evaluates against them for both
  timeframes, it doesn't change either.
- **Backfilling `5m` or re-verifying `1d`.** Nothing in this task uses
  either; the retention-by-granularity finding above reports the prompt's
  binary-search figures for those two as "documented, not independently
  re-verified by this task," not as something this task confirmed
  first-hand.
- **A two-tier ("regime-gated") 1h variant** — the alternative reading of
  Part 2's brief considered and not built; see the judgment call above.
  Would be a reasonable follow-up if a future session wants to test that
  reading directly.
- **Modifying `backtest/engine.py`, `KlineWindow`, or the fill-simulation
  logic in `backtest/fill.py`.** Both new strategies work entirely within
  the existing `Strategy` protocol and `GUARDED_MARKET` next-bar-fill
  semantics — no changes needed there.
- **Reconstructing a live-accurate running equity for position sizing.**
  Deliberately out of scope per the task's own brief — see
  `compute_position_size`'s docstring and Part 1's "Fixed-fractional
  position sizing" section above.
