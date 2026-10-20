# Research Direction Task CE — measure fixed intraday response horizons with enforced execution budgets

## 2026-10-20 — operator continuation and implementation begun

The operator directly approved the preceding concrete next-action proposal:
resolve the CPU-budget discrepancy, then compare five fixed response horizons
on the same saved observations. The report preceding that approval disclosed
negative assumed-cost means, incomplete inputs, recovery's power limitation
and the unresolved execution-budget issue. CLAUDE.md now records this new,
specific descriptive extension. This is not a general transfer of CD's waiver.

Fresh-context implementation tasks have begun for the minimal budget checker
and the response-curve caller with synthetic tests. A separate research-
integrity reviewer confirmed that the latest direct continuation supports
this bounded design, with every conditional power failure retained. No new
actual price response has been read to choose the following design.

### The fixed comparison

Use only the pinned successful CD report and the original CC minute archive.
Preserve both original mechanisms, event times, timing variants, membership,
peer selections, observation dates, costs and complete twenty-name target
matrix. D+1 and D+2 still concern monthly membership availability; neither
means a position held overnight for that number of days.

Measure exactly 5, 15, 30, 60 and 120 minutes from the SAME original entry-label
boundary. Use first-minute open to final-minute close, with complete valid
minute paths. No hindsight high/low exit, threshold search, new signal,
different stock selection, new source query or reserved date is included.

Fix the cohort to CD's ORIGINAL 120-minute complete pairs for every horizon.
Do not rescue a previously unknown pair because a shorter path exists. Keep
the original unknown signal/pair states and reasons visible, distinguishing
not measured outside the fixed cohort from an actual missing short-horizon
price. This cohort remains conditional on future data completeness; it is
not a historically executable inclusion rule or a coverage certificate.

The axis is response duration, with event and same-clock equal-weight peers
held fixed. Compute each horizon's own-label standard deviation and sizing
before its descriptive mean comparison. The new conditional sizing belongs
to this fixed cohort and must not be confused with CC's earlier larger
known-label population. Preserve every failed prerequisite under the current
one-extension authorization, alongside the upstream CC qualifications.

Report all horizons, including event/peer means, medians and positive fractions,
their fixed 33bp assumed-cost responses, peer-relative differences, monthly/
symbol breakdowns and dependence-aware error estimates. Keep exact prices and
per-pair rational arithmetic until the statistical aggregation boundary.
Retain whole-session circular five-session bootstrap blocks, two thousand
draws and seed zero, including zero-event reference dates; report corrected/
naive error ratios. No p-value, significance, horizon winner, portfolio PnL,
strategy pass, direction closure or promotion is licensed. A positive response
would still require a separately specified execution-aware candidate and the
existing confirmation checkpoints.

### Minimal reuse and execution budgets

CD's events and peer selections were already independently reconstructed from
raw inputs. Reuse those pinned upstream facts instead of rerunning its old
detectors, calibration or experiment. Stream the original archive once per
producer/verifier, retain one session's raw prices, and check its chunk vintages
against the pinned target rows. The new independent verifier reconstructs NEW
labels and aggregates without importing the producer. It does not recreate an
authentication claim for the original source or certify historical tradability.

The CPU limit remains a TOTAL 300 seconds for the whole execution pipeline;
do not silently double it by giving producer and verifier separate budgets.
The address-space limit remains 512MiB, wall limit 1,200 seconds and priority
nice 10, with no automatic retry. The budget checker records its PID, process-
time and getrusage baselines, absolute/delta usage and limiter readbacks, checks
hot loops and every phase, and refuses final success on a limit failure or
drift. Started/partial evidence remains durable if a kernel hard kill prevents
an orderly failure callback. RSS alone is not an address-space proof.

