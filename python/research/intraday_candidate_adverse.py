"""EJ Batch 5 matched adverse exits on the sealed activity/profit66 arm."""

from copy import deepcopy
from datetime import datetime
from fractions import Fraction

from research import intraday_candidate_exit as exit_rule
from research import intraday_candidate_exit_reference as reference
from research.intraday_ed_inputs import MINUTE, SessionInputs


THRESHOLD = 1 - exit_rule.COST


def _eligible(question):
    return (question["status"] == "opportunity" and question["activity_selection"]["state"] == "select"
            and question["candidate"]["status"] == "closed")


def needs_session(row):
    """Only a selected trade with time for an earlier request needs quotes."""
    for question in row["questions"]:
        if _eligible(question):
            prior = question["candidate"]
            if _clock(prior["entry_at"]) + 3 * MINUTE < _clock(prior["request_at"]):
                return True
    return False


def _saved_opens(row):
    exit_rule._require(row["entry_independently_verified"] is True and type(row["fold"]) is int
                       and row["fold"] in range(1, 6)
                       and all(row[key] == row["roster"][key]
                               for key in ("session", "symbol", "coordinate_status", "original_frames")),
                       "adverse coordinate provenance differs")
    opens = {at: (price, issue) for at, price, issue in row["modeled_opens"]}
    exit_rule._require(len(opens) == len(row["modeled_opens"]), "duplicate saved open")
    return opens


def _clock(value):
    exit_rule._require(type(value) is str, "saved adverse clock is not an aware minute")
    clock = datetime.fromisoformat(value)
    exit_rule._require(clock.utcoffset() is not None and clock.second == clock.microsecond == 0,
                       "saved adverse clock is not an aware minute")
    return clock


def _control(row, question, opens, session):
    """Validate the immutable, receipt-authenticated entry and prior sale."""
    require, number = exit_rule._require, exit_rule._number
    original, prior = question["baseline"], question["candidate"]
    fill = original["fill"]
    sale, = original["holding"]["realized_exits"]
    original_order, original_fill = original["holding"]["events"]
    quantity = fill["quantity"]
    require(original["status"] == original["holding"]["status"] == "closed" and fill["outcome"] == "modeled"
            and all(type(count) is int and count == quantity
                    for count in (quantity, prior["quantity"], original_fill["quantity"], sale["quantity"]))
            and quantity > 0 and sale["symbol"] == row["symbol"], "saved adverse entry quantity differs")
    entry = _clock(prior["entry_at"])
    request = _clock(prior["request_at"])
    execution = _clock(prior["exit_at"])
    original_request = _clock(original_order["at"])
    original_exit = _clock(original_fill["at"])
    require(entry == _clock(question["decision_at"]) + MINUTE and entry.date().isoformat() == row["session"]
            and entry <= request <= original_request and execution == request + MINUTE
            and original_request + MINUTE == original_exit <= entry + 30 * MINUTE
            and original_order["execute_at"] == original_fill["at"]
            and original_order["action"] == original_fill["action"] == "exit"
            and original_fill["fill_status"] == "modeled" and prior["issue"] is None,
            "saved adverse clock geometry differs")

    saved_entry, issue = opens.get(entry.isoformat(), (None, "missing"))
    buy, sell = number(prior["entry_price"]), number(prior["exit_price"])
    basis, gross = quantity * buy, quantity * (sell - buy)
    fee = basis * exit_rule.COST
    require(issue is None and number(saved_entry) == buy and buy > 0 and sell > 0
            and all(number(value) == basis for value in (original["entry_notional"], prior["entry_notional"],
                                                         sale["entry_basis"]))
            and all(number(value) == fee for value in (fill["cost"], original["cost"], prior["cost"],
                                                       sale["allocated_entry_cost"]))
            and number(prior["gross"]) == gross and number(prior["net"]) == gross - fee,
            "saved adverse entry basis, cost or prior payoff differs")
    if prior["selected_trigger"] == "profit66":
        start = _clock(prior["trigger_start_at"])
        require(entry <= start and start + 3 * MINUTE == request < original_request
                and number(prior["trigger_close"]) >= buy * exit_rule.THRESHOLD,
                "saved profit66 trigger differs")
    else:
        require(prior["selected_trigger"] == "original" and request == original_request
                and sell == number(original_fill["open_price"])
                and prior["trigger_start_at"] is prior["trigger_close"] is None, "saved original trigger differs")
    if session is not None:
        require(isinstance(session, SessionInputs) and session.symbol == row["symbol"]
                and session.opening.date().isoformat() == row["session"]
                and session.opening <= entry < execution <= original_exit <= session.closing,
                "adverse archive session identity or bounds differ")
    return entry, request, quantity, buy, basis


