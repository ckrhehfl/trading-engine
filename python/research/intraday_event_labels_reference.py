"""Independent EL target reconstruction from immutable query and raw minutes.

Only frozen input/output types and the independently implemented execution
reference are reused. No production path, waiting, maturity, denominator,
feature or label-building helper supplies an expected value.
"""

from collections.abc import Mapping
from fractions import Fraction

from research.intraday_event_execution import EntryRequest
from research import intraday_event_execution_reference as cash_reference
from research.intraday_event_labels import LabelQuery, PathTarget, CashTarget, PayoffTargets
from research.intraday_event_scenarios import EventFrame, MinuteBar


def prepare_query(frame, chart, *, session_length):
    if not isinstance(frame, EventFrame):
        raise ValueError("EL reference requires a causal event frame")
    reference_offset, reference_close = None, None
    if chart is not None:
        identity = {"schema": "EL-causal-chart-features-v1", "event_id": frame.event_id,
                    "family": frame.family, "state": frame.state, "decision_offset": frame.decision_offset}
        if not isinstance(chart, Mapping) or any(chart.get(key) != value for key, value in identity.items()):
            raise ValueError("EL reference chart identity differs")
        reference_offset, raw = chart.get("reference_offset"), chart.get("reference_close")
        if type(reference_offset) is not int or type(raw) is not str:
            raise ValueError("EL reference chart coordinate differs")
        reference_close = Fraction(raw)
        if str(reference_close) != raw:
            raise ValueError("EL reference requires canonical rational text")
    return LabelQuery(frame.event_id, frame.family, frame.state, frame.terminal, frame.onset_offset,
        frame.offset, frame.decision_offset, reference_offset, reference_close,
        frame.scale, frame.structure, session_length)


def _quote(rows, offset):
    value = rows.get(offset)
    if value is not None and (not isinstance(value, MinuteBar) or value.offset != offset):
        raise ValueError("EL reference raw coordinate differs")
    return value


def _check_reference(query, rows):
    if query.reference_close is None:
        return
    admitted = {}
    for offset in sorted(index for index in rows if 0 <= index < query.decision_offset):
        value = _quote(rows, offset)
        if value is not None and value.available_offset <= query.decision_offset:
            admitted[offset] = value.close
    if not admitted or (max(admitted), admitted[max(admitted)]) != (query.reference_offset, query.reference_close):
        raise ValueError("EL reference sealed close disagrees with the observed prefix")


def _inside(clock, session_length):
    return clock if clock < session_length else None


def _path_target(query, rows):
    first, last = query.decision_offset + 1, query.decision_offset + 30
    ceiling = (None if query.reference_close is None else query.reference_close +
               max(query.scale * 3, query.reference_close * Fraction(33, 5000)))

    def finish(value, state, why, observed=None, mature=None):
        return PathTarget(value, state, why, first, last, ceiling, query.structure_stop, observed, mature)

    if ceiling is None:
        return finish(None, "unknown", "initial_reference_unknown")
    if last >= query.session_length:
        return finish(None, "unknown", "path_horizon_not_inside_session")
    arrived = [query.decision_offset]
    for at in range(first, last + 1):
        quote = _quote(rows, at)
        if quote is None:
            return finish(None, "unknown", "missing_before_first_touch", at,
                          _inside(max(*arrived, at + 3), query.session_length))
        arrived.append(quote.available_offset)
        matured = _inside(max(arrived), query.session_length)
        if matured is None:
            return finish(None, "unknown", "path_not_mature_in_session", at)
        touch = int(quote.high >= ceiling) + 2 * int(quote.low <= query.structure_stop)
        if touch:
            return finish({1: 2, 2: 0, 3: None}[touch], "unknown_intrabar" if touch == 3 else "known",
                {1: "upper_first", 2: "lower_first", 3: "both_barriers_in_one_minute"}[touch], at, matured)
    return finish(1, "known", "neither_barrier_in_thirty_minutes", last, max(arrived))


def _no_trade(query, delay, denominator, status, reason, *, close=None, mature=None):
    known = status == "no_entry"
    decision = query.decision_offset + delay
    return CashTarget(Fraction(0) if known else None, Fraction(0) if known else None,
        0 if known else None, status, reason, decision, decision + 1, decision + 31,
        None, close, None if close is None else close + query.scale, denominator,
        mature, mature if known else None)


class _Reads(Mapping):
    def __init__(self, rows):
        self.rows, self.observations = rows, {}

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, at):
        found = _quote(self.rows, at)
        self.observations[at] = found
        if at not in self.rows:
            raise KeyError(at)
        return found


