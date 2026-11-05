"""Independent reconstruction of saved EF features, quotes and six labels.

This code starts from independently decoded archived minutes. It never calls
the EF producer, its input or label projection, or its tensor transforms.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import log1p
from statistics import median

from research.intraday_chart_tensors import ChartTensor, PeerTensor
from research.intraday_ed_inputs import MINUTE, SessionInputs


OHLC = tuple[Fraction, Fraction, Fraction, Fraction]


@dataclass(frozen=True)
class RawDay:
    opening: datetime
    closing: datetime
    prices: tuple[OHLC | None, ...]
    volumes: tuple[Fraction | None, ...]
    price_issues: tuple[str | None, ...]
    volume_issues: tuple[str | None, ...]


def original_day(session: SessionInputs) -> RawDay:
    """Check the registered minute-end-plus-two observation assumption."""
    length = (session.closing - session.opening) // MINUTE
    prices, volumes = [None] * length, [None] * length
    price_issues, volume_issues = ["missing_minute"] * length, ["missing_minute"] * length
    for at, minute in session.minutes.items():
        index = (at - session.opening) // MINUTE
        if (not 0 <= index < length or session.opening + index * MINUTE != at
                or minute.start_at != at or minute.available_at != at + 3 * MINUTE
                or minute.symbol != session.symbol):
            raise ValueError("independent EF observation clock or identity differs")
        if minute.price_state == "observed":
            prices[index] = tuple(map(Fraction, minute.ohlc))
        price_issues[index] = None if minute.price_state == "observed" else f"price_{minute.price_state}"
        if minute.volume_state == "observed":
            volumes[index] = Fraction(minute.shares)
        volume_issues[index] = None if minute.volume_state == "observed" else f"volume_{minute.volume_state}"
    return RawDay(session.opening, session.closing, tuple(prices), tuple(volumes), tuple(price_issues), tuple(volume_issues))


def _extrema(prices: tuple[OHLC, ...]) -> OHLC:
    return prices[0][0], max(p[1] for p in prices), min(p[2] for p in prices), prices[-1][3]


def previous_levels(prior: RawDay | None) -> tuple[Fraction, Fraction, Fraction] | None:
    if prior is None or not prior.prices or None in prior.prices:
        return None
    _, high, low, close = _extrema(prior.prices)
    return high, low, close


def chart_at(day: RawDay, offset: int, prior: tuple[Fraction, Fraction, Fraction] | None) -> ChartTensor | None:
    if offset > 315 or offset > len(day.prices):
        return None
    prices = day.prices[offset - 60:offset]
    if None in prices:
        return None
    volumes = day.volumes[offset - 60:offset]
    observed_positive = sorted(v for v in volumes if v is not None and v > 0)
    normal = median(observed_positive) if observed_positive else Fraction(1)
    anchor, close = prices[0][0], prices[-1][3]
    minute = []
    for (op, hi, lo, cl), volume in zip(prices, volumes, strict=True):
        width = hi - lo
        minute.append((float(100 * (op - anchor) / anchor), float(100 * (hi - anchor) / anchor),
                       float(100 * (lo - anchor) / anchor), float(100 * (cl - anchor) / anchor),
                       float(100 * (cl - op) / op), float(100 * width / op),
                       float((cl - lo) / width) if width else 0.0, float(not width),
                       log1p(float(volume / normal)) if volume is not None else 0.0,
                       float(volume is not None)))
    coarse = []
    for start in range(offset - 120, offset, 5):
        window = day.prices[start:start + 5] if start >= 0 else ()
        if not window or None in window:
            coarse.append((0.0, 0.0, 0.0, 0.0, 0.0, float(start < 0)))
        else:
            coarse.append((*tuple(float(100 * (p - close) / close) for p in _extrema(window)), 1.0, 0.0))
    levels = tuple(float(100 * (p - close) / close) for p in prior) if prior else (0.0, 0.0, 0.0)
    return ChartTensor(tuple(minute), tuple(coarse), (float(Fraction(offset, 390)), *levels, float(prior is not None)))


def peer_at(days: dict[str, RawDay], roster: tuple[str, ...], offset: int) -> dict[str, tuple[PeerTensor | None, tuple[str, ...]]]:
    paths = {}
    for symbol in roster:
        history = days[symbol].prices[offset - 60:offset]
        if len(history) == 60 and None not in history:
            anchor = history[0][0]
            paths[symbol] = tuple(((close - op) / op, (close - anchor) / anchor, int(close > anchor))
                                  for op, _, _, close in history)
    if len(paths) >= 5:
        totals = tuple(tuple(sum(column) for column in zip(*items, strict=True))
                       for items in zip(*paths.values(), strict=True))
    else:
        totals = ()
    peers = {}
    for symbol in roster:
        used = tuple(peer for peer in paths if peer != symbol)
        n = len(used)
        tensor = None
        if n >= 5:
            own = paths.get(symbol, ((Fraction(), Fraction(), 0),) * 60)
            tensor = PeerTensor(tuple((float(100 * (t[0] - x[0]) / n), float(100 * (t[1] - x[1]) / n),
                                       float(Fraction(t[2] - x[2], n)))
                                      for t, x in zip(totals, own, strict=True)), float(Fraction(n, len(roster) - 1)))
        peers[symbol] = tensor, used
    return peers


def quote_at(day: RawDay, offset: int) -> tuple[Fraction | None, str]:
    start = offset + 3
    if start >= len(day.prices) - 15:
        return None, "outside_clock"
    row = day.prices[start]
    return (None, "unavailable") if row is None else (row[0], "available")


def labels_at(day: RawDay, offset: int) -> tuple[tuple[Fraction, datetime] | None, ...]:
    start = offset + 3
    results = {}
    for horizon in (15, 30):
        end = start + horizon
        point = low = high = None
        if end < len(day.prices) - 15:
            entry, exit = day.prices[start], day.prices[end]
            if entry is not None and exit is not None:
                maturity = day.opening + (end + 3) * MINUTE
                point = (exit[0] - entry[0]) / entry[0], maturity
                held = day.prices[start:end]
                if None not in held:
                    _, top, bottom, _ = _extrema(held)
                    low = (min(bottom, exit[0]) - entry[0]) / entry[0], maturity
                    high = (max(top, exit[0]) - entry[0]) / entry[0], maturity
        results[horizon] = point, low, high
    return results[30][0], *results[15], results[30][1], results[30][2]
