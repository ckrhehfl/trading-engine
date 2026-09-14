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

#: The one real gap in this series, from CLAUDE.md's measured retention
#: table: `[2019-09-08T19:00:00Z, 2019-09-08T19:01:00Z)`, one missing bar.
#:
#: Declared rather than tolerated. Every rolling window and every
#: `forward_return` in this module reads a row index as a minute, so an
#: **undeclared** gap silently makes `idx + 1 + h` span more than `h`
#: minutes and distorts both the event definition and the return. The
#: guard fails closed on any gap that is not this one -- the same shape as
#: `run_preregistered_holdout.verify_known_gaps`.
KNOWN_GAPS_MS = ((1567969140000, 120_000),)  # 2019-09-08T18:59:00Z, one missing bar

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

#: Whether to use the dependence-agnostic Benjamini-Yekutieli correction.
#:
#: **rd-g justified BH's positive-dependence condition for the four
#: horizons on one event set, and that is not the whole family.** S1, S2
#: and S3 use different event sets and different control draws, so PRDS
#: across all twelve is asserted rather than shown. BY controls FDR under
#: *arbitrary* dependence, at the cost of a `sum(1/i)` factor -- 3.1032 at
#: m = 12, so the rank-1 threshold falls from 0.00833 to 0.002685.
#:
#: Both are reported. **The conclusion is identical under either**, which
#: is why adopting the stricter one after seeing the result is safe here;
#: a future specification must fix the choice in advance regardless.
USE_BENJAMINI_YEKUTIELI = True

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
    unconditional_mean: float  #: strata-weighted, see `stratified_baseline`
    pool_share: float
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
    def control_bias(self) -> float:
        """How far the control arm sits from the unconditional baseline.

        The single most diagnostic number in this module, and the one that
        no headline statistic exposes. rd-h: a matched placebo produced
        `+15.55bp, t = 2.76` out of nothing, and later `+130.50bp,
        t = 3.51`, purely through which bars were eligible as controls.

        The baseline is **strata-weighted** (`stratified_baseline`), so
        this measures what the *exclusion rule* did rather than what the
        events' volatility and hour composition did.
        """
        return self.control_mean - self.unconditional_mean

    @property
    def control_suspect(self) -> bool:
        """True when the control arm's own deviation from the baseline is
        large enough to account for the difference being claimed.

        **This is a veto, not a warning.** If the control has drifted
        further from unconditional than the size of the effect, the
        "effect" is a statement about the control construction.
        """
        return abs(self.control_bias) >= abs(self.difference) / 2.0

    @property
    def advances(self) -> bool:
        return (
            self.bh_significant
            and self.clears_effect_floor
            and not self.control_suspect
        )


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
    verify_continuity(t)
    return t, o, h, l, c, v


def verify_continuity(
    t_ms: np.ndarray, declared: tuple[tuple[int, int], ...] = KNOWN_GAPS_MS
) -> None:
    """Refuse a series whose gaps are not exactly the declared ones.

    A *missing* declared gap fails too, not only an extra one: if the
    series no longer has the gap this project measured, it is not the
    series these results were computed on, and that is worth stopping for
    rather than absorbing silently.

    `declared` is a parameter so the guard is usable on any series, not
    only this module's.
    """
    if t_ms.size < 2:
        return
    steps = np.diff(t_ms)
    irregular = steps != 60_000
    # The SIZE is declared, not only the position: a 3-minute gap where a
    # 2-minute one is declared means a different number of missing bars,
    # and row indices are read as minutes either way.
    found = tuple(
        (int(a), int(b)) for a, b in zip(t_ms[:-1][irregular], steps[irregular])
    )
    if found != tuple(declared):
        extra = sorted(set(found) - set(declared))
        missing = sorted(set(declared) - set(found))
        raise ValueError(
            f"1m gap set does not match the declaration -- unexpected "
            f"(start, step) {extra}, declared-but-absent {missing}. Row indices "
            f"are read as minutes, so a gap of the wrong size stretches every "
            f"forward window spanning it by the wrong amount"
        )


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


