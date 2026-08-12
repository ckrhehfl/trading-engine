"""Unit tests for `live.web_dashboard`'s pure logic (`day_over_day`,
`equity_history`). Deliberately does not import `streamlit` widget calls
(`st.metric`, `st.line_chart`, ...) -- those have no branching logic of
their own and rendering them requires a running Streamlit script context;
see `test_dashboard.py`'s own docstring for the same rationale applied to
`tmux`-calling functions.
"""

from decimal import Decimal

from live import dashboard
from live.dashboard import LoopStatus, TickStatus
from live.web_dashboard import day_over_day, equity_history


def _status(**overrides: object) -> LoopStatus:
    defaults: dict[str, object] = dict(
        key="simulated",
        display_name="SIMULATED LOOP",
        session="paper-trading",
        alive=True,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[],
    )
    defaults.update(overrides)
    return LoopStatus(**defaults)


def test_day_over_day_compares_against_last_completed_report():
    status = _status(
        tick=TickStatus("t", Decimal("102000"), True, None),
        daily_reports=[{"date": "2026-08-11", "ending_equity": "100000"}],
    )
    current, pct = day_over_day(status)
    assert current == Decimal("102000")
    assert pct == Decimal("2.00")


def test_day_over_day_falls_back_to_initial_equity_before_any_report():
    status = _status(tick=TickStatus("t", Decimal("100420"), True, None), daily_reports=[])
    current, pct = day_over_day(status)
    assert current == Decimal("100420")
    assert pct == (Decimal("100420") - dashboard.INITIAL_EQUITY) / dashboard.INITIAL_EQUITY * 100


def test_day_over_day_none_when_no_equity_data_at_all():
    status = _status(tick=None, daily_reports=[])
    assert day_over_day(status) == (None, None)


def test_day_over_day_none_pct_when_reference_is_zero():
    status = _status(
        tick=TickStatus("t", Decimal("100"), True, None),
        daily_reports=[{"date": "2026-08-11", "ending_equity": "0"}],
    )
    current, pct = day_over_day(status)
    assert current == Decimal("100")
    assert pct is None


def test_equity_history_none_when_no_daily_reports():
    assert equity_history(_status(daily_reports=[])) is None


def test_equity_history_builds_one_row_per_report():
    status = _status(
        daily_reports=[
            {"date": "2026-08-10", "ending_equity": "100100"},
            {"date": "2026-08-11", "ending_equity": "99900"},
        ]
    )
    df = equity_history(status)
    assert df is not None
    assert list(df.index) == ["2026-08-10", "2026-08-11"]
    assert list(df["equity"]) == [100100.0, 99900.0]


def test_equity_history_skips_unparseable_equity_and_returns_none_if_all_bad():
    status = _status(daily_reports=[{"date": "2026-08-10", "ending_equity": "not-a-number"}])
    assert equity_history(status) is None


def test_equity_history_skips_only_the_bad_rows():
    status = _status(
        daily_reports=[
            {"date": "2026-08-10", "ending_equity": "not-a-number"},
            {"date": "2026-08-11", "ending_equity": "100200"},
        ]
    )
    df = equity_history(status)
    assert df is not None
    assert list(df.index) == ["2026-08-11"]
