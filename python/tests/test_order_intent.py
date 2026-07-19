import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schemas.order_intent import OrderIntent, OrderType, Side


def _limit_order_kwargs():
    return dict(
        intent_id=uuid4(),
        symbol="BTC-USDT",
        side=Side.LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.5"),
        limit_price=Decimal("65000.12345678"),
        signal_timeframe="15m",
        created_at=datetime.now(timezone.utc),
    )


def test_round_trip_preserves_all_fields():
    original = OrderIntent(**_limit_order_kwargs())

    parsed = OrderIntent.model_validate_json(original.model_dump_json())

    assert parsed == original


def test_decimal_fields_serialize_as_json_strings_not_numbers():
    order = OrderIntent(**_limit_order_kwargs())

    raw = json.loads(order.model_dump_json())

    assert isinstance(raw["quantity"], str)
    assert isinstance(raw["limit_price"], str)
    assert raw["quantity"] == "0.5"


def test_schema_version_defaults_to_one_point_zero():
    order = OrderIntent(**_limit_order_kwargs())

    assert order.schema_version == "1.0"


def test_limit_order_requires_limit_price():
    kwargs = _limit_order_kwargs()
    kwargs["limit_price"] = None

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)


def test_guarded_market_order_rejects_limit_price():
    kwargs = _limit_order_kwargs()
    kwargs["order_type"] = OrderType.GUARDED_MARKET

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)


def test_guarded_market_order_without_limit_price_is_valid():
    kwargs = _limit_order_kwargs()
    kwargs["order_type"] = OrderType.GUARDED_MARKET
    kwargs["limit_price"] = None

    order = OrderIntent(**kwargs)

    assert order.limit_price is None


def test_missing_required_field_is_rejected():
    kwargs = _limit_order_kwargs()
    del kwargs["symbol"]

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)