def stratified_baseline(
    open_px: np.ndarray,
    horizon: int,
    decile: np.ndarray,
    hour: np.ndarray,
    events: np.ndarray,
) -> float:
    """Mean forward return of the **whole series**, reweighted to the
    events' own `(decile, hour)` composition.

    **The whole-series mean is the wrong baseline for this comparison.**
    `match_controls` draws inside each event's stratum, so a plain
    unconditional mean charges the *stratum composition* to the control
    construction: events sit in high-volatility, particular-hour bars, and
    those bars have their own mean return whatever the exclusion rule is.

    Reweighting isolates what the exclusion rule actually did, which is
    the quantity `EventTest.control_suspect` vetoes on.
    """
    n = open_px.size
    idx = np.arange(1, n - 1 - horizon)
    if idx.size == 0 or events.size == 0:
        return float("nan")
    r = forward_return(open_px, idx, horizon)
    key = decile[idx].astype(np.int64) * 24 + hour[idx].astype(np.int64)
    ev_key = decile[events].astype(np.int64) * 24 + hour[events].astype(np.int64)

    sums = np.bincount(key[key >= 0], weights=r[key >= 0])
    counts = np.bincount(key[key >= 0])
    total = 0.0
    weight = 0.0
    for k, w in zip(*np.unique(ev_key, return_counts=True)):
        if k < 0 or k >= counts.size or counts[k] == 0:
            continue
        total += w * (sums[k] / counts[k])
        weight += w
    return float(total / weight) if weight else float("nan")


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
        # rd-g section 1 says "broken by >=0.3%"; strict comparisons would
        # drop an event that breaks by exactly that.
        "S1 support penetration": np.isfinite(prior_low) & (l <= prior_low * 0.997),
        "S2 resistance break": np.isfinite(prior_high) & (h >= prior_high * 1.003),
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
    min_spacing: int = 0,
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

    `min_spacing` keeps drawn controls that far apart, so their forward
    windows do not overlap each other either. Without it the *control*
    arm carries the same dependence the event arm was just cleaned of, and
    the t-test's denominator is still wrong.
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
    # A boolean block mask rather than pairwise distances: marking a
    # picked control's neighbourhood is O(min_spacing) per draw, where
    # comparing every candidate against every prior pick is O(|pool| x
    # |chosen|) and on this series that is billions of comparisons.
    blocked = np.zeros(decile.size, dtype=bool)
    for i in events:
        key = (int(decile[i]), int(hour[i]))
        pool = pools.get(key)
        if pool is None or pool.size - len(taken[key]) < k:
            dropped += 1
            continue
        # Draw from what is ACTUALLY still available, rather than
        # rejection-sampling with an attempt budget: a budget can exhaust
        # itself on repeated hits even when k candidates remain, which
        # would drop an event the specification says to keep and make the
        # sample depend on event order.
        available = pool[~blocked[pool]] if min_spacing > 0 else (
            pool[~np.isin(pool, list(taken[key]))] if taken[key] else pool
        )
        if min_spacing > 0 and available.size:
            # Thin to a maximal spacing-valid subset BEFORE sampling.
            # Sampling k at random and then thinning drops the event
            # whenever the draw happens to collide, even though a valid
            # k-subset exists -- which makes the event sample depend on
            # the draw rather than on control availability, and inflates
            # the reported drop count.
            #
            # Any subset of a spacing-valid set is itself spacing-valid,
            # so sampling from it needs no second check.
            srt = np.sort(available)
            keepmask = np.ones(srt.size, bool)
            last = srt[0]
            for j in range(1, srt.size):
                if srt[j] - last < min_spacing:
                    keepmask[j] = False
                else:
                    last = srt[j]
            available = srt[keepmask]
        if available.size < k:
            dropped += 1
            continue
        picked = np.sort(rng.choice(available, size=k, replace=False))
        if min_spacing > 0:
            for x in picked:
                lo = max(0, int(x) - min_spacing + 1)
                blocked[lo : int(x) + min_spacing] = True
        taken[key].update(int(x) for x in picked)
        chosen.extend(int(x) for x in picked)
        kept.append(int(i))
    return (
        np.array(kept, dtype=np.int64),
        np.array(chosen, dtype=np.int64),
        dropped,
    )


