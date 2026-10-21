# Research Direction Task CG — calibrate one causal pre-entry relative-strength condition

## 2026-10-21 — performed intake and fixed diagnostic design

The operator continued the stated next step after CF: inspect movement before
entry and fix one new condition. CF's five fixed response horizons did not
produce a positive mean after the assumed 33bp cost. That observation motivates
another hypothesis; it neither closes the original mechanisms nor supplies
permission to optimize thresholds, choose a winning horizon or waive sizing.
No new saved-input read has occurred at this design entry.

### One condition, fixed before any new subgroup labels

Keep the original first opening-range and post-drop-recovery events, original
causal same-clock peer selections and D+1/D+2 membership variants. D+1/D+2
delay monthly universe information; they are not position holding periods.
For the thirty completed minutes ending at the original signal bar's end,
calculate first minute open to last minute close for the event and every
original selected peer. Require every intervening minute to be valid. The
feature becomes available at the end plus the existing assumed two-minute
lag and must be available by the original decision. Never move the decision,
use the later entry price, replace a peer, or let subsequent returns select
events. The event's return minus the equal-weight mean peer return must be
strictly positive; exact zero fails. There is no second absolute-return
condition or tuned strength threshold. Decimal/Fraction arithmetic remains
exact through the statistical boundary.

Thirty minutes follows the existing mechanism's context length; zero means
outperforming its observed peers. Neither is selected from newly inspected
conditional outcomes. CA already listed relative market/sector strength as
an explanatory candidate. The papers [Gao et al.](https://profiles.wustl.edu/en/publications/market-intraday-momentum/)
and [Heston et al.](https://arxiv.org/abs/1005.3535), inspected again during
this intake, describe different intraday predictability and reversal settings.
They motivate testing timing/context, not this exact rule or a Korean-stock
profit claim. The observed peers here are not a complete market index.

### Reused evidence and new verification boundary

Read only the CF report and original CC compressed archive, both immutable
saved inputs in the already-spent 2025-09-04 through 2026-09-23 window.
CF report SHA256: `ec9c110b7556eeb8fff2557bc069719cdc1b167b9c7c21da908d84e30180667e`.
Archive compressed SHA256: `4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de`.
Uncompressed stream SHA256: `d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da`.
CF's independently reconstructed future labels are reused as fixed upstream
evidence. A new standalone implementation reconstructs the new causal
features and selection from the archive and recalculates counts and sizing.
It does not claim to repeat CF's future-label audit or certify vendor truth.

Keep all 20 by 257 target rows per variant and original unknown states.
Retain incomplete original pairs; feature missingness is separate and never
zero-filled. Calculate label dispersion only on passing events within the
original 120-minute complete-pair cohort at all five original horizons.
This cohort remains conditional on future completeness and cannot represent
the full top-20 population. Do not rescue original unknowns at short horizons.

Report pass/reject/unknown/non-signal counts, retained opportunities by week
and month, own-label standard deviation, naive/session-cluster/whole-session
circular five-day bootstrap errors (2,000 draws, seed zero), maximum design
error and MDE against assumed 33bp costs. Include all reference days, including
zero-event days. No future-return means, peer future contrasts, winner,
p-values, strategy pass or investable performance are in this work unit.
Unknown or above-cost MDE retains the operator-choice requirement before a
new final comparison family. Prior CD/CE exceptions do not transfer.

### Bounded execution and completion requirements

One exclusive output is registered at
`/home/minjun4897/research-evidence/intraday-cg-entry-strength-20261021-v1`.
The existing isolated research checkout alone is updated after separate
fresh-context source and integrity review, real required CI and normal merge.
A canonical durable start precedes actual input access. Pin exact source,
specification, runtime and private verifier/wrapper identities. One shared
process budget covers production, standalone verification and final substantive
publication: CPU300s, address space512MiB, wall1,200s, nice10, no retries.
Reuse verified upstream labels rather than repeating CF's expensive full
future-label replay. Do not reset the budget between phases.

The collector checkout must remain clean at its existing 4ce85d7 commit.
No collector restart, schedule/source replacement, DB, API, credential file,
new observation date, reserved window or trading process is accessed.
Preserve only this attempt's canonical span and partial evidence on failure.
Two untracked operator files remain completely outside reads/tests/scans.

This entry records actual intake and implementation begun, not completed
verification, merge, deployment or a new result.

## 2026-10-21 — compact implementation and synthetic verification

The producer reuses CF's future labels and the existing session parser,
bounded archive iterator, own-dispersion calculation, logging and shared
ResearchBudget. New output projects the upstream event to its two decision
clocks and peer list to symbols; original full events/peer evidence stay
available by the pinned upstream reference. This avoids duplicating context
and future-price metadata while preserving every target, original state,
unknown reason, selected peer and newly reconstructed exact feature.
No new framework, database or data acquisition is introduced.

Explicit WSL test bundle on the final implementation: **208 passed, one
existing planning skip in24.42s**. It comprises53 new public tests,54 standalone
verification tests,17 publication tests,54 document and30 planning checks.
Coverage includes exact-zero rejection, future-price mutations not changing
selection, missing/invalid/duplicate buckets, late availability, no unknown
pair rescue, original peers, all reference dates, a257-date independent direct
bootstrap oracle, strict specification/path/source/runtime checks and a
durable start before saved evidence access. The initial document test found
a duplicated resource figure in the handoff; it was removed in favor of this
ledger. An initial named test command used a nonexistent document-test filename
and ran no tests; the corrected bundle above actually executed. No protected
untracked operator file was read, tested, searched or scanned.

A dedicated WSL process generated **1,002,280 synthetic minute rows across257
reference dates** using the retained CF fixture builder, produced the new
calibration and ran its standalone verifier within one unchanged budget.
The synthetic upstream/report were29,610,927/26,641,201 bytes; absolute total
CPU was34.871983253s, wall34.015223863s and peak RSS379,260KiB. Shared limit
readbacks remained CPU300s, AS536,870,912 bytes and nice10. This is a local
synthetic observation, not a remote guarantee: actual CF upstream is larger
and source/shape differences remain. The first synthetic attempt exposed
a fixture missing its required event-family field; only the fixture adapter
was corrected, without reading real inputs or changing the implementation.

Frozen private closure SHA256:

| File | SHA256 |
| --- | --- |
| `var/cg-entry-strength-publication.py` | `abb012cc35be96cdc6d819ed3303382302845dd2b695da337c9b99a2fe9200ec` |
| `var/test_cg_entry_strength_publication.py` | `a76cafb86070cf46226108258f5833f86ac6f972470d2764898e0b4fc5d2bdc0` |
| `var/cg-entry-strength-independent.py` | `d3f85572eb837c7cabbabddd0f7deb94280b8ac3a24905f34b87e5aa0e04c336` |
| `var/test_cg_entry_strength_independent.py` | `c60017cada06027e0a3d6ec2727bb328b0e2138d9021452e8709b0dbe54c0082` |
| `var/cg-synthetic-scale.py` | `5a4615c8224c27e5e482cff8c257a50dcb7f49ee153bd8567bbb94008eeb79a3` |
| `var/cg-synthetic-scale.json` | `29099ebbb7a0f6c2293eca8c1430ebc0df52fccf3884892626689b205582e87b` |

Final exact-source separate reviews, actual required CI and normal merge
remain before the single registered CG actual execution at this snapshot.
