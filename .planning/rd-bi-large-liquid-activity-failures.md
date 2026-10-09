# Research Direction Task BI — narrow historical evidence to possible activity signals

## Decision and scope before actual reads

The operator authorized the next numeric filtering step after BH's saved-input
preflight. CLAUDE.md's Task BI design was written before implementation; this
document preserves that design as work starts. This is a non-promotable Discovery
input/signal-scope preflight, not an eligible-stock universe, portfolio or return
trial. The existing AN/AQ/calibration parameters and D+1/D+2 policies stay fixed.

BH has 876 unresolved issue/formation windows across 133 codes, after 41 of its
917 windows failed exact source liquidity or verified short history. Calculate
the activity predicate before acquiring every window's detailed issuer history.
For a continuous operating period reaching formation, a start no later than the
oldest of the latest 60 complete normal observations preserves that baseline;
a later start has too few observations. Therefore a numeric failure is a valid
one-way exclusion. A numeric pass cannot certify identity, operating period,
historical source vintage, tradability or profitability.

This proof requires every intervening index session. Unknown missing/NULL/invalid
observations cannot be skipped: they could change the latest-60 median and turn
a failure into a pass. Distinguish frozen equal-OHLC/zero-turnover observations,
positive locked bars, actual nonflat zero, and unresolved causes. Use the same
KIS quote/activity turnover and Decimal state/predicate as the later selector;
do not substitute raw KRX ACC_TRDVAL or a different adjusted vintage. Renames,
market transfers and document versions do not reset operating periods.

## Fixed executable and input registration

Committed spec: `configs/research/discovery/activity-failure-preflight-v1.json`.
Execution: `python -m research.activity_failure_preflight`, from the reviewed
clean repository root. Freeze all tracked Python, interpreter identity,
`python/pyproject.toml`, `python/uv.lock`, this spec and the calibration spec.
No unused installed third-party distribution becomes an extra execution gate.

Read only four pinned saved JSON files declared in the spec: calendar, BB
inventory, BH result and AU metadata. AU supplies prior normal/frozen dates and
formation state; BB combines them into intervals. Check exact `(formation,code)`
population joins, prior dispositions, all calendar dates in each BB interval,
and AU's complete date/state partition before reading any SQLite values.

The spent SQLite path is
`/home/minjun4897/trading-engine/python/data/var/krx_scan.sqlite3`.
Read only BH-unresolved BB intervals, from `inventory_start` through formation,
expanded to the complete pinned index calendar; record the exact code/date union
in `read-scope.json` before database access. Validate the exact spent panel and
all requested complete code progress first in one read-only transaction. Then
read only `bsop_date,open,high,low,close,turnover` for that union, never volume,
out-of-scope dates, holding prices, reserved data or a whole DB file hash/copy.
Do not extend intervals when 60 valid observations cannot be reproduced.

Persist typed consumed-row fingerprint, progress metadata, row states and seven
provenance fields. Retain exact consumed turnover strings only in this private
GCP output so an independent verifier can reconstruct medians/products; retain
no raw OHLC values and do not copy these rows to a local/public checkout.
Modeled availability is the following index session at 08:30
KST; actual historical public time, row retrieval, original vintage and finality
remain unknown. A snapshot receipt/hash identifies this read, not a historical
vintage. Check consumed metadata against both arms' formation selection cutoffs.
Unknowns or disagreement with AU/BB remain unresolved and cannot remove windows.

## Execution, verification and honest limits

Log durable started before actual saved bodies/SQLite access to the existing
canonical `/home/minjun4897/trading-engine/runs/experiments.jsonl`. Reject log,
input, source and output aliases before writes. After CodeRabbit, required CI
and merge, fast-forward only the isolated research checkout and run once into a
fresh mode-0700 parent and exclusive child:
`/home/minjun4897/research-evidence/large-liquid-bi-activity-failures-20261009-v1/run`.
Preserve failures and partial manifests; no automatic retry/scope expansion.
Independent verification reads only this new output package and new log tail
after its captured offset, never old experiment history or the source DB.

Tests cover exact Decimal boundaries, actual zeros/frozen/locked, unknowns,
late availability, complete interval and state drift, read-only exact SQL scope,
panel/progress-first refusal, pin failures and durable failed trial preservation.
The default BH runner remains unchanged apart from a reusable optional spec
validator on its engineering source freezer.

Report proven failures separately from numeric survivors and unresolved input
windows. Preserve all 917 BH dispositions and the original 27 controls by
reference; no claim of a complete universe is made. Holding/action denominators
remain undefined, and both performance counts remain zero. Four full-performance
work groups stay partially open: eligibility/normal periods, consequential
price/actions, actual replay integration, final frozen logged performance run.
This step narrows their workload; it does not automatically close a work group.
There is no new official historical publication fact or inquiry requirement.
