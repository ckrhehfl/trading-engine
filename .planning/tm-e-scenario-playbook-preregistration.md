# Trade Management Task E — pre-registration

**Committed 2026-09-18, before any Task E run.** Design and reasoning:
[`tm-e-scenario-playbook-design.md`](tm-e-scenario-playbook-design.md).
This document is the contract. Everything in it is fixed now; nothing in
it may be extended, relaxed, or reinterpreted after a result exists.

**Operator decisions closing the design's §8**, taken 2026-09-18:

| | decision |
|---|---|
| §8.1 core | **both** spot and futures → **8 policies** |
| §8.2 universe | the **`rd-r` ten**, now, rather than waiting for the survivorship-safe full universe |
| §8.3 risk budget | **shared** between primary and alternative, matching Task D's constant-initial-layer rule |

---

## 1. Declarations made before any run

**This is a comparison run**, declared as one here under CLAUDE.md's
Comparison-run rule, before any Task E data access.

- **axis varied**: the response — playbook selection and what happens on
  invalidation.
- **held fixed**: the entry rule, the initial-layer sizing, the universe,
  and the window.
- **consequence**: every policy's figures may be reported, including the
  losers'. **No policy may be promoted, advanced to a holdout, or put
  forward as a candidate on the strength of this run.**

**This is a discovery-mode run.** KRX daily was spent by `ms-f` on
2026-09-13. Under the amended spent-window rule it is open for generating
hypotheses confirmed elsewhere and closed to selecting anything for
promotion. So the promotion `N` is **not** incremented, **nothing here may
be quoted as evidence of an edge or reported as a pass**, and the only
legitimate output is a written specification. Trials are still logged to
`runs/experiments.jsonl`.

**`N` for this study starts at 1 and counts up per policy**, on Task D's
reasoning: `N` is a property of the pair (data window, search history),
and the 129 prior trials searched the space of direction-predicting
formulas. This study fixes the entry in advance and varies only the
response, so it has not been searched over by them. **The guard that keeps
that honest is this document**: the entry rule and the complete policy
list are committed here, and adding a policy means a new
pre-registration.

**Criteria pinned before access**, per the "in force at the time means
pinned before, not chosen after" rule. Drawdown ceiling **20–25%**,
profit-factor floor **1.3–1.5**, trade-count floor by
`max(30, min(100, floor(bars / bars_per_day / 20)))` with
`bars_per_day = 1`. From CLAUDE.md as of **2026-09-18**.

## 2. The data

| | |
|---|---|
| universe | the `rd-r` ten (`research.krx_signal_ic.UNIVERSE`) |
| bars | KRX daily, `klines` where `symbol LIKE 'KRX:%'` and `interval = '1d'` |
| window | the inner join on date: **2021-11-29 → 2026-09-17, 1,176 dates** |
| prices | split-adjusted (`FID_ORG_ADJ_PRC=0`); **price return, not total return** — KIS adjusts splits but not dividends |

**Disclosed contamination, unchanged from `rd-t`/`rd-u`**: `rd-r` selected
these ten on **2026Q1** futures turnover, which sits inside the window.
Bounded rather than removed — rank persistence is Spearman **+0.954** — and
it means any level result is partly a statement about which names were
liquid in 2026. The h=1 intraday framing is preferred throughout for
exactly this reason: its unconditional baseline is ~0, so an excess is
signal rather than composition drift.

**The panel must have no interior hole.** `krx_conjunction.interleaved_sessions`
refuses a run where a session traded by some name was dropped from inside
the span; measured, 718 dates are dropped and **none is a hole**.

## 3. The entry — fixed, from outside, and not swept

**Opening-range breakout restricted to abnormally active names**,
Zarattini's "Stocks in Play" construction.

| | |
|---|---|
| activity filter | `quote_volume[t-1]` divided by its own median over `t-21 .. t-1`; a name is eligible when that ratio is in the **top half of the eligible cross-section that day** |
| range | day `t`'s open against the previous session's high/low |
| long trigger | `open[t] > close[t-1]` and the name is eligible |
| short trigger | `open[t] < close[t-1]` and the name is eligible |
| fill | day `t`'s **open** |
| direction in COMPRESSION | inverted — see §4 |

