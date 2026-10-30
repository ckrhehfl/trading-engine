"""Independent complete-unit verification for the fixed EC P09/P10 selectors.

Raw source reconstruction, causal feature arithmetic, original label arithmetic,
fold membership and cash states precede all output comparisons. Numerical model
verification replays the one serialized fit; it never trains another model.
This pure module has no files, network, experiment logger or trading boundary.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
import json
import math
from types import MappingProxyType

from research import intraday_campaign_feature_verifier as feature_oracle
from research import intraday_campaign_h45_account_verifier as raw_oracle
from research import intraday_campaign_independent as account_oracle
from research import intraday_campaign_ml_label_verifier as label_oracle
from research import intraday_campaign_ml_labels as labels
from research import intraday_campaign_ml_models as models
from research.intraday_campaign_context_readiness import ReadinessInputs
from research.intraday_context_experiment import PRICE_BASIS
from research import intraday_context_preparation as ea
from research import intraday_pattern_calibration as cc
from research.intraday_pattern_events import Bar


VerificationError = account_oracle.VerificationError
POLICIES = ("D+1", "D+2")
ARMS = ("control", "selected")
LABELS = ("net_pnl", "gross_pnl_same_fills")
LEARNERS = {"P09": "ridge", "P10": "lightgbm"}
_equal, _plain = account_oracle._equal, account_oracle._plain


def _need(condition, reason):
    if not condition:
        raise VerificationError(reason)


def _immutable_projection(value, depth=0):
    """Retain the official recursively frozen projection without copying it.

    project_readiness_inputs creates each proxy over a newly owned dictionary.
    Only those recursively read-only shapes and exact immutable scalar types
    are accepted here; a mutable caller tree must use that projection first.
    No whole-tree allocation or persistent object-id cache is needed.
    """
    _need(depth <= 64, "immutable projection nesting exceeded")
    kind = type(value)
    if kind is MappingProxyType:
        for key, item in value.items():
            _need(type(key) is str, "immutable projection string keys required")
            _immutable_projection(item, depth + 1)
    elif kind is tuple:
        for item in value:
            _immutable_projection(item, depth + 1)
    else:
        _need(kind in (str, int, bool, float, Fraction, datetime, date, type(None)),
              "deeply immutable original projection required")
        _need(kind is not float or math.isfinite(value), "nonfinite original projection")
    return value


def _typed(value):
    if type(value) is Fraction:
        return ["fraction", str(value.numerator), str(value.denominator)]
    if type(value) in (datetime, date):
        return [type(value).__name__, value.isoformat()]
    if type(value) is float:
        _need(math.isfinite(value), "nonfinite numerical assertion")
        return ["float64", value.hex()]
    if isinstance(value, Mapping):
        _need(all(type(key) is str for key in value), "typed mapping requires string keys")
        return ["mapping", [[key, _typed(value[key])] for key in sorted(value)]]
    if type(value) in (tuple, list):
        return ["sequence", [_typed(item) for item in value]]
    _need(type(value) in (str, int, bool, type(None)), "unsupported typed scalar")
    return [type(value).__name__, value]


def typed_sha256(value):
    """Stream the original typed JSON bytes with no second whole object tree.

    A dataclass has precisely the mapping representation produced by _plain.
    Scalar strings use JSON's ensure_ascii=False escaping; floating values keep
    their finite binary64 hex encoding, including the sign of zero.
    """
    digest, pending = hashlib.sha256(), bytearray()

    def emit(text):
        raw = text.encode("utf-8")
        if len(raw) >= 65536:
            if pending:
                digest.update(pending)
                pending.clear()
            digest.update(raw)
        else:
            pending.extend(raw)
            if len(pending) >= 65536:
                digest.update(pending)
                pending.clear()

    def walk(item):
        kind = type(item)
        if kind is Fraction:
            emit(f'["fraction","{item.numerator}","{item.denominator}"]')
        elif kind in (datetime, date):
            emit(f'["{kind.__name__}","{item.isoformat()}"]')
        elif kind is float:
            _need(math.isfinite(item), "nonfinite numerical assertion")
            emit(f'["float64","{item.hex()}"]')
        elif isinstance(item, Mapping) or (is_dataclass(item) and not isinstance(item, type)):
            if isinstance(item, Mapping):
                _need(all(type(key) is str for key in item), "typed mapping requires string keys")
                names, get = sorted(item), item.__getitem__
            else:
                names = sorted(field.name for field in fields(item))
                get = lambda name: getattr(item, name)
            emit('["mapping",[')
            for index, name in enumerate(names):
                if index:
                    emit(',')
                emit('[' + json.encoder.encode_basestring(name) + ',')
                walk(get(name))
                emit(']')
            emit(']]')
        elif kind in (tuple, list):
            emit('["sequence",[')
            for index, child in enumerate(item):
                if index:
                    emit(',')
                walk(child)
            emit(']]')
        elif kind is str:
            emit('["str",' + json.encoder.encode_basestring(item) + ']')
        elif kind is int:
            emit('["int",' + str(item) + ']')
        elif kind is bool:
            emit('["bool",' + ('true' if item else 'false') + ']')
        elif item is None:
            emit('["NoneType",null]')
        else:
            raise VerificationError("unsupported typed scalar")

    walk(value)
    if pending:
        digest.update(pending)
    return digest.hexdigest()


def fit_binding(policy_id, view, numerical_environment):
    """Bind training and evaluation features without any evaluation labels."""
    _need(policy_id in LEARNERS and type(view) is labels.FoldTrainingView,
          "registered policy and fold view required")
    _need(isinstance(numerical_environment, Mapping) and bool(numerical_environment),
          "pinned numerical environment required")
    return {"schema": "ec-ml-fold-binding-v1", "policy_id": policy_id,
        "learner": LEARNERS[policy_id], "timing_policy": view.timing_policy,
        "fold_index": view.fold_index, "cutoff": view.cutoff.isoformat(),
        "feature_order": list(view.feature_order),
        "train_row_keys_sha256": typed_sha256(view.train_row_keys),
        "train_features_exact_sha256": typed_sha256(view.X),
        "train_labels_exact_sha256": typed_sha256(view.y),
        "train_sessions_sha256": typed_sha256(view.train_sessions),
        "test_row_keys_sha256": typed_sha256(view.test_row_keys),
        "test_features_exact_sha256": typed_sha256(view.test_X),
        "numerical_environment": _plain(numerical_environment)}


class MLVerifier:
    def __init__(self, policy_id, a, b, label_a, label_b, *, authentication_reference,
                 calendar, numerical_environment):
        _need(policy_id in LEARNERS and type(authentication_reference) is str
              and bool(authentication_reference.strip()), "registered ML unit required")
        self.policy_id, self.authentication_reference = policy_id, authentication_reference
        self.calendar = calendar
        self.numerical_environment = _plain(numerical_environment)
        _need(bool(self.numerical_environment), "numerical environment identity required")
        self.originals = {}
        self.label_rows = {}
        for value, projection, setup in ((a, label_a, "EA-A"), (b, label_b, "EA-B")):
            _need(type(value) is ReadinessInputs and value.setup_id == setup
                  and type(projection) is labels.MLLabelProjection and projection.setup_id == setup,
                  "original immutable projections required")
            self.originals[setup] = {name: _immutable_projection(getattr(value, name)) for name in
                ("account_projection", "target_rows", "request_metadata")}
            _equal(set(projection.rows_by_policy), set(POLICIES), "label.timing_policies")
            for timing, rows in projection.rows_by_policy.items():
                original = tuple(source for source in value.target_rows[timing] if source["signal_state"] == "signal")
                _need(len(rows) == len(original), "original label population changed")
                for row, source in zip(rows, original, strict=True):
                    _need(type(row) is labels.OriginalLabelRow, "original signal label row required")
                    key = (timing, setup, source["original_row_index"], source["session"], source["symbol"])
                    _equal(row.row_key, key, "label.original_row_key")
                    _equal(row.signal["state"], source["signal_state"], "label.original_signal")
                    _equal(row.signal["reason"], source["signal_reason"], "label.original_signal_reason")
                    for name in ("decision_at", "observed_through", "signal_bar_start", "signal_bar_end", "setup_bar_end"):
                        _equal(row.signal[name], datetime.fromisoformat(source[name]) if source[name] is not None else None,
                               f"label.original_signal.{name}")
                    for name in ("structural_stop", "membership_day_index", "membership_target", "source_index", "volume_source_index"):
                        _equal(getattr(row, name), source[name], f"label.original.{name}")
                    index = source["source_index"]
                    sources = value.account_projection["sources"]
                    _need(type(index) is int and 0 <= index < len(sources), "original label source index changed")
                    _equal(row.source, sources[index], "label.original_source")
                    opening = raw_oracle._opening(source["session"])
                    entry = opening + ((row.signal["decision_at"] - opening) // (5 * cc.MINUTE) + 1) * 5 * cc.MINUTE
                    _equal((row.entry_at, row.cap_at), (entry, entry + 60 * cc.MINUTE), "label.original_plan_clocks")
                    _need(row.stored_outcome is not None, "original signal outcome required")
                    for tree in (row.signal, row.source, row.membership_target, row.stored_outcome):
                        _immutable_projection(tree)
                    _need(key not in self.label_rows, "duplicate original label row")
                    self.label_rows[key] = row
        self.account = self.originals["EA-A"]["account_projection"]
        for name in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources"):
            _equal(self.originals["EA-B"]["account_projection"][name], self.account[name], f"original.{name}")
        _equal(calendar, labels.fixed_calendar(self.account["reference_sessions"]), "original.fixed_calendar")
        for projection in (label_a, label_b):
            _equal(projection.source_reference_sessions, calendar.source_reference_sessions, "label.source_calendar")
        self.source_reference = list(calendar.source_reference_sessions)
        self.reference = list(calendar.evaluation_reference_sessions)
        self.weeks = list(calendar.evaluation_reference_weeks)
        self.sources = self.account["sources"]
        self.source_by_day = {day: {} for day in self.source_reference}
        for source in self.sources:
            day, symbol = source["session"], source["symbol"]
            _need(day in self.source_by_day and symbol not in self.source_by_day[day], "duplicate or extra source")
            self.source_by_day[day][symbol] = source
        self.rows = {(timing, day): [(setup, row) for setup in ("EA-A", "EA-B")
                     for row in self.originals[setup]["target_rows"][timing] if row["session"] == day]
                     for timing in POLICIES for day in self.source_reference}
        self.signal_keys_by_day = {day: tuple((timing, setup, row["original_row_index"], day, row["symbol"])
            for timing in POLICIES for setup, row in self.rows[timing, day] if row["signal_state"] == "signal")
            for day in self.source_reference}
        self.features, self.validated_labels = {}, {}
        self.fit_records, self.fit_receipts, self.predictions = {}, {}, {}
        self._states, self._days, self._plans = {}, {}, {}
        self._visited = {1: [], 2: []}
        self._receipts = {1: [], 2: []}
        self._source_pass_receipts = []
        self._active = self._raw_day = self._day_input_identity = None
        self._batch_labels = None
        self._feature_cache = None
        self._day_features, self._day_callbacks = set(), set()
        self._selection_cache, self._plan_cache = {}, {}
        self._features_closed = self._fits_closed = self._closed = False

    @property
    def source_pass_receipts(self):
        return _plain(self._source_pass_receipts)

    def observe_source_day(self, day_input, *, pass_index):
        _need(not self._closed and pass_index in (1, 2), "source pass identity changed")
        if self._active is not None:
            _need(self._active == (pass_index, day_input["session"])
                  and self._day_input_identity == id(day_input), "previous raw day not released")
            return
        if pass_index == 2:
            _need(self._fits_closed, "second source pass precedes verified predictions")
        else:
            _need(not self._features_closed, "first source pass already closed")
        index = len(self._visited[pass_index])
        _need(index < len(self.source_reference) and day_input["session"] == self.source_reference[index],
              "source day order changed")
        day = day_input["session"]
        expected = self.source_by_day[day]
        _equal(set(day_input["original_chunks"]), set(expected), "raw.original_chunks")
        _equal(day_input["original_sources"], expected, "raw.original_sources")
        _equal(set(day_input["original_sessions"]), set(expected), "raw.original_sessions")
        _equal(day_input["sources"], [expected[symbol] for symbol in sorted(expected)], "raw.sources")
        _equal(day_input["session_open"], raw_oracle._opening(day), "raw.opening")
        _equal(day_input["session_close"], raw_oracle._closing(day), "raw.closing")
        _equal(day_input["retained_price_symbols"], sorted(expected), "raw.full_target_prices")
        _equal(day_input["consumed_target_chunks"], len(expected), "raw.chunk_count")
        _equal(day_input["consumed_price_rows"], sum(row["input_rows"] for row in expected.values()), "raw.row_count")
        _equal(day_input["original_union_complete"], index == len(self.source_reference)-1, "raw.EOF")
        minutes, opens, sessions = {}, {}, {}
        for symbol in sorted(expected):
            raw = raw_oracle._raw_session(day_input["original_chunks"][symbol], day, symbol)
            _equal(raw["source"], expected[symbol], "raw.source_receipt")
            session = day_input["original_sessions"][symbol]
            _need(type(session) is cc.Session, "original Session required")
            for actual, key in ((session.opening, "opening"), (session.minutes, "minutes"),
                                (session.prefix, "bars"), (session.gaps, "gaps"),
                                (session.source_offsets_ms, "offsets")):
                _equal(actual, raw[key], f"raw.Session.{key}")
            minutes.update({(symbol, at): values for at, values in raw["minutes"].items()})
            opens.update({(symbol, at): value for at, value in raw["opens"].items()})
            sessions[symbol] = cc.Session(raw["opening"], raw["minutes"],
                tuple(Bar(**bar) for bar in raw["bars"]), raw["gaps"], raw["offsets"])
        supplied_minutes = day_input["minute_bars"]
        _need(isinstance(supplied_minutes, Mapping) and supplied_minutes.keys() == minutes.keys(),
              "raw.minute_bars: coordinate set differs")
        for (symbol, at), value in supplied_minutes.items():
            # Session values above have already been independently reconstructed.
            # Reuse equality only for identical immutable tuple/None objects;
            # distinct but equal tuples retain the original numeric comparison.
            checked = day_input["original_sessions"][symbol].minutes[at]
            if value is not checked or (value is not None and type(value) is not tuple):
                _equal(value, checked, "raw.minute_bars")
        _equal(day_input["open_prices"], opens, "raw.unique_OPEN")
        receipt = {"session": day, "sha256": typed_sha256({"session": day,
            "opening": day_input["session_open"], "closing": day_input["session_close"],
            "sources": [expected[symbol] for symbol in sorted(expected)],
            "original_union_complete": index == len(self.source_reference)-1})}
        if pass_index == 2:
            _equal(receipt, self._receipts[1][index], "two_pass_source_identity")
        self._receipts[pass_index].append(receipt)
        self._visited[pass_index].append(day)
        self._raw_day = {"session": day, "session_open": day_input["session_open"],
            "session_close": day_input["session_close"], "minute_bars": minutes, "open_prices": opens,
            "original_sessions": sessions, "original_sources": expected,
            "original_chunks": day_input["original_chunks"]}
        self._active, self._day_input_identity = (pass_index, day), id(day_input)
        self._day_features, self._day_callbacks = set(), set()
        self._selection_cache, self._plan_cache = {}, {}
        self._feature_cache = None

    def finish_source_day(self, *, pass_index):
        _need(self._active is not None and self._active[0] == pass_index, "source day not active")
        day = self._active[1]
        if pass_index == 1:
            _equal(self._day_features, set(self.signal_keys_by_day[day]), "day.original_signal_coverage")
        else:
            _need(len(self._day_callbacks) == (16 if day in self.reference else 0), "account day callbacks incomplete")
        self._raw_day = self._active = self._day_input_identity = self._batch_labels = None
        self._feature_cache = None
        self._selection_cache, self._plan_cache = {}, {}
        if len(self._visited[pass_index]) == len(self.source_reference):
            self._source_pass_receipts.append({"pass_index": pass_index, "source_sessions": len(self.source_reference),
                "source_chunks": len(self.sources), "price_rows": sum(row["input_rows"] for row in self.sources),
                "day_receipt_sha256": list(self._receipts[pass_index])})

    def _arguments(self, timing, setup, original):
        day = original["session"]
        index = original["membership_day_index"]
        _need(type(index) is int and self.source_reference[index] == day, "original membership index changed")
        membership = self.account["source_target_matrix"][timing]["days"][index]
        _need(original["membership_target"] in membership["targets"], "original target changed")
        source = self.sources[original["source_index"]]
        _equal((source["session"], source["symbol"]), (day, original["symbol"]), "original.source_identity")
        raw = source["raw_chunk_metadata"]
        observed = datetime.combine(date.fromisoformat(membership["observation_date"]), datetime.min.time(), cc.KST)
        observed += timedelta(days=1, microseconds=-1)
        provenance = ea.Provenance(observed, datetime.fromisoformat(membership["available_at"]),
            max(datetime.fromisoformat(value) for value in membership["retrieved_at"].values()),
            membership["source"], membership["is_final"],
            hashlib.sha256(feature_oracle._json_bytes(_plain(membership), "original.membership")).hexdigest(), "assumed")
        universe = ea.MonthlyUniverse(date.fromisoformat(day).replace(day=1),
            tuple(member["symbol"] for member in membership["targets"]),
            datetime.fromisoformat(membership["selected_at"]), provenance)
        target = ea.Target(original["symbol"], date.fromisoformat(day), timing,
            raw_oracle._opening(day), raw_oracle._closing(day), PRICE_BASIS,
            ea.PriceSource(datetime.fromisoformat(raw["retrieved_at"]), raw["source"], raw["is_final"], source["data_vintage"], "assumed"))
        if self._feature_cache is None:
            self._feature_cache = feature_oracle.FeatureVerifierDayCache(opening=target.opening, closing=target.closing,
                sessions=self._raw_day["original_sessions"], sources=self._raw_day["original_sources"])
        return {"target": target, "universe": universe, "sessions": self._feature_cache.sessions,
            "sources": self._feature_cache.sources, "setup_id": setup,
            "observed_end": datetime.fromisoformat(original["observed_through"]),
            "decision_at": datetime.fromisoformat(original["decision_at"]),
            "structural_stop": original["structural_stop"], "inputs_authenticated": True,
            "authentication_reference": self.authentication_reference, "price_unit": "KRW", "day_cache": self._feature_cache}

    def prepare_day_labels(self, day_input, producer_outcomes):
        self.observe_source_day(day_input, pass_index=1)
        _need(self._batch_labels is None and not self._day_features, "day labels already prepared or consumed")
        rows = tuple(self.label_rows[key] for key in self.signal_keys_by_day[day_input["session"]])
        self._batch_labels = label_oracle.verify_labels_for_day(self._raw_day, rows, producer_outcomes,
                                                               inputs_authenticated=True)
        return self._batch_labels

    def verify_feature_row(self, day_input, timing_policy, setup_id, original_row, prepared, vector, producer_label):
        self.observe_source_day(day_input, pass_index=1)
        key = (timing_policy, setup_id, original_row["original_row_index"], original_row["session"], original_row["symbol"])
        _need(key in self.signal_keys_by_day[day_input["session"]] and key not in self.features,
              "duplicate or extra original signal feature")
        expected = next(row for setup, row in self.rows[timing_policy, day_input["session"]]
                        if setup == setup_id and row["original_row_index"] == key[2])
        _equal(original_row, expected, "feature.original_row")
        arguments = self._arguments(timing_policy, setup_id, expected)
        feature_oracle.verify_features(prepared, **arguments)
        exact = tuple(prepared.features[name].value for name in models.FEATURE_NAMES)
        _need(type(vector) is tuple and all(value is None or type(value) is Fraction for value in vector),
              "exact original feature vector required")
        _equal(vector, exact, "feature.vector")
        if self._batch_labels is None:
            validated = label_oracle.verify_label_for_row(self._raw_day, self.label_rows[key], producer_label,
                                                         inputs_authenticated=True)
        else:
            validated = self._batch_labels[key]
            _equal(producer_label, validated.outcome, "feature.batch_original_outcome")
        self.features[key], self.validated_labels[key] = exact, validated
        self._day_features.add(key)
        return validated

    def finish_features(self, feature_rows, validated_labels):
        _need(not self._features_closed and self._active is None, "feature source pass incomplete")
        _equal(self._visited[1], self.source_reference, "feature.all_source_dates")
        expected_keys = {key for day in self.source_reference for key in self.signal_keys_by_day[day]}
        _equal(set(self.features), expected_keys, "feature.complete_original_signals")
        _equal(list(feature_rows), list(self.features), "feature.original_order")
        _equal(feature_rows, self.features, "feature.exact_rows")
        _equal(validated_labels, self.validated_labels, "label.exact_rows")
        self._features_closed = True
        return {"verified": True, "original_signal_rows": len(expected_keys),
                "features_sha256": typed_sha256([[key, value] for key, value in self.features.items()]),
                "labels_sha256": typed_sha256(tuple(self.validated_labels.values()))}

    def verify_fit_record(self, record):
        _need(self._features_closed and not self._fits_closed, "fit verification ordering changed")
        _need(isinstance(record, Mapping) and set(record) == {"timing_policy", "fold_index", "view", "fit_status", "reason", "artifact", "predictions"},
              "fit record fields changed")
        slot = record["timing_policy"], record["fold_index"]
        _need(slot[0] in POLICIES and type(slot[1]) is int and 1 <= slot[1] <= 5 and slot not in self.fit_records,
              "duplicate or extra fit slot")
        view = record["view"]
        _equal((view.timing_policy, view.fold_index), slot, "fit.slot_view")
        label_oracle.verify_fold_training_view(view, self.calendar, self.features, self.validated_labels)
        if view.status == "unavailable":
            _equal((record["fit_status"], record["reason"], record["artifact"], record["predictions"]),
                   ("unavailable", view.reason, None, ()), "fit.unavailable_slot")
            receipt = {"verified": True, "status": "unavailable", "reason": view.reason}
            for key in view.test_row_keys:
                self.predictions[key] = (slot[1], "unavailable", None)
        else:
            _equal((record["fit_status"], record["reason"]), ("trained", None), "fit.trained_slot")
            artifact = record["artifact"]
            _equal(artifact["learner"], LEARNERS[self.policy_id], "fit.learner")
            receipt = models.verify_model(artifact, tuple(tuple(map(float, row)) for row in view.X),
                tuple(map(float, view.y)), tuple(tuple(map(float, row)) for row in view.test_X),
                record["predictions"], feature_names=models.FEATURE_NAMES,
                binding=fit_binding(self.policy_id, view, self.numerical_environment))
            for key, prediction in zip(view.test_row_keys, record["predictions"], strict=True):
                _need(type(prediction) is float and math.isfinite(prediction), "finite prediction required")
                self.predictions[key] = (slot[1], "trained", prediction)
        # The full model and repeated training matrices stay only in the report.
        # Their verified digest guards later producer mutation without a clone.
        self.fit_records[slot] = {"timing_policy": slot[0], "fold_index": slot[1],
            "fit_status": record["fit_status"], "reason": record["reason"],
            "record_sha256": typed_sha256(record)}
        self.fit_receipts[slot] = _plain(receipt)
        return receipt

    def _verify_retained_fit_records(self, records):
        slots = [(timing, fold) for timing in POLICIES for fold in range(1, 6)]
        _need(type(records) in (tuple, list) and len(records) == len(slots), "all_ten_retained_fit_records_required")
        for record, slot in zip(records, slots, strict=True):
            _equal((record["timing_policy"], record["fold_index"]), slot, "report.fit_record_order")
            _need(typed_sha256(record) == self.fit_records[slot]["record_sha256"],
                  "serialized fit/prediction exact identity changed")

    def bind_prediction_map(self):
        _need(self._features_closed and not self._fits_closed, "prediction binding ordering changed")
        _equal(set(self.fit_records), {(timing, fold) for timing in POLICIES for fold in range(1, 6)}, "all_ten_fit_slots")
        expected = {key for key, vector in self.features.items() if key[3] in self.reference and None not in vector}
        _equal(set(self.predictions), expected, "complete_prediction_population")
        self._fits_closed = True
        return dict(self.predictions)

    def fold_status(self, timing, session):
        _need(self._fits_closed and timing in POLICIES and session in self.reference, "evaluation fold required")
        fold = next(fold for fold in self.calendar.folds if session in fold.test_sessions)
        return fold.index, self.fit_records[timing, fold.index]["fit_status"]

    def expected_selection_row(self, timing, setup, original):
        key = (timing, setup, original["original_row_index"], original["session"], original["symbol"])
        _need(timing in POLICIES and setup in ("EA-A", "EA-B") and original["session"] in self.reference,
              "original evaluation row required")
        expected = next(row for own_setup, row in self.rows[timing, original["session"]]
                        if own_setup == setup and row["original_row_index"] == key[2])
        _equal(original, expected, "selection.original_row")
        cache = self._active == (2, original["session"])
        if cache and key in self._selection_cache:
            return dict(self._selection_cache[key])
        fold, _ = self.fold_status(timing, original["session"])
        row = {"timing_policy": timing, "setup_id": setup, "original_row_index": key[2],
            "session": key[3], "symbol": key[4], "signal_state": original["signal_state"],
            "feature_state": "absent", "fold_index": fold, "model_status": "absent", "prediction_hex": None,
            "state": "absent", "reason": original["signal_reason"], "clock_eligible": None,
            "entry_at": None, "cap_exit_at": None,
            "decision_at": datetime.fromisoformat(original["decision_at"]) if original["decision_at"] is not None else None}
        if original["signal_state"] == "signal":
            _need(key in self.label_rows, "original signal label missing")
            label_row = self.label_rows[key]
            decision, entry, cap = label_row.signal["decision_at"], label_row.entry_at, label_row.cap_at
            eligible = raw_oracle._opening(key[3]) < decision < entry < cap < raw_oracle._closing(key[3])
            row.update(decision_at=decision, entry_at=entry, cap_exit_at=cap, clock_eligible=eligible)
            observed = None not in self.features[key]
            row.update(feature_state="observed" if observed else "unknown", state="unknown", reason="feature_unknown")
            if observed:
                _, status, prediction = self.predictions[key]
                row.update(model_status=status, reason="model_unavailable")
                if status == "trained":
                    row.update(prediction_hex=prediction.hex(), state="selected" if prediction > 0 else "rejected",
                               reason="positive_prediction" if prediction > 0 else "nonpositive_prediction")
        if cache:
            self._selection_cache[key] = MappingProxyType(dict(row))
        return row

    def _plans_and_coverage(self, timing, session, arm):
        cache_key = timing, session, arm
        cache = self._active == (2, session)
        if cache and cache_key in self._plan_cache:
            plans, coverage = self._plan_cache[cache_key]
            return [dict(plan) for plan in plans], _plain(coverage)
        rows, plans, counts = [], [], {}
        for setup in ("EA-A", "EA-B"):
            own = [self.expected_selection_row(timing, setup, original) for own_setup, original in self.rows[timing, session]
                   if own_setup == setup]
            rows.extend(own)
            counts[setup] = {"targets": len(own), "signals": sum(row["signal_state"] == "signal" for row in own),
                "known_no_signal": sum(row["signal_state"] == "known_no_signal" for row in own),
                "unknown_signals": sum(row["signal_state"] == "unknown" for row in own),
                "clock_ineligible": sum(row["clock_eligible"] is False for row in own),
                "feature_unknown": sum(row["feature_state"] == "unknown" for row in own),
                "support_observed": sum(row["feature_state"] == "observed" for row in own)}
        control = 0
        for row in rows:
            if row["feature_state"] != "observed" or not row["clock_eligible"]:
                continue
            control += 1
            if arm == "control" or row["state"] == "selected":
                key = (timing, row["setup_id"], row["original_row_index"], session, row["symbol"])
                plans.append({"strategy": row["setup_id"][-1], "symbol": row["symbol"],
                    "decision_at": row["decision_at"], "entry_at": row["entry_at"], "cap_exit_at": row["cap_exit_at"],
                    "structural_stop": self.label_rows[key].structural_stop})
        plans.sort(key=lambda row: (row["decision_at"], row["symbol"], row["strategy"]))
        coverage = {"by_setup": counts, "unknown_signal_units": sum(row["unknown_signals"] for row in counts.values()),
            "clock_ineligible_units": sum(row["clock_ineligible"] for row in counts.values()),
            "recorded_signal_units": sum(row["signals"] for row in counts.values()),
            "unknown_feature_units": sum(row["feature_unknown"] for row in counts.values()),
            "eligible_control_candidates": control, "eligible_arm_candidates": len(plans),
            "model_unavailable_units": sum(row["model_status"] == "unavailable" for row in rows),
            "model_rejected_units": sum(row["state"] == "rejected" for row in rows),
            "model_selected_units": sum(row["state"] == "selected" for row in rows)}
        if cache:
            nested = MappingProxyType({setup: MappingProxyType(dict(values)) for setup, values in counts.items()})
            frozen = MappingProxyType({**coverage, "by_setup": nested})
            self._plan_cache[cache_key] = tuple(MappingProxyType(dict(plan)) for plan in plans), frozen
        return plans, coverage

    def verify_day(self, arm, day_input, candidates, initial_state, result, compact, scenario):
        self.observe_source_day(day_input, pass_index=2)
        _need(arm in ARMS and day_input["session"] in self.reference, "evaluation account arm required")
        _need(isinstance(scenario, Mapping) and set(scenario) == {"timing_policy", "cost_bps", "mode", "risk_scope", "compress_idle_minutes"},
              "account scenario fields changed")
        timing, cost, mode, scope = (scenario[name] for name in ("timing_policy", "cost_bps", "mode", "risk_scope"))
        _need(timing in POLICIES and type(cost) is int and cost in (33, 43) and mode == "A+B"
              and scope in account_oracle.SCOPES and scenario["compress_idle_minutes"] is True, "registered ML account scenario required")
        key = arm, timing, cost, scope
        _need(key not in self._day_callbacks, "duplicate ML account callback")
        session = day_input["session"]
        plans, coverage = self._plans_and_coverage(timing, session, arm)
        _equal(candidates, plans, "account.original_admission")
        initial = (self._states.get(key, account_oracle._fresh(mode, cost, scope)) if scope == "continuous"
                   else account_oracle._fresh(mode, cost, scope))
        # Own a copy before adding this day's policy-unknown marker; never mutate
        # the previous terminal state retained in a compact account output.
        from copy import deepcopy
        initial = deepcopy(initial)
        fold, status = self.fold_status(timing, session)
        unavailable = arm == "selected" and status == "unavailable"
        if unavailable:
            account_oracle._freeze(initial, f"policy_unavailable:{self.policy_id}:{timing}:fold{fold}", day_input["session_open"])
        _equal(initial_state, initial, "account.independent_initial_state")
        state, events, snapshots, closed = account_oracle._reconstruct(self._raw_day, plans, initial, compress_idle_minutes=True)
        _equal(result.state, state, "account.state")
        _equal(result.events, events, "account.events")
        _equal(result.snapshots, snapshots, "account.snapshots")
        _equal(result.scenario, "fresh_daily_counterfactual_no_continuous_weekly_monthly_claim" if scope == "day_local_counterfactual"
               else "observed_candidates_conditional_on_archive_availability", "account.scenario")
        expected = account_oracle._compact(self._raw_day, state, events, snapshots, closed, coverage)
        expected["policy_state"] = "unavailable_model" if unavailable else "available"
        _equal(compact, expected, "account.compact")
        self._states[key] = state
        self._days.setdefault(key, []).append(expected)
        self._plans.setdefault(key, {})[session] = plans
        self._day_callbacks.add(key)
        return {"verified": True, "session": session, "arm": arm, "events": len(events)}

    def finish_verify(self, report):
        _need(not self._closed and self._fits_closed and self._active is None, "complete ML verification required")
        for number in (1, 2):
            _equal(self._visited[number], self.source_reference, f"source_pass{number}.complete257")
        _equal(len(self.source_pass_receipts), 2, "two_complete_source_pass_receipts")
        fields = {"mode": "ec-fixed-ml-selector-cash-account", "policy_id": self.policy_id,
            "authentication_reference": self.authentication_reference,
            "source_reference_sessions": self.source_reference, "source_reference_weeks": list(self.calendar.source_reference_weeks),
            "evaluation_reference_sessions": self.reference, "evaluation_reference_weeks": self.weeks,
            "evaluation_week_session_counts": self.calendar.evaluation_week_session_counts,
            "boundary_week_flags": self.calendar.evaluation_boundary_week_flags,
            "reference_sessions": self.reference, "reference_weeks": self.weeks,
            "initial_capital": account_oracle.CAPITAL, "source_passes": 2,
            "source_pass_receipts": self.source_pass_receipts, "consumed_reference_sessions": len(self.source_reference),
            "consumed_target_chunks": len(self.sources), "consumed_price_rows": sum(row["input_rows"] for row in self.sources),
            "original_union_complete": True, "day_replay_count": len(self.reference) * 16,
            "numerical_environment": self.numerical_environment,
            "idle_minutes_compressed": True, "performance_computed": True,
            "strategy_pass": False, "promotion_allowed": False, "historical_brokerage_account_claim": False,
            "historical_timing_certified": False, "corporate_action_units_certified": False,
            "volume_availability_reauthenticated": False, "policy_results_not_pooled": True,
            "actual_execution_authorized": False, "untouched_validation_claim": False,
            "original_requests": {setup: original["request_metadata"] for setup, original in self.originals.items()},
            **{name: self.account[name] for name in ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")}}
        for name, expected in fields.items():
            _equal(report[name], expected, f"report.{name}")
        _equal(report["feature_rows"], [{"row_key": key, "values": value} for key, value in self.features.items()], "report.features")
        _equal(report["label_rows"], list(self.validated_labels.values()), "report.labels")
        self._verify_retained_fit_records(report["fit_records"])
        _equal(set(report["selection_rows"]), set(POLICIES), "report.selection_timing")
        for timing in POLICIES:
            expected = [self.expected_selection_row(timing, setup, original) for day in self.reference
                        for setup, original in self.rows[timing, day]]
            _equal(report["selection_rows"][timing], expected, "report.all_original_evaluation_targets")
        expected_keys = {(arm, timing, cost, scope) for arm in ARMS for timing in POLICIES
                         for cost in (33, 43) for scope in account_oracle.SCOPES}
        _equal(set(self._days), expected_keys, "all_sixteen_scenario_scopes")
        _equal(set(report["scenarios"]), {f"{timing}|{cost}|{arm}" for arm in ARMS for timing in POLICIES for cost in (33, 43)}, "report.scenarios")
        for (arm, timing, cost, scope), days in self._days.items():
            _equal([row["session"] for row in days], self.reference, "account.evaluation157_only")
            result = report["scenarios"][f"{timing}|{cost}|{arm}"]
            _equal(set(result), {"timing_policy", "cost_bps", "mode", "arm", *account_oracle.SCOPES}, "scenario.fields")
            for name, expected in (("timing_policy", timing), ("cost_bps", cost), ("mode", "A+B"), ("arm", arm)):
                _equal(result[name], expected, "scenario.identity")
            _equal(set(result[scope]), {"days", "own_sizing", "summary"}, "scenario.scope_fields")
            _equal(result[scope]["days"], days, "scenario.days")
            _equal(result[scope]["own_sizing"], {label: account_oracle._sizing(days, self.reference, label) for label in LABELS}, "scenario.all_own_plans")
        comparisons, comparison_days = {}, {}
        for timing in POLICIES:
            for cost in (33, 43):
                all_days = {arm: self._days[arm, timing, cost, "day_local_counterfactual"] for arm in ARMS}
                identified = [{row["session"] for row in days if row["status"] == "identified"} for days in all_days.values()]
                common = [day for day in self.reference if all(day in dates for dates in identified)]
                selected = {arm: [row for row in days if row["session"] in common] for arm, days in all_days.items()}
                key = f"{timing}|{cost}"
                comparison_days[key] = selected
                comparisons[key] = {"timing_policy": timing, "cost_bps": cost,
                    "scope": "support_matched_common_identified_fresh_daily_accounts", "reference_sessions": self.reference,
                    "reference_weeks": self.weeks, "common_sessions": common,
                    "excluded_sessions": [day for day in self.reference if day not in common],
                    "own_sizing": {arm: {label: account_oracle._sizing(days, self.reference, label) for label in LABELS} for arm, days in selected.items()},
                    "paired_sizing": {"selected_minus_control": account_oracle._sizing(selected["selected"], self.reference, peer=selected["control"])},
                    "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
        # All 32 scenario, 16 common-arm and 4 paired plans precede any means.
        for key, expected in comparisons.items():
            summaries = {arm: account_oracle._summary(days, self.weeks, self.reference) for arm, days in comparison_days[key].items()}
            expected["accounts"] = summaries
            expected["paired_mean_daily_net_per_initial_capital"] = {"selected_minus_control":
                summaries["selected"]["mean_daily_net_per_initial_capital"] - summaries["control"]["mean_daily_net_per_initial_capital"]
                if expected["common_sessions"] else None}
        _equal(report["comparisons"], comparisons, "report.common_comparisons")
        for (arm, timing, cost, scope), days in self._days.items():
            _equal(report["scenarios"][f"{timing}|{cost}|{arm}"][scope]["summary"],
                   account_oracle._summary(days, self.weeks, self.reference), "scenario.summary")
        receipt = {"status": "verified", "verified": True, "policy_id": self.policy_id,
            "day_replays": len(self.reference) * 16,
            "unit_work": {"policy_families": 1, "account_mode": "A+B", "arms": list(ARMS),
                "timing_policies": list(POLICIES), "assumed_cost_bps": [33, 43], "scopes": list(account_oracle.SCOPES),
                "scenario_scopes": 16, "own_label_planning_count": 52,
                "planned_fit_slots": 10, "model_fits": sum(row["fit_status"] == "trained" for row in self.fit_records.values()),
                "threshold_searches": 0, "source_passes": 2},
            "features_sha256": typed_sha256([[key, value] for key, value in self.features.items()]),
            "labels_sha256": typed_sha256(tuple(self.validated_labels.values())),
            "fit_verifications": [{"timing_policy": timing, "fold_index": fold, **self.fit_receipts[timing, fold]}
                                  for timing in POLICIES for fold in range(1, 6)],
            "statistical_float_tolerance": {"relative": 1e-12, "absolute": 1e-15},
            "unverified": ["source_byte_authentication", "historical_market_availability", "corporate_action_price_unit_certification",
                "LightGBM_numerical_training_internals_trusted_pinned_external_code"]}
        if "independent_verification" in report:
            _equal(report["independent_verification"], receipt, "report.independent_verification")
        self._closed = True
        return receipt
