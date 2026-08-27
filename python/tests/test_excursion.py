"""Unit tests for `research.excursion`.

The properties worth pinning are the ones in S8 §3.7's calculation
contract -- the ones that, if they drift, make the same trades yield
different stop boundaries: measurement starts at the fill bar not the
signal bar, excursions are magnitudes on both sides, costs are charged,
censored positions are excluded from anything that judges an exit, and
the sample-size warning is part of the output rather than a footnote.
"""

import pytest

from research.excursion import (
    Excursion,
    fragility_check,
    mae_by_outcome,
    measure_excursion,
    mfe_capture_rate,
    recommend_stop,
)


def series(prices):
    """Flat bars -- open == high == low == close -- so the intrabar path
    is unambiguous and every excursion is hand-checkable.

    Returns (opens, highs, lows, closes) in the order `measure_excursion`
    takes them.
    """
    return list(prices), list(prices), list(prices), list(prices)


# --- the fill-bar contract --------------------------------------------------


def test_entry_is_the_open_of_the_bar_after_the_signal():
    """simulate_fill fills at next_bar.open, so the signal bar is excluded
    entirely and the entry price is an OPEN, not a close."""
    o, h, l, c = series([100.0, 900.0, 100.0, 100.0, 100.0])
    e = measure_excursion(0, "long", o, h, l, c, max_hold=3)
    assert e is not None
    assert e.entry_price == 900.0, "entry must be the OPEN of the bar after the signal"
    assert e.mae_bps > 0   # the 100.0 bars that follow are adverse from 900
    assert e.mfe_bps == 0


def test_movement_before_the_entry_open_is_never_counted_as_excursion():
    """The fill bar's own range is fair game -- it all happens after the
    open we entered at -- but nothing before that open may leak in. Here
    the SIGNAL bar has a violent range that would dominate every
    excursion if the implementation reached back into it."""
    opens =  [100.0, 100.0, 100.0]
    highs =  [500.0, 101.0, 101.0]   # signal bar spikes to 500
    lows =   [ 10.0,  99.0,  99.0]   # and down to 10
    closes = [100.0, 100.0, 100.0]
    e = measure_excursion(0, "long", opens, highs, lows, closes, max_hold=1)
    assert e is not None
    assert e.entry_price == 100.0
    # Only bars 1-2 count: +/-1% around a 100 entry.
    assert e.mae_bps == pytest.approx(100.0)
    assert e.mfe_bps == pytest.approx(100.0)


def test_returns_none_when_the_fill_bar_does_not_exist():
    o, h, l, c = series([100.0, 101.0])
    # Signal on the last bar: there is no next bar to fill at.
    assert measure_excursion(1, "long", o, h, l, c, max_hold=5) is None
    assert measure_excursion(5, "long", o, h, l, c, max_hold=5) is None
    # Signal on bar 0 DOES fill, at bar 1's open, and is censored because
    # the data runs out before max_hold elapses.
    e = measure_excursion(0, "long", o, h, l, c, max_hold=5)
    assert e is not None and e.censored


def test_measure_excursion_rejects_ragged_price_series():
    with pytest.raises(ValueError, match="same length"):
        measure_excursion(0, "long", [1.0], [1.0, 2.0], [1.0, 2.0], [1.0, 2.0], max_hold=1)


# --- excursions are magnitudes on both sides --------------------------------


def test_long_excursions_are_measured_in_the_right_directions():
    o, h, l, c = series([100.0, 100.0, 95.0, 110.0, 100.0])
    e = measure_excursion(0, "long", o, h, l, c, max_hold=3)
    assert e is not None
    assert e.entry_price == 100.0
    assert e.mae_bps == pytest.approx(500.0)   # down to 95
    assert e.mfe_bps == pytest.approx(1000.0)  # up to 110
    assert e.outcome_bps == pytest.approx(0.0)


def test_short_excursions_invert_adverse_and_favorable():
    o, h, l, c = series([100.0, 100.0, 95.0, 110.0, 100.0])
    e = measure_excursion(0, "short", o, h, l, c, max_hold=3)
    assert e is not None
    # For a short, a rise is adverse and a fall is favourable.
    assert e.mae_bps == pytest.approx(1000.0)
    assert e.mfe_bps == pytest.approx(500.0)


def test_a_short_that_falls_is_a_winner_and_a_long_that_falls_is_not():
    o, h, l, c = series([100.0, 100.0, 90.0])
    long_pos = measure_excursion(0, "long", o, h, l, c, max_hold=1)
    short_pos = measure_excursion(0, "short", o, h, l, c, max_hold=1)
    assert long_pos is not None and short_pos is not None
    assert not long_pos.is_winner
    assert short_pos.is_winner
    assert long_pos.outcome_bps == pytest.approx(-short_pos.outcome_bps)


# --- costs and ATR units ----------------------------------------------------


