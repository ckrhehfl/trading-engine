"""RD-B stage 2: does a situation shift the outcome distribution?

**The specification is fixed in `.planning/rd-g-stage2-specification.md`,
committed before any forward return was measured.** This module implements
it and must not extend it: the family is exactly **m = 12** (three
situations × four horizons), and a thirteenth test needs a new
specification document, not an extra entry here.

Runs in **discovery mode** on the designated discovery window (Binance
futures 1m, already closed to selection). Under CLAUDE.md's Discovery /
Confirmation split nothing produced here may be promoted, quoted as
evidence of an edge, or reported as a pass — the only legitimate output is
a specification for something to be confirmed on a window this has never
touched.

**The matched placebo is the instrument, not a detail.** rd-b measured
events sitting at the 76th percentile of the volatility distribution, so a
comparison against an *unconditional* baseline reads a volatility
difference as an edge. That is this project's single most expensive
recorded error — it produced the false conclusion that minutes-scale
trading is arithmetically impossible.

**Costs are deliberately not applied.** They are a constant subtracted
from both arms and cancel exactly in the difference. Stage 2 tests the
**difference**; stage 1's cost ceiling already tested the **level**
(`situation_catalogue.feasibility`). Applying costs here would
double-count them.

Run:

    python -m research.stage2_event_study
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.eligibility import _t_distribution_two_sided_p_value
from research.situation_catalogue import (
    COOLDOWN,
    DEFAULT_ROUND_TRIP,
    QUANTILE_STEP,
    QUANTILE_WINDOW,
    episodes as _count_episodes,
    roll_extreme,
    roll_sum_prior,
    trailing_quantile,
)

SYMBOL = "BINANCE-FUTURES:BTCUSDT"

#: rd-g §1. Fixed; extending it makes the reported FDR meaningless.
HORIZONS = (15, 60, 240, 1440)
FAMILY_SIZE = 12

#: rd-g §3.
CONTROLS_PER_EVENT = 5
VOLATILITY_DECILES = 10
SEED = 20260914

#: rd-g §4. Benjamini-Hochberg level, and the effect floor a test must
#: clear to be worth a stage 3 -- a difference smaller than the cost of
#: capturing it is a fact about market structure, not a candidate.
FDR_Q = 0.10
EFFECT_FLOOR = DEFAULT_ROUND_TRIP

VOL_WINDOW = 60


@dataclass(frozen=True)
class EventTest:
    situation: str
    horizon: int
    n_events: int
    n_controls: int
    n_dropped: int
    event_mean: float
    control_mean: float
    difference: float
    t_statistic: float
    p_value: float
    bh_rank: int = 0
    bh_threshold: float = 0.0
    bh_significant: bool = False

    @property
    def clears_effect_floor(self) -> bool:
        return abs(self.difference) > EFFECT_FLOOR

    @property
    def advances(self) -> bool:
        return self.bh_significant and self.clears_effect_floor


def load(db_path: str = DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT open_time_ms, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND interval='1m' ORDER BY open_time_ms",
        (SYMBOL,),
    ).fetchall()
    conn.close()
    t = np.array([r[0] for r in rows], dtype=np.int64)
    o, h, l, c, v = (np.array([float(r[i]) for r in rows]) for i in range(1, 6))
    return t, o, h, l, c, v


def realised_vol_prior(close: np.ndarray, w: int = VOL_WINDOW) -> np.ndarray:
    """Std of the `w` one-minute log returns ending at the previous bar."""
    r = np.diff(np.log(close), prepend=np.log(close[0]))
    r[0] = 0.0
    c1, c2 = np.cumsum(r), np.cumsum(r * r)
    n = close.size
    out = np.full(n, np.nan)
    idx = np.arange(n)
    ok = idx >= w + 1
    hi = idx[ok] - 1
    lo = hi - w
    s1, s2 = c1[hi] - c1[lo], c2[hi] - c2[lo]
    out[ok] = np.sqrt(np.maximum((s2 - s1 * s1 / w) / (w - 1), 0.0))
    return out


def volatility_decile(vol: np.ndarray) -> np.ndarray:
    """Prior-only decile label per bar, `-1` where undefined.

    Cut points come from `trailing_quantile`, so a bar's decile is decided
    by bars before it and never by itself or anything after.
    """
    n = vol.size
    label = np.full(n, -1, dtype=np.int8)
    edges = [
        trailing_quantile(vol, q / VOLATILITY_DECILES, QUANTILE_WINDOW, QUANTILE_STEP)
        for q in range(1, VOLATILITY_DECILES)
    ]
    ok = np.isfinite(vol) & np.isfinite(edges[0])
    label[ok] = 0
    for e in edges:
        label[ok & np.isfinite(e) & (vol > e)] += 1
    return label


def hour_of_day(t_ms: np.ndarray) -> np.ndarray:
    return ((t_ms // 3_600_000) % 24).astype(np.int8)


def situations(t, o, h, l, c, v) -> dict[str, np.ndarray]:
    """The three that cleared stage 1's cost ceiling. Definitions frozen by
    rd-g §1 and identical to `situation_catalogue`'s."""
    prior_low = roll_extreme(l, 1440, True)
    prior_high = roll_extreme(h, 1440, False)
    vol60 = roll_sum_prior(v, 60)
    vol60_top1 = trailing_quantile(vol60, 0.99)
    return {
        "S1 support penetration": np.isfinite(prior_low) & (l < prior_low * 0.997),
        "S2 resistance break": np.isfinite(prior_high) & (h > prior_high * 1.003),
        "S3 abnormal activity": (
            np.isfinite(vol60) & np.isfinite(vol60_top1) & (vol60 >= vol60_top1)
        ),
    }


