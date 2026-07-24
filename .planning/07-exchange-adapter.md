# Implementation Priority #7, Task B: `ExchangeAdapter` / `BingXAdapter`

**PR**: #21

## What was built

`java/exchange` (package `engine.exchange`): the `ExchangeAdapter` interface
and its first implementation, `BingXAdapter`, for BingX USDT-M perpetual
swap futures. This is the first component in the repo capable of placing a
real order against an exchange — see CLAUDE.md's Implementation Priority
7 note: from here on, `ExchangeAdapter` may only ever be invoked from
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

25 new tests in `java/exchange` (7 `BingXSignerTest`, 18 `BingXAdapterTest`
— including two added during CodeRabbit review: a response missing the
`code` field entirely, and a non-numeric value in a decimal field). Full
suite (`./gradlew clean test`) is 130 tests across all five modules, all
green.

## What's verified vs. documented-only

At initial implementation, everything in this module was **documented-only**
— built against CLAUDE.md's "Exchange API Facts — BingX" *Documented, not
yet empirically verified* section, itself read from BingX's docs site, not
tested against a live key. Shortly after this PR opened, a real VST key
became available and several of the assumptions below were checked against
it — see "Post-open: live VST key findings" for what changed as a result.
Everything not called out there is still documented-only.

## Post-open: live VST key findings (2026-07-24)

Once @ckrhehfl provided a real VST key, real (read-only, plus one
validate-without-execute call) requests were made against
`GET /openApi/swap/v3/user/balance`, `GET /openApi/swap/v2/user/positions`,
and `POST /openApi/swap/v2/trade/order/test`. This surfaced one real bug
and confirmed two prior assumptions:

- **Bug found and fixed: `getBalance()`'s response shape was wrong.**
  `data` is an array of per-asset balance objects (same envelope pattern
  `getPositions()` already correctly handled), not the nested
  `data.balance.{...}` object originally assumed. The original code
  checked `data.has("balance")`, which is always `false` on a JSON array,
  silently fell through to treating the array itself as the balance
  object, and would have returned null/wrong values rather than throwing
  — the kind of silent-wrong-answer failure mode that's worse than a
  crash. Fixed by `selectBalanceNode` (takes `data[0]`, throws
  `ExchangeException` if `data` isn't a non-empty array). The account
  used has exactly one margin asset in scope, so index 0 is correct here;
  see that method's Javadoc for why a hardcoded asset-name match was
  deliberately not used instead. `BingXAdapterTest`'s balance fixtures
  were wrong in the same way the implementation was — updated to the real
  array shape (`getBalanceParsesResponseAndHitsV3Endpoint`), plus a
  multi-entry case (`getBalanceTakesFirstEntryWhenMultipleAssetsPresent`)
  and an empty-array case
  (`getBalanceThrowsExchangeExceptionOnEmptyDataArray`).
- **Confirmed correct: `submitOrder`'s order-id nesting.** A real
  `POST /openApi/swap/v2/trade/order/test` (BingX's validate-without-
  executing endpoint) call returned `data.order.orderId` — exactly the
  nesting `unwrapOrderNode`/`extractOrderId` already assumed. `orderId`
  arrives as a JSON number, not a string; `JsonNode.asText()` renders it
  losslessly for values in this range, so no change was needed there
  either.
- **Still unverified: `queryOrder`'s field names.** The `/order/test` call
  above doesn't execute, so `status`/`avgPrice`/`executedQty` all came
  back empty — it can't confirm `queryOrder`'s assumed field names or
  whether *its* response is also nested under `"order"`. That needs a
  real submitted-then-queried order, which wasn't done here (deliberately
  — a step beyond pure validation, not taken without explicit go-ahead).
  Still flagged as an open assumption below.
