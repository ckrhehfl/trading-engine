"""Audit the first modeled entry against sealed features and original minute prices."""

from fractions import Fraction

from research.intraday_campaign_cash_book import INITIAL_CAPITAL, ORDER_FRACTION
from research.intraday_ed_inputs import MINUTE, _require, _session
from research.intraday_scenario_management_prices_reference import ReadSession
from research.intraday_scenario_open_reference import _aggregate, _prefix_rows, _price_row
from research.intraday_scenario_snapshot import ProjectedDay, _time


def _without_request(row: dict, status: str, reason: str) -> None:
    _require((row["status"], row["reason"], row["initial"], row["event_expiry"]) == (status, reason, None, None),
             "eh_entry_reference_unattempted_status_differs")


def verify_initial_entry(
    row: dict, projected: ProjectedDay, *, scenario: str, read_session: ReadSession,
    risk_budget: Fraction, cost_bps: int,
) -> None:
    """Check one trace row against its independently sealed day and authenticated price reader."""
    fields = {"day", "rank", "symbol", "status", "reason", "initial", "event_expiry"}
    _require(type(row) is dict and fields <= row.keys() and type(projected) is ProjectedDay
             and row["day"] == projected.day and type(row["rank"]) is int and type(row["symbol"]) is str
             and scenario in ("S1", "S2") and callable(read_session)
             and type(risk_budget) is Fraction and risk_budget > 0 and type(cost_bps) is int and cost_bps in (33, 43),
             "eh_entry_reference_scope_differs")
    symbol = row["symbol"]
    coordinate = next((item for item in projected.coordinates if item.symbol == symbol), None)
    _require(coordinate is not None and any((item.scenario, item.rank) == (scenario, row["rank"])
                                            for item in coordinate.targets), "eh_entry_reference_coordinate_differs")

    if coordinate.status == "known_no_event":
        return _without_request(row, "no_event", coordinate.status)
    if coordinate.status in ("support_unknown", "session_missing") or coordinate.breakout_gap:
        reason = "breakout_observation_gap" if coordinate.breakout_gap else coordinate.status
        return _without_request(row, "unknown", reason)
    if coordinate.status == "not_yet_eligible":
        return _without_request(row, "unsupported", coordinate.status)
    _require(coordinate.status == "frames_present", "eh_entry_reference_coordinate_status_differs")
    first = next((item.frame for item in projected.rows
                  if item.frame.symbol == symbol and scenario in item.scenarios
                  and item.frame.initial.stage == "breakout"), None)
    if first is None:
        return _without_request(row, "no_entry", "breakout_not_observed")
    chart = first.chart
    if chart is None or chart.status == "unsupported":
        return _without_request(row, "unsupported", "breakout_chart_unsupported")
    if chart.status == "unknown":
        return _without_request(row, "unknown", "breakout_chart_unknown")
    if chart.status == "resolved":
        return _without_request(row, "no_entry", "breakout_question_already_resolved")
    at, question = first.observed_at, first.initial.question
    _require(chart.status == "ready" and question is not None and question.start_at == at
             and chart.forecast_id == (symbol, *question.forecast_id(at))
             and first.event_at <= at < first.event_expires_at, "eh_entry_reference_first_breakout_differs")

    session = read_session(projected.day, symbol)
    if session is None:
        return _without_request(row, "unknown", "evaluation_session_missing")
    _session(session)
    _require((session.symbol, session.opening, session.closing) == (symbol, coordinate.opening, coordinate.closing),
             "eh_entry_reference_session_differs")
    end, remainder = divmod(at - session.opening - 2 * MINUTE, MINUTE)
    _require(not remainder and end >= 20 and end % 5 == 0 and first.event_expires_at < session.closing,
             "eh_entry_reference_first_breakout_differs")
    minutes, issues = _prefix_rows(session, range(end - 5, end), at)
    _require(not issues and minutes[-1].available_at == at, "eh_entry_reference_breakout_price_unavailable")
    bar = _aggregate(minutes)
    close = Fraction(bar.close)
    stop = min(Fraction(bar.low), question.level - question.band)
    limit = close + question.band
    _require(question.band > 0 and 0 < stop == question.stop < close
             and close == first.initial.start_close and close > question.level + question.band,
             "eh_entry_reference_breakout_bounds_differ")
    bar_prices = (bar.open, bar.high, bar.low, bar.close)
    _require(chart.five_minute_bars[-1] == tuple((Fraction(price) - question.level) / question.band for price in bar_prices)
             and chart.minutes[-1] == tuple((Fraction(price) - question.level) / question.band
                                            for price in minutes[-1].ohlc), "eh_entry_reference_breakout_price_differs")

    execute_at = at + MINUTE
    if first.event_expires_at - execute_at < 5 * MINUTE:
        return _without_request(row, "no_entry", "too_little_time_for_entry")
    cost = Fraction(cost_bps, 10_000)
    worst_unit_loss = limit * (1 + cost) - stop
    notional_ceiling = min(INITIAL_CAPITAL * ORDER_FRACTION, INITIAL_CAPITAL / (1 + cost))
    quantity = min((risk_budget / 2) // worst_unit_loss, notional_ceiling // limit)
    if not quantity:
        return _without_request(row, "no_entry", "insufficient_risk_or_cash_for_one_unit")

    initial = row["initial"]
    _require(type(initial) is dict and initial.keys() == {"stop", "maximum", "trade"}
             and (initial["stop"], initial["maximum"]) == (str(stop), str(limit)),
             "eh_entry_reference_frozen_bounds_differ")
    trade = initial["trade"]
    _require(type(trade) is list and len(trade) == 8 and type(trade[3]) is int
             and trade[:4] == ["enter", _time(at), _time(execute_at), quantity]
             and trade[7] == "higher_value_of_entry", "eh_entry_reference_request_differs")

    # Read the modeled open only after the exact decision-time request has been checked.
    opened, _ = _price_row(session, execute_at, execute_at + 3 * MINUTE)
    price = None if opened is None else Fraction(opened.ohlc[0])
    if price is None:
        status, reason, fill, filled = "unresolved", "entry_price_unknown", "indeterminate", 0
    elif not stop < price <= limit:
        status, reason, fill, filled = "declined", "open_outside_frozen_entry_bounds", "declined", 0
    else:
        status, reason, fill, filled = "confirmed", "entered", "modeled", quantity
    expiry = _time(first.event_expires_at) if status == "confirmed" else None
    _require(type(trade[5]) is int and trade[4:7] == [fill, filled, None if price is None else str(price)]
             and (row["status"], row["reason"], row["event_expiry"]) == (status, reason, expiry),
             "eh_entry_reference_modeled_open_differs")
