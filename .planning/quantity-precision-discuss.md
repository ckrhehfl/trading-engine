# GitHub issue #151: Quantity precision — `Discuss` before any code

**Status: RESOLVED 2026-09-09.** The operator chose **Option C** (§5);
it was built in PR #153, merged, deployed, and **verified against the
real BingX VST venue** — the account of that verification is §8 at the
foot of this document, and it is the part worth reading first.

The rest of this document is preserved as written, in the present tense
it was written in, because it is the record of a decision made *before*
the answer was known. Rewriting it to sound prescient would destroy the
only thing it is good for.

> **Status (as written, 2026-09-08)**: open. No code has been written.
> This document exists to get a human decision on **where** a venue's
> quantity step size is enforced, because every candidate location is in
> R3-risk territory (OMS / Risk Gateway / Execution) and CLAUDE.md's
> Development Methodology forbids skipping `Discuss` there.

**Date**: 2026-09-08. **Trigger**: a real incident, four hours earlier,
on the `bingx-vst` loop.

---

## 1. What happened, from the record

`daily-tsmom-ensemble` produced this project's first real live signal at
00:00 UTC on 2026-09-08. The signal file, verbatim from the VPS:

```json
{"intent_id":"efb4da8c-34ad-5671-94ce-78deb14a05df","symbol":"BTC-USDT",
 "side":"SHORT","order_type":"GUARDED_MARKET",
 "quantity":"0.02318401746487214694970992456", ...}
```

**29 significant digits.** BingX's own published contract spec for
`BTC-USDT`, re-verified live against the public API while writing this
document:

| field | value |
|---|---|
| `size` (step) | `0.0001` |
| `quantityPrecision` | `4` |
| `tradeMinQuantity` | `0.0001` |
| `tradeMinUSDT` | `2` |

The venue accepted the order, then filled `0.0231` — which is **exactly**
the signal quantity truncated to 4 decimal places. That match is what
makes the mechanism certain rather than inferred.

```
signal quantity   0.02318401746487214694970992456
truncated to 4dp  0.0231
BingX filled      0.0231          ← identical
unfilled residue  0.0000840174... BTC = 6.64 USDT = 0.362% of the order
```

### Why it did not simply end there

`ExchangeOrderExecutor` compares the venue's `filledQuantity` against
this project's own `approvedQuantity`. `0.0231 < 0.02318401…`, so our
state stayed `PARTIALLY_FILLED` while BingX reported `FILLED`.
`applyStatusMapping` refuses to reconcile that:

> order … exchange status reports FILLED but our own state is
> PARTIALLY_FILLED — a fill delta didn't complete it (a real quantity
> mismatch between the venue's own order and this project's
> approvedQuantity); the status string alone is never trusted to discard
> pending tracking

**That refusal is correct and should not be softened.** It is the check
that surfaced the defect. The order was then dropped from the executor's
pending map while remaining open in `OrderStore`, so on the next tick the
`Reconciler` found `ORPHANED_IN_BROKER` and tripped the kill switch — and
has re-tripped it every tick since. As of writing, the loop is still
halted, which is the system behaving as designed.

**Verified consequence for the deployment**: `KillSwitch` and
`OrderStore` are both in-memory (`new KillSwitch()`, never persisted).
Restarting the VST loop clears the orphaned order, so it would come back
**armed**, with this defect unfixed. That is why the loop has deliberately
not been restarted, and why this decision blocks the next deploy.

---

## 2. Where the number comes from

`research/strategies/daily_tsmom_ensemble.py`:

```python
base_quantity   = reference_equity / entry_price
target_quantity = base_quantity * abs(ensemble_value) * vol_scalar
```

A `Decimal` division under Python's default 28-digit context. Nothing
downstream reduces it, because nothing downstream was ever asked to.

**This is not a strategy bug.** The strategy is venue-agnostic by design
and should stay that way — it has no business knowing BingX's step size.
The question is purely *where the venue's constraint gets applied*.

