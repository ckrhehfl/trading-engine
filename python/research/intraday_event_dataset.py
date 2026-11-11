"""Bounded EL dataset assembly after caller-owned STARTED/source authentication.

No original-input path, acquisition, fit or execution authority is accepted.
Only the supplied authenticated reader opens chunks. One day (at most 23 names)
is resident, plus nine small selected prefix cards. Scientific projections are
new EL code; existing raw framing/calendar/private-file primitives are IO only.
Audit reconstructs raw minutes and every scientific stream independently, checks
all records and EOF, and never invokes producer detectors/features/label oracles.
"""

from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import fields, is_dataclass
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as source
from research import intraday_event_features as features
from research import intraday_event_held as held
from research import intraday_event_inputs as inputs
from research import intraday_event_labels as labels
from research import intraday_event_scenarios as scenarios
from research.intraday_event_execution import ENTRY_POLICIES, EntryRequest
from research.intraday_event_models import Query, TrainingLabels


MAX_FILE = 2 * 1024 ** 3
MAX_ROW = 256 * 1024
MAX_NAMES = 23
MAX_MINUTES = 600
CAUSAL_FILES = ("coordinate-support.jsonl", "event-support.jsonl", "broad-observations.jsonl",
                "event-states.jsonl", "queries.jsonl", "held-checkpoints.jsonl")
FILES = (*CAUSAL_FILES, "case-roster.json", "before-cards.jsonl", "labels.jsonl", "after-cards.jsonl")
CONTRACT = {
    "event_schema": "el-price-scenes-v1", "families": list(scenarios.FAMILIES),
    "continuous_end": "original_declared_session_close_minus_10_minutes",
    "continuous_end_evidence": "inferred_from_official_regular_closing_call_schedule_not_historical_channel_certification",
    "availability_lag_minutes": 3, "scale_bars": 20, "minimum_past_move_bps": 66,
    "reset_minutes": 5, "frame_step_minutes": 3, "a_impulse_minutes": 3,
    "a_confirm_ages": [3, 12], "b_opening_minutes": 30, "b_maximum_reclaim_age": 20,
    "c_impulse_minutes": 5, "c_maximum_pullback_age": 10, "c_resume_ages": [2, 10],
    "sequence_steps": 64, "sequence_channels": 13, "static_channels": 16,
    "peer_roster": "same_session_original_S1_S2_intersection_excluding_self", "peer_quorum": 5,
    "coalesce": "latest_known_frame_per_series_family_receipt_time",
    "path_minutes": 30, "upper_path_bps": 66, "upper_path_scales": 3,
    "path_both_touches": "unknown_intrabar", "wait_minutes": 3,
    "entry_policy": "next_open_capped", "expiry_minutes_after_entry": 30,
    "q_denominator": "same_initial_requested_guard_cap_notional_for_immediate_and_wait",
    "known_no_fill_cash": "0", "unknown_outcome": None,
    "holding_entry_roster": "all_causally_entry_admissible_coalesced_frames_before_model_selection",
    "holding_entry_policies": list(ENTRY_POLICIES), "holding_checkpoint_minutes": [10, 20],
    "holding_target": "next_execution_open_to_original_expiry_or_later_causal_stop_gross_return_without_recharging_cost",
    "holding_cohort": "causally_free_alive_not_future_complete_positions",
    "holding_query_deduplication": "capped_book_queries_only_reference_filled_subset_links_to_identical_capped_price_quantity_query",
    "case_examples_per_family": 3,
    "case_selection": "smallest_SHA256_of_original_onset_identity_without_terminal_or_path",
    "stage_seals": "per_shard_causal_queries_then_case_roster_and_before_cards_then_outcomes",
    "global_read_gate": "all_nine_shards_independently_audited_same_scientific_source_before_model_fit_economic_summary_or_manual_outcome_selection",
    "inference_population": "all_original_coordinates_and_causal_events_not_selected_cases",
}


def _plain(value):
    if isinstance(value, (Fraction, Decimal)):
        return str(Fraction(value))
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        wire.require(all(type(key) is str for key in value), "EL nontext object key")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    wire.require(value is None or type(value) in (str, int, bool), "EL exact canonical value required")
    return value


