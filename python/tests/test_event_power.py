"""Tests for `research.event_power`.

The module turns a non-result into a price, so the properties that matter
are the ones that stop the price from being flattering:

- **The alpha is the BY rank-1 threshold, not 0.05.** Using the nominal
  one would halve every count in the table.
- **A zero or sign-ambiguous effect costs `inf` events**, not a large
  finite number. No sample size establishes a null, and a finite figure
  there would read as "keep going, it is nearly there."
- **The normal approximation is reported, never assumed.** A permutation
  tail and a normal tail are different objects, and the module prints
  both so a disagreement is visible.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from research.event_power import (
    ALPHA_NOMINAL,
    DEFAULT_POWER,
    PowerRow,
    alpha_rank1,
    detectable_effect,
    effect_interval,
    normal_p,
    power_row,
    report,
    required_events,
    window_years,
)
from research.situation_catalogue import DEFAULT_ROUND_TRIP
from research.stage2_event_study import FAMILY_SIZE, FDR_Q, dependence_penalty
from research.stage2_shift_null import ShiftTest


def _test(effect=0.0010, se=0.0005, n=786, p=0.064, mode="matched", null_sd=None):
    """A `ShiftTest` whose `effect` and event-arm `se` are what was asked for.

    `effect` is `observed - null_mean`, so the observed is built from the
    null rather than set directly -- pinning the derived quantity would let
    the fixture disagree with the dataclass.

    `event_sd` is set so that `event_sd / sqrt(n)` is exactly `se`, since
    that is what `power_row` now uses. `null_sd` defaults to the same
    value, i.e. a **perfectly calibrated** null; pass it explicitly to
    build a mis-calibrated one.
    """
    return ShiftTest(
        situation="S3 abnormal activity",
        horizon=240,
        n_events=n,
        observed=0.0007 + effect,
        null_mean=0.0007,
        null_sd=se if null_sd is None else null_sd,
        unconditional=0.0002,
        p_value=p,
        permutations=2000,
        event_sd=se * math.sqrt(n),
        null_mode=mode,
    )


# ------------------------------------------------------------ the alpha


def test_the_alpha_is_the_benjamini_yekutieli_rank_one_threshold():
    """0.002685 at m=12 -- the bar rd-j's decision rule actually sets."""
    assert alpha_rank1() == pytest.approx(0.002685, abs=5e-7)
    assert alpha_rank1() == pytest.approx(FDR_Q / (FAMILY_SIZE * dependence_penalty()))


def test_the_correction_costs_roughly_twice_the_events():
    """Named so the multiplicity correction is priced rather than argued
    about: the same effect needs about half as many events at a nominal
    0.05, and quoting that number would understate the real cost."""
    kw = dict(effect=0.0010, se=0.0005, n_events=786)
    strict = required_events(alpha=alpha_rank1(), **kw)
    nominal = required_events(alpha=ALPHA_NOMINAL, **kw)
    assert 1.8 < strict / nominal < 2.3


# -------------------------------------------------- required_events


def test_required_events_scales_with_the_inverse_square_of_the_effect():
    """Halving the effect quadruples the cost -- the property that makes a
    weak-but-real signal so expensive to establish."""
    kw = dict(se=0.0005, n_events=786, alpha=0.002685)
    big = required_events(effect=0.0020, **kw)
    small = required_events(effect=0.0010, **kw)
    assert small / big == pytest.approx(4.0)


def test_a_zero_effect_costs_infinitely_many_events():
    """Not a large finite number. No sample size establishes a null, and a
    finite figure would read as 'nearly there'."""
    assert math.isinf(required_events(0.0, 0.0005, 786, 0.002685))


def test_the_sign_of_the_effect_does_not_change_the_cost():
    a = required_events(0.0010, 0.0005, 786, 0.002685)
    b = required_events(-0.0010, 0.0005, 786, 0.002685)
    assert a == pytest.approx(b)


def test_required_events_matches_the_closed_form():
    n, se, eff, alpha = 786, 0.0005, 0.0010, 0.002685
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(DEFAULT_POWER)
    assert required_events(eff, se, n, alpha) == pytest.approx(n * (z * se / eff) ** 2)


def test_an_effect_at_the_detection_boundary_needs_exactly_the_events_it_has():
    """The two directions are inverses, so the round trip pins them
    against each other rather than against my own arithmetic twice."""
    se, n, alpha = 0.0005, 786, 0.002685
    d = detectable_effect(se, alpha)
    assert required_events(d, se, n, alpha) == pytest.approx(n)


