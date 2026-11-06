"""Invented sealed-record examples for EG feature and label separation."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, InputRefusal, Issue
from research.intraday_highvol_coverage import project
from research.intraday_highvol_events import OFFSETS, classify_window, group_episodes, measure_window
from research.intraday_highvol_snapshot import iter_scenario_day
from test_intraday_highvol_scenarios import BoundedMinutes, OPEN, PULLBACK, RESUME, SYMBOL, UP, market


DAY = OPEN.date().isoformat()
PEERS = tuple(f"KRX:{number:06}" for number in range(2, 7))
ROSTERS = {"S1": (SYMBOL, *PEERS[:4]), "S2": (SYMBOL, *PEERS[:3])}
BARS = UP + (PULLBACK, RESUME, (107, 109, 106, 108)) + ((108, 110, 107, 109),) * 5 + (
    (110, 111, 108, 110),
) + ((108, 110, 107, 109),) * 5 + ((111, 112, 109, 111),)


def sealed(session, *, highs=(15,), gaps=()):
    decisions = []
    expected = tuple(OPEN - timedelta(days=days) for days in range(20, 0, -1))
    for offset in OFFSETS:
        metric = measure_window(session, offset, OPEN + (offset + 2) * MINUTE)
        energy = Fraction(0) if offset in highs else metric.energy or Fraction(0)
        prior = {
            opening: replace(
                metric, opening=opening, decision_at=opening + (offset + 2) * MINUTE,
                price_status="available", price_available_at=opening + (offset + 2) * MINUTE,
                energy=energy, signed_move=Fraction(0), relative_range=Fraction(0),
                zero_path=energy == 0, direction_efficiency=None if energy == 0 else Fraction(0),
                shares=None, volume_available_at=None, price_issues=(),
                volume_issues=(Issue(SYMBOL, opening + (offset - 15) * MINUTE, "volume_missing"),),
            )
            for opening in expected if not (offset in gaps and opening == expected[0])
        }
        decisions.append(classify_window(metric, expected, prior))
    return project({
        "session": DAY, "symbol": SYMBOL, "opening": session.opening, "closing": session.closing,
        "targets": ({"scenario": "S1"}, {"scenario": "S2"}),
        "decisions": tuple(decisions), "episodes": group_episodes(tuple(decisions)),
    })


def sessions_for(session):
    peers = {
        symbol: replace(session, symbol=symbol, minutes={
            at: replace(row, symbol=symbol) for at, row in session.minutes.items()
        }) for symbol in PEERS
    }
    return {SYMBOL: session, **peers}


def snapshot(session, record=None, rosters=ROSTERS):
    return list(iter_scenario_day([sealed(session) if record is None else record],
                                  sessions_for(session), rosters, lambda _: None))


def test_snapshot_keeps_distinct_clocks_and_timing_peer_denominators():
    [(feature, label)] = snapshot(market(BARS, close=120))

    assert feature["key"] == label["key"] == [DAY, SYMBOL, 15]
    assert feature["timings"] == ["S1", "S2"]
    assert feature["attempt"]["state"] == "confirmed"
    assert feature["immediate"]["entry_at"] == project(OPEN + 18 * MINUTE)
    assert label["immediate"]["gross"] == project(Fraction(108, 105) - 1)
    assert feature["entry"]["decision_at"] == project(OPEN + 27 * MINUTE)
    assert feature["entry"]["last_close"] == project(Fraction(107))
    assert feature["entry"]["primary_clock_supported"] is True
    assert feature["entry"]["chart"]["status"] == "available"
    peers = feature["entry"]["peers"]
    assert peers["S1"]["status"] == "available" and peers["S1"]["denominator"] == 4
    assert peers["S2"]["status"] == "unavailable" and peers["S2"]["denominator"] == 3
    assert PEERS[-1] not in peers["S1"]["expected_peer_ids"]
    assert label["primary"]["fixed_gross"] == project(Fraction(110, 107) - 1)
    assert label["diagnostic"]["fixed_gross"] == project(Fraction(111, 107) - 1)
    assert [item["step_minutes"] for item in feature["holding"]] == [5, 10, 15, 20, 25]
    first = label["holding"][0]
    assert first["decision_at"] == feature["holding"][0]["decision_at"] == project(OPEN + 32 * MINUTE)
    assert feature["holding"][0]["last_close"] == project(Fraction(108))
    assert first["next_open"] == project(Fraction(108))
    assert first["next_open_mature_at"] == project(OPEN + 36 * MINUTE)
    assert first["value"] == project(Fraction(2, 107))
    assert first["mature_at"] == project(OPEN + 61 * MINUTE)


def test_future_cap_change_modifies_labels_and_leaves_all_features_intact():
    session = market(BARS, close=120)
    record = sealed(session)
    [(feature_before, label_before)] = snapshot(session, record)
    changed_at = OPEN + 58 * MINUTE
    original = session.minutes[changed_at]
    op, high, low, close = original.ohlc
    session.minutes[changed_at] = replace(original, ohlc=(op + 1, high, low, close))
    [(feature_after, label_after)] = snapshot(session, record)

    assert feature_after == feature_before
    assert label_after["primary"]["fixed_gross"] != label_before["primary"]["fixed_gross"]
    assert label_after["holding"][0]["value"] != label_before["holding"][0]["value"]


def test_cluster_bridge_does_not_start_another_attempt():
    session = market(BARS, close=120)
    record = sealed(session, highs=(15, 25, 35), gaps=(20,))
    frames = snapshot(session, record)
    assert [feature["key"][2] for feature, _ in frames] == [15, 35]
    assert [feature["first_high"]["left_censored"] for feature, _ in frames] == [True, False]


@pytest.mark.parametrize("missing", [False, True])
def test_unconfirmed_clusters_keep_immediate_controls(missing):
    bars = BARS if missing else UP[:2] + ((102, 106, 101, 103),) + BARS[3:]
    session = market(bars, close=120)
    if missing:
        del session.minutes[OPEN + 20 * MINUTE]
    [(feature, label)] = snapshot(session)
    assert feature["attempt"]["state"] == ("unsupported" if missing else "no_setup")
    assert feature["entry"] is label["primary"] is label["diagnostic"] is None
    assert feature["holding"] == label["holding"] == []
    assert label["immediate"]["status"] == "available"


def test_saved_metric_and_original_roster_disagreement_refuse_projection():
    session = market(BARS, close=120)
    record = sealed(session)
    altered = deepcopy(record)
    altered["decisions"][0]["current"]["signed_move"] = project(Fraction(1, 10))
    with pytest.raises(InputRefusal, match="scenario_first_high_projection_changed"):
        snapshot(session, altered)
    with pytest.raises(InputRefusal, match="scenario_original_target_membership_changed"):
        snapshot(session, record, {"S1": ROSTERS["S1"], "S2": PEERS})


def test_outside_clock_preserves_confirmation_without_endpoint_reads():
    session = market(BARS[:9], close=45)
    record = sealed(session)
    sessions = sessions_for(session)
    sessions[SYMBOL] = replace(session, minutes=BoundedMinutes(session.minutes, before=25))
    [(feature, label)] = iter_scenario_day([record], sessions, ROSTERS, lambda _: None)

    assert feature["attempt"]["state"] == "confirmed"
    assert feature["entry"]["chart"]["status"] == "available"
    assert feature["entry"]["primary_clock_supported"] is False
    assert feature["immediate"]["status"] == label["immediate"]["status"] == "outside_clock"
    assert label["immediate"]["gross"] is None
    assert label["primary"]["status"] == label["diagnostic"]["status"] == "outside_clock"
    assert feature["holding"] == label["holding"] == []


def test_unavailable_cap_preserves_earlier_managed_exit_quote():
    session = market(BARS, close=120)
    record = sealed(session)
    del session.minutes[OPEN + 58 * MINUTE]
    [(feature, label)] = snapshot(session, record)
    assert feature["entry"]["primary_clock_supported"] is True
    assert label["primary"]["status"] == "admitted"
    assert label["primary"]["fixed_exit_open"] is None
    first = label["holding"][0]
    assert first["status"] == "cap_unavailable"
    assert first["value"] is first["mature_at"] is None
    assert first["next_open"] == project(Fraction(108))
    assert first["next_open_mature_at"] == project(OPEN + 36 * MINUTE)
