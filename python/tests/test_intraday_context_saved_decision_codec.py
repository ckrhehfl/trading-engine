"""Invented readback checks for the original EI causal decision wire rows."""

from dataclasses import asdict, replace
from decimal import Decimal
from fractions import Fraction
from json import loads

import pytest

from research.intraday_chart_cash import new_book
from research.intraday_context_economic_fold import _plain, entry_decision_records
from research.intraday_context_saved_decision_codec import decode_saved_entry_pair
from research.intraday_ed_inputs import MINUTE, InputRefusal
from test_intraday_context_entry_compare import data, prepare, value_model
from test_intraday_context_entry_values import POLICY


def _plan(data, **changes):
    return prepare(data, **{"book": new_book("EI-S1"), "risk_budget": Fraction(100_000), **changes})[0]


def _original_records(plan):
    records = entry_decision_records(plan)
    next(records)
    for raw, coordinate in zip(records, plan.coordinates, strict=True):
        pairs = loads(raw)["coordinate"]["decisions"]
        yield from zip(pairs, coordinate.decisions, strict=True)


def _decode(raw, expected, plan, *, forecast=None):
    return decode_saved_entry_pair(raw, expected.opportunity, scenario=plan.scenario,
                                  forecast_sha256=plan.forecast_sha256 if forecast is None else forecast,
                                  execution_policy_sha256=POLICY)


@pytest.mark.parametrize("missing_payoff", [False, True])
def test_roundtrips_original_entry_rows_including_inactive_and_unknown_value(data, missing_payoff):
    plan = _plan(data, model=value_model(data[2], unavailable_class=missing_payoff))
    pairs = tuple(_decode(raw, expected, plan) for raw, expected in _original_records(plan))
    assert pairs == tuple(pair for coordinate in plan.coordinates for pair in coordinate.decisions)
    assert any(pair.opportunity.status == "overlap_suppressed" for pair in pairs)
    assert any(pair.selected is not None and pair.selected.entry_value.status ==
               ("abstain" if missing_payoff else "supported") for pair in pairs)


@pytest.mark.parametrize("delay", [None, 1])
def test_retains_original_common_causal_absence(data, delay):
    session = data[-1]

    def visible(day, symbol, at):
        if delay is None:
            return None
        original = session.minutes[at]
        return replace(original, available_at=original.available_at + delay * MINUTE)

    plan = _plan(data, read_visible=visible)
    pairs = tuple(_decode(raw, expected, plan) for raw, expected in _original_records(plan))
    reason = "causal_close_missing" if delay is None else "causal_close_availability_differs"
    assert any(pair.baseline is pair.selected is None and pair.baseline_unavailable == pair.selected_unavailable == reason
               for pair in pairs)


def test_retains_unsupported_forecast_and_exact_visible_decimal_representation(data):
    session = data[-1]

    def visible(day, symbol, at):
        original = session.minutes[at]
        return replace(original, ohlc=tuple(price.quantize(Decimal("0.0001")) for price in original.ohlc))

    plan = _plan(data, read_visible=visible)
    _, original = next((raw, pair) for raw, pair in _original_records(plan) if pair.selected is not None)
    expected = replace(original, selected=None, selected_unavailable="no_mature_supported_training_for_stage")
    restored = _decode(_plain(asdict(expected)), expected, plan)
    assert restored == expected
    assert restored.selected is None and restored.selected_unavailable == expected.selected_unavailable
    assert str(restored.baseline.visible_minute.ohlc[0]).endswith(".0000")


@pytest.mark.parametrize("tampering", ["requested_quantity", "serialized_moment", "value", "causal_clock",
                                        "forecast_pin", "opportunity"])
def test_refuses_malformed_or_inconsistent_wire_rows(data, tampering):
    plan = _plan(data)
    raw, expected = next((raw, pair) for raw, pair in _original_records(plan) if pair.selected is not None)
    forecast = None
    if tampering == "requested_quantity":
        raw["baseline"]["step"]["episode"]["pending"]["quantity"] += 1
    elif tampering == "serialized_moment":
        raw["selected"]["entry_value"]["training_support"]["paths"][0]["support"]["complete_rows"] = True
    elif tampering == "value":
        raw["selected"]["entry_value"]["expected_net"] = "0"
    elif tampering == "causal_clock":
        raw["baseline"]["visible_minute"]["available_at"] = raw["baseline"]["visible_minute"]["start_at"]
    elif tampering == "forecast_pin":
        forecast = "b" * 64
    else:
        raw["opportunity"]["status"] = "overlap_suppressed"
    with pytest.raises(InputRefusal):
        _decode(raw, expected, plan, forecast=forecast)
