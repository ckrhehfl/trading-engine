"""Full original target projection, using invented report/chunk fixtures only."""

from copy import deepcopy
from decimal import Decimal

import pytest

from research import intraday_campaign_account_inputs as inputs
from test_intraday_campaign_account_inputs import projections
from test_intraday_campaign_context_readiness import bind_sources, fixture


def test_full_target_view_is_independent_of_original_ab_signal_pool():
    a, b, chunks = fixture()
    pa, pb = projections(a, b)
    for value in (pa, pb):
        for variant in value["variants"].values():
            for day in variant["dates"].values():
                day["candidates"] = []
    old = list(inputs.iter_context_price_days(pa, pb, iter(chunks)))[0]
    full = list(inputs.iter_full_target_price_days(pa, pb, iter(chunks)))[0]
    assert old["minute_bars"] == old["open_prices"] == {}
    assert old["retained_price_symbols"] == [] and "original_chunks" not in old
    assert full["retained_price_symbols"] == sorted(full["original_sources"])
    assert len(full["original_chunks"]) == len(full["original_sessions"]) == 20
    assert {symbol for symbol, _ in full["minute_bars"]} == set(full["original_chunks"])
    assert full["original_union_complete"] is True
    assert full["consumed_target_chunks"] == 20 and full["consumed_price_rows"] == 1800
    assert full["original_chunks"][chunks[0]["symbol"]] == chunks[0]


def test_full_target_unique_open_survives_invalid_hlc_but_not_duplicate():
    a, b, chunks = fixture()
    symbol = "KRX:000006"  # Old A/B known-no-signal, still part of full target view.
    chunk = next(chunk for chunk in chunks if chunk["symbol"] == symbol)
    chunk["rows"][80]["high"] = "invalid"
    chunk["rows"].append(deepcopy(chunk["rows"][81]))
    bind_sources(a, b, chunks)
    day = list(inputs.iter_full_target_price_days(*projections(a, b), chunks))[0]
    opening = day["session_open"]
    assert day["minute_bars"][(symbol, opening + 80 * inputs.eb.cc.MINUTE)] is None
    assert day["open_prices"][(symbol, opening + 80 * inputs.eb.cc.MINUTE)] == Decimal("100")
    assert day["minute_bars"][(symbol, opening + 81 * inputs.eb.cc.MINUTE)] is None
    assert day["open_prices"][(symbol, opening + 81 * inputs.eb.cc.MINUTE)] is None


@pytest.mark.parametrize("change", ["missing", "extra", "reordered", "changed_pin", "changed_metadata"])
def test_full_view_never_accepts_incomplete_changed_or_reordered_source(change):
    a, b, chunks = fixture()
    pa, pb = projections(a, b)
    if change == "missing":
        chunks.pop()
    elif change == "extra":
        chunks.append(deepcopy(chunks[-1]))
    elif change == "reordered":
        chunks[0], chunks[1] = chunks[1], chunks[0]
    elif change == "changed_pin":
        chunks[0]["rows"][0]["close"] = "100"
    else:
        chunks[0]["retrieved_at"] = "2026-10-21T00:00:00+00:00"
    with pytest.raises((inputs.AccountInputsRefusal, inputs.eb.ExperimentRefusal)):
        list(inputs.iter_full_target_price_days(pa, pb, iter(chunks)))


def test_full_view_consumes_original_order_and_has_no_previous_day_slots():
    a, b, chunks = fixture(sessions=2, churn=True)
    consumed = []
    def source():
        for chunk in chunks:
            consumed.append((chunk["session"], chunk["symbol"]))
            yield chunk
    stream = inputs.iter_full_target_price_days(*projections(a, b), source())
    first = next(stream)
    assert len(consumed) == 21 and not first["original_union_complete"]
    second = next(stream)
    assert len(consumed) == 42 and second["original_union_complete"]
    assert all(chunk["session"] == second["session"] for chunk in second["original_chunks"].values())
    assert {stamp.date().isoformat() for _, stamp in second["open_prices"]} == {second["session"]}
    assert len(second["original_chunks"]) == 21
    with pytest.raises(StopIteration):
        next(stream)


def test_existing_account_and_context_views_do_not_expose_raw_chunks_or_extra_prices():
    a, b, chunks = fixture()
    pa, pb = projections(a, b)
    account = list(inputs.iter_account_price_days(pa, pb, chunks))[0]
    context = list(inputs.iter_context_price_days(pa, pb, chunks))[0]
    assert "original_chunks" not in account and "original_chunks" not in context
    assert "original_sessions" not in account
    assert len(context["original_sessions"]) == 20
    assert account["minute_bars"] == context["minute_bars"]
    assert account["open_prices"] == context["open_prices"]
    assert "KRX:000006" not in context["retained_price_symbols"]
