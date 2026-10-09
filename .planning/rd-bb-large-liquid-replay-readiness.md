# Research Direction Task BB — bounded replay gates and synthetic lot bookkeeping

## Scope — 2026-10-09 KST

The operator approved the revised order: inventory the remaining prerequisites,
prepare the experiment with synthetic inputs alongside necessary evidence work,
run a newly registered sizing study only after its gates clear, then separately
register the first comparison if the existing detectable-effect/cost-floor rule
permits it. The selected universe and Tasks AG/AN/AQ remain binding. This record
does not reopen the old full-market study or change any numeric research rule.

Implementation has started in this chat's existing worktree. Its new branch is
`codex/large-liquid-replay-readiness`, based on merged main `29cdbfa9bc9b2fa9ce1e69dfd0e6595ee25bfc7f`.
The former BA branch's tree was verified identical to that merge before moving
this new branch's base. Existing source-point evidence and failed acquisitions
are retained. The operator selected GPT-6.1 Sol Ultra as the working default;
Astra should be recommended with a concrete review question at a consequential
design/accounting or interpretation checkpoint, not substituted automatically.

## Fixed completion gates

These are work-package gates, not a strategy-completion score. A partially
implemented gate stays open. An unavailable denominator stays undefined rather
than becoming zero. No number below certifies a historical classification.

| gate | scope and completion condition | starting state |
|---|---|---|
| R1 source population and sampled issue links | Preserve all AQ/AR/AS rows and controls; reconcile source-point identities without changing thresholds or dates. | Completed by AS-BA: 944 rows, 917 linked targets across 136 codes, 27 retained controls. |
| R2 historical eligibility | Positive operating-company/common-issue periods and their changes for all required target histories; explicit control dispositions. Exactly one eligible period must cover each required observation. | Open: source points do not establish continuous periods. |
| R3 information availability | Evidence-publication dates and the applicability of source release timing at each modeled decision; no downloaded/current record backdated into the past. | Open: BA's 917 `known_on` values remain null; actual historical KRX publication is uncertified. |
| R4 post-conversion activity baseline | Join observed prior candidates to verified operating-company periods, keeping AN's baseline separate from AQ's fixed-calendar absolute-liquidity window. Verified short history is a disposition; unknown identity stops replay. | Open: 909 observed complete windows and eight explained short histories are not certified operating-company baselines. |
| R5 price basis and corporate-action coverage | AG's raw/adjusted bridge, mandatory versus voluntary event, qualifying share class, availability and actual net cash/payment evidence; cover closed and successor lots as well as terminal inventory. | Open: the new book's actual lot/event denominator is not yet defined. |
| R6 integrated replay accounting | A new lot-based full-calendar runner with selection, entry, due exits, events, marks and re-entry in a reviewed order; cash/share/slot conservation and hand-computed synthetic checks. | Open: existing primitives are not wired into v1. This task starts the atomic bookkeeping component only. |
| R7 executable preregistration and logging | Frozen source/dependency and dataset/evidence hashes, reviewed committed specification and runner, durable trial-start record before real input access. | Open: no new large/liquid replay specification or execution is authorized by this inventory. |
| R8 sizing feasibility | One logged large/liquid sizing trial, after R1-R7, using event-arm session dispersion and the existing stopping rule. | Not executed. Comparison requires this decision first. |

R1 is a source-link gate only. It must not be reported as historical membership,
normal-baseline certification, universe readiness or strategy validation. R8
cannot promote a candidate; any permitted comparison is separately registered.
Failure of R8 does not authorize a descriptive exception or direction closure.

## Bounded metadata inventory — registered before the GCP read

The local BA result is pinned at
`/home/minju/.local/share/trading-engine-research/large-liquid-ba-identity-reconciliation-20261009-v1/result.json`,
SHA-256 `e871aa7388c046197b4b89e7185ff85f2b5ec93940165865ce663154d9f08c95`.
The local saved workload `var/av-identity-workload.json` is pinned at
`a242000a35165907fcbb12ef27a4127cb2ceabc90716a9334fef9c102b90d79e`.
Read only their dates, identities, dispositions, provenance and uncertified
flags. Their code/ISIN/formation counts must reconcile before publishing a
new inventory. Preserve all controls and AS's eight short-history pairs.

One additional metadata input may be read through the existing verified WSL
SSH route, without credentials or a trading database:

- GCP `/home/minjun4897/research-evidence/large-liquid-normal-lookbacks-20261008-v1/result.json`,
  pinned SHA-256 `eb95349f4a5555a45363b98ae907c6a7791e12297a3fee17fa7729a492ce1fa4`.
- Project only row formation/code, common-label and positive-ST presence,
  prior candidate dates/count, skipped-frozen/missing/invalid dates, required
  classification range, formation state and uncertified/null timing fields.
