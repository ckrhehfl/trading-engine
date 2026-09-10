# What we simulate versus what the venue does

**Status**: a catalogue, not a decision. Nothing here proposes a change.
Its purpose is to replace *finding these one at a time by accident* with
*knowing the list*.

**Date**: 2026-09-10. **Trigger**: `.planning/position-truth-discuss.md`
confirmed on a real account that an oversized opposite order does not
close a position on BingX hedge mode — the third instance of the same
shape. The first two were already known and disclosed. Three found by
three different accidents is a pattern about our method, not about any
one defect.

## How to read this

Every row is an assumption made by `python/backtest/fill.py`,
`python/backtest/engine.py`, or `engine.execution.PaperBroker` — the
three places that decide what happens to an order when no real venue is
involved. Each is marked with **how we know**, which matters more than
the assumption itself:

| mark | meaning |
|---|---|
| **CONFIRMED** | observed the real venue doing this |
| **CONFIRMED WRONG** | observed the real venue doing something else |
| **KNOWN GAP** | we know it is not modelled; already disclosed somewhere |
| **DOCS ONLY** | read from the venue's documentation, never tested |
| **UNTESTED** | assumed by the code; nobody has checked either way |

**UNTESTED is the row type this document exists to surface.** A
CONFIRMED WRONG row is a bug with a name. An UNTESTED row is the next
one.

---

## 1. Order semantics — what an order *means*

| # | We assume | Reality | How we know |
|---|---|---|---|
| 1.1 | An oversized opposite-side order closes the position and opens the residual (`PositionTracker`, `_transition_to`) | On BingX hedge mode it opens a **second** position; the original is untouched | **CONFIRMED WRONG** 2026-09-10 — `LONG 0.0001` then `SHORT 0.0001` left both open, `usedMargin` on both |
| 1.2 | `GUARDED_MARKET` carries a price guard | Maps to a plain `MARKET` on both `BingXAdapter` and `KisAdapter`; the guard is a name only | **KNOWN GAP** — CLAUDE.md gates Live Entry on it |
| 1.3 | Any quantity we send is the quantity that trades | BingX silently truncates to its step | **CONFIRMED WRONG** 2026-09-08, fixed in #153 and re-verified |
| 1.4 | One net position per symbol | Hedge mode holds `LONG` and `SHORT` separately, and margin is **not** netted between them | **CONFIRMED** — `tm-a` recorded the venue behaviour; 1.1 is what it costs us |

**1.1 and 1.4 are the same fact seen from two sides**, and the project
already knew 1.4. What was missing was noticing that the strategy's exit
path depends on the opposite being true.

---

## 2. Fill mechanics — *whether and at what price*

| # | We assume | Reality | How we know |
|---|---|---|---|
| 2.1 | An order fills entirely or not at all | BingX reports `PARTIALLY_FILLED` and a running `filledQuantity` | **KNOWN GAP** — `ExchangeOrderExecutor` handles partials on the wire; **neither `simulate_fill` nor `PaperBroker` models one**, so no backtest number reflects a partial fill |
| 2.2 | A `GUARDED_MARKET` fills at the next bar's open ± fixed slippage | A real market order fills at whatever the book gives | **UNTESTED as a distribution.** One real fill exists (2026-09-09, `avgPrice=79380.8`); we have never compared modelled versus realised slippage across orders |
| 2.3 | A `LIMIT` order fills 100% the instant a bar's high/low touches the limit | Queue position, depth and partial fills all decide this in reality | **KNOWN GAP** — scalping research restricted itself to `GUARDED_MARKET` because of exactly this |
| 2.4 | No same-bar execution: an intent fills on the *next* bar | A real market order fills in ~milliseconds, not on the next bar boundary | **Deliberate**, and conservative — it is a look-ahead guard, not a claim about the venue |
| 2.5 | `FEE_BPS = 5` flat | BingX's published VIP0 taker rate, and one real `commission` observed within 5bps of the model | **CONFIRMED once** — a single data point, not a distribution |
| 2.6 | Slippage is symmetric and constant | Measured median direction-flip cost is 0.014-0.015bps on Binance; BingX's thin best ask is ~1 tick | **CONFIRMED** by measurement (`scalp-s9`), and `SLIPPAGE_BPS=1` is deliberately ~65x it |

**2.1 is the most under-appreciated row here.** Every backtest result
this project has — including both holdout confirmations — assumes every
order filled completely. Nothing has ever tested what a partial fill does
to a strategy that computes its next order from a remembered position.

---

## 3. Account and lifecycle

| # | We assume | Reality | How we know |
|---|---|---|---|
| 3.1 | An order is never rejected | Venues reject for margin, precision, minimum size, rate limits | **KNOWN GAP** — `ExchangeOrderExecutor` maps a rejection to `order.reject()`; no backtest models one |
| 3.2 | Funding payments do not exist in the live loop | BTC-USDT perpetuals settle funding every 8h | **KNOWN GAP** — the backtest can model funding (`sr-m`, `sr-n`); **the Java paper loop does not account for it at all**, so paper equity and a real account would diverge over time even with identical fills |
| 3.3 | Leverage is whatever `RiskGateway` approved | Exchange-side leverage is a separate account setting | **CONFIRMED** — a fresh VST account was found at `20X`; `VstPreflight` now sets it, verified live |
| 3.4 | A resubmitted order is a new order | BingX rejects a duplicate `clientOrderID` server-side | **CONFIRMED** — `code 101400`, observed |
| 3.5 | Position mode is stable | Account-wide, cannot change while a position or open order exists | **DOCS ONLY** |
| 3.6 | Cancelling an unfilled order works | `DELETE .../trade/order` returned `CANCELLED` | **CONFIRMED** once |

**3.2 is a silent, compounding divergence.** It is not a bug in either
place — the backtest models it optionally and the loop simply does not —
but it means paper-trading equity is not comparable to a real account's
over any horizon that spans funding settlements.

---

## 4. What this catalogue says about method

Three rows are **CONFIRMED WRONG or KNOWN GAP on the same theme**: 1.1,
1.2, 1.3. Each was found a different way — 1.2 by reading during a design
pass, 1.3 by a live incident, 1.1 by measuring something else entirely.
None was found by looking for it.

The rows that should worry us most are **2.1, 2.2 and 3.2**, because they
are not defects — they are *unmodelled reality*, which produces no error
anywhere. A defect eventually trips something. An unmodelled cost simply
makes every number slightly wrong in the same direction, forever, and the
system reports success throughout.

**What this does not do**: it does not say any of these must be fixed, or
in what order. Several are correctly deferred — 2.3 is why scalping used
market orders, 2.4 is deliberately conservative. The point is that the
list now exists to be argued over, rather than discovered.

**Suggested use**: when a design change is proposed, state which rows it
closes. `position-truth-discuss.md`'s target-position proposal closes 1.1
and 1.3's whole class, does nothing for 1.2, and makes 2.1 and 3.1
*self-correcting* rather than fixed — which is a stronger claim than it
first appears, and worth weighing explicitly.

---

## 5. Deliberately out of scope here

- **Whether any of these invalidates a past result.** They may; that is a
  separate argument requiring per-result reasoning, not a table.
- **KIS.** Every row above is BingX. `KisAdapter` has its own quantity
  shape, its own reduction semantics, and its own unverified assumptions.
  It deserves the same table and does not have one.
- **Anything about strategy quality.** Nothing here is evidence for or
  against any strategy's edge.
