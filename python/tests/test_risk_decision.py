import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

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


def test_rejected_requires_reason():
    kwargs = dict(
        intent_id=uuid4(),
        decision=Decision.REJECTED,
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


def test_missing_required_field_is_rejected():
    kwargs = _approved_kwargs()
    del kwargs["intent_id"]

    with pytest.raises(ValidationError):
        RiskDecision(**kwargs)
