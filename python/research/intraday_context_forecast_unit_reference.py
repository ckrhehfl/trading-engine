"""Streaming saved EI forecast audit, without fitting or model inference.

The independently authenticated predecessor already certifies the original
snapshot's feature/label science. This audit checks its exact bytes and roster,
saved forecast/artifact bindings, support counts and score arithmetic. It does
not repeat numeric feature projection, training, tree/sequence inference, or
the worker's execution order. No numerical library or raw market reader is used.
"""

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from math import fsum, isclose, isfinite
from pathlib import Path
import json
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_context_model_inputs import encode, validate_training_contract
from research.intraday_context_snapshot import (
    FEATURE_SCHEMA, LABEL_SCHEMA, MAX_COORDINATES, _label_header, _roster, _target,
)
from research.intraday_context_snapshot_saved_header import stream_roster_header
from research.intraday_context_snapshot_unit import open_bundle
from research.intraday_ed_sources import UniverseCalendar
from research.intraday_scenario_snapshot import MAX_BYTES, SnapshotSeal, _read
from research.intraday_scenario_snapshot_unit import _sealed_input, _sealed_output, _stream_member


SCHEMA = "ei-context-forecast-unit-draft-v1"
FORECAST_SCHEMA = "ei-context-forecast-seal-draft-v1"
MAX_OUTPUT, MAX_HEADER = 128 * wire.MIB, 4 * wire.MIB
ARMS = ("pooled_empirical", "context_empirical", "chart_lightgbm", "context_lightgbm",
        "chart_sequence", "context_sequence")
CONTRASTS = (("context_empirical_increment", 0, 1), ("chart_tree_vs_pooled", 0, 2),
             ("context_tree_increment", 2, 3), ("chart_sequence_vs_tree", 2, 4),
             ("context_sequence_increment", 4, 5), ("context_sequence_vs_tree", 3, 5))
GROUPS = tuple((stage, context, name, first, second) for stage in ("approach", "breakout")
               for context in ("all", "A", "B", "other") for name, first, second in CONTRASTS)
HORIZONS = (5, 15, 30)
COUNTS = ("candidates", "first_supported", "second_supported", "paired_forecasts", "paired_outcomes",
          "scored_events", "scored_dates")
HCOUNTS = ("horizon_pairs", "horizon_outcomes", "horizon_events", "horizon_dates")


def _need(condition, reason):
    wire.require(condition, "EI saved forecast audit " + reason)


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names.split()), "schema fields differ")


def _same(actual, expected):
    if type(expected) is float:
        _need(type(actual) is float and isfinite(actual) and isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12),
              "score value differs")
        return abs(actual - expected)
    if type(expected) in (tuple, list):
        _need(type(actual) in (tuple, list) and len(actual) == len(expected), "sequence differs")
        return max((_same(left, right) for left, right in zip(actual, expected, strict=True)), default=0.0)
    if type(expected) is dict:
        _need(type(actual) is dict and set(actual) == set(expected), "mapping fields differ")
        return max((_same(actual[key], value) for key, value in expected.items()), default=0.0)
    _need(type(actual) is type(expected) and actual == expected, "identity or count differs")
    return 0.0


def _seal(raw, schema, maximum):
    _keys(raw, "schema sha256 size records")
    _need(raw["schema"] == schema and wire.valid_sha(raw["sha256"])
          and type(raw["size"]) is int and 0 < raw["size"] <= MAX_BYTES
          and type(raw["records"]) is int and 0 <= raw["records"] <= maximum, "member seal differs")
    return SnapshotSeal(**raw)


def _forecast_header(members):
    return {"schema": FORECAST_SCHEMA, "features": members["features"], "labels": members["labels"],
            "model_keys": list(ARMS)}


