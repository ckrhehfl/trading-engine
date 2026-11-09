"""EJ Batch 6 fixed exit timing and separate retrospective exit capacity."""

from copy import deepcopy
from fractions import Fraction

from research import intraday_candidate_adverse as adverse
from research import intraday_candidate_exit as exit_rule
from research import intraday_candidate_exit_reference as reference
from research.intraday_ed_inputs import MINUTE


TIMER_DELAY = 9 * MINUTE
FIRST_EARLIER_OPEN = 4 * MINUTE


def needs_session(row):
    """Request quotes when there are earlier opens to audit, including any timer fill."""
    for question in row["questions"]:
        if adverse._eligible(question):
            prior = question["candidate"]
            if adverse._clock(prior["entry_at"]) + FIRST_EARLIER_OPEN < adverse._clock(prior["exit_at"]):
                return True
    return False


def _reference_eligible(question):
    return (question["status"], question["activity_selection"]["state"], question["candidate"]["status"]) == (
        "opportunity", "select", "closed")


def needs_reference_session(row):
    """Independently identify the coordinates that require archived prices."""
    for question in row["questions"]:
        if _reference_eligible(question):
            prior = question["candidate"]
            entry = adverse._clock(prior["entry_at"])
            execution = adverse._clock(prior["exit_at"])
            if (execution - entry) // MINUTE > 4:
                return True
    return False


def _timer_exit(candidate, entry, prior_request, quantity, buy, basis, session):
    request = entry + TIMER_DELAY
    if request >= prior_request:
        return

    execution = request + MINUTE
    candidate.update(selected_trigger="timer10", request_at=request.isoformat(), exit_at=execution.isoformat(),
                     trigger_start_at=None, trigger_close=None, exit_price=None)
    quote = None if session is None else exit_rule._minute(session, execution)
    if quote is None:
        exit_rule._unknown(candidate, "exit_price_unknown")
        return

    sell = Fraction(quote[0])
    gross = (sell - buy) * quantity
    fee = basis * exit_rule.COST
    candidate.update(exit_price=str(sell), gross=str(gross), cost=str(fee), net=str(gross - fee))


def _capacity(entry, control_exit, buy, saved_sell, session):
    at = entry + FIRST_EARLIER_OPEN
    earlier, missing = 0, 0
    best = saved_sell
    while at < control_exit:
        earlier += 1
        quote = None if session is None else exit_rule._minute(session, at)
        if quote is None:
            missing += 1
        else:
            best = max(best, Fraction(quote[0]))
        at += MINUTE

    return {"earlier_opens": earlier, "missing_opens": missing,
            "best_open": None if missing else str(best),
            "best_net_bps": None if missing else str(((best - buy) / buy - exit_rule.COST) * 10_000)}


def evaluate_coordinate(row, session):
    """Keep the audited prior arm as control and only change the exit clock."""
    opens = adverse._saved_opens(row)
    output = deepcopy(row)
    for source, question in zip(row["questions"], output["questions"], strict=True):
        prior = source["candidate"]
        question["baseline"] = {**deepcopy(prior), "fill": deepcopy(source["baseline"]["fill"])}
        question["exit_capacity"] = None
        if adverse._eligible(source):
            entry, request, quantity, buy, basis = adverse._control(row, source, opens, session)
            _timer_exit(question["candidate"], entry, request, quantity, buy, basis, session)
            question["exit_capacity"] = _capacity(entry, request + MINUTE, buy, Fraction(prior["exit_price"]), session)
    return output


def verify_coordinate(row, reference_session, produced):
    """Rebuild timer cash and capacity independently from the archived quotes."""
    opens = adverse._saved_opens(row)
    expected = deepcopy(row)
    for source, question in zip(row["questions"], expected["questions"], strict=True):
        prior = source["candidate"]
        question["baseline"] = dict(prior, fill=question["baseline"]["fill"])
        question["exit_capacity"] = None
        if not _reference_eligible(source):
            continue

        entry, _, shares, buy, _ = adverse._control(row, source, opens, reference_session)
        prior_exit = adverse._clock(prior["exit_at"])
        duration = (prior_exit - entry) // MINUTE
        earlier = [reference._quote(reference_session, entry + offset * MINUTE) for offset in range(4, duration)]
        missing = earlier.count(None)
        best = None if missing else max([Fraction(prior["exit_price"]), *(quote[0] for quote in earlier)])
        question["exit_capacity"] = {
            "earlier_opens": len(earlier), "missing_opens": missing,
            "best_open": None if best is None else str(best),
            "best_net_bps": None if best is None else str(10_000 * best / buy - 10_033),
        }
        if duration <= 10:
            continue

        execution = entry + 10 * MINUTE
        candidate = question["candidate"]
        candidate.update(selected_trigger="timer10", request_at=(execution - MINUTE).isoformat(),
                         exit_at=execution.isoformat(), trigger_start_at=None, trigger_close=None)
        quote = reference._quote(reference_session, execution)
        if quote is None:
            candidate.update(status="unresolved", issue="exit_price_unknown",
                             **dict.fromkeys(("exit_price", "gross", "cost", "net", "entry_notional")))
        else:
            basis = shares * buy
            fee = basis * 33 / 10_000
            gross = quote[0] * shares - basis
            candidate.update(exit_price=str(quote[0]), gross=str(gross), cost=str(fee), net=str(gross - fee))
    reference._same(expected, produced, "timing coordinate, capacity or payoff differs")
