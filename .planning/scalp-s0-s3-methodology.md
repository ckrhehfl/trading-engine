# BTC scalping research, Tasks S0–S3 — retrospective methodology record

**Retrospective consolidation, not an original planning document** — the
same category of artifact as `.planning/00-*.md` through `06-*.md`, and
as `.planning/kis-phase1-venue-integration.md`. See
`.planning/README.md`'s "Where does a design belong: CLAUDE.md, or here?"
for the rule this file exists to satisfy.

Scalping Tasks **S4 onward each got their own `.planning/` doc** as they
happened (`scalp-s4-vwap-mid-reversion.md` and its `-result.md`,
`scalp-s5-binance-1m-orderflow-infra.md`, `scalp-s6-ofi-momentum.md` and
its `-result.md`, `scalp-s7-backtest-insolvency-floor.md`). **S0 through
S3 did not.** Their entire record — the original design write-up, the
real 1-minute retention probe, the execution-cost realism gate and the
sourcing behind its constants, and the statistical-methodology decision
that shaped every scalping run since — existed only inside CLAUDE.md
until this file was written.

That matters more than a normal documentation gap, because S2 and S3 are
not narrative history. They set **standing constraints** that every later
scalping task obeyed:

- **S2** fixed `FEE_BPS = 5` (re-verified, not inherited) and
  `SLIPPAGE_BPS = 10` for scalping `GUARDED_MARKET` preregistrations,
  restricted scalping candidates to `GUARDED_MARKET` execution only (a
  policy exclusion, not an engine limitation), and defined the
  cost-gate mechanics (`PF > 1.0`, Sharpe > 0) including the rule that a
  cost-disqualified candidate is still logged and still counts toward
  the project-level selection-trial count `N`.
- **S3** established `bars_per_day = 1440`, computed the real detection
  floors, and made the design decision — with the human operator — that
  1-minute scalping uses a **single whole-window pre-registered
  holdout** rather than a research/holdout split, together with the
  fail-closed `known_gaps` pre-access check.

**Fidelity**: the two extracts below the horizontal rules are CLAUDE.md's
own text reproduced **verbatim**, pulled mechanically with `sed` rather
than retyped or summarized, so no figure, citation, hedge, or disclosed
limitation can have been altered in transit. Both are preserved in
accumulated form — original text plus every correction layered on by
real CodeRabbit review — so they read as designs with amendments rather
than clean final specs. That is the real history and is deliberately not
tidied up.

**Two things to read alongside this file, deliberately left where they
are:**

1. The **real 1-minute retention numbers** S1 produced (BingX BTC-USDT
   `1m`: 631.98 days, 910,040 bars, 2 confirmed real gaps) live in
   CLAUDE.md's "Exchange API Facts — BingX" section, which stays in
   CLAUDE.md as durable operational reference rather than moving here.
   The same applies to S5's Binance figures under "Exchange API Facts —
   Binance".
2. The **standing statistical rules** S3 leans on — the Eligibility Bar,
   the Holdout confirmation (single-window variant), the frequency-scaled
   trade-count floor, the detection-floor formula — are project-wide
   policy under "Strategy Research Methodology" and stay in CLAUDE.md.
   S3 applied them to 1-minute bars; it did not replace them.

**Outcome of the arc these four tasks set up**, recorded here so this
file is not read as more hopeful than the evidence supports: both
candidates that ran under this methodology returned **INCONCLUSIVE** —
`vwap-mid-reversion` (S4) and `ofi-momentum` (S6) — for two genuinely
different reasons, and both 1-minute holdout windows are now spent. The
appendix at the end of this file records the investigation that ruled out
the two remaining named candidates between S4 and S5.

---

## Extract 1 — CLAUDE.md's Scalping Strategy Research section, S0 through S3

Verbatim, `sed -n '2301,2825p' CLAUDE.md`. Covers the section preamble
(scope decision, the backtest-vs-live-trading question, the codebase
investigation, and the external research behind the candidate list),
then Tasks S0, S1, S2, and S3.

### Scalping Strategy Research — planned, not yet started

Design committed 2026-08-24 per `.planning/README.md`'s "design lives
in CLAUDE.md before work begins" rule. A new, second research direction
alongside `daily-tsmom-ensemble`, human-approved 2026-08-24: **retail
scalping** (minutes to tens of minutes holding period) on BTC-USDT.
Explicitly requested as a
methodology-first effort — several earlier ad hoc "find me a strategy"
conversational attempts had already failed, and the human operator
asked for a real research methodology to be established before any
candidate signal work begins, grounded in a real investigation of this
codebase (not assumed).

**Scope, decided, not to be silently widened**: 1-minute bars and
coarser only. No tick/trade-level data, no true HFT — stays inside this
file's own permanent "HFT/co-location/tick-level strategies" non-goal
(see "Non-goals" above). "Tens of seconds" was explicitly considered and
rejected for this phase — it would need an entirely new trade/tick
data-collection layer this project doesn't have and isn't building now.

