"""Independent clocks and exact cash for the two registered EM exit comparisons.

Only the already reviewed independent EL-ledger audit is reused. This module
does not import the follow-up producer or an execution simulator, and receives
separately decoded minutes rather than opening any input files.
"""

from collections import Counter
from datetime import date
from fractions import Fraction

from research.intraday_event_attribution_reference import _book


_COST = Fraction(33, 10_000)
_FIXED = ("confirmation", "reference_limit_one_shot", "fixed_exit")
_FORECAST = ("confirmation", "reference_limit_one_shot", "forecast_full_exit")
_NAMES = ("original_fixed30_all", "cost_close_exit30_all", "original_forecast_full30_all",
          "original_fixed30_time_eligible", "fixed60_time_eligible")


def _check(condition, message):
    if not condition:
        raise ValueError("EM follow-up reference: " + message)


def _number(value):
    _check(type(value) in (str, int, Fraction), "exact number required")
    return Fraction(value)


def _named(source, name):
    return dict(source, name=name, source_cash_state=source["cash_state"],
                trigger_offset=None, trigger_receipt_offset=None, unknown_offset=None)


def _unknown(book, reason, clock):
    book.update(status="unresolved", reason=reason, cash_state="unknown", gross_cash=None,
                net_cash=None, cost_cash=None, exit_offset=None, exit_reason=None,
                unknown_offset=clock)
    return book


def _liquidate(book, market, clock, reason):
    bar = market.bars.get(clock)
    if bar is None:
        return _unknown(book, "exit_open_unknown", clock)
    if bar.available_offset >= market.session_length:
        return _unknown(book, "outcome_not_mature_in_session", clock)
    basis = _number(book["entry_notional"])
    gross = book["filled_quantity"] * _number(bar.open) - basis
    fee = basis * _COST
    book.update(status="closed", reason=reason, cash_state="settled", gross_cash=str(gross),
                net_cash=str(gross - fee), cost_cash=str(fee), exit_offset=clock,
                exit_reason=reason)
    return book


def _replay(source, row, market, name, horizon, *, take_cost):
    """Rebuild the entire received held prefix at every decision clock."""
    book = _named(source, name)
    if source["fill_state"] != "filled":
        return book
    entry = source["entry_offset"]
    expiry = entry + horizon
    _check(market is not None and expiry < market.session_length, "new expiry leaves original session")
    stop = _number(row["execution"]["structure_stop"])
    threshold = _number(source["entry_price"]) * (1 + _COST)
    scheduled = None
    for clock in range(entry + 1, expiry + 1):
        if scheduled == "structural":
            return _liquidate(book, market, clock, "structural")
        if clock == expiry:
            return _liquidate(book, market, clock, "time")
        if scheduled == "cost_close":
            return _liquidate(book, market, clock, "cost_close")
        due = clock - 3
        if due >= entry:
            required = market.bars.get(due)
            if required is None or required.available_offset > clock:
                return _unknown(book, "held_close_unknown", clock)
        cost_witness, prior_receipt = None, -1
        for offset in range(entry, clock):
            bar = market.bars.get(offset)
            if bar is None:
                break
            if bar.available_offset > clock:
                if any(later.available_offset <= clock for offset, later in market.bars.items()
                       if bar.offset < offset < clock):
                    return _unknown(book, "nonchronological_close_availability", clock)
                break
            if bar.available_offset < prior_receipt:
                return _unknown(book, "nonchronological_close_availability", clock)
            prior_receipt = bar.available_offset
            if _number(bar.close) <= stop:
                scheduled = "structural"
                break
            if take_cost and cost_witness is None and _number(bar.close) > threshold:
                cost_witness = bar
        if scheduled == "structural":
            continue
        if cost_witness is not None:
            scheduled = "cost_close"
            book.update(trigger_offset=cost_witness.offset,
                        trigger_receipt_offset=cost_witness.available_offset)
    raise ValueError("EM follow-up reference: no terminal clock")


def _excluded(source, name):
    book = _named(source, name)
    book.update(committed=False, status="no_entry", reason="horizon_time_ineligible",
                fill_state="known_no_fill", cash_state="known_zero", filled_quantity=0,
                entry_offset=None, entry_price=None, entry_notional=None, gross_cash="0",
                net_cash="0", cost_cash="0", exit_offset=None, exit_reason=None)
    return book


