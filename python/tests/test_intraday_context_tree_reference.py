"""Invented JSON and bounded synthetic native parity; no market inputs."""
from copy import deepcopy
import builtins
import importlib.util
import math
import struct

import pytest

from research.intraday_context_tree_reference import replay_multiclass


def model(iterations=1):
    return {"name": "tree", "version": "v4", "objective": "multiclass num_class:10",
            "num_class": 10, "num_tree_per_iteration": 10, "average_output": False,
            "max_feature_idx": 1, "feature_names": ["price", "volume"],
            "pandas_categorical": None,
            "tree_info": [{"tree_index": index, "num_leaves": 1, "num_cat": 0,
                           "shrinkage": 0.1, "tree_structure": {"leaf_value": 0.0,
                                                                  "leaf_count": 10}}
                          for index in range(10 * iterations)]}


def replay(dump, rows=((0.0, 0.0),), *, iterations=1, max_leaves=4):
    return replay_multiclass(dump, rows, feature_names=("price", "volume"),
                             expected_iterations=iterations, max_leaves=max_leaves)


def set_value(dump, index, value):
    dump["tree_info"][index]["tree_structure"]["leaf_value"] = value


def split_model(*, missing="NaN", default_left=True, threshold=0.5):
    dump = model()
    dump["tree_info"][0].update(num_leaves=2, tree_structure={
        "split_index": 0, "split_feature": 0, "threshold": threshold,
        "decision_type": "<=", "default_left": default_left, "missing_type": missing,
        "left_child": {"leaf_index": 0, "leaf_value": math.log(9)},
        "right_child": {"leaf_index": 1, "leaf_value": 0.0}})
    return dump


def test_known_softmax_uses_dumped_leaf_value_without_second_shrinkage():
    dump = model()
    set_value(dump, 0, math.log(9))
    result, = replay(dump)
    assert result == pytest.approx((0.5,) + (1 / 18,) * 9)
    assert sum(result) == pytest.approx(1)
    assert replay(dump, ()) == ()


def test_repeated_iterations_add_the_corresponding_class_before_softmax():
    dump = model(2)
    set_value(dump, 0, math.log(3))
    set_value(dump, 10, math.log(3))
    set_value(dump, 9, math.log(2))
    result, = replay(dump, iterations=2)
    assert result == pytest.approx((9 / 19,) + (1 / 19,) * 8 + (2 / 19,))


def test_stable_softmax_handles_large_finite_common_offset_and_extreme_gap():
    dump = model()
    for index in range(10):
        set_value(dump, index, 1000.0)
    assert replay(dump)[0] == pytest.approx((0.1,) * 10)
    set_value(dump, 0, 1e308)
    set_value(dump, 1, -1e308)
    assert replay(dump)[0] == (1.0,) + (0.0,) * 9


def test_threshold_equality_and_adjacent_binary64_values_have_distinct_routes():
    threshold = 1.0 + 2 ** -30  # All three values collapse if converted to float32.
    dump = split_model(threshold=threshold)
    rows = ((math.nextafter(threshold, -math.inf), 0), (threshold, 0),
            (math.nextafter(threshold, math.inf), 0))
    assert [row[0] for row in replay(dump, rows)] == pytest.approx([0.5, 0.5, 0.1])


@pytest.mark.parametrize("default_left", [False, True])
def test_nan_missing_uses_declared_default_and_does_not_treat_zero_as_nan(default_left):
    dump = split_model(default_left=default_left, threshold=-0.5)
    result = replay(dump, ((math.nan, 0), (0.0, 0)))
    assert result[0][0] == pytest.approx(0.5 if default_left else 0.1)
    assert result[1][0] == pytest.approx(0.1)


@pytest.mark.parametrize("default_left", [False, True])
def test_missing_none_converts_nan_to_zero_then_compares_threshold(default_left):
    dump = split_model(missing="None", default_left=default_left, threshold=0.5)
    assert replay(dump, ((math.nan, 0),))[0][0] == pytest.approx(0.5)
    dump["tree_info"][0]["tree_structure"]["threshold"] = -0.5
    assert replay(dump, ((math.nan, 0),))[0][0] == pytest.approx(0.1)


def test_zero_missing_uses_float32_literal_boundary_and_routes_nan():
    zero = struct.unpack("f", struct.pack("f", 1e-35))[0]
    dump = split_model(missing="Zero", default_left=True, threshold=-1)
    values = (math.nan, 0, -0.0, zero, -zero, math.nextafter(zero, math.inf),
              math.nextafter(-zero, -math.inf))
    assert [row[0] for row in replay(dump, tuple((value, 0) for value in values))] == (
        pytest.approx([0.5] * 5 + [0.1, 0.1]))


