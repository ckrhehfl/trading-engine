"""An independently reconstructed price or paired cash result rejects tampering."""

from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_highvol_coverage import project
from research.intraday_highvol_evaluation_reference import verify_evaluation
from research.intraday_highvol_policy import EntryForecast, compare_entry_policies
from research.intraday_highvol_saved_management import HoldingForecast, compare_saved_management

from test_intraday_highvol_policy import DAYS
from test_intraday_highvol_saved_management import archived, key


class InventedInputs:
    source_dates = DAYS

    def __init__(self, pairs, entries, holding):
        self.frames = [frame for frame, _ in pairs]
        self.labels = {tuple(frame["key"]): label for frame, label in pairs}
        self.evaluation_keys = tuple(self.labels)
        self.entries, self.holding = entries, holding
        self.features_complete = False

    def features(self):
        yield from self.frames
        self.features_complete = True

    def entry_forecasts(self):
        return self.entries

    def holding_forecasts(self):
        return self.holding

    @contextmanager
    def open_labels(self, keys):
        assert self.features_complete and set(keys) <= set(self.labels)
        yield self.labels.__getitem__


def test_reference_reconstructs_common_returns_and_stress_then_refuses_tampering():
    pairs = [archived(cap=104), archived(symbol="KRX:000002", cap=102)]
    for _, label in pairs:
        label["immediate"]["exit_open"] = project(Fraction(1001, 10))
    entries = {key(pairs[0][0]): EntryForecast(.40)}
    inputs = InventedInputs(pairs, entries, {})
    report = compare_entry_policies(DAYS, inputs.features(), labels_at=inputs.labels.__getitem__,
                                    forecasts=entries, include_chart_only=True)
    result = verify_evaluation("EG-POLICY-COMPARISON-1", InventedInputs(pairs, entries, {}), report, lambda _: None)
    assert result["complete"] is True and result["prospective_outcomes"]["keys"] == 2
    altered = deepcopy(report)
    cell = altered["groups"]["A"]["S1"]["common"]["fixed_selection_stress"]["arms"]["TCN_CHART"]
    cell["net_pp"]["mean"] += .01
    with pytest.raises(InputRefusal, match="statistic_differs"):
        verify_evaluation("EG-POLICY-COMPARISON-1", InventedInputs(pairs, entries, {}), altered, lambda _: None)


def test_reference_reconstructs_partials_and_never_repairs_missing_requested_sale():
    pairs = [archived(entry_open=101, cap=100), archived(symbol="KRX:000002", opens=(None, 106, 107, 108, 109))]
    entries = {key(frame): EntryForecast(.70) for frame, _ in pairs}
    holds = {key(frame, step): HoldingForecast(-.30) for frame, _ in pairs for step in (5, 10)}
    inputs = InventedInputs(pairs, entries, holds)
    report = compare_saved_management(DAYS, inputs.features(), labels_at=inputs.labels.__getitem__,
                                      entry_forecasts=entries, holding_forecasts=holds)
    result = verify_evaluation("EG-MANAGEMENT-1", InventedInputs(pairs, entries, holds), report, lambda _: None)
    assert result["checked_matched_paths"] == 2 and result["prospective_outcomes"]["keys"] == 2
    altered = deepcopy(report)
    altered["groups"]["A"]["S1"]["43"]["TCN_CHART"]["unresolved_matched"] = 0
    with pytest.raises(InputRefusal, match="management_counts_differ"):
        verify_evaluation("EG-MANAGEMENT-1", InventedInputs(pairs, entries, holds), altered, lambda _: None)
