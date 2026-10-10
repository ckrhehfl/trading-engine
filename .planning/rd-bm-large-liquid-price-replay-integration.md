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

### Pure paired report and scoped coverage route before actual returns

Added `research.activity_timing_report.paired_timing_report` over the existing
immutable replay results and explicit paired partition declarations. Initial
NAV, daily NAV ratios/returns, total return and maximum drawdown retain Decimal
arithmetic/text; initial NAV is both the first-return denominator and the initial
drawdown peak. Dispersion is unannualized sample session-return standard
deviation with float conversion only at that statistical boundary. Fewer than
two returns has unknown dispersion. Zero NAV is absorbing, not silently repaired.
Signal and actual-target changes each retain per-formation symmetric-difference
and union counts plus their aggregate ratio; an empty union has unknown rate.
Filled entries, ordinary sales and terminal open lots are separate counts.
Common coordinates do not certify identical costs, initial books, source truth
or event coverage. This helper performs no replay, IO, logging, family sizing or
lag selection. The actual paired protocol remains separate.

The worker's 31 new reporting cases and 58 existing timing cases passed together
(89 passed in 1.86 seconds). Existing engine synthetic fixtures demonstrate
first-session cost loss, equal signals with different entry/NAV outcomes,
delayed exits producing different targets, absent fills, retained terminal lots,
cash-only books, empty unions, zero NAV and malformed-result rejection.

A separate source-only route audit identifies the remaining minimal connection:
reviewed coverage/event declarations and a thin logged paired caller to the
existing engine. Reuse AY rename/market-movement dispositions, AZ reviewed
corrections/additional listings and BD's voluntary pre-entry F&F disposition;
do not reacquire or convert those into compulsory exchanges. AI/AL's old SPAC
evidence has no exact-code overlap with this pool and cannot certify its
no-event coverage. Scope remaining evidence to exact code/ISIN and possible
holding periods, joining only relevant events and any successors. Do not review
every price row manually or require actual lots before preflight. No complete
reviewed no-event ledger for these periods was found in the public repository
declarations. No actual input was opened by this audit. Paired descriptive
timing sensitivity has no family power-pass prerequisite; later strategy-family
calibration rules remain unchanged.

The reporting worker added a weighted-overlap regression after its first run
(32 new / 58 existing cases, 90 passed in 2.30 seconds). The main-agent combined
restoration, adapter, report, timing, partition replay and index run then passed
262 / one skipped in 11.73 seconds. A fresh adversarial review subsequently
reproduced one genuine count inconsistency: a result with one selected target
could report two filled entries. The explicit partition engine records exactly
one filled or unfilled outcome for every target, so the report now requires
their sum to equal target occurrences. It does not compare sales to entries,
because valid initial inventory can produce more sales than new entries.
Four mutation regressions cover excess entries, excess unfilled outcomes,
missing outcomes and double counting. Final worker report/timing verification
passed 94 cases (36 new plus 58 existing) in 2.24 seconds; scanner passed.
The earlier combined run predates this count correction and is not final-head
CI evidence. The new source consumes no actual input or returns.

### PR #271 completion and the preserved first BM attempt

PR #271 completed substantive CodeRabbit review on exact head
`69ba19585d7c70db82b24335342b18fb54c77f6b`.
Its initially requested extra private overlap assertion was answered with the
public caller's existing length/formation preconditions and rejection cases.
CodeRabbit withdrew the mandatory finding, resolved its thread and approved
that unchanged head at 2026-10-10T03:41:31Z. The reply is
<https://github.com/ckrhehfl/trading-engine/pull/271#discussion_r4236251864>.
All nine checks passed; each final Python CI run passed 5,629 / three skipped.
Normal squash merge was `c17babb7a45a70ad252a8b862a1bf80a617ea189` at
03:42:35Z. Reviewed-head and merge trees both equal
`d3b2fbf1b0fca9fb5fcfd23a9ab877af77f5d09c`.
The isolated research checkout reached that exact clean merge at
03:43:27.442085Z. The clean collector stayed at
`4ce85d714890b87f1a57ae89d4942660e41c0483` throughout.

Before price consumption, the auxiliary BL receipt provision failed because
the owned GCP research-evidence base was mode 0775, violating the helper's
private-parent requirement. No target receipt was created. Preserve the
original provision stdout/stderr/exit evidence in the registered independent
root. A separately recorded metadata repair tightened only that owned base
to 0700 at 03:46:47.269642Z, without changing contents or either checkout.
A separately registered `provision-recovery-1` reused the original remote
source byte-for-byte and succeeded. Its exclusive 1,611-byte BL receipt at
`/home/minjun4897/research-evidence/large-liquid-bl-independent-20261010-v2/verification.json`
has SHA-256 `915237d4306b4c93d2b6ac4e503f759d3866d3e61346cfac0009b0ac4678d860`.
Parent/file modes are 0700/0600 and readback matched. Do not provision it again.

