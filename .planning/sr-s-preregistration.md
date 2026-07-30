# Strategy Research Task S: pre-registration

## Why this task exists

`sr-r` closed out eight strategy attempts statistically and found that
**nothing survives**: 18 distinct configurations, 0 reaching DSR ≥ 0.95. The
project's best-ever result (Configuration C with funding P&L, mean annualized
Sharpe **+0.039**) lands at **DSR = 2.0e-05** against **117** research
selection trials, and would have needed an annualized Sharpe of **4.645** to
clear the bar — on a window whose own detection floor is **~1.21**.

The diagnosis was never about a strategy. It was that **the search budget was
spent without the count ever being honest**, so no future "winner" on that
data could be distinguished from best-of-N luck.

The agreed remedy had three parts. Two were already done:

| Task | What it fixed |
|---|---|
| `sr-p` | An honest `N` — family- and project-level, with sensitivity probes classified out, and the strategy-rename loophole closed. |
| `sr-q` | PSR/DSR implemented, stdlib-only — the statistic that consumes that `N`. |
| **`sr-s` (this task)** | **Makes it structurally impossible to quietly expand a search after seeing results.** |

Exactly one pre-registered attempt is planned against the virgin `1d` window
(`sr-t`). `N = 1` for that attempt is only defensible if it is *provable*, not
merely asserted. A committed, hashed, machine-checked specification is what
turns the assertion into evidence.

**This task ran no strategy research.** No backtest against real market data
was executed, `runs/experiments.jsonl` was neither read nor written, and the
`1d` holdout window was never loaded — see "Confirmation the 1d window was
not touched" below.

---

## Where the artifact lives, and why not `.planning/`

`configs/research/preregistrations/<preregistration_id>.json`, git-tracked.

`.planning/README.md` states its own rule plainly: a file there is "added in
the same PR as the work it plans", i.e. with or after the work, and it holds
prose narrative. A pre-registration inverts both properties:

- it is worthless unless committed **before** the work, and
- it must be **machine-readable**, because code checks a run against it.

`configs/research/` already holds exactly this shape — git-tracked JSON with a
substantial `rationale`, read by code (`holdout.json`, `holdout_1h.json`,
`holdout_1d.json`). That precedent is followed rather than a new location
invented. `configs/` is CODEOWNERS-matched, which is a feature here: the
directory whose contents gate research honesty is one a human is nominally
asked to review.

---

## The schema, field by field

Validated by `research/preregistration.py::validate_preregistration`, which
**raises** `PreregistrationError` (a `ValueError`) on anything missing,
mistyped or internally inconsistent. Unknown top-level keys are rejected;
`rationale` and `notes` are the two documented optional ones.

### Identity and provenance

| Field | Why it is required |
|---|---|
| `preregistration_id` | How a run, a log record and a reviewer refer to this file. Must equal the filename stem, checked in `load_preregistration` — otherwise two files can claim one registration. |
| `registered_at` | The claim that makes "before" meaningful. Cross-checkable against `git log` for the file. |
| `strategy_id` | **The id the run MUST log under.** Without it the log record cannot be tied back to the registration. |
| `strategy_version` | Pinned, so a "v2" re-run is visibly a different thing. |
| `strategy_family` | Required, and load-bearing for the amended Bar rather than decorative — see "How this composes with the Eligibility Bar" below. |
| `strategy_entry_point` | `module:attribute` inside the `research` package. Names the code that will run, so "which implementation" is not a later choice. |

### The hypothesis and its context

| Field | Why |
|---|---|
| `hypothesis` | One falsifiable paragraph. A hypothesis written after the fact is not a hypothesis. |
| `prior_art` | A non-empty list of non-blank strings. **Empty is rejected**: an empty list is ambiguous between "no prior art exists" and "nobody looked", and the honest entry for genuinely novel work is a string saying which. |
| `stopping_rule` | Pre-commits when searching ends. Without it, "one more configuration" is always available. |

### The data

`data.{symbol, interval, source, split, holdout_config_path, start_ms, end_ms, expected_bars}`.

`start_ms`/`end_ms` are an addition to the brief's list, and deliberate: the
runner needs a concrete range, and **the exact window is part of what is being
registered** — the declared detection floor and the trade-count floor are both
computed *from* it, so leaving it open would leave both unverifiable.
`expected_bars` is then cross-checked against what actually loads (a warning,
not a block — BingX retention is rolling).

