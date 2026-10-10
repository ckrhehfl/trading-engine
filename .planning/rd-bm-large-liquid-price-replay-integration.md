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

### Recovery-v1 completed and independently checked

PR #272 received substantive CodeRabbit approval on unchanged exact head
`71bd27ad1efac78b91313cc38b3595d8b7701235`, review `5477610742` at
2026-10-10T04:38:33Z. No review threads were opened. All nine checks passed;
each Python CI run passed 5,669 / three skipped. Normal squash merge was
`74ae8aae8e687f13efe6a4ccd4daa54f076eb028` at 04:39:10Z. Reviewed-head and
merge trees both equal `2f78fc1bbac522b09f22330372543b52fd9bc674`.
The isolated GCP research checkout reached that exact clean merge at
04:39:45.151113Z; the clean collector remained at
`4ce85d714890b87f1a57ae89d4942660e41c0483` before and after deployment,
diagnostic and independent verification. Exclusive recovery-sync receipts
preserve the checked helper, remote source and state reconciliation.

The single registered recovery diagnostic completed as run
`086bdb3b-9343-4307-ac1f-7eef1f5cd700`, started 04:40:22.536900Z and
completed 04:45:12.060149Z on October 10. It used the unchanged specification
and eleven input pins; there were no new API requests or automatic retries.
The separate recovery output contains the exact six successful diagnostic
files. Original failed outputs and auxiliary provision/repair receipts remain
unchanged.

| recovery output | SHA-256 |
|---|---|
| result.json | `9423082f1b8a69193f5c596095c17d94b65f0f2e5b2501dfcfc427ce25481e8d` |
| specification.json | `96aedecdc8207fbc4f55405a19ca4522893f23e95f6d5ddd424014b041654f77` |
| source-manifest.json | `1f9c9c1dfc16dd2709e16f4a70954479abdf5d56f1a036fb16e32306c9900331` |
| input-manifest.json | `db72b256e551d028b597d52b46a663fb7d007374710245e6e035dfb1c5589d24` |
| read-scope.json | `905c52d3696006a1938df7c4cf06fd216e40b4e5fec584b4511b09d567d0734d` |
| holding-input-audit.json | `567c800406e365a01b3bc6e86467d0d7a547062e09e64934872b81ecb08c594b` |

The exact 31-code / 24,598 requested-coordinate union contains 23,852 observed,
92 frozen and 654 absent requested rows. There are no unresolved observed rows.
The separate 34,240 unrequested coordinates are not missing requests. Both arms
retain 36 signal-window occurrences. Absent rows' causes are not established by
this diagnostic: do not label them delisted, halted or zero recovery, and do not
erase their signal/source population. These counts are not filled entries or
completed holdings.

The registered recovery independent wrapper executed verify once, without
provisioning the BL receipt again. Its producer-free verifier independently
reconstructed the six-file hashes, internal package links, exact requested
union, typed-row fingerprint, metadata, missingness and state counts from new
outputs and the three pinned BL/BJ reference outputs. `verified` is true with
scope `BM_new_output_typed_rows_scope_and_internal_lineage_only`.
Package SHA-256 is
`8552173544a761f885491ca74afadecae86ba10b12aac48798a214472992b6f1`;
quote snapshot is
`2488132206b8978016b0d322377523746aaee98e49c5c23464964e1a4c985499`;
activity snapshot remains
`1ff51e25fdd71e8c3c0798de9c1a39b822f683ff6609a8e2adcd6cd1b3b44308`.
The exact 2,142-byte local `verify-stdout.json` has SHA-256
`c6895ace52cfb4beab4045aa4860bf009eb0b600ace4a5cf1e14d4d282b49529`.
Its original-source truth, DB transaction/whole-DB hash, BI snapshot equality,
event coverage, vendor price basis and historical vintage flags remain false.
Independent verification opened no DB/API/original source inputs and computed
no returns.

