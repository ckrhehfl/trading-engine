"""RD-B stage 2, corrected: a null that does not partition the series.

**The specification is `.planning/rd-j-stage2-corrected-specification.md`,
committed at `a0ca36c` before any forward return was measured under it.**
This implements it and must not extend it: the family is exactly
**m = 12** and a thirteenth test needs a new specification.

**Why this exists.** `rd-h` ran `stage2_event_study`'s matched-placebo
design and found the one apparently-significant cell was an artifact of
*which bars were eligible as controls* -- and that repairing a second
defect made the artifact **larger**. The complement of "near an up-move"
is "a down-move", so a control drawn by excluding a directional
situation's neighbourhood cannot be unbiased, and the exclusion radius
disjointness demands is exactly what worsens it.

**So the event arm is unchanged and only the null moves.** A circular
block shift keeps every bar in the sample -- there is no complement to be
biased -- while preserving the return series' own autocorrelation,
volatility clustering and unconditional drift, and destroying only the
*alignment* between events and returns, which is the thing under test.

It also answers the dependence objection for free: overlapping forward
windows inflate a t-test's significance but not a permutation p-value,
because the null draws carry the same overlapping structure. That is why
this path uses the **base 240-bar cooldown** rather than
`stage2_event_study`'s horizon-scaled repair, which had cut event counts
by up to two thirds at h=1440.

`stage2_event_study` is deliberately left in place so rd-h's numbers stay
reproducible.

**Discovery mode**, on the designated discovery window. Nothing here may
be promoted, quoted as evidence of an edge, or reported as a pass.

Run:

    python -m research.stage2_shift_null
    python -m research.stage2_shift_null --shift day     # hour-preserving
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.situation_catalogue import DEFAULT_ROUND_TRIP
from research.stage2_event_study import (
    COOLDOWN,
    FAMILY_SIZE,
    FDR_Q,
    HORIZONS,
    USE_BENJAMINI_YEKUTIELI,
    collapse,
    dependence_penalty,
    forward_return,
    hour_of_day,
    load,
    realised_vol_prior,
    situations,
    volatility_decile,
)

#: rd-j §2. Enough that the smallest resolvable p-value, 1/(B+1), sits an
#: order of magnitude under the rank-1 BY threshold of 0.002685 -- a
#: permutation test cannot report a p smaller than that, so B has to be
#: large enough for the decision rule to be reachable at all.
DEFAULT_PERMUTATIONS = 2000
SEED = 20260915

#: rd-j §4. The 12bp measured BTC round trip: an effect real but smaller
#: than the cost of capturing it is a fact about market structure.
EFFECT_FLOOR = DEFAULT_ROUND_TRIP

#: rd-j §2. `"free"` draws any offset; `"day"` restricts to multiples of
#: one trading day so **hour-of-day alignment survives the shift**. rd-b
#: measured a real intraday cycle in crypto, so a free shift smears
#: exactly the structure a situation might be exploiting. Both are run,
#: and **disagreement between them IS the hour-of-day effect**.
SHIFT_MODES = ("free", "day")
BARS_PER_DAY = 1440

#: Every null this arc has built, selectable from one command.
#:
#: **`"matched"` is the one that decided rd-k**, and it was reachable only
#: from a scratch script until review caught that -- a result nobody can
#: reproduce with a repo command is not a reproducible result.
NULL_MODES = SHIFT_MODES + ("matched",)

#: Below this, `1/(B+1)` cannot reach the rank-1 BY threshold of
#: 0.002685, so no test could clear the decision rule however strong it
#: was -- the verdict would be an artefact of the permutation count.
MIN_PERMUTATIONS_FOR_VERDICT = 372


@dataclass(frozen=True)
class ShiftTest:
    situation: str
    horizon: int
    n_events: int
    observed: float
    null_mean: float
    null_sd: float
    unconditional: float
    p_value: float
    permutations: int
    #: Standard deviation of the **individual event forward returns**, so
    #: `event_sd / sqrt(n_events)` is the event arm's own standard error.
    #:
    #: **Purely additive and reported only** -- it enters no p-value, no
    #: effect and no verdict here, so every rd-h and rd-k number is
    #: unchanged (verified by re-running both after adding it). It exists
    #: because `research.event_power` needs the *statistic's* sampling
    #: error, and `null_sd` is the **null's** spread, which is a different
    #: quantity whenever the null is imperfectly calibrated -- on this data
    #: by a factor of 1.05 to 2.45. See `rd-l` §4.1.
    event_sd: float = 0.0
    null_mode: str = "free"
    bh_rank: int = 0
    bh_threshold: float = 0.0
    significant: bool = False

    @property
    def effect(self) -> float:
        return self.observed - self.null_mean

    @property
    def clears_effect_floor(self) -> bool:
        return abs(self.effect) > EFFECT_FLOOR

    @property
    def null_bias(self) -> float:
        """How far the null's centre sits from the unconditional return.

        **`control_suspect`'s replacement.** There is no control arm to
        drift any more, so the equivalent sanity check is that a shift
        null must reproduce the series' own mean: if it does not, the
        shift is not doing what it claims and the "effect" is measuring
        the shift.
        """
        return self.null_mean - self.unconditional

    @property
    def null_suspect(self) -> bool:
        """`|null_bias| >= |effect| / 2`, **for a shift null only**.

        A shift null relabels which return each event collects, so its
        centre must reproduce the series' own mean; a gap means the shift
        is not doing what it claims. That is what caught the seam.

        **It does not apply to the matched null, and applying it there
        would be backwards.** A volatility+hour matched null is *supposed*
        to differ from the unconditional mean — that difference **is** the
        confound being held fixed, and on the real data it reaches
        +18.57bp at h=1440. Vetoing on it would reject a test precisely
        for controlling the thing it was built to control.
        """
        if self.null_mode == "matched":
            return False
        return abs(self.null_bias) >= abs(self.effect) / 2.0

    @property
    def advances(self) -> bool:
        return self.significant and self.clears_effect_floor and not self.null_suspect


def shift_offsets(
    n: int,
    horizon_max: int,
    permutations: int,
    rng: np.random.Generator,
    mode: str = "free",
) -> np.ndarray:
    """Offsets drawn from `[horizon_max, n - horizon_max)`.

    Bounded away from both ends so no draw is a near-identity shift and
    none wraps a forward window across the seam -- rd-j §2.
    """
    if mode not in SHIFT_MODES:
        raise ValueError(f"mode must be one of {SHIFT_MODES}, got {mode!r}")
    lo, hi = horizon_max, n - horizon_max
    if hi <= lo:
        raise ValueError(f"series of {n} bars is too short for a {horizon_max}-bar horizon")
    if mode == "free":
        return rng.integers(lo, hi, size=permutations)
    first = int(np.ceil(lo / BARS_PER_DAY))
    last = int(np.floor((hi - 1) / BARS_PER_DAY))
    if last < first:
        raise ValueError("no whole-day offset fits inside the bounds")
    return rng.integers(first, last + 1, size=permutations) * BARS_PER_DAY


def permutation_test(
    open_px: np.ndarray,
    events: np.ndarray,
    horizon: int,
    offsets: np.ndarray,
) -> tuple[float, np.ndarray]:
    """`(observed, null_draws)` for the mean forward return over `events`.

    The **return series** is shifted, not the events: shifting events
    would move them off their own volatility and hour context, which is
    the confound the matched control was built to handle and got wrong.
    Rolling returns keeps every event where it is and asks what it would
    have collected from a differently-aligned market.

    **Only the LOOKUP POSITION wraps; the exit index never does.** The
    first version computed `exit = (entry + horizon) % n`, so an entry
    near the end of the series produced a return that wrapped to the
    start -- on this series a single **-87.4%** observation, since BTC
    opens at 10,000 and closes near 79,367.

    That contaminated every null draw in proportion to `horizon / n`, and
    the arithmetic matched the symptom exactly: predicted biases of
    -0.04 / -0.14 / -0.57 / -3.44bp at h = 15 / 60 / 240 / 1440 against
    measured -0.03 / -0.16 / -0.72 / -3.78. It was caught because rd-j
    section 6 registered "if a test advances, the first suspicion is the
    null's seam" **before** the run -- and a test advanced.

    Returns are therefore computed only over start positions that have a
    real forward window, and a shift permutes *within* that set.
    """
    n = open_px.size
    m = n - 1 - horizon  # start positions 1..m with a real, unwrapped exit
    if m < 2:
        raise ValueError(f"series of {n} bars is too short for a {horizon}-bar horizon")
    starts = np.arange(1, m + 1)
    ret_valid = (open_px[starts + horizon] - open_px[starts]) / open_px[starts]

    observed = float(forward_return(open_px, events, horizon).mean())
    # An event at index `e` enters at `e+1`, which is position `e` here.
    pos = events
    null = np.empty(offsets.size)
    for i, off in enumerate(offsets):
        null[i] = ret_valid[(pos + int(off)) % m].mean()
    return observed, null


def matched_null(
    open_px: np.ndarray,
    events: np.ndarray,
    horizon: int,
    decile: np.ndarray,
    hour: np.ndarray,
    rng: np.random.Generator,
    permutations: int = DEFAULT_PERMUTATIONS,
) -> tuple[float, np.ndarray, int]:
    """`(observed, null_draws, dropped)` against a volatility+hour matched
    null **with no exclusion**.

    **This is rd-h section 4's named-but-untested third construction, and
    it is what decided rd-k.** It holds the confound the shift null leaves
    open: a circular shift preserves drift, autocorrelation and era, but
    events do not sit in a random volatility stratum -- S3 is in the top
    decile **97.3%** of the time, so a shift null compares the market's
    most volatile hours against all hours.

    Matching on `(decile, hour)` fixes that. Not excluding anything is what
    keeps it from repeating rd-h's failure: with no exclusion there is no
    complement to be biased, which was the whole problem with rd-g's
    design.

    An event whose stratum has no eligible bar is **dropped and counted** --
    drawing from a neighbouring stratum would silently undo the matching
    this exists for.
    """
    n = open_px.size
    idx = np.arange(n)
    eligible = (decile >= 0) & (idx >= 1) & (idx + 1 + horizon < n)
    # An event may not be drawn as its own control -- that puts the
    # observation inside the null and shrinks the very difference being
    # measured.
    #
    # **This is not rd-h's exclusion and the difference is the point.**
    # rd-g removed each event's whole NEIGHBOURHOOD, +/-240 bars around
    # every episode, which is ~15% of the series and whose complement is
    # directionally biased. This removes the 786 event bars themselves:
    # 0.02% of the series, with no neighbourhood and so no complement to
    # be biased. Measured effect of the fix on S3 h=240: the null falls by
    # roughly 0.5bp and the effect rises by the same, which changes no
    # verdict.
    eligible[events] = False
    pools: dict[tuple[int, int], np.ndarray] = {}
    for key in {(int(decile[i]), int(hour[i])) for i in events}:
        d0, h0 = key
        pools[key] = np.where(eligible & (decile == d0) & (hour == h0))[0]

    # An event needs its OWN forward window as well as a non-empty pool.
    # Checking only the pool let an event near the end of the series reach
    # `forward_return` and index past the array -- caught by
    # `test_an_event_with_an_empty_stratum_is_dropped_and_counted`.
    keep = np.array(
        [
            i
            for i in events
            if i + 1 + horizon < n and pools[(int(decile[i]), int(hour[i]))].size > 0
        ],
        dtype=np.int64,
    )
    dropped = int(events.size - keep.size)
    if keep.size < 2:
        raise ValueError(f"only {keep.size} events have a non-empty stratum")

    observed = float(forward_return(open_px, keep, horizon).mean())
    keys = [(int(decile[i]), int(hour[i])) for i in keep]
    null = np.empty(permutations)
    for b in range(permutations):
        draw = np.array([pools[k][rng.integers(pools[k].size)] for k in keys])
        null[b] = forward_return(open_px, draw, horizon).mean()
    return observed, null, dropped


def permutation_p(observed: float, null: np.ndarray) -> float:
    """Two-sided, centred on the null's own mean.

    `(1 + #{|null - mean| >= |observed - mean|}) / (B + 1)`. The `+1`s are
    not cosmetic: a p-value of exactly 0 is not attainable from a finite
    permutation set and reporting one would overstate the evidence.
    """
    centre = null.mean()
    at_least = int(np.sum(np.abs(null - centre) >= abs(observed - centre)))
    return (1.0 + at_least) / (null.size + 1.0)


def benjamini_yekutieli(
    results: list[ShiftTest],
    q: float = FDR_Q,
    yekutieli: bool = USE_BENJAMINI_YEKUTIELI,
) -> list[ShiftTest]:
    """FDR control over the fixed family, `m` from `FAMILY_SIZE`.

    Taking `m` from the constant rather than `len(results)` means a run
    that silently produced fewer tests cannot inflate its own
    significance by shrinking the correction.
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
        out[i] = ShiftTest(
            **{**r.__dict__,
               "bh_rank": rank,
               "bh_threshold": q * rank / (FAMILY_SIZE * penalty),
               "significant": rank <= cutoff}
        )
    return out


