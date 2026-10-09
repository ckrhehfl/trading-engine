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

## Publication review follow-up

PR #258's first Python CI completed with 4,629 passed, three skipped and one
failure: the docs figure-ownership guard rejected the two comma-grouped
inventory counts repeated in the handoff. The handoff now points to this BB
record for those counts. Focused document and planning-index verification
passed 84 tests with one skip. The initial failure remains recorded rather
than described as a passing full-suite run.

CodeRabbit questioned using an availability-date price basis to express the
successor claim at effectiveness. Independent synthetic rechecking confirmed
AG's existing fixed-snapshot coordinate contract: changing future raw and
adjusted price levels together leaves the early book unchanged; changing only
the adjustment coordinate changes shares and carried unit mark inversely while
preserving raw entitlement, NAV, cash and occupied slots. An observable
successor mark still cannot update before availability. Simply delaying the
whole event would leave the old lot's earlier availability in place, permitting
the old issue to be marked during the delivery gap. The safe follow-up is to
make this contract explicit and add coordinate-invariance regressions, retaining
the mandatory effective-date claim and AG/v1 behavior.

This argument does not certify a real vendor bridge or permit the carried
quantity/mark to become a pre-availability price signal. R3, R5 and R6 remain
open. The future full runner must separately prove point-in-time selection,
fills and observable marks; the accounting component has no such consumers.

The WSL `scripts/dev.sh check` completed at 2026-10-09 05:37:18 UTC with
guardrails, 27 script regression tests, 34 hook tests and the collected Python
suite passing: 4,630 passed, three skipped in 1,633.33 seconds. This local
full-suite result follows the handoff correction. The additional coordinate
regressions still require their focused run and updated-head CI before merge.

The three coordinate-regression cases subsequently passed with the existing
accounting checks: 83 focused tests (61 book and 22 AG arithmetic), with the
scanner also passing. They include two constant-factor price-level changes,
and a factor rebase with independently specified raw quantities, carried value,
delivery restriction and same-economic-price valuation after delivery. No
execution semantics changed. Updated-head CI and the review response remain.

## Synthetic integration scope while publication waits

CodeRabbit reported a scheduled included-review limit after the batched fix.
Its reply created at 2026-10-09 05:40:12 UTC states 47 minutes; request one
full review after 06:27:12 UTC plus a small margin, using that reply's own
timestamp. No billing or subscription setting is changed, and a limited check
does not satisfy the merge gate.

Use this wait to prepare the next pure synthetic integration in
`research/activity_replay.py` and its tests, within this publication package.
The original book-only implementation above remains a historical stage. This
addition has no loader, CLI, data acquisition, automatic certification or
actual research execution. It composes the frozen v1 selection/fill conventions
and AG lot accounting, with explicit synthetic eligibility inputs rather than
relabeling source diagnostics as certified history. Existing v1 parameters
must be supplied from the original contract; no large/liquid outcome is used
to choose a new cutoff or size.

The session order is corporate actions due by the cutoff, opening due sales,
next-session entries from the preceding formation, then observable closing
marks. Process every index session, including those between formations. Due
sales precede same-day re-entry, as in v1. A mandatory successor occupies its
old acquisition slot through delivery, keeps the original due date and cannot
supply an unavailable signal, mark or fill. Cash payments credit only actual
verified net amounts at payment; unknown recovery remains unresolved.

The minimal acceptance matrix covers unchanged action-free v1 behavior; both
distinct lookback contracts; point-in-time refusal before hash selection;
next-open fills and selected-entry failure without substitution; cash-funded
allocation and dated tax; frozen/delayed exits; separate successor lots and
due dates; delivery marks; same-day due sale/re-entry; and future mutations
that cannot alter earlier economic states. Any unavailable input or conflicting
eligibility must stop, not remove a candidate silently. New study adapters,
durable real-run logging, complete source coverage and sizing remain outside
the synthetic component. Its completion does not close R2-R5 or R7-R8, and
R6 stays open until the entire declared matrix and independent review pass.

## Independent synthetic integration review

The initial toy integration passed 135 focused cases, including 27 new replay
cases and the unchanged v1/book/arithmetic suites. Independent adversarial
review then reproduced an event/quote conflict: an effective-date conversion
could consume its ID in an empty book before a stale positive OLD quote funded
a new OLD acquisition; a final-payout issue could similarly be sold after its
verified last trading date. Reject such active old-issue quote conflicts before
trade/mark processing, rather than replacing or dropping a candidate. A
formation made before the event followed by genuinely absent/frozen OLD quotes
is still an ordinary unfilled entry with cash retained, as in v1.

