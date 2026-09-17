"""Do conditions COMBINED say more than any one of them? -- the question rd-t did not ask.

[`rd-t`](../../.planning/rd-t-korean-signal-ic.md) measured **eight single
features** at three horizons and found that only the cross-sectional ones
carry. It is tempting to read that as *"timing does not work in Korea"*.
That reading is wrong, and this project has a name for the mistake:
**never conclude about a domain from one parameter setting.**

A trader does not use a single feature. A trader uses a **conjunction** --
*"the trend is intact AND a pullback has happened AND volume came in AND
it has not broken"* -- and a conjunction is a different object from its
parts. Trade Management Task C measured exactly this and produced the one
positive result in that arc:

    four conditions firing   364 / 348 / 479 / 912 times individually
    and jointly ................................ 2 times in 2,544 bars

Far below what correlated conditions would produce, so the independence
premise held. **What killed Task C was not the conjunction; it was that
2 firings cannot be measured**, plus a parameter-free exit that pinned the
holding period to one hour and so tested something other than the
hypothesis described.

Both of those are design errors with known fixes, and this module applies
them.

## Fix 1: median splits, not extreme thresholds

Task C's conditions were extreme (a tail of each distribution), so their
conjunction was vanishingly rare. Every condition here is a
**cross-sectional median split** -- above or below the universe's median
that day. Each fires ~50% of the time by construction, so a three-way
conjunction fires ~12.5% rather than 0.08%.

That has a second benefit that matters more than the sample size: **a
median split has no free parameter to fit.** There is no threshold to
search, so there is nothing to overfit and nothing to add to `N`.

## Fix 2: the holding period is declared, not implied

Task C's exit was "the setup no longer holds", which any one of three
conditions relaxing could end -- so the holding period was chosen by
accident. Here the horizon is **stated up front** and the position is held
for exactly that long.

## Fix 3: a day is one observation, not ten

A cross-sectional study has an independence problem a single-symbol one
does not: ten names on the same day share a market-wide move, so pooling
11,740 name-days as 11,740 draws understates the standard error. This is
S13's overlapping-excursion error wearing different clothes, and CLAUDE.md
already carries the standing rule -- *deduplicate to non-overlapping
samples before reporting a t-statistic, or say that you did not.*

Every statistic here is therefore computed over **dates**, each date
contributing the mean across whichever names fired on it. The correction
is not a uniform haircut and was not predictable from the naive figures:
on the real panel one p-value moved **0.016 -> 0.182** and another
**0.113 -> 0.039**, and the count of combinations surviving
Benjamini-Hochberg went from **1 to 0**.

## What the conditions are, and where each came from

**Nothing here was searched for.** Every condition is adopted from a
finding that already exists, which is what keeps this at one hypothesis
rather than a new search:

| condition | source | mechanism |
|---|---|---|
| overnight vs intraday split | **Lou, Polk & Skouras (2019)**, JFE 134(1) 192-213 | retail trades the open, institutions the close; the two components carry opposite signs |
| recent relative move | `rd-t` | cross-sectional short-horizon reversal |
| volatility regime | `scalp-s10` | the continuous absolute measure, used as a conditioner |

**The Lou-Polk-Skouras mechanism is observable in Korea and was only
inferable in theirs.** Their tug of war is between investor clienteles,
and they had to proxy it (institutional active weight, "comomentum").
KRX **mandatorily discloses** 개인/기관/외국인 per stock per day. That is
the single strongest reason to run this here rather than anywhere else --
and it is also why the study is not complete yet, because collection of
that series only started 2026-09-14.

**Their construction does not transfer as written, and that is stated
rather than glossed.** They sort a universe of thousands into deciles and
trade the extreme tenths. Ten names make a "decile" one name, which is
noise. So the decomposition is measured as a **panel** question -- does
the overnight component predict the intraday one -- rather than as a
decile long-short.

## Why daily, and why that is the point

One decision per name per day. At rd-q's ~13bp round trip a daily
strategy pays ~3.3% a year in costs, which is a real hurdle but a
*payable* one -- against the scalping arc, where ~70 round trips a day
made the cost structure unpayable at any edge. **Cost per unit of signal
is what killed every previous candidate**, and frequency is the lever on
it.

Run:

    python -m research.krx_conjunction
"""

from __future__ import annotations

import argparse
import itertools
import math
import sqlite3
import sys
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.ic import benjamini_hochberg
from research.krx_signal_ic import UNIVERSE

INTERVAL = "1d"

#: rd-q's measured round trip for the futures-liquid names. A conditional
#: mean has to clear this to be worth anything, and it is reported beside
#: every figure rather than applied at the end.
COST_FLOOR_BP = 13.0

