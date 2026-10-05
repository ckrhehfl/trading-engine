# Research Direction Task AB — record discovery before it reads data

## Discuss and plan

The operator authorized logging repair, a committed activity-portfolio
specification, and discovery on the spent post-2019 panel on 2026-10-05.
`runs/discovery_windows.json` records the concrete blocker: IC/event studies
do not have folds, so pretending they are `log_run` calls would fabricate
provenance. Existing `rd-*` history remains incomplete; this change does not
invent timestamps or results to backfill it.

## Implementation

`experiment_log.run_discovery_trial` accepts an evaluation callback and
declared study, specification hash, parameters and window. It writes and
fsyncs a `discovery_trial` start before invoking the callback. Completion
and failure append records with the same fresh run id; even KeyboardInterrupt
is recorded and re-raised. An abrupt process death leaves an unfinished start.
An unwritable log prevents the callback from reading any data. Error messages
are not persisted, only exception types, to avoid incidental sensitive text.

Discovery has its own record type rather than fake folds or a pretend holdout.
Existing promotion counters select `backtest_run` records and ignore it.
The API does not grant data access: each study must validate its actual loader
against the committed window and must not claim confirmation from discovery.
The existing single-writer assumption remains. No collector or trading code
is changed.

The pytest isolation fixture covers the new writer. Tests exercise the write
ordering, disk refusal before evaluation, failed/interrupted attempts, repeated
attempt identity, frozen input provenance, nonfinite results, and unchanged
promotion trial counts. Removing the start write makes the callback-order
test fail; skipping the distinct record type invalidates accounting.

## Adoption boundary

The activity sizing study uses this API. Old scripts are not silently converted
and the old hand-maintained discovery-window ledger remains a record of their
documented spends. Future studies must use the wrapper before their loaders;
the mere existence of a logger cannot force arbitrary research scripts to do so.

Verification on 2026-10-05: the logging change passed 26 focused tests and the
full suite (3,845 passed, 3 skipped), plus both guardrail suites. Temporarily
removing the durable start write made the callback-order regression fail as
expected; the write was restored immediately. The later activity pilot has
its own verification record in Task AC.
