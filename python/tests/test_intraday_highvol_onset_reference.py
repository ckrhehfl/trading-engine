"""Invented sealed-input stand-ins for the onset reference verifier."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
from math import sqrt

import pytest

from research.intraday_highvol_onset import compare_onset_expansion
from research.intraday_highvol_onset_reference import verify_onset_expansion
from research.intraday_chart_evaluation import NORMAL_95
from research.intraday_pattern_calibration import session_close, session_open


DATES = tuple(day.isoformat() for shift in range(400)
              if (day := date(2025, 8, 1) + timedelta(days=shift)).weekday() < 5)[:257]
MINUTE = timedelta(minutes=1)


def _ratio(value):
    number = Fraction(value)
    return {"fraction": [number.numerator, number.denominator]}


def _event(day, symbol, offset, *, expanded=True, missing=False, exit_open=101):
    opening = session_open(day)
    end, decision = opening + offset * MINUTE, opening + (offset + 2) * MINUTE
    entry, exit_at = decision + MINUTE, decision + 31 * MINUTE
    supported = exit_at < session_close(day) - 15 * MINUTE
    close = 104 if expanded else 102
    prices = ((100, 102, 99, 101), (101, 102, 100, 102), (102, 106, 101, close))
    bars = []
    for distance, ohlc in zip((10, 5, 0), prices, strict=True):
        bar_end = end - distance * MINUTE
        bars.append({"start_at": (bar_end - 5 * MINUTE).isoformat(), "end_at": bar_end.isoformat(),
                     "available_at": (bar_end + 2 * MINUTE).isoformat(), "ohlc": [_ratio(value) for value in ohlc]})
    key = day, symbol, offset
    feature = {
        "key": list(key), "timings": ["S1", "S2"],
        "first_high": {"left_censored": offset == 15, "threshold": _ratio(2), "current": {
            "symbol": symbol, "opening": opening.isoformat(), "offset": offset,
            "decision_at": decision.isoformat(), "price_available_at": decision.isoformat(), "price_status": "available",
            "energy": _ratio(3), "signed_move": _ratio(Fraction(close, 100) - 1),
        }},
        "attempt": {"observed_bars": bars[:2] if missing else [*bars, None],
                    "confirmation": object(), "state": object(), "scenario": object()},
        "immediate": {"status": "supported" if supported else "outside_clock", "decision_at": decision.isoformat(),
                      "entry_at": entry.isoformat(), "exit_at": exit_at.isoformat()},
    }
    label = {"key": list(key), "primary": object(), "immediate": {
        "status": "available" if exit_open is not None else "unavailable", "entry_open": _ratio(100),
        "entry_at": entry.isoformat(), "exit_at": exit_at.isoformat(),
        "exit_open": None if exit_open is None else _ratio(exit_open),
        "mature_at": None if exit_open is None else (exit_at + 3 * MINUTE).isoformat(),
        "gross": None if exit_open is None else _ratio(Fraction(exit_open, 100) - 1),
    }}
    return feature, label


class _Inputs:
    source_dates = DATES

    def __init__(self, events):
        self.events = events
        self.features_complete = False
        self.opened = None

    def features(self):
        for event, _ in self.events:
            assert self.opened is None
            yield event
        self.features_complete = True

    @contextmanager
    def open_labels(self, keys):
        assert self.features_complete
        labels = {tuple(frame["key"]): frame for _, frame in self.events if frame is not None}
        assert keys == tuple(labels)
        self.opened = keys
        yield labels.__getitem__


def _case():
    day = DATES[100]
    rows = (
        _event(DATES[99], "KRX:005930", 15),
        _event(day, "KRX:005930", 15),
        _event(day, "KRX:000660", 20, expanded=False, exit_open=99),
        _event(day, "KRX:005930", 20, exit_open=None),
        _event(day, "KRX:035420", 25, missing=True),
        _event("2026-01-02", "KRX:005930", 300),
    )
    eligible = {1, 2, 3}
    return tuple(sorted(((row, label if index in eligible else None) for index, (row, label) in enumerate(rows)),
                        key=lambda pair: (pair[0]["key"][0], pair[0]["key"][2], pair[0]["key"][1])))


def _produce(events):
    labels = {tuple(event["key"]): label for event, label in events if label is not None}
    return compare_onset_expansion(DATES, (frame for frame, _ in events), labels_at=labels.__getitem__)


def test_reference_freezes_only_eligible_keys_and_preserves_censored_and_unknown_events():
    events = _case()
    report = _produce(events)
    inputs = _Inputs(events)
    proof = verify_onset_expansion(inputs, report, lambda _: None)
    assert proof["complete"] is True and proof["events"] == 5 and proof["known_label_rows"] == 2
    assert proof["prospective_outcomes"]["keys"] == len(inputs.opened) == 3
    for timing in ("S1", "S2"):
        cost = report["timings"][timing]["costs"]["43"]
        assert cost["early_a"]["net_pp"]["mean"] == .57
        assert cost["early_a"]["outcome_counts"] == {"available": 1, "declined": 1, "fixed_exit_unavailable": 1}
        assert sum(week["same_symbol_nonoverlapping_selected"] for week in cost["early_a"]["weeks"].values()) == 1
        assert cost["paired"]["known_matched_opportunities"] == 2


@pytest.mark.parametrize("field", ("candidate", "pair", "overlap", "censor", "late", "maturity"))
def test_reference_refuses_changed_public_statistics_and_noncausal_rows(field):
    events = _case()
    report = _produce(events)
    first, _ = next((frame, label) for frame, label in events if label is not None)
    if field == "candidate":
        report["candidate_net43_both_timings"] = True
    elif field == "pair":
        report["timings"]["S1"]["costs"]["43"]["paired"]["known_matched_opportunities"] += 1
    elif field == "overlap":
        weeks = report["timings"]["S1"]["costs"]["43"]["early_a"]["weeks"]
        next(iter(weeks.values()))["same_symbol_nonoverlapping_selected"] += 1
    elif field == "censor":
        report["timings"]["S1"]["first_hour_left_censored_by_support"]["eligible_expansion"] += 1
    elif field == "late":
        first["attempt"]["observed_bars"][0]["available_at"] = first["immediate"]["entry_at"]
    else:
        _, label = next((frame, label) for frame, label in events if label is not None)
        label["immediate"]["mature_at"] = label["immediate"]["exit_at"]
    with pytest.raises(ValueError):
        verify_onset_expansion(_Inputs(events), deepcopy(report), lambda _: None)


def test_reference_reconstructs_candidate_sign_inside_numeric_comparison_tolerance():
    # Ten known days and nonzero daily variation give a valid interval just below zero.
    shift = Fraction.from_float(NORMAL_95 * sqrt(2 * 157 / 156) / 10 - 1e-13)
    exits = [Fraction(10043, 100) + shift + variation for variation in (1, -1, *([0] * 8))]
    events = tuple(_event(day, "KRX:005930", 15, exit_open=price)
                   for day, price in zip(DATES[100:110], exits, strict=True))
    report = _produce(events)
    verify_onset_expansion(_Inputs(events), report, lambda _: None)
    for timing in report["timings"].values():
        interval = timing["costs"]["43"]["early_a"]["net_pp"]["normal95_exploratory"]
        assert -2e-12 < interval[0] < 0
        interval[0] = -interval[0]
        timing["candidate"]["net43_lower_positive"] = timing["candidate"]["supported"] = True
    report["candidate_net43_both_timings"] = True
    with pytest.raises(ValueError, match="eg_onset_reference_candidate"):
        verify_onset_expansion(_Inputs(events), report, lambda _: None)
