# Implementation Priority #2: Java OMS State Machine (retrospective summary)

**PR**: #10 (merged 2026-07-17)

`engine.oms.Order` — 9-state lifecycle (`NEW` through terminal
`FILLED`/`CANCELLED`/`REJECTED`/`EXPIRED`). The only constructor is
`Order.fromApprovedDecision(OrderIntent, RiskDecision)`, which requires
`decision.decision()` to be `APPROVED`/`MODIFIED` — structurally
impossible to construct an `Order` that skipped risk assessment.
`OrderStore` provides idempotent creation keyed by client order id via
`ConcurrentHashMap#computeIfAbsent`, rejecting conflicting retries.

TDD followed (tests written first, confirmed failing to compile, then
implemented). CodeRabbit caught a real race: a partial fill arriving
while `CANCEL_PENDING` moved the order to `PARTIALLY_FILLED`, after which
`confirmCancel()` had no legal path back — fixed by staying in
`CANCEL_PENDING` on a partial fill (only a complete fill overrides a
pending cancel). Also caught a retry-conflict gap in `OrderStore`
(`computeIfAbsent` never re-validates on a cache hit) and a concurrency
test that only checked for *a* successful result rather than *every*
thread succeeding.