def test_costs_are_charged_against_the_outcome():
    o, h, l, c = series([100.0, 100.0, 101.0])
    free = measure_excursion(0, "long", o, h, l, c, max_hold=1)
    charged = measure_excursion(0, "long", o, h, l, c, max_hold=1, cost_bps=12.0)
    assert free is not None and charged is not None
    assert charged.outcome_bps == pytest.approx(free.outcome_bps - 12.0)


def test_a_win_becomes_a_loss_once_costs_exceed_it():
    o, h, l, c = series([100.0, 100.0, 100.05])  # +5bps gross
    e = measure_excursion(0, "long", o, h, l, c, max_hold=1, cost_bps=12.0)
    assert e is not None
    assert not e.is_winner, "5bps gross cannot survive a 12bps round trip"


def test_atr_units_are_the_excursion_divided_by_atr_at_entry():
    o, h, l, c = series([100.0, 100.0, 99.0])
    e = measure_excursion(0, "long", o, h, l, c, max_hold=1, atr=1.0)
    assert e is not None
    # ATR of 1.0 on a 100 entry is 100bps; MAE is 100bps -> exactly 1 ATR.
    assert e.mae_atr == pytest.approx(1.0)


def test_atr_units_are_none_when_atr_is_absent_or_degenerate():
    o, h, l, c = series([100.0, 100.0, 99.0])
    for bad in (None, 0.0, -1.0):
        e = measure_excursion(0, "long", o, h, l, c, max_hold=1, atr=bad)
        assert e is not None
        assert e.mae_atr is None and e.mfe_atr is None


@pytest.mark.parametrize("kwargs, match", [
    ({"side": "sideways"}, "side"),
    ({"max_hold": 0}, "max_hold"),
])
def test_measure_excursion_rejects_invalid_arguments(kwargs, match):
    o, h, l, c = series([100.0] * 10)
    args = {"index": 0, "side": "long", "opens": o, "highs": h, "lows": l, "closes": c, "max_hold": 3}
    args.update(kwargs)
    with pytest.raises(ValueError, match=match):
        measure_excursion(**args)


# --- censoring --------------------------------------------------------------


def test_reaching_the_holding_limit_is_a_real_exit_not_censoring():
    """A fixed holding period is a legitimate time-based exit, so the
    outcome at that point is real. Treating it as censoring makes every
    position censored when there is no other exit rule, which renders the
    whole study vacuous -- that is what the first run of this analysis
    did."""
    o, h, l, c = series([100.0] * 20)
    e = measure_excursion(0, "long", o, h, l, c, max_hold=5)
    assert e is not None
    assert not e.censored


def test_running_out_of_data_before_the_holding_limit_is_censoring():
    # The observation was truncated: what the position would have done is
    # genuinely unknown.
    o, h, l, c = series([100.0] * 6)
    e = measure_excursion(0, "long", o, h, l, c, max_hold=100)
    assert e is not None
    assert e.censored
    assert e.mfe_capture is None, "a truncated observation has no real outcome to judge"


def _ex(mae, outcome, censored=False, mfe=100.0, gross=None):
    return Excursion(0, "long", 100.0, mae, mfe, outcome,
                     outcome if gross is None else gross, mae, mfe, censored)


def test_censored_positions_are_excluded_from_the_stop_recommendation():
    real = [_ex(0.3, 10.0) for _ in range(60)] + [_ex(1.5, -10.0) for _ in range(60)]
    censored = [_ex(0.01, 500.0, censored=True) for _ in range(500)]
    rec = recommend_stop(real + censored)
    assert rec.n == 120, "censored positions must not enter the winner/loser split"
    assert rec.winner_mae_p80 == pytest.approx(0.3)


# --- the recommendation itself ----------------------------------------------


def test_the_boundary_separates_winners_from_losers_when_one_exists():
    """Sweeney's premise: winners cluster below some adverse excursion and
    losers extend past it. When that structure is present the
    recommendation must find it."""
    winners = [_ex(0.2 + 0.001 * i, 20.0) for i in range(100)]
    losers = [_ex(1.5 + 0.001 * i, -20.0) for i in range(100)]
    rec = recommend_stop(winners + losers)
    assert rec.n_winners == 100 and rec.n_losers == 100
    assert rec.winner_mae_p80 is not None
    assert rec.winner_mae_p80 < 0.5, "a stop at the winners' p80 should be well below the losers"
    assert rec.losers_cut_at_p80 == pytest.approx(1.0), "every loser should be cut by that stop"


def test_the_sample_size_warning_is_part_of_the_output():
    small = [_ex(0.3, 10.0) for _ in range(10)] + [_ex(1.5, -10.0) for _ in range(10)]
    rec = recommend_stop(small)
    assert rec.warning is not None and "50+" in rec.warning

    medium = [_ex(0.3, 10.0) for _ in range(40)] + [_ex(1.5, -10.0) for _ in range(40)]
    assert "100+" in (recommend_stop(medium).warning or "")

    large = [_ex(0.3, 10.0) for _ in range(200)] + [_ex(1.5, -10.0) for _ in range(200)]
    assert recommend_stop(large).warning is None


