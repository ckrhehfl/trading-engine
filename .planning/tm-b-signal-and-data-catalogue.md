# Trade Management Task B — what the exchange actually gives us, and what each thing means

**Reference document, written 2026-08-29.** Not a plan and not a
proposal: a catalogue, so that the next strategy is chosen from the full
set of available inputs rather than from whatever happened to be at hand.

Three columns matter throughout and are never merged:

- **available** — can we actually fetch it, verified against this repo
- **what it means** — the standard reading, from exchange docs
- **what WE measured** — this project's own result, where one exists

The third column is the point. A catalogue of what indicators *claim* is
freely available anywhere; a catalogue annotated with what they did on
this project's own data is not, and several of our measurements
contradict the standard reading.

---

## 1. What we collect today

Verified against `python/data/var/klines.sqlite3` and the adapters:

| Table | Fields | Coverage |
|---|---|---|
| `klines` | `open_time_ms, open, high, low, close, volume, taker_buy_base_volume, taker_buy_quote_volume` | BTC 1m/15m/1h/1d, ETH 1d, Binance futures 1m (3.66M bars), Binance spot 1d |
| `funding_rates` | `funding_time_ms, funding_rate, mark_price` | BTC-USDT, 6,199 settlements, 2020-11 → 2026-07 |
| `macro_series` | FRED series | `DFII10`, `SP500`, `DGS10`, `DTWEXBGS` |

**That is six price fields, two flow fields, and a funding rate.** Every
strategy this project has ever built was made from that.

## 2. What the exchange also offers and we have never fetched

From Binance USDⓈ-M futures docs (BingX exposes close equivalents):

| Data | Endpoint | Status here |
|---|---|---|
| **Open interest** | `/fapi/v1/openInterest`, `/futures/data/openInterestHist` | ❌ never fetched |
| **Global long/short account ratio** | `/futures/data/globalLongShortAccountRatio` | ❌ |
| **Top-trader long/short ACCOUNT ratio** | `/futures/data/topLongShortAccountRatio` | ❌ |
| **Top-trader long/short POSITION ratio** | `/futures/data/topLongShortPositionRatio` | ❌ |
| **Taker buy/sell ratio (aggregated)** | `/futures/data/takerlongshortRatio` | ⚠️ we derive our own from kline `taker_buy_base_volume` |
| **Order book depth** | `/fapi/v1/depth`, `@depth` WS | ❌ |
| **Liquidations** | `/fapi/v1/allForceOrders`, `@forceOrder` WS | ❌ |
| **Aggregated trades** | `/fapi/v1/aggTrades` | ⚠️ used once, for the S9 slippage measurement only |

### Constraints that shape what is worth building

- **The `/futures/data/` ratio endpoints keep only 30 days of history.**
  Anything longer requires our own persistence, started now and
  accumulated forward. A backtest over years is impossible from a cold
  start — this is the single biggest practical limit on the whole
  positioning-data family.
- **Liquidation data is throttled, not complete.** Since 2021-04-27 only
  the largest liquidation per 1000ms window is published. Any aggregate
  computed from it is a **lower bound**, not a measurement.
- **Depth has no WebSocket snapshot** — a REST snapshot must be merged
  with the incremental stream, and sequence gaps require a restart.
- Rate limits: taker ratio 1000 req / 5 min per IP; the `/futures/data/`
  family is REST-only, so everything must be polled.

## 3. The signal families, and what each is claimed to indicate

### 3a. Price-derived (we have the data; heavily tested here)

| Family | Reading | **What we measured** |
|---|---|---|
| **Trend / momentum** | Past direction persists | `daily-tsmom`: real but weak. BTC PF 1.72, ETH 1.10, correlation 0.67 so no diversification. Nearly cleared the bar on one window |
| **Mean reversion** | Extremes revert | At 1m: **the edge lives inside the adverse excursion.** No stop of any width helps, entering later does not help (S14/S15) |
| **Volatility** | Clusters, and is forecastable | **The one thing that measurably works.** Absolute ATR/price separates forward movement 5.21x; the ATR *ratio* separates only 1.22x (S10) |
| **Structure (ADX, ranges)** | Trend vs range regime | **Nothing.** ADX carried no information on either axis it could have (S10); regime switching failed non-overlapping t-tests (this session) |
| **Support / resistance** | Prior levels attract and repel | Distance to prior-day high: rank IC −0.030 at 60m — real but small (S11) |

