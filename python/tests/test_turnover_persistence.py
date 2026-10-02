"""A turnover surge's persistence, measured on constructed series.

The real measurement runs on the instance against the spent 2019+ panel. These
tests pin the arithmetic on series whose answer is known by construction, so a
sign error or a baseline mistake fails here rather than being read off 4.4
million bars as a finding.

**The property most worth protecting is the shared baseline.** Both arms divide
by the *event day's* trailing median, never by a fresh one taken at each
horizon — otherwise a name whose activity is trending up reads as "the surge
persisted" when nothing persisted at all. That is the same shape as the
`null_sd / se` rule: a comparison is only as good as what both sides are divided
by.
"""

from __future__ import annotations

import pytest

from research.turnover_persistence import (
    DEFAULT_LOOKBACK,
    measure_persistence,
    relative_turnover_series,
)


def _flat(n: int, level: float = 100.0) -> list[float]:
    return [level] * n


def test_relative_turnover_is_none_until_the_baseline_exists():
    """A name with fewer than `lookback` prior sessions has no defined normal,
    and inventing one would make its first real day look like a surge."""
    rel = relative_turnover_series(_flat(80), lookback=60)
    assert rel[:60] == [None] * 60
    assert all(v == pytest.approx(1.0) for v in rel[60:])


def test_a_baseline_of_zero_yields_none_rather_than_a_division():
    """A name that did not trade through its whole baseline window. Returning a
    number here would be a fabricated surge of infinite size."""
    series = _flat(60, 0.0) + _flat(10, 500.0)
    rel = relative_turnover_series(series, lookback=60)
    assert rel[60] is None, "zero baseline must not divide"


def test_the_median_baseline_is_not_raised_by_one_prior_surge():
    """Median, not mean, so detecting a second surge does not require it to
    clear the first one's contribution."""
    series = _flat(59, 100.0) + [10_000.0]  # one enormous day inside the window
    rel = relative_turnover_series(series + [300.0], lookback=60)
    # Baseline is the median of the 60-day window, so still ~100, and a 300 day
    # reads as 3x. Under a mean baseline it would be ~265 and read as 1.1x.
    assert rel[-1] == pytest.approx(3.0, abs=0.05)


def test_a_one_day_spike_shows_no_persistence():
    """The null case the real measurement had to rule out: if a surge evaporates
    immediately, the ratio at every horizon is ~1 and the filter cannot inform a
    held position."""
    # 60 flat days, one spike, then flat again — repeated so there are enough
    # events to take a median over.
    series = []
    for _ in range(6):
        series.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        series.append(500.0)  # 5x spike
        series.extend(_flat(100, 100.0))

    results = measure_persistence([series], horizons=(1, 5, 20))
    assert all(r.n_surges > 0 for r in results), "no surge detected at all"
    for r in results:
        assert r.ratio is not None
        assert r.ratio == pytest.approx(1.0, abs=0.1), (
            f"a one-day spike must not read as persistent at {r.horizon}d, got {r.ratio}"
        )


def test_a_sustained_elevation_shows_persistence():
    """The positive case, constructed: activity steps up and stays up, so the
    ratio stays well above 1."""
    series = []
    for _ in range(6):
        series.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        series.extend(_flat(80, 400.0))  # steps to 4x and stays

    results = measure_persistence([series], horizons=(1, 5, 20))
    for r in results:
        assert r.ratio is not None
        assert r.ratio > 2.0, (
            f"a sustained step must read as persistent at {r.horizon}d, got {r.ratio}"
        )


def test_a_rising_baseline_does_not_masquerade_as_persistence():
    """**The defect the shared baseline exists to prevent.**

    This name's turnover grows steadily and never surges. Measured against a
    *fresh* baseline at each horizon the ratio would stay near 1 — correct — but
    so would a genuinely persistent surge, since the baseline would have caught
    up. Measured against the event day's baseline, as the function does, a pure
    trend produces an ordinary arm that grows at the same rate as the surge arm,
    so their ratio stays near 1 and the trend is not mistaken for an event.
    """
    series = [100.0 * (1.01**i) for i in range(400)]  # +1% a day, no surges
    results = measure_persistence([series], horizons=(1, 20))
    for r in results:
        # With no day clearing 3x its trailing median, there is nothing to
        # report -- which is the correct answer, not a ratio near 1.
        assert r.n_surges == 0, f"a pure trend produced {r.n_surges} false surges"
        assert r.ratio is None


