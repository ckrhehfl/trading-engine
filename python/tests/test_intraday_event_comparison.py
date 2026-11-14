"""Invented raw bars through frozen policy, scored forecast and cash audit."""

from collections import Counter
from dataclasses import replace

import pytest

from research import intraday_event_comparison as comparison
from research import intraday_event_dataset as dataset
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as workflow
from research.intraday_event_comparison_verify import verify_replay, verify_replay_day
from test_intraday_event_dataset import invented
from test_intraday_event_model_workflow import CALENDAR


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    slot = tmp_path_factory.mktemp("el-comparison-invented")
    first, last = 95, 99
    active, reader = invented(tuple(range(8)), days=CALENDAR[first:last], only_s1=(8,))
    empty_days = tuple(replace(active.targets[0], session=day, stored_row_count=0)
                       for day in (*CALENDAR[:first], *CALENDAR[last:]))
    universe = replace(active, sessions=CALENDAR, targets=active.targets + empty_days)
    manifest = dataset.build_dataset(slot, universe, reader, session_indices=(first, last))
    return (slot, manifest), universe, reader


def test_real_module_path_seals_all_controls_and_checks_all_six_arms(material):
    (slot, manifest), universe, reader = material
    data = workflow.collect(CALENDAR, 0, "full_chart", inputs.query_rows(slot, manifest),
                            inputs.training_labels(slot, manifest, CALENDAR[97]))
    unit = workflow.fit_unit("empirical", data)
    assert workflow.verify_unit(unit, data)["predictions"] == len(data.evaluation)
    shards = ((slot, manifest),)
    roster = inputs.decision_roster(shards, data)
    markets = inputs.SessionMarkets(universe, reader, CALENDAR[97:137])
    audit_markets = inputs.SessionMarkets(universe, reader, CALENDAR[97:137], audit=True)
    trace = []

    def check(policy, _market, ledger, **options):
        trace.append(policy.control)
        return verify_replay(policy, audit_markets, ledger, **options)

    def labels():
        assert trace == ["onset", "confirmation", "model"]
        yield from inputs.evaluation_targets(shards, data)

    report = comparison.compare(unit, data, roster.episodes, roster.candidates, markets, labels, verify_cash=check)
    assert report["original_episodes"] > 0
    assert len(report["ledgers"]) == report["original_episodes"] * 3 * 6
    assert all(check["ledgers"] == report["original_episodes"] * 6 for check in report["independent_cash_checks"])
    assert len({policy["roster_sha256"] for policy in report["policies"]}) == 1
    assert report["forecast"]["calendar_sessions"] == report["cash"]["calendar_sessions"] == 40
    assert {episode.family for episode in roster.episodes} == {"A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK"}
    decisions = [row for row in report["cash"]["paired_differences"]
                 if row["comparison"] == "decision" and row["dimension"] == "all"]
    assert len(decisions) == 6 and all(row["original_opportunities"] == len(roster.episodes) for row in decisions)
    assert any(row["outcome"]["status"] == "closed" for row in report["ledgers"])


def test_missing_evaluation_labels_cannot_remove_a_cash_opportunity(material):
    (slot, manifest), universe, reader = material
    data = workflow.collect(CALENDAR, 0, "common_chart", inputs.query_rows(slot, manifest),
                            inputs.training_labels(slot, manifest, CALENDAR[97]))
    unit = workflow.fit_unit("empirical", data)
    roster = inputs.decision_roster(((slot, manifest),), data)
    markets = inputs.SessionMarkets(universe, reader, CALENDAR[97:137])
    with pytest.raises(ValueError, match="Evaluation target roster"):
        comparison.compare(unit, data, roster.episodes, roster.candidates, markets, lambda: ())


def test_day_replay_preserves_full_policy_output_and_decodes_each_session_once(material, monkeypatch):
    (slot, manifest), universe, reader = material
    data = workflow.collect(CALENDAR, 0, "full_chart", inputs.query_rows(slot, manifest),
                            inputs.training_labels(slot, manifest, CALENDAR[97]))
    unit = workflow.fit_unit("empirical", data)
    shards = ((slot, manifest),)
    roster = inputs.decision_roster(shards, data)
    original_day = inputs._day
    decoded = Counter()

    def count_decode(day, *args):
        decoded[day] += 1
        return original_day(day, *args)

    monkeypatch.setattr(inputs, "_day", count_decode)

    def run(**audit):
        markets = inputs.SessionMarkets(universe, reader, CALENDAR[97:137], audit=True)
        return comparison.compare(unit, data, roster.episodes, roster.candidates, markets,
                                  lambda: inputs.evaluation_targets(shards, data), **audit)

    expected = run(verify_cash=verify_replay)
    assert set(decoded) == set(CALENDAR[97:99])
    assert min(decoded.values()) > 1
    decoded.clear()
    actual = run(verify_cash_day=verify_replay_day)
    assert actual == expected  # Includes full policy seals, ledger order and per-control audit totals.
    assert decoded == Counter({session: 1 for session in CALENDAR[97:99]})