class _Stream:
    def __init__(self, path, audit):
        wire.safe_path(path)
        self.path, self.audit = path, audit
        self.before = wire.file_identity(wire.regular(path)) if audit else None
        flags = os.O_RDONLY | os.O_NOFOLLOW if audit else os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        self.stream = os.fdopen(os.open(path, flags, 0o600), "rb" if audit else "wb")
        if audit:
            wire.require(wire.file_identity(os.fstat(self.stream.fileno())) == self.before,
                         "EL saved stream changed before audit")
        self.digest, self.size, self.rows = sha256(), 0, 0
        self.finished = False

    def append(self, value):
        raw = wire.encode(_plain(value))
        wire.require(not self.finished and len(raw) <= MAX_ROW and self.size + len(raw) <= MAX_FILE,
                     "EL dataset row/file bound exceeded")
        if self.audit:
            wire.require(self.stream.readline(MAX_ROW + 1) == raw,
                         "EL independent dataset row differs: " + self.path.name)
        else:
            wire.write_all(self.stream, raw)
        self.digest.update(raw)
        self.size += len(raw)
        self.rows += 1

    def finish(self):
        wire.require(not self.finished, "EL stream already sealed")
        if self.audit:
            wire.require(self.stream.read(1) == b"" and wire.file_identity(os.fstat(self.stream.fileno())) == self.before,
                         "EL dataset EOF/file identity differs")
            wire.require(wire.file_identity(wire.regular(self.path)) == self.before, "EL saved dataset replaced")
        else:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            wire.fsync_directory(self.path.parent)
        self.finished = True
        return {"filename": self.path.name, "sha256": self.digest.hexdigest(), "bytes": self.size, "rows": self.rows}

    def close(self):
        self.stream.close()


def _document(slot, name, value, audit):
    stream = _Stream(slot / name, audit)
    try:
        stream.append(value)
        return stream.finish()
    finally:
        stream.close()


def _read_stream(slot, pin):
    path = slot / pin["filename"]
    before = wire.file_identity(wire.regular(path))
    wire.require(before[4] == pin["bytes"] <= MAX_FILE, "EL sealed stream byte size differs")
    digest, total, rows = sha256(), 0, 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == before, "EL sealed stream changed")
        while raw := stream.readline(MAX_ROW + 1):
            wire.require(len(raw) <= MAX_ROW, "EL sealed row exceeds bound")
            value = json.loads(raw)
            wire.require(isinstance(value, dict) and wire.encode(value) == raw, "EL noncanonical sealed row")
            digest.update(raw)
            total += len(raw)
            rows += 1
            wire.require(total <= MAX_FILE, "EL sealed file exceeds bound")
            yield value
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == before, "EL sealed stream changed at EOF")
    wire.require(wire.file_identity(wire.regular(path)) == before
                 and (digest.hexdigest(), total, rows) == (pin["sha256"], pin["bytes"], pin["rows"]),
                 "EL sealed stream digest/EOF differs")


def _population(universe, session_indices):
    wire.require(type(universe) is source.UniverseCalendar and wire.valid_sha(universe.raw_sha256),
                 "EL authenticated original universe required")
    days = universe.sessions
    wire.require(days and len(days) <= 257 and tuple(sorted(set(days))) == days, "EL original calendar differs")
    bounds = (0, len(days)) if session_indices is None else session_indices
    wire.require(type(bounds) is tuple and len(bounds) == 2 and all(type(v) is int for v in bounds)
                 and 0 <= bounds[0] < bounds[1] <= len(days), "EL half-open session shard differs")
    groups, seen = defaultdict(list), set()
    for target in universe.targets:
        identity = (target.scenario, target.session, target.symbol)
        wire.require(target.scenario in ("S1", "S2") and target.session in days and identity not in seen,
                     "EL duplicate/nonoriginal population coordinate")
        seen.add(identity)
        groups[target.session, target.symbol].append(target)
    names_by_day = Counter(day for day, _ in groups)
    wire.require(all(0 < names_by_day[day] <= MAX_NAMES for day in days), "EL daily name bound differs")
    return groups, bounds, days[bounds[0]:bounds[1]]


