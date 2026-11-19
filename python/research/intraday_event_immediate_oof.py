"""ET's two early chronological fits of the ES profitable-fill response."""

from dataclasses import fields, replace
from fractions import Fraction

from research import intraday_event_calibration_oof as er
from research import intraday_event_calibration_oof_reference as er_reference
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as original
from research import intraday_event_models as models
from research import intraday_event_success_reference as es_reference


SPLITS, SCOPE = er.SPLITS, er.SCOPE
fit_unit, verify_unit = er.fit_unit, er.verify_unit


def _require(condition, message):
    if not condition:
        raise ValueError("ET inner OOF: " + message)


def _training_records(shards, data):
    stop = SPLITS[data.inner][1]
    cutoff = data.calendar[stop]
    wanted = {row.key: row for row in data.training}
    _require(len(wanted) == len(data.training), "duplicate training query")
    seen = set()
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= stop:
            break
        # EL derives the number of label rows from the authenticated queries.
        for record in inputs.training_labels(slot, manifest, cutoff):
            _require(record["session"] < cutoff, "label reached the prediction boundary")
            key = record["key"]
            if key not in wanted:
                continue
            row, saved = wanted[key], record["training_labels"]
            _require(key not in seen and saved is not None and saved["key"] == key
                     and (record["session"], record["symbol"]) == (row.session, row.symbol),
                     "exact training label identity differs")
            seen.add(key)
            yield row, record
    _require(seen == set(wanted), "incomplete exact training labels")


def load_inner(shards, inner):
    """Keep the ER chronology, queries, action fills and auxiliary targets."""
    shards = tuple(shards)
    data = er.load_inner(shards, inner)
    fills = {action.key: action for action in data.action_targets}
    adapted = {}
    for row, record in _training_records(shards, data):
        action = fills[row.key]
        saved = record["training_labels"]
        immediate, wait3 = (saved[head] for head in models.Q_HEADS)
        responses = (None, None)
        if row.state not in models.HELD_STATES and None not in (immediate, wait3, action.immediate, action.wait3):
            # The EP adapter binds both exact cash ratios to the original D0 > 0.
            responses = (int(action.immediate == 1 and Fraction(immediate) > 0),
                         int(action.wait3 == 1 and Fraction(wait3) > 0))
        adapted[row.key] = responses
    targets = tuple(replace(label, net_immediate=adapted[label.key][0], net_wait3=adapted[label.key][1])
                    for label in data.targets)
    return replace(data, targets=targets)


def verify_inputs(shards, data):
    """Rebuild the original inner inputs and verify each binary substitution."""
    _require(type(data.inner) is int and data.inner in range(len(SPLITS)), "fixed inner split required")
    shards = tuple(shards)
    _require(tuple(inputs.calendar_from_coordinates(shards)) == data.calendar and data.scope == SCOPE,
             "authenticated calendar or scope differs")
    _require([label.key for label in data.targets] == sorted(row.key for row in data.training),
             "binary target roster differs")
    adapted = {label.key: label for label in data.targets}
    label_fields = fields(models.TrainingLabels)
    originals, joint_rows = {}, 0
    for row, record in _training_records(shards, data):
        label = originals[row.key] = original.decode_labels(record["training_labels"])
        # The ES witness checks original exact cash/D0 and separate fill clocks.
        values, fills = es_reference._training_actions(row, record)
        responses = dict.fromkeys(models.Q_HEADS)
        if all(value is not None for value in values.values()) and all(fill is not None for fill in fills):
            joint_rows += 1
            for head, fill in zip(models.Q_HEADS, fills):
                responses[head] = int(fill == 1 and Fraction(values[head]) > 0)
        actual, target = adapted[row.key], replace(label, **responses)
        _require(type(actual) is type(target) and actual == target and all(
            type(getattr(actual, field.name)) is type(getattr(target, field.name)) for field in label_fields),
            "binary response or original auxiliary target differs")
    original_targets = tuple(originals[key] for key in sorted(originals))
    verified = er_reference.verify_inputs(shards, replace(data, targets=original_targets))
    return {**verified, "joint_rows": joint_rows, "binary_success_responses": True}
