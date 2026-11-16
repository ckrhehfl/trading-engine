"""Pure EM attribution of sealed original episodes; no IO, fitting or orders.

Cash is nominal event cash. Opening extrema are hindsight descriptive bounds,
never a tradable selector. Archived outcome maturity remains authoritative.
"""

from collections import Counter
from datetime import date
from fractions import Fraction
from itertools import product

from research.intraday_event_evaluation import SessionMarket
from research.intraday_event_scenarios import MinuteBar


CONTROLS = ("onset", "confirmation")
ENTRIES = ("next_open_capped", "reference_limit_one_shot")
MANAGEMENT = ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")
ARM_KEYS = tuple(product(CONTROLS, ENTRIES, MANAGEMENT))
COST = Fraction(33, 10_000)
PATH_ARM = ("confirmation", "reference_limit_one_shot", "fixed_exit")
CATEGORIES = ("both_filled", "left_only", "right_only", "neither", "unknown")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _amount(value):
    _require(type(value) in (str, int, Fraction), "Exact amount required; no floats")
    return Fraction(value)


def _identity(roster, arm, row):
    _require(tuple(row[k] for k in ("control", "entry_policy", "management")) == arm
             and row["model"] == "tcn" and row["scope"] == "common_chart_peer"
             and row["family"] == "C_FIRSTPULLBACK", "Unexpected attribution arm")
    _require(tuple(row[k] for k in ("original_episode_id", "session", "symbol", "cluster_id"))
             == tuple(roster[k] for k in ("event_id", "session", "symbol", "cluster_id")),
             "Ledger differs from sealed episode identity")
    if row["action"] == "no_commit":
        _require(row["candidate_key"] is None and row["entry_decision_offset"] is None,
                 "No-commit arm has an entry decision")
        return
    clock = roster[arm[0] + "_decision_offset"]
    _require(row["action"] == "immediate" and type(clock) is int
             and row["entry_decision_offset"] == clock
             and row["candidate_key"] == f"{roster['event_id']}@{clock}"
             and row["selection_reason"] == "fixed_" + arm[0],
             "Ledger request differs from sealed causal clock")
    if arm[0] == "confirmation":
        _require(row["state"] == "CONFIRMED" and row["candidate_key"] == roster["confirmation_key"]
                 and roster["confirmation_reference"] is not None
                 and _amount(row["execution"]["reference_close"]) == _amount(roster["confirmation_reference"]),
                 "Confirmation differs from sealed causal reference")


def _entry_signature(row):
    report = row["execution"]
    return tuple(row.get(k) for k in ("action", "candidate_key", "entry_decision_offset")) + tuple(
        report.get(k) for k in ("decision_offset", "planned_entry_offset", "expiry_offset",
                               "reference_close", "guard_cap", "structure_stop", "requested_quantity",
                               "filled_quantity", "entry_offset", "entry_price"))


def _book(arm, row):
    out, report = row["outcome"], row["execution"]
    quantity = out["filled_quantity"]
    _require(quantity is None or (type(quantity) is int and quantity >= 0), "Invalid filled quantity")
    if quantity is not None:
        _require(quantity == report["filled_quantity"], "Outcome fill differs from execution")
    committed = row["action"] != "no_commit"
    _require(committed or quantity == 0, "No-commit outcome is not known zero")
    filled = quantity is not None and quantity > 0
    purchase = _amount(report["entry_price"]) if filled else None
    basis = quantity * purchase if filled else None
    if filled:
        _require(purchase > 0 and type(report["entry_offset"]) is int
                 and _amount(out["entry_notional"]) == basis
                 and _amount(report["entry_notional"]) == basis, "Entry notional differs")
    result = dict(zip(("control", "entry_policy", "management"), arm))
    result.update(committed=committed, selection_reason=row["selection_reason"],
                  status=out["status"], reason=out["reason"],
                  fill_state="unknown" if quantity is None else "filled" if filled else "known_no_fill",
                  cash_state="unknown", filled_quantity=quantity,
                  entry_offset=report["entry_offset"] if filled else None,
                  entry_price=str(purchase) if filled else None,
                  entry_notional=str(basis) if filled else None,
                  gross_cash=None, net_cash=None, cost_cash=None, exit_offset=None, exit_reason=None)
    if out["net"] is None:
        _require(out["gross"] is None, "Unknown net has known gross")
        return result
    _require(quantity is not None, "Known cash has unknown fill")
    if not filled:
        _require(_amount(out["net"]) == _amount(out["gross"]) == 0
                 and _amount(report["net"]) == _amount(report["gross"]) == 0
                 and not report["sales"], "Known no-fill cash must be zero")
        result.update(cash_state="known_zero", gross_cash="0", net_cash="0", cost_cash="0")
        return result
    _require(report["status"] == "closed" and report["remaining_quantity"] == 0
             and report["cost_bps"] == 33 and report["sales"], "Settled book must be fully liquidated")
    sold, gross, last = 0, Fraction(), report["entry_offset"]
    for sale in report["sales"]:
        amount, at, decision = sale["quantity"], sale["execute_offset"], sale["decision_offset"]
        _require(type(amount) is int and amount > 0 and type(at) is int and at > last
                 and type(decision) is int and decision < at <= report["expiry_offset"],
                 "Invalid sale quantity or causal clock")
        price = _amount(sale["price"])
        _require(price > 0, "Positive sale price required")
        leg_gross, leg_cost = amount * (price - purchase), amount * purchase * COST
        _require(_amount(sale["gross"]) == leg_gross
                 and _amount(sale["allocated_cost"]) == leg_cost
                 and _amount(sale["net"]) == leg_gross - leg_cost, "Sale cash reconstruction differs")
        sold += amount
        gross += leg_gross
        last = at
    cost, net = basis * COST, gross - basis * COST
    _require(sold == quantity and _amount(report["entry_cost"]) == cost
             and _amount(report["cost"]) == cost
             and _amount(report["gross"]) == _amount(out["gross"]) == gross
             and _amount(report["net"]) == _amount(out["net"]) == net
             and _amount(report["cash"]) - _amount(report["initial_capital"]) == net,
             "Settled cash reconstruction differs")
    result.update(cash_state="settled", gross_cash=str(gross), net_cash=str(net), cost_cash=str(cost),
                  exit_offset=last, exit_reason=report["sales"][-1]["reason"])
    return result


