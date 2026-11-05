"""Paired model arms receive identical chart fields and explicit masks."""
from fractions import Fraction

import pytest

from research.intraday_chart_folds import ObservedLabel, TrainingRow
from research.intraday_chart_model_inputs import lightgbm_matrix, sequence_buffers, training_targets
from research.intraday_chart_snapshot import PeerRow, SnapshotRow
from research.intraday_chart_tensors import ChartTensor, PeerTensor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_pattern_calibration import session_open


KEY = "2025-09-04", "KRX:000001", 60
CHART = ChartTensor(tuple((i / 10, i / 10 + 1, i / 10 - 1, i / 10, 0.0, 1.0, 0.5, 0.0, 0.0, 1.0)
                          for i in range(60)), ((0.0,) * 6,) * 24, (0.2, 0.0, 0.0, 0.0, 0.0))


class View:
    def __init__(self, peer):
        self.peer = peer

    def row(self, day, symbol, offset):
        assert (day, symbol, offset) == KEY
        return SnapshotRow(day, symbol, offset, CHART, (PeerRow("S1", self.peer, b"invented"),), None,
                           "unavailable", b"invented")


def test_market_augmentation_appends_only_peer_channels():
    view = View(PeerTensor(((0.25, 0.5, 0.75),) * 60, 0.5))
    chart = sequence_buffers(view, (KEY,), "S1", "TCN_CHART")
    combined = sequence_buffers(view, (KEY,), "S1", "TCN_CHART_PEER")
    assert chart.coarse == combined.coarse and chart.static[:-1] == combined.static[:-1]
    assert (chart.static[-1], combined.static[-1]) == (0.0, 0.5)
    for start in range(0, 60 * 13, 13):
        assert chart.minute[start:start + 10] == combined.minute[start:start + 10]
        assert tuple(chart.minute[start + 10:start + 13]) == (0.0, 0.0, 0.0)
        assert tuple(combined.minute[start + 10:start + 13]) == (0.25, 0.5, 0.75)
    assert chart.sha256() != combined.sha256()
    names, values = lightgbm_matrix(view, (KEY,), "S1", "LGB_CHART")
    other_names, other_values = lightgbm_matrix(view, (KEY,), "S1", "LGB_CHART_PEER")
    assert names == other_names[:38] and values[0] == other_values[0][:38]


def test_absent_peer_is_not_a_combined_prediction_input():
    chart = sequence_buffers(View(None), (KEY,), "S1", "TCN_CHART")
    assert chart.rows == 1
    with pytest.raises(InputRefusal, match="peer_support"):
        sequence_buffers(View(None), (KEY,), "S1", "TCN_CHART_PEER")


def test_training_target_distinguishes_observed_zero_from_masked_auxiliary():
    outcome = ObservedLabel(Fraction(0), session_open(KEY[0]))
    row = TrainingRow(KEY, (outcome, None, None, None, None, None))
    values, known = training_targets((row,))
    assert tuple(values) == (0.0,) * 6
    assert tuple(known) == (1, 0, 0, 0, 0, 0)
