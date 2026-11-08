"""Pure EI replay of LightGBM 4.6.0 ten-class softmax ``dump_model`` JSON.

No native import, prediction, training, file access or experiment is performed.
Runtime/package pins, training membership, class meanings, artifact identity,
registration and the whole research lifecycle remain the caller's responsibility.
In particular, a valid dump is not proof that it is the registered fitted model.

The caller supplies the exact retained iteration count and registered leaf bound,
and must use the same dense float64 feature values/order for native prediction.
All retained trees are evaluated; prediction truncation and early stopping are
not inferred. Categorical splits, linear leaves and averaged output are refused.
Numeric missing routing and already-shrunk leaves follow the pinned source:
https://github.com/microsoft/LightGBM/blob/v4.6.0/include/LightGBM/tree.h
https://github.com/microsoft/LightGBM/blob/v4.6.0/include/LightGBM/meta.h
https://github.com/microsoft/LightGBM/blob/v4.6.0/src/objective/multiclass_objective.hpp
Dense input zero elision is applied before any split's missing policy:
https://github.com/microsoft/LightGBM/blob/v4.6.0/src/c_api.cpp
https://github.com/microsoft/LightGBM/blob/v4.6.0/src/application/predictor.hpp
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Sequence


_CLASSES = 10
# C++ defines this as the float literal 1e-35f, then promotes it to double.
_ZERO_THRESHOLD = struct.unpack("f", struct.pack("f", 1e-35))[0]
_SPLIT_FIELDS = frozenset(("split_index", "split_feature", "split_gain", "threshold",
    "decision_type", "default_left", "missing_type", "internal_value", "internal_weight",
    "internal_count", "left_child", "right_child"))
_LEAF_FIELDS = frozenset(("leaf_index", "leaf_value", "leaf_weight", "leaf_count"))


@dataclass(frozen=True)
class _Split:
    feature: int
    threshold: float
    default_left: bool
    missing: str
    left: int
    right: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _integer(value: object, minimum: int, maximum: int, name: str) -> int:
    _require(type(value) is int and minimum <= value <= maximum, f"invalid {name}")
    return value


def _number(value: object, name: str, *, allow_nan: bool = False) -> float:
    _require(type(value) in (int, float), f"invalid numeric {name}")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"invalid numeric {name}") from error
    _require(math.isfinite(result) or (allow_nan and math.isnan(result)),
             f"nonfinite {name}")
    return result


def _dense_feature(value: object) -> float:
    value = _number(value, "feature", allow_nan=True)
    # RowPairFunctionFromDenseMatric omits finite values this close to zero;
    # omitted entries remain zero in the native prediction buffer. This also
    # applies to None/NaN splits, independently of zero_as_missing.
    return 0.0 if abs(value) <= _ZERO_THRESHOLD else value


def _tree(tree: object, index: int, width: int, max_leaves: int) -> tuple:
    _require(type(tree) is dict and set(tree) == {
        "tree_index", "num_leaves", "num_cat", "shrinkage", "tree_structure"},
        "unsupported tree schema")
    _integer(tree["tree_index"], index, index, "tree index/order")
    count = _integer(tree["num_leaves"], 1, max_leaves, "tree leaf bound")
    _integer(tree["num_cat"], 0, 0, "categorical tree")
    _require(_number(tree["shrinkage"], "shrinkage") > 0, "invalid shrinkage")
    # Compile all nodes, including branches no supplied row reaches. This both
    # rejects malformed hidden leaves and avoids rereading mutable dump objects.
    compiled = [None]
    pending = [(tree["tree_structure"], 0)]
    seen, splits, leaves = set(), set(), set()
    while pending:
        node, position = pending.pop()
        _require(type(node) is dict and id(node) not in seen, "invalid/shared tree node")
        seen.add(id(node))
        _require(len(seen) <= 2 * count - 1, "tree node count exceeds declared leaves")
        if "split_index" not in node:
            _require(set(node) <= _LEAF_FIELDS and "leaf_value" in node,
                     "unsupported leaf schema/linear leaf")
            leaf_id = _integer(node.get("leaf_index", 0 if count == 1 else None),
                               0, count - 1, "leaf index")
            _require(leaf_id not in leaves, "duplicate leaf index")
            leaves.add(leaf_id)
            compiled[position] = _number(node["leaf_value"], "leaf value")
            if "leaf_weight" in node:
                _require(_number(node["leaf_weight"], "leaf weight") >= 0,
                         "invalid leaf weight")
            if "leaf_count" in node:
                _require(type(node["leaf_count"]) is int and node["leaf_count"] >= 0,
                         "invalid leaf count")
            continue
        _require(set(node) <= _SPLIT_FIELDS and {
            "split_index", "split_feature", "threshold", "decision_type", "default_left",
            "missing_type", "left_child", "right_child"} <= set(node),
            "unsupported split schema")
        split_id = _integer(node["split_index"], 0, count - 2, "split index")
        _require(split_id not in splits, "duplicate split index")
        splits.add(split_id)
        feature = _integer(node["split_feature"], 0, width - 1, "split feature")
        threshold = _number(node["threshold"], "threshold")
        _require(node["decision_type"] == "<=", "unsupported categorical/nonnumeric split")
        _require(type(node["default_left"]) is bool, "invalid missing default direction")
        _require(node["missing_type"] in ("None", "NaN", "Zero"), "unsupported missing type")
        for field in ("split_gain", "internal_value", "internal_weight"):
            if field in node:
                _number(node[field], field)
        if "internal_count" in node:
            _require(type(node["internal_count"]) is int and node["internal_count"] >= 0,
                     "invalid internal count")
        left, right = len(compiled), len(compiled) + 1
        compiled.extend((None, None))
        compiled[position] = _Split(feature, threshold, node["default_left"],
                                    node["missing_type"], left, right)
        pending.extend(((node["right_child"], right), (node["left_child"], left)))
    _require(len(leaves) == count and len(splits) == count - 1,
             "tree leaf/split count differs")
    return tuple(compiled)


def _leaf(tree: tuple, row: tuple[float, ...]) -> float:
    node = tree[0]
    while isinstance(node, _Split):
        value = row[node.feature]
        if math.isnan(value) and node.missing != "NaN":
            value = 0.0
        missing = ((node.missing == "NaN" and math.isnan(value)) or
                   (node.missing == "Zero" and abs(value) <= _ZERO_THRESHOLD))
        left = node.default_left if missing else value <= node.threshold
        node = tree[node.left if left else node.right]
    return node


def replay_multiclass(dump: dict, rows: Sequence[Sequence[float]], *,
                      feature_names: Sequence[str], expected_iterations: int,
                      max_leaves: int) -> tuple[tuple[float, ...], ...]:
    """Return independent ten-class probabilities, in native class-index order.

    ``rows`` and ``feature_names`` are dense lists/tuples of Python numbers and
    strings; conversion is to Python binary64, never binary32. As in the native
    dense-matrix API, finite magnitudes <= its zero threshold become zero before
    routing. NaN retains each split's serialized missing policy. Infinity and
    nonnumeric values fail. Sparse/Arrow prediction paths are not covered.
    The positive caller bounds must come from its registered model geometry;
    passing bounds derived only from this dump does not authenticate geometry.
    """
    _require(type(expected_iterations) is int and expected_iterations > 0,
             "invalid expected iterations")
    _require(type(max_leaves) is int and max_leaves > 0, "invalid maximum leaves")
    _require(type(feature_names) in (tuple, list) and bool(feature_names) and
             all(type(name) is str and name for name in feature_names) and
             len(set(feature_names)) == len(feature_names), "invalid feature names")
    _require(type(dump) is dict, "model dump must be a JSON object")
    _require(dump.get("name") == "tree" and dump.get("version") == "v4",
             "unsupported model dump format")
    _require(dump.get("objective") == "multiclass num_class:10",
             "unsupported multiclass objective")
    _integer(dump.get("num_class"), _CLASSES, _CLASSES, "class count")
    _integer(dump.get("num_tree_per_iteration"), _CLASSES, _CLASSES,
             "trees per iteration")
    _require(dump.get("average_output") is False, "averaged output unsupported")
    _integer(dump.get("max_feature_idx"), len(feature_names) - 1,
             len(feature_names) - 1, "feature width")
    _require(type(dump.get("feature_names")) is list and
             dump["feature_names"] == list(feature_names), "feature names/order differ")
    _require(dump.get("pandas_categorical") in (None, []),
             "categorical feature mapping unsupported")
    trees = dump.get("tree_info")
    _require(type(trees) is list and len(trees) == _CLASSES * expected_iterations,
             "tree count differs from expected iterations")
    compiled = tuple(_tree(tree, index, len(feature_names), max_leaves)
                     for index, tree in enumerate(trees))
    _require(type(rows) in (tuple, list), "dense rows must be a list/tuple")
    output = []
    for row in rows:
        _require(type(row) in (tuple, list) and len(row) == len(feature_names),
                 "dense row feature width differs")
        values = tuple(_dense_feature(value) for value in row)
        logits = [0.0] * _CLASSES
        for index, tree in enumerate(compiled):
            # Dumped leaf values already include shrinkage and initial bias.
            logits[index % _CLASSES] += _leaf(tree, values)
        _require(all(math.isfinite(value) for value in logits), "nonfinite class sum")
        maximum = max(logits)
        weights = tuple(math.exp(value - maximum) for value in logits)
        denominator = sum(weights)
        output.append(tuple(weight / denominator for weight in weights))
    return tuple(output)
