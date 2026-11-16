"""Pure EN audit of saved entry forecasts and different nominal policy books.

No source loader, inference, refit or price-derived target is present. Callers
seal causal records and choices before supplying archived labels and ledgers.
Only descriptive statistics cross the floating numerical boundary.
"""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
import math

from research import intraday_event_attribution as attribution
from research import intraday_event_execution as execution
from research.intraday_event_evaluation import policy_support
from research.intraday_event_labels import LabelQuery
from research.intraday_event_models import HEADS


Q_HEADS = ("net_immediate", "net_wait3")
ARM_KEYS = (("model", "next_open_capped", "fixed_exit"),
            ("model", "reference_limit_one_shot", "fixed_exit"),
            ("model", "reference_limit_one_shot", "forecast_full_exit"),
            ("confirmation", "reference_limit_one_shot", "forecast_full_exit"))
BINS = ("below_minus33bp", "minus33bp_to_zero", "zero", "zero_to_33bp", "above_33bp")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _amount(value):
    _require(type(value) in (str, int, Fraction), "Exact amount required; no floats")
    return Fraction(value)


def _maybe(value):
    return None if value is None else str(_amount(value))


def _prediction(value):
    if value is None:
        return None
    _require(type(value) in (float, int, str, Fraction), "Finite statistical prediction required")
    number = float(Fraction(value)) if isinstance(value, str) else float(value)
    _require(math.isfinite(number), "Nonfinite statistical prediction")
    return str(Fraction(str(number)))