The bounded recovery package is complete: eight of eight local/source/review/
CI/merge/deploy/diagnostic/independent-verification stages. The first-performance
groups remain three open and zero newly closed by this diagnostic: consequential
price/action coverage, actual paired replay integration, and the final frozen
logged study. D1/D2 completed actual performance studies remain zero/zero.
No new official historical-publication evidence was obtained; availability and
vintage remain assumed. The next work uses these exact interval/state outputs
and existing reviewed issuer evidence to resolve consequential actions, while
connecting the already tested book/report components. No email/telephone or
whole-market notice search is introduced.

### Registered post-diagnostic metadata inventory before saved-output reads

The successful independent check above permits the next source-evidence
inventory, not another market scan or performance study. Read exactly three
private GCP files once, verifying their registered bytes before JSON parsing:

- Recovery `run/read-scope.json`, SHA-256 `905c52d3696006a1938df7c4cf06fd216e40b4e5fec584b4511b09d567d0734d`, ceiling 4 MiB.
- Recovery `run/holding-input-audit.json`, SHA-256 `567c800406e365a01b3bc6e86467d0d7a547062e09e64934872b81ecb08c594b`, ceiling 64 MiB.
- `/home/minjun4897/research-evidence/large-liquid-bk-evidence-adjudication-20261010-v1/verification/bk-period-adjudication.json`, SHA-256 `2c6f1fab960bf5214fe0a176b20ffee2207e4d10844c23b632a9cfebe1bef893`, ceiling 8 MiB.

The recovery root is the exact recovery-v1 location registered above. Project
only requested code/date coordinates, quote/activity/read-scope identifiers,
per-code state counts and consecutive abnormal-state runs; omit all typed
prices, volume, turnover, marks and returns. Reconcile every audit row to its
requested coordinate and count. From BK project exact matching code/ISIN and
window IDs/formation/approved interval bounds plus report receipt, source URL,
raw/readable hashes and publication/report-period metadata. No body/passages,
source truth, period extension or action decision is part of this inventory.
The bounded JSON parser necessarily reads the pinned audit bytes to extract
those fields, but does not adapt, rank or replay any price value.

Use a source-reviewed metadata helper with nice 10, address-space 768 MiB,
CPU 60 seconds and wall 120 seconds; no DB/API/credential access or retry. Check
the clean research/collector heads before and after. Persist helper/remote source,
registration, stdout/stderr/exit and the resulting metadata inventory in a fresh
exclusive local private root
`/home/minju/.local/share/trading-engine-research/large-liquid-bm-event-metadata-20261010-v1`.
This inventory determines exact subsequent public-source questions, not an
eligible-universe or no-event certificate. Register any later source-summary
or selected leaf read separately before access; reuse existing evidence first.

The metadata helper is frozen at `var/bm-event-metadata.py`, SHA-256
`136a3d13f4a708b8cee2e563c76e46b5833a73742f9c901a6f9a045b39777be2`;
its synthetic tests are
`2b35b923219605418b88f388a3192dbce22ebefc90114a65494df80015401021`.
Thirteen synthetic tests passed, including coordinate/count/identity/boundary
mutations and exclusive receipt checks. The source was reviewed before actual
invocation; remote Python disables bytecode and Git uses optional-locks off.
Check this exact source pin before compiling/executing those same bytes once.
Only the metadata projection below will be retained locally, not original
price/audit bodies. This is evidence inventory, not a replay/performance run.

