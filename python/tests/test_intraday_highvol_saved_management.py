"""Invented archived frames for paired EG management and fixed exits."""

from datetime import datetime
from fractions import Fraction

import pytest

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_readiness import _week
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_highvol_coverage import project
from research.intraday_highvol_policy import EntryForecast
from research.intraday_highvol_saved_management import (
    HoldingForecast, _bind_events, _evaluate, compare_saved_management,
)
from research.intraday_highvol_snapshot import HOLD_STEPS

from test_intraday_highvol_policy import DAYS, FIRST, frames


def archived(day=FIRST, *, symbol="KRX:000001", cap=99, entry_open=100, stop=98, target=112,
             closes=(101, 101, 101, 101, 101), opens=(104, 106, 107, 108, 109),
             peer_gaps=(), chart_gaps=(), entry_status="admitted"):
    feature, label = frames(day, symbol=symbol, timings=("S1",), peers=("S1",), scenario_status=entry_status)
    confirmation = feature["attempt"]["confirmation"]
    confirmation.update(project({"stop": Fraction(stop), "target": Fraction(target)}))
    entry = datetime.fromisoformat(confirmation["intended_entry_at"])
    primary = label["primary"]
    cap_quote = None if cap is None or entry_status != "admitted" else Fraction(cap)
    primary.update(project({"entry_open": None if entry_open is None else Fraction(entry_open),
                            "fixed_exit_open": cap_quote,
                            "fixed_mature_at": None if cap_quote is None else entry + 33 * MINUTE,
                            "fixed_gross": None if cap_quote is None else cap_quote / entry_open - 1}))
    feature["holding"], label["holding"] = [], []
    for step, close, quote in zip(HOLD_STEPS, closes, opens, strict=True):
        at = entry + step * MINUTE
        feature["holding"].append(project({
            "step_minutes": step, "decision_at": at - MINUTE,
            "last_close": None if close is None else Fraction(close),
            "chart": {"status": "unavailable" if step in chart_gaps or close is None else "available"},
            "peers": {"S1": {"status": "unavailable" if step in peer_gaps else "available"}},
        }))
        status = "next_open_unavailable" if quote is None else "cap_unavailable" if cap_quote is None else "available"
        label["holding"].append(project({
            "step_minutes": step, "decision_at": at - MINUTE, "next_open_at": at, "status": status,
            "next_open": None if quote is None else Fraction(quote),
            "next_open_mature_at": None if quote is None else at + 3 * MINUTE,
        }))
    return feature, label


def key(feature, step=0, arm="TCN_CHART"):
    return "A", "S1", arm, (*feature["key"], step)


def outcome(feature, label, forecasts, *, cost=33, arm="TCN_CHART"):
    bound = _bind_events(fixed_calendar(DAYS), (feature,), {key(feature, arm=arm): EntryForecast(.60)}, forecasts)
    observations = _evaluate(bound, lambda _: label)
    return observations["A", "S1", cost, arm][0].pair


