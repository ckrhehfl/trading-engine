# Research Direction Task AG — accounting inputs before corrected sizing

## Scope and implementation status

The operator continued Task AF on 2026-10-06. This step specifies and tests
the corporate-action arithmetic and dated instrument-classification contract,
and inventories the existing input data. It does **not** run a corrected
portfolio or repeat the sizing calculation. `activity_portfolio` v1 and its
committed specifications remain unchanged so their recorded runs reproduce.

`research/activity_accounting.py` implements pure, synthetic-tested primitives.
It is not wired to v1 and is not yet a complete portfolio simulator. Its caller
must supply inspected evidence and a complete event ledger; a source string or
dataset hash checks provenance consistency, not the truth of a filing. Unknown
inputs must not be replaced with plausible defaults to make a replay run.

## Accounting contract

For a same-session price pair, define `f = adjusted_close / raw_close`.
Value conservation requires `raw_quantity = adjusted_quantity * f`.
For a mandatory cross-code stock exchange with raw-share ratio `r`:

```
successor_adjusted_quantity = old_adjusted_quantity * f_old * r / f_new
```

Both factors must come from the same identified price snapshot as the held lot.
The old basis must reconcile its last observable mark; the new basis is anchored
to the verified availability date. A raw ratio applied directly to adjusted
units is refused by requiring both explicit bases. Whether the vendor's
adjustment convention supports each bridge still needs independent verification.

The transition preserves the lot's acquisition date, due-sale date, identifier,
and marked value. Between effectiveness and availability its mark is the old
marked value divided by the new quantity, explicitly a carried valuation, not
an observed successor price. Its observation date stays unchanged. Observable
marks can resume only after availability and on non-frozen bars. Availability
does not establish an executable fill; the next runner must also apply the
daily-bar, due-date and missing/frozen/limit conditions from Task AC.

Keep separate acquisition lots if two positions acquire the same successor
code. Combining by code would discard a due date. Entry-slot limits count lots;
a conversion creates neither free cash nor a free slot. Preserve the pilot's
analytical fractional-share convention. This models fractional stock claims,
not broker rounding or the issuer's actual fractional cash payment. A claim
about an executable account would need starting capital and rounding rules.

Only a **verified compulsory final cash distribution** is supported: the issue
has ceased trading before the record date, eligibility is established, and both
actual payment date and net cash per public raw share are evidenced. Until that
date the lot remains inventory and cash increment is zero. On/after payment,
cash is `adjusted_quantity * f * net_cash_per_raw_share` and the lot is removed.
Passing the returned remaining inventory on subsequent sessions cannot pay it
twice. The caller must apply both outputs atomically to its research book.
There is no fabricated market-sale fee/tax on this already-net payment.

An appraisal option, proposed payment date or a gross amount of uncertain tax
basis cannot instantiate that distribution. `None` is not zero recovery;
zero is admitted only as an explicitly verified amount. Partial liquidations,
dividends and ex-distribution trading are outside this primitive's contract.
The two audit-opinion delistings and two ongoing suspensions in Task AF retain
unknown recovery and stale-valuation disclosure under the operator's choice.

## Historical eligibility contract

Membership/tradability still comes from a completed price fetch and that day's
bar under CLAUDE.md; the new classification contract does not replace it with
a future delisting date. Classification is a half-open effective interval,
with a separate date when its evidence became public. For **every bar-eligible
name before hash selection**, require exactly one interval known by formation.
No interval, overlapping intervals, or a current snapshot backdated into the
past raises an error. Unknown classification cannot quietly remove a casualty.

A SPAC-to-operating-company change under the same code therefore has distinct
intervals. Future failure does not retroactively remove a common-stock period.
Recent names are useful for diagnosing the cache, not certifying history.
The input ledger must cover the full candidate pool and all relevant periods;
fixing only the eight residuals would condition the correction on outcomes.

## Existing-data inventory

Read-only metadata inspection on 2026-10-06 found:

