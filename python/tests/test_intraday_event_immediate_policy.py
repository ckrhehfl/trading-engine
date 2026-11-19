"""Invented decision-time and original cash witnesses for ET."""

from copy import deepcopy
from fractions import Fraction

import pytest

from research import intraday_event_immediate_policy as policy
from research import intraday_event_immediate_reference as reference
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction, tape
from test_intraday_event_mean_baseline import data_for, roster as decision_roster


def packed(value):
    value = Fraction(value)
    return [format(value.numerator, "x"), format(value.denominator, "x")]


def setup(candidates, direct, mapped):
    data, _ = data_for(evaluation_rows=tuple(c.query for c in candidates if c.query is not None))
    roster = decision_roster(candidates)
    predictions = {"direct": tuple(prediction(c, v, 999) for c, v in zip(candidates, direct) if c.query is not None),
        "mapped": tuple(prediction(c, None if v is None else packed(v), None)
                        for c, v in zip(candidates, mapped) if c.query is not None)}
    return data, roster, predictions, policy.freeze(data, roster, predictions)


def test_exact_positive_and_zero_first_eligible_without_wait_lookahead():
    first, later, last = candidate(), candidate(decision=36), candidate(decision=39)
    data, roster, scores, choices = setup((first, later, last), (0, -1, -1), (0, Fraction(1, 10**500), 2))
    assert choices["direct"]["choices"][0]["reason"] == "known_abstention"
    assert choices["mapped"]["choices"][0]["query_key"] == later.key
    assert [row[-1] for row in choices["mapped"]["trace"]] == [0, 1]
    reference.verify_policies(data, roster, scores, choices)
    copy = deepcopy(choices)
    copy["mapped"]["choices"][0]["decision_offset"] = last.frame.decision_offset
    with pytest.raises(ValueError, match="immediate policy"):
        reference.verify_policies(data, roster, scores, copy)


def test_missing_decision_kept_unknown_while_a_later_supported_choice_can_commit():
    first, later = candidate(query=False), candidate(decision=36)
    data, roster, scores, choices = setup((first, later), (None, 0), (None, 1))
    assert choices["direct"]["choices"][0]["reason"] == "unsupported_decision"
    assert choices["mapped"]["choices"][0]["query_key"] == later.key
    records = (label_record(first), label_record(later))
    report = policy.summarize(CALENDAR, 0, roster, choices, records)
    assert report["policy_rows"]["direct"][0]["net_cash"] is None
    assert report["policy"]["mapped_minus_direct"]["unknown_pairs"] == 1
    reference.verify_policies(data, roster, scores, choices)
    reference.verify_report(CALENDAR, 0, roster, choices, records, report)


def test_exact_cash_join_week_frequency_and_removed_loss():
    event = candidate()
    data, roster, scores, choices = setup((event,), (1,), (0,))
    records = (label_record(event),)
    report = policy.summarize(CALENDAR, 0, roster, choices, records)
    arm = report["policy"]["arms"]["direct"]
    assert arm["fills"] == arm["commits"] == 1
    assert arm["losing_fills"] == 1 and Fraction(arm["known_net_cash"]) < 0
    assert arm["weeks"] > 1 and sum(w["fills"] == 0 for w in arm["weekly"]) == arm["weeks"] - 1
    pair = report["policy"]["mapped_minus_direct"]
    assert Fraction(pair["known_cash_delta"]) == -Fraction(arm["known_net_cash"])
    assert pair["transitions"]["removed"]["better"] == 1
    reference.verify_policies(data, roster, scores, choices)
    reference.verify_report(CALENDAR, 0, roster, choices, records, report)


def test_known_fill_with_unknown_exit_is_counted_without_cash_imputation():
    event = candidate()
    _, roster, _, choices = setup((event,), (1,), (0,))
    bars = tape()
    del bars[64]
    records = (label_record(event, bars),)
    report = policy.summarize(CALENDAR, 0, roster, choices, records)
    arm = report["policy"]["arms"]["direct"]
    assert arm["fills"] == 1 and arm["unknown_cash"] == 1 and arm["unknown_fills"] == 0
    assert report["policy"]["mapped_minus_direct"]["unknown_pairs"] == 1
    reference.verify_report(CALENDAR, 0, roster, choices, records, report)


def test_pool_keeps_zero_weeks_and_rejects_tampered_period():
    from dataclasses import replace

    reports = []
    for fold in range(4):
        event = candidate(97 + 40 * fold)
        data, roster, scores, _ = setup((), (), ())
        data = replace(data, base=replace(data.base, fold=fold, evaluation=(event.query,)))
        roster = decision_roster((event,))
        scores = {"direct": [prediction(event, 1)], "mapped": [prediction(event, packed(0))]}
        choices = policy.freeze(data, roster, scores)
        reports.append(policy.summarize(CALENDAR, fold, roster, choices, (label_record(event),)))
    total = policy.pool(CALENDAR, reports)
    assert total["calendar_days"] == 160 and total["policy"]["arms"]["direct"]["fills"] == 4
    assert all(x["fills"] == 0 for x in total["policy"]["arms"]["mapped"]["weekly"])
    reference.verify_pool(CALENDAR, reports, total)
    reports[0]["policy"]["arms"]["direct"]["fills"] = 9
    with pytest.raises(ValueError, match="fold aggregate"):
        reference.verify_pool(CALENDAR, reports, total)
