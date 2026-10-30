"""Scientific boundaries for fixed learned selectors, with invented inputs."""
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

import pytest

from research import intraday_campaign_ml_comparison as subject


KST = timezone(timedelta(hours=9))
OPEN = datetime(2026, 1, 5, 9, tzinfo=KST)
CLOSE = OPEN.replace(hour=15, minute=30)


def original(state="signal"):
    return {"original_row_index": 0, "session": "2026-01-05", "symbol": "KRX:005930",
        "signal_state": state, "signal_reason": "invented", "decision_at": (OPEN + timedelta(minutes=62)).isoformat(),
        "structural_stop": Fraction(90)}


def selection(vector, prediction, state="signal"):
    return subject._selection_row("D+1", "EA-A", original(state), vector=vector,
        prediction=prediction, fold_index=1, opening=OPEN, closing=CLOSE)


@pytest.mark.parametrize("prediction,expected", [(0.01, "selected"), (0.0, "rejected"),
    (-0.0, "rejected"), (-0.001, "rejected")])
def test_admission_is_strict_positive_not_a_probability_threshold(prediction, expected):
    row = selection((Fraction(1),) * 10, (1, "trained", prediction))
    assert row["state"] == expected
    assert row["prediction_hex"] == prediction.hex()
    assert row["entry_at"] == OPEN + timedelta(minutes=65)
    assert row["cap_exit_at"] == OPEN + timedelta(minutes=125)


def test_feature_unknown_is_not_a_zero_prediction():
    row = selection((None,) + (Fraction(1),) * 9, None)
    assert (row["state"], row["feature_state"], row["prediction_hex"]) == ("unknown", "unknown", None)


def test_unavailable_model_does_not_remove_the_same_feature_control():
    row = selection((Fraction(1),) * 10, (1, "unavailable", None))
    originals = {("EA-A", 0): original()}
    control, coverage = subject._plans_and_coverage([row], originals, "control")
    selected, selected_coverage = subject._plans_and_coverage([row], originals, "selected")
    assert len(control) == 1 and selected == ()
    assert coverage["model_unavailable_units"] == selected_coverage["model_unavailable_units"] == 1
    assert selected_coverage["eligible_control_candidates"] == 1
    assert row["state"] == "unknown"


def test_nonsignal_preserves_its_original_decision_clock_without_an_order():
    row = selection(None, None, "unknown")
    assert row["decision_at"] == OPEN + timedelta(minutes=62)
    assert row["entry_at"] is None and row["clock_eligible"] is None
    assert row["state"] == "absent" and row["signal_state"] == "unknown"


def test_unavailable_zero_event_day_has_no_fabricated_cash_return():
    prior = subject.account.new_state("A+B", cost_bps=33)
    frozen = subject._initial_for_day(prior, cost=33, scope="continuous", unavailable=True,
        policy_id="P09", timing="D+1", fold_index=1, opening=OPEN)
    result = subject.account.replay_account((), {}, {}, {OPEN.date(): OPEN}, {OPEN.date(): CLOSE},
        mode="A+B", cost_bps=33, state=frozen, compress_idle_minutes=True)
    day = subject.statistics.compact_day({"session":"2026-01-05", "session_open":OPEN,
        "session_close":CLOSE}, frozen, result, {"unknown_signal_units":0})
    assert day["status"] == "indeterminate" and day["net_pnl"] is None
    assert day["final_nav"] is None and day["known_entry_count"] == 0
    assert prior.book.indeterminate is False
    later = subject._initial_for_day(result.state, cost=33, scope="continuous", unavailable=False,
        policy_id="P09", timing="D+1", fold_index=2, opening=OPEN + timedelta(days=1))
    assert later.book.indeterminate and later.unknown_at == OPEN
    daily = subject._initial_for_day(later, cost=33, scope="day_local_counterfactual", unavailable=False,
        policy_id="P09", timing="D+1", fold_index=2, opening=OPEN + timedelta(days=1))
    assert daily.book.indeterminate is False


def test_all_52_plans_precede_means_on_evaluation_calendar_including_zero_weeks(monkeypatch):
    reference = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(157)]
    weeks = sorted({subject.statistics._week(day) for day in reference})
    calls = []
    def own(days, calendar, **kwargs):
        assert calendar is reference and len(calendar) == 157
        calls.append("plan")
        return {"invented": True}
    def summary(days, actual_weeks, calendar):
        assert calls.count("plan") == 52
        assert calendar is reference and actual_weeks is weeks
        calls.append("mean")
        return {"mean_daily_net_per_initial_capital": None}
    monkeypatch.setattr(subject.statistics, "own_sizing", own)
    monkeypatch.setattr(subject.statistics, "summarize_days", summary)
    report = {"reference_sessions":reference, "reference_weeks":weeks,
        "scenarios":{f"{timing}|{cost}|{arm}": {scope:{"days":[]} for scope in subject.statistics.SCOPES}
            for timing in subject.TIMINGS for cost in subject.statistics.COSTS for arm in subject.ARMS}}
    subject._finish_statistics(report, None)
    assert calls[:52] == ["plan"] * 52
    assert len(report["comparisons"]) == 4
    assert all(value["excluded_sessions"] == reference for value in report["comparisons"].values())
    assert all(value["status"] == "no_common_identified_days" for value in report["comparisons"].values())
