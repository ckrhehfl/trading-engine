"""Stored evaluation forecasts retain both source members and their row clocks."""

from dataclasses import replace

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_seal import read_forecasts, seal_forecasts
from research.intraday_scenario_snapshot import FEATURE_SCHEMA, LABEL_SCHEMA, SnapshotSeal
from test_intraday_scenario_forecast_report import decision, forecast


FEATURES = SnapshotSeal(FEATURE_SCHEMA, "a" * 64, 13, 1)
LABELS = SnapshotSeal(LABEL_SCHEMA, "b" * 64, 14, 1)


def test_forecasts_readback_binds_members_and_does_not_drop_unavailable_arms(tmp_path):
    path = tmp_path / "forecast.jsonl"
    stored = forecast(decision(), replace(decision(update=True), peers_supported=False, chart_matched=None,
                                          market_matched=None))
    seal = seal_forecasts(path, stored, FEATURES, LABELS)
    assert read_forecasts(path, seal, scenario="S1", index=1, feature_seal=FEATURES, label_seal=LABELS) == stored
    with pytest.raises(InputRefusal, match="header_differs"):
        read_forecasts(path, seal, scenario="S1", index=1, feature_seal=FEATURES,
                       label_seal=replace(LABELS, sha256="c" * 64))


def test_forecasts_refuse_duplicate_coordinate_and_peer_value_on_unsupported_rows(tmp_path):
    with pytest.raises(InputRefusal, match="decision_order_differs"):
        seal_forecasts(tmp_path / "duplicate.jsonl", forecast(decision(), decision()), FEATURES, LABELS)
    with pytest.raises(InputRefusal, match="unsupported_peer"):
        seal_forecasts(tmp_path / "unsupported.jsonl", forecast(replace(decision(), peers_supported=False)),
                       FEATURES, LABELS)