`split` is `research` or `holdout`. The schema can express a holdout
confirmation because CLAUDE.md's single-window variant requires its criteria to
be "pinned *before* access, not chosen after" — which needs somewhere to pin
them. The **runner refuses to drive** a holdout registration; see "Two
refusals, and why only one is 'the hard block'".

### The grid — complete and enumerated

`parameter_grid` accepts two structurally-distinguished shapes (JSON objects
and arrays cannot be confused, so no discriminator key is needed):

```json
{"fast": [5, 8], "slow": [20, 30]}          → cartesian product, 4 candidates
[{"fast": 5, "slow": 20}, {"fast": 8, "slow": 30}]   → explicit, 2 candidates
```

The explicit form exists because a real grid is often **not** a full product
(e.g. a `fast < slow` constraint). Forcing such a grid into product form would
either register candidates that will never run, or silently understate the
count — and the count is the one number this whole task protects. Explicit
candidates must all declare the same parameter names, or the count is not
comparable.

`total_candidates` must **equal** the enumerated grid size. A declared count
that disagrees with its own grid would make the hard block meaningless, so
that inconsistency is a validation failure, not a warning.

`free_parameter_count` is the count of parameters fitted on *this project's*
data. Deliberately **not** derived from the grid dimensions: a parameter can be
fitted inside `fit()` without appearing in the grid, and a grid dimension can
be fixed by prior art rather than fitted. Zero is explicitly allowed and is the
strongest possible registration.

### The procedure

`procedure.{fee_bps, slippage_bps, bars_per_day, funding_included}` always;
plus `{train_bars, validate_bars, step_bars}` for a walk-forward registration
only. Fold geometry is **not** required for a holdout registration, because
CLAUDE.md's single-window variant drops every fold-based clause rather than
scaling it down — requiring meaningless numbers there would invite meaningless
answers.

### The criterion

`primary_criterion.kind` is `walk_forward_dsr` or `holdout_psr`, and the kind
must agree with `data.split`. This encodes the amended Bar directly: a
multi-fold research run is gated on the **Deflated** Sharpe Ratio against the
project-level `N`; a single-window holdout confirmation on the
**Probabilistic** Sharpe Ratio — deliberately PSR, not DSR, because the
holdout was never searched over (and `N`=1 makes DSR identical to PSR anyway).

A `holdout_psr` registration must additionally set
`require_sharpe_above_detection_floor: true`, which is the Bar's clause 3: an
observed Sharpe below the holdout window's own detection floor is reported
**not powered to confirm**, and clearing the other criteria does not
constitute confirmation. Registering `false` is rejected — that is not a
weaker preference, it is deleting an approved clause.

### The two fields carrying disproportionate weight

**`declared_detection_floor_sharpe`** and **`declared_power`**
(`{assumed_true_sharpe, probability, derivation}`, all three mandatory and
non-blank). These force writing down, *before* the run, how likely the
attempt is to detect the thing it hopes for. **That discipline is precisely
what was missing across all 117 prior trials** — `sr-r` had to compute the
detection floor retrospectively, and discovered it was above any plausible
real edge, after the entire search budget had been spent.

The committed example registration makes the point concretely: on the 1h
research window, power against a real **0.8** annualized Sharpe is **28.7%**.
Writing that down first would have ended the 1h research programme roughly
115 trials earlier.

**`outcome_interpretation`** pre-commits the narrative for each outcome region
so a disappointing result cannot be re-narrated afterwards. It requires
**three** regions, not two:

```text
PASS  /  INCONCLUSIVE  /  FAIL
```

A binary framing would be dishonest, and for a specific reason this project
has already lived: a real edge can plausibly fail to be detected. That is
exactly what `sr-r`'s `REJECTED-UNDERPOWERED` and
`INCONCLUSIVE-DATA-LIMITED` verdicts exist to say — "not shown" is not "shown
absent". Additional regions **are** allowed (the Bar's own holdout variant has
a fourth outcome, "not powered to confirm"); the three are a floor, not a
whitelist. `research/run_preregistered.py` prints all of them verbatim
alongside the numbers, which is the point of having pre-committed them.

