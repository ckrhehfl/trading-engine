# Scalping Strategy Research Task S4: VWAP-to-mid reversion (commit phase)

## Scope note, mirroring `sr-u`'s own precedent exactly

This task commits **the strategy implementation and its pre-registration**.
It does **not** execute either against the 1m whole-window holdout.
Execution is a separate, later step, which loads `configs/research/
holdout_1m.json`'s holdout klines via `research.holdout.load_holdout_klines`
and runs the committed registration exactly once. That separation is the
entire point of pre-registering: the specification (this task) must be
committed, hashed and git-tracked *before* the data (a later step) is ever
loaded, or `N=1` is merely asserted rather than provable.

**No real BTC-USDT `1m` price/volume content -- not the holdout window,
not any candidate research split (there is none, see below), not one
bar's close or volume value -- was read while writing this task.** Every
`Kline` in `python/tests/test_vwap_mid_reversion.py` and
`python/tests/test_verify_1m_gaps.py` is hand-built (flat/synthetic
prices, or a controlled temp database with hand-inserted rows), matching
`test_daily_tsmom_ensemble.py`'s own inviolable-rule precedent exactly.
The only real-database contact this task made was **metadata-only**:
`MIN(open_time_ms)`, `MAX(open_time_ms)`, `COUNT(*)` for the real
`BTC-USDT`/`1m` table (to get exact, non-hand-typed values for
`configs/research/holdout_1m.json`'s cutoff and the preregistration's
`data.start_ms`/`end_ms`/`expected_bars`), and a real run of
`data.store.find_missing_ranges` against the real database (to confirm
the 2 known gaps and nothing else -- the same function
`python/research/verify_1m_gaps.py` wraps for the real preflight check).
Both are timestamp/count metadata, never a price or volume value for any
real bar -- they don't inform any decision about whether the strategy
*works*, which is the property a holdout protects.

## Where this sits in the project's sequence

CLAUDE.md's "Scalping Strategy Research" section, Tasks S0-S3, are all
merged (`main`, PRs #107-#110). S1 confirmed real BingX `BTC-USDT`/`1m`
retention (910,040 bars, 2 known real gaps). S2 recommended
`fee_bps=5`/`slippage_bps=10` for scalping `GUARDED_MARKET` candidates,
with real citations. S3 computed the real detection-floor math (a
subagent read `research/eligibility.py` directly) and found PSR/DSR
resamples equity curves to *daily* granularity regardless of
`bars_per_day` -- so 1m's 910,040 raw bars don't buy the statistical
power they look like they should: the *entire* 631.98-day window's
detection floor is only ~1.25, barely better than the already-spent 1h
research window's own ~1.21. Human-confirmed design decision (2026-08-25,
via a real tradeoff presented as a structured question, not silently
picked): 1m scalping uses a **single pre-registered holdout** against
the full window, matching `daily-tsmom-ensemble`'s own
`sr-u`/`sr-v`/`sr-aa`/`sr-ab` precedent, not a walk-forward
research-split structure.

Task S4 is the first real candidate under this design: **VWAP-to-mid
deviation short-term reversion**, CLAUDE.md's own recommended first
candidate (the most directly, recently, and strongly supported
candidate found during Task S0's literature research -- order-flow
imbalance was explicitly *not* recommended first, given real published
evidence its effect is weaker at scalping-relevant frequencies).

## The strategy specification (decided before implementation, not redesigned mid-build)

- **Signal**: a 20-bar (20-minute) rolling volume-weighted average price
  (VWAP), with a Bollinger-Band-shaped envelope around it:
  `upper/lower = vwap +/- 2 * sample-stdev(closes)` over the same
  20-bar window. `close < lower` -> oversold -> want LONG. `close >
  upper` -> overbought -> want SHORT.
- **Sizing**: constant-target volatility at 20% annualized, reusing
  `research/strategies/volatility_targeting.py` unmodified -- mirrors
  `daily_tsmom_ensemble.py`'s composition (full-notional baseline scaled
  by `vol_scalar`), not `mean_reversion.py`'s (ATR-sized base scaled by
  an inverted ADX regime weight).
- **Deliberately absent**: no ADX regime gate, no ATR stop/target, no
  risk:reward grid, no funding signal.
- **`fit()` does no search**: `total_candidates: 1`.
- Orders via `GUARDED_MARKET` only (hardcoded, not a parameter),
  edge-triggered on signal *state* change, self-contained state,
  look-ahead-safe.