def verify_event(roster, ledgers, market, saved, *, minimum_receipt_lag=1):
    """Reconstruct original books and new exits, then compare every saved field."""
    _check(type(minimum_receipt_lag) is int and minimum_receipt_lag > 0,
           "positive integer minimum receipt lag required")
    if market is not None:
        _check(all(type(offset) is int and bar.offset == offset
                   and bar.available_offset >= offset + minimum_receipt_lag
                   for offset, bar in market.bars.items()), "minute violates minimum receipt lag")
    source = _book(roster, _FIXED, ledgers[_FIXED], market)
    forecast = _book(roster, _FORECAST, ledgers[_FORECAST], market)
    fields = ("decision_offset", "planned_entry_offset", "expiry_offset", "reference_close",
              "guard_cap", "structure_stop", "requested_quantity", "filled_quantity", "entry_price")
    signatures = [tuple(row.get(field) for field in ("action", "candidate_key", "entry_decision_offset"))
                  + tuple(row["execution"].get(field) for field in fields)
                  for row in (ledgers[_FIXED], ledgers[_FORECAST])]
    _check(signatures[0] == signatures[1], "management changed original request or purchase")
    row = ledgers[_FIXED]
    entry = row["execution"].get("planned_entry_offset") if source["committed"] else None
    length = None if market is None else market.session_length
    _check(entry is None or market is not None, "original request needs its session calendar")
    eligible = entry is not None and length is not None and entry + 60 + 3 < length
    reason = "eligible" if eligible else "no_original_request" if entry is None else "insufficient_session_time"
    eligibility = dict(eligible=eligible, reason=reason, planned_entry_offset=entry,
                       session_length=length, excluded_original_fill=not eligible and source["fill_state"] == "filled")
    arms = [_named(source, _NAMES[0]), _replay(source, row, market, _NAMES[1], 30, take_cost=True),
            _named(forecast, _NAMES[2])]
    arms.extend((_named(source, _NAMES[3]), _replay(source, row, market, _NAMES[4], 60, take_cost=False))
                if eligible else (_excluded(source, _NAMES[3]), _excluded(source, _NAMES[4])))
    result = dict(schema="EM-exit-followup-event-v1", roster=dict(roster),
                  eligibility=eligibility, arms=arms)
    _check(result == saved, "event reconstruction differs from saved record")
    return result


def _aggregate(books):
    books = tuple(books)
    settled = [book for book in books if book["cash_state"] == "settled"]
    filled = [book for book in books if book["fill_state"] == "filled"]
    result = dict(opportunities=len(books), commits=sum(book["committed"] for book in books),
                  fills=len(filled), known_no_fills=sum(book["fill_state"] == "known_no_fill" for book in books),
                  unknown_fills=sum(book["fill_state"] == "unknown" for book in books),
                  settled_fills=len(settled), unknown_cash=sum(book["cash_state"] == "unknown" for book in books),
                  wins=sum(_number(book["net_cash"]) > 0 for book in settled))
    for field in ("gross_cash", "net_cash", "cost_cash", "entry_notional"):
        result[field] = str(sum((_number(book[field]) for book in settled), Fraction()))
    result["all_fill_entry_notional"] = str(sum((_number(book["entry_notional"]) for book in filled), Fraction()))
    return result


def _paired(pairs):
    pairs = tuple(pairs)
    known = [(left, right) for left, right in pairs if left["net_cash"] is not None and right["net_cash"] is not None]
    amounts = [sum((_number(pair[side]["net_cash"]) for pair in known), Fraction()) for side in (0, 1)]
    return dict(known_pairs=len(known), unknown_pairs=len(pairs) - len(known),
                left_net_cash=str(amounts[0]), right_net_cash=str(amounts[1]),
                delta_net_cash=str(amounts[1] - amounts[0]))


def _fill_category(pair):
    states = tuple(book["fill_state"] for book in pair)
    if "unknown" in states:
        return "unknown"
    return {(True, True): "both_filled", (True, False): "left_only", (False, True): "right_only",
            (False, False): "neither"}[tuple(state == "filled" for state in states)]