def _unpack(path, directory, digest, size, members, checkpoint):
    _need(wire.valid_sha(digest) and type(size) is int and 0 < size <= MAX_OUTPUT, "external output pin differs")
    with _sealed_input(path) as (source, actual_size):
        _need(actual_size == size, "output byte count differs")
        raw = source.readline(MAX_HEADER + 1)
        _need(raw.endswith(b"\n") and len(raw) <= MAX_HEADER, "header bound exceeded")
        header = json.loads(raw)
        _need(wire.encode(header) == raw, "noncanonical header")
        _keys(header, "schema scenario adapter_mode model_keys contrasts snapshot original_universe_sha256 "
              "original_calendar_sha256 audited_snapshot_summary_sha256 folds date_weighted_comparisons "
              "learned_fit_count empirical_fit_count timing_scenarios_are_independent contains_economic_returns input_support io")
        _need(header["schema"] == SCHEMA and type(header["folds"]) is list and len(header["folds"]) == 5,
              "five fold bundle required")
        _same(header["snapshot"], members)
        seals = tuple(_seal(row["seal"], FORECAST_SCHEMA, MAX_COORDINATES + 5) for row in header["folds"])
        _need(len(raw) + sum(item.size for item in seals) == size, "member sizes differ")
        total, paths = sha256(raw), []
        for index, seal in enumerate(seals, 1):
            target = directory / f"fold-{index}.jsonl"
            with _sealed_output(target) as destination:
                _stream_member(source, seal, wire.encode(_forecast_header(members)), checkpoint,
                               target=destination, bundle_digest=total)
            paths.append(target)
        _need(not source.read(1) and total.hexdigest() == digest, "output hash or EOF differs")
    return header, tuple(zip(paths, seals, strict=True))


class _Census:
    def __init__(self):
        self.coordinates = self.frames = self.questions = 0
        self.coordinate_states, self.frame_states, self.contexts = Counter(), Counter(), Counter()
        self.features = {stage: Counter() for stage in ("approach", "breakout")}
        self.states = {stage: {str(h): Counter() for h in HORIZONS} for stage in self.features}
        self.paths = {stage: {str(h): Counter() for h in HORIZONS} for stage in self.features}

    def add(self, coordinate, labels):
        self.coordinates += 1
        self.frames += len(coordinate["frames"])
        self.questions += len(coordinate["questions"])
        self.coordinate_states[coordinate["status"]] += 1
        for frame in coordinate["frames"]:
            self.frame_states[frame[3]] += 1
            self.contexts[frame[1]] += 1
        for feature, label in zip(coordinate["questions"], labels["labels"], strict=True):
            stage = feature["question"]["identity"][3]
            self.features[stage][feature["status"]] += 1
            for result in label["results"]:
                self.states[stage][str(result[0])][result[1]] += 1
                if result[3] is not None:
                    self.paths[stage][str(result[0])][result[3]] += 1

    def payload(self):
        return {"coordinates": self.coordinates, "coordinate_states": dict(self.coordinate_states),
                "frames": self.frames, "frame_states": dict(self.frame_states), "contexts": dict(self.contexts),
                "questions": self.questions, "feature_states": {k: dict(v) for k, v in self.features.items()},
                "horizon_states": {k: {h: dict(v) for h, v in hs.items()} for k, hs in self.states.items()},
                "horizon_paths": {k: {h: dict(v) for h, v in hs.items()} for k, hs in self.paths.items()}}


def _eligible(feature):
    if feature["status"] != "ready":
        return "feature_unsupported:" + (feature["reasons"][0] if feature["reasons"] else "unspecified")
    question = feature["question"]
    _, _, level, band, target, stop = question["identity"][4]
    level, band, target, stop, close = map(Fraction, (level, band, target, stop, question["start_close"]))
    if "unsupported_price_geometry" in question["unsupported"] or not (
            0 < stop <= level - band < level + band < target and stop < close < target):
        return "unsupported_price_geometry"
    return None