def _cash_target(query, rows, delay, close, denominator):
    decision = query.decision_offset + delay
    request = EntryRequest(decision, close, query.scale, query.structure_stop)
    read = _Reads(rows)
    result = cash_reference.evaluate(request, read, query.session_length,
                                     entry_policy="next_open_capped", management="fixed_exit")
    consumption = [decision, *(bar.available_offset for bar in read.observations.values() if bar is not None)]
    if result["unknown_offset"] is not None:
        extra = 3 if result["reason"] in {"entry_open_unknown", "exit_open_unknown"} else 0
        consumption.append(result["unknown_offset"] + extra)
    maturity = _inside(max(consumption), query.session_length)
    fill = None if result["counts"]["entry_unknown"] else min(result["counts"]["filled"], 1)
    entry_quote = read.observations.get(decision + 1)
    fill_clock = (entry_quote.available_offset if entry_quote is not None else
                  decision if fill == 0 else None)
    fill_clock = _inside(fill_clock, query.session_length) if fill_clock is not None else None
    if fill_clock is None:
        fill = None
    net = Fraction(result["net"]) if result["net"] is not None else None
    status, reason = result["status"], result["reason"]
    if status in {"no_entry", "no_fill"}:
        value = Fraction(0)
    elif net is None:
        status, value = "unknown", None
    elif denominator == 0 or denominator is None:
        status, reason, value = "undefined", "zero_original_requested_notional", None
    else:
        status, value = "known", net / denominator
    if maturity is None:
        status, reason, value, net = "unknown", "cash_not_mature_in_session", None, None
    return CashTarget(value, net, fill, status, reason, decision, decision + 1, decision + 31,
        result["requested_quantity"], close, close + query.scale, denominator, maturity, fill_clock,
        result["status"], result["reason"])


def _original_allocation(query):
    ceiling = query.reference_close + query.scale
    risk_units = Fraction(50000) / (ceiling - query.structure_stop + ceiling * Fraction(33, 10000))
    cash_units = Fraction(2000000) / ceiling
    count = min(risk_units.numerator // risk_units.denominator, cash_units.numerator // cash_units.denominator)
    return count * ceiling


def _wait_target(query, rows, denominator):
    clock = query.decision_offset + 3
    if clock + 31 >= query.session_length:
        return _no_trade(query, 3, denominator, "no_entry", "delayed_expiry_not_inside_session",
                         mature=query.decision_offset)
    observations = [(i, _quote(rows, i)) for i in range(query.reference_offset + 1, clock)]
    received = [(i, bar) for i, bar in observations if bar is not None and bar.available_offset <= clock]
    if any(bar.close <= query.structure_stop for _, bar in received):
        return _no_trade(query, 3, denominator, "no_entry", "wait_structure_invalidated", mature=clock)
    absent_due = [i for i, bar in observations
                  if i + 3 <= clock and (bar is None or bar.available_offset > clock)]
    if absent_due:
        return _no_trade(query, 3, denominator, "unknown", "wait_required_close_unknown", mature=clock)
    close = received[-1][1].close if received else query.reference_close
    if close + query.scale <= query.structure_stop:
        return _no_trade(query, 3, denominator, "no_entry", "wait_stop_not_below_guard_cap", close=close, mature=clock)
    return _cash_target(query, rows, 3, close, denominator)


def build_targets(query, bars):
    if type(query) is not LabelQuery or not isinstance(bars, Mapping):
        raise TypeError("EL reference LabelQuery and raw mapping required")
    _check_reference(query, bars)
    path = _path_target(query, bars)
    if query.terminal and query.state != "CONFIRMED":
        admission, reason = "not_admissible", "terminal_state_not_entry_admissible"
    elif query.decision_offset + 31 >= query.session_length:
        admission, reason = "not_admissible", "expiry_not_inside_session"
    elif query.reference_close is None:
        admission, reason = "unknown", "initial_reference_unknown"
    elif query.reference_close + query.scale <= query.structure_stop:
        admission, reason = "not_admissible", "stop_not_below_guard_cap"
    else:
        admission, reason = "admissible", "causal_entry_request_supported"
    denominator = (_original_allocation(query) if query.reference_close is not None
        and query.reference_close + query.scale > query.structure_stop else None)
    if admission == "admissible":
        immediate = _cash_target(query, bars, 0, query.reference_close, denominator)
        waited = _wait_target(query, bars, denominator)
    else:
        immediate, waited = (_no_trade(query, delay, denominator,
            "no_entry" if admission == "not_admissible" else "unknown", reason,
            close=query.reference_close, mature=_inside(query.decision_offset, query.session_length)) for delay in (0, 3))
    return PayoffTargets(query.event_id, query.decision_offset, admission, reason, denominator, path, immediate, waited)


def verify(result, query, bars):
    if type(result) is not PayoffTargets or result != build_targets(query, bars):
        raise ValueError("EL independent offline targets differ")
