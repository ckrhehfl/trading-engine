# Implementation Priority #6: Paper Broker (retrospective summary)

**PR**: #15 (merged 2026-07-21)

`engine.execution.PaperBroker` — the first component to play "the
exchange" role: acknowledges an approved `Order` and resolves it to a
`Fill` against caller-supplied prices, letting OMS + Risk Gateway be
exercised end-to-end without a real exchange. Deliberately designed in
the three lessons from Priority #4's `python/backtest/fill.py` bugs
(slippage never applied to LIMIT fills, favorable-price fills at the
actual price not the limit, open-price checked before the range) instead
of relearning them — none of those three recurred here.

Reacts to live-ish injected price ticks rather than replaying historical
bars, so `Instant.now()` timestamps are correct here (unlike a backtest,
there's no lookahead concept to violate). Does not fetch prices itself —
that's Priority #7's (`ExchangeAdapter`) job.

CodeRabbit still found four new, real issues on first pass: negative
fee/slippage not rejected by the constructor, non-positive price ticks
not validated before mutating order state, no defense-in-depth against a
caller bypassing `OrderStore` and submitting two distinct `Order`
instances sharing a client order id, and a genuine concurrency race
between `onPriceUpdate`/`cancel` calls racing on the same pending order
(fixed via `Map#computeIfPresent`'s per-key atomicity, proven with a
20-thread stress test). A follow-up round caught the stress test itself
lacking timeouts and guaranteed cleanup.
