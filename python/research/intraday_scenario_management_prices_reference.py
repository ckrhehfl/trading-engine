"""Independent raw-price readback for the recorded EH held payoff paths."""

from collections.abc import Callable
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN
from fractions import Fraction
from functools import lru_cache

from research.intraday_campaign_readiness import KST
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _require
from research.intraday_scenario_management_payoff_reference import PayoffPath, score_held_payoffs
from research.intraday_scenario_open_reference import _price_row
from research.intraday_scenario_policy import Values


_LAG = 3 * MINUTE
ReadSession = Callable[[str, str], SessionInputs | None]
ReadBar = Callable[[str, str, datetime], Minute | None]


def audited_bar_reader(read_session: ReadSession) -> ReadBar:
    """Reuse raw validation across decisions over the same authenticated immutable input."""
    @lru_cache(maxsize=32768)
    def read(day: str, symbol: str, at: datetime) -> Minute | None:
        session = read_session(day, symbol)
        _require(session is not None and session.symbol == symbol and session.opening <= at < session.closing
                 and session.opening.astimezone(KST).date().isoformat() == day,
                 "eh_payoff_reference_source_scope_differs")
        raw = session.minutes.get(at)
        if raw is None:
            return None
        _require(at + _LAG <= raw.available_at, "eh_payoff_reference_source_publication_differs")
        _price_row(session, at, raw.available_at)
        return raw
    return read


def _path(session: SessionInputs, source_day: str, source_at: datetime, *, at: datetime, cap: datetime, cutoff: datetime,
          visible_mark: Fraction, weight: Fraction, read_bar: ReadBar) -> PayoffPath:
    anchor_at = source_at - _LAG
    shift = source_at - at
    _require(session.opening <= anchor_at < source_at < session.closing
             and cap + shift + _LAG < cutoff, "eh_payoff_reference_source_clock_differs")
    anchor = read_bar(source_day, session.symbol, anchor_at)
    _require(anchor is not None and anchor.ohlc is not None and anchor.available_at == source_at,
             "eh_payoff_reference_source_mark_missing")
    ratio = visible_mark / Fraction(anchor.ohlc[3])
    context = Context(prec=28, rounding=ROUND_HALF_EVEN)

    @lru_cache(maxsize=128)
    def scaled(price: Decimal) -> Fraction:
        exact = Fraction(price) * ratio
        return Fraction(context.divide(Decimal(exact.numerator), Decimal(exact.denominator)))

    @lru_cache(maxsize=34)
    def original(query_at: datetime):
        source_clock = query_at + shift
        if not anchor_at <= source_clock < session.closing:
            return None
        row = read_bar(source_day, session.symbol, source_clock)
        _require(row is None or row.available_at < cutoff, "eh_payoff_reference_source_publication_differs")
        return row

    def close_at(bar_start: datetime, decision_cutoff: datetime) -> Fraction | None:
        row = original(bar_start)
        return scaled(row.ohlc[3]) if row is not None and row.ohlc is not None and row.available_at <= decision_cutoff + shift else None

    def open_at(execute_at: datetime) -> Fraction | None:
        row = original(execute_at)
        return scaled(row.ohlc[0]) if row is not None and row.ohlc is not None and row.available_at == execute_at + shift + _LAG else None

    return PayoffPath(weight, close_at, open_at)


def reconstruct_held_values(
    *, day: str, symbol: str, at: datetime, cap: datetime, cutoff: datetime, entered_at: datetime,
    quantity: int, half_reduced: bool, stop: Fraction, downside_penalty: Fraction,
    sources: tuple[tuple[str, str, datetime], ...], read_session: ReadSession, read_bar: ReadBar,
) -> tuple[str, Values | None]:
    """Use authenticated sessions, reading only the visible query mark and used prior prices."""
    query = read_session(day, symbol)
    _require(query is not None and query.symbol == symbol and query.opening.astimezone(KST).date().isoformat() == day
             and query.opening <= at - _LAG < at < cap < query.closing and bool(sources),
             "eh_payoff_reference_query_scope_differs")
    mark, issue = _price_row(query, at - _LAG, at)
    _require(issue is None and mark.available_at == at, "eh_payoff_reference_query_mark_missing")
    visible_mark = Fraction(mark.ohlc[3])
    paths = []
    weight = Fraction(1, len(sources))
    for source_day, source_symbol, source_at in sources:
        _require(source_at.astimezone(KST).date().isoformat() == source_day
                 and source_at < cutoff <= at and source_day < day, "eh_payoff_reference_future_source")
        source = read_session(source_day, source_symbol)
        if source is None:
            return "selected_prior_session_missing", None
        _require(source.symbol == source_symbol and source.opening.astimezone(KST).date().isoformat() == source_day
                 and source.closing <= cutoff, "eh_payoff_reference_source_scope_differs")
        paths.append(_path(source, source_day, source_at, at=at, cap=cap, cutoff=cutoff,
                           visible_mark=visible_mark, weight=weight, read_bar=read_bar))
    values = score_held_payoffs(tuple(paths), at=at, cap=cap, entered_at=entered_at, quantity=quantity,
                               half_reduced=half_reduced, stop=stop, visible_mark=visible_mark,
                               downside_penalty=downside_penalty)
    return ("ready" if values is not None else "unresolved_path_mass"), values