The one metadata inventory completed successfully; its 369,299-byte private
`stdout.json` has SHA-256
`7a246c7a3dc349cdc978db1b060c77e6c6521e44a5e6df0e5aefd3c7288c2330`.
It reproduces all 24,598 coordinates, 31 exact issues and 36 BK window anchors,
with no raw price output or source-period extension. Both checkout guards passed.
All 654 absent requests are code 091990, 2024-01-12 through 2026-09-18. Frozen
runs are 003550 (2021-04-29–05-26, 18 sessions), 009830 (2023-02-27–03-30, 23),
012450 (2024-08-29–09-26, 18), 091990 (2023-12-18–2024-01-11, 16), and 207940
(2025-10-30–11-21, 17). These source-derived states do not establish their
corporate-action causes, rights or settlement. Several runs occur years after
their possible entry; the original through-end upper bound is preserved, but
it must not become a demand to reconcile events after a separately demonstrated
ordinary exit. A proposed conditional exit bound needs source/engineering
review and a separately logged actual quote-input protocol; metadata alone
does not establish realized exits or excuse unresolved earlier actions.

Before external discovery, register five exact public official-source metadata
queries, one each, no automatic retry or query expansion:

- `site:kind.krx.co.kr "091990" "2024" "합병"`
- `site:lg.co.kr "2021" "인적분할" "LX"`
- `site:kind.krx.co.kr "009830" "2023" "분할"`
- `site:kind.krx.co.kr "012450" "2024" "분할"`
- `site:kind.krx.co.kr "207940" "2025" "분할"`

Use the official-result metadata only to identify the exact event/period question
and selected official leaves. No search-hit absence is no-event coverage. This
does not authorize market-data requests, successor quotes or whole filings.
Register exact selected body URLs/pins before any later content review; retain
proposed/effective/delivery/payment dates separately. Later source questions
must follow the demonstrated relevant holding scope rather than expanding every
signal to every future corporate event by default.

### Conditional ordinary-exit child scope registered before implementation

The source-only independent audit found that the original through-end scope can
include corporate actions years after a scheduled ordinary sale. Preserve that
scope and all diagnostic bytes. Implement the explicit conditional child contract
now recorded in CLAUDE.md: every potential formation/arm/code/ISIN entry has exact
due entry-index+holding_sessions and a provisional first resolved observed positive
unfrozen opening at or after due. Locked openings remain the existing proxy. No
candidate or out-of-calendar due retains the evaluation end. No return, lot,
source truth, action completeness or historical publication claim follows.
Final use requires separately pinned inclusive calendar-day no-action coverage
through the candidate itself; earlier pending rights or relevant events keep and
expand the original bound. All later formations and both lagged entries remain.
The original package validator remains exact; a separate child proof must be
recomputed before any shorter decoder requirement is accepted.

The five previously registered public metadata queries were each submitted once.
Search output included incidental filing excerpts, including historical prices;
none was used as a quote/selection/return input or no-event coverage. The 091990
official result identifies a merger-related ownership-report cessation:
https://kind.krx.co.kr/external/2024/01/05/000505/20240105001406/00636.htm .
It is a candidate source, not a reviewed effective/delivery/payment certificate.
The other returned excerpts do not establish any relevant split. No search
absence establishes complete coverage. A bounded continuation may submit one
official LG-domain metadata query, `2021 LX 인적분할 5월 1일`, and retain only
result title/URL. Register the exact chosen body before its separate review.

Source-only fresh review also passed the action decoder after rejecting code
reuse across different ISINs, including disjoint original intervals and pending
successors. Source SHA-256 is
`f2e19d21ab5cda4822c5b24572dcca79b34a10e5b44bcf8b2a15a25e078564a4`;
tests `b667227316eae7e16f34ac2d16ce3a87d48213d0deb4c8d0a93b40fd8e8167ba`.
The related synthetic suite passed 270 cases. Fresh pure paired-prototype review
passed 28 integration cases, including pending and in-horizon cash settlement,
with no actionable findings; source/test pins are
`49be69a06f2e8d4d9796fd47e58b82873fca1f92f11781c35620577513c3be93` and
`2662577274f9349c91f286bf2063a4e0db929d48bbdab3ad31b74c8d1d82a5bd`.
Move that minimal pure seam into production and connect the independently
recomputed child proof. These synthetic checks close no actual study condition.