@pytest.mark.parametrize("missing", ["None", "NaN"])
def test_dense_zero_elision_precedes_both_signed_numeric_threshold_comparisons(missing):
    zero = struct.unpack("f", struct.pack("f", 1e-35))[0]
    values = (1e-36, -1e-36, zero, -zero, math.nextafter(zero, math.inf),
              math.nextafter(-zero, -math.inf))
    rows = tuple((value, 0) for value in values)
    dump = split_model(missing=missing, threshold=0)
    assert [row[0] for row in replay(dump, rows)] == pytest.approx(
        [0.5] * 4 + [0.1, 0.5])
    dump["tree_info"][0]["tree_structure"]["threshold"] = -1e-36
    assert [row[0] for row in replay(dump, rows)] == pytest.approx(
        [0.1] * 5 + [0.5])


@pytest.mark.parametrize(("field", "value"), [
    ("objective", "regression"), ("objective", "multiclassova num_class:10"),
    ("objective", "multiclass"), ("num_class", 9), ("num_class", 10.0),
    ("num_tree_per_iteration", 1), ("num_tree_per_iteration", True),
    ("average_output", True), ("average_output", 0), ("version", "v5"),
    ("name", "linear"), ("max_feature_idx", 2), ("max_feature_idx", True),
    ("feature_names", ["volume", "price"]), ("pandas_categorical", [["a", "b"]]),
])
def test_unsupported_model_geometry_is_rejected(field, value):
    dump = model()
    dump[field] = value
    with pytest.raises(ValueError):
        replay(dump)


@pytest.mark.parametrize("change", ["remove", "append", "reverse", "duplicate", "wrong_index"])
def test_changed_tree_array_cannot_silently_change_class_assignment(change):
    dump = model()
    trees = dump["tree_info"]
    if change == "remove":
        trees.pop()
    elif change == "append":
        trees.append(deepcopy(trees[0]))
    elif change == "reverse":
        trees.reverse()
    elif change == "duplicate":
        trees[1] = deepcopy(trees[0])
    else:
        trees[0]["tree_index"] = 0.0
    with pytest.raises(ValueError, match="tree"):
        replay(dump)


@pytest.mark.parametrize(("field", "value"), [
    ("decision_type", "=="), ("threshold", "0||1"), ("threshold", math.nan),
    ("threshold", math.inf), ("threshold", True), ("default_left", 1),
    ("missing_type", "unknown"), ("split_feature", 2), ("split_feature", True),
    ("split_index", -1), ("internal_value", math.nan), ("internal_count", 1.5),
])
def test_invalid_numeric_split_is_rejected(field, value):
    dump = split_model()
    dump["tree_info"][0]["tree_structure"][field] = value
    with pytest.raises(ValueError):
        replay(dump)


@pytest.mark.parametrize(("field", "value"), [
    ("leaf_value", math.inf), ("leaf_value", math.nan), ("leaf_value", "1.0"),
    ("leaf_value", True), ("leaf_const", 0.0), ("leaf_features", []),
    ("leaf_coeff", []), ("leaf_index", 0), ("leaf_count", 1.5),
])
def test_invalid_unvisited_leaf_is_rejected(field, value):
    dump = split_model()
    dump["tree_info"][0]["tree_structure"]["right_child"][field] = value
    with pytest.raises(ValueError):
        replay(dump, ((-100, 0),))


def test_tree_count_bound_metadata_and_cycles_fail_closed():
    dump = split_model()
    with pytest.raises(ValueError, match="leaf bound"):
        replay(dump, max_leaves=1)
    dump["tree_info"][0]["num_leaves"] = 3
    with pytest.raises(ValueError, match="count differs"):
        replay(dump)
    dump = split_model()
    dump["tree_info"][0]["num_cat"] = 1
    with pytest.raises(ValueError, match="categorical"):
        replay(dump)
    dump = split_model()
    root = dump["tree_info"][0]["tree_structure"]
    root["left_child"] = root
    with pytest.raises(ValueError, match="shared"):
        replay(dump)


@pytest.mark.parametrize("rows", [None, [1], [[0]], [[0, 0, 0]], [["1", 0]],
                                  [[None, 0]], [[True, 0]], [[math.inf, 0]],
                                  [[-math.inf, 0]], [[10 ** 1000, 0]]])
def test_invalid_dense_feature_values_and_dimensions_are_rejected(rows):
    with pytest.raises(ValueError):
        replay(model(), rows)


@pytest.mark.parametrize("names", [[], ["price", "price"], ["price", 1],
                                   ["volume", "price"], ["price"]])
def test_caller_feature_names_are_required_exactly_in_order(names):
    with pytest.raises(ValueError):
        replay_multiclass(model(), (), feature_names=names, expected_iterations=1, max_leaves=4)


@pytest.mark.parametrize(("iterations", "leaves"), [(0, 4), (True, 4), (1.0, 4),
                                                       (1, 0), (1, True), (1, 4.0)])
def test_caller_geometry_requires_positive_integer_bounds(iterations, leaves):
    with pytest.raises(ValueError):
        replay(model(), iterations=iterations, max_leaves=leaves)


