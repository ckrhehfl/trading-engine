# Research Direction Task AD — the sizing pilot stops before the comparison family

## What ran

One discovery dispersion pilot, under the committed
`configs/research/discovery/activity-calibration-v1.json`, after PR #223 passed
CodeRabbit review and all CI checks and merged. No comparison family, candidate
ranking, confirmation or promotion was run.

| provenance | value |
|---|---|
| durable start UTC | 2026-10-05T13:27:36.710489+00:00 |
| completed UTC | 2026-10-05T13:30:05.547010+00:00 |
| run id | `13adac55-56b3-460a-842d-7299105b5eca` |
| code | `cac80eb9db6e452305b1bb6f6581252e7123871a` |
| specification SHA-256 | `b344f20598d94d0f19a65edeb9394cc511f28318f872a5736d3a405835a79ff7` |
| dataset SHA-256 | `fc8a57eae72c33a84e31a3bc96b45110b1db2903c22c3c5b6fd3c67d0e2809e6` |
| declared price panel | 2019-01-02 .. 2026-09-18 |
| portfolio sessions | 1,834 |
| promotion-trial increment | 0 |

The dataset hash covers the loaded index calendar and streamed completed-code
price/turnover rows, in the runner's deterministic order. It is not a hash of
the whole SQLite file. Index dates through September 23 serve settlement only.
The append-only operational log is `runs/experiments.jsonl` on the GCP research
host; raw logs and databases are not copied into this checkout or published.
Read-back confirmed exactly one started and one completed record for this run,
with matching code/specification identities. The committed specification's
local SHA-256 independently matches the recorded hash.

## The sizing result

| measurement | result |
|---|---:|
| daily portfolio-return standard deviation | 0.015179892050066267 |
| iid session standard error | 0.0003544614563241631 |
| 63-session block SE | 0.00037851008218338 |
| 126-session block SE | 0.0003961278699904336 |
| 252-session block SE | 0.00043002275434881834 |
| largest block SE / iid SE | 1.2131721141368688 |
| normal-approximation daily effect at planned power | **10.692407681417903 bp** |
| modeled full-investment daily cost floor | **0.34555555555555556 bp** |
| `cost_floor_resolvable` | **false** |

The calculation uses the event arm's own dispersion, the largest of the three
registered block SEs, one-sided alpha 0.05 and planning power 0.8. The cost
comparator is the registered upper-bound 43.54 bp round trip divided by the
126-session holding period. It is a modeled amortization, not measured daily
cash costs. The detectable effect is about **31 times** that comparator.

This is a planning approximation, not an exact power guarantee or a significance
test. The longest blocks leave few effective blocks, and stale valuations
below also limit its interpretation. It does **not** establish that the activity
direction lacks an edge; it says this measurement cannot resolve an effect as
small as its modeled cost floor. No mean return, Sharpe or winning variant is
reported, and the direction is not declared closed.

## Accounting and data-quality findings

| diagnostic | count |
|---|---:|
| entries | 245 |
| closed trades | 237 |
| unresolved terminal holdings | **8** |
| invested sessions | 1,834 |
| delayed completed exits | 2 |
| unpriced/frozen holding **position-sessions** | 5,159 |
| pending-sale **position-sessions** | 4,408 |
| unfilled entry attempts, left as cash | 2 |
| limit-locked fill proxies | 1 |

Position-sessions count one held name on one session: 5,159 is not 5,159
independent dates. The eight unresolved holdings reconcile exactly to
245 entries minus 237 exits. Their last observed prices remain marks, not
liquidated cash or known recovery values. The operator-approved carry policy
was applied; it did not establish the realizability of those marks. Whether
each residual reflects a halt, a delisting or incomplete prices needs its own
diagnostic. The pilot did not output their identities or recovery claims.

The source scan still reports 3,043 done names, 434 never-served, 1,152 outside
the window, seven rejected and two transport failures. Instrument-type and
point-in-time market classification gaps remain. The KOSPI/KOSDAQ tax total is
an upper-bound model for possible KONEX names, not exact per-name taxation.
Dividends and queue availability at a daily limit are not established here.

## Verification and operational boundary

PR #223's final CI passed 3,868 Python tests with three existing skips; Java,
security and guardrail checks passed, as did all ten CodeRabbit pre-merge
checks. Its provenance finding was fixed, retested and resolved. The dedicated
GCP checkout separately passed 31 synthetic tests before the real run.

The real pilot took 149.25 seconds with peak child RSS 173,404 KiB, under a
384 MiB address-space cap and nice +15. The collector checkout remained at
`4ce85d7`. The post-run read-only progress check found 2,438 completed pre-2019
codes and a last-fetch timestamp of 2026-10-05T13:28:48.654071+00:00. Only
progress metadata was read from that reserved database, never its prices.

## The decision boundary

The committed sizing stop fired, so the comparison specification and run are
pending. The operator was offered two concrete choices on 2026-10-05:

1. Investigate the eight unresolved holdings and data/recovery quality first
   (recommended). This improves accounting but does not itself solve low power.
2. Authorize a narrowly scoped, descriptive comparison despite the sizing
   failure. This needs an explicit exception for this study to the cost-floor
   sizing rule; it cannot support an edge claim or any promotion, and the
   unresolved-valuation limitation would remain.

The choice is not assumed. No holdout access or permanent research-rule change
is implied by this report.
