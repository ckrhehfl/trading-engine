# Implementation Priority #3: Java Risk Gateway (retrospective summary)

**PR**: #11 (merged 2026-07-17)

`engine.risk.RiskGateway.evaluate(OrderIntent, referencePrice,
AccountState) -> RiskDecision`. `RiskLimits.canary()`/`.stable()`
hardcode CLAUDE.md's Risk Parameters verbatim. Loss-limit breach checked
first (most to least severe), independent of the order; notional over
the limit clamps quantity down (`MODIFIED`) rather than rejecting
outright; leverage is always assigned from config, never taken from the
request.

CodeRabbit caught a critical bug: a non-positive effective price (e.g. a
bad market-data tick) made notional `<= 0`, trivially clearing any
positive max-notional check and approving the full requested quantity —
fixed by rejecting non-positive prices outright.

Notable resolved disagreement: CodeRabbit twice suggested lowering
`RiskLimits.ABSOLUTE_MAX_LEVERAGE` from 3x to 2x, citing CLAUDE.md's
"Live Entry Criteria: leverage hard max 2x". Confirmed with @ckrhehfl
this refers to the initial paper-to-live transition gate (which runs
under the canary tier, itself capped at 2x), not a ceiling the
later-stage stable tier (documented max 3x) must also respect — kept at
3x, documented in `RiskLimits`' Javadoc and (as of the audit that
produced this `.planning/` directory) cross-referenced in CLAUDE.md's
Risk Parameters section directly.