`secondary_reported_not_gating` is required and non-empty: under the amended
Bar the t-test "may still be reported for continuity but is no longer a pass
criterion", so there is always at least one such statistic, and naming them
in advance stops a secondary number being promoted to a criterion after the
fact.

---

## Conformance to the approved Bar is validated; the research is not

Four numbers in a registration are not free choices — they restate the
human-approved Eligibility Bar's own constants. A registration declaring a
**laxer** value would be a non-human amending a human-approval-gated policy
(CLAUDE.md: the Bar has "the same status as Risk Parameters"). So validation
rejects laxer and accepts stricter:

| Field | Approved constant | Source |
|---|---|---|
| `threshold` | ≥ **0.95** | imported as `retrospective.DEFAULT_DSR_THRESHOLD`, not re-typed |
| `max_drawdown_ceiling` | ≤ **0.25** | Bar's "20-25%" band, lenient edge |
| `profit_factor_floor` | ≥ **1.3** | Bar's "1.3-1.5" band, lenient edge |
| `min_fold_consistency` | ≥ **0.80** | Bar's "80-90%" band, lenient edge |
| `sign_test_alpha` | ≤ **0.05** | project-wide α convention |
| `min_total_trades` | ≥ `frequency_scaled_min_trades(...)` | the Bar's own formula, below |

**Stricter is always accepted** — a researcher holding themselves to more than
the Bar is never a governance problem. This is conformance checking against an
approved constant, not an opinion about a hypothesis; `validate_preregistration`
validates the *artifact* and never judges the research.

### The frequency-scaled trade floor is implemented, not paraphrased

`frequency_scaled_min_trades(*, total_evaluated_bars, bars_per_day)` implements
the amended wording literally:

```text
max(30, min(100, floor(total_evaluated_bars / bars_per_day / 20)))
```

and is tested against **CLAUDE.md's own published table**, which it reproduces
exactly:

| Geometry | Evaluated bars | Floor | Matches CLAUDE.md |
|---|---|---|---|
| 1h, 19 folds × 720 | 13,680 | **30** | ✓ |
| 15m, 3 folds × 2,880 | 8,640 | **30** | ✓ |
| 1d, 822 research bars (`sr-t`) | 822 | **41** | ✓ |

For a walk-forward registration, `total_evaluated_bars` is derived from the
registration's own geometry via `walkforward.generate_folds` — the same
function the run will use, so the registered floor and the actual geometry
cannot disagree. For a holdout registration it is `expected_bars`.

### What is deliberately *not* enforced

The Bar's **"minimum 8-10 folds for the result to be considered credible"** is
reported, never enforced. `sr-t`'s 822-bar `1d` research window plausibly
cannot produce 8 folds at any sensible window sizing, and a criterion that a
data-limited window cannot satisfy would block the very attempt this machinery
exists to enable. `Preregistration.expected_fold_count` is derived and
`run_preregistered` **warns** below `ELIGIBILITY_BAR_MIN_FOLD_COUNT` — loud and
recorded, and the write-up must carry it, but not a gate.

This asymmetry is deliberate and worth stating: a *laxer restatement of an
approved threshold* raises, because the threshold is policy; a *geometry that
cannot reach an approved credibility floor* warns, because the data is a fact
about the world.

---

## Exactly one hard block, and why that one

`check_run_matches_preregistration(prereg, run_kwargs)` compares a run against
the registration over an explicit audit list (`COMPARED_RUN_FIELDS`: ids,
family, symbol/interval, fold geometry, `bars_per_day`, fees, funding
inclusion, holdout flag, candidate count).

