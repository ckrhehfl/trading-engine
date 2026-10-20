# Research Direction Task CD — compare two intraday price responses with same-clock peers

## 2026-10-20 — approved limited exploration and implementation begun

The operator explicitly selected option 1 after CC's actual calibration:
continue one limited exploratory comparison of both patterns despite the
incomplete observed target matrix and recovery's own-arm power shortfall.
This is a new, specific decision, now recorded in CLAUDE.md; older daily
study exceptions are not its authority. No CD outcome has been read while
choosing this design. The fresh-context author is implementing the caller
and synthetic tests; a separate author is preparing the raw-output verifier.

### Fixed question and inputs

For each previously defined first signal, does the subsequent two-hour price
change exceed that of other observed dated target names at the same clock?
Evaluate opening-range continuation and post-drop recovery separately. The
axis is signal-name response versus equal-weight same-clock observed peers;
keep CC's universe inputs, observation window, event parameters, availability
lag, decision cutoff and price-label horizon fixed. This is a comparison run,
not candidate evaluation or a search over thresholds, horizons or costs.

Reuse only CC's saved report and original minute archive. No new database
query, API request, credential access, reserved date or collector change is
needed. Pin the CC report, compressed archive and decompressed stream before
execution. Recompute existing event/status/label information from those saved
bytes and check it against the pinned CC report. Keep every original dated
twenty-name record for each timing variant, including unknown signals.

D+1 and D+2 concern when the monthly membership snapshot is assumed available
for selection. Both use the same intraday two-hour price-label horizon. They
are not one-day and two-day holding periods. None of the source-labelled
membership, calendar, minute-stamp, availability or price-basis assumptions
becomes certified through this comparison.

### Controls chosen before their outcomes

At an event's decision time, preselect all OTHER members of that variant and
session with a contiguous valid completed five-minute prefix through that
decision's signal-bar end. Their bars must be available by the event decision.
Do not require the peers to lack a signal, nor inspect their future labels
when selecting them. This answers a same-clock observed-peer question, not
a causal treatment-effect or a full unobserved-market counterfactual.

Use the event's exact entry-label boundary and two-hour horizon for all selected
peers. Require a known event label and known labels for ALL preselected peers
to form a complete pair. No peers, a missing event label or any missing peer
label leaves the pair unknown. Preserve the selected names and missing reasons;
do not replace a missing peer, average only future-complete peers, or turn a
missing response into zero. Pair completeness itself is a future-data condition:
reported pair statistics are explicitly conditional, and no selection-bias
certificate is claimed. A known event survives later gaps.

### Fixed descriptive reporting

Report signal, known-no-signal and unknown target counts, preselected peer
counts, complete/unknown pairs and their reasons. For complete pairs, report
event and equal-weight-peer price responses, their difference, medians and
positive fractions. Deduct the same fixed 33bp planning assumption from each
arm; this is an assumed-cost price response, not verified historical net
return, executable fills, a self-financing portfolio or total asset accounting.
The identical cost deduction cancels in the paired difference.

Show monthly and symbol contributions without selecting a favourable subset.
Use all reference sessions, including zero-event days, for the fixed circular
five-session block bootstrap with two thousand draws and seed zero. Report
naive, session-cluster and bootstrap standard errors and corrected-to-naive
ratio. Overlapping events, common market moves and repeated stocks prevent
interpreting event count as independent trades. No p-value, significance,
portfolio Sharpe, compounded wealth, drawdown or frequency-as-fills claim is
authorized by these sampled responses. No strategy may be promoted, sent to
reserved confirmation data, closed or called profitable from this run.

The recovery sizing shortfall remains visible. Both families also retain
unknown input, eligibility, revision, liquidity, calendar, availability and
price-basis qualifications. An attractive mean would justify a specific next
execution-aware investigation; it would not establish an investable edge.

### Execution and verification boundary

Stream one original session's union of names at a time. Register and fsync
canonical started before saved actual-evidence reads; preserve exclusive,
private, durable outputs and failures with no automatic retry. Freeze the
exact specification, source and runtime before completion. The parent uses
the existing isolated GCP research checkout and leaves the collector checkout
and loaded processes unchanged. Preserve and hash only the new canonical
started/completed span, never copy the historical experiment log.

Source review and research-integrity review cover the final committed whole
diff and private execution closure. The four required CI checks and normal
merge precede actual execution. A separately authored frozen verifier replays
saved raw bytes and independently checks events, causal peer selection, exact
labels, missing states and reported arithmetic without importing the producer
or detector. That checks supplied bytes and calculations, not extraction
truth, historical tradability or achievable returns. A separate receipt review
then checks result interpretation against the actual evidence.

At this entry implementation is in progress; no CD comparison, review, CI,
merge, deployment or actual-output verification has completed.