def test_nonfinite_class_sum_is_rejected_before_softmax():
    dump = model(2)
    set_value(dump, 0, 1e308)
    set_value(dump, 10, 1e308)
    with pytest.raises(ValueError, match="nonfinite class sum"):
        replay(dump, iterations=2)


def test_replay_imports_no_native_engine_and_preserves_inputs(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in ("numpy", "scipy", "lightgbm"):
            raise AssertionError("reference imported native prediction machinery")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    dump = split_model()
    before = deepcopy(dump)
    replay(dump)
    assert dump == before


def _threshold_zero_text_model(missing):
    """A complete invented native text model; no fit selects its zero split."""
    trees = []
    decision_type = {"None": 2, "NaN": 10, "Zero": 6}[missing]
    for index in range(10):
        left = math.log(9) if index == 0 else 0.0
        trees.append(f"""Tree={index}
num_leaves=2
num_cat=0
split_feature=0
split_gain=1
threshold=0
decision_type={decision_type}
left_child=-1
right_child=-2
leaf_value={left:.17g} 0
leaf_weight=1 1
leaf_count=1 1
internal_value=0
internal_weight=2
internal_count=2
is_linear=0
shrinkage=1


""")
    return ("tree\nversion=v4\nnum_class=10\nnum_tree_per_iteration=10\nlabel_index=0\n"
            "max_feature_idx=1\nobjective=multiclass num_class:10\n"
            "feature_names=price volume\nfeature_infos=[-1:1] [-1:1]\n"
            + "tree_sizes=" + " ".join(str(len(tree)) for tree in trees) + "\n\n"
            + "".join(trees) + "end of trees\n")


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None,
                    reason="pinned LightGBM unavailable")
@pytest.mark.parametrize("missing", ["None", "NaN", "Zero"])
def test_native_dense_input_zero_elision_before_threshold_routing(monkeypatch, missing):
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(name, "1")
    import lightgbm as lgb
    import numpy as np

    assert lgb.__version__ == "4.6.0"
    booster = lgb.Booster(model_str=_threshold_zero_text_model(missing))
    dump = booster.dump_model()
    assert dump["tree_info"][0]["tree_structure"]["threshold"] == 0.0
    assert dump["tree_info"][0]["tree_structure"]["missing_type"] == missing
    zero = struct.unpack("f", struct.pack("f", 1e-35))[0]
    values = (1e-36, -1e-36, zero, -zero, 0.0, -0.0, math.nan,
              math.nextafter(zero, math.inf), math.nextafter(-zero, -math.inf))
    rows = tuple((value, 0.0) for value in values)
    expected = booster.predict(np.array(rows, dtype=np.float64), num_threads=1)
    np.testing.assert_allclose(expected[:, 0], [0.5] * 7 + [0.1, 0.5],
                               rtol=1e-13, atol=1e-14)

    def forbidden(*args, **kwargs):
        raise AssertionError("reference called native prediction")

    monkeypatch.setattr(lgb.Booster, "predict", forbidden)
    np.testing.assert_allclose(replay(dump, rows), expected, rtol=1e-13, atol=1e-14)


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None,
                    reason="pinned LightGBM unavailable")
@pytest.mark.parametrize("missing_mode", ["nan", "zero", "none"])
def test_native_460_multiclass_parity_on_fixed_invented_rows(monkeypatch, missing_mode):
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(name, "1")
    import lightgbm as lgb
    import numpy as np

    assert lgb.__version__ == "4.6.0"
    rows = [(float(index % 10), float(index % 7)) for index in range(120)]
    for index in range(0, 120, 11):
        rows[index] = (math.nan, rows[index][1])
    matrix = np.array(rows, dtype=np.float64)
    dataset = lgb.Dataset(matrix, label=[index % 10 for index in range(120)],
                         feature_name=["price", "volume"])
    booster = lgb.train({"objective": "multiclass", "num_class": 10, "verbosity": -1,
                         "num_threads": 1, "num_leaves": 4, "min_data_in_leaf": 2,
                         "learning_rate": 0.1, "deterministic": True,
                         "force_col_wise": True, "use_missing": missing_mode != "none",
                         "zero_as_missing": missing_mode == "zero"}, dataset,
                        num_boost_round=3)
    dump = booster.dump_model(start_iteration=0, num_iteration=-1)
    queries = rows[:10] + [(math.nan, 0.0), (0.0, math.nan), (1e-36, 0.0)]
    expected = booster.predict(np.array(queries, dtype=np.float64), start_iteration=0,
                               num_iteration=-1, num_threads=1, pred_early_stop=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("reference called native fit or prediction")

    monkeypatch.setattr(lgb, "train", forbidden)
    monkeypatch.setattr(lgb.Booster, "predict", forbidden)
    actual = replay(dump, queries, iterations=3)
    np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-14)
