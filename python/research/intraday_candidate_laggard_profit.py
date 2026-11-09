"""Apply the fixed profit close to an audited immediate-entry receipt."""

from copy import deepcopy
from datetime import datetime
from fractions import Fraction

from research import intraday_candidate_exit as rule
from research import intraday_candidate_exit_reference as reference
from research import intraday_candidate_laggard_execution as trade
from research import intraday_candidate_timing as timing
from research.intraday_ed_inputs import MINUTE, SessionInputs


def _clock(value):
    clock = datetime.fromisoformat(value)
    rule._require(clock.utcoffset() is not None and clock.second == clock.microsecond == 0,
                  "laggard profit receipt clock differs")
    return clock


def needs_session(row):
    question, = row["questions"]
    arm = question["baseline"]
    return arm["status"] == "closed" and _clock(arm["entry_at"]) + 3 * MINUTE < _clock(arm["exit_request_at"])


def needs_reference_session(row):
    question, = row["questions"]
    arm = question["baseline"]
    return arm["status"] == "closed" and (_clock(arm["exit_at"]) - _clock(arm["entry_at"])) // MINUTE > 4


def _receipt(row, question):
    """Check the prior realized arm's immutable quantity, clocks and ledger."""
    arm, plan = question["baseline"], question["rank_decision"]
    entry, request, sale = (_clock(arm[name]) for name in ("entry_at", "exit_request_at", "exit_at"))
    buy, sell = (rule._number(arm[name]) for name in ("entry_price", "exit_price"))
    decision = _clock(question["decision_at"])
    fill = arm["fill"]
    quantity = fill["quantity"]
    limit, stop = (rule._number(plan[name]) for name in ("limit", "stop"))
    rule._require(question["status"] == "opportunity" and plan["rank_state"] == "select"
                  and plan["order_state"] == "ready" and arm["status"] == "closed"
                  and arm["reason"] == "exited" and arm["unknown_at"] is None and fill["outcome"] == "modeled"
                  and type(quantity) is int and quantity > 0 and type(arm["requested_quantity"]) is int
                  and arm["requested_quantity"] == quantity and 0 < stop < buy <= limit and sell > 0,
                  "laggard profit original position or bounds differ")
    rule._require(_clock(arm["entry_request_at"]) == decision and decision + MINUTE == entry
                  and _clock(arm["entry_execute_at"]) == entry and entry.date().isoformat() == row["session"]
                  and entry + 4 * MINUTE <= sale <= entry + 30 * MINUTE
                  and request + MINUTE == sale == _clock(arm["exit_execute_at"]),
                  "laggard profit original execution clocks differ")
    basis, gross = quantity * buy, quantity * (sell - buy)
    fee = basis * rule.COST
    rule._require(rule._number(fill["cost"]) == fee
                  and tuple(rule._number(arm[name]) for name in ("entry_notional", "gross", "cost", "net"))
                  == (basis, gross, fee, gross - fee), "laggard profit original payoff differs")
    if arm["selected_trigger"] == "structural":
        start = _clock(arm["trigger_start_at"])
        rule._require(entry <= start and start + 3 * MINUTE == request and sale < entry + 30 * MINUTE
                      and 0 < rule._number(arm["trigger_close"]) <= stop, "laggard profit original structure differs")
    else:
        rule._require(arm["selected_trigger"] == "time" and sale == entry + 30 * MINUTE
                      and arm["trigger_start_at"] is arm["trigger_close"] is None,
                      "laggard profit original expiry differs")
    return entry, request, sale, buy, sell, quantity, fee


def _validate_session(row, session, entry, sale):
    if session is not None:
        rule._require(isinstance(session, SessionInputs) and session.symbol == row["symbol"]
                      and session.opening.date().isoformat() == row["session"]
                      and entry == session.opening + 63 * MINUTE and sale < session.closing,
                      "laggard profit archive identity or bounds differ")


def _check_open(observation, saved):
    if observation is not None:
        rule._require(Fraction(observation[0]) == saved, "laggard profit archived original open differs")