def run(
    db_path: str = DEFAULT_DB_PATH,
    mode: str = "free",
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = SEED,
) -> list[ShiftTest]:
    if mode not in NULL_MODES:
        raise ValueError(f"mode must be one of {NULL_MODES}, got {mode!r}")
    if permutations < MIN_PERMUTATIONS_FOR_VERDICT:
        raise ValueError(
            f"B={permutations} cannot reach the rank-1 BY threshold "
            f"{FDR_Q / (FAMILY_SIZE * dependence_penalty()):.6f}: the smallest "
            f"attainable p-value is 1/(B+1)={1/(permutations+1):.6f}. "
            f"A verdict run needs B >= {MIN_PERMUTATIONS_FOR_VERDICT}."
        )
    t, o, h, l, c, v = load(db_path)
    n = c.size
    rng = np.random.default_rng(seed)
    decile = volatility_decile(realised_vol_prior(c))
    hour = hour_of_day(t)
    masks = situations(t, o, h, l, c, v)
    # Base cooldown, not the horizon-scaled one: a permutation test does
    # not need disjoint windows, and rd-g's repair had cut h=1440 event
    # counts by up to two thirds.
    events_by = {name: collapse(m, cooldown=COOLDOWN) for name, m in masks.items()}

    results: list[ShiftTest] = []
    for name, ev_all in events_by.items():
        for hz in HORIZONS:
            ev = ev_all[(ev_all + 1 + hz) < n]
            if ev.size < 2:
                raise ValueError(
                    f"{name} at h={hz} has {ev.size} usable episodes; the "
                    f"specification requires all {FAMILY_SIZE} tests to run"
                )
            if mode == "matched":
                observed, null, dropped = matched_null(
                    o, ev, hz, decile, hour, rng, permutations
                )
                kept = int(ev.size - dropped)
            else:
                offsets = shift_offsets(n, max(HORIZONS) + 1, permutations, rng, mode)
                observed, null = permutation_test(o, ev, hz, offsets)
                kept = int(ev.size)
            uncond = float(forward_return(o, np.arange(1, n - 1 - hz), hz).mean())
            event_sd = float(forward_return(o, ev, hz).std(ddof=1))
            results.append(
                ShiftTest(
                    situation=name,
                    horizon=hz,
                    n_events=kept,
                    observed=observed,
                    null_mean=float(null.mean()),
                    null_sd=float(null.std(ddof=1)),
                    unconditional=uncond,
                    p_value=permutation_p(observed, null),
                    permutations=int(null.size),
                    event_sd=event_sd,
                    null_mode=mode,
                )
            )
    if len(results) != FAMILY_SIZE:
        raise ValueError(f"produced {len(results)} tests, specification fixes {FAMILY_SIZE}")
    return benjamini_yekutieli(results)