def _path(book, report, market):
    result = {"status": "not_filled", "entry_offset": None, "exit_offset": None,
              "missing_offsets": [], "observed_openings": 0, "observed_cost_exceeded": False,
              "ever_cost_exceeded": None, "max_open_return": None, "min_open_return": None,
              "max_open_offset": None, "min_open_offset": None, "final_sale_return": None,
              "category": "not_applicable", "first_cost_close": None}
    if book["fill_state"] != "filled":
        return result
    _require(type(market) is SessionMarket, "Known reference fill needs its original session market")
    entry, purchase = book["entry_offset"], _amount(book["entry_price"])
    result.update(status="unknown_boundary", category="unknown_path", entry_offset=entry)
    sales = report["sales"]
    if not sales:
        return result
    _require(len(sales) == 1 and sales[0]["reason"] in ("time", "structural")
             and sales[0]["quantity"] == book["filled_quantity"], "Fixed boundary is not a full original liquidation")
    end = sales[0]["execute_offset"]
    _require(type(end) is int and entry < end <= report["expiry_offset"] < market.session_length,
             "Fixed boundary exceeds original expiry or session")
    quotes, missing = [], []
    for offset in range(entry + 1, end + 1):
        bar = market.bars.get(offset)
        _require(bar is None or (type(bar) is MinuteBar and bar.offset == offset), "Minute identity differs")
        if bar is None or bar.available_offset >= market.session_length:
            missing.append(offset)
        else:
            quotes.append(bar)
    complete = not missing
    positive = any(bar.open > purchase * (1 + COST) for bar in quotes)
    result.update(status="complete" if complete else "incomplete", exit_offset=end,
                  missing_offsets=missing, observed_openings=len(quotes), observed_cost_exceeded=positive,
                  ever_cost_exceeded=True if positive else False if complete else None)
    final = None
    if book["cash_state"] == "settled":
        final = _amount(sales[0]["price"]) / purchase - 1
        result["final_sale_return"] = str(final)
    if complete:
        largest, smallest = max(quotes, key=lambda b: b.open), min(quotes, key=lambda b: b.open)
        maximum = largest.open / purchase - 1
        result.update(max_open_return=str(maximum), min_open_return=str(smallest.open / purchase - 1),
                      max_open_offset=largest.offset, min_open_offset=smallest.offset)
        result["category"] = ("never_exceeded" if maximum < COST else "equal_only" if maximum == COST
                              else "exceeded_final_unknown" if final is None else "exceeded_retained" if final > COST
                              else "exceeded_then_equal" if final == COST else "exceeded_then_lost")
    entry_bar = market.bars.get(entry)
    _require(entry_bar is None or (type(entry_bar) is MinuteBar and entry_bar.offset == entry),
             "Entry minute identity differs")
    close_quotes = quotes + ([entry_bar] if entry_bar is not None
                             and entry_bar.available_offset < market.session_length else [])
    witnesses = [bar for bar in close_quotes if bar.close > purchase * (1 + COST)]
    if witnesses:
        witness = min(witnesses, key=lambda b: (b.available_offset, b.offset))
        at = witness.available_offset + 1
        following = market.bars.get(at) if at <= end else None
        _require(following is None or (type(following) is MinuteBar and following.offset == at),
                 "Trigger opening identity differs")
        known = following is not None and following.available_offset < market.session_length
        first_known = all((bar := market.bars.get(offset)) is not None
                          and bar.available_offset < market.session_length
                          for offset in range(entry, min(end, witness.available_offset - 1) + 1))
        result["first_cost_close"] = {"offset": witness.offset, "receipt_offset": witness.available_offset,
            "close": str(witness.close), "first_is_known": first_known, "opening_offset": at,
            "opening_status": "out_of_window" if at > end else "known" if known else "missing",
            "opening_price": str(following.open) if known else None,
            "opening_return": str(following.open / purchase - 1) if known else None}
    return result


