"""Whole invented snapshot with a separately rebuilt verification expectation."""
from dataclasses import replace
from pathlib import Path

import pytest

from research import intraday_chart_snapshot as store
from research import intraday_chart_snapshot_unit as unit
from research.intraday_chart_snapshot_run import FEATURE_FILE, LABEL_FILE, build_snapshot, verify_records
from research.intraday_trader_unit import _make_invented


ROOT = Path(__file__).resolve().parents[2]
PROVENANCE = b'{"fixture":"whole-invented"}\n'


def checkpoint(_):
    return None


def test_registration_binds_preexisting_source_and_fixed_science():
    inputs = tuple((ROOT / name).read_bytes() for name in (unit.SPEC, unit.ORIGINAL, unit.SCIENCE))
    spec, source = unit.contract(*inputs)
    assert spec["unit_id"] == unit.UNIT and source["geometry"] == unit.FULL
    assert tuple(pin.name for pin in unit.input_pins(source)) == ("CB", "CC", "CX")
    with pytest.raises(ValueError, match="registrations differ"):
        unit.contract(inputs[0], inputs[1], inputs[2] + b"\n")


def test_invented_archives_seal_and_verify_every_coordinate(tmp_path):
    received = tmp_path / "received"
    received.mkdir(mode=0o700)
    source = _make_invented(received, 1, checkpoint)
    envelope = replace(source, input_contract=unit.INPUT_CONTRACT)
    with unit._original_three(tmp_path, envelope, None, {}, True, checkpoint) as (universe, reader):
        seal, verification = build_snapshot(tmp_path, universe, reader, PROVENANCE, checkpoint)
        assert verification["feature_rows"] == 21 * 55
        assert verification["label_rows"] == 21 * 17
        assert verification["primary_labels_known"] > 0
        assert all(sum(arm.values()) == 20 * 17 for arm in verification["candidate_counts"].values())

        with (store.open_feature_snapshot(tmp_path / FEATURE_FILE, seal, checkpoint) as features,
              store.open_label_snapshot(tmp_path / LABEL_FILE, seal, checkpoint) as labels):
            class ChangedView:
                metadata = features.metadata
                days = features.days

                def rows(self):
                    changed = False
                    for row in features.rows():
                        if not changed and row.chart is not None:
                            chart = replace(row.chart, static=(999.0, *row.chart.static[1:]))
                            row = replace(row, chart=chart)
                            changed = True
                        yield row

            with pytest.raises(ValueError, match="chart, quote or availability"):
                verify_records(ChangedView(), labels, universe, reader, PROVENANCE, checkpoint)
