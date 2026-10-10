"""Grouped inventory capacity at the fixed cutoff; synthetic inputs only."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from research.activity_accounting import Lot
from research.activity_book import ActivityBook
from research.activity_partition_selection import PartitionSelection
from research.activity_replay import DecimalSeries, replay_synthetic
from research.activity_timing import SessionLagPolicy


SNAPSHOT = "a" * 64


def inputs(lag, *, slots, due_index, pending_index=0, frozen_component=False):
    calendar = tuple(date(2024, 1, 1) + timedelta(days=i) for i in range(13))
    path = Path(__file__).resolve().parents[2] / "configs/research/discovery/activity-calibration-v1.json"
    params = json.loads(path.read_text())["parameters"]
    params.update(lookback=2, holding_sessions=3, slots=slots, end=calendar[7].isoformat())
    policy = SessionLagPolicy(lag, paired=True)
    prices = (Decimal(100),) * len(calendar)
    series = DecimalSeries(prices, prices, (Decimal(10),) * len(calendar),
                           (False,) * len(calendar), (False,) * len(calendar),
                           (True,) * len(calendar))
    panel = {code: series for code in ("RETAINED", "ALLOTTED", "NEW")}
    entry = 2 + lag
    if frozen_component:
        frozen = tuple(i == entry for i in range(len(calendar)))
        panel["ALLOTTED"] = replace(series, frozen=frozen)
    selection = PartitionSelection(calendar[2], calendar[entry], policy.selection_at(calendar, 2),
                                   ("NEW",), 1, "b" * 64)
    lots = tuple(Lot(code, code, Decimal(1), Decimal(100), calendar[2], calendar[0],
                     calendar[due_index], calendar[pending_index if code == "ALLOTTED" else 0],
                     SNAPSHOT, investment_id="original-acquisition")
                 for code in ("RETAINED", "ALLOTTED"))
    initial = ActivityBook(Decimal(1000), lots, calendar[2])
    return calendar, panel, params, (selection,), initial, policy


def run(values):
    calendar, panel, params, selections, initial, policy = values
    return replay_synthetic(calendar, panel, params, selections, dataset_sha256=SNAPSHOT,
                            initial_book=initial, timing_policy=policy)


@pytest.mark.parametrize("lag", [1, 2])
def test_two_components_use_one_slot_in_selection_and_execution(lag):
    values = inputs(lag, slots=2, due_index=8)
    replay = run(values)
    first = replay.books[lag - 1]
    assert replay.selections == ((values[0][2], ("NEW",)),)
    assert len(first.lots) == 3
    assert first.slot_count == 2
    assert {lot.code for lot in first.lots} == {"RETAINED", "ALLOTTED", "NEW"}
    assert dict(replay.diagnostics).get("unfilled_capacity_targets", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
def test_partial_delivery_reserves_original_slot_before_quotes(lag):
    entry = 2 + lag
    values = inputs(lag, slots=1, due_index=entry, pending_index=entry + 1)
    replay = run(values)
    first = replay.books[lag - 1]
    assert replay.selections == ((values[0][2], ()),)
    assert tuple(lot.code for lot in first.lots) == ("ALLOTTED",)
    assert first.slot_count == 1
    assert dict(replay.diagnostics).get("entries", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
def test_failed_component_sale_blocks_reserved_entry_without_reselection(lag):
    entry = 2 + lag
    frozen_values = inputs(lag, slots=1, due_index=entry, frozen_component=True)
    clear_values = inputs(lag, slots=1, due_index=entry)
    frozen, clear = run(frozen_values), run(clear_values)
    # The current opening/frozen observation cannot change the cutoff target.
    assert frozen.selections == clear.selections == ((frozen_values[0][2], ("NEW",)),)
    frozen_first, clear_first = frozen.books[lag - 1], clear.books[lag - 1]
    assert tuple(lot.code for lot in frozen_first.lots) == ("ALLOTTED",)
    assert tuple(lot.code for lot in clear_first.lots) == ("NEW",)
    assert frozen_first.slot_count == clear_first.slot_count == 1
    assert dict(frozen.diagnostics)["unfilled_capacity_targets"] == 1
    assert dict(frozen.diagnostics)["unfilled_entries"] == 1
    assert dict(frozen.diagnostics).get("entries", 0) == 0

