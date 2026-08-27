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

- **Entry is the OPEN of the fill bar.** `backtest.fill.simulate_fill`
  fills at `next_bar.open` (`signal_bar_index + 1`), so the signal bar is
  excluded entirely and the whole fill bar happens *after* entry. Using
  that bar's close instead would let price movement from before the entry
  count as excursion -- same-candle contamination, and an earlier version
  of this module had exactly that bug.
- **Excursions are GROSS price movements; only the outcome is net.**
  This deliberately amends S8 3.7's contract, which said excursions were
  net. The reason: a stop order is placed on *price*, and triggers when
  price moves against the position regardless of fees. Deducting half a
  round trip from MAE would systematically misplace every stop derived
  from it. Win/loss classification still uses the net outcome, because
  that is what actually determines whether a position made money.
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
from bisect import bisect_left
from collections import deque
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
    outcome_gross_bps: float
    mae_atr: float | None
    mfe_atr: float | None
    censored: bool

    @property
    def is_winner(self) -> bool:
        """Net of costs -- a position that made 5bps of price and paid
        12bps of fees did not win."""
        return self.outcome_bps > 0

    @property
    def is_loser(self) -> bool:
        """Strictly negative. A flat outcome is neither, and counting it
        as a loss would distort the loser MAE distribution the stop
        boundary is chosen against."""
        return self.outcome_bps < 0

    @property
    def mfe_capture(self) -> float | None:
        """Share of the best excursion that survived to the exit.

        **Gross over gross**, deliberately: this measures *exit timing*,
        so mixing a net numerator with a gross denominator would fold the
        cost structure into a number that is supposed to be about when
        the position was closed.

        `None` for a censored observation (truncated by the end of the
        data, so there is no real exit to judge) and when there was no
        favourable excursion at all (no denominator).
        """
        if self.censored or self.mfe_bps <= 0:
            return None
        return self.outcome_gross_bps / self.mfe_bps

    def mae_r(self, planned_risk_atr: float) -> float | None:
        """MAE expressed in R -- multiples of the planned risk at entry.

        R is what Sweeney's thresholds are denominated in, and it does not
        exist until a stop has been chosen. It is therefore supplied by
        the caller rather than assumed: an earlier version of this module
        compared ATR units against an R threshold directly, which silently
        assumed 1R = 1 ATR and invalidated the comparison.
        """
        if self.mae_atr is None or planned_risk_atr <= 0:
            return None
        return self.mae_atr / planned_risk_atr