### 3b. Flow-derived (partially available)

| Family | Reading | **What we measured** |
|---|---|---|
| **Taker buy/sell imbalance** | Who is paying up to move price | IC −0.027 at 15m, and **uncorrelated with every price feature at \|r\| ≤ 0.006** (S11). The only genuinely independent information source this project has found |
| **Order flow imbalance (OFI)** | Aggressive flow direction | Traded as momentum in S6 and failed; S11 later measured the IC as **negative**, i.e. S6 may have been on the wrong side |
| **Volume** | Participation confirms moves | Volume ratio cleared no bar in S11's sweep |
| **Order book depth** | Passive liquidity, near-term S/R | Not measured. Easiest data to spoof; standard advice is to cross-check against actual fills |

### 3c. Positioning-derived (available, never fetched)

| Family | Reading | Status |
|---|---|---|
| **Open interest** | How much leverage exists. Rising OI + rising price = new longs; rising OI + falling price = new shorts; falling OI = deleveraging. **Directionless alone** | Untested |
| **Global long/short ratio** | Retail crowding; contrarian at extremes. Headcount-weighted | Untested |
| **Top-trader position ratio** | Size-weighted whale positioning. The more informative of the two | Untested |
| **Global vs top divergence** | The textbook "smart money vs retail" setup | Untested |
| **Liquidations** | Forced deleveraging; clusters mark capitulation | Untested, and **only a lower bound** |

### 3d. Carry / structural (we have the data)

| Family | Reading | **What we measured** |
|---|---|---|
| **Funding rate** | Leverage demand. Positive = longs pay shorts | **+8.4%/yr if continuously collected, positive in all 7 years, 78% of settlements positive.** Requires a spot leg to capture delta-neutral |
| **Basis (spot vs futures)** | Carry, converges at expiry | Mean **−0.015%** on Binance — already arbitraged away |

## 4. How families combine — the actual point

The exchange docs' own framing, which is the clearest statement of why
single indicators keep failing:

> - **open interest** = how much leverage exists
> - **long/short ratios** = which direction that leverage leans
> - **taker buy/sell** = who is currently paying up to move price
> - **depth** = what is passively standing in the way
> - **liquidations** = leverage being forcibly removed

Each answers a different question. **A single indicator answers one and
is silent on the rest**, which is precisely the shape of every candidate
this project has rejected: `vwap-mid-reversion` (price only),
`ofi-momentum` (flow only), `selective-reversion` (price + flow, two of
five).

The worked example the docs give is a *conjunction*, not an indicator:

> high OI + extreme global long ratio + negative taker flow + thin bid
> depth = the textbook precondition for a long-liquidation cascade

Four families, three of which we have never fetched.

## 5. What this catalogue implies, stated plainly

**The operator's instinct that our strategies felt "too simple" is
supported by this.** Not as a matter of taste — the strategies used two
of five available families, and the two we used are the two most heavily
mined by everyone else.

But two cautions that this project has already paid for:

1. **More inputs multiply the ways to overfit.** `N` is 127. A
   conjunction of five conditions has vastly more configurations than a
   threshold on one. Any use of this catalogue must specify the
   conjunction *before* looking, not search over it.
2. **The positioning family has only 30 days of history.** It cannot be
   backtested from a cold start at all. Using it means starting
   collection now and waiting — which is a real cost to state up front,
   not discover later.

## 6. The honest sequencing question this raises

A conjunction across families is the most promising *untested* direction
this catalogue reveals. It is also the one that cannot be validated for
months, because the positioning data does not exist historically.

That is a genuine tension and it is not resolved here. What the
catalogue does settle is that "we ran out of ideas" was never true:
**three of five signal families have never been touched, and the
combination the exchange's own documentation names as the highest-value
setup requires exactly those three.**
