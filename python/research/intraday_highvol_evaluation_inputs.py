"""Authenticated EG evaluation input without a backend or early outcome access."""

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
import re
from types import MappingProxyType

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_fit_run as fit
from research import intraday_highvol_fit_unit as fit_unit
from research import intraday_highvol_runtime as runtime
from research import intraday_highvol_snapshot_unit as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_highvol_coverage import MAX_COMPRESSED, MAX_DECODED, MAX_LINE, open_coverage
from research.intraday_highvol_events import OFFSETS
from research.intraday_highvol_models import ModelPrediction
from research.intraday_highvol_policy import EntryForecast
from research.intraday_highvol_saved_management import HoldingForecast
from research.intraday_highvol_snapshot import HOLD_STEPS, TIMINGS
from research.intraday_highvol_snapshot_io import open_snapshot


@dataclass(frozen=True)
class PredecessorPin:
    unit_id: str
    attempt: str
    audit_sha256: str
    merge_commit: str


@dataclass(frozen=True)
class SavedForecast:
    fold_index: int
    decision_at: str
    support: str
    entry_support: str
    status: str
    comparison_scope: str | None
    prediction: ModelPrediction | None
    training_target_counts: tuple[int, int]
    training_target_rate: Fraction | None
    replay_ambiguities: frozenset[str]


@dataclass(frozen=True)
class _Candidate:
    key: tuple[str, str, int, int]
    decision_at: datetime
    support: str
    entry_support: str

    def eligible(self, arm):
        if arm == "TCN_CHART_PEER":
            return self.support == self.entry_support == "common"
        return self.support in ("common", "chart_only") and self.entry_support in ("common", "chart_only")


def _pin(pin, unit_id, invented_only):
    wire.require(type(pin) is PredecessorPin and pin.unit_id == unit_id
                 and type(pin.attempt) is str and type(pin.merge_commit) is str
                 and re.fullmatch(r"[0-9a-f]{40}", pin.merge_commit) is not None
                 and wire.valid_sha(pin.audit_sha256)
                 and (re.fullmatch(r"qualification[1-9][0-9]*", pin.attempt) is not None if invented_only
                      else pin.attempt == "actual1"), "EG evaluation predecessor pin or scope differs")
    return runtime.NATIVE_ROOT / unit_id / pin.attempt


def _key(raw, *, decision=False):
    width = 4 if decision else 3
    wire.require(type(raw) in (list, tuple) and len(raw) == width, "EG evaluation key width differs")
    day, symbol, offset = raw[:3]
    wire.require(type(day) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day) is not None
                 and type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
                 and type(offset) is int and offset in OFFSETS
                 and (not decision or type(raw[3]) is int and raw[3] in (0, *HOLD_STEPS)),
                 "EG evaluation event key differs")
    return tuple(raw)


def _order(key):
    day, symbol, offset = key[:3]
    return day, offset, symbol, *key[3:]


def _clock(raw):
    value = datetime.fromisoformat(raw)
    wire.require(value.utcoffset() is not None and value.isoformat() == raw, "EG evaluation clock differs")
    return value