- Preserve the original search range. In the new inventory alone, limit the
  eight verified short histories to their AS listing dates; never relabel or
  overwrite AU's original result. Check all removed dates precede listing.
- Count candidate-window references separately from unique `(code, date)`
  obligations and broad per-code envelopes. An envelope is not the union of
  observation requirements. Count actual holding/action-lot requirements as
  undefined until the new eligible book and event coverage establish them.

Verify source hashes and private permissions. Use one serial SSH read with no
automatic retries or alternative host. Capture only the projection and its
provenance in a new private evidence root; never copy a database, `.env`, trial
log, raw price values or reserved data. Preserve any failure. No exchange
request, outcome selection, collector change or new research trial is included.

## Synthetic bookkeeping implementation

`research/activity_book.py` will compose the existing AG primitives into an
immutable cash-and-lot snapshot. Lots remain keyed by acquisition identity even
when several receive the same successor code. A compulsory cross-code event
or verified final payout applies to all matching lots atomically, with an
applied-event identity preventing repeated conversion or payment. A failed lot
transition must leave the whole input book unchanged. Time cannot move back.

The component has no data loader, file/network access, price replay, trade
selection or performance calculation. It does not implement voluntary tender
participation, partial distributions, dividends, broker rounding or a complete
calendar/event loop. Inspected evidence remains a caller obligation; a source
string or event ID is not proof that an event was compulsory or completed.

Synthetic acceptance checks are: separate successor lots and due dates;
adjusted/raw quantity and carried-value conservation; pending-delivery marks;
cash unchanged before actual payout and inventory removed when paid; repeated
event rejection; immutable failure on an inconsistent lot; finite Decimal
cash/IDs/date and time-direction validation. Full selection/fill/mark/exit
integration and its synthetic acceptance matrix remain explicit R6 work.

## Work order and reporting

Finish the fixed inventory and its independent metadata reconstruction first.
Prepare synthetic integration alongside evidence resolution. The immediate
evidence work is the two already identified F&F July 23 corrections and relevant
outcome evidence under a separately fixed minimal source scope, followed by
R2-R5 across this study's own pool. This is not permission to reacquire reviewed
AZ/BA originals or resume full-market SPAC expansion. Add source work only for
a named unresolved obligation that can affect eligibility, decisions, fills,
shares or cash; unavailable evidence or a changed research assumption is a
reported decision point.

Each operator report includes completed/total obligations where that denominator
exists, unresolved/undefined workloads, synthetic runner readiness, actual new
sizing/comparison counts, and the next action. Do not turn R1's completion or
test counts into a percentage of strategy completion. At registration, new
large/liquid sizing and comparison counts are both zero. Verification, review,
merge and isolated research-checkout sync remain the publication steps; the
running collector and reserved prices are outside this package.

## Completed bounded inventory

The one registered SSH metadata read completed at 2026-10-09 05:07:47 UTC
(14:07:47 KST). AU's saved source hash and private permissions matched. The
collector was clean at `4ce85d714890b87f1a57ae89d4942660e41c0483` before and
after; the isolated research checkout was clean at the expected BA merge.
No HTTP request, database read, price value, trial-log access or operational
change was made. BA and the saved workload matched their registered hashes.

| metadata obligation or partition | observed count |
|---|---:|
| unchanged full population | 944 rows / 140 codes |
| target candidate-formation windows | 917 / 136 codes / 14 formations |
| retained control/explicit-type rows | 27 |
| preceding non-frozen candidate date references | 54,766 |
| skipped frozen date references inside the required spans | 90 |
| complete observed candidate windows / verified short histories | 909 / 8 |
| original searched interval date references, including formation | 62,075 |
| pre-listing search references in the eight short cases | 6,302 |
| new listing-bounded required interval date references | 55,773 |
| distinct `(code, date)` obligations in original / new inventory | 62,075 / 55,773 |

Reference and distinct-date counts happen to agree here; they were calculated
separately. The four prior-state partitions are disjoint and reconcile to each
original `canonical_day_count`. Every removed date is missing before the
verified current-issue listing, not an internal collection gap. For each code,
the new date minimum, last formation and formation count agree with the saved
136-code workload. Its 52 earliest envelopes start at the spent-panel boundary;
84 start later. Broad envelopes still do not equal the observation-interval union.

This fixes the date-level denominator for R2/R4, not a requirement to fetch
55,773 separate filings: one verified period can cover many obligations. All
known/publication fields and certification/readiness flags remain null/false.
Actual new holding/action-lot denominators remain null until the new eligible
book and event coverage exist; zero would falsely close R5. New sizing and
comparison executions remain zero.

