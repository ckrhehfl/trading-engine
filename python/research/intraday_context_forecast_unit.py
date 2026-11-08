"""Five fixed EI forecast folds from caller-authenticated sealed inputs.

This source-only worker supplies no actual-access admission or registration.
The caller authenticates the original CB universe and predecessor summary.
Label bytes are indexed without decoding their outcomes; each fold opens only
training coordinates until its complete predictions are sealed and read back.
The resulting probability scores are not returns or economic entry policies.
"""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_context_forecast_fold import MODEL_KEYS, ForecastAdapters, forecast_fold
from research.intraday_context_forecast_report import CONTRASTS, combine_fold_reports, score_fold
from research.intraday_context_forecast_report_reference import verify_forecast_report
from research.intraday_context_forecast_seal import _header as forecast_header, read_forecasts, seal_forecasts
from research.intraday_context_snapshot_unit import open_bundle
from research.intraday_ed_sources import UniverseCalendar
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_input, _sealed_output, _stream_member


SCHEMA = "ei-context-forecast-unit-draft-v1"
MAXIMUM_BYTES = 128 * wire.MIB
MAXIMUM_HEADER = 4 * wire.MIB
ORIGINAL_PER_DAY = 20
FOLD_COUNT = 5
MAX_LEARNED_FITS = FOLD_COUNT * 4
MAX_EMPIRICAL_FITS = FOLD_COUNT


@dataclass(frozen=True)
class ForecastUnitResult:
    seal: BundleSeal
    summary: dict


def _json(value):
    if type(value) in (date, datetime):
        return value.isoformat()
    if type(value) in (list, tuple):
        return [_json(item) for item in value]
    if type(value) is dict:
        return {key: _json(item) for key, item in value.items()}
    return value


def _original(universe: UniverseCalendar, expected_members: dict, expected_summary: dict):
    wire.require(type(universe) is UniverseCalendar and wire.valid_sha(universe.raw_sha256)
                 and type(expected_members) is dict and set(expected_members) == {"features", "labels"}
                 and type(expected_summary) is dict, "EI forecast audited predecessor and original universe required")
    calendar = fixed_calendar(universe.sessions)
    groups = snapshot._roster(universe.targets)
    memberships = Counter((row.session, row.scenario) for row in universe.targets)
    wire.require(tuple(sorted({day for day, _ in groups})) == universe.sessions
                 and set(memberships) == {(day, scenario) for day in universe.sessions for scenario in ("S1", "S2")}
                 and set(memberships.values()) == {ORIGINAL_PER_DAY}, "EI forecast original population differs")
    expected = {"members": expected_members, "universe_sha256": universe.raw_sha256,
                "calendar_sha256": wire.digest(wire.encode(universe.sessions)),
                "original_dates": len(universe.sessions), "original_coordinates": len(groups),
                "original_memberships": len(universe.targets)}
    wire.require(all(wire.encode(expected_summary.get(key)) == wire.encode(value) for key, value in expected.items())
                 and type(expected_summary.get("counts")) is dict
                 and expected_summary.get("counts_sha256") == wire.digest(wire.encode(expected_summary["counts"])),
                 "EI forecast audited summary does not bind the original population")
    return calendar, groups


class _FeatureCounts:
    """Count causal support only: never inspect the predecessor's path totals."""

    def __init__(self):
        self.coordinates = self.frames = self.questions = 0
        self.coordinate_states, self.frame_states, self.contexts = Counter(), Counter(), Counter()
        self.feature_states = {stage: Counter() for stage in ("approach", "breakout")}

    def add(self, coordinate):
        self.coordinates += 1
        self.frames += len(coordinate.frames)
        self.questions += len(coordinate.questions)
        self.coordinate_states[coordinate.status] += 1
        self.frame_states.update(row.status for row in coordinate.frames)
        self.contexts.update(row.classification for row in coordinate.frames)
        for row in coordinate.questions:
            self.feature_states[row.question.stage][row.status] += 1

    def payload(self):
        return {"coordinates": self.coordinates, "frames": self.frames, "questions": self.questions,
                "coordinate_states": dict(self.coordinate_states), "frame_states": dict(self.frame_states),
                "contexts": dict(self.contexts),
                "feature_states": {stage: dict(counts) for stage, counts in self.feature_states.items()}}