#: Held for exactly this many days after the decision. Declared here
#: rather than emerging from an exit rule -- Task C's holding period was
#: chosen by accident and tested a different hypothesis than the one
#: described.
HOLD_DAYS = (1, 5)

#: Trailing window for the overnight/intraday decomposition, in trading
#: days. Lou-Polk-Skouras sort on a lagged **one month** of it; 21 is the
#: Korean trading month. Not searched -- taken from the paper.
LOOKBACK_DAYS = 21


@dataclass(frozen=True)
class Panel:
    """One aligned panel: dates x names, with the pieces of each day's move."""

    dates: list[int]
    codes: list[str]
    open_px: np.ndarray          # (dates, names)
    close_px: np.ndarray
    prev_close: np.ndarray

    @property
    def overnight(self) -> np.ndarray:
        """`open_t / close_{t-1} - 1`. The component retail is said to move."""
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(
                self.prev_close > 0, self.open_px / self.prev_close - 1.0, np.nan
            )

    @property
    def intraday(self) -> np.ndarray:
        """`close_t / open_t - 1`. The component institutions are said to move,
        and the one a futures position can actually capture in a day."""
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(self.open_px > 0, self.close_px / self.open_px - 1.0, np.nan)


def load_panel(conn: sqlite3.Connection, codes: list[str]) -> Panel:
    """Align every name onto the dates they all have.

    An inner join on date, deliberately: a cross-sectional median is
    meaningless if the cross-section changes composition day to day, and
    a name missing a day would otherwise shift the median under it.
    """
    per: dict[str, dict[int, tuple[float, float]]] = {}
    for code in codes:
        rows = conn.execute(
            "SELECT open_time_ms, CAST(open AS REAL), CAST(close AS REAL) FROM klines "
            "WHERE symbol=? AND interval=? ORDER BY open_time_ms",
            (f"KRX:{code}", INTERVAL),
        ).fetchall()
        if not rows:
            raise ValueError(
                f"no {INTERVAL} bars for KRX:{code}. The instance collects these; "
                f"run scripts/sync-krx-from-instance.sh first."
            )
        per[code] = {int(r[0]): (float(r[1]), float(r[2])) for r in rows}

    common = sorted(set.intersection(*(set(v) for v in per.values())))
    if len(common) < LOOKBACK_DAYS + max(HOLD_DAYS) + 2:
        raise ValueError(
            f"only {len(common)} dates common to all {len(codes)} names, which is "
            f"too few to measure anything. The newest names list later than the "
            f"others -- check per-name history before widening the universe."
        )
    # An end effect is fine and expected; a hole is not. See
    # `interleaved_sessions` -- a dropped session mid-span makes the
    # overnight legs either side of it span two nights, silently.
    holes = interleaved_sessions(common, sorted(set.union(*(set(v) for v in per.values()))))
    if holes:
        raise ValueError(
            f"{len(holes)} session(s) traded by some name were dropped from INSIDE "
            f"the common span (first {holes[0]}). The overnight legs either side of "
            f"a hole span two nights and would be reported as one."
        )
    o = np.array([[per[c][d][0] for c in codes] for d in common])
    cl = np.array([[per[c][d][1] for c in codes] for d in common])
    pc = np.vstack([np.full((1, len(codes)), np.nan), cl[:-1]])
    return Panel(common, list(codes), o, cl, pc)


def _above_median(row: np.ndarray) -> np.ndarray:
    """True where a name is above that day's cross-sectional median.

    **No free parameter.** A median split is fixed by the data rather than
    chosen, so there is no threshold to search and nothing to overfit --
    and each condition fires on about half the universe, which is what
    keeps a three-way conjunction measurable where Task C's extreme
    thresholds left it at two firings in 2,544 bars.
    """
    ok = np.isfinite(row)
    out = np.zeros(row.shape, dtype=bool)
    if ok.sum() < 3:
        return out
    out[ok] = row[ok] > np.median(row[ok])
    return out


def _trailing_mean(component: np.ndarray, n: int) -> np.ndarray:
    out = np.full(component.shape, np.nan)
    for t in range(n, component.shape[0]):
        window = component[t - n : t]
        with np.errstate(invalid="ignore"):
            out[t] = np.nanmean(window, axis=0)
    return out


@dataclass(frozen=True)
class Condition:
    name: str
    source: str
    mask: np.ndarray             # (dates, names) bool