Implementation: `python/research/strategies/vwap_mid_reversion.py`
(`VwapMidBands`, `VwapMidReversionStrategy`, `VwapMidReversionTrainable`).
Registration: `configs/research/preregistrations/vwap-mid-reversion-1m-
holdout.json`. Holdout config: `configs/research/holdout_1m.json`
(new -- see its own `rationale` field for the whole-window-as-holdout
mechanism). Gap preflight: `python/research/verify_1m_gaps.py`. Lineage:
a new `"btc-scalping"` family entry in `research/lineage.py`.

## Design decisions, with reasoning

### 1. Why 20-period / 2-standard-deviations, not literature-searched or grid-searched

Real 2026-08-25 web research (two independent searches) found: (a) no
peer-reviewed academic paper pinning down a specific rolling (non-
session) VWAP lookback period for scalping-scale reversion -- the
closest formal academic work found (an arXiv paper on VWAP *execution*
optimization for crypto) addresses a different problem entirely; (b) 2
standard deviations is the single most-cited VWAP-reversion entry
threshold across multiple independent practitioner/technical-analysis
sources (one cites a "63% reversion rate from 2-standard-deviation
extensions"; another calls 2-SD entries "among the most reliable
intraday setups"); (c) 20 (or 50) periods on a 1-minute chart is a
common practitioner convention for a VWAP/moving-average scalping
anchor.

Given no academic anchor exists specifically for the period, the
decision was to **reuse this project's own already-established
external convention** rather than invent a new one: `mean_reversion.py`'s
`DEFAULT_BOLLINGER_PERIOD = 20` / `DEFAULT_BOLLINGER_K = Decimal("2")`
are the canonical John Bollinger convention -- external, not fit to this
project's own data, already used unmodified for a *different*
(non-volume-weighted) indicator in this same package. Reusing the exact
same numbers for VWAP's own band keeps this candidate's
`free_parameter_count: 0` claim honest in the same way MOP's literature-
sourced 21/63/126/252 lookback set keeps `daily_tsmom_ensemble`'s claim
honest: neither number was chosen by looking at this project's own
results.

**Alternative considered and rejected**: deriving a *new* period from
first principles (e.g. matching some multiple of the average scalping
holding-period target). Rejected because doing so would itself be a
form of fitting -- "chosen to make the strategy behave a certain way on
this asset" -- exactly what the whole zero-fitted-parameter discipline
exists to avoid. Reusing an already-external, already-precedented
number is strictly safer.

### 2. Band width from raw price dispersion, not the deviation series

`VwapMidBands.update()` computes `stdev` from the trailing window's raw
**close prices** (`variance = sum((c - mean)**2 for c in closes) /
(period - 1)`), the identical computation `mean_reversion.BollingerBands`
already performs -- only the **centerline** changes (volume-weighted
average instead of simple average); the band-width computation is
untouched. An equally defensible alternative would band the *deviation*
series (`close - vwap`) instead. Not chosen, deliberately: it would
introduce a second, untested statistical convention into this codebase
for no evidenced benefit, where reusing `BollingerBands.update()`'s
exact, already-proven shape (just with `mean` replaced by a
volume-weighted average) keeps the new code's only genuinely novel
surface area small and specifically scoped to the VWAP computation
itself.

### 3. Exit rule: reversion to neutral, or a same-bar flip -- no ATR stop, no fixed holding period

`daily_tsmom_ensemble.py`'s composition was chosen as the template over
`mean_reversion.py`'s specifically because `mean_reversion.py`'s exit is
ATR-stop/target-based (a position holds until a fixed-multiple stop or
target is hit, independent of the Bollinger signal itself reverting),
which is not appropriate here: this strategy's own *thesis* is "price
reverts to VWAP," so the natural, parameter-free exit is "the signal
state itself says the reversion happened" (transition back to `0`) --
not an independently-chosen ATR multiple, which would be a new free
parameter with no literature/convention anchor at scalping timescales.
A direct flip to the opposite extreme (`+1` straight to `-1`, no bar at
neutral in between) is a single `OrderIntent` bundling close-then-reopen,
reusing `daily_tsmom_ensemble.DailyTsmomEnsembleStrategy._transition_to`'s
exact same-bar-flip pattern (itself resting on
`metrics.position.PositionTracker`'s already-proven over-sized-order
handling).

### 4. The disclosed, deliberately unmitigated unbounded-holding-period risk

Real web research repeatedly and consistently warned that VWAP/Bollinger
mean-reversion strategies fail badly in strongly trending markets
without a regime filter -- "the filter is what separates a tradeable
strategy from one that blows up." This project's own `mean_reversion.py`
already encodes exactly this lesson (an inverted ADX regime weight,
built specifically because an earlier, un-gated version of that
strategy's own research showed a real 66%-win-rate-but-net-loss failure
mode from trend-riding losses).

**This candidate deliberately does not add an ADX gate or an ATR stop.**
Reasoning: every one of those mechanisms came from this project's own
117-trial 1h-window search (`sr-r`'s statistical close-out) -- reusing
them here would silently reintroduce free parameters (which ADX
thresholds? which stop multiple?) from a search this project has
already spent and cannot honestly claim as "zero-fitted" for a *new*
signal family. The real risk this creates -- a position held
indefinitely through a strong trend while price never reverts inside
the band -- is accepted and disclosed, not engineered around: it will
show up honestly in the Eligibility Bar's own max-drawdown gating
criterion (20% ceiling) if it materializes, rather than being silently
pre-empted by a mechanism this candidate has no principled, unfit basis
to choose.

### 5. `funding_included: false` -- a structural reason, not `daily_tsmom_ensemble`'s reason

`daily_tsmom_ensemble`'s registration left funding P&L out because no
`load_holdout_funding` loader exists yet in `research/holdout.py`, and
building one would itself have touched the holdout window ahead of the
registered run. That reason does not really apply here in the same way
(a loader could in principle be built), but a **stronger, structural**
reason does: this strategy's own holding periods are minutes to tens of
minutes (matching the "retail scalping" scope this whole research
direction is named for), and BingX's funding settlement cadence is 8
hours. A position closing within tens of minutes essentially never spans
a funding settlement event -- funding P&L is not merely unbuilt here, it
is economically irrelevant to this strategy's own return profile at the
holding periods it actually produces.

### 6. Whole-window holdout mechanism: `holdout_1m.json`'s `holdout_side="after"` with the cutoff at the real earliest bar

Every 1m bar that exists at all is, by construction, on-or-after the
real earliest bar BingX retains (`2024-11-30T16:00:00Z`, queried
directly from the real database, not hand-typed). Setting
`holdout_cutoff_ms` to exactly that timestamp with `holdout_side="after"`
(the same default `holdout.json`/`holdout_1h.json` already use) makes
the holdout side cover the *entire* real dataset, and the research side
`(-inf, cutoff)` trivially empty -- verified directly (`research.holdout.
load_research_klines` against a wide range clamps to an empty range and
raises, exactly as it already does for any other config's out-of-range
research request). This is not a workaround or an edge-case exploit of
the mechanism; it is the natural, intended consequence of Task S3's own
design decision to reserve the entire window rather than split it.

### 7. Gap preflight as a standalone module, not a change to `run_preregistered_holdout.py`

CLAUDE.md's Task S3 design requires a real pre-access check against the
2 known 1m gaps, failing closed on anything unexpected, before the real
holdout access happens. `research/run_preregistered_holdout.py` is
shared, already-proven infrastructure (it has already executed
`sr-v`/`sr-ab`'s real holdout confirmations for a different strategy
family) -- every other interval's holdout confirmation has zero known
gaps, so folding a gap check into that shared runner would add an
interval-specific concern (1m's own 2 known gaps) to a generic module
that doesn't need it, for every future non-1m holdout registration too.
`python/research/verify_1m_gaps.py` is therefore a standalone,
read-only preflight (wraps `data.store.find_missing_ranges`, already
proven -- it's the same function that originally found these 2 gaps
during Task S1's real backfill), run manually before the real holdout
access, not wired into the runner itself. It fails closed on any gap set
that differs from the 2 already-disclosed ones (fewer, more, or
relocated) -- not on the 2 known gaps themselves, which are real,
permanent, and already accounted for.

### 8. `outcome_interpretation` follows `sr-ab`'s framing, not `sr-u`/`sr-v`'s

`sr-u`/`sr-v`'s own INCONCLUSIVE text said that outcome "ends the
BTC-only price-signal research program... the next move is a named
structural change" -- correct for that attempt, which really was the
terminal event for an eight-family, 117-trial research line. This
registration is different in kind: it is the *first* candidate within a
still-young, multi-candidate Scalping Strategy Research direction (order-
flow imbalance and other candidates remain explicitly named and
untested in CLAUDE.md's own Task S4 text). Copying `sr-u`/`sr-v`'s
"ends the whole program" framing here would overclaim the consequence of
one candidate's result. `sr-ab`'s own registration faced exactly this
same distinction (a second attempt at an *already*-INCONCLUSIVE
hypothesis, needing to explain precisely what its own result would and
would not resolve) and is the closer precedent, reasoning-wise, even
though the concrete situation differs (there, a second attempt at the
same hypothesis on different data; here, the first attempt at a
different hypothesis within the same young research direction). Both
share the same real underlying principle: an outcome's stated
consequence should be scoped to what was actually tested, not
generalized to a broader research program the specific result doesn't
speak to.

## Real values, computed not hand-derived

Queried directly against `python/data/var/klines.sqlite3`
(`SELECT MIN(open_time_ms), MAX(open_time_ms), COUNT(*) FROM klines
WHERE symbol='BTC-USDT' AND interval='1m'`):

```text
min_open_time_ms: 1732982400000  (2024-11-30T16:00:00Z)
max_open_time_ms: 1787585160000  (2026-08-24T15:26:00Z)
count: 910040
end_ms (half-open, max+60000): 1787585220000  (2026-08-24T15:27:00Z)
```

Computed via `research.preregistration.frequency_scaled_min_trades` and
`research.run_preregistered_holdout.recompute_detection_floor_sharpe`
(both real function calls, not hand arithmetic):

```text
evaluated_days = 910040 // 1440 = 631
min_total_trades = max(30, min(100, 631 // 20)) = 31
years = 910040 / 1440 / 365 = 1.7314307458143072
declared_detection_floor_sharpe = Phi^-1(0.95) / sqrt(years) = 1.2500422565371045
power at assumed_true_sharpe=1.0: Phi(sqrt(years)*1.0 - Phi^-1(0.95)) = 0.3710720966923513
power at assumed_true_sharpe=2.0: Phi(sqrt(years)*2.0 - Phi^-1(0.95)) = 0.8381353432575853
```

All three (`expected_bars`, `min_total_trades`, `declared_detection_floor_sharpe`)
match CLAUDE.md's own already-published Task S3 approximations
(~910,040 / ~31 / ~1.25) exactly, not merely approximately -- cross-
checked deliberately, since a mismatch would have meant either CLAUDE.md's
own prior computation or this task's real database state had drifted.

Gap preflight, run for real against the actual database
(`python -m research.verify_1m_gaps --start-ms 1732982400000 --end-ms
1787585220000`, and independently via a direct `find_missing_ranges`
call): confirms exactly the 2 known, disclosed gaps
(`[2025-04-25T06:54:00Z, 2025-04-25T06:57:00Z)`,
`[2026-02-13T20:32:00Z, 2026-02-13T20:36:00Z)`), nothing more, nothing
less.

## Real CodeRabbit review findings on this task's own PR, and how each was handled

Two review rounds, five findings total, verified individually rather than
applied blindly:

1. **Real, minor**: two fenced code blocks above were missing a language
   tag (markdownlint MD040). Fixed.
2. **Not a real bug -- verified and rejected, not silently applied.**
   The reviewer claimed `VwapMidReversionStrategy.__call__`'s first
   post-warmup band reading, if already at an extreme, would never
   produce an entry, because `_signal_state` would still be `None` at
   that point. Independently re-run against the actual code and the
   actual test suite before deciding: `test_enters_long_on_transition_
   into_oversold` and `test_a_sizing_rejected_entry_is_retried_once_
   sizing_becomes_available` -- the exact two tests the finding claimed
   would fail -- both pass, confirmed via a direct, isolated
   `pytest -k` run, not just the full-suite green light. Tracing the
   actual code: the unconditional `if not entry_rejected_by_filters:
   self._signal_state = current_signal` at the end of `__call__` runs on
   *every* call, warmup included (it sits outside the `if bands is not
   None:` guard, confirmed via `cat -A` to rule out a
   whitespace-misreading on either side) -- so `_signal_state` is set to
   `0` after the very first bar, never observed as `None` on any bar
   where `bands is not None` could be true. This project's own review
   discipline ("verify each finding against current code, fix only
   still-valid issues, skip the rest with a brief reason") was applied
   here for real, not just quoted: skipped, with this paragraph as the
   documented reason, rather than accepting a plausible-looking but
   factually-contradicted suggested diff.
3. **Real, round 1's most substantive finding**: `verify_1m_gaps.py` was
   originally a fully standalone module -- nothing in the real execution
   path called it automatically. The reviewer correctly traced
   `research/run_preregistered_holdout.py`'s real code and confirmed it
   never calls `verify_1m_gaps`, so running the same raw command every
   prior holdout confirmation (`sr-v`, `sr-ab`) used
   (`python -m research.run_preregistered_holdout <path>`) would consume
   the single-access claim even with an unexpected gap present -- a real
   gap between "a check exists" and "the check is structurally
   enforced." **First fix attempt (round 1): a dedicated wrapper
   script**, `research/run_vwap_mid_reversion_holdout.py`, that called
   `verify_1m_gaps` before calling `run_preregistered_holdout`, with
   both steps injectable so the short-circuit property was directly
   unit-tested. **Round 2's review correctly found this fix incomplete**
   (see finding 4 below) -- the raw `run_preregistered_holdout` command
   was still callable directly, bypassing the new wrapper entirely, so
   the "structurally enforced" claim was only true if the operator
   remembered to use the new command instead of the old one. **Superseded
   by a better design, not patched further**: the gap check now lives
   directly inside `research/run_preregistered_holdout.py` itself
   (`verify_known_gaps`/`UnexpectedKnownGapsError`), gated on a new,
   OPTIONAL `data.known_gaps` preregistration field rather than a
   hardcoded `1m`-specific constant -- a no-op (zero behavior change) for
   every registration that doesn't declare it, including every one
   already committed (verified directly: `daily-tsmom-ensemble-1d-
   holdout.json` still loads with `known_gaps=None` and is completely
   unaffected). Since this check now lives in the ONE function every
   holdout confirmation in this project already calls, there is no
   longer a second command to forget -- the bypass round 2 found is
   closed structurally, not by convention. `research/run_vwap_mid_
   reversion_holdout.py` and its test were deleted (redundant once the
   check moved into shared infrastructure); `verify_1m_gaps.py` survives
   as a standalone, `1m`-specific diagnostic CLI, no longer positioned as
   the enforcement mechanism.
4. **Real, round 2**: the round-1 wrapper fix above had two further real
   gaps, both closed by the same round-2 redesign rather than patched
   individually: (a) an operator could still bypass the gap check by
   calling `run_preregistered_holdout` directly instead of the new
   wrapper -- now impossible, since there is only one real command; (b)
   the wrapper's `preregistration_path` argument accepted any file while
   `verify_1m_gaps` always hardcoded `symbol="BTC-USDT"`/`interval="1m"`,
   so pointing the wrapper at a differently-scoped registration would
   have silently verified the wrong range against the wrong data --
   moot now, since `verify_known_gaps` reads `symbol`/`interval`/
   `start_ms`/`end_ms` directly from the SAME `prereg.data` the loader
   itself uses, never a separate hardcoded constant. A third round-2
   sub-point -- `force_reclaim_reason` accepts any non-blank string with
   no verifiable human-approval record -- was investigated and left
   unchanged: this is `research.holdout.load_holdout_klines`'s own
   existing, already-litigated, deliberately-permissive design (its own
   module docstring: "a mandatory, non-blank, human-written
   justification... The design intentionally puts the judgment call in a
   human's hands, not the software's"), already tracked as a real, open,
   separate question (github.com/ckrhehfl/trading-engine/issues/58,
   confirmed to actually exist via `gh issue view 58` before citing it,
   raised by CodeRabbit against the shared `sr-v` PR and deliberately not
   changed there either). Tightening it just for this one registration's
   own code path would create an inconsistent security model across
   registrations rather than fix anything -- out of this task's scope,
   not silently ignored.
5. **Real, round 2, minor**: `test_run_vwap_mid_reversion_holdout.py`'s
   `REAL_PREREGISTRATION_PATH` was a plain relative string, correct only
   when pytest is invoked with `cwd=python/` (this project's own
   established convention -- `test_daily_tsmom_ensemble.py` already uses
   the identical pattern) but fragile against any other invocation
   directory. Moot: that whole test file was deleted along with the
   wrapper it tested (see finding 3).

## What this task does NOT decide

Whether the real holdout access happens, and when -- that is a separate,
deliberate, human-confirmed step, matching `sr-u`->`sr-v`'s own
precedent of a real gap between committing a registration and spending
it. This task also does not decide the broader (2)-vs-(3) multi-symbol-
vs-different-data-source question CLAUDE.md's Strategy Research
Methodology section still leaves open for the BTC-only price-signal
research line generally -- scalping is a parallel, independent research
direction, not a resolution of that older open question.