def measure_event(roster_row, ledgers_by_arm, market):
    """Measure one already-sealed episode against all twelve archived books."""
    _require(set(ledgers_by_arm) == set(ARM_KEYS), "All twelve original arms required")
    _require(type(roster_row["onset_decision_offset"]) is int, "Original onset clock required")
    books = {}
    for arm in ARM_KEYS:
        row = ledgers_by_arm[arm]
        _identity(roster_row, arm, row)
        books[arm] = _book(arm, row)
    for control, entry in product(CONTROLS, ENTRIES):
        fixed = ledgers_by_arm[control, entry, "fixed_exit"]
        for management in MANAGEMENT[1:]:
            _require(_entry_signature(fixed) == _entry_signature(ledgers_by_arm[control, entry, management]),
                     "Management changed the matched original entry")
        capped = ledgers_by_arm[control, ENTRIES[0], "fixed_exit"]
        _require(_entry_signature(fixed)[:10] == _entry_signature(capped)[:10],
                 "Entry policies changed original request")
        if fixed["execution"]["filled_quantity"]:
            _require(_entry_signature(fixed) == _entry_signature(capped), "Reference fill is not identical capped subset")
    return {"schema": "EM-event-attribution-v1", "roster": dict(roster_row),
            "arms": [books[arm] for arm in ARM_KEYS],
            "path": _path(books[PATH_ARM], ledgers_by_arm[PATH_ARM]["execution"], market)}


def _totals():
    return {**dict.fromkeys(("opportunities", "commits", "fills", "known_no_fills", "unknown_fills",
                            "settled_fills", "unknown_cash"), 0),
            **dict.fromkeys(("gross_cash", "net_cash", "cost_cash", "entry_notional"), Fraction())}


def _add(total, book):
    total["opportunities"] += 1
    total["commits"] += int(book["committed"])
    total["fills"] += int(book["fill_state"] == "filled")
    total["known_no_fills"] += int(book["fill_state"] == "known_no_fill")
    total["unknown_fills"] += int(book["fill_state"] == "unknown")
    total["unknown_cash"] += int(book["cash_state"] == "unknown")
    if book["cash_state"] == "settled":
        total["settled_fills"] += 1
        for key in ("gross_cash", "net_cash", "cost_cash", "entry_notional"):
            total[key] += _amount(book[key])


def _pair_totals():
    return {"known_pairs": 0, "unknown_pairs": 0, "left_net_cash": Fraction(),
            "right_net_cash": Fraction(), "delta_net_cash": Fraction()}


def _pair_add(total, left, right):
    if left["cash_state"] == "unknown" or right["cash_state"] == "unknown":
        total["unknown_pairs"] += 1
    else:
        lnet, rnet = _amount(left["net_cash"]), _amount(right["net_cash"])
        total["known_pairs"] += 1
        total["left_net_cash"] += lnet
        total["right_net_cash"] += rnet
        total["delta_net_cash"] += rnet - lnet


def _comparisons():
    specs = {}
    for entry in ENTRIES:
        specs["timing|" + entry] = (("onset", entry, "fixed_exit"), ("confirmation", entry, "fixed_exit"), True)
        for management in MANAGEMENT[1:]:
            specs["management|" + entry + "|" + management] = (
                ("confirmation", entry, "fixed_exit"), ("confirmation", entry, management), False)
    specs["entry"] = (("confirmation", ENTRIES[0], "fixed_exit"), PATH_ARM, False)
    return specs


def _json_ready(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in sorted(value.items())}
    return value


