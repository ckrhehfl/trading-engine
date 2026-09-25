# Consolidation after the 2026-09-23 external audit — decisions and order

**Status**: plan. Four operator decisions are settled and recorded below;
execution is phased in §4. Nothing here changes a Risk Parameter, the
Eligibility Bar, or any Non-negotiable Rule.

---

## 1. Why this exists

Two independent reviews landed within a day of each other:

- **This project's own session review** (2026-09-21 … 09-23), which found
  twelve defects while building the full-universe scan — the SPAC filter,
  the half-filtered pool, `--install-cron` restarting a deliberately
  stopped loop, a `.gitignore` anchoring bug, `stale_checkout`, and the
  `absent` status conflating three states.
- **An external audit run in Codex** against commit `56fb2c1`, briefed by
  `CODEX-FULL-AUDIT.md` (kept outside the repo). Twelve further findings,
  eight major and four minor, each with a reproduction. It respected the
  brief's safety rules and its exclusion list, and it ran real mutation
  tests: eleven mutations, three survived.

**The operator's own description of the state was "뒤죽박죽" — jumbled —
and that reading is correct, but the cause is not design churn.** Measured:

| | |
|---|---|
| `CLAUDE.md` | 3,473 → 1,696 at the 2026-08-26 reorganisation → **3,506 today** |
| growth since | **+1,810 lines in the 28 days to 2026-09-23**, i.e. larger than before the cleanup |
| `.planning/` | 120 documents |
| merged PRs in 7 days | 23 |

The file's own trim rule — *"once that work has happened and its
`.planning/` document exists, this file's entry should come back down to
conclusions plus a pointer"* — has **not run once for the Korean-equities
arc**.

And the pool definition changed four times in four days (`ST` → ISIN issue
type → ISIN instrument class → SPAC → anchoring → whitespace separator).
Every change was a **narrowing**, and every one was forced by a
measurement. That is convergence, not thrashing. The real defect is
structural:

> **The pool definition was never designed. It was discovered while
> building a scanner on top of it.**

The code carries the evidence: the four-filter common-stock rule was
implemented **twice** (`krx_delisted.common_stock` and
`krx_scan.candidates`), which is why the SPAC fix reached one side and not
the other. Six modules know some part of the pool definition.

## 2. The four decisions

### D1 — Repair scope: **consolidate the pool definition and the page-read path**

Rejected: patching all 24 findings individually, and redesigning the
collection layer.

The audit's F-5 is the argument. **Four separate daily-data readers carry
the identical defect** — they filter malformed rows *before* enforcing the
silent row cap, so a truncated 100-row page with one bad row becomes an
apparently uncapped 99-row page. Patching that four times leaves the fifth
reader to be written wrong. One validated page-read contract, called by
all four, fixes it once and makes the next reader correct by construction.

Redesign was rejected on the audit's own evidence: it examined the
identity and survivorship mechanisms and found *"no additional verified
defect in those specific mechanisms."* The structure is not wrong; the
rules are scattered.

### D2 — Published conclusions touched by F-6/F-7: **withdraw, do not recompute**

`rd-u` (conjunctions) and `rd-v` (payoff geometry) are merged.

> **Corrected 2026-09-24: their conclusions do NOT sit in `CLAUDE.md`, and
> this sentence was never checked against the file.** Measured: the only
> `rd-u` material in `CLAUDE.md` is the `check_clustered_observations`
> row and the clustering rule's own evidence paragraph (p 0.016 → 0.182,
> p 0.113 → 0.039, BH 1 → 0), all of which are statements about the
> **clustering correction** and are untouched by F-7 — those p-values are
> raw-return-vs-zero tests, the quantity D3 settles on. `rd-v` appears in
> `CLAUDE.md` not at all.
>
> So **D2's withdrawal lands in the planning documents only**, and
> `CLAUDE.md` needs no edit for it. That is a smaller change than this
> plan assumed, and the assumption is exactly the kind F-12 was about.

Withdraw the affected claims; **do not manufacture corrected historical
figures.** This follows the audit's own recommendation and this project's
standing practice — a retraction that is quietly deleted teaches nothing,
and a corrected p-value invented after the fact is not evidence.