Independent mutation checks also found three weakly protected paths: event
chronological sorting, the available-cash cap on entry, and the availability
guard on opening-equity allocation. Add separate hand-computed fixtures for
each. The first full-suite attempt during these changing draft fixtures was
interrupted after an early failure indication; it is not a completed check.
Run the fixed snapshot and updated-head CI after the focused fixes. These
synthetic review findings are not real-price experiments or evidence closure.

## Implemented synthetic session integration

`SyntheticCandidate`, `SyntheticSelection` and `replay_synthetic` now compose
the book across every supplied replay session. Selection covers every present,
non-frozen formation code before held-code exclusion/hash ordering. Proven toy
excluded controls do not need operating-history screens; unknown classification
cannot use that exemption. The two history dispositions are separate caller
inputs, not implemented historical screen calculations. Toy availability has
date granularity and cannot certify the actual morning decision cutoff.

The event/quote check rejects positive non-frozen old-issue opens or closes
from compulsory effectiveness onward, or after a verified final distribution's
last trading date, including the pre-payment interval. It runs only within the
replayed sessions. It neither fabricates a cessation date nor reads settlement-
tail prices. A pre-event formation followed by absent/frozen entry-day quotes
still leaves cash unfilled without selecting a replacement.

Final focused verification passed 145 cases: 37 new replay cases and 108
unchanged v1/book/arithmetic cases. Hand-computed fixtures now cover the three
independent mutation gaps and the event/quote failures, including the valid
pre-effectiveness and missing/frozen alternatives. No original v1 or AG source
was edited. Independent rechecks, fixed-snapshot full checks and updated-head
CI/review remain the publication checks for this extension.

This completes a synthetic session-loop component, not the actual large/liquid
selector or dataset adapter. R2-R5, R7-R8 and R6's real selector/integration
remain open. No new sizing or comparison was executed and no actual lot/event
denominator was inferred from these toy holdings.

## Availability contract audit and bounded next document check

A separate repository-only audit distinguishes public historical information
from the date a modern API response was downloaded. AG's `known_on` means
classification was public, and AG requires it by formation; AQ's next-session
market-data decision does not waive that earlier classification cutoff. The
synthetic loop's next-session classification comparison was independently
reproduced as weaker than AG and is being corrected before publication.

R3 does not require historical server logs from the particular modern OpenAPI
unless the study claims to reproduce that provider's historical delivery.
It does require original public-information availability, dated classification
evidence and supported applicability of the market-observation release bound.
Current FAQ timing, a historical `basDd` and a modern receipt are not enough.
This clarification neither supplies `known_on` values nor closes R2-R5.

Before any further public document read, bound one timing audit to three
official candidates: the KRX OpenAPI FAQ at
`https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd`, the separate
KRX data-feed publication schedule mentioned in AP, and the FSC stock-price
service page `https://www.data.go.kr/data/15094808/openapi.do`. For the
unidentified KRX schedule only, allow one official-domain search to locate
one original schedule document. Review each candidate once for the publication
upper bound and historical applicability of close, final accumulated turnover,
listed shares and issue market capitalization during 2019-2025. Read service
documentation only: no data endpoint, filing search, stock price, authentication,
paid enrollment or historical API-response reconstruction. Record supported
bounds or unverified status and stop after these three candidates; do not
expand the scope or automatically adopt a new timing assumption. If evidence
is insufficient, preserve R3 and bring any required model assumption to the
operator separately.

## Final synthetic contract corrections

The event/quote conflict check now also runs on the initial formation before
the session loop; otherwise a ceased issue's positive first-formation quote
could still enter selection. Independent execution rejected that attack and
the two original stale-quote attacks, while retaining absent/frozen unfilled
entries, normal pre-event trading and untouched settlement-tail prices.
Three added initial-formation cases raised focused checks to 148.

The final contract audit then reproduced AG refusing a classification first
known on the next session while the new toy loop accepted it. The comparison
now uses formation, and fixture defaults use that same date. A held candidate
which also fails the size screen still cannot use next-session classification:
the added regression verifies refusal before hashing. AG and v1 remain
unchanged. Final focused checks passed 149 cases: 41 replay and 108 existing
v1/book/arithmetic cases. The repository scanner passed. The three independent
in-memory mutations (event ordering, cash cap, pending-delivery allocation)
are each rejected by their hand-computed fixture.

