"""Does a turnover surge persist long enough for a months-long holding period?

**The tension this settles, and why it had to be settled before anything else.**
`rd-c` named the selection filter as the axis this project never searched, citing
four sources that do not cite each other — among them Zarattini's "Stocks in
Play", which gets Sharpe 2.81 from a plain opening-range breakout **restricted to
abnormally active names**, where the same entry rule fails entirely with no
filter. But `rd-z` then measured the reserved window's cost ceiling, and it
admits only **months-long holding**: at 24 round trips a year the Korean 33bp
round trip costs more than a 0.5-Sharpe strategy's entire gross return.

So the literature's implementation is intraday and the window's economics forbid
intraday. The filter *idea* can still transfer — but only if the condition it
selects on **lasts**. A one-day surge that mean-reverts by the next session
cannot inform a position held for two months, and a filter whose condition has
evaporated by the time the position is opened is selecting on noise.

**This measures persistence and looks at no returns.** It therefore selects
nothing, and is a data-characteristic measurement in the same category as
`rd-y`'s coverage report rather than a discovery trial. The moment a return
enters, that changes — see `CLAUDE.md`'s Discovery guards.

Measured result on the spent 2019+ panel (281 codes, every tenth completed name,
55,128 surge days against 81,354 ordinary days): relative turnover after a surge
runs **4.41x** an ordinary day's at one session, **2.95x** at five, **1.97x at
twenty**, and **1.54x at sixty**. The condition survives a month at roughly
double, which is what makes the axis compatible with the window.

**Persistence is not profitability.** "Still active a month later" says nothing
about returns, and nothing here should be read as evidence of an edge.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

#: A surge, as a multiple of the name's own trailing median turnover. 3.0 rather
#: than a percentile because a multiple is comparable across names and eras,
#: where a percentile silently redefines itself as the pool's activity drifts
#: (`rd-x` measured that drift at +54%/yr on one universe).
DEFAULT_SURGE_MULTIPLE = 3.0
#: The trailing window the baseline is taken over. Median, not mean, so one
#: prior surge does not raise the bar for detecting the next.
DEFAULT_LOOKBACK = 60
#: An "ordinary day" band for the comparison arm. Deliberately narrow and
#: centred on 1.0: a wide band would include mild surges and shrink the contrast
#: by construction.
DEFAULT_ORDINARY_BAND = (0.8, 1.25)


@dataclass(frozen=True)
class PersistenceResult:
    horizon: int
    after_surge: float | None
    after_ordinary: float | None
    n_surges: int
    n_ordinary: int

    @property
    def ratio(self) -> float | None:
        """How much more active a surged name is than an ordinary one, later.

        **1.0 means the two arms' follow-up medians are equal** — the surged
        names are no more active than ordinary ones, so the surge carries no
        information at that horizon. It does not mean "the surge evaporated" in
        any absolute sense; both arms could have risen together.

        **The two arms share a normalisation, which is what makes the ratio
        comparable, and that is a weaker claim than cancelling a trend.** A
        name whose activity is trending up contributes a raised follow-up to
        whichever arm it lands in, so a trend common to both arms largely
        divides out. A trend that differs *between* the arms does not, and
        nothing here corrects for that.
        """
        if self.after_surge is None or not self.after_ordinary:
            return None
        return self.after_surge / self.after_ordinary


def _baselines(
    turnovers: Sequence[float], *, lookback: int = DEFAULT_LOOKBACK
) -> list[float | None]:
    """The trailing median before each day, or `None` where it is unusable.

    Separated from `relative_turnover_series` because `measure_persistence`
    needs the baseline itself — every horizon is divided by the *event day's*
    baseline, so it cannot work from ratios alone. Both callers therefore share
    one definition of "normal" rather than each computing its own.
    """
    out: list[float | None] = [None] * min(lookback, len(turnovers))
    for i in range(lookback, len(turnovers)):
        base = statistics.median(turnovers[i - lookback : i])
        out.append(None if base <= 0 else base)
    return out


def relative_turnover_series(
    turnovers: Sequence[float], *, lookback: int = DEFAULT_LOOKBACK
) -> list[float | None]:
    """Each day's turnover as a multiple of the trailing median before it.

    `None` for the first `lookback` days, and for any day whose trailing median
    is zero — a name that did not trade through its whole baseline window has no
    defined "normal", and inventing one would make every later day look like a
    surge.
    """
    bases = _baselines(turnovers, lookback=lookback)
    return [
        None if base is None else turnovers[i] / base for i, base in enumerate(bases)
    ]


def measure_persistence(
    series: Iterable[Sequence[float]],
    *,
    horizons: Sequence[int],
    lookback: int = DEFAULT_LOOKBACK,
    surge_multiple: float = DEFAULT_SURGE_MULTIPLE,
    ordinary_band: tuple[float, float] = DEFAULT_ORDINARY_BAND,
) -> list[PersistenceResult]:
    """Relative turnover at each horizon after a surge, and after an ordinary day.

    `series` is one turnover sequence per name, **already excluding frozen
    bars** — a halted session has zero turnover and would be counted as the
    quietest possible day, dragging every baseline down. Excluding it is
    `CLAUDE.md`'s standing rule, not a choice this function makes.

    Medians rather than means throughout: turnover is heavy-tailed enough that a
    mean is a statement about its largest observation. Measured on the real panel,
    the gap between p50 and p99.9 is three orders of magnitude.
    """
    if not horizons:
        return []
    # **Positive only.** A negative horizon would index *before* the event and
    # report the day leading into a surge as a follow-up to it -- a
    # look-backward result presented as a look-forward one. A zero horizon would
    # report the event day as its own follow-up. Neither is a plausible typo to
    # leave working. Raised rather than silently dropped, on this project's
    # fail-loud-at-the-entry-point convention.
    bad = [h for h in horizons if not isinstance(h, int) or isinstance(h, bool) or h < 1]
    if bad:
        raise ValueError(f"horizons must be positive integers, got {bad}")
    longest = max(horizons)
    surge: dict[int, list[float]] = {h: [] for h in horizons}
    ordinary: dict[int, list[float]] = {h: [] for h in horizons}
    lo, hi = ordinary_band

    for turnovers in series:
        # An optimisation, not a correctness guard: without it the loop below
        # would get an empty `range` for such a series and contribute nothing
        # anyway. Kept because the baseline pass over a long series is the
        # expensive part, and skipping it early avoids that work. Mutating this
        # line away changes no result, which is why no test asserts it.
        if len(turnovers) < lookback + longest + 1:
            continue
        # **One baseline definition, delegated.** An earlier version recomputed
        # the trailing median inline here, so the median-vs-mean choice and the
        # zero-baseline guard each existed twice and only one copy was tested —
        # mutating either inline copy left the suite green. Same defect this
        # project recorded in S16 and again on PR #220.
        # One pass for the baselines, then the ratios derived from them. An
        # earlier version called `relative_turnover_series` *and* `_baselines`,
        # which took the trailing median over every day twice -- 281 names of
        # ~1,900 sessions each on the research panel, so the waste is real.
        baselines = _baselines(turnovers, lookback=lookback)
        relative = [
            None if base is None else turnovers[i] / base
            for i, base in enumerate(baselines)
        ]

        for i in range(lookback, len(turnovers) - longest):
            rel = relative[i]
            base = baselines[i]
            if rel is None or base is None:
                continue
            # **Divided by the EVENT DAY's baseline, and the reason is the
            # opposite of what it first looks like.** A fresh baseline at each
            # horizon would absorb any rise into its own denominator, pulling
            # every follow-up back toward 1.0 and *understating* persistence --
            # a name that stayed genuinely elevated would read as having faded,
            # because its new normal is the elevated level. Holding the event
            # day's baseline fixed measures activity against what was normal
            # *before* the surge, which is the question being asked.
            followups = [(h, turnovers[i + h] / base) for h in horizons]
            if rel >= surge_multiple:
                for h, v in followups:
                    surge[h].append(v)
            elif lo <= rel <= hi:
                for h, v in followups:
                    ordinary[h].append(v)

    return [
        PersistenceResult(
            horizon=h,
            after_surge=statistics.median(surge[h]) if surge[h] else None,
            after_ordinary=statistics.median(ordinary[h]) if ordinary[h] else None,
            n_surges=len(surge[h]),
            n_ordinary=len(ordinary[h]),
        )
        for h in horizons
    ]
