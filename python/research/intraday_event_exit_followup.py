"""Two frozen EM exit comparisons on archived entries; no IO, fit or orders.

The original outcome remains authoritative for baseline cash and fill maturity.
Each new holding policy demands only its own causal path. Cash is nominal event
cash, never a concurrent funded account or a certified executable return.
"""

from collections import Counter
from datetime import date
from fractions import Fraction

from research import intraday_event_attribution as attribution
from research.intraday_event_evaluation import SessionMarket
from research.intraday_event_scenarios import MinuteBar


SOURCE_ARMS = (attribution.PATH_ARM, ("confirmation", "reference_limit_one_shot", "forecast_full_exit"))
ARM_NAMES = ("original_fixed30_all", "cost_close_exit30_all", "original_forecast_full30_all",
             "original_fixed30_time_eligible", "fixed60_time_eligible")
COMPARISONS = {"cost_close_vs_fixed30": (ARM_NAMES[0], ARM_NAMES[1]),
               "cost_close_vs_forecast_full30": (ARM_NAMES[2], ARM_NAMES[1]),
               "fixed60_vs_fixed30_time_eligible": (ARM_NAMES[3], ARM_NAMES[4])}
COST = Fraction(33, 10_000)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _named(book, name):
    return {**book, "name": name, "source_cash_state": book["cash_state"],
            "trigger_offset": None, "trigger_receipt_offset": None, "unknown_offset": None}


def _excluded(book, name):
    result = _named(book, name)
    result.update(committed=False, status="no_entry", reason="horizon_time_ineligible",
                  fill_state="known_no_fill", cash_state="known_zero", filled_quantity=0,
                  entry_offset=None, entry_price=None, entry_notional=None,
                  gross_cash="0", net_cash="0", cost_cash="0", exit_offset=None, exit_reason=None)
    return result


def _replay(book, report, market, *, horizon, cost_close, name):
    """Replay only the held side of the identical archived modeled entry."""
    result = _named(book, name)
    if book["fill_state"] != "filled":
        return result
    entry, purchase = book["entry_offset"], Fraction(book["entry_price"])
    quantity, stop = book["filled_quantity"], Fraction(report["structure_stop"])
    expiry = entry + horizon
    _require(expiry < market.session_length, "New expiry must remain inside session")
    result.update(status="unresolved", reason=None, cash_state="unknown", gross_cash=None,
                  net_cash=None, cost_cash=None, exit_offset=None, exit_reason=None)

    def unknown(reason, clock):
        result.update(reason=reason, unknown_offset=clock)
        return result

    def sell(clock, reason):
        quote = market.bars.get(clock)
        if quote is None:
            return unknown("exit_open_unknown", clock)
        if quote.available_offset >= market.session_length:
            return unknown("outcome_not_mature_in_session", clock)
        gross, cost = quantity * (quote.open - purchase), quantity * purchase * COST
        result.update(status="closed", reason=reason, cash_state="settled", gross_cash=str(gross),
                      net_cash=str(gross - cost), cost_cash=str(cost), exit_offset=clock, exit_reason=reason)
        return result

    observed, last_receipt, pending = entry, None, None
    for clock in range(entry + 1, expiry + 1):
        # A previously ordered structural liquidation retains its original priority.
        if pending == "structural":
            return sell(clock, pending)
        if clock == expiry:
            return sell(clock, "time")
        if pending is not None:
            return sell(clock, pending)
        due_offset = clock - 3
        if due_offset >= entry:
            due = market.bars.get(due_offset)
            if due is None or due.available_offset > clock:
                return unknown("held_close_unknown", clock)
        witness = None
        while observed < clock:
            quote = market.bars.get(observed)
            if quote is None:
                if observed + 3 <= clock:
                    return unknown("held_close_unknown", clock)
                break
            if quote.available_offset > clock:
                if any((later := market.bars.get(offset)) is not None and later.available_offset <= clock
                       for offset in range(observed + 1, clock)):
                    return unknown("nonchronological_close_availability", clock)
                break
            if last_receipt is not None and quote.available_offset < last_receipt:
                return unknown("nonchronological_close_availability", clock)
            last_receipt = quote.available_offset
            observed += 1
            if quote.close <= stop:
                pending = "structural"
                break
            if cost_close and witness is None and quote.close > purchase * (1 + COST):
                witness = quote
        if pending is None and witness is not None:
            pending = "cost_close"
            result.update(trigger_offset=witness.offset, trigger_receipt_offset=witness.available_offset)
    raise AssertionError("An admitted held path must terminate at expiry")