Before selected official-body review, register exactly two LG issuer pages:
`https://www.lg.co.kr/media/release/22752` (2020 plan) and
`https://www.lg.co.kr/ir/public/notice/34` (2021 split-progress announcement).
Read each once through the public web tool, with no automatic linked-page fetch
or expansion. Distinguish proposed ratios/dates from actual effectiveness,
allotment, listing, delivery and fractional-cash settlement. These sources may
identify a relevant action but cannot extend BK periods or certify full action
coverage or price basis. No market-data query/return computation is authorized.

The selected issuer announcement explicitly reports completion of the split
procedures on its May 3, 2021 page. The earlier release supplies proposed ratios,
not a final allotment certificate. Before follow-up selected-body reads register
exactly notice/34's body image (web link 46) and its adjacent titled notice
`(주)LG 분할에 따른 주식 병합 공고` (web link 47). These exact source coordinates
are used once each, without other linked documents or market data. Actual split
rights may affect the April 2021 potential entry; no event accounting conclusion
or coverage certificate follows yet.

### Registered actual ordinary-exit preflight v1

Before another saved market-input read, freeze committed specification
`configs/research/discovery/activity-ordinary-exit-preflight-v1.json`, SHA-256
`640f6a01a94fd1b2d46f95a53a2144237d041fe4a32821ea93985635ef4faf57`.
It preserves the reference window and exact parameter encodings, the nine
successful BL outputs/receipt/BJ and the six successful BM recovery outputs
recorded above. Its eighteenth input is the exact 2,142-byte BM independent
receipt at the new exclusive GCP path
`/home/minjun4897/research-evidence/large-liquid-bm-independent-20261010-v1/verification.json`,
SHA-256 `c6895ace52cfb4beab4045aa4860bf009eb0b600ace4a5cf1e14d4d282b49529`.
Provision only that checked receipt once; do not rerun either producer/verifier.
The original local receipt stays unchanged. The provision's local private root
is `/home/minju/.local/share/trading-engine-research/large-liquid-bm-receipt-provision-20261010-v1`.

One reviewed normal merge will be fast-forwarded only into the existing isolated
research checkout, from clean `74ae8aae8e687f13efe6a4ccd4daa54f076eb028`.
Check reviewed-head/merge tree equality and both checkout states. Collector must
remain clean at `4ce85d714890b87f1a57ae89d4942660e41c0483`; change no process,
schedule, credential, database or collector source. Fresh deployment receipts
go in `/home/minju/.local/share/trading-engine-research/large-liquid-bm-exit-deployment-20261010-v1`.

Invoke the new committed `research.activity_exit_preflight` once, using that
exact reviewed merge and specification, fresh GCP output
`/home/minjun4897/research-evidence/large-liquid-bm-ordinary-exit-scope-20261010-v1/run`,
canonical `/home/minjun4897/trading-engine/runs/experiments.jsonl`, and local
publication root
`/home/minju/.local/share/trading-engine-research/large-liquid-bm-ordinary-exit-publication-20261010-v1`.
Durable started precedes all eighteen saved-input reads. Retain original byte
pins and full source/dependency/runtime manifests, exact full quote snapshot,
every arm/formation entry and separate provisional child union. Read no database,
API, credential or filing body. No books, returns, no-event coverage or actual
exit claim is computed. Resource limits stay nice 10, address-space 768 MiB,
CPU 300 seconds, wall 900 seconds and at least 2 GiB free space. Retain measured
resource usage, six exclusive private outputs and partial failure evidence.
No automatic retry or old-output replacement is allowed.

Frozen private helpers (compile/execute the same checked bytes):

| helper | SHA-256 |
|---|---|
| `var/bm-exit-sync.py` | `6a0b25e225bd91b2776ecaca885cfed30cb091188241a815bb6188ad25192139` |
| `var/bm-exit-receipt-provision.py` | `720536db5f4ab84d7e5f599d2c09a105fb639fd2154cc3400a6507b2796a4ede` |
| `var/bm-exit-publication.py` | `1d01e61556c9503abedae6597636846e9774bb60c15caef97a3657a1de5c424e` |

