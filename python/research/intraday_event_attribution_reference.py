"""Producer-independent exact reconstruction for the registered EM diagnostic.

The caller supplies separately decoded original minutes and authenticated EL
ledger rows. No producer calculations, execution helpers or fitted models are
imported here. Outcome maturity remains authoritative about what is knowable;
an execution's cash balance cannot turn an unknown outcome into a known one.
"""

from collections import Counter
from datetime import date
from fractions import Fraction
from itertools import product


_ARMS = tuple(product(("onset", "confirmation"),
                      ("next_open_capped", "reference_limit_one_shot"),
                      ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")))
_CATEGORIES = ("both_filled", "left_only", "right_only", "neither", "unknown")
_COST = Fraction(33, 10_000)


def _check(condition, message):
    if not condition:
        raise ValueError("EM reference: " + message)


def _number(value):
    _check(type(value) in (str, int, Fraction), "exact number required")
    return Fraction(value)


def _integer(value, *, positive=False):
    _check(type(value) is int and value >= int(positive), "integer quantity/clock required")
    return value


def _name(arm):
    return "|".join(arm)


def _quote(market, offset):
    bar = market.bars.get(offset)
    _check(bar is None or bar.offset == offset, "minute identity differs")
    return bar


def _fixed_boundary(market, entry, expiry, stop):
    """Find the first receipt-triggered stop/time sale without saved exit totals."""
    cursor, previous_receipt, liquidation = entry, -1, None
    for clock in range(entry + 1, expiry + 1):
        if liquidation is not None:
            return liquidation, "structural"
        if clock == expiry:
            return clock, "time"
        overdue = clock - 3
        if overdue >= entry:
            quote = _quote(market, overdue)
            if quote is None or quote.available_offset > clock:
                return None
        for offset in range(cursor, clock):
            quote = _quote(market, offset)
            if quote is None:
                if offset + 3 <= clock:
                    return None
                break
            if quote.available_offset > clock:
                later = [_quote(market, at) for at in range(offset + 1, clock)]
                if any(bar is not None and bar.available_offset <= clock for bar in later):
                    return None
                break
            if quote.available_offset < previous_receipt:
                return None
            previous_receipt = quote.available_offset
            cursor = offset + 1
            if _number(quote.close) <= stop:
                liquidation = clock + 1
                break
    return None


def _audit_execution(row, market):
    """Reprice the saved purchase/sales and recompute the frozen integer size."""
    execution = row["execution"]
    if row["action"] == "no_commit":
        _check(execution["filled_quantity"] == 0 and not execution["sales"],
               "uncommitted execution contains a fill")
        return
    decision = row["entry_decision_offset"]
    _integer(decision)
    entry = decision + 1
    expiry = entry + 30
    cap = _number(execution["guard_cap"])
    stop = _number(execution["structure_stop"])
    reference = _number(execution["reference_close"])
    _check(0 < stop < cap and 0 < reference < cap, "invalid causal request prices")
    if market is not None:
        prefix = [offset for offset, bar in market.bars.items()
                  if offset < decision and bar.available_offset <= decision]
        _check(prefix and _number(_quote(market, max(prefix)).close) == reference,
               "request reference is not the last received causal close")
    _check(execution["decision_offset"] == decision
           and execution["planned_entry_offset"] == entry
           and execution["expiry_offset"] == expiry
           and execution["cost_bps"] == 33
           and _number(execution["initial_capital"]) == 100_000_000
           and _number(execution["first_entry_risk_budget"]) == 50_000,
           "execution clock/cost/capital changed")
    risk_denominator = cap - stop + cap * _COST
    risk_size = Fraction(50_000) / risk_denominator
    cash_size = Fraction(2_000_000) / cap
    expected = min(risk_size.numerator // risk_size.denominator,
                   cash_size.numerator // cash_size.denominator)
    _check(type(execution["requested_quantity"]) is int
           and execution["requested_quantity"] == expected, "reserved integer size differs")
    actual = execution["filled_quantity"]
    opening = None if market is None else _quote(market, entry)
    if (market is not None and expiry >= market.session_length) or expected == 0:
        _check(actual == 0 and not execution["sales"], "ineligible request filled")
        return
    if market is not None and opening is None:
        _check(actual is None and not execution["sales"], "missing purchase opening became known")
        return
    if market is None and not actual:
        _check(not execution["sales"], "unfilled execution contains a sale")
        return
    purchase = _number(execution["entry_price"] if market is None else opening.open)
    limit = cap if row["entry_policy"] == "next_open_capped" else reference
    if not stop < purchase <= limit:
        _check(actual == 0 and not execution["sales"], "price-rejected request filled")
        return
    _check(type(actual) is int and actual == expected
           and execution["entry_offset"] == entry
           and _number(execution["entry_price"]) == purchase,
           "modeled purchase differs from original opening")
    basis = expected * purchase
    fee = basis * _COST
    _check(_number(execution["entry_notional"]) == basis
           and _number(execution["entry_cost"]) == fee, "purchase basis/cost differs")
    if market is not None and row["management"] == "fixed_exit":
        boundary = _fixed_boundary(market, entry, expiry, stop)
        sales = execution["sales"]
        _check((not sales and boundary is None) or
               (len(sales) == 1 and boundary == (sales[0]["execute_offset"], sales[0]["reason"])),
               "fixed sale differs from original structural/time boundary")
    sold, proceeds, prior_offset = 0, Fraction(), entry
    for leg in execution["sales"]:
        quantity = _integer(leg["quantity"], positive=True)
        offset = _integer(leg["execute_offset"], positive=True)
        _check(prior_offset < offset <= expiry and sold + quantity <= expected,
               "sale quantity/clock exceeds the original position")
        prior_offset = offset
        quote = None if market is None else _quote(market, offset)
        if (market is not None and quote is None) or (market is None and leg["price"] is None):
            _check(leg["price"] is None and execution["net"] is None,
                   "missing sale opening became settled")
            continue
        price = _number(leg["price"] if market is None else quote.open)
        gross, allocated = quantity * (price - purchase), purchase * quantity * _COST
        _check(_number(leg["price"]) == price and _number(leg["gross"]) == gross
               and _number(leg["allocated_cost"]) == allocated
               and _number(leg["net"]) == gross - allocated,
               "sale cash differs from independent minute opening/quantity")
        _check(leg["reason"] in ("time", "structural", "forecast_half", "forecast_full"),
               "unknown sale reason")
        if leg["reason"] == "time":
            _check(offset == expiry, "time sale changed expiry")
        else:
            _check(leg["decision_offset"] + 1 == offset, "sale is not next-open causal")
        if leg["reason"].startswith("forecast_"):
            _check(row["management"] != "fixed_exit" and offset in (entry + 11, entry + 21),
                   "forecast sale outside held checkpoints")
        if leg["reason"] == "forecast_half":
            _check(row["management"] == "forecast_half_then_exit" and quantity == expected // 2,
                   "partial sale changed integer half size")
        sold += quantity
        proceeds += quantity * price
    if execution["net"] is not None:
        _check(sold == expected and execution["remaining_quantity"] == 0,
               "known execution cash retains unsold quantity")
        gross = proceeds - basis
        _check(_number(execution["gross"]) == gross and _number(execution["cost"]) == fee
               and _number(execution["net"]) == gross - fee
               and _number(execution["cash"]) == 100_000_000 + gross - fee,
               "closed execution cash/cost differs")


def _book(roster, arm, row, market):
    control, entry_policy, management = arm
    _check(tuple(row[field] for field in ("control", "entry_policy", "management")) == arm
           and (row["model"], row["scope"], row["family"]) == ("tcn", "common_chart_peer", "C_FIRSTPULLBACK")
           and tuple(row[field] for field in ("original_episode_id", "session", "symbol", "cluster_id"))
           == tuple(roster[field] for field in ("event_id", "session", "symbol", "cluster_id")),
           "ledger identity differs from sealed roster")
    committed = row["action"] != "no_commit"
    if committed:
        at = row["entry_decision_offset"]
        _integer(at)
        _check(row["action"] == "immediate" and row["candidate_key"] == f"{roster['event_id']}@{at}"
               and row["selection_reason"] == "fixed_" + control,
               "unexpected causal selection")
        if control == "onset":
            _check(at == roster["onset_decision_offset"], "onset clock differs")
        else:
            _check(row["state"] == "CONFIRMED" and row["candidate_key"] == roster["confirmation_key"]
                   and at == roster["confirmation_decision_offset"]
                   and roster["confirmation_reference"] is not None
                   and _number(row["execution"]["reference_close"]) == _number(roster["confirmation_reference"]),
                   "confirmation identity/reference differs")
    else:
        _check(row["candidate_key"] is None and row["entry_decision_offset"] is None,
               "unkeyed no-commit identity differs")
    _audit_execution(row, market)
    outcome, execution = row["outcome"], row["execution"]
    quantity = outcome["filled_quantity"]
    _check(quantity is None or type(quantity) is int and quantity >= 0, "invalid outcome quantity")
    _check((outcome["net"] is None) == (outcome["gross"] is None), "one-sided unknown cash")
    known = outcome["net"] is not None
    fill = "unknown" if quantity is None else "filled" if quantity > 0 else "known_no_fill"
    basis, purchase, entry = None, None, None
    if quantity:
        _check(quantity == execution["filled_quantity"], "outcome/execution fill differs")
        purchase = _number(execution["entry_price"])
        entry = execution["entry_offset"]
        basis = quantity * purchase
        _check(_number(outcome["entry_notional"]) == basis
               and (market is None or _quote(market, entry).available_offset < market.session_length),
               "outcome notional or fill maturity differs")
    elif quantity == 0:
        _check(execution["filled_quantity"] == 0 and _number(outcome["entry_notional"]) == 0,
               "known no-fill has an execution fill")
        if market is not None and committed and execution["status"] != "no_entry":
            _check(_quote(market, row["entry_decision_offset"] + 1).available_offset < market.session_length,
                   "unmatured no-fill became known")
    gross = net = cost = None
    exit_offset = exit_reason = None
    if known:
        _check(quantity is not None, "known cash with unknown fill")
        if quantity:
            _check(execution["status"] == "closed"
                   and sum(leg["quantity"] for leg in execution["sales"]) == quantity
                   and (market is None or all(_quote(market, leg["execute_offset"]).available_offset < market.session_length
                           for leg in execution["sales"])), "known cash lacks mature settled legs")
            gross = sum((leg["quantity"] * _number(leg["price"]) for leg in execution["sales"]), Fraction()) - basis
            cost = basis * 33 / 10_000
            net = gross - cost
            exit_offset, exit_reason = execution["sales"][-1]["execute_offset"], execution["sales"][-1]["reason"]
        else:
            gross = net = cost = Fraction()
            _check(_number(execution["gross"]) == 0 and _number(execution["net"]) == 0
                   and not execution["sales"], "known no-fill execution cash is not zero")
        _check(_number(outcome["gross"]) == gross and _number(outcome["net"]) == net,
               "outcome cash differs from reconstructed legs")
    _check(committed or quantity == 0 and known and net == 0, "uncommitted outcome is not known zero")
    return dict(control=control, entry_policy=entry_policy, management=management,
                committed=committed, selection_reason=row["selection_reason"],
                status=outcome["status"], reason=outcome["reason"], fill_state=fill,
                cash_state=("settled" if quantity else "known_zero") if known else "unknown",
                filled_quantity=quantity, entry_offset=entry,
                entry_price=None if purchase is None else str(purchase),
                entry_notional=None if basis is None else str(basis),
                gross_cash=None if gross is None else str(gross),
                net_cash=None if net is None else str(net), cost_cash=None if cost is None else str(cost),
                exit_offset=exit_offset, exit_reason=exit_reason)


def _path(book, row, market):
    result = dict(status="not_filled", entry_offset=book["entry_offset"], exit_offset=None,
                  missing_offsets=[], observed_openings=0, observed_cost_exceeded=False,
                  ever_cost_exceeded=None, max_open_return=None, min_open_return=None,
                  max_open_offset=None, min_open_offset=None, final_sale_return=None,
                  category="not_applicable", first_cost_close=None)
    if book["fill_state"] != "filled":
        return result
    result.update(status="unknown_boundary", category="unknown_path")
    sales = row["execution"]["sales"]
    if len(sales) != 1 or sales[0]["reason"] not in ("structural", "time"):
        return result
    first, last = book["entry_offset"] + 1, sales[0]["execute_offset"]
    _check(first <= last <= row["execution"]["expiry_offset"], "path leaves original exit boundary")
    result["exit_offset"] = last
    purchase = _number(book["entry_price"])
    observations = []
    for offset in range(first, last + 1):
        quote = _quote(market, offset)
        if quote is None or quote.available_offset >= market.session_length:
            result["missing_offsets"].append(offset)
        else:
            observations.append((offset, quote))
    complete = not result["missing_offsets"]
    returns = [(offset, _number(quote.open) / purchase - 1) for offset, quote in observations]
    exceeded = any(value > _COST for _, value in returns)
    result.update(status="complete" if complete else "incomplete",
                  observed_openings=len(returns), observed_cost_exceeded=exceeded,
                  ever_cost_exceeded=True if exceeded else False if complete else None)
    final = None
    if book["cash_state"] == "settled":
        final = _number(sales[0]["price"]) / purchase - 1
        result["final_sale_return"] = str(final)
    if complete:
        maximum, minimum = max(value for _, value in returns), min(value for _, value in returns)
        result.update(max_open_return=str(maximum), min_open_return=str(minimum),
                      max_open_offset=next(offset for offset, value in returns if value == maximum),
                      min_open_offset=next(offset for offset, value in returns if value == minimum))
        if maximum < _COST:
            category = "never_exceeded"
        elif maximum == _COST:
            category = "equal_only"
        elif final is None:
            category = "exceeded_final_unknown"
        else:
            category = "exceeded_retained" if final > _COST else "exceeded_then_equal" if final == _COST else "exceeded_then_lost"
        result["category"] = category
    received = [(offset, _quote(market, offset)) for offset in range(first - 1, last + 1)]
    crossings = sorted((quote.available_offset, offset, quote)
                       for offset, quote in received if quote is not None
                       and quote.available_offset < market.session_length
                       and _number(quote.close) / purchase - 1 > _COST)
    if crossings:
        receipt, offset, quote = crossings[0]
        next_offset = receipt + 1
        following = None if next_offset > last else _quote(market, next_offset)
        status = ("out_of_window" if next_offset > last else "missing"
                  if following is None or following.available_offset >= market.session_length else "known")
        price = _number(following.open) if status == "known" else None
        prior_complete = all(bar is not None and bar.available_offset < market.session_length
                             for at, bar in received if at < receipt)
        result["first_cost_close"] = dict(offset=offset, receipt_offset=receipt,
            close=str(_number(quote.close)), first_is_known=prior_complete, opening_offset=next_offset,
            opening_status=status, opening_price=None if price is None else str(price),
            opening_return=None if price is None else str(price / purchase - 1))
    return result


def verify_event(roster, ledgers_by_arm, market, saved):
    """Rebuild one sealed episode from separate prices and return its exact record."""
    _check(set(ledgers_by_arm) == set(_ARMS), "all twelve distinct arms required")
    arms = [_book(roster, arm, ledgers_by_arm[arm], market) for arm in _ARMS]
    for control, policy in product(("onset", "confirmation"),
                                   ("next_open_capped", "reference_limit_one_shot")):
        rows = [ledgers_by_arm[control, policy, management]
                for management in ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")]
        request_fields = ("decision_offset", "planned_entry_offset", "expiry_offset", "reference_close",
                          "guard_cap", "structure_stop", "requested_quantity")
        identities = [(row["action"], row["candidate_key"], row["entry_decision_offset"],
                       *(row["execution"].get(field) for field in request_fields),
                       row["execution"]["filled_quantity"], row["execution"]["entry_price"]) for row in rows]
        _check(all(identity == identities[0] for identity in identities), "management changed initial entry")
        capped = ledgers_by_arm[control, "next_open_capped", "fixed_exit"]
        _check(all(rows[0]["execution"].get(field) == capped["execution"].get(field) for field in request_fields),
               "entry policies changed original request")
        if rows[0]["execution"]["filled_quantity"]:
            _check((rows[0]["execution"]["filled_quantity"], rows[0]["execution"]["entry_price"])
                   == (capped["execution"]["filled_quantity"], capped["execution"]["entry_price"]),
                   "reference fill is not the same capped purchase")
    target = ("confirmation", "reference_limit_one_shot", "fixed_exit")
    selected = arms[_ARMS.index(target)]
    result = dict(schema="EM-event-attribution-v1", roster=dict(roster), arms=arms,
                  path=_path(selected, ledgers_by_arm[target], market))
    _check(result == saved, "event reconstruction differs from saved record")
    return result


def _totals(rows):
    result = dict.fromkeys(("opportunities", "commits", "fills", "known_no_fills", "unknown_fills",
                            "settled_fills", "unknown_cash"), 0)
    money = dict.fromkeys(("gross_cash", "net_cash", "cost_cash", "entry_notional"), Fraction())
    for row in rows:
        result["opportunities"] += 1
        result["commits"] += row["committed"]
        result["fills"] += row["fill_state"] == "filled"
        result["known_no_fills"] += row["fill_state"] == "known_no_fill"
        result["unknown_fills"] += row["fill_state"] == "unknown"
        result["unknown_cash"] += row["cash_state"] == "unknown"
        if row["cash_state"] == "settled":
            result["settled_fills"] += 1
            for field in money:
                money[field] += _number(row[field])
    result.update({field: str(value) for field, value in money.items()})
    return result


def _pairs(pairs):
    pairs = list(pairs)
    known = [(left, right) for left, right in pairs if left["net_cash"] is not None and right["net_cash"] is not None]
    left = sum((_number(row["net_cash"]) for row, _ in known), Fraction())
    right = sum((_number(row["net_cash"]) for _, row in known), Fraction())
    return dict(known_pairs=len(known), unknown_pairs=len(pairs) - len(known),
                left_net_cash=str(left), right_net_cash=str(right), delta_net_cash=str(right - left))


def _category(left, right):
    if "unknown" in (left["fill_state"], right["fill_state"]):
        return "unknown"
    filled = left["fill_state"] == "filled", right["fill_state"] == "filled"
    return {(True, True): "both_filled", (True, False): "left_only",
            (False, True): "right_only", (False, False): "neither"}[filled]


def verify_summary(calendar, saved_records, summary):
    """Independently aggregate audited records, keeping zero calendar dates/weeks."""
    calendar, records = tuple(calendar), tuple(saved_records)
    _check(calendar and tuple(sorted(set(calendar))) == calendar, "complete ordered calendar required")
    weeks = {}
    for day in calendar:
        parsed = date.fromisoformat(day)
        _check(parsed.isoformat() == day, "ISO date required")
        year, week, _ = parsed.isocalendar()
        weeks[day] = f"{year}-W{week:02}"
    seen, indexed = set(), []
    for record in records:
        roster = record["roster"]
        _check(record["schema"] == "EM-event-attribution-v1" and roster["event_id"] not in seen
               and roster["session"] in weeks, "duplicate or off-calendar episode")
        seen.add(roster["event_id"])
        arms = {tuple(row[field] for field in ("control", "entry_policy", "management")): row for row in record["arms"]}
        _check(len(record["arms"]) == 12 and set(arms) == set(_ARMS), "summary lost an arm")
        indexed.append((record, arms))
    terminal = Counter(record["roster"]["terminal_state"] for record in records)
    confirmations = Counter("absent" if record["roster"]["confirmation_key"] is None else
                            "unsupported_reference" if record["roster"]["confirmation_reference"] is None else "available"
                            for record in records)
    result = dict(schema="EM-attribution-summary-v1", opportunities=len(records),
                  calendar_sessions=len(calendar), calendar_weeks=len(set(weeks.values())),
                  funnel=dict(terminal_states=dict(terminal), confirmation_status=dict(confirmations), arms={
                      _name(arm): {plural: dict(Counter(arms[arm][field] for _, arms in indexed))
                                   for plural, field in (("selection_reasons", "selection_reason"),
                                                         ("statuses", "status"), ("reasons", "reason"))}
                      for arm in _ARMS}),
                  arms={_name(arm): _totals(arms[arm] for _, arms in indexed) for arm in _ARMS},
                  comparisons={}, paths=dict(Counter(record["path"]["category"] for record in records)),
                  weekly={}, concentration=dict(dates={}, names={}))
    paths = [record["path"] for record in records]
    witnesses = [path["first_cost_close"] for path in paths if path["first_cost_close"] is not None]
    result["path_observations"] = dict(statuses=dict(Counter(path["status"] for path in paths)),
        observed_cost_exceeded=sum(path["observed_cost_exceeded"] for path in paths),
        known_never_cost_exceeded=sum(path["ever_cost_exceeded"] is False for path in paths),
        unknown_ever_cost_exceeded=sum(path["status"] != "not_filled" and path["ever_cost_exceeded"] is None for path in paths),
        close_witnesses=len(witnesses), known_first_close_witnesses=sum(witness["first_is_known"] for witness in witnesses),
        trigger_openings=dict(Counter(witness["opening_status"] for witness in witnesses)))
    comparisons = []
    for policy in ("next_open_capped", "reference_limit_one_shot"):
        comparisons.append(("timing|" + policy, ("onset", policy, "fixed_exit"),
                            ("confirmation", policy, "fixed_exit"), True))
        for management in ("forecast_full_exit", "forecast_half_then_exit"):
            comparisons.append(("management|" + policy + "|" + management,
                                ("confirmation", policy, "fixed_exit"), ("confirmation", policy, management), False))
    comparisons.append(("entry", ("confirmation", "next_open_capped", "fixed_exit"),
                        ("confirmation", "reference_limit_one_shot", "fixed_exit"), False))
    for label, left, right, confounded in comparisons:
        pairs = [(arms[left], arms[right]) for _, arms in indexed]
        categories = {category: [pair for pair in pairs if _category(*pair) == category] for category in _CATEGORIES}
        common = [pair for pair in pairs if all(row["fill_state"] == "filled" and row["cash_state"] == "settled" for row in pair)]
        result["comparisons"][label] = dict(confounded=confounded,
            categories={category: len(values) for category, values in categories.items()},
            whole=_pairs(pairs), common_settled=_pairs(common),
            cohort_cash={category: _pairs(values) for category, values in categories.items()})
    for week in sorted(set(weeks.values())):
        week_rows = [arms for record, arms in indexed if weeks[record["roster"]["session"]] == week]
        result["weekly"][week] = {_name(arm): dict(
            fills=sum(arms[arm]["fill_state"] == "filled" for arms in week_rows),
            wins=sum(arms[arm]["cash_state"] == "settled" and _number(arms[arm]["net_cash"]) > 0 for arms in week_rows),
            unknown_fills=sum(arms[arm]["fill_state"] == "unknown" for arms in week_rows)) for arm in _ARMS}
    for dimension, field, values in (("dates", "session", calendar),
                                     ("names", "symbol", sorted({record["roster"]["symbol"] for record in records}))):
        for value in values:
            result["concentration"][dimension][value] = {
                _name(arm): _totals(arms[arm] for record, arms in indexed if record["roster"][field] == value)
                for arm in _ARMS}
    _check(result == summary, "summary reconstruction differs from saved report")
    return result


def verify_source_support(coordinates, support_rows, roster, saved):
    """Check each original coordinate and expand the C support runs independently."""
    lengths, price_states, support_states = {}, {}, {}
    result = dict.fromkeys(("coordinates", "continuous_minutes", "price_absent_coordinates",
                           "price_partial_coordinates", "price_complete_coordinates",
                           "first60_price_complete_coordinates"), 0)
    for row in coordinates:
        key = row["session"], row["symbol"]
        length = _integer(row["continuous_length"], positive=True)
        _check(key not in lengths, "duplicate original source coordinate")
        lengths[key] = length
        minutes = row["minute_states"]
        _check(len(minutes) >= length, "source minute prefix is incomplete")
        observed, first60 = 0, 0
        for offset in range(length):
            item = minutes[offset]
            state = item["price"]
            _check(type(item["offset"]) is int and item["offset"] == offset
                   and type(state) is str and state, "source minute identity/state differs")
            price_states[state] = price_states.get(state, 0) + 1
            if state == "observed":
                observed += 1
                first60 += offset < 60
        _check(type(row["continuous_price_observed"]) is int
               and observed == row["continuous_price_observed"], "original observed-price count differs")
        result["coordinates"] += 1
        result["continuous_minutes"] += length
        result["price_absent_coordinates"] += observed == 0
        result["price_partial_coordinates"] += 0 < observed < length
        result["price_complete_coordinates"] += observed == length
        result["first60_price_complete_coordinates"] += length >= 60 and first60 == 60
    seen = set()
    for row in support_rows:
        key = row["session"], row["symbol"]
        _check(key in lengths and key not in seen and row["family"] == "C_FIRSTPULLBACK"
               and type(row["scheduled_minutes"]) is int and row["scheduled_minutes"] == lengths[key],
               "C support coordinates or duration differ")
        seen.add(key)
        minute_states = {}
        cursor = 0
        for run in row["runs"]:
            start, end, state = run["start"], run["end"], run["status"]
            _check(type(start) is int and type(end) is int and cursor == start < end <= lengths[key]
                   and type(state) is str and state, "C support runs do not partition original minutes")
            for offset in range(start, end):
                minute_states[offset] = state
            cursor = end
        _check(cursor == lengths[key], "C support runs omit original minutes")
        expanded = {}
        for state in minute_states.values():
            expanded[state] = expanded.get(state, 0) + 1
        counts = row["counts"]
        _check(all(type(state) is str and state and type(count) is int and count >= 0
                   for state, count in counts.items())
               and all(expanded.get(state, 0) == counts.get(state, 0) for state in expanded.keys() | counts.keys()),
               "C support saved counts differ from expanded runs")
        for state in counts:
            support_states[state] = support_states.get(state, 0) + expanded.get(state, 0)
    events = {(row["session"], row["symbol"]) for row in roster.values()}
    _check(seen == lengths.keys() and events <= lengths.keys(), "source support changed original coordinate denominator")
    result.update(coordinates_with_events=len(events), coordinates_without_events=len(lengths) - len(events),
                  continuous_price_states=dict(sorted(price_states.items())),
                  c_support_minutes=dict(sorted(support_states.items())))
    _check(result == saved, "source-support reconstruction differs from saved report")
    return result