The first actual BM diagnostic was separately logged as run
`780fd304-39e1-4fd8-a135-bdf654818bdf`, started
2026-10-10T03:47:55.633824Z and failed at 03:47:58.752115Z.
All eleven saved BL/receipt/BJ inputs were hash-verified. The failure preceded
read-scope creation and price-DB access: only failure, input manifest, source
manifest and specification files exist in the original registered v1 run.
There is no price snapshot or holding audit, and no completed performance run.
Preserve that output and the publication wrapper's nonzero exit with no retry.

| preserved file | SHA-256 |
|---|---|
| failure.json | `d5c5ba7b82f58721267096b7abee7ec32379eedec8c490c30569fff36c5ffc46` |
| input-manifest.json | `ead01dbd2146840189a0f4395f36ae313f431a1c89cb96d6406ee13c232fba50` |
| source-manifest.json | `dc152c7acb01e49bec908235e08f012220e9d2543c5c27485a12bed2b2a69417` |
| specification.json | `96aedecdc8207fbc4f55405a19ca4522893f23e95f6d5ddd424014b041654f77` |

Source-only comparison identifies the exact incompatibility: pinned BL uses
integer `5` for slippage while the unchanged reference and BM specification
use float `5.0`. Every other parameter's canonical encoding agrees. The BL
validator accepted numeric equivalence; BM's new package boundary demanded
identical encoding. This is a connection defect, not a changed cost or a
market-data failure. Do not modify the immutable upstream specification,
receipts, pins or research parameters to repair it.

### Registered BM recovery-v1 before another actual attempt

Implement only the input-boundary exception described in CLAUDE.md: both
slippage values must be finite JSON int/float numbers, excluding bool/string,
and have equal Decimal values. Keep exact encoded comparison for every other
parameter and retain the new BM specification's strict reference comparison.
The actual committed BL/reference-literal regression, altered/nonfinite costs,
bool/string, missing/additional fields and other numeric type mutations join
the synthetic package roundtrip. Worker preflight/restoration verification
passed 138 cases in 7.34 seconds; scanner and whitespace checks passed.
This is synthetic compatibility evidence, not a successful actual diagnostic.

Keep the original actual specification and its `96aedec...` byte pin above.
Register one fresh output:
`/home/minjun4897/research-evidence/large-liquid-bm-holding-price-inputs-20261010-recovery-v1/run`.
Its fresh local publication root is
`/home/minju/.local/share/trading-engine-research/large-liquid-bm-holding-publication-20261010-recovery-v1`.
Retain all nine BL pins, its provisioned receipt, BJ pin, exact signal/date
union, source/runtime guards and resource limits from the original protocol.
No old file is replaced. Source review, exact-head CI/substantive CodeRabbit,
normal merge and exact isolated research deployment precede this single
separately logged attempt. Preserve failures; do not automatically retry.

Freeze `var/bm-holding-independent-recovery-v1.py` at SHA-256
`8043e60b28dad16f8cd68906ef54d94015fc5b785d3e9c4e4bcd5c81c728bad9`;
its tests are `528843475b01e03ba36f5f59dceb8f2f296c99637160a6fa56e91022f1bd8831`.
Its only delta from the original wrapper is the remote OUTPUT path above.
Ten fake-SSH checks passed. Retain the original helper and all provision and
repair evidence. Invoke only `verify REVIEWED_MERGE` once after successful
recovery publication, using the existing private independent root's unused
verify-prefixed exclusive files. Do not invoke its provision mode.
Before run/verify, check all three helper pins: original publication `0e86...`,
original producer-free verifier `6fde...` and recovery wrapper `8043...` as
fully recorded here and above; compile and execute those same checked bytes.
The producer retains nice 10, 768 MiB address space, CPU 300 seconds, wall
900 seconds and the 2 GiB free-space floor. Independent verification retains
nice 10, 1 GiB, CPU 180 seconds and wall 300 seconds. No API, credential,
collector/process/schedule, event or performance change is part of this repair.

After the failed attempt, the first-performance groups remain three open and
zero newly closed: consequential price/action coverage, actual paired replay
integration and the final frozen logged study. D1/D2 completed actual
performance studies remain zero/zero. Historical availability/vintage is still
assumed; this package adds no official historical publication evidence.

Fresh source-only review found no actionable defect in the recovery comparison
or wrapper. The reviewer confirmed the BM specification-validator AST is
unchanged and the wrapper is exactly the original bytes with only OUTPUT
replaced. Main-agent preflight/restoration/planning-index validation passed
168 / one skipped in 9.66 seconds. The repository guardrail scanner and Git
whitespace check passed. Actual recovery, CI and CodeRabbit are still pending
at this registration entry.
