# GitHub issue #157: Position truth — who owns it, and why the strategy currently does

**Status**: open. No code has been written. This document exists to get a
human decision, because the change it proposes touches `OrderIntent` (a
tested cross-language wire schema), the strategy interface, and the
OMS — all R3, where CLAUDE.md's methodology forbids skipping `Discuss`.

**Date**: 2026-09-10. **Trigger**: the open item left by
`.planning/quantity-precision-discuss.md` §8 — "Python never learns what
actually filled, at all."

**The headline changed during the investigation, and the new one is
bigger.** This started as "quantify a rounding drift". Measuring it
surfaced something else: **on BingX in hedge mode, this strategy cannot
close a position at all.** Every exit and every flip opens an opposing
position instead. §2 is that finding; §1 is the small thing that led to
it.

---

## 1. The small thing: rounding drift, measured and largely self-cancelling

`daily_tsmom_ensemble` keeps its own belief about what it holds:

```python
delta = target_quantity - self._position_quantity   # order = target − belief
self._position_quantity = target_quantity           # belief := target
```

The emitted order is then truncated to the venue step (0.0001 BTC) at the
emit boundary, so reality moves less than the belief. The next order is
computed from the belief, so the error is not corrected.

**Measured on the real 1,944-bar BTC-USDT 1d history, real strategy, real
emit path** (logged to a disposable path; `runs/` untouched, verified):

| | |
|---|---|
| orders emitted | 147 |
| longest same-sign run | **2** |
| worst gap while a position was open | 0.000197 BTC ≈ **$15** |
| final gap | **0** |

Two synthetic sweeps bracket why. With alternating deltas the errors
behave like a random walk (√n, not n) and stay near zero. With
**monotonically rising conviction** — every delta the same sign — they
genuinely accumulate: 0.0258 BTC ≈ $2,040 at 400 trades, 65% of the
n×step bound. The real sequence sits at the benign end because the
longest same-sign run is 2.

**And the final gap is exactly zero for a structural reason, not luck.**
`_flatten_to` closes using `self._position_quantity` — the same believed
number the entry sized from — so entry and exit truncate the identical
value and cancel. That property is **conditional on
`rebalance_on_conviction=False`**, which is the default and which
`generate_daily_signal.py` does not override, so `_resize_to` never fires
in production today. Turning that flag on removes the cancellation.

**Conclusion on the small thing: not worth code on its own.** $15, and
self-cancelling in the current configuration. It is reported here because
it is the visible edge of §2.

---

## 2. The large thing: an exit does not exit

`_flatten_to` closes a long by emitting `side=SHORT`. `_transition_to`
flips by emitting a single oversized order — `closing_quantity +
target_quantity` — on the new side. In the backtest,
`metrics.position.PositionTracker` interprets an oversized opposite order
as *close-then-open-the-residual*, which is a coherent model.

`BingXAdapter` maps that to the wire as:

```java
params.put("side", bingxSide(order.side()));
params.put("positionSide", bingxPositionSide(order.side()));
// private static String bingxPositionSide(Side side) {
//     return side == Side.LONG ? "LONG" : "SHORT";
// }
```

**In hedge mode — which BingX confirmed is this account's default, and
which `VstPreflight` explicitly sets leverage for on both sides — a
`SHORT` order with `positionSide=SHORT` opens or increases a short. It
does not reduce a long.** So:

| the strategy means | the venue does |
|---|---|
| close my 0.0231 long | open a 0.0231 short, long untouched |
| flip long → short, size 0.0231 | open a 0.0462 short, long untouched |

The account ends up **gross-long and gross-short simultaneously**, paying
margin on both, with a liquidation price computed on a netted whole that
the strategy has no model of.

**This is already known, but recorded as an operational nuisance rather
than a correctness defect.** CLAUDE.md says, of the position left over
from an earlier run: *"this codebase's OMS path has no way to close one
(in hedge mode a `SHORT` opens a second position rather than closing the
`LONG`)"*. That sentence is about a human cleaning up. The same fact,
read against `_flatten_to`, means **the strategy's own exit path has
never worked against this venue** — and could not have been noticed,
because the one real signal this project has emitted never completed.

**Not yet observed live.** The 2026-09-08 order was an entry, and it
halted before any exit. So this is a reasoned consequence of the wire
mapping, not something seen on an account — which is exactly why it
belongs in a `Discuss` rather than a patch.

---

## 3. Root cause, shared by both

The strategy emits **actions computed from remembered state**. Nothing
downstream can correct them, because nothing downstream knows what was
intended — only what was ordered.

That single choice produces all of:

- rounding drift (§1), because the remembered value is never trued up;
- the hedge-mode exit failure (§2), because "close" is expressed as an
  opposite-side order rather than as a target of zero;
- **no restart recovery** — a restarted strategy must reconstruct what it
  already sent, rather than restate where it wants to be;
- **no correction for a partial fill or a rejection**, ever.

---

## 4. What the standard pattern is

Externally researched rather than asserted (sources at the foot). The
dominant design in systematic trading:

> **The strategy emits a target position. A downstream layer diffs that
> target against the position reconciled from the broker, and emits the
> order.**

Two consequences matter here specifically:

- **Lot/step constraints belong in the diff layer.** The canonical
  worked example is a model computing 5,123 shares against a held 5,200
  when the round lot is 100: the 77-share order is below the minimum, so
  **no order is placed**. That is §1's problem, solved structurally
  rather than by rounding at an emit boundary.
- **Crossing zero is not special.** Target 20 short against 27 long is
  simply "sell 47" — computed by the layer that knows both the current
  position and how the venue expresses a reduction. That is §2's problem.