def collapse(mask: np.ndarray, cooldown: int = COOLDOWN) -> np.ndarray:
    """Episode indices: the first bar of each run, one per cooldown."""
    out: list[int] = []
    last = -(10**9)
    for i in np.where(mask)[0]:
        if i - last >= cooldown:
            out.append(int(i))
            last = int(i)
    return np.array(out, dtype=np.int64)


def forward_return(open_px: np.ndarray, idx: np.ndarray, h: int) -> np.ndarray:
    """Entry at the bar AFTER the event, exit `h` bars later, both at the
    open. Task C reported `+45` where real fills gave `-97` because the
    book used the prices the strategy saw when deciding; rd-b §3.3 made
    entering on the next bar a rule."""
    entry = open_px[idx + 1]
    exit_ = open_px[idx + 1 + h]
    return (exit_ - entry) / entry


def match_controls(
    events: np.ndarray,
    decile: np.ndarray,
    hour: np.ndarray,
    eligible: np.ndarray,
    rng: np.random.Generator,
    k: int = CONTROLS_PER_EVENT,
) -> tuple[np.ndarray, np.ndarray, int]:
    """`(kept_events, controls, n_dropped)`.

    `k` controls per event from the same (volatility decile, hour)
    stratum, drawn without replacement.

    An event whose stratum cannot supply `k` eligible controls is
    **dropped and counted** rather than having controls reused -- reuse
    would understate the placebo's variance, which is the denominator of
    the whole test.

    **The kept events are returned explicitly**, not inferred from the
    control count: a dropped event can sit anywhere in the sequence, so
    slicing the event array by `len(controls) // k` silently pairs the
    wrong events with the wrong controls.
    """
    pools: dict[tuple[int, int], np.ndarray] = {}
    where = np.where(eligible)[0]
    for key in {(int(decile[i]), int(hour[i])) for i in events}:
        d, hh = key
        pools[key] = where[(decile[where] == d) & (hour[where] == hh)]

    taken: dict[tuple[int, int], set[int]] = {key: set() for key in pools}
    chosen: list[int] = []
    kept: list[int] = []
    dropped = 0
    for i in events:
        key = (int(decile[i]), int(hour[i]))
        pool = pools.get(key)
        if pool is None or pool.size - len(taken[key]) < k:
            dropped += 1
            continue
        picked: list[int] = []
        # Rejection-sample against what this stratum has already given out,
        # so a control is never counted twice inside one test.
        attempts = 0
        while len(picked) < k and attempts < 50 * k:
            cand = int(pool[rng.integers(pool.size)])
            attempts += 1
            if cand not in taken[key]:
                taken[key].add(cand)
                picked.append(cand)
        if len(picked) < k:
            # Put back what this event took; a partial draw must not
            # deplete the stratum for the events that follow it.
            for c in picked:
                taken[key].discard(c)
            dropped += 1
            continue
        chosen.extend(picked)
        kept.append(int(i))
    return (
        np.array(kept, dtype=np.int64),
        np.array(chosen, dtype=np.int64),
        dropped,
    )


def welch(a: np.ndarray, b: np.ndarray) -> tuple[float, float, int]:
    """`(t, p, df)` for a two-sided Welch test. Welch rather than Student
    because the event arm is by construction the higher-volatility one, so
    equal variances is exactly the assumption that fails here."""
    na, nb = a.size, b.size
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se2 = va / na + vb / nb
    if se2 <= 0:
        return 0.0, 1.0, 1
    t = (a.mean() - b.mean()) / math.sqrt(se2)
    df = se2**2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    dfi = max(1, int(df))
    return t, _t_distribution_two_sided_p_value(t, dfi), dfi


def benjamini_hochberg(results: list[EventTest], q: float = FDR_Q) -> list[EventTest]:
    """BH at level `q` over the fixed family.

    `m` is taken from `FAMILY_SIZE`, not from `len(results)`, so a run that
    silently produced fewer tests cannot inflate its own significance by
    shrinking the correction.
    """
    order = sorted(range(len(results)), key=lambda i: results[i].p_value)
    cutoff = 0
    for rank, i in enumerate(order, start=1):
        if results[i].p_value <= q * rank / FAMILY_SIZE:
            cutoff = rank
    out = list(results)
    for rank, i in enumerate(order, start=1):
        r = results[i]
        out[i] = EventTest(
            **{**r.__dict__,
               "bh_rank": rank,
               "bh_threshold": q * rank / FAMILY_SIZE,
               "bh_significant": rank <= cutoff}
        )
    return out


