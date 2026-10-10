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

## First price-diagnostic runner engineering

The next source-only implementation is the thin bounded holding-price
diagnostic runner detailed in CLAUDE.md. It connects pinned successful BL
outputs and independent receipt to paired selection/holding requirements,
requires a subset of pinned BJ scope, persists the exact read-scope before one
read-only scan, and publishes separate typed quote diagnostics. Cash-only
scope reads no quotes and has no quote hash. No returns, action application or
coverage certification is part of this diagnostic. Final performance retains
those separate obligations, but they are not prerequisites to this first
price/state diagnostic. No actual inputs may be opened by this engineering
work; exact successful BL pins/specification and bounded publication protocol
will be registered separately after BL completes.

Source-only integration review found a conditional cash-only seam mismatch:
the holding adapter correctly returned no quote snapshot for an empty signal
union, but replay required a price hash unconditionally. The explicit partition
path now accepts None only with an empty quote panel, every eligible tuple
empty, no initial lots and an exactly empty event tuple. Complete paired
schedule and explicit timing remain required. All nonempty and legacy synthetic
paths retain the existing valid-hash requirement; no absent quote identity is
invented. D1/D2 connected requirements-to-adapter-to-book cases preserve initial
cash 0, 37.25 and default 1. Related WSL synthetic regressions passed 266 cases,
with guardrail scan and whitespace check passing. This does not certify actual
no-event coverage or publish performance.

The bounded diagnostic runner is implemented as
`research.activity_holding_preflight`. It validates nine byte-pinned BL files,
their independent package receipt and the separate BJ scope, derives both
signal arms, fsyncs the exact read scope, then calls only the existing bounded
single-transaction RO scan and pure adapter. It preserves requested/absent/
unrequested coordinates, typed storage and row provenance, separate activity/
quote identities and value-free failed frames. No prices are read for an empty
union. Only the compact population input has a separate 128 MiB bounded private
reader; other evidence retains the existing 64 MiB limit. One-time calendar
positions and panel projection avoid repeated work per observation.

The worker's final runner-focused suite passed 47 synthetic cases. Its earlier
combined selection/holding/replay/source run passed 298 before four final reader
cases were added. Fresh adversarial source review found no actionable defect;
actual saved package/DB compatibility is not claimed by that review. The main
agent's related cross-module run passed 290 / one skipped before those same
four cases were added. The current published pre-delta head
`1b840292f1123a229244bbd3b10867ac5bc2ce59` passed all nine CI checks; its
Python PR job 38015380956/114104412953 reported 5479 passed / three skipped.
Those results have distinct source collections and do not represent final-head
CI for the new runner. No actual BM specification, source read, price diagnostic
or performance study has executed.

The final main-agent cross-module rerun of the current files passed 290 / one
skipped in 16.28 seconds, including the runner's final test file. The earlier
sentence dating its first 290-case collection before the last reader additions
was inferred from worker-message timing and is not established by that receipt;
the final rerun is the authoritative current-file evidence. Fresh cash-only
delta review also found no actionable defect. Final-head CI/CodeRabbit remain
required before publication to GCP.

Before the one final substantive re-review, documented the new runner and its
four new test modules, including nested callbacks: 53 missing docstrings added,
71 functions in those files now documented. The touched BL source-coordinate
regression also received a short intent docstring. AST parse/compile, scanner
and whitespace checks passed; the delta changes no executable logic. This
addresses the recurring docstring-coverage warning before consuming another
review attempt. Final CI still runs on the exact published head.

## Actual holding-price diagnostic registration after verified BL completion

BL recovery-v2 and its producer-free independent verification completed after
PR #270's reviewed merge `6301fab0ad9e43aa6052119e520c926cae25c236`.
Its ledger records all nine successful output pins, canonical receipts and
preserved failures. Both arms have 36 actual normal-screen signals across 31
codes and zero unresolved inputs. This closes the normal-input connection
group only: three first-performance groups remain open, one newly closed;
completed D1/D2 performance studies remain zero.

Register `configs/research/discovery/activity-holding-price-preflight-v1.json`,
SHA-256 `96aedecdc8207fbc4f55405a19ca4522893f23e95f6d5ddd424014b041654f77`.
Its parameters preserve the reference JSON values and numeric types exactly,
including threshold 3.0. Its exact twelve roles are the nine successful BL
outputs, their independent receipt, BJ's existing conservative scope and the
read-only scan database. The specification's absolute paths and byte pins are
the input allowlist; no original KRX/KIS envelope or another DB is opened.
Only `bsop_date, open, high, low, close, volume, turnover` are read from the
declared DB, using the existing single-transaction typed bounded reader.