def _comparison_row(row):
    result = deepcopy(row)
    question, = result["questions"]
    question["candidate"] = deepcopy(question["baseline"])
    question["exit_capacity"] = None
    rule._require(question["baseline"]["status"] in rule.STATES, "laggard profit original outcome differs")
    return result, question


def _unknown_close(arm, start):
    arm.update(selected_trigger="unknown", exit_request_at=None, exit_execute_at=None, exit_at=None,
               exit_price=None, trigger_start_at=None, trigger_close=None)
    return trade._unresolved(arm, "first_profit_close_unknown", start)


def _request(arm, start, close, request, execution):
    arm.update(selected_trigger="profit66", trigger_start_at=start.isoformat(), trigger_close=str(close),
               exit_request_at=request.isoformat(), exit_execute_at=execution.isoformat(), exit_at=None, exit_price=None)


def project(row, session):
    """Overlay the first eligible profit close without changing the original arm."""
    output, question = _comparison_row(row)
    arm = question["candidate"]
    if arm["status"] != "closed":
        return output

    entry, request, sale, buy, sell, quantity, fee = _receipt(row, question)
    _validate_session(row, session, entry, sale)
    if session is not None and entry + 3 * MINUTE < request:
        _check_open(rule._minute(session, entry), buy)
    start = entry
    while start + 3 * MINUTE < request:
        bar = None if session is None else rule._minute(session, start)
        if bar is None:
            question["candidate"] = _unknown_close(arm, start)
            break
        close = Fraction(bar[3])
        if close >= buy * rule.THRESHOLD:
            new_request = start + 3 * MINUTE
            execution = new_request + MINUTE
            _request(arm, start, close, new_request, execution)
            quote = rule._minute(session, execution)
            if quote is None:
                question["candidate"] = trade._unresolved(arm, "profit_exit_price_unknown", execution)
            else:
                price = Fraction(quote[0])
                gross = quantity * (price - buy)
                arm.update(exit_at=execution.isoformat(), exit_price=str(price), gross=str(gross), net=str(gross - fee))
            break
        start += MINUTE
    if session is not None and entry + 3 * MINUTE < request:
        _check_open(rule._minute(session, sale), sell)
    question["exit_capacity"] = timing._capacity(entry, sale, buy, sell, session)
    return output


def reference_project(row, session):
    """Reconstruct the fixed clock, ledger, and diagnostic from reference quotes."""
    output, question = _comparison_row(row)
    arm = question["candidate"]
    if arm["status"] != "closed":
        return output

    entry, _, sale, buy, sell, quantity, _ = _receipt(row, question)
    _validate_session(row, session, entry, sale)
    duration = (sale - entry) // MINUTE
    if duration > 4:
        _check_open(reference._quote(session, entry), buy)
    for offset in range(4, duration):
        start = entry + (offset - 4) * MINUTE
        bar = reference._quote(session, start)
        if bar is None:
            question["candidate"] = _unknown_close(arm, start)
            break
        if 10_000 * bar[3] >= 10_066 * buy:
            execution = entry + offset * MINUTE
            _request(arm, start, bar[3], execution - MINUTE, execution)
            quote = reference._quote(session, execution)
            if quote is None:
                question["candidate"] = trade._unresolved(arm, "profit_exit_price_unknown", execution)
            else:
                proceeds = quantity * quote[0]
                basis = buy * quantity
                arm.update(exit_at=execution.isoformat(), exit_price=str(quote[0]), gross=str(proceeds - basis),
                           net=str(proceeds - basis * Fraction(10_033, 10_000)))
            break
    if duration > 4:
        _check_open(reference._quote(session, sale), sell)
    earlier = [reference._quote(session, entry + offset * MINUTE) for offset in range(4, duration)]
    missing = earlier.count(None)
    best = None if missing else max([sell, *(bar[0] for bar in earlier)])
    question["exit_capacity"] = {
        "earlier_opens": len(earlier), "missing_opens": missing,
        "best_open": None if best is None else str(best),
        "best_net_bps": None if best is None else str(10_000 * best / buy - 10_033),
    }
    return output
