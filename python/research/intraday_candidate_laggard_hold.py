"""Replay fixed longer holds on audited immediate-entry positions."""

from copy import deepcopy
from fractions import Fraction

from research import intraday_candidate_exit as rule
from research import intraday_candidate_exit_reference as reference
from research import intraday_candidate_laggard_execution as trade
from research import intraday_candidate_laggard_profit as profit
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, _session


HOLD_MINUTES = (60, 90)
ORIGINAL_HOLD_MINUTES = 30
FIRST_NEW_CLOSE_OFFSET = ORIGINAL_HOLD_MINUTES - 4


def needs_session(row):
    question, = row["questions"]
    arm = question["baseline"]
    return arm["status"] == "closed" and arm["selected_trigger"] == "time"


def needs_reference_session(row):
    question, = row["questions"]
    arm = question["baseline"]
    return (arm["status"] == "closed"
            and profit._clock(arm["exit_at"]) - profit._clock(arm["entry_at"]) == ORIGINAL_HOLD_MINUTES * MINUTE)


def _position(row, question, session):
    base = question["baseline"]
    rule._require(base["status"] in rule.STATES, "laggard hold original outcome differs")
    if base["status"] != "closed":
        return None
    entry, _, old_sale, buy, old_open, quantity, fee = profit._receipt(row, question)
    if base["selected_trigger"] == "structural":
        return None
    if session is not None:
        _session(session)
        profit._validate_session(row, session, entry, entry + max(HOLD_MINUTES) * MINUTE)
    stop = rule._number(question["rank_decision"]["stop"])
    return entry, old_sale, old_open, buy, quantity, fee, stop


def _unknown_close(base, start):
    arm = {**base, "selected_trigger": "unknown", "trigger_start_at": None, "trigger_close": None,
           "exit_request_at": None, "exit_execute_at": None, "exit_at": None, "exit_price": None}
    return trade._unresolved(arm, "first_structural_close_unknown", start)


def _schedule(base, execution, start=None, close=None):
    return {**base, "selected_trigger": "time" if start is None else "structural",
            "trigger_start_at": None if start is None else start.isoformat(),
            "trigger_close": None if close is None else str(close),
            "exit_request_at": (execution - MINUTE).isoformat(), "exit_execute_at": execution.isoformat(),
            "exit_at": None, "exit_price": None}


def _closed(arm, execution, price, gross, net):
    return {**arm, "exit_at": execution.isoformat(), "exit_price": str(price), "gross": str(gross), "net": str(net)}


def _project_arm(base, position, session, hold):
    entry, old_sale, old_open, buy, quantity, fee, stop = position
    expiry = entry + hold * MINUTE
    sale, trigger, trigger_close = expiry, None, None
    start = entry + FIRST_NEW_CLOSE_OFFSET * MINUTE
    while start + trade.BAR_LAG + MINUTE < expiry:
        bar = None if session is None else rule._minute(session, start)
        if bar is None:
            return _unknown_close(base, start)
        close = Fraction(bar[3])
        if close <= stop:
            sale, trigger, trigger_close = start + trade.BAR_LAG + MINUTE, start, close
            break
        start += MINUTE
    arm = _schedule(base, sale, trigger, trigger_close)
    quote = rule._minute(session, sale)
    if quote is None:
        return trade._unresolved(arm, "exit_price_unknown", sale)
    if sale == old_sale:
        profit._check_open(quote, old_open)
    price = Fraction(quote[0])
    gross = quantity * (price - buy)
    return _closed(arm, sale, price, gross, gross - fee)


def _reference_arm(base, position, session, hold):
    entry, old_sale, old_open, buy, quantity, _, stop = position
    sale = entry + hold * MINUTE
    trigger, trigger_close = None, None
    for offset in range(ORIGINAL_HOLD_MINUTES, hold):
        candidate = entry + offset * MINUTE
        bar_start = candidate - 4 * MINUTE
        quote = _reference_quote(session, bar_start)
        if quote is None:
            return _unknown_close(base, bar_start)
        if quote[3] <= stop:
            sale, trigger, trigger_close = candidate, bar_start, quote[3]
            break
    arm = _schedule(base, sale, trigger, trigger_close)
    prices = _reference_quote(session, sale)
    if prices is None:
        return trade._unresolved(arm, "exit_price_unknown", sale)
    price = prices[0]
    reference._check(sale != old_sale or price == old_open, "laggard hold archived original open differs")
    proceeds = quantity * price
    basis = quantity * buy
    return _closed(arm, sale, price, proceeds - basis, proceeds - basis * Fraction(10_033, 10_000))


def _compare(row, session, reprice):
    output = deepcopy(row)
    question, = output["questions"]
    base = question["baseline"]
    position = _position(row, question, session)
    for key, hold in zip(("candidate", "hold90"), HOLD_MINUTES, strict=True):
        question[key] = deepcopy(base) if position is None else reprice(base, position, session, hold)
    return output


def project(row, session):
    """Extend only baseline time exits, preserving the audited entry and structure."""
    return _compare(row, session, _project_arm)


def reference_project(row, session):
    """Rebuild both first requests and their cash outcomes from reference quotes."""
    return _compare(row, session, _reference_arm)