**Everything warns** — `logging.warning` plus an entry in `mismatches`.
That follows this project's established convention of making a risky situation
loud and visible rather than silently preventing it (`holdout.py`'s
`force_reclaim_reason` override; `overfitting_check.py`'s "a warning only, not
a block"). It is also simply correct: legitimate reasons to deviate exist — a
registered specification re-run under a corrected fee assumption should be
*recorded*, not forbidden — and a tool that blocks them teaches people to work
around it.

**One thing raises**: `total_candidates` **exceeding** the registered value
(`GridExpansionError`).

Why that one, and only that one:

1. **It is the single failure mode pre-registration exists to prevent.**
   Searching more than was declared, after seeing results, is exactly what
   produced `sr-r`'s `N` = 117 — each extra trial invisibly raising the bar
   every future result must clear.
2. **It has no judgment in it.** Two integers, one comparison. Every other
   field's "is this deviation acceptable?" is a question about intent; this one
   is arithmetic.
3. **The direction is asymmetric.** Running *fewer* candidates than registered
   is a deviation but not an expansion — it cannot inflate `N` — so it warns.
   Absence of the field warns too (see the disclosed gap below).

### One residual gap, named rather than hidden

A caller who omits `total_candidates` **entirely** — or passes it as `None`,
which is this codebase's established "not a grid search" value, written by
`log_run` for every standalone run — is warned, not blocked. Absence is not an
exceedance, and the block is specifically about exceeding a registered number.
The omission is visible three ways: the warning, the `mismatches` list, and a
logged `total_candidates: null` on the record itself. Blocking on absence was
considered and rejected as the wrong trade: it would make the check unusable
for any caller legitimately reporting a single non-grid run, in exchange for
closing a hole that is already loud.

### One evasion route that was closed on review

A count that is *present but not an integer* — `"7"`, `7.0`, `True` — used to
fall through to the warning-only path, because `"7" > 6` never compares
greater. That was a real way around the one hard block, found by CodeRabbit's
review of this PR. It now raises `PreregistrationError` (not
`GridExpansionError`: nothing has been *shown* to be expanded — the claim
simply is not a candidate count). **This is a type check on the block's input,
not a second policy gate**, and it keeps the distinction the gap above rests
on: `None`/absent is a claim of nothing and warns; a non-count is a caller bug
and fails loud, per `_require_int`'s convention throughout this module.

### Two refusals, and why only one is "the hard block"

`run_preregistered` also **raises** for a `data.split == "holdout"`
registration. That is a *scope guard on the runner*, not a match check:
driving a holdout confirmation from a general-purpose runner would make
spending an untouched window a one-command accident, when it is supposed to be
a single deliberate act with a single-access claim on record
(`holdout.load_holdout_klines`) and a different criterion (PSR, not DSR). The
"exactly one hard block" claim is about `check_run_matches_preregistration`'s
comparison surface, and this refusal is not on it.

---

## `warn_if_uncommitted`: one deliberate strengthening of the brief

The brief specified a best-effort `git diff --quiet -- <path>`, with
`experiment_log._git_head_sha`'s defensive `try/except (OSError,
SubprocessError)` shape. The implementation keeps the shape and the
never-blocks semantics, but uses
**`git status --porcelain --ignored -- <path>`** instead. Both departures are
based on a direct probe of real repositories rather than assumption:

> An **untracked** file produces no `git diff` at all. `git diff --quiet`
> therefore reports "clean" for a registration that **was never committed** —
> the strongest possible version of exactly the failure this check exists to
> catch. `git status --porcelain` reports it as `??`.

> **`--ignored` is load-bearing** (added on CodeRabbit's review of this PR,
> and the finding was correct). A **gitignored** file — or any file inside an
> ignored directory — produces *empty* plain porcelain output, so
> `git status --porcelain` alone also calls it "clean". This repo gitignores
> `runs/` and `python/data/var/`, so a registration dropped into either would
> have been reported as committed while being invisible to git entirely.

Probed behaviour, per state, reproduced in the module docstring:

```text
tracked, unmodified   -> ""                  -> True
tracked, modified     -> " M <path>"          -> False
untracked             -> "?? <path>"          -> False
ignored file          -> "!! <path>"          -> False
inside ignored dir    -> "!! <dir>/"          -> False
not a git checkout    -> CalledProcessError   -> None
```

Never a raised exception, never a block — the recorded sha is the durable
record, and this check exists to make a soft failure visible, not to prevent
it.

---

## The two new logged fields

`preregistration_id` and `preregistration_sha256`, threaded
`run_walk_forward` → `log_run`.

- **Additive, and omitted when `None`** — matching the convention `sr-p`
  established for `strategy_family`, for the same functional reason: *absent*
  must unambiguously mean "this run was not made under a pre-registration",
  which a `null` would collide with "made under one, but nobody recorded
  which". Every existing record in `runs/experiments.jsonl` reads back
  byte-for-byte identically, and every existing caller of `log_run` /
  `run_walk_forward` is unaffected.
- **The sha is of the file's raw bytes at run time.** Bytes, not parsed
  content: the point is that *any* later edit — including one that changes no
  field — is detectable by re-hashing the file and comparing against the log.
  Demonstrated: a whitespace-only edit moves the sha.
- **Supplied together or not at all.** A record claiming a pre-registration
  with no integrity hash looks like provenance while proving none; a hash with
  no id cannot be attributed. This is the same "they describe the same thing,
  so half of them is a caller mistake" rule
  `eligibility._resolve_moments` already applies to skewness/kurtosis.
  Enforced in `experiment_log.require_complete_preregistration_pair`, called
  by **both** `log_run` (so no writer can bypass it) and `run_walk_forward`'s
  entry point (so the failure happens *before* folds are backtested rather
  than at logging time, when a completed run's results would be thrown away).
  That entry-point duplication follows the precedent set by
  `run_walk_forward`'s own `bars_per_day` check, whose comment explains the
  identical reasoning.

---

## The runner, and the property that is the entire mechanism

`python -m research.run_preregistered <path>` reads the grid **from the
registration file**. There is no code path in it that accepts a grid, a
candidate count, a fee or a fold geometry from anywhere else. Expanding a
search therefore requires editing a git-tracked file — visible in `git log`,
and visible in the `preregistration_sha256` recorded on every affected run.

**A consequence worth stating rather than leaving as a puzzle**: on this path
the hard block can *never* fire, because the count it compares is derived from
the same file it is compared against. That is not the block being useless — it
is the stronger guarantee. The block exists for the *other* path: a
hand-written script calling `run_walk_forward` directly while claiming a
`preregistration_id`. One route makes expansion impossible by construction;
the other makes it loud.

Kept thin, per the brief. It resolves a strategy, loads the registered window
through the existing research loader, checks the run, and calls
`run_walk_forward`. Two contracts it defines and nothing more:

- **`strategy_entry_point`** resolves to something callable with exactly
  `(strategy_id, strategy_version, fee_bps, slippage_bps, runs_path)` — which
  is the signature this project's existing `Trainable` classes already have. A
  strategy needing anything else exposes a small module-level factory with
  this signature rather than widening the contract.
- **`params`** carries the grid as `params["candidates"]` (value lists in
  declared parameter order — the shape this project's `fit()` implementations
  already consume; the real log records e.g.
  `{"candidates": [[15], [20], [25], [30]]}`), plus `parameter_names`,
  `total_candidates` and `preregistration_id`. Both derived from one function
  (`candidate_rows` calls `enumerate_candidates`) so the readable enumeration
  and the shape handed to a strategy cannot drift apart.

It **does not assign a verdict**. It prints the numbers next to the
pre-committed interpretation regions and says so explicitly. Evaluating the
criterion stays with `research/eligibility.py` and
`research/overfitting_check.py`; a runner that also graded itself would be the
same conflation the Bar's own "PROPOSED, not adopted" discipline avoids.

### `funding_included` is observed, not restated (fixed on review)

The first implementation built `run_kwargs["funding_included"]` by copying
`procedure["funding_included"]` straight out of the registration — which made
that one comparison **trivially self-satisfying**, so it could never catch
anything. CodeRabbit caught it, and the finding was correct.

It is now derived from the funding series that will **actually** be applied
(`bool(funding_rates)` after resolution), which required moving the
`check_run_matches_preregistration` call to *after* data loading. Two real
conflicts it now catches, both previously silent:

- a caller **injecting** `funding_rates` under a registration declaring
  `funding_included: false` — funding P&L applied under a specification that
  denies it;
- a registration declaring `true` whose window loads **no funding rows at
  all** — a funding-inclusive claim that is not inclusive in fact.

Both warn rather than block, like every mismatch except the candidate count.
The cost of the reorder is that the candidate-count block would now fire after
a sqlite read rather than before — immaterial, since on the runner's path it
cannot fire at all by construction.

---

## How this composes with the amended Eligibility Bar

The Bar (amended and human-approved 2026-07-29) is **not** modified by this
task. Nothing here edits it; this task encodes it. Three specific joints:

1. **`strategy_family` is mandatory in a registration** — and that is
   load-bearing, not tidiness. The amended clause 2 says a DSR computed
   against an `"unmapped"` family resolution "is not admissible as an
   Eligibility Bar pass", because an unmapped id resolves to its own
   single-member family, **understating** `N` and thereby *inflating* DSR.
   A registration that names its family guarantees the run can pass
   `strategy_family=`, so `resolve_family` returns `source="logged"` and the
   fail-closed clause is satisfied structurally rather than by someone
   remembering.
2. **`primary_criterion.kind` mirrors the Bar's two shapes** — DSR against the
   project-level `N` for a multi-fold run, PSR for the single-window holdout
   confirmation — and the kind must agree with `data.split`, so the two cannot
   be mixed.
3. **`criteria_pinned_at_claude_md_revision`** is mandatory and non-blank.
   This is the Bar's "in force at the time means pinned *before* access, not
   chosen after" clause, made mechanical: the registration records which
   CLAUDE.md revision its criteria come from, and a later change to any of
   them is visibly not the one the run was judged under.

---

## Confirmation the 1d window was not touched

Part of this task's correctness, not a footnote. CLAUDE.md is explicit that
the `1d` early-window holdout stays reserved until a real specification is
committed for it (`sr-u`, after `Gate 2`).

- **No committed registration targets `1d`.** The single example registration
  is `1h`, `split: research`, `strategy_family: infrastructure`.
- **`configs/research/holdout_1d.json` is never read** by any code added here.
  It appears twice in `preregistration.py` — both times in the module
  docstring, citing `configs/research/` as the precedent for where the
  artifact lives.
- **No test reads real data.** Every fixture is synthetic (tmp-path
  registrations, tmp-path sqlite caches, tmp-path holdout configs, tmp-path
  experiment logs, and a real tmp `git init` repo for the commit check). The
  one exception reads *committed config only* — `test_every_committed_
  registration_validates` loads the example registration file, which is a
  git-tracked JSON config, not market data.
- **The worktree has no `python/data/var/` and no `runs/`** (both gitignored,
  absent from a fresh worktree), so the real kline cache and the real
  experiment log were not openable even accidentally.
- The end-to-end CLI demonstration ran against a **synthetic 200-bar sqlite
  fixture** in a scratchpad directory, writing to a scratchpad experiment log.

The example registration is also deliberately *safe to run*: it is
`strategy_family: "infrastructure"`, which `research/lineage.py` maps to
`purpose=infrastructure`, so running it could not inflate the research `N` —
and CLAUDE.md's standing rule on the spent 1h window explicitly still permits
"infrastructure testing … none of which select a configuration". It has never
been run.

---

## What was built

| File | Change |
|---|---|
| `python/research/preregistration.py` | **new.** Schema constants, `validate_preregistration`, `load_preregistration`/`Preregistration`, `file_sha256`, `enumerate_candidates`/`parameter_names`/`candidate_rows`, `frequency_scaled_min_trades`, `warn_if_uncommitted`, `check_run_matches_preregistration`/`PreregistrationCheckResult`, `PreregistrationError`, `GridExpansionError`. |
| `python/research/run_preregistered.py` | **new.** `run_preregistered`, `build_strategy`, `resolve_entry_point`, `main(argv)`. |
| `configs/research/preregistrations/example-ma-crossover-infrastructure-demo.json` | **new.** One committed reference instance; creates the directory; asserted valid by a test. |
| `python/research/experiment_log.py` | `log_run` gains the two optional fields (omitted when `None`) + `require_complete_preregistration_pair`. |
| `python/research/walkforward.py` | `run_walk_forward` gains and threads the two optional fields; validates the pair at its entry point. |
| `python/tests/test_preregistration.py` | **new**, 162 tests. |
| `python/tests/test_run_preregistered.py` | **new**, 14 tests. |
| `python/tests/test_experiment_log.py` | +4 tests (omitted-when-`None`, written-when-supplied, half-specified rejected ×2). |
| `python/tests/test_walkforward.py` | +4 tests (same four, at the harness level). |

**Zero new dependencies** — stdlib `hashlib`, `json`, `logging`, `subprocess`,
`argparse`, `dataclasses`, `decimal`, `itertools`, `pathlib`, plus
`importlib` for entry-point resolution.

Every dataclass is frozen with a `to_dict()`; every consciously-chosen
argument is keyword-only; validation failures always name the offending field
— matching `research/eligibility.py`, `research/overfitting_check.py` and
`research/retrospective.py`.

### One layering decision worth recording

`validate_preregistration` deliberately does **not** re-validate grid
alignment of `[start_ms, end_ms)`. `data/_grid.py`'s own docstring scopes it to
`python/data/`, and nothing outside that package imports it (verified:
`backfill.py`, `store.py`, `bingx_klines.py`, and its own test). Duplicating
its interval table into `research/` — to fail milliseconds earlier than
`data.store.fetch_klines` already fails loudly on the same input — would
create exactly the drift that module exists to prevent. The check is
*relocated, not lost*, and
`test_a_misaligned_window_still_fails_loudly_from_the_data_layer` proves that
path still fires.

---

## TDD

Tests were written first and confirmed failing against the unmodified tree —
`ModuleNotFoundError: No module named 'research.preregistration'` for the two
new files, and 6 real assertion/`TypeError` failures for the additive logged
fields:

```text
FAILED test_log_run_writes_both_preregistration_fields_when_supplied
FAILED test_log_run_rejects_a_half_specified_preregistration_pair[kwargs0/1]
FAILED test_run_walk_forward_threads_the_preregistration_fields_into_the_record
FAILED test_run_walk_forward_rejects_a_half_specified_preregistration_pair[kwargs0/1]
```

One test drove a real change rather than confirming one:
`test_growing_the_grid_is_the_one_hard_block` asserted the raised message
names the offending field, and the first implementation's message did not —
so `GridExpansionError` now leads with `total_candidates:`, which is what
makes it greppable in a log.

Two tests were **revised after being written**, disclosed rather than quietly
deleted: `test_a_grid_misaligned_window_is_rejected_at_registration_time` and
`test_an_unknown_interval_token_is_rejected` were replaced once the
`data._grid` layering decision above was made. They became
`test_grid_alignment_is_deliberately_left_to_the_data_layer` (asserting the
documented behaviour) plus the end-to-end test proving the data layer still
rejects it. The change was to the *design*, made explicit, not to a test that
was inconveniently failing.

**Full suite: 1,108 passed** (924 on `main` at the branch point + 184 new).
Nothing regressed; nothing was skipped or xfailed. (1,095 at first push; the
four review fixes below added 13 more tests.)

### CodeRabbit review: four findings, all four accepted

The review requested changes on the exact HEAD sha, and every finding was a
real defect rather than a style preference. Recorded here because two of them
were genuine holes in the mechanism this task exists to provide:

| Finding | Verdict | Fix |
|---|---|---|
| `warn_if_uncommitted` reads a **gitignored** file as clean | **Real hole.** Confirmed by direct probe: ignored files produce empty porcelain output. | `--ignored` added; 2 tests (ignored file, ignored directory). |
| A non-integer `total_candidates` slips past the hard block | **Real hole.** `"7" > 6` never compares greater, so a quoted count reached the warning-only path. | Raises `PreregistrationError`; 8 parametrized tests + 1 for the `None` case that must still only warn. |
| `funding_included` copied from the registration into its own comparison | **Real hole.** The comparison was self-satisfying. | Derived from the resolved series; check moved after data loading; 2 tests. |
| Four fenced blocks in this document lack a language | Valid lint. | `text` added, matching CLAUDE.md's own convention. |

Two of these are worth stating plainly: a check that cannot fail is worse than
no check, because it reads as coverage. Both the funding comparison and the
ignored-file case were exactly that, and neither would have been caught by the
tests as originally written.

### Coverage of what the brief asked for specifically

| Required | Where |
|---|---|
| validation raising on each individually-missing required field | 19 top-level (parametrized) + 8 `data` + 7 `procedure` + 3 `declared_power` + 3 outcome regions, each its own case |
| the `total_candidates` hard block firing | `test_growing_the_grid_is_the_one_hard_block`, `..._fires_even_when_every_other_field_matches`, `..._names_both_numbers` |
| *only* that one blocking, others warning | `test_every_non_grid_mismatch_warns_and_never_raises` — 15 parametrized fields, each asserted to return with a mismatch and a log record |
| `warn_if_uncommitted` when git is unavailable | `test_git_being_unavailable_warns_and_returns_none` (monkeypatched `FileNotFoundError`), `test_a_path_outside_any_git_checkout_returns_none`; plus a real tmp `git init` repo for clean / edited / never-committed |
| the sha computed from file bytes and recorded | `test_the_sha_is_computed_from_file_bytes_so_an_edit_changes_it`, `test_the_runner_drives_a_registration_end_to_end_and_logs_its_provenance` |
| the two logged fields genuinely additive | 4 tests in `test_experiment_log.py` + 4 in `test_walkforward.py`; the rest of both suites unchanged and passing |
| a runner round-trip on a small synthetic registration | `test_the_runner_drives_a_registration_end_to_end_and_logs_its_provenance`, `test_the_grid_reaching_the_strategy_comes_from_the_registration_file`, `test_editing_the_grid_in_the_file_is_the_only_way_to_change_it`, plus 3 CLI tests through `main(argv)` |

---

## End-to-end demonstration (synthetic)

Real `python -m research.run_preregistered` invocation against a synthetic
200-bar sqlite fixture and a synthetic registration, in a scratchpad
directory. Abridged output:

```text
WARN warn_if_uncommitted: could not ask git about .../sr-s-cli-demo.json
     (returned non-zero exit status 128) -- cannot confirm the pre-registration
     is committed; the recorded sha256 remains the durable record