Fresh independent source review passed the conditional planner, logged runner,
actual specification and helpers. It caught a new-directory parent-fsync omission
in the sync helper; the repair was independently rechecked with fake SSH/fsync
tracing before any actual operation. Its 118 synthetic tests passed. A separate
fresh paired-source review passed package joins, full-snapshot retention,
no-event-only child proof and full supported/pending-event fallback; four extra
repinned tamper cases were rejected before replay. Related implementation suites
passed 312 cases. These reviews use source/synthetic inputs only and establish
no actual event coverage or completed study.

After successful publication, independently verify the new six outputs against
only pinned BL result, original BM read-scope/audit and its independent receipt,
using a separate source-reviewed stdlib helper without producer/planner imports.
Register that verifier's exact source/test pins and exclusive private receipts
before invocation. It recomputes potential entries, due/candidate coordinates,
unions, windows and package links from the previously verified original states;
it does not reopen a DB or certify original source truth, events or vintage.
Source/CI/substantive CodeRabbit/merge/deployment and success/independent check
remain pending at this registration. First-performance groups remain three open,
zero newly closed; D1/D2 completed studies remain zero/zero.

Before reusing source-acquisition identifiers, register one bounded local
metadata read of ignored `var/bk-overview-inventory.json`, SHA-256
`2a45f0b51064940fd10b29453b501f461277e8e2219b86b8157c79c791b2943a`,
ceiling 2 MiB. Project only each of the existing 31 code/ISIN/corp_cik and report
receipt/publication/period/selected-source-coordinate fields. No leaf body,
price, ratio, metric, source-period extension or new request is part of this
read. Retain the inventory's exact original IDs; actual follow-up source dates
will be registered after the conditional coordinate preflight, not extrapolated
from formation-only evidence.

### Ordinary-exit preflight completed and independently checked

PR #273 received substantive CodeRabbit APPROVED on head
`6daaef8bb23e218f3d3db9aba8b128b4ba9c2037` at 2026-10-10T05:41:24Z;
all eight CI jobs passed, including 5,894 Python tests with three skips, and
there were no review threads. Normal squash merge
`a79262c080d71c0e77480b99f1e7647ae978a497` completed 05:42:05Z. Both trees equal
`b111fa80ab0ecc1a4db6f7488fde60b9836c3340`. The isolated research fast-forward
completed 05:42:51.742603Z; the collector stayed clean at its unchanged recorded
operational commit. The original BM independent receipt was provisioned once
at its registered private GCP location, without rerunning its producer/verifier.

The registered run `5b5e0702-adee-4d72-9cbd-ce50078c2b4c` durably started
05:43:35.483340Z and completed 05:45:51.502463Z. All 72 potential entries have
provisional opening candidates: 36 per arm. Original 31 issues/24,598 code-date
coordinates remain immutable; the conditional union has 4,604 coordinates and
34 merged calendar-day windows. This approximately 81% reduction is source-work
prioritization, conditional on separately reviewed complete event coverage.
It is not an actual exit/trade/return or an unconditional no-event finding.

| output | SHA-256 |
|---|---|
| specification.json | `640f6a01a94fd1b2d46f95a53a2144237d041fe4a32821ea93985635ef4faf57` |
| source-manifest.json | `d3062d710022d99c86864dd1aa0239cad2f8e570913edc94e65146ab38ad533c` |
| input-manifest.json | `33ec5ee0adca85b8b53bbaa7ad979484db399cb1654ec3acee4f28770448bbd6` |
| resource-manifest.json | `18139df477c2a43341d0a317ae72bc736bd8797fe5398708a8e3d4dd09be67db` |
| exit-scope.json | `e98683b885f81be54232439b04083ef8e6eff3ccf1af88b1c3c429a16c0d96d6` |
| result.json | `34cc5516c3469091915f9a7ad29c97a4f7e4fa8f4eaf65a377557c0f6b91a988` |

