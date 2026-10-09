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

## Completed publication and actual preflight — 2026-10-09 UTC

PR #265 merged normally as `aee4e38638061301c2a14e756245680a7a8ace88`.
CodeRabbit approved final head `3ef8cee349c6f00563c9fbd1446a62aeaae9d525`
with no actionable findings or unresolved threads. All nine checks passed;
both full Python CI runs recorded 5,022 passed and three skipped. The merged
tree matched the reviewed tree. The isolated GCP research checkout was
fast-forwarded to the merge; the clean collector stayed at
`4ce85d714890b87f1a57ae89d4942660e41c0483`, with no process/schedule changes.

Run `c8d66720-a8d9-464b-8d2c-b5cff0fd9ef3` started durably at
`2026-10-09T14:07:27.531980+00:00` and completed at
`2026-10-09T14:07:41.476082+00:00` in the registered private GCP root.
All four saved inputs matched their pins. One readonly snapshot consumed the
registered 53,526 code/dates across 133 codes: 53,435 observed and 91 frozen.
No missing/NULL/invalid/state-date drift was found. The 876 BH unresolved
windows partition into 839 exact activity failures, one frozen-formation
failure and 36 numeric-pass potential windows across 31 codes. Input-unresolved
windows are zero. All prior 41 BH failures and the 27 controls are preserved.

Result SHA-256 is
`b011e43ff4c34c26e8f6e66b216b202b1f1b06c340abc9e4d1340b6393729dc3`;
source manifest `93b642db2c575b2046e3e7eb22921dc9abaef9b1db96d10320248900c59b2f8c`;
input manifest `7e328048f3bd4ab55ecb93fd825fab1485ab6731a2d0d8c02dae0389681e3a0a`;
read scope `88dbd977017c957b2e2e197bb9254aac2e72852cb4b9da8c33036e677ab63c38`;
specification `fd682a1619d46c6870c983b68376b8b71a8010e70703cab7e057228684ddc875`.

An independent verifier read only the five new output files, not original
source bodies, DB, credentials or historical experiment logs. It reproduced
exact medians/products, date/state partitions, metadata cutoffs, all summary
counts and paired schedules including the 126-session distance. It could check
fingerprint consistency but could not independently recompute the typed raw
SQLite/OHLC digest because OHLC was deliberately not retained. Exact turnover
stays only in the private GCP package; no raw DB/OHLC was copied locally.

These are signal-scope failures, not certified eligibility or returns. All four
full-performance work groups remain partly open; neither arm has a completed
performance run. BJ focuses on only the potential windows and preserves the
full source population. Actual publication time/finality/vintage are unknown;
modeled next-session availability remains an assumption.
