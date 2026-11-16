"""Invented EN saved-record geometry; no original reads, model fit or orders."""

from datetime import date, timedelta
from fractions import Fraction
from functools import lru_cache

from research import intraday_event_evaluation as evaluation
from research import intraday_event_labels as labels
from research import intraday_event_models as models
from research.intraday_event_features import VolumeObservation
from research.intraday_event_scenarios import EventFrame, MinuteBar


FULL_EVENTS = 160 * 23
VARIANTS = 18
ARMS = (("model", "next_open_capped", "fixed_exit"),
        ("model", "reference_limit_one_shot", "fixed_exit"),
        ("model", "reference_limit_one_shot", "forecast_full_exit"),
        ("confirmation", "reference_limit_one_shot", "forecast_full_exit"))


def _prediction(query, kind, head_statuses):
    qi, qw = (0.002, 0.001)
    if kind in (1, 11, 14):
        qi, qw = 0.001, 0.002
    elif kind in (2, 6):
        qi, qw = -0.001, -0.002
    elif kind == 15:
        qi = qw = 0.002
    values = {"path_class": [0.3, 0.4, 0.3], "fill": 0.8,
              "net_immediate": qi, "net_wait3": None if kind == 3 else qw,
              "remaining_gross": None}
    statuses = {**head_statuses, "remaining_gross": "not_applicable_to_state"}
    return {"key": query.key, "session": query.session, "query_status": "supported_features",
            "predictions": values, "head_status": statuses}


