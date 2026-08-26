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
