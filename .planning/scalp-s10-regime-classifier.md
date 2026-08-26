# Scalping Strategy Research Task S10 — regime classifier, and why it failed its own test

S8 Part 4 step 2, executed 2026-08-26. The classifier was built as
specified and works correctly. **Measured on our actual data at our
actual horizons, it carries no usable information, and should not be
wired into a strategy on the strength of the literature alone.** That
negative result is the main output of this task.

## What was built

`python/research/strategies/regime_classifier.py`, following S8 §3.3:
two axes, hysteresis, minimum dwell time, look-ahead safe.

| Axis | Source | States |
|---|---|---|
| Structure | ADX (existing `regime_weighting.AverageDirectionalIndex`) | `TRENDING` / `RANGING` |
| Volatility | ATR ratio — current ATR over the mean of the trailing 20 ATR readings | `EXPANSION` / `COMPRESSION` |

Almost nothing was invented. ADX's 14 period and 20/25 thresholds, and
ATR's 14 period, are existing project constants already documented as
conventional and not tuned to this asset. The one new number is the
20-reading ATR-ratio lookback, the conventional value the regime
literature names.

**Hysteresis needs no new thresholds**: ADX above 25 is trending, below
20 is ranging, and the 20-25 band holds whatever the previous state was.
The volatility axis is identical with 1.5 / 0.8. This is the same shape
`compute_regime_weight` already uses those constants for.

**Minimum dwell is derived, not picked**: it defaults to the ADX period
(14), on the reasoning that a label from a 14-bar indicator cannot
meaningfully re-decide faster than its own lookback.

**Deliberate design choices worth keeping if this is ever revisited**:
the ATR ratio excludes the current reading from its own denominator
(including it damps exactly the signal being measured); a flat window
returns `None` rather than dividing by zero; and an axis that warms up
*inside* its hysteresis band keeps returning `None` rather than seeding
a fabricated initial label.

16 tests, including the two that matter — a reading inside the band
holds the previous label, and classifying a prefix is unchanged by bars
that come after it. Full suite 1557 → 1573.

## Test 1: does it separate forward volatility? No.

Both spent 1m windows, every bar classified (100% resolved after warmup),
median forward absolute move per regime:

**Binance USDT-M futures BTCUSDT (3,661,780 bars)**

| Regime | Share | 15m median | vs 12bps cost | 60m median |
|---|---|---|---|---|
| trending / compression | 59.9% | 11.8bps | 0.98x | 22.8bps |
| ranging / compression | 24.1% | 11.9bps | 0.99x | 23.3bps |
| trending / expansion | 13.7% | 12.4bps | 1.03x | 22.6bps |
| ranging / expansion | 2.3% | 11.4bps | 0.95x | 22.0bps |

**BingX BTC-USDT (910,040 bars)** — same pattern, 9.9-10.7bps at 15m.

The whole range across four regimes is **11.4 to 12.4bps — a 9%
spread**. For comparison, the far simpler activity measure from S8 Part 2
(rolling 30-bar sum of |1m returns|) separates the same horizon from
11.9bps to 62.5bps, a **5x spread**.

A trivial rolling-sum-of-absolute-returns does the volatility-conditioning
job dramatically better than this classifier does.

The distribution is also badly unbalanced: 60% of all bars land in one
regime and 2.3% in another, which is not a useful partition even before
looking at what it predicts.

## Test 2: does the structure axis predict momentum vs mean-reversion? No.

The magnitude test above cannot answer this, and stopping there would
have repeated a mistake this project has now made twice — testing a
directional hypothesis with a magnitude measurement. So: correlation
between the trailing H-bar return and the forward H-bar return, per
regime, on **non-overlapping** samples.

| Structure | Horizon | Samples | past↔future correlation |
|---|---|---|---|
| ranging | 15m | 64,256 | **+0.0080** |
| trending | 15m | 179,856 | **−0.0037** |
| ranging | 60m | 16,136 | **+0.0143** |
| trending | 60m | 44,891 | **−0.0106** |

Positive means continuation, negative means reversal. Every value is
under 0.015 in absolute terms — **below even the low end of the 0.02-0.05
band S8 itself names as "a genuinely useful IC"**. By this project's own
calibration these are not signals.

The signs are mildly interesting and should not be over-read: ranging is
slightly *continuation* and trending slightly *reversal*, the opposite of
the textbook mapping. At magnitudes this small that is far more likely
noise than a real inverted effect, and no directional conclusion is drawn
from it here.

