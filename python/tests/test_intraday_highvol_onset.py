"""Invented sealed-format cases for immediate EG expansion attribution."""

from datetime import date, timedelta
from fractions import Fraction

import pytest

from research.intraday_campaign_readiness import _week
from research.intraday_ed_inputs import FIVE, MINUTE, InputRefusal
from research.intraday_highvol_coverage import project
from research.intraday_highvol_onset import compare_onset_expansion, evaluate_onset, prepare_onset, prospective_keys
from research.intraday_pattern_calibration import session_close, session_open


START = date(2025, 7, 1)
DAYS = tuple(day.isoformat() for i in range(370)
             if (day := START + timedelta(days=i)).weekday() < 5)[:257]
FIRST = DAYS[100]


def archived(day=FIRST, *, symbol="KRX:000001", offset=15, timings=("S1", "S2"), impulse=110,
             prefix=3, censored=True, exit_open=101, entry_open=100):
    opening = session_open(day)
    high_end = opening + offset * MINUTE
    decision = high_end + 2 * MINUTE
    entry, exit_at = decision + MINUTE, decision + 31 * MINUTE
    supported = exit_at < session_close(day) - 15 * MINUTE
    bars = []
    for index, ohlc in enumerate(((100, 105, 97, 102), (102, 104, 99, 103),
                                  (103, max(110, impulse), min(98, impulse), impulse))[:prefix]):
        end = high_end - (2 - index) * FIVE
        bars.append({"start_at": end - FIVE, "end_at": end, "available_at": end + 2 * MINUTE,
                     "ohlc": tuple(map(Fraction, ohlc))})
    key = [day, symbol, offset]
    feature = {"key": key, "timings": timings, "first_high": {"left_censored": censored, "threshold": Fraction(1, 200),
               "current": {"opening": opening, "symbol": symbol, "offset": offset, "decision_at": decision,
                           "price_status": "available", "price_available_at": decision, "energy": Fraction(1, 100),
                           "signed_move": Fraction(impulse, 100) - 1}},
               "attempt": {"observed_bars": bars}, "immediate": {
                   "decision_at": decision, "entry_at": entry, "exit_at": exit_at,
                   "status": "supported" if supported else "outside_clock"}}
    known = supported and entry_open is not None and exit_open is not None
    label = {"key": key, "immediate": {"entry_at": entry, "exit_at": exit_at,
             "status": ("available" if known else "unavailable") if supported else "outside_clock",
             "entry_open": None if not supported or entry_open is None else Fraction(entry_open),
             "exit_open": None if not supported or exit_open is None else Fraction(exit_open),
             "gross": Fraction(exit_open, entry_open) - 1 if known else None,
             "mature_at": exit_at + 3 * MINUTE if known else None}}
    return project(feature), project(label)


def compare(pairs):
    labels = {tuple(feature["key"]): label for feature, label in pairs}
    return compare_onset_expansion(DAYS, (feature for feature, _ in pairs), labels_at=labels.__getitem__)


def test_future_attempt_fields_and_later_bars_are_never_read():
    class PrefixOnly(list):
        def __getitem__(self, index):
            assert index == slice(None, 3)
            return super().__getitem__(index)

    class InitialOnly(dict):
        def __getitem__(self, key):
            assert key == "observed_bars"
            return super().__getitem__(key)

    early, label = archived()
    early["attempt"] = InitialOnly(observed_bars=PrefixOnly([*early["attempt"]["observed_bars"], object()]))
    plan = prepare_onset(DAYS, (early,))
    assert prospective_keys(plan) == (tuple(early["key"]),)
    result = evaluate_onset(plan, labels_at=lambda _: label)
    assert result["timings"]["S1"]["support_counts"] == {"eligible_expansion": 1}


