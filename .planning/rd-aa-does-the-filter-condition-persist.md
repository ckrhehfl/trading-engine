# Research Direction Task AA — does the filter condition last long enough to be usable?

**The tension this settles, and it had to be settled before any filter was
specified.** Two of this project's own conclusions pointed in incompatible
directions:

- **`rd-c` named the selection filter as the axis never searched**, citing four
  sources that do not cite each other. The sharpest is Zarattini's "Stocks in
  Play": Sharpe 2.81 from a plain opening-range breakout **restricted to
  abnormally active names**, where the same entry rule fails entirely without
  the filter.
- **`rd-z` measured the reserved window's cost ceiling**, and it admits only
  **months-long holding**. At 24 round trips a year the Korean 33bp round trip
  costs more than a 0.5-Sharpe strategy's entire gross return.

So the literature's implementation is intraday and the window's economics forbid
intraday. **A filter is only transferable if the condition it selects on
lasts** — a one-day surge that mean-reverts by the next session cannot inform a
position held for two months, and selecting on it would be selecting on noise.

**This measures persistence and looks at no returns.** It selects nothing, so it
is a data-characteristic measurement in the same category as `rd-y`'s coverage
report rather than a discovery trial. The moment a return enters, that changes.

---

## 1. What a filter can be built from here, which is less than hoped

Measured on the 2019+ full-universe panel (the spent window):

| column | non-null | non-zero |
|---|---|---|
| open / high / low / close | 100% | 100% |
| volume | 100% | 95.8% |
| turnover | 100% | 95.8% |

The 4.2% that is zero is exactly the frozen set, so **the usable panel is
4,406,351 bars.**

**And the one genuinely new source this project has is not in it.** `rd-c`
singled out 투자자별 매매동향 — per-stock investor flow, mandatorily disclosed in
Korea and merely *inferred* in the entire US literature — as the only new
information source. It begins 2026-08-03 and holds **41 trading days**, which
`rd-z`'s own arithmetic puts far below usable: a 41-session window floors at an
annualized Sharpe in the double digits.

So a per-day selection filter on this panel is built from **OHLCV and turnover,
and nothing else.** That is a real narrowing of what `rd-c` proposed.

## 2. Turnover is dispersed enough for "abnormally active" to mean something

Percentiles of daily turnover, frozen bars excluded:

| | turnover |
|---|---|
| p1 | 2.0M won |
| p25 | 177M |
| p50 | 692M |
| p75 | 3.2B |
| p90 | 13.6B |
| p99 | 141.1B |
| p99.9 | 687.4B |

**Three orders of magnitude from p50 to p99.9.** A filter has room to separate
names here, which was not guaranteed — a tightly clustered distribution would
have left nothing to select on.

The pool itself grows from **2,118 to 2,508** names traded per session across
2019–2026, so the selection problem gets larger over the panel rather than
smaller.

## 3. The measurement: a surge persists for months

Relative turnover is each day's turnover over the name's own trailing 60-session
**median** — a median so one prior surge does not raise the bar for detecting the
next, and a multiple rather than a percentile so the definition does not drift
as the pool's activity does (`rd-x` measured that drift at +54%/yr).

A **surge** is relative turnover ≥ 3.0. The comparison arm is an **ordinary day**
for the same name, 0.8–1.25, deliberately narrow: a wide band would put mild
surges in the control and shrink every ratio by construction.

**Both arms are divided by the event day's own baseline at every horizon**, and
the reason is the opposite of what it first looks like. A fresh baseline at each
horizon would absorb any rise into its own denominator, pulling every follow-up
back toward 1.0 and **understating** persistence — a name that stayed genuinely
elevated would read as having faded, because its new normal *is* the elevated
level. Holding the event day's baseline fixed measures against what was normal
before the surge, which is the question.

**What the shared normalisation buys is comparability, which is weaker than
cancelling a trend.** A trend common to both arms largely divides out; a trend
that differs *between* the arms does not, and nothing here corrects for that.
So **a ratio of 1.0 means the two arms' follow-up medians are equal** — the
surge carries no information at that horizon — rather than "the surge
evaporated" in any absolute sense.

281 codes (every tenth completed name), 55,128 surge days against 81,354 ordinary
days:

| horizon | after a surge | after an ordinary day | ratio |
|---|---|---|---|
| 1 session | 4.02 | 0.91 | **4.41x** |
| 5 | 2.69 | 0.91 | **2.95x** |
| **20** | 1.82 | 0.92 | **1.97x** |
| 60 | 1.46 | 0.95 | **1.54x** |

**A surged name is still about twice as active a month later, and 1.5x after
three months.** The condition does not evaporate on the timescale the window's
cost ceiling forces.

## 4. What this does and does not establish

**Establishes**: the axis `rd-c` named is compatible with the window `rd-z`
characterised. A filter selecting on abnormal activity is selecting on something
that is still true when a months-long position would be held.

**Does not establish, and must not be read as**: that such a filter is
profitable. "Still active a month later" is a statement about turnover, not about
returns. Nothing here measured a return, and the whole reason this was done first
is that measuring returns is the step that spends a look.

**Two limitations of the measurement itself:**

- **One surge definition.** 3.0x on a 60-session median, not swept. This project
  has recorded the single-parameter-setting error five times, so: the persistence
  figure above is for this definition and a different threshold could behave
  differently. What makes it defensible as a *first* measurement is that the
  claim is directional — persistence exists — rather than a specific magnitude
  being load-bearing.
- **Every tenth name.** 281 of 3,043, chosen by code order rather than at
  random. Nothing suggests code order correlates with activity, but it was chosen
  to bound the pass rather than for statistical reasons.

## 5. What comes next, and what it needs first

The open question is now narrow and answerable: **does a portfolio selected on
abnormal activity, held for months, long-only, behave differently from one that
is not?**

That measures returns, so it is a discovery trial: it runs on this spent window
under `CLAUDE.md`'s Discovery guards, is logged, does **not** increment the
promotion `N`, and its only legitimate output is a written specification.

Two things it must carry from here:

- **Frozen bars excluded**, which is `CLAUDE.md`'s standing rule and measurably
  load-bearing: leaving halts in a baseline window halves its median, so a name's
  *unchanged* activity afterwards reads as a 2x surge. A filter built without the
  exclusion would partly be selecting on halt recovery.
- **Limit-locked bars included.** Equal OHLC with real turnover is a tradeable
  session at the daily limit, and `rd-y` found 16,200 of them. An OHLC-only
  frozen test would exclude exactly the names that traded most intensely.