def welch(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """`(t, p, df)` for a two-sided Welch test. Welch rather than Student
    because the event arm is by construction the higher-volatility one, so
    equal variances is exactly the assumption that fails here."""
    na, nb = a.size, b.size
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se2 = va / na + vb / nb
    if se2 <= 0:
        # Both samples are constant. Equal constants are genuinely no
        # difference; UNEQUAL constants are the clearest possible
        # difference, and reporting (0, 1) for them inverts the answer.
        return (0.0, 1.0, 1.0) if a.mean() == b.mean() else (
            math.inf if a.mean() > b.mean() else -math.inf, 0.0, 1.0
        )
    t = (a.mean() - b.mean()) / math.sqrt(se2)
    # Welch-Satterthwaite df is fractional and the p-value formula accepts
    # a real df, so truncating it throws away precision for nothing.
    df = max(1.0, se2**2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)))
    return t, _t_distribution_two_sided_p_value(t, df), df


def dependence_penalty(m: int = FAMILY_SIZE, yekutieli: bool = USE_BENJAMINI_YEKUTIELI) -> float:
    """`sum(1/i)` for Benjamini-Yekutieli, `1.0` for plain Benjamini-Hochberg."""
    return sum(1.0 / i for i in range(1, m + 1)) if yekutieli else 1.0