@pytest.mark.parametrize("cost", (33, 43))
def test_paid_cost_once_and_same_original_quantity_with_odd_lot(cost):
    feature, label = archived(entry_open=101, cap=100, opens=(105, 107, 108, 109, 110))
    forecasts = {key(feature, 5): HoldingForecast(-.12), key(feature, 10): HoldingForecast(-.23)}
    result = outcome(feature, label, forecasts, cost=cost)
    assert [row.action for row in result.updates] == ["reduce_half", "exit"]
    quantity = 2_000_000 // 101
    assert result.fixed.original_quantity == result.managed.original_quantity == quantity
    assert result.fixed.initial_cost == result.managed.initial_cost == quantity * 101 * Fraction(cost, 10000)
    assert [row.quantity for row in result.managed.legs] == [quantity // 2, quantity - quantity // 2]
    assert result.managed.gross_return == Fraction((quantity // 2) * 4 + (quantity - quantity // 2) * 6,
                                                   101 * quantity)
    assert result.fixed.gross_return == -Fraction(1, 101)
    assert result.fixed.gross_return - result.fixed.net_return == Fraction(cost, 10000)
    assert result.managed.gross_return - result.managed.net_return == Fraction(cost, 10000)


def test_unknown_close_and_replay_ambiguity_break_streak_and_allow_one_reduction():
    feature, label = archived(closes=(101, 101, None, 101, 101))
    forecasts = {key(feature, 5): HoldingForecast(-.10), key(feature, 10): HoldingForecast(-.01, True),
                 key(feature, 20): HoldingForecast(-.10), key(feature, 25): HoldingForecast(-.10)}
    result = outcome(feature, label, forecasts)
    assert [update.reason for update in result.updates] == [
        "first_nonpositive_hold_gain", "holding_replay_ambiguous", "structural_close_unavailable",
        "no_exit_signal", "consecutive_nonpositive_hold_gain"]
    assert [(leg.offset_after_entry, leg.status) for leg in result.managed.legs] == [(5, "modeled"), (25, "modeled")]


def test_structural_failure_takes_priority_and_known_early_quote_survives_missing_cap():
    feature, label = archived(cap=None, closes=(98, 101, 101, 101, 101))
    result = outcome(feature, label, {key(feature, 5): HoldingForecast(.70, True)})
    assert result.fixed.status == "unavailable"
    assert result.managed.status == "closed"
    assert result.managed.gross_return == Fraction(4, 100)
    assert len(result.updates) == 1 and result.updates[0].reason == "structural_close_failure"
    assert result.updates[0].hold_gain is None


def test_missing_requested_open_preserves_unresolved_inventory_and_stops_later_sales():
    feature, label = archived(opens=(None, 106, 107, 108, 109))
    label["holding"][1]["next_open_at"] = "later quote must not be consulted"
    result = outcome(feature, label, {key(feature, 5): HoldingForecast(-.10),
                                      key(feature, 10): HoldingForecast(-.10)})
    assert result.fixed.status == "closed"
    assert result.managed.status == "unavailable" and result.managed.net_return is None
    assert result.managed.unresolved_quantity == result.managed.original_quantity
    assert [(leg.offset_after_entry, leg.status) for leg in result.managed.legs] == [(5, "indeterminate")]
    assert len(result.updates) == 1


def test_prospective_scope_precedes_labels_and_keeps_independent_cost_populations():
    training, selected, declined, refused = (
        archived(DAYS[99]), archived(), archived(symbol="KRX:000002"),
        archived(symbol="KRX:000003", entry_open=112, entry_status="entry_refused"),
    )
    reads = []
    pairs = (training, selected, declined, refused)

    def features():
        for feature, _ in pairs:
            assert not reads
            yield feature

    labels = {tuple(feature["key"]): label for feature, label in (selected, refused)}

    def label(key):
        reads.append(key)
        return labels[key]

    result = compare_saved_management(
        DAYS, features(), labels_at=label, holding_forecasts={},
        entry_forecasts={key(selected[0]): EntryForecast(.40), key(declined[0]): EntryForecast(.30),
                         key(refused[0]): EntryForecast(.60)},
    )
    assert reads == list(labels)
    groups = result["groups"]["A"]["S1"]
    primary = groups["33"]["TCN_CHART"]
    assert primary["entry_status_counts"] == {"declined": 1, "modeled_entry": 1, "entry_refused": 1}
    assert primary["known_matched"] == 1 and primary["paired_managed_minus_fixed_net_pp"]["mean"] == 0
    assert primary["update_reasons"] == {"holding_forecast_absent": 5}
    assert groups["43"]["TCN_CHART"]["entry_status_counts"] == {"declined": 2, "entry_refused": 1}
    assert groups["43"]["TCN_CHART"]["known_matched"] == 0
    assert primary["weeks"][_week(DAYS[-1])]["selected"] == 0
    assert sum(row["reference_sessions"] for row in primary["weeks"].values()) == 157


def test_wrong_day_or_unsupported_holding_score_refused_before_any_outcome_read():
    feature, label = archived(peer_gaps=(5,))
    training, _ = archived(DAYS[99])
    for bad_key in (key(training, 5), key(feature, 5, "TCN_CHART_PEER")):
        with pytest.raises(InputRefusal, match="eg_holding_forecast_outside_sealed_evaluation_support"):
            compare_saved_management(
                DAYS, (training, feature), labels_at=lambda _: pytest.fail("outcome read before eligibility check"),
                entry_forecasts={key(feature): EntryForecast(.60)}, holding_forecasts={bad_key: HoldingForecast(-.10)},
            )
    result = outcome(feature, label, {}, arm="TCN_CHART_PEER")
    assert result.updates[0].reason == "holding_peer_unavailable"