def summarize(calendar, records):
    """Aggregate the full original calendar; paired sums are known-subset facts."""
    calendar = tuple(calendar)
    _require(calendar and tuple(sorted(set(calendar))) == calendar, "Ordered unique calendar required")
    weeks = {}
    for day in calendar:
        parsed = date.fromisoformat(day)
        _require(parsed.isoformat() == day, "Canonical ISO calendar date required")
        iso = parsed.isocalendar()
        weeks[day] = f"{iso.year}-W{iso.week:02}"
    names = {arm: "|".join(arm) for arm in ARM_KEYS}
    totals = lambda: {name: _totals() for name in names.values()}
    arms, dates, symbols = totals(), {day: totals() for day in calendar}, {}
    weekly = {week: {name: {"fills": 0, "wins": 0, "unknown_fills": 0} for name in names.values()}
              for week in weeks.values()}
    specs = _comparisons()
    comparisons = {name: {"confounded": spec[2], "categories": dict.fromkeys(CATEGORIES, 0),
                         "whole": _pair_totals(), "common_settled": _pair_totals(),
                         "cohort_cash": {category: _pair_totals() for category in CATEGORIES}}
                   for name, spec in specs.items()}
    terminals, confirmations, paths, seen = Counter(), Counter(), Counter(), set()
    path_observations = {"statuses": Counter(), "observed_cost_exceeded": 0,
                         "known_never_cost_exceeded": 0, "unknown_ever_cost_exceeded": 0,
                         "close_witnesses": 0, "known_first_close_witnesses": 0,
                         "trigger_openings": Counter()}
    arm_funnel = {name: {key: Counter() for key in ("selection_reasons", "statuses", "reasons")}
                  for name in names.values()}
    for record in records:
        roster = record["roster"]
        event, day, symbol = roster["event_id"], roster["session"], roster["symbol"]
        _require(record["schema"] == "EM-event-attribution-v1" and event not in seen and day in weeks,
                 "Duplicate or off-calendar original episode")
        seen.add(event)
        terminals[roster["terminal_state"]] += 1
        confirmations["absent" if roster["confirmation_key"] is None else
                      "unsupported_reference" if roster["confirmation_reference"] is None else "available"] += 1
        path = record["path"]
        paths[path["category"]] += 1
        path_observations["statuses"][path["status"]] += 1
        path_observations["observed_cost_exceeded"] += int(path["observed_cost_exceeded"])
        path_observations["known_never_cost_exceeded"] += int(path["ever_cost_exceeded"] is False)
        path_observations["unknown_ever_cost_exceeded"] += int(
            path["status"] != "not_filled" and path["ever_cost_exceeded"] is None)
        witness = path["first_cost_close"]
        if witness is not None:
            path_observations["close_witnesses"] += 1
            path_observations["known_first_close_witnesses"] += int(witness["first_is_known"])
            path_observations["trigger_openings"][witness["opening_status"]] += 1
        symbols.setdefault(symbol, totals())
        books = {tuple(book[key] for key in ("control", "entry_policy", "management")): book
                 for book in record["arms"]}
        _require(len(record["arms"]) == len(ARM_KEYS) and set(books) == set(ARM_KEYS), "Duplicate or absent arm")
        for arm, book in books.items():
            name = names[arm]
            arm_funnel[name]["selection_reasons"][book["selection_reason"]] += 1
            arm_funnel[name]["statuses"][book["status"]] += 1
            arm_funnel[name]["reasons"][book["reason"]] += 1
            for group in (arms, dates[day], symbols[symbol]):
                _add(group[name], book)
            week = weekly[weeks[day]][name]
            week["fills"] += int(book["fill_state"] == "filled")
            week["unknown_fills"] += int(book["fill_state"] == "unknown")
            week["wins"] += int(book["cash_state"] == "settled" and _amount(book["net_cash"]) > 0)
        for name, (left_key, right_key, _) in specs.items():
            left, right = books[left_key], books[right_key]
            ls, rs = left["fill_state"], right["fill_state"]
            category = ("unknown" if "unknown" in (ls, rs) else "both_filled" if ls == rs == "filled"
                        else "left_only" if ls == "filled" else "right_only" if rs == "filled" else "neither")
            pair = comparisons[name]
            pair["categories"][category] += 1
            _pair_add(pair["whole"], left, right)
            _pair_add(pair["cohort_cash"][category], left, right)
            if left["cash_state"] == right["cash_state"] == "settled":
                _pair_add(pair["common_settled"], left, right)
    return _json_ready({"schema": "EM-attribution-summary-v1", "opportunities": len(seen),
        "calendar_sessions": len(calendar), "calendar_weeks": len(set(weeks.values())),
        "funnel": {"terminal_states": dict(terminals), "confirmation_status": dict(confirmations),
                   "arms": arm_funnel},
        "arms": arms, "comparisons": comparisons, "paths": dict(paths),
        "path_observations": path_observations, "weekly": weekly,
        "concentration": {"dates": dates, "names": symbols}})