**A real, concrete question the human operator asked, worth recording
the answer to precisely**: is a backtest signal ("enter at HH:MM at
price P, exit at HH:MM:SS at price P'") meaningfully different from live
trading? Two separate concerns, conflated in the question but real and
distinct: (1) look-ahead bias — already prevented structurally by this
codebase's bar-by-bar `KlineWindow` (see "Look-ahead-bias protection"
above), unaffected by timeframe. (2) **Execution realism** — the real
gap for scalping specifically (see the fill-model finding below): the
signal must be net-positive after *realistic* fees and slippage, not
the exact theoretical fill a backtest optimistically assumes.

**Codebase investigation, confirmed by direct inspection before any task
breakdown** (three parallel Explore-agent passes, 2026-08-24 — not
assumed from memory):

1. **The statistical harness (`python/research/walkforward.py`,
   `eligibility.py`, `preregistration.py`, `overfitting_check.py`,
   `lineage.py`, `holdout.py`) already generalizes to minute bars with
   zero code changes.** `bars_per_day` is an explicit, required
   parameter threaded end-to-end — a deliberate result of the earlier
   1h-variant refactor that pulled a hardcoded 15m assumption out. DSR,
   PSR, the frequency-scaled trade-count floor
   (`frequency_scaled_min_trades`), fold generation, and experiment
   logging are all pure bar-count/statistics functions with no
   hardcoded timeframe anywhere. Every strategy module already declares
   its own `DEFAULT_BARS_PER_DAY` constant (`96`=15m, `24`=1h, `1`=1d);
   a scalping strategy needs its own (`1440` for 1m), following the
   identical established pattern — not a new one.