class _LabelIndex:
    """Compact raw-byte index, one decoded coordinate cache, explicit phase guard."""

    def __init__(self, stream, path, seal, feature_seal, groups, checkpoint):
        self.stream, self.path, self.checkpoint = stream, path, checkpoint
        self.identity = wire.file_identity(os.fstat(stream.fileno()))
        self.offsets, self.features = {}, {}
        self.cached_key, self.cached_labels = None, {}
        self.feature_eof = False
        self.evaluation_fold = None
        self.decoded = Counter()
        self.requests = Counter()
        self.maximum_cached_questions = 0
        wire.require(seal.records == len(groups) == feature_seal.records,
                     "EI forecast label index original count differs")
        header = stream.readline(snapshot.MAX_LINE + 1)
        wire.require(header == wire.encode(snapshot._label_header(feature_seal)),
                     "EI forecast label index header differs")
        digest, size = sha256(header), len(header)
        for key in groups:
            checkpoint("ei_forecast_index_label_bytes")
            offset = stream.tell()
            raw = stream.readline(snapshot.MAX_LINE + 1)
            wire.require(raw.endswith(b"\n") and len(raw) <= snapshot.MAX_LINE,
                         "EI forecast label index row bound differs")
            digest.update(raw)
            size += len(raw)
            wire.require(size <= seal.size, "EI forecast label index size exceeded")
            self.offsets[key] = offset, len(raw), wire.digest(raw)
        footer = stream.readline(snapshot.MAX_LINE + 1)
        wire.require(footer == wire.encode({"records": seal.records}) and not stream.read(1),
                     "EI forecast label index footer or EOF differs")
        digest.update(footer)
        size += len(footer)
        wire.require(size == seal.size and digest.hexdigest() == seal.sha256,
                     "EI forecast label index seal differs")
        self.assert_unchanged()

    def assert_unchanged(self):
        wire.require(self.identity == wire.file_identity(os.fstat(self.stream.fileno()))
                     == wire.file_identity(wire.regular(self.path)), "EI forecast indexed labels changed")

    def start_features(self):
        self.feature_eof = False
        self.cached_key, self.cached_labels = None, {}
        self.evaluation_fold = None

    def register(self, coordinate):
        key = coordinate.session, coordinate.symbol
        ids = tuple(snapshot.question_digest(row.question) for row in coordinate.questions)
        wire.require(key in self.offsets and len(set(ids)) == len(ids)
                     and self.features.setdefault(key, ids) == ids, "EI forecast original feature identities changed")

    def finish_features(self):
        wire.require(self.features.keys() == self.offsets.keys(), "EI forecast incomplete original feature stream")
        self.feature_eof = True

    def allow_evaluation(self, fold):
        wire.require(self.feature_eof and type(fold) is int and 1 <= fold <= FOLD_COUNT,
                     "EI forecast cannot enable evaluation before original EOF")
        self.cached_key, self.cached_labels = None, {}
        self.evaluation_fold = fold

    def reader(self, *, fold, phase, allowed_dates):
        wire.require(phase in ("training", "evaluation", "reference"), "EI forecast unknown label phase")
        dates = frozenset(allowed_dates)

        def read(day, symbol, question_id):
            wire.require(self.feature_eof and day in dates and (day, symbol) in self.offsets
                         and (self.evaluation_fold is None if phase == "training" else self.evaluation_fold == fold),
                         "EI forecast label phase or date violation")
            self.requests[phase] += 1
            key = day, symbol
            self.assert_unchanged()
            if self.cached_key != key:
                self.checkpoint(f"ei_forecast_{phase}_label_coordinate")
                offset, length, digest = self.offsets[key]
                self.stream.seek(offset)
                raw = self.stream.read(length)
                wire.require(len(raw) == length and wire.digest(raw) == digest, "EI forecast indexed label row changed")
                value = json.loads(raw)
                wire.require(wire.encode(value) == raw, "EI forecast indexed label noncanonical")
                coordinate = snapshot._read_label_coordinate(value)
                wire.require((coordinate.session, coordinate.symbol) == key
                             and tuple(label.question_id for label in coordinate.labels) == self.features[key]
                             and wire.encode(snapshot._label_coordinate(coordinate)) == raw,
                             "EI forecast indexed label original coordinate or question differs")
                self.cached_labels = {label.question_id: label for label in coordinate.labels}
                self.cached_key = key
                self.decoded[phase] += 1
                self.maximum_cached_questions = max(self.maximum_cached_questions, len(self.cached_labels))
            wire.require(question_id in self.cached_labels, "EI forecast original question label omitted")
            return self.cached_labels[question_id]

        return read