def verify_summary(calendar, records, summary):
    """Aggregate independent records by groups, preserving every calendar week."""
    calendar, records = tuple(calendar), tuple(records)
    _check(calendar and tuple(sorted(set(calendar))) == calendar, "ordered unique calendar required")
    weeks = {}
    for day in calendar:
        parsed = date.fromisoformat(day)
        _check(parsed.isoformat() == day, "canonical ISO date required")
        year, week, _ = parsed.isocalendar()
        weeks[day] = f"{year}-W{week:02}"
    seen, indexed = set(), []
    for record in records:
        roster = record["roster"]
        _check(record["schema"] == "EM-exit-followup-event-v1" and roster["session"] in weeks
               and roster["event_id"] not in seen, "duplicate or off-calendar original episode")
        seen.add(roster["event_id"])
        arms = {book["name"]: book for book in record["arms"]}
        _check(len(record["arms"]) == len(_NAMES) and set(arms) == set(_NAMES), "duplicate or absent arm")
        gate = record["eligibility"]
        _check(type(gate["eligible"]) is bool and gate["excluded_original_fill"] ==
               (not gate["eligible"] and arms[_NAMES[0]]["fill_state"] == "filled"),
               "eligibility exclusion differs from original fill")
        indexed.append((record, arms))
    eligible = [(record, arms) for record, arms in indexed if record["eligibility"]["eligible"]]
    triggered = [arms[_NAMES[1]] for _, arms in indexed if arms[_NAMES[1]]["trigger_offset"] is not None]
    result = dict(schema="EM-exit-followup-summary-v1", opportunities=len(records),
                  calendar_sessions=len(calendar), calendar_weeks=len(set(weeks.values())),
                  arms={name: _aggregate(arms[name] for _, arms in indexed) for name in _NAMES},
                  comparisons={}, weekly={}, exit_reasons={}, outcome_reasons={},
                  concentration=dict(dates={}, names={}), eligibility=dict(
                      eligible_events=len(eligible), ineligible_events=len(records) - len(eligible),
                      eligible_original_fills=sum(arms[_NAMES[0]]["fill_state"] == "filled" for _, arms in eligible),
                      excluded_original_fills=sum(record["eligibility"]["excluded_original_fill"] for record in records),
                      reasons=dict(Counter(record["eligibility"]["reason"] for record in records))))
    result["cost_trigger_counts"] = dict(
        triggered=len(triggered),
        settled_cost_exit=sum(book["cash_state"] == "settled" and book["exit_reason"] == "cost_close"
                              for book in triggered),
        triggered_time_exit=sum(book["cash_state"] == "settled" and book["exit_reason"] == "time"
                                for book in triggered),
        triggered_unknown_cash=sum(book["cash_state"] == "unknown" for book in triggered))
    comparisons = (("cost_close_vs_fixed30", _NAMES[0], _NAMES[1]),
                   ("cost_close_vs_forecast_full30", _NAMES[2], _NAMES[1]),
                   ("fixed60_vs_fixed30_time_eligible", _NAMES[3], _NAMES[4]))
    for label, left, right in comparisons:
        pairs = [(arms[left], arms[right]) for _, arms in indexed]
        categories = {key: sum(_fill_category(pair) == key for pair in pairs)
                      for key in ("both_filled", "left_only", "right_only", "neither", "unknown")}
        unknowns = {"both_unknown": 0, "left_unknown_only": 0, "right_unknown_only": 0}
        for pair in pairs:
            state = tuple(book["cash_state"] == "unknown" for book in pair)
            if any(state):
                unknowns[{(True, True): "both_unknown", (True, False): "left_unknown_only",
                          (False, True): "right_unknown_only"}[state]] += 1
        result["comparisons"][label] = dict(left=left, right=right, whole=_paired(pairs),
            common_settled=_paired(pair for pair in pairs if all(book["cash_state"] == "settled" for book in pair)),
            categories=categories, unknown_patterns=unknowns)
    for week in sorted(set(weeks.values())):
        group = [arms for record, arms in indexed if weeks[record["roster"]["session"]] == week]
        result["weekly"][week] = {name: dict(
            fills=sum(arms[name]["fill_state"] == "filled" for arms in group),
            wins=sum(arms[name]["cash_state"] == "settled" and _number(arms[name]["net_cash"]) > 0 for arms in group),
            unknown_fills=sum(arms[name]["fill_state"] == "unknown" for arms in group)) for name in _NAMES}
    for name in _NAMES:
        result["exit_reasons"][name] = dict(Counter(arms[name]["exit_reason"] for _, arms in indexed
                                                   if arms[name]["exit_reason"] is not None))
        result["outcome_reasons"][name] = dict(Counter(arms[name]["reason"] for _, arms in indexed))
    for dimension, field, values in (("dates", "session", calendar),
                                     ("names", "symbol", sorted({record["roster"]["symbol"] for record in records}))):
        result["concentration"][dimension] = {value: {
            name: _aggregate(arms[name] for record, arms in indexed if record["roster"][field] == value)
            for name in _NAMES} for value in values}
    _check(result == summary, "summary reconstruction differs from saved report")
    return result
