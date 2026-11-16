"""Independent reconstruction of EN joins and descriptive statistics.

Only caller-authenticated records enter this module. It imports no producer,
execution, label or model calculations and never opens market/model artifacts.
Exact saved legs establish cash; outcome maturity establishes knowability.
"""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
import math


_HEADS = ("net_immediate", "net_wait3")
_ALL_HEADS = ("path_class", "fill", *_HEADS, "remaining_gross")
_ARMS = (("model", "next_open_capped", "fixed_exit"),
         ("model", "reference_limit_one_shot", "fixed_exit"),
         ("model", "reference_limit_one_shot", "forecast_full_exit"),
         ("confirmation", "reference_limit_one_shot", "forecast_full_exit"))
_AVAILABLE = {"fitted_empirical", "trained_lightgbm", "trained_tcn", "constant_single_class"}
_UNAVAILABLE = {"unsupported", "failed", "unsupported_empty_training"}
_COST = Fraction(33, 10_000)


def _check(condition, message):
    if not condition:
        raise ValueError("EN independent audit: " + message)


def _exact(value):
    _check(type(value) in (str, int, Fraction), "exact amount required")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("EN independent audit: invalid exact amount") from error


def _prediction(value):
    if value is None:
        return None
    _check(type(value) in (str, int, float, Fraction), "finite prediction required")
    _check(math.isfinite(float(Fraction(value))), "nonfinite prediction")
    return Fraction(str(value)) if type(value) is float else Fraction(value)


def _wire(value):
    return None if value is None else str(value)


def _integer(value, *, minimum=0):
    _check(type(value) is int and value >= minimum, "nonnegative integer required")
    return value


def _same(actual, expected, where="record", *, statistical=False):
    """Tolerance belongs only to recomputed descriptive float statistics."""
    if isinstance(expected, dict):
        _check(isinstance(actual, dict) and actual.keys() == expected.keys(), where + " fields differ")
        for key, value in expected.items():
            _same(actual[key], value, where + "." + key, statistical=statistical)
    elif isinstance(expected, (list, tuple)):
        _check(isinstance(actual, (list, tuple)) and len(actual) == len(expected), where + " length differs")
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            _same(left, right, f"{where}[{index}]", statistical=statistical)
    elif type(expected) is float and statistical:
        _check(type(actual) in (int, float) and math.isfinite(actual)
               and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12), where + " statistic differs")
    else:
        _check(type(actual) is type(expected) and actual == expected, where + " differs")


def _event(event):
    _check(event.get("family", "C_FIRSTPULLBACK") == "C_FIRSTPULLBACK", "event outside C scope")
    _check(date.fromisoformat(event["session"]).isoformat() == event["session"], "noncanonical session")
    for field in ("event_id", "symbol", "cluster_id"):
        _check(type(event[field]) is str and event[field].strip() == event[field]
               and 0 < len(event[field]) <= 512, "invalid original identity")
    _integer(event["onset_decision_offset"])


