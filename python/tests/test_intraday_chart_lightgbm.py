"""Small native LightGBM fit and independent tree replay, invented only."""
from datetime import date, timedelta
from fractions import Fraction
import importlib.util

import pytest

from research.intraday_campaign_ml_models import THREAD_VARIABLES
from research.intraday_chart_folds import EntrySupport, ObservedLabel, select_fold
from research.intraday_chart_lightgbm import FitBinding, fit_lightgbm, predict_lightgbm, verify_lightgbm
from research.intraday_chart_snapshot import PeerRow, SnapshotRow
from research.intraday_chart_tensors import ChartTensor
from research.intraday_ed_inputs import MINUTE
from research.intraday_pattern_calibration import session_open


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="pinned LightGBM not installed")
def test_fit_reload_and_replay_accept_only_the_registered_training_view(monkeypatch):
    for key in THREAD_VARIABLES:
        monkeypatch.setenv(key, "1")
    start = date(2024, 1, 1)
    days = tuple((start + timedelta(days=i)).isoformat() for i in range(257))
    symbol = "KRX:000001"
    past = tuple(EntrySupport((day, symbol, offset), True, True)
                 for day in days[:30] for offset in (60, 75, 90, 105))
    keys = tuple((day, symbol, 60) for day in days[100:104])

    def label(key):
        assert key[0] < days[100]
        return (ObservedLabel(Fraction(date.fromisoformat(key[0]).day, 4000),
                              session_open(key[0]) + (key[2] + 36) * MINUTE),) + (None,) * 5

    class Snapshot:
        def row(self, day, symbol, offset):
            chart = ChartTensor(((0.0,) * 10,) * 60, ((0.0,) * 6,) * 24,
                                (0.0, float(date.fromisoformat(day).day), 0.0, 0.0, 1.0))
            return SnapshotRow(day, symbol, offset, chart, (PeerRow("S1", None, b"invented"),),
                               None, "unavailable", b"invented")

    view = Snapshot()
    split = select_fold(days, "S1", 1, (*past, *(EntrySupport(key, True, True) for key in keys)), label)
    binding = FitBinding(*(letter * 64 for letter in "abcde"), "S1", 1, "LGB_CHART")
    artifact = fit_lightgbm(view, split, binding)
    prediction = predict_lightgbm(view, keys, binding, artifact)
    proof = verify_lightgbm(view, split, keys, binding, artifact, prediction)
    assert proof["verified"] and proof["rows"] == 4 and artifact["training"]["rows"] == 120
    with pytest.raises(ValueError, match="scalar replay"):
        verify_lightgbm(view, split, keys, binding, artifact, (prediction[0] + 1, *prediction[1:]))
