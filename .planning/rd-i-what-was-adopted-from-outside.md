# Research Direction Task I — what was adopted from an outside write-up, and what was not

**2026-09-15.** The operator brought a community post on building
automated trading systems with an AI coding agent —
[dcinside AntiGravity gallery #106](https://gall.dcinside.com/mgallery/board/view/?id=ag&no=106),
*"ai 자동매매를 구축해 보자"* — and asked what it changes here.

**One thing was adopted.** This document records the decision, including
the parts that were declined, so a future session does not re-litigate it
from scratch.

**Caveat on the source**: it was read through a page-fetch summariser, so
headings and quoted lines are verbatim but the full body was not
transcribed. Nothing below rests on a detail finer than the quoted lines.

---

## 1. Why most of it needed no adopting

The post's checklist is good, and it overlaps this project's own rules
almost item for item — arrived at independently, which is worth something:

| The post | Already here, and where it came from |
|---|---|
| *"언덕이 뾰족하지 않고 완만한지"* — a plateau, not a peak | CLAUDE.md: *"never conclude about a domain from one parameter setting — sweep it first."* Written after **S16**, where `entry_z=5.0` was the only cell run and `\|z\|>=6` reversed the sign |
| *"리페인팅의 오류"* | Look-ahead. Committed **twice on 2026-09-14** — `situation_catalogue`'s whole-array quantile, and stage 2's baseline. Both caught on review, neither by the author |
| slippage / spread / commission | `scalp-s9` (BTC 12bp), [`rd-f`](rd-f-korean-cost-structure.md) (KRX ~30–33bp) |
| *"5년간 거래 5회"* | the trade-count floor, `max(30, min(100, ...))` |
| *"특정 기간에만 수익이 몰렸는지"* | TM Task D's P3: **+73.5R of +79.2R came from 2020 alone** |
| half-Kelly; 50% lost needs 100% back | S8's risk-budget-first, with the ruin threshold set at 25–30% rather than 50% |
| daily-loss / consecutive-loss / duplicate-order / no-martingale rails | `RiskGateway` + `KillSwitch` + S8's live-side prerequisite list |

## 2. What the post does not have, and it is what killed every candidate here

Stated because the overlap above could otherwise read as "we could have
just followed the post." The post is a **build** guide; its epistemics
stop at a trap checklist.

- **No multiple-testing correction.** "Find a smooth plateau" does not say
  that trying a hundred combinations and reporting the best inflates the
  result. At `N` = 127 the DSR-0.95 bar is an annualized Sharpe near
  **4.00** against a credible 0.4–0.8 — the arithmetic that has closed
  every window this project owns.
- **No pre-registration or holdout discipline.** Nothing stops re-running
  after seeing a result. On 2026-09-14 a pre-specified suspicion was the
  only thing between `t = 2.76, p = 0.0059` and a headline
  ([`rd-h`](rd-h-stage2-result.md)).
- **No detection floor.** It never asks whether the window could see the
  edge at all. `1.6449/sqrt(years)`.
- **No mechanism requirement.** *"본인에게 반복적으로 쓰는 기준이 있다면"*
  takes the strategy as given; S8 requires naming who is on the other
  side and why they lose.
- **No base rate.** [`rd-c`](rd-c-mechanism-catalogue.md) §1: under 1% of
  day traders are reliably profitable, and **71% of the average loss is
  transaction cost**.

And the framing difference that matters most: the post's premise is *you
have a strategy, and AI can now code it*. **Coding was never the
bottleneck here** — 1,883 logged runs all executed fine. What fails is
measurement.

## 3. What was adopted: the parameter surface

MetaTrader5's optimizer **draws** the parameter surface, so "the peak is a
spike" is something you see. This project asserted the same rule in prose
and had no way to look at it. `python/research/parameter_surface.py`
closes that, reading `runs/experiments.jsonl` only — it scores nothing,
runs no backtest, touches no window, and spends no `N`.

**Two departures from the idea as the post presents it, both from this
project's own incidents:**

1. **The verdict is computed, not eyeballed.** `plateau_ratio` =
   `mean(neighbours) / peak`, with `SPIKE` below 0.5. A surface you squint
   at is a judgement call; a ratio is something a test can pin, and
   `test_the_threshold_is_what_separates_them` shows the same data
   flipping verdict with the threshold, so it is a stated convention
   rather than a property of the data.
2. **Coverage is the headline, not the picture.** S16's error was
   concluding from cells that were run while the deciding cell was never
   run at all — invisible in a heatmap, obvious in a count. Unrun cells
   render as `?`, deliberately loud, because a blank would read as "low"
   and that is a different claim from "never measured."

### It found something on the first run, on this project's own history

| sweep | scored runs | **coverage** | verdict |
|---|---|---|---|
| `single-lookback-momentum` | 279 | **44/480 = 9%** | **SPIKE** — peak 2.696, neighbours 0.090, ratio **0.03** |
| `ma-crossover-task-g-sensitivity-demo` | 42 | ~29% | **SPIKE** — ratio −0.67 |
| `regime-momentum-btc-15m` | 30 | **5/25 = 20%** | **PEAK ISOLATED — every neighbour is an unrun cell** |
| `hourly_momentum` | 190 | **5/25 = 20%** | no positive cell |

Two things follow, and neither was visible before.

**The best cell of the largest sweep this project ever ran is a spike.**
Its immediate neighbours retain **3%** of its value.

**Two of the sweeps were never grids.** `hourly_momentum` and
`regime-momentum-btc-15m` each occupy 5 cells of a 5×5 grid, on the
diagonal — `fast` and `slow` were moved together as pairs, so there is no
surface, only a line through one. And `regime-momentum-btc-15m`'s peak has
**every neighbour unrun**, which is S16's error shape exactly, on a
strategy from long before S16.

**This changes no past conclusion**, because all four were rejected
anyway and the window they ran on is closed to selection. What it changes
is the next sweep: coverage and plateau are now reportable in one command,
before a conclusion is written rather than after.

## 4. What was declined, and why

**MetaTrader5 / MQL5 as a platform.** Three reasons, the first decisive:

1. **Venue mismatch, structural rather than preference.** MT5 serves
   FX/CFD brokers. This project trades BingX perpetuals and KRX. There is
   no MT5 path to either.
2. **MQL5 cannot express the research.** rd-a…rd-h is matched placebos,
   FDR, strata-weighted baselines, block bootstraps. MT5's strategy tester
   answers *"what would this EA have returned"*, not *"is this difference
   distinguishable from a control."*
3. **Risk separation.** A non-negotiable rule here is that Python cannot
   place orders and every order passes the Java `RiskGateway`. In MT5 the
   EA **is** the order placer and risk lives in the same program. The
   29-digit-quantity incident and the mock-signal near-miss are both cases
   where that separation is what caught the problem.

**Tick-level fill modelling — declined as a port, accepted as a standard.**
`fill.py` is bar-level and CLAUDE.md already records the gap: *"no
order-book depth, spread, or liquidity modeling of any kind."* MT5's tick
modelling is better on that axis, for FX. It is not portable, and
[`rd-f`](rd-f-korean-cost-structure.md) §1.2's one-tick floor is the same
gap showing up on KRX. Recorded as a known deficit, not adopted.

**"AI writes the EA, you run it" as a workflow.** Explicitly not adopted.
That loop is what produced the 1,883 formula variants already in the log,
of which zero survived.

## 5. What this does not claim

- **Nothing about whether any strategy works.** The surface tool reads
  already-logged numbers; it computes no new result and spends no `N`.
- **The four verdicts in §3 are not new evidence against those
  strategies.** They were rejected on their own terms, and a spike on a
  spent window is a statement about the sweep, not a fresh conclusion.
- **`plateau_ratio >= 0.5` is a convention**, not a derived threshold. It
  is an argument on the function precisely so it cannot harden into a law
  by being a constant.
- **The post was read through a summariser** (§0), so this is a response
  to its stated checklist, not a line-by-line review.