## Honest conclusion

The regime layer was specified in S8 on the strength of external
literature that identifies regime-blind mean-reversion as a classic
blowup — and that diagnosis of `vwap_mid_reversion` still stands. But
**this particular operationalisation of it does not work on this data at
these horizons**, and the measurement is what settles that, not the
literature.

Plausible reasons, none verified:

- **The ATR ratio normalises away the thing being measured.** It compares
  ATR to its own recent average, so a market that doubles its volatility
  and stays there returns to a ratio of 1.0. "Compression" therefore
  means "quieter than it recently was", not "quiet" — which is not what a
  cost-vs-move decision needs.
- **Hysteresis plus a 14-bar dwell makes labels very sticky**, which is
  what produces the 60%/2.3% imbalance.
- **ADX over 14 one-minute bars may simply be noise** at this granularity.

## What follows

1. **Do not gate anything on this classifier as it stands.** It is
   committed, correct, and tested, but nothing has shown it adds
   information.
2. **Use the activity measure for volatility conditioning** in S8 step 3
   (per-feature IC). It is simpler, already validated across a 22x
   volatility range, and separates forward movement 5x better.
3. If a regime layer is wanted later, the open question is whether an
   **absolute** volatility measure (ATR as a percentile of its own long
   history, rather than a ratio to its recent mean) separates where the
   ratio does not. That is a real, cheap experiment — but it is a new
   hypothesis, and it is not run here.

## Reproduction

Classifier: `research.strategies.regime_classifier.RegimeClassifier()`,
all defaults, fed every bar of each window in order. Test 1: median
`|close[i+h]/close[i] − 1|` in bps, grouped by label. Test 2: Pearson
correlation of trailing vs forward H-bar simple returns, stepping H bars
so samples do not overlap. Both on already-spent windows; no holdout
accessed, no strategy configuration selected, nothing logged to
`runs/experiments.jsonl`.

---

# Follow-up, same day: the hypothesis was right, and the fix is partial

The section above closed by naming an untested guess — that the ATR
*ratio* was the culprit, because it normalises away absolute level — and
called testing it a cheap experiment. It was, so it was run.

## The ratio was the whole problem, isolated cleanly

Three conditioners, same 3,661,780 bars, same global percentile buckets,
same forward 15-minute move. Only the measure differs:

| Conditioner | All bars | Top 1% | Separation |
|---|---|---|---|
| A. Activity — rolling 30-bar sum of \|1m returns\| (S8/S9 baseline) | 11.9bps | 62.5bps | **5.25x** |
| B. ATR **ratio** — ATR(14) ÷ mean of trailing 20 ATRs | 11.9bps | 14.6bps | **1.22x** |
| C. ATR **absolute** — ATR(14) ÷ price | 11.9bps | 62.0bps | **5.21x** |

B and C take **the same ATR(14) input**. The only difference is the
denominator — trailing mean versus price — and it accounts for the entire
gap. It was not the hysteresis, not the dwell, not ADX noise. C also
lands within 1% of the independent activity measure, which is a useful
cross-check: two unrelated formulations of "how much is this market
moving right now" agree, and the ratio disagrees with both.

## What changed in the code

`AbsoluteAtr` replaces `AtrRatio` as the default volatility axis
(`VolatilityAxis.ABSOLUTE`). It reports ATR as a fraction of price —
keeping the level — and returns its **percentile rank within a trailing
1,440-bar history** rather than a raw figure, so fixed thresholds stay
meaningful as the market's own volatility drifts across years.

Look-ahead safety with an affordable cost: the sorted reference snapshot
refreshes every 60 bars rather than every bar. That is safe in the only
direction that matters — the snapshot is always built from strictly older
bars, so a bar can never influence its own rank, and staleness makes a
rank slightly out of date rather than clairvoyant.

`AtrRatio` and `VolatilityAxis.RATIO` are kept **only so the negative
result stays reproducible**, and both say so in their own docstrings.

**A real property found by a test rather than assumed**: the absolute
measure forgets too. A trailing percentile decays once its window fills
with the new level — the difference from the ratio is timescale (1,440
bars versus 20, a 72x ratio), not kind. A permanently shifted regime
fades from both, which is correct: "high volatility" is only meaningful
against some reference.

## Re-characterisation: better, balanced, and still much weaker than the raw measure

