# Planning Artifacts

Durable record of GSD `Discuss`/`Plan` output — see CLAUDE.md's
"Development Methodology". One file per Implementation Priority (or other
non-trivial change), added in the same PR as the work it plans — that's
the rule for every *new* plan from `07` onward. `00`–`06` are the one
deliberate exception: retrospective backfill, added after the fact
because no real-time version of them was ever committed. See below.

Why this exists: earlier work in this project (governance setup and
Implementation Priorities #1–#6) used Claude Code's own session-local plan
file instead of a repo-committed artifact. That file gets overwritten by
each new plan, so those original planning documents no longer exist in
their original form. `00`–`06` below are lightweight retrospective
summaries reconstructed from commit messages and PR descriptions, not the
original planning documents — good enough to orient a future reader, not
a faithful reproduction. Everything from `07` onward is written live,
during planning, before the corresponding code exists.

## Where does a design belong: CLAUDE.md, or here?

Decided 2026-07-25, after CLAUDE.md's Priority #10 entry grew a large
inline design (`VerifiedRiskDecision`) and it wasn't obvious in the
moment whether that was the right place for it. The rule, now explicit
rather than judgment-called case by case:

- **A file here can only be created for work that has actually
  happened** (or been genuinely investigated, like `09`) — "same PR as
  the work" is the rule above, and a not-yet-started priority has no PR
  to attach a planning doc to yet.
- So a **detailed design for work that hasn't started** has nowhere to
  live but **CLAUDE.md itself**, in full — not a summary, the actual
  design, since CLAUDE.md is the one artifact guaranteed to survive to
  whenever that work actually starts (a future session, possibly with
  no memory of how the design was derived, has only this file to go
  on — see CLAUDE.md's Development Methodology section).
- **Once that work actually starts**, the new `.planning/NN-*.md` file
  for it should reference CLAUDE.md's original design and record
  whether implementation followed it exactly or deviated (and why). At
  that point — and not before — CLAUDE.md's own entry can be safely
  trimmed to a short summary plus a pointer to the `.planning/` file,
  since the full design is now durably preserved there instead.

This is what already happened correctly for the `VerifiedRiskDecision`
design (full detail in CLAUDE.md's Priority #10 entry, since Priority
#10 hasn't started) — this section just makes the reasoning explicit so
it doesn't need to be re-derived, or second-guessed as maybe-misplaced,
next time the same shape of question comes up.

## Index

77 documents and counting, which is past the point where `ls` is a
useful way to find one. Grouped by the arc each belongs to, newest arcs
last; the one-line description is each document's own title, so it says
what that document concluded rather than what it was about.

**This index is checked, not maintained by hope.**
`python/tests/test_planning_index.py` fails if any `.planning/*.md` is
missing from it, if it lists a file that no longer exists, or if a
document's title has changed without the index following. An index
nobody notices going stale is worse than none — it sends readers to the
wrong place with confidence.

### Implementation Priorities

The numbered build sequence from CLAUDE.md's own Implementation Priority list. `00`–`06` are retrospective summaries; `07` onward were written live, before the code.

- [`00-governance-setup.md`](00-governance-setup.md) — Governance Setup (pre-Implementation-Priority, retrospective summary)
- [`01-shared-schemas.md`](01-shared-schemas.md) — Implementation Priority #1: Shared Schemas (retrospective summary)
- [`02-oms-state-machine.md`](02-oms-state-machine.md) — Implementation Priority #2: Java OMS State Machine (retrospective summary)
- [`03-risk-gateway.md`](03-risk-gateway.md) — Implementation Priority #3: Java Risk Gateway (retrospective summary)
- [`04-backtest-fill-simulator.md`](04-backtest-fill-simulator.md) — Implementation Priority #4: Python Deterministic Backtest (retrospective summary)
- [`05-schema-compat-tests.md`](05-schema-compat-tests.md) — Implementation Priority #5: Schema Compatibility Tests (retrospective summary)
- [`06-paper-broker.md`](06-paper-broker.md) — Implementation Priority #6: Paper Broker (retrospective summary)
- [`07-exchange-adapter.md`](07-exchange-adapter.md) — Implementation Priority #7, Task B: `ExchangeAdapter` / `BingXAdapter`
- [`08-order-pipeline.md`](08-order-pipeline.md) — Implementation Priority #8, Task A: `OrderPipeline`
- [`08b-trading-loop.md`](08b-trading-loop.md) — Implementation Priority #8, Task B: the paper trading runtime loop
- [`09-order-store-hardening.md`](09-order-store-hardening.md) — `OrderStore.createOrder` interim hardening — investigated, not shipped

### Paper-trading bridge (Tasks A–E)

Getting a Python signal into the Java trading plane, and keeping a daily record of what happened.

- [`paper-trading-a-signal-source.md`](paper-trading-a-signal-source.md) — Paper-trading bridge, Task A: `SignalSource` interface + `FileSignalSource`
- [`paper-trading-b-signal-runner.md`](paper-trading-b-signal-runner.md) — Paper-trading bridge, Task B: daily production signal runner
- [`paper-trading-c-scheduler-entrypoint.md`](paper-trading-c-scheduler-entrypoint.md) — Paper-trading bridge, Task C: Java scheduler + `main()` entrypoint
- [`paper-trading-d-daily-reporting.md`](paper-trading-d-daily-reporting.md) — Paper-trading bridge, Task D: daily reporting
- [`paper-trading-e-reconciliation.md`](paper-trading-e-reconciliation.md) — Paper-trading bridge, Task E: minimal internal reconciliation

### BingX VST integration (Tasks F–H)

The first real venue behind `OrderExecutor`, and the layering rule it proved.

- [`paper-trading-f-order-executor.md`](paper-trading-f-order-executor.md) — BingX VST integration, Task F: `OrderExecutor` interface extraction + retrofit
- [`paper-trading-g-exchange-order-executor.md`](paper-trading-g-exchange-order-executor.md) — BingX VST integration, Task G: `ExchangeOrderExecutor`
- [`paper-trading-h-vst-integration.md`](paper-trading-h-vst-integration.md) — BingX VST integration, Task H: VST wiring, safety, and the real verification

### Follow-up issues

Defects and gaps found after the fact, each tracked to a GitHub issue.

- [`paper-trading-issue-74-shutdown-confirmation-test.md`](paper-trading-issue-74-shutdown-confirmation-test.md) — GitHub issue #74: deterministic test for `PaperTradingApp.stop()`'s shutdown-termination-confirmation logic
- [`paper-trading-issue-75-durable-report-persistence.md`](paper-trading-issue-75-durable-report-persistence.md) — GitHub issue #75: durable pending-daily-report persistence
- [`paper-trading-issue-80-guardrail-alias-tracking.md`](paper-trading-issue-80-guardrail-alias-tracking.md) — GitHub issue #80: cross-statement alias/taint tracking for the VST-host guardrail
- [`quantity-precision-discuss.md`](quantity-precision-discuss.md) — GitHub issue #151: Quantity precision — `Discuss` before any code
- [`position-truth-discuss.md`](position-truth-discuss.md) — GitHub issue #157: Position truth — who owns it, and why the strategy currently does

### KIS / KOSPI200 venue

The third paper-trading loop, and the shared-account ledger that followed it. Kill-switch-tripped by design.

- [`kis-ledger-a-account-state-provider.md`](kis-ledger-a-account-state-provider.md) — Shared KIS account risk ledger, Task A: `AccountStateProvider` extraction
- [`kis-ledger-b-account-ledger-store-lock.md`](kis-ledger-b-account-ledger-store-lock.md) — Shared KIS account risk ledger, Task B: `AccountLedgerStore` + `AccountLedgerLock`
- [`kis-ledger-c-shared-account-ledger.md`](kis-ledger-c-shared-account-ledger.md) — Shared KIS account risk ledger, Task C: `SharedKisAccountLedger` + wiring
- [`kis-ledger-d-account-ledger-reconciler.md`](kis-ledger-d-account-ledger-reconciler.md) — KIS Ledger Task D: `AccountLedgerReconciler`
- [`kis-phase1-venue-integration.md`](kis-phase1-venue-integration.md) — KIS / KOSPI200 venue integration, Phase 1 — retrospective design record

### Operations

Running the thing somewhere other than a laptop.

- [`ops-vps-deployment.md`](ops-vps-deployment.md) — Moving the paper-trading loops to a VPS

### Strategy research (sr-a … sr-ac)

Eight strategy families, 18 configurations, and the statistical machinery built alongside them. Nothing cleared the Eligibility Bar; every negative result is here.

- [`sr-a-data-pipeline.md`](sr-a-data-pipeline.md) — Strategy Research, Task A: the historical BingX kline data pipeline
- [`sr-aa-binance-virgin-window-registration.md`](sr-aa-binance-virgin-window-registration.md) — Strategy Research Task AA: registering a Binance-virgin-window replication of the daily-TSMOM hypothesis
- [`sr-ab-binance-virgin-holdout-result.md`](sr-ab-binance-virgin-holdout-result.md) — Strategy Research Task AB: Binance-virgin-window holdout replication, executed
- [`sr-ac-combined-holdout-meta-analysis.md`](sr-ac-combined-holdout-meta-analysis.md) — Strategy Research Task AC: combined-holdout statistical meta-analysis
- [`sr-b-engine-metrics.md`](sr-b-engine-metrics.md) — Strategy Research Task B: `KlineWindow` performance fix + `python/metrics/`
- [`sr-c-walkforward-holdout.md`](sr-c-walkforward-holdout.md) — Strategy Research Task C: walk-forward harness, holdout-split enforcement, experiment log
- [`sr-d-placeholder-strategy.md`](sr-d-placeholder-strategy.md) — Strategy Research Task D: placeholder MA-crossover `TrainableStrategy`
- [`sr-e-regime-momentum.md`](sr-e-regime-momentum.md) — Strategy Research: regime-filtered 15m momentum ("Task E")
- [`sr-f-risk-management-and-1h-variant.md`](sr-f-risk-management-and-1h-variant.md) — Strategy Research Task F: risk management for the 15m strategy, and a native-1h variant
- [`sr-g-overfitting-safeguards.md`](sr-g-overfitting-safeguards.md) — Strategy Research Task G: overfitting safeguards (parameter sensitivity + MinBTL-style combination tracking)
- [`sr-h-ensemble-regime-voltargeting.md`](sr-h-ensemble-regime-voltargeting.md) — Strategy Research Task H: multi-lookback ensemble vs. single-lookback baseline, ADX regime weighting, real volatility targeting
- [`sr-i-ensemble-refinement.md`](sr-i-ensemble-refinement.md) — Strategy Research Task I: diagnosing and refining the ensemble-momentum strategy
- [`sr-j-fold-diagnosis-and-eligibility-review.md`](sr-j-fold-diagnosis-and-eligibility-review.md) — Strategy Research Task J: diagnosing the 8 remaining negative folds, and a proposed (not-yet-approved) revision to the Eligibility Bar's "positive Sharpe in every fold" criterion
- [`sr-k-mean-reversion-and-blend.md`](sr-k-mean-reversion-and-blend.md) — Strategy Research Task K: regime-gated mean-reversion, a regime-adaptive
- [`sr-l-volume-signal.md`](sr-l-volume-signal.md) — Strategy Research Task L: does volume discriminate Configuration C's
- [`sr-m-funding-rate-pipeline.md`](sr-m-funding-rate-pipeline.md) — Strategy Research Task M: funding-rate data pipeline + funding P&L modeling
- [`sr-n-funding-rate-strategy.md`](sr-n-funding-rate-strategy.md) — Strategy Research Task N: funding-rate-extremity contrarian strategy + Configuration C funding re-run
- [`sr-o-funding-fold-boundary-fix.md`](sr-o-funding-fold-boundary-fix.md) — Strategy Research Task O: funding-extremity fold-boundary state-seeding fix
- [`sr-p-trial-accounting.md`](sr-p-trial-accounting.md) — Strategy Research Task P: honest trial accounting (lineage, `TrialKind`, project-level `N`)
- [`sr-q-deflated-sharpe.md`](sr-q-deflated-sharpe.md) — Strategy Research Task Q: Probabilistic and Deflated Sharpe Ratio
- [`sr-r-retrospective-closeout.md`](sr-r-retrospective-closeout.md) — Strategy Research Task R: the statistical close-out
- [`sr-s-preregistration.md`](sr-s-preregistration.md) — Strategy Research Task S: pre-registration
- [`sr-t-daily-data-path.md`](sr-t-daily-data-path.md) — Strategy Research Task T: the 1d data path and an early-window holdout
- [`sr-u-preregistered-attempt-spec.md`](sr-u-preregistered-attempt-spec.md) — Strategy Research Task U: the pre-registered daily TSMOM attempt (spec + registration)
- [`sr-v-preregistered-attempt-result.md`](sr-v-preregistered-attempt-result.md) — Strategy Research Task V: the pre-registered daily TSMOM attempt, executed
- [`sr-w-macro-data-pipeline.md`](sr-w-macro-data-pipeline.md) — Strategy Research Task W: the FRED macro-data pipeline
- [`sr-x-macro-real-yield-strategy.md`](sr-x-macro-real-yield-strategy.md) — Strategy Research Task X: the macro-real-yield-trend strategy (real, honest result)
- [`sr-y-macro-sp500-strategy.md`](sr-y-macro-sp500-strategy.md) — Strategy Research Task Y: the macro-sp500-trend strategy (real, honest result)
- [`sr-z-binance-data-research.md`](sr-z-binance-data-research.md) — Strategy Research Task Z: Binance as a deeper BTC data source

### Scalping research (S0 … S16)

A second research arc on 1-minute bars, opened 2026-08-24. Closed to selection — see CLAUDE.md for the arithmetic.

- [`scalp-s0-s3-methodology.md`](scalp-s0-s3-methodology.md) — BTC scalping research, Tasks S0–S3 — retrospective methodology record
- [`scalp-s10-regime-classifier.md`](scalp-s10-regime-classifier.md) — Scalping Strategy Research Task S10 — regime classifier, and why it failed its own test
- [`scalp-s11-feature-ic.md`](scalp-s11-feature-ic.md) — Scalping Strategy Research Task S11 — per-feature IC, and how many signals we actually have
- [`scalp-s12-mae-mfe.md`](scalp-s12-mae-mfe.md) — Scalping Strategy Research Task S12 — MAE/MFE, and the number that decides everything
- [`scalp-s13-horizon-sweep-and-closeout.md`](scalp-s13-horizon-sweep-and-closeout.md) — Scalping Strategy Research Task S13 — the holding-horizon sweep, and the selectivity reversal
- [`scalp-s14-selective-reversion.md`](scalp-s14-selective-reversion.md) — Scalping Strategy Research Task S14 — the first walk-forward-validated scalping candidate, and why the S13 result did not survive it
- [`scalp-s15-entry-risk-and-sizing.md`](scalp-s15-entry-risk-and-sizing.md) — Scalping Strategy Research Task S15 — the three remedies S14 pointed at, all three measured
- [`scalp-s16-audit-of-s15.md`](scalp-s16-audit-of-s15.md) — Scalping Strategy Research Task S16 — an audit of S15's own conclusion, requested because it was not trusted
- [`scalp-s4-vwap-mid-reversion-result.md`](scalp-s4-vwap-mid-reversion-result.md) — Scalping Strategy Research Task S4 — real holdout result
- [`scalp-s4-vwap-mid-reversion.md`](scalp-s4-vwap-mid-reversion.md) — Scalping Strategy Research Task S4: VWAP-to-mid reversion (commit phase)
- [`scalp-s5-binance-1m-orderflow-infra.md`](scalp-s5-binance-1m-orderflow-infra.md) — Scalping Strategy Research Task S5 — Binance 1m + taker-buy-volume data infrastructure
- [`scalp-s6-ofi-momentum-result.md`](scalp-s6-ofi-momentum-result.md) — Scalping Strategy Research Task S6 — real holdout result
- [`scalp-s6-ofi-momentum.md`](scalp-s6-ofi-momentum.md) — Scalping Strategy Research Task S6 — order-flow-imbalance momentum (commit phase)
- [`scalp-s7-backtest-insolvency-floor.md`](scalp-s7-backtest-insolvency-floor.md) — Scalping Strategy Research Task S7 — backtest engine insolvency floor
- [`scalp-s8-research-methodology.md`](scalp-s8-research-methodology.md) — Scalping Strategy Research Task S8 — research methodology, rebuilt
- [`scalp-s9-slippage-measurement.md`](scalp-s9-slippage-measurement.md) — Scalping Strategy Research Task S9 — real slippage measurement

### Trade management (Tasks A–D)

A multi-leg position model, built after the operator pointed out the framework could not express what they were describing.

- [`tm-a-trader-style-position-model.md`](tm-a-trader-style-position-model.md) — Trade Management Task A — a position model that can express what a trader actually does
- [`tm-b-signal-and-data-catalogue.md`](tm-b-signal-and-data-catalogue.md) — Trade Management Task B — what the exchange actually gives us, and what each thing means
- [`tm-c-confluence-hedge-result.md`](tm-c-confluence-hedge-result.md) — Trade Management Task C — result: the confluence hedge is REJECTED
- [`tm-c-confluence-hedge-specification.md`](tm-c-confluence-hedge-specification.md) — Trade Management Task C — specification for the core-plus-tactical-hedge candidate
- [`tm-d-breakout-management-preregistration.md`](tm-d-breakout-management-preregistration.md) — Trade Management Task D — pre-registration: what does *managing* a fixed entry actually do?
- [`tm-d-breakout-management-result.md`](tm-d-breakout-management-result.md) — Trade Management Task D — result: management is the biggest single effect this project has measured, and two policies clear Gate A
