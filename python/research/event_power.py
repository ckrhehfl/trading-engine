"""How many events would settle a stage-2 result — rd-k section 6, item 2.

[`rd-k`](../../.planning/rd-k-stage2-corrected-result.md) ended **0 of
12**, with four p-values clustered at 0.064-0.085 and the text *"that is
what an underpowered weak signal looks like. It is also what noise looks
like, and nothing here separates the two."*

**This separates them in the only way arithmetic can**: it does not say
which one is true, it says **what it would cost to find out.** rd-k §6
item 2 is explicit that this is cheaper to compute than another null is
to run, and it is the calculation that should precede any further mean
test rather than follow one.

## The instrument, and the correction that took two passes

The question is about the sampling variability of **the statistic that was
observed** -- the event-arm mean -- so the standard error is the event
arm's own, `sigma_event / sqrt(n)`:

    n_required = n_observed * ( (z[1-alpha/2] + z[power]) * se / effect )^2

**The first version of this module used `null_sd` instead, and that was
wrong.** The reasoning was that a permutation null's spread *is* the
sampling distribution of its statistic, which is true only when the null
is calibrated to the arm it is judging. rd-k's matched null is not: it
matches on **prior** volatility, and an event that is itself a volatility
burst has a more dispersed **forward** return than any bar sharing its
prior-volatility decile. Measured on this data, the null runs **1.05x to
2.45x narrower** than the event arm, worst at the short horizons where the
burst has not yet decayed.

That mattered twice over. Required-event counts scale as `se^2`, so they
were understated by up to **6x**; and every interval was narrowed by the
same ratio, which moved real verdicts.

**`null_sd` is still carried, now as the diagnostic it should always have
been** -- `null_calibration = null_sd / se`, where 1.0 is a correct null
and below 1.0 means the reference distribution is narrower than the
statistic it judges, so **the permutation p-value is too small.** For a
0-of-12 result that is the safe direction; it would not have been for
anything that advanced.

**rd-m's registered prediction 4 is what caught this** -- *"the realised
`se_diff` will be within 20% of 2 x stage 2's `se_full`"* -- and it came
back at 2.5x. That is the third defect a registered prediction has found
in this arc.

with `alpha` the **Benjamini-Yekutieli rank-1 threshold, 0.002685**, not
0.05 -- that is the bar rd-j's decision rule actually sets, and quoting a
nominal 0.05 requirement here would understate the real cost by roughly
2x. Both are printed so the price of the multiplicity correction is
visible rather than argued about.

## Three targets, because "the effect" is three different numbers

| powered against | why it is the honest one for some purpose |
|---|---|
| the **observed** effect | optimistic by construction -- an effect first noticed at p ~ 0.06 is the high draw of its own sampling distribution (winner's curse), so this is a floor on the cost, never an estimate of it |
| the **cost floor**, 12bp | the decision-relevant one. An effect under the round trip is a fact about market structure, not a candidate, so powering the study for exactly the floor asks "when would a *tradeable* effect become visible?" |
| the **CI lower bound** | the conservative one. If the true effect sits at the bottom of what this run cannot exclude, this is the cost. Undefined when the interval spans zero, and that is reported rather than papered over |

The interval itself is taken at the **same alpha as the decision rule**,
not a habitual 95%. Mixing the two put a contradiction in this table's
first version -- S2 at h=240 read "tradeable effect excluded" from a 95%
interval while its own `n@12bp` said the study was not powered to see
12bp at all. Both figures were right at their own confidence level, which
is exactly why only one may appear.

## The two things this cannot do, stated so the numbers are not over-read

1. **It assumes future events resemble these.** `n` scales as `1/se^2`
   only if the added events carry the same dispersion and the same
   stratum composition. More years of BTC means a different era, and
   rd-k §2.2 already showed S1 and S2 are clustered in 2021-22 -- a
   longer window does not deliver more of the *same* thing.
2. **It is a normal approximation to a permutation tail.** `normal_p` is
   printed beside the real permutation p for exactly this reason: where
   the two disagree, the power figure beside them is the less trustworthy
   number. A permutation p also cannot go below `1/(B+1)`, so a very
   strong test's two p-values will diverge at that floor for a reason
   that is not a failure of the approximation.

**Discovery mode.** Nothing here may be promoted, quoted as evidence of
an edge, or reported as a pass.

Run:

    python -m research.event_power                 # the matched null, rd-k's decider
    python -m research.event_power --null free     # rd-k section 2's shift null
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from statistics import NormalDist

from data._paths import DEFAULT_DB_PATH
from research.situation_catalogue import DEFAULT_ROUND_TRIP
from research.stage2_event_study import (
    FAMILY_SIZE,
    FDR_Q,
    SYMBOL,
    dependence_penalty,
)
from research.stage2_shift_null import NULL_MODES, ShiftTest
from research.stage2_shift_null import run as run_stage2

_NORM = NormalDist()

#: Conventional, and named rather than buried: 80% is the level below
#: which a study is usually called underpowered, not a property of this
#: data.
DEFAULT_POWER = 0.80

#: What a single test would be judged at with no family around it. Kept
#: only to show what the correction costs.
ALPHA_NOMINAL = 0.05

MS_PER_YEAR = 365.25 * 24 * 60 * 60 * 1000


def alpha_rank1(
    q: float = FDR_Q, m: int = FAMILY_SIZE, penalty: float | None = None
) -> float:
    """The BY rank-1 threshold, `q / (m * penalty)` -- 0.002685 at m=12.

    The **rank-1** threshold specifically, because that is what the
    smallest p-value in the family has to clear. A test powered against
    the rank-12 threshold would be powered to be significant only if
    eleven other tests already were.
    """
    return q / (m * (dependence_penalty(m) if penalty is None else penalty))


def normal_p(effect: float, se: float) -> float:
    """Two-sided normal tail for `effect / se`, for comparison only."""
    if se <= 0:
        raise ValueError(f"se must be positive, got {se}")
    return 2.0 * _NORM.cdf(-abs(effect / se))


def effect_interval(
    effect: float, se: float, level: float = 0.95
) -> tuple[float, float]:
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be in (0, 1), got {level}")
    z = _NORM.inv_cdf(1.0 - (1.0 - level) / 2.0)
    return effect - z * se, effect + z * se


def _z_sum(alpha: float, power: float) -> float:
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must be in (0, 1), got {power}")
    return _NORM.inv_cdf(1.0 - alpha / 2.0) + _NORM.inv_cdf(power)


def detectable_effect(
    se: float, alpha: float, power: float = DEFAULT_POWER
) -> float:
    """The smallest effect this many events could detect, at this alpha.

    The counterpart of `required_events`, and the more useful direction
    when the answer to "how many more events" is a number nobody can
    reach: it says what the study that already ran was capable of seeing.
    """
    if se <= 0:
        raise ValueError(f"se must be positive, got {se}")
    return _z_sum(alpha, power) * se


def required_events(
    effect: float,
    se: float,
    n_events: int,
    alpha: float,
    power: float = DEFAULT_POWER,
) -> float:
    """Events needed to detect `effect` at `alpha` two-sided with `power`.

    `se` is the observed standard error of the statistic **at
    `n_events`** -- `sigma_event / sqrt(n)` for an event-study mean.

    **Not the null distribution's standard deviation**, which is a
    different quantity whenever the null is imperfectly calibrated: see
    this module's own docstring for the 1.05-2.45x gap that made the
    distinction load-bearing rather than pedantic.

    Returns `inf` for a zero effect, which is the true answer and a more
    useful one than an exception: no sample size establishes a null.
    """
    if se <= 0:
        raise ValueError(f"se must be positive, got {se}")
    if n_events <= 0:
        raise ValueError(f"n_events must be positive, got {n_events}")
    if effect == 0.0:
        return math.inf
    return n_events * (_z_sum(alpha, power) * se / effect) ** 2


def window_years(db_path: str = DEFAULT_DB_PATH, symbol: str = SYMBOL) -> float:
    """Calendar span of the 1m series, measured rather than hardcoded.

    An event rate is events per *year*, so a hardcoded 6.96 would go
    quietly stale the moment the series is extended -- and the whole point
    of this module is to turn an event count into a calendar cost.
    """
    conn = sqlite3.connect(db_path)
    try:
        lo, hi = conn.execute(
            "SELECT MIN(open_time_ms), MAX(open_time_ms) FROM klines "
            "WHERE symbol=? AND interval='1m'",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    if lo is None or hi is None:
        raise ValueError(f"no 1m rows for {symbol!r} in {db_path}")
    years = (hi - lo) / MS_PER_YEAR
    if years <= 0:
        raise ValueError(f"1m series for {symbol!r} spans {years} years")
    return years


@dataclass(frozen=True)
class PowerRow:
    situation: str
    horizon: int
    n_events: int
    effect: float
    se: float
    null_sd: float
    permutation_p: float
    normal_p: float
    ci_low: float
    ci_high: float
    events_per_year: float
    alpha: float
    power: float
    cost_floor: float
    detectable: float
    n_for_observed: float
    n_for_floor: float
    n_for_ci_low: float

    def years_for(self, n: float) -> float:
        return math.inf if math.isinf(n) else n / self.events_per_year

    @property
    def null_calibration(self) -> float:
        """`null_sd / se` — how well the null's spread matches the event
        arm's own sampling error.

        **1.0 is a correctly calibrated null.** Below 1.0 the reference
        distribution is narrower than the statistic it is judging, so an
        observed deviation looks more extreme than it is and **the
        permutation p-value is too small**. That is the safe direction for
        a negative result — a 0-of-12 reported against an over-significant
        null is more robust, not less — and the unsafe one for anything
        that had advanced.

        Reported for every test on the standing rule that a guard's value
        is visible only when its number is.
        """
        return self.null_sd / self.se if self.se > 0 else float("nan")

    @property
    def null_underdispersed(self) -> bool:
        """A null more than 10% narrower than the statistic it judges.

        10% rather than any gap at all, because a permutation null carries
        its own Monte Carlo error and a few percent means nothing. On this
        data every one of the twelve is flagged, which is the finding
        rather than a tolerance being too tight.
        """
        return self.null_calibration < 0.9

    @property
    def verdict(self) -> str:
        """Whether this test can already rule a **tradeable** effect in or
        out, which is a different question from significance.

        This is the one output that turns rd-k's *"not shown, not shown
        absent"* into a number. An interval lying entirely inside
        +/-`cost_floor` says the data are inconsistent with an effect
        large enough to pay for its own round trip -- **shown absent, for
        trading purposes**, whatever the p-value did. An interval that
        spans the floor says only that this run could not tell.

        The interval is taken at the family's own alpha rather than a
        habitual 95%, so this reads at the same confidence as the decision
        rule it sits beside.

        `EXCLUDED` is a statement about a *tradeable* effect, never about
        a zero one: an interval of [-2.7, +1.7]bp excludes 12bp and is
        perfectly consistent with a real 1bp effect nobody can trade.
        """
        if self.ci_low > self.cost_floor or self.ci_high < -self.cost_floor:
            return "ABOVE FLOOR"
        if max(abs(self.ci_low), abs(self.ci_high)) < self.cost_floor:
            return "EXCLUDED"
        return "UNDERPOWERED"

    @property
    def instruments_for_floor(self) -> float:
        """`n_for_floor` expressed as independent instruments of this
        window's length, rather than as more years of this one.

        **The word doing the work is "independent."** BTC and ETH 1m move
        together, and this project has already measured 0.999955 daily
        log-return correlation between two *venues* for the same asset --
        a different pair, but the same warning. A nominal count here is an
        upper bound on what a universe buys, never a forecast.
        """
        return math.inf if math.isinf(self.n_for_floor) else self.n_for_floor / self.n_events


def power_row(
    test: ShiftTest,
    years: float,
    alpha: float | None = None,
    power: float = DEFAULT_POWER,
    cost_floor: float = DEFAULT_ROUND_TRIP,
) -> PowerRow:
    if years <= 0:
        raise ValueError(f"years must be positive, got {years}")
    a = alpha_rank1() if alpha is None else alpha
    # **The event arm's own standard error, not the null's spread.**
    #
    # The question "how many events would settle this" is about the
    # sampling variability of the statistic that was observed -- the event
    # mean -- which is `sigma_event / sqrt(n)`. `null_sd` is the spread of
    # the REFERENCE distribution, and the two coincide only when the null
    # is perfectly calibrated to the event arm. On this data they do not:
    # the matched null runs 1.05x to 2.45x narrower, because matching on
    # PRIOR volatility does not match FORWARD volatility for an event that
    # is itself a volatility burst.
    #
    # Using `null_sd` understated every required-event count by the square
    # of that ratio -- up to 6x -- and narrowed every interval by it. The
    # first version of this module did exactly that; rd-m's registered
    # prediction 4 is what caught it. `null_sd` is still carried, as the
    # diagnostic in `null_calibration`.
    se = (
        test.event_sd / math.sqrt(test.n_events)
        if test.event_sd > 0
        else test.null_sd
    )
    # **The interval is at the decision rule's own alpha, not a habitual
    # 95%.** Mixing the two put a contradiction in the first version of
    # this table: S2 at h=240 read EXCLUDED from a 95% interval while its
    # n@12bp of 2,068 said the study was not powered to see 12bp at all.
    # Neither figure was wrong; they were answering at different
    # confidence levels. Reported at one level, "EXCLUDED" means excluded
    # at the bar this family is actually judged at.
    lo, hi = effect_interval(test.effect, se, level=1.0 - a)
    # Powering against a bound that spans zero is not conservative, it is
    # undefined -- `required_events` would return a finite number for a
    # tiny negative effect and it would mean nothing. `inf` says "this run
    # cannot exclude no effect at all", which is the true statement.
    conservative = min(abs(lo), abs(hi)) if lo * hi > 0 else 0.0
    return PowerRow(
        situation=test.situation,
        horizon=test.horizon,
        n_events=test.n_events,
        effect=test.effect,
        se=se,
        null_sd=test.null_sd,
        permutation_p=test.p_value,
        normal_p=normal_p(test.effect, se),
        ci_low=lo,
        ci_high=hi,
        events_per_year=test.n_events / years,
        alpha=a,
        power=power,
        cost_floor=cost_floor,
        detectable=detectable_effect(se, a, power),
        n_for_observed=required_events(test.effect, se, test.n_events, a, power),
        n_for_floor=required_events(cost_floor, se, test.n_events, a, power),
        n_for_ci_low=required_events(conservative, se, test.n_events, a, power),
    )


def run(
    db_path: str = DEFAULT_DB_PATH,
    mode: str = "matched",
    power: float = DEFAULT_POWER,
    alpha: float | None = None,
    cost_floor: float = DEFAULT_ROUND_TRIP,
) -> list[PowerRow]:
    if mode not in NULL_MODES:
        raise ValueError(f"mode must be one of {NULL_MODES}, got {mode!r}")
    if cost_floor <= 0:
        raise ValueError(f"cost_floor must be positive, got {cost_floor}")
    years = window_years(db_path)
    tests = run_stage2(db_path, mode)
    return [power_row(t, years, alpha, power, cost_floor) for t in tests]


def _n(x: float) -> str:
    return "never" if math.isinf(x) else f"{x:,.0f}"


def _y(x: float) -> str:
    return "never" if math.isinf(x) else f"{x:,.1f}"


def report(rows: list[PowerRow], mode: str) -> None:
    if not rows:
        print("no tests")
        return
    a, p, floor = rows[0].alpha, rows[0].power, rows[0].cost_floor
    print(
        f"null: {mode}   alpha={a:.6f} (Benjamini-Yekutieli rank-1, m={FAMILY_SIZE})   "
        f"power={p:.0%}   cost floor {floor*1e4:.0f}bp"
    )
    print(
        f"a nominal alpha={ALPHA_NOMINAL} would need only "
        f"{(_z_sum(ALPHA_NOMINAL, p) / _z_sum(a, p)) ** 2:.2f}x these counts, so the "
        f"multiplicity correction\nnearly doubles the bill -- priced here rather than "
        f"argued about\n"
    )
    print(
        f"{'situation':26} {'h':>5} {'events':>7} {'effect':>8} {'se':>7} "
        f"{'nullsd':>7} {'cal':>5} "
        f"{'perm p':>9} {'norm p':>9} {f'{1-a:.2%} CI':>17} {'detect':>8} "
        f"{'n@obs':>10} {f'n@{floor*1e4:.0f}bp':>10} {f'yrs@{floor*1e4:.0f}bp':>9} "
        f"{'sym':>5}  {'verdict':<12}"
    )
    print("-" * 180)
    for r in sorted(rows, key=lambda x: x.permutation_p):
        print(
            f"{r.situation:26} {r.horizon:>5} {r.n_events:>7,} {r.effect*1e4:>8.2f} "
            f"{r.se*1e4:>7.2f} {r.null_sd*1e4:>7.2f} {r.null_calibration:>5.2f} "
            f"{r.permutation_p:>9.2e} {r.normal_p:>9.2e} "
            f"{f'[{r.ci_low*1e4:>6.2f},{r.ci_high*1e4:>6.2f}]':>17} "
            f"{r.detectable*1e4:>8.2f} {_n(r.n_for_observed):>10} "
            f"{_n(r.n_for_floor):>10} {_y(r.years_for(r.n_for_floor)):>9} "
            f"{_y(r.instruments_for_floor):>5}  {r.verdict:<12}"
        )
    excluded = [r for r in rows if r.verdict == "EXCLUDED"]
    under = [r for r in rows if r.verdict == "UNDERPOWERED"]
    above = [r for r in rows if r.verdict == "ABOVE FLOOR"]
    print(
        f"\n{len(excluded)} of {len(rows)} already EXCLUDE a tradeable effect at "
        f"{1-a:.2%} -- their whole interval\n  lies inside "
        f"+/-{floor*1e4:.0f}bp, so the "
        f"data are inconsistent with an effect that could pay for its\n  own round trip. "
        f"That is 'shown absent' for trading purposes, and it is a stronger\n  statement "
        f"than the 0-of-12 significance result, which was only 'not shown'."
    )
    print(
        f"{len(under)} of {len(rows)} are UNDERPOWERED -- the interval spans the floor, "
        f"so this run cannot tell.\n  Those are the only ones more events would settle."
    )
    # The three verdicts must partition the rows, or the summary silently
    # stops describing part of its own table.
    if above:
        print(
            f"{len(above)} of {len(rows)} lie entirely ABOVE FLOOR -- the interval "
            f"excludes +/-{floor*1e4:.0f}bp from outside.\n  Discovery mode: that is "
            f"still not a candidate and may not be promoted."
        )
    bad = [r for r in rows if r.null_underdispersed]
    if bad:
        print(
            f"\n{len(bad)} of {len(rows)} were judged against a null MORE THAN 10% "
            f"NARROWER than the statistic\n  it judges (worst cal "
            f"{min(r.null_calibration for r in bad):.2f}). A too-narrow null makes a "
            f"permutation p TOO SMALL,\n  so those p-values overstate their evidence -- "
            f"the safe direction for a negative\n  result, and not for anything that had "
            f"advanced."
        )
    print(
        "\ncal     : null_sd / se. 1.00 is a correctly calibrated null; below it the "
        "reference\n          distribution is narrower than the statistic it judges, and "
        "the permutation p is too small."
    )
    print(
        "n@obs   : events to detect the OBSERVED effect -- a floor on the cost, not "
        "an estimate;\n          an effect first seen at p ~ 0.06 is the high draw of "
        "its own distribution."
    )
    print(
        f"n@{floor*1e4:.0f}bp  : events to detect an effect exactly at the assumed round trip -- the "
        "decision-relevant one,\n          since a smaller effect is not a candidate "
        "however significant it becomes."
    )
    print(
        f"sym     : n@{floor*1e4:.0f}bp as INDEPENDENT instruments of this window's length. Crypto "
        "majors are not\n          independent, so this is an upper bound on what a "
        "universe buys."
    )
    print(
        "\nDiscovery mode: nothing here may be promoted or quoted as evidence of an edge."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--null", dest="mode", choices=NULL_MODES, default="matched",
                    help="'matched' is rd-k's deciding null and the default")
    ap.add_argument("--power", type=float, default=DEFAULT_POWER)
    ap.add_argument("--alpha", type=float, default=None,
                    help="default is the Benjamini-Yekutieli rank-1 threshold")
    ap.add_argument("--cost-floor", type=float, default=DEFAULT_ROUND_TRIP,
                    help="round trip as a fraction; default is BTC's measured "
                         "0.0012. rd-f puts the Korean one at 0.0030-0.0033")
    args = ap.parse_args(argv)
    report(
        run(args.db_path, args.mode, args.power, args.alpha, args.cost_floor),
        args.mode,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
