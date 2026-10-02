"""The fold-consistency clause has an attainability threshold, and it is high.

`CLAUDE.md`'s Eligibility Bar requires 80-90% of folds to show a positive
annualized Sharpe. That clause was set by `sr-j` to replace a literal 100%
sweep, on the argument that "even a genuinely strong, real 80%-true-edge
strategy clears a literal 19/19 sweep only ~1.4% of the time" — **an argument
about a win rate, never checked against an effect size.**

Checked here: at the 60-session daily geometry the 80% floor needs a true
annualized Sharpe of **1.701**, and the 90% floor **2.590**, both far outside
the 0.4-0.8 that credible institutional trend-following reports. So the clause
was unsatisfiable by any realistic candidate — a second obstruction independent
of the DSR one, since it involves neither `N` nor the panel's length.

The operator's amendment (2026-10-02) makes the clause report UNINFORMATIVE
below this threshold rather than FAIL, which is the handling
`check_criterion_attainable` already gives an unattainable trade count. These
tests pin the arithmetic so the threshold is computed from the geometry in force
rather than inherited as a constant.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from research.eligibility import fold_consistency_attainable_sharpe

#: The geometry `CLAUDE.md` specifies for the 1d path, and the KRX session count.
DAILY_FOLD_SESSIONS = 60
KRX_SESSIONS_PER_YEAR = 245.0


def _fold_positive_probability(sharpe: float, fold_sessions: int, sessions_per_year: float) -> float:
    """P(a fold's realised Sharpe > 0) for a true annualized `sharpe`."""
    tilt = sharpe * math.sqrt(fold_sessions / sessions_per_year)
    return NormalDist().cdf(tilt)


@pytest.mark.parametrize(
    "fraction,expected",
    [(0.80, 1.701), (0.90, 2.590)],
)
def test_the_daily_geometry_needs_a_sharpe_far_outside_a_realistic_edge(fraction, expected):
    """The two figures `CLAUDE.md` now quotes, recomputed from the geometry."""
    got = fold_consistency_attainable_sharpe(
        fold_sessions=DAILY_FOLD_SESSIONS,
        sessions_per_year=KRX_SESSIONS_PER_YEAR,
        min_fraction=fraction,
    )
    assert got is not None
    assert got == pytest.approx(expected, abs=0.002), f"{fraction:.0%} floor needs {got:.3f}"
    assert got > 0.8, (
        "the whole point of the amendment is that this threshold sits above the "
        "0.4-0.8 range a realistic edge occupies"
    )


@pytest.mark.parametrize(
    "sharpe,expected_probability",
    [(0.4, 0.578), (0.8, 0.654), (1.0, 0.690), (2.0, 0.839)],
)
def test_the_fold_positivity_table_in_claude_md(sharpe, expected_probability):
    """The other direction of the same arithmetic, which is the form the rule is
    stated in: how often a fold comes out positive for a given true effect."""
    got = _fold_positive_probability(sharpe, DAILY_FOLD_SESSIONS, KRX_SESSIONS_PER_YEAR)
    assert got == pytest.approx(expected_probability, abs=0.001)


def test_the_threshold_and_the_probability_are_inverses():
    """Guards against the two halves drifting apart: the Sharpe the function
    returns must be exactly the one that produces that fold fraction."""
    for fraction in (0.70, 0.80, 0.85, 0.90):
        sharpe = fold_consistency_attainable_sharpe(
            fold_sessions=DAILY_FOLD_SESSIONS,
            sessions_per_year=KRX_SESSIONS_PER_YEAR,
            min_fraction=fraction,
        )
        assert sharpe is not None
        back = _fold_positive_probability(sharpe, DAILY_FOLD_SESSIONS, KRX_SESSIONS_PER_YEAR)
        assert back == pytest.approx(fraction, abs=1e-9)


def test_a_longer_fold_lowers_the_threshold_and_a_longer_panel_does_not():
    """**The distinction that makes this a separate obstruction from the
    detection floor.** The floor improves with calendar span; this does not —
    it depends on the fold's own length, so a 27-year panel cut into 60-session
    folds is no better off than a 7-year one.
    """
    base = fold_consistency_attainable_sharpe(
        fold_sessions=60, sessions_per_year=KRX_SESSIONS_PER_YEAR, min_fraction=0.80
    )
    longer_fold = fold_consistency_attainable_sharpe(
        fold_sessions=480, sessions_per_year=KRX_SESSIONS_PER_YEAR, min_fraction=0.80
    )
    assert base is not None and longer_fold is not None
    assert longer_fold < base, "lengthening the fold must help"
    assert longer_fold == pytest.approx(0.601, abs=0.002), (
        "480-session folds are what put an 80% floor within reach of a 0.6 "
        "Sharpe -- the alternative CLAUDE.md records as colliding with the "
        "8-10 fold floor"
    )

    # The panel's own length appears nowhere in the computation, which is the
    # asymmetry: the same call cannot be made to depend on it.
    assert fold_consistency_attainable_sharpe(
        fold_sessions=60, sessions_per_year=KRX_SESSIONS_PER_YEAR, min_fraction=0.80
    ) == base


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fold_sessions": 0, "sessions_per_year": 245.0, "min_fraction": 0.8},
        {"fold_sessions": -60, "sessions_per_year": 245.0, "min_fraction": 0.8},
        {"fold_sessions": 60, "sessions_per_year": 0.0, "min_fraction": 0.8},
        {"fold_sessions": 60, "sessions_per_year": 245.0, "min_fraction": 0.0},
        {"fold_sessions": 60, "sessions_per_year": 245.0, "min_fraction": 1.0},
        {"fold_sessions": 60, "sessions_per_year": 245.0, "min_fraction": 1.5},
    ],
)
def test_undefined_inputs_return_none_rather_than_a_number(kwargs):
    """The "no evidence" convention the rest of this module uses. A threshold
    invented from a degenerate input would be worse than an absent one, because
    a caller reports it beside a fold fraction as though it meant something."""
    assert fold_consistency_attainable_sharpe(**kwargs) is None