def _feature_roster(directory, receipt, checkpoint):
    by_cell, full_keys, universe = defaultdict(list), [], None
    with open_snapshot(directory, "features", fit._seal(receipt["seals"]["features"]), checkpoint) as reader:
        wire.require(reader.header["binding"] == receipt["binding"], "EG evaluation feature header differs")
        universe = reader.header["universe"]
        calendar = fixed_calendar(universe["sessions"])
        source_dates = set(calendar.source_reference_sessions)
        folds = {day: fold.index for fold in calendar.folds for day in fold.test_sessions}
        for frame in reader.records():
            key = _key(frame["key"])
            day = key[0]
            wire.require(day in source_dates and (not full_keys or _order(full_keys[-1]) < _order(key)),
                         "EG evaluation feature order differs")
            full_keys.append(key)
            attempt, timings = frame["attempt"], frame["timings"]
            wire.require(type(timings) is list and timings and timings == [name for name in TIMINGS if name in timings],
                         "EG evaluation original timing membership differs")
            if day not in folds or attempt["state"] != "confirmed":
                continue
            branch = attempt["scenario"]
            entry, holding = frame["entry"], frame["holding"]
            inside = entry["primary_clock_supported"]
            wire.require(branch in ("A", "B") and type(inside) is bool and type(holding) is list
                         and tuple(row["step_minutes"] for row in holding) == (HOLD_STEPS if inside else ()),
                         "EG evaluation feature decision roster differs")
            for timing in timings:
                first = None
                for step, context in zip((0, *(HOLD_STEPS if inside else ())), (entry, *holding), strict=True):
                    chart, peer = context["chart"]["status"], context["peers"][timing]["status"]
                    wire.require(chart in ("available", "unavailable") and peer in ("available", "unavailable"),
                                 "EG evaluation feature support differs")
                    support = ("outside_clock" if not inside else "chart_unavailable" if chart == "unavailable"
                               else "common" if peer == "available" else "chart_only")
                    if step == 0:
                        first = support
                    by_cell[branch, timing, folds[day]].append(_Candidate((*key, step),
                        _clock(context["decision_at"]), support, first))
        wire.require(reader.footer["report"] == {"union_events": len(full_keys)}, "EG evaluation feature footer differs")
    return calendar, universe, tuple(full_keys), by_cell


def _fit_predecessor(directory, pin, invented_only, snapshot_receipt, checkpoint):
    audit_raw, audit = fit_unit._document(directory / "original-audit.json", wire.MIB)
    publication_raw, publication = fit_unit._document(directory / "publication.json")
    binding, result = publication["binding"], publication["result"]
    wire.require(wire.digest(audit_raw) == pin.audit_sha256 and audit["schema"] == "EG-original-audit-v1"
                 and audit["status"] == "PASS" and audit["science_reexecuted"] is False
                 and audit["binding"] == binding and audit["publication_sha256"] == wire.digest(publication_raw)
                 and publication["schema"] == "EG-unit-publication-v1" and binding["unit_id"] == pin.unit_id
                 and binding["attempt"] == pin.attempt and binding["invented_only"] is invented_only
                 and binding["merge_commit"] == pin.merge_commit and binding["resource_profile"] == runtime.PROFILE
                 and binding["input_contract"] == fit_unit.INPUT_CONTRACT
                 and binding["geometry"] == audit["full_geometry"] == result["geometry"] == runtime.FULL
                 and all(wire.valid_sha(binding[name]) for name in ("source_sha256", "runtime_sha256", "registration_sha256"))
                 and not (directory / "unresolved.json").exists() and not (directory / "unresolved.json").is_symlink(),
                 "EG evaluation audited fit publication differs")
    for name, expected in (("service-final.json", audit["service_final_sha256"]),
                           ("service-exit.json", audit["service_exit_sha256"]),
                           ("discovery.jsonl", publication["journal_sha256"])):
        wire.require(wire.valid_sha(expected) and wire.file_digest(directory / name, runtime.MAX_METADATA, checkpoint)[0]
                     == expected, "EG evaluation audited fit lifecycle bytes differ")
    receipt_raw, receipt = fit_unit._document(directory / fit.OUTPUT, wire.MIB)
    proof, summary = result["independent_verification"], result["summary"]
    _, snapshot_pins = fit._snapshot_contract(snapshot_receipt, binding, pin.unit_id)
    roster = fit.fit_roster(pin.unit_id)
    coordinates, verified = receipt["coordinates"], proof["coordinates"]
    wire.require(receipt["schema"] == fit.RECEIPT_SCHEMA and receipt["binding"] == binding
                 and receipt["snapshot"] == summary["snapshot"] == snapshot_pins
                 and proof["schema"] == "EG-model-fit-verification-v1" and proof["complete"] is True
                 and proof["refit_count"] == 0 and proof["receipt_sha256"] == wire.digest(receipt_raw)
                 and type(receipt["fit_count"]) is int and (0 < receipt["fit_count"] if invented_only else 0 <= receipt["fit_count"])
                 and receipt["fit_count"] == summary["fit_count"] == proof["fit_count"]
                 and receipt["fit_count"] == sum(row["status"] == "verified" for row in verified)
                 and receipt["fit_count"] <= receipt["maximum_fits"] == summary["maximum_fits"] == len(roster)
                 and len(coordinates) == len(verified) == len(roster), "EG evaluation fit receipt or replay proof differs")
    expected = []
    for coordinate, saved, replay in zip(roster, coordinates, verified, strict=True):
        wire.require(saved["name"] == coordinate.name and saved["coordinate"] == replay["coordinate"] == asdict(coordinate)
                     and (replay["status"] == "verified") == (saved["fit_status"] == "fit"),
                     "EG evaluation fit coordinate roster differs")
        seal = fit._seal(saved["seal"])
        expected.append(runtime.Artifact(coordinate.name, seal.sha256, seal.compressed_bytes, MAX_COMPRESSED).payload())
    expected.append(runtime.Artifact(fit.OUTPUT, wire.digest(receipt_raw), len(receipt_raw), wire.MIB).payload())
    wire.require(expected == result["artifacts"] and summary["coordinates"] == [
                 {key: saved[key] for key in ("coordinate", "fit_status", "candidate_counts")} for saved in coordinates],
                 "EG evaluation audited fit artifact pins differ")
    return receipt, proof