def build_conditions(panel: Panel) -> list[Condition]:
    """Every condition adopted from an existing finding, none searched."""
    on, intra = panel.overnight, panel.intraday
    on_1m = _trailing_mean(on, LOOKBACK_DAYS)
    intra_1m = _trailing_mean(intra, LOOKBACK_DAYS)
    total_5 = _trailing_mean(on + intra, 5)
    vol_21 = np.full(on.shape, np.nan)
    for t in range(LOOKBACK_DAYS, on.shape[0]):
        with np.errstate(invalid="ignore"):
            vol_21[t] = np.nanstd((on + intra)[t - LOOKBACK_DAYS : t], axis=0)

    def per_day(values: np.ndarray, invert: bool = False) -> np.ndarray:
        out = np.zeros(values.shape, dtype=bool)
        for t in range(values.shape[0]):
            above = _above_median(values[t])
            out[t] = ~above & np.isfinite(values[t]) if invert else above
        return out

    return [
        Condition(
            "overnight_strong_1m", "Lou-Polk-Skouras 2019", per_day(on_1m)
        ),
        Condition(
            "intraday_weak_1m", "Lou-Polk-Skouras 2019", per_day(intra_1m, invert=True)
        ),
        Condition("recent_laggard_5d", "rd-t (cross-sectional reversal)",
                  per_day(total_5, invert=True)),
        Condition("vol_low_21d", "scalp-s10 (conditioner)", per_day(vol_21, invert=True)),
    ]


@dataclass(frozen=True)
class Outcome:
    """One condition's outcome, with **two** sample sizes that are not
    interchangeable.

    `firings` is name-days -- how often the condition applied. `dates` is
    how many *independent* observations that amounts to, and it is the one
    the t-statistic is computed on. Ten names on one day share a
    market-wide move, so pooling them as ten observations understates the
    standard error exactly the way S13's overlapping excursions did. The
    correction is not cosmetic: it moved this study's own p-values from
    0.016 to 0.182 in one place and from 0.113 to 0.039 in another.
    """

    label: str
    firings: int
    dates: int
    mean_bp: float | None
    t_stat: float | None
    p_value: float | None

    def excess_bp(self, base_bp: float) -> float | None:
        """How far this condition's mean sits from the unconditional one.

        Signed, because **the edge here is on the short side** and an
        earlier version of this module could not see that: it tested
        `mean - base > COST_FLOOR` and so recognised a long edge only.
        Every conditional mean in the first real run was negative, so that
        test reported nothing while the result was entirely short.
        """
        return None if self.mean_bp is None else self.mean_bp - base_bp

    def clears_cost(self, base_bp: float) -> bool:
        """A round trip is paid in either direction, so the test is on the
        magnitude of the excess -- never on its sign."""
        excess = self.excess_bp(base_bp)
        return excess is not None and abs(excess) > COST_FLOOR_BP

    def direction(self, base_bp: float) -> str:
        excess = self.excess_bp(base_bp)
        if excess is None:
            return "-"
        return "SHORT" if excess < 0 else "LONG"


def _summarise(label: str, forward: np.ndarray, selected: np.ndarray) -> Outcome:
    """Summarise `forward` over `selected`, **one observation per date.**

    Each date contributes the mean across whichever names fired that day,
    so a day on which eight names qualify does not outvote a day on which
    one does, and the cross-sectional correlation between those eight is
    not counted as eight independent draws.
    """
    firings = int(selected.sum())
    per_date = [
        float(forward[t][selected[t]].mean())
        for t in range(selected.shape[0])
        if selected[t].any()
    ]
    if len(per_date) < 3:
        return Outcome(label, firings, len(per_date), None, None, None)
    arr = np.array(per_date)
    mean = float(arr.mean()) * 1e4
    sd = float(arr.std(ddof=1))
    if sd <= 0:
        return Outcome(label, firings, arr.size, mean, None, None)
    t = float(arr.mean()) / (sd / math.sqrt(arr.size))
    return Outcome(
        label, firings, arr.size, mean, t, 2 * (1 - NormalDist().cdf(abs(t)))
    )


def forward_total(panel: Panel, hold: int) -> np.ndarray:
    """Return from tomorrow's open to the close `hold` days later.

    Entered at the **next** open, so a decision made from today's close
    is acted on at a price nobody had seen when the decision was made.
    """
    o, c = panel.open_px, panel.close_px
    n = o.shape[0]
    out = np.full(o.shape, np.nan)
    for t in range(n - hold - 1):
        entry, exit_ = o[t + 1], c[t + hold]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[t] = np.where(entry > 0, exit_ / entry - 1.0, np.nan)
    return out