---

## 3. One candidate is already ruled out, on evidence

**Rounding inside `BingXAdapter.submitOrder` does not work, and would
make things worse.**

The `Order` — carrying `approvedQuantity` — is constructed by
`OrderPipeline` *before* the adapter is ever called. If the adapter
rounds only the value it puts on the wire, `approvedQuantity` still holds
29 digits, so the venue's filled quantity can now **never** equal it. The
`PARTIALLY_FILLED`-forever state stops being an accident and becomes a
guarantee.

So the fix must land **before `Order` construction**. That is what makes
this an OMS/Risk-layer decision rather than an adapter detail, and it is
the single most important finding here.

---

## 4. The seam already exists

This is the part that makes the decision smaller than it first appears.
`engine.risk.NotionalCalculator` — added in PR #105 for the KOSPI200
contract multiplier, and explicitly blessed in CLAUDE.md — already
declares:

```java
/**
 * Non-empty iff {@code intent.quantity()} is invalid for this
 * instrument shape (e.g. fractional where only a whole contract
 * count is valid) -- checked before any notional math, so an invalid
 * quantity fails the order closed rather than silently producing a
 * meaningless notional.
 */
Optional<String> quantityRejectionReason(OrderIntent intent);
```

A 29-digit quantity on an instrument with a `0.0001` step **is** invalid
for the instrument shape. The contract already covers this case in
words. What is missing is an implementation that uses it:

```java
public final class SimpleNotionalCalculator implements NotionalCalculator {
    private static final int QUANTITY_SCALE = 8;

    @Override
    public Optional<String> quantityRejectionReason(OrderIntent intent) {
        return Optional.empty();          // ← accepts any precision
    }

    @Override
    public BigDecimal maxQuantityFor(BigDecimal maxNotional, BigDecimal price) {
        return maxNotional.divide(price, QUANTITY_SCALE, RoundingMode.DOWN);
    }
}
```

**A second defect, found while reading this and not previously known**:
`maxQuantityFor` is the path `RiskGateway` takes when it *clamps* an
over-limit order rather than rejecting it. It produces **8** decimal
places. BingX accepts **4**. So a clamped order reproduces this same bug,
milder but identical in kind — and a clamp is exactly the path a
too-large order takes, i.e. the one where getting it wrong matters most.

Whatever is decided below must fix both, or explicitly say why not.

---

## 5. The decision

Something must produce a step-valid quantity. Rejection alone cannot be
the whole answer: every signal this strategy emits has 28 digits, so a
pure reject rule halts trading permanently.

### Option A — Python rounds before writing the signal file

`live/generate_daily_signal.py` quantizes to the venue step, `ROUND_DOWN`.

- **For**: smallest change; no Java, no R3 risk; the live signal
  generator is already venue-specific in other ways.
- **Against**: puts a BingX fact in the Python plane, which the
  architecture keeps venue-agnostic. Protects only this one signal
  source. Introduces a backtest/live divergence — the backtest keeps
  trading unrounded quantities.

### Option B — a venue-aware `NotionalCalculator` that rounds

A `SteppedNotionalCalculator(step)` wired in `PaperTradingApp`, with
`RiskGateway` gaining a call site that rewrites quantity to the step.

- **For**: the venue fact sits in the wiring layer, alongside
  `BINGX_VST_BASE_URL` and the ₩250,000 KOSPI multiplier — exactly where
  CLAUDE.md says venue facts belong. Covers every signal source.
- **Against**: a **new call site inside `RiskGateway`** is a materially
  bigger change than adding an interface implementation. CLAUDE.md
  blessed the *seam*; silently rewriting an approved quantity for a
  reason unrelated to risk is a different thing and deserves its own
  scrutiny.

### Option C — Python rounds, Java refuses anything unrounded *(recommended)*

Both: `generate_daily_signal.py` emits a step-valid quantity, **and**
`quantityRejectionReason` rejects any intent whose quantity is not a
multiple of the step.