The affected studies become **unevaluable on that axis**, not reversed.

### D3 — What `krx_conjunction` trades: **outright**

The module **computed** three different quantities and printed them as one
recommendation. This is the defect as found; it was fixed on 2026-09-24
(PR #203), so the table is a historical record and not a description of the
module today:

| | quantity, **as found** |
|---|---|
| `p_value` | conditional raw return vs **zero** |
| `direction` | sign of (conditional − unconditional panel mean) |
| `clears_cost` | \|conditional − panel mean\| > 13 bp |

So a basket rising 20 bp in a panel rising 50 bp printed
`<< SHORT, clears 13bp`, and shorting it loses.

**As it stands now**, `direction` and `clears_cost` both read `mean_bp`, so
the cost bar is **`|mean_bp| > 13 bp`** and `excess_bp` survives only as a
reported diagnostic. Every `11.4 bp` and `8.7%` below is an **excess**
figure and belongs to the combination effect or to the diagnostic, never to
a cost verdict; the cost verdict's own figures are `−14.4 bp` and `11.0%`.

**Outright is the settled definition**, for three reasons:

1. **The significance test is already correct for it** — one change
   (`direction`/`clears_cost` read `mean_bp`, not `excess_bp`) instead of
   three. Sign-awareness is preserved by testing `|mean_bp|`, which is what
   the `excess_bp` docstring's own incident was actually about.
2. **The hedged reading is not implementable as computed.** `base_bp` is
   the unconditional mean of the same ~2,700-name panel. That is not an
   instrument. A real hedge would need KOSPI200 futures, whose weights and
   constituents differ — which changes the number rather than interpreting
   it.
3. **`rd-u` §5.1 already moved its own operative claim to a raw-return
   statement**: *"the failure is direction, not cost … it predicted almost
   none of a move that is enormous relative to its cost"* — 11.4 bp of a
   131 bp median move.

`excess_bp` is **kept as a reported diagnostic**. It removes common panel
drift, which is the right unit for the superadditivity finding. What
changes is that the `<<` recommendation may only be derived from a
quantity that can actually be traded.

**What this does to `rd-u`.** The table below is what this plan predicted.
**Two of its four rows were wrong, in both directions, and the corrected
disposition is in `rd-u` §0** (written 2026-09-24, when the fix was
applied and the code was read rather than the plan re-read).

| claim | this plan predicted | what the code actually does |
|---|---|---|
| "0 of 11 survive Benjamini-Hochberg" | **withdrawn** — the gate mixed a raw-return null with an excess-based direction and cost test | **WRONG: it stands, untouched.** BH runs on the p-values alone — `benjamini_hochberg([r.p_value for r in conditional])`, `krx_conjunction.py:547` — and a `p_value` is the raw return against zero, exactly the quantity D3 settles on. BH and the cost test are two separate counts; only the cost half read the excess. |
| "does not clear the cost floor" | **survives either definition** | **WRONG: withdrawn, and it reverses.** The best pair's raw mean is **−14.4 bp** (rd-u §5's own table) against rd-q's **13.0 bp** round trip, so outright it clears. rd-u had compared **−11.4 bp of excess** to that floor. §5.1's own share arithmetic agrees independently: 14.4/131 = 11.0% against a floor of 9.9%. |
| §5.1 "the failure is direction, not cost" (8.7% of an available move) | **kept** | **kept, and strengthened** — the share moves to 11.0%, and the paragraph's warning that *"it did not clear the cost floor"* invites the wrong inference turns out to have been warning about a statement that was not arithmetically true either. |
| superadditivity, 2.8× / 1.56× | **kept**, with its unit named | **kept**, unit named: both are ratios of **excess**. |

**Net effect on `rd-u`: its headline had two independent reasons to stop
and now has one.** The surviving one is the stronger — p = 0.008 on the
best pair is precisely the value that does not survive eleven looks — and
discovery mode's first guard forbids quoting any of it as evidence of an
edge regardless. **No figure was recomputed**; the corrected comparison
reads a number rd-u already published.