The exact requested union is defined without a portfolio-lot dependency: for
each of the two pinned BL signal lists, take that schedule arm's possible
entry through the fixed evaluation end inclusive, then union by original
issue code/date. Validate it against the pinned BJ requested union before any
DB read. BL selected every one of BJ's 36 potential windows, so this protocol
expects the same 31-code / 24,598-code-date union, never a broader one. This
is a fixed deterministic scope definition from pinned evidence, not a claim
that a new quote snapshot or runtime read-scope hash already exists. The runner
must fsync the complete computed coordinates before opening the DB; independent
verification reconstructs them separately. No exit-date truncation, current
universe filter, unfilled-order replacement or reserved pre-2019 read is allowed.

One new actual attempt uses exclusive GCP output
`/home/minjun4897/research-evidence/large-liquid-bm-holding-price-inputs-20261010-v1/run`
and the existing canonical Discovery log at
`/home/minjun4897/trading-engine/runs/experiments.jsonl`. Keep clean isolated
research at the new reviewed merge before/after; sync only from the clean
previous reviewed merge above using an exact fast-forward. Collector HEAD and
cleanliness must remain unchanged at
`4ce85d714890b87f1a57ae89d4942660e41c0483`. No collector process, schedule,
credential or API changes are included. The single attempt retains durable
start before saved-input reads, exclusive private outputs and any partial
failure, with no automatic retry. Use niceness 10, 768 MiB address space,
CPU 300 seconds, wall 900 seconds and a 2 GiB free-space floor.

The BL independent receipt currently exists locally as the exact 1611-byte
`/home/minju/.local/share/trading-engine-research/large-liquid-bl-independent-20261010-v2/stdout.json`,
SHA-256 `915237d4306b4c93d2b6ac4e503f759d3866d3e61346cfac0009b0ac4678d860`.
Before the actual diagnostic, provision those checked bytes once to the new
private GCP path named in the actual specification. Preserve bytes exactly,
create parent 0700/file 0600 exclusively, fsync and read back the byte pin.
Do not alter the immutable nine-file BL package or claim Git installs this
private receipt. No original input, credential or database is copied.

The source-only publication helper is `var/bm-holding-publication.py`, SHA-256
`0e86bfd7360de0db2230e7b50f9ce63f76594045eebe4d251cba6b994338aa4c`;
its synthetic test source is
`14498b01f83154a7d3e78347850a6b315dfdea3b9589a46e0ad1866b6eb86c3e`.
It takes the exact committed specification path/pin, reviewed merge, exclusive
GCP output and fresh local private receipt directory, plus checked helper and
verifier source pins. It performs one research module invocation, preserves
local source/registration/remote/stdout/stderr/exit evidence and checks all six
successful output hashes, committed source/spec links, typed input statuses
and exact newly appended canonical started/completed receipts. It does not
sync the checkout or execute the independent verifier. Thirty-three fake-SSH
synthetic cases passed; fresh adversarial source review found no actionable
defect. The actual specification's pure validation and related runner/index
regressions passed 77 tests / one skipped. No actual BM input was opened by
these checks.

The separate producer-free verifier is `var/bm-holding-output-verify.py`,
SHA-256 `6fde4f31a18ff3b1d6b93f1aa2bfa53e7f6e47da42777be6289f4fa0abbe6e0a`;
synthetic tests are pinned at
`5a5dd898d9318b66bef217819e54759b7478a8083489475ac01de90307922a0f`.
Its exact read allowlist is six new BM files (`result.json`, `specification.json`,
`source-manifest.json`, `input-manifest.json`, `read-scope.json`,
`holding-input-audit.json`) plus separately byte-pinned BL `result.json`, BL
`read-scope.json` and BJ `result.json` from the actual specification. The extra
BL scope supplies the complete calendar absent from BL's result. Verify its
hash against BL's result audit map. Read no DB, original response, canonical
log or other input during this independent step. Reconstruct the paired signal
union and BJ subset, typed fingerprint/rows/storage/missingness, requested/
absent/unrequested coordinates, metadata/provenance, summaries, input links
and separate BI/quote identifiers. The CLI retains the fixed actual parameters;
synthetic overrides are not exposed as execution options. Twenty-eight synthetic
tests and mutation subcases passed; fresh source-only adversarial review found
no actionable defect. The 64 MiB per-file verification limit is retained:
synthetic size accounting for the fixed 24,598 requested coordinates plus all
34,240 possible unrequested coordinates estimated 49.53 MiB even with generous
normal numeric widths and an extra MiB of metadata. This estimate is not an
actual output-size observation.

