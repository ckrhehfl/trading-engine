"""RD-B stage 1 on BTC 1m: which catalogue situations occur often enough?

**Counts only.** No forward return of any situation is computed, so this
is feasibility, not evidence, and it spends no `N` and no holdout. rd-b
§4 stage 1: *"Which situations even occur often enough? -- instrument:
event counts -- cost: none."*

The threshold it is measured against is rd-b §1's: a setup firing about
**20 times a year** is measurable over a 7-year window (t = 1.77 for a
0.3% effect against 2% dispersion). Below roughly 5/year nothing is.

Every situation is **episode-collapsed** with a stated cooldown, because
consecutive bars satisfying one condition are one event, not many -- the
rule PR #167 review correctly demanded be written down.

Run on the designated discovery window (Binance futures 1m, already
closed to selection), per CLAUDE.md's Discovery/Confirmation split.
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import deque

import numpy as np

from data._paths import DEFAULT_DB_PATH

DB = DEFAULT_DB_PATH
SYMBOL = "BINANCE-FUTURES:BTCUSDT"
COOLDOWN = 240  # 4h, the forward window rd-b uses; one episode per cooldown


def load():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT open_time_ms, open, high, low, close, volume, taker_buy_base_volume "
        "FROM klines WHERE symbol=? AND interval='1m' ORDER BY open_time_ms",
        (SYMBOL,),
    ).fetchall()
    conn.close()
    t = np.array([r[0] for r in rows], dtype=np.int64)
    o, h, l, c, v = (np.array([float(r[i]) for r in rows]) for i in range(1, 6))
    tb = np.array([float(r[6]) if r[6] is not None else np.nan for r in rows])
    return t, o, h, l, c, v, tb


def roll_extreme(a: np.ndarray, k: int, want_min: bool) -> np.ndarray:
    """Extreme over the k bars STRICTLY BEFORE each bar."""
    n = a.size
    out = np.full(n, np.nan)
    dq: deque[int] = deque()
    for i in range(n):
        while dq and dq[0] <= i - 1 - k:
            dq.popleft()
        if i >= k and dq:
            out[i] = a[dq[0]]
        while dq and ((a[dq[-1]] >= a[i]) if want_min else (a[dq[-1]] <= a[i])):
            dq.pop()
        dq.append(i)
    return out


def roll_sum_prior(a: np.ndarray, w: int) -> np.ndarray:
    """Sum of the w values ending at the previous bar."""
    n = a.size
    cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(a))])
    out = np.full(n, np.nan)
    idx = np.arange(n)
    # `idx >= w`, not `w + 1`: at index w the prior w values are a[0..w-1],
    # which is exactly one full window. The stricter bound left the first
    # valid window NaN and delayed every volume signal by one bar.
    ok = idx >= w
    hi = idx[ok]
    out[ok] = cs[hi] - cs[hi - w]
    return out


def episodes(idx: np.ndarray, cooldown: int = COOLDOWN) -> int:
    last = -10**9
    n = 0
    for i in idx:
        if i - last >= cooldown:
            n += 1
            last = int(i)
    return n


#: Round-trip cost used by the feasibility ceiling. `scalp-s9` measured
#: BTC perpetual costs at ~12bp (5bp taker fee each way plus ~1bp
#: slippage); the fee dominates the spread by roughly 330x.
DEFAULT_ROUND_TRIP = 0.0012


def feasibility(per_year: float, round_trip: float = DEFAULT_ROUND_TRIP) -> tuple[float, str]:
    """`(annual cost drag, verdict)` for a situation firing `per_year` times.

    **rd-b gave stage 1 a floor and no ceiling, and the ceiling turns out
    to bind far harder.** A situation traded `per_year` times at a
    round-trip cost of `round_trip` burns `per_year * round_trip` a year
    in costs *before any edge exists*. When that figure is an implausible
    fraction of annual return, no stage-2 measurement can rescue the
    situation -- the per-event effect would have to be smaller than the
    spread it must cross twice.

    This is why the rare situations are the good ones, and it reaches the
    same conclusion as `rd-c` section 2 ("the filter is the strategy")
    from pure arithmetic rather than from the literature.
    """
    drag = per_year * round_trip
    if per_year < 5:
        return drag, "too rare"
    if drag > 1.0:
        return drag, "COST-INFEASIBLE"
    if drag > 0.35:
        return drag, "cost-hostile"
    return drag, "feasible"


def pct_rank_threshold(a: np.ndarray, q: float) -> float:
    """The `q`-quantile of the finite values of `a`.

    **Whole-array, so it is lookahead if applied per-bar** -- kept only
    for one-shot reporting over a closed window. Per-bar classification
    must use `trailing_quantile`.
    """
    finite = a[np.isfinite(a)]
    return float(np.quantile(finite, q)) if finite.size else np.nan


#: Recalibration cadence and lookback for `trailing_quantile`, in bars.
#: One trading day and thirty, at 1m resolution. A trader recalibrates
#: periodically rather than every bar, and a daily step keeps 3.66M bars
#: to ~2,500 quantile computations.
QUANTILE_STEP = 1440
QUANTILE_WINDOW = 43_200


def trailing_quantile(
    a: np.ndarray,
    q: float,
    window: int = QUANTILE_WINDOW,
    step: int = QUANTILE_STEP,
) -> np.ndarray:
    """Per-bar `q`-quantile computed from **prior bars only**.

    **This exists because the first version of this module used a
    whole-array quantile**, so a bar at time `i` was classified as
    "top 10% of volume" using volume from after `i`. That is precisely
    what CLAUDE.md's look-ahead clause forbids -- *"no feature may be
    computed using statistics ... derived from data outside what would
    actually have been available at that point in time"* -- and it moved
    the event counts and therefore the cost verdicts.

    The threshold is recomputed every `step` bars from the preceding
    `window` bars and held constant until the next recalibration, so a bar
    is never classified using itself or anything after it. Bars before the
    first full window get `NaN` and are excluded rather than guessed.
    """
    n = a.size
    out = np.full(n, np.nan)
    for start in range(window, n, step):
        prior = a[max(0, start - window) : start]
        finite = prior[np.isfinite(prior)]
        if finite.size:
            out[start : start + step] = float(np.quantile(finite, q))
    return out


def main() -> int:
    t, o, h, l, c, v, tb = load()
    n = c.size
    years = (t[-1] - t[0]) / 1000 / 86400 / 365.25
    print(f"{SYMBOL}  {n:,} 1m bars  {years:.2f} years")
    print(f"episode cooldown: {COOLDOWN} bars\n")

    prior_low_1d = roll_extreme(l, 1440, True)
    prior_high_1d = roll_extreme(h, 1440, False)
    prior_low_4h = roll_extreme(l, 240, True)
    prior_high_4h = roll_extreme(h, 240, False)

    # Every threshold below is a TRAILING quantile: computed from prior
    # bars only and held until the next recalibration. A whole-array
    # quantile would classify a bar using data from after it.
    vol60 = roll_sum_prior(v, 60)
    vol60_hi = trailing_quantile(vol60, 0.90)
    vol60_top1 = trailing_quantile(vol60, 0.99)

    rng = h - l
    # trailing 60-bar high-low range, prior-only
    ph60 = roll_extreme(h, 60, False)
    pl60 = roll_extreme(l, 60, True)
    rng60 = (ph60 - pl60) / c
    rng60_lo = trailing_quantile(rng60, 0.10)

    body = np.abs(c - o) / c
    body_lo = trailing_quantile(body, 0.10)

    taker_sell = v - tb
    with np.errstate(invalid="ignore"):
        imb = np.where(v > 0, np.abs(tb - taker_sell) / v, np.nan)
    imb_hi = trailing_quantile(imb, 0.95)

    # round numbers: BTC trades in thousands; use 1,000 USD grid
    grid = 1000.0
    dist_round = np.abs(c - np.round(c / grid) * grid) / c

    situations: list[tuple[str, np.ndarray, str]] = []

    def add(name, mask, note=""):
        mask = mask & np.isfinite(c)
        situations.append((name, np.where(mask)[0], note))

    add("1  support penetration (prior-1d low, 0.3%)",
        np.isfinite(prior_low_1d) & (l < prior_low_1d * 0.997))
    add("1b support penetration (prior-4h low, 0.1%)",
        np.isfinite(prior_low_4h) & (l < prior_low_4h * 0.999))
    add("2  resistance break (prior-1d high, 0.3%)",
        np.isfinite(prior_high_1d) & (h > prior_high_1d * 1.003))
    add("2b resistance break (prior-4h high, 0.1%)",
        np.isfinite(prior_high_4h) & (h > prior_high_4h * 1.001))
    add("3  round-number touch (within 0.02% of a $1k level)",
        dist_round < 0.0002)
    add("4  abnormal activity (60m volume, top 10%)",
        np.isfinite(vol60) & np.isfinite(vol60_hi) & (vol60 >= vol60_hi))
    add("4b abnormal activity (60m volume, top 1%)",
        np.isfinite(vol60) & np.isfinite(vol60_top1) & (vol60 >= vol60_top1))
    add("7  range expansion after compression",
        np.isfinite(rng60) & np.isfinite(rng60_lo) & (rng60 <= rng60_lo) & (rng > (ph60 - pl60) * 0.5))
    add("10 absorption (volume top 10%, body bottom 10%)",
        np.isfinite(vol60) & np.isfinite(vol60_hi) & (vol60 >= vol60_hi) & np.isfinite(body) & np.isfinite(body_lo) & (body <= body_lo))
    # FVG: bar i-1 high < bar i+1 low  (evaluated at i+1, no future use)
    fvg_up = np.zeros(n, dtype=bool)
    fvg_up[2:] = h[:-2] < l[2:]
    add("12 fair value gap, bullish (3-bar imbalance)", fvg_up)
    fvg_dn = np.zeros(n, dtype=bool)
    fvg_dn[2:] = l[:-2] > h[2:]
    add("12b fair value gap, bearish", fvg_dn)
    add("--  taker imbalance extreme (top 5%)",
        np.isfinite(imb) & np.isfinite(imb_hi) & (imb >= imb_hi))

    # rd-b gave stage 1 a FLOOR (>=20/yr to be measurable) and no CEILING.
    # The ceiling is the more binding constraint and it is pure arithmetic:
    # a situation firing F times a year, traded at round-trip cost kappa,
    # burns F*kappa a year in costs BEFORE any edge. If that is an
    # implausible fraction of annual return, no stage-2 measurement can
    # rescue it -- the per-event effect would have to be under the spread.
    MAX_EPISODES = n / COOLDOWN

    print(f"{'situation':52} {'raw':>9} {'episodes':>8} {'/yr':>6} {'cost/yr':>8}  verdict")
    print("-" * 106)
    for name, idx, note in situations:
        ep = episodes(idx)
        per_year = ep / years
        cost_drag, verdict = feasibility(per_year)
        if ep > 0.5 * MAX_EPISODES:
            verdict += " (near-continuous)"
        print(f"{name:52} {idx.size:>9,} {ep:>8,} {per_year:>6.0f} {cost_drag:>7.0%}  {verdict}")

    print()
    print(f"cooldown ceiling: {MAX_EPISODES:,.0f} episodes max ({MAX_EPISODES/years:,.0f}/yr).")
    print("A situation near it is not a condition -- it fires essentially always.")
    print(f"round trip assumed: {DEFAULT_ROUND_TRIP*1e4:.0f}bp (scalp-s9, BTC perps).")

    print()
    print("rd-b section 1 thresholds over a ~7y window, m=0.3%, s=2%:")
    for k in (5, 20, 50, 100):
        nn = k * years
        print(f"   {k:>3}/yr -> n={nn:>5.0f}  t={0.003/(0.02/np.sqrt(nn)):.2f}")
    print()
    print("NO forward return was computed. This is feasibility, not evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