**Recorded rather than quietly fixed, because the failure mode is this
consolidation's own subject**: a plan sentence that was never checked
against the code it describes. It is the same shape as F-12, where
CLAUDE.md called a spent window unspent, and as the premise below.

### D4 — Raw price storage: **basis becomes part of symbol identity**

F-4: raw and split-adjusted prices share a storage symbol and primary key
with no provenance, so requesting adjusted data can silently return a
database holding raw prices — a 50:1 split reads as a −98% day.

Rejected: a separate database (splits the store), and refusing raw prices
(forecloses corporate-action research).

`DEFAULT_DB_PATH` must stay resolved from `__file__` and must **not**
become environment-overridable — `data/_paths.py` exists because two
relative conventions once produced a second, parallel database and 3,600
rows went to the wrong one.

## 3. What is explicitly NOT in scope

- Any change to a Risk Parameter, the Eligibility Bar, or the Paper
  Trading Pass Criteria.
- Restarting any stopped loop.
- Recomputing a historical statistic (D2).
- `OrderStore.createOrder` hardening — still deferred to Implementation
  Priority #10 with its written design.
- The audit's own two open questions beyond D3/D4.

## 4. Order of execution

| phase | contents | why here |
|---|---|---|
| **A** | PR #198 merge · **F-12** (a spent window is labelled "unspent" in the detection-floor table, contradicted by `runs/spent_windows.json` and by four other paragraphs) · this document | Smallest risk removed per unit of effort. F-12 is the row a researcher would pick *first*, because its floor (~0.62) is the best on the table. |
| **B** | **F-3** — new one-fold runs are classified as sensitivity probes and contribute **zero** to `N` | **Before any new research.** The direction is unsafe (understated `N` inflates DSR), `CLAUDE.md` already has a fail-closed rule for a *different* path to the same harm, and the per-day selection study is exactly the shape that produces one-fold runs. |
| **C** | Consolidation per D1: one pool definition, one validated page-read contract, `absent` split into three states. Absorbs **F-5 · F-1 · F-8 · F-2 · F-9 · F-10 · F-11** and the duplicate-filter defect. | Must land before the scan's second pass, or that pass runs the old code. |
| **D** | D2 withdrawals in `CLAUDE.md` and the two planning documents; **F-7** fix per D3; **F-4** per D4 | Needs D1's contract in place and decides what §E can say. |
| **E** | `CLAUDE.md` trim, 3,506 → ~1,800, under the file's own two-step rule | Human checkpoint #3: the deletion diff is shown and approved before it lands. |
| **F** | Scan completes → second pass (retries failures and re-probes `absent:unknown`) → full coverage run → `rd-y-…-result.md` | Waiting, parallel to A–E. |
| **G** | Monte Carlo `Discuss` → implementation, plus a benchmark axis | Instrument before hypothesis. |
| **H** | The per-day selection-rule research | Safe only after B. |

## 5. Findings index

The twelve external findings are in `AUDIT.md` inside the audit archive,
with reproductions. **Five were independently re-verified here** before
being accepted: F-2 (code fact confirmed; the running scan has survived
~50 h, so it is latent rather than live), F-3, F-5, F-6, and F-12.

Two severity calls differ from the audit's:

- **F-5 is real but has not bitten.** The panel-completeness check found
  **zero interior gaps across 3,408,780 symbol-days**. Fix it; it does not
  invalidate collected data.
- **F-3 matters more than its listing suggests**, for the reason in §4.

---

## Appendix: the documentation split, and what `docs/` did five times

Added 2026-09-25, during PR #207. The operator asked for a conventional
project documentation structure and chose a **lifetime-based three-way split**:
invariants in `CLAUDE.md` (auto-loaded into every AI session), current
structure in `docs/` (replaced when reality changes), and the decision record
in `.planning/` (append-only). `docs/architecture.md` and a rewritten
`README.md` were the first half of that; the Exchange API facts are the second
and are deferred to their own PR.