## 2026-10-20 — implementation and synthetic verification completed

The implementation follows the fixed design above and validates the exact new
JSON registration without reading actual evidence. It retains CC's own-arm
sizing figures in each overall family result. Individual prices and event/peer
ratios remain exact; aggregate means cross the statistical boundary using
stable floating-point summation. This avoids unbounded denominator growth
when many distinct exact ratios are combined. No threshold or outcome was
examined to choose that engineering change.

The author's explicit new module tests passed **44 cases in 8.68 seconds**.
The parent ran the new comparison, existing calibration and event primitives,
planning index, document-consistency and private transport tests together:
**247 passed, one skipped, in 21.63 seconds**. An earlier attempted command
named a nonexistent handoff-test file and ran no tests; it was corrected to
the existing explicit document test, rather than reported as a pass.

The separately authored verifier uses no producer/core import and reconstructs
the CC raw rows, event/status/label metadata and calibration summaries before
checking every CD peer selection, label, missing state and summary. It reuses
verified report references to limit memory, not the producer's computations.
Its **50 synthetic tests passed in 13.20 seconds**, including hand-computed
peer differences, future-gap retention, absent peers, and semantic mutations
whose outer hashes were recomputed. The separate source reviewer also ran the
three new explicit test files: **101 passed in 26.50 seconds**. Those are
synthetic verification facts, not an actual performance result.

The private execution closure is frozen for final whole-diff review:

- Wrapper SHA256:
  `958acd3b95eb9287197665601981a66701316a4033a37a0dd96b5ce9c1f1fda7`.
- Wrapper-test SHA256:
  `7c2665433caf7ceb9d1606fa2ad85cb142d49fd582247440cb100ae7418b41e4`.
- Independent-verifier SHA256:
  `ab5a5b835c64600609651aeee778db14a353e94e9c0f8e9a778fdd89068546b4`.
- Verifier-test SHA256:
  `bf12a0e958beeeb48a73ff978f78c5e66a2c7633cfc69ff6c7dd08567f597a79`.

The wrapper authenticates report source/specification/runtime declarations
against its frozen actual closure; the bytes-only verifier cannot do that by
itself. It verifies the pinned CC membership metadata and causal clocks,
without independently authenticating the original CB source. These boundaries
are retained. Final exact-commit source/integrity reviews, required CI, normal
merge, isolated GCP reflection, one actual comparison and independent actual
output verification remain pending at this source snapshot.

## 2026-10-20 — actual comparison and arithmetic verification completed

PR #321 merged normally as `d1bc65e1959886300d5ce3c49493fc95d60e56b4`
after both separate final reviews, all eight push/PR checks and a final
zero-unresolved-thread query. Both full Python suites passed 8,728 cases with
three skips. The isolated GCP checkout ran the registered comparison once:
`471dde66-9388-4ea6-89cf-88489bded4b7`. The collector stayed clean at `4ce85d7`.

Frozen producer-independent raw replay passed. A separate receipt-only review
passed 2,099 arithmetic/lineage checks, with maximum floating-point difference
1.73e-18. It did not rehash absent remote evidence or replay the raw archive;
that is distinct from the frozen verifier's actual GCP execution. The durable
complete record, including limitations and the resource finding, is
https://github.com/ckrhehfl/trading-engine/pull/321#issuecomment-6292947296.

Complete pairs were 884/881 for continuation and 1,109/1,106 for recovery
at D+1/D+2. Continuation's assumed-cost mean was -40.036475/-40.117082bp,
with peer-relative mean +1.322632/+1.227352bp. Recovery's assumed-cost mean
was -32.130400/-31.997087bp, peer-relative -6.484998/-6.427806bp. These are
conditional sampled two-hour price responses, not realized portfolio PnL.
No winner, strategy pass, direction closure or promotion was established.

Report SHA256:
`830b69ddbf94fe3ee9297912df381e72c55463fc92ddbe5b4c296ea24743846a`.
Receipt SHA256:
`51c32e4033733b60d62878a5b4865a9df16bf3e232c16415a78d337c191d3963`.
The new canonical span has two records and 8,158 bytes, offsets
5,508,948–5,517,106, SHA256
`a795c5fc456053aeb99e10b3dd7abf413d9bd8df9f10406636250abb99109605`.

Runtime conformance is NOT certified: reported CPU 351.748990 seconds
exceeds the registered 300 seconds by 51.748990 seconds (17.249663%). The
wrapper configured RLIMIT_CPU but did not retain readback or final budget
assertion. The reason it completed beyond that reported limit is unconfirmed.
Wall was 355.134352 seconds and peak RSS 342,500KiB; RSS does not certify
address space. Preserve this run without retry or retrospective limit change.
Task CE begins budget enforcement/readback/postflight repair before its new
fixed-horizon study, under the operator's subsequent direct continuation.