- **For**: the correct value is produced at the source, and the risk
  layer independently refuses to pass a malformed one — defence in depth,
  with the Java half **fail-closed**, matching this project's discipline
  everywhere else (`KrxMarketCalendar`, `Book.reconcile`, `KisAdapter`'s
  parsing). No new `RiskGateway` call site: the rejection hook already
  exists and is already called. It also makes the guard *falsifiable* —
  break the Python rounding and Java rejects, loudly.
- **Against**: two places must agree on the step. If they disagree the
  loop stops trading rather than trading wrongly, which is the safe
  direction, but it is still a coupling worth naming.

**Recommendation: C.** It is the only option where the failure mode of
getting it wrong is "no order" rather than "wrong order", and it needs no
new decision-making inside `RiskGateway`.

### Option C is already demonstrated, not merely proposed

Verified while writing this, and it is the strongest argument for C.
`RiskGateway.evaluate()` calls the hook in its main path, before any
notional math:

```java
// RiskGateway.java:81
Optional<String> quantityRejection = notionalCalculator.quantityRejectionReason(intent);
if (quantityRejection.isPresent()) {
    return reject(intent, quantityRejection.get());
}
```

And `FixedMultiplierNotionalCalculator` — the KIS/KOSPI200 implementation
from PR #105 — **already uses it to reject a fractional quantity**, with
its own tests (`quantityRejectionReasonRejectsFractionalQuantity`,
`quantityRejectionReasonAcceptsWholeQuantityIncludingTrailingZeroForm`).

So Option C is not a new mechanism. It is the same mechanism, already
built, already tested, already running for one venue, applied to a
second. The BTC-USDT case differs only in that its valid shape is "a
multiple of 0.0001" rather than "a whole number". `SimpleNotionalCalculator`
is the one implementation that opted out of the check, and that opt-out
is the defect.

---

## 6. Open questions a decision must also answer

1. **Round down, or reject a non-conforming quantity outright?**
   Down, in my view: rounding *up* can exceed the notional limit
   `RiskGateway` just approved. But rounding down must then re-check
   `tradeMinQuantity` — at today's price the minimum order is 0.0001 BTC
   ≈ 7.90 USDT, so `tradeMinQuantity` binds before `tradeMinUSDT` does.
   **An order that rounds down to zero must be rejected, never sent.**

2. **Where does the step size come from?** Hardcoded per symbol, or read
   from BingX's `/quote/contracts` at startup? Reading it is more correct
   and adds a startup dependency plus a staleness question. Hardcoding
   repeats the KIS multiplier pattern, which CLAUDE.md accepted.

3. **Does Python's internal position state get the rounded value?**
   `daily_tsmom_ensemble` keeps `self._position_quantity` and computes
   the next rebalance delta from it. If the emitted quantity is rounded
   but the internal one is not, the two drift apart across rebalances.
   Making them agree changes strategy behaviour and therefore backtest
   reproducibility. **This is a pre-existing gap that rounding makes
   visible rather than creates** — Python never learns what actually
   filled, at all — and it may deserve its own task rather than being
   solved here.

4. **KIS.** `KisAdapter` has its own quantity shape (whole contracts).
   Does this change address it now, or is `FixedMultiplierNotionalCalculator`
   left as-is? The KIS loop's kill switch is tripped unconditionally, so
   nothing is live there.

5. **How does the fix get verified?** A unit test proves the arithmetic.
   It does not prove BingX accepts the result. Per CLAUDE.md's
   change-check rules, the real verification is a VST order that reaches
   `FILLED` with our own state agreeing — which means a deliberate,
   supervised test submission after the fix and after the loop restart.

---

## 7. What happens next, in order

1. Human decision on §5 and the questions in §6.
2. TDD, per CLAUDE.md's mandatory rule for Risk/Execution code: the
   failing test first, reproducing the real 29-digit quantity.
3. Deploy via `scripts/vps-deploy.sh` — a Java change is not deployed
   until the loops restart and the changer verifies it.
