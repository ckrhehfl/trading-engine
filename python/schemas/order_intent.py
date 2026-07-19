from enum import StrEnum
from typing import Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, field_validator, model_validator

from schemas._types import PositiveDecimalString, normalize_to_utc

SCHEMA_VERSION = "1.0"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    GUARDED_MARKET = "GUARDED_MARKET"


class OrderIntent(BaseModel):
    intent_id: UUID
    symbol: str
    side: Side
    order_type: OrderType
    quantity: PositiveDecimalString
    limit_price: Optional[PositiveDecimalString] = None
    signal_timeframe: Optional[str] = None
    created_at: AwareDatetime
    schema_version: str = SCHEMA_VERSION

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: "AwareDatetime") -> "AwareDatetime":
        return normalize_to_utc(value)

    @model_validator(mode="after")
    def _check_limit_price(self) -> "OrderIntent":
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required when order_type is LIMIT")
        if self.order_type == OrderType.GUARDED_MARKET and self.limit_price is not None:
            raise ValueError("limit_price must be null when order_type is GUARDED_MARKET")
        return self
