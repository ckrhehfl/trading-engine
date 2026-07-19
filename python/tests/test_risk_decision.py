import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from schemas.risk_decision import Decision, RiskDecision


def _approved_kwargs():
    return dict(
        intent_id=uuid4(),
        decision=Decision.APPROVED,
        approved_quantity=Decimal("0.5"),
        approved_leverage=Decimal("2"),
        decided_at=datetime.now(timezone.utc),
    )


def test_round_trip_preserves_all_fields():
    original = RiskDecision(**_approved_kwargs())

    parsed = RiskDecision.model_validate_json(original.model_dump_json())

    assert parsed == original


def test_decimal_fields_serialize_as_json_strings_not_numbers():
    decision = RiskDecision(**_approved_kwargs())

    raw = json.loads(decision.model_dump_json())

    assert isinstance(raw["approved_quantity"], str)
    assert isinstance(raw["approved_leverage"], str)


def test_schema_version_defaults_to_one_point_zero():
    decision = RiskDecision(**_approved_kwargs())

    assert decision.schema_version == "1.0"


@pytest.mark.parametrize("decision_value", [Decision.REJECTED, Decision.MODIFIED])
def test_rejected_or_modified_requires_reason(decision_value):
    kwargs = dict(
        intent_id=uuid4(),
        decision=decision_value,
        decided_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationError):
        RiskDecision(**kwargs)


def test_whitespace_only_reason_is_rejected():
    kwargs = dict(
        intent_id=uuid4(),
        decision=Decision.REJECTED,
        reason="   ",
        decided_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationError):
        RiskDecision(**kwargs)


def test_rejected_with_reason_is_valid():
    kwargs = dict(
        intent_id=uuid4(),
        decision=Decision.REJECTED,
        reason="daily loss limit exceeded",
        decided_at=datetime.now(timezone.utc),
    )

    decision = RiskDecision(**kwargs)

    assert decision.approved_quantity is None


@pytest.mark.parametrize("decision_value", [Decision.APPROVED, Decision.MODIFIED])
def test_approved_or_modified_requires_approved_fields(decision_value):
    kwargs = dict(
        intent_id=uuid4(),
        decision=decision_value,
        reason="ok" if decision_value == Decision.MODIFIED else None,
        decided_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationError):
        RiskDecision(**kwargs)


def test_rejected_with_approved_fields_set_is_rejected():
    kwargs = dict(
        intent_id=uuid4(),
        decision=Decision.REJECTED,
        reason="daily loss limit exceeded",
        approved_quantity=Decimal("0.5"),
        approved_leverage=Decimal("2"),
        decided_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationError):
        RiskDecision(**kwargs)


def test_missing_required_field_is_rejected():
    kwargs = _approved_kwargs()
    del kwargs["intent_id"]

    with pytest.raises(ValidationError):
        RiskDecision(**kwargs)


def test_known_values_produce_exact_json_fixture():
    fixed_id = UUID("11111111-1111-1111-1111-111111111111")
    decision = RiskDecision(
        intent_id=fixed_id,
        decision=Decision.APPROVED,
        approved_quantity=Decimal("0.5"),
        approved_leverage=Decimal("2"),
        decided_at=datetime(2026, 7, 19, 4, 0, 0, tzinfo=timezone.utc),
    )

    assert decision.model_dump_json() == (
        '{"intent_id":"11111111-1111-1111-1111-111111111111","decision":"APPROVED",'
        '"reason":null,"approved_quantity":"0.5","approved_leverage":"2",'
        '"decided_at":"2026-07-19T04:00:00Z","schema_version":"1.0"}'
    )
