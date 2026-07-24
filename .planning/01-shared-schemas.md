# Implementation Priority #1: Shared Schemas (retrospective summary)

**PR**: #9 (merged 2026-07-17)

`OrderIntent` and `RiskDecision` — Python (pydantic) and Java (Jackson)
implementations of the same contract, documented in `schemas/README.md`.

Design decisions: decimals serialize as JSON strings (never native JSON
numbers) to avoid float round-trip loss between `Decimal`/`BigDecimal`;
every message carries `schema_version` from day one; `symbol` stays a
free-form string rather than a BTC-USDT-only enum for future multi-symbol
work.

CodeRabbit caught real bugs across four review rounds: missing
positive-value validation on quantity/price/leverage, `NaN`/`Infinity`
accepted by Python's `Decimal`, naive (non-timezone-aware) datetimes
accepted, `RiskDecision`'s approved-field consistency not enforced,
Python/Java disagreeing on whitespace-only `reason` strings. All fixed
before merge — see PR #9's commit history for the specifics.