def measure_event(roster, ledgers_by_arm, market, *, minimum_receipt_lag=1):
    """Retain one complete original episode and the five registered arms."""
    _require(type(minimum_receipt_lag) is int and minimum_receipt_lag >= 1, "Positive minimum receipt lag required")
    _require(market is None or type(market) is SessionMarket, "Typed original session market required")
    if market is not None:
        _require(type(market.session_length) is int and market.session_length > 0, "Positive session length required")
        _require(all(type(offset) is int and type(bar) is MinuteBar and bar.offset == offset
                     and bar.available_offset >= offset + minimum_receipt_lag
                     for offset, bar in market.bars.items()), "Minute identity or minimum receipt lag differs")
    _require(set(SOURCE_ARMS).issubset(ledgers_by_arm), "Both original reference entry arms required")
    originals = []
    for arm in SOURCE_ARMS:
        row = ledgers_by_arm[arm]
        attribution._identity(roster, arm, row)
        originals.append(attribution._book(arm, row))
    fixed, forecast = originals
    report = ledgers_by_arm[SOURCE_ARMS[0]]["execution"]
    _require(attribution._entry_signature(ledgers_by_arm[SOURCE_ARMS[0]])
             == attribution._entry_signature(ledgers_by_arm[SOURCE_ARMS[1]]), "Management changed original entry")
    planned = report.get("planned_entry_offset")
    if planned is not None:
        _require(type(planned) is int and planned == report["decision_offset"] + 1
                 and report["expiry_offset"] == planned + 30, "Original thirty-minute request differs")
        _require(market is not None, "Original request needs its session calendar")
    eligible = planned is not None and planned + 60 + 3 < market.session_length
    eligibility = {"eligible": eligible,
        "reason": "eligible" if eligible else "no_original_request" if planned is None else "insufficient_session_time",
        "planned_entry_offset": planned, "session_length": None if market is None else market.session_length,
        "excluded_original_fill": not eligible and fixed["fill_state"] == "filled"}
    if fixed["fill_state"] == "filled":
        entry = fixed["entry_offset"]
        opening = market.bars.get(entry) if market is not None else None
        _require(planned == entry and opening is not None and opening.available_offset < market.session_length
                 and opening.open == Fraction(fixed["entry_price"])
                 and fixed["filled_quantity"] == report["requested_quantity"], "Original matched fill differs from market")
        replay30 = _replay(fixed, report, market, horizon=30, cost_close=False, name=ARM_NAMES[0])
        if fixed["cash_state"] == "settled":
            _require(all(replay30[key] == fixed[key] for key in
                         ("cash_state", "gross_cash", "net_cash", "cost_cash", "exit_offset", "exit_reason")),
                     "Original fixed exit differs from supplied minute path")
    books = [_named(fixed, ARM_NAMES[0]),
             _replay(fixed, report, market, horizon=30, cost_close=True, name=ARM_NAMES[1]),
             _named(forecast, ARM_NAMES[2]),
             _named(fixed, ARM_NAMES[3]) if eligible else _excluded(fixed, ARM_NAMES[3]),
             _replay(fixed, report, market, horizon=60, cost_close=False, name=ARM_NAMES[4])
             if eligible else _excluded(fixed, ARM_NAMES[4])]
    return {"schema": "EM-exit-followup-event-v1", "roster": dict(roster), "eligibility": eligibility, "arms": books}


def _totals():
    return {**attribution._totals(), "all_fill_entry_notional": Fraction(), "wins": 0}


def _add(total, book):
    attribution._add(total, book)
    if book["fill_state"] == "filled":
        total["all_fill_entry_notional"] += Fraction(book["entry_notional"])
    total["wins"] += int(book["cash_state"] == "settled" and Fraction(book["net_cash"]) > 0)


