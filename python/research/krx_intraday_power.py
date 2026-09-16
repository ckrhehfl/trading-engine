"""What a KRX intraday event study could resolve — before one is specified.

`rd-n` §8 item 1: *"a power calculation from the event arm's own
dispersion, before any family is specified."* `rd-l` §8 item 2 states the
same rule as two separate claims — a statistical one about what a study
can detect, and a **pre-registration policy** about not running a family
that cannot detect a cost-floor-sized effect.

This applies both to the KRX intraday series collected in `rd-o`, and it
**commits nothing**: no situation is defined, no threshold chosen, no
window designated. It measures the instrument, not the market.

## Why the forward return has to be session-aware

BTC 1m runs continuously, so `stage2_event_study.forward_return` indexes
positionally — `open[i + 1 + h]` is `h` minutes after `open[i + 1]`.
**On KRX that is false at three different scales**, measured on the real
series for 005930:

| gap | count | what it is |
|---|---|---|
| 11 minutes | **249** | the closing call auction, 15:20-15:29 — no continuous trade, so no bar |
| 17.5 hours | 186 | overnight |
| 65.5 hours | 46 | a weekend |
| 30-31 minutes | 9 | a late open (수능일, the year's first session) |

A positional forward return would silently span every one of them. On BTC
this project already records gap-blindness as a real exposure affecting
**two** signal positions in 3.6M bars; here it would affect **every
session boundary and every close** — 250 and 249 per symbol.

So `session_forward_return` returns a value only where entry and exit sit
in the same session, and drops the rest. Being explicit is the point: a
KRX event study that reuses the BTC machinery unchanged is wrong, and
wrong in a way that produces plausible numbers.

## What the answer is shaped like

The measured dispersion is **unconditional** — over all bars, not over
any situation's events. `rd-l` §4.1 found event forward returns are
**1.2x to 3.1x** more dispersed than unconditional ones on BTC, because
events are volatility bursts. So an unconditional figure is a **lower
bound on the required event count**, and this module reports the range
rather than the flattering end.

Run:

    python -m research.krx_intraday_power
    python -m research.krx_intraday_power --cost-floor 0.0012   # BTC's, for contrast
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sqlite3
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from data._paths import DEFAULT_DB_PATH
from data.kis_intraday import INTERVAL, KST
from research.event_power import DEFAULT_POWER, alpha_rank1

_NORM = NormalDist()

#: The KR-10 universe (`ms-e`), which is what `rd-o` collected.
KR10 = (
    "005930", "068270", "000660", "009150", "207940",
    "000720", "007390", "028300", "064350", "051910",
)

#: rd-f: the Korean round trip, 30-33bp against BTC's 12. 30 is the
#: today's-rates end of that range and the more favourable one to assume,
#: so it is the default and the harsher 33 is a flag away.
KRX_ROUND_TRIP = 0.0030

#: Horizons in minutes. Stops at 240 because a KRX session is **381 bars**
#: — h=1440 has no meaning inside one, and spanning sessions is what the
#: session-aware return exists to prevent.
HORIZONS = (5, 15, 30, 60, 120, 240)

#: rd-l §4.1's measured range for how much more dispersed an event's
#: forward return is than an unconditional one. Applied as a band, not a
#: point, because it was measured on BTC and no KRX equivalent exists yet.
EVENT_DISPERSION_LOW = 1.2
EVENT_DISPERSION_HIGH = 3.1


@dataclass(frozen=True)
class HorizonPower:
    horizon: int
    observations: int
    sigma: float
    cost_floor: float
    alpha: float
    power: float

    @property
    def effect_in_sigmas(self) -> float:
        """The per-event effect the cost floor demands, in dispersion units.

        **The number that decides whether the question is worth asking.**
        A conditional mean shift of 0.1-0.2σ is the size rd-b's framing
        calls detectable and the microstructure literature reports; 0.75σ
        is not a weak signal, it is a different claim entirely.
        """
        return self.cost_floor / self.sigma if self.sigma > 0 else math.inf

    @property
    def events_unconditional(self) -> float:
        """Events needed if events were as dispersed as ordinary bars.

        A **lower bound**, and stated as one: rd-l §4.1 measured events at
        1.2-3.1x the unconditional dispersion, and `events_band` carries
        that forward.
        """
        z = _NORM.inv_cdf(1.0 - self.alpha / 2.0) + _NORM.inv_cdf(self.power)
        return (z * self.sigma / self.cost_floor) ** 2

    @property
    def events_band(self) -> tuple[float, float]:
        """`(low, high)` once rd-l §4.1's event-dispersion range is applied."""
        n = self.events_unconditional
        return n * EVENT_DISPERSION_LOW**2, n * EVENT_DISPERSION_HIGH**2


def session_labels(open_time_ms: np.ndarray) -> np.ndarray:
    """The KST trading date each bar belongs to.

    KRX trades one continuous session a day, so the date *is* the session.
    Read in KST rather than UTC because a UTC date would split the session
    at 09:00 KST — exactly in the middle of it.
    """
    return np.array(
        [
            dt.datetime.fromtimestamp(ms / 1000, KST).strftime("%Y%m%d")
            for ms in open_time_ms
        ]
    )