@pytest.mark.parametrize("bad", [0.0, -1e-6])
def test_a_non_positive_se_is_refused(bad):
    with pytest.raises(ValueError, match="se must be positive"):
        required_events(0.001, bad, 786, 0.002685)


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_an_out_of_range_alpha_is_refused(alpha):
    with pytest.raises(ValueError, match="alpha must be in"):
        required_events(0.001, 0.0005, 786, alpha)


@pytest.mark.parametrize("power", [0.0, 1.0, -0.1, 1.5])
def test_an_out_of_range_power_is_refused(power):
    with pytest.raises(ValueError, match="power must be in"):
        required_events(0.001, 0.0005, 786, 0.002685, power)


def test_a_non_positive_event_count_is_refused():
    with pytest.raises(ValueError, match="n_events must be positive"):
        required_events(0.001, 0.0005, 0, 0.002685)


# ---------------------------------------------------- interval and p


def test_the_interval_is_symmetric_about_the_effect():
    lo, hi = effect_interval(0.0010, 0.0005)
    assert (lo + hi) / 2 == pytest.approx(0.0010)
    assert hi - lo == pytest.approx(2 * 1.959964 * 0.0005, rel=1e-5)


def test_a_wider_level_gives_a_wider_interval():
    n95 = effect_interval(0.001, 0.0005, 0.95)
    n99 = effect_interval(0.001, 0.0005, 0.99)
    assert n99[0] < n95[0] and n99[1] > n95[1]


@pytest.mark.parametrize("level", [0.0, 1.0, -0.5, 2.0])
def test_an_out_of_range_level_is_refused(level):
    with pytest.raises(ValueError, match="level must be in"):
        effect_interval(0.001, 0.0005, level)


def test_normal_p_is_two_sided_and_sign_blind():
    assert normal_p(0.001, 0.0005) == pytest.approx(normal_p(-0.001, 0.0005))
    assert normal_p(0.0005 * 1.959964, 0.0005) == pytest.approx(0.05, rel=1e-4)


# --------------------------------------------------------- power_row


def test_a_row_carries_both_p_values_so_a_disagreement_is_visible():
    """The permutation p is the real one; the normal p is what the power
    arithmetic assumes. Printing only the second would hide the
    approximation the whole table rests on."""
    r = power_row(_test(effect=0.001361, se=0.000735, p=0.064), years=6.96)
    assert r.permutation_p == 0.064
    assert r.normal_p == pytest.approx(normal_p(0.001361, 0.000735))
    assert 0.03 < r.normal_p < 0.12


def test_an_interval_spanning_zero_costs_infinite_events():
    """A conservative bound that spans zero is not conservative, it is
    undefined. Powering against the near-zero end would return a finite
    number that means nothing."""
    r = power_row(_test(effect=0.0002, se=0.0005), years=6.96)
    assert r.ci_low < 0 < r.ci_high
    assert math.isinf(r.n_for_ci_low)


def test_an_interval_clear_of_zero_gives_a_finite_conservative_cost():
    r = power_row(_test(effect=0.0030, se=0.0005), years=6.96)
    assert r.ci_low > 0
    assert math.isfinite(r.n_for_ci_low)
    # The conservative target is smaller than the observed one, so it
    # costs more -- that ordering is the point of reporting both.
    assert r.n_for_ci_low > r.n_for_observed


def test_the_cost_floor_target_is_the_round_trip_not_the_observed_effect():
    r = power_row(_test(effect=0.0500, se=0.0005), years=6.96)
    assert r.n_for_floor == pytest.approx(
        required_events(DEFAULT_ROUND_TRIP, 0.0005, 786, r.alpha)
    )
    # A huge observed effect is cheap; the floor is unmoved by it.
    assert r.n_for_observed < r.n_for_floor


def test_the_event_rate_is_per_year_of_the_measured_window():
    r = power_row(_test(n=786), years=6.96)
    assert r.events_per_year == pytest.approx(786 / 6.96)
    assert r.years_for(786 * 4) == pytest.approx(4 * 6.96)


def test_years_for_an_unreachable_count_is_never_not_a_number():
    r = power_row(_test(effect=0.0), years=6.96)
    assert math.isinf(r.n_for_observed) and math.isinf(r.years_for(r.n_for_observed))


def test_instruments_expresses_the_cost_as_a_universe_rather_than_a_wait():
    r = power_row(_test(effect=0.001361, se=0.000735), years=6.96)
    assert r.instruments_for_floor == pytest.approx(r.n_for_floor / r.n_events)


# ----------------------------------------------------------- the verdict


def test_a_tight_interval_inside_the_floor_excludes_a_tradeable_effect():
    """The real S2 h=15 shape: [-2.67, +1.67]bp. The p-value says only
    'not significant'; the interval says a 12bp effect is inconsistent
    with this data, which is the stronger and more useful statement."""
    r = power_row(_test(effect=-0.00005, se=0.000111), years=6.96)
    assert r.verdict == "EXCLUDED"
    assert max(abs(r.ci_low), abs(r.ci_high)) < DEFAULT_ROUND_TRIP