**The finding worth keeping is not the split — it is what the new document did
to itself.** `docs/architecture.md`'s own header states that it holds no
invariant, no safety property and no Non-negotiable Rule, because `CLAUDE.md`
is the file a session is guaranteed to have read. Its first draft then broke
that rule **five times**, and every one was caught in review rather than by the
check written for it:

| where | what it restated |
|---|---|
| §1 two planes | that Python may not place live orders; that no order may bypass the Risk Gateway |
| §3 layering | the `OrderExecutor` implementation count, verbatim as a blockquote |
| §4 execution modes | the `kis-paper` unconditional kill-switch trip, as a table column |
| §4 venue hosts | "no environment variable, argument, or other configuration surface" |
| §5 instrument identity | the options scope decision |

Two of the five were found three lines below the sentence promising not to do
it, and `CLAUDE.md`'s own Architecture section says in as many words that none
of what it holds *"is repeated in `docs/`"* — which the draft made false.

**So the transferable statement is a tendency, not an incident**: a structure
document describing a system whose rules live elsewhere drifts toward restating
them, because the rule is the most natural thing to say next after describing
what it governs. Writing "does not duplicate" in the header does not stop it.

**How it was closed, after four rounds of not closing it.** Each of the first
four fixes added the phrase that got through to a blocklist of phrases that had
got through, which converges one incident at a time.
`python/tests/test_docs_do_not_duplicate_claude_md.py` now instead **derives**
the prohibition from `_CLAUDE_MD_INVARIANTS`, the list it already kept for the
opposite assertion — that those phrases are still *in* `CLAUDE.md`. One list,
both directions: an invariant worth pinning into `CLAUDE.md` is by definition
one `docs/` must not carry a second copy of. The remaining blocklists
(`_SAFETY_PHRASES`, `_INVARIANT_PHRASES`, `_RISK_PARAMETER_LABELS`) are kept
and labelled as what they are — known phrases, not the category.

**A second, unrelated lesson from the same PR**, recorded because it cost eight
review rounds: `OrderExecutorImplementationCountTest` enforces the two-executor
invariant by **scanning Java source text**, and every round found the same
defect in a new costume — a text scan meeting a construct it did not model
(files not classes, `class` but not `record`, generics, comments, string
literals, text blocks, type-use annotations, `permits`, annotation array
values, anonymous classes). Thirty-four mutations were run against it and it is
now correct as far as anything found, but the surface is unbounded by
construction.

**The design that ends it is reflection, not a better scanner.** The invariant
is about types, so assert it over types: `OrderExecutor.class.isAssignableFrom`
catches named, anonymous, nested, sealed **and indirect** implementations — the
last being the one gap the text scan discloses it cannot close — with no
parsing at all. It cannot live in `:execution`, whose test would need `:runtime`
on its classpath and that is the dependency the other way round; `:runtime`
depends on every module (verified against the six `build.gradle.kts` files), so
that is where it belongs. **Not done in PR #207**: it is a `java/` structural
change that deserves its own review rather than a fifteenth commit on a
documentation PR, and it is the operator's call.

**The tendency showed up in the other two documents too, which is what makes it
a tendency.** `README.md` explained, in the Safety section, that its own first
draft had claimed not to summarise the Non-negotiable Rules and then summarised
three of them — and in Merge policy, that its path list had been missing
`.coderabbit.yaml` until review. `docs/paper-trading-runbook.md` opened a
paragraph with *"reading them as one is what this line used to do."* All three
are drafting history in a **living** document, i.e. the same misfiling as the
six paragraphs above, committed while fixing those six. They were removed and
the rules they carried kept: `CLAUDE.md` is binding and the README's list is
orientation; the path list is now test-enforced rather than explained.

**What the runbook's paragraph was actually for survived the trim and is worth
restating, because it is the only operationally load-bearing part:**
`VstPreflight` has two unhappy outcomes that look nothing alike from outside —
a non-`VST` balance asset throws and there is no process, while a pre-existing
non-zero position *starts* the loop with its kill switch tripped. An operator
asking "did it start?" gets opposite readings, so the runbook now gives the two
as a table instead of a sentence listing both as reasons it "declines", which
is what it said before review and was simply false.