class _Training:
    def __init__(self):
        self.coordinates = self.questions = self.eligible = self.supported = 0
        self.events, self.dates = set(), set()
        self.contexts, self.reasons, self.horizons = Counter(), Counter(), Counter()
        self.requests = self.decodes = self.max_cached = 0

    def add(self, coordinate, labels, cutoff):
        self.coordinates += 1
        requested = False
        for feature, label in zip(coordinate["questions"], labels["labels"], strict=True):
            self.questions += 1
            stage = feature["question"]["identity"][3]
            reason = _eligible(feature)
            if reason is not None:
                self.reasons[reason] += 1
                self.horizons.update((stage, h, "not_requested_feature_unsupported") for h in HORIZONS)
                continue
            self.eligible += 1
            self.requests += 1
            requested = True
            _need(datetime.fromisoformat(feature["forecast_at"]) < cutoff
                  and datetime.fromisoformat(label["cutoff_at"]) <= cutoff, "training availability differs")
            if not label["results"]:
                reason = "training_session_missing"
                self.horizons.update((stage, h, "session_missing") for h in HORIZONS)
            else:
                self.horizons.update((stage, row[0], row[1]) for row in label["results"])
                unavailable = next((row for row in label["results"] if row[1] != "resolved"), None)
                if unavailable is not None:
                    reason = f"joint_horizon_{unavailable[0]}_{unavailable[1]}:{unavailable[5]}"
                elif max(datetime.fromisoformat(row[4]) for row in label["results"]) >= cutoff:
                    reason = "outcome_not_mature_before_split"
            if reason:
                self.reasons[reason] += 1
            else:
                self.supported += 1
                self.contexts[stage, feature["question"]["origin_context"]] += 1
                self.events.add((coordinate["session"], coordinate["symbol"]))
                self.dates.add(coordinate["session"])
        self.decodes += int(requested)
        if requested:
            self.max_cached = max(self.max_cached, len(labels["labels"]))

    def payload(self):
        return {"original_coordinates": self.coordinates, "original_questions": self.questions,
                "eligible_feature_questions": self.eligible, "supported_joint_questions": self.supported,
                "symbol_sessions": len(self.events), "distinct_dates": len(self.dates),
                "supported_by_stage_context": [list(key) + [n] for key, n in sorted(self.contexts.items())],
                "unavailable_by_reason": [list(row) for row in sorted(self.reasons.items())],
                "label_support_by_stage_horizon": [list(key) + [n] for key, n in sorted(self.horizons.items())]}


class _Group:
    def __init__(self):
        self.candidates = self.first = self.second = self.paired = self.outcomes = 0
        self.horizon_outcomes = [0, 0, 0]
        self.days = [defaultdict(list) for _ in HORIZONS]
        self.events = set()

    def coordinate(self, day, symbol, observations):
        cells = [[], [], []]
        for first, second, outcomes in observations:
            self.candidates += 1
            self.first += first is not None
            self.second += second is not None
            if first is None or second is None:
                continue
            self.paired += 1
            self.outcomes += any(outcome is not None for outcome in outcomes)
            for h, outcome in enumerate(outcomes):
                if outcome is None:
                    continue
                target = ("P1", "P2", "P3", "P4").index(outcome)
                cells[h].append(tuple(fsum((p - int(i == target)) ** 2 for i, p in enumerate(values[h]))
                                      for values in (first, second)))
                self.horizon_outcomes[h] += 1
        for h, values in enumerate(cells):
            if values:
                self.days[h][day].append(tuple(fsum(row[arm] for row in values) / len(values) for arm in (0, 1)))
                self.events.add((day, symbol))

    def payload(self):
        daily = [{day: tuple(fsum(row[arm] for row in values) / len(values) for arm in (0, 1))
                  for day, values in dates.items()} for dates in self.days]
        days = sorted({day for horizon in daily for day in horizon})
        means = [tuple(fsum(row[arm] for row in horizon.values()) / len(horizon) if horizon else None
                       for horizon in daily) for arm in (0, 1)]
        return {"candidates": self.candidates, "first_supported": self.first, "second_supported": self.second,
                "paired_forecasts": self.paired, "paired_outcomes": self.outcomes,
                "scored_events": len(self.events), "scored_dates": len(days),
                "first_brier": means[0] if days else None, "second_brier": means[1] if days else None,
                "daily_difference": [[day, [horizon[day][1] - horizon[day][0] if day in horizon else None
                                            for horizon in daily]] for day in days],
                "horizon_pairs": [self.paired] * 3, "horizon_outcomes": self.horizon_outcomes,
                "horizon_events": [sum(map(len, dates.values())) for dates in self.days],
                "horizon_dates": [len(dates) for dates in self.days]}