def session_forward_return(
    open_px: np.ndarray, sessions: np.ndarray, horizon: int
) -> np.ndarray:
    """Forward returns that never cross a session boundary.

    Entry at the bar **after** the signal, exit `horizon` bars later, both
    at the open — the same convention as `stage2_event_study`. The
    difference is the mask: a pair is kept only when entry and exit carry
    the same session label, so no return spans the closing auction, a
    night, or a weekend.

    Returns only the valid ones; the count is the caller's evidence of how
    much the constraint costs, which at h=240 against a 381-bar session is
    **63% of all bars.**
    """
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1 bar, got {horizon}")
    n = open_px.size
    if n <= horizon + 1:
        return np.array([])
    entry_i = np.arange(1, n - horizon)
    exit_i = entry_i + horizon
    same = sessions[entry_i] == sessions[exit_i]
    entry, exit_ = open_px[entry_i[same]], open_px[exit_i[same]]
    return (exit_ - entry) / entry


def load_symbol(conn: sqlite3.Connection, code: str) -> tuple[np.ndarray, np.ndarray]:
    rows = conn.execute(
        "SELECT open_time_ms, CAST(open AS REAL) FROM klines "
        "WHERE symbol=? AND interval=? ORDER BY open_time_ms",
        (f"KRX:{code}", INTERVAL),
    ).fetchall()
    if not rows:
        raise ValueError(
            f"no {INTERVAL} bars for KRX:{code}. Collect them first: "
            f"`python -m data.backfill_kis_intraday --symbols {code}`"
        )
    return (
        np.array([r[0] for r in rows], dtype=np.int64),
        np.array([r[1] for r in rows], dtype=float),
    )


def measure(
    db_path: str = DEFAULT_DB_PATH,
    codes: tuple[str, ...] = KR10,
    horizons: tuple[int, ...] = HORIZONS,
    cost_floor: float = KRX_ROUND_TRIP,
    power: float = DEFAULT_POWER,
    alpha: float | None = None,
) -> list[HorizonPower]:
    """Pooled unconditional dispersion per horizon, across the universe."""
    if cost_floor <= 0:
        raise ValueError(f"cost_floor must be positive, got {cost_floor}")
    a = alpha_rank1() if alpha is None else alpha
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        series = [(t, o, session_labels(t)) for t, o in
                  (load_symbol(conn, c) for c in codes)]
    finally:
        conn.close()

    out = []
    for h in horizons:
        pooled = [session_forward_return(o, s, h) for _, o, s in series]
        pooled = [r for r in pooled if r.size]
        if not pooled:
            raise ValueError(f"no usable observations at h={h}")
        r = np.concatenate(pooled)
        out.append(
            HorizonPower(
                horizon=h,
                observations=int(r.size),
                sigma=float(r.std(ddof=1)),
                cost_floor=cost_floor,
                alpha=a,
                power=power,
            )
        )
    return out


def report(rows: list[HorizonPower], symbol_sessions: int | None = None) -> None:
    if not rows:
        print("no horizons measured")
        return
    floor, a, p = rows[0].cost_floor, rows[0].alpha, rows[0].power
    print(
        f"KRX intraday, unconditional dispersion   cost floor "
        f"{floor*1e4:.0f}bp   alpha={a:.6f}   power={p:.0%}\n"
    )
    print(
        f"{'h (min)':>8} {'observations':>13} {'sigma':>8} {'effect/sigma':>13} "
        f"{'n (lower bd)':>13} {'n (event 1.2x)':>15} {'n (event 3.1x)':>15}"
    )
    print("-" * 92)
    for r in rows:
        lo, hi = r.events_band
        print(
            f"{r.horizon:>8} {r.observations:>13,} {r.sigma*1e4:>8.1f} "
            f"{r.effect_in_sigmas:>13.2f} {r.events_unconditional:>13,.0f} "
            f"{lo:>15,.0f} {hi:>15,.0f}"
        )
    print(
        "\neffect/sigma : the per-event mean shift the cost floor demands, in "
        "dispersion units.\n               0.1-0.2 is the size rd-b's framing calls "
        "detectable; 0.75 is a different claim."
    )
    print(
        "n            : events to resolve the cost floor. The unconditional column "
        "is a LOWER\n               BOUND -- rd-l §4.1 measured event returns at "
        f"{EVENT_DISPERSION_LOW}-{EVENT_DISPERSION_HIGH}x this dispersion,"
        "\n               and the two right-hand columns carry that forward."
    )
    if symbol_sessions:
        print(f"\navailable: {symbol_sessions:,} symbol-sessions collected.")
        for per in (1.0, 0.5, 0.2, 0.1):
            print(f"  a situation firing {per:>4} per symbol-session -> "
                  f"{int(symbol_sessions*per):,} events")
    print(
        "\nNo situation is defined here and no window is designated. This measures "
        "the\ninstrument, not the market."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--cost-floor", type=float, default=KRX_ROUND_TRIP,
                    help="round trip as a fraction; rd-f puts Korea at 0.0030-0.0033")
    ap.add_argument("--power", type=float, default=DEFAULT_POWER)
    args = ap.parse_args(argv)

    rows = measure(args.db_path, cost_floor=args.cost_floor, power=args.power)
    conn = sqlite3.connect(args.db_path, timeout=60)
    try:
        sessions = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT symbol, open_time_ms/86400000 "
            "FROM klines WHERE interval=? AND symbol LIKE 'KRX:%')",
            (INTERVAL,),
        ).fetchone()[0]
    finally:
        conn.close()
    report(rows, symbol_sessions=int(sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