#: Which episodes are excluded from a test's control pool.
#:
#: **`"own"` is what rd-g section 3 specified and it is biased** -- proven
#: in rd-h section 3. Excluding only the tested situation's own
#: neighbourhood makes the control conditional on the *absence of that
#: situation*, and for a directional situation that is a directional
#: condition: S2 is an upward breakout, so removing its neighbourhood
#: removes the rising periods and drags its control mean from +2.86bp to
#: -13.01bp against an unconditional +2.17bp. That 16bp shift was the
#: entirety of the one "significant" result the specified run produced.
#:
#: `"all"` removes the asymmetry by excluding every catalogued situation
#: from every control pool. It is **less obviously wrong, not proven
#: right** -- it makes the pool conditional on quietness instead.
EXCLUSION_RULES = ("own", "all")


def control_pool(
    n: int,
    horizon: int,
    decile: np.ndarray,
    episodes_by_situation: dict,
    tested: str,
    rule: str = "own",
    cooldown: int = COOLDOWN,
) -> np.ndarray:
    """Bars eligible to be drawn as controls.

    See `EXCLUSION_RULES` for why `rule` is the most consequential
    argument in this module.
    """
    if rule not in EXCLUSION_RULES:
        raise ValueError(f"rule must be one of {EXCLUSION_RULES}, got {rule!r}")
    names = (tested,) if rule == "own" else tuple(episodes_by_situation)
    near = np.zeros(n, dtype=bool)
    for name in names:
        for i in episodes_by_situation[name]:
            near[max(0, i - cooldown) : i + cooldown + 1] = True
    idx = np.arange(n)
    return (~near) & (decile >= 0) & (idx + 1 + horizon < n) & (idx >= 1)


def run(db_path: str = DEFAULT_DB_PATH, rule: str = "own") -> list[EventTest]:
    t, o, h, l, c, v = load(db_path)
    n = c.size
    vol = realised_vol_prior(c)
    decile = volatility_decile(vol)
    hour = hour_of_day(t)
    rng = np.random.default_rng(SEED)

    masks = situations(t, o, h, l, c, v)
    all_events = {name: collapse(m) for name, m in masks.items()}

    results: list[EventTest] = []
    for name in masks:
        ev_all = all_events[name]
        for hz in HORIZONS:
            ev = ev_all[(ev_all + 1 + hz) < n]
            eligible = control_pool(n, hz, decile, all_events, name, rule)
            kept, ctrl, dropped = match_controls(ev, decile, hour, eligible, rng)
            if ctrl.size == 0 or kept.size < 2:
                continue
            a = forward_return(o, kept, hz)
            b = forward_return(o, ctrl, hz)
            tstat, p, _ = welch(a, b)
            results.append(
                EventTest(
                    situation=name,
                    horizon=hz,
                    n_events=int(kept.size),
                    n_controls=int(ctrl.size),
                    n_dropped=int(dropped),
                    event_mean=float(a.mean()),
                    control_mean=float(b.mean()),
                    difference=float(a.mean() - b.mean()),
                    t_statistic=float(tstat),
                    p_value=float(p),
                )
            )
    return benjamini_hochberg(results)


def report(results: list[EventTest]) -> None:
    print(f"family size (fixed by rd-g): {FAMILY_SIZE}   tests run: {len(results)}")
    print(f"BH q = {FDR_Q}   effect floor = {EFFECT_FLOOR*1e4:.0f}bp\n")
    print(f"{'situation':26} {'h':>5} {'events':>7} {'ctrl':>7} {'drop':>5} "
          f"{'event bp':>9} {'ctrl bp':>9} {'diff bp':>9} {'t':>7} {'p':>9} {'BH':>4} {'verdict':>10}")
    print("-" * 128)
    for r in sorted(results, key=lambda x: x.p_value):
        verdict = "ADVANCE" if r.advances else ("sig, small" if r.bh_significant else "-")
        print(
            f"{r.situation:26} {r.horizon:>5} {r.n_events:>7,} {r.n_controls:>7,} "
            f"{r.n_dropped:>5} {r.event_mean*1e4:>9.2f} {r.control_mean*1e4:>9.2f} "
            f"{r.difference*1e4:>9.2f} {r.t_statistic:>7.2f} {r.p_value:>9.2e} "
            f"{'yes' if r.bh_significant else 'no':>4} {verdict:>10}"
        )
    adv = [r for r in results if r.advances]
    print()
    print(f"{len(adv)} of {len(results)} advance to stage 3.")
    print("Discovery mode: nothing here may be promoted or quoted as evidence of an edge.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument(
        "--exclusion",
        choices=EXCLUSION_RULES,
        default="own",
        help="control-pool exclusion. 'own' is what rd-g specified and rd-h "
             "proved biased; 'all' is the diagnostic comparison, not a result",
    )
    args = ap.parse_args(argv)
    results = run(args.db_path, rule=args.exclusion)
    if args.exclusion != "own":
        print("*** exclusion rule is a DIAGNOSTIC, not a stage-2 result -- "
              "rd-g section 4 forbids re-running the family with adjusted "
              "definitions and calling the output a result ***\n")
    report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
