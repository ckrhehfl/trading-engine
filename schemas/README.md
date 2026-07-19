# Shared Schemas

Source of truth for the message contracts that cross the Python Research
Plane / Java Trading Plane boundary. Python (pydantic) and Java (Jackson)
each implement these independently — see `python/schemas/` and
`java/schemas/` — checked for drift by compatibility tests (not yet built,
see Implementation Priority #5 in `CLAUDE.md`).

## Conventions

- **Decimals as strings.** Any monetary/quantity field (`quantity`,
  `limit_price`, `approved_quantity`, `approved_leverage`) is serialized as
  a JSON string, never a native JSON number. Native JSON numbers round-trip
  lossily between Python `float`/`Decimal` and Java `double`/`BigDecimal`;
  strings don't. Python side uses `Decimal`, Java side uses `BigDecimal`.
  All four fields must be strictly positive and finite — Java's
  `BigDecimal` has no NaN/Infinity concept at all, so both sides reject
  zero, negative, `NaN`, and `Infinity` values at construction time.
- **Timestamps.** ISO 8601, UTC, e.g. `2026-07-19T13:00:00Z`, and must be
  timezone-aware — a naive timestamp is ambiguous and rejected on the
  Python side (`AwareDatetime`); Java's `Instant` has no naive form to
  begin with.
- **`schema_version`.** Every message carries it, starting at `"1.0"`.
  Bump the minor version for additive/backward-compatible changes, the
  major version for breaking changes, and note the change in this file.
- **Exchange/asset-agnostic where practical.** `symbol` is a free-form
  string, not a BTC-USDT-only enum — multi-symbol/multi-exchange is a
  long-term design target (see `CLAUDE.md`). `side` and `order_type` do
  reflect the current MVP scope (long/short, limit/guarded-market) rather
  than a fully generic order model — extend them when a real second case
  appears, not preemptively.

## OrderIntent

Research Plane → Risk Gateway. A proposed trade the strategy/research side
wants the Java trading plane to evaluate. Python must never send this
directly to an exchange — it only ever goes to the Risk Gateway.

| field | type | required | notes |
|---|---|---|---|
| `intent_id` | UUID string | yes | idempotency / client-order-id basis |
| `symbol` | string | yes | e.g. `"BTC-USDT"` |
| `side` | enum: `LONG`, `SHORT` | yes | |
| `order_type` | enum: `LIMIT`, `GUARDED_MARKET` | yes | |
| `quantity` | decimal string, > 0 | yes | |
| `limit_price` | decimal string, > 0 | conditional | required if `order_type == LIMIT`, must be null if `GUARDED_MARKET` |
| `signal_timeframe` | string | no | e.g. `"15m"` — traceability to the timeframe that generated the signal |
| `created_at` | ISO 8601 UTC timestamp | yes | |
| `schema_version` | string | yes | `"1.0"` |

## RiskDecision

Risk Gateway → Execution. The Risk Gateway's verdict on an `OrderIntent`.

| field | type | required | notes |
|---|---|---|---|
| `intent_id` | UUID string | yes | references the `OrderIntent` it decides on |
| `decision` | enum: `APPROVED`, `REJECTED`, `MODIFIED` | yes | |
| `reason` | string, non-blank | conditional | required if `decision` is `REJECTED` or `MODIFIED` — audit trail |
| `approved_quantity` | decimal string, > 0 | conditional | required (with `approved_leverage`) if `decision` is `APPROVED` or `MODIFIED`; must be null if `REJECTED` |
| `approved_leverage` | decimal string, > 0 | conditional | required (with `approved_quantity`) if `decision` is `APPROVED` or `MODIFIED`; must be null if `REJECTED` |
| `decided_at` | ISO 8601 UTC timestamp | yes | |
| `schema_version` | string | yes | `"1.0"` |

## Deliberately out of scope for now

`ExecutionReport`/fill schema and a shared `MarketData`/kline schema are
not defined yet — add them when the OMS skeleton (Implementation
Priority #2) or Risk Gateway skeleton (Implementation Priority #3)
reveal what they actually need, not ahead of time.