def test_heavy_censoring_is_warned_about_even_at_a_large_sample():
    ex = [_ex(0.3, 10.0) for _ in range(200)] + [_ex(0.3, 10.0, censored=True) for _ in range(600)]
    rec = recommend_stop(ex)
    assert rec.warning is not None and "censored" in rec.warning


def test_recommend_stop_rejects_an_unknown_unit():
    with pytest.raises(ValueError, match="unit"):
        recommend_stop([_ex(0.3, 10.0)], unit="furlongs")


# --- diagnostics ------------------------------------------------------------


def test_fragility_check_is_denominated_in_R_not_ATR():
    """Sweeney's 0.7 threshold is in R -- multiples of planned risk -- and
    R does not exist until a stop is chosen. The same ATR excursion is
    fragile against a tight stop and healthy against a wide one, so the
    stop must be supplied rather than assumed to be 1 ATR."""
    ex = [_ex(1.4, 10.0) for _ in range(50)]  # 1.4 ATR of adverse excursion

    # Against a 2.0 ATR stop that is 0.7R -- exactly at the threshold.
    avg, warning = fragility_check(ex, planned_risk_atr=2.0)
    assert avg == pytest.approx(0.7)
    assert warning is not None and "0.7R" in warning

    # Against a 4.0 ATR stop the very same excursions are only 0.35R.
    avg, warning = fragility_check(ex, planned_risk_atr=4.0)
    assert avg == pytest.approx(0.35)
    assert warning is None, "a wider stop makes the same excursions healthy"


def test_fragility_check_rejects_a_non_positive_planned_risk():
    with pytest.raises(ValueError, match="planned_risk_atr"):
        fragility_check([_ex(0.3, 10.0)], planned_risk_atr=0.0)


def test_mfe_capture_rate_reports_the_median_and_flags_a_low_one():
    good = [_ex(0.2, 40.0, mfe=100.0, gross=60.0) for _ in range(20)]
    rate, note = mfe_capture_rate(good)
    assert rate == pytest.approx(0.6)
    assert note is None

    poor = [_ex(0.2, -5.0, mfe=100.0, gross=10.0) for _ in range(20)]
    rate, note = mfe_capture_rate(poor)
    assert rate == pytest.approx(0.1)
    assert note is not None and "35-55" in note


def test_mfe_capture_uses_gross_over_gross_so_it_measures_exit_timing():
    """A net numerator against a gross denominator would fold the cost
    structure into a number that is supposed to be about *when* the
    position closed."""
    e = _ex(0.2, outcome=-2.0, mfe=100.0, gross=10.0)  # 10 gross, 12bps costs
    assert e.mfe_capture == pytest.approx(0.10), "capture must use the gross outcome"


def test_mfe_capture_is_none_when_there_was_never_a_favourable_excursion():
    e = _ex(0.5, outcome=-50.0, mfe=0.0)
    assert e.mfe_capture is None, "no favourable excursion means no denominator"


def test_a_flat_outcome_is_neither_a_winner_nor_a_loser():
    flat = _ex(0.3, outcome=0.0)
    assert not flat.is_winner
    assert not flat.is_loser, "counting a flat outcome as a loss distorts the loser MAE"
    rec = recommend_stop([flat] + [_ex(0.3, 10.0)] * 50 + [_ex(1.5, -10.0)] * 50)
    assert rec.n_winners + rec.n_losers == 100, "the flat position must be in neither bucket"


def test_the_percentile_is_nearest_rank():
    """int(n*p) is off by one against the stated contract: on 100 samples
    it returns the 81st value for p80, so "80% stayed inside this" would
    be false by one observation."""
    ex = [_ex(float(i + 1), 10.0) for i in range(100)]  # winner MAEs 1..100
    rec = recommend_stop(ex)
    assert rec.winner_mae_p80 == pytest.approx(80.0)
    assert rec.winner_mae_p90 == pytest.approx(90.0)


def test_mae_by_outcome_reports_the_trade_off_each_stop_makes():
    """The number that exposed S6's 1.5 ATR convention: a stop is only
    worth placing where it cuts materially more losers than winners."""
    ex = [_ex(0.5, 10.0) for _ in range(100)] + [_ex(3.0, -10.0) for _ in range(100)]
    table = mae_by_outcome(ex, [0.4, 1.0, 4.0])
    assert table[0.4]["winners_cut"] == pytest.approx(1.0)
    assert table[0.4]["losers_cut"] == pytest.approx(1.0)
    assert table[1.0]["winners_cut"] == pytest.approx(0.0), "a stop above every winner cuts none"
    assert table[1.0]["losers_cut"] == pytest.approx(1.0), "and still cuts every loser"
    assert table[4.0]["losers_cut"] == pytest.approx(0.0)


def test_mae_by_outcome_excludes_censored_observations():
    ex = [_ex(0.5, 10.0) for _ in range(50)] + [_ex(0.1, 10.0, censored=True) for _ in range(500)]
    assert mae_by_outcome(ex, [0.4])[0.4]["winners_cut"] == pytest.approx(1.0)