| Regime | Share | 15m median | 60m median |
|---|---|---|---|
| trending / expansion | 29.6% | **15.6bps** | 28.8bps |
| ranging / expansion | 9.9% | **15.1bps** | 28.6bps |
| trending / compression | 43.9% | 10.0bps | 19.6bps |
| ranging / compression | 16.5% | 10.4bps | 20.5bps |

Two things, and the second matters more than the first:

1. **The volatility axis now works.** Expansion reads ~15.5bps against
   compression's ~10.2bps, and the label distribution is balanced
   (43.9/29.6/16.5/9.9) where before it was 59.9/24.1/13.7/**2.3**. BingX
   shows the same pattern at a slightly lower level. That is a genuine
   fix, not a rounding difference.

2. **The structure axis still contributes nothing.** Within a volatility
   state the two structures are indistinguishable — 15.6 vs 15.1 in
   expansion, 10.0 vs 10.4 in compression. Every bit of the separation
   comes from volatility. Combined with the directional test above (all
   correlations under 0.015), ADX has now failed to carry information on
   *both* axes it could plausibly have carried it on.

3. **Discretising costs most of the signal.** The classifier separates
   ~1.5x where the continuous measure it is built from separates 5.2x.
   That is the expected price of two states plus hysteresis plus a
   14-bar dwell, but it is a large price, and it means the label is a
   worse conditioner than the number underneath it.

## Revised conclusion

- **Use the continuous absolute volatility measure**, not the
  discretised regime label, wherever a conditioner is needed — including
  S8 step 3's per-feature IC work. 5.2x beats 1.5x, and nothing about
  the IC measurement needs a discrete label.
- **The regime label earns its place only where a discrete state is
  genuinely required** — for example gating "trade / do not trade" — and
  even then the structure axis should be dropped or replaced until
  something shows it adds information.
- **ADX is now measured as unhelpful here twice**, on magnitude and on
  direction. Keeping it in the classifier is not justified by anything in
  this task; it stays only because removing it is a separate change and
  the axis is inert rather than harmful.
- The earlier instruction stands in spirit and is narrowed in fact: do
  not gate on the *classifier* as a whole, and specifically do not rely
  on its structure axis. Its volatility axis is now sound, just weaker
  than using the underlying measure directly.

---

# Second follow-up: gaps now fail closed

Raised on review of this PR, against this project's own "gap-aware
pre-access check, fail-closed" rule — and it was a real hole.

ADX and ATR both accumulate across consecutive bars. The classifier fed
them whatever arrived, without checking that two bars were actually
adjacent. A missing minute, a duplicate, or an out-of-order bar therefore
produced an indicator computed across a discontinuity, and a perfectly
confident-looking `Regime` derived from it. **This project's 1-minute data
has real gaps** — 2 in the BingX window, 1 in Binance — so the hole was
live, not hypothetical.

`update()` now checks contiguity **before touching any state**. The bar
interval is inferred from the first two bars (or supplied explicitly via
`expected_interval`), and any bar not sitting exactly one interval after
its predecessor resets every accumulated indicator and returns `None`
until warmup completes again. A `discontinuities` counter exposes how
often that happened, so a caller measuring over data with known gaps can
assert the count is what it expects.

**Verified against the real data, and it matches the documented gap counts
exactly:**

| Window | Bars | Discontinuities detected | Gaps on record |
|---|---|---|---|
| `BINANCE-FUTURES:BTCUSDT` | 3,661,780 | **1** | 1 |
| `BTC-USDT` (BingX) | 910,040 | **2** | 2 |

That is an independent confirmation of the gap counts in CLAUDE.md's
Exchange API Facts, arrived at by a completely different route (streaming
contiguity check) than the one that originally found them
(`find_missing_ranges` over the stored table).

Five existing tests failed when this landed — every one because its
fixture jumped in time (`start=100` after twelve bars) rather than because
the check was wrong. Fixtures made contiguous; four new tests cover the
gap, duplicate, and out-of-order cases, an explicit `expected_interval`
overriding inference, and that contiguous data never trips the counter.

Suite 1581 → 1585.

**Scope note**: `AtrRatio` and `AbsoluteAtr` themselves remain
time-unaware by design. They are indicator primitives fed by a caller;
the contiguity contract lives at the classifier boundary, which is the
only place that sees a bar stream rather than a sequence of readings.
