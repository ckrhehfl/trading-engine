# Trade Management Task A — a position model that can express what a trader actually does

**Status: Stages 1-2 BUILT and merged.** `metrics/book.py` (`Leg`,
`LegPurpose`, `Book`) and `research/strategies/leg_manager.py` exist,
with `LegAction`/`replay_fills` added afterwards so reported P&L comes
from real fills rather than signal-time prices. Stage 3 was exercised by
Task C's candidate (REJECTED — see `tm-c-confluence-hedge-result.md`);
**Stage 4, the live/adapter half, is still not built** and remains gated
on Stage 3 producing something worth carrying forward.

The original status line read *"Discuss. Nothing here is built"* and is
preserved here because the design below was written under it — nothing
in the reasoning was adjusted after implementation.

Written 2026-08-29 after six
months of strategy research produced no usable edge, and after the
operator identified — correctly — that the failure was partly in the
verification framework rather than in the strategies.

## The finding that forced this

Across many exchanges the operator described the same thing: enter long,
watch the move, add a tactical short when it weakens, close **only the
short** on the drop while keeping the core long, and manage the trade as
a sequence of decisions rather than one rule.

Every reply flattened it into something the codebase could already
express — a stop, a delayed entry, a size adjustment — and each of those
was measured and rejected (S15, S17, and the conviction-rebalancing test).
Those measurements are sound. **They were not measurements of what was
asked for**, because the codebase cannot represent it:

```text
DailyTsmomEnsembleStrategy   _position_sign      (one int)
                             _position_quantity  (one Decimal)

metrics.position.PositionTracker
                             position_qty        (one signed quantity)
                             avg_entry_price     (one weighted average)
```

A short opened against an existing long does not become a second
position. It **reduces `position_qty`**. There is no object to close
later, no separate entry price, no separate P&L. The sentence "close the
short and keep the long" has no representation.

So the honest state is not "trader-style management was tried and
failed". It is **"trader-style management has never been expressible"**.

## What the exchange actually gives us — the binding constraint

Researched rather than assumed (Binance and Bybit API docs, 2026-08-29):

- **Hedge mode is real and already available.** Binance
  `POST /fapi/v1/positionSide/dual`, orders carry
  `positionSide=LONG|SHORT`. BingX exposes the same
  (`/openApi/swap/v1/positionSide/dual`), and **this repo's
  `BingXAdapter` already sends `positionSide` and already implements
  `setPositionMode(PositionMode.HEDGE)`**. CLAUDE.md records that a fresh
  VST key came back `dualSidePosition: "true"` — hedge mode is the
  default there.
- **But the exchange tracks exactly one position per direction.** Quoting
  the research: *"the exchange calculates a single liquidation price that
  applies to all positions in the same direction on the same market, and
  both Binance and Bybit track only a single position in each direction —
  so any 'multiple long positions' abstraction is **client-side
  bookkeeping**, not exchange state."*

That sentence is the whole design constraint. It splits the problem into
two layers that must never be confused:

| Layer | What it holds | Who owns it |
|---|---|---|
| **Exchange truth** | ≤1 LONG + ≤1 SHORT per symbol, each with its own entry price, unrealised P&L, liquidation price, margin | the venue |
| **Our book** | the legs that *compose* those two, each with its own purpose, entry, size and exit rule | us |

Reconciliation is the contract between them: **the sum of our legs on a
side must equal the exchange's position on that side, at every tick.** A
book that can drift from exchange truth is a position-mismatch generator,
and "zero position mismatches" is a Paper Trading Pass Criteria line.

## Costs of hedging, which must be modelled and not assumed away

From the same research, and each one is a real constraint rather than a
footnote:

- **Funding is paid on both legs.** A hedged book bleeds funding twice.
  This project already models funding P&L (`sr-m`, `metrics/position.py`),
  so this is expressible — but only once legs are separable.
- **Margin is not netted.** Holding long and short simultaneously uses
  *more* margin than either alone. `RiskGateway`'s notional limit
  therefore has to be evaluated on **gross** exposure, not net. A book
  that is delta-flat is not risk-free and must not be sized as if it were.
- **`reduceOnly` is rejected** in hedge mode for a same-side open, because
  `side` + `positionSide` already determines open-vs-reduce.
- **Switching position mode requires a flat book.** Not a runtime toggle.
- Fees are paid on every leg transition, so a three-tranche design pays
  three times. Research consensus: two or three tranches cover most
  designs; beyond three, tranches rarely change the outcome and the fees
  compound.