def measure(panel: Panel, conditions: list[Condition], hold: int) -> list[Outcome]:
    fwd = forward_total(panel, hold)
    finite = np.isfinite(fwd)
    rows = [_summarise("(unconditional)", fwd, finite)]
    for cond in conditions:
        rows.append(_summarise(cond.name, fwd, cond.mask & finite))
    # Every pair and the full conjunction — the question is whether any
    # combination beats its own best part, not whether it beats nothing.
    for r in sorted({2, len(conditions)}):
        for combo in itertools.combinations(range(len(conditions)), r):
            sel = finite.copy()
            for i in combo:
                sel &= conditions[i].mask
            label = " & ".join(conditions[i].name for i in combo)
            rows.append(_summarise(label, fwd, sel))
    return rows


def independence(panel: Panel, conditions: list[Condition]) -> tuple[float, float]:
    """`(observed joint rate, rate if the conditions were independent)`.

    Task C's one positive result was this comparison coming out far below
    the independent product, which is what makes a conjunction worth
    forming at all: conditions that always fire together add nothing.
    """
    joint = np.ones(conditions[0].mask.shape, dtype=bool)
    product = 1.0
    for cond in conditions:
        joint &= cond.mask
        product *= float(cond.mask.sum()) / max(cond.mask.size, 1)
    return float(joint.sum()) / max(joint.size, 1), product


def decomposition_gap(panel: Panel) -> float:
    """The largest amount by which the two legs fail to rebuild the move
    from the **previous row's** close.

    **The obvious version of this check is a tautology, and writing it
    that way first is what surfaced the distinction.** Composing the two
    legs gives `(open/pc)·(close/open) - 1 = close/pc - 1`, so comparing
    that against a close-to-close return *also* computed from `prev_close`
    is an algebraic identity -- it holds for any `prev_close` whatsoever
    and cannot fail. A test written to watch it fail is what caught it,
    which is this project's own rule about removing a guard and watching
    the test break, arriving one step earlier than usual.

    So the comparison is against `close_px` shifted by one row instead,
    which asks the question that can actually be wrong: **does the
    overnight leg gap from the close immediately before it?** A
    `prev_close` that is anything else -- a stale row, a misaligned join,
    a hand-built panel -- is then caught, and the run refuses rather than
    reporting figures derived from a gap that spans the wrong night.
    """
    close = panel.close_px
    if close.shape[0] < 2:
        return math.inf
    with np.errstate(divide="ignore", invalid="ignore"):
        c2c = np.where(close[:-1] > 0, close[1:] / close[:-1] - 1.0, np.nan)
    rebuilt = ((1 + panel.overnight) * (1 + panel.intraday) - 1)[1:]
    diff = np.abs(rebuilt - c2c)
    if not np.isfinite(diff).any():
        return math.inf
    return float(np.nanmax(diff))


