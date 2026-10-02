"""Can a window answer the question before it is spent on one?

This exists because of a sequencing failure the external review named and this
project had committed repeatedly: specifying a study, running it, and only then
discovering that the window could not have detected the effect being looked for.
`CLAUDE.md`'s own rule is to compute the detectable effect **before** specifying
the family, and `research/retrospective.detection_floor_sharpe` gives only half
of what that needs.

Three separate constraints bind, and they pull against each other:

- **statistical** — a longer window detects a smaller effect, and the *floor*
  (alpha only) is a weaker requirement than *power* (alpha and beta);
- **economic** — turnover costs are linear in round trips, so a cost ceiling is
  a turnover ceiling, which is a holding-period floor;
- **evidential** — the Eligibility Bar's trade-count floor is a *minimum* number
  of trades, which pulls the opposite way from the turnover ceiling.

The functions here compute each, and `feasible_shapes` finds where they
intersect. Nothing here decides anything: a shape being feasible says the
question is answerable, never that the answer will be favourable.

**The portfolio result is the one worth knowing in advance**: trade count and
turnover are separable. A round trip in one of `n` equally weighted names moves
`1/n` of capital, so widening the universe multiplies trades without touching
turnover. A single name cannot reach a 100-trade floor across 27 years at a
holding period the cost ceiling permits; thirty names reach it with room to
spare.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

#: One-sided alpha, matching `retrospective.DEFAULT_SIGNIFICANCE_ALPHA`.
DEFAULT_ALPHA = 0.05
#: Conventional power target. Stated as a default rather than a rule — the
#: project has no approved power standard, and choosing one is an operator
#: decision. 0.80 is the standard planning value.
DEFAULT_POWER = 0.80
#: KRX trades roughly this many sessions a year. Used only to convert turnover
#: into a holding period, never to compute a detection floor (which depends on
#: calendar span, not session count).
KRX_SESSIONS_PER_YEAR = 245.0


def detection_floor(years: float, *, alpha: float = DEFAULT_ALPHA) -> float | None:
    """The annualized Sharpe below which a result is indistinguishable from
    noise at one-sided `alpha`. Mirrors `retrospective.detection_floor_sharpe`.
    """
    if years <= 0:
        return None
    return NormalDist().inv_cdf(1 - alpha) / math.sqrt(years)


def smallest_detectable_at_power(
    years: float, *, alpha: float = DEFAULT_ALPHA, power: float = DEFAULT_POWER
) -> float | None:
    """The smallest true Sharpe this span detects with probability `power`.

    **This is the figure a study should be sized against, and the floor is not.**
    The floor says what a result must exceed to be publishable; this says what a
    real effect must be for the study to find it. They differ by the power
    quantile, which is a factor of 2.28 in required years at the conventional
    0.05/0.80 pair.
    """
    if years <= 0 or not 0 < power < 1:
        return None
    n = NormalDist()
    return (n.inv_cdf(1 - alpha) + n.inv_cdf(power)) / math.sqrt(years)


def power_for(
    true_sharpe: float, years: float, *, alpha: float = DEFAULT_ALPHA
) -> float | None:
    """Probability this span rejects the null for a given true Sharpe."""
    if years <= 0:
        return None
    n = NormalDist()
    return 1 - n.cdf(n.inv_cdf(1 - alpha) - true_sharpe * math.sqrt(years))


def cost_drag(round_trips_per_year: float, round_trip_bps: float) -> float:
    """Annual cost as a fraction of capital. Linear in round trips by
    construction — one full-capital-equivalent round trip costs its spread once.
    """
    return round_trips_per_year * round_trip_bps / 10_000.0


def max_round_trips(
    *, round_trip_bps: float, gross_return: float, cost_budget_fraction: float
) -> float | None:
    """Round trips a year before costs eat more than `cost_budget_fraction` of a
    strategy's gross return.

    `gross_return` is a fraction of capital. A 0.5 Sharpe at 15% volatility is
    0.075, which is the figure this project's own realistic-edge range implies.
    """
    if round_trip_bps <= 0 or gross_return <= 0 or not 0 < cost_budget_fraction <= 1:
        return None
    return gross_return * cost_budget_fraction / (round_trip_bps / 10_000.0)


def trade_count_floor(sessions: float) -> int:
    """`CLAUDE.md`'s frequency-scaled floor: roughly one trade per 20 evaluated
    sessions, clamped to [30, 100].
    """
    return max(30, min(100, int(sessions / 20)))


@dataclass(frozen=True)
class Shape:
    """One candidate strategy shape, and whether the three constraints allow it."""

    universe_size: int
    trades_per_name_per_year: float
    years: float
    round_trip_bps: float

    @property
    def trades_per_year(self) -> float:
        return self.universe_size * self.trades_per_name_per_year

    @property
    def total_trades(self) -> float:
        return self.trades_per_year * self.years

    @property
    def round_trips_per_year(self) -> float:
        """**Trade count and turnover are separable, and this is where.**

        A round trip in one of `n` equally weighted names moves `1/n` of
        capital, so `trades_per_year / n` — which is just
        `trades_per_name_per_year`. Widening the universe multiplies trades and
        leaves turnover alone.
        """
        if self.universe_size <= 0:
            return 0.0
        return self.trades_per_year / self.universe_size

    @property
    def drag(self) -> float:
        return cost_drag(self.round_trips_per_year, self.round_trip_bps)

    @property
    def mean_holding_sessions(self) -> float | None:
        if self.round_trips_per_year <= 0:
            return None
        return KRX_SESSIONS_PER_YEAR / self.round_trips_per_year

    def meets_trade_floor(self) -> bool:
        return self.total_trades >= trade_count_floor(self.years * KRX_SESSIONS_PER_YEAR)

    def within_cost_budget(self, *, gross_return: float, budget_fraction: float) -> bool:
        return self.drag <= gross_return * budget_fraction


def feasible_shapes(
    *,
    years: float,
    round_trip_bps: float,
    gross_return: float,
    cost_budget_fraction: float,
    universe_sizes: tuple[int, ...] = (1, 5, 10, 20, 30),
    trades_per_name: tuple[float, ...] = (1.0, 2.0, 4.0),
) -> list[Shape]:
    """Every shape in the grid that clears both the trade floor and the cost
    budget. Order is the input order, not a ranking — nothing here prefers one
    shape to another.
    """
    out: list[Shape] = []
    for n in universe_sizes:
        for per_name in trades_per_name:
            shape = Shape(n, per_name, years, round_trip_bps)
            if shape.meets_trade_floor() and shape.within_cost_budget(
                gross_return=gross_return, budget_fraction=cost_budget_fraction
            ):
                out.append(shape)
    return out
