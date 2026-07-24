# Implementation Priority #5: Schema Compatibility Tests (retrospective summary)

**PR**: #13 (merged 2026-07-20)

Closed a gap CodeRabbit flagged on PR #9: schema tests only round-tripped
within one language, so a Python/Java field-name or format drift would
never be caught automatically. `schemas/fixtures/*.json` — five golden
fixtures (`OrderIntent` LIMIT/GUARDED_MARKET, `RiskDecision`
APPROVED/REJECTED/MODIFIED) that both `python/tests/test_schema_compat.py`
and `java/schemas/.../SchemaCompatTest.java` independently parse, assert
specific field values against, and re-serialize + compare as parsed JSON
structure (not raw text, since JSON doesn't guarantee key order). If both
languages reproduce the same fixture, they agree with each other by
transitivity — no cross-process test harness needed.

CodeRabbit approved with zero findings — the only PR so far with a clean
first-pass review.