4. Only then reset the VST kill switch, deliberately, with the account
   inspected first.

**Nothing in step 3 or 4 happens without step 1.** The loop is safely
halted; there is no time pressure, and acting under the appearance of
urgency is how the original defect reached a real venue.


---

## 8. Resolution, and what the real venue actually did

**Decision**: Option C, chosen by the operator on 2026-09-09. Built in
PR #153, with three follow-on PRs (#154, #155) for defects the deployment
itself exposed.

### What was built

- **Java** — `engine.risk.SteppedNotionalCalculator`, reached through the
  `NotionalCalculator.quantityRejectionReason` hook `RiskGateway` already
  called. `PaperTradingApp.resolveNotionalCalculatorForSymbol` fails
  closed for any symbol whose step this project has not verified. The
  second defect found in §4 is fixed too: `maxQuantityFor` now clamps to
  the step rather than to 8 decimals.
- **Python** — `generate_daily_signal.py` quantizes to the venue step at
  the emit boundary, `ROUND_DOWN`, using exact integer arithmetic. The
  obvious `quantity / step` form was **wrong in the unsafe direction** —
  the division rounds under the 28-digit context *before* `ROUND_DOWN`
  sees it, so a value just below a step boundary rounds **up** past it.
  Caught by CodeRabbit, not by the author.

### Verified against the real venue, 2026-09-09

Both halves exercised through the full
`OrderIntent → OrderPipeline → RiskGateway → Order → ExchangeOrderExecutor
→ BingXAdapter` path on the `bingx-vst` loop:

| | quantity | outcome |
|---|---|---|
| A | `0.02318401746487214694970992456` — the incident's own value | **rejected by `RiskGateway`**, no venue call |
| B | `0.0001` | **`FILLED`**, no orphan, no kill-switch trip |

BingX's own record for B: `side=LONG amt=0.0001 avgPrice=79380.8
leverage=1` — identical to the approved quantity. The mismatch that
defined the incident is gone.

### The open questions from §6, answered

1. **Round down, or reject?** Both, in different places — Python rounds
   down, Java rejects anything unrounded. A quantity below one step
   writes no signal rather than a zero-quantity order.
2. **Where does the step come from?** Hardcoded, following the KIS
   multiplier precedent, in `PaperTradingApp` — the same layer
   `BINGX_VST_BASE_URL` occupies. A cross-language test pins the Python
   table against the Java constant, since Option C's one real cost is
   two places having to agree.
3. **Python's internal position state** — **still open, and deliberately
   so.** `daily_tsmom_ensemble` tracks `_position_quantity` unrounded, so
   emitted-versus-believed drifts by up to one step per rebalance. That
   is bounded (0.0001 BTC, ~$8 at present prices) and is a *smaller*
   instance of a pre-existing gap this fix did not create and does not
   close: **Python never learns what actually filled, at all.** It
   deserves its own task, not a footnote here.
4. **KIS** — unchanged. `FixedMultiplierNotionalCalculator` already
   rejects a fractional contract count, which is the same mechanism.
5. **How it gets verified** — done, above.

### Two defects the deployment itself exposed

Neither was reachable from a test; both were found by running the tooling
against the real box. Recorded here because they are the reason a
"finished" fix took three more PRs.

- **`vps-deploy.sh` did not reproduce cron's environment**, so the
  restart came back on the Gradle launcher and no loop started. Fixing it
  took four rounds, because each fix reintroduced the same failure
  through its own matching: assignments after the job, an indented
  comment, a value containing the job's name, and a crontab with no job
  entry at all.
- **`VstPreflight` had no retry**, and its first call lands at the worst
  possible moment. Two JVMs cold-starting together on a 955 MB instance
  produced `code=109400 msg=timestamp is invalid` — **not clock drift**
  (392 ms, NTP synchronised); the identical call succeeded once load
  eased. It now retries only `ExchangeException`, never the
  not-a-demo-account refusal.
