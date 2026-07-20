import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from schemas.order_intent import OrderIntent, OrderType, Side
from schemas.risk_decision import Decision, RiskDecision

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "schemas" / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _assert_semantically_round_trips(raw: str, reserialized: str) -> None:
    """Compare parsed JSON structure, not raw text — JSON never
    guarantees key order or whitespace, so a text diff would be fragile
    in a way that doesn't reflect an actual contract violation.
    """
    assert json.loads(raw) == json.loads(reserialized)


def test_order_intent_limit_fixture():
    raw = _read_fixture("order_intent_limit.json")

    order = OrderIntent.model_validate_json(raw)

    assert order.intent_id == UUID("a1a1a1a1-1111-4111-8111-111111111111")
    assert order.symbol == "BTC-USDT"
    assert order.side == Side.LONG
    assert order.order_type == OrderType.LIMIT
    assert order.quantity == Decimal("0.5")
    assert order.limit_price == Decimal("65000.12345678")
    assert order.signal_timeframe == "15m"
    _assert_semantically_round_trips(raw, order.model_dump_json())


def test_order_intent_guarded_market_fixture():
    raw = _read_fixture("order_intent_guarded_market.json")

    order = OrderIntent.model_validate_json(raw)

    assert order.side == Side.SHORT
    assert order.order_type == OrderType.GUARDED_MARKET
    assert order.quantity == Decimal("0.25")
    assert order.limit_price is None
    assert order.signal_timeframe is None
    _assert_semantically_round_trips(raw, order.model_dump_json())


def test_risk_decision_approved_fixture():
    raw = _read_fixture("risk_decision_approved.json")

    decision = RiskDecision.model_validate_json(raw)

    assert decision.decision == Decision.APPROVED
    assert decision.reason is None
    assert decision.approved_quantity == Decimal("0.5")
    assert decision.approved_leverage == Decimal("2")
    _assert_semantically_round_trips(raw, decision.model_dump_json())


def test_risk_decision_rejected_fixture():
    raw = _read_fixture("risk_decision_rejected.json")

    decision = RiskDecision.model_validate_json(raw)

    assert decision.decision == Decision.REJECTED
    assert decision.reason is not None
    assert decision.approved_quantity is None
    assert decision.approved_leverage is None
    _assert_semantically_round_trips(raw, decision.model_dump_json())


def test_risk_decision_modified_fixture():
    raw = _read_fixture("risk_decision_modified.json")

    decision = RiskDecision.model_validate_json(raw)

    assert decision.decision == Decision.MODIFIED
    assert decision.reason is not None
    assert decision.approved_quantity == Decimal("0.1")
    assert decision.approved_leverage == Decimal("1")
    _assert_semantically_round_trips(raw, decision.model_dump_json())