def test_frozen_bars_left_in_turn_ordinary_activity_into_a_fake_surge():
    """**Why excluding halts is the caller's job and not optional.**

    A halted session is zero turnover — the quietest possible day — so leaving
    them in drags the trailing median down and the name's *ordinary* activity
    afterwards reads as a surge against it. Measured here rather than asserted
    in prose, because the size of the distortion is what makes it a rule.

    A first version of this test expected `None`, reasoning that the zeros would
    make the median zero. They do not: a window half zeros has a median halfway
    up, which is worse than undefined — it is a plausible-looking wrong number.
    """
    clean = _flat(DEFAULT_LOOKBACK, 100.0) + _flat(40, 100.0)
    half_halted = (
        _flat(DEFAULT_LOOKBACK // 2, 100.0)
        + _flat(DEFAULT_LOOKBACK // 2, 0.0)
        + _flat(40, 100.0)
    )

    rel_clean = relative_turnover_series(clean, lookback=DEFAULT_LOOKBACK)
    rel_halted = relative_turnover_series(half_halted, lookback=DEFAULT_LOOKBACK)

    assert rel_clean[DEFAULT_LOOKBACK] == pytest.approx(1.0), "the control"
    assert rel_halted[DEFAULT_LOOKBACK] == pytest.approx(2.0), (
        "half a baseline window of halts halves the median, so unchanged "
        "activity reads as 2x -- a fabricated surge from a data artifact"
    )

    # Past half, the median reaches zero and the day becomes undefined rather
    # than an ever-larger surge. So the distortion is bounded in an unexpected
    # way: a *partially* halted baseline is the dangerous case, because it
    # produces a finite plausible number, while a mostly-halted one is rejected
    # outright. A first version of this comment claimed the mostly-halted
    # fixture demonstrated a 3x surge; it does not, and cannot.
    mostly_halted = (
        _flat(15, 100.0) + _flat(DEFAULT_LOOKBACK - 15, 0.0) + _flat(40, 100.0)
    )
    rel_mostly = relative_turnover_series(mostly_halted, lookback=DEFAULT_LOOKBACK)
    assert rel_mostly[DEFAULT_LOOKBACK] is None, (
        "a window whose median is exactly zero is undefined, not infinite"
    )


def test_counts_are_reported_so_a_ratio_can_be_weighed():
    """A ratio over three events is not the same evidence as a ratio over fifty
    thousand, and the real run reported 55,128 against 81,354."""
    series = []
    for _ in range(4):
        series.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        series.append(500.0)
        series.extend(_flat(30, 100.0))

    results = measure_persistence([series], horizons=(1,))
    assert results[0].n_surges >= 3
    assert results[0].n_ordinary > results[0].n_surges, (
        "ordinary days should outnumber surges in any realistic series"
    )


def test_no_horizons_returns_no_results():
    assert measure_persistence([_flat(200)], horizons=()) == []


def test_a_series_shorter_than_lookback_plus_horizon_is_skipped():
    """Silently, because a partial series is not evidence of absence — and the
    real pass skips names with fewer than 400 bars for the same reason."""
    assert measure_persistence([_flat(10)], horizons=(20,))[0].n_surges == 0


# ---------------------------------------------------------------------------
# `measure_persistence`'s own logic
#
# Found by mutation: five rules inside it survived every test above, because the
# tests exercised `relative_turnover_series` and the function had its own
# duplicate copy of the baseline. The duplication is gone; these cover what is
# left — the aggregation statistic, the comparison band, and the skip.

from research.turnover_persistence import DEFAULT_SURGE_MULTIPLE, measure_persistence as _mp


def _surge_then(level_after: float, *, repeats: int = 8, tail: int = 40) -> list[float]:
    """A series with clean 5x surges followed by a chosen activity level."""
    out: list[float] = []
    for _ in range(repeats):
        out.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        out.append(500.0)
        out.extend(_flat(tail, level_after))
    return out


def test_followups_are_summarised_by_median_not_mean():
    """Turnover is heavy-tailed — on the real panel p50 to p99.9 spans three
    orders of magnitude — so a mean follow-up is a statement about its largest
    observation. Constructed so the two differ sharply: most days return to
    normal, one is enormous.
    """
    series: list[float] = []
    for k in range(8):
        series.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        series.append(500.0)  # the surge
        # One session later: normal, except on the last repeat where it is huge.
        series.append(100_000.0 if k == 7 else 100.0)
        series.extend(_flat(40, 100.0))

    result = _mp([series], horizons=(1,))[0]
    assert result.after_surge is not None
    assert result.after_surge == pytest.approx(1.0, abs=0.1), (
        f"a median must ignore the one huge follow-up; got {result.after_surge} "
        "(a mean would be in the hundreds)"
    )


def test_the_ordinary_band_excludes_mild_surges():
    """The comparison arm must be *ordinary* days. Widening the band to admit
    2x days puts mild surges into the control and shrinks every ratio by
    construction — so the band is asserted, not just configured.

    The series needs days at *intermediate* activity for this to be visible: a
    first version alternated only between 1x and 5x, so no day fell between the
    two bands and widening changed nothing. That passed against a mutation
    widening the default, which is the gap this now closes.
    """
    series: list[float] = []
    for _ in range(8):
        series.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        series.append(500.0)                  # 5x, a surge under either band
        series.extend(_flat(10, 100.0))       # 1x, ordinary under either
        series.extend(_flat(10, 200.0))       # 2x, ordinary ONLY under the wide band
        series.extend(_flat(20, 100.0))

    narrow = _mp([series], horizons=(1,), ordinary_band=(0.8, 1.25))[0]
    wide = _mp([series], horizons=(1,), ordinary_band=(0.1, 2.9))[0]

    assert narrow.n_ordinary > 0 and wide.n_ordinary > 0
    assert wide.n_ordinary > narrow.n_ordinary, (
        f"a wider band must admit the 2x days: narrow={narrow.n_ordinary}, "
        f"wide={wide.n_ordinary}"
    )


def test_the_default_ordinary_band_is_narrower_than_a_wide_one():
    """**The band's default, not just the parameter.** The test above passes an
    explicit band both times, so a change to `DEFAULT_ORDINARY_BAND` slipped
    past it — a mutation widening the default to (0.1, 2.9) survived.

    A first attempt here tried to construct a series with *no* day inside the
    default band, which cannot be done: the baseline is the name's own trailing
    median, so a steady series sits at exactly 1.0 by construction. Comparing
    the default against an explicitly wide band works instead — if the default
    has been widened to match, the two counts coincide and this fails.
    """
    series: list[float] = []
    for _ in range(8):
        series.extend(_flat(DEFAULT_LOOKBACK, 100.0))
        series.append(500.0)             # 5x surge
        series.extend(_flat(10, 100.0))  # 1x, ordinary under any band
        series.extend(_flat(10, 200.0))  # 2x, ordinary ONLY under a wide band
        series.extend(_flat(20, 100.0))

    default = _mp([series], horizons=(1,))[0]
    wide = _mp([series], horizons=(1,), ordinary_band=(0.1, 2.9))[0]

    assert default.n_surges > 0, "the surges themselves must still be found"
    assert wide.n_ordinary > default.n_ordinary, (
        f"the default band must exclude days a wide one admits: default="
        f"{default.n_ordinary}, wide={wide.n_ordinary}. If these match, "
        "DEFAULT_ORDINARY_BAND has widened and every published ratio is diluted "
        "by mild surges sitting in the control arm."
    )


def test_the_surge_threshold_is_applied():
    """Raising it past the constructed surge size must leave no events."""
    series = _surge_then(100.0)
    assert _mp([series], horizons=(1,), surge_multiple=3.0)[0].n_surges > 0
    assert _mp([series], horizons=(1,), surge_multiple=50.0)[0].n_surges == 0, (
        f"a 5x surge must not clear a 50x threshold (default is {DEFAULT_SURGE_MULTIPLE})"
    )


def test_a_series_too_short_for_the_longest_horizon_contributes_nothing():
    """Not merely zero surges — it must not be partially counted, since a name
    present for only part of a horizon would bias that horizon's median toward
    whatever its early days did.
    """
    long_enough = _surge_then(400.0)
    too_short = _flat(DEFAULT_LOOKBACK + 5, 100.0) + [500.0] + _flat(3, 400.0)

    alone = _mp([long_enough], horizons=(20,))[0]
    together = _mp([long_enough, too_short], horizons=(20,))[0]
    assert together.n_surges == alone.n_surges, (
        "the short series contributed events it does not have the horizon for"
    )
    assert together.after_surge == alone.after_surge


def test_a_zero_baseline_inside_measure_persistence_is_skipped():
    """The same guard `relative_turnover_series` has, now shared — a name that
    did not trade through its whole baseline window has no defined normal, and
    dividing by it would fabricate an unbounded surge."""
    dead_then_active = _flat(DEFAULT_LOOKBACK, 0.0) + _flat(60, 500.0)
    result = _mp([dead_then_active], horizons=(1, 20))[0]
    assert result.n_surges == 0, "a zero baseline must not produce a surge"
    assert result.ratio is None


@pytest.mark.parametrize("horizons", [(-1,), (0,), (20, -1), (20, 0), (1.5,), (True,)])
def test_a_non_positive_horizon_is_refused(horizons):
    """**A negative horizon would index before the event**, reporting the day
    leading *into* a surge as a follow-up to it — a look-backward result
    presented as a look-forward one, which is the single worst failure mode this
    function could have. Zero would report the event day as its own follow-up,
    and a float would index by accident or not at all.

    Raised rather than silently dropped, on this project's
    fail-loud-at-the-entry-point convention: a caller who passes `(20, -1)`
    wants both numbers, and quietly returning one is worse than refusing.

    `True` is in the list because `isinstance(True, int)` is `True` in Python,
    so a bool would otherwise pass as the integer 1.
    """
    with pytest.raises(ValueError, match="horizons must be positive integers"):
        _mp([_flat(300, 100.0)], horizons=horizons)


def test_the_baselines_are_computed_once_per_series():
    """The trailing median over every day is the expensive part — 281 names of
    ~1,900 sessions on the research panel — and an earlier version computed it
    twice by calling `relative_turnover_series` as well as `_baselines`.

    Asserted by counting calls rather than by timing, which would be flaky.
    """
    import research.turnover_persistence as mod

    calls = {"n": 0}
    real = mod._baselines

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    mod._baselines = counting
    try:
        _mp([_flat(300, 100.0), _flat(300, 100.0)], horizons=(1, 20))
    finally:
        mod._baselines = real

    assert calls["n"] == 2, (
        f"two series must mean two baseline passes, got {calls['n']}"
    )