The independently reconstructed child scope hash is
`725dff94e0d30ebbd0d94b12a60a1435ffbf19dd06a85c8d7331dfd14571dc67`.
The 2,910-byte independent receipt, SHA-256
`957906bba586f4b327fc946de59eaa1877872af8b92f0eb936c91e370ae35468`,
is in the registered local `large-liquid-bm-exit-independent-20261010-v1` root.
Its scope is `new_conditional_exit_coordinates_and_original_state_links_only`:
six new files plus the four pinned references, with no original eighteen-input
reread, DB/API/source body or quote-value rehash. It explicitly relies on the
original independently verified BM typed states for candidate-open eligibility.
Original source truth, event/basis/vintage and actual exits remain uncertified.
Source review repaired missing success-report merge/specification comparisons
in the independent wrapper before actual invocation; 59 combined synthetic
checks passed. Verifier and wrapper source pins are respectively
`3c0c75ea5d0b7a3bd2a5021fa6bdeb8202432e6b268eda0243409ed9a58884bc` and
`bf5e6d85e538c3b243343c71910b9650a658e05e74c48f6e205f644f80625eeb`.

A separately registered one-file metadata projection then succeeded. Its source
`var/bm-exit-metadata.py` is pinned at
`415e33bc2bcd82a12adf70ab1ad14d59918c37e74cabc5eaf5c3209604e060c0`;
ten synthetic cases and root source review preceded access. The private local
`large-liquid-bm-exit-metadata-20261010-v1/stdout.json` is 19,748 bytes, SHA-256
`c9ddf371d5fbac2ef4108ab90481eee699642f1cf3fc89600ddf5866d7e98d5c`.
It projects only exact issue/entry/due/candidate/end coordinates, windows,
counts and four snapshot/scope identifiers. No price value or original source
body is in it. All operational/research checkout guards passed.

Of the five original abnormal runs, only LG's 2021 run intersects the new
provisional holding windows. The 009830/012450/091990/207940 later runs are after
their respective candidates. This is a conditional prioritization result; it
does not certify absence of earlier rights or erase the original full scope.
LG's child union is 2021-04-16 through 2021-10-22. The issuer's completion notice
establishes a relevant split question, while the earlier plan does not supply
final allotment/eligibility/delivery or adjusted-unit accounting evidence.

The operator explicitly selected original-investment grouping for spin-off
components. CLAUDE.md records the binding policy: one original slot, inherited
acquisition/due dates, separate post-due sales only when delivered/tradable,
undelivered rights retained. No new optional acquisition or future-event
exclusion from the historical formation pool. Source-only audit found the old
price-return/ex-dividend convention does not authorize discarding the new-company
right. Existing one-to-one exchange/final-cash primitives need a minimal extension.

Use exact provisional windows for the next issuer-history source workload.
Annual/half-year issuer histories may support an explicitly reviewed bounded
`inferred_no_compulsory_action` finding under the existing rules; a report label,
filing date, empty search or quote continuity alone cannot. No exhaustive daily
absence search or whole-vendor-formula proof is added. The current pure paired
seam has an intentionally limited all-no-event child path and refuses successor
quote expansion; supported spin-off integration must preserve the original
artifacts and connect separately pinned relevant bounds and coherent quotes.

Current package execution/independent check are complete. First-performance
groups remain three open and zero newly closed by this diagnostic; D1/D2 actual
performance studies remain zero/zero. Historical availability and vintage remain
assumed. Next work is relevant official holding-period history, authorized
grouped-right accounting, and the actual fixed paired study connection.