- **Related, not this module's code:** default position mode on a fresh
  key was confirmed as hedge mode (`dualSidePosition: "true"`), not
  undocumented as the original research assumed. Recorded in CLAUDE.md
  directly (PR #22), not repeated here since it doesn't change anything
  in this module's code — `setPositionMode` already sets the mode
  explicitly rather than assuming a default.

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
  nested under `"order"`, since nesting wasn't confirmed either way at
  the time) — **now confirmed correct** for `submitOrder` against a real
  `/order/test` call, see "Post-open: live VST key findings" above.
  `executedQty` / `avgPrice` for `queryOrder`'s filled-quantity/price
  fields, and whether `queryOrder`'s response nests under `"order"` the
  same way, **remain unconfirmed** — following the Binance-derivative
  naming convention BingX's Swap V2 API broadly follows, but not checked
  against a real filled/queried order yet. `getBalance`'s field names
  were also wrong in a different way (array vs. nested-object envelope,
  not field naming) — see "Post-open" above for that bug and its fix.
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

## CodeRabbit review findings

Eight findings across two review passes on the PR. Three doc fixes
applied (test-count accuracy in this file, a `#7` sequence in prose that
could render/link unexpectedly in Markdown — reworded rather than
special-cased, and this section itself). Two real code fixes applied to
`BingXAdapter.java`:

- `parseBigDecimal` now catches `NumberFormatException` and wraps it in
  `ExchangeException` with the field name and raw value, instead of
  letting a bare JDK exception escape this module's own error type.
- Every `code`-field check now goes through a new `requireCode(root,
  context)` helper that throws `ExchangeException` if `code` is absent
  entirely, instead of `JsonNode#path("code").asInt()`'s default
  behavior of silently returning `0` for a missing field — which would
  have misread a malformed/unexpected response shape as success. Applied
  uniformly across all seven methods that check `code`. Two new tests
  cover this (missing `code` field, non-numeric decimal field).

One test-isolation fix on the second review pass: the "missing `code`
field" test above originally paired that with an empty `data` array,
which meant the test couldn't tell `requireCode`'s check apart from
`selectBalanceNode`'s separate empty-array check — either one alone
would have made it pass. Fixed by giving that test a valid, non-empty
balance array so `code` is the only thing missing, plus an assertion
that the exception message actually names the missing field.

Three findings declined, with reasoning left as PR review replies
rather than silently skipped:

- A suggestion to add human-approval gating to *future* wiring that
  would pick a production base URL — there is no such wiring in this PR
  (see "no live/paper flag" design note above), so there's nothing here
  to gate; the concern is valid but belongs to whichever future priority
  actually builds that wiring, not this one.
- A suggestion to add structured retry/idempotency handling to
  `ExchangeException` — this directly re-opens a scope boundary already
  decided and documented (see "Deliberately out of scope / deferred"
  below: retry/backoff is explicitly Priority #8's job, not this
  module's). The ambiguous-vs-confirmed-failure distinction it's asking
  for already exists structurally (`SUBMITTED` = ambiguous, `REJECTED` =
  confirmed) — formalizing it as a queryable exception field is a
  reasonable idea for whenever #8 actually builds a retry policy, not a
  gap in this PR.
- A suggestion that `BingXAdapterTest` bypasses the Risk Gateway boundary
  because its `guardedMarketOrder()` helper hand-constructs a
  `RiskDecision` rather than obtaining one from a real
  `RiskGateway.evaluate()` call. Declined: the test already only ever
  obtains an `Order` via `Order.fromApprovedDecision` (the one path that
  exists, proven by
  `orderHasNoPublicConstructorOtherThanFromApprovedDecisionFactory`) —
  it isn't hand-building an `Order`, which is what the "OMS-mediated
  flows only" rule actually governs. An integrated
  `OrderIntent → RiskGateway.evaluate() → Order` pipeline doesn't exist
  anywhere in this codebase yet to test against — CLAUDE.md's own
  Implementation Priority #8 entry says so explicitly ("nothing wires
  these together yet, so this can't be tested until this priority builds
  that wiring"), and building that wiring is named as #8's job, not
  #7's. This is also the exact same idiom `PaperBrokerTest` already
  uses and had already been merged and reviewed under (Priority #6) —
  applying a stricter bar here than the codebase's own established
  precedent would be inconsistent, not more correct.

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
