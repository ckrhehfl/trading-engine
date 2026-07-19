import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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


@pytest.mark.parametrize("bad_quantity", [Decimal("0"), Decimal("-0.5")])
def test_zero_or_negative_quantity_is_rejected(bad_quantity):
    kwargs = _limit_order_kwargs()
    kwargs["quantity"] = bad_quantity

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)


@pytest.mark.parametrize("bad_price", [Decimal("0"), Decimal("-1")])
def test_zero_or_negative_limit_price_is_rejected(bad_price):
    kwargs = _limit_order_kwargs()
    kwargs["limit_price"] = bad_price

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)


@pytest.mark.parametrize("bad_value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_quantity_is_rejected(bad_value):
    kwargs = _limit_order_kwargs()
    kwargs["quantity"] = bad_value

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)


def test_naive_datetime_is_rejected():
    kwargs = _limit_order_kwargs()
    kwargs["created_at"] = datetime(2026, 7, 19, 4, 0, 0)  # no tzinfo

    with pytest.raises(ValidationError):
        OrderIntent(**kwargs)


def test_non_utc_offset_is_normalized_to_utc():
    kst = timezone(timedelta(hours=9))
    kwargs = _limit_order_kwargs()
    kwargs["created_at"] = datetime(2026, 7, 19, 13, 0, 0, tzinfo=kst)

    order = OrderIntent(**kwargs)

    assert order.created_at == datetime(2026, 7, 19, 4, 0, 0, tzinfo=timezone.utc)
    assert "T04:00:00Z" in order.model_dump_json()


def test_known_values_produce_exact_json_fixture():
    fixed_id = UUID("11111111-1111-1111-1111-111111111111")
    order = OrderIntent(
        intent_id=fixed_id,
        symbol="BTC-USDT",
        side=Side.LONG,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.5"),
        limit_price=Decimal("65000.12345678"),
        signal_timeframe="15m",
        created_at=datetime(2026, 7, 19, 4, 0, 0, tzinfo=timezone.utc),
    )

    assert order.model_dump_json() == (
        '{"intent_id":"11111111-1111-1111-1111-111111111111","symbol":"BTC-USDT",'
        '"side":"LONG","order_type":"LIMIT","quantity":"0.5",'
        '"limit_price":"65000.12345678","signal_timeframe":"15m",'
        '"created_at":"2026-07-19T04:00:00Z","schema_version":"1.0"}'
    )