@lru_cache(maxsize=VARIANTS)
def _prototype(kind):
    day, symbol, event_id = "2024-01-02", "INVENTED_TEMPLATE", "C:INVENTED_TEMPLATE"
    cluster = day + ":" + symbol
    start = 125 if kind == 13 else 25
    clocks = [start + 3 * i for i in range(6)]
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 3) for i in range(150)}
    if kind == 7:
        del bars[26]
    if kind == 8:
        del bars[56]
    if kind == 9:
        bars[26] = MinuteBar(26, 101, 101, 100, 100, 29)
    if kind == 10:
        for i in range(30, 150):
            bars[i] = MinuteBar(i, 101, 101, 101, 101, i + 3)
    if kind == 11:
        bars[22] = MinuteBar(22, 10_000_000, 10_000_000, 10_000_000, 10_000_000, 25)
    if kind == 14:
        bars[25] = MinuteBar(25, 100, 100, 97, 97, 28)
    volumes = {i: VolumeObservation(i, 100, i + 3) for i in bars}
    peers = {f"PEER{i}": bars for i in range(6)}
    market = evaluation.SessionMarket(150, bars, volumes, peers)
    episode = evaluation.Episode(day, symbol, event_id, cluster, "C_FIRSTPULLBACK", start)
    head_statuses = dict.fromkeys(models.HEADS, "trained_tcn")
    if kind == 3:
        head_statuses["net_wait3"] = "unsupported"
    if kind == 12:
        head_statuses["remaining_gross"] = "unsupported"
    wrappers, predictions, target_rows, candidates = [], {}, {}, []
    for index, clock in enumerate(clocks):
        if kind == 17 or (kind == 16 and index == 0):
            continue
        state = ("WAITING_PULLBACK" if index < 2 else "PULLBACK" if index < 5 else
                 "INVALIDATED" if kind == 6 else "CONFIRMED")
        terminal = index == 5
        offset = clock - 3
        frame = EventFrame("C_FIRSTPULLBACK", event_id, cluster, start - 3, offset, clock,
            start - 3, offset + 1, "at_onset" if index == 0 else "terminal" if terminal else
            "first_pullback" if index == 2 else "checkpoint", state, terminal,
            Fraction(2), Fraction(104), Fraction(98), ())
        reference_offset = max(i for i, bar in bars.items() if i < clock and bar.available_offset <= clock)
        reference = bars[reference_offset].close
        label_query = labels.LabelQuery(event_id, "C_FIRSTPULLBACK", state, terminal,
            start - 3, offset, clock, reference_offset, reference, Fraction(2), Fraction(98), 150)
        key = event_id + "@" + str(clock)
        query = None if kind == 5 else models.Query(key, day, symbol, event_id, cluster, clock,
            "C_FIRSTPULLBACK", state, ((0.0,) * 13,) * 64, (0.0,) * 16,
            peer=None if kind == 4 else (0.0, 0.0, 0.5))
        wrapper = {"kind": "query", "query_kind": "entry", "key": key,
            "session": day, "symbol": symbol, "continuous_length": 150, "frame": frame.as_dict(),
            "query_status": "supported_features" if query else "unsupported_chart",
            "query": evaluation._plain(query), "label_query": evaluation._plain(label_query),
            "reference_close": str(reference), "peer_support": None, "origin": None}
        wrappers.append(wrapper)
        candidate = evaluation.Candidate(episode, frame, label_query, query)
        if query is not None and query.peer is not None:
            candidates.append(candidate)
            predictions[key] = _prediction(query, kind, head_statuses)
        target = labels.build_targets(label_query, bars)
        admitted = target.entry_admissibility == "admissible"
        known = {"path_class": target.path.value if target.path.matured_offset is not None else None,
            "fill": target.immediate.fill if admitted and target.immediate.fill_matured_offset is not None else None,
            "net_immediate": target.immediate.value if admitted and target.immediate.matured_offset is not None else None,
            "net_wait3": target.wait3.value if admitted and target.wait3.matured_offset is not None else None,
            "remaining_gross": None}
        training = None if query is None else {"key": key,
            "matured_session": day if any(value is not None for value in known.values()) else None, **known}
        target_rows[key] = evaluation._plain({"kind": "labels", "query_kind": "entry", "key": key,
            "session": day, "symbol": symbol, "targets": target, "training_labels": training})
    binding = "a" * 64
    def held_predict(query):
        values = dict.fromkeys(models.HEADS)
        values["remaining_gross"] = None if kind == 12 else -0.001
        statuses = dict.fromkeys(models.HEADS, "not_applicable_to_state")
        statuses["remaining_gross"] = head_statuses["remaining_gross"]
        return {"key": query.key, "session": day, "query_status": "supported_features",
                "predictions": values, "head_status": statuses}
    books, trace = {}, None
    for control in ("model", "confirmation"):
        policy = evaluation.freeze_policy((episode,), candidates,
            tuple(predictions.values()) if control == "model" else (), control=control,
            model="tcn", scope="common_chart_peer", prediction_binding=binding)
        if control == "model":
            trace = evaluation._plain(policy.trace)
        for row in evaluation.replay_policy(policy, {(day, symbol): market},
                held_predict=held_predict, prediction_binding=binding):
            arm = tuple(row[k] for k in ("control", "entry_policy", "management"))
            if arm in ARMS:
                books["|".join(arm)] = evaluation._plain(row)
    confirmed = kind != 6 and kind != 17
    event = {"event_id": event_id, "session": day, "symbol": symbol, "cluster_id": cluster,
        "onset_decision_offset": start, "terminal_state": "CONFIRMED" if confirmed else "INVALIDATED",
        "confirmation_key": event_id + "@" + str(clocks[-1]) if confirmed else None,
        "confirmation_decision_offset": clocks[-1] if confirmed else None,
        "confirmation_reference": str(bars[clocks[-1] - 3].close) if confirmed else None}
    return {"event": event, "wrappers": wrappers, "predictions": predictions, "labels": target_rows,
            "ledgers": books, "head_statuses": head_statuses, "trace": trace}


def case(index=0, *, day="2024-01-02", symbol="INVENTED00", fold=0):
    event_id = f"C:{day}:{symbol}:{index}"
    def clone(value):
        if isinstance(value, dict):
            return {clone(key): clone(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [clone(item) for item in value]
        if isinstance(value, str):
            return value.replace("C:INVENTED_TEMPLATE", event_id).replace("2024-01-02", day).replace("INVENTED_TEMPLATE", symbol)
        return value
    return {**clone(_prototype(index % VARIANTS)), "fold": fold}


def invented_cases(calendar_sessions=160, names=23):
    calendar, day = [], date(2024, 1, 2)
    while len(calendar) < calendar_sessions:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    return calendar, (case(i * names + name, day=session, symbol=f"INVENTED{name:02}", fold=min(i // 40, 3))
                      for i, session in enumerate(calendar) for name in range(names))


def full_cases():
    return invented_cases()