def _feature_stream(path, seal, universe, labels, expected_summary, checkpoint):
    labels.start_features()
    total, scenarios = _FeatureCounts(), {name: _FeatureCounts() for name in ("S1", "S2")}
    for coordinate in snapshot.read_features(path, seal, expected_targets=universe.targets):
        checkpoint("ei_forecast_feature_coordinate")
        labels.register(coordinate)
        total.add(coordinate)
        for target in coordinate.targets:
            scenarios[target.scenario].add(coordinate)
        yield coordinate
    expected = expected_summary["counts"]
    wire.require(type(expected.get("union")) is dict and type(expected.get("scenarios")) is dict,
                 "EI forecast original causal support missing")
    for counted, saved in ((total, expected["union"]),
                           *((scenarios[name], expected["scenarios"].get(name)) for name in ("S1", "S2"))):
        wire.require(type(saved) is dict and all(wire.encode(saved.get(key)) == wire.encode(value)
                     for key, value in counted.payload().items()), "EI forecast original causal support differs")
    labels.finish_features()


def _pack(output, header, folds, checkpoint):
    raw = wire.encode(header)
    size = len(raw) + sum(seal.size for _, seal in folds)
    wire.require(len(raw) <= MAXIMUM_HEADER and size <= MAXIMUM_BYTES, "EI forecast bundle bounds exceeded")
    digest = sha256(raw)
    with _sealed_output(output) as destination:
        wire.write_all(destination, raw)
        for path, seal in folds:
            checkpoint("ei_forecast_pack_fold")
            with _sealed_input(path) as (source, member_size):
                wire.require(member_size == seal.size, "EI forecast member size differs")
                first = wire.encode(forecast_header(snapshot.SnapshotSeal(**header["snapshot"]["features"]),
                                                    snapshot.SnapshotSeal(**header["snapshot"]["labels"])))
                _stream_member(source, seal, first, checkpoint, target=destination, bundle_digest=digest)
                wire.require(not source.read(1), "EI forecast member trailing bytes")
    wire.require(wire.file_digest(output, MAXIMUM_BYTES, checkpoint) == (digest.hexdigest(), size),
                 "EI forecast bundle readback differs")
    return BundleSeal(digest.hexdigest(), size)