| existing input | observed state |
|---|---|
| scan panel | 2019-01-02 .. 2026-09-18; 3,043 done codes |
| live identity snapshots | 2026-09-14 .. 2026-10-05, 15 dates |
| delisted identity snapshots | 2026-09-21 .. 2026-10-05, 11 dates |
| raw equity rows in the main store | zero (`KRX-RAW:*`, all intervals) |
| successor 316140 / 우리금융지주 | done; 2019-02-13 .. 2026-09-18 |
| successor 146060 / 율촌 | done; 2023-09-08 .. 2026-09-18 |
| successor 462310 / 뉴키즈온 | done; 2025-07-09 .. 2026-09-18 |

Joining all done codes to the October 5 snapshots, with the existing common
stock filter and live `ST` condition, gives 2,826 passes, **212 SPAC labels,
two REIT labels, two other exclusions, and one conflicting identity**. These
sum to 3,043; they are contemporary labels, not historical membership counts.
The two other exclusions are foreign issues SBI핀테크솔루션즈 (`950110`) and
SNK (`950180`). The conflict is 수성웹툰 (`084180`), present in both live and
delisted snapshots with the same ISIN. Do not silently prefer either row.
Even the 2,826 passes may contain earlier SPAC periods under renamed codes.

`research.activity_readiness` makes this inventory repeatable. It reads only
scan completion, dated identity fields and counts of raw daily rows inside the
spent window; it never selects price values. It begins read transactions,
rejects a reserved/wrong panel before identity queries, hashes the metadata
inputs, and requires its transitive repository sources to be committed. Its
output deliberately contains no eligibility list, readiness verdict, returns
or power number. A metadata inventory is not another discovery return trial.

New official evidence corroborates successor `462310` and actual listing on
2025-07-09: the issuer's [July 16 ownership filing](https://kind.krx.co.kr/external/2025/07/16/000357/20250716000989/00634.htm).
Task AF's cited completed-event filings remain the basis for the three ratios.
No newly inspected source establishes the liquidation's net payment details;
the previously reported 2,182 won and announced September 2 date remain
unusable as a verified cash settlement.

## Readiness gates for the next real replay

1. Reconstruct contemporaneous instrument intervals for the full bar-eligible
   pool, including renamed SPACs, and resolve duplicate/missing identities.
   Document unresolved instrument-type uncertainty rather than excluding it.
2. Build a systematic corporate-action ledger for all held intervals, including
   closed lots and holdings created after earlier corrections. The eight known
   residuals are evidence to seed an audit, not proof of complete coverage.
3. Verify raw/adjusted basis bridges, successor availability, and qualifying
   share class; establish actual net cash/date before using any payout.
4. Integrate the primitives into a new lot-based runner, validate event ordering,
   slot/cash conservation, full calendar replay, and re-entry interactions.
   Precommit its specification and complete CodeRabbit review before prices run.
5. Log a new discovery sizing trial and its new dataset/evidence hashes. Apply
   the existing cost-floor stopping rule before any comparison family.

These are remaining data and integration work, not a conclusion that the
activity direction fails. The old 10.69 bp result remains specific to v1.
No pre-2019 prices were accessed; no collector checkout, schedule or credential
was changed. The collector stayed clean at `4ce85d7` during the initial audit.

## Verification and repeatable audit result

Local focused verification: **80 passed, one existing skip**, including v1
portfolio regressions and the planning index. Removing the `known_on` guard
made the historical-SPAC test fail (`DID NOT RAISE`); restoring it passed all
25 new tests. This checks the failure direction rather than just a happy path.
The inventory tests use databases with no price columns or `scan_bars` table,
compare file bytes before/after, and trace the reserved-panel refusal before
any identity query. A changed identity changes the metadata hash.

GCP executed the committed auditor at
`7324bf8fec3648fc40c554aea3a26ba7bfac4ac2` in the existing isolated research
checkout. **25 tests passed in 0.37 seconds** there. The resulting metadata hash
was `952df59dd96510c01115d33ed3c8156edfa2c2ba884fbf2d30e294158310bf8f`;
all counts above reproduced, including 217 exceptions and zero raw daily rows
inside the spent window. This is a metadata hash, not a price-dataset hash.
The collector HEAD and clean status matched before and after the command.
No new return trial or experiment-log entry was created.