def measure_excursion(
    index: int,
    side: Side,
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    max_hold: int,
    cost_bps: float = 0.0,
    atr: float | None = None,
) -> Excursion | None:
    """Track one position opened at the **open of the bar after** `index`.

    That entry point is `backtest.fill.simulate_fill`'s real contract
    (`next_bar.open`), which is what makes it safe to include the fill
    bar's own high/low in the excursion: the entire bar happens after the
    open. Entering at that bar's *close* instead would count pre-entry
    movement as excursion.

    Returns `None` when there is not enough data left to fill the entry
    and observe it, rather than silently reporting a zero-length position.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if max_hold <= 0:
        raise ValueError(f"max_hold must be positive, got {max_hold}")
    if not len(opens) == len(highs) == len(lows) == len(closes):
        raise ValueError(
            "opens, highs, lows and closes must be the same length, got "
            f"{len(opens)}, {len(highs)}, {len(lows)} and {len(closes)}"
        )

    fill = index + 1  # contract: the bar AFTER the signal
    if fill >= len(closes):
        return None
    entry = opens[fill]
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
    gross = raw if side == "long" else -raw

    atr_bps = None if atr is None or atr <= 0 else atr / entry * 1e4
    return Excursion(
        index=index,
        side=side,
        entry_price=entry,
        mae_bps=worst,
        mfe_bps=best,
        outcome_bps=gross - cost_bps,
        outcome_gross_bps=gross,
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
    """Nearest-rank percentile: `ceil(n * p) - 1`.

    Not `int(n * p)`, which is off by one against the stated contract --
    on 100 samples it returns the 81st value for p80, so "80% of winners
    stayed inside this" would have been false by one observation.
    """
    if not values:
        return None
    s = sorted(values)
    return s[max(0, min(math.ceil(len(s) * p) - 1, len(s) - 1))]


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
    losers = [mae(e) for e in usable if e.is_loser]

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


def fragility_check(
    excursions: Sequence[Excursion],
    planned_risk_atr: float,
) -> tuple[float | None, str | None]:
    """Sweeney's structural-fragility diagnostic, in R.

    Winners whose MAE routinely reaches deep into the planned risk were
    rescue trades that nearly failed. A small worsening of conditions
    converts them into losses, so a strategy resting on them is fragile
    even when its headline win rate looks fine.

    **`planned_risk_atr` is required, not optional.** Sweeney's 0.7
    threshold is denominated in R -- multiples of the risk planned at
    entry -- and R does not exist until a stop has been chosen. An
    earlier version of this function compared raw ATR (or worse, raw bps)
    against 0.7 directly, which silently assumed 1R = 1 ATR and made the
    `bps` case warn essentially always. Supplying the candidate stop
    makes the comparison mean what it says.

    Returns `(mean winning-position MAE in R, warning)`.
    """
    if planned_risk_atr <= 0:
        raise ValueError(f"planned_risk_atr must be positive, got {planned_risk_atr}")
    winners = [
        e.mae_r(planned_risk_atr)
        for e in excursions
        if e.is_winner and not e.censored
    ]
    winners = [w for w in winners if w is not None]
    if not winners:
        return None, None
    avg = sum(winners) / len(winners)
    warning = None
    if avg >= 0.7:
        warning = (
            f"mean winning-position MAE is {avg:.2f}R against a {planned_risk_atr:.2f} ATR "
            "stop -- at or above Sweeney's 0.7R fragility threshold, so many 'winners' "
            "are rescue trades that nearly failed"
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


def trailing_percentile_rank(
    values: Sequence[float | None],
    history: int,
    refresh_every: int = 60,
) -> list[float | None]:
    """Each value's percentile rank within the `history` values *before*
    it -- the look-ahead-safe way to ask "is this bar in the top 10%".

    A global percentile over the whole series is **look-ahead**: it filters
    early bars using the distribution of bars that had not happened yet.
    That is fine for asking whether a quantity *separates* at all, and it
    is not fine for generating the positions whose statistics get
    reported, because a live system could never have selected them. An
    earlier version of the Task S12 runner made exactly that mistake.

    Mirrors `regime_classifier.AbsoluteAtr`'s approach, including the
    scheduled re-sort: rebuilding the sorted reference every bar is
    prohibitive over millions of bars, and a stale snapshot only makes a
    rank slightly out of date -- never clairvoyant -- because it is always
    built from strictly earlier values.

    `None` until `history` values have accumulated, and `None` passes
    through as `None` rather than being imputed.
    """
    if history <= 0:
        raise ValueError(f"history must be positive, got {history}")
    if refresh_every <= 0:
        raise ValueError(f"refresh_every must be positive, got {refresh_every}")

    out: list[float | None] = [None] * len(values)
    window: deque[float] = deque(maxlen=history)
    snapshot: list[float] = []
    since = 0
    for i, v in enumerate(values):
        if v is None:
            continue
        if len(window) == history:
            if not snapshot or since >= refresh_every:
                snapshot = sorted(window)
                since = 0
            since += 1
            out[i] = bisect_left(snapshot, v) / len(snapshot)
        window.append(v)
    return out


def mae_by_outcome(
    excursions: Sequence[Excursion],
    stops_atr: Sequence[float],
    unit: str = "atr",
) -> dict[float, dict[str, float]]:
    """For each candidate stop, what share of winners and of losers it
    would have cut.

    This is the trade-off a stop choice actually makes, and it is the
    number that exposed S6's convention: a stop is only worth placing
    where it cuts materially more losers than winners.

    Censored observations are excluded, for the same reason
    `recommend_stop` excludes them -- their outcome was truncated by the
    end of the data, so classifying them as winner or loser would judge
    an observation that never finished.
    """
    if unit not in ("atr", "bps"):
        raise ValueError(f"unit must be 'atr' or 'bps', got {unit!r}")

    def mae(e: Excursion) -> float | None:
        return e.mae_atr if unit == "atr" else e.mae_bps

    usable = [e for e in excursions if not e.censored and mae(e) is not None]
    winners = [mae(e) for e in usable if e.is_winner]
    losers = [mae(e) for e in usable if e.is_loser]
    out: dict[float, dict[str, float]] = {}
    for stop in stops_atr:
        out[stop] = {
            "winners_cut": (sum(1 for m in winners if m >= stop) / len(winners)) if winners else 0.0,
            "losers_cut": (sum(1 for m in losers if m >= stop) / len(losers)) if losers else 0.0,
        }
    return out