def _fold_forecasts(directory, coordinate, saved, replay, receipt, calendar, candidates, checkpoint):
    bound = {**receipt["binding"], "snapshot": receipt["snapshot"], "coordinate": asdict(coordinate)}
    fold = calendar.folds[coordinate.fold_index - 1]
    counts, scored, forecasts = Counter(), {}, {}
    fitted = saved["fit_status"] == "fit"
    ambiguity = defaultdict(set)
    if fitted:
        ambiguities = replay["threshold_ambiguous_keys"]
        wire.require(set(ambiguities) == {"gross33", "gross43", "hold_zero"}, "EG evaluation replay threshold roster differs")
        for threshold, keys in ambiguities.items():
            previous = None
            for raw in keys:
                key = _key(raw, decision=True)
                wire.require((previous is None or _order(previous) < _order(key))
                             and (key[3] != 0) == (threshold == "hold_zero"), "EG evaluation replay ambiguity key differs")
                previous = key
                ambiguity[key].add(threshold)
    else:
        wire.require(saved["fit_status"] in ("fold_unsupported", "no_prediction_inputs")
                     and replay["status"] == saved["fit_status"] and replay["replayed_rows"] == 0,
                     "EG evaluation unsupported fold proof differs")

    with open_coverage(directory / coordinate.name, fit._seal(saved["seal"]), checkpoint, schema=fit.SCHEMA) as reader:
        wire.require(reader.header["binding"] == bound and reader.header["universe"] == {}, "EG evaluation fit header differs")
        records = reader.records()
        metadata = next(records)
        model = metadata["model"]
        target = metadata["target_first_false_true_rows"]
        wire.require(type(target) is list and len(target) == 2 and all(type(n) is int and n >= 0 for n in target),
                     "EG evaluation training target counts differ")
        rate = Fraction(target[1], sum(target)) if sum(target) else None
        wire.require(metadata["kind"] == "fit" and metadata["binding"] == bound
                     and metadata["fit_status"] == saved["fit_status"]
                     and metadata["training_sessions"] == list(fold.training_sessions)
                     and metadata["test_sessions"] == list(fold.test_sessions)
                     and metadata["first_test_opening"] == fold.first_test_opening.isoformat()
                     and metadata["target_first_train_rate"] == (None if rate is None else [rate.numerator, rate.denominator])
                     and (type(model) is dict and wire.valid_sha(replay["artifact_sha256"])
                          and model["artifact_sha256"] == replay["artifact_sha256"] and wire.valid_sha(replay["inputs_sha256"])
                          and model["training"]["target_first_false_true_rows"] == target if fitted else model is None),
                     "EG evaluation fold metadata differs")
        for row in candidates:
            raw = next(records, None)
            wire.require(type(raw) is dict, "EG evaluation fit candidate missing")
            score = fit._score(raw["prediction"], holding=bool(row.key[3]))
            eligible = row.eligible(coordinate.arm)
            wire.require((score is not None) == (fitted and eligible)
                         and raw == fit._candidate(row, score, eligible, saved["fit_status"]),
                         "EG evaluation fit candidate identity or support differs")
            counts[raw["status"]] += 1
            if score is not None:
                scored[row.key] = score
            flags = frozenset(ambiguity.get(row.key, ()))
            forecasts[coordinate.branch, coordinate.timing, coordinate.arm, row.key] = SavedForecast(
                coordinate.fold_index, raw["decision_at"], row.support, row.entry_support, raw["status"],
                raw["comparison_scope"], score, tuple(target), rate, flags)
        wire.require(next(records, None) is None and reader.footer["report"] == dict(counts) == saved["candidate_counts"],
                     "EG evaluation fit candidate footer differs")
    wire.require(type(replay["replayed_rows"]) is int and len(scored) == replay["replayed_rows"]
                 and (len(scored) > 0) == fitted and set(ambiguity) <= scored.keys()
                 and all((scored[key].hold_advantage_pct is not None if key[3] else scored[key].gross30_pct is not None)
                         for key in ambiguity)
                 and (not fitted or replay["target_first_clipped_rows"] == sum(x.target_first_clipped for x in scored.values())),
                 "EG evaluation fit replay candidate counts differ")
    return forecasts


