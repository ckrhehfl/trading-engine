# Research Direction Task CM — calibrate one fixed take-profit stop-loss and time exit rule

## 2026-10-21 — operator request, fixed rule and implementation begun

The operator requested preserving the original three-minute entry wait,
symbols and signals, fixing one take-profit/stop-loss/time rule before its
comparison, and completing experiment/verification/review/merge/isolated GCP
reflection unless a material decision is needed. Intake verified normal CL
merge and clean tracked source; the two protected untracked files remain
outside content reads/imports/tests/scans/edits/staging/deletion. CL's appended
ledger and PR completion record preserve its results. No new CM actual input
has been opened or management-policy outcome calculated/read at registration.

### One fixed research policy and control

The rule is fixed before outcomes, without a threshold sweep: gross return
from the original entry open at least+1% triggers take profit; at most-0.5%
triggers stop loss; maximum holding is30 minutes. These are experimental
price-label parameters, not changes to live/account risk parameters. A simple
fixed asymmetric target/invalidation tests whether earlier profit/loss exits
help the current entry; it is not an optimized setting or claimed optimum.
The cap is a common previously qualified horizon for both patterns. The known
same-data discovery history is retained; this is not independent confirmation.

Keep CH's original selected-complete first-event masks, original causal peers,
full twenty-by257 target matrix per membership timing variant, original unknowns,
source/vintage metadata and future-complete-cohort restriction. Counts remain
continuation787/recovery377 in D+1 and785/376 in D+2. No new symbol/signal,
reselection, short-horizon rescue, exposure allocation or entry change occurs.
D+1/D+2 concern universe-information timing, not holding days. The window remains
the spent257 intraday sessions2025-09-04..2026-09-23.

For signal end S, original decision D=S+two minutes under the existing assumed
availability lag. Entry E remains the strict next five-minute boundary, with
E-D=three minutes, using the original entry-minute open. Time cap X=E+30minutes.
The time-only control exits at the open of minute[X,X+one minute). The managed
policy uses exactly that same open/time when no earlier executable trigger
occurs. Preserve the original CH30 endpoint close in[X-one minute,X) as an
explicit bridge. It must reproduce the pinned CH return exactly; do not silently
treat that old close and the new common open-price time exit as identical.

Both new arms need their own label dispersion: old CH close-price sizing is
not the new time-only open-price arm's sizing. The common new open-price control
separates management effects from a change in time-exit price convention. The
final comparison must report the old-close/new-time-open bridge as a separate
descriptive reconciliation, not quietly attribute it to management.

### Trigger, information and execution clocks

Monitor completed one-minute CLOSE prices only, not hindsight high/low touches.
For close timestamp T>E, the assumed price availability and decision are T+two
minutes. Schedule the fill at F=T+three minutes, the next minute after that
decision. Only triggers with F<X qualify for an earlier exit. Thus eligible
close times run E+one minute through X-four minutes; an execution tied with X
uses the preplanned time exit. First eligible trigger wins; later price changes
while that exit is pending do not replace it. A single close cannot meet the
positive target and negative stop simultaneously.

Use the actual observed open of minute[F,F+one minute), never the threshold
price or the triggering close as an assumed fill. The eventual price may
overshoot or reverse; this is not a guarantee of a maximum loss or profit.
The time cap is known from original D and does not use a future price. The
entry-price observation is conservatively modeled available at E+three minutes
(entry minute end plus the same lag), no later than the first trigger decision.
Enforce entry/trigger available_at<=decision_at<execution_at. An outcome using
an exit-minute open is modeled available at exit+three minutes and remains a
future label, never a selection feature. The old-close anchor keeps its original
X+two-minute future-label availability assumption. None of these assumed clocks
is historical publication proof; retrieved_at remains separate.

Reconstruct and require the complete original120-minute event path, whose known
state defined the original selected cohort. Both the cap-minute open and every
possible earlier managed fill lie inside that existing interval. A missing/
invalid/duplicate bucket contradicting that original completeness, or an old
anchor mismatch, refuses the run. Preserve original nonselected/unknown rows;
never turn absent data into zero, drop selected events or substitute a price.
Keep Decimal prices and exact Fraction returns until the statistical boundary.

### Own-label calibration before final performance family

CM records per-event time-only/managed labels, trace, old-close anchor and exact
metadata needed to verify them. Summaries report counts/missingness and each
new arm's own dispersion/detectable effect only: four pattern/timing groups
times two arms. No new performance mean, aggregate profit/exit-reason counts,
paired performance contrast, winning parameter or final comparison family is
specified or run by this calibration.

Use all reference sessions including zero-event days and the existing event SD,
naive/session-cluster/circular-five-session bootstrap errors, two thousand draws
and seed zero. Compare the resulting own-label MDE to the unchanged assumed33bp
cost floor. Do not use the smaller paired error or transfer an old exploratory
waiver. Unknown labels/sizing or above-cost sizing retain the ordinary operator
choice requirement. If all prerequisites pass, continue the already authorized
request through a separately registered final comparison without another
permission question. No edge, strategy pass, direction closure, reserved access
or paper/live promotion follows.

### Registered input and execution boundary