The independent report certifies output arithmetic/typed consistency and
pinned evidence links. It does not independently reread the original BL
verification receipt, establish original source/DB-transaction truth, certify
vendor price conventions, identify corporate events or compute performance.
Relevant event/no-event and basis accounting remain later obligations, not
prerequisites to this diagnostic. Freeze the separate receipt-provisioning/
independent wrapper and its tests before publication below; retain its checked
source privately and execute the same checked bytes in memory over SSH, as for
BL. Final CI/CodeRabbit, normal merge and exact research deployment precede
actual BM input consumption.

### Checked private provisioning and independent wrapper freeze

Freeze `var/bm-holding-independent.py` at SHA-256
`45bf88b5cbc7eaaee40460e29bdf65ced674a68eef07e40623ab90dcf242c523`;
its synthetic test source is
`038b42ed088e52412f3d777ab9eebfdb407d933adfd43bff58d4657f924a28d9`.
It accepts exactly `provision REVIEWED_MERGE` or `verify REVIEWED_MERGE`.
Provision once after the reviewed research deployment; run independent verify
once after successful actual diagnostic publication. Both modes require the
exact clean reviewed research commit and unchanged clean collector before and
after. Verification uses niceness 10, 1 GiB address space, CPU 180 seconds and
wall 300 seconds; it imports no producer. Its failure JSON and nonzero status
remain preserved, including verification failures, rather than losing the
captured diagnostic to a wrapper exception. Ten fake-SSH synthetic tests passed.

External helper sources are this task's WSL directory
`/mnt/c/Users/minju/.codex/worktrees/0473/trading-engine/var`; Git does not install
them. Before the first operation and each provision/run/verify invocation,
check all three registered BM helper byte pins (publication, independent
verifier and two-mode wrapper) and compile the same checked bytes with the
proper source `__file__`. Archive those bytes and their exact registration
privately before SSH; do not execute a newly reopened unverified path. A later
host/source-path change requires the same pin checks. The committed research
calculation remains `research.activity_holding_preflight`.

The fresh publication receipt root is
`/home/minju/.local/share/trading-engine-research/large-liquid-bm-holding-publication-20261010-v1`.
The two-mode wrapper uses
`/home/minju/.local/share/trading-engine-research/large-liquid-bm-independent-20261010-v1`,
created exclusively during provision and reused only for its distinct verify
receipts. Mode-prefixed launcher/source/remote/registration/stdout/stderr/exit
files are private, exclusive and durable before SSH. The only evidence copied
to GCP is the fixed verified BL receipt; no credentials or original market data
are moved into a checkout.

An additional producer-to-independent synthetic integration source is
`var/bm-holding-roundtrip-tests.py`, SHA-256
`bbeba52f129675aa84698737dc92e806c7e409d9350973f00b8599003bde0e32`.
All four checks passed: real `run_preflight`/`evaluate_inputs`/PinnedInputs and
bounded temporary SQLite output consumed by the independent verifier, observed/
frozen/locked/absent/unresolved states, empty-union no-scan/no-quote behavior and
state tampering even with internally reissued output hashes. The verifier's
stdlib-only import boundary is also checked. These integration tests use
synthetic evidence and do not certify actual-package compatibility or prices.

### Pure restoration seam batched before the actual diagnostic review

Implemented `research.activity_holding_restore.restore_holding_inputs` while
the actual diagnostic registration awaited substantive CodeRabbit review.
It reconstructs the exact bounded reader scan from the retained holding audit,
rechecks the canonical read-scope hash and caller-supplied activity/quote pins,
then reuses `adapt_holding_scan`. Audit rows, state/issues, coordinate sidecars
and projection counts must agree. The caller still verifies original package
bytes, source links and the independent receipt separately; this helper is not
a second whole-package verifier. It opens no file or database, supplies no
event declaration and computes no performance. Empty scope retains None scan
and no quote snapshot. No actual research input was consumed in this work.

The actual producer's synthetic audit restores the complete immutable adapter
result, including exact Decimal text, TEXT/INTEGER/REAL/BLOB/NULL identity,
requested absence, unrequested coordinates, frozen/locked bars, unresolved
observations and row provenance. Newly pinned contradictory storage identity,
scope/metadata/row mutations and invented cash-only evidence are rejected.
The worker ran 51 new restoration cases plus 46 existing adapter cases:
97 passed in 7.83 seconds. Repository scanner and whitespace checks passed.
A separate fresh source-only adversarial review reported no actionable finding.
The helper is batched into this existing diagnostic PR before its substantive
review rather than creating another review queue. Actual BM price consumption,
events/basis accounting and D1/D2 performance remain unexecuted.

The main-agent focused run added the planning-index regression to those two
modules: 127 passed / one skipped in 8.41 seconds. This is synthetic source
validation, not an actual audit restoration or price diagnostic.