_NULL_LABEL = {
    "free": "circular block shift, free offsets",
    "day": "circular block shift, day-multiple offsets (hour-preserving)",
    "matched": "volatility+hour matched, no neighbourhood exclusion",
}


def report(results: list[ShiftTest], mode: str) -> None:
    pen = dependence_penalty()
    print(f"null: {_NULL_LABEL.get(mode, mode)}   B={results[0].permutations if results else 0}")
    if mode == "matched":
        print("(`nullbias` is expected to be non-zero here -- it IS the confound "
              "being held fixed -- so NULL-SUSPECT does not apply)")
    print(f"Benjamini-Yekutieli q={FDR_Q} (penalty {pen:.4f}, rank-1 threshold "
          f"{FDR_Q/(FAMILY_SIZE*pen):.5f})   effect floor {EFFECT_FLOOR*1e4:.0f}bp\n")
    print(f"{'situation':26} {'h':>5} {'events':>7} {'observed':>9} {'null':>8} "
          f"{'uncond':>8} {'nullbias':>9} {'effect':>8} {'p':>9} {'rank':>5} "
          f"{'thresh':>9} {'sig':>4} {'verdict':>14}")
    print("-" * 152)
    for r in sorted(results, key=lambda x: x.p_value):
        if r.advances:
            verdict = "ADVANCE"
        elif r.significant and r.null_suspect:
            verdict = "NULL-SUSPECT"
        elif r.significant:
            verdict = "sig, small"
        else:
            verdict = "-"
        print(f"{r.situation:26} {r.horizon:>5} {r.n_events:>7,} {r.observed*1e4:>9.2f} "
              f"{r.null_mean*1e4:>8.2f} {r.unconditional*1e4:>8.2f} {r.null_bias*1e4:>9.2f} "
              f"{r.effect*1e4:>8.2f} {r.p_value:>9.2e} {r.bh_rank:>5} "
              f"{r.bh_threshold:>9.6f} {'yes' if r.significant else 'no':>4} {verdict:>14}")
    adv = [r for r in results if r.advances]
    print(f"\n{len(adv)} of {len(results)} advance to stage 3.")
    print("Discovery mode: nothing here may be promoted or quoted as evidence of an edge.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--null", dest="shift", choices=NULL_MODES, default="matched",
                    help="'matched' is rd-k's deciding null and the default")
    ap.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS)
    args = ap.parse_args(argv)
    report(run(args.db_path, args.shift, args.permutations), args.shift)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