## What this makes newly expressible — the point of doing it

Every item below is something the operator described and the current
model cannot state. None of them is *claimed to work*; the point is that
each becomes a testable hypothesis rather than an untestable one.

1. **Core + tactical.** A long held on the primary thesis, plus a short
   opened on weakness and closed independently.
2. **Scaling in / pyramiding.** Add on confirmation, with the combined
   size re-checked against the risk budget — research is explicit that
   *"risk only compounds if you add without re-sizing"*.
3. **Scaling out.** Partial take-profit at a first objective, a second
   tranche later, a runner left to trail. Expressed in R-multiples off
   the entry stop, which is the standard framing and the one S12's
   MAE/MFE machinery already measures in.
4. **Per-leg invalidation.** A tactical leg can be wrong without the core
   thesis being wrong. Today one stop governs everything.
5. **Asymmetric management.** Different rules for the winning and losing
   side of the book.

And critically: **this framework is asset-agnostic.** The same leg model
serves KOSPI200 futures through the existing `KisAdapter` exactly as it
serves BTC perps. The operator's observation that this would apply to
equities is correct, and the `ExchangeAdapter` seam already exists to
carry it.

## Proposed structure

Deliberately staged so that nothing touching live risk moves before the
research-side model has proven it can express anything worth trading.

### Stage 1 — book model and backtest accounting (no live risk)

- A `Leg`: side, quantity, entry price, entry time, a **purpose** tag
  (`core` / `tactical` / `hedge` / `runner`), and its own invalidation.
- A `Book`: legs per symbol, `net_by_side()` and `gross()`, plus the
  reconciliation assertion against a `PositionTracker` view.
- Per-leg realised P&L, so "the short made money while the long was still
  open" is a statement the metrics layer can produce.
- **Fully additive**: today's single-position strategies keep working
  untouched, as a one-leg book.

### Stage 2 — strategy API

Strategies emit **leg-scoped intents** (`open`/`add`/`reduce`/`close` on a
named leg) instead of one net target quantity. The existing strategies
become the degenerate case of a single `core` leg.

### Stage 3 — evaluation

Only after Stages 1-2: build the operator's own described strategy and
measure it, against the same Eligibility Bar and with the same
non-overlapping-sample discipline. **Not** another parameter sweep — one
specified hypothesis, one test.

### Stage 4 — live translation (R3-risk, its own `Discuss`)

- `(symbol, side, intent)` → `positionSide` mapping in the adapters.
- `RiskGateway` evaluating **gross** notional across both sides.
- Reconciliation of the book against the venue's two positions each tick.

Stage 4 must not begin until Stage 3 has produced something worth
running. Building live multi-leg execution for a strategy that does not
work would be the most expensive version of this project's existing
failure mode.

## What would make this fail, named in advance

So the next retrospective is not written from scratch:

- **A book that drifts from exchange truth.** Mitigated by making
  reconciliation an assertion, not a report.
- **Parameter explosion.** Legs multiply the ways to overfit: tranche
  count, sizes, triggers per leg. `N` is already 127. Stage 3 must be one
  specified hypothesis, not a search over leg configurations.
- **Fees and funding eating the structure.** Three legs pay three times,
  and a hedge pays funding on both sides. If the measured edge is smaller
  than the added cost, the structure loses even when the idea is right —
  this is exactly what killed the 1m work, and it must be checked before
  the strategy is built, not after.
- **Mistaking expressiveness for edge.** Being able to state a strategy is
  not evidence it works. Stage 3 exists to test it, and a negative result
  there is a real outcome that this document commits to reporting.

## Open questions for the `Discuss`

1. Is `purpose` a fixed enum or free-form? Fixed is safer for
   reconciliation and reporting; free-form is more expressive.
2. Do legs close FIFO, LIFO, or explicitly by id? The exchange nets, so
   this is purely our accounting convention — but it changes reported
   per-leg P&L and must be decided once, not per strategy.
3. Does `RiskGateway` cap gross exposure, net, or both? Research says
   margin is not netted, which argues for gross — but that makes a hedged
   book *more* expensive in risk budget than an unhedged one, which is
   correct and needs to be stated explicitly rather than discovered.
4. Is the first Stage 3 hypothesis the operator's core+tactical-hedge
   description, or something simpler that isolates one mechanism?