The first grouped-inventory foundation is implemented without changing the
frozen pilot: an optional original-investment ID has a distinct namespace from
legacy lot IDs; group components must share acquisition/due/snapshot terms.
Book slots and both cutoff/actual-fill capacity checks count investments.
Pending/partly sold groups retain one slot. The cutoff reads no current opening
or frozen state, and failed component sales invalidate reserved new targets
without reselection. A fresh read-only correctness review found no actionable
issue; its six related modules passed 194 synthetic cases. Root checks passed
124 group/replay/timing cases, 160 paired/exit/report cases and 54 documentation
checks. These are overlapping test sets, not an additive unique-test count.
The actual spin-off primitive, final evidence and coherent quote extension
remain separate work; no split allotment or actual portfolio result is claimed.

### Group foundation shipped; compulsory spin-off connected

PR #274 received substantive CodeRabbit APPROVED on exact head
`88c1879b72f60704b8f1d3463530ccdc53f27b39` at2026-10-10T06:49:32Z.
All eight CI checks and genuine CodeRabbit success preceded normal squash merge
`8296c7e992f7b5e15c0f6cf7299abe15d9e8b4dd` at06:51:52Z, with zero review threads.
Reviewed/merged trees equal `0bdc550a9ddb42870618c32f935f8fd72cf67ed0`.
The isolated research checkout reached that clean merge at06:53:05.457023Z.
The collector remained clean at its unchanged recorded operational commit;
no process, schedule, credential or database changed.

The continuation adds one compulsory two-component spin-off to the same
immutable book/replay. Both components inherit the original acquisition/due
dates and investment slot. Delivered components exit independently after due;
the remaining right retains its slot. Exact raw ratios and coherent raw/adjusted
unit bridges determine quantities. A separately referenced carry convention
partitions old value with an exact residual, without booking future availability
price levels. Observable valid marks clear carried value. Analytical fractional
shares retain the standing convention; net-asset weights are not market weights.

Fresh adversarial review reproduced two defects before shipping. Bounded local
Decimal aggregation now prevents a boundary-digit loss from pending NAV, without
changing global context or the existing share/price-product convention. An
eligible parent sale after rights attachment but before legal effect is now
explicitly rejected: the minimal conversion does not model detached rights and
must not silently discard them, defer a valid sale or invent market suspension.
Same-day effect precedes sales. Repeated IDs, collisions and ambiguous same-day
chains are refused. Both findings were independently rechecked after repair.

The reviewed-declaration decoder links exact spin-off terms, three basis roles
and retained/new issue identities to one coherent snapshot. Purchase cutoff and
record day remain distinct. Carry evidence strength is separately linked and
preserved. New-issue complete coverage and both retained/new availability anchors
become explicit quote requirements. The package caller still refuses expanded
requirements until a separately registered coherent artifact is supplied; new
series cannot inherit an old quote hash. Pure decoding is not that artifact.

### All34 bounded company-history leaves received

Each actual read had fixed byte pins, exact report/issuer/window/leaf coordinates,
limits and exclusive private outputs. The original34 targets were not reduced.
Concrete metadata parser repairs handled issuer-listing badges and delayed DART
child insertion; strict selected-leaf/ancestor/version checks remain. Unrelated
financial TOC extents stay uncertified. Eleven previously unrequested wrappers
were completed with11 requests, no retry/failure. Attachment-main refusals remain.

The first seven leaves received bounded explicit inferred-no-compulsory-action
content findings. The next batch stopped on a DART connection reset at original
request14 after13 bodies, with12 readable texts and one LG parser refusal.
The refusal was a Korean substring collision between a shareholder subject and
later exchange wording. A fresh parser preserves all selected-node/document/
heading/financial-sibling guards;19 synthetic cases pass. Its registered network0
LG derivation contains829 lines/44,574 bytes, SHA-256
`9a47a2271af48c8e425aa64437f6b9fd6b9761ad01c5cee05594e34fa51b43d0`.
Old failure/raw receipts remain immutable.