def test_excluded_is_about_a_tradeable_effect_not_a_zero_one():
    """An interval excluding 12bp is perfectly consistent with a real 1bp
    effect nobody can trade, and the module must not be read as claiming
    otherwise."""
    r = power_row(_test(effect=0.0001, se=0.00002), years=6.96)
    assert r.verdict == "EXCLUDED"
    assert r.ci_low > 0  # a real, positive, entirely untradeable effect


def test_the_interval_is_at_the_familys_alpha_not_a_habitual_95_percent():
    """Mixing the two contradicted this table's first version: S2 at h=240
    read EXCLUDED from a 95% interval while its own `n@12bp` said the study
    could not see 12bp at all. Both were right at their own confidence
    level, which is why only one may appear."""
    r = power_row(_test(effect=0.0010, se=0.0005), years=6.96)
    wide = effect_interval(0.0010, 0.0005, level=1.0 - alpha_rank1())
    assert (r.ci_low, r.ci_high) == pytest.approx(wide)
    # And it is genuinely wider than the conventional one, not equal to it.
    assert r.ci_high > effect_interval(0.0010, 0.0005, 0.95)[1]


def test_a_test_that_excludes_the_floor_is_also_broadly_powered_for_it():
    """The two criteria are not identical -- exclusion also uses the
    observed effect -- but they now read at one confidence level, so a
    flat contradiction between the verdict and `n@12bp` is not possible
    for a test whose observed effect is not tiny."""
    r = power_row(_test(effect=0.0004, se=0.0002), years=6.96)
    assert r.verdict == "EXCLUDED"
    assert r.n_for_floor < r.n_events


def test_an_interval_spanning_the_floor_is_underpowered():
    """The real S3 h=240 shape: [-0.74, +27.97]bp -- it admits both no
    effect and a very tradeable one, so more events would settle it."""
    r = power_row(_test(effect=0.001361, se=0.000733), years=6.96)
    assert r.verdict == "UNDERPOWERED"
    assert r.ci_low < DEFAULT_ROUND_TRIP < r.ci_high


def test_an_interval_entirely_above_the_floor_is_not_called_underpowered():
    """Not reachable in this run, and the branch exists so a future result
    that does clear the floor is never mislabelled as 'could not tell'."""
    r = power_row(_test(effect=0.0050, se=0.0005), years=6.96)
    assert r.verdict == "ABOVE FLOOR"


def test_a_negative_effect_below_minus_the_floor_is_also_above_floor():
    """Two-sided by construction, as the whole family is."""
    assert power_row(_test(effect=-0.0050, se=0.0005), years=6.96).verdict == "ABOVE FLOOR"


def test_the_verdict_moves_with_the_cost_floor_it_is_measured_against():
    """A Korean round trip is 2.5-2.8x harsher (rd-f), so the same
    interval that excludes a tradeable BTC effect need not exclude a
    tradeable Korean one -- the verdict is a statement about a cost, not
    only about the data."""
    t = _test(effect=0.0010, se=0.0006)
    assert power_row(t, years=6.96, cost_floor=0.0012).verdict == "UNDERPOWERED"
    assert power_row(t, years=6.96, cost_floor=0.0033).verdict == "EXCLUDED"


def test_the_report_counts_the_excluded_and_the_underpowered(capsys):
    rows = [
        power_row(_test(effect=-0.00005, se=0.000111), years=6.96),
        power_row(_test(effect=0.001361, se=0.000733), years=6.96),
    ]
    report(rows, "matched")
    out = capsys.readouterr().out
    assert "1 of 2 already EXCLUDE" in out and "1 of 2 are UNDERPOWERED" in out


def test_a_non_positive_window_is_refused():
    with pytest.raises(ValueError, match="years must be positive"):
        power_row(_test(), years=0.0)


# ------------------------------------------------------- window_years


def test_window_years_is_measured_from_the_series(tmp_path):
    """Hardcoding 6.96 would go stale the moment the series is extended,
    and every calendar figure in the report is derived from it."""
    import sqlite3

    from research.stage2_event_study import SYMBOL

    p = tmp_path / "k.sqlite3"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER)"
    )
    day = 24 * 60 * 60 * 1000
    conn.executemany(
        "INSERT INTO klines VALUES (?,?,?)",
        [(SYMBOL, "1m", 0), (SYMBOL, "1m", 731 * day)],
    )
    conn.commit()
    conn.close()
    assert window_years(str(p)) == pytest.approx(2.0, rel=1e-3)


