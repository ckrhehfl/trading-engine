# Strategy Research Task P: honest trial accounting (lineage, `TrialKind`, project-level `N`)

## Why this task exists

This project has now run **1,839 logged backtests across 8 strategy
families** and found nothing that clears its Eligibility Bar. Before
spending more effort on new strategies, a diagnostic pass established that
the *measurement apparatus itself* is defective in three specific ways,
all inside `python/research/overfitting_check.py`'s
`check_combination_count`. This task fixes those three defects.

That matters concretely, not just aesthetically: a sibling task (`sr-q`)
adds a Deflated Sharpe Ratio, whose `N` — "the number of trials from which
this maximum was selected" (Bailey, Borwein, López de Prado, Zhu 2016) —
has a precise meaning. `check_combination_count`'s number could not serve
as that `N`. Supplying an honest one is this task's output.

Nothing here changes any strategy, any signal, or any reported backtest
result. It changes only how *trials* are counted.

## The three defects, as verified against the real log

All figures below were re-derived directly from the real, untouched
`runs/experiments.jsonl` (1,840 records: **1,839 `backtest_run` + 1
`holdout_access`**), not taken on trust from the brief that assigned this
task.

### Defect 1 — renaming a strategy launders its data-snooping history

Counting was keyed on the bare `strategy_id`. Real, current example:

| `strategy_id` | `check_combination_count` N | risk level |
|---|---|---|
| `ensemble-momentum` | 234 | **high** |
| `ensemble-momentum-configuration-c` | 10 | **low** |

