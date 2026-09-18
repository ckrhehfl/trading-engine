"""Is the cost floor the binding constraint on KRX? -- measured, not assumed.

[`rd-u`](../../.planning/rd-u-do-conjunctions-beat-their-parts.md) reported
that its best combination *"does not clear the cost floor"*. That sentence
is arithmetically true and invites exactly the wrong inference -- that
trading costs are what defeated it, and therefore that cheaper execution
or better infrastructure is the remedy.

This module exists so that reading is checkable rather than rhetorical. It
answers three questions on the same panel `krx_conjunction` uses:

1. **How large is a day's move against the round trip?** If the move is a
   small multiple of the cost, cost is binding. If it is a large multiple,
   the failure is prediction and nothing else.
2. **Is there any volatility regime where cost becomes binding?** A median
   over everything can hide a quiet regime where the move is small.
3. **Does predictability rise with volatility?** The natural response to a
   non-binding cost is to trade only the large moments -- the operator's
   own instinct, and `scalp-s8`'s retraction plus Zarattini's "Stocks in
   Play" both point at it. Whether it works here is a measurement.

**Every statistic here is computed with the DATE as the unit**, per
CLAUDE.md's session-clustering rule: ten names at one instant share that
instant's market-wide move and are not ten draws. `main` runs
`check_clustered_observations` against its own sample to prove it rather
than asserting it.

**What this module does NOT measure, stated because rd-u overreached on
exactly this point and CodeRabbit caught it.** Moves here are computed
from printed prices -- open to close -- with **no slippage, no spread, no
borrow, and no size-dependent execution effect** whatsoever. The 13bp
figure is `rd-q`'s measured round trip for the futures-liquid names, and
it is compared against a *theoretical* move. So a finding that the move is
ten times the cost says **there is no evidence in this data that execution
cost is the binding constraint**; it does not establish that execution
quality is irrelevant, because execution quality was never modelled.

Run:

    python -m research.krx_cost_context
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.conclusion_check import (
    check_clustered_observations,
    format_findings,
    require_no_blockers,
)
from research.ic import spearman
from research.krx_conjunction import COST_FLOOR_BP, Panel, load_panel
from research.krx_signal_ic import UNIVERSE

#: Reported percentiles of the absolute daily move. Chosen to bracket the
#: distribution rather than to find a flattering point on it.
MOVE_PERCENTILES = (25, 50, 75, 90, 99)

#: Trailing window for realised volatility, in trading days. One Korean
#: trading month, the same constant `krx_conjunction` takes from
#: Lou-Polk-Skouras. Not searched.
VOL_WINDOW = 21

#: Trailing window for the reversal signal, in trading days. `rd-t`'s
#: surviving signal is short-horizon cross-sectional reversal.
SIGNAL_WINDOW = 5

VOL_TERCILES = ("quiet", "middle", "volatile")


def close_to_close(panel: Panel) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(
            panel.prev_close > 0, panel.close_px / panel.prev_close - 1.0, np.nan
        )


def trailing_vol(panel: Panel, window: int = VOL_WINDOW) -> np.ndarray:
    """Realised volatility over the `window` days **before** each day.

    Strictly trailing: row `t` uses rows `t-window .. t-1`, so it is known
    before day `t` opens and conditioning on it is not look-ahead.
    """
    c2c = close_to_close(panel)
    out = np.full(c2c.shape, np.nan)
    for t in range(window, c2c.shape[0]):
        with np.errstate(invalid="ignore"):
            out[t] = np.nanstd(c2c[t - window : t], axis=0)
    return out


def trailing_return(panel: Panel, window: int = SIGNAL_WINDOW) -> np.ndarray:
    """Total return over the `window` days before each day -- the signal."""
    c2c = close_to_close(panel)
    out = np.full(c2c.shape, np.nan)
    for t in range(window, c2c.shape[0]):
        with np.errstate(invalid="ignore"):
            out[t] = np.nansum(c2c[t - window : t], axis=0)
    return out


def move_percentiles(
    moves_bp: np.ndarray, percentiles: tuple[int, ...] = MOVE_PERCENTILES
) -> list[tuple[int, float, float]]:
    """`[(percentile, bp, multiple of the round trip)]`."""
    finite = moves_bp[np.isfinite(moves_bp)]
    if finite.size == 0:
        return []
    return [
        (q, float(np.percentile(finite, q)), float(np.percentile(finite, q)) / COST_FLOOR_BP)
        for q in percentiles
    ]


def move_by_vol_bucket(
    moves_bp: np.ndarray, vol: np.ndarray, buckets: int = 10
) -> list[tuple[int, int, float, float]]:
    """`[(bucket, n, median move bp, multiple)]`, bucket 1 = calmest.

    Answers whether any volatility regime brings the move down to where
    the cost floor starts to bind.
    """
    ok = np.isfinite(moves_bp) & np.isfinite(vol)
    m, v = moves_bp[ok], vol[ok]
    if m.size < buckets:
        return []
    edges = np.percentile(v, np.linspace(0, 100, buckets + 1))
    out = []
    for b in range(buckets):
        sel = (v >= edges[b]) & (v <= edges[b + 1] if b == buckets - 1 else v < edges[b + 1])
        if sel.sum() < 30:
            continue
        med = float(np.median(m[sel]))
        out.append((b + 1, int(sel.sum()), med, med / COST_FLOOR_BP))
    return out


@dataclass(frozen=True)
class IcByRegime:
    label: str
    dates: int
    ic: float | None
    p_value: float | None
    median_move_bp: float | None


def reversal_ic_by_vol_tercile(
    panel: Panel, forward: np.ndarray
) -> tuple[list[IcByRegime], list[int]]:
    """Cross-sectional reversal IC per volatility tercile, and the dates used.

    **One IC per date**, which is what makes the tercile p-values
    admissible at all -- pooling name-days here would be the very defect
    `check_clustered_observations` exists to block. The returned date list
    is fed to that check by `main`, so the claim is verified rather than
    asserted.
    """
    sig, vol = trailing_return(panel), trailing_vol(panel)
    moves_bp = np.abs(panel.intraday) * 1e4

    ics: list[float] = []
    vols: list[float] = []
    moves: list[float] = []
    dates: list[int] = []
    for t in range(panel.close_px.shape[0]):
        ok = np.isfinite(sig[t]) & np.isfinite(forward[t]) & np.isfinite(vol[t])
        if ok.sum() < 5:
            continue
        rho = spearman(sig[t][ok].tolist(), forward[t][ok].tolist())
        if rho is None:
            continue
        ics.append(rho)
        vols.append(float(np.mean(vol[t][ok])))
        moves.append(float(np.nanmedian(moves_bp[t][ok])))
        dates.append(panel.dates[t])

    if len(ics) < 9:
        return [], dates
    a, v, mv = np.array(ics), np.array(vols), np.array(moves)
    edges = np.percentile(v, [0, 100 / 3, 200 / 3, 100])
    out: list[IcByRegime] = []
    for i, label in enumerate(VOL_TERCILES):
        sel = (v >= edges[i]) & (v <= edges[i + 1] if i == 2 else v < edges[i + 1])
        if sel.sum() < 3:
            out.append(IcByRegime(label, int(sel.sum()), None, None, None))
            continue
        sample = a[sel]
        mean = float(sample.mean())
        sd = float(sample.std(ddof=1))
        p = None
        if sd > 0:
            t_stat = mean / (sd / math.sqrt(sample.size))
            p = 2 * (1 - NormalDist().cdf(abs(t_stat)))
        out.append(
            IcByRegime(label, int(sel.size and sel.sum()), mean, p, float(np.median(mv[sel])))
        )
    return out, dates


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

    from research.krx_conjunction import forward_total

    moves_bp = np.abs(panel.intraday) * 1e4
    vol = trailing_vol(panel)
    usable = int((np.isfinite(moves_bp) & np.isfinite(vol)).sum())
    print(
        f"panel: {len(panel.dates):,} dates x {len(panel.codes)} names; "
        f"{usable:,} usable name-days\ncost floor: {COST_FLOOR_BP}bp round trip "
        f"(rd-q, futures-liquid names)\n"
    )

    print("=== 1. how large is a day's move against the round trip? ===")
    print(f"  absolute intraday move (open -> close)")
    for q, bp, mult in move_percentiles(moves_bp):
        print(f"    p{q:<3} {bp:8.1f}bp = {mult:6.2f}x cost")

    print("\n=== 2. is there a regime where cost becomes binding? ===")
    print(f"    {'decile':>7} {'n':>7} {'median move':>13} {'x cost':>8}")
    for b, n, med, mult in move_by_vol_bucket(moves_bp, vol):
        print(f"    {b:>7} {n:>7,} {med:>12.1f}bp {mult:>7.2f}x")

    fwd = forward_total(panel, 1)
    rows, dates = reversal_ic_by_vol_tercile(panel, fwd)
    print("\n=== 3. does PREDICTABILITY rise with volatility? ===")
    print(f"    {'regime':>9} {'dates':>7} {'IC':>9} {'p':>7} {'median move':>13}")
    for r in rows:
        ic = f"{r.ic:+.4f}" if r.ic is not None else "-"
        p = f"{r.p_value:.3f}" if r.p_value is not None else "-"
        mv = f"{r.median_move_bp:.0f}bp" if r.median_move_bp is not None else "-"
        print(f"    {r.label:>9} {r.dates:>7,} {ic:>9} {p:>7} {mv:>13}")

    # The tercile p-values are only admissible because the unit is the
    # date. Prove it against the sample actually used rather than saying so.
    findings = [check_clustered_observations(dates, reported_n=len(dates))]
    require_no_blockers(findings)
    print(f"\n  {format_findings(findings)} — the unit of the test is the date, "
          f"{len(dates):,} of them.")
    print(
        "\n  NOT measured here: slippage, spread, borrow, or any size-dependent\n"
        "  execution effect. Moves are printed prices. So a large multiple is\n"
        "  evidence that execution cost is not the binding constraint in this\n"
        "  data — not evidence that execution quality is irrelevant."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
