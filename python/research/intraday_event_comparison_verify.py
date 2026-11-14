"""Reconstruct EL action ledgers from a sealed policy and supplied minutes.

Policy selection and model fitting are upstream. The caller supplies the same
artifact-bound inference function for independent reconstruction of held queries.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
import json

from research import intraday_event_execution_reference as execution
from research import intraday_event_features_reference as features
from research import intraday_event_held_reference as held
from research.intraday_event_evaluation import CLAIM, ReplayDay, SessionMarket, verify_policy
from research.intraday_event_execution import EntryRequest, Forecast
from research.intraday_event_models import HEADS, Query


ENTRIES = ("next_open_capped", "reference_limit_one_shot")
MANAGEMENT = ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")


def _entry(choice, market):
    candidate = choice.candidate
    query = candidate.label_query
    if type(market) is not SessionMarket or market.session_length != query.session_length:
        raise ValueError("Comparison calendar differs from sealed candidate")
    clock = query.decision_offset
    visible = sorted((offset, bar) for offset, bar in market.bars.items()
                     if offset < clock and bar.available_offset <= clock)
    if not visible or (visible[-1][0], visible[-1][1].close) != (query.reference_offset, query.reference_close):
        raise ValueError("Comparison decision reference differs from causal minutes")
    original = EntryRequest(clock, query.reference_close, query.scale, query.structure_stop)
    size = execution.evaluate(original, {}, market.session_length)["requested_quantity"]
    if choice.initial_denominator != size * (original.reference_close + original.scale):
        raise ValueError("Comparison initial denominator differs from independent sizing")
    if choice.action == "immediate":
        return original, candidate.frame, None, None
    if choice.action != "wait3":
        raise ValueError("Selected comparison candidate requires immediate or wait3")

    clock += 3
    if clock + 31 >= market.session_length:
        return None, None, "no_entry", "delayed_expiry_not_inside_session"
    last = query.reference_offset
    arrived = {offset: market.bars[offset] for offset in range(last + 1, clock)
               if offset in market.bars and market.bars[offset].available_offset <= clock}
    if any(bar.close <= query.structure_stop for bar in arrived.values()):
        return None, None, "no_entry", "wait_structure_invalidated"
    if not set(range(last + 1, clock - 2)).issubset(arrived):
        return None, None, "unknown", "wait_required_close_unknown"
    offset = max(arrived, default=last)
    close = arrived[offset].close if offset in arrived else query.reference_close
    if query.structure_stop >= close + query.scale:
        return None, None, "no_entry", "wait_stop_not_below_guard_cap"
    frame = replace(candidate.frame, offset=offset, decision_offset=clock, interval_end=offset + 1)
    return EntryRequest(clock, close, query.scale, query.structure_stop), frame, None, None


def _forecasts(request, frame, market, episode, predict):
    records, forecasts = [], []
    if request is None or predict is None:
        return records, ()
    for age in (10, 20):
        checkpoint = held.held_checkpoint(request, frame, market.bars, market.session_length, age_minutes=age)
        if checkpoint.status != "free_alive":
            records.append(dict(age=age, status=checkpoint.status, query_key=None, value=None))
            continue
        frame_at_age = checkpoint.frame
        clock = checkpoint.checkpoint_offset
        chart = features.chart_features(frame_at_age, market.bars, session_length=market.session_length,
                                        volumes=market.volumes)
        if chart is None:
            raise ValueError("Comparison held reference chart is missing")
        peer = features.peer_features(episode.symbol, market.peers, decision_offset=clock)["values"]
        query = Query(key=f"{checkpoint.book_id}@{clock}", session=episode.session,
                      symbol=episode.symbol, episode_id=checkpoint.book_id, cluster_id=episode.cluster_id,
                      decision_offset=clock, family=episode.family, state=frame_at_age.state,
                      sequence=tuple(map(tuple, chart["sequence"])), static=tuple(chart["static"]),
                      peer=None if peer is None else tuple(peer[k] for k in ("median5", "median15", "positive15_fraction")),
                      position=checkpoint.position, original_episode_id=episode.event_id)
        prediction = predict(query)
        values = prediction["predictions"]
        if (prediction["key"], prediction["session"]) != (query.key, query.session) or set(values) != set(HEADS):
            raise ValueError("Comparison held prediction identity differs")
        raw = values["remaining_gross"]
        if (any(value is not None for head, value in values.items() if head != "remaining_gross")
                or (raw is not None and prediction["query_status"] != "supported_features")):
            raise ValueError("Comparison held prediction has an inadmissible head")
        value = None if raw is None else Fraction(str(raw))
        forecasts.append(Forecast(clock, clock, value))
        records.append(dict(age=age, status="known" if value is not None else "prediction_unknown",
                            query_key=query.key, value=None if value is None else str(value)))
    return records, tuple(forecasts)


class _ReadMinutes(Mapping):
    """Keep the receipts consumed by the independent reference evaluator."""

    def __init__(self, bars):
        self.bars = bars
        self.consumed = {}

    def __getitem__(self, offset):
        bar = self.bars[offset]
        self.consumed[offset] = bar
        return bar

    def __iter__(self):
        return iter(self.bars)

    def __len__(self):
        return len(self.bars)


def _book(request, market, entry, management, forecasts, status, reason):
    if request is None:
        unknown = status == "unknown"
        report = dict(status=status, reason=reason, net=None if unknown else "0", gross=None if unknown else "0",
                      filled_quantity=None if unknown else 0, requested_quantity=None, entry_price=None,
                      entry_notional=None if unknown else "0", remaining_quantity=None if unknown else 0, sales=[])
        maturity = fill_clock = None
        mature = fill_known = True
    else:
        minutes = _ReadMinutes(market.bars)
        report = execution.evaluate(request, minutes, market.session_length, entry_policy=entry,
                                    management=management, forecasts=forecasts)
        clock = request.decision_offset
        maturity = max((clock, *(bar.available_offset for bar in minutes.consumed.values())))
        gap = report["unknown_offset"]
        if gap is not None:
            deadline = gap + 3 if report["reason"] in ("entry_open_unknown", "exit_open_unknown") else gap
            maturity = max(maturity, deadline)
        opening = minutes.consumed.get(clock + 1)
        fill_clock = clock if report["status"] == "no_entry" else None if opening is None else opening.available_offset
        mature = maturity < market.session_length
        fill_known = fill_clock is not None and fill_clock < market.session_length
    outcome = dict(status=report["status"] if mature else "unknown",
                   reason=report["reason"] if mature else "outcome_not_mature_in_session",
                   net=report["net"] if mature else None, gross=report["gross"] if mature else None,
                   filled_quantity=report["filled_quantity"] if fill_known else None,
                   entry_notional=report["entry_notional"] if fill_known else None,
                   matured_offset=maturity if mature else None, fill_matured_offset=fill_clock if fill_known else None)
    return report, outcome


def _canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def verify_replay(policy, markets, ledgers, *, held_predict=None, prediction_binding=None):
    """Verify all six books per sealed episode and return replay coverage counts."""
    verify_policy(policy)
    return _verify(policy, policy.choices, markets, ledgers, held_predict, prediction_binding)


def verify_replay_day(day, markets, ledgers, *, held_predict=None, prediction_binding=None):
    """Reconstruct one verified day while its independent decoder is resident."""
    if type(day) is not ReplayDay:
        raise ValueError("A verified replay day is required")
    return _verify(day.policy, day.choices, markets, ledgers, held_predict, prediction_binding)


def _verify(policy, choices, markets, ledgers, held_predict, prediction_binding):
    if held_predict is not None and prediction_binding != policy.prediction_binding:
        raise ValueError("Comparison held artifact binding differs")
    indexed = {}
    for row in ledgers:
        identity = row["original_episode_id"], row["entry_policy"], row["management"]
        if identity in indexed:
            raise ValueError("Duplicate comparison ledger arm")
        indexed[identity] = row
    counts = Counter()
    for choice in choices:
        episode = choice.episode
        candidate = choice.candidate
        market = None if candidate is None else markets[episode.session, episode.symbol]
        if candidate is None:
            if choice.action != "no_commit":
                raise ValueError("Comparison unselected episode requires no_commit")
            request, frame, status, reason = None, None, "no_entry", choice.reason
        else:
            request, frame, status, reason = _entry(choice, market)
        records, forecasts = _forecasts(request, frame, market, episode, held_predict)
        base = dict(session=episode.session, symbol=episode.symbol, family=episode.family,
                    original_episode_id=episode.event_id, cluster_id=episode.cluster_id, model=policy.model,
                    scope=policy.scope, control=policy.control, policy_sha256=policy.sha256,
                    action=choice.action, selection_reason=choice.reason,
                    candidate_key=None if candidate is None else candidate.key,
                    state="NO_COMMIT" if candidate is None else candidate.frame.state,
                    initial_denominator=None if choice.initial_denominator is None else str(choice.initial_denominator),
                    held_predictions=records, observation_continuation_value=None, claim=CLAIM,
                    entry_decision_offset=None if request is None else request.decision_offset)
        for entry in ENTRIES:
            for management in MANAGEMENT:
                row = indexed.pop((episode.event_id, entry, management), None)
                if row is None:
                    raise ValueError("Missing comparison ledger arm")
                report, outcome = _book(request, market, entry, management, forecasts, status, reason)
                expected = dict(base, entry_policy=entry, management=management, execution=report, outcome=outcome)
                if _canonical(row) != _canonical(expected):
                    fields = sorted(key for key in expected if _canonical(row.get(key)) != _canonical(expected[key]))
                    raise ValueError(f"Comparison ledger differs for {entry}/{management} fields {fields}")
                counts[outcome["status"]] += 1
    if indexed:
        raise ValueError("Unexpected comparison ledger arm")
    return {"episodes": len(choices), "ledgers": counts.total(), "outcome_counts": dict(sorted(counts.items()))}