`ensemble-momentum-configuration-c` is not a fresh hypothesis — it is a
direct descendant of `ensemble-momentum`'s own 234-combination search
(`sr-i` refined the ensemble and `sr-n` re-ran the refined "Configuration
C" with funding P&L under the new id). Every rename resets the meter.
Verified further: the two ids' logged results overlap exactly, with
`ensemble-momentum-configuration-c`'s `task-n-reproduction-no-funding`
run reproducing `mean_sharpe = 0.02713372192476914` — byte-identical to
six earlier `ensemble-momentum` records.

**Fix**: count per research *family* (`python/research/lineage.py`), not
per `strategy_id`.

### Defect 2 — parameter-sensitivity probes were miscounted as selection trials

`check_combination_count` added `1` for every record with
`parent_run_id is None`. **382 of the 1,839 `backtest_run` records are
standalone *and* single-fold, and all 382 are `check_parameter_sensitivity`
probes** — not selection trials.

Verified exhaustively, not sampled:

- Exactly three `strategy_id`s have standalone single-fold records:
  `ensemble-momentum` (190), `single-lookback-momentum` (165),
  `ma-crossover-task-g-sensitivity-demo-v2` (27). Total 382.
- Exactly those three ran the sensitivity check under `sr-g`'s
  post-wiring-fix configuration (`parent_run_id=None`), confirmed by
  reading `parameter_sensitivity` back off their own walk-forward records.
- Each count **reconciles exactly** with that run's own recorded neighbour
  evaluations:
  - `ensemble-momentum`: 2 sensitivity-enabled runs × 19 folds ×
    (1 winner + 4 neighbours) = **190** ✓
  - `single-lookback-momentum`: 19 folds, summing each fold's own actual
    non-errored neighbour count = **165** ✓
  - `ma-crossover-task-g-sensitivity-demo-v2`: 3 folds × (1 + 8) = **27** ✓
- All 382 carry `total_candidates: 1`, `candidate_index: 0`,
  `is_holdout_run: false`. **Zero counterexamples across the whole log.**

These re-evaluate an *already-selected* winner's ±10%/±25% neighbours, so
they cannot have influenced which candidate was chosen. Under Bailey et
al.'s framework they are not trials at all.

**This reverses a deliberate `sr-g` decision, and says so rather than
quietly changing it.** `sr-g`'s "Judgment calls resolved without asking"
explicitly considered excluding them and chose not to:

> Considered excluding them (arguably more "pure" to the MinBTL
> framework's actual concern — selection bias specifically) but rejected:
> they are still genuine additional configurations backtested against the
> same limited real data, and this project's overfitting-safeguard
> philosophy … already favors flagging risk earlier and more
> conservatively.

That reasoning was **defensible for a warning heuristic** — a
deliberately conservative advisory number where erring high is the safe
direction. It is **not defensible as the `N` of a Deflated Sharpe Ratio**,
where `N` is a specific quantity in a specific formula and inflating it
does not "err on the safe side", it produces a wrong number. So `sr-g`'s
choice is superseded *for the new statistic's input only*: probes are
excluded from `N` and **reported separately** (`sensitivity_probe_trials`)
rather than discarded, and `check_combination_count`'s own conservative
behaviour is left byte-for-byte intact for anyone who wants the old view.

### Defect 3 — the count was sensitive to a wiring detail, not to research activity

| `strategy_id` | N | risk level |
|---|---|---|
| `ma-crossover-task-g-sensitivity-demo` | 6 | moderate |
| `ma-crossover-task-g-sensitivity-demo-v2` | 33 | **high** |

These two records describe **identical work** — the same 3-fold
`ma_crossover` demo over the same 19,870 bars, with the same 5-candidate
grid and the same 8-neighbour sensitivity check, producing the same
`mean_sharpe = -3.901071759035124`. The only difference is that `sr-g`
re-wired the sensitivity `fit()` calls from
`parent_run_id=<enclosing run_id>` to `parent_run_id=None` between them.
Under the old rule the first collapses its 27 probes into one
already-counted `parent_run_id` group; the second counts them as 27
separate standalone `+1`s.

Confirmed in the raw data: the `-demo` group carries **inconsistent**
`total_candidates` values `[1, 5]` (the exact anomaly `sr-g` built the
defensive-note path for, still visible in the project result's `notes`),
while `-demo-v2`'s group is a clean `[5]` with 27 orphan standalones.

**Fix**: both now report **6** selection trials and 27 sensitivity probes.

## Corrected accounting — real numbers from the real log

`check_project_combination_count("/…/runs/experiments.jsonl")`:

| family | purpose | raw (naive) | **selection trials (`N`)** | sensitivity probes | reproductions | dedup'd | data span | risk |
|---|---|---|---|---|---|---|---|---|
| `trend-momentum` | research | 452 | **97** | 355 | 51 | 46 | 2.12y | high |
| `mean-reversion` | research | 8 | **8** | 0 | 3 | 5 | 1.84y | low |
| `volume` | research | 4 | **4** | 0 | 2 | 2 | 1.84y | low |
| `funding` | research | 8 | **8** | 0 | 3 | 5 | 1.84y | low |
| `infrastructure` | infrastructure | 46 | **19** | 27 | 6 | 13 | 0.57y | high |

**Project totals: naive 518 → corrected 136**, of which **117 research**
and **19 infrastructure**; **382 sensitivity probes** excluded from `N`;
**65 reproductions** reported but not merged away.

Every figure the assigning brief predicted reproduced **exactly** — 518,
136, 117, 19, 382, and all five per-family raw/selection pairs. No
discrepancy to report.

`trend-momentum`'s 2.12-year span is wider than any single member's
because the family unions the 15m-era strategies' window
(`2025-11-16 → 2026-06-11`) with the 1h-era strategies'
(`2024-04-27 → …`). That is correct behaviour for a family-level span,
and worth noting only so a future reader doesn't mistake it for a bug.

### Which `N` to report: both, always

`check_project_combination_count` returns per-family results **and**
project-level totals, and the module documents why neither alone is the
answer. Bailey's `N` is "the number of trials from which this maximum was
selected". Within `trend-momentum` this project compared 97 momentum
variants against each other — but it also chose momentum over
mean-reversion, over the momentum/reversion blend, over volume, over
funding, **after seeing all of their results**. So the honest `N` for
"the best thing this project has found" (Configuration C) is the project
research total, **117**, not `trend-momentum`'s 97 and certainly not
`ensemble-momentum-configuration-c`'s laundered 10.

## What was built

### 1. `python/research/lineage.py` (new)

- **`FAMILY_BY_STRATEGY_ID`** — a **curated, hand-written** map, one entry
  per historical `strategy_id` (all 15), each carrying `family`, `purpose`
  (`"research"` | `"infrastructure"`), and a **citation** to the
  `.planning/sr-*.md` document that establishes the attribution.
- **`resolve_family(strategy_id, record=None) -> FamilyResolution`** —
  resolution order: (1) `record["strategy_family"]` when present →
  `source="logged"`; (2) the curated map → `source="curated_map"`;
  (3) fall back to the bare `strategy_id` as its own single-member family
  → `source="unmapped"`, with an attached note. **It never silently
  invents a family** — an unmapped id surfaces as its own family plus a
  visible note, which is the signal to add a curated entry.
- **`PURPOSE_BY_FAMILY`** is *derived* from the curated map at import
  time (and raises if a family ever acquires mixed purposes), rather than
  being a second hand-maintained copy that could drift.

**Why curated rather than derived — verified, not assumed.** The brief
claimed lineage is unrecoverable from the log; I checked it directly
before accepting it. Across all 1,839 records, every multi-fold
`run_walk_forward` record logs `params` as one of `{}`,
`{"symbol": "BTC-USDT"}`, or `{"candidates": [[15], [20], [25], [30]]}`.
The configuration actually under test — ADX thresholds, volatility-target
parameters, lookback pairs, risk fractions, `initial_signal_state` — lives
in the `Trainable`'s **constructor arguments**, which `run_walk_forward`
never receives and therefore never logs. Six `ensemble-momentum` /
`ensemble-momentum-configuration-c` records are byte-identical in
`params` + `walk_forward_config` + `data_range` while representing
materially different work. The claim holds: there is nothing in the log
to derive a family from.

### 2. Optional `strategy_family` logged field

`experiment_log.log_run` gained a keyword-only `strategy_family: str | None
= None`, threaded through `run_walk_forward`. **When `None` the key is
omitted from the record entirely** — deliberately *not* written as `null`
the way `parent_run_id` is.

This is functional, not cosmetic: "key absent" then unambiguously means
"pre-lineage record, attribute via the curated map", while "key present"
means "trust the record". A `null` would collide the two cases and make
the curated map's scope undecidable. Every pre-existing record is
unaffected and reads back identically.

### 3. `TrialKind` classification (replaces flat `+1`-per-record)

- **`SELECTION`** — a real selection trial: has a genuine `parent_run_id`
  (grid-search candidate), or is a multi-fold standalone walk-forward run.
  These are what `N` counts.
- **`SENSITIVITY_PROBE`** — excluded from `N`, reported separately.
- **`REPRODUCTION`** — a re-run of an already-counted configuration;
  reported separately, **never silently merged**.

`classify_trial_kind(record)` implements the first two. For historical
records it uses the **empirical rule** (standalone + exactly one fold ⇒
sensitivity probe), and its docstring states plainly that this is
*an empirical fact about this specific log, not a general invariant*,
along with the reconciliation evidence above. `REPRODUCTION` is
deliberately **not** returned by `classify_trial_kind`: whether a run
duplicates an earlier one is a property of the record's position in the
whole log, not of the record itself, so it can only be assigned during
aggregation.

**Counting unit.** A whole `parent_run_id` group counts once (weight =
its resolved `total_candidates`) — `sr-g`'s original insight, preserved:
a 19-fold walk-forward re-evaluates the *same* grid per fold, which is
walk-forward validation working as intended, not 19× the data snooping.
A standalone record counts as 1. A `sensitivity:`-prefixed group counts
its *records* (each is a distinct probe evaluation), since the
one-count-per-group rule exists for repeated-grid semantics that don't
apply to probes.

### 4. Self-describing sensitivity records going forward

At `walkforward.py`'s `check_parameter_sensitivity` call site,
`parent_run_id` is now `f"sensitivity:{run_id}"` instead of `None`.

**This supersedes `sr-g`'s documented compromise, and the supersession is
deliberate.** `sr-g` rejected grouping the probes under the *enclosing*
`run_id` for a real, empirically-discovered reason: it puts
single-candidate probes in the same `parent_run_id` group as the real
5-candidate grid, producing the inconsistent-`total_candidates` anomaly
that `check_combination_count` had to defensively flag (still visible in
the `-demo` record today). Its fix was `parent_run_id=None`.

A **prefixed distinct** id avoids that exact problem — it is a different
`parent_run_id` from the real grid's, so the group stays clean — while
also making the records self-identifying, which `None` never did. It
achieves both properties where `sr-g`'s fix achieved one. Once this
lands, the empirical single-fold rule becomes unnecessary for new records
(it stays, for the 382 historical ones).

`robustness.py`'s docstring, which documented the old `None` choice at
length, was updated to describe the new wiring rather than left to
contradict the code.

### 5. `check_project_combination_count(runs_path) -> ProjectOverfittingCheckResult`

Per-family `FamilyOverfittingCheckResult`s plus project-level totals split
by `purpose`. Warnings, never hard blocks — the established convention
(`research/holdout.py`, then `sr-g`).

**Backward compatibility (hard requirement, verified).**
`check_combination_count(strategy_id, runs_path)` keeps its exact
signature and behaviour. Confirmed against the real log for seven
`strategy_id`s, including the three `sr-g` published a table for:

| `strategy_id` | now | `sr-g`'s published value |
|---|---|---|
| `hourly_momentum` | 12, low, 1.84y | 12, 1.84y, low ✓ |
| `regime_momentum` | 12, moderate, 0.57y | 12, 0.57y, moderate ✓ |
| `task-c-e2e-verification` | 1, low, 0.57y | 1, 0.57y, low ✓ |
| `ma-crossover-task-g-sensitivity-demo-v2` | 33, high, 0.57y | 33, 0.567y, HIGH ✓ |

All 13 pre-existing `test_overfitting_check.py` tests pass unmodified.

## Honest limits of the `reproduction` heuristic

The fingerprint is `(family, params, walk_forward_config, data_range)`
where `params` is informative, falling back to
`(family, strategy_version, aggregate_metrics.mean_sharpe)` where it
isn't.

**"Informative" excludes `symbol` and `candidates`**, a refinement worth
stating explicitly because it is a small deviation from the brief's
literal wording. `symbol` is constant project-wide. `candidates` is the
grid *definition* a caller passed in, not the configuration the
`Trainable` was constructed with — and using it would have actively
produced a **false** merge, collapsing `ensemble-momentum` `v2`
(`mean_sharpe = -1.011`) into the Configuration C runs
(`mean_sharpe = 0.027`) purely because both logged the same
`{"candidates": [[15], [20], [25], [30]]}`. Routing those records to the
version+result fallback keeps them correctly distinct. Same reasoning as
Defect 2's: the real configuration is in the constructor.

**The fallback is a heuristic and can merge two genuinely distinct
configurations that scored identically.** Real cases in this log, stated
plainly rather than buried:

- **The obvious one the brief predicted**: eight records share
  `mean_sharpe = 0.02713372192476914` (seven under `ensemble-momentum`,
  one under `ensemble-momentum-configuration-c`). The brief said six; the
  real count is eight. They are genuinely the same Configuration C re-run
  across `sr-i`/`sr-j`/`sr-l`/`sr-n`, so flagging them is *correct* here —
  but only because `strategy_version` happens to separate the ones that
  differ. It is doing the right thing for a partly-lucky reason.
- **A real false positive, disclosed**: the funding-extremity family shows
  3 group-level reproductions across 4 runs. One of those "reproductions"
  is `sr-o`'s fold-boundary fix, which genuinely changed behaviour
  (7 → 14 trades) — but its `initial_signal_state` seeding lives in the
  constructor, so its logged child `params` are byte-identical to the
  pre-fix run's. The heuristic cannot see the difference. This is the
  same root cause as everything else in this document, and it is exactly
  why `reproduction` is **reported separately and never subtracted from
  `N` automatically**: a human can look at the 3 and reject one.
- Conversely it is sometimes *too* conservative: `funding-extremity-
  contrarian` `v1` and `v1-post-atr-fix` produced identical
  `mean_sharpe = -0.005415…` from what was effectively the same run, but
  are not merged because the version strings differ.

`deduplicated_selection_trials` exposes the merged view for a reviewer who
has checked the reproductions; `selection_trials` (the headline `N`) never
applies it unilaterally.

## Judgment calls resolved without asking

- **`reproduction` counts toward `N` by default.** Both readings of "do not
  silently merge" were plausible. Chosen because it is the only reading
  under which the brief's independently-derived target numbers (97/8/4/8/19)
  reconcile exactly — and because it is the conservative direction: a
  reviewer can always subtract using `deduplicated_selection_trials`, but
  a silently-deduplicated `N` cannot be un-merged after the fact.
- **A new `FamilyOverfittingCheckResult` type rather than reusing
  `OverfittingCheckResult`.** The existing class is keyed on `strategy_id`
  and its `to_dict()` shape is depended on by existing tests; widening it
  would have put the hard backward-compatibility requirement at risk for
  no real gain.
- **The double-count in the naive rule is preserved, not silently
  fixed.** Each 19-fold run logs *both* a `parent_run_id` group *and* its
  own standalone record, so a single walk-forward run contributes
  `total_candidates + 1`. That is arguably a fourth defect, but it is not
  one of the three this task was scoped to, and the brief's target numbers
  bake it in. Flagged here rather than changed — CLAUDE.md's "touch only
  what the task requires", and "flag pre-existing dead code instead of
  removing it unasked" applied to arithmetic.
- **Zero new dependencies.** `enum.StrEnum`, `json`, `dataclasses` — all
  stdlib.

## TDD

Tests were written first and confirmed failing before any production code
existed — verified by running them against the unmodified tree and
observing `ImportError: cannot import name 'lineage' from 'research'` and
`ImportError: cannot import name 'SENSITIVITY_PARENT_RUN_ID_PREFIX'`, then
`TypeError: run_walk_forward() got an unexpected keyword argument
'strategy_family'` once the import errors cleared.

**49 new tests**, all against **synthetic fixtures only** — never the real
log, matching every existing test in this repo:

- **`test_lineage.py`** (27, incl. a 15-way parametrization): the full
  three-step resolution order and its precedence; `null`-valued
  `strategy_family` degrading to the curated map; purpose derivation for
  known and unknown logged families; the curated map's own
  well-formedness (every entry has a `.planning/` citation and a valid
  purpose; no family has mixed purposes; the family set matches this
  document); `to_dict()` JSON-serializability.
- **`test_overfitting_check.py`** (+17, purely additive): `TrialKind`
  classification for all four record shapes; probes excluded from `N` and
  reported separately; renamed `strategy_id`s aggregating into one family
  (with an assertion that the *naive* per-id view still shows the
  laundered number); reproductions reported without being merged;
  distinct results **not** called reproductions; project totals split by
  purpose; logged family beating the curated map; an unmapped id becoming
  its own family with a note; grid groups counted once, not per record;
  `holdout_access` ignored; empty log; risk level driven by selection
  trials rather than the naive total; JSON-serializability; and an
  explicit backward-compatibility test pinning `check_combination_count`'s
  old behaviour on a probe-laden log.
- **`test_experiment_log.py`** (+2): `strategy_family` omitted entirely
  when `None`, written when supplied.
- **`test_walkforward.py`** (+3): the `sensitivity:`-prefixed
  `parent_run_id` (asserting it is present *and* distinct from the real
  grid's `run_id`, i.e. that `sr-g`'s concern stays solved);
  `strategy_family` omitted by default and threaded when supplied.

**Full suite: 749 passed** (700 before this task's branch point + 49 new).
Nothing regressed — confirmed with the complete unfiltered
`uv run pytest`, not just the new files.

## Deliberately out of scope

- **Computing the Deflated Sharpe Ratio itself** — that is `sr-q`. This
  task supplies the honest `N` it needs, nothing more.
- **Backfilling `strategy_family` into existing records.**
  `runs/experiments.jsonl` is an append-only audit trail; rewriting it
  would destroy exactly the history this task exists to measure. The
  curated map covers the historical records instead.
- **Changing CLAUDE.md's Eligibility Bar**, or making any of this part of
  its pass/fail logic. Purely diagnostic/advisory, same status as `sr-g`'s
  two checks.
- **Fixing the group-plus-standalone double count** in the naive rule —
  see "Judgment calls" above.
- **Re-running any strategy.** No backtest was executed by this task; the
  real log was read only.