2. **The data layer needs one real line of code, plus a real,
   currently-unknown fact.** `python/data/_grid.py`'s `INTERVAL_MS`
   dict wires up only `15m`/`1h`/`1d` (`5m` is named-but-unwired per its
   own docstring; `1m` isn't named at all). Adding `"1m": 60_000` is
   structurally trivial — the SQLite cache schema
   (`python/data/store.py`) has no CHECK constraint on `interval`, and
   both exchange clients already pass `interval` through as a free
   string. **But BingX's real 1-minute retention has never been
   probed** — no figure exists anywhere in this file. The `5m` figure
   itself (~3 months, see "Exchange API Facts — BingX" above) is only a
   binary-search estimate, never confirmed via a full backfill. This is
   the single biggest open unknown determining whether this whole
   effort is viable at all.
3. **The backtest engine's fill/fee model is the real, load-bearing
   risk for scalping specifically** — the honest answer to the question
   above. `python/backtest/fill.py`: fees and slippage are flat basis
   points on notional (slippage only applied to `GUARDED_MARKET`
   orders, never limit orders); there is no order-book depth, spread,
   or liquidity modeling of any kind; a limit order fills at 100% the
   moment a bar's high/low merely *touches* the limit price, with no
   volume/queue-position awareness. This barely matters at daily bars;
   it is a materially bigger source of backtest-to-live divergence at
   scalping bar sizes. The existing daily strategy's calibration
   (`FEE_BPS=5`, `SLIPPAGE_BPS=2`, `python/live/generate_daily_signal.py`)
   is very likely to understate real scalping costs if reused
   unexamined — nothing in the codebase currently prevents that reuse.
4. **Real, current (2025-2026) academic research on candidate signal
   sources** — found via live search specifically to avoid repeating
   this project's own prior pattern of narrow, purely-factor-style
   candidate generation (moving-average/momentum/mean-reversion), which
   is a poor fit for scalping timescales anyway:
   - **VWAP-to-mid deviation short-term reversion**: real literature
     support exists, but precisely scoped rather than claimed as direct
     proof for this project's own planned implementation (tightened on
     real CodeRabbit review): arXiv:2602.00776 ("Explainable Patterns in
     Cryptocurrency Microstructure") analyzed **Binance Futures
     order-book/trade data at 1-second frequency**, through October
     2025, and found VWAP-to-mid deviations show "asymmetric effects
     coherent with short-lived pressure and microstructure reversion."
     This project's own Task S1/S4 would use **BingX 1-minute OHLCV
     kline bars** — no order-book data, a coarser frequency, a different
     venue — as a necessary proxy (e.g. a rolling volume-weighted price
     over kline closes standing in for a true tick-level VWAP, and
     kline close standing in for mid-price). The paper's finding is real
     evidence the *mechanism* (VWAP-to-mid reversion) exists in crypto
     perpetuals microstructure; it is **not** evidence that this
     project's specific 1-minute-OHLCV-proxy implementation on BingX
     will show the same effect — that remains a hypothesis Task S4 must
     test for real, not an imported result. Task S4's own preregistration
     must state the exact proxy definition and cite this gap explicitly,
     not imply direct transferability.
   - **Order flow imbalance (OFI)**: real support exists, but with an
     important, honest caveat directly relevant to scalping — "The
     Quarter-Hour Effect" (2026 arXiv, Binance USDT perpetuals) found
     opening order imbalance predicts returns over **4-12 hours**, with
     "much weaker effects at finer clock-time frequencies." A BitMEX
     XBTUSD study similarly found the OFI-price relationship holds
     mainly "over large enough time intervals." OFI's real published
     support is weaker, not stronger, at the fine frequencies retail
     scalping targets — deliberately not treated as an easy scalping
     win below.
   - **Liquidation cascades**: real minute-bar academic work exists
     (two 2025-2026 arXiv papers analyzing the real October 2025 $19B
     and November 2025 cascades), but the actual finding is about
     **pre-cascade early-warning signals** (rolling variance, lag-1
     autocorrelation buildup before a cascade) — not "trade the
     cascade's own momentum after it starts," a naive framing
     deliberately avoided below.

**Task breakdown** (own `.planning/scalp-*.md` doc per task, own PR
each). Python research/backtest-infra work (Tasks S1/S2) is not
CODEOWNERS-matched — per this project's existing Auto-merge Policy for
non-risk Python paths (no live-order risk), that means CI **and** a
completed, non-pending CodeRabbit review passing is sufficient to
auto-merge, not CI alone; this section itself and its Task S3
follow-up are CODEOWNERS-matched (`CLAUDE.md`) — stop-and-ask,
matching the KIS documentation PRs' own precedent this same day.

- **Task S0** (this section) — design write-up, before any code.
- **Task S1** — 1-minute BTC-USDT data infrastructure
  (`_grid.py`'s `INTERVAL_MS`) — **done, PR #108.** `python/tests/
  test_grid.py`'s existing assertions were updated in place (an
  exact-wired-set assertion, not a standalone "`1m` raises
  `ValueError`" case) to cover `1m` alongside every prior interval.
  **Real retention probe result: GO, not a formality-cleared gate but a
  genuinely good one.** Binary search first (matching the established
  methodology — and, unusually, exact on the first try: the estimate
  reproduced bar-for-bar against the real backfill), then a real, full
  backfill with an independently-confirmed gap count. `1m` retention is
  **631.98 days** (910,040 bars, 2 small real gaps confirmed via retry —
  see "Exchange API Facts — BingX" above for the full result) —
  materially deeper than the pre-probe worst-case fear ("days to a
  couple of weeks") and, surprisingly, deeper than both `15m` and `5m`
  despite being the finest granularity checked. Walk-forward folds on
  real backtested history are viable; the live-paper-trading-only
  fallback path this task's own go/no-go was written to trigger is not
  needed. Fold geometry for Task S3 can proceed from this real number.
  **A new prerequisite for Task S3/S4, surfaced by this task's own real
  gap count**: neither `walkforward.py`'s fold generation nor the
  backtest engine's bar iteration detects a timestamp gap in the
  underlying kline sequence today (both are pure positional/bar-count
  arithmetic) — never a live issue for the always-zero-gap `1d`/`1h`
  data this project has used so far, but `1m` has 2 confirmed real gaps
  (see "Exchange API Facts — BingX" above for the exact windows). Task
  S3/S4 must not silently assume this is handled — either add real
  gap-aware validation before any `1m` walk-forward run, or explicitly
  verify the chosen research/holdout window against the known gap list
  first (and re-check after any future backfill re-run, since more
  gaps could exist further back or appear on a rerun).
  **Still open, undecided**: BingX only, or also probe Binance (which
  has shown deeper `1d` retention than BingX — unconfirmed whether that
  holds at `1m`, and now lower-priority given BingX's own `1m` depth
  already exceeds what a scalping walk-forward plausibly needs).
- **Task S2** — execution-cost-first realism gate, the methodologically
  most important task — **done, PR #110.** Research real, current
  BTC-USDT spread/slippage
  behavior at 1-10 minute holding periods (cited, not invented) before
  picking `fee_bps`/`slippage_bps` for any scalping preregistration —
  never reuse the daily strategy's `5`/`2` bps figures without explicit
  justification. **A real constraint tightened on CodeRabbit review of
  the PR that added this section**: raising `slippage_bps` is only a
  real, meaningful lever for `GUARDED_MARKET` candidates — per finding 3
  above, `fill.py` applies slippage *exclusively* to `GUARDED_MARKET`
  orders; a `LIMIT` order still fills 100% at the exact limit price the
  instant a bar's high/low touches it, completely unaffected by
  `slippage_bps`, no matter how high it's set. Declaring a "higher"
  `slippage_bps` for a limit-order candidate would silently do nothing
  and produce a false sense of conservatism. **Scalping candidates
  researched under this task are therefore restricted to
  `GUARDED_MARKET` execution only, for now** — **a policy exclusion,
  not a claim the engine lacks `LIMIT` support** (clarified on real
  CodeRabbit review: `fill.py::simulate_fill` genuinely implements a
  real `LIMIT` branch and will produce a deterministic result for one —
  the point below is that this project chooses not to trust that
  result for scalping validation, not that it can't be computed at
  all). A `LIMIT`-order-based scalping candidate is explicitly out of
  scope until `fill.py`'s limit-order fill model itself is hardened
  (order-book depth, partial-fill/queue-position, adverse-selection
  awareness — the "real, larger undertaking" already named as a
  disclosed possible follow-up, not committed to here); until then,
  treat any `LIMIT`-order scalping result as unverifiable under this
  engine's current optimistic 100%-fill-on-touch model, not merely
  conservative.
  **This gate must run and produce a real, disclosed pass/fail before
  any walk-forward/DSR statistical validation** — a `GUARDED_MARKET`
  candidate that isn't net-positive under a conservative, cited
  `slippage_bps` is disqualified regardless of any statistical
  significance a raw backtest might show, the same ordering discipline
  already applied to the KOSPI200 contract-multiplier conversion
  running before `RiskLimits.canary()`'s percentage check. **This
  `GUARDED_MARKET`-only restriction is a research/backtest *scope*
  decision only — not a live- or paper-order-submission approval of any
  kind** (clarified explicitly on real CodeRabbit review, so it can
  never be misread as one): clearing this gate, or any later
  walk-forward/DSR/holdout gate, never bypasses the Live Entry
  Criteria's own separate, still-unverified "market-order guard
  enabled" requirement (see that section above — real per-call
  verification against a live account has not happened for
  `GUARDED_MARKET` on either adapter yet), and every real order,
  scalping or otherwise, still must pass through the Java Trading
  Plane's `RiskGateway` in full, exactly as this file's Non-negotiable
  Rules already require.

  **Real cost research completed 2026-08-25** (three real searches, not
  invented numbers — citations below). Two separate cost components,
  held to different confidence levels since one is a precisely
  documented exchange fact and the other is a reasoned estimate:

  - **`fee_bps` — confirmed, no change needed.** BingX's and Binance's
    own published VIP0 (base-tier retail) taker fee for **USDT-M
    perpetual futures** — this project's own product scope, and what
    scalping's `GUARDED_MARKET` orders actually trade — is **0.05% =
    5bps** on both venues, an exact match to the existing `FEE_BPS=5`
    this project's daily strategy already uses
    (`python/live/generate_daily_signal.py`). Unlike slippage, an
    exchange's taker fee is a fixed percentage of notional regardless of
    holding period, so there is no scalping-specific reason to raise it
    — `FEE_BPS=5` is reused for scalping preregistrations **as a
    confirmed fact re-verified for this use, not an unexamined default
    carried over** (the distinction this task exists to enforce).
    **Product-scoped deliberately, not a blanket "both venues" claim**
    (tightened on real CodeRabbit review): Binance's and BingX's own
    **spot** VIP0 taker fee is a different, higher **0.10% = 10bps** —
    irrelevant to this project's perpetual-futures-only scope, but a
    real, disclosed discrepancy this research surfaced in an unrelated,
    already-completed task: `configs/research/preregistrations/daily-
    tsmom-ensemble-binance-virgin-holdout.json` (`sr-aa`/`sr-ab`, a
    **Binance spot** backtest) used `fee_bps=5` — the futures rate, not
    spot's real 10bps — understating that run's real costs. That
    holdout access already happened and its INCONCLUSIVE result is
    already logged and disclosed on its own terms; editing the spent
    config now would misrepresent history rather than fix anything, so
    it is disclosed here rather than silently corrected in a PR whose
    actual scope is scalping's own cost gate, not `sr-ab`'s.
  - **`slippage_bps` — must rise materially above the daily default's
    `2`, for real, cited reasons.** Real BTC-USDT typical daily-average
    bid-ask spread is on the order of **~0.04% = 4bps** (general
    market-data sources), corroborated by rigorous recent academic
    measurement: "The Extremity Premium: Sentiment Regimes and Adverse
    Selection in Cryptocurrency Markets" (arXiv 2602.07018) validates
    spread estimators against real 90-day Bybit L2 order-book data and
    61-day Binance effective-spread data (Oct 2025-Jan 2026), confirming
    BTC spreads are both rigorously measurable and materially consistent
    cross-exchange. That same body of work (and the general
    market-microstructure literature) also confirms spreads widen during
    volatile regimes — exactly the kind of short-term price dislocation
    a scalping signal is, by construction, more likely to trigger around.

    `fill.py`'s own model applies `slippage_bps` as a price displacement
    from the *next bar's open* for a `GUARDED_MARKET` order, in the
    direction against the trader (see its module logic) — so the number
    chosen here must cover not just the bid-ask spread itself, but also
    the gap between "next bar's open" and the price a real market order
    would actually pay, including volatility-driven widening around the
    trigger moment. **Recommended: `SLIPPAGE_BPS = 10` for scalping
    `GUARDED_MARKET` preregistrations** — roughly 2.5x the cited ~4bps
    typical spread. This multiplier is **not itself a citation** — it is
    a deliberately conservative reasoned estimate (moderate confidence,
    disclosed as such rather than presented as an exact figure): ~4bps
    for the spread itself, plus a margin for volatility-regime widening
    and the open-vs-achievable-price gap above. A candidate that can't
    stay net-positive after `FEE_BPS=5` + `SLIPPAGE_BPS=10` per round
    trip (~15bps one-way, ~30bps round trip — non-trivial relative to
    scalping-scale price moves) is disqualified before any statistical
    validation, regardless of raw Sharpe.
  - Same discipline as the Risk Parameters/Eligibility Bar above: any
    future revision of these two constants needs its own
    re-justification here, not silent per-strategy tuning to make a
    marginal candidate pass.

  **Concrete gate mechanics**: a scalping candidate's `GUARDED_MARKET`
  backtest, run with `FEE_BPS=5`/`SLIPPAGE_BPS=10` (or a
  candidate-specific higher figure, itself justified — never lower
  without new evidence), must show a mean profit factor **greater than
  1.0** (not merely positive — tightened on real CodeRabbit review:
  profit factor is gross-profit/gross-loss, a ratio of two non-negative
  magnitudes, so it is arithmetically *always* ≥0; "positive" excludes
  nothing, and a `0 < PF < 1` candidate — net cost-losing — would pass
  a bare positivity check) and a positive mean Sharpe. The Sharpe check
  *is* already a real net-of-cost check, not merely directional —
  `fill.py`'s fees/slippage are applied to the fill price itself,
  before the return series used to compute Sharpe is ever built, so
  Sharpe>0 already means net-positive after `FEE_BPS`/`SLIPPAGE_BPS`.
  A fold's `profit_factor: null` (zero trades, or zero losing trades)
  is interpreted per this file's own existing Eligibility Bar convention
  above, not a new rule invented here. This cost gate is deliberately a
  separate, earlier, cheaper screen than the full Eligibility Bar's own
  later profit-factor floor (1.3-1.5, a cushion for backtest-to-live
  mismodeling on top of a candidate that has already cleared
  walk-forward/DSR) — `PF>1.0` here only establishes "not obviously
  cost-negative," relying on `SLIPPAGE_BPS=10`'s own built-in
  conservatism (already ~2.5x the cited real spread) rather than
  duplicating the stricter downstream floor.

  **Two different execution shapes need two different orderings for
  this gate, stated explicitly rather than left to the runner's
  judgment** (tightened on real CodeRabbit review, which correctly
  pointed out the original single "before any walk-forward/DSR test"
  wording doesn't fit a single-window holdout at all): for a
  **research-split, walk-forward candidate** (the shape every non-1m
  timeframe uses, and any future scalping candidate that *does* get a
  reusable research split), the gate runs on the research folds and
  must pass **before** any walk-forward/DSR significance test is run
  against those same folds — real iterative access exists here, so a
  cheaper cost-only pass genuinely can precede the fuller statistical
  one. For a **single pre-registered holdout candidate** (1m scalping's
  own design per Task S3 above — see `daily-tsmom-ensemble`'s
  `sr-u`/`sr-v`/`sr-aa`/`sr-ab` precedent), there is no separate prior
  access to run the gate against without spending the one-time holdout
  itself — the gate criteria (`PF>1.0`, Sharpe>0) are instead evaluated
  **from that same single holdout run**, as an additional required
  criterion alongside the Eligibility Bar's existing Holdout confirmation
  (single-window variant) checks (PSR≥0.95, drawdown, trade count,
  profit-factor floor), not as a temporally separate backtest. If the
  cost-gate criteria fail on that one access, the result is reported as
  **cost-disqualified**, regardless of what PSR says — a high PSR on a
  cost-disqualified holdout run would be an artifact of the assumed
  fee/slippage figures, not evidence of a tradeable edge, so cost
  disqualification takes reporting priority over a PSR-based pass. Task
  S4's own preregistration must state which of these two shapes it uses
  and, if the single-holdout shape, restate this ordering explicitly
  rather than silently inherit it.

  This is the same ordering discipline already established for the
  KOSPI200 contract-multiplier conversion running before
  `RiskLimits.canary()`'s percentage check. A candidate failing this
  gate is reported as cost-disqualified, never run through DSR at all —
  but **its backtest run must still be logged via `log_run`, not
  silently discarded** (a real gap closed on CodeRabbit review, which
  traced `research/overfitting_check.py::check_project_combination_count`
  and confirmed it counts every logged `total_candidates` regardless of
  cost-filter outcome, with no cost-status field to exclude one): a
  cost-disqualified candidate was still a real attempt to find a viable
  configuration, and excluding it from the project-level `N` would
  understate how much searching actually happened — the same
  "default to overstating rather than understating selection bias"
  direction this file's Eligibility Bar clause 2 already applies to an
  unrecognized `strategy_family`. What the cost gate saves is a DSR
  *trial's* worth of downstream computation and interpretation on a
  candidate that can't clear realistic costs — not its contribution to
  `N`, which is preregistered here as always counted, matching the same
  reasoning behind the standing rule against further searching on the
  spent 1h window.
- **Task S3** — statistical methodology addendum. **Resolved with a
  real, computed finding (2026-08-25), not guessed — the finding
  changed the design, so this is more than "mostly documentation" turned
  out to be.** `python/research/eligibility.py`'s PSR/DSR machinery
  resamples equity curves to **daily** granularity before computing
  significance (`psr_from_equity_curve`'s default `SAMPLING_DAILY`; the
  module's own docstring: "the detection threshold depends on calendar
  span, not sampling frequency (~`1.6449/sqrt(years)`)") — confirmed by
  reproducing this project's own already-published 1h (1.84y → 1.213 ≈
  "~1.21") and 1d-holdout (2.95y → 0.958 ≈ "~0.96") numbers exactly from
  this one formula. **Consequence, real and load-bearing**:
  `bars_per_day=1440` only changes how many raw bars get resampled
  *into* each daily point — it does not change the number of daily
  points, which is bounded by calendar days regardless of native bar
  frequency. Using the *entire* 631.98-day 1m retention window (1.7302
  years) gives a detection floor of **1.6449/sqrt(1.7302) ≈ 1.25** —
  barely better than the already-spent 1h research window's own ~1.21,
  and materially worse than the 1d holdout's ~0.96. Any real holdout
  that reserves less than the full window (as every other timeframe's
  holdout does) sits higher still — e.g. a 180-day holdout floor is
  ~2.34. **The 632-day retention depth Task S1 found does not, by
  itself, buy 1m the statistical power its raw bar count (910,040)
  suggests** — worth stating plainly here rather than discovering it
  only after a real holdout access was already spent.

  **Design decision, made with this finding in hand (human-confirmed
  2026-08-25, chosen over two alternatives — a research-split/holdout
  design mirroring 15m/1h/1d, and deprioritizing backtest validation in
  favor of live paper-trading accumulation)**: 1m scalping does **not**
  use a walk-forward research-split + holdout-confirmation structure.
  Instead, the **entire 631.98-day window is reserved as a single
  pre-registered holdout**, evaluated exactly once via the Eligibility
  Bar's existing **Holdout confirmation (single-window variant)** clause
  defined earlier in this file — matching `daily-tsmom-ensemble`'s own
  `sr-u`/`sr-v`/`sr-aa`/`sr-ab` precedent exactly, not a new mechanism.
  Rationale: (1) Task S4's own recommended first candidate (VWAP-to-mid
  reversion) is designed with zero or minimal free parameters, so there
  is no fitting/searching step that actually needs a separate research
  window; (2) splitting the 632-day window in two would weaken the
  already-thin holdout power further (a ~180-day holdout floor of ~2.34
  is a much higher bar than the full window's ~1.25); (3) 1.73 years is
  this project's *worst*-powered untouched window among 15m/1h/1d/1m —
  worse even than the already-spent 1h research window — so treating it
  as a single precious access follows this file's own standing "every
  additional trial... adds no new evidence" reasoning for the closed 1h
  window, applied here pre-emptively rather than after the fact.

  **What this resolves, replacing the "flagged, not resolved" placeholder
  CodeRabbit correctly rejected**: because there is no walk-forward
  research portion for 1m, the pooled-window `N`-accounting question
  this section originally flagged as "possibly genuinely new territory"
  **does not arise** — there is nothing to pool. The single-holdout run
  is `N=1` for its *own* PSR evaluation (never searched over, one
  access, one run — DSR and PSR coincide at `N=1`, same reasoning as
  `daily-tsmom-ensemble`'s).

  **Corrected on a second real CodeRabbit review pass, which verified
  against the actual code and a runnable reproduction rather than trust
  this document's own prior claim**: an earlier version of this
  paragraph asserted the single-holdout run "contributes exactly one new
  entry to the project-level research `N`." **That was checked against
  `python/research/overfitting_check.py` and found to be false.**
  `check_project_combination_count`'s own scanner (`_holdout_run_ids`/
  `_is_holdout_related`, Strategy Research Task V) explicitly excludes
  both a logged holdout confirmation's own final record *and* every
  record related to it (by `parent_run_id`) from the project-level
  count — contributing **zero**, not one. The module's own docstring
  states the real reasoning directly: "a holdout confirmation was never
  searched over, so it is not a combination 'tried' in the sense this
  heuristic measures." This is not a gap or a bug to fix — it is the
  same treatment `daily-tsmom-ensemble`'s own `sr-v`/`sr-ab` holdout
  confirmations have silently and correctly received all along, and the
  1m single-holdout design simply inherits it unchanged, exactly as it
  should: `sr-x`/`sr-y`'s macro attempts are the *wrong* comparison to
  reach for here (this document's own prior error) — those were
  ordinary walk-forward runs on an untouched *research* split, never
  marked `is_holdout_run=True`, which is precisely why they *do* count.
  A single-window holdout access is categorically different from an
  iterative research-split run for `N`-accounting purposes, and this
  file's text now says so correctly instead of conflating the two.

  A curated `research/lineage.py` entry (family `"btc-scalping"`) will
  still be added in Task S4 once the real `strategy_id` exists, for
  record-keeping/attribution hygiene consistent with every other logged
  strategy — but not, as an earlier version of this paragraph implied,
  because it changes this run's own contribution to the project-level
  `N` (it doesn't: zero, either way, per the mechanism above). Not added
  here in S3 since no `strategy_id` exists yet and `lineage.py`'s own
  docstring requires a citation to the document that justifies each
  entry.

  Live-paper-trading accumulation, once/if this strategy is ever
  promoted, is **not** a selection trial and does not touch this `N` at
  all — unchanged from how paper trading already works for every other
  strategy in this project (`daily-tsmom-ensemble`'s own paper-trading
  days were never logged as a research trial). No new machinery was
  needed to state this; it was implicit and is now explicit.

  **`bars_per_day = 1440`** is used wherever the Eligibility Bar's
  formulas need it (the daily-resampling bucket size above,
  `frequency_scaled_min_trades`'s `evaluated_days` computation below) —
  the same one-constant-per-timeframe convention `DEFAULT_BARS_PER_DAY`
  already establishes for every existing strategy module (`96`=15m,
  `24`=1h, `1`=1d).

  **Minimum trade-count floor, real formula applied**:
  `frequency_scaled_min_trades` (`python/research/preregistration.py`)
  computes `max(30, min(100, floor(evaluated_days/20)))` where
  `evaluated_days = total_evaluated_bars // bars_per_day`. Against the
  real Task S1 bar count (910,040 bars, `bars_per_day=1440`):
  `evaluated_days = 631` (floor division — one day short of the 631.98-
  day calendar span, since a day needs a full 1440 completed bars to
  count), `631 // 20 = 31`, floor after clamp = **31**. The exact figure
  will move slightly once a real candidate's own warmup period (e.g. a
  VWAP lookback window) is subtracted from the evaluated range — 31 is
  the ceiling this can reach, not a guess Task S4 must re-derive from
  scratch.

  **Gap-detection prerequisite from Task S1, resolved with a real
  pre-access check, not documentation alone** (a first version of this
  paragraph claimed the residual risk narrows to "rolling-window
  features only" once `walkforward.py`'s own fold-generation is out of
  the picture for a single-window run — **corrected on real CodeRabbit
  review, which was right that this understated the scope**, verified
  by reading `backtest/engine.py`, `backtest/fill.py`,
  `backtest/kline_window.py`, and `metrics/metrics.py` directly rather
  than re-asserting the original claim). The real, code-verified scope:
  (1) `fill.py::simulate_fill` selects the fill bar **positionally**
  (`klines[signal_bar_index + 1]`) — since only *one* bar-index step is
  taken, exactly **one** signal position is affected per gap (the last
  real bar strictly before it), not a range of bars, and the exact
  delay is computable, not a rounded estimate (tightened on real
  CodeRabbit review, which caught the original "≤2 bars... 4-7 minutes"
  phrasing as imprecise): a signal on the `2025-04-25T06:53:00Z` bar
  fills against `06:57:00Z` (4 real minutes later, 3 minutes more than
  a continuous series' 1-minute step would give); a signal on the
  `2026-02-13T20:31:00Z` bar fills against `20:36:00Z` (5 real minutes
  later, 4 minutes more than continuous) — a genuine engine-level
  effect, not merely a rolling-feature one; (2) `metrics.py::_sharpe_ratio`'s
  per-bar returns are computed between **consecutive array elements**,
  so the 2 gap-spanning observations are real, if bounded, distortions
  feeding directly into Sharpe/PSR; (3) `eligibility.py`'s
  `resample_equity_to_daily` chunks positionally too, so daily-bucket
  boundaries drift from true UTC-midnight alignment by up to the gap's
  own bar count after each gap — bounded, cumulative ≤7 minutes across
  the whole series, never compounding further. **One claim in the
  reviewer's own framing is precisely wrong and worth stating, not just
  conceding everything**: holding-period/elapsed-time tracking itself is
  *not* affected — `metrics/position.py`'s `ClosedTrade.entry_time`/
  `exit_time` are real `datetime` values sourced from `fill.fill_time`,
  not bar-count arithmetic, so a trade's *duration* is always accurate
  even when *which bar* became the fill was distorted by (1) above.

  **The actual fix, not just a bounded disclosure**: `python/data/
  store.py::find_missing_ranges` already exists (it is the same function
  that originally found these 2 gaps) and needs no engine change to
  reuse — Task S4 must call it against the full loaded 1m holdout window
  **before** the single holdout access happens, and **fail closed if the
  result differs from exactly the 2 known, already-disclosed gaps**
  (`[2025-04-25T06:54:00Z, 2025-04-25T06:57:00Z)`, 3 bars;
  `[2026-02-13T20:32:00Z, 2026-02-13T20:36:00Z)`, 4 bars — see "Exchange
  API Facts — BingX"). Not a bare "fail on any gap" — these 2 are real,
  permanent, and unfixable at the source, so that would block the design
  outright — but a real, testable guard against an *unexpected* gap
  (fewer, more, or relocated versus the disclosed 2), matching this
  file's own established "fail closed on undetermined, not on an
  already-accepted condition" pattern (e.g. `KrxMarketCalendar`'s
  holiday-lookup discipline above). The 2 known gaps' own bounded impact
  — (1)-(3) above — is accepted and disclosed, not eliminated; what this
  check adds is protection against a *different*, undisclosed gap
  appearing (e.g. from a future backfill re-run) and silently distorting
  results beyond the bound already reasoned about here. Task S4's own
  preregistration must name both gaps, this disclosure, and this
  pre-access check explicitly, not silently inherit it.

---

## Appendix — the second-candidate investigation (2026-08-26)

Verbatim, `sed -n '2923,2969p' CLAUDE.md`. This block sits between Tasks
S4 and S5 in CLAUDE.md and, like S0-S3, had no `.planning/` doc of its
own. It is the record of *why* the scalping arc pivoted to building data
infrastructure (S5) instead of immediately trying a third candidate: both
remaining named candidates were investigated for real and found to lack a
usable foundation. Preserved here because a future session considering
either one again needs the reasons they were set aside, not just the fact
that they were.

**Second-candidate investigation (2026-08-26): both of the remaining
named candidates turned out to lack a real, external, zero-fitted-
parameter foundation once actually investigated — disclosed here rather
than quietly abandoned.** After Task S4's INCONCLUSIVE result, the
human operator asked to proceed to the next candidate. Real research
(not assumption) into each of the two remaining options this section
originally named:

- **Order-flow imbalance**: this project's data layer cannot compute it
  at all today — BingX's own kline wire format has no buyer/seller
  volume breakdown whatsoever (confirmed directly against
  `bingx_klines.py`), and while Binance's kline wire format does carry
  `taker_buy_base_volume`/`taker_buy_quote_volume`, this project's own
  `binance_klines.py::_parse_row` discarded those fields entirely, and
  no Binance data had ever been backfilled at `1m` granularity. Real,
  fresh literature research also found the existing "Quarter-Hour
  Effect" citation's own real horizon (4-12 hours) doesn't transfer
  down to scalping timescales, and no better-fitting new evidence
  specifically for BTC at 1-minute granularity was found.
- **Liquidation cascades**: the real papers behind this project's
  existing citation (Garcia Seuma, arXiv:2607.27070 and arXiv:2608.03616,
  studying 7 major BTC liquidation cascades including the real 2025-10-10
  $19B event) turned out to be pure diagnostic/monitoring studies with
  **no trading rule proposed anywhere** — the OHLCV-computable half of
  their early-warning signal (rolling variance/lag-1 autocorrelation of
  price) is silent in exactly 2 of 7 events (including the most famous
  one) and was swept across 39 analysis configurations per event with no
  fixed, reusable window/threshold convention to borrow (unlike
  VWAP-reversion's genuine external 20-period/2-SD convention); the one
  cross-event-consistent signal needs taker order-flow/open-interest data
  this project also lacks; and **no post-cascade price-reversion pattern
  is documented anywhere in this literature** — the structural
  justification VWAP-reversion's own citation at least partially
  provided (a real, if imperfect, "63% reversion rate from 2-SD
  extensions" statistic) has no analog here. A fallback search for a
  general (non-liquidation-specific) large-move-reversion paper at
  minute-scale for BTC found nothing rigorous, only anecdotal/blog-level
  claims.

Given neither candidate could be tested honestly without either (a)
fitting parameters from scratch against this project's own 1m data
(repeating the exact 117-trial overfitting mistake this project has
already learned from) or (b) data this project structurally lacks, the
human operator chose **(b), build the missing data infrastructure
first**, rather than press ahead with a weakly-grounded design or pause
scalping research entirely.

