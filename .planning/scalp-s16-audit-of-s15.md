# Scalping Strategy Research Task S16 — an audit of S15's own conclusion, requested because it was not trusted

Executed 2026-08-28, immediately after S15 shipped. The operator asked a
direct question: *is this conclusion actually right, or is it another
case of measuring the wrong thing and declaring the domain closed?*

Given that this arc has already produced four documented overclaims, the
question was well founded. It was answered by measuring, not by
re-reasoning.

**Three real defects were found in S15's own work.** The final verdict
(REJECTED) survives, but S15's stated *reason* for it was wrong, and one
of the defects had been quietly weakening every DSR this project has
reported from that scorer.

---

## Defect 1 — the conclusion was drawn from the weaker of two cells, again

S13's corrected sweep left two cells standing: `|z|>=5` (+13.97bps
gross, t=1.95) and `|z|>=6` (+30.87bps, t=2.56). **Every walk-forward
S14 and S15 ran used `entry_z=5.0`.** The `|z|>=6` cell — measured at
2.2x the outcome — was never walk-forwarded, and "the signal is not
there" was declared anyway.

This is the *fifth* instance of the error pattern already written into
CLAUDE.md as a rule: **never conclude about a domain from one parameter
setting.** The rule was followed at the sweep stage and then abandoned at
the walk-forward stage, which is the more expensive place to abandon it.

Running the missing cells reverses the sign of the headline result:

| Cell | mean fold Sharpe | folds positive | compounded |
|---|---|---|---|
| `\|z\|>=5`, top 1% (all S14/S15 runs) | −0.3326 | 45.8% | **−50.6%** |
| `\|z\|>=6`, top 1% | **+0.4557** | 56.2% | **+13.97%** |
| `\|z\|>=6`, top 0.1% | **+0.8993** | 66.2% | **+32.42%** |

## Defect 2 — a fold-based bar applied to a strategy with ~2 trades per fold

S15 reported "45.8% of folds positive against an 80% floor" as evidence.
At `|z|>=6` the median fold holds **2 trades**; at `|z|>=5` it holds 6.
A fold's sign is then close to a coin flip, and the floor is not merely
hard — it is unreachable:

| P(a fold is positive) | P(>= 67 of 83 folds) |
|---|---|
| 0.50 (no edge) | 6.9e-09 |
| 0.60 (a good edge) | **4.6e-05** |
| 0.70 (a strong edge) | 1.9e-02 |

