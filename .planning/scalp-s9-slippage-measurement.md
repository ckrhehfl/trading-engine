# Scalping Strategy Research Task S9 — real slippage measurement

S8 Part 4 item 1, executed 2026-08-26. **No order was placed.** The
measurement used only public historical trade data and public order-book
queries, which is the route S8 named as preferred precisely because it
involves no order at all — the bounded VST demo fallback was not needed.

## Why this ran first

S8's own sensitivity analysis made every short-horizon conclusion
contingent on `SLIPPAGE_BPS`, and in the wrong direction: a strategy that
deliberately enters on volatility spikes gets worse fills than average,
while the assumed `SLIPPAGE_BPS = 10` was calibrated against a ~4bps
*typical* spread cited in Task S2. At **30bps of one-way slippage —
a 70bps round trip** — 15-minute holding failed even in the top 1% of
activity. So the assumption had to be measured before any signal
research could be trusted.

**Units, stated once because two different "30bps" appear in this
document.** `backtest/fill.py` applies `fee_bps` to every fill but
`slippage_bps` **only on the `GUARDED_MARKET` branch** — a `LIMIT` fill
pays fee alone. Every figure here is therefore a `GUARDED_MARKET` cost,
which is the only execution type scalping candidates may use under the
S2 policy restriction; none of it describes a limit-order cost. On that
branch a one-way cost is `FEE_BPS + SLIPPAGE_BPS` and a round trip is
twice that. The *baseline* being revised is `5 + 10 = 15bps` one way,
**30bps round trip** — the 30bps used in every viability table. The
sensitivity row quoted immediately above instead varies *slippage alone*
up to 30bps one way, a **70bps round trip**, also `GUARDED_MARKET`-only.
Every figure below is labelled explicitly.

## Method

**Effective spread, Binance USDT-M futures BTCUSDT.** Public `aggTrades`
from `data.binance.vision` carry `price` and `is_buyer_maker`:
`false` means the buyer was the taker, so the trade executed at the ask;
`true` means the seller was the taker, so it executed at the bid. For
each pair of *consecutive* trades whose direction flips, the absolute
price difference estimates the spread. Consecutive aggTrades are
typically milliseconds to seconds apart, so contamination from price
movement between them is small.

**This estimator is not a guaranteed upper bound, and an earlier draft
of this document wrongly claimed it was.** Price movement between the
two trades can *offset* the spread as easily as add to it — a taker-sell
at 100.00 followed by a taker-buy at 100.05, on a true 0.10 spread with
0.05 of downward drift in between, understates it. The observed
exact-zero pairs (0.9% quiet, 4.5% volatile) are consistent with exactly
that offsetting happening occasionally. What survives is weaker and
sufficient: with symmetric price noise the *median* is robust even
though individual observations are noisy in both directions, and the
result below is corroborated independently by the tick-multiple
distribution (below) and by a live BingX quote check, neither of which
uses this estimator.

Three days were chosen from our own 1m data to span the volatility
range, measured as the daily sum of |1-minute returns|:

| Day | Daily activity | Regime |
|---|---|---|
| 2026-08-08 | 869 bps | quietest of the last 206 days |
| 2026-05-28 | 5,467 bps | median |
| 2026-02-05 | 19,662 bps | most volatile (22x the quietest) |

**Depth**, same source, `bookDepth` daily files: cumulative resting
size within ±0.20% of mid, the narrowest band Binance publishes.

**BingX**, the venue actually traded: public
`GET /openApi/swap/v2/quote/depth` sampled live, since BingX publishes no
comparable history.

## Results

### Spread is one tick, in every regime

| Day | Direction-flip pairs | Median | 90th pct | 99th pct |
|---|---|---|---|---|
| 2026-08-08 (quiet) | 81,797 | **0.015bps** | 0.015 | 0.015 |
| 2026-05-28 (median) | 312,740 | **0.014bps** | 0.014 | 1.888 |
| 2026-02-05 (volatile) | 1,128,088 | **0.015bps** | 0.149 | 2.382 |

Tick size is $0.10, which at ~$65,000 is 0.0154bps — so the median *is*
the minimum possible value. This is not a measurement floor artefact
masking something larger: **98.3% of direction-flip pairs on the quiet
day and 75.2% on the most volatile day differ by exactly one tick**, and
exact-zero pairs (both trades at the same price) are only 0.9% and 4.5%
respectively, far too few to distort the median.

Even the 99th percentile on the most volatile day of the last seven
months is **2.4bps**, still a quarter of the assumed `SLIPPAGE_BPS = 10`.

### Depth makes market impact irrelevant at our size

Cumulative resting size within ±0.20% of mid:

| Day | Minimum | Median |
|---|---|---|
| 2026-08-08 | 491 BTC | 761 BTC |
| 2026-02-05 | **101 BTC** | 307 BTC |

The canary tier caps an order at 2% of account notional — roughly $2,000,
or **0.03 BTC**, on a $100k account. That is 0.03% of the *minimum*
depth observed on the most volatile day. Market impact is not a
meaningful cost component at this size.

### BingX, the venue we actually trade, is the same order of magnitude

Live samples: spread **0.026-0.038bps** ($0.2-0.3 at ~$78,400) — roughly
2x Binance's, still ~300x smaller than the assumed 10bps.

