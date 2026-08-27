"""MAE / MFE: where do stops and targets actually belong?

Scalping Strategy Research Task S8 step 5 (see
`.planning/scalp-s8-research-methodology.md` §3.7). Implements the
calculation contract pinned there, so the same trades cannot yield
different R-multiples and therefore different stop boundaries depending
on who measured them.

**Maximum Adverse Excursion** is the worst unrealised loss a position
experiences before it closes -- measured on *every* position, winners
included. **Maximum Favorable Excursion** is the best unrealised gain.
Sweeney's method (Campaign Trading, 1996): plot MAE against final
outcome across 100+ trades and the boundary appears -- winners cluster
below some adverse excursion, losers keep extending past it. That
boundary is where the stop belongs.

**Not a strategy and not a backtest.** It takes entries as given and
reports the excursion distribution. It has no stop and no target,
because the whole point is to derive them: positions run to a fixed
maximum holding period, which is itself a legitimate time-based exit, so
the outcome there is real. **Censoring means the data ran out first** --
those observations are truncated and are excluded from anything that
judges an outcome. An earlier version had this backwards, which made
every position censored and the analysis vacuous.

The contract, restated because it is the reason this module exists
rather than a few lines inline:

- **Measurement starts at the fill bar**, not the signal bar --
  `backtest.fill.simulate_fill` fills at `signal_bar_index + 1`, so the
  signal bar is excluded and excursion is measured from the real fill.
- **Excursions are net of costs.** `cost_bps` is charged against entry so
  the numbers compare directly against the reported P&L basis.
- **Intrabar path uses bar high/low**, the only intrabar information a
  1m bar carries. This is an approximation and it makes MAE a *lower*
  bound on the true worst excursion -- disclosed, never treated as exact.
- **R is the planned risk at entry**, fixed there and never re-based, so
  R-multiples stay comparable across positions.
- **Censored positions are flagged** -- meaning the dataset ended before
  the intended holding period -- and their MFE capture rate is
  meaningless by construction.

Excursions are reported in **ATR units** as well as bps. ATR units are
what makes a stop boundary transferable: a 40bps stop means something
different in a quiet hour than a violent one, while "0.8 ATR" does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Literal, Sequence

Side = Literal["long", "short"]


@dataclass(frozen=True)
class Excursion:
    """One position's excursion record.

    `mae_bps`/`mfe_bps` are always non-negative magnitudes: MAE is how far
    price went *against* the position, MFE how far *for* it, regardless of
    side. `outcome_bps` is signed and net of `cost_bps`.

    `mae_atr`/`mfe_atr` are the same quantities divided by the ATR at
    entry, which is the unit a transferable stop is expressed in.

    `censored` means the observation was **cut short by the end of the
    data**, so what the position would have done is unknown. Reaching
    `max_hold` is *not* censoring: a fixed holding period is a legitimate
    time-based exit and the outcome at that point is real. Getting this
    backwards makes every position censored and the whole analysis
    vacuous, which is exactly what happened on the first run.
    """

    index: int
    side: Side
    entry_price: float
    mae_bps: float
    mfe_bps: float
    outcome_bps: float
    mae_atr: float | None
    mfe_atr: float | None
    censored: bool

    @property
    def is_winner(self) -> bool:
        return self.outcome_bps > 0

    @property
    def mfe_capture(self) -> float | None:
        """Realised gain as a fraction of the best it ever showed.
        `None` for a censored position (no real exit to judge) and for a
        position that never showed any favourable excursion (no
        denominator)."""
        if self.censored or self.mfe_bps <= 0:
            return None
        return self.outcome_bps / self.mfe_bps


def measure_excursion(
    index: int,
    side: Side,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    max_hold: int,
    cost_bps: float = 0.0,
    atr: float | None = None,
) -> Excursion | None:
    """Track one position opened at the bar *after* `index`.

    Returns `None` when there is not enough data left to fill the entry
    and observe at least one bar, rather than silently reporting a
    zero-length position.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if max_hold <= 0:
        raise ValueError(f"max_hold must be positive, got {max_hold}")

    fill = index + 1  # contract: fill bar, not signal bar
    if fill >= len(closes) - 1:
        return None
    entry = closes[fill]
    if entry <= 0:
        return None

    intended_last = fill + max_hold
    last = min(intended_last, len(closes) - 1)
    worst = best = 0.0
    for i in range(fill, last + 1):
        if side == "long":
            adverse = (entry - lows[i]) / entry * 1e4
            favorable = (highs[i] - entry) / entry * 1e4
        else:
            adverse = (highs[i] - entry) / entry * 1e4
            favorable = (entry - lows[i]) / entry * 1e4
        worst = max(worst, adverse)
        best = max(best, favorable)

    raw = (closes[last] - entry) / entry * 1e4
    outcome = (raw if side == "long" else -raw) - cost_bps

    atr_bps = None if atr is None or atr <= 0 else atr / entry * 1e4
    return Excursion(
        index=index,
        side=side,
        entry_price=entry,
        mae_bps=worst,
        mfe_bps=best,
        outcome_bps=outcome,
        mae_atr=None if atr_bps is None else worst / atr_bps,
        mfe_atr=None if atr_bps is None else best / atr_bps,
        # Censored means the data ran out before the intended holding
        # period elapsed -- the outcome is unknown, not merely time-based.
        censored=(last < intended_last),
    )