def test_a_series_that_is_not_there_is_an_error_not_a_zero(tmp_path):
    import sqlite3

    p = tmp_path / "k.sqlite3"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="no 1m rows"):
        window_years(str(p))


# ------------------------------------------------------------- report


def test_the_report_names_the_alpha_the_floor_and_the_approximation(capsys):
    rows = [power_row(_test(effect=0.001361, se=0.000735, p=0.064), years=6.96)]
    report(rows, "matched")
    out = capsys.readouterr().out
    assert "0.002685" in out and "Benjamini-Yekutieli" in out
    assert "12bp" in out
    assert "Discovery mode" in out


def test_an_unreachable_count_prints_as_never_rather_than_inf(capsys):
    report([power_row(_test(effect=0.0), years=6.96)], "matched")
    assert "never" in capsys.readouterr().out


def test_an_empty_run_reports_rather_than_indexing_into_nothing(capsys):
    report([], "matched")
    assert "no tests" in capsys.readouterr().out


def test_the_row_is_frozen_so_a_reported_figure_cannot_be_edited_after():
    r = power_row(_test(), years=6.96)
    with pytest.raises(Exception):
        r.effect = 1.0  # type: ignore[misc]
    assert isinstance(r, PowerRow)


# ------------------------------------------------- the standard error


def test_the_se_is_the_event_arms_own_not_the_nulls():
    """**The correction rd-m's prediction 4 caught.** rd-k's matched null
    matches on PRIOR volatility, and an event that is itself a volatility
    burst has a more dispersed FORWARD return than any bar sharing its
    prior-volatility decile -- so the null runs up to 2.45x narrower than
    the statistic it judges. Required counts scale as `se^2`, so using the
    null understated them by up to 6x."""
    r = power_row(_test(se=0.0005, n=786, null_sd=0.0002), years=6.96)
    assert r.se == pytest.approx(0.0005)
    assert r.null_sd == pytest.approx(0.0002)


def test_a_narrower_null_is_reported_as_a_calibration_below_one():
    r = power_row(_test(se=0.0005, null_sd=0.0002), years=6.96)
    assert r.null_calibration == pytest.approx(0.4)
    assert r.null_underdispersed


def test_a_calibrated_null_is_not_flagged():
    r = power_row(_test(se=0.0005), years=6.96)
    assert r.null_calibration == pytest.approx(1.0)
    assert not r.null_underdispersed


def test_a_few_percent_of_monte_carlo_noise_is_not_a_finding():
    """The flag is at 10%, not at any gap at all: a permutation null
    carries its own Monte Carlo error."""
    assert not power_row(_test(se=0.0005, null_sd=0.00048), years=6.96).null_underdispersed


def test_using_the_null_would_have_understated_the_cost_by_the_square():
    """Naming the size of the error the correction fixes, so a future
    reader can tell it was material rather than tidy."""
    good = power_row(_test(effect=0.0010, se=0.0005, n=786), years=6.96)
    # what the first version computed, with the null's spread as the se
    bad = required_events(0.0010, 0.0002, 786, good.alpha)
    assert good.n_for_observed / bad == pytest.approx((0.0005 / 0.0002) ** 2)


def test_a_test_without_an_event_sd_falls_back_to_the_null():
    """`event_sd` is additive with a 0.0 default, so a `ShiftTest` built
    before this field existed must still produce a row rather than a
    division by zero."""
    t = ShiftTest(
        situation="S1 support penetration", horizon=15, n_events=100,
        observed=0.001, null_mean=0.0, null_sd=0.0004, unconditional=0.0,
        p_value=0.2, permutations=2000,
    )
    assert t.event_sd == 0.0
    assert power_row(t, years=6.96).se == pytest.approx(0.0004)


def test_the_report_names_an_underdispersed_null(capsys):
    report([power_row(_test(se=0.0005, null_sd=0.0002), years=6.96)], "matched")
    out = capsys.readouterr().out
    assert "NARROWER" in out and "TOO SMALL" in out


def test_the_report_accounts_for_every_verdict(capsys):
    """The three verdicts must partition the rows. A summary that counts
    only two silently stops describing part of its own table."""
    rows = [
        power_row(_test(effect=0.0001, se=0.00002), years=6.96),   # EXCLUDED
        power_row(_test(effect=0.001361, se=0.000733), years=6.96),  # UNDERPOWERED
        power_row(_test(effect=0.0050, se=0.0005), years=6.96),    # ABOVE FLOOR
    ]
    report(rows, "matched")
    out = capsys.readouterr().out
    assert "1 of 3 already EXCLUDE" in out
    assert "1 of 3 are UNDERPOWERED" in out
    assert "1 of 3 lie entirely ABOVE FLOOR" in out