def _request(query):
    close, scale, stop = query["reference_close"], _exact(query["scale"]), _exact(query["structure_stop"])
    _check(scale > 0 and stop > 0, "nonpositive request geometry")
    denominator = None
    if close is not None:
        close = _exact(close)
        _check(close > 0, "nonpositive reference")
        cap = close + scale
        if stop < cap:
            count = min(int(Fraction(50_000) // (cap - stop + cap * _COST)),
                        int(Fraction(2_000_000) // cap))
            denominator = count * cap
    if query["terminal"] and query["state"] != "CONFIRMED":
        return denominator, "terminal_not_admissible", "not_admissible"
    if query["decision_offset"] + 31 >= query["session_length"]:
        return denominator, "expiry_not_inside_session", "not_admissible"
    if close is None:
        return denominator, "reference_unknown", "unknown"
    if stop >= close + scale:
        return denominator, "stop_not_below_cap", "not_admissible"
    return denominator, None, "admissible"


def _causal(event, wrapper, pred, fold):
    _event(event)
    _check(type(fold) is int and fold in range(4), "fold outside original four folds")
    _check(wrapper["kind"] == "query" and wrapper["query_kind"] == "entry", "held query is not an entry query")
    frame, query = wrapper["frame"], wrapper["label_query"]
    _check(query is not None, "entry label query missing")
    key, clock = wrapper["key"], query["decision_offset"]
    _integer(clock)
    _check(key == f"{event['event_id']}@{clock}" and clock >= event["onset_decision_offset"], "query key/onset differs")
    _check((wrapper["session"], wrapper["symbol"]) == (event["session"], event["symbol"]), "query owner differs")
    for field in ("event_id", "family", "state", "terminal", "onset_offset", "decision_offset", "scale"):
        _same(query[field], frame[field], "label/frame " + field)
    _check(query["event_id"] == event["event_id"] and query["family"] == "C_FIRSTPULLBACK"
           and frame["overlap_group_id"] == event["cluster_id"]
           and query["event_offset"] == frame["offset"]
           and _exact(query["structure_stop"]) == _exact(frame["structure"]), "frame original identity differs")
    length = _integer(query["session_length"], minimum=1)
    _check(wrapper.get("continuous_length", length) == length and type(query["terminal"]) is bool
           and 0 <= query["onset_offset"] <= query["event_offset"] < clock
           and query["event_offset"] < length, "query causal geometry differs")
    active = {"WAITING", "WAITING_PULLBACK", "PULLBACK"}
    terminal = {"CONFIRMED", "INVALIDATED", "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP", "SESSION_END"}
    _check(query["state"] in (terminal if query["terminal"] else active), "terminal/state differs")
    _check((query["reference_close"] is None) == (query["reference_offset"] is None), "reference pair differs")
    if query["reference_close"] is not None:
        _integer(query["reference_offset"])
        _check(query["reference_offset"] < clock, "reference is not causal")
    chart = wrapper["query"]
    chart_supported = chart is not None
    peer_supported = False
    if chart_supported:
        expected = dict(key=key, session=event["session"], symbol=event["symbol"],
                        episode_id=event["event_id"], cluster_id=event["cluster_id"],
                        family="C_FIRSTPULLBACK", state=query["state"], decision_offset=clock)
        _check(all(chart[field] == value for field, value in expected.items()), "chart identity differs")
        _check(chart.get("original_episode_id") in (None, event["event_id"])
               and chart.get("position") is None, "held/original chart identity differs")
        peer_supported = chart["peer"] is not None and all(value is not None for value in chart["peer"])
    scope = chart_supported and peer_supported
    values, heads = dict.fromkeys(_HEADS), dict.fromkeys(_ALL_HEADS, "prediction_absent")
    if pred is not None:
        _check(scope and pred["key"] == key and pred["session"] == event["session"], "prediction join/scope differs")
        _check(set(pred["predictions"]) == set(_ALL_HEADS)
               and set(pred["head_status"]) == set(_ALL_HEADS), "original five heads required")
        values = {head: _wire(_prediction(pred["predictions"][head])) for head in _HEADS}
        heads = dict(pred["head_status"])
        _check(pred["query_status"] == "supported_features" or all(v is None for v in pred["predictions"].values()),
               "unsupported prediction has values")
    denominator, reason, admission = _request(query)
    phase = ("onset" if clock == event["onset_decision_offset"] else "waiting_pullback"
             if query["state"] == "WAITING_PULLBACK" else "pullback" if query["state"] == "PULLBACK"
             else "confirmation" if query["state"] == "CONFIRMED" else "invalid_or_terminal")
    return dict(schema="EN-causal-query-v1", key=key, event_id=event["event_id"],
                session=event["session"], symbol=event["symbol"], cluster_id=event["cluster_id"],
                fold=fold, decision_offset=clock, onset_decision_offset=event["onset_decision_offset"],
                state=query["state"], terminal=query["terminal"], phase=phase,
                clock_bin="<60" if clock < 60 else "60_to_180" if clock < 180 else "180_plus",
                chart_supported=chart_supported, peer_supported=peer_supported, scope_supported=scope,
                query_status=wrapper["query_status"], prediction_status="absent" if pred is None else pred["query_status"],
                head_status=heads, admission_reason=reason, entry_admissibility=admission,
                initial_denominator=_wire(denominator), session_length=length,
                predictions=values, label_query=query)


def verify_causal(event, wrapper, pred, fold, saved):
    _same(saved, _causal(event, wrapper, pred, fold), "causal")
    return True


def _choice(event, causal_records):
    _event(event)
    rows = sorted(causal_records, key=lambda row: row["decision_offset"])
    _check(len({row["key"] for row in rows}) == len(rows)
           and len({row["decision_offset"] for row in rows}) == len(rows), "duplicate causal query")
    _check(all(row["event_id"] == event["event_id"] and row["session"] == event["session"]
               and row["symbol"] == event["symbol"] and row["cluster_id"] == event["cluster_id"]
               for row in rows), "choice original identity differs")
    supported = [row for row in rows if row["scope_supported"]]
    selected, reason, denominator, action, trace = None, "no_causal_frame", None, "no_commit", []
    for row in supported:
        if denominator is None:
            denominator = row["initial_denominator"]
        qi, qw = (_prediction(row["predictions"][head]) for head in _HEADS)
        if row["admission_reason"] is not None:
            reason, trace_action = row["admission_reason"], "abstain"
        elif qi is None or qw is None:
            reason, trace_action = "missing_paired_value_heads", "abstain"
        elif max(qi, qw) <= 0:
            reason, trace_action = "observe_without_commitment", "observe"
        else:
            selected = row
            reason = "first_positive_paired_value"
            action = trace_action = "immediate" if qi >= qw else "wait3"
            denominator = row["initial_denominator"]
        trace.append([event["event_id"], row["key"], row["decision_offset"], row["state"],
                      trace_action, reason, _wire(qi), _wire(qw)])
        if selected is not None:
            break
    return dict(event_id=event["event_id"], key=None if selected is None else selected["key"],
                decision_offset=None if selected is None else selected["decision_offset"],
                state="NO_COMMIT" if selected is None else selected["state"], action=action, reason=reason,
                initial_denominator=denominator,
                net_immediate=None if selected is None else selected["predictions"][_HEADS[0]],
                net_wait3=None if selected is None else selected["predictions"][_HEADS[1]],
                selected_prediction=None if selected is None else selected["predictions"]["net_" + action],
                trace=trace, scope_frames=len(supported))


def verify_choice(event, causal_records, saved):
    _same(saved, _choice(event, causal_records), "choice")
    return True


def _measure(causal, label, choice):
    _check(label["kind"] == "labels" and label["query_kind"] == "entry"
           and all(label[key] == causal[key] for key in ("key", "session", "symbol")), "label join differs")
    target, training = label["targets"], label["training_labels"]
    denominator = None if causal["initial_denominator"] is None else _exact(causal["initial_denominator"])
    _check(target["event_id"] == causal["event_id"] and target["decision_offset"] == causal["decision_offset"]
           and target["entry_admissibility"] == causal["entry_admissibility"]
           and target["original_requested_notional"] == causal["initial_denominator"], "target original question differs")
    _check((training is not None) == causal["chart_supported"], "training target/chart support differs")
    if training is not None:
        _check(training["key"] == causal["key"] and training["remaining_gross"] is None, "training entry identity differs")
        any_known = any(training[head] is not None for head in _ALL_HEADS)
        _check(training["matured_session"] == (causal["session"] if any_known else None), "training maturity date differs")
    heads = {}
    for head, source_name, delay in (("net_immediate", "immediate", 0), ("net_wait3", "wait3", 3)):
        source = target[source_name]
        _check(source["denominator"] == causal["initial_denominator"]
               and source["decision_offset"] == causal["decision_offset"] + delay
               and source["entry_offset"] == causal["decision_offset"] + delay + 1
               and source["expiry_offset"] == causal["decision_offset"] + delay + 31,
               "target denominator or commitment timing differs")
        for key in ("matured_offset", "fill_matured_offset"):
            if source[key] is not None:
                _integer(source[key])
                _check(source[key] < causal["session_length"], "target maturity outside session")
        raw = None if source["value"] is None else _exact(source["value"])
        cash = None if source["net_cash"] is None else _exact(source["net_cash"])
        if raw is not None:
            _check(cash is not None, "known target value has unknown cash")
            if denominator is not None and denominator > 0:
                _check(raw == cash / denominator, "target ratio differs from original denominator")
            else:
                _check(raw == cash == 0 and source["status"] in ("no_entry", "no_fill"),
                       "undefined target carries nonzero value")
        _check(source["fill"] is None or type(source["fill"]) is int and source["fill"] in (0, 1), "invalid target fill")
        expected = raw if causal["entry_admissibility"] == "admissible" and source["matured_offset"] is not None else None
        trained = None if training is None or training[head] is None else _exact(training[head])
        _check(trained == (expected if causal["chart_supported"] else None), "training target semantics differ")
        state = ("inapplicable" if causal["entry_admissibility"] == "not_admissible" else "unknown_chart"
                 if not causal["chart_supported"] else "unknown_admission" if causal["entry_admissibility"] != "admissible"
                 else "undefined_zero_denominator" if denominator == 0
                 else "immature" if source["matured_offset"] is None else "unknown" if expected is None else "known")
        actual = expected if state == "known" else None
        prediction = _prediction(causal["predictions"][head])
        scored = actual is not None and prediction is not None
        heads[head] = dict(prediction=_wire(prediction), target=_wire(actual), training_target=_wire(trained),
                           target_state=state, raw_status=source["status"], reason=source["reason"],
                           net_cash=_wire(cash), denominator=_wire(denominator), fill=source["fill"],
                           matured_offset=source["matured_offset"], fill_matured_offset=source["fill_matured_offset"],
                           scored=scored, error=_wire(prediction - actual) if scored else None)
    selected = choice["key"] == causal["key"]
    paired_known = all(heads[head]["scored"] for head in _HEADS)
    predicted = observed = error = agreement = None
    if paired_known:
        predicted = _exact(heads[_HEADS[1]]["prediction"]) - _exact(heads[_HEADS[0]]["prediction"])
        observed = _exact(heads[_HEADS[1]]["target"]) - _exact(heads[_HEADS[0]]["target"])
        error = predicted - observed
        agreement = (predicted > 0) - (predicted < 0) == (observed > 0) - (observed < 0)
    return {**causal, "schema": "EN-query-value-v1", "selected": selected,
            "selected_head": "net_" + choice["action"] if selected else None, "heads": heads,
            "paired": dict(scored=paired_known, predicted_wait_advantage=_wire(predicted),
                           observed_wait_advantage=_wire(observed), error=_wire(error), direction_agreement=agreement)}


def verify_query(causal, label, choice, saved):
    _same(saved, _measure(causal, label, choice), "query")
    return True


def _reprice(row):
    """Validate the saved order geometry and independently price each sale."""
    report = row["execution"]
    quantity = report["filled_quantity"]
    _check(quantity is None or type(quantity) is int and quantity >= 0, "invalid raw fill quantity")
    decision = row["entry_decision_offset"]
    if decision is None:
        _check(quantity in (None, 0) and not report["sales"], "absent request contains execution")
        return
    _integer(decision)
    cap, stop, reference = (_exact(report[key]) for key in ("guard_cap", "structure_stop", "reference_close"))
    _check(0 < stop < cap and 0 < reference < cap, "invalid saved request prices")
    size = min(int(Fraction(50_000) // (cap - stop + cap * _COST)), int(Fraction(2_000_000) // cap))
    _check(report["decision_offset"] == decision and report["planned_entry_offset"] == decision + 1
           and report["expiry_offset"] == decision + 31 and report["cost_bps"] == 33
           and _exact(report["initial_capital"]) == 100_000_000
           and _exact(report["first_entry_risk_budget"]) == 50_000
           and type(report["requested_quantity"]) is int and report["requested_quantity"] == size,
           "saved request timing/cost/size differs")
    if not quantity:
        _check(not report["sales"], "unfilled position contains sale")
        if quantity == 0:
            _check(_exact(report["entry_notional"]) == 0, "unfilled basis is not zero")
        return
    price = _exact(report["entry_price"])
    limit = cap if row["entry_policy"] == "next_open_capped" else reference
    _check(quantity == size and stop < price <= limit and report["entry_offset"] == decision + 1,
           "saved fill violates request")
    basis, cost = quantity * price, quantity * price * _COST
    _check(_exact(report["entry_notional"]) == basis and _exact(report["entry_cost"]) == cost,
           "saved purchase basis/cost differs")
    proceeds, sold, scheduled, prior = Fraction(), 0, 0, decision + 1
    for leg in report["sales"]:
        count, clock = _integer(leg["quantity"], minimum=1), _integer(leg["execute_offset"], minimum=1)
        scheduled += count
        _check(prior < clock <= report["expiry_offset"] and scheduled <= quantity,
               "sale ordering or quantity exceeds position")
        prior = clock
        reason = leg["reason"]
        _check(reason in ("time", "structural", "forecast_full", "forecast_half"), "unregistered sale reason")
        _check((reason == "time" and clock == report["expiry_offset"])
               or (reason != "time" and leg["decision_offset"] + 1 == clock), "sale causal clock differs")
        if reason.startswith("forecast"):
            _check(row["management"] != "fixed_exit" and clock in (decision + 12, decision + 22),
                   "forecast sale outside held checkpoints")
        if reason == "forecast_half":
            _check(row["management"] == "forecast_half_then_exit" and count == quantity // 2, "half sale quantity differs")
        if leg["price"] is None:
            _check(report["net"] is None, "missing sale price has known cash")
            continue
        sale_price = _exact(leg["price"])
        _check(sale_price > 0, "nonpositive sale price")
        leg_gross, leg_cost = count * (sale_price - price), count * price * _COST
        _check(_exact(leg["gross"]) == leg_gross and _exact(leg["allocated_cost"]) == leg_cost
               and _exact(leg["net"]) == leg_gross - leg_cost, "sale leg cash differs")
        sold += count
        proceeds += count * sale_price
    if report["net"] is not None:
        gross = proceeds - basis
        _check(sold == quantity and report["remaining_quantity"] == 0 and report["status"] == "closed"
               and _exact(report["gross"]) == gross and _exact(report["cost"]) == cost
               and _exact(report["net"]) == gross - cost
               and _exact(report["cash"]) == 100_000_000 + gross - cost,
               "saved settled totals differ from independent legs")


def _ledger_identity(event, arm, row, choice, measured):
    _check(tuple(row[key] for key in ("control", "entry_policy", "management")) == arm
           and (row["model"], row["scope"], row["family"]) == ("tcn", "common_chart_peer", "C_FIRSTPULLBACK"),
           "ledger policy scope differs")
    _check(tuple(row[key] for key in ("original_episode_id", "session", "symbol", "cluster_id"))
           == tuple(event[key] for key in ("event_id", "session", "symbol", "cluster_id")), "ledger original identity differs")
    if arm[0] == "model":
        _check(row["candidate_key"] == choice["key"] and row["action"] == choice["action"]
               and row["selection_reason"] == choice["reason"] and row["state"] == choice["state"]
               and row["initial_denominator"] == choice["initial_denominator"], "ledger selected commitment differs")
        if choice["key"] is not None:
            query = measured[choice["key"]]["label_query"]
            actual_clock = row["entry_decision_offset"]
            if actual_clock is not None:
                _check(actual_clock == choice["decision_offset"] + (3 if choice["action"] == "wait3" else 0),
                       "wait commitment clock differs")
                _check(_exact(row["execution"]["structure_stop"]) == _exact(query["structure_stop"]),
                       "selected structural stop differs")
                _check(_exact(row["execution"]["guard_cap"]) - _exact(row["execution"]["reference_close"])
                       == _exact(query["scale"]), "selected request scale differs")
                if choice["action"] == "immediate":
                    _check(_exact(row["execution"]["reference_close"]) == _exact(query["reference_close"]),
                           "immediate reference differs")
    elif row["action"] != "no_commit":
        _check(row["action"] == "immediate" and row["state"] == "CONFIRMED"
               and row["selection_reason"] == "fixed_confirmation"
               and row["candidate_key"] in measured, "confirmation control differs")
        source = measured[row["candidate_key"]]
        _check(source["state"] == "CONFIRMED" and row["entry_decision_offset"] == source["decision_offset"]
               and row["initial_denominator"] == source["initial_denominator"], "confirmation coordinate differs")
    if row["action"] == "no_commit":
        _check(row["candidate_key"] is None and row["entry_decision_offset"] is None,
               "uncommitted ledger has a candidate/request")


def _book(arm, row, available):
    _reprice(row)
    out, report = row["outcome"], row["execution"]
    quantity = out["filled_quantity"]
    _check(quantity is None or type(quantity) is int and quantity >= 0, "invalid mature fill")
    _check((out["gross"] is None) == (out["net"] is None), "one-sided unknown outcome cash")
    if quantity is not None:
        _check(quantity == report["filled_quantity"], "mature/raw fill differs")
    basis = None
    if quantity:
        basis = quantity * _exact(report["entry_price"])
        _check(_exact(out["entry_notional"]) == basis, "mature purchase basis differs")
    elif quantity == 0:
        _check(_exact(out["entry_notional"]) == 0, "mature no-fill basis differs")
    result = dict(zip(("control", "entry_policy", "management"), arm, strict=True))
    result.update(committed=row["action"] != "no_commit", selection_reason=row["selection_reason"],
                  status=out["status"] if available else "model_unavailable", reason=out["reason"],
                  fill_state="unknown" if quantity is None else "filled" if quantity else "known_no_fill",
                  cash_state="unknown", filled_quantity=quantity,
                  entry_offset=report["entry_offset"] if quantity else None,
                  entry_price=_wire(_exact(report["entry_price"])) if quantity else None,
                  entry_notional=_wire(basis), gross_cash=None, net_cash=None, cost_cash=None,
                  exit_offset=None, exit_reason=None, available=available)
    if not available:
        result.update(fill_state="unavailable", cash_state="unavailable", filled_quantity=None,
                      entry_offset=None, entry_price=None, entry_notional=None)
        return result
    if out["net"] is not None:
        _check(quantity is not None, "known outcome with unknown fill")
        if quantity:
            _check(report["status"] == "closed" and report["remaining_quantity"] == 0
                   and sum(leg["quantity"] for leg in report["sales"]) == quantity,
                   "known outcome does not settle the position")
            gross = sum((_exact(leg["price"]) * leg["quantity"] for leg in report["sales"]), Fraction()) - basis
            cost = basis * _COST
            result.update(cash_state="settled", exit_offset=report["sales"][-1]["execute_offset"],
                          exit_reason=report["sales"][-1]["reason"])
        else:
            gross = cost = Fraction()
            _check(not report["sales"] and _exact(report["gross"]) == _exact(report["net"]) == 0,
                   "no-fill execution has nonzero cash")
            result["cash_state"] = "known_zero"
        _check(_exact(out["gross"]) == gross and _exact(out["net"]) == gross - cost,
               "mature cash differs from saved legs")
        result.update(gross_cash=_wire(gross), cost_cash=_wire(cost), net_cash=_wire(gross - cost))
    _check(result["committed"] or quantity == 0 and result["net_cash"] == "0", "no-commit outcome is not known zero")
    return result


def _episode(event, choice, measured, ledgers, statuses, fold):
    _event(event)
    _check(type(fold) is int and fold in range(4), "episode fold differs")
    _check(set(ledgers) == set(_ARMS) and set(statuses) == set(_ALL_HEADS)
           and all(value in _AVAILABLE | _UNAVAILABLE for value in statuses.values()), "ledger/head roster differs")
    _check(choice["event_id"] == event["event_id"], "episode choice owner differs")
    _check(all(row["key"] == key and row["event_id"] == event["event_id"] and row["fold"] == fold
               for key, row in measured.items()), "measured episode population differs")
    _check(choice["key"] is None or choice["key"] in measured, "selected target missing")
    books = []
    for arm in _ARMS:
        row = ledgers[arm]
        _ledger_identity(event, arm, row, choice, measured)
        available = ((arm[0] != "model" or all(statuses[head] in _AVAILABLE for head in _HEADS))
                     and (arm[2] == "fixed_exit" or statuses["remaining_gross"] in _AVAILABLE))
        books.append(_book(arm, row, available))
    # Entry/holding treatments must preserve the same original request, even
    # when a future fill or cash observation is unavailable.
    for field in ("entry_decision_offset",):
        _check(len({ledgers[arm][field] for arm in _ARMS[:3]}) == 1, "model request clock differs across arms")
    for field in ("decision_offset", "planned_entry_offset", "expiry_offset", "reference_close",
                  "guard_cap", "structure_stop", "requested_quantity"):
        _check(len({ledgers[arm]["execution"].get(field) for arm in _ARMS[:3]}) == 1,
               "model request differs across arms: " + field)
    for field in ("filled_quantity", "entry_offset", "entry_price", "entry_notional"):
        _check(ledgers[_ARMS[1]]["execution"].get(field) == ledgers[_ARMS[2]]["execution"].get(field),
               "holding treatment changed entry")
    if ledgers[_ARMS[1]]["execution"]["filled_quantity"]:
        for field in ("filled_quantity", "entry_offset", "entry_price"):
            _check(ledgers[_ARMS[0]]["execution"].get(field) == ledgers[_ARMS[1]]["execution"].get(field),
                   "reference fill is not the identical capped subset")
    reconcile = dict(status="no_commit" if choice["key"] is None else "unknown", label_net_cash=None,
                     cap_net_cash=None, cap_minus_label_cash=None, label_value=None, cap_value=None)
    decomp = dict.fromkeys(("denominator", "prediction", "predicted_cash", "cap_residual", "entry_effect",
                           "holding_effect", "total_residual", "label_error", "cap_minus_label_cash"))
    decomp["status"] = "no_commit" if choice["key"] is None else "unknown"
    if choice["key"] is not None:
        selected = measured[choice["key"]]
        head = selected["heads"]["net_" + choice["action"]]
        denominator = None if choice["initial_denominator"] is None else _exact(choice["initial_denominator"])
        label_cash = None if head["target"] is None else _exact(head["net_cash"])
        cap_cash = None if books[0]["net_cash"] is None else _exact(books[0]["net_cash"])
        reconcile.update(label_net_cash=_wire(label_cash), cap_net_cash=_wire(cap_cash), label_value=head["target"],
                         cap_value=_wire(cap_cash / denominator) if cap_cash is not None and denominator else None)
        if label_cash is not None and cap_cash is not None:
            gap = cap_cash - label_cash
            reconcile.update(status="matched" if gap == 0 else "cash_mismatch", cap_minus_label_cash=_wire(gap))
        elif (label_cash is None) != (cap_cash is None):
            reconcile["status"] = "support_mismatch"
        if denominator is not None and denominator > 0 and all(book["available"] and book["net_cash"] is not None for book in books[:3]):
            prediction = _prediction(choice["selected_prediction"])
            _check(prediction is not None, "committed prediction missing")
            cash = [_exact(book["net_cash"]) for book in books[:3]]
            predicted_cash = denominator * prediction
            terms = (cash[0] - predicted_cash, cash[1] - cash[0], cash[2] - cash[1])
            _check(sum(terms) == cash[2] - predicted_cash, "three-term identity failed")
            decomp.update(status="known", denominator=_wire(denominator), prediction=_wire(prediction),
                          predicted_cash=_wire(predicted_cash), cap_residual=_wire(terms[0]), entry_effect=_wire(terms[1]),
                          holding_effect=_wire(terms[2]), total_residual=_wire(cash[2] - predicted_cash),
                          label_error=_wire(label_cash - predicted_cash) if label_cash is not None else None,
                          cap_minus_label_cash=_wire(cash[0] - label_cash) if label_cash is not None else None)
    return dict(schema="EN-episode-value-v1", roster=event, fold=fold, choice=choice,
                arms=books, reconciliation=reconcile, decomposition=decomp)


def verify_episode(event, choice, measured_by_key, ledgers_by_arm, head_statuses, fold, saved):
    _same(saved, _episode(event, choice, measured_by_key, ledgers_by_arm, head_statuses, fold), "episode")
    return True


def _counts(rows):
    return dict(frames=len(rows), episodes=len({r["event_id"] for r in rows}),
                clusters=len({r["cluster_id"] for r in rows}), dates=len({r["session"] for r in rows}))


def _weighted(rows):
    tree = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        tree[row["session"]][row["cluster_id"]][row["event_id"]].append(row)
    weighted = []
    for clusters in tree.values():
        for episodes in clusters.values():
            for frames in episodes.values():
                weight = Fraction(1, len(tree) * len(clusters) * len(episodes) * len(frames))
                weighted.extend((row, weight) for row in frames)
    return weighted


def _metrics(rows, values):
    names = ("prediction_mean", "observed_mean", "signed_error", "mae", "rmse",
             "zero_signed_error", "zero_mae", "zero_rmse")
    if not rows:
        return dict.fromkeys(names)
    sums = [Fraction() for _ in names]
    for row, weight in _weighted(rows):
        pred, actual = map(_exact, values(row))
        delta = pred - actual
        terms = pred, actual, delta, abs(delta), delta * delta, -actual, abs(actual), actual * actual
        for index, term in enumerate(terms):
            sums[index] += weight * term
    return {name: math.sqrt(float(value)) if name in ("rmse", "zero_rmse") else float(value)
            for name, value in zip(names, sums, strict=True)}


def _head_summary(rows, head):
    scored = [row for row in rows if row["heads"][head]["scored"]]
    positive = dict.fromkeys(("positive", "zero", "negative", "unknown"), 0)
    for row in rows:
        item = row["heads"][head]
        if item["prediction"] is not None and _exact(item["prediction"]) > 0:
            target = None if item["target"] is None else _exact(item["target"])
            positive["unknown" if target is None else "positive" if target > 0 else "negative" if target < 0 else "zero"] += 1
    return dict(population=_counts(rows), scored=_counts(scored),
                chart_supported=sum(row["chart_supported"] for row in rows),
                peer_supported=sum(row["peer_supported"] for row in rows),
                scope_supported=sum(row["scope_supported"] for row in rows),
                prediction_known=sum(row["heads"][head]["prediction"] is not None for row in rows),
                target_states=dict(Counter(row["heads"][head]["target_state"] for row in rows)),
                positive_prediction_outcomes=positive,
                **_metrics(scored, lambda row: (row["heads"][head]["prediction"], row["heads"][head]["target"])))


def _paired_summary(rows):
    scored = [row for row in rows if row["paired"]["scored"]]
    return dict(population=_counts(rows), scored=_counts(scored),
                direction_agreement=sum(row["paired"]["direction_agreement"] for row in scored),
                weighted_direction_agreement=(float(sum((weight for row, weight in _weighted(scored)
                                                         if row["paired"]["direction_agreement"]), Fraction())) if scored else None),
                predicted_ties=sum(_exact(row["paired"]["predicted_wait_advantage"]) == 0 for row in scored),
                observed_ties=sum(_exact(row["paired"]["observed_wait_advantage"]) == 0 for row in scored),
                **_metrics(scored, lambda row: (row["paired"]["predicted_wait_advantage"], row["paired"]["observed_wait_advantage"])))


def _calibration(rows):
    groups = defaultdict(list)
    groups["whole"] = list(rows)
    for row in rows:
        for group in (f"fold/{row['fold']}", "phase/" + row["phase"],
                      f"fold_phase/{row['fold']}|{row['phase']}", "clock/" + row["clock_bin"]):
            groups[group].append(row)
    return {key: {"heads": {head: _head_summary(group, head) for head in _HEADS},
                  "paired": _paired_summary(group)} for key, group in groups.items()}


def _bins(rows):
    result = {}
    for head in _HEADS:
        groups = {name: [] for name in ("below_minus33bp", "minus33bp_to_zero", "zero", "zero_to_33bp", "above_33bp")}
        for row in rows:
            prediction = row["heads"][head]["prediction"]
            if prediction is None:
                continue
            value = _exact(prediction)
            key = ("below_minus33bp" if value < -_COST else "minus33bp_to_zero" if value < 0
                   else "zero" if value == 0 else "zero_to_33bp" if value <= _COST else "above_33bp")
            groups[key].append(row)
        result[head] = {key: _head_summary(group, head) for key, group in groups.items()}
    return result


def _totals(books):
    result = dict.fromkeys(("opportunities", "unavailable", "commits", "fills", "wins", "known_no_fills",
                            "unknown_fills", "settled_fills", "unknown_cash"), 0)
    sums = dict.fromkeys(("gross_cash", "net_cash", "cost_cash", "entry_notional"), Fraction())
    for book in books:
        result["opportunities"] += 1
        if not book["available"]:
            result["unavailable"] += 1
            continue
        result["commits"] += int(book["committed"])
        result["fills"] += int(book["fill_state"] == "filled")
        result["known_no_fills"] += int(book["fill_state"] == "known_no_fill")
        result["unknown_fills"] += int(book["fill_state"] == "unknown")
        result["unknown_cash"] += int(book["cash_state"] == "unknown")
        result["settled_fills"] += int(book["cash_state"] == "settled")
        if book["cash_state"] == "settled":
            result["wins"] += int(_exact(book["net_cash"]) > 0)
            for field in sums:
                sums[field] += _exact(book[field])
    return {**result, **{key: _wire(value) for key, value in sums.items()}}


def _week(day):
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02}"


def _summary(calendar, queries, episodes, diagnostics):
    calendar, queries, episodes = tuple(calendar), tuple(queries), tuple(episodes)
    _check(calendar and tuple(sorted(set(calendar))) == calendar
           and all(date.fromisoformat(day).isoformat() == day for day in calendar), "original calendar differs")
    _check(len({row["key"] for row in queries}) == len(queries)
           and len({row["roster"]["event_id"] for row in episodes}) == len(episodes), "duplicate summary population")
    roster = {row["roster"]["event_id"]: row for row in episodes}
    owners, cluster_ids = {}, {}
    for row in episodes:
        event = row["roster"]
        _event(event)
        _check(event["session"] in calendar, "episode escaped original calendar")
        owner = event["session"], event["symbol"]
        _check(owners.setdefault(event["cluster_id"], owner) == owner
               and cluster_ids.setdefault(owner, event["cluster_id"]) == event["cluster_id"], "cluster original owner differs")
    for row in queries:
        _check(row["event_id"] in roster and row["session"] in calendar, "query escaped original roster")
        episode = roster[row["event_id"]]
        _check(all(row[field] == episode["roster"][field] for field in ("session", "symbol", "cluster_id"))
               and row["fold"] == episode["fold"]
               and row["selected"] == (row["key"] == episode["choice"]["key"]), "query summary owner/selection differs")
    query_keys = {row["key"] for row in queries}
    for episode in episodes:
        key = episode["choice"]["key"]
        _check(key is None or key in query_keys, "summary selected query missing")
    weeks = sorted({_week(day) for day in calendar})
    arms = ["|".join(arm) for arm in _ARMS]
    all_books = {arm: [] for arm in arms}
    weekly = {week: {arm: [] for arm in arms} for week in weeks}
    daily = {day: {arm: [] for arm in arms} for day in calendar}
    symbols = {symbol: {arm: [] for arm in arms} for symbol in sorted({row["roster"]["symbol"] for row in episodes})}
    for row in episodes:
        _check(len(row["arms"]) == len(_ARMS), "episode arm count differs")
        for index, arm in enumerate(arms):
            book, event = row["arms"][index], row["roster"]
            _check("|".join(book[field] for field in ("control", "entry_policy", "management")) == arm,
                   "episode arm order differs")
            all_books[arm].append(book)
            weekly[_week(event["session"])][arm].append(book)
            daily[event["session"]][arm].append(book)
            symbols[event["symbol"]][arm].append(book)
    weekly, daily, symbols = ({key: {arm: _totals(books) for arm, books in group.items()}
                               for key, group in collection.items()} for collection in (weekly, daily, symbols))
    totals = {arm: _totals(books) for arm, books in all_books.items()}
    for arm, value in totals.items():
        basis = _exact(value["entry_notional"])
        value.update(net_return_on_settled_notional=_wire(_exact(value["net_cash"]) / basis) if basis else None,
                     gross_return_on_settled_notional=_wire(_exact(value["gross_cash"]) / basis) if basis else None,
                     calendar_weeks=len(weeks), weeks_at_least_15_fills=sum(weekly[week][arm]["fills"] >= 15 for week in weeks),
                     fills_per_calendar_week=_wire(Fraction(value["fills"], len(weeks))))
    known = [row["decomposition"] for row in episodes if row["decomposition"]["status"] == "known"]
    expanded = [row for row in known if row["label_error"] is not None and row["cap_minus_label_cash"] is not None]
    three = ("predicted_cash", "cap_residual", "entry_effect", "holding_effect", "total_residual")
    four = ("label_error", "cap_minus_label_cash", "entry_effect", "holding_effect", "total_residual")
    selected = [row for row in queries if row["selected"]]
    return dict(schema="EN-value-summary-v1", calendar_sessions=len(calendar), calendar_weeks=len(weeks),
                original_episodes=len(episodes), query_frames=len(queries),
                calibration={"all": _calibration(queries), "selected": _calibration(selected)},
                prediction_bins={"all": _bins(queries), "selected": _bins(selected)},
                arms=totals, weekly=weekly, date_concentration=daily, symbol_concentration=symbols,
                decomposition=dict(statuses=dict(Counter(row["decomposition"]["status"] for row in episodes)),
                                   three_term={key: _wire(sum((_exact(row[key]) for row in known), Fraction())) for key in three},
                                   expanded_known=len(expanded),
                                   four_term={key: _wire(sum((_exact(row[key]) for row in expanded), Fraction())) for key in four}),
                selected_target_reconciliation=dict(statuses=dict(Counter(row["reconciliation"]["status"] for row in episodes)),
                    cap_minus_label_cash=_wire(sum((_exact(row["reconciliation"]["cap_minus_label_cash"]) for row in episodes
                                                  if row["reconciliation"]["cap_minus_label_cash"] is not None), Fraction()))),
                fit_diagnostics=diagnostics,
                claim="Discovery descriptive audit; nominal episode books, not a simultaneous funded account; no causal identification or promotion")


def verify_summary(calendar, queries, episodes, fit_diagnostics, summary):
    _same(summary, _summary(calendar, queries, episodes, fit_diagnostics), "summary", statistical=True)
    # Fit evidence is copied, not recomputed numerical statistics: no tolerance.
    _same(summary["fit_diagnostics"], fit_diagnostics, "fit_diagnostics")
    return True
