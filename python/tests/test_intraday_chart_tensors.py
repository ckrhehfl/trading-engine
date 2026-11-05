"""Check the EF numerical boundary on exact invented observations."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import log

import pytest

from research.intraday_chart_inputs import build_chart_inputs, build_peer_context
from research.intraday_chart_tensors import (
    TABULAR_CHART_NAMES, TABULAR_PEER_NAMES, chart_tensor, peer_tensor, remove_temporal_order, tabular_chart,
    tabular_peer,
)
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPENING = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))


def session(symbol="invented", opening=OPENING, count=60):
    rows = {}
    for offset in range(count):
        at = opening + offset * MINUTE
        price = Decimal(100 + offset)
        rows[at] = Minute(symbol, at, at + 3 * MINUTE, (price, price + 2, price - 1, price + 1),
                          None if offset == 1 else Decimal(0 if offset == 0 else 4),
                          volume_state="missing" if offset == 1 else "observed")
    return SessionInputs(symbol, opening, opening + 390 * MINUTE, rows)


def chart(current, minutes=60, **kwargs):
    end = current.opening + minutes * MINUTE
    return build_chart_inputs(current, end, end + 2 * MINUTE, **kwargs)


def test_tensor_keeps_zero_volume_padding_and_prior_missingness_distinct():
    encoded = chart_tensor(chart(session()))
    assert len(encoded.minute) == 60 and len(encoded.coarse) == 24
    assert encoded.minute[0] == pytest.approx((0, 2, -1, 1, 1, 3, 2 / 3, 0, 0, 1))
    assert encoded.minute[1][-2:] == (0, 0)
    assert encoded.minute[2][-2:] == pytest.approx((log(2), 1))
    assert encoded.coarse[:12] == ((0, 0, 0, 0, 0, 1),) * 12
    assert encoded.coarse[12][0] == -37.5
    assert encoded.coarse[-1][3:] == (0, 1, 0)
    assert encoded.static == pytest.approx((60 / 390, 0, 0, 0, 0))


def test_optional_chart_support_never_gets_copied_into_required_minutes():
    current = session(count=75)
    rows = dict(current.minutes)
    del rows[OPENING]
    previous_open = OPENING - timedelta(days=3)
    previous = session(opening=previous_open, count=390)
    encoded = chart_tensor(chart(replace(current, minutes=rows), minutes=75,
                                 expected_previous_opening=previous_open, previous_session=previous))
    assert encoded.coarse[8][4:] == (0, 1)  # scheduled preopening
    assert encoded.coarse[9] == (0, 0, 0, 0, 0, 0)  # missing in-session coarse bar
    assert encoded.static[1:] == pytest.approx((100 * (491 / 175 - 1), 100 * (99 / 175 - 1),
                                                100 * (490 / 175 - 1), 1))
    assert chart_tensor(chart(replace(current, minutes=rows))) is None


def test_peer_tensor_is_separate_and_reports_full_original_denominator():
    current = session()
    peers = {str(index): session(str(index)) for index in range(5)}
    end = OPENING + 60 * MINUTE
    expected = (*peers, "unavailable")
    peer = build_peer_context(current, end, end + 2 * MINUTE, expected_peer_ids=expected, peer_sessions=peers)
    encoded = peer_tensor(peer)
    assert encoded.minute[0] == pytest.approx((1, 1, 1))
    assert encoded.minute[-1] == pytest.approx((100 / 159, 60, 1))
    assert encoded.coverage == pytest.approx(5 / 6)
    assert len(tabular_peer(encoded)) == len(TABULAR_PEER_NAMES) == 4
    assert tabular_peer(encoded)[1:] == pytest.approx((60, 1, 5 / 6))
    assert peer_tensor(build_peer_context(current, end, end + 2 * MINUTE,
                       expected_peer_ids=expected, peer_sessions={})) is None
    assert set(vars(chart_tensor(chart(current)))) == {"minute", "coarse", "static"}


def test_time_order_control_moves_observations_but_preserves_current_state_and_masked_slots():
    source = chart_tensor(chart(session()))
    control = remove_temporal_order(source, coordinate="S1:invented:2026-01-05:60")
    assert control == remove_temporal_order(source, coordinate="S1:invented:2026-01-05:60")
    assert control.minute[:-1] != source.minute[:-1]
    assert Counter(control.minute[:-1]) == Counter(source.minute[:-1])
    assert Counter(control.coarse[12:-1]) == Counter(source.coarse[12:-1])
    assert control.coarse[:12] == source.coarse[:12]
    assert (control.minute[-1], control.coarse[-1], control.static) == (source.minute[-1], source.coarse[-1], source.static)


def test_tabular_summary_uses_the_same_chart_scale_and_volume_mask():
    tensor = chart_tensor(chart(session()))
    vector = tabular_chart(tensor)
    assert len(vector) == len(set(TABULAR_CHART_NAMES)) == 38
    values = dict(zip(TABULAR_CHART_NAMES, vector, strict=True))
    assert values["minute5__return_pct"] == pytest.approx(100 * 5 / 155)
    assert values["minute5__span_pct"] == pytest.approx(100 * 7 / 155)
    assert values["minute60__volume_fraction"] == pytest.approx(59 / 60)
    assert values["minute60__volume_log_mean"] == pytest.approx(58 * log(2) / 59)
    assert values["coarse24__observed_fraction"] == 0.5