def _reference_adapt(session):
    # Independent of the producer adapter: direct typed projection of the raw
    # reference decoder, including availability ceil and every absent minute.
    minute = timedelta(minutes=1)
    length = (session.closing - session.opening) // minute
    wire.require(10 < length <= MAX_MINUTES, "EL reference regular session bound")
    bars, volumes, states = {}, {}, []
    for offset in range(length):
        item = session.minutes.get(session.opening + offset * minute)
        if item is None:
            states.append({"offset": offset, "price": "missing", "volume": "missing", "available_offset": None})
            continue
        elapsed = item.available_at - session.opening
        available = -(-elapsed // minute)
        wire.require(item.start_at == session.opening + offset * minute and item.symbol == session.symbol
                     and item.available_at >= item.start_at + minute, "EL reference minute identity differs")
        if item.price_state == "observed":
            bars[offset] = scenarios.MinuteBar(offset, *item.ohlc, available)
        if item.volume_state == "observed":
            volumes[offset] = features.VolumeObservation(offset, Fraction(item.shares), available)
        states.append({"offset": offset, "price": item.price_state, "volume": item.volume_state,
                       "available_offset": available})
    return inputs.Observations(session.symbol, length, bars, volumes, tuple(states))


def _day(day, groups, reader, audit):
    if audit:
        from research.intraday_ed_snapshot_verify import reconstruct_reference_session
        from research.intraday_pattern_calibration import session_open, session_close
        from research.intraday_event_support_reference import source_metadata
    else:
        from research.intraday_event_support import source_metadata
    result = {}
    for _, symbol in sorted(key for key in groups if key[0] == day):
        price, volume = (reader.load_raw_chunk(kind, symbol, day) for kind in ("CC", "CX"))
        if audit:
            opening, closing = session_open(day), session_close(day)
            requested = tuple(opening + i * timedelta(minutes=1) for i in range((closing - opening) // timedelta(minutes=1)))
            original = reconstruct_reference_session(symbol, day, price, volume, requested_starts=requested)
            observations = _reference_adapt(original)
        else:
            original = source.session_inputs(symbol, day, price, volume).session
            observations = inputs.adapt_session(original)
        length = observations.session_length - 10
        wire.require(0 < length <= MAX_MINUTES - 10, "EL continuous interval differs")
        continuous = inputs.Observations(symbol, length,
            {i: b for i, b in observations.bars.items() if i < length},
            {i: v for i, v in observations.volumes.items() if i < length}, observations.states[:length])
        result[symbol] = observations, continuous, {"CC": source_metadata(price), "CX": source_metadata(volume)}
        reader.assert_unchanged()
    return result


def _rle(rows, family, length):
    selected = [row for row in rows if row.family == family]
    wire.require([row.offset for row in selected] == list(range(length)), "EL every scheduled support minute required")
    runs, counts = [], Counter()
    for row in selected:
        counts[row.status] += 1
        if runs and (runs[-1]["status"], runs[-1]["event_id"]) == (row.status, row.event_id):
            runs[-1]["end"] = row.offset + 1
        else:
            runs.append({"start": row.offset, "end": row.offset + 1, "status": row.status, "event_id": row.event_id})
    return {"family": family, "scheduled_minutes": length, "counts": dict(counts), "runs": runs,
            "boundaries": "half-open; all minutes retained; broad conditions are in broad-observations"}


def _reference_card(frame, observations):
    def row(offset):
        price, volume = observations.bars.get(offset), observations.volumes.get(offset)
        pknown = price is not None and price.available_offset <= frame.decision_offset
        vknown = volume is not None and volume.available_offset <= frame.decision_offset
        return {"offset": offset, "price_known": pknown,
                "ohlc": [str(price.open), str(price.high), str(price.low), str(price.close)] if pknown else None,
                "volume_known": vknown, "reported_volume": str(volume.value) if vknown else None}
    return {"schema": "EL-before-decision-card-v1", "frame": frame.as_dict(),
            "observations": [row(i) for i in range(max(0, frame.offset - 63), min(frame.decision_offset, observations.session_length))],
            "as_of_offset": frame.decision_offset,
            "claim": "available prefix only; proposed numerical scenario, not trader-authenticated rule"}


def _select_case(selected, rank, make):
    # Producer uses a sorted bounded list; the reference uses threshold replace.
    if len(selected) < 3 or rank < selected[-1][0]:
        selected.append((rank, make()))
        selected.sort(key=lambda pair: pair[0])
        del selected[3:]


def _reference_select_case(selected, rank, make):
    if len(selected) < 3:
        selected.append((rank, make()))
    else:
        index = max(range(3), key=lambda i: selected[i][0])
        if rank < selected[index][0]:
            selected[index] = (rank, make())


def _query_key(frame):
    return frame.event_id + "@" + str(frame.decision_offset)


def _query_record(frame, day, symbol, continuous, peers, feature_module, label_module,
                  *, query_kind="entry", position=None, origin=None):
    chart = (feature_module.chart_features(frame, continuous.bars, session_length=continuous.session_length,
                                          volumes=continuous.volumes)
             if frame.decision_offset < continuous.session_length else None)
    peer = (feature_module.peer_features(symbol, peers, decision_offset=frame.decision_offset, quorum=5)
            if chart is not None else None)
    peer_values = None if peer is None or peer["values"] is None else tuple(peer["values"][name]
        for name in ("median5", "median15", "positive15_fraction"))
    query = (None if chart is None else Query(_query_key(frame), day, symbol, frame.event_id,
        frame.overlap_group_id, frame.decision_offset, frame.family, frame.state,
        tuple(tuple(step) for step in chart["sequence"]), tuple(chart["static"]), peer_values, position,
        frame.event_id if origin is None else origin["original_event_id"]))
    label_query = label_module.prepare_query(frame, chart, session_length=continuous.session_length) if query_kind == "entry" else None
    return {"kind": "query", "query_kind": query_kind, "key": _query_key(frame), "session": day,
            "symbol": symbol, "continuous_length": continuous.session_length, "frame": frame.as_dict(),
            "query_status": "supported_chart" if query is not None else "outside_continuous_clock" if frame.decision_offset >= continuous.session_length else "unknown_chart",
            "query": _plain(query), "label_query": _plain(label_query), "peer_support": peer,
            "origin": _plain(origin), "reference_close": None if chart is None else chart["reference_close"]}


def _decode_frame(value):
    return scenarios.EventFrame(**{**value, "scale": Fraction(value["scale"]),
        "level": Fraction(value["level"]), "structure": Fraction(value["structure"]),
        "conditions": tuple(sorted(value["conditions"].items()))})


def _decode_label_query(value):
    return labels.LabelQuery(**{**value, **{name: None if value[name] is None else Fraction(value[name])
        for name in ("reference_close", "scale", "structure_stop")}})


def _decode_request(value):
    return EntryRequest(**{**value, **{name: Fraction(value[name]) for name in
        ("reference_close", "scale", "structure_stop", "capital")}})


def _entry_request(record):
    query, frame = record["label_query"], record["frame"]
    if (query is None or record["query"] is None or frame["terminal"] and frame["state"] != "CONFIRMED"
            or frame["decision_offset"] + 31 >= record["continuous_length"]):
        return None
    close, scale, stop = (Fraction(query[name]) for name in ("reference_close", "scale", "structure_stop"))
    return None if stop >= close + scale else EntryRequest(frame["decision_offset"], close, scale, stop)


def _reference_entry_request(record):
    # Re-derive the causal admission rather than trust the producer selector.
    frame = record["frame"]
    if record["query"] is None:
        return None
    allowed = {"WAITING", "WAITING_PULLBACK", "PULLBACK", "CONFIRMED"}
    if frame["state"] not in allowed:
        return None
    end_open = frame["decision_offset"] + CONTRACT["expiry_minutes_after_entry"] + 1
    if end_open not in range(record["continuous_length"]):
        return None
    causal = record["label_query"]
    reference = Fraction(causal["reference_close"])
    scale, stop = Fraction(causal["scale"]), Fraction(causal["structure_stop"])
    if reference + scale <= stop:
        return None
    return EntryRequest(frame["decision_offset"], reference, scale, stop)


def _stats():
    return {"coordinates": 0, "regular_minutes": 0, "continuous_minutes": 0,
            "original_episodes": 0, "series_clusters": 0,
            "scenario_coordinates": Counter({"S1": 0, "S2": 0}),
            "events_by_family": Counter(dict.fromkeys(scenarios.FAMILIES, 0)),
            "broad_minutes_by_family": Counter(dict.fromkeys(scenarios.FAMILIES, 0)),
            "frames_by_state": Counter(), "queries_supported": 0, "queries_unsupported": 0,
            "entry_queries": 0, "held_queries": 0, "peer_supported": 0, "peer_unknown": 0,
            "counterfactual_books": 0, "held_checkpoint_status": Counter(),
            "entry_admissibility": Counter({"admissible": 0, "not_admissible": 0, "unknown": 0}),
            "path_status": Counter({"known": 0, "unknown": 0, "unknown_intrabar": 0}),
            "immediate_status": Counter({"known": 0, "unknown": 0, "no_fill": 0, "no_entry": 0}),
            "wait3_status": Counter({"known": 0, "unknown": 0, "no_fill": 0, "no_entry": 0}),
            "held_target_status": Counter({"known": 0, "unknown": 0, "not_applicable": 0}), "cases": 0}


def _add_query(stream, record, counts):
    stream.append(record)
    counts["entry_queries" if record["query_kind"] == "entry" else "held_queries"] += 1
    counts["queries_supported" if record["query"] is not None else "queries_unsupported"] += 1
    counts["peer_supported" if record["query"] is not None and record["query"]["peer"] is not None else "peer_unknown"] += 1


def build_dataset(slot, universe, reader, *, audit=False, session_indices=None):
    """Assemble/check one shard; only the all-nine audited bundle admits models.

    The fixed hash minima are composable across shards. No stage chooses rows by
    later fills, complete labels or realized profitability. Held facts belong to
    their own receipt-time checkpoints, never to an earlier entry's Query.
    """
    wire.require(type(audit) is bool, "EL explicit audit mode required")
    slot = Path(slot)
    wire.safe_path(slot)
    wire.require(slot.is_dir(), "EL caller-owned exclusive output directory required")
    groups, bounds, days = _population(universe, session_indices)
    if audit:
        from research import intraday_event_scenarios_reference as scene_module
        from research import intraday_event_features_reference as feature_module
        from research import intraday_event_labels_reference as label_module
        from research import intraday_event_held_reference as held_module
        scan, select, card = scene_module.reference_scan, _reference_select_case, _reference_card
        entry_request = _reference_entry_request
    else:
        feature_module, label_module, held_module = features, labels, held
        scan, select, card = scenarios.scan_scenarios, _select_case, inputs.prefix_card
        entry_request = _entry_request
    base = {"schema": "EL-event-dataset-v1", "universe_sha256": universe.raw_sha256,
            "calendar_sha256": sha256(wire.encode(universe.sessions)).hexdigest(),
            "session_indices": list(bounds), "scientific_contract": CONTRACT}
    counts, pins = _stats(), {}
    observed_maximum_names_on_day = 0
    selected = {family: [] for family in scenarios.FAMILIES}
    with ExitStack() as stack:
        streams = {}
        for name in CAUSAL_FILES:
            stream = streams[name] = _Stream(slot / name, audit)
            stack.callback(stream.close)
            stream.append({"kind": "header", "filename": name, **base})
        for day in days:
            current = _day(day, groups, reader, audit)
            observed_maximum_names_on_day = max(observed_maximum_names_on_day, len(current))
            intersection = sorted(symbol for symbol in current
                if {t.scenario for t in groups[day, symbol]} == {"S1", "S2"})
            peers = {symbol: current[symbol][1].bars for symbol in intersection}
            for symbol, (regular, continuous, metadata) in current.items():
                series = "EL:" + universe.raw_sha256 + ":" + day + ":" + symbol
                targets = [{"scenario": t.scenario, "rank": t.rank, "state": t.state,
                            "stored_row_count": t.stored_row_count, "metadata_hex": t.metadata.hex()}
                           for t in sorted(groups[day, symbol], key=lambda t: t.scenario)]
                counts["coordinates"] += 1
                counts["series_clusters"] += 1
                counts["regular_minutes"] += regular.session_length
                counts["continuous_minutes"] += continuous.session_length
                counts["scenario_coordinates"].update(t["scenario"] for t in targets)
                streams["coordinate-support.jsonl"].append({"kind": "coordinate", "session": day,
                    "symbol": symbol, "series_id": series, "targets": targets, "sources": metadata,
                    "regular_length": regular.session_length, "continuous_length": continuous.session_length,
                    "minute_states": regular.states, "continuous_price_observed": len(continuous.bars),
                    "auction_interval": [continuous.session_length, regular.session_length],
                    "auction_absence_is_continuous_gap": False,
                    "day_peer_population": intersection,
                    "peer_roster": [name for name in intersection if name != symbol]})
                projection = scan(tuple(continuous.bars.values()), end_offset=continuous.session_length,
                                  session_length=continuous.session_length, series_id=series)
                wire.require(len(projection.frames) <= 6 * MAX_MINUTES
                             and len(projection.broad_observations) <= 2 * MAX_MINUTES, "EL per-coordinate event bound")
                for family in scenarios.FAMILIES:
                    streams["event-support.jsonl"].append({"kind": "support_runs", "session": day,
                        "symbol": symbol, **_rle(projection.support, family, continuous.session_length)})
                for broad in projection.broad_observations:
                    counts["broad_minutes_by_family"][broad.family] += 1
                    streams["broad-observations.jsonl"].append({"kind": "broad_observation", "session": day, "symbol": symbol, **_plain(broad)})
                coalesced = feature_module.coalesce_frames(projection.frames)
                display = {(frame.family, frame.decision_offset): frame for frame in coalesced}
                for frame in projection.frames:
                    counts["frames_by_state"][frame.state] += 1
                    streams["event-states.jsonl"].append({"kind": "event_state", "session": day,
                                                         "symbol": symbol, "frame": frame.as_dict()})
                    if frame.kind != "at_onset":
                        continue
                    counts["events_by_family"][frame.family] += 1
                    counts["original_episodes"] += 1
                    identity = (frame.family, frame.event_id, frame.overlap_group_id, frame.onset_offset, frame.decision_offset)
                    rank = sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(), identity
                    shown = display[frame.family, frame.decision_offset]
                    def example(frame=frame, shown=shown, rank=rank):
                        roster = {"family": frame.family, "event_id": frame.event_id, "series_id": series,
                            "session": day, "symbol": symbol, "onset_offset": frame.onset_offset,
                            "initial_available_offset": frame.decision_offset, "selection_hash": rank[0]}
                        return {"roster": roster, "query_key": _query_key(shown),
                            "before": {"kind": "before_card", "selected_onset": roster,
                                "decision_query_key": _query_key(shown),
                                "coalesced_onset_warning": shown != frame,
                                "warning": "one latest state per receipt; an onset is not a separate entry when a later update is already known",
                                "card": card(shown, continuous)}}
                    select(selected[frame.family], rank, example)
                coordinate_queries = 0
                for frame in coalesced:
                    record = _query_record(frame, day, symbol, continuous, peers, feature_module, label_module)
                    _add_query(streams["queries.jsonl"], record, counts)
                    coordinate_queries += 1
                    request = entry_request(record)
                    if request is None:
                        continue
                    counts["counterfactual_books"] += len(ENTRY_POLICIES)
                    for age in (10, 20):
                        checkpoints = {policy: held_module.held_checkpoint(request, frame, continuous.bars,
                            continuous.session_length, age_minutes=age, entry_policy=policy) for policy in ENTRY_POLICIES}
                        cap = checkpoints["next_open_capped"]
                        held_key = _query_key(cap.frame) if cap.status == "free_alive" else None
                        for policy, checkpoint in checkpoints.items():
                            counts["held_checkpoint_status"][checkpoint.status] += 1
                            if policy != "next_open_capped" and checkpoint.entry_price is not None:
                                wire.require(checkpoint.entry_price == cap.entry_price
                                    and checkpoint.requested_quantity == cap.requested_quantity,
                                    "EL reference-limit fill is not the identical capped position subset")
                            if policy != "next_open_capped" and checkpoint.status == "free_alive":
                                wire.require(cap.status == "free_alive" and checkpoint.entry_price == cap.entry_price
                                    and checkpoint.requested_quantity == cap.requested_quantity
                                    and checkpoint.position == cap.position and checkpoint.reference_offset == cap.reference_offset,
                                    "EL reference-limit duplicate differs from capped position")
                            streams["held-checkpoints.jsonl"].append({"kind": "held_checkpoint", "session": day,
                                "symbol": symbol, "original_query_key": record["key"], "checkpoint": checkpoint,
                                "learning_query_key": held_key if checkpoint.status == "free_alive" else None,
                                "learning_row_duplicated": False})
                        if cap.status == "free_alive":
                            origin = {"frame": frame.as_dict(), "request": _plain(request),
                                      "age_minutes": age, "entry_policy": "next_open_capped",
                                      "original_event_id": frame.event_id, "original_query_key": record["key"]}
                            held_record = _query_record(cap.frame, day, symbol, continuous, peers, feature_module,
                                label_module, query_kind="held", position=cap.position, origin=origin)
                            _add_query(streams["queries.jsonl"], held_record, counts)
                            coordinate_queries += 1
                wire.require(coordinate_queries <= 9 * MAX_MINUTES, "EL per-coordinate query bound")
            del current, peers
            regular = continuous = metadata = projection = None
        reader.assert_unchanged()
        for name, stream in streams.items():
            stream.append({"kind": "footer", "data_rows": stream.rows - 1})
            pins[name] = stream.finish()
    causal_seal = sha256(wire.encode(pins)).hexdigest()
    examples = [value for family in sorted(selected) for _, value in sorted(selected[family], key=lambda item: item[0])]
    counts["cases"] = len(examples)
    roster = {"schema": "EL-onset-case-roster-v1", "scope": "shard minima; global minima are selected from their union",
              "per_family": 3, "candidate_episodes": sum(counts["events_by_family"].values()),
              "examples": [item["roster"] for item in examples], "causal_sha256": causal_seal,
              "selection": CONTRACT["case_selection"], "inference_sample": False}
    pins["case-roster.json"] = _document(slot, "case-roster.json", roster, audit)
    before = _Stream(slot / "before-cards.jsonl", audit)
    try:
        before.append({"kind": "header", "causal_sha256": causal_seal, "case_roster": pins["case-roster.json"]})
        for item in examples:
            before.append(item["before"])
        before.append({"kind": "footer", "cases": len(examples)})
        pins["before-cards.jsonl"] = before.finish()
    finally:
        before.close()
    before_seal = sha256(wire.encode({name: pins[name] for name in (*CAUSAL_FILES, "case-roster.json", "before-cards.jsonl")})).hexdigest()
    query_rows = (row for row in _read_stream(slot, pins["queries.jsonl"]) if row["kind"] == "query")
    pending = next(query_rows, None)
    after_cards, label_stream = [], _Stream(slot / "labels.jsonl", audit)
    try:
        label_stream.append({"kind": "header", "before_sha256": before_seal, **base})
        for day in days:
            current = _day(day, groups, reader, audit)
            case_targets = {}
            while pending is not None and pending["session"] == day:
                continuous = current[pending["symbol"]][1]
                if pending["query_kind"] == "entry":
                    result = label_module.build_targets(_decode_label_query(pending["label_query"]), continuous.bars)
                    counts["entry_admissibility"][result.entry_admissibility] += 1
                    counts["path_status"][result.path.status] += 1
                    counts["immediate_status"][result.immediate.status] += 1
                    counts["wait3_status"][result.wait3.status] += 1
                    admitted = result.entry_admissibility == "admissible"
                    known = {"path_class": result.path.value if result.path.matured_offset is not None else None,
                             "fill": result.immediate.fill if admitted and result.immediate.fill_matured_offset is not None else None,
                             "net_immediate": result.immediate.value if admitted and result.immediate.matured_offset is not None else None,
                             "net_wait3": result.wait3.value if admitted and result.wait3.matured_offset is not None else None,
                             "remaining_gross": None}
                else:
                    origin = pending["origin"]
                    result = held_module.remaining_gross_target(_decode_request(origin["request"]),
                        _decode_frame(origin["frame"]), continuous.bars, continuous.session_length,
                        age_minutes=origin["age_minutes"], entry_policy=origin["entry_policy"])
                    counts["held_target_status"][result.status] += 1
                    known = dict.fromkeys(("path_class", "fill", "net_immediate", "net_wait3"))
                    known["remaining_gross"] = result.value if result.matured_offset is not None else None
                training = (None if pending["query"] is None else TrainingLabels(pending["key"],
                    matured_session=day if any(value is not None for value in known.values()) else None, **known))
                row = {"kind": "labels", "query_kind": pending["query_kind"], "key": pending["key"],
                       "session": day, "symbol": pending["symbol"], "targets": result, "training_labels": training}
                label_stream.append(row)
                if any(item["query_key"] == pending["key"] for item in examples):
                    case_targets[pending["key"]] = _plain(row)
                pending = next(query_rows, None)
            for item in examples:
                chosen = item["roster"]
                if chosen["session"] != day:
                    continue
                continuous = current[chosen["symbol"]][1]
                start = chosen["initial_available_offset"] + 1
                future = []
                for offset in range(start, min(start + 31, continuous.session_length)):
                    bar, volume = continuous.bars.get(offset), continuous.volumes.get(offset)
                    future.append({"offset": offset, "bar": _plain(bar), "reported_volume": _plain(volume)})
                wire.require(item["query_key"] in case_targets, "EL selected case decision target absent")
                after_cards.append({"kind": "after_card", "selected_onset": chosen,
                    "decision_query_key": item["query_key"], "targets": case_targets[item["query_key"]],
                    "future_observations": future, "selection_changed_after_outcome": False})
            del current
            continuous = None
        wire.require(pending is None, "EL query stream outside selected calendar")
        reader.assert_unchanged()
        label_stream.append({"kind": "footer", "data_rows": label_stream.rows - 1})
        pins["labels.jsonl"] = label_stream.finish()
    finally:
        label_stream.close()
    after = _Stream(slot / "after-cards.jsonl", audit)
    try:
        after.append({"kind": "header", "case_roster": pins["case-roster.json"], "labels": pins["labels.jsonl"]})
        for row in sorted(after_cards, key=lambda item: (item["selected_onset"]["family"], item["selected_onset"]["selection_hash"])):
            after.append(row)
        after.append({"kind": "footer", "cases": len(after_cards)})
        pins["after-cards.jsonl"] = after.finish()
    finally:
        after.close()
    manifest = {**base, "counts": _plain(counts), "files": pins,
                "causal_sha256": causal_seal, "before_sha256": before_seal,
                "stage_order": ["causal", "case_roster", "before_cards", "outcomes", "after_cards"],
                "held_learning_arm": "capped only; reference-limit duplicates linked, not learned twice",
                "maximum_resident_names": MAX_NAMES, "row_bytes_bound": MAX_ROW, "file_bytes_bound": MAX_FILE,
                "observed_maximum_names_on_day": observed_maximum_names_on_day,
                "global_read_gate": CONTRACT["global_read_gate"],
                "claim": "private discovery dataset; cases are illustrative; event cash is not account NAV or an edge"}
    manifest_pin = _document(slot, "dataset-manifest.json", manifest, audit)
    return {**manifest, "manifest_file": manifest_pin}