def summarize(calendar, records):
    """Keep the original calendar, all modeled fills and unknown paired cash."""
    calendar = tuple(calendar)
    _require(calendar and calendar == tuple(sorted(set(calendar))), "Ordered unique calendar required")
    weeks = {}
    for day in calendar:
        parsed = date.fromisoformat(day)
        _require(parsed.isoformat() == day, "Canonical ISO calendar date required")
        iso = parsed.isocalendar()
        weeks[day] = f"{iso.year}-W{iso.week:02}"
    totals = lambda: {name: _totals() for name in ARM_NAMES}
    arms, dates, names = totals(), {day: totals() for day in calendar}, {}
    weekly = {week: {name: {"fills": 0, "wins": 0, "unknown_fills": 0} for name in ARM_NAMES}
              for week in weeks.values()}
    comparisons = {name: {"left": left, "right": right, "whole": attribution._pair_totals(),
        "common_settled": attribution._pair_totals(), "categories": dict.fromkeys(attribution.CATEGORIES, 0),
        "unknown_patterns": dict.fromkeys(("both_unknown", "left_unknown_only", "right_unknown_only"), 0)}
        for name, (left, right) in COMPARISONS.items()}
    eligibility = {"eligible_events": 0, "ineligible_events": 0, "eligible_original_fills": 0,
                   "excluded_original_fills": 0, "reasons": Counter()}
    cost_triggers = dict.fromkeys(("triggered", "settled_cost_exit", "triggered_time_exit", "triggered_unknown_cash"), 0)
    exits = {name: Counter() for name in ARM_NAMES}
    reasons = {name: Counter() for name in ARM_NAMES}
    seen = set()
    for record in records:
        roster = record["roster"]
        event, day, symbol = roster["event_id"], roster["session"], roster["symbol"]
        _require(record["schema"] == "EM-exit-followup-event-v1" and event not in seen and day in weeks,
                 "Duplicate or off-calendar original episode")
        seen.add(event)
        books = {book["name"]: book for book in record["arms"]}
        _require(len(record["arms"]) == len(ARM_NAMES) and set(books) == set(ARM_NAMES), "Duplicate or absent arm")
        cost_book = books[ARM_NAMES[1]]
        if cost_book["trigger_offset"] is not None:
            cost_triggers["triggered"] += 1
            cost_triggers["settled_cost_exit"] += int(
                cost_book["cash_state"] == "settled" and cost_book["exit_reason"] == "cost_close")
            cost_triggers["triggered_time_exit"] += int(
                cost_book["cash_state"] == "settled" and cost_book["exit_reason"] == "time")
            cost_triggers["triggered_unknown_cash"] += int(cost_book["cash_state"] == "unknown")
        gate = record["eligibility"]
        original_fill = books[ARM_NAMES[0]]["fill_state"] == "filled"
        _require(type(gate["eligible"]) is bool and gate["excluded_original_fill"] == (not gate["eligible"] and original_fill),
                 "Eligibility exclusion differs from original fill")
        eligibility["eligible_events" if gate["eligible"] else "ineligible_events"] += 1
        eligibility["eligible_original_fills"] += int(gate["eligible"] and original_fill)
        eligibility["excluded_original_fills"] += int(gate["excluded_original_fill"])
        eligibility["reasons"][gate["reason"]] += 1
        names.setdefault(symbol, totals())
        for name, book in books.items():
            for group in (arms, dates[day], names[symbol]):
                _add(group[name], book)
            week = weekly[weeks[day]][name]
            week["fills"] += int(book["fill_state"] == "filled")
            week["unknown_fills"] += int(book["fill_state"] == "unknown")
            week["wins"] += int(book["cash_state"] == "settled" and Fraction(book["net_cash"]) > 0)
            reasons[name][book["reason"]] += 1
            if book["exit_reason"] is not None:
                exits[name][book["exit_reason"]] += 1
        for name, (left_name, right_name) in COMPARISONS.items():
            left, right = books[left_name], books[right_name]
            pair = comparisons[name]
            ls, rs = left["fill_state"], right["fill_state"]
            category = ("unknown" if "unknown" in (ls, rs) else "both_filled" if ls == rs == "filled"
                        else "left_only" if ls == "filled" else "right_only" if rs == "filled" else "neither")
            pair["categories"][category] += 1
            attribution._pair_add(pair["whole"], left, right)
            if left["cash_state"] == right["cash_state"] == "settled":
                attribution._pair_add(pair["common_settled"], left, right)
            lu, ru = left["cash_state"] == "unknown", right["cash_state"] == "unknown"
            if lu or ru:
                pair["unknown_patterns"]["both_unknown" if lu and ru else "left_unknown_only" if lu else "right_unknown_only"] += 1
    return attribution._json_ready({"schema": "EM-exit-followup-summary-v1", "opportunities": len(seen),
        "calendar_sessions": len(calendar), "calendar_weeks": len(set(weeks.values())),
        "arms": arms, "comparisons": comparisons, "eligibility": eligibility, "weekly": weekly,
        "cost_trigger_counts": cost_triggers,
        "exit_reasons": exits, "outcome_reasons": reasons, "concentration": {"dates": dates, "names": names}})