The no-response failed request received one separately registered manual second
attempt, followed only by13 never-attempted requests. This continuation completed
14 requests/responses/readable texts, zero failures/parser refusals/automatic
retries, without re-requesting the20 received leaves. Result SHA-256
`3cc85f41c9c86697ac3fd982e4418f279553f26be77c7bffaa50938c4ccd9208`, scope
`04c11f0c5f6a1bd0ca56e53baf598743c21677ca15aaa094a4f299ec29c5b623`.
All34 selected targets now have received/readable representations. Acquisition
completeness is distinct from complete action coverage; content review continues.

LG's official annual-history leaf receipt20220321001237/node5/dcm8485703,
offset33135/length68808 directly confirms the executed May1,2021 spin-off,
April30 allocation record, raw LG0.9115879/LX0.4420605 allocations per old share
and LX relisting May27. Raw SHA-256
`ec979e5cbe851efe59330779d3bd94d9efb3ae849017fb858c40879a9ef3d449`.
Source: https://dart.fss.or.kr/report/viewer.do?rcpNo=20220321001237&dcmNo=8485703&eleId=5&offset=33135&length=68808&dtd=dart3.xsd
This retrospective event evidence is not a formation-availability timestamp.
Custodian delivery and the final eligible purchase cutoff are not directly
attested by this leaf. The selected official LX guide independently supplies
May27 trading: https://kind.krx.co.kr/external/2021/05/26/000323/20210526001048/99334.htm

A concrete HYBE funding-right question was checked separately. Its official
April19,2021 securities-registration report records April16 ex-rights and
April19 allocation record: https://kind.krx.co.kr/external/2021/04/19/000094/20210419000163/10601.htm
Potential entries April16(D1)/April19(D2) are on/after ex-rights; exclusion from
that old subscription right is a dated source-based eligibility inference.
Purchase-date comparison to the later record day alone would wrongly grant it.
This finding does not authorize optional exercise or prove all-event absence.

First-performance status remains three open groups, zero newly closed; actual
completed D1/D2 studies remain zero/zero. Normal inputs, original holding-price
diagnosis, independent checks and source prioritization are complete. Next:
finish bounded content decisions, connect mixed per-entry requirements and a
coherent component quote artifact, then freeze/run the existing logged pair.
Historical publication/finality/vintage remain explicit assumptions/unknowns;
D2 sensitivity does not prove them. Institution contact is not a blocker.

### Received-history content decisions continued

The next12 received leaves were fully reviewed with exact issuer/window/node
coordinates and raw pins: 275,565 raw bytes and3,862 derived visible-text lines.
Eleven initially supported bounded inferred-no-compulsory-action findings.
Kakao initially remained unknown because its July1,2021 Melon separation did
not specify the recipient of the new shares in the selected annual-history leaf.
That initial decision is preserved rather than silently replaced.

Two narrowly selected official pages resolved the particular ownership question.
The corrected May27,2021 decision specifies a physical subsidiary split with all
new shares owned by Kakao:
https://kind.krx.co.kr/external/2021/05/27/000292/20210527000849/11345.htm
The February25,2022 AGM notice records the executed July1 physical split and
Kakao's acquisition of those shares, followed by the September1 subsidiary
merger replacement:
https://kind.krx.co.kr/external/2022/02/25/001588/20220225003185/00591.htm
Together with the selected completed annual history, this supports an inferred
no-compulsory-shareholder-allotment finding for the exact Kakao holding window.
The12 final interval findings are inferred, not complete-source certification;
the addendum identifies root's official-page review separately from the original
leaf reviewer. Decision SHA-256
`7c3a44cbae46b59095827044e9b727ae753e1ac8a231cb34f1a2e311dfc4ad60`.
The first7 and these12 intervals now have content findings; LG has a confirmed
relevant spin-off. The final14 received leaves remain in content review. This
does not close a first-performance group or increment D1/D2 completed studies.