**The point-in-time cutoff is part of the contract, not an implementation
detail.** `acml_tr_pbmn` is cumulative within the session, so a daily
bar's 거래대금 includes everything traded *after* the open. **The filter
may read `quote_volume[t-1]` and earlier only. `quote_volume[t]` is
unreadable in day `t`'s logic.**

**Required verification**: a test that sets `quote_volume[t]` to an
extreme value for the selected names and asserts **the selection does not
change**. A filter that reads the forward value cannot pass it; one that
merely looks correct can.

**A median split, so there is no threshold to fit.** Zarattini's own
relative-volume cutoff is intraday and cannot transfer (§4.1 of the
design); a cross-sectional median is the parameter-free substitute and is
the same device `rd-u` used. **Disclosed deviation**: the filter is one
session stale against the source specification, which may weaken it.

## 4. The regime → playbook contract

**Two branches, volatility axis only.** `Structure` (ADX) is computed and
recorded and **not acted on** — S10 measured it carrying nothing on both
axes it could have.

| regime | playbook | entry direction |
|---|---|---|
| **EXPANSION** | breakout | with the gap (continuation) |
| **COMPRESSION** | fade | against the gap (reversion) |

Three behaviours fixed here because they decide the result:

1. **Warm-up (`RegimeClassifier.update` returns `None`): no new entries.**
   Any open position continues under the playbook it was opened with.
   Absence of a regime is not a regime.
2. **A regime change does not switch an open position's playbook.** The
   position lives out the contract it was opened under, exit rule
   included; the new regime governs the next *entry* only.
3. **No new threshold is introduced.** ADX 20/25, the volatility
   25th/90th percentile band, and the 14-bar minimum dwell are
   `regime_classifier`'s existing constants.

**Daily-bar constants, which the classifier's defaults do not cover.**
`AbsoluteAtr`'s defaults are `history=1440`, `refresh_every=60` — one
trading day of 1-minute bars. On daily bars 1440 is 5.9 years, longer than
this panel, so the classifier would never leave warm-up. Pinned instead:

| | daily value | why |
|---|---|---|
| `history` | **252** | one trading year, the conventional round number for daily data |
| `refresh_every` | **21** | one Korean trading month, the same constant §5 and `rd-u` already take from Lou-Polk-Skouras |
| `min_dwell_bars` | **14** | the classifier's own default, the ATR period — unchanged |

**These are conventional, not fitted**, and they are declared here rather
than chosen after seeing labels. Neither is swept. Cost: 252 bars of
warm-up leaves **~924 of 1,176 dates** classified, and that is reported
rather than absorbed.

## 5. The eight policies — the complete and closed list

Entry identical across all eight. Initial-layer sizing constant, `R`
normalised to that layer. **Risk budget shared** between primary and
alternative (§8.3), so total exposure is comparable across policies.

| | core | playbook selection | on invalidation |
|---|---|---|---|
| **E0-F** | futures | one playbook always (P3 management) | flat |
| **E1-F** | futures | regime selects | flat |
| **E2-F** | futures | regime selects | alternative scenario |
| **E3-F** | futures | regime selects | hedge leg, core kept |
| **E0-S** | spot | one playbook always (P3 management) | flat |
| **E1-S** | spot | regime selects | flat |
| **E2-S** | spot | regime selects | alternative scenario |
| **E3-S** | spot | regime selects | hedge leg, core kept |

**The cores are different experiments even for E0–E2**, because those
close on invalidation and closing costs differ: **20 bp of 증권거래세 on
spot** (`krx_tax_schedule.total_bp`, era-correct) against **~13 bp of
futures round trip** (`rd-q`).

### 5.1 Management, adopted from Task D's P3 unchanged

The only policy in project history to clear Gate A. Not re-searched.

- scale out **50% at +1R**
- **trail the remainder** at 3 × ATR(14)
- stop at **1R**; `R` is the initial-layer planned risk
- time exit at **10 sessions** if neither target nor stop is reached

### 5.2 The state machine, and its exhaustiveness requirement

At every bar, in this order, exactly one branch fires:

| order | branch | condition |
|---|---|---|
| 1 | **stop** | adverse excursion reaches 1R |
| 2 | **scale** | favourable excursion reaches 1R and the scale has not been taken |
| 3 | **trail** | the runner's trailing stop is touched |
| 4 | **time** | 10 sessions elapsed |
| 5 | **hold** | none of the above |

**A bar matching no branch must raise, not silently hold.** Branch 5 is
the explicit catch-all; if the implementation can reach a state where none
applies, that is a hole and the run is void. Task C's parameter-free exit
pinned its holding period to one hour *by accident*, which is what this
requirement exists to prevent.

**Invalidation** is branch 1 (the stop). What happens next is the policy
axis: flat (E0/E1), the opposite thesis (E2), or a hedge (E3).

### 5.3 The hedge leg — E3 only

| | |
|---|---|
| instrument | front-month single-stock future on the same underlying |
| contract size | **10 shares**, uniform across all 283 listed names (`rd-r`) |
| hedge ratio | **50% of the current core**, matching P5 |
| quantity | `floor(core_shares × 0.5 / 10)` contracts, whole contracts only |
| **too small to hedge** | falls back to E2's behaviour for that episode, and **every fallback is counted and reported** |
| roll | front month, rolled the session before the final trading day |
| close order | hedge leg first; the core closes by its own rule |
| costs | ~13 bp per futures leg; a spot core's close additionally pays the era's tax |

**The fallback count is part of the result, not a footnote.** A policy that
silently cannot act on some episodes is not being tested on them, and
reporting the aggregate as if it were is Task C's error in a new place.

## 6. Registered predictions

Stated before any run, so the write-up cannot be read as having chosen
its own interpretation.

**E1 vs E0 — uncertain, prior leans negative.** S10 found the structure
axis carries nothing and that discretising costs most of the volatility
axis. If regime branching only reproduces what a continuous conditioner
already does, E1 ≈ E0 and the machinery is cost without benefit.

**E2 vs E1 — no prior.** Whether "the thesis broke, so the opposite thesis
is live" is worth anything is untested here and in the literature this
project has read.

**E3 vs E2 — split by core, and this is the prediction worth the most.**

- **futures core: E3-F < E2-F**, by about one futures round trip. Task D's
  P5 lost to P3 by 14.4R over 464 identical entries (≈0.031R each) with
  the mechanism *a hedge is a costlier way to reduce exposure than
  reducing it*. **If P5 does not replicate here, this project's cost model
  is wrong and that is investigated before anything else in this task is
  trusted.**
- **spot core: E3-S may beat E2-S**, by roughly the tax differential.
  Closing spot pays 20 bp; hedging with a future pays ~6.5 bp and no
  증권거래세. That gap is ~13 bp — the same magnitude P5 lost by on a
  market where it is zero. **Both Task C and Task D measured the hedge on
  BTC-USDT, where no transaction tax exists**, so their verdict may not
  transfer, and this is the one place the operator's hedging intuition
  could be right for a structural reason rather than a hopeful one.

**A prediction about the whole task, registered because it is the honest
expectation**: none of the eight is expected to produce a promotable
candidate. `rd-t` found one usable signal and `rd-u` found its
conjunctions capture 8.7% of the available move. Task E changes the
*shape* of the payoff, not the prediction, and on a signal this weak that
may not be enough. The output is a specification either way.

## 7. The stopping rule

**Foreclosed: adjusting a branch, a threshold, or a constant and
re-running.** The permitted responses to the result are:

1. accept it and write the specification; or
2. specify a **different** state machine in a **new** pre-registration.

The regime constants in §4, the entry in §3, the management in §5.1 and
the hedge terms in §5.3 are all fixed. A result that would have been
better with a different `history` window is not a reason to change
`history`.

## 8. What would make this run void rather than negative

Named in advance so they cannot be rationalised later:

- **a state-machine hole** — any bar matching no branch (§5.2)
- **the look-ahead test failing** (§3), which would mean the filter read
  the forward value
- **an interior hole in the panel** (§2)
- **P5 failing to replicate on the futures core** (§6), which impeaches
  the cost model rather than the policy
- **fewer than 30 episodes** on any policy, which is
  `INCONCLUSIVE-DATA-LIMITED` for that policy and not evidence against it