def benjamini_hochberg(
    results: list[EventTest],
    q: float = FDR_Q,
    yekutieli: bool = USE_BENJAMINI_YEKUTIELI,
) -> list[EventTest]:
    """FDR control at level `q` over the fixed family.

    `m` is taken from `FAMILY_SIZE`, not from `len(results)`, so a run that
    silently produced fewer tests cannot inflate its own significance by
    shrinking the correction.

    `yekutieli` divides the thresholds by `sum(1/i)`, which controls FDR
    under arbitrary dependence -- see `USE_BENJAMINI_YEKUTIELI` for why
    the family's dependence structure is not obviously positive.
    """
    penalty = dependence_penalty(FAMILY_SIZE, yekutieli)
    order = sorted(range(len(results)), key=lambda i: results[i].p_value)
    cutoff = 0
    for rank, i in enumerate(order, start=1):
        if results[i].p_value <= q * rank / (FAMILY_SIZE * penalty):
            cutoff = rank
    out = list(results)
    for rank, i in enumerate(order, start=1):
        r = results[i]
        out[i] = EventTest(
            **{**r.__dict__,
               "bh_rank": rank,
               "bh_threshold": q * rank / (FAMILY_SIZE * penalty),
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


#: Whether event (and control) forward windows are forced to be disjoint.
#:
#: **rd-g's specification is internally inconsistent and this is the
#: repair.** It fixes an episode cooldown of 240 bars and horizons up to
#: 1,440, then applies a two-sample t-test -- but a t-test is a statement
#: about *independent* observations, and two events 300 bars apart share
#: 1,140 bars of a 1,440-bar forward window. Measured on the real series:
#: H1 and H2 have **zero** overlapping pairs, H3 has 1-37, and **H4 has
#: 308-531 out of 785-1,196, i.e. 39-44%**.
#:
#: CLAUDE.md already records this exact error shape -- S13's t of 7-8 was
#: really 1.5-2.6 once overlap was removed -- and already records the
#: lesson that *"building the right tool does not protect you if the next
#: analysis does not use it."* `research.conclusion_check
#: .check_disjoint_intervals` is that tool and was not applied.
#:
#: Overlap inflates significance, so the as-specified run erred toward
#: FALSE POSITIVES. Its "zero of twelve" conclusion is therefore unaffected
#: in direction; its H3/H4 p-values are not.
ENFORCE_DISJOINT_WINDOWS = True


def effective_cooldown(horizon: int, enforce: bool = ENFORCE_DISJOINT_WINDOWS) -> int:
    """The episode spacing a horizon actually requires.

    `horizon + 1` because entry is the bar *after* the event, so an event
    at `i` occupies `[i+1, i+1+horizon]`.
    """
    return max(COOLDOWN, horizon + 1) if enforce else COOLDOWN


def run(
    db_path: str = DEFAULT_DB_PATH,
    rule: str = "own",
    enforce_disjoint: bool = ENFORCE_DISJOINT_WINDOWS,
) -> list[EventTest]:
    t, o, h, l, c, v = load(db_path)
    n = c.size
    vol = realised_vol_prior(c)
    decile = volatility_decile(vol)
    hour = hour_of_day(t)
    rng = np.random.default_rng(SEED)

    masks = situations(t, o, h, l, c, v)
    # The exclusion map uses the base cooldown: it answers "is this bar
    # near an event", which does not depend on the horizon being tested.
    all_events = {name: collapse(m) for name, m in masks.items()}

    results: list[EventTest] = []
    for name in masks:
        for hz in HORIZONS:
            cd = effective_cooldown(hz, enforce_disjoint)
            ev_all = collapse(masks[name], cooldown=cd)
            ev = ev_all[(ev_all + 1 + hz) < n]
            # `cd`, not the base cooldown: an event and a control 300 bars
            # apart have overlapping 1,440-bar forward windows, which is
            # the same contract violation the event arm was just fixed for.
            eligible = control_pool(n, hz, decile, all_events, name, rule, cooldown=cd)
            kept, ctrl, dropped = match_controls(
                ev, decile, hour, eligible, rng, min_spacing=cd if enforce_disjoint else 0
            )
            if ctrl.size == 0 or kept.size < 2:
                # rd-g section 4 requires exactly twelve tests, all
                # reported. Silently skipping one would leave BH dividing
                # by a family larger than what was actually run, and the
                # missing cell would appear nowhere.
                raise ValueError(
                    f"{name} at h={hz} produced no testable sample "
                    f"({kept.size} events, {ctrl.size} controls, {dropped} dropped); "
                    f"the specification requires all {FAMILY_SIZE} tests to run"
                )
            a = forward_return(o, kept, hz)
            b = forward_return(o, ctrl, hz)
            tstat, p, _ = welch(a, b)
            uncond = stratified_baseline(o, hz, decile, hour, kept)
            results.append(
                EventTest(
                    situation=name,
                    horizon=hz,
                    n_events=int(kept.size),
                    n_controls=int(ctrl.size),
                    n_dropped=int(dropped),
                    event_mean=float(a.mean()),
                    control_mean=float(b.mean()),
                    unconditional_mean=uncond,
                    pool_share=float(eligible.sum() / n),
                    difference=float(a.mean() - b.mean()),
                    t_statistic=float(tstat),
                    p_value=float(p),
                )
            )
    if len(results) != FAMILY_SIZE:
        raise ValueError(
            f"produced {len(results)} tests, specification fixes {FAMILY_SIZE}"
        )
    return benjamini_hochberg(results)


def report(results: list[EventTest]) -> None:
    print(f"family size (fixed by rd-g): {FAMILY_SIZE}   tests run: {len(results)}")
    name = "Benjamini-Yekutieli" if USE_BENJAMINI_YEKUTIELI else "Benjamini-Hochberg"
    short = "BY" if USE_BENJAMINI_YEKUTIELI else "BH"
    pen = dependence_penalty()
    print(f"{name} q = {FDR_Q} (dependence penalty {pen:.4f}, rank-1 threshold "
          f"{FDR_Q/(FAMILY_SIZE*pen):.5f})   effect floor = {EFFECT_FLOOR*1e4:.0f}bp\n")
    print(f"{'situation':26} {'h':>5} {'events':>7} {'pool%':>6} "
          f"{'event bp':>9} {'ctrl bp':>9} {'base':>8} {'ctrl bias':>10} "
          f"{'diff bp':>9} {'t':>7} {'p':>9} {short:>4} {'verdict':>16}")
    print("-" * 142)
    for r in sorted(results, key=lambda x: x.p_value):
        if r.advances:
            verdict = "ADVANCE"
        elif r.control_suspect and r.bh_significant:
            verdict = "CONTROL-SUSPECT"
        elif r.bh_significant:
            verdict = "sig, small"
        else:
            verdict = "-"
        print(
            f"{r.situation:26} {r.horizon:>5} {r.n_events:>7,} {100*r.pool_share:>5.0f}% "
            f"{r.event_mean*1e4:>9.2f} {r.control_mean*1e4:>9.2f} "
            f"{r.unconditional_mean*1e4:>8.2f} {r.control_bias*1e4:>10.2f} "
            f"{r.difference*1e4:>9.2f} {r.t_statistic:>7.2f} {r.p_value:>9.2e} "
            f"{'yes' if r.bh_significant else 'no':>4} {verdict:>16}"
        )
    adv = [r for r in results if r.advances]
    suspect = [r for r in results if r.control_suspect and r.bh_significant]
    print()
    print(f"{len(adv)} of {len(results)} advance to stage 3.")
    if suspect:
        print(f"{len(suspect)} significant test(s) VETOED: the control arm's own "
              f"deviation from the unconditional baseline is at least half the "
              f"difference claimed, so the 'effect' is a statement about the "
              f"control construction. rd-h section 3.")
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
