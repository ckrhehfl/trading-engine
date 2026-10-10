# Research Direction Task BM — connect proof partitions, prices and portfolio replay

Engineering preparation started 2026-10-10 while BL PR #269's final documentation
revision awaits CodeRabbit capacity. The complete execution design remains in
CLAUDE.md. BL actual recovery and independent verification are pending; this
record opens no actual source, manifest, DB, price or return read.

## Existing contracts and shortest integration route

The current synthetic replay requires classifications for its complete toy
non-frozen panel pool. BL's complete historical source/proof partition is a
different contract; shrinking the source pool to the holding price panel or
inventing classifications for proven failures would be wrong. Build an explicit
immutable evidence selection declaration and connect it to the same pure book
engine, preserving the original synthetic validation path.

The declaration must keep all dated source keys and dispositions, exact normal
window/ISIN identities, independently pinned package lineage, both signal lists
and metadata cutoffs. It selects eligible signals only; fixed hash/held/slot
logic makes portfolio targets before the day's quotes. Failed fills receive
no replacement. Synthetic checks establish internal contract/accounting behavior,
not actual source/classification/price correctness or study completion.

Use BJ's already registered conservative holding upper bound through the fixed
evaluation end, narrowing only by successful BL signals after its receipts are
frozen. Required price coverage precedes replay; actual target/filled/lot counts
come from replay. Do not introduce another circular target/lot-scope gate.
The existing bounded `activity_price_parity.load_scan` pattern already preserves
requested code/date storage identity, missing rows and a single read-only
transaction. The full-code `activity_inputs.load_scan` is not the bounded route.

BI activity evidence, a new holding-price snapshot and saved KRX raw observations
must retain separate coordinates/vintages. BK's partial parity is neither full
price coverage nor an explanation of vendor differences. Relevant compulsory
events, successor closure, settlements and reviewed no-event coverage must be
connected without presuming an empty event ledger is complete. No numeric
threshold, grid, holding rule, cost or price convention is changed here.

## Initial progress and boundaries

Four first-performance groups remain open until BL actually completes; no group
is closed by this engineering start. D1 and D2 completed performance studies
remain zero. First implement and synthetically verify the proof-selection seam;
then register the successful BL pins and exact consequential price/action union
before actual consumption. The current BL PR branch must not acquire BM changes;
BM changes remain separately reviewable and ship on their own branch/PR.

## Public vendor contract check, without market-input access

On 2026-10-10 the [official KIS exact-endpoint example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py)
directly states `FID_ORG_ADJ_PRC=0` for adjusted quotes and `1` for raw quotes
on `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`.
This confirms the current flag semantics used by the scan implementation.
It does not establish a historical response vintage, complete adjustment
formula, cash-dividend treatment or any particular cause of BK's differences.

The [official legacy daily-price example](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_domstk.py)
describes adjustments for split/consolidation-type rights against current price.
That example uses the different `/inquire-daily-price` endpoint, so its general
description is context, not proof of the exact long-window endpoint's full
adjustment algorithm. No trading API was called and no SDK/MCP was installed.

## Pure connection implementation and synthetic checks

The separate `PartitionSelection` path now connects the complete BL source
partition to the existing book engine without reconstructing that population
from the holding-price panel. Its factory checks both arms, field cutoffs,
issue/provenance joins, dispositions, normal candidates and signal lists.
The full formation rows and referenced metadata contribute to a fingerprint;
the caller still checks package byte pins and independent proof arithmetic.
Selection uses only the existing hash, held-code exclusion and slot count.
The original synthetic selection checks and accounting order remain intact.

Initial combined selection/replay/legacy/timing tests passed 185 cases. A fresh
source-only adversarial review found no producer-schema or accounting-order
defect; a literal-type consistency concern was nevertheless strengthened.
Cap-pass BH/BI/normal rows must retain common-share, non-foreign source labels.
The focused selection checks then passed 76 cases, including 18 mutations of
those labels and corrected aggregates. These are synthetic/internal contract
checks, not evidence that an actual price/action package is ready.

Next, implement a narrow pure holding-input adapter in the same engineering
package. Derive possible signal entry-to-evaluation-end code/date requirements
for both arms, requiring a subset of BJ's already registered original-issue
upper bound. Do not require actual targets/lots or truncate at scheduled exit.
Consume only the existing bounded reader's in-memory return schema; do not
call the cached-parity planning/runner path or open actual inputs here.

Verify the typed snapshot's fingerprint, requested rows and provenance before
building lossless Decimal series. Keep explicit absent rows separate from
unrequested calendar coordinates, reject unresolved NULL/storage/invalid values
at the replay boundary, and reuse the existing frozen/locked interpretation.
Preserve the separate activity and holding-quote pins. This adapter supplies
neither event/no-event coverage nor a vendor price-convention certificate.
Temporary synthetic databases may exercise the existing bounded reader; actual
price/action access still requires the separately registered successful BL pins,
exact scope, reviewed source and durable-start protocol described above.

The pure holding route is now implemented as `holding_requirements` and
`adapt_holding_scan`. It retains immutable typed receipts, separate BI/quote
identifiers and requested/absent/unrequested coordinates. An empty signal union
needs neither a quote read nor an invented snapshot. New holding checks passed
46 cases; the combined holding/partition/replay/timing/price-parity/input suite
passed 394 cases. The scanner and tracked whitespace check passed. A second
fresh static adversarial review found no actionable defect in the new adapter;
its own attempted WSL execution was unavailable, so execution evidence comes
from the worker's reported real WSL synthetic runs, not that reviewer.
The main agent's project-wide check is still running at this checkpoint.
No actual input, action, price or performance completion is inferred from these
checks, and the BM package has not yet been committed, reviewed or deployed.

The main agent's WSL project check completed with 5411 passed / three skipped
in 1596.38 seconds, including the scanner and both guardrail regression suites.
That collection preceded the final 18 literal-label mutations and 46 holding
tests. A final focused check of the current holding/selection/replay/timing/
parity/inputs and planning index completed with 424 passed / one skipped in
17.02 seconds. Thus the earlier complete collection and the subsequently added
cases have distinct execution receipts; neither number is described as CI for
an as-yet-unpublished BM head. The first non-login wrapper invocation could not
locate uv; specifying the already installed WSL executable path fixed the
invocation without installing or changing dependencies.

## Publication sequencing update

BL PR #269 completed actual review/normal merge, but its one actual recovery
failed at the inherited basic-coordinate join; both failures remain preserved
in BL's ledger. PR #270 repairs that exact join and registers fresh v2 helpers.
CodeRabbit's automatic response at 2026-10-10T01:58:04Z reported the next
included review in 34 minutes, with one included review per hour. No paid
overage or subscription change is authorized or requested.

Revise the earlier separate-PR sequencing: retain BL repair and this pure BM
engineering package as separate commits, but publish them together in PR #270
for one substantive final-head review. This avoids another capacity wait for
the already tested next input seam. Both parts remain separately inspectable.
The actual GCP operation remains only BL's registered recovery-v2 and its
independent output verification; shipping BM source does not authorize actual
BM price/action reads or imply replay/performance completion. Successful BL
output pins and a separately frozen actual-input protocol remain necessary.
The four open first-performance groups and zero D1/D2 performance counts are
unchanged by this publication sequencing choice.