Study `intraday-exit-policy-calibration-v1`, specification
`configs/research/discovery/intraday-exit-policy-calibration-v1.json`.
Read only the pinned CH report and original CC archive:

- `/home/minjun4897/research-evidence/intraday-ch-entry-strength-comparison-20261021-v1/report.json`,
  SHA256`f758e48e7aa0431f9dc2ea1cb50f8925bfbd8588669fb99a50c87ca334fbf028`.
- `/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz`,
  compressed SHA256`4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de`,
  stream SHA256`d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da`.

Original union5,158 chunks/1,002,211 rows. Preserve observation_date, unknown raw
available_at/finality, retrieved_at, source, exact data_vintage and evidence_level.
No new date/API/database/credential/reserved-window input is needed.
Exclusive output
`/home/minjun4897/research-evidence/intraday-cm-exit-policy-20261021-v1`.
The fifteen-file source closure substitutes the new producer/specification into
CK's equivalent raw-replay dependency set. Pin source/specification/runtime and
private wrapper/standalone verifier. Durable start precedes both input reads.

A standalone stdlib-only verifier independently reconstructs raw paths, causal
trigger/decision/fill traces, open/close bridge, new exact labels and eight own
sizing cells without importing a producer. Handwritten oracle/mutation tests,
direct resampling checks and representative invented-input profiling precede
actual execution. Source/runtime/logging authentication is a separate wrapper
responsibility; the verifier does not certify vendor truth. One unchanged
shared whole-process budget covers production, independent replay, rechecks
and substantive publication preparation. Preserve partial failures; no retries
or per-phase reset. Distinct final source/integrity reviews, actual required
CI and normal merge precede one actual run. Update only the isolated GCP research
checkout; collectors, schedules, credentials, databases and trading controls
remain outside scope. Use named local tests and the staged-only scanner.

## 2026-10-21 — implementation and invented-input verification before actual input

The fresh implementation worker completed the public producer, specification
and fifty synthetic regression cases. Its final named CM/CK/CI bundle passed
162 tests in48.21s. The separate standalone-verifier worker completed49
handwritten-oracle/mutation tests, passing in6.25s. Both workers confirmed no
actual-input, protected-file-content, network/API/database/credential or
reserved-window access. No actual outcome was measured by these tests.

The author ran the final explicitly named WSL bundle:
`python/.venv/bin/python -B -m pytest -q python/tests/test_intraday_exit_policy.py python/tests/test_intraday_entry_delay.py python/tests/test_intraday_signal_paths.py var/test_cm_exit_policy_independent.py var/test_cm_exit_policy_publication.py python/tests/test_planning_index.py python/tests/test_docs_do_not_duplicate_claude_md.py`.
Result:312 passed/1 skipped in59.59s. The earlier wrapper/document bundle passed
101/1 skipped in6.30s. These named commands exclude both protected originals;
no local full-suite or full-tree scanner was used. Final staged scanning and
normal Git hooks follow before the reviewable source commit.

The final standalone-only invented-input profile reconstructed257 sessions,
5,140 synthetic chunks and1,002,211 rows, with three continuation/two recovery
events per session and eight own-power cells. The actual archive remains5,158
chunks; these are deliberately different populations, not an actual-data
substitution. Verification passed with36,872 checkpoints, verifier CPU14.711637146s,
total process CPU21.217504025s and peak RSS169,992KiB under one unchanged
CPU300s/AS512MiB/wall1200s/nice10 budget. Starting/ending helper hashes matched.
This local invented profile does not guarantee GCP runtime. An earlier profile
made before final anchor-consistency strengthening was discarded rather than
treated as final evidence.

Public raw-byte SHA256 at this boundary:

- producer:`a17bdf985b6f9d3e7fce90073212fb95322222ae3b44f53e07e01b428b1d75a6`;
- public test:`d4ab9be700a9924c3168476dee8e8595398903a3401b16a6c28f994fe2a97bd4`;
- specification:`0718bcca9fedf46e2e5680508d163a8ca2e26731b86df423f1ef402db77e8b72`.

Frozen ignored helpers, reviewed separately from the Git source:

- `var/cm-exit-policy-publication.py`:
  `149ef099e8724afb5aa9e1c76296d6aba184e20dc9823404ba9aeb854f52b9fa`;
- `var/test_cm_exit_policy_publication.py`:
  `d613070c9858cea34b0b79668ccc0216d6097281be644c6667ca6c5312e1e153`;
- `var/cm-exit-policy-independent.py`:
  `7de1798c5bd732d967d31af53617474a19d955b183d51de96b8726a6c593ab8b`;
- `var/test_cm_exit_policy_independent.py`:
  `0bd5d9a89181f3582edc4f21bfa2a6134eb6897e4db9603e17647f62d0ab3fd5`;
- `var/cm-synthetic-profile.py`:
  `2de6373410e29e5e787cdd9f616a856896a05b76bbb22d42c205323124cfdc99`;
- `var/cm-synthetic-profile.json`:
  `efd34a764352c4945ec1f3b61de80148c1767590481f7d61d237e7030db0c91c`.

These are preparation results. Separate fresh final source/integrity reviews,
actual required CI, normal merge, exact-tree/helper rechecks and the single
registered GCP calibration/replay remain pending at this record.