WARN run_preregistered: pre-registration 'sr-s-cli-demo' declares a geometry
     producing 6 fold(s), below CLAUDE.md's 8-10 fold credibility floor.
     Reported, not blocked ...

pre-registration : sr-s-cli-demo
  sha256         : a640f2d0de9ed3613e830272d6a69ae74e431dfa2bae06ae0fda3ff05d5c651a
  candidates     : 4 registered
  criterion      : walk_forward_dsr >= 0.95
  detection floor: 7.6 annualized Sharpe (declared before the run)
run_id           : 7cceb3a5-...
  folds          : 6      total trades : 9
pre-committed outcome interpretation (written before this run):
  PASS: ...  INCONCLUSIVE: Below the 30-trade floor: no conclusion either way.  FAIL: ...
This runner does NOT assign a verdict.
```

Both warnings fired correctly and neither blocked. The logged record carried
`preregistration_id`, `preregistration_sha256` (matching the file on disk),
`total_candidates: 4`, `strategy_family: infrastructure`, and
`params.candidates: [[2,5],[2,9],[3,5],[3,9]]` — read from the file, not from
a call site. 24 grid-candidate child records all reported
`total_candidates: 4`.

Then, separately verified:

- **The hard block fires**: claiming 12 candidates against a registered 4
  raises `GridExpansionError` naming both numbers.
- **Every other deviation warns and returns**: a renamed `strategy_id`,
  zeroed `fee_bps`, changed `train_bars`, flipped `funding_included`, and a
  *smaller* candidate count each produced a warning and a `mismatches` entry,
  and none raised.
- **Editing the committed grid moves the sha**
  (`a640f2d0…` → `56206dde…`), while the already-logged run keeps the old one
  — so the two are distinguishable after the fact, which is the whole point.

---

## Deliberately out of scope

- **A registration for the real upcoming `1d` attempt.** That is `sr-u`, after
  a human decision (`Gate 2`). Writing a placeholder here would either spend
  the reservation or leave a file a future reader could mistake for the real
  specification.
- **Loading the `1d` holdout window.** See above.
- **Driving a holdout confirmation run.** The schema can express one so its
  criteria can be pinned before access; the runner refuses to drive one.
- **Evaluating the criterion.** The runner reports; `eligibility.py` and
  `overfitting_check.py` judge. A runner that graded itself would be a
  conflation this project has deliberately avoided since `sr-q`.
- **Amending CLAUDE.md.** The Bar was human-approved on 2026-07-29; this task
  encodes it and does not touch it. No file in this PR edits the Bar's
  wording.
- **Enforcing the 8-10 fold credibility floor.** Reported and warned; see
  "What is deliberately *not* enforced" for why enforcing it would block the
  `1d` attempt this task exists to enable.
- **Retrofitting pre-registrations onto historical runs.** They were not
  pre-registered; recording that they were is the one thing this machinery
  must never make possible.
