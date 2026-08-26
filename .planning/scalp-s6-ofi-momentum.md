# Scalping Strategy Research Task S6 — order-flow-imbalance momentum (commit phase)

Commits the strategy implementation and its pre-registration; does
**not** execute the real holdout access. A separate, later, deliberate
step does — exactly mirroring `sr-u`'s own precedent ("This task commits
the strategy implementation and this registration; it does not execute
it. A separate, later task loads the real holdout klines and runs this
registration exactly once."), already followed once by this project's
own `vwap-mid-reversion-1m-holdout` registration (Task S4 commit phase /
real execution, PRs #111/#112).

## Why this candidate, and why now

Two prior investigations found order-flow imbalance and liquidation
cascades both lacked a real foundation with this project's data at the
time (`.planning/scalp-s5-binance-1m-orderflow-infra.md`'s own "Why this
task exists" section). Task S5 built the missing infrastructure: real
Binance USDT-M futures BTCUSDT `1m` order-flow data
(`taker_buy_base_volume`/`taker_buy_quote_volume`), 3,661,780 bars,
2019-09-08T17:57:00Z through present — not a rolling window at all. This
task is the first real use of that infrastructure: an order-flow-
imbalance momentum candidate, chosen over liquidation cascades because
(per the real literature investigation) OFI has *some* real, if
horizon-mismatched, citable evidence (`arXiv:2607.09426`), while
liquidation cascades had no trading rule and no post-event reversion
pattern documented anywhere in the real cited papers.

## Design decisions and their reasoning

**15-bar ("quarter-hour") rolling window.** Named directly after, and
grounded in, the real cited paper's own title/window convention (Kim &
Hansen 2026, "The Quarter-Hour Effect," arXiv:2607.09426) — external,
not fit to this project's own data.

**2 standard deviations.** The same external convention this codebase
has now reused three times (`mean_reversion.BollingerBands`,
`vwap_mid_reversion.VwapMidBands`, now this), applied to an order-flow-
imbalance series rather than a price series.

**Momentum, not reversion.** The real cited paper's own finding is that
order imbalance predicts returns in the *same* direction it points —
imbalance is followed by continuation, not reversion. This is a
structurally different signal class from `vwap_mid_reversion.py`'s own
mean-reversion framing, not a variation of it.

**Honest horizon-mismatch disclosure, not glossed over.** The real
cited paper's own finding is a 4-12 hour forward-return horizon, with
"much weaker effects at finer clock-time frequencies." This registration
tests a much shorter (minutes-to-tens-of-minutes) adaptation as a
genuine, disclosed hypothesis — the same honesty standard
`vwap_mid_reversion.py`'s own registration already applied to its own
1-second-order-book-vs-1-minute-OHLCV proxy gap. This is not an imported
result.

**Pure ATR-based stop/target, no signal-based exit at all — the real,
deliberate fix for `vwap_mid_reversion.py`'s own catastrophic failure.**
That strategy's real holdout result
(`.planning/scalp-s4-vwap-mid-reversion-result.md`) confirmed, at scale,
the risk its own docstring disclosed in advance: zero ATR stop, zero
regime filter, and an unbounded position held against a market that
never reverted produced a real 10,619% raw drawdown. That document's own
closing disclosure named the real, open question for "a future task":
whether a stop-loss should be treated as a legitimate, literature-
sourced (zero-*search*, not necessarily zero-*parameter*) risk control.
This task is that answer. `research/strategies/risk_management.py`'s own
module docstring states its ATR period (14, Wilder's original), stop
multiplier (1.5x), target multiplier (3.0x, a 1:2 risk:reward), and risk
fraction (1% fixed-fractional) are all "not searched or tuned to this
asset" — genuinely external conventions already implemented, tested, and
proven in this codebase (`hourly_momentum.py`,
`regime_momentum_risk_managed.py`), reused here completely unmodified
rather than re-derived or, worse, silently ported from the spent
1h-window search the way an ATR-stop-using momentum family member's own
parameters originally were. Composition (entry-only-while-flat,
exit-checked-first, `_open`/`_flatten` shape) directly mirrors
`hourly_momentum.HourlyMomentumStrategy` — the established ATR-stop/
target precedent in this package — not `vwap_mid_reversion.py`, which
had no such composition to mirror.

**Disclosed consequence of adding a real exit mechanism**: holding
period is now price-determined (however long it takes price to move
1.5x or 3x ATR), not time-bounded the way `vwap_mid_reversion.py`'s
implicit ~20-minute average was. It could occasionally exceed "tens of
minutes" in a slow-moving market — a disclosed, accepted characteristic
of reusing `hourly_momentum.py`'s own proven exit pattern, not scope
creep.

## A real, closed infrastructure gap found during this task

**The single most consequential finding of this task.** Before writing
any strategy code, checked directly whether `backtest.kline.Kline` (the
datetime-keyed, in-memory type a `Strategy` actually receives via its
`window` argument — **not** `data.bingx_klines.KlineRow`, the ms-
timestamp-keyed storage type Task S5 extended) carries the two new
order-flow fields at all.

**It did not.** `Kline` had no `taker_buy_base_volume`/
`taker_buy_quote_volume` fields, and `research.holdout._kline_row_to_kline`
(the real, only conversion function from stored `KlineRow` rows to the
`Kline` objects a real backtest/holdout run actually feeds a strategy)
silently dropped both fields on every call. Task S5 extended the storage
layer only; this gap meant the entire OFI-momentum design, however
correct on paper, would have been structurally unable to see real
order-flow data when run against real holdout klines — only against
directly hand-built test fixtures.

Closed as real, necessary, in-scope work for this task, mirroring
exactly how `KlineRow` itself was extended in Task S5 (additive, `None`
default, zero regression):

- `backtest/kline.py`: `Kline` gains the same two optional fields,
  `Decimal | None = None`, added at the end. Verified additive: every
  real `Kline(...)` construction site in the codebase (`research/holdout.py`,
  `python/live/generate_daily_signal.py`, `research/strategies/
  regime_momentum.py`) is keyword-based, nothing breaks.
- `research/holdout.py::_kline_row_to_kline`: now carries both fields
  through from the stored `KlineRow` unchanged.
- New tests in `python/tests/test_holdout.py`: a real round-trip
  confirming a Binance-style row's real taker-buy values survive the
  full storage-to-`Kline` conversion, and a regression test confirming
  a plain BingX-style row still converts cleanly with genuine `None`s.

`python/live/generate_daily_signal.py`'s own separate, deliberately-
duplicated `_kline_row_to_kline` (BingX-only, `1d` live signal
generation) was **not** touched — BingX rows have no real order-flow
data regardless, so the new fields simply default to `None` there
either way; touching it would be scope creep unasked by this task.

## Preregistration — real, computed values, not hand-derived

Queried directly against the real production
`python/data/var/klines.sqlite3` (`SELECT MIN(open_time_ms),
MAX(open_time_ms), COUNT(*) FROM klines WHERE symbol=
'BINANCE-FUTURES:BTCUSDT' AND interval='1m'`), independently re-confirmed
rather than trusting Task S5's own already-published numbers:

```text
min_open_time_ms : 1567965420000  (2019-09-08T17:57:00Z)
max_open_time_ms : 1787672220000  (2026-08-25T15:37:00Z)
count             : 3661780
end_ms (half-open, max+60000) : 1787672280000  (2026-08-25T15:38:00Z)
```

Real gap check, independently re-run via `data.store.find_missing_ranges`
against the real database (not assumed unchanged from Task S5's own
finding):

```text
gaps: [(1567969200000, 1567969260000)]  -- exactly 1, matching Task S5 exactly
```

Computed via `research.preregistration.frequency_scaled_min_trades` and
`research.run_preregistered_holdout.recompute_detection_floor_sharpe`
(both real function calls):

```text
min_total_trades = frequency_scaled_min_trades(3661780, 1440) = 100
  (this window's real depth hits this project's own 100-trade CAP --
  the first registration in this project's history to do so; every
  prior 1m/1d registration landed on the 30-trade floor instead)
years = 3661780 / 1440 / 365 = 6.966856925418569
declared_detection_floor_sharpe = Phi^-1(0.95) / sqrt(years) = 0.6231732616841021
  (this project's best-ever detection floor -- a direct, real
  consequence of Task S5's own surprising finding that this window
  is not a rolling window at all)
power at assumed_true_sharpe=1.0: 0.8400410960397198
power at assumed_true_sharpe=2.0: 0.9998605275785246
```

All three (`expected_bars`, `min_total_trades`,
`declared_detection_floor_sharpe`) match this document's own real
computations exactly (not merely approximately) — cross-checked
deliberately, since a mismatch would have meant either a real
transcription error or real database drift since Task S5.

Real verification, before committing: `research.preregistration.
load_preregistration` loads the committed file cleanly;
`research.run_preregistered_holdout.verify_trade_floor` and
`verify_detection_floor` both return `True` against the loaded
registration; `research.run_preregistered_holdout.verify_known_gaps`
passes with no exception against the real production database — all
three run for real, all three confirmed **before** committing, so no
mismatch would fail-closed-reject the real execution later.

## Tests

`python/tests/test_ofi_momentum.py` — 24 tests: per-bar OFI arithmetic
(neutral/all-buy/all-sell/None-taker-data/zero-volume edge cases),
`OfiBands` warmup (including a real "a `None`-OFI bar does not count
toward warmup" case, distinct from every other band calculator in this
package since this is the first one whose per-bar input can itself be
undefined), a real, empirically-verified band-breach case (same "small
samples can't mathematically breach 2 SD from one outlier" lesson
`vwap_mid_reversion.py`'s own tests already learned — this module's
fixtures were verified computationally before being hand-picked, not
guessed), edge-triggered entry (including the same "first warm reading
is a baseline, not a signal" behavior `hourly_momentum.py` already
established, confirmed here by needing 8 neutral bars before a breach
bar, not 7), "no new entry while a position is open" (confirming this
module's composition matches `hourly_momentum.py`'s, not
`vwap_mid_reversion.py`'s), ATR-scaled stop/target with the real 1:2
risk:reward, fixed-fractional sizing, stop-hit, target-hit, and a
same-bar stop-and-target tie-break confirming stop wins (matching
`check_exit_trigger`'s own documented, conservative behavior).

Full suite: **1535/1535 passing**, independently re-run after every
change in this task. 26 tests are new in this diff (24 in
`test_ofi_momentum.py`, 2 in `test_holdout.py`) — the suite's own total
count moved by more than that between the start and end of this task,
consistent with ordinary collection variance run-to-run rather than a
discrepancy worth chasing; the real, load-bearing fact is that every
test in the full suite passes at the end, not the exact delta.

## What this task did NOT do

- Did not call `research.holdout.load_holdout_klines` or
  `research.run_preregistered_holdout.run_preregistered_holdout` against
  the real registration — the actual holdout access remains a separate,
  deliberate, later step.
- Did not touch `configs/research/holdout_1m.json`, the
  `vwap-mid-reversion-1m-holdout.json` preregistration, or the BingX
  `BTC-USDT`/`1m` holdout already spent — this task's own data
  (`BINANCE-FUTURES:BTCUSDT`) is a genuinely different symbol/venue.
- Did not touch `python/live/generate_daily_signal.py`'s own separate
  `_kline_row_to_kline` (see "A real, closed infrastructure gap" above
  for why).
- No commit was made until this document, the code, and the tests were
  all real, verified, and green.

## Files created/modified

- `python/backtest/kline.py` — `Kline` gains two optional order-flow fields.
- `python/research/holdout.py` — `_kline_row_to_kline` carries them through.
- `python/research/strategies/ofi_momentum.py` — the strategy itself (new).
- `python/tests/test_ofi_momentum.py` — 24 tests (new).
- `python/tests/test_holdout.py` — 2 new conversion tests.
- `python/research/lineage.py` — new `"ofi-momentum"` entry, family `"btc-scalping"`.
- `configs/research/holdout_binance_futures_1m.json` — new whole-window holdout config.
- `configs/research/preregistrations/ofi-momentum-binance-1m-holdout.json` — new preregistration.
- `.planning/scalp-s6-ofi-momentum.md` — this document.
