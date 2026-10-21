# Research Direction Task CI — diagnose fixed signal paths and entry-delay opportunities

## 2026-10-21 — operator request, performed intake and implementation begun

The operator requested the next diagnostic after CH: whether a cost-sized
movement occurred after the existing signals and whether the fixed entry was
late, with implementation/verification/review/merge/isolated GCP reflection
delegated unless a material research choice is needed. Actual Git intake
found the normal CH merge and only the two pre-existing protected untracked
operator files; neither file's contents were accessed. No new market-evidence
read has occurred at this design entry.

### Fixed population and evidence

Reuse CH's original selected-complete event masks and full20-by257 target
matrices in both membership timing variants. D+1/D+2 delay monthly universe
information, not position holding. Selected counts remain continuation787,
recovery377 in D+1 and785/376 in D+2. Keep continuation5/15/30/60/120-minute
and recovery5/15/30-minute ranges; do not reopen the deferred recovery60/120
performance cells or rescue an original unknown using a shorter path.

Read only the verified CH report and the original CC archive:

- CH `/home/minjun4897/research-evidence/intraday-ch-entry-strength-comparison-20261021-v1/report.json`,
  SHA256`f758e48e7aa0431f9dc2ea1cb50f8925bfbd8588669fb99a50c87ca334fbf028`.
- Archive `/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz`,
  compressed SHA256`4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de`,
  stream SHA256`d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da`.

CH supplies previously verified events, selection and terminal label anchors.
Do not rerun its detector, selection, peer choice or return comparison. The
new standalone verifier reconstructs the new paths directly from the supplied
archive. This does not certify vendor history, historical eligibility or fills.
No new database, API, credential, observation date or reserved input is needed.

### Three clock phases and two clearly named reference prices

Let S be original signal-bar end, D original decision/assumed availability,
and E the original first five-minute boundary strictly after D. Existing
assumptions retain D=S+two minutes. Signal reference price is the close in
minute[S-1,S); it is known only at D and is not a fill at S. Entry reference
is the open in minute[E,E+1). Diagnose:

1. [S,D): movement before the signal is assumed available.
2. [D,E): movement during the remaining wait after availability.
3. [E,E+h): movement after the original entry, at every fixed horizon h.

Entry delay after signal end and after availability is clock arithmetic.
Signal-reference movement before D cannot demonstrate that the algorithm
missed a tradable opportunity. A touch in [D,E) can identify overlap with the
extra wait, not prove an earlier entry was available/fillable or profitable.
No counterfactual earlier-entry returns or optimized delay are calculated.

### Hindsight path measurements, fixed before access

For each selected event and post-entry window, require every minute to be
valid. Maximum high/entry-open minus one is hindsight MFE; minimum low/entry-
open minus one is MAE. Record earliest extreme buckets, terminal price change
and MFE minus terminal change as giveback. The terminal change must reproduce
CH exactly. A missing selected post-entry path or signal-close anchor
contradicts the pinned known cohort and refuses execution, rather than dropping
events. High/low envelopes are not realizable exits or a profit curve.

Use the unchanged33bp planning value. Strictly exceeding entry-open times
(one plus33bp) is a favorable cost-sized move; exact equality is not above
cost. A strict low below entry-open times(one minus33bp) is a symmetric adverse
movement diagnostic, not a stop rule. Record first crossing minute buckets.
If up/down first crossings share a bucket, their order is unknown; OHLC cannot
resolve it. Classify up-only, down-only, up-before-down, down-before-up,
same-minute-order-unknown and neither. Count favorable crossings followed by
a terminal response no greater than cost; do not present them as target exits.

Separately use the same signal-close reference for first favorable crossing
over [S,E+h). Classify first crossing before D, during[D,E), after E or none
only if the entire pre-entry path is known. Pre-entry [S,E) lies outside the
old terminal label and can genuinely be missing. Any missing pre-entry bucket
leaves that first-crossing classification unknown, even if a post-entry touch
is visible. Keep all original selected events and independently known
post-entry paths. Missing pre-entry prices never become zero or remove events.
Full signal-reference extrema require the complete pre-entry path.

Report cases with a signal-reference pre-entry crossing but no entry-reference
post-entry crossing, split between before-availability and remaining-wait
phases. The reference prices differ; these cases do not calculate an earlier
strategy's profit. Preserve that qualification beside the result. No
high/low order or precise crossing instant is inferred inside a minute.