def _arms(raw):
    _need(type(raw) is list and len(raw) == 6, "six arms required")
    result = []
    for key, arm in zip(ARMS, raw, strict=True):
        _need(type(arm) is list and len(arm) == 4 and arm[0] == key, "arm order differs")
        if arm[1] == "unsupported":
            _need(arm[2] is None and type(arm[3]) is str and arm[3], "unsupported arm differs")
            result.append(None)
            continue
        values = arm[2]
        _need(arm[1] == "predicted" and arm[3] is None and type(values) is list and len(values) == 10
              and all(type(x) in (int, float) and isfinite(x) and 0 <= x <= 1 for x in values), "probabilities differ")
        total = fsum(values)
        _need(abs(total - 1) <= 1e-9, "probability mass differs")
        result.append(tuple(tuple(fsum(values[p + 3 * b] for b in range(h + 1)) / total for p in range(3))
                            + (fsum(values[3 * (h + 1):]) / total,) for h in range(3)))
    return tuple(result)


def _models(stream, metadata, record, fold, mode):
    models = []
    for key, summary in zip(ARMS[2:], record["models"], strict=True):
        row = next(stream, None)
        _need(row is not None, "model omitted")
        raw = row[0]
        _keys(raw, "kind key status reason artifact training query_input_sha256 independent_verification artifact_bytes_sha256 pair_sha256")
        _need(raw["kind"] == "model" and raw["key"] == key, "model order differs")
        contract = raw["training"]
        validate_training_contract(contract)
        expected_mode = "chart_context" if key.startswith("context_") else "chart"
        _need(contract["mode"] == expected_mode and contract["fold"] == fold.index
              and datetime.fromisoformat(contract["cutoff_at"]) == fold.first_test_opening
              and wire.valid_sha(raw["query_input_sha256"]), "model training identity differs")
        artifact, proof = raw["artifact"], raw["independent_verification"]
        proof_count = proof_digest = None
        if raw["status"] == "fitted":
            _need(raw["reason"] is None and type(artifact) is dict and artifact.get("training") == contract
                  and raw["artifact_bytes_sha256"] == sha256(encode(artifact)).hexdigest(), "artifact digest differs")
            if key.endswith("sequence"):
                _need(type(proof) is dict and proof.get("verified") is True
                      and type(proof.get("rows")) is int and type(proof.get("query_ids")) is list
                      and proof["rows"] == len(proof["query_ids"])
                      and proof.get("artifact_sha256") == artifact.get("artifact_sha256")
                      and proof.get("query_input_sha256") == raw["query_input_sha256"], "saved sequence proof differs")
                proof_count, proof_digest = proof["rows"], wire.digest(wire.encode(proof["query_ids"]))
            else:
                _need(proof is None, "unexpected tree proof")
        else:
            _need(raw["status"] == "unsupported" and raw["reason"] in
                  ("no_mature_supported_training_rows", "no_supported_test_questions")
                  and artifact is None and proof is None and raw["artifact_bytes_sha256"] is None,
                  "unsupported model differs")
        pair = {"model": key, "training_input_sha256": contract["input_sha256"],
                "query_input_sha256": raw["query_input_sha256"], "artifact_bytes_sha256": raw["artifact_bytes_sha256"],
                "unsupported_reason": raw["reason"]}
        _need(raw["pair_sha256"] == sha256(encode(pair)).hexdigest(), "model pair digest differs")
        expected = {name: raw[name] for name in ("key", "status", "reason", "artifact_bytes_sha256", "pair_sha256", "query_input_sha256")}
        expected.update(training_input_sha256=contract["input_sha256"], training_roster_sha256=contract["roster_sha256"],
                        training_weights_sha256=contract["weights_sha256"])
        _same(summary, expected)
        models.append({**expected, "contract": contract, "proof_count": proof_count, "proof_digest": proof_digest})
    _need(len(models) == 4 and models[0]["contract"] == models[2]["contract"]
          and models[1]["contract"] == models[3]["contract"]
          and models[0]["query_input_sha256"] == models[2]["query_input_sha256"]
          and models[1]["query_input_sha256"] == models[3]["query_input_sha256"], "same-view model contract differs")
    for field in ("roster_sha256", "weights_sha256", "outcomes_sha256", "supported_rows", "candidate_rows"):
        _need(all(model["contract"][field] == models[0]["contract"][field] for model in models), "common training differs")
    return models


