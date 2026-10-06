# Research Direction Task AK — six quotation anchors agree, without basis certification

Date: 2026-10-06. This records the one actual acquisition specified in Task AI
and implemented in Task AJ, after PR #230 merged. It is a quotation diagnostic,
not a portfolio replay, return comparison, sizing experiment or promotion.
Task AG's accounting input gates and Task AD's comparison-family stop remain.

## What ran and the narrow authorization

PR [#230](https://github.com/ckrhehfl/trading-engine/pull/230) merged as
`7f3ade586cad66671acdece14c374c0e7b75dde4` after CodeRabbit approval, passing
CI and operator approval. The isolated GCP research checkout was clean at that
commit. The CLI ran there once and exited zero:

| provenance | observed value |
|---|---|
| research checkout | `/home/minjun4897/research-checkouts/activity-discovery-20261005` |
| source database | `/home/minjun4897/trading-engine/python/data/var/krx_scan.sqlite3` |
| source panel checked by the CLI | 2019-01-02 .. 2026-09-18 |
| durable CLI start UTC | 2026-10-06T10:27:55.774421+00:00 |
| completed UTC | 2026-10-06T10:29:58.150586+00:00 |
| elapsed between those records | 122.376165 seconds |
| endpoint | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` |
| host | `https://openapivts.koreainvestment.com:29443` |
| result | `acquired_consistent_anchors`; `basis_certified: false` |

The operator explicitly approved reading **only `KIS_APP_KEY` and
`KIS_APP_SECRET`** from the collector's existing `.env` for this single fixed
six-date, 72-logical-call diagnostic. This was a one-time exception to Task AJ's
manual-entry step, not a new credential-loading policy or permission to rerun.
An in-memory launcher parsed only the two exact assignments without executing
the file, then passed their values through the child environment on GCP.
Values were not printed, copied locally, put in command arguments or persisted
in research evidence. No reusable credential helper was added to the repository.
The existing `KisSession` token-cache behavior was unchanged.

A sibling launch record was synced before the credential read; the CLI then
created its own start record before authentication or source-price access.
The launcher applied nice +15 and a 384 MiB address-space limit. All recorded
quotation events occurred during 19:27–19:29 KST, outside the protected session.
Collector HEAD remained `4ce85d714890b87f1a57ae89d4942660e41c0483`, clean before
and after. This is a checkout observation, not a claim to have measured every
collector process. No collector configuration, schedule or deployment was
changed. No reserved prices or order endpoints were accessed.

## Actual observations

The fixed order contained 36 negative controls, 12 positive controls, 12 target
responses and 12 target repeats. All 72 responses validated. Negative controls
were empty; positive controls returned exactly the requested date. Each target's
six normalized fields matched its repeat; each adjusted target matched its
archived counterpart in all six fields, including volume and turnover.

There were **102 quotation HTTP attempts for 72 logical calls**. Of the logical
calls, 48 used one attempt, 19 used two, four used three and one used four:
`48 + 19*2 + 4*3 + 1*4 = 102`. The 30 additional attempts are recorded, but their
upstream causes are not: the sanitized event log does not retain vendor error
messages. Authentication/token-cache traffic is outside these counts.

| code | exact day | adjusted close | raw close | observed close ratio |
|---|---|---:|---:|---:|
| 033660 | 2021-08-05 | 11,500 | 11,500 | 1 |
| 316140 | 2021-08-27 | 11,350 | 11,350 | 1 |
| 367480 | 2023-08-17 | 3,940 | 3,940 | 1 |
| 146060 | 2023-09-08 | 5,910 | 5,910 | 1 |
| 476470 | 2025-06-19 | 2,195 | 2,195 | 1 |
| 462310 | 2025-07-09 | 12,640 | 12,640 | 1 |

All six raw/adjusted pairs are equal in **all six fields**, not only close.
This is the observation, not permission to set all historical adjustment factors
to one. There is no independently established non-unit-factor control in this
matrix. Equal answers therefore cannot discriminate a genuinely unadjusted
anchor from an ignored basis flag. Repeats establish within-run consistency,
not independent price truth or a vendor conversion formula.

## Durable evidence and audit

The original four CLI files remain on GCP, outside both checkouts, at
`/home/minjun4897/research-evidence/activity-basis-20261006T102755Z-cf7e22f8/`.
No raw trading log or database was copied into this worktree. The sibling
`activity-basis-20261006T102755Z-cf7e22f8-launch.json` records the scoped approval
and source/collector state; `-launch-result.json` records exit zero and the
after-state. Neither contains credential values.

| hash scope | SHA-256 |
|---|---|
| canonical fixed plan | `d84bc7208db355938e7729ffaf4eebd6def4c15281fd1109f014589b9b9f8c27` |
| canonical six archived anchors | `c49228f44b834afea08b244246882a7143cac8b716f447eaa218be405a9a93af` |
| canonical twelve target quotations | `0c45bea09531f1d46e5961df351180988c89500d82a1cbcca61085fe8103962a` |
| `started.json` file bytes | `4701b2a75fecdcd196f9015af812e2e6a883113fe005399b0ac73fcdcce27eb2` |
| `archive.json` file bytes | `db5e59718a8cb6900aaf5b1f6354f10d0ffd37829f3bdc10e45585db396b4739` |
| `events.jsonl` file bytes | `03da91a58ca301a035bcf9c0d7e31fb4726a087b647d82ed68623009e06f70f6` |
| `result.json` file bytes | `ebe9b1b421022bc470d6ba7a3fe710f0b26743a16b92b72147d9fa8dd01d2f12` |

A separate read-only audit reconstructed the entire fixed request sequence
without importing the acquisition code. It recomputed the three canonical
hashes, checked all 246 event records in order, matched requests/attempts/
responses, checked the sequential counters and normalized values, and compared
the target/repeat/archive records. It also required the successful launch exit
and the absence of `failure.json` and `.result.pending`. All assertions passed.
This audits the stored evidence; it makes no new API or database request.

The audit source and output are preserved in the sibling directory
`activity-basis-20261006T102755Z-cf7e22f8-audit/`, as `audit.py` and `audit.json`.
Their SHA-256 values are respectively
`356c30442043289b95b9338ecf40ab93b113d0d46ef06e34d78da9223f3fb2d6` and
`b6363747798f2c40582a9f4aae5a9281438334b33ca93e56b802a69ca0a6637e`.
The acquisition code and evidence are traceable, but neither a file hash nor
this second audit constitutes independent verification of the vendor.

## What the official documentation establishes

Official KIS sources were retrieved at 2026-10-06T10:34:53.779331+00:00, pinned
to `koreainvestment/open-trading-api` commit
`277ec0eb7a9b7f63b6807829286c80f36649dad2`, and preserved under
`/home/minjun4897/research-evidence/activity-basis-vendor-docs-20261006/`.
`manifest.json` records four exact source URLs, local filenames and byte hashes;
its SHA-256 is `b54b12af8165a2b4a8783e93207d3ab12b099efb7f95cc8b34b7338cd704be00`.
Only public source files were fetched in this step, not additional quotations.

The [sample for the exact endpoint](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py)
documents `FID_ORG_ADJ_PRC=0` as adjusted and `1` as raw, consistent with this
client. It specifies up to 100 rows and uses TR ID `FHKST03010100` for both
environments. The [associated field mapping](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock/inquire_daily_itemchartprice/chk_inquire_daily_itemchartprice.py)
labels cumulative volume/turnover, ex-rights category, split ratio, changed flag
and revaluation reason. Those labels do not specify their numerical conventions.

A [different endpoint's sample](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock/inquire_daily_price/inquire_daily_price.py)
describes the same-named flag in the opposite direction. That is not evidence
to invert this endpoint's constants. Endpoint-specific documentation must stay
attached to the request it describes.

These inspected sources do not establish an event-by-event adjustment formula,
its normalization date or revision policy, factor units/direction/rounding,
volume/turnover transformation, or a cross-code share-exchange convention.
No claim is made that every vendor document was exhaustively searched. The
six-anchor hash is also **not** Task AD's full portfolio dataset hash, and no
new full-dataset reconciliation was performed here.

## Remaining gates and next work

The acquisition prerequisite is complete; **basis certification remains open**.
Do not instantiate Task AG's usable price bases from this result alone or rerun
the credentialed diagnostic under its consumed one-time approval.

Continue public evidence work on contemporaneous instrument intervals for the
full bar-eligible pool, including the 104 unresolved merger name candidates and
pre-conversion lookbacks. Audit corporate actions for all held intervals,
including closed and subsequently created successor lots. Task AI's eleven
identified pairs still need historical evidence availability; the Softcamp date
conflict and actual net liquidation payment evidence remain unresolved.

Before corrected sizing, independently establish vendor basis semantics and
reconcile the actual held-lot dataset, then complete Task AG's full input ledger,
new runner integration and reviewed preregistration. A new durable trial must
apply the existing cost-floor stop before any comparison family. This diagnostic
does not change the v1 result, conclude that the activity direction fails, or
authorize holdout access, paper trading or live trading.