Summary counts/fractions, extreme mean/median/min/max, giveback and crossing
times characterize observed opportunities; they do not estimate an optimized
strategy's expected payoff. Retain month/symbol descriptions and denominator
coverage. Do not subtract cost from hindsight MFE and label the result PnL.
No new peer-path performance contrast, p-value, winner, optimal threshold,
exit, earlier-entry policy or directional-payoff family is specified. The
ordinary own-label sizing rule still governs any later entry/exit comparison;
it is not applied to turn this nonnegative-by-construction magnitude into an
edge test or silently waived. Source/calendar/cost/availability/price-basis
and original future-complete-cohort qualifications remain.

### Metadata, bounded execution and final gates

Keep the original target status, unknown reasons, selection/cohort state and
peer identities even where diagnostics are absent. Preserve raw chunk
observation_date, retrieved_at, source, unknown finality, assumed evidence
level and exact data_vintage. Assumed future path availability is window end
plus two minutes; it is a retrospective label, never an input at selection.
Distinguish it from the original decision and query retrieval timestamp.

New exclusive output:
`/home/minjun4897/research-evidence/intraday-ci-signal-paths-20261021-v1`.
Register exact source/spec/runtime/private pins and fsync canonical start
before CH/archive access. Bound the same5,158 chunks/1,002,211 rows and257
reference sessions; reuse the existing bounded stream parser. Cache identical
per-session event windows across timing variants without omitting checks.
One shared CPU300s/AS512MiB/wall1,200s/nice10 budget covers producer,
standalone verification and final substantive publication; no retry/reset.
Read only the named saved evidence, preserve the new canonical span alone,
and leave collector source/processes/schedules, credentials and trading intact.
Distinct final source/integrity reviews, required actual CI and normal merge
precede the one GCP execution. This entry records work begun, not an actual
result, closed research direction or trading authorization.

## 2026-10-21 — synthetic implementation and private execution closure

Fresh-context implementation produced the registered specification,
`intraday_signal_paths` module and its named synthetic tests. A distinct
fresh-context worker produced the stdlib-only independent path verifier and
hand-written package/mutation tests without importing a research producer.
The main agent adapted the existing reviewed single-input/archive publication
pattern. Its source manifest contains exactly14 files, including the new
specification/module and CH/CG/horizon dependency closure. Frozen prior helpers
were read for reuse and were not edited.

Author verification in this worktree's WSL Ubuntu-24.04 environment:

```text
python/.venv/bin/python -B -m pytest -q
  python/tests/test_intraday_signal_paths.py
  python/tests/test_planning_index.py
  python/tests/test_docs_do_not_duplicate_claude_md.py
  var/test_ci_signal_paths_independent.py
  var/test_ci_signal_paths_publication.py
219 passed, 1 skipped in24.99s
```

These are explicit named synthetic/document checks, not a full local suite or
repository-wide scan. The two protected untracked operator files were not
read, imported, tested or staged. No actual archive/report, API, GCP process,
collector or reserved input was accessed by these checks. A synthetic size
projection suggests the registered report bound is adequate; this is not an
actual memory/runtime certificate. The shared unchanged execution budget is
still required and cannot be reset after a partial read.

Private helpers remain ignored local execution material and are included in
the final review scope by their exact content hashes:

| file | SHA256 |
|---|---|
| `var/ci-signal-paths-publication.py` | `61926f5e65bb9648ab487997153c7f59552a7ca735e811a4867980e328035eec` |
| `var/test_ci_signal_paths_publication.py` | `73f3e864a2690432a42c6b5edb3505054d31d9552c1bc8762e2aa1ef0ccc9a82` |
| `var/ci-signal-paths-independent.py` | `cfc818d4a6790a6733de92667022148a296c03e4dd52b1b186a2e4aa00353477` |
| `var/test_ci_signal_paths_independent.py` | `b28aee5a8feea536bdfbef8239524af04ea6c193c63625f5bf8b3f06a197e112` |

The publication wrapper requires local reviewed-merge identity and canonical
source blobs, strict existing SSH identity, unchanged clean collector source,
exclusive output and a durable start before input access. One budget covers
production, producer-free raw verification, source/input/report rechecks,
canonical-span extraction and substantive publication preparation. Its final
receipt must conform to measured CPU/AS/wall/readback limits. No retries,
whole old journal copies, source/runtime authentication by declaration alone,
or new entry/exit policy returns are permitted. Final distinct source and
research-integrity reviews, actual required CI and normal merge remain pending
at this snapshot; no CI actual evidence has been read.