def _plain(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def causal_query(event, wrapper, prediction, fold):
    """Compact a native entry wrapper without accepting outcomes or features later."""
    f, raw, q = wrapper["frame"], wrapper["label_query"], wrapper["query"]
    event_id, day, symbol, cluster = (event[k] for k in ("event_id", "session", "symbol", "cluster_id"))
    _require(type(fold) is int and fold >= 0, "Original fold required")
    _require(wrapper["kind"] == "query" and wrapper["query_kind"] == "entry"
             and f["family"] == "C_FIRSTPULLBACK", "Only original C entry wrappers are admitted")
    clock = f["decision_offset"]
    _require(wrapper["key"] == f"{event_id}@{clock}"
             and (wrapper["session"], wrapper["symbol"], f["event_id"], f["overlap_group_id"])
             == (day, symbol, event_id, cluster)
             and type(event["onset_decision_offset"]) is int and clock >= event["onset_decision_offset"],
             "Wrapper differs from original episode identity or clock")
    bound = LabelQuery(**{**raw, **{name: None if raw[name] is None else _amount(raw[name])
                       for name in ("reference_close", "scale", "structure_stop")}})
    _require((bound.event_id, bound.family, bound.state, bound.terminal, bound.onset_offset,
              bound.event_offset, bound.decision_offset, bound.scale, bound.structure_stop)
             == (event_id, f["family"], f["state"], f["terminal"], f["onset_offset"], f["offset"],
                 clock, _amount(f["scale"]), _amount(f["structure"]))
             and bound.session_length == wrapper["continuous_length"]
             and _maybe(wrapper["reference_close"]) == _maybe(bound.reference_close),
             "Sealed label query differs from causal wrapper")
    if q is not None:
        _require(tuple(q[k] for k in ("key", "session", "symbol", "episode_id", "cluster_id", "family", "state", "decision_offset"))
                 == (wrapper["key"], day, symbol, event_id, cluster, f["family"], f["state"], clock)
                 and q.get("original_episode_id") in (None, event_id) and q.get("position") is None,
                 "Query identity differs from entry wrapper")
    chart, peer = q is not None, q is not None and q["peer"] is not None
    supported = chart and peer
    values, statuses = dict.fromkeys(Q_HEADS), dict.fromkeys(HEADS, "prediction_absent")
    prediction_status = "absent"
    if prediction is not None:
        _require(supported and set(prediction) == {"key", "session", "query_status", "predictions", "head_status"}
                 and prediction["key"] == wrapper["key"] and prediction["session"] == day
                 and set(prediction["predictions"]) == set(HEADS) and set(prediction["head_status"]) == set(HEADS),
                 "Prediction identity or common scope differs")
        prediction_status, statuses = prediction["query_status"], dict(prediction["head_status"])
        _require(prediction["predictions"]["remaining_gross"] is None, "Held prediction on entry wrapper")
        if prediction_status != "supported_features":
            _require(all(v is None for v in prediction["predictions"].values()), "Unsupported prediction carries values")
        values = {head: _prediction(prediction["predictions"][head]) for head in Q_HEADS}
    cap = None if bound.reference_close is None else bound.reference_close + bound.scale
    denominator = None
    if cap is not None and bound.structure_stop < cap:
        quantity = int(min(execution.FIRST_RISK // (cap * (1 + execution.COST) - bound.structure_stop),
                           min(execution.CAPITAL / 50, execution.CAPITAL / (1 + execution.COST)) // cap))
        denominator = quantity * cap
    if bound.terminal and bound.state != "CONFIRMED":
        reason, admission = "terminal_not_admissible", "not_admissible"
    elif clock + 31 >= bound.session_length:
        reason, admission = "expiry_not_inside_session", "not_admissible"
    elif bound.reference_close is None:
        reason, admission = "reference_unknown", "unknown"
    elif bound.structure_stop >= cap:
        reason, admission = "stop_not_below_cap", "not_admissible"
    else:
        reason, admission = None, "admissible"
    phase = ("onset" if clock == event["onset_decision_offset"] else
             {"WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback", "CONFIRMED": "confirmation"}
             .get(bound.state, "invalid_or_terminal"))
    return {"schema": "EN-causal-query-v1", "key": wrapper["key"], "event_id": event_id,
            "session": day, "symbol": symbol, "cluster_id": cluster, "fold": fold,
            "decision_offset": clock, "onset_decision_offset": event["onset_decision_offset"],
            "state": bound.state, "terminal": bound.terminal, "phase": phase,
            "clock_bin": "<60" if clock < 60 else "60_to_180" if clock < 180 else "180_plus",
            "chart_supported": chart, "peer_supported": peer, "scope_supported": supported,
            "query_status": wrapper["query_status"], "prediction_status": prediction_status,
            "head_status": statuses, "admission_reason": reason, "entry_admissibility": admission,
            "initial_denominator": _maybe(denominator), "session_length": bound.session_length,
            "predictions": values, "label_query": _plain(raw)}


def select_event(event, causal_records):
    """Reconstruct the archived common-scope first-positive paired-head choice."""
    records = sorted(causal_records, key=lambda r: r["decision_offset"])
    _require(all(r["event_id"] == event["event_id"] and r["session"] == event["session"]
                 and r["symbol"] == event["symbol"] and r["cluster_id"] == event["cluster_id"] for r in records)
             and len({r["key"] for r in records}) == len(records)
             and len({r["decision_offset"] for r in records}) == len(records), "Duplicate or foreign causal query")
    selected, action, reason, denominator, trace = None, "no_commit", "no_causal_frame", None, []
    supported = [r for r in records if r["scope_supported"]]
    for row in supported:
        if denominator is None:
            denominator = row["initial_denominator"]
        qi, qw = (None if row["predictions"][h] is None else _amount(row["predictions"][h]) for h in Q_HEADS)
        unavailable = row["admission_reason"]
        if unavailable is not None:
            reason = unavailable
        elif qi is None or qw is None:
            reason = "missing_paired_value_heads"
        elif max(qi, qw) <= 0:
            reason = "observe_without_commitment"
        else:
            selected, action, reason = row, "immediate" if qi >= qw else "wait3", "first_positive_paired_value"
        trace.append([event["event_id"], row["key"], row["decision_offset"], row["state"],
                      action if selected else "abstain" if unavailable or qi is None or qw is None else "observe",
                      reason, _maybe(qi), _maybe(qw)])
        if selected is not None:
            denominator = selected["initial_denominator"]
            break
    values = dict.fromkeys(Q_HEADS) if selected is None else selected["predictions"]
    return {"event_id": event["event_id"], "key": None if selected is None else selected["key"],
            "decision_offset": None if selected is None else selected["decision_offset"],
            "state": "NO_COMMIT" if selected is None else selected["state"], "action": action, "reason": reason,
            "initial_denominator": denominator, **values,
            "selected_prediction": None if selected is None else values["net_" + action],
            "trace": trace, "scope_frames": len(supported)}


def _head(causal, target, training, head):
    delay = 0 if head == "net_immediate" else 3
    clock, length = causal["decision_offset"] + delay, causal["session_length"]
    denominator = causal["initial_denominator"]
    _require((target["decision_offset"], target["entry_offset"], target["expiry_offset"])
             == (clock, clock + 1, clock + 31) and _maybe(target["denominator"]) == denominator,
             "Target changed clock or original denominator")
    mature, fill_mature = target["matured_offset"], target["fill_matured_offset"]
    for value in (mature, fill_mature):
        _require(value is None or type(value) is int and causal["decision_offset"] <= value < length,
                 "Target maturity outside original session")
    value, net = _maybe(target["value"]), _maybe(target["net_cash"])
    _require(target["fill"] is None or type(target["fill"]) is int and target["fill"] in (0, 1),
             "Invalid target fill indicator")
    admitted = causal["entry_admissibility"] == "admissible"
    if value is not None:
        _require(net is not None, "Known target value lacks exact cash")
        if target["status"] in ("no_entry", "no_fill"):
            _require(_amount(value) == _amount(net) == 0, "No-entry target cash differs")
        else:
            _require(denominator is not None and _amount(denominator) > 0
                     and _amount(value) == _amount(net) / _amount(denominator), "Target cash ratio differs")
    saved = None if training is None else _maybe(training[head])
    expected = value if admitted and mature is not None and causal["chart_supported"] else None
    _require(saved == expected, "Saved training target differs from original admissibility/maturity")
    state = ("inapplicable" if causal["entry_admissibility"] == "not_admissible" else
             "unknown_chart" if not causal["chart_supported"] else "unknown_admission" if not admitted else
             "undefined_zero_denominator" if denominator is not None and _amount(denominator) == 0 else
             "immature" if mature is None else "unknown" if expected is None else "known")
    observed = expected if state == "known" else None
    prediction = causal["predictions"][head]
    scored = causal["scope_supported"] and prediction is not None and observed is not None
    return {"prediction": prediction, "target": observed, "training_target": saved, "target_state": state,
            "raw_status": target["status"], "reason": target["reason"], "net_cash": net,
            "denominator": denominator, "fill": target["fill"], "matured_offset": mature,
            "fill_matured_offset": fill_mature, "scored": scored,
            "error": str(_amount(prediction) - _amount(observed)) if scored else None}


def measure_query(causal, label, choice):
    """Reconstruct saved training semantics; operational terminal zero is excluded."""
    _require(label["kind"] == "labels" and label["query_kind"] == "entry"
             and (label["key"], label["session"], label["symbol"])
             == (causal["key"], causal["session"], causal["symbol"])
             and choice["event_id"] == causal["event_id"], "Label or choice identity differs")
    targets, training = label["targets"], label["training_labels"]
    _require((targets["event_id"], targets["decision_offset"], targets["entry_admissibility"])
             == (causal["event_id"], causal["decision_offset"], causal["entry_admissibility"])
             and _maybe(targets["original_requested_notional"]) == causal["initial_denominator"],
             "Original target admission or denominator differs")
    reasons = {None: "causal_entry_request_supported", "terminal_not_admissible": "terminal_state_not_entry_admissible",
               "expiry_not_inside_session": "expiry_not_inside_session", "reference_unknown": "initial_reference_unknown",
               "stop_not_below_cap": "stop_not_below_guard_cap"}
    _require(targets["schema"] == "EL-separated-offline-targets-v1"
             and targets["entry_reason"] == reasons[causal["admission_reason"]], "Original target admission reason differs")
    _require((training is not None) == causal["chart_supported"], "Training label chart support differs")
    if training is not None:
        _require(training["key"] == causal["key"] and training["remaining_gross"] is None
                 and training["matured_session"] in (None, causal["session"]), "Training label identity differs")
        known = any(training[h] is not None for h in HEADS)
        _require((training["matured_session"] is not None) == known, "Training maturity differs")
        path, immediate = targets["path"], targets["immediate"]
        _require(training["path_class"] == (path["value"] if path["matured_offset"] is not None else None)
                 and training["fill"] == (immediate["fill"] if causal["entry_admissibility"] == "admissible"
                     and immediate["fill_matured_offset"] is not None else None), "Saved auxiliary training target differs")
    heads = {h: _head(causal, targets[h.removeprefix("net_")], training, h) for h in Q_HEADS}
    joint = all(heads[h]["scored"] for h in Q_HEADS)
    predicted = _amount(heads[Q_HEADS[1]]["prediction"]) - _amount(heads[Q_HEADS[0]]["prediction"]) if joint else None
    observed = _amount(heads[Q_HEADS[1]]["target"]) - _amount(heads[Q_HEADS[0]]["target"]) if joint else None
    selected = choice["key"] == causal["key"]
    return {**causal, "schema": "EN-query-value-v1", "selected": selected,
            "selected_head": "net_" + choice["action"] if selected else None, "heads": heads,
            "paired": {"scored": joint, "predicted_wait_advantage": _maybe(predicted),
                       "observed_wait_advantage": _maybe(observed), "error": _maybe(predicted - observed) if joint else None,
                       "direction_agreement": ((predicted > 0) - (predicted < 0) == (observed > 0) - (observed < 0)) if joint else None}}


def measure_episode(event, choice, measured_by_key, ledgers_by_arm, head_statuses, fold):
    """Rebuild four archived books and retain genuine label-versus-policy gaps."""
    _require(set(ledgers_by_arm) == set(ARM_KEYS) and choice["event_id"] == event["event_id"],
             "Four original policies and one original choice required")
    support = policy_support(head_statuses)
    selected = None if choice["key"] is None else measured_by_key.get(choice["key"])
    _require(choice["key"] is None or selected is not None and selected["selected"]
             and selected["event_id"] == event["event_id"] and selected["fold"] == fold,
             "Selected query measurement missing or foreign")
    if selected is not None:
        _require(choice["action"] in ("immediate", "wait3")
                 and (choice["key"], choice["decision_offset"], choice["state"], choice["initial_denominator"])
                 == (selected["key"], selected["decision_offset"], selected["state"], selected["initial_denominator"])
                 and selected["selected_head"] == "net_" + choice["action"]
                 and choice["selected_prediction"] == selected["predictions"][selected["selected_head"]]
                 and all(choice[h] == selected["predictions"][h] for h in Q_HEADS),
                 "Choice differs from its sealed query prediction or denominator")
    books = []
    for arm in ARM_KEYS:
        row = ledgers_by_arm[arm]
        _require(tuple(row[k] for k in ("control", "entry_policy", "management")) == arm
                 and (row["model"], row["scope"], row["family"]) == ("tcn", "common_chart_peer", "C_FIRSTPULLBACK")
                 and tuple(row[k] for k in ("original_episode_id", "session", "symbol", "cluster_id"))
                 == tuple(event[k] for k in ("event_id", "session", "symbol", "cluster_id")), "Ledger identity differs")
        if arm[0] == "model":
            _require((row["candidate_key"], row["action"], row["selection_reason"], row["state"], _maybe(row["initial_denominator"]))
                     == (choice["key"], choice["action"], choice["reason"], choice["state"], choice["initial_denominator"]),
                     "Archived model selection differs")
            clock = row["entry_decision_offset"]
            _require(clock is None or selected is not None and clock == selected["decision_offset"] + (3 if choice["action"] == "wait3" else 0),
                     "Archived model execution decision clock differs")
            if selected is None:
                _require(clock is None, "No-commit has an execution clock")
            elif choice["action"] == "immediate":
                _require(clock == selected["decision_offset"], "Immediate commitment lost its request clock")
            if clock is not None:
                report, causal = row["execution"], selected["label_query"]
                reference, scale, stop = (_amount(report["reference_close"]), _amount(causal["scale"]),
                                          _amount(causal["structure_stop"]))
                cap = reference + scale
                _require(reference > 0 and 0 < stop < cap, "Archived request has invalid price geometry")
                quantity = int(min(execution.FIRST_RISK // (cap * (1 + execution.COST) - stop),
                                   min(execution.CAPITAL / 50, execution.CAPITAL / (1 + execution.COST)) // cap))
                _require((report["decision_offset"], report["planned_entry_offset"], report["expiry_offset"])
                         == (clock, clock + 1, clock + 31) and _amount(report["guard_cap"]) == cap
                         and _amount(report["structure_stop"]) == stop and report["requested_quantity"] == quantity
                         and (choice["action"] != "immediate" or reference == _amount(causal["reference_close"])),
                         "Archived request differs from selected causal geometry or reserved quantity")
        else:
            attribution._identity(event, arm, row)
        available = (support["entry"] or arm[0] != "model") and (support["holding"] or arm[2] == "fixed_exit")
        measured = row if available else {**row, "outcome": {**row["outcome"], "status": "model_unavailable", "net": None, "gross": None}}
        book = attribution._book(arm, measured)
        book["available"] = available
        if not available:
            # Mechanical fallback fills are not fills of a learnable policy.
            book.update(fill_state="unavailable", cash_state="unavailable", filled_quantity=None,
                        entry_offset=None, entry_price=None, entry_notional=None)
        books.append(book)
    cap, fixed, full = (ledgers_by_arm[a] for a in ARM_KEYS[:3])
    _require(attribution._entry_signature(fixed) == attribution._entry_signature(full), "Holding policy changed matched entry")
    _require(attribution._entry_signature(cap)[:10] == attribution._entry_signature(fixed)[:10], "Entry policy changed initial request")
    if fixed["execution"]["filled_quantity"]:
        _require(attribution._entry_signature(cap) == attribution._entry_signature(fixed), "Reference fill differs from capped subset")
    rec = dict.fromkeys(("label_net_cash", "cap_net_cash", "cap_minus_label_cash", "label_value", "cap_value"))
    rec["status"] = "no_commit" if selected is None else "unknown"
    decomposition = dict.fromkeys(("denominator", "prediction", "predicted_cash", "cap_residual", "entry_effect",
                                   "holding_effect", "total_residual", "label_error", "cap_minus_label_cash"))
    decomposition["status"] = "no_commit" if selected is None else "unknown"
    if selected is not None:
        head = selected["heads"][selected["selected_head"]]
        label_net = head["net_cash"] if head["target"] is not None else None
        cap_net = books[0]["net_cash"]
        denominator = _amount(choice["initial_denominator"])
        rec.update(label_net_cash=label_net, cap_net_cash=cap_net, label_value=head["target"],
                   cap_value=str(_amount(cap_net) / denominator) if cap_net is not None and denominator > 0 else None)
        if label_net is not None and cap_net is not None:
            gap = _amount(cap_net) - _amount(label_net)
            rec.update(status="matched" if gap == 0 else "cash_mismatch", cap_minus_label_cash=str(gap))
        elif label_net is not None or cap_net is not None:
            rec["status"] = "support_mismatch"
        if denominator > 0 and all(b["net_cash"] is not None and b["available"] for b in books[:3]):
            q = _amount(choice["selected_prediction"])
            a, b, c = (_amount(book["net_cash"]) for book in books[:3])
            residual, entry, holding, total = a - denominator * q, b - a, c - b, c - denominator * q
            _require(total == residual + entry + holding, "Exact three-term identity differs")
            decomposition.update(status="known", denominator=str(denominator), prediction=str(q),
                predicted_cash=str(denominator * q), cap_residual=str(residual), entry_effect=str(entry),
                holding_effect=str(holding), total_residual=str(total))
            if label_net is not None:
                error, gap = _amount(label_net) - denominator * q, a - _amount(label_net)
                _require(total == error + gap + entry + holding, "Exact expanded identity differs")
                decomposition.update(label_error=str(error), cap_minus_label_cash=str(gap))
    return {"schema": "EN-episode-value-v1", "roster": dict(event), "fold": fold, "choice": dict(choice),
            "arms": books, "reconciliation": rec, "decomposition": decomposition}


def _weights(rows):
    dates, clusters, events = defaultdict(set), defaultdict(set), Counter()
    for row in rows:
        day, cluster, event = row["session"], row["cluster_id"], row["event_id"]
        dates[day].add(cluster)
        clusters[day, cluster].add(event)
        events[day, cluster, event] += 1
    return [Fraction(1, len(dates) * len(dates[r["session"]]) * len(clusters[r["session"], r["cluster_id"]])
                     * events[r["session"], r["cluster_id"], r["event_id"]]) for r in rows]


def _counts(rows):
    return {"frames": len(rows), "episodes": len({r["event_id"] for r in rows}),
            "clusters": len({r["cluster_id"] for r in rows}), "dates": len({r["session"] for r in rows})}


def _metrics(rows, getter):
    if not rows:
        return dict.fromkeys(("prediction_mean", "observed_mean", "signed_error", "mae", "rmse",
                              "zero_signed_error", "zero_mae", "zero_rmse"))
    pairs = [(float(_amount(getter(r)[0])), float(_amount(getter(r)[1]))) for r in rows]
    weights = [float(w) for w in _weights(rows)]
    def average(values):
        value = math.fsum(w * x for w, x in zip(weights, values))
        _require(math.isfinite(value), "Nonfinite descriptive metric")
        return value
    p, y = zip(*pairs)
    errors = [a - b for a, b in pairs]
    return {"prediction_mean": average(p), "observed_mean": average(y), "signed_error": average(errors),
            "mae": average([abs(e) for e in errors]), "rmse": math.sqrt(average([e * e for e in errors])),
            "zero_signed_error": -average(y), "zero_mae": average([abs(v) for v in y]),
            "zero_rmse": math.sqrt(average([v * v for v in y]))}


def _head_summary(rows, head):
    scored = [r for r in rows if r["heads"][head]["scored"]]
    positive = Counter({"positive": 0, "zero": 0, "negative": 0, "unknown": 0})
    for row in rows:
        h = row["heads"][head]
        if h["prediction"] is not None and _amount(h["prediction"]) > 0:
            value = None if h["target"] is None else _amount(h["target"])
            positive["unknown" if value is None else "positive" if value > 0 else "negative" if value < 0 else "zero"] += 1
    return {"population": _counts(rows), "scored": _counts(scored),
            "chart_supported": sum(r["chart_supported"] for r in rows),
            "peer_supported": sum(r["peer_supported"] for r in rows),
            "scope_supported": sum(r["scope_supported"] for r in rows),
            "prediction_known": sum(r["heads"][head]["prediction"] is not None for r in rows),
            "target_states": dict(sorted(Counter(r["heads"][head]["target_state"] for r in rows).items())),
            "positive_prediction_outcomes": dict(positive),
            **_metrics(scored, lambda r: (r["heads"][head]["prediction"], r["heads"][head]["target"]))}


def _group_summary(rows):
    paired = [r for r in rows if r["paired"]["scored"]]
    weights = _weights(paired)
    return {"heads": {head: _head_summary(rows, head) for head in Q_HEADS},
            "paired": {"population": _counts(rows), "scored": _counts(paired),
                       "direction_agreement": sum(r["paired"]["direction_agreement"] for r in paired),
                       "weighted_direction_agreement": float(sum((w for r, w in zip(paired, weights)
                           if r["paired"]["direction_agreement"]), Fraction())) if paired else None,
                       "predicted_ties": sum(_amount(r["paired"]["predicted_wait_advantage"]) == 0 for r in paired),
                       "observed_ties": sum(_amount(r["paired"]["observed_wait_advantage"]) == 0 for r in paired),
                       **_metrics(paired, lambda r: (r["paired"]["predicted_wait_advantage"], r["paired"]["observed_wait_advantage"]))}}


def _bin(value):
    value, cost = _amount(value), Fraction(33, 10_000)
    return BINS[0] if value < -cost else BINS[1] if value < 0 else BINS[2] if value == 0 else BINS[3] if value <= cost else BINS[4]


def _cash_totals():
    return {**dict.fromkeys(("opportunities", "unavailable", "commits", "fills", "wins", "known_no_fills",
                             "unknown_fills", "settled_fills", "unknown_cash"), 0),
            **dict.fromkeys(("gross_cash", "net_cash", "cost_cash", "entry_notional"), Fraction())}


def _add_cash(total, book):
    total["opportunities"] += 1
    if not book["available"]:
        total["unavailable"] += 1
        return
    total["commits"] += book["committed"]
    total["fills"] += book["fill_state"] == "filled"
    total["known_no_fills"] += book["fill_state"] == "known_no_fill"
    total["unknown_fills"] += book["fill_state"] == "unknown"
    total["unknown_cash"] += book["cash_state"] == "unknown"
    if book["cash_state"] == "settled":
        total["settled_fills"] += 1
        total["wins"] += _amount(book["net_cash"]) > 0
        for key in ("gross_cash", "net_cash", "cost_cash", "entry_notional"):
            total[key] += _amount(book[key])


def summarize(calendar, query_records, episode_records, fit_diagnostics):
    """Equal date/cluster/episode/frame scoring and full-calendar nominal cash."""
    calendar, queries, episodes = tuple(calendar), list(query_records), list(episode_records)
    _require(calendar and tuple(sorted(set(calendar))) == calendar, "Ordered unique calendar required")
    weeks = {}
    for day in calendar:
        parsed = date.fromisoformat(day)
        _require(parsed.isoformat() == day, "Canonical calendar required")
        iso = parsed.isocalendar()
        weeks[day] = f"{iso.year}-W{iso.week:02}"
    roster = {r["roster"]["event_id"]: r for r in episodes}
    _require(len(roster) == len(episodes), "Duplicate original episode")
    seen, owners, clusters = set(), {}, {}
    for row in queries:
        _require(row["schema"] == "EN-query-value-v1" and row["key"] not in seen and row["session"] in weeks
                 and row["event_id"] in roster, "Duplicate, missing or off-calendar query")
        event = roster[row["event_id"]]
        _require(tuple(row[k] for k in ("session", "symbol", "cluster_id"))
                 == tuple(event["roster"][k] for k in ("session", "symbol", "cluster_id"))
                 and row["fold"] == event["fold"]
                 and row["selected"] == (event["choice"]["key"] == row["key"]), "Query/episode identity differs")
        seen.add(row["key"])
    for record in episodes:
        r = record["roster"]
        _require(record["schema"] == "EN-episode-value-v1" and r["session"] in weeks
                 and (record["choice"]["key"] is None or record["choice"]["key"] in seen), "Missing selected query or off-calendar episode")
        owner = r["session"], r["symbol"]
        _require(owners.setdefault(r["cluster_id"], owner) == owner and clusters.setdefault(owner, r["cluster_id"]) == r["cluster_id"],
                 "Original symbol/session cluster split or mixed")
    calibration, bins = {}, {}
    for selection in ("all", "selected"):
        rows = queries if selection == "all" else [r for r in queries if r["selected"]]
        groups = defaultdict(list)
        groups["whole"] = rows
        for row in rows:
            for key in (f"fold/{row['fold']}", "phase/" + row["phase"], f"fold_phase/{row['fold']}|{row['phase']}", "clock/" + row["clock_bin"]):
                groups[key].append(row)
        calibration[selection] = {key: _group_summary(group) for key, group in sorted(groups.items())}
        bins[selection] = {head: {name: _head_summary([r for r in rows if r["heads"][head]["prediction"] is not None
                                      and _bin(r["heads"][head]["prediction"]) == name], head) for name in BINS} for head in Q_HEADS}
    names = ["|".join(arm) for arm in ARM_KEYS]
    fresh = lambda: {name: _cash_totals() for name in names}
    totals, dates, symbols = fresh(), {day: fresh() for day in calendar}, {}
    weekly = {week: fresh() for week in sorted(set(weeks.values()))}
    statuses, rec_statuses = Counter(), Counter()
    decomposition = {key: Fraction() for key in ("predicted_cash", "cap_residual", "entry_effect", "holding_effect", "total_residual")}
    expanded = {key: Fraction() for key in ("label_error", "cap_minus_label_cash", "entry_effect", "holding_effect", "total_residual")}
    expanded_count, rec_gap = 0, Fraction()
    for record in episodes:
        r = record["roster"]
        symbols.setdefault(r["symbol"], fresh())
        _require(len(record["arms"]) == len(ARM_KEYS) and [tuple(b[k] for k in ("control", "entry_policy", "management"))
                 for b in record["arms"]] == list(ARM_KEYS), "Duplicate or missing episode book")
        for name, book in zip(names, record["arms"]):
            for group in (totals, dates[r["session"]], symbols[r["symbol"]], weekly[weeks[r["session"]]]):
                _add_cash(group[name], book)
        d, rec = record["decomposition"], record["reconciliation"]
        statuses[d["status"]] += 1
        rec_statuses[rec["status"]] += 1
        if rec["cap_minus_label_cash"] is not None:
            rec_gap += _amount(rec["cap_minus_label_cash"])
        if d["status"] == "known":
            _require(_amount(d["total_residual"]) == sum((_amount(d[k]) for k in ("cap_residual", "entry_effect", "holding_effect")), Fraction()), "Corrupt decomposition")
            for key in decomposition:
                decomposition[key] += _amount(d[key])
            if d["label_error"] is not None:
                expanded_count += 1
                for key in expanded:
                    expanded[key] += _amount(d[key])
                _require(_amount(d["total_residual"]) == sum((_amount(d[k]) for k in ("label_error", "cap_minus_label_cash", "entry_effect", "holding_effect")), Fraction()), "Corrupt expanded decomposition")
    for name, total in totals.items():
        total["net_return_on_settled_notional"] = total["net_cash"] / total["entry_notional"] if total["entry_notional"] else None
        total["gross_return_on_settled_notional"] = total["gross_cash"] / total["entry_notional"] if total["entry_notional"] else None
        total["calendar_weeks"] = len(weekly)
        total["weeks_at_least_15_fills"] = sum(group[name]["fills"] >= 15 for group in weekly.values())
        total["fills_per_calendar_week"] = Fraction(total["fills"], len(weekly))
    return _plain({"schema": "EN-value-summary-v1", "calendar_sessions": len(calendar), "calendar_weeks": len(weekly),
                   "original_episodes": len(episodes), "query_frames": len(queries), "calibration": calibration,
                   "prediction_bins": bins, "arms": totals, "weekly": weekly,
                   "date_concentration": dates, "symbol_concentration": symbols,
                   "decomposition": {"statuses": dict(statuses), "three_term": decomposition,
                                     "expanded_known": expanded_count, "four_term": expanded},
                   "selected_target_reconciliation": {"statuses": dict(rec_statuses), "cap_minus_label_cash": rec_gap},
                   "fit_diagnostics": fit_diagnostics,
                   "claim": "Discovery descriptive audit; nominal episode books, not a simultaneous funded account; no causal identification or promotion"})