The private output root is
`/home/minju/.local/share/trading-engine-research/large-liquid-bb-replay-inventory-20261009-v1/`.
Result SHA-256:
`2ac0b2c81d19921d19cfea95a850574d9bef8dc7b5d1ebc900aa1f039ea0d9b4`.
Projected-source SHA-256:
`e130c3bb60bf806170cf8998c8f5a9740f072de59453743c23b8ef03d9d0ad98`.
The scope, producer and remote projection source were exclusively saved before
the one SSH request. Independent saved-metadata reconstruction and private
preservation verification are the next checks for this result.

## Implemented synthetic accounting component

`ActivityBook`, `CompulsoryStockExchange` and `FinalCashPayment` now compose
AG's arithmetic. Future events advance the cutoff without changing cash, lots
or consumed IDs. A due event is consumed even if no matching lot is held; an
ID repeated across either event type is refused. Every matching lot must
validate before the new snapshot is returned. Other lots and distinct successor
lots keep their acquisition, due-sale and observation dates. Availability can
be future; acquisition and observed marks cannot be after the book cutoff.

The independent adversarial review reproduced mutable provenance: AG's older
constructors allow a list of 64 hex characters as a dataset identifier. Sharing
it across lots/bases permits a later mutation of otherwise frozen snapshots.
The new book/event boundaries require a lowercase 64-character hex string,
with list/tuple/missing/numeric/malformed-string regression cases. AG and v1
remain unchanged. The reviewer verified the correction, existing-successor
lot preservation, separate due dates, cash/slot/value conservation and atomic
failure after a later lot rejects, with no remaining scoped finding.

Focused WSL verification passed 80 tests: 58 new book cases and 22 existing
accounting cases. Removing `_check_session` in memory made the past-session
case fail with `DID NOT RAISE`; restored source passed. The scanner passed.
These checks are synthetic accounting, not a return trial or executable
large/liquid runner. R6 still needs selection, fills, full-calendar event order,
marks, due exits and re-entry. Full checks, CodeRabbit and publication remain.

## Independent inventory verification

A fresh verifier reconstructed all row fields and aggregate results from the
saved projection, BA identities and workload without importing the producer.
The complete canonical result matched, including all original controls, the
eight listing-bound adjustments and null/false limitations. Twenty-three
mutations were refused, including a post-listing gap with otherwise consistent
counts, duplicate/deleted identities, changed dates, dropped controls, promoted
known/certification values and a fabricated zero holding denominator. Separate
read-back verified input/output hashes and 0700/0600 private permissions.

- Verifier source: `17db8b6d1a4cc83361b8bdf1dfe01f37bab7f09f1359fa1f534c7b1a328c7a9e`.
- Verification receipt: `e8186170145487e4f525d6178bbfb431b17c10462c9cc4aac9d378dba81b1cc3`.
- Mutation receipt: `12011bba5a27d3c9b794299813596b5e27a2d0f1aa2083f1e835ab02fa371a53`.
- Interpretation supplement: `71c3c6f31c299949a3231b38395b143f021f3facab2b20223d1050aa31e4c92f`.

The supplement preserves the first receipt and narrows two labels: its 1,699
dates are the AU projected-date union, not AP's complete trading calendar, and
its 121,707 envelope references use that projected union only. They are not a
full-calendar broad-envelope denominator. AP's full calendar was not read in
this verification. The actual required-date union is the separately verified
55,773 count; the full-calendar envelope denominator remains undefined.

## Frozen private archive

Local preflight froze nine payloads: the eight inventory-root files (scope,
producer, remote projection source, projected metadata, result, verification,
mutation receipt and interpretation supplement) plus the independent verifier
source. Total payload size is 4,731,305 bytes, below the fixed 8 MiB limit.
Manifest SHA-256 is
`e7b1a8716bdfa83d2f2ad36f1c47649e9224db6851276b36685a03e4dc24bcec`.

Transfer only this frozen package to the fresh private GCP root
`/home/minjun4897/research-evidence/large-liquid-bb-replay-readiness-20261009-v1`.
The exclusive receiver must validate manifest, membership, bytes and hashes
before writing, preserve 0700/0600 permissions and verify the collector is
unchanged. There are ten final files including the manifest. A separate SSH
read-back must verify them again. No overwrite, automatic retry, database,
credential or reserved series is part of this archive. The isolated research
checkout is synchronized only after required review/CI and merge.

## Archive completion

The frozen package was transferred once to the registered GCP root. A separate
SSH read-back completed at 2026-10-09 05:20:42 UTC (14:20:42 KST): all ten
files, manifest membership, byte lengths, SHA-256 hashes and 0700/0600
permissions matched. The collector stayed clean at the pinned operational
commit before and after both operations. No research run or collector change
was made. The earlier independent-reconstruction and archive instructions
are now satisfied by the receipts above; review/CI, merge and isolated code
synchronization remain the publication steps.