def _skip_label(reader):
    # The predecessor already certified the ordered feature/label pairing.
    # Keep hashing the exact skipped bytes without exposing their values.
    reader.checkpoint("eg_evaluation_skip_outcome")
    raw = reader.zipped.readline(MAX_LINE + 1)
    wire.require(len(raw) <= MAX_LINE and raw.startswith(b'{"kind":"record","value":') and raw.endswith(b"}\n"),
                 "EG evaluation skipped outcome framing differs")
    reader.size += len(raw)
    wire.require(reader.size <= MAX_DECODED, "EG evaluation decoded outcome bound exceeded")
    reader.decoded.update(raw)


def _finish_labels(reader, report, records):
    footer = reader._line()
    seal = reader.seal
    wire.require(footer == {"kind": "footer", "records": records, "report": report}
                 and records == seal.records and reader.zipped.read(1) == b""
                 and reader.size == seal.decoded_bytes and reader.decoded.hexdigest() == seal.decoded_sha256,
                 "EG evaluation outcome footer or decoded seal differs")
    reader.count, reader.footer, reader.completed = records, footer, True


class EvaluationInputs:
    """Complete feature and forecast evidence before opening the outcome stream."""

    def __init__(self, directory, receipt, calendar, universe, keys, forecasts, checkpoint):
        self.source_dates = calendar.source_reference_sessions
        evaluation_dates = set(calendar.evaluation_reference_sessions)
        self.evaluation_keys = tuple(key for key in keys if key[0] in evaluation_dates)
        self.forecasts = MappingProxyType(forecasts)
        self._directory, self._receipt, self._universe, self._keys = directory, receipt, universe, keys
        self._checkpoint = checkpoint
        self._features_started = self._features_completed = self._labels_started = False

    def _stream(self, role):
        return open_snapshot(self._directory, role, fit._seal(self._receipt["seals"][role]), self._checkpoint)

    def _frames(self, reader):
        wire.require(reader.header["binding"] == self._receipt["binding"] and reader.header["universe"] == self._universe,
                     "EG evaluation sealed stream header differs")
        for expected, frame in zip(self._keys, reader.records(), strict=True):
            wire.require(_key(frame["key"]) == expected, "EG evaluation sealed stream event differs")
            yield expected, frame

    def features(self):
        wire.require(not self._features_started, "EG evaluation feature reader already used")
        self._features_started = True
        with self._stream("features") as reader:
            for _, frame in self._frames(reader):
                yield frame
        self._features_completed = True

    def entry_forecasts(self):
        result = {}
        for key, row in self.forecasts.items():
            if key[-1][3] or row.comparison_scope is None:
                continue
            score = row.prediction
            result[key] = EntryForecast(None if score is None else score.gross30_pct,
                None if score is None else score.target_first_probability, row.training_target_rate,
                frozenset(cost for cost in (33, 43) if f"gross{cost}" in row.replay_ambiguities),
                row.status if score is None else None)
        return result

    def holding_forecasts(self):
        result = {}
        for key, row in self.forecasts.items():
            if not key[-1][3] or row.comparison_scope is None:
                continue
            advantage = None if row.prediction is None else row.prediction.hold_advantage_pct
            result[key] = HoldingForecast(advantage, "hold_zero" in row.replay_ambiguities,
                                          row.status if advantage is None else None)
        return result

    @contextmanager
    def open_labels(self, prospective_keys):
        """Yield a monotonic exact-key callback after the caller freezes decisions."""
        wire.require(self._features_completed and not self._labels_started and type(prospective_keys) is tuple,
                     "EG evaluation requires complete prospective features and frozen label keys")
        keys = tuple(_key(key) for key in prospective_keys)
        allowed = set(keys)
        wire.require(keys == tuple(sorted(allowed, key=_order)) and allowed <= set(self.evaluation_keys),
                     "EG evaluation prospective label keys differ")
        self._labels_started = True
        with self._stream("labels") as reader:
            wire.require(reader.header["binding"] == self._receipt["binding"]
                         and reader.header["universe"] == self._universe, "EG evaluation outcome header differs")
            remaining = iter(self._keys)
            previous = None

            def labels_at(wanted):
                nonlocal previous
                wire.require(wanted in allowed, "EG evaluation outcome key was not registered prospectively")
                wire.require(previous is None or _order(previous) < _order(wanted),
                             "EG evaluation outcome key is not monotonic")
                for key in remaining:
                    if key == wanted:
                        record = reader._line()
                        wire.require(type(record) is dict and set(record) == {"kind", "value"}
                                     and record["kind"] == "record" and type(record["value"]) is dict
                                     and _key(record["value"]["key"]) == key, "EG evaluation outcome record differs")
                        previous = key
                        return record["value"]
                    wire.require(_order(key) < _order(wanted), "EG evaluation outcome key missing")
                    _skip_label(reader)
                raise ValueError("EG evaluation outcome key missing")

            yield labels_at
            for _ in remaining:
                _skip_label(reader)
            _finish_labels(reader, self._receipt["report"], len(self._keys))


