"""Synthetic declarations for the original-investment compulsory spin-off."""
from copy import deepcopy
from decimal import Decimal

import pytest

from research.activity_book import CompulsorySpinOff
from test_activity_reviewed_actions import (
    A, AI, B, BI, call, fixture, price_basis, reference, window,
)


def spin_fixture():
    coverage, evidence, required = fixture()
    event_id = "spin-1"
    coverage["events"] = [dict(event_id=event_id, kind="compulsory_spin_off", status="verified_effective",
        code=A, isin=AI, successor_code=B, successor_isin=BI, retained_ratio="0.9123456789012345678901234567",
        new_ratio="0.456", old_basis_id="old", retained_basis_id="retained", new_basis_id="new",
        last_eligible_entry_on="2020-01-03", record_on="2020-01-04", effective_on="2020-01-05",
        retained_available_on="2020-01-06", new_available_on="2020-01-07", retained_carry_weight="0.8",
        carry_evidence_level="assumed", carry_evidence_ref="carry", evidence_ref="event")]
    coverage["windows"] = [window(ids=(event_id,)), window(B, BI, "2020-01-05", "2020-01-08", ref="new-complete")]
    evidence["references"]["complete"] = reference(finding="inferred_supported_events_complete", ids=(event_id,))
    evidence["references"]["new-complete"] = reference(B, BI, "2020-01-05", "2020-01-08",
                                                              "inferred_no_compulsory_action")
    evidence["references"]["event"] = reference(start="2020-01-03", end="2020-01-05",
        finding="verified_compulsory_spin_off", ids=(event_id,))
    evidence["references"]["carry"] = reference(start="2020-01-05", end="2020-01-05",
        finding="registered_spin_off_carry_assumed", ids=(event_id,))
    price_basis(evidence, "old", A, AI, "2020-01-03", "last_observable_pre_event")
    price_basis(evidence, "retained", A, AI, "2020-01-06", "retained_availability")
    price_basis(evidence, "new", B, BI, "2020-01-07", "successor_availability")
    return coverage, evidence, required


def test_decodes_one_slot_event_without_mutating_or_promoting_carry_assumption():
    args = spin_fixture()
    before = deepcopy(args)
    events, requirements = call(*args)
    assert args == before
    assert len(events) == 1 and isinstance(events[0], CompulsorySpinOff)
    event = events[0]
    assert event.retained_ratio == Decimal("0.9123456789012345678901234567")
    assert event.carry_evidence_level == "assumed"
    assert "review_ref:carry" in event.carry_source
    assert requirements == {A: ("2020-01-06",), B: ("2020-01-07", "2020-01-08")}


def test_retained_basis_anchor_after_original_window_remains_a_quote_requirement():
    coverage, evidence, required = spin_fixture()
    coverage["events"][0]["retained_available_on"] = "2020-01-09"
    evidence["bases"]["retained"]["session"] = "2020-01-09"
    evidence["references"]["retained"]["start"] = "2020-01-09"
    evidence["references"]["retained"]["end"] = "2020-01-09"
    _, requirements = call(coverage, evidence, required)
    assert requirements[A] == ("2020-01-09",)


@pytest.mark.parametrize("field,value", [
    ("status", "proposed"), ("retained_ratio", 0.9), ("new_ratio", "0"),
    ("retained_carry_weight", "1"), ("carry_evidence_level", "unknown"),
    ("last_eligible_entry_on", "2020-01-05"), ("record_on", "2020-01-07"),
    ("new_available_on", "2020-01-03"), ("successor_code", A),
    ("retained_basis_id", "old"), ("carry_evidence_ref", "event"),
])
def test_rejects_unverified_terms_inexact_ratios_and_inconsistent_dates(field, value):
    coverage, evidence, required = spin_fixture()
    coverage["events"][0][field] = value
    with pytest.raises(ValueError):
        call(coverage, evidence, required)


@pytest.mark.parametrize("case", ["missing-successor", "wrong-basis-hash", "missing-retained",
                                  "carry-promoted", "no-cutoff-evidence", "unused-evidence"])
def test_complete_intervals_and_all_three_basis_links_are_mandatory(case):
    coverage, evidence, required = spin_fixture()
    if case == "missing-successor":
        coverage["windows"].pop()
        evidence["references"].pop("new-complete")
    elif case == "wrong-basis-hash":
        evidence["bases"]["retained"]["quote_snapshot_sha256"] = "f" * 64
    elif case == "missing-retained":
        evidence["bases"].pop("retained")
    elif case == "carry-promoted":
        evidence["references"]["carry"]["finding"] = "registered_spin_off_carry_confirmed"
    elif case == "no-cutoff-evidence":
        evidence["references"]["event"]["start"] = "2020-01-05"
    else:
        evidence["references"]["extra"] = reference()
    with pytest.raises(ValueError):
        call(coverage, evidence, required)