The [Python resource documentation](https://docs.python.org/3.12/library/resource.html)
distinguishes setting/readback of limits from measurement of self and child
usage. The [Linux limit manual](https://man7.org/linux/man-pages/man2/getrlimit.2.html)
describes process CPU limits and termination at the hard boundary. These
documents do not explain away CD's observed overrun. Its runtime conformance
remains unresolved until directly supported evidence is available; preserve
the original source, specification and receipt without a retrospective pass.

A bounded synthetic host diagnostic is prepared to check current limiter
behavior: one private child with a one-second CPU limit and a maximum
three-second synthetic CPU loop, a parent wall timeout and cleanup of only
that child. It reads no repository, market data, credential file, database,
canonical log or service configuration and changes no collector. Its result
can characterize current host behavior, not prove the historical CD limit.

The actual response study still requires exact committed registration,
separate final source/integrity reviews, required CI and normal merge. Use
the existing isolated GCP research checkout; preserve collector source and
processes. Started must be fsynced before saved actual evidence is read.
Freeze source/specification/runtime, use exclusive private outputs, preserve
failures and only the new canonical span, and verify actual outputs and their
receipt separately. A producer-completed record alone is not a verified,
budget-compliant publication.

At this entry code and synthetic verification are in progress. The host
diagnostic and CE response study have not run, and no new result is claimed.

## 2026-10-20 — bounded current-host diagnostic completed

The one prepared synthetic probe ran once through the existing strict SSH
transport. The child read back CPU soft/hard limits of one second, 64MiB
address space and nice 10. It exited by SIGKILL (-9) after measured child
user/system CPU 0.993142 + 0.009009 = 1.002151 seconds, with wall
1.002922 seconds and no parent timeout. The parent read back its unchanged
10-second CPU and 128MiB limits and nice 10; its self CPU was separate from
the child's. Current host kernel was `6.17.0-1022-gcp`, Python 3.12.3.

This directly supports current tiny-limit behavior, not the historical reason
for CD's reported 351.748990 seconds. No repository, source data, credential
file, database, API, canonical log or collector configuration was read or
modified by this probe. No remote file was created and no retry occurred.

Private probe-source SHA256:
`f2b491679f96bea72c1ff9aeac56570959e16eb7437a17639ff0dfa5b2f62db3`.
Private probe-receipt SHA256:
`1c56235d0aa362d5bc3271ee1c74a087628be5d7dcdc2a2604d61ddd9d819587`.

The new minimal shared budget checker passed 44 explicit synthetic/subprocess
tests in 2.43 seconds. Removing its final checkpoint made all three original
351.748990-second overrun counterexamples fail; after restoring the guard,
all 44 passed again. The horizon implementation and independent verifier are
still being finalized. None of these checks read actual new response labels.

## 2026-10-20 — implementation and synthetic verification complete; actual run pending

The fixed-cohort producer, shared budget guard, private publication wrapper
and producer-independent verifier are now implemented. One explicit WSL
Ubuntu-24.04 command ran their four synthetic test files together: **148 passed
in 27.73 seconds** (44 budget, 44 horizon, 18 publication and 42 independent
verification cases). The independent verifier's fixture is handwritten and
imports neither producer nor CD/CC/core code. Its tests include raw-price
tampering with recomputed hashes, original unknown preservation, exact
per-horizon responses, own-label dispersion, zero-session resampling and the
120-minute upstream anchor. The archive opens only once per verifier call.

Publication tests reject the original 351.748990-second overrun even if a
receipt labels itself successful, limit/readback drift, nonfinite usage,
transport failures and canonical-span mutation. They exercise the actual
remote publication code to check that substantive preparation and collector
identity checks precede the final shared-budget finish, and that an exhausted
budget cannot publish success. No test opens actual study data or the two
protected pre-existing untracked user files. Their contents remain untouched
and excluded from staging and local test/scanner scope.

The frozen private execution closure has these SHA256 identifiers:

| File | SHA256 |
| --- | --- |
| `var/ce-horizons-publication.py` | `c29ebb7885f867110921a9e00b325c6e65bb334c4a0337677c1c55c0d1991993` |
| `var/test_ce_horizons_publication.py` | `d053330c1423ed348d41aa021e814365e625e432cf938a26734c39b2600725a2` |
| `var/ce-horizons-independent.py` | `d9819a054abf26f27b6d908e2589edeeb8fb8dce8c980b8bbc741f2754952051` |
| `var/test_ce_horizons_independent.py` | `b035fb982fd74ef0766d28afc0d6a4ed24fc1734beab188ca270649b7e2528b7` |

This is a pre-execution source snapshot. Separate final whole-diff source and
research-integrity reviews, actual required CI results and normal merge remain
prerequisites to the one CE actual run. Its output is not yet measured. The
publication wrapper will preserve one new canonical started/completed span,
source/runtime pins, independent verification, stage and final budget receipts
and unchanged collector identity. A failed or killed run must remain failed;
the registration permits no automatic retry or retrospective limit increase.
