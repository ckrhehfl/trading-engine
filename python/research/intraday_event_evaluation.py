"""Pure EL policy freezing, matched replay and separate descriptive evaluation.

Policy selection accepts causal candidates and predictions, never labels/bars.
Only a sealed policy admits execution replay; forecast labels join separately.
No IO, model fitting, tuning, order path or funded-account arithmetic is here.
Floating point is confined to model outputs and descriptive statistics. Event
cash, sizing and opportunity denominators remain exact fractions.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from fractions import Fraction
from hashlib import sha256
from itertools import groupby
import json
import math
import random

from research import intraday_event_execution as execution
from research import intraday_event_features as features
from research import intraday_event_held as held
from research.intraday_event_labels import LabelQuery
from research.intraday_event_models import FAMILIES, HEADS, HELD_STATES, Query, TrainingLabels, support_seal
from research.intraday_event_scenarios import EventFrame


CONTROLS = ("onset", "confirmation", "model")
BOOTSTRAPS = 1000
SEED = 20261111
CALIBRATION_BINS = 10
LOG_FLOOR = 1e-15
PROBABILITY_SUM_TOLERANCE = 1e-6
CLAIM = "Discovery nominal event cash and observed-label forecasts; not account NAV, executable fills or confirmation"
CONTRACT = {"entry_controls": list(CONTROLS), "q_admission": "both_known_first_strictly_positive_maximum_tie_immediate",
    "commitment": "one_per_original_episode_wait3_never_renewed", "held_ages": [10, 20],
    "bootstrap_replicates": BOOTSTRAPS, "seed": SEED, "calibration_bins": CALIBRATION_BINS,
    "log_probability_floor": LOG_FLOOR, "uncertainty": "date_block_percentile_95_descriptive",
    "unknown_cash": "never_zero_complete_date_subset_explicit", "case_selection": "not_used_by_policy"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _plain(value):
    if isinstance(value, Fraction):
        return str(value)
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def _json(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _number(value):
    _require(not isinstance(value, bool) and isinstance(value, (int, float, str, Fraction)), "Finite prediction number required")
    result = float(value) if not isinstance(value, str) else float(Fraction(value))
    _require(math.isfinite(result), "Nonfinite prediction")
    return result


def _fraction(value):
    _number(value)
    return Fraction(str(value)) if isinstance(value, float) else Fraction(value)


def _calendar(values):
    days = tuple(values)
    _require(days and days == tuple(sorted(set(days))) and all(date.fromisoformat(d).isoformat() == d for d in days),
             "Complete ordered unique original calendar required")
    return days


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True)
class Episode:
    session: str
    symbol: str
    event_id: str
    cluster_id: str
    family: str
    onset_decision_offset: int

    def __post_init__(self):
        _calendar((self.session,))
        _require(self.family in FAMILIES and all(isinstance(v, str) and 0 < len(v) <= 512
            for v in (self.symbol, self.event_id, self.cluster_id)), "Original episode identity required")
        _require(type(self.onset_decision_offset) is int and self.onset_decision_offset >= 0, "Original onset receipt required")


@dataclass(frozen=True)
class Candidate:
    """One already-coalesced receipt; absent chart is retained, not filtered."""

    episode: Episode
    frame: EventFrame
    label_query: LabelQuery
    query: Query | None

    def __post_init__(self):
        e, f, q = self.episode, self.frame, self.label_query
        _require(type(e) is Episode and type(f) is EventFrame and type(q) is LabelQuery, "Typed causal candidate required")
        _require((f.event_id, f.overlap_group_id, f.family) == (e.event_id, e.cluster_id, e.family)
            and f.decision_offset >= e.onset_decision_offset, "Frame and original episode differ")
        _require((q.event_id, q.family, q.state, q.terminal, q.onset_offset, q.event_offset, q.decision_offset,
                  q.scale, q.structure_stop) == (f.event_id, f.family, f.state, f.terminal, f.onset_offset,
                  f.offset, f.decision_offset, f.scale, f.structure), "Sealed reference/frame differ")
        if self.query is not None:
            r = self.query
            _require(type(r) is Query and (r.session, r.symbol, r.episode_id, r.cluster_id, r.family, r.state, r.decision_offset)
                == (e.session, e.symbol, e.event_id, e.cluster_id, e.family, f.state, f.decision_offset), "Query candidate identity differs")

    @property
    def key(self):
        return self.frame.event_id + "@" + str(self.frame.decision_offset) if self.query is None else self.query.key


@dataclass(frozen=True)
class Choice:
    episode: Episode
    candidate: Candidate | None
    action: str
    reason: str
    initial_denominator: Fraction | None


@dataclass(frozen=True)
class FrozenPolicy:
    control: str
    model: str
    scope: str
    prediction_binding: str
    roster_sha256: str
    choices: tuple[Choice, ...]
    trace: tuple[tuple, ...]
    sha256: str


def _policy_body(control, model, scope, binding, roster, choices, trace):
    return {"control": control, "model": model, "scope": scope, "prediction_binding": binding,
            "roster_sha256": roster, "trace": trace, "choices": [{"episode": c.episode,
            "candidate": None if c.candidate is None else {"frame": c.candidate.frame,
                "label_query": c.candidate.label_query, "key": c.candidate.key},
            "action": c.action, "reason": c.reason, "initial_denominator": c.initial_denominator} for c in choices]}


def verify_policy(policy):
    _require(type(policy) is FrozenPolicy and policy.control in CONTROLS and _sha(policy.prediction_binding)
        and _sha(policy.roster_sha256), "Frozen policy binding required")
    body = _policy_body(policy.control, policy.model, policy.scope, policy.prediction_binding,
                        policy.roster_sha256, policy.choices, policy.trace)
    _require(sha256(_json(body)).hexdigest() == policy.sha256, "Frozen policy seal differs")


@dataclass(frozen=True)
class VerifiedReplayPolicy:
    """Authenticate the full policy once before visiting any of its sessions."""

    policy: FrozenPolicy

    def __post_init__(self):
        verify_policy(self.policy)

    def days(self):
        start = 0
        previous = None
        for session, choices in groupby(self.policy.choices, key=lambda choice: choice.episode.session):
            _require(previous is None or previous < session, "Policy replay days must be chronological")
            stop = start + sum(1 for _ in choices)
            yield ReplayDay(self, session, start, stop)
            start, previous = stop, session


@dataclass(frozen=True)
class ReplayDay:
    """A complete day of choices bound to the original full-policy seal."""

    verified: VerifiedReplayPolicy
    session: str
    start: int
    stop: int

    def __post_init__(self):
        _require(type(self.verified) is VerifiedReplayPolicy, "Verified replay policy required")
        choices = self.policy.choices
        _require(type(self.start) is int and type(self.stop) is int and 0 <= self.start < self.stop <= len(choices)
                 and all(choice.episode.session == self.session for choice in self.choices)
                 and (self.start == 0 or choices[self.start - 1].episode.session != self.session)
                 and (self.stop == len(choices) or choices[self.stop].episode.session != self.session),
                 "Replay slice must contain all choices on one sealed session")

    @property
    def policy(self):
        return self.verified.policy

    @property
    def choices(self):
        return self.policy.choices[self.start:self.stop]


def _admission(candidate):
    q = candidate.label_query
    if q.terminal and q.state != "CONFIRMED":
        return "terminal_not_admissible"
    if q.decision_offset + 31 >= q.session_length:
        return "expiry_not_inside_session"
    if q.reference_close is None:
        return "reference_unknown"
    if q.structure_stop >= q.reference_close + q.scale:
        return "stop_not_below_cap"
    return None


def _denominator(candidate):
    q = candidate.label_query
    if q.reference_close is None or q.structure_stop >= q.reference_close + q.scale:
        return None
    cap = q.reference_close + q.scale
    quantity = int(min(execution.FIRST_RISK // (cap * (1 + execution.COST) - q.structure_stop),
        min(execution.CAPITAL / 50, execution.CAPITAL / (1 + execution.COST)) // cap))
    return quantity * cap


def _prediction_rows(predictions, queries):
    expected = {q.key: q for q in queries}
    _require(len(expected) == len(queries), "Duplicate prediction query key")
    result = {}
    for row in predictions:
        _require(isinstance(row, dict) and set(row) == {"key", "session", "query_status", "predictions", "head_status"},
                 "Native target-free prediction schema required")
        key = row["key"]
        _require(key in expected and key not in result and row["session"] == expected[key].session,
                 "Prediction population/identity differs")
        _require(set(row["predictions"]) == set(HEADS) and set(row["head_status"]) == set(HEADS), "Five prediction heads required")
        values = {}
        for head, value in row["predictions"].items():
            applicable = (expected[key].state in HELD_STATES) == (head == "remaining_gross")
            _require(applicable or value is None, "Prediction head is inapplicable to this query state")
            if value is None:
                values[head] = None
            elif head == "path_class":
                _require(isinstance(value, (tuple, list)) and len(value) == 3, "Three path probabilities required")
                values[head] = tuple(_number(v) for v in value)
                _require(all(0 <= v <= 1 for v in values[head])
                         and abs(sum(values[head]) - 1) <= PROBABILITY_SUM_TOLERANCE,
                         "Invalid path probability vector")
            else:
                values[head] = _fraction(value)
                _require(head != "fill" or 0 <= values[head] <= 1, "Invalid fill probability")
        if row["query_status"] != "supported_features":
            _require(all(v is None for v in values.values()), "Unsupported prediction cannot carry a value")
        result[key] = values
    # Omitted predictions are explicit abstentions. No outcome-supported subset
    # can silently remove the original candidate itself.
    return result


def freeze_policy(episodes, candidates, predictions=(), *, control, model, scope, prediction_binding):
    """One commitment per ORIGINAL episode. Both Q heads are required.

    Positive ties choose immediate; wait3 is a one-time commitment, not another
    opportunity to predict three minutes later. The trace's observation value
    remains unknown; choosing no entry is distinct from valuing observation at 0.
    """
    _require(control in CONTROLS and _sha(prediction_binding) and all(isinstance(v, str) and v for v in (model, scope)),
             "Fixed control/model/scope/artifact binding required")
    episodes, candidates = tuple(episodes), tuple(candidates)
    _require(len(episodes) <= 100_000 and len(candidates) <= 100_000, "Bounded policy roster required")
    roster = {e.event_id: e for e in episodes}
    _require(all(type(e) is Episode for e in episodes) and len(roster) == len(episodes), "Duplicate original episode")
    owners, clusters, grouped, seen, clocks = {}, {}, defaultdict(list), set(), set()
    for e in episodes:
        owner = e.session, e.symbol
        _require(owners.setdefault(e.cluster_id, owner) == owner and clusters.setdefault(owner, e.cluster_id) == e.cluster_id,
                 "Original symbol/day must have one cluster")
    for c in candidates:
        _require(type(c) is Candidate and roster.get(c.episode.event_id) == c.episode and c.key not in seen,
                 "Candidate original identity/duplicate differs")
        clock = c.episode.cluster_id, c.frame.family, c.frame.decision_offset
        _require(clock not in clocks, "Coalesce all family updates at a receipt before policy selection")
        clocks.add(clock)
        seen.add(c.key)
        grouped[c.episode.event_id].append(c)
    p = _prediction_rows(predictions, tuple(c.query for c in candidates if c.query is not None))
    digest = sha256()
    for e in sorted(episodes, key=lambda e: (e.session, e.symbol, e.family, e.event_id)):
        digest.update(_json(e) + b"\n")
    for c in sorted(candidates, key=lambda c: c.key):
        digest.update(_json(c) + b"\n")
    choices, trace = [], []
    for e in sorted(episodes, key=lambda e: (e.session, e.symbol, e.family, e.event_id)):
        selected, action, reason, denominator = None, "no_commit", "no_causal_frame", None
        for c in sorted(grouped[e.event_id], key=lambda c: c.frame.decision_offset):
            if denominator is None:
                denominator = _denominator(c)
            eligible = control == "model" or (control == "onset" and c.frame.decision_offset == e.onset_decision_offset) or (
                control == "confirmation" and c.frame.state == "CONFIRMED")
            if not eligible:
                continue
            unavailable = _admission(c)
            values = p.get(c.key, {})
            qi, qw = values.get("net_immediate"), values.get("net_wait3")
            if unavailable is not None:
                reason = unavailable
            elif control != "model":
                selected, action, reason = c, "immediate", "fixed_" + control
            elif qi is None or qw is None:
                reason = "missing_paired_value_heads"
            elif max(qi, qw) <= 0:
                reason = "observe_without_commitment"
            else:
                selected, action = c, "immediate" if qi >= qw else "wait3"
                reason = "first_positive_paired_value"
            trace.append((e.event_id, c.key, c.frame.decision_offset, c.frame.state,
                          action if selected else "abstain" if unavailable or qi is None or qw is None else "observe",
                          reason, qi, qw))
            if selected is not None or control != "model":
                break
        if selected is not None:
            denominator = _denominator(selected)
        choices.append(Choice(e, selected, action, reason, denominator))
    body = _policy_body(control, model, scope, prediction_binding, digest.hexdigest(), choices, trace)
    return FrozenPolicy(control, model, scope, prediction_binding, digest.hexdigest(), tuple(choices), tuple(trace), sha256(_json(body)).hexdigest())


@dataclass(frozen=True)
class SessionMarket:
    """Caller-authenticated single continuous session; no original loader here."""

    session_length: int
    bars: dict
    volumes: dict
    peers: dict


def _request(choice, market):
    """Resolve fixed wait only from information received at its fixed clock."""
    c, q = choice.candidate, choice.candidate.label_query
    _require(market.session_length == q.session_length, "Continuous calendar differs")
    known = {i: b for i, b in market.bars.items() if i < q.decision_offset and b.available_offset <= q.decision_offset}
    _require(known and max(known) == q.reference_offset and known[q.reference_offset].close == q.reference_close,
             "Frozen decision reference differs from causal prefix")
    decision, reference, offset = q.decision_offset, q.reference_close, q.reference_offset
    if choice.action == "wait3":
        decision += 3
        if decision + 31 >= market.session_length:
            return None, None, "no_entry", "delayed_expiry_not_inside_session"
        later = {i: b for i, b in market.bars.items() if q.reference_offset < i < decision and b.available_offset <= decision}
        if any(b.close <= q.structure_stop for b in later.values()):
            return None, None, "no_entry", "wait_structure_invalidated"
        if any(i not in later for i in range(q.reference_offset + 1, decision - 2)):
            return None, None, "unknown", "wait_required_close_unknown"
        if later:
            offset = max(later)
            reference = later[offset].close
        if q.structure_stop >= reference + q.scale:
            return None, None, "no_entry", "wait_stop_not_below_guard_cap"
    request = execution.EntryRequest(decision, reference, q.scale, q.structure_stop)
    frame = c.frame if choice.action == "immediate" else replace(c.frame, offset=offset, decision_offset=decision, interval_end=offset + 1)
    return request, frame, None, None


def _held_query(checkpoint, episode, market):
    frame = checkpoint.frame
    chart = features.chart_features(frame, market.bars, session_length=market.session_length, volumes=market.volumes)
    _require(chart is not None, "Observed held checkpoint requires causal chart reference")
    peer = features.peer_features(episode.symbol, market.peers, decision_offset=frame.decision_offset)
    values = None if peer["values"] is None else tuple(peer["values"][key] for key in ("median5", "median15", "positive15_fraction"))
    return Query(key=frame.event_id + "@" + str(frame.decision_offset), session=episode.session,
        symbol=episode.symbol, episode_id=frame.event_id, cluster_id=episode.cluster_id,
        decision_offset=frame.decision_offset, family=episode.family, state=frame.state,
        sequence=tuple(tuple(row) for row in chart["sequence"]), static=tuple(chart["static"]), peer=values,
        position=checkpoint.position, original_episode_id=episode.event_id)


def _checkpoints(request, frame, market):
    for age in (10, 20):
        yield age, held.held_checkpoint(request, frame, market.bars, market.session_length, age_minutes=age)


def held_queries(policy, markets):
    """Derive causal holding inputs in receipt order for batched inference."""
    verify_policy(policy)
    yield from _held_queries(policy.choices, markets)


def held_queries_day(day, markets):
    _require(type(day) is ReplayDay, "A verified replay day is required")
    yield from _held_queries(day.choices, markets)


def _held_queries(choices, markets):
    for choice in choices:
        if choice.candidate is None:
            continue
        episode = choice.episode
        market = markets[episode.session, episode.symbol]
        request, frame, _, _ = _request(choice, market)
        if request is not None:
            for _, checkpoint in _checkpoints(request, frame, market):
                if checkpoint.status == "free_alive":
                    yield _held_query(checkpoint, episode, market)


class _Consumed(Mapping):
    def __init__(self, bars):
        self.bars, self.read = bars, {}

    def __getitem__(self, offset):
        self.read[offset] = self.bars.get(offset)
        return self.bars[offset]

    def __iter__(self):
        return iter(self.bars)

    def __len__(self):
        return len(self.bars)


def _outcome(report, request, consumed, length, status, reason):
    if request is None:
        return {"status": status, "reason": reason, "net": report["net"], "gross": report["gross"],
                "filled_quantity": report["filled_quantity"], "entry_notional": report["entry_notional"],
                "matured_offset": None, "fill_matured_offset": None}
    maturity = max([request.decision_offset, *(b.available_offset for b in consumed.read.values() if b is not None)])
    if report["unknown_offset"] is not None:
        maturity = max(maturity, report["unknown_offset"] + (3 if report["reason"] in ("entry_open_unknown", "exit_open_unknown") else 0))
    entry = consumed.read.get(request.decision_offset + 1)
    fill_maturity = (request.decision_offset if report["status"] == "no_entry" else
                     None if entry is None else entry.available_offset)
    fill_known = fill_maturity is not None and fill_maturity < length
    mature = maturity < length
    return {"status": report["status"] if mature else "unknown", "reason": report["reason"] if mature else "outcome_not_mature_in_session",
        "net": report["net"] if mature else None, "gross": report["gross"] if mature else None,
        "filled_quantity": report["filled_quantity"] if fill_known else None,
        "entry_notional": report["entry_notional"] if fill_known else None,
        "matured_offset": maturity if mature else None, "fill_matured_offset": fill_maturity if fill_known else None}


def replay_policy(policy, markets, *, held_predict=None, prediction_binding=None):
    """Replay a SEALED policy; held_predict receives only a fresh target-free Query.

    Both entry arms use the same request. Capped position forecasts are shared
    only with its identical filled reference-limit subset. The caller binds the
    callback to the same already-fitted fold artifact; no fit/labels are accepted.
    """
    verify_policy(policy)
    return _replay(policy, policy.choices, markets, held_predict, prediction_binding)


def replay_policy_day(day, markets, *, held_predict=None, prediction_binding=None):
    """Replay one verified session with the full policy identity on each book."""
    _require(type(day) is ReplayDay, "A verified replay day is required")
    return _replay(day.policy, day.choices, markets, held_predict, prediction_binding)


def _replay(policy, choices, markets, held_predict, prediction_binding):
    _require(held_predict is None or prediction_binding == policy.prediction_binding, "Held callback artifact binding differs")
    output = []
    for choice in choices:
        e = choice.episode
        base = {"session": e.session, "symbol": e.symbol, "family": e.family, "original_episode_id": e.event_id,
            "cluster_id": e.cluster_id, "model": policy.model, "scope": policy.scope, "control": policy.control,
            "policy_sha256": policy.sha256, "action": choice.action, "selection_reason": choice.reason,
            "candidate_key": None if choice.candidate is None else choice.candidate.key,
            "state": "NO_COMMIT" if choice.candidate is None else choice.candidate.frame.state,
            "initial_denominator": None if choice.initial_denominator is None else str(choice.initial_denominator)}
        request, frame, status, reason, forecast_rows = None, None, "no_entry", choice.reason, []
        market = None
        forecasts = []
        if choice.candidate is not None:
            market = markets[e.session, e.symbol]
            _require(type(market) is SessionMarket, "Typed supplied session market required")
            request, frame, status, reason = _request(choice, market)
            if request is not None and held_predict is not None:
                for age, checkpoint in _checkpoints(request, frame, market):
                    if checkpoint.status != "free_alive":
                        forecast_rows.append({"age": age, "status": checkpoint.status, "query_key": None, "value": None})
                        continue
                    query = _held_query(checkpoint, e, market)
                    predicted = _prediction_rows((held_predict(query),), (query,))[query.key]["remaining_gross"]
                    forecasts.append(execution.Forecast(query.decision_offset, query.decision_offset, predicted))
                    forecast_rows.append({"age": age, "status": "known" if predicted is not None else "prediction_unknown",
                                          "query_key": query.key, "value": None if predicted is None else str(predicted)})
        arm_reports = {}
        for entry_policy in execution.ENTRY_POLICIES:
            for management in execution.MANAGEMENT_POLICIES:
                consumed = None if market is None else _Consumed(market.bars)
                report = (None if request is None else execution.evaluate(request, consumed, market.session_length,
                    entry_policy=entry_policy, management=management, forecasts=tuple(forecasts)))
                if report is None:
                    report = {"status": status, "reason": reason, "net": None if status == "unknown" else "0",
                        "gross": None if status == "unknown" else "0", "filled_quantity": None if status == "unknown" else 0,
                        "requested_quantity": None, "entry_price": None, "entry_notional": None if status == "unknown" else "0",
                        "remaining_quantity": None if status == "unknown" else 0, "sales": []}
                arm_reports[entry_policy, management] = report
                outcome = _outcome(report, request, consumed, None if market is None else market.session_length, status, reason)
                output.append({**base, "entry_policy": entry_policy, "management": management,
                    "entry_decision_offset": None if request is None else request.decision_offset,
                    "execution": report, "outcome": outcome, "held_predictions": forecast_rows, "observation_continuation_value": None,
                    "claim": CLAIM})
        if request is not None:
            cap = arm_reports["next_open_capped", "fixed_exit"]
            for (entry_policy, management), report in arm_reports.items():
                original = arm_reports[entry_policy, "fixed_exit"]
                _require((report["requested_quantity"], report["filled_quantity"], report["entry_price"])
                    == (original["requested_quantity"], original["filled_quantity"], original["entry_price"]),
                    "Management changed matched entry/quantity")
                _require(report["requested_quantity"] == cap["requested_quantity"], "Entry policies changed reserved quantity")
                if entry_policy != "next_open_capped" and report["entry_price"] is not None:
                    _require(report["entry_price"] == cap["entry_price"] and report["filled_quantity"] == cap["filled_quantity"],
                             "Reference-limit fill is not the identical capped subset")
    return tuple(output)


def _phase(row):
    if row.state in HELD_STATES:
        return "held"
    if row.state == "CONFIRMED":
        return "confirmation"
    if row.state in ("INVALIDATED", "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP", "SESSION_END"):
        return "invalid_or_terminal"
    return "active"  # Explicit onset keys are supplied to forecast_report.


def _blocks(values):
    """Fixed date-block resampling; no row-level independence assumption."""
    if len(values) < 2:
        return {"dates": len(values), "mean": None if not values else float(values[0]), "interval95": None,
                "method": "insufficient_date_blocks"}
    rng, means = random.Random(SEED), []
    for _ in range(BOOTSTRAPS):
        means.append(sum(values[rng.randrange(len(values))] for _ in values) / len(values))
    means.sort()
    return {"dates": len(values), "mean": sum(values) / len(values), "interval95": [means[24], means[974]],
            "replicates": BOOTSTRAPS, "seed": SEED,
            "method": "date_block_percentile_descriptive_discovery_not_independent_row_CI"}


def _original(row, identities):
    return identities.get(row.key, getattr(row, "original_episode_id", None) or row.episode_id)


def _weights(rows, identities):
    days, clusters, episodes, frames = set(), defaultdict(set), defaultdict(set), Counter()
    for row in rows:
        original = _original(row, identities)
        days.add(row.session)
        clusters[row.session].add(row.cluster_id)
        episodes[row.session, row.cluster_id].add(original)
        frames[row.session, row.cluster_id, original] += 1
    return {row.key: 1 / (len(days) * len(clusters[row.session]) * len(episodes[row.session, row.cluster_id])
        * frames[row.session, row.cluster_id, _original(row, identities)]) for row in rows}


def forecast_report(queries, predictions, labels, calendar, *, model, scope, as_of_session,
                    onset_keys=(), original_episodes=None):
    """Post-policy reporting only. Known cohorts differ by head, never inference.

    A caller supplies original identity for held books if its Query schema does
    not yet carry it. Dates, symbols, family, state and phase remain separate.
    """
    rows, labels, calendar = tuple(queries), tuple(labels), _calendar(calendar)
    _require(all(type(r) is Query and r.session in calendar for r in rows), "Evaluation queries outside original calendar")
    _calendar((as_of_session,))
    if rows:
        support_seal(rows)  # Shared backend-free original identity/cluster audit.
    p, by_key = _prediction_rows(predictions, rows), {}
    keys = {r.key for r in rows}
    identities, onset_keys = dict(original_episodes or {}), set(onset_keys)
    _require(set(identities) <= keys and onset_keys <= keys, "Original episode/onset identities outside query roster")
    for row in rows:
        recorded = getattr(row, "original_episode_id", None)
        _require(row.key not in identities or recorded is None or identities[row.key] == recorded,
                 "Reported original episode differs from causal Query")
        if row.state in HELD_STATES:
            _require(row.key in identities or getattr(row, "original_episode_id", None), "Held forecast metrics require original episode identity")
    for label in labels:
        _require(type(label) is TrainingLabels and label.key in keys and label.key not in by_key,
                 "Report labels duplicate or outside prediction roster")
        by_key[label.key] = label
    for r in rows:
        label = by_key.get(r.key)
        _require(label is None or label.matured_session is None or label.matured_session >= r.session,
                 "Label cannot mature before its original query session")
    groups = defaultdict(list)
    for r in rows:
        phase = "onset" if r.key in onset_keys and _phase(r) == "active" else _phase(r)
        for dimension, value in (("all", "all"), ("session", r.session), ("symbol", r.symbol),
            ("family", r.family), ("state", r.state), ("phase", phase), ("family_phase", r.family + ":" + phase)):
            groups[dimension, value].append(r)
    report = []
    for (dimension, value), selected in sorted(groups.items()):
        summary = {"model": model, "scope": scope, "dimension": dimension, "value": value,
            "rows": len(selected), "dates": len({r.session for r in selected}), "clusters": len({r.cluster_id for r in selected}),
            "original_episodes": len({_original(r, identities) for r in selected}),
            "counterfactual_books": len({r.episode_id for r in selected if r.state in HELD_STATES}), "heads": {}}
        for head in HEADS:
            applicable = [r for r in selected if (r.state in HELD_STATES) == (head == "remaining_gross")]
            known = [r for r in applicable if r.key in by_key and getattr(by_key[r.key], head) is not None
                and by_key[r.key].matured_session is not None and by_key[r.key].matured_session <= as_of_session]
            scored = [r for r in known if p.get(r.key, {}).get(head) is not None]
            stats = {"applicable_rows": len(applicable), "known_labels": len(known), "missing_or_immature_labels": len(applicable) - len(known),
                "scored_rows": len(scored), "missing_predictions": len(known) - len(scored), "score": None}
            if scored:
                weights, daily = _weights(scored, identities), defaultdict(list)
                losses, logs, calibration = [], [], defaultdict(list)
                for r in scored:
                    target, predicted = getattr(by_key[r.key], head), p[r.key][head]
                    if head == "path_class":
                        loss = sum((prob - int(i == target)) ** 2 for i, prob in enumerate(predicted))
                        log = -math.log(max(LOG_FLOOR, predicted[target]))
                        probabilities = enumerate(predicted)
                    elif head == "fill":
                        predicted = float(predicted)
                        loss = (predicted - target) ** 2
                        log = -math.log(max(LOG_FLOOR, predicted if target else 1 - predicted))
                        probabilities = ((1, predicted),)
                    else:
                        loss, log, probabilities = (float(predicted) - float(Fraction(target))) ** 2, None, ()
                    weight = weights[r.key]
                    losses.append(weight * loss)
                    if log is not None:
                        logs.append(weight * log)
                    daily[r.session].append((weight, loss))
                    for cls, prob in probabilities:
                        calibration[cls, min(CALIBRATION_BINS - 1, int(prob * CALIBRATION_BINS))].append((weight, prob, int(target == cls)))
                bins = []
                for (cls, bucket), samples in sorted(calibration.items()):
                    total = sum(w for w, _, _ in samples)
                    bins.append({"class": cls, "bin": bucket, "rows": len(samples), "weight": total,
                        "mean_probability": sum(w * prob for w, prob, _ in samples) / total,
                        "observed_fraction": sum(w * actual for w, _, actual in samples) / total})
                stats.update(score=sum(losses), score_name="brier" if head in ("path_class", "fill") else "mse",
                    log_score=sum(logs) if logs else None, calibration=bins,
                    date_uncertainty=_blocks([sum(w * loss for w, loss in items) / sum(w for w, _ in items)
                                             for _, items in sorted(daily.items())]),
                    weighting="date->symbol/session cluster->original episode->frame")
            summary["heads"][head] = stats
        report.append(summary)
    return {"schema": "EL-forecast-evaluation-v1", "calendar_sessions": len(calendar), "as_of_session": as_of_session,
            "query_roster_sha256": support_seal(rows) if rows else sha256(b"empty").hexdigest(),
            "groups": report, "claim": CLAIM}


def _drawdown(values):
    cash, peak, drawdown = Fraction(), Fraction(), Fraction()
    for value in values:
        cash += value
        peak = max(peak, cash)
        drawdown = max(drawdown, peak - cash)
    return str(drawdown)


def policy_support(head_statuses):
    available = {"fitted_empirical", "trained_lightgbm", "trained_tcn", "constant_single_class"}
    unavailable = {"unsupported", "failed", "unsupported_empty_training"}
    _require(set(head_statuses) == set(HEADS) and all(value in available | unavailable for value in head_statuses.values()),
             "EL model head availability differs")
    return {"entry": all(head_statuses[name] in available for name in ("net_immediate", "net_wait3")),
            "holding": head_statuses["remaining_gross"] in available}


def supported_cash_rows(ledgers, support):
    """Keep mechanical fallback receipts separate from learnable policy results."""
    for row in ledgers:
        if ((row["control"] == "model" and not support["entry"])
                or (row["management"] != "fixed_exit" and not support["holding"])):
            yield {**row, "outcome": {**row["outcome"], "status": "model_unavailable", "net": None, "gross": None}}
        else:
            yield row


def _paired_cash(groups, calendar):
    comparisons = []
    for (arm, dimension, value), baseline in sorted(groups.items()):
        if dimension not in ("all", "family"):
            continue
        targets = []
        if arm[4] == "fixed_exit":
            targets.extend(("management", (*arm[:4], management)) for management in execution.MANAGEMENT_POLICIES[1:])
            if arm[2] == "onset":
                targets.extend(("decision", (*arm[:2], control, *arm[3:])) for control in ("confirmation", "model"))
            elif arm[2] == "confirmation":
                targets.append(("decision", (*arm[:2], "model", *arm[3:])))
        if arm[3] == "next_open_capped":
            targets.append(("entry", (*arm[:3], "reference_limit_one_shot", arm[4])))
        for comparison, target in targets:
            counterpart = groups.get((target, dimension, value))
            if counterpart is None:
                continue
            left = {r["original_episode_id"]: r for r in baseline}
            right = {r["original_episode_id"]: r for r in counterpart}
            _require(left.keys() == right.keys(), "Matched action comparison changed original episode roster")
            differences, missing_dates = {}, set()
            daily = dict.fromkeys(calendar, Fraction())
            for identity in sorted(left):
                a, b = left[identity], right[identity]
                _require((a["session"], a["symbol"], a["cluster_id"]) == (b["session"], b["symbol"], b["cluster_id"]),
                         "Matched policies changed the original episode identity")
                if comparison != "decision":
                    _require((a["candidate_key"], a["initial_denominator"]) == (b["candidate_key"], b["initial_denominator"]),
                             "Matched policies changed selected entry request/denominator")
                if a["outcome"]["net"] is None or b["outcome"]["net"] is None:
                    missing_dates.add(a["session"])
                    continue
                difference = Fraction(b["outcome"]["net"]) - Fraction(a["outcome"]["net"])
                differences[identity] = difference
                daily[a["session"]] += difference
            comparisons.append({"comparison": comparison, "baseline": dict(zip(
                ("model", "scope", "control", "entry_policy", "management"), arm)),
                "alternative": dict(zip(("model", "scope", "control", "entry_policy", "management"), target)),
                "dimension": dimension, "value": value, "original_opportunities": len(left),
                "joint_known_opportunities": len(differences), "excluded_unpaired_unknown": len(left) - len(differences),
                "alternative_minus_baseline_known_cash": str(sum(differences.values(), Fraction())) if differences else None,
                "all_opportunity_cash_difference": str(sum(differences.values(), Fraction())) if len(differences) == len(left) else None,
                "paired_mean_cash_difference": None if not differences else str(sum(differences.values(), Fraction()) / len(differences)),
                "unknown_dates": sorted(missing_dates),
                "date_uncertainty": _blocks([float(amount) for day, amount in daily.items() if day not in missing_dates]),
                "cohort": ("identical original episodes; decision policies may choose different requests; joint-known outcomes only"
                           if comparison == "decision" else
                           "identical original episodes and selected requests; joint-known outcomes only, not missing-at-random")})
    return comparisons


def cash_report(ledgers, calendar):
    """Never aggregate comparison arms together or replace unknown cash by zero."""
    calendar, groups, seen = _calendar(calendar), defaultdict(list), set()
    for row in ledgers:
        _require(row["session"] in calendar, "Cash ledger outside original calendar")
        arm = tuple(row[key] for key in ("model", "scope", "control", "entry_policy", "management"))
        identity = arm, row["original_episode_id"]
        _require(identity not in seen, "More than one attempt per original episode/arm")
        seen.add(identity)
        for dimension, value in (("all", "all"), ("session", row["session"]), ("symbol", row["symbol"]),
                                  ("family", row["family"]), ("state", row["state"])):
            groups[arm, dimension, value].append(row)
    summaries = []
    for (arm, dimension, value), rows in sorted(groups.items()):
        dates = calendar if dimension != "session" else (value,)
        status, daily, weekly, names = Counter(), {d: [] for d in dates}, {}, defaultdict(Fraction)
        for d in dates:
            iso = date.fromisoformat(d).isocalendar()
            weekly.setdefault(f"{iso.year}-W{iso.week:02}", {"opportunities": 0, "commits": 0, "fills": 0, "unknown": 0})
        known, filled, denominator_rows = [], [], []
        commits = fills = 0
        for row in rows:
            book = row["outcome"]
            status[book["status"]] += 1
            daily[row["session"]].append(row)
            iso = date.fromisoformat(row["session"]).isocalendar()
            week = weekly[f"{iso.year}-W{iso.week:02}"]
            week["opportunities"] += 1
            if book["status"] != "model_unavailable":
                committed = int(row["action"] != "no_commit")
                executed = int(bool(book["filled_quantity"]))
                week["commits"] += committed
                week["fills"] += executed
                commits += committed
                fills += executed
            week["unknown"] += int(book["net"] is None)
            if book["net"] is not None:
                known.append(row)
                names[row["symbol"]] += abs(Fraction(book["net"]))
                if book["filled_quantity"]:
                    filled.append(row)
                if row["initial_denominator"] is not None and Fraction(row["initial_denominator"]) > 0:
                    denominator_rows.append(row)
        net = sum((Fraction(r["outcome"]["net"]) for r in known), Fraction())
        gross = sum((Fraction(r["outcome"]["gross"]) for r in known), Fraction())
        denom = sum((Fraction(r["initial_denominator"]) for r in denominator_rows), Fraction())
        numerator = sum((Fraction(r["outcome"]["net"]) for r in denominator_rows), Fraction())
        fill_basis = sum((Fraction(r["outcome"]["entry_notional"]) for r in filled), Fraction())
        fill_net = sum((Fraction(r["outcome"]["net"]) for r in filled), Fraction())
        daily_known, daily_unknown = [], []
        for d, entries in daily.items():
            if any(r["outcome"]["net"] is None for r in entries):
                daily_unknown.append(d)
            else:
                daily_known.append(sum((Fraction(r["outcome"]["net"]) for r in entries), Fraction()))
        total_absolute = sum(names.values(), Fraction())
        fractions = {name: float(amount / total_absolute) for name, amount in sorted(names.items())} if total_absolute else {}
        summaries.append({**dict(zip(("model", "scope", "control", "entry_policy", "management"), arm)),
            "dimension": dimension, "value": value, "rows": len(rows),
            "original_episodes": len({r["original_episode_id"] for r in rows}), "clusters": len({r["cluster_id"] for r in rows}),
            "dates_with_opportunity": len({r["session"] for r in rows}), "calendar_dates": len(dates),
            "commits": commits, "fills": fills,
            "status": dict(status), "known_cash_rows": len(known), "unknown_cash_rows": len(rows) - len(known),
            "total_net_cash": str(net) if len(known) == len(rows) else None,
            "known_subset_net_cash": str(net) if known else None, "known_subset_gross_cash": str(gross) if known else None,
            "known_denominator_rows": len(denominator_rows), "known_initial_requested_notional": str(denom),
            "known_opportunity_net_ratio": None if not denom else str(numerator / denom),
            "known_cash_per_original_opportunity": None if len(known) != len(rows) or not rows else str(net / len(rows)),
            "known_fill_notional": str(fill_basis), "known_fill_net_ratio": None if not fill_basis else str(fill_net / fill_basis),
            "weekly": weekly, "zero_opportunity_weeks": sum(w["opportunities"] == 0 for w in weekly.values()),
            "daily_cash_drawdown": None if daily_unknown else _drawdown(daily_known),
            "known_complete_dates_drawdown": _drawdown(daily_known) if daily_known else None, "unknown_dates": daily_unknown,
            "date_uncertainty": _blocks([float(v) for v in daily_known]),
            "uncertainty_cohort": "complete-cash dates including original zero-opportunity dates; unknown dates excluded explicitly",
            "absolute_net_concentration": {"shares": fractions, "top_share": max(fractions.values(), default=None),
                "hhi": sum(v * v for v in fractions.values()) if fractions else None}, "claim": CLAIM})
    weeks = sorted({f"{date.fromisoformat(d).isocalendar().year}-W{date.fromisoformat(d).isocalendar().week:02}" for d in calendar})
    return {"schema": "EL-event-cash-evaluation-v1", "calendar_sessions": len(calendar), "calendar_weeks": weeks,
            "groups": summaries, "paired_differences": _paired_cash(groups, calendar), "claim": CLAIM}
