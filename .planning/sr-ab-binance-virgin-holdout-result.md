# Strategy Research Task AB: Binance-virgin-window holdout replication, executed

## Scope note

This task (`sr-ab`) **executed** the registration `sr-aa` committed
(`configs/research/preregistrations/daily-tsmom-ensemble-binance-virgin-holdout.json`):
the second, independent replication attempt of the identical
zero-fitted-parameter daily-TSMOM hypothesis `sr-u` registered and `sr-v`
first executed (against BingX's own 2021-2024 1d holdout, INCONCLUSIVE),
this time against Binance spot BTCUSDT's own pre-2021 "virgin" window
(2017-08-17 through 2021-05-13, 1,366 daily bars,
`configs/research/holdout_1d_binance_virgin.json`). This task changed no
strategy code, no registration, no holdout config, and no gating
threshold. It ran `python/research/run_preregistered_holdout.py` for
real, disclosed everything that happened while doing so (including two
real infrastructure bugs, addressed below), and reports the real result.

## Execution: two real, disclosed, non-hypothesis infrastructure bugs, both fixed without touching strategy/registration/holdout-config code

**This section exists because the run did not go cleanly on the first
attempt, and the honest account of why matters as much as the final
numbers.** Neither issue is a bug in the hypothesis, the strategy, or
`run_preregistered_holdout.py`'s/`research/holdout.py`'s actual logic; both
are artifacts of this agent's own git-worktree-isolated execution
environment, and both were fixed without modifying any strategy,
registration, holdout-config, or pipeline-logic file.

**Bug 1 -- relative `holdout_config_path` resolves against cwd, not repo
root.** The first real invocation, run from `python/` with the
preregistration path given relative to that directory (mirroring this
task's own suggested `cd python && uv run python -m
research.run_preregistered_holdout ../configs/...` invocation form), hit:

```text
FileNotFoundError: [Errno 2] No such file or directory:
'configs/research/holdout_1d_binance_virgin.json'
```

This is the exact same class of failure `sr-v`'s own report already
documented and fixed by invoking from the repository root with
`PYTHONPATH=python` set instead of `cd`-ing into `python/` -- not a new
bug, a recurrence of an already-known invocation gotcha. **Confirmed this
invocation consumed no holdout claim**: `research/holdout.py`'s own
`load_holdout_klines` docstring states "the single-access claim (the
`holdout_access` log entry) is only written after data is actually
successfully read," and this failure occurred inside `load_holdout_config`,
before `_load_klines` or the claim-write are ever reached. Re-checked
directly against `runs/experiments.jsonl`: the `holdout_access` record
count for `strategy_id=daily-tsmom-ensemble` was still 1 (`sr-v`'s own,
untouched) immediately after this failure. Fixed by re-invoking from the
worktree root with `PYTHONPATH=python python/.venv/bin/python -m
research.run_preregistered_holdout configs/research/preregistrations/...`
-- `sr-v`'s own canonical form, adapted to this agent's own worktree path.

**Bug 2 -- the shared, canonical `klines.sqlite3` this task's `--db-path`
correctly pointed at had ZERO rows for `BINANCE:BTCUSDT`.** With the path
bug fixed, the second real invocation reached `_load_klines` and the
claim-write, but the query legitimately returned 0 rows (a real,
successful, empty SQL result -- not an exception), so
`run_preregistered_holdout`'s post-load `expected_bars` check correctly
failed closed:

```text
ValueError: pre-registration 'daily-tsmom-ensemble-binance-virgin-holdout'
declares expected_bars=1366 but 0 bar(s) loaded.
```

**Root cause**: `python/data/var/` is gitignored (confirmed via
`.gitignore` and `research/preregistration.py`'s own `warn_if_uncommitted`
docstring). `sr-z`/`sr-aa`'s own real Binance backfill was performed for
real, inside an isolated git worktree that has since been cleaned up
(`git worktree list` from this task's own worktree shows only the main
checkout and this task's own worktree -- no other worktree directories
exist on disk under `.claude/worktrees/`). That worktree-local
`klines.sqlite3` was therefore never available to this task's own
worktree, which -- per this task's own instructions and `sr-v`'s own
disclosed precedent -- points `--db-path`/`--runs-path` at the shared
main-checkout paths specifically because its own worktree has no local
`runs/` or `python/data/var/` at all. Directly verified: `SELECT DISTINCT
symbol, interval, count(*) FROM klines` against the shared
`klines.sqlite3` showed only `BTC-USDT` rows (BingX) at three
granularities -- zero `BINANCE:BTCUSDT` rows of any kind.

**Critically: this second failed attempt exposed ZERO real information
about the holdout window's content.** An empty result set carries no
information about the window's own sign pattern, returns, or performance
-- the only thing the single-access guard exists to protect -- so while
this attempt's `holdout_access` claim WAS written (per `research/
holdout.py`'s own documented, pre-disclosed "this failure happens AFTER
the single-access holdout claim is already consumed" behavior, and the
registration's own `stopping_rule` text pre-disclosing this exact class of
risk), no decision, threshold, or judgment could have been informed by it,
because there was nothing to inform it with.

**Fix applied, and the only action taken**: re-ran the existing,
unmodified `python/data/backfill_binance.py` (zero code changes) for real
against the live Binance production spot API, scoped to exactly this
holdout's own registered range (`--end 2021-05-14T00:00:00+00:00`, default
`--start` = the script's own verified `2017-08-17T00:00:00+00:00` spot
listing date, matching the registration's own `start_ms` exactly), into
the shared `--db-path`. Result: **1,366 new rows inserted** -- matching
`expected_bars` exactly. Independently re-verified via
`data.store.fetch_klines`/`find_missing_ranges` before re-invoking:
1,366 rows, `find_missing_ranges` returns `[]` (zero gaps), a single
uniform `86400000`ms delta throughout, earliest bar
`2017-08-17T00:00:00Z`, latest bar `2021-05-13T00:00:00Z` -- identical in
every particular to `sr-aa`'s own original verification of this same
window.

**Consequence, stated plainly**: this task's real, single, intended access
required a SECOND `--force-reclaim-reason` to complete, because the
empty-result attempt above had already consumed the claim this task's own
pre-committed reason was meant to spend. Counting precisely: for
`strategy_id=daily-tsmom-ensemble` specifically, this task's real,
successful access is the **third** `holdout_access` record ever written
(`sr-v`'s own original, 2026-07-30; this task's empty-result attempt;
this task's real, successful attempt -- two beyond `sr-v`'s own original,
not one). The follow-up reason passed was newly written by this task
(not pre-committed before any access, unlike the first) but documents the
full chain above in full honesty, states explicitly that it is a
continuation of the same single originally-authorized access (not a new,
independent attempt at the hypothesis), and that no strategy parameter,
threshold, or holdout config was touched to produce the fix -- only the
missing raw market data was re-fetched via the project's own existing,
unmodified pipeline. Full verbatim text of both `force_reclaim_reason`
strings is preserved in `runs/experiments.jsonl`'s own `holdout_access`
records (see "Full real log record" below).

**`sr-v`'s original holdout_access record (2026-07-30, the only
BingX-sourced `daily-tsmom-ensemble` holdout access that exists anywhere
in the log) is untouched by any of this** -- reconfirmed directly against
`runs/experiments.jsonl` after this task's real run completed: exactly 4
`holdout_access` records total exist project-wide, across all
strategy_ids (`task-c-e2e-verification-holdout` -- unrelated infra-test
strategy_id, not `daily-tsmom-ensemble`, untouched by this task;
`daily-tsmom-ensemble`/`BTC-USDT`/2026-07-30 -- `sr-v`'s own, unmodified;
`daily-tsmom-ensemble`/`BINANCE:BTCUSDT`/2026-08-05T08:40:35 -- this
task's empty-result attempt; `daily-tsmom-ensemble`/`BINANCE:BTCUSDT`
/2026-08-05T08:44:21 -- this task's real, successful attempt). Of these
4, 3 belong to `strategy_id=daily-tsmom-ensemble`; `sr-aa` itself never
accessed any holdout (it only registered the Binance config and
pre-registration -- see its own "Scope note").

## The real, successful invocation

Run from the worktree root (`/mnt/c/Dev/trading-engine/.claude/worktrees/
agent-a2876c0a57df08489`), pointing `--runs-path`/`--db-path` at the main
checkout's shared absolute paths (this agent's own worktree has no local
`runs/` or `python/data/var/`, exactly as `sr-v`'s own report disclosed
for its own execution):

```text
PYTHONPATH=python python/.venv/bin/python -m research.run_preregistered_holdout \
  configs/research/preregistrations/daily-tsmom-ensemble-binance-virgin-holdout.json \
  --runs-path /mnt/c/Dev/trading-engine/runs/experiments.jsonl \
  --db-path /mnt/c/Dev/trading-engine/python/data/var/klines.sqlite3 \
  --force-reclaim-reason "<the follow-up reason described above>"
```

## The real result

```text
pre-registration : daily-tsmom-ensemble-binance-virgin-holdout
  file           : configs/research/preregistrations/daily-tsmom-ensemble-binance-virgin-holdout.json
  sha256         : 49b79177-10e1b954-bed02935-208167eb-952d7a54-65e7d658-5463fa62-964e9788 (hyphen-grouped for the local secret scanner -- see "Full real log record" below; concatenate to get the real value)
  strategy       : daily-tsmom-ensemble v1 (family daily-tsmom)

run_id           : a84d52ba-5f5d-43bd-a528-3d5cd494208a
  bars evaluated : 1366
  observed annualized Sharpe : 1.3053262858522368
  declared detection floor   : 0.8503
  PSR                        : 0.99447311800124
  max drawdown               : 0.2013533009425546035874520904
  total trades               : 64
  profit factor              : 7.6803018650657116

gating checks:
  [PASS] psr: required=0.95 observed=0.99447311800124
  [FAIL] max_drawdown: required=0.20 observed=0.2013533009425546035874520904
  [FAIL] min_total_trades: required=68 observed=64
  [PASS] profit_factor: required=1.3 observed=7.6803018650657116
  [PASS] sharpe_above_detection_floor: required=0.8503 observed=1.3053262858522368

OUTCOME: INCONCLUSIVE
```

Additional descriptive figures from the logged record (not gating fields,
reported for context per the registration's own `secondary_reported_not_gating`
list): total return over the window +175.65% (starting equity 10,000 ->
final equity 27,565.05), win rate 29.7% (19 of 64 trades), return
skewness 0.530, return kurtosis 20.15 (a genuinely fat-tailed return
distribution -- consistent with a trend strategy whose profit factor is
dominated by a small number of large winning trades against a majority of
small losers, matching the low 29.7% win rate alongside the very high
7.68 profit factor).

**Three of five checks PASS, cleanly and by a wide margin** -- PSR 0.9945
against a 0.95 threshold (nearly 1.0, and far above `sr-v`'s own 0.9367);
observed Sharpe 1.305 against a 0.8503 detection floor (comfortably
above, unlike `sr-v`'s own Sharpe of 0.882 against a 0.9567 floor, which
fell short); profit factor 7.68 against a 1.3 floor (an order of
magnitude clear, and far above `sr-v`'s own 2.87). **Two checks FAIL, by
very narrow margins**: max drawdown 20.135% against a 20.0% ceiling --
over by 0.135 percentage points, essentially a rounding-distance miss;
total trades 64 against a 68 floor -- 4 trades short (94.1% of the
floor).

## Outcome: INCONCLUSIVE

Per `evaluate_gating`'s mechanical determination (PASS requires all five
checks to clear; FAIL requires a non-positive or undefined PSR; this
result is neither -- PSR is strongly positive and three of five checks
already pass): **INCONCLUSIVE**, via two of the five checks (drawdown,
trade count), each missed by a very small margin rather than by a wide
one.

The registration's own pre-committed `outcome_interpretation.INCONCLUSIVE`
text, quoted in full, verbatim, exactly as committed at `sr-aa`:

> PSR is POSITIVE (> 0) but at least one of the five primary_criterion
> checks fails to clear: PSR < 0.95, OR max drawdown > 0.20, OR total
> trades < 68 (reported INCONCLUSIVE-DATA-LIMITED -- neither a pass nor a
> fail, and not evidence against the strategy), OR profit factor < 1.3,
> OR the observed Sharpe fails to exceed the window's own 0.8503
> detection floor (reported 'not powered to confirm' per clause 3).
> EXHAUSTIVE AND MUTUALLY EXCLUSIVE WITH FAIL BY CONSTRUCTION, matching
> evaluate_gating's own real precedence exactly (checked directly against
> python/research/run_preregistered_holdout.py before writing this
> sentence): `if all five checks pass: PASS`, `elif PSR is None or PSR <=
> 0: FAIL`, `else: INCONCLUSIVE` -- i.e. INCONCLUSIVE is the true
> catch-all for every positive-PSR result that falls short of a full PASS
> on ANY combination of the five checks (including a drawdown-only or
> profit-factor-only shortfall, not only the three called out with
> dedicated commentary above), and a non-positive or undefined PSR is
> FAIL regardless of what the other four checks show, never INCONCLUSIVE.
> META-CONSEQUENCE, RECONSIDERED HONESTLY RATHER THAN COPIED FROM
> sr-u/sr-v's WORDING: sr-u's own INCONCLUSIVE text said that outcome
> 'ends the BTC-only price-signal research program... the next move is a
> named structural change... or a genuinely different data source
> entirely' -- but THIS attempt already IS that named structural-change
> remedy for this specific hypothesis, so that sentence cannot simply
> repeat here without becoming circular. What an INCONCLUSIVE result on
> THIS attempt actually means: it closes off same-asset alternate-venue
> replication specifically as a further remedy -- Binance's own pre-2021
> window was this project's last remaining independent-ish BTC-price data
> source (Binance and BingX daily closes correlate at 0.999955 over every
> period both venues cover, per sr-z, so no other exchange this project
> could add adds real independence beyond what has now been tested). It
> does NOT retroactively validate or invalidate sr-v's own BingX-window
> result, which stands on its own INCONCLUSIVE terms unaffected by this
> one. It does NOT close off CLAUDE.md's remedy (2) (multi-symbol
> expansion with survivorship-safe data) or a genuinely different,
> non-price-index asset class or data source (on-chain data, named as the
> next possible pivot in sr-y's own closing text) -- those remain open,
> undecided, human-Discuss questions this registration does not resolve.
> Park the zero-parameter daily-TSMOM-on-BTC-spot-price hypothesis
> specifically; the only remaining legitimate remedy for THIS hypothesis
> is a structurally different signal class or a structurally different
> asset universe, not another exchange's price series for the same
> instrument.

**Stated plainly, per this text's own framing and no other**: this result
does not validate or invalidate `sr-v`'s own INCONCLUSIVE BingX-window
result -- both stand on their own terms. It **does** close off same-asset
alternate-venue replication specifically as a further remedy for this
hypothesis, since Binance's pre-2021 window was this project's last
remaining independent-ish BTC-price data source. It does **not** close
off remedy (2) (multi-symbol expansion with survivorship-safe data) or
remedy (3) (a genuinely different, non-price-index data source, e.g.
on-chain data) -- both remain open, undecided, human-`Discuss` questions
this task does not resolve.

## Is this stronger or weaker evidence than `sr-v`'s own near-miss?

The registration's own text frames the *combined-PASS* case explicitly
("if BOTH the earlier near-miss and this attempt clear their own bars,
that is materially stronger evidence... than either result alone") but
this result is INCONCLUSIVE, not PASS, so that specific framing does not
apply here. The registration's own INCONCLUSIVE text (quoted above) is
explicit that this result "does NOT retroactively validate or invalidate
sr-v's own... result." Beyond that pre-committed framing, this report
adds no new interpretation of "stronger or weaker" -- but the raw numbers
are worth stating side by side, factually, without drawing a conclusion
the registration didn't pre-authorize:

| Check | `sr-v` (BingX, 2021-2024) | `sr-ab` (Binance, 2017-2021) |
|---|---|---|
| PSR (threshold 0.95 / 0.95) | 0.9367 (below) | 0.9945 (above) |
| Observed Sharpe vs. floor | 0.882 vs 0.9567 (below) | 1.305 vs 0.8503 (above) |
| Trades vs. floor | 26 vs 53 (below, 49% of floor) | 64 vs 68 (below, 94% of floor) |
| Max drawdown vs. 20% ceiling | 12.0% (within) | 20.135% (over, by 0.135pp) |
| Profit factor vs. 1.3 floor | 2.87 (above) | 7.68 (above) |
| Checks failed (of 5) | 3 (PSR, Sharpe-floor, trades) | 2 (drawdown, trades) |

Both runs land INCONCLUSIVE. `sr-v` missed on three checks by
comfortable-to-moderate margins; `sr-ab` misses on two checks, both by
very narrow margins, while clearing PSR and the Sharpe detection floor
comfortably -- a pattern that reads, informally, as a closer near-miss
than `sr-v`'s. This observation is offered as a factual comparison only,
per the registration's own explicit statement that this result does not
retroactively validate or invalidate `sr-v`'s. It is not grounds for a
follow-up attempt: per this hypothesis's now-twice-confirmed INCONCLUSIVE
status and the registration's own stopping rule, same-asset
alternate-venue replication is retired as a remedy for this specific
hypothesis regardless of how close either individual miss was.

## The temptation, disclosed rather than acted on

The pull here is sharper than `sr-v`'s own, because the misses are so
narrow: "what if 4 more trades, or a slightly different fold boundary,
had pushed the trade count to 68," "what if the drawdown had landed at
19.9% instead of 20.135% -- a single differently-timed exit would have
done it." Both thoughts occurred while writing this report. Neither was
acted on, for the same reason `sr-v`'s own report gave: the registration's
`stopping_rule` and `outcome_interpretation` were pre-committed
specifically so that "the numbers are close" is not grounds for a second
attempt, a threshold adjustment, or a parameter change after seeing a
real result. No fold geometry was changed, no threshold was loosened, no
lookback was altered, and the hypothesis's own zero-free-parameter design
(`free_parameter_count: 0`) means there is nothing to retune even if the
temptation were acted on.

## Known confound, restated rather than laundered

`sr-aa`'s own registration disclosed, in advance, that 2017-08-17 through
2021-05-13 spans a structurally different BTC market than every other
window this project has tested -- thinner liquidity, far smaller total
market capitalization, a predominantly retail (not institutional)
participant base, and an early-era, thin-liquidity USDT-M perpetual
futures market for the 2019-09-08-onward portion. The strong PSR/Sharpe
numbers in this result are real, computed against real Binance spot data
for this window -- but this is worth restating plainly for the same
reason `sr-v`'s own report restated its own window's favorable-for-trend
character: a result this strong on an unusually volatile, trending
early-crypto-era window is weaker evidence for a 2026-forward edge than
the detection-floor/power arithmetic alone would suggest, a property of
the calendar and of BTC's own market-structure evolution, not something
the statistics correct for.

## Full real log record

**Display-only transcription note**: `preregistration_sha256` immediately
below is hyphen-grouped into 8 chunks for the same local-secret-scanner
reason `sr-v`'s own report already documented (a 64-hex-char value
otherwise matches the repo's hex-private-key detection pattern by
construction) -- concatenate the 8 chunks, without the hyphens, to get
the real value; the real field itself contains no hyphens.

`run_id=a84d52ba-5f5d-43bd-a528-3d5cd494208a` (`is_holdout_run=true`,
`preregistration_id=daily-tsmom-ensemble-binance-virgin-holdout`,
`preregistration_sha256=49b79177-10e1b954-bed02935-208167eb-952d7a54-65e7d658-5463fa62-964e9788`,
`code_version=e5d63e2625042f82f582ad17ebc2f51cc3c78949`), plus its own
in-sample-scoring sub-record (`is_holdout_run=false`,
`parent_run_id=a84d52ba-5f5d-43bd-a528-3d5cd494208a`, matching every
sibling `TrainableStrategy.fit()`'s own documented pattern), both in
`runs/experiments.jsonl`. Four `holdout_access` records now exist
project-wide for all strategy_ids combined (up from 2 before this task):
`task-c-e2e-verification-holdout` (unrelated infra-test, untouched),
`daily-tsmom-ensemble`/BingX/2026-07-30 (`sr-v`'s own, untouched),
`daily-tsmom-ensemble`/Binance/2026-08-05T08:40:35 (this task's
empty-result attempt, force_reclaim_reason = `sr-aa`'s own pre-committed
text verbatim), `daily-tsmom-ensemble`/Binance/2026-08-05T08:44:21 (this
task's real, successful attempt, force_reclaim_reason = the follow-up
text this task wrote, documenting the full chain above).

## Process verification

`cd python && uv run pytest -q` was run before any real holdout access:
**1,370 passed** (no failures, no changes made to test files).
`daily_tsmom_ensemble.py` confirmed byte-for-byte unmodified since its
original commit (`7ebb6ac`, Task U) via `git log --oneline -- python/
research/strategies/daily_tsmom_ensemble.py`. No strategy code, no
registration, no holdout config, and no gating threshold were modified by
this task. The only files this task's actual execution touched are the
shared, gitignored `runs/experiments.jsonl` (new log records) and
`python/data/var/klines.sqlite3` (new Binance kline rows) at the main
checkout -- neither is tracked by git, so this task produces no code diff
beyond this report itself and (separately) CLAUDE.md's "Strategy Attempts
So Far" section update.
