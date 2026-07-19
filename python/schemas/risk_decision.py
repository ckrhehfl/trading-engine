from enum import StrEnum
from typing import Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, field_validator, model_validator

from schemas._types import PositiveDecimalString, normalize_to_utc

SCHEMA_VERSION = "1.0"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"


class RiskDecision(BaseModel):
    intent_id: UUID
    decision: Decision
    reason: Optional[str] = None
    approved_quantity: Optional[PositiveDecimalString] = None
    approved_leverage: Optional[PositiveDecimalString] = None
    decided_at: AwareDatetime
    schema_version: str = SCHEMA_VERSION

    @field_validator("decided_at")
    @classmethod
    def _normalize_decided_at(cls, value: "AwareDatetime") -> "AwareDatetime":
        return normalize_to_utc(value)

    @model_validator(mode="after")
    def _check_reason_required(self) -> "RiskDecision":
        if self.decision in (Decision.REJECTED, Decision.MODIFIED) and (
            self.reason is None or not self.reason.strip()
        ):
            raise ValueError("reason is required when decision is REJECTED or MODIFIED")
        return self

    @model_validator(mode="after")
    def _check_approved_fields(self) -> "RiskDecision":
        has_both = self.approved_quantity is not None and self.approved_leverage is not None
        has_neither = self.approved_quantity is None and self.approved_leverage is None
        if self.decision in (Decision.APPROVED, Decision.MODIFIED) and not has_both:
            raise ValueError(
                "approved_quantity and approved_leverage are required when "
                "decision is APPROVED or MODIFIED"
            )
        if self.decision == Decision.REJECTED and not has_neither:
            raise ValueError(
                "approved_quantity and approved_leverage must be null when decision is REJECTED"
            )
        return self