def interleaved_sessions(kept: list[int], seen: list[int]) -> list[int]:
    """Dates some name traded that the aligned panel dropped from **inside**
    its own span -- rd-p's gap rule, on the daily axis.

    `load_panel` inner-joins on date, so one name missing one session drops
    that session for everyone. The overnight leg either side of the hole
    then spans two nights and is reported as one, which is exactly the
    error rd-p found on the intraday tape and nothing had looked for here.

    Measured on the real ten-name panel: 718 dates are dropped and **none
    of them lies inside the common span** -- every drop is an end effect
    from the eight newer names listing later. So the alignment is sound on
    this data, which is a measurement and not a property, and it stops
    being sound the first time a name halts for a day mid-window.
    """
    if not kept:
        return []
    lo, hi = kept[0], kept[-1]
    held = set(kept)
    return sorted(d for d in seen if lo < d < hi and d not in held)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=",".join(UNIVERSE))
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    try:
        panel = load_panel(conn, codes)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    conditions = build_conditions(panel)
    print(
        f"panel: {len(panel.dates)} dates x {len(panel.codes)} names "
        f"= {len(panel.dates) * len(panel.codes):,} name-days\n"
    )

    on_m = float(np.nanmean(panel.overnight)) * 1e4
    id_m = float(np.nanmean(panel.intraday)) * 1e4
    # The two components must rebuild the close-to-close move, or the
    # decomposition is wrong and every figure below is meaningless.
    gap = decomposition_gap(panel)
    if gap > 1e-9:
        print(
            f"refused: overnight and intraday do not compose to the "
            f"close-to-close move (max gap {gap:.2e}). The decomposition is "
            f"wrong, so nothing below would mean anything.",
            file=sys.stderr,
        )
        return 1
    print("=== the decomposition Lou-Polk-Skouras is about ===")
    print(f"  mean overnight move : {on_m:+.2f} bp per day")
    print(f"  mean intraday move  : {id_m:+.2f} bp per day")
    print(
        "  their finding is that a strategy's profit sits ENTIRELY in one of "
        "these\n  and reverses in the other. Asian markets are documented to "
        "flip the US sign,\n  so the direction here is measured, never assumed.\n"
    )

    obs, indep = independence(panel, conditions)
    print("=== is the conjunction worth forming? ===")
    print(f"  all four fire together on {obs:.3%} of name-days")
    print(f"  if they were independent  {indep:.3%}")
    print(
        f"  {'far below — the conditions carry different information'
           if obs < indep * 0.6 else
           'close to independent, or overlapping — see the ratio'}\n"
    )

    for hold in HOLD_DAYS:
        rows = measure(panel, conditions, hold)
        base = rows[0].mean_bp or 0.0

        # **Eleven combinations are tested and the best is quoted**, which
        # is the selection problem this project corrects everywhere else.
        # BH over the conditionals only -- the unconditional row is the
        # baseline, not a hypothesis.
        conditional = rows[1:]
        survives = benjamini_hochberg([r.p_value for r in conditional])

        leg = (
            "the INTRADAY leg only (open -> close, no overnight gap)"
            if hold == 1
            else f"{hold - 1} overnight gaps PLUS {hold} intraday legs"
        )
        print(f"=== held {hold} day(s), entered at the NEXT open — {leg} ===")
        if hold > 1:
            print(
                "  *** CONFOUNDED, and the figures below are not findings. ***\n"
                f"  The baseline is {base:+.1f}bp because this window includes "
                f"{hold - 1} overnight gaps,\n"
                "  and this universe was selected in 2026Q1 for futures liquidity --\n"
                "  which correlates with having risen. So an 'excess' here is largely\n"
                "  a measure of WHICH NAMES DRIFTED, not of what a condition predicts.\n"
                "  'vol_low_21d clears the cost floor' reduces to 'low-volatility\n"
                "  names rose less than semiconductors did in 2019-2026', which is\n"
                "  close to a tautology. The h=1 block above is the clean read: its\n"
                "  baseline is ~0, so its excesses are signal rather than composition."
            )
        print(
            f"{'condition':<62} {'name-days':>9} {'dates':>6} {'mean bp':>9} "
            f"{'excess':>8} {'p':>8} {'BH':>4}"
        )
        print("-" * 112)
        print(
            f"{rows[0].label:<62} {rows[0].firings:>9,} {rows[0].dates:>6,} "
            f"{base:>+9.1f} {'':>8} {rows[0].p_value or 0:>8.3f} {'':>4}"
        )
        for row, keep in zip(conditional, survives):
            m = f"{row.mean_bp:+.1f}" if row.mean_bp is not None else "-"
            ex = row.excess_bp(base)
            e = f"{ex:+.1f}" if ex is not None else "-"
            pv = f"{row.p_value:.3f}" if row.p_value is not None else "-"
            flag = "ok" if keep else "no"
            mark = ""
            if row.clears_cost(base) and keep:
                mark = f"  << {row.direction(base)}, clears {COST_FLOOR_BP:.0f}bp"
            elif row.clears_cost(base):
                mark = "  (clears cost, fails BH)"
            print(
                f"{row.label:<62} {row.firings:>9,} {row.dates:>6,} {m:>9} "
                f"{e:>8} {pv:>8} {flag:>4}{mark}"
            )

        best = max(
            (r for r in conditional if r.excess_bp(base) is not None),
            key=lambda r: abs(r.excess_bp(base) or 0.0),
        )
        kept = sum(survives)
        print(
            f"\n  baseline {base:+.1f}bp; the bar is |excess| > {COST_FLOOR_BP:.0f}bp, "
            f"since a round trip is paid in either direction.\n"
            f"  largest excess: {best.excess_bp(base):+.1f}bp "
            f"({best.label}) — "
            f"{'CLEARS' if abs(best.excess_bp(base) or 0) > COST_FLOOR_BP else 'does NOT clear'}"
            f" the cost floor."
        )
        # Two separate tests, and a combination has to pass both. Saying how
        # many of the eleven survived is the part a reader needs in order to
        # discount the best one, and "none" is the most important case to
        # print rather than leave as an empty BH column.
        print(
            f"  {kept} of {len(conditional)} combinations survive "
            f"Benjamini-Hochberg at 0.05"
            f"{'. So the excess above is the best of eleven looks and is not '
               'distinguishable from noise.' if kept == 0 else '.'}\n"
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
