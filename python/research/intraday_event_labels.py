"""Pure EL offline targets, separated from target-free decision information.

The caller seals its causal chart/query stream before calling ``build_targets``.
Path classes describe thirty complete minutes d+1..d+30; execution exits at the
open of d+31. Neither high/low touches nor the proposed 66bp upper barrier imply
a modeled fill or profitable execution. All clocks refer to one known session.
No loader, learner, order, source registration or actual-run authority is here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

from research import intraday_event_execution as execution
from research.intraday_event_scenarios import EventFrame, MinuteBar


PATH_CLASSES = {"lower_first": 0, "neutral": 1, "upper_first": 2}
HORIZON = 30
WAIT = 3
UPPER_BPS = 66


@dataclass(frozen=True)
class LabelQuery:
    """Only information fixed at the initial decision; no outcome fields."""

    event_id: str
    family: str
    state: str
    terminal: bool
    onset_offset: int
    event_offset: int
    decision_offset: int
    reference_offset: int | None
    reference_close: Fraction | None
    scale: Fraction
    structure_stop: Fraction
    session_length: int

    def __post_init__(self):
        active = {"WAITING", "WAITING_PULLBACK", "PULLBACK"}
        terminal = {"CONFIRMED", "INVALIDATED", "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK",
                    "UNKNOWN_GAP", "SESSION_END"}
        if (type(self.event_id) is not str or not self.event_id.strip() or len(self.event_id) > 512
                or self.family not in ("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK")
                or type(self.terminal) is not bool or self.state not in (terminal if self.terminal else active)
                or any(type(value) is not int for value in (
                    self.onset_offset, self.event_offset, self.decision_offset, self.session_length))
                or not 0 <= self.onset_offset <= self.event_offset < self.decision_offset
                or not self.event_offset < self.session_length
                or not isinstance(self.scale, Fraction) or self.scale <= 0
                or not isinstance(self.structure_stop, Fraction) or self.structure_stop <= 0):
            raise ValueError("EL label query identity or causal geometry differs")
        if (self.reference_offset is None) != (self.reference_close is None):
            raise ValueError("EL label query reference pair differs")
        if self.reference_close is not None and (
                type(self.reference_offset) is not int or not 0 <= self.reference_offset < self.decision_offset
                or not isinstance(self.reference_close, Fraction) or self.reference_close <= 0):
            raise ValueError("EL label query reference geometry differs")


@dataclass(frozen=True)
class PathTarget:
    value: int | None
    status: str
    reason: str
    first_offset: int
    last_offset: int
    upper: Fraction | None
    lower: Fraction
    observed_through: int | None
    matured_offset: int | None


@dataclass(frozen=True)
class CashTarget:
    value: Fraction | None
    net_cash: Fraction | None
    fill: int | None
    status: str
    reason: str
    decision_offset: int
    entry_offset: int
    expiry_offset: int
    requested_quantity: int | None
    reference_close: Fraction | None
    guard_cap: Fraction | None
    denominator: Fraction | None
    matured_offset: int | None
    fill_matured_offset: int | None
    execution_status: str | None = None
    execution_reason: str | None = None


@dataclass(frozen=True)
class PayoffTargets:
    event_id: str
    decision_offset: int
    entry_admissibility: str
    entry_reason: str
    original_requested_notional: Fraction | None
    path: PathTarget
    immediate: CashTarget
    wait3: CashTarget
    schema: str = "EL-separated-offline-targets-v1"
    claim: str = "discovery path and nominal event cash targets; no actual fill, account return or profitability claim"


def prepare_query(frame: EventFrame, chart: Mapping | None, *, session_length: int) -> LabelQuery:
    """Bind a sealed chart reference to its frame without accepting future bars."""
    if (not isinstance(frame, EventFrame) or type(session_length) is not int or session_length <= 0
            or any(type(value) is not int for value in (frame.onset_offset, frame.offset, frame.decision_offset))
            or not 0 <= frame.onset_offset <= frame.offset < frame.decision_offset
            or frame.offset >= session_length or type(frame.terminal) is not bool
            or not isinstance(frame.scale, Fraction) or frame.scale <= 0
            or not isinstance(frame.structure, Fraction) or frame.structure <= 0):
        raise ValueError("EL label query causal frame geometry differs")
    offset, close = None, None
    if chart is not None:
        if (not isinstance(chart, Mapping) or chart.get("schema") != "EL-causal-chart-features-v1"
                or any(chart.get(name) != value for name, value in (
                    ("event_id", frame.event_id), ("family", frame.family), ("state", frame.state),
                    ("decision_offset", frame.decision_offset)))
                or type(chart.get("reference_offset")) is not int
                or not 0 <= chart["reference_offset"] < frame.decision_offset
                or type(chart.get("reference_close")) is not str):
            raise ValueError("EL label query chart binding differs")
        offset, close = chart["reference_offset"], Fraction(chart["reference_close"])
        if close <= 0 or str(close) != chart["reference_close"]:
            raise ValueError("EL label query requires a canonical positive reference")
    return LabelQuery(frame.event_id, frame.family, frame.state, frame.terminal,
        frame.onset_offset, frame.offset, frame.decision_offset, offset, close,
        frame.scale, frame.structure, session_length)


def _bar(bars, offset):
    bar = bars.get(offset)
    if bar is not None and (not isinstance(bar, MinuteBar) or bar.offset != offset):
        raise ValueError("EL target raw minute coordinate differs")
    return bar


def _reference(query, bars):
    if query.reference_close is None:
        return
    known = [offset for offset in bars if 0 <= offset < query.decision_offset
             and (bar := _bar(bars, offset)) is not None and bar.available_offset <= query.decision_offset]
    if (not known or max(known) != query.reference_offset
            or _bar(bars, query.reference_offset).close != query.reference_close):
        raise ValueError("EL sealed reference differs from the causal raw prefix")


def _mature(clock, query):
    return clock if clock < query.session_length else None


def _path(query, bars):
    start, finish = query.decision_offset + 1, query.decision_offset + HORIZON
    upper = (None if query.reference_close is None else query.reference_close +
             max(3 * query.scale, query.reference_close * Fraction(UPPER_BPS, 10000)))

    def result(value, status, reason, through=None, matured=None):
        return PathTarget(value, status, reason, start, finish, upper, query.structure_stop, through, matured)

    if upper is None:
        return result(None, "unknown", "initial_reference_unknown")
    if finish >= query.session_length:
        return result(None, "unknown", "path_horizon_not_inside_session")
    maturity = query.decision_offset
    for offset in range(start, finish + 1):
        bar = _bar(bars, offset)
        if bar is None:
            maturity = max(maturity, offset + 3)
            return result(None, "unknown", "missing_before_first_touch", offset, _mature(maturity, query))
        maturity = max(maturity, bar.available_offset)
        if _mature(maturity, query) is None:
            return result(None, "unknown", "path_not_mature_in_session", offset)
        up, down = bar.high >= upper, bar.low <= query.structure_stop
        if up and down:
            return result(None, "unknown_intrabar", "both_barriers_in_one_minute", offset, maturity)
        if up or down:
            return result(PATH_CLASSES["upper_first" if up else "lower_first"], "known",
                          "upper_first" if up else "lower_first", offset, maturity)
    return result(PATH_CLASSES["neutral"], "known", "neither_barrier_in_thirty_minutes", finish, maturity)


def _cash(query, delay, denominator, *, reason, known, reference=None, maturity=None):
    decision = query.decision_offset + delay
    return CashTarget(Fraction() if known else None, Fraction() if known else None,
        0 if known else None, "no_entry" if known else "unknown", reason, decision,
        decision + 1, decision + HORIZON + 1, None, reference,
        None if reference is None else reference + query.scale, denominator,
        maturity, maturity if known else None)


class _Consumed(Mapping):
    """Record only quote reads demanded by the reviewed execution evaluator."""

    def __init__(self, bars):
        self.bars = bars
        self.read = {}

    def __getitem__(self, key):
        if key not in self.bars:
            self.read[key] = None
            raise KeyError(key)
        value = _bar(self.bars, key)
        self.read[key] = value
        return value

    def __iter__(self):
        return iter(self.bars)

    def __len__(self):
        return len(self.bars)


def _execution_target(query, bars, delay, reference, denominator):
    request = execution.EntryRequest(query.decision_offset + delay, reference, query.scale, query.structure_stop)
    consumed = _Consumed(bars)
    report = execution.evaluate(request, consumed, query.session_length,
                                entry_policy="next_open_capped", management="fixed_exit")
    decision, entry = request.decision_offset, request.decision_offset + 1
    maturity = max([decision, *(bar.available_offset for bar in consumed.read.values() if bar is not None)])
    if report["unknown_offset"] is not None:
        missing_open = report["reason"] in ("entry_open_unknown", "exit_open_unknown")
        maturity = max(maturity, report["unknown_offset"] + (3 if missing_open else 0))
    fill = None if report["counts"]["entry_unknown"] else int(bool(report["counts"]["filled"]))
    opening = consumed.read.get(entry)
    fill_maturity = decision if opening is None and fill == 0 else None if opening is None else opening.available_offset
    fill_maturity = None if fill_maturity is None else _mature(fill_maturity, query)
    if fill_maturity is None:
        fill = None
    net = None if report["net"] is None else Fraction(report["net"])
    if report["status"] in ("no_entry", "no_fill"):
        value, status, reason = Fraction(), report["status"], report["reason"]
    elif net is None:
        value, status, reason = None, "unknown", report["reason"]
    elif not denominator:
        value, status, reason = None, "undefined", "zero_original_requested_notional"
    else:
        value, status, reason = net / denominator, "known", report["reason"]
    mature = _mature(maturity, query)
    if mature is None:
        value, net, status, reason = None, None, "unknown", "cash_not_mature_in_session"
    return CashTarget(value, net, fill, status, reason, decision, entry,
        decision + HORIZON + 1, report["requested_quantity"], reference, reference + query.scale,
        denominator, mature, fill_maturity, report["status"], report["reason"])


def _notional(query):
    cap = query.reference_close + query.scale
    count = int(min(execution.FIRST_RISK // (cap * (1 + execution.COST) - query.structure_stop),
        min(execution.CAPITAL / 50, execution.CAPITAL / (1 + execution.COST)) // cap))
    return count * cap


def _waiting(query, bars, denominator):
    delayed = query.decision_offset + WAIT
    if delayed + HORIZON + 1 >= query.session_length:
        return _cash(query, WAIT, denominator, known=True, reason="delayed_expiry_not_inside_session",
                     maturity=query.decision_offset)
    needed = range(query.reference_offset + 1, delayed - 2)
    known, missing = {}, []
    for offset in range(query.reference_offset + 1, delayed):
        bar = _bar(bars, offset)
        if bar is not None and bar.available_offset <= delayed:
            known[offset] = bar
        elif offset in needed:
            missing.append(offset)
    # A received violating CLOSE is a sufficient no-entry proof. Unknown bars
    # cannot restore a structure already observed to be invalidated.
    if any(bar.close <= query.structure_stop for bar in known.values()):
        return _cash(query, WAIT, denominator, known=True, reason="wait_structure_invalidated",
                     maturity=delayed)
    if missing:
        return _cash(query, WAIT, denominator, known=False, reason="wait_required_close_unknown", maturity=delayed)
    reference = known[max(known)].close if known else query.reference_close
    if query.structure_stop >= reference + query.scale:
        return _cash(query, WAIT, denominator, known=True, reason="wait_stop_not_below_guard_cap",
                     reference=reference, maturity=delayed)
    return _execution_target(query, bars, WAIT, reference, denominator)


def build_targets(query: LabelQuery, bars: Mapping[int, MinuteBar]) -> PayoffTargets:
    """Retain each sealed query, with separate path, admission and label support."""
    if type(query) is not LabelQuery or not isinstance(bars, Mapping):
        raise TypeError("EL LabelQuery and raw minute mapping required")
    _reference(query, bars)
    path = _path(query, bars)
    if query.terminal and query.state != "CONFIRMED":
        admissibility, reason = "not_admissible", "terminal_state_not_entry_admissible"
    elif query.decision_offset + HORIZON + 1 >= query.session_length:
        admissibility, reason = "not_admissible", "expiry_not_inside_session"
    elif query.reference_close is None:
        admissibility, reason = "unknown", "initial_reference_unknown"
    elif query.structure_stop >= query.reference_close + query.scale:
        admissibility, reason = "not_admissible", "stop_not_below_guard_cap"
    else:
        admissibility, reason = "admissible", "causal_entry_request_supported"
    denominator = (_notional(query) if query.reference_close is not None
                   and query.structure_stop < query.reference_close + query.scale else None)
    if admissibility != "admissible":
        immediate, wait3 = (_cash(query, delay, denominator, known=admissibility == "not_admissible", reason=reason,
            reference=query.reference_close, maturity=_mature(query.decision_offset, query)) for delay in (0, WAIT))
    else:
        immediate = _execution_target(query, bars, 0, query.reference_close, denominator)
        wait3 = _waiting(query, bars, denominator)
    return PayoffTargets(query.event_id, query.decision_offset, admissibility, reason, denominator,
                         path, immediate, wait3)