def open_evaluation_inputs(snapshot_pin: PredecessorPin, fit_pins: tuple[PredecessorPin, ...], *,
                           invented_only: bool, checkpoint) -> EvaluationInputs:
    """Authenticate the exact six fit units and matching split scenario snapshot."""
    wire.require(type(invented_only) is bool and type(fit_pins) is tuple and len(fit_pins) == len(fit_unit.FIT_IDS)
                 and all(type(pin) is PredecessorPin for pin in fit_pins)
                 and {pin.unit_id for pin in fit_pins} == set(fit_unit.FIT_IDS), "EG evaluation fit predecessor roster differs")
    snapshot_dir = _pin(snapshot_pin, snapshot.UNIT, invented_only)
    pins = {pin.unit_id: pin for pin in fit_pins}
    fit_dirs = {name: _pin(pins[name], name, invented_only) for name in fit_unit.FIT_IDS}
    receipt = fit_unit._predecessor(snapshot_dir, invented_only, snapshot_pin.attempt, snapshot_pin.audit_sha256,
                                    snapshot_pin.merge_commit, checkpoint)
    calendar, universe, keys, by_cell = _feature_roster(snapshot_dir, receipt, checkpoint)
    forecasts = {}
    for unit_id in fit_unit.FIT_IDS:
        directory = fit_dirs[unit_id]
        saved, proof = _fit_predecessor(directory, pins[unit_id], invented_only, receipt, checkpoint)
        for coordinate, cell, replay in zip(fit.fit_roster(unit_id), saved["coordinates"], proof["coordinates"], strict=True):
            data = _fold_forecasts(directory, coordinate, cell, replay, saved, calendar,
                                   by_cell[coordinate.branch, coordinate.timing, coordinate.fold_index], checkpoint)
            wire.require(not forecasts.keys() & data.keys(), "EG evaluation duplicate fit forecast key")
            forecasts.update(data)
    return EvaluationInputs(snapshot_dir, receipt, calendar, universe, keys, forecasts, checkpoint)
