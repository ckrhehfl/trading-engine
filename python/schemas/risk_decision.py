from datetime import datetime
from enum import StrEnum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, model_validator

from schemas._types import DecimalString

SCHEMA_VERSION = "1.0"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"


class RiskDecision(BaseModel):
    intent_id: UUID
    decision: Decision
    reason: Optional[str] = None
    approved_quantity: Optional[DecimalString] = None
    approved_leverage: Optional[DecimalString] = None
    decided_at: datetime
    schema_version: str = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_reason_required(self) -> "RiskDecision":
        if self.decision in (Decision.REJECTED, Decision.MODIFIED) and not self.reason:
            raise ValueError("reason is required when decision is REJECTED or MODIFIED")
        return self
