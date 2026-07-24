# Implementation Priority #7, Task B: `ExchangeAdapter` / `BingXAdapter`

**PR**: #21

## What was built

`java/exchange` (package `engine.exchange`): the `ExchangeAdapter` interface
and its first implementation, `BingXAdapter`, for BingX USDT-M perpetual
swap futures. This is the first component in the repo capable of placing a
real order against an exchange — see CLAUDE.md's Implementation Priority
#7 note: from here on, `ExchangeAdapter` may only ever be invoked from
OMS-mediated flows, never called directly with a hand-built `Order`.

New types: `ExchangeAdapter` (interface), `BingXAdapter`, `BingXSigner`
(standalone HMAC-SHA256 request signer), `PositionMode`, `PositionSnapshot`,
`BalanceSnapshot`, `OrderStatus`, `ExchangeException`.

`submitOrder`/`cancelOrder` mutate the passed `Order` directly, mirroring
`engine.execution.PaperBroker`'s idiom, so OMS-facing code doesn't need to
know whether it's talking to the paper broker or a live adapter. `queryOrder`
is the deliberate exception: unlike a paper fill (resolvable synchronously
from an injected price), a real exchange fill needs polling or a push —
out of scope here — so `queryOrder` is a pure read that never mutates the
`Order` it's given. `BingXAdapterTest.queryOrderReturnsParsedStatusWithoutMutatingOrder`
asserts this explicitly (captures `order.state()` before the call, asserts
it's unchanged after).

No live/paper flag exists anywhere in this module. `BingXAdapter`'s base
URL is a plain constructor string argument; deciding which host to point
at and reading it from the environment is entirely the caller's job — not
built yet, deferred to whichever future priority wires runtime
configuration together (see "Deferred" below).

## TDD

Tests were written first and confirmed to fail (a compile error, the normal
"red" state for a statically-typed language, since `BingXSigner`/
`BingXAdapter` didn't exist yet) before any production code was added.

- `BingXSignerTest`: signatures checked against vectors hand-computed
  independently in Python (`hmac`/`hashlib`, not `BingXSigner` itself) —
  a match demonstrates actual correctness against BingX's documented
  signing scheme, not just that the method runs. Also covers: identical
  signature regardless of input `Map` insertion order (proves sorting is
  actually enforced, not incidentally correct for one already-sorted
  case), uppercase-hex output format, and null-argument rejection.
- `BingXAdapterTest`: exercises `BingXAdapter` against a real local HTTP
  server (`com.sun.net.httpserver.HttpServer`, part of the JDK since Java
  6 — no new test dependency) serving canned BingX-shaped JSON, asserting
  both the resulting `Order` state transitions and the actual HTTP
  request the fake server received (method, path, query params, headers)
  — not just "the call didn't throw."
- One test (`orderHasNoPublicConstructorOtherThanFromApprovedDecisionFactory`)
  asserts via reflection that `Order.class.getConstructors()` is empty,
  proving there is no way — in this test suite or any other caller — to
  obtain an `Order` other than through `Order.fromApprovedDecision`. Every
  `Order` used elsewhere in this test suite is built the same way
  `PaperBrokerTest` builds one.

21 new tests (7 `BingXSignerTest`, 14 `BingXAdapterTest`). Full suite
(`./gradlew test`) is 126 tests across all five modules, all green.

## What's verified vs. documented-only

Everything in this module is **documented-only** — none of it has been
called against a real BingX endpoint (VST or production) with a real API
key. It implements against CLAUDE.md's "Exchange API Facts — BingX"
*Documented, not yet empirically verified* section, which itself was read
from BingX's docs site, not tested against a live key. Treat this whole
module with that same reduced confidence until someone runs it against a
real VST key.

## Judgment calls made resolving ambiguity in the documented API shape

The research notes this was built against captured endpoint paths, the
auth scheme, and the general response-envelope convention
(`{"code":0,"msg":"","data":{...}}`) — but not exact field names inside
`data`, nor confirmation of body-vs-query-string for writes. Several
things had to be decided rather than looked up:

- **Exchange-level order rejection vs. ambiguous failure.** During
  `submitOrder`, an HTTP 200 with a non-zero `code` is treated as a
  *definitive* answer from the exchange (order seen and declined —
  e.g. insufficient margin) and maps to `order.reject(reason)`. A
  network/HTTP-level failure (5xx, timeout, I/O error) is treated as an
  *ambiguous* outcome — we don't know if BingX received the order — and
  throws `ExchangeException` without any further state transition beyond
  whatever `order.submit()` already did before the network call ran.
  `OrderState.SUBMITTED` exists precisely to represent this "in flight,
  outcome unknown" case. `cancelOrder` follows the same split, except a
  non-zero `code` there also throws `ExchangeException` rather than
  attempting any state transition — `Order` has no "cancel failed, revert"
  transition, so `CANCEL_PENDING` is genuinely the only place left to
  leave it.
- **Response field names.** Assumed `data.order.orderId` for the
  submitted/queried order id (falling back to `data.orderId` if not
  nested under `"order"`, since nesting wasn't confirmed either way);
  `executedQty` / `avgPrice` for `queryOrder`'s filled-quantity/price
  fields, following the Binance-derivative naming convention BingX's
  Swap V2 API broadly follows. None of this is confirmed against a real
  response — see `BingXAdapter`'s Javadoc for where each assumption lives.
- **Request transport.** All params (including for `POST`/`DELETE`) are
  sent as an unencoded URL query string with an empty body, not a JSON
  body — inferred from BingX's general API convention, not explicitly
  stated in the research notes. `BingXSigner.queryString` is reused by
  both the signing step and the actual request-building step so the two
  can't silently disagree about encoding.
- **No URL-encoding.** Per the research notes' description of the signing
  scheme. Fine for every param value used today (symbols, decimals,
  enum-like strings); would need real encoding added if a future param
  ever contains a character requiring it.
- **`positionSide` mapping.** `Side.LONG`/`Side.SHORT` map to BingX's
  hedge-mode `positionSide` values (`LONG`/`SHORT`) unconditionally.
  `Order` carries no position-mode context to choose `BOTH` (one-way
  mode) instead — this only really targets hedge-mode accounts as
  implemented today. Flagged as a known limitation, not silently assumed
  correct.
- **`setPositionMode`'s request field name** (`dualSidePosition`) isn't in
  the research notes (only the endpoint path was captured) — filled in
  from general familiarity with BingX's API surface. Needs verification.
- **`GUARDED_MARKET` → BingX `type=MARKET`.** BingX has no "guarded
  market" order type; the slippage guard is this system's own concept,
  enforced elsewhere (Risk Gateway / execution logic), not something the
  exchange API itself models.

## Deliberately out of scope / deferred

- WebSocket market data and the private user-data stream (order/position
  push) — CLAUDE.md's research notes describe the `listenKey` flow, not
  built here.
- The actual paper-vs-live runtime switch: reading `BINGX_BASE_URL` from
  the environment and constructing `BingXAdapter` with it. No caller of
  `BingXAdapter` exists yet.
- Position reconciliation loop, kill switch, 24/7 process supervision
  (restart recovery, health checks) — all Priority #8.
- Clock sync / server-time check (`GET /openApi/swap/v2/server/time`).
  BingX requires request timestamps within a 5s window of server time;
  keeping a VPS clock in sync is an operational concern for whichever
  priority handles deployment, not this adapter's job to self-correct.
- Retry/backoff for transient network failures — `ExchangeException`
  just propagates; a retry policy is a runtime-supervision concern
  (Priority #8), not this module's.
- Order-status casing normalization (`OrderStatus.status` is returned
  as BingX's raw string, unnormalized) — CLAUDE.md's research notes
  flag a REST/WebSocket casing inconsistency (`CANCELLED` vs
  `CANCELED`) as something to test, not trust; normalizing here would
  be guessing at a convention nobody has verified yet.
