"""Execution timing, inaccessible holdings and reserved-panel refusal."""

from array import array
from datetime import date, timedelta
import sqlite3

import pytest

from research.activity_portfolio import Series, eligible, load_panel, simulate, block_standard_error, calibration


def fixture_book():
    dates = [date(2026, 9, 8) + timedelta(days=i) for i in range(13)]
    series = Series(array("d", [100] * 13), array("d", [100] * 13),
                    array("d", [10, 10, 30] + [10] * 10), bytearray(13), bytearray(13))
    params = dict(lookback=2, holding_sessions=3, slots=1, threshold=3.0,
                  seed=1, end="2026-09-18", commission_bps_per_side=0,
                  slippage_bps_per_side=0)
    return dates, series, params


def test_formation_does_not_read_future_and_excludes_frozen_baselines():
    _, series, _ = fixture_book()
    assert eligible(series, 2, 2, 3)
    series.turnover[3:] = array("d", [1e10] * 10)
    assert eligible(series, 2, 2, 3)
    series.frozen[1] = 1
    assert not eligible(series, 2, 2, 3)  # only one preceding usable observation
    series.frozen[1] = 0
    series.frozen[2] = 1
    assert not eligible(series, 2, 2, 3)
    series.frozen[2] = 0
    series.locked[2] = 1
    assert eligible(series, 2, 2, 3)  # a limit-locked trading day is not a halt


def test_next_open_and_sale_tax_are_real_cash_flows():
    dates, series, params = fixture_book()
    series.opens[3] = 200  # known only after the formation close
    series.closes[3:] = array("d", [200] * 10)
    series.opens[6] = 240
    book = simulate(dates, {"A": series}, params)
    assert book["navs"][0] == pytest.approx(1)
    assert book["navs"][-1] == pytest.approx(1.2 * 0.998)
    assert book["closed_trades"] == 1
    assert book["unresolved_positions"] == 0


def test_halt_defers_exit_and_keeps_last_mark_until_resumption():
    dates, series, params = fixture_book()
    for i in (6, 7):
        series.frozen[i] = 1
        series.opens[i] = series.closes[i] = 999  # cannot change the held mark
    series.opens[8] = 80
    book = simulate(dates, {"A": series}, params)
    assert book["navs"][3:5] == pytest.approx([1, 1])
    assert book["navs"][-1] == pytest.approx(0.8 * 0.998)
    assert book["diagnostics"]["delayed_exits"] == 1
    assert book["diagnostics"]["pending_exit_sessions"] == 2


def test_disappearing_name_is_never_sold_at_its_retrospective_last_bar():
    dates, series, params = fixture_book()
    series.opens[6:] = array("d", [0] * 7)
    series.closes[6:] = array("d", [0] * 7)
    book = simulate(dates, {"A": series}, params)
    assert book["closed_trades"] == 0
    assert book["unresolved_positions"] == 1
    assert book["unresolved_marked_value"] == 1
    assert book["diagnostics"]["entries"] == 1


def test_unfilled_entry_remains_cash():
    dates, series, params = fixture_book()
    series.frozen[3] = 1
    book = simulate(dates, {"A": series}, params)
    assert book["navs"] == [1] * 8
    assert book["diagnostics"]["unfilled_entries"] == 1
    assert book["closed_trades"] == 0


def test_costs_are_charged_on_both_actual_notionals():
    dates, series, params = fixture_book()
    params.update(commission_bps_per_side=2, slippage_bps_per_side=5)
    book = simulate(dates, {"A": series}, params)
    assert book["navs"][-1] == pytest.approx((1 - 27 / 10000) / (1 + 7 / 10000))


def test_reserved_panel_refused_before_reading_any_prices(tmp_path, monkeypatch):
    scan, calendar = tmp_path / "reserved.db", tmp_path / "calendar.db"
    with sqlite3.connect(scan) as con:
        con.execute("CREATE TABLE scan_panel(id INTEGER, start TEXT, end TEXT)")
        con.execute("INSERT INTO scan_panel VALUES(1,'19910828','20181231')")
    sqlite3.connect(calendar).close()
    statements = []
    original = sqlite3.connect

    def trace(*args, **kwargs):
        con = original(*args, **kwargs)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(sqlite3, "connect", trace)
    with pytest.raises(ValueError, match="only the registered spent"):
        load_panel(scan, calendar)
    assert not any("scan_bars" in sql for sql in statements)


def test_session_blocks_capture_serial_dependence_and_seed_reproduces():
    values = [0.01] * 100 + [-0.01] * 100
    block = block_standard_error(values, 50, 99, 1000)
    assert block > 3 * block_standard_error(values, 1, 99, 1000)
    assert block == block_standard_error(values, 50, 99, 1000)


def test_calibration_only_reports_dispersion_and_stops_an_unpowered_family():
    book = {"returns": [0.01] * 300 + [-0.01] * 300,
            "diagnostics": {}, "closed_trades": 40, "unresolved_positions": 0}
    params = dict(bootstrap_blocks=[63, 126, 252], bootstrap_repetitions=200,
                  seed=1, alpha=0.05, power=0.8, holding_sessions=126,
                  sizing_round_trip_bps=43.54)
    result = calibration(book, params)
    assert result["cost_floor_resolvable"] is False
    assert not {"mean", "sharpe", "total_return", "pass"} & result.keys()
    assert result["corrected_to_naive_se"] > 1