@dataclass(frozen=True)
class StopRecommendation:
    """Where the data says the stop belongs, and how much it costs.

    `winner_mae_pXX` is the excursion that `XX`% of *winning* positions
    stayed inside. A stop placed there would have survived that share of
    the winners -- which is the number to choose against, since a stop
    tighter than most winners' MAE converts winners into losses.

    `losers_cut` is the share of losing positions whose MAE reached the
    recommended stop, i.e. how many losses would have been truncated.
    A recommendation only earns its place when it cuts materially more
    losers than winners.
    """

    n: int
    n_winners: int
    n_losers: int
    winner_mae_p80: float | None
    winner_mae_p90: float | None
    loser_mae_median: float | None
    losers_cut_at_p80: float | None
    warning: str | None


def _pct(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(int(len(s) * p), len(s) - 1)]


def recommend_stop(excursions: Sequence[Excursion], unit: str = "atr") -> StopRecommendation:
    """Sweeney's boundary, computed rather than eyeballed.

    `unit` selects `mae_atr` (default, transferable) or `mae_bps`.

    **Sample-size warning is part of the output, not a footnote.** The
    practitioner convention is 50+ positions per setup for the clustering
    to mean anything and 100+ for a clean read; below that this returns a
    recommendation *with* a warning attached rather than pretending the
    number is solid.

    Censored positions are **excluded from the winner/loser split**:
    their outcome was truncated by the end of the data, so counting one
    as a winner or loser would place the stop against an observation that
    never finished.
    """
    if unit not in ("atr", "bps"):
        raise ValueError(f"unit must be 'atr' or 'bps', got {unit!r}")

    def mae(e: Excursion) -> float | None:
        return e.mae_atr if unit == "atr" else e.mae_bps

    usable = [e for e in excursions if not e.censored and mae(e) is not None]
    winners = [mae(e) for e in usable if e.is_winner]
    losers = [mae(e) for e in usable if not e.is_winner]

    p80 = _pct(winners, 0.80)
    p90 = _pct(winners, 0.90)
    cut = None
    if p80 is not None and losers:
        cut = sum(1 for m in losers if m >= p80) / len(losers)

    warning = None
    if len(usable) < 50:
        warning = f"only {len(usable)} uncensored positions; 50+ needed for MAE clustering to mean anything"
    elif len(usable) < 100:
        warning = f"{len(usable)} uncensored positions; 100+ gives a materially cleaner read"
    if usable and len(usable) < len(excursions) * 0.5:
        extra = f"{len(excursions) - len(usable)} of {len(excursions)} positions were censored"
        warning = f"{warning}; {extra}" if warning else extra

    return StopRecommendation(
        n=len(usable),
        n_winners=len(winners),
        n_losers=len(losers),
        winner_mae_p80=p80,
        winner_mae_p90=p90,
        loser_mae_median=median(losers) if losers else None,
        losers_cut_at_p80=cut,
        warning=warning,
    )


def fragility_check(excursions: Sequence[Excursion], unit: str = "atr") -> tuple[float | None, str | None]:
    """Sweeney's structural-fragility diagnostic.

    Winners whose MAE routinely reaches deep into the planned risk were
    rescue trades that nearly failed. A small worsening of conditions
    converts them into losses, so a strategy that depends on them is
    fragile even when its headline win rate looks fine.

    Returns `(mean winner MAE, warning)`. The 0.7R threshold is Sweeney's
    published convention, not a number fitted here.
    """
    def mae(e: Excursion) -> float | None:
        return e.mae_atr if unit == "atr" else e.mae_bps

    winners = [mae(e) for e in excursions if e.is_winner and not e.censored and mae(e) is not None]
    if not winners:
        return None, None
    avg = sum(winners) / len(winners)
    warning = None
    if avg >= 0.7:
        warning = (
            f"mean winning-position MAE is {avg:.2f} -- at or above Sweeney's 0.7 fragility "
            "threshold, so many 'winners' are rescue trades that nearly failed"
        )
    return avg, warning


def mfe_capture_rate(excursions: Sequence[Excursion]) -> tuple[float | None, str | None]:
    """Median share of the best unrealised gain that was actually kept.

    Practitioner reference: 35-55% is typical retail, above 0.5 is
    healthy. Censored positions are excluded -- their outcome was cut off
    by the end of the data, so their capture rate would measure the
    dataset boundary rather than the exit.

    Under a pure time-based exit this measures how much of the best
    excursion survived to the holding limit, which is precisely the
    number that says whether a target would have been worth adding.
    """
    rates = [e.mfe_capture for e in excursions if e.mfe_capture is not None]
    if not rates:
        return None, None
    m = median(rates)
    note = None
    if m < 0.35:
        note = f"median MFE capture {m:.2f} is below the 35-55% typical band -- exits are early or late"
    return m, note
