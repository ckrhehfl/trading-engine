"""RD-B stage 3: what observable at decision time splits the branches.

**The specification is `.planning/rd-m-stage3-separator-specification.md`,
committed before any separator's forward return was measured.** This
implements it and must not extend it: the family is exactly **m = 18** and
a nineteenth test needs a new specification.

**Why a separator test rather than a fourth null.** rd-b's 개미털기 story
says a support penetration resolves into one of two opposite outcomes, and
*the mean is uninformative precisely because it mixes them*. A situation
with a mean of zero and two large opposite branches is indistinguishable,
under a mean test, from a situation with nothing in it.

**And the instrument is genuinely better, not merely different.** The
events are held fixed and only the branch **labels** are permuted, so
every confound that affects a situation's events equally cancels in the
difference -- the whole class that took this arc three nulls to handle:

    rd-h  directional exclusion bias   -> there is no control pool
    rd-j  calendar-era drift           -> both branches share the eras
    rd-k  the volatility stratum       -> both branches share the mix
    rd-k  the circular seam            -> nothing is shifted

What survives is a confound **correlated with the separator itself**, and
`split_confounded` is the guard for exactly that -- with its threshold
taken from the same permutation rather than from a chosen constant, which
is the one thing rd-j's `null_suspect` got wrong badly enough that rd-k
had to amend the specification.

**Discovery mode**, on the designated discovery window. Nothing here may
be promoted, quoted as evidence of an edge, or reported as a pass.

Run:

    python -m research.stage3_separator
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.situation_catalogue import (
    DEFAULT_ROUND_TRIP,
    roll_extreme,
    roll_sum_prior,
    trailing_quantile,
)
from research.stage2_event_study import (
    COOLDOWN,
    FDR_Q,
    SYMBOL,
    USE_BENJAMINI_YEKUTIELI,
    VOL_WINDOW,
    collapse,
    dependence_penalty,
    forward_return,
    hour_of_day,
    realised_vol_prior,
    situations,
    verify_continuity,
    volatility_decile,
)
from research.stage2_shift_null import permutation_p

#: rd-m §3. Fixed; extending either dimension makes the reported FDR
#: meaningless.
HORIZONS = (15, 60)
SEPARATORS = ("X1 taker-buy share", "X2 extremity", "X3 prior trend")
FAMILY_SIZE = 18

DEFAULT_PERMUTATIONS = 2000
SEED = 20260915

#: rd-m §3. `1/(B+1)` must reach the m=18 rank-1 BY threshold of
#: 0.001590, or no test could clear the decision rule however strong it
#: was and the verdict would be an artefact of the permutation count.
MIN_PERMUTATIONS_FOR_VERDICT = 629

#: rd-m §6 clause 2. The floor is on the **branch**, not the difference:
#: a strategy trades one branch and pays one round trip to be in it, so a
#: real separation between two sub-cost branches is a fact about market
#: structure rather than a candidate.
EFFECT_FLOOR = DEFAULT_ROUND_TRIP

#: rd-m §5. The composition guard's threshold is this quantile **of the
#: null TVD produced by the same permutation**, not a constant.
TVD_QUANTILE = 0.99

VOLATILITY_DECILES = 10
HOURS = 24

#: rd-m §4, X3: the prior 4 hours ending at the event bar's close.
TREND_LOOKBACK = 240

#: rd-m §4, X2 for S3: the quantile S3's own trigger is measured against,
#: so "extremity" is how far past its own threshold the event fired.
S3_VOLUME_QUANTILE = 0.99
S3_VOLUME_WINDOW = 60


@dataclass(frozen=True)
class SeparatorTest:
    situation: str
    horizon: int
    separator: str
    n_high: int
    n_low: int
    n_dropped: int
    mean_high: float
    mean_low: float
    null_sd: float
    p_value: float
    permutations: int
    tvd_decile: float
    tvd_decile_null: float
    tvd_hour: float
    tvd_hour_null: float
    bh_rank: int = 0
    bh_threshold: float = 0.0
    significant: bool = False

    @property
    def difference(self) -> float:
        return self.mean_high - self.mean_low

    @property
    def best_branch(self) -> float:
        """The larger branch mean in magnitude, signed.

        What clause 2 is measured on. `max(abs(...))` alone would throw
        away the direction, and a -20bp branch is as tradeable as a +20bp
        one -- the family is two-sided by construction.
        """
        return self.mean_high if abs(self.mean_high) >= abs(self.mean_low) else self.mean_low

    @property
    def clears_effect_floor(self) -> bool:
        return abs(self.best_branch) > EFFECT_FLOOR

    @property
    def split_confounded(self) -> bool:
        """Either branch-composition TVD above its own permutation null.

        **The threshold is not a chosen constant.** The same label
        permutation that produces the null difference produces a null TVD,
        so "more different than random labelling of these same events
        would produce" is measured rather than asserted. rd-j's
        `null_suspect` needed a magic fraction, the specification and the
        implementation read it differently, and rd-k had to amend the
        registration -- this has nothing to disagree about.
        """
        return (
            self.tvd_decile > self.tvd_decile_null
            or self.tvd_hour > self.tvd_hour_null
        )

    @property
    def advances(self) -> bool:
        return (
            self.significant and self.clears_effect_floor and not self.split_confounded
        )


def load(db_path: str = DEFAULT_DB_PATH):
    """The stage 2 series plus `taker_buy_base_volume`.

    **Every OHLCV column is `CAST` to REAL in SQL rather than compared in
    it.** `klines` stores them as TEXT for exact decimal round-trip, so a
    bare `WHERE taker_buy_base_volume > volume` is a *lexicographic*
    comparison: it reports 1,186,178 violations on this series because
    `'9.005' > '13.102'` as a string, where the numeric comparison reports
    the zero CLAUDE.md records. No read path in this repo makes that
    mistake; an ad-hoc query will, and it looks entirely plausible when it
    does.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT open_time_ms, CAST(open AS REAL), CAST(high AS REAL), "
            "CAST(low AS REAL), CAST(close AS REAL), CAST(volume AS REAL), "
            "CAST(taker_buy_base_volume AS REAL) FROM klines "
            "WHERE symbol=? AND interval='1m' ORDER BY open_time_ms",
            (SYMBOL,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError(f"no 1m rows for {SYMBOL!r} in {db_path}")
    t = np.array([r[0] for r in rows], dtype=np.int64)
    o, h, l, c, v = (np.array([r[i] for r in rows], dtype=float) for i in range(1, 6))
    tb = np.array(
        [np.nan if r[6] is None else r[6] for r in rows], dtype=float
    )
    verify_continuity(t)
    return t, o, h, l, c, v, tb


def taker_buy_share(volume: np.ndarray, taker_buy: np.ndarray) -> np.ndarray:
    """`taker_buy / volume`, `NaN` on a zero-volume bar.

    526 bars of this series have no trade at all, so the share is
    genuinely undefined there. It is left `NaN` and the event is dropped
    and counted downstream -- imputing 0.5 would put a manufactured
    "balanced" observation into whichever branch the median put it in.
    """
    out = np.full(volume.size, np.nan)
    ok = volume > 0
    out[ok] = taker_buy[ok] / volume[ok]
    return out


def extremity(
    name: str, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray
) -> np.ndarray:
    """rd-m §4, X2 -- how far past its own trigger the event fired.

    Per situation, because **S3 has no level** for a depth to be measured
    against: it fires on volume, so its extremity is how far past the
    volume threshold it went. S1 and S2 normalise their penetration depth
    by prior realised volatility, which is what keeps the separator from
    being a volatility measurement in disguise -- and `split_confounded`
    checks whether that succeeded rather than assuming it.
    """
    if name.startswith("S1"):
        prior_low = roll_extreme(low, 1440, True)
        depth = (prior_low - low) / prior_low
    elif name.startswith("S2"):
        prior_high = roll_extreme(high, 1440, False)
        depth = (high - prior_high) / prior_high
    elif name.startswith("S3"):
        vol = roll_sum_prior(volume, S3_VOLUME_WINDOW)
        thresh = trailing_quantile(vol, S3_VOLUME_QUANTILE)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(thresh > 0, vol / thresh, np.nan)
        return out
    else:
        raise ValueError(f"no registered extremity for situation {name!r}")
    vol = realised_vol_prior(close, VOL_WINDOW)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(vol > 0, depth / vol, np.nan)


def prior_trend(close: np.ndarray, lookback: int = TREND_LOOKBACK) -> np.ndarray:
    """rd-m §4, X3 -- the return over the prior `lookback` bars, ending at
    **this bar's close**, so it is known when the event is identified."""
    out = np.full(close.size, np.nan)
    if close.size > lookback:
        past = close[:-lookback]
        out[lookback:] = np.where(
            past > 0, (close[lookback:] - past) / past, np.nan
        )
    return out


def separator_values(
    name: str,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    taker_buy: np.ndarray,
) -> dict[str, np.ndarray]:
    """The three registered separators for one situation, per bar."""
    return {
        SEPARATORS[0]: taker_buy_share(volume, taker_buy),
        SEPARATORS[1]: extremity(name, high, low, close, volume),
        SEPARATORS[2]: prior_trend(close),
    }


def median_split(x: np.ndarray, events: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """`(kept_events, is_high, n_dropped)` at the events' own median.

    **An in-sample threshold, deliberately** (rd-m §4): a trading rule
    could not use it, and stage 4 must re-specify it as a trailing
    quantile. It is the *optimistic* case, which is what makes a negative
    result here conclusive and a positive one merely a reason to re-test.

    Events whose separator is undefined are **dropped and counted**, never
    imputed -- and the median is taken over the survivors, so a dropped
    event does not shift the threshold for the ones that remain.
    """
    vals = x[events]
    ok = np.isfinite(vals)
    kept = events[ok]
    dropped = int(events.size - kept.size)
    if kept.size < 4:
        raise ValueError(f"only {kept.size} events have a defined separator")
    med = float(np.median(x[kept]))
    return kept, x[kept] >= med, dropped


def total_variation(a: np.ndarray, b: np.ndarray, bins: int) -> float:
    """TVD between two label samples' discrete distributions.

    `0.5 * sum |p_i - q_i|`, so it is 0 for identical composition and 1
    for disjoint -- a single number for "how differently are these two
    branches composed", usable unchanged on both 10 volatility deciles and
    24 hours.
    """
    if a.size == 0 or b.size == 0:
        return 1.0
    pa = np.bincount(a, minlength=bins)[:bins] / a.size
    pb = np.bincount(b, minlength=bins)[:bins] / b.size
    return float(0.5 * np.abs(pa - pb).sum())


def separator_test(
    open_px: np.ndarray,
    events: np.ndarray,
    horizon: int,
    x: np.ndarray,
    decile: np.ndarray,
    hour: np.ndarray,
    situation: str,
    separator: str,
    rng: np.random.Generator,
    permutations: int = DEFAULT_PERMUTATIONS,
) -> SeparatorTest:
    """One cell of the family: split, difference, label-permutation null.

    The null **permutes the labels among the same events**, holding the
    two branch sizes fixed. That is exact under "the separator carries no
    information": if it does not, every assignment of these labels to
    these events is equally likely, and no assumption about the return
    distribution is needed anywhere.
    """
    n = open_px.size
    usable = events[(events + 1 + horizon) < n]
    kept, is_high, dropped = median_split(x, usable)
    dropped += int(events.size - usable.size)
    n_high = int(is_high.sum())
    n_low = int(kept.size - n_high)
    if n_high < 2 or n_low < 2:
        raise ValueError(
            f"{situation} h={horizon} {separator}: split is {n_high}/{n_low}; "
            f"a median split that degenerates means the separator is near-constant"
        )

    r = forward_return(open_px, kept, horizon)
    d_ev = decile[kept].astype(np.int64)
    h_ev = hour[kept].astype(np.int64)
    # A stratum label of -1 (undefined volatility) would index from the
    # end of `bincount`'s output. Shift into a non-negative range instead
    # of dropping the event, since an undefined decile is a fact about the
    # composition guard's input, not about the event.
    d_ev = np.where(d_ev < 0, VOLATILITY_DECILES, d_ev)

    observed = float(r[is_high].mean() - r[~is_high].mean())
    null = np.empty(permutations)
    tvd_d = np.empty(permutations)
    tvd_h = np.empty(permutations)
    k = kept.size
    for b in range(permutations):
        perm = rng.permutation(k)
        hi, lo = perm[:n_high], perm[n_high:]
        null[b] = r[hi].mean() - r[lo].mean()
        tvd_d[b] = total_variation(d_ev[hi], d_ev[lo], VOLATILITY_DECILES + 1)
        tvd_h[b] = total_variation(h_ev[hi], h_ev[lo], HOURS)

    return SeparatorTest(
        situation=situation,
        horizon=horizon,
        separator=separator,
        n_high=n_high,
        n_low=n_low,
        n_dropped=dropped,
        mean_high=float(r[is_high].mean()),
        mean_low=float(r[~is_high].mean()),
        null_sd=float(null.std(ddof=1)),
        p_value=permutation_p(observed, null),
        permutations=int(null.size),
        tvd_decile=total_variation(
            d_ev[is_high], d_ev[~is_high], VOLATILITY_DECILES + 1
        ),
        tvd_decile_null=float(np.quantile(tvd_d, TVD_QUANTILE)),
        tvd_hour=total_variation(h_ev[is_high], h_ev[~is_high], HOURS),
        tvd_hour_null=float(np.quantile(tvd_h, TVD_QUANTILE)),
    )


def benjamini_yekutieli(
    results: list[SeparatorTest],
    q: float = FDR_Q,
    yekutieli: bool = USE_BENJAMINI_YEKUTIELI,
) -> list[SeparatorTest]:
    """FDR over the fixed family, `m` from `FAMILY_SIZE`.

    Taking `m` from the constant rather than `len(results)` means a run
    that silently produced fewer tests cannot inflate its own significance
    by shrinking its own correction.
    """
    penalty = dependence_penalty(FAMILY_SIZE, yekutieli)
    order = sorted(range(len(results)), key=lambda i: results[i].p_value)
    cutoff = 0
    for rank, i in enumerate(order, start=1):
        if results[i].p_value <= q * rank / (FAMILY_SIZE * penalty):
            cutoff = rank
    out = list(results)
    for rank, i in enumerate(order, start=1):
        out[i] = SeparatorTest(
            **{
                **results[i].__dict__,
                "bh_rank": rank,
                "bh_threshold": q * rank / (FAMILY_SIZE * penalty),
                "significant": rank <= cutoff,
            }
        )
    return out


def run(
    db_path: str = DEFAULT_DB_PATH,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = SEED,
) -> list[SeparatorTest]:
    if permutations < MIN_PERMUTATIONS_FOR_VERDICT:
        raise ValueError(
            f"B={permutations} cannot reach the rank-1 BY threshold "
            f"{FDR_Q / (FAMILY_SIZE * dependence_penalty(FAMILY_SIZE)):.6f}: the "
            f"smallest attainable p-value is 1/(B+1)={1/(permutations+1):.6f}. "
            f"A verdict run needs B >= {MIN_PERMUTATIONS_FOR_VERDICT}."
        )
    t, o, h, l, c, v, tb = load(db_path)
    rng = np.random.default_rng(seed)
    decile = volatility_decile(realised_vol_prior(c))
    hour = hour_of_day(t)
    masks = situations(t, o, h, l, c, v)

    results: list[SeparatorTest] = []
    for name, mask in masks.items():
        ev = collapse(mask, cooldown=COOLDOWN)
        xs = separator_values(name, h, l, c, v, tb)
        for hz in HORIZONS:
            for sep in SEPARATORS:
                results.append(
                    separator_test(
                        o, ev, hz, xs[sep], decile, hour, name, sep, rng, permutations
                    )
                )
    if len(results) != FAMILY_SIZE:
        raise ValueError(
            f"produced {len(results)} tests, specification fixes {FAMILY_SIZE}"
        )
    return benjamini_yekutieli(results)


def verdict(r: SeparatorTest) -> str:
    if r.advances:
        return "ADVANCE"
    if r.significant and r.split_confounded:
        return "SPLIT-CONFOUNDED"
    if r.significant:
        return "sig, small"
    return "-"


def report(results: list[SeparatorTest]) -> None:
    pen = dependence_penalty(FAMILY_SIZE)
    print(
        f"null: label permutation within each situation's own events   "
        f"B={results[0].permutations if results else 0}"
    )
    print(
        f"Benjamini-Yekutieli q={FDR_Q} (penalty {pen:.4f}, rank-1 threshold "
        f"{FDR_Q/(FAMILY_SIZE*pen):.6f})   branch floor "
        f"{EFFECT_FLOOR*1e4:.0f}bp\n"
    )
    print(
        f"{'situation':26} {'h':>4} {'separator':20} {'n hi/lo':>13} {'drop':>5} "
        f"{'hi':>8} {'lo':>8} {'diff':>8} {'sd':>7} {'p':>9} {'rank':>5} "
        f"{'TVDvol':>7} {'(99%)':>7} {'TVDhr':>7} {'(99%)':>7} {'verdict':>17}"
    )
    print("-" * 185)
    for r in sorted(results, key=lambda x: x.p_value):
        print(
            f"{r.situation:26} {r.horizon:>4} {r.separator:20} "
            f"{f'{r.n_high:,}/{r.n_low:,}':>13} {r.n_dropped:>5} "
            f"{r.mean_high*1e4:>8.2f} {r.mean_low*1e4:>8.2f} "
            f"{r.difference*1e4:>8.2f} {r.null_sd*1e4:>7.2f} {r.p_value:>9.2e} "
            f"{r.bh_rank:>5} {r.tvd_decile:>7.3f} {r.tvd_decile_null:>7.3f} "
            f"{r.tvd_hour:>7.3f} {r.tvd_hour_null:>7.3f} {verdict(r):>17}"
        )
    adv = [r for r in results if r.advances]
    print(f"\n{len(adv)} of {len(results)} advance to stage 4.")
    print(
        "Discovery mode: nothing here may be promoted or quoted as evidence of an edge."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    report(run(args.db_path, args.permutations, args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