def test_same_support_pair_preserves_unknowns_and_explicit_no_trade():
    pairs = (archived(DAYS[99]), archived(),
             archived(symbol="KRX:000002", timings=("S1",), impulse=105, exit_open=99),
             archived(symbol="KRX:000003", timings=("S2",), prefix=2),
             archived(symbol="KRX:000004", exit_open=None),
             archived(symbol="KRX:000005", impulse=98, entry_open=None),
             archived("2026-01-02", offset=300, timings=("S1",), censored=False))
    labels = {tuple(feature["key"]): label for feature, label in pairs}
    expected = tuple(tuple(pairs[index][0]["key"]) for index in (1, 2, 4, 5))
    reads = []

    def features():
        for feature, _ in pairs:
            assert not reads
            yield feature

    def label_at(key):
        reads.append(key)
        return labels[key]

    result = compare_onset_expansion(DAYS, features(), labels_at=label_at)
    assert tuple(reads) == expected
    assert result["reference_sessions"] == list(DAYS[100:])
    s1, s2 = (result["timings"][timing] for timing in ("S1", "S2"))
    assert s1["support_counts"] == {"eligible_expansion": 2, "eligible_other": 2, "outside_clock": 1}
    assert s2["support_counts"] == {"eligible_expansion": 2, "eligible_other": 1, "initial_context_unavailable": 1}
    assert s1["left_censored_by_support"] == s1["first_hour_by_support"] == {"eligible_expansion": 2, "eligible_other": 2}
    assert s1["first_hour_left_censored_by_support"] == s1["first_hour_by_support"]
    assert s1["first_hour_confirmed_by_support"] == {}
    cost = s1["costs"]["33"]
    assert cost["early_a"]["outcome_counts"] == {"available": 1, "declined": 2, "fixed_exit_unavailable": 1}
    assert cost["all_high"]["outcome_counts"] == {"available": 2, "fixed_exit_unavailable": 1, "entry_unavailable": 1}
    assert cost["early_a"]["net_pp"]["mean"] == .67
    pair = cost["paired"]
    assert (pair["known_matched_opportunities"], pair["unknown_matched_opportunities"]) == (2, 2)
    assert pair["net_pp"]["left_minus_right"]["mean"] == pytest.approx(.665)


def test_net43_candidate_requires_both_timings_and_uses_day_cluster_interval():
    pairs = tuple(archived(day, timings=("S1", "S2") if i < 9 else ("S1",), exit_open=101 + i % 2)
                  for i, day in enumerate(DAYS[100:110]))
    result = compare(pairs)
    s1, s2 = (result["timings"][timing]["candidate"] for timing in ("S1", "S2"))
    assert s1 == {"contributing_days": 10, "net43_lower_positive": True, "supported": True}
    assert s2 == {"contributing_days": 9, "net43_lower_positive": False, "supported": False}
    assert result["candidate_net43_both_timings"] is False
    updated = (*pairs[:-1], archived(DAYS[109], exit_open=102))
    assert compare(updated)["candidate_net43_both_timings"] is True


def test_weekly_overlap_is_diagnostic_and_zero_weeks_stay_in_denominator():
    monday = next(day for day in DAYS[100:] if date.fromisoformat(day).weekday() == 0)
    start = DAYS.index(monday)
    pairs = tuple(archived(day, offset=offset, timings=("S1",)) for day in DAYS[start:start + 4]
                  for offset in (15, 25, 35, 45, 55))
    arm = compare(pairs)["timings"]["S1"]["costs"]["43"]["early_a"]
    week = arm["weeks"][_week(monday)]
    assert (week["selected"], week["same_symbol_nonoverlapping_selected"], week["modeled_entries"]) == (20, 8, 20)
    assert week["at_least_15_selected"] and not week["at_least_15_nonoverlapping_selected"]
    assert sum(row["reference_sessions"] for row in arm["weeks"].values()) == 157
    assert arm["weeks"][_week(DAYS[-1])]["selected"] == 0


def test_first_hour_support_partitions_confirmed_and_left_censored_clusters():
    pairs = (archived(offset=20, censored=False), archived(offset=60, prefix=2),
             archived(offset=65, prefix=2), archived(offset=75, censored=False))
    report = compare(pairs)["timings"]["S1"]
    assert report["first_hour_by_support"] == {"eligible_expansion": 1, "initial_context_unavailable": 1}
    assert report["first_hour_confirmed_by_support"] == {"eligible_expansion": 1}
    assert report["first_hour_left_censored_by_support"] == {"initial_context_unavailable": 1}
    assert report["left_censored_by_support"] == {"initial_context_unavailable": 2}


def test_late_initial_bar_and_changed_outcome_refuse_before_producing_results():
    feature, label = archived()
    first_bar = feature["attempt"]["observed_bars"][0]
    first_bar["available_at"] = feature["first_high"]["current"]["decision_at"]
    with pytest.raises(InputRefusal, match="eg_onset_initial_bar_clock_changed"):
        compare_onset_expansion(DAYS, (feature,), labels_at=lambda _: pytest.fail("outcome read after clock mismatch"))
    feature, label = archived()
    label["immediate"]["gross"] = project(Fraction(99, 100))
    with pytest.raises(InputRefusal, match="eg_onset_outcome_maturity_or_return_changed"):
        compare(((feature, label),))
