"""Invented chronological and missingness boundary checks, no source inputs."""
from dataclasses import asdict, replace
from datetime import date, timedelta
from fractions import Fraction
from unittest.mock import patch

import pytest

from research import intraday_event_model_workflow as flow
from research import intraday_event_models as models


CALENDAR = tuple((date(2025, 9, 4) + timedelta(days=i)).isoformat() for i in range(257))


def row(index, *, peer=None):
    query = models.Query(f"key{index}", CALENDAR[index], "KRX:000001", f"event{index}",
        f"cluster{index}", 50, "A_DOWNFAIL", "WAITING", (("1/2",) * 13,) * 64,
        ("1/2",) * 16, peer)
    return {"kind": "query", "key": query.key, "session": query.session,
            "symbol": query.symbol, "query": asdict(query)}


def target(record, *, immediate="1/100", wait="2/100"):
    return {"kind": "labels", "key": record["key"], "session": record["session"],
            "training_labels": {"key": record["key"], "path_class": 2, "fill": 1,
                "net_immediate": immediate, "net_wait3": wait, "remaining_gross": None,
                "matured_session": record["session"]}}


def test_common_scopes_have_identical_target_independent_rosters():
    records = [row(0, peer=(0, 0, 1)), row(1), row(97, peer=(0, 0, 1)), row(98)]
    labels = [target(r) for r in records[:2]]
    own = flow.collect(CALENDAR, 0, "full_chart", records, labels)
    chart = flow.collect(CALENDAR, 0, "common_chart", records, labels)
    context = flow.collect(CALENDAR, 0, "common_chart_peer", records, labels)
    assert len(own.training) == len(own.evaluation) == 2
    assert chart.support == context.support
    assert len(chart.training) == len(chart.evaluation) == 1
    assert own.training[0].sequence[0][0] == 0.5
    assert chart.support["evaluation_labels_read"] is False


def test_test_targets_cannot_reach_fit_even_if_their_key_is_unwanted():
    records = [row(0), row(97)]
    with pytest.raises(ValueError, match="Evaluation label"):
        flow.collect(CALENDAR, 0, "full_chart", records, [target(r) for r in records])


def test_missing_chart_and_label_are_distinct_and_unknown_outcome_does_not_drop_evaluation():
    unknown = row(98)
    unknown["query"] = None
    records = [row(0), row(97), unknown]
    result = flow.collect(CALENDAR, 0, "full_chart", records, [target(records[0], immediate=None, wait=None)])
    assert result.targets[0].net_immediate is None
    assert len(result.evaluation) == 1
    assert result.support["counts"]["evaluation:original"] == 2
    assert result.support["counts"]["evaluation:unknown_chart"] == 1
    with pytest.raises(ValueError, match="target roster incomplete"):
        flow.collect(CALENDAR, 0, "full_chart", records, [])


def test_all_four_test_folds_cover_exactly_the_original_tail():
    covered = [day for _, start, stop in flow.FOLDS for day in CALENDAR[start:stop]]
    assert covered == list(CALENDAR[97:])
    assert len(set(covered)) == 160


def test_empty_training_is_an_explicit_unsupported_unit_without_native_fit():
    data = flow.collect(CALENDAR, 0, "full_chart", [row(97)], [])
    with patch.object(flow.tabular, "fit", side_effect=AssertionError("fit must not run")):
        result = flow.fit_unit("empirical", data)
    assert result["fit_artifacts"] == 0
    assert flow.verify_unit(result, data)["verified"]


def test_complete_empirical_fit_and_saved_prediction_replay():
    records = [row(0), row(1), row(97), row(98)]
    data = flow.collect(CALENDAR, 0, "full_chart", records, [target(r) for r in records[:2]])
    result = flow.fit_unit("empirical", data)
    assert result["fit_artifacts"] == 1
    assert flow.verify_unit(result, data) == {"verified": True, "predictions": 2,
                                           "native_optimizer_reimplemented": False}
    result["predictions"][0]["predictions"]["net_immediate"] += 1
    with pytest.raises(ValueError, match="forecast differs"):
        flow.verify_unit(result, data)


def test_nonfinite_boolean_and_duplicate_queries_rejected():
    for value in (float("nan"), True):
        with pytest.raises(ValueError):
            flow.number(value)
    r = row(0)
    with pytest.raises(ValueError, match="Duplicate"):
        flow.collect(CALENDAR, 0, "full_chart", [r, r], [target(r)])


def test_repeated_exact_wire_scalars_keep_the_uncached_query_and_target_values(monkeypatch):
    records = [row(0), row(97)]
    records[1]["query"]["static"] = ("0/3", "-1/7", "2/4") + ("1/2",) * 13
    expected = [flow.decode_query(r["query"]) for r in records]
    monkeypatch.setattr(flow, "MAX_SHARED_SCALARS", 1)
    data = flow.collect(CALENDAR, 0, "full_chart", records, [target(records[0])])
    assert data.training + data.evaluation == tuple(expected)
    assert data.training[0].sequence[0][0] is data.evaluation[0].sequence[63][12]
    assert data.targets == (flow.decode_labels(target(records[0])["training_labels"]),)