**This is a spot check, not a measurement.** Three samples at one moment
in one regime, against three days and 1.5 million observations spanning a
22x volatility range for Binance. It establishes the order of magnitude
and nothing more. One asymmetry worth noting from the samples: resting
size at the best bid was 26-33 BTC while the best ask held only
0.010-0.028 BTC, so a buy of 0.03 BTC could clear the top ask and take
the next level — an impact of about one tick, consistent with everything
above. A proper BingX distribution needs the depth endpoint polled over
days across regimes; that is real but simple work, and it is the honest
prerequisite before these figures are treated as venue-specific rather
than indicative.

## The revised cost structure

| Component | One way | Basis |
|---|---|---|
| Taker fee, VIP0 perpetual futures | 5.000 bps | published schedule, both venues (S2) |
| Effective half-spread | ~0.015 bps | BingX live samples (~0.03bps spread) |
| Market impact at 0.03 BTC — **Binance** | ~0 | 0.03% of the 101 BTC minimum ±0.20% depth |
| Market impact at 0.03 BTC — **BingX** | **~1 tick (~0.013 bps)** | best ask held 0.010-0.028 BTC in the live samples, so the order can clear it |
| **Conservative `SLIPPAGE_BPS`** | **1.000 bps** | ≈65x the measured half-spread |
| **Total one way** | **6 bps** | |
| **Round trip** | **12 bps** | was assumed 30 |

**Market impact is not uniformly negligible across venues, and the two
must not be merged.** On Binance it genuinely rounds to zero at this
size. On BingX the live samples showed a thin best ask (0.010-0.028 BTC
against a 26-33 BTC best bid), so a 0.03 BTC buy can clear the top level
and take the next — roughly one tick of impact, on one side only. That
is still far inside the `SLIPPAGE_BPS = 1` allowance, but it is a real
venue difference, observed rather than assumed, and it is one of the
reasons that allowance is set loose rather than tight.

`SLIPPAGE_BPS = 1` is deliberately ~65x the measured half-spread rather
than a tight fit, to absorb the BingX-vs-Binance gap, regime variation
not captured by three days, and the top-of-book asymmetry above.

**The fee now dominates completely — it is ~330x the spread.** That is
the single most consequential consequence of this measurement, and it
redirects effort: at these numbers, *reducing the number of round trips*
matters far more than improving execution precision, and the gap between
taker and maker fees becomes a first-order design question rather than a
detail. Both prior candidates traded ~70 and ~89 times per day.

## Effect on horizon viability

Median absolute move as a multiple of the round trip, from S8 Part 2:

| Holding | at 30bps (assumed) | at 12bps (measured) |
|---|---|---|
| 5 min, all moments | 0.23x | 0.58x |
| **15 min, all moments** | 0.40x | **0.99x** |
| 15 min, top 10% activity | 1.09x | **2.73x** |
| 15 min, top 1% | 2.08x | **5.21x** |
| **30 min, all moments** | 0.55x | **1.37x** |
| 1 hour, all moments | 0.76x | **1.91x** |

**This does not make any strategy viable**, and the S8 language stands:
these are unsigned absolute moves. Covering cost requires direction, and
none of this speaks to direction. What it changes is which horizons are
*excluded on cost grounds* — and the answer is now far fewer of them.
15-minute holding sits at roughly breakeven even unconditionally, and
clears comfortably once entries are restricted to elevated activity.

## What this corrects

Task S2 set `SLIPPAGE_BPS = 10` as "~2.5x the cited ~4bps typical
BTC-USDT spread," disclosed at the time as a reasoned estimate rather
than a measurement. The measurement now says the ~4bps figure does not
describe BTCUSDT perpetual futures on a major venue: the real effective
spread is ~0.015-0.04bps, two to three orders of magnitude tighter. The
cited figure was most likely drawn from spot markets, smaller venues, or
a cross-pair average. S2's own reasoning was sound given the input; the
input was wrong for this instrument.

## Limits, stated plainly

- **BingX is spot-checked, not measured.** See above. Everything
  regime-conditional is Binance.
- **Three days, not a continuous series.** They span a 22x volatility
  range by construction, but three days cannot capture a market
  dislocation, an exchange outage, or a funding-settlement spike.
- **Latency and adverse selection are not measured here.** The gap
  between deciding on a bar close and actually filling is modelled
  separately by `backtest/fill.py`, which fills at the *next* bar's open
  before applying `SLIPPAGE_BPS` at all, so it is not double-counted —
  but neither is it validated by this task.
- **Taker fee now carries the entire cost structure.** If the 5bps VIP0
  figure is ever wrong, or a tier change applies, the conclusion moves
  proportionally. It was confirmed in S2 against both venues' published
  schedules and is not re-verified here.
- **Nothing here is a directional or expectancy claim.**

## Reproduction

Data: `data.binance.vision` public daily archives, `aggTrades` and
`bookDepth`, `futures/um`, symbol `BTCUSDT`, dates as tabled. BingX:
`GET https://open-api.bingx.com/openApi/swap/v2/quote/depth?symbol=BTC-USDT&limit=5`,
3 samples, 2026-08-26. Day selection came from the local
`BINANCE-FUTURES:BTCUSDT` 1m klines, daily sum of |1m returns|, over
2026-02-01 onward (206 days).

Note for anyone re-running: `bookTicker` daily files would be the more
direct source for spread, but Binance **stopped publishing them after
2024-03-30** — verified by HTTP 404 on every later date probed. That is
why this task used `aggTrades` direction flips instead.