class _Fold:
    def __init__(self, stream, record, fold, scenario, mode):
        self.stream, self.record, self.fold, self.scenario = stream, record, fold, scenario
        received = next(stream, None)
        _need(received is not None, "fold metadata omitted")
        self.metadata = metadata = received[0]
        _keys(metadata, "kind schema scenario index first_test_opening adapter_mode timing_scenarios_are_independent "
              "training_support empirical learned_fit_count empirical_fit_count")
        _need(metadata["kind"] == "fold" and metadata["schema"] == "ei-context-forecast-fold-draft-v1"
              and type(metadata["index"]) is int and metadata["index"] == fold.index
              and metadata["scenario"] == scenario and metadata["adapter_mode"] == mode
              and metadata["timing_scenarios_are_independent"] is False
              and datetime.fromisoformat(metadata["first_test_opening"]) == fold.first_test_opening,
              "fold identity differs")
        self.models = _models(stream, metadata, record, fold, mode)
        # Advancing past the last model releases its potentially large raw JSON
        # from the generic generator; retain only one small coordinate per fold.
        self.pending = next(stream, None)
        self.training = _Training()
        self.groups = [_Group() for _ in GROUPS]
        self.coordinates = self.questions = self.predicted = 0
        self.frames, self.statuses, self.missing = Counter(), Counter(), Counter()
        self.query_digest, self.query_first = sha256(b"["), True
        self.stage_keys = {stage: [] for stage in ("approach", "breakout")}
        self.reference_keys = []
        self.max_cached = 0

    def add(self, coordinate, labels):
        received = self.pending if self.pending is not None else next(self.stream, None)
        self.pending = None
        _need(received is not None, "original forecast coordinate omitted")
        raw = received[0]
        _keys(raw, "kind original predictions")
        original = {**coordinate, "questions": [[row["id"], wire.digest(wire.encode(row))] for row in coordinate["questions"]]}
        _need(raw["kind"] == "coordinate" and wire.encode(raw["original"]) == wire.encode(original),
              "original forecast feature or frame differs")
        _need(type(raw["predictions"]) is list and len(raw["predictions"]) == len(coordinate["questions"]),
              "prediction questions omitted or extra")
        self.coordinates += 1
        self.questions += len(coordinate["questions"])
        self.frames.update(frame[3] for frame in coordinate["frames"])
        self.statuses[coordinate["status"]] += 1
        observations = []
        stages = {row[0] for row in self.metadata["training_support"]["supported_by_stage_context"]}
        key = coordinate["session"], coordinate["symbol"]
        for feature, label, saved in zip(coordinate["questions"], labels["labels"], raw["predictions"], strict=True):
            _need(type(saved) is list and len(saved) == 2 and saved[0] == feature["id"], "saved question identity differs")
            projected = _arms(saved[1])
            stage, context = feature["question"]["identity"][3], feature["question"]["origin_context"]
            reason = _eligible(feature)
            if reason is None and stage not in stages:
                reason = "no_mature_supported_training_for_stage"
            _need(all(arm[1] == ("predicted" if reason is None else "unsupported") and arm[3] == reason for arm in saved[1]),
                  "common prediction support differs")
            outcomes = (None, None, None)
            if reason is None:
                self.predicted += 1
                if not self.query_first:
                    self.query_digest.update(b",")
                self.query_first = False
                self.query_digest.update(wire.encode(feature["question"]["identity"])[:-1])
                for keys in (self.stage_keys[stage], self.reference_keys):
                    if not keys or keys[-1] != key:
                        keys.append(key)
                self.max_cached = max(self.max_cached, len(labels["labels"]))
                if label["results"]:
                    outcomes = tuple(row[3] if row[1] == "resolved" else None for row in label["results"])
                    self.missing.update((row[0], row[5] or row[1]) for row in label["results"] if row[1] != "resolved")
                else:
                    self.missing.update((h, label["status"]) for h in HORIZONS)
            observations.append((stage, context, projected, outcomes))
        for group, (stage, context, _, first, second) in zip(self.groups, GROUPS, strict=True):
            group.coordinate(*key, [(arms[first], arms[second], outcomes) for s, c, arms, outcomes in observations
                                     if s == stage and (context == "all" or c == context)])

    def finish(self):
        _need(self.pending is None and next(self.stream, None) is None, "forecast coordinate extra")
        self.query_digest.update(b"]\n")
        support = self.training.payload()
        _same(self.metadata["training_support"], support)
        _same(self.record["training_support"], support)
        fits = 0
        for model in self.models:
            contract = model["contract"]
            for name, count in (("candidate_rows", self.training.eligible), ("supported_rows", self.training.supported),
                                ("event_count", len(self.training.events)), ("date_count", len(self.training.dates))):
                _same(contract[name], count)
            reason = ("no_mature_supported_training_rows" if not self.training.supported else
                      "no_supported_test_questions" if not self.predicted else None)
            _need(model["reason"] == reason and model["status"] == ("fitted" if reason is None else "unsupported"),
                  "model support differs")
            fits += int(reason is None)
            if reason is None and model["key"].endswith("sequence"):
                _need(model["proof_count"] == self.predicted and model["proof_digest"] == self.query_digest.hexdigest(),
                      "saved independent query roster differs")
        empirical_fits = int(bool(self.training.supported))
        for source in (self.metadata, self.record):
            _same(source["learned_fit_count"], fits)
            _same(source["empirical_fit_count"], empirical_fits)
        _need((self.metadata["empirical"] is not None) == bool(empirical_fits), "empirical support differs")
        report = {"scenario": self.scenario, "fold": self.fold.index, "evaluation_dates": list(self.fold.test_sessions),
                  "original_coordinates": self.coordinates, "original_questions": self.questions,
                  "original_frames_by_status": [list(item) for item in sorted(self.frames.items())],
                  "coordinate_status": [list(item) for item in sorted(self.statuses.items())],
                  "unavailable_labels_by_horizon": [list(key) + [n] for key, n in sorted(self.missing.items())],
                  "groups": [{"stage": stage, "origin_context": context, "contrast": name, "counts": group.payload()}
                             for (stage, context, name, _, _), group in zip(GROUPS, self.groups, strict=True)]}
        error = _same(self.record["score"], report)
        proof = self.record["report_reference"]
        _keys(proof, "verified scenario fold original_coordinates original_questions groups evaluation_labels_read "
              "maximum_absolute_difference evaluation_dates")
        expected = {"verified": True, "scenario": self.scenario, "fold": self.fold.index,
                    "original_coordinates": self.coordinates, "original_questions": self.questions,
                    "groups": 48, "evaluation_labels_read": self.predicted, "evaluation_dates": list(self.fold.test_sessions)}
        for name, value in expected.items():
            _same(proof[name], value)
        _need(type(proof["maximum_absolute_difference"]) in (float, int)
              and isfinite(proof["maximum_absolute_difference"]) and 0 <= proof["maximum_absolute_difference"] <= 1e-9,
              "saved reference error differs")
        return report, fits, empirical_fits, error