A criterion a genuinely good strategy clears 0.005% of the time is not
measuring the strategy. CLAUDE.md already contains this exact reasoning
for *fold counts* (`sr-j`: demanding a literal 19/19 sweep "mostly
measures luck, not edge"); the same argument applies to *trades per
fold* and had not been made. **Fold-consistency and the sign test are
uninformative below roughly 20-30 trades per fold, and reporting them as
evidence is misleading in both directions.**

Note this does not rescue the strategy — the aggregate compounded return
is the real evidence and it was negative at `|z|>=5`. It means the
*reasoning* offered was not the right instrument.

## Defect 3 — the DSR input was wrong, in the strict direction

`s14_eligibility.py` fed `trial_sharpe_variance` **this run's own
per-fold Sharpes**. The benchmark wants the variance across the other
**trials**, each of which is itself an average over folds — a much
smaller quantity. `research/retrospective.py`, the module this project
already built for this computation, pools trial-level Sharpes by purpose;
the scorer did not.

The effect was to inflate the selection benchmark and push every reported
DSR toward zero. Every DSR figure S14 and S15 reported came from that
path.

Corrected, and then corrected again: even with the right input, a second
implementation kept disagreeing with the reference on details that are
invisible at a glance (which purposes to pool, whether `sampling` is
passed). **The scorer now delegates DSR, PSR and the trial counts to
`retrospective.py` outright** rather than recomputing them, and its
output matches the reference exactly (6.46139e-11).

## What the corrected evaluation actually says

`|z|>=6`, top 0.1%, no stop, compounding sizing — the best configuration
this arc has produced:

```
fold consistency  49/83 = 59.0%  vs 80%      FAIL  (uninformative, see Defect 2)
sign test         p = 0.0619                 FAIL  (uninformative)
mean-Sharpe t     t = +2.3877, p = 0.0098    PASS  <- first ever in this arc
DSR (N = 127)     6.46e-11      vs 0.95      FAIL
  PSR (no search) 0.9905     DSR (family N=5) 0.4319
detection floor   0.623 vs observed +0.899 -- the window IS powered for this
max drawdown      9.93%         vs 25%       PASS
trade count       181           vs 100       PASS
profit factor     6.4413        vs 1.3       PASS  (median fold 0.626 - FRAGILE)
```

Per-year, compounded, from the walk-forward itself:

| 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| −0.30% | +11.51% | +4.99% | −1.11% | +13.96% | +4.47% | +0.35% | −3.99% |

**Six of eight years positive, and NOT 2021-dependent** — excluding 2021
still gives +26.12% of the +32.42% total. That directly contradicts
S13's finding that 2021 supplied ~60% of the edge, and the reason is
instructive: S13 measured *mean bps per position*, where 2021's violent
moves dominate, while the walk-forward compounds *equity* under
ATR-inverse position sizing, which gives those same moves a small
position. **Risk-based sizing neutralised the regime concentration.**

## So is it an edge? No — and now for a reason worth acting on

The gap between **PSR 0.9905** and **DSR 6.46e-11** is entirely the
selection penalty for 127 project trials. Inverting the benchmark gives
the Sharpe a result must post to clear DSR 0.95 at each `N`:

| N | required annualized Sharpe |
|---|---|
| 1 (no search) | **0.63** |
| 5 (this family) | 2.17 |
| 50 | 3.56 |
| **127 (this project today)** | **4.00** |

Credible institutional trend-following reports 0.4-0.8. **At N = 127 no
realistic edge can clear this bar on this data, whatever it is.** The
observed 0.899 would need to be 4.5x larger.

This is not a new phenomenon — it is exactly what CLAUDE.md already
records for the 1h window ("Configuration C would have needed an
annualized Sharpe of 4.6"). **The same thing has now happened to the
Binance futures 1m window**, and the standing rule written for the 1h
window applies verbatim: further *selection* there is strictly
value-destroying, because raising `N` can only lower the DSR of any
result, never raise it.

## The one number that changes the sequencing

At `N = 1` the requirement is **0.63**. The observed research-window
Sharpe is **0.899**, on a window whose own detection floor is 0.623.

Binance **spot** 1m has never been touched by this project and is not in
the local store. A single pre-registered confirmation there faces `N` = 1
by construction (`overfitting_check` excludes holdout runs), so the bar
is 0.63, not 4.00.

**That is the only remaining path on which this candidate could ever be
confirmed** — and it is a one-shot, spend-once resource under the
`sr-u`/`sr-v` protocol. Stated as a finding, not a recommendation: it
needs its own `Discuss`, its own pre-registration committed before any
spot data is fetched, and an honest accounting of the real differences
between spot and perpetual-futures microstructure (fee schedule, no
funding, different participants) that make it a *replication* rather
than a continuation.

## Corrections this makes to the S15 record

1. **"The signal is not there" is withdrawn.** The supported statement is
   narrower: *at the operating point tested, the signal does not clear
   costs; at a more selective one it does, and that result cannot be
   distinguished from the best of 127 searches on this window.*
2. **"Three independent remedies each moved the result the right way and
   none crossed zero"** was true only of `|z|>=5`. Two of the three cells
   never tested do cross zero.
3. **The 2021-concentration warning does not transfer** from the
   excursion measurement to the sized walk-forward.

## Artefacts

| File | Change |
|---|---|
| `python/research/analysis/s14_eligibility.py` | DSR/PSR/trial counts delegated to `retrospective.py`; detection floor reported |
| `python/research/analysis/s16_audit_run.py` | the two missing walk-forward cells, and the attainability computation |

Logged runs: `ee1e4fea-0495-4b68-af00-ef367c344797` (`|z|>=6` top 1%),
`c7e88deb-f6e8-484b-8d89-c7d09d3bc021` (`|z|>=6` top 0.1%). Both counted;
`N` is now **127**.