def _unknown(result, issue):
    result.update(trigger_start_at=None, trigger_close=None)
    return exit_rule._unknown(result, issue)


def _loss_exit(row, question, opens, session, result):
    entry, prior_request, quantity, buy, basis = _control(row, question, opens, session)
    if entry + 3 * MINUTE >= prior_request:
        return result
    if session is None:
        return _unknown(result, "missing_session")
    initial = exit_rule._minute(session, entry)
    if initial is None:
        return _unknown(result, "entry_price_unknown")
    exit_rule._require(Fraction(initial[0]) == buy, "archived adverse entry price differs")

    start = entry
    while start + 3 * MINUTE < prior_request:
        prices = exit_rule._minute(session, start)
        if prices is None:
            return _unknown(result, "first_trigger_unknown")
        close = Fraction(prices[3])
        if close <= buy * THRESHOLD:
            request, execution = start + 3 * MINUTE, start + 4 * MINUTE
            result.update(selected_trigger="loss33", trigger_start_at=start.isoformat(), trigger_close=str(close),
                          request_at=request.isoformat(), exit_at=execution.isoformat(), exit_price=None)
            fill = exit_rule._minute(session, execution)
            if fill is None:
                return exit_rule._unknown(result, "exit_price_unknown")
            sell = Fraction(fill[0])
            gross, fee = (sell - buy) * quantity, basis * exit_rule.COST
            result.update(exit_price=str(sell), gross=str(gross), cost=str(fee), net=str(gross - fee))
            break
        start += MINUTE
    return result


def evaluate_coordinate(row, session):
    """Keep the prior profit66 payoff as control and try an earlier full exit."""
    opens = _saved_opens(row)
    output = deepcopy(row)
    for original, question in zip(row["questions"], output["questions"], strict=True):
        prior = original["candidate"]
        question["baseline"] = {**deepcopy(prior), "fill": deepcopy(original["baseline"]["fill"])}
        if _eligible(original):
            question["candidate"] = _loss_exit(row, original, opens, session, question["candidate"])
    return output


def _reference_exit(row, question, opens, session, expected):
    entry, limit, shares, entry_price, _ = _control(row, question, opens, session)
    span = (limit - entry) // MINUTE - 3
    if span <= 0:
        return
    initial = reference._quote(session, entry)
    issue = None
    if initial is None:
        issue = "missing_session" if session is None else "entry_price_unknown"
    else:
        reference._check(initial[0] == entry_price, "archived adverse entry differs")
        # Enumerate only the clocks whose requests beat the audited one. The
        # first absent close or cross-product match settles the new outcome.
        for offset in range(span):
            clock = entry + offset * MINUTE
            quote = reference._quote(session, clock)
            if quote is None:
                issue = "first_trigger_unknown"
                break
            if 10_000 * (entry_price - quote[3]) >= 33 * entry_price:
                at = clock + 4 * MINUTE
                exit_quote = reference._quote(session, at)
                expected.update(selected_trigger="loss33", trigger_start_at=clock.isoformat(),
                                trigger_close=str(quote[3]), request_at=(at - MINUTE).isoformat(),
                                exit_at=at.isoformat(), exit_price=None)
                if exit_quote is None:
                    issue = "exit_price_unknown"
                else:
                    principal = entry_price * shares
                    fee = principal * 33 / 10_000
                    cash = exit_quote[0] * shares - principal
                    expected.update(exit_price=str(exit_quote[0]), gross=str(cash), cost=str(fee), net=str(cash - fee))
                break
    if issue is not None:
        expected.update(status="unresolved", issue=issue,
                        **dict.fromkeys(("gross", "cost", "net", "entry_notional")))
        if issue != "exit_price_unknown":
            expected.update(selected_trigger="unknown",
                            **dict.fromkeys(("request_at", "exit_at", "exit_price", "trigger_start_at", "trigger_close")))


def verify_coordinate(row, reference_session, produced):
    """Reconstruct the candidate from separately decoded quotes."""
    opens = _saved_opens(row)
    expected = deepcopy(row)
    for source, saved in zip(row["questions"], expected["questions"], strict=True):
        saved["baseline"] = deepcopy(source["candidate"])
        saved["baseline"]["fill"] = deepcopy(source["baseline"]["fill"])
        if (source["activity_selection"]["state"], source["status"], saved["baseline"]["status"]) == (
                "select", "opportunity", "closed"):
            _reference_exit(row, source, opens, reference_session, saved["candidate"])
    reference._same(expected, produced, "adverse coordinate or payoff differs")