def _combined(reports):
    result = []
    for index, (stage, context, contrast, _, _) in enumerate(GROUPS):
        scores = [report["groups"][index]["counts"] for report in reports]
        values = {name: [sum(score[name][h] for score in scores) for h in range(3)] for name in HCOUNTS}
        for name in ("first_brier", "second_brier"):
            values[name] = [fsum(score[name][h] * score["horizon_dates"][h] for score in scores
                                if score["horizon_dates"][h]) / count if count else None
                            for h, count in enumerate(values["horizon_dates"])]
        result.append({"stage": stage, "origin_context": context, "contrast": contrast,
            **{name: sum(score[name] for score in scores) for name in COUNTS}, **values,
            "second_minus_first": [None if left is None else right - left
                                   for left, right in zip(values["first_brier"], values["second_brier"], strict=True)],
            "daily_difference": [row for score in scores for row in score["daily_difference"]]})
    return result


def audit_forecast_bundle(forecast_bundle: Path, snapshot_bundle: Path, directory: Path, *, expected_sha256: str,
                          expected_bytes: int, expected_members: dict, expected_summary: dict,
                          universe: UniverseCalendar, scenario: str, checkpoint,
                          expected_adapter_mode: str = "fixed_native") -> dict:
    """Audit saved forecasts in one original-coordinate pass, never rerun models.

    Authenticated original pins must come from the external receipt audit. This
    result verifies saved identities and arithmetic, not the legitimacy of pins.
    The caller owns an empty private directory and the existing audit budget.
    """
    _need(type(universe) is UniverseCalendar and wire.valid_sha(universe.raw_sha256) and callable(checkpoint)
          and scenario in ("S1", "S2") and expected_adapter_mode in ("fixed_native", "injected_diagnostic"),
          "arguments differ")
    _keys(expected_members, "features labels")
    _seal(expected_members["features"], FEATURE_SCHEMA, MAX_COORDINATES)
    _seal(expected_members["labels"], LABEL_SCHEMA, MAX_COORDINATES)
    wire.safe_path(directory)
    _need(directory.is_dir() and directory.stat().st_mode & 0o777 == 0o700 and not any(directory.iterdir()),
          "fresh private directory required")
    calendar = fixed_calendar(universe.sessions)
    groups = _roster(universe.targets)
    _need(tuple(sorted({day for day, _ in groups})) == universe.sessions
          and Counter((row.session, row.scenario) for row in universe.targets)
              == Counter({(day, s): 20 for day in universe.sessions for s in ("S1", "S2")}), "original roster differs")
    _same(expected_summary["members"], expected_members)
    for name, expected in (("universe_sha256", universe.raw_sha256),
                           ("calendar_sha256", wire.digest(wire.encode(universe.sessions))),
                           ("original_dates", len(universe.sessions)), ("original_coordinates", len(groups)),
                           ("original_memberships", len(universe.targets)),
                           ("counts_sha256", wire.digest(wire.encode(expected_summary["counts"])))):
        _same(expected_summary[name], expected)
    with TemporaryDirectory(prefix="ei-saved-forecast-audit-", dir=directory) as scratch:
        folder = Path(scratch)
        header, members = _unpack(forecast_bundle, folder, expected_sha256, expected_bytes, expected_members, checkpoint)
        _same(header["scenario"], scenario)
        _same(header["adapter_mode"], expected_adapter_mode)
        _same(header["model_keys"], list(ARMS))
        _same(header["contrasts"], [[name, ARMS[first], ARMS[second]] for name, first, second in CONTRASTS])
        _same(header["original_universe_sha256"], universe.raw_sha256)
        _same(header["original_calendar_sha256"], wire.digest(wire.encode(universe.sessions)))
        _same(header["audited_snapshot_summary_sha256"], wire.digest(wire.encode(expected_summary)))
        _same(header["input_support"], expected_summary["counts"])
        _need(header["timing_scenarios_are_independent"] is header["contains_economic_returns"] is False,
              "scope flags differ")
        unpacked = folder / "snapshot"
        unpacked.mkdir(mode=0o700)
        feature_path, feature_seal, label_path, label_seal = open_bundle(snapshot_bundle, unpacked, checkpoint)
        _same({"features": asdict(feature_seal), "labels": asdict(label_seal)}, expected_members)
        audits = []
        for fold, record, (path, seal) in zip(calendar.folds, header["folds"], members, strict=True):
            _keys(record, "fold training_sessions test_sessions seal learned_fit_count empirical_fit_count training_support models score report_reference")
            _same(record["fold"], fold.index)
            _same(record["training_sessions"], list(fold.training_sessions))
            _same(record["test_sessions"], list(fold.test_sessions))
            _need(type(record["models"]) is list and len(record["models"]) == 4, "four model records required")
            stream = _read(path, seal, _forecast_header(expected_members), maximum=MAX_COORDINATES + 5)
            audits.append(_Fold(stream, record, fold, scenario, expected_adapter_mode))
        source = _read(feature_path, feature_seal, stream_roster_header(snapshot, groups), maximum=MAX_COORDINATES)
        labels = _read(label_path, label_seal, _label_header(feature_seal), maximum=MAX_COORDINATES)
        census = {name: _Census() for name in ("union", "S1", "S2")}
        training_days = {day: [audit for audit in audits if day in audit.fold.training_sessions] for day in universe.sessions}
        test_days = {day: audit for audit in audits for day in audit.fold.test_sessions}
        for ((day, symbol), targets), (coordinate, _), (label, _) in zip(groups.items(), source, labels, strict=True):
            checkpoint("ei_saved_forecast_original_coordinate")
            _need(coordinate["session"] == day and coordinate["symbol"] == symbol
                  and coordinate["targets"] == [_target(row) for row in targets]
                  and label["session"] == day and label["symbol"] == symbol
                  and len(coordinate["questions"]) == len(label["labels"]), "snapshot original coordinate differs")
            for feature, outcome in zip(coordinate["questions"], label["labels"], strict=True):
                _need(feature["id"] == outcome["id"]
                      and wire.encode(feature["question"]) == wire.encode(outcome["question"]), "snapshot question binding differs")
            census["union"].add(coordinate, label)
            for target in targets:
                census[target.scenario].add(coordinate, label)
            if any(target.scenario == scenario for target in targets):
                for audit in training_days[day]:
                    audit.training.add(coordinate, label, audit.fold.first_test_opening)
                if day in test_days:
                    test_days[day].add(coordinate, label)
        counts = {"union": census["union"].payload(), "scenarios": {name: census[name].payload() for name in ("S1", "S2")}}
        _same(counts, expected_summary["counts"])
        completed = [audit.finish() for audit in audits]
        reports = [row[0] for row in completed]
        _same(header["learned_fit_count"], sum(row[1] for row in completed))
        _same(header["empirical_fit_count"], sum(row[2] for row in completed))
        error = max(max(row[3] for row in completed), _same(header["date_weighted_comparisons"], _combined(reports)))
        decoded, requested, maximum_cached = Counter(), Counter(), 0
        for audit in audits:
            if audit.training.requests:
                requested["training"] += audit.training.requests
                decoded["training"] += audit.training.decodes
            cache = None
            for phase, keys in (("evaluation", audit.stage_keys["approach"] + audit.stage_keys["breakout"]),
                                ("reference", audit.reference_keys)):
                if audit.predicted:
                    requested[phase] += audit.predicted
                for key in keys:
                    if key != cache:
                        decoded[phase] += 1
                        cache = key
            maximum_cached = max(maximum_cached, audit.max_cached, audit.training.max_cached)
        io = {"label_coordinate_decodes": dict(decoded), "label_question_requests": dict(requested),
              "maximum_cached_label_questions": maximum_cached, "label_index_coordinates": len(groups),
              "raw_label_index_full_eof_verified": True}
        _same(header["io"], io)
    producer_summary = {"schema": SCHEMA, "scenario": scenario, "adapter_mode": expected_adapter_mode,
        "input_members": expected_members, "folds": 5,
        "evaluation_sessions": len(calendar.evaluation_reference_sessions),
        "original_evaluation_coordinates": sum(row["original_coordinates"] for row in reports),
        "original_evaluation_questions": sum(row["original_questions"] for row in reports),
        "learned_fit_count": header["learned_fit_count"], "empirical_fit_count": header["empirical_fit_count"],
        "fold_seals": [record["seal"] for record in header["folds"]], "fold_prediction_readback_verified": True,
        "report_reference_verified": True, "independent_saved_bundle_audit": False,
        "timing_scenarios_are_independent": False, "contains_economic_returns": False, "io": io}
    return {"schema": SCHEMA, "verified": True, "scenario": scenario, "adapter_mode": expected_adapter_mode,
            "output_sha256": expected_sha256, "output_bytes": expected_bytes, "folds": 5,
            "original_evaluation_coordinates": sum(row["original_coordinates"] for row in reports),
            "original_evaluation_questions": sum(row["original_questions"] for row in reports),
            "learned_fit_count": header["learned_fit_count"], "empirical_fit_count": header["empirical_fit_count"],
            "fold_seals": [record["seal"] for record in header["folds"]], "members": expected_members,
            "maximum_absolute_difference": error, "full_eof_verified": True, "original_roster_verified": True,
            "original_snapshot_science_reexecuted": False, "model_refit": False, "model_inference_reexecuted": False,
            "numeric_query_projection_reexecuted": False, "worker_execution_order_reexecuted": False,
            "independent_saved_bundle_audit": True, "contains_economic_returns": False,
            "timing_scenarios_are_independent": False, "io": io, "producer_summary": producer_summary}