The local full check already in progress collected the preceding 37 replay
cases; it does not establish that the last four regressions were collected.
Those are covered by the final focused run and must also run in exact-head CI.
Its final result and the classification-cutoff independent recheck are still
pending at this entry. No real sizing, comparison or source certification
was executed by these synthetic checks.

## Bounded timing-document result

The registered three-candidate timing audit is complete. The FAQ URL and FSC
service URL were each opened once; one KRX-domain search attempted to identify
the separate feed schedule. Five text searches used already-opened results.
The FAQ's extracted text did not expose individual answers, so it supplies no
new historical timing evidence beyond AP's previously inspected current FAQ.
The [FSC service page](https://www.data.go.kr/data/15094808/openapi.do) states
that it links and collects KRX source information before publishing after
13:00 on the following business day. Its registration date is 2021-11-16,
not the original market information's first-publication date. This service
does not support the modeled 08:30 decision or establish KRX's historical
release bound. The single KRX-domain search did not identify the separate
schedule; that is an acquisition limitation, not evidence it does not exist.

No field-specific historical upper bound was certified. Some search snippets
incidentally contained quote tables; no quote page or data endpoint was opened,
and no quote values were extracted, saved or used. The audit stopped at its
declared scope, with R3 still open. The operator was presented with the concrete
choice of an explicit next-session 08:30 market-information assumption, further
historical-timing evidence, or Astra review of that assumption first. No choice
is inferred from silence. Classification remains known by formation regardless
of that pending market-information decision, and R2/R4/R5 remain separate.

## Operator timing decision and further bounded verification

The operator explicitly selected option 2, further historical-publication
evidence, on 2026-10-09. Do not adopt or mark approved the proposed 08:30
historical-information assumption. Keep R3 open while current synthetic code
publication proceeds independently.

The next documentation-only scope may make at most two search batches totaling
eight queries, limited to official KRX/KOSCOM/FSC domains. Inspect at most six
original official documents and at most two directly related official links
needed to read their historical schedule or effective-date provisions. Seek
the historical public release upper bound of final daily close, accumulated
turnover including after-hours, listed shares and issue capitalization during
2019-2025; separate market-information publication from the modern OpenAPI's
delivery. Preserve dates, field coverage and effective periods, distinguishing
direct evidence from inference. No data endpoint, stock quote, authentication,
paid enrollment or API credential is used. Stop at the declared document/search
limits and report any uncovered periods or fields; no silent timing assumption,
additional scope expansion or real research trial follows from this check.

The final independent synthetic recheck reproduced refusal of next-session
classification and acceptance of classification known by formation. The old
event/quote and first-formation attacks remained blocked; all 41 new replay
tests passed in the independent WSL run. No scoped findings or testing gaps
remain in that review. This satisfies the synthetic code's independent review,
not actual historical certification or the required completed CodeRabbit review.

## Further historical-publication findings

The second registered document check stopped after two batches/eight official-
domain queries, five original documents and two related official links. It
did not adopt the proposed assumption. The [KRX distribution-product page](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp)
lists KOSPI/KOSDAQ realtime and EOD products and current first/second daytime
close transmissions at 16:00 and 18:10. It does not state the schedule's
historical effective interval or certify final accumulated turnover, listed
shares and issue capitalization in that batch. Copyright is not an effective
date. The [KRX receiving guide](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp)
and [KOSCOM service description](https://mig.koscom.co.kr/portal/main/contents.do?menuNo=200611)
establish distribution channels outside the modern API, including post-market
FTP closing information, but not the required historical field-level bound.

The [FSC notice dated 2019-04-03](https://www.fsc.go.kr/po010106/73613)
describes pre-opening previous-close trading during 07:30-08:30 and a planned
2019-04-29 change to 08:30-08:40. This is partial evidence that the previous
close was usable in morning trading under that contemporary system; it does
not certify continuous 2019-2025 release bounds for all study fields. A KOSCOM
service overview and business description added no historical schedule; a
retrieved official PDF was a 2013 winter magazine and was not used as timing
evidence. Non-target market-number snippets in search results were not opened
as quote pages, extracted or used as research input.

Thus the close has partial contemporary-system/current-distribution support;
the historical release bounds for final after-hours-inclusive turnover,
listed shares and issue capitalization remain unverified. R3 stays open.
This is a finite acquisition result, not a claim that such evidence does not
exist or that an unsupported historical assumption has been approved. Further
evidence work must target those remaining fields and applicable periods,
rather than demand unavailable per-response logs from the current OpenAPI.