The state split the sources describe: the strategy owns **model
parameters, signals, indicator history**; the OMS owns **positions and
open orders**, and reconciles them against the broker.

---

## 5. What this project already has

More than half of it, which is what makes this proportionate:

| piece | where | status |
|---|---|---|
| real position from the venue | `ExchangeAdapter#getPositions()` | exists, used by `VstPreflight` and the reconciler |
| position reconstructed from fills | `metrics.position.PositionTracker` | exists, used by the backtest — the strategy just keeps a parallel copy |
| ledger-vs-venue reconciliation | `AccountLedgerReconciler` | exists, KIS only, detects and trips the kill switch |
| the target itself | `_compute_target_quantity` | **already computed, then discarded** |

The last row is the important one. `_resize_to(current, sign, target_quantity)`
receives the target and converts it to a delta. Emitting the target
instead is a smaller change to the strategy than "redesign" suggests.

---

## 6. What would actually change — and what would not

**Not changing**: the Python/Java split, `ExchangeAdapter`, `RiskGateway`,
`OrderExecutor`, the OMS state machine, the paper/live mode split. Every
seam this project has built stays.

**Changing**, and each of these is a real decision:

1. **`OrderIntent`'s meaning.** Today `quantity` is an order size. A
   target-position design needs either a new field (`target_position`,
   signed) or a second intent kind. `OrderIntent` is a tested
   cross-language wire schema — `schemas/fixtures/order_intent_*.json`,
   `python/schemas/order_intent.py`, the Java record, `SchemaCompatTest`
   — so this is not a local edit.
2. **The strategy interface.** The rebalance decision needs the current
   position as an **input** rather than a memory:
   `drift = |target − position| / |position|`. In backtest that value
   comes from the fill simulator; in live, from the venue. This is more
   correct in both, and it **changes backtest results**, which is the
   sharp edge — see §7.
3. **A diff step in the OMS**, owning step size, minimum order size, and
   the venue's own way of expressing a reduction (`positionSide`, and
   whether a reduce-only flag exists on each venue).

---

## 7. Open questions a decision must answer

1. **Does this invalidate the holdout confirmations?** `sr-v` and `sr-ab`
   are `daily-tsmom-ensemble`'s only evidence, and CLAUDE.md's Paper
   Trading Policy Exception rests on them. If the strategy's interface
   changes, is it the same strategy? My reading: emitting a target rather
   than a delta is a **representation** change with identical intent, and
   the fills a backtest produces should be unchanged where no rounding or
   partial fill intervenes — but "should be" is not "is", and this must
   be **demonstrated by re-running and diffing**, not assumed. If the
   results differ at all, that is a new strategy version and the
   exception does not transfer.
2. **Additive field, or new intent type?** An additive optional field
   keeps every existing fixture valid; a second type is cleaner but
   doubles the surface `RiskGateway` and the OMS must handle.
3. **In-flight orders.** The research names this trap explicitly:
   diffing a target against a *settled* position while an order is
   working double-trades. Our loop ticks every 5 minutes against a daily
   signal, so the window is real. `delivered.marker` and
   `SubmissionMarkerStore` exist and are the natural materials, but the
   design must state the rule rather than inherit it by luck.
4. **Which venue truth?** `getPositions()` costs an API call per tick and
   has a rate limit (10/s per UID). Per-tick, per-signal, or cached with
   a staleness bound? Note CLAUDE.md's still-open gap: `PriceFeed`
   returns a bare value with no timestamp, and a staleness check was
   specified and never built. This decision should not repeat that.
5. **Hedge mode versus one-way mode.** A one-way account would make
   "close" natural, and BingX's position mode is account-wide and
   settable. Is switching to one-way the smaller fix for §2, and does it
   interact with anything else? It is not obviously free: hedge mode is
   what the account is in today, and `VstPreflight` sets leverage for
   both sides.
6. **Does KIS need the same?** `KisAdapter` has its own quantity shape
   and its own reduction semantics, unexamined here.

---

## 8. Recommendation

**Fix §2 before §1, and do not let §1's small number set the priority.**
A rounding error of $15 is not worth a schema change. An exit path that
opens an opposing position is worth one, and the same design fixes both.

**Sequence proposed, for the human to accept or change:**

1. **Establish §2 as fact against the real venue** before designing
   around it — a single deliberate VST test: open a small long, then send
   the exact intent `_flatten_to` would produce, and observe whether the
   long closes or a short appears beside it. This is cheap, it is the
   kind of evidence this project trusts, and it costs one supervised
   order. **Nothing in §2 has been observed live yet.**
2. Only then decide §6.1 and §6.2, on evidence.
3. Treat §7.1 as a gate: if the change moves any backtest number, it is a
   new strategy version, and the Paper Trading Policy Exception is
   re-argued rather than inherited.

**Explicitly not recommended: patching `bingxPositionSide` to guess a
reduction.** That would put venue-specific position semantics inside an
adapter method that has no idea what the current position is — the same
category of mistake as rounding inside `BingXAdapter`, which
`.planning/quantity-precision-discuss.md` §3 already ruled out on
evidence.

---

## Sources

Consulted while writing §4, rather than asserted from memory:

- [A Modular Architecture for Systematic Quantitative Trading Systems](https://hiya31.medium.com/a-modular-architecture-for-systematic-quantitative-trading-systems-2a8d46463570)
- [Quant Trading Systems: Architecture & Infrastructure](https://mbrenndoerfer.com/writing/quant-trading-system-architecture-infrastructure)
- [Trading System Architecture Guide — Low-Latency Components & Design Patterns](https://gegobyteapps.com/resources/trading-system-architecture)
- [Target trading system and method (US Patent 8,712,896)](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8712896)