def compare_snapshot(snapshot_bundle: Path, output: Path, *, scenario: str, expected_members: dict,
                     expected_summary: dict, universe: UniverseCalendar, checkpoint,
                     adapters: ForecastAdapters | None = None) -> ForecastUnitResult:
    """Compare five fixed folds; the caller supplies authenticated original pins.

    ``adapters`` exists for invented wiring tests and is recorded as diagnostic.
    Only the registered caller can authorize an actual run; this API cannot.
    The audited predecessor's full label semantics are trusted by member hash,
    while every accessed label is again bound to its original typed question.
    """
    wire.require(scenario in ("S1", "S2") and snapshot_bundle != output and callable(checkpoint)
                 and (adapters is None or type(adapters) is ForecastAdapters), "EI forecast arguments differ")
    calendar, groups = _original(universe, expected_members, expected_summary)
    with TemporaryDirectory(prefix="ei-forecast-folds-", dir=output.parent) as temporary:
        folder = Path(temporary)
        unpacked = folder / "snapshot"
        unpacked.mkdir(mode=0o700)
        feature_path, feature_seal, label_path, label_seal = open_bundle(snapshot_bundle, unpacked, checkpoint)
        members = {"features": asdict(feature_seal), "labels": asdict(label_seal)}
        wire.require(members == expected_members, "EI forecast snapshot members differ from audited predecessor")
        with _sealed_input(label_path) as (stream, size):
            wire.require(size == label_seal.size, "EI forecast indexed source size differs")
            labels = _LabelIndex(stream, label_path, label_seal, feature_seal, groups, checkpoint)
            folds, reports, summaries = [], [], []
            learned_fits = empirical_fits = 0
            for fold in calendar.folds:
                checkpoint("ei_forecast_start_fold")
                source = _feature_stream(feature_path, feature_seal, universe, labels, expected_summary, checkpoint)
                forecast = forecast_fold(source, fold, scenario,
                    labels.reader(fold=fold.index, phase="training", allowed_dates=fold.training_sessions),
                    expected_targets=universe.targets, adapters=adapters)
                learned_fits += forecast.learned_fit_count
                empirical_fits += forecast.empirical_fit_count
                wire.require(learned_fits <= MAX_LEARNED_FITS and empirical_fits <= MAX_EMPIRICAL_FITS,
                             "EI forecast fixed fit budget exceeded")
                prediction_path = folder / f"fold-{fold.index}.jsonl"
                prediction_seal = seal_forecasts(prediction_path, forecast, feature_seal, label_seal)
                original = forecast.original_test_coordinates
                mode = forecast.adapter_mode
                del forecast
                stored = read_forecasts(prediction_path, prediction_seal, feature_seal=feature_seal,
                    label_seal=label_seal, expected_coordinates=original, expected_fold=fold,
                    expected_scenario=scenario, expected_adapter_mode=mode)
                checkpoint("ei_forecast_predictions_verified")
                labels.allow_evaluation(fold.index)
                report = score_fold(stored.forecast,
                    labels.reader(fold=fold.index, phase="evaluation", allowed_dates=fold.test_sessions),
                    allowed_dates=fold.test_sessions, expected_targets=universe.targets)
                proof = verify_forecast_report(stored.forecast, report,
                    labels.reader(fold=fold.index, phase="reference", allowed_dates=fold.test_sessions),
                    allowed_dates=fold.test_sessions, expected_targets=universe.targets)
                wire.require(proof.get("verified") is True and proof.get("fold") == fold.index
                             and proof.get("scenario") == scenario
                             and report.original_coordinates == ORIGINAL_PER_DAY * len(fold.test_sessions),
                             "EI forecast original population or independent report differs")
                checkpoint("ei_forecast_report_verified")
                summary = {"fold": fold.index, "training_sessions": fold.training_sessions,
                    "test_sessions": fold.test_sessions, "seal": asdict(prediction_seal),
                    "learned_fit_count": stored.forecast.learned_fit_count,
                    "empirical_fit_count": stored.forecast.empirical_fit_count,
                    "training_support": _json(asdict(stored.forecast.training_support)),
                    "models": [{"key": model.key, "status": model.status, "reason": model.reason,
                                "artifact_bytes_sha256": model.artifact_bytes_sha256,
                                "pair_sha256": model.pair_sha256, "query_input_sha256": model.query_input_sha256,
                                "training_input_sha256": model.training["input_sha256"],
                                "training_roster_sha256": model.training["roster_sha256"],
                                "training_weights_sha256": model.training["weights_sha256"]}
                               for model in stored.forecast.models],
                    "score": _json(asdict(report)), "report_reference": _json(proof)}
                folds.append((prediction_path, prediction_seal))
                reports.append(report)
                summaries.append(summary)
                del stored, original, source
            labels.assert_unchanged()
            io = {"label_coordinate_decodes": dict(labels.decoded), "label_question_requests": dict(labels.requests),
                  "maximum_cached_label_questions": labels.maximum_cached_questions,
                  "label_index_coordinates": len(labels.offsets), "raw_label_index_full_eof_verified": True}
        wire.require(len(folds) == FOLD_COUNT, "EI forecast fixed five folds required")
        comparisons = _json(combine_fold_reports(tuple(reports)))
        header = {"schema": SCHEMA, "scenario": scenario, "adapter_mode": mode, "model_keys": MODEL_KEYS,
            "contrasts": CONTRASTS, "snapshot": members,
            "original_universe_sha256": universe.raw_sha256,
            "original_calendar_sha256": wire.digest(wire.encode(universe.sessions)),
            "audited_snapshot_summary_sha256": wire.digest(wire.encode(expected_summary)),
            "folds": summaries, "date_weighted_comparisons": comparisons,
            "learned_fit_count": learned_fits, "empirical_fit_count": empirical_fits,
            "timing_scenarios_are_independent": False, "contains_economic_returns": False,
            "input_support": expected_summary["counts"], "io": io}
        sealed = _pack(output, header, tuple(folds), checkpoint)
    summary = {"schema": SCHEMA, "scenario": scenario, "adapter_mode": mode, "input_members": members,
        "folds": FOLD_COUNT, "evaluation_sessions": len(calendar.evaluation_reference_sessions),
        "original_evaluation_coordinates": sum(report.original_coordinates for report in reports),
        "original_evaluation_questions": sum(report.original_questions for report in reports),
        "learned_fit_count": learned_fits, "empirical_fit_count": empirical_fits,
        "fold_seals": [item["seal"] for item in summaries], "fold_prediction_readback_verified": True,
        "report_reference_verified": True, "independent_saved_bundle_audit": False,
        "timing_scenarios_are_independent": False, "contains_economic_returns": False, "io": io}
    return ForecastUnitResult(sealed, summary)
